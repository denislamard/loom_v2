# SPDX-License-Identifier: Apache-2.0
"""Le style d'un document ``word`` ou ``pdf`` : des styles nommés, dans des fichiers YAML.

Un fichier de style est une liste de **styles nommés** — ``body``, ``document_title``,
``heading1``… —, chacun avec ses propriétés (police, taille, couleur, espacements,
filets), et quelques **réglages** (``colors``, ``page``, ``numbering``, ``language``,
``labels``, ``image``, ``chart``, ``fonts``). Le contenu de l'en-tête, du pied et du
filigrane est un réglage (``page.header``, ``page.footer``, ``page.watermark``) ; les
styles ``header``, ``footer`` et ``watermark`` n'en ont que l'apparence. Le format est
décrit dans ``docs/style_format.md``. Trois fichiers, du plus général au plus
précis :

1. ``styles/default.yaml``, livré avec loom : le style maison ;
2. la charte de l'outil (``style:``) ;
3. le style d'un modèle de document (``style:`` dans le fichier du modèle).

**Les fichiers d'abord** : chaque style complète le même style des fichiers précédents,
propriété par propriété. **L'héritage ensuite** : un style prend les propriétés de sa
``base`` (``body`` sans base), déjà complétée par les fichiers, puis les siennes. Le
code des outils n'a aucune valeur visuelle : tout vient de ces fichiers, distances et
espaces compris (restent des unités et des mécaniques, que les essais nomment).

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
from collections.abc import Callable, Iterable, Mapping, Sequence
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
# La table d'un en-tête ou d'un pied de page : ses colonnes, ses lignes, la largeur d'une
# colonne ``auto`` au moins (en cm).
MAX_BAND_COLUMNS: Final = 4
MAX_BAND_ROWS: Final = 3
MIN_AUTO_WIDTH: Final = 1.0
MAX_STYLES: Final = 80
MAX_LABEL: Final = 40
# Les polices présentes sur presque tous les postes (Windows, macOS, LibreOffice les
# remplace par des équivalentes métriques ; Aptos, celle de Word depuis Microsoft 365,
# s'installe avec Office) : une autre s'affiche ailleurs dans une police de remplacement,
# et ``loom validate`` le signale.
SAFE_FONTS: Final = frozenset(
    {
        "aptos",
        "aptos display",
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
        (
            "aptos",
            "aptos display",
            "arial",
            "helvetica",
            "calibri",
            "verdana",
            "tahoma",
            "trebuchet ms",
        ),
        "Helvetica",
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
# Les filets d'un tableau : aucun, entre ses lignes, entre ses colonnes, les deux (grid).
type Borders = Literal["none", "horizontal", "vertical", "grid"]
type VAlign = Literal["top", "center", "bottom"]
type TableWidth = Literal["full", "content"]
type Numbering = Literal["none", "decimal", "article"]
type PageSize = Literal["A4", "A5", "Letter"]
type Orientation = Literal["portrait", "landscape"]
type Language = Literal["fr", "en"]

# Un centimètre en vingtièmes de point (twips).
CM: Final = 567


def cm_twips(value: float) -> int:
    """Une longueur en cm, en twips : la mesure de Word, que le PDF reprend."""
    return round(value * CM)

PAGE_SIZES: Final[Mapping[str, tuple[int, int]]] = {
    "A4": (11906, 16838),
    "A5": (8391, 11906),
    "Letter": (12240, 15840),
}

# Les styles prévus, et la base de chacun quand le fichier n'en donne pas.
BASES: Final[Mapping[str, str | None]] = {
    "body": None,
    "document_title": "body",
    "heading1": "body",
    "heading2": "body",
    "heading3": "body",
    "paragraph": "body",
    "list": "body",
    "note": "body",
    "caption": "body",
    "quote": "body",
    "quote_source": "quote",
    "table": "body",
    "table_header": "table",
    "table_total": "table",
    "table_group": "table",
    "table_subtotal": "table",
    "invoice_totals": "table",
    "attendance": "table",
    "box": "body",
    "toc": "body",
    "toc1": "body",
    "toc2": "body",
    "toc3": "body",
    "signature": "body",
    "signature_mention": "body",
    "header": "body",
    "footer": "body",
    "watermark": "body",
    "code": "body",
    "link": "body",
    "footnote": "body",
    "fields": "body",
    "fields_label": "fields",
}
# --- Le registre des propriétés ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Property:
    """Une propriété de style, telle que la décrit le registre : son nom ; les styles qui la
    portent, avec ceux qui en partent (``None`` : tous, une propriété commune) ; sa valeur ;
    l'unité d'un nombre ; ses bornes ; son rôle."""

    name: str
    owners: frozenset[str] | None
    value: str
    role: str
    unit: str = ""
    bounds: str = ""


_TABLE: Final = frozenset({"table"})
_BOXES: Final = frozenset({"box", "table"})
# Les styles d'une ligne de tableau : leur espacement est celui de leurs cellules.
ROW_STYLES: Final = frozenset({"table_header", "table_total", "table_group", "table_subtotal"})
_BANDS: Final = frozenset({"header", "footer"})

# Le registre : la seule liste des propriétés. ``Props`` (le fichier lu) et ``Look`` (le
# style résolu) en ont exactement les noms, ce qu'un essai vérifie ; ``_OWNERS`` et le
# message d'une propriété inconnue en sont tirés.
PROPERTIES: Final[tuple[Property, ...]] = (
    Property("base", None, "nom de style", "Le style dont celui-ci part"),
    Property("description", None, "texte", "Le sens d'un style à toi, proposé au modèle de langage sur son type de bloc", bounds="1 à 200 caractères"),
    Property("font", None, "nom de police", "La police", bounds="60 caractères"),
    Property("size", None, "nombre", "La taille du texte", unit="pt", bounds="6 à 72"),
    Property("color", None, "couleur", "La couleur du texte"),
    Property("fill", None, "couleur, ou none", "Le fond"),
    Property("bold", None, "booléen", "Gras"),
    Property("italic", None, "booléen", "Italique"),
    Property("underline", None, "booléen", "Souligné"),
    Property("uppercase", None, "booléen", "Capitales"),
    Property("align", None, "left, center, right, justify", "L'alignement du texte"),
    Property("space_before", None, "nombre", "L'espace avant", unit="pt", bounds="0 à 72"),
    Property("space_after", None, "nombre", "L'espace après", unit="pt", bounds="0 à 72"),
    Property("line_spacing", None, "nombre", "L'interligne, un multiple", bounds="1 à 3"),
    Property("indent", None, "nombre", "Le retrait à gauche et à droite", unit="cm", bounds="0 à 10"),
    Property("border_top", None, "filet, ou none", "Un filet au-dessus"),
    Property("border_bottom", None, "filet, ou none", "Un filet au-dessous"),
    Property("border_left", None, "filet, ou none", "Un filet à gauche"),
    Property("border_right", None, "filet, ou none", "Un filet à droite"),
    Property("border", None, "filet, ou none", "Un filet sur les quatre côtés"),
    Property("border_space", None, "1 à 4 longueurs", "L'écart entre un filet (ou le fond) et le texte, de chaque côté", unit="pt", bounds="0 à 20"),
    Property("valign", _TABLE | _BANDS, "top, center, bottom", "Le texte dans la hauteur de sa cellule"),
    Property("borders", _TABLE | _BANDS, "none, horizontal, vertical, grid", "Les filets d'un tableau, d'une table d'en-tête ou de pied"),
    Property("line", _TABLE | _BANDS, "filet", "Le trait de ces filets"),
    Property("column_gap", _BANDS, "nombre", "L'écart entre deux colonnes d'une table d'en-tête ou de pied", unit="cm", bounds="0 à 3"),
    Property("bands", _TABLE, "couleur", "Le fond d'une ligne sur deux"),
    Property("padding", _BOXES, "1 à 4 longueurs", "L'écart entre le bord d'un cadre, d'une cellule, et son texte", unit="cm", bounds="0 à 2"),
    Property("width", _TABLE, "full, content", "Toute la largeur, ou celle du contenu"),
    Property("position", _TABLE, "left, center, right", "La place d'un tableau étroit"),
    Property("numbered", frozenset({"caption"}), "booléen", "« Tableau 1 — », « Figure 1 — »"),
    Property("levels", frozenset({"toc"}), "nombre", "Les niveaux de titre du sommaire", bounds="1 à 3"),
    Property("page_numbers", frozenset({"toc"}), "booléen", "Les numéros de page du sommaire"),
    Property("level_indent", frozenset({"list", "toc"}), "nombre", "Le retrait de chaque niveau d'une liste, du sommaire", unit="cm", bounds="0 à 5"),
    Property("marker_space", frozenset({"list"}), "nombre", "La place de la puce ou du numéro, devant le texte", unit="cm", bounds="0 à 3"),
    Property("sign_height", frozenset({"signature"}), "nombre", "La place pour signer, sous « Signature : »", unit="cm", bounds="0 à 10"),
    Property("label_width", frozenset({"fields"}), "nombre", "La largeur des libellés", unit="cm", bounds="1 à 10"),
    Property("row_height", frozenset({"attendance"}), "nombre", "La hauteur d'une ligne d'émargement", unit="cm", bounds="0,5 à 5"),
)
PROPERTY_NAMES: Final = frozenset(found.name for found in PROPERTIES)
# Les propriétés propres à certains styles, et ceux (avec leurs dérivés) qui les portent.
_OWNERS: Final[Mapping[str, frozenset[str]]] = {
    found.name: found.owners for found in PROPERTIES if found.owners is not None
}
# Les réglages : des clés du fichier qui ne sont pas des styles.
SETTINGS: Final = frozenset({"colors", "page", "numbering", "language", "labels", "image", "chart", "fonts"})
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
    "paragraphs": "body (line → line_spacing, after → space_after, justify → align: justify)",
    "tables": "table et table_header (align → position, header.fill → table_header.fill)",
    "captions": "caption (numbered)",
    "images": "image (side_by_side)",
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
    "subtotal",
    "total_excl_tax",
    "tax",
    "total_incl_tax",
    "paid",
    "due",
    "participant",
)
# Les champs du libellé d'une ligne de TVA : le taux (obligatoire) et sa base.
TAX_FIELDS: Final = frozenset({"rate", "base"})
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
        "subtotal": "Sous-total",
        "total_excl_tax": "Total HT",
        "tax": "TVA {rate} sur {base}",
        "total_incl_tax": "Total TTC",
        "paid": "Acompte versé",
        "due": "Net à payer",
        "participant": "Participant",
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
        "subtotal": "Subtotal",
        "total_excl_tax": "Total excl. VAT",
        "tax": "VAT {rate} on {base}",
        "total_incl_tax": "Total incl. VAT",
        "paid": "Deposit paid",
        "due": "Amount due",
        "participant": "Participant",
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
    raise ValueError(f"couleur {value!r} : six chiffres hexadécimaux (« 1F3864 »), ou le nom d'une couleur définie dans colors")


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
        raise ValueError(f"police {value!r} : un nom de police (lettres, chiffres, espaces, tirets ; 60 caractères au plus)")
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
        raise ValueError(f"nom de couleur {value!r} : des minuscules, chiffres et _, une lettre d'abord, et pas six chiffres hexadécimaux (brand, grey)")
    return value


def _style_name(value: str) -> str:
    if not _NAME.match(value):
        raise ValueError(f"nom de style {value!r} : des minuscules, chiffres et _, une lettre d'abord (40 caractères au plus)")
    return value


# --- Les longueurs et les proportions ------------------------------------------------------
# Une longueur : un nombre, et au besoin son unité collée (pt, mm, cm, in), virgule ou
# point ; un nombre seul est dans l'unité de sa propriété : pt pour la typographie et les
# espacements, cm pour la géométrie. Une proportion : un nombre suivi de %, pour une largeur.

POINTS_PER: Final[Mapping[str, float]] = {"pt": 1.0, "mm": 72 / 25.4, "cm": 72 / 2.54, "in": 72.0}
_LENGTH: Final = re.compile(r"^(\d+(?:[.,]\d+)?)\s*(pt|mm|cm|in)?$")
_PERCENT: Final = re.compile(r"^(\d+(?:[.,]\d+)?)\s*%$")


def _length_in(unit: str) -> Callable[[object], object]:
    """Une longueur écrite (``11pt``, ``2cm``, ``12mm``, ``1in``, ou un nombre), dans ``unit``."""

    def read(value: object) -> object:
        if not isinstance(value, str):
            return value
        found = _LENGTH.match(value.strip())
        if found is None:
            raise ValueError(
                f"longueur {value!r} : un nombre et, au besoin, son unité (pt, mm, cm, in), "
                "ex. 11pt, 2cm"
            )
        number = float(found.group(1).replace(",", "."))
        given = found.group(2) or unit
        # Arrondie au millionième : 1,5 cm reste 1,5, et 20 mm vaut 2 cm.
        return number if given == unit else round(number * POINTS_PER[given] / POINTS_PER[unit], 6)

    return read


def percent(value: object) -> object:
    """Une proportion écrite (``55%``, ou un nombre), en pour cent."""
    if not isinstance(value, str):
        return value
    found = _PERCENT.match(value.strip())
    if found is None:
        raise ValueError(f"proportion {value!r} : un nombre suivi de %, ex. 55%")
    return float(found.group(1).replace(",", "."))


# Une longueur lue en points (la typographie, les espacements), en centimètres (la géométrie).
in_points: Final = _length_in("pt")
in_centimetres: Final = _length_in("cm")

# Les quatre côtés d'un écart : en haut, à droite, en bas, à gauche (l'ordre de CSS).
type Sides = tuple[float, float, float, float]


def _sides_in(unit: str, high: float) -> Callable[[object], object]:
    """Une à quatre longueurs (``4pt 8pt``), dans ``unit``, de 0 à ``high`` chacune, dans
    l'ordre de CSS : une, les quatre côtés ; deux, en haut et en bas, puis à droite et à
    gauche ; trois, en haut, à droite et à gauche, en bas ; quatre, en haut, à droite, en
    bas, à gauche."""
    read = _length_in(unit)

    def sides(value: object) -> object:
        if isinstance(value, bool):
            raise ValueError(f"{value!r} : une à quatre longueurs (4pt 8pt)")
        parts: list[object]
        if isinstance(value, str):
            parts = list(value.split())
        elif isinstance(value, list | tuple):
            parts = list(cast(Sequence[object], value))
        else:
            parts = [value]
        if not 1 <= len(parts) <= 4:
            raise ValueError(f"{value!r} : une à quatre longueurs (4pt 8pt)")
        numbers: list[float] = []
        for part in parts:
            got = read(part)
            if isinstance(got, bool) or not isinstance(got, int | float):
                raise ValueError(f"longueur {part!r} : un nombre et, au besoin, son unité (pt, mm, cm, in)")
            if not 0 <= got <= high:
                raise ValueError(f"longueur {part!r} : de 0 à {high:g} {unit}")
            numbers.append(float(got))
        if len(numbers) == 1:
            numbers *= 4
        elif len(numbers) == 2:
            numbers *= 2
        elif len(numbers) == 3:
            numbers.append(numbers[1])
        top, right, bottom, left = numbers
        return (top, right, bottom, left)

    return sides

type Color = Annotated[str, BeforeValidator(_text_color), AfterValidator(_color)]
type Fill = Annotated[str, BeforeValidator(_text_color), AfterValidator(_fill)]
type Hex = Annotated[str, BeforeValidator(_text_color), AfterValidator(_hex)]
type Rule = Annotated[str, AfterValidator(_rule)]
type FontName = Annotated[str, AfterValidator(_font)]
type BandText = Annotated[str, AfterValidator(_band_text)]
type StyleName = Annotated[str, AfterValidator(_style_name)]
type ColorName = Annotated[str, AfterValidator(_color_name)]
type Points = Annotated[float, BeforeValidator(in_points), Field(ge=0, le=72)]


class _Spec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# Les styles renommés en 2.0.21 (docs/style_format.md), refusés avec leur nouveau nom.
RENAMED_STYLES: Final[Mapping[str, str]] = {"text": "body"}


def refuse_renamed(value: object, where: str, renamed: Mapping[str, str]) -> object:
    """Une table où une clé renommée en 2.0.21 paraît : refusée, avec son nouveau nom."""
    if not isinstance(value, Mapping):
        return value
    entries = cast(Mapping[object, object], value)
    found = [f"{where}.{old} → {where}.{new}" for old, new in renamed.items() if old in entries]
    if found:
        raise ValueError(f"{' ; '.join(found)} : renommé en 2.0.21")
    return entries


# --- Le fichier de style -----------------------------------------------------------------


class BandElementSpec(_Spec):
    """Un élément d'un en-tête ou d'un pied de page : une zone, une ligne, une cellule de
    sa table. Un texte (``text``), ou des lignes (``lines``, dans une cellule seulement),
    avec ``{page}``, ``{pages}``, ``{title}`` ; le nom du style qui le met en forme
    (``style``, un style qui part de ``header`` ou de ``footer``) ; le logo de l'en-tête
    ou du pied (``logo: true``). Écrit en court : un texte, ou une liste de lignes. Aucune
    propriété de style : elles vont dans le style qu'il nomme."""

    text: BandText | None = None
    lines: Annotated[list[BandText], Field(min_length=1, max_length=MAX_BAND_LINES)] | None = None
    style: StyleName | None = None
    logo: bool | None = None

    @model_validator(mode="before")
    @classmethod
    def _short(cls, value: object) -> object:
        if isinstance(value, str):
            return {"text": value}
        if isinstance(value, list):
            return {"lines": cast(list[object], value)}
        if isinstance(value, Mapping):
            entries = cast(Mapping[object, object], value)
            inline = [key for key in ("align", "valign") if key in entries]
            if inline:
                raise ValueError(
                    f"{', '.join(inline)} : une propriété de style, pas d'un élément ; l'écrire "
                    "dans un style, nommé par style: (footer_page: {base: footer, align: right})"
                )
            if isinstance(entries.get("style"), Mapping):
                raise ValueError("style : le nom d'un style, défini dans le fichier de style, pas un style écrit en ligne")
            return entries
        return value

    @model_validator(mode="after")
    def _one_text(self) -> Self:
        if self.text is not None and self.lines is not None:
            raise ValueError("un élément a un texte (text) ou des lignes (lines), pas les deux")
        return self


