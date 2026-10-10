# SPDX-License-Identifier: Apache-2.0
"""Exécuteur d'outils : lot parallèle, contrôles, timeout, reprise."""

import asyncio
import logging
from contextlib import aclosing
from dataclasses import dataclass, field

import pytest
from jsonschema.exceptions import SchemaError
from pydantic import JsonValue

from loom_ia.adapters.artifacts import InMemoryArtifactStore
from loom_ia.core import bounded_regex
from loom_ia.core.events import (
    ApprovalGranted,
    ApprovalRequested,
    DurablePayload,
    ToolCalled,
    ToolCompleted,
)
from loom_ia.core.model import (
    INVALID_JSON_KEY,
    Approved,
    Approver,
    CallerContext,
    PendingApproval,
    PendingCall,
    RunState,
    RunStatus,
    TenantId,
    TextBlock,
    ToolOutput,
    ToolSpec,
)
from loom_ia.core.ports import ToolContext, ToolError
from loom_ia.core.projections import fold
from loom_ia.engine import UNKNOWN_STATE, ToolExecutor
from loom_ia.engine.executor import ToolEvent
from loom_ia.testing import RunJournal, tool_call_message
from loom_ia.tools import tool


@dataclass
class RecordingTool:
    """Outil minimal qui note ses appels et exécute ``action``."""

    spec: ToolSpec
    result: ToolOutput = field(default_factory=lambda: ToolOutput.text("ok"))
    error: Exception | None = None
    delay: float = 0.0
    calls: list[tuple[dict[str, JsonValue], ToolContext]] = field(
        default_factory=list[tuple[dict[str, JsonValue], ToolContext]]
    )
    cancelled: bool = False

    async def invoke(self, arguments: dict[str, JsonValue], context: ToolContext) -> ToolOutput:
        self.calls.append((arguments, context))
        try:
            await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        if self.error is not None:
            raise self.error
        return self.result


def spec(name: str, **options: object) -> ToolSpec:
    schema: dict[str, JsonValue] = {
        "type": "object",
        "properties": {"x": {"type": "integer"}},
        "additionalProperties": False,
    }
    return ToolSpec.model_validate(
        {"name": name, "description": name, "kind": "python", "input_schema": schema, **options}
    )


def awaiting(*calls: PendingCall, tenant: str = "default") -> RunState:
    journal = RunJournal(agent="demo", tenant_id=TenantId(tenant))
    journal.start("?", context=CallerContext(tenant_id=TenantId(tenant), user_id="u1"))
    events = [draft.to_event(seq) for seq, draft in enumerate(journal.take(), start=1)]
    return fold(events, journal.run_id).model_copy(
        update={"status": RunStatus.AWAITING_TOOLS, "pending_calls": calls}
    )


def call(call_id: str, name: str, *, started: bool = False, **arguments: JsonValue) -> PendingCall:
    return PendingCall(call_id=call_id, name=name, arguments=arguments, started=started)


def journaled(call_id: str, name: str, *payloads: DurablePayload, x: int = 3) -> RunState:
    """État d'un run relu au journal : le modèle a demandé l'appel, puis ces événements ont suivi.

    Contrairement à ``awaiting``, l'état est **plié** depuis les événements : c'est
    ainsi que la reprise le lit, avec l'ordre entre l'accord et le lancement.
    """
    journal = RunJournal(agent="demo")
    journal.start("?").model_turn(tool_call_message((call_id, name, {"x": x})))
    drafts = [*journal.take(), *(journal.scope.draft(p) for p in payloads)]
    events = [draft.to_event(seq) for seq, draft in enumerate(drafts, start=1)]
    return fold(events, journal.run_id)


def asked(
    call_id: str, name: str, reason: str = "outil à approbation obligatoire"
) -> DurablePayload:
    return ApprovalRequested(call_id=call_id, tool_name=name, arguments={"x": 3}, reason=reason)


def granted(call_id: str, name: str) -> DurablePayload:
    return ApprovalGranted(call_id=call_id, tool_name=name, by="denis")


def launched(call_id: str, name: str, *, resumed: bool = False) -> DurablePayload:
    return ToolCalled(
        call_id=call_id, tool_name=name, tool_kind="python", arguments={"x": 3}, resumed=resumed
    )


