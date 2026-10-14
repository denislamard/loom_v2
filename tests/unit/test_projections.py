# SPDX-License-Identifier: Apache-2.0
"""Projections : état d'un run et historique d'une session."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import JsonValue

from loom_ia.core.events import (
    ApprovalGranted,
    ApprovalRejected,
    ApprovalRequested,
    DurablePayload,
    Event,
    EventDraft,
    RunClaimed,
    RunStarted,
    RunTransitioned,
    SessionCompacted,
    SessionTrimmed,
    ToolCalled,
    ToolSourceUnavailable,
    UserMessage,
)
from loom_ia.core.model import (
    Message,
    ReasoningBlock,
    RunState,
    RunStatus,
    SessionId,
    TextBlock,
    ToolOutput,
    ToolResultBlock,
    Usage,
)
from loom_ia.core.projections import (
    ProjectionError,
    apply,
    fold,
    fold_all,
    history,
    session_state,
)
from loom_ia.testing import RunJournal, tool_call_message


def numbered(*journals: RunJournal) -> list[Event]:
    """Événements des journaux, numérotés comme un store le ferait."""
    drafts = [d for journal in journals for d in journal.take()]
    return [draft.to_event(seq) for seq, draft in enumerate(drafts, start=1)]


def calculation_run(session: SessionId | None = None) -> RunJournal:
    journal = RunJournal(session_id=session)
    journal.start("Combien font 12 * 7 + 3 ?")
    journal.model_turn(
        tool_call_message(("c1", "calculer", {"expr": "12*7+3"})),
        usage=Usage(input_tokens=100, output_tokens=10),
        cost_usd=0.002,
    )
    journal.tool_results({"c1": ToolOutput.text("87")})
    journal.model_turn(
        Message.assistant("12 * 7 + 3 = 87"),
        usage=Usage(input_tokens=120, output_tokens=8),
        cost_usd=0.003,
    )
    return journal.complete()


def test_run_state_keeps_the_root_span() -> None:
    events = numbered(calculation_run())
    state = fold(events, events[0].run_id)
    assert state.span_id == events[0].span_id
    assert state.parent_span_id is None


def test_fold_rebuilds_a_completed_run() -> None:
    journal = calculation_run()
    state = fold(numbered(journal), journal.run_id)

    assert state.status is RunStatus.COMPLETED
    assert state.finished
    assert state.iterations == 2
    assert state.step == 3
    assert state.usage == Usage(input_tokens=220, output_tokens=18)
    assert state.cost_usd == pytest.approx(0.005)
    assert state.output == Message.assistant("12 * 7 + 3 = 87")
    assert [m.role for m in state.messages] == ["user", "assistant", "tool", "assistant"]
    assert state.pending_calls == ()
    assert state.last_seq == 16


def test_pending_calls_follow_the_tool_lifecycle() -> None:
    journal = RunJournal()
    journal.start("x").model_turn(tool_call_message(("c1", "a", {}), ("c2", "b", {})))
    events = numbered(journal)
    state = fold(events, journal.run_id)
    assert state.status is RunStatus.AWAITING_TOOLS
    assert [(c.call_id, c.started) for c in state.pending_calls] == [
        ("c1", False),
        ("c2", False),
    ]

    started = journal.scope.draft(ToolCalled(call_id="c1", tool_name="a", tool_kind="python"))
    state = apply(state, started.to_event(len(events) + 1))
    assert [(c.call_id, c.started) for c in state.pending_calls] == [("c1", True), ("c2", False)]


def _after_request(*payloads: DurablePayload) -> list[Event]:
    """Journal d'un run dont le modèle demande l'appel c1, suivi de ces événements."""
    journal = RunJournal()
    journal.start("x").model_turn(tool_call_message(("c1", "virer", {})))
    drafts = [*journal.take(), *(journal.scope.draft(p) for p in payloads)]
    return [draft.to_event(seq) for seq, draft in enumerate(drafts, start=1)]


def _ask(reason: str = "") -> DurablePayload:
    return ApprovalRequested(call_id="c1", tool_name="virer", reason=reason)


def _grant() -> DurablePayload:
    return ApprovalGranted(call_id="c1", tool_name="virer", by="denis")


def _launch() -> DurablePayload:
    return ToolCalled(call_id="c1", tool_name="virer", tool_kind="python")


def test_a_grant_is_spent_by_the_launch_that_follows_it() -> None:
    """L'ordre du journal dit si l'accord a déjà servi : avant l'appel, après, ou les deux."""

    def launched(*payloads: DurablePayload) -> list[bool]:
        events = _after_request(*payloads)
        return [a.launched for a in fold(events, events[0].run_id).approvals]

    assert launched(_ask(), _grant()) == [False]
    assert launched(_ask(), _grant(), _launch()) == [True]
    # Demandé après coup (effet d'état inconnu) : pas encore servi, tant qu'il n'a pas relancé.
    assert launched(_launch(), _ask("inconnu"), _grant()) == [False]
    assert launched(_launch(), _ask("inconnu"), _grant(), _launch()) == [True]
    # Une demande sans réponse ne se consomme pas : seule une décision accordée sert.
    assert launched(_ask(), _launch()) == [False]
    refusee = ApprovalRejected(call_id="c1", tool_name="virer", by="denis")
    assert launched(_ask(), refusee) == [False]


