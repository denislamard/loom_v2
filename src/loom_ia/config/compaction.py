# SPDX-License-Identifier: Apache-2.0
"""Agent interne de compaction, tiré de la configuration (#23).

Résumer une session est un appel de modèle comme un autre : plutôt qu'un
chemin à part, `sessions.compaction` produit un agent ordinaire, monté comme
les autres. Il hérite ainsi du retry, du modèle de secours, du suivi des
coûts, des spans et de la réparation, sans rien recâbler.

Il n'a pas d'outils, une seule itération, et n'est publié ni en REST ni en
MCP. Son nom est réservé : un agent déclaré ainsi est refusé au chargement.
"""

from pathlib import Path
from typing import Final, Self

from pydantic import Field, NonNegativeInt, PositiveInt, model_validator

from loom_ia.agents.spec import AgentSpec, Expose, MainRole
from loom_ia.core.model.base import DomainModel

COMPACTION_AGENT: Final = "_compaction"

COMPACTION_SYSTEM: Final = """\
Tu résumes les échanges anciens d'une conversation entre un utilisateur et un
agent, pour que la suite tienne dans le contexte du modèle.

- Écris un résumé suivi, en français, sans titre, sans liste à puces, et sans
  commenter ton travail : seul le résumé sort.
- Conserve tels quels les identifiants, numéros, montants, dates, quantités,
  adresses e-mail et noms propres : ce sont eux qui servent à la suite.
- Garde ce qui a été demandé, ce qui a été fait, ce qui a été décidé et ce qui
  reste à faire. Écarte les formules de politesse et la mise en forme.
- Un résumé des échanges plus anciens peut ouvrir le segment : reprends ce
  qu'il garde d'utile, sans le perdre ni le recopier mot pour mot.
- N'invente rien et ne conclus rien qui ne soit pas dans les échanges.
"""


class CompactionConfig(DomainModel):
    """Résumé d'une session devenue longue (#23, §11.3)."""

    # Modèle dédié, moins cher que celui de l'agent.
    model: str = Field(min_length=1)
    # Taille d'historique au-delà de laquelle un résumé est mis en file à la
    # fin d'un run, et taille au-delà de laquelle il devient bloquant.
    over_tokens: PositiveInt = 12_000
    hard_tokens: PositiveInt = 150_000
    # Tours laissés intacts à la fin de l'historique (un tour = un run).
    keep_last: NonNegativeInt = 6
    # Contrôle déterministe des repères du segment dans le résumé.
    fidelity_check: bool = True
    # Prompt interne surchargé, relatif au dossier des prompts.
    system_file: Path | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.hard_tokens <= self.over_tokens:
            raise ValueError(
                "Compaction : 'hard_tokens' doit dépasser 'over_tokens' "
                f"({self.hard_tokens} <= {self.over_tokens})"
            )
        return self


def compaction_agent(spec: CompactionConfig) -> AgentSpec:
    """Agent interne qui résume un segment de session."""
    main = (
        MainRole(model=spec.model, system_file=spec.system_file)
        if spec.system_file is not None
        else MainRole(model=spec.model, system=COMPACTION_SYSTEM)
    )
    return AgentSpec(
        name=COMPACTION_AGENT,
        description="Résume les échanges anciens d'une session (agent interne).",
        expose=Expose(rest=False, mcp=False),
        main=main,
        max_iterations=1,
    )
