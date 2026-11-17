# SPDX-License-Identifier: Apache-2.0
"""Agents : définition et registre (fonction A9).

- ``spec`` : ``AgentSpec`` et ses blocs, tels que la config les déclare sous
  ``agents.<nom>`` (rôle ``main``, rôles, juges, outils, politiques, guards,
  state, budgets, accès, exposition) ;
- ``registry`` : ``AgentRegistry``, les agents d'une instance.
"""

from loom_ia.agents.registry import AgentRegistry, UnknownAgent
from loom_ia.agents.spec import (
    AgentSpec,
    BaseRole,
    Expose,
    LlmSettings,
    MainRole,
    McpTools,
    PythonTool,
    RoleSpec,
    SessionStateContext,
    StateKey,
    StateSource,
    SubAgentRef,
    ToolResultsContext,
)

__all__ = [
    "AgentRegistry",
    "AgentSpec",
    "BaseRole",
    "Expose",
    "LlmSettings",
    "MainRole",
    "McpTools",
    "PythonTool",
    "RoleSpec",
    "SessionStateContext",
    "StateKey",
    "StateSource",
    "SubAgentRef",
    "ToolResultsContext",
    "UnknownAgent",
]
