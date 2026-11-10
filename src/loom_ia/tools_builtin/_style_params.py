# SPDX-License-Identifier: Apache-2.0
"""Le style d'un document ``word`` ou ``pdf`` : des styles nommés, dans des fichiers YAML.

Un fichier de style est une liste de **styles nommés** — ``text``, ``document_title``,
``heading1``… —, chacun avec ses propriétés (police, taille, couleur, espacements,
filets), et quelques **réglages** (``colors``, ``page``, ``numbering``, ``language``,
``labels``, ``image``, ``chart``, ``fonts``). Trois fichiers, du plus général au plus
précis :

1. ``styles/default.yaml``, livré avec loom : le style maison ;
2. la charte de l'outil (``style:``) ;
3. le style d'un modèle de document (``style:`` dans le fichier du modèle).

**Les fichiers d'abord** : chaque style complète le même style des fichiers précédents,
propriété par propriété. **L'héritage ensuite** : un style prend les propriétés de sa
``base`` (``text`` sans base), déjà complétée par les fichiers, puis les siennes. Le
code des outils n'a aucune valeur visuelle : tout vient de ces fichiers.

Un style à toi qui a une ``description`` est **proposé au modèle de langage**, sur le type
de bloc dont il descend (``important_note``, ``base: note`` : le champ ``style`` d'un bloc
``note``) ; il n'en voit que le nom et la description. Du reste, il ne voit rien.

``fonts`` donne les fichiers TrueType d'une police, que le PDF embarque ; ils sont lus au
chargement, et vérifiés (``font_fault``) : contours TrueType, droits d'embarquement.

Module léger — Pydantic et PyYAML — : la config l'importe pour valider une référence,
sans importer l'outil.
"""

import difflib
import re
import struct
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

DEFAULT_FILE: Final = Path(__file__).with_name("styles") / "default.yaml"
MAX_STYLE_CHARS: Final = 20_000
MAX_LOGO_BYTES: Final = 2_000_000
# Les polices embarquées dans le PDF : leurs fichiers, et leurs familles par fichier de style.
MAX_FONT_BYTES: Final = 2_000_000
MAX_FONT_FAMILIES: Final = 4
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
# Les polices standard du PDF, qui ne s'embarquent pas : celle qui rend une police du style
# dont le style ne donne pas les fichiers (``fonts``), selon sa forme ; Helvetica sinon.
PDF_FONTS: Final[Mapping[str, str]] = {
    **dict.fromkeys(
        ("arial", "helvetica", "calibri", "verdana", "tahoma", "trebuchet ms"), "Helvetica"
    ),
    **dict.fromkeys(("times new roman", "times", "cambria", "georgia", "garamond"), "Times-Roman"),
    **dict.fromkeys(("courier new", "courier"), "Courier"),
}
PDF_FALLBACK: Final = "Helvetica"
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
    "code": "text",
    "link": "text",
    "footnote": "text",
    "fields": "text",
    "fields_label": "fields",
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
    "label_width": frozenset({"fields"}),
}
# Les réglages : des clés du fichier qui ne sont pas des styles.
SETTINGS: Final = frozenset(
    {"colors", "page", "numbering", "language", "labels", "image", "chart", "fonts"}
)
# La disposition des sections d'un modèle : dans le modèle de document depuis 2.1.0.
LAYOUT: Final = frozenset({"sections", "beside"})
# Les styles de bloc : un style à toi qui en descend peut être proposé au modèle de langage
# (``description``), sur ce type de bloc.
BLOCK_STYLES: Final[Mapping[str, str]] = {
    "heading1": "heading",
    "heading2": "heading",
    "heading3": "heading",
    "paragraph": "paragraph",
    "list": "list",
    "note": "note",
    "quote": "quote",
    "table": "table",
}
MAX_STYLE_DESCRIPTION: Final = 200
# Les clés du format de la 2.0, refusées avec ce qui les remplace.
OLD_KEYS: Final[Mapping[str, str]] = {
    "title": "document_title",
    "headings": "heading1, heading2, heading3 (et numbering : decimal, article)",
    "paragraphs": "text (line → line_spacing, after → space_after, justify → align: justify)",
    "tables": "table et table_header (align → position, header.fill → table_header.fill)",
    "captions": "caption (numbered)",
    "images": "image (grid)",
}

