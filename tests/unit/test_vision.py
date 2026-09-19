# SPDX-License-Identifier: Apache-2.0
"""Images : pièces jointes, résolution selon les capacités, rôle vision (G1, C4, #14, J2.3)."""

from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import JsonValue

from loom_ia.access.api import Loom
from loom_ia.access.cli import main
from loom_ia.adapters.artifacts import InMemoryArtifactStore
from loom_ia.adapters.models.fake import FakeModel
from loom_ia.adapters.stores import InMemoryEventStore
from loom_ia.config import load_config
from loom_ia.core.events import ArtifactStored, Event, ModelResponded, UserMessage
from loom_ia.core.model import (
    DEFAULT_TENANT,
    MOVED_IMAGES,
    ArtifactRefBlock,
    Attachment,
    AttachmentError,
    InlineDataBlock,
    Message,
    ModelCapabilities,
    ModelRequest,
    ModelSpec,
    RetryPolicy,
    RunId,
    RunStatus,
    SessionId,
    TextBlock,
    ToolDefinition,
    ToolOutput,
    ToolResultBlock,
    artifact_uri,
)
from loom_ia.core.ports import ArtifactStore, EventStore, ModelError, complete
from loom_ia.engine import (
    REFS_HINT,
    MediaResolver,
    RoleDefinition,
    RoleTool,
    RunContext,
    ToolExecutor,
    begin_run,
    drive,
)
from loom_ia.testing import ScriptedModel, tool_call_message

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
MENTION = "[image jointe photo.png (image/png, 32 octets) : non visible par ce modèle]"
BLIND = ModelSpec(id="MAIN", sdk="fake", model="main-1", retry=RetryPolicy(initial_delay=0))
VISION = ModelSpec(
    id="VISION",
    sdk="fake",
    model="vision-1",
    capabilities=ModelCapabilities(vision=True),
    retry=RetryPolicy(initial_delay=0),
)


async def stored(store: ArtifactStore, data: bytes = PNG) -> ArtifactRefBlock:
    uri = artifact_uri("default", "s1", data, "image/png")
    await store.put(uri, data)
    return ArtifactRefBlock(uri=uri, media_type="image/png", size=len(data), name="photo.png")


def asking(*blocks: Any) -> ModelRequest:
    return ModelRequest(
        model_id="m", messages=(Message(role="user", blocks=(TextBlock(text="Regarde"), *blocks)),)
    )


# --- Résolution des références -------------------------------------------------


async def test_blind_models_get_a_mention() -> None:
    files = InMemoryArtifactStore()
    image = await stored(files)
    pdf = ArtifactRefBlock(
        uri="artifact://default/s1/x.pdf", media_type="application/pdf", size=2048, name="devis.pdf"
    )
    request = asking(image, pdf)
    resolved = await MediaResolver(BLIND, files).resolve(request)
    assert resolved.messages[0].blocks[1:] == (
        TextBlock(text=MENTION),
        TextBlock(text="[fichier devis.pdf (application/pdf, 2 Ko)]"),
    )
    assert MediaResolver(BLIND, files).images(request) == 0
    # Un modèle vision ne reçoit que les images ; le reste reste une mention.
    seen = await MediaResolver(VISION, files).resolve(request)
    assert seen.messages[0].blocks[1:] == (
        InlineDataBlock(media_type="image/png", data=PNG, name="photo.png"),
        TextBlock(text="[fichier devis.pdf (application/pdf, 2 Ko)]"),
    )
    assert MediaResolver(VISION, files).images(request) == 1
    # La requête d'origine n'est pas touchée, et sans référence rien n'est copié.
    assert request.messages[0].blocks[1] == image
    plain = ModelRequest(model_id="m", messages=(Message.user("a"),))
    assert await MediaResolver(VISION, files).resolve(plain) is plain