async def collect(executor: ToolExecutor, state: RunState) -> list[ToolEvent]:
    return [event async for event in executor.run_batch(state)]


def completed(events: list[ToolEvent]) -> dict[str, ToolOutput]:
    return {e.call_id: e.output for e in events if isinstance(e, ToolCompleted)}


async def test_batch_runs_in_parallel_and_reports_as_soon_as_possible() -> None:
    both_started = asyncio.Event()
    started: list[str] = []

    @tool
    async def lent(x: int) -> str:
        """Lent."""
        started.append("lent")
        if len(started) == 2:
            both_started.set()
        await both_started.wait()
        await asyncio.sleep(0.02)
        return "lent"

    @tool
    async def rapide(x: int) -> str:
        """Rapide."""
        started.append("rapide")
        if len(started) == 2:
            both_started.set()
        await both_started.wait()
        return "rapide"

    executor = ToolExecutor([lent, rapide])
    async with asyncio.timeout(2):
        events = await collect(
            executor, awaiting(call("c1", "lent", x=1), call("c2", "rapide", x=2))
        )

    assert [(type(e).__name__, e.call_id) for e in events] == [
        ("ToolCalled", "c1"),
        ("ToolCalled", "c2"),
        ("ToolCompleted", "c2"),
        ("ToolCompleted", "c1"),
    ]
    first = events[0]
    assert isinstance(first, ToolCalled)
    assert (first.tool_name, first.tool_kind, first.arguments, first.resumed) == (
        "lent",
        "python",
        {"x": 1},
        False,
    )
    last = events[-1]
    assert isinstance(last, ToolCompleted)
    assert last.latency_ms >= 20
    assert last.size == len(ToolOutput.text("lent").model_dump_json())


async def test_rejected_calls_are_not_started() -> None:
    target = RecordingTool(spec("cible"))
    executor = ToolExecutor([target])
    events = await collect(
        executor,
        awaiting(
            call("c1", "inconnu"),
            PendingCall(call_id="c2", name="cible", arguments={INVALID_JSON_KEY: '{"x": '}),
            call("c3", "cible", x="deux", y=1),
        ),
    )
    assert all(isinstance(e, ToolCompleted) for e in events)
    assert target.calls == []
    outputs = completed(events)
    assert all(o.is_error for o in outputs.values())
    assert outputs["c1"].as_text == "Outil inconnu : 'inconnu'. Outils disponibles : cible."
    assert outputs["c2"].as_text.startswith("Arguments illisibles")
    assert outputs["c3"].as_text == (
        "Arguments non conformes au schéma de l'outil :\n"
        "- (racine) : Additional properties are not allowed ('y' was unexpected)\n"
        "- x : 'deux' is not of type 'integer'"
    )
    assert all(isinstance(e, ToolCompleted) and e.latency_ms == 0 for e in events)


