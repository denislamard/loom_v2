# SPDX-License-Identifier: Apache-2.0
"""La mémoire (``loom.memory``) dans le moteur : approbation, refus, rejeu, variante, clients.

L'orchestrateur est simulé (``sdk: fake``), la mémoire tourne sur ses modèles
factices, sa base dans un dossier temporaire. Ce que loom garantit autour
d'elle :

- les écritures attendent une approbation humaine (``approval: always`` par
  défaut) ; une écriture refusée n'est jamais faite ;
- un run rejoué ne réécrit rien : le journal sert les résultats ;
- en variante, rien n'est exécuté : l'outil est déclaré au pire de ses actions ;
- chaque client a sa base (``store.path_env`` lu dans ses secrets).
"""

import asyncio
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final, cast

import pytest
import yaml
from conftest import DEV, secured

from loom_ia.access.api import Loom
from loom_ia.config import load_config
from loom_ia.core.events import ToolCalled, ToolCompleted
from loom_ia.core.model import RunStatus, SessionId, TenantId
from loom_ia.tools_builtin.memory.fake import FakeEmbedder
from loom_ia.tools_builtin.memory.models import Embedding, checkpoint

NOTE: Final = (
    "Le devis D-2026-042 de Mme Martin porte sur le remplacement d'un chauffe-eau de 200 L, "
    "envoyé le 2 septembre."
)


def call(**arguments: Any) -> dict[str, Any]:
    return {"tool_calls": [{"name": "memory", "arguments": arguments}]}


# Le script de l'orchestrateur simulé : chaque demande choisit ses réponses.
SCRIPT: Final[list[dict[str, Any]]] = [
    {"with_text": "Mémorise", **call(action="add_text", text=NOTE, title="Devis Martin")},
    {"with_text": "Mémorise", "text": "C'est noté."},
    {"with_text": "chauffe-eau", **call(action="search", query="chauffe-eau de Mme Martin")},
    {"with_text": "chauffe-eau", "text": "Un chauffe-eau de 200 L."},
    {"with_text": "Liste", **call(action="list_docs")},
    {"with_text": "Liste", "text": "Voilà la liste."},
    {"with_text": "Oublie", **call(action="delete", doc_id="{doc_id}")},
    {"with_text": "Oublie", "text": "C'est oublié."},
]


def script_deleting(doc_id: str) -> list[dict[str, Any]]:
    return [
        {**step, **call(action="delete", doc_id=doc_id)}
        if "tool_calls" in step and step["with_text"] == "Oublie"
        else step
        for step in SCRIPT
    ]


def write_config(
    base: Path,
    script: list[dict[str, Any]],
    *,
    tenants: list[dict[str, Any]] | None = None,
    other: list[dict[str, Any]] | None = None,
    files: bool = False,
) -> Path:
    """Une config : l'orchestrateur simulé, l'agent ``assistant`` et sa mémoire."""
    (base / "agents").mkdir(parents=True, exist_ok=True)
    store = {"path": "memoire.db"} if tenants is None else {"path_env": "MEMOIRE_BASE"}
    memory: dict[str, Any] = {
        "python": "loom.memory",
        "store": store,
        "project": "atelier",
        "models": {"kind": "fake"},
        "actions": ["search", "list_docs", "add_text", "delete"],
    }
    if files:
        (base / "workspace").mkdir(exist_ok=True)
        memory["actions"] = [*memory["actions"], "add_file"]
        memory["folders"] = [{"name": "workspace", "path": "workspace"}]
    config: dict[str, Any] = {
        "version": 1,
        "models": [
            {"id": "FAKE", "sdk": "fake", "model": "fake", "params": {"script": script}},
            # Un autre orchestrateur, pour les variantes.
            {"id": "AUTRE", "sdk": "fake", "model": "autre", "params": {"script": other or []}},
        ],
        "storage": {"events": {"backend": "jsonl", "path": "data"}},
    }
    if tenants is not None:
        config["tenants"] = tenants
    (base / "loom.yaml").write_text(
        yaml.safe_dump(secured(config), allow_unicode=True), encoding="utf-8"
    )
    agent = {
        "name": "assistant",
        "description": "Répond en s'appuyant sur la mémoire.",
        "main": {"model": "FAKE", "system": "Cherche dans la mémoire avant de répondre."},
        "tools": [memory],
    }
    (base / "agents" / "assistant.yaml").write_text(
        yaml.safe_dump(agent, allow_unicode=True), encoding="utf-8"
    )
    return base / "loom.yaml"