class BandLogoSpec(_Spec):
    """Le logo d'un en-tête ou d'un pied : son fichier (PNG ou JPEG, relatif à
    ``resources_dir`` ; ses octets une fois lu au chargement), sa largeur (cm), l'écart à
    son texte (pt). Écrit en court : le fichier seul."""

    file: str | bytes
    width: Annotated[float, BeforeValidator(in_centimetres), Field(ge=0.5, le=17)] | None = None
    gap: Annotated[float, BeforeValidator(in_points), Field(ge=0, le=20)] | None = None

    @model_validator(mode="before")
    @classmethod
    def _short(cls, value: object) -> object:
        return {"file": value} if isinstance(value, str | bytes) else value


def _band_width_read(value: object) -> object:
    """Une largeur de colonne : ``auto`` ; une proportion (``50%``, de plus de 0 à 100 %) ;
    une longueur (``1.5cm``, ``15mm``, un nombre en cm, de plus de 0 à 30 cm)."""
    if value == "auto":
        return value
    if isinstance(value, str) and value.strip().endswith("%"):
        share = percent(value)
        assert isinstance(share, float)
        if not 0 < share <= 100:
            raise ValueError(f"largeur {value!r} : une proportion de plus de 0 à 100 %")
        return f"{share:g}%"
    if isinstance(value, bool) or not isinstance(value, str | int | float):
        raise ValueError(f"largeur {value!r} : auto, une proportion (50%) ou une longueur (1.5cm)")
    width = in_centimetres(value)
    assert isinstance(width, float | int)
    if not 0 < width <= 30:
        raise ValueError(f"largeur {value!r} : de plus de 0 à 30 cm")
    return float(width)


