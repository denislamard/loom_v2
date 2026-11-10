# SPDX-License-Identifier: Apache-2.0
"""Le style d'un document ``word`` : des styles nommés, dans des fichiers YAML.

Un fichier de style est une liste de **styles nommés** — ``text``, ``document_title``,
``heading1``… —, chacun avec ses propriétés (police, taille, couleur, espacements,
filets), et quelques **réglages** (``colors``, ``page``, ``numbering``, ``language``,
``labels``, ``image``, ``chart``). Trois fichiers, du plus général au plus précis :

1. ``styles/default.yaml``, livré avec loom : le style maison ;
2. la charte de l'outil (``style:``) ;
3. le style d'un modèle de document (``templates.<nom>.style``).

**Les fichiers d'abord** : chaque style complète le même style des fichiers précédents,
propriété par propriété. **L'héritage ensuite** : un style prend les propriétés de sa
``base`` (``text`` sans base), déjà complétée par les fichiers, puis les siennes. Le
code des outils n'a aucune valeur visuelle : tout vient de ces fichiers.

Jusqu'au lot 3 de la refonte, le style d'un modèle porte aussi la disposition de ses
sections (``sections``, ``beside``), que la charte n'a pas.

Le modèle de langage n'en voit rien. Module léger — Pydantic, PyYAML et ``_templates``
— : la config l'importe pour valider une référence, sans importer l'outil.
"""

import difflib
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Annotated, Any, Final, Literal, Self, cast

import yaml
from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

from loom_ia.tools_builtin._templates import Template, key

DEFAULT_FILE: Final = Path(__file__).with_name("styles") / "default.yaml"
MAX_STYLE_CHARS: Final = 20_000
MAX_LOGO_BYTES: Final = 2_000_000
MAX_BAND_TEXT: Final = 200
MAX_BAND_LINES: Final = 4
MAX_STYLES: Final = 80
MAX_LABEL: Final = 40
# Les polices présentes sur presque tous les postes (Windows, macOS, LibreOffice les
# remplace par des équivalentes métriques) : une autre s'affiche ailleurs dans une
# police de remplacement, et ``loom validate`` le signale.
SAFE_FONTS: Final = frozenset(
    {
        "arial",
        "calibri",
        "cambria",
        "georgia",
        "times new roman",
        "verdana",
        "tahoma",
        "trebuchet ms",
        "garamond",
        "courier new",
    }
)
# Les champs d'un en-tête ou d'un pied de page.
PLACEHOLDERS: Final = frozenset({"page", "pages", "title"})
_PLACEHOLDER: Final = re.compile(r"\{([^{}]*)\}")
_HEX: Final = re.compile(r"^#?[0-9A-Fa-f]{6}$")
_FONT: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 \-]{0,59}$")
_NAME: Final = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_RULE: Final = re.compile(r"^(\d+(?:[.,]\d+)?)\s*pt\s+(\S+)$")
NONE: Final = "none"

type Align = Literal["left", "center", "right", "justify"]
type Position = Literal["left", "center", "right"]
type Borders = Literal["grid", "horizontal", "none"]
type TableWidth = Literal["full", "content"]
type Numbering = Literal["none", "decimal", "article"]
type PageSize = Literal["A4", "A5", "Letter"]
type Orientation = Literal["portrait", "landscape"]
type Language = Literal["fr", "en"]
# La disposition d'une section (jusqu'au lot 3) : son titre, la taille de son texte.
type TitleMode = Literal["visible", "hidden", "quiet"]
type TextSize = Literal["normal", "small"]

# Un centimètre en vingtièmes de point (twips).
CM: Final = 567
PAGE_SIZES: Final[Mapping[str, tuple[int, int]]] = {
    "A4": (11906, 16838),
    "A5": (8391, 11906),
    "Letter": (12240, 15840),
}

