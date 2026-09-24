# SPDX-License-Identifier: Apache-2.0
"""Magasin d'idempotence dans Redis : partagé, rapide, oublieux (#18, #49).

Même contrat que les magasins SQL, avec une différence qu'il faut avoir en
tête : **Redis oublie tout seul**. Une clé porte une durée de vie, et passée
la rétention elle disparaît — y compris une réservation périmée, que SQLite et
Postgres gardent indéfiniment comme la trace d'un effet d'état inconnu. Au
bout d'un jour (la rétention par défaut), cet effet redevient donc rejouable.
C'est le prix d'un magasin qui ne grossit jamais tout seul, et il est assumé :
une clé métier sert à ne pas renvoyer deux fois le même e-mail dans la
journée, pas à s'en souvenir un an.

``reserve`` tient en un **script Lua**, exécuté d'un bloc par Redis : lire la
clé, la reprendre si sa date est passée, ne rien faire sinon. C'est le même
arbitrage que le ``ON CONFLICT … WHERE`` des magasins SQL — ce qui protège,
c'est l'atomicité, pas le ``get`` qui précède.

Ce qu'une clé appartient (client, session) est tenu à part, dans un ensemble :
Redis ne sait pas chercher, et l'oubli RGPD doit pouvoir nommer les clés d'une
session sans balayer la base.

Le SDK ``redis`` laisse des ``Unknown`` dans ses signatures asynchrones (des
``**kwargs`` non typés) : les trois règles pyright concernées sont levées pour
ce module, comme pour le bus Redis.
"""

# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false

import json
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import redis.asyncio as redis

from loom_ia.core.model import (
    DEFAULT_RETENTION,
    IdempotencyRecord,
    IdempotencyStatus,
    SessionId,
    TenantId,
    recordable,
)
from loom_ia.core.ports import KeyScope

# Préfixes : les enregistrements d'un côté, les appartenances de l'autre.
RECORD: Final = "loom:idem:k:"
OWNER: Final = "loom:idem:o:"

# Prendre la clé, ou la reprendre si sa date est passée. Rend 1 si elle est à
# nous, 0 si quelqu'un la tient encore. L'ensemble d'appartenance suit la
# clé : il porte la même durée de vie, pour disparaître avec elle.
_RESERVE: Final = """
local raw = redis.call('GET', KEYS[1])
if raw then
  local held = cjson.decode(raw)
  if tonumber(held.expires_at) > tonumber(ARGV[2]) then
    return 0
  end
end
redis.call('SET', KEYS[1], ARGV[1], 'PX', tonumber(ARGV[3]))
redis.call('SADD', KEYS[2], KEYS[1])
redis.call('PEXPIRE', KEYS[2], tonumber(ARGV[3]))
return 1
"""

# Enregistrer ce que l'effet a rendu : seulement si la clé est bien tenue.
_COMPLETE: Final = """
if not redis.call('GET', KEYS[1]) then
  return 0
end
redis.call('SET', KEYS[1], ARGV[1], 'PX', tonumber(ARGV[2]))
return 1
"""


def _seconds(moment: datetime) -> float:
    return moment.timestamp()


def _ms(seconds: float) -> str:
    """Durée de vie en millisecondes, jamais nulle : Redis refuse un PX à 0."""
    return str(max(int(seconds * 1000), 1))


class RedisIdempotency:
    """Magasin partagé par tous les process qui parlent au même Redis.

    ``retention`` : durée de vie d'un résultat mémorisé quand l'outil n'en
    fixe pas — et, ici, durée au bout de laquelle Redis oublie la clé, quel
    que soit son état.
    """

    def __init__(self, url: str, *, retention: float = DEFAULT_RETENTION) -> None:
        self._url = url
        self.retention = retention
        self._client: redis.Redis | None = None

    def __repr__(self) -> str:
        return f"RedisIdempotency({RECORD!r})"

    def _redis(self) -> redis.Redis:
        if self._client is None:
            self._client = redis.from_url(self._url)
        return self._client

    async def get(self, key: str) -> IdempotencyRecord | None:
        """Un résultat hors de sa rétention n'est pas rendu ; une réservation périmée, si."""
        raw = await self._redis().get(RECORD + key)
        if raw is None:
            return None
        held: dict[str, Any] = json.loads(raw)
        expires = datetime.fromtimestamp(float(held["expires_at"]), UTC)
        status = _status(held.get("status"))
        if status == "completed" and expires <= datetime.now(UTC):
            return None
        return IdempotencyRecord(
            key=key, status=status, result=held.get("result"), expires_at=expires
        )

    async def reserve(self, key: str, ttl: float, scope: KeyScope) -> bool:
        now = datetime.now(UTC)
        record = json.dumps(
            {
                "status": "in_progress",
                "result": None,
                "expires_at": _seconds(now + timedelta(seconds=ttl)),
            }
        )
        taken = await self._redis().eval(
            _RESERVE,
            2,
            RECORD + key,
            f"{OWNER}{scope.tenant_id}:{scope.session_id}",
            record,
            str(_seconds(now)),
            # La clé vit au moins le temps de la réservation : une rétention
            # plus courte ferait disparaître une réservation encore tenue.
            _ms(max(self.retention, ttl)),
        )
        return int(taken) == 1

    async def complete(self, key: str, result: object, ttl: float | None = None) -> None:
        now = datetime.now(UTC)
        expires = now + timedelta(seconds=self.retention if ttl is None else ttl)
        record = json.dumps(
            {
                "status": "completed",
                "result": recordable(result),
                "expires_at": _seconds(expires),
            },
            ensure_ascii=False,
        )
        done = await self._redis().eval(
            _COMPLETE, 1, RECORD + key, record, _ms((expires - now).total_seconds())
        )
        if int(done) == 0:
            raise KeyError(f"Clé {key!r} non réservée : rien à enregistrer")

    async def release(self, key: str) -> None:
        """Rend une clé réservée dont l'effet ne s'est pas produit."""
        found = await self.get(key)
        if found is not None and found.status == "in_progress":
            _ = await self._redis().delete(RECORD + key)

    async def forget(self, tenant_id: TenantId, session_id: SessionId | None = None) -> int:
        """Oublie les clés d'un client, ou de l'une de ses sessions (RGPD).

        Les appartenances sont tenues à part, faute de quoi il faudrait
        balayer Redis pour retrouver les clés d'une session.
        """
        client = self._redis()
        if session_id is not None:
            owners = [f"{OWNER}{tenant_id}:{session_id}"]
        else:
            owners = [
                key.decode() if isinstance(key, bytes) else str(key)
                async for key in client.scan_iter(match=f"{OWNER}{tenant_id}:*")
            ]
        removed = 0
        for owner in owners:
            keys = await client.smembers(owner)
            if keys:
                removed += int(await client.delete(*keys))
            _ = await client.delete(owner)
        return removed

    async def aclose(self) -> None:
        client = self._client
        self._client = None
        if client is not None:
            await client.aclose()


def _status(value: object) -> IdempotencyStatus:
    return "completed" if value == "completed" else "in_progress"
