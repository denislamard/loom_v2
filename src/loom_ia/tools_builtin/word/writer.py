# SPDX-License-Identifier: Apache-2.0
"""Écrire un .docx : le document validé posé dans son style, en WordprocessingML.

Sans bibliothèque : un .docx est un zip de quelques parties XML, que ce module
écrit lui-même. Le style vient de fichiers (``_style_params``) : ``default.yaml``,
la charte de l'outil, le style du modèle suivi ; ce module n'a aucune valeur
visuelle, seulement la mécanique (marges intérieures des cellules, écarts entre
colonnes, place pour signer) et les règles de composition (nombres à droite,
en-tête de tableau répété, ligne jamais coupée). Le document est dans la langue
du style (correcteur, césure, libellés). Une image est une partie ``word/media``
rangée telle quelle, une seule fois par image distincte (``word/image.py``).

Un document qui suit un modèle s'écrit **section par section**, dans l'ordre du modèle :
le titre de la section (sauf ``show_title: false``), puis ses blocs ; son premier
paragraphe porte un signet caché (``_loom_s<n>``, n sa place dans le modèle) qui marque
son début, et que la relecture retrouve. Le signet est un point, pas une étendue, et
toujours dans un paragraphe (celui de la première cellule quand la section commence par
un tableau) : LibreOffice déplace un signet posé hors d'un paragraphe et dédouble les
fins de ceux qui couvrent plusieurs paragraphes. La
section choisit ses styles (``styles``) et sa mise en page : à côté de la précédente
(un tableau sans bordure, style « loom layout », que la relecture traverse), encadrée,
en colonnes, en paysage, ou page de garde. Paysage et colonnes sont des sections Word :
un saut de section les ouvre et les ferme.

Deux documents identiques donnent le même fichier, octet pour octet : les
dates des propriétés et de l'archive sont fixées, et rien ne dépend de
l'heure ni de la machine. L'ordre des éléments suit le schéma
WordprocessingML, que Word, contrairement à LibreOffice, applique strictement.
"""

import dataclasses
import io
import re
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final
from xml.sax.saxutils import escape, quoteattr

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
from loom_ia.tools_builtin._document.picture import Picture
from loom_ia.tools_builtin._document.picture import parse as parse_picture
from loom_ia.tools_builtin._style_params import Band, Look, Style, house, parse_rule
from loom_ia.tools_builtin._templates import SectionSpec, Template
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

# L'espace laissé pour une signature, sous « Signature : » (2 cm).
SIGN_SPACE: Final = 1134
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
# Les signets des sections : leurs identifiants commencent ici, loin de ceux du sommaire.
SECTION_MARKS: Final = 1000
# Les identifiants des signets des titres que vise un renvoi (``REF_MARK`` et leur rang).
REF_MARKS: Final = 2000

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
# Avec des notes de bas de page : les deux séparateurs, que Word veut nommés ici.
SETTINGS_NOTES: Final = SETTINGS.replace(
    "<w:compat>",
    '<w:footnotePr><w:footnote w:id="-1"/><w:footnote w:id="0"/></w:footnotePr><w:compat>',
)
# Le style maison, tel qu'écrit dans un document sans style.
STYLES: Final = look.styles_xml(house())


def _core(title: str | None, template: str | None = None, language: str = "fr-FR") -> str:
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
        f"</cp:lastModifiedBy><dc:language>{language}</dc:language>"
        f'<dcterms:created xsi:type="dcterms:W3CDTF">{FIXED_DATE}</dcterms:created>'
        f'<dcterms:modified xsi:type="dcterms:W3CDTF">{FIXED_DATE}</dcterms:modified>'
        "</cp:coreProperties>"
    )


# --- Segments et paragraphes ----------------------------------------------------------


def _size(size: int | None) -> str:
    return f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/>' if size else ""


def _half(points: float | None) -> int | None:
    """Une taille en points, en demi-points ; ``None`` sans taille."""
    return look.half_points(points) if points is not None else None


def _plain_runs(runs: Inline, *, bold: bool = False, size: int | None = None) -> str:
    """Des segments sans lien, note ni renvoi : un titre, une légende, une case."""
    return "".join(_one(run, bold=bold, size=size) for run in runs)


def _one(run: Run, *, bold: bool = False, size: int | None = None, link: bool = False) -> str:
    props = (
        (f'<w:rStyle w:val="{look.HYPERLINK}"/>' if link else "")
        + ("<w:b/><w:bCs/>" if run.bold or bold else "")
        + ("<w:i/><w:iCs/>" if run.italic else "")
        + _size(size)
        + ('<w:u w:val="single"/>' if run.underline else "")
    )
    return _run(run.text, f"<w:rPr>{props}</w:rPr>" if props else "")


def _runs(
    body: _Body,
    runs: Inline,
    *,
    bold: bool = False,
    size: int | None = None,
    part: str = "document",
) -> str:
    """Les segments d'un paragraphe, d'un élément de liste, d'une cellule : l'emphase, les
    liens (une relation externe de ``part``), les appels de note, les renvois."""
    said = ""
    index = 0
    while index < len(runs):
        run = runs[index]
        if run.link is not None:
            group = [run]
            while index + len(group) < len(runs) and runs[index + len(group)].link == run.link:
                group.append(runs[index + len(group)])
            index += len(group)
            body.used.add(look.HYPERLINK)
            relation = body.relation(run.link, part)
            said += (
                f'<w:hyperlink r:id="{relation}" w:history="1">'
                + "".join(_one(piece, bold=bold, size=size, link=True) for piece in group)
                + "</w:hyperlink>"
            )
            continue
        index += 1
        if run.note is not None:
            said += _footnote(body, run.note)
        elif run.ref is not None:
            said += _reference(body, run, bold=bold, size=size)
        else:
            said += _one(run, bold=bold, size=size)
    return said


