# SPDX-License-Identifier: Apache-2.0
"""Outil fourni ``loom.pdf`` : écrire un PDF dans le style maison, lire le texte d'un PDF.

Les PDF écrits sont relus avec pypdf. La lecture passe par le process borné
(``worker``) : ses bornes sont éprouvées par de faux process (qui dorment, qui
meurent) et par une bombe de décompression.
"""

import asyncio
import io
import json
import re
import struct
import subprocess
import sys
import time
import zlib
from pathlib import Path
from typing import Any, Final, cast
from zoneinfo import ZoneInfo

import pytest

pytest.importorskip("reportlab", reason="extra 'pdf' absent")
pytest.importorskip("pypdf", reason="extra 'pdf' absent")

from conftest import ConfigFactory, demo_agent
from image_files import png
from pypdf import PdfReader, PdfWriter
from reportlab.lib.units import cm
from reportlab.pdfgen.canvas import Canvas
from test_word_styles import (
    CHARTE,
    FACTURE_SECTIONS,
    FACTURE_YAML,
    RAPPORT_SECTIONS,
    RAPPORT_YAML,
)

import loom_ia.tools_builtin.pdf.reader as reader_module
from loom_ia.access.cli import main
from loom_ia.adapters.stores import InMemoryEventStore
from loom_ia.agents.spec import PythonTool, builtin_settings
from loom_ia.config import ConfigError, load_config
from loom_ia.core.bounded_regex import bounded_validator
from loom_ia.core.model import DEFAULT_TENANT, RunId, SessionId
from loom_ia.core.ports import ToolContext, ToolError
from loom_ia.runtime import build_agent
from loom_ia.tools_builtin import BuiltinContext
from loom_ia.tools_builtin._style_params import SAFE_FONTS, font_fault, house
from loom_ia.tools_builtin.pdf import PdfTool, build
from loom_ia.tools_builtin.pdf import tool as tool_module
from loom_ia.tools_builtin.pdf import writer as writer_module
from loom_ia.tools_builtin.pdf_params import PdfParams

CONTEXT: Final = ToolContext(
    tenant_id=DEFAULT_TENANT,
    session_id=SessionId("s"),
    run_id=RunId("r"),
    call_id="c1",
    agent="demo",
)
JSONL: Final = {"events": {"backend": "jsonl", "path": "journal"}}


def make(tmp_path: Path, *folders: dict[str, Any], **params: Any) -> PdfTool:
    model = PdfParams.model_validate({"folders": list(folders), **params})
    return build(model, BuiltinContext(base_dir=tmp_path))


@pytest.fixture
def disk(tmp_path: Path) -> Path:
    for name in ("recus", "sortie"):
        (tmp_path / name).mkdir()
    return tmp_path


@pytest.fixture
def tool(disk: Path) -> PdfTool:
    return make(
        disk,
        {"name": "recus", "path": "recus", "rights": ["read"]},
        {"name": "sortie", "path": "sortie", "rights": ["read", "write", "overwrite"]},
    )


async def call(tool: PdfTool, **arguments: Any) -> dict[str, Any]:
    output = await tool.invoke(dict(arguments), CONTEXT)
    assert isinstance(output.data, dict)
    return output.data


async def refused(tool: PdfTool, match: str, **arguments: Any) -> None:
    with pytest.raises(ToolError, match=match):
        await tool.invoke(dict(arguments), CONTEXT)


DEVIS: Final[dict[str, Any]] = {
    "title": "Devis D-2026-042",
    "blocks": [
        {"type": "paragraph", "text": "Madame **Dupont**,\nvoici notre *proposition*."},
        {"type": "heading", "text": "Travaux"},
        {"type": "list", "items": ["Dépose", {"text": "Pose", "items": ["receveur"]}]},
        {"type": "list", "items": ["Signature", "Acompte"], "ordered": True},
        {
            "type": "table",
            "columns": [{"header": "Désignation"}, {"header": "Montant HT", "type": "amount_eur"}],
            "rows": [["Dépose", 180], ["Pose", 1250.5]],
            "totals": {"Montant HT": "sum"},
            "caption": "Montants hors taxes.",
        },
        {"type": "note", "title": "Validité", "text": "3 mois."},
        {"type": "quote", "text": "Travail soigné.", "source": "M. Martin"},
        {"type": "heading", "text": "Conditions", "level": 2},
    ],
}


def pdf_of(*pages: str, form: dict[str, str] | None = None) -> bytes:
    """Un PDF d'essai : une page par texte (vide : une page sans texte), et un formulaire."""
    raw = io.BytesIO()
    canvas = Canvas(raw, invariant=1)
    for text in pages:
        if text:
            for number, line in enumerate(text.splitlines()):
                canvas.drawString(72, 760 - 14 * number, line)
        else:
            canvas.rect(72, 600, 200, 100, fill=1)
        for name, value in (form or {}).items():
            form_maker: Any = canvas.acroForm
            form_maker.textfield(name=name, value=value, x=72, y=400, width=200, height=20)
        form = None
        canvas.showPage()
    canvas.save()
    return raw.getvalue()


# --- Ce que voit le modèle ------------------------------------------------------------


def test_the_spec_names_the_folders_and_the_reading(tool: PdfTool) -> None:
    spec = tool.spec
    assert spec.name == "pdf"
    for said in ("- recus : read", "Polices du PDF", "pages", "pas d'OCR", "next_page"):
        assert said in spec.description, said
    settings = tool.settings()
    assert settings.options == (
        ("fuseau", "Europe/Paris"),
        ("blocs", "aucun"),
        ("modèles", "aucun"),
        ("lecture des PDF", "oui, process à part (20 s, 1 Gio)"),
    )


def _old_version(_: str) -> str:
    return "6.14.1"


def test_an_old_pypdf_is_refused_at_mount(disk: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tool_module, "version", _old_version)
    with pytest.raises(ValueError, match=r"pypdf 6\.14\.1 : 6\.14\.2 ou plus attendu"):
        make(disk, {"name": "recus", "path": "recus", "rights": ["read"]})
    # Sans dossier read, pypdf ne sert pas : rien à vérifier.
    make(disk, {"name": "sortie", "path": "sortie", "rights": ["write"]})


# --- Écriture -------------------------------------------------------------------------


async def test_writes_a_pdf_in_the_house_style(tool: PdfTool, disk: Path) -> None:
    result = await call(tool, path="sortie/devis.pdf", **DEVIS)
    assert (result["written"], result["pages"], result["warnings"]) == (True, 1, [])
    found = PdfReader(disk / "sortie" / "devis.pdf")
    assert len(found.pages) == 1
    assert found.metadata is not None and found.metadata.title == "Devis D-2026-042"
    first = found.pages[0].extract_text()
    for said in ("Page 1 / 1", "Devis D-2026-042", "Dupont", "1 430,50 €", "Total", "Conditions"):
        assert said in first.replace("\u00a0", " "), said


