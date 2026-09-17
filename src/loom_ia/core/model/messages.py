# SPDX-License-Identifier: Apache-2.0
"""Messages neutres échangés avec les modèles.

Trois rôles : ``user``, ``assistant`` et ``tool`` (résultats d'outils).
Le prompt système n'est pas un message : il appartient à la requête.
"""

from typing import Literal, Self

from pydantic import model_validator

from loom_ia.core.model.base import DomainModel
from loom_ia.core.model.content import (
    ContentBlock,
    ReasoningBlock,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
)

type Role = Literal["user", "assistant", "tool"]


class Message(DomainModel):
    role: Role
    blocks: tuple[ContentBlock, ...]

    @model_validator(mode="after")
    def _check_blocks(self) -> Self:
        if not self.blocks:
            raise ValueError("Un message doit contenir au moins un bloc")
        for block in self.blocks:
            if isinstance(block, ToolResultBlock) != (self.role == "tool"):
                raise ValueError(
                    "Les résultats d'outils vont dans un message 'tool', et uniquement là"
                )
            if isinstance(block, ToolCallBlock | ReasoningBlock) and self.role != "assistant":
                raise ValueError(
                    f"Un bloc {block.type!r} n'est permis que dans un message 'assistant'"
                )
        return self

    @classmethod
    def user(cls, text: str) -> Self:
        return cls(role="user", blocks=(TextBlock(text=text),))

    @classmethod
    def assistant(cls, text: str) -> Self:
        return cls(role="assistant", blocks=(TextBlock(text=text),))

    @property
    def text(self) -> str:
        """Contenu des blocs ``text``, concaténé."""
        return "".join(b.text for b in self.blocks if isinstance(b, TextBlock))

    @property
    def tool_calls(self) -> tuple[ToolCallBlock, ...]:
        return tuple(b for b in self.blocks if isinstance(b, ToolCallBlock))

    def without_reasoning(self) -> Self | None:
        """Copie sans blocs de raisonnement, ou None si plus rien ne reste."""
        kept = tuple(b for b in self.blocks if not isinstance(b, ReasoningBlock))
        if len(kept) == len(self.blocks):
            return self
        if not kept:
            return None
        return self.model_copy(update={"blocks": kept})
