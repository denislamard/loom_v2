# SPDX-License-Identifier: Apache-2.0
"""Consommation de tokens et tarifs des modèles (fonctions J1 et J2).

``Usage`` compte les tokens d'un appel ou d'un run ; ``Pricing`` (sous
``models[].pricing``) en tire le coût en dollars.
"""

from typing import Final, Self

from pydantic import NonNegativeFloat, NonNegativeInt, PositiveInt, model_validator

from loom_ia.core.model.base import DomainModel

CHARS_PER_TOKEN: Final = 4
"""Caractères (de JSON) par token, en approximation.

C'est l'estimation de loom quand le fournisseur ne compte pas (contrôle de la
fenêtre de contexte, fonction B7 ; seuils de compaction), et le compte du
modèle simulé.
"""


class Usage(DomainModel):
    """Tokens consommés par un appel de modèle, ou cumulés sur un run (fonction J1).

    Deux ``Usage`` s'additionnent avec ``+``.

    Attributes:
        input_tokens: Tokens d'entrée hors cache.
        output_tokens: Tokens de sortie, raisonnement compris.
        cache_read_tokens: Tokens d'entrée lus dans le cache de prompt.
        cache_write_tokens: Tokens d'entrée écrits dans le cache de prompt.
        reasoning_tokens: Tokens de raisonnement, quand le fournisseur les
            distingue ; déjà comptés dans ``output_tokens``.
    """

    input_tokens: NonNegativeInt = 0
    output_tokens: NonNegativeInt = 0
    cache_read_tokens: NonNegativeInt = 0
    cache_write_tokens: NonNegativeInt = 0
    reasoning_tokens: NonNegativeInt = 0

    def __add__(self, other: Self) -> Self:
        """Somme champ par champ de deux consommations."""
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
        """Total des tokens : entrée (cache compris) et sortie."""
        return self.prompt_tokens + self.output_tokens

    @property
    def prompt_tokens(self) -> int:
        """Tokens d'entrée d'un appel, cache compris : ils choisissent le palier du tarif."""
        return self.input_tokens + self.cache_read_tokens + self.cache_write_tokens


TOKENS_PER_UNIT: Final = 1_000_000
"""Tokens auxquels s'applique un prix de la grille : les prix sont par million de tokens."""


class PriceTier(DomainModel):
    """Palier de tarif : prix d'un appel dont les tokens d'entrée (cache compris) dépassent ``above``.

    Attributes:
        above: Seuil de tokens d'entrée par appel, cache compris.
        input: Prix des tokens d'entrée, en dollars par million ; absent :
            celui du tarif de base.
        output: Prix des tokens de sortie ; absent : celui du tarif de base.
        cache_read: Prix des tokens lus dans le cache ; absent : celui du
            tarif de base.
        cache_write: Prix des tokens écrits dans le cache ; absent : celui
            du tarif de base.
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

    Attributes:
        input: Prix des tokens d'entrée hors cache, en dollars par million.
            Défaut : 0.
        output: Prix des tokens de sortie. Défaut : 0.
        cache_read: Prix des tokens lus dans le cache. Défaut : 0.
        cache_write: Prix des tokens écrits dans le cache (celui de la durée
            de cache choisie). Défaut : 0.
        tiers: Paliers, aux seuils ``above`` strictement croissants.

    Raises:
        ValueError: Les seuils des paliers ne sont pas strictement
            croissants.

    Examples:
        Sous ``models[]`` ::

            pricing:
              input: 0.3
              output: 1.2
              tiers:
              - { above: 512000, input: 0.6, output: 2.4 }
    """

    input: NonNegativeFloat = 0.0
    output: NonNegativeFloat = 0.0
    cache_read: NonNegativeFloat = 0.0
    cache_write: NonNegativeFloat = 0.0
    tiers: tuple[PriceTier, ...] = ()

    @model_validator(mode="after")
    def _check_tiers(self) -> Self:
        """Exige des seuils ``above`` strictement croissants."""
        thresholds = [tier.above for tier in self.tiers]
        if thresholds != sorted(set(thresholds)):
            raise ValueError("tiers : seuils 'above' strictement croissants attendus")
        return self

    @property
    def priced(self) -> bool:
        """Faux pour un modèle sans tarif : ses appels comptent 0 $ (backlog #010)."""
        return any((self.input, self.output, self.cache_read, self.cache_write))

    def rates(self, usage: Usage) -> Pricing:
        """Tarif qui s'applique à un appel : celui du palier le plus haut atteint, sinon le tarif de base.

        Args:
            usage: Consommation de l'appel ; ses tokens d'entrée, cache
                compris, choisissent le palier.

        Returns:
            Les prix, chaque prix absent d'un palier repris du tarif de base.
        """
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
        """Coût en dollars de la consommation d'un appel, au tarif de son palier.

        Args:
            usage: Consommation de l'appel.

        Returns:
            Le coût : chaque catégorie de tokens à son prix, divisé par un
            million. Les tokens de raisonnement ne sont pas comptés à part.
        """
        rates = self.rates(usage)
        total = (
            usage.input_tokens * rates.input
            + usage.output_tokens * rates.output
            + usage.cache_read_tokens * rates.cache_read
            + usage.cache_write_tokens * rates.cache_write
        )
        return total / TOKENS_PER_UNIT
