# SPDX-License-Identifier: Apache-2.0
"""Ce que le modèle envoie à ``sql`` : l'action, la base, et le SQL avec ses paramètres."""

from collections.abc import Mapping
from typing import Annotated, Final, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, model_validator

from loom_ia.core.ports import ToolError

NAME: Final = "sql"
MAX_STATEMENTS: Final = 20
MAX_SQL_CHARS: Final = 20_000
MAX_PARAMS: Final = 100

type Action = Literal["schema", "query", "execute"]

# Une valeur de paramètre : un scalaire JSON, ou une liste de scalaires (tableau Postgres).
type Scalar = str | int | float | bool | None
type ParamValue = Scalar | list[Scalar] | dict[str, JsonValue]


class Statement(BaseModel):
    """Une instruction et ses paramètres ($1, $2… dans l'ordre de la liste)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sql: Annotated[str, Field(min_length=1, max_length=MAX_SQL_CHARS)]
    params: Annotated[
        tuple[ParamValue, ...],
        Field(max_length=MAX_PARAMS, description="Valeurs de $1, $2… dans l'ordre."),
    ] = ()


class SqlArguments(BaseModel):
    """Arguments d'un appel : ceux que voit le modèle."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action: Annotated[
        Action,
        Field(
            description="schema : structure des tables ; query : une lecture (SELECT) ; "
            "execute : des écritures (INSERT, UPDATE, DELETE) dans une même transaction."
        ),
    ]
    database: Annotated[str, Field(description="Nom de la base, parmi celles décrites.")]
    sql: Annotated[
        str | None, Field(max_length=MAX_SQL_CHARS, description="Pour query : le SELECT.")
    ] = None
    params: Annotated[
        tuple[ParamValue, ...] | None,
        Field(max_length=MAX_PARAMS, description="Pour query : valeurs de $1, $2…"),
    ] = None
    statements: Annotated[
        tuple[Statement, ...] | None,
        Field(
            min_length=1,
            max_length=MAX_STATEMENTS,
            description="Pour execute : les instructions, appliquées ensemble ou pas du tout.",
        ),
    ] = None
    tables: Annotated[
        tuple[str, ...] | None,
        Field(description="Pour schema : les tables voulues ; toutes si absent."),
    ] = None

    @model_validator(mode="after")
    def _fields_of_the_action(self) -> SqlArguments:
        said = {
            "sql": self.sql is not None,
            "params": self.params is not None,
            "statements": self.statements is not None,
            "tables": self.tables is not None,
        }
        allowed = {
            "schema": {"tables"},
            "query": {"sql", "params"},
            "execute": {"statements"},
        }[self.action]
        stray = sorted(name for name, given in said.items() if given and name not in allowed)
        if stray:
            raise ValueError(f"{', '.join(stray)} ne sert pas à {self.action}")
        if self.action == "query" and not (self.sql or "").strip():
            raise ValueError("query attend 'sql', le SELECT")
        if self.action == "execute" and self.statements is None:
            raise ValueError("execute attend 'statements' : [{sql, params}, …]")
        return self

    def units(self) -> tuple[Statement, ...]:
        """Les instructions de l'appel : celle de ``query``, ou celles d'``execute``."""
        if self.action == "query":
            return (Statement(sql=cast(str, self.sql), params=self.params or ()),)
        return self.statements or ()


def parse(arguments: Mapping[str, JsonValue]) -> SqlArguments:
    try:
        return SqlArguments.model_validate(dict(arguments))
    except ValidationError as exc:
        lines = ["Arguments invalides :"]
        for item in exc.errors(include_url=False):
            location = ".".join(str(part) for part in item["loc"]) or "(racine)"
            lines.append(f"- {location} : {item['msg'].removeprefix('Value error, ')}")
        raise ToolError("\n".join(lines)) from None


def input_schema() -> dict[str, JsonValue]:
    schema = SqlArguments.model_json_schema()
    _untitled(schema)
    return cast(dict[str, JsonValue], schema)


def _untitled(node: object) -> None:
    """Retire les titres que Pydantic ajoute : ils répètent les noms, en tokens."""
    if isinstance(node, dict):
        mapping = cast(dict[str, object], node)
        title = mapping.get("title")
        if isinstance(title, str):
            del mapping["title"]
        for value in mapping.values():
            _untitled(value)
    elif isinstance(node, list):
        for value in cast(list[object], node):
            _untitled(value)
