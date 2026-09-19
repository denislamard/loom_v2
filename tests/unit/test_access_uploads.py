# SPDX-License-Identifier: Apache-2.0
"""Pièces jointes par l'API REST (multipart) et par le serveur MCP (J2.5)."""

import base64
import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from conftest import ANSWER, PNG, QUESTION, ConfigFactory

from loom_ia.access import Loom
from loom_ia.config import ConfigError, load_config
from loom_ia.core.events import RunStarted
from loom_ia.core.model import Attachment, AttachmentError, RunId, artifact_uri

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 24
PDF = b"%PDF-1.7 ..."
LIMITS: dict[str, Any] = {"attachments": {"max_bytes": 1000, "max_files": 2}}


async def test_python_access_limits_the_number_of_attachments(demo: ConfigFactory) -> None:
    async with Loom.from_config(demo(execution=LIMITS)) as loom:
        with pytest.raises(AttachmentError, match="3 pièces jointes, au-delà de la limite de 2"):
            await loom.run("demo", QUESTION, attachments=[Attachment(data=PNG)] * 3)


def test_mcp_access_config(demo: ConfigFactory, tmp_path: Path) -> None:
    config = load_config(demo(server={"mcp": {"file_roots": ["photos", "/srv/images"]}}))
    assert config.server.mcp.file_roots == (tmp_path / "photos", Path("/srv/images"))
    assert load_config(demo()).server.mcp.file_roots == ()
    assert load_config(demo()).execution.attachments.max_files == 10
    with pytest.raises(ConfigError, match="J5"):
        load_config(demo(server={"mcp": {"http": True}}))


# --- API REST ----------------------------------------------------------------------------


@asynccontextmanager
async def rest(path: Path) -> AsyncGenerator[tuple[Loom, Any]]:
    """Une instance et un client HTTP branché sur son application."""
    pytest.importorskip("fastapi", reason="extra 'http' absent")
    client = pytest.importorskip("httpx2", reason="client HTTP de test absent")
    from loom_ia.access.http import create_app

    async with Loom.from_config(path) as loom:
        transport = client.ASGITransport(create_app(loom))
        async with client.AsyncClient(transport=transport, base_url="http://loom.test") as http:
            yield loom, http


async def test_multipart_attaches_images(demo: ConfigFactory) -> None:
    files = [
        ("attachments", ("photo.png", PNG, "image/png")),
        # Type inconnu du client : la signature décide.
        ("attachments", ("carte.jpg", JPEG, "application/octet-stream")),
    ]
    fields = {"message": QUESTION, "user_id": "denis", "metadata": json.dumps({"canal": "web"})}
    async with rest(demo()) as (loom, http):
        posted = await http.post("/v1/agents/demo/runs", data=fields, files=files)
        body = posted.json()
        events = await loom.events(RunId(body["run_id"]))
        stored = await loom.artifact(body["artifacts"][0]["uri"])

    assert posted.status_code == 201 and body["text"] == ANSWER
    assert [(a["name"], a["media_type"], a["origin"]) for a in body["artifacts"]] == [
        ("photo.png", "image/png", "attachment"),
        ("carte.jpg", "image/jpeg", "attachment"),
    ]
    assert stored == PNG
    [started] = [e.payload for e in events if isinstance(e.payload, RunStarted)]
    assert (started.context.user_id, started.context.metadata) == ("denis", {"canal": "web"})


