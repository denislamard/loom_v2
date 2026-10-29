# SPDX-License-Identifier: Apache-2.0
"""Outil fourni ``loom.pdf`` : écrire un PDF dans le style maison, lire le texte d'un PDF.

Les PDF écrits sont relus avec pypdf. La lecture passe par le process borné
(``worker``) : ses bornes sont éprouvées par de faux process (qui dorment, qui
meurent) et par une bombe de décompression.
"""

import asyncio
import io
import json
import subprocess
import sys
import time
import zlib
from pathlib import Path
from typing import Any, Final
from zoneinfo import ZoneInfo

import pytest

pytest.importorskip("reportlab", reason="extra 'pdf' absent")
pytest.importorskip("pypdf", reason="extra 'pdf' absent")

from conftest import ConfigFactory, demo_agent
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen.canvas import Canvas

import loom_ia.tools_builtin.pdf.reader as reader_module
from loom_ia.access.cli import main
from loom_ia.adapters.stores import InMemoryEventStore
from loom_ia.config import ConfigError, load_config
from loom_ia.core.bounded_regex import bounded_validator
from loom_ia.core.model import DEFAULT_TENANT, RunId, SessionId
from loom_ia.core.ports import ToolContext, ToolError
from loom_ia.runtime import build_agent
from loom_ia.tools_builtin import BuiltinContext
from loom_ia.tools_builtin.pdf import PdfTool, build
from loom_ia.tools_builtin.pdf import tool as tool_module
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
        {"type": "paragraphe", "text": "Madame **Dupont**,\nvoici notre *proposition*."},
        {"type": "titre", "text": "Travaux"},
        {"type": "liste", "items": ["Dépose", {"text": "Pose", "items": ["receveur"]}]},
        {"type": "liste", "items": ["Signature", "Acompte"], "ordered": True},
        {
            "type": "tableau",
            "columns": [{"header": "Désignation"}, {"header": "Montant HT", "type": "montant_eur"}],
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
    for said in ("- recus : read", "Polices standard du PDF", "pages", "pas d'OCR", "next_page"):
        assert said in spec.description, said
    settings = tool.settings()
    assert settings.options == (
        ("fuseau", "Europe/Paris"),
        ("graphiques", "non"),
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
    assert (result["written"], result["pages"], result["warnings"]) == (True, 2, [])
    found = PdfReader(disk / "sortie" / "devis.pdf")
    assert len(found.pages) == 2
    assert found.metadata is not None and found.metadata.title == "Devis D-2026-042"
    first = found.pages[0].extract_text()
    for said in ("Page 1 / 2", "Devis D-2026-042", "Dupont", "1 430,50 €", "Total"):
        assert said in first.replace("\u00a0", " "), said
    assert "Conditions" in found.pages[1].extract_text()


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
        blocks=[{"type": "paragraphe", "text": "Merci 🙂 — 中文 ≥ \u03b1 €"}],
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
    assert found["page_count"] == 2 and found["truncated"] is False
    assert found["metadata"]["title"] == "Devis D-2026-042"
    assert [page["number"] for page in found["pages"]] == [1, 2]
    assert "Madame Dupont" in found["pages"][0]["text"]
    assert "notes" not in found


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
    (disk / "recus" / "scan.pdf").write_bytes(pdf_of("texte", ""))
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
    "type": "tableau",
    "columns": [
        {"header": "Rang", "type": "entier"},
        {"header": "Client"},
        {"header": "Code"},
        {"header": "Factures", "type": "entier"},
        {"header": "Total HT", "type": "montant_eur"},
        {"header": "Total TTC", "type": "montant_eur"},
    ],
    "rows": [
        [1, "Julien Barre", "C-429", 2, 17094.0, 18034.17],
        [2, "Chauffage Lemaire Rénovation", "C-851", 2, 13539.47, 16247.36],
        [10, "Maxime Hubert", "C-143", 1, 10095.29, 10650.53],
    ],
    "totals": {"Factures": "somme", "Total HT": "somme", "Total TTC": "somme"},
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
    from loom_ia.tools_builtin.pdf.writer import CELL, HEADER_CELL, TEXT_WIDTH

    request = WriteArguments.model_validate({"path": "s/t.pdf", "title": None, "blocks": [table]})
    [found] = prepare(request, ZoneInfo("Europe/Paris")).blocks
    assert isinstance(found, TableOut)
    for index, (header, part) in enumerate(zip(found.headers, found.widths, strict=True)):
        width = part * TEXT_WIDTH - 8  # 4 points de marge de chaque côté de la cellule
        for word in header.split():
            assert stringWidth(word, HEADER_CELL.fontName, HEADER_CELL.fontSize) <= width, word
        for row in [*found.rows, found.total or ()]:
            value = row[index] if row else ""
            if len(value) <= SHORT_VALUE_CHARS:
                assert stringWidth(value, CELL.fontName, CELL.fontSize) <= width, value
    await call(tool, path="sortie/top.pdf", blocks=[table], overwrite=True)
    text = PdfReader(disk / "sortie" / "top.pdf").pages[0].extract_text()
    for said in ("Rang", "Code", "Factures", "C-429", "18\u00a0034,17\u00a0€"):
        assert said in text or said.replace("\u00a0", " ") in text, said


# --- Graphiques ------------------------------------------------------------------------


@pytest.fixture
def charted(disk: Path) -> PdfTool:
    """Le même outil, monté avec ``charts: true``."""
    return make(
        disk, {"name": "sortie", "path": "sortie", "rights": ["read", "write"]}, charts=True
    )


async def test_charts_are_off_unless_asked(tool: PdfTool, charted: PdfTool) -> None:
    assert "graphique" not in json.dumps(tool.spec.input_schema)
    assert "Graphique" not in tool.spec.description
    assert "Graphique ('type': 'graphique')" in charted.spec.description
    assert ("graphiques", "oui") in charted.settings().options
    chart = {"type": "graphique", "categories": ["a"], "series": [{"name": "s", "values": [1]}]}
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
            "type": "graphique",
            "kind": "barres_horizontales",
            "title": "TTC par client",
            "from_table": {"categories": "Client", "values": ["Total TTC"]},
        },
        {
            "type": "graphique",
            "kind": "courbe",
            "value_type": "montant_eur",
            "categories": ["janv.", "févr.", "mars"],
            "series": [
                {"name": "Facturé", "values": [18200, 21500, 19800]},
                {"name": "Encaissé", "values": [15100, None, 20500]},
            ],
        },
        {
            "type": "graphique",
            "kind": "secteurs",
            "value_type": "pourcentage",
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
