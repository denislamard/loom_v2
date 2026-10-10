# SPDX-License-Identifier: Apache-2.0
"""Raccordement Postgres : DSN, pool, rôle applicatif et client par transaction.

Partagé par le journal (``adapters.stores.postgres``) et l'idempotence
(``adapters.idempotency.postgres``), qui ne se connaissent pas — les familles
d'adaptateurs sont indépendantes — mais ont le même raccordement à faire.

Quatre choses s'y jouent, et aucune ne va de soi.

**Le DSN ne vient jamais de la config.** Elle nomme la variable
d'environnement qui le porte (``dsn_env``), comme elle nomme déjà celle d'une
clé d'API : un mot de passe n'a pas à se trouver dans un fichier versionné
(§16.3).

**Le schéma est créé à la première ouverture**, si le rôle connecté le peut.
Sinon l'erreur renvoie vers ``loom storage sql``, dont la sortie s'applique
avec le rôle qui en a le droit. Le DDL est rejouable, donc les deux chemins
cohabitent. Un seul process le pose à la fois, sous verrou consultatif, et
un pool ne s'ouvre qu'une fois même si ses premières requêtes arrivent
ensemble.

**Chaque connexion prend le rôle applicatif.** La sécurité au niveau des
lignes ne s'applique pas au propriétaire d'une table, sauf ``FORCE`` — que le
DDL pose —, et le rôle applicatif n'a de toute façon pas le droit de modifier
un événement écrit. Le rôle est pris une fois par connexion, pas par
transaction : c'est le pool qui le fait à l'ouverture.

**Le client est posé par transaction**, par ``set_config(..., true)`` dont le
``true`` veut dire « local à la transaction ». Toute lecture et toute
écriture passent donc par une transaction, même une simple lecture : hors
transaction, le réglage ne vaudrait que pour l'instruction suivante et la
politique ne verrait rien.
"""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Final

import asyncpg
from asyncpg.pool import PoolConnectionProxy

from loom_ia.adapters.postgres.sql import TENANT_SETTING, check_name

# Connexion tenue le temps d'une transaction : ce que rend le pool, et non un
# ``asyncpg.Connection``, qu'il enrobe.
type Held = PoolConnectionProxy[asyncpg.Record]

# Connexions gardées ouvertes par pool. Basse par défaut : un service de
# quelques agents n'a pas besoin de dix connexions, et un client qui déclare
# son propre stockage a son propre pool (§7.3).
MIN_SIZE: Final = 1
MAX_SIZE: Final = 10
# Délais par défaut, en secondes. Avant eux, rien ne bornait l'attente : un verrou
# tenu par une transaction coincée gelait tous les ``append`` de la session, et un
# pool épuisé faisait attendre ``acquire()`` sans fin.
COMMAND_TIMEOUT: Final = 60.0
LOCK_TIMEOUT: Final = 10.0
ACQUIRE_TIMEOUT: Final = 30.0


@dataclass(frozen=True, slots=True)
class PoolLimits:
    """Taille d'un pool et délais de ses attentes.

    ``command_timeout`` borne une instruction, ``lock_timeout`` l'attente d'un
    verrou (le verrou consultatif du journal, celui du schéma, un verrou de
    ligne), ``acquire_timeout`` l'attente d'une connexion libre du pool. Un
    dépassement lève : ``TimeoutError`` pour la première et la dernière,
    ``asyncpg.LockNotAvailableError`` pour la seconde.
    """

    min_size: int = MIN_SIZE
    max_size: int = MAX_SIZE
    command_timeout: float = COMMAND_TIMEOUT
    lock_timeout: float = LOCK_TIMEOUT
    acquire_timeout: float = ACQUIRE_TIMEOUT

    def __post_init__(self) -> None:
        if not 1 <= self.min_size <= self.max_size:
            raise ValueError(
                f"Pool Postgres : 1 <= min_size <= max_size attendu, "
                f"pas {self.min_size} et {self.max_size}"
            )
        for name in ("command_timeout", "lock_timeout", "acquire_timeout"):
            if getattr(self, name) <= 0:
                raise ValueError(f"Pool Postgres : {name} doit être positif")

    @property
    def lock_milliseconds(self) -> str:
        """Délai de verrou tel que Postgres le lit : des millisecondes entières, une au moins."""
        return str(max(1, round(self.lock_timeout * 1000)))


_PRESENT: Final = "SELECT to_regclass($1)"
# Verrou consultatif de session, pris le temps de poser le schéma : tous les
# process qui ouvrent un stockage Postgres sur la même base le partagent.
_SCHEMA_LOCK: Final = "SELECT pg_advisory_lock(hashtext($1)::bigint)"
_SCHEMA_KEY: Final = "loom_ia/schema"
# Réglages **de la transaction** (``true``) : le client pour la politique de lignes, et
# le délai des verrous. Le pool d'asyncpg rend ses connexions avec ``RESET ALL`` — un
# ``SET`` fait à l'ouverture ne survivrait pas à la première transaction.
_SET_TENANT: Final = (
    f"SELECT set_config('{TENANT_SETTING}', $1, true), set_config('lock_timeout', $2, true)"
)
_SET_LOCK: Final = "SELECT set_config('lock_timeout', $1, true)"


DEFAULT_LIMITS: Final = PoolLimits()


class PostgresNotPrepared(RuntimeError):
    """La base n'a pas le schéma de loom, et le rôle connecté ne peut pas le poser.

    Le mot de Postgres est repris tel quel : le schéma tient des tables, un
    rôle et des politiques, et ce n'est pas toujours la table qui manque —
    un rôle applicatif créé par quelqu'un d'autre ne s'accorde pas non plus.
    """

    def __init__(self, table: str, role: str, said: str) -> None:
        super().__init__(
            f"Base Postgres non préparée : le rôle {role!r} n'a pas pu poser le schéma de "
            f"loom ({table!r} et ce qui va avec). Postgres a dit : « {said} ». Appliquer la "
            "sortie de 'loom storage sql' avec un rôle qui en a le droit."
        )


