# SPDX-License-Identifier: Apache-2.0
"""Un graphique dans un PDF : dessiné par reportlab (``reportlab.graphics``), en vectoriel.

Le même graphique que dans ``word`` — mêmes couleurs, mêmes étiquettes de
valeur quand il y a peu de barres, légende seulement s'il y a plusieurs séries
ou des secteurs —, dans les polices standard du PDF. Les nombres s'écrivent à
la française, comme dans les tableaux.
"""

from collections.abc import Callable, Sequence
from typing import Any, Final, cast

from reportlab.graphics.charts.barcharts import HorizontalBarChart, VerticalBarChart
from reportlab.graphics.charts.legends import Legend
from reportlab.graphics.charts.linecharts import HorizontalLineChart
from reportlab.graphics.charts.piecharts import Pie
from reportlab.graphics.shapes import Drawing
from reportlab.graphics.widgets import markers
from reportlab.lib import colors
from reportlab.lib.units import cm

from loom_ia.tools_builtin import _chart_style as style
from loom_ia.tools_builtin._document.content import NBSP, ChartOut, display
from loom_ia.tools_builtin._values import ColumnType

FONT: Final = "Helvetica"
# Le style commun aux trois outils (``_chart_style``), en couleurs de reportlab.
PALETTE: Final = tuple(colors.HexColor(f"#{code}") for code in style.PALETTE)
GRID: Final = colors.HexColor(f"#{style.GRID}")
TEXT: Final = colors.HexColor(f"#{style.TEXT}")
HEIGHT: Final = 9 * cm
# Un libellé plus long est raccourci sous l'axe : il chevaucherait ses voisins.
AXIS_LABEL: Final = 18


def _axis(kind: ColumnType) -> Callable[[float], str]:
    """Les graduations : des nombres ronds, sans décimale inutile."""

    def said(value: float) -> str:
        if kind == "pourcentage":
            return display(value * 100, "entier") + f"{NBSP}%"
        whole = display(value, "entier")
        match kind:
            case "montant_eur":
                return f"{whole}{NBSP}€"
            case "montant_keur":
                return f"{whole}{NBSP}k€"
            case "decimal" if value != int(value):
                return display(value, "decimal")
            case _:
                return whole

    return said


def _label(kind: ColumnType) -> Callable[[float], str]:
    return lambda value: display(value, kind)


def _short(label: str) -> str:
    return label if len(label) <= AXIS_LABEL else label[: AXIS_LABEL - 1] + "…"


def height(chart: ChartOut) -> float:
    if chart.kind != "barres_horizontales":
        return HEIGHT
    return min(20 * cm, max(6 * cm, 1.4 * cm + 0.6 * cm * len(chart.categories)))


