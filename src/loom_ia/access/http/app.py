# SPDX-License-Identifier: Apache-2.0
"""API REST d'une instance loom (N2).

Cinq routes, sous ``/v1`` :

- ``GET  /agents`` : les agents publiés (``expose.rest``) ;
- ``POST /agents/{name}/runs`` : lancement **synchrone** ; la réponse est le
  résultat du run. Le corps est en JSON, ou en ``multipart/form-data`` pour
  joindre des images (``uploads``) ;
- ``GET  /runs/{run_id}`` : statut et résultat d'un run ;
- ``GET  /runs/{run_id}/events`` : le journal du run en SSE — ce qui est déjà
  écrit, puis la suite en direct s'il tourne encore. Les événements de ses
  sous-runs y sont mêlés, sauf avec ``?subruns=false`` ; le flux se ferme
  sur la clôture du run demandé ;
- ``GET  /sessions/{session_id}/report`` : consommation de toute une session
  (J3), ventilée par run, par rôle et par modèle.

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

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Annotated, Final

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from loom_ia.access.api import Loom, RunResult, StreamItem, UnknownRun
from loom_ia.access.http.auth import Caller, identify, require
from loom_ia.access.http.schemas import AgentInfo
from loom_ia.access.http.uploads import RUN_BODY, run_request
from loom_ia.agents.registry import UnknownAgent
from loom_ia.core.events import Event
from loom_ia.core.model import AttachmentError, RunId, SessionId
from loom_ia.usage import UsageReport

logger = logging.getLogger(__name__)

LOCAL_HOSTS: Final = frozenset({"127.0.0.1", "localhost", "::1"})


async def caller(request: Request) -> Caller:
    """Appelant de la requête, reconnu par les clés de l'instance servie."""
    loom: Loom = request.app.state.loom
    return identify(loom.config.security, request)


# L'appelant, injecté dans chaque route.
type Who = Annotated[Caller, Depends(caller)]


def create_app(loom: Loom, *, own: bool = False) -> FastAPI:
    """Application ASGI servant les agents d'une instance.

    ``own`` confie l'instance à l'application : elle la ferme à l'arrêt du
    serveur. Sans lui, la fermeture reste à l'appelant.
    """
    http = loom.config.server.http
    security = loom.config.security
    if not security.api_keys and http.host not in LOCAL_HOSTS:
        logger.warning(
            "API REST ouverte sur %s sans clé déclarée : ajouter 'security.api_keys' "
            "(loom keys create) pour en exiger une",
            http.host,
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        yield
        if own:
            await loom.aclose()

    app = FastAPI(title="loom-ia", summary="Agents loom exposés en HTTP", lifespan=lifespan)
    app.state.loom = loom
    router = APIRouter(prefix=f"{http.base_path}/v1")

    @app.exception_handler(UnknownAgent)
    @app.exception_handler(UnknownRun)
    async def _not_found(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse({"detail": _message(exc)}, status_code=status.HTTP_404_NOT_FOUND)

    @router.get("/agents", summary="Agents publiés par l'API")
    async def agents(who: Who) -> list[AgentInfo]:
        require(who, "read")
        return [AgentInfo.of(spec) for spec in loom.exposed("rest") if who.allows(spec.name)]

    @router.post(
        "/agents/{name}/runs",
        summary="Lance un run et attend sa fin",
        status_code=status.HTTP_201_CREATED,
        openapi_extra=RUN_BODY,
    )
    async def start(name: str, request: Request, who: Who) -> RunResult:
        require(who, "run", name)
        _published(loom, name)
        try:
            body, attachments = await run_request(request, loom.config.execution.attachments)
            if body.judges == "skip":
                # Se passer des juges retire un contrôle : réservé à l'administration.
                require(who, "admin")
            return await loom.run(
                name,
                body.message,
                attachments=attachments,
                session_id=body.session_id,
                context=body.context(),
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
        found = await loom.result(run_id, session_id=session_id)
        require(who, "read", found.agent)
        return found

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
        state = await loom.state(run_id, session_id=session_id)
        require(who, "read", state.agent)
        resumed = request.headers.get("last-event-id")
        after = int(resumed) if resumed and resumed.isdigit() else after_seq
        return EventSourceResponse(
            _messages(loom.follow(run_id, session_id=session_id, after_seq=after, subruns=subruns))
        )

    @router.get("/sessions/{session_id}/report", summary="Consommation d'une session")
    async def report(who: Who, session_id: SessionId) -> UsageReport:
        require(who, "read")
        found = await loom.report(session_id=session_id)
        if not found.runs:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Session {session_id} inconnue")
        for agent in dict.fromkeys(run.agent for run in found.runs):
            require(who, "read", agent)
        return found

    app.include_router(router)
    return app


def sse(item: StreamItem) -> dict[str, str]:
    """Message SSE d'un événement du journal ou d'un morceau du modèle."""
    if isinstance(item, Event):
        return {"event": item.type, "id": str(item.seq), "data": item.model_dump_json()}
    return {"event": f"chunk.{item.type}", "data": item.model_dump_json()}


async def _messages(events: AsyncGenerator[Event]) -> AsyncGenerator[dict[str, str]]:
    async for event in events:
        yield sse(event)


def _published(loom: Loom, name: str) -> None:
    """Refuse un agent inconnu ou non publié en REST."""
    published = [spec.name for spec in loom.exposed("rest")]
    if name not in published:
        raise UnknownAgent(name, published)


def _message(exc: Exception) -> str:
    return str(exc.args[0]) if exc.args else str(exc)