# Les styles prévus, et la base de chacun quand le fichier n'en donne pas.
BASES: Final[Mapping[str, str | None]] = {
    "text": None,
    "document_title": "text",
    "heading1": "text",
    "heading2": "text",
    "heading3": "text",
    "paragraph": "text",
    "list": "text",
    "note": "text",
    "caption": "text",
    "quote": "text",
    "quote_source": "quote",
    "table": "text",
    "table_header": "table",
    "table_total": "table",
    "box": "text",
    "toc": "text",
    "signature": "text",
    "signature_mention": "text",
    "header": "text",
    "footer": "text",
    "watermark": "text",
}
# Les propriétés propres à certains styles, et ceux (avec leurs dérivés) qui les portent.
_OWNERS: Final[Mapping[str, frozenset[str]]] = {
    **dict.fromkeys(("borders", "line", "bands", "width", "position"), frozenset({"table"})),
    **dict.fromkeys(
        ("left", "center", "right", "lines", "logo", "logo_width", "logo_position", "first_page"),
        frozenset({"header", "footer"}),
    ),
    "numbered": frozenset({"caption"}),
    "levels": frozenset({"toc"}),
    "page_numbers": frozenset({"toc"}),
    "text": frozenset({"watermark"}),
}
# Les réglages : des clés du fichier qui ne sont pas des styles.
SETTINGS: Final = frozenset({"colors", "page", "numbering", "language", "labels", "image", "chart"})
# La disposition des sections d'un modèle, jusqu'au lot 3.
LAYOUT: Final = frozenset({"sections", "beside"})
# Les clés du format de la 2.0, refusées avec ce qui les remplace.
OLD_KEYS: Final[Mapping[str, str]] = {
    "title": "document_title",
    "headings": "heading1, heading2, heading3 (et numbering : decimal, article)",
    "paragraphs": "text (line → line_spacing, after → space_after, justify → align: justify)",
    "fonts": "font, sur text et sur chaque titre",
    "tables": "table et table_header (align → position, header.fill → table_header.fill)",
    "captions": "caption (numbered)",
    "images": "image (grid)",
}

# Les libellés que l'outil écrit dans le document, par langue.
LABEL_KEYS: Final = ("table", "figure", "contents", "date", "signature", "article", "total")
LABELS: Final[Mapping[Language, Mapping[str, str]]] = {
    "fr": {
        "table": "Tableau",
        "figure": "Figure",
        "contents": "Sommaire",
        "date": "Date :",
        "signature": "Signature :",
        "article": "Article",
        "total": "Total",
    },
    "en": {
        "table": "Table",
        "figure": "Figure",
        "contents": "Contents",
        "date": "Date:",
        "signature": "Signature:",
        "article": "Article",
        "total": "Total",
    },
}


# --- Les valeurs ------------------------------------------------------------------------


def _text_color(value: object) -> object:
    """Les couleurs YAML écrites sans guillemets : ``1F3864`` reste une chaîne, ``000000``
    aussi (un nombre pour YAML)."""
    return f"{value:06d}" if isinstance(value, int) and not isinstance(value, bool) else value


def _color(value: str) -> str:
    """Six chiffres hexadécimaux (en majuscules), ou le nom d'une couleur de ``colors``."""
    if _HEX.match(value):
        return value.lstrip("#").upper()
    if _NAME.match(value):
        return value
    raise ValueError(
        f"couleur {value!r} : six chiffres hexadécimaux (« 1F3864 »), ou le nom d'une "
        "couleur définie dans colors"
    )


def _fill(value: str) -> str:
    return NONE if value == NONE else _color(value)


def _hex(value: str) -> str:
    if not _HEX.match(value):
        raise ValueError(f"couleur {value!r} : six chiffres hexadécimaux (« 1F3864 »)")
    return value.lstrip("#").upper()


def _rule(value: str) -> str:
    """Un filet : « épaisseur couleur » (``1pt 2F5496``, ``3pt brand``), ou ``none``."""
    if value == NONE:
        return value
    found = _RULE.match(value.strip())
    if found is None:
        raise ValueError(f"filet {value!r} : « épaisseur couleur », ex. 1pt 2F5496, ou none")
    width = float(found.group(1).replace(",", "."))
    if not 0.25 <= width <= 6:
        raise ValueError(f"filet {value!r} : une épaisseur de 0,25 à 6 pt")
    return f"{width:g}pt {_color(found.group(2))}"


def _font(value: str) -> str:
    if not _FONT.match(value):
        raise ValueError(
            f"police {value!r} : un nom de police (lettres, chiffres, espaces, tirets ; "
            "60 caractères au plus)"
        )
    return value


def _band_text(value: str) -> str:
    if len(value) > MAX_BAND_TEXT:
        raise ValueError(f"texte de {len(value)} caractères, au-delà de {MAX_BAND_TEXT}")
    unknown = sorted({name for name in _PLACEHOLDER.findall(value) if name not in PLACEHOLDERS})
    if unknown:
        old = " ({titre} s'écrit {title} depuis 2.1.0)" if "titre" in unknown else ""
        raise ValueError(
            f"champ inconnu {', '.join('{' + n + '}' for n in unknown)} : seuls {{page}}, "
            f"{{pages}} et {{title}} sont remplacés (une date fait partie du contenu){old}"
        )
    return value


def _color_name(value: str) -> str:
    if _HEX.match(value) or not _NAME.match(value):
        raise ValueError(
            f"nom de couleur {value!r} : des minuscules, chiffres et _, une lettre d'abord, "
            "et pas six chiffres hexadécimaux (brand, grey)"
        )
    return value


def _style_name(value: str) -> str:
    if not _NAME.match(value):
        raise ValueError(
            f"nom de style {value!r} : des minuscules, chiffres et _, une lettre d'abord "
            "(40 caractères au plus)"
        )
    return value


