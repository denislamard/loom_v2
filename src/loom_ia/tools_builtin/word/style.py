# SPDX-License-Identifier: Apache-2.0
"""Le style d'un .docx en WordprocessingML : ses styles, ses numérotations, ses en-têtes.

Tout part d'un ``Style`` résolu (``_style_params``) : ``default.yaml``, la charte de
l'outil, le style du modèle suivi. Chaque style nommé devient un style Word, basé sur
le style Word de sa base, qui n'écrit que ce qui lui est propre : Word reprend le reste
de sa base. Sans charte, ce que donne ``default.yaml`` est le même, octet pour octet,
qu'avant les fichiers de style : ``styles.xml``, ``numbering.xml`` et ``footer1.xml``
d'un document sans style ne changent pas.

Ce module ne fixe aucune valeur visuelle : il traduit les propriétés des styles
(police, taille, couleur, espacements, filets) en éléments WordprocessingML, dans
l'ordre du schéma, et ajoute la mécanique (un titre reste avec ce qui le suit, son
niveau de plan, la numérotation des titres, les retraits des listes).

Un style à toi que le document emploie (un modèle le choisit pour une section, ou le
modèle de langage pour un bloc) devient un style Word du même nom, basé sur le style
Word de sa base : il paraît dans la galerie de Word. Les styles que seule une
disposition demande (en-tête, tableau de disposition) ne sont écrits que si le document
s'en sert.
"""

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Final
from xml.sax.saxutils import escape, quoteattr

from loom_ia.tools_builtin._style_params import (
    NONE,
    Band,
    BandElement,
    Language,
    Look,
    Numbering,
    Sides,
    Style,
    band_columns,
    band_frame,
    band_rows,
    cm_twips,
    parse_rule,
)

_W: Final = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_HEAD: Final = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
_VML: Final = (
    'xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office"'
)
_PLACEHOLDER: Final = re.compile(r"\{(page|pages|title)\}")
# La numérotation des titres (réglage ``numbering``), à part des listes.
HEADING_NUMBERING: Final = 900
# Les styles que seule une disposition demande, par identifiant.
HEADER: Final = "Header"
LAYOUT: Final = "LoomLayout"
# Le nom du style de tableau de disposition : la relecture le reconnaît.
LAYOUT_NAME: Final = "loom layout"
# Les blocs signature et toc, et la liste à cocher : leurs styles, que la relecture
# reconnaît par leur nom.
SIGNATURE: Final = "LoomSignature"
SIGNATURE_NAME: Final = "loom signature"
SIGNER: Final = "LoomSigner"
SIGNER_NAME: Final = "loom signer"
MENTION: Final = "LoomMention"
MENTION_NAME: Final = "loom mention"
CHECK: Final = "LoomCheck"
CHECK_2: Final = "LoomCheck2"
CHECK_NAMES: Final = {CHECK: "loom checkbox", CHECK_2: "loom checkbox 2"}
# La case : un glyphe, et la police de symboles de Windows qui l'a (LibreOffice en prend
# une autre qui l'a). Une mécanique, comme les puces : pas une valeur de charte.
CHECK_FONT: Final = "Segoe UI Symbol"
CHECKED: Final = "\u2612"
UNCHECKED: Final = "\u2610"
# Le sommaire : les styles intégrés de Word (« TOC Heading », « toc 1 »…), qu'il reprend
# à la mise à jour du champ.
# Le bloc code, les champs (un tableau sans bordure, son libellé, sa valeur), le lien, la
# note de bas de page (son texte, son appel).
CODE: Final = "LoomCode"
FIELDS: Final = "LoomFields"
FIELDS_NAME: Final = "loom fields"
FIELD_LABEL: Final = "LoomFieldLabel"
FIELD_VALUE: Final = "LoomFieldValue"
HYPERLINK: Final = "Hyperlink"
FOOTNOTE_TEXT: Final = "FootnoteText"
FOOTNOTE_REFERENCE: Final = "FootnoteReference"
# Les lignes d'un groupe et de son sous-total, et le récapitulatif de TVA : un style de
# paragraphe à leur nom, que la relecture reconnaît.
TABLE_GROUP: Final = "LoomTableGroup"
TABLE_SUBTOTAL: Final = "LoomTableSubtotal"
INVOICE_TOTALS: Final = "LoomInvoiceTotals"
# La feuille d'émargement : le style de paragraphe de ses cellules.
ATTENDANCE: Final = "LoomAttendance"
TOC_HEADING: Final = "TOCHeading"
TOC_LEVELS: Final = ("TOC1", "TOC2", "TOC3")
# Le corps du texte, quand le style ``paragraph`` dit autre chose que ``text``.
BODY: Final = "BodyText"
_JC: Final = {"left": "left", "center": "center", "right": "right", "justify": "both"}
# La langue déclarée du document (correcteur, césure).
LANGUAGES: Final[Mapping[Language, str]] = {"fr": "fr-FR", "en": "en-US"}

# L'ordre des enfants de ``pPr`` et de ``rPr`` que le schéma impose.
_PPR: Final = (
    "pStyle",
    "keepNext",
    "keepLines",
    "pageBreakBefore",
    "numPr",
    "pBdr",
    "shd",
    "tabs",
    "spacing",
    "ind",
    "jc",
    "outlineLvl",
)
_RPR: Final = ("rFonts", "b", "i", "caps", "vanish", "color", "sz", "u", "lang")
# Les côtés d'un filet de paragraphe, et leur place dans ``border_space`` (l'ordre de CSS).
_SIDE_OF: Final = {"top": 0, "right": 1, "bottom": 2, "left": 3}
_RULES: Final = ("border_top", "border_bottom", "border_left", "border_right", "border")
_SPACING: Final = ("space_before", "space_after")
# Ce que ``text`` met dans les valeurs par défaut du document, plutôt que dans Normal.
_DEFAULT_RUN: Final = frozenset({"font", "color", "size"})
_DEFAULT_PARAGRAPH: Final = frozenset({"space_before", "space_after", "line_spacing"})

