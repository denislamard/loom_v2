# SPDX-License-Identifier: Apache-2.0
"""Exécuteur d'outils : lot parallèle, contrôles, timeout, reprise."""

import asyncio
import copy
import logging
import math
import pickle
from contextlib import aclosing
from dataclasses import dataclass, field

import pytest
from jsonschema.exceptions import SchemaError
from pydantic import JsonValue

from loom_ia.adapters.artifacts import InMemoryArtifactStore
from loom_ia.adapters.stores import InMemoryEventStore
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
    AfterTool,
    Approved,
    Approver,
    CallerContext,
    Decision,
    JsonBlock,
    Message,
    ModelSpec,
    PendingApproval,
    PendingCall,
    Replace,
    Retry,
    RetryPolicy,
    RunId,
    RunState,
    RunStatus,
    TenantId,
    TextBlock,
    ToolOutput,
    ToolSpec,
    state_size,
)
from loom_ia.core.ports import ToolContext, ToolError
from loom_ia.core.projections import fold
from loom_ia.engine import (
    UNKNOWN_STATE,
    BoundPolicy,
    Policies,
    RunContext,
    StateKeyRule,
    StateRefused,
    StateRules,
    StateSourceRule,
    ToolExecutor,
    begin_run,
    drive,
)
from loom_ia.engine.executor import ToolEvent
from loom_ia.engine.session_state import pointed
from loom_ia.policies import policy
from loom_ia.testing import RunJournal, ScriptedModel, tool_call_message
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
        "- (racine) : propriété non prévue par le schéma : y\n"
        "- x : doit être un entier (reçu : du texte)"
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
        "- devis.montant : doit être du texte (reçu : un nombre) — valeur transmise par la "
        "référence result:1 (chercher)\n"
        # Une valeur écrite par le modèle est dite de même : son genre, pas son contenu.
        "- ton : doit être du texte (reçu : un nombre)"
    )


async def test_a_refused_argument_does_not_quote_its_value() -> None:
    target = RecordingTool(spec("cible"))
    events = await collect(ToolExecutor([target]), awaiting(call("c1", "cible", x="sk-SECRET-123")))
    refusal = completed(events)["c1"].as_text
    assert "x : doit être un entier (reçu : du texte)" in refusal
    assert "SECRET" not in refusal


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
    "- (racine) : propriété non prévue par le schéma : intrus\n"
    "- x : doit être un entier (reçu : du texte)"
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


# --- State de la session (option C) --------------------------------------------

CLIENT = StateKeyRule(
    schema={"type": "string", "pattern": "^C-[0-9]+$"},
    sources={"fiche": StateSourceRule(path="/code"), "clore": StateSourceRule(value=None)},
)
VILLE = StateKeyRule(
    schema={"type": "string"}, sources={"fiche": StateSourceRule(path="/adresse/ville")}
)
ETIQUETTE = StateKeyRule(schema={"enum": ["vip"]}, sources={"fiche": StateSourceRule(value="vip")})
LANGUE = StateKeyRule(schema={"enum": ["fr", "en"]}, default="fr")
FICHE: JsonValue = {"code": "C-002", "adresse": {"ville": "Dijon"}}


def data(value: JsonValue, **changes: object) -> ToolOutput:
    """Résultat d'un outil qui rend des données, comme un outil Python qui rend un ``dict``."""
    return ToolOutput(blocks=(JsonBlock(data=value),), data=value).model_copy(update=changes)


def stateful(*tools: RecordingTool, **options: object) -> ToolExecutor:
    rules = StateRules(
        {"client": CLIENT, "ville": VILLE, "etiquette": ETIQUETTE, "langue": LANGUE},
        **options,  # pyright: ignore[reportArgumentType]
    )
    return ToolExecutor(list(tools), state_rules=rules)