def test_a_call_can_ask_for_a_second_approval() -> None:
    """Le premier accord a servi ; une seconde demande suit si l'effet devient inconnu."""
    events = _after_request(_ask(), _grant(), _launch(), _ask("inconnu"), _grant())
    asked = fold(events[:-1], events[0].run_id)
    assert [a.reason for a in asked.approvals] == ["", "inconnu"]
    assert asked.awaiting == (asked.approvals[1],)
    assert asked.approval("c1") is asked.approvals[1]

    state = fold(events, events[0].run_id)
    assert state.awaiting == ()
    # La décision ne ferme que la dernière demande ; la première garde la sienne.
    assert [(a.outcome and a.outcome.verdict, a.launched) for a in state.approvals] == [
        ("granted", True),
        ("granted", False),
    ]


def test_a_decision_settles_a_request_only_once() -> None:
    events = _after_request(_ask(), _grant())
    state = fold(events, events[0].run_id)
    again = RunJournal(run_id=state.run_id).scope.draft(_grant())
    with pytest.raises(ProjectionError, match="déjà tranché"):
        apply(state, again.to_event(len(events) + 1))


def test_tool_result_is_added_to_messages() -> None:
    journal = calculation_run()
    state = fold(numbered(journal), journal.run_id)
    result = state.messages[2].blocks[0]
    assert isinstance(result, ToolResultBlock)
    assert result.call_id == "c1"
    assert result.output.as_text == "87"


def test_failed_run() -> None:
    journal = RunJournal()
    journal.start("x").fail("TimeoutError", "modèle muet")
    state = fold(numbered(journal), journal.run_id)
    assert state.status is RunStatus.FAILED
    assert (state.error_type, state.error) == ("TimeoutError", "modèle muet")


def test_event_before_run_started_is_rejected() -> None:
    journal = RunJournal()
    journal.start("x")
    events = numbered(journal)
    with pytest.raises(ProjectionError, match=r"avant run\.started"):
        fold(events[1:], journal.run_id)


def test_duplicate_run_started_is_rejected() -> None:
    journal = RunJournal()
    journal.start("x")
    first = numbered(journal)[0]
    state = apply(None, first)
    with pytest.raises(ProjectionError, match="en double"):
        apply(state, first.model_copy(update={"seq": 2}))


def test_transition_from_wrong_state_is_a_divergence() -> None:
    journal = RunJournal()
    journal.start("x")
    events = numbered(journal)
    state = fold(events, journal.run_id)
    wrong = journal.scope.draft(
        RunTransitioned(from_state=RunStatus.PAUSED, to_state=RunStatus.READY_FOR_MODEL)
    )
    with pytest.raises(ProjectionError, match="transition depuis paused"):
        apply(state, wrong.to_event(3))


def test_unknown_tool_call_is_rejected() -> None:
    journal = RunJournal()
    journal.start("x")
    state = fold(numbered(journal), journal.run_id)
    unknown = journal.scope.draft(ToolCalled(call_id="zz", tool_name="t", tool_kind="python"))
    with pytest.raises(ProjectionError, match="appel inconnu"):
        apply(state, unknown.to_event(3))


def test_nothing_is_accepted_after_closing() -> None:
    journal = calculation_run()
    events = numbered(journal)
    state = fold(events, journal.run_id)
    extra = journal.scope.draft(
        RunTransitioned(from_state=RunStatus.COMPLETED, to_state=RunStatus.READY_FOR_MODEL)
    )
    with pytest.raises(ProjectionError, match="après l'état completed"):
        apply(state, extra.to_event(len(events) + 1))


