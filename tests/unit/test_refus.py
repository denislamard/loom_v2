# SPDX-License-Identifier: Apache-2.0
"""Un appel refusé à l'approbation : ce que le modèle en lit, et qu'il n'est pas redemandé.

Constaté au test D12 du 18/10 : « Appel à forge__forge refusé : test D12, pas
de VM » a été lu comme une panne. Haiku a relancé le même appel, mêmes
arguments ; Denis a dû refuser une seconde fois, puis Haiku a conclu que « le
service de forge a refusé ».
"""

from pydantic import JsonValue

from loom_ia.adapters.stores import InMemoryEventStore
from loom_ia.core.events import ApprovalRequested, ToolCompleted
from loom_ia.core.model import (
    Approved,
    Message,
    ModelSpec,
    PendingApproval,
    Rejected,
    RetryPolicy,
    RunStatus,
)
from loom_ia.engine import Policies, RunContext, ToolExecutor, begin_run, drive
from loom_ia.engine.executor import _refused  # pyright: ignore[reportPrivateUsage]
from loom_ia.testing import ScriptedModel, tool_call_message
from loom_ia.tools import ConfiguredTool, tool

SPEC = ModelSpec(id="FAKE", sdk="fake", model="fake-1", retry=RetryPolicy(initial_delay=0))
ARGUMENTS: dict[str, JsonValue] = {"client": "Dupont", "message": "Bonjour"}
DECIDED = (
    "C'est une décision, pas une panne : ne refais pas cet appel ; dis à l'utilisateur "
    "ce qui a été refusé et pourquoi."
)


@tool
def envoyer(client: str, message: str) -> str:
    """Envoie une relance."""
    return f"Relance envoyée à {client}"


SENSIBLE = ConfiguredTool(tool=envoyer, spec=envoyer.spec.model_copy(update={"approval": "always"}))


def test_the_refusal_says_who_decided_and_what_to_do() -> None:
    assert _refused("envoyer", "rejected", "pas maintenant", "denis") == (
        f"Appel à envoyer refusé à l'approbation par denis (motif : pas maintenant). {DECIDED}"
    )
    assert (
        _refused("envoyer", "rejected", "") == f"Appel à envoyer refusé à l'approbation. {DECIDED}"
    )
    assert _refused("envoyer", "expired", "sans réponse avant 2026-10-19 18:25.") == (
        "Appel à envoyer non exécuté : personne ne l'a approuvé dans le délai imparti "
        "(sans réponse avant 2026-10-19 18:25). Ne le refais pas de toi-même ; dis à "
        "l'utilisateur qu'il n'a pas été approuvé à temps."
    )
    # Le motif garde son sens sans son point final, qui doublerait celui de la phrase.
    assert "(motif : déjà payé)." in _refused("envoyer", "rejected", "déjà payé.", "denis")


async def test_the_same_call_refused_once_is_not_asked_again() -> None:
    asked: list[PendingApproval] = []

    async def approver(pending: PendingApproval) -> Rejected | Approved:
        asked.append(pending)
        if pending.arguments == ARGUMENTS:
            return Rejected(by="denis", reason="pas maintenant")
        return Approved(by="denis")

    autre: dict[str, JsonValue] = {**ARGUMENTS, "message": "Bonsoir"}
    model = ScriptedModel(
        tool_call_message(("c1", "envoyer", ARGUMENTS)),
        tool_call_message(("c2", "envoyer", ARGUMENTS)),
        tool_call_message(("c3", "envoyer", autre)),
        Message.assistant("La première relance a été refusée, la seconde est partie."),
    )
    store = InMemoryEventStore()
    ctx = RunContext(
        agent="demo",
        store=store,
        model=model,
        policies=Policies(()),
        tools=ToolExecutor([SENSIBLE]),
        system="Tu relances.",
        model_spec=SPEC,
        max_iterations=5,
        approver=approver,
    )
    state = await drive(ctx, (await begin_run(ctx, "Relance Dupont.")).run_id)

    assert state.status is RunStatus.COMPLETED
    # L'approbateur a vu c1 et c3 ; c2, identique à c1, ne lui a pas été remis.
    assert [p.call_id for p in asked] == ["c1", "c3"]
    events = [e.payload for e in await store.read(state.context.tenant_id, state.session_id)]
    assert [e.call_id for e in events if isinstance(e, ApprovalRequested)] == ["c1", "c3"]
    results = {e.call_id: e.output for e in events if isinstance(e, ToolCompleted)}
    assert results["c1"].as_text == (
        f"Appel à envoyer refusé à l'approbation par denis (motif : pas maintenant). {DECIDED}"
    )
    assert results["c2"].is_error and results["c2"].as_text == (
        "Appel à envoyer non exécuté : le même appel, avec les mêmes arguments, a déjà été "
        "refusé à l'approbation par denis (motif : pas maintenant) dans ce tour (appel c1). "
        f"Il n'est pas redemandé. {DECIDED}"
    )
    assert results["c3"].as_text == "Relance envoyée à Dupont"
