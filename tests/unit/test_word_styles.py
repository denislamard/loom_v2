# SPDX-License-Identifier: Apache-2.0
"""Styles de document de ``loom.word`` : la charte de l'outil, le style et la mise en page
d'un modèle.

Un fichier de style est une liste de styles nommés (``text``, ``heading1``…) et quelques
réglages ; ``default.yaml`` en est le point de départ, que la charte puis le style du
modèle complètent, propriété par propriété. Les fichiers sont lus au chargement de la
config (``load_config``), ou donnés en Python (``spec``). Les documents écrits sont
relus dans leur XML, et par l'outil (``action: read``) : ce que le style pose ne revient
pas, le plan du modèle oui. LibreOffice, s'il est là, vérifie le rendu.
"""

import ast
import hashlib
import io
import json
import re
import subprocess
import zipfile
from pathlib import Path
from typing import Any, Final
from xml.etree import ElementTree

import pytest
from conftest import ConfigFactory, demo_agent
from image_files import png
from pydantic import ValidationError

from loom_ia.access.cli import main
from loom_ia.agents.spec import PythonTool, builtin_settings
from loom_ia.config import ConfigError, load_config
from loom_ia.core.model import DEFAULT_TENANT, RunId, SessionId
from loom_ia.core.ports import ToolContext, ToolError
from loom_ia.tools_builtin import BuiltinContext
from loom_ia.tools_builtin import word as word_package
from loom_ia.tools_builtin._style_params import (
    BASES,
    DEFAULT_FILE,
    PROPERTIES,
    PROPERTY_NAMES,
    SAFE_FONTS,
    SETTINGS,
    BandElement,
    ChartSpec,
    ImageSpec,
    Look,
    PageSpec,
    Props,
    Style,
    StyleFile,
    band_columns,
    describe,
    house,
    load_style,
    resolve,
)
from loom_ia.tools_builtin._templates import SectionSpec
from loom_ia.tools_builtin.pdf_params import PdfParams
from loom_ia.tools_builtin.word import WordTool, build
from loom_ia.tools_builtin.word import reader as reader_module
from loom_ia.tools_builtin.word.style import styles_xml
from loom_ia.tools_builtin.word.writer import STYLES
from loom_ia.tools_builtin.word_params import WordParams

CONTEXT: Final = ToolContext(
    tenant_id=DEFAULT_TENANT,
    session_id=SessionId("s"),
    run_id=RunId("r"),
    call_id="c1",
    agent="demo",
)
JSONL: Final = {"events": {"backend": "jsonl", "path": "journal"}}
W: Final = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
FOLDER: Final = {"name": "sortie", "path": "sortie", "rights": ["read", "write", "overwrite"]}
LOGO: Final = png(40, 14)

FACTURE_YAML: Final = """description: Facture d'un artisan.
style:
  quiet_title:
    { base: heading1, uppercase: false, color: 2F5496, size: 10, space_before: 10, space_after: 3 }
  small_text: { base: paragraph, size: 8, align: justify }
  summary_table: { base: table, width: content, position: right }
sections:
  Émetteur: { show_title: false, width: 55 }
  Client: { show_title: false, beside: true, width: 45, box: true }
  Description: { show_title: false, styles: { table: summary_table } }
  Informations de paiement: { styles: { heading1: quiet_title } }
  Conditions et termes: { styles: { heading1: quiet_title, paragraph: small_text } }
"""
CHARTE: Final[dict[str, Any]] = {
    "colors": {"brand": "0B5D3B", "alert": "c0392b"},
    "body": {"font": "Arial", "size": 10},
    "document_title": {
        "size": 22, "color": "brand", "align": "right", "uppercase": True, "border_bottom": "none"
    },
    "heading1": {"size": 13, "uppercase": True, "color": "brand"},
    "heading2": {"color": "brand"},
    "heading3": {"color": "alert"},
    "table": {"borders": "horizontal", "border_top": "1pt brand", "border_bottom": "1pt brand"},
    "table_header": {"fill": "E8F1EC"},
    "page": {
        "header": {"logo": {"file": LOGO, "width": 3.5}, "left": {"logo": True}, "right": "{title}"},
        "footer": {"lines": ["Dupont Plomberie SARL · SIRET 812 345 678 00021"],
                   "right": "Page {page} / {pages}"},
        "watermark": "BROUILLON",
    },
}  # fmt: skip
FACTURE: Final[list[dict[str, Any]]] = [
    {"type": "heading", "text": "Émetteur"},
    {"type": "paragraph", "text": "**Dupont Plomberie**\n12 rue des Lilas, 69003 Lyon"},
    {"type": "heading", "text": "Client"},
    {"type": "paragraph", "text": "SCI Les Tilleuls\n4 avenue Foch, 69006 Lyon"},
    {"type": "heading", "text": "Description"},
    {
        "type": "table",
        "columns": [
            {"header": "Désignation"},
            {"header": "Qté", "type": "integer"},
            {"header": "PU HT", "type": "amount_eur"},
            {"header": "Montant HT", "type": "amount_eur"},
        ],
        "rows": [
            ["Remplacement du chauffe-eau électrique de 200 litres, dépose comprise", 1, 890, 890],
            ["Main-d'œuvre", 4, 55, 220],
        ],
    },
    {
        "type": "table",
        "columns": [{"header": "Rubrique"}, {"header": "Montant", "type": "amount_eur"}],
        "rows": [["Sous-total HT", 1110], ["TVA (10 %)", 111], ["Net à payer", 1221]],
    },
    {"type": "heading", "text": "Informations de paiement"},
    {"type": "paragraph", "text": "Virement : IBAN FR76 1234 5678 9012"},
    {"type": "heading", "text": "Conditions et termes"},
    {"type": "paragraph", "text": "Paiement à 30 jours ; indemnité forfaitaire : 40 €."},
]
# Les sections de la facture : ses blocs, sans les titres que l'outil écrit.
FACTURE_SECTIONS: Final[dict[str, list[dict[str, Any]]]] = {
    "Émetteur": [FACTURE[1]],
    "Client": [FACTURE[3]],
    "Description": FACTURE[5:7],
    "Informations de paiement": [FACTURE[8]],
    "Conditions et termes": [FACTURE[10]],
}
RAPPORT_YAML: Final = """style:
  numbering: decimal
  caption: { numbered: true }
  image: { side_by_side: true }
  small_text: { base: paragraph, size: 9 }
sections:
  Page de garde: { show_title: false, cover: true }
  Contexte:
  Mesures: { optional: true, landscape: true }
  Conditions générales: { columns: 2, styles: { paragraph: small_text } }
"""
RAPPORT: Final[list[dict[str, Any]]] = [
    {"type": "heading", "text": "Page de garde"},
    {"type": "paragraph", "text": "Chantier SCI Les Tilleuls"},
    {"type": "heading", "text": "Contexte"},
    {"type": "heading", "text": "Photos", "level": 2},
    {"type": "image", "path": "sortie/a.png", "width": "half", "caption": "Avant"},
    {"type": "image", "path": "sortie/a.png", "width": "half", "caption": "Après"},
    {"type": "heading", "text": "Mesures"},
    {
        "type": "table",
        "columns": [{"header": "Point"}, {"header": "Pression", "type": "decimal"}],
        "rows": [["P1", 3.2], ["P2", 2.1]],
        "caption": "Pression mesurée",
    },
    {"type": "heading", "text": "Conditions générales"},
    {"type": "paragraph", "text": "Article 1. Le rapport décrit l'état constaté."},
]
RAPPORT_SECTIONS: Final[dict[str, list[dict[str, Any]]]] = {
    "Page de garde": [RAPPORT[1]],
    "Contexte": RAPPORT[3:6],
    "Mesures": [RAPPORT[7]],
    "Conditions générales": [RAPPORT[9]],
}


# Le dossier de sortie de chaque outil monté par ``make``.
OUTPUTS: Final[dict[int, Path]] = {}


def make(tmp_path: Path, blocks: tuple[str, ...] = ("image",), **params: Any) -> WordTool:
    (tmp_path / "sortie").mkdir(exist_ok=True)
    (tmp_path / "sortie" / "a.png").write_bytes(png(80, 60))
    model = WordParams.model_validate({"folders": [FOLDER], "blocks": list(blocks), **params})
    tool = build(model, BuiltinContext(base_dir=tmp_path))
    OUTPUTS[id(tool)] = tmp_path / "sortie"
    return tool


def styled(tmp_path: Path, charte: dict[str, Any] | None = CHARTE) -> WordTool:
    return make(
        tmp_path,
        style={"spec": charte} if charte is not None else None,
        templates={"facture": {"text": FACTURE_YAML}, "rapport": {"text": RAPPORT_YAML}},
    )


async def call(tool: WordTool, **arguments: Any) -> dict[str, Any]:
    output = await tool.invoke(dict(arguments), CONTEXT)
    assert isinstance(output.data, dict)
    return output.data


async def write(tool: WordTool, name: str, blocks: list[dict[str, Any]], **more: Any) -> Path:
    await call(tool, path=f"sortie/{name}.docx", blocks=blocks, overwrite=True, **more)
    return OUTPUTS[id(tool)] / f"{name}.docx"


async def write_sections(tool: WordTool, name: str, template: str, sections: dict[str, Any], **more: Any) -> Path:
    await call(
        tool,
        path=f"sortie/{name}.docx",
        template=template,
        sections=sections,
        overwrite=True,
        **more,
    )
    return OUTPUTS[id(tool)] / f"{name}.docx"


def part(path: Path, name: str) -> str:
    with zipfile.ZipFile(path) as archive:
        return archive.read(name).decode()


