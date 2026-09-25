# SPDX-License-Identifier: Apache-2.0
"""Port du sceau : fermer et ouvrir des octets avec la clé d'un client (#30, §11.5).

Le journal garde les contenus complets — c'est lui qui fait foi (#22). Ce
port permet de les garder **illisibles au repos** : la charge d'un événement
et les octets d'un fichier sont fermés avec la clé du client à qui ils
appartiennent. Effacer cette clé rend tout son journal illisible,
sauvegardes comprises : c'est le *crypto-shredding* du RGPD, en complément
de la suppression physique (point 22).

Une clé par client, et loom ne la détient jamais : elle vit dans les secrets
du client (``SecretProvider``), comme les clés de ses fournisseurs. La
config ne nomme que le secret qui la porte.

``key_id`` est une **empreinte** de la clé : elle dit laquelle a fermé un
sceau, sans rien dire de la clé elle-même. C'est ce qui rend le
renouvellement possible — la nouvelle clé ferme, les anciennes ouvrent
encore ce qu'elles ont fermé — et ce qui permet de **nommer** la clé qui
manque au lieu de la deviner.

``aad`` (*additional authenticated data*) n'est pas chiffré : il est
authentifié. Un sceau ouvert avec un ``aad`` différent de celui qui l'a
fermé est refusé, ce qui interdit de déplacer un sceau — d'un journal à un
autre, d'une position à une autre, d'un fichier à un autre.
"""

from collections.abc import Sequence
from typing import Protocol

from loom_ia.core.model.ids import TenantId


class SealError(Exception):
    """Un sceau n'a pas pu être fermé ou ouvert."""


class MissingKey(SealError):
    """Aucune clé pour ce client, ou pas celle qui a fermé ce sceau.

    Le cas voulu du *crypto-shredding* : la clé effacée, ce qu'elle a fermé
    ne s'ouvre plus. Le cas subi d'une variable oubliée lui ressemble trait
    pour trait, et la configuration ne peut pas les distinguer — le message
    nomme donc le client et l'empreinte attendue, et laisse conclure.
    """


class SealBroken(SealError):
    """Le sceau ne s'ouvre pas avec cette clé : octets ou ``aad`` altérés."""


class Cipher(Protocol):
    """Une clé, en service : elle ferme et elle ouvre."""

    @property
    def key_id(self) -> str:
        """Empreinte de la clé : elle dit laquelle, sans la dire."""
        ...

    def seal(self, clear: bytes, aad: bytes) -> bytes:
        """Ferme ces octets ; ``aad`` est authentifié, pas chiffré."""
        ...

    def unseal(self, sealed: bytes, aad: bytes) -> bytes:
        """Ouvre ces octets ; ``SealBroken`` si le sceau ne correspond pas."""
        ...


class Keyring(Protocol):
    """Les clés des clients, telles que la configuration les désigne."""

    def ciphers(self, tenant_id: TenantId) -> Sequence[Cipher]:
        """Clés de ce client : la première ferme, toutes peuvent ouvrir.

        Lève ``MissingKey`` quand ce client n'en a aucune : un trousseau
        existe parce que la config a demandé le sceau, et écrire en clair
        sous prétexte qu'une clé manque serait exactement la fuite que le
        sceau est là pour empêcher.
        """
        ...