type Pieces = list[tuple[str, str]]
# Ce qu'un style de tableau dit du tableau et de ses cellules, et pas de leur texte.
_CELL_PROPS: Final = frozenset(
    {"border", "border_top", "border_bottom", "border_left", "border_right", "fill"}
)


@dataclass(frozen=True, slots=True)
class Watermark:
    """Le filigrane : son texte, sa police, sa couleur."""

    text: str
    font: str
    color: str


def watermark(style: Style) -> Watermark | None:
    """Le filigrane : son texte (``page.watermark``), la police et la couleur du style
    ``watermark`` ; ``None`` sans texte."""
    text = style.watermark
    if not text:
        return None
    look = style.look("watermark")
    return Watermark(text, look.font or "", look.color or "")


def jc(align: str) -> str:
    """L'alignement WordprocessingML d'un alignement de style (« justify » → « both »)."""
    return _JC[align]


def half_points(size: float) -> int:
    return round(size * 2)


def twips(points: float) -> int:
    return round(points * 20)


def _flag(name: str, value: bool, *, complex_script: bool = True) -> str:
    off = "" if value else ' w:val="0"'
    said = f"<w:{name}{off}/>"
    return said + (f"<w:{name}Cs{off}/>" if complex_script else "")


def fonts(name: str) -> str:
    return f'<w:rFonts w:ascii="{name}" w:hAnsi="{name}" w:eastAsia="{name}" w:cs="{name}"/>'


def _size(points: float) -> str:
    value = half_points(points)
    return f'<w:sz w:val="{value}"/><w:szCs w:val="{value}"/>'


def run_pieces(props: Mapping[str, Any]) -> Pieces:
    """Les propriétés de caractère d'un style : police, gras, italique, majuscules,
    couleur, taille, soulignement."""
    pieces: Pieces = []
    if (font := props.get("font")) is not None:
        pieces.append(("rFonts", fonts(font)))
    if (bold := props.get("bold")) is not None:
        pieces.append(("b", _flag("b", bold)))
    if (italic := props.get("italic")) is not None:
        pieces.append(("i", _flag("i", italic)))
    if (uppercase := props.get("uppercase")) is not None:
        pieces.append(("caps", _flag("caps", uppercase, complex_script=False)))
    if (color := props.get("color")) is not None:
        pieces.append(("color", f'<w:color w:val="{color}"/>'))
    if (size := props.get("size")) is not None:
        pieces.append(("sz", _size(size)))
    if (underline := props.get("underline")) is not None:
        pieces.append(("u", f'<w:u w:val="{"single" if underline else "none"}"/>'))
    return pieces


def _side(side: str, value: str, space: Sides | None) -> str:
    """Un filet de paragraphe ; son écart au texte, ``border_space`` de son côté, en points
    entiers (Word n'en écrit pas d'autres)."""
    rule = parse_rule(value)
    if rule is None:
        return f'<w:{side} w:val="nil"/>'
    width, color = rule
    gap = round(space[_SIDE_OF[side]]) if space is not None else 0
    return (
        f'<w:{side} w:val="single" w:sz="{round(width * 8)}" w:space="{gap}" '
        f'w:color="{color}"/>'
    )


def paragraph_pieces(props: Mapping[str, Any], space: Sides | None = None) -> Pieces:
    """Les propriétés de paragraphe d'un style : filets (à ``space`` du texte, le
    ``border_space`` du style résolu), fond, espacements, retrait, alignement."""
    pieces: Pieces = []
    every = props.get("border")
    sides = [
        _side(side, value, space)
        for side, prop in (
            ("top", "border_top"),
            ("left", "border_left"),
            ("bottom", "border_bottom"),
            ("right", "border_right"),
        )
        if (value := props.get(prop) or every) is not None
    ]
    if sides:
        pieces.append(("pBdr", f"<w:pBdr>{''.join(sides)}</w:pBdr>"))
    if (fill := props.get("fill")) is not None:
        shown = "auto" if fill == NONE else fill
        pieces.append(("shd", f'<w:shd w:val="clear" w:color="auto" w:fill="{shown}"/>'))
    spacing = ""
    if (before := props.get("space_before")) is not None:
        spacing += f' w:before="{twips(before)}"'
    if (after := props.get("space_after")) is not None:
        spacing += f' w:after="{twips(after)}"'
    if (line := props.get("line_spacing")) is not None:
        spacing += f' w:line="{round(line * 240)}" w:lineRule="auto"'
    if spacing:
        pieces.append(("spacing", f"<w:spacing{spacing}/>"))
    if (indent := props.get("indent")) is not None:
        amount = round(indent * 567)
        pieces.append(("ind", f'<w:ind w:left="{amount}" w:right="{amount}"/>'))
    if (align := props.get("align")) is not None:
        pieces.append(("jc", f'<w:jc w:val="{jc(align)}"/>'))
    return pieces


def ordered(pieces: Pieces, order: tuple[str, ...]) -> str:
    """Les éléments dans l'ordre du schéma (un tri stable : les mêmes, dans l'ordre donné)."""
    return "".join(xml for _, xml in sorted(pieces, key=lambda piece: order.index(piece[0])))


def run_xml(props: Mapping[str, Any]) -> str:
    return ordered(run_pieces(props), _RPR)


def _style(
    style_id: str,
    name: str,
    *,
    kind: str = "paragraph",
    based: str | None = "Normal",
    next_style: str | None = None,
    paragraph: str = "",
    run: str = "",
    default: bool = False,
) -> str:
    said = f'<w:style w:type="{kind}" w:styleId="{style_id}"'
    said += ' w:default="1">' if default else ">"
    said += f'<w:name w:val="{name}"/>'
    if based:
        said += f'<w:basedOn w:val="{based}"/>'
    if next_style:
        said += f'<w:next w:val="{next_style}"/>'
    said += "<w:qFormat/>"
    if paragraph:
        said += f"<w:pPr>{paragraph}</w:pPr>"
    if run:
        said += f"<w:rPr>{run}</w:rPr>"
    return said + "</w:style>"


