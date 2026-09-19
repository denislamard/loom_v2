# SPDX-License-Identifier: Apache-2.0
"""Fichiers : pièces jointes, URI d'artefacts, stockages, événement, config (G1, G2, J2.3)."""

from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from loom_ia.adapters.artifacts import InMemoryArtifactStore, LocalArtifactStore
from loom_ia.config import load_config
from loom_ia.core.events import ArtifactStored, RunScope, RunStarted, ToolCompleted, UserMessage
from loom_ia.core.model import (
    ArtifactLocation,
    ArtifactRefBlock,
    Attachment,
    AttachmentError,
    AttachmentPolicy,
    InlineDataBlock,
    Message,
    ModelCapabilities,
    RunId,
    SessionId,
    TextBlock,
    ToolOutput,
    ToolResultBlock,
    artifact_uri,
    extension,
    has_inline_data,
    sniff,
)
from loom_ia.core.ports import ArtifactNotFound, ArtifactStore
from loom_ia.core.projections import fold
from loom_ia.runtime import create_artifact_store
from loom_ia.tools import Image, to_output

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 24
GIF = b"GIF89a" + b"\x00" * 24
WEBP = b"RIFF\x10\x00\x00\x00WEBPVP8 " + b"\x00" * 16


# --- Types reconnus, pièces jointes -------------------------------------------


def test_types_come_from_the_signature() -> None:
    assert [sniff(data) for data in (PNG, JPEG, GIF, WEBP)] == [
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
    ]
    assert sniff(b"%PDF-1.7") is None
    assert sniff(b"") is None
    assert (extension("image/jpeg"), extension("application/json"), extension("x/y")) == (
        "jpg",
        "json",
        "bin",
    )


@pytest.mark.parametrize(
    ("attachment", "message"),
    [
        (Attachment(data=b"", name="vide.png"), "vide.png : fichier vide"),
        (Attachment(data=PNG * 10, name="gros.png"), "au-delà de la limite de 100"),
        (Attachment(data=b"%PDF-1.7 ...", name="devis.pdf"), "devis.pdf : format non reconnu"),
        (
            Attachment(data=PNG, media_type="image/jpeg", name="photo.jpg"),
            "annoncé image/jpeg, mais le contenu est image/png",
        ),
        (Attachment(data=GIF), "pièce jointe : image/gif non accepté"),
    ],
)
def test_attachments_are_checked(attachment: Attachment, message: str) -> None:
    policy = AttachmentPolicy(max_bytes=100, types=("image/png", "image/jpeg"))
    with pytest.raises(AttachmentError, match=message):
        attachment.checked(policy)


def test_accepted_attachment(tmp_path: Path) -> None:
    path = tmp_path / "photo.png"
    path.write_bytes(PNG)
    attachment = Attachment.from_path(path)
    assert (attachment.name, attachment.media_type) == ("photo.png", None)
    assert attachment.checked(AttachmentPolicy()) == "image/png"
    assert Attachment(data=PNG, media_type="image/png").checked(AttachmentPolicy()) == "image/png"


def test_attachment_policy_accepts_images_only() -> None:
    with pytest.raises(ValidationError, match="application/pdf non pris en charge"):
        AttachmentPolicy(types=("application/pdf",))
    with pytest.raises(ValidationError):
        AttachmentPolicy(types=())


# --- URI -----------------------------------------------------------------------


def test_uri_is_addressed_by_content() -> None:
    uri = artifact_uri("default", "s1", PNG, "image/png")
    assert uri.startswith("artifact://default/s1/") and uri.endswith(".png")
    assert artifact_uri("default", "s1", PNG, "image/png") == uri
    assert artifact_uri("default", "s2", PNG, "image/png") != uri
    location = ArtifactLocation.parse(uri)
    assert (location.tenant, location.session) == ("default", "s1")
    assert location.relative_path() == Path("default") / "s1" / location.name


