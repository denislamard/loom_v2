# SPDX-License-Identifier: Apache-2.0
"""Écrire un .docx : le document validé posé dans le style maison, en WordprocessingML.

Sans bibliothèque : un .docx est un zip de quelques parties XML, que ce module
écrit lui-même. Le style est figé ici — A4, marges de 2 cm, Calibri 11, titres
bleu nuit, tableaux à en-tête bleu pâle et ligne de total en gras, numéros de
page en pied — et le document est en français (correcteur, césure).

Deux documents identiques donnent le même fichier, octet pour octet : les
dates des propriétés et de l'archive sont fixées, et rien ne dépend de
l'heure ni de la machine. L'ordre des éléments suit le schéma
WordprocessingML, que Word, contrairement à LibreOffice, applique strictement.
"""

import io
import zipfile
from collections.abc import Iterable
from typing import Final
from xml.sax.saxutils import escape, quoteattr

from loom_ia.tools_builtin._document.content import (
    BlockOut,
    BreakOut,
    ChartOut,
    HeadingOut,
    Inline,
    ListOut,
    NoteOut,
    ParagraphOut,
    Prepared,
    QuoteOut,
    TableOut,
    TitleOut,
)
from loom_ia.tools_builtin.word import chart

ZIP_DATE: Final = (2000, 1, 1, 0, 0, 0)
FIXED_DATE: Final = "2000-01-01T00:00:00Z"

# --- Style maison ------------------------------------------------------------------
# Mesures en vingtièmes de point (twips) : 567 pour un centimètre.

PAGE_WIDTH: Final = 11906
PAGE_HEIGHT: Final = 16838
MARGIN: Final = 1134
TEXT_WIDTH: Final = PAGE_WIDTH - 2 * MARGIN
FONT: Final = "Calibri"
NAVY: Final = "1F3864"
BLUE: Final = "2F5496"
GREY: Final = "595959"
RULE: Final = "A6A6A6"
HEADER_FILL: Final = "D9E1F2"
NOTE_FILL: Final = "F2F2F2"

_W: Final = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_R: Final = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_REL: Final = "http://schemas.openxmlformats.org/package/2006/relationships"
_TYPE: Final = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_CT: Final = "application/vnd.openxmlformats-officedocument.wordprocessingml"
_HEAD: Final = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'

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


