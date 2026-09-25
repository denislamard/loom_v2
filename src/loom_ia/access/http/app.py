# SPDX-License-Identifier: Apache-2.0
"""API REST d'une instance loom (N2).

Sous ``/v1``, les agents et leurs runs :

- ``GET  /agents`` : les agents publiés (``expose.rest``) ;
- ``POST /agents/{name}/runs`` : lancement. **Synchrone** par défaut — la
  réponse est le résultat du run ; avec ``background: true``, la réponse est
  202 et l'identifiant d'un run déjà inscrit au journal, qu'on suit ensuite
  par les routes ci-dessous. Le corps est en JSON, ou en
  ``multipart/form-data`` pour joindre des images (``uploads``) ;
- ``GET  /runs/{run_id}`` : statut et résultat d'un run ;
- ``GET  /runs/{run_id}/events`` : le journal du run en SSE — ce qui est déjà
  écrit, puis la suite en direct s'il tourne encore. Les événements de ses
  sous-runs y sont mêlés, sauf avec ``?subruns=false`` ; le flux se ferme
  sur la clôture du run demandé ;
- ``POST /runs/{run_id}/approve`` et ``/reject`` : trancher une approbation
  (#17), portée ``approve`` ; sans ``call_id``, toutes les demandes en
  attente le sont. L'approbateur écrit au journal est l'identifiant de la
  clé d'API, qu'un ``by`` dans le corps remplace — une passerelle nomme
  ainsi l'humain qui a tranché ;
- ``POST /runs/{run_id}/cancel`` : arrêt d'un run (A5), portée ``run``.

Et les sessions (F7) :

- ``GET    /sessions`` : les journaux du client, le plus récent d'abord.
  Refusé à une clé limitée à certains agents : la liste ne dit pas de quels
  agents sont les runs d'une session ;
- ``GET    /sessions/{session_id}`` : la fiche — ses runs et ce qui attend
  un humain, tous runs confondus ;
- ``GET    /sessions/{session_id}/events`` : le journal entier en JSONL ;
- ``GET    /sessions/{session_id}/report`` : consommation de toute la
  session (J3), ventilée par run, par rôle et par modèle ;
- ``DELETE /sessions/{session_id}`` : effacement RGPD — journal, fichiers et
  clés d'idempotence. Irréversible, et il concerne tous les agents de la
  session : portée ``admin``.

Et le journal lui-même (K5, #32) :

- ``GET /runs`` : les runs du client, du plus récemment écrit au plus ancien,
  avec ``agent``, ``status`` (répétable), ``since`` et ``until``. Ils sont
  **lus au journal**, session par session : il n'y a pas de projection à
  tenir à jour, et le prix est une lecture par journal ouvert. Deux bornes le
  tiennent — ``limit`` runs rendus, ``sessions`` journaux ouverts —, et la
  page dit ce qu'elle a coûté (``scanned``) et si une borne l'a arrêtée
  (``truncated``). Une clé limitée à certains agents y a droit, contrairement
  à ``/sessions`` : chaque run dit de quel agent il est, si bien que la liste
  se filtre honnêtement. Aucun contenu là-dedans, seulement le type d'un
  échec ;
- ``GET /events`` : la recherche au journal (``EventQuery``) — ``session_id``,
  ``run_id``, ``type``, ``category``, ``status``, ``agent``, ``role``,
  ``tool_name``, ``model_id``, ``since``, ``until``, et ``after`` pour
  reprendre la pagination après un événement. Le client vient de la clé : rien
  dans l'URL ne le nomme, donc on ne cherche que chez soi. Les facettes
  libres ne sont pas interrogeables par l'URL ; les deux que ``EventQuery``
  nomme (``tool_name``, ``model_id``) le sont.

Résultat d'un run (J3) : la réponse, ``unverified`` si elle a été gardée sans
respecter son contrat ou son juge, l'usage et le coût, leur ventilation
(``report``) et les verdicts des juges (``verdicts``). Un run échoué garde le
code 201 : ``status`` vaut ``failed``, ``error_type`` dit ce qui l'a arrêté
(``guard.judge``, ``model.auth``…) et ``error`` le dit en clair. Le champ
``judges`` du corps (``auto``, ``force``, ``skip``) règle les juges du run ;
``skip`` demande une clé de portée ``admin``.

Pour suivre un run depuis son départ, l'appelant choisit son ``run_id`` dans
le corps de la requête : il peut ouvrir le flux sans attendre la réponse.

Tout passe par la façade ``Loom`` : le journal d'un run est le même que par
la CLI ou par MCP.

Document OpenAPI : chaque route porte un résumé et une famille (``TAGS``), et
les deux façons de présenter une clé y sont déclarées — de quoi essayer l'API
depuis sa propre page, sans lire ce fichier.
"""