async def test_a_schema_pattern_that_does_not_finish_refuses_the_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Le motif d'un schéma ne gèle pas l'hôte : l'appel est refusé, l'outil n'est pas lancé.

    Le schéma d'un outil forgé est écrit par le modèle, et ses arguments aussi.
    """
    monkeypatch.setattr(bounded_regex, "REGEX_TIMEOUT", 0.2)
    schema: dict[str, JsonValue] = {
        "type": "object",
        "properties": {"s": {"type": "string", "pattern": "^(a|aa)+$"}},
        "required": ["s"],
    }
    lent = ToolSpec.model_validate(
        {"name": "lent", "description": "lent", "kind": "python", "input_schema": schema}
    )
    target = RecordingTool(lent)
    executor = ToolExecutor([target])
    events = await collect(
        executor, awaiting(call("c1", "lent", s="a" * 64 + "!"), call("c2", "lent", s="aaa"))
    )
    outputs = completed(events)
    assert outputs["c1"].is_error
    assert outputs["c1"].as_text.startswith("Arguments non conformes au schéma de l'outil :")
    assert "motif trop coûteux" in outputs["c1"].as_text
    # Un texte que le motif finit par accepter passe, et l'outil est lancé pour lui seul.
    assert not outputs["c2"].is_error
    assert [arguments for arguments, _ in target.calls] == [{"s": "aaa"}]


async def test_a_value_from_a_reference_is_refused_saying_so() -> None:
    """Le refus dit d'où vient une valeur que le modèle n'a pas écrite : sa référence.

    Ce qu'a fait MiniMax-M3 au run réel de 6.2c : la référence en chaîne pour
    un champ texte, résolue en l'objet du devis — refusée deux fois avec, pour
    seule explication, cet objet recopié.
    """
    devis: JsonValue = {"numero": "D-2026-042", "montant": 1840}
    schema: dict[str, JsonValue] = {
        "type": "object",
        "properties": {
            "consignes": {"type": "string"},
            "devis": {"type": "object", "properties": {"montant": {"type": "string"}}},
            "ton": {"type": "string"},
        },
    }
    target = RecordingTool(
        ToolSpec.model_validate(
            {"name": "rediger", "description": "Rédige.", "kind": "python", "input_schema": schema}
        )
    )
    arguments: dict[str, JsonValue] = {
        "consignes": '{"$ref": "result:1"}',
        "devis": {"$ref": "result:1"},
        "ton": 3,
    }
    journal = RunJournal(agent="demo")
    journal.start("Relance le devis.")
    journal.model_turn(tool_call_message(("c1", "chercher", {})))
    journal.tool_results({"c1": ToolOutput(data=devis)})
    journal.model_turn(tool_call_message(("c2", "rediger", arguments)))
    events = [draft.to_event(seq) for seq, draft in enumerate(journal.take(), start=1)]

    outputs = completed(await collect(ToolExecutor([target]), fold(events, journal.run_id)))
    assert target.calls == []
    assert outputs["c2"].as_text == (
        "Arguments non conformes au schéma de l'outil :\n"
        "- consignes : la référence result:1 (chercher), écrite en chaîne mais lue comme une "
        "référence, transmet un objet JSON ; ce champ attend du texte. Une référence passe le "
        "résultat tel quel : si le champ attend autre chose, écris la valeur toi-même.\n"
        "- devis.montant : 1840 is not of type 'string' — valeur transmise par la référence "
        "result:1 (chercher)\n"
        # Une valeur écrite par le modèle garde le message d'origine.
        "- ton : 3 is not of type 'string'"
    )


async def test_validation_can_be_disabled() -> None:
    target = RecordingTool(spec("cible"))
    events = await collect(
        ToolExecutor([target], validate_arguments=False), awaiting(call("c1", "cible", x="a"))
    )
    assert target.calls[0][0] == {"x": "a"}
    assert not completed(events)["c1"].is_error


BAD_ARGUMENTS: dict[str, JsonValue] = {"x": "pas un entier", "intrus": True}
CORRECTION_REFUSED = (
    "Appel à virement non exécuté : les arguments corrigés par l'approbateur sont refusés "
    "par le schéma de l'outil. Arguments non conformes au schéma de l'outil :\n"
    "- (racine) : Additional properties are not allowed ('intrus' was unexpected)\n"
    "- x : 'pas un entier' is not of type 'integer'"
)


def approved_with(arguments: dict[str, JsonValue]) -> Approver:
    async def approver(pending: PendingApproval) -> Approved:
        return Approved(by="humain", arguments=arguments)

    return approver


async def test_arguments_corrected_by_an_online_approver_are_validated() -> None:
    """Comme ceux du modèle : refusés par le schéma, l'appel ne part pas et le modèle le lit."""
    virement = RecordingTool(spec("virement", approval="always", side_effects="irreversible"))
    executor = ToolExecutor([virement])
    batch = awaiting(call("c1", "virement", x=3))
    events = [e async for e in executor.run_batch(batch, approver=approved_with(BAD_ARGUMENTS))]
    # La décision est au journal : on y lit ce que l'approbateur a voulu.
    assert [type(e).__name__ for e in events] == [
        "ApprovalRequested",
        "ApprovalGranted",
        "ToolCompleted",
    ]
    assert completed(events)["c1"] == ToolOutput.error(CORRECTION_REFUSED)
    assert virement.calls == []

    # Conformes, ils partent tels quels.
    events = [e async for e in executor.run_batch(batch, approver=approved_with({"x": 7}))]
    assert not completed(events)["c1"].is_error
    assert [arguments for arguments, _ in virement.calls] == [{"x": 7}]


