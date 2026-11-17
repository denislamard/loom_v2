# SPDX-License-Identifier: Apache-2.0
"""Registre des agents d'une instance (fonction A9).

Une instance héberge plusieurs agents nommés ; chaque point d'accès (Python,
REST, MCP) y puise les agents qu'il publie.
"""

from collections.abc import Iterable, Iterator
from typing import TYPE_CHECKING, Literal, Self

from loom_ia.agents.spec import AgentSpec
from loom_ia.core.model import Unknown

if TYPE_CHECKING:
    from loom_ia.config.models import LoomConfig

type Access = Literal["rest", "mcp"]
"""Point d'accès qui publie des agents."""


class UnknownAgent(Unknown):
    """Aucun agent de ce nom dans le registre.

    Le message liste les agents déclarés, sans les agents internes.

    Args:
        name: Le nom demandé.
        known: Les noms à proposer.

    Attributes:
        name: Le nom demandé.
    """

    def __init__(self, name: str, known: Iterable[str]) -> None:
        available = ", ".join(known) or "aucun"
        super().__init__(f"Agent {name!r} inconnu (agents : {available})")
        self.name = name


class AgentRegistry:
    """Les agents d'une instance, par nom.

    Args:
        agents: Les agents, dont les agents internes (``_compaction``).

    Raises:
        ValueError: Deux agents du même nom.
    """

    def __init__(self, agents: Iterable[AgentSpec] = ()) -> None:
        self._agents: dict[str, AgentSpec] = {}
        for agent in agents:
            if agent.name in self._agents:
                raise ValueError(f"Agent déclaré deux fois : {agent.name}")
            self._agents[agent.name] = agent

    @classmethod
    def from_config(cls, config: LoomConfig) -> Self:
        """Registre des agents d'une configuration, agents internes compris.

        Args:
            config: La configuration.
        """
        return cls(config.all_agents)

    def get(self, name: str) -> AgentSpec:
        """L'agent de ce nom.

        Args:
            name: Le nom.

        Raises:
            UnknownAgent: Aucun agent de ce nom ; le message propose les
                agents déclarés, sans les agents internes.
        """
        try:
            return self._agents[name]
        except KeyError as exc:
            # Les agents internes (``_compaction``) ne se lancent pas : ils ne sont
            # pas proposés à qui s'est trompé de nom (essai de la fonction N4, 23/10).
            declared = [n for n in self.names if not n.startswith("_")]
            raise UnknownAgent(name, declared) from exc

    def exposed(self, access: Access) -> tuple[AgentSpec, ...]:
        """Agents publiés par un point d'accès (``expose.rest`` ou ``expose.mcp``).

        Args:
            access: ``rest`` ou ``mcp``.
        """
        return tuple(agent for agent in self._agents.values() if (agent.expose.rest if access == "rest" else agent.expose.mcp))

    @property
    def names(self) -> tuple[str, ...]:
        """Noms des agents, dans l'ordre de déclaration, agents internes compris."""
        return tuple(self._agents)

    def __contains__(self, name: str) -> bool:
        return name in self._agents

    def __iter__(self) -> Iterator[AgentSpec]:
        return iter(self._agents.values())

    def __len__(self) -> int:
        return len(self._agents)
