# SPDX-License-Identifier: Apache-2.0
"""Un graphique Word natif : la partie DrawingML (``word/charts/chartN.xml``) et son ancre.

Le graphique est celui de Word lui-même, pas une image : net à toute taille,
retouchable (couleurs, type, titre) dans Word, et ses nombres suivent la langue
du lecteur. Ses données sont écrites dans le cache de la partie (``numCache``,
``strCache``), sans classeur embarqué : Word et LibreOffice l'affichent tel quel,
mais « Modifier les données » ne trouve pas de classeur.

Le style est figé, comme celui du document (``_chart_style``) : la palette de la maison, des
étiquettes de valeur quand il y a peu de barres, la légende seulement quand il y
a plusieurs séries (ou des secteurs). L'ordre des éléments suit le schéma
DrawingML, que Word applique strictement.
"""

from collections.abc import Sequence
from typing import Final
from xml.sax.saxutils import escape, quoteattr

from loom_ia.tools_builtin._chart_style import (
    AXIS_FORMATS,
    GRID,
    LABEL_FORMATS,
    LABEL_ROOM,
    LABELLED_BARS,
    PALETTE,
    TEXT,
    axis_end,
)
from loom_ia.tools_builtin._document.content import ChartOut

_C: Final = "http://schemas.openxmlformats.org/drawingml/2006/chart"
_A: Final = "http://schemas.openxmlformats.org/drawingml/2006/main"
_R: Final = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_WP: Final = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
_HEAD: Final = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'

FONT: Final = "Calibri"
# Mesures en EMU : 360 000 par centimètre.
CM: Final = 360_000
WIDTH: Final = 17 * CM
HEIGHT: Final = 9 * CM


def height(chart: ChartOut) -> int:
    """La hauteur du graphique : des barres horizontales prennent la place de leurs libellés."""
    if chart.kind != "barres_horizontales":
        return HEIGHT
    return min(20 * CM, max(6 * CM, int(1.4 * CM + 0.6 * CM * len(chart.categories))))


def anchor(relation: str, number: int, chart: ChartOut) -> str:
    """Le paragraphe qui porte le graphique, à la largeur du texte."""
    name = escape(chart.title or f"Graphique {number}", {'"': "&quot;"})
    return (
        '<w:p><w:pPr><w:keepNext/><w:spacing w:before="120" w:after="60"/><w:jc w:val="center"/>'
        "</w:pPr><w:r><w:drawing>"
        f'<wp:inline xmlns:wp="{_WP}" distT="0" distB="0" distL="0" distR="0">'
        f'<wp:extent cx="{WIDTH}" cy="{height(chart)}"/>'
        '<wp:effectExtent l="0" t="0" r="0" b="0"/>'
        f'<wp:docPr id="{number}" name="{name}"/><wp:cNvGraphicFramePr/>'
        f'<a:graphic xmlns:a="{_A}"><a:graphicData uri="{_C}">'
        f'<c:chart xmlns:c="{_C}" xmlns:r="{_R}" r:id="{relation}"/>'
        "</a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>"
    )


def _text_props(size: int, *, bold: bool = False) -> str:
    return (
        "<c:txPr><a:bodyPr/><a:lstStyle/><a:p><a:pPr>"
        f'<a:defRPr sz="{size}" b="{int(bold)}"><a:solidFill><a:srgbClr val="{TEXT}"/>'
        f'</a:solidFill><a:latin typeface="{FONT}"/></a:defRPr></a:pPr>'
        '<a:endParaRPr lang="fr-FR"/></a:p></c:txPr>'
    )


def _fill(color: str) -> str:
    return f'<c:spPr><a:solidFill><a:srgbClr val="{color}"/></a:solidFill></c:spPr>'


def _line(color: str, width: int = 28575) -> str:
    return (
        f'<c:spPr><a:ln w="{width}" cap="rnd"><a:solidFill><a:srgbClr val="{color}"/>'
        "</a:solidFill><a:round/></a:ln></c:spPr>"
    )


def _column(index: int) -> str:
    """La lettre de colonne d'une série dans la feuille de référence : B, C, D…"""
    return chr(ord("B") + index)


def _categories(labels: Sequence[str]) -> str:
    points = "".join(
        f'<c:pt idx="{i}"><c:v>{escape(label)}</c:v></c:pt>' for i, label in enumerate(labels)
    )
    return (
        f"<c:cat><c:strRef><c:f>Feuil1!$A$2:$A${len(labels) + 1}</c:f><c:strCache>"
        f'<c:ptCount val="{len(labels)}"/>{points}</c:strCache></c:strRef></c:cat>'
    )


