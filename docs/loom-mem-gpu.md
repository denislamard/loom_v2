# loom-mem-gpu — garder les modèles de la mémoire en VRAM

*Conception du 05/11/2026, avant de coder. Ce document reprend et précise le point 9 de `loom-memory-points-en-suspens.md` (serveur de modèles). Le cas visé est le GPU du poste, une RTX 5060 Ti de 16 Go. La VM cloud vient dans un second temps (lot 5). L'application sert **BGE-M3 et son reranker, et rien d'autre**.*

---

## 1. Besoin

Chaque process loom qui se sert de `loom.memory` charge lui-même BGE-M3 et bge-reranker-v2-m3 : `loom run`, `loom approve`, `loom memory …`, et `loom serve --reload` à chaque relance. Sur GPU, le chargement prend environ 15 s (mesure du 03/11) pour environ 0,5 s de calcul utile, puis la VRAM est rendue à la sortie du process. Une recherche lancée depuis la CLI coûte donc 16 à 17 s.

`shared_models()` (`memory/models.py`) ne partage les modèles qu'**à l'intérieur d'un process**.

On veut **une application qui charge les modèles une seule fois et les garde en VRAM**, et que tous les process loom appellent. La config permet de garder le calcul dans loom ou de le confier à l'application.

## 2. Décisions

| # | Décision | Retenu |
|---|---|---|
| D1 | Périmètre de l'application | **Calcul seul.** La base SQLite, les projets, les tenants, `path_env` et les approbations restent dans loom. L'application ne voit jamais la base. |
| D2 | Bascule par la config | **Sous-clé `models.server`** de `loom.memory`. Sans elle, le comportement est celui d'aujourd'hui. |
| D3 | Réglages matériels (device, fp16, offline) | **Ils appartiennent à l'application.** Côté loom, ils ne servent qu'au repli local, et s'écrivent alors **dans `server.fallback`** : tout ce qui concerne le mode « application » se trouve sous la clé `server`. |
| D4 | Repli par défaut | **Aucun** (`server.fallback` absent) : une panne se voit. Le repli est un choix explicite de la config. |
| D5 | Forme et nom | **Une petite application à part**, dans les sources de loom : `loom_ia/tools_builtin/memory/server/`. Exécutable **`loom-mem-gpu`**, unité systemd `loom-mem-gpu.service`. |
| D5b | Serveur HTTP | **Bibliothèque standard** (`http.server.ThreadingHTTPServer`). Aucun extra en plus de `memory`. |
| D6 | Accès réseau (VM cloud) | **Plus tard** (lot 5). Ce chantier ne couvre que la boucle locale. |
| D7 | Version | **Gardée à 2.0.18.** |
| D8 | Modèles servis | **BGE-M3 et bge-reranker-v2-m3 seulement.** Ni profils de modèles ni interface commune à plusieurs familles : l'application réutilise `BgeM3Embedder` et `BgeReranker` tels quels. D'autres modèles ont été étudiés et écartés (§3, « Pistes écartées »). |
| D9 | Mesures préalables | **Aucune.** Le chargement et la recherche ne sont pas mesurés avant de coder. |

## 3. Faisabilité

Le découpage existe déjà. `MemoryService` ne connaît que `Models(embedder, reranker)`, qui suivent les protocoles `Embedder` et `Reranker` de `memory/models.py`. Il suffit d'en écrire une implémentation distante. Ni le service, ni la base, ni le découpage, ni la recherche (`retrieval.py`), ni l'approbation, ni la maintenance (`loom memory export | import | reindex | eval`) ne changent.

| Point | Constat | Conséquence |
|---|---|---|
| Carte Blackwell (sm_120) | **Vérifié le 05/11** : torch `2.15.0+cu132`, CUDA 13.2, architectures jusqu'à `sm_120`. Un produit matriciel fp16 a tourné sur la carte. | `device: cuda` est utilisable tel quel. |
| VRAM | 2 × 568 M de paramètres en fp16, soit environ 2,3 Go, plus le contexte CUDA et les activations (lots de 16 × 1024 tokens). | Environ 3 à 4 Go, ce qui laisse de l'ordre de 12 Go à un LLM local. **vLLM** réserve 90 % de la carte par défaut : lancer loom-mem-gpu d'abord, et régler `--gpu-memory-utilization` vers 0,7. Ollama ne pose pas ce problème. |
| Dépendances | Client : `httpx2`, déjà dans le noyau. Application : torch et transformers (extra `memory`) et la bibliothèque standard. | Aucune dépendance nouvelle, aucun extra nouveau. |
| Imports légers | Le client n'importe jamais torch. | Sans `server.fallback`, un process loom n'a plus besoin de l'extra `memory` et ne paie plus l'import de torch. |
| Vecteurs | Le dense est stocké en float32 (BLOB, `store.py`). Le même `bge_m3.py` tourne des deux côtés. | On transporte du float32 en base64 : aucune perte, environ 5,5 Ko par texte. |
| Lexical | BGE-M3 produit le lexical (identifiants de tokens et poids) dans le même passage du modèle que le dense. | L'application le renvoie avec le dense. La recherche lexicale reste dans loom (§4.1). |
| Annulation (point 2, fait en 2.0.17) | Aujourd'hui, le calcul s'arrête entre deux lots. | Le client envoie **une requête par lot de 16** et regarde `cancel` entre deux requêtes. La garantie est la même, sans annulation côté application. |
| Concurrence | Les verrous de `LazyModel` gardent chaque modèle. | Un seul process applicatif. Les lots de plusieurs clients s'entrelacent : un gros fichier ne bloque jamais une recherche plus longtemps qu'un lot. |

