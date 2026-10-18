# SPDX-License-Identifier: Apache-2.0
"""Un tour lancé pendant qu'un autre attend une approbation en est prévenu.

Constaté au test du 18/10 : le run en pause n'est pas dans l'historique (il
porte un appel sans résultat), et Haiku, au tour suivant, a proposé d'envoyer
une relance qui attendait déjà la décision de Denis.
"""

from pydantic import JsonValue

from loom_ia.adapters.stores import InMemoryEventStore
from loom_ia.core.events import UserMessage
from loom_ia.core.model import (
    DEFAULT_TENANT,
    Message,
    ModelSpec,
    PendingApproval,
    RetryPolicy,
    RunStatus,
    SessionId,
)
from loom_ia.core.projections import awaiting_in_session
from loom_ia.engine import Policies, RunContext, ToolExecutor, begin_run, drive
from loom_ia.engine.pending import ARGUMENTS_CHARS, pending_note
from loom_ia.testing import ScriptedModel, tool_call_message
from loom_ia.tools import ConfiguredTool, tool

SPEC = ModelSpec(id="FAKE", sdk="fake", model="fake-1", retry=RetryPolicy(initial_delay=0))
SESSION = SessionId("atelier")
ARGUMENTS: dict[str, JsonValue] = {"client": "Dupont", "message": "Bonjour, où en est D-0048 ?"}


@tool
def envoyer(client: str, message: str) -> str:
    """Envoie une relance."""
    return f"Relance envoyée à {client}"


SENSIBLE = ConfiguredTool(tool=envoyer, spec=envoyer.spec.model_copy(update={"approval": "always"}))


def context(store: InMemoryEventStore, model: ScriptedModel) -> RunContext:
    return RunContext(
        agent="demo",
        store=store,
        model=model,
        policies=Policies(()),
        tools=ToolExecutor([SENSIBLE]),
        system="Tu relances.",
        model_spec=SPEC,
        max_iterations=3,
    )


def test_the_note_names_the_call_and_its_arguments() -> None:
    asked = PendingApproval(call_id="c1", tool_name="envoyer", arguments=ARGUMENTS)
    note = pending_note([asked])
    assert note == Message.user(
        "[Note du système : au début de ce tour, une action demandée plus tôt dans cette "
        "conversation attend la décision d'un humain : envoyer (appel c1, arguments : "
        '{"client":"Dupont","message":"Bonjour, où en est D-0048 ?"}). Elle sera exécutée '
        "ou refusée sans toi ; ne la redemande pas.]"
    )
    assert pending_note([]) is None


def test_long_arguments_are_cut_and_several_actions_are_listed() -> None:
    long = PendingApproval(call_id="c1", tool_name="envoyer", arguments={"message": "x" * 1000})
    other = PendingApproval(call_id="c2", tool_name="facturer", arguments={})
    note = pending_note([long, other])
    assert note is not None
    text = note.text
    assert "des actions demandées" in text and "ne les redemande pas" in text
    assert f"{'x' * (ARGUMENTS_CHARS - len('{"message":"') - 1)}…" in text
    assert "facturer (appel c2, arguments : {})" in text


async def test_a_new_turn_hears_of_the_call_still_awaiting() -> None:
    store = InMemoryEventStore()
    model = ScriptedModel(
        tool_call_message(("c1", "envoyer", ARGUMENTS)),
        Message.assistant("La relance attend ta décision."),
    )
    ctx = context(store, model)
    first = await drive(
        ctx,
        (await begin_run(ctx, "Relance Dupont.", session_id=SESSION)).run_id,
        session_id=SESSION,
    )
    assert first.status is RunStatus.PAUSED

    second = await begin_run(ctx, "Où en est D-0048 ?", session_id=SESSION)
    state = await drive(ctx, second.run_id, session_id=SESSION)

    assert state.status is RunStatus.COMPLETED
    sent = model.requests[1].messages
    # Le run en pause n'est pas dans l'historique : la note vient juste avant la demande.
    assert [m.role for m in sent] == ["user", "user"]
    assert sent[0].text.startswith("[Note du système : au début de ce tour, une action")
    assert '"client":"Dupont"' in sent[0].text and "appel c1" in sent[0].text
    assert sent[1] == Message.user("Où en est D-0048 ?")
    # Rien au journal : la note n'existe que dans la requête.
    events = await store.read(state.context.tenant_id, SESSION)
    asked = [e.payload.message.text for e in events if isinstance(e.payload, UserMessage)]
    assert asked == ["Relance Dupont.", "Où en est D-0048 ?"]


async def test_nothing_awaiting_adds_nothing() -> None:
    store = InMemoryEventStore()
    model = ScriptedModel(Message.assistant("Bonjour."), Message.assistant("Toujours là."))
    ctx = context(store, model)
    for prompt in ("Bonjour", "Encore"):
        await drive(
            ctx, (await begin_run(ctx, prompt, session_id=SESSION)).run_id, session_id=SESSION
        )
    assert all("Note du système" not in m.text for r in model.requests for m in r.messages)
    events = await store.read(DEFAULT_TENANT, SESSION)
    assert awaiting_in_session(events) == ()
