# SPDX-License-Identifier: Apache-2.0
"""Port des outils (#15, #18).

Un outil se décrit par un ``ToolSpec`` et s'exécute avec ``invoke``. Python,
MCP, rôles et sous-agents implémentent ce même port.
"""

import hashlib
from dataclasses import dataclass, field
from typing import Protocol

from pydantic import JsonValue

from loom_ia.core.model.content import ToolOutput
from loom_ia.core.model.context import CallerContext
from loom_ia.core.model.ids import RunId, SessionId, TenantId
from loom_ia.core.model.tooling import ToolSpec


def idempotency_key(run_id: RunId, call_id: str) -> str:
    """Clé technique d'un appel : identique à chaque reprise du même appel (#18)."""
    return hashlib.sha256(f"{run_id}:{call_id}".encode()).hexdigest()


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolContext:
    """Ce qu'un outil sait de l'appel en cours."""

    tenant_id: TenantId
    session_id: SessionId
    run_id: RunId
    call_id: str
    agent: str
    caller: CallerContext = field(default_factory=CallerContext)

    @property
    def idempotency_key(self) -> str:
        return idempotency_key(self.run_id, self.call_id)


class ToolError(Exception):
    """Erreur dont le message est destiné au modèle, pour qu'il corrige son appel."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class Tool(Protocol):
    @property
    def spec(self) -> ToolSpec: ...

    async def invoke(self, arguments: dict[str, JsonValue], context: ToolContext) -> ToolOutput:
        """Exécute l'appel ; les arguments ont déjà été validés par le schéma."""
        ...