def completed(events: list[Any], action: str) -> list[ToolCompleted]:
    """Les appels de la mémoire à cette action, tels qu'ils se sont terminés."""
    asked = {
        e.payload.call_id
        for e in events
        if isinstance(e.payload, ToolCalled)
        and e.payload.tool_name == "memory"
        and e.payload.arguments.get("action") == action
    }
    return [
        e.payload
        for e in events
        if isinstance(e.payload, ToolCompleted) and e.payload.call_id in asked
    ]


def rendered(done: ToolCompleted) -> list[dict[str, Any]]:
    """La liste que la mémoire a rendue (``search``, ``list_docs``)."""
    data = done.output.data
    assert isinstance(data, list)
    return [cast(dict[str, Any], item) for item in data]


async def listed(
    loom: Loom, session: SessionId, tenant: TenantId | None = None
) -> list[dict[str, Any]]:
    """Les documents de la mémoire, lus par un run qui appelle ``list_docs``."""
    result = await loom.run(
        "assistant", "Liste les documents.", session_id=session, tenant=tenant, user=tenant or DEV
    )
    assert result.status == RunStatus.COMPLETED
    events = await loom.events(result.run_id, session_id=session, tenant_id=tenant)
    [done] = completed(events, "list_docs")
    return rendered(done)


async def test_reading_needs_no_approval_and_writing_waits_for_a_human(tmp_path: Path) -> None:
    session = SessionId("atelier")
    async with Loom(load_config(write_config(tmp_path, SCRIPT))) as loom:
        asked = await loom.run("assistant", "Mémorise ce devis.", session_id=session, user=DEV)
        assert asked.status == RunStatus.PAUSED
        [pending] = asked.pending_approvals
        assert pending.tool_name == "memory"
        assert pending.arguments["title"] == "Devis Martin"
        # Rien n'est écrit tant que personne n'a dit oui.
        assert await listed(loom, session) == []

        await loom.approve(asked.run_id, user=DEV, session_id=session)
        await loom.drain()
        done = await loom.result(asked.run_id, session_id=session)
        assert done.status == RunStatus.COMPLETED
        [document] = await listed(loom, session)
        assert document["title"] == "Devis Martin" and document["project"] == "atelier"

        found = await loom.run("assistant", "Et ce chauffe-eau ?", session_id=session, user=DEV)
        assert found.status == RunStatus.COMPLETED
        events = await loom.events(found.run_id, session_id=session)
        [hit] = completed(events, "search")
        assert [h["title"] for h in rendered(hit)] == ["Devis Martin"]


async def test_a_refused_write_is_never_made(tmp_path: Path) -> None:
    session = SessionId("atelier")
    async with Loom(load_config(write_config(tmp_path, SCRIPT))) as loom:
        asked = await loom.run("assistant", "Mémorise ce devis.", session_id=session, user=DEV)
        assert asked.status == RunStatus.PAUSED
        await loom.reject(asked.run_id, user=DEV, reason="pas maintenant", session_id=session)
        await loom.drain()
        done = await loom.result(asked.run_id, session_id=session)
        assert done.status == RunStatus.COMPLETED
        assert await listed(loom, session) == []


async def test_replaying_a_run_that_wrote_writes_nothing(tmp_path: Path) -> None:
    """Le document écrit puis effacé ne revient pas : le rejeu lit le journal."""
    session = SessionId("atelier")
    path = write_config(tmp_path, SCRIPT)
    async with Loom(load_config(path)) as loom:
        wrote = await loom.run("assistant", "Mémorise ce devis.", session_id=session, user=DEV)
        await loom.approve(wrote.run_id, user=DEV, session_id=session)
        await loom.drain()
        [document] = await listed(loom, session)
    path = write_config(tmp_path, script_deleting(document["doc_id"]))
    async with Loom(load_config(path)) as loom:
        forgot = await loom.run("assistant", "Oublie ce devis.", session_id=session, user=DEV)
        assert forgot.status == RunStatus.PAUSED
        await loom.approve(forgot.run_id, user=DEV, session_id=session)
        await loom.drain()
        assert await listed(loom, session) == []
    # Le rejeu tourne avec la config du run d'origine : sinon le script de
    # l'orchestrateur simulé, changé, ferait diverger la première requête.
    path = write_config(tmp_path, SCRIPT)
    async with Loom(load_config(path)) as loom:
        report = await loom.replay(wrote.run_id, session_id=session)
        assert report.identical
        served, replayed = report.tool_calls
        assert served == replayed == 1
        assert await listed(loom, session) == []


