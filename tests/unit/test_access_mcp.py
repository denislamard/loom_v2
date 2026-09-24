# SPDX-License-Identifier: Apache-2.0
"""Accès MCP : les agents publiés en outils, client et serveur en process."""

import base64
import json
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

import pytest
from conftest import ANSWER, PNG, QUESTION, TREE_QUESTION, ConfigFactory, demo_agent

from loom_ia.access import Loom
from loom_ia.core.events import Event, RunCancelled
from loom_ia.core.model import Attachment, SessionId

pytest.importorskip("mcp", reason="extra 'mcp' absent")

from mcp import ClientSession
from mcp.client.session import ElicitationFnT
from mcp.shared.context import RequestContext
from mcp.shared.exceptions import McpError
from mcp.shared.memory import create_connected_server_and_client_session as connected
from mcp.types import (
    BlobResourceContents,
    ContentBlock,
    ElicitRequestParams,
    ElicitResult,
    Implementation,
    ReadResourceResult,
    TextContent,
    TextResourceContents,
)
from pydantic import AnyUrl

from loom_ia.access.mcp_server import (
    CANCEL_TOOL,
    REPORT_TOOL,
    STATUS_TOOL,
    create_server,
)
from loom_ia.access.resources import RUNS, SESSIONS, TEMPLATES

# Le rappel qu'un client branche pour répondre aux formulaires du serveur.
type Elicitation = ElicitationFnT

CLIENT = Implementation(name="atelier-client", version="1.0")

type ElicitAction = Literal["accept", "decline", "cancel"]


@asynccontextmanager
async def serving(
    path: Path, *, elicitation: Elicitation | None = None
) -> AsyncGenerator[tuple[Loom, ClientSession]]:
    """Une instance servie en MCP, et un client branché dessus.

    Avec ``elicitation``, le client déclare la capacité : c'est ce que le
    serveur regarde pour faire trancher une approbation dans la boucle.
    """
    async with Loom.from_config(path) as loom:
        async with connected(
            create_server(loom), client_info=CLIENT, elicitation_callback=elicitation
        ) as client:
            yield loom, client


async def test_each_published_agent_is_a_tool(demo: ConfigFactory) -> None:
    path = demo(agents=[demo_agent(), demo_agent(name="cache", expose={"mcp": False})])
    async with serving(path) as (_, client):
        listed = await client.list_tools()

    tools = {tool.name: tool for tool in listed.tools}
    assert set(tools) == {"demo", STATUS_TOOL, REPORT_TOOL, CANCEL_TOOL}
    assert tools["demo"].description == "Répond aux questions de calcul."
    assert tools["demo"].inputSchema["required"] == ["message"]
    assert "run_id" in (tools["demo"].outputSchema or {})["properties"]


async def test_calling_an_agent_runs_it(demo: ConfigFactory) -> None:
    async with serving(demo()) as (loom, client):
        result = await client.call_tool("demo", {"message": QUESTION})
        assert result.structuredContent is not None
        events = await loom.events(result.structuredContent["run_id"])

    assert not result.isError
    [content] = result.content
    assert isinstance(content, TextContent) and content.text == ANSWER
    assert result.structuredContent["status"] == "completed"
    assert [event.type for event in events][-1] == "run.completed"


async def test_run_status_reads_a_finished_run(demo: ConfigFactory) -> None:
    async with serving(demo()) as (_, client):
        first = await client.call_tool("demo", {"message": QUESTION})
        assert first.structuredContent is not None
        run_id = first.structuredContent["run_id"]
        again = await client.call_tool(STATUS_TOOL, {"run_id": run_id})

    assert again.structuredContent == first.structuredContent


async def test_a_session_gathers_two_calls(demo: ConfigFactory) -> None:
    session = SessionId("atelier")
    async with serving(demo()) as (loom, client):
        first = await client.call_tool("demo", {"message": QUESTION, "session_id": session})
        second = await client.call_tool("demo", {"message": "Et encore ?", "session_id": session})
        assert first.structuredContent is not None and second.structuredContent is not None
        events = await loom.events(second.structuredContent["run_id"], session_id=session)

    assert first.structuredContent["session_id"] == "atelier"
    assert events[0].seq > 1


