# SPDX-License-Identifier: Apache-2.0
"""Ce que voit le modèle : un document décrit par ses blocs, commun à ``word`` et ``pdf``.

Le modèle dit ce que **sont** les blocs — heading, paragraph, list, table aux
colonnes typées, note, quote — et jamais leur apparence : police, tailles,
couleurs, marges, sauts de page et numérotation des pages viennent du style.
Clés et vocabulaire en anglais, comme ``excel`` ; descriptions en français.

Pydantic seulement : ni disque, ni bibliothèque de rendu.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from functools import cache
from typing import Annotated, Any, ClassVar, Final, Literal, Self, TypeAliasType, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    ValidationError,
    create_model,
    model_validator,
)

from loom_ia.core.ports import ToolError
from loom_ia.tools_builtin._office_params import OPTIONAL_BLOCKS
from loom_ia.tools_builtin._templates import NONE as NO_TEMPLATE
from loom_ia.tools_builtin._values import Aggregate, ColumnType, Scalar

MAX_BLOCKS: Final = 1_000
MAX_TEXT: Final = 20_000
MAX_ITEMS: Final = 200
MAX_TABLE_COLUMNS: Final = 8
MAX_TABLE_ROWS: Final = 500
MAX_CATEGORIES: Final = 50
MAX_SLICES: Final = 12
MAX_SERIES: Final = 6
# Images : par image, pour tout le document, nombre de blocs, pixels d'une image.
MAX_IMAGE_BYTES: Final = 10_000_000
MAX_IMAGES_BYTES: Final = 40_000_000
MAX_IMAGES: Final = 50
MAX_PIXELS: Final = 40_000_000
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
    type: Literal["heading"]
    text: str
    level: Annotated[int, Field(ge=1, le=3)] = 1


class Paragraph(_Model):
    type: Literal["paragraph"]
    text: str


class Item(_Model):
    """Un élément de liste qui porte une sous-liste (un niveau)."""

    text: str
    items: list[str]


class ListBlock(_Model):
    type: Literal["list"]
    items: Annotated[list[str | Item], Field(min_length=1)]
    ordered: bool = False


class CheckList(ListBlock):
    """Une liste, à cocher (``checkboxes``) : chaque élément commence par [x] ou [ ]."""

    checkboxes: bool = False


class Column(_Model):
    header: Annotated[str, Field(min_length=1)]
    type: ColumnType = "text"


class Table(_Model):
    type: Literal["table"]
    columns: Annotated[list[Column], Field(min_length=1)]
    rows: list[list[Scalar]]
    totals: dict[str, Aggregate] | None = None
    caption: str | None = None


class Note(_Model):
    type: Literal["note"]
    text: str
    title: str | None = None


class Quote(_Model):
    type: Literal["quote"]
    text: str
    source: str | None = None


type ChartKind = Literal["bar", "horizontal_bar", "line", "pie"]


class Series(_Model):
    name: str
    values: list[Scalar]


class FromTable(_Model):
    """Les données du tableau qui précède le graphique : ses colonnes, par en-tête."""

    categories: Annotated[str, Field(description="En-tête de la colonne des libellés.")]
    values: Annotated[
        list[str], Field(min_length=1, description="En-têtes des colonnes de nombres.")
    ]


class Chart(_Model):
    type: Literal["chart"]
    kind: ChartKind = "bar"
    title: str | None = None
    categories: list[str] | None = None
    series: list[Series] | None = None
    value_type: ColumnType | None = None
    from_table: FromTable | None = None
    caption: str | None = None

    @model_validator(mode="after")
    def _one_source(self) -> Self:
        inline = self.categories is not None or self.series is not None
        if inline == (self.from_table is not None):
            raise ValueError(
                "un graphique prend ses données soit de 'categories' et 'series', soit de "
                "'from_table' (le tableau qui le précède)"
            )
        if inline and (self.categories is None or self.series is None):
            raise ValueError("'categories' et 'series' vont ensemble")
        if self.from_table is not None and self.value_type is not None:
            raise ValueError(
                "'value_type' ne sert qu'aux données en ligne : from_table prend le type "
                "des colonnes"
            )
        return self


type ImageWidth = Literal["full", "half", "small"]


class Image(_Model):
    type: Literal["image"]
    path: str
    width: ImageWidth = "full"
    caption: str | None = None
    alt: str | None = None


MAX_SIGNERS: Final = 4


class Signature(_Model):
    type: Literal["signature"]
    signers: Annotated[list[str], Field(min_length=1, max_length=MAX_SIGNERS)]
    mention: str | None = None


class Contents(_Model):
    type: Literal["toc"]


# Les blocs que le modèle voit sans bloc facultatif.
type Block = Annotated[
    Heading | Paragraph | ListBlock | Table | Note | Quote,
    Field(discriminator="type"),
]
# Tous les blocs : ce qu'une écriture porte, quel que soit l'outil. Une liste à cocher
# (``CheckList``) y passe comme liste : elle en est une.
type WrittenBlock = Annotated[
    Heading | Paragraph | ListBlock | Table | Note | Quote | Chart | Image | Signature | Contents,
    Field(discriminator="type"),
]


# Les blocs qui prennent un style proposé (``style``), par leur type.
STYLED_BLOCKS: Final = ("heading", "paragraph", "list", "table", "note", "quote")
STYLE_SAID: Final = "Un style proposé pour ce bloc (voir Styles proposés) ; sinon, rien."

type Proposed = tuple[tuple[str, tuple[str, ...]], ...]


@cache
def _styled(kind: type[_Model], names: tuple[str, ...]) -> type[_Model]:
    """Le bloc, avec un champ ``style`` : un des styles qui lui sont proposés."""
    choices: Any = cast(Any, Literal)[names]
    return create_model(
        f"{kind.__name__}Styled",
        __base__=kind,
        __module__=kind.__module__,
        style=(Annotated[choices | None, Field(description=STYLE_SAID)], None),
    )


def block_union(blocks: Iterable[str], styles: Proposed = ()) -> object:
    """Les blocs du schéma pour ces blocs facultatifs, dans un ordre fixe ; ``styles`` : les
    styles proposés, par type de bloc (le champ ``style`` des blocs qui en ont)."""
    chosen = set(blocks)
    proposed = dict(styles)
    kinds: list[type[_Model]] = [
        Heading,
        Paragraph,
        CheckList if "checkboxes" in chosen else ListBlock,
        Table,
        Note,
        Quote,
    ]
    kinds = [
        _styled(kind, proposed[name]) if (name := _TYPES[kind]) in proposed else kind
        for kind in kinds
    ]
    kinds += [
        kind
        for name, kind in (
            ("chart", Chart),
            ("image", Image),
            ("signature", Signature),
            ("toc", Contents),
        )
        if name in chosen
    ]
    union: Any = kinds[0]
    for kind in kinds[1:]:
        union = union | kind
    return Annotated[union, Field(discriminator="type")]


_TYPES: Final[Mapping[type[_Model], str]] = {
    Heading: "heading",
    Paragraph: "paragraph",
    ListBlock: "list",
    CheckList: "list",
    Table: "table",
    Note: "note",
    Quote: "quote",
}


def style_of(block: object) -> str | None:
    """Le style proposé qu'un bloc a choisi ; ``None`` sans style."""
    said = getattr(block, "style", None)
    return said if isinstance(said, str) else None


