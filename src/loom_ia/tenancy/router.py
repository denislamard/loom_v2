# SPDX-License-Identifier: Apache-2.0
"""Isolation physique par client : ``TenantRouter`` (#34, §7.3).

L'isolation logique tient au ``tenant_id`` que porte chaque événement et au
préfixe de chaque URI d'artefact : elle suffit tant que tout le monde
partage la même base. Un client peut vouloir plus — son dossier, son
fichier, et plus tard son schéma Postgres, sa collection Firestore ou son
bucket (J5.3). Il lui suffit de déclarer son propre ``storage``.

Le routeur ne s'insère nulle part dans le moteur : il **est** un journal et
un stockage d'artefacts, qui choisissent le leur au vu du client. Toutes les
opérations des deux ports nomment déjà le client — directement pour le
journal, par l'URI pour les artefacts —, donc le reste de loom-ia ne voit
qu'un stockage de plus et ne sait pas qu'il y en a plusieurs.
"""

from collections.abc import Callable, Sequence

from loom_ia.config.models import StorageConfig
from loom_ia.core.events.envelope import Event, EventDraft
from loom_ia.core.events.query import EventQuery
from loom_ia.core.model import ArtifactLocation, RunId, SessionId, TenantId
from loom_ia.core.ports import ArtifactStore, EventStore, SessionRecord, journal_key
from loom_ia.tenancy.registry import Tenants

type EventStoreFactory = Callable[[StorageConfig], EventStore]
type ArtifactStoreFactory = Callable[[StorageConfig], ArtifactStore]


class TenantRouter:
    """Choisit le journal et le stockage d'artefacts d'un client.

    Un client sans bloc ``storage`` partage ceux de la racine : c'est le cas
    courant, et l'isolation y reste logique. Les stockages créés pour un
    client appartiennent au routeur, qui les ferme ; ceux de la racine
    restent à leur propriétaire.
    """

    def __init__(
        self,
        tenants: Tenants,
        *,
        events: EventStoreFactory,
        artifacts: ArtifactStoreFactory,
        shared_events: EventStore,
        shared_artifacts: ArtifactStore,
    ) -> None:
        self._shared_events = shared_events
        self._shared_artifacts = shared_artifacts
        self._events: dict[TenantId, EventStore] = {}
        self._artifacts: dict[TenantId, ArtifactStore] = {}
        for tenant in tenants.all():
            storage = tenant.storage
            if storage is None:
                continue
            self._events[tenant.id] = events(storage)
            self._artifacts[tenant.id] = artifacts(storage)

    @property
    def routed(self) -> bool:
        """Vrai si au moins un client a son propre stockage."""
        return bool(self._events or self._artifacts)

    @property
    def tenants(self) -> tuple[TenantId, ...]:
        """Clients qui ont leur propre stockage."""
        return tuple(self._events)

    def events(self, tenant_id: TenantId) -> EventStore:
        return self._events.get(tenant_id, self._shared_events)

    def artifacts(self, tenant_id: TenantId) -> ArtifactStore:
        return self._artifacts.get(tenant_id, self._shared_artifacts)

    async def aclose(self) -> None:
        """Ferme les stockages créés pour des clients ; pas ceux de la racine."""
        for store in self._events.values():
            await store.aclose()
        for artifacts in self._artifacts.values():
            await artifacts.aclose()
        self._events.clear()
        self._artifacts.clear()


class RoutedEventStore:
    """Journal qui délègue au journal du client de chaque opération."""

    def __init__(self, router: TenantRouter) -> None:
        self._router = router

    async def append(
        self, drafts: Sequence[EventDraft], *, expected_seq: int | None
    ) -> list[Event]:
        tenant_id, _ = journal_key(drafts)
        return await self._router.events(tenant_id).append(drafts, expected_seq=expected_seq)

    async def read(
        self,
        tenant_id: TenantId,
        session_id: SessionId,
        *,
        after_seq: int = 0,
        run_id: RunId | None = None,
    ) -> list[Event]:
        store = self._router.events(tenant_id)
        return await store.read(tenant_id, session_id, after_seq=after_seq, run_id=run_id)

    async def query(self, query: EventQuery) -> list[Event]:
        return await self._router.events(query.tenant_id).query(query)

    async def last_seq(self, tenant_id: TenantId, session_id: SessionId) -> int:
        return await self._router.events(tenant_id).last_seq(tenant_id, session_id)

    async def sessions(self, tenant_id: TenantId) -> list[SessionRecord]:
        return await self._router.events(tenant_id).sessions(tenant_id)

    async def delete(self, tenant_id: TenantId, session_id: SessionId) -> int:
        return await self._router.events(tenant_id).delete(tenant_id, session_id)

    async def aclose(self) -> None:
        # Les stockages du routeur sont fermés par lui, une fois : l'instance
        # s'en charge à sa fermeture, en même temps que celui de la racine.
        return None


class RoutedArtifactStore:
    """Stockage d'artefacts qui suit le client nommé par l'URI."""

    def __init__(self, router: TenantRouter) -> None:
        self._router = router

    def _of(self, uri: str) -> ArtifactStore:
        return self._router.artifacts(TenantId(ArtifactLocation.parse(uri).tenant))

    async def put(self, uri: str, data: bytes) -> None:
        await self._of(uri).put(uri, data)

    async def get(self, uri: str) -> bytes:
        return await self._of(uri).get(uri)

    async def delete(self, tenant_id: TenantId, session_id: SessionId) -> int:
        return await self._router.artifacts(tenant_id).delete(tenant_id, session_id)

    async def aclose(self) -> None:
        # Les stockages du routeur sont fermés une fois, avec le journal.
        return None
