# SPDX-License-Identifier: Apache-2.0
"""State v2 : qui écrit (``writers``), corrections E1 à E4, extraction et ``loom.state``.

Analyse : ``docs/state_v2_analyse.md``.
"""

import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from conftest import ConfigFactory, demo_agent
from pydantic import JsonValue, ValidationError

from loom_ia.adapters.stores import InMemoryEventStore
from loom_ia.agents import StateKey
from loom_ia.config import load_config
from loom_ia.core.events import ToolCalled, ToolCompleted
from loom_ia.core.model import (
    CallerContext,
    JsonBlock,
    PendingCall,
    RunId,
    RunState,
    RunStatus,
    SessionId,
    TenantId,
    ToolOutput,
    ToolSpec,
)
from loom_ia.core.ports import ToolContext, ToolError
from loom_ia.core.projections import fold
from loom_ia.core.state_extract import ExtractError, Extraction, ModelKey, extract
from loom_ia.engine import StateKeyRule, StateRules, StateSourceRule, ToolExecutor
from loom_ia.engine.executor import ToolEvent
from loom_ia.runtime import build_agent
from loom_ia.testing import RunJournal, tool_call_message
from loom_ia.tools_builtin.state import StateTool

# --- Outillage ---------------------------------------------------------------------------


@dataclass
class Writer:
    """Outil minimal qui rend ``result``."""

    spec: ToolSpec
    result: ToolOutput
    calls: list[ToolContext] = field(default_factory=list[ToolContext])

    async def invoke(self, arguments: dict[str, JsonValue], context: ToolContext) -> ToolOutput:
        self.calls.append(context)
        return self.result


def spec(name: str) -> ToolSpec:
    return ToolSpec.model_validate(
        {
            "name": name,
            "description": name,
            "kind": "python",
            "input_schema": {"type": "object", "properties": {}},
        }
    )


def writes(name: str, state: dict[str, JsonValue], value: JsonValue = None) -> Writer:
    data: JsonValue = value if value is not None else {"ok": True}
    output = ToolOutput(blocks=(JsonBlock(data=data),), data=data, state=state)
    return Writer(spec(name), output)


def awaiting(*names: str) -> RunState:
    journal = RunJournal(agent="demo", tenant_id=TenantId("default"))
    journal.start("?", context=CallerContext(tenant_id=TenantId("default"), user_id="u1"))
    events = [draft.to_event(seq) for seq, draft in enumerate(journal.take(), start=1)]
    calls = tuple(PendingCall(call_id=f"c{i}", name=n, arguments={}) for i, n in enumerate(names))
    return fold(events, journal.run_id).model_copy(
        update={"status": RunStatus.AWAITING_TOOLS, "pending_calls": calls}
    )


async def outputs(executor: ToolExecutor, state: RunState) -> list[ToolOutput]:
    events: list[ToolEvent] = [event async for event in executor.run_batch(state)]
    return [e.output for e in events if isinstance(e, ToolCompleted)]


TEXT = {"type": "string"}


# --- Lot 1 : writers (S1), E1, E2 ------------------------------------------------------


async def test_by_default_any_tool_still_writes_a_declared_key() -> None:
    """Défaut ``[app, tools]`` : le comportement d'avant writers, sans ``from`` (B3)."""
    rules = StateRules({"note": StateKeyRule(schema=TEXT)})
    [output] = await outputs(
        ToolExecutor([writes("fiche", {"note": "x"})], state_rules=rules), awaiting("fiche")
    )
    assert (output.state, output.state_refused) == ({"note": "x"}, ())


async def test_a_key_kept_for_the_application_refuses_a_tool() -> None:
    """E1 : ``writers: [app]`` réserve la clé à l'application."""
    rules = StateRules({"signature": StateKeyRule(schema=TEXT, writers=frozenset({"app"}))})
    [output] = await outputs(
        ToolExecutor([writes("fiche", {"signature": "Pirate"})], state_rules=rules),
        awaiting("fiche"),
    )
    assert output.state == {}
    assert output.state_refused == ("signature non posé : un outil ne l'écrit pas (writers : app)",)
    assert not output.is_error


async def test_the_model_tool_writes_only_keys_open_to_the_model() -> None:
    """Les écritures de l'outil monté pour ``loom.state`` sont celles du modèle (B4)."""
    rules = StateRules(
        {
            "client": StateKeyRule(schema=TEXT, writers=frozenset({"model"})),
            "ville": StateKeyRule(schema=TEXT),
        },
        model_tool="state",
    )
    executor = ToolExecutor(
        [writes("state", {"client": "C-1", "ville": "Dijon"}), writes("fiche", {"client": "C-2"})],
        state_rules=rules,
    )
    by_model, by_tool = await outputs(executor, awaiting("state", "fiche"))
    assert by_model.state == {"client": "C-1"}
    assert by_model.state_refused == (
        "ville non posé : le modèle ne l'écrit pas (writers : app, tools)",
    )
    assert by_tool.state == {}
    assert by_tool.state_refused == ("client non posé : un outil ne l'écrit pas (writers : model)",)


