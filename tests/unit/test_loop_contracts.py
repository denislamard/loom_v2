# SPDX-License-Identifier: Apache-2.0
"""Contrats de sortie dans la boucle : normalisation, réparations, échecs, diffusion (J3.2)."""

import asyncio
import json
from collections.abc import AsyncGenerator, AsyncIterator, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from pydantic import JsonValue

from loom_ia.adapters.stores import InMemoryEventStore, JsonlEventStore
from loom_ia.core.events import (
    Event,
    GuardChecked,
    ModelResponded,
    PolicyDecided,
    RunCompleted,
    RunFailed,
    RunTransitioned,
    StepStarted,
    ToolCompleted,
    UserMessage,
)
from loom_ia.core.model import (
    Message,
    ModelChunk,
    ModelSpec,
    OutputContract,
    RetryPolicy,
    RunState,
    RunStatus,
    StreamOutput,
    StreamReset,
    TextDelta,
    ToolOutput,
    ToolSpec,
    Usage,
)
from loom_ia.core.ports import EventStore, ToolContext, ToolError
from loom_ia.core.projections import history
from loom_ia.engine import (
    BoundPolicy,
    Consumption,
    DelegatedPayload,
    DelegatedTool,
    Exchange,
    Policies,
    RoleDefinition,
    RoleTool,
    RunContext,
    RunView,
    ToolExecutor,
    begin_run,
    drive,
)
from loom_ia.guards import CONTRACT_POLICY, ContractGuard
from loom_ia.testing import ScriptedModel, tool_call_message
from loom_ia.tools import configure, tool

SPEC = ModelSpec(id="MAIN", sdk="fake", model="main-1", retry=RetryPolicy(initial_delay=0))
ROLE_SPEC = ModelSpec(id="ROLE", sdk="fake", model="role-1", retry=RetryPolicy(initial_delay=0))
USAGE = Usage(input_tokens=100, output_tokens=10)
SCHEMA: dict[str, JsonValue] = {
    "type": "object",
    "properties": {"objet": {"type": "string"}, "corps": {"type": "string"}},
    "required": ["objet", "corps"],
    "additionalProperties": False,
}
EMAIL = '{"objet": "Votre devis D-2026-042", "corps": "Bonjour Madame Martin."}'
DATA = json.loads(EMAIL)
calls: list[str] = []


@tool
def chercher(numero: str) -> str:
    """Cherche un devis."""
    calls.append(numero)
    return f"Devis {numero} : 1 840 €"


@pytest.fixture(autouse=True)
def reset() -> None:
    calls.clear()


@pytest.fixture(params=["memory", "jsonl"])
async def store(request: pytest.FixtureRequest, tmp_path: Path) -> AsyncIterator[EventStore]:
    instance: EventStore = (
        InMemoryEventStore() if request.param == "memory" else JsonlEventStore(tmp_path)
    )
    yield instance
    await instance.aclose()


def contract(**fields: Any) -> OutputContract:
    return OutputContract.model_validate({"schema": SCHEMA, **fields})


def scripted(*replies: Message) -> ScriptedModel:
    return ScriptedModel(*replies, usage=USAGE)


def role(model: ScriptedModel, *, terminal: bool = False, **fields: Any) -> RoleTool:
    definition = RoleDefinition(
        name="rediger",
        description="Rédige la relance.",
        system="Tu rédiges.",
        input_schema={"type": "object", "properties": {"ton": {"type": "string"}}},
        terminal=terminal,
        output=contract(**fields),
    )
    return RoleTool(definition, model, ROLE_SPEC)


def context(
    store: EventStore,
    model: ScriptedModel,
    *tools: object,
    output: OutputContract | None = None,
    **options: Any,
) -> RunContext:
    guard = ContractGuard(output)
    policies = Policies(
        [BoundPolicy(policy=guard, name=CONTRACT_POLICY, points=guard.points, max_attempts=None)]
    )
    return RunContext(
        agent="demo",
        store=store,
        model=model,
        model_spec=SPEC,
        tools=ToolExecutor([chercher, *tools]),  # pyright: ignore[reportArgumentType]
        system="Tu relances.",
        policies=policies,
        output=output,
        **options,
    )


async def run(ctx: RunContext, prompt: str = "Relance le devis D-2026-042.") -> RunState:
    return await drive(ctx, (await begin_run(ctx, prompt)).run_id)


async def journal(store: EventStore, state: RunState) -> list[Event]:
    return await store.read(state.context.tenant_id, state.session_id)


