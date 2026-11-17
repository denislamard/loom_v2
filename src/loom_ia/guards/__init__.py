# SPDX-License-Identifier: Apache-2.0
"""Guards : contrôles des sorties, branchés comme des politiques (fonctions E1 à E6, points 20 et 21).

- ``contract`` : ``loom.contract``, contrats de sortie (schéma, motifs,
  longueurs), réponse finale, rôles et outils ;
- ``judge`` : ``loom.judge.<nom>``, juges LLM à critères notés ;
- ``fidelity`` : ``loom.fidelity``, repères d'un résumé de compaction.

Le montage les place en tête des politiques de l'agent : contrats, puis
juges, puis budget.
"""

from loom_ia.guards.contract import (
    CONTRACT_POLICY,
    GUARD,
    Checked,
    ContractGuard,
    check,
    diagnostic,
    normalize,
)
from loom_ia.guards.fidelity import (
    FIDELITY_GUARD,
    FIDELITY_POLICY,
    FidelityGuard,
    markers,
    missing,
)
from loom_ia.guards.judge import (
    JUDGE_GUARD,
    JUDGE_POLICY_PREFIX,
    JUDGE_SYSTEM,
    VERDICT_TOOL,
    Condition,
    JudgeDefinition,
    JudgeGuard,
    correlated,
    judge_policy_name,
    verdict_scores,
    verdict_tool,
)

__all__ = [
    "CONTRACT_POLICY",
    "FIDELITY_GUARD",
    "FIDELITY_POLICY",
    "GUARD",
    "JUDGE_GUARD",
    "JUDGE_POLICY_PREFIX",
    "JUDGE_SYSTEM",
    "VERDICT_TOOL",
    "Checked",
    "Condition",
    "ContractGuard",
    "FidelityGuard",
    "JudgeDefinition",
    "JudgeGuard",
    "check",
    "correlated",
    "diagnostic",
    "judge_policy_name",
    "markers",
    "missing",
    "normalize",
    "verdict_scores",
    "verdict_tool",
]
