# SPDX-License-Identifier: Apache-2.0
"""Écrire un PDF : le document validé, posé dans son style, avec reportlab.

Le même style que ``word`` — ``default.yaml``, la charte, le style du modèle — et la
même mise en page des sections : côte à côte, encadrées, en colonnes, en paysage, page
de garde. Le code n'a aucune valeur visuelle : polices, tailles, couleurs, filets et
espacements viennent du style résolu (``Style``) ; il n'ajoute que la mécanique (un
paragraphe encadré est une cellule de tableau, un en-tête est dessiné à chaque page).

**Les polices.** Le PDF a trois familles standard, qui ne s'embarquent pas : une police
du style s'y rend par son équivalent standard (``PDF_FONTS``, ``PDF_FALLBACK``). Une
police dont le style donne les fichiers TrueType (``fonts``) est embarquée, en
sous-ensemble. Un caractère qu'une police n'a pas s'imprimerait en carré noir : le
résultat le signale.

**Ce que le PDF fait mieux que Word** : le sommaire a ses vrais numéros de page dès
l'écriture, et « Page n / N » aussi, par des rendus successifs (le premier compte les
pages et repère les titres, le suivant les écrit).

Deux documents identiques donnent le même fichier, octet pour octet : le mode
``invariant`` de reportlab fixe la date et l'identifiant du PDF.
"""

import hashlib
import io
import re
import threading
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, fields, replace
from typing import Any, Final, cast
from xml.sax.saxutils import escape, quoteattr

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT
from reportlab.lib.fonts import ps2tt, tt2ps
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm, mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
    Frame,
    Image,
    KeepTogether,
    ListFlowable,
    ListItem,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.doctemplate import LayoutError
from reportlab.platypus.flowables import BalancedColumns
from reportlab.platypus.tableofcontents import drawPageNumbers

from loom_ia.core.ports import ToolError
from loom_ia.tools_builtin._document.content import (
    QUOTES,
    REF_MARK,
    BlockOut,
    ChartOut,
    CodeOut,
    ContentsOut,
    Entry,
    FieldsOut,
    HeadingOut,
    ImageOut,
    Inline,
    ListOut,
    NoteOut,
    ParagraphOut,
    Prepared,
    QuoteOut,
    Run,
    SignatureOut,
    TableOut,
    TitleOut,
    plain,
    referenced,
)
from loom_ia.tools_builtin._style_params import (
    PDF_FALLBACK,
    PDF_FONTS,
    Band,
    Look,
    Style,
    house,
    parse_rule,
)
from loom_ia.tools_builtin._templates import SectionSpec, Template
from loom_ia.tools_builtin.pdf import chart

MAX_MISSING_SAID: Final = 10
# Un twip (vingtième de point) en points : les mesures du style résolu.
TWIP: Final = 1 / 20
# L'interligne simple, en part de la taille du texte.
LEADING: Final = 1.2
# Les distances de Word : l'en-tête et le pied à 1 cm du bord, l'écart entre deux colonnes
# de texte, entre deux cellules côte à côte, la marge d'un encadré, l'espace en haut d'une
# page de garde, la place pour signer.
BAND_DISTANCE: Final = 1 * cm
BAND_GAP: Final = 6.0
# Le soulignement d'un texte d'en-tête ou de pied, en fraction de sa taille : comme
# ``Paragraph`` (sous la ligne de base, d'un vingtième de la taille).
UNDERLINE_OFFSET: Final = -0.125
UNDERLINE_WIDTH: Final = 0.05
COLUMN_GAP: Final = 1 * cm
CELL_GAP: Final = 284 * TWIP
BOX_PAD: Final = 170 * TWIP
COVER_SPACE: Final = 6 * cm
SIGN_SPACE: Final = 2 * cm
# Le filet d'un paragraphe : son écart au texte, en haut et en bas, à gauche et à droite.
RULE_SPACE: Final = (4.0, 8.0)
# Les marges d'une cellule de tableau (haut et bas, gauche et droite).
CELL_PADDING: Final = (2.0, 85 * TWIP)
# Une liste : le texte à 36 pt du bord par niveau, la puce 18 pt avant.
LIST_INDENT: Final = 36.0
LIST_HANG: Final = 18.0
# Le sommaire : le retrait par niveau, les espacements de ses entrées.
TOC_INDENT: Final = 284 * TWIP
# Le filigrane : la place qu'il prend, en diagonale, et son opacité.
WATERMARK_WIDTH: Final = 440.0
WATERMARK_HEIGHT: Final = 110.0
WATERMARK_ALPHA: Final = 0.5
# L'espace après le paragraphe vide qui suit un tableau dans Word (120 twips).
AFTER_TABLE: Final = 120 * TWIP
# La part de la hauteur utile d'une page qu'une image peut prendre, avec sa légende dessous.
IMAGE_HEIGHT: Final = 0.85
# La case d'une liste à cocher : son côté, son trait.
CHECK_SIDE: Final = 0.62
CHECK_LINE: Final = 0.6
_ALIGN: Final = {"left": TA_LEFT, "center": TA_CENTER, "right": TA_RIGHT, "justify": TA_JUSTIFY}
_PLACEHOLDER: Final = re.compile(r"\{(page|pages|title)\}")
# Le rappel qui note la page d'un titre du sommaire, quand il se dessine.
MARK: Final = "loom_mark"
# L'ancre d'une note, en fin de document : ``NOTE_MARK`` et son numéro.
NOTE_MARK: Final = "loom_note_"
# Les puces des trois niveaux d'une liste, et la numérotation de chaque niveau (1., a), i.).
BULLETS: Final = ("\u2022", "\u2013", "\u25aa")
NUMBERS: Final = (("1", "%s."), ("a", "%s)"), ("i", "%s."))
# Une puce qu'une police n'a pas (le carré du troisième niveau) : la première, plus petite.
SMALL_BULLET: Final = 0.7
_LOOK_FIELDS: Final = frozenset(f.name for f in fields(Look))


# --- Les polices ----------------------------------------------------------------------

_REGISTERING: Final = threading.Lock()


def _registered(family: str, files: Mapping[str, bytes]) -> str:
    """Les fichiers TrueType d'une police, enregistrés une fois dans reportlab : le nom de
    sa graisse normale, qui nomme aussi sa famille (``<b>``, ``<i>``)."""
    digest = hashlib.sha256(b"".join(files.get(v, b"") for v in sorted(files))).hexdigest()
    names = {variant: f"loom-{digest[:16]}-{variant}" for variant in files}
    metrics: Any = pdfmetrics
    with _REGISTERING:
        known = pdfmetrics.getRegisteredFontNames()
        for variant, data in files.items():
            if names[variant] not in known:
                metrics.registerFont(TTFont(names[variant], io.BytesIO(data)))
        regular = names["regular"]
        metrics.registerFontFamily(
            regular,
            normal=regular,
            bold=names.get("bold", regular),
            italic=names.get("italic", regular),
            boldItalic=names.get("bold_italic", names.get("bold", regular)),
        )
    del family
    return regular


def register_fonts(styles: Iterable[Style]) -> None:
    """Les polices embarquées des styles, enregistrées au montage : un fichier que reportlab
    ne sait pas lire se voit dès ``loom validate`` (``ValueError``)."""
    for style in styles:
        for family, files in style.fonts.items():
            try:
                _registered(family, files)
            except Exception as exc:  # reportlab lève ce que sa lecture rencontre
                raise ValueError(f"police {family} : illisible par reportlab ({exc})") from None


class _Fonts:
    """Les polices PDF d'un style : une police dont le style donne les fichiers est
    embarquée ; une autre se rend par son équivalent standard."""

    def __init__(self, style: Style) -> None:
        self._embedded = {
            family: _registered(family, files) for family, files in style.fonts.items()
        }

    def name(self, family: str | None, bold: bool = False, italic: bool = False) -> str:
        """La police PDF d'une police du style, dans sa graisse et son style."""
        if family and family in self._embedded:
            regular = self._embedded[family]
        else:
            regular = PDF_FONTS.get((family or "").lower(), PDF_FALLBACK)
        found, *_ = ps2tt(regular)
        return tt2ps(found, 1 if bold else 0, 1 if italic else 0)