type Color = Annotated[str, BeforeValidator(_text_color), AfterValidator(_color)]
type Fill = Annotated[str, BeforeValidator(_text_color), AfterValidator(_fill)]
type Hex = Annotated[str, BeforeValidator(_text_color), AfterValidator(_hex)]
type Rule = Annotated[str, AfterValidator(_rule)]
type FontName = Annotated[str, AfterValidator(_font)]
type BandText = Annotated[str, AfterValidator(_band_text)]
type StyleName = Annotated[str, AfterValidator(_style_name)]
type ColorName = Annotated[str, AfterValidator(_color_name)]
type Points = Annotated[float, Field(ge=0, le=72)]


class _Spec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# --- Le fichier de style -----------------------------------------------------------------


class Props(_Spec):
    """Les propriétés d'un style nommé, telles qu'écrites ; toutes facultatives."""

    base: StyleName | None = None
    font: FontName | None = None
    size: Annotated[float, Field(ge=6, le=72)] | None = None
    color: Color | None = None
    fill: Fill | None = None
    bold: bool | None = None
    italic: bool | None = None
    underline: bool | None = None
    uppercase: bool | None = None
    align: Align | None = None
    space_before: Points | None = None
    space_after: Points | None = None
    line_spacing: Annotated[float, Field(ge=1, le=3)] | None = None
    indent: Annotated[float, Field(ge=0, le=10)] | None = None
    border_top: Rule | None = None
    border_bottom: Rule | None = None
    border_left: Rule | None = None
    border: Rule | None = None
    # Le tableau.
    borders: Borders | None = None
    line: Rule | None = None
    bands: Fill | None = None
    width: TableWidth | None = None
    position: Position | None = None
    # L'en-tête et le pied de page : trois zones, des lignes centrées, un logo (son
    # fichier, relatif à resources_dir ; ses octets une fois lu au chargement).
    left: BandText | None = None
    center: BandText | None = None
    right: BandText | None = None
    lines: Annotated[list[BandText], Field(max_length=MAX_BAND_LINES)] | None = None
    logo: str | bytes | None = None
    logo_width: Annotated[float, Field(ge=0.5, le=17)] | None = None
    logo_position: Position | None = None
    first_page: bool | None = None
    # La légende, le sommaire, le filigrane.
    numbered: bool | None = None
    levels: Annotated[int, Field(ge=1, le=3)] | None = None
    page_numbers: bool | None = None
    text: Annotated[str, Field(max_length=40)] | None = None


class PageSpec(_Spec):
    size: PageSize | None = None
    orientation: Orientation | None = None
    margins: Annotated[float, Field(ge=0.5, le=5)] | None = None
    margin_top: Annotated[float, Field(ge=0.5, le=5)] | None = None
    margin_bottom: Annotated[float, Field(ge=0.5, le=5)] | None = None
    margin_left: Annotated[float, Field(ge=0.5, le=5)] | None = None
    margin_right: Annotated[float, Field(ge=0.5, le=5)] | None = None


class LabelsSpec(_Spec):
    table: Annotated[str, Field(min_length=1, max_length=MAX_LABEL)] | None = None
    figure: Annotated[str, Field(min_length=1, max_length=MAX_LABEL)] | None = None
    contents: Annotated[str, Field(min_length=1, max_length=MAX_LABEL)] | None = None
    date: Annotated[str, Field(min_length=1, max_length=MAX_LABEL)] | None = None
    signature: Annotated[str, Field(min_length=1, max_length=MAX_LABEL)] | None = None
    article: Annotated[str, Field(min_length=1, max_length=MAX_LABEL)] | None = None
    total: Annotated[str, Field(min_length=1, max_length=MAX_LABEL)] | None = None


class ImageSpec(_Spec):
    # Deux images ``half`` qui se suivent : côte à côte.
    grid: bool | None = None


class ChartSpec(_Spec):
    # La couleur de chaque série, dans l'ordre ; le texte, la grille, le titre.
    colors: Annotated[list[Color], Field(min_length=1, max_length=12)] | None = None
    text: Color | None = None
    grid: Color | None = None
    title: Color | None = None


class SectionTable(_Spec):
    """Les tableaux d'une section (disposition, jusqu'au lot 3) : les propriétés de ``table``."""

    size: Annotated[float, Field(ge=6, le=72)] | None = None
    borders: Borders | None = None
    line: Rule | None = None
    bands: Fill | None = None
    width: TableWidth | None = None
    position: Position | None = None
    border_top: Rule | None = None
    border_bottom: Rule | None = None


