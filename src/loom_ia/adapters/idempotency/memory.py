# SPDX-License-Identifier: Apache-2.0
"""Magasin d'idempotence en mémoire : tests et déploiement mono-process (points 18 et 49).

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
    """Un enregistrement, de qui il est, et le détenteur qui l'a pris."""

    record: IdempotencyRecord
    scope: KeyScope
    holder: str | None = None


class InMemoryIdempotency:
    """Magasin non durable, limité au process.

    ``reserve`` ne contient aucun ``await`` : le contrôle et la prise de la
    clé sont donc atomiques pour la boucle asyncio, ce que le port exige.

    Une réservation périmée n'est **jamais** effacée d'elle-même : c'est la
    trace d'un effet d'état inconnu, et l'effacer la ferait passer pour un
    appel jamais lancé (point 18). Seuls les résultats hors rétention s'oublient.
    """

    def __init__(self, *, retention: float = DEFAULT_RETENTION) -> None:
        self.retention = retention
        self._held: dict[str, _Held] = {}

    def __repr__(self) -> str:
        return f"InMemoryIdempotency({len(self._held)} clés)"

    async def get(self, key: str) -> IdempotencyRecord | None:
        """Ce que le magasin sait de cette clé, ou ``None`` (``IdempotencyStore.get``).

        Un résultat hors de sa rétention n'est pas rendu ; une réservation
        périmée, si.
        """
        held = self._held.get(key)
        if held is None:
            return None
        record = held.record
        # Un résultat hors de sa rétention n'a plus cours ; une réservation
        # périmée, si — c'est la trace d'un effet d'état inconnu.
        if record.status == "completed" and not record.alive(datetime.now(UTC)):
            return None
        return record

    async def reserve(self, key: str, ttl: float, scope: KeyScope, *, holder: str | None = None) -> bool:
        """Prend la clé pour ``ttl`` secondes ; faux si quelqu'un la tient déjà (``IdempotencyStore.reserve``).

        Ce qui protège, c'est la date : une réservation tenue ou un résultat
        encore mémorisé gardent la clé ; une clé périmée peut être reprise. Sans ``await`` : atomique pour la boucle asyncio.

        Args:
            key: La clé.
            ttl: Durée de la réservation, en secondes.
            scope: De qui est la clé (client, session), pour l'oublier avec eux.
            holder: Jeton propre à cette prise de clé.

        Returns:
            Vrai si la clé est prise.
        """
        now = datetime.now(UTC)
        held = self._held.get(key)
        # C'est la date qui protège : une réservation tenue ou un résultat
        # encore mémorisé gardent la clé, une ligne périmée la rend.
        if held is not None and held.record.alive(now):
            return False
        self._held[key] = _Held(
            record=IdempotencyRecord(key=key, status="in_progress", expires_at=now + timedelta(seconds=ttl)),
            scope=scope,
            holder=holder,
        )
        return True

    async def complete(self, key: str, result: object, ttl: float | None = None, *, holder: str | None = None) -> None:
        """Enregistre ce que l'effet a rendu : la clé cesse d'être une réservation (``IdempotencyStore.complete``).

        Les résultats périmés sont d'abord effacés ; les réservations, jamais.

        Args:
            key: La clé réservée.
            result: Ce que l'outil a rendu.
            ttl: Durée de consultation du résultat, en secondes ; ``None`` : la
                rétention du magasin.
            holder: Avec un jeton, l'enregistrement n'a lieu que s'il est celui de
                la clé ; sinon, il est sans effet.

        Raises:
            KeyError: Le magasin ne connaît plus la clé.
        """
        now = datetime.now(UTC)
        self._forget(now)
        held = self._held.get(key)
        if held is None:
            raise KeyError(f"Clé {key!r} non réservée : rien à enregistrer")
        if holder is not None and held.holder != holder:
            # Réservation reprise par un autre : ce résultat n'est pas le sien à poser.
            return
        self._held[key] = _Held(
            record=IdempotencyRecord(
                key=key,
                status="completed",
                result=recordable(result),
                expires_at=now + timedelta(seconds=self.retention if ttl is None else ttl),
            ),
            scope=held.scope,
            holder=held.holder,
        )

    async def release(self, key: str, *, holder: str | None = None) -> None:
        """Rend une clé réservée dont l'effet ne s'est pas produit (``IdempotencyStore.release``).

        Un résultat enregistré n'est jamais rendu ; avec un jeton, seule une
        réservation encore à ce détenteur l'est.

        Args:
            key: La clé réservée.
            holder: Le jeton de la prise de clé.
        """
        held = self._held.get(key)
        if held is not None and held.record.status == "in_progress" and (holder is None or held.holder == holder):
            del self._held[key]

    async def forget(self, tenant_id: TenantId, session_id: SessionId | None = None) -> int:
        """Oublie les clés d'un client, ou de l'une de ses sessions (RGPD)."""
        partantes = [
            key for key, held in self._held.items() if held.scope.tenant_id == tenant_id and (session_id is None or held.scope.session_id == session_id)
        ]
        for key in partantes:
            del self._held[key]
        return len(partantes)

    def _forget(self, now: datetime) -> None:
        """Oublie les résultats hors rétention ; les réservations restent."""
        stale = [key for key, held in self._held.items() if held.record.status == "completed" and not held.record.alive(now)]
        for key in stale:
            del self._held[key]