### Pistes écartées

- **Ollama** : seulement le dense (`/api/embed`), ni le lexical ni le reranker.
- **TEI, Infinity** : on n'y trouve pas le lexical de BGE-M3 tel que loom le calcule, d'où des vecteurs incompatibles.
- **Une application qui tiendrait toute la mémoire, base comprise** (comme loom-notes) : il faudrait sortir de loom les projets, les tenants, `path_env` et l'approbation.
- **Faire servir les runs par un `loom serve` permanent** : la CLI n'a pas de mode client, et `--reload` relance le process.
- **Qwen3-Embedding-4B et Qwen3-Reranker (profils de modèles nommés)**, étudiés le 05/11. Qwen3-Embedding ne fournit pas de lexical : il aurait fallu refaire la recherche par les mots dans loom (FTS5 et BM25, nouveau schéma, migration), réindexer toutes les bases, et ajouter une couche de profils et une interface commune. Il faut aussi environ 10 Go de VRAM au lieu de 3 à 4, et le repli sur CPU devient irréaliste. Aucun gain n'est démontré : BGE-M3 place le bon document en tête sur le corpus testé (scores de 0,84 à 0,996). **Condition pour y revenir** : un gain net mesuré par `loom memory eval` sur le jeu doré. Le numéro de protocole et l'`algo` annoncés par `/v1/health` (§5.3) permettraient alors d'en faire une version 2 sans casser les clients.

## 4. Architecture

```
loom run / approve / memory / serve / worker   (N process, sans torch)
  └─ loom.memory → MemoryService → Models(RemoteEmbedder, RemoteReranker)
                                        │  HTTP + JSON, une requête par lot de 16
                                        ▼  127.0.0.1:8765
                loom-mem-gpu            (1 process, unité systemd --user)
                  BgeM3Embedder + BgeReranker chargés une fois, fp16, cuda
```

### 4.1 Qui fait quoi

L'application **calcule** : des vecteurs (dense et lexical) pour des textes, des scores pour des paires (question, passage). Elle ne garde rien. loom **range et cherche** : la base, l'index en mémoire, les comparaisons, la fusion RRF, les seuils.

**Indexation** (`add_text`, `add_file`, `add_url`, `loom memory import | reindex`) :

```
loom                                                   loom-mem-gpu
 découpe le document en passages
 POST /v1/embed {texts: [16 passages]}          ──►    BgeM3Embedder : un passage du modèle par lot,
                                                        dense (1024) + lexical (tokens, poids)
 ◄── {items: [{dense: b64, sparse: {indices, values}}, …]}
 … lot suivant, jusqu'au dernier passage …
 écrit dans SQLite (chunks.dense, chunks.sparse), dans une seule transaction
```

**Recherche** (`search`, `loom memory eval`) :

```
loom                                                   loom-mem-gpu
 POST /v1/embed {texts: [question]}             ──►    dense + lexical de la question
 ◄──
 dans loom, sur l'index lu depuis SQLite (retrieval.py, inchangé) :
   dense   : produit scalaire question · passage
   lexical : somme des poids des tokens communs
   fusion RRF → rerank_candidates passages
 POST /v1/score {query, passages: [8 passages]} ──►    BgeReranker : un score par passage
 ◄── {scores: […]}
 tri, min_score, max_chunks_per_doc → résultats
```

Une recherche, c'est deux requêtes à l'application. La comparaison avec tous les passages de la base se fait dans loom, en quelques millisecondes, sans aller-retour réseau par passage.

### 4.2 Sources

```
src/loom_ia/tools_builtin/memory/
  protocol.py          contrat du fil : schémas Pydantic, PROTOCOL, plafonds, encodage du dense — partagé
  remote.py            client, côté loom : RemoteEmbedder, RemoteReranker, état de l'application, repli
  server/
    __init__.py        main() : point d'entrée de l'exécutable loom-mem-gpu
    __main__.py        python -m loom_ia.tools_builtin.memory.server
    app.py             les points d'accès (http.server)
    settings.py        réglages de l'application : options, variable du jeton, YAML facultatif
```

Dans `pyproject.toml`, section `[project.scripts]` : `loom-mem-gpu = "loom_ia.tools_builtin.memory.server:main"`. L'exécutable est installé avec loom-ia quels que soient les extras. Sans torch, il s'arrête en disant : « installer l'extra : loom-ia[memory] ».

