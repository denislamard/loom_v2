# SPDX-License-Identifier: Apache-2.0
"""Le socle de ``loom.word`` et ``loom.pdf`` : arguments, contenu validé, valeurs à la française.

Sans dépendance : ce que ces essais couvrent vaut pour les deux formats, qui
ne font ensuite que poser le contenu préparé sur la page.
"""

from typing import Any, Final
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from loom_ia.core.ports import ToolError
from loom_ia.tools_builtin._document.arguments import (
    DocumentCall,
    ReadArguments,
    WithCharts,
    WriteArguments,
    parse,
)
from loom_ia.tools_builtin._document.content import (
    NBSP,
    BreakOut,
    ChartOut,
    HeadingOut,
    ListOut,
    NoteOut,
    ParagraphOut,
    Prepared,
    QuoteOut,
    Run,
    TableOut,
    TitleOut,
    display,
    inline,
    prepare,
)
from loom_ia.tools_builtin.pdf_params import PdfParams
from loom_ia.tools_builtin.word_params import WordParams

PARIS: Final = ZoneInfo("Europe/Paris")


def prepared(*blocks: dict[str, Any], title: str | None = None) -> Prepared:
    # Le vocabulaire complet, graphique compris (un outil monté avec charts).
    request = parse(WithCharts, {"path": "s/a", "title": title, "blocks": list(blocks)})
    assert isinstance(request, WriteArguments)
    return prepare(request, PARIS)


def refused(match: str, *blocks: dict[str, Any], title: str | None = None) -> str:
    with pytest.raises(ToolError, match=match) as caught:
        prepared(*blocks, title=title)
    return str(caught.value)


# --- Arguments -------------------------------------------------------------------------


def test_write_by_default_read_on_demand() -> None:
    write = parse(DocumentCall, {"path": "s/a", "blocks": [{"type": "saut_de_page"}]})
    assert isinstance(write, WriteArguments) and write.overwrite is False
    read = parse(DocumentCall, {"action": "read", "path": "s/a"})
    assert isinstance(read, ReadArguments)


@pytest.mark.parametrize(
    ("arguments", "said"),
    [
        ({"path": "s/a"}, "write attend 'blocks'"),
        ({"action": "read", "path": "s/a", "blocks": []}, "blocks ne sert pas à read"),
        ({"action": "read", "path": "s/a", "title": "T"}, "title ne sert pas à read"),
        ({"path": "s/a", "blocks": [{"type": "inconnu"}]}, "blocks.0"),
        ({"path": "s/a", "blocks": [{"type": "titre", "text": "x", "level": 4}]}, "level"),
        (
            {"path": "s/a", "blocks": [{"type": "paragraphe", "text": "x", "style": "rouge"}]},
            "style",
        ),
    ],
)
def test_arguments_refused(arguments: dict[str, Any], said: str) -> None:
    with pytest.raises(ToolError, match=said):
        parse(DocumentCall, arguments)


def test_overwrite_false_on_a_read_is_not_a_fault() -> None:
    assert isinstance(
        parse(DocumentCall, {"action": "read", "path": "s/a", "overwrite": False}), ReadArguments
    )


# --- Contenu -----------------------------------------------------------------------------


def test_blocks_are_prepared_in_order() -> None:
    found = prepared(
        {"type": "titre", "text": "Objet"},
        {"type": "paragraphe", "text": "Bonjour"},
        {"type": "liste", "items": ["a", {"text": "b", "items": ["b1"]}], "ordered": True},
        {"type": "note", "text": "N", "title": "Attention"},
        {"type": "citation", "text": "Q", "source": "S"},
        {"type": "saut_de_page"},
        title="Devis",
    )
    kinds = [type(block) for block in found.blocks]
    assert kinds == [TitleOut, HeadingOut, ParagraphOut, ListOut, NoteOut, QuoteOut, BreakOut]
    listed = found.blocks[3]
    assert isinstance(listed, ListOut) and listed.ordered
    assert [len(entry.children) for entry in listed.entries] == [0, 1]
    assert found.title == "Devis"


