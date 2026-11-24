# SPDX-License-Identifier: Apache-2.0
"""Ce que l'approbateur examine : détail, fichier désigné, aperçu (docs/analyse-exortateur-loom.md § 6.7, lot 4)."""

import asyncio
import base64
import hashlib
import io
import re
import zipfile
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from conftest import DEV, OWNER, audited, boot, secured
from image_files import png

from loom_ia.access import AccessDenied, ApprovalFileRefused, Loom
from loom_ia.access import api as api_module
from loom_ia.access.api import UnknownApproval
from loom_ia.core.model import RunId, RunStatus, SessionId

LOGO = png(40, 20)
CSV = "Client;Date devis;Montant HT;Relances\nDupont;02/10/2026;1 250,50;2\nMartin;2026-10-09;830;0\n"
DEVIS: dict[str, Any] = {
    "title": "Devis D-042",
    "blocks": [
        {"type": "paragraph", "text": "Madame **Dupont**, voici notre proposition."},
        {"type": "image", "path": "images/logo.png", "caption": "Notre logo"},
    ],
}
SUIVI: dict[str, Any] = {
    "name": "Suivi",
    "columns": [
        {"header": "Client", "type": "text"},
        {"header": "Date devis", "type": "date"},
        {"header": "Montant HT", "type": "amount_eur"},
        {"header": "Relances", "type": "integer"},
    ],
    "data": {"path": "donnees/devis.csv"},
}
OUTIL = '''
from loom_ia.tools import tool


@tool
async def envoyer_email(destinataire: str) -> str:
    """Envoie un e-mail."""
    return f"envoyé à {destinataire}"
'''

CALLS: dict[str, tuple[str, dict[str, Any]]] = {
    "fichiers": ("local_files", {"action": "delete", "path": "sortie/vieux.txt"}),
    "reecrire": ("local_files", {"action": "write", "path": "sortie/note.txt", "content": "Nouveau texte.", "overwrite": True}),
    "lien": ("local_files", {"action": "delete", "path": "sortie/lien.txt"}),
    "word": ("word", {"action": "write", "path": "sortie/devis.docx", **DEVIS}),
    "pdf": ("pdf", {"action": "write", "path": "sortie/devis.pdf", **DEVIS}),
    "excel": ("excel", {"path": "sortie/suivi.xlsx", "sheets": [SUIVI]}),
    "mail": ("envoyer_email", {"destinataire": "mme.martin@example.com, IBAN FR76 3000 6000 0112 3456 7890 189"}),
    "plan": ("word", {"action": "write", "path": "sortie/plan.docx", "title": "Plan", "blocks": [{"type": "image", "path": "privees/plan.png"}]}),
}
FOLDERS: list[dict[str, Any]] = [
    {
        "name": "sortie",
        "path": "sortie",
        "rights": ["read", "write", "overwrite", "delete"],
        "approval": {"write": "always", "overwrite": "always", "delete": "always"},
    },
    {"name": "images", "path": "images", "rights": ["read"]},
    {"name": "donnees", "path": "donnees", "rights": ["read"]},
    {"name": "privees", "path": "privees", "rights": ["read"], "approval": {"read": "always"}},
]
OFFICE: list[dict[str, Any]] = [
    {"name": "sortie", "path": "sortie", "rights": ["read", "write", "overwrite"], "approval": {"write": "always", "overwrite": "always"}},
    *FOLDERS[1:],
]
TOOLS: dict[str, dict[str, Any]] = {
    "local_files": {"python": "loom.local_files", "folders": FOLDERS},
    "word": {"python": "loom.word", "folders": OFFICE, "blocks": ["image"]},
    "pdf": {"python": "loom.pdf", "folders": OFFICE, "blocks": ["image"]},
    "excel": {"python": "loom.excel", "folders": OFFICE},
    "envoyer_email": {"python": "envoyer_email", "side_effects": "irreversible", "approval": "always"},
}

