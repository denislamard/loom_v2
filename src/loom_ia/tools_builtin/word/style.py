# SPDX-License-Identifier: Apache-2.0
"""Le style d'un .docx en WordprocessingML : ses styles, ses numérotations, ses en-têtes.

Tout part d'un ``Style`` résolu (``_style_params``) : la charte de l'outil,
complétée par la disposition du modèle suivi. ``Style()`` est le style maison, et
ce qu'il donne est le même, octet pour octet, qu'avant les styles : ``styles.xml``,
``numbering.xml`` et ``footer1.xml`` d'un document sans style ne changent pas.

Les styles que seule une disposition demande (titre masqué, titre discret, en-tête,
tableau de disposition) ne sont écrits que si le document s'en sert : un style
déclaré vide (``{}``) donne encore le fichier du style maison.
"""

import re
from collections.abc import Iterable
from typing import Final
from xml.sax.saxutils import escape

from loom_ia.tools_builtin._style_params import Band, Level, Numbering, Style

_W: Final = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_HEAD: Final = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
_VML: Final = (
    'xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office"'
)
_PLACEHOLDER: Final = re.compile(r"\{(page|pages|titre)\}")
# La numérotation des titres (charte ``headings.numbering``), à part des listes.
HEADING_NUMBERING: Final = 900
# Les styles que seule une disposition demande, par identifiant.
HIDDEN: Final = "HeadingHidden"
QUIET: Final = "HeadingQuiet"
HEADER: Final = "Header"
LAYOUT: Final = "LoomLayout"
# Le nom du style de tableau de disposition : la relecture le reconnaît.
LAYOUT_NAME: Final = "loom layout"
_JC: Final = {"gauche": "left", "centre": "center", "droite": "right", "justifie": "both"}


def jc(align: str) -> str:
    """L'alignement WordprocessingML d'un alignement de style (« centre » → « center »)."""
    return _JC[align]


def half_points(size: float) -> int:
    return round(size * 2)


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


def _fonts(name: str | None) -> str:
    if name is None:
        return ""
    return f'<w:rFonts w:ascii="{name}" w:hAnsi="{name}" w:eastAsia="{name}" w:cs="{name}"/>'


def _size(points: float) -> str:
    value = half_points(points)
    return f'<w:sz w:val="{value}"/><w:szCs w:val="{value}"/>'


def _heading(style: Style, level: int, look: Level, before: int, after: int) -> str:
    numbered = (
        f'<w:numPr><w:ilvl w:val="{level - 1}"/><w:numId w:val="{HEADING_NUMBERING}"/></w:numPr>'
        if style.numbering != "aucune"
        else ""
    )
    return _style(
        f"Heading{level}",
        f"heading {level}",
        next_style="Normal",
        paragraph=f'<w:keepNext/><w:keepLines/>{numbered}<w:spacing w:before="{before}" '
        f'w:after="{after}"/><w:outlineLvl w:val="{level - 1}"/>',
        run=f"{_fonts(style.heading_font)}<w:b/><w:bCs/>"
        + ("<w:caps/>" if look.uppercase else "")
        + f'<w:color w:val="{look.color}"/>{_size(look.size)}',
    )


def styles_xml(style: Style, used: Iterable[str] = ()) -> str:
    """``styles.xml`` : les styles maison aux valeurs du style, et ceux que le corps demande."""
    wanted = set(used)
    title = style.title
    text_color = f'<w:color w:val="{style.text_color}"/>' if style.text_color else ""
    said = (
        _HEAD + f'<w:styles xmlns:w="{_W}">'
        "<w:docDefaults><w:rPrDefault><w:rPr>"
        f"{_fonts(style.font)}{text_color}{_size(style.font_size)}"
        '<w:lang w:val="fr-FR" w:eastAsia="fr-FR" w:bidi="ar-SA"/></w:rPr></w:rPrDefault>'
        f'<w:pPrDefault><w:pPr><w:spacing w:after="{style.after}" w:line="{style.line}" '
        'w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>'
        + _style(
            "Normal",
            "Normal",
            based=None,
            default=True,
            paragraph='<w:jc w:val="both"/>' if style.justify else "",
        )
        + _style(
            "Title",
            "Title",
            next_style="Normal",
            paragraph=(
                f'<w:pBdr><w:bottom w:val="single" w:sz="8" w:space="4" w:color="{style.accent}"/>'
                "</w:pBdr>"
                if title.rule
                else ""
            )
            + '<w:spacing w:after="300"/>'
            + (f'<w:jc w:val="{jc(title.align)}"/>' if title.align != "gauche" else ""),
            run=f"{_fonts(style.heading_font)}<w:b/><w:bCs/>"
            + ("<w:caps/>" if title.uppercase else "")
            + f'<w:color w:val="{title.color}"/>{_size(title.size)}',
        )
        + _heading(style, 1, style.levels[0], 360, 120)
        + _heading(style, 2, style.levels[1], 240, 80)
        + _heading(style, 3, style.levels[2], 200, 60)
        + _style("ListParagraph", "List Paragraph", paragraph='<w:spacing w:after="60"/>')
        + _style(
            "Quote",
            "Quote",
            next_style="Normal",
            paragraph='<w:pBdr><w:left w:val="single" w:sz="12" w:space="8" '
            f'w:color="{style.rule}"/></w:pBdr><w:spacing w:before="160" w:after="80"/>'
            '<w:ind w:left="567" w:right="567"/>',
            run=f'<w:i/><w:iCs/><w:color w:val="{style.muted}"/>',
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
            paragraph=f'<w:pBdr><w:left w:val="single" w:sz="24" w:space="8" '
            f'w:color="{style.note_border}"/></w:pBdr><w:shd w:val="clear" w:color="auto" '
            f'w:fill="{style.note_fill}"/>'
            '<w:spacing w:before="120" w:after="200"/><w:ind w:left="170" w:right="170"/>',
        )
        + _style(
            "Caption",
            "caption",
            next_style="Normal",
            paragraph='<w:spacing w:before="60" w:after="240"/><w:jc w:val="center"/>',
            run=f'<w:i/><w:iCs/><w:color w:val="{style.muted}"/>'
            '<w:sz w:val="18"/><w:szCs w:val="18"/>',
        )
        + _style(
            "TableText",
            "Table Text",
            paragraph='<w:spacing w:after="0" w:line="240" w:lineRule="auto"/>',
            run=_size(style.tables.size),
        )
        + _style(
            "Footer",
            "footer",
            paragraph='<w:spacing w:after="0"/><w:jc w:val="center"/>',
            run=f'<w:color w:val="{style.muted}"/><w:sz w:val="18"/><w:szCs w:val="18"/>',
        )
    )
    if HEADER in wanted:
        said += _style(
            HEADER,
            "header",
            paragraph='<w:spacing w:after="0"/>',
            run=f'<w:color w:val="{style.muted}"/><w:sz w:val="18"/><w:szCs w:val="18"/>',
        )
    # Un titre masqué ne prend pas de numéro : sa numérotation est annulée (numId 0).
    cancel = '<w:numPr><w:numId w:val="0"/></w:numPr>' if style.numbering != "aucune" else ""
    if HIDDEN in wanted:
        said += _style(
            HIDDEN,
            "loom heading hidden",
            based="Heading1",
            next_style="Normal",
            paragraph=cancel,
            run="<w:vanish/>",
        )
    if QUIET in wanted:
        said += _style(
            QUIET,
            "loom heading quiet",
            based="Heading1",
            next_style="Normal",
            paragraph='<w:spacing w:before="200" w:after="60"/>',
            run=f'<w:caps w:val="0"/><w:color w:val="{style.accent}"/>{_size(style.font_size)}',
        )
    if LAYOUT in wanted:
        said += (
            f'<w:style w:type="table" w:styleId="{LAYOUT}"><w:name w:val="{LAYOUT_NAME}"/>'
            "<w:tblPr/></w:style>"
        )
    return said + "</w:styles>"


