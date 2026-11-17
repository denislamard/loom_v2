# SPDX-License-Identifier: Apache-2.0
"""Juge : un modèle qui note une sortie selon des critères (fonctions E3 et E6, point 21).

Un juge porte sur la réponse finale d'un agent ou sur la sortie d'un rôle.
Pour chaque critère, il rend une note entre 0 et 1 et un motif. Un critère
est réussi si sa note atteint son seuil (``min_score``). Un critère
bloquant sous son seuil fait refuser la sortie : son auteur la répare, puis
``on_failure`` décide, comme pour un contrat de sortie. Un critère non
bloquant sous son seuil est seulement signalé.

Déclenchement (point 21) : décidé par le code, jamais par le LLM. Le juge
s'exécute si toutes les clauses présentes de ``when`` sont vraies :

- ``tenants`` : clients concernés ;
- ``sample`` : proportion des runs jugés, par un tirage déterministe
  (``hash(run_id + nom du juge) < sample``) : deux juges ne tirent pas les
  mêmes runs, et le rejeu reproduit le même tirage ;
- ``condition`` : prédicat Python qui reçoit un ``JudgeInput``.

L'appelant peut forcer tous les juges (``judges="force"``, audit, évals) ou
les sauter (``skip``). Un juge qui ne s'exécute pas écrit un ``guard.checked``
``skipped`` avec son motif (``SkipReason``).

Un juge se déclare sous ``judge`` d'un agent ou d'un rôle ; son modèle ne
doit pas être celui qu'il juge (avertissement, erreur en profil ``prod``,
fonction E6). Son verdict est écrit dans ``judge.evaluated``.
"""

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final, Literal

from pydantic import Field, JsonValue

from loom_ia.core.model.base import DomainModel
from loom_ia.core.model.context import CallerContext

type JudgesMode = Literal["auto", "force", "skip"]
"""Juges d'un run, choisis par l'appelant : selon leur ``when`` (``auto``), tous (``force``), ou aucun (``skip``).

Se passe à ``run`` (``judges=``), à ``loom run --judges`` ; écrit dans
``run.started`` et hérité par les sous-runs.
"""
type SkipReason = Literal["filtered", "sampled_out", "condition_false", "caller_skip", "other_profile"]
"""Motif d'un juge qui ne s'exécute pas : client hors ``tenants``, run non tiré, condition fausse, ``skip`` de l'appelant, autre profil."""

JUDGES_MODES: Final[tuple[JudgesMode, ...]] = ("auto", "force", "skip")
"""Les valeurs de ``JudgesMode``, dans l'ordre."""
CRITERION_NAME_PATTERN: Final = r"^[A-Za-z0-9_-]{1,64}$"
"""Forme d'un nom de critère : 1 à 64 lettres, chiffres, ``_`` ou ``-``."""
DEFAULT_MIN_SCORE: Final = 0.8
"""Seuil d'un critère par défaut."""


class Criterion(DomainModel):
    """Critère d'un juge : une règle, un seuil, bloquant ou non.

    Attributes:
        name: Nom du critère, repris dans le verdict.
        rule: Ce que la sortie doit respecter, donné au juge tel quel.
        min_score: Seuil, entre 0 et 1. Défaut : 0,8.
        blocking: Sous son seuil, un critère bloquant fait refuser la sortie ;
            un critère non bloquant est seulement signalé. Défaut : vrai.
    """

    name: str = Field(pattern=CRITERION_NAME_PATTERN)
    rule: str = Field(min_length=1)
    min_score: float = Field(default=DEFAULT_MIN_SCORE, ge=0.0, le=1.0)
    blocking: bool = True


class JudgeWhen(DomainModel):
    """Déclenchement d'un juge (``when``) : toutes les clauses présentes doivent être vraies.

    Attributes:
        sample: Proportion des runs jugés, entre 0 et 1 (tirage déterministe
            par run et par juge). Défaut : 1, tous.
        condition: Prédicat ``(JudgeInput) -> bool``, synchrone ou non, donné
            par ``module:attr``.
        tenants: Clients dont les runs sont jugés, un au moins.
        profiles: Profils où ce juge travaille (phase 5.5a) : ailleurs il est
            sauté, et le journal le dit (``other_profile``) — un juge déclaré
            ne disparaît pas en silence parce qu'on a changé de profil.
    """

    sample: float = Field(default=1.0, ge=0.0, le=1.0)
    condition: str | None = Field(default=None, min_length=1)
    tenants: tuple[str, ...] | None = Field(default=None, min_length=1)
    profiles: tuple[str, ...] | None = Field(default=None, min_length=1)


class CriterionScore(DomainModel):
    """Note d'un critère par le juge, avec son seuil ; écrite dans ``judge.evaluated``.

    Attributes:
        name: Nom du critère.
        score: Note donnée par le juge, entre 0 et 1.
        min_score: Seuil du critère.
        blocking: Vrai si le critère est bloquant.
        reason: Motif donné par le juge ; du contenu.
    """

    name: str
    score: float = Field(ge=0.0, le=1.0)
    min_score: float = Field(ge=0.0, le=1.0)
    blocking: bool
    reason: str = ""

    @property
    def passed(self) -> bool:
        """Vrai si la note atteint le seuil."""
        return self.score >= self.min_score


@dataclass(frozen=True, slots=True, kw_only=True)
class JudgeInput:
    """Ce que reçoit le prédicat ``when.condition`` d'un juge.

    Attributes:
        run_id: Run jugé.
        agent: Agent du run.
        target: ``output`` (réponse finale) ou ``role:<nom>``.
        role: Rôle dont la sortie est jugée ; ``None`` pour la réponse finale.
        output: Sortie jugée, en texte.
        data: Son objet JSON s'il y en a un (contrat avec schéma).
        request: Demande de l'utilisateur qui a lancé le run.
        arguments: Arguments du rôle jugé.
        caller: Contexte de l'appelant (client, utilisateur, métadonnées).
    """

    run_id: str
    agent: str
    target: str
    role: str | None
    output: str
    data: JsonValue = None
    request: str = ""
    arguments: Mapping[str, JsonValue] = field(default_factory=dict[str, JsonValue])
    caller: CallerContext = field(default_factory=CallerContext)


def sampled(run_id: str, judge: str, rate: float) -> bool:
    """Tirage déterministe d'un run pour un juge : vrai si le run est jugé.

    Le tirage dépend du run et du juge (SHA-256 de ``<run_id>:<juge>``) : deux
    juges ne tirent pas les mêmes runs, et le rejeu reproduit le même tirage.

    Args:
        run_id: Le run.
        judge: Le nom du juge.
        rate: Proportion des runs jugés (``when.sample``).

    Returns:
        Vrai si le run est jugé ; toujours vrai à 1, toujours faux à 0.
    """
    if rate >= 1.0:
        return True
    if rate <= 0.0:
        return False
    digest = hashlib.sha256(f"{run_id}:{judge}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64 < rate
