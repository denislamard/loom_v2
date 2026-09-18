# SPDX-License-Identifier: Apache-2.0
"""Rôles délégués dans la boucle : message, journal, $ref, outil terminal, reprise (J2.1)."""

import logging
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from pydantic import JsonValue

from loom_ia.adapters.stores import InMemoryEventStore, JsonlEventStore
from loom_ia.core.events import (
    Event,
    EventDraft,
    ModelResponded,
    ModelRetried,
    RunCompleted,
    RunTransitioned,
    ToolCalled,
    ToolCompleted,
)
from loom_ia.core.model import (
    MAIN_ROLE,
    TERMINAL_HINT,
    CallerContext,
    Message,
    ModelSpec,
    Pricing,
    RetryPolicy,
    RunState,
    RunStatus,
    SessionId,
    TextBlock,
    ToolOutput,
    ToolResultBlock,
    Usage,
)
from loom_ia.core.ports import EventStore, ModelError
from loom_ia.core.projections import TERMINAL_MARKER, ProjectionError, apply, fold
from loom_ia.core.template import Template
from loom_ia.engine import (
    REFS_HINT,
    RoleDefinition,
    RoleTool,
    RunContext,
    RunView,
    ToolExecutor,
    ToolResults,
    begin_run,
    drive,
)
from loom_ia.testing import RunJournal, ScriptedModel, tool_call_message
from loom_ia.tools import tool

USAGE = Usage(input_tokens=1_000, output_tokens=100)
ROLE_USAGE = Usage(input_tokens=200, output_tokens=50)
SPEC = ModelSpec(
    id="MAIN",
    sdk="fake",
    model="main-1",
    pricing=Pricing(input=1.0, output=5.0),
    retry=RetryPolicy(initial_delay=0),
)
ROLE_SPEC = ModelSpec(
    id="ROLE",
    sdk="fake",
    model="role-1",
    max_tokens=800,
    params={"temperature": 0.7, "top_p": 1},
    pricing=Pricing(input=2.0, output=10.0),
    retry=RetryPolicy(initial_delay=0),
)
SCHEMA: dict[str, JsonValue] = {
    "type": "object",
    "properties": {"ton": {"type": "string"}, "calcul": {}},
    "required": ["ton"],
}


@tool
def calculer(expr: str) -> str:
    """Évalue une expression arithmétique."""
    return str(eval(expr, {"__builtins__": {}}))


@pytest.fixture(params=["memory", "jsonl"])
async def store(request: pytest.FixtureRequest, tmp_path: Path) -> AsyncIterator[EventStore]:
    instance: EventStore = (
        InMemoryEventStore() if request.param == "memory" else JsonlEventStore(tmp_path)
    )
    yield instance
    await instance.aclose()


def role(model: ScriptedModel, *, name: str = "rediger", **changes: object) -> RoleTool:
    options: dict[str, object] = {
        "name": name,
        "description": "Rédige une réponse soignée.",
        "system": "Tu rédiges.",
        "input_schema": SCHEMA,
        "context": ("user_input", ToolResults(tools=("calculer",))),
        "max_tokens": 300,
        "params": {"temperature": 0},
    }
    definition = RoleDefinition(**(options | changes))  # pyright: ignore[reportArgumentType]
    return RoleTool(definition, model, ROLE_SPEC)


def context(
    store: EventStore, model: ScriptedModel, *tools: object, **options: object
) -> RunContext:
    executor = ToolExecutor([calculer, *tools])  # pyright: ignore[reportArgumentType]
    return RunContext(
        agent="demo",
        store=store,
        model=model,
        model_spec=SPEC,
        tools=executor,
        system="Tu orchestres.",
        **options,  # pyright: ignore[reportArgumentType]
    )


def main_model(*replies: Message) -> ScriptedModel:
    return ScriptedModel(*replies, usage=USAGE)


def role_model(*replies: Message | Exception) -> ScriptedModel:
    return ScriptedModel(*replies, usage=ROLE_USAGE)


async def run(
    ctx: RunContext, prompt: str = "Combien font 12 fois 7 plus 3 ? Réponds poliment."
) -> RunState:
    started = await begin_run(ctx, prompt, context=CallerContext(user_id="u1"))
    return await drive(ctx, started.run_id)


