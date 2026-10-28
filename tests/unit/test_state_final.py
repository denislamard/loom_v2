# SPDX-License-Identifier: Apache-2.0
"""Réponse finale écrite avec des appels de ``loom.state`` (M1, ``docs/state_analyse_moteur.md``).

Essai réel t9 du 28/10 : Haiku écrit sa réponse **et** appelle ``state`` dans la
même réponse ; rappelé, il en réécrit une autre, qui remplaçait la bonne.
"""

from typing import Any

import pytest
from pydantic import JsonValue

from loom_ia.adapters.stores import InMemoryEventStore
from loom_ia.core.events import Event, EventDraft, ModelResponded, PolicyDecided, RunCompleted
from loom_ia.core.model import (
    CallerContext,
    Message,
    ModelSpec,
    OutputContract,
    RunState,
    RunStatus,
    TextDelta,
)
from loom_ia.core.ports import EventStore
from loom_ia.core.projections import history, session_state
from loom_ia.core.state_extract import Extraction, ModelKey
from loom_ia.engine import (
    BoundPolicy,
    Policies,
    RunContext,
    StateKeyRule,
    StateRules,
    ToolExecutor,
    begin_run,
    drive,
)
from loom_ia.guards import CONTRACT_POLICY, ContractGuard
from loom_ia.testing import ScriptedModel, tool_call_message
from loom_ia.tools import tool
from loom_ia.tools_builtin.state import StateTool

SPEC = ModelSpec(id="M", sdk="fake", model="fake")
CLIENT: dict[str, JsonValue] = {
    "type": "object",
    "properties": {"id": {"type": "integer"}, "code": {"type": "string"}},
}


@tool
def chercher(nom: str) -> dict[str, Any]:
    """Cherche des clients."""
    rows = [[261, "C-261", "Renard"], [2, "C-002", "Renault"]]
    return {"columns": ["id", "code", "nom"], "rows": [r for r in rows if r[2] == nom]}


@tool
def calculer(x: int) -> int:
    """Double."""
    return 2 * x


def state_tool(max_value_bytes: int = 1024) -> StateTool:
    key = ModelKey(
        name="client",
        description="Client.",
        schema=CLIENT,
        extraction=Extraction(fields={"id": "id", "code": "code"}),
    )
    return StateTool((key,), max_value_bytes)


def context(
    store: EventStore, model: ScriptedModel, *, max_total: int = 4096, **options: Any
) -> RunContext:
    rules = StateRules(
        {"client": StateKeyRule(schema=CLIENT, writers=frozenset({"model"}))},
        max_total_bytes=max_total,
        model_tool="state",
    )
    executor = ToolExecutor([chercher, calculer, state_tool()], state_rules=rules)
    return RunContext(
        agent="demo", store=store, model=model, model_spec=SPEC, tools=executor, **options
    )


async def run(ctx: RunContext, prompt: str = "Fiche de Renard ?") -> RunState:
    started = await begin_run(ctx, prompt, context=CallerContext(user_id="u1"))
    return await drive(ctx, started.run_id)


SEARCH = tool_call_message(("c1", "chercher", {"nom": "Renard"}))
SET: dict[str, JsonValue] = {"set": "client", "result": "result:1"}
ANSWER = "Léa Renard est la cliente C-261."


def answered_with_state(
    text: str = ANSWER, arguments: dict[str, JsonValue] | None = None
) -> Message:
    return tool_call_message(("c2", "state", {**SET, **(arguments or {})}), text=text)


async def test_the_text_written_with_state_is_the_final_answer() -> None:
    store = InMemoryEventStore()
    model = ScriptedModel(SEARCH, answered_with_state())
    done = await run(context(store, model))
    assert done.status is RunStatus.COMPLETED
    assert done.output is not None and done.output.text == ANSWER
    assert done.output.tool_calls == ()
    # Deux appels de modèle, pas trois : il n'est pas rappelé pour réécrire.
    assert len(model.requests) == 2 and model.remaining == 0
    events = await store.read(done.context.tenant_id, done.session_id)
    assert session_state(events) == {"client": {"id": 261, "code": "C-261"}}


async def test_without_text_the_model_is_called_again() -> None:
    store = InMemoryEventStore()
    model = ScriptedModel(SEARCH, answered_with_state(text=""), Message.assistant(ANSWER))
    done = await run(context(store, model))
    assert done.output == Message.assistant(ANSWER)
    assert len(model.requests) == 3


@pytest.mark.parametrize(
    "arguments", [{"result": "result:9"}, {"set": "inconnue"}], ids=["ref", "clé"]
)
async def test_a_failed_state_call_goes_back_to_the_model(arguments: dict[str, JsonValue]) -> None:
    store = InMemoryEventStore()
    model = ScriptedModel(
        SEARCH, answered_with_state(arguments=arguments), Message.assistant("Corrigé.")
    )
    done = await run(context(store, model))
    assert done.output == Message.assistant("Corrigé.")
    assert len(model.requests) == 3


