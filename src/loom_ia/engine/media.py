# SPDX-License-Identifier: Apache-2.0
"""Fichiers dans les appels de modèle : références résolues selon les capacités (#14).

Le journal et le ``RunState`` ne portent que des références
(``ArtifactRefBlock``). Juste avant l'appel, chaque référence est remplacée
selon les capacités du modèle :

- **image, modèle avec ``vision``** : les octets, lus dans le stockage
  d'artefacts (``InlineDataBlock``, envoyé en base64 par l'adaptateur), après
  contrôle du format (``image_formats``) et de la taille
  (``max_image_bytes``) ; un format ou une taille refusés donnent une erreur
  explicite avant l'appel ;
- **sinon** (modèle sans vision, fichier qui n'est pas une image) : une
  mention textuelle, qui dit au modèle qu'un fichier existe sans le lui
  montrer.

Une image renvoyée par un outil reste dans son résultat si le modèle
l'accepte là (``tool_result_media``) ; sinon, le résultat la mentionne et
elle part dans un message utilisateur placé après les résultats du tour.

L'empreinte de la requête (``request_hash``) est calculée sur la requête
avant résolution : elle ne dépend pas des octets des fichiers.
"""

from collections.abc import Sequence
from typing import Final

from loom_ia.core.model import (
    IMAGE_TYPES,
    MOVED_IMAGES,
    ArtifactRefBlock,
    ContentBlock,
    InlineDataBlock,
    Message,
    ModelRequest,
    ModelSpec,
    OutputBlock,
    TextBlock,
    ToolResultBlock,
    is_image,
)
from loom_ia.core.ports import ArtifactNotFound, ArtifactStore, ModelError

# Tokens comptés par image dans l'estimation de la fenêtre de contexte (B7) :
# ordre de grandeur d'une image d'un mégapixel chez Anthropic.
IMAGE_TOKENS: Final = 1600


def size_label(size: int | None) -> str:
    """Taille lisible : octets, Ko ou Mo."""
    if size is None:
        return "taille inconnue"
    if size < 1024:
        return f"{size} octets"
    if size < 1024 * 1024:
        return f"{size / 1024:.0f} Ko"
    return f"{size / (1024 * 1024):.1f} Mo".replace(".", ",")


def describe(block: ArtifactRefBlock) -> str:
    """Nom, type et taille d'un fichier : ``photo.jpg (image/jpeg, 45 Ko)``."""
    name = block.name or block.uri.rsplit("/", 1)[-1]
    return f"{name} ({block.media_type}, {size_label(block.size)})"


def has_refs(messages: Sequence[Message]) -> bool:
    """Vrai si des références de fichiers sont à résoudre dans ces messages."""
    return any(_refs(message.blocks) for message in messages)


class MediaResolver:
    """Remplace les références de fichiers d'une requête, pour un modèle donné."""

    def __init__(self, spec: ModelSpec, store: ArtifactStore | None) -> None:
        self.spec = spec
        self.store = store

    def sees(self, block: ArtifactRefBlock) -> bool:
        """Vrai si le modèle reçoit les octets de ce fichier, pas une mention."""
        return self.spec.capabilities.vision and is_image(block.media_type)

    def images(self, request: ModelRequest) -> int:
        """Nombre d'images que le modèle recevra (estimation de la fenêtre)."""
        return sum(
            1 for message in request.messages for block in _refs(message.blocks) if self.sees(block)
        )

    async def resolve(self, request: ModelRequest) -> ModelRequest:
        """Requête prête à partir ; lève ``ModelError`` si une image est inutilisable."""
        if not has_refs(request.messages):
            return request
        messages: list[Message] = []
        moved: list[ContentBlock] = []
        for index, message in enumerate(request.messages):
            blocks = [await self._message_block(block, moved) for block in message.blocks]
            messages.append(message.model_copy(update={"blocks": tuple(blocks)}))
            following = request.messages[index + 1] if index + 1 < len(request.messages) else None
            if moved and (following is None or following.role != "tool"):
                messages.append(Message(role="user", blocks=(TextBlock(text=MOVED_IMAGES), *moved)))
                moved = []
        return request.model_copy(update={"messages": tuple(messages)})

    # --- Interne ---------------------------------------------------------

    async def _message_block(self, block: ContentBlock, moved: list[ContentBlock]) -> ContentBlock:
        match block:
            case ArtifactRefBlock():
                return await self._resolved(block, "jointe")
            case ToolResultBlock(output=output) if _refs(output.blocks):
                outputs = [await self._output_block(item, moved) for item in output.blocks]
                resolved = output.model_copy(update={"blocks": tuple(outputs)})
                return block.model_copy(update={"output": resolved})
            case _:
                return block

    async def _output_block(self, block: OutputBlock, moved: list[ContentBlock]) -> OutputBlock:
        if not isinstance(block, ArtifactRefBlock):
            return block
        if self.sees(block) and not self.spec.capabilities.tool_result_media:
            moved.append(await self._resolved(block, "renvoyée"))
            return TextBlock(text=f"[image {describe(block)} : dans le message qui suit]")
        return await self._resolved(block, "renvoyée")

    async def _resolved(self, block: ArtifactRefBlock, verb: str) -> TextBlock | InlineDataBlock:
        if not self.sees(block):
            if is_image(block.media_type):
                return TextBlock(
                    text=f"[image {verb} {describe(block)} : non visible par ce modèle]"
                )
            return TextBlock(text=f"[fichier {describe(block)}]")
        capabilities = self.spec.capabilities
        label = describe(block)
        image_format = IMAGE_TYPES[block.media_type]
        if image_format not in capabilities.image_formats:
            raise ModelError(
                "invalid_request",
                f"Image {label} : format {image_format} refusé par le modèle {self.spec.id} "
                f"(formats acceptés : {', '.join(capabilities.image_formats)})",
            )
        data = await self._load(block, label)
        limit = capabilities.max_image_bytes
        if limit is not None and len(data) > limit:
            raise ModelError(
                "invalid_request",
                f"Image {label} : {len(data)} octets, au-delà des {limit} acceptés "
                f"par le modèle {self.spec.id}",
            )
        return InlineDataBlock(media_type=block.media_type, data=data, name=block.name)

    async def _load(self, block: ArtifactRefBlock, label: str) -> bytes:
        if self.store is None:
            raise ModelError(
                "invalid_request", f"Image {label} : aucun stockage d'artefacts pour la lire"
            )
        try:
            return await self.store.get(block.uri)
        except ArtifactNotFound as exc:
            raise ModelError(
                "invalid_request", f"Image {label} introuvable dans le stockage : {block.uri}"
            ) from exc


def _refs(blocks: Sequence[ContentBlock] | Sequence[OutputBlock]) -> list[ArtifactRefBlock]:
    """Références de fichiers de ces blocs, résultats d'outils compris."""
    found: list[ArtifactRefBlock] = []
    for block in blocks:
        if isinstance(block, ArtifactRefBlock):
            found.append(block)
        elif isinstance(block, ToolResultBlock):
            found += _refs(block.output.blocks)
    return found
