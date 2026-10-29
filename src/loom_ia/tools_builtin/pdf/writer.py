# SPDX-License-Identifier: Apache-2.0
"""Écrire un PDF : le document validé posé dans le style maison, avec reportlab.

Le même document que ``word`` — A4, marges de 2 cm, titres bleu nuit,
tableaux à en-tête bleu pâle et ligne de total en gras, « Page n / N » en pied —
dans les polices standard du PDF (Helvetica), qui ne s'embarquent pas : le
fichier reste léger et s'affiche partout. Elles couvrent le français et
l'Europe de l'Ouest (WinAnsi), plus les symboles grecs et mathématiques ; un
caractère hors de ce jeu (emoji, idéogramme…) s'imprimerait en carré noir, et
le résultat le signale.

Deux documents identiques donnent le même fichier, octet pour octet : le mode
``invariant`` de reportlab fixe la date et l'identifiant du PDF.
"""

import io
from collections.abc import Callable, Iterable
from typing import Any, Final, cast
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    Flowable,
    ListFlowable,
    ListItem,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Table,
    TableStyle,
)
from reportlab.platypus.flowables import HRFlowable

from loom_ia.tools_builtin._document.content import (
    BlockOut,
    BreakOut,
    HeadingOut,
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

# --- Style maison ------------------------------------------------------------------

MARGIN: Final = 2 * cm
TEXT_WIDTH: Final = A4[0] - 2 * MARGIN
FONT: Final = "Helvetica"
BOLD: Final = "Helvetica-Bold"
NAVY: Final = colors.HexColor("#1F3864")
BLUE: Final = colors.HexColor("#2F5496")
GREY: Final = colors.HexColor("#595959")
RULE: Final = colors.HexColor("#A6A6A6")
HEADER_FILL: Final = colors.HexColor("#D9E1F2")
NOTE_FILL: Final = colors.HexColor("#F2F2F2")
MAX_MISSING_SAID: Final = 10

BODY: Final = ParagraphStyle("Corps", fontName=FONT, fontSize=10.5, leading=14, spaceAfter=6)
TITLE: Final = ParagraphStyle(
    "Titre", parent=BODY, fontName=BOLD, fontSize=20, leading=24, textColor=NAVY, spaceAfter=4
)
HEADINGS: Final = {
    1: ParagraphStyle(
        "Titre1",
        parent=BODY,
        fontName=BOLD,
        fontSize=16,
        leading=20,
        textColor=NAVY,
        spaceBefore=14,
        spaceAfter=6,
        keepWithNext=1,
    ),
    2: ParagraphStyle(
        "Titre2",
        parent=BODY,
        fontName=BOLD,
        fontSize=13,
        leading=16,
        textColor=NAVY,
        spaceBefore=10,
        spaceAfter=4,
        keepWithNext=1,
    ),
    3: ParagraphStyle(
        "Titre3",
        parent=BODY,
        fontName=BOLD,
        fontSize=11.5,
        leading=14,
        textColor=BLUE,
        spaceBefore=8,
        spaceAfter=3,
        keepWithNext=1,
    ),
}
ITEM: Final = ParagraphStyle("Element", parent=BODY, spaceAfter=2)
QUOTE: Final = ParagraphStyle(
    "Citation",
    parent=BODY,
    fontName="Helvetica-Oblique",
    textColor=GREY,
    spaceAfter=2,
)
SOURCE: Final = ParagraphStyle(
    "Source", parent=BODY, textColor=GREY, alignment=TA_RIGHT, spaceAfter=0
)
NOTE: Final = ParagraphStyle("Note", parent=BODY, spaceAfter=0)
CAPTION: Final = ParagraphStyle(
    "Legende",
    parent=BODY,
    fontName="Helvetica-Oblique",
    fontSize=8.5,
    leading=11,
    textColor=GREY,
    alignment=TA_CENTER,
    spaceBefore=3,
    spaceAfter=12,
)
CELL: Final = ParagraphStyle("Cellule", parent=BODY, fontSize=9.5, leading=12, spaceAfter=0)
CELLS: Final = {
    "left": CELL,
    "center": ParagraphStyle("CelluleCentre", parent=CELL, alignment=TA_CENTER),
    "right": ParagraphStyle("CelluleDroite", parent=CELL, alignment=TA_RIGHT),
}
HEADER_CELL: Final = ParagraphStyle(
    "EnTete", parent=CELL, fontName=BOLD, alignment=TA_CENTER, leading=11.5
)
# Un tableau vide de contenu garde une ligne, et un texte long ne déborde pas.
_ALIGN: Final = {"left": TA_LEFT, "center": TA_CENTER, "right": TA_RIGHT}


def _footer(total: int) -> Callable[[Canvas, Any], None]:
    """Le pied de chaque page : « Page n / N », le total venu d'un premier rendu."""

    def draw(canvas: Canvas, _: Any) -> None:
        canvas.saveState()
        canvas.setFont(FONT, 8.5)
        canvas.setFillColor(GREY)
        canvas.drawCentredString(A4[0] / 2, 1 * cm, f"Page {canvas.getPageNumber()} / {total}")
        canvas.restoreState()

    return draw


def _markup(runs: Inline, *, bold: bool = False) -> str:
    """Le balisage de ``Paragraph`` : le texte échappé, l'emphase en <b> et <i>."""
    said = ""
    for run in runs:
        text = escape(run.text).replace("\n", "<br/>").replace("\t", "&nbsp;" * 4)
        if run.italic:
            text = f"<i>{text}</i>"
        if run.bold or bold:
            text = f"<b>{text}</b>"
        said += text
    return said


def _text(text: str) -> str:
    return escape(text).replace("\n", "<br/>")


def _table(table: TableOut) -> list[Flowable]:
    widths = [part * TEXT_WIDTH for part in table.widths]
    data: list[list[Flowable]] = [[Paragraph(_text(h), HEADER_CELL) for h in table.headers]]
    for cells in table.rows:
        data.append(
            [Paragraph(_text(c), CELLS[a]) for c, a in zip(cells, table.align, strict=True)]
        )
    commands: list[tuple[Any, ...]] = [
        ("GRID", (0, 0), (-1, -1), 0.5, RULE),
        ("BACKGROUND", (0, 0), (-1, 0), HEADER_FILL),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]
    if table.total is not None:
        bold = {
            align: ParagraphStyle(f"Total{align}", parent=style, fontName=BOLD)
            for align, style in CELLS.items()
        }
        data.append(
            [Paragraph(_text(c), bold[a]) for c, a in zip(table.total, table.align, strict=True)]
        )
        commands.append(("LINEABOVE", (0, -1), (-1, -1), 1.2, NAVY))
    found = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    found.setStyle(TableStyle(commands))
    flowables: list[Flowable] = [found]
    flowables.append(
        Paragraph(_markup(table.caption), CAPTION)
        if table.caption
        else Paragraph("", ParagraphStyle("Apres", parent=BODY, spaceAfter=4, leading=4))
    )
    return flowables


def _note(note: NoteOut) -> Flowable:
    head = f"<b>{_text(note.title)}</b><br/>" if note.title else ""
    box = Table([[Paragraph(head + _markup(note.runs), NOTE)]], colWidths=[TEXT_WIDTH])
    box.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), NOTE_FILL),
                ("LINEBEFORE", (0, 0), (0, -1), 3, BLUE),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    box.spaceBefore, box.spaceAfter = 6, 10
    return box