@pytest.mark.parametrize(
    ("capabilities", "stored_file", "message"),
    [
        (
            {"image_formats": ("jpeg",)},
            True,
            r"format png refusé par le modèle VISION \(formats acceptés : jpeg\)",
        ),
        ({"max_image_bytes": 10}, True, "32 octets, au-delà des 10 acceptés"),
        ({}, False, "introuvable dans le stockage"),
    ],
)
async def test_unusable_images_fail_before_the_call(
    capabilities: dict[str, Any], stored_file: bool, message: str
) -> None:
    files = InMemoryArtifactStore()
    image = await stored(files) if stored_file else await stored(InMemoryArtifactStore())
    spec = VISION.model_copy(
        update={"capabilities": ModelCapabilities(vision=True, **capabilities)}
    )
    with pytest.raises(ModelError, match=message) as caught:
        await MediaResolver(spec, files).resolve(asking(image))
    assert caught.value.kind == "invalid_request"
    with pytest.raises(ModelError, match="aucun stockage d'artefacts"):
        await MediaResolver(VISION, None).resolve(asking(image))


async def test_tool_images_follow_the_results_when_needed() -> None:
    files = InMemoryArtifactStore()
    image = await stored(files)
    messages = (
        Message.user("Trace la courbe."),
        tool_call_message(("c1", "tracer", {}), ("c2", "compter", {})),
        Message(
            role="tool",
            blocks=(
                ToolResultBlock(
                    call_id="c1", output=ToolOutput(blocks=(TextBlock(text="voici"), image))
                ),
            ),
        ),
        Message(role="tool", blocks=(ToolResultBlock(call_id="c2", output=ToolOutput.text("3")),)),
    )
    request = ModelRequest(model_id="m", messages=messages)
    inline = InlineDataBlock(media_type="image/png", data=PNG, name="photo.png")

    moved = await MediaResolver(VISION, files).resolve(request)
    assert [m.role for m in moved.messages] == ["user", "assistant", "tool", "tool", "user"]
    first = moved.messages[2].blocks[0]
    assert isinstance(first, ToolResultBlock)
    assert first.output.blocks[1] == TextBlock(
        text="[image photo.png (image/png, 32 octets) : dans le message qui suit]"
    )
    assert moved.messages[4].blocks == (TextBlock(text=MOVED_IMAGES), inline)

    capable = VISION.model_copy(
        update={"capabilities": ModelCapabilities(vision=True, tool_result_media=True)}
    )
    kept = await MediaResolver(capable, files).resolve(request)
    assert len(kept.messages) == 4
    result = kept.messages[2].blocks[0]
    assert isinstance(result, ToolResultBlock) and result.output.blocks[1] == inline

    blind = await MediaResolver(BLIND, files).resolve(request)
    result = blind.messages[2].blocks[0]
    assert isinstance(result, ToolResultBlock)
    assert result.output.blocks[1] == TextBlock(
        text="[image renvoyée photo.png (image/png, 32 octets) : non visible par ce modèle]"
    )


# --- Pièces jointes d'un run ---------------------------------------------------


def context(
    store: EventStore,
    model: ScriptedModel,
    *tools: object,
    artifacts: ArtifactStore | None,
    spec: ModelSpec = BLIND,
) -> RunContext:
    executor = ToolExecutor(list(tools), artifacts=artifacts)  # pyright: ignore[reportArgumentType]
    return RunContext(agent="demo", store=store, model=model, model_spec=spec, tools=executor)


async def journal(store: EventStore, run_id: RunId) -> list[Event]:
    return await store.read(DEFAULT_TENANT, SessionId(run_id))


