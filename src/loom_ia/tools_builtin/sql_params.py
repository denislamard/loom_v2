# SPDX-License-Identifier: Apache-2.0
"""Paramètres de ``loom.sql`` : les bases permises, et pour chacune ses tables et leurs droits.

Module léger — Pydantic seulement — : la config l'importe pour valider la
référence au chargement, sans importer l'outil, ni sqlglot, ni asyncpg.

    - python: loom.sql
      timezone: Europe/Paris
      databases:
      - name: crm
        postgres: { dsn_env: CRM_DATABASE_URL, schema: public }
        tables:
          devis:    { select: never, update: always }
          clients:  [ select ]
          relances: [ select, insert ]     # forme courte : approbations par défaut
        describe:
          devis.statut: "brouillon | envoye | relance | accepte | refuse"
      - name: stock
        sqlite: { path: data/stock.db }
        tables:
          articles: [ select ]

``tables`` donne, pour chaque table (ou vue), une table **droit →
approbation**, comme les dossiers de ``loom.local_files`` : un droit présent
est accordé, et sa valeur dit si chaque appel qui l'exerce attend un humain.
En liste, ``select`` et ``insert`` sont à ``never``, ``update`` et ``delete``
à ``always``. ``update`` et ``delete`` exigent ``select`` : la base lit les
colonnes de leur ``WHERE``.

Le DSN d'une base Postgres ne vient jamais de la config : elle nomme la
variable qui le porte (``dsn_env``), comme ``api_key_env`` pour un modèle.
"""

from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Final, Literal, Self, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PositiveFloat,
    WithJsonSchema,
    model_validator,
)

type SqlRight = Literal["select", "insert", "update", "delete"]
type RightApproval = Literal["never", "always"]

# Dans l'ordre où on les dit.
SQL_RIGHTS: Final[tuple[SqlRight, ...]] = ("select", "insert", "update", "delete")
DEFAULT_APPROVAL: Final[Mapping[SqlRight, RightApproval]] = {
    "select": "never",
    "insert": "never",
    "update": "always",
    "delete": "always",
}
DATABASE_NAME_PATTERN: Final = r"^[a-z0-9][a-z0-9_-]{0,31}$"
# Un identifiant SQL simple : lettres, chiffres, « _ », « $ ». Une majuscule veut
# dire un nom créé entre guillemets (« "Devis" ») : il se compare tel quel.
IDENTIFIER_PATTERN: Final = r"^[A-Za-z_][A-Za-z0-9_$]{0,62}$"
ENV_NAME_PATTERN: Final = r"^[A-Za-z_][A-Za-z0-9_]{0,127}$"
DEFAULT_TIMEZONE: Final = "Europe/Paris"
DEFAULT_STATEMENT_TIMEOUT: Final = 10.0
DEFAULT_MAX_ROWS: Final = 200
DEFAULT_MAX_AFFECTED_ROWS: Final = 100
MAX_ROWS_CEILING: Final = 1000


def _short_form(value: object) -> object:
    """Une liste de droits devient la table, chacun avec son approbation par défaut."""
    if not isinstance(value, list | tuple):
        return value
    given = [str(item) for item in cast("list[object] | tuple[object, ...]", value)]
    doubles = sorted({right for right in given if given.count(right) > 1})
    if doubles:
        raise ValueError(f"droit donné deux fois : {', '.join(doubles)}")
    # Un droit inconnu garde une valeur quelconque : la clé échoue ensuite, nommée.
    return {right: DEFAULT_APPROVAL.get(cast(SqlRight, right), "never") for right in given}


_RIGHTS_SCHEMA: Final = {
    "description": (
        "Droits accordés sur la table : table droit → approbation (never ou always), "
        "ou liste de droits avec leur approbation par défaut (select et insert : never ; "
        "update et delete : always)."
    ),
    "anyOf": [
        {
            "type": "object",
            "propertyNames": {"enum": list(SQL_RIGHTS)},
            "additionalProperties": {"enum": ["never", "always"]},
            "minProperties": 1,
        },
        {
            "type": "array",
            "items": {"enum": list(SQL_RIGHTS)},
            "uniqueItems": True,
            "minItems": 1,
        },
    ],
}

type TableRights = Annotated[
    dict[SqlRight, RightApproval],
    BeforeValidator(_short_form),
    WithJsonSchema(_RIGHTS_SCHEMA),
]


class PostgresSpec(BaseModel):
    """Une base Postgres : la variable de son DSN, et le schéma où sont ses tables."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    dsn_env: str = Field(pattern=ENV_NAME_PATTERN)
    # Posé en ``search_path`` à chaque transaction : les tables se nomment sans schéma.
    schema_name: str = Field(default="public", alias="schema", pattern=IDENTIFIER_PATTERN)


class SqliteSpec(BaseModel):
    """Une base SQLite : son fichier, qui doit exister (l'outil n'en crée jamais)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # Absolu, ou relatif au dossier du fichier de config. Ni « ~ » ni variable.
    path: Path

    @model_validator(mode="after")
    def _check(self) -> Self:
        said = str(self.path)
        if said.startswith("~") or "$" in said:
            raise ValueError(
                f"{said!r} — ni « ~ » ni variable ($HOME…) dans 'path', qui n'est pas "
                "interprété : donner un chemin absolu, ou relatif au fichier de config"
            )
        return self


