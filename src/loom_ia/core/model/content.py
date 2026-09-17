# SPDX-License-Identifier: Apache-2.0
"""Blocs de contenu des messages et résultat d'outil (#8, #15).

Le format est neutre : chaque adaptateur de modèle le traduit vers le format
de son fournisseur. Les données propres à un fournisseur (signature du
raisonnement, identifiant d'item…) vivent dans ``provider_meta``, typé par
fournisseur ; un adaptateur ne lit que son entrée.
"""

from typing import Annotated, Literal, Self, cast

from pydantic import Field, JsonValue, field_validator, model_validator

from loom_ia.core.model.base import DomainModel


class AnthropicMeta(DomainModel):
    provider: Literal["anthropic"] = "anthropic"
    signature: str | None = None
    redacted_data: str | None = None


class OpenAIMeta(DomainModel):
    provider: Literal["openai"] = "openai"
    item_id: str | None = None
    encrypted_content: str | None = None


type ProviderMeta = Annotated[AnthropicMeta | OpenAIMeta, Field(discriminator="provider")]


class _Block(DomainModel):
    # Point de cache neutre (#8) : chaque adaptateur le traduit.
    cache_breakpoint: bool = False
    provider_meta: dict[str, ProviderMeta] = Field(default_factory=dict)

    @field_validator("provider_meta", mode="before")
    @classmethod
    def _infer_provider(cls, value: object) -> object:
        # {"anthropic": {"signature": …}} : le fournisseur est déduit de la clé.
        if not isinstance(value, dict):
            return value
        result: dict[object, object] = {}
        for key, meta in cast(dict[object, object], value).items():
            if isinstance(meta, dict):
                fields = cast(dict[str, object], meta)
                result[key] = fields if "provider" in fields else {"provider": key, **fields}
            else:
                result[key] = meta
        return result

    @model_validator(mode="after")
    def _check_provider_keys(self) -> Self:
        for key, meta in self.provider_meta.items():
            if key != meta.provider:
                raise ValueError(
                    f"provider_meta[{key!r}] contient des données du fournisseur {meta.provider!r}"
                )
        return self


class TextBlock(_Block):
    type: Literal["text"] = "text"
    text: str


class JsonBlock(_Block):
    type: Literal["json"] = "json"
    data: JsonValue


class ArtifactRefBlock(_Block):
    """Référence vers un fichier du stockage d'artefacts, jamais ses octets."""

    type: Literal["artifact"] = "artifact"
    uri: str
    media_type: str
    size: int | None = Field(default=None, ge=0)
    name: str | None = None


class ReasoningBlock(_Block):
    """Raisonnement du modèle, neutre vis-à-vis du fournisseur (#7)."""

    type: Literal["reasoning"] = "reasoning"
    text: str = ""
    # Modèle qui l'a produit : après une bascule de modèle, le raisonnement
    # d'un autre fournisseur est écarté.
    model_id: str | None = None


class ToolCallBlock(_Block):
    type: Literal["tool_call"] = "tool_call"
    call_id: str
    name: str
    arguments: dict[str, JsonValue] = Field(default_factory=dict)


type OutputBlock = Annotated[TextBlock | JsonBlock | ArtifactRefBlock, Field(discriminator="type")]


class ToolOutput(DomainModel):
    """Résultat d'un outil (#15)."""

    blocks: tuple[OutputBlock, ...] = ()
    # Données structurées pour $ref, la réponse structurée et l'interface.
    data: JsonValue = None
    is_error: bool = False
    # URI des artefacts produits (G3).
    artifacts: tuple[str, ...] = ()

    @classmethod
    def text(cls, text: str, *, is_error: bool = False) -> Self:
        return cls(blocks=(TextBlock(text=text),), is_error=is_error)

    @classmethod
    def error(cls, message: str) -> Self:
        return cls.text(message, is_error=True)

    @property
    def as_text(self) -> str:
        """Contenu textuel des blocs ``text``, concaténé."""
        return "\n".join(b.text for b in self.blocks if isinstance(b, TextBlock))


class ToolResultBlock(_Block):
    type: Literal["tool_result"] = "tool_result"
    call_id: str
    output: ToolOutput


type ContentBlock = Annotated[
    TextBlock | JsonBlock | ArtifactRefBlock | ReasoningBlock | ToolCallBlock | ToolResultBlock,
    Field(discriminator="type"),
]
