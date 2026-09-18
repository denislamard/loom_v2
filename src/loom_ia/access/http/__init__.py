# SPDX-License-Identifier: Apache-2.0
"""Accès REST : application FastAPI et serveur de développement (N2).

Demande l'extra ``http`` : ``uv sync --extra http``.
"""

from loom_ia.access.http.app import create_app, sse
from loom_ia.access.http.auth import Caller, identify, require
from loom_ia.access.http.schemas import AgentInfo, RunRequest
from loom_ia.access.http.serve import serve

__all__ = [
    "AgentInfo",
    "Caller",
    "RunRequest",
    "create_app",
    "identify",
    "require",
    "serve",
    "sse",
]