async def journal(store: EventStore, state: RunState) -> list[Event]:
    return await store.read(state.context.tenant_id, state.session_id)


def types(events: list[Event]) -> list[str]:
    return [e.type for e in events]


def payloads[P](events: list[Event], kind: type[P]) -> list[P]:
    return [e.payload for e in events if isinstance(e.payload, kind)]


# --- Délégation --------------------------------------------------------------


async def test_role_round_trip(store: EventStore) -> None:
    main = main_model(
        tool_call_message(("c1", "calculer", {"expr": "12*7+3"})),
        tool_call_message(("c2", "rediger", {"ton": "poli", "calcul": {"$ref": "result:1"}})),
        Message.assistant("Voici : 87, avec plaisir."),
    )
    writer = role_model(Message.assistant("Cela fait 87, avec plaisir."))
    state = await run(context(store, main, role(writer)))

    assert state.status is RunStatus.COMPLETED
    assert state.output == Message.assistant("Voici : 87, avec plaisir.")
    # Seuls les appels de l'orchestrateur comptent comme itérations ; le rôle paie sa part.
    assert state.iterations == 3
    assert state.usage == USAGE + USAGE + USAGE + ROLE_USAGE
    assert state.cost_usd == pytest.approx(3 * 0.0015 + 0.0009)

    # Le rôle ne reçoit que son message : prompt, réglages et contexte déclaré.
    [request] = writer.requests
    assert (request.model_id, request.system, request.max_tokens) == ("role-1", "Tu rédiges.", 300)
    assert request.params == {"temperature": 0, "top_p": 1}
    assert request.tools == ()
    assert request.messages == (
        Message.user(
            "<user_input>\nCombien font 12 fois 7 plus 3 ? Réponds poliment.\n</user_input>\n\n"
            '<tool_result tool="calculer" ref="result:1">\n87\n</tool_result>\n\n'
            '<arguments>\n{"ton": "poli", "calcul": "87"}\n</arguments>'
        ),
    )

    events = await journal(store, state)
    batch = events[types(events).index("tool.called", 12) - 1 :]
    assert types(batch)[:5] == [
        "step.started",
        "tool.called",
        "model.responded",
        "tool.completed",
        "step.completed",
    ]
    step, called, responded, completed = batch[:4]
    assert isinstance(called.payload, ToolCalled)
    assert called.payload.arguments == {"ton": "poli", "calcul": {"$ref": "result:1"}}
    assert (called.payload.refs, called.payload.tool_kind) == (("result:1",), "role")
    assert isinstance(responded.payload, ModelResponded)
    assert (responded.payload.call_id, responded.role) == ("c2", "rediger")
    assert responded.payload.model_id == "role-1"
    assert responded.payload.cost_usd == pytest.approx(0.0009)
    assert responded.payload.request_hash == request.request_hash()
    # Span de l'appel d'outil, puis celui de l'appel de modèle du rôle, en dessous.
    assert called.parent_span_id == step.span_id
    assert completed.span_id == called.span_id != responded.span_id
    assert responded.parent_span_id == called.span_id
    assert isinstance(completed.payload, ToolCompleted)
    assert completed.payload.output == ToolOutput.text("Cela fait 87, avec plaisir.")

    # Les appels de l'orchestrateur sont attribués à ``main``.
    main_responses = [e for e in events if isinstance(e.payload, ModelResponded) and e != responded]
    assert {e.role for e in main_responses} == {MAIN_ROLE}
    assert all(
        isinstance(e.payload, ModelResponded) and e.payload.call_id is None for e in main_responses
    )


async def test_orchestrator_sees_references_when_the_agent_has_roles(store: EventStore) -> None:
    main = main_model(
        tool_call_message(("c1", "calculer", {"expr": "1+1"})),
        Message.assistant("2"),
    )
    await run(context(store, main, role(role_model())))

    first, second = main.requests
    assert first.system == f"Tu orchestres.\n\n{REFS_HINT}"
    [result] = [b for m in second.messages for b in m.blocks if isinstance(b, ToolResultBlock)]
    assert result.output.blocks == (TextBlock(text="[result:1]"), TextBlock(text="2"))

    # Sans rôle, rien ne change par rapport au J1.
    plain = main_model(
        tool_call_message(("c1", "calculer", {"expr": "1+1"})), Message.assistant("2")
    )
    await run(context(InMemoryEventStore(), plain))
    assert plain.requests[0].system == "Tu orchestres."
    [result] = [
        b for m in plain.requests[1].messages for b in m.blocks if isinstance(b, ToolResultBlock)
    ]
    assert result.output == ToolOutput.text("2")