class SectionSpec(_Spec):
    """La disposition d'une section du modèle (jusqu'au lot 3)."""

    title: TitleMode | None = None
    align: Align | None = None
    size: TextSize | None = None
    box: bool | None = None
    columns: Annotated[int, Field(ge=1, le=3)] | None = None
    page_break: bool | None = None
    landscape: bool | None = None
    cover: bool | None = None
    space_before: Annotated[float, Field(ge=0, le=20)] | None = None
    table: SectionTable | None = None

    @model_validator(mode="before")
    @classmethod
    def _old(cls, value: object) -> object:
        if isinstance(value, Mapping):
            entries = cast(Mapping[object, object], value)
            if "tables" in entries:
                raise ValueError("tables s'écrit table depuis 2.1.0, avec les propriétés de table")
            return entries
        return value


class BesideSpec(_Spec):
    """Des sections posées côte à côte, et la part de chacune (en %, 100 en tout)."""

    sections: Annotated[list[str], Field(min_length=2, max_length=3)]
    widths: list[int] | None = None

    @model_validator(mode="after")
    def _widths(self) -> Self:
        if self.widths is None:
            return self
        if len(self.widths) != len(self.sections):
            raise ValueError("beside : une largeur par section")
        if any(width < 10 for width in self.widths) or sum(self.widths) != 100:
            raise ValueError("beside : des largeurs en %, de 10 au moins, qui font 100 en tout")
        return self


def _closest(name: str) -> str:
    found = difflib.get_close_matches(name, [*BASES, *SETTINGS], n=1, cutoff=0.6)
    return f" — vouliez-vous dire {found[0]} ?" if found else ""


class StyleFile(_Spec):
    """Un fichier de style lu : ses réglages, ses styles nommés, et (jusqu'au lot 3) la
    disposition des sections d'un modèle."""

    colors: dict[ColorName, Hex] | None = None
    page: PageSpec | None = None
    numbering: Numbering | None = None
    language: Language | None = None
    labels: LabelsSpec | None = None
    image: ImageSpec | None = None
    chart: ChartSpec | None = None
    styles: dict[StyleName, Props] = {}
    sections: dict[str, SectionSpec] = {}
    beside: list[BesideSpec] = []

    @model_validator(mode="before")
    @classmethod
    def _named(cls, value: object) -> object:
        """Le fichier tel qu'écrit : les réglages, puis tout autre nom est un style."""
        if not isinstance(value, Mapping):
            return value
        entries = cast(Mapping[object, object], value)
        old = [f"{name} → {OLD_KEYS[name]}" for name in OLD_KEYS if name in entries]
        if old:
            raise ValueError(
                f"style au format de la 2.0 : {' ; '.join(old)}. Un fichier de style est "
                "depuis 2.1.0 une liste de styles nommés (text, document_title, heading1…)"
            )
        data: dict[str, object] = {}
        styles: dict[str, object] = {}
        for name, given in entries.items():
            if not isinstance(name, str):
                raise ValueError(f"clé {name!r} : un nom de style ou de réglage")
            if name in SETTINGS or name in LAYOUT:
                data[name] = given
                continue
            if name == "watermark" and isinstance(given, str):
                given = {"text": given}
            given = {} if given is None else given
            if (
                name in ("header", "footer")
                and isinstance(given, Mapping)
                and isinstance(cast(Mapping[object, object], given).get("logo"), Mapping)
            ):
                raise ValueError(
                    f"{name}.logo : le fichier seul depuis 2.1.0 (logo: images/logo.png), "
                    "et logo_width, logo_position à côté"
                )
            if name not in BASES and not (
                isinstance(given, Mapping) and "base" in cast(Mapping[object, object], given)
            ):
                raise ValueError(
                    f"{name} : ni un style prévu, ni un réglage ; un style à toi dit de quel "
                    f"style il part (base: note){_closest(name)}"
                )
            styles[name] = given
        if len(styles) > MAX_STYLES:
            raise ValueError(f"{len(styles)} styles, au-delà de {MAX_STYLES}")
        data["styles"] = styles
        return data


def describe(error: ValidationError, where: str) -> list[str]:
    """Les fautes d'un fichier de style, une par ligne : « <where>.heading1.colour — … »,
    ou « <where> : … » pour une faute de tout le fichier."""
    lines: list[str] = []
    for item in error.errors(include_url=False):
        loc = [str(part) for part in item["loc"]]
        if loc[:1] == ["styles"]:
            loc = loc[1:]
        message = item["msg"].removeprefix("Value error, ")
        lines.append(f"{where}.{'.'.join(loc)} — {message}" if loc else f"{where} : {message}")
    return lines


def load_style(text: str) -> StyleFile:
    """Le texte d'un fichier de style, lu ; ``yaml.YAMLError`` ou ``ValidationError``."""
    data = yaml.safe_load(text)
    return StyleFile.model_validate(data if data is not None else {})


@cache
def default_style() -> StyleFile:
    """``styles/default.yaml`` : le style maison, livré avec loom."""
    return load_style(DEFAULT_FILE.read_text(encoding="utf-8"))