def _named(
    style: Style,
    style_id: str,
    word_name: str,
    loom: str,
    *,
    based: str = "Normal",
    stop: str = "body",
    next_style: str | None = None,
    mechanics: Pieces | None = None,
) -> str:
    """Un style Word tiré d'un style nommé : ce qu'il écrit en plus de ``stop``, le style
    loom de son style Word de base, et la mécanique (``mechanics``). Les filets et le fond
    d'un tableau sont ceux du tableau et de ses cellules, pas du texte d'une cellule."""
    props = style.between(loom, stop)
    own = style.look(loom)
    if "table" in style.chain(loom):
        props = {prop: value for prop, value in props.items() if prop not in _CELL_PROPS}
        if style.table_level(loom):
            # L'espacement d'un style de tableau vaut autour du tableau : ses cellules n'ont
            # que celui de leur style de ligne, et rien de celui de Normal.
            props = {prop: value for prop, value in props.items() if prop not in _SPACING}
            if loom == "table":
                body = style.look("body")
                props.update({prop: 0.0 for prop in _SPACING if getattr(body, prop)})
    elif "border_space" in props:
        # Un nouvel écart : les filets du style, réécrits à cet écart.
        props = {prop: getattr(own, prop) for prop in _RULES if getattr(own, prop) is not None} | props
    return _style(
        style_id,
        word_name,
        based=based,
        next_style=next_style,
        paragraph=ordered([*(mechanics or []), *paragraph_pieces(props, own.border_space)], _PPR),
        run=run_xml(props),
    )


def _heading(style: Style, level: int) -> str:
    numbered: Pieces = (
        [
            (
                "numPr",
                f'<w:numPr><w:ilvl w:val="{level - 1}"/>'
                f'<w:numId w:val="{HEADING_NUMBERING}"/></w:numPr>',
            )
        ]
        if style.numbering != "none"
        else []
    )
    return _named(
        style,
        f"Heading{level}",
        f"heading {level}",
        f"heading{level}",
        next_style="Normal",
        mechanics=[
            ("keepNext", "<w:keepNext/>"),
            ("keepLines", "<w:keepLines/>"),
            *numbered,
            ("outlineLvl", f'<w:outlineLvl w:val="{level - 1}"/>'),
        ],
    )


def paragraph_style(style: Style) -> str:
    """Le style Word des paragraphes : Normal, ou « Body Text » si ``paragraph`` le règle."""
    return BODY if style.between("paragraph", "body") else "Normal"


# Le style Word de chaque style prévu qui en a un (un paragraphe) : la base Word d'un style
# à toi qui en part.
WORD_IDS: Final[Mapping[str, str]] = {
    "body": "Normal",
    "document_title": "Title",
    "heading1": "Heading1",
    "heading2": "Heading2",
    "heading3": "Heading3",
    "list": "ListParagraph",
    "note": "Note",
    "caption": "Caption",
    "quote": "Quote",
    "quote_source": "QuoteSource",
    "table": "TableText",
    "toc": TOC_HEADING,
    "signature": SIGNER,
    "signature_mention": MENTION,
    "header": HEADER,
    "footer": "Footer",
    "code": CODE,
    "footnote": FOOTNOTE_TEXT,
    "fields": FIELD_VALUE,
    "fields_label": FIELD_LABEL,
    "table_group": TABLE_GROUP,
    "table_subtotal": TABLE_SUBTOTAL,
    "invoice_totals": INVOICE_TOTALS,
    "attendance": ATTENDANCE,
}


def word_id(style: Style, name: str) -> str:
    """Le style Word d'un style nommé : celui d'un style prévu, ou son nom pour un style à
    toi (``paragraph`` : Normal, ou « Body Text » s'il dit autre chose que ``text``)."""
    if name == "paragraph":
        return paragraph_style(style)
    return WORD_IDS.get(name, name)


def _custom(style: Style, used: Iterable[str]) -> str:
    """Les styles à toi que le document emploie, et leurs bases à toi : chacun basé sur le
    style Word de sa base, n'écrivant que ce qui lui est propre."""
    wanted: set[str] = set()
    for name in used:
        wanted.update(
            step for step in style.chain(name) if step not in WORD_IDS and step != "paragraph"
        )
    said = ""
    for name in sorted(wanted):
        base = style.bases.get(name) or "body"
        said += _named(
            style, name, name, name, based=word_id(style, base), stop=base, next_style=None
        )
    return said


