# SPDX-License-Identifier: Apache-2.0
"""La mémoire long terme (F6, J6.4d) : ``loom-notes`` branché en serveur MCP.

Le serveur est le vrai, celui de Denis, désigné par ``LOOM_NOTES_SERVER``
(le binaire ``loom-notes-mcp`` de son venv) ; sans la variable, ces essais
sont sautés — même sous ``--require-services`` : il n'est pas dans le dépôt.
Il tourne en modèles factices (``LOOM_NOTES_FAKE_MODELS``) sur un Qdrant
embarqué dans un dossier temporaire : pas de GPU, pas de Docker, et aucune
base existante n'est touchée.

Ce que loom ajoute à la mémoire :

- les écritures (``add_text``, ``add_url``, ``add_file``, ``update``,
  ``delete``) passent par une approbation humaine (``approval: always``) :
  la règle « sur demande explicite » devient un contrôle ;
- un run rejoué ne réécrit rien : le journal sert les résultats ;
- chaque client a sa mémoire : ``scope: tenant``, un serveur par client, son
  dossier de données lu dans ses secrets.
"""

import os
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

from loom_ia.access.api import Loom
from loom_ia.config import load_config
from loom_ia.core.events import ToolCompleted
from loom_ia.core.model import RunStatus, SessionId, TenantId

pytestmark = pytest.mark.integration

SERVER_ENV = "LOOM_NOTES_SERVER"
WRITES = ("add_text", "add_url", "add_file", "update", "delete")
READS = ("search", "get", "list_docs", "projects")
NOTE = (
    "Le devis D-2026-042 de Mme Martin porte sur le remplacement d'un chauffe-eau de 200 L, "
    "envoyé le 2 septembre."
)


@pytest.fixture
def memory_server() -> str:
    """Le binaire ``loom-notes-mcp`` ; saute l'essai s'il n'est pas désigné."""
    found = os.environ.get(SERVER_ENV, "")
    if not found:
        pytest.skip(f"{SERVER_ENV} absent : pas de serveur loom-notes pour cet essai")
    server = Path(found).expanduser()
    if not server.is_file():
        pytest.skip(f"{SERVER_ENV} : {server} introuvable")
    return str(server)


def call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {"tool_calls": [{"name": f"memoire__{name}", "arguments": arguments}]}


def write_config(
    base: Path,
    server: str,
    script: list[dict[str, Any]],
    *,
    tenants: list[dict[str, Any]] | None = None,
    other: list[dict[str, Any]] | None = None,
) -> Path:
    """Une config : l'orchestrateur simulé, la mémoire, l'agent ``assistant``."""
    (base / "agents").mkdir(parents=True, exist_ok=True)
    memoire: dict[str, Any] = {
        "name": "memoire",
        "transport": "stdio",
        "command": server,
        "env": {
            "LOOM_NOTES_FAKE_MODELS": "true",
            "FASTMCP_SHOW_SERVER_BANNER": "false",
            "FASTMCP_CHECK_FOR_UPDATES": "off",
            "FASTMCP_LOG_LEVEL": "WARNING",
        },
        # Les modèles se chargent au démarrage du serveur : on ne le ferme pas.
        "idle_timeout": None,
        "tools": {name: {"approval": "always"} for name in WRITES},
    }
    if tenants is None:
        memoire["env"]["LOOM_NOTES_DATA_DIR"] = str(base / "memoire")
    else:
        memoire["scope"] = "tenant"
        memoire["env_from"] = {"LOOM_NOTES_DATA_DIR": "MEMOIRE_DOSSIER"}
    config: dict[str, Any] = {
        "version": 1,
        "models": [
            {"id": "FAKE", "sdk": "fake", "model": "fake", "params": {"script": script}},
            # Un autre orchestrateur, pour les variantes.
            {"id": "AUTRE", "sdk": "fake", "model": "autre", "params": {"script": other or []}},
        ],
        "mcp_servers": [memoire],
        "storage": {"events": {"backend": "jsonl", "path": "data"}},
    }
    if tenants is not None:
        config["tenants"] = tenants
    (base / "loom.yaml").write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    agent = {
        "name": "assistant",
        "description": "Répond en s'appuyant sur la mémoire.",
        "main": {"model": "FAKE", "system": "Cherche dans la mémoire avant de répondre."},
        "tools": [{"mcp": "memoire"}],
    }
    (base / "agents" / "assistant.yaml").write_text(
        yaml.safe_dump(agent, allow_unicode=True), encoding="utf-8"
    )
    return base / "loom.yaml"