def test_role_description_lists_the_context_it_already_receives() -> None:
    both = role(
        role_model(),
        context=("user_input", ToolResults(tools=("calculer", "chercher")), "caller_context"),
        terminal=True,
    )
    assert both.spec.description == (
        "Rédige une réponse soignée.\n\n"
        "Reçoit déjà, inutile de les transmettre : la demande de l'utilisateur ; "
        "les résultats de calculer, chercher ; le contexte de l'appelant."
    )
    # Ce que voit le modèle : la mention du contexte, puis celle de l'outil terminal.
    assert both.spec.definition().description == f"{both.spec.description}\n\n{TERMINAL_HINT}"
    assert role(role_model(), context=()).spec.description == "Rédige une réponse soignée."


async def test_serialized_reference_reaches_the_role(store: EventStore) -> None:
    # Cas relevé avec MiniMax-M3 : la référence arrive sérialisée en chaîne.
    main = main_model(
        tool_call_message(("c1", "calculer", {"expr": "12*7+3"})),
        tool_call_message(("c2", "rediger", {"ton": "poli", "calcul": '{"$ref": "result:1"}'})),
        Message.assistant("Fait."),
    )
    writer = role_model(Message.assistant("87."))
    state = await run(context(store, main, role(writer, context=())))

    [called] = [p for p in payloads(await journal(store, state), ToolCalled) if p.call_id == "c2"]
    assert called.refs == ("result:1",)
    assert called.arguments["calcul"] == '{"$ref": "result:1"}'
    assert writer.requests[0].messages == (
        Message.user('<arguments>\n{"ton": "poli", "calcul": "87"}\n</arguments>'),
    )


async def test_arguments_not_in_the_schema_are_refused(store: EventStore) -> None:
    main = main_model(
        tool_call_message(("c1", "rediger", {"ton": "poli", "devis": "D-42"})),
        tool_call_message(("c2", "libre", {"ton": "poli", "devis": "D-42"})),
        Message.assistant("Fait."),
    )
    strict = role_model()
    # Un rôle qui déclare lui-même additionalProperties garde son choix.
    open_schema: dict[str, JsonValue] = {**SCHEMA, "additionalProperties": True}
    loose = role_model(Message.assistant("Accepté."))
    state = await run(
        context(
            store,
            main,
            role(strict, context=()),
            role(loose, name="libre", input_schema=open_schema, context=()),
        )
    )

    assert strict.requests == []
    outputs = {p.call_id: p.output for p in payloads(await journal(store, state), ToolCompleted)}
    assert outputs["c1"].is_error
    assert "Additional properties are not allowed ('devis' was unexpected)" in outputs["c1"].as_text
    assert outputs["c2"] == ToolOutput.text("Accepté.")
    rediger = next(d for d in main.requests[0].tools if d.name == "rediger")
    assert rediger.input_schema["additionalProperties"] is False


async def test_role_with_a_template(store: EventStore) -> None:
    main = main_model(
        tool_call_message(("c1", "calculer", {"expr": "2*3"})),
        tool_call_message(("c2", "calculer", {"expr": "4*5"})),
        tool_call_message(("c3", "rediger", {"ton": "sec"})),
        Message.assistant("Fait."),
    )
    writer = role_model(Message.assistant("6 et 20."))
    template = Template.parse(
        "Ton : {{ args.ton }}\nDemande : {{ context.user_input }}\n"
        "Résultats :\n{{ context.tool_results.calculer }}\n"
        "Client : {{ context.caller_context.user_id }}"
    )
    tools = role(
        writer,
        template=template,
        context=("user_input", ToolResults(tools=("calculer",)), "caller_context"),
    )
    await run(context(store, main, tools), "Calcule.")

    [request] = writer.requests
    assert request.messages == (
        Message.user("Ton : sec\nDemande : Calcule.\nRésultats :\n6\n\n20\nClient : u1"),
    )


