# SPDX-License-Identifier: Apache-2.0
"""Stockage d'artefacts en mémoire : tests et journaux ``memory``."""

from loom_ia.core.model import ArtifactLocation, SessionId, TenantId
from loom_ia.core.ports import ArtifactNotFound


class InMemoryArtifactStore:
    """Artefacts gardés dans un dictionnaire, perdus à la fin du process."""

    def __init__(self) -> None:
        self._files: dict[str, bytes] = {}

    async def put(self, uri: str, data: bytes) -> None:
        """Range des octets à une URI (``ArtifactStore.put``). ``ValueError`` si l'URI est invalide."""
        ArtifactLocation.parse(uri)
        self._files[uri] = data

    async def get(self, uri: str) -> bytes:
        """Rend les octets d'un artefact ; ``ArtifactNotFound`` s'il n'y est pas (``ArtifactStore.get``)."""
        try:
            return self._files[uri]
        except KeyError:
            raise ArtifactNotFound(uri) from None

    async def delete(self, tenant_id: TenantId, session_id: SessionId) -> int:
        """Supprime les fichiers d'une session (RGPD) ; rend le nombre de fichiers retirés."""
        doomed = [uri for uri in self._files if (location := ArtifactLocation.parse(uri)).tenant == tenant_id and location.session == session_id]
        for uri in doomed:
            del self._files[uri]
        return len(doomed)

    async def aclose(self) -> None:
        """Rien à fermer."""
        pass

    def __len__(self) -> int:
        return len(self._files)

    def __repr__(self) -> str:
        return f"InMemoryArtifactStore({len(self._files)} fichier(s))"