# Une largeur de colonne : auto, une proportion (``50%``), une longueur (cm par défaut).
type BandWidth = Annotated[float | str, BeforeValidator(_band_width_read)]


class BandTableSpec(_Spec):
    """La table d'un en-tête ou d'un pied de page : la largeur de chaque colonne (une
    longueur, une proportion, ou ``auto`` : la place qui reste, partagée) ; ses lignes,
    chacune un élément par colonne ; le nom du style qui l'habille (``style``, qui part de
    ``header`` ou de ``footer`` ; ``header`` ou ``footer`` sans lui) : son cadre, son fond,
    ses filets intérieurs (``borders``, ``line``), l'écart entre ses colonnes
    (``column_gap``)."""

    widths: Annotated[list[BandWidth], Field(min_length=1, max_length=MAX_BAND_COLUMNS)]
    rows: Annotated[list[list[BandElementSpec]], Field(min_length=1, max_length=MAX_BAND_ROWS)]
    style: StyleName | None = None

    @model_validator(mode="before")
    @classmethod
    def _styled(cls, value: object) -> object:
        """``gap``, ``borders``, ``line`` d'une table : des propriétés de son style depuis 2.0.21."""
        if not isinstance(value, Mapping):
            return value
        entries = cast(Mapping[object, object], value)
        moved = {"gap": "column_gap", "borders": "borders", "line": "line"}
        found = [f"{old} → {new}" for old, new in moved.items() if old in entries]
        if found:
            raise ValueError(
                f"{' ; '.join(found)} : des propriétés du style de la table depuis 2.0.21 "
                "(footer, ou le style que nomme style:)"
            )
        return entries

    @model_validator(mode="after")
    def _cells(self) -> Self:
        columns = len(self.widths)
        for number, row in enumerate(self.rows, start=1):
            if len(row) != columns:
                raise ValueError(
                    f"ligne {number} : {len(row)} cellule(s) pour {columns} colonne(s) (widths)"
                )
        return self