async def test_attachments_are_stored_then_joined_to_the_request() -> None:
    events, files = InMemoryEventStore(), InMemoryArtifactStore()
    main = ScriptedModel(Message.assistant("Je ne vois pas les images."))
    ctx = context(events, main, artifacts=files)
    photo = Attachment(data=PNG, media_type="image/png", name="photo.png")
    started = await begin_run(ctx, "Que montre la photo ?", attachments=[photo])

    written = await journal(events, started.run_id)
    assert [e.type for e in written] == ["run.started", "artifact.stored", "message.user"]
    record = written[1].payload
    assert isinstance(record, ArtifactStored)
    assert (record.origin, record.media_type, record.size, record.name, record.call_id) == (
        "attachment",
        "image/png",
        len(PNG),
        "photo.png",
        None,
    )
    assert await files.get(record.uri) == PNG
    assert record.uri.startswith(f"artifact://default/{started.run_id}/")
    request = written[2].payload
    assert isinstance(request, UserMessage)
    assert request.message.blocks == (
        TextBlock(text="Que montre la photo ?"),
        ArtifactRefBlock(uri=record.uri, media_type="image/png", size=len(PNG), name="photo.png"),
    )

    state = await drive(ctx, started.run_id)
    assert state.status is RunStatus.COMPLETED
    assert [a.uri for a in state.attachments] == [record.uri]
    [sent] = main.requests
    assert sent.messages[0].blocks[1] == TextBlock(text=MENTION)
    # L'empreinte porte sur la requête avant résolution : sans les octets.
    [answer] = [
        e.payload
        for e in await journal(events, started.run_id)
        if isinstance(e.payload, ModelResponded)
    ]
    unresolved = sent.model_copy(update={"messages": (request.message,)})
    assert answer.request_hash == unresolved.request_hash() != sent.request_hash()


async def test_refused_attachments_write_nothing() -> None:
    events = InMemoryEventStore()
    ctx = context(events, ScriptedModel(), artifacts=InMemoryArtifactStore())
    with pytest.raises(AttachmentError, match=r"devis\.pdf : format non reconnu"):
        await begin_run(
            ctx,
            "Voici",
            attachments=[Attachment(data=b"%PDF-1.7", name="devis.pdf")],
            run_id=RunId("r1"),
        )
    assert await journal(events, RunId("r1")) == []
    without = context(events, ScriptedModel(), artifacts=None)
    with pytest.raises(ValueError, match="aucun stockage d'artefacts"):
        await begin_run(without, "Voici", attachments=[Attachment(data=PNG)], run_id=RunId("r1"))
    assert await journal(events, RunId("r1")) == []


# --- Rôle vision -----------------------------------------------------------------


def vision_role(model: ScriptedModel) -> RoleTool:
    definition = RoleDefinition(
        name="decrire_image",
        description="Décrit les images jointes.",
        system="Tu décris.",
        input_schema={
            "type": "object",
            "properties": {"consigne": {"type": "string"}},
            "required": ["consigne"],
        },
        context=("attachments",),
    )
    return RoleTool(definition, model, VISION)


async def test_vision_role_is_masked_without_attachments() -> None:
    events = InMemoryEventStore()
    main = ScriptedModel(
        tool_call_message(("c1", "decrire_image", {"consigne": "Décris."})),
        Message.assistant("Rien à voir."),
    )
    eye = ScriptedModel()
    ctx = context(events, main, vision_role(eye), artifacts=InMemoryArtifactStore())
    started = await begin_run(ctx, "Bonjour")
    state = await drive(ctx, started.run_id)

    assert state.status is RunStatus.COMPLETED
    # Ni le rôle vision ni artifact_read : rien à regarder, rien de déporté.
    assert main.requests[0].tools == ()
    # Aucun rôle proposé : ni consigne $ref ni marques [result:n].
    assert main.requests[1].system == ""
    result = state.messages[2].blocks[0]
    assert isinstance(result, ToolResultBlock) and result.output.is_error
    assert result.output.as_text == "Outil inconnu : 'decrire_image'. Outils disponibles : aucun."
    assert eye.requests == []