async def test_arguments_corrected_by_a_human_are_validated_on_resume() -> None:
    """Reprise d'un run en pause : l'accord et ses arguments sont au journal, non contrôlés."""
    virement = RecordingTool(spec("virement", approval="always", side_effects="irreversible"))
    executor = ToolExecutor([virement])

    def corrected(arguments: dict[str, JsonValue]) -> RunState:
        accord = ApprovalGranted(
            call_id="c1", tool_name="virement", by="denis", arguments=arguments
        )
        return journaled("c1", "virement", asked("c1", "virement"), accord)

    events = await collect(executor, corrected(BAD_ARGUMENTS))
    assert completed(events)["c1"] == ToolOutput.error(CORRECTION_REFUSED)
    assert not [e for e in events if isinstance(e, ToolCalled)]
    assert virement.calls == []

    events = await collect(executor, corrected({"x": 7}))
    assert not completed(events)["c1"].is_error
    assert [arguments for arguments, _ in virement.calls] == [{"x": 7}]


async def test_corrected_arguments_follow_the_validation_setting() -> None:
    """``validate_arguments=False`` vaut aussi pour ce que corrige l'approbateur."""
    virement = RecordingTool(spec("virement", approval="always", side_effects="irreversible"))
    executor = ToolExecutor([virement], validate_arguments=False)
    batch = awaiting(call("c1", "virement", x=3))
    events = [e async for e in executor.run_batch(batch, approver=approved_with(BAD_ARGUMENTS))]
    assert not completed(events)["c1"].is_error
    assert [arguments for arguments, _ in virement.calls] == [BAD_ARGUMENTS]


async def test_unknown_tool_without_any_tool() -> None:
    outputs = completed(await collect(ToolExecutor(), awaiting(call("c1", "t"))))
    assert outputs["c1"].as_text.endswith("Outils disponibles : aucun.")


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ToolError("x doit être positif"), "x doit être positif"),
        (ValueError("mauvais"), "Erreur de l'outil cible : ValueError: mauvais"),
        (TimeoutError("api lente"), "Erreur de l'outil cible : TimeoutError: api lente"),
    ],
)
async def test_failures_become_error_results(
    error: Exception, expected: str, caplog: pytest.LogCaptureFixture
) -> None:
    target = RecordingTool(spec("cible"), error=error)
    with caplog.at_level(logging.WARNING, logger="loom_ia.engine.executor"):
        outputs = completed(await collect(ToolExecutor([target]), awaiting(call("c1", "cible"))))
    assert outputs["c1"] == ToolOutput.error(expected)
    logged = [r for r in caplog.records if r.message == "Échec de l'outil cible"]
    assert len(logged) == (0 if isinstance(error, ToolError) else 1)


async def test_a_reference_with_a_non_ascii_digit_is_refused_to_the_model() -> None:
    """``result:²`` : ``isdigit`` l'accepte, ``int`` lève, et la reprise rejouait l'appel."""
    cible = RecordingTool(spec("cible"))
    events = await collect(
        ToolExecutor([cible]),
        awaiting(call("c1", "cible", x={"$ref": "result:²"}), call("c2", "cible", x=1)),
    )
    outputs = completed(events)
    assert outputs["c1"].is_error
    assert outputs["c1"].as_text.startswith("Référence invalide : 'result:²'")
    assert not outputs["c2"].is_error
    assert [arguments for arguments, _ in cible.calls] == [{"x": 1}]


