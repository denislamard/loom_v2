# SPDX-License-Identifier: Apache-2.0
"""Styles de document de ``loom.word`` : la charte de l'outil, la disposition d'un modèle.

Les fichiers de style sont lus au chargement de la config (``load_config``), ou donnés
en Python (``spec``). Les documents écrits sont relus dans leur XML, et par l'outil
(``action: read``) : ce que le style pose ne revient pas, le plan du modèle oui.
LibreOffice, s'il est là, vérifie le rendu.
"""

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
from loom_ia.tools_builtin._style_params import (
    HOUSE,
    StyleSpec,
    check_charte,
    merged,
    resolve,
)
from loom_ia.tools_builtin._templates import parse as parse_template
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

FACTURE_MD: Final = """---
description: Facture d'un artisan.
---
# Émetteur
# Client
# Description
# Informations de paiement
# Conditions et termes
"""
CHARTE: Final[dict[str, Any]] = {
    "fonts": {"body": {"name": "Arial", "size": 10}},
    "colors": {"primary": "0B5D3B", "accent": "c0392b"},
    "title": {"size": 22, "align": "droite", "uppercase": True, "rule": False},
    "headings": {1: {"size": 13, "uppercase": True}},
    "tables": {"borders": "filets", "header": {"fill": "E8F1EC"}},
    "header": {"logo": {"data": LOGO, "width": 3.5}, "right": "{titre}"},
    "footer": {"lines": ["Dupont Plomberie SARL · SIRET 812 345 678 00021"],
               "right": "Page {page} / {pages}"},
    "watermark": "BROUILLON",
}  # fmt: skip
FACTURE_STYLE: Final[dict[str, Any]] = {
    "beside": [{"sections": ["Émetteur", "Client"], "widths": [55, 45]}],
    "sections": {
        "Émetteur": {"title": "masque"},
        "Client": {"title": "masque", "box": True},
        "Description": {"title": "masque", "tables": {"width": "contenu", "align": "droite"}},
        "informations de paiement": {"title": "discret"},
        "Conditions et termes": {"title": "discret", "size": "petit", "align": "justifie"},
    },
}
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
RAPPORT_MD: Final = "# Page de garde\n# Contexte\n# Mesures (facultatif)\n# Conditions générales\n"
RAPPORT_STYLE: Final[dict[str, Any]] = {
    "headings": {"numbering": "decimale"},
    "captions": {"numbered": True},
    "images": {"grid": True},
    "sections": {
        "Page de garde": {"title": "masque", "cover": True},
        "Mesures": {"landscape": True},
        "Conditions générales": {"columns": 2, "size": "petit"},
    },
}
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
        templates={
            "facture": {"text": FACTURE_MD, "style": {"spec": FACTURE_STYLE}},
            "rapport": {"text": RAPPORT_MD, "style": {"spec": RAPPORT_STYLE}},
        },
    )


async def call(tool: WordTool, **arguments: Any) -> dict[str, Any]:
    output = await tool.invoke(dict(arguments), CONTEXT)
    assert isinstance(output.data, dict)
    return output.data


async def write(tool: WordTool, name: str, blocks: list[dict[str, Any]], **more: Any) -> Path:
    await call(tool, path=f"sortie/{name}.docx", blocks=blocks, overwrite=True, **more)
    return OUTPUTS[id(tool)] / f"{name}.docx"


def part(path: Path, name: str) -> str:
    with zipfile.ZipFile(path) as archive:
        return archive.read(name).decode()