async def test_vision_role_sees_the_attachments() -> None:
    events, files = InMemoryEventStore(), InMemoryArtifactStore()
    main = ScriptedModel(
        tool_call_message(("c1", "decrire_image", {"consigne": "Décris."})),
        Message.assistant("La photo montre un carré bleu."),
    )
    eye = ScriptedModel(Message.assistant("Un carré bleu."))
    ctx = context(events, main, vision_role(eye), artifacts=files)
    photo = Attachment(data=PNG, name="photo.png")
    started = await begin_run(ctx, "Que montre la photo ?", attachments=[photo])
    state = await drive(ctx, started.run_id)

    assert state.output == Message.assistant("La photo montre un carré bleu.")
    offered = main.requests[0].tools
    assert [t.name for t in offered] == ["decrire_image"]
    assert offered[0].description.endswith(
        "Reçoit déjà, inutile de les transmettre : les pièces jointes de la demande (images)."
    )
    # L'orchestrateur ne voit pas l'image ; le rôle la reçoit après son texte.
    assert main.requests[0].messages[0].blocks[1] == TextBlock(text=MENTION)
    # Le rôle est proposé : consigne $ref et résultats marqués.
    assert main.requests[1].system == REFS_HINT
    marked = main.requests[1].messages[2].blocks[0]
    assert isinstance(marked, ToolResultBlock)
    assert marked.output.as_text == "[result:1]\nUn carré bleu."
    [seen] = eye.requests
    text = (
        "<attachments>\n- photo.png (image/png, 32 octets)\n</attachments>\n\n"
        '<arguments>\n{"consigne": "Décris."}\n</arguments>'
    )
    assert seen.messages == (
        Message(
            role="user",
            blocks=(
                TextBlock(text=text),
                InlineDataBlock(media_type="image/png", data=PNG, name="photo.png"),
            ),
        ),
    )
    [role_answer] = [
        e.payload
        for e in await journal(events, started.run_id)
        if isinstance(e.payload, ModelResponded) and e.role == "decrire_image"
    ]
    [ref] = [b for b in state.messages[0].blocks if isinstance(b, ArtifactRefBlock)]
    unresolved = seen.model_copy(
        update={"messages": (Message(role="user", blocks=(TextBlock(text=text), ref)),)}
    )
    assert role_answer.request_hash == unresolved.request_hash()


async def test_vision_role_template_lists_the_attachments() -> None:
    from loom_ia.core.template import Template

    eye = ScriptedModel(Message.assistant("Un carré."))
    definition = RoleDefinition(
        name="voir",
        description="Voit.",
        template=Template.parse("Fichiers :\n{{ context.attachments }}"),
        context=("attachments",),
    )
    main = ScriptedModel(tool_call_message(("c1", "voir", {})), Message.assistant("Vu."))
    ctx = context(
        InMemoryEventStore(),
        main,
        RoleTool(definition, eye, VISION),
        artifacts=InMemoryArtifactStore(),
    )
    started = await begin_run(ctx, "?", attachments=[Attachment(data=PNG, name="photo.png")])
    await drive(ctx, started.run_id)
    assert eye.requests[0].messages[0].blocks[0] == TextBlock(
        text="Fichiers :\n- photo.png (image/png, 32 octets)"
    )


# --- Modèle simulé ---------------------------------------------------------------


async def test_fake_script_follows_the_offered_tools() -> None:
    script: JsonValue = [
        {"text": "Je regarde.", "with_tool": "voir", "tool_calls": [{"name": "voir"}]},
        {"text": "Sans image.", "without_tool": "voir"},
        {"text": "Avec image.", "with_tool": "voir"},
    ]
    model = FakeModel(ModelSpec(id="F", sdk="fake", model="f", params={"script": script}))
    tool = ToolDefinition(name="voir", description="Voit.")

    offered = ModelRequest(model_id="f", messages=(Message.user("a"),), tools=(tool,))
    first = await complete(model, offered)
    assert [c.name for c in first.message.tool_calls] == ["voir"]
    result = Message(
        role="tool",
        blocks=(ToolResultBlock(call_id=first.message.tool_calls[0].call_id, output=ToolOutput()),),
    )
    # Le message qui porte les images des résultats n'est pas une nouvelle demande.
    moved = Message(role="user", blocks=(TextBlock(text=MOVED_IMAGES),))
    follow_up = offered.model_copy(
        update={"messages": (Message.user("a"), first.message, result, moved)}
    )
    assert (await complete(model, follow_up)).message.text == "Avec image."

    alone = ModelRequest(model_id="f", messages=(Message.user("a"),))
    assert (await complete(model, alone)).message.text == "Sans image."