async def test_no_tool_erases_a_required_key() -> None:
    """E2 : effacer une clé obligatoire bloquerait chaque run suivant de la session."""
    rules = StateRules({"signature": StateKeyRule(schema=TEXT, required=True)})
    [output] = await outputs(
        ToolExecutor([writes("efface", {"signature": None})], state_rules=rules),
        awaiting("efface"),
    )
    assert output.state == {}
    assert output.state_refused == (
        "signature non posé : clé obligatoire : seule l'application la remplace, rien ne l'efface",
    )
    # La remplacer reste permis.
    [output] = await outputs(
        ToolExecutor([writes("pose", {"signature": "Lamard"})], state_rules=rules),
        awaiting("pose"),
    )
    assert output.state == {"signature": "Lamard"}


def test_the_application_cannot_write_a_key_kept_for_the_model() -> None:
    rules = StateRules({"client": StateKeyRule(schema=TEXT, writers=frozenset({"model"}))})
    assert rules.opening({"client": "C-1"}, {}) == [
        "client : l'application ne l'écrit pas (writers : model)"
    ]


def test_a_source_needs_tools_among_the_writers() -> None:
    with pytest.raises(ValidationError, match="'tools' doit être dans writers"):
        StateKey.model_validate(
            {"schema": TEXT, "writers": ["app"], "from": [{"tool": "fiche", "path": "/code"}]}
        )


def test_a_source_cannot_erase_a_required_key() -> None:
    with pytest.raises(ValidationError, match="effacerait une clé obligatoire"):
        StateKey.model_validate(
            {"schema": TEXT, "required": True, "from": [{"tool": "fiche", "value": None}]}
        )


def test_writers_are_named_once_and_never_empty() -> None:
    with pytest.raises(ValidationError, match="cité deux fois"):
        StateKey.model_validate({"schema": TEXT, "writers": ["app", "app"]})
    with pytest.raises(ValidationError):
        StateKey.model_validate({"schema": TEXT, "writers": []})
    with pytest.raises(ValidationError):
        StateKey.model_validate({"schema": TEXT, "writers": ["humain"]})
    assert StateKey.model_validate({"schema": TEXT}).writers == ("app", "tools")


def test_a_source_rule_keeps_its_writers() -> None:
    rule = StateKeyRule(schema=TEXT, sources={"fiche": StateSourceRule(path="/code")})
    assert rule.writers == frozenset({"app", "tools"})


# --- E3 : un chemin dans le résultat de loom.sql --------------------------------------


# Un outil Python nommé ``sql`` qui rend ``{columns, rows}`` comme ``loom.sql``, sur
# SQLite (bibliothèque standard) : les essais de ``loom.state`` tournent aussi sans
# l'extra ``sql`` (job « noyau seul » du CI). Seul l'essai E3 vise ``loom.sql`` lui-même.
OUTILS_SQL = """
import sqlite3
from pathlib import Path

from loom_ia.tools import tool


@tool
def sql(action: str, database: str, sql: str) -> dict[str, object]:
    \"\"\"Lit la base crm (SELECT seulement).\"\"\"
    with sqlite3.connect(Path(__file__).parent / "crm.db") as conn:
        cursor = conn.execute(sql)
        columns = [d[0] for d in cursor.description]
        rows = [list(r) for r in cursor.fetchall()]
    return {"database": database, "columns": columns, "rows": rows, "truncated": False}
"""
IMPORTS = ["outils_acces", "outils_sql"]


@pytest.fixture
def crm(tmp_path: Path) -> Path:
    (tmp_path / "outils_sql.py").write_text(OUTILS_SQL, encoding="utf-8")
    path = tmp_path / "crm.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            "CREATE TABLE clients (id INTEGER PRIMARY KEY, code TEXT);"
            "INSERT INTO clients VALUES (261, 'C-261'), (2, 'C-002');"
        )
    conn.close()
    return path


def sql_tool(base: Path) -> dict[str, Any]:
    return {
        "python": "loom.sql",
        "databases": [
            {"name": "crm", "sqlite": {"path": str(base)}, "tables": {"clients": ["select"]}}
        ],
    }


async def test_a_path_in_a_sql_result_is_warned_at_mount(
    demo: ConfigFactory, crm: Path, caplog: pytest.LogCaptureFixture
) -> None:
    pytest.importorskip("sqlglot", reason="extra 'sql' absent")
    state = {
        "client": {
            "schema": TEXT,
            "from": [{"tool": "sql", "path": "/rows/0/1", "on_missing": "ignore"}],
        }
    }
    path = demo(agents=[demo_agent(tools=[sql_tool(crm)], state=state)])
    with caplog.at_level(logging.WARNING, logger="loom_ia.runtime.wiring"):
        agent = build_agent(load_config(path), "demo", InMemoryEventStore())
    await agent.aclose()
    [warning] = [r for r in caplog.records if r.name == "loom_ia.runtime.wiring"]
    assert "loom.sql" in warning.getMessage() and "préférer loom.state" in warning.getMessage()


