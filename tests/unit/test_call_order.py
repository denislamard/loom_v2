# SPDX-License-Identifier: Apache-2.0
"""Ordre des appels d'un même tour : la barrière sur les effets.

Voir ``docs/moteur-ordre-des-appels-analyse.md``. Les appels sans effet d'une
réponse partent ensemble ; un appel à effet attend ceux qui le précèdent, et
ceux qui le suivent l'attendent. Un appel qui attend un humain retient ceux
qui le suivent.
"""

import asyncio
from collections.abc import AsyncGenerator, Mapping, Sequence
from contextlib import aclosing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import yaml
from conftest import DEV, secured
from pydantic import JsonValue

from loom_ia.access.api import Loom, RunResult
from loom_ia.access.cli import main
from loom_ia.access.progress import Progress
from loom_ia.config import ConfigError, load_config
from loom_ia.core.events import (
    ApprovalRequested,
    DurablePayload,
    Event,
    ModelResponded,
    RunTransitioned,
    ToolCalled,
    ToolCompleted,
)
from loom_ia.core.model import (
    CONTINUE,
    AfterTool,
    CallerContext,
    Decision,
    Fail,
    PendingCall,
    RunId,
    RunState,
    RunStatus,
    SessionId,
    TenantId,
    ToolOutput,
    ToolSpec,
)
from loom_ia.core.ports import CallAssessment, ToolContext, ToolError, UnknownEffect
from loom_ia.core.projections import fold
from loom_ia.engine import UNKNOWN_STATE, BoundPolicy, Policies, ToolExecutor
from loom_ia.engine.delegated import (
    Consumption,
    DelegatedPayload,
    DelegatedTool,
    Exchange,
    RunView,
    Waiting,
)
from loom_ia.engine.executor import CallOrder, ToolEvent
from loom_ia.policies import policy
from loom_ia.testing import RunJournal, assert_replays, tool_call_message


@dataclass
class Disque:
    """Un dossier en mémoire, et le déroulé des appels : (début|fin, appel)."""

    files: dict[str, str] = field(default_factory=dict[str, str])
    log: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])


def schema() -> dict[str, JsonValue]:
    return {
        "type": "object",
        "properties": {
            "action": {"type": "string"},
            "nom": {"type": "string"},
            "texte": {"type": "string"},
            "delai": {"type": "number"},
        },
        "additionalProperties": False,
    }


@dataclass
class Fichiers:
    """Un outil qui lit ou écrit selon ``action``, et le dit appel par appel (``loom.word``)."""

    disque: Disque
    name: str = "fichiers"
    approval: str = "never"
    on_unknown: str = "error"
    timeout: float | None = None
    fail: Exception | None = None
    # Lectures à approuver (un dossier sous ``approval: { read: always }``).
    read_approval: bool = False

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec.model_validate(
            {
                "name": self.name,
                "description": "Lit ou écrit un fichier.",
                "kind": "python",
                "input_schema": schema(),
                "side_effects": "reversible",
                "approval": self.approval,
                "on_unknown": self.on_unknown,
                "timeout": self.timeout,
            }
        )

    def assess_call(self, arguments: Mapping[str, JsonValue]) -> CallAssessment:
        if arguments.get("action") == "read":
            return CallAssessment(
                side_effects="none",
                idempotent=True,
                approval="always" if self.read_approval else "never",
            )
        return CallAssessment(
            side_effects="reversible",
            idempotent=True,
            approval="always" if self.approval == "always" else "never",
        )

    async def invoke(self, arguments: dict[str, JsonValue], context: ToolContext) -> ToolOutput:
        nom = str(arguments["nom"])
        self.disque.log.append(("début", context.call_id))
        await asyncio.sleep(float(str(arguments.get("delai", 0.0))))
        if self.fail is not None and arguments.get("action") != "read":
            self.disque.log.append(("fin", context.call_id))
            raise self.fail
        if arguments.get("action") == "read":
            text = self.disque.files.get(nom, "introuvable")
        else:
            self.disque.files[nom] = str(arguments["texte"])
            text = f"écrit : {nom}"
        self.disque.log.append(("fin", context.call_id))
        return ToolOutput.text(text)


def lire(call_id: str, nom: str = "note.txt", delai: float = 0.0) -> PendingCall:
    return PendingCall(
        call_id=call_id, name="fichiers", arguments={"action": "read", "nom": nom, "delai": delai}
    )


def ecrire(call_id: str, texte: str, nom: str = "note.txt", delai: float = 0.05) -> PendingCall:
    return PendingCall(
        call_id=call_id,
        name="fichiers",
        arguments={"action": "write", "nom": nom, "texte": texte, "delai": delai},
    )


def awaiting(*calls: PendingCall) -> RunState:
    journal = RunJournal(agent="demo", tenant_id=TenantId("default"))
    journal.start("?", context=CallerContext(tenant_id=TenantId("default"), user_id="u1"))
    events = [draft.to_event(seq) for seq, draft in enumerate(journal.take(), start=1)]
    return fold(events, journal.run_id).model_copy(
        update={"status": RunStatus.AWAITING_TOOLS, "pending_calls": calls}
    )