def test_emphasis_and_line_breaks() -> None:
    assert inline("Madame **Dupont**, *merci*.") == (
        Run("Madame "),
        Run("Dupont", bold=True),
        Run(", "),
        Run("merci", italic=True),
        Run("."),
    )
    assert inline("**gras *et italique***") == (
        Run("gras ", bold=True),
        Run("et italique", bold=True, italic=True),
    )
    # Un astérisque isolé, ou non refermé, reste du texte.
    assert inline("2 * 3 = 6") == (Run("2 * 3 = 6"),)
    assert inline("2 * 3 * 4") == (Run("2 * 3 * 4"),)
    assert inline("note* de bas") == (Run("note* de bas"),)
    assert inline("ligne 1\nligne 2") == (Run("ligne 1\nligne 2"),)


def test_values_are_written_the_french_way() -> None:
    assert display(1250.5, "montant_eur") == f"1{NBSP}250,50{NBSP}€"
    assert display(-3, "montant_eur") == f"-3,00{NBSP}€"
    assert display(12_500, "montant_keur") == f"12{NBSP}500{NBSP}k€"
    assert display(1234567, "entier") == f"1{NBSP}234{NBSP}567"
    assert display(0.155, "pourcentage") == f"15,5{NBSP}%"
    assert display(2.5, "decimal") == "2,50"
    assert display(None, "texte") == ""


def test_a_table_converts_aligns_and_totals() -> None:
    found = prepared(
        {
            "type": "tableau",
            "columns": [
                {"header": "Client"},
                {"header": "Date", "type": "date"},
                {"header": "Montant", "type": "montant_eur"},
                {"header": "Relances", "type": "entier"},
            ],
            "rows": [
                ["Dupont", "02/10/2026", "1 250,50", 2],
                ["Martin", "2026-10-09", 830],
            ],
            "totals": {"Montant": "somme", "Relances": "moyenne", "Date": "max"},
            "caption": "Suivi au **29/10**",
        }
    )
    [table] = found.blocks
    assert isinstance(table, TableOut)
    assert table.align == ("left", "center", "right", "right")
    assert table.rows[0] == ("Dupont", "02/10/2026", f"1{NBSP}250,50{NBSP}€", "2")
    # Une valeur manquante en fin de ligne : la cellule est vide.
    assert table.rows[1][3] == ""
    assert table.total == ("Total", "09/10/2026", f"2{NBSP}080,50{NBSP}€", "2,00")
    assert abs(sum(table.widths) - 1) < 1e-9
    assert table.caption is not None and table.caption[1] == Run("29/10", bold=True)


def test_structure_faults_are_listed_together() -> None:
    said = refused(
        "Document refusé",
        {"type": "titre", "text": "  "},
        {
            "type": "tableau",
            "columns": [{"header": "A"}, {"header": "A", "type": "entier"}],
            "rows": [["x", "pas un nombre", "de trop"]],
            "totals": {"A": "somme", "B": "somme"},
        },
        {"type": "paragraphe", "text": "bip\x07"},
    )
    for part in (
        "bloc 1 (titre) : texte vide",
        "en-tête donné deux fois : A",
        "ligne 1 : 3 valeurs pour 2 colonnes",
        "'pas un nombre' n'est pas un(e) entier",
        "total sur une colonne inconnue 'B'",
        "caractère de contrôle U+0007 refusé",
    ):
        assert part in said, part


def test_tables_are_bounded() -> None:
    columns = [{"header": f"C{i}"} for i in range(9)]
    refused("9 colonnes, au-delà de 8", {"type": "tableau", "columns": columns, "rows": []})
    refused(
        "501 lignes, au-delà de 500",
        {"type": "tableau", "columns": [{"header": "A"}], "rows": [["x"]] * 501},
    )


def test_an_aggregate_must_fit_its_column() -> None:
    refused(
        "somme sur « Nom », colonne texte : pas un nombre",
        {
            "type": "tableau",
            "columns": [{"header": "Nom"}],
            "rows": [["x"]],
            "totals": {"Nom": "somme"},
        },
    )


