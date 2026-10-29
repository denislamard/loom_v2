# SPDX-License-Identifier: Apache-2.0
"""Une base SQLite : une connexion par appel, et l'authorizer qui fait foi.

SQLite n'a pas de rôles : la barrière est l'**authorizer** de la connexion,
appelé à la préparation de chaque instruction — triggers et vues compris.
Il accorde, table par table, ce que la config accorde, et rien d'autre :
DDL, ``ATTACH``, ``PRAGMA``, fonctions de ``FORBIDDEN`` sont refusés.

Ce qu'il voit, essayé le 26/10 (SQLite 3.53) :

- une lecture de colonne : ``READ(table, colonne, 'main', origine)`` ; l'origine
  est la vue, la CTE ou le trigger qui la fait, ``None`` à la racine ;
- ``count(*)`` : ``READ(table, '', None, None)`` — la même forme qu'une CTE
  lue sans colonne. Une table non déclarée y est donc refusée, à moins que
  l'analyse n'en ait fait une CTE ;
- ``count(*)`` sur une vue : ses colonnes lues avec la vue pour origine, puis
  ``READ(table, '', 'main', None)`` — permis pour une table qu'une vue permise
  vient de lire dans le même appel ;
- un trigger qui écrit : ``INSERT(table, None, 'main', trigger)``. L'agent ne
  peut pas créer de trigger (DDL refusé) : ceux de la base sont ceux de son
  propriétaire, et leurs accès sont permis.

La connexion s'ouvre en ``mode=ro`` pour une lecture, ``mode=rw`` pour une
écriture, jamais ``rwc`` : l'outil ne crée pas de base. ``trusted_schema``
est coupé et ``ATTACH`` borné à zéro avant que l'authorizer ne soit posé.
Le délai d'une instruction passe par ``set_progress_handler`` ; une
annulation (délai de l'outil, arrêt du run) interrompt la connexion depuis
la boucle (``interrupt``), et la transaction est annulée.
"""

import asyncio
import json
import sqlite3
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from urllib.parse import quote
from zoneinfo import ZoneInfo

from pydantic import JsonValue

from loom_ia.core.ports import ToolError
from loom_ia.tools_builtin import Place
from loom_ia.tools_builtin.sql.analysis import Analysis, Catalog, is_forbidden
from loom_ia.tools_builtin.sql.arguments import ParamValue, Statement
from loom_ia.tools_builtin.sql.schema import Column, TableInfo
from loom_ia.tools_builtin.sql.values import row
from loom_ia.tools_builtin.sql_params import DatabaseSpec, SqlRight

# Toutes les N instructions de la machine virtuelle de SQLite, le délai est regardé.
_PROGRESS_STEPS: Final = 1000

_WRITES: Final[Mapping[int, SqlRight]] = {
    sqlite3.SQLITE_INSERT: "insert",
    sqlite3.SQLITE_UPDATE: "update",
    sqlite3.SQLITE_DELETE: "delete",
}
_ALWAYS: Final = frozenset(
    {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_TRANSACTION, sqlite3.SQLITE_RECURSIVE}
)


@dataclass(frozen=True, slots=True)
class Objects:
    """Les vues et les triggers de la base : l'origine d'un accès en est un, ou une CTE."""

    views: frozenset[str]
    triggers: frozenset[str]


def authorizer(
    catalog: Catalog, objects: Objects, ctes: frozenset[str], *, write: bool
) -> Callable[[int, str | None, str | None, str | None, str | None], int]:
    """L'authorizer d'un appel : les droits de la config, rien d'autre."""
    rights = {table.lower(): frozenset(granted) for table, granted in catalog.rights.items()}

    def allowed(table: str | None, right: SqlRight) -> bool:
        return right in rights.get((table or "").lower(), frozenset())

    # Tables lues par une vue permise : ``count(*)`` sur la vue les lit ensuite d'un bloc,
    # sans origine (``READ(table, '', 'main', None)``, essayé le 26/10).
    through_views: set[str] = set()

    def decide(
        action: int, arg1: str | None, arg2: str | None, db: str | None, origin: str | None
    ) -> int:
        if action in _ALWAYS:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_FUNCTION:
            return sqlite3.SQLITE_DENY if is_forbidden(arg2 or "") else sqlite3.SQLITE_OK
        source = (origin or "").lower()
        if action == sqlite3.SQLITE_READ:
            if source in objects.triggers:
                return sqlite3.SQLITE_OK
            if source in objects.views and allowed(source, "select"):
                through_views.add((arg1 or "").lower())
                return sqlite3.SQLITE_OK
            if (
                origin is None
                and db == "main"
                and arg2 == ""
                and (arg1 or "").lower() in through_views
            ):
                return sqlite3.SQLITE_OK
            if db is None and (arg1 or "").lower() in ctes:
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_OK if allowed(arg1, "select") else sqlite3.SQLITE_DENY
        right = _WRITES.get(action)
        if right is None or not write:
            return sqlite3.SQLITE_DENY
        if source in objects.triggers:
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_OK if allowed(arg1, right) else sqlite3.SQLITE_DENY

    return decide


