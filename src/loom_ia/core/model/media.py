# SPDX-License-Identifier: Apache-2.0
"""Fichiers : types reconnus, pièces jointes, références d'artefacts (fonctions G1 et G2, point 14).

Le type d'une pièce jointe se déduit de sa signature binaire (acquis de V1),
jamais de son nom : un type déclaré qui ne correspond pas est refusé.

Un artefact est désigné par une URI adressée par son contenu :

    artifact://<client>/<session>/<sha256>.<ext>

Un même fichier n'est donc stocké qu'une fois par session, le rejeu retrouve
les mêmes URI, et la suppression d'une session (RGPD) touche un seul
dossier. Client et session sont encodés pour tenir dans une URI et un chemin.

Les pièces jointes acceptées à l'entrée d'un run se règlent sous
``execution.attachments`` (``AttachmentPolicy``) : par défaut, des images
JPEG, PNG, GIF ou WebP, 5 Mio par fichier, 10 fichiers au plus.
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
"""Préfixe des URI d'artefacts."""

type ArtifactOrigin = Literal["attachment", "tool_output", "offload"]
"""Origine d'un artefact : pièce jointe de la demande, fichier produit par un outil, ou résultat déporté."""

type KeepImages = Literal["last_run", "all"]
"""Images des tours précédents d'une session dans une requête (``sessions.media.keep_images``, fonction F4).

``last_run`` : seules celles du run en cours partent en octets ; ``all``
(défaut) : toutes.
"""

MOVED_IMAGES: Final = "Images renvoyées par les outils ci-dessus :"
"""Début du message utilisateur qui porte, après les résultats d'un tour, les images que le modèle n'accepte pas dans un résultat d'outil (point 15)."""

type ImageFormat = Literal["jpeg", "png", "gif", "webp"]
"""Nom court d'un format d'image, tel que ``image_formats`` des modèles le déclare."""

IMAGE_TYPES: Final[Mapping[str, ImageFormat]] = {
    "image/jpeg": "jpeg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
}
"""Types d'image acceptés partout (jalon J2), et leur nom court."""
_EXTENSIONS: Final[Mapping[str, str]] = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/svg+xml": "svg",
    "text/plain": "txt",
    "text/csv": "csv",
    "text/markdown": "md",
    "text/html": "html",
    "text/xml": "xml",
    "application/xml": "xml",
    "application/json": "json",
    "application/pdf": "pdf",
    "application/zip": "zip",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "audio/wav": "wav",
    "audio/mpeg": "mp3",
}


def sniff(data: bytes) -> str | None:
    """Type MIME d'une image d'après sa signature binaire, jamais d'après son nom.

    Args:
        data: Les octets du fichier.

    Returns:
        ``image/jpeg``, ``image/png``, ``image/gif`` ou ``image/webp`` ;
        ``None`` pour une autre signature.
    """
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
    """Vrai pour un type d'image reconnu (JPEG, PNG, GIF, WebP)."""
    return media_type in IMAGE_TYPES


def extension(media_type: str) -> str:
    """Extension d'un fichier de ce type, sans le point ; ``bin`` pour un type inconnu.

    Les paramètres du type (``text/csv; charset=utf-8``) et la casse ne comptent
    pas. Types connus : images, SVG, texte, CSV, Markdown, HTML, XML, JSON, PDF,
    ZIP, XLSX, DOCX, PPTX, WAV, MP3.
    """
    return _EXTENSIONS.get(media_type.split(";", 1)[0].strip().lower(), "bin")


# Type d'un nom de fichier, d'après son extension : la table ci-dessus lue à
# l'envers (le premier type d'une extension l'emporte), et quelques variantes.
_BY_EXTENSION: Final[Mapping[str, str]] = {
    **{ext: media for media, ext in reversed(_EXTENSIONS.items())},
    "jpeg": "image/jpeg",
    "htm": "text/html",
}
OCTETS: Final = "application/octet-stream"
"""Type d'un fichier dont on ne sait rien."""


def media_type_of(name: str, data: bytes) -> str:
    """Type d'un fichier produit par du code qu'on ne connaît pas.

    La signature d'une image l'emporte sur le nom. Un nom d'image que la
    signature ne confirme pas donne ``application/octet-stream`` : sinon, un
    « graphe.png » qui n'en est pas un partirait comme image vers un modèle
    qui les voit, et le fournisseur refuserait la requête. Hors images, le
    type vient de l'extension (table de loom, pas celle du système : le même
    fichier a le même type partout).

    Args:
        name: Nom du fichier.
        data: Ses octets.

    Returns:
        Le type MIME ; ``application/octet-stream`` pour une extension
        inconnue ou une fausse image.
    """
    if (sniffed := sniff(data)) is not None:
        return sniffed
    media = _BY_EXTENSION.get(Path(name).suffix.removeprefix(".").lower(), OCTETS)
    return OCTETS if is_image(media) else media


def _component(value: str) -> str:
    """Segment d'URI et de chemin sûr : encodé, jamais « . » ni « .. »."""
    encoded = quote(value, safe="-_")
    return encoded.replace(".", "%2E")


def artifact_uri(tenant: str, session: str, data: bytes, media_type: str) -> str:
    """URI d'un artefact, adressée par son contenu, dans l'espace du client et de la session.

    Args:
        tenant: Le client.
        session: La session.
        data: Les octets du fichier.
        media_type: Son type, qui donne l'extension.

    Returns:
        ``artifact://<client>/<session>/<sha256>.<ext>``, client et session
        encodés.
    """
    digest = hashlib.sha256(data).hexdigest()
    return f"{ARTIFACT_SCHEME}{_component(tenant)}/{_component(session)}/{digest}.{extension(media_type)}"


