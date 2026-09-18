# SPDX-License-Identifier: Apache-2.0
"""Définition d'un agent (A9, #50).

Sous-ensemble du jalon J1 : orchestrateur (``main``) et outils Python. Les
rôles délégués, les sous-agents, les guards, le juge, le budget et les
politiques arrivent avec leurs phases ; les déclarer aujourd'hui donne une
erreur qui nomme la phase.
"""

from pathlib import Path
from typing import Final, Self, cast

from pydantic import Field, PositiveFloat, PositiveInt, model_validator

from loom_ia.core.model import Approval, DomainModel, SideEffects, reject_later

AGENT_NAME_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"

# Clés du schéma complet d'un agent, prévues pour plus tard (§17.4).
LATER_AGENT: Final[dict[str, str]] = {
    "roles": "J2 (rôles délégués)",
    "subagents": "J2 (sous-agents)",
    "approval": "J2 (approbations)",
    "max_depth": "J2 (sous-agents)",
    "policies": "J3 (politiques)",
    "output": "J3 (réponse structurée)",
    "judge": "J3 (juge)",
    "budget": "J3 (coûts et budgets)",
    "stream_output": "J3 (guards de sortie)",
    "timeout": "J1.6 (cycle de vie des runs)",
}
LATER_MAIN: Final[dict[str, str]] = {
    "fallbacks": "J3 (modèle de secours)",
    "llm": "J2 (réglages par rôle)",
}
LATER_TOOL: Final[dict[str, str]] = {
    "mcp": "J2 (client MCP)",
    "offload_over": "J2 (déport des gros résultats)",
}


class Expose(DomainModel):
    """Points d'accès qui publient l'agent (N1 à N5)."""

    rest: bool = True
    mcp: bool = True


class MainRole(DomainModel):
    """Rôle orchestrateur : le modèle qui mène le run."""

    # Identifiant d'un modèle déclaré dans ``models``.
    model: str = Field(min_length=1)
    system: str = ""
    # Chemin relatif à ``prompts_dir`` ; lu au chargement.
    system_file: Path | None = None

    @model_validator(mode="before")
    @classmethod
    def _check_input(cls, data: object) -> object:
        _reject_two_prompts(data)
        reject_later(data, LATER_MAIN)
        return data


def _reject_two_prompts(data: object) -> None:
    if not isinstance(data, dict):
        return
    keys = cast(dict[object, object], data).keys()
    if {"system", "system_file"} <= keys:
        raise ValueError("'system' et 'system_file' ne peuvent pas être donnés ensemble")


class PythonTool(DomainModel):
    """Outil Python référencé par un nom enregistré ou un chemin ``module:attr``.

    Les champs renseignés ici remplacent ce que l'outil déclare lui-même.
    """

    python: str = Field(min_length=1)
    timeout: PositiveFloat | None = None
    side_effects: SideEffects | None = None
    approval: Approval | None = None
    idempotent: bool | None = None

    @model_validator(mode="before")
    @classmethod
    def _later(cls, data: object) -> object:
        reject_later(data, LATER_TOOL)
        return data


class AgentSpec(DomainModel):
    name: str = Field(pattern=AGENT_NAME_PATTERN)
    description: str = ""
    expose: Expose = Expose()
    main: MainRole
    max_iterations: PositiveInt = 10
    tools: tuple[PythonTool, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def _later(cls, data: object) -> object:
        reject_later(data, LATER_AGENT)
        return data

    @model_validator(mode="after")
    def _unique_tools(self) -> Self:
        names = [tool.python for tool in self.tools]
        doubles = {name for name in names if names.count(name) > 1}
        if doubles:
            raise ValueError(f"Outil déclaré deux fois : {', '.join(sorted(doubles))}")
        return self