def styles_xml(style: Style, used: Iterable[str] = (), custom: Iterable[str] = ()) -> str:
    """``styles.xml`` : un style Word par style nommé que l'outil emploie, et ceux que le
    corps demande ; ``custom`` : les styles à toi qu'il emploie."""
    wanted = set(used)
    text = style.between("body", None)
    language = LANGUAGES[style.language]
    defaults_run = ordered(
        [
            *run_pieces({p: v for p, v in text.items() if p in _DEFAULT_RUN}),
            ("lang", f'<w:lang w:val="{language}" w:eastAsia="{language}" w:bidi="ar-SA"/>'),
        ],
        _RPR,
    )
    space = style.look("body").border_space
    defaults_paragraph = ordered(
        paragraph_pieces({p: v for p, v in text.items() if p in _DEFAULT_PARAGRAPH}, space), _PPR
    )
    normal = {p: v for p, v in text.items() if p not in _DEFAULT_RUN | _DEFAULT_PARAGRAPH}
    said = (
        _HEAD + f'<w:styles xmlns:w="{_W}">'
        f"<w:docDefaults><w:rPrDefault><w:rPr>{defaults_run}</w:rPr></w:rPrDefault>"
        f"<w:pPrDefault><w:pPr>{defaults_paragraph}</w:pPr></w:pPrDefault></w:docDefaults>"
        + _style(
            "Normal",
            "Normal",
            based=None,
            default=True,
            paragraph=ordered(paragraph_pieces(normal, space), _PPR),
            run=run_xml(normal),
        )
        + _named(style, "Title", "Title", "document_title", next_style="Normal")
        + _heading(style, 1)
        + _heading(style, 2)
        + _heading(style, 3)
        + _named(style, "ListParagraph", "List Paragraph", "list")
        + _named(style, "Quote", "Quote", "quote", next_style="Normal")
        + _named(
            style,
            "QuoteSource",
            "Quote Source",
            "quote_source",
            based="Quote",
            stop="quote",
            next_style="Normal",
        )
        + _named(style, "Note", "Note", "note", next_style="Normal")
        + _named(style, "Caption", "caption", "caption", next_style="Normal")
        + _named(style, "TableText", "Table Text", "table")
        + _named(style, "Footer", "footer", "footer")
    )
    if paragraph_style(style) == BODY:
        said += _named(style, BODY, "Body Text", "paragraph")
    if HEADER in wanted:
        said += _named(style, HEADER, "header", "header")
    if LAYOUT in wanted:
        said += (
            f'<w:style w:type="table" w:styleId="{LAYOUT}"><w:name w:val="{LAYOUT_NAME}"/>'
            "<w:tblPr/></w:style>"
        )
    return said + _extra_styles(style, wanted) + _custom(style, custom) + "</w:styles>"


def _character(style_id: str, name: str, run: str) -> str:
    """Un style de caractère : le lien, l'appel de note."""
    return (
        f'<w:style w:type="character" w:styleId="{style_id}"><w:name w:val="{name}"/>'
        f"<w:qFormat/><w:rPr>{run}</w:rPr></w:style>"
    )


def _extra_styles(style: Style, wanted: set[str]) -> str:
    """Les styles des blocs signature, toc, code et champs, de la liste à cocher, du lien et
    des notes, des groupes de tableau, du récapitulatif de TVA et de l'émargement, que le
    document emploie."""
    said = ""
    if CODE in wanted:
        said += _named(
            style,
            CODE,
            "loom code",
            "code",
            mechanics=[("keepLines", "<w:keepLines/>")],
        )
    if FIELDS in wanted:
        said += (
            f'<w:style w:type="table" w:styleId="{FIELDS}"><w:name w:val="{FIELDS_NAME}"/>'
            "<w:tblPr/></w:style>"
        )
    if FIELD_VALUE in wanted:
        said += _named(style, FIELD_VALUE, "loom field value", "fields")
        said += _named(
            style, FIELD_LABEL, "loom field label", "fields_label", based=FIELD_VALUE, stop="fields"
        )
    if TABLE_GROUP in wanted:
        said += _named(
            style,
            TABLE_GROUP,
            "loom table group",
            "table_group",
            based="TableText",
            stop="table",
            mechanics=[("keepNext", "<w:keepNext/>")],
        )
    if TABLE_SUBTOTAL in wanted:
        said += _named(
            style,
            TABLE_SUBTOTAL,
            "loom table subtotal",
            "table_subtotal",
            based="TableText",
            stop="table",
        )
    if INVOICE_TOTALS in wanted:
        said += _named(
            style,
            INVOICE_TOTALS,
            "loom invoice totals",
            "invoice_totals",
            based="TableText",
            stop="table",
        )
    if ATTENDANCE in wanted:
        said += _named(
            style, ATTENDANCE, "loom attendance", "attendance", based="TableText", stop="table"
        )
    if HYPERLINK in wanted:
        said += _character(HYPERLINK, "Hyperlink", run_xml(style.between("link", "body")))
    if FOOTNOTE_TEXT in wanted:
        said += _named(style, FOOTNOTE_TEXT, "footnote text", "footnote")
        said += _character(
            FOOTNOTE_REFERENCE, "footnote reference", '<w:vertAlign w:val="superscript"/>'
        )
    if SIGNATURE in wanted:
        said += (
            f'<w:style w:type="table" w:styleId="{SIGNATURE}"><w:name w:val="{SIGNATURE_NAME}"/>'
            "<w:tblPr/></w:style>"
        )
    keep: Pieces = [("keepNext", "<w:keepNext/>")]
    if SIGNER in wanted:
        said += _named(style, SIGNER, SIGNER_NAME, "signature", mechanics=keep)
    if MENTION in wanted:
        said += _named(style, MENTION, MENTION_NAME, "signature_mention", mechanics=keep)
    for check, level in ((CHECK, 0), (CHECK_2, 1)):
        if check in wanted:
            left = 680 + 340 * level
            said += _style(
                check,
                CHECK_NAMES[check],
                based="ListParagraph",
                paragraph=f'<w:tabs><w:tab w:val="left" w:pos="{left}"/></w:tabs>'
                f'<w:ind w:left="{left}" w:hanging="340"/>',
            )
    if TOC_HEADING in wanted:
        said += _named(
            style, TOC_HEADING, "TOC Heading", "toc", next_style="Normal", mechanics=keep
        )
    step = cm_twips(style.look("toc").level_indent or 0)
    for level, toc in enumerate(TOC_LEVELS):
        if toc in wanted:
            said += _toc_entry(style, toc, level, step)
    return said


