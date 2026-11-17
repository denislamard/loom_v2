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
"""Blocs au plus dans un document."""
MAX_TEXT: Final = 20_000
"""Longueur au plus d'un texte de bloc, en caractères."""
MAX_ITEMS: Final = 200
"""Éléments au plus dans une liste."""
MAX_TABLE_COLUMNS: Final = 8
"""Colonnes au plus dans un tableau."""
MAX_TABLE_ROWS: Final = 500
"""Lignes au plus dans un tableau."""
MAX_CATEGORIES: Final = 50
"""Libellés au plus dans un graphique."""
MAX_SLICES: Final = 12
"""Parts au plus dans un camembert."""
MAX_SERIES: Final = 6
"""Séries au plus dans un graphique."""
MAX_IMAGE_BYTES: Final = 10_000_000
"""Images : par image, pour tout le document, nombre de blocs, pixels d'une image."""
MAX_IMAGES_BYTES: Final = 40_000_000
"""Taille au plus de toutes les images d'un document, en octets."""
MAX_IMAGES: Final = 50
"""Images au plus dans un document."""
MAX_PIXELS: Final = 40_000_000
"""Pixels au plus dans une image."""
MAX_SOURCE_BYTES: Final = 20_000_000
"""Taille au plus d'un fichier lu, en octets."""
MAX_OUTPUT_BYTES: Final = 50_000_000
"""Taille au plus d'un fichier écrit, en octets."""
MAX_WARNINGS: Final = 20
"""Avertissements rendus au plus."""
MAX_ERRORS_SAID: Final = 10
"""Erreurs citées au plus dans un refus."""
READ_CHARS: Final = 40_000
"""Ce que rend une lecture, en caractères JSON : sous le seuil de déport par
défaut (50 000), comme la lecture d'un classeur.
"""