async def collect(executor: ToolExecutor, state: RunState) -> list[ToolEvent]:
    async with asyncio.timeout(5):
        return [event async for event in executor.run_batch(state)]


def trace(events: list[ToolEvent]) -> list[str]:
    """Le journal du lot, en court : « called c1 », « completed c1 », « approval c2 »."""
    names = {
        ToolCalled: "called",
        ToolCompleted: "completed",
        ApprovalRequested: "approval",
    }
    return [
        f"{names[type(e)]} {e.call_id}"
        for e in events
        if isinstance(e, ToolCalled | ToolCompleted | ApprovalRequested)
    ]


def results(events: list[ToolEvent]) -> dict[str, str]:
    return {e.call_id: e.output.as_text for e in events if isinstance(e, ToolCompleted)}


# --- La course et sa correction ----------------------------------------------------


async def test_a_read_after_a_write_in_the_same_turn_reads_the_new_version() -> None:
    disque = Disque(files={"note.txt": "version 1"})
    executor = ToolExecutor([Fichiers(disque)])

    events = await collect(executor, awaiting(ecrire("w", "version 2"), lire("r")))

    assert results(events)["r"] == "version 2"
    assert trace(events) == ["called w", "completed w", "called r", "completed r"]


async def test_without_ordering_the_read_overtakes_the_write() -> None:
    """``order: free`` : le comportement d'avant, celui des journaux du POC shaper."""
    disque = Disque(files={"note.txt": "version 1"})
    executor = ToolExecutor([Fichiers(disque)], order="free")

    events = await collect(executor, awaiting(ecrire("w", "version 2"), lire("r")))

    assert results(events)["r"] == "version 1"
    assert trace(events) == ["called w", "called r", "completed r", "completed w"]


async def test_reads_still_run_together() -> None:
    """Trois lectures d'un même tour se chevauchent : aucune n'attend la fin d'une autre."""
    disque = Disque(files={"a": "A", "b": "B", "c": "C"})
    executor = ToolExecutor([Fichiers(disque)])

    events = await collect(
        executor, awaiting(lire("r1", "a", 0.05), lire("r2", "b", 0.05), lire("r3", "c", 0.05))
    )

    assert [step for step, _ in disque.log] == ["début"] * 3 + ["fin"] * 3
    assert trace(events)[:3] == ["called r1", "called r2", "called r3"]


async def test_a_write_waits_for_the_reads_before_it_and_holds_the_reads_after_it() -> None:
    disque = Disque(files={"note.txt": "v1"})
    executor = ToolExecutor([Fichiers(disque)])

    events = await collect(
        executor,
        awaiting(
            lire("r1", delai=0.05),
            lire("r2", delai=0.02),
            ecrire("w", "v2"),
            lire("r3"),
            lire("r4"),
        ),
    )

    assert results(events) == {
        "r1": "v1",
        "r2": "v1",
        "w": "écrit : note.txt",
        "r3": "v2",
        "r4": "v2",
    }
    order = trace(events)
    # Les deux premières lectures ensemble, puis l'écriture seule, puis les deux dernières.
    assert order[:2] == ["called r1", "called r2"]
    assert order[2:4] == ["completed r2", "completed r1"]
    assert order[4:6] == ["called w", "completed w"]
    assert order[6:8] == ["called r3", "called r4"]


async def test_two_writes_keep_the_order_the_model_wrote() -> None:
    """La première, plus lente, ne se fait plus doubler par la seconde."""
    disque = Disque()
    executor = ToolExecutor([Fichiers(disque)])

    await collect(
        executor, awaiting(ecrire("w1", "lente", delai=0.05), ecrire("w2", "rapide", delai=0.0))
    )

    assert disque.files["note.txt"] == "rapide"
    assert disque.log == [("début", "w1"), ("fin", "w1"), ("début", "w2"), ("fin", "w2")]


async def test_free_order_keeps_the_previous_journal() -> None:
    """Sans ordre, tous les ``tool.called`` précèdent la première exécution, comme avant."""
    disque = Disque(files={"note.txt": "v1"})
    executor = ToolExecutor([Fichiers(disque)], order="free")

    events = await collect(executor, awaiting(lire("r1"), ecrire("w", "v2"), lire("r2")))

    assert trace(events)[:3] == ["called r1", "called w", "called r2"]
    assert len(disque.log) == 6


# --- Échecs au milieu d'une chaîne -------------------------------------------------


async def test_a_failed_write_does_not_stop_the_chain() -> None:
    disque = Disque(files={"note.txt": "v1"})
    executor = ToolExecutor([Fichiers(disque, fail=ToolError("disque plein"))])

    events = await collect(executor, awaiting(ecrire("w", "v2"), lire("r")))

    assert results(events) == {"w": "disque plein", "r": "v1"}
    assert trace(events) == ["called w", "completed w", "called r", "completed r"]