# --- Lot 2 : extraction (module pur) ----------------------------------------------------

SQL: dict[str, JsonValue] = {
    "database": "crm",
    "columns": ["id", "code", "nom"],
    "rows": [[261, "C-261", "Renard"], [2, "C-002", "Renault"]],
    "row_count": 2,
    "truncated": False,
}
CLIENT = Extraction(fields={"id": "id", "code": "code"})
SELECTION = Extraction(columns={"ids": "id"}, label="libelle")


def test_fields_take_one_row_of_a_table() -> None:
    assert extract(SQL, CLIENT, row=1) == {"id": 2, "code": "C-002"}
    assert extract(SQL, Extraction(value="code"), row=0) == "C-261"


def test_several_rows_need_a_row_and_none_is_refused() -> None:
    with pytest.raises(
        ExtractError, match=r"2 lignes : désigner la bonne avec 'match'.* ou 'row' \(0 à 1\)"
    ):
        extract(SQL, CLIENT)
    with pytest.raises(ExtractError, match="aucune ligne"):
        extract({**SQL, "rows": []}, CLIENT)
    with pytest.raises(ExtractError, match="ligne 5 hors du résultat"):
        extract(SQL, CLIENT, row=5)
    single: dict[str, JsonValue] = {**SQL, "rows": [[261, "C-261", "Renard"]]}
    assert extract(single, CLIENT) == {"id": 261, "code": "C-261"}


def test_columns_take_every_row_with_the_label() -> None:
    got = extract(SQL, SELECTION, label="mes clients")
    assert got == {"ids": [261, 2], "libelle": "mes clients"}
    assert extract(SQL, SELECTION) == {"ids": [261, 2]}
    with pytest.raises(ExtractError, match="retirer 'row'"):
        extract(SQL, SELECTION, row=0)


def test_a_truncated_result_gives_no_list() -> None:
    with pytest.raises(ExtractError, match="tronqué"):
        extract({**SQL, "truncated": True}, SELECTION)
    # Une ligne d'un résultat tronqué reste juste.
    assert extract({**SQL, "truncated": True}, CLIENT, row=0) == {"id": 261, "code": "C-261"}


def test_missing_or_doubled_columns_are_named() -> None:
    with pytest.raises(ExtractError, match="colonne 'id' absente du résultat \\(colonnes : code"):
        extract({**SQL, "columns": ["code", "nom", "x"]}, CLIENT, row=0)
    # ``SELECT d.id, c.id`` : deux colonnes du même nom, aux valeurs différentes.
    with pytest.raises(
        ExtractError, match="présente 2 fois, avec des valeurs différentes \\(ligne 0\\)"
    ):
        extract({**SQL, "columns": ["id", "code", "id"]}, CLIENT, row=0)
    with pytest.raises(ExtractError, match="présente 2 fois, avec des valeurs différentes"):
        extract({**SQL, "columns": ["id", "code", "id"]}, SELECTION)


def test_a_doubled_column_with_the_same_value_is_read() -> None:
    """``SELECT id, code, *`` (essai t3 du 28/10) : les doubles ont la même valeur."""
    doubled: dict[str, JsonValue] = {
        "columns": ["id", "code", "id", "code", "nom"],
        "rows": [[261, "C-261", 261, "C-261", "Renard"], [2, "C-002", 2, "C-002", "Renault"]],
        "truncated": False,
    }
    assert extract(doubled, CLIENT, row=0) == {"id": 261, "code": "C-261"}
    assert extract(doubled, SELECTION) == {"ids": [261, 2]}
    # Une seule ligne en désaccord suffit à refuser la liste.
    mixed: dict[str, JsonValue] = {
        **doubled,
        "rows": [[261, "C-261", 261, "C-261", "Renard"], [2, "C-002", 9, "C-002", "Renault"]],
    }
    assert extract(mixed, CLIENT, row=0) == {"id": 261, "code": "C-261"}
    with pytest.raises(ExtractError, match="valeurs différentes \\(ligne 1\\)"):
        extract(mixed, SELECTION)


def test_records_and_a_single_object_are_rows() -> None:
    records: JsonValue = [{"id": 1, "code": "C-001"}, {"id": 2, "code": "C-002"}]
    assert extract(records, CLIENT, row=1) == {"id": 2, "code": "C-002"}
    assert extract(records, SELECTION, label="x") == {"ids": [1, 2], "libelle": "x"}
    assert extract({"id": 7, "code": "C-007", "ville": "Dijon"}, CLIENT) == {
        "id": 7,
        "code": "C-007",
    }
    with pytest.raises(ExtractError, match="colonne 'code' absente"):
        extract([{"id": 1}], CLIENT)


def test_text_has_nothing_to_extract_and_a_label_needs_a_field() -> None:
    with pytest.raises(ExtractError, match="pas de données structurées"):
        extract("Bonjour", CLIENT)
    with pytest.raises(ExtractError, match="ne prend pas de libellé"):
        extract(SQL, CLIENT, row=0, label="x")


