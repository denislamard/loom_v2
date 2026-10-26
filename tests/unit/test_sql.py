# SPDX-License-Identifier: Apache-2.0
"""Outil fourni ``loom.sql`` : paramètres, analyse, SQLite, approbation, montage et run.

L'analyse est éprouvée seule (``analyze``), puis à travers l'outil sur une base
SQLite temporaire ; l'authorizer l'est aussi seul, comme si l'analyse avait
laissé passer l'instruction : c'est lui qui fait foi. Postgres a sa suite
d'intégration (``tests/integration/test_sql_postgres.py``) ; ici, ce qui se juge
sans serveur : le DSN, le contrôle du rôle, la conversion des paramètres.
"""

import asyncio
import sqlite3
import sys
from pathlib import Path
from typing import Any, Final

import pytest

pytest.importorskip("sqlglot", reason="extra 'sql' absent")

from conftest import MODEL, ConfigFactory, demo_agent
from pydantic import ValidationError

from loom_ia.access.cli import main
from loom_ia.adapters.stores import InMemoryEventStore
from loom_ia.config import ConfigError, load_config
from loom_ia.core.events import ToolCompleted
from loom_ia.core.model import DEFAULT_TENANT, RunId, RunStatus, SessionId
from loom_ia.core.ports import ToolContext, ToolError
from loom_ia.engine import begin_run, drive
from loom_ia.runtime import build_agent
from loom_ia.tools_builtin import BuiltinContext, Protected
from loom_ia.tools_builtin.sql import SqlTool, build
from loom_ia.tools_builtin.sql.analysis import FORBIDDEN, Catalog, analyze
from loom_ia.tools_builtin.sql.sqlite import (  # pyright: ignore[reportPrivateUsage]
    Objects,
    authorizer,
)
from loom_ia.tools_builtin.sql.values import cell, duration
from loom_ia.tools_builtin.sql_params import DatabaseSpec, SqlParams

CONTEXT: Final = ToolContext(
    tenant_id=DEFAULT_TENANT,
    session_id=SessionId("s"),
    run_id=RunId("r"),
    call_id="c1",
    agent="demo",
)
JSONL: Final = {"events": {"backend": "jsonl", "path": "journal"}}

SCHEMA: Final = """
CREATE TABLE clients (id INTEGER PRIMARY KEY, nom TEXT NOT NULL -- raison sociale
);
CREATE TABLE devis (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  client_id INTEGER NOT NULL REFERENCES clients(id),
  statut TEXT NOT NULL CHECK (statut IN ('brouillon', 'envoye', 'relance')),
  montant REAL,
  note TEXT
);
CREATE TABLE relances (id INTEGER PRIMARY KEY, devis_id INTEGER, canal TEXT);
CREATE TABLE audit (id INTEGER PRIMARY KEY, msg TEXT);
CREATE TRIGGER t_devis AFTER UPDATE ON devis BEGIN INSERT INTO audit (msg) VALUES ('maj'); END;
CREATE VIEW v_devis AS
  SELECT d.id, c.nom, d.montant FROM devis d JOIN clients c ON c.id = d.client_id;
CREATE VIEW v_audit AS SELECT * FROM audit;
INSERT INTO clients VALUES (1, 'Dupont'), (2, 'Martin');
INSERT INTO devis (client_id, statut, montant) VALUES (1, 'envoye', 1250.5), (2, 'envoye', 300);
"""

TABLES: Final = {
    "devis": ["select", "insert", "update"],
    "clients": ["select"],
    "relances": {"select": "never", "insert": "never", "delete": "never"},
    "v_devis": ["select"],
}


@pytest.fixture
def base(tmp_path: Path) -> Path:
    path = tmp_path / "crm.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA)
    conn.close()
    return path


def make(tmp_path: Path, *databases: dict[str, Any], **params: Any) -> SqlTool:
    model = SqlParams.model_validate({"databases": list(databases), **params})
    return build(model, BuiltinContext(base_dir=tmp_path))


def crm(base: Path, **changes: Any) -> dict[str, Any]:
    return {"name": "crm", "sqlite": {"path": str(base)}, "tables": TABLES, **changes}


@pytest.fixture
def tool(tmp_path: Path, base: Path) -> SqlTool:
    return make(tmp_path, crm(base))


async def call(tool: SqlTool, **arguments: Any) -> dict[str, Any]:
    output = await tool.invoke(dict(arguments), CONTEXT)
    assert isinstance(output.data, dict)
    return output.data


async def refused(tool: SqlTool, match: str, **arguments: Any) -> None:
    with pytest.raises(ToolError, match=match):
        await tool.invoke(dict(arguments), CONTEXT)


def query(sql: str, *params: Any) -> dict[str, Any]:
    return {"action": "query", "database": "crm", "sql": sql, "params": list(params)}


def execute(*statements: tuple[str, list[Any]] | str) -> dict[str, Any]:
    units = [
        {"sql": s} if isinstance(s, str) else {"sql": s[0], "params": s[1]} for s in statements
    ]
    return {"action": "execute", "database": "crm", "statements": units}


def rows(base: Path, sql: str) -> list[tuple[Any, ...]]:
    with sqlite3.connect(base) as conn:
        found = conn.execute(sql).fetchall()
    conn.close()
    return found


# --- Paramètres -----------------------------------------------------------------------


def database(**changes: Any) -> dict[str, Any]:
    return {"name": "crm", "sqlite": {"path": "crm.db"}, "tables": {"devis": ["select"]}, **changes}


def test_short_form_takes_the_default_approvals() -> None:
    spec = DatabaseSpec.model_validate(
        database(tables={"devis": ["select", "insert", "update", "delete"]})
    )
    assert spec.tables["devis"] == {
        "select": "never",
        "insert": "never",
        "update": "always",
        "delete": "always",
    }