async def test_same_description_same_file(tool: PdfTool) -> None:
    first = await call(tool, path="sortie/a.pdf", **DEVIS)
    second = await call(tool, path="sortie/b.pdf", **DEVIS)
    assert first["sha256"] == second["sha256"]
    assert (await call(tool, path="sortie/a.pdf", **DEVIS))["written"] is False


def test_the_same_file_from_another_process(tool: PdfTool, disk: Path) -> None:
    """Ni l'heure ni le process n'entrent dans le fichier : un autre interpréteur, mêmes octets."""
    code = (
        "import asyncio, sys\n"
        "from pathlib import Path\n"
        "from loom_ia.core.model import DEFAULT_TENANT, RunId, SessionId\n"
        "from loom_ia.core.ports import ToolContext\n"
        "from loom_ia.tools_builtin import BuiltinContext\n"
        "from loom_ia.tools_builtin.pdf import build\n"
        "from loom_ia.tools_builtin.pdf_params import PdfParams\n"
        "import json\n"
        "args = json.loads(sys.argv[2])\n"
        "tool = build(PdfParams.model_validate({'folders': [{'name': 'sortie', 'path': "
        "sys.argv[1], 'rights': ['write']}]}), BuiltinContext())\n"
        "ctx = ToolContext(tenant_id=DEFAULT_TENANT, session_id=SessionId('s'), "
        "run_id=RunId('r'), call_id='c', agent='a')\n"
        "out = asyncio.run(tool.invoke({'path': 'sortie/x.pdf', **args}, ctx))\n"
        "print(out.data['sha256'])\n"
    )
    import json

    folder = disk / "autre"
    folder.mkdir()
    done = subprocess.run(
        [sys.executable, "-c", code, str(folder), json.dumps(DEVIS)],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    here = asyncio.run(call(tool, path="sortie/x.pdf", **DEVIS))
    assert done.stdout.strip() == here["sha256"]


async def test_characters_outside_the_fonts_are_said(tool: PdfTool) -> None:
    result = await call(
        tool,
        path="sortie/a.pdf",
        blocks=[{"type": "paragraph", "text": "Merci 🙂 — 中文 ≥ \u03b1 €"}],
    )
    [warning] = result["warnings"]
    assert warning.startswith("3 caractère(s) absent(s) des polices du PDF")
    for char in ("🙂", "中", "文"):
        assert char in warning
    for char in ("≥", "\u03b1", "€"):
        assert char not in warning


async def test_paths_and_rights(tool: PdfTool, disk: Path) -> None:
    await refused(tool, r"s'écrit en \.pdf", path="sortie/a.docx", **DEVIS)
    await refused(tool, "Droit write refusé", path="recus/a.pdf", **DEVIS)
    await refused(tool, r"seuls les fichiers \.pdf", action="read", path="recus/a.txt")
    assess = tool.assess_call({"path": "sortie/a.pdf", "overwrite": True, **DEVIS})
    assert (assess.approval, assess.side_effects) == ("always", "irreversible")


# --- Lecture --------------------------------------------------------------------------


async def test_what_it_writes_reads_back(tool: PdfTool) -> None:
    await call(tool, path="sortie/devis.pdf", **DEVIS)
    found = await call(tool, action="read", path="sortie/devis.pdf")
    assert found["page_count"] == 1 and found["truncated"] is False
    assert found["metadata"]["title"] == "Devis D-2026-042"
    assert [page["number"] for page in found["pages"]] == [1]
    assert "Madame Dupont" in found["pages"][0]["text"]
    assert "notes" not in found


async def test_underline_is_drawn_not_written(tool: PdfTool) -> None:
    from reportlab.lib.styles import ParagraphStyle

    from loom_ia.tools_builtin._document.content import Prepared, inline
    from loom_ia.tools_builtin.pdf.writer import _Body  # pyright: ignore[reportPrivateUsage]

    body = _Body(Prepared(None, (), ()), house(), None, {})
    look = house().look("paragraph")
    said = body.text(inline("un <u>mot</u> et **<u>deux</u>**"), ParagraphStyle("p"), look)
    assert said == "un <u>mot</u> et <b><u>deux</u></b>"
    blocks = [{"type": "paragraph", "text": "Le dépôt sera fermé le <u>vendredi 13</u>."}]
    await call(tool, path="sortie/u.pdf", title="Note", blocks=blocks)
    found = await call(tool, action="read", path="sortie/u.pdf")
    text = found["pages"][0]["text"]
    assert "vendredi 13" in text and "<u>" not in text


@pytest.mark.parametrize(
    ("pages", "numbers"),
    [("2", [2]), ("2-3", [2, 3]), ("3-", [3, 4]), (" 1 - 2 ", [1, 2]), ("4-9", [4])],
)
async def test_pages_choose_what_is_read(
    tool: PdfTool, disk: Path, pages: str, numbers: list[int]
) -> None:
    (disk / "recus" / "quatre.pdf").write_bytes(pdf_of("un", "deux", "trois", "quatre"))
    found = await call(tool, action="read", path="recus/quatre.pdf", pages=pages)
    assert [page["number"] for page in found["pages"]] == numbers
    assert found["page_count"] == 4


@pytest.mark.parametrize(
    ("pages", "said"),
    [("a", "écrire 3, 2-5 ou 4-"), ("0", "commencent à 1"), ("3-2", "dans l'ordre"),
     ("9", "page 9 demandée, le PDF en a 4")],
)  # fmt: skip
async def test_pages_refused(tool: PdfTool, disk: Path, pages: str, said: str) -> None:
    (disk / "recus" / "quatre.pdf").write_bytes(pdf_of("un", "deux", "trois", "quatre"))
    await refused(tool, said, action="read", path="recus/quatre.pdf", pages=pages)


async def test_a_long_pdf_is_read_in_parts(tool: PdfTool, disk: Path) -> None:
    page = "\n".join("ligne de texte assez longue pour remplir la page " * 2 for _ in range(45))
    (disk / "recus" / "long.pdf").write_bytes(pdf_of(*[page] * 20))
    first = await call(tool, action="read", path="recus/long.pdf")
    assert first["truncated"] is True
    next_page = first["next_page"]
    assert 1 < next_page <= 20 and len(first["pages"]) == next_page - 1
    assert f"pages={next_page}-" in " ".join(first["notes"])
    rest = await call(tool, action="read", path="recus/long.pdf", pages=f"{next_page}-")
    assert rest["pages"][0]["number"] == next_page


async def test_a_page_without_text_is_said(tool: PdfTool, disk: Path) -> None:
    (disk / "recus" / "scan.pdf").write_bytes(pdf_of("text", ""))
    found = await call(tool, action="read", path="recus/scan.pdf")
    assert found["pages"][1] == {"number": 2, "text": ""}
    assert "Page(s) sans texte : 2" in found["notes"][0]


async def test_form_fields_are_read(tool: PdfTool, disk: Path) -> None:
    (disk / "recus" / "cerfa.pdf").write_bytes(
        pdf_of("Formulaire", "suite", form={"nom": "Dupont"})
    )
    found = await call(tool, action="read", path="recus/cerfa.pdf")
    assert found["fields"] == {"nom": "Dupont"}
    # Les champs ne reviennent qu'avec la première page.
    assert "fields" not in await call(tool, action="read", path="recus/cerfa.pdf", pages="2")


async def test_encrypted_pdfs(tool: PdfTool, disk: Path) -> None:
    for name, user in (("ferme.pdf", "secret"), ("ouvert.pdf", "")):
        writer = PdfWriter(clone_from=PdfReader(io.BytesIO(pdf_of("confidentiel"))))
        writer.encrypt(user_password=user, owner_password="proprietaire", algorithm="RC4-128")
        raw = io.BytesIO()
        writer.write(raw)
        (disk / "recus" / name).write_bytes(raw.getvalue())
    await refused(tool, "protégé par mot de passe", action="read", path="recus/ferme.pdf")
    found = await call(tool, action="read", path="recus/ouvert.pdf")
    assert "confidentiel" in found["pages"][0]["text"]
    assert any("chiffré" in note for note in found["notes"])


# --- Fichiers hostiles, process borné ---------------------------------------------------


async def test_not_a_pdf(tool: PdfTool, disk: Path) -> None:
    (disk / "recus" / "faux.pdf").write_bytes(b"PK\x03\x04 pas un pdf")
    await refused(tool, "en-tête %PDF- absent", action="read", path="recus/faux.pdf")
    (disk / "recus" / "casse.pdf").write_bytes(b"%PDF-1.7\n rien de plus")
    await refused(tool, "PDF illisible|PDF sans page", action="read", path="recus/casse.pdf")


def bomb(size: int) -> bytes:
    """Une page dont le contenu fait ``size`` octets décompressés, pour 1 000 fois moins."""
    packer = zlib.compressobj(9)
    stream = b"".join(packer.compress(b"0" * (1 << 20)) for _ in range(size >> 20))
    stream += packer.flush()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.7\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    table = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer << /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        table,
    )
    return bytes(out)


