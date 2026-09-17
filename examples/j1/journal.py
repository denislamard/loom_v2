# SPDX-License-Identifier: Apache-2.0
"""Phase 1.2 : écrire le journal d'un run fictif, puis reconstruire son état.

    uv run python examples/j1/journal.py

Le journal est écrit en JSONL dans ``data/examples/j1/`` (ignoré par git).
Chaque étape est écrite séparément, avec le contrôle ``expected_seq``, comme
le fera le moteur. Le journal est ensuite relu par une autre instance du
store, comme après un redémarrage.
"""

import asyncio
from pathlib import Path

from loom_ia.adapters.stores import JsonlEventStore
from loom_ia.core.model import DEFAULT_TENANT, Message, SessionId, ToolOutput, Usage, new_id
from loom_ia.core.projections import fold, history
from loom_ia.testing import RunJournal, tool_call_message

ROOT = Path("data/examples/j1")


async def write_run(store: JsonlEventStore, session: SessionId) -> RunJournal:
    journal = RunJournal(session_id=session)
    last = 0

    async def checkpoint() -> None:
        nonlocal last
        events = await store.append(journal.take(), expected_seq=last)
        last = events[-1].seq

    journal.start("Combien font 12 * 7 + 3 ?")
    await checkpoint()
    journal.model_turn(
        tool_call_message(("c1", "calculer", {"expr": "12*7+3"})),
        usage=Usage(input_tokens=180, output_tokens=24),
        cost_usd=0.00008,
    )
    await checkpoint()
    journal.tool_results({"c1": ToolOutput.text("87")})
    await checkpoint()
    journal.model_turn(
        Message.assistant("12 * 7 + 3 = 87"),
        usage=Usage(input_tokens=230, output_tokens=12),
        cost_usd=0.00009,
    )
    journal.complete()
    await checkpoint()
    return journal


async def main() -> None:
    session = SessionId(new_id())
    journal = await write_run(JsonlEventStore(ROOT), session)

    reopened = JsonlEventStore(ROOT)
    events = await reopened.read(DEFAULT_TENANT, session)
    print(f"Journal : {reopened.path(DEFAULT_TENANT, session)}\n")
    for event in events:
        facets = ", ".join(f"{k}={v}" for k, v in event.facets.items())
        print(f"{event.seq:>3}  {event.type:<18} {event.status:<6} {facets}")

    state = fold(events, journal.run_id)
    print("\nÉtat reconstruit")
    print(f"  statut      : {state.status}")
    print(f"  itérations  : {state.iterations}")
    print(f"  tokens      : {state.usage.total_tokens}")
    print(f"  coût        : {state.cost_usd:.5f} $")
    print(f"  réponse     : {state.output.text if state.output else '—'}")

    print("\nHistorique pour le modèle")
    for message in history(events):
        print(f"  {message.role:<9} {[block.type for block in message.blocks]}")


if __name__ == "__main__":
    asyncio.run(main())
