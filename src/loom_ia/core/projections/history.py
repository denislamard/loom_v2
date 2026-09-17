# SPDX-License-Identifier: Apache-2.0
"""Historique d'une session envoyé au modèle (#7, #22).

Seuls les runs racine terminés avec succès y figurent : un run échoué peut
contenir un appel d'outil sans résultat, que les API refusent. Les sous-runs
ont leur propre journal et n'en font pas partie. Le raisonnement est retiré.

Les snapshots, la compaction (J4) et le marqueur de sortie terminale (J2)
s'ajouteront ici.
"""

from collections.abc import Iterable

from loom_ia.core.events import Event
from loom_ia.core.model import Message, RunStatus
from loom_ia.core.projections.run_state import fold_all


def history(events: Iterable[Event]) -> list[Message]:
    """Messages des runs racine terminés, dans l'ordre du journal."""
    messages: list[Message] = []
    for state in fold_all(events).values():
        if state.parent_run_id is not None or state.status is not RunStatus.COMPLETED:
            continue
        for message in state.messages:
            kept = message.without_reasoning()
            if kept is not None:
                messages.append(kept)
    return messages