async def test_a_decompression_bomb_is_refused(tool: PdfTool, disk: Path) -> None:
    (disk / "recus" / "bombe.pdf").write_bytes(bomb(1 << 30))
    started = time.monotonic()
    await refused(tool, "bombe de décompression", action="read", path="recus/bombe.pdf")
    assert time.monotonic() - started < 10


def fake_worker(tmp_path: Path, code: str) -> Path:
    script = tmp_path / "faux_worker.py"
    script.write_text(code)
    return script


async def test_a_worker_that_hangs_is_killed(
    tool: PdfTool, disk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(reader_module, "WORKER", fake_worker(disk, "import time\ntime.sleep(60)\n"))
    monkeypatch.setattr(reader_module, "READ_DEADLINE", 0.5)
    (disk / "recus" / "a.pdf").write_bytes(pdf_of("un"))
    started = time.monotonic()
    await refused(tool, r"lecture arrêtée après 0\.5 s", action="read", path="recus/a.pdf")
    assert time.monotonic() - started < 5


async def test_a_worker_killed_by_its_bounds(
    tool: PdfTool, disk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code = "import os, signal\nos.kill(os.getpid(), signal.SIGKILL)\n"
    monkeypatch.setattr(reader_module, "WORKER", fake_worker(disk, code))
    (disk / "recus" / "a.pdf").write_bytes(pdf_of("un"))
    await refused(tool, "arrêtée par ses bornes", action="read", path="recus/a.pdf")


async def test_a_cancelled_read_kills_its_worker(
    tool: PdfTool, disk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le délai de l'outil annule l'appel : le process ne lui survit pas."""
    pid_file = disk / "pid"
    code = (
        f"import os, time\nopen({str(pid_file)!r}, 'w').write(str(os.getpid()))\ntime.sleep(60)\n"
    )
    monkeypatch.setattr(reader_module, "WORKER", fake_worker(disk, code))
    (disk / "recus" / "a.pdf").write_bytes(pdf_of("un"))
    task = asyncio.create_task(tool.invoke({"action": "read", "path": "recus/a.pdf"}, CONTEXT))
    pid = await asyncio.to_thread(_wait_for_pid, pid_file)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not await asyncio.to_thread(_alive, pid)


def _wait_for_pid(pid_file: Path) -> int:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if pid_file.exists() and pid_file.read_text():
            return int(pid_file.read_text())
        time.sleep(0.05)
    raise AssertionError("le faux process n'a pas démarré")


def _alive(pid: int) -> bool:
    """Vrai si le process tourne encore (un zombie compte pour mort)."""
    stat = Path(f"/proc/{pid}/stat")
    return stat.exists() and stat.read_text().split()[2] != "Z"


def test_the_worker_bounds_its_own_memory() -> None:
    worker = reader_module.WORKER
    code = (
        f"import sys; sys.path.insert(0, {str(worker.parent)!r}); import worker\n"
        "worker._limit(256 << 20, 5)\n"
        "try:\n    bytearray(1 << 30)\nexcept MemoryError:\n    print('borne')\n"
    )
    done = subprocess.run(
        [sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=30, check=False
    )
    assert done.stdout.strip() == "borne"


# --- Montage par la config, validate ----------------------------------------------------


def pdf_agent(**reference: Any) -> dict[str, Any]:
    return demo_agent(tools=[{"python": "loom.pdf", **reference}])


@pytest.mark.parametrize(
    ("given", "said"),
    [
        ({"charts": True}, r"charts → blocks: \[chart\]"),
        ({"images": True}, r"images → blocks: \[image\]"),
    ],
)
def test_pdf_blocks_when_the_config_loads(
    demo: ConfigFactory, given: dict[str, Any], said: str
) -> None:
    folders = [{"name": "s", "path": "/tmp", "rights": ["write"]}]
    with pytest.raises(ConfigError, match=said):
        load_config(demo(agents=[pdf_agent(folders=folders, **given)]))


def test_a_missing_extra_is_said_at_mount(
    demo: ConfigFactory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "sortie").mkdir()
    package = "loom_ia.tools_builtin.pdf"
    for name in [n for n in sys.modules if n == package or n.startswith(f"{package}.")]:
        monkeypatch.delitem(sys.modules, name)
    # Les sous-modules retirés : un ``from reportlab.lib import …`` les trouverait déjà là.
    for name in [n for n in sys.modules if n.startswith("reportlab.")]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.setitem(sys.modules, "reportlab", None)
    folders = [{"name": "sortie", "path": "sortie", "rights": ["write"]}]
    path = demo(agents=[pdf_agent(folders=folders)], storage=JSONL)
    with pytest.raises(
        ConfigError,
        match=r"Outil fourni 'loom.pdf' : dépendance 'reportlab(\.lib)?' absente "
        r"\(installer l'extra : loom-ia\[pdf\]\)",
    ):
        build_agent(load_config(path), "demo", InMemoryEventStore())


def test_validate_shows_the_folders(
    demo: ConfigFactory, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "recus").mkdir()
    folders = [{"name": "recus", "path": "recus", "rights": ["read"]}]
    path = demo(agents=[pdf_agent(folders=folders)], storage=JSONL)
    assert main(["--config", str(path), "validate"]) == 0
    out = capsys.readouterr().out
    assert "loom.pdf" in out
    assert "lecture des PDF : oui, process à part (20 s, 1 Gio)" in out


# --- Le process de lecture, appelé en direct (sa couverture) -----------------------------


def _unbounded(memory: int, seconds: int) -> None:
    return None


@pytest.fixture
def worker(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Le module du process, importé ici ; ses bornes ne s'appliquent pas aux essais."""
    from loom_ia.tools_builtin.pdf import worker as module

    monkeypatch.setattr(module, "_limit", _unbounded)
    return module


def test_the_worker_reads_pages_within_its_budget(worker: Any) -> None:
    found = worker.read(pdf_of("un", "deux", "", "quatre"), 2, None, 40_000)
    assert [page["number"] for page in found["pages"]] == [2, 3, 4]
    assert (found["page_count"], found["empty_pages"], found["fields"]) == (4, [3], None)
    tight = worker.read(pdf_of("un " * 300, "deux " * 300), 1, None, 2_000)
    assert tight["next_page"] == 2 and tight["cut"] is False
    cut = worker.read(pdf_of("mot " * 2_000), 1, 1, 500)
    assert cut["cut"] is True and cut["pages"][0]["text"].endswith("…")


@pytest.mark.parametrize(
    ("data", "said"),
    [
        (b"%PDF-1.7\n", "PDF illisible"),
        (bomb(1 << 30), "bombe de décompression"),
    ],
)
def test_the_worker_says_what_it_refuses(worker: Any, data: bytes, said: str) -> None:
    assert said in worker.read(data, 1, None, 40_000)["error"]


def test_the_worker_main_writes_json(
    worker: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import json

    request = {"first": 1, "last": None, "budget": 40_000, "memory": 1, "seconds": 1}
    monkeypatch.setattr(sys, "argv", ["worker.py", json.dumps(request)])
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(pdf_of("bonjour"))))
    assert worker.main() == 0
    assert json.loads(capsys.readouterr().out)["pages"][0]["text"] == "bonjour"

    def explode(*_: Any) -> None:
        raise MemoryError

    monkeypatch.setattr(worker, "read", explode)
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"")))
    worker.main()
    assert "mémoire épuisée" in json.loads(capsys.readouterr().out)["error"]


