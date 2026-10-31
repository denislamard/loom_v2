# SPDX-License-Identifier: Apache-2.0
"""``loom.excel`` avec ``charts: true`` : des graphiques Excel liés aux cellules de leur onglet.

Le paramètre ``charts`` (schéma, description, ``loom validate``) ; les
graphiques écrits, relus dans leur XML (parties ``xl/charts`` et
``xl/drawings``) ; leur validation avec le reste du classeur ; LibreOffice, s'il
est là, vérifie qu'il les reconnaît.
"""

import json
import re
import subprocess
import zipfile
from pathlib import Path
from typing import Any, Final

import pytest

pytest.importorskip("openpyxl", reason="extra 'office' absent")

from conftest import ConfigFactory, demo_agent

from loom_ia.access.cli import main
from loom_ia.core.bounded_regex import bounded_validator
from loom_ia.core.model import DEFAULT_TENANT, RunId, SessionId
from loom_ia.core.ports import ToolContext, ToolError
from loom_ia.tools_builtin import BuiltinContext
from loom_ia.tools_builtin.excel import ExcelTool, build
from loom_ia.tools_builtin.excel_params import ExcelParams

CONTEXT: Final = ToolContext(
    tenant_id=DEFAULT_TENANT,
    session_id=SessionId("s"),
    run_id=RunId("r"),
    call_id="c1",
    agent="demo",
)
JSONL: Final = {"events": {"backend": "jsonl", "path": "journal"}}
FOLDERS: Final = [
    {"name": "donnees", "path": "donnees", "rights": ["read"]},
    {"name": "sortie", "path": "sortie", "rights": ["read", "write", "overwrite"]},
]


def make(tmp_path: Path, **params: Any) -> ExcelTool:
    model = ExcelParams.model_validate({"folders": FOLDERS, **params})
    return build(model, BuiltinContext(base_dir=tmp_path))


@pytest.fixture
def disk(tmp_path: Path) -> Path:
    for name in ("donnees", "sortie"):
        (tmp_path / name).mkdir()
    return tmp_path


@pytest.fixture
def tool(disk: Path) -> ExcelTool:
    return make(disk)


@pytest.fixture
def charted(disk: Path) -> ExcelTool:
    return make(disk, charts=True)


async def call(tool: ExcelTool, **arguments: Any) -> dict[str, Any]:
    output = await tool.invoke(dict(arguments), CONTEXT)
    assert isinstance(output.data, dict)
    return output.data


async def refused(tool: ExcelTool, match: str, **arguments: Any) -> None:
    with pytest.raises(ToolError, match=match):
        await tool.invoke(dict(arguments), CONTEXT)


def part(path: Path, name: str) -> str:
    with zipfile.ZipFile(path) as archive:
        return archive.read(name).decode()


CLIENTS: Final[dict[str, Any]] = {
    "name": "Clients",
    "columns": [{"header": "Client"}, {"header": "Total TTC", "type": "montant_eur"}],
    "rows": [
        {"cells": ["Barre", 18034.17]},
        {"cells": ["Lemaire", 16247.36]},
        {"cells": ["Guillot", 14709.17]},
    ],
    "totals": {"Total TTC": "somme"},
}
BARS: Final = {
    "kind": "barres_horizontales",
    "title": "TTC par client",
    "categories": "Client",
    "values": ["Total TTC"],
}
PIE: Final = {"kind": "secteurs", "categories": "Client", "values": ["Total TTC"]}
MONTHS: Final[dict[str, Any]] = {
    "name": "CA mensuel",
    "columns": [
        {"header": "Mois"},
        {"header": "Facturé", "type": "montant_eur"},
        {"header": "Encaissé", "type": "montant_eur"},
        {"header": "Écart", "type": "montant_eur"},
        {"header": "Relances", "type": "entier"},
    ],
    "rows": [
        {"cells": ["janv.", 18200, 15100, "=B2-C2", 3]},
        {"cells": ["févr.", 21500, 19900, "=B3-C3", 1]},
        {"cells": ["mars", 19800, None, "=B4-C4", 0]},
    ],
}