class BandSpec(_Spec):
    """Ce qu'écrit un en-tête ou un pied de page (``page.header``, ``page.footer``) : trois
    zones sur une ligne, ou une table ; des lignes de toute la largeur ; le logo, posé dans
    l'élément qui le demande ; ``first_page: false`` pour une première page sans."""

    first_page: bool | None = None
    logo: BandLogoSpec | None = None
    left: BandElementSpec | None = None
    center: BandElementSpec | None = None
    right: BandElementSpec | None = None
    lines: Annotated[list[BandElementSpec], Field(max_length=MAX_BAND_LINES)] | None = None
    table: BandTableSpec | None = None

    @model_validator(mode="after")
    def _one_line_each(self) -> Self:
        for name in ("left", "center", "right"):
            element = cast(BandElementSpec | None, getattr(self, name))
            if element is not None and element.lines is not None:
                raise ValueError(f"{name} : une zone a un texte (text), pas des lignes")
        for number, element in enumerate(self.lines or (), start=1):
            if element.lines is not None:
                raise ValueError(f"lines.{number} : une ligne a un texte (text), pas des lignes")
        return self


class Props(_Spec):
    """Les propriétés d'un style nommé, telles qu'écrites ; toutes facultatives. Leurs noms
    sont ceux du registre (``PROPERTIES``) ; une autre est dite par ``describe``."""

    base: StyleName | None = None
    font: FontName | None = None
    size: Annotated[float, BeforeValidator(in_points), Field(ge=6, le=72)] | None = None
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
    indent: Annotated[float, BeforeValidator(in_centimetres), Field(ge=0, le=10)] | None = None
    border_top: Rule | None = None
    border_bottom: Rule | None = None
    border_left: Rule | None = None
    border_right: Rule | None = None
    border: Rule | None = None
    # L'écart entre un filet (ou le fond) et le texte, en points, les quatre côtés.
    border_space: Annotated[Sides, BeforeValidator(_sides_in("pt", 20))] | None = None
    # Le tableau, et les tables d'en-tête et de pied : le texte dans la hauteur d'une cellule.
    valign: VAlign | None = None
    # Le tableau.
    borders: Borders | None = None
    line: Rule | None = None
    bands: Fill | None = None
    width: TableWidth | None = None
    position: Position | None = None
    # Le cadre d'une section, une cellule de tableau : l'écart au texte, en cm, les quatre côtés.
    padding: Annotated[Sides, BeforeValidator(_sides_in("cm", 2))] | None = None
    # La table d'un en-tête ou d'un pied : l'écart entre deux colonnes, en cm.
    column_gap: Annotated[float, BeforeValidator(in_centimetres), Field(ge=0, le=3)] | None = None
    # La légende, le sommaire.
    numbered: bool | None = None
    levels: Annotated[int, Field(ge=1, le=3)] | None = None
    page_numbers: bool | None = None
    # Une liste, le sommaire : le retrait de chaque niveau ; la place de la puce, en cm.
    level_indent: Annotated[float, BeforeValidator(in_centimetres), Field(ge=0, le=5)] | None = None
    marker_space: Annotated[float, BeforeValidator(in_centimetres), Field(ge=0, le=3)] | None = None
    # La signature : la place pour signer, en cm.
    sign_height: Annotated[float, BeforeValidator(in_centimetres), Field(ge=0, le=10)] | None = None
    # Les champs : la largeur de la colonne des libellés, en cm.
    label_width: Annotated[float, BeforeValidator(in_centimetres), Field(ge=1, le=10)] | None = None
    # La hauteur d'une ligne d'émargement, en cm : la place de signer.
    row_height: Annotated[float, BeforeValidator(in_centimetres), Field(ge=0.5, le=5)] | None = None
    # Le sens d'un style à toi : il est proposé au modèle de langage, sur son type de bloc.
    description: Annotated[str, Field(min_length=1, max_length=MAX_STYLE_DESCRIPTION)] | None = None


type Margin = Annotated[float, BeforeValidator(in_centimetres), Field(ge=0.5, le=5)]


class PageSpec(_Spec):
    paper: PageSize | None = None
    orientation: Orientation | None = None
    margins: Margin | None = None
    margin_top: Margin | None = None
    margin_bottom: Margin | None = None
    margin_left: Margin | None = None
    margin_right: Margin | None = None
    # La distance de l'en-tête et du pied au bord de la page, en cm.
    header_distance: Annotated[float, BeforeValidator(in_centimetres), Field(ge=0, le=5)] | None = None
    footer_distance: Annotated[float, BeforeValidator(in_centimetres), Field(ge=0, le=5)] | None = None
    # L'écart entre les colonnes d'une section, entre deux sections (ou deux blocs) côte à
    # côte, en cm ; l'espace après des sections côte à côte ou encadrées, en points ; le haut
    # d'une page de garde sans margin_top, en cm.
    column_gap: Annotated[float, BeforeValidator(in_centimetres), Field(ge=0, le=5)] | None = None
    beside_gap: Annotated[float, BeforeValidator(in_centimetres), Field(ge=0, le=5)] | None = None
    beside_space: Points | None = None
    cover_margin_top: Annotated[float, BeforeValidator(in_centimetres), Field(ge=0, le=20)] | None = None
    # Ce qu'écrivent l'en-tête, le pied, le filigrane : du contenu, pas un style.
    header: BandSpec | None = None
    footer: BandSpec | None = None
    watermark: Annotated[str, Field(max_length=40)] | None = None

    @model_validator(mode="before")
    @classmethod
    def _renamed(cls, value: object) -> object:
        return refuse_renamed(value, "page", {"size": "paper"})


