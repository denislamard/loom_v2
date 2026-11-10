# SPDX-License-Identifier: Apache-2.0
"""Outil fourni ``loom.word`` : écrire un .docx dans le style maison, lire un .docx.

L'outil est construit par sa fabrique sur des dossiers temporaires ; ses appels
passent par ``invoke`` et ``assess_call``, les deux chemins du moteur. Les
documents écrits sont relus dans leur XML ; LibreOffice, s'il est là, vérifie
qu'ils s'ouvrent. Les documents lus sont fabriqués à la main (cas que l'outil
n'écrit pas : modifications suivies, contrôles de contenu, bombes) ou par
LibreOffice (un document « d'ailleurs »).
"""

import io
import json
import re
import subprocess
import zipfile
from pathlib import Path
from typing import Any, Final, cast
from xml.etree import ElementTree

import pytest
from conftest import MODEL, ConfigFactory, demo_agent
from image_files import jpeg, png

from loom_ia.access.cli import main
from loom_ia.adapters.stores import InMemoryEventStore
from loom_ia.config import ConfigError, load_config
from loom_ia.core.bounded_regex import bounded_validator
from loom_ia.core.events import ToolCompleted
from loom_ia.core.model import DEFAULT_TENANT, RunId, RunStatus, SessionId
from loom_ia.core.ports import ToolContext, ToolError
from loom_ia.engine import begin_run, drive
from loom_ia.runtime import build_agent
from loom_ia.tools_builtin import BuiltinContext
from loom_ia.tools_builtin._document.arguments import MAX_IMAGE_BYTES
from loom_ia.tools_builtin.word import WordTool, build
from loom_ia.tools_builtin.word import reader as reader_module
from loom_ia.tools_builtin.word.writer import FIXED_DATE
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
NS: Final = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def make(tmp_path: Path, *folders: dict[str, Any], **params: Any) -> WordTool:
    model = WordParams.model_validate({"folders": list(folders), **params})
    return build(model, BuiltinContext(base_dir=tmp_path))


@pytest.fixture
def disk(tmp_path: Path) -> Path:
    for name in ("modeles", "sortie", "valide"):
        (tmp_path / name).mkdir()
    return tmp_path


@pytest.fixture
def tool(disk: Path) -> WordTool:
    return make(
        disk,
        {"name": "modeles", "path": "modeles", "rights": ["read"]},
        {"name": "sortie", "path": "sortie", "rights": ["read", "write", "overwrite"]},
        {"name": "valide", "path": "valide", "rights": ["write"], "approval": {"write": "always"}},
    )


async def call(tool: WordTool, **arguments: Any) -> dict[str, Any]:
    output = await tool.invoke(dict(arguments), CONTEXT)
    assert isinstance(output.data, dict)
    return output.data


async def refused(tool: WordTool, match: str, **arguments: Any) -> None:
    with pytest.raises(ToolError, match=match):
        await tool.invoke(dict(arguments), CONTEXT)


DEVIS: Final[dict[str, Any]] = {
    "title": "Devis D-2026-042",
    "blocks": [
        {"type": "paragraph", "text": "Madame **Dupont**,\nvoici notre *proposition*."},
        {"type": "heading", "text": "Travaux"},
        {"type": "list", "items": ["Dépose", {"text": "Pose", "items": ["receveur", "paroi"]}]},
        {"type": "list", "items": ["Signature", "Acompte"], "ordered": True},
        {
            "type": "table",
            "columns": [
                {"header": "Désignation"},
                {"header": "Montant HT", "type": "amount_eur"},
            ],
            "rows": [["Dépose", 180], ["Pose", 1250.5]],
            "totals": {"Montant HT": "sum"},
            "caption": "Montants hors taxes.",
        },
        {"type": "note", "title": "Validité", "text": "3 mois."},
        {"type": "quote", "text": "Travail soigné.", "source": "M. Martin"},
        {"type": "heading", "text": "Conditions", "level": 2},
    ],
}


def convert(soffice: str, source: Path, out: Path, target: str) -> None:
    """Une conversion par LibreOffice, avec un profil neuf : elle doit réussir."""
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


def part(path: Path, name: str) -> ElementTree.Element:
    with zipfile.ZipFile(path) as archive:
        return ElementTree.fromstring(archive.read(name))


def _val(element: ElementTree.Element, child: str) -> str | None:
    found = element.find(f"{W}{child}")
    return None if found is None else found.get(f"{W}val")


def styles_of(body: ElementTree.Element) -> list[str]:
    found: list[str] = []
    for paragraph in body.iter(f"{W}p"):
        style = paragraph.find(f"{W}pPr/{W}pStyle")
        found.append(style.get(f"{W}val", "") if style is not None else "")
    return found


# --- Ce que voit le modèle ------------------------------------------------------------


def test_the_spec_names_the_folders_and_the_vocabulary(tool: WordTool) -> None:
    spec = tool.spec
    assert spec.name == "word"
    for said in (
        "- sortie : read, write, overwrite (attend une approbation humaine : overwrite)",
        "- valide : write (attend une approbation humaine : write)",
        "heading (text, level 1 à 3)",
        "amount_eur",
        "**gras**",
        "<u>souligné</u>",
        "ni autre balise",
        "next_start",
    ):
        assert said in spec.description, said
    assert (spec.side_effects, spec.approval) == ("irreversible", "always")
    assert "title" not in spec.input_schema
    # Allégé : ni docstrings de modèles, ni « default: null » (le schéma part à chaque appel).
    said = json.dumps(spec.input_schema, ensure_ascii=False)
    assert '"default": null' not in said and "Un élément de liste" not in said
    properties = spec.input_schema["properties"]
    assert isinstance(properties, dict) and "start" in properties


def test_a_read_only_tool_says_so(disk: Path) -> None:
    only = make(disk, {"name": "modeles", "path": "modeles", "rights": ["read"]})
    assert only.spec.description.startswith("Lit un document Word existant")
    assert "Types de colonne" not in only.spec.description
    assert only.spec.side_effects == "none"


def test_settings_for_validate(tool: WordTool) -> None:
    settings = tool.settings()
    assert [place.name for place in settings.places] == ["modeles", "sortie", "valide"]
    assert settings.options == (
        ("fuseau", "Europe/Paris"),
        ("blocs", "aucun"),
        ("modèles", "aucun"),
        ("lecture des documents", "oui"),
    )


# --- Écriture -------------------------------------------------------------------------


async def test_writes_a_document_in_the_house_style(tool: WordTool, disk: Path) -> None:
    result = await call(tool, path="sortie/devis.docx", **DEVIS)
    assert result["path"] == "sortie/devis.docx" and result["written"] is True
    assert result["blocks"] == len(DEVIS["blocks"]) and result["warnings"] == []
    path = disk / "sortie" / "devis.docx"
    body = part(path, "word/document.xml")
    assert styles_of(body) == [
        "Title", "Normal", "Heading1",
        "ListParagraph", "ListParagraph", "ListParagraph", "ListParagraph",
        "ListParagraph", "ListParagraph",
        *["TableText"] * 8,
        "Caption", "Note", "Quote", "QuoteSource", "Heading2",
    ]  # fmt: skip
    texts = "".join(t.text or "" for t in body.iter(f"{W}t"))
    assert "1\u00a0430,50\u00a0€" in texts and "— M. Martin" in texts
    # L'emphase et le saut de ligne.
    runs = body.findall(f".//{W}r")
    assert any(
        r.find(f"{W}rPr/{W}b") is not None
        and "Dupont" in "".join(t.text or "" for t in r.iter(f"{W}t"))
        for r in runs
    )
    assert body.find(f".//{W}br") is not None
    # L'en-tête du tableau se répète d'une page à l'autre ; le total est en gras.
    rows = body.findall(f".//{W}tr")
    assert rows[0].find(f"{W}trPr/{W}tblHeader") is not None
    assert rows[-1].find(f".//{W}b") is not None
    # Deux listes, deux numérotations : la liste numérotée repart de 1.
    numbering = part(path, "word/numbering.xml")
    nums = numbering.findall(f"{W}num")
    assert [_val(n, "abstractNumId") for n in nums] == ["0", "1"]
    core = part(path, "docProps/core.xml")
    said = ElementTree.tostring(core, encoding="unicode")
    assert "Devis D-2026-042" in said and "fr-FR" in said
    # Une date fixe, écrite en dur ici : pas la constante du module, qu'une faute changerait aussi.
    assert said.count("2000-01-01T00:00:00Z") == 2 and FIXED_DATE == "2000-01-01T00:00:00Z"


async def test_same_description_same_file(tool: WordTool, disk: Path) -> None:
    first = await call(tool, path="sortie/a.docx", **DEVIS)
    second = await call(tool, path="sortie/b.docx", **DEVIS)
    assert first["sha256"] == second["sha256"]
    again = await call(tool, path="sortie/a.docx", **DEVIS)
    # Le même contenu déjà là n'est pas réécrit : une reprise est sans effet.
    assert again["written"] is False
    with zipfile.ZipFile(disk / "sortie" / "a.docx") as archive:
        assert {info.date_time for info in archive.infolist()} == {(2000, 1, 1, 0, 0, 0)}


async def test_replacing_needs_overwrite(tool: WordTool) -> None:
    await call(tool, path="sortie/a.docx", **DEVIS)
    other = {**DEVIS, "title": "Autre"}
    await refused(tool, "existe déjà : overwrite=true", path="sortie/a.docx", **other)
    replaced = await call(tool, path="sortie/a.docx", overwrite=True, **other)
    assert replaced["replaced"] is True


