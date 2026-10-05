# SPDX-License-Identifier: Apache-2.0
"""Rejeu d'un run depuis son journal (K6, #31).

À l'identique (6.2a) : la logique de loom retourne, le monde est servi par le
journal, et la première divergence est dite — quel appel, et quelle partie de
sa requête a changé.

En variante (6.2b) : un autre modèle ou une autre config ; ce que le journal
connaît est servi, le reste part pour de vrai — jamais un outil à effets de
bord —, et le rapport compare les deux runs.
"""

from loom_ia.replay.book import (
    Divergence,
    DivergenceKind,
    JournalTools,
    ReplayBook,
    ReplayError,
    ReplayModelClient,
)
from loom_ia.replay.runner import (
    Comparison,
    ReplayMode,
    ReplayReport,
    RunSide,
    Verdict,
    replay_run,
)
from loom_ia.replay.variant import (
    Double,
    ToolFate,
    VariantModelClient,
    VariantTools,
    fate_label,
    swap_models,
)

__all__ = [
    "Comparison",
    "Divergence",
    "DivergenceKind",
    "Double",
    "JournalTools",
    "ReplayBook",
    "ReplayError",
    "ReplayMode",
    "ReplayModelClient",
    "ReplayReport",
    "RunSide",
    "ToolFate",
    "VariantModelClient",
    "VariantTools",
    "Verdict",
    "fate_label",
    "replay_run",
    "swap_models",
]
