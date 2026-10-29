# SPDX-License-Identifier: Apache-2.0
"""Une base Postgres : un pool ouvert au premier appel, et le rôle de la base qui fait foi.

La barrière est le **rôle** du DSN : ses droits, table par table. L'outil
s'en assure au premier usage du pool (``_check``) :

- un superutilisateur, ou un rôle ``BYPASSRLS``, est refusé : il passe outre
  tout droit, et la config ne promettrait plus rien ;
- un droit que la config accorde et que la base refuse fait échouer chaque
  appel, jusqu'à ce qu'il soit donné : le modèle croirait pouvoir le faire ;
- un droit que la base accorde en plus de la config (une autre table, TRUNCATE,
  CREATE sur le schéma, tables temporaires…) est dit une fois, en
  avertissement : la défense ne repose plus alors que sur l'analyse.

Chaque appel est une transaction — en lecture seule pour ``query`` et
``schema`` — qui pose en local ``search_path`` (le schéma déclaré),
``statement_timeout`` et ``lock_timeout``. Chaque instruction est préparée
(protocole étendu) : le serveur refuse alors d'en recevoir plusieurs, et
donne les types de ses paramètres, qui servent à convertir les valeurs JSON
du modèle (asyncpg refuse une chaîne pour une ``date``).

Une annulation (délai de l'outil, arrêt du run) annule la requête côté serveur
(asyncpg envoie l'annulation) et la transaction ; la connexion est rendue au
pool, qui la remet à zéro ou la ferme.
"""

import asyncio
import json
import logging
import re
import uuid
from collections.abc import Awaitable, Callable, Sequence
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Final, cast
from zoneinfo import ZoneInfo

import asyncpg
from asyncpg.pool import PoolConnectionProxy
from asyncpg.types import Type
from pydantic import JsonValue

from loom_ia.core.ports import ToolError
from loom_ia.tools_builtin import Place
from loom_ia.tools_builtin.sql.analysis import Analysis, Catalog
from loom_ia.tools_builtin.sql.arguments import ParamValue, Statement
from loom_ia.tools_builtin.sql.schema import Column, TableInfo
from loom_ia.tools_builtin.sql.values import row
from loom_ia.tools_builtin.sql_params import DatabaseSpec

logger = logging.getLogger(__name__)

type Held = PoolConnectionProxy[asyncpg.Record]

POOL_MAX: Final = 4
CONNECT_TIMEOUT: Final = 10.0
_PRIVILEGES: Final = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")

_INTEGERS: Final = frozenset({"int2", "int4", "int8", "oid", "smallint", "integer", "bigint"})
_FLOATS: Final = frozenset({"float4", "float8", "real", "double precision"})
_TEXTS: Final = frozenset({"text", "varchar", "bpchar", "char", "name", "citext", "unknown"})