type Build = Callable[..., Path]

EXTRAS: dict[str, tuple[tuple[str, ...], str]] = {"pdf": (("reportlab", "pypdf"), "pdf"), "excel": (("openpyxl",), "office")}
"""Les agents dont l'outil demande un extra : ses modules, et son nom."""


def needs(agent: str) -> None:
    """Saute l'essai si l'extra de l'outil de cet agent n'est pas installé (job du noyau seul)."""
    modules, extra = EXTRAS.get(agent, ((), ""))
    for module in modules:
        pytest.importorskip(module, reason=f"extra '{extra}' absent")


@pytest.fixture
def bureau(tmp_path: Path) -> Build:
    """Un agent par appel à approuver ; ``dev`` propriétaire, ``paul`` approuve sans lire le contenu, ``lea`` lit sans approuver."""

    def build(**root: Any) -> Path:
        for name in ("agents", "sortie", "images", "donnees", "dehors", "privees"):
            (tmp_path / name).mkdir(exist_ok=True)
        (tmp_path / "images" / "logo.png").write_bytes(LOGO)
        (tmp_path / "privees" / "plan.png").write_bytes(LOGO)
        (tmp_path / "donnees" / "devis.csv").write_text(CSV, encoding="utf-8")
        (tmp_path / "sortie" / "vieux.txt").write_text("Ancien devis.", encoding="utf-8")
        (tmp_path / "sortie" / "note.txt").write_text("Ancien texte.", encoding="utf-8")
        (tmp_path / "dehors" / "secret.txt").write_text("Hors des dossiers.", encoding="utf-8")
        link = tmp_path / "sortie" / "lien.txt"
        if not link.is_symlink():
            link.symlink_to(tmp_path / "dehors" / "secret.txt")
        (tmp_path / "outils_bureau.py").write_text(OUTIL, encoding="utf-8")
        models = [
            {
                "id": name.upper(),
                "sdk": "fake",
                "model": f"{name}-1",
                "params": {"script": [{"text": "", "tool_calls": [{"name": tool, "arguments": arguments}]}, {"text": "Fait."}]},
            }
            for name, (tool, arguments) in CALLS.items()
        ]
        config: dict[str, Any] = {
            "version": 1,
            "imports": ["outils_bureau"],
            "models": models,
            "storage": {"events": {"backend": "jsonl", "path": "data"}},
            "telemetry": {"logging": {"level": "CRITICAL"}},
            "security": {
                "roles": [OWNER, {"id": "approbateur", "permissions": ["read", "approve"]}, {"id": "lecteur", "permissions": ["read", "read_content"]}],
                "users": [{"id": DEV, "roles": ["owner"]}, {"id": "paul", "roles": ["approbateur"]}, {"id": "lea", "roles": ["lecteur"]}],
            },
            **root,
        }
        (tmp_path / "loom.yaml").write_text(yaml.safe_dump(secured(config)), encoding="utf-8")
        for name, (tool, _) in CALLS.items():
            agent = {"name": name, "description": f"Agent {name}.", "main": {"model": name.upper(), "system": "Tu travailles."}, "tools": [TOOLS[tool]]}
            (tmp_path / "agents" / f"{name}.yaml").write_text(yaml.safe_dump(agent), encoding="utf-8")
        return boot(tmp_path / "loom.yaml")

    return build


@asynccontextmanager
async def paused(config: Path, agent: str) -> AsyncGenerator[tuple[Loom, RunId, str]]:
    async with Loom.from_config(config) as loom:
        result = await loom.run(agent, "Vas-y.", session_id=SessionId(agent), user=DEV)
        assert result.status is RunStatus.PAUSED, result
        (pending,) = result.pending_approvals
        yield loom, result.run_id, pending.call_id