def test_only_the_closing_event_follows_a_terminal_transition() -> None:
    journal = RunJournal()
    journal.start("x").transition(RunStatus.COMPLETED)
    events = numbered(journal)
    state = fold(events, journal.run_id)
    user = journal.scope.draft(events[1].payload)
    with pytest.raises(ProjectionError, match="après l'état completed"):
        apply(state, user.to_event(len(events) + 1))


def test_a_lease_written_after_the_final_state_is_absorbed() -> None:
    """Concession de reprise ou de renouvellement, écrite pendant la clôture.

    Elle parle du pilote, pas du run : le journal reste lisible, que la clôture
    la précède ou la suive. Les autres événements restent refusés après un état
    final — l'exception ne vaut que pour elle.
    """
    journal = RunJournal()
    journal.start("x").model_turn(Message.assistant("ok")).complete()
    *opening, closing = journal.take()  # ... transition vers completed, puis run.completed
    lease = journal.scope.draft(
        RunClaimed(worker_id="worker-2", lease_until=datetime.now(UTC) + timedelta(seconds=60))
    )

    for order in ([lease, closing], [closing, lease]):
        events = [d.to_event(seq) for seq, d in enumerate([*opening, *order], start=1)]
        state = fold(events, journal.run_id)

        assert state.finished and state.status is RunStatus.COMPLETED
        assert state.last_seq == len(events)
        assert state.claim is not None and state.claim.worker_id == "worker-2"

    stray = journal.scope.draft(ToolSourceUnavailable(source="erp", error="injoignable"))
    events = [d.to_event(seq) for seq, d in enumerate([*opening, closing, stray], start=1)]
    with pytest.raises(ProjectionError, match="après l'état completed"):
        fold(events, journal.run_id)


def test_a_decision_written_after_the_closing_is_absorbed() -> None:
    """Run clos pendant que l'approbateur décidait : la décision arrive après la clôture.

    Elle ne rouvre rien et ne rend pas le run illisible. La demande reste sans
    réponse, pour l'historique, et le run n'attend plus rien. Les autres
    événements restent refusés après un état final.
    """
    journal = RunJournal()
    journal.start("x").model_turn(tool_call_message(("c1", "virer", {})))
    opening = [*journal.take(), journal.scope.draft(_ask())]
    *_, transition, closing = journal.fail("Timeout", "approbation non reçue à l'échéance").take()

    def read(*drafts: EventDraft) -> RunState:
        events = [d.to_event(seq) for seq, d in enumerate([*opening, *drafts], start=1)]
        state = fold(events, journal.run_id)
        assert state.last_seq == len(events)
        return state

    refused = ApprovalRejected(call_id="c1", tool_name="virer", by="denis")
    for decision in (_grant(), refused):
        late = journal.scope.draft(decision)
        # Après la clôture, ou entre la transition finale et la clôture (plantage entre les deux).
        for order in ([transition, closing, late], [transition, late, closing]):
            state = read(*order)
            assert state.finished and state.status is RunStatus.FAILED
            assert [a.outcome for a in state.approvals] == [None]
            assert state.awaiting == ()
        # Le run déjà en état final, sa clôture pas encore écrite, n'attend rien non plus.
        between = read(transition, late)
        assert not between.finished and between.awaiting == ()

    stray = journal.scope.draft(ToolSourceUnavailable(source="erp", error="injoignable"))
    with pytest.raises(ProjectionError, match="après l'état failed"):
        read(transition, closing, stray)


def test_event_of_another_run_is_rejected() -> None:
    first, second = RunJournal(), RunJournal()
    first.start("a")
    second.start("b")
    state = fold(numbered(first), first.run_id)
    other = numbered(second)[1]
    with pytest.raises(ProjectionError, match="appliqué au run"):
        apply(state, other)


def test_fold_without_events_for_the_run() -> None:
    journal = RunJournal()
    with pytest.raises(ProjectionError, match="Aucun événement"):
        fold([], journal.run_id)


def test_fold_all_separates_runs_of_a_session() -> None:
    session = SessionId("c-42")
    first, second = calculation_run(session), RunJournal(session_id=session)
    second.start("et ensuite ?")
    states = fold_all(numbered(first, second))
    assert list(states) == [first.run_id, second.run_id]
    assert states[second.run_id].status is RunStatus.READY_FOR_MODEL


