# SPDX-License-Identifier: Apache-2.0
"""Contexte fourni par l'appelant (A8)."""

from pydantic import Field, JsonValue

from loom_ia.core.model.base import DomainModel
from loom_ia.core.model.ids import DEFAULT_TENANT, TenantId


class CallerContext(DomainModel):
    tenant_id: TenantId = DEFAULT_TENANT
    user_id: str | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