def same_document(first: bytes, second: bytes) -> bool:
    """Le même fichier, horodatages mis à part (propriétés d'un fichier Office, dates d'un PDF)."""
    if first[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(first)) as one, zipfile.ZipFile(io.BytesIO(second)) as two:
            names = [name for name in one.namelist() if name != "docProps/core.xml"]
            return names == [name for name in two.namelist() if name != "docProps/core.xml"] and all(one.read(n) == two.read(n) for n in names)
    stamp = re.compile(rb"/(CreationDate|ModDate) \(D:[^)]*\)|/ID \[<[0-9a-f]+><[0-9a-f]+>\]")
    return stamp.sub(b"", first) == stamp.sub(b"", second)


# --- Le détail --------------------------------------------------------------------------


async def test_details_name_the_files_and_the_preview(bureau: Build) -> None:
    async with paused(bureau(), "word") as (loom, run_id, call_id):
        (detail,) = await loom.approval_details(run_id, session_id=SessionId("word"), user=DEV)
    assert (detail.call_id, detail.tool_name, detail.agent, detail.run_id) == (call_id, "word", "word", run_id)
    assert detail.files == ("images/logo.png",)
    assert detail.preview is True
    assert detail.arguments["title"] == "Devis D-042"


async def test_a_tool_without_the_protocols_shows_its_arguments_only(bureau: Build) -> None:
    async with paused(bureau(), "mail") as (loom, run_id, call_id):
        (detail,) = await loom.approval_details(run_id, session_id=SessionId("mail"), user=DEV)
        assert (detail.files, detail.preview) == ((), False)
        assert detail.arguments == dict(CALLS["mail"][1]), "en clair pour qui lit le contenu : il sait ce qu'il approuve"
        assert loom.config.telemetry.redaction.redactor().json(detail.arguments) != detail.arguments, "l'e-mail et l'IBAN seraient masqués"
        with pytest.raises(ApprovalFileRefused, match="no_preview"):
            await loom.approval_preview(run_id, call_id, session_id=SessionId("mail"), user=DEV)
        with pytest.raises(ApprovalFileRefused, match="not_cited"):
            await loom.approval_file(run_id, call_id, "sortie/vieux.txt", session_id=SessionId("mail"), user=DEV)


async def test_rights_are_those_of_a_decision_and_of_the_content(bureau: Build) -> None:
    config = bureau()
    async with paused(config, "fichiers") as (loom, run_id, call_id):
        session = SessionId("fichiers")
        with pytest.raises(AccessDenied, match="read_content"):
            await loom.approval_details(run_id, session_id=session, user="paul")
        with pytest.raises(AccessDenied, match="trancher"):
            await loom.approval_file(run_id, call_id, "sortie/vieux.txt", session_id=session, user="lea")
        with pytest.raises(UnknownApproval):
            await loom.approval_preview(run_id, "autre-appel", session_id=session, user=DEV)
        await loom.approve(run_id, session_id=session, user=DEV)
        await loom.drain()
        assert await loom.approval_details(run_id, session_id=session, user=DEV) == ()
    assert {line["user"] for line in audited(config)} == {"paul", "lea"}


# --- Le fichier désigné (cas 2) -------------------------------------------------------


async def test_the_file_a_call_would_delete_is_served(bureau: Build, tmp_path: Path) -> None:
    async with paused(bureau(), "fichiers") as (loom, run_id, call_id):
        session = SessionId("fichiers")
        (detail,) = await loom.approval_details(run_id, session_id=session, user=DEV)
        assert detail.files == ("sortie/vieux.txt",)
        found = await loom.approval_file(run_id, call_id, "sortie/vieux.txt", session_id=session, user=DEV)
        assert (found.name, found.media_type, found.data) == ("vieux.txt", "text/plain", b"Ancien devis.")
        with pytest.raises(ApprovalFileRefused, match="not_cited"):
            await loom.approval_file(run_id, call_id, "sortie/note.txt", session_id=session, user=DEV)
        with pytest.raises(ApprovalFileRefused, match="too_large"):
            await loom.approval_file(run_id, call_id, "sortie/vieux.txt", session_id=session, user=DEV, max_bytes=5)
    assert (tmp_path / "sortie" / "vieux.txt").exists(), "rien n'est touché"