class DatabaseSpec(BaseModel):
    """Une base permise : son nom pour le modèle, sa connexion, ses tables et leurs droits."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(pattern=DATABASE_NAME_PATTERN)
    postgres: PostgresSpec | None = None
    sqlite: SqliteSpec | None = None
    tables: dict[Annotated[str, Field(pattern=IDENTIFIER_PATTERN)], TableRights]
    # Ce que la description de l'outil dit d'une table (« devis ») ou d'une colonne
    # (« devis.statut ») : le sens métier, les valeurs permises…
    describe: dict[str, Annotated[str, Field(min_length=1, max_length=500)]] = Field(
        default_factory=dict[str, str]
    )
    # Fonctions que sqlglot ne connaît pas, permises quand même (age, clock_timestamp,
    # jsonb_build_object, une fonction à soi…). Les autres inconnues sont refusées.
    functions: tuple[Annotated[str, Field(pattern=IDENTIFIER_PATTERN)], ...] = ()
    # Délai d'une instruction, côté base (statement_timeout, ou l'interruption SQLite).
    statement_timeout: Annotated[PositiveFloat, Field(le=300)] = DEFAULT_STATEMENT_TIMEOUT
    # Lignes rendues au modèle par une lecture ; au-delà, ``truncated``.
    max_rows: Annotated[int, Field(ge=1, le=MAX_ROWS_CEILING)] = DEFAULT_MAX_ROWS
    # Lignes qu'un ``execute`` peut modifier en tout ; au-delà, rien n'est enregistré.
    max_affected_rows: Annotated[int, Field(ge=1, le=100_000)] = DEFAULT_MAX_AFFECTED_ROWS
    # UPDATE et DELETE sans WHERE : refusés, sauf si l'on veut vraiment les permettre.
    allow_full_table: bool = False

    @model_validator(mode="after")
    def _check(self) -> Self:
        if (self.postgres is None) == (self.sqlite is None):
            raise ValueError(f"base {self.name!r} : donner 'postgres' ou 'sqlite', et un seul")
        if not self.tables:
            raise ValueError(f"base {self.name!r} : 'tables' ne peut pas être vide")
        for table, rights in self.tables.items():
            if not rights:
                raise ValueError(f"base {self.name!r}, table {table!r} : aucun droit donné")
            needing = [right for right in ("update", "delete") if right in rights]
            if needing and "select" not in rights:
                raise ValueError(
                    f"base {self.name!r}, table {table!r} : {', '.join(needing)} exige "
                    "'select' (la base lit les colonnes du WHERE)"
                )
        if self.sqlite is not None:
            folded = [table.lower() for table in self.tables]
            doubles = sorted({t for t in folded if folded.count(t) > 1})
            if doubles:
                raise ValueError(
                    f"base {self.name!r} : tables qui ne diffèrent que par la casse "
                    f"({', '.join(doubles)}) — SQLite ne les distingue pas"
                )
        for key in self.describe:
            table = key.split(".", 1)[0]
            if table not in self.tables:
                raise ValueError(
                    f"base {self.name!r} : describe {key!r} — table {table!r} non déclarée"
                )
        lowered = [name.lower() for name in self.functions]
        doubles = sorted({f for f in lowered if lowered.count(f) > 1})
        if doubles:
            raise ValueError(f"base {self.name!r} : fonction donnée deux fois : {doubles}")
        return self

    @property
    def dialect(self) -> Literal["postgres", "sqlite"]:
        return "postgres" if self.postgres is not None else "sqlite"

    @property
    def writes(self) -> bool:
        """Vrai si une table au moins accorde une écriture."""
        return any(set(rights) - {"select"} for rights in self.tables.values())


def _databases(databases: tuple[DatabaseSpec, ...]) -> tuple[DatabaseSpec, ...]:
    if not databases:
        raise ValueError("au moins une base est attendue")
    names = [database.name for database in databases]
    doubles = sorted({name for name in names if names.count(name) > 1})
    if doubles:
        raise ValueError(f"nom de base donné deux fois : {', '.join(doubles)}")
    return databases


class SqlParams(BaseModel):
    """Paramètres de ``loom.sql``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    databases: Annotated[
        tuple[DatabaseSpec, ...],
        AfterValidator(_databases),
        Field(json_schema_extra={"minItems": 1}),
    ]
    # Fuseau IANA où passent les dates-heures rendues au modèle, et où se lit une
    # date-heure sans fuseau donnée en paramètre d'une colonne ``timestamptz``.
    timezone: str = DEFAULT_TIMEZONE

    @model_validator(mode="after")
    def _check(self) -> Self:
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError, ValueError:
            raise ValueError(
                f"fuseau inconnu : {self.timezone!r} — un nom IANA, par exemple Europe/Paris"
            ) from None
        return self

    def secret_names(self) -> tuple[str, ...]:
        """Les variables que l'outil lit : un client peut les rediriger (``secrets``)."""
        return tuple(
            dict.fromkeys(db.postgres.dsn_env for db in self.databases if db.postgres is not None)
        )
