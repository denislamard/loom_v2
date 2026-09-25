# SPDX-License-Identifier: Apache-2.0
"""Sceau AES-256-GCM, et trousseau lu dans les secrets des clients (#30, §11.5).

Un seul algorithme : AES-256-GCM, chiffrement authentifié. Le sceau porte son
nonce (12 octets tirés au hasard à chaque fermeture) suivi des octets fermés ;
l'``aad`` est authentifié sans être chiffré, si bien qu'un sceau déplacé ne
s'ouvre pas.

La clé ne vient pas de la configuration mais des **secrets du client** : la
config nomme le secret (``storage.encryption.keys``), et chaque client
redirige ce nom vers sa variable (``secrets: {journal_key: MARTIN_JOURNAL_KEY}``).
C'est le mécanisme de 5.1a, sans rien de neuf — et sa règle joue ici à plein :
une redirection vers une variable **absente** vaut vide, jamais le secret
commun. Effacer la variable d'un client scelle donc son journal pour de bon,
sans que celui du voisin y soit pour quelque chose.

Plusieurs noms sont possibles, dans l'ordre : le premier ferme, tous ouvrent.
C'est le renouvellement — on met la nouvelle clé devant, l'ancienne reste
derrière le temps que les anciens journaux servent encore.

L'empreinte (``key_id``) est un condensé de la clé, pas la clé : elle tient
dans une ligne de log ou un message d'erreur, et elle suffit à dire laquelle
a fermé un sceau — donc laquelle manque.
"""

import os
from base64 import b64decode
from collections.abc import Mapping, Sequence
from hashlib import sha256
from typing import Final

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from loom_ia.core.model import TenantId
from loom_ia.core.ports import Cipher, MissingKey, SealBroken, SealError, SecretProvider

# AES-256 : la clé fait 32 octets, et rien d'autre ne passe.
KEY_BYTES: Final = 32
# Nonce de GCM : 96 bits, la taille pour laquelle il est spécifié.
NONCE_BYTES: Final = 12
# Ce que l'empreinte garde du condensé : assez pour distinguer des clés,
# trop peu pour servir à autre chose.
FINGERPRINT_CHARS: Final = 12
_FINGERPRINT_SALT: Final = b"loom.key.v1"


def how_to_make_one() -> str:
    """Comment fabriquer une clé, à dire dans un message d'erreur."""
    return 'python -c "import base64, os; print(base64.b64encode(os.urandom(32)).decode())"'


def fingerprint(key: bytes) -> str:
    """Empreinte d'une clé : elle dit laquelle, sans la dire."""
    return sha256(_FINGERPRINT_SALT + key).hexdigest()[:FINGERPRINT_CHARS]


def decode_key(value: str, *, what: str) -> bytes:
    """Clé de 32 octets tirée d'un secret en base64 ; ``SealError`` sinon.

    Base64 standard ou *urlsafe*, remplissage facultatif : ce qu'un opérateur
    obtient de sa commande, quelle qu'elle soit, sans avoir à y penser.
    """
    text = value.strip()
    padded = text + "=" * (-len(text) % 4)
    try:
        key = b64decode(padded.replace("-", "+").replace("_", "/"), validate=True)
    except ValueError as exc:
        raise SealError(
            f"{what} : la clé n'est pas du base64 ({exc}) — en fabriquer une avec "
            f"{how_to_make_one()}"
        ) from exc
    if len(key) != KEY_BYTES:
        raise SealError(
            f"{what} : la clé fait {len(key)} octets, il en faut {KEY_BYTES} (AES-256) — "
            f"en fabriquer une avec {how_to_make_one()}"
        )
    return key


class AesGcmCipher:
    """Une clé AES-256-GCM en service."""

    def __init__(self, key: bytes) -> None:
        if len(key) != KEY_BYTES:
            raise SealError(f"Clé de {len(key)} octets : il en faut {KEY_BYTES} (AES-256)")
        self._aead = AESGCM(key)
        self._key_id = fingerprint(key)

    @property
    def key_id(self) -> str:
        return self._key_id

    def seal(self, clear: bytes, aad: bytes) -> bytes:
        nonce = os.urandom(NONCE_BYTES)
        return nonce + self._aead.encrypt(nonce, clear, aad)

    def unseal(self, sealed: bytes, aad: bytes) -> bytes:
        if len(sealed) <= NONCE_BYTES:
            raise SealBroken(f"Sceau de {len(sealed)} octets : trop court pour en être un")
        try:
            return self._aead.decrypt(sealed[:NONCE_BYTES], sealed[NONCE_BYTES:], aad)
        except InvalidTag as exc:
            raise SealBroken(
                f"Sceau {self._key_id!r} : il ne s'ouvre pas avec cette clé — octets ou "
                "enveloppe altérés"
            ) from exc

    def __repr__(self) -> str:
        return f"AesGcmCipher(key_id={self._key_id!r})"


class SecretKeyring:
    """``Keyring`` adossé aux secrets des clients.

    Les clés sont résolues au premier besoin et gardées : un client garde les
    mêmes objets tout du long, et l'empreinte ne se recalcule pas à chaque
    événement.
    """

    def __init__(self, secrets: SecretProvider, names: Sequence[str]) -> None:
        if not names:
            raise SealError("Trousseau sans nom de secret : rien ne peut être scellé")
        self._secrets = secrets
        self._names = tuple(names)
        self._rings: dict[TenantId, tuple[Cipher, ...]] = {}

    @property
    def names(self) -> tuple[str, ...]:
        """Secrets qui portent les clés : le premier ferme, tous ouvrent."""
        return self._names

    def ciphers(self, tenant_id: TenantId) -> Sequence[Cipher]:
        ring = self._rings.get(tenant_id)
        if ring is None:
            ring = self._resolve(tenant_id)
            self._rings[tenant_id] = ring
        return ring

    def _resolve(self, tenant_id: TenantId) -> tuple[Cipher, ...]:
        table: Mapping[str, str] = self._secrets.secrets(tenant_id)
        found = [(name, table[name]) for name in self._names if table.get(name, "").strip()]
        if not found:
            declared = ", ".join(repr(name) for name in self._names)
            raise MissingKey(
                f"Client {tenant_id!r} : aucune clé de sceau — le secret {declared} est absent "
                "ou vide. Son journal reste illisible et ses runs sont refusés : c'est l'effet "
                "voulu après un effacement de clé, et si la clé n'était pas censée disparaître, "
                "c'est elle qu'il faut remettre"
            )
        return tuple(
            AesGcmCipher(decode_key(value, what=f"Client {tenant_id!r}, secret {name!r}"))
            for name, value in found
        )

    def __repr__(self) -> str:
        return f"SecretKeyring(names={self._names!r})"
