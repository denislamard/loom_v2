# SPDX-License-Identifier: Apache-2.0
"""Résultats riches et gros résultats : fichiers produits, déport, artifact_read (#16, G3)."""

import json

from pydantic import JsonValue

from loom_ia.adapters.artifacts import InMemoryArtifactStore
from loom_ia.adapters.stores import InMemoryEventStore
from loom_ia.core.events import ArtifactStored, Event, ToolCompleted
from loom_ia.core.model import (
    DEFAULT_TENANT,
    ArtifactRefBlock,
    Message,
    ModelRequest,
    ModelSpec,
    RetryPolicy,
    RunState,
    RunStatus,
    SessionId,
    TextBlock,
    ToolOutput,
    ToolResultBlock,
)
from loom_ia.core.ports import ArtifactStore, EventStore
from loom_ia.engine import (
    ARTIFACT_READ,
    RoleDefinition,
    RoleTool,
    RunContext,
    ToolExecutor,
    ToolResults,
    begin_run,
    drive,
)
from loom_ia.engine.offload import preview
from loom_ia.testing import ScriptedModel, tool_call_message
from loom_ia.tools import ConfiguredTool, Image, tool

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
SPEC = ModelSpec(id="MAIN", sdk="fake", model="main-1", retry=RetryPolicy(initial_delay=0))
LONG = "".join(f"ligne {n:05d}\n" for n in range(3000))
LINES: JsonValue = {"devis": "D-42", "lignes": [{"n": n, "libelle": "x" * 20} for n in range(500)]}


@tool
def graphique() -> Image:
    """Trace un graphique."""
    return Image(PNG, name="graphe.png")


@tool
def journal_brut() -> str:
    """Renvoie un long journal."""
    return LONG


@tool
def devis() -> JsonValue:
    """Renvoie un gros devis."""
    return LINES


def context(
    model: ScriptedModel,
    *tools: object,
    artifacts: ArtifactStore | None,
    store: EventStore | None = None,
    offload_over: int | None = 10_000,
) -> RunContext:
    executor = ToolExecutor(
        list(tools),  # pyright: ignore[reportArgumentType]
        artifacts=artifacts,
        offload_over=offload_over,
    )
    return RunContext(
        agent="demo",
        store=store or InMemoryEventStore(),
        model=model,
        model_spec=SPEC,
        tools=executor,
    )


async def run(ctx: RunContext, prompt: str = "Vas-y.") -> tuple[RunState, list[Event]]:
    started = await begin_run(ctx, prompt)
    state = await drive(ctx, started.run_id)
    events = await ctx.store.read(DEFAULT_TENANT, SessionId(started.run_id))
    return state, events


def results(state: RunState) -> list[ToolOutput]:
    return [
        block.output
        for message in state.messages
        for block in message.blocks
        if isinstance(block, ToolResultBlock)
    ]


# --- Fichiers produits -----------------------------------------------------------


async def test_images_from_tools_are_stored() -> None:
    files = InMemoryArtifactStore()
    main = ScriptedModel(tool_call_message(("c1", "graphique", {})), Message.assistant("Voilà."))
    state, events = await run(context(main, graphique, artifacts=files))

    assert [e.type for e in events if e.category in {"tool", "artifact"}] == [
        "tool.called",
        "artifact.stored",
        "tool.completed",
    ]
    stored = next(e for e in events if isinstance(e.payload, ArtifactStored))
    completed = next(e for e in events if isinstance(e.payload, ToolCompleted))
    assert isinstance(stored.payload, ArtifactStored)
    assert (stored.payload.origin, stored.payload.call_id, stored.span_id) == (
        "tool_output",
        "c1",
        completed.span_id,
    )
    uri = stored.payload.uri
    assert await files.get(uri) == PNG
    [output] = results(state)
    assert output.blocks == (
        ArtifactRefBlock(uri=uri, media_type="image/png", size=len(PNG), name="graphe.png"),
    )
    assert output.artifacts == (uri,)
    assert [(a.uri, a.origin) for a in state.artifacts] == [(uri, "tool_output")]
    # Le modèle orchestrateur, sans vision, n'en reçoit qu'une mention.
    shown = main.requests[1].messages[2].blocks[0]
    assert isinstance(shown, ToolResultBlock)
    assert shown.output.as_text == (
        "[image renvoyée graphe.png (image/png, 32 octets) : non visible par ce modèle]"
    )