@pytest.mark.parametrize(
    ("changes", "said"),
    [
        ({"tables": {"devis": ["update"]}}, "update exige 'select'"),
        ({"tables": {"devis": {"delete": "never"}}}, "delete exige 'select'"),
        ({"tables": {}}, "'tables' ne peut pas être vide"),
        ({"tables": {"devis": []}}, "aucun droit donné"),
        ({"tables": {"devis": ["select", "select"]}}, "droit donné deux fois : select"),
        ({"tables": {"devis": ["drop"]}}, "Input should be 'select'"),
        ({"tables": {"devis; DROP": ["select"]}}, "String should match pattern"),
        ({"tables": {"Devis": ["select"], "devis": ["select"]}}, "ne diffèrent que par la casse"),
        ({"postgres": {"dsn_env": "X"}}, "donner 'postgres' ou 'sqlite', et un seul"),
        ({"sqlite": None}, "donner 'postgres' ou 'sqlite', et un seul"),
        ({"sqlite": {"path": "~/crm.db"}}, "ni « ~ » ni variable"),
        ({"describe": {"audit": "x"}}, "table 'audit' non déclarée"),
        ({"functions": ["age", "AGE"]}, "fonction donnée deux fois"),
        ({"max_rows": 5000}, "less than or equal to 1000"),
        ({"statement_timeout": 0}, "greater than 0"),
    ],
)
def test_database_faults(changes: dict[str, Any], said: str) -> None:
    with pytest.raises(ValidationError, match=said):
        DatabaseSpec.model_validate(database(**changes))


def test_params_faults() -> None:
    with pytest.raises(ValidationError, match="au moins une base"):
        SqlParams.model_validate({"databases": []})
    with pytest.raises(ValidationError, match="nom de base donné deux fois : crm"):
        SqlParams.model_validate({"databases": [database(), database()]})
    with pytest.raises(ValidationError, match="fuseau inconnu"):
        SqlParams.model_validate({"databases": [database()], "timezone": "Paris"})


def test_postgres_schema_and_secret_names() -> None:
    pg = {
        "name": "pg",
        "postgres": {"dsn_env": "CRM_DSN", "schema": "ventes"},
        "tables": {"t": ["select"]},
    }
    params = SqlParams.model_validate({"databases": [database(), pg, {**pg, "name": "pg2"}]})
    assert params.databases[1].postgres is not None
    assert params.databases[1].postgres.schema_name == "ventes"
    assert params.secret_names() == ("CRM_DSN",)


# --- Montage --------------------------------------------------------------------------


def test_the_sqlite_file_must_exist(tmp_path: Path) -> None:
    with pytest.raises(
        ValueError, match=r"n'existe pas ou n'est pas un fichier — loom\.sql n'en crée pas"
    ):
        make(tmp_path, database())


def test_a_relative_path_is_from_the_config(tmp_path: Path, base: Path) -> None:
    tool = make(tmp_path, database(sqlite={"path": base.name}))
    assert tool.settings_lines()[0].startswith(f"crm → sqlite, {base} : devis select")


def test_loom_own_files_are_refused(tmp_path: Path, base: Path) -> None:
    model = SqlParams.model_validate({"databases": [crm(base)]})
    journal = BuiltinContext(protected=(Protected(base, "le journal"),))
    with pytest.raises(ValueError, match="est le journal"):
        build(model, journal)
    artefacts = BuiltinContext(
        protected=(Protected(base.parent, "le dossier des artefacts", subtree=True),)
    )
    with pytest.raises(ValueError, match="est dans le dossier des artefacts"):
        build(model, artefacts)


def postgres(**changes: Any) -> dict[str, Any]:
    return {
        "name": "pg",
        "postgres": {"dsn_env": "CRM_DSN"},
        "tables": {"devis": ["select"]},
        **changes,
    }


def test_the_dsn_comes_from_the_secrets_and_is_never_shown() -> None:
    model = SqlParams.model_validate({"databases": [postgres()]})
    secret = "postgresql://agent:motdepasse@db/crm"
    with pytest.raises(ValueError, match="la variable CRM_DSN \\(dsn_env\\) est absente ou vide"):
        build(model, BuiltinContext(secrets={}))
    tool = build(model, BuiltinContext(secrets={"CRM_DSN": secret}))
    shown = "\n".join([tool.spec.description, *tool.settings_lines()])
    assert "motdepasse" not in shown
    assert "pg → postgres, CRM_DSN, schéma public : devis select" in shown


def test_the_storage_dsn_is_refused() -> None:
    model = SqlParams.model_validate({"databases": [postgres()]})
    with pytest.raises(ValueError, match="CRM_DSN est le DSN du stockage de loom"):
        build(model, BuiltinContext(secrets={"CRM_DSN": "x"}, protected_dsns=("CRM_DSN",)))
    same = {"CRM_DSN": "postgresql://loom@db/loom", "LOOM_DSN": "postgresql://loom@db/loom"}
    with pytest.raises(ValueError, match="porte le même DSN que LOOM_DSN"):
        build(model, BuiltinContext(secrets=same, protected_dsns=("LOOM_DSN",)))


def test_declarations_are_the_worst_case(tmp_path: Path, base: Path) -> None:
    def spec(tables: dict[str, Any]) -> tuple[str, str, str]:
        found = make(tmp_path, crm(base, tables=tables)).spec
        return (found.side_effects, found.approval, found.on_unknown)

    assert spec({"devis": ["select"]}) == ("none", "never", "error")
    assert spec({"devis": ["select", "insert"]}) == ("reversible", "never", "pause")
    assert spec({"devis": {"select": "always"}}) == ("none", "always", "error")
    assert spec({"devis": ["select", "update"]}) == ("irreversible", "always", "pause")
    assert spec({"devis": {"select": "never", "delete": "never"}}) == (
        "irreversible",
        "never",
        "pause",
    )