class PostgresDatabase:
    """Une base Postgres de la config, montée : son DSN (jamais montré), ses droits."""

    def __init__(self, spec: DatabaseSpec, dsn: str, catalog: Catalog, zone: ZoneInfo) -> None:
        if spec.postgres is None:
            raise TypeError("base Postgres attendue")
        self.spec = spec
        self.schema = spec.postgres.schema_name
        self.catalog = catalog
        self.zone = zone
        self._dsn = dsn
        self._pool: asyncpg.Pool[asyncpg.Record] | None = None
        self._lock = asyncio.Lock()
        self._checked = False

    def where(self) -> Place:
        assert self.spec.postgres is not None
        notes = (f"DSN dans {self.spec.postgres.dsn_env}", f"schéma {self.schema}")
        return Place(name="", kind="postgres", notes=notes)

    # --- Appels ---------------------------------------------------------------------

    async def query(self, unit: Statement, analysis: Analysis) -> dict[str, JsonValue]:
        del analysis  # le rôle fait foi : l'analyse a déjà jugé l'instruction
        limit = self.spec.max_rows

        async def work(conn: Held) -> dict[str, JsonValue]:
            statement = await conn.prepare(unit.sql)
            arguments = convert(unit.params, statement.get_parameters(), self.zone)
            fetched: list[asyncpg.Record] = []
            async for record in statement.cursor(*arguments, prefetch=min(limit + 1, 500)):
                fetched.append(record)
                if len(fetched) > limit:
                    break
            columns = [attribute.name for attribute in statement.get_attributes()]
            rows: list[JsonValue] = [row(tuple(r.values()), self.zone) for r in fetched[:limit]]
            return {
                "database": self.spec.name,
                "columns": list[JsonValue](columns),
                "rows": rows,
                "row_count": len(rows),
                "truncated": len(fetched) > limit,
            }

        return await self._run(work, readonly=True)

    async def execute(self, units: list[tuple[Statement, Analysis]]) -> dict[str, JsonValue]:
        async def work(conn: Held) -> dict[str, JsonValue]:
            results: list[JsonValue] = []
            affected = 0
            for position, (unit, _) in enumerate(units, start=1):
                try:
                    statement = await conn.prepare(unit.sql)
                    arguments = convert(unit.params, statement.get_parameters(), self.zone)
                    returned = await statement.fetch(*arguments)
                except ToolError as exc:
                    raise ToolError(f"Instruction {position} : {exc.message}") from None
                except asyncpg.PostgresError as exc:
                    raise _Failed(position, exc) from exc
                count = _affected(statement.get_statusmsg(), len(returned))
                affected += count
                if affected > self.spec.max_affected_rows:
                    raise ToolError(
                        f"{affected} lignes modifiées, au-delà de {self.spec.max_affected_rows} : "
                        "rien n'est enregistré (transaction annulée). Restreindre le WHERE, ou "
                        "procéder par lots."
                    )
                result: dict[str, JsonValue] = {"affected_rows": count}
                attributes = statement.get_attributes()
                if attributes:
                    result["columns"] = [attribute.name for attribute in attributes]
                    result["returning"] = [
                        row(tuple(r.values()), self.zone) for r in returned[: self.spec.max_rows]
                    ]
                    if len(returned) > self.spec.max_rows:
                        result["truncated"] = True
                results.append(result)
            return {
                "database": self.spec.name,
                "results": results,
                "affected_rows": affected,
                "committed": True,
            }

        return await self._run(work, readonly=False)

    async def introspect(self) -> list[TableInfo]:
        declared = list(self.catalog.rights)

        async def work(conn: Held) -> list[TableInfo]:
            columns = await conn.fetch(_COLUMNS_SQL, self.schema, declared)
            constraints = await conn.fetch(_CONSTRAINTS_SQL, self.schema, declared)
            by_table: dict[str, dict[str, object]] = {}
            for record in columns:
                entry = by_table.setdefault(
                    record["relname"],
                    {"kind": record["relkind"], "comment": record["comment"], "columns": []},
                )
                if record["attname"] is not None:
                    cast(list[Column], entry["columns"]).append(
                        Column(
                            name=record["attname"],
                            type=record["type"],
                            not_null=bool(record["attnotnull"]),
                            default=record["coldefault"],
                            comment=record["colcomment"],
                        )
                    )
            kept: dict[str, list[str]] = {}
            for record in constraints:
                if record["contype"] == "f" and record["ref"] not in self.catalog.rights:
                    continue  # une clé vers une table non permise : rien à en dire
                kept.setdefault(record["relname"], []).append(record["def"])
            return [
                TableInfo(
                    name=name,
                    kind="view" if entry["kind"] in ("v", "m") else "table",
                    columns=tuple(cast(list[Column], entry["columns"])),
                    constraints=tuple(kept.get(name, ())),
                    comment=cast(str | None, entry["comment"]),
                )
                for name, entry in by_table.items()
            ]

        return await self._run(work, readonly=True)

    async def aclose(self) -> None:
        pool, self._pool = self._pool, None
        if pool is not None:
            await pool.close()

    # --- Connexion ------------------------------------------------------------------

    async def _run[T](self, work: Callable[[Held], Awaitable[T]], *, readonly: bool) -> T:
        pool = await self._ready()
        try:
            async with pool.acquire() as conn:
                async with conn.transaction(readonly=readonly):
                    await self._settings(conn)
                    return await work(conn)
        except ToolError:
            raise
        except _Failed as exc:
            raise ToolError(
                f"Instruction {exc.position} : {self._said(exc.error)} Rien n'est enregistré."
            ) from exc
        except asyncpg.PostgresError as exc:
            raise ToolError(self._said(exc)) from exc
        except (OSError, TimeoutError, asyncpg.InterfaceError) as exc:
            raise ToolError(
                f"Base {self.spec.name} injoignable ({type(exc).__name__}) : réessayer plus tard."
            ) from exc

    async def _settings(self, conn: Held) -> None:
        timeout = str(int(self.spec.statement_timeout * 1000))
        await conn.execute(
            "SELECT set_config('search_path', quote_ident($1), true), "
            "set_config('statement_timeout', $2, true), set_config('lock_timeout', $2, true)",
            self.schema,
            timeout,
        )

    async def _ready(self) -> asyncpg.Pool[asyncpg.Record]:
        async with self._lock:
            if self._pool is None:
                try:
                    self._pool = await asyncpg.create_pool(
                        self._dsn,
                        min_size=0,
                        max_size=POOL_MAX,
                        timeout=CONNECT_TIMEOUT,
                        init=_init,
                        server_settings={"application_name": "loom.sql"},
                    )
                except (OSError, TimeoutError, asyncpg.PostgresError, ValueError) as exc:
                    raise ToolError(
                        f"Base {self.spec.name} injoignable ({type(exc).__name__}) : "
                        "réessayer plus tard."
                    ) from exc
            pool = self._pool
            if not self._checked:
                try:
                    async with pool.acquire() as conn:
                        await self._check(conn)
                except (OSError, TimeoutError, asyncpg.InterfaceError) as exc:
                    raise ToolError(
                        f"Base {self.spec.name} injoignable ({type(exc).__name__}) : "
                        "réessayer plus tard."
                    ) from exc
                except asyncpg.PostgresError as exc:
                    raise ToolError(
                        f"Base {self.spec.name} : connexion refusée ({exc.sqlstate})."
                    ) from exc
                self._checked = True
        return pool

    async def _check(self, conn: Held) -> None:
        """Le rôle, et ses droits face à la config ; voir la docstring du module."""
        role = await conn.fetchrow(
            "SELECT current_user AS name, rolsuper, rolbypassrls, "
            "has_database_privilege(current_database(), 'TEMP') AS temp "
            "FROM pg_roles WHERE rolname = current_user"
        )
        assert role is not None
        name = self.spec.name
        if role["rolsuper"] or role["rolbypassrls"]:
            said = "superutilisateur" if role["rolsuper"] else "BYPASSRLS"
            raise ToolError(
                f"Base {name} : le rôle {role['name']} est {said} — il passe outre tout droit, "
                "loom.sql le refuse. Donner un rôle dédié, aux droits de la config."
            )
        # has_schema_privilege lève sur un schéma absent : on regarde d'abord s'il existe.
        if await conn.fetchval("SELECT to_regnamespace(quote_ident($1))", self.schema) is None:
            raise ToolError(f"Base {name} : le schéma {self.schema} n'existe pas.")
        creates = await conn.fetchval(
            "SELECT has_schema_privilege(current_user, $1, 'CREATE')", self.schema
        )
        granted = await conn.fetch(_PRIVILEGES_SQL, self.schema, list(self.catalog.rights))
        missing: list[str] = []
        extra: list[str] = []
        absent: list[str] = []
        for record in granted:
            table = record["table_name"]
            if record["oid"] is None:
                absent.append(table)
                continue
            declared = {right.upper() for right in self.catalog.rights[table]}
            held = {p for p in _PRIVILEGES if record[p.lower()]}
            missing += [f"{table} {p}" for p in sorted(declared - held)]
            extra += [f"{table} {p}" for p in sorted(held - declared)]
        if missing:
            raise ToolError(
                f"Base {name} : droits accordés par la config mais refusés par la base au rôle "
                f"{role['name']} : {', '.join(missing)}. Les donner (GRANT), ou les retirer de "
                "la config."
            )
        others = await conn.fetch(_OTHERS_SQL, self.schema, list(self.catalog.rights))
        extra += [f"{record['relname']} (non déclarée)" for record in others]
        if creates:
            extra.append(f"CREATE sur le schéma {self.schema}")
        if role["temp"]:
            extra.append("TEMP sur la base")
        if absent:
            logger.warning(
                "loom.sql, base %s : table(s) déclarée(s) absente(s) de la base : %s",
                name,
                ", ".join(absent),
            )
        if extra:
            logger.warning(
                "loom.sql, base %s : le rôle %s peut plus que la config — %s. La défense ne "
                "repose alors que sur l'analyse de l'outil : retirer ces droits (REVOKE).",
                name,
                role["name"],
                ", ".join(extra),
            )

    def _said(self, exc: asyncpg.PostgresError) -> str:
        message = str(getattr(exc, "message", "") or exc)
        if isinstance(exc, asyncpg.exceptions.QueryCanceledError):
            return (
                f"Instruction arrêtée après {self.spec.statement_timeout:g} s (délai de la base)."
            )
        if isinstance(exc, asyncpg.exceptions.LockNotAvailableError):
            return (
                f"Verrou non obtenu en {self.spec.statement_timeout:g} s : une autre "
                "transaction tient ces lignes ; réessayer plus tard."
            )
        if isinstance(exc, asyncpg.exceptions.InsufficientPrivilegeError):
            return f"Accès refusé par la base : {message}."
        if isinstance(exc, asyncpg.exceptions.ReadOnlySQLTransactionError):
            return "Lecture seule : query ne modifie rien, une écriture passe par execute."
        detail = getattr(exc, "detail", None)
        hint = getattr(exc, "hint", None)
        extra = "".join(f" {part}" for part in (detail, hint) if part)
        return f"Erreur Postgres ({exc.sqlstate}) : {message}.{extra}"


