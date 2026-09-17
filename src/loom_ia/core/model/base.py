# SPDX-License-Identifier: Apache-2.0
"""Base commune des types du domaine."""

from pydantic import BaseModel, ConfigDict


class DomainModel(BaseModel):
    """Modèle Pydantic immuable, qui refuse les champs inconnus (#6)."""

    model_config = ConfigDict(frozen=True, extra="forbid")
