# loom_ia.tools_builtin.memory_params

Module `loom_ia.tools_builtin.memory_params` de loom_v2 (fichier `src/loom_ia/tools_builtin/memory_params.py`).

Paramètres de `loom.memory` : la base, le projet, les modèles et les actions permises.

Module léger — Pydantic seulement — : la config l'importe pour valider la
référence au chargement, sans importer l'outil, ni torch, ni transformers.

    - python: loom.memory
      store: { path: memoire/agent.db }        # ou { path_env: MEMOIRE_BASE }
      project: documentation                   # fixé : le modèle ne le choisit pas
      folders:                                 # pour add_file seulement
      - { name: workspace, path: /home/denis/temp/agent/workspace }
      models: { device: cpu, preload: true, offline: true }
\      # ou, calcul confié à loom-mem-gpu : models: { server: { url: http://127.0.0.1:8765 } }
      retrieval: { rerank_candidates: 8 }
      actions:
        search: {}
        get: {}
        add_text: {}                           # écriture : approbation always par défaut
        add_file: {}
        update: {}
        delete: {}

`actions` liste les actions **permises** ; une action absente n'est ni
montrée au modèle ni exécutée. Chacune prend son approbation par défaut :
`never` pour une lecture (`search`, `get`, `list_docs`, `projects`),
`always` pour une écriture (`add_text`, `add_url`, `add_file`,
`update`, `delete`). `{approval: never}` ou `{approval: always}` la
change. Une liste (`actions: [search, get]`) vaut des actions aux défauts.

`store` désigne le fichier SQLite de la mémoire : `path`, absolu ou
relatif au fichier de config, ou `path_env`, la variable qui le porte — lue
dans les secrets du client, pour qu'un client ait sa base. L'outil crée le
fichier s'il manque, jamais son dossier.

`models.server` confie les calculs à `loom-mem-gpu`, qui garde les modèles
en VRAM (`docs/loom-mem-gpu.md`) : `device`, `fp16`, `offline` et
`preload` se règlent alors sur l'application, et sont refusés ici.

`project` fixé, le modèle n'a pas d'argument `project` : tout ce qu'il
écrit va dans ce projet, et il ne lit, ne modifie ni ne supprime rien d'un
autre. Sans lui, le modèle donne le projet à chaque écriture et peut filtrer
ses recherches.

## Constantes de `loom_ia.tools_builtin.memory_params`

- `MemoryAction = Literal['search', 'get', 'list_docs', 'projects', 'add_text', 'add_url', 'add_file', 'update', 'delete']` : Action de `loom.memory`.
- `ActionApproval = Literal['never', 'always']` : Approbation d'une action : `never` ou `always`.
- `ACTIONS = ('search', 'get', 'list_docs', 'projects', 'add_text', 'add_url', 'add_file', 'update', 'delete')` : Dans l'ordre où on les dit.
- `READS = frozenset({'search', 'get', 'list_docs', 'projects'})` : Les actions qui lisent.
- `DEFAULT_APPROVAL = {action: 'never' if action in READS else 'always' for action in ACTIONS}` : Approbation par défaut : `never` pour une lecture, `always` pour une écriture.
- `PROJECT_PATTERN = '^[a-z0-9][a-z0-9_.-]{0,63}$'` : Un projet s'écrit en minuscules : l'outil range et cherche les projets ainsi.
- `MODELS_EXTRA = 'memory'` : L'extra de loom-ia qui apporte les vrais modèles.
- `DEVICE_PATTERN = '^(auto|cpu|mps|cuda(:[0-9]+)?)$'` : Le device des modèles, et les défauts des réglages de calcul : une seule définition pour `models`, `server.fallback` et les réglages de loom-mem-gpu.
- `DEFAULT_DEVICE = 'auto'` : Device des modèles sans réglage : choisi selon la machine.
- `DEFAULT_FP16 = True` : Calcul en demi-précision, sans réglage.
- `DEFAULT_OFFLINE = False` : Modèles téléchargés au besoin, sans réglage.
- `_SET_BY_SERVER = ('device', 'fp16', 'offline')` : Ce que `server` règle sur l'application : refusé à côté de lui.

## Classe `loom_ia.tools_builtin.memory_params.StoreSpec`

```python
class StoreSpec(BaseModel)
```

Le fichier SQLite de la mémoire : son chemin, ou la variable qui le porte.

**Attributs :**

- `path` : Chemin du fichier : absolu, ou relatif au dossier du fichier de config. Ni « ~ » ni variable.
- `path_env` : Variable d'environnement qui porte le chemin, lue dans les secrets du client.

## Classe `loom_ia.tools_builtin.memory_params.MemoryFolder`

```python
class MemoryFolder(BaseModel)
```

Un dossier où `add_file` peut lire : son nom pour le modèle, son chemin.

**Attributs :**

- `name` : Nom du dossier pour le modèle.
- `path` : Absolu, ou relatif au dossier du fichier de config. Ni « ~ » ni variable.

## Fonction `loom_ia.tools_builtin.memory_params.is_loopback`

```python
def is_loopback(host: str) -> bool
```

Vrai pour une adresse de la boucle locale (ou `localhost`).

## Fonction `loom_ia.tools_builtin.memory_params.server_url_problem`

```python
def server_url_problem(url: str) -> str | None
```

Pourquoi l'adresse de `loom-mem-gpu` est refusée, ou `None`.

`http://` sur la boucle locale seulement : l'accès par le réseau (https,
jeton obligatoire) viendra plus tard.

## Classe `loom_ia.tools_builtin.memory_params.FallbackSpec`

```python
class FallbackSpec(BaseModel)
```

