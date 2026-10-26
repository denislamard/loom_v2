# SPDX-License-Identifier: Apache-2.0
"""``loom.sql`` sur un vrai Postgres : le rôle qui fait foi, les types, les transactions.

Chaque essai crée son schéma et son rôle — un rôle dédié, aux seuls droits que
la config déclare, comme le guide le recommande —, puis les supprime. Le rôle
de ``LOOM_TEST_POSTGRES`` doit pouvoir créer des rôles (CREATEROLE) et
posséder sa base ; il n'est pas superutilisateur.
"""

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any, Final
from urllib.parse import urlsplit, urlunsplit

import pytest

pytest.importorskip("sqlglot", reason="extra 'sql' absent")
pytest.importorskip("asyncpg", reason="extra 'sql' absent")

import asyncpg

from loom_ia.core.model import DEFAULT_TENANT, RunId, SessionId
from loom_ia.core.ports import ToolContext, ToolError
from loom_ia.tools_builtin import BuiltinContext
from loom_ia.tools_builtin.sql import SqlTool, build
from loom_ia.tools_builtin.sql.analysis import Analysis
from loom_ia.tools_builtin.sql.arguments import Statement
from loom_ia.tools_builtin.sql.postgres import PostgresDatabase
from loom_ia.tools_builtin.sql_params import SqlParams

pytestmark = pytest.mark.integration

CONTEXT: Final = ToolContext(
    tenant_id=DEFAULT_TENANT, session_id=SessionId("s"), run_id=RunId("r"), call_id="c", agent="a"
)
PASSWORD: Final = "agent-essai"

DDL: Final = """
CREATE TABLE {s}.clients (id serial PRIMARY KEY, nom text NOT NULL);
COMMENT ON COLUMN {s}.clients.nom IS 'raison sociale';
CREATE TABLE {s}.audit (id serial PRIMARY KEY, msg text);
CREATE TABLE {s}.devis (
  id serial PRIMARY KEY,
  client_id int NOT NULL REFERENCES {s}.clients(id),
  audit_id int REFERENCES {s}.audit(id),
  statut text NOT NULL DEFAULT 'brouillon' CHECK (statut IN ('brouillon', 'envoye', 'relance')),
  montant_ht numeric(10, 2),
  envoye_le timestamptz,
  echeance date,
  meta jsonb,
  ref uuid,
  delai interval,
  tags text[]
);
COMMENT ON TABLE {s}.devis IS 'Devis envoyés aux clients';
CREATE TABLE {s}.relances (id serial PRIMARY KEY, devis_id int, canal text);
INSERT INTO {s}.clients (nom) VALUES ('Dupont'), ('Martin');
INSERT INTO {s}.devis (client_id, statut, montant_ht, envoye_le, echeance, meta, ref, delai, tags)
VALUES (1, 'envoye', 1250.50, '2026-10-01 07:30+00', '2026-11-01', '{{"canal": "email"}}',
        '00000000-0000-0000-0000-000000000001', '1 day 02:30', ARRAY['urgent']),
       (2, 'envoye', 300, '2026-10-20 12:00+00', '2026-11-20', NULL, NULL, NULL, NULL);
"""

TABLES: Final = {
    "devis": ["select", "update"],
    "clients": ["select"],
    "relances": ["select", "insert"],
}


def _as(dsn: str, role: str) -> str:
    parts = urlsplit(dsn)
    host = parts.hostname or "127.0.0.1"
    port = f":{parts.port}" if parts.port else ""
    return urlunsplit(parts._replace(netloc=f"{role}:{PASSWORD}@{host}{port}"))


class Base:
    """Un schéma et un rôle d'essai, le DSN de ce rôle, et de quoi regarder la base."""

    def __init__(self, owner: str) -> None:
        tag = uuid.uuid4().hex[:8]
        self.schema = f"loom_sql_{tag}"
        self.role = f"loom_sql_agent_{tag}"
        self.owner = owner
        self.dsn = _as(owner, self.role)

    async def fetch(self, sql: str) -> list[asyncpg.Record]:
        conn = await asyncpg.connect(self.owner)
        try:
            return await conn.fetch(sql.format(s=self.schema))
        finally:
            await conn.close()

    async def run(self, sql: str) -> None:
        conn = await asyncpg.connect(self.owner)
        try:
            await conn.execute(sql.format(s=self.schema, r=self.role))
        finally:
            await conn.close()