class StyleRef(BaseModel):
    """Un style : son fichier (relatif à ``resources_dir``), ou son contenu.

    Le chargeur de config lit le fichier (et ses logos) et range le style dans
    ``spec`` ; ``file`` n'est alors plus gardé. En config : ``style: styles/charte.yaml``,
    ou le style écrit en ligne (``style: { heading1: { color: 0B5D3B } }``).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    file: Path | None = None
    spec: StyleFile | None = None

    @model_validator(mode="after")
    def _one(self) -> Self:
        if (self.file is None) == (self.spec is None):
            raise ValueError("un style se donne par son fichier (file) ou son contenu (spec)")
        return self


def short_style(value: object) -> object:
    """``style: fichier.yaml`` (un chemin), ou un style écrit en ligne (une table)."""
    if isinstance(value, str | Path):
        return {"file": value}
    if isinstance(value, Mapping):
        entries = cast(Mapping[object, object], value)
        if entries and set(entries) <= {"file", "spec"}:
            return entries
        return {"spec": entries}
    return value


type StyleField = Annotated[StyleRef | None, BeforeValidator(short_style)]


# --- Contrôles, au chargement -------------------------------------------------------------


def check_charte(spec: StyleFile, where: str) -> None:
    """La charte de l'outil : ni ``sections`` ni ``beside``, qui disposent un modèle."""
    if spec.sections or spec.beside:
        raise ValueError(
            f"{where} : sections et beside disposent les sections d'un modèle ; ils se "
            "déclarent dans le style du modèle (templates.<nom>.style), pas dans la charte"
        )


def check_layout(spec: StyleFile, template: Template, where: str) -> None:
    """La disposition d'un modèle, comparée à son plan ; ``ValueError`` qui dit quoi corriger."""
    plan = [section.key for section in template.sections]
    titles = {section.key: section.title for section in template.sections}
    outline = " · ".join(section.title for section in template.sections) or "(aucune section)"
    unknown = [name for name in spec.sections if key(name) not in titles]
    for group in spec.beside:
        unknown += [name for name in group.sections if key(name) not in titles]
    if unknown:
        raise ValueError(
            f"{where} : section(s) hors du modèle {template.name} : "
            f"{', '.join(f'« {n} »' for n in dict.fromkeys(unknown))} (plan : {outline})"
        )
    by_key: dict[str, SectionSpec] = {}
    for name, section in spec.sections.items():
        if key(name) in by_key:
            raise ValueError(f"{where} : section « {name} » donnée deux fois")
        by_key[key(name)] = section
    placed: set[str] = set()
    for group in spec.beside:
        keys = [key(name) for name in group.sections]
        if len(set(keys)) != len(keys) or placed & set(keys):
            raise ValueError(f"{where} : beside, une section n'est posée qu'une fois")
        placed |= set(keys)
        positions = [plan.index(k) for k in keys]
        if positions != list(range(positions[0], positions[0] + len(positions))):
            raise ValueError(
                f"{where} : beside, {' et '.join(f'« {titles[k]} »' for k in keys)} doivent "
                "se suivre dans le plan du modèle"
            )
        for index, k in enumerate(keys):
            section = by_key.get(k)
            if section is None:
                continue
            if section.columns not in (None, 1) or section.landscape or section.cover:
                raise ValueError(
                    f"{where} : « {titles[k]} » est côte à côte (beside) : ni columns, ni "
                    "landscape, ni cover"
                )
            if index and (section.page_break or section.space_before):
                raise ValueError(
                    f"{where} : « {titles[k]} » suit une autre section sur la même ligne "
                    "(beside) : page_break et space_before vont à la première"
                )
    for k, section in by_key.items():
        if section.cover:
            if plan.index(k) != 0:
                raise ValueError(
                    f"{where} : cover (page de garde) va à la première section du modèle, "
                    f"« {titles[plan[0]]} »"
                )
            if section.landscape or section.columns not in (None, 1) or section.box:
                raise ValueError(
                    f"{where} : une page de garde (cover) n'a ni landscape, ni columns, ni box"
                )


def logos(spec: StyleFile) -> list[tuple[str, str | bytes]]:
    """Les logos d'un fichier de style : (nom du style, fichier ou octets)."""
    return [(name, props.logo) for name, props in spec.styles.items() if props.logo is not None]


# --- Le style résolu ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Look:
    """Un style résolu : ses propriétés, héritées de sa base puis les siennes ; ``None``
    pour ce que rien ne règle (la valeur de Word)."""

    font: str | None = None
    size: float | None = None
    color: str | None = None
    fill: str | None = None
    bold: bool | None = None
    italic: bool | None = None
    underline: bool | None = None
    uppercase: bool | None = None
    align: Align | None = None
    space_before: float | None = None
    space_after: float | None = None
    line_spacing: float | None = None
    indent: float | None = None
    border_top: str | None = None
    border_bottom: str | None = None
    border_left: str | None = None
    border: str | None = None
    borders: Borders | None = None
    line: str | None = None
    bands: str | None = None
    width: TableWidth | None = None
    position: Position | None = None
    left: str | None = None
    center: str | None = None
    right: str | None = None
    lines: tuple[str, ...] | None = None
    logo: bytes | None = field(default=None, repr=False)
    logo_width: float | None = None
    logo_position: Position | None = None
    first_page: bool | None = None
    numbered: bool | None = None
    levels: int | None = None
    page_numbers: bool | None = None
    text: str | None = None