def test_uri_components_are_encoded() -> None:
    uri = artifact_uri("client/a", "../..", PNG, "image/png")
    assert "/../" not in uri
    location = ArtifactLocation.parse(uri)
    assert (location.tenant, location.session) == ("client/a", "../..")
    assert ".." not in location.relative_path().parts
    assert len(location.relative_path().parts) == 3


@pytest.mark.parametrize(
    "uri",
    [
        "file:///etc/passwd",
        "artifact://a/b",
        "artifact://a/b/c/d",
        "artifact://a//x.png",
        "artifact://a/b/.x",
        "artifact://a/b/%2E%2E",
    ],
)
def test_invalid_uris(uri: str) -> None:
    with pytest.raises(ValueError, match="URI d'artefact invalide"):
        ArtifactLocation.parse(uri)


# --- Stockages -----------------------------------------------------------------


@pytest.fixture(params=["memory", "local"])
def artifacts(request: pytest.FixtureRequest, tmp_path: Path) -> ArtifactStore:
    if request.param == "memory":
        return InMemoryArtifactStore()
    return LocalArtifactStore(tmp_path / "artefacts")


async def test_stores_keep_bytes_by_uri(artifacts: ArtifactStore) -> None:
    uri = artifact_uri("default", "s1", PNG, "image/png")
    with pytest.raises(ArtifactNotFound, match="Artefact introuvable"):
        await artifacts.get(uri)
    await artifacts.put(uri, PNG)
    await artifacts.put(uri, PNG)
    assert await artifacts.get(uri) == PNG
    with pytest.raises(ValueError, match="URI d'artefact invalide"):
        await artifacts.put("file:///tmp/x", PNG)
    await artifacts.aclose()