TOP10: Final[dict[str, Any]] = {
    "type": "table",
    "columns": [
        {"header": "Rang", "type": "integer"},
        {"header": "Client"},
        {"header": "Code"},
        {"header": "Factures", "type": "integer"},
        {"header": "Total HT", "type": "amount_eur"},
        {"header": "Total TTC", "type": "amount_eur"},
    ],
    "rows": [
        [1, "Julien Barre", "C-429", 2, 17094.0, 18034.17],
        [2, "Chauffage Lemaire Rénovation", "C-851", 2, 13539.47, 16247.36],
        [10, "Maxime Hubert", "C-143", 1, 10095.29, 10650.53],
    ],
    "totals": {"Factures": "sum", "Total HT": "sum", "Total TTC": "sum"},
}


def _with_bars(table: dict[str, Any]) -> dict[str, Any]:
    """Le tableau, plus une colonne de barres en caractères, large : celle qu'a ajoutée Marie."""
    rows = [[*row, "#" * round(30 * row[5] / 18034.17)] for row in table["rows"]]
    return {**table, "columns": [*table["columns"], {"header": "Graphique (TTC)"}], "rows": rows}


@pytest.mark.parametrize("table", [TOP10, _with_bars(TOP10)], ids=["six", "avec-barres"])
async def test_header_words_and_short_values_are_never_cut(
    tool: PdfTool, disk: Path, table: dict[str, Any]
) -> None:
    """Essais réels du 29/10 : « Ran / g », puis, à côté d'une large colonne, « Cod / e »
    et « C-42 / 9 ». Un mot d'en-tête, ou une valeur courte, tient dans sa colonne."""
    from reportlab.pdfbase.pdfmetrics import stringWidth

    from loom_ia.tools_builtin._document.arguments import WriteArguments
    from loom_ia.tools_builtin._document.content import SHORT_VALUE_CHARS, TableOut, prepare
    from loom_ia.tools_builtin.pdf.writer import CELL_PADDING, TWIP

    request = WriteArguments.model_validate({"path": "s/t.pdf", "title": None, "blocks": [table]})
    [found] = prepare(request, ZoneInfo("Europe/Paris")).blocks
    assert isinstance(found, TableOut)
    # Le style maison en PDF : Calibri rendu en Helvetica, l'en-tête en gras.
    size = house().look("table").size or 0
    text_width = house().text_width * TWIP
    for index, (header, part) in enumerate(zip(found.headers, found.widths, strict=True)):
        width = part * text_width - 2 * CELL_PADDING[1]
        for word in header.split():
            assert stringWidth(word, "Helvetica-Bold", size) <= width, word
        for row in [*found.rows, found.total or ()]:
            value = row[index] if row else ""
            if len(value) <= SHORT_VALUE_CHARS:
                assert stringWidth(value, "Helvetica", size) <= width, value
    await call(tool, path="sortie/top.pdf", blocks=[table], overwrite=True)
    text = PdfReader(disk / "sortie" / "top.pdf").pages[0].extract_text()
    for said in ("Rang", "Code", "Factures", "C-429", "18\u00a0034,17\u00a0€"):
        assert said in text or said.replace("\u00a0", " ") in text, said


# --- Graphiques ------------------------------------------------------------------------


@pytest.fixture
def charted(disk: Path) -> PdfTool:
    """Le même outil, monté avec ``blocks: [chart]``."""
    return make(
        disk, {"name": "sortie", "path": "sortie", "rights": ["read", "write"]}, blocks=["chart"]
    )