async def test_a_timed_out_write_does_not_stop_the_chain() -> None:
    disque = Disque(files={"note.txt": "v1"})
    executor = ToolExecutor([Fichiers(disque, timeout=0.02)])

    events = await collect(executor, awaiting(ecrire("w", "v2", delai=1.0), lire("r")))

    assert results(events)["w"].startswith("Délai dépassé")
    assert results(events)["r"] == "v1"
    assert trace(events) == ["called w", "completed w", "called r", "completed r"]


async def test_an_unknown_effect_said_to_the_model_does_not_stop_the_chain() -> None:
    disque = Disque(files={"note.txt": "v1"})
    executor = ToolExecutor([Fichiers(disque, fail=UnknownEffect("réservation périmée"))])

    events = await collect(executor, awaiting(ecrire("w", "v2"), lire("r")))

    assert results(events) == {"r": "v1", "w": "réservation périmée"}
    assert trace(events) == ["called w", "called r", "completed r", "completed w"]


async def test_an_unknown_effect_a_human_must_decide_stops_the_chain() -> None:
    """``on_unknown: pause`` : l'appel sera peut-être relancé ; ce qui le suit l'attend."""
    disque = Disque(files={"note.txt": "v1"})
    tool = Fichiers(disque, on_unknown="pause", fail=UnknownEffect("réservation périmée"))
    executor = ToolExecutor([tool])

    events = await collect(executor, awaiting(ecrire("w", "v2"), lire("r")))

    assert trace(events) == ["called w", "approval w"]
    assert ("début", "r") not in disque.log


async def test_closing_the_batch_launches_nothing_more() -> None:
    """Une annulation entre deux appels ordonnés : le second n'a jamais de ``tool.called``."""
    disque = Disque(files={"note.txt": "v1"})
    executor = ToolExecutor([Fichiers(disque)])
    seen: list[ToolEvent] = []

    async with aclosing(
        executor.run_batch(awaiting(ecrire("w", "v2", delai=10.0), lire("r")))
    ) as batch:
        async for event in batch:
            seen.append(event)
            if isinstance(event, ToolCalled):
                break
    await asyncio.sleep(0)

    assert trace(seen) == ["called w"]
    assert ("début", "r") not in disque.log
    assert disque.files["note.txt"] == "v1"


# --- Approbations ------------------------------------------------------------------


async def test_a_write_waiting_for_an_approval_holds_back_the_calls_after_it() -> None:
    disque = Disque(files={"note.txt": "v1"})
    executor = ToolExecutor([Fichiers(disque, approval="always")])

    events = await collect(
        executor, awaiting(lire("r1"), ecrire("w", "v2"), lire("r2"), ecrire("w2", "v3"))
    )

    # La lecture d'avant passe ; l'écriture attend un humain ; ce qui suit attend avec elle,
    # et la seconde écriture, à approuver elle aussi, est demandée dans la même pause.
    assert trace(events) == ["called r1", "completed r1", "approval w", "approval w2"]
    assert [call for _, call in disque.log] == ["r1", "r1"]


async def test_two_writes_to_approve_are_asked_together_and_run_in_order() -> None:
    """Toutes deux demandées dans la même pause ; accordées, elles partent dans l'ordre."""
    disque = Disque(files={"note.txt": "v1"})
    executor = ToolExecutor([Fichiers(disque, approval="always")])

    events = await collect(
        executor, awaiting(ecrire("w1", "v2", delai=0.05), lire("r"), ecrire("w2", "v3"))
    )

    # La lecture entre les deux attend sans rien écrire ; aucune politique n'a tourné pour elle.
    assert trace(events) == ["approval w1", "approval w2"]
    assert disque.log == []


async def test_without_ordering_two_writes_to_approve_are_asked_together() -> None:
    disque = Disque(files={"note.txt": "v1"})
    executor = ToolExecutor([Fichiers(disque, approval="always")], order="free")

    events = await collect(executor, awaiting(ecrire("w1", "v2"), ecrire("w2", "v3")))

    assert trace(events) == ["approval w1", "approval w2"]


async def test_a_read_waiting_for_an_approval_holds_back_only_the_writes_after_it() -> None:
    disque = Disque(files={"note.txt": "v1", "autre": "A"})

    @dataclass
    class Lecteur(Fichiers):
        """Un second outil de fichiers, dont les lectures demandent une approbation."""

        name: str = "secret"
        read_approval: bool = True

    executor = ToolExecutor([Fichiers(disque), Lecteur(disque)])
    secret = PendingCall(call_id="s", name="secret", arguments={"action": "read", "nom": "autre"})

    events = await collect(executor, awaiting(secret, lire("r"), ecrire("w", "v2")))

    assert trace(events) == ["called r", "completed r", "approval s"]
    assert disque.files["note.txt"] == "v1"