def _footnote(body: _Body, note: Inline) -> str:
    """L'appel d'une note de bas de page ; son texte va dans ``footnotes.xml``."""
    body.used.update((look.FOOTNOTE_TEXT, look.FOOTNOTE_REFERENCE))
    number = len(body.footnotes) + 1
    body.footnotes.append("")
    body.footnotes[number - 1] = (
        f'<w:footnote w:id="{number}"><w:p><w:pPr><w:pStyle w:val="{look.FOOTNOTE_TEXT}"/>'
        f'</w:pPr><w:r><w:rPr><w:rStyle w:val="{look.FOOTNOTE_REFERENCE}"/></w:rPr>'
        '<w:footnoteRef/></w:r><w:r><w:t xml:space="preserve"> </w:t></w:r>'
        + _runs(body, note, part="footnotes")
        + "</w:p></w:footnote>"
    )
    return (
        f'<w:r><w:rPr><w:rStyle w:val="{look.FOOTNOTE_REFERENCE}"/></w:rPr>'
        f'<w:footnoteReference w:id="{number}"/></w:r>'
    )


def _reference(body: _Body, run: Run, *, bold: bool, size: int | None) -> str:
    """Un renvoi : le numéro du titre visé (ses chiffres seuls), dans un champ ``REF`` que
    Word recalcule ; un titre sans numéro, entre guillemets, en lien vers lui. Un article
    (« Article 2 » et son tiret) : son numéro en lien, LibreOffice recalculant le champ
    avec le libellé et le tiret."""
    rank = run.ref or 0
    mark = f"{REF_MARK}{rank}"
    number = body.ref_numbers.get(rank)
    if number is not None and rank not in body.articles:
        shown = _one(dataclasses.replace(run, text=number), bold=bold, size=size)
        return f'<w:fldSimple w:instr=" REF {mark} \\n \\t \\h ">{shown}</w:fldSimple>'
    if number is None:
        opening, closing = QUOTES[body.style.language]
        number = f"{opening}{run.text}{closing}"
    shown = _one(dataclasses.replace(run, text=number), bold=bold, size=size)
    return f'<w:hyperlink w:anchor="{mark}" w:history="1">{shown}</w:hyperlink>'


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
    template: Template | None = None
    # Les listes : numérotée ?, à trois niveaux ?
    lists: list[tuple[bool, bool]] = field(default_factory=list[tuple[bool, bool]])
    charts: list[ChartOut] = field(default_factory=list[ChartOut])
    # Les images distinctes, dans l'ordre où elles paraissent la première fois.
    media: list[Picture] = field(default_factory=list[Picture])
    stored: dict[str, int] = field(default_factory=dict[str, int])
    # Les dessins (graphiques et images) : chacun son identifiant, unique dans le document.
    drawings: int = 0
    # Les styles que seule une disposition demande, et que le corps emploie.
    used: set[str] = field(default_factory=set[str])
    # Les styles à toi que le corps emploie (``styles`` d'une section, ``style`` d'un bloc).
    custom: set[str] = field(default_factory=set[str])
    # Les légendes numérotées : le dernier numéro, par sorte (« Tableau », « Figure »).
    numbers: dict[str, int] = field(default_factory=dict[str, int])
    # Le sommaire : ses entrées (niveau, texte, signet), et le signet de chaque titre repris.
    toc: list[tuple[int, str, str]] = field(default_factory=list[tuple[int, str, str]])
    marks: dict[int, tuple[int, str]] = field(default_factory=dict[int, tuple[int, str]])
    # Les renvois : le rang de chaque titre (par bloc), ceux qu'un renvoi vise, et le numéro
    # de chacun si les titres sont numérotés.
    ranks: dict[int, int] = field(default_factory=dict[int, int])
    targeted: set[int] = field(default_factory=set[int])
    ref_numbers: dict[int, str] = field(default_factory=dict[int, str])
    articles: set[int] = field(default_factory=set[int])
    # Les notes de bas de page (leur XML), et les liens de chaque partie (leurs adresses).
    footnotes: list[str] = field(default_factory=list[str])
    links: dict[str, list[str]] = field(default_factory=dict[str, list[str]])

    def word(self, name: str) -> str:
        """Le style Word d'un style nommé ; un style à toi est écrit s'il sert."""
        if name not in look.WORD_IDS and name != "paragraph":
            self.custom.add(name)
        return look.word_id(self.style, name)

    def relation(self, address: str, part: str) -> str:
        """La relation d'un lien dans une partie (le corps, les notes) : une par adresse."""
        found = self.links.setdefault(part, [])
        if address not in found:
            found.append(address)
        return f"rIdLink{found.index(address) + 1}"

    def picture(self, picture: Picture) -> int:
        """Le numéro de la partie qui porte l'image : chaque image distincte une fois."""
        if picture.digest not in self.stored:
            self.media.append(picture)
            self.stored[picture.digest] = len(self.media)
        return self.stored[picture.digest]