def test_history_keeps_completed_root_runs_without_reasoning() -> None:
    session = SessionId("c-42")

    done = RunJournal(session_id=session)
    done.start("Bonjour")
    done.model_turn(
        Message(
            role="assistant",
            blocks=(ReasoningBlock(text="je réfléchis"), TextBlock(text="Bonjour !")),
        )
    )
    done.complete()

    failed = RunJournal(session_id=session)
    failed.start("Plante").model_turn(tool_call_message(("c1", "t", {}))).fail("E", "boom")

    child = RunJournal(session_id=session, root_run_id=done.run_id)
    child.start("sous-tâche", parent_run_id=done.run_id, parent_call_id="c9", depth=1)
    child.model_turn(Message.assistant("fait")).complete()

    thinking_only = RunJournal(session_id=session)
    thinking_only.start("Et ça ?")
    thinking_only.model_turn(Message(role="assistant", blocks=(ReasoningBlock(text="..."),)))
    thinking_only.complete()

    empty = RunJournal(session_id=session)
    empty.start("Rien ?")
    empty.model_turn(
        Message(role="assistant", blocks=(TextBlock(text=""), TextBlock(text="")))
    ).complete()

    mixed = RunJournal(session_id=session)
    mixed.start("Et là ?")
    mixed.model_turn(
        Message(role="assistant", blocks=(TextBlock(text=""), TextBlock(text="Oui")))
    ).complete()

    messages = history(numbered(done, failed, child, thinking_only, empty, mixed))
    assert messages == [
        Message.user("Bonjour"),
        Message.assistant("Bonjour !"),
        Message.user("Et ça ?"),
        Message.user("Rien ?"),
        Message.user("Et là ?"),
        Message.assistant("Oui"),
    ]


# --- State de la session ------------------------------------------------------


def written(text: str, **state: JsonValue) -> ToolOutput:
    """Résultat d'outil réussi qui écrit ces clés du state."""
    return ToolOutput(blocks=(TextBlock(text=text),), state=state)


def writing_run(
    session: SessionId,
    *outputs: ToolOutput,
    posed: dict[str, JsonValue] | None = None,
) -> RunJournal:
    """Run dont le modèle appelle un outil par résultat donné, dans un même lot."""
    journal = RunJournal(session_id=session)
    journal.start("x", state=posed)
    if outputs:
        calls: list[tuple[str, str, dict[str, JsonValue]]] = [
            (f"c{n}", "outil", {}) for n in range(1, len(outputs) + 1)
        ]
        journal.model_turn(tool_call_message(*calls))
        journal.tool_results({f"c{n}": out for n, out in enumerate(outputs, start=1)})
    return journal.model_turn(Message.assistant("fait"))


def test_the_state_written_by_a_tool_is_not_shown_to_the_model() -> None:
    """Le résultat que lit le modèle est celui d'un outil sans state : mêmes requêtes."""
    session = SessionId("c-42")
    with_state = writing_run(session, written("ok", client="C-002")).complete()
    without = writing_run(session, ToolOutput.text("ok")).complete()

    state = fold(numbered(with_state), with_state.run_id)
    plain = fold(numbered(without), without.run_id)

    assert state.messages == plain.messages
    assert [(w.call_id, w.values) for w in state.state_writes] == [("c1", {"client": "C-002"})]
    assert state.state_delta == {"client": "C-002"}
    assert plain.state_writes == ()


def test_a_result_in_error_writes_nothing() -> None:
    """Un résultat en erreur ne pose rien, même s'il garde un state (politique qui refuse)."""
    refused = ToolOutput(blocks=(TextBlock(text="refusé"),), is_error=True, state={"k": 1})
    journal = writing_run(SessionId("c-42"), refused).complete()
    events = numbered(journal)
    assert fold(events, journal.run_id).state_writes == ()
    assert session_state(events) == {}