async def test_unknown_agent_and_unknown_run_are_errors(demo: ConfigFactory) -> None:
    path = demo(agents=[demo_agent(), demo_agent(name="cache", expose={"mcp": False})])
    async with serving(path) as (_, client):
        absent = await client.call_tool("absent", {"message": QUESTION})
        hidden = await client.call_tool("cache", {"message": QUESTION})
        unknown = await client.call_tool(STATUS_TOOL, {"run_id": "run-absent"})
        missing = await client.call_tool("demo", {})

    assert absent.isError and hidden.isError and unknown.isError
    assert missing.isError and "message" in _text(missing.content)


async def test_the_journal_is_the_same_as_by_the_python_access(demo: ConfigFactory) -> None:
    path = demo()
    async with serving(path) as (loom, client):
        result = await client.call_tool("demo", {"message": QUESTION})
        assert result.structuredContent is not None
        by_mcp = await loom.events(result.structuredContent["run_id"])

    async with Loom.from_config(path) as loom:
        direct = await loom.run("demo", QUESTION)
        by_python = await loom.events(direct.run_id)

    assert [event.type for event in by_mcp] == [event.type for event in by_python]


# --- Ressources et arrêt (J5.4b) ---------------------------------------------


async def test_the_two_indexes_are_listed_and_the_rest_is_templated(demo: ConfigFactory) -> None:
    async with serving(demo()) as (_, client):
        listed = await client.list_resources()
        gabarits = await client.list_resource_templates()

    assert [str(entry.uri) for entry in listed.resources] == [RUNS, SESSIONS]
    assert all(entry.mimeType == "application/json" for entry in listed.resources)
    # Ce qu'on ne peut pas énumérer sans tout ouvrir se construit d'un gabarit.
    assert [template.uriTemplate for template in gabarits.resourceTemplates] == [
        uri for uri, _, _ in TEMPLATES
    ]
    assert all(template.description for template in gabarits.resourceTemplates)


async def test_the_index_of_runs_is_the_page_of_the_python_access(demo: ConfigFactory) -> None:
    async with serving(demo()) as (loom, client):
        first = await loom.run("demo", QUESTION, session_id=SessionId("c-1"))
        await loom.run("demo", QUESTION, session_id=SessionId("c-2"))
        page = _read(await client.read_resource(AnyUrl(RUNS)))
        journaux = _read(await client.read_resource(AnyUrl(SESSIONS)))
        direct = await loom.runs()

    assert [run["run_id"] for run in page["runs"]] == [run.run_id for run in direct.runs]
    assert first.run_id in [run["run_id"] for run in page["runs"]]
    # La ressource dit ce que la page dit : ce qu'elle a coûté, et si elle est bornée.
    assert page["scanned"] == direct.scanned and page["truncated"] is direct.truncated
    assert [record["session_id"] for record in journaux] == ["c-2", "c-1"]


async def test_a_run_and_its_trace_are_read_by_uri(demo: ConfigFactory) -> None:
    async with serving(demo()) as (loom, client):
        ran = await loom.run("demo", QUESTION, session_id=SessionId("c-1"))
        dans = "?session_id=c-1"
        run = _read(await client.read_resource(AnyUrl(f"{RUNS}/{ran.run_id}{dans}")))
        trace = _read(await client.read_resource(AnyUrl(f"{RUNS}/{ran.run_id}/events{dans}")))
        fiche = _read(await client.read_resource(AnyUrl(f"{SESSIONS}/c-1")))
        journal = _read(await client.read_resource(AnyUrl(f"{SESSIONS}/c-1/events")))
        entiers = await loom.events(ran.run_id, session_id=SessionId("c-1"))

    assert (run["status"], run["text"]) == ("completed", ANSWER)
    assert [event["type"] for event in trace] == [event.type for event in entiers]
    assert fiche["session_id"] == "c-1" and [r["agent"] for r in fiche["runs"]] == ["demo"]
    # Le journal de la session porte ses marqueurs, l'arbre du run non.
    assert len(journal) >= len(trace)


