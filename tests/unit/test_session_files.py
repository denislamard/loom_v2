# SPDX-License-Identifier: Apache-2.0
"""Fichiers d'une session : liste tirée du journal, extraction, accès REST.

Le stockage range les fichiers sous leur condensé ; seul le journal dit ce
qu'ils sont (``artifact.stored``). Demandé au test D8 du 18/10 : retrouver
les fichiers d'une session sous leur nom, sans écrire d'index à côté.
"""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
import yaml

from loom_ia.access import Loom
from loom_ia.access.api import SessionFile, UnknownSession
from loom_ia.access.cli import main
from loom_ia.config.keys import fingerprint, new_api_key
from loom_ia.core.model import Attachment, SessionId
from loom_ia.core.ports import ArtifactNotFound

SESSION = SessionId("chantier")
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
AUTRE_PNG = b"\x89PNG\r\n\x1a\n" + b"\x01" * 24
CSV = b"designation;prix\npose;45\n"

OUTILS = f'''
from loom_ia.core.model import InlineDataBlock, TextBlock, ToolOutput
from loom_ia.tools import Image, tool


@tool
def graphique() -> Image:
    """Dessine le graphique."""
    return Image(data={PNG!r}, name="graphique.png")


@tool
def autre_graphique() -> Image:
    """Dessine un autre graphique, sous le même nom."""
    return Image(data={AUTRE_PNG!r}, name="graphique.png")


@tool
def exporter() -> ToolOutput:
    """Exporte le devis."""
    return ToolOutput(
        blocks=(
            TextBlock(text="Exporté."),
            InlineDataBlock(media_type="text/csv", data={CSV!r}, name="devis.csv"),
        )
    )
'''

SCRIPT: list[dict[str, Any]] = [
    {
        "text": "Je produis.",
        "tool_calls": [
            {"name": "graphique", "arguments": {}},
            {"name": "graphique", "arguments": {}},
            {"name": "exporter", "arguments": {}},
            {"name": "autre_graphique", "arguments": {}},
        ],
    },
    {"text": "C'est fait."},
]

LECTEUR, CONTENU = new_api_key(), new_api_key()


@pytest.fixture
def atelier(tmp_path: Path) -> Path:
    (tmp_path / "agents").mkdir()
    (tmp_path / "outils_fichiers.py").write_text(OUTILS, encoding="utf-8")
    config: dict[str, Any] = {
        "version": 1,
        "imports": ["outils_fichiers"],
        "models": [
            {
                "id": "FAKE",
                "sdk": "fake",
                "model": "fake-1",
                "params": {"script": SCRIPT},
                "capabilities": {"vision": True},
            }
        ],
        "storage": {"events": {"backend": "jsonl", "path": "data"}},
        "telemetry": {"logging": {"level": "CRITICAL"}},
        "security": {
            "api_keys": [
                {"id": "lecteur", "hash": fingerprint(LECTEUR), "scopes": ["read"]},
                {
                    "id": "contenu",
                    "hash": fingerprint(CONTENU),
                    "scopes": ["read", "read_content"],
                },
            ]
        },
    }
    (tmp_path / "loom.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    agent = {
        "name": "demo",
        "description": "Produit des fichiers.",
        "main": {"model": "FAKE", "system": "Tu produis."},
        "tools": [
            {"python": "graphique"},
            {"python": "autre_graphique"},
            {"python": "exporter"},
        ],
    }
    (tmp_path / "agents" / "demo.yaml").write_text(yaml.safe_dump(agent), encoding="utf-8")
    return tmp_path / "loom.yaml"


async def produced(path: Path) -> tuple[SessionFile, ...]:
    async with Loom.from_config(path) as loom:
        photo = Attachment(data=PNG, media_type="image/png", name="photo.png")
        await loom.run("demo", "Produis.", session_id=SESSION, attachments=[photo])
        return await loom.session_files(SESSION)


def test_files_come_from_the_journal_once_each(atelier: Path) -> None:
    files = asyncio.run(produced(atelier))
    # La pièce jointe et les deux premiers graphiques ont les mêmes octets : un seul
    # fichier, décrit par sa première production. Les fichiers d'un lot sont rangés
    # dans l'ordre où les outils finissent : l'ordre entre eux n'est pas fixé.
    photo, *rest = files
    assert (photo.name, photo.origin, photo.count) == ("photo.png", "attachment", 3)
    assert photo.tool_name is None and photo.call_id is None
    produits = {f.tool_name or "": f for f in rest}
    assert sorted(produits) == ["autre_graphique", "exporter"]
    devis, autre = produits["exporter"], produits["autre_graphique"]
    assert (devis.name, devis.media_type, devis.size) == ("devis.csv", "text/csv", len(CSV))
    assert devis.file.endswith(".csv") and devis.uri.endswith(devis.file)
    assert (autre.name, autre.count, autre.agent) == ("graphique.png", 1, "demo")


