# SPDX-License-Identifier: Apache-2.0
"""Accès MCP : un serveur qui publie les agents en outils (N3).

Demande l'extra ``mcp`` : ``uv sync --extra mcp``.
"""

from loom_ia.access.mcp_server.server import (
    SERVER_NAME,
    STATUS_TOOL,
    create_server,
    run_stdio,
    structured,
)

__all__ = ["SERVER_NAME", "STATUS_TOOL", "create_server", "run_stdio", "structured"]