async def test_a_variant_never_writes(tmp_path: Path) -> None:
    """Rejoué par un autre orchestrateur, aucun appel de la mémoire n'est exécuté.

    Au rejeu, l'évaluation d'un appel ne peut que durcir la déclaration de
    l'outil (``strictest_side_effects``) : un outil unique qui permet une
    écriture est déclaré au pire de ses actions, et même ``search`` est
    refusé. C'est le prix d'un seul outil à actions ; rien n'est écrit.
    """
    session = SessionId("atelier")
    other = [
        {"with_text": "Mémorise", **call(action="search", query="chauffe-eau")},
        {"with_text": "Mémorise", **call(action="add_text", text="Autre.", title="Autre note")},
        {"with_text": "Mémorise", "text": "C'est noté autrement."},
    ]
    path = write_config(tmp_path, SCRIPT, other=other)
    async with Loom(load_config(path)) as loom:
        wrote = await loom.run("assistant", "Mémorise ce devis.", session_id=session, user=DEV)
        await loom.approve(wrote.run_id, user=DEV, session_id=session)
        await loom.drain()
        report = await loom.replay(
            wrote.run_id, session_id=session, mode="variant", models={"main": "AUTRE"}
        )
        assert report.comparison is not None
        assert report.comparison.calls == (("memory", "refused"), ("memory", "refused"))
        assert [d["title"] for d in await listed(loom, session)] == ["Devis Martin"]


async def test_each_client_has_its_own_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``store.path_env`` : la base de chaque client, lue dans ses secrets."""
    for client in ("dupont", "martin"):
        monkeypatch.setenv(f"{client.upper()}_MEMOIRE", str(tmp_path / f"{client}.db"))
    tenants = [
        {"id": client, "secrets": {"MEMOIRE_BASE": f"{client.upper()}_MEMOIRE"}}
        for client in ("dupont", "martin")
    ]
    dupont, martin = TenantId("dupont"), TenantId("martin")
    session = SessionId("atelier")
    path = write_config(tmp_path, SCRIPT, tenants=tenants)
    async with Loom(load_config(path)) as loom:
        asked = await loom.run(
            "assistant", "Mémorise ce devis.", session_id=session, tenant=dupont, user=dupont
        )
        await loom.approve(asked.run_id, user=dupont, session_id=session, tenant_id=dupont)
        await loom.drain()
        assert [d["title"] for d in await listed(loom, session, dupont)] == ["Devis Martin"]
        assert await listed(loom, session, martin) == []

        found = await loom.run(
            "assistant", "Et ce chauffe-eau ?", session_id=session, tenant=martin, user=martin
        )
        events = await loom.events(found.run_id, session_id=session, tenant_id=martin)
        [hit] = completed(events, "search")
        assert rendered(hit) == []
    # Deux bases, chacune au chemin que donnent les secrets de son client.
    assert (tmp_path / "dupont.db").is_file() and (tmp_path / "martin.db").is_file()


async def test_a_file_add_file_refuses_never_waits_for_a_human(tmp_path: Path) -> None:
    """Une extension refusée l'est avant l'approbation : le run ne s'arrête pas pour rien."""
    script = [
        {"with_text": "plaquette", **call(action="add_file", file="workspace/plaquette.odt")},
        {"with_text": "plaquette", "text": "Format refusé."},
        *SCRIPT,
    ]
    path = write_config(tmp_path, script, files=True)
    (tmp_path / "workspace" / "plaquette.odt").write_bytes(b"PK\x03\x04")
    async with Loom(load_config(path)) as loom:
        session = SessionId("atelier")
        done = await loom.run("assistant", "Mémorise la plaquette.", session_id=session, user=DEV)
        assert done.status == RunStatus.COMPLETED and not done.pending_approvals
        events = await loom.events(done.run_id, session_id=session)
        [refusal] = completed(events, "add_file")
        assert refusal.output.is_error
        assert await listed(loom, session) == []


