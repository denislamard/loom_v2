# SPDX-License-Identifier: Apache-2.0
"""Port de la file de tâches (#27, H5).

La durabilité ne vient pas de la file, mais du journal : au démarrage, les
runs restés dans un état actionnable sont remis en file, et une livraison en
double est sans risque puisque l'état se reconstruit depuis le journal. Une
file non durable suffit donc en mode librairie.

Un seul type de tâche est traité au jalon J4.1b, la compaction (#23) ; les
autres sont déclarés ici et refusés tant que leur phase n'est pas là.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal, Protocol

from pydantic import JsonValue

from loom_ia.core.model.ids import RunId, SessionId, TenantId

# ``compaction`` : résumé d'une session (4.1b). ``run`` et ``resume`` : runs en
# arrière-plan et reprise après approbation (4.2, 4.3). ``expire_approval`` :
# tâche différée qui fait expirer une demande d'approbation (4.3).
type JobKind = Literal["compaction", "run", "resume", "expire_approval"]
type JobState = Literal["pending", "running", "done", "failed", "cancelled", "unknown"]


@dataclass(frozen=True, slots=True, kw_only=True)
class Job:
    """Tâche à faire hors du run qui l'a demandée."""

    kind: JobKind
    tenant_id: TenantId
    session_id: SessionId
    # Run concerné, quand la tâche en vise un.
    run_id: RunId | None = None
    params: Mapping[str, JsonValue] = field(default_factory=dict[str, JsonValue])


class TaskQueue(Protocol):
    async def submit(self, job: Job, *, key: str | None = None, delay: float | None = None) -> str:
        """Met une tâche en file et rend son identifiant.

        ``key`` dédoublonne : une tâche de même clé déjà en attente ou en
        cours rend son identifiant, sans en créer une seconde. ``delay``
        diffère son départ, en secondes.
        """
        ...

    async def state(self, job_id: str) -> JobState:
        """État d'une tâche ; ``unknown`` si la file ne la connaît plus."""
        ...

    async def cancel(self, job_id: str) -> bool:
        """Annule une tâche ; faux si elle est déjà terminée ou inconnue."""
        ...

    async def aclose(self) -> None: ...