# --- Lot 2 : ToolContext.results et keeps_state -----------------------------------------


@dataclass
class Reader:
    """Outil qui lit un résultat du run par ``ToolContext.results``."""

    spec: ToolSpec
    ref: str
    seen: list[JsonValue] = field(default_factory=list[JsonValue])
    keeps: list[bool] = field(default_factory=list[bool])

    async def invoke(self, arguments: dict[str, JsonValue], context: ToolContext) -> ToolOutput:
        assert context.results is not None
        self.keeps.append(context.keeps_state)
        self.seen.append(await context.results.read(self.ref))
        return ToolOutput.text("lu")


def answered(*calls: tuple[str, str, ToolOutput]) -> RunState:
    """Un run dont un premier tour a déjà ses résultats, et qui attend ``lire``."""
    journal = RunJournal(agent="demo", tenant_id=TenantId("default"))
    journal.start("?")
    empty: dict[str, JsonValue] = {}
    journal.model_turn(tool_call_message(*((cid, name, empty) for cid, name, _ in calls)))
    journal.tool_results({cid: output for cid, _, output in calls})
    journal.model_turn(tool_call_message(("c9", "lire", {})))
    events = [draft.to_event(seq) for seq, draft in enumerate(journal.take(), start=1)]
    return fold(events, journal.run_id)


async def test_a_tool_reads_an_earlier_result_of_the_run() -> None:
    sql = ToolOutput(blocks=(JsonBlock(data=SQL),), data=SQL)
    state = answered(("c1", "sql", sql))
    reader = Reader(spec("lire"), "result:1")
    rules = StateRules({"client": StateKeyRule(schema={"type": "object"})})
    await outputs(ToolExecutor([reader], state_rules=rules), state)
    assert reader.seen == [SQL]
    assert reader.keeps == [True]


async def test_an_unknown_reference_is_a_tool_error_that_lists_the_results() -> None:
    sql = ToolOutput(blocks=(JsonBlock(data=SQL),), data=SQL)
    reader = Reader(spec("lire"), "result:4")
    [output] = await outputs(ToolExecutor([reader]), answered(("c1", "sql", sql)))
    assert output.is_error
    assert "Référence inconnue : result:4" in output.as_text
    assert "result:1 (sql)" in output.as_text
    # Sans state déclaré, le run ne tient pas de state.
    assert reader.keeps == [False]


# --- Lot 3 : loom.state, de bout en bout (modèle simulé, base SQLite) -------------------


def sql(query: str) -> dict[str, JsonValue]:
    return {"action": "query", "database": "crm", "sql": query}


def turn(asked: str, *calls: tuple[str, dict[str, JsonValue]]) -> dict[str, Any]:
    return {"with_text": asked, "tool_calls": [{"name": n, "arguments": a} for n, a in calls]}


SCRIPT: list[dict[str, Any]] = [
    # Une fiche : la recherche, puis le state depuis son résultat.
    turn("Fiche", ("sql", sql("SELECT id, code FROM clients WHERE code = 'C-261'"))),
    turn("Fiche", ("state", {"set": "client", "result": "result:1"})),
    {"with_text": "Fiche", "text": "Voici la fiche de C-261."},
    # Une liste : la sélection ; le client s'efface de lui-même (clears).
    turn("Liste", ("sql", sql("SELECT id, code FROM clients ORDER BY id"))),
    turn(
        "Liste",
        ("state", {"set": "selection", "result": "result:1", "label": "tous les clients"}),
    ),
    {"with_text": "Liste", "text": "Deux clients."},
    # Inventer : sans résultat, rien ne se pose.
    turn("Invente", ("state", {"set": "client", "result": "result:7"})),
    {"with_text": "Invente", "text": "Rien à poser."},
]

MODEL_STATE: dict[str, Any] = {
    "client": {
        "description": "Client dont parle la conversation.",
        "schema": {
            "type": "object",
            "properties": {"id": {"type": "integer"}, "code": {"type": "string"}},
            "required": ["id", "code"],
            "additionalProperties": False,
        },
        "writers": ["model"],
        "extract": {"fields": {"id": "id", "code": "code"}},
    },
    "selection": {
        "description": "Liste de travail.",
        "schema": {
            "type": "object",
            "properties": {
                "ids": {"type": "array", "items": {"type": "integer"}, "maxItems": 100},
                "libelle": {"type": "string"},
            },
            "required": ["ids"],
        },
        "writers": ["model"],
        "extract": {"columns": {"ids": "id"}, "label": "libelle"},
        "clears": ["client"],
    },
    "signature": {"schema": TEXT, "writers": ["app"]},
}


def state_agent(base: Path, **changes: Any) -> dict[str, Any]:
    agent: dict[str, Any] = {
        "main": {"model": "FAKE", "system_file": "demo.md", "context": ["session_state"]},
        "tools": [{"python": "sql"}, {"python": "loom.state"}],
        "state": MODEL_STATE,
        "max_iterations": 6,
    }
    return demo_agent(**{**agent, **changes})