def completed(events: list[Any], tool: str) -> list[ToolCompleted]:
    return [
        e.payload
        for e in events
        if isinstance(e.payload, ToolCompleted) and e.payload.tool_name == f"memoire__{tool}"
    ]


# Le script de l'orchestrateur simulé : chaque demande choisit ses réponses.
SCRIPT: list[dict[str, Any]] = [
    {
        "with_text": "Mémorise",
        **call("add_text", {"text": NOTE, "title": "Devis Martin", "project": "dupont"}),
    },
    {"with_text": "Mémorise", "text": "C'est noté."},
    {"with_text": "chauffe-eau", **call("search", {"query": "chauffe-eau de Mme Martin"})},
    {"with_text": "chauffe-eau", "text": "Un chauffe-eau de 200 L."},
    {"with_text": "Liste", **call("list_docs", {})},
    {"with_text": "Liste", "text": "Voilà la liste."},
    {"with_text": "Oublie", **call("delete", {"doc_id": "{doc_id}"})},
    {"with_text": "Oublie", "text": "C'est oublié."},
]


def script_deleting(doc_id: str) -> list[dict[str, Any]]:
    return [
        {**step, **call("delete", {"doc_id": doc_id})}
        if "tool_calls" in step and step["with_text"] == "Oublie"
        else step
        for step in SCRIPT
    ]


def rendered(done: ToolCompleted) -> list[dict[str, Any]]:
    """La liste qu'un outil de la mémoire a rendue (``search``, ``list_docs``).

    FastMCP enveloppe une liste dans ``{"result": …}`` ; loom en fait le ``data``.
    """
    data = done.output.data
    assert isinstance(data, dict)
    found = data["result"]
    assert isinstance(found, list)
    return [cast(dict[str, Any], item) for item in found]


async def listed(
    loom: Loom, session: SessionId, tenant: TenantId | None = None
) -> list[dict[str, Any]]:
    """Les documents de la mémoire, lus par un run qui appelle ``list_docs``."""
    result = await loom.run("assistant", "Liste les documents.", session_id=session, tenant=tenant)
    assert result.status == RunStatus.COMPLETED
    events = await loom.events(result.run_id, session_id=session, tenant_id=tenant)
    [done] = completed(events, "list_docs")
    return rendered(done)


async def test_reading_needs_no_approval_and_writing_waits_for_a_human(
    tmp_path: Path, memory_server: str
) -> None:
    session = SessionId("atelier")
    async with Loom(load_config(write_config(tmp_path, memory_server, SCRIPT))) as loom:
        asked = await loom.run("assistant", "Mémorise ce devis.", session_id=session)
        assert asked.status == RunStatus.PAUSED
        [pending] = asked.pending_approvals
        assert pending.tool_name == "memoire__add_text"
        assert pending.arguments["title"] == "Devis Martin"
        # Rien n'est écrit tant que personne n'a dit oui.
        assert await listed(loom, session) == []

        await loom.approve(asked.run_id, by="l'artisan", session_id=session)
        await loom.drain()
        done = await loom.result(asked.run_id, session_id=session)
        assert done.status == RunStatus.COMPLETED
        [document] = await listed(loom, session)
        assert document["title"] == "Devis Martin" and document["project"] == "dupont"

        found = await loom.run("assistant", "Et ce chauffe-eau ?", session_id=session)
        assert found.status == RunStatus.COMPLETED
        events = await loom.events(found.run_id, session_id=session)
        [hit] = completed(events, "search")
        assert [h["title"] for h in rendered(hit)] == ["Devis Martin"]


async def test_a_refused_write_is_never_made(tmp_path: Path, memory_server: str) -> None:
    session = SessionId("atelier")
    async with Loom(load_config(write_config(tmp_path, memory_server, SCRIPT))) as loom:
        asked = await loom.run("assistant", "Mémorise ce devis.", session_id=session)
        assert asked.status == RunStatus.PAUSED
        await loom.reject(asked.run_id, by="l'artisan", reason="pas maintenant", session_id=session)
        await loom.drain()
        done = await loom.result(asked.run_id, session_id=session)
        assert done.status == RunStatus.COMPLETED
        events = await loom.events(asked.run_id, session_id=session)
        assert completed(events, "add_text") == [] or all(
            c.output.is_error for c in completed(events, "add_text")
        )
        assert await listed(loom, session) == []