# --- Reprise -----------------------------------------------------------------------


def journaled(*payloads: DurablePayload) -> RunState:
    """Un run relu au journal : le modèle a demandé ``w`` puis ``r``, puis ces événements."""
    journal = RunJournal(agent="demo")
    journal.start("?").model_turn(
        tool_call_message(
            ("w", "fichiers", {"action": "write", "nom": "note.txt", "texte": "v2", "delai": 0.0}),
            ("r", "fichiers", {"action": "read", "nom": "note.txt", "delai": 0.0}),
        )
    )
    drafts = [*journal.take(), *(journal.scope.draft(p) for p in payloads)]
    return fold([d.to_event(seq) for seq, d in enumerate(drafts, start=1)], journal.run_id)


def called(call_id: str) -> ToolCalled:
    arguments: dict[str, JsonValue] = (
        {"action": "write", "nom": "note.txt", "texte": "v2", "delai": 0.0}
        if call_id == "w"
        else {"action": "read", "nom": "note.txt", "delai": 0.0}
    )
    return ToolCalled(
        call_id=call_id, tool_name="fichiers", tool_kind="python", arguments=arguments
    )


async def test_a_stop_between_two_ordered_calls_resumes_the_second_as_new() -> None:
    """Arrêt après l'écriture, avant la lecture : la lecture part, sans « état inconnu »."""
    disque = Disque(files={"note.txt": "v2"})
    done = ToolCompleted(call_id="w", tool_name="fichiers", output=ToolOutput.text("écrit"))
    state = journaled(called("w"), done)

    events = await collect(ToolExecutor([Fichiers(disque)]), state)

    first = events[0]
    assert isinstance(first, ToolCalled) and (first.call_id, first.resumed) == ("r", False)
    assert results(events) == {"r": "v2"}


async def test_a_stop_during_the_write_resumes_it_before_the_read() -> None:
    """Arrêt pendant l'écriture : elle est reprise (sûre à refaire), puis la lecture part."""
    disque = Disque(files={"note.txt": "v1"})
    state = journaled(called("w"))

    events = await collect(ToolExecutor([Fichiers(disque)]), state)

    assert trace(events) == ["called w", "completed w", "called r", "completed r"]
    resumed = [e.resumed for e in events if isinstance(e, ToolCalled)]
    assert resumed == [True, False]
    assert results(events)["r"] == "v2"


async def test_a_write_of_unknown_effect_on_resume_is_settled_before_the_read() -> None:
    """Non sûre à refaire, l'écriture reprise rend l'état inconnu au modèle ; la lecture suit."""
    disque = Disque(files={"note.txt": "v1"})

    @dataclass
    class NonSure(Fichiers):
        def assess_call(self, arguments: Mapping[str, JsonValue]) -> CallAssessment:
            found = super().assess_call(arguments)
            return CallAssessment(side_effects=found.side_effects, idempotent=False)

    state = journaled(called("w"))

    events = await collect(ToolExecutor([NonSure(disque)]), state)

    assert results(events) == {"w": UNKNOWN_STATE, "r": "v1"}


# --- Sous-agent en pause -------------------------------------------------------------


class Enfant(DelegatedTool):
    """Un sous-agent dont le run s'arrête sur une approbation."""

    def __init__(self) -> None:
        self.ran = 0

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="enfant",
            description="Délègue.",
            kind="agent",
            side_effects="irreversible",
            idempotent=True,
        )

    def child_run_id(self, call: PendingCall) -> RunId | None:
        return RunId("enfant-1")

    def run(
        self, arguments: dict[str, JsonValue], context: ToolContext, run: RunView
    ) -> AsyncGenerator[DelegatedPayload | Consumption | Exchange | ToolOutput | Waiting]:
        async def go() -> AsyncGenerator[
            DelegatedPayload | Consumption | Exchange | ToolOutput | Waiting
        ]:
            self.ran += 1
            yield Waiting(call_id=context.call_id, run_id=RunId("enfant-1"))

        return go()


async def test_a_paused_sub_agent_holds_back_the_calls_after_it() -> None:
    disque = Disque(files={"note.txt": "v1"})
    enfant = Enfant()
    executor = ToolExecutor([Fichiers(disque), enfant])
    delegation = PendingCall(call_id="e", name="enfant", arguments={})

    events = await collect(executor, awaiting(lire("r1"), delegation, lire("r2")))

    assert trace(events) == ["called r1", "completed r1", "called e"]
    assert any(isinstance(e, Waiting) for e in events)
    assert [call for _, call in disque.log] == ["r1", "r1"]
    assert enfant.ran == 1