# --- Numérotations -----------------------------------------------------------------

# Puces et numéros : deux niveaux, la sous-liste en retrait.
_LEVELS: Final = {
    False: (("bullet", "•"), ("bullet", "\u2013")),
    True: (("decimal", "%1."), ("lowerLetter", "%2)")),
}
_HEADING_TEXTS: Final[dict[Numbering, tuple[str, str, str]]] = {
    "decimale": ("%1.", "%1.%2", "%1.%2.%3"),
    "article": ("Article %1 \u2013", "%1.%2", "%1.%2.%3"),
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


def _heading_abstract(numbering: Numbering) -> str:
    """Les titres numérotés : un numéro suivi d'une espace, lié aux styles de titre."""
    said = '<w:abstractNum w:abstractNumId="2"><w:multiLevelType w:val="multilevel"/>'
    for level, text in enumerate(_HEADING_TEXTS[numbering]):
        said += (
            f'<w:lvl w:ilvl="{level}"><w:start w:val="1"/><w:numFmt w:val="decimal"/>'
            f'<w:pStyle w:val="Heading{level + 1}"/><w:suff w:val="space"/>'
            f'<w:lvlText w:val="{text}"/><w:lvlJc w:val="left"/>'
            '<w:pPr><w:ind w:left="0" w:firstLine="0"/></w:pPr></w:lvl>'
        )
    return said + "</w:abstractNum>"


def numbering_xml(lists: Iterable[bool], headings: Numbering = "aucune") -> str:
    """Une numérotation par liste : chaque liste numérotée repart de 1. Les définitions
    (``abstractNum``) précèdent toutes les numérotations (``num``) : Word l'exige."""
    said = _HEAD + f'<w:numbering xmlns:w="{_W}">' + _abstract(0, False) + _abstract(1, True)
    if headings != "aucune":
        said += _heading_abstract(headings)
    for number, ordered in enumerate(lists, start=1):
        said += f'<w:num w:numId="{number}"><w:abstractNumId w:val="{int(ordered)}"/>'
        said += "".join(
            f'<w:lvlOverride w:ilvl="{level}"><w:startOverride w:val="1"/></w:lvlOverride>'
            for level in range(2)
        )
        said += "</w:num>"
    if headings != "aucune":
        said += f'<w:num w:numId="{HEADING_NUMBERING}"><w:abstractNumId w:val="2"/></w:num>'
    return said + "</w:numbering>"


# --- En-têtes et pieds de page --------------------------------------------------------


def _text(text: str, title: str) -> str:
    """Un texte d'en-tête : ``{page}`` et ``{pages}`` en champs, ``{titre}`` le titre du
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
    watermark: str | None = None,
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
        for name, place in (("left", "gauche"), ("center", "centre"), ("right", "droite"))
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


def _watermark(text: str) -> str:
    """Le filigrane : un texte en diagonale, gris clair, derrière le texte de chaque page.

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
        'mso-position-vertical-relative:margin" o:allowincell="f" fillcolor="#D9D9D9" '
        'stroked="f"><v:fill opacity=".5"/>'
        '<v:textpath style="font-family:&quot;Arial&quot;;font-size:1pt" '
        f'string="{escape(text, {chr(34): "&quot;"})}"/></v:shape></w:pict></w:r>'
    )
