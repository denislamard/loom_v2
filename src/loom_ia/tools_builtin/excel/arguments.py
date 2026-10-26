# SPDX-License-Identifier: Apache-2.0
"""Ce que voit le modèle : les arguments de ``excel``, leur vocabulaire, les bornes d'un appel.

Le modèle décrit ce que **sont** les données — types de colonne, rôles des
lignes et des cellules, formules —, jamais leur apparence. Pydantic
seulement : ni openpyxl, ni disque.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Annotated, Final, Literal, Self, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, model_validator

from loom_ia.core.ports import ToolError

NAME: Final = "excel"
MAX_SHEETS: Final = 50
MAX_COLUMNS: Final = 200
MAX_INLINE_ROWS: Final = 200
MAX_DATA_ROWS: Final = 100_000
MAX_SOURCE_BYTES: Final = 20_000_000
MAX_OUTPUT_BYTES: Final = 50_000_000
MAX_WARNINGS: Final = 20
MAX_ERRORS_SAID: Final = 10
# Excel : 1 048 576 lignes, 16 384 colonnes (XFD).
MAX_SHEET_ROWS: Final = 1_048_576
MAX_SHEET_COLUMNS: Final = 16_384

type ColumnType = Literal[
    "texte",
    "entier",
    "decimal",
    "montant_eur",
    "montant_keur",
    "pourcentage",
    "date",
    "date_heure",
    "annee",
    "booleen",
]
type Role = Literal["saisie", "formule", "hypothese", "total", "note"]
type Aggregate = Literal["somme", "moyenne", "nombre", "min", "max"]
type Scalar = str | int | float | bool | None

# Les descriptions des champs restent courtes : la description de l'outil
# explique déjà le vocabulaire, et le schéma est envoyé au modèle à chaque appel.


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ColumnSpec(_Model):
    header: Annotated[
        str,
        Field(
            min_length=1,
            description="L'unité seulement si le format ne l'affiche pas : « Surface (m²) », "
            "mais « Montant HT », pas « Montant HT (€) ».",
        ),
    ]
    type: ColumnType = "texte"


class CellSpec(_Model):
    """Une cellule qui porte son propre rôle ou un commentaire."""

    value: Scalar = None
    role: Role | None = None
    note: str | None = None


class RowSpec(_Model):
    cells: Annotated[
        list[Scalar | CellSpec], Field(description="Dans l'ordre des colonnes ; « =… » : formule.")
    ]
    role: Role | None = None
    note: Annotated[str | None, Field(description="Commentaire de la ligne (source…).")] = None


class DataSource(_Model):
    path: Annotated[
        str,
        Field(
            description="Un .csv (en-têtes en 1re ligne, « , » ou « ; ») ou un .json (liste "
            "d'objets dont les clés sont les en-têtes)."
        ),
    ]


class SheetSpec(_Model):
    name: str
    columns: Annotated[list[ColumnSpec], Field(min_length=1)]
    rows: list[RowSpec] | None = None
    data: DataSource | None = None
    totals: dict[str, Aggregate] | None = None

    @model_validator(mode="after")
    def _one_source(self) -> Self:
        # Avant toute lecture : un fichier cité pour rien n'est pas ouvert.
        if self.rows is not None and self.data is not None:
            raise ValueError(f"{self.name} : 'rows' ou 'data', pas les deux")
        return self


class ExcelArguments(_Model):
    path: Annotated[str, Field(description="« <dossier>/<chemin>.xlsx »")]
    sheets: Annotated[list[SheetSpec], Field(min_length=1, max_length=MAX_SHEETS)]
    overwrite: bool = False


def parse(arguments: Mapping[str, JsonValue]) -> ExcelArguments:
    """Les arguments validés ; ``ToolError`` qui dit chaque champ fautif (10 au plus)."""
    try:
        return ExcelArguments.model_validate(dict(arguments))
    except ValidationError as exc:
        lines = ["Arguments invalides :"]
        for item in exc.errors(include_url=False)[:MAX_ERRORS_SAID]:
            location = ".".join(str(part) for part in item["loc"]) or "(racine)"
            lines.append(f"- {location} : {item['msg']}")
        raise ToolError("\n".join(lines)) from None


def input_schema() -> dict[str, JsonValue]:
    """Le schéma des arguments, sans les titres que Pydantic ajoute à chaque champ."""
    return cast(dict[str, JsonValue], _untitled(ExcelArguments.model_json_schema()))


def _untitled(node: object, *, names: bool = False) -> object:
    """``names`` : ``node`` est une table de propriétés, dont les clés sont des noms."""
    if isinstance(node, dict):
        items = cast(dict[str, object], node).items()
        # « title » n'est retiré que comme mot-clé, jamais comme nom de propriété.
        return {
            key: _untitled(value, names=key == "properties" and not names)
            for key, value in items
            if names or key != "title"
        }
    if isinstance(node, list):
        return [_untitled(value) for value in cast(list[object], node)]
    return node


@dataclass(slots=True)
class Problems:
    """Les fautes d'un passage de validation, dites ensemble ; et les avertissements."""

    errors: list[str] = field(default_factory=list[str])
    warnings: list[str] = field(default_factory=list[str])

    def error(self, said: str) -> None:
        self.errors.append(said)

    def warn(self, said: str) -> None:
        if said not in self.warnings and len(self.warnings) < MAX_WARNINGS:
            self.warnings.append(said)

    def raise_if_any(self) -> None:
        if not self.errors:
            return
        shown = self.errors[:MAX_ERRORS_SAID]
        more = len(self.errors) - len(shown)
        tail = [f"… et {more} autre(s)."] if more else []
        raise ToolError("\n".join(["Classeur refusé :", *(f"- {e}" for e in shown), *tail]))
