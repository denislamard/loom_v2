# SPDX-License-Identifier: Apache-2.0
"""Mémoire des effets déjà produits, par clé (#18, #49).

Un outil à effet de bord peut être exécuté deux fois : parce que son appel a
été interrompu entre l'effet et son enregistrement au journal, ou parce que le
modèle redemande la même action par un autre appel. Ce port donne de quoi s'en
prémunir : réserver une clé avant d'agir, enregistrer le résultat après, et le
retrouver au lieu de refaire.

``reserve`` est le seul point délicat. ``get`` puis ``reserve``, c'est un
check-then-act : entre les deux, un autre pilote peut passer. Ce n'est donc pas
``get`` qui protège, c'est l'**atomicité** de ``reserve`` — il rend faux si la
clé est déjà tenue, et c'est ce faux qui fait foi.

**Jeton de détenteur.** Une réservation périmée est reprise par un autre, et
son premier détenteur, resté en vie, finit un jour par appeler ``complete`` ou
``release`` : sans rien pour les distinguer, il écraserait la réservation ou le
résultat de son successeur. ``reserve`` reçoit donc un jeton opaque (``holder``)
que le magasin retient avec la clé ; ``complete`` et ``release`` ne s'appliquent
que si on leur présente ce jeton. Il est **optionnel** : sans lui, rien n'est
contrôlé, comme avant — un appelant ou un magasin qui n'en connaît pas reste
valable.
"""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from loom_ia.core.model.ids import SessionId, TenantId
from loom_ia.core.model.tooling import IdempotencyRecord


@dataclass(frozen=True, slots=True, kw_only=True)
class KeyScope:
    """De qui est une clé : le client qui la porte, la session qui l'a créée.

    Le client cadre la clé — deux artisans ne se partagent pas « relance du
    devis D-2026-042 ». La session dit de quoi la clé est la trace, et c'est
    par elle qu'on l'efface quand la session s'efface (RGPD).
    """

    tenant_id: TenantId
    session_id: SessionId


@runtime_checkable
class IdempotencyStore(Protocol):
    """Magasin de clés d'idempotence (#49)."""

    async def get(self, key: str) -> IdempotencyRecord | None:
        """Ce que le magasin sait de cette clé, ou ``None`` s'il ne la connaît pas.

        Un résultat hors de sa durée de vie n'est **pas** rendu : il n'a plus
        cours, et le rendre ferait croire à un effet encore mémorisé. Une
        réservation périmée, elle, est rendue telle quelle — c'est la trace
        d'un effet d'état inconnu, et c'est à l'appelant d'en décider (#18),
        non au magasin de l'effacer.
        """
        ...

    async def reserve(
        self, key: str, ttl: float, scope: KeyScope, *, holder: str | None = None
    ) -> bool:
        """Prend la clé pour ``ttl`` secondes ; faux si quelqu'un la tient déjà.

        Atomique : de deux appelants simultanés, un seul obtient vrai. Une
        réservation **périmée** peut être reprise — c'est le seul chemin par
        lequel un effet d'état inconnu est rejoué, et il faut le vouloir.

        ``scope`` dit de qui est la clé : le magasin le retient pour pouvoir
        l'oublier avec sa session ou avec son client.

        ``holder`` : jeton opaque, propre à cette prise de clé ; le magasin le
        retient, et la reprise d'une réservation périmée le remplace.
        """
        ...

    async def complete(
        self, key: str, result: object, ttl: float | None = None, *, holder: str | None = None
    ) -> None:
        """Enregistre ce que l'effet a rendu : la clé cesse d'être une réservation.

        ``ttl`` : durée pendant laquelle le résultat reste consultable ;
        ``None`` laisse le magasin décider.

        ``holder`` : avec un jeton, l'enregistrement n'a lieu que s'il est celui
        de la clé ; celui d'un détenteur périmé, dont la clé a été reprise,
        n'a **aucun effet** (et ne lève pas). Une clé que le magasin ne connaît
        plus lève ``KeyError``, avec ou sans jeton.
        """
        ...

    async def release(self, key: str, *, holder: str | None = None) -> None:
        """Rend une clé réservée dont l'effet ne s'est **pas** produit.

        À n'appeler que si l'on en est sûr : un échec avant tout effet de
        bord. Dans le doute, on laisse la réservation expirer.

        ``holder`` : avec un jeton, la clé n'est rendue que si elle est encore
        à ce détenteur ; sinon elle est à quelqu'un d'autre, et n'est pas touchée.
        """
        ...

    async def forget(self, tenant_id: TenantId, session_id: SessionId | None = None) -> int:
        """Oublie les clés d'un client, ou de l'une de ses sessions (RGPD).

        Rend le nombre de clés effacées. Une clé oubliée rend son effet
        reproductible : c'est le prix de la suppression, et il est assumé —
        la trace de cet effet disparaît de toute façon avec la session.
        """
        ...
