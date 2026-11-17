# loom_ia.tenancy.secrets

Module `loom_ia.tenancy.secrets` de loom_v2 (fichier `src/loom_ia/tenancy/secrets.py`).

Secrets par client, lus dans l'environnement (fonctions L2 et M3, conception §16.3).

La configuration ne nomme que des variables : `api_key_env` d'un modèle,
`env_from` et `headers_env` d'un serveur MCP. Un client redirige ces
noms vers **ses** variables (`secrets: {CRM_TOKEN: DUPONT_CRM_TOKEN}`), et
c'est la table résolue — et elle seule — qui descend au montage de ses
agents.

Ce qu'un client ne redirige pas reste lu dans l'environnement commun : sans
cela, la première config multi-clients casserait sur `ANTHROPIC_API_KEY`.
Mais une redirection vers une variable **absente** ne retombe pas sur le
nom d'origine : elle vaut vide. Sinon un client dont le secret manque
emprunterait celui de tout le monde — exactement la fuite que la
redirection est là pour empêcher.

Quand la config déclare des clients, la table d'un client ne contient que ce
que la configuration **nomme** — `api_key_env` des modèles, `env_from` et
`headers_env` des serveurs MCP, secrets de `storage.encryption`, `dsn_env`
des bases de `loom.sql` — et les
noms que les clients redirigent (celui qui ne redirige pas l'un d'eux lit la
variable commune du même nom) : pas le reste de l'environnement, où dupont
verrait `MARTIN_CRM_TOKEN`. Elle est figée et en lecture seule, car elle
part aussi aux fabriques de paquets tiers (`tool_sources`). Sans clients
déclarés, il n'y a personne à isoler : la table est l'environnement, en
lecture seule.

## Classe `loom_ia.tenancy.secrets.EnvironmentSecrets`

```python
class EnvironmentSecrets
```

`SecretProvider` adossé à l'environnement du process.

**Arguments :**

- `config` : La configuration : clients déclarés et variables qu'elle nomme.
- `environ` : Environnement à lire ; `os.environ` par défaut.

### Méthode `loom_ia.tenancy.secrets.EnvironmentSecrets.secrets`

```python
def secrets(self, tenant_id: TenantId) -> Mapping[str, str]
```

Table des secrets du client, calculée une fois puis gardée.

**Arguments :**

- `tenant_id` : Le client.

**Renvoie :**

Une table en lecture seule : nom de variable vers valeur, noms
redirigés compris (vides si la variable cible est absente) ;
l'environnement entier sans clients déclarés.

## Fonction `loom_ia.tenancy.secrets._named`

```python
def _named(config: LoomConfig) -> Iterator[str]
```

Les variables que la configuration nomme, et que les adaptateurs viennent lire.

Les noms que les clients redirigent en font partie : un client qui ne
redirige pas `CRM_TOKEN` lit la variable commune du même nom.

## Fonction `loom_ia.tenancy.secrets._builtin_secrets`

```python
def _builtin_secrets(config: LoomConfig) -> Iterator[str]
```

Les variables que nomment les paramètres des outils fournis (`dsn_env` de loom.sql).

Un modèle de paramètres qui lit des secrets le dit par `secret_names()` ;
seuls les modules légers des paramètres sont importés ici.