async def test_role_needs_the_declared_tool_results(store: EventStore) -> None:
    main = main_model(
        tool_call_message(("c1", "rediger", {"ton": "poli"})),
        Message.assistant("Je calcule d'abord."),
    )
    writer = role_model()
    state = await run(context(store, main, role(writer)))

    assert writer.requests == []
    events = await journal(store, state)
    assert "tool.called" not in types(events)
    [refused] = payloads(events, ToolCompleted)
    assert refused.output == ToolOutput.error(
        "Le rôle rediger a besoin d'un résultat de calculer : appelle d'abord cet outil."
    )


async def test_references_are_checked_before_the_call(store: EventStore) -> None:
    main = main_model(
        tool_call_message(("c1", "calculer", {"expr": {"$ref": "result:5"}})),
        Message.assistant("Tant pis."),
    )
    state = await run(context(store, main, role(role_model())))
    events = await journal(store, state)
    assert "tool.called" not in types(events)
    [refused] = payloads(events, ToolCompleted)
    assert refused.output.is_error
    assert refused.output.as_text == (
        "Référence inconnue : result:5. Aucun résultat à référencer dans ce run."
    )


async def test_role_failures_become_error_results(store: EventStore) -> None:
    main = main_model(
        tool_call_message(("c1", "calculer", {"expr": "1"})),
        tool_call_message(("c2", "rediger", {"ton": "a"})),
        tool_call_message(("c3", "rediger", {"ton": "b"})),
        tool_call_message(("c4", "rediger", {"ton": "c"})),
        Message.assistant("Tant pis."),
    )
    # c2 : surcharge puis réponse ; c3 : clé refusée ; c4 : réponse vide.
    writer = role_model(
        ModelError("overloaded", "surchargé"),
        Message.assistant("Bonne réponse."),
        ModelError("auth", "clé refusée", http_status=401),
        Message.assistant(" "),
    )
    state = await run(context(store, main, role(writer)))

    assert state.status is RunStatus.COMPLETED
    events = await journal(store, state)
    outputs = {p.call_id: p.output for p in payloads(events, ToolCompleted)}
    assert outputs["c2"] == ToolOutput.text("Bonne réponse.")
    assert outputs["c3"] == ToolOutput.error(
        "Le rôle rediger n'a pas pu répondre (model.auth) : clé refusée"
    )
    assert outputs["c4"] == ToolOutput.error("Le rôle rediger n'a rien produit (arrêt : end).")
    # La nouvelle tentative est journalisée dans l'appel du rôle, à son nom.
    [retried] = [e for e in events if isinstance(e.payload, ModelRetried)]
    assert isinstance(retried.payload, ModelRetried)
    assert (retried.payload.call_id, retried.role) == ("c2", "rediger")
    [c2_response] = [p for p in payloads(events, ModelResponded) if p.call_id == "c2"]
    assert c2_response.attempts == 2


async def test_role_without_input_is_refused(store: EventStore) -> None:
    main = main_model(
        tool_call_message(("c1", "resumer", {})),
        Message.assistant("Tant pis."),
    )
    optional: dict[str, JsonValue] = {"type": "object", "properties": {"texte": {}}}
    summary = role(role_model(), name="resumer", input_schema=optional, context=())
    state = await run(context(store, main, summary))
    [refused] = payloads(await journal(store, state), ToolCompleted)
    assert refused.output == ToolOutput.error(
        "Le rôle resumer n'a rien reçu : donne-lui ses arguments."
    )
    assert repr(summary) == "RoleTool('resumer', modèle 'ROLE')"


async def test_tool_calls_of_a_role_are_dropped(store: EventStore) -> None:
    main = main_model(
        tool_call_message(("c1", "rediger", {"ton": "a"})),
        tool_call_message(("c2", "rediger", {"ton": "b"})),
        Message.assistant("Fini."),
    )
    writer = role_model(
        tool_call_message(("x1", "calculer", {"expr": "1"}), text="Texte gardé."),
        tool_call_message(("x2", "calculer", {"expr": "2"})),
    )
    state = await run(context(store, main, role(writer, context=())))
    events = await journal(store, state)
    outputs = {p.call_id: p.output for p in payloads(events, ToolCompleted)}
    assert outputs["c1"] == ToolOutput.text("Texte gardé.")
    assert outputs["c2"] == ToolOutput.error("Le rôle rediger n'a rien produit (arrêt : tool_use).")
    role_messages = [p.message for p in payloads(events, ModelResponded) if p.call_id]
    assert all(not m.tool_calls for m in role_messages)


