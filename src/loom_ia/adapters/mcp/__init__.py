# SPDX-License-Identifier: Apache-2.0
"""Client MCP : serveurs référencés par les agents comme sources d'outils (#19, D3).

Demande l'extra ``mcp`` ; le montage n'importe ce module que si la config
déclare des serveurs.
"""

from loom_ia.adapters.mcp.convert import declared, description, to_output
from loom_ia.adapters.mcp.pool import McpPool
from loom_ia.adapters.mcp.server import BACKOFF, ConnectionLost, McpServer, is_connection_lost
from loom_ia.adapters.mcp.source import IDEMPOTENCY_META, McpSelection, McpSource, McpTool
from loom_ia.adapters.mcp.transports import McpConfigError, SessionFactory, session_factory

__all__ = [
    "BACKOFF",
    "IDEMPOTENCY_META",
    "ConnectionLost",
    "McpConfigError",
    "McpPool",
    "McpSelection",
    "McpServer",
    "McpSource",
    "McpTool",
    "SessionFactory",
    "declared",
    "description",
    "is_connection_lost",
    "session_factory",
    "to_output",
]