@dataclass(frozen=True, slots=True)
class ArtifactLocation:
    """Parties d'une URI d'artefact, décodées.

    Attributes:
        tenant: Le client.
        session: La session.
        name: Le nom du fichier : ``<sha256>.<ext>``.
    """

    tenant: str
    session: str
    name: str

    @classmethod
    def parse(cls, uri: str) -> Self:
        """Découpe une URI d'artefact.

        Args:
            uri: L'URI, ``artifact://<client>/<session>/<nom>``.

        Returns:
            Ses parties, client et session décodés.

        Raises:
            ValueError: L'URI n'a pas cette forme, ou son nom tente de sortir
                de son dossier.
        """
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
        """Chemin relatif sûr (segments encodés), sous la racine d'un stockage.

        Lève ``ValueError`` si le client ou la session est vide : un segment vide
        s'effacerait du chemin, et le dossier d'une session deviendrait celui de
        son client, voire la racine.
        """
        for kind, value in (("client", self.tenant), ("session", self.session)):
            if not value:
                raise ValueError(f"Identifiant de {kind} inutilisable dans un chemin d'artefact : {value!r}")
        return Path(_component(self.tenant)) / _component(self.session) / self.name


class ArtifactRecord(DomainModel):
    """Un artefact du run, tel que le journal le décrit (``artifact.stored``).

    C'est ce que listent ``RunResult.produced``, ``loom sessions files`` et
    ``GET /v1/sessions/{id}/files``.

    Attributes:
        uri: Référence du fichier.
        media_type: Type MIME.
        size: Taille, en octets.
        name: Nom du fichier.
        origin: ``attachment``, ``tool_output`` ou ``offload``.
        call_id: Appel d'outil qui l'a produit (sortie ou déport).
    """

    uri: str
    media_type: str
    size: NonNegativeInt
    name: str | None = None
    origin: ArtifactOrigin
    call_id: str | None = None


class AttachmentPolicy(DomainModel):
    """Pièces jointes acceptées à l'entrée d'un run (``execution.attachments``, fonction G1).

    Le format se reconnaît à la signature binaire ; PDF, audio et autres
    fichiers ne sont pas encore pris en charge.

    Attributes:
        max_bytes: Taille maximale d'une pièce jointe, en octets. Défaut :
            5 Mio, la limite d'une image chez Anthropic.
        types: Types MIME acceptés, parmi les images reconnues. Défaut :
            JPEG, PNG, GIF, WebP.
        max_files: Nombre de pièces jointes d'un run ; borne aussi la taille
            d'un envoi REST. Défaut : 10.

    Raises:
        ValueError: Un type de ``types`` n'est pas une image reconnue.
    """

    max_bytes: PositiveInt = 5 * 1024 * 1024
    types: tuple[str, ...] = Field(default=tuple(IMAGE_TYPES), min_length=1)
    max_files: PositiveInt = 10

    @model_validator(mode="after")
    def _check_types(self) -> Self:
        """Refuse un type qui n'est pas une image reconnue."""
        unknown = [media for media in self.types if media not in IMAGE_TYPES]
        if unknown:
            raise ValueError(f"types : {', '.join(unknown)} non pris en charge (images reconnues : {', '.join(IMAGE_TYPES)})")
        return self

    def check_count(self, count: int) -> None:
        """Lève ``AttachmentError`` au-delà de ``max_files`` pièces jointes."""
        if count > self.max_files:
            raise AttachmentError(f"{count} pièces jointes, au-delà de la limite de {self.max_files}")

    def check_size(self, label: str, size: int) -> None:
        """Lève ``AttachmentError`` si ``size`` dépasse ``max_bytes``."""
        if size > self.max_bytes:
            raise AttachmentError(f"{label} : {size} octets, au-delà de la limite de {self.max_bytes}")


class AttachmentError(ValueError):
    """Pièce jointe refusée : format non reconnu ou non accepté, taille, type incohérent."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Attachment:
    """Fichier joint à la demande de l'utilisateur (``loom run --attach``, ``run(attachments=…)``, envoi multipart).

    Attributes:
        data: Les octets.
        media_type: Type annoncé par l'appelant ; vérifié contre la
            signature.
        name: Nom du fichier, tel que le modèle le verra.
    """

    data: bytes
    media_type: str | None = None
    name: str | None = None

    @classmethod
    def from_path(cls, path: str | Path, *, media_type: str | None = None) -> Self:
        """Pièce jointe lue depuis un fichier, sous son nom.

        Args:
            path: Chemin du fichier.
            media_type: Type annoncé ; ``None`` : déduit de la signature.

        Returns:
            La pièce jointe.
        """
        file = Path(path)
        return cls(data=file.read_bytes(), media_type=media_type, name=file.name)

    def checked(self, policy: AttachmentPolicy) -> str:
        """Contrôle la pièce jointe et rend son type reconnu.

        Args:
            policy: Ce que le run accepte.

        Returns:
            Le type MIME reconnu à la signature.

        Raises:
            AttachmentError: Fichier vide, trop gros, format non reconnu,
                type annoncé qui ne correspond pas au contenu, ou type non
                accepté.
        """
        label = self.name or "pièce jointe"
        if not self.data:
            raise AttachmentError(f"{label} : fichier vide")
        policy.check_size(label, len(self.data))
        detected = sniff(self.data)
        if detected is None:
            raise AttachmentError(f"{label} : format non reconnu (acceptés : {', '.join(policy.types)})")
        if self.media_type is not None and self.media_type != detected:
            raise AttachmentError(f"{label} : annoncé {self.media_type}, mais le contenu est {detected}")
        if detected not in policy.types:
            raise AttachmentError(f"{label} : {detected} non accepté (acceptés : {', '.join(policy.types)})")
        return detected