# --- Outil terminal ----------------------------------------------------------


async def test_terminal_role_output_is_the_final_answer(store: EventStore) -> None:
    main = main_model(
        tool_call_message(("c1", "calculer", {"expr": "12*7+3"})),
        tool_call_message(("c2", "rediger", {"ton": "poli"})),
    )
    writer = role_model(Message.assistant("Cela fait 87."))
    terminal = role(writer, terminal=True)
    state = await run(context(store, main, terminal))

    assert state.status is RunStatus.COMPLETED
    assert state.output == Message.assistant("Cela fait 87.")
    assert (state.terminal_call_id, state.iterations) == ("c2", 2)
    assert f"\n\n{TERMINAL_HINT}" in main.requests[0].tools[1].description

    events = await journal(store, state)
    assert types(events)[-5:] == [
        "model.responded",
        "tool.completed",
        "step.completed",
        "run.transitioned",
        "run.completed",
    ]
    done, transition, closing = events[-4], events[-2], events[-1]
    assert isinstance(transition.payload, RunTransitioned)
    assert (transition.payload.from_state, transition.payload.to_state) == (
        RunStatus.AWAITING_TOOLS,
        RunStatus.COMPLETED,
    )
    assert transition.payload.cause_event_id == done.event_id
    assert isinstance(closing.payload, RunCompleted)
    # Pas de duplication : run.completed désigne le tool.completed terminal.
    assert (closing.payload.output, closing.payload.output_event_id) == (None, done.event_id)


async def test_failed_terminal_role_returns_to_the_orchestrator(store: EventStore) -> None:
    main = main_model(
        tool_call_message(("c1", "calculer", {"expr": "1"})),
        tool_call_message(("c2", "rediger", {"ton": "poli"})),
        Message.assistant("Je réponds moi-même : 1."),
    )
    writer = role_model(ModelError("auth", "clé refusée"))
    state = await run(context(store, main, role(writer, terminal=True)))
    assert state.output == Message.assistant("Je réponds moi-même : 1.")
    assert state.terminal_call_id is None


async def test_terminal_role_called_with_other_tools(
    store: EventStore, caplog: pytest.LogCaptureFixture
) -> None:
    main = main_model(
        tool_call_message(("c1", "calculer", {"expr": "1"})),
        tool_call_message(("c2", "rediger", {"ton": "poli"}), ("c3", "calculer", {"expr": "2"})),
        Message.assistant("Je compose : 1 puis 2."),
    )
    writer = role_model(Message.assistant("Rédigé."))
    with caplog.at_level(logging.WARNING, logger="loom_ia.engine.loop"):
        state = await run(context(store, main, role(writer, terminal=True)))
    assert state.output == Message.assistant("Je compose : 1 puis 2.")
    assert "Outil terminal rediger appelé avec d'autres outils" in caplog.text


async def test_terminal_role_wins_over_the_iteration_limit(store: EventStore) -> None:
    main = main_model(tool_call_message(("c1", "rediger", {"ton": "poli"})))
    writer = role_model(Message.assistant("Bonjour."))
    tools = role(writer, terminal=True, context=())
    state = await run(context(store, main, tools, max_iterations=1))
    assert state.status is RunStatus.COMPLETED
    assert state.output == Message.assistant("Bonjour.")


async def test_session_history_shows_the_terminal_output(store: EventStore) -> None:
    session = SessionId("s-1")
    writer = role_model(Message.assistant("Cela fait 87."), Message.assistant("-"))
    main = main_model(
        tool_call_message(("c1", "rediger", {"ton": "poli"})),
        Message.assistant("Toujours 87."),
    )
    ctx = context(store, main, role(writer, terminal=True, context=()))
    first = await begin_run(ctx, "Combien ?", session_id=session)
    await drive(ctx, first.run_id, session_id=session)
    second = await begin_run(ctx, "Et donc ?", session_id=session)
    await drive(ctx, second.run_id, session_id=session)

    request = main.requests[-1]
    assert [m.role for m in request.messages] == ["user", "assistant", "tool", "assistant", "user"]
    [marker] = [b for b in request.messages[2].blocks if isinstance(b, ToolResultBlock)]
    assert marker.output.as_text.endswith(TERMINAL_MARKER)
    assert request.messages[3] == Message.assistant("Cela fait 87.")


