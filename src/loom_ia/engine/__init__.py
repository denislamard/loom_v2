# SPDX-License-Identifier: Apache-2.0
"""Moteur d'exécution : boucle d'un run, exécution des outils, rôles délégués (#3)."""

from loom_ia.engine.delegated import DelegatedPayload, DelegatedTool, RunView
from loom_ia.engine.executor import (
    DEFAULT_TOOL_TIMEOUT,
    UNKNOWN_STATE,
    AnyTool,
    Delegated,
    ToolEvent,
    ToolExecutor,
)
from loom_ia.engine.loop import DEFAULT_MAX_ITERATIONS, RunContext, begin_run, drive, step
from loom_ia.engine.refs import REF_KEY, REF_PREFIX, REFS_HINT, RefError, ResultIndex, mark_results
from loom_ia.engine.roles import ContextItem, RoleDefinition, RoleTool, ToolResults

__all__ = [
    "DEFAULT_MAX_ITERATIONS",
    "DEFAULT_TOOL_TIMEOUT",
    "REFS_HINT",
    "REF_KEY",
    "REF_PREFIX",
    "UNKNOWN_STATE",
    "AnyTool",
    "ContextItem",
    "Delegated",
    "DelegatedPayload",
    "DelegatedTool",
    "RefError",
    "ResultIndex",
    "RoleDefinition",
    "RoleTool",
    "RunContext",
    "RunView",
    "ToolEvent",
    "ToolExecutor",
    "ToolResults",
    "begin_run",
    "drive",
    "mark_results",
    "step",
]