def model(script: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"id": "FAKE", "sdk": "fake", "model": "fake-1", "params": {"script": script}}]


async def test_the_model_poses_and_clears_from_results(demo: ConfigFactory, crm: Path) -> None:
    from loom_ia.access.api import Loom
    from loom_ia.core.model import SessionId

    session = SessionId("s1")
    path = demo(imports=IMPORTS, agents=[state_agent(crm)], models=model(SCRIPT))
    async with Loom.from_config(path) as loom:
        first = await loom.run("demo", "Fiche", session_id=session, state={"signature": "L"})
        after_first = await loom.session_state(session)
        second = await loom.run("demo", "Liste", session_id=session)
        after_second = await loom.session_state(session)
        third = await loom.run("demo", "Invente", session_id=session)
        after_third = await loom.session_state(session)
        events = await loom.export_session(session)
        # Rejeu identique (state servi du journal) et variante (loom.state relancé).
        reports = [
            await loom.replay(run.run_id, session_id=session, mode=mode)
            for run in (first, second, third)
            for mode in ("exact", "variant")
        ]

    for report in reports:
        assert report.identical, (report.mode, report.divergence)
    assert first.ok and second.ok and third.ok
    assert after_first == {"signature": "L", "client": {"id": 261, "code": "C-261"}}
    assert after_second == {
        "signature": "L",
        "selection": {"ids": [2, 261], "libelle": "tous les clients"},
    }
    assert after_third == after_second
    invented = [
        e.payload.output
        for e in events
        if isinstance(e.payload, ToolCompleted) and e.run_id == third.run_id
    ]
    assert invented[0].is_error and "Référence inconnue : result:7" in invented[0].as_text


async def test_the_application_cannot_pose_a_model_key(demo: ConfigFactory, crm: Path) -> None:
    from loom_ia.access.api import Loom
    from loom_ia.core.model import SessionId

    path = demo(imports=IMPORTS, agents=[state_agent(crm)], models=model(SCRIPT))
    async with Loom.from_config(path) as loom:
        with pytest.raises(ValueError, match="client : l'application ne l'écrit pas"):
            await loom.run("demo", "Fiche", session_id=SessionId("s2"), state={"client": {"id": 1}})


async def test_the_system_prompt_asks_the_model_to_keep_its_keys(
    demo: ConfigFactory, crm: Path
) -> None:
    agent = build_agent(
        load_config(demo(imports=IMPORTS, agents=[state_agent(crm)], models=model(SCRIPT))),
        "demo",
        InMemoryEventStore(),
    )
    rules = agent.context.tools.state_rules
    assert rules.model_tool == "state"
    hint = rules.hint(())
    assert hint.endswith(
        "Tiens toi-même à jour client, selection avec l'outil state, sans demander et sans en "
        "parler : le state doit toujours dire de quoi parle la conversation. Pose une clé dès "
        "qu'un résultat la désigne ; quand la conversation change de sujet, remplace-la ou "
        "efface-la."
    )
    (state_spec,) = [s for s in agent.context.tools.specs if s.name == "state"]
    assert "- client : Client dont parle la conversation. (une ligne, colonnes code, id)" in (
        state_spec.description
    )
    assert "toutes les lignes, ids ← id ; libellé dans libelle ('label')" in (
        state_spec.description
    )
    assert (state_spec.side_effects, state_spec.approval) == ("none", "never")
    # Essai t5 du 28/10 : Haiku proposait d'enregistrer la sélection au lieu de la poser.
    assert "sans demander l'accord de l'utilisateur, et sans en parler" in state_spec.description
    await agent.aclose()


def test_an_agent_without_model_keys_keeps_its_hint() -> None:
    """B2 : la consigne d'avant ne change pas d'un octet."""
    rules = StateRules({"client": StateKeyRule(schema=TEXT, description="Client.")})
    assert rules.hint(()).endswith("Clés :\n- client : Client.")
    with_tool = StateRules(
        {"client": StateKeyRule(schema=TEXT, description="Client.")}, model_tool="state"
    )
    assert with_tool.hint(()) == rules.hint(())


async def test_references_are_shown_with_loom_state(demo: ConfigFactory, crm: Path) -> None:
    """E4 : sans rôle ni sous-agent, ``loom.state`` suffit à montrer ``[result:N]``."""
    agent = build_agent(
        load_config(demo(imports=IMPORTS, agents=[state_agent(crm)], models=model(SCRIPT))),
        "demo",
        InMemoryEventStore(),
    )
    tools = agent.context.tools
    view = tools.view(awaiting("sql"))
    assert tools.shows_refs(view)
    plain = build_agent(
        load_config(demo(imports=IMPORTS, agents=[demo_agent(tools=[{"python": "sql"}])])),
        "demo",
        InMemoryEventStore(),
    )
    assert not plain.context.tools.shows_refs(plain.context.tools.view(awaiting("sql")))
    await agent.aclose()
    await plain.aclose()