# --- Appels --------------------------------------------------------------------------


class WriteArguments(_Model):
    """Une écriture : le document, ses blocs (document libre) ou ses sections (modèle)."""

    path: str
    title: str | None
    blocks: list[WrittenBlock] = []
    overwrite: bool = False
    # Le modèle suivi (son nom) ou ``none`` ; ``None`` : l'outil n'a pas de modèles.
    template: str | None = None
    # Le contenu de chaque section, par son nom, pour un document qui suit un modèle.
    sections: dict[str, list[WrittenBlock]] | None = None

    def all_blocks(self) -> list[WrittenBlock]:
        """Tous les blocs, sections comprises, dans l'ordre où ils sont donnés."""
        if self.sections is None:
            return list(self.blocks)
        return [block for blocks in self.sections.values() for block in blocks]


class ReadArguments(_Model):
    """Une lecture : où commencer, selon le format (bloc ou page)."""

    path: str
    start: int | None = None
    pages: str | None = None


class TemplateArguments(_Model):
    """La lecture d'un modèle de document déclaré (``action: template``)."""

    name: str


type Action = Literal["write", "read"]
# Les actions d'un outil monté avec des modèles de document.
type TemplatedAction = Literal["write", "read", "template"]

BLOCKS_SAID: Final = "Pour write : les blocs, dans l'ordre."


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
        Field(max_length=MAX_BLOCKS, description=BLOCKS_SAID),
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
        sections = getattr(self, "sections", None)
        if self.action == "write" and self.blocks is None and sections is None:
            raise ValueError("write attend 'blocks', le contenu du document")
        return self

    def request(self) -> WriteArguments | ReadArguments | TemplateArguments:
        template = cast(str | None, getattr(self, "template", None))
        if cast(str, self.action) == "template":
            return TemplateArguments(name=cast(str, template))
        if self.action == "read":
            return ReadArguments(
                path=self.path,
                start=cast(int | None, getattr(self, "start", None)),
                pages=cast(str | None, getattr(self, "pages", None)),
            )
        sections = cast(dict[str, list[WrittenBlock]] | None, getattr(self, "sections", None))
        return WriteArguments(
            path=self.path,
            title=self.title,
            blocks=cast(list[WrittenBlock], self.blocks or []),
            overwrite=bool(self.overwrite),
            template=template,
            sections=sections,
        )