async def test_multipart_refusals(demo: ConfigFactory) -> None:
    ask = {"message": QUESTION}

    def part(name: str, data: bytes, kind: str = "image/png") -> tuple[str, Any]:
        return ("attachments", (name, data, kind))

    async with rest(demo(execution=LIMITS)) as (_, http):
        url = "/v1/agents/demo/runs"
        cases = {
            "pdf": await http.post(url, data=ask, files=[part("devis.pdf", PDF, "")]),
            "annonce": await http.post(url, data=ask, files=[part("a.png", JPEG)]),
            "taille": await http.post(url, data=ask, files=[part("gros.png", PNG * 40)]),
            "nombre": await http.post(url, data=ask, files=[part("a.png", PNG)] * 3),
            "champ": await http.post(url, data=ask, files=[("photo", ("a.png", PNG))]),
            "texte": await http.post(url, data={**ask, "attachments": "photo.png"}),
            "metadata": await http.post(url, data={**ask, "metadata": "{"}, files=[part("a", PNG)]),
            "message": await http.post(url, data={"x": "1"}, files=[part("a.png", PNG)]),
            "double": await http.post(
                url, data={"message": [QUESTION, QUESTION]}, files=[part("a.png", PNG)]
            ),
        }

    assert {name: r.status_code for name, r in cases.items()} == dict.fromkeys(cases, 422)
    details = {
        name: json.dumps(r.json()["detail"], ensure_ascii=False) for name, r in cases.items()
    }
    assert "devis.pdf : format non reconnu" in details["pdf"]
    assert "annoncé image/png, mais le contenu est image/jpeg" in details["annonce"]
    assert "gros.png : 1280 octets, au-delà de la limite de 1000" in details["taille"]
    assert "3 pièces jointes, au-delà de la limite de 2" in details["nombre"]
    assert "fichier inattendu" in details["champ"]
    assert "un fichier est attendu" in details["texte"]
    assert "JSON invalide" in details["metadata"]
    assert "message" in details["message"]
    assert "champ répété" in details["double"]


async def test_an_oversized_upload_is_refused_on_its_header(demo: ConfigFactory) -> None:
    limits = {"attachments": {"max_bytes": 1000, "max_files": 1}}
    big = [("attachments", ("gros.png", PNG + b"\x00" * 300_000, "image/png"))]
    async with rest(demo(execution=limits)) as (_, http):
        response = await http.post("/v1/agents/demo/runs", data={"message": QUESTION}, files=big)
        plain = await http.post(
            "/v1/agents/demo/runs", content=QUESTION, headers={"content-type": "text/plain"}
        )

    assert response.status_code == 413 and "au-delà de la limite" in response.json()["detail"]
    assert plain.status_code == 415 and "text/plain" in plain.json()["detail"]


async def test_openapi_describes_both_bodies(demo: ConfigFactory) -> None:
    async with rest(demo()) as (_, http):
        schema = (await http.get("/openapi.json")).json()

    body = schema["paths"]["/v1/agents/{name}/runs"]["post"]["requestBody"]
    assert set(body["content"]) == {"application/json", "multipart/form-data"}
    multipart = body["content"]["multipart/form-data"]["schema"]
    assert multipart["properties"]["attachments"]["items"]["format"] == "binary"


# --- Serveur MCP -------------------------------------------------------------------------


@asynccontextmanager
async def mcp(path: Path) -> AsyncGenerator[tuple[Loom, Any]]:
    """Une instance servie en MCP, et un client branché dessus."""
    pytest.importorskip("mcp", reason="extra 'mcp' absent")
    from mcp.shared.memory import create_connected_server_and_client_session as connected

    from loom_ia.access.mcp_server import create_server

    async with Loom.from_config(path) as loom:
        async with connected(create_server(loom)) as client:
            yield loom, client


def image(data: bytes, **extra: str) -> dict[str, str]:
    return {"type": "image", "data": base64.b64encode(data).decode(), **extra}


def link(uri: str, **extra: str) -> dict[str, str]:
    return {"type": "resource_link", "uri": uri, **extra}


def error(result: Any) -> str:
    assert result.isError
    return " ".join(part.text for part in result.content)


async def ask(client: Any, *attachments: dict[str, str]) -> Any:
    return await client.call_tool("demo", {"message": QUESTION, "attachments": list(attachments)})


async def test_mcp_tool_takes_attachments(demo: ConfigFactory) -> None:
    async with mcp(demo()) as (loom, client):
        listed = await client.list_tools()
        first = await ask(client, image(PNG, mimeType="image/png", name="photo.png"))
        uri = first.structuredContent["artifacts"][0]["uri"]
        # Le même fichier, par son lien, dans un nouvel appel.
        again = await ask(client, link(uri))
        other = again.structuredContent["artifacts"][0]
        copied = await loom.artifact(other["uri"])

    [tool] = [tool for tool in listed.tools if tool.name == "demo"]
    assert "attachments" in tool.inputSchema["properties"]
    assert not first.isError and first.structuredContent["text"] == ANSWER
    assert first.structuredContent["artifacts"] == [
        {
            "uri": uri,
            "media_type": "image/png",
            "size": len(PNG),
            "name": "photo.png",
            "origin": "attachment",
        }
    ]
    assert other["uri"] != uri and other["name"] == uri.rsplit("/", 1)[1]
    assert copied == PNG


