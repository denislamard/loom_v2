# SPDX-License-Identifier: Apache-2.0
"""Contexte fourni par l'appelant d'un run (fonction A8)."""

from pydantic import Field, JsonValue

from loom_ia.core.model.base import DomainModel
from loom_ia.core.model.ids import DEFAULT_TENANT, TenantId


class CallerContext(DomainModel):
    """Qui lance un run, et ce qu'il y joint : transmis aux outils, aux politiques et aux traces.

    Écrit dans le ``run.started`` du run (``context``) ; ``metadata`` est du
    contenu, retiré sans la permission ``read_content``.

    Attributes:
        tenant_id: Client du run. Défaut : ``default``, le client implicite
            du mode librairie.
        user_id: Utilisateur au nom duquel le run est lancé : le lanceur,
            dont dépend l'auto-approbation (décision D-7 des utilisateurs et
            rôles).
        via: Passerelle qui l'a lancé en son nom (``as_user``, décision D-5
            des utilisateurs et rôles) ; absente sans passerelle.
        metadata: Métadonnées libres de l'appelant (JSON), transmises aux
            outils (``ToolContext.caller``).
    """

    tenant_id: TenantId = DEFAULT_TENANT
    user_id: str | None = None
    via: str | None = Field(default=None, exclude_if=lambda value: value is None)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