ACTION_SAID: Final = (
    "write (par défaut) : écrire ; read : lire un fichier (droit read) ; template : lire "
    "un modèle de document."
)
TEMPLATE_SAID: Final = (
    "Pour write : le modèle suivi, ou none (document libre) ; pour template : le modèle à lire."
)
SECTIONS_SAID: Final = (
    "Pour write avec un modèle : le contenu de chaque section, par son nom (voir action "
    "template) ; l'outil écrit les titres de section."
)
# Le nom de l'appel selon ses blocs facultatifs : ``WordChartImageCall``…
_SUFFIXES: Final[Mapping[str, str]] = {
    "chart": "Chart",
    "image": "Image",
    "signature": "Signature",
    "toc": "Toc",
    "checkboxes": "Checkboxes",
}


def variant(
    base: type[DocumentCall],
    *,
    blocks: tuple[str, ...] = (),
    templates: tuple[str, ...] = (),
    styles: Mapping[str, Iterable[str]] | None = None,
) -> type[DocumentCall]:
    """L'appel d'un format (``WordCall``…), élargi aux réglages de l'outil.

    ``blocks`` (les blocs facultatifs déclarés, dans n'importe quel ordre) : ``blocks`` du
    schéma gagne les leurs. ``templates`` (les noms des modèles déclarés) : l'action
    ``template``, le champ ``template``, obligatoire à l'écriture, et ``sections``.
    ``styles`` (les styles proposés, par type de bloc) : le champ ``style`` de ces blocs.
    Sans réglage, ``base`` lui-même. Le nom du modèle ne paraît pas dans le schéma allégé
    (``input_schema``).
    """
    chosen = tuple(name for name in OPTIONAL_BLOCKS if name in blocks)
    proposed: Proposed = tuple(
        (kind, tuple(sorted(set(names))))
        for kind, names in sorted((styles or {}).items())
        if kind in STYLED_BLOCKS and names
    )
    return _variant(base, chosen, templates, proposed)


