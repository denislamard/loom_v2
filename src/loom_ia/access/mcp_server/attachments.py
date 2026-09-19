# SPDX-License-Identifier: Apache-2.0
"""Pièces jointes d'un appel MCP (G1).

L'outil d'un agent reçoit ses images dans l'argument ``attachments``, sous
deux formes calquées sur les contenus MCP :

- ``{"type": "image", "data": "<base64>", "mimeType": "image/png"}`` : les
  octets dans l'appel ;
- ``{"type": "resource_link", "uri": "…"}`` : un lien, vers

  - ``artifact://…`` : un fichier déjà rangé par loom pour le même client, par
    exemple la pièce jointe d'un appel précédent (listée dans ``artifacts`` du
    résultat) ;
  - ``file:///…`` : un fichier de la machine de loom, lu seulement sous
    ``server.mcp.file_roots`` (aucun dossier par défaut : refusé). C'est la
    forme utile depuis Claude Code ou Claude Desktop en local : le modèle du
    client connaît le chemin d'un fichier, pas ses octets en base64.

``name`` et ``mimeType`` sont facultatifs dans les deux formes. Le contenu est
contrôlé ensuite par le moteur (signature, type accepté) ; la taille l'est
dès la lecture.
"""

import asyncio
import base64
import binascii
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final, cast
from urllib.parse import urlsplit
from urllib.request import url2pathname

from loom_ia.core.model import (
    ARTIFACT_SCHEME,
    ArtifactLocation,
    Attachment,
    AttachmentError,
    AttachmentPolicy,
    TenantId,
)
from loom_ia.core.ports import ArtifactNotFound, ArtifactStore

FILE_SCHEME: Final = "file://"

_ITEM_COMMON: Final[dict[str, Any]] = {
    "name": {"type": "string", "description": "Nom du fichier"},
    "mimeType": {"type": "string", "description": "Type annoncé, vérifié sur le contenu"},
}

ATTACHMENTS_INPUT: Final[dict[str, Any]] = {
    "type": "array",
    "description": (
        "Images jointes : en base64 (type image), ou par lien (type resource_link) vers "
        "un fichier déjà rangé par loom (artifact://…) ou un fichier local autorisé (file:///…)"
    ),
    "items": {
        "oneOf": [
            {
                "type": "object",
                "properties": {
                    "type": {"const": "image"},
                    "data": {"type": "string", "description": "Octets de l'image en base64"},
                    **_ITEM_COMMON,
                },
                "required": ["type", "data"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {
                    "type": {"const": "resource_link"},
                    "uri": {"type": "string", "description": "artifact://… ou file:///…"},
                    **_ITEM_COMMON,
                },
                "required": ["type", "uri"],
                "additionalProperties": False,
            },
        ]
    },
}


class AttachmentReader:
    """Lit les pièces jointes d'un appel, pour un client et des dossiers donnés."""

    def __init__(
        self,
        artifacts: ArtifactStore,
        policy: AttachmentPolicy,
        *,
        tenant: TenantId,
        roots: Sequence[Path] = (),
    ) -> None:
        self._artifacts = artifacts
        self._policy = policy
        self._tenant = tenant
        self._roots = tuple(roots)

    async def read(self, raw: object) -> list[Attachment]:
        """Pièces jointes de l'argument ``attachments`` (absent : aucune)."""
        if raw is None:
            return []
        if not isinstance(raw, list):
            raise AttachmentError("attachments : une liste est attendue")
        items = cast(list[object], raw)
        self._policy.check_count(len(items))
        return [await self._item(item, n) for n, item in enumerate(items, start=1)]

    async def _item(self, raw: object, n: int) -> Attachment:
        if not isinstance(raw, dict):
            raise AttachmentError(f"pièce jointe {n} : un objet est attendu")
        item = cast(dict[str, object], raw)
        name = _text(item, "name")
        media_type = _text(item, "mimeType")
        match item.get("type"):
            case "image":
                data = _decoded(_text(item, "data") or "", name or f"pièce jointe {n}")
                return Attachment(data=data, media_type=media_type, name=name)
            case "resource_link":
                uri = _text(item, "uri") or ""
                data, found = await self._linked(uri)
                return Attachment(data=data, media_type=media_type, name=name or found)
            case other:
                raise AttachmentError(
                    f"pièce jointe {n} : type {other!r} inconnu (image ou resource_link)"
                )

    async def _linked(self, uri: str) -> tuple[bytes, str]:
        """Octets et nom du fichier désigné par un lien."""
        if uri.startswith(ARTIFACT_SCHEME):
            return await self._artifact(uri)
        if uri.startswith(FILE_SCHEME):
            return await asyncio.to_thread(self._file, uri)
        scheme = uri.split(":", 1)[0] if ":" in uri else uri
        raise AttachmentError(
            f"{uri} : lien {scheme!r} non pris en charge (artifact://… ou file:///…)"
        )

    async def _artifact(self, uri: str) -> tuple[bytes, str]:
        try:
            location = ArtifactLocation.parse(uri)
        except ValueError as exc:
            raise AttachmentError(str(exc)) from exc
        # Un fichier d'un autre client est introuvable, comme un fichier absent.
        missing = AttachmentError(f"{uri} : artefact introuvable")
        if location.tenant != self._tenant:
            raise missing
        try:
            data = await self._artifacts.get(uri)
        except ArtifactNotFound:
            raise missing from None
        self._policy.check_size(location.name, len(data))
        return data, location.name

    def _file(self, uri: str) -> tuple[bytes, str]:
        """Fichier local sous un dossier autorisé, lu jusqu'à la taille limite."""
        if not self._roots:
            raise AttachmentError(
                f"{uri} : liens file:// refusés, aucun dossier autorisé (server.mcp.file_roots)"
            )
        parts = urlsplit(uri)
        if parts.netloc not in ("", "localhost"):
            raise AttachmentError(f"{uri} : hôte {parts.netloc!r} non pris en charge")
        path = Path(url2pathname(parts.path))
        roots = [root.resolve() for root in self._roots]
        outside = AttachmentError(f"{uri} : hors des dossiers autorisés")
        # Dans les dossiers avant de toucher au fichier : rien n'est dit de ce qui est ailleurs.
        if not path.is_absolute() or not _inside(Path(os.path.normpath(path)), roots):
            raise outside
        try:
            real = path.resolve(strict=True)
        except OSError:
            raise AttachmentError(f"{uri} : fichier introuvable") from None
        # Un lien symbolique ne sort pas des dossiers.
        if not _inside(real, roots):
            raise outside
        if not real.is_file():
            raise AttachmentError(f"{uri} : n'est pas un fichier")
        self._policy.check_size(real.name, real.stat().st_size)
        with real.open("rb") as file:
            data = file.read(self._policy.max_bytes + 1)
        self._policy.check_size(real.name, len(data))
        return data, real.name


def _text(item: dict[str, object], key: str) -> str | None:
    value = item.get(key)
    return value if isinstance(value, str) else None


def _decoded(data: str, label: str) -> bytes:
    try:
        return base64.b64decode(data, validate=True)
    except binascii.Error, ValueError:
        raise AttachmentError(f"{label} : base64 invalide") from None


def _inside(path: Path, roots: Sequence[Path]) -> bool:
    return any(path.is_relative_to(root) for root in roots)