@pytest.mark.parametrize(
    ("changes", "said"),
    [
        ({"tools": [{"python": "calculer"}]}, "n'a pas l'outil loom.state"),
        (
            {"main": {"model": "FAKE", "system_file": "demo.md"}},
            "le modèle écrit 'client', 'selection' sans la voir",
        ),
        (
            {
                "main": {
                    "model": "FAKE",
                    "system_file": "demo.md",
                    "context": [{"session_state": ["client"]}],
                }
            },
            "le modèle écrit 'selection' sans la voir",
        ),
    ],
)
def test_model_keys_need_the_tool_and_must_be_shown(
    demo: ConfigFactory, crm: Path, changes: dict[str, Any], said: str
) -> None:
    from loom_ia.config import ConfigError

    with pytest.raises(ConfigError, match=said):
        load_config(
            demo(imports=IMPORTS, agents=[state_agent(crm, **changes)], models=model(SCRIPT))
        )


def test_loom_state_without_model_keys_is_refused(demo: ConfigFactory, crm: Path) -> None:
    from loom_ia.config import ConfigError

    agent = demo_agent(tools=[{"python": "loom.state"}], state={"note": {"schema": TEXT}})
    with pytest.raises(ConfigError, match="aucune clé du state n'a 'model'"):
        load_config(demo(agents=[agent]))


@pytest.mark.parametrize(
    ("extract", "said"),
    [
        ({}, "une seule des trois"),
        ({"value": "id", "fields": {"id": "id"}}, "une seule des trois"),
        ({"value": "id", "label": "x"}, "pas avec 'value'"),
        ({"fields": {"id": "id"}, "label": "id"}, "à la fois label et colonne"),
    ],
)
def test_extract_rules(extract: dict[str, Any], said: str) -> None:
    with pytest.raises(ValidationError, match=said):
        StateKey.model_validate({"schema": TEXT, "writers": ["model"], "extract": extract})


def test_model_and_extract_go_together() -> None:
    with pytest.raises(ValidationError, match="vont ensemble"):
        StateKey.model_validate({"schema": TEXT, "writers": ["model"]})
    with pytest.raises(ValidationError, match="vont ensemble"):
        StateKey.model_validate({"schema": TEXT, "extract": {"value": "id"}})


# --- Lot 3 : refus de l'outil ------------------------------------------------------------


@dataclass
class Results:
    """``RunResults`` d'essai : un seul résultat, ``result:1``."""

    data: JsonValue

    async def read(self, ref: str) -> JsonValue:
        if ref != "result:1":
            raise ToolError(f"Référence inconnue : {ref}.")
        return self.data


def context(data: JsonValue = None, *, keeps: bool = True) -> ToolContext:
    return ToolContext(
        tenant_id=TenantId("default"),
        session_id=SessionId("s"),
        run_id=RunId("r"),
        call_id="c1",
        agent="demo",
        results=Results(SQL if data is None else data),
        keeps_state=keeps,
    )


def state_tool(max_value_bytes: int = 1024) -> StateTool:
    keys = (
        ModelKey(
            name="client",
            description="Client.",
            schema={
                "type": "object",
                "properties": {"id": {"type": "integer"}, "code": {"type": "string"}},
                "required": ["id", "code"],
            },
            extraction=CLIENT,
        ),
        ModelKey(
            name="selection",
            description="Liste.",
            schema={"type": "object", "properties": {"ids": {"maxItems": 1}}},
            extraction=SELECTION,
        ),
        ModelKey(
            name="signature",
            description="Obligatoire.",
            schema=TEXT,
            extraction=Extraction(value="code"),
            required=True,
        ),
    )
    return StateTool(keys, max_value_bytes)


async def test_the_tool_sets_and_clears() -> None:
    tool = state_tool()
    got = await tool.invoke({"set": "client", "result": "result:1", "row": 1}, context())
    assert got.state == {"client": {"id": 2, "code": "C-002"}}
    assert got.data == {"set": "client", "value": {"id": 2, "code": "C-002"}, "from": "result:1"}
    cleared = await tool.invoke({"clear": "client"}, context())
    assert (cleared.state, cleared.data) == ({"client": None}, {"cleared": "client"})