# --- Par la façade et la CLI -----------------------------------------------------


def write(tmp_path: Path) -> Path:
    script: list[dict[str, Any]] = [
        {
            "text": "Je regarde.",
            "with_tool": "decrire_image",
            "tool_calls": [{"name": "decrire_image", "arguments": {"consigne": "Décris."}}],
        },
        {"text": "La photo montre un carré bleu.", "with_tool": "decrire_image"},
        {"text": "Aucune image jointe.", "without_tool": "decrire_image"},
    ]
    config: dict[str, Any] = {
        "version": 1,
        "models": [
            {"id": "MAIN", "sdk": "fake", "model": "main-1", "params": {"script": script}},
            {
                "id": "VISION",
                "sdk": "fake",
                "model": "vision-1",
                "capabilities": {"vision": True},
                "params": {"script": [{"text": "Un carré bleu."}]},
            },
        ],
        "storage": {"events": {"backend": "jsonl", "path": "data"}},
        "telemetry": {"logging": {"level": "WARNING"}},
    }
    agent: dict[str, Any] = {
        "name": "demo",
        "main": {"model": "MAIN", "system": "Tu réponds."},
        "roles": [
            {
                "name": "decrire_image",
                "description": "Décrit les images jointes.",
                "model": "VISION",
                "system": "Tu décris.",
                "input_schema": {
                    "type": "object",
                    "properties": {"consigne": {"type": "string"}},
                    "required": ["consigne"],
                },
                "context": ["attachments"],
            }
        ],
    }
    (tmp_path / "agents").mkdir()
    (tmp_path / "loom.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    (tmp_path / "agents" / "demo.yaml").write_text(yaml.safe_dump(agent), encoding="utf-8")
    return tmp_path / "loom.yaml"


async def test_loom_runs_with_attachments(tmp_path: Path) -> None:
    async with Loom(load_config(write(tmp_path))) as loom:
        photo = Attachment(data=PNG, name="photo.png")
        result = await loom.run("demo", "Que montre la photo ?", attachments=[photo])
        [record] = result.artifacts
        assert await loom.artifact(record.uri) == PNG
        plain = await loom.run("demo", "Bonjour")

    assert (result.text, result.iterations) == ("La photo montre un carré bleu.", 2)
    assert (record.origin, record.name) == ("attachment", "photo.png")
    assert result.produced == ()
    stored = list((tmp_path / "data" / ".artifacts" / "default" / result.run_id).iterdir())
    assert [p.read_bytes() for p in stored] == [PNG]
    assert (plain.text, plain.artifacts) == ("Aucune image jointe.", ())


def test_cli_attaches_images(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = str(write(tmp_path))
    photo = tmp_path / "photo.png"
    photo.write_bytes(PNG)
    pdf = tmp_path / "devis.pdf"
    pdf.write_bytes(b"%PDF-1.7")

    assert main(["--config", path, "validate"]) == 0
    assert f"Artefacts  : local ({tmp_path / 'data' / '.artifacts'})" in capsys.readouterr().out

    assert main(["--config", path, "run", "demo", "Que montre ?", "--attach", str(photo)]) == 0
    assert capsys.readouterr().out.strip() == "La photo montre un carré bleu."

    assert main(["--config", path, "run", "demo", "?", "--attach", str(photo), "--stream"]) == 0
    assert "· pièce jointe rangée : photo.png (image/png, 32 octets)" in capsys.readouterr().err

    assert main(["--config", path, "run", "demo", "?", "--attach", str(pdf)]) == 2
    assert "Demande refusée : devis.pdf : format non reconnu" in capsys.readouterr().err
    assert main(["--config", path, "run", "demo", "?", "--attach", str(tmp_path / "x.png")]) == 2
    assert "Pièce jointe illisible" in capsys.readouterr().err
