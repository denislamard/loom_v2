# SPDX-License-Identifier: Apache-2.0
"""Un journal écrit par un code plus récent se lit : les champs inconnus sont ignorés (DEC-1).

Ajouter un champ optionnel ne change pas ``schema_version`` : un code plus ancien (retour
arrière, workers de versions mêlées) doit relire ce journal sans le déclarer illisible.
L'écriture reste stricte, et une version de schéma inconnue reste refusée.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import JsonValue, ValidationError

from loom_ia.adapters.stores import JsonlEventStore
from loom_ia.core.events import Event, EventDraft, RunStarted, ToolCompleted
from loom_ia.core.model import DEFAULT_TENANT, Message, SessionId, TextBlock, ToolOutput
from loom_ia.core.projections import fold, history
from loom_ia.replay.runner import read_journal
from loom_ia.testing import RunJournal, tool_call_message

SESSION = SessionId("tolerant")


def _path(root: Path) -> Path:
    return root / "journal" / DEFAULT_TENANT / f"{SESSION}.jsonl"


async def _written(root: Path) -> tuple[JsonlEventStore, list[dict[str, Any]]]:
    store = JsonlEventStore(root / "journal")
    journal = RunJournal(session_id=SESSION)
    journal.start("Bonjour")
    journal.complete()
    await store.append(journal.take(), expected_seq=0)
    rows = [json.loads(line) for line in _path(root).read_text(encoding="utf-8").splitlines()]
    return store, rows


def _rewrite(root: Path, rows: list[dict[str, Any]]) -> None:
    _path(root).write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


async def test_a_field_added_later_is_ignored_on_read(tmp_path: Path) -> None:
    store, rows = await _written(tmp_path)
    expected = await store.read(DEFAULT_TENANT, SESSION)
    for row in rows:
        row["champ_futur"] = {"a": 1}
        row["payload"]["autre_champ_futur"] = "x"
    _rewrite(tmp_path, rows)
    assert await store.read(DEFAULT_TENANT, SESSION) == expected
    assert await store.last_seq(DEFAULT_TENANT, SESSION) == len(expected)
    await store.aclose()


async def test_a_replay_reads_a_journal_with_a_later_field(tmp_path: Path) -> None:
    store, rows = await _written(tmp_path)
    rows[0]["champ_futur"] = 1
    _rewrite(tmp_path, rows)
    assert len(read_journal(_path(tmp_path))) == len(rows)
    await store.aclose()


async def test_an_unknown_schema_version_is_still_refused(tmp_path: Path) -> None:
    store, rows = await _written(tmp_path)
    rows[0]["schema_version"] = 2
    _rewrite(tmp_path, rows)
    with pytest.raises(Exception, match=r"schema|illisible"):
        await store.read(DEFAULT_TENANT, SESSION)
    await store.aclose()


def test_writing_stays_strict() -> None:
    journal = RunJournal(session_id=SESSION)
    journal.start("Bonjour")
    row = json.loads(journal.take()[0].model_dump_json())
    row["seq"] = 1
    row["champ_futur"] = 1
    with pytest.raises(ValidationError, match="Extra inputs"):
        Event.model_validate(row)


# --- State de session : des champs ajoutés, aucun type ni facette nouveaux ----------


def _state_journal(*, with_state: bool) -> RunJournal:
    """Run qui pose du state à l'ouverture et dont l'outil en écrit, ou le même sans state."""
    journal = RunJournal(session_id=SESSION)
    journal.start("Bonjour", state={"client_id": "CL-1"} if with_state else None)
    journal.model_turn(tool_call_message(("c1", "get_client", {})))
    state: dict[str, JsonValue] = {"client": "C-002"} if with_state else {}
    journal.tool_results({"c1": ToolOutput(blocks=(TextBlock(text="C-002"),), state=state)})
    return journal.model_turn(Message.assistant("fait")).complete()


def _derived(drafts: list[EventDraft]) -> list[tuple[str, str, str, list[str]]]:
    events = [draft.to_event(seq) for seq, draft in enumerate(drafts, start=1)]
    return [(e.type, e.category, e.status, sorted(e.facets)) for e in events]


def test_the_state_adds_no_type_and_no_facet() -> None:
    """Un type ou une facette inconnus rendent la session illisible à un loom plus ancien.

    Les valeurs, elles, peuvent changer (la taille d'un résultat compte son
    state) : un lecteur plus ancien les relit sans les connaître.
    """
    with_state = _state_journal(with_state=True).take()
    without = _state_journal(with_state=False).take()
    assert _derived(with_state) == _derived(without)
    started = with_state[0].payload
    assert isinstance(started, RunStarted) and started.state == {"client_id": "CL-1"}


async def test_a_reader_that_ignores_the_state_reads_the_journal(tmp_path: Path) -> None:
    """Retour arrière : un loom plus ancien ne connaît pas ``state``, et l'ignore.

    Comme tout champ ajouté, depuis la lecture tolérante de la 2.0.5 (DEC-1). Le
    simuler : renommer ``state`` en un champ que le code actuel ne connaît pas,
    à la même place (``run.started``, et ``output`` dans ``tool.completed``).
    """
    store = JsonlEventStore(tmp_path / "journal")
    journal = _state_journal(with_state=True)
    await store.append(journal.take(), expected_seq=0)
    expected = await store.read(DEFAULT_TENANT, SESSION)

    lines = _path(tmp_path).read_text(encoding="utf-8").splitlines()
    rows: list[dict[str, Any]] = [json.loads(line) for line in lines]
    renamed = 0
    for row in rows:
        where: dict[str, Any] = row["payload"]
        if row["type"] == "tool.completed":
            where = where["output"]
        elif row["type"] != "run.started":
            continue
        where["champ_futur"] = where.pop("state")
        renamed += 1
    assert renamed == 2
    _rewrite(tmp_path, rows)

    read = await store.read(DEFAULT_TENANT, SESSION)
    assert len(read) == len(expected)
    assert fold(read, journal.run_id).status == fold(expected, journal.run_id).status
    assert history(read) == history(expected)
    assert not any(isinstance(e.payload, RunStarted) and e.payload.state for e in read)
    assert not any(isinstance(e.payload, ToolCompleted) and e.payload.output.state for e in read)
    await store.aclose()
