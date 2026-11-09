# SPDX-License-Identifier: Apache-2.0
"""Écrire un .docx : le document validé posé dans son style, en WordprocessingML.

Sans bibliothèque : un .docx est un zip de quelques parties XML, que ce module
écrit lui-même. Le style vient de la config (``_style_params``) : la charte de
l'outil, complétée par la disposition du modèle suivi ; sans style, le style
maison — A4, marges de 2 cm, Calibri 11, titres bleu nuit, tableaux à en-tête
bleu pâle et ligne de total en gras, numéros de page en pied —, le même octet
pour octet qu'avant les styles. Le document est en français (correcteur,
césure). Une image est une partie ``word/media`` rangée telle quelle, une seule
fois par image distincte (``word/image.py``).

La disposition s'applique **section par section** : une section est un titre de
niveau 1 et ce qui le suit, jusqu'au suivant. Elle peut masquer le titre, poser
des sections côte à côte (un tableau sans bordure, style « loom layout », que la
relecture traverse), encadrer, réduire le texte, passer en colonnes, en paysage,
ou faire de la première section une page de garde. Paysage et colonnes sont des
sections Word : un saut de section les ouvre et les ferme.

Deux documents identiques donnent le même fichier, octet pour octet : les
dates des propriétés et de l'archive sont fixées, et rien ne dépend de
l'heure ni de la machine. L'ordre des éléments suit le schéma
WordprocessingML, que Word, contrairement à LibreOffice, applique strictement.
"""

import dataclasses
import io
import zipfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Final
from xml.sax.saxutils import escape, quoteattr

from loom_ia.tools_builtin._document.content import (
    BlockOut,
    BreakOut,
    ChartOut,
    HeadingOut,
    ImageOut,
    Inline,
    ListOut,
    NoteOut,
    ParagraphOut,
    Prepared,
    QuoteOut,
    TableOut,
    TitleOut,
    plain,
)
from loom_ia.tools_builtin._document.picture import Picture
from loom_ia.tools_builtin._document.picture import parse as parse_picture
from loom_ia.tools_builtin._style_params import (
    HOUSE,
    PLAIN_SECTION,
    Band,
    Beside,
    SectionLook,
    Style,
    TableLook,
)
from loom_ia.tools_builtin._templates import key
from loom_ia.tools_builtin.word import chart, image
from loom_ia.tools_builtin.word import style as look

ZIP_DATE: Final = (2000, 1, 1, 0, 0, 0)
FIXED_DATE: Final = "2000-01-01T00:00:00Z"

_W: Final = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_R: Final = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_REL: Final = "http://schemas.openxmlformats.org/package/2006/relationships"
_TYPE: Final = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_CT: Final = "application/vnd.openxmlformats-officedocument.wordprocessingml"
_HEAD: Final = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'

# Un centimètre en vingtièmes de point (twips) ; un twip en EMU ; l'écart entre deux
# colonnes de texte, entre deux cellules côte à côte, et la marge d'un encadré.
CM: Final = 567
EMU_PER_TWIP: Final = 635
COLUMN_GAP: Final = CM
CELL_GAP: Final = 284
BOX_PAD: Final = 170
# L'espace en haut d'une page de garde, sans ``space_before`` : LibreOffice ignore le
# centrage vertical d'une page (``vAlign``), un espace fixe marche partout.
COVER_SPACE: Final = 6 * CM

CONTENT_TYPES: Final = (
    _HEAD + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" '
    'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    f'<Override PartName="/word/document.xml" ContentType="{_CT}.document.main+xml"/>'
    f'<Override PartName="/word/styles.xml" ContentType="{_CT}.styles+xml"/>'
    f'<Override PartName="/word/numbering.xml" ContentType="{_CT}.numbering+xml"/>'
    f'<Override PartName="/word/settings.xml" ContentType="{_CT}.settings+xml"/>'
    f'<Override PartName="/word/footer1.xml" ContentType="{_CT}.footer+xml"/>'
    '<Override PartName="/docProps/core.xml" '
    'ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
    '<Override PartName="/docProps/app.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
    "</Types>"
)
_CHART_TYPE: Final = "application/vnd.openxmlformats-officedocument.drawingml.chart+xml"
PACKAGE_RELS: Final = (
    _HEAD + f'<Relationships xmlns="{_REL}">'
    f'<Relationship Id="rId1" Type="{_TYPE}/officeDocument" Target="word/document.xml"/>'
    '<Relationship Id="rId2" '
    'Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/'
    'core-properties" Target="docProps/core.xml"/>'
    f'<Relationship Id="rId3" Type="{_TYPE}/extended-properties" Target="docProps/app.xml"/>'
    "</Relationships>"
)
DOCUMENT_RELS: Final = (
    _HEAD + f'<Relationships xmlns="{_REL}">'
    f'<Relationship Id="rId1" Type="{_TYPE}/styles" Target="styles.xml"/>'
    f'<Relationship Id="rId2" Type="{_TYPE}/numbering" Target="numbering.xml"/>'
    f'<Relationship Id="rId3" Type="{_TYPE}/settings" Target="settings.xml"/>'
    f'<Relationship Id="rId4" Type="{_TYPE}/footer" Target="footer1.xml"/>'
    "</Relationships>"
)
# Les parties d'en-tête : l'en-tête des pages, et l'en-tête et le pied vides d'une
# première page sans en-tête (page de garde, ``first_page: false``).
HEADER_PART: Final = "header1.xml"
FIRST_HEADER_PART: Final = "header2.xml"
FIRST_FOOTER_PART: Final = "footer2.xml"


