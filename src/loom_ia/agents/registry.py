# SPDX-License-Identifier: Apache-2.0
"""Registre des agents d'une instance (A9).

Une instance héberge plusieurs agents nommés ; chaque point d'accès (Python,
REST, MCP) y puise les agents qu'il publie.
"""

from collections.abc import Iterable, Iterator
from typing import TYPE_CHECKING, Literal, Self

from loom_ia.agents.spec import AgentSpec

if TYPE_CHECKING:
    from loom_ia.config.models import LoomConfig

type Access = Literal["rest", "mcp"]


class UnknownAgent(KeyError):
    """Aucun agent de ce nom dans le registre."""

    def __init__(self, name: str, known: Iterable[str]) -> None:
        available = ", ".join(known) or "aucun"
        super().__init__(f"Agent {name!r} inconnu (agents : {available})")
        self.name = name


class AgentRegistry:
    def __init__(self, agents: Iterable[AgentSpec] = ()) -> None:
        self._agents: dict[str, AgentSpec] = {}
        for agent in agents:
            if agent.name in self._agents:
                raise ValueError(f"Agent déclaré deux fois : {agent.name}")
            self._agents[agent.name] = agent

    @classmethod
    def from_config(cls, config: LoomConfig) -> Self:
        """Registre des agents d'une configuration."""
        return cls(config.agents)

    def get(self, name: str) -> AgentSpec:
        try:
            return self._agents[name]
        except KeyError as exc:
            raise UnknownAgent(name, self.names) from exc

    def exposed(self, access: Access) -> tuple[AgentSpec, ...]:
        """Agents publiés par un point d'accès."""
        return tuple(
            agent
            for agent in self._agents.values()
            if (agent.expose.rest if access == "rest" else agent.expose.mcp)
        )

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._agents)

    def __contains__(self, name: str) -> bool:
        return name in self._agents

    def __iter__(self) -> Iterator[AgentSpec]:
        return iter(self._agents.values())

    def __len__(self) -> int:
        return len(self._agents)
