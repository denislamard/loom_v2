# loom_ia.adapters.models.factory

Module `loom_ia.adapters.models.factory` de loom_v2 (fichier `src/loom_ia/adapters/models/factory.py`).

Fabrique des clients de modèles : le champ `sdk` choisit l'adaptateur (point 9).

Les SDK sont des extras, importés seulement quand un modèle les utilise. La
clé est lue dans la variable nommée par `api_key_env` ; elle n'apparaît
jamais dans les messages d'erreur ni dans les logs.

## Classe `loom_ia.adapters.models.factory.ModelConfigError`

```python
class ModelConfigError(ValueError)
```

Définition de modèle inutilisable en l'état (extra manquant, clé absente…).

## Fonction `loom_ia.adapters.models.factory.create_model_client`

```python
def create_model_client(spec: ModelSpec, *, environ: Mapping[str, str] | None=None, http_client: httpx2.AsyncClient | None=None, record: bool=False) -> ModelClient
```

Client du modèle `spec`.

`http_client` remplace le client HTTP du SDK (tests, proxy). `record`
lui donne un client qui garde ses échanges bruts (jalon J6.1b) — dans le
registre que le moteur ouvre, et seulement là ; le modèle simulé, qui n'a
pas d'HTTP, y dépose un échange synthétique de lui-même.

## Fonction `loom_ia.adapters.models.factory._http`

```python
def _http(given: httpx2.AsyncClient | None, record: bool) -> httpx2.AsyncClient | None
```

Le client HTTP à passer au SDK : celui donné, sinon un client qui garde, sinon aucun.