async def test_replaying_a_run_that_wrote_writes_nothing(
    tmp_path: Path, memory_server: str
) -> None:
    """Le document écrit puis effacé ne revient pas : le rejeu lit le journal."""
    session = SessionId("atelier")
    path = write_config(tmp_path, memory_server, SCRIPT)
    async with Loom(load_config(path)) as loom:
        wrote = await loom.run("assistant", "Mémorise ce devis.", session_id=session)
        await loom.approve(wrote.run_id, by="l'artisan", session_id=session)
        await loom.drain()
        [document] = await listed(loom, session)
    path = write_config(tmp_path, memory_server, script_deleting(document["doc_id"]))
    async with Loom(load_config(path)) as loom:
        forgot = await loom.run("assistant", "Oublie ce devis.", session_id=session)
        assert forgot.status == RunStatus.PAUSED
        await loom.approve(forgot.run_id, by="l'artisan", session_id=session)
        await loom.drain()
        assert await listed(loom, session) == []
    # Le rejeu tourne avec la config du run d'origine : sinon le script de
    # l'orchestrateur simulé, changé, ferait diverger la première requête.
    path = write_config(tmp_path, memory_server, SCRIPT)
    async with Loom(load_config(path)) as loom:
        report = await loom.replay(wrote.run_id, session_id=session)
        assert report.identical
        served, replayed = report.tool_calls
        assert served == replayed == 1
        assert await listed(loom, session) == []


async def test_a_variant_reads_for_real_but_never_writes(
    tmp_path: Path, memory_server: str
) -> None:
    """Rejoué par un autre orchestrateur : ``search`` part, ``add_text`` est refusé.

    Ce sont les annotations du serveur qui le disent à loom : ``search`` est en
    lecture seule, ``add_text`` n'en porte aucune — il est donc irréversible.
    """
    session = SessionId("atelier")
    other = [
        {"with_text": "Mémorise", **call("search", {"query": "chauffe-eau"})},
        {
            "with_text": "Mémorise",
            **call(
                "add_text", {"text": "Autre chose.", "title": "Autre note", "project": "dupont"}
            ),
        },
        {"with_text": "Mémorise", "text": "C'est noté autrement."},
    ]
    path = write_config(tmp_path, memory_server, SCRIPT, other=other)
    async with Loom(load_config(path)) as loom:
        wrote = await loom.run("assistant", "Mémorise ce devis.", session_id=session)
        await loom.approve(wrote.run_id, by="l'artisan", session_id=session)
        await loom.drain()
        report = await loom.replay(
            wrote.run_id, session_id=session, mode="variant", models={"main": "AUTRE"}
        )
        assert report.comparison is not None
        assert report.comparison.calls == (
            ("memoire__search", "run"),
            ("memoire__add_text", "refused"),
        )
        assert [d["title"] for d in await listed(loom, session)] == ["Devis Martin"]


async def test_each_client_has_its_own_memory(
    tmp_path: Path, memory_server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``scope: tenant`` : un serveur par client, son dossier lu dans ses secrets."""
    for client in ("dupont", "martin"):
        monkeypatch.setenv(f"{client.upper()}_MEMOIRE", str(tmp_path / f"memoire-{client}"))
    tenants = [
        {"id": client, "secrets": {"MEMOIRE_DOSSIER": f"{client.upper()}_MEMOIRE"}}
        for client in ("dupont", "martin")
    ]
    dupont, martin = TenantId("dupont"), TenantId("martin")
    session = SessionId("atelier")
    path = write_config(tmp_path, memory_server, SCRIPT, tenants=tenants)
    async with Loom(load_config(path)) as loom:
        asked = await loom.run("assistant", "Mémorise ce devis.", session_id=session, tenant=dupont)
        await loom.approve(asked.run_id, by="Dupont", session_id=session, tenant_id=dupont)
        await loom.drain()
        assert [d["title"] for d in await listed(loom, session, dupont)] == ["Devis Martin"]
        assert await listed(loom, session, martin) == []

        found = await loom.run(
            "assistant", "Et ce chauffe-eau ?", session_id=session, tenant=martin
        )
        events = await loom.events(found.run_id, session_id=session, tenant_id=martin)
        [hit] = completed(events, "search")
        assert rendered(hit) == []
    # Deux bases, chacune dans le dossier de son client.
    assert (tmp_path / "memoire-dupont" / "qdrant").is_dir()
    assert (tmp_path / "memoire-martin" / "qdrant").is_dir()