@pytest.mark.parametrize(
    ("arguments", "said"),
    [
        ({}, "'set' \\(avec 'result'\\) ou 'clear'"),
        ({"set": "client", "clear": "client"}, "l'un des deux"),
        ({"set": "inconnue", "result": "result:1"}, "Clé 'inconnue' inconnue ici"),
        ({"set": "client"}, "donner 'result'"),
        ({"set": "client", "result": "result:9"}, "Référence inconnue : result:9"),
        ({"set": "client", "result": "result:1"}, "2 lignes : désigner la bonne avec 'match'"),
        ({"set": "client", "result": "result:1", "match": {"code": "C-999"}}, "aucune ligne"),
        ({"set": "client", "result": "result:1", "row": 0, "match": {"id": 2}}, "l'un des deux"),
        ({"set": "client", "result": "result:1", "match": {}}, "'match' : un objet"),
        ({"set": "client", "result": "résultat 1"}, "'result' : la référence"),
        (
            {"set": "selection", "result": "result:1", "match": {"id": 2}},
            "retirer 'row' et 'match'",
        ),
        ({"set": "client", "result": "result:1", "row": -1}, "un entier, à partir de 0"),
        ({"set": "selection", "result": "result:1"}, "ne suit pas son schéma — ids"),
        ({"clear": "signature"}, "clé obligatoire, elle ne s'efface pas"),
        ({"clear": "client", "row": 0}, "ne prend rien d'autre"),
    ],
)
async def test_the_tool_refuses_with_what_to_fix(
    arguments: dict[str, JsonValue], said: str
) -> None:
    with pytest.raises(ToolError, match=said):
        await state_tool().invoke(arguments, context())


async def test_the_tool_refuses_a_sub_run_and_an_oversized_value() -> None:
    with pytest.raises(ToolError, match="ne tient pas le state de la session"):
        await state_tool().invoke({"clear": "client"}, context(keeps=False))
    with pytest.raises(ToolError, match=r"au-delà de sessions\.state\.max_value_bytes \(10\)"):
        await state_tool(10).invoke({"set": "client", "result": "result:1", "row": 0}, context())


def test_the_tool_needs_a_model_key() -> None:
    with pytest.raises(ValueError, match="aucune clé du state n'a 'model'"):
        StateTool((), 1024)


# --- Durcissement (relecture du 28/10, essais t2 à t7) ----------------------------------


def test_match_designates_the_row_by_its_values() -> None:
    """Un modèle compte mal les lignes ; une valeur lue ne désigne qu'une ligne qui la porte."""
    assert extract(SQL, CLIENT, match={"code": "C-002"}) == {"id": 2, "code": "C-002"}
    # Un nombre écrit en texte, des espaces : la même valeur.
    assert extract(SQL, CLIENT, match={"id": "261"}) == {"id": 261, "code": "C-261"}
    assert extract(SQL, CLIENT, match={"code": " C-261 "}) == {"id": 261, "code": "C-261"}
    homonyms: dict[str, JsonValue] = {**SQL, "rows": [[1, "C-1", "Renard"], [2, "C-2", "Renard"]]}
    with pytest.raises(ExtractError, match="2 lignes du résultat ont nom = 'Renard'"):
        extract(homonyms, CLIENT, match={"nom": "Renard"})
    assert extract(homonyms, CLIENT, match={"nom": "Renard", "code": "C-2"}) == {
        "id": 2,
        "code": "C-2",
    }
    with pytest.raises(ExtractError, match="aucune ligne du résultat n'a code = 'C-9'"):
        extract(SQL, CLIENT, match={"code": "C-9"})
    with pytest.raises(ExtractError, match="colonne 'ville' absente"):
        extract(SQL, CLIENT, match={"ville": "Dijon"})


def test_a_missing_column_says_to_query_it_without_alias() -> None:
    aliased: dict[str, JsonValue] = {**SQL, "columns": ["id", "code_client", "nom"]}
    with pytest.raises(
        ExtractError, match="refaire la requête avec la colonne 'code', sous ce nom"
    ):
        extract(aliased, CLIENT, row=0)


def test_an_empty_list_is_not_a_selection() -> None:
    with pytest.raises(ExtractError, match="aucune ligne : liste vide"):
        extract({**SQL, "rows": []}, SELECTION)


@pytest.mark.parametrize(
    "written", ["result:1", "1", 1, " result: 1", {"$ref": "result:1"}, '{"$ref": "result:1"}']
)
async def test_the_reference_may_be_written_in_any_usual_form(written: JsonValue) -> None:
    got = await state_tool().invoke(
        {"set": "client", "result": written, "match": {"code": "C-261"}}, context()
    )
    assert got.state == {"client": {"id": 261, "code": "C-261"}}
    assert got.data == {"set": "client", "value": {"id": 261, "code": "C-261"}, "from": "result:1"}


async def test_a_result_of_the_same_turn_says_to_wait_for_it() -> None:
    @dataclass
    class Pending:
        async def read(self, ref: str) -> JsonValue:
            raise ToolError(
                f"{ref} (sql) n'a pas encore de résultat : un appel du même tour ne peut pas "
                "être référencé."
            )

    early = ToolContext(
        tenant_id=TenantId("default"),
        session_id=SessionId("s"),
        run_id=RunId("r"),
        call_id="c1",
        agent="demo",
        results=Pending(),
        keeps_state=True,
    )
    with pytest.raises(ToolError, match="Appelle state au tour suivant"):
        await state_tool().invoke({"set": "client", "result": "result:1"}, early)


