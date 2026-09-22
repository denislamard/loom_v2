# SPDX-License-Identifier: Apache-2.0
"""Magasin d'idempotence en SQLite : partagé, durable, entre process (#18, #49).

C'est le magasin que réclame une clé **métier** : elle doit être vue depuis un
autre run, une autre session, un autre worker, et survivre à un redémarrage.
Le magasin ``journal`` ne voit que son run, ``memory`` que son process.

Une table, une ligne par clé. ``reserve`` tient en **une seule instruction** :

    INSERT … ON CONFLICT(key) DO UPDATE … WHERE status = 'in_progress'
                                            AND expires_at < <maintenant>

Prendre la clé si elle est libre, la reprendre si la réservation d'un autre a
expiré, ne rien faire sinon — et c'est SQLite qui arbitre, pas nous. Un
``get`` suivi d'un ``INSERT`` serait un check-then-act : entre les deux, un
second worker passerait. Le nombre de lignes touchées (0 ou 1) fait foi.

Une réservation périmée n'est jamais effacée d'elle-même : c'est la trace
d'un effet d'état inconnu, et l'effacer la ferait passer pour un appel jamais
lancé. Seuls les résultats hors rétention s'oublient, au fil des écritures.
"""

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

import aiosqlite

from loom_ia.core.model import (
    DEFAULT_RETENTION,
    IdempotencyRecord,
    IdempotencyStatus,
    SessionId,
    TenantId,
    recordable,
)
from loom_ia.core.ports import KeyScope

SCHEMA: Final = """
CREATE TABLE IF NOT EXISTS idempotency (
    key        TEXT NOT NULL PRIMARY KEY,
    tenant_id  TEXT NOT NULL,
    session_id TEXT NOT NULL,
    status     TEXT NOT NULL,
    result     TEXT,
    expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idempotency_owner ON idempotency (tenant_id, session_id);
CREATE INDEX IF NOT EXISTS idempotency_expiry ON idempotency (status, expires_at);
"""

# Prend la clé, ou reprend une ligne périmée. Ce qui protège, c'est la date :
# une réservation encore tenue ou un résultat encore mémorisé ne bougent pas.
# Passée leur échéance, la clé est libre — une réservation périmée parce que
# son effet est d'état inconnu, un résultat parce qu'il n'a plus cours.
_RESERVE: Final = """
INSERT INTO idempotency (key, tenant_id, session_id, status, result, expires_at)
VALUES (?, ?, ?, 'in_progress', NULL, ?)
ON CONFLICT(key) DO UPDATE SET
    tenant_id  = excluded.tenant_id,
    session_id = excluded.session_id,
    status     = 'in_progress',
    result     = NULL,
    expires_at = excluded.expires_at
WHERE idempotency.expires_at < ?
"""

_COMPLETE: Final = (
    "UPDATE idempotency SET status = 'completed', result = ?, expires_at = ? WHERE key = ?"
)


class SqliteIdempotency:
    """Magasin durable dans un fichier SQLite, sa propre base.

    ``retention`` : durée de vie d'un résultat mémorisé quand l'outil n'en
    fixe pas. Les résultats périmés sont effacés au fil des écritures ; les
    réservations, jamais.
    """

    def __init__(
        self, path: str | os.PathLike[str], *, retention: float = DEFAULT_RETENTION
    ) -> None:
        self.path = Path(path)
        self.retention = retention
        self._connection: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    def __repr__(self) -> str:
        return f"SqliteIdempotency({str(self.path)!r})"

    async def _connect(self) -> aiosqlite.Connection:
        if self._connection is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = await aiosqlite.connect(self.path, isolation_level=None)
            await connection.execute("PRAGMA journal_mode = WAL")
            # Une réservation vaut ce que vaut sa durabilité : un effet de
            # bord suit, et on ne veut pas le refaire après un arrêt brutal.
            await connection.execute("PRAGMA synchronous = FULL")
            await connection.execute("PRAGMA busy_timeout = 5000")
            await connection.executescript(SCHEMA)
            self._connection = connection
        return self._connection

    async def get(self, key: str) -> IdempotencyRecord | None:
        """Un résultat hors de sa rétention n'est pas rendu ; une réservation périmée, si."""
        async with self._lock:
            connection = await self._connect()
            async with connection.execute(
                "SELECT status, result, expires_at FROM idempotency WHERE key = ?"
                " AND (status = 'in_progress' OR expires_at > ?)",
                (key, datetime.now(UTC).isoformat()),
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            return None
        status, result, expires_at = row
        return IdempotencyRecord(
            key=key,
            status=_status(status),
            result=None if result is None else json.loads(result),
            expires_at=datetime.fromisoformat(expires_at),
        )

    async def reserve(self, key: str, ttl: float, scope: KeyScope) -> bool:
        now = datetime.now(UTC)
        values: tuple[Any, ...] = (
            key,
            scope.tenant_id,
            scope.session_id,
            (now + timedelta(seconds=ttl)).isoformat(),
            now.isoformat(),
        )
        async with self._lock:
            connection = await self._connect()
            async with connection.execute(_RESERVE, values) as cursor:
                return cursor.rowcount == 1

    async def complete(self, key: str, result: object, ttl: float | None = None) -> None:
        now = datetime.now(UTC)
        expires = now + timedelta(seconds=self.retention if ttl is None else ttl)
        payload = json.dumps(recordable(result), ensure_ascii=False)
        async with self._lock:
            connection = await self._connect()
            # Le ménage passe **avant** l'écriture : après, il emporterait le
            # résultat qu'on vient de poser si sa rétention était déjà nulle.
            await connection.execute(
                "DELETE FROM idempotency WHERE status = 'completed' AND expires_at < ?",
                (now.isoformat(),),
            )
            async with connection.execute(_COMPLETE, (payload, expires.isoformat(), key)) as cursor:
                touched = cursor.rowcount
        if touched == 0:
            raise KeyError(f"Clé {key!r} non réservée : rien à enregistrer")

    async def release(self, key: str) -> None:
        async with self._lock:
            connection = await self._connect()
            await connection.execute(
                "DELETE FROM idempotency WHERE key = ? AND status = 'in_progress'", (key,)
            )

    async def forget(self, tenant_id: TenantId, session_id: SessionId | None = None) -> int:
        """Oublie les clés d'un client, ou de l'une de ses sessions (RGPD)."""
        sql = "DELETE FROM idempotency WHERE tenant_id = ?"
        values: tuple[Any, ...] = (tenant_id,)
        if session_id is not None:
            sql += " AND session_id = ?"
            values = (tenant_id, session_id)
        async with self._lock:
            connection = await self._connect()
            async with connection.execute(sql, values) as cursor:
                return cursor.rowcount

    async def aclose(self) -> None:
        async with self._lock:
            if self._connection is not None:
                await self._connection.close()
                self._connection = None


def _status(value: object) -> IdempotencyStatus:
    return "completed" if value == "completed" else "in_progress"