def _caption(body: _Body, kind: str, runs: Inline, style_id: str = "Caption") -> str:
    """La légende ; numérotée (« Figure 2 — ») si le style ``caption`` le veut, par un champ
    ``SEQ`` que Word et LibreOffice recalculent, et que la relecture retire. ``kind`` : le
    libellé (``labels.table``, ``labels.figure``)."""
    prefix = ""
    if body.style.look("caption").numbered:
        number = body.numbers[kind] = body.numbers.get(kind, 0) + 1
        prefix = (
            _run(f"{kind} ")
            + f'<w:fldSimple w:instr=" SEQ {kind} \\* ARABIC "><w:r><w:t>{number}</w:t></w:r>'
            "</w:fldSimple>" + _run(" — ")
        )
    return _paragraph(style_id, prefix + _plain_runs(runs))


def _border(value: str | None) -> str:
    """Un filet de tableau ou de cellule (« épaisseur couleur ») : ses attributs ; ``nil``
    sans filet."""
    rule = parse_rule(value)
    if rule is None:
        return 'w:val="nil"'
    width, color = rule
    return f'w:val="single" w:sz="{round(width * 8)}" w:space="0" w:color="{color}"'


def _table_borders(tables: Look) -> list[tuple[str, str]]:
    """Les filets d'un tableau : ``borders`` (grille, horizontales, aucun) au trait ``line``,
    puis ``border_top`` et ``border_bottom`` s'ils sont dits."""
    every: tuple[str, ...] = ("top", "left", "bottom", "right", "insideH", "insideV")
    sides: tuple[str, ...] = {
        "grid": every,
        "horizontal": ("top", "bottom", "insideH"),
        "none": every,
    }[tables.borders or "grid"]
    line = 'w:val="nil"' if tables.borders == "none" else _border(tables.line)
    drawn: dict[str, str] = dict.fromkeys(sides, line)
    for side, value in (("top", tables.border_top), ("bottom", tables.border_bottom)):
        if value is not None:
            drawn[side] = _border(value)
    return [(side, drawn[side]) for side in every if side in drawn]


def _cell_fill(fill: str | None) -> str:
    if fill is None or fill == "none":
        return ""
    return f'<w:shd w:val="clear" w:color="auto" w:fill="{fill}"/>'


def _table(
    body: _Body,
    table: TableOut,
    width: int,
    tables: Look,
    *,
    cell_style: str = "TableText",
    caption: str = "Caption",
) -> str:
    style = body.style
    header = style.look("table_header")
    total = style.look("table_total")
    # « content » : un tableau étroit (la moitié de la page au plus, un récapitulatif) prend
    # sa largeur ; un plus large garde toute la largeur, plutôt qu'une largeur bâtarde.
    narrow = round(table.natural * style.text_width)
    full_width = tables.width != "content" or narrow > style.text_width // 2
    full = width if full_width else min(width, narrow)
    widths = [round(part * full) for part in table.widths]
    widths[-1] += full - sum(widths)
    size: int | None = None
    position = tables.position or "left"
    said = (
        "<w:tbl><w:tblPr>"
        + (
            '<w:tblW w:w="5000" w:type="pct"/>'
            if full_width
            else f'<w:tblW w:w="{full}" w:type="dxa"/>'
        )
        + (f'<w:jc w:val="{look.jc(position)}"/>' if position != "left" else "")
        + "<w:tblBorders>"
        + "".join(f"<w:{side} {border}/>" for side, border in _table_borders(tables))
        + '</w:tblBorders><w:tblLayout w:type="fixed"/><w:tblCellMar>'
        '<w:top w:w="40" w:type="dxa"/><w:left w:w="85" w:type="dxa"/>'
        '<w:bottom w:w="40" w:type="dxa"/><w:right w:w="85" w:type="dxa"/>'
        '</w:tblCellMar><w:tblLook w:val="04A0" w:firstRow="1" w:lastRow="0" '
        'w:firstColumn="0" w:lastColumn="0" w:noHBand="1" w:noVBand="1"/>'
        # Ce que la relecture retrouve d'un tableau calculé (le récapitulatif de TVA).
        + (f"<w:tblDescription w:val={quoteattr(table.data)}/>" if table.data else "")
        + "</w:tblPr><w:tblGrid>"
        + "".join(f'<w:gridCol w:w="{w}"/>' for w in widths)
        + "</w:tblGrid>"
    )

    # Ce que l'en-tête et le total écrivent en plus du tableau (gras, couleur…) ; le
    # sous-total d'un groupe le doit à son style de paragraphe.
    written: dict[str, Mapping[str, Any]] = {
        "header": style.between("table_header", "table"),
        "total": style.between("table_total", "table"),
        "subtotal": {},
        "body": {},
    }
    subtotal = style.look("table_subtotal")
    lines = {"header": header, "total": total, "subtotal": subtotal}

    def row(cells: Sequence[str | Inline], kind: str = "body", band: bool = False) -> str:
        props = (
            "<w:trPr><w:cantSplit/><w:tblHeader/></w:trPr>"
            if kind == "header"
            else ("<w:trPr><w:cantSplit/></w:trPr>")
        )
        said = f"<w:tr>{props}"
        own = written[kind]
        line_look = lines.get(kind)
        paragraph_style = cell_style
        if kind == "subtotal":
            body.used.add(look.TABLE_SUBTOTAL)
            paragraph_style = look.TABLE_SUBTOTAL
        for index, text in enumerate(cells):
            filled = line_look.fill if line_look is not None else tables.bands if band else None
            ruled = line_look.border_top if line_look is not None and kind != "header" else None
            top = f"<w:tcBorders><w:top {_border(ruled)}/></w:tcBorders>" if ruled else ""
            align = (line_look.align if line_look is not None else None) or table.align[index]
            if align == "justify":
                align = table.align[index]
            if not isinstance(text, str) and kind != "body":
                text = plain(text)
            if isinstance(text, str):
                runs = {prop: value for prop, value in own.items() if prop != "size"}
                props = look.run_xml(runs) + _size(size or _half(own.get("size")))
                content = _run(text, f"<w:rPr>{props}</w:rPr>" if props else "")
            else:
                content = _runs(body, text, size=size)
            said += (
                f'<w:tc><w:tcPr><w:tcW w:w="{widths[index]}" w:type="dxa"/>{top}'
                f'{_cell_fill(filled)}<w:vAlign w:val="center"/></w:tcPr>'
                + _paragraph(paragraph_style, content, f'<w:jc w:val="{_JC[align]}"/>')
                + "</w:tc>"
            )
        return said + "</w:tr>"

    def group_row(label: Inline) -> str:
        """La ligne d'un groupe : une cellule sur toute la largeur, gardée avec la suite."""
        body.used.add(look.TABLE_GROUP)
        grouped = style.look("table_group")
        return (
            "<w:tr><w:trPr><w:cantSplit/></w:trPr><w:tc><w:tcPr>"
            f'<w:tcW w:w="{full}" w:type="dxa"/><w:gridSpan w:val="{len(widths)}"/>'
            f'{_cell_fill(grouped.fill)}<w:vAlign w:val="center"/></w:tcPr>'
            + _paragraph(look.TABLE_GROUP, _runs(body, label))
            + "</w:tc></w:tr>"
        )

    if table.headers:
        said += row(table.headers, "header")
    kinds = table.kinds or ("body",) * len(table.cells)
    band = 0
    for kind, cells in zip(kinds, table.cells, strict=True):
        if kind == "group":
            said += group_row(cells[0])
            band = 0
        elif kind == "body":
            said += row(cells, band=band % 2 == 1)
            band += 1
        else:
            said += row(cells, kind)
    if table.total is not None:
        said += row(table.total, "total")
    said += "</w:tbl>"
    # Toujours un paragraphe après le tableau : deux tableaux voisins ne fusionnent pas.
    if table.caption:
        said += _caption(body, style.labels.table, table.caption, caption)
    else:
        said += _paragraph("Normal", "", _AFTER_TABLE)
    return said