def _tax_label(label: str) -> str:
    """Le libellé d'une ligne de TVA : {rate} (le taux), et au besoin {base} (sa base)."""
    fields = set(re.findall(r"\{([^{}]*)\}", label))
    if "rate" not in fields or not fields <= TAX_FIELDS:
        raise ValueError(
            f"labels.tax {label!r} : {{rate}} (le taux), et au besoin {{base}} (le montant HT de ce taux), sans autre champ — ex. « TVA {{rate}} »"
        )
    return label


class LabelsSpec(_Spec):
    table: Annotated[str, Field(min_length=1, max_length=MAX_LABEL)] | None = None
    figure: Annotated[str, Field(min_length=1, max_length=MAX_LABEL)] | None = None
    contents: Annotated[str, Field(min_length=1, max_length=MAX_LABEL)] | None = None
    date: Annotated[str, Field(min_length=1, max_length=MAX_LABEL)] | None = None
    signature: Annotated[str, Field(min_length=1, max_length=MAX_LABEL)] | None = None
    article: Annotated[str, Field(min_length=1, max_length=MAX_LABEL)] | None = None
    total: Annotated[str, Field(min_length=1, max_length=MAX_LABEL)] | None = None
    notes: Annotated[str, Field(min_length=1, max_length=MAX_LABEL)] | None = None
    subtotal: Annotated[str, Field(min_length=1, max_length=MAX_LABEL)] | None = None
    total_excl_tax: Annotated[str, Field(min_length=1, max_length=MAX_LABEL)] | None = None
    tax: Annotated[str, Field(min_length=1, max_length=MAX_LABEL), AfterValidator(_tax_label)] | None = None
    total_incl_tax: Annotated[str, Field(min_length=1, max_length=MAX_LABEL)] | None = None
    paid: Annotated[str, Field(min_length=1, max_length=MAX_LABEL)] | None = None
    due: Annotated[str, Field(min_length=1, max_length=MAX_LABEL)] | None = None
    participant: Annotated[str, Field(min_length=1, max_length=MAX_LABEL)] | None = None


class ImageSpec(_Spec):
    # Deux images ``half`` qui se suivent : côte à côte.
    side_by_side: bool | None = None
    # L'espace après une image sans légende, en points.
    space_after: Points | None = None

    @model_validator(mode="before")
    @classmethod
    def _renamed(cls, value: object) -> object:
        return refuse_renamed(value, "image", {"grid": "side_by_side"})


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
    series: Annotated[list[Color], Field(min_length=1, max_length=12)] | None = None
    text_color: Color | None = None
    grid_color: Color | None = None
    title_color: Color | None = None
    # L'espace après un graphique sans légende, en points.
    space_after: Points | None = None

    @model_validator(mode="before")
    @classmethod
    def _renamed(cls, value: object) -> object:
        renamed = {"colors": "series", "text": "text_color", "grid": "grid_color", "title": "title_color"}
        return refuse_renamed(value, "chart", renamed)


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
        renamed = [f"{old} → {new}" for old, new in RENAMED_STYLES.items() if old in entries]
        if renamed:
            raise ValueError(
                f"{' ; '.join(renamed)} : le style de base s'appelle body depuis 2.0.21 "
                "(docs/style_format.md)"
            )
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
                "fonts → font, sur body et sur chaque titre (fonts donne depuis 2.1.0 les "
                "fichiers TrueType d'une police, pour le PDF : fonts: {Inter: {regular: "
                "fonts/Inter-Regular.ttf}})"
            )
        if old:
            raise ValueError(
                f"style au format de la 2.0 : {' ; '.join(old)}. Un fichier de style est "
                "depuis 2.1.0 une liste de styles nommés (body, document_title, heading1…)"
            )
        moved = [found for name, given in entries.items() if isinstance(name, str) and (found := _moved_content(name, given))]
        if moved:
            raise ValueError(
                f"{' ; '.join(moved)} : le contenu de l'en-tête, du pied et du filigrane va dans "
                "page depuis 2.0.21 (docs/style_format.md) ; le style n'en garde que l'apparence"
            )
        data: dict[str, object] = {}
        styles: dict[str, object] = {}
        for name, given in entries.items():
            if not isinstance(name, str):
                raise ValueError(f"clé {name!r} : un nom de style ou de réglage")
            if name in SETTINGS:
                data[name] = given
                continue
            given = dict[str, object]() if given is None else given
            if name not in BASES and not (isinstance(given, Mapping) and "base" in cast(Mapping[object, object], given)):
                raise ValueError(f"{name} : ni un style prévu, ni un réglage ; un style à toi dit de quel style il part (base: note){_closest(name)}")
            styles[name] = given
        if len(styles) > MAX_STYLES:
            raise ValueError(f"{len(styles)} styles, au-delà de {MAX_STYLES}")
        data["styles"] = styles
        return data


# Le contenu que portaient les styles ``header``, ``footer`` et ``watermark`` avant 2.0.21,
# et où il va.
_MOVED: Final[Mapping[str, str]] = {
    "left": "left",
    "center": "center",
    "right": "right",
    "lines": "lines",
    "table": "table",
    "logo": "logo",
    "logo_width": "logo.width",
    "logo_position": "logo: true, dans la zone qui le porte",
    "first_page": "first_page",
}


def _moved_content(name: str, given: object) -> str:
    """Ce qu'un style ``header``, ``footer`` ou ``watermark`` écrit encore de son contenu,
    avec sa nouvelle place ; ``""`` s'il n'en écrit pas."""
    if name == "watermark":
        if isinstance(given, str):
            return f"watermark: {given} → page.watermark: {given}"
        if isinstance(given, Mapping) and "text" in cast(Mapping[object, object], given):
            return "watermark.text → page.watermark"
        return ""
    if name not in ("header", "footer") or not isinstance(given, Mapping):
        return ""
    keys = cast(Mapping[object, object], given)
    found = [f"{name}.{key} → page.{name}.{place}" for key, place in _MOVED.items() if key in keys]
    return " ; ".join(found)