def _parts_listed(
    charts: int, media: list[Picture], body_media: int, bands: Sequence[tuple[str, str, str]]
) -> tuple[str, str]:
    """Types de contenu et relations du corps : ceux d'un document sans graphique, image
    ni en-tête, à l'octet près, plus une partie et une relation par graphique, par image
    du corps (``body_media`` premières), et par en-tête ou pied ajouté
    (``bands`` : identifiant, type, partie)."""
    if not charts and not media and not bands:
        return CONTENT_TYPES, DOCUMENT_RELS
    formats = sorted({picture.format for picture in media})
    defaults = "".join(f'<Default Extension="{fmt}" ContentType="image/{fmt}"/>' for fmt in formats)
    types = "".join(
        f'<Override PartName="/word/charts/chart{n}.xml" ContentType="{_CHART_TYPE}"/>'
        for n in range(1, charts + 1)
    ) + "".join(
        f'<Override PartName="/word/{part}" ContentType="{_CT}.{kind}+xml"/>'
        for _, kind, part in bands
    )
    relations = (
        "".join(
            f'<Relationship Id="rIdChart{n}" Type="{_TYPE}/chart" Target="charts/chart{n}.xml"/>'
            for n in range(1, charts + 1)
        )
        + "".join(
            f'<Relationship Id="rIdImage{n}" Type="{_TYPE}/image" '
            f'Target="media/image{n}.{picture.extension}"/>'
            for n, picture in enumerate(media[:body_media], start=1)
        )
        + "".join(
            f'<Relationship Id="{ident}" Type="{_TYPE}/{kind}" Target="{part}"/>'
            for ident, kind, part in bands
        )
    )
    xml_default = '<Default Extension="xml" ContentType="application/xml"/>'
    return (
        CONTENT_TYPES.replace(xml_default, xml_default + defaults).replace(
            "</Types>", types + "</Types>"
        ),
        DOCUMENT_RELS.replace("</Relationships>", relations + "</Relationships>"),
    )


APP: Final = (
    _HEAD + '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/'
    'extended-properties"><Application>loom-ia</Application></Properties>'
)
SETTINGS: Final = (
    _HEAD + f'<w:settings xmlns:w="{_W}">'
    '<w:defaultTabStop w:val="708"/><w:characterSpacingControl w:val="doNotCompress"/>'
    '<w:compat><w:compatSetting w:name="compatibilityMode" '
    'w:uri="http://schemas.microsoft.com/office/word" w:val="15"/></w:compat>'
    "</w:settings>"
)
# Le style maison, tel qu'écrit dans un document sans style.
STYLES: Final = look.styles_xml(HOUSE)


def _core(title: str | None, template: str | None = None) -> str:
    named = f"<dc:title>{escape(title)}</dc:title>" if title else ""
    # Le modèle suivi : la catégorie du document (Fichier > Propriétés dans Word) ;
    # absente sans modèle, pour qu'un document sans modèle ne change pas.
    if template:
        named += f"<cp:category>{escape(template)}</cp:category>"
    return (
        _HEAD + '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/'
        'metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        f"{named}<dc:creator>loom-ia</dc:creator><cp:lastModifiedBy>loom-ia"
        "</cp:lastModifiedBy><dc:language>fr-FR</dc:language>"
        f'<dcterms:created xsi:type="dcterms:W3CDTF">{FIXED_DATE}</dcterms:created>'
        f'<dcterms:modified xsi:type="dcterms:W3CDTF">{FIXED_DATE}</dcterms:modified>'
        "</cp:coreProperties>"
    )


# --- Segments et paragraphes ----------------------------------------------------------


def _size(size: int | None) -> str:
    return f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/>' if size else ""


