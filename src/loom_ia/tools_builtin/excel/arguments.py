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
from loom_ia.tools_builtin._values import Aggregate, ColumnType, Scalar

NAME: Final = "excel"
MAX_SHEETS: Final = 50
MAX_COLUMNS: Final = 200
MAX_INLINE_ROWS: Final = 200
MAX_DATA_ROWS: Final = 100_000
MAX_SOURCE_BYTES: Final = 20_000_000
MAX_OUTPUT_BYTES: Final = 50_000_000
MAX_WARNINGS: Final = 20
MAX_ERRORS_SAID: Final = 10
# Lecture : lignes rendues par appel, par défaut et au plus.
READ_ROWS: Final = 200
MAX_READ_ROWS: Final = 1_000
# Excel : 1 048 576 lignes, 16 384 colonnes (XFD).
MAX_SHEET_ROWS: Final = 1_048_576
MAX_SHEET_COLUMNS: Final = 16_384

type Role = Literal["saisie", "formule", "hypothese", "total", "note"]

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
            description="Un .csv (en-têtes en 1re ligne, « , » ou « ; »), un .json (liste "
            "d'objets dont les clés sont les en-têtes) ou un .xlsx (en-têtes en ligne 1)."
        ),
    ]
    sheet: Annotated[
        str | None, Field(description="Pour un .xlsx : l'onglet lu ; le premier si absent.")
    ] = None

    @model_validator(mode="after")
    def _sheet_of_a_workbook(self) -> Self:
        # Avant toute lecture : la faute se dit sans ouvrir le fichier.
        if self.sheet is not None and not self.path.lower().endswith((".xlsx", ".xlsm")):
            raise ValueError(f"{self.path} : 'sheet' ne sert qu'à un .xlsx")
        return self


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
    """Une écriture : le classeur et ses onglets."""

    path: Annotated[str, Field(description="« <dossier>/<chemin>.xlsx »")]
    sheets: Annotated[list[SheetSpec], Field(min_length=1, max_length=MAX_SHEETS)]
    overwrite: bool = False


class ReadArguments(_Model):
    """Une lecture : sans onglet, la liste des onglets ; avec, ses cellules."""

    path: str
    sheet: str | None = None
    range: str | None = None
    max_rows: int = READ_ROWS


type Action = Literal["write", "read"]
_FOR: Final[dict[str, Action]] = {
    "sheets": "write",
    "overwrite": "write",
    "sheet": "read",
    "range": "read",
    "max_rows": "read",
}


class ExcelCall(_Model):
    """Ce que voit le modèle : une écriture (par défaut) ou une lecture, à plat."""

    action: Annotated[
        Action,
        Field(
            description="write (par défaut) : écrire un classeur ; read : lire un classeur "
            "existant (droit read)."
        ),
    ] = "write"
    path: Annotated[str, Field(description="« <dossier>/<chemin>.xlsx »")]
    sheets: Annotated[
        list[SheetSpec] | None,
        Field(min_length=1, max_length=MAX_SHEETS, description="Pour write : les onglets."),
    ] = None
    overwrite: Annotated[
        bool | None, Field(description="Pour write : remplacer un classeur existant.")
    ] = None
    sheet: Annotated[
        str | None,
        Field(description="Pour read : l'onglet ; sans lui, la liste des onglets et leur début."),
    ] = None
    range: Annotated[
        str | None,
        Field(description="Pour read avec sheet : la plage (A1:F50, A:C, 2:10) ; depuis A1 sinon."),
    ] = None
    max_rows: Annotated[
        int | None,
        Field(
            ge=1,
            le=MAX_READ_ROWS,
            description=f"Pour read : lignes rendues au plus ({READ_ROWS} par défaut).",
        ),
    ] = None

    @model_validator(mode="after")
    def _fields_of_the_action(self) -> Self:
        stray = sorted(
            name
            for name, action in _FOR.items()
            # « overwrite: false » sur une lecture ne dit rien : pas une faute.
            if action != self.action and getattr(self, name) not in (None, False)
        )
        if stray:
            raise ValueError(f"{', '.join(stray)} ne sert pas à {self.action}")
        if self.action == "write" and self.sheets is None:
            raise ValueError("write attend 'sheets', les onglets à écrire")
        if self.range is not None and self.sheet is None:
            raise ValueError("'range' demande 'sheet'")
        return self

    def request(self) -> ExcelArguments | ReadArguments:
        if self.action == "read":
            return ReadArguments(
                path=self.path,
                sheet=self.sheet,
                range=self.range,
                max_rows=self.max_rows or READ_ROWS,
            )
        return ExcelArguments(
            path=self.path,
            sheets=cast(list[SheetSpec], self.sheets),
            overwrite=bool(self.overwrite),
        )


def parse(arguments: Mapping[str, JsonValue]) -> ExcelArguments | ReadArguments:
    """Les arguments validés ; ``ToolError`` qui dit chaque champ fautif (10 au plus)."""
    try:
        return ExcelCall.model_validate(dict(arguments)).request()
    except ValidationError as exc:
        lines = ["Arguments invalides :"]
        for item in exc.errors(include_url=False)[:MAX_ERRORS_SAID]:
            location = ".".join(str(part) for part in item["loc"]) or "(racine)"
            lines.append(f"- {location} : {item['msg']}")
        raise ToolError("\n".join(lines)) from None


def input_schema() -> dict[str, JsonValue]:
    """Le schéma des arguments, sans les titres que Pydantic ajoute à chaque champ."""
    return cast(dict[str, JsonValue], _untitled(ExcelCall.model_json_schema()))


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