class SqliteDatabase:
    """Une base SQLite de la config, montée : son fichier et ses droits."""

    def __init__(self, spec: DatabaseSpec, path: Path, catalog: Catalog, zone: ZoneInfo) -> None:
        self.spec = spec
        self.path = path
        self.catalog = catalog
        self.zone = zone

    def where(self) -> Place:
        return Place(name="", kind="sqlite", path=self.path)

    async def query(self, unit: Statement, analysis: Analysis) -> dict[str, JsonValue]:
        def work(conn: sqlite3.Connection) -> dict[str, JsonValue]:
            conn.execute("BEGIN")
            try:
                cursor = conn.execute(unit.sql, _bindings(unit.params))
                columns = [d[0] for d in cursor.description or ()]
                fetched = cursor.fetchmany(self.spec.max_rows + 1)
            finally:
                conn.execute("ROLLBACK")
            truncated = len(fetched) > self.spec.max_rows
            rows: list[JsonValue] = [row(r, self.zone) for r in fetched[: self.spec.max_rows]]
            return {
                "database": self.spec.name,
                "columns": list[JsonValue](columns),
                "rows": rows,
                "row_count": len(rows),
                "truncated": truncated,
            }

        return await self._run(work, analysis.ctes, write=False)

    async def execute(self, units: list[tuple[Statement, Analysis]]) -> dict[str, JsonValue]:
        ctes = frozenset(name for _, analysis in units for name in analysis.ctes)

        def work(conn: sqlite3.Connection) -> dict[str, JsonValue]:
            conn.execute("BEGIN IMMEDIATE")
            results: list[JsonValue] = []
            affected = 0
            try:
                for position, (unit, _) in enumerate(units, start=1):
                    try:
                        cursor = conn.execute(unit.sql, _bindings(unit.params))
                        returned = cursor.fetchall() if cursor.description else None
                    except sqlite3.Error as exc:
                        raise _Failed(position, exc) from exc
                    count = cursor.rowcount if cursor.rowcount >= 0 else 0
                    affected += count
                    _check_affected(affected, self.spec.max_affected_rows)
                    result: dict[str, JsonValue] = {"affected_rows": count}
                    if returned is not None and cursor.description:
                        result["columns"] = [d[0] for d in cursor.description]
                        result["returning"] = [
                            row(r, self.zone) for r in returned[: self.spec.max_rows]
                        ]
                        if len(returned) > self.spec.max_rows:
                            result["truncated"] = True
                    results.append(result)
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
            return {
                "database": self.spec.name,
                "results": results,
                "affected_rows": affected,
                "committed": True,
            }

        return await self._run(work, ctes, write=True)

    async def introspect(self) -> list[TableInfo]:
        def read() -> list[TableInfo]:
            # Sans authorizer : c'est l'outil qui lit le catalogue, pas le modèle.
            conn = sqlite3.connect(self._uri(write=False), uri=True, autocommit=True)
            try:
                found: list[TableInfo] = []
                declared = {t.lower(): t for t in self.catalog.rights}
                rows = conn.execute(
                    "SELECT type, name, sql FROM sqlite_schema "
                    "WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%' ORDER BY name"
                ).fetchall()
                for kind, name, ddl in rows:
                    key = declared.get(str(name).lower())
                    if key is None:
                        continue
                    info = conn.execute(f"PRAGMA table_info({_quoted(str(name))})").fetchall()
                    columns = tuple(
                        Column(name=str(c[1]), type=str(c[2] or ""), not_null=bool(c[3]))
                        for c in info
                    )
                    found.append(
                        TableInfo(
                            name=key,
                            kind="view" if kind == "view" else "table",
                            columns=columns,
                            ddl=str(ddl or ""),
                        )
                    )
                return found
            finally:
                conn.close()

        try:
            return await asyncio.to_thread(read)
        except sqlite3.Error as exc:
            raise ToolError(f"Base {self.spec.name} : {exc}.") from exc

    async def aclose(self) -> None:
        return None

    # --- Connexion ------------------------------------------------------------------

    def _uri(self, *, write: bool) -> str:
        return f"file:{quote(str(self.path))}?mode={'rw' if write else 'ro'}"

    def _connect(self, ctes: frozenset[str], *, write: bool) -> sqlite3.Connection:
        timeout = self.spec.statement_timeout
        conn = sqlite3.connect(self._uri(write=write), uri=True, autocommit=True, timeout=timeout)
        try:
            conn.execute("PRAGMA trusted_schema = OFF")
            conn.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
            listed = conn.execute(
                "SELECT type, lower(name) FROM sqlite_schema WHERE type IN ('view', 'trigger')"
            ).fetchall()
            objects = Objects(
                views=frozenset(name for kind, name in listed if kind == "view"),
                triggers=frozenset(name for kind, name in listed if kind == "trigger"),
            )
            deadline = time.monotonic() + timeout
            conn.set_progress_handler(lambda: int(time.monotonic() > deadline), _PROGRESS_STEPS)
            conn.set_authorizer(authorizer(self.catalog, objects, ctes, write=write))
        except BaseException:
            conn.close()
            raise
        return conn

    async def _run[T](
        self, work: Callable[[sqlite3.Connection], T], ctes: frozenset[str], *, write: bool
    ) -> T:
        holder: list[sqlite3.Connection] = []

        def run() -> T:
            conn = self._connect(ctes, write=write)
            holder.append(conn)
            try:
                return work(conn)
            finally:
                conn.close()

        task = asyncio.ensure_future(asyncio.to_thread(run))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # Le thread ne s'annule pas : on interrompt l'instruction, il annule la
            # transaction et ferme la connexion de lui-même.
            if holder:
                holder[0].interrupt()
            raise
        except _Failed as exc:
            raise ToolError(
                f"Instruction {exc.position} : {_said(exc.error, self.spec.statement_timeout)} "
                "Rien n'est enregistré."
            ) from exc
        except _TooMany as exc:
            raise ToolError(str(exc)) from exc
        except sqlite3.Error as exc:
            raise ToolError(_said(exc, self.spec.statement_timeout)) from exc