Le repli dans loom quand `loom-mem-gpu` ne répond pas : où calculer, et comment.

**Attributs :**

- `device` : Où calculer : `auto`, `cpu`, `mps` ou `cuda[:n]` ; même défaut que dans `models`.
- `fp16` : Demi-précision sur GPU ; même défaut que dans `models`.
- `offline` : Modèles lus dans le cache local seulement ; même défaut que dans `models`.

## Classe `loom_ia.tools_builtin.memory_params.ServerSpec`

```python
class ServerSpec(BaseModel)
```

`loom-mem-gpu` : l'application qui garde les modèles en VRAM et calcule pour loom.

**Attributs :**

- `url` : Adresse de l'application.
- `url_env` : Variable qui porte l'adresse, lue dans les secrets du client.
- `token_env` : La variable qui porte le jeton, s'il en faut un ; jamais le jeton lui-même.
- `connect_timeout` : Délai de connexion, en secondes : une application arrêtée ne bloque pas chaque appel.
- `read_timeout` : Délai de réponse, en secondes : un lot, au plus.
- `fallback` : Calcul dans ce process quand l'application ne répond pas ; absent : pas de repli, une erreur qui dit comment la démarrer.

### Méthode `loom_ia.tools_builtin.memory_params.ServerSpec.secret_names`

```python
def secret_names(self) -> tuple[str, ...]
```

Les variables que le serveur lit : `url_env` et `token_env`.

## Classe `loom_ia.tools_builtin.memory_params.ModelsSpec`

```python
class ModelsSpec(BaseModel)
```

Les modèles d'embedding et de reranking, partagés par tout le process.

**Attributs :**

- `kind` : `bge-m3`, ou `fake` : modèles factices et déterministes, sans torch (essais, démonstrations).
- `embedder` : Modèle d'embedding : nom Hugging Face ou dossier local.
- `reranker` : Modèle de reranking : nom Hugging Face ou dossier local.
- `device` : `auto` : cuda si la carte est prise en charge, sinon mps, sinon cpu.
- `fp16` : Demi-précision sur GPU ; sans effet sur CPU, qui calcule en float32.
- `preload` : Charger les modèles en tâche de fond dès le montage de l'agent, au lieu du premier appel.
- `offline` : Lire les modèles dans le cache local de Hugging Face seulement : aucune requête vers le Hub. Un modèle absent du cache fait échouer le chargement (`hf download` l'y met).
- `server` : Calculs confiés à loom-mem-gpu ; `embedder` et `reranker` sont alors les modèles qu'elle doit annoncer.

### Méthode `loom_ia.tools_builtin.memory_params.ModelsSpec.fallback`

```python
def fallback(self) -> ModelsSpec | None
```

Les modèles du repli dans loom, réglés par `server.fallback` ; `None` sans repli.

## Classe `loom_ia.tools_builtin.memory_params.RetrievalSpec`

```python
class RetrievalSpec(BaseModel)
```

Réglages de la recherche : candidats, reranking, extraits.

**Attributs :**

- `prefetch_limit` : Candidats retenus par chacune des deux recherches (dense, lexicale) avant fusion.
- `rerank_candidates` : Candidats passés au reranker, le poste de coût dominant sur CPU.
- `max_chunks_per_doc` : Passages rendus au plus par document.
- `snippet_chars` : Longueur de l'extrait rendu quand le passage est trop long pour être rendu entier.
- `full_chunk_chars` : En dessous de cette longueur, search rend le passage entier.
- `min_score` : Score du reranker (0 à 1) sous lequel un passage n'est pas rendu.

## Classe `loom_ia.tools_builtin.memory_params.ChunkingSpec`

```python
class ChunkingSpec(BaseModel)
```

Découpage des documents, en caractères (environ 4 par token en français).

**Attributs :**

- `target_chars` : Longueur visée d'un passage.
- `max_chars` : Longueur au plus d'un passage.
- `overlap_chars` : Recouvrement entre deux passages qui se suivent.
- `min_chars` : Longueur au moins d'un passage.

## Classe `loom_ia.tools_builtin.memory_params.ActionSpec`

```python
class ActionSpec(BaseModel)
```

Une action permise, et son approbation si elle n'est pas celle par défaut.

**Attributs :**

- `approval` : Approbation de l'action ; absente : celle par défaut (`never` pour une lecture, `always` pour une écriture).

## Classe `loom_ia.tools_builtin.memory_params.MemoryParams`

```python
class MemoryParams(BaseModel)
```

Paramètres de `loom.memory`.

**Attributs :**

- `store` : Le fichier SQLite de la mémoire.
- `project` : Projet où l'outil range et cherche ; en minuscules.
- `folders` : Dossiers où `add_file` peut lire, de noms distincts.
- `models` : Modèles d'embedding et de reranking.
- `retrieval` : Réglages de la recherche.
- `chunking` : Découpage des documents.
- `actions` : Actions permises (`search`, `get`, `list_docs`, `projects`, `add_text`, `add_url`, `add_file`, `update`, `delete`), et l'approbation de chacune.

### Méthode `loom_ia.tools_builtin.memory_params.MemoryParams.declared`

```python
def declared(self) -> tuple[MemoryAction, ...]
```

Les actions permises, dans l'ordre où on les dit.

### Méthode `loom_ia.tools_builtin.memory_params.MemoryParams.approval`

```python
def approval(self, action: MemoryAction) -> ActionApproval
```

L'approbation de l'action : celle de la config, sinon celle par défaut.

### Méthode `loom_ia.tools_builtin.memory_params.MemoryParams.secret_names`

```python
def secret_names(self) -> tuple[str, ...]
```

Les variables que l'outil lit : un client peut les rediriger (`secrets`).