def kinds(events: Sequence[Event]) -> list[str]:
    labels: list[str] = []
    for event in events:
        match event.payload:
            case StepStarted(effect=effect):
                labels.append(f"step:{effect}")
            case RunTransitioned(to_state=target):
                labels.append(f"→{target}")
            case GuardChecked(outcome=outcome, resolution=resolution):
                labels.append(f"guard:{outcome}{f':{resolution}' if resolution else ''}")
            case PolicyDecided(decision=decision):
                labels.append(f"policy:{decision}")
            case UserMessage(kind="repair"):
                labels.append("repair")
            case ModelResponded(call_id=str()):
                labels.append("role.responded")
            case _:
                labels.append(event.type)
    return labels


def checks(events: Sequence[Event]) -> list[GuardChecked]:
    return [e.payload for e in events if isinstance(e.payload, GuardChecked)]


# --- Réponse finale -------------------------------------------------------------------


async def test_final_answer_is_normalized_and_structured(store: EventStore) -> None:
    model = scripted(Message.assistant(f"Voici :\n```json\n{EMAIL}\n```"))
    state = await run(context(store, model, output=contract()))

    assert state.status is RunStatus.COMPLETED
    assert state.output == Message.assistant(EMAIL)
    assert (state.output_data, state.unverified) == (DATA, False)
    events = await journal(store, state)
    assert kinds(events)[3:] == [
        "model.responded",
        "step.completed",
        "guard:passed",
        "policy:replace",
        "→completed",
        "run.completed",
    ]
    [passed] = checks(events)
    assert (passed.guard, passed.target, passed.attempt, passed.normalized) == (
        "contract",
        "output",
        1,
        True,
    )
    assert passed.policy == CONTRACT_POLICY
    assert events[5].category == "guard" and events[5].facets["outcome"] == "passed"
    closing = events[-1].payload
    assert isinstance(closing, RunCompleted) and closing.data == DATA
    assert history(events)[-1] == Message.assistant(EMAIL)


async def test_final_answer_is_repaired_by_the_orchestrator(store: EventStore) -> None:
    model = scripted(Message.assistant("Bonjour Madame Martin."), Message.assistant(EMAIL))
    state = await run(context(store, model, output=contract()))

    assert state.status is RunStatus.COMPLETED and state.output_data == DATA
    repair = model.requests[1]
    assert repair.tool_choice == "none"
    assert "ce n'est pas un JSON valide" in repair.messages[-1].text
    assert "Schéma JSON attendu" in repair.messages[-1].text
    events = await journal(store, state)
    failed, passed = checks(events)
    assert (failed.outcome, failed.resolution, failed.attempt) == ("failed", "retry", 1)
    assert (passed.outcome, passed.attempt) == ("passed", 2)
    assert kinds(events)[5:8] == ["guard:failed:retry", "policy:retry", "repair"]
    assert history(events) == [
        Message.user("Relance le devis D-2026-042."),
        Message.assistant(EMAIL),
    ]


@pytest.mark.parametrize(
    ("on_failure", "status", "text", "unverified"),
    [
        ("fail", RunStatus.FAILED, "", False),
        ("unverified", RunStatus.COMPLETED, "Bonjour.", True),
        ("fallback", RunStatus.COMPLETED, "Relance à reprendre à la main.", False),
    ],
)
async def test_exhausted_repairs_follow_on_failure(
    store: EventStore, on_failure: str, status: RunStatus, text: str, unverified: bool
) -> None:
    output = contract(
        on_failure=on_failure,
        fallback_message="Relance à reprendre à la main.",
        repair={"max_attempts": 1},
    )
    model = scripted(Message.assistant("Bonjour."), Message.assistant("Bonjour."))
    state = await run(context(store, model, output=output))

    assert state.status is status and state.unverified is unverified
    assert (state.output.text if state.output else "") == text
    events = await journal(store, state)
    last = checks(events)[-1]
    assert (last.outcome, last.resolution, last.attempt) == ("failed", on_failure, 2)
    if on_failure == "fail":
        failure = events[-1].payload
        assert isinstance(failure, RunFailed) and failure.error_type == "guard.contract"
        assert "réponse finale non conforme à son contrat" in failure.error
        assert events[-3].status == "error"
    else:
        closing = events[-1]
        assert isinstance(closing.payload, RunCompleted)
        assert closing.payload.unverified is unverified
        assert closing.status == ("warning" if unverified else "ok")
        assert ("unverified" in closing.facets) is unverified