def _old_fonts(value: object) -> bool:
    """``fonts`` au format de la 2.0 : ``body`` et ``headings``, un nom ou ``{name, size}``."""
    if not isinstance(value, Mapping):
        return False
    for given in cast(Mapping[object, object], value).values():
        if isinstance(given, str):
            return True
        if isinstance(given, Mapping) and {"name", "size"} & set(cast(Mapping[object, object], given)):
            return True
    return False


def describe(error: ValidationError, where: str) -> list[str]:
    """Les fautes d'un fichier de style, une par ligne : « <where>.heading1.colour — … »,
    ou « <where> : … » pour une faute de tout le fichier."""
    lines: list[str] = []
    for item in error.errors(include_url=False):
        loc = [str(part) for part in item["loc"]]
        unknown = loc[:1] == ["styles"] and len(loc) == 3 and item["type"] == "extra_forbidden"
        if loc[:1] == ["styles"]:
            loc = loc[1:]
        message = item["msg"].removeprefix("Value error, ")
        if unknown:
            # Une propriété hors du registre : la plus proche, s'il y en a une.
            found = difflib.get_close_matches(loc[-1], PROPERTY_NAMES, n=1, cutoff=0.6)
            message = "propriété inconnue" + (f" — vouliez-vous dire {found[0]} ?" if found else "")
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
    """Les logos d'un fichier de style : (où, fichier ou octets) ; ``page.header``,
    ``page.footer``."""
    page = spec.page
    if page is None:
        return []
    return [
        (f"page.{name}", band.logo.file)
        for name, band in (("header", page.header), ("footer", page.footer))
        if band is not None and band.logo is not None
    ]


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
            return "police « aperçu et impression seulement » (fsType 4) : sa licence ne permet pas un document modifiable"
        if rights & _RESTRICTED:
            return "police à embarquement interdit (fsType 2) : sa licence ne le permet pas"
    if rights & _NO_SUBSETTING:
        return "police à embarquer entière (fsType 0x100) : le PDF n'en embarque qu'une partie"
    if rights & _BITMAP_ONLY:
        return "police à embarquer en images seulement (fsType 0x200)"
    return None


# --- Le style résolu ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BandElement:
    """Un élément résolu d'un en-tête ou d'un pied : une zone, une ligne, une cellule. Ses
    lignes (une seule pour une zone ou une ligne), le style qui le met en forme (``None`` :
    celui de l'en-tête, du pied, ou de sa table), s'il porte le logo."""

    lines: tuple[str, ...] = ("",)
    style: str | None = None
    logo: bool = False

    @property
    def text(self) -> str:
        return self.lines[0]


@dataclass(frozen=True, slots=True)
class Share:
    """Une largeur en proportion de la largeur de la table, en pour cent."""

    percent: float


@dataclass(frozen=True, slots=True)
class BandTable:
    """La table résolue d'un en-tête ou d'un pied : les largeurs en cm (``None`` : auto)."""

    # En cm, en proportion de la largeur de la table (``Share``), ou ``None`` (auto).
    widths: tuple[float | Share | None, ...]
    rows: tuple[tuple[BandElement, ...], ...]
    # Le style qui l'habille (cadre, filets, écart entre colonnes) : ``None``, celui de
    # l'en-tête ou du pied.
    style: str | None = None


@dataclass(frozen=True, slots=True)
class BandRow:
    """Une ligne de l'en-tête ou du pied en mode table : une ligne de la table, ou une des
    ``lines``, sur toute la largeur (``line``)."""

    cells: tuple[BandElement, ...]
    line: bool = False


def _band_element(given: Mapping[str, Any]) -> BandElement:
    text = given.get("text")
    lines = given.get("lines")
    said: tuple[str, ...] = (text,) if text is not None else tuple(lines) if lines else ("",)
    return BandElement(lines=said, style=given.get("style"), logo=bool(given.get("logo")))


def _band_width(width: object) -> float | Share | None:
    if width == "auto":
        return None
    if isinstance(width, str):
        found = percent(width)
        assert isinstance(found, float)
        return Share(found)
    return float(cast(float, width))


def _band_table(given: Mapping[str, Any]) -> BandTable:
    return BandTable(
        widths=tuple(_band_width(width) for width in given["widths"]),
        rows=tuple(tuple(_band_element(cell) for cell in row) for row in given["rows"]),
        style=given.get("style"),
    )


# Le logo d'un en-tête ou d'un pied sans largeur ni écart : 3 cm, à 4 pt de son texte.
LOGO_WIDTH: Final = 3.0
LOGO_GAP: Final = 4.0


def _band(given: Mapping[str, Any]) -> Band | None:
    """Ce qu'écrit un en-tête ou un pied (``page.header``, ``page.footer``, fusionnés) ;
    ``None`` s'il n'écrit rien."""
    logo_given = cast(Mapping[str, Any] | None, given.get("logo"))
    logo = None
    if logo_given is not None and isinstance(logo_given.get("file"), bytes):
        logo = Logo(
            logo_given["file"],
            float(logo_given.get("width") or LOGO_WIDTH),
            float(logo_given["gap"]) if logo_given.get("gap") is not None else LOGO_GAP,
        )
    table = cast(Mapping[str, Any] | None, given.get("table"))
    zones = {name: cast(Mapping[str, Any] | None, given.get(name)) for name in ("left", "center", "right")}
    band = Band(
        left=_band_element(zones["left"]) if zones["left"] is not None else None,
        center=_band_element(zones["center"]) if zones["center"] is not None else None,
        right=_band_element(zones["right"]) if zones["right"] is not None else None,
        lines=tuple(_band_element(line) for line in cast(list[Mapping[str, Any]], given.get("lines") or [])),
        logo=logo,
        first_page=given.get("first_page") is not False,
        table=_band_table(table) if table is not None else None,
        logo_given=logo_given is not None,
    )
    return None if band.empty and band.first_page else band


def _fixed(width: float | Share, available: int) -> int:
    """Une largeur de colonne en twips : en cm, ou en proportion de ``available``."""
    if isinstance(width, Share):
        return round(available * width.percent / 100)
    return round(width * CM)


def band_columns(table: BandTable, available: int) -> list[int]:
    """La largeur de chaque colonne, en twips, pour ``available`` twips : les colonnes en cm
    ou en proportion, puis le reste partagé entre les ``auto`` (la dernière prend l'arrondi)."""
    fixed = [_fixed(width, available) for width in table.widths if width is not None]
    autos = [index for index, width in enumerate(table.widths) if width is None]
    rest = max(available - sum(fixed), 0)
    share = rest // len(autos) if autos else 0
    said = [_fixed(width, available) if width is not None else share for width in table.widths]
    if autos:
        said[autos[-1]] += rest - share * len(autos)
    return said