import json
import logging
import math
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, Annotated, Final, cast

from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader, HTTPBearer
from fastapi.security.http import HTTPAuthorizationCredentials
from pydantic import AwareDatetime, JsonValue
from sse_starlette.sse import EventSourceResponse

from loom_ia.access.api import (
    RUNS_LIMIT,
    RUNS_MAX,
    SESSIONS_MAX,
    SESSIONS_READ,
    AgentNotAllowed,
    DeliveryRefused,
    Loom,
    RunPage,
    RunResult,
    SessionDeletion,
    SessionInfo,
    StreamItem,
    Triggered,
    UnknownApproval,
    UnknownRun,
    UnknownSession,
    UnknownTrigger,
)
from loom_ia.access.http.auth import API_KEY_HEADER, Caller, identify, require, throttle
from loom_ia.access.http.schemas import (
    AgentInfo,
    Approval,
    Cancellation,
    Cancelled,
    Decided,
    Decision,
    RunAccepted,
)
from loom_ia.access.http.uploads import RUN_BODY, run_request
from loom_ia.agents.registry import UnknownAgent
from loom_ia.config import LoomConfig
from loom_ia.core.events import (
    EVENTS_LIMIT,
    EVENTS_MAX,
    Event,
    EventCategory,
    EventQuery,
    EventStatus,
    redacted,
    redacted_all,
)
from loom_ia.core.model import (
    DEFAULT_TENANT,
    AttachmentError,
    EventId,
    RunId,
    RunStatus,
    SessionId,
)
from loom_ia.core.ports import SealError, SessionRecord
from loom_ia.runtime import announce
from loom_ia.tenancy import BudgetExhausted, QuotaExceeded, RateWindow, UnknownTenant
from loom_ia.usage import UsageReport

if TYPE_CHECKING:
    from loom_ia.access.mcp_server.http import McpHttp

logger = logging.getLogger(__name__)

LOCAL_HOSTS: Final = frozenset({"127.0.0.1", "localhost", "::1"})
# Un événement par ligne : le format d'export du journal, celui de la CLI.
NDJSON: Final = "application/x-ndjson"

# Familles de routes du document OpenAPI : chaque route en porte une, et le
# document les décrit — c'est ce qui rend l'API lisible sans ce fichier.
TAGS: Final[tuple[dict[str, str], ...]] = (
    {"name": "agents", "description": "Les agents que cette instance publie en REST."},
    {"name": "runs", "description": "Lancer un run, le suivre, le trancher, l'arrêter."},
    {"name": "sessions", "description": "Les journaux du client : fiches, export, effacement."},
    {
        "name": "hooks",
        "description": "Portes d'entrée déclarées : un appel extérieur ouvre un run.",
    },
    {
        "name": "journal",
        "description": "Recherche au journal : les runs d'un client et leurs événements.",
    },
)