def test_warnings_do_not_refuse() -> None:
    found = prepared(
        {"type": "titre", "text": "A", "level": 1},
        {"type": "titre", "text": "B", "level": 3},
        {
            "type": "tableau",
            "columns": [{"header": "Taux", "type": "pourcentage"}],
            "rows": [[15]],
        },
    )
    assert any("niveau sauté" in w for w in found.warnings)
    assert any("0.15 pour 15 %" in w for w in found.warnings)


def test_an_empty_document_is_refused() -> None:
    refused("document vide", {"type": "saut_de_page"})


# --- Paramètres --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("model", "reference"), [(WordParams, "loom.word"), (PdfParams, "loom.pdf")]
)
def test_params_refuse_delete_and_unknown_zones(model: type[WordParams], reference: str) -> None:
    folder = {"name": "s", "path": "/tmp", "rights": ["write", "delete"]}
    with pytest.raises(ValidationError, match=f"le droit 'delete' ne sert pas à {reference}"):
        model.model_validate({"folders": [folder]})
    folder = {"name": "s", "path": "/tmp", "rights": ["write"]}
    with pytest.raises(ValidationError, match="fuseau inconnu"):
        model.model_validate({"folders": [folder], "timezone": "Europe/Dijon"})
    with pytest.raises(ValidationError, match="au moins un dossier"):
        model.model_validate({"folders": []})


def test_aggregates_of_every_kind() -> None:
    found = prepared(
        {
            "type": "tableau",
            "columns": [
                {"header": "Nom"},
                {"header": "Quand", "type": "date_heure"},
                {"header": "Montant", "type": "montant_keur"},
                {"header": "Vide", "type": "decimal"},
            ],
            "rows": [["a", "2026-10-02T09:30:00+00:00", 12], ["b", "02/10/2026 08:00", 3.5]],
            "totals": {"Nom": "nombre", "Quand": "min", "Montant": "max", "Vide": "somme"},
        }
    )
    [table] = found.blocks
    assert isinstance(table, TableOut)
    # Toutes les colonnes ont un agrégat : pas de libellé « Total ».
    assert table.total == ("2", "02/10/2026 08:00", f"12{NBSP}k€", "")
    # Une date-heure avec fuseau passe à l'heure de Paris.
    assert table.rows[0][1] == "02/10/2026 11:30"


def test_long_texts_and_lists_are_bounded() -> None:
    refused("au-delà de 300", {"type": "paragraphe", "text": "x"}, title="t" * 301)
    refused("au-delà de 20000", {"type": "paragraphe", "text": "x" * 20_001})
    items: list[Any] = [{"text": f"e{i}", "items": ["a", "b"]} for i in range(70)]
    refused("210 éléments, au-delà de 200", {"type": "liste", "items": items})


def test_a_total_without_rows_is_not_written() -> None:
    found = prepared(
        {
            "type": "tableau",
            "columns": [{"header": "N", "type": "entier"}],
            "rows": [],
            "totals": {"N": "somme"},
        }
    )
    [table] = found.blocks
    assert isinstance(table, TableOut) and table.total is None
    assert any("aucune ligne" in w for w in found.warnings)


# --- Graphiques ------------------------------------------------------------------------

SALES: Final[dict[str, Any]] = {
    "type": "tableau",
    "columns": [
        {"header": "Client"},
        {"header": "Total HT", "type": "montant_eur"},
        {"header": "Total TTC", "type": "montant_eur"},
        {"header": "Factures", "type": "entier"},
    ],
    "rows": [["Barre", 17094, "18 034,17", 2], ["Lemaire", 13539.47, 16247.36, 2]],
    "totals": {"Total TTC": "somme"},
}


