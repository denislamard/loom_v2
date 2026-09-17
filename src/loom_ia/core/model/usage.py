# SPDX-License-Identifier: Apache-2.0
"""Consommation de tokens d'un appel ou d'un ensemble d'appels (J1)."""

from typing import Self

from pydantic import NonNegativeInt

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
