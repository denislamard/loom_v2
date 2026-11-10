# SPDX-License-Identifier: Apache-2.0
"""Un graphique dans un PDF : dessiné par reportlab (``reportlab.graphics``), en vectoriel.

Le même graphique que dans ``word`` — les couleurs du style (``chart`` : séries, texte,
grille), mêmes étiquettes de valeur quand il y a peu de barres, légende seulement s'il y
a plusieurs séries ou des secteurs —, dans la police du texte. Les nombres s'écrivent
dans la langue du document, comme dans les tableaux.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
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
from loom_ia.tools_builtin._style_params import ChartLook, Language
from loom_ia.tools_builtin._values import ColumnType

HEIGHT: Final = 9 * cm
# Un libellé plus long est raccourci sous l'axe : il chevaucherait ses voisins.
AXIS_LABEL: Final = 18


@dataclass(frozen=True, slots=True)
class _Paint:
    """Les couleurs du style, en couleurs de reportlab ; la police et la langue."""

    palette: tuple[Any, ...]
    grid: Any
    text: Any
    font: str
    language: Language

    def color(self, index: int) -> Any:
        return self.palette[index % len(self.palette)]


def _paint(look: ChartLook, font: str, language: Language) -> _Paint:
    def hex_color(value: str, default: str) -> Any:
        return colors.HexColor(f"#{value or default}")

    codes = look.colors or style.PALETTE
    return _Paint(
        palette=tuple(colors.HexColor(f"#{code}") for code in codes),
        grid=hex_color(look.grid, style.GRID),
        text=hex_color(look.text, style.TEXT),
        font=font,
        language=language,
    )


def _axis(kind: ColumnType, language: Language) -> Callable[[float], str]:
    """Les graduations : des nombres ronds, sans décimale inutile."""

    def said(value: float) -> str:
        if kind == "percent":
            return display(value * 100, "integer", language) + f"{NBSP}%"
        whole = display(value, "integer", language)
        match kind:
            case "amount_eur":
                return f"{whole}{NBSP}€"
            case "amount_keur":
                return f"{whole}{NBSP}k€"
            case "decimal" if value != int(value):
                return display(value, "decimal", language)
            case _:
                return whole

    return said


def _label(kind: ColumnType, language: Language) -> Callable[[float], str]:
    return lambda value: display(value, kind, language)


def _short(label: str) -> str:
    return label if len(label) <= AXIS_LABEL else label[: AXIS_LABEL - 1] + "…"


def height(chart: ChartOut) -> float:
    if chart.kind != "horizontal_bar":
        return HEIGHT
    return min(20 * cm, max(6 * cm, 1.4 * cm + 0.6 * cm * len(chart.categories)))


def drawing(
    chart: ChartOut, width: float, look: ChartLook, font: str, language: Language
) -> Drawing:
    """Le graphique, à la largeur donnée, aux couleurs du style ``chart``, dans la police
    ``font`` et la langue du document."""
    paint = _paint(look, font, language)
    tall = height(chart)
    found = Drawing(width, tall)
    multi = len(chart.series) > 1
    if chart.kind == "pie":
        _pie(found, chart, width, tall, paint)
        return found
    bottom = 2.0 * cm if multi else 0.9 * cm
    if chart.kind == "horizontal_bar":
        plot: Any = HorizontalBarChart()
        left = 4 * cm
    elif chart.kind == "line":
        plot = HorizontalLineChart()
        left = 1.8 * cm
    else:
        plot = VerticalBarChart()
        left = 1.8 * cm
    # Des libellés qui ne tiennent pas côte à côte sous l'axe s'inclinent.
    room = (width - left) / max(len(chart.categories), 1) / (8 * 0.55)
    longest = max((len(_short(label)) for label in chart.categories), default=0)
    rotated = chart.kind != "horizontal_bar" and longest > room
    if rotated:
        bottom += 1.4 * cm
    plot.x, plot.y = left, bottom
    plot.width, plot.height = width - left - 0.4 * cm, tall - bottom - 0.4 * cm
    names = [_short(label) for label in chart.categories]
    data = [
        [value if value is not None else None for value in values] for _, values in chart.series
    ]
    if chart.kind == "horizontal_bar":
        # reportlab dessine la première catégorie en bas : à l'envers, elle est en haut.
        names = names[::-1]
        data = [row[::-1] for row in data]
    plot.data = data
    plot.categoryAxis.categoryNames = names
    for axis in (plot.categoryAxis, plot.valueAxis):
        axis.labels.fontName = paint.font
        axis.labels.fontSize = 8
        axis.labels.fillColor = paint.text
        axis.strokeColor = paint.grid
    plot.valueAxis.labelTextFormat = _axis(chart.value_type, language)
    plot.valueAxis.visibleGrid = 1
    plot.valueAxis.gridStrokeColor = paint.grid
    plot.valueAxis.visibleAxis = 0
    values = [v for _, row in chart.series for v in row if v is not None]
    if values and min(values) >= 0:
        plot.valueAxis.valueMin = 0
    if rotated:
        plot.categoryAxis.labels.angle = 35
        plot.categoryAxis.labels.boxAnchor = "ne"
    if chart.kind == "line":
        for index in range(len(chart.series)):
            plot.lines[index].strokeColor = paint.color(index)
            plot.lines[index].strokeWidth = 2
            make = cast(Any, markers).makeMarker
            plot.lines[index].symbol = make("FilledCircle", size=4)
            plot.lines[index].symbol.fillColor = paint.color(index)
    else:
        plot.barSpacing = 1
        plot.groupSpacing = 6
        for index in range(len(chart.series)):
            plot.bars[index].fillColor = paint.color(index)
            plot.bars[index].strokeColor = None
        if not multi and len(chart.categories) <= style.LABELLED_BARS:
            plot.barLabelFormat = _label(chart.value_type, language)
            plot.barLabels.fontName = paint.font
            plot.barLabels.fontSize = 7
            plot.barLabels.fillColor = paint.text
            plot.barLabels.nudge = 3
            horizontal = chart.kind == "horizontal_bar"
            plot.barLabels.boxAnchor = "w" if horizontal else "s"
            if values and min(values) >= 0:
                # De la place au bout de la plus grande barre, pour son étiquette.
                plot.valueAxis.valueMax = max(values) * style.LABEL_ROOM[chart.kind]
    found.add(plot)
    if multi:
        _legend(found, [name for name, _ in chart.series], width / 2, 0.1 * cm, "s", paint)
    return found


def _pie(found: Drawing, chart: ChartOut, width: float, tall: float, paint: _Paint) -> None:
    pie: Any = Pie()
    size = tall - 1 * cm
    pie.x, pie.y = 1 * cm, 0.5 * cm
    pie.width = pie.height = size
    values = [value or 0.0 for value in chart.series[0][1]]
    total = sum(values) or 1.0
    pie.data = values
    pie.labels = [display(value / total, "percent", paint.language) for value in values]
    pie.simpleLabels = 1
    pie.slices.fontName = paint.font
    pie.slices.fontSize = 8
    pie.slices.fontColor = paint.text
    pie.slices.strokeColor = colors.white
    pie.slices.strokeWidth = 1
    pie.slices.labelRadius = 1.15
    for index in range(len(values)):
        pie.slices[index].fillColor = paint.color(index)
    found.add(pie)
    names = list(chart.categories)
    _legend(found, names, size + 2.5 * cm, tall / 2, "w", paint, columns=len(names))


def _legend(
    found: Drawing,
    names: Sequence[str],
    x: float,
    y: float,
    anchor: str,
    paint: _Paint,
    *,
    columns: int | None = None,
) -> None:
    legend: Any = Legend()
    legend.x, legend.y = x, y
    legend.boxAnchor = anchor
    legend.fontName = paint.font
    legend.fontSize = 8
    legend.fillColor = paint.text
    legend.alignment = "right"
    legend.columnMaximum = columns or 1
    legend.dx = legend.dy = 7
    legend.colorNamePairs = [(paint.color(index), _short(name)) for index, name in enumerate(names)]
    found.add(legend)