def test_a_chart_takes_its_data_from_the_table_before_it() -> None:
    found = prepared(
        SALES,
        {
            "type": "graphique",
            "kind": "barres_horizontales",
            "title": "TTC par client",
            "from_table": {"categories": "Client", "values": ["Total HT", "Total TTC"]},
        },
    )
    chart = found.blocks[1]
    assert isinstance(chart, ChartOut)
    assert chart.categories == ("Barre", "Lemaire")
    # Les valeurs converties du tableau, sans sa ligne de total.
    assert chart.series == (
        ("Total HT", (17094.0, 13539.47)),
        ("Total TTC", (18034.17, 16247.36)),
    )
    assert (chart.kind, chart.value_type, chart.title) == (
        "barres_horizontales",
        "montant_eur",
        "TTC par client",
    )


def test_a_chart_with_inline_data() -> None:
    found = prepared(
        {
            "type": "graphique",
            "kind": "courbe",
            "value_type": "pourcentage",
            "categories": ["janv.", "févr.", "mars"],
            "series": [{"name": "Marge", "values": [0.2, "25%", None]}],
        }
    )
    [chart] = found.blocks
    assert isinstance(chart, ChartOut)
    assert chart.series == (("Marge", (0.2, 0.25, None)),)


@pytest.mark.parametrize(
    ("blocks", "said"),
    [
        (
            [{"type": "graphique", "from_table": {"categories": "A", "values": ["B"]}}],
            "aucun tableau ne précède le graphique",
        ),
        (
            [
                SALES,
                {"type": "graphique", "from_table": {"categories": "Client", "values": ["TVA"]}},
            ],
            r"colonne\(s\) inconnue\(s\) du tableau précédent : TVA",
        ),
        (
            [
                SALES,
                {
                    "type": "graphique",
                    "from_table": {"categories": "Client", "values": ["Total TTC", "Factures"]},
                },
            ],
            "colonnes de nombres d'un même type",
        ),
        (
            [
                {
                    "type": "graphique",
                    "categories": ["a", "b"],
                    "series": [{"name": "s", "values": [1]}],
                }
            ],
            "1 valeurs pour 2 libellés",
        ),
        (
            [
                {
                    "type": "graphique",
                    "kind": "secteurs",
                    "categories": ["a", "b"],
                    "series": [{"name": "s", "values": [1, -1]}, {"name": "t", "values": [1, 1]}],
                }
            ],
            "un graphique en secteurs n'a qu'une série",
        ),
        (
            [
                {
                    "type": "graphique",
                    "kind": "secteurs",
                    "categories": [str(i) for i in range(13)],
                    "series": [{"name": "s", "values": [1] * 13}],
                }
            ],
            "13 secteurs, au-delà de 12",
        ),
        (
            [
                {
                    "type": "graphique",
                    "value_type": "texte",
                    "categories": ["a"],
                    "series": [{"name": "s", "values": [1]}],
                }
            ],
            "un graphique porte des nombres",
        ),
    ],
)
def test_charts_refused(blocks: list[dict[str, Any]], said: str) -> None:
    refused(said, *blocks)


@pytest.mark.parametrize(
    "blocks",
    [
        [{"type": "graphique", "from_table": {"categories": "A", "values": ["B"]}}],
        [SALES, {"type": "graphique", "from_table": {"categories": "Client", "values": ["TVA"]}}],
    ],
)
def test_a_chart_without_its_table_says_one_fault(blocks: list[dict[str, Any]]) -> None:
    # Ni « aucun libellé » ni « aucune série » en plus : elles découlent de la première.
    said = refused("graphique", *blocks)
    assert said.count("\n- ") == 1, said


@pytest.mark.parametrize(
    ("chart", "said"),
    [
        ({"type": "graphique"}, "soit de 'categories' et 'series', soit de 'from_table'"),
        ({"type": "graphique", "categories": ["a"]}, "'categories' et 'series' vont ensemble"),
        (
            {
                "type": "graphique",
                "value_type": "entier",
                "from_table": {"categories": "a", "values": ["b"]},
            },
            "'value_type' ne sert qu'aux données en ligne",
        ),
    ],
)
def test_chart_arguments_refused(chart: dict[str, Any], said: str) -> None:
    with pytest.raises(ToolError, match=said):
        parse(WithCharts, {"path": "s/a", "blocks": [chart]})