async def test_charts_are_off_unless_asked(tool: PdfTool, charted: PdfTool) -> None:
    assert "chart" not in json.dumps(tool.spec.input_schema)
    assert "Graphique" not in tool.spec.description
    assert "Graphique ('type': 'chart')" in charted.spec.description
    assert ("blocs", "chart") in charted.settings().options
    chart = {"type": "chart", "categories": ["a"], "series": [{"name": "s", "values": [1]}]}
    arguments: dict[str, Any] = {"path": "sortie/g.pdf", "blocks": [chart]}
    assert list(bounded_validator(tool.spec.input_schema).iter_errors(arguments))
    assert not list(bounded_validator(charted.spec.input_schema).iter_errors(arguments))
    with pytest.raises(ToolError, match="Arguments invalides"):
        await call(tool, **arguments)
    assert (await call(charted, **arguments))["pages"] == 1


async def test_a_chart_is_drawn_in_the_pdf(charted: PdfTool, disk: Path) -> None:
    tool = charted
    blocks: list[dict[str, Any]] = [
        TOP10,
        {
            "type": "chart",
            "kind": "horizontal_bar",
            "title": "TTC par client",
            "from_table": {"categories": "Client", "values": ["Total TTC"]},
        },
        {
            "type": "chart",
            "kind": "line",
            "value_type": "amount_eur",
            "categories": ["janv.", "févr.", "mars"],
            "series": [
                {"name": "Facturé", "values": [18200, 21500, 19800]},
                {"name": "Encaissé", "values": [15100, None, 20500]},
            ],
        },
        {
            "type": "chart",
            "kind": "pie",
            "value_type": "percent",
            "categories": ["Plomberie", "Chauffage 🔥"],
            "series": [{"name": "Part", "values": [0.6, 0.4]}],
        },
    ]
    result = await call(tool, path="sortie/graphiques.pdf", blocks=blocks)
    # Un libellé de graphique passe par la même vérification des polices que le texte.
    assert [w for w in result["warnings"] if "🔥" in w]
    text = " ".join(
        page.extract_text() for page in PdfReader(disk / "sortie" / "graphiques.pdf").pages
    ).replace("\u00a0", " ")
    for said in ("TTC par client", "Julien Barre", "18 034,17 €", "Facturé", "60,0 %"):
        assert said in text, said
    again = await call(tool, path="sortie/graphiques2.pdf", blocks=blocks)
    assert again["sha256"] == result["sha256"]


# --- Images ---------------------------------------------------------------------------

PHOTO: Final = Path(__file__).parents[1] / "examples" / "j2" / "files" / "photo.jpg"


async def test_images_in_a_pdf(disk: Path) -> None:
    shots = disk / "maquettes"
    shots.mkdir()
    (shots / "depot.png").write_bytes(png(390, 844, color=6))
    (shots / "photo.jpg").write_bytes(PHOTO.read_bytes())
    plain = make(disk, {"name": "sortie", "path": "sortie", "rights": ["write"]})
    assert "Image ('type': 'image')" not in plain.spec.description
    tool = make(
        disk,
        {"name": "maquettes", "path": "maquettes", "rights": ["read"]},
        {"name": "sortie", "path": "sortie", "rights": ["write"]},
        blocks=["image"],
    )
    assert "Image ('type': 'image')" in tool.spec.description
    blocks = [
        {"type": "image", "path": "maquettes/depot.png", "caption": "Écran 1"},
        {"type": "image", "path": "maquettes/photo.jpg", "width": "half", "alt": "Chantier"},
        {"type": "image", "path": "maquettes/depot.png", "width": "small", "alt": "Rappel"},
    ]
    result = await call(tool, path="sortie/cdc.pdf", title="Cahier", blocks=blocks)
    assert (result["images"], result["pages"], result["warnings"]) == (3, 2, [])
    found = PdfReader(disk / "sortie" / "cdc.pdf")
    # Trois images posées, dans leurs pixels ; la même image deux fois.
    drawn: list[Any] = [
        cast(Any, image).image.size for page in found.pages for image in page.images
    ]
    assert sorted(drawn) == [(390, 844), (390, 844), (2016, 1512)]
    assert "Écran 1" in found.pages[0].extract_text()
    again = await call(tool, path="sortie/cdc2.pdf", title="Cahier", blocks=blocks)
    assert again["sha256"] == result["sha256"]


async def test_a_pdf_follows_its_template(disk: Path) -> None:
    rapport = "sections:\n  Contexte:\n  Travaux réalisés:\n  Garantie:\n    text: Un an.\n"
    tool = make(
        disk,
        {"name": "sortie", "path": "sortie", "rights": ["write"]},
        templates={"rapport": {"text": rapport}},
    )
    assert "- rapport" in tool.spec.description
    done: list[Any] = [{"type": "paragraph", "text": "Fait."}]
    with pytest.raises(ToolError, match="section\\(s\\) absente\\(s\\) : « Contexte »"):
        await call(
            tool, path="sortie/r.pdf", template="rapport", sections={"Travaux réalisés": done}
        )
    sections = {"Contexte": done, "Travaux réalisés": done}
    written = await call(tool, path="sortie/r.pdf", template="rapport", sections=sections)
    assert (written["template"], written["pages"]) == ("rapport", 1)
    text = PdfReader(disk / "sortie" / "r.pdf").pages[0].extract_text()
    # Les titres écrits par l'outil, et le texte fixe.
    assert text.index("Contexte") < text.index("Travaux réalisés") < text.index("Un an.")


# --- Le style dans le PDF (2.1.0) ---------------------------------------------------------

FONTS: Final = Path("/usr/share/fonts/truetype/crosextra")
SORTIE: Final = {"name": "sortie", "path": "sortie", "rights": ["read", "write", "overwrite"]}


def styled_pdf(disk: Path, **params: Any) -> PdfTool:
    """L'outil monté avec la charte, la facture et le rapport des essais de ``word``."""
    (disk / "sortie" / "a.png").write_bytes(png(80, 60))
    return make(
        disk,
        SORTIE,
        blocks=["image", "signature", "toc", "checkboxes"],
        style={"spec": CHARTE},
        templates={"facture": {"text": FACTURE_YAML}, "rapport": {"text": RAPPORT_YAML}},
        **params,
    )


def placed(page: Any) -> list[tuple[float, str]]:
    """Le texte d'une page, morceau par morceau, avec sa hauteur dans la page."""
    found: list[tuple[float, str]] = []

    def seen(text: str, cm: Any, tm: Any, font: Any, size: Any) -> None:
        del cm, font, size
        if text.strip():
            found.append((round(float(tm[5]), 1), text.strip()))

    page.extract_text(visitor_text=seen)
    return found


