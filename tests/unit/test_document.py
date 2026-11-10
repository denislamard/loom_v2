# SPDX-License-Identifier: Apache-2.0
"""Le socle de ``loom.word`` et ``loom.pdf`` : arguments, contenu validé, valeurs à la française.

Sans dépendance : ce que ces essais couvrent vaut pour les deux formats, qui
ne font ensuite que poser le contenu préparé sur la page.
"""

import hashlib
import json
import re
import zlib
from typing import Any, Final, cast
from zoneinfo import ZoneInfo

import pytest
from image_files import chunk, jpeg, png
from pydantic import ValidationError

from loom_ia.core.ports import ToolError
from loom_ia.tools_builtin._document.arguments import (
    MAX_IMAGES,
    MAX_IMAGES_BYTES,
    DocumentCall,
    ReadArguments,
    TemplateArguments,
    WithCharts,
    WriteArguments,
    input_schema,
    parse,
    variant,
)
from loom_ia.tools_builtin._document.content import (
    MAX_NOTES,
    NBSP,
    ChartOut,
    CodeOut,
    Entry,
    FieldsOut,
    HeadingOut,
    ImageOut,
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
    referenced,
)
from loom_ia.tools_builtin._document.picture import Picture, PictureError, dimensions
from loom_ia.tools_builtin._document.picture import parse as parse_picture
from loom_ia.tools_builtin._templates import fixed_paragraphs
from loom_ia.tools_builtin._templates import key as template_key
from loom_ia.tools_builtin._templates import parse as parse_template
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
    write = parse(DocumentCall, {"path": "s/a", "blocks": [{"type": "paragraph", "text": "x"}]})
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
        ({"path": "s/a", "blocks": [{"type": "heading", "text": "x", "level": 4}]}, "level"),
        (
            {"path": "s/a", "blocks": [{"type": "paragraph", "text": "x", "style": "rouge"}]},
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
        {"type": "heading", "text": "Objet"},
        {"type": "paragraph", "text": "Bonjour"},
        {"type": "list", "items": ["a", {"text": "b", "items": ["b1"]}], "ordered": True},
        {"type": "note", "text": "N", "title": "Attention"},
        {"type": "quote", "text": "Q", "source": "S"},
        title="Devis",
    )
    kinds = [type(block) for block in found.blocks]
    assert kinds == [TitleOut, HeadingOut, ParagraphOut, ListOut, NoteOut, QuoteOut]
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


def test_underline_marks() -> None:
    assert inline("un <u>mot</u> souligné") == (
        Run("un "),
        Run("mot", underline=True),
        Run(" souligné"),
    )
    # Avec le gras et l'italique, dans n'importe quel ordre ; au milieu d'un mot aussi.
    both = (Run("gras souligné", bold=True, underline=True),)
    assert inline("**<u>gras souligné</u>**") == both
    assert inline("<u>**gras souligné**</u>") == both
    assert inline("*<u>it</u>*") == (Run("it", italic=True, underline=True),)
    assert inline("mi<u>lieu</u>du") == (Run("mi"), Run("lieu", underline=True), Run("du"))
    # Une balise seule, non refermée, en majuscules ou imbriquée reste du texte.
    for said in ("a <u> b", "fin </u> seule", "<u>ouvert", "<U>maj</U>"):
        assert inline(said) == (Run(said),), said
    assert inline("<u><u>x</u></u>") == (Run("<u>x", underline=True), Run("</u>"))


def test_values_are_written_the_french_way() -> None:
    assert display(1250.5, "amount_eur") == f"1{NBSP}250,50{NBSP}€"
    assert display(-3, "amount_eur") == f"-3,00{NBSP}€"
    assert display(12_500, "amount_keur") == f"12{NBSP}500{NBSP}k€"
    assert display(1234567, "integer") == f"1{NBSP}234{NBSP}567"
    assert display(0.155, "percent") == f"15,5{NBSP}%"
    assert display(2.5, "decimal") == "2,50"
    assert display(None, "text") == ""