async def test_a_result_poses_what_its_sources_say() -> None:
    fiche = RecordingTool(spec("fiche"), result=data(FICHE))
    clore = RecordingTool(spec("clore"))
    outputs = completed(await collect(stateful(fiche, clore), awaiting(call("c1", "fiche"))))
    assert outputs["c1"].state == {"client": "C-002", "ville": "Dijon", "etiquette": "vip"}
    assert outputs["c1"].state_refused == ()
    # Le reste du résultat ne bouge pas.
    assert (outputs["c1"].data, outputs["c1"].is_error) == (FICHE, False)

    # Une valeur fixe ``null`` efface la clé ; un outil qu'aucune source ne cite n'écrit rien.
    outputs = completed(await collect(stateful(fiche, clore), awaiting(call("c1", "clore"))))
    assert outputs["c1"].state == {"client": None}
    plain = RecordingTool(spec("autre"))
    outputs = completed(await collect(stateful(plain), awaiting(call("c1", "autre"))))
    assert outputs["c1"] == ToolOutput.text("ok")


async def test_what_the_tool_writes_itself_wins_and_is_checked_the_same() -> None:
    # Sans « code » dans ses données : la source de la config ne dit rien, l'outil a écrit.
    written = data({"adresse": {"ville": "Dijon"}}, state={"client": "C-009", "intrus": 1})
    fiche = RecordingTool(spec("fiche"), result=written)
    [output] = completed(await collect(stateful(fiche), awaiting(call("c1", "fiche")))).values()
    assert output.state == {"client": "C-009", "ville": "Dijon", "etiquette": "vip"}
    assert output.state_refused == ("intrus non posé : clé non déclarée dans 'state'",)
    assert not output.is_error