# Schémas d'authentification déclarés au document : la clé est lue sur l'un ou
# l'autre en-tête (``identify``), et les déclarer ici la rend saisissable depuis
# la page de l'API. Ils ne refusent rien eux-mêmes — c'est ``caller`` qui le fait.
BEARER_SCHEME: Final = HTTPBearer(auto_error=False, description="Clé d'API de l'instance")
HEADER_SCHEME: Final = APIKeyHeader(
    name=API_KEY_HEADER, auto_error=False, description="Clé d'API de l'instance"
)


def _open_warnings(config: LoomConfig) -> list[str]:
    """Ce qu'une API sans clé déclarée laisse passer (#39)."""
    if config.security.api_keys:
        return []
    warnings: list[str] = []
    if config.tenants:
        named = ", ".join(config.tenant_ids)
        warnings.append(
            f"API REST sans clé déclarée alors que la config nomme des clients ({named}) : "
            f"tout passera par {DEFAULT_TENANT!r}, puisque c'est la clé qui dit au nom de "
            "qui elle agit"
        )
    if config.server.http.host not in LOCAL_HOSTS:
        warnings.append(
            f"API REST ouverte sur {config.server.http.host} sans clé déclarée : ajouter "
            "'security.api_keys' (loom keys create) pour en exiger une"
        )
    return warnings


async def _payload(request: Request) -> JsonValue:
    """La charge d'une livraison : du JSON, ou rien.

    Un corps vide est légitime — un planificateur n'a rien à dire d'autre que
    « c'est l'heure ». Un corps illisible est refusé, pour ne pas lancer un run
    sur une charge que le gabarit lira vide.
    """
    raw = await request.body()
    if not raw.strip():
        return None
    try:
        return cast("JsonValue", json.loads(raw))
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"Charge illisible : {exc}"
        ) from exc


def package_version() -> str:
    """Version du paquet servi, ou ``0`` hors installation."""
    try:
        return version("loom-ia")
    except PackageNotFoundError:  # pragma: no cover - dépend de l'installation
        return "0"


async def caller(
    request: Request,
    bearer: Annotated[HTTPAuthorizationCredentials | None, Depends(BEARER_SCHEME)] = None,
    header: Annotated[str | None, Depends(HEADER_SCHEME)] = None,
) -> Caller:
    """Appelant de la requête, reconnu par les clés de l'instance servie.

    Le débit de la clé est compté ici, donc sur toutes les routes : une clé qui
    s'emballe est arrêtée avant qu'on ne lise son corps (#39).

    Les deux schémas sont là pour le document OpenAPI ; la clé se relit sur la
    requête elle-même, une instance ouverte n'en demandant aucune.
    """
    loom: Loom = request.app.state.loom
    who = identify(loom.config.security, request)
    throttle(who, request.app.state.rates)
    return who


# L'appelant, injecté dans chaque route.
type Who = Annotated[Caller, Depends(caller)]


