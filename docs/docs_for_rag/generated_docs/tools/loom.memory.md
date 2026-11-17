# Outil fourni loom.memory

L'outil `loom.memory` est fourni avec loom_v2. Un agent l'active dans son `agent.yaml` : `- python: loom.memory` dans `tools`.

## Ce que fait loom.memory

Outil memory : une mémoire long terme où l'agent cherche et range des documents.

Recherche hybride (dense et lexicale, puis reranking) sur une base SQLite,
avec les modèles BGE-M3 (extra `memory`) ou des modèles factices. Le modèle
agit par une `action` parmi celles que la config permet ; les écritures
attendent une approbation humaine par défaut. Ses paramètres sont dans
`memory_params`, module léger que la config importe seul ; ce paquet n'est
importé qu'au montage d'un agent qui active l'outil.

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

## Comment loom.memory est monté

- cible : `loom_ia.tools_builtin.memory:build`
- extra de loom-ia à installer : `loom-ia[memory]`
- réglages communs refusés sur sa référence : `approval`, `side_effects`, `idempotent`, car chaque action permise a son approbation (actions.<action>.approval), et ses effets tiennent à l'action
- l'outil tire sa description de ses réglages : `description` est refusé, `skill` ajoute du texte

## Les paramètres de loom.memory dans agent.yaml (réglages sous sa référence)

Les paramètres de `loom.memory` sont des clés de sa référence dans `tools`, à côté de `python: loom.memory`.

### `tools[]` (référence `python: loom.memory`)

Paramètres de `loom.memory`.

Modèle Pydantic : `loom_ia.tools_builtin.memory_params.MemoryParams`.

- `tools[].store` (type `StoreSpec` ; obligatoire) : Le fichier SQLite de la mémoire.
- `tools[].project` (type `str | null` ; défaut : `null` ; bornes : motif `^[a-z0-9][a-z0-9_.-]{0,63}$`) : Projet où l'outil range et cherche ; en minuscules.
- `tools[].folders` (type `tuple[MemoryFolder, ...]` ; défaut : `[]`) : Dossiers où `add_file` peut lire, de noms distincts.
- `tools[].models` (type `ModelsSpec` ; défaut : les défauts de la section (`kind: "bge-m3"`, `embedder: "BAAI/bge-m3"`, `reranker: "BAAI/bge-reranker-v2-m3"`, `device: "auto"`, `fp16: true`, `preload: false`, `offline: false`)) : Modèles d'embedding et de reranking.
- `tools[].retrieval` (type `RetrievalSpec` ; défaut : les défauts de la section (`prefetch_limit: 15`, `rerank_candidates: 20`, `max_chunks_per_doc: 2`, `snippet_chars: 300`, `full_chunk_chars: 1000`, `min_score: 0.1`)) : Réglages de la recherche.
- `tools[].chunking` (type `ChunkingSpec` ; défaut : les défauts de la section (`target_chars: 1800`, `max_chars: 2400`, `overlap_chars: 180`, `min_chars: 200`)) : Découpage des documents.
- `tools[].actions` (type `dict[MemoryAction, ActionSpec]` ; obligatoire) : Actions permises (`search`, `get`, `list_docs`, `projects`, `add_text`, `add_url`, `add_file`, `update`, `delete`), et l'approbation de chacune.

### `tools[].store`

Le fichier SQLite de la mémoire : son chemin, ou la variable qui le porte.

Modèle Pydantic : `loom_ia.tools_builtin.memory_params.StoreSpec`.

- `tools[].store.path` (type `Path | null` ; défaut : `null`) : Chemin du fichier : absolu, ou relatif au dossier du fichier de config. Ni « ~ » ni variable.
- `tools[].store.path_env` (type `str | null` ; défaut : `null` ; bornes : motif `^[A-Za-z_][A-Za-z0-9_]{0,127}$`) : Variable d'environnement qui porte le chemin, lue dans les secrets du client.

### `tools[].folders[]`

Un dossier où `add_file` peut lire : son nom pour le modèle, son chemin.

Modèle Pydantic : `loom_ia.tools_builtin.memory_params.MemoryFolder`.