def xs(page: Any) -> list[tuple[float, str]]:
    """Le texte d'une page, morceau par morceau, avec sa position depuis le bord gauche."""
    found: list[tuple[float, str]] = []

    def seen(text: str, cm: Any, tm: Any, font: Any, size: Any) -> None:
        del cm, font, size
        if text.strip():
            found.append((round(float(tm[4]), 1), text.strip()))

    page.extract_text(visitor_text=seen)
    return found


def heights(page: Any) -> list[tuple[str, float]]:
    """Le texte d'une page, morceau par morceau, avec sa hauteur depuis le bas de la page
    (la matrice du texte dans celle du dessin : un paragraphe se dessine à sa place)."""
    found: list[tuple[str, float]] = []

    def seen(text: str, cm: Any, tm: Any, font: Any, size: Any) -> None:
        del font, size
        if text.strip():
            found.append((text.strip(), round(float(tm[5]) * float(cm[3]) + float(cm[5]), 1)))

    page.extract_text(visitor_text=seen)
    return found


def drawn(page: Any) -> bytes:
    """Le flux de dessin d'une page : ses couleurs, ses polices, ses filets."""
    return cast(bytes, page.get_contents().get_data())


def test_no_visual_value_in_the_pdf_code() -> None:
    """Ni couleur ni police dans l'écriture PDF : elles viennent de ``default.yaml``, et les
    familles standard du PDF de ``PDF_FONTS``. Reste le blanc du papier (le trait entre deux
    secteurs d'un graphique)."""
    folder = Path(writer_module.__file__).parent
    fonts = re.compile(
        "|".join(sorted({*SAFE_FONTS, "helvetica", "times", "courier"})), re.IGNORECASE
    )
    for source in sorted(folder.glob("*.py")):
        code = source.read_text(encoding="utf-8")
        assert not re.findall(r"\b[0-9A-F]{6}\b", code), source.name
        assert not fonts.search(code), f"{source.name} : {fonts.search(code)}"


async def test_a_pdf_without_style_is_in_default_yaml(tool: PdfTool, disk: Path) -> None:
    """Sans style, le style de base de Word : le corps en 12 points (Aptos, rendu en
    Helvetica), les titres 0F4761, « Page n / N » en pied."""
    await call(tool, path="sortie/devis.pdf", **DEVIS)
    page = PdfReader(disk / "sortie" / "devis.pdf").pages[0]
    assert b" 12 Tf" in drawn(page)
    assert b".058824 .278431 .380392 rg" in drawn(page)  # 0F4761, les titres
    assert not any(label in ("style", "polices") for label, _ in tool.settings().options)


async def test_a_facture_in_the_style_of_its_template(disk: Path) -> None:
    """La facture de Word, en PDF : la charte (logo, couleurs, filets, mentions, filigrane)
    et le modèle (émetteur et client côte à côte, récapitulatif à droite, titres discrets)."""
    tool = styled_pdf(disk)
    written = await call(
        tool, path="sortie/f.pdf", template="facture", title="Facture", sections=FACTURE_SECTIONS
    )
    assert (written["pages"], written["warnings"]) == (1, [])
    page = PdfReader(disk / "sortie" / "f.pdf").pages[0]
    found = placed(page)
    said = {text for _, text in found}
    # Les titres des sections sans titre ne s'impriment pas ; les titres discrets, si.
    assert not {"Émetteur", "Client", "Description"} & said
    assert "Informations de paiement" in said
    # Émetteur et client sur la même ligne.
    height = {text: y for y, text in found}
    assert height["Dupont Plomberie"] == height["SCI Les Tilleuls"]
    text = page.extract_text()
    for line in ("FACTURE", "BROUILLON", "Page 1 / 1", "SIRET 812 345 678 00021", "Net à payer"):
        assert line in text, line
    assert b".043137 .364706 .231373" in drawn(page)  # brand, 0B5D3B
    assert b".909804 .945098 .92549 rg" in drawn(page)  # E8F1EC, l'en-tête des tableaux
    assert b" Do" in drawn(page)  # le logo
    again = await call(
        tool, path="sortie/g.pdf", template="facture", title="Facture", sections=FACTURE_SECTIONS
    )
    assert again["sha256"] == written["sha256"]


async def test_the_header_and_the_footer_in_the_frame_of_their_style(disk: Path) -> None:
    """L'en-tête et le pied dans le cadre de leur style, comme Word : un filet sous l'en-tête,
    au-dessus du pied, un fond ; les capitales et le soulignement ; les lignes du pied à
    gauche quand son style l'est ; le texte du corps descendu d'autant."""
    plain = make(disk, SORTIE)
    framed = make(
        disk,
        SORTIE,
        style={
            "spec": {
                "header": {
                    "right": "{title}",
                    "border_bottom": "1pt 0B5D3B",
                    "space_after": 6,
                    "uppercase": True,
                    "underline": True,
                },
                "footer": {
                    "lines": ["SIRET 812 345 678 00021"],
                    "border_top": "0.5pt C0392B",
                    "fill": "F2F2F2",
                    "align": "left",
                },
            }
        },
    )
    note = {"title": "Note", "blocks": [{"type": "paragraph", "text": "Texte."}]}
    await call(plain, path="sortie/a.pdf", **note)
    await call(framed, path="sortie/b.pdf", **note)
    page = PdfReader(disk / "sortie" / "b.pdf").pages[0]
    said = drawn(page)
    assert b".043137 .364706 .231373 RG\n1 w" in said  # 0B5D3B, 1 pt : sous l'en-tête
    assert b".752941 .223529 .168627 RG\n.5 w" in said  # C0392B, 0,5 pt : sur le pied
    assert b".94902 .94902 .94902 rg" in said  # F2F2F2, le fond du pied
    assert said.count(b" l S") == 3  # les deux filets, le soulignement du titre
    text = page.extract_text()
    assert "NOTE" in text and "SIRET 812 345 678 00021" in text
    # Le pied à gauche : à la marge, plus l'écart du fond ; sans style, centré.
    where = {text: x for x, text in xs(page)}
    assert where["SIRET 812 345 678 00021"] == pytest.approx(2 * cm + 8, abs=0.5)
    assert where["Page 1 / 1"] == pytest.approx(2 * cm + 8, abs=0.5)
    first = dict(heights(PdfReader(disk / "sortie" / "a.pdf").pages[0]))
    assert dict(heights(page))["Texte."] < first["Texte."]  # l'en-tête prend plus de place


