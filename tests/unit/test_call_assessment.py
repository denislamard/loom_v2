# SPDX-License-Identifier: Apache-2.0
"""Évaluation de chaque appel par son outil (``assess_call``) : ce que le moteur en fait.

Un outil qui évalue ses appels remplace, pour chacun, sa déclaration fixe :
approbation, reprise d'un appel interrompu, rejeu et évaluations. Un outil qui
ne le fait pas garde sa déclaration ; une enveloppe de config transmet
l'évaluation, et ce qu'elle fixe l'emporte.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

import pytest
from pydantic import JsonValue

from loom_ia.core.events import ApprovalRequested, ToolCompleted
from loom_ia.core.model import (
    Approval,
    ApprovalOutcome,
    CallerContext,
    PendingApproval,
    PendingCall,
    RunId,
    RunState,
    RunStatus,
    TenantId,
    ToolOutput,
    ToolSpec,
)
from loom_ia.core.ports import CallAssessment, ToolContext, assessed, strictest_side_effects
from loom_ia.core.projections import fold
from loom_ia.engine import UNKNOWN_STATE, ToolExecutor
from loom_ia.engine.executor import ToolEvent
from loom_ia.replay.book import ReplayBook
from loom_ia.replay.evals import EvalTools
from loom_ia.replay.variant import VariantTools
from loom_ia.testing import RunJournal
from loom_ia.tools import ConfiguredTool, configure, tool
from loom_ia.tools_builtin import BuiltinContext
from loom_ia.tools_builtin.local_files import build
from loom_ia.tools_builtin.local_files_params import LocalFilesParams

# L'outil d'essai : ``x`` pair se lit, ``x`` impair supprime et demande un humain.
DELETE = CallAssessment(
    approval="always", side_effects="irreversible", reason="suppression : à approuver"
)
READ = CallAssessment(side_effects="none")
# Tous les droits, sans aucune approbation.
ALL_NEVER: Final = {
    "rights": ["read", "write", "overwrite", "delete"],
    "approval": {"overwrite": "never", "delete": "never"},
}


def spec(name: str = "fichiers", **options: object) -> ToolSpec:
    schema: dict[str, JsonValue] = {
        "type": "object",
        "properties": {"x": {"type": "integer"}},
        "additionalProperties": False,
    }
    # La déclaration fixe dit le contraire de l'évaluation : on voit laquelle compte.
    return ToolSpec.model_validate(
        {"name": name, "description": name, "kind": "python", "input_schema": schema, **options}
    )


@dataclass
class AssessingTool:
    spec: ToolSpec = field(default_factory=lambda: spec(side_effects="none", approval="never"))
    calls: list[dict[str, JsonValue]] = field(default_factory=list[dict[str, JsonValue]])
    broken: bool = False

    def assess_call(self, arguments: Mapping[str, JsonValue]) -> CallAssessment | None:
        if self.broken:
            raise RuntimeError("évaluation en panne")
        x = arguments.get("x")
        return DELETE if isinstance(x, int) and x % 2 else READ

    async def invoke(self, arguments: dict[str, JsonValue], context: ToolContext) -> ToolOutput:
        self.calls.append(arguments)
        return ToolOutput.text("ok")


def awaiting(*calls: PendingCall) -> RunState:
    journal = RunJournal(agent="demo", tenant_id=TenantId("default"))
    journal.start("?", context=CallerContext(tenant_id=TenantId("default"), user_id="u1"))
    events = [draft.to_event(seq) for seq, draft in enumerate(journal.take(), start=1)]
    return fold(events, journal.run_id).model_copy(
        update={"status": RunStatus.AWAITING_TOOLS, "pending_calls": calls}
    )


def call(call_id: str, x: int, *, started: bool = False, name: str = "fichiers") -> PendingCall:
    return PendingCall(call_id=call_id, name=name, arguments={"x": x}, started=started)


async def collect(executor: ToolExecutor, state: RunState) -> list[ToolEvent]:
    return [event async for event in executor.run_batch(state)]


def completed(events: list[ToolEvent]) -> dict[str, ToolOutput]:
    return {e.call_id: e.output for e in events if isinstance(e, ToolCompleted)}


def requested(events: list[ToolEvent]) -> dict[str, str]:
    return {e.call_id: e.reason for e in events if isinstance(e, ApprovalRequested)}


# --- Le protocole -------------------------------------------------------------------


def test_a_tool_without_assess_call_keeps_its_declaration() -> None:
    @tool(side_effects="reversible")
    def ecrire(x: int) -> str:
        """Écrit."""
        return str(x)

    assert assessed(ecrire, {"x": 1}) is None
    assert strictest_side_effects(ecrire, {"x": 1}) == "reversible"


def test_a_failing_assessment_is_the_most_careful() -> None:
    """Un défaut de l'outil ne doit pas laisser passer un appel qu'il aurait retenu."""
    found = assessed(AssessingTool(broken=True), {"x": 2})
    assert found is not None
    assert (found.approval, found.side_effects) == ("always", "irreversible")
    assert found.reason is not None and "évaluation en panne" in found.reason


def test_the_config_envelope_forwards_the_assessment() -> None:
    """Régler le délai ne fait pas perdre l'approbation par appel."""
    wrapped = configure(AssessingTool(), timeout=5)
    assert isinstance(wrapped, ConfiguredTool)
    assert assessed(wrapped, {"x": 1}) == DELETE
    assert assessed(wrapped, {"x": 2}) == READ