def test_description_and_settings(tool: SqlTool) -> None:
    description = tool.spec.description
    assert (
        "- crm (sqlite ; 200 lignes lues, 100 modifiées au plus) : "
        "devis (select, insert, update (approbation))" in description
    )
    assert "relances (select, insert, delete)" in description
    assert "paramètres $1, $2…" in description
    assert "en heure de Europe/Paris" in description
    assert "title" not in str(tool.spec.input_schema)
    assert tool.settings_lines()[1:] == (
        "crm : 200 lignes lues, 100 modifiées au plus ; 10 s par instruction",
        "fuseau : Europe/Paris",
    )


# --- Analyse --------------------------------------------------------------------------

SQLITE: Final = Catalog.of(DatabaseSpec.model_validate(crm(Path("x"), functions=["julianday_x"])))
PG: Final = Catalog.of(
    DatabaseSpec.model_validate(
        postgres(
            tables={
                "devis": ["select", "insert", "update", "delete"],
                "clients": ["select"],
                "Factures": ["select"],
                "relances": ["select", "insert"],
            },
            functions=["age"],
        )
    )
)


@pytest.mark.parametrize(
    ("sql", "write", "said", "params"),
    [
        ("CREATE TABLE x (a int)", True, "Instruction CREATE interdite", 0),
        ("DROP TABLE devis", True, "Instruction DROP interdite", 0),
        ("ALTER TABLE devis ADD COLUMN x int", True, "Instruction ALTER", 0),
        ("TRUNCATE devis", True, "Instruction TRUNCATE interdite", 0),
        (
            "SELECT 1; DROP TABLE devis",
            False,
            "Une seule instruction par entrée \\(2 données\\)",
            0,
        ),
        ("LOCK TABLE devis IN ACCESS EXCLUSIVE MODE", True, "Instruction LOCK interdite", 0),
        ("CALL purge()", True, "Instruction CALL interdite", 0),
        ("SET statement_timeout = 0", False, "Instruction SET interdite", 0),
        ("COPY devis TO PROGRAM 'rm -rf /'", True, "Instruction COPY interdite", 0),
        ("EXPLAIN ANALYZE DELETE FROM devis", True, "Instruction EXPLAIN interdite", 0),
        ("BEGIN", True, "Instruction BEGIN interdite", 0),
        (
            "MERGE INTO devis USING clients ON true WHEN MATCHED THEN DELETE",
            True,
            "Instruction MERGE",
            0,
        ),
        ("SELECT * INTO copie FROM devis", False, "SELECT … INTO crée une table", 0),
        ("SELECT * FROM devis FOR UPDATE", False, "FOR UPDATE / FOR SHARE", 0),
        (
            "WITH d AS (DELETE FROM devis WHERE id = 1 RETURNING *) SELECT * FROM d",
            False,
            "query ne fait que lire",
            0,
        ),
        (
            "WITH d AS (DELETE FROM devis WHERE id = 1 RETURNING id) "
            "INSERT INTO relances (id) SELECT id FROM d",
            True,
            "n'écrit que dans sa table cible",
            0,
        ),
        (
            "UPDATE devis SET statut = 'x' WHERE id = 1",
            False,
            "UPDATE écrit : passer par execute",
            1,
        ),
        ("SELECT * FROM devis", True, "une lecture passe par query", 0),
        ("SELECT * FROM public.devis", False, "Nom qualifié « public.devis » interdit", 0),
        ("UPDATE public.devis SET statut = 'x' WHERE id = 1", True, "Nom qualifié", 0),
        (
            "SELECT * FROM audit",
            False,
            "Table « audit » non autorisée. Tables de la base : devis, clients, Factures, relances",
            0,
        ),
        ("SELECT count(*) FROM audit", False, "Table « audit » non autorisée", 0),
        (
            "WITH audit AS (SELECT 1) SELECT * FROM audit a JOIN pg_catalog.pg_authid p ON true",
            False,
            "Nom qualifié",
            0,
        ),
        ("SELECT * FROM factures", False, "Table « factures » non autorisée", 0),
        (
            "DELETE FROM clients WHERE id = 1",
            True,
            "DELETE non autorisé sur « clients » \\(droits : select\\)",
            0,
        ),
        (
            "INSERT INTO relances (id) VALUES (1) ON CONFLICT (id) DO UPDATE SET canal = 'x'",
            True,
            "UPDATE non autorisé sur « relances »",
            0,
        ),
        ("UPDATE devis SET statut = 'x'", True, "UPDATE sans WHERE refusé", 0),
        ("DELETE FROM devis", True, "DELETE sans WHERE refusé", 0),
        ("SELECT pg_sleep(10)", False, "Fonction pg_sleep interdite", 0),
        (
            "SELECT set_config('search_path', 'public', false)",
            False,
            "Fonction set_config interdite",
            0,
        ),
        ("SELECT pg_advisory_lock(1)", False, "Fonction pg_advisory_lock interdite", 0),
        ("SELECT dblink_exec('x', 'DROP TABLE devis')", False, "Fonction dblink_exec interdite", 0),
        (
            "SELECT query_to_xml('DELETE FROM devis', true, false, '')",
            False,
            "Fonction query_to_xml interdite",
            0,
        ),
        (
            "SELECT clock_timestamp()",
            False,
            "Fonction clock_timestamp non permise : inconnue de l'analyse",
            0,
        ),
        ("SELECT public.age(now())", False, "Fonction qualifiée", 0),
        ("SELECT * FROM devis WHERE id = ?", False, "Paramètres à écrire \\$1, \\$2…", 1),
        ("SELECT * FROM devis WHERE id = :id", False, "Paramètres à écrire", 1),
        ("SELECT * FROM devis WHERE id = $2", False, "\\$2 cité, mais 1 paramètre", 1),
        ("SELECT * FROM devis WHERE id = 1", False, "Paramètre \\$1 donné mais jamais cité", 1),
        ("SELEC * FROM devis", False, "SQL illisible|Instruction", 0),
    ],
)
def test_analysis_refuses(sql: str, write: bool, said: str, params: int) -> None:
    with pytest.raises(ToolError, match=said):
        analyze(sql, params, PG, write=write)