class RoleUnavailable(RuntimeError):
    """Le rôle connecté ne peut pas endosser le rôle applicatif."""

    def __init__(self, role: str, current: str) -> None:
        super().__init__(
            f"Rôle applicatif {role!r} : le rôle connecté {current!r} ne peut pas le "
            f"prendre. Soit 'GRANT {role} TO {current}', soit connecter loom "
            f"directement avec {role!r}, soit 'role: null' dans la config."
        )


class PostgresPool:
    """Pool de connexions vers la base d'un stockage, ouvert à la première requête.

    ``ddl`` est appliqué si ``table`` n'existe pas encore ; ``role``, s'il est
    donné, est pris par chaque connexion du pool ; ``limits`` fixe sa taille et
    ses délais. ``upgrade``, s'il est donné,
    est appliqué quand la table existe déjà : la mise à niveau d'un schéma plus
    ancien, qui doit pouvoir se rejouer sans effet.
    """

    def __init__(
        self,
        dsn: str,
        *,
        table: str,
        ddl: str,
        role: str | None,
        label: str = "Postgres",
        upgrade: str | None = None,
        limits: PoolLimits = DEFAULT_LIMITS,
    ) -> None:
        self._dsn = dsn
        self._limits = limits
        self._table = table
        self._ddl = ddl
        self._upgrade = upgrade
        self._role = None if role is None else check_name(role)
        self._label = label
        self._pool: asyncpg.Pool[asyncpg.Record] | None = None
        self._opening = asyncio.Lock()

    def __repr__(self) -> str:
        return f"PostgresPool({self._table!r}, role={self._role!r})"

    # --- Ouverture --------------------------------------------------------

    async def pool(self) -> asyncpg.Pool[asyncpg.Record]:
        """Le pool, créé et vérifié à la première demande.

        Les premières demandes simultanées attendent la même ouverture : un
        seul ``_prepare`` et un seul pool, jamais un pool ouvert puis perdu.
        """
        if self._pool is None:
            async with self._opening:
                if self._pool is None:
                    await self._prepare()
                    self._pool = await asyncpg.create_pool(
                        self._dsn,
                        min_size=self._limits.min_size,
                        max_size=self._limits.max_size,
                        command_timeout=self._limits.command_timeout,
                        init=self._take_role,
                    )
        return self._pool

    async def _prepare(self) -> None:
        """Crée le schéma s'il manque, avec une connexion **sans** rôle applicatif.

        Sans rôle, parce que c'est le propriétaire qui crée : la connexion du
        pool, elle, aura déjà pris le rôle applicatif et n'aurait pas le
        droit.
        """
        connection = await asyncpg.connect(self._dsn)
        try:
            # L'attente du verrou ci-dessous est bornée comme les autres : un process
            # qui pose le schéma depuis une heure ne bloque pas les suivants sans fin.
            await connection.execute(f"SET lock_timeout = {self._limits.lock_milliseconds}")
            # Un seul process pose le schéma à la fois. Sans ce verrou, deux
            # ouvertures simultanées voient la table absente et jouent chacune
            # le DDL ; le second ``ALTER TABLE`` demande un verrou exclusif
            # pendant que des écrivains travaillent déjà sur la table du
            # premier, et Postgres les interbloque. Le verrou est de session :
            # la fermeture de la connexion le rend.
            await connection.execute(_SCHEMA_LOCK, _SCHEMA_KEY)
            present = await connection.fetchval(_PRESENT, self._table) is not None
            statement = self._upgrade if present else self._ddl
            if statement is None:
                return
            try:
                await connection.execute(statement)
            except asyncpg.InsufficientPrivilegeError as exc:
                current = await connection.fetchval("SELECT current_user")
                raise PostgresNotPrepared(self._table, str(current), str(exc)) from exc
        finally:
            await connection.close()

    async def _take_role(self, connection: asyncpg.Connection[asyncpg.Record]) -> None:
        """Prend le rôle applicatif sur une connexion neuve du pool."""
        if self._role is None:
            return
        current = await connection.fetchval("SELECT current_user")
        if current == self._role:
            return
        try:
            await connection.execute(f"SET ROLE {self._role}")
        except asyncpg.InsufficientPrivilegeError as exc:
            raise RoleUnavailable(self._role, str(current)) from exc

    # --- Transactions -----------------------------------------------------

    @asynccontextmanager
    async def transaction(self, tenant_id: str | None = None) -> AsyncGenerator[Held]:
        """Une transaction, avec le client posé pour la politique s'il est donné.

        Sans ``tenant_id``, la politique du journal ne laisse rien passer :
        c'est pour les tables qui n'en ont pas, comme celle de l'idempotence.
        """
        pool = await self.pool()
        async with (
            pool.acquire(timeout=self._limits.acquire_timeout) as connection,
            connection.transaction(),
        ):
            lock = self._limits.lock_milliseconds
            if tenant_id is not None:
                await connection.execute(_SET_TENANT, tenant_id, lock)
            else:
                await connection.execute(_SET_LOCK, lock)
            yield connection

    async def aclose(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None


def rows_touched(status: str) -> int:
    """Nombre de lignes d'un état de commande asyncpg (« UPDATE 1 », « INSERT 0 1 »)."""
    parts = status.split()
    return int(parts[-1]) if parts and parts[-1].isdigit() else 0
