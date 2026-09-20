# SPDX-License-Identifier: Apache-2.0
"""Consommation de tokens et tarifs des modèles (J1, J2)."""

from typing import Final, Self

from pydantic import NonNegativeFloat, NonNegativeInt, PositiveInt, model_validator

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
        return self.prompt_tokens + self.output_tokens

    @property
    def prompt_tokens(self) -> int:
        """Tokens d'entrée d'un appel, cache compris : ils choisissent le palier du tarif."""
        return self.input_tokens + self.cache_read_tokens + self.cache_write_tokens


TOKENS_PER_UNIT: Final = 1_000_000


class PriceTier(DomainModel):
    """Tarif d'un appel dont les tokens d'entrée (cache compris) dépassent ``above``.

    Un prix absent reprend celui du tarif de base.
    """

    above: PositiveInt
    input: NonNegativeFloat | None = None
    output: NonNegativeFloat | None = None
    cache_read: NonNegativeFloat | None = None
    cache_write: NonNegativeFloat | None = None


class Pricing(DomainModel):
    """Grille tarifaire d'un modèle, en dollars par million de tokens.

    Les tokens de raisonnement sont déjà comptés dans ``output_tokens`` par les
    fournisseurs : ils ne sont pas facturés une seconde fois.

    Paliers (``tiers``) : certains fournisseurs changent de prix au-delà d'un
    nombre de tokens d'entrée par appel (MiniMax-M3 double ses prix au-delà de
    512k). Le palier le plus haut dont le seuil est dépassé s'applique à tout
    l'appel.
    """

    input: NonNegativeFloat = 0.0
    output: NonNegativeFloat = 0.0
    cache_read: NonNegativeFloat = 0.0
    cache_write: NonNegativeFloat = 0.0
    # Seuils croissants.
    tiers: tuple[PriceTier, ...] = ()

    @model_validator(mode="after")
    def _check_tiers(self) -> Self:
        thresholds = [tier.above for tier in self.tiers]
        if thresholds != sorted(set(thresholds)):
            raise ValueError("tiers : seuils 'above' strictement croissants attendus")
        return self

    @property
    def priced(self) -> bool:
        """Faux pour un modèle sans tarif : ses appels comptent 0 $ (backlog #010)."""
        return any((self.input, self.output, self.cache_read, self.cache_write))

    def rates(self, usage: Usage) -> Pricing:
        """Tarif qui s'applique à un appel : celui du palier atteint, sinon le tarif de base."""
        prompt = usage.prompt_tokens
        reached = [tier for tier in self.tiers if prompt > tier.above]
        if not reached:
            return self
        tier = reached[-1]
        return Pricing(
            input=self.input if tier.input is None else tier.input,
            output=self.output if tier.output is None else tier.output,
            cache_read=self.cache_read if tier.cache_read is None else tier.cache_read,
            cache_write=self.cache_write if tier.cache_write is None else tier.cache_write,
        )

    def cost(self, usage: Usage) -> float:
        """Coût en dollars de la consommation d'un appel, au tarif de son palier."""
        rates = self.rates(usage)
        total = (
            usage.input_tokens * rates.input
            + usage.output_tokens * rates.output
            + usage.cache_read_tokens * rates.cache_read
            + usage.cache_write_tokens * rates.cache_write
        )
        return total / TOKENS_PER_UNIT