def _runs(runs: Inline, *, bold: bool = False, size: int | None = None) -> str:
    said = ""
    for run in runs:
        props = (
            ("<w:b/><w:bCs/>" if run.bold or bold else "")
            + ("<w:i/><w:iCs/>" if run.italic else "")
            + _size(size)
        )
        said += _run(run.text, f"<w:rPr>{props}</w:rPr>" if props else "")
    return said


def _run(text: str, props: str = "") -> str:
    """Un segment : ``\\n`` devient un saut de ligne, ``\\t`` une tabulation."""
    parts: list[str] = []
    for number, line in enumerate(text.split("\n")):
        if number:
            parts.append("<w:br/>")
        for index, piece in enumerate(line.split("\t")):
            if index:
                parts.append("<w:tab/>")
            if piece:
                parts.append(f'<w:t xml:space="preserve">{escape(piece)}</w:t>')
    return f"<w:r>{props}{''.join(parts)}</w:r>" if parts else ""


def _paragraph(style: str, content: str, extra: str = "") -> str:
    return f'<w:p><w:pPr><w:pStyle w:val="{style}"/>{extra}</w:pPr>{content}</w:p>'


def _spacer(twips: int) -> str:
    """Un paragraphe vide de ``twips`` de haut : un espace avant, sans texte à relire."""
    return (
        f'<w:p><w:pPr><w:spacing w:before="0" w:after="0" w:line="{twips}" '
        'w:lineRule="exact"/></w:pPr></w:p>'
    )


_AFTER_TABLE: Final = '<w:spacing w:after="120"/>'
_JC: Final = {"left": "left", "center": "center", "right": "right"}


# --- Le corps ---------------------------------------------------------------------------


@dataclass(slots=True)
class _Body:
    """Ce que le corps demande à l'archive : numérotations, graphiques, images, styles."""

    style: Style
    title: str
    lists: list[bool] = field(default_factory=list[bool])
    charts: list[ChartOut] = field(default_factory=list[ChartOut])
    # Les images distinctes, dans l'ordre où elles paraissent la première fois.
    media: list[Picture] = field(default_factory=list[Picture])
    stored: dict[str, int] = field(default_factory=dict[str, int])
    # Les dessins (graphiques et images) : chacun son identifiant, unique dans le document.
    drawings: int = 0
    # Les styles que seule une disposition demande, et que le corps emploie.
    used: set[str] = field(default_factory=set[str])
    # Les légendes numérotées : le dernier numéro, par sorte (« Tableau », « Figure »).
    numbers: dict[str, int] = field(default_factory=dict[str, int])

    def picture(self, picture: Picture) -> int:
        """Le numéro de la partie qui porte l'image : chaque image distincte une fois."""
        if picture.digest not in self.stored:
            self.media.append(picture)
            self.stored[picture.digest] = len(self.media)
        return self.stored[picture.digest]


def _caption(body: _Body, kind: str, runs: Inline) -> str:
    """La légende ; numérotée (« Figure 2 — ») si la charte le veut, par un champ ``SEQ``
    que Word et LibreOffice recalculent, et que la relecture retire."""
    prefix = ""
    if body.style.captions_numbered:
        number = body.numbers[kind] = body.numbers.get(kind, 0) + 1
        prefix = (
            _run(f"{kind} ")
            + f'<w:fldSimple w:instr=" SEQ {kind} \\* ARABIC "><w:r><w:t>{number}</w:t></w:r>'
            "</w:fldSimple>" + _run(" — ")
        )
    return _paragraph("Caption", prefix + _runs(runs))