def test_a_forbidden_function_stays_refused_even_when_allowed() -> None:
    """Toutes celles de FORBIDDEN, même déclarées : sqlglot ne doit pas en typer une."""
    for name in sorted(FORBIDDEN):
        catalog = Catalog(
            dialect="postgres",
            rights={"t": {"select": "never"}},
            functions=frozenset({name}),
            allow_full_table=False,
        )
        with pytest.raises(ToolError, match=f"Fonction {name} interdite"):
            analyze(f"SELECT {name}('x') FROM t", 0, catalog, write=False)


@pytest.mark.parametrize(
    ("sql", "params", "write", "needs"),
    [
        (
            "SELECT d.id, c.nom FROM devis d JOIN clients c ON c.id = d.client_id WHERE d.id = $1",
            1,
            False,
            {"devis": {"select"}, "clients": {"select"}},
        ),
        (
            "WITH recents AS (SELECT * FROM devis) SELECT * FROM recents",
            0,
            False,
            {"devis": {"select"}},
        ),
        ('SELECT * FROM "Factures"', 0, False, {"Factures": {"select"}}),
        ("SELECT * FROM DEVIS", 0, False, {"devis": {"select"}}),
        (
            "SELECT age(now()), date_trunc('month', now()), string_agg(nom, ',') FROM clients",
            0,
            False,
            {"clients": {"select"}},
        ),
        ("SELECT * FROM generate_series(1, 3) g", 0, False, {}),
        (
            "INSERT INTO relances (devis_id, canal) VALUES ($1,$2)",
            2,
            True,
            {"relances": {"insert"}},
        ),
        (
            "INSERT INTO relances (devis_id) SELECT id FROM devis WHERE statut = $1 RETURNING id",
            1,
            True,
            {"relances": {"insert", "select"}, "devis": {"select"}},
        ),
        (
            "INSERT INTO devis (id) VALUES (1) ON CONFLICT (id) DO UPDATE SET statut = 'x'",
            0,
            True,
            {"devis": {"insert", "update", "select"}},
        ),
        (
            "INSERT INTO relances (id) VALUES (1) ON CONFLICT DO NOTHING",
            0,
            True,
            {"relances": {"insert"}},
        ),
        (
            "UPDATE devis d SET statut = 'relance' FROM clients c "
            "WHERE c.id = d.client_id AND c.nom = $1",
            1,
            True,
            {"devis": {"update", "select"}, "clients": {"select"}},
        ),
        (
            "DELETE FROM devis USING clients "
            "WHERE clients.id = devis.client_id AND clients.nom = $1",
            1,
            True,
            {"devis": {"delete", "select"}, "clients": {"select"}},
        ),
    ],
)
def test_analysis_finds_what_each_table_needs(
    sql: str, params: int, write: bool, needs: dict[str, set[str]]
) -> None:
    found = analyze(sql, params, PG, write=write)
    assert {table: set(rights) for table, rights in found.needs.items()} == needs


def test_sqlite_names_ignore_case_and_dollar_params_parse() -> None:
    found = analyze("SELECT * FROM DEVIS WHERE id = $1 AND statut = $2", 2, SQLITE, write=False)
    assert set(found.needs) == {"devis"}
    found = analyze("SELECT julianday_x(1) FROM devis", 0, SQLITE, write=False)
    assert set(found.needs) == {"devis"}


def test_full_table_writes_can_be_allowed() -> None:
    catalog = Catalog(
        dialect="postgres",
        rights={"t": {"select": "never", "delete": "never"}},
        functions=frozenset(),
        allow_full_table=True,
    )
    assert analyze("DELETE FROM t", 0, catalog, write=True).operation == "delete"


# --- SQLite : lire ---------------------------------------------------------------------


async def test_query_returns_columns_and_rows(tool: SqlTool) -> None:
    result = await call(
        tool, **query("SELECT id, montant FROM devis WHERE statut = $1 ORDER BY id", "envoye")
    )
    assert result == {
        "database": "crm",
        "columns": ["id", "montant"],
        "rows": [[1, 1250.5], [2, 300.0]],
        "row_count": 2,
        "truncated": False,
    }


async def test_query_is_truncated_at_max_rows(tmp_path: Path, base: Path) -> None:
    tool = make(tmp_path, crm(base, max_rows=1))
    result = await call(tool, **query("SELECT id FROM devis ORDER BY id"))
    assert (result["rows"], result["truncated"]) == ([[1]], True)


async def test_a_declared_view_can_be_read(tool: SqlTool) -> None:
    result = await call(tool, **query("SELECT nom FROM v_devis ORDER BY id"))
    assert result["rows"] == [["Dupont"], ["Martin"]]