async def test_mcp_artifact_links_stay_within_the_tenant(demo: ConfigFactory) -> None:
    foreign = artifact_uri("autre", "s1", PNG, "image/png")
    async with mcp(demo()) as (loom, client):
        await loom.artifacts.put(foreign, PNG)
        results = {
            "foreign": await ask(client, link(foreign)),
            "missing": await ask(client, link(artifact_uri("default", "s1", JPEG, "image/jpeg"))),
            "invalid": await ask(client, link("artifact://a/b")),
        }

    messages = {name: error(result) for name, result in results.items()}
    assert "artefact introuvable" in messages["foreign"]
    assert "artefact introuvable" in messages["missing"]
    assert "URI d'artefact invalide" in messages["invalid"]


async def test_mcp_file_links_are_read_under_the_roots(demo: ConfigFactory, tmp_path: Path) -> None:
    photos = tmp_path / "photos"
    photos.mkdir()
    (photos / "ma photo.png").write_bytes(PNG)
    (photos / "gros.png").write_bytes(PNG * 100)
    (tmp_path / "secret.png").write_bytes(PNG)
    (photos / "fuite.png").symlink_to(tmp_path / "secret.png")
    path = demo(server={"mcp": {"file_roots": ["photos"]}}, execution=LIMITS)
    async with mcp(path) as (_, client):
        read = await ask(client, link((photos / "ma photo.png").as_uri()))
        results = {
            "outside": await ask(client, link((tmp_path / "secret.png").as_uri())),
            "absent_outside": await ask(client, link((tmp_path / "absent.png").as_uri())),
            "absent": await ask(client, link((photos / "absent.png").as_uri())),
            "symlink": await ask(client, link((photos / "fuite.png").as_uri())),
            "folder": await ask(client, link(photos.as_uri())),
            "big": await ask(client, link((photos / "gros.png").as_uri())),
            "host": await ask(client, link("file://serveur/photos/a.png")),
            "relative": await ask(client, link("file:photos/a.png")),
        }

    assert not read.isError
    [stored] = read.structuredContent["artifacts"]
    assert (stored["name"], stored["media_type"]) == ("ma photo.png", "image/png")
    messages = {name: error(result) for name, result in results.items()}
    assert "hors des dossiers autorisés" in messages["outside"]
    # Rien n'est dit de ce qui existe hors des dossiers.
    assert "hors des dossiers autorisés" in messages["absent_outside"]
    assert "fichier introuvable" in messages["absent"]
    assert "hors des dossiers autorisés" in messages["symlink"]
    assert "n'est pas un fichier" in messages["folder"]
    assert "gros.png : 3200 octets, au-delà de la limite de 1000" in messages["big"]
    assert "hôte 'serveur' non pris en charge" in messages["host"]
    assert "lien 'file' non pris en charge" in messages["relative"]


async def test_mcp_refusals(demo: ConfigFactory, tmp_path: Path) -> None:
    (tmp_path / "photo.png").write_bytes(PNG)
    async with mcp(demo(execution=LIMITS)) as (_, client):
        results = {
            "no_roots": await ask(client, link((tmp_path / "photo.png").as_uri())),
            "scheme": await ask(client, link("https://exemple.fr/photo.png")),
            "base64": await ask(client, {"type": "image", "data": "pas du base64 !"}),
            "count": await ask(client, *[image(PNG)] * 3),
            "content": await ask(client, image(PDF, name="devis.pdf")),
            "schema": await client.call_tool(
                "demo", {"message": QUESTION, "attachments": [{"type": "image"}]}
            ),
        }

    messages = {name: error(result) for name, result in results.items()}
    assert "liens file:// refusés, aucun dossier autorisé" in messages["no_roots"]
    assert "lien 'https' non pris en charge" in messages["scheme"]
    assert "base64 invalide" in messages["base64"]
    assert "3 pièces jointes, au-delà de la limite de 2" in messages["count"]
    assert "devis.pdf : format non reconnu" in messages["content"]
    assert "Input validation error" in messages["schema"]
