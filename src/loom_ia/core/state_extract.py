# SPDX-License-Identifier: Apache-2.0
"""Extraction d'une valeur du state depuis le résultat d'un outil (state v2).

Le modèle désigne un résultat (``loom.state``) ; la règle d'extraction, elle,
est déclarée sur la clé (``extract:``) : le modèle ne transmet aucune donnée,
il ne peut ni mal recopier ni inventer.

Trois formes de résultat sont lues, comme des lignes :

- un tableau ``{"columns": [...], "rows": [[...], ...]}`` (``loom.sql``) ;
- une liste d'objets ;
- un objet seul (une ligne).

Trois règles :

- ``value`` : la colonne d'une ligne, telle quelle ;
- ``fields`` : un objet, chaque champ pris dans une colonne d'une ligne ;
- ``columns`` : un objet, chaque champ la liste d'une colonne sur toutes les
  lignes. Refusé sur un résultat tronqué (``"truncated": true``), qui
  donnerait une liste incomplète sans le dire.

Une ligne se choisit par ``row`` (à partir de 0) ; sans ``row``, le résultat
doit avoir exactement une ligne. ``label`` ajoute un champ, rempli par le
texte que donne le modèle (un libellé, jamais une donnée du résultat).

Module pur : aucune entrée-sortie, aucune dépendance au reste de loom.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from pydantic import JsonValue


class ExtractError(ValueError):
    """Extraction impossible ; le message, destiné au modèle, dit quoi corriger."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Extraction:
    """Règle d'extraction d'une clé : exactement une de ``value``, ``fields``, ``columns``."""

    value: str | None = None
    fields: Mapping[str, str] = field(default_factory=dict[str, str])
    columns: Mapping[str, str] = field(default_factory=dict[str, str])
    # Champ qui reçoit le libellé donné par le modèle ; avec ``fields`` ou ``columns``.
    label: str | None = None

    @property
    def single_row(self) -> bool:
        """Vrai si la règle lit une seule ligne (``value``, ``fields``)."""
        return not self.columns


@dataclass(frozen=True, slots=True)
class _Table:
    names: tuple[str, ...]
    rows: tuple[Mapping[str, JsonValue] | Sequence[JsonValue], ...]
    tabular: bool
    truncated: bool

    def cell(self, row: int, column: str) -> JsonValue:
        record = self.rows[row]
        if self.tabular:
            assert isinstance(record, Sequence) and not isinstance(record, str)
            index = self.index(column)
            if index >= len(record):
                raise ExtractError(f"ligne {row} : pas de valeur pour la colonne {column!r}")
            return record[index]
        assert isinstance(record, Mapping)
        if column not in record:
            raise ExtractError(
                f"ligne {row} : colonne {column!r} absente (colonnes : {_listed(record)})"
            )
        return record[column]

    def index(self, column: str) -> int:
        found = [i for i, name in enumerate(self.names) if name == column]
        if not found:
            raise ExtractError(
                f"colonne {column!r} absente du résultat (colonnes : {_listed(self.names)})"
            )
        if len(found) > 1:
            raise ExtractError(
                f"colonne {column!r} présente {len(found)} fois : donner un alias dans la requête"
            )
        return found[0]


@dataclass(frozen=True, slots=True, kw_only=True)
class ModelKey:
    """Une clé du state que le modèle tient à jour (``writers: [model]``), pour ``loom.state``."""

    name: str
    description: str
    schema: Mapping[str, JsonValue]
    extraction: Extraction
    required: bool = False


def extract(
    data: JsonValue, rule: Extraction, *, row: int | None = None, label: str | None = None
) -> JsonValue:
    """La valeur que la règle tire de ces données ; ``ExtractError`` sinon."""
    table = _table(data)
    if rule.label is None and label is not None:
        raise ExtractError("cette clé ne prend pas de libellé : retirer 'label'")
    if rule.single_row:
        index = _row(table, row)
        if rule.value is not None:
            return table.cell(index, rule.value)
        result: dict[str, JsonValue] = {
            name: table.cell(index, column) for name, column in rule.fields.items()
        }
    else:
        if row is not None:
            raise ExtractError("cette clé prend toutes les lignes du résultat : retirer 'row'")
        if table.truncated:
            raise ExtractError(
                "résultat tronqué (truncated) : la liste serait incomplète ; refaire la requête "
                "pour qu'elle rende toutes les lignes"
            )
        if table.tabular:
            for column in rule.columns.values():
                table.index(column)
        result = {
            name: [table.cell(i, column) for i in range(len(table.rows))]
            for name, column in rule.columns.items()
        }
    if rule.label is not None and label is not None:
        result[rule.label] = label
    return result


def _table(data: JsonValue) -> _Table:
    if isinstance(data, dict) and "columns" in data and "rows" in data:
        names, rows = data["columns"], data["rows"]
        if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
            raise ExtractError("résultat illisible : 'columns' n'est pas une liste de noms")
        if not isinstance(rows, list):
            raise ExtractError("résultat illisible : 'rows' n'est pas une liste de lignes")
        lines = tuple(row for row in rows if isinstance(row, list))
        if len(lines) != len(rows):
            raise ExtractError("résultat illisible : 'rows' n'est pas une liste de lignes")
        return _Table(
            tuple(str(n) for n in names),
            lines,
            tabular=True,
            truncated=data.get("truncated") is True,
        )
    if isinstance(data, list):
        if not all(isinstance(item, dict) for item in data):
            raise ExtractError(
                "résultat illisible : une liste dont les éléments ne sont pas des objets"
            )
        records = tuple(item for item in data if isinstance(item, dict))
        names = tuple(dict.fromkeys(key for record in records for key in record))
        return _Table(names, records, tabular=False, truncated=False)
    if isinstance(data, dict):
        return _Table(tuple(data), (data,), tabular=False, truncated=False)
    raise ExtractError(
        "ce résultat n'a pas de données structurées (texte, image) : rien à extraire"
    )


def _row(table: _Table, row: int | None) -> int:
    count = len(table.rows)
    if row is None:
        if count == 1:
            return 0
        if count == 0:
            raise ExtractError("le résultat n'a aucune ligne : rien à poser")
        raise ExtractError(f"le résultat a {count} lignes : préciser 'row' (0 à {count - 1})")
    if not 0 <= row < count:
        raise ExtractError(f"ligne {row} hors du résultat ({count} ligne(s) : 0 à {count - 1})")
    return row


def _listed(names: Mapping[str, object] | Sequence[str]) -> str:
    shown = list(names)
    return ", ".join(shown[:20]) + (", …" if len(shown) > 20 else "") if shown else "aucune"
