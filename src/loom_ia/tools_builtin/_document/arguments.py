# SPDX-License-Identifier: Apache-2.0
"""Ce que voit le modèle : un document décrit par ses blocs, commun à ``word`` et ``pdf``.

Le modèle dit ce que **sont** les blocs — titre, paragraphe, liste, tableau
aux colonnes typées, note, citation — et jamais leur apparence : police,
tailles, couleurs, marges, numérotation des pages viennent du style maison
de chaque format. Clés en anglais, vocabulaire en français, comme ``excel``.

Pydantic seulement : ni disque, ni bibliothèque de rendu.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Annotated, ClassVar, Final, Literal, Self, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, model_validator

from loom_ia.core.ports import ToolError
from loom_ia.tools_builtin._values import Aggregate, ColumnType, Scalar

MAX_BLOCKS: Final = 1_000
MAX_TEXT: Final = 20_000
MAX_ITEMS: Final = 200
MAX_TABLE_COLUMNS: Final = 8
MAX_TABLE_ROWS: Final = 500
MAX_SOURCE_BYTES: Final = 20_000_000
MAX_OUTPUT_BYTES: Final = 50_000_000
MAX_WARNINGS: Final = 20
MAX_ERRORS_SAID: Final = 10
# Ce que rend une lecture, en caractères JSON : sous le seuil de déport par défaut
# (50 000), comme la lecture d'un classeur.
READ_CHARS: Final = 40_000


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --- Blocs ---------------------------------------------------------------------------
# Les descriptions des champs restent courtes : la description de l'outil explique
# déjà le vocabulaire, et le schéma est envoyé au modèle à chaque appel.


class Heading(_Model):
    type: Literal["titre"]
    text: str
    level: Annotated[int, Field(ge=1, le=3)] = 1


class Paragraph(_Model):
    type: Literal["paragraphe"]
    text: str


class Item(_Model):
    """Un élément de liste qui porte une sous-liste (un niveau)."""

    text: str
    items: list[str]


class ListBlock(_Model):
    type: Literal["liste"]
    items: Annotated[list[str | Item], Field(min_length=1)]
    ordered: bool = False


class Column(_Model):
    header: Annotated[str, Field(min_length=1)]
    type: ColumnType = "texte"


class Table(_Model):
    type: Literal["tableau"]
    columns: Annotated[list[Column], Field(min_length=1)]
    rows: list[list[Scalar]]
    totals: dict[str, Aggregate] | None = None
    caption: str | None = None


class Note(_Model):
    type: Literal["note"]
    text: str
    title: str | None = None


class Quote(_Model):
    type: Literal["citation"]
    text: str
    source: str | None = None


class PageBreak(_Model):
    type: Literal["saut_de_page"]


type Block = Annotated[
    Heading | Paragraph | ListBlock | Table | Note | Quote | PageBreak,
    Field(discriminator="type"),
]


# --- Appels --------------------------------------------------------------------------


class WriteArguments(_Model):
    """Une écriture : le document et ses blocs."""

    path: str
    title: str | None
    blocks: list[Block]
    overwrite: bool = False


class ReadArguments(_Model):
    """Une lecture : où commencer, selon le format (bloc ou page)."""

    path: str
    start: int | None = None
    pages: str | None = None


type Action = Literal["write", "read"]


class DocumentCall(_Model):
    """Ce que voit le modèle : une écriture (par défaut) ou une lecture, à plat.

    Chaque format ajoute ses champs de lecture et les nomme dans ``READ_FIELDS``.
    """

    READ_FIELDS: ClassVar[tuple[str, ...]] = ()
    SUFFIX: ClassVar[str] = ""

    action: Annotated[
        Action,
        Field(description="write (par défaut) : écrire ; read : lire un fichier (droit read)."),
    ] = "write"
    path: Annotated[str, Field(description="« <dossier>/<chemin> »")]
    title: Annotated[
        str | None, Field(description="Pour write : le titre, en tête et dans les propriétés.")
    ] = None
    blocks: Annotated[
        list[Block] | None,
        Field(max_length=MAX_BLOCKS, description="Pour write : les blocs, dans l'ordre."),
    ] = None
    overwrite: Annotated[
        bool | None, Field(description="Pour write : remplacer un fichier existant.")
    ] = None

    @model_validator(mode="after")
    def _fields_of_the_action(self) -> Self:
        writing = ("title", "blocks", "overwrite")
        stray = sorted(
            name
            for name in (*writing, *self.READ_FIELDS)
            # « overwrite: false » sur une lecture ne dit rien : pas une faute.
            if (name in writing) != (self.action == "write")
            and getattr(self, name) not in (None, False)
        )
        if stray:
            raise ValueError(f"{', '.join(stray)} ne sert pas à {self.action}")
        if self.action == "write" and self.blocks is None:
            raise ValueError("write attend 'blocks', le contenu du document")
        return self

    def request(self) -> WriteArguments | ReadArguments:
        if self.action == "read":
            return ReadArguments(
                path=self.path,
                start=cast(int | None, getattr(self, "start", None)),
                pages=cast(str | None, getattr(self, "pages", None)),
            )
        return WriteArguments(
            path=self.path,
            title=self.title,
            blocks=cast(list[Block], self.blocks),
            overwrite=bool(self.overwrite),
        )


def parse(
    model: type[DocumentCall], arguments: Mapping[str, JsonValue]
) -> WriteArguments | ReadArguments:
    """Les arguments validés ; ``ToolError`` qui dit chaque champ fautif (10 au plus)."""
    try:
        return model.model_validate(dict(arguments)).request()
    except ValidationError as exc:
        lines = ["Arguments invalides :"]
        for item in exc.errors(include_url=False)[:MAX_ERRORS_SAID]:
            location = ".".join(str(part) for part in item["loc"]) or "(racine)"
            lines.append(f"- {location} : {item['msg']}")
        raise ToolError("\n".join(lines)) from None


def input_schema(model: type[DocumentCall]) -> dict[str, JsonValue]:
    """Le schéma des arguments, allégé de ce qui ne sert pas au modèle.

    Ni les titres que Pydantic ajoute à chaque champ, ni les docstrings des
    modèles (écrites pour qui lit le code), ni les ``default: null`` des champs
    facultatifs : le schéma part à chaque appel du modèle.
    """
    return cast(dict[str, JsonValue], _light(model.model_json_schema()))


def _light(node: object, *, names: bool = False) -> object:
    """``names`` : ``node`` est une table de propriétés, dont les clés sont des noms."""
    if isinstance(node, dict):
        found: dict[str, object] = {}
        mapping = cast(dict[str, object], node)
        for key, value in mapping.items():
            # Retirés comme mots-clés seulement, jamais comme noms de propriété.
            if not names and (
                key == "title"
                or (key == "description" and "properties" in mapping)
                or (key == "default" and value is None)
            ):
                continue
            found[key] = _light(value, names=key == "properties" and not names)
        return found
    if isinstance(node, list):
        return [_light(value) for value in cast(list[object], node)]
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
        raise ToolError("\n".join(["Document refusé :", *(f"- {e}" for e in shown), *tail]))