class _Failed(Exception):
    def __init__(self, position: int, error: sqlite3.Error) -> None:
        super().__init__(str(error))
        self.position = position
        self.error = error


class _TooMany(Exception):
    pass


def _check_affected(affected: int, limit: int) -> None:
    if affected > limit:
        raise _TooMany(
            f"{affected} lignes modifiées, au-delà de {limit} : rien n'est enregistré "
            "(transaction annulée). Restreindre le WHERE, ou procéder par lots."
        )


def _said(exc: sqlite3.Error, timeout: float) -> str:
    text = str(exc)
    if text == "interrupted":
        return f"Instruction arrêtée après {timeout:g} s (délai de la base)."
    if text in ("not authorized", "prohibited") or text.startswith("access to "):
        return f"Accès refusé par la base ({text})."
    if text.startswith("You did not supply a value") or "binding" in text.lower():
        return f"Paramètres : {text} (écrire $1, $2…)."
    return f"Erreur SQLite : {text}."


def _bindings(params: tuple[ParamValue, ...]) -> dict[str, object]:
    """``$1…$n`` sont des paramètres nommés pour SQLite : « 1 », « 2 »…"""
    bound: dict[str, object] = {}
    for index, value in enumerate(params, start=1):
        match value:
            case bool():
                bound[str(index)] = int(value)
            case list() | dict():
                bound[str(index)] = json.dumps(value, ensure_ascii=False)
            case _:
                bound[str(index)] = value
    return bound


def _quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'