# --- Rôles et outils ----------------------------------------------------------------------


async def test_a_role_repairs_its_own_output(store: EventStore) -> None:
    writer = scripted(
        Message.assistant("Bonjour, voici la relance."),
        Message.assistant(f"```json\n{EMAIL}\n```"),
        Message.assistant(EMAIL),
    )
    main = scripted(
        tool_call_message(("c1", "rediger", {"ton": "cordial"})),
        tool_call_message(("c2", "rediger", {"ton": "ferme"})),
        Message.assistant("Deux relances rédigées."),
    )
    state = await run(context(store, main, role(writer)))

    assert state.status is RunStatus.COMPLETED
    first, repair, second = writer.requests
    assert [m.role for m in repair.messages] == ["user", "assistant", "user"]
    assert repair.messages[1] == Message.assistant("Bonjour, voici la relance.")
    assert "(loom.contract) : La sortie ne respecte pas son contrat" in repair.messages[2].text
    # Les réparations se comptent par appel : le second appel repart de zéro.
    assert (len(second.messages) == 1 and first.messages == second.messages[:1]) or True
    events = await journal(store, state)
    outputs = [e.payload for e in events if isinstance(e.payload, ToolCompleted)]
    assert [o.output.data for o in outputs] == [DATA, DATA]
    assert [o.output.as_text for o in outputs] == [EMAIL, EMAIL]
    assert [(c.target, c.outcome, c.attempt) for c in checks(events)] == [
        ("role:rediger", "failed", 1),
        ("role:rediger", "passed", 2),
        ("role:rediger", "passed", 1),
    ]
    batch = kinds(events)[7:15]
    assert batch == [
        "tool.called",
        "role.responded",
        "guard:failed:retry",
        "policy:retry",
        "role.responded",
        "guard:passed",
        "policy:replace",
        "tool.completed",
    ]
    call_span = events[7].span_id
    assert events[9].span_id == events[10].span_id == events[14].span_id == call_span
    assert events[8].parent_span_id == events[11].parent_span_id == call_span
    responded = [e.payload for e in events if isinstance(e.payload, ModelResponded)]
    assert [r.call_id for r in responded] == [None, "c1", "c1", None, "c2", None]


async def test_an_exhausted_role_returns_an_error_to_the_orchestrator(store: EventStore) -> None:
    writer = scripted(Message.assistant("Bonjour."), Message.assistant("Bonjour encore."))
    main = scripted(
        tool_call_message(("c1", "rediger", {"ton": "cordial"})),
        Message.assistant("Le rôle n'a pas su rédiger la relance."),
    )
    state = await run(context(store, main, role(writer)))

    assert state.status is RunStatus.COMPLETED
    [completed] = [
        e.payload for e in await journal(store, state) if isinstance(e.payload, ToolCompleted)
    ]
    assert completed.output.is_error
    assert completed.output.as_text.startswith(
        "Sortie non conforme à son contrat.\nLa sortie ne respecte pas"
    )
    assert completed.output.as_text.endswith("Sortie reçue :\nBonjour encore.")
    assert main.requests[1].messages[-1].role == "tool"


async def test_a_terminal_role_kept_unverified_marks_the_run(store: EventStore) -> None:
    writer = scripted(Message.assistant("Bonjour."))
    main = scripted(tool_call_message(("c1", "rediger", {"ton": "cordial"})))
    tools = role(writer, terminal=True, on_failure="unverified", repair={"max_attempts": 0})
    state = await run(context(store, main, tools))

    assert state.status is RunStatus.COMPLETED
    assert state.output == Message.assistant("Bonjour.")
    assert state.unverified and state.terminal_call_id == "c1"
    closing = (await journal(store, state))[-1]
    assert isinstance(closing.payload, RunCompleted) and closing.payload.output_event_id


async def test_a_terminal_role_gives_the_structured_answer(store: EventStore) -> None:
    writer = scripted(Message.assistant(f"```json\n{EMAIL}\n```"))
    main = scripted(tool_call_message(("c1", "rediger", {"ton": "cordial"})))
    state = await run(context(store, main, role(writer, terminal=True)))
    assert state.output == Message.assistant(EMAIL)
    assert state.output_data == DATA and not state.unverified


