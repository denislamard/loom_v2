# SPDX-License-Identifier: Apache-2.0
"""Magasin d'idempotence en mémoire : tests et déploiement mono-process (#18, #49).

Il voit toutes les clés d'un process — contrairement au magasin ``journal``,
qui ne voit que son run — mais seulement de ce process : deux workers ne
partagent rien, et rien ne survit à un redémarrage. Une clé métier, qui doit
valoir pour tout le monde et durer, lui est donc refusée au chargement ; c'est
ce que le magasin ``sqlite`` apporte en plus.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from loom_ia.core.model import (
    DEFAULT_RETENTION,
    IdempotencyRecord,
    SessionId,
    TenantId,
    recordable,
)
from loom_ia.core.ports import KeyScope


@dataclass(frozen=True, slots=True)
class _Held:
    """Un enregistrement et de qui il est."""

    record: IdempotencyRecord
    scope: KeyScope


class InMemoryIdempotency:
    """Magasin non durable, limité au process.

    ``reserve`` ne contient aucun ``await`` : le contrôle et la prise de la
    clé sont donc atomiques pour la boucle asyncio, ce que le port exige.

    Une réservation périmée n'est **jamais** effacée d'elle-même : c'est la
    trace d'un effet d'état inconnu, et l'effacer la ferait passer pour un
    appel jamais lancé (#18). Seuls les résultats hors rétention s'oublient.
    """

    def __init__(self, *, retention: float = DEFAULT_RETENTION) -> None:
        self.retention = retention
        self._held: dict[str, _Held] = {}

    def __repr__(self) -> str:
        return f"InMemoryIdempotency({len(self._held)} clés)"

    async def get(self, key: str) -> IdempotencyRecord | None:
        held = self._held.get(key)
        if held is None:
            return None
        record = held.record
        # Un résultat hors de sa rétention n'a plus cours ; une réservation
        # périmée, si — c'est la trace d'un effet d'état inconnu.
        if record.status == "completed" and not record.alive(datetime.now(UTC)):
            return None
        return record

    async def reserve(self, key: str, ttl: float, scope: KeyScope) -> bool:
        now = datetime.now(UTC)
        held = self._held.get(key)
        # C'est la date qui protège : une réservation tenue ou un résultat
        # encore mémorisé gardent la clé, une ligne périmée la rend.
        if held is not None and held.record.alive(now):
            return False
        self._held[key] = _Held(
            record=IdempotencyRecord(
                key=key, status="in_progress", expires_at=now + timedelta(seconds=ttl)
            ),
            scope=scope,
        )
        return True

    async def complete(self, key: str, result: object, ttl: float | None = None) -> None:
        now = datetime.now(UTC)
        self._forget(now)
        held = self._held.get(key)
        if held is None:
            raise KeyError(f"Clé {key!r} non réservée : rien à enregistrer")
        self._held[key] = _Held(
            record=IdempotencyRecord(
                key=key,
                status="completed",
                result=recordable(result),
                expires_at=now + timedelta(seconds=self.retention if ttl is None else ttl),
            ),
            scope=held.scope,
        )

    async def release(self, key: str) -> None:
        held = self._held.get(key)
        if held is not None and held.record.status == "in_progress":
            del self._held[key]

    async def forget(self, tenant_id: TenantId, session_id: SessionId | None = None) -> int:
        """Oublie les clés d'un client, ou de l'une de ses sessions (RGPD)."""
        partantes = [
            key
            for key, held in self._held.items()
            if held.scope.tenant_id == tenant_id
            and (session_id is None or held.scope.session_id == session_id)
        ]
        for key in partantes:
            del self._held[key]
        return len(partantes)

    def _forget(self, now: datetime) -> None:
        """Oublie les résultats hors rétention ; les réservations restent."""
        stale = [
            key
            for key, held in self._held.items()
            if held.record.status == "completed" and not held.record.alive(now)
        ]
        for key in stale:
            del self._held[key]
