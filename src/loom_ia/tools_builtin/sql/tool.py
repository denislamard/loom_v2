# SPDX-License-Identifier: Apache-2.0
"""L'outil ``sql`` monté : ses bases, leurs droits ; et sa fabrique."""

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

from pydantic import BaseModel, JsonValue

from loom_ia.core.model import SideEffects, ToolOutput, ToolSpec
from loom_ia.core.ports import CallAssessment, ToolContext, ToolError
from loom_ia.tools import to_output
from loom_ia.tools_builtin import BuiltinContext, Protected
from loom_ia.tools_builtin.sql.analysis import Analysis, Catalog, analyze
from loom_ia.tools_builtin.sql.arguments import (
    MAX_STATEMENTS,
    NAME,
    SqlArguments,
    Statement,
    input_schema,
    parse,
)
from loom_ia.tools_builtin.sql.schema import SchemaCache, TableInfo, close_columns, render
from loom_ia.tools_builtin.sql.sqlite import SqliteDatabase
from loom_ia.tools_builtin.sql_params import SQL_RIGHTS, DatabaseSpec, SqlParams, SqlRight


class Backend(Protocol):
    """Ce que l'outil demande à une base, Postgres ou SQLite."""

    def location(self) -> str: ...

    async def query(self, unit: Statement, analysis: Analysis) -> dict[str, JsonValue]: ...

    async def execute(self, units: list[tuple[Statement, Analysis]]) -> dict[str, JsonValue]: ...

    async def introspect(self) -> list[TableInfo]: ...

    async def aclose(self) -> None: ...


@dataclass(frozen=True, slots=True)
class Database:
    """Une base montée : sa déclaration, ses droits pour l'analyse, sa connexion, son schéma."""

    spec: DatabaseSpec
    catalog: Catalog
    backend: Backend
    schema: SchemaCache

    @property
    def name(self) -> str:
        return self.spec.name

    def asking(self, table: str) -> list[SqlRight]:
        return [r for r in SQL_RIGHTS if self.spec.tables[table].get(r) == "always"]

    def granted(self, table: str) -> str:
        """« select, update (approbation) »."""
        rights = self.spec.tables[table]
        return ", ".join(
            f"{right}{' (approbation)' if rights[right] == 'always' else ''}"
            for right in SQL_RIGHTS
            if right in rights
        )


