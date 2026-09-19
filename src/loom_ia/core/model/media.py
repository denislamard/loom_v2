# SPDX-License-Identifier: Apache-2.0
"""Fichiers : types reconnus, pièces jointes, références d'artefacts (G1, G2, #14).

Le type d'une pièce jointe se déduit de sa signature binaire (acquis de V1),
jamais de son nom : un type déclaré qui ne correspond pas est refusé.

Un artefact est désigné par une URI adressée par son contenu :

    artifact://<client>/<session>/<sha256>.<ext>

Un même fichier n'est donc stocké qu'une fois par session, le rejeu retrouve
les mêmes URI, et la suppression d'une session (RGPD) touche un seul
dossier. Client et session sont encodés pour tenir dans une URI et un chemin.
"""

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, Self
from urllib.parse import quote, unquote

from pydantic import Field, NonNegativeInt, PositiveInt, model_validator

from loom_ia.core.model.base import DomainModel

ARTIFACT_SCHEME: Final = "artifact://"

type ArtifactOrigin = Literal["attachment", "tool_output", "offload"]

# Début du message utilisateur qui porte, après les résultats d'un tour, les
# images que le modèle n'accepte pas dans un résultat d'outil (#15).
MOVED_IMAGES: Final = "Images renvoyées par les outils ci-dessus :"
# Nom court d'un format d'image, tel que ``image_formats`` des modèles le déclare.
type ImageFormat = Literal["jpeg", "png", "gif", "webp"]

# Types d'image acceptés partout (jalon J2), et leur nom court.
IMAGE_TYPES: Final[Mapping[str, ImageFormat]] = {
    "image/jpeg": "jpeg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
}
_EXTENSIONS: Final[Mapping[str, str]] = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
    "text/plain": "txt",
    "application/json": "json",
    "application/pdf": "pdf",
    "audio/wav": "wav",
    "audio/mpeg": "mp3",
}


def sniff(data: bytes) -> str | None:
    """Type MIME d'une image d'après sa signature, ou None s'il n'est pas reconnu."""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def is_image(media_type: str) -> bool:
    return media_type in IMAGE_TYPES


def extension(media_type: str) -> str:
    return _EXTENSIONS.get(media_type, "bin")


def _component(value: str) -> str:
    """Segment d'URI et de chemin sûr : encodé, jamais « . » ni « .. »."""
    encoded = quote(value, safe="-_")
    return encoded.replace(".", "%2E")


def artifact_uri(tenant: str, session: str, data: bytes, media_type: str) -> str:
    """URI adressée par le contenu, dans l'espace du client et de la session."""
    digest = hashlib.sha256(data).hexdigest()
    return (
        f"{ARTIFACT_SCHEME}{_component(tenant)}/{_component(session)}/"
        f"{digest}.{extension(media_type)}"
    )


@dataclass(frozen=True, slots=True)
class ArtifactLocation:
    """Parties d'une URI d'artefact, décodées."""

    tenant: str
    session: str
    name: str

    @classmethod
    def parse(cls, uri: str) -> Self:
        if not uri.startswith(ARTIFACT_SCHEME):
            raise ValueError(f"URI d'artefact invalide : {uri!r}")
        parts = uri.removeprefix(ARTIFACT_SCHEME).split("/")
        if len(parts) != 3 or not all(parts) or parts[2].startswith("."):
            raise ValueError(f"URI d'artefact invalide : {uri!r}")
        tenant, session, name = parts
        if "%" in name or "/" in unquote(name):
            raise ValueError(f"URI d'artefact invalide : {uri!r}")
        return cls(tenant=unquote(tenant), session=unquote(session), name=name)

    def relative_path(self) -> Path:
        """Chemin relatif sûr (segments encodés), sous la racine d'un stockage."""
        return Path(_component(self.tenant)) / _component(self.session) / self.name


class ArtifactRecord(DomainModel):
    """Un artefact du run, tel que le journal le décrit."""

    uri: str
    media_type: str
    size: NonNegativeInt
    name: str | None = None
    origin: ArtifactOrigin
    # Appel d'outil qui l'a produit (sortie ou déport).
    call_id: str | None = None


class AttachmentPolicy(DomainModel):
    """Pièces jointes acceptées à l'entrée d'un run (G1)."""

    # 5 Mio : la limite d'une image chez Anthropic.
    max_bytes: PositiveInt = 5 * 1024 * 1024
    # Types MIME acceptés, parmi les images reconnues.
    types: tuple[str, ...] = Field(default=tuple(IMAGE_TYPES), min_length=1)

    @model_validator(mode="after")
    def _check_types(self) -> Self:
        unknown = [media for media in self.types if media not in IMAGE_TYPES]
        if unknown:
            raise ValueError(
                f"types : {', '.join(unknown)} non pris en charge "
                f"(images reconnues : {', '.join(IMAGE_TYPES)})"
            )
        return self


class AttachmentError(ValueError):
    """Pièce jointe refusée : format non reconnu ou non accepté, taille, type incohérent."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Attachment:
    """Fichier joint à la demande de l'utilisateur."""

    data: bytes
    # Type annoncé par l'appelant ; vérifié contre la signature.
    media_type: str | None = None
    name: str | None = None

    @classmethod
    def from_path(cls, path: str | Path, *, media_type: str | None = None) -> Self:
        file = Path(path)
        return cls(data=file.read_bytes(), media_type=media_type, name=file.name)

    def checked(self, policy: AttachmentPolicy) -> str:
        """Type MIME reconnu, après les contrôles de ``policy`` ; lève ``AttachmentError``."""
        label = self.name or "pièce jointe"
        if not self.data:
            raise AttachmentError(f"{label} : fichier vide")
        if len(self.data) > policy.max_bytes:
            raise AttachmentError(
                f"{label} : {len(self.data)} octets, au-delà de la limite de {policy.max_bytes}"
            )
        detected = sniff(self.data)
        if detected is None:
            raise AttachmentError(
                f"{label} : format non reconnu (acceptés : {', '.join(policy.types)})"
            )
        if self.media_type is not None and self.media_type != detected:
            raise AttachmentError(
                f"{label} : annoncé {self.media_type}, mais le contenu est {detected}"
            )
        if detected not in policy.types:
            raise AttachmentError(
                f"{label} : {detected} non accepté (acceptés : {', '.join(policy.types)})"
            )
        return detected