def band_frame(look: Look) -> tuple[tuple[str | None, str | None, str | None, str | None], str | None]:
    """Le cadre d'un en-tête ou d'un pied en mode table, autour de la table : ses filets
    (haut, bas, gauche, droite ; ``None`` sans filet), et son fond (``None`` sans fond)."""

    def rule(value: str | None) -> str | None:
        return value if value is not None and value != NONE else None

    every = rule(look.border)
    fill = look.fill if look.fill is not None and look.fill != NONE else None
    return (
        (
            rule(look.border_top) or every,
            rule(look.border_bottom) or every,
            rule(look.border_left) or every,
            rule(look.border_right) or every,
        ),
        fill,
    )


def band_rows(band: Band, *, header: bool) -> list[BandRow]:
    """Les lignes d'un en-tête ou d'un pied en mode table, du haut vers le bas : la table,
    puis les ``lines`` dans un en-tête ; les ``lines``, puis la table dans un pied."""
    table = band.table
    rows = [BandRow(row) for row in (table.rows if table else ())]
    lines = [BandRow((line,), line=True) for line in band.lines]
    return rows + lines if header else lines + rows


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
    border_right: str | None = None
    border: str | None = None
    border_space: Sides | None = None
    valign: VAlign | None = None
    borders: Borders | None = None
    line: str | None = None
    bands: str | None = None
    width: TableWidth | None = None
    position: Position | None = None
    padding: Sides | None = None
    column_gap: float | None = None
    numbered: bool | None = None
    levels: int | None = None
    page_numbers: bool | None = None
    level_indent: float | None = None
    marker_space: float | None = None
    sign_height: float | None = None
    label_width: float | None = None
    row_height: float | None = None


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
    subtotal: str = "Sous-total"
    total_excl_tax: str = "Total HT"
    tax: str = "TVA {rate} sur {base}"
    total_incl_tax: str = "Total TTC"
    paid: str = "Acompte versé"
    due: str = "Net à payer"
    participant: str = "Participant"


FRENCH: Final = Labels()


@dataclass(frozen=True, slots=True)
class Logo:
    data: bytes = field(repr=False)
    # Largeur dans la page, en cm ; l'écart à son texte, en points.
    width: float
    gap: float = LOGO_GAP


@dataclass(frozen=True, slots=True)
class Band:
    """Ce qu'écrit un en-tête ou un pied : trois zones, ou une table ; des lignes ; le logo,
    dans l'élément qui le demande."""

    left: BandElement | None = None
    center: BandElement | None = None
    right: BandElement | None = None
    lines: tuple[BandElement, ...] = ()
    logo: Logo | None = None
    first_page: bool = True
    table: BandTable | None = None
    # Un logo donné, même non lu (``logos`` le dit au chargeur) : pour vérifier sa place.
    logo_given: bool = field(default=False, compare=False)

    @property
    def zones(self) -> dict[str, BandElement | None]:
        return {"left": self.left, "center": self.center, "right": self.right}

    def elements(self) -> list[BandElement]:
        """Tous ses éléments : zones, lignes, cellules."""
        said = [element for element in self.zones.values() if element is not None]
        said += self.lines
        if self.table is not None:
            said += [cell for row in self.table.rows for cell in row]
        return said

    @property
    def empty(self) -> bool:
        return not (any(self.zones.values()) or self.lines or self.logo or self.table)


@dataclass(frozen=True, slots=True)
class ChartLook:
    """Les couleurs des graphiques : les séries, le texte, la grille, le titre ; l'espace
    après un graphique sans légende, en points."""

    colors: tuple[str, ...]
    text: str
    grid: str
    title: str
    space_after: float


@dataclass(frozen=True, slots=True)
class PageLook:
    """Les distances de la page, en twips (``cm_twips``) : l'en-tête et le pied au bord,
    l'écart entre les colonnes d'une section et entre deux sections côte à côte, le haut
    d'une page de garde sans ``margin_top`` ; en points, l'espace après des sections côte
    à côte ou encadrées et après une image sans légende."""

    header_distance: int
    footer_distance: int
    column_gap: int
    beside_gap: int
    cover_margin_top: int
    beside_space: float
    image_space_after: float


@dataclass(frozen=True, slots=True)
class Style:
    """Un style complet, prêt à rendre : chaque style nommé résolu, et les réglages."""

    looks: Mapping[str, Look]
    # Ce que chaque style écrit lui-même (les fichiers fusionnés, couleurs résolues) : un
    # style Word reprend son style de base, et n'écrit que ce qui lui est propre.
    declared: Mapping[str, Mapping[str, Any]]
    bases: Mapping[str, str | None]
    # Les distances de la page (``page`` et ``image``, dans default.yaml).
    distances: PageLook
    page: tuple[int, int] = PAGE_SIZES["A4"]
    landscape: bool = False
    # Haut, bas, gauche, droite, en twips.
    margins: tuple[int, int, int, int] = (1134, 1134, 1134, 1134)
    numbering: Numbering = "none"
    labels: Labels = FRENCH
    image_grid: bool = False
    chart: ChartLook = ChartLook((), "", "", "", 0.0)
    # Les fichiers TrueType des polices, par famille puis variante (``regular``…) : le PDF
    # les embarque.
    fonts: Mapping[str, Mapping[str, bytes]] = field(default_factory=dict[str, Mapping[str, bytes]])
    # Ce qu'écrivent l'en-tête, le pied, le filigrane (``page.header``, ``page.footer``,
    # ``page.watermark``) : ``None`` sans rien.
    header_band: Band | None = None
    footer_band: Band | None = None
    watermark_text: str | None = None

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
        """Le style et ses bases, du plus précis à ``body``."""
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

    def table_level(self, name: str) -> bool:
        """Un style de tableau (``table``, ``invoice_totals``, un style à toi qui en part),
        et non d'une de ses lignes (``table_header``, ``table_group``…) : son espace avant
        et après vaut autour du tableau."""
        chain = self.chain(name)
        return "table" in chain and not ROW_STYLES & set(chain)

    def cell_spacing(self, name: str | None, table: str, family: str) -> tuple[float, float]:
        """L'espace avant et après des paragraphes d'une cellule, en points : ce que son
        style (``name``) écrit en plus de celui de la table (``table``, ou ``family`` qu'ils
        partagent : ``table``, ``header``, ``footer``) ; 0 sans lui. L'espacement du style
        de la table vaut autour d'elle, pas dans ses cellules."""
        if name is None or name == table:
            return (0.0, 0.0)
        stop = table if table in self.chain(name) else family
        said = self.between(name, stop)
        return (float(said.get("space_before") or 0.0), float(said.get("space_after") or 0.0))

    def band(self, name: Literal["header", "footer"]) -> Band | None:
        """L'en-tête ou le pied de page ; ``None`` s'il n'écrit rien."""
        return self.header_band if name == "header" else self.footer_band

    def element_look(self, element: BandElement, base: str) -> Look:
        """Le style d'un élément d'en-tête ou de pied : le sien, sinon ``base``."""
        return self.looks[element.style] if element.style else self.looks[base]

    @property
    def header(self) -> Band | None:
        return self.band("header")

    @property
    def footer(self) -> Band | None:
        return self.band("footer")

    @property
    def watermark(self) -> str | None:
        return self.watermark_text or None


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
        if file.image is not None and file.image.side_by_side is not None:
            grid = file.image.side_by_side
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
    images: dict[str, Any] = {}
    for file in given:
        if file.image is not None:
            images.update(file.image.model_dump(exclude_none=True))
    distances = PageLook(
        header_distance=cm_twips(page["header_distance"]),
        footer_distance=cm_twips(page["footer_distance"]),
        column_gap=cm_twips(page["column_gap"]),
        beside_gap=cm_twips(page["beside_gap"]),
        cover_margin_top=cm_twips(page["cover_margin_top"]),
        beside_space=float(page["beside_space"]),
        image_space_after=float(images["space_after"]),
    )
    resolved = Style(
        looks=looks,
        declared=declared,
        bases=bases,
        distances=distances,
        page=PAGE_SIZES[page["paper"]],
        landscape=page["orientation"] == "landscape",
        margins=(
            cm_twips(page["margin_top"]),
            cm_twips(page["margin_bottom"]),
            cm_twips(page["margin_left"]),
            cm_twips(page["margin_right"]),
        ),
        numbering=numbering,
        labels=said,
        image_grid=grid,
        chart=chart,
        fonts={family: files for family, files in fonts.items() if "regular" in files},
        header_band=_band(page.get("header") or {}),
        footer_band=_band(page.get("footer") or {}),
        watermark_text=page.get("watermark"),
    )
    _check_bands(resolved, faults)
    if faults:
        raise ValueError(" ; ".join(dict.fromkeys(faults)))
    return resolved