async def test_a_report_with_its_cover_landscape_and_columns(disk: Path) -> None:
    tool = styled_pdf(disk)
    sections = {**RAPPORT_SECTIONS, "Page de garde": [*RAPPORT_SECTIONS["Page de garde"]]}
    sections["Page de garde"].append({"type": "toc"})
    await call(tool, path="sortie/r.pdf", template="rapport", title="Rapport", sections=sections)
    pages = PdfReader(disk / "sortie" / "r.pdf").pages
    assert len(pages) == 4
    landscape = [float(page.mediabox.width) > float(page.mediabox.height) for page in pages]
    assert landscape == [False, False, True, False]
    cover = pages[0].extract_text()
    # La page de garde : sans en-tête, pied ni filigrane ; le sommaire et ses vraies pages.
    assert "Page 1" not in cover and "BROUILLON" not in cover and "Page de garde" not in cover
    numbers = [line.strip(" .") for line in cover.splitlines() if line.strip(" .").isdigit()]
    assert numbers == ["2", "2", "3", "4"]
    # Les entrées : le titre et son numéro, sans la mise en forme du titre (capitales).
    for heading in ("1. Contexte", "1.1 Photos", "2. Mesures", "3. Conditions générales"):
        assert heading in cover, heading
    assert len(cast(list[Any], pages[0]["/Annots"])) == 4  # un lien par entrée
    second = pages[1].extract_text()
    assert "Figure 1 — Avant" in second and "Figure 2 — Après" in second
    assert "Page 2 / 4" in second and "BROUILLON" in second
    assert "Tableau 1 — Pression mesurée" in pages[2].extract_text()


async def test_signature_contents_and_checkboxes(disk: Path) -> None:
    tool = styled_pdf(disk)
    blocks = [
        {"type": "toc"},
        {"type": "heading", "text": "Contrôles"},
        {
            "type": "list",
            "checkboxes": True,
            "items": ["[x] Coupure d'eau faite", {"text": "[ ] Fuite", "items": ["[x] Joint"]}],
        },
        {
            "type": "signature",
            "signers": ["Le client", "L'entreprise"],
            "mention": "Bon pour accord",
        },
    ]
    written = await call(tool, path="sortie/pv.pdf", template="none", blocks=blocks)
    assert written["warnings"] == []
    text = PdfReader(disk / "sortie" / "pv.pdf").pages[0].extract_text()
    for said in ("SOMMAIRE", "Coupure d'eau faite", "Joint", "Le client", "L'entreprise",
                 "Bon pour accord", "Date :", "Signature :"):  # fmt: skip
        assert said in text, said
    # Les cases sont dessinées : aucun caractère de case dans le texte.
    assert "\u2610" not in text and "\u2612" not in text


async def test_proposed_styles_and_language_in_a_pdf(disk: Path) -> None:
    charte = {
        "language": "en",
        "important_note": {
            "base": "note",
            "description": "A warning the reader must not miss.",
            "fill": "FDECEA",
        },
    }
    tool = make(disk, SORTIE, style={"spec": charte})
    assert '"important_note"' in json.dumps(tool.spec.input_schema)
    blocks = [
        {"type": "note", "style": "important_note", "text": "Closed on Friday."},
        {
            "type": "table",
            "columns": [{"header": "Item"}, {"header": "Amount", "type": "amount_eur"}],
            "rows": [["Joint", 1250.5]],
            "totals": {"Amount": "sum"},
        },
    ]
    await call(tool, path="sortie/n.pdf", blocks=blocks)
    page = PdfReader(disk / "sortie" / "n.pdf").pages[0]
    assert b".992157 .92549 .917647 rg" in drawn(page)  # FDECEA
    text = page.extract_text()
    assert "€1,250.50" in text and "Total" in text


@pytest.mark.skipif(not (FONTS / "Caladea-Regular.ttf").is_file(), reason="police Caladea absente")
async def test_an_embedded_font(disk: Path) -> None:
    """Une police dont le style donne les fichiers : embarquée en sous-ensemble ; le même
    fichier à chaque fois ; un caractère qu'elle n'a pas, dit."""
    regular = (FONTS / "Caladea-Regular.ttf").read_bytes()
    bold = (FONTS / "Caladea-Bold.ttf").read_bytes()
    style = {"fonts": {"Caladea": {"regular": regular, "bold": bold}}, "text": {"font": "Caladea"}}
    tool = make(disk, SORTIE, style={"spec": style})
    assert ("polices", "Caladea : embarquée ; Courier New → Courier") in tool.settings().options
    blocks = [{"type": "paragraph", "text": "Un **devis** et *une* note 🙂."}]
    first = await call(tool, path="sortie/a.pdf", title="Devis", blocks=blocks)
    second = await call(tool, path="sortie/b.pdf", title="Devis", blocks=blocks)
    assert first["sha256"] == second["sha256"]
    assert first["warnings"] == [
        "1 caractère(s) absent(s) des polices du PDF, imprimé(s) en carré noir : 🙂 — les "
        "remplacer par du texte"
    ]
    page = PdfReader(disk / "sortie" / "a.pdf").pages[0]
    resources = cast(dict[str, Any], page["/Resources"])
    fonts = cast(dict[str, Any], resources["/Font"])
    names = {str(font.get_object()["/BaseFont"]) for font in fonts.values()}
    assert {"/AAAAAA+Caladea-Regular", "/AAAAAA+Caladea-Bold"} <= names


def test_what_a_font_must_be() -> None:
    assert "WOFF" in str(font_fault(b"wOF2" + bytes(40)))
    assert "CFF" in str(font_fault(b"OTTO" + bytes(40)))
    assert "collection" in str(font_fault(b"ttcf" + bytes(40)))
    assert font_fault(b"PK\x03\x04" + bytes(40)) == "pas une police TrueType (.ttf)"


def _with_rights(data: bytes, rights: int) -> bytes:
    """La police, ses droits d'embarquement (``fsType``) changés."""
    count = struct.unpack(">H", data[4:6])[0]
    for index in range(count):
        name, _, offset, _ = struct.unpack(">4sIII", data[12 + 16 * index : 28 + 16 * index])
        if name == b"OS/2":
            return data[: offset + 8] + struct.pack(">H", rights) + data[offset + 10 :]
    raise AssertionError("pas de table OS/2")