def _image(body: _Body, block: ImageOut, width: int, caption: str = "Caption") -> str:
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
        return said + _caption(body, body.style.labels.figure, block.caption, caption)
    return said + _paragraph("Normal", "", _AFTER_TABLE)


def _layout(
    body: _Body,
    cells: Sequence[str],
    widths: Sequence[int],
    *,
    boxed: Mapping[int, Look] | None = None,
) -> str:
    """Un tableau de disposition : des contenus côte à côte (ou un seul, encadré).

    Sans bordure, au style « loom layout », que la relecture reconnaît : elle rend les
    blocs de chaque cellule, dans l'ordre, et jamais le tableau. ``boxed`` : les cellules
    encadrées, et le style de leur cadre (``box``, ou celui que la section choisit).
    """
    body.used.add(look.LAYOUT)
    framed = dict(boxed or {})
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
        box = framed.get(index)
        frame = (
            "<w:tcBorders>"
            + "".join(
                f"<w:{side} {_border(box.border)}/>" for side in ("top", "left", "bottom", "right")
            )
            + "</w:tcBorders>"
            + _cell_fill(box.fill)
            if box is not None
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


@dataclass(frozen=True, slots=True)
class _Layout:
    """Ce qu'une section du modèle dit de sa mise en page et de ses styles ; un document
    libre est une seule section, sans rien de tout cela."""

    styles: Mapping[str, str] = field(default_factory=dict[str, str])
    box: bool = False
    beside: bool = False
    width: int | None = None
    page_break: bool = False
    columns: int = 1
    landscape: bool = False
    cover: bool = False
    # Espace avant la section, en twips.
    space_before: int = 0
    # Une page de garde : son texte centré.
    align: str | None = None


PLAIN: Final = _Layout()


def _layout_of(spec: SectionSpec) -> _Layout:
    return _Layout(
        styles={str(element): name for element, name in spec.styles.items()},
        box=spec.box,
        beside=spec.beside,
        width=spec.width,
        page_break=spec.page_break,
        columns=spec.columns,
        landscape=spec.landscape,
        cover=spec.cover,
        space_before=round((spec.space_before or 0) * CM),
    )


def _style_id(body: _Body, element: str, chosen: str | None, layout: _Layout) -> str:
    """Le style Word d'un élément : celui que le bloc choisit (``style``), sinon celui que
    la section choisit (``styles``), sinon le style prévu de l'élément."""
    return body.word(chosen or layout.styles.get(element) or element)


def _blocks(body: _Body, blocks: Sequence[BlockOut], width: int, layout: _Layout = PLAIN) -> str:
    """Les blocs d'une section, dans ``width`` twips, selon ses styles."""
    style = body.style
    align = f'<w:jc w:val="{look.jc(layout.align)}"/>' if layout.align else ""
    caption = _style_id(body, "caption", None, layout)
    said = ""
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
            half = width // 2
            # La même place pour les deux : l'écart entre elles est pris sur la première cellule.
            room = _room(half, False, True)
            cells = [_image(body, block, room, caption), _image(body, second, room, caption)]
            said += _layout(body, cells, [half, width - half])
            continue
        match block:
            case TitleOut(runs=runs):
                said += _paragraph(
                    "Title", _plain_runs(runs), '<w:jc w:val="center"/>' if layout.cover else ""
                )
            case HeadingOut(level=level, runs=runs):
                content = _plain_runs(runs)
                mark = body.marks.get(id(block))
                if mark is not None:
                    number, name = mark
                    content = (
                        f'<w:bookmarkStart w:id="{number}" w:name="{name}"/>{content}'
                        f'<w:bookmarkEnd w:id="{number}"/>'
                    )
                rank = body.ranks.get(id(block), 0)
                if rank in body.targeted:
                    number = REF_MARKS + rank
                    content = (
                        f'<w:bookmarkStart w:id="{number}" w:name="{REF_MARK}{rank}"/>{content}'
                        f'<w:bookmarkEnd w:id="{number}"/>'
                    )
                heading = _style_id(body, f"heading{level}", block.style, layout)
                said += _paragraph(heading, content)
            case ParagraphOut(runs=runs):
                paragraph = _style_id(body, "paragraph", block.style, layout)
                said += _paragraph(paragraph, _runs(body, runs), align)
            case ListOut(entries=entries, checkboxes=True):
                body.used.update((look.CHECK, look.CHECK_2))
                for entry in entries:
                    said += _checkbox(body, look.CHECK, entry.checked, entry.runs, None)
                    said += "".join(
                        _checkbox(body, look.CHECK_2, child.checked, child.runs, None)
                        for child in entry.children
                    )
            case ListOut(ordered=ordered, entries=entries):
                deep = any(child.children for entry in entries for child in entry.children)
                body.lists.append((ordered, deep))
                number = len(body.lists)
                items = _style_id(body, "list", block.style, layout)
                said += "".join(
                    _list_items(body, number, 0, entry, align, items) for entry in entries
                )
            case TableOut():
                name = block.style or layout.styles.get("table") or "table"
                text = body.word(name)
                if text == look.INVOICE_TOTALS:
                    body.used.add(text)
                said += _table(
                    body, block, width, style.look(name), cell_style=text, caption=caption
                )
            case NoteOut(title=title, runs=runs):
                head = (
                    _run(title, "<w:rPr><w:b/><w:bCs/></w:rPr>") + "<w:r><w:br/></w:r>"
                    if title
                    else ""
                )
                note = _style_id(body, "note", block.style, layout)
                said += _paragraph(note, head + _runs(body, runs))
            case QuoteOut(runs=runs, source=source):
                quote = _style_id(body, "quote", block.style, layout)
                said += _paragraph(quote, _runs(body, runs))
                if source:
                    said += _paragraph("QuoteSource", _run(f"— {source}"))
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
                    said += _caption(body, style.labels.figure, block.caption, caption)
                else:
                    said += _paragraph("Normal", "", _AFTER_TABLE)
            case ImageOut():
                said += _image(body, block, width, caption)
            case SignatureOut():
                said += _signature(body, block, width)
            case ContentsOut():
                said += _contents(body, width)
            case FieldsOut():
                said += _fields(body, block, width)
            case CodeOut(text=text):
                body.used.add(look.CODE)
                said += _paragraph(look.CODE, _run(text, "<w:rPr><w:noProof/></w:rPr>"))
    return said


def _checkbox(body: _Body, style: str, checked: bool, runs: Inline, size: int | None) -> str:
    """Un élément de liste à cocher : la case (☒ ou ☐), une tabulation, le texte."""
    box = (
        f'<w:r><w:rPr><w:rFonts w:ascii="{look.CHECK_FONT}" w:hAnsi="{look.CHECK_FONT}" '
        f'w:cs="{look.CHECK_FONT}"/>{_size(size)}</w:rPr>'
        f"<w:t>{look.CHECKED if checked else look.UNCHECKED}</w:t></w:r><w:r><w:tab/></w:r>"
    )
    return _paragraph(style, box + _runs(body, runs, size=size))


def _signature(body: _Body, block: SignatureOut, width: int) -> str:
    """La zone de signature : une colonne par signataire, sans cadre.

    Au style de tableau « loom signature », que la relecture reconnaît : elle rend le
    bloc ``signature``, et non un tableau.
    """
    body.used.update((look.SIGNATURE, look.SIGNER, look.MENTION))
    count = len(block.signers)
    widths = [width // count] * count
    widths[-1] += width - sum(widths)
    said = (
        f'<w:tbl><w:tblPr><w:tblStyle w:val="{look.SIGNATURE}"/>'
        f'<w:tblW w:w="{width}" w:type="dxa"/><w:tblLayout w:type="fixed"/>'
        '<w:tblCellMar><w:top w:w="0" w:type="dxa"/><w:left w:w="0" w:type="dxa"/>'
        '<w:bottom w:w="0" w:type="dxa"/><w:right w:w="0" w:type="dxa"/></w:tblCellMar>'
        '<w:tblLook w:val="0000" w:firstRow="0" w:lastRow="0" w:firstColumn="0" '
        'w:lastColumn="0" w:noHBand="1" w:noVBand="1"/></w:tblPr><w:tblGrid>'
        + "".join(f'<w:gridCol w:w="{cell}"/>' for cell in widths)
        + "</w:tblGrid><w:tr><w:trPr><w:cantSplit/></w:trPr>"
    )
    for index, (signer, cell) in enumerate(zip(block.signers, widths, strict=True)):
        gap = (
            f'<w:tcMar><w:right w:w="{CELL_GAP}" w:type="dxa"/></w:tcMar>'
            if index < count - 1
            else ""
        )
        content = _paragraph(look.SIGNER, _run(signer))
        if block.mention:
            content += _paragraph(look.MENTION, _run(block.mention))
        labels = body.style.labels
        content += _paragraph("Normal", _run(labels.date), '<w:spacing w:after="60"/>')
        content += _paragraph(
            "Normal", _run(labels.signature), f'<w:spacing w:after="{SIGN_SPACE}"/>'
        )
        said += f'<w:tc><w:tcPr><w:tcW w:w="{cell}" w:type="dxa"/>{gap}</w:tcPr>{content}</w:tc>'
    return said + "</w:tr></w:tbl>" + _paragraph("Normal", "", _AFTER_TABLE)


def _contents(body: _Body, width: int) -> str:
    """Le sommaire : un champ ``TOC`` que Word met à jour à l'ouverture (``dirty``), avec
    ses entrées déjà écrites — les titres, et leurs numéros s'ils sont numérotés — pour
    qu'il se lise avant toute mise à jour. Les numéros de page (``toc.pages``) sont des
    champs ``PAGEREF`` vers le signet de chaque titre : seul le traitement de texte connaît
    les pages, et LibreOffice les écrit à « Mettre à jour l'index »."""
    style = body.style
    toc = style.look("toc")
    levels = toc.levels or 2
    body.used.update((look.TOC_HEADING, *look.TOC_LEVELS[:levels]))
    pages = toc.page_numbers is not False
    switches = f' TOC \\o "1-{levels}" \\h \\z \\u' + ("" if pages else " \\n")
    begin = (
        '<w:r><w:fldChar w:fldCharType="begin" w:dirty="true"/></w:r>'
        f'<w:r><w:instrText xml:space="preserve">{switches} </w:instrText></w:r>'
        '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
    )
    end = '<w:r><w:fldChar w:fldCharType="end"/></w:r>'
    tabs = (
        f'<w:tabs><w:tab w:val="right" w:leader="dot" w:pos="{width}"/></w:tabs>' if pages else ""
    )
    said = _paragraph(look.TOC_HEADING, _run(style.labels.contents))
    if not body.toc:
        return said + _paragraph(look.TOC_LEVELS[0], begin + end)
    for index, (level, label, mark) in enumerate(body.toc):
        page = (
            '<w:r><w:tab/></w:r><w:r><w:fldChar w:fldCharType="begin"/></w:r>'
            f'<w:r><w:instrText xml:space="preserve"> PAGEREF {mark} \\h </w:instrText></w:r>'
            '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
            '<w:r><w:fldChar w:fldCharType="end"/></w:r>'
            if pages
            else ""
        )
        entry = f'<w:hyperlink w:anchor="{mark}" w:history="1">{_run(label)}{page}</w:hyperlink>'
        said += _paragraph(
            look.TOC_LEVELS[level - 1],
            (begin if index == 0 else "") + entry + (end if index == len(body.toc) - 1 else ""),
            tabs,
        )
    return said


def _toc_entries(body: _Body, blocks: Sequence[BlockOut]) -> None:
    """Les entrées du sommaire, s'il y en a un : les titres repris (jusqu'à ``toc.levels``),
    leur numéro tel que Word l'écrira, et un signet."""
    if not any(isinstance(block, ContentsOut) for block in blocks):
        return
    style = body.style
    levels = style.look("toc").levels or 2
    counters = [0, 0, 0]
    for block in blocks:
        if not isinstance(block, HeadingOut):
            continue
        text = plain(block.runs)
        numbered = style.numbering != "none"
        if numbered:
            counters[block.level - 1] += 1
            counters[block.level :] = [0] * (3 - block.level)
        if block.level > levels:
            continue
        label = text
        if numbered:
            number = _heading_number(style.numbering, counters[: block.level], style.labels.article)
            label = f"{number} {text}"
        name = f"_Toc_loom_{len(body.toc) + 1}"
        body.marks[id(block)] = (len(body.toc) + 1, name)
        body.toc.append((block.level, label, name))


def _ref_targets(body: _Body, blocks: Sequence[BlockOut]) -> None:
    """Les titres qu'un renvoi vise : leur rang (l'ordre des titres, celui de la
    validation), et leur numéro, tel que le champ ``REF`` l'écrit, si les titres sont
    numérotés (les chiffres seuls : « 2 », « 2.1 »)."""
    targeted = referenced(blocks)
    if not targeted:
        return
    body.targeted = set(targeted)
    numbered = body.style.numbering != "none"
    counters = [0, 0, 0]
    rank = 0
    for block in blocks:
        if not isinstance(block, HeadingOut):
            continue
        rank += 1
        body.ranks[id(block)] = rank
        if numbered:
            counters[block.level - 1] += 1
            counters[block.level :] = [0] * (3 - block.level)
            if rank in targeted:
                body.ref_numbers[rank] = ".".join(str(n) for n in counters[: block.level])
                if block.level == 1 and body.style.numbering == "article":
                    body.articles.add(rank)


def _footnotes(notes: Sequence[str]) -> str:
    """Les notes de bas de page, après les deux séparateurs que Word attend."""
    separators = "".join(
        f'<w:footnote w:type="{kind}" w:id="{number}"><w:p><w:pPr>'
        '<w:spacing w:after="0" w:line="240" w:lineRule="auto"/></w:pPr>'
        f"<w:r><w:{kind}/></w:r></w:p></w:footnote>"
        for number, kind in ((-1, "separator"), (0, "continuationSeparator"))
    )
    return (
        _HEAD
        + f"<w:footnotes xmlns:w={quoteattr(_W)} xmlns:r={quoteattr(_R)}>"
        + separators
        + "".join(notes)
        + "</w:footnotes>"
    )


def _link_relations(body: _Body, part: str, *, bare: bool = False) -> str:
    """Les relations des liens d'une partie : vers leur adresse, hors du document ;
    ``bare`` : sans l'enveloppe (ajoutées aux relations du corps)."""
    said = "".join(
        f'<Relationship Id="rIdLink{n}" Type="{_TYPE}/hyperlink" Target={quoteattr(address)} '
        'TargetMode="External"/>'
        for n, address in enumerate(body.links.get(part, []), start=1)
    )
    return said if bare else _HEAD + f'<Relationships xmlns="{_REL}">{said}</Relationships>'


def _heading_number(numbering: str, counters: Sequence[int], article: str) -> str:
    """Le numéro d'un titre, comme la numérotation de ``word/style.py`` l'écrit."""
    if len(counters) == 1:
        return f"{article} {counters[0]} \u2013" if numbering == "article" else f"{counters[0]}."
    return ".".join(str(number) for number in counters)


def _list_items(body: _Body, number: int, level: int, entry: Entry, align: str, style: str) -> str:
    """Un élément de liste, et sa sous-liste, au niveau suivant."""
    numbering = f'<w:numPr><w:ilvl w:val="{level}"/><w:numId w:val="{number}"/></w:numPr>'
    said = _paragraph(style, _runs(body, entry.runs), numbering + align)
    return said + "".join(
        _list_items(body, number, level + 1, child, align, style) for child in entry.children
    )


def _fields(body: _Body, block: FieldsOut, width: int) -> str:
    """Des libellés et leurs valeurs : un tableau sans bordure, au style « loom fields » que
    la relecture reconnaît ; une valeur vide est une ligne à remplir (le trait des
    tableaux, en bas de sa cellule)."""
    style = body.style
    body.used.update((look.FIELDS, look.FIELD_VALUE))
    label = min(round((style.look("fields").label_width or 4) * CM), width // 2)
    widths = [label, width - label]
    blank = f"<w:tcBorders><w:bottom {_border(style.look('table').line)}/></w:tcBorders>"
    said = (
        f'<w:tbl><w:tblPr><w:tblStyle w:val="{look.FIELDS}"/>'
        f'<w:tblW w:w="{width}" w:type="dxa"/><w:tblLayout w:type="fixed"/>'
        '<w:tblCellMar><w:top w:w="0" w:type="dxa"/><w:left w:w="0" w:type="dxa"/>'
        f'<w:bottom w:w="0" w:type="dxa"/><w:right w:w="{CELL_GAP // 2}" w:type="dxa"/>'
        '</w:tblCellMar><w:tblLook w:val="0000" w:firstRow="0" w:lastRow="0" '
        'w:firstColumn="0" w:lastColumn="0" w:noHBand="1" w:noVBand="1"/></w:tblPr><w:tblGrid>'
        + "".join(f'<w:gridCol w:w="{cell}"/>' for cell in widths)
        + "</w:tblGrid>"
    )
    for name, value in block.items:
        said += (
            "<w:tr><w:trPr><w:cantSplit/></w:trPr>"
            f'<w:tc><w:tcPr><w:tcW w:w="{widths[0]}" w:type="dxa"/></w:tcPr>'
            + _paragraph(look.FIELD_LABEL, _run(name))
            + f'</w:tc><w:tc><w:tcPr><w:tcW w:w="{widths[1]}" w:type="dxa"/>'
            + ("" if value else blank)
            + "</w:tcPr>"
            + _paragraph(look.FIELD_VALUE, _runs(body, value))
            + "</w:tc></w:tr>"
        )
    return said + "</w:tbl>" + _paragraph("Normal", "", _AFTER_TABLE)


# --- Sections du modèle, sections Word ------------------------------------------------------


@dataclass(slots=True)
class _Group:
    """Une section du document : sa place dans le modèle (``None`` pour un document libre,
    ou le titre du document avant les sections), sa ligne, sa mise en page, ses blocs."""

    position: int | None
    row: int | None
    layout: _Layout
    blocks: list[BlockOut]


@dataclass(slots=True)
class _Unit:
    """Ce qui se pose d'un tenant : une section, ou des sections côte à côte."""

    xml: str
    landscape: bool
    columns: int
    cover: bool = False
    page_break: bool = False


def _groups(prepared: Prepared, template: Template | None) -> list[_Group]:
    """Un document libre : un seul groupe. Un document qui suit un modèle : le titre du
    document, puis une section par groupe, dans l'ordre du modèle."""
    if prepared.sections is None or template is None:
        return [_Group(None, None, PLAIN, list(prepared.blocks))]
    rows = template.rows()
    specs = {section.position: section.spec for section in template.sections}
    groups: list[_Group] = []
    head = [block for block in prepared.blocks if isinstance(block, TitleOut)]
    if head:
        groups.append(_Group(None, None, PLAIN, list[BlockOut](head)))
    for section in prepared.sections:
        blocks: list[BlockOut] = [*([section.title] if section.title else []), *section.blocks]
        layout = _layout_of(specs[section.position])
        groups.append(_Group(section.position, rows[section.position], layout, blocks))
    # Une page de garde porte aussi ce qui précède sa section : le titre du document.
    if len(groups) > 1 and groups[0].position is None and groups[1].layout.cover:
        groups[1].blocks[:0] = groups[0].blocks
        del groups[0]
    return groups


def _width(style: Style, landscape: bool, columns: int = 1) -> int:
    page_width = style.page[1] if landscape else style.page[0]
    width = page_width - style.margins[2] - style.margins[3]
    return (width - (columns - 1) * COLUMN_GAP) // columns


def _units(body: _Body, groups: Sequence[_Group]) -> list[_Unit]:
    """Les groupes, posés : seuls, ou côte à côte quand ils sont sur la même ligne du
    modèle (une section ``beside`` absente laisse l'autre seule, en pleine largeur)."""
    style = body.style
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
        landscape = style.landscape or group.layout.landscape
        if len(together) > 1:
            units.append(_beside(body, together, landscape))
            continue
        units.append(_alone(body, group, landscape))
    return units


# Le début du premier paragraphe : après ses propriétés, s'il en a.
_FIRST_PARAGRAPH: Final = re.compile(r"<w:p>(?:<w:pPr>.*?</w:pPr>)?", re.DOTALL)


def _marked(group: _Group, content: str) -> str:
    """Le contenu d'une section, son début marqué d'un signet dans son premier paragraphe
    (celui de la première cellule d'un tableau) : la relecture retrouve la section."""
    if group.position is None:
        return content
    number = SECTION_MARKS + group.position
    mark = (
        f'<w:bookmarkStart w:id="{number}" w:name="_loom_s{group.position}"/>'
        f'<w:bookmarkEnd w:id="{number}"/>'
    )
    found = _FIRST_PARAGRAPH.search(content)
    if found is None:  # une section a toujours un paragraphe ; sinon, devant elle
        return mark + content
    return content[: found.end()] + mark + content[found.end() :]


def _box(body: _Body, layout: _Layout) -> Look:
    return body.style.look(layout.styles.get("box", "box"))


def _alone(body: _Body, group: _Group, landscape: bool) -> _Unit:
    layout = group.layout
    columns = layout.columns
    width = _width(body.style, landscape, columns)
    space = COVER_SPACE if layout.cover and not layout.space_before else layout.space_before
    said = _spacer(space) if space else ""
    if layout.cover:
        layout = dataclasses.replace(layout, align="center")
    if layout.box:
        content = _marked(group, _blocks(body, group.blocks, _room(width, True, False), layout))
        said += _layout(body, [content], [width], boxed={0: _box(body, layout)})
    else:
        said += _marked(group, _blocks(body, group.blocks, width, layout))
    return _Unit(said, landscape, columns, layout.cover, layout.page_break)


def _beside(body: _Body, together: Sequence[_Group], landscape: bool) -> _Unit:
    """Des sections côte à côte : une cellule chacune, à la part que dit ``width`` (des
    parts égales sans elle)."""
    width = _width(body.style, landscape)
    given = [group.layout.width for group in together]
    shares = [float(share or 0) for share in given] if all(given) else [1.0] * len(together)
    widths = [round(width * share / sum(shares)) for share in shares]
    widths[-1] += width - sum(widths)
    cells: list[str] = []
    boxed: dict[int, Look] = {}
    for position, (group, cell) in enumerate(zip(together, widths, strict=True)):
        last = position == len(together) - 1
        if group.layout.box:
            boxed[position] = _box(body, group.layout)
        room = _room(cell, group.layout.box, not last)
        cells.append(_marked(group, _blocks(body, group.blocks, room, group.layout)))
    first = together[0].layout
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
    units = _units(body, _groups(prepared, body.template))
    sections = _sections(units, style)
    bands = _Bands(
        header=style.header is not None or look.watermark(style) is not None,
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
                    watermark=look.watermark(style),
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


def render(
    prepared: Prepared, style: Style | None = None, template: Template | None = None
) -> bytes:
    """Le fichier .docx, le même octet pour octet pour un même document et un même style
    (le style maison, sans style) ; ``template`` : le modèle que le document suit."""
    style = style or house()
    body = _Body(style, plain(_title_runs(prepared)), template=template)
    _toc_entries(body, prepared.blocks)
    _ref_targets(body, prepared.blocks)
    document, bands = _document(prepared, body)
    body_media = len(body.media)
    band_parts, listed = _band_parts(body, bands)
    notes: tuple[tuple[str, str], ...] = ()
    if body.footnotes:
        listed.append(("rIdFootnotes", "footnotes", "footnotes.xml"))
        notes = (("word/footnotes.xml", _footnotes(body.footnotes)),)
        if body.links.get("footnotes"):
            notes += (("word/_rels/footnotes.xml.rels", _link_relations(body, "footnotes")),)
    types, relations = _parts_listed(len(body.charts), body.media, body_media, listed)
    if body.links.get("document"):
        relations = relations.replace(
            "</Relationships>", _link_relations(body, "document", bare=True) + "</Relationships>"
        )
    language = look.LANGUAGES[style.language]
    font = style.look("text").font or ""
    parts: tuple[tuple[str, str | bytes], ...] = (
        ("[Content_Types].xml", types),
        ("_rels/.rels", PACKAGE_RELS),
        ("docProps/core.xml", _core(prepared.title, prepared.template, language)),
        ("docProps/app.xml", APP),
        ("word/document.xml", document),
        ("word/_rels/document.xml.rels", relations),
        ("word/styles.xml", look.styles_xml(style, body.used, body.custom)),
        (
            "word/numbering.xml",
            look.numbering_xml(body.lists, style.numbering, style.labels.article),
        ),
        ("word/settings.xml", SETTINGS_NOTES if body.footnotes else SETTINGS),
        *notes,
        *band_parts,
        *(
            (f"word/charts/chart{n}.xml", chart.part(c, style.chart, font, language))
            for n, c in enumerate(body.charts, start=1)
        ),
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