class Delegue(DelegatedTool):
    """Un rôle (sans effet) ou un sous-agent (à effet) qui répond après un délai."""

    def __init__(self, name: str, kind: str, log: list[tuple[str, str]]) -> None:
        self.name, self.kind, self.log = name, kind, log

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec.model_validate(
            {
                "name": self.name,
                "description": self.name,
                "kind": self.kind,
                "side_effects": "none" if self.kind == "role" else "irreversible",
                "idempotent": True,
            }
        )

    def child_run_id(self, call: PendingCall) -> RunId | None:
        return RunId(f"enfant-{call.call_id}") if self.kind == "agent" else None

    def run(
        self, arguments: dict[str, JsonValue], context: ToolContext, run: RunView
    ) -> AsyncGenerator[DelegatedPayload | Consumption | Exchange | ToolOutput | Waiting]:
        async def go() -> AsyncGenerator[
            DelegatedPayload | Consumption | Exchange | ToolOutput | Waiting
        ]:
            self.log.append(("début", context.call_id))
            await asyncio.sleep(0.03)
            self.log.append(("fin", context.call_id))
            yield ToolOutput.text("ok")

        return go()


async def test_two_roles_of_a_turn_still_run_together() -> None:
    log: list[tuple[str, str]] = []
    executor = ToolExecutor([Delegue("redacteur", "role", log), Delegue("relecteur", "role", log)])

    await collect(
        executor,
        awaiting(
            PendingCall(call_id="a", name="redacteur", arguments={}),
            PendingCall(call_id="b", name="relecteur", arguments={}),
        ),
    )

    assert [step for step, _ in log] == ["début", "début", "fin", "fin"]


async def test_two_sub_agents_of_a_turn_now_run_one_after_the_other() -> None:
    """Un sous-agent a les effets des outils de son agent : déclaré ``irreversible``."""
    log: list[tuple[str, str]] = []
    executor = ToolExecutor([Delegue("compta", "agent", log), Delegue("secretaire", "agent", log)])

    await collect(
        executor,
        awaiting(
            PendingCall(call_id="a", name="compta", arguments={"message": "?"}),
            PendingCall(call_id="b", name="secretaire", arguments={"message": "?"}),
        ),
    )

    assert log == [("début", "a"), ("fin", "a"), ("début", "b"), ("fin", "b")]


def test_call_order_values() -> None:
    orders: tuple[CallOrder, ...] = ("effects", "free")
    assert ToolExecutor().order == "effects"
    assert {ToolExecutor(order=o)._copy().order for o in orders} == set(orders)  # pyright: ignore[reportPrivateUsage]


# --- De bout en bout : pause, reprise, affichage, consommation ---------------------

OUTILS_FICHIERS = '''
import asyncio
from pathlib import Path

from loom_ia.tools import tool

DOSSIER = Path(__file__).parent / "travail"


@tool(side_effects="reversible", idempotent=True)
async def ecrire(nom: str, texte: str) -> str:
    """Écrit un fichier, lentement."""
    await asyncio.sleep(0.05)
    (DOSSIER / nom).write_text(texte, encoding="utf-8")
    return f"écrit : {nom}"


@tool
async def lire(nom: str) -> str:
    """Lit un fichier."""
    chemin = DOSSIER / nom
    return chemin.read_text(encoding="utf-8") if chemin.exists() else f"{nom} : introuvable"
'''

# Une lecture, une écriture à approuver, une relecture : dans la même réponse.
SCRIPT_E2E: list[dict[str, object]] = [
    {
        "text": "Je lis, j'écris, je relis.",
        "tool_calls": [
            {"name": "lire", "arguments": {"nom": "note.txt"}},
            {"name": "ecrire", "arguments": {"nom": "note.txt", "texte": "version 2"}},
            {"name": "lire", "arguments": {"nom": "note.txt"}},
        ],
    },
    {"text": "C'est fait."},
]


def atelier(
    tmp_path: Path,
    *,
    order: str,
    approval: str | None,
    script: list[dict[str, object]] | None = None,
) -> Path:
    (tmp_path / "agents").mkdir()
    (tmp_path / "travail").mkdir()
    (tmp_path / "travail" / "note.txt").write_text("version 1", encoding="utf-8")
    (tmp_path / "outils_ordre.py").write_text(OUTILS_FICHIERS, encoding="utf-8")
    config: dict[str, Any] = {
        "version": 1,
        "imports": ["outils_ordre"],
        "models": [
            {
                "id": "FAKE",
                "sdk": "fake",
                "model": "fake-1",
                "params": {"script": script or SCRIPT_E2E},
            }
        ],
        "storage": {"events": {"backend": "jsonl", "path": "data"}},
        "telemetry": {"logging": {"level": "CRITICAL"}},
        "execution": {"tools": {"order": order}},
    }
    (tmp_path / "loom.yaml").write_text(yaml.safe_dump(secured(config)), encoding="utf-8")
    ecrire_ref: dict[str, Any] = {"python": "ecrire"}
    if approval is not None:
        ecrire_ref["approval"] = approval
    agent = {
        "name": "demo",
        "description": "Ordre des appels.",
        "main": {"model": "FAKE", "system": "Tu écris."},
        "tools": [{"python": "lire"}, ecrire_ref],
    }
    (tmp_path / "agents" / "demo.yaml").write_text(yaml.safe_dump(agent), encoding="utf-8")
    return tmp_path / "loom.yaml"