async def test_query_refusals_reach_the_model(tool: SqlTool) -> None:
    await refused(tool, "Table « audit » non autorisée", **query("SELECT * FROM audit"))
    await refused(
        tool,
        "Base « autre » inconnue. Bases permises : crm",
        action="query",
        database="autre",
        sql="SELECT 1",
    )
    await refused(
        tool,
        "statements ne sert pas à query",
        action="query",
        database="crm",
        sql="SELECT 1",
        statements=[{"sql": "x"}],
    )
    await refused(
        tool,
        "Colonne « montan » inconnue — colonnes proches dans devis : montant",
        **query("SELECT montan FROM devis"),
    )


async def test_a_long_query_is_stopped(tmp_path: Path, base: Path) -> None:
    tool = make(tmp_path, crm(base, statement_timeout=0.3))
    loop = "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r) SELECT count(*) FROM r"
    started = asyncio.get_running_loop().time()
    await refused(tool, "Instruction arrêtée après 0.3 s", **query(loop))
    assert asyncio.get_running_loop().time() - started < 3


async def test_a_cancelled_call_interrupts_sqlite(tool: SqlTool, base: Path) -> None:
    """Le délai de l'outil (wait_for) interrompt l'instruction : la base n'est pas tenue."""
    loop = "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r) SELECT count(*) FROM r"
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(tool.invoke(query(loop), CONTEXT), 0.2)
    await asyncio.sleep(0.2)
    with sqlite3.connect(base, timeout=0.5) as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("ROLLBACK")
    conn.close()


async def test_a_wal_database_is_read_in_read_only_mode(tool: SqlTool, base: Path) -> None:
    with sqlite3.connect(base) as conn:
        assert conn.execute("PRAGMA journal_mode = WAL").fetchone() == ("wal",)
    conn.close()
    result = await call(tool, **query("SELECT count(*) AS n FROM devis"))
    assert result["rows"] == [[2]]


# --- SQLite : écrire -------------------------------------------------------------------


async def test_execute_applies_all_statements_together(tool: SqlTool, base: Path) -> None:
    result = await call(
        tool,
        **execute(
            ("UPDATE devis SET statut = $1 WHERE id = $2", ["relance", 1]),
            ("INSERT INTO relances (devis_id, canal) VALUES ($1,$2) RETURNING id", [1, "email"]),
        ),
    )
    assert result == {
        "database": "crm",
        "results": [
            {"affected_rows": 1},
            {"affected_rows": 1, "columns": ["id"], "returning": [[1]]},
        ],
        "affected_rows": 2,
        "committed": True,
    }
    assert rows(base, "SELECT statut FROM devis WHERE id = 1") == [("relance",)]
    # Le trigger du propriétaire de la base a écrit dans une table non déclarée.
    assert rows(base, "SELECT msg FROM audit") == [("maj",)]


async def test_a_failing_statement_rolls_everything_back(tool: SqlTool, base: Path) -> None:
    await refused(
        tool,
        "Instruction 2 : Erreur SQLite : CHECK constraint failed.*Rien n'est enregistré",
        **execute(
            ("UPDATE devis SET statut = $1 WHERE id = $2", ["relance", 1]),
            ("UPDATE devis SET statut = $1 WHERE id = $2", ["perdu", 2]),
        ),
    )
    assert rows(base, "SELECT statut FROM devis ORDER BY id") == [("envoye",), ("envoye",)]
    assert rows(base, "SELECT count(*) FROM audit") == [(0,)]


async def test_too_many_affected_rows_commit_nothing(tmp_path: Path, base: Path) -> None:
    tool = make(tmp_path, crm(base, max_affected_rows=1))
    await refused(
        tool,
        "2 lignes modifiées, au-delà de 1 : rien n'est enregistré",
        **execute("UPDATE devis SET note = 'x' WHERE id > 0"),
    )
    assert rows(base, "SELECT count(*) FROM devis WHERE note IS NOT NULL") == [(0,)]


async def test_execute_refusals(tool: SqlTool) -> None:
    await refused(
        tool,
        "Instruction 2 : DELETE non autorisé sur « devis »",
        **execute("UPDATE devis SET note = 'a' WHERE id = 1", "DELETE FROM devis WHERE id = 1"),
    )
    await refused(
        tool,
        "statements.*at most 20",
        **execute(*["UPDATE devis SET note = 'a' WHERE id = 1"] * 21),
    )
    await refused(tool, "execute attend 'statements'", action="execute", database="crm")


async def test_parameters_are_bound_by_number(tool: SqlTool, base: Path) -> None:
    await call(
        tool, **execute(("INSERT INTO relances (devis_id, canal) VALUES ($2, $1)", ["sms", 2]))
    )
    await call(
        tool,
        **execute(("INSERT INTO relances (devis_id, canal) VALUES ($1, $2)", [True, {"a": 1}])),
    )
    assert rows(base, "SELECT devis_id, canal FROM relances ORDER BY id") == [
        (2, "sms"),
        (1, '{"a": 1}'),
    ]


# --- L'authorizer, seul : ce qui passerait l'analyse ne passe pas la base ---------------


def raw(base: Path, sql: str, *, write: bool, ctes: frozenset[str] = frozenset()) -> list[Any]:
    """Exécute ``sql`` sous l'authorizer de l'outil, sans l'analyse devant."""
    conn = sqlite3.connect(base, autocommit=True)
    try:
        listed = conn.execute(
            "SELECT type, lower(name) FROM sqlite_schema WHERE type IN ('view', 'trigger')"
        ).fetchall()
        objects = Objects(
            views=frozenset(n for k, n in listed if k == "view"),
            triggers=frozenset(n for k, n in listed if k == "trigger"),
        )
        conn.set_authorizer(authorizer(SQLITE, objects, ctes, write=write))
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM audit",
        "SELECT count(*) FROM audit",
        "SELECT * FROM v_audit",
        "SELECT count(*) FROM v_audit",
        "WITH x AS (SELECT msg FROM audit) SELECT * FROM x",
        "DELETE FROM devis WHERE id = 1",
        "INSERT INTO clients (nom) VALUES ('x')",
        "CREATE TABLE x (a)",
        "DROP TABLE audit",
        "ATTACH DATABASE ':memory:' AS autre",
        "PRAGMA writable_schema = 1",
        "SELECT load_extension('x')",
        "SELECT * FROM sqlite_schema",
    ],
)
def test_the_authorizer_refuses_what_the_config_does_not_grant(base: Path, sql: str) -> None:
    with pytest.raises(sqlite3.DatabaseError, match=r"not authorized|prohibited"):
        raw(base, sql, write=True, ctes=frozenset({"x"}))