def _toc_entry(style: Style, style_id: str, level: int, step: int) -> str:
    """Une entrée du sommaire (« toc 1 » à « toc 3 ») : le style ``toc1`` à ``toc3``, à
    gauche, en retrait de ``toc.level_indent`` par niveau (et de son ``indent``)."""
    loom = f"toc{level + 1}"
    props = style.between(loom, "body")
    indent = props.pop("indent", None)
    pieces = paragraph_pieces(props, style.look(loom).border_space)
    margin = cm_twips(indent or 0)
    left = margin + step * level
    if left or margin:
        right = f' w:right="{margin}"' if margin else ""
        pieces.append(("ind", f'<w:ind w:left="{left}"{right}/>'))
    return _style(
        style_id,
        f"toc {level + 1}",
        next_style="Normal",
        paragraph=ordered(pieces, _PPR),
        run=run_xml(props),
    )


# --- Numérotations -----------------------------------------------------------------

# Puces et numéros : deux niveaux, la sous-liste en retrait ; un troisième pour une liste
# qui en a un (une définition à part : les documents à deux niveaux restent les mêmes).
_LEVELS: Final = {
    False: (("bullet", "•"), ("bullet", "\u2013"), ("bullet", "\u25aa")),
    True: (("decimal", "%1."), ("lowerLetter", "%2)"), ("lowerRoman", "%3.")),
}


def _heading_texts(numbering: Numbering, article: str) -> tuple[str, str, str]:
    """Le numéro de chaque niveau de titre : « 1. », ou « Article 1 » et un tiret
    (``labels.article``)."""
    first = f"{article} %1 \u2013" if numbering == "article" else "%1."
    return (first, "%1.%2", "%1.%2.%3")


def _abstract(number: int, ordered: bool, indent: tuple[int, int], depth: int = 2) -> str:
    """Une numérotation de liste : chaque niveau en retrait de ``indent[0]`` (twips), la
    puce ou le numéro ``indent[1]`` avant le texte (``list.level_indent``, ``marker_space``)."""
    step, marker = indent
    said = f'<w:abstractNum w:abstractNumId="{number}"><w:multiLevelType w:val="hybridMultilevel"/>'
    for level, (kind, text) in enumerate(_LEVELS[ordered][:depth]):
        left = step * (level + 1)
        said += (
            f'<w:lvl w:ilvl="{level}"><w:start w:val="1"/><w:numFmt w:val="{kind}"/>'
            f'<w:lvlText w:val="{text}"/><w:lvlJc w:val="left"/>'
            f'<w:pPr><w:ind w:left="{left}" w:hanging="{marker}"/></w:pPr></w:lvl>'
        )
    return said + "</w:abstractNum>"


def _heading_abstract(numbering: Numbering, article: str) -> str:
    """Les titres numérotés : un numéro suivi d'une espace, lié aux styles de titre."""
    said = '<w:abstractNum w:abstractNumId="2"><w:multiLevelType w:val="multilevel"/>'
    for level, text in enumerate(_heading_texts(numbering, article)):
        said += (
            f'<w:lvl w:ilvl="{level}"><w:start w:val="1"/><w:numFmt w:val="decimal"/>'
            f'<w:pStyle w:val="Heading{level + 1}"/><w:suff w:val="space"/>'
            f"<w:lvlText w:val={quoteattr(text)}/>"
            '<w:lvlJc w:val="left"/><w:pPr><w:ind w:left="0" w:firstLine="0"/></w:pPr></w:lvl>'
        )
    return said + "</w:abstractNum>"


# Les définitions à trois niveaux : à puces, numérotée.
_DEEP: Final = 3


def numbering_xml(
    lists: Iterable[tuple[bool, bool]],
    indent: tuple[int, int],
    headings: Numbering = "none",
    article: str = "Article",
) -> str:
    """Une numérotation par liste (numérotée ?, à trois niveaux ?) : chaque liste numérotée
    repart de 1. Les définitions (``abstractNum``) précèdent toutes les numérotations
    (``num``) : Word l'exige."""
    lists = list(lists)
    said = _HEAD + f'<w:numbering xmlns:w="{_W}">' + _abstract(0, False, indent) + _abstract(1, True, indent)
    if headings != "none":
        said += _heading_abstract(headings, article)
    for ordered in (False, True):
        if (ordered, True) in lists:
            said += _abstract(_DEEP + int(ordered), ordered, indent, depth=3)
    for number, (ordered, deep) in enumerate(lists, start=1):
        definition = int(ordered) + (_DEEP if deep else 0)
        said += f'<w:num w:numId="{number}"><w:abstractNumId w:val="{definition}"/>'
        said += "".join(
            f'<w:lvlOverride w:ilvl="{level}"><w:startOverride w:val="1"/></w:lvlOverride>'
            for level in range(3 if deep else 2)
        )
        said += "</w:num>"
    if headings != "none":
        said += f'<w:num w:numId="{HEADING_NUMBERING}"><w:abstractNumId w:val="2"/></w:num>'
    return said + "</w:numbering>"


# --- En-têtes et pieds de page --------------------------------------------------------


def _text(text: str, title: str, run: str = "") -> str:
    """Un texte d'en-tête : ``{page}`` et ``{pages}`` en champs, ``{title}`` le titre du
    document, le reste en segments ; ``run`` : leurs propriétés de caractère (le style d'une
    zone)."""
    said = ""
    position = 0
    props = f"<w:rPr>{run}</w:rPr>" if run else ""
    for found in _PLACEHOLDER.finditer(text):
        said += _plain(text[position : found.start()], props)
        name = found.group(1)
        if name == "page":
            said += f'<w:fldSimple w:instr=" PAGE "><w:r>{props}<w:t>1</w:t></w:r></w:fldSimple>'
        elif name == "pages":
            said += f'<w:fldSimple w:instr=" NUMPAGES "><w:r>{props}<w:t>1</w:t></w:r></w:fldSimple>'
        else:
            said += _plain(title, props)
        position = found.end()
    return said + _plain(text[position:], props)


