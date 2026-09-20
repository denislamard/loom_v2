# SPDX-License-Identifier: Apache-2.0
"""Stockage d'artefacts dans un dossier local : mode librairie et dev (G2).

Un fichier par artefact : ``<racine>/<client>/<session>/<sha256>.<ext>``,
chemin tiré de l'URI (segments encodés, jamais « .. »). Supprimer une
session revient à supprimer son dossier.

L'URI est adressée par le contenu : un fichier déjà présent n'est pas
réécrit. L'écriture passe par un fichier temporaire renommé, pour qu'un
lecteur ne voie jamais un fichier à moitié écrit.
"""

import asyncio
import os
import shutil
import tempfile
from pathlib import Path

from loom_ia.core.model import ArtifactLocation, SessionId, TenantId
from loom_ia.core.ports import ArtifactNotFound


class LocalArtifactStore:
    """Artefacts rangés sous un dossier."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root)

    def path(self, uri: str) -> Path:
        """Fichier d'un artefact ; lève ``ValueError`` si l'URI est invalide."""
        return self.root / ArtifactLocation.parse(uri).relative_path()

    async def put(self, uri: str, data: bytes) -> None:
        await asyncio.to_thread(_write, self.path(uri), data)

    async def get(self, uri: str) -> bytes:
        path = self.path(uri)
        try:
            return await asyncio.to_thread(path.read_bytes)
        except FileNotFoundError:
            raise ArtifactNotFound(uri) from None

    def session_path(self, tenant_id: TenantId, session_id: SessionId) -> Path:
        """Dossier d'une session ; lève ``ValueError`` si les segments sont invalides."""
        location = ArtifactLocation(tenant=tenant_id, session=session_id, name="-")
        return (self.root / location.relative_path()).parent

    async def delete(self, tenant_id: TenantId, session_id: SessionId) -> int:
        return await asyncio.to_thread(_remove, self.session_path(tenant_id, session_id))

    async def aclose(self) -> None:
        pass

    def __repr__(self) -> str:
        return f"LocalArtifactStore({str(self.root)!r})"


def _remove(directory: Path) -> int:
    """Supprime le dossier d'une session et rend le nombre de fichiers retirés."""
    if not directory.is_dir():
        return 0
    count = sum(1 for entry in directory.iterdir() if entry.is_file())
    shutil.rmtree(directory)
    return count


def _write(path: Path, data: bytes) -> None:
    if path.is_file() and path.stat().st_size == len(data):
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
