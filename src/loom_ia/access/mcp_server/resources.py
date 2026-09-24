# SPDX-License-Identifier: Apache-2.0
"""Ressources MCP : le journal en lecture seule (N5, #32).

Un outil fait travailler ; une ressource se lit. Ce module expose donc en
ressources ce que l'API REST rend par ses routes de lecture (J5.4a) — les runs
d'un client, leurs traces, les sessions — plus les **octets** d'un fichier du
run, qui n'étaient jusqu'ici désignés que par leur URI.

Deux index se listent (``resources/list``) :

- ``loom://runs`` : les runs du client, du plus récemment écrit au plus ancien,
  c'est-à-dire la page de ``Loom.runs()`` ;
- ``loom://sessions`` : ses journaux de session, le plus récent d'abord.

Le reste se construit depuis un gabarit (``resources/templates/list``), faute
de pouvoir énumérer sans tout ouvrir :

- ``loom://runs/{run_id}`` : le résultat d'un run, comme ``run_status`` ;
- ``loom://runs/{run_id}/events`` : son journal, sous-runs compris ;
- ``loom://sessions/{session_id}`` : la fiche d'une session ;
- ``loom://sessions/{session_id}/events`` : son journal entier ;
- ``loom://artifacts/{client}/{session}/{fichier}`` : les octets d'un fichier.

**Rien dans une URI ne nomme un client** : c'est la clé de la requête qui le
dit (#34). L'URI d'un fichier porte bien un client, puisque celle du journal
le porte — il est alors *vérifié*, et un fichier d'un autre client est
« introuvable », comme un fichier absent : on n'apprend pas qu'il existe.

Portées (#39, J5.2a) : lire une ressource demande ``read``, et sans
``read_content`` le contenu est masqué exactement comme une relecture par
REST — ce sont les mêmes lectures. ``loom://sessions`` est refusé à une clé
limitée à certains agents, comme ``GET /v1/sessions`` : une liste de sessions
ne dit pas de quels agents sont leurs runs. ``loom://runs``, elle, se filtre
honnêtement, chaque run nommant son agent.

Pas d'abonnement : le serveur déclare ``subscribe: false``. Un client qui veut
le direct prend le flux SSE de REST ; MCP n'a que des notifications de
progression, et cette limite-là n'a pas changé.

Les octets d'un fichier sont bornés par ``execution.attachments.max_bytes``,
faute d'une borne de lecture à part : un fichier plus gros n'est pas lisible
par ressource, et reste accessible par le stockage.
"""

import json
import mimetypes
from collections.abc import Sequence
from typing import Any
from urllib.parse import parse_qs, urlsplit

import mcp.types as types
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.shared.exceptions import McpError
from pydantic import AnyUrl

from loom_ia.access.api import Loom, UnknownRun, UnknownSession
from loom_ia.access.caller import Caller
from loom_ia.access.resources import (
    ARTIFACTS,
    EVENTS,
    JSON_TYPE,
    RUNS,
    SESSIONS,
    TEMPLATES,
)
from loom_ia.core.events import Event, redacted_all
from loom_ia.core.model import (
    ARTIFACT_SCHEME,
    ArtifactLocation,
    AttachmentPolicy,
    RunId,
    SessionId,
)
from loom_ia.core.model.base import DomainModel
from loom_ia.core.ports import ArtifactNotFound