class _Model(BaseModel):
    """La base des modèles d'arguments : figés, sans champ inconnu.

    Sa docstring ne part pas dans le schéma : Pydantic ne la transmet pas aux sous-classes.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)


# --- Blocs ---------------------------------------------------------------------------
# Les descriptions des champs restent courtes : la description de l'outil explique
# déjà le vocabulaire, et le schéma est envoyé au modèle à chaque appel.


class Heading(_Model):
    """Bloc ``heading`` : un titre du document.

    Pas de description dans le schéma : ``_light`` la retire, la description de l'outil explique le vocabulaire.

    Attributes:
        type: ``heading``.
        text: Le texte du titre (l'emphase seule).
        level: Le niveau, de 1 à 3.
    """

    type: Literal["heading"]
    text: str
    level: Annotated[int, Field(ge=1, le=3)] = 1


class Paragraph(_Model):
    r"""Bloc ``paragraph`` : un paragraphe de texte, avec le balisage permis (gras, italique, souligné, liens, notes, renvois).

    Attributes:
        type: ``paragraph``.
        text: Le texte ; ``\n`` va à la ligne dans le même paragraphe.
    """

    type: Literal["paragraph"]
    text: str


class SubItem(_Model):
    """Un élément de sous-liste qui porte à son tour une sous-liste : le troisième niveau."""

    text: str
    items: list[str]


class Item(_Model):
    """Un élément de liste qui porte une sous-liste (deux niveaux en dessous au plus)."""

    text: str
    items: list[str | SubItem]


class ListBlock(_Model):
    """Bloc ``list`` : une liste à puces ou numérotée, jusqu'à trois niveaux (``Item``, ``SubItem``).

    Attributes:
        type: ``list``.
        items: Les éléments : un texte, ou un élément qui porte une sous-liste.
        ordered: Vrai pour une liste numérotée.
    """

    type: Literal["list"]
    items: Annotated[list[str | Item], Field(min_length=1)]
    ordered: bool = False


class CheckList(ListBlock):
    """Une liste, à cocher (``checkboxes``) : chaque élément commence par [x] ou [ ]."""

    checkboxes: bool = False


class Column(_Model):
    """Une colonne d'un tableau.

    Attributes:
        header: Son en-tête, qui la désigne aussi dans ``totals``, ``compute``
            et les graphiques.
        type: Son type de valeur (``text``, ``integer``, ``amount_eur``…) :
            l'outil écrit les valeurs à la française.
        compute: Une colonne calculée par l'outil, ligne par ligne, par une
            formule sur les nombres de la ligne (``"{Qté} * {PU HT}"``) : les
            lignes ne la donnent pas.
    """

    header: Annotated[str, Field(min_length=1)]
    type: ColumnType = "text"
    compute: str | None = None


class Group(_Model):
    """Un groupe de lignes (un lot, une pièce) : son libellé, puis ses lignes."""

    group: Annotated[str, Field(min_length=1)]
    rows: list[list[Scalar]]


class Table(_Model):
    """Bloc ``table`` : un tableau.

    Attributes:
        type: ``table``.
        columns: Ses colonnes.
        rows: Ses lignes, une liste de valeurs chacune, ou des groupes de
            lignes (``Group``).
        totals: En-tête → agrégat (``sum``, ``average``, ``count``, ``min``,
            ``max``), calculé par l'outil ; un sous-total par groupe.
        caption: Sa légende.
    """

    type: Literal["table"]
    columns: Annotated[list[Column], Field(min_length=1)]
    rows: list[list[Scalar] | Group]
    totals: dict[str, Aggregate] | None = None
    caption: str | None = None


class Note(_Model):
    """Bloc ``note`` : un encadré, ce que le lecteur doit retenir ou une mise en garde.

    Attributes:
        type: ``note``.
        text: Son texte.
        title: Son titre.
    """

    type: Literal["note"]
    text: str
    title: str | None = None


class Quote(_Model):
    """Bloc ``quote`` : une citation.

    Attributes:
        type: ``quote``.
        text: Le texte cité.
        source: Sa source.
    """

    type: Literal["quote"]
    text: str
    source: str | None = None


type ChartKind = Literal["bar", "horizontal_bar", "line", "pie"]
"""Type de graphique : barres, barres horizontales, courbe ou camembert."""


class Series(_Model):
    """Une série d'un graphique aux données en ligne.

    Attributes:
        name: Son nom, dans la légende.
        values: Ses valeurs, une par libellé.
    """

    name: str
    values: list[Scalar]


class FromTable(_Model):
    """Les données du tableau qui précède le graphique : ses colonnes, par en-tête."""

    categories: Annotated[str, Field(description="En-tête de la colonne des libellés.")]
    values: Annotated[list[str], Field(min_length=1, description="En-têtes des colonnes de nombres.")]


class Chart(_Model):
    """Bloc ``chart`` : un graphique, si l'outil le permet (``blocks``).

    Ses données viennent du tableau qui le précède (``from_table``), ou sont données en ligne (``categories`` et ``series``) : l'un ou l'autre.

    Attributes:
        type: ``chart``.
        kind: ``bar``, ``horizontal_bar``, ``line`` ou ``pie``.
        title: Son titre.
        categories: Les libellés, pour des données en ligne.
        series: Les séries, pour des données en ligne.
        value_type: Le type des valeurs en ligne ; ``from_table`` prend celui
            des colonnes.
        from_table: Les colonnes du tableau qui précède.
        caption: Sa légende.
    """

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
            raise ValueError("un graphique prend ses données soit de 'categories' et 'series', soit de 'from_table' (le tableau qui le précède)")
        if inline and (self.categories is None or self.series is None):
            raise ValueError("'categories' et 'series' vont ensemble")
        if self.from_table is not None and self.value_type is not None:
            raise ValueError("'value_type' ne sert qu'aux données en ligne : from_table prend le type des colonnes")
        return self


type ImageWidth = Literal["full", "half", "small"]
"""Largeur d'une image : toute la largeur, la moitié, ou petite."""


class Image(_Model):
    """Bloc ``image`` : une image PNG ou JPEG, si l'outil le permet (``blocks``).

    Attributes:
        type: ``image``.
        path: « <dossier>/<chemin> » d'un fichier d'un dossier qui a le droit
            ``read`` : l'outil le lit lui-même.
        width: ``full`` (par défaut), ``half`` ou ``small`` (un logo).
        caption: Sa légende, dessous.
        alt: Ce que montre l'image pour qui ne la voit pas ; la légende à
            défaut.
    """

    type: Literal["image"]
    path: str
    width: ImageWidth = "full"
    caption: str | None = None
    alt: str | None = None


MAX_SIGNERS: Final = 4
"""Signataires au plus d'un bloc signature."""


class Signature(_Model):
    """Bloc ``signature`` : la zone de signature, si l'outil le permet (``blocks``).

    Attributes:
        type: ``signature``.
        signers: Un libellé par signataire (« Le client »), ``MAX_SIGNERS`` au
            plus.
        mention: La mention au-dessus des signatures (« Bon pour accord »).
    """

    type: Literal["signature"]
    signers: Annotated[list[str], Field(min_length=1, max_length=MAX_SIGNERS)]
    mention: str | None = None


class Contents(_Model):
    """Bloc ``toc`` : le sommaire, tiré des titres, si l'outil le permet (``blocks``).

    Attributes:
        type: ``toc``.
    """

    type: Literal["toc"]


MAX_FIELDS: Final = 30
"""Champs au plus d'un bloc fields."""
MAX_FIELD_LABEL: Final = 80
"""Longueur au plus d'un libellé de champ."""
MAX_FIELD_VALUE: Final = 500
"""Longueur au plus d'une valeur de champ."""
MAX_CODE_LINES: Final = 200
"""Lignes au plus d'un bloc code."""


class Fields(_Model):
    """Des libellés et leurs valeurs : une paire est une liste de deux textes (plutôt qu'un tuple, que tous les fournisseurs n'acceptent pas dans un schéma)."""

    type: Literal["fields"]
    items: Annotated[
        list[Annotated[list[str], Field(min_length=2, max_length=2)]],
        Field(min_length=1, max_length=MAX_FIELDS),
    ]


class Code(_Model):
    """Bloc ``code`` : du texte préformaté (code, commande, journal), écrit tel quel, si l'outil le permet (``blocks``).

    Attributes:
        type: ``code``.
        text: Le texte, ``MAX_CODE_LINES`` lignes au plus.
    """

    type: Literal["code"]
    text: str


class TaxColumns(_Model):
    """Les colonnes du tableau qui précède, par en-tête : les montants HT et leurs taux de TVA (l'aide du bloc les décrit)."""

    amount: str
    rate: str


class InvoiceTotals(_Model):
    """Bloc ``invoice_totals`` : le récapitulatif de TVA d'une facture, si l'outil le permet (``blocks``).

    L'outil écrit le total HT, la TVA de chaque taux, le total TTC et, avec ``paid``, l'acompte et le net à payer.

    Attributes:
        type: ``invoice_totals``.
        from_table: Les colonnes des montants HT et des taux du tableau qui
            précède.
        paid: L'acompte déjà versé.
    """

    type: Literal["invoice_totals"]
    from_table: TaxColumns
    paid: Scalar | None = None


MAX_PARTICIPANTS: Final = 100
"""Participants au plus d'une feuille d'émargement."""
MAX_SESSIONS: Final = 10
"""Séances au plus d'une feuille d'émargement."""
MAX_ATTENDANCE_LABEL: Final = 80
"""Longueur au plus d'un libellé de la feuille d'émargement."""


class Attendance(_Model):
    """Une feuille d'émargement : une ligne par participant, une case par session."""

    type: Literal["attendance"]
    participants: Annotated[
        list[Annotated[str, Field(min_length=1)]],
        Field(min_length=1, max_length=MAX_PARTICIPANTS),
    ]
    sessions: Annotated[
        list[Annotated[str, Field(min_length=1)]],
        Field(min_length=1, max_length=MAX_SESSIONS),
    ]


type Block = Annotated[
    Heading | Paragraph | ListBlock | Table | Note | Quote,
    Field(discriminator="type"),
]
"""Les blocs que le modèle voit sans bloc facultatif."""
type WrittenBlock = Annotated[
    Heading | Paragraph | ListBlock | Table | Note | Quote | Chart | Image | Signature | Contents | Fields | Code | InvoiceTotals | Attendance,
    Field(discriminator="type"),
]
"""Tous les blocs : ce qu'une écriture porte, quel que soit l'outil. Une liste
à cocher (``CheckList``) y passe comme liste : elle en est une.
"""


STYLED_BLOCKS: Final = ("heading", "paragraph", "list", "table", "note", "quote")
"""Les blocs qui prennent un style proposé (``style``), par leur type."""
STYLE_SAID: Final = "Un style proposé pour ce bloc (voir Styles proposés) ; sinon, rien."
"""Description du champ ``style`` d'un bloc, lue par le modèle."""

type Proposed = tuple[tuple[str, tuple[str, ...]], ...]
"""Styles proposés : (type de bloc, noms des styles)."""


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
    """Les blocs du schéma pour ces blocs facultatifs, dans un ordre fixe.

    ``styles`` : les styles proposés, par type de bloc (le champ ``style`` des blocs qui
    en ont).
    """
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
    kinds = [_styled(kind, proposed[name]) if (name := _TYPES[kind]) in proposed else kind for kind in kinds]
    kinds += [
        kind
        for name, kind in (
            ("chart", Chart),
            ("image", Image),
            ("signature", Signature),
            ("toc", Contents),
            ("fields", Fields),
            ("code", Code),
            ("invoice_totals", InvoiceTotals),
            ("attendance", Attendance),
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
"""Action de l'outil : écrire ou lire."""
type TemplatedAction = Literal["write", "read", "template"]
"""Les actions d'un outil monté avec des modèles de document."""

BLOCKS_SAID: Final = "Pour write : les blocs, dans l'ordre."
"""Description du champ ``blocks``, lue par le modèle."""


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
    title: Annotated[str | None, Field(description="Pour write : le titre, en tête et dans les propriétés.")] = None
    blocks: Annotated[
        list[Block] | None,
        Field(max_length=MAX_BLOCKS, description=BLOCKS_SAID),
    ] = None
    overwrite: Annotated[bool | None, Field(description="Pour write : remplacer un fichier existant.")] = None

    @model_validator(mode="after")
    def _fields_of_the_action(self) -> Self:
        writing = ("title", "blocks", "overwrite")
        stray = sorted(
            name
            for name in (*writing, *self.READ_FIELDS)
            # « overwrite: false » sur une lecture ne dit rien : pas une faute.
            if (name in writing) != (self.action == "write") and getattr(self, name) not in (None, False)
        )
        if stray:
            raise ValueError(f"{', '.join(stray)} ne sert pas à {self.action}")
        sections = getattr(self, "sections", None)
        if self.action == "write" and self.blocks is None and sections is None:
            raise ValueError("write attend 'blocks', le contenu du document")
        return self

    def request(self) -> WriteArguments | ReadArguments | TemplateArguments:
        """La demande que l'appel porte : écrire (``WriteArguments``), lire (``ReadArguments``) ou lire un modèle (``TemplateArguments``)."""
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


ACTION_SAID: Final = "write (par défaut) : écrire ; read : lire un fichier (droit read) ; template : lire un modèle de document."
"""Description du champ ``action``, lue par le modèle."""
TEMPLATE_SAID: Final = "Pour write : le modèle suivi, ou none (document libre) ; pour template : le modèle à lire."
"""Description du champ ``template``, lue par le modèle."""
SECTIONS_SAID: Final = "Pour write avec un modèle : le contenu de chaque section, par son nom (voir action template) ; l'outil écrit les titres de section."
"""Description du champ ``sections``, lue par le modèle."""
_SUFFIXES: Final[Mapping[str, str]] = {
    "chart": "Chart",
    "image": "Image",
    "signature": "Signature",
    "toc": "Toc",
    "checkboxes": "Checkboxes",
    "fields": "Fields",
    "code": "Code",
    "invoice_totals": "InvoiceTotals",
    "attendance": "Attendance",
}
"""Le nom de l'appel selon ses blocs facultatifs : ``WordChartImageCall``…"""


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
    proposed: Proposed = tuple((kind, tuple(sorted(set(names)))) for kind, names in sorted((styles or {}).items()) if kind in STYLED_BLOCKS and names)
    return _variant(base, chosen, templates, proposed)


@cache
def _variant(
    base: type[DocumentCall],
    chosen: tuple[str, ...],
    templates: tuple[str, ...],
    proposed: Proposed = (),
) -> type[DocumentCall]:
    """Les arguments de l'outil élargis aux blocs facultatifs choisis, aux modèles et aux styles proposés ; la base telle quelle sans rien de tout cela."""
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
    suffix = "".join(_SUFFIXES[name] for name in chosen) + ("Template" if templates else "") + ("Styled" if proposed else "")
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
        raise ValueError("write attend 'template' : le modèle suivi, ou none pour un document libre")
    sections = getattr(self, "sections", None)
    if action == "write" and template == NO_TEMPLATE and sections is not None:
        raise ValueError("un document libre (template none) s'écrit par 'blocks', pas 'sections'")
    if action == "write" and template not in (None, NO_TEMPLATE):
        if self.blocks is not None:
            raise ValueError(f"un document qui suit le modèle {template} s'écrit par 'sections' (le contenu de chaque section, par son nom), pas 'blocks'")
        if sections is None:
            raise ValueError(f"write avec le modèle {template} attend 'sections' : le contenu de chaque section, par son nom (action template pour les lire)")
    if action == "read" and template is not None:
        raise ValueError("template ne sert pas à read")
    if action == "read" and sections is not None:
        raise ValueError("sections ne sert pas à read")
    if action == "template":
        if template is None or template == NO_TEMPLATE:
            raise ValueError("action template attend 'template' : le nom du modèle à lire")
        stray = sorted(name for name in (*self.READ_FIELDS, "title", "blocks", "overwrite", "sections") if getattr(self, name, None) not in (None, False))
        if stray:
            raise ValueError(f"{', '.join(stray)} ne sert pas à template")
    return self


WithCharts: Final = variant(DocumentCall, blocks=("chart",))
"""Le même appel, avec le bloc ``chart`` (tests du vocabulaire commun)."""


def parse(model: type[DocumentCall], arguments: Mapping[str, JsonValue]) -> WriteArguments | ReadArguments | TemplateArguments:
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
            if not names and (key == "title" or (key == "description" and "properties" in mapping) or (key == "default" and value is None)):
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
        """Note une erreur : le document sera refusé."""
        self.errors.append(said)

    def warn(self, said: str) -> None:
        """Note un avertissement, une fois, ``MAX_WARNINGS`` au plus."""
        if said not in self.warnings and len(self.warnings) < MAX_WARNINGS:
            self.warnings.append(said)

    def raise_if_any(self) -> None:
        """Lève ``ToolError`` s'il y a des erreurs : « Document refusé », puis les ``MAX_ERRORS_SAID`` premières."""
        if not self.errors:
            return
        shown = self.errors[:MAX_ERRORS_SAID]
        more = len(self.errors) - len(shown)
        tail = [f"… et {more} autre(s)."] if more else []
        raise ToolError("\n".join(["Document refusé :", *(f"- {e}" for e in shown), *tail]))