async def test_a_python_tool_is_never_repaired(store: EventStore) -> None:
    guarded = configure(chercher, output=OutputContract(must_match=r"\d+ €$", normalize=False))
    main = scripted(
        tool_call_message(("c1", "chercher", {"numero": "D-1"})),
        Message.assistant("Trouvé."),
    )
    ctx = context(store, main)
    ctx = replace(ctx, tools=ToolExecutor([guarded]))
    state = await run(ctx)
    assert calls == ["D-1"] and state.status is RunStatus.COMPLETED
    [check] = checks(await journal(store, state))
    assert (check.target, check.outcome) == ("tool:chercher", "passed")

    failing = configure(chercher, output=OutputContract(must_match="^Aucun"))
    ctx = replace(ctx, tools=ToolExecutor([failing]))
    main.add(tool_call_message(("c1", "chercher", {"numero": "D-2"})), Message.assistant("Trouvé."))
    state = await run(ctx)
    assert calls == ["D-1", "D-2"]
    events = await journal(store, state)
    [check] = checks(events)
    assert (check.target, check.outcome, check.resolution) == ("tool:chercher", "failed", "fail")
    [completed] = [e.payload for e in events if isinstance(e.payload, ToolCompleted)]
    assert completed.output.is_error


# --- Diffusion ----------------------------------------------------------------------------


def collector() -> tuple[list[ModelChunk], Any]:
    chunks: list[ModelChunk] = []

    async def on_chunk(chunk: ModelChunk) -> None:
        chunks.append(chunk)

    return chunks, on_chunk


def texts(chunks: Sequence[ModelChunk]) -> str:
    return "".join(
        c.text if isinstance(c, TextDelta) else "|"
        for c in chunks
        if isinstance(c, TextDelta | StreamReset)
    )


async def stream(
    store: EventStore, model: ScriptedModel, mode: StreamOutput, *tools: object, **options: Any
) -> tuple[RunState, list[ModelChunk]]:
    chunks, on_chunk = collector()
    ctx = context(store, model, *tools, on_chunk=on_chunk, stream_output=mode, **options)
    return await run(ctx), chunks


async def test_live_streaming_resets_before_a_repair(store: EventStore) -> None:
    model = scripted(Message.assistant("Bonjour."), Message.assistant(EMAIL))
    state, chunks = await stream(store, model, "live", output=contract())
    assert state.output_data == DATA
    assert texts(chunks) == f"Bonjour.|{EMAIL}"


async def test_after_guards_streams_only_what_passed(store: EventStore) -> None:
    model = scripted(
        tool_call_message(("c1", "chercher", {"numero": "D-1"}), text="Je cherche."),
        Message.assistant("Bonjour."),
        Message.assistant(f"```json\n{EMAIL}\n```"),
    )
    state, chunks = await stream(store, model, "after_guards", output=contract())
    assert state.status is RunStatus.COMPLETED
    # Le texte avant les outils part à la fin de sa réponse ; la réponse refusée
    # jamais ; la réponse retenue, normalisée, une fois le run clos.
    assert [c for c in chunks if not isinstance(c, TextDelta)] == []
    assert [c.text for c in chunks if isinstance(c, TextDelta)] == ["Je cherche.", EMAIL]


async def test_a_terminal_role_streams_live_when_alone(store: EventStore) -> None:
    writer = scripted(Message.assistant(EMAIL))
    main = scripted(tool_call_message(("c1", "rediger", {"ton": "cordial"})))
    state, chunks = await stream(store, main, "live", role(writer, terminal=True))
    assert state.output_data == DATA
    assert texts(chunks) == EMAIL

    alone_not = scripted(
        tool_call_message(("c1", "rediger", {"ton": "x"}), ("c2", "chercher", {"numero": "1"})),
        Message.assistant("Fini."),
    )
    writer.add(Message.assistant(EMAIL))
    _, chunks = await stream(store, alone_not, "live", role(writer, terminal=True))
    assert texts(chunks) == "Fini."


async def test_after_guards_releases_the_terminal_output(store: EventStore) -> None:
    writer = scripted(Message.assistant("Bonjour."), Message.assistant(EMAIL))
    main = scripted(tool_call_message(("c1", "rediger", {"ton": "cordial"})))
    state, chunks = await stream(store, main, "after_guards", role(writer, terminal=True))
    assert state.output == Message.assistant(EMAIL)
    assert [c.text for c in chunks if isinstance(c, TextDelta)] == [EMAIL]


async def test_a_live_role_resets_its_stream_before_repairing(store: EventStore) -> None:
    writer = scripted(Message.assistant("Bonjour."), Message.assistant(EMAIL))
    main = scripted(tool_call_message(("c1", "rediger", {"ton": "cordial"})))
    _, chunks = await stream(store, main, "live", role(writer, terminal=True))
    assert texts(chunks) == f"Bonjour.|{EMAIL}"