def test_origin_filters_and_unknown_session(atelier: Path) -> None:
    async def go() -> tuple[list[str | None], bool, bool]:
        await produced(atelier)
        async with Loom.from_config(atelier) as loom:
            only = await loom.session_files(SESSION, origin=["attachment"])
            with pytest.raises(UnknownSession):
                await loom.session_files(SessionId("inconnue"))
            with pytest.raises(ArtifactNotFound):
                await loom.session_file(SESSION, "absent.png")
            _, data = await loom.session_file(SESSION, only[0].file)
        return [f.name for f in only], data == PNG, True

    names, same, _ = asyncio.run(go())
    assert names == ["photo.png"] and same


def test_cli_lists_and_extracts_under_real_names(
    atelier: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    asyncio.run(produced(atelier))
    capsys.readouterr()
    assert main(["--config", str(atelier), "sessions", "files", SESSION]) == 0
    listed = capsys.readouterr().out
    assert "devis.csv  (text/csv" in listed and "outil exporter" in listed
    assert "rangé 3 fois" in listed

    out = tmp_path / "extrait"
    assert main(["--config", str(atelier), "sessions", "files", SESSION, "--out", str(out)]) == 0
    assert sorted(p.name for p in out.iterdir()) == ["devis.csv", "graphique.png", "photo.png"]
    assert (out / "devis.csv").read_bytes() == CSV
    assert (out / "graphique.png").read_bytes() == AUTRE_PNG

    # Rien n'est écrasé sans --force, et la commande le dit.
    (out / "devis.csv").write_text("modifié", encoding="utf-8")
    capsys.readouterr()
    assert main(["--config", str(atelier), "sessions", "files", SESSION, "--out", str(out)]) == 1
    assert "non écrasé" in capsys.readouterr().err
    assert (out / "devis.csv").read_text(encoding="utf-8") == "modifié"
    args = ["--config", str(atelier), "sessions", "files", SESSION, "--out", str(out), "--force"]
    assert main(args) == 0
    assert (out / "devis.csv").read_bytes() == CSV

    assert main(["--config", str(atelier), "sessions", "show", SESSION]) == 0
    assert "Fichiers   :" in capsys.readouterr().out


def test_names_collide_and_get_a_suffix() -> None:
    from loom_ia.access.cli.main import (  # pyright: ignore[reportPrivateUsage]
        _extracted_names,  # pyright: ignore[reportPrivateUsage]
    )

    files = asyncio.run(_files_named(["a.png", "a.png", None, "../b.csv", "a.png"]))
    assert _extracted_names(files) == ["a.png", "a-2.png", "f2.bin", "b.csv", "a-3.png"]


async def _files_named(names: list[str | None]) -> tuple[SessionFile, ...]:
    from datetime import UTC, datetime

    from loom_ia.core.model import RunId

    return tuple(
        SessionFile(
            uri=f"artifact://default/s/f{i}.bin",
            file=f"f{i}.bin",
            name=name,
            media_type="application/octet-stream",
            size=1,
            origin="tool_output",
            run_id=RunId("r"),
            stored_at=datetime.now(UTC),
        )
        for i, name in enumerate(names)
    )


pytest.importorskip("fastapi", reason="extra 'http' absent")


@asynccontextmanager
async def serving(path: Path) -> AsyncGenerator[Any]:
    httpx2 = pytest.importorskip("httpx2", reason="client HTTP de test absent")
    from loom_ia.access.http import create_app

    async with Loom.from_config(path) as loom:
        transport = httpx2.ASGITransport(create_app(loom))
        async with httpx2.AsyncClient(transport=transport, base_url="http://loom.test") as http:
            yield http


async def test_rest_lists_masks_names_and_downloads_with_read_content(atelier: Path) -> None:
    await produced(atelier)
    async with serving(atelier) as http:
        masked = await http.get(f"/v1/sessions/{SESSION}/files", headers={"X-API-Key": LECTEUR})
        full = await http.get(
            f"/v1/sessions/{SESSION}/files",
            params={"origin": "tool_output"},
            headers={"X-API-Key": CONTENU},
        )
        devis = next(f for f in full.json() if f["name"] == "devis.csv")
        url = f"/v1/sessions/{SESSION}/files/{devis['file']}"
        refused = await http.get(url, headers={"X-API-Key": LECTEUR})
        got = await http.get(url, headers={"X-API-Key": CONTENU})
        absent = await http.get(
            f"/v1/sessions/{SESSION}/files/absent.png", headers={"X-API-Key": CONTENU}
        )
        elsewhere = await http.get(
            f"/v1/sessions/autre/files/{devis['file']}", headers={"X-API-Key": CONTENU}
        )

    assert masked.status_code == 200
    assert [f["name"] for f in masked.json()] == [None, None, None]
    # Filtrée, la liste décrit chaque fichier par sa première production de cette origine.
    assert sorted((f["name"], f["count"]) for f in full.json()) == [
        ("devis.csv", 1),
        ("graphique.png", 1),
        ("graphique.png", 2),
    ]
    assert refused.status_code == 403
    assert got.status_code == 200 and got.content == CSV
    assert got.headers["content-type"].startswith("text/csv")
    assert got.headers["content-disposition"] == "attachment; filename*=UTF-8''devis.csv"
    # Un nom qui n'est pas dans la session, ou le bon nom demandé ailleurs : 404.
    assert absent.status_code == 404 and elsewhere.status_code == 404