@pytest.mark.skipif(not (FONTS / "Caladea-Regular.ttf").is_file(), reason="police Caladea absente")
def test_fonts_are_read_when_the_config_loads(demo: ConfigFactory, tmp_path: Path) -> None:
    (tmp_path / "sortie").mkdir()
    folders = [{"name": "sortie", "path": "sortie", "rights": ["write"]}]
    path = demo(agents=[pdf_agent(folders=folders, style="styles/charte.yaml")], storage=JSONL)
    prompts = tmp_path / "prompts"
    (prompts / "styles").mkdir(parents=True)
    (prompts / "styles" / "charte.yaml").write_text(
        "fonts:\n  Caladea: { regular: fonts/caladea.ttf }\ntext: { font: Caladea }\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match=r"style\.fonts\.Caladea\.regular — police introuvable"):
        load_config(path)
    (prompts / "fonts").mkdir()
    regular = (FONTS / "Caladea-Regular.ttf").read_bytes()
    for data, said in (
        (_with_rights(regular, 4), "aperçu et impression seulement"),
        (_with_rights(regular, 2), "embarquement interdit"),
        (b"wOF2" + bytes(40), "une police web"),
    ):
        (prompts / "fonts" / "caladea.ttf").write_bytes(data)
        with pytest.raises(ConfigError, match=said):
            load_config(path)
    # Modifiable (8) : embarquée.
    (prompts / "fonts" / "caladea.ttf").write_bytes(_with_rights(regular, 8))
    config = load_config(path)
    declared = config.agents[0].tools[0]
    assert isinstance(declared, PythonTool)
    params = PdfParams.model_validate(builtin_settings(declared, PdfParams))
    mounted = build(params, BuiltinContext(base_dir=tmp_path))
    assert ("polices", "Caladea : embarquée ; Courier New → Courier") in mounted.settings().options
    # Le format de la 2.0 : fonts nommait les polices du corps et des titres.
    (prompts / "styles" / "charte.yaml").write_text("fonts: { body: Arial }\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="fonts → font, sur text et sur chaque titre"):
        load_config(path)


def test_validate_says_how_the_pdf_writes_the_fonts(disk: Path) -> None:
    tool = make(disk, SORTIE, style={"spec": {"heading1": {"font": "Georgia"}}})
    assert (
        "polices",
        "Aptos → Helvetica ; Courier New → Courier ; Georgia → Times",
    ) in tool.settings().options


# --- Le texte enrichi (lot 5) -------------------------------------------------------------

RICH: Final[list[dict[str, Any]]] = [
    {"type": "heading", "text": "Objet"},
    {
        "type": "paragraph",
        "text": "Voir [le site](https://example.com/a?b=1)^[Par [courriel](mailto:a@b.fr), "
        "voir §[Détail].] et le détail en §[Détail].",
    },
    {"type": "heading", "text": "Détail", "level": 2},
    {
        "type": "list",
        "ordered": True,
        "items": [{"text": "un", "items": [{"text": "sous", "items": ["fin"]}]}],
    },
    {
        "type": "table",
        "columns": [{"header": "Désignation"}],
        "rows": [["**Pose**^[Certifiée.] voir [fiche](https://example.com/f)"]],
    },
    {"type": "fields", "items": [["Client", "M. **Dupont**"], ["Signature", ""]]},
    {"type": "code", "text": "def f():\n    return  1"},
]


async def test_rich_text_in_a_pdf(disk: Path) -> None:
    tool = make(disk, SORTIE, blocks=["fields", "code"], style={"spec": {"numbering": "decimal"}})
    written = await call(tool, path="sortie/riche.pdf", title="Devis", blocks=RICH)
    assert written["warnings"] == []
    reader = PdfReader(disk / "sortie" / "riche.pdf")
    text = "\n".join(page.extract_text() for page in reader.pages)
    # Le renvoi : le numéro du titre visé ; les notes, en fin de document, sous « Notes ».
    assert "le détail en 1.1." in text
    notes = text[text.index("Notes") :]
    assert "1 Par courriel, voir 1.1." in notes and "2 Certifiée." in notes
    # Trois niveaux numérotés : 1., a), i. ; le code garde ses espaces.
    for said in ("1.", "a)", "i.", "Client", "Dupont", "Signature", "return  1"):
        assert said in text, said
    links: list[Any] = []
    for page in reader.pages:
        for annotation in cast(list[Any], page.get("/Annots") or []):
            action = annotation.get_object().get("/A")
            links.append(action.get("/URI") if action is not None else "interne")
    # Les liens externes, et les liens internes (appels de note, renvois) vers leur ancre.
    assert {"https://example.com/a?b=1", "mailto:a@b.fr", "https://example.com/f"} <= set(links)
    assert links.count("interne") >= 4


async def test_a_pdf_without_notes_has_no_notes_heading(tool: PdfTool, disk: Path) -> None:
    await call(tool, path="sortie/devis.pdf", **DEVIS)
    text = PdfReader(disk / "sortie" / "devis.pdf").pages[0].extract_text()
    assert "Notes" not in text


# --- Les calculs (lot 6) ----------------------------------------------------------------

INVOICE: Final[list[dict[str, Any]]] = [
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
            {
                "group": "Lot 1 Salle de bains",
                "rows": [["Dépose", 1, 180, 0.1], ["Receveur", 1, 1250.5, 0.1]],
            },
            {"group": "Lot 2 Cuisine", "rows": [["Mitigeur", 2, 89.99, 0.2]]},
        ],
        "totals": {"Montant HT": "sum"},
    },
    {"type": "invoice_totals", "from_table": {"amount": "Montant HT", "rate": "TVA"}, "paid": 500},
]


async def test_an_invoice_in_a_pdf(disk: Path) -> None:
    tool = make(
        disk, SORTIE, blocks=["invoice_totals"], style={"spec": {"table_group": {"fill": "EAF1DD"}}}
    )
    written = await call(tool, path="sortie/f.pdf", title="Facture", blocks=INVOICE)
    assert written["warnings"] == []
    page = PdfReader(disk / "sortie" / "f.pdf").pages[0]
    # Le texte, ses espaces insécables lues en espaces.
    text = page.extract_text().replace("\u00a0", " ")
    for said in ("Lot 1 Salle de bains", "Sous-total", "1 430,50 €", "179,98 €",
                 "TVA 20 % sur 179,98 €", "1 789,53 €", "Net à payer", "1 289,53 €"):  # fmt: skip
        assert said in text, said
    # Le fond des lignes de groupe (EAF1DD).
    assert b".917647 .945098 .866667 rg" in drawn(page)


# --- L'émargement (lot 7) ---------------------------------------------------------------


async def test_an_attendance_sheet_in_a_pdf(disk: Path) -> None:
    tool = make(disk, SORTIE, blocks=["attendance"])
    people = [f"Participant {number}" for number in range(1, 25)]
    sheet = {"type": "attendance", "participants": people, "sessions": ["Matin", "Soir"]}
    written = await call(tool, path="sortie/e.pdf", title="Émargement", blocks=[sheet])
    # 24 lignes de 1,5 cm (36 cm) : deux pages, l'en-tête répété sur la seconde.
    assert written["pages"] == 2
    first, second = (page.extract_text() for page in PdfReader(disk / "sortie" / "e.pdf").pages)
    assert "Participant 1\n" in first and "Participant 24" in second
    assert "Matin" in second and "Soir" in second