async def test_files_without_store_become_a_mention() -> None:
    main = ScriptedModel(tool_call_message(("c1", "graphique", {})), Message.assistant("Voilà."))
    state, events = await run(context(main, graphique, artifacts=None))
    assert not any(isinstance(e.payload, ArtifactStored) for e in events)
    [output] = results(state)
    assert output.blocks == (
        TextBlock(
            text="[fichier image/png de 32 octets non conservé : aucun stockage d'artefacts]"
        ),
    )


async def test_store_failures_become_tool_errors() -> None:
    class Broken(InMemoryArtifactStore):
        async def put(self, uri: str, data: bytes) -> None:
            raise OSError("disque plein")

    main = ScriptedModel(tool_call_message(("c1", "graphique", {})), Message.assistant("Tant pis."))
    state, _ = await run(context(main, graphique, artifacts=Broken()))
    [output] = results(state)
    assert output.is_error
    assert output.as_text == (
        "Résultat de l'outil graphique perdu : échec du stockage d'artefacts "
        "(OSError: disque plein)."
    )


# --- Déport ---------------------------------------------------------------------


async def test_long_text_is_offloaded_and_read_back() -> None:
    files = InMemoryArtifactStore()
    main = ScriptedModel(
        tool_call_message(("c1", "journal_brut", {})),
        lambda request: tool_call_message(
            ("c2", ARTIFACT_READ, {"ref": _offloaded(request), "offset": 12, "limit": 24})
        ),
        Message.assistant("Lu."),
    )
    state, events = await run(context(main, journal_brut, artifacts=files))
    assert state.status is RunStatus.COMPLETED

    stored = [e.payload for e in events if isinstance(e.payload, ArtifactStored)]
    [offload] = stored
    assert (offload.origin, offload.media_type, offload.name) == (
        "offload",
        "text/plain",
        "journal_brut.txt",
    )
    assert (await files.get(offload.uri)).decode() == LONG
    first, read = results(state)
    assert first.offloaded == offload.uri
    [text] = first.blocks
    assert isinstance(text, TextBlock)
    assert text.text.startswith(LONG[:2000] + "…\n\n[Résultat déporté : 36000 caractères (texte)")
    assert f'outil artifact_read avec ref="{offload.uri}"' in text.text
    completed = [e for e in events if isinstance(e.payload, ToolCompleted)]
    assert completed[0].facets["offloaded"] is True
    assert "offloaded" not in completed[1].facets

    # artifact_read n'apparaît qu'après le premier déport, et n'est jamais déporté.
    assert [t.name for t in main.requests[0].tools] == ["journal_brut"]
    assert [t.name for t in main.requests[1].tools] == ["journal_brut", ARTIFACT_READ]
    assert (
        read.as_text
        == "[Caractères 12 à 36 sur 36000 ; suite : offset=36]\nligne 00001\nligne 00002\n"
    )


def _offloaded(request: ModelRequest, index: int = -1) -> str:
    """Référence du résultat déporté porté par le message ``index`` de la requête."""
    result = request.messages[index].blocks[0]
    assert isinstance(result, ToolResultBlock) and result.output.offloaded is not None
    return result.output.offloaded


async def test_artifact_read_checks_its_reference() -> None:
    main = ScriptedModel(
        tool_call_message(("c1", "journal_brut", {})),
        tool_call_message(
            ("c2", ARTIFACT_READ, {"ref": "artifact://default/x/y.txt"}),
        ),
        lambda request: tool_call_message(
            ("c3", ARTIFACT_READ, {"ref": _offloaded(request, 2), "offset": 99_999})
        ),
        Message.assistant("Fini."),
    )
    state, _ = await run(context(main, journal_brut, artifacts=InMemoryArtifactStore()))
    _, unknown, beyond = results(state)
    assert unknown.is_error
    assert unknown.as_text.startswith("Référence inconnue : 'artifact://default/x/y.txt'.")
    assert beyond.as_text == "[Rien à lire : offset 99999 au-delà de la fin (36000).]"


