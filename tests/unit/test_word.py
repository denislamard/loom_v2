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
import subprocess
import zipfile
from pathlib import Path
from typing import Any, Final
from xml.etree import ElementTree

import pytest
from conftest import MODEL, ConfigFactory, demo_agent

from loom_ia.access.cli import main
from loom_ia.adapters.stores import InMemoryEventStore
from loom_ia.config import ConfigError, load_config
from loom_ia.core.events import ToolCompleted
from loom_ia.core.model import DEFAULT_TENANT, RunId, RunStatus, SessionId
from loom_ia.core.ports import ToolContext, ToolError
from loom_ia.engine import begin_run, drive
from loom_ia.runtime import build_agent
from loom_ia.tools_builtin import BuiltinContext
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


def make(tmp_path: Path, *folders: dict[str, Any]) -> WordTool:
    model = WordParams.model_validate({"folders": list(folders)})
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
        {"type": "paragraphe", "text": "Madame **Dupont**,\nvoici notre *proposition*."},
        {"type": "titre", "text": "Travaux"},
        {"type": "liste", "items": ["Dépose", {"text": "Pose", "items": ["receveur", "paroi"]}]},
        {"type": "liste", "items": ["Signature", "Acompte"], "ordered": True},
        {
            "type": "tableau",
            "columns": [
                {"header": "Désignation"},
                {"header": "Montant HT", "type": "montant_eur"},
            ],
            "rows": [["Dépose", 180], ["Pose", 1250.5]],
            "totals": {"Montant HT": "somme"},
            "caption": "Montants hors taxes.",
        },
        {"type": "note", "title": "Validité", "text": "3 mois."},
        {"type": "citation", "text": "Travail soigné.", "source": "M. Martin"},
        {"type": "saut_de_page"},
        {"type": "titre", "text": "Conditions", "level": 2},
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
        "titre (text, level 1 à 3)",
        "montant_eur",
        "**gras**",
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
    assert settings.options == (("fuseau", "Europe/Paris"), ("lecture des documents", "oui"))


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
        "Caption", "Note", "Quote", "QuoteSource", "", "Heading2",
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
        "écrire sortie/a.docx : approbation demandée pour overwrite sur sortie/a.docx"
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
    assert blocks[0] == {"type": "titre", "level": 0, "text": "Devis D-2026-042"}
    assert blocks[1] == {
        "type": "paragraphe",
        "text": "Madame **Dupont**,\nvoici notre *proposition*.",
    }
    assert blocks[2] == {"type": "titre", "level": 1, "text": "Travaux"}
    assert blocks[3] == {
        "type": "liste",
        "ordered": False,
        "items": ["Dépose", {"text": "Pose", "items": ["receveur", "paroi"]}],
    }
    assert blocks[4] == {"type": "liste", "ordered": True, "items": ["Signature", "Acompte"]}
    assert blocks[5]["type"] == "tableau"
    assert blocks[5]["rows"][-1] == ["Total", "1\u00a0430,50\u00a0€"]
    assert {"type": "note", "text": "**Validité**\n3 mois."} in blocks
    assert {"type": "citation", "text": "Travail soigné."} in blocks
    assert blocks[-1] == {"type": "titre", "level": 2, "text": "Conditions"}


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
        {"type": "titre", "level": 1, "text": "Chapitre"},
        {"type": "titre", "level": 1, "text": "Section héritée"},
        {"type": "titre", "level": 3, "text": "Niveau direct"},
        {"type": "liste", "ordered": False, "items": ["Point par style"]},
        {"type": "paragraphe", "text": "gardé inséré pas gras lien arrivé"},
        {"type": "paragraphe", "text": "Dans un contrôle"},
        {"type": "tableau", "rows": [["Fusion", ""], ["a", "b"], ["c", ""]]},
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
    assert {"type": "titre", "level": 1, "text": "Rapport"} in blocks
    assert {"type": "titre", "level": 2, "text": "Points"} in blocks
    assert {"type": "paragraphe", "text": "Texte **gras** et *penché*."} in blocks
    assert {"type": "liste", "ordered": True, "items": ["un", "deux"]} in blocks
    assert {"type": "tableau", "rows": [["Nom", "Total"], ["A", "3"]]} in blocks


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
    assert "fuseau : Europe/Paris · lecture des documents : oui" in out


async def test_overwrite_needs_its_right(disk: Path) -> None:
    only = make(disk, {"name": "sortie", "path": "sortie", "rights": ["write"]})
    await refused(only, "Droit overwrite refusé", path="sortie/a.docx", overwrite=True, **DEVIS)