def test_the_call_order_decides_between_two_writes_of_a_key() -> None:
    """D9 : l'ordre des appels, et non celui d'arrivée des résultats, départage."""
    for arrival in (("c1", "c2"), ("c2", "c1")):
        journal = RunJournal()
        journal.start("x").model_turn(tool_call_message(("c1", "a", {}), ("c2", "b", {})))
        outputs = {"c1": written("1", k=1, seul_c1=True), "c2": written("2", k=2)}
        journal.tool_results({call: outputs[call] for call in arrival})
        first_batch = numbered(journal)
        assert fold(first_batch, journal.run_id).state_delta == {"k": 2, "seul_c1": True}

        journal.model_turn(tool_call_message(("c3", "a", {})))
        journal.tool_results({"c3": written("3", k=3)})
        second_batch = journal.take()
        start = len(first_batch) + 1
        events = [*first_batch, *(d.to_event(n) for n, d in enumerate(second_batch, start))]
        state = fold(events, journal.run_id)

        assert [w.call_id for w in state.state_writes] == ["c1", "c2", "c3"]
        assert [(w.turn, w.rank) for w in state.state_writes] == [(1, 0), (1, 1), (4, 0)]
        assert state.state_delta == {"k": 3, "seul_c1": True}


def test_the_session_state_follows_the_journal() -> None:
    """L'application pose à l'ouverture, les outils écrivent, ``None`` efface (D10)."""
    session = SessionId("c-42")
    first = writing_run(
        session, written("ticket", ticket="T-1"), posed={"client_id": "CL-1", "langue": "fr"}
    ).complete()
    second = writing_run(session, written("clos", ticket=None), posed={"langue": "en"}).complete()
    events = numbered(first, second)
    first_run = [e for e in events if e.run_id == first.run_id]

    assert session_state(first_run) == {"client_id": "CL-1", "langue": "fr", "ticket": "T-1"}
    assert session_state(events) == {"client_id": "CL-1", "langue": "en"}
    assert session_state([]) == {}


def test_failed_runs_sub_runs_and_compactions_do_not_write() -> None:
    """D2 et D3 : seuls les runs racine ordinaires terminés écrivent.

    Ce que l'application a posé à l'ouverture d'un run racine reste, quel que
    soit son sort ; un sous-run ou une compaction ne pose rien, même ouvert avec
    du state.
    """
    session = SessionId("c-42")
    failed = writing_run(session, written("ok", perdu=1), posed={"client_id": "CL-1"})
    failed.fail("E", "boom")

    parent = writing_run(session).complete()
    child = RunJournal(session_id=session, root_run_id=parent.run_id)
    child.start(
        "sous-tâche",
        parent_run_id=parent.run_id,
        parent_call_id="c9",
        depth=1,
        state={"pose_par_le_sous_run": 1},
    )
    child.model_turn(tool_call_message(("c1", "outil", {})))
    child.tool_results({"c1": written("ok", du_sous_run=1)})
    child.model_turn(Message.assistant("fait")).complete()

    compaction = writing_run(session, written("ok", de_la_compaction=1), posed={"x": 1}).complete()
    opening, *rest = compaction.take()
    started = opening.payload
    assert isinstance(started, RunStarted)
    system = compaction.scope.draft(started.model_copy(update={"kind": "compaction"}))

    events = numbered(failed, parent, child)
    tail = [d.to_event(seq) for seq, d in enumerate([system, *rest], start=len(events) + 1)]
    assert session_state([*events, *tail]) == {"client_id": "CL-1"}


def test_the_run_in_progress_sees_its_own_writes() -> None:
    """Le run en cours voit ses écritures ; elles ne comptent pour la session qu'à sa clôture."""

    def running() -> RunJournal:
        posed: dict[str, JsonValue] = {"client_id": "CL-1"}
        return writing_run(SessionId("c-42"), written("ok", ticket="T-7"), posed=posed)

    journal = running()
    events = numbered(journal)
    assert session_state(events) == {"client_id": "CL-1"}
    assert session_state(events, run_id=journal.run_id) == {"client_id": "CL-1", "ticket": "T-7"}

    completed = running().complete()
    events = numbered(completed)
    assert session_state(events) == {"client_id": "CL-1", "ticket": "T-7"}
    assert session_state(events, run_id=completed.run_id) == session_state(events)

    failed = running().fail("E", "boom")
    events = numbered(failed)
    assert session_state(events, run_id=failed.run_id) == {"client_id": "CL-1"}