async def test_offloaded_json_keeps_its_structure_and_its_reference() -> None:
    files = InMemoryArtifactStore()
    counted: list[int] = []

    @tool
    def recevoir(devis: dict[str, JsonValue]) -> str:
        """Reçoit un devis."""
        counted.append(len(json.dumps(devis)))
        return "reçu"

    writer = ScriptedModel(Message.assistant("Relance rédigée."))
    definition = RoleDefinition(
        name="rediger",
        description="Rédige.",
        context=(ToolResults(tools=("devis",)),),
    )
    main = ScriptedModel(
        tool_call_message(("c1", "devis", {})),
        tool_call_message(
            ("c2", "recevoir", {"devis": {"$ref": "result:1"}}), ("c3", "rediger", {})
        ),
        Message.assistant("Fait."),
    )
    role = RoleTool(definition, writer, SPEC)
    state, _ = await run(context(main, devis, recevoir, role, artifacts=files))

    first = results(state)[0]
    assert first.data is None and first.offloaded is not None
    assert first.offloaded.endswith(".json")
    [text] = first.blocks
    assert isinstance(text, TextBlock)
    assert text.text.startswith(
        'Objet JSON, 2 clé(s) :\n- devis : "D-42"\n- lignes : liste de 500 élément(s)\n\n'
        "[Résultat déporté : "
    )
    # Avec des rôles, le modèle apprend qu'il peut transmettre le tout par $ref.
    assert "sa référence $ref" in text.text
    # $ref et tool_results transmettent le contenu complet, relu dans le stockage.
    assert counted == [len(json.dumps(LINES))]
    [request] = writer.requests
    assert json.dumps(LINES, ensure_ascii=False) in request.messages[0].text


async def test_without_store_long_results_are_truncated() -> None:
    main = ScriptedModel(tool_call_message(("c1", "journal_brut", {})), Message.assistant("Ok."))
    state, events = await run(context(main, journal_brut, artifacts=None, offload_over=100))
    [output] = results(state)
    assert output.offloaded is None
    assert output.as_text == (
        LONG[:100] + "\n\n[Résultat tronqué : 100 caractères montrés sur 36000, "
        "aucun stockage d'artefacts pour conserver le reste.]"
    )
    assert not any(isinstance(e.payload, ArtifactStored) for e in events)
    assert ARTIFACT_READ not in [t.name for t in main.requests[1].tools]


async def test_thresholds() -> None:
    files = InMemoryArtifactStore()
    # Le seuil de l'outil l'emporte sur celui de l'exécuteur ; None désactive le déport.
    patient = journal_brut.spec.model_copy(update={"offload_over": 100_000})
    main = ScriptedModel(tool_call_message(("c1", "journal_brut", {})), Message.assistant("Ok."))
    state, _ = await run(
        context(main, ConfiguredTool(tool=journal_brut, spec=patient), artifacts=files)
    )
    assert results(state)[0].offloaded is None and len(files) == 0

    main = ScriptedModel(tool_call_message(("c1", "journal_brut", {})), Message.assistant("Ok."))
    state, _ = await run(context(main, journal_brut, artifacts=files, offload_over=None))
    assert results(state)[0].as_text == LONG


async def test_terminal_outputs_are_never_offloaded() -> None:
    writer = ScriptedModel(Message.assistant(LONG))
    definition = RoleDefinition(
        name="rediger",
        description="Rédige.",
        input_schema={"type": "object", "properties": {"sujet": {"type": "string"}}},
        terminal=True,
    )
    main = ScriptedModel(tool_call_message(("c1", "rediger", {"sujet": "tout"})))
    files = InMemoryArtifactStore()
    state, _ = await run(context(main, RoleTool(definition, writer, SPEC), artifacts=files))
    assert state.output is not None and state.output.text == LONG
    assert len(files) == 0


def test_previews() -> None:
    assert preview("court") == "court"
    assert preview(list(range(10))) == "Liste JSON de 10 élément(s) ; les premiers :\n- 0\n- 1\n- 2"
    many: JsonValue = {f"k{n}": n for n in range(25)}
    shown = preview(many)
    assert shown.startswith("Objet JSON, 25 clé(s) :\n- k0 : 0\n")
    assert shown.endswith("- k19 : 19\n- … et 5 autre(s) clé(s)")
    long_text: JsonValue = {"texte": "é" * 100, "objet": {"a": 1}, "vrai": True}
    assert preview(long_text) == (
        'Objet JSON, 3 clé(s) :\n- texte : texte de 100 caractères, "' + "é" * 80 + '"…\n'
        "- objet : objet à 1 clé(s)\n- vrai : true"
    )
    assert preview(42) == "42"


async def test_journal_keeps_no_offloaded_content() -> None:
    main = ScriptedModel(tool_call_message(("c1", "journal_brut", {})), Message.assistant("Ok."))
    ctx = context(main, journal_brut, artifacts=InMemoryArtifactStore())
    _, events = await run(ctx)
    completed = next(e for e in events if isinstance(e.payload, ToolCompleted))
    assert isinstance(completed.payload, ToolCompleted)
    assert completed.payload.size < 4_000
    assert LONG not in completed.model_dump_json()