SESSION_E2E = SessionId("ordre")


def deroule(events: Sequence[Event]) -> list[str]:
    """Le journal en court : « called lire#0 », « completed lire#0 → version 1 »…

    Le numéro est la place de l'appel dans la réponse du modèle (fin de son identifiant).
    """
    lines: list[str] = []
    for event in events:
        payload = event.payload
        if isinstance(payload, ToolCalled | ToolCompleted | ApprovalRequested):
            name = f"{payload.tool_name}#{payload.call_id.rsplit('_', 1)[-1]}"
            match payload:
                case ToolCalled():
                    lines.append(f"called {name}")
                case ToolCompleted(output=output):
                    lines.append(f"completed {name} → {output.as_text}")
                case _:
                    lines.append(f"approval {name}")
        elif isinstance(payload, RunTransitioned) and payload.to_state is RunStatus.PAUSED:
            lines.append("pause")
    return lines


def test_the_run_pauses_before_the_calls_after_a_write_to_approve(tmp_path: Path) -> None:
    async def go() -> tuple[list[str], list[str], str]:
        async with Loom.from_config(atelier(tmp_path, order="effects", approval="always")) as loom:
            run = await loom.run("demo", "Note", session_id=SESSION_E2E, user=DEV)
            paused = deroule(await loom.export_session(SESSION_E2E))
            assert await loom.approve(run.run_id, user=DEV, session_id=SESSION_E2E)
            await loom.drain()
            fin = await loom.result(run.run_id, session_id=SESSION_E2E)
            return paused, deroule(await loom.export_session(SESSION_E2E)), str(fin.status)

    paused, done, status = asyncio.run(go())

    # À la pause, seule la lecture d'avant est passée.
    assert paused == ["called lire#0", "completed lire#0 → version 1", "approval ecrire#1", "pause"]
    # Accordée, l'écriture part, puis la relecture qui l'attendait : elle lit la nouvelle version.
    assert done[len(paused) :] == [
        "called ecrire#1",
        "completed ecrire#1 → écrit : note.txt",
        "called lire#2",
        "completed lire#2 → version 2",
    ]
    assert status == RunStatus.COMPLETED


def test_one_decision_for_two_writes_then_everything_runs_in_order(tmp_path: Path) -> None:
    script: list[dict[str, object]] = [
        {
            "tool_calls": [
                {"name": "ecrire", "arguments": {"nom": "note.txt", "texte": "version 2"}},
                {"name": "lire", "arguments": {"nom": "note.txt"}},
                {"name": "ecrire", "arguments": {"nom": "note.txt", "texte": "version 3"}},
                {"name": "lire", "arguments": {"nom": "note.txt"}},
            ],
        },
        {"text": "C'est fait."},
    ]

    async def go() -> tuple[list[str], list[str]]:
        path = atelier(tmp_path, order="effects", approval="always", script=script)
        async with Loom.from_config(path) as loom:
            run = await loom.run("demo", "Note", session_id=SESSION_E2E, user=DEV)
            paused = deroule(await loom.export_session(SESSION_E2E))
            assert len(await loom.approve(run.run_id, user=DEV, session_id=SESSION_E2E)) == 2
            await loom.drain()
            return paused, deroule(await loom.export_session(SESSION_E2E))

    paused, done = asyncio.run(go())

    assert paused == ["approval ecrire#0", "approval ecrire#2", "pause"]
    assert done[len(paused) :] == [
        "called ecrire#0",
        "completed ecrire#0 → écrit : note.txt",
        "called lire#1",
        "completed lire#1 → version 2",
        "called ecrire#2",
        "completed ecrire#2 → écrit : note.txt",
        "called lire#3",
        "completed lire#3 → version 3",
    ]


def test_without_ordering_the_read_after_a_write_to_approve_runs_before_the_pause(
    tmp_path: Path,
) -> None:
    """Le comportement d'avant : la relecture passe avant l'écriture, sans course, chaque fois."""

    async def go() -> list[str]:
        async with Loom.from_config(atelier(tmp_path, order="free", approval="always")) as loom:
            await loom.run("demo", "Note", session_id=SESSION_E2E, user=DEV)
            return deroule(await loom.export_session(SESSION_E2E))

    assert asyncio.run(go()) == [
        "called lire#0",
        "called lire#2",
        "completed lire#0 → version 1",
        "completed lire#2 → version 1",
        "approval ecrire#1",
        "pause",
    ]