@pytest.fixture
async def base(postgres_dsn: str) -> AsyncIterator[Base]:
    made = Base(postgres_dsn)
    await made.run(f"CREATE SCHEMA {made.schema}")
    await made.run(DDL)
    await made.run(
        f"CREATE ROLE {made.role} LOGIN PASSWORD '{PASSWORD}' NOSUPERUSER NOCREATEDB NOCREATEROLE;"
        f"GRANT USAGE ON SCHEMA {made.schema} TO {made.role};"
        f"GRANT SELECT, UPDATE ON {made.schema}.devis TO {made.role};"
        f"GRANT SELECT ON {made.schema}.clients TO {made.role};"
        f"GRANT SELECT, INSERT ON {made.schema}.relances TO {made.role};"
        f"GRANT USAGE ON SEQUENCE {made.schema}.relances_id_seq TO {made.role};"
    )
    try:
        yield made
    finally:
        await made.run(f"DROP SCHEMA {made.schema} CASCADE")
        await made.run(f"DROP ROLE {made.role}")


def make(base: Base, **changes: Any) -> SqlTool:
    spec = {
        "name": "crm",
        "postgres": {"dsn_env": "CRM_DSN", "schema": base.schema},
        "tables": TABLES,
        **changes,
    }
    params = SqlParams.model_validate({"databases": [spec]})
    return build(params, BuiltinContext(secrets={"CRM_DSN": base.dsn}))


@pytest.fixture
async def tool(base: Base) -> AsyncIterator[SqlTool]:
    made = make(base, functions=["age"])
    try:
        yield made
    finally:
        await made.aclose()


async def call(tool: SqlTool, **arguments: Any) -> dict[str, Any]:
    output = await tool.invoke(dict(arguments), CONTEXT)
    assert isinstance(output.data, dict)
    return output.data


def query(sql: str, *params: Any) -> dict[str, Any]:
    return {"action": "query", "database": "crm", "sql": sql, "params": list(params)}


def execute(*statements: tuple[str, list[Any]] | str) -> dict[str, Any]:
    units = [
        {"sql": s} if isinstance(s, str) else {"sql": s[0], "params": s[1]} for s in statements
    ]
    return {"action": "execute", "database": "crm", "statements": units}


# --- Valeurs ----------------------------------------------------------------------------


async def test_values_come_back_as_json(tool: SqlTool) -> None:
    result = await call(
        tool,
        **query(
            "SELECT montant_ht, envoye_le, echeance, meta, ref, delai, tags, age($1::date, "
            "echeance) AS ecart FROM devis WHERE id = $2",
            "2026-12-01",
            1,
        ),
    )
    assert result["columns"] == [
        "montant_ht",
        "envoye_le",
        "echeance",
        "meta",
        "ref",
        "delai",
        "tags",
        "ecart",
    ]
    assert result["rows"] == [
        [
            "1250.50",
            "2026-10-01T09:30:00+02:00",
            "2026-11-01",
            {"canal": "email"},
            "00000000-0000-0000-0000-000000000001",
            "P1DT2H30M",
            ["urgent"],
            "P30D",
        ]
    ]


@pytest.mark.parametrize(
    ("where", "value", "count"),
    [
        ("echeance < $1", "2026-11-10", 1),
        ("envoye_le > $1", "2026-10-15T00:00:00", 1),  # sans fuseau : heure de Paris
        ("envoye_le > $1", "2026-10-01T07:29:00Z", 2),
        ("montant_ht > $1", 500, 1),
        ("montant_ht > $1", "299.99", 2),
        ("id = ANY($1)", [1, 2], 2),
        ("ref = $1", "00000000-0000-0000-0000-000000000001", 1),
        ("meta = $1", {"canal": "email"}, 1),
        ("delai > $1", 3600, 1),
        ("delai > $1", "P2D", 0),
        ("statut = $1", "envoye", 2),
        ("(id = 1) = $1", "oui", 1),
    ],
)
async def test_parameters_are_converted_to_their_type(
    tool: SqlTool, where: str, value: Any, count: int
) -> None:
    result = await call(tool, **query(f"SELECT count(*) AS n FROM devis WHERE {where}", value))
    assert result["rows"] == [[count]]