def test_the_authorizer_lets_through_what_the_config_grants(base: Path) -> None:
    assert raw(base, "SELECT count(*) FROM devis", write=False) == [(2,)]
    assert raw(
        base,
        "WITH r AS (SELECT id FROM devis) SELECT count(*) FROM r",
        write=False,
        ctes=frozenset({"r"}),
    ) == [(2,)]
    assert raw(base, "SELECT count(*) FROM v_devis", write=False) == [(2,)]
    # Une vue permise sur une table qui ne l'est pas : la vue se lit, la table non.
    only_view = Catalog(
        dialect="sqlite",
        rights={"v_audit": {"select": "never"}},
        functions=frozenset(),
        allow_full_table=False,
    )
    conn = sqlite3.connect(base, autocommit=True)
    try:
        objects = Objects(views=frozenset({"v_audit", "v_devis"}), triggers=frozenset())
        conn.set_authorizer(authorizer(only_view, objects, frozenset(), write=False))
        assert conn.execute("SELECT count(*) FROM v_audit").fetchall() == [(0,)]
        assert conn.execute("SELECT * FROM v_audit").fetchall() == []
        with pytest.raises(sqlite3.DatabaseError, match=r"not authorized|prohibited"):
            conn.execute("SELECT count(*) FROM audit")
    finally:
        conn.close()
    with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
        raw(base, "UPDATE devis SET note = 'x' WHERE id = 1", write=False)
    raw(base, "UPDATE devis SET note = 'x' WHERE id = 1", write=True)
    assert rows(base, "SELECT count(*) FROM audit") == [(1,)]


# --- Approbation ------------------------------------------------------------------------


def test_assess_follows_the_rights_exercised(tmp_path: Path, base: Path) -> None:
    tables = {
        "devis": {"select": "never", "update": "always"},
        "clients": {"select": "always"},
        "relances": ["select", "insert"],
    }
    tool = make(tmp_path, crm(base, tables=tables))
    plain = tool.assess_call(query("SELECT * FROM devis"))
    assert (plain.side_effects, plain.approval) == ("none", "never")
    asked = tool.assess_call(query("SELECT nom FROM clients"))
    assert (asked.approval, asked.reason) == (
        "always",
        "crm : lecture — approbation demandée pour select sur clients",
    )
    insert = tool.assess_call(execute(("INSERT INTO relances (devis_id) VALUES ($1)", [1])))
    assert (insert.side_effects, insert.approval, insert.idempotent) == (
        "reversible",
        "never",
        False,
    )
    update = tool.assess_call(
        execute(
            ("INSERT INTO relances (devis_id) VALUES ($1)", [1]),
            "UPDATE devis SET note = 'x' WHERE id = 1",
        )
    )
    assert (update.side_effects, update.approval) == ("irreversible", "always")
    assert (
        update.reason
        == "crm : insert sur relances, update sur devis — approbation demandée pour update "
        "sur devis"
    )
    invalid = tool.assess_call(execute("DROP TABLE devis"))
    assert (invalid.side_effects, invalid.approval) == ("none", "never")
    assert tool.assess_call(query("SELECT * FROM devis WHERE id = $1")).side_effects == "none"


# --- Schéma -------------------------------------------------------------------------------


async def test_schema_shows_the_declared_tables_with_their_rights(
    tmp_path: Path, base: Path
) -> None:
    tool = make(
        tmp_path,
        crm(
            base,
            describe={"devis": "Devis envoyés", "devis.statut": "brouillon | envoye | relance"},
        ),
    )
    output = await tool.invoke({"action": "schema", "database": "crm"}, CONTEXT)
    text = output.as_text
    assert text.startswith("-- Base « crm » (sqlite) : tables permises et leurs droits.")
    assert (
        "-- droits : SELECT, INSERT, UPDATE (approbation humaine)\n-- Devis envoyés\n"
        "-- devis.statut : brouillon | envoye | relance\nCREATE TABLE devis (" in text
    )
    assert "nom TEXT NOT NULL -- raison sociale" in text
    assert "audit" not in text.replace("INSERT INTO audit", "")
    one = await tool.invoke(
        {"action": "schema", "database": "crm", "tables": ["clients", "audit"]}, CONTEXT
    )
    said = one.as_text
    assert "CREATE TABLE clients" in said and "CREATE TABLE devis" not in said
    assert "-- « audit » : table non autorisée" in said


# --- Valeurs ------------------------------------------------------------------------------