async def test_paths_follow_local_files(tool: WordTool, disk: Path) -> None:
    await refused(tool, r"s'écrit en \.docx", path="sortie/a.pdf", **DEVIS)
    await refused(tool, "Droit write refusé", path="modeles/a.docx", **DEVIS)
    await refused(tool, "Droit read refusé", action="read", path="valide/a.docx")
    await refused(tool, "« .. » n'est pas permis", path="sortie/../a.docx", **DEVIS)
    await refused(tool, "Dossier inconnu", path="ailleurs/a.docx", **DEVIS)
    (disk / "sortie" / "lien").symlink_to(disk)
    await refused(tool, "hors des dossiers permis", path="sortie/lien/a.docx", **DEVIS)


def test_assess_asks_only_where_the_folder_asks(tool: WordTool) -> None:
    free = tool.assess_call({"path": "sortie/a.docx", **DEVIS})
    assert (free.approval, free.side_effects, free.idempotent) == ("never", "reversible", True)
    replacing = tool.assess_call({"path": "sortie/a.docx", "overwrite": True, **DEVIS})
    assert (replacing.approval, replacing.side_effects) == ("always", "irreversible")
    assert replacing.reason == (
        "écrire le fichier sortie/a.docx, en remplaçant le fichier s'il existe — la config "
        "exige une approbation pour remplacer un fichier existant dans sortie"
    )
    asked = tool.assess_call({"path": "valide/a.docx", **DEVIS})
    assert asked.approval == "always"
    read = tool.assess_call({"action": "read", "path": "modeles/a.docx"})
    assert (read.approval, read.side_effects) == ("never", "none")
    invalid = tool.assess_call({"path": "sortie/a.docx"})
    assert invalid.side_effects == "none"


async def test_libreoffice_opens_what_it_writes(tool: WordTool, disk: Path, soffice: str) -> None:
    await call(tool, path="sortie/devis.docx", **DEVIS)
    out = disk / "converti"
    convert(soffice, disk / "sortie" / "devis.docx", out, "txt:Text (encoded):UTF8")
    text = (out / "devis.txt").read_text(encoding="utf-8-sig")
    for said in ("Devis D-2026-042", "Montant HT", "Total", "Travail soigné"):
        assert said in text, said


# --- Lecture --------------------------------------------------------------------------


async def test_what_it_writes_reads_back_in_the_same_vocabulary(tool: WordTool) -> None:
    await call(tool, path="sortie/devis.docx", **DEVIS)
    found = await call(tool, action="read", path="sortie/devis.docx")
    assert found["title"] == "Devis D-2026-042" and found["truncated"] is False
    blocks = found["blocks"]
    assert blocks[0] == {"type": "heading", "level": 0, "text": "Devis D-2026-042"}
    assert blocks[1] == {
        "type": "paragraph",
        "text": "Madame **Dupont**,\nvoici notre *proposition*.",
    }
    assert blocks[2] == {"type": "heading", "level": 1, "text": "Travaux"}
    assert blocks[3] == {
        "type": "list",
        "ordered": False,
        "items": ["Dépose", {"text": "Pose", "items": ["receveur", "paroi"]}],
    }
    assert blocks[4] == {"type": "list", "ordered": True, "items": ["Signature", "Acompte"]}
    assert blocks[5]["type"] == "table"
    assert blocks[5]["rows"][-1] == ["Total", "1\u00a0430,50\u00a0€"]
    assert {"type": "note", "text": "**Validité**\n3 mois."} in blocks
    assert {"type": "quote", "text": "Travail soigné."} in blocks
    assert blocks[-1] == {"type": "heading", "level": 2, "text": "Conditions"}


def docx(
    body: str, *, styles: str = "", numbering: str = "", extra: dict[str, str] | None = None
) -> bytes:
    """Un .docx fait main : le corps, et au besoin des styles et une numérotation."""
    relations = ""
    if styles:
        relations += (
            '<Relationship Id="rS" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/styles" Target="styles.xml"/>'
        )
    if numbering:
        relations += (
            '<Relationship Id="rN" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/numbering" Target="numbering.xml"/>'
        )
    parts = {
        "[Content_Types].xml": "<Types/>",
        "_rels/.rels": (
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="r1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/officeDocument" Target="word/document.xml"/></Relationships>'
        ),
        "word/document.xml": f"<w:document {NS}><w:body>{body}</w:body></w:document>",
        "word/_rels/document.xml.rels": (
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f"{relations}</Relationships>"
        ),
        **({"word/styles.xml": f"<w:styles {NS}>{styles}</w:styles>"} if styles else {}),
        **(
            {"word/numbering.xml": f"<w:numbering {NS}>{numbering}</w:numbering>"}
            if numbering
            else {}
        ),
        **(extra or {}),
    }
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, text in parts.items():
            archive.writestr(name, text)
    return raw.getvalue()


def p(text: str, props: str = "", run: str = "") -> str:
    return f"<w:p><w:pPr>{props}</w:pPr><w:r><w:rPr>{run}</w:rPr><w:t>{text}</w:t></w:r></w:p>"


async def test_reads_what_word_writes_and_this_tool_does_not(tool: WordTool, disk: Path) -> None:
    styles = (
        '<w:style w:type="paragraph" w:styleId="Titre1"><w:name w:val="heading 1"/></w:style>'
        '<w:style w:type="paragraph" w:styleId="Perso"><w:basedOn w:val="Titre1"/>'
        '<w:name w:val="Mon titre"/></w:style>'
        '<w:style w:type="paragraph" w:styleId="Puce"><w:name w:val="Ma puce"/><w:pPr>'
        '<w:numPr><w:numId w:val="7"/></w:numPr></w:pPr></w:style>'
    )
    numbering = (
        '<w:abstractNum w:abstractNumId="3"><w:lvl w:ilvl="0"><w:numFmt w:val="bullet"/>'
        "</w:lvl></w:abstractNum>"
        '<w:num w:numId="7"><w:abstractNumId w:val="3"/></w:num>'
    )
    body = (
        p("Chapitre", '<w:pStyle w:val="Titre1"/>')
        + p("Section héritée", '<w:pStyle w:val="Perso"/>')
        + p("Niveau direct", '<w:outlineLvl w:val="2"/>')
        + p("Point par style", '<w:pStyle w:val="Puce"/>')
        + "<w:p><w:r><w:t>gardé </w:t></w:r><w:ins><w:r><w:t>inséré</w:t></w:r></w:ins>"
        "<w:del><w:r><w:delText>supprimé</w:delText></w:r></w:del>"
        '<w:r><w:rPr><w:b w:val="0"/></w:rPr><w:t> pas gras</w:t></w:r>'
        "<w:r><w:fldChar/><w:instrText>PAGE</w:instrText></w:r>"
        "<w:hyperlink><w:r><w:t> lien</w:t></w:r></w:hyperlink>"
        "<w:moveFrom><w:r><w:t> parti</w:t></w:r></w:moveFrom>"
        "<w:moveTo><w:r><w:t> arrivé</w:t></w:r></w:moveTo>"
        "<w:r><w:drawing/></w:r></w:p>"
        "<w:sdt><w:sdtContent>" + p("Dans un contrôle") + "</w:sdtContent></w:sdt>"
        "<w:tbl><w:tr><w:tc><w:tcPr><w:gridSpan w:val=\"2\"/></w:tcPr>" + p("Fusion")
        + "</w:tc></w:tr><w:tr><w:tc>" + p("a") + "</w:tc><w:tc><w:tcPr>"
        '<w:vMerge w:val="restart"/></w:tcPr>' + p("b") + "</w:tc></w:tr>"
        "<w:tr><w:tc>" + p("c") + "</w:tc><w:tc><w:tcPr><w:vMerge/></w:tcPr>" + p("b")
        + "</w:tc></w:tr></w:tbl>"
        + p("   ")
    )  # fmt: skip
    (disk / "modeles" / "fait-main.docx").write_bytes(
        docx(body, styles=styles, numbering=numbering, extra={"word/comments.xml": "<c/>"})
    )
    found = await call(tool, action="read", path="modeles/fait-main.docx")
    assert found["blocks"] == [
        {"type": "heading", "level": 1, "text": "Chapitre"},
        {"type": "heading", "level": 1, "text": "Section héritée"},
        {"type": "heading", "level": 3, "text": "Niveau direct"},
        {"type": "list", "ordered": False, "items": ["Point par style"]},
        {"type": "paragraph", "text": "gardé inséré pas gras lien arrivé"},
        {"type": "paragraph", "text": "Dans un contrôle"},
        {"type": "table", "rows": [["Fusion", ""], ["a", "b"], ["c", ""]]},
    ]
    notes = " ".join(found["notes"])
    assert "1 image(s)" in notes and "Modifications suivies" in notes
    # Pas de partie de commentaires reliée ici : seule une relation « comments » compte.
    assert "commentaires" not in notes


async def test_reads_a_document_from_elsewhere(tool: WordTool, disk: Path, soffice: str) -> None:
    html = disk / "ailleurs.html"
    html.write_text(
        "<html><body><h1>Rapport</h1><p>Texte <b>gras</b> et <i>penché</i>.</p>"
        "<h2>Points</h2><ol><li>un</li><li>deux</li></ol>"
        "<table border=1><tr><th>Nom</th><th>Total</th></tr><tr><td>A</td><td>3</td></tr>"
        "</table></body></html>",
        encoding="utf-8",
    )
    convert(soffice, html, disk / "modeles", "docx:MS Word 2007 XML")
    found = await call(tool, action="read", path="modeles/ailleurs.docx")
    blocks = found["blocks"]
    assert {"type": "heading", "level": 1, "text": "Rapport"} in blocks
    assert {"type": "heading", "level": 2, "text": "Points"} in blocks
    assert {"type": "paragraph", "text": "Texte **gras** et *penché*."} in blocks
    assert {"type": "list", "ordered": True, "items": ["un", "deux"]} in blocks
    assert {"type": "table", "rows": [["Nom", "Total"], ["A", "3"]]} in blocks