def create_app(loom: Loom, *, own: bool = False) -> FastAPI:
    """Application ASGI servant les agents d'une instance.

    ``own`` confie l'instance à l'application : elle la ferme à l'arrêt du
    serveur. Sans lui, la fermeture reste à l'appelant.
    """
    http = loom.config.server.http
    # Profil prod : une API sans clé y est une erreur, pas un avertissement (M4).
    announce(loom.config, _open_warnings(loom.config))

    # Serveur MCP monté dans la même application (J5.2b) : un seul port, une
    # seule authentification, et la clé donne le client à chaque requête.
    served = _mcp(loom) if loom.config.server.mcp.http else None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        if served is None:
            yield
        else:
            # Le gestionnaire de session du SDK a son propre cycle de vie :
            # il vit aussi longtemps que l'application qui le sert.
            async with served.running():
                yield
        if own:
            await loom.aclose()

    app = FastAPI(
        title="loom-ia",
        summary="Agents loom exposés en HTTP",
        version=package_version(),
        openapi_tags=list(TAGS),
        lifespan=lifespan,
    )
    app.state.loom = loom
    # Débit des clés d'API (#39) : une fenêtre glissante par application servie.
    app.state.rates = RateWindow()
    router = APIRouter(prefix=f"{http.base_path}/v1")

    @app.exception_handler(UnknownAgent)
    @app.exception_handler(UnknownApproval)
    @app.exception_handler(UnknownRun)
    @app.exception_handler(UnknownSession)
    @app.exception_handler(UnknownTrigger)
    async def _not_found(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse({"detail": _message(exc)}, status_code=status.HTTP_404_NOT_FOUND)

    @app.exception_handler(BudgetExhausted)
    @app.exception_handler(QuotaExceeded)
    async def _too_many(request: Request, exc: Exception) -> JSONResponse:
        # Un budget de journée épuisé et un débit dépassé disent la même chose
        # à l'appelant — reviens plus tard —, et ``Retry-After`` porte la
        # différence : quelques secondes pour l'un, la bascule de la période
        # pour l'autre.
        after = getattr(exc, "retry_after", 1.0)
        return JSONResponse(
            {"detail": _message(exc)},
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            headers={"Retry-After": str(max(1, math.ceil(float(after))))},
        )

    @app.exception_handler(SealError)
    async def _sealed(request: Request, exc: Exception) -> JSONResponse:
        # Un journal scellé dont la clé a disparu n'est pas une panne : c'est
        # un état voulu (5.5b), et 424 le dit mieux qu'un 500 — la demande est
        # juste, ce qui manque est ailleurs, et le message nomme l'empreinte
        # attendue.
        return JSONResponse(
            {"detail": _message(exc)}, status_code=status.HTTP_424_FAILED_DEPENDENCY
        )

    @app.exception_handler(AgentNotAllowed)
    @app.exception_handler(UnknownTenant)
    async def _forbidden(request: Request, exc: Exception) -> JSONResponse:
        # Le client vient de la clé : ce n'est pas une demande mal formée,
        # c'est une demande que cette clé n'a pas le droit de faire.
        return JSONResponse({"detail": _message(exc)}, status_code=status.HTTP_403_FORBIDDEN)

    @router.get("/agents", tags=["agents"], summary="Agents publiés par l'API")
    async def agents(who: Who) -> list[AgentInfo]:
        require(who, "read")
        published = loom.exposed("rest", who.tenant)
        return [AgentInfo.of(spec) for spec in published if who.allows(spec.name)]

    @router.post(
        "/agents/{name}/runs",
        tags=["runs"],
        summary="Lance un run et attend sa fin, ou le laisse en arrière-plan",
        status_code=status.HTTP_201_CREATED,
        openapi_extra=RUN_BODY,
        responses={
            status.HTTP_202_ACCEPTED: {
                "model": RunAccepted,
                "description": "Run inscrit au journal et confié à l'instance",
            }
        },
    )
    async def start(
        name: str, request: Request, response: Response, who: Who
    ) -> RunResult | RunAccepted:
        require(who, "run", name)
        _published(loom, name)
        try:
            body, attachments = await run_request(request, loom.config.execution.attachments)
            if body.judges == "skip":
                # Se passer des juges retire un contrôle : réservé à l'administration.
                require(who, "admin")
            if body.background:
                run_id = await loom.submit(
                    name,
                    body.message,
                    attachments=attachments,
                    session_id=body.session_id,
                    context=body.context(who.tenant),
                    run_id=body.run_id,
                    judges=body.judges,
                )
                # Le run est inscrit au journal avant le retour de ``submit`` :
                # l'état relu ici désigne un run qui existe déjà.
                state = await loom.state(run_id, session_id=body.session_id, tenant_id=who.tenant)
                response.status_code = status.HTTP_202_ACCEPTED
                return RunAccepted(run_id=run_id, session_id=state.session_id, status=state.status)
            return await loom.run(
                name,
                body.message,
                attachments=attachments,
                session_id=body.session_id,
                context=body.context(who.tenant),
                run_id=body.run_id,
                judges=body.judges,
            )
        except AttachmentError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    @router.post(
        "/hooks/{name}",
        tags=["hooks"],
        summary="Livraison d'un déclencheur : ouvre le run qu'il déclare",
        status_code=status.HTTP_202_ACCEPTED,
        responses={
            status.HTTP_200_OK: {
                "model": Triggered,
                "description": "Livraison déjà reçue : son run est retrouvé, pas rouvert",
            }
        },
    )
    async def hook(name: str, request: Request, response: Response, who: Who) -> Triggered:
        spec = loom.trigger_spec(name)
        # Le déclencheur nomme son agent : c'est sur lui que porte le droit,
        # et la liste `agents` d'une clé borne donc ce qu'elle peut déclencher.
        require(who, "run", spec.agent)
        delivery = request.headers.get(spec.delivery_header) if spec.delivery_header else None
        try:
            opened = await loom.trigger(
                name,
                await _payload(request),
                delivery_id=delivery,
                tenant_id=who.tenant,
            )
        except DeliveryRefused as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
        if opened.repeated:
            # Rien n'a été relancé : le code le dit, pour qu'une plateforme qui
            # réessaie ne croie pas avoir ouvert un second run.
            response.status_code = status.HTTP_200_OK
        return opened

    @router.get("/runs", tags=["journal"], summary="Runs du client, le plus récent d'abord")
    async def listed(
        who: Who,
        agent: str | None = None,
        statuses: Annotated[tuple[RunStatus, ...], Query(alias="status")] = (),
        since: AwareDatetime | None = None,
        until: AwareDatetime | None = None,
        limit: Annotated[int, Query(ge=1, le=RUNS_MAX)] = RUNS_LIMIT,
        sessions: Annotated[int, Query(ge=1, le=SESSIONS_MAX)] = SESSIONS_READ,
    ) -> RunPage:
        require(who, "read")
        if agent is not None:
            # Demander un agent qu'on n'a pas le droit de lire est un refus, pas
            # une page vide : l'appelant saurait sinon que l'agent existe.
            require(who, "read", agent)
        found = await loom.runs(
            tenant_id=who.tenant,
            agent=agent,
            status=statuses,
            since=since,
            until=until,
            limit=limit,
            sessions=sessions,
        )
        # Contrairement à la liste des sessions, celle des runs se filtre
        # honnêtement : chaque run dit de quel agent il est.
        return found.model_copy(
            update={"runs": tuple(run for run in found.runs if who.allows(run.agent))}
        )

    @router.get(
        "/events",
        tags=["journal"],
        summary="Recherche d'événements au journal",
        response_model=list[Event],
    )
    async def found_events(
        who: Who,
        session_id: SessionId | None = None,
        run_id: RunId | None = None,
        types: Annotated[tuple[str, ...], Query(alias="type")] = (),
        categories: Annotated[tuple[EventCategory, ...], Query(alias="category")] = (),
        statuses: Annotated[tuple[EventStatus, ...], Query(alias="status")] = (),
        agent: str | None = None,
        role: str | None = None,
        tool_name: str | None = None,
        model_id: str | None = None,
        since: AwareDatetime | None = None,
        until: AwareDatetime | None = None,
        after: EventId | None = None,
        limit: Annotated[int, Query(ge=1, le=EVENTS_MAX)] = EVENTS_LIMIT,
    ) -> Response | list[Event]:
        require(who, "read")
        if agent is not None:
            require(who, "read", agent)
        query = EventQuery(
            tenant_id=who.tenant,
            session_id=session_id,
            run_id=run_id,
            types=types,
            categories=categories,
            status=statuses,
            agent=agent,
            role=role,
            tool_name=tool_name,
            model_id=model_id,
            since=since,
            until=until,
            after=after,
            limit=limit,
        )
        events = await loom.query(query)
        for named in dict.fromkeys(event.agent for event in events if event.agent):
            require(who, "read", named)
        return JSONResponse(redacted_all(events)) if who.masks else events

    @router.get("/runs/{run_id}", tags=["runs"], summary="Statut et résultat d'un run")
    async def result(who: Who, run_id: RunId, session_id: SessionId | None = None) -> RunResult:
        require(who, "read")
        found = await loom.result(run_id, session_id=session_id, tenant_id=who.tenant)
        require(who, "read", found.agent)
        # Relire un run, c'est lire le journal : sans `read_content`, le
        # statut et les coûts passent, la correspondance non.
        return found.masked() if who.masks else found

    @router.get("/runs/{run_id}/events", tags=["runs"], summary="Journal du run en SSE")
    async def events(
        request: Request,
        who: Who,
        run_id: RunId,
        session_id: SessionId | None = None,
        after_seq: Annotated[int, Query(ge=0)] = 0,
        subruns: bool = True,
    ) -> EventSourceResponse:
        require(who, "read")
        state = await loom.state(run_id, session_id=session_id, tenant_id=who.tenant)
        require(who, "read", state.agent)
        resumed = request.headers.get("last-event-id")
        after = int(resumed) if resumed and resumed.isdigit() else after_seq
        followed = loom.follow(
            run_id,
            session_id=session_id,
            after_seq=after,
            subruns=subruns,
            tenant_id=who.tenant,
        )
        return EventSourceResponse(_messages(followed, masked=who.masks))

    @router.post(
        "/runs/{run_id}/approve", tags=["runs"], summary="Autorise un appel que le run attend"
    )
    async def approve(
        who: Who, run_id: RunId, body: Approval, session_id: SessionId | None = None
    ) -> Decided:
        require(who, "approve")
        state = await loom.state(run_id, session_id=session_id, tenant_id=who.tenant)
        require(who, "approve", state.agent)
        calls = await loom.approve(
            run_id,
            call_id=body.call_id,
            by=body.by or _signature(who),
            reason=body.reason,
            arguments=body.arguments,
            session_id=session_id,
            tenant_id=who.tenant,
        )
        return Decided(run_id=run_id, calls=calls)

    @router.post(
        "/runs/{run_id}/reject", tags=["runs"], summary="Refuse un appel que le run attend"
    )
    async def reject(
        who: Who, run_id: RunId, body: Decision, session_id: SessionId | None = None
    ) -> Decided:
        require(who, "approve")
        state = await loom.state(run_id, session_id=session_id, tenant_id=who.tenant)
        require(who, "approve", state.agent)
        calls = await loom.reject(
            run_id,
            call_id=body.call_id,
            by=body.by or _signature(who),
            reason=body.reason,
            session_id=session_id,
            tenant_id=who.tenant,
        )
        return Decided(run_id=run_id, calls=calls)

    @router.post("/runs/{run_id}/cancel", tags=["runs"], summary="Arrête un run")
    async def cancel(
        who: Who, run_id: RunId, body: Cancellation, session_id: SessionId | None = None
    ) -> Cancelled:
        state = await loom.state(run_id, session_id=session_id, tenant_id=who.tenant)
        require(who, "run", state.agent)
        stopped = await loom.cancel(
            run_id, session_id=session_id, by=body.by or _signature(who), tenant_id=who.tenant
        )
        return Cancelled(run_id=run_id, cancelled=stopped)

    @router.get(
        "/sessions", tags=["sessions"], summary="Sessions du client, la plus récente d'abord"
    )
    async def sessions(who: Who) -> list[SessionRecord]:
        require(who, "read")
        if who.key is not None and who.key.agents:
            # La liste ne dit pas de quels agents sont les runs d'une session :
            # la filtrer honnêtement demanderait de lire chaque journal.
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Clé limitée à certains agents : la liste des sessions ne peut pas être filtrée",
            )
        return await loom.sessions(tenant_id=who.tenant)

    @router.get("/sessions/{session_id}", tags=["sessions"], summary="Fiche d'une session")
    async def session(who: Who, session_id: SessionId) -> SessionInfo:
        require(who, "read")
        found = await loom.session(session_id, tenant_id=who.tenant)
        for agent in dict.fromkeys(run.agent for run in found.runs):
            require(who, "read", agent)
        return found.masked() if who.masks else found

    @router.get(
        "/sessions/{session_id}/events",
        tags=["sessions"],
        summary="Journal d'une session en JSONL",
    )
    async def export(who: Who, session_id: SessionId) -> Response:
        require(who, "read")
        events = await loom.export_session(session_id, tenant_id=who.tenant)
        for agent in dict.fromkeys(event.agent for event in events if event.agent):
            require(who, "read", agent)
        if who.masks:
            dumped = (json.dumps(redacted(event), ensure_ascii=False) for event in events)
        else:
            dumped = (event.model_dump_json() for event in events)
        return Response("".join(f"{line}\n" for line in dumped), media_type=NDJSON)

    @router.delete(
        "/sessions/{session_id}", tags=["sessions"], summary="Supprime une session (RGPD)"
    )
    async def forget(who: Who, session_id: SessionId) -> SessionDeletion:
        # Effacer un journal, ses fichiers et ses clés est irréversible et
        # concerne tous les agents de la session : portée ``admin``, sans
        # exception d'agent à vérifier.
        require(who, "admin")
        removed = await loom.delete_session(session_id, tenant_id=who.tenant)
        if not removed.events and not removed.artifacts and not removed.keys:
            raise UnknownSession(session_id)
        return removed

    @router.get(
        "/sessions/{session_id}/report", tags=["sessions"], summary="Consommation d'une session"
    )
    async def report(who: Who, session_id: SessionId) -> UsageReport:
        require(who, "read")
        found = await loom.report(session_id=session_id, tenant_id=who.tenant)
        if not found.runs:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Session {session_id} inconnue")
        for agent in dict.fromkeys(run.agent for run in found.runs):
            require(who, "read", agent)
        return found

    app.include_router(router)
    if served is not None:
        app.mount(f"{http.base_path}{served.path}", served)
    return app