class ResourceReader:
    """Lit les ressources d'une instance, au nom d'un appelant.

    Une instance par requête : l'appelant ne change pas en cours de lecture.
    """

    def __init__(self, loom: Loom, caller: Caller, *, policy: AttachmentPolicy) -> None:
        self._loom = loom
        self._caller = caller
        self._policy = policy

    # --- Ce que le serveur publie --------------------------------------------

    def listed(self) -> list[types.Resource]:
        """Les index lisibles par cet appelant, ou aucun s'il ne peut pas lire."""
        if not self._caller.may("read"):
            return []
        found = [
            types.Resource(
                uri=AnyUrl(RUNS),
                name="runs",
                title="Runs du client",
                description="Les runs du client, du plus récemment écrit au plus ancien",
                mimeType=JSON_TYPE,
            )
        ]
        if self._whole():
            found.append(
                types.Resource(
                    uri=AnyUrl(SESSIONS),
                    name="sessions",
                    title="Sessions du client",
                    description="Les journaux de session du client, le plus récent d'abord",
                    mimeType=JSON_TYPE,
                )
            )
        return found

    def templates(self) -> list[types.ResourceTemplate]:
        """Les gabarits, ou aucun si l'appelant ne peut pas lire."""
        if not self._caller.may("read"):
            return []
        return [
            types.ResourceTemplate(uriTemplate=uri, name=name, description=description)
            for uri, name, description in TEMPLATES
        ]

    # --- Lecture --------------------------------------------------------------

    async def read(self, raw: str) -> list[ReadResourceContents]:
        """Contenu de la ressource, ou une erreur MCP qui dit ce qui manque."""
        if not self._caller.may("read"):
            raise _refused("Clé sans la portée 'read'")
        # Un run d'une session nommée ne se retrouve pas par son seul
        # identifiant : la session s'ajoute en paramètre, comme en REST.
        uri, session = _split(raw)
        if uri.startswith(ARTIFACT_SCHEME):
            # L'URI que le journal publie : un lien rendu par loom se lit tel quel.
            return await self._artifact(uri)
        if uri.startswith(ARTIFACTS):
            return await self._artifact(f"{ARTIFACT_SCHEME}{uri.removeprefix(ARTIFACTS)}")
        if uri == RUNS:
            return [_json(await self._runs())]
        if uri == SESSIONS:
            return [_json(await self._sessions())]
        if uri.startswith(f"{RUNS}/"):
            return await self._run(uri.removeprefix(f"{RUNS}/"), session)
        if uri.startswith(f"{SESSIONS}/"):
            return await self._session(uri.removeprefix(f"{SESSIONS}/"))
        raise _unknown(uri)

    async def _runs(self) -> dict[str, Any]:
        page = await self._loom.runs(tenant_id=self._caller.tenant)
        # Chaque run dit son agent : la liste se filtre honnêtement, là où
        # celle des sessions doit être refusée.
        kept = tuple(run for run in page.runs if self._caller.allows(run.agent))
        return page.model_copy(update={"runs": kept}).model_dump(mode="json")

    async def _sessions(self) -> list[dict[str, Any]]:
        if not self._whole():
            raise _refused(
                "Clé limitée à certains agents : la liste des sessions ne peut pas être filtrée"
            )
        records = await self._loom.sessions(tenant_id=self._caller.tenant)
        return [record.model_dump(mode="json") for record in records]

    async def _run(self, rest: str, session_id: SessionId | None) -> list[ReadResourceContents]:
        trace = rest.endswith(EVENTS)
        run_id = RunId(rest.removesuffix(EVENTS) if trace else rest)
        if not run_id:
            raise _unknown(f"{RUNS}/{rest}")
        tenant = self._caller.tenant
        try:
            # Le droit sur l'agent se vérifie avant de rendre quoi que ce soit,
            # et il demande de savoir de quel agent est le run.
            state = await self._loom.state(run_id, session_id=session_id, tenant_id=tenant)
            self._allowed(state.agent)
            if trace:
                events = await self._loom.events(run_id, session_id=session_id, tenant_id=tenant)
                return [_json(self._shown(events))]
            found = await self._loom.result(run_id, session_id=session_id, tenant_id=tenant)
        except (UnknownRun, UnknownSession) as exc:
            raise _unknown(_said(exc)) from None
        return [_json(self._sent(found.masked() if self._caller.masks else found))]

    async def _session(self, rest: str) -> list[ReadResourceContents]:
        trace = rest.endswith(EVENTS)
        session_id = SessionId(rest.removesuffix(EVENTS) if trace else rest)
        if not session_id:
            raise _unknown(f"{SESSIONS}/{rest}")
        tenant = self._caller.tenant
        try:
            if trace:
                events = await self._loom.export_session(session_id, tenant_id=tenant)
                if not events:
                    raise _unknown(f"{SESSIONS}/{rest}")
                for agent in dict.fromkeys(event.agent for event in events if event.agent):
                    self._allowed(agent)
                return [_json(self._shown(events))]
            info = await self._loom.session(session_id, tenant_id=tenant)
        except (UnknownRun, UnknownSession) as exc:
            raise _unknown(_said(exc)) from None
        for agent in dict.fromkeys(run.agent for run in info.runs):
            self._allowed(agent)
        return [_json(self._sent(info.masked() if self._caller.masks else info))]

    async def _artifact(self, uri: str) -> list[ReadResourceContents]:
        try:
            location = ArtifactLocation.parse(uri)
        except ValueError:
            raise _unknown(uri) from None
        # Un fichier d'un autre client est introuvable, comme un fichier absent :
        # on n'apprend pas qu'il existe.
        if location.tenant != self._caller.tenant:
            raise _unknown(uri)
        try:
            data = await self._loom.artifact(uri)
        except ArtifactNotFound:
            raise _unknown(uri) from None
        if len(data) > self._policy.max_bytes:
            raise _refused(
                f"{location.name} : {len(data)} octets, au-delà de la limite de lecture "
                f"({self._policy.max_bytes} octets, execution.attachments.max_bytes)"
            )
        return [ReadResourceContents(content=data, mime_type=_media(location.name))]

    # --- Ce que l'appelant a le droit de voir ---------------------------------

    def _whole(self) -> bool:
        """Vrai si la clé n'est pas limitée à certains agents."""
        key = self._caller.key
        return key is None or not key.agents

    def _allowed(self, agent: str) -> None:
        if not self._caller.allows(agent):
            raise _refused(f"Clé non autorisée sur l'agent {agent!r}")

    def _shown(self, events: Sequence[Event]) -> list[dict[str, Any]]:
        """Événements en JSON, privés de leur contenu sans ``read_content``."""
        if self._caller.masks:
            return redacted_all(events)
        return [event.model_dump(mode="json") for event in events]

    def _sent(self, model: DomainModel) -> dict[str, Any]:
        return model.model_dump(mode="json")


def _split(uri: str) -> tuple[str, SessionId | None]:
    """L'URI sans sa requête, et la session qu'elle nomme s'il y en a une."""
    parts = urlsplit(uri)
    if not parts.query:
        return uri, None
    named = parse_qs(parts.query).get("session_id", [])
    return uri.removesuffix(f"?{parts.query}"), SessionId(named[0]) if named else None


def _json(payload: object) -> ReadResourceContents:
    return ReadResourceContents(
        content=json.dumps(payload, ensure_ascii=False), mime_type=JSON_TYPE
    )


def _media(name: str) -> str:
    """Type d'un fichier, deviné sur son extension."""
    guessed, _ = mimetypes.guess_type(name)
    return guessed or "application/octet-stream"


def _said(exc: Exception) -> str:
    return str(exc.args[0]) if exc.args else str(exc)


def _unknown(what: str) -> McpError:
    return McpError(types.ErrorData(code=types.INVALID_PARAMS, message=f"{what} : introuvable"))


def _refused(message: str) -> McpError:
    return McpError(types.ErrorData(code=types.INVALID_REQUEST, message=message))