def test_ordering_changes_neither_the_consumption_nor_the_model_requests(tmp_path: Path) -> None:
    """Mêmes itérations, même consommation, mêmes requêtes au modèle (empreintes) : les
    résultats lui parviennent dans l'ordre des appels (``in_call_order``), quel que soit
    leur ordre d'arrivée — seul le texte de la relecture change."""

    async def go(order: str) -> tuple[RunResult, list[str]]:
        folder = tmp_path / order
        folder.mkdir()
        async with Loom.from_config(atelier(folder, order=order, approval=None)) as loom:
            run = await loom.run("demo", "Note", session_id=SESSION_E2E, user=DEV)
            events = await loom.export_session(SESSION_E2E)
        hashes = [e.payload.request_hash for e in events if isinstance(e.payload, ModelResponded)]
        return run, hashes

    ordered, ordered_hashes = asyncio.run(go("effects"))
    free, free_hashes = asyncio.run(go("free"))

    assert (ordered.iterations, ordered.usage, ordered.cost_usd) == (
        free.iterations,
        free.usage,
        free.cost_usd,
    )
    # Première requête identique ; la seconde porte la relecture, « version 2 » ou « version 1 ».
    assert ordered_hashes[0] == free_hashes[0]
    assert ordered_hashes[1] != free_hashes[1]


def test_the_live_display_follows_the_new_journal_order(tmp_path: Path) -> None:
    async def go() -> list[str]:
        async with Loom.from_config(atelier(tmp_path, order="effects", approval=None)) as loom:
            await loom.run("demo", "Note", session_id=SESSION_E2E, user=DEV)
            events = await loom.export_session(SESSION_E2E)
        progress = Progress()
        return [line for e in events if (line := progress.line(e)) is not None]

    assert asyncio.run(go()) == [
        "· lire(nom='note.txt')",
        "· lire : fait",
        "· ecrire(nom='note.txt', texte='version 2')",
        "· ecrire : fait",
        "· lire(nom='note.txt')",
        "· lire : fait",
    ]


@pytest.mark.parametrize("approval", [None, "always"])
def test_a_journal_written_without_ordering_replays_identically(
    tmp_path: Path, approval: str | None
) -> None:
    """Un journal d'avant (lecture passée avant l'écriture) se rejoue à l'identique avec l'ordre :
    les résultats viennent du journal, et la requête au modèle les range dans l'ordre des appels."""

    async def record() -> Path:
        path = atelier(tmp_path, order="free", approval=approval)
        async with Loom.from_config(path) as loom:
            run = await loom.run("demo", "Note", session_id=SESSION_E2E, user=DEV)
            if approval is not None:
                assert await loom.approve(run.run_id, user=DEV, session_id=SESSION_E2E)
                await loom.drain()
            events = await loom.export_session(SESSION_E2E)
        journal = tmp_path / "avant.jsonl"
        journal.write_text("".join(e.model_dump_json() + "\n" for e in events), encoding="utf-8")
        return journal

    journal = asyncio.run(record())
    config = yaml.safe_load((tmp_path / "loom.yaml").read_text(encoding="utf-8"))
    config["execution"]["tools"]["order"] = "effects"
    (tmp_path / "loom.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")

    [replayed] = asyncio.run(assert_replays(tmp_path / "loom.yaml", journal))

    assert replayed.identical


# --- D-11 : une politique after_tool qui fait échouer le run arrête la chaîne ---------


@policy(points=["after_tool"], decisions=["fail"])
def refuse_l_ecriture(subject: AfterTool) -> Decision:
    """Fait échouer le run dès qu'une écriture rend son résultat."""
    return Fail("écriture refusée") if subject.arguments.get("action") == "write" else CONTINUE


def failing() -> Policies:
    return Policies(
        [
            BoundPolicy(
                policy=refuse_l_ecriture, name="refuse_l_ecriture", points=refuse_l_ecriture.points
            )
        ]
    )


async def test_a_failing_after_tool_policy_stops_the_chain() -> None:
    """Le run va échouer : l'écriture suivante ne part pas, la lecture d'avant est passée."""
    disque = Disque(files={"note.txt": "v1"})
    executor = ToolExecutor([Fichiers(disque)])

    async with asyncio.timeout(5):
        events = [
            e
            async for e in executor.run_batch(
                awaiting(lire("r"), ecrire("w1", "v2"), ecrire("w2", "v3")), policies=failing()
            )
        ]

    assert trace(events) == ["called r", "completed r", "called w1", "completed w1"]
    assert disque.files["note.txt"] == "v2"


async def test_without_ordering_a_failing_policy_lets_the_whole_batch_run() -> None:
    disque = Disque(files={"note.txt": "v1"})
    executor = ToolExecutor([Fichiers(disque)], order="free")

    async with asyncio.timeout(5):
        events = [
            e
            async for e in executor.run_batch(
                awaiting(ecrire("w1", "v2", delai=0.0), ecrire("w2", "v3", delai=0.05)),
                policies=failing(),
            )
        ]

    assert sorted(results(events)) == ["w1", "w2"]


# --- D-2 : le réglage de l'agent l'emporte sur celui de la racine ---------------------