async def test_a_schema_that_cannot_be_resolved_fails_its_own_calls_only(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Schéma MCP à ``$ref`` pendant : ``check_schema`` l'accepte, la validation lève."""
    schema: dict[str, JsonValue] = {
        "type": "object",
        "properties": {"x": {"$ref": "#/$defs/absent"}},
    }
    casse = RecordingTool(
        ToolSpec(name="mcp_casse", description="d", kind="mcp", input_schema=schema)
    )
    sain = RecordingTool(spec("sain"))
    with caplog.at_level(logging.ERROR, logger="loom_ia.engine.executor"):
        events = await collect(
            ToolExecutor([casse, sain]),
            awaiting(call("c1", "mcp_casse", x=1), call("c2", "sain", x=1)),
        )
    outputs = completed(events)
    assert outputs["c1"].is_error
    assert outputs["c1"].as_text.startswith("Appel à mcp_casse impossible à préparer : ")
    assert not outputs["c2"].is_error
    assert casse.calls == []
    # L'erreur de programmation n'est pas muette : elle est journalisée, avec sa trace.
    [logged] = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert logged.getMessage() == "Préparation de l'appel à mcp_casse impossible"
    assert logged.exc_info is not None


async def test_a_failing_artifact_store_fails_the_call_that_reads_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Contenu déporté illisible pour une autre raison qu'« introuvable » : erreur d'appel."""

    class Broken(InMemoryArtifactStore):
        async def get(self, uri: str) -> bytes:
            raise PermissionError("disque illisible")

    cible = RecordingTool(spec("cible"))
    journal = RunJournal(agent="demo")
    journal.start("?").model_turn(tool_call_message(("c1", "gros", {})))
    journal.tool_results(
        {"c1": ToolOutput(blocks=(TextBlock(text="aperçu"),), offloaded="artifact://t/s/a.txt")}
    )
    journal.model_turn(
        tool_call_message(("c2", "cible", {"x": {"$ref": "result:1"}}), ("c3", "cible", {"x": 2}))
    )
    drafts = journal.take()
    state = fold([d.to_event(seq) for seq, d in enumerate(drafts, start=1)], journal.run_id)

    with caplog.at_level(logging.ERROR, logger="loom_ia.engine.executor"):
        events = await collect(ToolExecutor([cible], artifacts=Broken()), state)
    outputs = completed(events)
    assert outputs["c2"] == ToolOutput.error(
        "Appel à cible impossible à préparer : PermissionError: disque illisible"
    )
    assert not outputs["c3"].is_error
    assert [arguments for arguments, _ in cible.calls] == [{"x": 2}]
    assert len([r for r in caplog.records if r.levelno == logging.ERROR]) == 1


async def test_timeouts() -> None:
    own = RecordingTool(spec("propre", timeout=0.01), delay=1)
    default = RecordingTool(spec("defaut"), delay=1)
    unlimited = RecordingTool(spec("libre"), delay=0.03)
    executor = ToolExecutor([own, default, unlimited], default_timeout=0.02)
    outputs = completed(
        await collect(executor, awaiting(call("c1", "propre"), call("c2", "defaut")))
    )
    assert outputs["c1"] == ToolOutput.error("Délai dépassé : pas de réponse en 0.01 s.")
    assert outputs["c2"] == ToolOutput.error("Délai dépassé : pas de réponse en 0.02 s.")
    assert own.cancelled and default.cancelled

    executor.default_timeout = None
    outputs = completed(await collect(executor, awaiting(call("c3", "libre"))))
    assert not outputs["c3"].is_error


async def test_interrupted_calls_follow_the_resume_rule() -> None:
    reader = RecordingTool(spec("lire"))
    idempotent = RecordingTool(spec("payer", side_effects="irreversible", idempotent=True))
    sender = RecordingTool(spec("envoyer", side_effects="irreversible"))
    executor = ToolExecutor([reader, idempotent, sender])
    events = await collect(
        executor,
        awaiting(
            call("c1", "lire", started=True),
            call("c2", "payer", started=True),
            call("c3", "envoyer", started=True),
            call("c4", "envoyer"),
        ),
    )
    called = {e.call_id: e.resumed for e in events if isinstance(e, ToolCalled)}
    assert called == {"c1": True, "c2": True, "c4": False}
    assert completed(events)["c3"] == ToolOutput.error(UNKNOWN_STATE)
    assert [c[1].call_id for c in sender.calls] == ["c4"]
    assert len(reader.calls) == len(idempotent.calls) == 1


async def test_an_approval_given_before_the_first_launch_lets_the_tool_run_once() -> None:
    """Cas normal : l'accord précède le lancement, l'outil part une fois, sans rien d'inconnu."""
    virer = RecordingTool(spec("virer", side_effects="irreversible", approval="always"))
    state = journaled("c1", "virer", asked("c1", "virer"), granted("c1", "virer"))
    events = await collect(ToolExecutor([virer]), state)
    assert [e.resumed for e in events if isinstance(e, ToolCalled)] == [False]
    assert not completed(events)["c1"].is_error
    assert len(virer.calls) == 1
    # L'humain n'a pas vu d'effet d'état inconnu : son accord ne l'efface pas.
    assert virer.calls[0][1].replay_unknown is False


async def test_an_approved_call_that_was_launched_is_not_relaunched() -> None:
    """Accord, lancement, plantage : l'accord a servi, l'effet est d'état inconnu (#18)."""
    virer = RecordingTool(spec("virer", side_effects="irreversible", approval="always"))
    state = journaled(
        "c1", "virer", asked("c1", "virer"), granted("c1", "virer"), launched("c1", "virer")
    )
    events = await collect(ToolExecutor([virer]), state)
    assert completed(events)["c1"] == ToolOutput.error(UNKNOWN_STATE)
    assert not [e for e in events if isinstance(e, ToolCalled)]
    assert virer.calls == []


async def test_an_approved_call_that_was_launched_can_ask_a_human_again() -> None:
    """``on_unknown: pause`` : le premier accord ne répond pas à l'état inconnu, on redemande."""
    virer = RecordingTool(
        spec("virer", side_effects="irreversible", approval="always", on_unknown="pause")
    )
    state = journaled(
        "c1", "virer", asked("c1", "virer"), granted("c1", "virer"), launched("c1", "virer")
    )
    events = await collect(ToolExecutor([virer]), state)
    [demande] = [e for e in events if isinstance(e, ApprovalRequested)]
    assert demande.reason == UNKNOWN_STATE
    assert not completed(events)
    assert virer.calls == []

    # Le second accord, lui, est une réponse à l'état inconnu : l'appel repart une fois.
    second = journaled(
        "c1",
        "virer",
        asked("c1", "virer"),
        granted("c1", "virer"),
        launched("c1", "virer"),
        asked("c1", "virer", UNKNOWN_STATE),
        granted("c1", "virer"),
    )
    events = await collect(ToolExecutor([virer]), second)
    assert [e.resumed for e in events if isinstance(e, ToolCalled)] == [True]
    assert len(virer.calls) == 1
    assert virer.calls[0][1].replay_unknown is True


async def test_an_approval_asked_after_the_launch_is_spent_by_the_next_one() -> None:
    """Plantage après le lancement accordé en état inconnu : ce second accord a servi aussi."""
    virer = RecordingTool(
        spec("virer", side_effects="irreversible", on_unknown="pause", approval="never")
    )
    state = journaled(
        "c1",
        "virer",
        launched("c1", "virer"),
        asked("c1", "virer", UNKNOWN_STATE),
        granted("c1", "virer"),
        launched("c1", "virer", resumed=True),
    )
    events = await collect(ToolExecutor([virer]), state)
    assert [e.reason for e in events if isinstance(e, ApprovalRequested)] == [UNKNOWN_STATE]
    assert virer.calls == []


@pytest.mark.parametrize("options", [{}, {"idempotent": True, "side_effects": "irreversible"}])
async def test_a_call_safe_to_retry_resumes_after_its_approval(options: dict[str, object]) -> None:
    """Lecture ou outil idempotent : l'accord déjà donné vaut encore, sans nouvelle demande."""
    lire = RecordingTool(spec("lire", approval="always", on_unknown="pause", **options))
    state = journaled(
        "c1", "lire", asked("c1", "lire"), granted("c1", "lire"), launched("c1", "lire")
    )
    events = await collect(ToolExecutor([lire]), state)
    assert not [e for e in events if isinstance(e, ApprovalRequested)]
    assert not completed(events)["c1"].is_error
    assert [e.resumed for e in events if isinstance(e, ToolCalled)] == [True]
    # Pas un état inconnu qu'un humain aurait levé : la réservation périmée reste signalée.
    assert lire.calls[0][1].replay_unknown is False


async def test_tool_context() -> None:
    target = RecordingTool(spec("cible"))
    state = awaiting(call("c1", "cible", x=1), tenant="acme")
    await collect(ToolExecutor([target]), state)
    arguments, context = target.calls[0]
    assert arguments == {"x": 1}
    assert (context.tenant_id, context.session_id, context.run_id) == (
        "acme",
        state.session_id,
        state.run_id,
    )
    assert (context.call_id, context.agent, context.caller.user_id) == ("c1", "demo", "u1")


async def test_closing_the_batch_cancels_running_tools() -> None:
    fast = RecordingTool(spec("rapide"))
    slow = RecordingTool(spec("lent"), delay=5)
    executor = ToolExecutor([fast, slow])
    seen: list[ToolEvent] = []
    async with aclosing(
        executor.run_batch(awaiting(call("c1", "rapide"), call("c2", "lent")))
    ) as events:
        async for event in events:
            seen.append(event)
            if isinstance(event, ToolCompleted):
                break
    assert [type(e).__name__ for e in seen] == ["ToolCalled", "ToolCalled", "ToolCompleted"]
    assert slow.cancelled


@dataclass
class SelfCancellingTool:
    """Outil qui se termine annulé sans que le lot l'ait demandé."""

    spec: ToolSpec
    how: str

    async def invoke(self, arguments: dict[str, JsonValue], context: ToolContext) -> ToolOutput:
        if self.how == "raises":
            raise asyncio.CancelledError
        if self.how == "inner_task":
            # Une tâche que l'outil attend est annulée par ailleurs (client, pool…).
            inner = asyncio.ensure_future(asyncio.sleep(10))
            asyncio.get_running_loop().call_soon(inner.cancel)
            await inner
        else:
            task = asyncio.current_task()
            assert task is not None
            task.cancel()
            await asyncio.sleep(10)
        return ToolOutput.text("jamais")


@pytest.mark.parametrize("how", ["raises", "inner_task", "own_task"])
async def test_a_tool_cancelled_from_inside_fails_without_blocking_the_batch(
    how: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Annulé de l'intérieur, un outil n'arrête pas le run : c'est son échec, dit au modèle.

    Sans résultat pour cet appel, le lot l'attendait indéfiniment.
    """
    annule, sain = SelfCancellingTool(spec("annule"), how), RecordingTool(spec("sain"))
    with caplog.at_level(logging.WARNING, logger="loom_ia.engine.executor"):
        async with asyncio.timeout(2):
            events = await collect(
                ToolExecutor([annule, sain]), awaiting(call("c1", "annule"), call("c2", "sain"))
            )
    outputs = completed(events)
    assert outputs["c1"] == ToolOutput.error(
        "Erreur de l'outil annule : l'outil s'est annulé de lui-même (CancelledError), "
        "sans que le run ait été arrêté."
    )
    assert outputs["c2"] == ToolOutput.text("ok")
    logged = [r.getMessage() for r in caplog.records if r.name == "loom_ia.engine.executor"]
    assert logged == ["Outil annule annulé de l'intérieur"]


async def test_an_outside_cancellation_still_stops_the_batch() -> None:
    """L'arrêt du run se propage au lot, qui annule ses outils : aucun résultat n'est inventé."""
    started, interrupted = asyncio.Event(), asyncio.Event()

    @tool
    async def lent(x: int) -> str:
        """Lent."""
        started.set()
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            interrupted.set()
            raise
        return "lent"

    running = asyncio.create_task(collect(ToolExecutor([lent]), awaiting(call("c1", "lent", x=1))))
    async with asyncio.timeout(2):
        await started.wait()
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
        await interrupted.wait()
    assert running.cancelled()


def test_registration() -> None:
    first, second = RecordingTool(spec("a")), RecordingTool(spec("b"))
    executor = ToolExecutor([first])
    executor.add(second)
    assert executor.get("b") is second
    assert executor.get("c") is None
    assert [d.name for d in executor.definitions()] == ["a", "b"]
    assert executor.specs == (first.spec, second.spec)
    with pytest.raises(ValueError, match="déjà déclaré"):
        executor.add(RecordingTool(spec("a")))
    broken = ToolSpec(name="casse", description="x", kind="python", input_schema={"type": "objet"})
    with pytest.raises(SchemaError):
        executor.add(RecordingTool(broken))
    assert executor.get("casse") is None


class Fatal(BaseException):
    """Exception hors de ``Exception`` : elle ne devient pas un résultat d'erreur."""


@dataclass
class FatalTool:
    spec: ToolSpec

    async def invoke(self, arguments: dict[str, JsonValue], context: ToolContext) -> ToolOutput:
        raise Fatal("arrêt")


async def test_fatal_error_interrupts_the_batch() -> None:
    executor = ToolExecutor([FatalTool(spec("fatal"))])
    with pytest.raises(Fatal, match="arrêt"):
        await collect(executor, awaiting(call("c1", "fatal")))