def _with_charts(count: int) -> tuple[str, str]:
    """Types de contenu et relations du corps : ceux d'un document sans graphique, à
    l'octet près, plus une partie et une relation par graphique."""
    if not count:
        return CONTENT_TYPES, DOCUMENT_RELS
    types = "".join(
        f'<Override PartName="/word/charts/chart{n}.xml" ContentType="{_CHART_TYPE}"/>'
        for n in range(1, count + 1)
    )
    relations = "".join(
        f'<Relationship Id="rIdChart{n}" Type="{_TYPE}/chart" Target="charts/chart{n}.xml"/>'
        for n in range(1, count + 1)
    )
    return (
        CONTENT_TYPES.replace("</Types>", types + "</Types>"),
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


def _heading(level: int, size: int, before: int, after: int, color: str) -> str:
    return _style(
        f"Heading{level}",
        f"heading {level}",
        next_style="Normal",
        paragraph=f'<w:keepNext/><w:keepLines/><w:spacing w:before="{before}" '
        f'w:after="{after}"/><w:outlineLvl w:val="{level - 1}"/>',
        run=f'<w:b/><w:bCs/><w:color w:val="{color}"/><w:sz w:val="{size}"/>'
        f'<w:szCs w:val="{size}"/>',
    )


STYLES: Final = (
    _HEAD + f'<w:styles xmlns:w="{_W}">'
    "<w:docDefaults><w:rPrDefault><w:rPr>"
    f'<w:rFonts w:ascii="{FONT}" w:hAnsi="{FONT}" w:eastAsia="{FONT}" w:cs="{FONT}"/>'
    '<w:sz w:val="22"/><w:szCs w:val="22"/><w:lang w:val="fr-FR" w:eastAsia="fr-FR" '
    'w:bidi="ar-SA"/></w:rPr></w:rPrDefault>'
    '<w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="276" w:lineRule="auto"/>'
    "</w:pPr></w:pPrDefault></w:docDefaults>"
    + _style("Normal", "Normal", based=None, default=True)
    + _style(
        "Title",
        "Title",
        next_style="Normal",
        paragraph=f'<w:pBdr><w:bottom w:val="single" w:sz="8" w:space="4" w:color="{BLUE}"/>'
        '</w:pBdr><w:spacing w:after="300"/>',
        run=f'<w:b/><w:bCs/><w:color w:val="{NAVY}"/><w:sz w:val="40"/><w:szCs w:val="40"/>',
    )
    + _heading(1, 32, 360, 120, NAVY)
    + _heading(2, 26, 240, 80, NAVY)
    + _heading(3, 23, 200, 60, BLUE)
    + _style("ListParagraph", "List Paragraph", paragraph='<w:spacing w:after="60"/>')
    + _style(
        "Quote",
        "Quote",
        next_style="Normal",
        paragraph=f'<w:pBdr><w:left w:val="single" w:sz="12" w:space="8" w:color="{RULE}"/>'
        '</w:pBdr><w:spacing w:before="160" w:after="80"/><w:ind w:left="567" w:right="567"/>',
        run=f'<w:i/><w:iCs/><w:color w:val="{GREY}"/>',
    )
    + _style(
        "QuoteSource",
        "Quote Source",
        based="Quote",
        next_style="Normal",
        paragraph='<w:spacing w:after="200"/><w:jc w:val="right"/>',
        run='<w:i w:val="0"/><w:iCs w:val="0"/>',
    )
    + _style(
        "Note",
        "Note",
        next_style="Normal",
        paragraph=f'<w:pBdr><w:left w:val="single" w:sz="24" w:space="8" w:color="{BLUE}"/>'
        f'</w:pBdr><w:shd w:val="clear" w:color="auto" w:fill="{NOTE_FILL}"/>'
        '<w:spacing w:before="120" w:after="200"/><w:ind w:left="170" w:right="170"/>',
    )
    + _style(
        "Caption",
        "caption",
        next_style="Normal",
        paragraph='<w:spacing w:before="60" w:after="240"/><w:jc w:val="center"/>',
        run=f'<w:i/><w:iCs/><w:color w:val="{GREY}"/><w:sz w:val="18"/><w:szCs w:val="18"/>',
    )
    + _style(
        "TableText",
        "Table Text",
        paragraph='<w:spacing w:after="0" w:line="240" w:lineRule="auto"/>',
        run='<w:sz w:val="20"/><w:szCs w:val="20"/>',
    )
    + _style(
        "Footer",
        "footer",
        paragraph='<w:spacing w:after="0"/><w:jc w:val="center"/>',
        run=f'<w:color w:val="{GREY}"/><w:sz w:val="18"/><w:szCs w:val="18"/>',
    )
    + "</w:styles>"
)

FOOTER: Final = (
    _HEAD + f'<w:ftr xmlns:w="{_W}"><w:p><w:pPr><w:pStyle w:val="Footer"/></w:pPr>'
    '<w:r><w:t xml:space="preserve">Page </w:t></w:r>'
    '<w:fldSimple w:instr=" PAGE "><w:r><w:t>1</w:t></w:r></w:fldSimple>'
    '<w:r><w:t xml:space="preserve"> / </w:t></w:r>'
    '<w:fldSimple w:instr=" NUMPAGES "><w:r><w:t>1</w:t></w:r></w:fldSimple>'
    "</w:p></w:ftr>"
)

# Puces et numéros : deux niveaux, la sous-liste en retrait.
_LEVELS: Final = {
    False: (("bullet", "•"), ("bullet", "\u2013")),
    True: (("decimal", "%1."), ("lowerLetter", "%2)")),
}


def _abstract(number: int, ordered: bool) -> str:
    said = f'<w:abstractNum w:abstractNumId="{number}"><w:multiLevelType w:val="hybridMultilevel"/>'
    for level, (kind, text) in enumerate(_LEVELS[ordered]):
        left = 720 * (level + 1)
        said += (
            f'<w:lvl w:ilvl="{level}"><w:start w:val="1"/><w:numFmt w:val="{kind}"/>'
            f'<w:lvlText w:val="{text}"/><w:lvlJc w:val="left"/>'
            f'<w:pPr><w:ind w:left="{left}" w:hanging="360"/></w:pPr></w:lvl>'
        )
    return said + "</w:abstractNum>"


def _numbering(lists: Iterable[bool]) -> str:
    """Une numérotation par liste : chaque liste numérotée repart de 1."""
    said = _HEAD + f'<w:numbering xmlns:w="{_W}">' + _abstract(0, False) + _abstract(1, True)
    for number, ordered in enumerate(lists, start=1):
        said += f'<w:num w:numId="{number}"><w:abstractNumId w:val="{int(ordered)}"/>'
        said += "".join(
            f'<w:lvlOverride w:ilvl="{level}"><w:startOverride w:val="1"/></w:lvlOverride>'
            for level in range(2)
        )
        said += "</w:num>"
    return said + "</w:numbering>"


def _core(title: str | None) -> str:
    named = f"<dc:title>{escape(title)}</dc:title>" if title else ""
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


# --- Corps ---------------------------------------------------------------------------


def _runs(runs: Inline, *, bold: bool = False) -> str:
    said = ""
    for run in runs:
        props = ("<w:b/><w:bCs/>" if run.bold or bold else "") + (
            "<w:i/><w:iCs/>" if run.italic else ""
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


def _table(table: TableOut) -> str:
    widths = [round(part * TEXT_WIDTH) for part in table.widths]
    widths[-1] += TEXT_WIDTH - sum(widths)
    border = f'w:val="single" w:sz="4" w:space="0" w:color="{RULE}"'
    said = (
        '<w:tbl><w:tblPr><w:tblW w:w="5000" w:type="pct"/><w:tblBorders>'
        + "".join(
            f"<w:{side} {border}/>"
            for side in ("top", "left", "bottom", "right", "insideH", "insideV")
        )
        + '</w:tblBorders><w:tblLayout w:type="fixed"/><w:tblCellMar>'
        '<w:top w:w="40" w:type="dxa"/><w:left w:w="85" w:type="dxa"/>'
        '<w:bottom w:w="40" w:type="dxa"/><w:right w:w="85" w:type="dxa"/>'
        '</w:tblCellMar><w:tblLook w:val="04A0" w:firstRow="1" w:lastRow="0" '
        'w:firstColumn="0" w:lastColumn="0" w:noHBand="1" w:noVBand="1"/></w:tblPr>'
        "<w:tblGrid>" + "".join(f'<w:gridCol w:w="{w}"/>' for w in widths) + "</w:tblGrid>"
    )

    def row(cells: Iterable[str], *, header: bool = False, total: bool = False) -> str:
        props = (
            "<w:trPr><w:cantSplit/><w:tblHeader/></w:trPr>"
            if header
            else ("<w:trPr><w:cantSplit/></w:trPr>")
        )
        said = f"<w:tr>{props}"
        for index, text in enumerate(cells):
            fill = f'<w:shd w:val="clear" w:color="auto" w:fill="{HEADER_FILL}"/>' if header else ""
            top = (
                f'<w:tcBorders><w:top w:val="single" w:sz="12" w:space="0" w:color="{NAVY}"/>'
                "</w:tcBorders>"
                if total
                else ""
            )
            align = "center" if header else table.align[index]
            content = _run(text, "<w:rPr><w:b/><w:bCs/></w:rPr>" if header or total else "")
            said += (
                f'<w:tc><w:tcPr><w:tcW w:w="{widths[index]}" w:type="dxa"/>{top}{fill}'
                f'<w:vAlign w:val="center"/></w:tcPr>'
                + _paragraph("TableText", content, f'<w:jc w:val="{_JC[align]}"/>')
                + "</w:tc>"
            )
        return said + "</w:tr>"

    said += row(table.headers, header=True)
    said += "".join(row(cells) for cells in table.rows)
    if table.total is not None:
        said += row(table.total, total=True)
    said += "</w:tbl>"
    # Toujours un paragraphe après le tableau : deux tableaux voisins ne fusionnent pas.
    if table.caption:
        said += _paragraph("Caption", _runs(table.caption))
    else:
        said += _paragraph("Normal", "", '<w:spacing w:after="120"/>')
    return said


_JC: Final = {"left": "left", "center": "center", "right": "right"}


def _body(blocks: Iterable[BlockOut]) -> tuple[str, list[bool], list[ChartOut]]:
    """Le corps du document ; pour chaque liste, si elle est numérotée ; les graphiques."""
    said = ""
    lists: list[bool] = []
    charts: list[ChartOut] = []
    for block in blocks:
        match block:
            case TitleOut(runs=runs):
                said += _paragraph("Title", _runs(runs))
            case HeadingOut(level=level, runs=runs):
                said += _paragraph(f"Heading{level}", _runs(runs))
            case ParagraphOut(runs=runs):
                said += _paragraph("Normal", _runs(runs))
            case ListOut(ordered=ordered, entries=entries):
                lists.append(ordered)
                number = len(lists)
                for entry in entries:
                    said += _list_item(number, 0, entry.runs)
                    said += "".join(_list_item(number, 1, child) for child in entry.children)
            case TableOut():
                said += _table(block)
            case NoteOut(title=title, runs=runs):
                head = (
                    _run(title, "<w:rPr><w:b/><w:bCs/></w:rPr>") + "<w:r><w:br/></w:r>"
                    if title
                    else ""
                )
                said += _paragraph("Note", head + _runs(runs))
            case QuoteOut(runs=runs, source=source):
                said += _paragraph("Quote", _runs(runs))
                if source:
                    said += _paragraph("QuoteSource", _run(f"— {source}"))
            case BreakOut():
                said += '<w:p><w:r><w:br w:type="page"/></w:r></w:p>'
            case ChartOut():
                charts.append(block)
                number = len(charts)
                said += chart.anchor(f"rIdChart{number}", number, block)
                if block.caption:
                    said += _paragraph("Caption", _runs(block.caption))
                else:
                    said += _paragraph("Normal", "", '<w:spacing w:after="120"/>')
    return said, lists, charts


def _list_item(number: int, level: int, runs: Inline) -> str:
    numbering = f'<w:numPr><w:ilvl w:val="{level}"/><w:numId w:val="{number}"/></w:numPr>'
    return _paragraph("ListParagraph", _runs(runs), numbering)


def _document(blocks: Iterable[BlockOut]) -> tuple[str, list[bool], list[ChartOut]]:
    body, lists, charts = _body(blocks)
    section = (
        '<w:sectPr><w:footerReference w:type="default" r:id="rId4"/>'
        f'<w:pgSz w:w="{PAGE_WIDTH}" w:h="{PAGE_HEIGHT}"/>'
        f'<w:pgMar w:top="{MARGIN}" w:right="{MARGIN}" w:bottom="{MARGIN}" '
        f'w:left="{MARGIN}" w:header="567" w:footer="567" w:gutter="0"/>'
        "</w:sectPr>"
    )
    return (
        _HEAD + f"<w:document xmlns:w={quoteattr(_W)} xmlns:r={quoteattr(_R)}>"
        f"<w:body>{body}{section}</w:body></w:document>",
        lists,
        charts,
    )


def render(prepared: Prepared) -> bytes:
    """Le fichier .docx, le même octet pour octet pour un même document."""
    document, lists, charts = _document(prepared.blocks)
    types, relations = _with_charts(len(charts))
    parts = (
        ("[Content_Types].xml", types),
        ("_rels/.rels", PACKAGE_RELS),
        ("docProps/core.xml", _core(prepared.title)),
        ("docProps/app.xml", APP),
        ("word/document.xml", document),
        ("word/_rels/document.xml.rels", relations),
        ("word/styles.xml", STYLES),
        ("word/numbering.xml", _numbering(lists)),
        ("word/settings.xml", SETTINGS),
        ("word/footer1.xml", FOOTER),
        *((f"word/charts/chart{n}.xml", chart.part(c)) for n, c in enumerate(charts, start=1)),
    )
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, text in parts:
            entry = zipfile.ZipInfo(name, date_time=ZIP_DATE)
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o600 << 16
            archive.writestr(entry, text.encode("utf-8"))
    return raw.getvalue()