class SqlTool:
    """L'outil ``sql`` monté avec ses bases."""

    def __init__(self, databases: Sequence[Database], zone: ZoneInfo) -> None:
        if not databases:
            raise ValueError("au moins une base est attendue")
        self._databases = {database.name: database for database in databases}
        self._zone = zone
        rights = {right for db in databases for table in db.spec.tables.values() for right in table}
        effects: SideEffects = (
            "irreversible"
            if rights & {"update", "delete"}
            else "reversible"
            if "insert" in rights
            else "none"
        )
        asking = any(db.asking(table) for db in databases for table in db.spec.tables)
        self._spec = ToolSpec(
            name=NAME,
            description=_description(databases, zone),
            input_schema=input_schema(),
            kind="python",
            # Le pire des cas, pour ce qui ne regarde pas l'appel lui-même ; chaque
            # appel est évalué à part (``assess_call``).
            side_effects=effects,
            approval="always" if asking else "never",
            # Une écriture interrompue a peut-être été validée : un humain vérifie
            # avant de la refaire. Une lecture est toujours sûre à refaire.
            on_unknown="pause" if effects != "none" else "error",
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def settings_lines(self) -> tuple[str, ...]:
        """Une ligne par base, puis les limites et le fuseau, pour ``loom validate``."""
        lines: list[str] = []
        for db in self._databases.values():
            tables = " ; ".join(f"{t} {db.granted(t)}" for t in db.spec.tables)
            lines.append(f"{db.name} → {db.backend.location()} : {tables}")
            lines.append(
                f"{db.name} : {db.spec.max_rows} lignes lues, {db.spec.max_affected_rows} "
                f"modifiées au plus ; {db.spec.statement_timeout:g} s par instruction"
                + (" ; UPDATE/DELETE sans WHERE permis" if db.spec.allow_full_table else "")
                + (
                    f" ; fonctions permises : {', '.join(db.spec.functions)}"
                    if db.spec.functions
                    else ""
                )
            )
        lines.append(f"fuseau : {self._zone.key}")
        return tuple(lines)

    def assess_call(self, arguments: Mapping[str, JsonValue]) -> CallAssessment:
        """Approbation et effets de cet appel : les droits qu'il exerce, table par table."""
        try:
            request = parse(arguments)
            database = self._database(request.database)
            analyses = self._analyses(request, database)
        except ToolError:
            # L'appel sera refusé à l'exécution, pour la même raison : aucun effet.
            return CallAssessment(side_effects="none")
        exercised = list(dict.fromkeys(pair for a in analyses for pair in a.exercised()))
        operations = {a.operation for a in analyses}
        effects: SideEffects = (
            "irreversible"
            if operations & {"update", "delete"}
            else "reversible"
            if "insert" in operations
            else "none"
        )
        asking = [
            f"{right} sur {table}"
            for table, right in exercised
            if database.spec.tables[table].get(right) == "always"
        ]
        if not asking:
            return CallAssessment(side_effects=effects)
        done = ", ".join(f"{right} sur {table}" for table, right in exercised if right != "select")
        return CallAssessment(
            approval="always",
            side_effects=effects,
            reason=f"{database.name} : {done or 'lecture'} — approbation demandée pour "
            + ", ".join(asking),
        )

    async def invoke(self, arguments: dict[str, JsonValue], context: ToolContext) -> ToolOutput:
        request = parse(arguments)
        database = self._database(request.database)
        if request.action == "schema":
            return ToolOutput.text(await self._schema(database, request.tables))
        analyses = self._analyses(request, database)
        units = list(zip(request.units(), analyses, strict=True))
        try:
            if request.action == "query":
                unit, analysis = units[0]
                result = await database.backend.query(unit, analysis)
            else:
                result = await database.backend.execute(units)
        except ToolError as exc:
            hint = await self._hint(database, exc.message, analyses)
            if hint is None:
                raise
            raise ToolError(f"{exc.message} {hint}") from None
        return to_output(result)

    async def aclose(self) -> None:
        for database in self._databases.values():
            await database.backend.aclose()

    # --- Appels ---------------------------------------------------------------------

    def _database(self, name: str) -> Database:
        found = self._databases.get(name)
        if found is None:
            raise ToolError(
                f"Base « {name} » inconnue. Bases permises : {', '.join(self._databases)}."
            )
        return found

    def _analyses(self, request: SqlArguments, database: Database) -> list[Analysis]:
        if request.action == "schema":
            return []
        units = request.units()
        many = len(units) > 1
        return [
            analyze(
                unit.sql,
                len(unit.params),
                database.catalog,
                write=request.action == "execute",
                position=f"Instruction {index} : " if many else "",
            )
            for index, unit in enumerate(units, start=1)
        ]

    async def _schema(self, database: Database, wanted: Sequence[str] | None) -> str:
        tables = await database.schema.tables()
        named: list[str] | None = None
        if wanted is not None:
            named = []
            for name in wanted:
                declared = database.catalog.declared(name, quoted=name != name.lower())
                named.append(declared or name)
        return render(
            tables,
            database.spec.tables,
            database.spec.describe,
            database=database.name,
            dialect=database.spec.dialect,
            wanted=named,
        )

    async def _hint(self, database: Database, message: str, analyses: list[Analysis]) -> str | None:
        """Les colonnes proches d'une colonne inconnue, d'après le schéma (lu au besoin)."""
        if "column" not in message:
            return None
        try:
            tables = await database.schema.tables()
        except ToolError:
            return None
        within = {table for analysis in analyses for table in analysis.needs}
        return close_columns(message, tables, within)


def _description(databases: Sequence[Database], zone: ZoneInfo) -> str:
    lines = [
        "Interroge et modifie des bases SQL permises, avec le SQL que vous écrivez.",
        "Bases, tables et droits (« approbation » : un humain valide chaque appel qui "
        "exerce ce droit) :",
    ]
    for db in databases:
        spec = db.spec
        lines.append(
            f"- {db.name} ({spec.dialect} ; {spec.max_rows} lignes lues, "
            f"{spec.max_affected_rows} modifiées au plus) : "
            + " ; ".join(f"{table} ({db.granted(table)})" for table in spec.tables)
        )
        for key, text in spec.describe.items():
            lines.append(f"  · {key} : {text}")
        if spec.functions:
            lines.append(f"  · fonctions permises en plus : {', '.join(spec.functions)}")
    lines += [
        "Actions : schema (colonnes, types, clés et contraintes des tables — à lire avant "
        "d'écrire du SQL) ; query (un SELECT, dans 'sql') ; execute (dans 'statements', au "
        f"plus {MAX_STATEMENTS} INSERT, UPDATE ou DELETE, appliqués ensemble ou pas du tout ; "
        "au-delà de la limite de lignes modifiées, rien n'est enregistré).",
        "Règles : une instruction par entrée ; tables nommées sans schéma devant ; valeurs en "
        "paramètres $1, $2… données dans 'params', jamais collées dans le SQL ; UPDATE et "
        "DELETE ont un WHERE ; ni DDL (CREATE, ALTER, DROP…), ni TRUNCATE, MERGE, LOCK, SET, "
        "COPY, ni FOR UPDATE ; les fonctions inconnues de l'outil sont refusées.",
        f"Valeurs rendues : numeric en texte exact (« 1250.50 »), dates-heures en ISO 8601 "
        f"en heure de {zone.key}, binaires non rendus. Résultat d'un query : columns, rows "
        "(listes), row_count, truncated.",
    ]
    return "\n".join(lines)


def build(params: BaseModel, context: BuiltinContext) -> SqlTool:
    """La fabrique de ``loom.sql`` : vérifie les bases sans s'y connecter."""
    if not isinstance(params, SqlParams):
        raise TypeError(f"paramètres de {NAME} attendus, reçu {type(params).__name__}")
    zone = ZoneInfo(params.timezone)
    secrets: Mapping[str, str] = context.secrets if context.secrets is not None else os.environ
    databases: list[Database] = []
    for spec in params.databases:
        catalog = Catalog.of(spec)
        backend: Backend
        if spec.sqlite is not None:
            backend = SqliteDatabase(spec, _sqlite_path(spec, context), catalog, zone)
        else:
            # Lot 2 : le DSN est vérifié, mais la connexion Postgres n'arrive qu'au lot 3.
            _dsn(spec, secrets, context)
            raise ValueError(
                f"base {spec.name!r} : Postgres n'est pas encore pris en charge par loom.sql"
            )
        databases.append(Database(spec, catalog, backend, SchemaCache(backend.introspect)))
    return SqlTool(databases, zone)


def _sqlite_path(spec: DatabaseSpec, context: BuiltinContext) -> Path:
    assert spec.sqlite is not None
    given = spec.sqlite.path
    if not given.is_absolute() and context.base_dir is not None:
        given = context.base_dir / given
    path = given.resolve()
    if not path.is_file():
        raise ValueError(
            f"base {spec.name!r} : {path} n'existe pas ou n'est pas un fichier — loom.sql "
            "n'en crée pas"
        )
    problem = _exposes(path, context.protected)
    if problem is not None:
        raise ValueError(
            f"base {spec.name!r} : {path} {problem} — le modèle pourrait y réécrire ce que "
            "loom a journalisé ; exposer une autre base"
        )
    return path


def _exposes(path: Path, protected: Sequence[Protected]) -> str | None:
    for item in protected:
        guarded = item.path.resolve()
        if guarded == path:
            return f"est {item.what}"
        if item.subtree and path.is_relative_to(guarded):
            return f"est dans {item.what} ({guarded})"
    return None


def _dsn(spec: DatabaseSpec, secrets: Mapping[str, str], context: BuiltinContext) -> str:
    assert spec.postgres is not None
    env = spec.postgres.dsn_env
    if env in context.protected_dsns:
        raise ValueError(
            f"base {spec.name!r} : {env} est le DSN du stockage de loom — le modèle pourrait "
            "y réécrire son journal ; donner la base de l'application, sous un rôle dédié"
        )
    value = secrets.get(env, "")
    if not value:
        raise ValueError(f"base {spec.name!r} : la variable {env} (dsn_env) est absente ou vide")
    for guarded in context.protected_dsns:
        # Le stockage lit son DSN dans l'environnement du process, pas dans la table du client.
        if value in (secrets.get(guarded), os.environ.get(guarded)):
            raise ValueError(
                f"base {spec.name!r} : {env} porte le même DSN que {guarded}, celui du "
                "stockage de loom — donner la base de l'application, sous un rôle dédié"
            )
    return value
