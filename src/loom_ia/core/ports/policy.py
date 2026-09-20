# SPDX-License-Identifier: Apache-2.0
"""Port des politiques (#1, #2).

Une politique déclare ses points d'accroche et les décisions qu'elle peut
rendre : la config vérifie au démarrage que ces décisions sont permises aux
points où l'agent la branche. Le décorateur ``@policy`` de
``loom_ia.policies`` fabrique une politique à partir d'une fonction.
"""

from typing import Protocol, runtime_checkable

from loom_ia.core.model.policy import (
    Decision,
    DecisionKind,
    HookPoint,
    PolicyContext,
    PolicySubject,
)


@runtime_checkable
class Policy(Protocol):
    """Politique branchable sur un ou plusieurs points d'accroche.

    ``isinstance`` vérifie seulement la présence des attributs : c'est ce qui
    sert à reconnaître une politique dans la config.
    """

    @property
    def name(self) -> str: ...

    @property
    def points(self) -> frozenset[HookPoint]:
        """Points où la politique sait s'appliquer."""
        ...

    @property
    def decisions(self) -> frozenset[DecisionKind]:
        """Décisions qu'elle peut rendre, en plus de ``continue``."""
        ...

    async def decide(self, subject: PolicySubject, context: PolicyContext) -> Decision:
        """Décision pour ce qui se passe au point ``subject.point``."""
        ...
