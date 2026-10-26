# SPDX-License-Identifier: Apache-2.0
"""Le schéma que voit le modèle : celui des tables permises, en DDL, avec leurs droits.

Les LLM lisent mieux un ``CREATE TABLE`` qu'un JSON de colonnes, et il dit
d'un coup types, clés, contraintes et commentaires. Seules les tables
déclarées y sont ; chacune porte ses droits, et ce que la config en dit
(``describe``).

Le schéma est lu dans la base par l'outil (jamais par le modèle) et gardé
``CACHE_SECONDS`` : l'agent ne peut pas le changer, seul un tiers le peut.
Il sert aussi à proposer les colonnes proches d'un nom inconnu.
"""

import difflib
import re
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Final, Literal

from loom_ia.tools_builtin.sql_params import RightApproval, SqlRight

CACHE_SECONDS: Final = 300.0


@dataclass(frozen=True, slots=True)
class Column:
    name: str
    type: str
    not_null: bool = False
    default: str | None = None
    comment: str | None = None


@dataclass(frozen=True, slots=True)
class TableInfo:
    """Une table ou une vue lue dans la base."""

    name: str
    kind: Literal["table", "view"]
    columns: tuple[Column, ...]
    # Clés, unicité, CHECK, clés étrangères vers une table permise (Postgres).
    constraints: tuple[str, ...] = ()
    comment: str | None = None
    # Le DDL d'origine, quand la base le garde (SQLite) : rendu tel quel.
    ddl: str | None = None


def render(
    tables: Iterable[TableInfo],
    rights: Mapping[str, Mapping[SqlRight, RightApproval]],
    describe: Mapping[str, str],
    *,
    database: str,
    dialect: str,
    wanted: Iterable[str] | None = None,
) -> str:
    """Le DDL des tables permises (``wanted`` : celles-là seulement), avec leurs droits."""
    found = {table.name: table for table in tables}
    names = list(rights) if wanted is None else list(dict.fromkeys(wanted))
    blocks = [f"-- Base « {database} » ({dialect}) : tables permises et leurs droits."]
    for name in names:
        granted = rights.get(name)
        if granted is None:
            blocks.append(f"-- « {name} » : table non autorisée ({', '.join(rights)}).")
            continue
        said = ", ".join(
            f"{right.upper()}{' (approbation humaine)' if approval == 'always' else ''}"
            for right, approval in granted.items()
        )
        lines = [f"-- droits : {said}"]
        if name in describe:
            lines.append(f"-- {describe[name]}")
        for key, text in describe.items():
            if key.startswith(f"{name}."):
                lines.append(f"-- {key} : {text}")
        table = found.get(name)
        if table is None:
            lines.append(f"-- « {name} » est déclarée, mais absente de la base.")
        elif table.ddl:
            lines.append(table.ddl.strip().rstrip(";") + ";")
        else:
            lines.append(_ddl(table))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _ddl(table: TableInfo) -> str:
    keyword = "VIEW" if table.kind == "view" else "TABLE"
    head = f"CREATE {keyword} {table.name} ("
    if table.comment:
        head += f"  -- {table.comment}"
    entries: list[tuple[str, str | None]] = []
    for column in table.columns:
        text = f"  {column.name} {column.type}"
        if column.not_null:
            text += " NOT NULL"
        if column.default is not None:
            text += f" DEFAULT {column.default}"
        entries.append((text, column.comment))
    entries += [(f"  {constraint}", None) for constraint in table.constraints]
    rendered: list[str] = []
    for index, (text, comment) in enumerate(entries):
        separator = "," if index < len(entries) - 1 else ""
        rendered.append(f"{text}{separator}" + (f"  -- {comment}" if comment else ""))
    return "\n".join([head, *rendered, ");"])


@dataclass(slots=True)
class SchemaCache:
    """Le schéma lu, gardé ``CACHE_SECONDS``."""

    read: Callable[[], Awaitable[list[TableInfo]]]
    clock: Callable[[], float] = time.monotonic
    _tables: list[TableInfo] | None = None
    _at: float = field(default=0.0)

    async def tables(self) -> list[TableInfo]:
        now = self.clock()
        if self._tables is None or now - self._at > CACHE_SECONDS:
            self._tables = await self.read()
            self._at = now
        return self._tables

    def forget(self) -> None:
        self._tables = None


_UNKNOWN_COLUMN: Final = re.compile(
    r'column "?(?:[\w$]+\.)?(?P<name>[\w$]+)"? does not exist'
    r"|no such column: (?:[\w$]+\.)?(?P<name2>[\w$]+)"
)


def close_columns(message: str, tables: Iterable[TableInfo], within: Iterable[str]) -> str | None:
    """« colonnes proches dans devis : montant_ht » pour une colonne inconnue, ou ``None``."""
    found = _UNKNOWN_COLUMN.search(message)
    if found is None:
        return None
    name = found.group("name") or found.group("name2") or ""
    wanted = set(within)
    hints: list[str] = []
    for table in tables:
        if table.name not in wanted:
            continue
        close = difflib.get_close_matches(name, [c.name for c in table.columns], n=3, cutoff=0.5)
        if close:
            hints.append(f"{table.name} : {', '.join(close)}")
    if not hints:
        return None
    return f"Colonne « {name} » inconnue — colonnes proches dans " + " ; ".join(hints) + "."