- `tools[].folders[].name` (type `str` ; obligatoire ; bornes : motif `^[a-z0-9][a-z0-9_-]{0,31}$`) : Nom du dossier pour le modèle.
- `tools[].folders[].path` (type `Path` ; obligatoire) : Absolu, ou relatif au dossier du fichier de config. Ni « ~ » ni variable.

### `tools[].models`

Les modèles d'embedding et de reranking, partagés par tout le process.

Modèle Pydantic : `loom_ia.tools_builtin.memory_params.ModelsSpec`.

- `tools[].models.kind` (type `"bge-m3" | "fake"` ; défaut : `"bge-m3"`) : `bge-m3`, ou `fake` : modèles factices et déterministes, sans torch (essais, démonstrations).
- `tools[].models.embedder` (type `str` ; défaut : `"BAAI/bge-m3"` ; bornes : longueur ≥ 1) : Modèle d'embedding : nom Hugging Face ou dossier local.
- `tools[].models.reranker` (type `str` ; défaut : `"BAAI/bge-reranker-v2-m3"` ; bornes : longueur ≥ 1) : Modèle de reranking : nom Hugging Face ou dossier local.
- `tools[].models.device` (type `str` ; défaut : `"auto"` ; bornes : motif `^(auto|cpu|mps|cuda(:[0-9]+)?)$`) : `auto` : cuda si la carte est prise en charge, sinon mps, sinon cpu.
- `tools[].models.fp16` (type `bool` ; défaut : `true`) : Demi-précision sur GPU ; sans effet sur CPU, qui calcule en float32.
- `tools[].models.preload` (type `bool` ; défaut : `false`) : Charger les modèles en tâche de fond dès le montage de l'agent, au lieu du premier appel.
- `tools[].models.offline` (type `bool` ; défaut : `false`) : Lire les modèles dans le cache local de Hugging Face seulement : aucune requête vers le Hub. Un modèle absent du cache fait échouer le chargement (`hf download` l'y met).
- `tools[].models.server` (type `ServerSpec | null` ; défaut : `null`) : Calculs confiés à loom-mem-gpu ; `embedder` et `reranker` sont alors les modèles qu'elle doit annoncer.

### `tools[].models.server`

`loom-mem-gpu` : l'application qui garde les modèles en VRAM et calcule pour loom.

Modèle Pydantic : `loom_ia.tools_builtin.memory_params.ServerSpec`.

- `tools[].models.server.url` (type `str | null` ; défaut : `null`) : Adresse de l'application.
- `tools[].models.server.url_env` (type `str | null` ; défaut : `null` ; bornes : motif `^[A-Za-z_][A-Za-z0-9_]{0,127}$`) : Variable qui porte l'adresse, lue dans les secrets du client.
- `tools[].models.server.token_env` (type `str | null` ; défaut : `null` ; bornes : motif `^[A-Za-z_][A-Za-z0-9_]{0,127}$`) : La variable qui porte le jeton, s'il en faut un ; jamais le jeton lui-même.
- `tools[].models.server.connect_timeout` (type `float` ; défaut : `2.0` ; bornes : > 0) : Délai de connexion, en secondes : une application arrêtée ne bloque pas chaque appel.
- `tools[].models.server.read_timeout` (type `float` ; défaut : `60.0` ; bornes : > 0) : Délai de réponse, en secondes : un lot, au plus.
- `tools[].models.server.fallback` (type `FallbackSpec | null` ; défaut : `null`) : Calcul dans ce process quand l'application ne répond pas ; absent : pas de repli, une erreur qui dit comment la démarrer.

### `tools[].models.server.fallback`

Le repli dans loom quand `loom-mem-gpu` ne répond pas : où calculer, et comment.

Modèle Pydantic : `loom_ia.tools_builtin.memory_params.FallbackSpec`.

- `tools[].models.server.fallback.device` (type `str` ; défaut : `"auto"` ; bornes : motif `^(auto|cpu|mps|cuda(:[0-9]+)?)$`) : Où calculer : `auto`, `cpu`, `mps` ou `cuda[:n]` ; même défaut que dans `models`.
- `tools[].models.server.fallback.fp16` (type `bool` ; défaut : `true`) : Demi-précision sur GPU ; même défaut que dans `models`.
- `tools[].models.server.fallback.offline` (type `bool` ; défaut : `false`) : Modèles lus dans le cache local seulement ; même défaut que dans `models`.

### `tools[].retrieval`

Réglages de la recherche : candidats, reranking, extraits.

Modèle Pydantic : `loom_ia.tools_builtin.memory_params.RetrievalSpec`.

- `tools[].retrieval.prefetch_limit` (type `int` ; défaut : `15` ; bornes : > 0) : Candidats retenus par chacune des deux recherches (dense, lexicale) avant fusion.
- `tools[].retrieval.rerank_candidates` (type `int` ; défaut : `20` ; bornes : > 0) : Candidats passés au reranker, le poste de coût dominant sur CPU.
- `tools[].retrieval.max_chunks_per_doc` (type `int` ; défaut : `2` ; bornes : > 0) : Passages rendus au plus par document.
- `tools[].retrieval.snippet_chars` (type `int` ; défaut : `300` ; bornes : > 0) : Longueur de l'extrait rendu quand le passage est trop long pour être rendu entier.
- `tools[].retrieval.full_chunk_chars` (type `int` ; défaut : `1000` ; bornes : > 0) : En dessous de cette longueur, search rend le passage entier.
- `tools[].retrieval.min_score` (type `float` ; défaut : `0.1` ; bornes : ≥ 0.0, ≤ 1.0) : Score du reranker (0 à 1) sous lequel un passage n'est pas rendu.

### `tools[].chunking`

Découpage des documents, en caractères (environ 4 par token en français).

Modèle Pydantic : `loom_ia.tools_builtin.memory_params.ChunkingSpec`.

- `tools[].chunking.target_chars` (type `int` ; défaut : `1800` ; bornes : > 0) : Longueur visée d'un passage.
- `tools[].chunking.max_chars` (type `int` ; défaut : `2400` ; bornes : > 0) : Longueur au plus d'un passage.
- `tools[].chunking.overlap_chars` (type `int` ; défaut : `180` ; bornes : ≥ 0) : Recouvrement entre deux passages qui se suivent.
- `tools[].chunking.min_chars` (type `int` ; défaut : `200` ; bornes : ≥ 0) : Longueur au moins d'un passage.

### `tools[].actions.<nom>`

Une action permise, et son approbation si elle n'est pas celle par défaut.

Modèle Pydantic : `loom_ia.tools_builtin.memory_params.ActionSpec`.

- `tools[].actions.<nom>.approval` (type `ActionApproval | null` ; défaut : `null`) : Approbation de l'action ; absente : celle par défaut (`never` pour une lecture, `always` pour une écriture).

## Les arguments que le modèle donne à loom.memory

- `action` (`"search" | "get" | "list_docs" | "projects" | "add_text" | "add_url" | "add_file" | "update" | "delete"` ; obligatoire) : search : chercher ; get : lire un document entier ; list_docs : derniers documents ; projects : projets et nombre de documents ; add_text, add_url, add_file : mémoriser un texte, une page web, un fichier ; update : remplacer le texte d'un document ; delete : supprimer un document.
- `query` (`string | null`) : Pour search : la question, ou des mots-clés.
- `k` (`integer`) : Pour search : nombre de résultats.
- `tags` (`array[string] | null`) : Pour search : ne garder que les documents qui portent tous ces tags ; pour un ajout : les tags du document.
- `project` (`string | null`) : Projet du document : obligatoire pour un ajout ; pour search et list_docs, restreint au projet (absent : tous les projets).
- `doc_id` (`string | null`) : Pour get, update, delete : l'identifiant du document.
- `text` (`string | null`) : Pour add_text : le texte à mémoriser ; pour update : le texte entier.
- `title` (`string | null`) : Pour add_text : un titre court et explicite.
- `url` (`string | null`) : Pour add_url : l'adresse http(s).
- `file` (`string | null`) : Pour add_file : « <dossier>/<chemin> » d'un fichier texte, Word ou PDF (les formats lus sont dans la description de l'outil).
- `n` (`integer`) : Pour list_docs : nombre de documents.