@pytest.mark.parametrize(
    ("where", "value", "said"),
    [
        (
            "echeance < $1",
            "01/11/2026",
            r"Paramètre \$1 : '01/11/2026' ne convient pas au type date",
        ),
        ("id = $1", True, r"Paramètre \$1 : True n'est pas un entier"),
        ("id = $1", 1.5, r"n'est pas un entier"),
        ("id = ANY($1)", 1, r"une liste est attendue"),
        ("ref = $1", "pas-un-uuid", r"ne convient pas au type uuid"),
    ],
)
async def test_a_parameter_that_does_not_fit_is_said(
    tool: SqlTool, where: str, value: Any, said: str
) -> None:
    with pytest.raises(ToolError, match=said):
        await tool.invoke(query(f"SELECT count(*) FROM devis WHERE {where}", value), CONTEXT)


# --- Écritures et transactions --------------------------------------------------------------


async def test_execute_commits_together_or_not_at_all(tool: SqlTool, base: Base) -> None:
    result = await call(
        tool,
        **execute(
            ("UPDATE devis SET statut = $1 WHERE id = $2 RETURNING id, statut", ["relance", 1]),
            ("INSERT INTO relances (devis_id, canal) VALUES ($1,$2)", [1, "email"]),
        ),
    )
    assert result["results"] == [
        {"affected_rows": 1, "columns": ["id", "statut"], "returning": [[1, "relance"]]},
        {"affected_rows": 1},
    ]
    with pytest.raises(
        ToolError, match=r"Instruction 2 : .*check constraint.*Rien n'est enregistré"
    ):
        await tool.invoke(
            execute(
                ("UPDATE devis SET statut = $1 WHERE id = $2", ["envoye", 1]),
                ("UPDATE devis SET statut = $1 WHERE id = $2", ["perdu", 2]),
            ),
            CONTEXT,
        )
    found = await base.fetch("SELECT id, statut FROM {s}.devis ORDER BY id")
    assert [tuple(r) for r in found] == [(1, "relance"), (2, "envoye")]


async def test_too_many_rows_roll_back(base: Base) -> None:
    tool = make(base, max_affected_rows=1)
    try:
        with pytest.raises(ToolError, match="2 lignes modifiées, au-delà de 1"):
            await tool.invoke(execute("UPDATE devis SET statut = 'relance' WHERE id > 0"), CONTEXT)
    finally:
        await tool.aclose()
    found = await base.fetch("SELECT count(*) FROM {s}.devis WHERE statut = 'relance'")
    assert found[0][0] == 0


# --- Le rôle fait foi ---------------------------------------------------------------------------


def _analysis() -> Analysis:
    return Analysis(operation="select", needs={}, ctes=frozenset())


async def test_the_role_refuses_what_the_analysis_would_have(tool: SqlTool, base: Base) -> None:
    """Sans l'analyse devant, la base refuse : le rôle n'a pas DELETE, ni la table audit."""
    backend = make(base)._database("crm").backend  # pyright: ignore[reportPrivateUsage]
    assert isinstance(backend, PostgresDatabase)
    try:
        with pytest.raises(ToolError, match="Accès refusé par la base"):
            await backend.execute([(Statement(sql="DELETE FROM devis WHERE id = 1"), _analysis())])
        with pytest.raises(ToolError, match="Accès refusé par la base"):
            await backend.query(Statement(sql="SELECT * FROM audit"), _analysis())
        with pytest.raises(ToolError, match="Lecture seule"):
            await backend.query(
                Statement(sql="UPDATE devis SET statut = 'x' WHERE id = 1"), _analysis()
            )
        with pytest.raises(ToolError, match="Accès refusé par la base"):
            await backend.execute([(Statement(sql="CREATE TABLE x (a int)"), _analysis())])
        with pytest.raises(ToolError, match="cannot insert multiple commands"):
            await backend.query(Statement(sql="SELECT 1; SELECT 2"), _analysis())
    finally:
        await backend.aclose()


async def test_a_declared_right_the_base_refuses_blocks_every_call(base: Base) -> None:
    tool = make(base, tables={**TABLES, "clients": ["select", "insert"]})
    try:
        with pytest.raises(ToolError, match=r"refusés par la base au rôle .*: clients INSERT"):
            await tool.invoke(query("SELECT 1"), CONTEXT)
        await base.run("GRANT INSERT ON {s}.clients TO {r}")
        assert (await call(tool, **query("SELECT 1 AS un")))["rows"] == [[1]]
    finally:
        await tool.aclose()


