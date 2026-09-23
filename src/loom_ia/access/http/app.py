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
"""

import json
import logging
import math
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Annotated, Final

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
from sse_starlette.sse import EventSourceResponse

from loom_ia.access.api import (
    AgentNotAllowed,
    Loom,
    RunResult,
    SessionDeletion,
    SessionInfo,
    StreamItem,
    UnknownApproval,
    UnknownRun,
    UnknownSession,
)
from loom_ia.access.http.auth import Caller, identify, require, throttle
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
from loom_ia.core.events import Event, redacted
from loom_ia.core.model import DEFAULT_TENANT, AttachmentError, RunId, SessionId
from loom_ia.core.ports import SessionRecord
from loom_ia.tenancy import BudgetExhausted, QuotaExceeded, RateWindow, UnknownTenant
from loom_ia.usage import UsageReport

if TYPE_CHECKING:
    from loom_ia.access.mcp_server.http import McpHttp

logger = logging.getLogger(__name__)

LOCAL_HOSTS: Final = frozenset({"127.0.0.1", "localhost", "::1"})
# Un événement par ligne : le format d'export du journal, celui de la CLI.
NDJSON: Final = "application/x-ndjson"


async def caller(request: Request) -> Caller:
    """Appelant de la requête, reconnu par les clés de l'instance servie.

    Le débit de la clé est compté ici, donc sur toutes les routes : une clé qui
    s'emballe est arrêtée avant qu'on ne lise son corps (#39).
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
    security = loom.config.security
    if loom.config.tenants and not security.api_keys:
        logger.warning(
            "API REST sans clé déclarée alors que la config nomme des clients (%s) : "
            "tout passera par %r, puisque c'est la clé qui dit au nom de qui elle agit",
            ", ".join(loom.config.tenant_ids),
            DEFAULT_TENANT,
        )
    if not security.api_keys and http.host not in LOCAL_HOSTS:
        logger.warning(
            "API REST ouverte sur %s sans clé déclarée : ajouter 'security.api_keys' "
            "(loom keys create) pour en exiger une",
            http.host,
        )

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

    app = FastAPI(title="loom-ia", summary="Agents loom exposés en HTTP", lifespan=lifespan)
    app.state.loom = loom
    # Débit des clés d'API (#39) : une fenêtre glissante par application servie.
    app.state.rates = RateWindow()
    router = APIRouter(prefix=f"{http.base_path}/v1")

    @app.exception_handler(UnknownAgent)
    @app.exception_handler(UnknownApproval)
    @app.exception_handler(UnknownRun)
    @app.exception_handler(UnknownSession)
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

    @app.exception_handler(AgentNotAllowed)
    @app.exception_handler(UnknownTenant)
    async def _forbidden(request: Request, exc: Exception) -> JSONResponse:
        # Le client vient de la clé : ce n'est pas une demande mal formée,
        # c'est une demande que cette clé n'a pas le droit de faire.
        return JSONResponse({"detail": _message(exc)}, status_code=status.HTTP_403_FORBIDDEN)

    @router.get("/agents", summary="Agents publiés par l'API")
    async def agents(who: Who) -> list[AgentInfo]:
        require(who, "read")
        published = loom.exposed("rest", who.tenant)
        return [AgentInfo.of(spec) for spec in published if who.allows(spec.name)]

    @router.post(
        "/agents/{name}/runs",
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

    @router.get("/runs/{run_id}", summary="Statut et résultat d'un run")
    async def result(who: Who, run_id: RunId, session_id: SessionId | None = None) -> RunResult:
        require(who, "read")
        found = await loom.result(run_id, session_id=session_id, tenant_id=who.tenant)
        require(who, "read", found.agent)
        # Relire un run, c'est lire le journal : sans `read_content`, le
        # statut et les coûts passent, la correspondance non.
        return found.masked() if who.masks else found

    @router.get("/runs/{run_id}/events", summary="Journal du run en SSE")
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

    @router.post("/runs/{run_id}/approve", summary="Autorise un appel que le run attend")
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

    @router.post("/runs/{run_id}/reject", summary="Refuse un appel que le run attend")
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

    @router.post("/runs/{run_id}/cancel", summary="Arrête un run")
    async def cancel(
        who: Who, run_id: RunId, body: Cancellation, session_id: SessionId | None = None
    ) -> Cancelled:
        state = await loom.state(run_id, session_id=session_id, tenant_id=who.tenant)
        require(who, "run", state.agent)
        stopped = await loom.cancel(
            run_id, session_id=session_id, by=body.by or _signature(who), tenant_id=who.tenant
        )
        return Cancelled(run_id=run_id, cancelled=stopped)

    @router.get("/sessions", summary="Sessions du client, la plus récente d'abord")
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

    @router.get("/sessions/{session_id}", summary="Fiche d'une session")
    async def session(who: Who, session_id: SessionId) -> SessionInfo:
        require(who, "read")
        found = await loom.session(session_id, tenant_id=who.tenant)
        for agent in dict.fromkeys(run.agent for run in found.runs):
            require(who, "read", agent)
        return found.masked() if who.masks else found

    @router.get("/sessions/{session_id}/events", summary="Journal d'une session en JSONL")
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

    @router.delete("/sessions/{session_id}", summary="Supprime une session (RGPD)")
    async def forget(who: Who, session_id: SessionId) -> SessionDeletion:
        # Effacer un journal, ses fichiers et ses clés est irréversible et
        # concerne tous les agents de la session : portée ``admin``, sans
        # exception d'agent à vérifier.
        require(who, "admin")
        removed = await loom.delete_session(session_id, tenant_id=who.tenant)
        if not removed.events and not removed.artifacts and not removed.keys:
            raise UnknownSession(session_id)
        return removed

    @router.get("/sessions/{session_id}/report", summary="Consommation d'une session")
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
