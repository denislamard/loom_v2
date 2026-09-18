# SPDX-License-Identifier: Apache-2.0
"""Projections pures du journal : état d'un run, historique d'une session."""

from loom_ia.core.projections.history import TERMINAL_MARKER, history
from loom_ia.core.projections.run_state import ProjectionError, apply, fold, fold_all

__all__ = ["TERMINAL_MARKER", "ProjectionError", "apply", "fold", "fold_all", "history"]