def _values(index: int, values: Sequence[float | None], code: str) -> str:
    column = _column(index)
    points = "".join(
        f'<c:pt idx="{i}"><c:v>{value!r}</c:v></c:pt>'
        for i, value in enumerate(values)
        if value is not None
    )
    return (
        f"<c:val><c:numRef><c:f>Feuil1!${column}$2:${column}${len(values) + 1}</c:f>"
        f"<c:numCache><c:formatCode>{escape(code)}</c:formatCode>"
        f'<c:ptCount val="{len(values)}"/>{points}</c:numCache></c:numRef></c:val>'
    )


def _name(index: int, name: str) -> str:
    return (
        f"<c:tx><c:strRef><c:f>Feuil1!${_column(index)}$1</c:f><c:strCache>"
        f'<c:ptCount val="1"/><c:pt idx="0"><c:v>{escape(name)}</c:v></c:pt>'
        "</c:strCache></c:strRef></c:tx>"
    )


def _labels(code: str, *, percent: bool = False, position: str | None = None) -> str:
    """Étiquettes de valeur (ou de pourcentage, pour des secteurs)."""
    place = f'<c:dLblPos val="{position}"/>' if position else ""
    return (
        f'<c:dLbls><c:numFmt formatCode={quoteattr(code)} sourceLinked="0"/>'
        "<c:spPr><a:noFill/><a:ln><a:noFill/></a:ln></c:spPr>"
        f"{_text_props(800)}{place}"
        '<c:showLegendKey val="0"/>'
        f'<c:showVal val="{int(not percent)}"/><c:showCatName val="0"/>'
        f'<c:showSerName val="0"/><c:showPercent val="{int(percent)}"/>'
        '<c:showBubbleSize val="0"/></c:dLbls>'
    )


def _labelled(chart: ChartOut) -> bool:
    """Une valeur sur chaque barre : une seule série, et pas trop de barres."""
    return len(chart.series) == 1 and len(chart.categories) <= LABELLED_BARS


def _scale(chart: ChartOut) -> str:
    """Les bornes de l'axe des valeurs, quand une étiquette doit tenir au bout des barres.

    Sans elles, Word arrête l'axe juste après la plus grande barre, et son
    étiquette déborde du graphique. Le bout de l'axe reste un nombre rond.
    """
    room = LABEL_ROOM.get(chart.kind)
    if room is None or not _labelled(chart):
        return ""
    values = [value for _, row in chart.series for value in row if value is not None]
    end = axis_end(values, room)
    if end is None:
        return ""
    return f'<c:max val="{round(end, 10):.10g}"/><c:min val="0"/>'


def _axes(chart: ChartOut) -> str:
    horizontal = chart.kind == "barres_horizontales"
    code = AXIS_FORMATS[chart.value_type]
    grid = (
        f'<c:majorGridlines><c:spPr><a:ln w="6350"><a:solidFill><a:srgbClr val="{GRID}"/>'
        "</a:solidFill></a:ln></c:spPr></c:majorGridlines>"
    )
    no_line = "<c:spPr><a:ln><a:noFill/></a:ln></c:spPr>"
    axis_line = (
        f'<c:spPr><a:ln w="9525"><a:solidFill><a:srgbClr val="{GRID}"/></a:solidFill>'
        "</a:ln></c:spPr>"
    )
    # Barres horizontales : la première catégorie en haut, l'axe des valeurs en bas.
    order = "maxMin" if horizontal else "minMax"
    return (
        f'<c:catAx><c:axId val="1001"/><c:scaling><c:orientation val="{order}"/></c:scaling>'
        f'<c:delete val="0"/><c:axPos val="{"l" if horizontal else "b"}"/>'
        '<c:numFmt formatCode="General" sourceLinked="0"/>'
        '<c:majorTickMark val="none"/><c:minorTickMark val="none"/>'
        f'<c:tickLblPos val="nextTo"/>{axis_line}{_text_props(900)}'
        '<c:crossAx val="1002"/><c:crosses val="autoZero"/><c:auto val="1"/>'
        '<c:lblAlgn val="ctr"/><c:lblOffset val="100"/><c:noMultiLvlLbl val="0"/></c:catAx>'
        '<c:valAx><c:axId val="1002"/><c:scaling><c:orientation val="minMax"/>'
        f"{_scale(chart)}</c:scaling>"
        f'<c:delete val="0"/><c:axPos val="{"b" if horizontal else "l"}"/>{grid}'
        f'<c:numFmt formatCode={quoteattr(code)} sourceLinked="0"/>'
        '<c:majorTickMark val="none"/><c:minorTickMark val="none"/>'
        f'<c:tickLblPos val="nextTo"/>{no_line}{_text_props(900)}'
        f'<c:crossAx val="1001"/><c:crosses val="{"max" if horizontal else "autoZero"}"/>'
        '<c:crossBetween val="between"/></c:valAx>'
    )


