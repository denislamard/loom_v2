# SPDX-License-Identifier: Apache-2.0
"""Rejeu d'un run depuis son journal (K6, #31).

À l'identique (6.2a) : la logique de loom retourne, le monde est servi par le
journal, et la première divergence est dite — quel appel, et quelle partie de
sa requête a changé.

En variante (6.2b) : un autre modèle ou une autre config ; ce que le journal
connaît est servi, le reste part pour de vrai — jamais un outil à effets de
bord —, et le rapport compare les deux runs.

Les évals (6.3a) vivent ici aussi : une suite de cas joués par variante, des
contrôles et un juge, le même monde qu'en variante pour les outils à effets de
bord — doublés ou refusés, jamais exécutés.

La non-régression (6.3b) rejoue à l'identique les runs d'un journal exporté
(``read_journal``) — une session, ou le run d'une éval — : la config
d'aujourd'hui doit les reproduire requête par requête.
"""

from loom_ia.replay.book import (
    Divergence,
    DivergenceKind,
    JournalTools,
    ReplayBook,
    ReplayError,
    ReplayModelClient,
)
from loom_ia.replay.evals import (
    BASE_VARIANT,
    IDENTICAL,
    CheckResult,
    EvalCase,
    EvalCriterion,
    EvalError,
    EvalFate,
    EvalJudge,
    EvalJudgeClient,
    EvalReport,
    EvalRun,
    EvalSuite,
    EvalTools,
    EvalVariant,
    Expect,
    Judgment,
    Outcome,
    ToolExpectation,
    ToolUse,
    VariantSummary,
    check,
    eval_fate_label,
    isolated,
    judged,
    load_suite,
    render_eval,
    unjudged,
)
from loom_ia.replay.runner import (
    Comparison,
    JournalReplay,
    JournalRun,
    ReplayMode,
    ReplayReport,
    RunSide,
    Verdict,
    journal_runs,
    read_journal,
    replay_run,
)
from loom_ia.replay.variant import (
    Double,
    ToolFate,
    VariantModelClient,
    VariantTools,
    doubled,
    fate_label,
    swap_models,
)

__all__ = [
    "BASE_VARIANT",
    "IDENTICAL",
    "CheckResult",
    "Comparison",
    "Divergence",
    "DivergenceKind",
    "Double",
    "EvalCase",
    "EvalCriterion",
    "EvalError",
    "EvalFate",
    "EvalJudge",
    "EvalJudgeClient",
    "EvalReport",
    "EvalRun",
    "EvalSuite",
    "EvalTools",
    "EvalVariant",
    "Expect",
    "JournalReplay",
    "JournalRun",
    "JournalTools",
    "Judgment",
    "Outcome",
    "ReplayBook",
    "ReplayError",
    "ReplayMode",
    "ReplayModelClient",
    "ReplayReport",
    "RunSide",
    "ToolExpectation",
    "ToolFate",
    "ToolUse",
    "VariantModelClient",
    "VariantSummary",
    "VariantTools",
    "Verdict",
    "check",
    "doubled",
    "eval_fate_label",
    "fate_label",
    "isolated",
    "journal_runs",
    "judged",
    "load_suite",
    "read_journal",
    "render_eval",
    "replay_run",
    "swap_models",
    "unjudged",
]
