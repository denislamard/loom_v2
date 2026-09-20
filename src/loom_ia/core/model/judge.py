# SPDX-License-Identifier: Apache-2.0
"""Juge : un modèle qui note une sortie selon des critères (E3, E6, #21).

Un juge porte sur la réponse finale d'un agent ou sur la sortie d'un rôle.
Pour chaque critère, il rend une note entre 0 et 1 et un motif. Un critère
est réussi si sa note atteint son seuil (``min_score``). Un critère
bloquant sous son seuil fait refuser la sortie : son auteur la répare, puis
``on_failure`` décide, comme pour un contrat de sortie. Un critère non
bloquant sous son seuil est seulement signalé.

Déclenchement (#21) : décidé par le code, jamais par le LLM. Le juge
s'exécute si toutes les clauses présentes de ``when`` sont vraies :

- ``tenants`` : clients concernés ;
- ``sample`` : proportion des runs jugés, par un tirage déterministe
  (``hash(run_id + nom du juge) < sample``) : deux juges ne tirent pas les
  mêmes runs, et le rejeu reproduit le même tirage ;
- ``condition`` : prédicat Python qui reçoit un ``JudgeInput``.

L'appelant peut forcer tous les juges (``judges="force"``, audit, évals) ou
les sauter (``skip``). Un juge qui ne s'exécute pas écrit un ``guard.checked``
``skipped`` avec son motif (``SkipReason``).
"""

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final, Literal

from pydantic import Field, JsonValue, model_validator

from loom_ia.core.model.base import DomainModel, reject_later
from loom_ia.core.model.context import CallerContext

# Juges d'un run, choisis par l'appelant : selon leur ``when``, tous, ou aucun.
type JudgesMode = Literal["auto", "force", "skip"]
# Motif d'un juge qui ne s'exécute pas.
type SkipReason = Literal["filtered", "sampled_out", "condition_false", "caller_skip"]

JUDGES_MODES: Final[tuple[JudgesMode, ...]] = ("auto", "force", "skip")
CRITERION_NAME_PATTERN: Final = r"^[A-Za-z0-9_-]{1,64}$"
DEFAULT_MIN_SCORE: Final = 0.8

# Clés de ``when`` prévues pour plus tard.
LATER_WHEN: Final[dict[str, str]] = {"profiles": "J5 (profils dev et prod)"}


class Criterion(DomainModel):
    """Critère d'un juge : une règle, un seuil, bloquant ou non."""

    name: str = Field(pattern=CRITERION_NAME_PATTERN)
    # Ce que la sortie doit respecter, donné au juge tel quel.
    rule: str = Field(min_length=1)
    min_score: float = Field(default=DEFAULT_MIN_SCORE, ge=0.0, le=1.0)
    # Sous son seuil, un critère bloquant fait refuser la sortie.
    blocking: bool = True


class JudgeWhen(DomainModel):
    """Déclenchement d'un juge : toutes les clauses présentes doivent être vraies."""

    # Proportion des runs jugés (tirage déterministe par run et par juge).
    sample: float = Field(default=1.0, ge=0.0, le=1.0)
    # Prédicat ``(JudgeInput) -> bool``, synchrone ou non : ``module:attr``.
    condition: str | None = Field(default=None, min_length=1)
    # Clients dont les runs sont jugés.
    tenants: tuple[str, ...] | None = Field(default=None, min_length=1)

    @model_validator(mode="before")
    @classmethod
    def _later(cls, data: object) -> object:
        reject_later(data, LATER_WHEN)
        return data


class CriterionScore(DomainModel):
    """Note d'un critère par le juge, avec son seuil."""

    name: str
    score: float = Field(ge=0.0, le=1.0)
    min_score: float = Field(ge=0.0, le=1.0)
    blocking: bool
    reason: str = ""

    @property
    def passed(self) -> bool:
        return self.score >= self.min_score


@dataclass(frozen=True, slots=True, kw_only=True)
class JudgeInput:
    """Ce que reçoit le prédicat ``when.condition`` d'un juge."""

    run_id: str
    agent: str
    # ``output`` (réponse finale) ou ``role:<nom>``.
    target: str
    # Rôle dont la sortie est jugée ; None pour la réponse finale.
    role: str | None
    # Sortie jugée, en texte, et son objet JSON s'il y en a un (contrat avec schéma).
    output: str
    data: JsonValue = None
    # Demande de l'utilisateur qui a lancé le run.
    request: str = ""
    # Arguments du rôle jugé.
    arguments: Mapping[str, JsonValue] = field(default_factory=dict[str, JsonValue])
    caller: CallerContext = field(default_factory=CallerContext)


def sampled(run_id: str, judge: str, rate: float) -> bool:
    """Tirage déterministe d'un run pour un juge : vrai si le run est jugé."""
    if rate >= 1.0:
        return True
    if rate <= 0.0:
        return False
    digest = hashlib.sha256(f"{run_id}:{judge}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64 < rate
