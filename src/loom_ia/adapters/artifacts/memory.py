# SPDX-License-Identifier: Apache-2.0
"""Stockage d'artefacts en mémoire : tests et journaux ``memory``."""

from loom_ia.core.model import ArtifactLocation
from loom_ia.core.ports import ArtifactNotFound


class InMemoryArtifactStore:
    """Artefacts gardés dans un dictionnaire, perdus à la fin du process."""

    def __init__(self) -> None:
        self._files: dict[str, bytes] = {}

    async def put(self, uri: str, data: bytes) -> None:
        ArtifactLocation.parse(uri)
        self._files[uri] = data

    async def get(self, uri: str) -> bytes:
        try:
            return self._files[uri]
        except KeyError:
            raise ArtifactNotFound(uri) from None

    async def aclose(self) -> None:
        pass

    def __len__(self) -> int:
        return len(self._files)

    def __repr__(self) -> str:
        return f"InMemoryArtifactStore({len(self._files)} fichier(s))"
