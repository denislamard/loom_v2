# SPDX-License-Identifier: Apache-2.0
"""Corps des requêtes et des réponses de l'API REST (N2)."""

from typing import Self

from pydantic import Field, JsonValue

from loom_ia.agents.spec import AgentSpec
from loom_ia.core.model import CallerContext, RunId, SessionId
from loom_ia.core.model.base import DomainModel


class AgentInfo(DomainModel):
    """Un agent tel que l'API le publie."""

    name: str
    description: str = ""

    @classmethod
    def of(cls, spec: AgentSpec) -> Self:
        return cls(name=spec.name, description=spec.description)


class RunRequest(DomainModel):
    """Demande de lancement d'un run."""

    message: str = Field(min_length=1)
    # Journal auquel rattacher le run ; par défaut le run est son propre journal.
    session_id: SessionId | None = None
    # Identifiant choisi par l'appelant, pour suivre le run dès son départ.
    run_id: RunId | None = None
    user_id: str | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict[str, JsonValue])

    def context(self) -> CallerContext:
        return CallerContext(user_id=self.user_id, metadata=dict(self.metadata))