def test_values_rendered_to_the_model() -> None:
    from datetime import UTC, date, datetime, timedelta
    from decimal import Decimal
    from uuid import UUID
    from zoneinfo import ZoneInfo

    paris = ZoneInfo("Europe/Paris")
    assert cell(Decimal("1250.50"), paris) == "1250.50"
    assert cell(datetime(2026, 10, 1, 7, 30, tzinfo=UTC), paris) == "2026-10-01T09:30:00+02:00"
    assert cell(datetime(2026, 10, 1, 7, 30), paris) == "2026-10-01T07:30:00"
    assert cell(date(2026, 10, 1), paris) == "2026-10-01"
    assert cell(b"\x00" * 12, paris) == "<bytes: 12>"
    assert cell(UUID(int=1), paris) == "00000000-0000-0000-0000-000000000001"
    assert cell(float("nan"), paris) == "nan"
    assert cell("x" * 1005, paris) == "x" * 1000 + "…[+5 caractères]"
    assert cell({"a": [Decimal(1)]}, paris) == {"a": ["1"]}
    assert duration(timedelta(days=1, hours=2, minutes=30)) == "P1DT2H30M"
    assert duration(timedelta(seconds=0.5)) == "PT0.5S"
    assert duration(timedelta(days=-1)) == "-P1D"


# --- Montage par la config, run, validate, secrets des clients ----------------------------


def sql_agent(**reference: Any) -> dict[str, Any]:
    return demo_agent(tools=[{"python": "loom.sql", **reference}])


def test_mounted_from_the_config(demo: ConfigFactory, base: Path) -> None:
    path = demo(
        agents=[sql_agent(timeout=20, databases=[crm(base, tables={"devis": ["select"]})])],
        storage=JSONL,
    )
    agent = build_agent(load_config(path), "demo", InMemoryEventStore())
    (spec,) = agent.context.tools.specs
    assert (spec.name, spec.timeout, spec.approval, spec.side_effects) == (
        "sql",
        20,
        "never",
        "none",
    )


def test_common_settings_it_refuses(demo: ConfigFactory, base: Path) -> None:
    path = demo(agents=[sql_agent(idempotent=True, databases=[crm(base)])], storage=JSONL)
    with pytest.raises(
        ConfigError, match="idempotent ne se règle pas sur cet outil — les droits de chaque table"
    ):
        load_config(path)