async def test_setting_a_key_clears_its_clears() -> None:
    tool = StateTool(
        (
            ModelKey(
                name="selection",
                description="Liste.",
                schema={"type": "object"},
                extraction=SELECTION,
                clears=("client",),
            ),
            ModelKey(
                name="client", description="Client.", schema={"type": "object"}, extraction=CLIENT
            ),
        ),
        1024,
    )
    shown = ToolContext(
        tenant_id=TenantId("default"),
        session_id=SessionId("s"),
        run_id=RunId("r"),
        call_id="c1",
        agent="demo",
        results=Results(SQL),
        keeps_state=True,
        state={"client": {"id": 261, "code": "C-261"}},
    )
    got = await tool.invoke({"set": "selection", "result": "result:1", "label": "x"}, shown)
    assert got.state == {"selection": {"ids": [261, 2], "libelle": "x"}, "client": None}
    assert got.data == {
        "set": "selection",
        "value": {"ids": [261, 2], "libelle": "x"},
        "from": "result:1",
        "cleared": ["client"],
    }


@pytest.mark.parametrize(
    ("clears", "said"),
    [
        (["selection"], "une clé ne s'efface pas en se posant"),
        (["inconnue"], "clé non déclarée"),
        (["signature"], "clé obligatoire, rien ne l'efface"),
        (["ville"], "'model' doit être dans ses writers"),
    ],
)
def test_clears_names_keys_the_model_may_erase(
    demo: ConfigFactory, crm: Path, clears: list[str], said: str
) -> None:
    from loom_ia.config import ConfigError

    state = {
        **MODEL_STATE,
        "selection": {**MODEL_STATE["selection"], "clears": clears},
        "signature": {"schema": TEXT, "writers": ["app"], "required": True},
        "ville": {"schema": TEXT},
    }
    with pytest.raises(ConfigError, match=said):
        load_config(
            demo(imports=IMPORTS, agents=[state_agent(crm, state=state)], models=model(SCRIPT))
        )


def test_clears_goes_with_extract() -> None:
    with pytest.raises(ValidationError, match="'clears' va avec 'extract'"):
        StateKey.model_validate({"schema": TEXT, "clears": ["client"]})


async def test_the_engine_hands_a_ref_to_loom_state_untouched(
    demo: ConfigFactory, crm: Path
) -> None:
    """``{"$ref": "result:1"}`` n'est pas remplacé par les données pour ``loom.state`` (B1)."""
    from loom_ia.access.api import Loom
    from loom_ia.core.model import SessionId as Sid

    script: list[dict[str, Any]] = [
        turn("Ref", ("sql", sql("SELECT id, code FROM clients ORDER BY id"))),
        turn(
            "Ref",
            (
                "state",
                {"set": "client", "result": {"$ref": "result:1"}, "match": {"code": "C-261"}},
            ),
        ),
        {"with_text": "Ref", "text": "Posé."},
    ]
    path = demo(imports=IMPORTS, agents=[state_agent(crm)], models=model(script))
    async with Loom.from_config(path) as loom:
        result = await loom.run("demo", "Ref", session_id=Sid("r1"))
        state = await loom.session_state(Sid("r1"))
        events = await loom.export_session(Sid("r1"))
    assert result.ok
    assert state == {"client": {"id": 261, "code": "C-261"}}
    called = [e.payload for e in events if isinstance(e.payload, ToolCalled)]
    assert called[-1].arguments["result"] == {"$ref": "result:1"}
    assert called[-1].refs == ()


async def test_an_answer_written_with_state_ends_the_run_and_replays(
    demo: ConfigFactory, crm: Path
) -> None:
    """M1 de bout en bout : la réponse écrite avec ``state`` est la sortie ; rejeu et variante."""
    from loom_ia.access.api import Loom
    from loom_ia.core.model import SessionId as Sid

    script: list[dict[str, Any]] = [
        turn("Réponds", ("sql", sql("SELECT id, code FROM clients WHERE code = 'C-261'"))),
        {
            "with_text": "Réponds",
            "text": "Le client est C-261.",
            "tool_calls": [{"name": "state", "arguments": {"set": "client", "result": "result:1"}}],
        },
    ]
    path = demo(imports=IMPORTS, agents=[state_agent(crm)], models=model(script))
    async with Loom.from_config(path) as loom:
        result = await loom.run("demo", "Réponds", session_id=Sid("m1"))
        state = await loom.session_state(Sid("m1"))
        reports = [
            await loom.replay(result.run_id, session_id=Sid("m1"), mode=mode)
            for mode in ("exact", "variant")
        ]
    assert result.ok and result.text == "Le client est C-261."
    assert state == {"client": {"id": 261, "code": "C-261"}}
    for report in reports:
        assert report.identical, (report.mode, report.divergence)
        assert report.model_calls == (2, 2)


def test_settings_for_validate() -> None:
    """``loom validate`` montre la règle de chaque clé, une ligne par clé."""
    assert state_tool().settings().rows == (
        ("client", "une ligne, colonnes id, code"),
        ("selection", "toutes les lignes, ids ← id ; libellé dans libelle ('label')"),
        ("signature", "une ligne, colonne code"),
    )