@pytest.mark.parametrize("second_call", ["c2", "c1"])
def test_the_writes_of_a_repaired_call_are_dropped(second_call: str) -> None:
    """D2 : l'historique retire l'appel refusé et son résultat, le state ses écritures.

    Le run lui-même a vu le résultat refusé, et ses écritures avec lui. Un
    fournisseur peut redonner le même identifiant d'appel après la réparation :
    c'est la position de l'appel qui compte.
    """
    journal = RunJournal(session_id=SessionId("c-42"))
    journal.start("Rédige la relance")
    journal.model_turn(tool_call_message(("c1", "rediger", {})))
    journal.tool_results({"c1": written("v1", brouillon="v1")})
    repair = UserMessage(message=Message.user("Montant absent."), kind="repair", policy="p")
    drafts = [*journal.take(), journal.scope.draft(repair)]
    journal.model_turn(tool_call_message((second_call, "rediger", {})))
    journal.tool_results({second_call: written("v2", version=2)})
    drafts += journal.take()
    running = [draft.to_event(seq) for seq, draft in enumerate(drafts, start=1)]
    journal.model_turn(Message.assistant("fait")).complete()
    drafts += journal.take()
    events = [draft.to_event(seq) for seq, draft in enumerate(drafts, start=1)]

    seen = {"brouillon": "v1", "version": 2}
    assert session_state(running, run_id=journal.run_id) == seen
    assert fold(events, journal.run_id).state_delta == seen
    assert session_state(events) == {"version": 2}


def test_defaults_fill_the_absent_keys_without_being_written() -> None:
    session = SessionId("c-42")
    defaults: dict[str, JsonValue] = {"langue": "fr"}
    posed = writing_run(session, written("ok", langue="en")).complete()
    erased = writing_run(session, written("ok", langue=None)).complete()
    events = numbered(posed, erased)
    first_run = [e for e in events if e.run_id == posed.run_id]

    assert session_state([], defaults=defaults) == {"langue": "fr"}
    assert session_state(first_run, defaults=defaults) == {"langue": "en"}
    assert session_state(events, defaults=defaults) == {"langue": "fr"}


def test_the_state_given_is_a_copy() -> None:
    """Modifier le state rendu ne touche ni au journal lu, ni aux valeurs par défaut."""
    session = SessionId("c-42")
    events = numbered(writing_run(session, written("ok", client={"code": "C-002"})).complete())
    defaults: dict[str, JsonValue] = {"options": {"ton": "cordial"}}
    state = session_state(events, defaults=defaults)
    client, options = state["client"], state["options"]
    assert isinstance(client, dict) and isinstance(options, dict)
    client["code"] = "modifié"
    options["ton"] = "modifié"
    assert session_state(events, defaults=defaults) == {
        "client": {"code": "C-002"},
        "options": {"ton": "cordial"},
    }


def test_session_markers_do_not_change_the_state() -> None:
    """Snapshot, compaction et coupe remplacent l'historique, pas le state."""
    session = SessionId("c-42")
    first = writing_run(session, written("ok", client="C-002"), posed={"langue": "fr"})
    events = numbered(first.complete())
    expected = session_state(events)
    seq = len(events)
    for marker in (
        SessionTrimmed(up_to_seq=seq, dropped=1),
        SessionCompacted(up_to_seq=seq, summary="Le client C-002 a demandé un devis."),
    ):
        marked = [*events, first.scope.draft(marker).to_event(seq + 1)]
        assert history(marked) != history(events)
        assert session_state(marked) == expected == {"langue": "fr", "client": "C-002"}


def test_concurrent_runs_write_in_the_order_they_finish() -> None:
    """Deux runs sur une session : les écritures d'un run comptent à sa clôture."""
    session = SessionId("c-42")
    slow, fast = RunJournal(session_id=session), RunJournal(session_id=session)
    slow.start("lent").model_turn(tool_call_message(("c1", "outil", {})))
    slow.tool_results({"c1": written("ok", k="lent")})
    opened = slow.take()
    fast.start("rapide").model_turn(tool_call_message(("c1", "outil", {})))
    fast.tool_results({"c1": written("ok", k="rapide")})
    fast.model_turn(Message.assistant("fait")).complete()
    finished = fast.take()
    slow.model_turn(Message.assistant("fait")).complete()

    drafts = [*opened, *finished, *slow.take()]
    events = [draft.to_event(seq) for seq, draft in enumerate(drafts, start=1)]
    assert session_state(events[: len(opened) + len(finished)]) == {"k": "rapide"}
    assert session_state(events) == {"k": "lent"}