def _check_bands(style: Style, faults: list[str]) -> None:
    """L'en-tête et le pied, dans le style résolu (tous les fichiers lus) : une table sans
    les trois zones ; le logo dans un seul élément ; le style de chaque élément et de la
    table, qui part de ``header`` ou de ``footer`` ; les largeurs de la table."""
    for name in ("header", "footer"):
        band = style.band(name)
        if band is None:
            continue
        where = f"page.{name}"
        table = band.table
        zones = [zone for zone, element in band.zones.items() if element is not None]
        if table is not None and zones:
            faults.append(
                f"{where}.table : pas avec {', '.join(zones)} (leurs textes vont dans les "
                f"cellules) ; les retirer, ou les effacer : {', '.join(f'{zone}: null' for zone in zones)}"
            )
        logos = sum(1 for element in band.elements() if element.logo)
        has_logo = band.logo is not None or band.logo_given
        if has_logo and logos == 0:
            faults.append(f"{where}.logo : le poser dans une zone ou une cellule (logo: true)")
        if logos > 1:
            faults.append(f"{where} : un seul élément porte le logo (logo: true)")
        if logos and not has_logo:
            faults.append(f"{where} : un élément demande le logo, mais {where} n'en a pas (logo:)")
        named = [element.style for element in band.elements() if element.style]
        if table is not None and table.style:
            named.append(table.style)
        for chosen in dict.fromkeys(named):
            if chosen not in style.looks:
                faults.append(f"{where} : style « {chosen} » inconnu")
            elif name not in style.chain(chosen):
                faults.append(
                    f"{where} : le style « {chosen} » ne part pas de {name} "
                    f"(base : {style.bases.get(chosen)})"
                )
        if table is None or (table.style and table.style not in style.looks):
            continue
        look = style.look(table.style or name)
        indent = round((look.indent or 0) * CM)
        available = style.text_width - 2 * indent
        fixed = sum(_fixed(width, available) for width in table.widths if width is not None)
        autos = sum(1 for width in table.widths if width is None)
        room = available - fixed
        if room < autos * round(MIN_AUTO_WIDTH * CM) or room < 0:
            faults.append(
                f"{where}.table : {fixed / CM:.4g} cm de colonnes pour {available / CM:.4g} cm de "
                f"largeur utile"
                + (f", et {MIN_AUTO_WIDTH:g} cm au moins par colonne auto" if autos else "")
            )
        gap = round((look.column_gap or 0) * CM)
        if gap and any(width <= gap for width in band_columns(table, available)):
            faults.append(f"{where}.table : une colonne n'est pas plus large que l'écart (column_gap)")


_COLORED: Final = frozenset({"color", "fill", "bands"})
_RULES: Final = frozenset({"border_top", "border_bottom", "border_left", "border_right", "border", "line"})


def _named_colors(name: str, prop: str, value: Any, colors: Mapping[str, str], faults: list[str]) -> Any:
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
            renamed = " : le style de base s'appelle body depuis 2.0.21" if base == "text" else ""
            faults.append(f"style {name} : base inconnue « {base} »{renamed}")
            bases[name] = "body"
    for name in sorted(bases):
        seen = [name]
        current = bases[name]
        while current is not None:
            if current in seen:
                loop = " → ".join([*seen[seen.index(current) :], current])
                faults.append(f"style {name} : base en boucle ({loop})")
                bases[name] = "body" if name != "body" else None
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
                faults.append(f"style {name}.{prop} : propre à {', '.join(sorted(owners))} (et aux styles qui en partent)")


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
    return Look(**{**inherited, **own})


def _page(given: Iterable[StyleFile]) -> dict[str, Any]:
    page: dict[str, Any] = {}
    for file in given:
        if file.page is None:
            continue
        said = file.page.model_dump(exclude_none=True, exclude={"header", "footer"})
        margins = said.pop("margins", None)
        if margins is not None:
            for side in ("top", "bottom", "left", "right"):
                page[f"margin_{side}"] = margins
        page.update(said)
        # L'en-tête et le pied : clé par clé, d'un fichier à l'autre ; null efface.
        bands = file.page.model_dump(exclude_unset=True, include={"header", "footer"})
        for name, band in bands.items():
            if band is None:
                page[name] = {}
            else:
                page[name] = {**page.get(name, {}), **band}
    return page


def _chart(given: Iterable[StyleFile], colors: Mapping[str, str], faults: list[str]) -> ChartLook:
    said: dict[str, Any] = {}
    for file in given:
        if file.chart is not None:
            said.update(file.chart.model_dump(exclude_none=True))

    def color(value: str) -> str:
        return _named_colors("chart", "color", value, colors, faults) if value else value

    return ChartLook(
        colors=tuple(color(value) for value in said.get("series", ())),
        text=color(said.get("text_color", "")),
        grid=color(said.get("grid_color", "")),
        title=color(said.get("title_color", "")),
        space_after=float(said.get("space_after", 0.0)),
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