def _logo_gap(band: Band | None) -> str:
    """L'écart entre le logo et son texte (``logo.gap``) : une espace insécable d'un point,
    suivie de cet espacement de caractères — ce que Word et LibreOffice font tous deux
    (LibreOffice ignore l'espacement d'un caractère sans largeur, et ``effectExtent``)."""
    if band is None or band.logo is None or not band.logo.gap:
        return ""
    return (
        f'<w:r><w:rPr><w:spacing w:val="{twips(band.logo.gap)}"/><w:sz w:val="2"/>'
        '<w:szCs w:val="2"/></w:rPr><w:t xml:space="preserve">\u00a0</w:t></w:r>'
    )


def _with_logo(logo: str, band: Band | None, text: str) -> str:
    """Le logo, et son écart s'il est suivi d'un texte."""
    return logo + (_logo_gap(band) if text else "")


def _plain(text: str, props: str = "") -> str:
    return f'<w:r>{props}<w:t xml:space="preserve">{escape(text)}</w:t></w:r>' if text else ""


def band_xml(
    kind: str,
    band: Band | None,
    *,
    title: str,
    width: int,
    logo: str = "",
    watermark: Watermark | None = None,
    style: Style | None = None,
    word: Callable[[str], str] | None = None,
) -> str:
    """Un en-tête (``hdr``) ou un pied de page (``ftr``).

    ``logo`` : le segment qui porte le logo, déjà écrit (sa relation est propre à la
    partie). Trois zones sur une ligne — gauche, centre, droite, par des taquets —, et
    des lignes centrées : sous la ligne des zones dans un en-tête, au-dessus dans un
    pied de page (les mentions, puis le numéro de page). Avec une table (``table``), la
    table remplace la ligne des zones, et les lignes y deviennent des lignes de toute la
    largeur (``_band_table``) ; ``style`` : le style résolu, ``word`` : le style Word d'un
    style nommé (un style de cellule à toi y est écrit).
    """
    if band is not None and band.table is not None and style is not None:
        # L'espacement du style de la table vaut autour d'elle : un paragraphe de sa hauteur
        # avant, l'espace avant le paragraphe qui la clôt après.
        around = style.look(band.table.style or ("header" if kind == "hdr" else "footer"))
        before = twips(around.space_before or 0)
        return (
            _HEAD
            + f'<w:{kind} xmlns:w="{_W}"{f" {_VML}" if watermark else ""}>'
            + (_band_space(kind, style, before) if before else "")
            + _band_table(kind, band, style, title=title, width=width, logo=logo, word=word)
            + _band_end(kind, style, watermark, before=twips(around.space_after or 0))
            + f"</w:{kind}>"
        )
    name = "header" if kind == "hdr" else "footer"
    style_id = HEADER if kind == "hdr" else "Footer"
    centered = '<w:jc w:val="center"/>'

    def runs(element: BandElement | None) -> str:
        """Une zone : ses caractères au style de l'élément (police, taille, couleur, gras…),
        le logo devant s'il le porte ; sa place est celle de la zone."""
        if element is None:
            return ""
        props = style.between(element.style, name) if style is not None and element.style else {}
        lead = _with_logo(logo, band, element.text) if element.logo else ""
        return lead + _text(element.text, title, run_xml(props))

    zones = {zone: runs(element) for zone, element in (band.zones if band else {}).items()}
    zones = {zone: zones.get(zone, "") for zone in ("left", "center", "right")}
    paragraphs: list[str] = []
    if band is not None and (band.left is not None or band.right is not None):
        tabs = (
            f'<w:tabs><w:tab w:val="center" w:pos="{width // 2}"/>'
            f'<w:tab w:val="right" w:pos="{width}"/></w:tabs><w:jc w:val="left"/>'
        )
        paragraphs.append(
            f'<w:p><w:pPr><w:pStyle w:val="{style_id}"/>{tabs}</w:pPr>{zones["left"]}'
            f"<w:r><w:tab/></w:r>{zones['center']}<w:r><w:tab/></w:r>{zones['right']}</w:p>"
        )
    elif zones["center"]:
        # Le centre, seul : toujours centré.
        paragraphs.append(
            f'<w:p><w:pPr><w:pStyle w:val="{style_id}"/>{centered}</w:pPr>{zones["center"]}</w:p>'
        )
    lines: list[str] = []
    for element in band.lines if band else ():
        # Une ligne : un paragraphe au style de l'élément ; dans un en-tête, centrée si son
        # style ne dit pas d'alignement.
        own_id = (word(element.style) if word else element.style) if element.style else style_id
        own = style.element_look(element, name) if style is not None else None
        line_jc = centered if kind == "hdr" and (own is None or own.align is None) else ""
        lines.append(
            f'<w:p><w:pPr><w:pStyle w:val="{own_id}"/>{line_jc}</w:pPr>'
            f"{(_with_logo(logo, band, element.text) if element.logo else '') + _text(element.text, title)}</w:p>"
        )
    paragraphs = paragraphs + lines if kind == "hdr" else lines + paragraphs
    if not paragraphs:
        paragraphs = [f'<w:p><w:pPr><w:pStyle w:val="{style_id}"/></w:pPr></w:p>']
    if watermark:
        first = paragraphs[0]
        cut = first.index("</w:pPr>") + len("</w:pPr>")
        paragraphs[0] = first[:cut] + _watermark(watermark) + first[cut:]
    space = f" {_VML}" if watermark else ""
    return _HEAD + f'<w:{kind} xmlns:w="{_W}"{space}>' + "".join(paragraphs) + f"</w:{kind}>"


_NIL_BORDERS: Final = (
    '<w:pBdr><w:top w:val="nil"/><w:left w:val="nil"/><w:bottom w:val="nil"/>'
    '<w:right w:val="nil"/></w:pBdr>'
)


def _table_side(side: str, value: str | None) -> str:
    rule = parse_rule(value)
    if rule is None:
        return f'<w:{side} w:val="nil"/>'
    width, color = rule
    return f'<w:{side} w:val="single" w:sz="{round(width * 8)}" w:space="0" w:color="{color}"/>'


