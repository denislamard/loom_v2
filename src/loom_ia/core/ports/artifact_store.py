# SPDX-License-Identifier: Apache-2.0
"""Port du stockage d'artefacts (fonction G2, point 16).

Les fichiers (pièces jointes, images produites par les outils, gros
résultats) vivent hors du journal, qui n'en garde que l'URI. L'URI est
calculée par le noyau (``artifact_uri``) : le stockage ne fait que ranger et
rendre des octets.

Implémentations fournies (``adapters/artifacts``) : en mémoire, dossier local,
et une enveloppe qui scelle les octets avec la clé du client
(``storage.encryption``). Le stockage se déclare sous ``storage.artifacts``.
"""

from typing import Protocol

from loom_ia.core.model.base import Unknown
from loom_ia.core.model.ids import SessionId, TenantId


class ArtifactNotFound(Unknown):
    """Aucun artefact à cette URI ; l'API REST la rend en 404.

    Args:
        uri: L'URI demandée.

    Attributes:
        uri: La même URI. Le message dit « Artefact introuvable : <uri> ».
    """

    def __init__(self, uri: str) -> None:
        super().__init__(f"Artefact introuvable : {uri}")
        self.uri = uri


class ArtifactStore(Protocol):
    """Stockage des fichiers d'un run, hors du journal, adressés par URI."""

    async def put(self, uri: str, data: bytes) -> None:
        """Range des octets à une URI ; réécrire le même contenu ne change rien.

        Args:
            uri: URI calculée par le noyau (``artifact_uri``), qui contient le
                client et la session.
            data: Les octets du fichier.
        """
        ...

    async def get(self, uri: str) -> bytes:
        """Rend les octets d'un artefact.

        Args:
            uri: URI de l'artefact.

        Returns:
            Ses octets, en clair.

        Raises:
            ArtifactNotFound: Aucun artefact à cette URI.
        """
        ...

    async def delete(self, tenant_id: TenantId, session_id: SessionId) -> int:
        """Supprime les fichiers d'une session (RGPD, conception §11.5).

        Args:
            tenant_id: Client de la session.
            session_id: La session.

        Returns:
            Le nombre de fichiers supprimés.
        """
        ...

    async def aclose(self) -> None:
        """Libère les ressources du stockage."""
        ...