@pytest.mark.parametrize(
    ("result", "options", "line"),
    [
        (
            data({"adresse": {"ville": "Dijon"}}),
            {},
            "client non posé : chemin '/code' absent du résultat",
        ),
        (data({**FICHE, "code": "X-SECRET-1"}), {}, "client non posé : ne suit pas son schéma"),
        (data(FICHE), {"max_value_bytes": 6}, "ville non posé : 7 octets, au-delà de"),
        (
            data(FICHE),
            {"max_value_bytes": 10, "max_total_bytes": 40},
            "le state de la session ferait",
        ),
    ],
)
async def test_a_refused_write_keeps_the_result_a_success(
    result: ToolOutput,
    options: dict[str, object],
    line: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """D8 (décision du 14/10) : l'outil a pu agir ; un résultat en erreur le ferait refaire."""
    fiche = RecordingTool(spec("fiche"), result=result)
    with caplog.at_level(logging.WARNING, logger="loom_ia.engine.session_state"):
        outputs = completed(
            await collect(stateful(fiche, **options), awaiting(call("c1", "fiche")))
        )
    output = outputs["c1"]
    assert not output.is_error
    assert (output.blocks, output.data) == (result.blocks, result.data)
    assert any(line in refused for refused in output.state_refused), output.state_refused
    assert line in caplog.text
    # Le motif ne recopie pas la valeur refusée : elle peut porter un secret.
    assert "SECRET" not in " ".join(output.state_refused) + caplog.text
    # Ce qui passe est posé quand même, sauf si c'est le total qui déborde.
    if "ferait" not in line:
        assert output.state.get("etiquette") == "vip"


async def test_a_result_in_error_poses_nothing() -> None:
    """Même s'il porte du state : refusé par une politique, ou rendu en erreur par l'outil."""
    failed = ToolOutput(blocks=(TextBlock(text="panne"),), is_error=True, state={"client": "C-002"})
    fiche = RecordingTool(spec("fiche"), result=failed)
    [output] = completed(await collect(stateful(fiche), awaiting(call("c1", "fiche")))).values()
    assert output.is_error and output.state == {} and output.state_refused == ()


async def test_a_tool_reads_the_state_as_it_was_when_its_batch_began(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Session, puis ouverture du run, puis lots précédents ; les clés de l'agent seules."""
    journal = RunJournal(agent="demo")
    journal.start("?", state={"ville": "Paris", "langue": "en"})
    journal.model_turn(tool_call_message(("c1", "fiche", {})))
    journal.tool_results({"c1": data(FICHE, state={"client": "C-002", "ville": "Lyon"})})
    journal.model_turn(tool_call_message(("c2", "rapide", {}), ("c3", "lent", {})))
    events = [draft.to_event(seq) for seq, draft in enumerate(journal.take(), start=1)]
    state = fold(events, journal.run_id)

    rapide = RecordingTool(spec("rapide"), result=ToolOutput(state={"client": "C-777"}))
    lent = RecordingTool(spec("lent"), delay=0.05)
    executor = stateful(rapide, lent)
    base: dict[str, JsonValue] = {"etiquette": "inconnue", "hors_agent": 1}
    with caplog.at_level(logging.WARNING, logger="loom_ia.engine.session_state"):
        events_ = [e async for e in executor.run_batch(state, session_state=base)]
    assert completed(events_)["c2"].state == {"client": "C-777"}

    [(_, context)] = lent.calls
    # Le lot précédent l'emporte sur l'ouverture, qui l'emporte sur la session ;
    # la valeur par défaut comble ; une clé hors de l'agent, ou une valeur qui ne
    # tient plus son schéma, n'est pas montrée.
    assert dict(context.state) == {"client": "C-002", "ville": "Lyon", "langue": "en"}
    assert "etiquette écartée" in caplog.text
    with pytest.raises(TypeError):
        context.state["client"] = "C-1"  # type: ignore[index]


async def test_each_call_reads_its_own_copy_of_the_state() -> None:
    """Un outil qui modifie une valeur imbriquée ne touche pas ce que lisent les autres appels."""

    @tool
    def remplit(ctx: ToolContext) -> str:
        """Ajoute au panier qu'il lit."""
        panier = ctx.state["panier"]
        assert isinstance(panier, list)
        panier.append("x")
        return "ajouté"

    @tool
    async def lit(ctx: ToolContext) -> JsonValue:
        """Lit le panier, après l'autre."""
        await asyncio.sleep(0.02)
        return ctx.state["panier"]

    rules = StateRules({"panier": StateKeyRule(schema={"type": "array"})})
    executor = ToolExecutor([remplit, lit], state_rules=rules)
    pending = awaiting(call("c1", "remplit"), call("c2", "lit"))
    session: dict[str, JsonValue] = {"panier": ["a"]}
    outputs = completed([e async for e in executor.run_batch(pending, session_state=session)])
    assert (outputs["c1"].as_text, outputs["c2"].data) == ("ajouté", ["a"])
    assert session == {"panier": ["a"]}


async def test_a_replayed_result_is_served_as_recorded() -> None:
    """Le rejeu sert le résultat du journal, qui porte déjà son state : rien n'est recalculé."""
    recorded = data(FICHE, state={"client": "C-002"}, state_refused=("ville non posé : …",))

    class Journal:
        def serves(self, tool: object, call: PendingCall, run_id: object) -> bool:
            return True

        def from_journal(self, run_id: object, call_id: str) -> bool:
            return True

        async def output(self, *args: object) -> tuple[ToolOutput, None]:
            return recorded, None

        async def approve(self, run_id: object, pending: PendingApproval) -> Approved:
            return Approved()

    executor = stateful(RecordingTool(spec("fiche")))
    executor.replay = Journal()  # pyright: ignore[reportAttributeAccessIssue]
    [output] = completed(await collect(executor, awaiting(call("c1", "fiche")))).values()
    assert output == recorded


async def test_a_double_result_is_posed_like_the_tool_result() -> None:
    """Une doublure rend un résultat neuf : le state s'y pose comme sur celui de l'outil.

    En variante, en éval ou au banc : ses sources ``from`` s'appliquent, et ce
    que la doublure écrit elle-même est contrôlé.
    """

    class Doublure:
        def serves(self, tool: object, call: PendingCall, run_id: object) -> bool:
            return True

        def from_journal(self, run_id: object, call_id: str) -> bool:
            return False

        async def output(self, *args: object) -> tuple[ToolOutput, None]:
            return data(FICHE, state={"intrus": 1}), None

        async def approve(self, run_id: object, pending: PendingApproval) -> Approved:
            return Approved()

    fiche = RecordingTool(spec("fiche"))
    executor = stateful(fiche)
    executor.replay = Doublure()  # pyright: ignore[reportAttributeAccessIssue]
    [output] = completed(await collect(executor, awaiting(call("c1", "fiche")))).values()
    assert output.state == {"client": "C-002", "ville": "Dijon", "etiquette": "vip"}
    assert output.state_refused == ("intrus non posé : clé non déclarée dans 'state'",)
    assert fiche.calls == []


async def test_a_result_read_from_the_journal_counts_for_the_batch_size() -> None:
    """D7 : ce que pose un résultat lu au journal compte pour les appels suivants du lot.

    En variante, un lot peut mêler un résultat lu et un résultat neuf : le
    second ne passe pas la limite qu'il aurait dépassée en direct.
    """
    big = StateKeyRule(schema={"type": "string"})
    rules = StateRules({"a": big, "b": big}, max_value_bytes=1000, max_total_bytes=1500)

    class Melange:
        def serves(self, tool: object, call: PendingCall, run_id: object) -> bool:
            return True

        def from_journal(self, run_id: object, call_id: str) -> bool:
            return call_id == "c1"

        async def output(
            self, run_id: object, call_id: str, *args: object
        ) -> tuple[ToolOutput, None]:
            if call_id == "c1":
                return ToolOutput(state={"a": "x" * 900}), None
            # La doublure répond après la lecture du journal.
            await asyncio.sleep(0.02)
            return ToolOutput(state={"b": "x" * 900}), None

        async def approve(self, run_id: object, pending: PendingApproval) -> Approved:
            return Approved()

    executor = ToolExecutor([RecordingTool(spec("a")), RecordingTool(spec("b"))], state_rules=rules)
    executor.replay = Melange()  # pyright: ignore[reportAttributeAccessIssue]
    outputs = completed(await collect(executor, awaiting(call("c1", "a"), call("c2", "b"))))
    assert outputs["c1"].state == {"a": "x" * 900}
    assert outputs["c2"].state == {}
    assert "max_total_bytes (1500)" in outputs["c2"].state_refused[0]


async def test_an_erasure_read_from_the_journal_frees_its_room_in_the_batch() -> None:
    """D7, D10 : la clé effacée par un résultat lu au journal ne compte plus pour la suite."""
    big = StateKeyRule(schema={"type": "string"})
    rules = StateRules({"a": big, "b": big}, max_value_bytes=1000, max_total_bytes=1000)

    class Melange:
        def serves(self, tool: object, call: PendingCall, run_id: object) -> bool:
            return True

        def from_journal(self, run_id: object, call_id: str) -> bool:
            return call_id == "c1"

        async def output(
            self, run_id: object, call_id: str, *args: object
        ) -> tuple[ToolOutput, None]:
            if call_id == "c1":
                return ToolOutput(state={"a": None}), None
            await asyncio.sleep(0.02)
            # 992 octets seul ; avec « "a":null », il passerait la limite.
            return ToolOutput(state={"b": "x" * 984}), None

        async def approve(self, run_id: object, pending: PendingApproval) -> Approved:
            return Approved()

    executor = ToolExecutor([RecordingTool(spec("a")), RecordingTool(spec("b"))], state_rules=rules)
    executor.replay = Melange()  # pyright: ignore[reportAttributeAccessIssue]
    session: dict[str, JsonValue] = {"a": "x" * 900}
    state = awaiting(call("c1", "a"), call("c2", "b"))
    outputs = completed([e async for e in executor.run_batch(state, session_state=session)])
    assert outputs["c1"].state == {"a": None}
    assert (outputs["c2"].state, outputs["c2"].state_refused) == ({"b": "x" * 984}, ())


async def test_a_sub_run_keeps_no_state_from_the_journal() -> None:
    """D3 : un résultat lu au journal pour un sous-run n'y pose rien, comme en direct."""
    recorded = data(FICHE, state={"client": "C-002"})

    class Journal:
        def serves(self, tool: object, call: PendingCall, run_id: object) -> bool:
            return True

        def from_journal(self, run_id: object, call_id: str) -> bool:
            return True

        async def output(self, *args: object) -> tuple[ToolOutput, None]:
            return recorded, None

        async def approve(self, run_id: object, pending: PendingApproval) -> Approved:
            return Approved()

    child = awaiting(call("c1", "fiche")).model_copy(
        update={"parent_run_id": RunId("parent"), "depth": 1}
    )
    executor = stateful(RecordingTool(spec("fiche")))
    executor.replay = Journal()  # pyright: ignore[reportAttributeAccessIssue]
    [output] = completed([e async for e in executor.run_batch(child)]).values()
    assert (output.state, output.state_refused) == ({}, ())
    assert output.data == FICHE


async def test_after_tool_policies_decide_which_result_writes() -> None:
    """Le state se lit sur le résultat retenu : remplacé, il vient du nouveau ; refusé, de rien."""

    @policy(points=["after_tool"], decisions=["replace", "retry"])
    def verifie(subject: AfterTool) -> Decision:
        if subject.call.call_id == "c1":
            return Retry("Résultat suspect.")
        return Replace(data({"code": "C-003"}))

    fiche = RecordingTool(spec("fiche"), result=data(FICHE))
    bound = Policies([BoundPolicy(policy=verifie, name=verifie.name, points=verifie.points)])
    state = awaiting(call("c1", "fiche"), call("c2", "fiche"))
    outputs = completed([e async for e in stateful(fiche).run_batch(state, policies=bound)])
    assert outputs["c1"].is_error and outputs["c1"].state == {}
    assert outputs["c2"].state == {"client": "C-003", "etiquette": "vip"}
    assert outputs["c2"].state_refused == (
        "ville non posé : chemin '/adresse/ville' absent du résultat",
    )


async def test_the_call_order_decides_whatever_order_the_tools_finish_in() -> None:
    """D9 : le premier appelé finit le dernier ; c'est quand même le second qui l'emporte."""
    rules = StateRules({"client": CLIENT})
    lent = RecordingTool(spec("lent"), delay=0.05, result=ToolOutput(state={"client": "C-001"}))
    rapide = RecordingTool(spec("rapide"), result=ToolOutput(state={"client": "C-002"}))
    model = ScriptedModel(
        tool_call_message(("c1", "lent", {}), ("c2", "rapide", {})), Message.assistant("Fait.")
    )
    ctx = RunContext(
        agent="demo",
        store=InMemoryEventStore(),
        model=model,
        model_spec=ModelSpec(id="M", sdk="fake", model="m", retry=RetryPolicy(initial_delay=0)),
        tools=ToolExecutor([lent, rapide], state_rules=rules),
    )
    started = await begin_run(ctx, "?")
    state = await drive(ctx, started.run_id)
    events = await ctx.store.read(state.context.tenant_id, state.session_id)
    arrived = [e.payload.call_id for e in events if isinstance(e.payload, ToolCompleted)]
    assert arrived == ["c2", "c1"]
    assert state.state_delta == {"client": "C-002"}


async def test_a_resumed_batch_shows_the_same_state_to_its_remaining_calls() -> None:
    """Reprise d'un lot interrompu : un voisin déjà fini ne change pas ce que voient les autres."""
    journal = RunJournal(agent="demo")
    journal.start("?")
    journal.model_turn(tool_call_message(("c1", "rapide", {}), ("c2", "lent", {})))
    drafts = journal.take()
    done = ToolCompleted(
        call_id="c1", tool_name="rapide", output=ToolOutput(state={"client": "C-777"})
    )
    drafts.append(journal.scope.draft(done))
    events = [draft.to_event(seq) for seq, draft in enumerate(drafts, start=1)]
    state = fold(events, journal.run_id)
    assert [c.call_id for c in state.pending_calls] == ["c2"]

    lent = RecordingTool(spec("lent"))
    await collect(stateful(lent), state)
    [(_, context)] = lent.calls
    assert dict(context.state) == {"langue": "fr"}


def test_a_pointer_reads_objects_and_lists() -> None:
    """RFC 6901 : « ~1 » pour « / », « ~0 » pour « ~ », un indice sans zéro en tête."""
    value: JsonValue = {"lignes": [{"n": 1}, {"n": 2}], "a/b": {"~": "ok"}}
    assert pointed(value, "") == value
    assert pointed(value, "/lignes/1/n") == 2
    assert pointed(value, "/a~1b/~0") == "ok"
    for path in ("/lignes/2", "/lignes/01", "/lignes/-", "/lignes/x", "/absent", "/lignes/0/n/x"):
        assert isinstance(pointed(value, path), type(pointed(None, ""))), path
    assert repr(StateRules({"client": CLIENT})) == "StateRules(client)"


async def test_a_schema_that_does_not_finish_refuses_the_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un motif trop coûteux ne fait pas poser une valeur qu'on n'a pas pu vérifier."""
    monkeypatch.setattr(bounded_regex, "REGEX_TIMEOUT", 0.2)
    slow = StateKeyRule(
        schema={"type": "string", "pattern": "^(a|aa)+$"},
        sources={"fiche": StateSourceRule(path="/code")},
    )
    fiche = RecordingTool(spec("fiche"), result=data({"code": "a" * 64 + "!"}))
    executor = ToolExecutor([fiche], state_rules=StateRules({"code": slow}))
    [output] = completed(await collect(executor, awaiting(call("c1", "fiche")))).values()
    assert output.state == {}
    [line] = output.state_refused
    assert line.startswith("code non posé : son schéma ne s'applique pas")


async def test_a_replayed_result_keeps_its_state_whatever_the_policies_rebuild() -> None:
    """Au rejeu, un repli de contrat ou une politique qui remplace le résultat en rend un neuf."""
    recorded = data(FICHE, state={"client": "C-002"}, state_refused=("ville non posé : …",))

    class Journal:
        def serves(self, tool: object, call: PendingCall, run_id: object) -> bool:
            return True

        def from_journal(self, run_id: object, call_id: str) -> bool:
            return True

        async def output(self, *args: object) -> tuple[ToolOutput, None]:
            return recorded, None

        async def approve(self, run_id: object, pending: PendingApproval) -> Approved:
            return Approved()

    @policy(points=["after_tool"], decisions=["replace", "retry"])
    def refait(subject: AfterTool) -> Decision:
        if subject.call.call_id == "c1":
            return Replace(ToolOutput.text("repli"))
        return Retry("refusé")

    executor = stateful(RecordingTool(spec("fiche")))
    executor.replay = Journal()  # pyright: ignore[reportAttributeAccessIssue]
    bound = Policies([BoundPolicy(policy=refait, name=refait.name, points=refait.points)])
    state = awaiting(call("c1", "fiche"), call("c2", "fiche"))
    outputs = completed([e async for e in executor.run_batch(state, policies=bound)])
    assert outputs["c1"].as_text == "repli"
    assert (outputs["c1"].state, outputs["c1"].state_refused) == (
        recorded.state,
        recorded.state_refused,
    )
    assert outputs["c2"].is_error and outputs["c2"].state == {}


async def test_a_batch_cannot_grow_the_state_beyond_its_limit_together() -> None:
    """Chaque appel compte ce que les précédents du lot ont posé (D7)."""
    big = StateKeyRule(schema={"type": "string"})
    rules = StateRules({"a": big, "b": big, "c": big}, max_value_bytes=1000, max_total_bytes=2500)
    tools = [
        RecordingTool(spec(name), result=ToolOutput(state={name: "x" * 900}))
        for name in ("a", "b", "c")
    ]
    executor = ToolExecutor(tools, state_rules=rules)
    state = awaiting(call("c1", "a"), call("c2", "b"), call("c3", "c"))
    outputs = completed(await collect(executor, state))
    posed = {k: v for output in outputs.values() for k, v in output.state.items()}
    refused = [line for output in outputs.values() for line in output.state_refused]
    assert len(posed) == 2 and len(refused) == 1
    assert "max_total_bytes (2500)" in refused[0]


async def test_a_session_over_its_limit_can_still_shrink() -> None:
    """Limite abaissée depuis : grossir est refusé, réduire passe."""
    big = StateKeyRule(schema={"type": "string"})
    # 1 817 octets en session ; même réduit, le state reste au-delà de la limite.
    rules = StateRules({"a": big, "b": big}, max_value_bytes=1000, max_total_bytes=1000)
    session: dict[str, JsonValue] = {"a": "x" * 900, "b": "x" * 900}
    shrink = RecordingTool(spec("reduit"), result=ToolOutput(state={"a": "x" * 100}))
    grow = RecordingTool(spec("grossit"), result=ToolOutput(state={"b": "x" * 950}))
    executor = ToolExecutor([shrink, grow], state_rules=rules)
    state = awaiting(call("c1", "reduit"))
    [output] = completed(
        [e async for e in executor.run_batch(state, session_state=session)]
    ).values()
    assert output.state == {"a": "x" * 100}
    state = awaiting(call("c1", "grossit"))
    [output] = completed(
        [e async for e in executor.run_batch(state, session_state=session)]
    ).values()
    assert output.state == {}
    assert "ferait" in output.state_refused[0]


async def test_nan_and_infinity_are_never_posed() -> None:
    """Le journal les écrirait ``null`` : la clé serait effacée, et la relecture différerait."""
    score = StateKeyRule(schema={"type": "number"}, sources={"fiche": StateSourceRule(path="")})
    rules = StateRules({"score": score})
    for value in (math.nan, math.inf, -math.inf):
        fiche = RecordingTool(spec("fiche"), result=data(value))
        executor = ToolExecutor([fiche], state_rules=rules)
        [output] = completed(await collect(executor, awaiting(call("c1", "fiche")))).values()
        assert (output.state, output.is_error) == ({}, False)
        problem = "contient NaN ou un infini, que le JSON du journal n'écrit pas"
        assert output.state_refused == (f"score non posé : {problem}",)
        assert rules.opening({"score": [1, {"x": value}]}, {}) == [f"score : {problem}"]
    assert rules.opening({"score": 1.5}, {}) == []


def test_a_refused_state_survives_copy_and_pickle() -> None:
    refused = StateRefused(["a : clé non déclarée dans 'state'", "b : clé obligatoire sans valeur"])
    for twin in (copy.copy(refused), pickle.loads(pickle.dumps(refused))):
        assert isinstance(twin, StateRefused)
        assert twin.problems == refused.problems and str(twin) == str(refused)


def test_the_application_can_still_shrink_a_session_over_its_limit() -> None:
    """À l'ouverture d'un run, comme pour un outil : seul ce qui fait grossir est refusé."""
    text = StateKeyRule(schema={"type": "string"})
    rules = StateRules({"a": text, "b": text}, max_value_bytes=1000, max_total_bytes=1000)
    # 1 217 octets en session : la limite a été abaissée depuis.
    session: dict[str, JsonValue] = {"a": "x" * 600, "b": "y" * 600}
    # Même réduite, la session reste au-delà de sa limite ; elle ne grossit pas.
    assert rules.opening({"a": "x" * 500}, session) == []
    assert rules.opening({"a": None}, session) == []
    grown: dict[str, JsonValue] = {"a": "x" * 700, "b": "y" * 600}
    assert rules.opening({"a": "x" * 700}, session) == [
        f"le state de la session ferait {state_size(grown)} octets, "
        "au-delà de sessions.state.max_total_bytes (1000)"
    ]
    # Sans clé obligatoire, rien ne manque jamais.
    assert rules.opening({}, {}) == []


async def test_a_sub_run_neither_reads_nor_writes_the_state() -> None:
    """D3 : ni valeurs, ni valeurs par défaut ; ce que ses outils écrivent est retiré sans bruit."""
    fiche = RecordingTool(spec("fiche"), result=data(FICHE, state={"langue": "en"}))
    child = awaiting(call("c1", "fiche")).model_copy(
        update={"parent_run_id": RunId("parent"), "depth": 1}
    )
    session: dict[str, JsonValue] = {"client": "C-002"}
    events = [e async for e in stateful(fiche).run_batch(child, session_state=session)]
    [(_, context)] = fiche.calls
    assert dict(context.state) == {}
    [output] = completed(events).values()
    assert (output.state, output.state_refused) == ({}, ())


async def test_an_agent_without_state_drops_what_its_tools_write() -> None:
    """Un outil partagé qui écrit du state, chez un agent qui n'en déclare pas : rien au modèle."""
    tool_ = RecordingTool(spec("fiche"), result=data(FICHE, state={"client": "C-002"}))
    [output] = completed(
        await collect(ToolExecutor([tool_]), awaiting(call("c1", "fiche")))
    ).values()
    assert (output.state, output.state_refused) == ({}, ())


def test_a_stale_value_gives_way_to_the_default() -> None:
    """Une valeur qui ne tient plus son schéma laisse la place à la valeur par défaut."""
    rules = StateRules({"langue": LANGUE, "client": CLIENT})
    assert dict(rules.visible({"langue": "de", "client": "X"})) == {"langue": "fr"}
    assert dict(rules.visible({"langue": "en", "client": "C-1"})) == {
        "langue": "en",
        "client": "C-1",
    }