async def test_local_store_layout(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    uri = artifact_uri("default", "s1", JPEG, "image/jpeg")
    await store.put(uri, JPEG)
    path = store.path(uri)
    assert path.parent == tmp_path / "default" / "s1"
    assert path.read_bytes() == JPEG
    assert [p.name for p in path.parent.iterdir()] == [path.name]
    assert repr(store) == f"LocalArtifactStore({str(tmp_path)!r})"
    memory = InMemoryArtifactStore()
    await memory.put(uri, JPEG)
    assert (len(memory), repr(memory)) == (1, "InMemoryArtifactStore(1 fichier(s))")


# --- Journal -------------------------------------------------------------------


def scope() -> RunScope:
    return RunScope(
        tenant_id="default",  # pyright: ignore[reportArgumentType]
        session_id=SessionId("s1"),
        run_id=RunId("r1"),
        root_run_id=RunId("r1"),
        agent="demo",
    )


def test_file_bytes_never_reach_the_journal() -> None:
    inline = InlineDataBlock(media_type="image/png", data=PNG)
    with pytest.raises(ValidationError, match="octets de fichier interdits"):
        UserMessage(message=Message(role="user", blocks=(TextBlock(text="a"), inline)))
    with pytest.raises(ValidationError, match=r"tool\.completed : octets de fichier interdits"):
        ToolCompleted(call_id="c1", tool_name="t", output=ToolOutput(blocks=(inline,)))
    nested = ToolResultBlock(call_id="c1", output=ToolOutput(blocks=(inline,)))
    assert has_inline_data((nested,))
    assert not has_inline_data((TextBlock(text="a"),))
    # En dehors du journal, les octets passent en base64 dans le JSON.
    assert InlineDataBlock.model_validate_json(inline.model_dump_json()) == inline


def test_offloaded_facet_only_when_true() -> None:
    plain = ToolCompleted(call_id="c1", tool_name="t", output=ToolOutput.text("ok"))
    assert "offloaded" not in plain.facets()
    offloaded = ToolCompleted(
        call_id="c1", tool_name="t", output=ToolOutput(offloaded="artifact://d/s/x.txt")
    )
    assert offloaded.facets()["offloaded"] is True


def test_run_state_lists_artifacts_once() -> None:
    uri = artifact_uri("default", "s1", PNG, "image/png")
    stored = ArtifactStored(uri=uri, media_type="image/png", size=len(PNG), origin="attachment")
    draft = scope().draft(stored)
    assert (draft.category, draft.facets) == (
        "artifact",
        {"origin": "attachment", "media_type": "image/png", "size": len(PNG)},
    )
    ref = ArtifactRefBlock(uri=uri, media_type="image/png", size=len(PNG))
    drafts = [
        scope().draft(RunStarted()),
        draft,
        scope().draft(stored),
        scope().draft(
            ArtifactStored(
                uri="artifact://default/s1/a.txt",
                media_type="text/plain",
                size=3,
                origin="offload",
                call_id="c1",
            )
        ),
        scope().draft(UserMessage(message=Message(role="user", blocks=(TextBlock(text="a"), ref)))),
    ]
    state = fold([d.to_event(seq) for seq, d in enumerate(drafts, start=1)], RunId("r1"))
    assert [a.origin for a in state.artifacts] == ["attachment", "offload"]
    assert [a.uri for a in state.attachments] == [uri]
    assert state.offloaded
    assert state.artifact("artifact://default/s1/a.txt") is not None
    assert state.artifact("artifact://default/s1/b.txt") is None


# --- Capacités et config -------------------------------------------------------


def test_vision_needs_base64() -> None:
    assert ModelCapabilities(vision=True).image_input == ("base64",)
    assert ModelCapabilities(vision=True, image_input=("base64", "url")).vision
    with pytest.raises(ValidationError, match="seul l'envoi en base64"):
        ModelCapabilities(vision=True, image_input=("url",))


def write(tmp_path: Path, root: dict[str, Any]) -> Path:
    (tmp_path / "agents").mkdir(exist_ok=True)
    config = {"version": 1, "models": [{"id": "F", "sdk": "fake", "model": "f"}], **root}
    (tmp_path / "loom.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    return tmp_path / "loom.yaml"


def test_artifact_store_follows_the_journal(tmp_path: Path) -> None:
    journal = load_config(
        write(tmp_path, {"storage": {"events": {"backend": "jsonl", "path": "data"}}})
    )
    assert (journal.storage.artifacts_backend, journal.storage.artifacts_path) == (
        "local",
        tmp_path / "data" / ".artifacts",
    )
    store = create_artifact_store(journal)
    assert isinstance(store, LocalArtifactStore) and store.root == tmp_path / "data" / ".artifacts"

    memory = load_config(write(tmp_path, {}))
    assert memory.storage.artifacts_backend == "memory"
    assert isinstance(create_artifact_store(memory), InMemoryArtifactStore)

    chosen = load_config(
        write(tmp_path, {"storage": {"artifacts": {"backend": "local", "path": "fichiers"}}})
    )
    assert chosen.storage.artifacts_path == tmp_path / "fichiers"
    forced = load_config(
        write(
            tmp_path,
            {
                "storage": {
                    "events": {"backend": "jsonl", "path": "data"},
                    "artifacts": {"backend": "memory"},
                }
            },
        )
    )
    assert isinstance(create_artifact_store(forced), InMemoryArtifactStore)
    assert forced.execution.tools.offload_over == 50_000
    assert forced.execution.attachments == AttachmentPolicy()


# --- Outils Python -------------------------------------------------------------


def test_python_tools_return_images(tmp_path: Path) -> None:
    output = to_output(Image(PNG, name="graphe.png"))
    assert output.blocks == (InlineDataBlock(media_type="image/png", data=PNG, name="graphe.png"),)
    # La signature l'emporte ; le type annoncé ne sert qu'à défaut.
    assert to_output(Image(JPEG, media_type="image/png")).blocks[0] == InlineDataBlock(
        media_type="image/jpeg", data=JPEG
    )
    raw = to_output(Image(b"\x00\x01", media_type="application/octet-stream"))
    assert raw.blocks == (InlineDataBlock(media_type="application/octet-stream", data=b"\x00\x01"),)
    with pytest.raises(ValueError, match="format non reconnu"):
        to_output(Image(b"\x00\x01"))
    path = tmp_path / "carte.gif"
    path.write_bytes(GIF)
    assert Image.from_path(path) == Image(GIF, name="carte.gif")