def names(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as archive:
        return archive.namelist()


def word_style(styles: str, style_id: str) -> str:
    """Un style de ``styles.xml``, par son identifiant."""
    found = re.search(rf'<w:style w:type="\w+" w:styleId="{style_id}">.*?</w:style>', styles)
    assert found is not None, style_id
    return found.group()


def style_of(given: dict[str, Any]) -> StyleFile:
    return StyleFile.model_validate(given)


# L'ordre des enfants que le schéma WordprocessingML impose, pour ce qu'écrit l'outil :
# Word refuse un document qui ne le suit pas, LibreOffice non.
ORDER: Final = {
    "sectPr": ("headerReference", "footerReference", "type", "pgSz", "pgMar", "cols", "titlePg"),
    "pPr": ("pStyle", "keepNext", "keepLines", "numPr", "pBdr", "shd", "tabs", "spacing", "ind",
            "jc", "outlineLvl", "sectPr"),
    "tblPr": ("tblStyle", "tblW", "jc", "tblBorders", "tblLayout", "tblCellMar", "tblLook"),
    "tcPr": ("tcW", "tcBorders", "shd", "tcMar", "vAlign"),
    "rPr": ("rFonts", "b", "bCs", "i", "iCs", "caps", "vanish", "color", "sz", "szCs", "u",
            "lang"),
    "pBdr": ("top", "left", "bottom", "right"),
    "tblBorders": ("top", "left", "bottom", "right", "insideH", "insideV"),
    "numbering": ("abstractNum", "num"),
}  # fmt: skip


def check_order(xml: str) -> None:
    root = ElementTree.fromstring(xml)
    for element in root.iter():
        local = element.tag.rpartition("}")[2]
        expected = ORDER.get(local)
        if expected is None:
            continue
        children = [child.tag.rpartition("}")[2] for child in element]
        known = [child for child in children if child in expected]
        # headerReference et footerReference peuvent alterner : un même rang.
        positions = [max(expected.index(child), 1) for child in known]
        assert positions == sorted(positions), f"{local} : {children}"


# --- Sans charte, rien ne change ---------------------------------------------------------

# L'empreinte du devis de ``test_word`` écrit sans style, dans le style de base de 2.1.1
# (les valeurs de Word, Microsoft 365). Jusqu'à 2.1.0, le style maison (Calibri 11, titres
# bleu nuit) : 149987cb…, celle de la 2.0.21 pour le même contenu.
DEVIS_SHA256: Final = "42a9ae695da79af59cd6adaf82996083df451c6523a0336260bf668177ad8eab"
# Le ``styles.xml`` de ``default.yaml`` seul (5c73cfc1… jusqu'à 2.1.0, le style maison).
HOUSE_STYLES_SHA256: Final = "cddc5ca87685aac0444316035181b5cc7f50af94cb16fabc55150f02adbbab99"


async def test_without_a_style_nothing_changes(tmp_path: Path) -> None:
    from test_word import DEVIS

    plain = make(tmp_path)
    assert (await call(plain, path="sortie/a.docx", **DEVIS))["sha256"] == DEVIS_SHA256
    # Un style vide : default.yaml seul, le même fichier.
    empty = make(tmp_path, style={"spec": {}})
    assert (await call(empty, path="sortie/b.docx", **DEVIS))["sha256"] == DEVIS_SHA256
    assert resolve(StyleFile()) == house()
    assert styles_xml(house()) == STYLES
    assert hashlib.sha256(STYLES.encode()).hexdigest() == HOUSE_STYLES_SHA256
    # Ni le schéma ni la description ne disent rien des styles : le modèle n'en voit rien.
    assert empty.spec == plain.spec
    assert ("style", "charte") in empty.settings().options
    assert not any(label == "style" for label, _ in plain.settings().options)


def test_no_visual_value_in_the_code() -> None:
    """Ni couleur ni police dans l'écriture Word : elles viennent de ``default.yaml``.

    Deux mécaniques restent : le blanc du papier (le trait entre deux secteurs d'un
    graphique) et la police de symboles de la case à cocher (un glyphe, comme une puce).
    """
    folder = Path(word_package.__file__).parent
    fonts = re.compile("|".join(sorted({*SAFE_FONTS, "helvetica", "times"})), re.IGNORECASE)
    for source in sorted(folder.glob("*.py")):
        code = source.read_text(encoding="utf-8")
        colors = set(re.findall(r"\b[0-9A-F]{6}\b", code)) - {"FFFFFF"}
        assert not colors, f"{source.name} : {sorted(colors)}"
        assert not fonts.search(code), f"{source.name} : {fonts.search(code)}"


def numeric_constants(source: Path) -> set[str]:
    """Les constantes d'un module qui valent un nombre (``NAME: Final = 2 * cm``)."""

    def numeric(node: ast.expr) -> bool:
        if isinstance(node, ast.Constant):
            return isinstance(node.value, int | float) and not isinstance(node.value, bool)
        if isinstance(node, ast.BinOp):
            return numeric(node.left) or numeric(node.right)
        if isinstance(node, ast.UnaryOp):
            return numeric(node.operand)
        if isinstance(node, ast.Tuple):
            return any(numeric(item) for item in node.elts)
        return False

    tree = ast.parse(source.read_text(encoding="utf-8"))
    return {
        f"{source.stem}.{node.target.id}"
        for node in tree.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None and numeric(node.value)
    }


# Les nombres restés dans l'écriture Word, et pourquoi : toute longueur visuelle vient de
# default.yaml (docs/style_format.md § 11).
WORD_NUMBERS: Final[dict[str, str]] = {
    "chart.CM": "un centimètre en EMU, l'unité de DrawingML",
    "chart.WIDTH": "la taille d'un graphique (une mécanique)",
    "chart.HEIGHT": "la taille d'un graphique (une mécanique)",
    "image.EMU": "un millième de millimètre en EMU",
    "style.HEADING_NUMBERING": "l'identifiant de la numérotation des titres",
    "style._DEEP": "l'identifiant d'une numérotation à trois niveaux",
    "style._BAND_END": "le paragraphe d'un point qui clôt un en-tête ou un pied après sa table (Word en veut un)",
    "writer.ZIP_DATE": "la date des parties du .docx",
    "writer.CM": "un centimètre en twips",
    "writer.EMU_PER_TWIP": "un twip en EMU",
    "writer._DATE_SPACE": "l'espace sous « Date : » d'une signature, resté dans le code",
    "writer.SECTION_MARKS": "le premier identifiant des signets des sections",
    "writer.REF_MARKS": "le premier identifiant des signets des renvois",
}
# Les longueurs écrites telles quelles dans le XML de Word : (fichier, attribut, valeur).
WORD_INLINE: Final[dict[tuple[str, str, str], str]] = {
    ("image.py", "before", "120"): "le paragraphe d'une image : 6 pt avant, resté dans le code",
    ("image.py", "after", "60"): "le paragraphe d'une image : 3 pt après, resté dans le code",
    ("chart.py", "before", "120"): "le paragraphe d'un graphique : 6 pt avant, resté dans le code",
    ("chart.py", "after", "60"): "le paragraphe d'un graphique : 3 pt après, resté dans le code",
    ("writer.py", "line", "240"): "le séparateur des notes de bas de page, celui de Word",
    ("style.py", "hanging", "340"): "la case d'une liste à cocher (une mécanique)",
}


def test_no_visual_length_in_the_code() -> None:
    """Ni distance ni espace dans l'écriture Word : ils viennent de default.yaml. Restent
    des unités, des identifiants, et des mécaniques nommées ici (``WORD_NUMBERS``,
    ``WORD_INLINE``)."""
    folder = Path(word_package.__file__).parent
    writers = [folder / name for name in ("writer.py", "style.py", "chart.py", "image.py")]
    assert set().union(*(numeric_constants(source) for source in writers)) == set(WORD_NUMBERS)
    attribute = re.compile(r'\bw:(space|before|after|left|right|hanging|line|header|footer|firstLine)="([1-9][0-9]*)"')
    found = {(source.name, name, value) for source in writers for name, value in attribute.findall(source.read_text(encoding="utf-8"))}
    assert found == set(WORD_INLINE)


def test_default_yaml_is_a_style_file_like_any_other() -> None:
    """``default.yaml`` se lit comme une charte ; ses couleurs de graphique sont celles
    d'``excel`` et de ``pdf``."""
    from loom_ia.tools_builtin._chart_style import GRID, PALETTE, TEXT

    house_file = load_style(DEFAULT_FILE.read_text(encoding="utf-8"))
    assert house_file.chart is not None and tuple(house_file.chart.series or ()) == PALETTE
    assert (house_file.chart.grid_color, house_file.chart.text_color) == (GRID, TEXT)
    # Les six couleurs d'accent du thème Office 2023, puis les mêmes plus sombres.
    assert PALETTE[:3] == ("156082", "E97132", "196B24") and len(PALETTE) == 12
    style = house()
    assert style.look("heading1").font == "Aptos"  # hérité de text
    assert style.look("document_title").border_bottom is None  # ni filet, ni fond, ni gras
    assert style.look("table_header").fill is None and style.look("note").fill is None
    assert style.look("quote_source").italic is False  # sa base, quote, est en italique
    assert style.look("table_header").size == 10  # hérité de table
    assert style.footer is None  # ni numéro de page ni mention : la charte les demande
    assert style.header is None and style.watermark is None
    assert style.labels.contents == "Sommaire" and style.margins == (1134, 1134, 1134, 1134)


# --- La charte ----------------------------------------------------------------------------


async def test_the_charte_sets_fonts_colors_and_headings(tmp_path: Path) -> None:
    tool = styled(tmp_path)
    path = await write(tool, "libre", FACTURE, title="Facture", template="none")
    styles = part(path, "word/styles.xml")
    assert 'w:ascii="Arial"' in styles and '<w:sz w:val="20"/>' in styles
    title = word_style(styles, "Title")
    assert '<w:jc w:val="right"/>' in title and "<w:caps/>" in title
    # border_bottom: none retire le filet de default.yaml.
    assert "pBdr" not in title and 'w:val="0B5D3B"' in title
    heading = word_style(styles, "Heading1")
    assert "<w:caps/>" in heading and '<w:sz w:val="26"/>' in heading
    # Une couleur nommée prend sa valeur, en majuscules.
    assert 'w:color w:val="C0392B"' in word_style(styles, "Heading3")
    # Horizontales, et les filets haut et bas de la charte ; pas de verticales.
    document = part(path, "word/document.xml")
    assert '<w:top w:val="single" w:sz="8" w:space="0" w:color="0B5D3B"/>' in document
    assert '<w:insideH w:val="single" w:sz="4" w:space="0" w:color="000000"/>' in document
    assert "insideV" not in document
    assert 'w:fill="E8F1EC"' in document
    for name in ("word/document.xml", "word/styles.xml", "word/header1.xml"):
        check_order(part(path, name))


async def test_an_underlined_title(tmp_path: Path) -> None:
    """Le texte du titre souligné, de sa longueur ; sans filet de toute la largeur."""
    charte = {"document_title": {"underline": True, "border_bottom": "none"}}
    tool = styled(tmp_path, charte=charte)
    path = await write(tool, "libre", FACTURE, title="Facture", template="none")
    styles = part(path, "word/styles.xml")
    title = word_style(styles, "Title")
    assert '<w:u w:val="single"/>' in title and "pBdr" not in title
    check_order(styles)
    # Sans la propriété, rien de souligné.
    plain = resolve(style_of({"document_title": {"border_bottom": "none"}}))
    assert "<w:u " not in styles_xml(plain)


async def test_underlined_headings(tmp_path: Path) -> None:
    """Un niveau souligné, les autres non ; un style à toi qui part du niveau 1 en hérite."""
    charte = {"heading1": {"underline": True}}
    tool = make(tmp_path, style={"spec": charte}, templates={"facture": {"text": FACTURE_YAML}})
    path = await write_sections(tool, "facture", "facture", FACTURE_SECTIONS, title="Facture")
    styles = part(path, "word/styles.xml")
    check_order(styles)
    for level, underlined in ((1, True), (2, False), (3, False)):
        heading = word_style(styles, f"Heading{level}")
        assert ('<w:u w:val="single"/>' in heading) is underlined
    quiet = word_style(styles, "quiet_title")
    assert '<w:basedOn w:val="Heading1"/>' in quiet and '<w:name w:val="quiet_title"/>' in quiet
    assert "<w:u " not in quiet  # hérité, pas annulé
    # uppercase: false n'écrit rien : le niveau 1 n'est pas en capitales.
    assert "caps" not in quiet and '<w:color w:val="2F5496"/>' in quiet


async def test_header_footer_logo_and_watermark(tmp_path: Path) -> None:
    tool = styled(tmp_path)
    path = await write(tool, "libre", FACTURE, title="Facture F-12", template="none")
    header = part(path, "word/header1.xml")
    assert "Facture F-12" in header  # {title}
    assert 'string="BROUILLON"' in header and "PowerPlusWaterMarkObject" in header
    assert 'fillcolor="#D9D9D9"' in header and "&quot;Arial&quot;" in header
    assert 'r:embed="rIdImage1"' in header
    assert "media/image1.png" in part(path, "word/_rels/header1.xml.rels")
    # Le logo est une partie de l'archive, mais pas une relation du corps.
    assert "word/media/image1.png" in names(path)
    assert "rIdImage1" not in part(path, "word/_rels/document.xml.rels")
    assert 'Target="header1.xml"' in part(path, "word/_rels/document.xml.rels")
    assert "/word/header1.xml" in part(path, "[Content_Types].xml")
    footer = part(path, "word/footer1.xml")
    # Les mentions, puis la ligne des zones (le numéro de page à droite, center vidé).
    assert footer.index("SIRET") < footer.index(" PAGE ") < footer.index(" NUMPAGES ")
    assert footer.count(" PAGE ") == 1
    assert '<w:tab w:val="right" w:pos="9638"/>' in footer
    document = part(path, "word/document.xml")
    assert '<w:headerReference w:type="default" r:id="rIdHeader1"/>' in document
    assert "titlePg" not in document


async def test_the_house_footer_has_no_page_numbers(tmp_path: Path) -> None:
    """Le pied de default.yaml n'écrit rien : des mentions ajoutées viennent seules, et une
    charte qui veut les numéros de page les demande."""
    tool = make(tmp_path, style={"spec": {"page": {"footer": {"lines": ["SIRET 812 345 678 00021"]}}}})
    path = await write(tool, "d", FACTURE)
    footer = part(path, "word/footer1.xml")
    assert "SIRET" in footer and " PAGE " not in footer
    numbered = make(tmp_path, style={"spec": {"page": {"footer": {"center": "Page {page} / {pages}"}}}})
    footer = part(await write(numbered, "e", FACTURE), "word/footer1.xml")
    assert footer.index(" PAGE ") < footer.index(" NUMPAGES ")


# --- La table d'un en-tête ou d'un pied de page -------------------------------------------

BAND_TABLE: Final[dict[str, Any]] = {
    "colors": {"brand": "1F3A5F"},
    "footer_page": {"base": "footer", "bold": True, "align": "right", "valign": "center"},
    "footer": {"size": 8, "border_top": "0.5pt brand", "borders": "vertical", "line": "0.5pt brand", "column_gap": 0.4},
    "page": {
        "footer": {
            "lines": ["Mention de toute la largeur"],
            "table": {
                "widths": ["auto", 1.5],
                "rows": [
                    [
                        ["App-Novative · 2 allée du Semoir, 77185 Lognes", "SIRET 000 000 000 00000"],
                        {"text": "{page}/{pages}", "style": "footer_page"},
                    ]
                ],
            },
        }
    },
}  # fmt: skip


def band_style(*, footer: dict[str, Any] | None = None, **page: Any) -> Style:
    """Le style de ``BAND_TABLE`` : son style ``footer`` complété de ``footer``, son
    ``page.footer`` de ``page`` (``None`` : clé retirée)."""
    content = {key: value for key, value in {**BAND_TABLE["page"]["footer"], **page}.items() if value is not None}
    spec = {**BAND_TABLE, "footer": {**BAND_TABLE["footer"], **(footer or {})}, "page": {"footer": content}}
    return resolve(StyleFile.model_validate(spec))


def test_a_band_table_is_read_and_checked() -> None:
    """La table d'un pied : son contenu dans ``page.footer``, ses cellules en court (un
    texte, des lignes) ou nommant leur style ; ses filets et l'écart entre ses colonnes,
    des propriétés de son style ; refusée avec les zones, mal formée."""
    footer = band_style().footer
    assert footer is not None and footer.table is not None and footer.lines
    table = footer.table
    assert table.widths == (None, 1.5) and table.style is None
    look = band_style().look("footer")
    assert (look.column_gap, look.borders, look.line) == (0.4, "vertical", "0.5pt 1F3A5F")  # couleur résolue
    left, right = table.rows[0]
    assert left == BandElement(("App-Novative · 2 allée du Semoir, 77185 Lognes", "SIRET 000 000 000 00000"))
    assert right == BandElement(("{page}/{pages}",), style="footer_page")
    # La place qui reste va à la colonne auto ; 1,5 cm = 850 twips.
    assert band_columns(table, 9638) == [8788, 850]
    faults: list[tuple[dict[str, Any], dict[str, Any], str]] = [
        ({}, {"center": "Page {page}"}, r"pas avec center .* center: null"),
        ({}, {"table": {"widths": [18], "rows": [["a"]]}}, r"18 cm de colonnes pour 17 cm"),
        ({}, {"table": {"widths": [16.5, "auto"], "rows": [["a", "b"]]}}, r"1 cm au moins par colonne auto"),
        ({}, {"table": {"widths": ["auto"], "rows": [[{"text": "a", "style": "nope"}]]}}, r"style « nope » inconnu"),
        ({}, {"table": {"widths": ["auto"], "rows": [[{"text": "a", "style": "heading1"}]]}}, r"« heading1 » ne part pas de footer"),
        ({}, {"table": {"widths": ["auto"], "style": "note", "rows": [["a"]]}}, r"« note » ne part pas de footer"),
        ({}, {"table": {"widths": ["auto"], "rows": [[{"logo": True}]]}}, r"page\.footer n'en a pas"),
        ({}, {"table": {"widths": [0.2, "auto"], "rows": [["a", "b"]]}}, r"plus large que l'écart \(column_gap\)"),
        ({"line": "1pt rouge"}, {}, r"footer\.line : couleur « rouge »"),
    ]  # fmt: skip
    for style_changes, content, match in faults:
        with pytest.raises(ValueError, match=match):
            band_style(footer=style_changes, **content)
    for wrong, match in (
        ({"widths": ["auto", 1], "rows": [["a"]]}, r"1 cellule\(s\) pour 2 colonne\(s\)"),
        ({"widths": ["auto"], "rows": [[{"text": "a", "lines": ["b"]}]]}, r"pas les deux"),
        ({"widths": ["auto"] * 5, "rows": [["a"] * 5]}, r"at most 4"),
        ({"widths": ["auto"], "rows": [["a"]] * 4}, r"at most 3"),
        ({"widths": ["auto"], "rows": [[{"text": "{date}"}]]}, r"champ inconnu \{date\}"),
        ({"widths": ["auto"], "rows": [[{"text": "a", "valign": "top"}]]}, r"valign : une propriété de style, pas d'un élément"),
        ({"widths": ["auto"], "rows": [[{"text": "a", "style": {"bold": True}}]]}, r"le nom d'un style.* pas un style écrit en ligne"),
        ({"widths": ["auto"], "gap": 0.4, "rows": [["a"]]}, r"gap → column_gap : des propriétés du style de la table"),
    ):
        with pytest.raises(ValidationError, match=match):
            StyleFile.model_validate({"page": {"footer": {"table": wrong}}})
    with pytest.raises(ValueError, match=r"body\.column_gap : propre à footer, header"):
        resolve(StyleFile.model_validate({"body": {"column_gap": 1}}))
    # table: null efface la table d'un fichier précédent ; le reste du pied est gardé.
    plain = resolve(
        StyleFile.model_validate({"page": {"footer": {"table": {"widths": ["auto"], "rows": [["a"]]}, "lines": ["b"]}}}),
        StyleFile.model_validate({"page": {"footer": {"table": None}}}),
    )
    assert plain.footer is not None and plain.footer.table is None and plain.footer.lines == (BandElement(("b",)),)
    # Clé par clé d'un fichier à l'autre (la charte, puis le style du modèle) : null efface
    # une clé ; footer: null, tout le pied.
    charte = StyleFile.model_validate({"page": {"footer": {"left": "Gauche", "lines": ["Mentions"]}, "watermark": "COPIE"}})
    merged = resolve(charte, StyleFile.model_validate({"page": {"footer": {"left": None, "right": "{page}"}}}))
    assert merged.footer is not None and merged.footer.left is None and merged.footer.lines == (BandElement(("Mentions",)),)
    assert merged.footer.right == BandElement(("{page}",)) and merged.watermark_text == "COPIE"
    assert resolve(charte, StyleFile.model_validate({"page": {"footer": None}})).footer is None


async def test_each_line_of_a_band_has_its_alignment(tmp_path: Path) -> None:
    """Une des ``lines`` : un texte, au style de l'en-tête ou du pied, ou ``{text, style}``,
    au style qu'elle nomme ; dans un en-tête, centrée si son style ne dit pas d'alignement ;
    ``center`` seul toujours centré, même si le style du pied ne l'est pas."""
    spec = {
        "header_right": {"base": "header", "align": "right"},
        "footer_center": {"base": "footer", "align": "center"},
        "page": {
            "header": {"lines": ["Au centre", {"text": "À droite", "style": "header_right"}]},
            "footer": {"lines": [{"text": "Centrée", "style": "footer_center"}, "Selon le style"], "center": "Au milieu"},
        },
    }
    style = resolve(StyleFile.model_validate(spec))
    assert style.footer is not None and style.footer.lines == (BandElement(("Centrée",), "footer_center"), BandElement(("Selon le style",)))
    path = await write(make(tmp_path, style={"spec": spec}), "l", FACTURE)
    footer = part(path, "word/footer1.xml")
    assert '<w:pStyle w:val="footer_center"/></w:pPr><w:r><w:t xml:space="preserve">Centrée' in footer
    assert '<w:pStyle w:val="Footer"/></w:pPr><w:r><w:t xml:space="preserve">Selon le style' in footer
    assert '<w:pStyle w:val="Footer"/><w:jc w:val="center"/></w:pPr><w:r><w:t xml:space="preserve">Au milieu' in footer
    assert '<w:jc w:val="center"/>' in word_style(part(path, "word/styles.xml"), "footer_center")
    header = part(path, "word/header1.xml")
    assert '<w:jc w:val="center"/></w:pPr><w:r><w:t xml:space="preserve">Au centre' in header
    assert '<w:pStyle w:val="header_right"/></w:pPr><w:r><w:t xml:space="preserve">À droite' in header
    with pytest.raises(ValidationError, match=r"align : une propriété de style, pas d'un élément"):
        StyleFile.model_validate({"page": {"footer": {"lines": [{"text": "a", "align": "left"}]}}})
    with pytest.raises(ValidationError, match=r"lines\.1 : une ligne a un texte \(text\), pas des lignes"):
        StyleFile.model_validate({"page": {"footer": {"lines": [["a", "b"]]}}})


async def test_a_zone_takes_the_characters_of_its_style(tmp_path: Path) -> None:
    """Une zone au style qu'elle nomme : sa police, sa taille, sa couleur, son gras, en
    propriétés de caractère dans la ligne des zones."""
    spec = {
        "footer_page": {"base": "footer", "bold": True, "color": "1F3A5F"},
        "page": {"footer": {"left": "Gauche", "right": {"text": "{page}", "style": "footer_page"}}},
    }
    footer = part(await write(make(tmp_path, style={"spec": spec}), "z", FACTURE), "word/footer1.xml")
    bold = '<w:rPr><w:b/><w:bCs/><w:color w:val="1F3A5F"/></w:rPr>'
    assert f'<w:fldSimple w:instr=" PAGE "><w:r>{bold}<w:t>1</w:t></w:r></w:fldSimple>' in footer
    assert '<w:r><w:t xml:space="preserve">Gauche</w:t></w:r>' in footer  # sans style : celui du pied


async def test_a_footer_table_in_word(tmp_path: Path) -> None:
    """Le pied en table : les lignes de toute la largeur, puis la table ; le cadre du style
    sur la table, pas sur ses paragraphes ; l'écart en deux moitiés autour du filet
    intérieur ; les numéros de page en champs, dans le style de cellule ; un paragraphe
    d'un point pour finir."""
    tool = make(tmp_path, style={"spec": BAND_TABLE})
    path = await write(tool, "t", FACTURE)
    footer = part(path, "word/footer1.xml")
    assert footer.index("<w:tbl>") < footer.index("Mention") < footer.index("App-Novative")
    assert '<w:gridCol w:w="8788"/><w:gridCol w:w="850"/>' in footer
    assert '<w:gridSpan w:val="2"/>' in footer  # la mention, sur toute la largeur
    assert '<w:top w:val="single" w:sz="4" w:space="0" w:color="1F3A5F"/>' in footer
    assert footer.count('<w:right w:val="single" w:sz="4" w:space="0" w:color="1F3A5F"/>') == 1
    # 0,4 cm = 227 twips : 113 à droite de la première colonne, 114 à gauche de la seconde.
    assert '<w:right w:w="113" w:type="dxa"/>' in footer and '<w:left w:w="114" w:type="dxa"/>' in footer
    assert '<w:top w:w="80" w:type="dxa"/>' in footer  # l'écart au filet du haut
    assert '<w:vAlign w:val="center"/>' in footer and '<w:pStyle w:val="footer_page"/>' in footer
    assert '<w:jc w:val="right"/>' in word_style(part(path, "word/styles.xml"), "footer_page")  # le style l'aligne
    assert footer.index(" PAGE ") < footer.index(" NUMPAGES ") < footer.index("</w:tbl>")
    assert footer.count('<w:pBdr><w:top w:val="nil"/>') == 5  # 4 paragraphes, et le dernier
    assert footer.endswith('<w:rPr><w:sz w:val="2"/><w:szCs w:val="2"/></w:rPr></w:pPr></w:p></w:ftr>')
    styles = part(path, "word/styles.xml")
    assert '<w:basedOn w:val="Footer"/>' in word_style(styles, "footer_page")
    ElementTree.fromstring(footer)


async def test_a_header_table_with_its_logo_and_the_watermark(tmp_path: Path) -> None:
    """L'en-tête en table : le logo dans sa cellule, les lignes sous la table, centrées ;
    le filigrane dans le paragraphe qui clôt l'en-tête, pas dans une cellule."""
    charte = {
        "header_title": {"base": "header", "align": "right"},
        "page": {
            "header": {
                "logo": {"file": LOGO, "width": 3},
                "lines": ["Sous la table"],
                "table": {"widths": [6, "auto"], "rows": [[{"text": "App", "logo": True}, {"text": "{title}", "style": "header_title"}]]},
            },
            "watermark": "BROUILLON",
        },
    }  # fmt: skip
    tool = make(tmp_path, style={"spec": charte})
    path = await write(tool, "h", FACTURE, title="Facture F-12")
    header = part(path, "word/header1.xml")
    table = header[: header.index("</w:tbl>")]
    assert 'r:embed="rIdImage1"' in table and "Facture F-12" in table
    # Le logo, puis l'écart à son texte (4 pt) : une espace d'un point, espacée de 80 twips.
    assert '</w:drawing></w:r><w:r><w:rPr><w:spacing w:val="80"/><w:sz w:val="2"/>' in table
    assert table.index("Facture F-12") < table.index("Sous la table")
    assert '<w:jc w:val="center"/></w:pPr><w:r><w:t xml:space="preserve">Sous la table' in table
    assert "PowerPlusWaterMarkObject" in header[header.index("</w:tbl>") :]
    assert "PowerPlusWaterMarkObject" not in table
    ElementTree.fromstring(header)
    with pytest.raises(ValueError, match=r"page\.header\.logo : le poser dans une zone ou une cellule"):
        resolve(StyleFile.model_validate({"page": {"header": {"logo": {"file": LOGO}, "table": {"widths": ["auto"], "rows": [["a"]]}}}}))


async def test_a_first_page_without_header(tmp_path: Path) -> None:
    charte = {**CHARTE, "page": {"header": {"right": "{title}", "first_page": False}, "footer": CHARTE["page"]["footer"]}}
    tool = styled(tmp_path, charte)
    path = await write(tool, "libre", FACTURE, title="Facture", template="none")
    document = part(path, "word/document.xml")
    assert "<w:titlePg/>" in document
    assert '<w:headerReference w:type="first" r:id="rIdHeader2"/>' in document
    # Le pied de page garde sa première page : first_page n'est faux que pour l'en-tête.
    assert '<w:footerReference w:type="first" r:id="rId4"/>' in document
    assert "Facture" not in part(path, "word/header2.xml")


async def test_numbered_headings_and_captions(tmp_path: Path) -> None:
    tool = styled(tmp_path)
    path = await write_sections(tool, "rapport", "rapport", RAPPORT_SECTIONS, title="Rapport")
    numbering = part(path, "word/numbering.xml")
    check_order(numbering)
    assert '<w:pStyle w:val="Heading1"/><w:suff w:val="space"/><w:lvlText w:val="%1."/>' in numbering
    assert '<w:num w:numId="900"><w:abstractNumId w:val="2"/></w:num>' in numbering
    document = part(path, "word/document.xml")
    # La page de garde n'imprime pas son titre (show_title: false) : pas de titre masqué.
    assert "Page de garde" not in document and "HeadingHidden" not in part(path, "word/styles.xml")
    assert re.search(r'Figure </w:t></w:r><w:fldSimple w:instr=" SEQ Figure \\\* ARABIC ">', document)
    assert ">2</w:t></w:r></w:fldSimple>" in document
    assert 'w:instr=" SEQ Tableau \\* ARABIC "' in document
    read = await call(tool, action="read", path="sortie/rapport.docx")
    sections = read["sections"]
    # Relu sans numéros : ni « 1. » devant les titres, ni « Figure 1 — » dans les légendes ;
    # les titres de section ne reviennent pas, l'outil les écrit.
    assert list(sections) == ["Page de garde", "Contexte", "Mesures", "Conditions générales"]
    assert sections["Contexte"][0] == {"type": "heading", "level": 2, "text": "Photos"}
    captions = [b.get("caption") for b in sections["Contexte"] if b["type"] == "image"]
    assert captions == ["Avant", "Après"]
    assert {"type": "paragraph", "text": "Pression mesurée"} in sections["Mesures"]


async def test_section_names_are_compared_loosely(tmp_path: Path) -> None:
    """« <u>Client</u> », « 2. client » : la section Client ; l'outil écrit son nom."""
    sections = {f"<u>{name}</u>" if name == "Client" else name.upper(): blocks for name, blocks in FACTURE_SECTIONS.items()}
    path = await write_sections(styled(tmp_path), "facture", "facture", sections)
    document = part(path, "word/document.xml")
    assert ">Informations de paiement<" in document and "INFORMATIONS" not in document
    assert '<w:tblStyle w:val="LoomLayout"/>' in document


async def test_article_numbering(tmp_path: Path) -> None:
    tool = make(tmp_path, style={"spec": {"numbering": "article"}})
    path = await write(tool, "contrat", [{"type": "heading", "text": "Objet"}], title="Contrat")
    assert '<w:lvlText w:val="Article %1 \u2013"/>' in part(path, "word/numbering.xml")


async def test_a_document_in_english(tmp_path: Path) -> None:
    """``language: en`` : la langue déclarée, les nombres, les dates, les libellés."""
    tool = make(
        tmp_path,
        blocks=("image", "toc", "signature"),
        style={"spec": {"language": "en", "numbering": "article", "caption": {"numbered": True},
                        "labels": {"total": "Grand total"}}},
    )  # fmt: skip
    table = {
        "type": "table",
        "columns": [{"header": "Item"}, {"header": "Amount", "type": "amount_eur"},
                    {"header": "Date", "type": "date"}, {"header": "Paid", "type": "boolean"},
                    {"header": "Rate", "type": "percent"}],
        "rows": [["Pipe", 1250.5, "2026-11-10", True, 0.2],
                 ["Refund", -12, "2026-11-11", False, 0.1]],
        "totals": {"Amount": "sum"},
        "caption": "Costs",
    }  # fmt: skip
    blocks = [
        {"type": "toc"},
        {"type": "heading", "text": "Work"},
        table,
        {"type": "signature", "signers": ["Customer"]},
    ]
    path = await write(tool, "en", blocks, title="Quote")
    styles = part(path, "word/styles.xml")
    assert '<w:lang w:val="en-US" w:eastAsia="en-US" w:bidi="ar-SA"/>' in styles
    assert "<dc:language>en-US</dc:language>" in part(path, "docProps/core.xml")
    document = part(path, "word/document.xml")
    said = ("€1,250.50", "-€12.00", "€1,238.50", "11/10/2026", "Yes", "No", "20.0%",
            "Grand total", "Contents", "Date:", "Signature:", "Table ", "SEQ Table ")  # fmt: skip
    for text in said:
        assert text in document, text
    assert '<w:lvlText w:val="Article %1 \u2013"/>' in part(path, "word/numbering.xml")


# --- La disposition (le style d'un modèle, jusqu'au lot 3) ---------------------------------


async def test_a_facture_laid_out(tmp_path: Path) -> None:
    tool = styled(tmp_path)
    path = await write_sections(tool, "facture", "facture", FACTURE_SECTIONS, title="Facture")
    document = part(path, "word/document.xml")
    check_order(document)
    styles = part(path, "word/styles.xml")
    check_order(styles)
    assert '<w:name w:val="loom layout"/>' in styles
    # Trois sections sans titre ; deux au titre discret, un style du modèle.
    assert ">Émetteur<" not in document and ">Description<" not in document
    assert document.count('<w:pStyle w:val="quiet_title"/>') == 2
    # Émetteur et client côte à côte, 55/45, le client encadré au style box.
    layout = re.search(r'<w:tblStyle w:val="LoomLayout"/>.*?</w:tbl>', document)
    assert layout is not None
    assert '<w:gridCol w:w="5301"/><w:gridCol w:w="4337"/>' in layout.group()
    assert layout.group().count("<w:tcBorders>") == 1
    assert '<w:top w:val="single" w:sz="4" w:space="0" w:color="000000"/>' in layout.group()
    assert layout.group().index("Dupont Plomberie") < layout.group().index("SCI Les Tilleuls")
    # Chaque section entre ses signets, ceux des sections côte à côte dans leur cellule.
    marks = re.findall(r'w:name="(_loom_s\d)"', document)
    assert marks == ["_loom_s1", "_loom_s2", "_loom_s3", "_loom_s4", "_loom_s5"]
    assert layout.group().count("<w:bookmarkStart ") == 2
    # Les prestations en pleine largeur ; le récapitulatif, étroit, à droite (summary_table) ;
    # leur texte au style du tableau du modèle, basé sur Table Text.
    assert document.count('<w:tblW w:w="5000" w:type="pct"/>') == 1
    assert re.search(r'<w:tblW w:w="\d+" w:type="dxa"/><w:jc w:val="right"/>', document)
    assert '<w:pStyle w:val="summary_table"/>' in document
    summary = word_style(styles, "summary_table")
    assert '<w:basedOn w:val="TableText"/>' in summary and "pBdr" not in summary
    # Petit texte justifié dans les conditions : un style du modèle, basé sur Normal.
    small = word_style(styles, "small_text")
    assert '<w:jc w:val="both"/>' in small and '<w:sz w:val="16"/>' in small
    assert '<w:basedOn w:val="Normal"/>' in small
    assert '<w:pStyle w:val="small_text"/>' in document
    # Relu : les sections du modèle, dans l'ordre, et le texte des cellules.
    read = await call(tool, action="read", path="sortie/facture.docx")
    assert list(read["sections"]) == list(FACTURE_SECTIONS)
    assert read["sections"]["Client"] == FACTURE_SECTIONS["Client"]
    # Réécrit tel que relu (les tableaux relus sont des lignes, repris de l'original).
    again = {name: blocks for name, blocks in read["sections"].items()}
    again["Description"] = FACTURE_SECTIONS["Description"]
    rewritten = await call(tool, path="sortie/f2.docx", template="facture", title="Facture", sections=again)
    assert rewritten["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


async def test_a_report_laid_out(tmp_path: Path) -> None:
    tool = styled(tmp_path, None)
    path = await write_sections(tool, "rapport", "rapport", RAPPORT_SECTIONS, title="Rapport")
    document = part(path, "word/document.xml")
    check_order(document)
    sections = re.findall(r"<w:sectPr>.*?</w:sectPr>", document)
    # Page de garde ; texte ; paysage ; colonnes ; colonnes fermées (équilibrées).
    assert len(sections) == 5
    assert "<w:titlePg/>" in sections[0] and 'r:id="rIdFooter2"' in sections[0]
    assert "orient" not in sections[1] and "<w:type" not in sections[1]
    assert 'w:orient="landscape"' in sections[2] and "<w:type" not in sections[2]
    assert '<w:cols w:num="2" w:space="567"/>' in sections[3] and "<w:type" not in sections[3]
    assert '<w:type w:val="continuous"/>' in sections[4] and "cols" not in sections[4]
    assert "titlePg" not in "".join(sections[1:])
    # La page de garde : son espace en haut, son titre centré.
    assert '<w:spacing w:before="0" w:after="0" w:line="3402" w:lineRule="exact"/>' in document
    assert '<w:pStyle w:val="Title"/><w:jc w:val="center"/>' in document
    # La grille : deux images côte à côte, de même largeur.
    grid = re.search(r'<w:tblStyle w:val="LoomLayout"/>.*?</w:tbl>', document)
    assert grid is not None and grid.group().count("<wp:inline") == 2
    assert len(set(re.findall(r'<wp:extent cx="(\d+)"', grid.group()))) == 1
    # Le tableau en paysage prend la largeur de la page couchée.
    assert '<w:gridCol w:w="' in document
    assert "word/header2.xml" in names(path) and "word/footer2.xml" in names(path)


PARAGRAPH: Final[list[dict[str, Any]]] = [{"type": "paragraph", "text": "x"}]


async def test_columns_then_landscape_are_balanced(tmp_path: Path) -> None:
    layout = "sections:\n  A: {columns: 2}\n  B: {landscape: true}\n"
    tool = make(tmp_path, templates={"t": {"text": layout}})
    path = await write_sections(tool, "d", "t", {"A": PARAGRAPH, "B": PARAGRAPH})
    sections = re.findall(r"<w:sectPr>.*?</w:sectPr>", part(path, "word/document.xml"))
    # Colonnes, fermeture continue d'une colonne, paysage sur nouvelle page.
    assert "cols" in sections[0]
    assert '<w:type w:val="continuous"/>' in sections[1] and "cols" not in sections[1]
    assert "landscape" in sections[2] and "<w:type" not in sections[2]


async def test_a_boxed_section_and_a_page_break(tmp_path: Path) -> None:
    layout = (
        "style:\n  red_box: { base: box, border: 1pt 7F7F7F, fill: F7F7F7 }\n"
        "sections:\n  A:\n  B: { box: true, page_break: true, margin_top: 1,"
        " styles: { box: red_box } }\n"
    )
    tool = make(tmp_path, templates={"t": {"text": layout}})
    path = await write_sections(tool, "d", "t", {"A": PARAGRAPH, "B": PARAGRAPH})
    document = part(path, "word/document.xml")
    check_order(document)
    assert document.index('<w:br w:type="page"/>') < document.index('w:line="567"')
    assert document.index('w:line="567"') < document.index("<w:tcBorders>")
    # Le cadre au style que la section choisit : son filet, son fond.
    assert '<w:left w:val="single" w:sz="8" w:space="0" w:color="7F7F7F"/>' in document
    assert '<w:shd w:val="clear" w:color="auto" w:fill="F7F7F7"/>' in document
    read = await call(tool, action="read", path="sortie/d.docx")
    assert read["sections"] == {"A": PARAGRAPH, "B": PARAGRAPH}


async def test_an_absent_optional_section_leaves_the_other_alone(tmp_path: Path) -> None:
    layout = "sections:\n  A:\n  B: {optional: true, beside: true}\n  C:\n"
    tool = make(tmp_path, templates={"t": {"text": layout}})
    path = await write_sections(tool, "d", "t", {"A": PARAGRAPH, "C": PARAGRAPH})
    assert "LoomLayout" not in part(path, "word/document.xml")


async def test_a_styled_document_is_reproducible(tmp_path: Path) -> None:
    tool = styled(tmp_path)
    first = await call(tool, path="sortie/a.docx", sections=FACTURE_SECTIONS, template="facture")
    second = await call(tool, path="sortie/b.docx", sections=FACTURE_SECTIONS, template="facture")
    assert first["sha256"] == second["sha256"]


async def test_a_section_of_fixed_text(tmp_path: Path) -> None:
    """Le texte fixe d'une section : écrit par l'outil, balisage compris ; jamais relu."""
    layout = (
        "sections:\n  Message:\n  Mentions:\n    show_title: false\n    text: |\n"
        "      **Dupont Plomberie SARL**\n      SIRET 812 345 678 00021\n\n"
        "      Document *interne*.\n"
    )
    tool = make(tmp_path, templates={"t": {"text": layout}})
    path = await write_sections(tool, "d", "t", {"Message": PARAGRAPH})
    body = ElementTree.fromstring(part(path, "word/document.xml"))
    texts = ["".join(t.text or "" for t in p.iter(f"{W}t")) for p in body.iter(f"{W}p")]
    # Une ligne par ligne du texte (un saut de ligne), un paragraphe par ligne vide.
    lines = "Dupont Plomberie SARL" + "SIRET 812 345 678 00021"
    assert texts == ["Message", "x", lines, "Document interne."]
    assert "<w:br/>" in part(path, "word/document.xml")
    read = await call(tool, action="read", path="sortie/d.docx")
    assert read["sections"] == {"Message": PARAGRAPH}
    with pytest.raises(ToolError, match="« Mentions » est une section au texte fixe"):
        await call(
            tool,
            path="sortie/e.docx",
            template="t",
            sections={"Message": PARAGRAPH, "Mentions": PARAGRAPH},
        )


async def test_proposed_styles(tmp_path: Path) -> None:
    """Un style à toi qui a une description : proposé au modèle de langage, sur son type de
    bloc ; la charte dans la description de l'outil, le style d'un modèle à sa lecture."""
    charte = {
        "important_note": {
            "base": "note",
            "description": "Une mise en garde que le lecteur ne doit pas manquer.",
            "fill": "FDECEA",
            "border_left": "3pt C0392B",
        }
    }
    note = "style:\n  key_figure:\n    base: paragraph\n    description: Un chiffre clé, seul.\n    size: 16\n    bold: true\nsections:\n  Message:\n"
    tool = make(tmp_path, style={"spec": charte}, templates={"note": {"text": note}})
    assert "Styles proposés ('style' d'un bloc" in tool.spec.description
    assert "note : important_note (Une mise en garde" in tool.spec.description
    assert "key_figure" not in tool.spec.description
    schema = json.dumps(tool.spec.input_schema)
    assert '"important_note"' in schema and '"key_figure"' in schema
    found = await call(tool, action="template", template="note")
    assert found["styles"] == {"paragraph": {"key_figure": "Un chiffre clé, seul."}}
    assert ("styles proposés", "note : important_note") in tool.settings().options
    message = [
        {"type": "paragraph", "text": "1 250 €", "style": "key_figure"},
        {"type": "note", "text": "Fermé vendredi.", "style": "important_note"},
    ]
    path = await write_sections(tool, "n", "note", {"Message": message})
    styles = part(path, "word/styles.xml")
    assert '<w:basedOn w:val="Note"/>' in word_style(styles, "important_note")
    assert '<w:basedOn w:val="Normal"/>' in word_style(styles, "key_figure")
    check_order(styles)
    read = await call(tool, action="read", path="sortie/n.docx")
    assert read["sections"]["Message"] == message
    # Le style d'un modèle ne vaut que pour ses documents.
    with pytest.raises(ToolError, match="style « key_figure » : pas proposé pour un bloc paragraph"):
        await call(tool, path="sortie/libre.docx", template="none", blocks=message[:1])


# --- Cascade, héritage, contrôles ----------------------------------------------------------


def test_the_files_first_then_the_bases() -> None:
    """Chaque style complète le même style des fichiers d'avant ; puis il prend sa base."""
    charte = style_of(CHARTE)
    modele = style_of(
        {
            "table": {"borders": "grid"},
            "page": {"footer": {"lines": []}, "watermark": ""},
            "heading1": {"size": 11, "underline": True},
            "message_title": {"base": "heading1", "size": 14, "underline": False},
        }
    )
    style = resolve(charte, modele)
    assert style.look("table").borders == "grid"
    assert style.look("table_header").fill == "E8F1EC"  # gardé de la charte
    assert style.look("body").font == "Arial"
    assert style.footer is not None and style.footer.lines == ()
    assert style.footer.right == BandElement(("Page {page} / {pages}",))
    assert style.watermark is None
    heading = style.look("heading1")
    assert heading.uppercase and heading.size == 11 and heading.color == "0B5D3B"
    assert style.look("heading3").color == "C0392B"
    # message_title part de heading1 tel que les trois fichiers l'ont fait : la couleur de la
    # charte, les majuscules de la charte, la taille et le soulignement à lui.
    message = style.look("message_title")
    assert (message.color, message.uppercase, message.size, message.underline) == (
        "0B5D3B", True, 14, False
    )  # fmt: skip
    assert message.font == "Arial" and message.space_before == 18
    # Un style Word n'écrit que ce qui diffère de sa base.
    assert style.between("message_title", "heading1") == {
        "base": "heading1", "size": 14, "underline": False
    }  # fmt: skip


def test_the_property_registry_is_the_only_list() -> None:
    """Le registre est la seule liste des propriétés : le fichier lu (``Props``) et le style
    résolu (``Look``) en ont les noms, ni plus ni moins ; chacune dit sa valeur et son rôle ;
    une propriété propre va à des styles prévus, et une commune n'a pas de propriétaire."""
    assert set(Props.model_fields) == PROPERTY_NAMES
    assert set(Look.__dataclass_fields__) == PROPERTY_NAMES - {"base", "description"}
    assert len(PROPERTY_NAMES) == len(PROPERTIES)  # pas de doublon
    for found in PROPERTIES:
        assert found.value and found.role, found.name
        assert found.owners is None or found.owners <= set(BASES), found.name
    assert {"align", "size", "border"} <= {p.name for p in PROPERTIES if p.owners is None}
    # Une propriété hors du registre : dite avec la plus proche, à sa place dans le fichier.
    for given, said in (
        ({"heading1": {"colour": "1F3864"}}, ["charte.heading1.colour — propriété inconnue — vouliez-vous dire color ?"]),
        ({"heading1": {"pading": 2}}, ["charte.heading1.pading — propriété inconnue — vouliez-vous dire padding ?"]),
        ({"heading1": {"marge": 2}}, ["charte.heading1.marge — propriété inconnue"]),
    ):
        with pytest.raises(ValidationError) as caught:
            StyleFile.model_validate(given)
        assert describe(caught.value, "charte") == said


# La doc du format des styles, et celle des outils.
DOCS: Final = Path(__file__).resolve().parents[2] / "docs"


def _rows(text: str, section: str) -> list[list[str]]:
    """Les lignes des tableaux d'une section (« ## 8. ») de ``docs/style_format.md``,
    cellule par cellule, celles qui commencent par un nom."""
    part = text.split(f"\n## {section}. ", 1)[1].split("\n## ", 1)[0]
    return [[cell.strip() for cell in line.strip()[1:-1].split("|")] for line in part.splitlines() if line.startswith("| `")]


def _named(cell: str) -> list[str]:
    return re.findall(r"`([a-z_][a-z0-9_.]*)`", cell)


def test_the_format_document_matches_the_registry() -> None:
    """``docs/style_format.md`` dit le format tel que le code le lit : chaque propriété du
    registre (§ 8), sa famille, son unité, les styles qui la portent ; chaque style prévu et
    sa base (§ 7), et, sur la ligne de chaque style qui porte une propriété propre, cette
    propriété ; chaque réglage (§ 5). La doc des outils les nomme tous."""
    text = (DOCS / "style_format.md").read_text(encoding="utf-8")
    properties = {name: cells for cells in _rows(text, "8") for name in _named(cells[0])}
    assert set(properties) == PROPERTY_NAMES
    for found in PROPERTIES:
        cells = properties[found.name]
        assert cells[1] == ("commune" if found.owners is None else "propre"), found.name
        assert cells[3] == found.unit, found.name
        if found.owners is not None:
            assert set(_named(cells[5])) == found.owners, found.name
    styles = {name: cells for cells in _rows(text, "7") for name in _named(cells[0])}
    assert set(styles) == set(BASES)
    for name, base in BASES.items():
        assert styles[name][1] == (f"`{base}`" if base else "—"), name
    for found in PROPERTIES:
        for owner in found.owners or ():
            assert found.name in _named(styles[owner][4]), (owner, found.name)
    settings = {name for cells in _rows(text, "5") for name in _named(cells[0])}
    blocks = {"page": PageSpec, "image": ImageSpec, "chart": ChartSpec}
    expected = {name for name in SETTINGS if name not in blocks} | {f"{name}.{key}" for name, spec in blocks.items() for key in spec.model_fields}
    assert settings == expected
    # La doc des outils : les styles, les propriétés et les réglages, de « Les styles prévus »
    # à « La relecture ».
    howto = (DOCS / "howto" / "06-outils-fournis.md").read_text(encoding="utf-8")
    part = howto.split("**Les styles prévus.**", 1)[1].split("**La relecture**", 1)[0]
    said = set(_named(part))
    keys = {key for spec in blocks.values() for key in spec.model_fields}
    missing = (set(BASES) | PROPERTY_NAMES | SETTINGS | keys) - said
    assert not missing, sorted(missing)


def test_keys_renamed_in_2_0_21_are_refused_with_their_new_name() -> None:
    """Le format des styles de 2.0.21 (docs/style_format.md) : une clé d'avant est refusée
    avec celle qui la remplace."""
    for given, said in (
        ({"text": {"font": "Arial"}}, r"text → body : le style de base s'appelle body"),
        ({"page": {"size": "A4"}}, r"page\.size → page\.paper : renommé en 2\.0\.21"),
        ({"image": {"grid": True}}, r"image\.grid → image\.side_by_side"),
        ({"chart": {"colors": ["156082"], "title": "595959"}}, r"chart\.colors → chart\.series ; chart\.title → chart\.title_color"),
        ({"chart": {"text": "595959", "grid": "D9D9D9"}}, r"chart\.text → chart\.text_color ; chart\.grid → chart\.grid_color"),
    ):
        with pytest.raises(ValidationError, match=said):
            StyleFile.model_validate(given)
    with pytest.raises(ValueError, match=r"base inconnue « text » : le style de base s'appelle body"):
        resolve(StyleFile.model_validate({"small": {"base": "text", "size": 9}}))
    renamed = {"page": {"paper": "A5"}, "image": {"side_by_side": True}, "chart": {"series": ["156082"], "title_color": "1F3A5F"}}
    style = resolve(StyleFile.model_validate(renamed))
    assert style.page == (8391, 11906) and style.image_grid and style.chart.colors == ("156082",) and style.chart.title == "1F3A5F"
    # Le contenu de l'en-tête, du pied et du filigrane : dans page, toutes les clés dites
    # d'un coup ; les styles header, footer et watermark n'en gardent que l'apparence.
    old = {
        "header": {"logo": "images/logo.png", "logo_width": 3.5, "right": "{title}", "size": 8},
        "footer": {"right": "Page {page} / {pages}", "logo_position": "left"},
        "watermark": "BROUILLON",
    }
    moved = (
        r"header\.right → page\.header\.right ; header\.logo → page\.header\.logo ; "
        r"header\.logo_width → page\.header\.logo\.width ; footer\.right → page\.footer\.right ; "
        r"footer\.logo_position → page\.footer\.logo: true, dans la zone qui le porte ; "
        r"watermark: BROUILLON → page\.watermark: BROUILLON : le contenu de l'en-tête, du pied et du filigrane va dans page"
    )
    with pytest.raises(ValidationError, match=moved):
        StyleFile.model_validate(old)
    with pytest.raises(ValidationError, match=r"watermark\.text → page\.watermark"):
        StyleFile.model_validate({"watermark": {"text": "COPIE", "color": "D9D9D9"}})
    kept = resolve(StyleFile.model_validate({"header": {"size": 8}, "watermark": {"color": "F2F2F2"}, "page": {"watermark": "COPIE"}}))
    assert kept.look("header").size == 8 and kept.watermark_text == "COPIE"


async def test_a_right_rule_vertical_borders_and_the_vertical_alignment(tmp_path: Path) -> None:
    """``border_right`` (un filet à droite, enfin), ``borders: vertical`` (les filets entre
    colonnes et sur les côtés), ``valign`` d'un tableau : centré par default.yaml, en haut
    si la charte le dit ; dans une table de pied, celui du style de la cellule."""
    spec = {
        "note": {"border_right": "1pt 1F3A5F"},
        "table": {"borders": "vertical", "valign": "top"},
        "footer_page": {"base": "footer", "valign": "bottom"},
        "page": {"footer": {"table": {"widths": ["auto", 1.5], "rows": [[["a", "b"], {"text": "{page}", "style": "footer_page"}]]}}},
    }
    path = await write(make(tmp_path, style={"spec": spec}), "v", FACTURE)
    assert '<w:right w:val="single" w:sz="8" w:space="8" w:color="1F3A5F"/>' in word_style(part(path, "word/styles.xml"), "Note")
    document = part(path, "word/document.xml")
    assert '<w:insideV w:val="single"' in document and '<w:insideH w:val="single"' not in document
    assert '<w:vAlign w:val="top"/>' in document and '<w:vAlign w:val="center"/>' not in document
    footer = part(path, "word/footer1.xml")
    assert footer.count('<w:vAlign w:val="top"/>') == 1 and footer.count('<w:vAlign w:val="bottom"/>') == 1
    # Sans charte : les cellules d'un tableau centrées, comme avant (default.yaml).
    house_path = await write(make(tmp_path), "h", FACTURE)
    assert '<w:vAlign w:val="center"/>' in part(house_path, "word/document.xml")
    with pytest.raises(ValueError, match=r"style note\.valign : propre à footer, header, table"):
        resolve(StyleFile.model_validate({"note": {"valign": "top"}}))
    # La marge au-dessus d'une section, en cm (l'adresse dans la fenêtre d'une enveloppe) ;
    # space_before, son nom d'avant, est celui d'une propriété de style, en points.
    assert SectionSpec.model_validate({"margin_top": "40mm"}).margin_top == 4
    assert SectionSpec.model_validate({"margin_top": 4}).margin_top == 4
    with pytest.raises(ValidationError, match=r"space_before → margin_top : renommé en 2\.0\.21"):
        SectionSpec.model_validate({"space_before": 4})


def test_lengths_and_shares_take_their_unit() -> None:
    """Une longueur prend son unité (pt, mm, cm, in), et un nombre seul celle de sa
    propriété : pt pour la typographie et les espacements, cm pour la géométrie ; une
    largeur de section ou de colonne, une proportion (%). Le même style, écrit des deux
    façons, est le même."""
    written = {
        "body": {"size": "11pt", "space_after": "0,5cm", "indent": "3mm"},
        "fields": {"label_width": "40mm"},
        "footer": {"column_gap": "4mm"},
        "page": {"margins": "20mm", "margin_left": "1in", "footer": {"table": {"widths": ["50%", "auto", "15mm"], "rows": [["a", "b", "c"]]}}},
    }
    plain = {
        "body": {"size": 11, "space_after": 14.173228, "indent": 0.3},
        "fields": {"label_width": 4},
        "footer": {"column_gap": 0.4},
        "page": {"margins": 2, "margin_left": 2.54, "footer": {"table": {"widths": ["50%", "auto", 1.5], "rows": [["a", "b", "c"]]}}},
    }
    assert resolve(StyleFile.model_validate(written)) == resolve(StyleFile.model_validate(plain))
    style = resolve(StyleFile.model_validate(written))
    assert style.margins == (1134, 1134, 1440, 1134)  # 1 pouce = 1440 twips
    footer = style.footer
    assert footer is not None and footer.table is not None
    # 21 cm - 2 cm - 1 pouce : 9 332 twips ; la moitié, 1,5 cm (850), et le reste.
    assert band_columns(footer.table, style.text_width) == [4666, 3816, 850]
    assert SectionSpec.model_validate({"width": "55%"}).width == 55
    for given, said in (
        ({"body": {"size": "11px"}}, r"longueur '11px' : un nombre et, au besoin, son unité \(pt, mm, cm, in\)"),
        ({"body": {"indent": "2em"}}, r"longueur '2em'"),
        ({"page": {"footer": {"table": {"widths": ["120%"], "rows": [["a"]]}}}}, r"largeur '120%' : une proportion de plus de 0 à 100 %"),
        ({"page": {"footer": {"table": {"widths": ["31cm"], "rows": [["a"]]}}}}, r"largeur '31cm' : de plus de 0 à 30 cm"),
        ({"page": {"margins": "6cm"}}, r"less than or equal to 5"),
    ):
        with pytest.raises(ValidationError, match=said):
            StyleFile.model_validate(given)
    with pytest.raises(ValidationError, match=r"proportion '55 pour cent'"):
        SectionSpec.model_validate({"width": "55 pour cent"})


def test_one_to_four_lengths_in_the_order_of_css() -> None:
    """``border_space`` et ``padding`` : une à quatre longueurs, dans l'ordre de CSS (une :
    les quatre côtés ; deux : en haut et en bas, puis les côtés ; trois : en haut, les
    côtés, en bas ; quatre : en haut, à droite, en bas, à gauche)."""
    for given, sides in (
        ("4pt", (4.0, 4.0, 4.0, 4.0)),
        ("4pt 8pt", (4.0, 8.0, 4.0, 8.0)),
        ("1 2 3", (1.0, 2.0, 3.0, 2.0)),
        ("1 2 3 4", (1.0, 2.0, 3.0, 4.0)),
        (0, (0.0, 0.0, 0.0, 0.0)),
        ("0,5cm 2mm", (14.173228, 5.669291, 14.173228, 5.669291)),
    ):
        assert resolve(StyleFile.model_validate({"note": {"border_space": given}})).look("note").border_space == sides
    assert resolve(StyleFile.model_validate({"box": {"padding": "0.3cm 0.3cm 0.15cm"}})).look("box").padding == (0.3, 0.3, 0.15, 0.3)
    for written, said in (
        ({"note": {"border_space": "1 2 3 4 5"}}, r"une à quatre longueurs \(4pt 8pt\)"),
        ({"note": {"border_space": "21pt"}}, r"longueur '21pt' : de 0 à 20 pt"),
        ({"box": {"padding": "3cm"}}, r"longueur '3cm' : de 0 à 2 cm"),
        ({"note": {"border_space": "4px"}}, r"longueur '4px'"),
    ):
        with pytest.raises(ValidationError, match=said):
            StyleFile.model_validate(written)
    with pytest.raises(ValueError, match=r"style note\.padding : propre à box, table"):
        resolve(StyleFile.model_validate({"note": {"padding": 1}}))


async def test_the_distances_and_spaces_of_the_page_come_from_the_style(tmp_path: Path) -> None:
    """Les distances et les espaces que le code écrivait en dur viennent du style
    (default.yaml, et la charte qui les change) : l'en-tête et le pied au bord, l'écart et
    l'espace après des sections côte à côte, le cadre d'une section, l'écart entre un filet
    et le texte, les marges et l'espace autour d'un tableau, les retraits d'une liste et du
    sommaire, la place pour signer, l'espace après une image et un graphique."""
    spec: dict[str, Any] = {
        "page": {"header_distance": "2cm", "footer_distance": "8mm", "beside_gap": "1cm", "beside_space": "12pt"},
        "image": {"space_after": 9},
        "chart": {"space_after": 4},
        "body": {"border_space": "2pt 5pt"},
        "note": {"border_left": "3pt 1F3A5F"},
        "table": {"space_before": 6, "space_after": 10, "padding": "3pt 0.2cm"},
        "table_group": {"space_after": 2},
        "box": {"padding": "0.5cm"},
        "list": {"level_indent": "1cm", "marker_space": "0.5cm"},
        "toc": {"level_indent": "1cm"},
        "toc1": {"space_after": 6},
        "signature": {"sign_height": "3cm"},
    }
    blocks = ("image", "chart", "signature", "toc")
    tool = make(tmp_path, blocks, style={"spec": spec}, templates={"facture": {"text": FACTURE_YAML}})
    free = [
        {"type": "toc"},
        {"type": "heading", "text": "Un"},
        {"type": "heading", "text": "Deux", "level": 2},
        {"type": "note", "text": "Une note."},
        {"type": "list", "items": ["a", {"text": "b", "items": ["c"]}]},
        {"type": "table", "columns": [{"header": "A"}], "rows": [["x"]]},
        {"type": "image", "path": "sortie/a.png", "width": "small"},
        {"type": "chart", "kind": "pie", "value_type": "percent", "categories": ["P", "C"], "series": [{"name": "Part", "values": [0.6, 0.4]}]},
        {"type": "signature", "signers": ["Le client"]},
    ]
    path = await write(tool, "d", free, template="none")
    document = part(path, "word/document.xml")
    styles = part(path, "word/styles.xml")
    assert 'w:header="1134" w:footer="454"' in document
    assert '<w:left w:val="single" w:sz="24" w:space="5" w:color="1F3A5F"/>' in word_style(styles, "Note")
    # Le tableau : ses marges, un paragraphe de 6 pt avant lui, 10 pt après le paragraphe
    # vide qui le suit ; ses cellules sans espacement (TableText : 0 après).
    assert '<w:top w:w="60" w:type="dxa"/><w:left w:w="113" w:type="dxa"/>' in document
    assert '<w:spacing w:before="0" w:after="0" w:line="120" w:lineRule="exact"/></w:pPr></w:p><w:tbl>' in document
    assert '</w:tbl><w:p><w:pPr><w:pStyle w:val="Normal"/><w:spacing w:after="200"/>' in document
    cells = word_style(styles, "TableText")
    assert 'w:after="0"' in cells and "w:before" not in cells
    assert '<w:ind w:left="567" w:hanging="284"/>' in part(path, "word/numbering.xml")
    assert '<w:ind w:left="567"/>' in word_style(styles, "TOC2")
    assert '<w:spacing w:before="60" w:after="120"/>' in word_style(styles, "TOC1")
    assert 'w:after="1701"' in document  # 3 cm sous « Signature : »
    assert '<w:spacing w:after="180"/>' in document and '<w:spacing w:after="80"/>' in document
    # Des sections côte à côte, l'une encadrée : l'écart, le cadre, l'espace après.
    facture = part(await write_sections(tool, "f", "facture", FACTURE_SECTIONS), "word/document.xml")
    assert '<w:tcMar><w:right w:w="567" w:type="dxa"/></w:tcMar>' in facture
    assert '<w:tcMar><w:top w:w="284" w:type="dxa"/><w:left w:w="284" w:type="dxa"/><w:bottom w:w="284" w:type="dxa"/>' in facture
    assert '</w:tbl><w:p><w:pPr><w:pStyle w:val="Normal"/><w:spacing w:after="240"/>' in facture
    # Une page de garde sans margin_top, des colonnes : page.cover_margin_top, page.column_gap.
    pages = {"page": {"cover_margin_top": "4cm", "column_gap": "0.6cm"}}
    report = make(tmp_path, ("image",), style={"spec": pages}, templates={"rapport": {"text": RAPPORT_YAML}})
    rapport = part(await write_sections(report, "r", "rapport", RAPPORT_SECTIONS), "word/document.xml")
    assert 'w:line="2268" w:lineRule="exact"' in rapport and '<w:cols w:num="2" w:space="340"/>' in rapport


async def test_the_spacing_of_a_band_table_is_around_it(tmp_path: Path) -> None:
    """L'espace avant et après du style d'une table d'en-tête ou de pied vaut autour
    d'elle : un paragraphe de sa hauteur avant, l'espace avant le paragraphe qui la clôt ;
    ses cellules n'ont que ce que leur style écrit en plus (0 sans lui)."""
    spec = {
        "footer": {"space_before": 4, "space_after": 6},
        "footer_page": {"base": "footer", "space_after": 2},
        "page": {"footer": {"table": {"widths": ["auto", 2], "rows": [["Gauche", {"text": "{page}", "style": "footer_page"}]]}}},
    }
    footer = part(await write(make(tmp_path, style={"spec": spec}), "b", FACTURE), "word/footer1.xml")
    assert footer.index('w:line="80" w:lineRule="exact"') < footer.index("<w:tbl>")
    assert '<w:spacing w:before="120" w:after="0" w:line="20" w:lineRule="exact"/>' in footer
    assert '<w:pStyle w:val="Footer"/><w:spacing w:before="0" w:after="0"/>' in footer
    assert '<w:pStyle w:val="footer_page"/><w:spacing w:before="0" w:after="40"/>' in footer
    # Sans espacement : ni paragraphe avant, ni espacement dans les cellules, comme avant.
    plain = {**spec, "footer": {}}
    bare = part(await write(make(tmp_path, style={"spec": plain}), "c", FACTURE), "word/footer1.xml")
    assert 'w:lineRule="exact"/></w:pPr></w:p><w:tbl>' not in bare and '<w:spacing w:before="0" w:after="0"/>' not in bare


@pytest.mark.parametrize(
    ("given", "said"),
    [
        ({"colors": {"brand": "bleu"}}, "six chiffres hexadécimaux"),
        ({"colors": {"accede": "000000"}}, "pas six chiffres hexadécimaux"),
        ({"page": {"footer": {"center": "{date}"}}}, r"champ inconnu \{date\}"),
        ({"page": {"header": {"right": "{titre}"}}}, r"\{titre\} s'écrit \{title\} depuis 2\.1\.0"),
        ({"footer": {"lines": ["a"], "center": "b"}}, r"footer\.center → page\.footer\.center ; footer\.lines → page\.footer\.lines : le contenu"),
        ({"watermark": "BROUILLON"}, r"watermark: BROUILLON → page\.watermark: BROUILLON"),
        ({"body": {"font": "Arial<"}}, "un nom de police"),
        ({"beside": [{"sections": ["A", "B"]}]}, "se déclare dans le modèle de document"),
        ({"table": {"borders": "pointillés"}}, "'none', 'horizontal', 'vertical' or 'grid'"),
        ({"page": {"margins": 9}}, "less than or equal to 5"),
        ({"heading1": {"colour": "1F3864"}}, "Extra inputs"),
        ({"heading1": {"border_bottom": "2pt"}}, "« épaisseur couleur », ex. 1pt 2F5496"),
        ({"heading1": {"border_bottom": "9pt 1F3864"}}, "une épaisseur de 0,25 à 6 pt"),
        ({"heading_1": {"size": 12}}, "vouliez-vous dire heading1"),
        ({"important": {"fill": "FDECEA"}}, r"un style à toi dit de quel style il part \(base"),
        ({"title": {"size": 20}, "headings": {}}, "format de la 2.0 : title → document_title"),
        ({"header": {"logo": "logo.png", "logo_width": 3}}, r"header\.logo → page\.header\.logo ; header\.logo_width → page\.header\.logo\.width"),
        ({"sections": {"A": {"box": True}}}, "sections : la disposition des sections"),
        ({"labels": {"tax": "TVA"}}, r"labels\.tax 'TVA' : \{rate\} \(le taux\)"),
        ({"labels": {"tax": "TVA {taux}"}}, r"labels\.tax 'TVA \{taux\}'"),
    ],
)  # fmt: skip
def test_a_style_file_is_checked(given: dict[str, Any], said: str) -> None:
    with pytest.raises(ValidationError, match=said):
        StyleFile.model_validate(given)


@pytest.mark.parametrize(
    ("given", "said"),
    [
        ({"heading1": {"color": "brand"}}, r"heading1\.color : couleur « brand » inconnue"),
        ({"x": {"base": "y"}}, "style x : base inconnue « y »"),
        ({"a": {"base": "b"}, "b": {"base": "a"}}, r"base en boucle \(a → b → a\)"),
        ({"note": {"borders": "grid"}}, r"style note\.borders : propre à footer, header, table"),
        ({"note": {"label_width": 3}}, r"style note\.label_width : propre à fields"),
        ({"table": {"row_height": 2}}, r"style table\.row_height : propre à attendance"),
        ({"chart": {"series": ["brand"]}}, r"chart\.color : couleur « brand » inconnue"),
    ],
)  # fmt: skip
def test_the_cascade_is_checked(given: dict[str, Any], said: str) -> None:
    with pytest.raises(ValueError, match=said):
        resolve(style_of(given))


def test_properties_and_descriptions_of_your_styles() -> None:
    # Une propriété propre va aussi aux styles qui partent de son style.
    style = resolve(style_of({"brand_table": {"base": "table", "borders": "none"}}))
    assert style.look("brand_table").borders == "none" and style.look("brand_table").size == 10
    # Une description, à un style à toi qui part d'un style de bloc.
    for given in (
        {"note": {"description": "x"}},
        {"red_box": {"base": "box", "description": "x"}},
    ):
        with pytest.raises(ValueError, match="une description ne sert qu'à un style à toi"):
            resolve(style_of(given))


@pytest.mark.parametrize(
    ("styles", "said"),
    [
        ("{heading1: message_title}", "style « message_title » inconnu"),
        ("{heading1: small_text}", "« small_text » ne part pas de heading1 (base : paragraph)"),
        ("{box: heading1}", "« heading1 » ne part pas de box"),
    ],
)
def test_the_styles_of_a_section_are_checked(tmp_path: Path, styles: str, said: str) -> None:
    text = f"style:\n  small_text: {{base: paragraph}}\nsections:\n  A: {{styles: {styles}}}\n"
    with pytest.raises(ValueError, match=re.escape("« A ».styles.") + ".*" + re.escape(said)):
        make(tmp_path, templates={"t": {"text": text}})


def test_a_logo_must_be_an_image(tmp_path: Path) -> None:
    charte = {"page": {"header": {"logo": {"file": b"GIF89a...."}, "left": {"logo": True}}}}
    with pytest.raises(ValueError, match=r"logo de page\.header : image GIF"):
        make(tmp_path, style={"spec": charte})
    with pytest.raises(ValueError, match=r"logo de page\.header \(logo\.png\) non lu"):
        make(tmp_path, style={"spec": {"page": {"header": {"logo": "logo.png", "left": {"logo": True}}}}})
    with pytest.raises(ValueError, match=r"non lu — les styles se lisent au chargement"):
        make(tmp_path, style="charte.yaml")


def test_an_uncommon_font_is_said(tmp_path: Path) -> None:
    tool = make(tmp_path, style={"spec": {"body": {"font": "Inter"}}})
    options = dict(tool.settings().options)
    assert options["polices"].startswith("Inter : hors des polices de tous les postes")


def test_pdf_takes_the_style_of_word() -> None:
    """Depuis 2.1.0, ``loom.pdf`` prend les mêmes styles, de la même façon."""
    folders = [{"name": "s", "path": "s", "rights": ["write"]}]
    params = PdfParams.model_validate({"folders": folders, "style": "charte.yaml"})
    assert params.style is not None and params.style.file == Path("charte.yaml")
    with pytest.raises(ValidationError, match="le modèle nomme son style dans son fichier"):
        PdfParams.model_validate({"folders": folders, "templates": {"r": {"file": "r.yaml", "style": "r.yaml"}}})


# --- Le chargement de la config --------------------------------------------------------------


def word_agent(**reference: Any) -> dict[str, Any]:
    return demo_agent(tools=[{"python": "loom.word", **reference}])


def test_styles_are_read_when_the_config_loads(demo: ConfigFactory, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "sortie").mkdir()
    folders = [{"name": "sortie", "path": "sortie", "rights": ["read", "write"]}]
    reference = {
        "folders": folders,
        "style": "styles/charte.yaml",
        "templates": {"facture": "facture.yaml"},
    }
    path = demo(agents=[word_agent(**reference)], storage=JSONL)
    prompts = tmp_path / "prompts"
    facture = "style: styles/facture.yaml\nsections:\n  Émetteur: {show_title: false}\n  Client:\n"
    (prompts / "facture.yaml").write_text(facture, encoding="utf-8")
    with pytest.raises(ConfigError, match=r"templates\[facture\]\.style — style introuvable"):
        load_config(path)
    (prompts / "styles").mkdir()
    (prompts / "styles" / "facture.yaml").write_text("{}\n", encoding="utf-8")
    with pytest.raises(ConfigError, match=r"tools\[loom\.word\]\.style — style introuvable"):
        load_config(path)
    (prompts / "styles" / "charte.yaml").write_text(
        "colors: {brand: 0B5D3B}\nbody: {font: Arial}\nheading1: {color: brand}\n"
        "page:\n  header:\n    logo: { file: logo.png, width: 3 }\n    left: { logo: true }\n    right: '{title}'\n",
        encoding="utf-8",
    )
    (prompts / "styles" / "facture.yaml").write_text("heading2: {color: brand}\n", encoding="utf-8")
    with pytest.raises(ConfigError, match=r"style\.page\.header\.logo — logo introuvable"):
        load_config(path)
    (prompts / "logo.png").write_bytes(LOGO)
    config = load_config(path)
    assert config is not None
    assert main(["--config", str(path), "validate"]) == 0
    assert "style : charte ; style de modèle : facture" in capsys.readouterr().out
    for written, said in (
        ("table: {borders: pointillés}\n", r"templates\[facture\]\.style\.table\.borders"),
        ("table: {}\ntable: {}\n", "YAML invalide"),
        (
            "page:\n  footer:\n    center: Qui joindre : le standard\n",
            "un texte qui contient « : » ou « # » s'écrit entre guillemets",
        ),
        ("title: {size: 20}\n", r"facture\]\.style : style au format de la 2\.0"),
        ("sections:\n  Annexes: {}\n", "la disposition des sections se déclare dans le modèle"),
        (
            "heading2: {color: red}\n",
            r"templates\[facture\]\.style : heading2\.color : couleur « red » inconnue "
            r"\(colors : brand\)",
        ),
    ):
        (prompts / "styles" / "facture.yaml").write_text(written, encoding="utf-8")
        with pytest.raises(ConfigError, match=said):
            load_config(path)
    (prompts / "styles" / "facture.yaml").write_text("{}\n", encoding="utf-8")
    # Les styles des sections, cherchés dans la cascade du modèle.
    (prompts / "facture.yaml").write_text(
        "style: styles/facture.yaml\nsections:\n  A: {styles: {heading1: nope}}\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match=r"templates\[facture\] : « A »\.styles\.heading1"):
        load_config(path)
    # Un style écrit dans le modèle.
    (prompts / "facture.yaml").write_text(
        "style:\n  title_x: {base: heading1, color: brand}\nsections:\n  A: {styles: {heading1: title_x}}\n",
        encoding="utf-8",
    )
    load_config(path)
    (prompts / "facture.yaml").write_text(
        "style:\n  title_x: {base: heading1, colour: brand}\nsections:\n  A:\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match=r"templates\[facture\]\.style\.title_x\.colour"):
        load_config(path)


def test_a_style_written_in_the_config(demo: ConfigFactory, tmp_path: Path) -> None:
    """``style`` : un chemin, ou le style écrit en ligne dans la config."""
    (tmp_path / "sortie").mkdir()
    folders = [{"name": "sortie", "path": "sortie", "rights": ["read", "write"]}]
    inline = {"colors": {"brand": "0B5D3B"}, "heading1": {"color": "brand"}}
    config = load_config(demo(agents=[word_agent(folders=folders, style=inline)], storage=JSONL))
    declared = config.agents[0].tools[0]
    assert isinstance(declared, PythonTool)
    params = WordParams.model_validate(builtin_settings(declared, WordParams))
    assert params.style is not None and params.style.spec is not None
    assert resolve(params.style.spec).look("heading1").color == "0B5D3B"


async def test_a_style_written_in_yaml_writes_the_facture(demo: ConfigFactory, tmp_path: Path) -> None:
    """Le chemin réel : les fichiers lus par load_config, l'outil monté par sa fabrique."""
    (tmp_path / "sortie").mkdir()
    prompts = tmp_path / "prompts"
    prompts.mkdir(parents=True, exist_ok=True)
    (prompts / "facture.yaml").write_text(
        FACTURE_YAML.replace("width: 55", "width: 50").replace("width: 45", "width: 50"),
        encoding="utf-8",
    )
    (prompts / "facture-style.yaml").write_text("heading1: {size: 12}\n", encoding="utf-8")
    folders = [FOLDER]
    reference = {"templates": {"facture": "facture.yaml"}}
    config = load_config(demo(agents=[word_agent(folders=folders, **reference)], storage=JSONL))
    declared = config.agents[0].tools[0]
    assert isinstance(declared, PythonTool)
    params = WordParams.model_validate(builtin_settings(declared, WordParams))
    tool = build(params, BuiltinContext(base_dir=tmp_path))
    OUTPUTS[id(tool)] = tmp_path / "sortie"
    path = await write_sections(tool, "f", "facture", FACTURE_SECTIONS)
    assert '<w:gridCol w:w="4819"/><w:gridCol w:w="4819"/>' in part(path, "word/document.xml")
    assert '<w:sz w:val="20"/>' in word_style(part(path, "word/styles.xml"), "quiet_title")


# --- La relecture --------------------------------------------------------------------------


def test_a_cancelled_numbering_is_not_a_list() -> None:
    """B-4 : un style qui annule la numérotation de son parent (numId 0) n'est pas une liste."""
    styles = (
        '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:style w:type="paragraph" w:styleId="Num"><w:name w:val="Numbered"/>'
        '<w:pPr><w:numPr><w:numId w:val="1"/></w:numPr></w:pPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="Plain"><w:name w:val="Plain"/>'
        '<w:basedOn w:val="Num"/><w:pPr><w:numPr><w:numId w:val="0"/></w:numPr></w:pPr></w:style>'
        "</w:styles>"
    )
    body = (
        '<w:p><w:pPr><w:pStyle w:val="Num"/></w:pPr><w:r><w:t>Un</w:t></w:r></w:p><w:p><w:pPr><w:pStyle w:val="Plain"/></w:pPr><w:r><w:t>Deux</w:t></w:r></w:p>'
    )
    found = reader_module.read(docx(body, styles), "d.docx", 0)
    assert found["blocks"] == [
        {"type": "list", "ordered": False, "items": ["Un"]},
        {"type": "paragraph", "text": "Deux"},
    ]


def test_a_caption_numbered_by_a_complex_field() -> None:
    """Une légende que Word a réenregistrée : le champ SEQ en fldChar, pas en fldSimple."""
    body = (
        '<w:p><w:pPr><w:pStyle w:val="Caption"/></w:pPr><w:r><w:t xml:space="preserve">Tableau '
        '</w:t></w:r><w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText> SEQ '
        'Tableau \\* ARABIC </w:instrText></w:r><w:r><w:fldChar w:fldCharType="separate"/></w:r>'
        '<w:r><w:t>3</w:t></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r><w:r><w:t '
        'xml:space="preserve"> — Pression</w:t></w:r></w:p>'
    )
    found = reader_module.read(docx(body), "d.docx", 0)
    assert found["blocks"] == [{"type": "paragraph", "text": "Pression"}]


def docx(body: str, styles: str | None = None) -> bytes:
    ns = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as archive:
        archive.writestr("word/document.xml", f"<w:document {ns}><w:body>{body}</w:body></w:document>")
        if styles is not None:
            archive.writestr("word/styles.xml", styles)
            archive.writestr(
                "word/_rels/document.xml.rels",
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
                'relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/'
                'officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>',
            )
    return raw.getvalue()


# --- LibreOffice --------------------------------------------------------------------------


def convert(soffice: str, source: Path, out: Path, target: str) -> Path:
    done = subprocess.run(
        [
            soffice,
            f"-env:UserInstallation={(out.parent / 'profil').as_uri()}",
            "--headless",
            "--convert-to",
            target,
            "--outdir",
            str(out),
            str(source),
        ],
        capture_output=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    return out / f"{source.stem}.{target.split(':')[0]}"


async def test_libreoffice_prints_the_facture(tmp_path: Path, soffice: str) -> None:
    pypdf = pytest.importorskip("pypdf")
    tool = styled(tmp_path)
    path = await write_sections(tool, "facture", "facture", FACTURE_SECTIONS, title="Facture")
    pdf = pypdf.PdfReader(convert(soffice, path, tmp_path / "pdf", "pdf"))
    assert len(pdf.pages) == 1
    text = pdf.pages[0].extract_text()
    # Les titres masqués ne s'impriment pas ; le contenu des cellules côte à côte, si.
    assert "Émetteur" not in text and "Description" not in text
    assert "Dupont Plomberie" in text and "SCI Les Tilleuls" in text
    assert "Informations de paiement" in text and "SIRET 812 345 678 00021" in text
    assert "Page 1 / 1" in text


async def test_libreoffice_lays_out_the_report(tmp_path: Path, soffice: str) -> None:
    pypdf = pytest.importorskip("pypdf")
    tool = styled(tmp_path, None)
    path = await write_sections(tool, "rapport", "rapport", RAPPORT_SECTIONS, title="Rapport")
    pages = pypdf.PdfReader(convert(soffice, path, tmp_path / "pdf", "pdf")).pages
    # Page de garde, texte, paysage, conditions générales en colonnes.
    assert len(pages) == 4
    landscape = [float(page.mediabox.width) > float(page.mediabox.height) for page in pages]
    assert landscape == [False, False, True, False]
    assert "Page de garde" not in pages[0].extract_text()
    assert "1. Contexte" in pages[1].extract_text()
    assert "Figure 2 — Après" in pages[1].extract_text()
    assert "Tableau 1 — Pression mesurée" in pages[2].extract_text()


async def test_sections_survive_libreoffice(tmp_path: Path, soffice: str) -> None:
    """Un document repris par LibreOffice (docx → docx) : ses signets restent, ses sections
    se relisent, côte à côte comprises."""
    tool = styled(tmp_path)
    path = await write_sections(tool, "facture", "facture", FACTURE_SECTIONS, title="Facture")
    again = convert(soffice, path, tmp_path / "repris", "docx:MS Word 2007 XML")
    (tmp_path / "sortie" / "repris.docx").write_bytes(again.read_bytes())
    read = await call(tool, action="read", path="sortie/repris.docx")
    assert read["template"] == "facture" and "notes" not in read
    assert list(read["sections"]) == list(FACTURE_SECTIONS)
    assert read["sections"]["Client"] == FACTURE_SECTIONS["Client"]
    assert read["sections"]["Conditions et termes"] == FACTURE_SECTIONS["Conditions et termes"]


# --- Étape 2 : signature, sommaire, liste à cocher (blocks) ---------------------------------

LIVRAISON: Final[list[dict[str, Any]]] = [
    {"type": "heading", "text": "Contrôles"},
    {
        "type": "list",
        "checkboxes": True,
        "items": [
            "[x] Coupure d'eau faite",
            {"text": "[X] Fuite localisée", "items": ["[x] Raccord", "[ ] Canalisation"]},
            "[ ] Pièce disponible",
        ],
    },
    {"type": "signature", "signers": ["Le client", "L'entreprise"], "mention": "Bon pour accord"},
]
SOMMAIRE: Final[list[dict[str, Any]]] = [
    {"type": "heading", "text": "Page de garde"},
    {"type": "toc"},
    {"type": "heading", "text": "Contexte"},
    {"type": "heading", "text": "Photos", "level": 2},
    {"type": "heading", "text": "Détail", "level": 3},
    {"type": "heading", "text": "Mesures"},
    {"type": "heading", "text": "Conditions générales"},
]


def extra(tmp_path: Path, **params: Any) -> WordTool:
    return make(tmp_path, blocks=("image", "signature", "toc", "checkboxes"), **params)


async def test_optional_blocks_are_off_unless_declared(tmp_path: Path) -> None:
    plain = make(tmp_path)
    tool = extra(tmp_path)
    schema = json.dumps(plain.spec.input_schema)
    assert "signature" not in schema and "checkboxes" not in schema
    assert "Sommaire ('type': 'toc')" not in plain.spec.description
    assert "Sommaire ('type': 'toc')" in tool.spec.description
    assert '"toc"' in json.dumps(tool.spec.input_schema)
    # Le coût : schéma et description, à quelques caractères près de la mesure de l'analyse.
    grown = len(json.dumps(tool.spec.input_schema, ensure_ascii=False, separators=(",", ":")))
    grown += len(tool.spec.description)
    before = len(json.dumps(plain.spec.input_schema, ensure_ascii=False, separators=(",", ":")))
    before += len(plain.spec.description)
    assert 800 < grown - before < 880
    assert ("blocs", "image, signature, toc, checkboxes") in tool.settings().options
    with pytest.raises(ToolError, match=r"blocks\.0 : Input tag 'signature'"):
        await call(plain, path="sortie/a.docx", blocks=LIVRAISON[2:])


async def test_a_checklist_and_a_signature(tmp_path: Path) -> None:
    tool = extra(tmp_path)
    path = await write(tool, "livraison", LIVRAISON, title="PV de livraison")
    document = part(path, "word/document.xml")
    check_order(document)
    assert document.count('<w:pStyle w:val="LoomCheck"/>') == 3
    assert document.count('<w:pStyle w:val="LoomCheck2"/>') == 2
    assert document.count("☒") == 3 and document.count("☐") == 2
    assert "[x]" not in document and "numPr" not in document
    signature = re.search(r'<w:tblStyle w:val="LoomSignature"/>.*?</w:tbl>', document)
    assert signature is not None
    assert signature.group().count('<w:pStyle w:val="LoomSigner"/>') == 2
    assert signature.group().count("Bon pour accord") == 2
    styles = part(path, "word/styles.xml")
    assert '<w:name w:val="loom signature"/>' in styles
    assert '<w:name w:val="loom checkbox 2"/>' in styles
    read = await call(tool, action="read", path="sortie/livraison.docx")
    assert read["blocks"][2:] == [
        {
            "type": "list",
            "ordered": False,
            "checkboxes": True,
            "items": [
                "[x] Coupure d'eau faite",
                {"text": "[x] Fuite localisée", "items": ["[x] Raccord", "[ ] Canalisation"]},
                "[ ] Pièce disponible",
            ],
        },
        {
            "type": "signature",
            "signers": ["Le client", "L'entreprise"],
            "mention": "Bon pour accord",
        },
    ]
    # Relu puis réécrit tel quel : le même fichier.
    again = await call(tool, path="sortie/encore.docx", title="PV de livraison", blocks=read["blocks"][1:])
    assert again["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


async def test_what_the_extra_blocks_refuse(tmp_path: Path) -> None:
    tool = extra(tmp_path)
    for blocks, said in (
        (
            [{"type": "list", "checkboxes": True, "items": ["Fait"]}],
            r"élément 1 : dans une liste à cocher \(checkboxes\), chaque élément commence par",
        ),
        (
            [{"type": "list", "checkboxes": True, "ordered": True, "items": ["[x] a"]}],
            "une liste à cocher n'est pas numérotée",
        ),
        ([{"type": "signature", "signers": [" "]}], "signataire 1 : texte vide"),
        ([{"type": "signature", "signers": ["x" * 81]}], "81 caractères, au-delà de 80"),
        ([{"type": "toc"}, {"type": "toc"}], "un seul sommaire par document"),
    ):
        with pytest.raises(ToolError, match=said):
            await call(tool, path="sortie/a.docx", blocks=blocks)
    with pytest.raises(ToolError, match=r"signers : List should have at most 4 items"):
        await call(tool, path="sortie/a.docx", blocks=[{"type": "signature", "signers": ["a"] * 5}])
    alone = await call(tool, path="sortie/b.docx", blocks=[{"type": "toc"}])
    assert alone["warnings"] == ["sommaire : le document n'a aucun titre, le sommaire sera vide"]


async def test_the_contents_lists_the_headings(tmp_path: Path) -> None:
    rapport = "sections:\n  Page de garde: {show_title: false, cover: true}\n  Contexte:\n  Mesures:\n  Conditions générales:\n"
    tool = extra(
        tmp_path,
        style={"spec": {"numbering": "decimal"}},
        templates={"rapport": {"text": rapport}},
    )
    text = [{"type": "paragraph", "text": "x"}]
    sections = {
        "Page de garde": [{"type": "toc"}],
        "Contexte": [{**SOMMAIRE[3]}, {**SOMMAIRE[4]}, *text],
        "Mesures": text,
        "Conditions générales": text,
    }
    path = await write_sections(tool, "rapport", "rapport", sections, title="Rapport")
    document = part(path, "word/document.xml")
    check_order(document)
    assert '<w:fldChar w:fldCharType="begin" w:dirty="true"/>' in document
    assert ' TOC \\o "1-2" \\h \\z \\u </w:instrText>' in document
    labels = re.findall(r'<w:hyperlink w:anchor="(_Toc_loom_\d)" w:history="1"><w:r><w:t '
                        r'xml:space="preserve">([^<]*)</w:t>', document)  # fmt: skip
    # La page de garde, sans titre, n'y est pas ; un titre de niveau 3, non plus (levels : 2).
    assert labels == [
        ("_Toc_loom_1", "1. Contexte"),
        ("_Toc_loom_2", "1.1 Photos"),
        ("_Toc_loom_3", "2. Mesures"),
        ("_Toc_loom_4", "3. Conditions générales"),
    ]
    for mark, _ in labels:
        assert f'<w:bookmarkStart w:id="{mark[-1]}" w:name="{mark}"/>' in document
        assert f" PAGEREF {mark} \\h " in document
    assert document.count('<w:fldChar w:fldCharType="end"/>') == 5
    read = await call(tool, action="read", path="sortie/rapport.docx")
    assert read["sections"]["Page de garde"] == [{"type": "toc"}]
    blocks = [b for written in read["sections"].values() for b in written]
    assert not any(b.get("text", "").startswith("1.") for b in blocks)


async def test_the_charte_sets_the_contents(tmp_path: Path) -> None:
    tool = extra(tmp_path, style={"spec": {"toc": {"levels": 1, "page_numbers": False}}})
    path = await write(tool, "d", SOMMAIRE)
    document = part(path, "word/document.xml")
    assert ' TOC \\o "1-1" \\h \\z \\u \\n </w:instrText>' in document
    assert "PAGEREF" not in document and 'w:leader="dot"' not in document
    assert len(re.findall("<w:hyperlink ", document)) == 4
    # Sans numérotation, l'entrée est le titre seul.
    assert '<w:t xml:space="preserve">Contexte</w:t></w:r></w:hyperlink>' in document


async def test_a_tool_without_the_blocks_says_so_when_reading(tmp_path: Path) -> None:
    await write(extra(tmp_path), "livraison", [*LIVRAISON, *SOMMAIRE[1:3]])
    read = await call(make(tmp_path), action="read", path="sortie/livraison.docx")
    assert read["notes"] == [
        "Lu : checkboxes (1), signature (1), toc (1) ; cet outil n'en écrit pas : les retirer "
        "avant de réécrire le document (ou blocks: [checkboxes, signature, toc] dans la config "
        "de l'outil)."
    ]
    # Un outil qui en écrit une partie ne parle que des autres.
    partial = make(tmp_path, blocks=("toc",))
    read = await call(partial, action="read", path="sortie/livraison.docx")
    assert "(ou blocks: [checkboxes, signature] dans" in read["notes"][0]


def test_pdf_writes_signature_toc_and_checkboxes() -> None:
    folders = [{"name": "s", "path": "s", "rights": ["write"]}]
    blocks = ["chart", "signature", "toc", "checkboxes"]
    assert PdfParams.model_validate({"folders": folders, "blocks": blocks}).blocks == (
        "chart",
        "signature",
        "toc",
        "checkboxes",
    )


async def test_libreoffice_prints_the_extra_blocks(tmp_path: Path, soffice: str) -> None:
    pypdf = pytest.importorskip("pypdf")
    tool = extra(tmp_path, style={"spec": {"numbering": "decimal"}})
    path = await write(tool, "d", [*SOMMAIRE[1:], *LIVRAISON], title="Rapport")
    text = "".join(page.extract_text() for page in pypdf.PdfReader(convert(soffice, path, tmp_path / "pdf", "pdf")).pages)
    assert "Sommaire" in text and "2. Mesures" in text
    assert "Coupure d'eau faite" in text and "Canalisation" in text
    assert text.count("Bon pour accord") == 2 and "Signature :" in text


# --- Le texte enrichi (lot 5) -------------------------------------------------------------

RICH_BLOCKS: Final[list[dict[str, Any]]] = [
    {"type": "heading", "text": "Objet"},
    {"type": "paragraph", "text": "Voir [le site](https://a.fr)^[Une note.] et §[Objet]."},
    {"type": "list", "items": [{"text": "a", "items": [{"text": "b", "items": ["c"]}]}]},
    {"type": "list", "ordered": True, "items": ["un", "deux"]},
    {"type": "fields", "items": [["Client", "Dupont"], ["Signature", ""]]},
    {"type": "code", "text": "x = 1\n  y = 2"},
]


async def test_the_styles_of_rich_text(tmp_path: Path) -> None:
    charte = {
        "link": {"color": "C00000"},
        "fields": {"label_width": 3},
        "fields_label": {"italic": True},
        "code": {"fill": "EEEEEE"},
        "footnote": {"size": 8},
    }
    tool = make(tmp_path, blocks=("fields", "code"), style={"spec": charte})
    path = await write(tool, "riche", RICH_BLOCKS)
    document = part(path, "word/document.xml")
    styles = part(path, "word/styles.xml")
    numbering = part(path, "word/numbering.xml")
    for xml in (document, styles, numbering, part(path, "word/footnotes.xml")):
        check_order(xml)
    # Le lien : un style de caractère, la couleur et le soulignement du style link.
    assert '<w:color w:val="C00000"/>' in word_style(styles, "Hyperlink")
    assert '<w:u w:val="single"/>' in word_style(styles, "Hyperlink")
    assert '<w:sz w:val="16"/>' in word_style(styles, "FootnoteText")
    assert '<w:vertAlign w:val="superscript"/>' in word_style(styles, "FootnoteReference")
    code = word_style(styles, "LoomCode")
    assert 'w:ascii="Courier New"' in code and 'w:fill="EEEEEE"' in code
    label = word_style(styles, "LoomFieldLabel")
    assert '<w:basedOn w:val="LoomFieldValue"/>' in label and "<w:i/>" in label
    assert '<w:name w:val="loom fields"/>' in word_style(styles, "LoomFields")
    # Les champs : le libellé large de label_width (3 cm), la valeur vide soulignée.
    assert '<w:gridCol w:w="1701"/>' in document
    assert document.count("<w:tcBorders><w:bottom ") == 1
    # La liste à trois niveaux : une définition à trois niveaux, à part ; l'autre liste garde
    # la sienne.
    assert '<w:abstractNum w:abstractNumId="3">' in numbering
    assert numbering.count('<w:lvl w:ilvl="2">') == 1
    assert '<w:num w:numId="1"><w:abstractNumId w:val="3"/>' in numbering
    assert '<w:num w:numId="2"><w:abstractNumId w:val="1"/>' in numbering


async def test_without_rich_text_no_new_style(tmp_path: Path) -> None:
    """Un document sans lien, note, champ ni code n'a aucun des styles qu'ils demandent."""
    path = await write(make(tmp_path, blocks=("fields", "code")), "simple", RICH_BLOCKS[3:4])
    styles = part(path, "word/styles.xml")
    for style_id in ("Hyperlink", "FootnoteText", "LoomCode", "LoomFields", "LoomFieldValue"):
        assert f'w:styleId="{style_id}"' not in styles, style_id
    assert '<w:abstractNum w:abstractNumId="3">' not in part(path, "word/numbering.xml")


async def test_fields_and_code_cost_only_when_declared(tmp_path: Path) -> None:
    plain = make(tmp_path)
    tool = make(tmp_path, blocks=("image", "fields", "code"))
    assert "Champs ('type': 'fields')" not in plain.spec.description
    assert "Champs ('type': 'fields')" in tool.spec.description
    assert "Code ('type': 'code')" in tool.spec.description

    def cost(mounted: WordTool) -> int:
        schema = json.dumps(mounted.spec.input_schema, ensure_ascii=False, separators=(",", ":"))
        return len(schema) + len(mounted.spec.description)

    # Les deux blocs : 794 caractères mesurés (§ 22 de l'analyse).
    assert 760 < cost(tool) - cost(plain) < 830
    read_with = make(tmp_path, blocks=("fields",))
    await write(tool, "fc", RICH_BLOCKS[4:])
    read = await call(read_with, action="read", path="sortie/fc.docx")
    assert read["notes"] == [
        "Lu : code (1) ; cet outil n'en écrit pas : les retirer avant de réécrire le document (ou blocks: [code] dans la config de l'outil)."
    ]


# --- Les calculs (lot 6) ----------------------------------------------------------------

CALCULATED_BLOCKS: Final[list[dict[str, Any]]] = [
    {
        "type": "table",
        "columns": [
            {"header": "Désignation"},
            {"header": "Qté", "type": "integer"},
            {"header": "PU HT", "type": "amount_eur"},
            {"header": "TVA", "type": "percent"},
            {"header": "Montant HT", "type": "amount_eur", "compute": "{Qté} * {PU HT}"},
        ],
        "rows": [
            {"group": "Lot 1", "rows": [["Dépose", 1, 180, 0.1]]},
            {"group": "Lot 2", "rows": [["Mitigeur", 2, 89.99, 0.2]]},
        ],
        "totals": {"Montant HT": "sum"},
    },
    {"type": "invoice_totals", "from_table": {"amount": "Montant HT", "rate": "TVA"}},
]


async def test_the_styles_of_calculated_tables(tmp_path: Path) -> None:
    charte = {
        "table_group": {"fill": "EAF1DD", "color": "C00000"},
        "table_subtotal": {"border_top": "1pt 1F3864"},
        "invoice_totals": {"position": "center"},
        "labels": {"tax": "TVA {rate}"},
    }
    tool = make(tmp_path, blocks=("invoice_totals",), style={"spec": charte})
    path = await write(tool, "facture", CALCULATED_BLOCKS)
    document = part(path, "word/document.xml")
    styles = part(path, "word/styles.xml")
    check_order(document)
    check_order(styles)
    # Le groupe : son fond sur la cellule, sa couleur dans son style, gardé avec la suite.
    assert document.count('w:fill="EAF1DD"') == 2
    group = word_style(styles, "LoomTableGroup")
    assert '<w:color w:val="C00000"/>' in group and "<w:keepNext/>" in group
    assert "<w:i/>" in word_style(styles, "LoomTableSubtotal")
    # Le sous-total : son filet en haut de chaque cellule.
    assert document.count('<w:top w:val="single" w:sz="8" w:space="0" w:color="1F3864"/>') == 10
    # Le récapitulatif : étroit, au centre ; le libellé de TVA sans sa base.
    invoice = document[document.index('<w:pStyle w:val="LoomInvoiceTotals"/>') - 2000 :]
    assert '<w:jc w:val="center"/><w:tblBorders>' in invoice
    assert ">TVA 10\u00a0%</w:t>" in document and "sur 180" not in document


def test_invoice_totals_cost_only_when_declared(tmp_path: Path) -> None:
    plain = make(tmp_path)
    tool = make(tmp_path, blocks=("image", "invoice_totals"))
    said = "Récapitulatif de TVA ('type': 'invoice_totals')"
    assert said not in plain.spec.description and said in tool.spec.description

    def cost(mounted: WordTool) -> int:
        schema = json.dumps(mounted.spec.input_schema, ensure_ascii=False, separators=(",", ":"))
        return len(schema) + len(mounted.spec.description)

    # 745 caractères mesurés (§ 23 de l'analyse).
    assert 710 < cost(tool) - cost(plain) < 780


async def test_the_style_of_an_attendance_sheet(tmp_path: Path) -> None:
    charte = {"attendance": {"row_height": 2, "size": 9}, "labels": {"participant": "Stagiaire"}}
    tool = make(tmp_path, blocks=("attendance",), style={"spec": charte})
    sheet = {"type": "attendance", "participants": ["Léa", "Paul"], "sessions": ["Matin"]}
    path = await write(tool, "emargement", [sheet])
    document = part(path, "word/document.xml")
    check_order(document)
    assert document.count('<w:trHeight w:val="1134" w:hRule="atLeast"/>') == 2
    assert ">Stagiaire</w:t>" in document
    assert '<w:sz w:val="18"/>' in word_style(part(path, "word/styles.xml"), "LoomAttendance")


def test_attendance_costs_only_when_declared(tmp_path: Path) -> None:
    plain = make(tmp_path, blocks=())
    tool = make(tmp_path, blocks=("attendance",))
    said = "Feuille d'émargement ('type': 'attendance')"
    assert said not in plain.spec.description and said in tool.spec.description

    def cost(mounted: WordTool) -> int:
        schema = json.dumps(mounted.spec.input_schema, ensure_ascii=False, separators=(",", ":"))
        return len(schema) + len(mounted.spec.description)

    # 530 caractères mesurés (§ 24 de l'analyse).
    assert 500 < cost(tool) - cost(plain) < 560
