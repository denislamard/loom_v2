# SPDX-License-Identifier: Apache-2.0
"""Phase 1.3 : un run du moteur, interrompu pendant les outils, puis repris.

    uv run python examples/j1/moteur.py

Le modèle est simulé (``ScriptedModel``). Il demande deux outils en
parallèle : ``calculer``, rapide et déclaré à effet de bord, et
``taux_tva``, lent et sans effet de bord. Le process est « tué » pendant
l'outil lent, juste après l'écriture du résultat de l'outil rapide.

Un second moteur, avec une nouvelle instance du store, reprend le run depuis
le journal : l'outil rapide, déjà terminé, n'est pas relancé ; l'outil lent,
interrompu mais sans risque, est réexécuté. Le journal JSONL est écrit dans
``data/examples/j1/`` (ignoré par git).
"""

import asyncio
from collections.abc import Sequence
from pathlib import Path

from loom_ia.adapters.stores import JsonlEventStore
from loom_ia.core.events import (
    Event,
    EventDraft,
    ModelResponded,
    RunTransitioned,
    StepStarted,
    ToolCalled,
    ToolCompleted,
    UserMessage,
)
from loom_ia.core.model import DEFAULT_TENANT, Message, ModelSpec, Pricing
from loom_ia.engine import RunContext, ToolExecutor, begin_run, drive
from loom_ia.testing import ScriptedModel, tool_call_message
from loom_ia.tools import tool

ROOT = Path("data/examples/j1")
PROMPT = "Quel est le prix TTC de 3 articles à 40 € HT ?"
MODEL = ModelSpec(id="FAKE", sdk="fake", model="fake-1", pricing=Pricing(input=1.0, output=5.0))
executions = {"calculer": 0, "taux_tva": 0}


@tool(side_effects="irreversible")
def calculer(expr: str) -> str:
    """Évalue une expression arithmétique."""
    executions["calculer"] += 1
    return str(eval(expr, {"__builtins__": {}}))


@tool(timeout=60)
async def taux_tva(pays: str) -> dict[str, object]:
    """Taux de TVA normal d'un pays (code ISO)."""
    executions["taux_tva"] += 1
    # La première exécution est interrompue par le « plantage ».
    await asyncio.sleep(30 if executions["taux_tva"] == 1 else 0.05)
    return {"pays": pays, "taux": 0.20}


class WatchedStore(JsonlEventStore):
    """Store JSONL qui signale l'écriture du premier résultat d'outil."""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.first_result = asyncio.Event()

    async def append(
        self, drafts: Sequence[EventDraft], *, expected_seq: int | None
    ) -> list[Event]:
        events = await super().append(drafts, expected_seq=expected_seq)
        if any(isinstance(e.payload, ToolCompleted) for e in events):
            self.first_result.set()
        return events


def engine(store: JsonlEventStore, model: ScriptedModel) -> RunContext:
    return RunContext(
        agent="demo",
        store=store,
        model=model,
        model_spec=MODEL,
        system="Tu réponds aux questions de prix.",
        tools=ToolExecutor([calculer, taux_tva]),
    )


def describe(event: Event) -> str:
    match event.payload:
        case StepStarted(step_no=no, effect=effect):
            return f"étape {no} : {effect}"
        case UserMessage(message=message):
            return message.text
        case ModelResponded(stop_reason=reason, usage=usage):
            return f"{reason}, {usage.total_tokens} tokens"
        case RunTransitioned(from_state=before, to_state=after):
            return f"{before} → {after}"
        case ToolCalled(tool_name=name, resumed=resumed):
            return f"{name}{' (reprise)' if resumed else ''}"
        case ToolCompleted(tool_name=name, output=output):
            return f"{name} = {output.as_text or output.data}"
        case _:
            return ""


async def main() -> None:
    # Premier process : le modèle demande les deux outils, puis plantage.
    store = WatchedStore(ROOT)
    first = engine(
        store,
        ScriptedModel(
            tool_call_message(
                ("c1", "calculer", {"expr": "3 * 40"}),
                ("c2", "taux_tva", {"pays": "FR"}),
                text="Je calcule le total et je cherche le taux.",
            )
        ),
    )
    run = await begin_run(first, PROMPT)
    crashed = asyncio.create_task(drive(first, run.run_id))
    await store.first_result.wait()
    crashed.cancel()
    try:
        await crashed
    except asyncio.CancelledError:
        print("Plantage simulé pendant l'exécution de taux_tva.\n")
    interrupted = len(await store.read(DEFAULT_TENANT, run.session_id))

    # Second process : nouvelle instance du store, reprise depuis le journal.
    reopened = JsonlEventStore(ROOT)
    model = ScriptedModel(Message.assistant("120 € HT, soit 144 € TTC avec 20 % de TVA."))
    state = await drive(engine(reopened, model), run.run_id)

    events = await reopened.read(DEFAULT_TENANT, run.session_id)
    print(f"Journal : {reopened.path(DEFAULT_TENANT, run.session_id)}\n")
    for event in events:
        if event.seq == interrupted + 1:
            print("     --- reprise ---")
        print(f"{event.seq:>3}  {event.type:<18} {event.status:<6} {describe(event)}")

    print("\nRésultat")
    print(f"  statut      : {state.status}")
    print(f"  réponse     : {state.output.text if state.output else '—'}")
    print(f"  itérations  : {state.iterations}")
    print(f"  coût        : {state.cost_usd:.5f} $")
    print(f"  exécutions  : {executions}")
    results = [m for m in model.requests[0].messages if m.role == "tool"]
    print(f"  résultats envoyés au modèle après reprise : {len(results)}")


if __name__ == "__main__":
    asyncio.run(main())
