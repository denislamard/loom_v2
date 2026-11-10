# SPDX-License-Identifier: Apache-2.0
"""Le socle de ``loom.word`` et ``loom.pdf`` : arguments, contenu validé, valeurs à la française.

Sans dépendance : ce que ces essais couvrent vaut pour les deux formats, qui
ne font ensuite que poser le contenu préparé sur la page.
"""

import hashlib
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
    NBSP,
    BreakOut,
    ChartOut,
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
)
from loom_ia.tools_builtin._document.picture import Picture, PictureError, dimensions
from loom_ia.tools_builtin._document.picture import parse as parse_picture
from loom_ia.tools_builtin._templates import key as template_key
from loom_ia.tools_builtin._templates import parse as parse_template
from loom_ia.tools_builtin._templates import plan_faults
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


# --- Images ------------------------------------------------------------------------------

EVERY: Final = variant(DocumentCall, charts=True, images=True)


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
        {"type": "image", "path": "m/large.png", "width": "moitie", "caption": "Bis"},
        {"type": "image", "path": "m/large.png", "width": "petite", "alt": "Logo"},
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
        {"type": "image", "path": "m/anim.png", "width": "petite"},
    )
    said = "\n".join(found.warnings)
    assert "bloc 1 (image) : m/logo.png, 120 x 60 pixels sur 17,0 cm (18 pixels par pouce)" in said
    assert "bloc 2 (image) : m/photo.jpg est une photo prise de côté (orientation EXIF 6)" in said
    assert "bloc 3 (image) : m/anim.png est animée" in said
    assert "bloc 3 (image) : ni alt ni caption" in said