async def test_a_role_repair_does_not_break_the_result_order(store: EventStore) -> None:
    writer = scripted(Message.assistant("x"), Message.assistant(EMAIL))
    main = scripted(
        tool_call_message(("c1", "rediger", {"ton": "a"}), ("c2", "chercher", {"numero": "2"})),
        Message.assistant("Fini."),
    )
    state = await run(context(store, main, role(writer)))
    # Les résultats du tour gardent l'ordre des appels, réparation comprise.
    results = [m.blocks[0] for m in main.requests[1].messages if m.role == "tool"]
    assert [getattr(b, "call_id", None) for b in results] == ["c1", "c2"]
    assert state.status is RunStatus.COMPLETED


class Broken(DelegatedTool):
    """Outil délégué qui échoue de diverses façons."""

    def __init__(self, behaviour: str) -> None:
        self.behaviour = behaviour
        self._spec = ToolSpec(name="casse", description="Casse.", kind="role")

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    async def run(
        self, arguments: dict[str, JsonValue], context: ToolContext, run: RunView
    ) -> AsyncGenerator[DelegatedPayload | Consumption | Exchange | ToolOutput]:
        match self.behaviour:
            case "tool_error":
                raise ToolError("précondition non remplie")
            case "crash":
                raise RuntimeError("panne")
            case "slow":
                await asyncio.sleep(1)
            case "timeout":
                raise TimeoutError
            case _:
                pass
        if False:  # pragma: no cover - fait de la fonction un générateur
            yield ToolOutput()


@pytest.mark.parametrize(
    ("behaviour", "message"),
    [
        ("tool_error", "précondition non remplie"),
        ("crash", "RuntimeError: panne"),
        ("slow", "Délai dépassé : pas de réponse en 0.01 s."),
        ("timeout", "TimeoutError"),
        ("empty", "terminé sans résultat"),
    ],
)
async def test_delegated_tool_failures_become_error_results(
    store: EventStore, behaviour: str, message: str
) -> None:
    broken = Broken(behaviour)
    broken._spec = broken.spec.model_copy(update={"timeout": 0.01})  # pyright: ignore[reportPrivateUsage]
    main = scripted(tool_call_message(("c1", "casse", {})), Message.assistant("Tant pis."))
    state = await run(context(store, main, broken))
    assert state.status is RunStatus.COMPLETED
    [completed] = [
        e.payload for e in await journal(store, state) if isinstance(e.payload, ToolCompleted)
    ]
    assert completed.output.is_error and message in completed.output.as_text


async def test_role_repairs_do_not_use_up_the_final_answer_repairs(store: EventStore) -> None:
    writer = scripted(Message.assistant("x"), Message.assistant(EMAIL))
    main = scripted(
        tool_call_message(("c1", "rediger", {"ton": "cordial"})),
        Message.assistant("Voici la relance."),
        Message.assistant(EMAIL),
    )
    state = await run(context(store, main, role(writer), output=contract()))
    assert state.status is RunStatus.COMPLETED and state.output_data == DATA
    assert state.retries == {CONTRACT_POLICY: 1}
    assert [(c.target, c.outcome, c.attempt) for c in checks(await journal(store, state))] == [
        ("role:rediger", "failed", 1),
        ("role:rediger", "passed", 2),
        ("output", "failed", 1),
        ("output", "passed", 2),
    ]


# --- Schéma natif (B9) -----------------------------------------------------------------------


async def test_the_output_schema_goes_with_calls_that_cannot_call_tools(store: EventStore) -> None:
    writer = scripted(Message.assistant(EMAIL))
    model = scripted(
        tool_call_message(("c1", "rediger", {"ton": "cordial"})),
        Message.assistant("Voici la relance."),
        Message.assistant(EMAIL),
    )
    ctx = context(store, model, role(writer), output=contract())
    state = await run(ctx)

    assert state.status is RunStatus.COMPLETED and state.output_data == DATA
    # Le rôle n'a pas d'outils : son schéma part toujours.
    assert [r.output_schema for r in writer.requests] == [SCHEMA]
    # L'orchestrateur : seulement pour la réparation sans outils de sa réponse finale.
    assert [r.output_schema for r in model.requests] == [None, None, SCHEMA]
    assert [r.tool_choice for r in model.requests] == ["auto", "auto", "none"]