def _table(body: _Body, table: TableOut, width: int, tables: TableLook, size: int | None) -> str:
    style = body.style
    # « contenu » : un tableau étroit (la moitié de la page au plus, un récapitulatif) prend
    # sa largeur ; un plus large garde toute la largeur, plutôt qu'une largeur bâtarde.
    narrow = round(table.natural * HOUSE.text_width)
    pleine = tables.width == "pleine" or narrow > HOUSE.text_width // 2
    full = width if pleine else min(width, narrow)
    widths = [round(part * full) for part in table.widths]
    widths[-1] += full - sum(widths)
    thin = f'w:val="single" w:sz="4" w:space="0" w:color="{style.rule}"'
    strong = f'w:val="single" w:sz="8" w:space="0" w:color="{style.primary}"'
    sides = {
        "grille": (("top", thin), ("left", thin), ("bottom", thin), ("right", thin),
                   ("insideH", thin), ("insideV", thin)),
        "filets": (("top", strong), ("bottom", strong), ("insideH", thin)),
        "horizontales": (("top", thin), ("bottom", thin), ("insideH", thin)),
        "aucune": tuple((side, 'w:val="nil"') for side in
                        ("top", "left", "bottom", "right", "insideH", "insideV")),
    }[tables.borders]  # fmt: skip
    size = size or (look.half_points(tables.size) if tables.size != style.tables.size else None)
    said = (
        "<w:tbl><w:tblPr>"
        + (
            '<w:tblW w:w="5000" w:type="pct"/>'
            if pleine
            else f'<w:tblW w:w="{full}" w:type="dxa"/>'
        )
        + (f'<w:jc w:val="{look.jc(tables.align)}"/>' if tables.align != "gauche" else "")
        + "<w:tblBorders>"
        + "".join(f"<w:{side} {border}/>" for side, border in sides)
        + '</w:tblBorders><w:tblLayout w:type="fixed"/><w:tblCellMar>'
        '<w:top w:w="40" w:type="dxa"/><w:left w:w="85" w:type="dxa"/>'
        '<w:bottom w:w="40" w:type="dxa"/><w:right w:w="85" w:type="dxa"/>'
        '</w:tblCellMar><w:tblLook w:val="04A0" w:firstRow="1" w:lastRow="0" '
        'w:firstColumn="0" w:lastColumn="0" w:noHBand="1" w:noVBand="1"/></w:tblPr>'
        "<w:tblGrid>" + "".join(f'<w:gridCol w:w="{w}"/>' for w in widths) + "</w:tblGrid>"
    )

    def row(
        cells: Iterable[str], *, header: bool = False, total: bool = False, band: bool = False
    ) -> str:
        props = (
            "<w:trPr><w:cantSplit/><w:tblHeader/></w:trPr>"
            if header
            else ("<w:trPr><w:cantSplit/></w:trPr>")
        )
        said = f"<w:tr>{props}"
        for index, text in enumerate(cells):
            filled = tables.header_fill if header else tables.bands if band else None
            fill = f'<w:shd w:val="clear" w:color="auto" w:fill="{filled}"/>' if filled else ""
            top = (
                f'<w:tcBorders><w:top w:val="single" w:sz="12" w:space="0" '
                f'w:color="{style.primary}"/></w:tcBorders>'
                if total
                else ""
            )
            align = "center" if header else table.align[index]
            color = (
                f'<w:color w:val="{tables.header_color}"/>'
                if header and tables.header_color
                else ""
            )
            props = ("<w:b/><w:bCs/>" if header or total else "") + color + _size(size)
            content = _run(text, f"<w:rPr>{props}</w:rPr>" if props else "")
            said += (
                f'<w:tc><w:tcPr><w:tcW w:w="{widths[index]}" w:type="dxa"/>{top}{fill}'
                f'<w:vAlign w:val="center"/></w:tcPr>'
                + _paragraph("TableText", content, f'<w:jc w:val="{_JC[align]}"/>')
                + "</w:tc>"
            )
        return said + "</w:tr>"

    said += row(table.headers, header=True)
    said += "".join(row(cells, band=index % 2 == 1) for index, cells in enumerate(table.rows))
    if table.total is not None:
        said += row(table.total, total=True)
    said += "</w:tbl>"
    # Toujours un paragraphe après le tableau : deux tableaux voisins ne fusionnent pas.
    if table.caption:
        said += _caption(body, "Tableau", table.caption)
    else:
        said += _paragraph("Normal", "", _AFTER_TABLE)
    return said