def test_a_table_converts_aligns_and_totals() -> None:
    found = prepared(
        {
            "type": "table",
            "columns": [
                {"header": "Client"},
                {"header": "Date", "type": "date"},
                {"header": "Montant", "type": "amount_eur"},
                {"header": "Relances", "type": "integer"},
            ],
            "rows": [
                ["Dupont", "02/10/2026", "1 250,50", 2],
                ["Martin", "2026-10-09", 830],
            ],
            "totals": {"Montant": "sum", "Relances": "average", "Date": "max"},
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
        {"type": "heading", "text": "  "},
        {
            "type": "table",
            "columns": [{"header": "A"}, {"header": "A", "type": "integer"}],
            "rows": [["x", "pas un nombre", "de trop"]],
            "totals": {"A": "sum", "B": "sum"},
        },
        {"type": "paragraph", "text": "bip\x07"},
    )
    for part in (
        "bloc 1 (heading) : texte vide",
        "en-tête donné deux fois : A",
        "ligne 1 : 3 valeurs pour 2 colonnes",
        "'pas un nombre' n'est pas une valeur integer",
        "total sur une colonne inconnue 'B'",
        "caractère de contrôle U+0007 refusé",
    ):
        assert part in said, part


def test_tables_are_bounded() -> None:
    columns = [{"header": f"C{i}"} for i in range(9)]
    refused("9 colonnes, au-delà de 8", {"type": "table", "columns": columns, "rows": []})
    refused(
        "501 lignes, au-delà de 500",
        {"type": "table", "columns": [{"header": "A"}], "rows": [["x"]] * 501},
    )


def test_an_aggregate_must_fit_its_column() -> None:
    refused(
        "sum sur « Nom », colonne text : pas un nombre",
        {
            "type": "table",
            "columns": [{"header": "Nom"}],
            "rows": [["x"]],
            "totals": {"Nom": "sum"},
        },
    )


def test_warnings_do_not_refuse() -> None:
    found = prepared(
        {"type": "heading", "text": "A", "level": 1},
        {"type": "heading", "text": "B", "level": 3},
        {
            "type": "table",
            "columns": [{"header": "Taux", "type": "percent"}],
            "rows": [[15]],
        },
    )
    assert any("niveau sauté" in w for w in found.warnings)
    assert any("0.15 pour 15 %" in w for w in found.warnings)


def test_an_empty_document_is_refused() -> None:
    refused("document vide")


def test_a_page_break_is_no_longer_a_block() -> None:
    """Un saut de page est de la mise en page : le style le pose, pas le modèle de langage."""
    with pytest.raises(ToolError, match="saut_de_page") as caught:
        parse(DocumentCall, {"path": "s/a", "blocks": [{"type": "saut_de_page"}]})
    assert "'heading', 'paragraph', 'list', 'table', 'note', 'quote'" in str(caught.value)


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
            "type": "table",
            "columns": [
                {"header": "Nom"},
                {"header": "Quand", "type": "datetime"},
                {"header": "Montant", "type": "amount_keur"},
                {"header": "Vide", "type": "decimal"},
            ],
            "rows": [["a", "2026-10-02T09:30:00+00:00", 12], ["b", "02/10/2026 08:00", 3.5]],
            "totals": {"Nom": "count", "Quand": "min", "Montant": "max", "Vide": "sum"},
        }
    )
    [table] = found.blocks
    assert isinstance(table, TableOut)
    # Toutes les colonnes ont un agrégat : pas de libellé « Total ».
    assert table.total == ("2", "02/10/2026 08:00", f"12{NBSP}k€", "")
    # Une date-heure avec fuseau passe à l'heure de Paris.
    assert table.rows[0][1] == "02/10/2026 11:30"


def test_long_texts_and_lists_are_bounded() -> None:
    refused("au-delà de 300", {"type": "paragraph", "text": "x"}, title="t" * 301)
    refused("au-delà de 20000", {"type": "paragraph", "text": "x" * 20_001})
    items: list[Any] = [{"text": f"e{i}", "items": ["a", "b"]} for i in range(70)]
    refused("210 éléments, au-delà de 200", {"type": "list", "items": items})


def test_a_total_without_rows_is_not_written() -> None:
    found = prepared(
        {
            "type": "table",
            "columns": [{"header": "N", "type": "integer"}],
            "rows": [],
            "totals": {"N": "sum"},
        }
    )
    [table] = found.blocks
    assert isinstance(table, TableOut) and table.total is None
    assert any("aucune ligne" in w for w in found.warnings)


# --- Graphiques ------------------------------------------------------------------------

SALES: Final[dict[str, Any]] = {
    "type": "table",
    "columns": [
        {"header": "Client"},
        {"header": "Total HT", "type": "amount_eur"},
        {"header": "Total TTC", "type": "amount_eur"},
        {"header": "Factures", "type": "integer"},
    ],
    "rows": [["Barre", 17094, "18 034,17", 2], ["Lemaire", 13539.47, 16247.36, 2]],
    "totals": {"Total TTC": "sum"},
}