class _Failed(Exception):
    def __init__(self, position: int, error: asyncpg.PostgresError) -> None:
        super().__init__(str(error))
        self.position = position
        self.error = error


async def _init(conn: asyncpg.Connection[asyncpg.Record]) -> None:
    for name in ("json", "jsonb"):
        await conn.set_type_codec(
            name, schema="pg_catalog", encoder=_json_dumps, decoder=json.loads, format="text"
        )


def _json_dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def _affected(status: str | None, returned: int) -> int:
    """« UPDATE 3 », « INSERT 0 1 », « DELETE 2 » : le dernier nombre."""
    last = (status or "").rsplit(" ", 1)[-1]
    return int(last) if last.isdigit() else returned


# --- Paramètres : du JSON du modèle aux types qu'attend asyncpg ------------------------


def convert(params: Sequence[ParamValue], types: Sequence[Type], zone: ZoneInfo) -> list[object]:
    if len(params) != len(types):
        raise ToolError(f"{len(types)} paramètre(s) attendu(s), {len(params)} donné(s).")
    return [
        _value(value, expected, zone, f"${index}")
        for index, (value, expected) in enumerate(zip(params, types, strict=True), start=1)
    ]


def _value(value: ParamValue, expected: Type, zone: ZoneInfo, where: str) -> object:
    if value is None:
        return None
    name = expected.name
    if expected.kind == "array":
        if not isinstance(value, list):
            raise ToolError(f"Paramètre {where} : une liste est attendue ({name}).")
        element = Type(
            oid=0,
            name=name.removeprefix("_").removesuffix("[]"),
            kind="scalar",
            schema="pg_catalog",
        )
        return [_value(item, element, zone, where) for item in value]
    try:
        return _scalar(value, name, expected, zone)
    except ToolError as exc:
        raise ToolError(f"Paramètre {where} : {exc.message}") from None
    except ValueError, TypeError, InvalidOperation, ArithmeticError:
        raise ToolError(f"Paramètre {where} : {value!r} ne convient pas au type {name}.") from None