async def test_extra_rights_are_said_once(base: Base, caplog: pytest.LogCaptureFixture) -> None:
    await base.run("GRANT DELETE, TRUNCATE ON {s}.devis TO {r}; GRANT SELECT ON {s}.audit TO {r}")
    tool = make(base)
    try:
        with caplog.at_level(logging.WARNING, logger="loom_ia.tools_builtin.sql.postgres"):
            await call(tool, **query("SELECT 1 AS un"))
            await call(tool, **query("SELECT 1 AS un"))
    finally:
        await tool.aclose()
    said = [r.getMessage() for r in caplog.records if "peut plus que la config" in r.getMessage()]
    assert len(said) == 1
    assert "devis DELETE, devis TRUNCATE, audit (non déclarée)" in said[0]


async def test_an_unknown_schema_or_a_bad_password(base: Base) -> None:
    tool = make(base)
    tool_bad = build(
        SqlParams.model_validate(
            {
                "databases": [
                    {
                        "name": "crm",
                        "postgres": {"dsn_env": "D", "schema": base.schema},
                        "tables": TABLES,
                    }
                ]
            }
        ),
        BuiltinContext(secrets={"D": base.dsn.replace(base.role, "inconnu_essai")}),
    )
    try:
        with pytest.raises(ToolError, match=r"Base crm injoignable|connexion refusée") as raised:
            await tool_bad.invoke(query("SELECT 1"), CONTEXT)
        assert PASSWORD not in raised.value.message
    finally:
        await tool.aclose()
        await tool_bad.aclose()
    gone = make(base, postgres={"dsn_env": "CRM_DSN", "schema": "nulle_part"})
    try:
        with pytest.raises(ToolError, match="le schéma nulle_part n'existe pas"):
            await gone.invoke(query("SELECT 1"), CONTEXT)
    finally:
        await gone.aclose()


# --- Délais et annulation ------------------------------------------------------------------------


async def test_a_long_query_is_stopped_by_the_base(base: Base) -> None:
    tool = make(base, statement_timeout=0.5)
    try:
        with pytest.raises(ToolError, match=r"Instruction arrêtée après 0\.5 s"):
            await tool.invoke(query("SELECT count(*) FROM generate_series(1, 1000000000)"), CONTEXT)
    finally:
        await tool.aclose()


async def test_a_cancelled_call_stops_the_query_on_the_server(tool: SqlTool, base: Base) -> None:
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(
            tool.invoke(query("SELECT count(*) FROM generate_series(1, 1000000000)"), CONTEXT), 0.5
        )
    await asyncio.sleep(0.5)
    active = await base.fetch(
        f"SELECT count(*) FROM pg_stat_activity WHERE usename = '{base.role}' AND state = 'active'"
    )
    assert active[0][0] == 0


# --- Schéma -----------------------------------------------------------------------------


async def test_schema_is_rendered_from_the_catalog(tool: SqlTool) -> None:
    output = await tool.invoke({"action": "schema", "database": "crm"}, CONTEXT)
    text = output.as_text
    assert (
        "-- droits : SELECT, UPDATE (approbation humaine)\nCREATE TABLE devis (  -- Devis envoyés"
        in text
    )
    assert "  montant_ht numeric(10,2)," in text
    assert "  FOREIGN KEY (client_id) REFERENCES clients(id)," in text
    # La clé vers audit, table non permise, n'est pas montrée.
    assert "audit" not in text.replace("audit_id", "")
    assert (
        "CHECK ((statut = ANY (ARRAY['brouillon'::text, 'envoye'::text, 'relance'::text])))" in text
    )
    assert "  nom text NOT NULL,  -- raison sociale" in text


async def test_an_unknown_column_gets_close_names(tool: SqlTool) -> None:
    with pytest.raises(ToolError, match="colonnes proches dans devis : montant_ht"):
        await tool.invoke(query("SELECT montant FROM devis"), CONTEXT)


async def test_aclose_closes_the_pool(tool: SqlTool, base: Base) -> None:
    await call(tool, **query("SELECT 1 AS un"))
    await tool.aclose()
    await asyncio.sleep(0.1)
    found = await base.fetch(f"SELECT count(*) FROM pg_stat_activity WHERE usename = '{base.role}'")
    assert found[0][0] == 0