def test_what_the_config_fixes_wins_field_by_field() -> None:
    lifted = configure(AssessingTool(), approval="never")
    assert assessed(lifted, {"x": 1}) == CallAssessment(side_effects="irreversible")
    forced = configure(AssessingTool(), approval="always", side_effects="reversible")
    # Fixée par la config, l'approbation dit d'où elle vient (test D7 du 18/10).
    assert assessed(forced, {"x": 2}) == CallAssessment(
        approval="always",
        side_effects="reversible",
        reason="outil déclaré à approbation obligatoire",
    )
    # ``policy`` laisse décider les politiques : pas d'approbation d'office.
    assert assessed(configure(AssessingTool(), approval="policy"), {"x": 1}) == CallAssessment(
        side_effects="irreversible"
    )
    sure = configure(AssessingTool(), idempotent=True)
    assert assessed(sure, {"x": 1}) == CallAssessment(
        approval="always", side_effects="irreversible", idempotent=True, reason=DELETE.reason
    )


def test_an_envelope_around_a_plain_tool_assesses_nothing() -> None:
    @tool
    def lire(x: int) -> str:
        """Lit."""
        return str(x)

    assert assessed(configure(lire, timeout=5), {"x": 1}) is None


# --- Approbation ----------------------------------------------------------------------


async def test_only_the_calls_that_ask_wait_for_a_human() -> None:
    fichiers = AssessingTool()
    events = await collect(ToolExecutor([fichiers]), awaiting(call("c1", 2), call("c2", 1)))
    assert requested(events) == {"c2": "suppression : à approuver (effets : irreversible)"}
    assert not completed(events)["c1"].is_error
    assert fichiers.calls == [{"x": 2}]


async def test_the_declaration_does_not_count_when_the_tool_assesses() -> None:
    """Déclaré ``approval: always``, l'outil ne fait attendre que les appels qu'il désigne."""
    fichiers = AssessingTool(spec=spec(approval="always", side_effects="irreversible"))
    events = await collect(ToolExecutor([fichiers]), awaiting(call("c1", 2)))
    assert requested(events) == {}
    assert fichiers.calls == [{"x": 2}]


@pytest.mark.parametrize(
    ("imposed", "waiting"),
    [("never", set[str]()), ("always", {"c1", "c2"})],
)
async def test_the_tenant_keeps_the_last_word(imposed: Approval, waiting: set[str]) -> None:
    executor = ToolExecutor([AssessingTool()], imposed={"fichiers": imposed})
    events = await collect(executor, awaiting(call("c1", 2), call("c2", 1)))
    assert set(requested(events)) == waiting


