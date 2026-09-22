# SPDX-License-Identifier: Apache-2.0
"""Magasin d'idempotence adossé au journal du run (#18, #49).

Il n'a pas d'autre stockage que le journal : la trace est durable dès que le
journal l'est, et il n'y a rien à installer. En contrepartie il ne porte que
des **clés techniques** — celles d'un appel précis, ``hash(run_id, call_id)``.
Une clé métier doit être vue depuis un autre run, et le journal d'un run ne
l'est pas (magasin partagé, J4.4b).

La réservation, elle, est déjà au journal : c'est le ``tool.called`` de
l'appel, écrit avant son exécution. ``reserve`` n'a donc rien à écrire, et
c'est la règle de reprise (#18) qui décide du sort d'un appel commencé sans
résultat. Ce que ce magasin ajoute, c'est l'autre moitié : un appel rejoué
dont l'effet **a** eu lieu rend le résultat mémorisé au lieu de le refaire.

La concession (#27) garantit un seul pilote par run : deux exécutions
simultanées du même appel ne peuvent pas se produire, et ``reserve`` n'a donc
pas à les départager.

Une clé **métier** n'a donc rien à faire ici, et le chargement le refuse : ce
magasin ne la porterait que dans un run, là où elle doit valoir pour tous.

``complete`` écrit tout de suite, par l'écrivain de la session, sans passer
par la file du lot : c'est justement entre l'effet et le ``tool.completed``
que se situe l'accident qu'on veut couvrir. Un enregistrement mis en file
serait écrit en même temps que le résultat, et ne couvrirait rien.
"""

from datetime import UTC, datetime, timedelta

from loom_ia.core.events import IdempotencyRecorded, RunScope
from loom_ia.core.model import IdempotencyRecord, RunId, SessionId, SpanId, TenantId, recordable
from loom_ia.core.ports import KeyScope
from loom_ia.engine.writer import SessionWriter

# Un enregistrement du journal ne périme pas : il vaut tant que le run existe.
_FOREVER = timedelta(days=365 * 100)


class JournalIdempotency:
    """``IdempotencyStore`` qui lit et écrit dans le journal du run."""

    def __init__(
        self,
        writer: SessionWriter,
        scope: RunScope,
        *,
        call_id: str,
        tool_name: str,
        span_id: SpanId | None = None,
        parent_span_id: SpanId | None = None,
    ) -> None:
        self._writer = writer
        self._scope = scope
        self._call_id = call_id
        self._tool_name = tool_name
        # Span de l'appel et celui de son étape : l'enregistrement se range
        # sous l'appel qui l'a produit, comme son ``tool.called``. Sans eux,
        # il tombe sur le span racine du run.
        self._span_id = span_id
        self._parent_span_id = parent_span_id

    def __repr__(self) -> str:
        return f"JournalIdempotency(run {self._run_id}, appel {self._call_id})"

    @property
    def _run_id(self) -> RunId:
        return self._scope.run_id

    async def get(self, key: str) -> IdempotencyRecord | None:
        """Résultat mémorisé pour cette clé, s'il a été enregistré dans ce run.

        Relit les événements du run à chaque appel : c'est le prix d'un
        magasin sans stockage propre, et il se paie une fois par appel d'outil
        décoré. Une clé seulement réservée n'est pas ici — elle est au
        ``tool.called``, que la reprise (#18) consulte de son côté.
        """
        events = await self._writer.store.read(
            self._writer.tenant_id, self._writer.session_id, run_id=self._run_id
        )
        recorded = [
            payload
            for event in events
            if isinstance(payload := event.payload, IdempotencyRecorded) and payload.key == key
        ]
        if not recorded:
            return None
        return IdempotencyRecord(
            key=key,
            status="completed",
            result=recorded[-1].result,
            expires_at=datetime.now(UTC) + _FOREVER,
        )

    async def reserve(self, key: str, ttl: float, scope: KeyScope) -> bool:
        """Toujours vrai : le ``tool.called`` de l'appel tient lieu de réservation."""
        return True

    async def complete(self, key: str, result: object, ttl: float | None = None) -> None:
        """Écrit l'effet au journal du run, sous sa clé."""
        await self._writer.append(
            [
                self._scope.draft(
                    IdempotencyRecorded(
                        key=key,
                        call_id=self._call_id,
                        tool_name=self._tool_name,
                        result=recordable(result),
                    ),
                    span_id=self._span_id,
                    parent_span_id=self._parent_span_id,
                )
            ]
        )

    async def release(self, key: str) -> None:
        """Sans objet : rien n'a été écrit à la réservation."""
        return None

    async def forget(self, tenant_id: TenantId, session_id: SessionId | None = None) -> int:
        """Sans objet : les enregistrements partent avec le journal de leur session.

        Supprimer une session (RGPD) efface ses événements, et ceux-ci en
        font partie. Il n'y a rien de plus à oublier ici.
        """
        return 0