async def test_a_long_document_is_read_in_parts(tool: WordTool, disk: Path) -> None:
    body = "".join(p(f"Partie {n}", '<w:outlineLvl w:val="0"/>') + p("x" * 900) for n in range(100))
    (disk / "modeles" / "long.docx").write_bytes(docx(body))
    first = await call(tool, action="read", path="modeles/long.docx")
    assert first["truncated"] is True and first["block_count"] == 200
    start = first["next_start"]
    assert 0 < start < 200 and len(first["blocks"]) == start
    assert first["outline"][3] == {"index": 6, "level": 1, "text": "Partie 3"}
    rest = await call(tool, action="read", path="modeles/long.docx", start=start)
    assert rest["blocks"][0] == first["blocks"][0] if start == 0 else rest["start"] == start
    beyond = await call(tool, action="read", path="modeles/long.docx", start=500)
    assert beyond["blocks"] == [] and "start va de 0 à 199" in " ".join(beyond["notes"])


# --- Fichiers hostiles ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "data", "said"),
    [
        ("vieux.doc", b"\xd0\xcf\x11\xe0", r"Word 97-2003 \(\.doc\) non lu"),
        ("texte.odt", b"PK", r"OpenDocument \(\.odt\) non lu"),
        ("renomme.docx", b"pas un zip", "pas un document .docx lisible"),
    ],
)
async def test_unreadable_files(
    tool: WordTool, disk: Path, name: str, data: bytes, said: str
) -> None:
    (disk / "modeles" / name).write_bytes(data)
    await refused(tool, said, action="read", path=f"modeles/{name}")


async def test_a_zip_without_a_body_is_refused(tool: WordTool, disk: Path) -> None:
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as archive:
        archive.writestr("xl/workbook.xml", "<x/>")
    (disk / "modeles" / "classeur.docx").write_bytes(raw.getvalue())
    await refused(tool, r"pas un document Word \(word/document.xml absent\)", action="read",
                  path="modeles/classeur.docx")  # fmt: skip