# --- Reprise d'un appel interrompu --------------------------------------------------


async def test_an_interrupted_call_is_judged_by_its_own_assessment() -> None:
    """Une lecture interrompue repart ; une suppression interrompue est d'état inconnu."""
    fichiers = AssessingTool(spec=spec(side_effects="irreversible"))
    events = await collect(
        ToolExecutor([fichiers]), awaiting(call("c1", 2, started=True), call("c2", 1, started=True))
    )
    assert not completed(events)["c1"].is_error
    assert completed(events)["c2"] == ToolOutput.error(UNKNOWN_STATE)
    assert fichiers.calls == [{"x": 2}]


async def test_an_approved_call_that_was_launched_is_judged_by_its_launched_arguments() -> None:
    """Approuvé, lancé avec des arguments remplacés, interrompu : ceux-là décident."""
    fichiers = AssessingTool()
    interrupted = call("c1", 2, started=True).model_copy(update={"replaced_arguments": {"x": 1}})
    approval = PendingApproval(
        call_id="c1",
        tool_name="fichiers",
        arguments={"x": 1},
        outcome=ApprovalOutcome(verdict="granted", by="denis"),
        launched=True,
    )
    state = awaiting(interrupted).model_copy(update={"approvals": (approval,)})
    events = await collect(ToolExecutor([fichiers]), state)
    assert completed(events)["c1"] == ToolOutput.error(UNKNOWN_STATE)
    assert fichiers.calls == []


async def test_an_interrupted_call_uses_the_arguments_it_was_launched_with() -> None:
    """Remplacés par une politique, ce sont eux qui décident si l'appel peut repartir."""
    fichiers = AssessingTool()
    interrupted = call("c1", 2, started=True).model_copy(update={"replaced_arguments": {"x": 1}})
    events = await collect(ToolExecutor([fichiers]), awaiting(interrupted))
    assert completed(events)["c1"] == ToolOutput.error(UNKNOWN_STATE)
    assert fichiers.calls == []


# --- Rejeu et évaluations -------------------------------------------------------------
# L'appel y est jugé avant que ses références ne soient résolues et que les politiques
# ne remplacent ses arguments : l'évaluation ne peut que durcir la déclaration.


@pytest.mark.parametrize("replay", ["variant", "eval"])
def test_in_replay_the_assessment_only_tightens(replay: str) -> None:
    tools = VariantTools(ReplayBook()) if replay == "variant" else EvalTools()
    plain = AssessingTool(spec=spec(side_effects="none"))
    assert tools.serves(plain, call("c1", 2), RunId("r")) is False
    # Déclaré sans effet, mais l'appel supprime : refusé.
    assert tools.serves(plain, call("c2", 1), RunId("r")) is True
    # Déclaré irréversible : refusé, même un appel que l'outil dit sans effet.
    worst = AssessingTool(spec=spec(side_effects="irreversible"))
    assert tools.serves(worst, call("c3", 2), RunId("r")) is True
    assert tools.fates == {("r", "c1"): "run", ("r", "c2"): "refused", ("r", "c3"): "refused"}


def test_in_an_eval_a_local_files_call_never_runs(tmp_path: Path) -> None:
    """Même un chemin qui n'est qu'une référence, en objet ou en chaîne, résolue plus tard."""
    (tmp_path / "work").mkdir()
    params = LocalFilesParams.model_validate(
        {"folders": [{"name": "work", "path": "work", **ALL_NEVER}]}
    )
    files = build(params, BuiltinContext(base_dir=tmp_path))
    tools = EvalTools()
    paths: list[JsonValue] = ["work/a.txt", {"$ref": "result:1"}, '{"$ref": "result:1"}']
    for number, path in enumerate(paths, start=1):
        delete = PendingCall(
            call_id=f"c{number}", name="local_files", arguments={"action": "delete", "path": path}
        )
        assert tools.serves(files, delete, RunId("r")) is True