@pytest.mark.parametrize(
    ("root", "agent", "expected"),
    [
        ("effects", None, "effects"),
        ("free", None, "free"),
        ("effects", "free", "free"),
        ("free", "effects", "effects"),
    ],
)
def test_the_agent_setting_overrides_the_root_one(
    tmp_path: Path, root: str, agent: str | None, expected: str
) -> None:
    path = atelier(tmp_path, order=root, approval=None)
    if agent is not None:
        fichier = tmp_path / "agents" / "demo.yaml"
        spec = yaml.safe_load(fichier.read_text(encoding="utf-8"))
        spec["execution"] = {"tools": {"order": agent}}
        fichier.write_text(yaml.safe_dump(spec), encoding="utf-8")

    async def go() -> str:
        async with Loom.from_config(path) as loom:
            return loom.context("demo", TenantId("default")).tools.order

    assert asyncio.run(go()) == expected


def test_an_agent_cannot_set_the_other_tool_settings(tmp_path: Path) -> None:
    """Seul l'ordre se règle par agent : un délai y serait accepté sans s'appliquer."""
    path = atelier(tmp_path, order="effects", approval=None)
    fichier = tmp_path / "agents" / "demo.yaml"
    spec = yaml.safe_load(fichier.read_text(encoding="utf-8"))
    spec["execution"] = {"tools": {"timeout": 5}}
    fichier.write_text(yaml.safe_dump(spec), encoding="utf-8")

    with pytest.raises(ConfigError, match="timeout"):
        load_config(path)


# --- D-3 : la référence d'un sous-agent déclare ses effets ----------------------------


def test_a_sub_agent_reference_declares_its_effects(tmp_path: Path) -> None:
    path = atelier(tmp_path, order="effects", approval=None)
    fichier = tmp_path / "agents" / "demo.yaml"
    demo = yaml.safe_load(fichier.read_text(encoding="utf-8"))
    for name in ("compta", "secretaire"):
        child = {**demo, "name": name, "expose": {"rest": False, "mcp": False}}
        (tmp_path / "agents" / f"{name}.yaml").write_text(yaml.safe_dump(child), encoding="utf-8")
    demo["subagents"] = [
        {"agent": "compta", "description": "Calcule.", "side_effects": "none"},
        {"agent": "secretaire", "description": "Envoie."},
    ]
    fichier.write_text(yaml.safe_dump(demo), encoding="utf-8")

    async def go() -> dict[str, str]:
        async with Loom.from_config(path) as loom:
            tools = loom.context("demo", TenantId("default")).tools
            return {
                name: found.spec.side_effects
                for name in ("compta", "secretaire")
                if (found := tools.get(name)) is not None
            }

    assert asyncio.run(go()) == {"compta": "none", "secretaire": "irreversible"}


async def test_two_sub_agents_declared_without_effect_run_together() -> None:
    log: list[tuple[str, str]] = []

    class SansEffet(Delegue):
        @property
        def spec(self) -> ToolSpec:
            return super().spec.model_copy(update={"side_effects": "none"})

    executor = ToolExecutor([SansEffet("compta", "agent", log), SansEffet("paie", "agent", log)])

    await collect(
        executor,
        awaiting(
            PendingCall(call_id="a", name="compta", arguments={"message": "?"}),
            PendingCall(call_id="b", name="paie", arguments={"message": "?"}),
        ),
    )

    assert [step for step, _ in log] == ["début", "début", "fin", "fin"]


# --- D-6 : loom validate signale un outil Python qui ne déclare pas ses effets --------


@pytest.mark.parametrize(
    ("declared", "warned"),
    [
        ("", True),
        ('side_effects="none"', False),
        ('side_effects="reversible"', False),
    ],
)
def test_validate_warns_about_a_python_tool_without_declared_effects(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], declared: str, warned: bool
) -> None:
    path = atelier(tmp_path, order="effects", approval=None)
    source = (tmp_path / "outils_ordre.py").read_text(encoding="utf-8")
    (tmp_path / "outils_ordre.py").write_text(
        source.replace("@tool\nasync def lire", f"@tool({declared})\nasync def lire"),
        encoding="utf-8",
    )

    assert main(["--config", str(path), "validate"]) == 0

    out = capsys.readouterr().out
    assert ("outil lire : side_effects non déclaré" in out) is warned
    assert "outil ecrire : side_effects" not in out


def test_validate_takes_the_declaration_of_the_reference(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = atelier(tmp_path, order="free", approval=None)
    fichier = tmp_path / "agents" / "demo.yaml"
    spec = yaml.safe_load(fichier.read_text(encoding="utf-8"))
    spec["tools"] = [{"python": "lire", "side_effects": "none"}, {"python": "ecrire"}]
    fichier.write_text(yaml.safe_dump(spec), encoding="utf-8")

    assert main(["--config", str(path), "validate"]) == 0

    out = capsys.readouterr().out
    assert "side_effects non déclaré" not in out
    # L'écart au défaut se voit dans l'en-tête de l'agent.
    assert "appels d'un tour tous ensemble (order: free)" in out