def _has_rule(look: Look) -> bool:
    return any(
        parse_rule(value) is not None
        for value in (
            look.border, look.border_top, look.border_bottom, look.border_left, look.border_right
        )
    )


def _cell_paragraph(
    style_id: str, look: Look, align: str | None, content: str, spacing: tuple[float, float]
) -> str:
    """Un paragraphe d'une cellule : le style de l'en-tête, du pied ou de la cellule, sans
    les filets, le fond ni le retrait du style, que porte la table ; son espace avant et
    après, ``spacing`` (``Style.cell_spacing``), s'il n'est pas celui de son style."""
    pieces: Pieces = [("pStyle", f'<w:pStyle w:val="{style_id}"/>')]
    if _has_rule(look):
        pieces.append(("pBdr", _NIL_BORDERS))
    if look.fill is not None and look.fill != NONE:
        pieces.append(("shd", '<w:shd w:val="clear" w:color="auto" w:fill="auto"/>'))
    if spacing != (look.space_before or 0.0, look.space_after or 0.0):
        before, after = spacing
        pieces.append(("spacing", f'<w:spacing w:before="{twips(before)}" w:after="{twips(after)}"/>'))
    if look.indent:
        pieces.append(("ind", '<w:ind w:left="0" w:right="0"/>'))
    if align is not None:
        pieces.append(("jc", f'<w:jc w:val="{jc(align)}"/>'))
    return f"<w:p><w:pPr>{ordered(pieces, _PPR)}</w:pPr>{content}</w:p>"


def _band_table(
    kind: str,
    band: Band,
    style: Style,
    *,
    title: str,
    width: int,
    logo: str,
    word: Callable[[str], str] | None,
) -> str:
    """La table d'un en-tête ou d'un pied : ses lignes, et les ``lines`` en lignes de toute
    la largeur (``band_rows``). Le cadre et le fond du style vont sur la table, l'écart
    au texte en marges des cellules du bord ; l'écart entre colonnes (``gap``) en deux
    moitiés, de part et d'autre du filet intérieur."""
    name = "header" if kind == "hdr" else "footer"
    table = band.table
    assert table is not None
    # Le style de la table : son cadre, ses filets, l'écart entre ses colonnes ; et celui des
    # cellules qui n'en nomment pas.
    look = style.look(table.style or name)
    if table.style is None:
        base_id = HEADER if kind == "hdr" else "Footer"
    else:
        base_id = word(table.style) if word is not None else table.style
    indent = round((look.indent or 0) * 567)
    total = width - 2 * indent
    columns = band_columns(table, total)
    count = len(columns)
    (top, bottom, left, right), fill = band_frame(look)
    # L'écart au cadre, celui des filets d'un paragraphe (``border_space``), en marges des
    # cellules du bord ; et autour d'un filet intérieur.
    space = look.border_space or (0.0, 0.0, 0.0, 0.0)
    above, after_side, below, before_side = (twips(value) for value in space)
    pads = (
        above if top or fill else 0,
        before_side if left or fill else 0,
        below if bottom or fill else 0,
        after_side if right or fill else 0,
    )
    gap = round((look.column_gap or 0) * 567)
    half = gap // 2
    inner_h = look.borders in ("horizontal", "grid")
    inner_v = look.borders in ("vertical", "grid")
    line = look.line or style.look("table").line
    rows = band_rows(band, header=kind == "hdr")
    # Un filet horizontal intérieur sous une ligne de la table, et l'écart au texte de part
    # et d'autre, comme celui du cadre.
    ruled = [
        inner_h and not row.line and number + 1 < len(rows) and not rows[number + 1].line
        for number, row in enumerate(rows)
    ]
    said = (
        f'<w:tbl><w:tblPr><w:tblW w:w="{total}" w:type="dxa"/>'
        + (f'<w:tblInd w:w="{indent}" w:type="dxa"/>' if indent else "")
        + "<w:tblBorders>"
        + _table_side("top", top)
        + _table_side("left", left)
        + _table_side("bottom", bottom)
        + _table_side("right", right)
        + '<w:insideH w:val="nil"/><w:insideV w:val="nil"/></w:tblBorders>'
        '<w:tblLayout w:type="fixed"/>'
        '<w:tblCellMar><w:top w:w="0" w:type="dxa"/><w:left w:w="0" w:type="dxa"/>'
        '<w:bottom w:w="0" w:type="dxa"/><w:right w:w="0" w:type="dxa"/></w:tblCellMar>'
        '<w:tblLook w:val="0000" w:firstRow="0" w:lastRow="0" w:firstColumn="0" '
        'w:lastColumn="0" w:noHBand="1" w:noVBand="1"/></w:tblPr><w:tblGrid>'
        + "".join(f'<w:gridCol w:w="{column}"/>' for column in columns)
        + "</w:tblGrid>"
    )
    for number, row in enumerate(rows):
        cells: list[tuple[BandElement, int, int]] = (
            [(row.cells[0], 0, count)]
            if row.line
            else [(cell, index, 1) for index, cell in enumerate(row.cells)]
        )
        ruled_below = ruled[number]
        ruled_above = number > 0 and ruled[number - 1]
        said += "<w:tr>"
        for cell, start, span in cells:
            end = start + span
            cell_width = sum(columns[start:end])
            margins = (
                pads[0] if number == 0 else above if ruled_above else 0,
                pads[1] if start == 0 else gap - half,
                pads[2] if number == len(rows) - 1 else below if ruled_below else 0,
                pads[3] if end == count else half,
            )
            rules = ""
            if ruled_below:
                rules += _table_side("bottom", line)
            if inner_v and not row.line and end < count:
                rules += _table_side("right", line)
            own = style.look(cell.style) if cell.style else look
            # Le fond d'un style de cellule, s'il n'est pas celui du cadre.
            own_fill = own.fill if cell.style and own.fill not in (None, NONE) else None
            shade = own_fill or fill
            properties = (
                f'<w:tcW w:w="{cell_width}" w:type="dxa"/>'
                + (f'<w:gridSpan w:val="{span}"/>' if span > 1 else "")
                + (f"<w:tcBorders>{rules}</w:tcBorders>" if rules else "")
                + (f'<w:shd w:val="clear" w:color="auto" w:fill="{shade}"/>' if shade else "")
                + "<w:tcMar>"
                + "".join(
                    f'<w:{side} w:w="{value}" w:type="dxa"/>'
                    for side, value in zip(("top", "left", "bottom", "right"), margins, strict=True)
                )
                + "</w:tcMar>"
                + f'<w:vAlign w:val="{own.valign or look.valign or "top"}"/>'
            )
            if cell.style is None:
                style_id = base_id
            elif word is not None:
                style_id = word(cell.style)
            else:
                style_id = cell.style
            # Une ligne de toute la largeur, dans un en-tête : centrée si son style ne dit pas
            # d'alignement.
            centered = "center" if row.line and kind == "hdr" and own.align is None else None
            spacing = style.cell_spacing(cell.style, table.style or name, name)
            paragraphs = "".join(
                _cell_paragraph(
                    style_id,
                    own,
                    centered,
                    (_with_logo(logo, band, text) if cell.logo and index == 0 else "") + _text(text, title),
                    spacing,
                )
                for index, text in enumerate(cell.lines)
            )
            said += f"<w:tc><w:tcPr>{properties}</w:tcPr>{paragraphs}</w:tc>"
        said += "</w:tr>"
    return said + "</w:tbl>"