async def test_an_unknown_resource_says_so(demo: ConfigFactory) -> None:
    async with serving(demo()) as (_, client):
        for uri in (
            f"{RUNS}/run-absent",
            f"{SESSIONS}/c-absente",
            f"{SESSIONS}/c-absente/events",
            "loom://autre-chose",
        ):
            with pytest.raises(McpError, match="introuvable"):
                await client.read_resource(AnyUrl(uri))


async def test_the_bytes_of_a_file_are_read_by_resource(tree: ConfigFactory) -> None:
    async with serving(tree()) as (loom, client):
        photo = Attachment(data=PNG, media_type="image/png", name="photo.png")
        ran = await loom.run("demo", TREE_QUESTION, attachments=[photo])
        [record] = [art for art in ran.artifacts if art.origin == "attachment"]
        # L'URI que le journal publie se lit telle quelle : un lien rendu par
        # loom n'est plus un lien mort.
        directe = await client.read_resource(AnyUrl(record.uri))
        alias = record.uri.replace("artifact://", "loom://artifacts/")
        par_alias = await client.read_resource(AnyUrl(alias))

    assert _blob(directe) == PNG and _blob(par_alias) == PNG
    assert directe.contents[0].mimeType == "image/png"


async def test_a_file_beyond_the_read_bound_is_refused(demo: ConfigFactory) -> None:
    # Le déport d'une sortie d'outil peut passer la borne de lecture ; l'octet
    # est là, la ressource refuse de le rendre, et elle dit laquelle borne.
    path = demo(execution={"attachments": {"max_bytes": 64}})
    uri = "artifact://default/c-1/gros.bin"
    async with serving(path) as (loom, client):
        await loom.artifacts.put(uri, b"x" * 65)
        with pytest.raises(McpError, match="au-delà de la limite de lecture"):
            await client.read_resource(AnyUrl(uri))


async def test_a_file_of_another_client_is_not_found(demo: ConfigFactory) -> None:
    path = demo(tenants=[{"id": "default"}, {"id": "dupont"}])
    sien = "artifact://dupont/c-1/abc.png"
    mien = "artifact://default/c-1/abc.png"
    async with serving(path) as (loom, client):
        await loom.artifacts.put(sien, PNG)
        await loom.artifacts.put(mien, PNG)
        # Le serveur en stdio sert `default` : le fichier existe, et il est
        # pourtant « introuvable » — on n'apprend pas qu'il est là.
        with pytest.raises(McpError, match="introuvable"):
            await client.read_resource(AnyUrl(sien))
        # Le même, chez soi, se lit.
        assert _blob(await client.read_resource(AnyUrl(mien))) == PNG


async def test_cancel_stops_a_run_and_says_when_there_was_nothing_to_stop(
    demo: ConfigFactory,
) -> None:
    async with serving(demo()) as (loom, client):
        ran = await loom.run("demo", QUESTION)
        fini = await client.call_tool(CANCEL_TOOL, {"run_id": ran.run_id})
        laisse = await loom.submit("demo", QUESTION)
        arrete = await client.call_tool(CANCEL_TOOL, {"run_id": laisse, "by": "l'artisan"})
        events = await loom.events(laisse)

    # Un run déjà terminé n'est pas une erreur : il n'y avait rien à arrêter.
    assert fini.isError is not True
    assert fini.structuredContent == {"run_id": ran.run_id, "cancelled": False}
    assert "déjà terminé" in _text(fini.content)
    assert arrete.structuredContent == {"run_id": laisse, "cancelled": True}
    # `by` est de l'audit, porté par la charge et non par une facette.
    [stopped] = [event.payload for event in events if event.type == "run.cancelled"]
    assert isinstance(stopped, RunCancelled) and stopped.by == "l'artisan"


# --- Approbations : elicitation, ou pause (J4.5) ------------------------------


def _answers(decision: str, *, action: ElicitAction = "accept", motif: str = "") -> Elicitation:
    """Client qui répond au formulaire comme le ferait un humain."""

    async def callback(
        context: RequestContext[ClientSession, Any], params: ElicitRequestParams
    ) -> ElicitResult:
        assert "envoyer_email" in params.message
        if action != "accept":
            return ElicitResult(action=action)
        return ElicitResult(action="accept", content={"decision": decision, "motif": motif})

    return callback