def test_a_missing_extra_is_said_at_mount(
    demo: ConfigFactory, base: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = "loom_ia.tools_builtin.sql"
    for name in [n for n in sys.modules if n == package or n.startswith(f"{package}.")]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.setitem(sys.modules, "sqlglot", None)
    path = demo(agents=[sql_agent(databases=[crm(base)])], storage=JSONL)
    with pytest.raises(
        ConfigError, match=r"dépendance 'sqlglot' absente \(installer l'extra : loom-ia\[sql\]\)"
    ):
        build_agent(load_config(path), "demo", InMemoryEventStore())


def test_a_sqlite_only_agent_does_not_import_asyncpg(demo: ConfigFactory, base: Path) -> None:
    import subprocess

    code = (
        "import sys\n"
        "from loom_ia.tools_builtin import builtin_tool, builtin_params, BuiltinContext\n"
        "p = builtin_params('loom.sql').model_validate({'databases': [{'name': 'c', "
        f"'sqlite': {{'path': {str(base)!r}}}, 'tables': {{'devis': ['select']}}}}]}})\n"
        "builtin_tool('loom.sql', p, context=BuiltinContext())\n"
        "print('sqlglot' in sys.modules, 'asyncpg' in sys.modules)\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, timeout=60
    )
    assert done.stdout.split() == ["True", "False"]


async def test_a_run_reads_and_writes(demo: ConfigFactory, base: Path) -> None:
    script: list[dict[str, Any]] = [
        {
            "text": "Je regarde.",
            "tool_calls": [
                {
                    "name": "sql",
                    "arguments": query("SELECT id FROM devis WHERE statut = $1", "envoye"),
                }
            ],
        },
        {
            "text": "Je note la relance.",
            "tool_calls": [
                {
                    "name": "sql",
                    "arguments": execute(
                        ("INSERT INTO relances (devis_id, canal) VALUES ($1, $2)", [1, "email"])
                    ),
                }
            ],
        },
        {"text": "Relance notée."},
    ]
    path = demo(
        agents=[sql_agent(databases=[crm(base)])],
        models=[{**MODEL, "params": {"script": script}}],
        storage=JSONL,
    )
    store = InMemoryEventStore()
    agent = build_agent(load_config(path), "demo", store)
    try:
        run = await begin_run(agent.context, "Relance le devis envoyé.")
        state = await drive(agent.context, run.run_id)
    finally:
        await agent.aclose()
    assert state.status is RunStatus.COMPLETED
    events = await store.read(DEFAULT_TENANT, run.session_id)
    first = next(e.payload for e in events if isinstance(e.payload, ToolCompleted))
    assert isinstance(first.output.data, dict)
    assert first.output.data["rows"] == [[1], [2]]
    assert rows(base, "SELECT devis_id, canal FROM relances") == [(1, "email")]


async def test_an_agent_closes_its_sql_tool(
    demo: ConfigFactory, base: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    closed: list[str] = []

    async def aclose(self: SqlTool) -> None:
        closed.append("sql")

    monkeypatch.setattr(SqlTool, "aclose", aclose)
    path = demo(agents=[sql_agent(timeout=5, databases=[crm(base)])], storage=JSONL)
    agent = build_agent(load_config(path), "demo", InMemoryEventStore())
    await agent.aclose()
    assert closed == ["sql"]


def test_validate_shows_the_databases(
    demo: ConfigFactory, base: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = demo(agents=[sql_agent(databases=[crm(base)])], storage=JSONL)
    assert main(["--config", str(path), "validate"]) == 0
    out = capsys.readouterr().out
    assert "Fournis    : loom.sql\n" in out
    assert (
        f"    outil sql : crm → sqlite, {base} : clients select ; devis select, insert, update "
        "(approbation) ; " in out
    )
    assert "    outil sql : fuseau : Europe/Paris\n" in out


def test_schema_is_built_from_catalog_rows_when_the_base_keeps_no_ddl() -> None:
    """Le rendu des tables lues dans un catalogue (Postgres), sans DDL d'origine."""
    from loom_ia.tools_builtin.sql.schema import Column, TableInfo, close_columns, render

    devis = TableInfo(
        name="devis",
        kind="table",
        comment="Devis envoyés",
        columns=(
            Column(name="id", type="integer", not_null=True, default="nextval('s')"),
            Column(name="montant_ht", type="numeric(10,2)", comment="en euros HT"),
        ),
        constraints=("PRIMARY KEY (id)",),
    )
    vue = TableInfo(name="v", kind="view", columns=(Column(name="id", type="integer"),))
    text = render(
        [devis, vue],
        {"devis": {"select": "never"}, "v": {"select": "never"}, "absente": {"select": "never"}},
        {},
        database="crm",
        dialect="postgres",
    )
    assert (
        "CREATE TABLE devis (  -- Devis envoyés\n"
        "  id integer NOT NULL DEFAULT nextval('s'),\n"
        "  montant_ht numeric(10,2),  -- en euros HT\n"
        "  PRIMARY KEY (id)\n);"
    ) in text
    assert "CREATE VIEW v (\n  id integer\n);" in text
    assert "-- « absente » est déclarée, mais absente de la base." in text
    hint = close_columns('column "montant" does not exist', [devis], ["devis"])
    assert hint == "Colonne « montant » inconnue — colonnes proches dans devis : montant_ht."
    assert close_columns('column "zzz" does not exist', [devis], ["devis"]) is None
    assert close_columns("syntax error", [devis], ["devis"]) is None


# --- Postgres sans serveur : le contrôle du rôle, les paramètres ----------------------


async def test_a_superuser_role_is_refused() -> None:
    """Le contrôle du rôle, sans base : un superutilisateur ou un BYPASSRLS est refusé."""
    pytest.importorskip("asyncpg", reason="extra 'sql' absent")
    from zoneinfo import ZoneInfo

    from loom_ia.tools_builtin.sql.postgres import PostgresDatabase

    spec = DatabaseSpec.model_validate(postgres())

    class Role:
        def __init__(self, **flags: bool) -> None:
            self.flags = flags

        async def fetchrow(self, *_: object) -> dict[str, object]:
            return {"name": "agent", "temp": False, **self.flags}

        async def fetchval(self, *_: object) -> object:
            return 1

        async def fetch(self, *_: object) -> list[object]:
            return []

    for flags, said in (
        ({"rolsuper": True, "rolbypassrls": False}, "est superutilisateur"),
        ({"rolsuper": False, "rolbypassrls": True}, "est BYPASSRLS"),
    ):
        database = PostgresDatabase(spec, "postgresql://x", Catalog.of(spec), ZoneInfo("UTC"))
        with pytest.raises(ToolError, match=f"le rôle agent {said} — il passe outre tout droit"):
            await database._check(Role(**flags))  # pyright: ignore[reportPrivateUsage, reportArgumentType]


def test_postgres_parameters_follow_the_declared_types() -> None:
    from datetime import date, datetime, time, timedelta
    from decimal import Decimal
    from zoneinfo import ZoneInfo

    pytest.importorskip("asyncpg", reason="extra 'sql' absent")
    from asyncpg.types import Type

    from loom_ia.tools_builtin.sql.postgres import convert

    def typed(name: str, kind: str = "scalar") -> Type:
        return Type(oid=0, name=name, kind=kind, schema="pg_catalog")

    paris = ZoneInfo("Europe/Paris")
    given: list[Any] = [
        "42",
        3.0,
        "2.5",
        "12.30",
        "non",
        7,
        "2026-10-01T09:30:00+00:00",
        "2026-10-01T09:30:00",
        "10:15",
        "PT1H30M",
        "brouillon",
        ["1", 2],
        None,
    ]
    names = [
        "int4",
        "int8",
        "float8",
        "numeric",
        "bool",
        "text",
        "timestamp",
        "timestamptz",
        "time",
        "interval",
        "statut",
        "_int4",
        "date",
    ]
    types = [
        typed(n, "enum" if n == "statut" else "array" if n == "_int4" else "scalar") for n in names
    ]
    assert convert(given, types, paris) == [
        42,
        3,
        2.5,
        Decimal("12.30"),
        False,
        "7",
        datetime(2026, 10, 1, 11, 30),
        datetime(2026, 10, 1, 9, 30, tzinfo=paris),
        time(10, 15),
        timedelta(hours=1, minutes=30),
        "brouillon",
        [1, 2],
        None,
    ]
    assert convert(["2026-10-01"], [typed("date")], paris) == [date(2026, 10, 1)]
    with pytest.raises(ToolError, match="2 paramètre\\(s\\) attendu\\(s\\), 1 donné"):
        convert([1], [typed("int4"), typed("int4")], paris)
    faults: list[tuple[Any, str, str]] = [
        (True, "float8", "n'est pas un nombre"),
        (False, "numeric", "n'est pas un nombre"),
        ("peut-être", "bool", "n'est pas un booléen"),
        (True, "text", "un texte est attendu"),
        ([1], "int4", "une valeur simple est attendue"),
        (12, "date", "un texte ISO 8601 est attendu"),
        ("P", "interval", "une durée ISO 8601"),
        ("192.168.0.1", "inet", r"le caster en texte dans le SQL \(\$n::text::inet\)"),
        (b"x".decode(), "bytea", "le caster en texte"),
    ]
    for value, name, said in faults:
        with pytest.raises(ToolError, match=f"Paramètre \\$1 : .*{said}"):
            convert([value], [typed(name)], paris)
