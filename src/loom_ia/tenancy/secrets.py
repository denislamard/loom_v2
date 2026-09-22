# SPDX-License-Identifier: Apache-2.0
"""Secrets par client, lus dans l'environnement (L2, M3, §16.3).

La configuration ne nomme que des variables : ``api_key_env`` d'un modèle,
``env_from`` et ``headers_env`` d'un serveur MCP. Un client redirige ces
noms vers **ses** variables (``secrets: {CRM_TOKEN: DUPONT_CRM_TOKEN}``), et
c'est la table résolue — et elle seule — qui descend au montage de ses
agents.

Ce qu'un client ne redirige pas reste lu dans l'environnement commun : sans
cela, la première config multi-clients casserait sur ``ANTHROPIC_API_KEY``.
Mais une redirection vers une variable **absente** ne retombe pas sur le
nom d'origine : elle vaut vide. Sinon un client dont le secret manque
emprunterait celui de tout le monde — exactement la fuite que la
redirection est là pour empêcher.
"""

import os
from collections.abc import Mapping

from loom_ia.config.models import LoomConfig
from loom_ia.core.model import TenantId


class EnvironmentSecrets:
    """``SecretProvider`` adossé à l'environnement du process."""

    def __init__(self, config: LoomConfig, environ: Mapping[str, str] | None = None) -> None:
        self._environ = os.environ if environ is None else environ
        self._mappings = {tenant.id: dict(tenant.secrets) for tenant in config.tenants}
        self._tables: dict[TenantId, Mapping[str, str]] = {}

    def secrets(self, tenant_id: TenantId) -> Mapping[str, str]:
        table = self._tables.get(tenant_id)
        if table is None:
            table = self._resolve(tenant_id)
            self._tables[tenant_id] = table
        return table

    def _resolve(self, tenant_id: TenantId) -> Mapping[str, str]:
        redirected = self._mappings.get(tenant_id)
        if not redirected:
            return self._environ
        return {
            **self._environ,
            **{name: self._environ.get(variable, "") for name, variable in redirected.items()},
        }