async def test_an_approval_is_elicited_when_the_client_can(atelier: ConfigFactory) -> None:
    async with serving(atelier(), elicitation=_answers("accorder", motif="devis vérifié")) as (
        loom,
        client,
    ):
        result = await client.call_tool("demo", {"message": "Relance."})
        assert result.structuredContent is not None
        events = await loom.events(result.structuredContent["run_id"])

    assert result.structuredContent["status"] == "completed"
    assert result.structuredContent["pending_approvals"] == []
    types = [event.type for event in events]
    # Le run n'est jamais passé par la pause : la décision est venue dans la boucle.
    assert "approval.requested" in types and "approval.granted" in types
    assert "paused" not in [event.facets.get("to_state") for event in events]
    assert _by(events, "approval.granted") == ["mcp:atelier-client"]
    assert _reason(events, "approval.granted") == "devis vérifié"


async def test_an_elicited_refusal_never_calls_the_tool(atelier: ConfigFactory) -> None:
    async with serving(atelier(), elicitation=_answers("refuser", motif="mauvais montant")) as (
        loom,
        client,
    ):
        result = await client.call_tool("demo", {"message": "Relance."})
        assert result.structuredContent is not None
        events = await loom.events(result.structuredContent["run_id"])

    assert result.structuredContent["status"] == "completed"
    assert _by(events, "approval.rejected") == ["mcp:atelier-client"]
    assert "tool.called" not in [event.type for event in events]


async def test_a_dismissed_form_does_not_authorise_the_effect(atelier: ConfigFactory) -> None:
    async with serving(atelier(), elicitation=_answers("accorder", action="cancel")) as (
        loom,
        client,
    ):
        result = await client.call_tool("demo", {"message": "Relance."})
        assert result.structuredContent is not None
        events = await loom.events(result.structuredContent["run_id"])

    # Fermer le formulaire n'est pas refuser, mais l'effet n'a pas été autorisé.
    assert _reason(events, "approval.rejected") == "demande fermée sans réponse"
    assert "tool.called" not in [event.type for event in events]


async def test_without_elicitation_the_tool_hands_back_a_paused_run(
    atelier: ConfigFactory,
) -> None:
    async with serving(atelier()) as (loom, client):
        paused = await client.call_tool("demo", {"message": "Relance."})
        assert paused.structuredContent is not None
        run_id = paused.structuredContent["run_id"]
        relu = await client.call_tool(STATUS_TOOL, {"run_id": run_id})

        await loom.approve(run_id, by="denis")
        await loom.drain()
        fini = await client.call_tool(STATUS_TOOL, {"run_id": run_id})
        assert fini.structuredContent is not None

    # Un run qui attend un humain n'est pas un échec : l'outil rend la main.
    assert not paused.isError
    assert paused.structuredContent["status"] == "paused"
    assert [a["tool_name"] for a in paused.structuredContent["pending_approvals"]] == [
        "envoyer_email"
    ]
    dit = _text(paused.content)
    assert "en attente d'approbation" in dit and run_id in dit and STATUS_TOOL in dit
    assert relu.structuredContent == paused.structuredContent
    assert fini.structuredContent["status"] == "completed"


def _by(events: Sequence[Event], type_: str) -> list[str]:
    return [str(event.facets.get("by")) for event in events if event.type == type_]


def _reason(events: Sequence[Event], type_: str) -> str:
    [payload] = [event.payload for event in events if event.type == type_]
    return str(getattr(payload, "reason", ""))


def _text(content: Sequence[ContentBlock]) -> str:
    return " ".join(part.text for part in content if isinstance(part, TextContent))


def _read(result: ReadResourceResult) -> Any:
    """Le JSON d'une ressource lue."""
    [content] = result.contents
    assert isinstance(content, TextResourceContents)
    assert content.mimeType == "application/json"
    return json.loads(content.text)


def _blob(result: ReadResourceResult) -> bytes:
    [content] = result.contents
    assert isinstance(content, BlobResourceContents)
    return base64.b64decode(content.blob)
