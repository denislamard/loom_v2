# SPDX-License-Identifier: Apache-2.0
"""Limitation de débit : quota d'un client, débit d'une clé d'API (L3, #39).

Fenêtre **glissante** de soixante secondes, et non calendaire comme les
budgets : un plafond de débit qui laisse passer le double à cheval sur la
minute n'en est pas un. Le prix est modeste — on garde au plus ``limit``
horodatages par clé de comptage, et on jette ce qui est sorti de la fenêtre.

Le même compteur sert deux choses qu'il ne faut pas confondre. Le quota d'un
**client** (``quotas.runs_per_minute``) est une règle métier : il est vérifié
à la façade, donc il vaut par les trois accès et par la ligne de commande. Le
débit d'une **clé d'API** (``rate_limit.per_minute``) protège le serveur : il
est vérifié par l'accès HTTP, qui répond 429 avec un ``Retry-After``.
"""

import time
from collections import deque
from typing import Final

from loom_ia.core.model import WINDOW, TenantId

# Ce qu'on compte : un client, ou une clé d'API.
type Counted = str


class QuotaExceeded(RuntimeError):
    """Le débit accordé est dépassé : la demande n'est pas servie.

    ``retry_after`` porte les secondes au bout desquelles la plus ancienne
    demande de la fenêtre en sortira, donc l'instant où la suivante passera.
    """

    def __init__(self, what: str, limit: int, retry_after: float) -> None:
        super().__init__(f"{what} : {limit} par minute dépassé, réessayer dans {retry_after:.1f} s")
        self.limit = limit
        self.retry_after = retry_after


class RateWindow:
    """Fenêtre glissante partagée, par clé de comptage."""

    def __init__(self, window: float = WINDOW) -> None:
        self.window = window
        self._seen: dict[Counted, deque[float]] = {}

    def take(self, key: Counted, limit: int, now: float | None = None) -> float | None:
        """Compte une demande et rend ``None`` ; sinon l'attente, sans rien compter."""
        moment = time.monotonic() if now is None else now
        seen = self._seen.setdefault(key, deque())
        horizon = moment - self.window
        while seen and seen[0] <= horizon:
            seen.popleft()
        if len(seen) >= limit:
            return max(0.0, seen[0] + self.window - moment)
        seen.append(moment)
        return None

    def forget(self, key: Counted) -> None:
        self._seen.pop(key, None)

    def __repr__(self) -> str:
        return f"RateWindow({self.window:g} s, {len(self._seen)} compteur(s))"


class Quota:
    """Quota d'un client : runs lancés par minute, quel que soit l'accès."""

    PREFIX: Final = "tenant:"

    def __init__(self, window: RateWindow | None = None) -> None:
        self._window = window if window is not None else RateWindow()

    def check(self, tenant_id: TenantId, limit: int | None) -> None:
        """Lève ``QuotaExceeded`` si le client a déjà lancé son compte de la minute."""
        if limit is None:
            return
        waiting = self._window.take(f"{self.PREFIX}{tenant_id}", limit)
        if waiting is not None:
            raise QuotaExceeded(f"Client {tenant_id!r}", limit, waiting)
