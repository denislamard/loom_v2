# SPDX-License-Identifier: Apache-2.0
"""Messages neutres échangés avec les modèles.

Trois rôles : ``user``, ``assistant`` et ``tool`` (résultats d'outils).
Le prompt système n'est pas un message : il appartient à la requête.
"""

from typing import Final, Literal, Self

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

# Balise du state de la session que le moteur ajoute à la fin d'une requête de
# l'orchestrateur (D4). Ce message n'est jamais journalisé comme message.
SESSION_STATE_TAG: Final = "session_state"


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


def is_session_state(message: Message) -> bool:
    """Vrai pour le message du state de la session ajouté par le moteur à une requête (D4).

    Un adaptateur pose son point de cache avant lui, car c'est la partie de la
    requête qui change d'un appel à l'autre ; le faux modèle ne le prend pas
    pour une demande de l'utilisateur. Le moteur échappe la balise dans les
    valeurs du state : seul un message qui commence par elle passe pour lui.
    """
    first = message.blocks[0]
    return (
        message.role == "user"
        and len(message.blocks) == 1
        and isinstance(first, TextBlock)
        and first.text.startswith(f"<{SESSION_STATE_TAG}>")
    )


# Début des notes que le moteur ajoute à une requête, entre crochets : « [Note
# du système : …] » (approbation en attente, arguments remplacés).
SYSTEM_NOTE: Final = "Note du système"


def is_system_note(message: Message) -> bool:
    """Vrai pour un message ``user`` qui n'est qu'une note du moteur, pas une demande.

    Le faux modèle ne compte pas ses réponses à partir d'elle : la note des
    arguments remplacés suit les résultats d'un tour, au milieu de la boucle.
    """
    first = message.blocks[0]
    return (
        message.role == "user"
        and len(message.blocks) == 1
        and isinstance(first, TextBlock)
        and first.text.startswith(f"[{SYSTEM_NOTE} : ")
    )