def _plot(chart: ChartOut) -> str:
    label = LABEL_FORMATS[chart.value_type]
    match chart.kind:
        case "secteurs":
            name, values = chart.series[0]
            slices = "".join(
                f'<c:dPt><c:idx val="{i}"/><c:bubble3D val="0"/>'
                f'<c:spPr><a:solidFill><a:srgbClr val="{PALETTE[i % len(PALETTE)]}"/>'
                '</a:solidFill><a:ln w="12700"><a:solidFill><a:srgbClr val="FFFFFF"/>'
                "</a:solidFill></a:ln></c:spPr></c:dPt>"
                for i in range(len(chart.categories))
            )
            series = (
                f'<c:ser><c:idx val="0"/><c:order val="0"/>{_name(0, name)}{slices}'
                f"{_labels('0%', percent=True, position='outEnd')}"
                f"{_categories(chart.categories)}{_values(0, values, label)}</c:ser>"
            )
            return (
                f'<c:pieChart><c:varyColors val="1"/>{series}'
                '<c:firstSliceAng val="0"/></c:pieChart>'
            )
        case "courbe":
            series = "".join(
                f'<c:ser><c:idx val="{i}"/><c:order val="{i}"/>{_name(i, name)}'
                f"{_line(PALETTE[i % len(PALETTE)])}"
                '<c:marker><c:symbol val="circle"/><c:size val="5"/>'
                f"{_fill(PALETTE[i % len(PALETTE)])}</c:marker>"
                f"{_categories(chart.categories)}{_values(i, values, label)}"
                '<c:smooth val="0"/></c:ser>'
                for i, (name, values) in enumerate(chart.series)
            )
            return (
                f'<c:lineChart><c:grouping val="standard"/><c:varyColors val="0"/>{series}'
                '<c:marker val="1"/><c:axId val="1001"/><c:axId val="1002"/></c:lineChart>'
                + _axes(chart)
            )
        case "barres" | "barres_horizontales":
            labelled = _labelled(chart)
            series = "".join(
                f'<c:ser><c:idx val="{i}"/><c:order val="{i}"/>{_name(i, name)}'
                f'{_fill(PALETTE[i % len(PALETTE)])}<c:invertIfNegative val="0"/>'
                f"{_labels(label, position='outEnd') if labelled else ''}"
                f"{_categories(chart.categories)}{_values(i, values, label)}</c:ser>"
                for i, (name, values) in enumerate(chart.series)
            )
            direction = "bar" if chart.kind == "barres_horizontales" else "col"
            return (
                f'<c:barChart><c:barDir val="{direction}"/><c:grouping val="clustered"/>'
                f'<c:varyColors val="0"/>{series}<c:gapWidth val="60"/>'
                '<c:axId val="1001"/><c:axId val="1002"/></c:barChart>' + _axes(chart)
            )


def part(chart: ChartOut) -> str:
    """La partie ``chartN.xml`` du graphique."""
    title = (
        "<c:title><c:tx><c:rich><a:bodyPr/><a:lstStyle/><a:p><a:pPr>"
        f'<a:defRPr sz="1100" b="1"><a:solidFill><a:srgbClr val="1F3864"/></a:solidFill>'
        f'<a:latin typeface="{FONT}"/></a:defRPr></a:pPr><a:r><a:rPr lang="fr-FR" sz="1100" '
        f'b="1"><a:solidFill><a:srgbClr val="1F3864"/></a:solidFill><a:latin typeface="{FONT}"/>'
        f"</a:rPr><a:t>{escape(chart.title)}</a:t></a:r></a:p></c:rich></c:tx>"
        '<c:overlay val="0"/></c:title><c:autoTitleDeleted val="0"/>'
        if chart.title
        else '<c:autoTitleDeleted val="1"/>'
    )
    legend = (
        '<c:legend><c:legendPos val="'
        + ("r" if chart.kind == "secteurs" else "b")
        + f'"/><c:overlay val="0"/>{_text_props(900)}</c:legend>'
        if chart.kind == "secteurs" or len(chart.series) > 1
        else ""
    )
    return (
        _HEAD + f'<c:chartSpace xmlns:c="{_C}" xmlns:a="{_A}" xmlns:r="{_R}">'
        '<c:date1904 val="0"/><c:lang val="fr-FR"/><c:roundedCorners val="0"/>'
        f"<c:chart>{title}<c:plotArea><c:layout/>{_plot(chart)}</c:plotArea>{legend}"
        '<c:plotVisOnly val="1"/><c:dispBlanksAs val="gap"/></c:chart>'
        "<c:spPr><a:noFill/><a:ln><a:noFill/></a:ln></c:spPr>"
        f"{_text_props(900)}</c:chartSpace>"
    )