def _band_space(kind: str, style: Style, height: int, *, before: int = 0, mark: str = "") -> str:
    """Un paragraphe vide d'un en-tête ou d'un pied, haut de ``height`` twips, sans filet ni
    fond : l'espace avant une table, ou le paragraphe qui la clôt (Word en veut un)."""
    look = style.look("header" if kind == "hdr" else "footer")
    pieces: Pieces = [
        ("pStyle", f'<w:pStyle w:val="{HEADER if kind == "hdr" else "Footer"}"/>'),
        ("spacing", f'<w:spacing w:before="{before}" w:after="0" w:line="{height}" w:lineRule="exact"/>'),
    ]
    if _has_rule(look):
        pieces.append(("pBdr", _NIL_BORDERS))
    if look.fill is not None and look.fill != NONE:
        pieces.append(("shd", '<w:shd w:val="clear" w:color="auto" w:fill="auto"/>'))
    return (
        f"<w:p><w:pPr>{ordered(pieces, _PPR)}"
        '<w:rPr><w:sz w:val="2"/><w:szCs w:val="2"/></w:rPr></w:pPr>'
        f"{mark}</w:p>"
    )


# La hauteur du paragraphe qui clôt un en-tête ou un pied après sa table : un point.
_BAND_END: Final = 20


def _band_end(kind: str, style: Style, watermark: Watermark | None, *, before: int = 0) -> str:
    """Le paragraphe qui clôt un en-tête ou un pied après sa table : haut d'un point, à
    ``before`` twips d'elle (l'espace après du style de la table) ; il porte le filigrane."""
    mark = _watermark(watermark) if watermark else ""
    return _band_space(kind, style, _BAND_END, before=before, mark=mark)


def _watermark(watermark: Watermark) -> str:
    """Le filigrane : un texte en diagonale, derrière le texte de chaque page (le style
    ``watermark`` : sa police et sa couleur, à demi transparente).

    La forme VML qu'écrit Word lui-même (« PowerPlusWaterMarkObject ») ; LibreOffice la
    lit et l'imprime.
    """
    return (
        '<w:r><w:pict><v:shapetype id="_x0000_t136" coordsize="21600,21600" o:spt="136" '
        'adj="10800" path="m@7,l@8,m@5,21600l@6,21600e"><v:formulas>'
        '<v:f eqn="sum #0 0 10800"/><v:f eqn="prod #0 2 1"/><v:f eqn="sum 21600 0 @1"/>'
        '<v:f eqn="sum 0 0 @2"/><v:f eqn="sum 21600 0 @3"/><v:f eqn="if @0 @3 0"/>'
        '<v:f eqn="if @0 21600 @1"/><v:f eqn="if @0 0 @2"/><v:f eqn="if @0 @4 21600"/>'
        '<v:f eqn="mid @5 @6"/><v:f eqn="mid @8 @5"/><v:f eqn="mid @7 @8"/>'
        '<v:f eqn="mid @6 @7"/><v:f eqn="sum @6 0 @5"/></v:formulas>'
        '<v:path textpathok="t" o:connecttype="custom" '
        'o:connectlocs="@9,0;@10,10800;@11,21600;@12,10800" o:connectangles="270,180,90,0"/>'
        '<v:textpath on="t" fitshape="t"/><o:lock v:ext="edit" text="t" shapetype="t"/>'
        "</v:shapetype>"
        '<v:shape id="PowerPlusWaterMarkObject1" o:spid="_x0000_s2049" type="#_x0000_t136" '
        'style="position:absolute;margin-left:0;margin-top:0;width:440pt;height:110pt;'
        "rotation:315;z-index:-251657216;mso-position-horizontal:center;"
        "mso-position-horizontal-relative:margin;mso-position-vertical:center;"
        'mso-position-vertical-relative:margin" o:allowincell="f" '
        f'fillcolor="#{watermark.color}" stroked="f"><v:fill opacity=".5"/>'
        f'<v:textpath style="font-family:&quot;{watermark.font}&quot;;font-size:1pt" '
        f'string="{escape(watermark.text, {chr(34): "&quot;"})}"/></v:shape></w:pict></w:r>'
    )