def substitutions(style: Style) -> dict[str, str]:
    """Les polices du style qui ne s'écrivent pas telles quelles : la famille standard qui
    les remplace, ou « embarquée »."""
    said: dict[str, str] = {}
    for name, look in style.looks.items():
        family = look.font
        # Le filigrane sans texte n'écrit rien.
        if not family or family in said or (name == "watermark" and not look.text):
            continue
        if family in style.fonts:
            said[family] = "embarquée"
            continue
        standard, *_ = ps2tt(PDF_FONTS.get(family.lower(), PDF_FALLBACK))
        if standard != family.lower():
            said[family] = standard.capitalize()
    return said


def _on_draw(canvas: Canvas, name: str, callback: Callable[[Canvas, str, str], None]) -> None:
    """Le rappel d'un ``<onDraw name=…>`` : reportlab 5 le cherche par ``getNamedCB`` ;
    avant (4.4, la borne basse), par un attribut du canevas."""
    named = getattr(canvas, "setNamedCB", None)
    if named is not None:
        named(name, callback)
    else:
        setattr(canvas, name, callback)


def _covers(font: str, char: str) -> bool:
    found: Any = pdfmetrics.getFont(font)
    if isinstance(found, TTFont):
        return ord(char) in cast(Mapping[int, int], cast(Any, found).face.charToGlyph)
    encodings = [found.encName, *(sub.encName for sub in found.substitutionFonts)]
    for encoding in encodings:
        try:
            char.encode(encoding)
        except UnicodeEncodeError:
            continue
        return True
    return False


# --- Le style en reportlab ---------------------------------------------------------------


def _color(value: str | None) -> Any:
    return colors.HexColor(f"#{value}") if value and value != "none" else None


def _rule(value: str | None) -> tuple[float, Any] | None:
    rule = parse_rule(value)
    if rule is None:
        return None
    width, color = rule
    return width, _color(color)


def _merged(look: Look, props: Mapping[str, Any]) -> Look:
    """Un style, avec les propriétés qu'un autre écrit en plus (l'en-tête d'un tableau)."""
    own = {name: value for name, value in props.items() if name in _LOOK_FIELDS}
    return replace(look, **own)


def _framed(look: Look) -> bool:
    """Un style qui dessine autour du texte : un filet, un fond."""
    return any(
        value is not None and value != "none"
        for value in (look.border, look.border_top, look.border_bottom, look.border_left)
    ) or (look.fill is not None and look.fill != "none")


# --- Les sections du modèle --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Layout:
    """Ce qu'une section du modèle dit de sa mise en page et de ses styles ; un document
    libre est une seule section, sans rien de tout cela."""

    styles: Mapping[str, str] = field(default_factory=dict[str, str])
    box: bool = False
    width: int | None = None
    page_break: bool = False
    columns: int = 1
    landscape: bool = False
    cover: bool = False
    # Espace avant la section, en points.
    space_before: float = 0.0
    # Une page de garde : son texte centré.
    align: str | None = None


PLAIN: Final = _Layout()


def _layout_of(spec: SectionSpec) -> _Layout:
    return _Layout(
        styles={str(element): name for element, name in spec.styles.items()},
        box=spec.box,
        width=spec.width,
        page_break=spec.page_break,
        columns=spec.columns,
        landscape=spec.landscape,
        cover=spec.cover,
        space_before=(spec.space_before or 0) * cm,
    )


@dataclass(slots=True)
class _Group:
    """Une section du document : sa ligne dans le modèle (``None`` pour un document libre,
    ou le titre du document avant les sections), sa mise en page, ses blocs."""

    row: int | None
    layout: _Layout
    blocks: list[BlockOut]


def _groups(prepared: Prepared, template: Template | None) -> list[_Group]:
    """Un document libre : un seul groupe. Un document qui suit un modèle : le titre du
    document, puis une section par groupe, dans l'ordre du modèle."""
    if prepared.sections is None or template is None:
        return [_Group(None, PLAIN, list(prepared.blocks))]
    rows = template.rows()
    specs = {section.position: section.spec for section in template.sections}
    groups: list[_Group] = []
    head = [block for block in prepared.blocks if isinstance(block, TitleOut)]
    if head:
        groups.append(_Group(None, PLAIN, list[BlockOut](head)))
    for section in prepared.sections:
        blocks: list[BlockOut] = [*([section.title] if section.title else []), *section.blocks]
        layout = _layout_of(specs[section.position])
        groups.append(_Group(rows[section.position], layout, blocks))
    # Une page de garde porte aussi ce qui précède sa section : le titre du document.
    if len(groups) > 1 and groups[0].row is None and groups[1].layout.cover:
        groups[1].blocks[:0] = groups[0].blocks
        del groups[0]
    return groups


@dataclass(slots=True)
class _Unit:
    """Ce qui se pose d'un tenant : une section, ou des sections côte à côte."""

    flowables: list[Flowable]
    landscape: bool
    cover: bool = False
    page_break: bool = False


# --- Les flowables propres -----------------------------------------------------------------


class _Check(Flowable):
    """La case d'une liste à cocher, dessinée : les polices standard n'ont pas de case."""

    def __init__(self, checked: bool, size: float, color: Any) -> None:
        super().__init__()
        self.checked = checked
        self.side = size * CHECK_SIDE
        self.color = color
        self.width = self.height = self.side

    def wrap(self, aW: float, aH: float) -> tuple[float, float]:
        del aW, aH
        return self.side, self.side

    def draw(self) -> None:
        canvas: Any = self.canv
        canvas.saveState()
        canvas.setStrokeColor(self.color or colors.black)
        canvas.setLineWidth(CHECK_LINE)
        canvas.rect(0, 0, self.side, self.side, stroke=1, fill=0)
        if self.checked:
            inset = self.side * 0.2
            canvas.line(inset, inset, self.side - inset, self.side - inset)
            canvas.line(inset, self.side - inset, self.side - inset, inset)
        canvas.restoreState()


class _Contents(Flowable):
    """Le sommaire : ses entrées (le titre, et son numéro s'il est numéroté), un lien vers
    chacune, et sa page, connue d'un rendu précédent, après une ligne de points."""

    def __init__(
        self,
        entries: Sequence[tuple[int, str, str]],
        pages: Mapping[str, int],
        levels: Sequence[ParagraphStyle],
        numbered: bool,
    ) -> None:
        super().__init__()
        self.entries = entries
        self.pages = pages
        self.levels = levels
        self.numbered = numbered
        self._table: Any = None

    def wrap(self, aW: float, aH: float) -> tuple[float, float]:
        canvas: Any = self.canv

        def end(canv: Canvas, kind: str, label: str) -> None:
            del kind
            page, level = (int(part) for part in label.split(","))
            drawPageNumbers(canv, self.levels[level], [(page, None)], aW, aH, " . ")

        if self.numbered:
            _on_draw(canvas, "loom_toc_page", end)
        rows: list[list[Flowable]] = []
        for level, label, key in self.entries:
            text = f'<a href="#{key}">{escape(label)}</a>'
            if self.numbered:
                page = self.pages.get(key, 0)
                text += f'<onDraw name="loom_toc_page" label="{page},{level - 1}"/>'
            rows.append([Paragraph(text, self.levels[level - 1])])
        self._table = Table(rows, colWidths=[aW])
        self._table.setStyle(
            TableStyle(
                [
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ]
            )
        )
        width, height = self._table.wrapOn(canvas, aW, aH)
        return width, height

    def split(self, aW: float, aH: float) -> list[Flowable]:
        self.wrap(aW, aH)
        return cast(list[Flowable], self._table.splitOn(self.canv, aW, aH))

    def drawOn(self, canvas: Canvas, x: float, y: float, _sW: float = 0) -> None:
        self._table.drawOn(canvas, x, y, _sW)


# --- Le corps ---------------------------------------------------------------------------


@dataclass(slots=True)
class _Pass:
    """Ce qu'un rendu apprend pour le suivant : le nombre de pages, la page de chaque titre
    du sommaire."""

    total: int | None = None
    pages: dict[str, int] = field(default_factory=dict[str, int])