async def test_a_link_out_of_the_folder_is_refused(bureau: Build) -> None:
    async with paused(bureau(), "lien") as (loom, run_id, call_id):
        (detail,) = await loom.approval_details(run_id, session_id=SessionId("lien"), user=DEV)
        assert detail.files == ("sortie/lien.txt",)
        with pytest.raises(ApprovalFileRefused, match=r"unreadable : Chemin refusé : sortie/lien\.txt mène hors des dossiers permis"):
            await loom.approval_file(run_id, call_id, "sortie/lien.txt", session_id=SessionId("lien"), user=DEV)


async def test_the_image_a_document_reads_is_served(bureau: Build) -> None:
    needs("pdf")
    async with paused(bureau(), "pdf") as (loom, run_id, call_id):
        found = await loom.approval_file(run_id, call_id, "images/logo.png", session_id=SessionId("pdf"), user=DEV)
    assert (found.name, found.media_type, found.data) == ("logo.png", "image/png", LOGO)


# --- L'aperçu (cas 3) -----------------------------------------------------------------


async def test_a_text_preview_then_the_old_file(bureau: Build, tmp_path: Path) -> None:
    async with paused(bureau(), "reecrire") as (loom, run_id, call_id):
        session = SessionId("reecrire")
        (detail,) = await loom.approval_details(run_id, session_id=session, user=DEV)
        assert detail.files == ("sortie/note.txt",)
        rendered = await loom.approval_preview(run_id, call_id, session_id=session, user=DEV)
        old = await loom.approval_file(run_id, call_id, "sortie/note.txt", session_id=session, user=DEV)
    assert (rendered.name, rendered.media_type, rendered.data) == ("note.txt", "text/plain", b"Nouveau texte.")
    assert old.data == b"Ancien texte."
    assert (tmp_path / "sortie" / "note.txt").read_text(encoding="utf-8") == "Ancien texte."


async def test_a_preview_of_nothing_written_is_refused(bureau: Build) -> None:
    async with paused(bureau(), "fichiers") as (loom, run_id, call_id):
        with pytest.raises(ApprovalFileRefused, match=r"no_preview : l'appel .* n'écrit pas de fichier"):
            await loom.approval_preview(run_id, call_id, session_id=SessionId("fichiers"), user=DEV)