# Les libellés que l'outil écrit dans le document, par langue.
LABEL_KEYS: Final = (
    "table",
    "figure",
    "contents",
    "date",
    "signature",
    "article",
    "total",
    "notes",
)
LABELS: Final[Mapping[Language, Mapping[str, str]]] = {
    "fr": {
        "table": "Tableau",
        "figure": "Figure",
        "contents": "Sommaire",
        "date": "Date :",
        "signature": "Signature :",
        "article": "Article",
        "total": "Total",
        "notes": "Notes",
    },
    "en": {
        "table": "Table",
        "figure": "Figure",
        "contents": "Contents",
        "date": "Date:",
        "signature": "Signature:",
        "article": "Article",
        "total": "Total",
        "notes": "Notes",
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
    # Les champs : la largeur de la colonne des libellés, en cm.
    label_width: Annotated[float, Field(ge=1, le=10)] | None = None
    # Le sens d'un style à toi : il est proposé au modèle de langage, sur son type de bloc.
    description: Annotated[str, Field(min_length=1, max_length=MAX_STYLE_DESCRIPTION)] | None = None


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
    notes: Annotated[str, Field(min_length=1, max_length=MAX_LABEL)] | None = None


class ImageSpec(_Spec):
    # Deux images ``half`` qui se suivent : côte à côte.
    grid: bool | None = None


class FontFiles(_Spec):
    """Les fichiers TrueType d'une police, relatifs à ``resources_dir`` ; leurs octets une
    fois lus au chargement. ``regular`` seul suffit : le gras et l'italique le prennent."""

    regular: str | bytes
    bold: str | bytes | None = None
    italic: str | bytes | None = None
    bold_italic: str | bytes | None = None


# Les variantes d'une police, dans l'ordre de ``FontFiles``.
FONT_VARIANTS: Final = ("regular", "bold", "italic", "bold_italic")


class ChartSpec(_Spec):
    # La couleur de chaque série, dans l'ordre ; le texte, la grille, le titre.
    colors: Annotated[list[Color], Field(min_length=1, max_length=12)] | None = None
    text: Color | None = None
    grid: Color | None = None
    title: Color | None = None


def _closest(name: str) -> str:
    found = difflib.get_close_matches(name, [*BASES, *SETTINGS], n=1, cutoff=0.6)
    return f" — vouliez-vous dire {found[0]} ?" if found else ""


class StyleFile(_Spec):
    """Un fichier de style lu : ses réglages et ses styles nommés."""

    colors: dict[ColorName, Hex] | None = None
    page: PageSpec | None = None
    numbering: Numbering | None = None
    language: Language | None = None
    labels: LabelsSpec | None = None
    image: ImageSpec | None = None
    chart: ChartSpec | None = None
    fonts: Annotated[dict[FontName, FontFiles], Field(max_length=MAX_FONT_FAMILIES)] | None = None
    styles: dict[StyleName, Props] = {}

    @model_validator(mode="before")
    @classmethod
    def _named(cls, value: object) -> object:
        """Le fichier tel qu'écrit : les réglages, puis tout autre nom est un style."""
        if not isinstance(value, Mapping):
            return value
        entries = cast(Mapping[object, object], value)
        layout = sorted(name for name in LAYOUT if name in entries)
        if layout:
            raise ValueError(
                f"{', '.join(layout)} : la disposition des sections se déclare dans le modèle "
                "de document depuis 2.1.0 (sections.<nom> : box, beside, width, page_break, "
                "columns, landscape, cover, styles)"
            )
        old = [f"{name} → {OLD_KEYS[name]}" for name in OLD_KEYS if name in entries]
        if _old_fonts(entries.get("fonts")):
            old.append(
                "fonts → font, sur text et sur chaque titre (fonts donne depuis 2.1.0 les "
                "fichiers TrueType d'une police, pour le PDF : fonts: {Inter: {regular: "
                "fonts/Inter-Regular.ttf}})"
            )
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
            if name in SETTINGS:
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


def _old_fonts(value: object) -> bool:
    """``fonts`` au format de la 2.0 : ``body`` et ``headings``, un nom ou ``{name, size}``."""
    if not isinstance(value, Mapping):
        return False
    for given in cast(Mapping[object, object], value).values():
        if isinstance(given, str):
            return True
        if isinstance(given, Mapping) and {"name", "size"} & set(
            cast(Mapping[object, object], given)
        ):
            return True
    return False


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


def logos(spec: StyleFile) -> list[tuple[str, str | bytes]]:
    """Les logos d'un fichier de style : (nom du style, fichier ou octets)."""
    return [(name, props.logo) for name, props in spec.styles.items() if props.logo is not None]


def font_files(spec: StyleFile) -> list[tuple[str, str, str | bytes]]:
    """Les fichiers des polices d'un fichier de style : (famille, variante, fichier ou
    octets)."""
    found: list[tuple[str, str, str | bytes]] = []
    for family, files in (spec.fonts or {}).items():
        for variant in FONT_VARIANTS:
            given = cast(str | bytes | None, getattr(files, variant))
            if given is not None:
                found.append((family, variant, given))
    return found


# Le droit d'embarquer une police (``fsType`` de sa table OS/2) : ce qui l'interdit.
_RESTRICTED: Final = 0x0002
_PREVIEW_PRINT: Final = 0x0004
_EDITABLE: Final = 0x0008
_NO_SUBSETTING: Final = 0x0100
_BITMAP_ONLY: Final = 0x0200


def font_fault(data: bytes) -> str | None:
    """Ce qui empêche d'embarquer une police dans un PDF ; ``None`` si rien.

    Une police TrueType (contours ``glyf``), seule dans son fichier, dont la licence permet
    l'embarquement, en sous-ensemble, pour un document qu'on peut modifier : ``fsType`` 0
    (installable) ou 8 (modifiable). Une police « aperçu et impression » (4) est refusée :
    un lecteur s'en servirait sans qu'on puisse modifier le document.
    """
    tag = data[:4]
    if tag in (b"wOFF", b"wOF2"):
        return "une police web (WOFF) : le fichier TrueType (.ttf) est attendu"
    if tag == b"OTTO":
        return "une police OpenType à contours CFF (.otf) : une police TrueType (.ttf) est attendue"
    if tag == b"ttcf":
        return "une collection de polices (.ttc) : un fichier .ttf par police est attendu"
    if tag not in (b"\x00\x01\x00\x00", b"true") or len(data) < 12:
        return "pas une police TrueType (.ttf)"
    count = struct.unpack(">H", data[4:6])[0]
    if len(data) < 12 + 16 * count:
        return "police TrueType tronquée"
    tables: dict[bytes, tuple[int, int]] = {}
    for index in range(count):
        name, _, offset, length = struct.unpack(">4sIII", data[12 + 16 * index : 28 + 16 * index])
        tables[name] = (offset, length)
    if b"glyf" not in tables:
        return "pas de contours TrueType (table glyf) : une police TrueType (.ttf) est attendue"
    offset, length = tables.get(b"OS/2", (0, 0))
    if length < 10 or offset + 10 > len(data):
        return "pas de table OS/2 : les droits d'embarquement de la police sont inconnus"
    rights = struct.unpack(">H", data[offset + 8 : offset + 10])[0]
    if not rights & _EDITABLE:
        if rights & _PREVIEW_PRINT:
            return (
                "police « aperçu et impression seulement » (fsType 4) : sa licence ne permet "
                "pas un document modifiable"
            )
        if rights & _RESTRICTED:
            return "police à embarquement interdit (fsType 2) : sa licence ne le permet pas"
    if rights & _NO_SUBSETTING:
        return "police à embarquer entière (fsType 0x100) : le PDF n'en embarque qu'une partie"
    if rights & _BITMAP_ONLY:
        return "police à embarquer en images seulement (fsType 0x200)"
    return None


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
    label_width: float | None = None


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
    notes: str = "Notes"


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
    # Les fichiers TrueType des polices, par famille puis variante (``regular``…) : le PDF
    # les embarque.
    fonts: Mapping[str, Mapping[str, bytes]] = field(default_factory=dict[str, Mapping[str, bytes]])

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

    def block_type(self, name: str) -> tuple[str, int] | None:
        """Le type de bloc d'un style et, pour un titre, son niveau : (« note », 0),
        (« heading », 2) ; ``None`` pour un style qui ne part d'aucun style de bloc."""
        for step in self.chain(name):
            kind = BLOCK_STYLES.get(step)
            if kind is not None:
                return kind, int(step[-1]) if kind == "heading" else 0
        return None

    def proposed(self) -> dict[str, list[tuple[str, str]]]:
        """Les styles proposés au modèle de langage, par type de bloc : (nom, description)."""
        found: dict[str, list[tuple[str, str]]] = {}
        for name, props in self.declared.items():
            description = props.get("description")
            kind = self.block_type(name)
            if isinstance(description, str) and kind is not None:
                found.setdefault(kind[0], []).append((name, description))
        return {kind: sorted(names) for kind, names in sorted(found.items())}

    def element_fault(self, element: str, name: str) -> str | None:
        """Ce qui empêche ``name`` d'être le style de ``element`` (``styles`` d'une section) :
        un style inconnu, ou qui ne part pas de l'élément."""
        if name not in self.looks:
            return f"style « {name} » inconnu"
        if element not in self.chain(name):
            return f"« {name} » ne part pas de {element} (base : {self.bases.get(name)})"
        return None

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
    _check_descriptions(declared, bases, faults)
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
    fonts: dict[str, dict[str, bytes]] = {}
    for file in given:
        for family in file.fonts or {}:
            fonts[family] = {}
        for family, variant, data in font_files(file):
            # Un fichier non lu : le chargeur de config lit les fichiers ; la fabrique le refuse.
            if isinstance(data, bytes):
                fonts[family][variant] = data
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
        fonts={family: files for family, files in fonts.items() if "regular" in files},
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


def _chain(name: str, bases: Mapping[str, str | None]) -> list[str]:
    chain: list[str] = []
    current: str | None = name
    while current is not None and current not in chain:
        chain.append(current)
        current = bases.get(current)
    return chain


def _check_descriptions(
    declared: Mapping[str, Mapping[str, Any]],
    bases: Mapping[str, str | None],
    faults: list[str],
) -> None:
    """Une description ne va qu'à un style à toi qui part d'un style de bloc."""
    for name, props in declared.items():
        if "description" not in props:
            continue
        if name in BASES or not any(step in BLOCK_STYLES for step in _chain(name, bases)):
            faults.append(
                f"style {name} : une description ne sert qu'à un style à toi qui part d'un "
                f"style de bloc ({', '.join(BLOCK_STYLES)}) : il est alors proposé au modèle "
                "de langage"
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
