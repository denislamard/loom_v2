# SPDX-License-Identifier: Apache-2.0
"""Port du compteur de consommation par client et par période (fonctions J4 et L3, conception §15).

Le journal dit la vérité, mais il la dit lentement : la dépense d'un client
sur une journée est éparpillée dans toutes ses sessions, et un budget se lit
avant **chaque** lancement de run. Ce port est le cache qui rend cette
lecture immédiate ; il est toujours reconstructible depuis le journal, et
c'est à lui de ne jamais compter deux fois.

D'où la forme de ``record`` : il **pose** ce qu'un run a coûté, il ne l'ajoute
pas. Un compteur en mémoire se réchauffe en relisant le journal, et le
réchauffage peut chevaucher la vie courante — un run commencé avant, fini
après, serait compté deux fois par un ``+=``. Avec une valeur posée par run,
réenregistrer le même run ne change rien, et l'ordre des deux sources n'a
plus d'importance.

La clé de période est une chaîne triable (``day:2026-09-23``,
``month:2026-09``), calculée par l'appelant : le magasin ne connaît ni les
calendriers ni les fuseaux, il range et il somme.

Implémentation fournie : ``adapters/usage/memory.py``, réchauffée depuis le
journal. Les budgets par client et par période se déclarent sous
``budgets.tenant`` (``max_cost_per_day``…).
"""

from typing import Protocol

from loom_ia.core.model.budget import Spent
from loom_ia.core.model.ids import RunId, TenantId


class UsageCounter(Protocol):
    """Compteur de ce qu'un client a consommé sur une période."""

    async def consumed(self, tenant_id: TenantId, period: str) -> Spent:
        """Somme de ce qui a été enregistré pour un client sur une période.

        Args:
            tenant_id: Le client.
            period: Clé de période (``day:2026-09-23``, ``month:2026-09``).

        Returns:
            Coût et tokens cumulés ; zéro pour une période sans run.
        """
        ...

    async def record(self, tenant_id: TenantId, period: str, run_id: RunId, spent: Spent) -> None:
        """Pose ce qu'un run a coûté ; réenregistrer le même run ne double pas.

        Args:
            tenant_id: Le client.
            period: Clé de période.
            run_id: Le run **racine** : la consommation d'un sous-agent est
                déjà dans la somme de son parent.
            spent: Ce que le run a coûté en tout, qui remplace une valeur
                déjà posée pour ce run.
        """
        ...

    async def aclose(self) -> None:
        """Libère les ressources du compteur."""
        ...
