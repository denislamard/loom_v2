# SPDX-License-Identifier: Apache-2.0
"""Ports des outils (#15, #18, #19).

Un outil se décrit par un ``ToolSpec`` et s'exécute avec ``invoke``. Python,
MCP, rôles et sous-agents implémentent ce même port.

Une source d'outils (``ToolSource``, un serveur MCP par exemple) fournit ses
outils au début de chaque run : ils peuvent changer d'un run à l'autre, et la
source peut être indisponible. La liste obtenue reste fixe jusqu'à la fin du
run, pour que les requêtes au modèle restent stables.
"""

import hashlib
from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

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


@runtime_checkable
class Tool(Protocol):
    """Outil appelable par un agent.

    ``isinstance`` vérifie seulement la présence de ``spec`` et ``invoke`` :
    c'est ce qui sert à reconnaître un outil dans la config.
    """

    @property
    def spec(self) -> ToolSpec: ...

    async def invoke(self, arguments: dict[str, JsonValue], context: ToolContext) -> ToolOutput:
        """Exécute l'appel ; les arguments ont déjà été validés par le schéma."""
        ...


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceContext:
    """Le run pour lequel une source ouvre ses outils."""

    tenant_id: TenantId
    session_id: SessionId
    run_id: RunId
    agent: str


class SourceUnavailable(Exception):
    """La source ne peut pas fournir ses outils (serveur injoignable, erreur de protocole…)."""

    def __init__(self, source: str, message: str) -> None:
        super().__init__(f"{source} : {message}")
        self.source = source
        self.message = message


@runtime_checkable
class ToolSource(Protocol):
    """Fournisseur d'outils découverts au début d'un run (serveur MCP…)."""

    @property
    def name(self) -> str:
        """Nom de la source, repris dans le journal."""
        ...

    @property
    def required(self) -> bool:
        """Vrai si le run ne peut pas se passer de cette source."""
        ...

    def open(self, context: SourceContext) -> AbstractAsyncContextManager[Sequence[Tool]]:
        """Outils disponibles pour ce run, jusqu'à la sortie du contexte.

        Lève ``SourceUnavailable`` si la source ne répond pas.
        """
        ...
