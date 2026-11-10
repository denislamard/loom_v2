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
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Final
from xml.sax.saxutils import escape, quoteattr

from loom_ia.tools_builtin._style_params import (
    NONE,
    Band,
    Language,
    Numbering,
    Style,
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
# L'écart entre un filet et le texte, en points : au-dessus et au-dessous, à gauche.
_RULE_SPACE: Final = {"top": 4, "bottom": 4, "left": 8, "right": 8}
# Ce que ``text`` met dans les valeurs par défaut du document, plutôt que dans Normal.
_DEFAULT_RUN: Final = frozenset({"font", "color", "size"})
_DEFAULT_PARAGRAPH: Final = frozenset({"space_before", "space_after", "line_spacing"})

type Pieces = list[tuple[str, str]]
# Ce qu'un style de tableau dit du tableau et de ses cellules, et pas de leur texte.
_CELL_PROPS: Final = frozenset({"border", "border_top", "border_bottom", "border_left", "fill"})


@dataclass(frozen=True, slots=True)
class Watermark:
    """Le filigrane : son texte, sa police, sa couleur."""

    text: str
    font: str
    color: str


def watermark(style: Style) -> Watermark | None:
    """Le filigrane du style ``watermark`` ; ``None`` sans texte."""
    look = style.look("watermark")
    if not look.text:
        return None
    return Watermark(look.text, look.font or "", look.color or "")


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


def _side(side: str, value: str) -> str:
    rule = parse_rule(value)
    if rule is None:
        return f'<w:{side} w:val="nil"/>'
    width, color = rule
    return (
        f'<w:{side} w:val="single" w:sz="{round(width * 8)}" w:space="{_RULE_SPACE[side]}" '
        f'w:color="{color}"/>'
    )


def paragraph_pieces(props: Mapping[str, Any]) -> Pieces:
    """Les propriétés de paragraphe d'un style : filets, fond, espacements, retrait,
    alignement."""
    pieces: Pieces = []
    every = props.get("border")
    sides = [
        _side(side, value)
        for side, prop in (
            ("top", "border_top"),
            ("left", "border_left"),
            ("bottom", "border_bottom"),
            ("right", None),
        )
        if (value := (props.get(prop) if prop else None) or every) is not None
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
    stop: str = "text",
    next_style: str | None = None,
    mechanics: Pieces | None = None,
) -> str:
    """Un style Word tiré d'un style nommé : ce qu'il écrit en plus de ``stop``, le style
    loom de son style Word de base, et la mécanique (``mechanics``). Les filets et le fond
    d'un tableau sont ceux du tableau et de ses cellules, pas du texte d'une cellule."""
    props = style.between(loom, stop)
    if "table" in style.chain(loom):
        props = {prop: value for prop, value in props.items() if prop not in _CELL_PROPS}
    return _style(
        style_id,
        word_name,
        based=based,
        next_style=next_style,
        paragraph=ordered([*(mechanics or []), *paragraph_pieces(props)], _PPR),
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
    return BODY if style.between("paragraph", "text") else "Normal"


# Le style Word de chaque style prévu qui en a un (un paragraphe) : la base Word d'un style
# à toi qui en part.
WORD_IDS: Final[Mapping[str, str]] = {
    "text": "Normal",
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
        base = style.bases.get(name) or "text"
        said += _named(
            style, name, name, name, based=word_id(style, base), stop=base, next_style=None
        )
    return said


def styles_xml(style: Style, used: Iterable[str] = (), custom: Iterable[str] = ()) -> str:
    """``styles.xml`` : un style Word par style nommé que l'outil emploie, et ceux que le
    corps demande ; ``custom`` : les styles à toi qu'il emploie."""
    wanted = set(used)
    text = style.between("text", None)
    language = LANGUAGES[style.language]
    defaults_run = ordered(
        [
            *run_pieces({p: v for p, v in text.items() if p in _DEFAULT_RUN}),
            ("lang", f'<w:lang w:val="{language}" w:eastAsia="{language}" w:bidi="ar-SA"/>'),
        ],
        _RPR,
    )
    defaults_paragraph = ordered(
        paragraph_pieces({p: v for p, v in text.items() if p in _DEFAULT_PARAGRAPH}), _PPR
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
            paragraph=ordered(paragraph_pieces(normal), _PPR),
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
    des notes, des groupes de tableau et du récapitulatif de TVA, que le document emploie."""
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
    if HYPERLINK in wanted:
        said += _character(HYPERLINK, "Hyperlink", run_xml(style.between("link", "text")))
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
    for level, toc in enumerate(TOC_LEVELS):
        if toc in wanted:
            said += _style(
                toc,
                f"toc {level + 1}",
                next_style="Normal",
                paragraph=f'<w:spacing w:before="{60 if level == 0 else 0}" w:after="40"/>'
                + (f'<w:ind w:left="{284 * level}"/>' if level else ""),
                run="<w:b/><w:bCs/>" if level == 0 else "",
            )
    return said


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


def _abstract(number: int, ordered: bool, depth: int = 2) -> str:
    said = f'<w:abstractNum w:abstractNumId="{number}"><w:multiLevelType w:val="hybridMultilevel"/>'
    for level, (kind, text) in enumerate(_LEVELS[ordered][:depth]):
        left = 720 * (level + 1)
        said += (
            f'<w:lvl w:ilvl="{level}"><w:start w:val="1"/><w:numFmt w:val="{kind}"/>'
            f'<w:lvlText w:val="{text}"/><w:lvlJc w:val="left"/>'
            f'<w:pPr><w:ind w:left="{left}" w:hanging="360"/></w:pPr></w:lvl>'
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
    lists: Iterable[tuple[bool, bool]], headings: Numbering = "none", article: str = "Article"
) -> str:
    """Une numérotation par liste (numérotée ?, à trois niveaux ?) : chaque liste numérotée
    repart de 1. Les définitions (``abstractNum``) précèdent toutes les numérotations
    (``num``) : Word l'exige."""
    lists = list(lists)
    said = _HEAD + f'<w:numbering xmlns:w="{_W}">' + _abstract(0, False) + _abstract(1, True)
    if headings != "none":
        said += _heading_abstract(headings, article)
    for ordered in (False, True):
        if (ordered, True) in lists:
            said += _abstract(_DEEP + int(ordered), ordered, depth=3)
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


def _text(text: str, title: str) -> str:
    """Un texte d'en-tête : ``{page}`` et ``{pages}`` en champs, ``{title}`` le titre du
    document, le reste en segments."""
    said = ""
    position = 0
    for found in _PLACEHOLDER.finditer(text):
        said += _plain(text[position : found.start()])
        name = found.group(1)
        if name == "page":
            said += '<w:fldSimple w:instr=" PAGE "><w:r><w:t>1</w:t></w:r></w:fldSimple>'
        elif name == "pages":
            said += '<w:fldSimple w:instr=" NUMPAGES "><w:r><w:t>1</w:t></w:r></w:fldSimple>'
        else:
            said += _plain(title)
        position = found.end()
    return said + _plain(text[position:])


def _plain(text: str) -> str:
    return f'<w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r>' if text else ""


def band_xml(
    kind: str,
    band: Band | None,
    *,
    title: str,
    width: int,
    logo: str = "",
    watermark: Watermark | None = None,
) -> str:
    """Un en-tête (``hdr``) ou un pied de page (``ftr``).

    ``logo`` : le segment qui porte le logo, déjà écrit (sa relation est propre à la
    partie). Trois zones sur une ligne — gauche, centre, droite, par des taquets —, et
    des lignes centrées : sous la ligne des zones dans un en-tête, au-dessus dans un
    pied de page (les mentions, puis le numéro de page).
    """
    style = HEADER if kind == "hdr" else "Footer"
    centered = '<w:jc w:val="center"/>' if kind == "hdr" else ""
    texts = {
        "left": band.left if band else "",
        "center": band.center if band else "",
        "right": band.right if band else "",
    }
    position = band.logo.position if band and band.logo else None
    zones = {
        name: (logo if position == place else "") + _text(texts[name], title)
        for name, place in (("left", "left"), ("center", "center"), ("right", "right"))
    }
    paragraphs: list[str] = []
    if zones["left"] or zones["right"]:
        tabs = (
            f'<w:tabs><w:tab w:val="center" w:pos="{width // 2}"/>'
            f'<w:tab w:val="right" w:pos="{width}"/></w:tabs><w:jc w:val="left"/>'
        )
        paragraphs.append(
            f'<w:p><w:pPr><w:pStyle w:val="{style}"/>{tabs}</w:pPr>{zones["left"]}'
            f"<w:r><w:tab/></w:r>{zones['center']}<w:r><w:tab/></w:r>{zones['right']}</w:p>"
        )
    elif zones["center"]:
        paragraphs.append(
            f'<w:p><w:pPr><w:pStyle w:val="{style}"/>{centered}</w:pPr>{zones["center"]}</w:p>'
        )
    lines = [
        f'<w:p><w:pPr><w:pStyle w:val="{style}"/>{centered}</w:pPr>{_text(line, title)}</w:p>'
        for line in (band.lines if band else ())
    ]
    paragraphs = paragraphs + lines if kind == "hdr" else lines + paragraphs
    if not paragraphs:
        paragraphs = [f'<w:p><w:pPr><w:pStyle w:val="{style}"/></w:pPr></w:p>']
    if watermark:
        first = paragraphs[0]
        cut = first.index("</w:pPr>") + len("</w:pPr>")
        paragraphs[0] = first[:cut] + _watermark(watermark) + first[cut:]
    space = f" {_VML}" if watermark else ""
    return _HEAD + f'<w:{kind} xmlns:w="{_W}"{space}>' + "".join(paragraphs) + f"</w:{kind}>"


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
