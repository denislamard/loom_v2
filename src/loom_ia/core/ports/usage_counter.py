# SPDX-License-Identifier: Apache-2.0
"""Port du compteur de consommation par client et par période (J4, L3, §15).

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
"""

from typing import Protocol

from loom_ia.core.model.budget import Spent
from loom_ia.core.model.ids import RunId, TenantId


class UsageCounter(Protocol):
    """Ce qu'un client a consommé sur une période."""

    async def consumed(self, tenant_id: TenantId, period: str) -> Spent:
        """Somme de ce qui a été enregistré pour ce client sur cette période."""
        ...

    async def record(self, tenant_id: TenantId, period: str, run_id: RunId, spent: Spent) -> None:
        """Pose ce qu'un run a coûté ; réenregistrer le même run ne double pas.

        ``run_id`` est celui du run **racine** : la consommation d'un
        sous-agent est déjà dans la somme de son parent.
        """
        ...

    async def aclose(self) -> None: ...