def _scalar(value: ParamValue, name: str, expected: Type, zone: ZoneInfo) -> object:
    if name in ("json", "jsonb"):
        return value
    if isinstance(value, list | dict):
        raise ToolError(f"une valeur simple est attendue ({name}), pas {type(value).__name__}.")
    assert value is not None  # écarté par _value
    if name in _INTEGERS:
        if isinstance(value, bool):
            raise ToolError(f"{value!r} n'est pas un entier.")
        if isinstance(value, float):
            if not value.is_integer():
                raise ToolError(f"{value!r} n'est pas un entier.")
            return int(value)
        return int(value)
    if name in _FLOATS:
        if isinstance(value, bool):
            raise ToolError(f"{value!r} n'est pas un nombre.")
        return float(value)
    if name == "numeric":
        if isinstance(value, bool):
            raise ToolError(f"{value!r} n'est pas un nombre.")
        return Decimal(str(value))
    if name == "bool":
        if isinstance(value, bool):
            return value
        said = str(value).strip().lower()
        if said in ("true", "t", "1", "yes", "oui"):
            return True
        if said in ("false", "f", "0", "no", "non"):
            return False
        raise ToolError(f"{value!r} n'est pas un booléen.")
    if name in _TEXTS or expected.kind == "enum":
        if isinstance(value, bool):
            raise ToolError(f"{value!r} : un texte est attendu.")
        return str(value)
    if not isinstance(value, str):
        if name == "interval" and not isinstance(value, bool):
            return timedelta(seconds=value)
        raise ToolError(f"{value!r} : un texte ISO 8601 est attendu ({name}).")
    match name:
        case "date":
            return date.fromisoformat(value)
        case "timestamp":
            moment = datetime.fromisoformat(value)
            return moment.astimezone(zone).replace(tzinfo=None) if moment.tzinfo else moment
        case "timestamptz":
            moment = datetime.fromisoformat(value)
            return moment if moment.tzinfo else moment.replace(tzinfo=zone)
        case "time" | "timetz":
            return time.fromisoformat(value)
        case "uuid":
            return uuid.UUID(value)
        case "interval":
            return _interval(value)
        case _:
            raise ToolError(
                f"type {name} non converti : le caster en texte dans le SQL ($n::text::{name})."
            )


