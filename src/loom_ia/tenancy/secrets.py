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

Quand la config déclare des clients, la table d'un client ne contient que ce
que la configuration **nomme** — ``api_key_env`` des modèles, ``env_from`` et
``headers_env`` des serveurs MCP, secrets de ``storage.encryption`` — et les
noms que les clients redirigent (celui qui ne redirige pas l'un d'eux lit la
variable commune du même nom) : pas le reste de l'environnement, où dupont
verrait ``MARTIN_CRM_TOKEN``. Elle est figée et en lecture seule, car elle
part aussi aux fabriques de paquets tiers (``tool_sources``). Sans clients
déclarés, il n'y a personne à isoler : la table est l'environnement, en
lecture seule.
"""

import os
from collections.abc import Iterator, Mapping
from types import MappingProxyType

from loom_ia.config.models import LoomConfig
from loom_ia.core.model import TenantId


class EnvironmentSecrets:
    """``SecretProvider`` adossé à l'environnement du process."""

    def __init__(self, config: LoomConfig, environ: Mapping[str, str] | None = None) -> None:
        self._environ = os.environ if environ is None else environ
        self._mappings = {tenant.id: dict(tenant.secrets) for tenant in config.tenants}
        # Sans clients déclarés, rien à restreindre : la table est l'environnement.
        self._named = tuple(dict.fromkeys(_named(config))) if config.tenants else None
        self._tables: dict[TenantId, Mapping[str, str]] = {}

    def secrets(self, tenant_id: TenantId) -> Mapping[str, str]:
        table = self._tables.get(tenant_id)
        if table is None:
            table = self._resolve(tenant_id)
            self._tables[tenant_id] = table
        return table

    def _resolve(self, tenant_id: TenantId) -> Mapping[str, str]:
        if self._named is None:
            return MappingProxyType(self._environ)
        redirected = self._mappings.get(tenant_id, {})
        table = {name: self._environ[name] for name in self._named if name in self._environ}
        for name, variable in redirected.items():
            table[name] = self._environ.get(variable, "")
        return MappingProxyType(table)


def _named(config: LoomConfig) -> Iterator[str]:
    """Les variables que la configuration nomme, et que les adaptateurs viennent lire.

    Les noms que les clients redirigent en font partie : un client qui ne
    redirige pas ``CRM_TOKEN`` lit la variable commune du même nom.
    """
    for model in config.models:
        if model.api_key_env is not None:
            yield model.api_key_env
    for server in config.mcp_servers:
        yield from server.env_from.values()
        yield from server.headers_env.values()
    if config.storage.encryption is not None:
        yield from config.storage.encryption.keys
    for tenant in config.tenants:
        yield from tenant.secrets