def names(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as archive:
        return archive.namelist()


# L'ordre des enfants que le schéma WordprocessingML impose, pour ce qu'écrit l'outil :
# Word refuse un document qui ne le suit pas, LibreOffice non.
ORDER: Final = {
    "sectPr": ("headerReference", "footerReference", "type", "pgSz", "pgMar", "cols", "titlePg"),
    "pPr": ("pStyle", "keepNext", "keepLines", "numPr", "pBdr", "shd", "tabs", "spacing", "ind",
            "jc", "outlineLvl", "sectPr"),
    "tblPr": ("tblStyle", "tblW", "jc", "tblBorders", "tblLayout", "tblCellMar", "tblLook"),
    "tcPr": ("tcW", "tcBorders", "shd", "tcMar", "vAlign"),
    "rPr": ("rFonts", "b", "bCs", "i", "iCs", "caps", "vanish", "color", "sz", "szCs", "lang"),
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


# --- Sans style, rien ne change ---------------------------------------------------------

# L'empreinte du devis de ``test_word`` écrit sans style : celle de la 2.0.21 pour le même
# contenu écrit dans le vocabulaire français (le devis n'a plus de saut de page, sorti du
# vocabulaire en 2.1.0). Avant le saut de page retiré, celle du commit 9ae0492 (avant les styles).
DEVIS_SHA256: Final = "149987cb614b5a4aded8996c5524116ba1a35a14863c513bb81cb04521f4f455"


async def test_without_a_style_nothing_changes(tmp_path: Path) -> None:
    from test_word import DEVIS

    plain = make(tmp_path)
    assert (await call(plain, path="sortie/a.docx", **DEVIS))["sha256"] == DEVIS_SHA256
    # Un style vide : le style maison, le même fichier.
    empty = make(tmp_path, style={"spec": {}})
    assert (await call(empty, path="sortie/b.docx", **DEVIS))["sha256"] == DEVIS_SHA256
    assert resolve(StyleSpec()) == HOUSE
    assert styles_xml(HOUSE) == STYLES
    # Ni le schéma ni la description ne disent rien des styles : le modèle n'en voit rien.
    assert empty.spec == plain.spec
    assert ("style", "charte") in empty.settings().options
    assert not any(label == "style" for label, _ in plain.settings().options)


# --- La charte ----------------------------------------------------------------------------


async def test_the_charte_sets_fonts_colors_and_headings(tmp_path: Path) -> None:
    tool = styled(tmp_path)
    path = await write(tool, "libre", FACTURE, title="Facture", template="none")
    styles = part(path, "word/styles.xml")
    assert 'w:ascii="Arial"' in styles and '<w:sz w:val="20"/>' in styles
    title = re.search(r'<w:style w:type="paragraph" w:styleId="Title">.*?</w:style>', styles)
    assert title is not None
    assert '<w:jc w:val="right"/>' in title.group() and "<w:caps/>" in title.group()
    assert "pBdr" not in title.group() and 'w:val="0B5D3B"' in title.group()
    heading = re.search(r'w:styleId="Heading1">.*?</w:style>', styles)
    assert heading is not None and "<w:caps/>" in heading.group()
    assert '<w:sz w:val="26"/>' in heading.group()
    # Une couleur nommée (accent) prend la valeur de la charte, en majuscules.
    assert 'w:styleId="Heading3"' in styles and 'w:color w:val="C0392B"' in styles
    # Filets : haut et bas forts, lignes intérieures fines, pas de verticales.
    document = part(path, "word/document.xml")
    assert '<w:top w:val="single" w:sz="8" w:space="0" w:color="0B5D3B"/>' in document
    assert "insideV" not in document
    assert 'w:fill="E8F1EC"' in document
    for name in ("word/document.xml", "word/styles.xml", "word/header1.xml"):
        check_order(part(path, name))


async def test_an_underlined_title(tmp_path: Path) -> None:
    """Le texte du titre souligné, de sa longueur ; sans filet de toute la largeur."""
    charte = {"title": {"underline": True, "rule": False}}
    tool = styled(tmp_path, charte=charte)
    path = await write(tool, "libre", FACTURE, title="Facture", template="none")
    styles = part(path, "word/styles.xml")
    title = re.search(r'<w:style w:type="paragraph" w:styleId="Title">.*?</w:style>', styles)
    assert title is not None
    assert '<w:u w:val="single"/>' in title.group() and "pBdr" not in title.group()
    check_order(styles)
    # Sans la clé, rien de souligné.
    assert "<w:u " not in styles_xml(resolve(StyleSpec.model_validate({"title": {"rule": False}})))


async def test_underlined_headings(tmp_path: Path) -> None:
    """Un niveau souligné, les autres non ; un titre discret hérite du niveau 1."""
    charte = {"headings": {1: {"underline": True}}}
    tool = make(
        tmp_path,
        style={"spec": charte},
        templates={"facture": {"text": FACTURE_MD, "style": {"spec": FACTURE_STYLE}}},
    )
    path = await write(tool, "facture", FACTURE, title="Facture", template="facture")
    styles = part(path, "word/styles.xml")
    check_order(styles)
    for level, underlined in ((1, True), (2, False), (3, False)):
        heading = re.search(rf'w:styleId="Heading{level}">.*?</w:style>', styles)
        assert heading is not None
        assert ('<w:u w:val="single"/>' in heading.group()) is underlined
    quiet = re.search(r'w:styleId="HeadingQuiet">.*?</w:style>', styles)
    assert quiet is not None and '<w:basedOn w:val="Heading1"/>' in quiet.group()
    assert "<w:u " not in quiet.group()  # hérité, pas annulé


async def test_header_footer_logo_and_watermark(tmp_path: Path) -> None:
    tool = styled(tmp_path)
    path = await write(tool, "libre", FACTURE, title="Facture F-12", template="none")
    header = part(path, "word/header1.xml")
    assert "Facture F-12" in header  # {titre}
    assert 'string="BROUILLON"' in header and "PowerPlusWaterMarkObject" in header
    assert 'r:embed="rIdImage1"' in header
    assert "media/image1.png" in part(path, "word/_rels/header1.xml.rels")
    # Le logo est une partie de l'archive, mais pas une relation du corps.
    assert "word/media/image1.png" in names(path)
    assert "rIdImage1" not in part(path, "word/_rels/document.xml.rels")
    assert 'Target="header1.xml"' in part(path, "word/_rels/document.xml.rels")
    assert "/word/header1.xml" in part(path, "[Content_Types].xml")
    footer = part(path, "word/footer1.xml")
    # Les mentions, puis la ligne des zones (le numéro de page à droite).
    assert footer.index("SIRET") < footer.index(" PAGE ") < footer.index(" NUMPAGES ")
    assert '<w:tab w:val="right" w:pos="9638"/>' in footer
    document = part(path, "word/document.xml")
    assert '<w:headerReference w:type="default" r:id="rIdHeader1"/>' in document
    assert "titlePg" not in document


async def test_a_first_page_without_header(tmp_path: Path) -> None:
    charte = {**CHARTE, "header": {"right": "{titre}", "first_page": False}, "watermark": None}
    tool = styled(tmp_path, charte)
    path = await write(tool, "libre", FACTURE, title="Facture", template="none")
    document = part(path, "word/document.xml")
    assert "<w:titlePg/>" in document
    assert '<w:headerReference w:type="first" r:id="rIdHeader2"/>' in document
    # Le pied de page garde sa première page : first_page n'est faux que pour l'en-tête.
    assert '<w:footerReference w:type="first" r:id="rId4"/>' in document
    assert "Facture" not in part(path, "word/header2.xml")


async def test_numbered_headings_and_captions(tmp_path: Path) -> None:
    path = await write(styled(tmp_path), "rapport", RAPPORT, title="Rapport", template="rapport")
    numbering = part(path, "word/numbering.xml")
    check_order(numbering)
    assert (
        '<w:pStyle w:val="Heading1"/><w:suff w:val="space"/><w:lvlText w:val="%1."/>' in numbering
    )
    assert '<w:num w:numId="900"><w:abstractNumId w:val="2"/></w:num>' in numbering
    styles = part(path, "word/styles.xml")
    # Le titre masqué ne prend pas de numéro.
    hidden = re.search(r'w:styleId="HeadingHidden">.*?</w:style>', styles)
    assert hidden is not None and '<w:numId w:val="0"/>' in hidden.group()
    document = part(path, "word/document.xml")
    assert re.search(
        r'Figure </w:t></w:r><w:fldSimple w:instr=" SEQ Figure \\\* ARABIC ">', document
    )
    assert ">2</w:t></w:r></w:fldSimple>" in document
    assert 'w:instr=" SEQ Tableau \\* ARABIC "' in document
    read = await call(styled(tmp_path), action="read", path="sortie/rapport.docx")
    blocks = read["blocks"]
    # Relu sans numéros : ni « 1. » devant les titres, ni « Figure 1 — » dans les légendes.
    assert {"type": "heading", "level": 1, "text": "Contexte"} in blocks
    captions = [b.get("caption") for b in blocks if b["type"] == "image"]
    assert captions == ["Avant", "Après"]
    assert {"type": "paragraph", "text": "Pression mesurée"} in blocks
    # Le titre masqué revient en titre, pas en liste (B-4).
    assert {"type": "heading", "level": 1, "text": "Page de garde"} in blocks


async def test_an_underlined_section_title_keeps_its_layout(tmp_path: Path) -> None:
    """« <u>Client</u> » est la section Client : le plan l'accepte, la disposition la pose."""
    blocks = [
        dict(block, text=f"<u>{block['text']}</u>") if block["type"] == "heading" else block
        for block in FACTURE
    ]
    path = await write(styled(tmp_path), "facture", blocks, title="Facture", template="facture")
    document = part(path, "word/document.xml")
    assert document.count('<w:pStyle w:val="HeadingHidden"/>') == 3
    assert '<w:tblStyle w:val="LoomLayout"/>' in document


async def test_article_numbering(tmp_path: Path) -> None:
    tool = make(tmp_path, style={"spec": {"headings": {"numbering": "article"}}})
    path = await write(tool, "contrat", [{"type": "heading", "text": "Objet"}], title="Contrat")
    assert '<w:lvlText w:val="Article %1 \u2013"/>' in part(path, "word/numbering.xml")


# --- La disposition ------------------------------------------------------------------------


async def test_a_facture_laid_out(tmp_path: Path) -> None:
    tool = styled(tmp_path)
    path = await write(tool, "facture", FACTURE, title="Facture", template="facture")
    document = part(path, "word/document.xml")
    check_order(document)
    styles = part(path, "word/styles.xml")
    assert '<w:name w:val="loom layout"/>' in styles
    hidden = re.search(r'w:styleId="HeadingHidden">.*?</w:style>', styles)
    assert hidden is not None and "<w:vanish/>" in hidden.group()
    assert "numId" not in hidden.group()  # pas de numérotation à annuler
    assert document.count('<w:pStyle w:val="HeadingHidden"/>') == 3
    assert document.count('<w:pStyle w:val="HeadingQuiet"/>') == 2
    # Émetteur et client côte à côte, 55/45, le client encadré.
    layout = re.search(r'<w:tblStyle w:val="LoomLayout"/>.*?</w:tbl>', document)
    assert layout is not None
    assert '<w:gridCol w:w="5301"/><w:gridCol w:w="4337"/>' in layout.group()
    assert layout.group().count("<w:tcBorders>") == 1
    assert layout.group().index("Émetteur") < layout.group().index("Client")
    # Les prestations en pleine largeur ; le récapitulatif, étroit, à droite.
    assert document.count('<w:tblW w:w="5000" w:type="pct"/>') == 1
    assert re.search(r'<w:tblW w:w="\d+" w:type="dxa"/><w:jc w:val="right"/>', document)
    # Petit texte justifié dans les conditions.
    assert '<w:jc w:val="both"/></w:pPr><w:r><w:rPr><w:sz w:val="16"/>' in document
    # Relu : le plan du modèle, dans l'ordre, et le texte des cellules.
    read = await call(tool, action="read", path="sortie/facture.docx")
    titles = [b["text"] for b in read["blocks"] if b["type"] == "heading"]
    assert titles == ["Facture", *(s.title for s in parse_template("facture", FACTURE_MD).sections)]
    assert {"type": "paragraph", "text": "SCI Les Tilleuls\n4 avenue Foch, 69006 Lyon"} in read[
        "blocks"
    ]
    # Réécrit tel que relu (sans le titre du document) : le plan passe la vérification.
    again = [
        block if block["type"] != "table" else FACTURE[index]
        for index, block in enumerate(read["blocks"][1:])
    ]
    assert len(again) == len(FACTURE)
    await call(tool, path="sortie/f2.docx", blocks=again, template="facture", title="Facture")


async def test_a_report_laid_out(tmp_path: Path) -> None:
    tool = styled(tmp_path, None)
    path = await write(tool, "rapport", RAPPORT, title="Rapport", template="rapport")
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


async def test_columns_then_landscape_are_balanced(tmp_path: Path) -> None:
    layout = {"sections": {"A": {"columns": 2}, "B": {"landscape": True}}}
    tool = make(tmp_path, templates={"t": {"text": "# A\n# B\n", "style": {"spec": layout}}})
    blocks = [{"type": "heading", "text": "A"}, {"type": "heading", "text": "B"}]
    path = await write(tool, "d", blocks, template="t")
    sections = re.findall(r"<w:sectPr>.*?</w:sectPr>", part(path, "word/document.xml"))
    # Colonnes, fermeture continue d'une colonne, paysage sur nouvelle page.
    assert "cols" in sections[0]
    assert '<w:type w:val="continuous"/>' in sections[1] and "cols" not in sections[1]
    assert "landscape" in sections[2] and "<w:type" not in sections[2]


async def test_a_boxed_section_and_a_page_break(tmp_path: Path) -> None:
    layout = {"sections": {"B": {"box": True, "page_break": True, "space_before": 1}}}
    tool = make(tmp_path, templates={"t": {"text": "# A\n# B\n", "style": {"spec": layout}}})
    blocks = [{"type": "heading", "text": "A"}, {"type": "heading", "text": "B"}]
    path = await write(tool, "d", blocks, template="t")
    document = part(path, "word/document.xml")
    assert document.index('<w:br w:type="page"/>') < document.index('w:line="567"')
    assert document.index('w:line="567"') < document.index("<w:tcBorders>")
    read = await call(tool, action="read", path="sortie/d.docx")
    assert [b["text"] for b in read["blocks"]] == ["A", "B"]


async def test_an_absent_optional_section_leaves_the_other_alone(tmp_path: Path) -> None:
    md = "# A\n# B (facultatif)\n# C\n"
    layout = {"beside": [{"sections": ["A", "B"]}]}
    tool = make(tmp_path, templates={"t": {"text": md, "style": {"spec": layout}}})
    blocks = [{"type": "heading", "text": "A"}, {"type": "heading", "text": "C"}]
    path = await write(tool, "d", blocks, template="t")
    assert "LoomLayout" not in part(path, "word/document.xml")


async def test_a_styled_document_is_reproducible(tmp_path: Path) -> None:
    tool = styled(tmp_path)
    first = await call(tool, path="sortie/a.docx", blocks=FACTURE, template="facture")
    second = await call(tool, path="sortie/b.docx", blocks=FACTURE, template="facture")
    assert first["sha256"] == second["sha256"]


# --- Fusion et contrôles ---------------------------------------------------------------------


def test_the_layout_completes_the_charte_key_by_key() -> None:
    charte = StyleSpec.model_validate(CHARTE)
    layout = StyleSpec.model_validate(
        {"tables": {"borders": "grille"}, "footer": {"lines": []}, "watermark": ""}
    )
    style = resolve(merged(charte, layout))
    assert style.tables.borders == "grille"
    assert style.tables.header_fill == "E8F1EC"  # gardé de la charte
    assert style.font == "Arial"
    assert style.footer is not None and style.footer.lines == ()
    assert style.footer.right == "Page {page} / {pages}"
    assert style.watermark is None
    assert style.levels[0].uppercase and style.levels[0].size == 13
    assert style.levels[2].color == "C0392B"


def test_a_style_file_is_checked() -> None:
    cases: tuple[tuple[dict[str, Any], str], ...] = (
        ({"colors": {"primary": "bleu"}}, "six chiffres hexadécimaux"),
        ({"footer": {"center": "{date}"}}, r"champ inconnu \{date\}"),
        ({"fonts": {"body": {"name": "Arial<"}}}, "un nom de police"),
        ({"beside": [{"sections": ["A", "B"], "widths": [60, 30]}]}, "font 100 en tout"),
        ({"tables": {"borders": "pointillés"}}, "grille"),
        ({"page": {"margins": {"top": 9}}}, "less than or equal to 5"),
        ({"en_tete": {}}, "Extra inputs"),
    )
    for given, said in cases:
        with pytest.raises(ValidationError, match=said):
            StyleSpec.model_validate(given)
    with pytest.raises(ValueError, match="pas dans la charte"):
        check_charte(StyleSpec.model_validate({"sections": {"A": {}}}), "loom.word.style")


PAIR: Final[dict[str, Any]] = {"beside": [{"sections": ["Émetteur", "Client"]}]}


@pytest.mark.parametrize(
    ("layout", "said"),
    [
        ({"sections": {"Annexes": {}}}, r"hors du modèle facture : « Annexes » \(plan : Émetteur"),
        ({"beside": [{"sections": ["Émetteur", "Description"]}]}, "doivent se suivre"),
        ({**PAIR, "sections": {"Client": {"landscape": True}}}, "ni columns, ni landscape"),
        ({**PAIR, "sections": {"Client": {"page_break": True}}}, "vont à la première"),
        ({"sections": {"Client": {"cover": True}}}, "première section du modèle, « Émetteur »"),
        ({"sections": {"Émetteur": {"cover": True, "box": True}}}, "ni columns, ni box"),
    ],
)  # fmt: skip
def test_a_layout_follows_its_template(tmp_path: Path, layout: dict[str, Any], said: str) -> None:
    with pytest.raises(ValueError, match=said):
        make(tmp_path, templates={"facture": {"text": FACTURE_MD, "style": {"spec": layout}}})


def test_a_logo_must_be_an_image(tmp_path: Path) -> None:
    charte = {"header": {"logo": {"data": b"GIF89a....", "width": 3}}}
    with pytest.raises(ValueError, match="logo de header : image GIF"):
        make(tmp_path, style={"spec": charte})
    with pytest.raises(ValueError, match=r"non lu — les styles se lisent au chargement"):
        make(tmp_path, style="charte.yaml")


def test_an_uncommon_font_is_said(tmp_path: Path) -> None:
    tool = make(tmp_path, style={"spec": {"fonts": {"body": {"name": "Inter"}}}})
    options = dict(tool.settings().options)
    assert options["polices"].startswith("Inter : hors des polices de tous les postes")


def test_pdf_has_no_style_yet() -> None:
    folders = [{"name": "s", "path": "s", "rights": ["write"]}]
    for given in (
        {"folders": folders, "style": "charte.yaml"},
        {"folders": folders, "templates": {"r": {"file": "r.md", "style": "r.yaml"}}},
    ):
        with pytest.raises(ValidationError, match=r"ne servent qu'à loom\.word pour l'instant"):
            PdfParams.model_validate(given)


# --- Le chargement de la config --------------------------------------------------------------


def word_agent(**reference: Any) -> dict[str, Any]:
    return demo_agent(tools=[{"python": "loom.word", **reference}])


def test_styles_are_read_when_the_config_loads(
    demo: ConfigFactory, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "sortie").mkdir()
    folders = [{"name": "sortie", "path": "sortie", "rights": ["read", "write"]}]
    reference = {
        "folders": folders,
        "style": "styles/charte.yaml",
        "templates": {"facture": {"file": "facture.md", "style": "styles/facture.yaml"}},
    }
    path = demo(agents=[word_agent(**reference)], storage=JSONL)
    prompts = tmp_path / "prompts"
    (prompts / "facture.md").write_text(FACTURE_MD, encoding="utf-8")
    with pytest.raises(ConfigError, match=r"tools\[loom\.word\]\.style — style introuvable"):
        load_config(path)
    (prompts / "styles").mkdir()
    (prompts / "styles" / "charte.yaml").write_text(
        "fonts: {body: {name: Arial}}\ncolors: {primary: 0B5D3B}\n"
        "header:\n  logo: {file: logo.png, width: 3}\n  right: '{titre}'\n",
        encoding="utf-8",
    )
    (prompts / "styles" / "facture.yaml").write_text(
        "beside:\n- sections: [Émetteur, Client]\nsections:\n  Émetteur: {title: masque}\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match=r"style\.header\.logo — logo introuvable"):
        load_config(path)
    (prompts / "logo.png").write_bytes(LOGO)
    config = load_config(path)
    assert config is not None
    assert main(["--config", str(path), "validate"]) == 0
    assert "style : charte ; disposition : facture" in capsys.readouterr().out
    for written, said in (
        (
            "sections:\n  Annexes: {}\n",
            r"templates\[facture\]\.style : section\(s\) hors du modèle",
        ),
        ("tables: {borders: pointillés}\n", r"templates\[facture\]\.style\.tables\.borders"),
        ("tables: {}\ntables: {}\n", "YAML invalide"),
    ):
        (prompts / "styles" / "facture.yaml").write_text(written, encoding="utf-8")
        with pytest.raises(ConfigError, match=said):
            load_config(path)
    (prompts / "styles" / "facture.yaml").write_text("sections: {}\n", encoding="utf-8")
    (prompts / "styles" / "charte.yaml").write_text("sections: {A: {}}\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="pas dans la charte"):
        load_config(path)


async def test_a_style_written_in_yaml_writes_the_facture(
    demo: ConfigFactory, tmp_path: Path
) -> None:
    """Le chemin réel : les fichiers lus par load_config, l'outil monté par sa fabrique."""
    (tmp_path / "sortie").mkdir()
    prompts = tmp_path / "prompts"
    prompts.mkdir(parents=True, exist_ok=True)
    (prompts / "facture.md").write_text(FACTURE_MD, encoding="utf-8")
    (prompts / "facture.yaml").write_text(
        "headings: {1: {size: 12}}\nbeside:\n- {sections: [Émetteur, Client], widths: [50, 50]}\n",
        encoding="utf-8",
    )
    folders = [FOLDER]
    reference = {"templates": {"facture": {"file": "facture.md", "style": "facture.yaml"}}}
    config = load_config(demo(agents=[word_agent(folders=folders, **reference)], storage=JSONL))
    declared = config.agents[0].tools[0]
    assert isinstance(declared, PythonTool)
    params = WordParams.model_validate(builtin_settings(declared, WordParams))
    tool = build(params, BuiltinContext(base_dir=tmp_path))
    OUTPUTS[id(tool)] = tmp_path / "sortie"
    path = await write(tool, "f", FACTURE, template="facture")
    assert '<w:gridCol w:w="4819"/><w:gridCol w:w="4819"/>' in part(path, "word/document.xml")
    assert '<w:sz w:val="24"/>' in part(path, "word/styles.xml")


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
        '<w:p><w:pPr><w:pStyle w:val="Num"/></w:pPr><w:r><w:t>Un</w:t></w:r></w:p>'
        '<w:p><w:pPr><w:pStyle w:val="Plain"/></w:pPr><w:r><w:t>Deux</w:t></w:r></w:p>'
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
        archive.writestr(
            "word/document.xml", f"<w:document {ns}><w:body>{body}</w:body></w:document>"
        )
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
    path = await write(styled(tmp_path), "facture", FACTURE, title="Facture", template="facture")
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
    path = await write(tool, "rapport", RAPPORT, title="Rapport", template="rapport")
    pages = pypdf.PdfReader(convert(soffice, path, tmp_path / "pdf", "pdf")).pages
    # Page de garde, texte, paysage, conditions générales en colonnes.
    assert len(pages) == 4
    landscape = [float(page.mediabox.width) > float(page.mediabox.height) for page in pages]
    assert landscape == [False, False, True, False]
    assert "Page de garde" not in pages[0].extract_text()
    assert "1. Contexte" in pages[1].extract_text()
    assert "Figure 2 — Après" in pages[1].extract_text()
    assert "Tableau 1 — Pression mesurée" in pages[2].extract_text()


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
    again = await call(
        tool, path="sortie/encore.docx", title="PV de livraison", blocks=read["blocks"][1:]
    )
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
    layout = {"sections": {"Page de garde": {"title": "masque", "cover": True}}}
    md = "# Page de garde\n# Contexte\n# Mesures\n# Conditions générales\n"
    tool = extra(
        tmp_path,
        style={"spec": {"headings": {"numbering": "decimale"}}},
        templates={"rapport": {"text": md, "style": {"spec": layout}}},
    )
    path = await write(tool, "rapport", SOMMAIRE, title="Rapport", template="rapport")
    document = part(path, "word/document.xml")
    check_order(document)
    assert '<w:fldChar w:fldCharType="begin" w:dirty="true"/>' in document
    assert ' TOC \\o "1-2" \\h \\z \\u </w:instrText>' in document
    labels = re.findall(r'<w:hyperlink w:anchor="(_Toc_loom_\d)" w:history="1"><w:r><w:t '
                        r'xml:space="preserve">([^<]*)</w:t>', document)  # fmt: skip
    # Le titre masqué n'y est pas ; un titre de niveau 3, non plus (levels : 2).
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
    assert {"type": "toc"} in read["blocks"]
    assert [b["type"] for b in read["blocks"]].count("toc") == 1
    assert not any(b.get("text", "").startswith("1. ") for b in read["blocks"])


async def test_the_charte_sets_the_contents(tmp_path: Path) -> None:
    tool = extra(tmp_path, style={"spec": {"toc": {"levels": 1, "pages": False}}})
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


def test_pdf_has_no_signature_toc_or_checkboxes_yet() -> None:
    folders = [{"name": "s", "path": "s", "rights": ["write"]}]
    for name in ("signature", "toc", "checkboxes"):
        with pytest.raises(ValidationError, match=f"blocks : {name} ne sert qu'à loom.word"):
            PdfParams.model_validate({"folders": folders, "blocks": ["chart", name]})
    assert PdfParams.model_validate({"folders": folders, "blocks": ["chart", "image"]}).blocks


async def test_libreoffice_prints_the_extra_blocks(tmp_path: Path, soffice: str) -> None:
    pypdf = pytest.importorskip("pypdf")
    tool = extra(tmp_path, style={"spec": {"headings": {"numbering": "decimale"}}})
    path = await write(tool, "d", [*SOMMAIRE[1:], *LIVRAISON], title="Rapport")
    text = "".join(
        page.extract_text()
        for page in pypdf.PdfReader(convert(soffice, path, tmp_path / "pdf", "pdf")).pages
    )
    assert "Sommaire" in text and "2. Mesures" in text
    assert "Coupure d'eau faite" in text and "Canalisation" in text
    assert text.count("Bon pour accord") == 2 and "Signature :" in text