async def test_deleting_an_unknown_document_never_waits_for_a_human(tmp_path: Path) -> None:
    """Un document inconnu ne peut pas être supprimé : le run ne s'arrête pas pour rien."""
    path = write_config(tmp_path, script_deleting("00000000-0000-0000-0000-000000000000"))
    async with Loom(load_config(path)) as loom:
        session = SessionId("atelier")
        done = await loom.run("assistant", "Oublie ce devis.", session_id=session, user=DEV)
        assert done.status == RunStatus.COMPLETED and not done.pending_approvals
        events = await loom.events(done.run_id, session_id=session)
        [refusal] = completed(events, "delete")
        assert refusal.output.is_error


async def test_adding_a_note_already_there_never_waits_for_a_human(tmp_path: Path) -> None:
    """Le même texte, déjà mémorisé : ``duplicate_of`` sans approbation, rien n'est écrit."""
    script = [
        {"with_text": "Encore", **call(action="add_text", text=NOTE, title="Encore le devis")},
        {"with_text": "Encore", "text": "Déjà noté."},
        *SCRIPT,
    ]
    session = SessionId("atelier")
    async with Loom(load_config(write_config(tmp_path, script))) as loom:
        first = await loom.run("assistant", "Mémorise ce devis.", session_id=session, user=DEV)
        await loom.approve(first.run_id, user=DEV, session_id=session)
        await loom.drain()
        [document] = await listed(loom, session)
        again = await loom.run("assistant", "Encore ce devis.", session_id=session, user=DEV)
        assert again.status == RunStatus.COMPLETED and not again.pending_approvals
        events = await loom.events(again.run_id, session_id=session)
        [done] = completed(events, "add_text")
        assert cast(dict[str, Any], done.output.data)["duplicate_of"] == document["doc_id"]
        assert [d["doc_id"] for d in await listed(loom, session)] == [document["doc_id"]]


async def test_a_write_cut_by_the_tool_timeout_stops_computing_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Délai de l'outil dépassé : le run continue, rien n'est écrit, le calcul s'arrête."""
    computed: list[str] = []

    def slow(
        self: FakeEmbedder, texts: Sequence[str], cancel: threading.Event | None = None
    ) -> list[Embedding]:
        found: list[Embedding] = []
        for text in texts:
            checkpoint(cancel)
            time.sleep(0.05)
            computed.append(text)
            found.append(self.embed_query(text))
        return found

    monkeypatch.setattr(FakeEmbedder, "embed_documents", slow)
    # Environ 40 passages : 2 s de calcul, pour un délai de 0,3 s.
    long_text = "\n\n".join(f"Paragraphe {i} : " + "mot " * 40 for i in range(400))
    script = [
        {"with_text": "Archive", **call(action="add_text", text=long_text, title="Archive")},
        {"with_text": "Archive", "text": "Trop long pour le délai."},
        *SCRIPT,
    ]
    path = write_config(tmp_path, script)
    agent_file = tmp_path / "agents" / "assistant.yaml"
    agent = yaml.safe_load(agent_file.read_text(encoding="utf-8"))
    agent["tools"][0]["timeout"] = 0.3
    agent["tools"][0]["actions"] = {
        "search": {},
        "list_docs": {},
        "add_text": {"approval": "never"},
    }
    agent_file.write_text(yaml.safe_dump(agent, allow_unicode=True), encoding="utf-8")
    async with Loom(load_config(path)) as loom:
        session = SessionId("atelier")
        done = await loom.run("assistant", "Archive ce texte.", session_id=session, user=DEV)
        assert done.status == RunStatus.COMPLETED
        events = await loom.events(done.run_id, session_id=session)
        [cut] = completed(events, "add_text")
        assert cut.output.is_error
        assert any("Délai dépassé" in getattr(b, "text", "") for b in cut.output.blocks)
        # Le calcul s'arrête au lot suivant : le texte en cours à l'annulation (dans son
        # sleep) va à son terme, puis plus rien — loin des 40 passages.
        cut_at = len(computed)
        await asyncio.sleep(0.3)
        settled = len(computed)
        assert cut_at <= settled <= cut_at + 1 and settled < 20
        await asyncio.sleep(0.3)
        assert len(computed) == settled
        assert await listed(loom, session) == []
