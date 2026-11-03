# SPDX-License-Identifier: Apache-2.0
"""Mémoire (``loom.memory``) : export, import, réindexation, évaluation, et ``loom memory``."""

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any, Final

import pytest
from conftest import ConfigFactory, demo_agent

from loom_ia.access.cli import main
from loom_ia.core.ports import ToolError
from loom_ia.tools_builtin.memory import maintenance
from loom_ia.tools_builtin.memory.maintenance import GoldenCase, evaluate, load_golden
from loom_ia.tools_builtin.memory.models import shared_models
from loom_ia.tools_builtin.memory.service import Extracted, MemoryService
from loom_ia.tools_builtin.memory.store import MemoryStore
from loom_ia.tools_builtin.memory_params import ChunkingSpec, ModelsSpec, RetrievalSpec

DOCS: Final = {
    "qdrant": ("Lancer Qdrant", "docker run qdrant avec restart unless-stopped"),
    "roles": ("Rôles loom-fs", "reference sourcecode workspace sont les rôles plafonds"),
    "arb": ("Localisation", "les fichiers ARB portent les traductions flutter"),
}


def service_on(path: Path, **chunking: int) -> MemoryService:
    return MemoryService(
        MemoryStore(path),
        shared_models(ModelsSpec(kind="fake")),
        RetrievalSpec(),
        ChunkingSpec(**chunking),
    )


async def filled(path: Path) -> tuple[MemoryService, dict[str, str]]:
    memory = service_on(path)
    ids: dict[str, str] = {}
    for key, (title, text) in DOCS.items():
        done = await memory.add(Extracted(title, text), kind="text", source=None, project="p")
        ids[key] = done.doc_id
    return memory, ids


async def test_export_then_import_into_another_base(tmp_path: Path) -> None:
    memory, ids = await filled(tmp_path / "a.db")
    exported = tmp_path / "export.jsonl"
    assert await maintenance.export(memory, exported) == 3
    lines = [json.loads(line) for line in exported.read_text(encoding="utf-8").splitlines()]
    assert {line["doc_id"] for line in lines} == set(ids.values())
    other = service_on(tmp_path / "b.db")
    done = await maintenance.import_(other, exported)
    assert (done.imported, done.skipped) == (3, 0)
    assert other.store.count() == memory.store.count()
    again = await maintenance.import_(other, exported)
    assert (again.imported, again.skipped) == (0, 3)


async def test_export_never_overwrites(tmp_path: Path) -> None:
    memory, _ = await filled(tmp_path / "a.db")
    existing = tmp_path / "export.jsonl"
    existing.write_text("à garder", encoding="utf-8")
    with pytest.raises(FileExistsError):
        await maintenance.export(memory, existing)
    assert existing.read_text(encoding="utf-8") == "à garder"


async def test_an_export_of_loom_notes_imports_as_is(tmp_path: Path) -> None:
    """Le format d'un export de loom-notes : les mêmes champs, une ligne par document."""
    line: dict[str, Any] = {
        "doc_id": "a086f05c-e7ec-47a2-b33b-66141146417c",
        "title": "Créer le conteneur Qdrant (Docker)",
        "project": "infra",
        "tags": ["docker"],
        "source_kind": "text",
        "source": None,
        "added_at": "2026-09-01T08:00:00.000000+00:00",
        "updated_at": None,
        "content_hash": "0" * 64,
        "text": "docker run -d --name qdrant --restart unless-stopped qdrant/qdrant",
    }
    exported = tmp_path / "loom-notes.jsonl"
    exported.write_text(json.dumps(line) + "\n\n", encoding="utf-8")
    memory = service_on(tmp_path / "m.db")
    done = await maintenance.import_(memory, exported)
    assert done.imported == 1
    doc = await memory.get(str(line["doc_id"]), project="infra")
    assert doc.added_at == line["added_at"] and doc.tags == ["docker"]


async def test_an_unreadable_line_is_named(tmp_path: Path) -> None:
    broken = tmp_path / "abime.jsonl"
    broken.write_text('{"doc_id": "x"}\n', encoding="utf-8")
    with pytest.raises(ToolError, match=r"abime\.jsonl, ligne 1 : document illisible"):
        await maintenance.import_(service_on(tmp_path / "m.db"), broken)


async def test_reindex_follows_the_chunking_and_the_model(tmp_path: Path) -> None:
    path = tmp_path / "m.db"
    memory = service_on(path)
    text = "\n\n".join(f"Paragraphe {i} " + "mot " * 40 for i in range(6))
    await memory.add(Extracted("Long", text), kind="text", source=None, project="p")
    assert memory.store.count() == (1, 1)
    finer = service_on(path, target_chars=300, max_chars=300, overlap_chars=0)
    assert await maintenance.reindex(finer) == 1
    assert finer.store.count()[1] > 1
    # Une base indexée par un autre modèle est refusée, puis réindexée.
    connection = sqlite3.connect(path)
    with connection:
        connection.execute("UPDATE meta SET value = 'autre' WHERE key = 'embedder'")
    connection.close()
    with pytest.raises(ToolError, match="loom memory reindex"):
        await service_on(path).search("paragraphe", project=None)
    await maintenance.reindex(service_on(path))
    assert await service_on(path).search("paragraphe", project=None)


