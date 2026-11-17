# SPDX-License-Identifier: Apache-2.0
"""Projections pures du journal : état d'un run, arbre des runs, historique, ledger, state.

Le journal fait foi (point 22) ; tout le reste en est déduit ici, sans
entrée-sortie :

- ``run_state`` : l'état d'un run (``RunState``) rejoué événement par
  événement (``apply``, ``fold``) — c'est ce qui permet la reprise après un
  plantage ;
- ``history`` : l'historique de la session envoyé au modèle, résumés de
  compaction compris ;
- ``state`` : le state de session ;
- ``ledger`` : la consommation, appel de modèle par appel ;
- ``tree`` : l'arbre d'un run et de ses sous-runs, et les résumés faits à
  son ouverture.
"""

from loom_ia.core.projections.history import (
    SUMMARY_MARKER,
    TERMINAL_MARKER,
    history,
    last_marker,
    last_summary,
    turns,
)
from loom_ia.core.projections.ledger import LedgerEntry, ledger, spent
from loom_ia.core.projections.run_state import (
    ProjectionError,
    apply,
    asking_in_session,
    awaiting_in_session,
    fold,
    fold_all,
)
from loom_ia.core.projections.state import session_state, state_of_run
from loom_ia.core.projections.tree import OpeningSummaries, RunTree

__all__ = [
    "SUMMARY_MARKER",
    "TERMINAL_MARKER",
    "LedgerEntry",
    "OpeningSummaries",
    "ProjectionError",
    "RunTree",
    "apply",
    "asking_in_session",
    "awaiting_in_session",
    "fold",
    "fold_all",
    "history",
    "last_marker",
    "last_summary",
    "ledger",
    "session_state",
    "spent",
    "state_of_run",
    "turns",
]
