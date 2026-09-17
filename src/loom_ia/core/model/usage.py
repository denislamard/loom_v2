# SPDX-License-Identifier: Apache-2.0
"""Consommation de tokens et tarifs des modèles (J1, J2)."""

from typing import Final, Self

from pydantic import NonNegativeFloat, NonNegativeInt

from loom_ia.core.model.base import DomainModel


class Usage(DomainModel):
    input_tokens: NonNegativeInt = 0
    output_tokens: NonNegativeInt = 0
    cache_read_tokens: NonNegativeInt = 0
    cache_write_tokens: NonNegativeInt = 0
    # Tokens de raisonnement, quand le fournisseur les distingue.
    reasoning_tokens: NonNegativeInt = 0

    def __add__(self, other: Self) -> Self:
        return self.model_copy(
            update={
                "input_tokens": self.input_tokens + other.input_tokens,
                "output_tokens": self.output_tokens + other.output_tokens,
                "cache_read_tokens": self.cache_read_tokens + other.cache_read_tokens,
                "cache_write_tokens": self.cache_write_tokens + other.cache_write_tokens,
                "reasoning_tokens": self.reasoning_tokens + other.reasoning_tokens,
            }
        )

    @property
    def total_tokens(self) -> int:
        """Entrée (cache compris) et sortie."""
        return (
            self.input_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
            + self.output_tokens
        )


TOKENS_PER_UNIT: Final = 1_000_000


class Pricing(DomainModel):
    """Grille tarifaire d'un modèle, en dollars par million de tokens.

    Les tokens de raisonnement sont déjà comptés dans ``output_tokens`` par les
    fournisseurs : ils ne sont pas facturés une seconde fois.
    """

    input: NonNegativeFloat = 0.0
    output: NonNegativeFloat = 0.0
    cache_read: NonNegativeFloat = 0.0
    cache_write: NonNegativeFloat = 0.0

    def cost(self, usage: Usage) -> float:
        """Coût en dollars d'une consommation."""
        total = (
            usage.input_tokens * self.input
            + usage.output_tokens * self.output
            + usage.cache_read_tokens * self.cache_read
            + usage.cache_write_tokens * self.cache_write
        )
        return total / TOKENS_PER_UNIT
