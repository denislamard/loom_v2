# SPDX-License-Identifier: Apache-2.0
"""Fichiers scellés avec la clé de leur client (point 30, conception §11.5).

Le journal ne porte pas les fichiers : une pièce jointe, une image produite
par un outil, un gros résultat déporté (``offload_over``) vont au stockage
d'artefacts, et le journal n'en garde que l'URI. Sceller le journal sans
sceller ces fichiers laisserait donc en clair le plus volumineux de ce qu'un
run manipule — et ferait du *crypto-shredding* un mot qui ne tient pas.

Cet enrobage ferme les octets à ``put`` et les ouvre à ``get``. Le client
vient de l'URI, qui le nomme (``artifact://<client>/<session>/<fichier>``),
et c'est l'URI entière qui est authentifiée : un fichier recopié sous une
autre URI ne s'ouvre pas.

Un fichier rangé **avant** le sceau se relit tel quel : les octets scellés
commencent par une marque que les siens n'ont pas. C'est la même tolérance
que pour une ligne de journal en clair, et pour la même raison — déclarer le
sceau ne réécrit pas ce qui existe.

L'URI est adressée par le contenu (un condensé des octets en clair, calculé
par le noyau) : le sceau ne la change pas, et deux fermetures des mêmes
octets donnent des octets différents, puisque chacune tire son nonce. Mais
elles ont la même taille, et le stockage sur disque laisse en place un fichier
de même taille : ranger deux fois le même fichier y garde la première
fermeture, avec sa clé. Le stockage en mémoire, lui, remplace. Sur disque,
l'écriture passe par un fichier temporaire renommé : un lecteur voit l'un ou
l'autre, jamais un mélange.
"""

from typing import Final

from loom_ia.core.model import ArtifactLocation, SessionId, TenantId
from loom_ia.core.ports import ArtifactStore, Cipher, Keyring, MissingKey, SealBroken

MAGIC: Final = b"LOOMSEAL1"
"""Marque des octets scellés : un fichier en clair ne commence pas par là."""
KEY_ID_BYTES: Final = 12
"""Empreinte de la clé, en ASCII, juste après la marque : elle dit laquelle a
fermé ce fichier, donc laquelle manque quand il ne s'ouvre pas.
"""
HEADER_BYTES: Final = len(MAGIC) + KEY_ID_BYTES
"""Taille de l'en-tête d'un fichier scellé : la marque, puis l'empreinte de la clé."""


class SealingArtifactStore:
    """Enrobe un stockage de fichiers et scelle ce qui y passe."""

    def __init__(self, inner: ArtifactStore, keyring: Keyring) -> None:
        self._inner = inner
        self._keyring = keyring

    @property
    def inner(self) -> ArtifactStore:
        """Le stockage enrobé."""
        return self._inner

    async def put(self, uri: str, data: bytes) -> None:
        """Scelle les octets avec la clé courante du client de l'URI, puis les range.

        Le fichier rangé commence par la marque ``LOOMSEAL1`` et l'empreinte de la
        clé ; l'URI est authentifiée avec le contenu.
        """
        cipher = self._sealer(_tenant(uri))
        closed = cipher.seal(data, uri.encode())
        await self._inner.put(uri, MAGIC + cipher.key_id.encode() + closed)

    async def get(self, uri: str) -> bytes:
        """Rend les octets d'un artefact, ouverts avec la clé qui les a scellés.

        Un fichier rangé avant que le sceau soit déclaré est rendu tel quel.

        Raises:
            ArtifactNotFound: L'artefact n'est pas dans le stockage.
            MissingKey: La clé qui l'a scellé n'est pas dans le trousseau du client.
            SealBroken: Octets altérés, ou fichier déplacé sous une autre URI.
        """
        stored = await self._inner.get(uri)
        if not stored.startswith(MAGIC):
            # Fichier rangé avant que le sceau soit déclaré.
            return stored
        key_id = stored[len(MAGIC) : HEADER_BYTES].decode(errors="replace")
        cipher = self._opener(_tenant(uri), key_id)
        try:
            return cipher.unseal(stored[HEADER_BYTES:], uri.encode())
        except SealBroken as exc:
            raise SealBroken(f"{uri} : {exc}") from exc

    async def delete(self, tenant_id: TenantId, session_id: SessionId) -> int:
        """Supprime les fichiers d'une session dans le stockage enrobé (RGPD)."""
        return await self._inner.delete(tenant_id, session_id)

    async def aclose(self) -> None:
        """Ferme le stockage enrobé."""
        await self._inner.aclose()

    def _sealer(self, tenant_id: TenantId) -> Cipher:
        return self._keyring.ciphers(tenant_id)[0]

    def _opener(self, tenant_id: TenantId, key_id: str) -> Cipher:
        try:
            ring = self._keyring.ciphers(tenant_id)
        except MissingKey as exc:
            raise MissingKey(f"{exc} ; ce fichier est scellé par {key_id!r}") from exc
        for cipher in ring:
            if cipher.key_id == key_id:
                return cipher
        raise MissingKey(
            f"Client {tenant_id!r} : la clé {key_id!r} qui a scellé ce fichier n'est pas dans "
            "son trousseau — effacée (le contenu est perdu, c'est l'effet voulu) ou pas encore "
            "déclarée"
        )

    def __repr__(self) -> str:
        return f"SealingArtifactStore({self._inner!r})"


def _tenant(uri: str) -> TenantId:
    """Client nommé par l'URI ; ``ValueError`` si elle n'en est pas une."""
    return TenantId(ArtifactLocation.parse(uri).tenant)
