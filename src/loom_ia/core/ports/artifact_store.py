# SPDX-License-Identifier: Apache-2.0
"""Port du stockage d'artefacts (G2, #16).

Les fichiers (pièces jointes, images produites par les outils, gros
résultats) vivent hors du journal, qui n'en garde que l'URI. L'URI est
calculée par le noyau (``artifact_uri``) : le stockage ne fait que ranger et
rendre des octets.
"""

from typing import Protocol


class ArtifactNotFound(KeyError):
    """Aucun artefact à cette URI."""

    def __init__(self, uri: str) -> None:
        super().__init__(f"Artefact introuvable : {uri}")
        self.uri = uri


class ArtifactStore(Protocol):
    async def put(self, uri: str, data: bytes) -> None:
        """Range les octets à cette URI ; réécrire le même contenu ne change rien."""
        ...

    async def get(self, uri: str) -> bytes:
        """Octets de l'artefact ; lève ``ArtifactNotFound``."""
        ...

    async def aclose(self) -> None: ...
