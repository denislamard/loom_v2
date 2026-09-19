# SPDX-License-Identifier: Apache-2.0
"""Projections pures du journal : état d'un run, arbre des runs, historique d'une session."""

from loom_ia.core.projections.history import TERMINAL_MARKER, history
from loom_ia.core.projections.run_state import ProjectionError, apply, fold, fold_all
from loom_ia.core.projections.tree import RunTree

__all__ = [
    "TERMINAL_MARKER",
    "ProjectionError",
    "RunTree",
    "apply",
    "fold",
    "fold_all",
    "history",
]