_ISO_DURATION: Final = re.compile(
    r"^P(?:(?P<w>\d+(?:\.\d+)?)W)?(?:(?P<d>\d+(?:\.\d+)?)D)?"
    r"(?:T(?:(?P<h>\d+(?:\.\d+)?)H)?(?:(?P<m>\d+(?:\.\d+)?)M)?(?:(?P<s>\d+(?:\.\d+)?)S)?)?$"
)


def _interval(value: str) -> timedelta:
    found = _ISO_DURATION.match(value.strip().upper())
    if found is None or value.strip().upper() in ("P", "PT"):
        raise ToolError(f"{value!r} : une durée ISO 8601 (P1DT2H) ou des secondes est attendue.")
    parts = {key: float(found.group(key) or 0) for key in "wdhms"}
    return timedelta(
        weeks=parts["w"], days=parts["d"], hours=parts["h"], minutes=parts["m"], seconds=parts["s"]
    )


# --- Catalogue -------------------------------------------------------------------------

_COLUMNS_SQL: Final = """
SELECT c.relname, c.relkind::text AS relkind, obj_description(c.oid, 'pg_class') AS comment,
       a.attname, format_type(a.atttypid, a.atttypmod) AS type, a.attnotnull,
       pg_get_expr(d.adbin, d.adrelid) AS coldefault, col_description(c.oid, a.attnum) AS colcomment
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
LEFT JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
LEFT JOIN pg_attrdef d ON d.adrelid = c.oid AND d.adnum = a.attnum
WHERE n.nspname = $1 AND c.relname = ANY($2::text[]) AND c.relkind IN ('r', 'v', 'm', 'p', 'f')
ORDER BY c.relname, a.attnum
"""

_CONSTRAINTS_SQL: Final = """
SELECT cl.relname, con.contype::text AS contype, pg_get_constraintdef(con.oid) AS def,
       ref.relname AS ref
FROM pg_constraint con
JOIN pg_class cl ON cl.oid = con.conrelid
JOIN pg_namespace n ON n.oid = cl.relnamespace
LEFT JOIN pg_class ref ON ref.oid = con.confrelid
WHERE n.nspname = $1 AND cl.relname = ANY($2::text[]) AND con.contype IN ('p', 'u', 'f', 'c')
ORDER BY cl.relname,
  CASE con.contype WHEN 'p' THEN 1 WHEN 'u' THEN 2 WHEN 'f' THEN 3 ELSE 4 END, con.conname
"""

_PRIVILEGES_SQL: Final = """
SELECT t.table_name, r.oid,
       r.oid IS NOT NULL AND has_table_privilege(r.oid, 'SELECT') AS select,
       r.oid IS NOT NULL AND has_table_privilege(r.oid, 'INSERT') AS insert,
       r.oid IS NOT NULL AND has_table_privilege(r.oid, 'UPDATE') AS update,
       r.oid IS NOT NULL AND has_table_privilege(r.oid, 'DELETE') AS delete,
       r.oid IS NOT NULL AND has_table_privilege(r.oid, 'TRUNCATE') AS truncate,
       r.oid IS NOT NULL AND has_table_privilege(r.oid, 'REFERENCES') AS references,
       r.oid IS NOT NULL AND has_table_privilege(r.oid, 'TRIGGER') AS trigger
FROM unnest($2::text[]) AS t(table_name)
LEFT JOIN LATERAL (SELECT to_regclass(format('%I.%I', $1::text, t.table_name))::oid AS oid) r
  ON true
"""

_OTHERS_SQL: Final = """
SELECT c.relname
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = $1 AND c.relkind IN ('r', 'v', 'm', 'p', 'f')
  AND NOT (c.relname = ANY($2::text[]))
  AND (has_table_privilege(c.oid, 'SELECT') OR has_table_privilege(c.oid, 'INSERT')
       OR has_table_privilege(c.oid, 'UPDATE') OR has_table_privilege(c.oid, 'DELETE')
       OR has_table_privilege(c.oid, 'TRUNCATE'))
ORDER BY c.relname
"""