_LOOK_FIELDS: Final = frozenset(Look.__dataclass_fields__)


@dataclass(frozen=True, slots=True)
class Labels:
    """Les libellés que l'outil écrit dans le document, et sa langue."""

    language: Language = "fr"
    table: str = "Tableau"
    figure: str = "Figure"
    contents: str = "Sommaire"
    date: str = "Date :"
    signature: str = "Signature :"
    article: str = "Article"
    total: str = "Total"


FRENCH: Final = Labels()


@dataclass(frozen=True, slots=True)
class Logo:
    data: bytes = field(repr=False)
    # Largeur dans la page, en cm.
    width: float
    position: Position


@dataclass(frozen=True, slots=True)
class Band:
    left: str = ""
    center: str = ""
    right: str = ""
    lines: tuple[str, ...] = ()
    logo: Logo | None = None
    first_page: bool = True

    @property
    def empty(self) -> bool:
        return not (self.left or self.center or self.right or self.lines or self.logo)


@dataclass(frozen=True, slots=True)
class ChartLook:
    """Les couleurs des graphiques : les séries, le texte, la grille, le titre."""

    colors: tuple[str, ...]
    text: str
    grid: str
    title: str


@dataclass(frozen=True, slots=True)
class SectionLook:
    """La disposition d'une section (jusqu'au lot 3)."""

    title: TitleMode = "visible"
    align: Align | None = None
    size: TextSize = "normal"
    box: bool = False
    columns: int = 1
    page_break: bool = False
    landscape: bool = False
    cover: bool = False
    # Espace avant la section, en twips.
    space_before: int = 0
    # Le style des tableaux de la section, s'il diffère de ``table``.
    table: Look | None = None


PLAIN_SECTION: Final = SectionLook()


@dataclass(frozen=True, slots=True)
class Beside:
    # Les clés des sections (``_templates.key``) et la part de chacune (somme 1).
    keys: tuple[str, ...]
    widths: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class Style:
    """Un style complet, prêt à rendre : chaque style nommé résolu, et les réglages."""

    looks: Mapping[str, Look]
    # Ce que chaque style écrit lui-même (les fichiers fusionnés, couleurs résolues) : un
    # style Word reprend son style de base, et n'écrit que ce qui lui est propre.
    declared: Mapping[str, Mapping[str, Any]]
    bases: Mapping[str, str | None]
    page: tuple[int, int] = PAGE_SIZES["A4"]
    landscape: bool = False
    # Haut, bas, gauche, droite, en twips.
    margins: tuple[int, int, int, int] = (1134, 1134, 1134, 1134)
    numbering: Numbering = "none"
    labels: Labels = FRENCH
    image_grid: bool = False
    chart: ChartLook = ChartLook((), "", "", "")
    # La disposition des sections, par clé de titre (``_templates.key``), jusqu'au lot 3.
    sections: Mapping[str, SectionLook] = field(default_factory=dict[str, SectionLook])
    beside: tuple[Beside, ...] = ()

    @property
    def language(self) -> Language:
        return self.labels.language

    @property
    def text_width(self) -> int:
        """La largeur utile d'une page, en twips (couchée si le style l'est)."""
        width = self.page[1] if self.landscape else self.page[0]
        return width - self.margins[2] - self.margins[3]

    def look(self, name: str) -> Look:
        return self.looks[name]

    def section(self, title: str) -> SectionLook:
        return self.sections.get(key(title), PLAIN_SECTION)

    def chain(self, name: str) -> list[str]:
        """Le style et ses bases, du plus précis à ``text``."""
        said: list[str] = []
        current: str | None = name
        while current is not None:
            said.append(current)
            current = self.bases.get(current)
        return said

    def between(self, name: str, stop: str | None) -> dict[str, Any]:
        """Ce que ``name`` écrit en plus de ``stop`` : les propriétés déclarées par ``name``
        et ses bases jusqu'à ``stop`` (exclu), la plus précise gagnant. Une propriété
        négative (``false``, ``none``) que ``stop`` a déjà n'y est pas."""
        chain = self.chain(name)
        if stop in chain:
            chain = chain[: chain.index(stop)]
        said: dict[str, Any] = {}
        for current in reversed(chain):
            said.update(self.declared.get(current, {}))
        reference = self.looks.get(stop) if stop is not None else None
        for prop in [p for p, value in said.items() if value is False or value == NONE]:
            before = getattr(reference, prop, None) if reference is not None else None
            if before is None or before is False or before == NONE:
                del said[prop]
        return said

    def band(self, name: Literal["header", "footer"]) -> Band | None:
        """L'en-tête ou le pied de page ; ``None`` s'il n'écrit rien."""
        look = self.looks[name]
        logo = (
            Logo(look.logo, look.logo_width or 3.0, look.logo_position or "left")
            if look.logo is not None
            else None
        )
        band = Band(
            left=look.left or "",
            center=look.center or "",
            right=look.right or "",
            lines=look.lines or (),
            logo=logo,
            first_page=look.first_page if look.first_page is not None else True,
        )
        return None if band.empty and band.first_page else band

    @property
    def header(self) -> Band | None:
        return self.band("header")

    @property
    def footer(self) -> Band | None:
        return self.band("footer")

    @property
    def watermark(self) -> str | None:
        return self.looks["watermark"].text or None