async def test_a_zip_bomb_is_refused_unopened(
    tool: WordTool, disk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(reader_module, "MAX_UNCOMPRESSED", 1_000_000)
    body = p("0" * 2_000_000)
    (disk / "modeles" / "bombe.docx").write_bytes(docx(body))
    assert (disk / "modeles" / "bombe.docx").stat().st_size < 20_000
    await refused(tool, "une fois décompressé, au-delà de 1000000", action="read",
                  path="modeles/bombe.docx")  # fmt: skip


async def test_xml_entities_are_neither_expanded_nor_loaded(tool: WordTool, disk: Path) -> None:
    laughs = (
        '<?xml version="1.0"?><!DOCTYPE d [<!ENTITY a "aaaaaaaaaa">'
        + "".join(f'<!ENTITY {chr(98 + i)} "{("&" + chr(97 + i) + ";") * 10}">' for i in range(8))
        + f"]><w:document {NS}><w:body><w:p><w:r><w:t>&i;</w:t></w:r></w:p></w:body></w:document>"
    )
    raw = io.BytesIO(docx(""))
    data = _replace(raw.getvalue(), "word/document.xml", laughs)
    (disk / "modeles" / "rires.docx").write_bytes(data)
    await refused(tool, "XML refusé", action="read", path="modeles/rires.docx")
    secret = disk / "secret.txt"
    secret.write_text("MOT-DE-PASSE")
    external = (
        f'<?xml version="1.0"?><!DOCTYPE d [<!ENTITY x SYSTEM "{secret.as_uri()}">]>'
        f"<w:document {NS}><w:body><w:p><w:r><w:t>&x;</w:t></w:r></w:p></w:body></w:document>"
    )
    (disk / "modeles" / "externe.docx").write_bytes(
        _replace(docx(""), "word/document.xml", external)
    )
    with pytest.raises(ToolError) as caught:
        await tool.invoke({"action": "read", "path": "modeles/externe.docx"}, CONTEXT)
    assert "MOT-DE-PASSE" not in str(caught.value)


async def test_deep_nesting_is_refused(tool: WordTool, disk: Path) -> None:
    body = "<w:sdt><w:sdtContent>" * 3000 + p("fond") + "</w:sdtContent></w:sdt>" * 3000
    (disk / "modeles" / "profond.docx").write_bytes(docx(body))
    await refused(tool, "trop imbriqué", action="read", path="modeles/profond.docx")


def _replace(data: bytes, name: str, text: str) -> bytes:
    source = zipfile.ZipFile(io.BytesIO(data))
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as target:
        for info in source.infolist():
            target.writestr(info.filename, text if info.filename == name else source.read(info))
    return raw.getvalue()


def test_reading_is_refused_where_expat_is_too_old(
    disk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pyexpat

    monkeypatch.setattr(pyexpat, "version_info", (2, 2, 0))
    old = make(disk, {"name": "modeles", "path": "modeles", "rights": ["read"]})
    assert "indisponible ici (expat 2.2.0" in old.spec.description
    assert old.settings().options[-1][1].startswith("non (expat 2.2.0")


# --- Montage par la config, run, validate ----------------------------------------------


def word_agent(**reference: Any) -> dict[str, Any]:
    return demo_agent(tools=[{"python": "loom.word", **reference}])


@pytest.mark.parametrize(
    ("old", "said"),
    [
        ("charts", r"charts → blocks: \[chart\]"),
        ("images", r"images → blocks: \[image\]"),
        ("extra_blocks", r"extra_blocks → blocks: \[signature, toc, checkboxes\]"),
    ],
)
def test_the_keys_of_2_0_say_the_new_form(demo: ConfigFactory, old: str, said: str) -> None:
    """``charts``, ``images`` et ``extra_blocks`` (2.0.x) : refusés avec ce qui les remplace."""
    folders = [{"name": "s", "path": "/tmp", "rights": ["write"]}]
    with pytest.raises(ConfigError, match=said):
        load_config(demo(agents=[word_agent(folders=folders, **{old: True})]))


@pytest.mark.parametrize(
    ("blocks", "said"),
    [
        (["chart", "chart"], "bloc nommé deux fois : chart"),
        (
            ["graphique"],
            "Input should be 'chart', 'image', 'signature', 'toc', 'checkboxes', 'fields' "
            "or 'code'",
        ),
    ],
)
def test_blocks_are_known_and_named_once(demo: ConfigFactory, blocks: list[str], said: str) -> None:
    folders = [{"name": "s", "path": "/tmp", "rights": ["write"]}]
    with pytest.raises(ConfigError, match=said):
        load_config(demo(agents=[word_agent(folders=folders, blocks=blocks)]))


def test_delete_is_refused_when_the_config_loads(demo: ConfigFactory) -> None:
    folders = [{"name": "s", "path": "/tmp", "rights": ["write"], "approval": {"delete": "never"}}]
    with pytest.raises(
        ConfigError, match=r"dossier s : le droit 'delete' ne sert pas à loom\.word"
    ):
        load_config(demo(agents=[word_agent(folders=folders)]))


async def test_a_run_writes_the_document(demo: ConfigFactory, tmp_path: Path) -> None:
    (tmp_path / "sortie").mkdir()
    script: list[dict[str, Any]] = [
        {
            "text": "J'écris le devis.",
            "tool_calls": [{"name": "word", "arguments": {"path": "sortie/d.docx", **DEVIS}}],
        },
        {"text": "Devis écrit."},
    ]
    folders = [{"name": "sortie", "path": "sortie", "rights": ["write"]}]
    path = demo(
        agents=[word_agent(timeout=30, folders=folders)],
        models=[{**MODEL, "params": {"script": script}}],
        storage=JSONL,
    )
    store = InMemoryEventStore()
    agent = build_agent(load_config(path), "demo", store)
    (spec,) = agent.context.tools.specs
    assert (spec.name, spec.timeout, spec.approval) == ("word", 30, "never")
    try:
        run = await begin_run(agent.context, "Fais le devis.")
        state = await drive(agent.context, run.run_id)
    finally:
        await agent.aclose()
    assert state.status is RunStatus.COMPLETED
    events = await store.read(DEFAULT_TENANT, run.session_id)
    [done] = [e.payload for e in events if isinstance(e.payload, ToolCompleted)]
    assert isinstance(done.output.data, dict) and done.output.data["path"] == "sortie/d.docx"
    assert (tmp_path / "sortie" / "d.docx").is_file()


def test_validate_shows_the_folders(
    demo: ConfigFactory, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "sortie").mkdir()
    folders = [{"name": "sortie", "path": "sortie", "rights": ["read", "write"]}]
    path = demo(agents=[word_agent(folders=folders)], storage=JSONL)
    assert main(["--config", str(path), "validate"]) == 0
    out = capsys.readouterr().out
    assert "loom.word" in out
    assert (
        "fuseau : Europe/Paris · blocs : aucun · modèles : aucun · "
        "lecture des documents : oui" in out
    )


async def test_overwrite_needs_its_right(disk: Path) -> None:
    only = make(disk, {"name": "sortie", "path": "sortie", "rights": ["write"]})
    await refused(only, "Droit overwrite refusé", path="sortie/a.docx", overwrite=True, **DEVIS)


# --- Graphiques ------------------------------------------------------------------------

C: Final = "{http://schemas.openxmlformats.org/drawingml/2006/chart}"
RAPPORT: Final[dict[str, Any]] = {
    "title": "Top clients",
    "blocks": [
        {
            "type": "table",
            "columns": [{"header": "Client"}, {"header": "Total TTC", "type": "amount_eur"}],
            "rows": [["Barre", 18034.17], ["Lemaire", 16247.36], ["Guillot", 14709.17]],
        },
        {
            "type": "chart",
            "kind": "horizontal_bar",
            "title": "TTC par client",
            "from_table": {"categories": "Client", "values": ["Total TTC"]},
            "caption": "Montants facturés.",
        },
        {
            "type": "chart",
            "kind": "pie",
            "value_type": "percent",
            "categories": ["Plomberie", "Chauffage"],
            "series": [{"name": "Part", "values": [0.6, 0.4]}],
        },
    ],
}


@pytest.fixture
def charted(disk: Path) -> WordTool:
    """Le même outil, monté avec ``blocks: [chart]``."""
    return make(
        disk,
        {"name": "modeles", "path": "modeles", "rights": ["read"]},
        {"name": "sortie", "path": "sortie", "rights": ["read", "write", "overwrite"]},
        blocks=["chart"],
    )


def test_charts_are_off_unless_asked(tool: WordTool, charted: WordTool) -> None:
    # Sans chart dans blocks, ni le bloc ni sa ligne.
    assert "chart" not in json.dumps(tool.spec.input_schema)
    assert "Graphique" not in tool.spec.description
    assert '"chart"' in json.dumps(charted.spec.input_schema)
    assert "Graphique ('type': 'chart')" in charted.spec.description
    assert ("blocs", "chart") in charted.settings().options


async def test_without_charts_a_chart_is_refused(tool: WordTool, charted: WordTool) -> None:
    arguments: dict[str, Any] = {"path": "sortie/r.docx", **RAPPORT}
    # Le moteur valide les arguments contre le schéma, avant l'outil.
    assert list(bounded_validator(tool.spec.input_schema).iter_errors(arguments))
    assert not list(bounded_validator(charted.spec.input_schema).iter_errors(arguments))
    with pytest.raises(ToolError, match="Arguments invalides"):
        await call(tool, **arguments)


async def test_reading_charts_without_charts_says_so(tool: WordTool, charted: WordTool) -> None:
    await call(charted, path="sortie/rapport.docx", **RAPPORT)
    found = await call(tool, action="read", path="sortie/rapport.docx")
    # Lus quand même : lire un graphique ne coûte rien dans le schéma.
    assert [b["kind"] for b in found["blocks"] if b["type"] == "chart"] == [
        "horizontal_bar",
        "pie",
    ]
    assert "Lu : chart (2) ; cet outil n'en écrit pas" in " ".join(found["notes"])
    assert "(ou blocks: [chart] dans la config de l'outil)" in " ".join(found["notes"])
    again = await call(charted, action="read", path="sortie/rapport.docx")
    assert "chart" not in " ".join(again.get("notes", []))


def test_validate_shows_charts(
    demo: ConfigFactory, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "sortie").mkdir()
    folders = [{"name": "sortie", "path": "sortie", "rights": ["write"]}]
    path = demo(agents=[word_agent(folders=folders, blocks=["chart"])], storage=JSONL)
    assert main(["--config", str(path), "validate"]) == 0
    assert "blocs : chart ·" in capsys.readouterr().out


async def test_a_chart_is_a_native_word_chart(charted: WordTool, disk: Path) -> None:
    tool = charted
    result = await call(tool, path="sortie/rapport.docx", **RAPPORT)
    assert result["warnings"] == []
    path = disk / "sortie" / "rapport.docx"
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        types = archive.read("[Content_Types].xml").decode()
        relations = archive.read("word/_rels/document.xml.rels").decode()
    assert {"word/charts/chart1.xml", "word/charts/chart2.xml"} <= names
    assert types.count("drawingml.chart+xml") == 2
    assert 'Target="charts/chart2.xml"' in relations
    body = part(path, "word/document.xml")
    assert [c.get(f"{{{_R}}}id") for c in body.iter(f"{C}chart")] == ["rIdChart1", "rIdChart2"]
    first = part(path, "word/charts/chart1.xml")
    direction = first.find(f".//{C}barDir")
    assert direction is not None and direction.get("val") == "bar"
    values = [v.text for v in first.iter(f"{C}v")]
    assert ["Barre", "Lemaire", "Guillot"] == values[1:4]
    assert ["18034.17", "16247.36", "14709.17"] == values[4:]
    # De la place au bout de la plus grande barre pour son étiquette : 18 034,17 fois 1,22,
    # arrondi à la graduation ronde suivante.
    scaling = first.find(f".//{C}valAx/{C}scaling")
    assert scaling is not None
    bounds = [(child.tag.removeprefix(C), child.get("val")) for child in scaling]
    assert bounds == [("orientation", "minMax"), ("max", "24000"), ("min", "0")]
    second = part(path, "word/charts/chart2.xml")
    assert second.find(f".//{C}pieChart") is not None
    # Le même document, le même fichier.
    again = await call(tool, path="sortie/rapport2.docx", **RAPPORT)
    assert again["sha256"] == result["sha256"]


_R: Final = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


@pytest.mark.parametrize(
    ("chart", "bounds"),
    [
        # Barres verticales : 0,6 fois 1,1, arrondi à 0,02 près.
        ({"kind": "bar", "series": [{"name": "Part", "values": [0.6, 0.4]}]}, ["0.66", "0"]),
        # Deux séries : pas d'étiquette, l'axe est celui de Word.
        (
            {
                "kind": "bar",
                "series": [{"name": "A", "values": [1, 2]}, {"name": "B", "values": [3, 4]}],
            },
            [],
        ),
        # Une valeur négative : l'axe est celui de Word.
        ({"kind": "bar", "series": [{"name": "Écart", "values": [-5, 12]}]}, []),
        # Une courbe n'a pas d'étiquette.
        ({"kind": "line", "series": [{"name": "CA", "values": [10, 20]}]}, []),
    ],
)
async def test_the_value_axis_leaves_room_for_the_labels(
    charted: WordTool, disk: Path, chart: dict[str, Any], bounds: list[str]
) -> None:
    block = {"type": "chart", "categories": ["a", "b"], **chart}
    await call(charted, path="sortie/axe.docx", blocks=[block], overwrite=True)
    scaling = part(disk / "sortie" / "axe.docx", "word/charts/chart1.xml").find(
        f".//{C}valAx/{C}scaling"
    )
    assert scaling is not None
    found = [child.get("val") for child in scaling if child.tag != f"{C}orientation"]
    assert found == bounds


async def test_a_document_without_chart_has_no_chart_part(tool: WordTool, disk: Path) -> None:
    from loom_ia.tools_builtin.word.writer import CONTENT_TYPES, DOCUMENT_RELS

    await call(tool, path="sortie/devis.docx", **DEVIS)
    with zipfile.ZipFile(disk / "sortie" / "devis.docx") as archive:
        assert not [n for n in archive.namelist() if "chart" in n]
        assert archive.read("[Content_Types].xml").decode() == CONTENT_TYPES
        assert archive.read("word/_rels/document.xml.rels").decode() == DOCUMENT_RELS


async def test_a_chart_reads_back_in_the_same_vocabulary(charted: WordTool) -> None:
    tool = charted
    await call(tool, path="sortie/rapport.docx", **RAPPORT)
    found = await call(tool, action="read", path="sortie/rapport.docx")
    charts = [b for b in found["blocks"] if b["type"] == "chart"]
    assert charts == [
        {
            "type": "chart",
            "kind": "horizontal_bar",
            "title": "TTC par client",
            "categories": ["Barre", "Lemaire", "Guillot"],
            "series": [{"name": "Total TTC", "values": [18034.17, 16247.36, 14709.17]}],
        },
        {
            "type": "chart",
            "kind": "pie",
            "categories": ["Plomberie", "Chauffage"],
            "series": [{"name": "Part", "values": [0.6, 0.4]}],
        },
    ]
    # Un graphique n'est pas compté comme une image non lue.
    assert "image" not in " ".join(found.get("notes", []))


async def test_libreoffice_reads_the_chart(charted: WordTool, disk: Path, soffice: str) -> None:
    tool = charted
    await call(tool, path="sortie/rapport.docx", **RAPPORT)
    convert(soffice, disk / "sortie" / "rapport.docx", disk / "odt", "odt")
    with zipfile.ZipFile(disk / "odt" / "rapport.odt") as archive:
        objects = [
            archive.read(name).decode()
            for name in archive.namelist()
            if name.startswith("Object") and name.endswith("content.xml")
        ]
    classes = sorted(set(re.findall(r'chart:class="chart:(\w+)"', "".join(objects))))
    assert classes == ["bar", "circle"]
    assert "18034.17" in "".join(objects)


# --- Images ---------------------------------------------------------------------------

A: Final = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
WP: Final = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"
PHOTO: Final = Path(__file__).parents[1] / "examples" / "j2" / "files" / "photo.jpg"


@pytest.fixture
def imaged(disk: Path) -> WordTool:
    """Monté avec ``images`` et ``charts`` ; des maquettes à lire, l'une sous approbation."""
    shots = disk / "maquettes"
    shots.mkdir()
    (shots / "depot.png").write_bytes(png(390, 844))
    (shots / "tableau.png").write_bytes(png(1600, 900, color=6))
    (shots / "photo.jpg").write_bytes(PHOTO.read_bytes())
    (disk / "secret").mkdir()
    (disk / "secret" / "plan.png").write_bytes(png(800, 600))
    return make(
        disk,
        {"name": "maquettes", "path": "maquettes", "rights": ["read"]},
        {"name": "sortie", "path": "sortie", "rights": ["read", "write", "overwrite"]},
        {"name": "valide", "path": "valide", "rights": ["write"]},
        {"name": "secret", "path": "secret", "rights": ["read"], "approval": {"read": "always"}},
        blocks=["chart", "image"],
    )


CDC: Final[dict[str, Any]] = {
    "title": "Cahier des charges",
    "blocks": [
        {"type": "heading", "text": "Écrans"},
        {
            "type": "image",
            "path": "maquettes/depot.png",
            "caption": "Écran 1 — Dépôt de la demande",
        },
        {"type": "image", "path": "maquettes/tableau.png", "width": "half", "alt": "Tableau"},
        {
            "type": "chart",
            "categories": ["sept.", "oct."],
            "series": [{"name": "Demandes", "values": [12, 18]}],
        },
        {"type": "image", "path": "maquettes/photo.jpg", "width": "half", "caption": "Photo"},
        # La même image, une deuxième fois : stockée une seule fois.
        {"type": "image", "path": "maquettes/depot.png", "width": "small", "alt": "Rappel"},
    ],
}


def test_images_are_off_unless_asked(tool: WordTool, imaged: WordTool) -> None:
    assert '"image"' not in json.dumps(tool.spec.input_schema)
    assert "Image ('type': 'image')" not in tool.spec.description
    assert '"image"' in json.dumps(imaged.spec.input_schema)
    assert "Image ('type': 'image')" in imaged.spec.description
    assert ("blocs", "chart, image") in imaged.settings().options
    # Le chemin garde son suffixe dans le schéma, quels que soient les réglages.
    for mounted in (tool, imaged):
        properties = cast(dict[str, Any], mounted.spec.input_schema["properties"])
        assert properties["path"]["description"] == "« <dossier>/<chemin>.docx »"


async def test_without_images_an_image_is_refused(tool: WordTool, imaged: WordTool) -> None:
    arguments: dict[str, Any] = {"path": "sortie/a.docx", "blocks": [CDC["blocks"][1]]}
    assert list(bounded_validator(tool.spec.input_schema).iter_errors(arguments))
    assert not list(bounded_validator(imaged.spec.input_schema).iter_errors(arguments))
    with pytest.raises(ToolError, match="Arguments invalides"):
        await call(tool, **arguments)


async def test_images_are_parts_of_the_archive(imaged: WordTool, disk: Path) -> None:
    result = await call(imaged, path="sortie/cdc.docx", **CDC)
    assert (result["blocks"], result["images"], result["warnings"]) == (6, 4, [])
    path = disk / "sortie" / "cdc.docx"
    with zipfile.ZipFile(path) as archive:
        media = {i.filename: i for i in archive.infolist() if i.filename.startswith("word/media/")}
        types = part(path, "[Content_Types].xml")
        relations = part(path, "word/_rels/document.xml.rels")
        # Une image n'est pas recompressée.
        assert {i.compress_type for i in media.values()} == {zipfile.ZIP_STORED}
        assert archive.read("word/media/image3.jpeg") == PHOTO.read_bytes()
    assert sorted(media) == [
        "word/media/image1.png",
        "word/media/image2.png",
        "word/media/image3.jpeg",
    ]
    defaults = {
        d.get("Extension"): d.get("ContentType") for d in types if d.tag.endswith("Default")
    }
    assert (defaults["png"], defaults["jpeg"]) == ("image/png", "image/jpeg")
    targets = {r.get("Id"): r.get("Target") for r in relations}
    body = part(path, "word/document.xml")
    embeds = [b.get(f"{{{_R}}}embed") for b in body.iter(f"{A}blip")]
    assert [targets[e] for e in embeds if e] == [
        "media/image1.png", "media/image2.png", "media/image3.jpeg", "media/image1.png",
    ]  # fmt: skip
    # Un identifiant par dessin, graphique compris ; le nom garde l'adresse du fichier lu.
    drawn = [(d.get("id"), d.get("name"), d.get("descr")) for d in body.iter(f"{WP}docPr")]
    assert drawn == [
        ("1", "maquettes/depot.png", "Écran 1 — Dépôt de la demande"),
        ("2", "maquettes/tableau.png", "Tableau"),
        ("3", "Graphique 1", None),
        ("4", "maquettes/photo.jpg", "Photo"),
        ("5", "maquettes/depot.png", "Rappel"),
    ]
    # Les proportions en EMU (360 par centième de mm) ; la maquette bornée en hauteur.
    sizes = [(e.get("cx"), e.get("cy")) for e in body.iter(f"{WP}extent")]
    assert sizes[0] == (str(9_704 * 360), str(21_000 * 360))
    assert sizes[1] == (str(8_500 * 360), str(4_781 * 360))
    # La légende reste avec son image.
    assert styles_of(body)[2:5] == ["", "Caption", ""]
    again = await call(imaged, path="sortie/cdc2.docx", **CDC)
    assert again["sha256"] == result["sha256"]


async def test_a_document_without_image_is_unchanged(imaged: WordTool, disk: Path) -> None:
    from loom_ia.tools_builtin.word.writer import CONTENT_TYPES, DOCUMENT_RELS

    await call(imaged, path="sortie/devis.docx", **DEVIS)
    with zipfile.ZipFile(disk / "sortie" / "devis.docx") as archive:
        assert not [n for n in archive.namelist() if "media" in n]
        assert archive.read("[Content_Types].xml").decode() == CONTENT_TYPES
        assert archive.read("word/_rels/document.xml.rels").decode() == DOCUMENT_RELS


async def test_image_faults_are_said_together(imaged: WordTool, disk: Path) -> None:
    shots = disk / "maquettes"
    (shots / "note.png").write_bytes(b"%PDF-1.7 pas une image")
    (shots / "coupee.png").write_bytes(png(100, 100)[:-20])
    (shots / "lourde.jpg").write_bytes(jpeg(10, 10) + bytes(MAX_IMAGE_BYTES))
    (shots / "dehors.png").symlink_to(disk / "modeles")
    (disk / "sortie" / "a.png").write_bytes(png(10, 10))
    (disk / "valide" / "b.png").write_bytes(png(10, 10))
    blocks = [
        {"type": "image", "path": path, "alt": "x"}
        for path in (
            "valide/b.png",
            "inconnu/c.png",
            "maquettes/absente.png",
            "maquettes/dehors.png",
            "maquettes/note.png",
            "maquettes/coupee.png",
            "maquettes/lourde.jpg",
            "sortie/a.png",
        )
    ]
    with pytest.raises(ToolError) as caught:
        await call(imaged, path="sortie/x.docx", blocks=blocks)
    assert str(caught.value).splitlines() == [
        "Document refusé :",
        "- bloc 1 (image) : Droit read refusé sur valide/b.png (dossier valide : write)",
        "- bloc 2 (image) : Dossier inconnu : 'inconnu'. Dossiers permis : maquettes, sortie, "
        "valide, secret",
        "- bloc 3 (image) : maquettes/absente.png : fichier introuvable",
        "- bloc 4 (image) : Chemin refusé : maquettes/dehors.png mène hors des dossiers permis",
        "- bloc 5 (image) : maquettes/note.png : format non reconnu : PNG ou JPEG attendu (le "
        "format se lit dans le fichier, pas dans son nom)",
        "- bloc 6 (image) : maquettes/coupee.png : PNG tronqué (fichier incomplet)",
        f"- bloc 7 (image) : maquettes/lourde.jpg dépasse {MAX_IMAGE_BYTES} octets",
    ]
    assert not (disk / "sortie" / "x.docx").exists()


def test_assess_counts_the_reads_of_images(imaged: WordTool) -> None:
    free = imaged.assess_call({"path": "sortie/a.docx", **CDC})
    assert (free.approval, free.side_effects) == ("never", "reversible")
    blocks: list[Any] = [
        *CDC["blocks"],
        {"type": "image", "path": "secret/plan.png", "alt": "Plan"},
    ]
    asked = imaged.assess_call({"path": "sortie/a.docx", "blocks": blocks})
    assert (asked.approval, asked.side_effects) == ("always", "reversible")
    assert asked.reason == (
        "écrire le fichier sortie/a.docx, avec 4 image(s) lue(s) dans maquettes, secret — la "
        "config exige une approbation pour lire dans secret"
    )
    # Une image d'un dossier sans read : l'appel sera refusé, il n'a aucun effet.
    refused_call = imaged.assess_call(
        {"path": "sortie/a.docx", "blocks": [{"type": "image", "path": "valide/b.png"}]}
    )
    assert refused_call.side_effects == "none"


UNDERLINED: Final = [
    {"type": "heading", "level": 1, "text": "Points <u>importants</u>"},
    {
        "type": "paragraph",
        "text": "Fermé le <u>vendredi</u>, **<u>toute la journée</u>**, pour l'*<u>stock</u>*.",
    },
    {"type": "list", "ordered": False, "items": ["Clés à rendre <u>avant 17 h</u>"]},
]


async def test_underline_is_written_read_back_and_rewritten(tool: WordTool, disk: Path) -> None:
    written = await call(tool, path="sortie/u.docx", title="Note", blocks=UNDERLINED)
    with zipfile.ZipFile(disk / "sortie" / "u.docx") as archive:
        document = archive.read("word/document.xml").decode()
    assert document.count('<w:u w:val="single"/>') == 5
    found = await call(tool, action="read", path="sortie/u.docx")
    blocks = [b for b in found["blocks"] if b.get("level") != 0]
    assert blocks == UNDERLINED
    again = await call(tool, path="sortie/u2.docx", title="Note", blocks=blocks)
    assert again["sha256"] == written["sha256"]


async def test_any_underline_of_a_document_from_elsewhere_reads_back(
    tool: WordTool, disk: Path
) -> None:
    body = (
        '<w:p><w:r><w:t xml:space="preserve">Voir </w:t></w:r>'
        '<w:r><w:rPr><w:u w:val="double"/></w:rPr><w:t>double</w:t></w:r>'
        '<w:r><w:rPr><w:u w:val="none"/></w:rPr><w:t xml:space="preserve"> et non</w:t></w:r>'
        '<w:r><w:rPr><w:b/><w:u w:val="dotted"/></w:rPr><w:t xml:space="preserve"> ici</w:t></w:r>'
        "</w:p>"
    )
    (disk / "modeles" / "souligne.docx").write_bytes(docx(body))
    found = await call(tool, action="read", path="modeles/souligne.docx")
    assert {"type": "paragraph", "text": "Voir <u>double</u> et non **<u>ici</u>**"} in found[
        "blocks"
    ]


async def test_images_read_back_and_rewrite_the_same_file(imaged: WordTool) -> None:
    written = await call(imaged, path="sortie/cdc.docx", **CDC)
    found = await call(imaged, action="read", path="sortie/cdc.docx")
    images = [b for b in found["blocks"] if b["type"] == "image"]
    assert images == [
        {
            "type": "image",
            "path": "maquettes/depot.png",
            "width": "full",
            "caption": "Écran 1 — Dépôt de la demande",
        },
        {"type": "image", "path": "maquettes/tableau.png", "width": "half", "alt": "Tableau"},
        {"type": "image", "path": "maquettes/photo.jpg", "width": "half", "caption": "Photo"},
        {"type": "image", "path": "maquettes/depot.png", "width": "small", "alt": "Rappel"},
    ]
    assert "notes" not in found
    # Relu puis réécrit tel quel : le même fichier.
    blocks = [b for b in found["blocks"] if b.get("level") != 0]
    again = await call(imaged, path="sortie/cdc2.docx", title=found["title"], blocks=blocks)
    assert again["sha256"] == written["sha256"]


async def test_without_images_reading_counts_them(tool: WordTool, imaged: WordTool) -> None:
    await call(imaged, path="sortie/cdc.docx", **CDC)
    found = await call(tool, action="read", path="sortie/cdc.docx")
    assert not [b for b in found["blocks"] if b["type"] == "image"]
    assert "4 image(s) ou forme(s) non lue(s)." in found["notes"]


async def test_an_image_from_elsewhere_has_no_path(imaged: WordTool, disk: Path) -> None:
    await call(imaged, path="sortie/cdc.docx", **CDC)
    source = disk / "sortie" / "cdc.docx"
    edited = disk / "sortie" / "ailleurs.docx"
    with zipfile.ZipFile(source) as old, zipfile.ZipFile(edited, "w") as new:
        for item in old.infolist():
            data = old.read(item)
            if item.filename == "word/document.xml":
                data = re.sub(rb'name="maquettes/[^"]+"', b'name="Image 1"', data)
            new.writestr(item, data)
    found = await call(imaged, action="read", path="sortie/ailleurs.docx")
    images = [b for b in found["blocks"] if b["type"] == "image"]
    assert [("path" in b, b["width"]) for b in images] == [
        (False, "full"), (False, "half"), (False, "half"), (False, "small"),
    ]  # fmt: skip
    assert "4 image(s) sans fichier source (pas de path)" in " ".join(found["notes"])


async def test_libreoffice_shows_the_images(imaged: WordTool, disk: Path, soffice: str) -> None:
    await call(imaged, path="sortie/cdc.docx", **CDC)
    out = disk / "converti"
    convert(soffice, disk / "sortie" / "cdc.docx", out, "odt")
    with zipfile.ZipFile(out / "cdc.odt") as archive:
        pictures = [n for n in archive.namelist() if n.startswith("Pictures/")]
        content = archive.read("content.xml").decode()
    # Trois images distinctes (et l'aperçu du graphique), quatre cadres et leur texte
    # alternatif. LibreOffice renomme le second cadre de la même image (« Image… »).
    assert len([n for n in pictures if not n.startswith("Pictures/Object")]) == 3
    descriptions = re.findall(r"<svg:desc>([^<]*)</svg:desc>", content)
    assert descriptions == ["Écran 1 — Dépôt de la demande", "Tableau", "Photo", "Rappel"]
    assert len(re.findall(r'draw:name="maquettes/', content)) == 3


def test_validate_shows_images(
    demo: ConfigFactory, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "sortie").mkdir()
    folders = [{"name": "sortie", "path": "sortie", "rights": ["read", "write"]}]
    path = demo(agents=[word_agent(folders=folders, blocks=["image", "chart"])], storage=JSONL)
    assert main(["--config", str(path), "validate"]) == 0
    said = capsys.readouterr().out
    assert "blocs : chart, image · modèles : aucun · lecture des documents : oui" in said


# --- Modèles de document -----------------------------------------------------------------

RAPPORT_YAML: Final = """description: Rapport d'intervention chez un client.
instructions: |
  Sobre et factuel.
sections:
  Contexte:
    instructions: |
      Le client, le site, la demande.
  Photos:
    optional: true
  Travaux réalisés:
  Garantie:
    text: |
      Un an, pièces et main-d'œuvre.
"""


def rapport(*names: str) -> dict[str, list[Any]]:
    return {name: [{"type": "paragraph", "text": "Fait."}] for name in names}


@pytest.fixture
def modeled(disk: Path) -> WordTool:
    return make(
        disk,
        {"name": "sortie", "path": "sortie", "rights": ["read", "write", "overwrite"]},
        templates={
            "rapport": {"text": RAPPORT_YAML},
            "note": {"text": "description: Une note courte.\nsections:\n  Message:\n"},
        },
    )


def test_templates_are_off_unless_declared(tool: WordTool, modeled: WordTool) -> None:
    assert "template" not in json.dumps(tool.spec.input_schema)
    assert "sections" not in json.dumps(tool.spec.input_schema)
    assert "Modèles de document" not in tool.spec.description
    assert ("modèles", "aucun") in tool.settings().options
    assert "Modèles de document" in modeled.spec.description
    assert "- rapport : Rapport d'intervention chez un client." in modeled.spec.description
    assert "s'écrit par 'sections'" in modeled.spec.description
    # Ni les consignes ni les sections d'un modèle ne partent dans la description.
    assert "Le client, le site, la demande." not in modeled.spec.description
    assert ("modèles", "rapport, note") in modeled.settings().options


async def test_a_template_is_read_from_the_tool(modeled: WordTool) -> None:
    found = await call(modeled, action="template", template="rapport")
    # Ce que le modèle de langage remplit : ni le texte fixe ni la mise en page.
    assert found == {
        "template": "rapport",
        "description": "Rapport d'intervention chez un client.",
        "instructions": "Sobre et factuel.",
        "sections": [
            {"name": "Contexte", "instructions": "Le client, le site, la demande."},
            {"name": "Photos", "optional": True},
            {"name": "Travaux réalisés"},
        ],
    }
    assessed = modeled.assess_call({"action": "template", "template": "rapport"})
    assert (assessed.approval, assessed.side_effects) == ("never", "none")


async def test_a_document_follows_its_template(modeled: WordTool, disk: Path) -> None:
    with pytest.raises(ToolError, match="write attend 'template'"):
        await call(modeled, path="sortie/r.docx", blocks=[{"type": "paragraph", "text": "x"}])
    with pytest.raises(ToolError) as caught:
        await call(
            modeled,
            path="sortie/r.docx",
            template="rapport",
            sections=rapport("Travaux réalisés", "Bilan"),
        )
    assert str(caught.value).splitlines() == [
        "Document refusé :",
        "- modèle rapport : section inconnue « Bilan »",
        "- modèle rapport : section(s) absente(s) : « Contexte »",
        "- modèle rapport, sections attendues : Contexte · Photos (optional) · Travaux réalisés "
        "· Garantie (fixed)",
    ]
    assert not (disk / "sortie" / "r.docx").exists()
    written = await call(
        modeled,
        path="sortie/r.docx",
        template="rapport",
        title="Rapport",
        sections=rapport("2. Travaux réalisés", "1. contexte"),
    )
    assert written["template"] == "rapport" and written["sections"] == 3
    path = disk / "sortie" / "r.docx"
    core = part(path, "docProps/core.xml")
    category = "{http://schemas.openxmlformats.org/package/2006/metadata/core-properties}category"
    assert core.findtext(category) == "rapport"
    body = part(path, "word/document.xml")
    # Les titres écrits par l'outil, dans l'ordre du modèle, sous le nom du modèle ; le
    # texte fixe à la fin ; chaque section entre ses signets.
    texts = ["".join(t.text or "" for t in p.iter(f"{W}t")) for p in body.iter(f"{W}p")]
    assert texts == ["Rapport", "Contexte", "Fait.", "Travaux réalisés", "Fait.", "Garantie",
                     "Un an, pièces et main-d'œuvre."]  # fmt: skip
    marks = [m.get(f"{W}name") for m in body.iter(f"{W}bookmarkStart")]
    assert marks == ["_loom_s1", "_loom_s3", "_loom_s4"]
    # Relu, le document rend son modèle et ses sections : une réécriture le refait.
    found = await call(modeled, action="read", path="sortie/r.docx")
    assert found["template"] == "rapport" and "notes" not in found and "blocks" not in found
    assert found["sections"] == rapport("Contexte", "Travaux réalisés")
    again = await call(
        modeled,
        path="sortie/r2.docx",
        template="rapport",
        title=found["title"],
        sections=found["sections"],
    )
    assert again["sha256"] == written["sha256"]


async def test_a_free_document_has_no_template(
    modeled: WordTool, tool: WordTool, disk: Path
) -> None:
    free = await call(modeled, path="sortie/libre.docx", template="none", **DEVIS)
    plain = await call(tool, path="sortie/devis.docx", **DEVIS)
    # Un document libre est le même fichier qu'un document écrit sans modèles.
    assert free["sha256"] == plain["sha256"] and free["template"] == "none"
    assert "template" not in plain and "sections" not in free
    found = await call(modeled, action="read", path="sortie/libre.docx")
    assert "template" not in found and "sections" not in found


async def test_reading_a_template_the_tool_does_not_know(
    modeled: WordTool, tool: WordTool, disk: Path
) -> None:
    other = make(
        disk,
        {"name": "sortie", "path": "sortie", "rights": ["read", "write", "overwrite"]},
        templates={"offre": {"text": "sections:\n  Besoin:\n"}},
    )
    await call(other, path="sortie/o.docx", template="offre", sections=rapport("Besoin"))
    found = await call(modeled, action="read", path="sortie/o.docx")
    assert found["template"] == "offre"
    assert (
        "Le document dit suivre le modèle « offre », que cet outil ne déclare pas"
        in found["notes"][0]
    )
    # Relu à plat : le titre de la section est un titre de niveau 1.
    assert found["blocks"][0] == {"type": "heading", "level": 1, "text": "Besoin"}
    # Un outil sans modèles ne dit rien des modèles.
    plain = await call(tool, action="read", path="sortie/o.docx")
    assert "template" not in plain and "notes" not in plain


async def test_a_document_whose_sections_are_lost(modeled: WordTool, disk: Path) -> None:
    """Un document qui dit suivre un modèle, sans les signets de ses sections (écrit avant
    2.1.0, ou dont on a coupé et recollé les sections) : relu en document libre."""
    await call(
        modeled,
        path="sortie/r.docx",
        template="rapport",
        sections=rapport("Contexte", "Travaux réalisés"),
    )
    path = disk / "sortie" / "r.docx"
    with zipfile.ZipFile(path) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    parts["word/document.xml"] = re.sub(
        rb"<w:bookmark(Start|End) [^>]*/>", b"", parts["word/document.xml"]
    )
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    found = await call(modeled, action="read", path="sortie/r.docx")
    assert "template" not in found and "sections" not in found
    assert "ses sections ne se retrouvent pas" in found["notes"][0]
    assert {"type": "heading", "level": 1, "text": "Contexte"} in found["blocks"]


def test_templates_need_a_folder_to_write(disk: Path) -> None:
    with pytest.raises(ValueError, match="aucun dossier n'a le droit write"):
        make(
            disk,
            {"name": "modeles", "path": "modeles", "rights": ["read"]},
            templates={"r": {"text": "sections:\n  A:\n"}},
        )
    with pytest.raises(ValueError, match=r"modèle r \(r\.yaml\) non lu"):
        make(
            disk,
            {"name": "sortie", "path": "sortie", "rights": ["write"]},
            templates={"r": "r.yaml"},
        )


def test_templates_are_read_when_the_config_loads(
    demo: ConfigFactory, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "sortie").mkdir()
    folders = [{"name": "sortie", "path": "sortie", "rights": ["read", "write"]}]
    path = demo(
        agents=[word_agent(folders=folders, templates={"rapport": "rapport.yaml"})], storage=JSONL
    )
    with pytest.raises(ConfigError, match=r"templates\[rapport\] — modèle introuvable"):
        load_config(path)
    prompts = tmp_path / "prompts"
    (prompts / "rapport.yaml").write_text(RAPPORT_YAML, encoding="utf-8")
    load_config(path)
    assert main(["--config", str(path), "validate"]) == 0
    assert (
        "blocs : aucun · modèles : rapport · lecture des documents : oui" in capsys.readouterr().out
    )
    for given, said in (
        ("../secret.yaml", "le fichier doit être dans le dossier des ressources"),
        ("prompts/rapport.yaml", "le chemin est relatif à resources_dir : écrire rapport.yaml"),
        ({"file": "rapport.yaml", "style": "s.yaml"}, "le modèle nomme son style dans son fichier"),
    ):
        path = demo(
            agents=[word_agent(folders=folders, templates={"rapport": given})], storage=JSONL
        )
        with pytest.raises(ConfigError, match=re.escape(said)):
            load_config(path)
    for written, said in (
        ("sections:\n  A:\n  a:\n", "section donnée deux fois"),
        ("---\ndescription: x\n---\n# A\n", "un modèle est un fichier YAML depuis 2.1.0"),
    ):
        (prompts / "casse.yaml").write_text(written, encoding="utf-8")
        path = demo(
            agents=[word_agent(folders=folders, templates={"rapport": "casse.yaml"})],
            storage=JSONL,
        )
        with pytest.raises(ConfigError, match=re.escape(said)):
            load_config(path)


# --- B-2 et B-3 ----------------------------------------------------------------------------


def test_a_shape_and_its_fallback_count_once() -> None:
    """B-2 : Word range une forme deux fois, en dessin (``mc:Choice``) et en VML de secours
    (``mc:Fallback``) ; la relecture ne la compte qu'une fois."""
    mc = 'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"'
    shape = (
        f'<w:r><mc:AlternateContent {mc}><mc:Choice Requires="wps"><w:drawing/></mc:Choice>'
        "<mc:Fallback><w:pict/></mc:Fallback></mc:AlternateContent></w:r>"
    )
    body = (
        f"<w:p>{shape}<w:r><w:t>Une zone de texte, puis une image.</w:t></w:r></w:p>"
        f"<w:p><w:r><w:drawing/></w:r></w:p><w:p>{shape}</w:p>"
    )
    found = reader_module.read(docx(body), "d.docx", 0)
    assert found["blocks"] == [{"type": "paragraph", "text": "Une zone de texte, puis une image."}]
    assert found["notes"] == ["3 image(s) ou forme(s) non lue(s)."]


async def test_windows_line_endings_become_line_breaks(tool: WordTool, disk: Path) -> None:
    """B-3 : un \\r\\n ou un \\r venu du modèle est un saut de ligne, pas un \\r dans le XML."""
    await call(
        tool,
        path="sortie/crlf.docx",
        title="Devis\r\nn° 12",
        blocks=[
            {"type": "paragraph", "text": "ligne 1\r\nligne 2\rligne 3"},
            {"type": "list", "items": ["a\r\nb"]},
            {
                "type": "table",
                "columns": [{"header": "Dési\r\ngnation"}, {"header": "Note"}],
                "rows": [["Pose\r\nfinition", "ok"]],
            },
        ],
    )
    with zipfile.ZipFile(disk / "sortie" / "crlf.docx") as archive:
        assert b"\r" not in archive.read("word/document.xml")
        assert b"\r" not in archive.read("docProps/core.xml")
    found = await call(tool, action="read", path="sortie/crlf.docx")
    assert found["blocks"][:3] == [
        {"type": "heading", "level": 0, "text": "Devis\nn° 12"},
        {"type": "paragraph", "text": "ligne 1\nligne 2\nligne 3"},
        {"type": "list", "ordered": False, "items": ["a\nb"]},
    ]
    assert found["blocks"][3]["rows"] == [["Dési\ngnation", "Note"], ["Pose\nfinition", "ok"]]


# --- Le texte enrichi (lot 5) -----------------------------------------------------------

RICH: Final[list[dict[str, Any]]] = [
    {"type": "heading", "text": "Objet"},
    {
        "type": "paragraph",
        "text": "Voir [le site](https://example.com/devis?a=1&b=2) et [**le guide**]"
        "(https://example.com/guide). Une note^[Par virement, voir [ici](mailto:a@b.fr) et "
        "§[Conditions].] et le détail en §[Travaux]. Tels quels : \\^[x] et \\§[y].",
    },
    {"type": "heading", "text": "Travaux"},
    {
        "type": "list",
        "items": [
            "Dépose",
            {
                "text": "Pose^[Certifiée.]",
                "items": [{"text": "receveur", "items": ["extra-plat", "*antidérapant*"]}, "paroi"],
            },
        ],
    },
    {
        "type": "list",
        "ordered": True,
        "items": [{"text": "un", "items": [{"text": "a", "items": ["i", "ii"]}]}, "deux"],
    },
    {"type": "fields", "items": [["Client", "M. **Dupont**"], ["Signature", ""]]},
    {"type": "code", "text": "def total(lignes):\n    return sum(lignes)  # HT\n\n\tfin"},
    {"type": "heading", "text": "Conditions", "level": 2},
    {"type": "paragraph", "text": "Voir [notre site](https://example.com/devis?a=1&b=2)."},
]


def rich_tool(disk: Path, **params: Any) -> WordTool:
    return make(
        disk,
        {"name": "modeles", "path": "modeles", "rights": ["read"]},
        {"name": "sortie", "path": "sortie", "rights": ["read", "write", "overwrite"]},
        blocks=["fields", "code"],
        **params,
    )


async def test_rich_text_is_written_read_back_and_rewritten(disk: Path) -> None:
    tool = rich_tool(disk)
    written = await call(tool, path="sortie/riche.docx", title="Devis", blocks=RICH)
    path = disk / "sortie" / "riche.docx"
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        document = archive.read("word/document.xml").decode()
        relations = archive.read("word/_rels/document.xml.rels").decode()
        notes = archive.read("word/footnotes.xml").decode()
        note_relations = archive.read("word/_rels/footnotes.xml.rels").decode()
        settings = archive.read("word/settings.xml").decode()
        types = archive.read("[Content_Types].xml").decode()
    assert "word/footnotes.xml" in names and "footnotes+xml" in types
    assert '<w:footnotePr><w:footnote w:id="-1"/><w:footnote w:id="0"/></w:footnotePr>' in settings
    # Un lien : une relation externe par adresse, la même pour ses deux emplois.
    assert relations.count('TargetMode="External"') == 2
    assert 'Target="https://example.com/devis?a=1&amp;b=2"' in relations
    assert document.count('<w:hyperlink r:id="rIdLink1"') == 2
    # Les notes : leur appel dans le corps, leur texte (et ses liens) à part.
    assert document.count("<w:footnoteReference") == 2
    assert 'w:id="2"' in notes and "Certifiée." in notes
    assert 'Target="mailto:a@b.fr"' in note_relations
    # Un renvoi sans numérotation : le titre entre guillemets, en lien vers son signet.
    assert '<w:bookmarkStart w:id="2002" w:name="_Ref_loom_2"/>' in document
    assert '<w:hyperlink w:anchor="_Ref_loom_2" w:history="1">' in document
    assert "\u00ab\u00a0Travaux\u00a0\u00bb" in document
    # Les champs : un tableau « loom fields » ; le code : un paragraphe, ses lignes en sauts.
    assert '<w:tblStyle w:val="LoomFields"/>' in document
    assert document.count('<w:pStyle w:val="LoomCode"/>') == 1
    found = await call(tool, action="read", path="sortie/riche.docx")
    blocks = [b for b in found["blocks"] if b.get("level") != 0]
    expected = [
        {**block, "ordered": block.get("ordered", False)} if block["type"] == "list" else block
        for block in RICH
    ]
    expected = [
        {"type": "heading", "level": block.get("level", 1), "text": block["text"]}
        if block["type"] == "heading"
        else block
        for block in expected
    ]
    assert blocks == expected
    again = await call(tool, path="sortie/riche2.docx", title="Devis", blocks=blocks)
    assert again["sha256"] == written["sha256"]


@pytest.mark.parametrize(
    ("numbering", "said"),
    [
        (
            "decimal",
            '<w:fldSimple w:instr=" REF _Ref_loom_3 \\n \\t \\h "><w:r>'
            '<w:t xml:space="preserve">2.1</w:t></w:r></w:fldSimple>',
        ),
        # Un article : son numéro en lien (LibreOffice récrirait le champ « Article 2 » et son
        # tiret).
        (
            "article",
            '<w:hyperlink w:anchor="_Ref_loom_2" w:history="1"><w:r>'
            '<w:t xml:space="preserve">2</w:t></w:r></w:hyperlink>',
        ),
    ],
)
async def test_a_reference_to_a_numbered_heading_is_its_number(
    disk: Path, numbering: str, said: str
) -> None:
    tool = rich_tool(disk, style={"spec": {"numbering": numbering}})
    target = "Détail" if numbering == "decimal" else "Travaux"
    await call(
        tool,
        path="sortie/n.docx",
        blocks=[
            {"type": "heading", "text": "Objet"},
            {"type": "paragraph", "text": f"Voir §[{target}]."},
            {"type": "heading", "text": "Travaux"},
            {"type": "heading", "text": "Détail", "level": 2},
        ],
    )
    with zipfile.ZipFile(disk / "sortie" / "n.docx") as archive:
        assert said in archive.read("word/document.xml").decode()
    found = await call(tool, action="read", path="sortie/n.docx")
    assert {"type": "paragraph", "text": f"Voir §[{target}]."} in found["blocks"]


async def test_a_document_without_rich_text_has_no_footnotes(disk: Path) -> None:
    await call(rich_tool(disk), path="sortie/a.docx", **DEVIS)
    with zipfile.ZipFile(disk / "sortie" / "a.docx") as archive:
        assert "word/footnotes.xml" not in archive.namelist()
        assert "footnotePr" not in archive.read("word/settings.xml").decode()
        assert "hyperlink" not in archive.read("word/_rels/document.xml.rels").decode()


async def test_reads_links_notes_and_references_from_elsewhere(disk: Path) -> None:
    """Un document d'ailleurs : un renvoi en champ complexe (celui qu'écrit Word) ou en lien
    interne, vers un signet de titre ; une note de fin ; un lien hors des schémas permis
    revient en texte ; un renvoi vers un signet qui n'est pas un titre, son texte."""
    hyperlink = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"
    rels = "http://schemas.openxmlformats.org/package/2006/relationships"
    r = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    styles = '<w:style w:type="paragraph" w:styleId="T1"><w:name w:val="heading 1"/></w:style>'
    body = (
        '<w:p><w:pPr><w:pStyle w:val="T1"/></w:pPr><w:bookmarkStart w:id="1" w:name="_Ref1"/>'
        '<w:r><w:t>Garantie</w:t></w:r><w:bookmarkEnd w:id="1"/></w:p>'
        '<w:p><w:r><w:t xml:space="preserve">Voir </w:t></w:r>'
        '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
        '<w:r><w:instrText xml:space="preserve"> REF _Ref1 \\r \\h </w:instrText></w:r>'
        '<w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>1</w:t></w:r>'
        '<w:r><w:fldChar w:fldCharType="end"/></w:r>'
        '<w:r><w:t xml:space="preserve">, </w:t></w:r>'
        '<w:hyperlink w:anchor="_Ref1"><w:r><w:t>ici</w:t></w:r></w:hyperlink>'
        '<w:r><w:t xml:space="preserve">, </w:t></w:r>'
        '<w:hyperlink w:anchor="_Autre"><w:r><w:t>là</w:t></w:r></w:hyperlink>'
        '<w:r><w:t xml:space="preserve">, </w:t></w:r>'
        '<w:hyperlink r:id="rL1"><w:r><w:t>le site</w:t></w:r></w:hyperlink>'
        '<w:r><w:t xml:space="preserve">, </w:t></w:r>'
        '<w:hyperlink r:id="rL2"><w:r><w:t>le fichier</w:t></w:r></w:hyperlink>'
        '<w:r><w:endnoteReference w:id="1"/></w:r><w:r><w:t>.</w:t></w:r>'
        '<w:bookmarkStart w:id="2" w:name="_Autre"/><w:bookmarkEnd w:id="2"/></w:p>'
    )
    data = docx(
        body.replace("<w:hyperlink r:id", f"<w:hyperlink {r} r:id"),
        styles=styles,
        extra={
            "word/endnotes.xml": f"<w:endnotes {NS}>"
            '<w:endnote w:type="separator" w:id="0"><w:p><w:r><w:separator/></w:r></w:p>'
            '</w:endnote><w:endnote w:id="1"><w:p><w:r><w:endnoteRef/></w:r>'
            '<w:r><w:t xml:space="preserve"> Une note de fin.</w:t></w:r></w:p></w:endnote>'
            "</w:endnotes>",
        },
    )
    # Les relations du corps : ses styles, ses notes de fin, ses deux liens.
    data = _replace(
        data,
        "word/_rels/document.xml.rels",
        f'<Relationships xmlns="{rels}">'
        '<Relationship Id="rS" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/styles" Target="styles.xml"/>'
        '<Relationship Id="rE" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/endnotes" Target="endnotes.xml"/>'
        f'<Relationship Id="rL1" Type="{hyperlink}" Target="https://a.fr" '
        'TargetMode="External"/>'
        f'<Relationship Id="rL2" Type="{hyperlink}" Target="file:///C:/devis.pdf" '
        'TargetMode="External"/></Relationships>',
    )
    (disk / "modeles" / "ailleurs.docx").write_bytes(data)
    found = await call(rich_tool(disk), action="read", path="modeles/ailleurs.docx")
    assert found["blocks"][1] == {
        "type": "paragraph",
        "text": "Voir §[Garantie], §[Garantie], là, [le site](https://a.fr), le fichier"
        "^[Une note de fin.].",
    }


async def test_table_emphasis_reads_back_except_header_and_total(disk: Path) -> None:
    tool = rich_tool(disk)
    table = {
        "type": "table",
        "columns": [{"header": "Désignation"}, {"header": "Montant", "type": "amount_eur"}],
        "rows": [["**Pose** de la [paroi](https://a.fr)^[Verre trempé.]", 10]],
        "totals": {"Montant": "sum"},
    }
    await call(tool, path="sortie/t.docx", blocks=[table])
    found = await call(tool, action="read", path="sortie/t.docx")
    assert found["blocks"][0]["rows"] == [
        ["Désignation", "Montant"],
        ["**Pose** de la [paroi](https://a.fr)^[Verre trempé.]", "10,00\u00a0€"],
        ["Total", "10,00\u00a0€"],
    ]


async def test_libreoffice_shows_notes_and_references(disk: Path, soffice: str) -> None:
    tool = rich_tool(disk, style={"spec": {"numbering": "decimal"}})
    await call(tool, path="sortie/riche.docx", title="Devis", blocks=RICH)
    out = disk / "converti"
    convert(soffice, disk / "sortie" / "riche.docx", out, "txt:Text (encoded):UTF8")
    text = (out / "riche.txt").read_text(encoding="utf-8-sig")
    # Le renvoi recalculé par LibreOffice : « 2 » ; l'appel de la note, son numéro.
    assert "Une note1 et le détail en 2." in text and "2.1 Conditions" in text