async def test_a_refused_write_goes_back_to_the_model() -> None:
    """Écriture refusée par le moteur (taille totale) : le modèle doit lire le refus."""
    store = InMemoryEventStore()
    model = ScriptedModel(SEARCH, answered_with_state(), Message.assistant("Noté."))
    done = await run(context(store, model, max_total=5))
    assert done.output == Message.assistant("Noté.")
    assert len(model.requests) == 3


async def test_state_with_another_tool_is_an_ordinary_turn() -> None:
    store = InMemoryEventStore()
    mixed = tool_call_message(("c2", "state", SET), ("c3", "calculer", {"x": 2}), text=ANSWER)
    model = ScriptedModel(SEARCH, mixed, Message.assistant("4."))
    done = await run(context(store, model))
    assert done.output == Message.assistant("4.")
    assert len(model.requests) == 3


async def test_the_next_run_sees_the_answer_once_after_the_tool_result() -> None:
    store = InMemoryEventStore()
    model = ScriptedModel(SEARCH, answered_with_state(), Message.assistant("Rien d'autre."))
    ctx = context(store, model)
    first = await run(ctx)
    events = await store.read(first.context.tenant_id, first.session_id)
    seen = history(events)
    assert [m.role for m in seen] == ["user", "assistant", "tool", "assistant", "tool", "assistant"]
    assert seen[3].tool_calls and not seen[3].text
    assert seen[-1].text == ANSWER
    assert sum(ANSWER in m.text for m in seen) == 1
    started = await begin_run(ctx, "Et après ?", session_id=first.session_id)
    await drive(ctx, started.run_id, session_id=first.session_id)
    request = model.requests[-1]
    assert sum(ANSWER in m.text for m in request.messages) == 1


async def test_after_guards_streams_the_answer_once() -> None:
    chunks: list[object] = []

    async def on_chunk(chunk: object) -> None:
        chunks.append(chunk)

    store = InMemoryEventStore()
    model = ScriptedModel(SEARCH, answered_with_state())
    await run(context(store, model, on_chunk=on_chunk, stream_output="after_guards"))
    texts = [c.text for c in chunks if isinstance(c, TextDelta)]
    assert "".join(texts) == ANSWER


async def test_a_structured_answer_is_checked_like_any_final_answer() -> None:
    store = InMemoryEventStore()
    contract = OutputContract.model_validate(
        {
            "schema": {"type": "object", "required": ["code"]},
            "repair": {"max_attempts": 1},
            "on_failure": "fail",
        }
    )
    model = ScriptedModel(
        SEARCH, answered_with_state(text="pas du JSON"), Message.assistant('{"code": "C-261"}')
    )
    guard = ContractGuard(contract)
    policies = Policies(
        [BoundPolicy(policy=guard, name=CONTRACT_POLICY, points=guard.points, max_attempts=None)]
    )
    ctx = context(store, model, output=contract, policies=policies)
    done = await run(ctx)
    assert done.output == Message.assistant('{"code": "C-261"}')
    assert done.output_data == {"code": "C-261"}
    events = await store.read(done.context.tenant_id, done.session_id)
    # La réponse refusée et son tour sont retirés : leur écriture du state aussi (D2).
    assert any(isinstance(e.payload, PolicyDecided) for e in events)
    assert session_state(events) == {}


async def test_the_closing_is_written_on_resume() -> None:
    store = InMemoryEventStore()
    done = await run(context(store, ScriptedModel(SEARCH, answered_with_state())))
    events = await store.read(done.context.tenant_id, done.session_id)
    assert isinstance(events[-1].payload, RunCompleted)
    crashed = InMemoryEventStore()
    drafts = [EventDraft.model_validate(e.model_dump(exclude={"seq"})) for e in events[:-1]]
    await crashed.append(drafts, expected_seq=0)
    resumed = await drive(context(crashed, ScriptedModel()), done.run_id)
    assert resumed.output is not None and resumed.output.text == ANSWER
    again: list[Event] = await crashed.read(done.context.tenant_id, done.session_id)
    assert again[-1].payload == events[-1].payload


async def test_a_run_without_loom_state_is_untouched() -> None:
    """Sans ``loom.state`` monté, un texte avec un appel d'outil reste un tour ordinaire."""
    store = InMemoryEventStore()
    model = ScriptedModel(
        tool_call_message(("c1", "calculer", {"x": 2}), text="Je calcule."),
        Message.assistant("4."),
    )
    ctx = RunContext(
        agent="demo", store=store, model=model, model_spec=SPEC, tools=ToolExecutor([calculer])
    )
    done = await run(ctx)
    assert done.output == Message.assistant("4.")
    assert len(model.requests) == 2
    assert [
        type(e.payload) for e in await store.read(done.context.tenant_id, done.session_id)
    ].count(ModelResponded) == 2