def _mcp(loom: Loom) -> McpHttp:
    """Monte le serveur MCP, ou dit ce qui manque pour l'extra ``mcp``."""
    try:
        from loom_ia.access.mcp_server.http import McpHttp
    except ImportError as manque:  # pragma: no cover - dépend de l'installation
        raise RuntimeError(
            "'server.mcp.http' demande l'extra 'mcp' : uv sync --extra http --extra mcp"
        ) from manque
    return McpHttp(loom)


def sse(item: StreamItem, *, masked: bool = False) -> dict[str, str]:
    """Message SSE d'un événement du journal ou d'un morceau du modèle."""
    if isinstance(item, Event):
        body = json.dumps(redacted(item), ensure_ascii=False) if masked else item.model_dump_json()
        return {"event": item.type, "id": str(item.seq), "data": body}
    return {"event": f"chunk.{item.type}", "data": item.model_dump_json()}


async def _messages(
    events: AsyncGenerator[Event], *, masked: bool = False
) -> AsyncGenerator[dict[str, str]]:
    async for event in events:
        yield sse(event, masked=masked)


def _signature(who: Caller) -> str | None:
    """Qui signe une décision faute de ``by`` dans le corps : la clé d'API.

    Une instance ouverte n'a personne à nommer : l'audit tiendra à ce que
    l'appelant a bien voulu dire.
    """
    return who.key.id if who.key is not None else None


def _published(loom: Loom, name: str) -> None:
    """Refuse un agent inconnu ou non publié en REST.

    Qu'il soit ouvert au client de la clé est une autre question, et elle a
    une autre réponse : 403, comme pour une clé limitée à certains agents.
    """
    published = [spec.name for spec in loom.exposed("rest")]
    if name not in published:
        raise UnknownAgent(name, published)


def _message(exc: Exception) -> str:
    return str(exc.args[0]) if exc.args else str(exc)