# --- Reprise ------------------------------------------------------------------


async def copy_until(source: list[Event], stop: int) -> InMemoryEventStore:
    """Journal qui s'arrête avant l'événement ``stop`` : un plantage simulé."""
    target = InMemoryEventStore()
    drafts = [EventDraft.model_validate(e.model_dump(exclude={"seq"})) for e in source[:stop]]
    await target.append(drafts, expected_seq=0)
    return target


async def test_interrupted_role_is_called_again(store: EventStore) -> None:
    main = main_model(tool_call_message(("c1", "rediger", {"ton": "poli"})))
    first = role_model(Message.assistant("Premier jet."))
    ctx = context(store, main, role(first, terminal=True, context=()))
    done = await run(ctx)
    events = await journal(store, done)
    cut = types(events).index("tool.completed")

    crashed = await copy_until(events, cut)
    again = role_model(Message.assistant("Second jet."))
    resumed = context(crashed, main_model(), role(again, terminal=True, context=()))
    state = await drive(resumed, done.run_id)

    assert state.output == Message.assistant("Second jet.")
    assert len(again.requests) == 1
    # Les deux appels du rôle ont réellement eu lieu : les deux sont comptés.
    assert state.usage == USAGE + ROLE_USAGE + ROLE_USAGE
    calls = payloads(await crashed.read(done.context.tenant_id, done.session_id), ToolCalled)
    assert [c.resumed for c in calls] == [False, True]


async def test_terminal_closing_is_written_on_resume(store: EventStore) -> None:
    main = main_model(tool_call_message(("c1", "rediger", {"ton": "poli"})))
    ctx = context(
        store, main, role(role_model(Message.assistant("Fini.")), terminal=True, context=())
    )
    done = await run(ctx)
    events = await journal(store, done)

    crashed = await copy_until(events, len(events) - 1)
    state = await drive(
        context(crashed, main_model(), role(role_model(), terminal=True, context=())), done.run_id
    )
    assert state.output == Message.assistant("Fini.")
    closing = (await crashed.read(done.context.tenant_id, done.session_id))[-1].payload
    assert isinstance(closing, RunCompleted)
    assert closing.output_event_id == events[-4].event_id


# --- Projection ---------------------------------------------------------------


def test_role_response_for_an_unknown_call_is_a_divergence() -> None:
    journal = RunJournal(agent="demo")
    journal.start("?")
    events = [draft.to_event(seq) for seq, draft in enumerate(journal.take(), start=1)]
    state = fold(events, journal.run_id)
    stray = events[-1].model_copy(
        update={
            "seq": 3,
            "type": "model.responded",
            "category": "model",
            "status": "ok",
            "facets": {},
            "payload": ModelResponded(
                model_id="m",
                provider="fake",
                message=Message.assistant("x"),
                request_hash="h",
                call_id="inconnu",
            ),
        }
    )
    with pytest.raises(ProjectionError, match="appel inconnu 'inconnu'"):
        apply(state, stray)


def test_terminal_closing_needs_a_tool_result() -> None:
    journal = RunJournal(agent="demo")
    journal.start("?").model_turn(Message.assistant("Bonjour"))
    events = [draft.to_event(seq) for seq, draft in enumerate(journal.take(), start=1)]
    state = fold(events, journal.run_id)
    closing = events[-1].model_copy(
        update={
            "seq": len(events) + 1,
            "type": "run.completed",
            "category": "run",
            "facets": {},
            "payload": RunCompleted(output_event_id=events[-1].event_id),
        }
    )
    with pytest.raises(ProjectionError, match="pas un résultat d'outil unique"):
        apply(state, closing)


def test_run_view_exposes_state_and_results() -> None:
    journal = RunJournal(agent="demo")
    journal.start("Bonjour")
    events = [draft.to_event(seq) for seq, draft in enumerate(journal.take(), start=1)]
    view = RunView.of(fold(events, journal.run_id))
    assert view.results.records == ()
    assert view.state.messages == (Message.user("Bonjour"),)