def resolve(*files: StyleFile | None) -> Style:
    """Le style complet : ``default.yaml``, puis chaque fichier donné, propriété par
    propriété ; puis l'héritage. ``ValueError`` qui dit la faute (couleur inconnue, base
    inconnue ou en boucle, propriété hors de son style)."""
    given = [default_style(), *(file for file in files if file is not None)]
    colors: dict[str, str] = {}
    for file in given:
        colors.update(file.colors or {})
    declared: dict[str, dict[str, Any]] = {}
    for file in given:
        for name, props in file.styles.items():
            declared.setdefault(name, {}).update(props.model_dump(exclude_unset=True))
    faults: list[str] = []
    for name, props in declared.items():
        for prop, value in list(props.items()):
            props[prop] = _named_colors(name, prop, value, colors, faults)
    bases = _bases(declared, faults)
    _check_owners(declared, bases, faults)
    chart = _chart(given, colors, faults)
    if faults:
        raise ValueError(" ; ".join(dict.fromkeys(faults)))
    looks: dict[str, Look] = {}
    for name in declared:
        looks[name] = _look(name, declared, bases, looks)
    for name in BASES:
        if name not in looks:
            looks[name] = _look(name, declared, bases, looks)
    page = _page(given)
    language: Language = "fr"
    labels: dict[str, str] = {}
    numbering: Numbering = "none"
    grid = False
    for file in given:
        language = file.language or language
        numbering = file.numbering or numbering
        if file.image is not None and file.image.grid is not None:
            grid = file.image.grid
    for file in given:
        if file.labels is not None:
            labels.update(file.labels.model_dump(exclude_none=True))
    said = Labels(language=language, **{**LABELS[language], **labels})
    sections, beside = _layout(given, looks.get("table", Look()))
    return Style(
        looks=looks,
        declared=declared,
        bases=bases,
        page=PAGE_SIZES[page["size"]],
        landscape=page["orientation"] == "landscape",
        margins=(
            round(page["margin_top"] * CM),
            round(page["margin_bottom"] * CM),
            round(page["margin_left"] * CM),
            round(page["margin_right"] * CM),
        ),
        numbering=numbering,
        labels=said,
        image_grid=grid,
        chart=chart,
        sections=sections,
        beside=beside,
    )


_COLORED: Final = frozenset({"color", "fill", "bands"})
_RULES: Final = frozenset({"border_top", "border_bottom", "border_left", "border", "line"})


def _named_colors(
    name: str, prop: str, value: Any, colors: Mapping[str, str], faults: list[str]
) -> Any:
    """Une couleur nommée remplacée par sa valeur ; une inconnue, dite."""

    def color(said: str) -> str:
        if said == NONE or _HEX.match(said):
            return said
        if said in colors:
            return colors[said]
        known = ", ".join(sorted(colors)) or "aucune"
        faults.append(f"{name}.{prop} : couleur « {said} » inconnue (colors : {known})")
        return said

    if prop in _COLORED and isinstance(value, str):
        return color(value)
    if prop in _RULES and isinstance(value, str) and value != NONE:
        width, said = value.split(" ", 1)
        return f"{width} {color(said)}"
    return value


def _bases(declared: Mapping[str, Mapping[str, Any]], faults: list[str]) -> dict[str, str | None]:
    bases: dict[str, str | None] = {}
    for name in {*BASES, *declared}:
        written = declared.get(name, {}).get("base")
        bases[name] = written if written is not None else BASES.get(name)
    for name in sorted(bases):
        base = bases[name]
        if base is not None and base not in bases:
            faults.append(f"style {name} : base inconnue « {base} »")
            bases[name] = "text"
    for name in sorted(bases):
        seen = [name]
        current = bases[name]
        while current is not None:
            if current in seen:
                loop = " → ".join([*seen[seen.index(current) :], current])
                faults.append(f"style {name} : base en boucle ({loop})")
                bases[name] = "text" if name != "text" else None
                break
            seen.append(current)
            current = bases[current]
    return bases