def sheet(base: dict[str, Any], *charts: dict[str, Any], **changes: Any) -> dict[str, Any]:
    return {**base, "charts": list(charts), **changes}


# --- Le paramètre charts ---------------------------------------------------------------


def test_charts_are_off_unless_asked(tool: ExcelTool, charted: ExcelTool) -> None:
    # Sans charts, ni le champ ni sa ligne : ce que voyait le modèle avant les graphiques.
    assert "charts" not in json.dumps(tool.spec.input_schema)
    assert "Graphiques" not in tool.spec.description
    assert ("graphiques", "non") in tool.settings().options
    assert '"charts"' in json.dumps(charted.spec.input_schema)
    assert "Graphiques d'un onglet" in charted.spec.description
    assert ("graphiques", "oui") in charted.settings().options


async def test_without_charts_a_chart_is_refused(tool: ExcelTool, charted: ExcelTool) -> None:
    arguments: dict[str, Any] = {"path": "sortie/c.xlsx", "sheets": [sheet(CLIENTS, BARS)]}
    # Le moteur valide les arguments contre le schéma, avant l'outil.
    assert list(bounded_validator(tool.spec.input_schema).iter_errors(arguments))
    assert not list(bounded_validator(charted.spec.input_schema).iter_errors(arguments))
    await refused(tool, "Arguments invalides", **arguments)


async def test_a_workbook_without_chart_does_not_change(
    tool: ExcelTool, charted: ExcelTool, disk: Path
) -> None:
    plain = await call(tool, path="sortie/a.xlsx", sheets=[CLIENTS, MONTHS])
    same = await call(charted, path="sortie/b.xlsx", sheets=[CLIENTS, sheet(MONTHS)])
    assert plain["sha256"] == same["sha256"]
    # Le résumé ne gagne « charts » que pour un onglet qui en a.
    assert all("charts" not in found for found in same["sheets"])


def test_validate_shows_charts(
    demo: ConfigFactory, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "sortie").mkdir()
    folders = [{"name": "sortie", "path": "sortie", "rights": ["write"]}]
    agent = demo_agent(tools=[{"python": "loom.excel", "folders": folders, "charts": True}])
    path = demo(agents=[agent], storage=JSONL)
    assert main(["--config", str(path), "validate"]) == 0
    assert "graphiques : oui" in capsys.readouterr().out


# --- Écriture ---------------------------------------------------------------------------


async def test_a_chart_is_linked_to_its_cells(charted: ExcelTool, disk: Path) -> None:
    result = await call(charted, path="sortie/c.xlsx", sheets=[sheet(CLIENTS, BARS, PIE)])
    assert result["sheets"] == [{"name": "Clients", "rows": 3, "formulas": 1, "charts": 2}]
    path = disk / "sortie" / "c.xlsx"
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
    assert {"xl/charts/chart1.xml", "xl/charts/chart2.xml", "xl/drawings/drawing1.xml"} <= names
    bars = part(path, "xl/charts/chart1.xml")
    # Les plages de l'onglet, sans la ligne de total (ligne 5) : rien n'est recopié.
    assert "<strRef><f>'Clients'!$A$2:$A$4</f></strRef>" in bars
    assert "<f>'Clients'!$B$2:$B$4</f>" in bars
    assert "<f>'Clients'!B1</f>" in bars  # le nom de la série : son en-tête
    assert "18034" not in bars
    assert '<barDir val="bar" />' in bars and '<orientation val="maxMin" />' in bars
    assert '<a:srgbClr val="1F3864" />' in bars  # la palette de la maison
    assert '<showVal val="1" />' in bars and "<legend>" not in bars
    # Valeurs connues : l'axe va jusqu'à un nombre rond qui laisse la place des étiquettes.
    assert '<max val="24000" />' in bars and '<min val="0" />' in bars
    pie = part(path, "xl/charts/chart2.xml")
    assert "<pieChart>" in pie and '<showPercent val="1" />' in pie and "<legend>" in pie
    # À droite des données (colonnes A et B : le graphique part de D), empilés.
    drawing = part(path, "xl/drawings/drawing1.xml")
    anchors = re.findall(r"<from><col>(\d+)</col><colOff>0</colOff><row>(\d+)</row>", drawing)
    # Le premier fait 6 cm (trois barres horizontales) : 12 lignes et une d'écart.
    assert anchors == [("3", "1"), ("3", "14")]