def _quote(content: list[Flowable]) -> Flowable:
    """Une citation : en retrait, un filet gris à gauche, comme dans ``word``."""
    box = Table([[content]], colWidths=[TEXT_WIDTH - 2 * cm], hAlign="CENTER")
    box.setStyle(
        TableStyle(
            [
                ("LINEBEFORE", (0, 0), (0, -1), 1.5, RULE),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    box.spaceBefore, box.spaceAfter = 6, 10
    return box


def _list(block: ListOut) -> Flowable:
    items: list[ListItem] = []
    for entry in block.entries:
        content: list[Flowable] = [Paragraph(_markup(entry.runs), ITEM)]
        if entry.children:
            content.append(
                ListFlowable(
                    [Paragraph(_markup(child), ITEM) for child in entry.children],
                    bulletType="a" if block.ordered else "bullet",
                    start=None if block.ordered else "\u2013",
                    bulletFormat="%s)" if block.ordered else None,
                    leftIndent=14,
                    bulletFontName=FONT,
                    bulletFontSize=10,
                )
            )
        items.append(ListItem(content))
    found = ListFlowable(
        cast(list[Flowable], items),
        bulletType="1" if block.ordered else "bullet",
        start=None if block.ordered else "\u2022",
        bulletFormat="%s." if block.ordered else None,
        leftIndent=16,
        bulletFontName=FONT,
        bulletFontSize=10,
    )
    found.spaceAfter = 6
    return found


def _flowables(blocks: Iterable[BlockOut]) -> list[Flowable]:
    found: list[Flowable] = []
    for block in blocks:
        match block:
            case TitleOut(runs=runs):
                found.append(Paragraph(_markup(runs), TITLE))
                found.append(HRFlowable(width="100%", thickness=1, color=BLUE, spaceAfter=14))
            case HeadingOut(level=level, runs=runs):
                found.append(Paragraph(_markup(runs), HEADINGS[level]))
            case ParagraphOut(runs=runs):
                found.append(Paragraph(_markup(runs), BODY))
            case ListOut():
                found.append(_list(block))
            case TableOut():
                found.extend(_table(block))
            case NoteOut():
                found.append(_note(block))
            case QuoteOut(runs=runs, source=source):
                quote: list[Flowable] = [Paragraph(_markup(runs), QUOTE)]
                if source:
                    quote.append(Paragraph(_text(f"\u2014 {source}"), SOURCE))
                found.append(_quote(quote))
            case BreakOut():
                found.append(PageBreak())
    return found


def missing_glyphs(prepared: Prepared) -> list[str]:
    """Les caractères que les polices standard n'ont pas : ils s'imprimeraient en carré noir."""
    font = pdfmetrics.getFont(FONT)
    encodings = [font.encName, *(f.encName for f in font.substitutionFonts)]
    text: set[str] = set(prepared.title or "")
    for block in prepared.blocks:
        text |= set(_said(block))
    missing: list[str] = []
    for char in sorted(text - {"\n", "\t"}):
        if not any(_encodes(char, encoding) for encoding in encodings):
            missing.append(char)
    return missing


def _encodes(char: str, encoding: str) -> bool:
    try:
        char.encode(encoding)
    except UnicodeEncodeError:
        return False
    return True


def _said(block: BlockOut) -> str:
    match block:
        case TitleOut(runs=runs) | HeadingOut(runs=runs) | ParagraphOut(runs=runs):
            return plain(runs)
        case ListOut(entries=entries):
            return "".join(plain(e.runs) + "".join(plain(c) for c in e.children) for e in entries)
        case TableOut():
            cells = [*block.headers, *(c for row in block.rows for c in row), *(block.total or ())]
            return "".join(cells) + (plain(block.caption) if block.caption else "")
        case NoteOut(title=title, runs=runs):
            return (title or "") + plain(runs)
        case QuoteOut(runs=runs, source=source):
            return plain(runs) + (source or "")
        case BreakOut():
            return ""


def render(prepared: Prepared) -> tuple[bytes, int, list[str]]:
    """Le PDF, son nombre de pages, et les avertissements du rendu."""
    warnings: list[str] = []
    missing = missing_glyphs(prepared)
    if missing:
        shown = " ".join(missing[:MAX_MISSING_SAID]) + (
            " …" if len(missing) > MAX_MISSING_SAID else ""
        )
        warnings.append(
            f"{len(missing)} caractère(s) absent(s) des polices du PDF, imprimé(s) en carré "
            f"noir : {shown} — les remplacer par du texte"
        )
    # Deux rendus : le premier compte les pages, le second écrit « Page n / N ».
    total = _build(prepared, None)[1]
    data, pages = _build(prepared, total)
    return data, pages, warnings


def _build(prepared: Prepared, total: int | None) -> tuple[bytes, int]:
    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=MARGIN,
        bottomMargin=MARGIN,
        title=prepared.title or "",
        author="loom-ia",
        subject=prepared.title or "",
        creator="loom-ia",
        lang="fr-FR",
        invariant=1,
    )
    # Des blocs neufs à chaque rendu : reportlab découpe ceux qu'il pose.
    flowables = _flowables(prepared.blocks)
    if total is None:
        document.build(flowables)
    else:
        footer = _footer(total)
        document.build(flowables, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue(), document.page