@cache
def _variant(
    base: type[DocumentCall],
    chosen: tuple[str, ...],
    templates: tuple[str, ...],
    proposed: Proposed = (),
) -> type[DocumentCall]:
    if not (chosen or templates or proposed):
        return base
    fields: dict[str, Any] = {}
    union: Any = block_union(chosen, proposed)
    if templates:
        # Les blocs servent à 'blocks' et à 'sections' : nommés, le schéma les dit une fois.
        # (un alias construit à l'exécution : pyright ne sait pas le nommer.)
        named: Any = TypeAliasType
        union = Block if not (chosen or proposed) else named("Block", union)
    if chosen or proposed:
        fields["blocks"] = (
            Annotated[
                list[union] | None,  # type: ignore[valid-type]
                Field(max_length=MAX_BLOCKS, description=BLOCKS_SAID),
            ],
            None,
        )
    validators: dict[str, Any] = {}
    if templates:
        names: Any = cast(Any, Literal)[(*templates, NO_TEMPLATE)]
        fields["action"] = (
            Annotated[TemplatedAction, Field(description=ACTION_SAID)],
            "write",
        )
        fields["template"] = (Annotated[names | None, Field(description=TEMPLATE_SAID)], None)
        fields["sections"] = (
            Annotated[
                dict[str, list[union]] | None,  # type: ignore[valid-type]
                Field(description=SECTIONS_SAID),
            ],
            None,
        )
        # Lire un modèle ne vise aucun fichier : ``path`` n'est demandé que par write et read.
        fields["path"] = (
            Annotated[str | None, Field(description=base.model_fields["path"].description)],
            None,
        )
        validators["_template_of_the_action"] = model_validator(mode="after")(_template_rule)
    suffix = (
        "".join(_SUFFIXES[name] for name in chosen)
        + ("Template" if templates else "")
        + ("Styled" if proposed else "")
    )
    return create_model(
        f"{base.__name__.removesuffix('Call')}{suffix}Call",
        __base__=base,
        __module__=base.__module__,
        __validators__=validators,
        **fields,
    )


def _template_rule[M: DocumentCall](self: M) -> M:
    """Avec des modèles : write nomme le sien (ou none), template nomme celui à lire."""
    action = cast(str, self.action)
    template = cast(str | None, getattr(self, "template", None))
    path = cast(str | None, self.path)
    if action != "template" and path is None:
        raise ValueError(f"{action} attend 'path'")
    if action == "template" and path is not None:
        raise ValueError("path ne sert pas à template")
    if action == "write" and template is None:
        raise ValueError(
            "write attend 'template' : le modèle suivi, ou none pour un document libre"
        )
    sections = getattr(self, "sections", None)
    if action == "write" and template == NO_TEMPLATE and sections is not None:
        raise ValueError("un document libre (template none) s'écrit par 'blocks', pas 'sections'")
    if action == "write" and template not in (None, NO_TEMPLATE):
        if self.blocks is not None:
            raise ValueError(
                f"un document qui suit le modèle {template} s'écrit par 'sections' (le contenu "
                "de chaque section, par son nom), pas 'blocks'"
            )
        if sections is None:
            raise ValueError(
                f"write avec le modèle {template} attend 'sections' : le contenu de chaque "
                "section, par son nom (action template pour les lire)"
            )
    if action == "read" and template is not None:
        raise ValueError("template ne sert pas à read")
    if action == "read" and sections is not None:
        raise ValueError("sections ne sert pas à read")
    if action == "template":
        if template is None or template == NO_TEMPLATE:
            raise ValueError("action template attend 'template' : le nom du modèle à lire")
        stray = sorted(
            name
            for name in (*self.READ_FIELDS, "title", "blocks", "overwrite", "sections")
            if getattr(self, name, None) not in (None, False)
        )
        if stray:
            raise ValueError(f"{', '.join(stray)} ne sert pas à template")
    return self


# Le même appel, avec le bloc ``chart`` (tests du vocabulaire commun).
WithCharts: Final = variant(DocumentCall, blocks=("chart",))


def parse(
    model: type[DocumentCall], arguments: Mapping[str, JsonValue]
) -> WriteArguments | ReadArguments | TemplateArguments:
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
