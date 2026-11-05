# SPDX-License-Identifier: Apache-2.0
"""Contexte fourni par l'appelant (A8)."""

from pydantic import Field, JsonValue

from loom_ia.core.model.base import DomainModel
from loom_ia.core.model.ids import DEFAULT_TENANT, TenantId


class CallerContext(DomainModel):
    tenant_id: TenantId = DEFAULT_TENANT
    # Utilisateur au nom duquel le run est lancé : le lanceur, dont dépend
    # l'auto-approbation (D-7).
    user_id: str | None = None
    # Passerelle qui l'a lancé en son nom (``as_user``, D-5) ; absente sans passerelle.
    via: str | None = Field(default=None, exclude_if=lambda value: value is None)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
