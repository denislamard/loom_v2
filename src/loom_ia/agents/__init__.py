# SPDX-License-Identifier: Apache-2.0
"""Agents : définition et registre (A9)."""

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
    "ToolResultsContext",
    "UnknownAgent",
]