async def test_a_chart_on_formulas_lets_excel_choose_its_axis(
    charted: ExcelTool, disk: Path
) -> None:
    line = {"kind": "courbe", "categories": "Mois", "values": ["Facturé", "Encaissé"]}
    gaps = {"kind": "barres", "categories": "Mois", "values": ["Écart"]}
    await call(charted, path="sortie/m.xlsx", sheets=[sheet(MONTHS, line, gaps)])
    path = disk / "sortie" / "m.xlsx"
    curve = part(path, "xl/charts/chart1.xml")
    assert "<lineChart>" in curve and "<legend>" in curve  # deux séries : une légende
    assert '<dispBlanksAs val="gap" />' in curve  # mars sans encaissé : un trou
    formulas = part(path, "xl/charts/chart2.xml")
    assert "<f>'CA mensuel'!$D$2:$D$4</f>" in formulas
    assert "<max " not in formulas  # une formule n'a sa valeur qu'à l'ouverture


async def test_same_call_same_bytes(charted: ExcelTool) -> None:
    sheets = [sheet(CLIENTS, BARS, PIE)]
    first = await call(charted, path="sortie/a.xlsx", sheets=sheets)
    second = await call(charted, path="sortie/b.xlsx", sheets=sheets)
    assert first["sha256"] == second["sha256"]


# --- Refus ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("chart", "said"),
    [
        ({**BARS, "values": ["TVA"]}, r"inconnue\(s\) : TVA \(colonnes : Client, Total TTC\)"),
        ({**BARS, "values": ["Client"]}, "une colonne citée deux fois"),
        ({**BARS, "categories": "Total TTC", "values": ["Client"]}, "colonnes de nombres"),
        ({**PIE, "values": ["Total TTC", "Total TTC"]}, "une colonne citée deux fois"),
    ],
)  # fmt: skip
async def test_bad_columns_are_refused(
    charted: ExcelTool, chart: dict[str, Any], said: str
) -> None:
    await refused(charted, said, path="sortie/x.xlsx", sheets=[sheet(CLIENTS, chart)])


async def test_series_of_one_type(charted: ExcelTool) -> None:
    mixed = {"kind": "barres", "categories": "Mois", "values": ["Facturé", "Relances"]}
    await refused(
        charted,
        r"colonnes de nombres d'un même type \(ici : entier, montant_eur\)",
        path="sortie/x.xlsx",
        sheets=[sheet(MONTHS, mixed)],
    )


async def test_pie_rules(charted: ExcelTool) -> None:
    pie = {"kind": "secteurs", "categories": "Mois", "values": ["Facturé", "Encaissé"]}
    await refused(charted, "n'a qu'une série", path="sortie/x.xlsx", sheets=[sheet(MONTHS, pie)])
    negative = {**CLIENTS, "rows": [{"cells": ["Barre", -5]}, {"cells": ["Lemaire", 3]}]}
    await refused(charted, "négative", path="sortie/x.xlsx", sheets=[sheet(negative, PIE)])
    many = {**CLIENTS, "rows": [{"cells": [f"C{n}", n]} for n in range(1, 14)]}
    await refused(
        charted, "13 secteurs, au-delà de 12", path="sortie/x.xlsx", sheets=[sheet(many, PIE)]
    )
    zero = {**CLIENTS, "rows": [{"cells": ["Barre", 0]}, {"cells": ["Lemaire", 0]}]}
    await refused(
        charted, "toutes les valeurs sont nulles", path="sortie/x.xlsx", sheets=[sheet(zero, PIE)]
    )