def test_image_faults_join_the_document_faults() -> None:
    said = refused_with(
        {"m/a.png": "Droit read refusé sur m/a.png (dossier m : write)", "m/b.png": "x"},
        {"type": "titre", "text": ""},
        {"type": "image", "path": "m/a.png"},
        {"type": "image", "path": "m/inconnue.png"},
    )
    assert said.splitlines() == [
        "Document refusé :",
        "- bloc 1 (titre) : texte vide",
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
    assert variant(DocumentCall, charts=False, images=False) is DocumentCall
    assert variant(DocumentCall, charts=True, images=False) is WithCharts
    image: dict[str, Any] = {"type": "image", "path": "m/a.png"}
    chart: dict[str, Any] = {
        "type": "graphique",
        "categories": ["a"],
        "series": [{"name": "s", "values": [1]}],
    }
    for charts, images in ((False, False), (True, False), (False, True), (True, True)):
        model = variant(DocumentCall, charts=charts, images=images)
        for block, allowed in ((image, images), (chart, charts)):
            try:
                parse(model, {"path": "s/a", "blocks": [block]})
            except ToolError:
                assert not allowed
            else:
                assert allowed


# --- Modèles de document ---------------------------------------------------------------

RAPPORT: Final = """---
description: Rapport d'intervention chez un client.
---
Consignes générales : sobre et factuel.

# Contexte
Le client, le site, la demande.

# Constat
## Une sous-partie : pas une section
```
# Pas une section non plus : dans un bloc de code
```

# Photos (facultatif)
# Travaux réalisés
# Suite à donner
"""


def test_a_template_is_its_headings() -> None:
    found = parse_template("rapport", RAPPORT)
    assert found.description == "Rapport d'intervention chez un client."
    assert [(s.title, s.optional) for s in found.sections] == [
        ("Contexte", False),
        ("Constat", False),
        ("Photos", True),
        ("Travaux réalisés", False),
        ("Suite à donner", False),
    ]
    assert found.outline() == (
        "Contexte · Constat · Photos (facultatif) · Travaux réalisés · Suite à donner"
    )
    assert found.text == RAPPORT
    # Sans titre, un modèle n'a que des consignes : aucun plan à vérifier.
    assert parse_template("libre", "Écrire court.").sections == ()


@pytest.mark.parametrize(
    ("name", "text", "said"),
    [
        ("Rapport", "# A", "minuscules, chiffres et tirets"),
        ("aucun", "# A", "pas « aucun »"),
        ("r", "", "vide"),
        ("r", "---\ndescription: x\n# A", "jamais fermé"),
        ("r", "---\nauteur: moi\n---\n# A", "seule « description » est lue"),
        ("r", "# Constat\n# 2. constat", "section donnée deux fois"),
        ("r", "#   \n", "titre de section vide"),
        ("r", "x" * 20_001, "au-delà de 20000"),
    ],
)
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
def test_headings_are_compared_loosely(heading: str, found: str) -> None:
    assert template_key(heading) == found


def test_the_plan_of_a_document() -> None:
    template = parse_template("rapport", RAPPORT)
    assert (
        plan_faults(template, ["1. Contexte", "2. Constat", "Travaux réalisés", "Suite à donner"])
        == []
    )
    assert plan_faults(
        template, ["Contexte", "Annexe", "Travaux réalisés", "Constat", "Constat"]
    ) == [
        "section hors du modèle « Annexe »",
        "section donnée deux fois « Constat »",
        "section(s) absente(s) : « Suite à donner »",
        "« Travaux réalisés » avant « Constat » : le modèle les met dans l'autre ordre",
    ]
    assert plan_faults(parse_template("libre", "Écrire court."), ["N'importe quoi"]) == []


def test_prepare_checks_the_plan_of_the_template() -> None:
    template = parse_template("rapport", RAPPORT)
    request = parse(
        DocumentCall, {"path": "s/a", "blocks": [{"type": "titre", "text": "Contexte"}]}
    )
    assert isinstance(request, WriteArguments)
    with pytest.raises(ToolError) as caught:
        prepare(request, PARIS, template=template)
    assert str(caught.value).splitlines() == [
        "Document refusé :",
        "- modèle rapport : section(s) absente(s) : « Constat », « Travaux réalisés », "
        "« Suite à donner »",
        "- modèle rapport, plan attendu (titres de niveau 1, dans l'ordre) : Contexte · Constat · "
        "Photos (facultatif) · Travaux réalisés · Suite à donner",
    ]
    whole: list[Any] = [
        {"type": "titre", "text": t}
        for t in ("Contexte", "Constat", "Travaux réalisés", "Suite à donner")
    ]
    request = parse(DocumentCall, {"path": "s/a", "blocks": whole})
    assert isinstance(request, WriteArguments)
    assert prepare(request, PARIS, template=template).template == "rapport"
    assert prepare(request, PARIS).template is None


def test_templates_add_an_action_and_a_field() -> None:
    model = variant(DocumentCall, charts=False, images=False, templates=("rapport", "devis"))
    schema = input_schema(model)
    properties = cast(dict[str, Any], schema["properties"])
    assert properties["template"]["anyOf"][0]["enum"] == ["rapport", "devis", "aucun"]
    assert "required" not in schema
    blocks: list[Any] = [{"type": "paragraphe", "text": "x"}]
    assert parse(model, {"action": "template", "template": "devis"}) == TemplateArguments(
        name="devis"
    )
    written = parse(model, {"path": "s/a", "blocks": blocks, "template": "aucun"})
    assert isinstance(written, WriteArguments) and written.template == "aucun"
    for arguments, said in (
        ({"path": "s/a", "blocks": blocks}, "write attend 'template'"),
        ({"path": "s/a", "blocks": blocks, "template": "facture"}, "template"),
        ({"blocks": blocks, "template": "aucun"}, "write attend 'path'"),
        ({"action": "template"}, "action template attend 'template'"),
        ({"action": "template", "template": "aucun"}, "action template attend 'template'"),
        ({"action": "template", "template": "devis", "path": "s/a"}, "path ne sert pas à template"),
        ({"action": "template", "template": "devis", "title": "T"}, "title ne sert pas à template"),
        ({"action": "read", "path": "s/a", "template": "devis"}, "template ne sert pas à read"),
    ):
        with pytest.raises(ToolError, match=re.escape(said)):
            parse(model, cast(dict[str, Any], arguments))


def test_windows_line_endings_are_line_breaks() -> None:
    """B-3 : ``prepare`` ramène \\r\\n et \\r à \\n, pour ``word`` comme pour ``pdf``."""
    found = prepared(
        {"type": "paragraphe", "text": "un\r\ndeux\rtrois"},
        {"type": "note", "title": "A\r\nB", "text": "c\r\nd"},
        {
            "type": "graphique",
            "categories": ["x\r\ny"],
            "series": [{"name": "s\r\nt", "values": [1]}],
        },
        title="T\r\nU",
    )
    said = repr(found)
    assert "\\r" not in said
    assert "un\\ndeux\\ntrois" in said and "T\\nU" in said