def _image(body: _Body, block: ImageOut, width: int) -> str:
    """Une image, réduite à la place disponible (une colonne, une cellule), et sa légende."""
    room = width * 2540 // 1440
    if block.width > room:
        block = dataclasses.replace(
            block, width=room, height=max(1, block.height * room // block.width)
        )
    body.drawings += 1
    relation = f"rIdImage{body.picture(block.picture)}"
    said = image.inline(relation, body.drawings, block, keep_next=bool(block.caption))
    if block.caption:
        return said + _caption(body, "Figure", block.caption)
    return said + _paragraph("Normal", "", _AFTER_TABLE)


def _layout(
    body: _Body,
    cells: Sequence[str],
    widths: Sequence[int],
    *,
    boxed: Iterable[int] = (),
) -> str:
    """Un tableau de disposition : des contenus côte à côte (ou un seul, encadré).

    Sans bordure, au style « loom layout », que la relecture reconnaît : elle rend les
    blocs de chaque cellule, dans l'ordre, et jamais le tableau.
    """
    body.used.add(look.LAYOUT)
    framed = set(boxed)
    border = f'w:val="single" w:sz="6" w:space="0" w:color="{body.style.primary}"'
    said = (
        f'<w:tbl><w:tblPr><w:tblStyle w:val="{look.LAYOUT}"/>'
        f'<w:tblW w:w="{sum(widths)}" w:type="dxa"/><w:tblLayout w:type="fixed"/>'
        '<w:tblCellMar><w:top w:w="0" w:type="dxa"/><w:left w:w="0" w:type="dxa"/>'
        '<w:bottom w:w="0" w:type="dxa"/><w:right w:w="0" w:type="dxa"/></w:tblCellMar>'
        '<w:tblLook w:val="0000" w:firstRow="0" w:lastRow="0" w:firstColumn="0" '
        'w:lastColumn="0" w:noHBand="1" w:noVBand="1"/></w:tblPr><w:tblGrid>'
        + "".join(f'<w:gridCol w:w="{width}"/>' for width in widths)
        + "</w:tblGrid><w:tr>"
    )
    for index, (content, width) in enumerate(zip(cells, widths, strict=True)):
        frame = (
            "<w:tcBorders>"
            + "".join(f"<w:{side} {border}/>" for side in ("top", "left", "bottom", "right"))
            + "</w:tcBorders>"
            if index in framed
            else ""
        )
        said += (
            f'<w:tc><w:tcPr><w:tcW w:w="{width}" w:type="dxa"/>{frame}'
            f"{_cell_margins(index in framed, index < len(cells) - 1)}</w:tcPr>"
            f"{content or '<w:p/>'}</w:tc>"
        )
    return said + "</w:tr></w:tbl>" + _paragraph("Normal", "", _AFTER_TABLE)


def _cell_margins(framed: bool, gap: bool) -> str:
    if framed:
        return (
            f'<w:tcMar><w:top w:w="{BOX_PAD}" w:type="dxa"/><w:left w:w="{BOX_PAD}" '
            f'w:type="dxa"/><w:bottom w:w="{BOX_PAD // 2}" w:type="dxa"/>'
            f'<w:right w:w="{BOX_PAD}" w:type="dxa"/></w:tcMar>'
        )
    if gap:
        return f'<w:tcMar><w:right w:w="{CELL_GAP}" w:type="dxa"/></w:tcMar>'
    return ""


def _room(width: int, framed: bool, gap: bool) -> int:
    """La largeur utile d'une cellule de disposition."""
    return width - (2 * BOX_PAD if framed else CELL_GAP if gap else 0)


def _blocks(
    body: _Body, blocks: Sequence[BlockOut], width: int, section: SectionLook = PLAIN_SECTION
) -> str:
    """Les blocs d'une section, dans ``width`` twips, selon sa disposition."""
    style = body.style
    tables = section.tables or style.tables
    size = look.half_points(max(7.0, style.font_size - 2)) if section.size == "petit" else None
    align = f'<w:jc w:val="{look.jc(section.align)}"/>' if section.align else ""
    said = ""
    index = 0
    while index < len(blocks):
        block = blocks[index]
        index += 1
        if (
            style.images_grid
            and isinstance(block, ImageOut)
            and block.wanted == "moitie"
            and index < len(blocks)
            and isinstance(second := blocks[index], ImageOut)
            and second.wanted == "moitie"
        ):
            index += 1
            half = width // 2
            # La même place pour les deux : l'écart entre elles est pris sur la première cellule.
            room = _room(half, False, True)
            cells = [_image(body, block, room), _image(body, second, room)]
            said += _layout(body, cells, [half, width - half])
            continue
        match block:
            case TitleOut(runs=runs):
                said += _paragraph(
                    "Title", _runs(runs), '<w:jc w:val="center"/>' if section.cover else ""
                )
            case HeadingOut(level=level, runs=runs):
                said += _paragraph(_heading_style(body, level, section), _runs(runs))
            case ParagraphOut(runs=runs):
                said += _paragraph("Normal", _runs(runs, size=size), align)
            case ListOut(ordered=ordered, entries=entries):
                body.lists.append(ordered)
                number = len(body.lists)
                for entry in entries:
                    said += _list_item(number, 0, entry.runs, size, align)
                    said += "".join(
                        _list_item(number, 1, child, size, align) for child in entry.children
                    )
            case TableOut():
                said += _table(body, block, width, tables, size)
            case NoteOut(title=title, runs=runs):
                head = (
                    _run(title, f"<w:rPr><w:b/><w:bCs/>{_size(size)}</w:rPr>")
                    + "<w:r><w:br/></w:r>"
                    if title
                    else ""
                )
                said += _paragraph("Note", head + _runs(runs, size=size))
            case QuoteOut(runs=runs, source=source):
                said += _paragraph("Quote", _runs(runs, size=size))
                if source:
                    sized = f"<w:rPr>{_size(size)}</w:rPr>" if size else ""
                    said += _paragraph("QuoteSource", _run(f"— {source}", sized))
            case BreakOut():
                said += '<w:p><w:r><w:br w:type="page"/></w:r></w:p>'
            case ChartOut():
                body.charts.append(block)
                number = len(body.charts)
                body.drawings += 1
                room = width * EMU_PER_TWIP
                said += chart.anchor(
                    f"rIdChart{number}",
                    number,
                    block,
                    body.drawings,
                    room if room < chart.WIDTH else None,
                )
                if block.caption:
                    said += _caption(body, "Figure", block.caption)
                else:
                    said += _paragraph("Normal", "", _AFTER_TABLE)
            case ImageOut():
                said += _image(body, block, width)
    return said


def _heading_style(body: _Body, level: int, section: SectionLook) -> str:
    """Le style d'un titre : celui de son niveau, sauf le titre d'une section masqué ou discret."""
    if level != 1 or section.title == "visible":
        return f"Heading{level}"
    chosen = look.HIDDEN if section.title == "masque" else look.QUIET
    body.used.add(chosen)
    return chosen


def _list_item(number: int, level: int, runs: Inline, size: int | None, align: str) -> str:
    numbering = f'<w:numPr><w:ilvl w:val="{level}"/><w:numId w:val="{number}"/></w:numPr>'
    return _paragraph("ListParagraph", _runs(runs, size=size), numbering + align)


# --- Sections du modèle, sections Word ------------------------------------------------------


@dataclass(slots=True)
class _Group:
    """Une section du document : son titre de niveau 1 (sa clé) et ses blocs."""

    key: str | None
    look: SectionLook
    blocks: list[BlockOut]


@dataclass(slots=True)
class _Unit:
    """Ce qui se pose d'un tenant : une section, ou des sections côte à côte."""

    xml: str
    landscape: bool
    columns: int
    cover: bool = False
    page_break: bool = False


def _groups(blocks: Iterable[BlockOut], style: Style) -> list[_Group]:
    groups: list[_Group] = []
    for block in blocks:
        if isinstance(block, HeadingOut) and block.level == 1:
            name = key(plain(block.runs))
            groups.append(_Group(name, style.sections.get(name, PLAIN_SECTION), [block]))
        elif groups:
            groups[-1].blocks.append(block)
        else:
            groups.append(_Group(None, PLAIN_SECTION, [block]))
    # Une page de garde porte aussi ce qui précède sa section : le titre du document.
    if len(groups) > 1 and groups[0].key is None and groups[1].look.cover:
        groups[1].blocks[:0] = groups[0].blocks
        del groups[0]
    return groups


def _width(style: Style, landscape: bool, columns: int = 1) -> int:
    page_width = style.page[1] if landscape else style.page[0]
    width = page_width - style.margins[2] - style.margins[3]
    return (width - (columns - 1) * COLUMN_GAP) // columns


def _units(body: _Body, groups: Sequence[_Group]) -> list[_Unit]:
    style = body.style
    beside_of: dict[str, Beside] = {k: group for group in style.beside for k in group.keys}
    units: list[_Unit] = []
    index = 0
    while index < len(groups):
        group = groups[index]
        together = [group]
        if group.key in beside_of:
            row = beside_of[group.key]
            while (
                index + len(together) < len(groups)
                and (following := groups[index + len(together)]).key in row.keys
            ):
                together.append(following)
        index += len(together)
        landscape = style.landscape or group.look.landscape
        if len(together) > 1:
            units.append(_beside(body, together, beside_of[_named(group.key)], landscape))
            continue
        units.append(_alone(body, group, landscape))
    return units


def _named(name: str | None) -> str:
    """La clé d'une section qui a un titre (toute section d'une ligne côte à côte)."""
    return name or ""


def _alone(body: _Body, group: _Group, landscape: bool) -> _Unit:
    section = group.look
    columns = section.columns
    width = _width(body.style, landscape, columns)
    space = COVER_SPACE if section.cover and not section.space_before else section.space_before
    said = _spacer(space) if space else ""
    if section.cover:
        section = dataclasses.replace(section, align=section.align or "centre")
    if section.box:
        content = _blocks(body, group.blocks, _room(width, True, False), section)
        said += _layout(body, [content], [width], boxed=(0,))
    else:
        said += _blocks(body, group.blocks, width, section)
    return _Unit(said, landscape, columns, section.cover, section.page_break)


def _beside(body: _Body, together: Sequence[_Group], row: Beside, landscape: bool) -> _Unit:
    """Des sections côte à côte : une cellule chacune, aux parts de la disposition."""
    width = _width(body.style, landscape)
    shares = [row.widths[row.keys.index(_named(group.key))] for group in together]
    widths = [round(width * share / sum(shares)) for share in shares]
    widths[-1] += width - sum(widths)
    cells: list[str] = []
    boxed: list[int] = []
    for position, (group, cell) in enumerate(zip(together, widths, strict=True)):
        last = position == len(together) - 1
        if group.look.box:
            boxed.append(position)
        room = _room(cell, group.look.box, not last)
        cells.append(_blocks(body, group.blocks, room, group.look))
    first = together[0].look
    said = _spacer(first.space_before) if first.space_before else ""
    said += _layout(body, cells, widths, boxed=boxed)
    return _Unit(said, landscape, 1, page_break=first.page_break)


@dataclass(slots=True)
class _Section:
    """Une section Word : son orientation, ses colonnes, son début, son contenu."""

    landscape: bool
    columns: int
    start: str
    first_page_blank: bool = False
    xml: list[str] = field(default_factory=list[str])


def _sections(units: Sequence[_Unit], style: Style) -> list[_Section]:
    """Les sections Word : une nouvelle à chaque changement d'orientation ou de colonnes,
    et après une page de garde. Des colonnes ne s'équilibrent que suivies d'une section
    continue : une section en colonnes que suit une nouvelle page est donc fermée par une
    section continue vide, d'une colonne."""
    sections: list[_Section] = []
    previous: _Unit | None = None
    for unit in units:
        current = sections[-1] if sections else None
        if current is None or previous is None:
            sections.append(_Section(unit.landscape, unit.columns, "nextPage", unit.cover))
        elif (unit.landscape, unit.columns) != (
            current.landscape,
            current.columns,
        ) or previous.cover:
            continuous = (
                unit.landscape == current.landscape and not previous.cover and not unit.page_break
            )
            if not continuous and current.columns > 1:
                sections.append(_Section(current.landscape, 1, "continuous"))
            sections.append(
                _Section(unit.landscape, unit.columns, "continuous" if continuous else "nextPage")
            )
        elif unit.page_break:
            current.xml.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')
        sections[-1].xml.append(unit.xml)
        previous = unit
    if not sections:
        sections.append(_Section(style.landscape, 1, "nextPage"))
    if sections[-1].columns > 1:
        sections.append(_Section(sections[-1].landscape, 1, "continuous"))
    return sections


@dataclass(frozen=True, slots=True)
class _Bands:
    """Les en-têtes et pieds de page du document : leurs relations dans le corps."""

    header: bool
    title_page: bool


def _section_properties(style: Style, section: _Section, bands: _Bands, first: bool) -> str:
    refs = ""
    if bands.header:
        refs += '<w:headerReference w:type="default" r:id="rIdHeader1"/>'
    refs += '<w:footerReference w:type="default" r:id="rId4"/>'
    title_page = first and bands.title_page
    if title_page:
        header = style.header
        footer = style.footer
        keep_header = (
            bands.header and not section.first_page_blank and (header is None or header.first_page)
        )
        keep_footer = not section.first_page_blank and (footer is None or footer.first_page)
        refs += (
            '<w:headerReference w:type="first" r:id="'
            + ("rIdHeader1" if keep_header else "rIdHeader2")
            + '"/><w:footerReference w:type="first" r:id="'
            + ("rId4" if keep_footer else "rIdFooter2")
            + '"/>'
        )
    width, height = style.page
    size = (
        f'<w:pgSz w:w="{height}" w:h="{width}" w:orient="landscape"/>'
        if section.landscape
        else f'<w:pgSz w:w="{width}" w:h="{height}"/>'
    )
    top, bottom, left, right = style.margins
    return (
        f"<w:sectPr>{refs}"
        + ('<w:type w:val="continuous"/>' if section.start == "continuous" else "")
        + size
        + f'<w:pgMar w:top="{top}" w:right="{right}" w:bottom="{bottom}" '
        f'w:left="{left}" w:header="567" w:footer="567" w:gutter="0"/>'
        + (
            f'<w:cols w:num="{section.columns}" w:space="{COLUMN_GAP}"/>'
            if section.columns > 1
            else ""
        )
        + ("<w:titlePg/>" if title_page else "")
        + "</w:sectPr>"
    )


def _blank_first_page(style: Style, sections: Sequence[_Section]) -> bool:
    """La première page sans en-tête ni pied : page de garde, ou ``first_page: false``."""
    return bool(sections and sections[0].first_page_blank) or any(
        band is not None and not band.first_page for band in (style.header, style.footer)
    )


def _document(prepared: Prepared, body: _Body) -> tuple[str, _Bands]:
    style = body.style
    units = _units(body, _groups(prepared.blocks, style))
    sections = _sections(units, style)
    bands = _Bands(
        header=style.header is not None or style.watermark is not None,
        title_page=_blank_first_page(style, sections),
    )
    said = ""
    for index, section in enumerate(sections):
        properties = _section_properties(style, section, bands, index == 0)
        said += "".join(section.xml)
        if index < len(sections) - 1:
            said += f"<w:p><w:pPr>{properties}</w:pPr></w:p>"
        else:
            said += properties
    return (
        _HEAD + f"<w:document xmlns:w={quoteattr(_W)} xmlns:r={quoteattr(_R)}>"
        f"<w:body>{said}</w:body></w:document>",
        bands,
    )


def _band_parts(
    body: _Body, bands: _Bands
) -> tuple[list[tuple[str, str | bytes]], list[tuple[str, str, str]]]:
    """Les parties d'en-tête et de pied de page, leurs relations propres (un logo), et
    leurs relations dans le corps (identifiant, type, partie)."""
    style = body.style
    width = _width(style, style.landscape)
    parts: list[tuple[str, str | bytes]] = []
    listed: list[tuple[str, str, str]] = []

    def logo(band: Band | None) -> tuple[str, str]:
        if band is None or band.logo is None:
            return "", ""
        picture = parse_picture(band.logo.data)
        number = body.picture(picture)
        body.drawings += 1
        cx = round(band.logo.width * 360_000)
        cy = cx * picture.height // picture.width
        relation = f"rIdImage{number}"
        rels = (
            _HEAD + f'<Relationships xmlns="{_REL}"><Relationship Id="{relation}" '
            f'Type="{_TYPE}/image" Target="media/image{number}.{picture.extension}"/>'
            "</Relationships>"
        )
        return image.run(relation, body.drawings, cx, cy, "logo", "Logo"), rels

    if bands.header:
        body.used.add(look.HEADER)
        drawn, rels = logo(style.header)
        parts.append(
            (
                f"word/{HEADER_PART}",
                look.band_xml(
                    "hdr",
                    style.header,
                    title=body.title,
                    width=width,
                    logo=drawn,
                    watermark=style.watermark,
                ),
            )
        )
        if rels:
            parts.append((f"word/_rels/{HEADER_PART}.rels", rels))
        listed.append(("rIdHeader1", "header", HEADER_PART))
    drawn, rels = logo(style.footer)
    parts.append(
        (
            "word/footer1.xml",
            look.band_xml("ftr", style.footer, title=body.title, width=width, logo=drawn),
        )
    )
    if rels:
        parts.append(("word/_rels/footer1.xml.rels", rels))
    if bands.title_page:
        body.used.add(look.HEADER)
        parts.append(
            (f"word/{FIRST_HEADER_PART}", look.band_xml("hdr", None, title="", width=width))
        )
        parts.append(
            (f"word/{FIRST_FOOTER_PART}", look.band_xml("ftr", None, title="", width=width))
        )
        listed.append(("rIdHeader2", "header", FIRST_HEADER_PART))
        listed.append(("rIdFooter2", "footer", FIRST_FOOTER_PART))
    return parts, listed


def render(prepared: Prepared, style: Style = HOUSE) -> bytes:
    """Le fichier .docx, le même octet pour octet pour un même document et un même style."""
    body = _Body(style, plain(_title_runs(prepared)))
    document, bands = _document(prepared, body)
    body_media = len(body.media)
    band_parts, listed = _band_parts(body, bands)
    types, relations = _parts_listed(len(body.charts), body.media, body_media, listed)
    parts: tuple[tuple[str, str | bytes], ...] = (
        ("[Content_Types].xml", types),
        ("_rels/.rels", PACKAGE_RELS),
        ("docProps/core.xml", _core(prepared.title, prepared.template)),
        ("docProps/app.xml", APP),
        ("word/document.xml", document),
        ("word/_rels/document.xml.rels", relations),
        ("word/styles.xml", look.styles_xml(style, body.used)),
        ("word/numbering.xml", look.numbering_xml(body.lists, style.numbering)),
        ("word/settings.xml", SETTINGS),
        *band_parts,
        *((f"word/charts/chart{n}.xml", chart.part(c)) for n, c in enumerate(body.charts, start=1)),
        *(
            (f"word/media/image{n}.{p.extension}", p.data)
            for n, p in enumerate(body.media, start=1)
        ),
    )
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in parts:
            entry = zipfile.ZipInfo(name, date_time=ZIP_DATE)
            # Une image est déjà compressée : rangée telle quelle, sans temps perdu.
            entry.compress_type = (
                zipfile.ZIP_STORED if isinstance(content, bytes) else zipfile.ZIP_DEFLATED
            )
            entry.external_attr = 0o600 << 16
            archive.writestr(entry, content if isinstance(content, bytes) else content.encode())
    return raw.getvalue()


def _title_runs(prepared: Prepared) -> Inline:
    for block in prepared.blocks:
        if isinstance(block, TitleOut):
            return block.runs
    return ()
