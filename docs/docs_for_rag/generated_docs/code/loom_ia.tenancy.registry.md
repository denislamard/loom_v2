# loom_ia.tenancy.registry

Module `loom_ia.tenancy.registry` de loom_v2 (fichier `src/loom_ia/tenancy/registry.py`).

Clients : ce qu'ils surchargent, et la config qui en découle (fonction L1, points 33 et 34).

Un client est toujours présent ; en mode librairie, c'est `default`, et il
ne surcharge rien (point 33). Dès que la config déclare des `tenants`, la liste
est **fermée** : un client qu'elle ne nomme pas est refusé, plutôt que servi
en silence avec les réglages de tout le monde.

Deux surcharges sont appliquées **dans la configuration** que le client voit,
plutôt que portées à côté : ses modèles et ses budgets. Les autres — agents
ouverts, outils retirés, approbations imposées, secrets, variables, stockage —
sont des autorisations et des valeurs, et voyagent avec `Tenant`.

La correspondance des modèles est appliquée **sur la définition** du modèle,
pas sur les références qui le citent : dans la config d'un client,
l'identifiant `M3_MAIN` désigne le modèle qu'il a choisi. Un seul endroit
à réécrire, et la correspondance vaut donc partout d'un coup —
orchestrateur, rôles, juges, chaînes de secours, compaction — sans qu'aucun
agent ne change. La config ainsi obtenue repasse les contrôles de cohérence
(fonction M5) : un remplacement qui ne sait pas appeler d'outils ou lire une image
est refusé au démarrage, pour le client qui le demande.

Ses budgets suivent le même chemin : le `budgets` de la racine, surchargé
clé par clé par celui du client, remplace celui de la config résolue. Un agent
monté pour ce client reçoit donc les bons plafonds de run et de session sans
que `build_agent` ait à connaître les clients, et les plafonds par période
(`budgets.tenant`, jalon J5.1b) voyagent au même endroit.

Config : `tenants` (`id`, `agents`, `tools_deny`, `models`,
`approvals`, `secrets`, `variables`, `budgets`, `quotas`,
`storage`, `retention`, `telemetry`).

## Classe `loom_ia.tenancy.registry.UnknownTenant`

```python
class UnknownTenant(Unknown)
```

Aucun client de cet identifiant dans la configuration.

## Classe `loom_ia.tenancy.registry.Tenant`

```python
@dataclass(frozen=True, slots=True)
class Tenant
```

Un client, et tout ce qui le distingue au montage de ses agents.

**Attributs :**

- `id` : Identifiant du client.
- `config` : Configuration vue par ce client : la correspondance des modèles et ses budgets appliqués.
- `secrets` : Table de ses secrets, telle qu'elle descend aux adaptateurs (fonction L2).
- `spec` : Sa fiche dans `tenants` ; `None` pour `default` sans clients déclarés.
- `provided` : Vrai quand la table vient d'un gestionnaire de secrets passé par l'application (`Loom(..., secrets=...)`), pas de l'environnement : un secret absent ne se dit alors pas « variable d'environnement » (fonction M3).

### Méthode `loom_ia.tenancy.registry.Tenant.allows`

```python
def allows(self, agent: str) -> bool
```

Vrai si ce client peut lancer cet agent (fonction L1).

**Arguments :**

- `agent` : Nom de l'agent.

### Méthode `loom_ia.tenancy.registry.Tenant.denied`

```python
@property
def denied(self) -> frozenset[str]
```

Outils retirés à ce client, sous le nom que voit le modèle.

### Méthode `loom_ia.tenancy.registry.Tenant.approvals`

```python
@property
def approvals(self) -> Mapping[str, Approval]
```

Approbations imposées par ce client, par nom d'outil (point 17).

### Méthode `loom_ia.tenancy.registry.Tenant.variables`

```python
@property
def variables(self) -> Mapping[str, JsonValue]
```

Valeurs des variables citées par les prompts (conception §6).

### Méthode `loom_ia.tenancy.registry.Tenant.storage`

```python
@property
def storage(self) -> StorageConfig | None
```

Stockage propre à ce client ; `None` s'il partage celui de la racine.

### Méthode `loom_ia.tenancy.registry.Tenant.budget`

```python
@property
def budget(self) -> TenantBudget
```

Plafonds de ce client par période (jalon J5.1b) ; déjà fusionnés dans sa config.

### Méthode `loom_ia.tenancy.registry.Tenant.quotas`

```python
@property
def quotas(self) -> Quotas
```

Débit accordé à ce client (fonction L3).

## Classe `loom_ia.tenancy.registry.Tenants`

```python
class Tenants
```

Les clients d'une configuration, résolus une fois pour toutes.

La résolution a lieu à la construction : une correspondance de modèles
incohérente est une erreur de démarrage, pas une erreur au premier run
du client concerné (fonction M1).

**Arguments :**

- `config` : La configuration.
- `environ` : Environnement à lire pour les secrets ; `os.environ` par défaut.
- `secrets` : Gestionnaire de secrets de l'application ; à défaut, les secrets sont lus dans l'environnement (`EnvironmentSecrets`).

**Lève :**

- `ConfigError` : Une correspondance de modèles rend la config d'un client incohérente.

### Méthode `loom_ia.tenancy.registry.Tenants.__init__`

```python
def __init__(self, config: LoomConfig, *, environ: Mapping[str, str] | None=None, secrets: SecretProvider | None=None) -> None
```

Résout chaque client : sa config, sa table de secrets.

### Méthode `loom_ia.tenancy.registry.Tenants.ids`

```python
@property
def ids(self) -> tuple[TenantId, ...]
```

Clients déclarés ; `default` seul quand la config n'en nomme aucun.

### Méthode `loom_ia.tenancy.registry.Tenants.closed`

```python
@property
def closed(self) -> bool
```

Vrai si la config nomme ses clients : un autre est alors refusé.

### Méthode `loom_ia.tenancy.registry.Tenants.all`

```python
def all(self) -> tuple[Tenant, ...]
```

Tous les clients résolus.

### Méthode `loom_ia.tenancy.registry.Tenants.get`

```python
def get(self, tenant_id: TenantId | None=None) -> Tenant
```

Un client par son identifiant ; `None` désigne `default`.

**Arguments :**

- `tenant_id` : Le client.

**Lève :**

- `UnknownTenant` : Client que la config ne nomme pas.

## Fonction `loom_ia.tenancy.registry._resolved`

```python
def _resolved(config: LoomConfig, spec: TenantSpec) -> LoomConfig
```

Config vue par un client : ses modèles et ses budgets à la place de ceux de la racine.

## Fonction `loom_ia.tenancy.registry._mapped`

```python
def _mapped(config: LoomConfig, spec: TenantSpec) -> tuple[ModelSpec, ...]
```

Modèles de la config, ceux que le client remplace pris à leur cible.