@pytest.mark.parametrize(
    ("agent", "written", "media_type"),
    [
        ("word", "devis.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ("pdf", "devis.pdf", "application/pdf"),
        ("excel", "suivi.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ],
)
async def test_the_preview_is_the_file_written_after_approval(bureau: Build, tmp_path: Path, agent: str, written: str, media_type: str) -> None:
    needs(agent)
    async with paused(bureau(), agent) as (loom, run_id, call_id):
        session = SessionId(agent)
        rendered = await loom.approval_preview(run_id, call_id, session_id=session, user=DEV)
        assert (rendered.name, rendered.media_type) == (written, media_type)
        assert not (tmp_path / "sortie" / written).exists(), "l'aperçu n'écrit rien"
        await loom.approve(run_id, session_id=session, user=DEV)
        await loom.drain()
    assert same_document(rendered.data, (tmp_path / "sortie" / written).read_bytes())


async def test_a_slow_preview_is_cut(bureau: Build, monkeypatch: pytest.MonkeyPatch) -> None:
    slow_previewed = api_module.previewed

    def slow(tool: object, arguments: Any) -> Any:
        import time

        time.sleep(0.5)
        return slow_previewed(tool, arguments)

    monkeypatch.setattr(api_module, "previewed", slow)
    async with paused(bureau(), "reecrire") as (loom, run_id, call_id):
        with pytest.raises(ApprovalFileRefused, match="preview_timeout"):
            await loom.approval_preview(run_id, call_id, session_id=SessionId("reecrire"), user=DEV, within=0.05)
        await asyncio.sleep(0.6)


async def test_a_too_large_preview_is_refused(bureau: Build) -> None:
    async with paused(bureau(), "word") as (loom, run_id, call_id):
        with pytest.raises(ApprovalFileRefused, match="too_large"):
            await loom.approval_preview(run_id, call_id, session_id=SessionId("word"), user=DEV, max_bytes=100)


async def test_a_preview_reads_an_image_under_approval(bureau: Build, tmp_path: Path) -> None:
    """Q-8 : l'image d'un dossier dont la lecture demande une approbation est lue par l'aperçu ; la demande la couvre."""
    async with paused(bureau(), "plan") as (loom, run_id, call_id):
        session = SessionId("plan")
        (detail,) = await loom.approval_details(run_id, session_id=session, user=DEV)
        assert detail.files == ("privees/plan.png",)
        assert "lue(s) dans privees" in detail.reason
        rendered = await loom.approval_preview(run_id, call_id, session_id=session, user=DEV)
        assert any("ni alt ni caption" in warning for warning in rendered.warnings), "les avertissements du rendu suivent l'aperçu"
        with zipfile.ZipFile(io.BytesIO(rendered.data)) as document:
            assert any(name.startswith("word/media/") for name in document.namelist())
    assert not (tmp_path / "sortie" / "plan.docx").exists()


# --- Par la console et par le REST ------------------------------------------------------


async def test_the_console_gets_a_cited_file_and_a_preview(bureau: Build) -> None:
    from typing import cast

    pytest.importorskip("websockets", reason="extra 'console' absent")
    from loom_ia.console.channel import ConsoleChannel
    from loom_ia.console.commands import CommandExecutor
    from loom_ia.console.contract import CommandPayload, CommandResultPayload, FileDataPayload, Payload

    class Sent:
        def __init__(self) -> None:
            self.payloads: list[Payload] = []
            self.three_results = asyncio.Event()

        async def send(self, payload: Payload, *, message_id: str | None = None) -> bool:
            del message_id
            self.payloads.append(payload)
            if sum(1 for item in self.payloads if not isinstance(item, FileDataPayload)) >= 3:
                self.three_results.set()
            return True

    console = {
        "enable": True,
        "url": "ws://127.0.0.1:9/loom",
        "key_env": "LOOM_CONSOLE_KEY",
        "commands": {"user": "console-gateway"},
        "files": {"max_size": 1000, "chunk_size": 8},
    }
    security = {
        "roles": [OWNER, {"id": "gateway", "permissions": ["act_as"], "act_as_roles": ["owner"]}],
        "users": [{"id": DEV, "roles": ["owner"]}, {"id": "console-gateway", "roles": ["gateway"]}],
    }
    async with paused(bureau(profile="dev", console=console, security=security), "reecrire") as (loom, run_id, call_id):
        assert loom.config.console is not None
        sent = Sent()
        executor = CommandExecutor(loom, cast("ConsoleChannel", sent), loom.config.console)
        expires = datetime.now(UTC) + timedelta(minutes=1)
        base = {"expires_at": expires, "as_user": DEV}
        cited = CommandPayload.model_validate(
            {
                **base,
                "command_id": "c-file",
                "name": "approval.file",
                "args": {"run_id": run_id, "call_id": call_id, "path": "sortie/note.txt", "session_id": "reecrire"},
            }
        )
        preview = CommandPayload.model_validate(
            {**base, "command_id": "c-preview", "name": "approval.preview", "args": {"run_id": run_id, "call_id": call_id, "session_id": "reecrire"}}
        )
        absent = CommandPayload.model_validate(
            {
                **base,
                "command_id": "c-absent",
                "name": "approval.file",
                "args": {"run_id": run_id, "call_id": call_id, "path": "sortie/vieux.txt", "session_id": "reecrire"},
            }
        )
        for command in (cited, preview, absent):
            executor.receive(command)
        async with asyncio.timeout(5):
            await sent.three_results.wait()
        await asyncio.sleep(0.05)
        await executor.aclose()
    results = {item.command_id: item for item in sent.payloads if isinstance(item, CommandResultPayload)}
    assert results["c-file"].result == {
        "name": "note.txt",
        "media_type": "text/plain",
        "size": 13,
        "sha256": hashlib.sha256(b"Ancien texte.").hexdigest(),
        "parts": 2,
    }
    assert cast("dict[str, Any]", results["c-preview"].result)["size"] == len("Nouveau texte.")
    assert (results["c-absent"].outcome, results["c-absent"].reason) == (
        "refused",
        "not_cited : sortie/vieux.txt n'est pas un fichier que l'appel " + call_id + " désigne",
    )
    parts = [item for item in sent.payloads if isinstance(item, FileDataPayload) and item.command_id == "c-file"]
    assert b"".join(base64.b64decode(part.data) for part in parts) == b"Ancien texte."


async def test_the_rest_serves_details_files_and_previews(bureau: Build) -> None:
    pytest.importorskip("fastapi", reason="extra 'http' absent")
    httpx2 = pytest.importorskip("httpx2", reason="client HTTP de test absent")
    from loom_ia.access.http import create_app

    async with paused(bureau(), "reecrire") as (loom, run_id, call_id):
        transport = httpx2.ASGITransport(create_app(loom))
        async with httpx2.AsyncClient(transport=transport, base_url="http://loom.test") as http:
            details = await http.get(f"/v1/runs/{run_id}/approvals", params={"session_id": "reecrire"})
            cited = await http.get(f"/v1/runs/{run_id}/approvals/{call_id}/file", params={"session_id": "reecrire", "path": "sortie/note.txt"})
            rendered = await http.get(f"/v1/runs/{run_id}/approvals/{call_id}/preview", params={"session_id": "reecrire"})
            absent = await http.get(f"/v1/runs/{run_id}/approvals/{call_id}/file", params={"session_id": "reecrire", "path": "sortie/vieux.txt"})
            as_lea = await http.get(f"/v1/runs/{run_id}/approvals", params={"session_id": "reecrire", "as_user": "lea"})
    assert details.status_code == 200
    ((detail,),) = [details.json()]
    assert (detail["call_id"], detail["files"], detail["preview"]) == (call_id, ["sortie/note.txt"], True)
    assert detail["arguments"] == CALLS["reecrire"][1] and "masked" not in detail
    assert (cited.status_code, cited.content, cited.headers["content-type"].split(";")[0]) == (200, b"Ancien texte.", "text/plain")
    assert (rendered.status_code, rendered.content) == (200, b"Nouveau texte.")
    assert (absent.status_code, absent.json()["code"]) == (404, "not_cited")
    assert as_lea.status_code == 403, "dev n'est pas une passerelle"


async def test_the_rest_gives_the_detail_in_clear(bureau: Build) -> None:
    """Comme ``approval_details`` : l'e-mail et l'IBAN d'un appel reviennent tels quels à qui lit le contenu."""
    pytest.importorskip("fastapi", reason="extra 'http' absent")
    httpx2 = pytest.importorskip("httpx2", reason="client HTTP de test absent")
    from loom_ia.access.http import create_app

    async with paused(bureau(), "mail") as (loom, run_id, call_id):
        transport = httpx2.ASGITransport(create_app(loom))
        async with httpx2.AsyncClient(transport=transport, base_url="http://loom.test") as http:
            details = await http.get(f"/v1/runs/{run_id}/approvals", params={"session_id": "mail"})
    assert details.status_code == 200
    ((detail,),) = [details.json()]
    assert (detail["call_id"], detail["arguments"]) == (call_id, CALLS["mail"][1])