### 4.3 Ce que « dissocié » garantit

- `memory/server` n'importe que `memory/models.py`, `memory/bge_m3.py`, `memory/protocol.py` et `memory_params.ModelsSpec`. Il n'importe ni la config, ni le runtime, ni access, ni engine, ni tenancy, ni sessions, ni agents.
- Deux vérifications en CI :
  - le contrat import-linter « Application loom-mem-gpu indépendante de loom » : `loom_ia.tools_builtin.memory.server` n'importe ni `loom_ia.access`, ni `runtime`, ni `config`, ni `engine`, ni `agents`, ni `tenancy`, ni `sessions`, ni `replay`, ni `telemetry`, ni `adapters` ;
  - « loom n'importe pas l'application » : un test (`test_nothing_in_loom_imports_the_application`) parcourt les sources et refuse tout import de `loom_ia.tools_builtin.memory.server` hors de l'application elle-même ; un contrat import-linter l'aurait dit par une liste de modules à tenir à jour. Seul `protocol.py` est partagé avec `remote.py`.
- À l'exécution, importer l'application charge aussi le paquet `memory` (son `__init__` monte l'outil : `core`, `tools`), mais rien des couches interdites, ni torch : un test le vérifie dans un process neuf.
- L'application ne lit jamais `loom.yaml`. Elle se déploie seule (`pip install 'loom-ia[memory]'`), sans projet loom.
- Le client et l'application viennent du même paquet : le protocole et l'algorithme sont alignés par construction. Un écart de version est détecté par `/v1/health` (§5.3).

## 5. L'application `loom-mem-gpu`

### 5.1 Ligne de commande

```
loom-mem-gpu [serve]  [--host 127.0.0.1] [--port 8765]
                      [--device cuda|cuda:N|cpu|auto] [--no-fp16] [--offline]
                      [--embedder BAAI/bge-m3] [--reranker BAAI/bge-reranker-v2-m3]
                      [--token-env VAR] [--max-vram-fraction F]
                      [--config fichier.yaml]
loom-mem-gpu status   [--url http://127.0.0.1:8765] [--token-env VAR]
```

- **Les réglages des modèles** sont validés par le même `ModelsSpec` qu'aujourd'hui (mêmes motifs, même `resolve_device`). **Une seule différence : le device vaut `cuda` par défaut**, au lieu d'`auto`. Une application « gpu » qui retomberait en silence sur le CPU serait un piège. Si la carte ne convient pas, le refus est clair (`UnsupportedDevice`). `--device cpu` reste permis.
- **`--embedder` et `--reranker`** acceptent un autre nom Hugging Face ou un dossier local, mais **de même nature** : un modèle de la famille BGE-M3 (avec `sparse_linear.pt`) et un reranker à classifieur de paires. C'est exactement ce que loom accepte déjà en calcul local.
- **`--config`** : un YAML facultatif, avec les mêmes clés que les options (`host`, `port`, `device`, `fp16`, `offline`, `embedder`, `reranker`, `token_env`, `max_vram_fraction`). Une option donnée l'emporte sur le fichier. Pour changer un réglage, on modifie le fichier ou l'unité systemd, puis on relance.
- **Le jeton** est lu dans la variable que nomme `--token-env`, jamais passé en argument (il serait visible dans `ps`). Il est facultatif sur la boucle locale et comparé en temps constant (`hmac.compare_digest`).
- **`--max-vram-fraction`** appelle `torch.cuda.set_per_process_memory_fraction`. C'est facultatif : ça plafonne l'application à côté d'un LLM local. Dépasser le plafond donne une erreur de calcul (500), pas un débordement sur le LLM.
- **`--host`** : la boucle locale seulement, tant que le lot 5 n'est pas fait. Toute autre adresse est refusée au démarrage.
- **`status`** interroge `/v1/health` et affiche l'état, les modèles, le device, la version et la VRAM occupée. Code de sortie : 0 si prêt, 1 sinon.

### 5.2 Démarrage et arrêt

1. Les réglages sont validés. En cas d'erreur, sortie avec le code 2.
2. **Le port est ouvert avant tout chargement.** S'il est pris, sortie avec le code 3, sans rien charger : la VRAM n'est jamais occupée deux fois.
3. Le serveur répond aussitôt. `/v1/health` renvoie 503 `{state: "loading"}` pendant le chargement.
4. Les deux modèles se chargent dans un thread, puis font un passage d'essai (`LazyModel`). La VRAM réservée par le process est journalisée (`torch.cuda.memory_reserved`). En cas d'échec, `/v1/health` renvoie 503 `{state: "failed", error}`, et l'application sort avec le code 4 au bout de quelques secondes, pour laisser à `status` le temps de lire l'erreur.
5. `{state: "ready"}` : les modèles ne sont plus jamais déchargés.
6. **SIGTERM ou SIGINT** : plus aucune requête n'est acceptée, celles en cours finissent (un lot au plus), puis sortie avec le code 0.

Les codes de sortie servent à systemd (§8) : il relance après un échec de chargement (4), jamais après une erreur de config (2) ou un port pris (3).

### 5.3 Protocole (version 1)

| Point d'accès | Entrée | Sortie |
|---|---|---|
| `GET /v1/health` | — | 200 `{protocol: 1, state: "ready", algo: "bge-m3/2", embedder, reranker, dim: 1024, device: "cuda:0", fp16: true, vram_used_mb, max_texts: 32, max_chars: 20000, loom: "2.0.18"}`. 503 `{state: "loading" \| "failed", error?}`. |
| `POST /v1/embed` | `{texts: [str, …]}` | `{items: [{dense: "<base64 float32 LE>", sparse: {indices: [int], values: [float]}}]}` |
| `POST /v1/score` | `{query: str, passages: [str, …]}` | `{scores: [float]}`, entre 0 et 1 |

- **`algo`** est une constante de `bge_m3.py`, à incrémenter dès que le calcul change : dense, lexical, troncature, normalisation, sigmoïde. Le client refuse un `algo` différent du sien : des vecteurs incompatibles n'entrent jamais en silence dans la base.
- **Plafonds par requête** : au plus 32 textes (ou passages), 20 000 caractères chacun (`bge_m3.MAX_CHARS`), et 4 Mo de corps de requête — de quoi porter le corps le plus lourd, une question et 32 passages de 20 000 caractères tous échappés en JSON. `Content-Length` est vérifié **avant** la lecture du corps. Le client envoie des lots de 16 et coupe chaque texte à 20 000 caractères, comme les modèles le font eux-mêmes dans les deux chemins : il n'atteint jamais les plafonds.
- **Erreurs** : 400 (JSON illisible), 401 (jeton absent ou faux), 404, 413 (plafond dépassé), 422 (champs invalides), 503 avec `Retry-After: 1` (chargement en cours, ou file d'attente pleine), 500 (`ModelRunError`, avec le message lisible actuel). Le corps est toujours `{error: "…"}`. Sur un 503 dû aux modèles, il porte aussi `state: "loading" | "failed"` : le client distingue ainsi une application relancée (contact à refaire) d'une file pleine (nouvel essai), sans lire le texte du message.

### 5.4 Serveur HTTP : bibliothèque standard

- `ThreadingHTTPServer`, un thread par requête. Les modèles sont synchrones, il n'y a pas d'asyncio. `daemon_threads = False` et `block_on_close = True` : à l'arrêt, `server_close` attend les requêtes en cours. `allow_reuse_port = False` : une seconde instance ne peut pas partager le port.
- HTTP/1.0 : une connexion par requête. Aucune connexion inactive ne retarde l'arrêt ; sur la boucle locale, le coût est négligeable.
- Un `timeout` de socket par requête (30 s), pour qu'un client muet ne garde pas de thread.
- **File d'attente bornée** : un sémaphore de 32 requêtes à la fois. Au-delà, 503 avec `Retry-After`. Derrière, chaque modèle calcule un lot à la fois (verrou de `LazyModel`), et l'embedder et le reranker tournent en parallèle.
- **Journal** : méthode, chemin, code, nombre de textes, durée. **Jamais le contenu des textes.** `log_message` de `BaseHTTPRequestHandler` est redirigé vers `logging`.
- Pas de TLS dans ce chantier : la boucle locale seulement. Pour le lot 5, voir §9.

## 6. Côté loom : la config

### 6.1 Les trois formes

**Calcul dans loom** (aujourd'hui, inchangé) :

```yaml
- python: loom.memory
  models: { device: cuda, preload: true, offline: true }
```

**Calcul par loom-mem-gpu** :

```yaml
- python: loom.memory
  models: { server: { url: http://127.0.0.1:8765 } }
```

**Par loom-mem-gpu, avec repli dans loom s'il est arrêté** :

```yaml
- python: loom.memory
  models:
    server:
      url: http://127.0.0.1:8765
      fallback: { device: cuda, offline: true }   # repli dans loom ; absent : pas de repli
```

Tout ce qui concerne le mode « application » est sous `server`, y compris les réglages du repli local. Pour revenir au calcul dans loom, on retire `server` et on remet `device`, `offline` et `preload` au niveau de `models`.

| Mode | Sous `models` | Sous `models.server` |
|---|---|---|
| Calcul dans loom | `device`, `fp16`, `offline`, `preload`, `embedder`, `reranker` | — |
| Calcul par loom-mem-gpu | `embedder` et `reranker` seulement (les modèles attendus) | `url` ou `url_env`, `token_env`, `fallback: {device, fp16, offline}`, délais |

### 6.2 Clés de `models.server`

| Clé | Rôle | Valeurs | Défaut |
|---|---|---|---|
| `url` | Adresse de l'application. | `http://127.0.0.1:<port>`, `http://localhost:<port>`, `http://[::1]:<port>` | — |
| `url_env` | Variable qui porte l'adresse, lue dans les secrets du client (comme `store.path_env`). | nom de variable | — |
| `token_env` | Variable qui porte le jeton (`Authorization: Bearer`). | nom de variable | `null` |
| `fallback` | Repli dans loom quand l'application est injoignable : calcul dans le process, avec ces réglages. Absent : pas de repli, erreur claire. | objet `{device, fp16, offline}`, mêmes valeurs et mêmes défauts que dans `models` (`device: auto`, `fp16: true`, `offline: false`) ; `{}` accepte tous les défauts | absent |
| `connect_timeout` | Secondes. Une application arrêtée ne bloque pas chaque appel. | > 0 | 2 |
| `read_timeout` | Secondes, par lot. | > 0 | 60 |

Il faut `url` ou `url_env`, l'un des deux.

Seuls `embedder` et `reranker` restent au niveau de `models` dans les deux modes : ce sont les modèles **attendus**, que l'application doit annoncer. Leurs défauts sont ceux d'aujourd'hui (`BAAI/bge-m3`, `BAAI/bge-reranker-v2-m3`).

### 6.3 Contrôles au chargement de la config, sans contacter l'application

- `server` avec `kind: fake` : refusé.
- `url` hors de la boucle locale : refusé (« réservé au lot réseau »). Cette règle sera remplacée au lot 5 (https et jeton obligatoires).
- `server` avec `device`, `fp16` ou `offline` **donnés** au niveau de `models` (`model_fields_set`) : refusé. Message : « avec server, device, fp16, offline se règle(nt) sur loom-mem-gpu (ses options), pas ici » ; le lot 3 y ajoutera « pour le repli dans loom, les mettre dans server.fallback ». Deux endroits pour une même décision finissent par se contredire.
- `server` avec `preload` **donné** : refusé. Message : « avec server, les modèles sont chargés par loom-mem-gpu à son démarrage ; preload ne sert qu'au calcul dans loom ». Le repli ne précharge pas non plus (§7.4).
- `secret_names()` renvoie aussi `url_env` et `token_env` : un client peut les rediriger (`tenants[].secrets`).
- `build()` (`memory/tool.py`) ne vérifie torch et transformers que si un calcul local est possible : sans `server`, ou avec `server.fallback`.

## 7. Comportement du client (`remote.py`)

### 7.1 Premier contact, une fois par process

`GET /v1/health`, puis une comparaison avec la config : `protocol`, `algo`, `embedder`, `reranker` et `dim`. En cas d'écart, une `ModelLoadError`, avec le même message que pour une base indexée par un autre modèle. La réponse est gardée : `device` sert au motif d'approbation, et les plafonds à la taille des lots.

`dim` est connu sans réseau (1024, BGE-M3) : `MemoryService.start()` vérifie la base sans attendre l'application.

### 7.2 Calcul

- `embed_documents`, `embed_query` et `score` découpent en lots de 16 et envoient une requête par lot. `checkpoint(cancel)` est appelé entre deux requêtes : un appel annulé s'arrête au lot suivant, comme en local.
- Le client est un `httpx2.Client` synchrone, appelé dans le thread de `MemoryService._model`. Il est partagé par le process, dans le registre de `shared_models()`, sous une clé qui comprend l'adresse, le jeton et le repli.
- Le dense est décodé de base64 en `array('f')`, puis en `list[float]` (`Embedding.dense`). Le lexical devient un `SparseVec`.

### 7.3 Erreurs et repli

| Ce qui arrive | Sans `server.fallback` | Avec `server.fallback` |
|---|---|---|
| Connexion refusée, délai de connexion dépassé, 502 ou 504 (proxy, lot 5) | `ModelLoadError` : « loom-mem-gpu injoignable (http://127.0.0.1:8765) : le démarrer (systemctl --user start loom-mem-gpu), ou ajouter models.server.fallback pour calculer dans loom. » | Calcul local, avec les mêmes `BgeM3Embedder` et `BgeReranker` qu'aujourd'hui, réglés par `server.fallback` et chargés au premier repli (environ 4,5 Go de RAM sur CPU). Une ligne d'avertissement dans le journal, une seule fois. |
| 503 `loading` | Attente, au plus 60 s (constante), en surveillant `cancel`. Ensuite, comme « injoignable ». | Pareil : l'application arrive, on ne se replie pas tout de suite. |
| 503 `failed` (l'application n'a pas chargé ses modèles) | `ModelLoadError` avec son erreur. | Repli : l'application est là, mais ne peut pas calculer. |
| 503 file pleine | Nouvel essai après `Retry-After`, tant que `read_timeout` le permet. | Pareil. |
| 401 | `ModelLoadError` : jeton refusé. Pas de repli : c'est une erreur de config. | Pareil. |
| Écart de modèle ou d'`algo` | `ModelLoadError`. Pas de repli. | Pareil. |
| 500 | `ModelRunError` (le message de l'application). | Pareil : le calcul a échoué, mais l'application est là. |

**Retour sur l'application.** Après un repli, elle est sondée toutes les 30 s (constante). Dès qu'elle répond « ready » et passe le contrôle du §7.1, les calculs y retournent. Les modèles locaux déjà chargés restent en mémoire jusqu'à la fin du process.

Un document indexé en partie par l'application et en partie en local reste cohérent : mêmes poids, même code. Seule la précision change (fp16 contre fp32), comme aujourd'hui entre deux process sur des devices différents.

### 7.4 `preload`

Avec `server`, `preload` est refusé (§6.3) :
- loom-mem-gpu charge toujours ses modèles à son démarrage, et ne les décharge jamais ;
- le premier appel d'un process interroge `/v1/health` de toute façon, en quelques millisecondes : une vérification au montage n'apporterait rien ;
- **les modèles du repli ne sont jamais préchargés** : ce serait 4,5 Go chargés pour rien tant que l'application répond. Ils sont chargés au premier repli.

### 7.5 Ce qu'on en voit

- **Motif d'approbation** (`_duration`) : le device vient de la réponse gardée, sans requête. Exemple : « moins d'une seconde de calcul sur cuda:0 (loom-mem-gpu) ». Pendant un repli, c'est le device local.
- **`loom validate`** (`settings()`) : « modèles : loom-mem-gpu http://127.0.0.1:8765 (repli : aucun) », ou « (repli : cuda) », sans contacter l'application. Son état se lit avec `loom-mem-gpu status`.
- **Users et rôles** (`loom_role_user.md`) : l'application n'est pas un point d'entrée de loom. Elle n'a ni run, ni session, ni approbation, et reste donc hors de la matrice des droits.

## 8. Exploitation sur le poste

### 8.1 Où l'installer

L'application doit tourner dans un venv qui a l'extra `memory`, avec **la même version de loom** que les agents (`algo` le vérifie de toute façon). Le `.venv` de `loom_v2` n'a pas torch. Celui d'agent-mem (`/home/denis/temp/agent-mem/.venv`) a tout ce qu'il faut, et pointe sur `loom_v2` en éditable.

### 8.2 Unité systemd utilisateur : `install-loom-mem`

L'aide à l'installation est livrée avec loom-ia : `scripts/bin/install-loom-mem` est installé dans le bin du venv, à côté de `loom-mem-gpu` (`[tool.uv.build-backend.data] scripts = "scripts/bin"`, en installation normale comme en éditable). Le service lance le `loom-mem-gpu` de ce venv.

```
install-loom-mem [--port N] [--device D] [--offline] [--no-fp16]
                 [--max-vram-fraction F] [--linger] [--dry-run]
install-loom-mem --uninstall [--dry-run]
```

Ce qu'il fait, dans l'ordre :

1. **Prérequis** : `systemctl --user` répond ; `loom-mem-gpu` est à côté du script ; les réglages et l'extra `memory` sont validés **par l'application elle-même** (`loom-mem-gpu serve --check`, sans rien charger ni ouvrir : aucune règle recopiée dans le script) ; avec `--device cuda`, `nvidia-smi` voit une carte. Un refus : code 2, rien n'est écrit.
2. **Port** : pris par autre chose que ce service (une instance lancée à la main, un autre programme), code 3, rien n'est écrit. Le script ne tue jamais un process qu'il n'a pas lancé.
3. **Unité** `~/.config/systemd/user/loom-mem-gpu.service` (nom fixe : une seule instance), avec `PYTORCH_CUDA_ALLOC_CONF`, et `HF_HOME` s'il est défini dans le shell (le service ne lit pas `~/.bashrc`).
4. `daemon-reload`, `enable`, `restart` (qui arrête l'ancien process avant d'en lancer un) ; puis attente de `ready` (`loom-mem-gpu status`, 180 s au plus, `LOOM_MEM_READY_WAIT`). Un service en échec : la fin de son journal, code 1.

**Rejeu.** Unité identique et service actif : rien ne change, le service garde ses modèles en VRAM (« déjà installé et actif, unité à jour »). Unité différente : la différence est montrée, l'unité réécrite, le service redémarré une fois. `--dry-run` montre l'unité et les commandes sans rien faire ; `--linger` lance `loginctl enable-linger` (sinon, le script dit seulement qu'il faut le faire pour que le service tourne hors session). `--uninstall` : `disable --now`, unité retirée, `daemon-reload` ; rejoué, « rien à désinstaller ».

L'unité écrite, par exemple pour `install-loom-mem --offline` :

```ini
# Écrit par install-loom-mem : le relancer plutôt que modifier ce fichier à la main.
[Unit]
Description=loom-mem-gpu — modèles de la mémoire de loom gardés en VRAM
StartLimitIntervalSec=300
StartLimitBurst=5

[Service]
ExecStart="/home/denis/temp/agent-mem/.venv/bin/loom-mem-gpu" serve --device cuda --offline
Environment=PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
Restart=on-failure
RestartSec=5
# Réglages refusés (2) ou port pris (3) : relancer ne changerait rien.
RestartPreventExitStatus=2 3

[Install]
WantedBy=default.target
```

```
journalctl --user -u loom-mem-gpu -f
loom-mem-gpu status
```

Sous systemd (variable `JOURNAL_STREAM`), les lignes du journal ne portent pas l'heure : journald la note déjà.

### 8.3 Qui relance quoi

**C'est systemd qui relance l'application, pas nginx.** Un reverse proxy (nginx, Caddy) ne fait que relayer les requêtes : si l'application tombe, il répond 502 (Bad Gateway) ou 504 (délai), sans rien relancer. nginx est lui-même un service que systemd relance. On a donc deux unités, chacune avec son `Restart=`.

Côté loom, une 502 ou une 504 du proxy compte comme « injoignable » (§7.3) : repli ou erreur claire selon `server.fallback`, puis nouvelle sonde toutes les 30 s.

Le proxy ne servira qu'au lot 5. Sur le poste, loom parle directement à `127.0.0.1:8765`.

### 8.4 Changer de réglage

| Ce qu'on change | Où | Effet |
|---|---|---|
| Port, device, fp16, offline, plafond de VRAM | Relancer `install-loom-mem` avec les nouvelles options | Différence montrée, unité réécrite, service redémarré une fois. Les process loom attendent pendant le chargement (503 `loading`, 60 s au plus). Avec un autre port, changer aussi `models.server.url` des agents. |
| Calcul dans loom ou par l'application, repli | `models` de `loom.memory` dans `agent.yaml` | Pris en compte au prochain process loom, ou par `serve --reload`. |

## 9. Lot 5, plus tard : l'application sur une VM cloud

Le lot 5 reprend les règles du point 9 de `loom-memory-points-en-suspens.md` :

- adresse hors de la boucle locale seulement en `https://`, et jeton obligatoire ;
- TLS assuré par un reverse proxy (nginx, Caddy) devant `127.0.0.1:8765` sur la VM, ou TLS natif (`ssl.SSLContext` de la bibliothèque standard, `--tls-cert` et `--tls-key`) ;
- `ca_file` côté loom pour un certificat auto-signé ;
- deux jetons acceptés à la fois, pour pouvoir changer de jeton sans coupure.

Derrière nginx :

- `proxy_read_timeout` au-dessus de `read_timeout` ;
- `client_max_body_size` à 4 Mo, comme le plafond de l'application ;
- n'ouvrir que le port HTTPS ;
- garder en tête la localisation des données chez le fournisseur (RGPD).

## 10. Impact

| Fichier | Changement |
|---|---|
| `tools_builtin/memory/protocol.py` (nouveau) | Schémas du fil, `PROTOCOL`, plafonds, encodage du dense. |
| `tools_builtin/memory/remote.py` (nouveau) | Client, `RemoteEmbedder`, `RemoteReranker`, état de l'application, repli, nouvelle sonde. |
| `tools_builtin/memory/server/` (nouveau) | L'application `loom-mem-gpu`. |
| `tools_builtin/memory_params.py` | `ServerSpec` et son `FallbackSpec` (`device`, `fp16`, `offline`), `ModelsSpec.server`, contrôles du §6.3, `secret_names()`. |
| `tools_builtin/memory/models.py` | Clé du registre avec `server` ; `Models` distant. |
| `tools_builtin/memory/bge_m3.py` | Constante `ALGO`. Calcul inchangé. |
| `tools_builtin/memory/tool.py` | `build()` (torch seulement si un calcul local est possible), `_duration`, `settings()`. |
| `tools_builtin/memory/service.py`, `retrieval.py`, `store.py`, `maintenance.py` | **Inchangés** : ils passent par `Models`. |
| `pyproject.toml` | `[project.scripts]` `loom-mem-gpu`, un contrat import-linter ; version inchangée (2.0.18). |
| `access/cli/` | **Inchangé.** |
| Doc | Howto (chapitre mémoire), `configuration.md`, CHANGELOG, formation, point 9 de `loom-memory-points-en-suspens.md`. |

**Non-régression :** sans `models.server`, le chemin du code est exactement celui d'aujourd'hui. loom-notes n'est pas touché.

## 11. Tests

- **Application** (`test_memory_gpu_server.py`), sur les faux torch et transformers de `test_memory_models.py` :
  - `/v1/health` en `loading`, `ready` et `failed` ;
  - `/v1/embed` et `/v1/score` ;
  - plafonds, avec `Content-Length` lu avant le corps ;
  - jeton absent ou faux ;
  - file pleine (503) ;
  - port pris : code 3, sans chargement ;
  - adresse hors de la boucle locale refusée ;
  - arrêt propre sur SIGTERM ;
  - aucun texte dans le journal.
- **Protocole** : aller-retour du dense en base64 float32 et du lexical, bit à bit.
- **Config** : les règles du §6.3, et `url_env` et `token_env` lus dans les secrets du client.
- **Client**, contre une fausse application :
  - écart de modèle, d'`algo` ou de `protocol` ;
  - attente pendant `loading` ;
  - sans `server.fallback` (message) et avec (repli), puis retour après la sonde ;
  - 502 et 504 vus comme « injoignable » ;
  - annulation entre deux lots ;
  - `Retry-After`.
- **Moteur** : `add_text` puis `search` avec `server` sans `fallback`, **sans torch importé**.
- **Isolement** : le contrat et le test du §4.3 ; importer l'application ne charge ni torch ni les couches interdites ; `remote.py` n'importe pas torch.
- **Essai en vrai sur la 5060 Ti** (fonctionnel, sans campagne de mesures) : `loom memory eval` sur le même jeu doré, calcul dans loom puis par l'application : même ordre et scores proches.

## 12. Lots

| Lot | Contenu | Testable par |
|---|---|---|
| 1 | **Fait le 05/11.** `protocol.py`, l'application `loom-mem-gpu` (`serve` et `status`), `ALGO`, l'exécutable, le contrat import-linter et le test d'isolement ; `tests/unit/test_memory_gpu_server.py` (44 essais). | `loom-mem-gpu` lancé à la main, puis `loom-mem-gpu status` et `curl`. |
| 2 | **Fait le 05/11.** `remote.py`, `models.server` sans repli, les contrôles de config, `build()`, `settings()`, `_duration` ; `tests/unit/test_memory_remote.py` (34 essais, contre un vrai `loom-mem-gpu` aux modèles factices). | agent-mem avec `server` : une recherche et un `add_file` approuvé. |
| 3 | **Fait le 05/11.** `server.fallback` (`FallbackSpec`, `ModelsSpec.fallback()`), le repli lot par lot et la nouvelle sonde après 30 s (`REPROBE`) ; seule `ServerUnavailable` (injoignable, chargement trop long, sans modèles) déclenche le repli ; avec repli, le montage exige l'extra `memory` ; 11 essais de plus dans `tests/unit/test_memory_remote.py`. | Arrêter l'application pendant un run, puis la relancer. |
| 4 | Unité systemd documentée (§8.2) ; journal sans heure en double sous systemd (fait le 05/11). Essai en vrai du §11 : à faire sur le poste. | `systemctl --user`, `loom memory eval`. |
| 5 | Plus tard : réseau, VM cloud (§9). | — |
| 6 | **Fait le 05/11.** CHANGELOG (2.0.18), guide : exemple 29.4 et annexe des extras, formation : exercice 5.6 (encadré), README (extra `memory`), point 9 de `loom-memory-points-en-suspens.md` marqué fait. `configuration.md` inchangé : il ne décrit pas les paramètres de `loom.memory`, que le guide porte. | — |
| 7 | **Fait le 05/11 — un seul calcul, deux chemins.** Revue des doublons entre calcul local et loom-mem-gpu : règle de la demi-précision (`bge_m3.half_precision`), lots (`bge_m3.batches`), coupe des textes à 20 000 caractères dans les deux chemins (`bge_m3.MAX_CHARS`, `clipped` ; avant, un texte plus long passait en local et était refusé par l'application), nom de l'application (`protocol.APP`), motif et défauts du device (`memory_params.DEVICE_PATTERN`, `DEFAULT_*`) ; corps de requête porté à 4 Mo ; `ALGO` passe à `bge-m3/2` (la coupe touche la troncature). Essais : équivalence du calcul local et du calcul par l'application sur les vraies classes (faux torch), coupe, corps le plus lourd, demi-précision, listes de champs parallèles à `ModelsSpec`, options de l'application. | |
| 8 | **Fait le 06/11 — aide à l'installation.** `install-loom-mem` (bash, `scripts/bin/`, installé dans le bin du venv avec le paquet) : prérequis, validation par `loom-mem-gpu serve --check` (nouveau, qui partage `preflight` avec le démarrage), port pris par un autre refusé, unité au nom fixe, rejeu sans redémarrage si rien ne change, `--dry-run`, `--linger`, `--uninstall` (§8.2). `tests/unit/test_install_loom_mem.py` : le vrai script, dans un faux venv, avec de faux systemctl, loginctl, nvidia-smi ; shellcheck sans remarque. | `install-loom-mem --dry-run`, puis sans. |

## 13. Risques

- **VRAM partagée avec un LLM local** : l'ordre de démarrage et `--gpu-memory-utilization` pour vLLM ; `--max-vram-fraction` pour loom-mem-gpu.
- **Version de l'application différente de celle des agents** : `algo` et `protocol` la refusent, avec un message qui dit laquelle mettre à jour.
- **Port local ouvert à tous les process du poste** : acceptable sur un poste à un seul utilisateur, d'autant que l'application ne voit aucune base. `--token-env` est là si besoin.
- **Repli local sur un poste à petite RAM** : environ 4,5 Go chargés d'un coup. C'est pourquoi le repli n'est pas activé par défaut.