async def test_evaluation_metrics_and_threshold(tmp_path: Path) -> None:
    memory, ids = await filled(tmp_path / "m.db")
    golden = tmp_path / "golden.jsonl"
    cases = [
        GoldenCase(query="docker qdrant restart", doc_id=ids["qdrant"]),
        GoldenCase(query="rôles plafonds", doc_id=ids["roles"], project="p"),
        GoldenCase(query="traductions ARB flutter", doc_id=ids["arb"], title="x"),
        GoldenCase(query="sujet inconnu", doc_id="00000000-0000-0000-0000-000000000000"),
    ]
    golden.write_text(
        "".join(case.model_dump_json(exclude_none=True) + "\n" for case in cases), encoding="utf-8"
    )
    loaded = load_golden(golden)
    assert len(loaded) == 4 and loaded[2].title == "x"
    report = await evaluate(memory, loaded, k=10)
    assert report.cases == 4
    assert [r.rank for r in report.results[:3]] == [1, 1, 1]
    assert report.results[3].rank is None and report.results[3].expected_score is None
    assert report.recall_at_1 == report.recall_at_5 == 0.75
    assert report.mrr == pytest.approx(0.75)
    assert report.suggested_min_score is not None and 0 < report.suggested_min_score <= 1


# --- loom memory --------------------------------------------------------------------------

JSONL: Final = {"events": {"backend": "jsonl", "path": "journal"}}


def memory_config(demo: ConfigFactory, **root: Any) -> Path:
    tool = {
        "python": "loom.memory",
        "store": {"path": "memoire.db"},
        "models": {"kind": "fake"},
        "actions": ["search", "add_text"],
    }
    return demo(
        agents=[demo_agent(name="assistant", tools=[tool]), demo_agent()], storage=JSONL, **root
    )


def test_the_cli_exports_imports_reindexes_and_evaluates(
    demo: ConfigFactory, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = memory_config(demo)
    _, ids = asyncio.run(filled(tmp_path / "memoire.db"))
    config = ["--config", str(path)]
    exported = tmp_path / "export.jsonl"
    assert main([*config, "memory", "export", "assistant", str(exported)]) == 0
    assert "3 document(s) écrit(s)" in capsys.readouterr().out
    assert main([*config, "memory", "export", "assistant", str(exported)]) == 2
    assert "existe déjà" in capsys.readouterr().err
    assert main([*config, "memory", "import", "assistant", str(exported)]) == 0
    assert "0 document(s) importé(s), 3 déjà présent(s)" in capsys.readouterr().out
    assert main([*config, "memory", "reindex", "assistant"]) == 0
    assert "3 document(s) réindexé(s)" in capsys.readouterr().out
    golden = tmp_path / "golden.jsonl"
    golden.write_text(
        json.dumps({"query": "docker qdrant", "doc_id": ids["qdrant"]}) + "\n", encoding="utf-8"
    )
    assert main([*config, "memory", "eval", "assistant", str(golden)]) == 0
    out = capsys.readouterr().out
    assert "ok       #1" in out and "1 cas — recall@1 1.00" in out
    assert main([*config, "memory", "eval", "assistant", str(golden), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["recall_at_1"] == 1.0


def test_an_agent_without_memory_is_refused(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    path = memory_config(demo)
    assert main(["--config", str(path), "memory", "reindex", "demo"]) == 2
    assert "L'agent 'demo' n'active pas loom.memory." in capsys.readouterr().err


def test_each_client_has_its_own_base(
    demo: ConfigFactory,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool = {
        "python": "loom.memory",
        "store": {"path_env": "MEMOIRE_BASE"},
        "models": {"kind": "fake"},
        "actions": ["search"],
    }
    tenants = [
        {"id": "dupont", "secrets": {"MEMOIRE_BASE": "MEMOIRE_DUPONT"}},
        {"id": "martin", "secrets": {"MEMOIRE_BASE": "MEMOIRE_MARTIN"}},
    ]
    path = demo(agents=[demo_agent(tools=[tool])], storage=JSONL, tenants=tenants)
    for name in ("dupont", "martin"):
        monkeypatch.setenv(f"MEMOIRE_{name.upper()}", str(tmp_path / f"{name}.db"))
    assert main(["--config", str(path), "memory", "reindex", "demo", "--tenant", "dupont"]) == 0
    assert str(tmp_path / "dupont.db") in capsys.readouterr().out
    assert (tmp_path / "dupont.db").exists() and not (tmp_path / "martin.db").exists()