def drawing(chart: ChartOut, width: float) -> Drawing:
    """Le graphique, à la largeur du texte."""
    tall = height(chart)
    found = Drawing(width, tall)
    multi = len(chart.series) > 1
    if chart.kind == "secteurs":
        _pie(found, chart, width, tall)
        return found
    bottom = 2.0 * cm if multi else 0.9 * cm
    if chart.kind == "barres_horizontales":
        plot: Any = HorizontalBarChart()
        left = 4 * cm
    elif chart.kind == "courbe":
        plot = HorizontalLineChart()
        left = 1.8 * cm
    else:
        plot = VerticalBarChart()
        left = 1.8 * cm
    # Des libellés qui ne tiennent pas côte à côte sous l'axe s'inclinent.
    room = (width - left) / max(len(chart.categories), 1) / (8 * 0.55)
    longest = max((len(_short(label)) for label in chart.categories), default=0)
    rotated = chart.kind != "barres_horizontales" and longest > room
    if rotated:
        bottom += 1.4 * cm
    plot.x, plot.y = left, bottom
    plot.width, plot.height = width - left - 0.4 * cm, tall - bottom - 0.4 * cm
    names = [_short(label) for label in chart.categories]
    data = [
        [value if value is not None else None for value in values] for _, values in chart.series
    ]
    if chart.kind == "barres_horizontales":
        # reportlab dessine la première catégorie en bas : à l'envers, elle est en haut.
        names = names[::-1]
        data = [row[::-1] for row in data]
    plot.data = data
    plot.categoryAxis.categoryNames = names
    for axis in (plot.categoryAxis, plot.valueAxis):
        axis.labels.fontName = FONT
        axis.labels.fontSize = 8
        axis.labels.fillColor = TEXT
        axis.strokeColor = GRID
    plot.valueAxis.labelTextFormat = _axis(chart.value_type)
    plot.valueAxis.visibleGrid = 1
    plot.valueAxis.gridStrokeColor = GRID
    plot.valueAxis.visibleAxis = 0
    values = [v for _, row in chart.series for v in row if v is not None]
    if values and min(values) >= 0:
        plot.valueAxis.valueMin = 0
    if rotated:
        plot.categoryAxis.labels.angle = 35
        plot.categoryAxis.labels.boxAnchor = "ne"
    if chart.kind == "courbe":
        for index in range(len(chart.series)):
            plot.lines[index].strokeColor = PALETTE[index % len(PALETTE)]
            plot.lines[index].strokeWidth = 2
            make = cast(Any, markers).makeMarker
            plot.lines[index].symbol = make("FilledCircle", size=4)
            plot.lines[index].symbol.fillColor = PALETTE[index % len(PALETTE)]
    else:
        plot.barSpacing = 1
        plot.groupSpacing = 6
        for index in range(len(chart.series)):
            plot.bars[index].fillColor = PALETTE[index % len(PALETTE)]
            plot.bars[index].strokeColor = None
        if not multi and len(chart.categories) <= style.LABELLED_BARS:
            plot.barLabelFormat = _label(chart.value_type)
            plot.barLabels.fontName = FONT
            plot.barLabels.fontSize = 7
            plot.barLabels.fillColor = TEXT
            plot.barLabels.nudge = 3
            horizontal = chart.kind == "barres_horizontales"
            plot.barLabels.boxAnchor = "w" if horizontal else "s"
            if values and min(values) >= 0:
                # De la place au bout de la plus grande barre, pour son étiquette.
                plot.valueAxis.valueMax = max(values) * style.LABEL_ROOM[chart.kind]
    found.add(plot)
    if multi:
        _legend(found, [name for name, _ in chart.series], width / 2, 0.1 * cm, "s")
    return found


def _pie(found: Drawing, chart: ChartOut, width: float, tall: float) -> None:
    pie: Any = Pie()
    size = tall - 1 * cm
    pie.x, pie.y = 1 * cm, 0.5 * cm
    pie.width = pie.height = size
    values = [value or 0.0 for value in chart.series[0][1]]
    total = sum(values) or 1.0
    pie.data = values
    pie.labels = [display(value / total, "pourcentage") for value in values]
    pie.simpleLabels = 1
    pie.slices.fontName = FONT
    pie.slices.fontSize = 8
    pie.slices.fontColor = TEXT
    pie.slices.strokeColor = colors.white
    pie.slices.strokeWidth = 1
    pie.slices.labelRadius = 1.15
    for index in range(len(values)):
        pie.slices[index].fillColor = PALETTE[index % len(PALETTE)]
    found.add(pie)
    names = list(chart.categories)
    _legend(found, names, size + 2.5 * cm, tall / 2, "w", columns=len(names))


def _legend(
    found: Drawing,
    names: Sequence[str],
    x: float,
    y: float,
    anchor: str,
    *,
    columns: int | None = None,
) -> None:
    legend: Any = Legend()
    legend.x, legend.y = x, y
    legend.boxAnchor = anchor
    legend.fontName = FONT
    legend.fontSize = 8
    legend.fillColor = TEXT
    legend.alignment = "right"
    legend.columnMaximum = columns or 1
    legend.dx = legend.dy = 7
    legend.colorNamePairs = [
        (PALETTE[index % len(PALETTE)], _short(name)) for index, name in enumerate(names)
    ]
    found.add(legend)