def test_a_chart_takes_its_data_from_the_table_before_it() -> None:
    found = prepared(
        SALES,
        {
            "type": "chart",
            "kind": "horizontal_bar",
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
        "horizontal_bar",
        "amount_eur",
        "TTC par client",
    )


def test_a_chart_with_inline_data() -> None:
    found = prepared(
        {
            "type": "chart",
            "kind": "line",
            "value_type": "percent",
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
            [{"type": "chart", "from_table": {"categories": "A", "values": ["B"]}}],
            "aucun tableau ne précède le graphique",
        ),
        (
            [
                SALES,
                {"type": "chart", "from_table": {"categories": "Client", "values": ["TVA"]}},
            ],
            r"colonne\(s\) inconnue\(s\) du tableau précédent : TVA",
        ),
        (
            [
                SALES,
                {
                    "type": "chart",
                    "from_table": {"categories": "Client", "values": ["Total TTC", "Factures"]},
                },
            ],
            "colonnes de nombres d'un même type",
        ),
        (
            [
                {
                    "type": "chart",
                    "categories": ["a", "b"],
                    "series": [{"name": "s", "values": [1]}],
                }
            ],
            "1 valeurs pour 2 libellés",
        ),
        (
            [
                {
                    "type": "chart",
                    "kind": "pie",
                    "categories": ["a", "b"],
                    "series": [{"name": "s", "values": [1, -1]}, {"name": "t", "values": [1, 1]}],
                }
            ],
            "un graphique pie n'a qu'une série",
        ),
        (
            [
                {
                    "type": "chart",
                    "kind": "pie",
                    "categories": [str(i) for i in range(13)],
                    "series": [{"name": "s", "values": [1] * 13}],
                }
            ],
            "13 secteurs, au-delà de 12",
        ),
        (
            [
                {
                    "type": "chart",
                    "value_type": "text",
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
        [{"type": "chart", "from_table": {"categories": "A", "values": ["B"]}}],
        [SALES, {"type": "chart", "from_table": {"categories": "Client", "values": ["TVA"]}}],
    ],
)
def test_a_chart_without_its_table_says_one_fault(blocks: list[dict[str, Any]]) -> None:
    # Ni « aucun libellé » ni « aucune série » en plus : elles découlent de la première.
    said = refused("chart", *blocks)
    assert said.count("\n- ") == 1, said


@pytest.mark.parametrize(
    ("chart", "said"),
    [
        ({"type": "chart"}, "soit de 'categories' et 'series', soit de 'from_table'"),
        ({"type": "chart", "categories": ["a"]}, "'categories' et 'series' vont ensemble"),
        (
            {
                "type": "chart",
                "value_type": "integer",
                "from_table": {"categories": "a", "values": ["b"]},
            },
            "'value_type' ne sert qu'aux données en ligne",
        ),
    ],
)
def test_chart_arguments_refused(chart: dict[str, Any], said: str) -> None:
    with pytest.raises(ToolError, match=said):
        parse(WithCharts, {"path": "s/a", "blocks": [chart]})


# --- Images ------------------------------------------------------------------------------

EVERY: Final = variant(DocumentCall, blocks=("chart", "image"))


def with_pictures(pictures: dict[str, Picture | str], *blocks: dict[str, Any]) -> Prepared:
    request = parse(EVERY, {"path": "s/a", "blocks": list(blocks)})
    assert isinstance(request, WriteArguments)
    return prepare(request, PARIS, pictures)


def test_png_and_jpeg_are_recognised_by_their_bytes() -> None:
    found = parse_picture(png(390, 844))
    assert (found.format, found.width, found.height, found.extension) == ("png", 390, 844, "png")
    assert found.digest == hashlib.sha256(found.data).hexdigest()
    for color, depth in ((0, 1), (3, 4), (4, 16), (6, 8)):
        assert parse_picture(png(33, 7, color=color, depth=depth)).width == 33
    assert parse_picture(png(333, 111, interlace=1)).height == 111
    assert parse_picture(png(10, 10, extra=chunk(b"acTL", bytes(8)))).animated
    photo = parse_picture(jpeg(4032, 3024, orientation=6))
    assert (photo.format, photo.width, photo.height, photo.orientation) == ("jpeg", 4032, 3024, 6)
    assert parse_picture(jpeg(800, 600, sof=0xC2)).orientation == 1
    # EXIF gros-boutiste, bourrage et marqueur isolé ; une orientation hors de 1 à 8 vaut 1.
    assert parse_picture(jpeg(800, 600, orientation=8, big=True, padded=True)).orientation == 8
    assert parse_picture(jpeg(800, 600, orientation=9)).orientation == 1
    exif_less = jpeg(800, 600, orientation=6).replace(b"II*\x00", b"XX*\x00")
    assert parse_picture(exif_less).orientation == 1
    assert dimensions(png(390, 844)) == (390, 844) and dimensions(jpeg(80, 60)) == (80, 60)
    for unknown in (b"GIF89a", jpeg(80, 60)[:30], png(10, 10)[:12] + b"tEXt"):
        assert dimensions(unknown) is None


@pytest.mark.parametrize(
    ("data", "said"),
    [
        (b"", "fichier vide"),
        (b"GIF89a" + bytes(20), "GIF : la convertir en PNG"),
        (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "WebP : Word ne l'affiche pas partout"),
        (b'<svg xmlns="http://www.w3.org/2000/svg"/>', "SVG : la convertir en PNG"),
        (b"%PDF-1.7", "format non reconnu : PNG ou JPEG attendu"),
        (png(10, 10)[:-30], "PNG tronqué"),
        (png(10, 10).replace(b"IDAT", b"IDAX", 1), "PNG abîmé"),
        (png(0, 10), "dimension nulle"),
        (png(10, 10, color=3, palette=False), "palette absente"),
        (
            png(10, 10)[:33] + chunk(b"IDAT", zlib.compress(b"\x00" * 5)) + chunk(b"IEND", b""),
            "pixels manquants",
        ),
        (png(8000, 6000, color=0, depth=1), "au-delà de 40 millions"),
        (jpeg(800, 600)[:-40], "JPEG tronqué"),
        (jpeg(800, 600, sof=0xC9), "codage que Word n'affiche pas"),
        (jpeg(0, 600), "dimension nulle"),
        (jpeg(8000, 6000), "8000 x 6000 pixels, au-delà de 40 millions"),
        (jpeg(800, 600)[:-2], "JPEG tronqué (fin de l'image absente)"),
        (png(10, 10)[:33] + chunk(b"IDAT", b"pas du zlib") + chunk(b"IEND", b""), "illisibles"),
        (b"\xff\xd8\xff\xe0\x00\x04\x00\x00\x00\x00\x00\x00", "JPEG abîmé (segment illisible)"),
        (b"\xff\xd8\xff\xd9\x00\x00", "fin d'image avant les pixels"),
        (b"\xff\xd8\xff\xda\x00\x02\xff\xd9", "dimensions absentes"),
        (b"\xff\xd8\xff\xc0\x00\x05\x08\x00\x01\xff\xd9", "JPEG abîmé (en-tête)"),
        (png(10, 10)[:8] + chunk(b"tEXt", b"a") + png(10, 10)[8:], "en-tête absent"),
        (png(10, 10)[:8] + chunk(b"IHDR", bytes(12)) + png(10, 10)[33:], "PNG abîmé (en-tête)"),
    ],
)
def test_pictures_refused(data: bytes, said: str) -> None:
    with pytest.raises(PictureError, match=re.escape(said)):
        parse_picture(data)


def test_an_image_keeps_its_proportions_within_the_page() -> None:
    wide, tall = parse_picture(png(1600, 900)), parse_picture(png(390, 844))
    found = with_pictures(
        {"m/large.png": wide, "m/haute.png": tall},
        {"type": "image", "path": "m/large.png", "caption": "Écran **1**", "alt": "Accueil"},
        {"type": "image", "path": "m/large.png", "width": "half", "caption": "Bis"},
        {"type": "image", "path": "m/large.png", "width": "small", "alt": "Logo"},
        {"type": "image", "path": "m/haute.png", "caption": "Téléphone"},
    )
    images = [b for b in found.blocks if isinstance(b, ImageOut)]
    assert [(i.width, i.height) for i in images] == [
        (17_000, 9_562),
        (8_500, 4_781),
        (4_000, 2_250),
        # Bornée à la hauteur de la page : la largeur suit les proportions.
        (9_704, 21_000),
    ]
    assert images[0].caption == (Run("Écran "), Run("1", bold=True))
    # Le texte alternatif : alt, ou la légende.
    assert [i.alt for i in images] == ["Accueil", "Bis", "Logo", "Téléphone"]
    assert images[0].source == "m/large.png" and found.warnings == ()


def test_image_warnings() -> None:
    found = with_pictures(
        {
            "m/logo.png": parse_picture(png(120, 60)),
            "m/photo.jpg": parse_picture(jpeg(4032, 3024, orientation=6)),
            "m/anim.png": parse_picture(png(800, 600, extra=chunk(b"acTL", bytes(8)))),
        },
        {"type": "image", "path": "m/logo.png", "caption": "Logo"},
        {"type": "image", "path": "m/photo.jpg", "alt": "Chantier"},
        {"type": "image", "path": "m/anim.png", "width": "small"},
    )
    said = "\n".join(found.warnings)
    assert "bloc 1 (image) : m/logo.png, 120 x 60 pixels sur 17,0 cm (18 pixels par pouce)" in said
    assert "bloc 2 (image) : m/photo.jpg est une photo prise de côté (orientation EXIF 6)" in said
    assert "bloc 3 (image) : m/anim.png est animée" in said
    assert "bloc 3 (image) : ni alt ni caption" in said


def test_image_faults_join_the_document_faults() -> None:
    said = refused_with(
        {"m/a.png": "Droit read refusé sur m/a.png (dossier m : write)", "m/b.png": "x"},
        {"type": "heading", "text": ""},
        {"type": "image", "path": "m/a.png"},
        {"type": "image", "path": "m/inconnue.png"},
    )
    assert said.splitlines() == [
        "Document refusé :",
        "- bloc 1 (heading) : texte vide",
        "- bloc 2 (image) : Droit read refusé sur m/a.png (dossier m : write)",
        "- bloc 3 (image) : image non lue",
    ]


def test_images_are_bounded_per_document() -> None:
    small = parse_picture(png(10, 10))
    heavy = Picture("png", 10, 10, bytes(MAX_IMAGES_BYTES // 2 + 1), "a")
    heavier = Picture("png", 10, 10, bytes(MAX_IMAGES_BYTES // 2 + 1), "b")
    said = refused_with(
        {"m/a.png": heavy, "m/b.png": heavier, "m/c.png": small},
        *([{"type": "image", "path": "m/c.png", "alt": "c"}] * (MAX_IMAGES + 1)),
        {"type": "image", "path": "m/a.png", "alt": "a"},
        {"type": "image", "path": "m/b.png", "alt": "b"},
    )
    assert f"{MAX_IMAGES + 3} images, au-delà de {MAX_IMAGES}" in said
    assert f"au-delà de {MAX_IMAGES_BYTES}" in said


def refused_with(pictures: dict[str, Picture | str], *blocks: dict[str, Any]) -> str:
    with pytest.raises(ToolError) as caught:
        with_pictures(pictures, *blocks)
    return str(caught.value)


@pytest.mark.parametrize(
    ("image", "said"),
    [
        ({"type": "image"}, "path"),
        ({"type": "image", "path": "m/a.png", "width": "12cm"}, "width"),
        ({"type": "image", "path": "m/a.png", "height": 3}, "height"),
    ],
)
def test_image_arguments_refused(image: dict[str, Any], said: str) -> None:
    with pytest.raises(ToolError, match=said):
        parse(EVERY, {"path": "s/a", "blocks": [image]})


def test_variants_add_only_what_is_asked() -> None:
    assert variant(DocumentCall) is DocumentCall
    assert variant(DocumentCall, blocks=("chart",)) is WithCharts
    # L'ordre des noms ne fait pas un autre appel.
    assert variant(DocumentCall, blocks=("image", "chart")) is EVERY
    examples: dict[str, dict[str, Any]] = {
        "image": {"type": "image", "path": "m/a.png"},
        "chart": {"type": "chart", "categories": ["a"], "series": [{"name": "s", "values": [1]}]},
        "signature": {"type": "signature", "signers": ["Le client"]},
        "toc": {"type": "toc"},
        "checkboxes": {"type": "list", "checkboxes": True, "items": ["[x] a"]},
        "fields": {"type": "fields", "items": [["Nom", ""]]},
        "code": {"type": "code", "text": "x = 1"},
    }
    for chosen in ((), ("chart",), ("image", "toc"), ("signature", "checkboxes"), tuple(examples)):
        model = variant(DocumentCall, blocks=chosen)
        for name, block in examples.items():
            try:
                parse(model, {"path": "s/a", "blocks": [block]})
            except ToolError:
                assert name not in chosen
            else:
                assert name in chosen


# --- Modèles de document ---------------------------------------------------------------

RAPPORT: Final = """description: Rapport d'intervention chez un client.
instructions: |
  Sobre et factuel.
sections:
  Contexte:
    instructions: |
      Le client, le site, la demande.
  Constat:
  Photos:
    optional: true
  Travaux réalisés:
  Conditions:
    text: |
      **Garantie** : un an,
      pièces et main-d'œuvre.

      Rapport établi sur place.
"""


def test_a_template_is_its_sections() -> None:
    found = parse_template("rapport", RAPPORT)
    assert found.description == "Rapport d'intervention chez un client."
    assert found.instructions == "Sobre et factuel.\n"
    assert [(s.name, s.position, s.optional, s.fixed) for s in found.sections] == [
        ("Contexte", 1, False, False),
        ("Constat", 2, False, False),
        ("Photos", 3, True, False),
        ("Travaux réalisés", 4, False, False),
        ("Conditions", 5, False, True),
    ]
    assert found.outline() == (
        "Contexte · Constat · Photos (optional) · Travaux réalisés · Conditions (fixed)"
    )
    assert found.text == RAPPORT and found.style is None
    assert found.section("2. CONSTAT") is not None and found.section("Annexe") is None
    assert fixed_paragraphs(found.sections[4].spec.text or "") == [
        "**Garantie** : un an,\npièces et main-d'œuvre.",
        "Rapport établi sur place.",
    ]


SECTIONS: Final = "sections:\n  A:\n  B:\n"


@pytest.mark.parametrize(
    ("name", "text", "said"),
    [
        ("Rapport", SECTIONS, "minuscules, chiffres et tirets"),
        ("none", SECTIONS, "pas « none »"),
        ("r", "", "un modèle a des sections"),
        ("r", "description: x\n", "un modèle a des sections"),
        ("r", "---\ndescription: x\n---\n# A\n", "un modèle est un fichier YAML depuis 2.1.0"),
        ("r", "Consignes.\n\n# Contexte\nLe client.\n", "chaque titre « # Section » devient"),
        ("r", "sections:\n  Constat:\n  2. constat:\n", "section donnée deux fois"),
        ("r", "sections:\n  A:\n  A:\n", "clé donnée deux fois : A"),
        ("r", "sections: {}\n", "sections — Dictionary should have at least 1 item"),
        ("r", "sections:\n  A: {boxx: true}\n", "sections.A.boxx — Extra inputs"),
        ("r", "sections:\n  A:\n    instructions: Qui joindre : le standard\n", "entre guillemets"),
        ("r", "x" * 20_001, "au-delà de 20000"),
        ("r", "sections:\n  A: {beside: true}\n", "la première n'en a pas"),
        ("r", "sections:\n  A:\n  B: {beside: true, columns: 2}\n", "ni columns, ni landscape"),
        ("r", "sections:\n  A:\n  B: {beside: true, page_break: true}\n", "vont à la première"),
        ("r", "sections:\n  A: {width: 50}\n  B:\n", "width ne sert qu'à des sections côte"),
        ("r", "sections:\n  A: {width: 60}\n  B: {beside: true}\n", "à toutes les sections"),
        ("r", "sections:\n  A: {width: 60}\n  B: {beside: true, width: 30}\n", "font 100"),
        (
            "r",
            "sections:\n  A:\n  B: {beside: true}\n  C: {beside: true}\n  D: {beside: true}\n",
            "3 sections côte à côte au plus",
        ),
        ("r", "sections:\n  A:\n  B: {cover: true}\n", "va à la première section, « A »"),
        ("r", "sections:\n  A: {cover: true, box: true}\n", "ni landscape, ni columns, ni box"),
        ("r", "sections:\n  A: {cover: true}\n  B: {beside: true}\n", "« A » est côte à côte"),
        ("r", "sections:\n  A: {text: x, instructions: y}\n", "pas les deux"),
        ("r", "sections:\n  A: {text: x, optional: true}\n", "toujours écrite"),
        ("r", "sections:\n  A: {styles: {heading1: Titre}}\n", "n'est pas un nom de style"),
        ("r", "sections:\n  A: {styles: {header: x}}\n", "sections.A.styles.header"),
    ],
)  # fmt: skip
def test_templates_refused(name: str, text: str, said: str) -> None:
    with pytest.raises(ValueError, match=re.escape(said)):
        parse_template(name, text)


@pytest.mark.parametrize(
    ("heading", "found"),
    [
        ("1. Contexte et Objectifs", "contexte et objectifs"),
        ("2.3 - Périmètre", "perimetre"),
        ("4) DONNÉES", "donnees"),
        ("a) Rôles", "roles"),
        ("IV. Intégrations", "integrations"),
        ("**Constat** :", "constat"),
        ("<u>Client</u>", "client"),
        ("2. **<u>Détail</u>** du devis", "detail du devis"),
        ("Mix : produits", "mix : produits"),
    ],
)
def test_section_names_are_compared_loosely(heading: str, found: str) -> None:
    assert template_key(heading) == found


def written(model: type[DocumentCall], **arguments: Any) -> WriteArguments:
    request = parse(model, {"path": "s/a", **arguments})
    assert isinstance(request, WriteArguments)
    return request


def test_prepare_places_the_sections_of_the_template() -> None:
    template = parse_template("rapport", RAPPORT)
    model = variant(DocumentCall, templates=("rapport",))
    paragraph: list[Any] = [{"type": "paragraph", "text": "Fait."}]
    request = written(
        model,
        template="rapport",
        title="Rapport",
        sections={"travaux realises": paragraph, "1. Contexte": paragraph, "Constat": paragraph},
    )
    found = prepare(request, PARIS, template=template)
    assert found.template == "rapport" and found.sections is not None
    # Dans l'ordre du modèle, quel que soit l'ordre de l'appel ; le titre de chaque section
    # écrit par l'outil ; Photos (facultative) absente ; le texte fixe des conditions.
    assert [(s.position, s.name, s.fixed) for s in found.sections] == [
        (1, "Contexte", False),
        (2, "Constat", False),
        (4, "Travaux réalisés", False),
        (5, "Conditions", True),
    ]
    titles = [b for b in found.blocks if isinstance(b, HeadingOut)]
    assert [(t.level, t.section, t.runs[0].text) for t in titles][:2] == [
        (1, 1, "Contexte"),
        (1, 2, "Constat"),
    ]
    assert found.sections[3].blocks == (
        ParagraphOut((Run("Garantie", bold=True), Run(" : un an,\npièces et main-d'œuvre."))),
        ParagraphOut((Run("Rapport établi sur place."),)),
    )
    assert isinstance(found.blocks[0], TitleOut) and len(found.blocks) == 1 + 4 + 3 + 2


def test_prepare_says_what_the_sections_miss() -> None:
    template = parse_template("rapport", RAPPORT)
    model = variant(DocumentCall, templates=("rapport",))
    paragraph: list[Any] = [{"type": "paragraph", "text": "Fait."}]
    request = written(
        model,
        template="rapport",
        sections={
            "Contexte": [{"type": "heading", "text": "Le site"}],
            "Annexe": paragraph,
            "Conditions": paragraph,
            "Photos": [],
            "contexte": paragraph,
        },
    )
    with pytest.raises(ToolError) as caught:
        prepare(request, PARIS, template=template)
    assert str(caught.value).splitlines() == [
        "Document refusé :",
        "- modèle rapport : section inconnue « Annexe »",
        "- modèle rapport : « Conditions » est une section au texte fixe, écrite par l'outil : "
        "ne pas la remplir",
        "- modèle rapport : « Photos » est vide — la retirer (elle est facultative)",
        "- modèle rapport : section donnée deux fois : « Contexte » et « contexte »",
        "- modèle rapport : section(s) absente(s) : « Constat », « Travaux réalisés »",
        "- modèle rapport, sections attendues : Contexte · Constat · Photos (optional) · "
        "Travaux réalisés · Conditions (fixed)",
        "- section « Contexte », bloc 1 (heading) : le niveau 1 est le titre de la section, que "
        "l'outil écrit ; dans une section, les titres sont de niveau 2 ou 3",
    ]


def test_a_section_without_its_title() -> None:
    template = parse_template("note", "sections:\n  À:\n    show_title: false\n  Message:\n")
    model = variant(DocumentCall, templates=("note",))
    paragraph: list[Any] = [{"type": "paragraph", "text": "x"}]
    found = prepare(
        written(model, template="note", sections={"À": paragraph, "Message": paragraph}),
        PARIS,
        template=template,
    )
    assert found.sections is not None and found.sections[0].title is None
    assert [type(b).__name__ for b in found.blocks] == [
        "ParagraphOut",
        "HeadingOut",
        "ParagraphOut",
    ]


def test_templates_add_an_action_and_two_fields() -> None:
    model = variant(DocumentCall, templates=("rapport", "devis"))
    schema = input_schema(model)
    properties = cast(dict[str, Any], schema["properties"])
    assert properties["template"]["anyOf"][0]["enum"] == ["rapport", "devis", "none"]
    assert properties["sections"]["anyOf"][0]["type"] == "object"
    assert "required" not in schema
    blocks: list[Any] = [{"type": "paragraph", "text": "x"}]
    assert parse(model, {"action": "template", "template": "devis"}) == TemplateArguments(
        name="devis"
    )
    free = parse(model, {"path": "s/a", "blocks": blocks, "template": "none"})
    assert isinstance(free, WriteArguments) and free.template == "none"
    following = parse(model, {"path": "s/a", "sections": {"A": blocks}, "template": "devis"})
    assert isinstance(following, WriteArguments) and following.sections is not None
    assert following.all_blocks() == following.sections["A"]
    for arguments, said in (
        ({"path": "s/a", "blocks": blocks}, "write attend 'template'"),
        ({"path": "s/a", "blocks": blocks, "template": "facture"}, "template"),
        ({"blocks": blocks, "template": "none"}, "write attend 'path'"),
        ({"path": "s/a", "blocks": blocks, "template": "devis"}, "s'écrit par 'sections'"),
        ({"path": "s/a", "template": "devis"}, "write attend 'blocks'"),
        ({"path": "s/a", "sections": {"A": blocks}, "template": "none"}, "par 'blocks', pas"),
        ({"action": "template"}, "action template attend 'template'"),
        ({"action": "template", "template": "none"}, "action template attend 'template'"),
        ({"action": "template", "template": "devis", "path": "s/a"}, "path ne sert pas à template"),
        ({"action": "template", "template": "devis", "title": "T"}, "title ne sert pas à template"),
        ({"action": "read", "path": "s/a", "template": "devis"}, "template ne sert pas à read"),
        ({"action": "read", "path": "s/a", "sections": {"A": blocks}}, "sections ne sert pas"),
    ):  # fmt: skip
        with pytest.raises(ToolError, match=re.escape(said)):
            parse(model, cast(dict[str, Any], arguments))


def test_proposed_styles_are_a_field_of_their_blocks() -> None:
    model = variant(DocumentCall, styles={"note": ["important_note"], "heading": ["big"]})
    schema = json.dumps(input_schema(model))
    assert '"important_note"' in schema and '"big"' in schema
    note = {"type": "note", "text": "x", "style": "important_note"}
    request = written(model, blocks=[note])
    with pytest.raises(ToolError, match="Input should be 'important_note'"):
        written(model, blocks=[{**note, "style": "other"}])
    with pytest.raises(ToolError, match="Extra inputs"):
        written(model, blocks=[{"type": "paragraph", "text": "x", "style": "important_note"}])
    # Permis par le schéma, mais pas proposé à ce document : prepare le dit.
    with pytest.raises(ToolError, match=r"style « important_note » : pas proposé pour un bloc "):
        prepare(request, PARIS)
    found = prepare(request, PARIS, allowed={"note": {"important_note": 0}})
    assert found.blocks == (NoteOut(None, (Run("x"),), "important_note"),)
    heading = written(model, blocks=[{"type": "heading", "text": "T", "level": 2, "style": "big"}])
    with pytest.raises(ToolError, match="un titre de niveau 1, pas 2"):
        prepare(heading, PARIS, allowed={"heading": {"big": 1}})
    # Sans style proposé, le schéma est celui d'avant.
    assert variant(DocumentCall, styles={"note": []}) is DocumentCall


def test_windows_line_endings_are_line_breaks() -> None:
    """B-3 : ``prepare`` ramène \\r\\n et \\r à \\n, pour ``word`` comme pour ``pdf``."""
    found = prepared(
        {"type": "paragraph", "text": "un\r\ndeux\rtrois"},
        {"type": "note", "title": "A\r\nB", "text": "c\r\nd"},
        {
            "type": "chart",
            "categories": ["x\r\ny"],
            "series": [{"name": "s\r\nt", "values": [1]}],
        },
        title="T\r\nU",
    )
    said = repr(found)
    assert "\\r" not in said
    assert "un\\ndeux\\ntrois" in said and "T\\nU" in said


# --- Le texte enrichi (lot 5) -------------------------------------------------------------


def rich_prepared(*blocks: dict[str, Any], template: Any = None, **arguments: Any) -> Prepared:
    """Le document préparé, champs, code et listes à cocher compris ; avec ``template``,
    par sections."""
    names = (template.name,) if template is not None else ()
    model = variant(DocumentCall, blocks=("fields", "code", "checkboxes"), templates=names)
    if template is not None:
        arguments["template"] = template.name
    request = written(model, **({"blocks": list(blocks)} if blocks else {}), **arguments)
    return prepare(request, PARIS, template=template)


def rich_refused(match: str, *blocks: dict[str, Any], **arguments: Any) -> str:
    with pytest.raises(ToolError, match=match) as caught:
        rich_prepared(*blocks, **arguments)
    return str(caught.value)


def test_links_notes_and_references_in_a_text() -> None:
    found = rich_prepared(
        {"type": "heading", "text": "Objet"},
        {
            "type": "paragraph",
            "text": "Voir [le site](https://example.com/a?b=1) et **[le guide](mailto:a@b.fr)**"
            ", une note^[Par **virement**, voir [ici](http://x.fr).] et §[travaux prévus]. "
            "Tels quels : \\^[pas une note], \\§[pas un renvoi], [voir](page 2).",
        },
        {"type": "heading", "text": "Travaux *prévus*", "level": 2},
    )
    paragraph = found.blocks[1]
    assert isinstance(paragraph, ParagraphOut)
    assert paragraph.runs == (
        Run("Voir "),
        Run("le site", link="https://example.com/a?b=1"),
        Run(" et "),
        Run("le guide", bold=True, link="mailto:a@b.fr"),
        Run(", une note"),
        Run("", note=(Run("Par "), Run("virement", bold=True), Run(", voir "),
                      Run("ici", link="http://x.fr"), Run("."))),
        Run(" et "),
        # Le renvoi : le titre visé, tel qu'il s'écrit, et son rang parmi les titres.
        Run("Travaux prévus", ref=2),
        Run(". Tels quels : ^[pas une note], §[pas un renvoi], [voir](page 2)."),
    )  # fmt: skip
    assert referenced(found.blocks) == {2}


@pytest.mark.parametrize(
    ("text", "said"),
    [
        ("[x](ftp://a.fr)", "lien 'ftp://a.fr' : seuls https, http et mailto sont permis"),
        ("une ^[note sans fin", "« ^[ » sans « ] »"),
        ("^[une ^[note] dans une note]", "pas de note dans une note"),
        ("^[" + "x" * 501 + "]", "501 caractères, au-delà de 500"),
        ("§[Inconnu]", "renvoi §[Inconnu] : aucun titre de ce nom (titres : Objet · Objet)"),
        ("§[objet]", "renvoi §[objet] : deux titres de ce nom"),
    ],
)
def test_rich_text_faults_are_said(text: str, said: str) -> None:
    rich_refused(
        re.escape(said),
        {"type": "heading", "text": "Objet"},
        {"type": "heading", "text": "Objet"},
        {"type": "paragraph", "text": text},
    )


def test_titles_captions_and_labels_take_emphasis_only() -> None:
    said = rich_refused(
        "Document refusé",
        {"type": "heading", "text": "Voir ^[note]"},
        {"type": "note", "title": "§[Voir]", "text": "x"},
        {
            "type": "table",
            "columns": [{"header": "[A](https://a.fr)"}],
            "rows": [["x"]],
            "caption": "Une ^[note]",
        },
        {"type": "fields", "items": [["Nom^[x]", "y"]]},
        title="Le [titre](https://a.fr)",
    )
    for where in ("title", "bloc 1 (heading)", "bloc 2 (note), title", "bloc 3 (table), caption",
                  "bloc 4 (fields), paire 1, libellé"):  # fmt: skip
        assert f"{where} : ni lien, ni note" in said, where


def test_notes_are_bounded_per_document() -> None:
    text = " ".join(["x^[n]"] * (MAX_NOTES + 1))
    rich_refused(f"{MAX_NOTES + 1} notes de bas de page, au-delà de {MAX_NOTES}",
                 {"type": "paragraph", "text": text})  # fmt: skip


def test_references_follow_the_sections_of_a_template() -> None:
    template = parse_template(
        "note", "sections:\n  À:\n    show_title: false\n  Message:\n  Suite:\n"
    )
    paragraph = [{"type": "paragraph", "text": "Voir §[Message] et §[Détail]."}]
    found = rich_prepared(
        template=template,
        sections={
            "À": [{"type": "paragraph", "text": "x"}],
            "Message": [{"type": "heading", "text": "Détail", "level": 2}],
            "Suite": paragraph,
        },
    )
    # Les titres dans l'ordre : Message (imprimé), Détail, Suite ; « À » ne s'imprime pas.
    last = found.blocks[-1]
    assert isinstance(last, ParagraphOut)
    assert [run.ref for run in last.runs if run.ref] == [1, 2]
    hidden = [{"type": "paragraph", "text": "§[À]"}]
    sections = {"À": hidden, "Message": [{"type": "paragraph", "text": "y"}], "Suite": hidden}
    with pytest.raises(ToolError, match="cette section n'imprime pas son titre"):
        rich_prepared(template=template, sections=sections)


def test_lists_have_three_levels() -> None:
    found = rich_prepared(
        {"type": "list", "items": ["a", {"text": "b", "items": [{"text": "c", "items": ["d"]}]}]},
        {"type": "list", "checkboxes": True, "items": [{"text": "[x] a", "items": ["[ ] b"]}]},
    )
    deep, checks = found.blocks
    assert isinstance(deep, ListOut) and isinstance(checks, ListOut)
    assert deep.entries == (
        Entry((Run("a"),)),
        Entry((Run("b"),), (Entry((Run("c"),), (Entry((Run("d"),)),)),)),
    )
    assert checks.entries == (Entry((Run("a"),), (Entry((Run("b"),)),), checked=True),)
    rich_refused(
        "une liste à cocher a deux niveaux",
        {"type": "list", "checkboxes": True,
         "items": [{"text": "[x] a", "items": [{"text": "[ ] b", "items": ["[ ] c"]}]}]},
    )  # fmt: skip
    # Un quatrième niveau ne passe pas le schéma.
    with pytest.raises(ToolError):
        rich_prepared({"type": "list", "items": [{"text": "a", "items": [
            {"text": "b", "items": [{"text": "c", "items": ["d"]}]}]}]})  # fmt: skip


def test_fields_and_code() -> None:
    found = rich_prepared(
        {"type": "fields", "items": [["Client", "M. **Dupont**^[Gérant.]"], ["Signature", ""]]},
        {"type": "code", "text": "\n \n  x = 1\n\n\ty = 2  \n\n"},
    )
    fields, code = found.blocks
    assert fields == FieldsOut(
        (
            ("Client", (Run("M. "), Run("Dupont", bold=True), Run("", note=(Run("Gérant."),)))),
            ("Signature", ()),
        )
    )
    # Les lignes vides du début et de la fin retirées ; le reste tel quel.
    assert code == CodeOut("  x = 1\n\n\ty = 2")
    rich_refused("201 lignes, au-delà de 200", {"type": "code", "text": "x\n" * 201})
    rich_refused(r"bloc 1 \(code\) : texte vide", {"type": "code", "text": " \n "})
    rich_refused(
        "31",
        {"type": "fields", "items": [["a", "b"]] * 31},
    )


def test_text_cells_of_a_table_are_rich() -> None:
    found = rich_prepared(
        {"type": "heading", "text": "Objet"},
        {
            "type": "table",
            "columns": [{"header": "Désignation"}, {"header": "Prix", "type": "amount_eur"}],
            "rows": [["**Pose**, voir [fiche](https://a.fr)^[Du fabricant.] et §[Objet]", 10]],
        },
    )
    table = found.blocks[1]
    assert isinstance(table, TableOut)
    assert table.cells[0] == (
        (Run("Pose", bold=True), Run(", voir "), Run("fiche", link="https://a.fr"),
         Run("", note=(Run("Du fabricant."),)), Run(" et "), Run("Objet", ref=1)),
        (Run(f"10,00{NBSP}€"),),
    )  # fmt: skip
    # Le texte seul, pour la largeur des colonnes et ce qui n'a pas d'emphase.
    assert table.rows[0][0] == "Pose, voir fiche et Objet"
    assert referenced(found.blocks) == {1}
