# SPDX-License-Identifier: Apache-2.0
"""Magasin d'idempotence en Postgres : partagé entre machines (#18, #49).

Le magasin ``sqlite`` suffit à plusieurs process d'une même machine ; celui-ci
tient quand les workers sont sur plusieurs machines, ce qui est le cas dès
que la file entre en jeu (5.3b).

``reserve`` tient en une seule instruction, comme en SQLite — prendre la clé
si elle est libre, la reprendre si la réservation d'un autre a expiré, ne
rien faire sinon —, et c'est Postgres qui arbitre : ``ON CONFLICT (key) DO
UPDATE … WHERE``. Le nombre de lignes touchées fait foi. Un ``get`` suivi
d'un ``INSERT`` serait un check-then-act, et deux workers passeraient.

Pas de politique de lignes sur cette table, contrairement au journal :
``get(key)`` ne nomme pas de client, et une politique par client rendrait
invisible la ligne qu'il faut justement relire. Ce qui cadre une clé, c'est
son préfixe par client (#49) ; ce que portent les colonnes ``tenant_id`` et
``session_id``, c'est de quoi l'oublier avec sa session (RGPD).
"""

import json
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from loom_ia.adapters.postgres.pool import PostgresPool, rows_touched
from loom_ia.adapters.postgres.sql import DEFAULT_ROLE, IDEMPOTENCY_TABLE, ddl
from loom_ia.core.model import (
    DEFAULT_RETENTION,
    IdempotencyRecord,
    IdempotencyStatus,
    SessionId,
    TenantId,
    recordable,
)
from loom_ia.core.ports import KeyScope

# Ce qui protège, c'est la date : une réservation encore tenue ou un résultat
# encore mémorisé ne bougent pas. Passée leur échéance, la clé est libre.
_RESERVE: Final = f"""
INSERT INTO {IDEMPOTENCY_TABLE} (key, tenant_id, session_id, status, result, expires_at)
VALUES ($1, $2, $3, 'in_progress', NULL, $4)
ON CONFLICT (key) DO UPDATE SET
    tenant_id  = excluded.tenant_id,
    session_id = excluded.session_id,
    status     = 'in_progress',
    result     = NULL,
    expires_at = excluded.expires_at
WHERE {IDEMPOTENCY_TABLE}.expires_at < $5
"""

_COMPLETE: Final = (
    f"UPDATE {IDEMPOTENCY_TABLE} SET status = 'completed', result = $1, expires_at = $2"
    " WHERE key = $3"
)

_GET: Final = (
    f"SELECT status, result, expires_at FROM {IDEMPOTENCY_TABLE}"
    " WHERE key = $1 AND (status = 'in_progress' OR expires_at > $2)"
)


class PostgresIdempotency:
    """Magasin durable dans une base Postgres.

    ``retention`` : durée de vie d'un résultat mémorisé quand l'outil n'en
    fixe pas. Les résultats périmés sont effacés au fil des écritures ; les
    réservations, jamais — une réservation périmée est la trace d'un effet
    d'état inconnu, et l'effacer la ferait passer pour un appel jamais lancé.
    """

    def __init__(
        self, dsn: str, *, role: str | None = DEFAULT_ROLE, retention: float = DEFAULT_RETENTION
    ) -> None:
        self.retention = retention
        self._pg = PostgresPool(
            dsn,
            table=IDEMPOTENCY_TABLE,
            ddl=ddl(role=role, events=False),
            role=role,
        )

    def __repr__(self) -> str:
        return f"PostgresIdempotency({IDEMPOTENCY_TABLE!r})"

    async def get(self, key: str) -> IdempotencyRecord | None:
        """Un résultat hors de sa rétention n'est pas rendu ; une réservation périmée, si."""
        async with self._pg.transaction() as connection:
            row = await connection.fetchrow(_GET, key, datetime.now(UTC))
        if row is None:
            return None
        result = row["result"]
        return IdempotencyRecord(
            key=key,
            status=_status(row["status"]),
            result=None if result is None else json.loads(str(result)),
            expires_at=row["expires_at"],
        )

    async def reserve(self, key: str, ttl: float, scope: KeyScope) -> bool:
        now = datetime.now(UTC)
        async with self._pg.transaction() as connection:
            status = await connection.execute(
                _RESERVE,
                key,
                scope.tenant_id,
                scope.session_id,
                now + timedelta(seconds=ttl),
                now,
            )
        return rows_touched(status) == 1

    async def complete(self, key: str, result: object, ttl: float | None = None) -> None:
        now = datetime.now(UTC)
        expires = now + timedelta(seconds=self.retention if ttl is None else ttl)
        payload = json.dumps(recordable(result), ensure_ascii=False)
        async with self._pg.transaction() as connection:
            # Le ménage passe **avant** l'écriture : après, il emporterait le
            # résultat qu'on vient de poser si sa rétention était déjà nulle.
            await connection.execute(
                f"DELETE FROM {IDEMPOTENCY_TABLE} WHERE status = 'completed' AND expires_at < $1",
                now,
            )
            status = await connection.execute(_COMPLETE, payload, expires, key)
        if rows_touched(status) == 0:
            raise KeyError(f"Clé {key!r} non réservée : rien à enregistrer")

    async def release(self, key: str) -> None:
        async with self._pg.transaction() as connection:
            await connection.execute(
                f"DELETE FROM {IDEMPOTENCY_TABLE} WHERE key = $1 AND status = 'in_progress'", key
            )

    async def forget(self, tenant_id: TenantId, session_id: SessionId | None = None) -> int:
        """Oublie les clés d'un client, ou de l'une de ses sessions (RGPD)."""
        sql = f"DELETE FROM {IDEMPOTENCY_TABLE} WHERE tenant_id = $1"
        values: list[Any] = [tenant_id]
        if session_id is not None:
            sql += " AND session_id = $2"
            values.append(session_id)
        async with self._pg.transaction() as connection:
            return rows_touched(await connection.execute(sql, *values))

    async def aclose(self) -> None:
        await self._pg.aclose()


def _status(value: object) -> IdempotencyStatus:
    return "completed" if value == "completed" else "in_progress"
