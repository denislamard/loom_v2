# SPDX-License-Identifier: Apache-2.0
"""Clients : ce qu'ils surchargent, et la config qui en découle (L1, #33, #34).

Un client est toujours présent ; en mode librairie, c'est ``default``, et il
ne surcharge rien (#33). Dès que la config déclare des ``tenants``, la liste
est **fermée** : un client qu'elle ne nomme pas est refusé, plutôt que servi
en silence avec les réglages de tout le monde.

La correspondance des modèles est appliquée **sur la définition** du modèle,
pas sur les références qui le citent : dans la config d'un client,
l'identifiant ``M3_MAIN`` désigne le modèle qu'il a choisi. Un seul endroit
à réécrire, et la correspondance vaut donc partout d'un coup —
orchestrateur, rôles, juges, chaînes de secours, compaction — sans qu'aucun
agent ne change. La config ainsi obtenue repasse les contrôles de cohérence
(M5) : un remplacement qui ne sait pas appeler d'outils ou lire une image
est refusé au démarrage, pour le client qui le demande.

Le reste des surcharges ne réécrit pas la config : ce sont des autorisations
et des valeurs, portées par ``Tenant`` jusqu'au montage des agents.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from pydantic import JsonValue

from loom_ia.config.errors import ConfigError
from loom_ia.config.models import LoomConfig, StorageConfig, TenantSpec
from loom_ia.core.model import DEFAULT_TENANT, Approval, ModelSpec, TenantId
from loom_ia.core.ports import SecretProvider
from loom_ia.tenancy.secrets import EnvironmentSecrets

EMPTY: Final[Mapping[str, str]] = {}


class UnknownTenant(KeyError):
    """Aucun client de cet identifiant dans la configuration."""

    def __init__(self, tenant_id: TenantId, declared: tuple[TenantId, ...]) -> None:
        super().__init__(
            f"Client {tenant_id!r} non déclaré (clients : {', '.join(declared) or 'aucun'})"
        )
        self.tenant_id = tenant_id


@dataclass(frozen=True, slots=True)
class Tenant:
    """Un client, et tout ce qui le distingue au montage de ses agents."""

    id: TenantId
    # Configuration vue par ce client : la correspondance des modèles appliquée.
    config: LoomConfig
    # Table de ses secrets, telle qu'elle descend aux adaptateurs (L2).
    secrets: Mapping[str, str]
    spec: TenantSpec | None = None

    def allows(self, agent: str) -> bool:
        """Vrai si ce client peut lancer cet agent (L1)."""
        return self.spec is None or self.spec.allows(agent)

    @property
    def denied(self) -> frozenset[str]:
        """Outils retirés à ce client, sous le nom que voit le modèle."""
        return frozenset(self.spec.tools_deny) if self.spec is not None else frozenset()

    @property
    def approvals(self) -> Mapping[str, Approval]:
        """Approbations imposées par ce client, par nom d'outil (#17)."""
        return self.spec.approvals if self.spec is not None else {}

    @property
    def variables(self) -> Mapping[str, JsonValue]:
        """Valeurs des variables citées par les prompts (§6)."""
        return self.spec.variables if self.spec is not None else {}

    @property
    def storage(self) -> StorageConfig | None:
        """Stockage propre à ce client ; ``None`` s'il partage celui de la racine."""
        return self.spec.storage if self.spec is not None else None


class Tenants:
    """Les clients d'une configuration, résolus une fois pour toutes.

    La résolution a lieu à la construction : une correspondance de modèles
    incohérente est une erreur de démarrage, pas une erreur au premier run
    du client concerné (M1).
    """

    def __init__(
        self,
        config: LoomConfig,
        *,
        environ: Mapping[str, str] | None = None,
        secrets: SecretProvider | None = None,
    ) -> None:
        self._config = config
        self._secrets = secrets if secrets is not None else EnvironmentSecrets(config, environ)
        self._declared = config.tenant_ids
        self._open = not config.tenants
        self._tenants = {spec.id: self._resolve(spec) for spec in config.tenants}
        if self._open:
            self._tenants[DEFAULT_TENANT] = self._resolve(None)

    @property
    def ids(self) -> tuple[TenantId, ...]:
        """Clients déclarés ; ``default`` seul quand la config n'en nomme aucun."""
        return self._declared

    @property
    def closed(self) -> bool:
        """Vrai si la config nomme ses clients : un autre est alors refusé."""
        return not self._open

    def all(self) -> tuple[Tenant, ...]:
        return tuple(self._tenants[tenant_id] for tenant_id in self._declared)

    def get(self, tenant_id: TenantId | None = None) -> Tenant:
        """Un client par son identifiant ; ``None`` désigne ``default``."""
        wanted = tenant_id or DEFAULT_TENANT
        found = self._tenants.get(wanted)
        if found is None:
            raise UnknownTenant(wanted, self._declared)
        return found

    def _resolve(self, spec: TenantSpec | None) -> Tenant:
        tenant_id = spec.id if spec is not None else DEFAULT_TENANT
        config = self._config if spec is None else _mapped(self._config, spec)
        return Tenant(
            id=tenant_id,
            config=config,
            secrets=self._secrets.secrets(tenant_id),
            spec=spec,
        )


def _mapped(config: LoomConfig, spec: TenantSpec) -> LoomConfig:
    """Config vue par un client : ses modèles à la place de ceux de la racine."""
    if not spec.models:
        return config
    by_id = {model.id: model for model in config.models}
    models: list[ModelSpec] = []
    for model in config.models:
        target = spec.models.get(model.id)
        replaced = model if target is None else by_id[target].model_copy(update={"id": model.id})
        models.append(replaced)
    resolved = config.model_copy(update={"models": tuple(models)})
    try:
        resolved.check()
    except ValueError as exc:
        raise ConfigError(f"Client {spec.id!r} : {exc}") from exc
    return resolved