def _check_owners(
    declared: Mapping[str, Mapping[str, Any]],
    bases: Mapping[str, str | None],
    faults: list[str],
) -> None:
    """Une propriété propre (``borders``, ``logo``…) ne va qu'à son style et ses dérivés."""
    for name, props in declared.items():
        chain: set[str] = set()
        current: str | None = name
        while current is not None and current not in chain:
            chain.add(current)
            current = bases.get(current)
        for prop in props:
            owners = _OWNERS.get(prop)
            if owners is not None and not owners & chain:
                faults.append(
                    f"style {name}.{prop} : propre à {', '.join(sorted(owners))} "
                    "(et aux styles qui en partent)"
                )


def _look(
    name: str,
    declared: Mapping[str, Mapping[str, Any]],
    bases: Mapping[str, str | None],
    looks: dict[str, Look],
) -> Look:
    if name in looks:
        return looks[name]
    base = bases.get(name)
    inherited: dict[str, Any] = {}
    if base is not None:
        parent = _look(base, declared, bases, looks)
        looks[base] = parent
        inherited = {prop: getattr(parent, prop) for prop in _LOOK_FIELDS}
    own = {prop: value for prop, value in declared.get(name, {}).items() if prop in _LOOK_FIELDS}
    if "lines" in own and own["lines"] is not None:
        own["lines"] = tuple(own["lines"])
    if isinstance(own.get("logo"), str):
        # Un logo non lu : le chargeur de config lit les fichiers ; la fabrique le refuse.
        own["logo"] = None
    return Look(**{**inherited, **own})


def _page(given: Iterable[StyleFile]) -> dict[str, Any]:
    page: dict[str, Any] = {}
    for file in given:
        if file.page is None:
            continue
        said = file.page.model_dump(exclude_none=True)
        margins = said.pop("margins", None)
        if margins is not None:
            for side in ("top", "bottom", "left", "right"):
                page[f"margin_{side}"] = margins
        page.update(said)
    return page


def _chart(given: Iterable[StyleFile], colors: Mapping[str, str], faults: list[str]) -> ChartLook:
    said: dict[str, Any] = {}
    for file in given:
        if file.chart is not None:
            said.update(file.chart.model_dump(exclude_none=True))

    def color(value: str) -> str:
        return _named_colors("chart", "color", value, colors, faults) if value else value

    return ChartLook(
        colors=tuple(color(value) for value in said.get("colors", ())),
        text=color(said.get("text", "")),
        grid=color(said.get("grid", "")),
        title=color(said.get("title", "")),
    )


def _layout(
    given: Iterable[StyleFile], table: Look
) -> tuple[dict[str, SectionLook], tuple[Beside, ...]]:
    """La disposition des sections (le style d'un modèle), jusqu'au lot 3."""
    sections: dict[str, SectionLook] = {}
    beside: tuple[Beside, ...] = ()
    for file in given:
        for name, spec in file.sections.items():
            tables = spec.table.model_dump(exclude_none=True) if spec.table else {}
            sections[key(name)] = SectionLook(
                title=spec.title or "visible",
                align=spec.align,
                size=spec.size or "normal",
                box=bool(spec.box),
                columns=spec.columns or 1,
                page_break=bool(spec.page_break),
                landscape=bool(spec.landscape),
                cover=bool(spec.cover),
                space_before=round((spec.space_before or 0) * CM),
                table=_with(table, tables) if tables else None,
            )
        if file.beside:
            beside = tuple(
                Beside(
                    keys=tuple(key(name) for name in group.sections),
                    widths=tuple(
                        (width / 100 for width in group.widths)
                        if group.widths
                        else (1 / len(group.sections) for _ in group.sections)
                    ),
                )
                for group in file.beside
            )
    return sections, beside


def _with(look: Look, changes: Mapping[str, Any]) -> Look:
    values = {prop: getattr(look, prop) for prop in _LOOK_FIELDS}
    values.update(changes)
    return Look(**values)


def unsafe_fonts(style: Style) -> list[str]:
    """Les polices du style hors de la liste sûre : remplacées chez qui ne les a pas."""
    fonts = [look.font for look in style.looks.values() if look.font]
    return sorted({font for font in fonts if font.lower() not in SAFE_FONTS})


def parse_rule(value: str | None) -> tuple[float, str] | None:
    """Un filet résolu : (épaisseur en points, couleur) ; ``None`` sans filet."""
    if value is None or value == NONE:
        return None
    width, color = value.split(" ", 1)
    return float(width.removesuffix("pt")), color


@cache
def house() -> Style:
    """Le style maison : ``default.yaml`` seul."""
    return resolve()