async def test_data_rows_are_contiguous(charted: ExcelTool) -> None:
    rows = [
        {"cells": ["Barre", 1]},
        {"cells": ["Sous-total", 1], "role": "total"},
        {"cells": ["Lemaire", 2]},
    ]
    await refused(
        charted,
        "la ligne 4 suit une ligne de total ou de note",
        path="sortie/x.xlsx",
        sheets=[sheet({**CLIENTS, "rows": rows}, BARS)],
    )
    empty = {**CLIENTS, "rows": [{"cells": ["Total", 1], "role": "total"}]}
    await refused(
        charted, "aucune ligne de données", path="sortie/x.xlsx", sheets=[sheet(empty, BARS)]
    )


async def test_faults_are_said_together(charted: ExcelTool) -> None:
    with pytest.raises(ToolError) as caught:
        await call(
            charted,
            path="sortie/x.xlsx",
            sheets=[sheet(CLIENTS, {**BARS, "values": ["TVA"]}, *[BARS] * 10)],
        )
    said = str(caught.value)
    assert "11 graphiques, au-delà de 10" in said and "colonne(s) inconnue(s) : TVA" in said


async def test_a_long_line_but_not_long_bars(charted: ExcelTool, disk: Path) -> None:
    lines = ["Jour,Ventes", *(f"J{n},{n * 10}" for n in range(1, 366))]
    (disk / "donnees" / "jours.csv").write_text("\n".join(lines), encoding="utf-8")
    days = {
        "name": "Jours",
        "columns": [{"header": "Jour"}, {"header": "Ventes", "type": "entier"}],
        "data": {"path": "donnees/jours.csv"},
    }
    line = {"kind": "courbe", "categories": "Jour", "values": ["Ventes"]}
    await call(charted, path="sortie/j.xlsx", sheets=[sheet(days, line)])
    assert "$B$2:$B$366" in part(disk / "sortie" / "j.xlsx", "xl/charts/chart1.xml")
    bars = {"kind": "barres", "categories": "Jour", "values": ["Ventes"]}
    await refused(
        charted,
        "365 lignes, au-delà de 50 pour des barres",
        path="sortie/k.xlsx",
        sheets=[sheet(days, bars)],
    )
    many = [f"J{n},{n}" for n in range(1, 1002)]
    (disk / "donnees" / "long.csv").write_text("\n".join(["Jour,Ventes", *many]), encoding="utf-8")
    longer = {**days, "data": {"path": "donnees/long.csv"}}
    await refused(
        charted,
        "1001 lignes, au-delà de 1000 pour une courbe",
        path="sortie/l.xlsx",
        sheets=[sheet(longer, line)],
    )


# --- LibreOffice ----------------------------------------------------------------------


def to_ods(soffice: str, source: Path, out: Path) -> None:
    """Une conversion par LibreOffice, avec un profil neuf : elle doit réussir."""
    done = subprocess.run(
        [
            soffice,
            f"-env:UserInstallation={(out.parent / 'profil').as_uri()}",
            "--headless",
            "--convert-to",
            "ods",
            "--outdir",
            str(out),
            str(source),
        ],
        capture_output=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stderr


async def test_libreoffice_reads_the_charts(charted: ExcelTool, disk: Path, soffice: str) -> None:
    line = {"kind": "courbe", "categories": "Mois", "values": ["Facturé"]}
    await call(
        charted, path="sortie/c.xlsx", sheets=[sheet(CLIENTS, BARS, PIE), sheet(MONTHS, line)]
    )
    out = disk / "ods"
    to_ods(soffice, disk / "sortie" / "c.xlsx", out)
    with zipfile.ZipFile(out / "c.ods") as archive:
        objects = "".join(
            archive.read(name).decode()
            for name in archive.namelist()
            if name.startswith("Object") and name.endswith("content.xml")
        )
    assert sorted(set(re.findall(r'chart:class="chart:(\w+)"', objects))) == [
        "bar",
        "circle",
        "line",
    ]
    # Les plages de l'onglet, reconnues comme telles.
    assert "Clients.A2:Clients.A4" in objects.replace("$", "").replace("'", "")