class _Body:
    """Les blocs du document en flowables, selon le style ; les caractères de chaque police,
    pour dire ceux qu'elle n'a pas."""

    def __init__(
        self,
        prepared: Prepared,
        style: Style,
        template: Template | None,
        pages: Mapping[str, int],
    ) -> None:
        self.prepared = prepared
        self.style = style
        self.template = template
        self.pages = pages
        # La hauteur utile d'une page, que ``_build`` connaît : une image n'y dépasse pas.
        self.max_height = float("inf")
        self.typefaces = _Fonts(style)
        self.title = plain(_title_runs(prepared))
        self.fonts: dict[str, set[str]] = {}
        self._styles: dict[tuple[str, str | None, bool, bool], ParagraphStyle] = {}
        self._numbers: dict[str, int] = {}
        # Les titres : leur numéro (si le style numérote), et l'entrée du sommaire.
        self.heading_numbers: dict[int, str] = {}
        self.toc: list[tuple[int, str, str]] = []
        self.marks: dict[int, str] = {}
        # Les renvois : le rang de chaque titre, ceux qu'un renvoi vise, leur numéro.
        self.ranks: dict[int, int] = {}
        self.targeted = referenced(prepared.blocks)
        self.ref_numbers: dict[int, str] = {}
        # Les notes, numérotées dans l'ordre où le texte les appelle ; écrites en fin de
        # document.
        self.notes: list[Inline] = []
        self._note_numbers: dict[int, int] = {}
        self._headings(prepared.blocks)

    # --- styles

    def look(self, element: str, chosen: str | None, layout: _Layout) -> tuple[str, Look]:
        """Le style d'un élément : celui que le bloc choisit (``style``), sinon celui que la
        section choisit (``styles``), sinon le style prévu de l'élément."""
        name = chosen or layout.styles.get(element) or element
        return name, self.style.look(name)

    def paragraph_style(
        self,
        name: str,
        look: Look,
        *,
        align: str | None = None,
        keep: bool = False,
        bare: bool = False,
    ) -> ParagraphStyle:
        """Le style reportlab d'un style nommé. ``bare`` : sans retrait ni espacement, que
        porte le cadre qui l'entoure (un filet, un fond)."""
        key = (name, align, keep, bare)
        found = self._styles.get(key)
        if found is not None:
            return found
        size = look.size or 11.0
        indent = (look.indent or 0) * cm
        found = ParagraphStyle(
            f"loom-{len(self._styles)}-{name}",
            fontName=self.typefaces.name(look.font, bool(look.bold), bool(look.italic)),
            fontSize=size,
            leading=size * LEADING * (look.line_spacing or 1.0),
            textColor=_color(look.color) or colors.black,
            alignment=cast(Any, _ALIGN[align or look.align or "left"]),
            spaceBefore=0 if bare else look.space_before or 0,
            spaceAfter=0 if bare else look.space_after or 0,
            leftIndent=0 if bare else indent,
            rightIndent=0 if bare else indent,
            textTransform="uppercase" if look.uppercase else None,
            keepWithNext=int(keep),
        )
        self._styles[key] = found
        return found

    def text(self, runs: Inline, pstyle: ParagraphStyle, look: Look, *, prefix: str = "") -> str:
        """Le balisage de ``Paragraph`` : le texte échappé, l'emphase en ``<b>``, ``<i>``,
        ``<u>`` ; un lien dans le style ``link`` ; l'appel d'une note, en exposant, vers
        la note ; un renvoi : le numéro du titre visé (ou son titre entre guillemets), vers
        lui. Ses caractères comptés pour la police."""
        said = escape(prefix)
        seen = self.fonts.setdefault(pstyle.fontName, set())
        seen.update(prefix)
        for run in runs:
            if run.note is not None:
                number = self._note_numbers.get(id(run))
                if number is None:
                    self.notes.append(run.note)
                    number = self._note_numbers[id(run)] = len(self.notes)
                seen.update(str(number))
                said += f'<a href="#{NOTE_MARK}{number}"><super>{number}</super></a>'
                continue
            text = run.text
            if run.ref is not None:
                number = self.ref_numbers.get(run.ref)
                opening, closing = QUOTES[self.style.language]
                text = number if number is not None else f"{opening}{text}{closing}"
            seen.update(text)
            piece = escape(text).replace("\n", "<br/>").replace("\t", "&nbsp;" * 4)
            if run.underline:
                piece = f"<u>{piece}</u>"
            if run.italic:
                piece = f"<i>{piece}</i>"
            if run.bold:
                piece = f"<b>{piece}</b>"
            if run.link is not None:
                link = self.style.look("link")
                if link.underline:
                    piece = f"<u>{piece}</u>"
                color = f' color="#{link.color}"' if link.color and link.color != "none" else ""
                piece = f"<a href={quoteattr(run.link)}{color}>{piece}</a>"
            elif run.ref is not None:
                piece = f'<a href="#{REF_MARK}{run.ref}">{piece}</a>'
            said += piece
        return f"<u>{said}</u>" if look.underline else said

    def plain(self, value: str, pstyle: ParagraphStyle) -> str:
        self.fonts.setdefault(pstyle.fontName, set()).update(value)
        return escape(value).replace("\n", "<br/>")

    def paragraph(
        self,
        name: str,
        look: Look,
        runs: Inline,
        width: float,
        *,
        align: str | None = None,
        keep: bool = False,
        prefix: str = "",
        anchor: str = "",
    ) -> Flowable:
        """Un paragraphe dans son style ; dans un cadre si le style a un filet ou un fond."""
        framed = _framed(look)
        pstyle = self.paragraph_style(name, look, align=align, keep=keep, bare=framed)
        text = anchor + self.text(runs, pstyle, look, prefix=prefix)
        found: Flowable = Paragraph(text, pstyle)
        if framed:
            found = self.frame([found], look, width)
            found.keepWithNext = int(keep)  # type: ignore[attr-defined]
        return found

    def frame(self, content: list[Flowable], look: Look, width: float) -> Flowable:
        """Des paragraphes dans le cadre d'un style : ses filets, son fond, son retrait et ses
        espacements, comme Word les pose autour d'un paragraphe."""
        indent = (look.indent or 0) * cm
        every = _rule(look.border)
        top = _rule(look.border_top) or every
        bottom = _rule(look.border_bottom) or every
        left = _rule(look.border_left) or every
        right = every
        filled = _color(look.fill)
        vertical, horizontal = RULE_SPACE
        commands: list[tuple[Any, ...]] = [
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), vertical if top or filled else 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), vertical if bottom or filled else 0),
            ("LEFTPADDING", (0, 0), (-1, -1), horizontal if left or filled else 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), horizontal if right or filled else 0),
        ]
        for command, rule in (
            ("LINEABOVE", top),
            ("LINEBELOW", bottom),
            ("LINEBEFORE", left),
            ("LINEAFTER", right),
        ):
            if rule is not None:
                commands.append((command, (0, 0), (-1, -1), rule[0], rule[1]))
        if filled is not None:
            commands.append(("BACKGROUND", (0, 0), (-1, -1), filled))
        # Une ligne du cadre se coupe d'une page à l'autre : une note longue y tient.
        box = Table([[content]], colWidths=[width - 2 * indent], hAlign="CENTER", splitInRow=1)
        box.setStyle(TableStyle(commands))
        box.spaceBefore = look.space_before or 0
        box.spaceAfter = look.space_after or 0
        return box

    # --- titres et sommaire

    def _headings(self, blocks: Sequence[BlockOut]) -> None:
        """Le numéro de chaque titre (si le style numérote), et les entrées du sommaire
        (s'il y en a un), avec l'ancre de chaque titre repris."""
        style = self.style
        toc = any(isinstance(block, ContentsOut) for block in blocks)
        levels = style.look("toc").levels or 2
        counters = [0, 0, 0]
        rank = 0
        for block in blocks:
            if not isinstance(block, HeadingOut):
                continue
            rank += 1
            self.ranks[id(block)] = rank
            number = ""
            if style.numbering != "none":
                counters[block.level - 1] += 1
                counters[block.level :] = [0] * (3 - block.level)
                number = _heading_number(
                    style.numbering, counters[: block.level], style.labels.article
                )
                self.heading_numbers[id(block)] = number
                if rank in self.targeted:
                    self.ref_numbers[rank] = ".".join(str(n) for n in counters[: block.level])
            if toc and block.level <= levels:
                label = plain(block.runs)
                key = f"_Toc_loom_{len(self.toc) + 1}"
                self.marks[id(block)] = key
                self.toc.append((block.level, f"{number} {label}" if number else label, key))

    def heading(self, block: HeadingOut, width: float, layout: _Layout) -> Flowable:
        name, look = self.look(f"heading{block.level}", block.style, layout)
        number = self.heading_numbers.get(id(block))
        key = self.marks.get(id(block))
        rank = self.ranks.get(id(block), 0)
        target = f'<a name="{REF_MARK}{rank}"/>' if rank in self.targeted else ""
        found = self.paragraph(
            name,
            look,
            block.runs,
            width,
            align=layout.align,
            keep=True,
            prefix=f"{number} " if number else "",
            # L'ancre du lien du sommaire, et sa page notée quand le titre se dessine (aussi
            # dans des colonnes, où le document ne voit pas passer le titre).
            anchor=target
            + (f'<a name="{key}"/><onDraw name="{MARK}" label="{key}"/>' if key else ""),
        )
        return found

    def endnotes(self, width: float) -> list[Flowable]:
        """Les notes, en fin de document : sous un titre (``labels.notes``) au style des
        titres de premier niveau, chacune dans le style ``footnote``, après son numéro."""
        if not self.notes:
            return []
        style = self.style
        label = (_run(style.labels.notes),)
        found = [self.paragraph("heading1", style.look("heading1"), label, width, keep=True)]
        look = style.look("footnote")
        for number, runs in enumerate(self.notes, start=1):
            anchor = f'<a name="{NOTE_MARK}{number}"/><super>{number}</super> '
            found.append(self.paragraph("footnote", look, runs, width, anchor=anchor))
        return found

    def contents(self, width: float, pages: Mapping[str, int]) -> list[Flowable]:
        style = self.style
        toc = style.look("toc")
        heading = self.paragraph("toc", toc, (_run(style.labels.contents),), width, keep=True)
        text = style.look("text")
        levels: list[ParagraphStyle] = []
        for level in range(3):
            entry = replace(
                text,
                bold=True if level == 0 else text.bold,
                space_before=3.0 if level == 0 else 0.0,
                space_after=2.0,
                align="left",
                indent=None,
            )
            pstyle = self.paragraph_style(f"toc{level + 1}", entry)
            levels.append(
                ParagraphStyle(
                    f"{pstyle.name}-entry",
                    parent=pstyle,
                    leftIndent=TOC_INDENT * level,
                    rightIndent=0,
                )
            )
        for _, label, _ in self.toc:
            self.fonts.setdefault(levels[0].fontName, set()).update(label)
        numbered = toc.page_numbers is not False
        return [heading, _Contents(self.toc, pages, levels, numbered)]

    # --- les blocs

    def caption(self, kind: str, runs: Inline, width: float, layout: _Layout) -> Flowable:
        """La légende ; numérotée (« Figure 2 — ») si le style ``caption`` le veut."""
        name, look = self.look("caption", None, layout)
        prefix = ""
        if self.style.look("caption").numbered:
            number = self._numbers[kind] = self._numbers.get(kind, 0) + 1
            prefix = f"{kind} {number} \u2014 "
        return self.paragraph(name, look, runs, width, prefix=prefix)

    def after(self, look: Look) -> Flowable:
        """L'espace sous un tableau ou une image sans légende : le paragraphe vide qui les
        suit dans Word, une ligne de texte et son espace après."""
        size = look.size or 11.0
        return Spacer(1, size * LEADING * (look.line_spacing or 1.0) + AFTER_TABLE)

    def blocks(
        self, blocks: Sequence[BlockOut], width: float, layout: _Layout = PLAIN
    ) -> list[Flowable]:
        style = self.style
        found: list[Flowable] = []
        index = 0
        while index < len(blocks):
            block = blocks[index]
            index += 1
            if (
                style.image_grid
                and isinstance(block, ImageOut)
                and block.wanted == "half"
                and index < len(blocks)
                and isinstance(second := blocks[index], ImageOut)
                and second.wanted == "half"
            ):
                index += 1
                found.append(self.image_pair(block, second, width, layout))
                continue
            match block:
                case TitleOut(runs=runs):
                    look = style.look("document_title")
                    found.append(
                        self.paragraph("document_title", look, runs, width, align=layout.align)
                    )
                case HeadingOut():
                    found.append(self.heading(block, width, layout))
                case ParagraphOut(runs=runs):
                    name, look = self.look("paragraph", block.style, layout)
                    found.append(self.paragraph(name, look, runs, width, align=layout.align))
                case ListOut(checkboxes=True):
                    found.append(self.checklist(block, width, layout))
                case ListOut():
                    found.append(self.list(block, width, layout))
                case TableOut():
                    found.extend(self.table(block, width, layout))
                case NoteOut(title=title, runs=runs):
                    name, look = self.look("note", block.style, layout)
                    head = (_run(title, bold=True), _run("\n")) if title else ()
                    found.append(self.paragraph(name, look, (*head, *runs), width))
                case QuoteOut():
                    found.append(self.quote(block, width, layout))
                case ChartOut():
                    found.append(self.chart(block, width, layout))
                case ImageOut():
                    found.append(self.image(block, width, layout))
                case SignatureOut():
                    found.append(self.signature(block, width))
                case ContentsOut():
                    found.extend(self.contents(width, self.pages))
                case FieldsOut():
                    found.extend(self.fields(block, width))
                case CodeOut():
                    found.append(self.code(block, width))
        return found

    def list(self, block: ListOut, width: float, layout: _Layout) -> Flowable:
        """Une liste et ses sous-listes : puce ou numéro à chaque niveau (1., a), i.)."""
        del width
        name, look = self.look("list", block.style, layout)
        pstyle = self.paragraph_style(name, look, align=layout.align)
        font = self.typefaces.name(look.font)

        def level(entries: Sequence[Entry], depth: int) -> ListFlowable:
            items: list[Flowable | ListItem] = []
            for entry in entries:
                paragraph = Paragraph(self.text(entry.runs, pstyle, look), pstyle)
                if depth == 0 or entry.children:
                    content: list[Flowable] = [paragraph]
                    if entry.children:
                        content.append(level(entry.children, depth + 1))
                    items.append(ListItem(content))
                else:
                    items.append(paragraph)
            bullet = BULLETS[depth]
            size = pstyle.fontSize
            if not block.ordered and not _covers(font, bullet):
                bullet, size = BULLETS[0], size * SMALL_BULLET
            kind, form = NUMBERS[depth]
            return ListFlowable(
                cast(list[Flowable], items),
                bulletType=kind if block.ordered else "bullet",
                start=None if block.ordered else bullet,
                bulletFormat=form if block.ordered else None,
                leftIndent=LIST_INDENT,
                bulletDedent=LIST_HANG,
                bulletFontName=font,
                bulletFontSize=size,
                bulletColor=pstyle.textColor,
            )

        found = level(block.entries, 0)
        # Après la liste, l'espace d'un paragraphe : celui du texte.
        found.spaceAfter = self.style.look("text").space_after or 0
        return found

    def checklist(self, block: ListOut, width: float, layout: _Layout) -> Flowable:
        """Une liste à cocher : une case dessinée devant chaque élément, à la place d'une
        puce, et chaque sous-élément en retrait."""
        name, look = self.look("list", block.style, layout)
        pstyle = self.paragraph_style(name, look, align=layout.align, bare=True)
        color = pstyle.textColor
        rows: list[list[Any]] = []
        commands: list[tuple[Any, ...]] = [
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), look.space_after or 0),
        ]
        size = pstyle.fontSize
        for entry in block.entries:
            commands.append(("SPAN", (2, len(rows)), (3, len(rows))))
            rows.append(
                [
                    "",
                    _Check(entry.checked, size, color),
                    Paragraph(self.text(entry.runs, pstyle, look), pstyle),
                    "",
                ]
            )
            for child in entry.children:
                rows.append(
                    [
                        "",
                        "",
                        _Check(child.checked, size, color),
                        Paragraph(self.text(child.runs, pstyle, look), pstyle),
                    ]
                )
        table = Table(
            rows, colWidths=[LIST_HANG, LIST_HANG, LIST_HANG, width - 3 * LIST_HANG], hAlign="LEFT"
        )
        table.setStyle(TableStyle(commands))
        return table

    def quote(self, block: QuoteOut, width: float, layout: _Layout) -> Flowable:
        """La citation et sa source, dans le cadre de la citation (un seul filet)."""
        name, look = self.look("quote", block.style, layout)
        pstyle = self.paragraph_style(name, look, bare=_framed(look))
        content: list[Flowable] = [Paragraph(self.text(block.runs, pstyle, look), pstyle)]
        if block.source:
            source = self.style.look("quote_source")
            sstyle = self.paragraph_style("quote_source", source, bare=_framed(look))
            if _framed(look):
                sstyle = ParagraphStyle(
                    f"{sstyle.name}-in", parent=sstyle, spaceBefore=look.space_after or 0
                )
            content.append(Paragraph(self.plain(f"\u2014 {block.source}", sstyle), sstyle))
        if not _framed(look):
            return KeepTogether(content)
        box = self.frame(content, look, width)
        if block.source:
            box.spaceAfter = self.style.look("quote_source").space_after or 0
        return box

    def table(self, block: TableOut, width: float, layout: _Layout) -> list[Flowable]:
        style = self.style
        name, tables = self.look("table", block.style, layout)
        header = _merged(tables, style.between("table_header", "table"))
        total = _merged(tables, style.between("table_total", "table"))
        # « content » : un tableau étroit (la moitié de la page au plus, un récapitulatif)
        # prend sa largeur ; un plus large garde toute la largeur.
        text_width = style.text_width * TWIP
        narrow = block.natural * text_width
        full_width = tables.width != "content" or narrow > text_width / 2
        full = width if full_width else min(width, narrow)
        widths = [part * full for part in block.widths]

        def cell(text: str | Inline, look: Look, kind: str, align: str) -> Paragraph:
            if align == "justify":
                align = "left"
            pstyle = self.paragraph_style(f"{name}-{kind}", look, align=align, bare=True)
            if isinstance(text, str):
                return Paragraph(self.plain(text, pstyle), pstyle)
            return Paragraph(self.text(text, pstyle, look), pstyle)

        def heading_align(index: int) -> str:
            return header.align or block.align[index]

        data: list[list[Paragraph | str]] = []
        if block.headers:
            data.append(
                [
                    cell(text, header, "header", heading_align(i))
                    for i, text in enumerate(block.headers)
                ]
            )
        # Les lignes de données à fond alterné, et les lignes de groupe, de sous-total ou en
        # gras, par leur place dans le tableau.
        banded: list[int] = []
        marked: list[tuple[str, int]] = []
        looks = {
            "group": _merged(tables, style.between("table_group", "table")),
            "subtotal": _merged(tables, style.between("table_subtotal", "table")),
            "total": total,
        }
        band = 0
        for kind, cells in zip(
            block.kinds or ("body",) * len(block.cells), block.cells, strict=True
        ):
            if kind == "group":
                data.append([cell(cells[0], looks[kind], kind, "left"), *[""] * (len(widths) - 1)])
                marked.append((kind, len(data) - 1))
                band = 0
                continue
            if kind == "body":
                if band % 2 == 1:
                    banded.append(len(data))
                band += 1
                data.append(
                    [cell(text, tables, "body", block.align[i]) for i, text in enumerate(cells)]
                )
                continue
            line = looks[kind]
            data.append(
                [
                    cell(plain(text), line, kind, line.align or block.align[i])
                    for i, text in enumerate(cells)
                ]
            )
            marked.append((kind, len(data) - 1))
        if block.total is not None:
            data.append(
                [
                    cell(text, total, "total", total.align or block.align[i])
                    for i, text in enumerate(block.total)
                ]
            )
        vertical, horizontal = CELL_PADDING
        commands: list[tuple[Any, ...]] = [
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), vertical),
            ("BOTTOMPADDING", (0, 0), (-1, -1), vertical),
            ("LEFTPADDING", (0, 0), (-1, -1), horizontal),
            ("RIGHTPADDING", (0, 0), (-1, -1), horizontal),
        ]
        commands += _table_lines(tables)
        if block.headers and (fill := _color(header.fill)) is not None:
            commands.append(("BACKGROUND", (0, 0), (-1, 0), fill))
        if (bands := _color(tables.bands)) is not None:
            for row in banded:
                commands.append(("BACKGROUND", (0, row), (-1, row), bands))
        if block.total is not None:
            if (fill := _color(total.fill)) is not None:
                commands.append(("BACKGROUND", (0, -1), (-1, -1), fill))
            if (rule := _rule(total.border_top)) is not None:
                commands.append(("LINEABOVE", (0, -1), (-1, -1), rule[0], rule[1]))
        for kind, row in marked:
            line = looks[kind]
            if kind == "group":
                commands.append(("SPAN", (0, row), (-1, row)))
            if (fill := _color(line.fill)) is not None:
                commands.append(("BACKGROUND", (0, row), (-1, row), fill))
            if kind != "group" and (rule := _rule(line.border_top)) is not None:
                commands.append(("LINEABOVE", (0, row), (-1, row), rule[0], rule[1]))
        position = tables.position or "left"
        # Une ligne d'émargement : sa hauteur, pour signer ; l'en-tête, la sienne.
        tall = tables.row_height * cm if tables.row_height else None
        found = Table(
            data,
            colWidths=widths,
            rowHeights=[
                None if index < len(data) - len(block.cells) else tall for index in range(len(data))
            ]
            if tall
            else None,
            repeatRows=1 if block.headers else 0,
            hAlign=cast(Any, position.upper()),
        )
        found.setStyle(TableStyle(commands))
        found.spaceBefore = tables.space_before or 0
        said: list[Flowable] = [found]
        if block.caption:
            said.append(self.caption(style.labels.table, block.caption, width, layout))
        else:
            said.append(self.after(style.look("text")))
        return said

    def fields(self, block: FieldsOut, width: float) -> list[Flowable]:
        """Des libellés et leurs valeurs, en deux colonnes sans filet (le libellé large de
        ``label_width``) ; une valeur vide est une ligne à remplir, au trait des tableaux."""
        style = self.style
        look = style.look("fields")
        label_look = style.look("fields_label")
        vstyle = self.paragraph_style("fields", look, bare=True)
        lstyle = self.paragraph_style("fields_label", label_look, bare=True)
        label = min((look.label_width or 4) * cm, width / 2)
        commands: list[tuple[Any, ...]] = [
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), CELL_GAP / 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), look.space_after or 0),
        ]
        line = _rule(style.look("table").line)
        rows: list[list[Flowable]] = []
        for index, (name, value) in enumerate(block.items):
            # Une valeur vide garde la hauteur d'une ligne : la ligne à remplir.
            shown = self.text(value, vstyle, look) or "\u00a0"
            rows.append([Paragraph(self.plain(name, lstyle), lstyle), Paragraph(shown, vstyle)])
            if not value and line is not None:
                commands.append(("LINEBELOW", (1, index), (1, index), line[0], line[1]))
        table = Table(rows, colWidths=[label, width - label], hAlign="LEFT")
        table.setStyle(TableStyle(commands))
        return [table, self.after(style.look("text"))]

    def code(self, block: CodeOut, width: float) -> Flowable:
        """Du code : ses lignes telles quelles (les espaces gardées, une ligne trop longue
        reprise à la suivante), dans le style ``code`` et son cadre (un fond)."""
        look = self.style.look("code")
        framed = _framed(look)
        pstyle = self.paragraph_style("code", look, bare=framed)
        self.fonts.setdefault(pstyle.fontName, set()).update(block.text)
        lines = (_spaced(line) for line in block.text.split("\n"))
        found: Flowable = Paragraph("<br/>".join(lines), pstyle)
        if framed:
            found = self.frame([found], look, width)
        return found

    def image_flowable(self, block: ImageOut, width: float) -> Flowable:
        """L'image, à la taille décidée pour les deux formats, réduite à la place qu'elle a."""
        wide = block.width / 100 * mm
        tall = block.height / 100 * mm
        if wide > width:
            tall = tall * width / wide
            wide = width
        # Une image plus haute que la page (une photo en portrait) : à la hauteur de la page,
        # avec la place de sa légende.
        room = self.max_height * IMAGE_HEIGHT
        if tall > room:
            wide = wide * room / tall
            tall = room
        picture = Image(io.BytesIO(block.picture.data), width=wide, height=tall)
        picture.hAlign = "CENTER"
        return picture

    def image(self, block: ImageOut, width: float, layout: _Layout) -> Flowable:
        parts: list[Flowable] = [self.image_flowable(block, width)]
        if block.caption:
            parts.append(self.caption(self.style.labels.figure, block.caption, width, layout))
        else:
            parts.append(self.after(self.style.look("text")))
        return KeepTogether(parts)

    def image_pair(
        self, first: ImageOut, second: ImageOut, width: float, layout: _Layout
    ) -> Flowable:
        """Deux images ``half`` côte à côte (``image.grid``), chacune avec sa légende."""
        # La même place pour les deux : l'écart entre elles est pris sur la première cellule.
        half = width / 2
        room = half - CELL_GAP
        cells: list[list[Flowable]] = []
        for block in (first, second):
            content: list[Flowable] = [self.image_flowable(block, room)]
            if block.caption:
                content.append(self.caption(self.style.labels.figure, block.caption, room, layout))
            cells.append(content)
        table = Table([cells], colWidths=[half, half])
        table.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (0, -1), CELL_GAP),
                    ("RIGHTPADDING", (1, 0), (1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ]
            )
        )
        return table

    def chart(self, block: ChartOut, width: float, layout: _Layout) -> Flowable:
        """Le titre, le dessin et la légende du graphique, sur une même page."""
        style = self.style
        text = style.look("text")
        font = self.typefaces.name(text.font)
        parts: list[Flowable] = []
        if block.title:
            look = replace(
                text,
                bold=True,
                color=style.chart.title or text.color,
                align="center",
                space_before=6.0,
                space_after=4.0,
                indent=None,
            )
            parts.append(self.paragraph("chart_title", look, (_run(block.title),), width))
        self.fonts.setdefault(font, set()).update(
            "".join(block.categories) + "".join(name for name, _ in block.series)
        )
        parts.append(chart.drawing(block, width, style.chart, font, style.language))
        if block.caption:
            parts.append(self.caption(style.labels.figure, block.caption, width, layout))
        else:
            parts.append(self.after(text))
        return KeepTogether(parts)

    def signature(self, block: SignatureOut, width: float) -> Flowable:
        """La zone de signature : une colonne par signataire, sans cadre."""
        style = self.style
        count = len(block.signers)
        widths = [width / count] * count
        text = style.look("text")
        signer = style.look("signature")
        mention = style.look("signature_mention")
        date = self.paragraph_style("signature-date", replace(text, space_before=0, space_after=3))
        sign = self.paragraph_style("signature-sign", replace(text, space_before=0, space_after=0))
        cells: list[list[Flowable]] = []
        for name in block.signers:
            content: list[Flowable] = [
                self.paragraph("signature", signer, (_run(name),), width / count, keep=True)
            ]
            if block.mention:
                content.append(
                    self.paragraph(
                        "signature_mention", mention, (_run(block.mention),), width / count
                    )
                )
            content.append(Paragraph(self.plain(style.labels.date, date), date))
            content.append(Paragraph(self.plain(style.labels.signature, sign), sign))
            content.append(Spacer(1, SIGN_SPACE))
            cells.append(content)
        table = Table([cells], colWidths=widths, hAlign="LEFT")
        commands: list[tuple[Any, ...]] = [
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), CELL_GAP),
            ("RIGHTPADDING", (-1, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]
        table.setStyle(TableStyle(commands))
        return KeepTogether([table])

    # --- sections

    def box(self, layout: _Layout) -> tuple[Any, Any]:
        """Le cadre d'une section encadrée : son filet et son fond (``box``, ou celui que la
        section choisit)."""
        look = self.style.look(layout.styles.get("box", "box"))
        return _rule(look.border), _color(look.fill)

    def layout_table(
        self,
        cells: Sequence[list[Flowable]],
        widths: Sequence[float],
        boxed: Mapping[int, _Layout],
    ) -> Flowable:
        """Des contenus côte à côte, ou un seul, encadré : un tableau sans bordure."""
        commands: list[tuple[Any, ...]] = [
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]
        for index in range(len(cells) - 1):
            if index not in boxed:
                commands.append(("RIGHTPADDING", (index, 0), (index, 0), CELL_GAP))
        for index, layout in boxed.items():
            rule, fill = self.box(layout)
            spot = (index, 0)
            commands += [
                ("LEFTPADDING", spot, spot, BOX_PAD),
                ("RIGHTPADDING", spot, spot, BOX_PAD),
                ("TOPPADDING", spot, spot, BOX_PAD),
                ("BOTTOMPADDING", spot, spot, BOX_PAD / 2),
            ]
            if rule is not None:
                commands.append(("BOX", spot, spot, rule[0], rule[1]))
            if fill is not None:
                commands.append(("BACKGROUND", spot, spot, fill))
        # Une section longue continue sur la page suivante, comme dans Word.
        table = Table([list(cells)], colWidths=list(widths), hAlign="LEFT", splitInRow=1)
        table.setStyle(TableStyle(commands))
        return table

    def units(self, groups: Sequence[_Group], widths: Callable[[bool], float]) -> list[_Unit]:
        """Les groupes, posés : seuls, ou côte à côte quand ils sont sur la même ligne du
        modèle (une section ``beside`` absente laisse l'autre seule, en pleine largeur)."""
        units: list[_Unit] = []
        index = 0
        while index < len(groups):
            group = groups[index]
            together = [group]
            while (
                group.row is not None
                and index + len(together) < len(groups)
                and groups[index + len(together)].row == group.row
            ):
                together.append(groups[index + len(together)])
            index += len(together)
            landscape = self.style.landscape or group.layout.landscape
            if len(together) > 1:
                units.append(self.beside(together, widths(landscape), landscape))
            else:
                units.append(self.alone(group, widths(landscape), landscape))
        return units

    def alone(self, group: _Group, width: float, landscape: bool) -> _Unit:
        layout = group.layout
        space = COVER_SPACE if layout.cover and not layout.space_before else layout.space_before
        said: list[Flowable] = [Spacer(1, space)] if space else []
        if layout.cover:
            layout = replace(layout, align="center")
        columns = layout.columns
        room = (width - (columns - 1) * COLUMN_GAP) / columns
        if layout.box:
            content = self.blocks(group.blocks, room - 2 * BOX_PAD, layout)
            body: list[Flowable] = [self.layout_table([content], [room], {0: layout})]
        else:
            body = self.blocks(group.blocks, room, layout)
        if columns > 1:
            said.append(BalancedColumns(body, nCols=columns, innerPadding=COLUMN_GAP, endSlack=0.1))
        else:
            said += body
        return _Unit(said, landscape, layout.cover, layout.page_break)

    def beside(self, together: Sequence[_Group], width: float, landscape: bool) -> _Unit:
        """Des sections côte à côte : une cellule chacune, à la part que dit ``width`` (des
        parts égales sans elle)."""
        given = [group.layout.width for group in together]
        shares = [float(share or 0) for share in given] if all(given) else [1.0] * len(together)
        widths = [width * share / sum(shares) for share in shares]
        cells: list[list[Flowable]] = []
        boxed: dict[int, _Layout] = {}
        for position, (group, cell) in enumerate(zip(together, widths, strict=True)):
            last = position == len(together) - 1
            framed = group.layout.box
            if framed:
                boxed[position] = group.layout
            room = cell - (2 * BOX_PAD if framed else 0 if last else CELL_GAP)
            cells.append(self.blocks(group.blocks, room, group.layout))
        first = together[0].layout
        said: list[Flowable] = [Spacer(1, first.space_before)] if first.space_before else []
        said.append(self.layout_table(cells, widths, boxed))
        said.append(self.after(self.style.look("text")))
        return _Unit(said, landscape, page_break=first.page_break)


def _table_lines(tables: Look) -> list[tuple[Any, ...]]:
    """Les filets d'un tableau : ``borders`` (grille, horizontales, aucun) au trait
    ``line``, puis ``border_top`` et ``border_bottom`` s'ils sont dits."""
    commands: list[tuple[Any, ...]] = []
    line = _rule(tables.line) if tables.borders != "none" else None
    borders = tables.borders or "grid"
    if line is not None:
        if borders == "grid":
            commands.append(("GRID", (0, 0), (-1, -1), line[0], line[1]))
        elif borders == "horizontal":
            commands.append(("LINEABOVE", (0, 0), (-1, -1), line[0], line[1]))
            commands.append(("LINEBELOW", (0, -1), (-1, -1), line[0], line[1]))
    for command, value, row in (
        ("LINEABOVE", tables.border_top, 0),
        ("LINEBELOW", tables.border_bottom, -1),
    ):
        rule = _rule(value)
        if rule is not None:
            commands.append((command, (0, row), (-1, row), rule[0], rule[1]))
    return commands


def _spaced(line: str) -> str:
    """Une ligne de code, ses espaces gardées : celles du début, et celles d'une série (la
    dernière reste une espace, où la ligne peut se couper) ; une tabulation, quatre."""
    said = escape(line.replace("\t", "    "))
    return _SPACES.sub(lambda found: "&nbsp;" * len(found.group()), said)


_SPACES: Final = re.compile(r"^ +| (?= )")


def _heading_number(numbering: str, counters: Sequence[int], article: str) -> str:
    """Le numéro d'un titre, comme Word l'écrit : « 2. », « 2.1 », « Article 2 » et un
    tiret."""
    if len(counters) == 1:
        return f"{article} {counters[0]} \u2013" if numbering == "article" else f"{counters[0]}."
    return ".".join(str(number) for number in counters)


def _run(text: str, *, bold: bool = False) -> Run:
    return Run(text, bold=bold)


def _title_runs(prepared: Prepared) -> Inline:
    for block in prepared.blocks:
        if isinstance(block, TitleOut):
            return block.runs
    return ()


# --- Les pages ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Zone:
    """Une ligne d'en-tête ou de pied de page déjà mesurée : sa hauteur, et de quoi la
    dessiner."""

    height: float
    draw: Callable[[Canvas, float, float, float, int, int | None], None]


@dataclass(frozen=True, slots=True)
class _Box:
    """Les lignes d'un en-tête ou d'un pied dans le cadre de son style, comme Word groupe
    les paragraphes qui se suivent dans un même style : l'espace avant le premier, puis
    les filets et le fond autour du groupe (à l'écart du texte de ``RULE_SPACE``), entre
    deux lignes l'espace après l'une et avant l'autre, l'espace après la dernière ; le
    retrait à gauche et à droite."""

    zones: list[_Zone]
    look: Look

    def _pads(self) -> tuple[float, float, float, float]:
        """L'écart du texte au cadre : en haut, en bas, à gauche, à droite."""
        look = self.look
        every = look.border
        filled = _color(look.fill) is not None
        vertical, horizontal = RULE_SPACE
        return (
            vertical if look.border_top or every or filled else 0.0,
            vertical if look.border_bottom or every or filled else 0.0,
            horizontal if look.border_left or every or filled else 0.0,
            horizontal if every or filled else 0.0,
        )

    @property
    def height(self) -> float:
        if not self.zones:
            return 0.0
        look = self.look
        before, after = look.space_before or 0.0, look.space_after or 0.0
        top, bottom, _, _ = self._pads()
        inner = (len(self.zones) - 1) * (before + after)
        return before + top + sum(z.height for z in self.zones) + inner + bottom + after

    def draw(
        self, canvas: Canvas, top: float, left: float, right: float, page: int, total: int | None
    ) -> None:
        if not self.zones:
            return
        look = self.look
        before, after = look.space_before or 0.0, look.space_after or 0.0
        indent = (look.indent or 0) * cm
        left, right = left + indent, right - indent
        pad_top, _, pad_left, pad_right = self._pads()
        upper = top - before
        lower = top - self.height + after
        filled = _color(look.fill)
        if filled is not None:
            canvas.setFillColor(filled)
            canvas.rect(left, lower, right - left, upper - lower, stroke=0, fill=1)
        every = _rule(look.border)
        for rule, line in (
            (_rule(look.border_top) or every, (left, upper, right, upper)),
            (_rule(look.border_bottom) or every, (left, lower, right, lower)),
            (_rule(look.border_left) or every, (left, lower, left, upper)),
            (every, (right, lower, right, upper)),
        ):
            if rule is not None:
                canvas.setStrokeColor(rule[1])
                canvas.setLineWidth(rule[0])
                canvas.line(*line)
        y = upper - pad_top
        for index, zone in enumerate(self.zones):
            if index:
                y -= after + before
            zone.draw(canvas, y, left + pad_left, right - pad_right, page, total)
            y -= zone.height


class _Bands:
    """L'en-tête, le pied de page et le filigrane, dessinés à chaque page : à 1 cm du bord,
    comme Word ; trois zones (gauche, centre, droite) et des lignes centrées — sous les
    zones dans un en-tête, au-dessus dans un pied de page."""

    def __init__(self, style: Style, title: str, fonts: _Fonts) -> None:
        self.style = style
        self.title = title
        self.header = style.header
        self.footer = style.footer
        self.fonts = fonts

    def _font(self, look: Look) -> str:
        return self.fonts.name(look.font, bool(look.bold), bool(look.italic))

    def _said(self, text: str, page: int, total: int | None, look: Look) -> str:
        def value(found: re.Match[str]) -> str:
            name = found.group(1)
            if name == "page":
                return str(page)
            if name == "pages":
                return str(total) if total is not None else "0"
            return self.title

        said = _PLACEHOLDER.sub(value, text)
        return said.upper() if look.uppercase else said

    def lines(self, band: Band | None, look: Look, *, header: bool) -> list[_Zone]:
        """Les lignes d'un en-tête ou d'un pied, du haut vers le bas."""
        if band is None:
            return []
        size = look.size or 11.0
        leading = size * LEADING * (look.line_spacing or 1.0)
        font = self._font(look)
        color = _color(look.color) or colors.black
        # Comme Word : les lignes d'un en-tête sont centrées ; celles d'un pied, et son
        # texte du centre quand il est seul, suivent l'alignement de son style.
        own = "left" if look.align in (None, "justify") else look.align
        aligned = "center" if header else own

        def write(canvas: Canvas, x: float, baseline: float, text: str) -> None:
            canvas.setFont(font, size)
            canvas.setFillColor(color)
            canvas.drawString(x, baseline, text)
            if look.underline:
                below = baseline + size * UNDERLINE_OFFSET
                canvas.setStrokeColor(color)
                canvas.setLineWidth(size * UNDERLINE_WIDTH)
                canvas.line(x, below, x + pdfmetrics.stringWidth(text, font, size), below)

        zones: list[_Zone] = []
        logo = band.logo
        picture: Any = None
        logo_width = logo_height = 0.0
        if logo is not None:
            picture = ImageReader(io.BytesIO(logo.data))
            pixels = picture.getSize()
            logo_width = logo.width * cm
            logo_height = logo_width * pixels[1] / pixels[0]
        texts = {"left": band.left, "center": band.center, "right": band.right}
        alone = (
            not texts["left"] and not texts["right"] and (logo is None or logo.position == "center")
        )
        if any(texts.values()) or logo is not None:
            height = max(leading, logo_height)

            def zone(
                canvas: Canvas, top: float, left: float, right: float, page: int, total: int | None
            ) -> None:
                bottom = top - height
                baseline = bottom + size * 0.22
                for place in ("left", "center", "right"):
                    text = self._said(texts[place], page, total, look)
                    has_logo = logo is not None and logo.position == place
                    text_width = pdfmetrics.stringWidth(text, font, size) if text else 0.0
                    gap = 4.0 if has_logo and text else 0.0
                    group = (logo_width if has_logo else 0.0) + gap + text_width
                    spot = aligned if place == "center" and alone else place
                    x = (
                        left
                        if spot == "left"
                        else right - group
                        if spot == "right"
                        else (left + right - group) / 2
                    )
                    if has_logo:
                        drawn: Any = canvas
                        drawn.drawImage(picture, x, bottom, logo_width, logo_height, mask="auto")
                        x += logo_width + gap
                    if text:
                        write(canvas, x, baseline, text)

            zones.append(_Zone(height, zone))
        rows: list[_Zone] = []
        for line in band.lines:

            def centered(
                canvas: Canvas,
                top: float,
                left: float,
                right: float,
                page: int,
                total: int | None,
                line: str = line,
            ) -> None:
                text = self._said(line, page, total, look)
                width = pdfmetrics.stringWidth(text, font, size)
                x = (
                    left
                    if aligned == "left"
                    else right - width
                    if aligned == "right"
                    else (left + right - width) / 2
                )
                write(canvas, x, top - leading + size * 0.22, text)

            rows.append(_Zone(leading, centered))
        return zones + rows if header else rows + zones

    def box(self, band: Band | None, look: Look, *, header: bool) -> _Box:
        """Les lignes d'un en-tête ou d'un pied dans le cadre de son style."""
        return _Box(self.lines(band, look, header=header), look)

    def heights(self) -> tuple[float, float]:
        """La place que prennent l'en-tête et le pied, depuis le bord de la page."""
        header = self.box(self.header, self.style.look("header"), header=True).height
        footer = self.box(self.footer, self.style.look("footer"), header=False).height
        return (
            BAND_DISTANCE + header + BAND_GAP if header else 0.0,
            BAND_DISTANCE + footer + BAND_GAP if footer else 0.0,
        )

    def draw(
        self,
        canvas: Canvas,
        size: tuple[float, float],
        page: int,
        total: int | None,
        *,
        header: bool,
        footer: bool,
    ) -> None:
        style = self.style
        width, height = size
        top, bottom, left, right = (value * TWIP for value in style.margins)
        del top, bottom
        canvas.saveState()
        if header:
            self._watermark(canvas, size)
            box = self.box(self.header, style.look("header"), header=True)
            box.draw(canvas, height - BAND_DISTANCE, left, width - right, page, total)
        if footer:
            box = self.box(self.footer, style.look("footer"), header=False)
            box.draw(canvas, BAND_DISTANCE + box.height, left, width - right, page, total)
        canvas.restoreState()

    def _watermark(self, canvas: Canvas, size: tuple[float, float]) -> None:
        """Le filigrane : un texte en diagonale, derrière le texte de la page, dans la police
        et la couleur du style ``watermark``, à demi transparent."""
        look = self.style.look("watermark")
        text = look.text
        if not text:
            return
        font = self._font(look)
        points = min(
            WATERMARK_HEIGHT * 0.75, WATERMARK_WIDTH / max(pdfmetrics.stringWidth(text, font, 1), 1)
        )
        canvas.saveState()
        canvas.setFillColor(_color(look.color) or colors.lightgrey)
        canvas.setFillAlpha(WATERMARK_ALPHA)
        canvas.translate(size[0] / 2, size[1] / 2)
        canvas.rotate(45)
        canvas.setFont(font, points)
        canvas.drawCentredString(0, -points / 3, text)
        canvas.restoreState()


def _build(
    prepared: Prepared, style: Style, template: Template | None, known: _Pass
) -> tuple[bytes, _Pass, _Body]:
    body = _Body(prepared, style, template, known.pages)
    bands = _Bands(style, body.title, body.typefaces)
    portrait = style.page
    width, height = (portrait[1], portrait[0]) if style.landscape else portrait
    sizes = {
        "portrait": (width * TWIP, height * TWIP),
        "landscape": (max(width, height) * TWIP, min(width, height) * TWIP),
        "cover": (width * TWIP, height * TWIP),
    }
    top, bottom, left, right = (value * TWIP for value in style.margins)
    above, below = bands.heights()

    def text_width(landscape: bool) -> float:
        return sizes["landscape" if landscape else "portrait"][0] - left - right

    body.max_height = min(
        size[1] - max(top, above) - max(bottom, below)
        for name, size in sizes.items()
        if name != "cover"
    )
    units = body.units(_groups(prepared, template), text_width)
    # La première page sans en-tête ni pied : page de garde, ou ``first_page: false``.
    first = "portrait"
    if units:
        first = "cover" if units[0].cover else "landscape" if units[0].landscape else "portrait"
    header_first = first != "cover" and (bands.header is None or bands.header.first_page)
    footer_first = first != "cover" and (bands.footer is None or bands.footer.first_page)

    found: dict[str, int] = {}

    def mark(canvas: Canvas, kind: str, label: str) -> None:
        del kind
        found.setdefault(label, canvas.getPageNumber())

    def on_page(cover: bool) -> Callable[[Canvas, Any], None]:
        def draw(canvas: Canvas, document: Any) -> None:
            _on_draw(canvas, MARK, mark)
            page = canvas.getPageNumber()
            bands.draw(
                canvas,
                (float(document.pagesize[0]), float(document.pagesize[1])),
                page,
                known.total,
                header=not cover and (header_first if page == 1 else True),
                footer=not cover and (footer_first if page == 1 else True),
            )

        return draw

    templates: dict[str, PageTemplate] = {}
    for name, page in sizes.items():
        roof = top if name == "cover" else max(top, above)
        floor = bottom if name == "cover" else max(bottom, below)
        frame = Frame(
            left,
            floor,
            page[0] - left - right,
            page[1] - roof - floor,
            leftPadding=0,
            rightPadding=0,
            topPadding=0,
            bottomPadding=0,
            id=name,
        )
        templates[name] = PageTemplate(
            id=name, frames=[frame], onPage=on_page(name == "cover"), pagesize=page
        )
    story: list[Flowable] = []
    current: str | None = None
    for unit in units:
        wanted = "cover" if unit.cover else "landscape" if unit.landscape else "portrait"
        if current is None:
            current = wanted
        elif wanted != current or current == "cover":
            story.append(NextPageTemplate(wanted))
            story.append(PageBreak())
            current = wanted
        elif unit.page_break:
            story.append(PageBreak())
        story += unit.flowables
    # Les notes, après la dernière section : sur une page à elles après une page de garde.
    if body.notes:
        if current == "cover":
            story += [NextPageTemplate("portrait"), PageBreak()]
            current = "portrait"
        story += body.endnotes(text_width(current == "landscape"))
    buffer = io.BytesIO()
    document = BaseDocTemplate(
        buffer,
        pagesize=sizes[first],
        pageTemplates=[templates[first], *(t for n, t in templates.items() if n != first)],
        title=prepared.title or "",
        author="loom-ia",
        subject=prepared.title or "",
        creator="loom-ia",
        lang="fr-FR" if style.language == "fr" else "en-US",
        invariant=1,
    )
    document.build(story or [Spacer(1, 0)])
    return buffer.getvalue(), _Pass(document.page, found), body


def missing_glyphs(body: _Body) -> list[str]:
    """Les caractères qu'une police du document n'a pas : ils s'imprimeraient en carré
    noir."""
    missing: set[str] = set()
    for font, chars in body.fonts.items():
        for char in chars - {"\n", "\t", " "}:
            if not _covers(font, char):
                missing.add(char)
    return sorted(missing)


def render(
    prepared: Prepared, style: Style | None = None, template: Template | None = None
) -> tuple[bytes, int, list[str]]:
    """Le PDF, son nombre de pages, et les avertissements du rendu. ``style`` : le style du
    document (le style maison sans lui) ; ``template`` : le modèle qu'il suit."""
    style = style or house()
    known = _Pass()
    try:
        data, found, body = _build(prepared, style, template, known)
        # Le rendu suivant écrit ce que le précédent a appris : « Page n / N », les pages du
        # sommaire. Trois au plus : un sommaire qui change de page se stabilise au suivant.
        for _ in range(3):
            if found.total == known.total and found.pages == known.pages:
                break
            known = found
            data, found, body = _build(prepared, style, template, known)
    except LayoutError:
        raise ToolError(
            "Document refusé : un bloc ne tient pas dans une page (un tableau à une ligne "
            "très haute, une section côte à côte trop longue) ; le découper"
        ) from None
    warnings: list[str] = []
    missing = missing_glyphs(body)
    if missing:
        shown = " ".join(missing[:MAX_MISSING_SAID]) + (
            " …" if len(missing) > MAX_MISSING_SAID else ""
        )
        warnings.append(
            f"{len(missing)} caractère(s) absent(s) des polices du PDF, imprimé(s) en carré "
            f"noir : {shown} — les remplacer par du texte"
        )
    return data, found.total or 0, warnings
