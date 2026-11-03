# loom.memory — points en suspens après les tests utilisateur

*Branche `loom-mem`, loom-ia 2.0.16, tests de bout en bout du 03/11/2026 sur l'agent `agent-mem` (Haiku 5.5, `device: cpu`, modèles réels BGE-M3 et bge-reranker-v2-m3).*

Ce document recense ce qui reste à faire. Rien n'y est implémenté. Pour chaque point : ce qui a été observé, la cause dans le code, la correction proposée, les effets de bord à surveiller et les tests à ajouter.

---

## Vue d'ensemble

| # | Point | Nature | Priorité | Effort |
|---|---|---|---|---|
| 1 | Une question de FAQ séparée de sa réponse au découpage | Qualité de recherche | Haute | Moyen |
| 2 | Le calcul continue après l'expiration du délai | Ressources | Haute | Moyen |
| 3 | Gros fichiers sur CPU : délai dépassé, coût invisible à l'approbation | Ergonomie / doc | Moyenne | Faible |
| 4 | Premier passage trop court, avant le premier titre | Qualité de recherche | Basse | Faible |
| 5 | `add_file` limité à `.md`, `.markdown` et `.txt` | Fonctionnalité | Moyenne | Moyen |
| 6 | Texte d'interface résiduel dans `add_url` | Qualité d'extraction | Basse | Faible |
| 7 | Outils MCP non annotés : irréversibles mais sans approbation | Sécurité | Haute | Moyen (effets de bord) |
| 8 | Tests non joués en vrai | Validation | Moyenne | Faible |
| 9 | Calculs des modèles sur un GPU distant, avec repli sur le CPU | Fonctionnalité / performance | Moyenne | Élevé |

Les limites connues et acceptées sont listées à la fin. Elles ne sont pas à corriger, seulement à garder en tête.

---

## 1. Une question de FAQ séparée de sa réponse au découpage

### Observé (test-mem-7)

La question « Que se passe-t-il si Denis est indisponible pendant un projet ? » porte sur la FAQ de `https://denislamard.github.io/`.

- **Premier résultat, score 0,46 :** un passage qui se **termine** par la question « Vous êtes seul. Que se passe-t-il si vous êtes indisponible ? ». Il ne contient pas la réponse.
- **Deuxième résultat, score 0,18 :** le passage suivant, qui **commence** par la réponse.

Marie a répondu juste uniquement parce que 0,18 dépasse `min_score: 0.1`. Avec un seuil à 0,2, la réponse était perdue. Le seuil de 0,435 suggéré par `loom memory eval` l'aurait perdue aussi.

### Cause

`chunk.py`, fonction `_split_body` : un passage est fermé dès que le paragraphe suivant ferait dépasser `target_chars` (1800). Dans une FAQ, la question et sa réponse sont deux paragraphes distincts. La coupure peut donc tomber entre les deux.

Le chevauchement (`overlap_chars`, 180) ne règle pas le problème. Il recopie la fin du passage précédent au début du suivant, mais c'est un fragment de texte, pas une unité de sens.

Sur une page HTML, la question vient probablement d'un `<summary>` (dans un `<details>`) ou d'un `<dt>`, que markdownify convertit en paragraphe ordinaire. C'est à vérifier sur la page : sa FAQ n'a aucun titre `#`.

### Correction proposée

Deux corrections complémentaires :

**A. Générale, dans `chunk.py` (`_split_body`).** Au moment de fermer un passage, si sa **dernière unité** est un paragraphe court qui annonce la suite, le reporter au début du passage suivant au lieu de le laisser en fin. Un paragraphe « annonce la suite » s'il remplit l'une de ces conditions :
- il finit par `?` ;
- il finit par `:` ;
- il fait moins de N caractères sans ponctuation finale (une ligne de titre en gras, par exemple `**Règles générales**`).

N est à fixer, autour de 200, et pourrait devenir un réglage de `ChunkingSpec`. Le report ne doit pas s'appliquer si le paragraphe est la seule unité du passage, sinon la boucle ne progresse plus.

**B. Spécifique au web, dans `web.py` (`extract_from_html`).** Avant la conversion en markdown :
- convertir `<details><summary>Q</summary>R</details>` en `#### Q` suivi de R ;
- convertir `<dt>Q</dt><dd>R</dd>` de la même façon.

Chaque question devient alors une section : elle entre dans le `heading_path`, qui est vectorisé avec chaque passage (`Chunk.embed_text`), et sa réponse forme un passage à part. C'est le meilleur cas pour la recherche.

### Effets de bord à surveiller

- **Le découpage change pour les documents déjà en base.** Il faut lancer `loom memory reindex` pour en profiter. Ce n'est pas obligatoire, car l'ancien découpage reste valide.
- **Les passages d'une FAQ ne doivent pas dépasser `max_chars` (2400) après le report.** Le report déplace au plus un paragraphe court, donc la marge entre 1800 et 2400 suffit si N reste petit.
- **Avec B, la profondeur des titres change.** Un `####` sous un `##` allonge le `heading_path`, déjà long sur ta page : la racine « Un agent qui marche une fois… » fait plus de 80 caractères. À surveiller sur le coût en tokens, puisque le `heading_path` est vectorisé avec chaque passage.

### Tests à ajouter

- `test_memory_chunk.py` : une FAQ en paragraphes dont la coupure tomberait entre la question et la réponse. La question doit se retrouver avec sa réponse.
- `test_memory_chunk.py` : un paragraphe court seul dans un passage ne boucle pas.
- `test_memory_web.py` : une page avec `<details>/<summary>` et avec `<dl>`. Chaque question devient un titre, et sa réponse le suit.
- **Non-régression :** les tests existants du découpage (chevauchement, morceaux minuscules, blocs de code) restent verts.

### Validation en vrai

Après `reindex`, rejouer test-mem-7. Le passage qui contient à la fois la question et la réponse doit arriver premier, avec un score nettement au-dessus de 0,46.

---

## 2. Le calcul continue après l'expiration du délai

### Observé (test-mem-4)

`add_url` a dépassé les 30 secondes du délai par défaut de l'époque. L'outil a répondu « Délai dépassé », et Marie a réessayé. La **deuxième tentative** a attendu derrière la première, puis a dépassé le délai à son tour.

Rien n'a été écrit. L'écriture n'est jamais lancée après une expiration, et c'est vérifié.

### Cause

`service.py`, `index_document` → `_model(...)` → `asyncio.to_thread(embedder.embed_documents, …)`.

- L'exécuteur (`engine/executor.py`, `_invoked`) annule l'attente avec `asyncio.timeout`.
- Mais le **thread** qui calcule ne peut pas être interrompu. Il termine tous ses lots, puis son résultat est jeté.
- Pendant ce temps, il occupe le processeur et garde le verrou du modèle (`LazyModel.run`, `self._lock`).
- Tout appel suivant, `search` compris, attend donc la fin d'un calcul devenu inutile. Sur CPU, c'est jusqu'à plusieurs minutes.

Le délai propre de 300 s, ajouté depuis, rend le cas plus rare, mais il existe toujours, par exemple pour un fichier de plus de 150 Ko (point 3).

### Correction proposée

Une annulation coopérative entre les lots :

1. Donner au calcul un `threading.Event` d'annulation. Par exemple :
   - `Embedder.embed_documents(texts, cancel: threading.Event | None = None)` ;
   - le même paramètre pour `Reranker.score`.
2. Dans `bge_m3.py`, `_Encoder.encode` et `_PairScorer.score` vérifient l'événement **entre deux lots de 16**, puis lèvent une exception dédiée (par exemple `ModelCancelled`).
3. Dans `service.py`, `_model` crée l'événement, attend le thread, et sur `asyncio.CancelledError` lève l'événement avant de relancer l'annulation.
4. `LazyModel.run` ne doit **pas** transformer `ModelCancelled` en `ModelRunError`. Il le laisse passer.

Le calcul s'arrête alors au plus tard après le lot en cours. Sur CPU, un lot de 16 passages prend environ 30 s : c'est le temps maximal pendant lequel le verrou reste encore tenu.

### Effets de bord à surveiller

- **Les protocoles `Embedder` et `Reranker` changent de signature.** `fake.py` et les faux modèles des tests sont à adapter.
- **Le préchargement** (`Models.preload`) n'est pas concerné : c'est un chargement, pas un calcul.
- **`loom memory reindex` et `import`** ne passent pas par le délai d'un outil. Pas d'annulation pour eux, sauf un Ctrl-C, à traiter à part si besoin.
- **Un calcul annulé ne doit rien laisser dans la base.** C'est déjà le cas, puisque l'écriture vient après le calcul, dans une transaction.

### Tests à ajouter

- `test_memory_models.py` : un faux modèle lent sur plusieurs lots. L'événement levé pendant le premier lot arrête le calcul avant le deuxième.
- `test_memory_service.py` : un `add` annulé par `asyncio.timeout` libère le verrou rapidement. Un `search` lancé juste après n'attend pas la fin du calcul complet.
- `test_memory_engine.py` : un outil au `timeout` très court sur un texte long. Le run continue, rien n'est écrit, et un appel suivant répond.

---

## 3. Gros fichiers sur CPU : délai dépassé, coût invisible à l'approbation

### Observé (test-mem-16b)

`workspace/config.md`, 64 Ko, a donné 63 passages, indexés en **2 min 10 s** sur CPU. C'est environ 2 s par passage de 1000 caractères en moyenne. Sur test-mem-5 et `reindex`, on mesure environ 1,5 s par passage.

### Cause

`tool.py` : `MAX_FILE_BYTES = 1_000_000`. À la vitesse du CPU :

| Taille | Passages (ordre de grandeur) | Durée sur CPU |
|---|---|---|
| 64 Ko | 63 | ~2 min |
| 150 Ko | ~150 | ~5 min (le délai par défaut de 300 s) |
| 1 Mo (maximum accepté) | ~1000 | ~30 min |

Côté web, `fetch_page.MAX_BYTES` vaut 5 Mo de HTML.

Au moment de l'approbation, rien ne dit à l'humain que l'appel va occuper le processeur pendant de longues minutes.

### Correction proposée

1. **Documentation**, dans le howto chapitre 4 et la formation (`pieges.html`) : l'ordre de grandeur par passage sur CPU et sur GPU, et les deux remèdes, un `timeout` plus long ou le GPU.
2. **Une estimation dans le motif d'approbation de `add_file`.** Pendant l'évaluation, on dispose déjà du chemin résolu (`_file_target`). Un `stat()` donne la taille sans lire le fichier. Le motif pourrait devenir : « mémoriser un fichier (workspace/config.md, 64 Ko, ~40 passages) … ». Le nombre de passages se déduit de la taille divisée par `chunking.target_chars`. L'appareil, lui, n'est connu qu'au chargement du modèle : ne l'afficher que si le modèle est déjà chargé.
3. **Option à discuter :** refuser dès l'évaluation un fichier dont l'estimation dépasse nettement le délai de l'outil, avec un message qui dit quoi faire. Le risque est un refus à tort, si l'estimation se trompe ou si le GPU est plus rapide que prévu. Je ne le recommande pas sans mesures sur GPU.

### Effets de bord à surveiller

- **Le motif d'approbation fait partie du journal.** Le changer change le texte de `approval.requested`, mais pas la décision, donc pas le rejeu.
- **`stat()` pendant l'évaluation** est un accès disque bref, de même nature que la résolution du chemin, déjà faite.

### Tests à ajouter

- `test_memory_tool.py` : le motif de `add_file` contient la taille et l'estimation du nombre de passages, et ne lit pas le contenu du fichier.

---

## 4. Premier passage trop court, avant le premier titre

### Observé (test-mem-5)

Le premier passage de la page fait 58 caractères : « Développeur IA en freelance · Systèmes agentiques · France ». C'est le texte qui précède le premier titre `#`.

### Cause

`chunk.py`, `_merge_tiny` : un morceau de moins de `min_chars` (200) est fusionné avec le **précédent**. Le premier morceau du document n'en a pas, donc il reste seul.

### Correction proposée

Dans `_merge_tiny`, si le premier morceau est trop court et que le suivant tient avec lui sous `max_chars`, le fusionner **en avant**. Le passage obtenu prend alors le `heading_path` du suivant, ou garde le titre du document : à trancher. Je recommande celui du suivant, plus précis.

### Effets de bord à surveiller

- Changement du découpage, donc `reindex` pour en profiter (voir le point 1).
- Un document d'un seul morceau court reste un passage unique.

### Tests à ajouter

- `test_memory_chunk.py` : un texte court avant le premier titre est fusionné avec la section suivante.
- `test_memory_chunk.py` : un document entièrement court reste un passage unique.

---

## 5. `add_file` limité à `.md`, `.markdown` et `.txt`

### Observé (test-mem-9, 9b, 9c)

`plaquette_commerciale_APP_Novative.docx` est refusé par `add_file`. Ce refus arrive maintenant **sans approbation**, ce qui a été corrigé pendant les tests.

Marie a contourné la limite en lisant le fichier avec `loom.word`, puis en le passant à `add_text`. Le résultat a deux défauts :
- **les titres perdent leur balisage**, alors que `word` les identifiait (niveaux 1, 2, 3). Il n'y a donc plus de sections au découpage ;
- **l'origine n'est pas enregistrée** : le document est de type `text`, sans le nom du fichier. Un nouvel enregistrement de la plaquette modifiée créerait un second document au lieu de remplacer le premier.

### Correction proposée

Étendre `add_file` aux `.docx` et aux `.pdf` en réutilisant la lecture des outils `loom.word` et `loom.pdf` :
- le `.docx` donne des blocs typés (titre et niveau, paragraphe, liste, tableau), à convertir en markdown (`#` selon le niveau, `-` pour les listes, tableaux en lignes) ;
- le `.pdf` donne du texte page par page. Les titres y sont rarement repérables, donc le découpage se fera par paragraphes.

Points de conception :
- **Les dépendances** viennent des extras `office` (python-docx) et `pdf`. `FILE_SUFFIXES` doit dépendre des extras installés. Si un extra manque, le refus doit le nommer, par exemple « .docx demande l'extra office ».
- **La taille** : `MAX_FILE_BYTES` vaut pour le fichier binaire. Il faudrait aussi plafonner le texte extrait, puisqu'un PDF compressé peut beaucoup grossir.
- **La source** reste l'adresse `workspace/…`. Un nouvel enregistrement du même fichier remplace le document au lieu d'en créer un second : c'est déjà le comportement de `find_by_source`.
- **La vérification avant approbation** (`_file_target`) doit accepter les nouvelles extensions selon les extras installés.

### Effets de bord à surveiller

- **Le contrat des imports légers.** `tool.py` ne doit pas importer python-docx ou pypdf au chargement. Il faut un import paresseux, comme pour `web.py`, sinon les 5 contrats d'import cassent.
- **La CI** installe `office` et `pdf` (variable `EXTRAS`), donc les tests tourneront.

### Tests à ajouter

- `test_memory_tool.py` :
  - un `.docx` avec des titres donne des sections, avec la source `file` ;
  - un `.pdf` donne du texte ;
  - un extra absent produit un refus qui le nomme ;
  - un PDF dont le texte dépasse le plafond est refusé.
- Les deux formats sans approbation quand l'extension n'est pas permise, comme aujourd'hui pour le reste.

---

## 6. Texte d'interface résiduel dans `add_url`

### Observé (test-mem-5)

Le texte stocké de la page contient deux fois « faites défiler le schéma → ». Ce sont des légendes d'interface autour d'éléments supprimés (des `svg`).

### Cause

`web.py` : `_NOISE` retire `svg`, `nav`, `footer`, etc., mais pas les éléments voisins qui ne servent qu'à l'interface (indications de défilement, boutons, éléments `aria-hidden` sur un parent mal annoté).

### Correction proposée

Ajouter à `_NOISE` les éléments sans contenu utile en lecture : `button`, `[role='button']`, `[role='presentation']`, et éventuellement `figure:has(svg)` si bs4 le permet avec html.parser. À valider sur quelques pages réelles avant d'élargir, pour ne pas retirer du contenu.

### Effets de bord à surveiller

- **Retirer trop :** certains sites mettent du texte utile dans des `figure` ou des `button`, par exemple un accordéon de FAQ. Ce point **interagit avec le point 1** : une FAQ en accordéon utilise souvent `button` pour la question, donc il faut le traiter avant de retirer les `button`.

### Tests à ajouter

- `test_memory_web.py` : une page avec un indice de défilement à côté d'un `svg` est nettoyée, et une FAQ en `button` + panneau garde sa question.

---

## 7. Outils MCP non annotés : irréversibles mais sans approbation (bug 7)

### Rappel (analyse d'impact, section 8)

`adapters/mcp/convert.py`, `declared()` : sans annotation, la spécification MCP considère un outil comme modifiant (`readOnlyHint: false`) et destructeur (`destructiveHint: true`). loom le déclare donc **irréversible**.

Mais `ToolSpec.approval` vaut `"never"` par défaut (`core/model/tooling.py`), et `adapters/mcp/source.py`, `_spec()`, ne le change pas. Un outil MCP non annoté est donc **irréversible et exécuté sans approbation**, sauf si la config en demande une (`approval: always` ou `approvals`).

C'est ce qui permettait à `add_url` de loom-notes d'écrire sans accord quand l'agent référençait tout le serveur. Pour la mémoire, le problème a disparu avec `loom.memory`, dont les écritures sont en `always` par défaut. Il reste pour **tous les autres serveurs MCP**.

### Options

| Option | Comportement | Effets de bord |
|---|---|---|
| A. Irréversible → `always` par défaut, pour les outils MCP seulement | Tout outil MCP non annoté, ou annoté destructeur, demande un accord | **Rupture** pour les configs existantes : des runs qui passaient s'arrêteront en attente. Il faut un journal durable, puisqu'une config avec `always` sans journal durable est refusée (`ConfigError` « dont certains appels attendent »). Des serveurs mal annotés (beaucoup n'annotent rien) deviennent pénibles. |
| B. Avertissement au chargement | `loom validate` et le démarrage signalent chaque outil MCP irréversible sans approbation | Aucune rupture. Ne protège pas, informe seulement. |
| C. Réglage du serveur | `mcp_servers.<nom>.unannotated: ask \| allow`, `allow` par défaut au début, puis `ask` à la version suivante | Migration en douceur. Un réglage de plus. |
| D. Combinaison B puis A | Avertissement en 2.0.x, changement de défaut en 2.1 avec une note de migration | Le plus sûr pour les utilisateurs existants. |

**Ma recommandation : D.** L'avertissement d'abord, avec la liste des outils concernés et la ligne de config pour les approuver ou les déclarer sans effet. Le changement de défaut ensuite, avec une version mineure.

### Effets de bord à vérifier avant d'agir

- **Les variantes et les évals :** un outil déclaré `always` y est traité selon `strictest_side_effects`. Vérifier qu'aucune suite d'éval existante ne change de résultat.
- **Le stockage :** `always` exige un journal durable. Les configs d'exemple en `backend: memory` qui utilisent MCP doivent être recensées (`examples/`, `docs/howto`, la formation).
- **`approvals` au niveau du serveur** (`config/models.py`, `approvals: dict[str, Approval]`) doit continuer de l'emporter sur le nouveau défaut.
- **Les sous-agents et les outils délégués** qui exposent des outils MCP : le défaut doit s'appliquer de la même façon.

### Tests à ajouter

- `adapters/mcp` : un outil sans annotation → irréversible + avertissement (B) ou `always` (A), et un outil annoté `readOnlyHint` → `none`, `never`.
- Config : `approvals` d'un serveur l'emporte, et une config `backend: memory` avec un outil MCP non annoté est refusée ou avertie selon l'option choisie.
- Non-régression : les tests d'intégration MCP existants.

---

## 8. Tests non joués en vrai

Ces cas sont couverts par les tests automatiques, mais n'ont pas été joués avec les vrais modèles :

| Cas | Commande | Attendu |
|---|---|---|
| Le même fichier enregistré deux fois | `loom run agent "Utilise l'action add_file de la mémoire pour mémoriser workspace/config.md." --session …` | Approbation **demandée**, puisque le contenu d'un fichier n'est pas lu avant. Après accord : `duplicate_of: 569d565d-…`, aucun calcul. |
| Suppression approuvée | `loom run agent "Supprime le document « loom_v2 — Clés de configuration » de la mémoire." --session …` | Approbation (irréversible), puis document et 63 passages supprimés. Base à 2 documents et 24 passages. |
| GPU, carte prise en charge | `models: { device: auto }` sur la RTX 5060 Ti | `cuda` choisi, fp16, temps par passage à mesurer, mémoire GPU d'environ 1,1 Go par modèle. |
| GPU, petite carte | `device: cuda` forcé sur la carte de 4 Go | Refus clair au chargement (« models.device cuda inutilisable : … ») si la carte n'est pas prise en charge par torch 2.15. Sinon, chargement en fp16 sans débordement mémoire, grâce à la conversion avant copie. |
| Plusieurs clients | `store: { path_env: … }` et deux `tenants` | Deux fichiers, aucun ne voit l'autre (couvert en test moteur). |
| `serve --reload` | Modifier un fichier de config pendant que la mémoire sert | Rechargement, puis un premier `search` qui fonctionne, avec et sans `preload`. |
| `examples/j6/memory.py` | Avec un vrai orchestrateur | Les 4 cas de l'exemple. |

---

## 9. Calculs des modèles sur un GPU distant, avec repli sur le CPU

### Besoin

Sur CPU, avec les mesures des tests du 03/11 :
- une recherche prend environ 10 s avec `rerank_candidates: 8` ;
- l'indexation prend environ 1,5 à 2 s par passage ;
- au-delà d'environ 150 Ko, un fichier dépasse le délai de 300 s.

On veut que loom continue de tourner sur le poste local, et n'envoie que les **calculs des modèles** (vecteurs et reranking) à un GPU distant quand il est disponible : une VM louée à la minute, comme Thunder Compute, ou la machine qui a la RTX 5060 Ti.

### Ce qui est possible aujourd'hui, sans code

Pour les gros calculs ponctuels, on peut tout faire à la main :
1. démarrer la VM ;
2. y copier `memoire.db` et la config ;
3. y lancer `loom memory import` ou `loom memory reindex` ;
4. rapatrier `memoire.db` ;
5. arrêter la VM.

La base reste compatible, puisque c'est le même modèle des deux côtés : un calcul en fp16 sur GPU et en fp32 sur CPU donne des vecteurs quasi identiques. Mais ça ne couvre pas les `add_file` et `add_url` lancés par l'agent en cours de run, ni les recherches.

### Solution retenue : un serveur de modèles à nous

**Écartée : Ollama.** `ollama pull bge-m3` sert BGE-M3, mais `/api/embed` ne renvoie que le vecteur **dense**. Le lexical, qui retrouve les termes exacts comme les numéros de devis, les références ou les noms propres, n'est pas disponible. Ollama n'a pas non plus de point d'accès dédié au reranking. Il faudrait un `llama-server` de llama.cpp en plus pour bge-reranker-v2-m3, la base indexée deviendrait incompatible avec le calcul local, et la recherche serait moins bonne qu'aujourd'hui.

**Retenue : un serveur qui réutilise `bge_m3.py`**, pour calculer exactement le même dense, le même lexical et le même reranking qu'en local.

#### Côté GPU : `loom memory models serve`

- Une nouvelle commande, qui charge `BgeM3Embedder` et `BgeReranker` avec un `ModelsSpec` (`device: cuda`, `fp16: true`) et les sert en HTTP.
- Ses points d'accès, avec du JSON, en entrée comme en sortie :

  | Point d'accès | Entrée | Sortie |
  |---|---|---|
  | `GET /health` | — | `{embedder, reranker, dim, device, fp16, version}` |
  | `POST /embed` | `{texts: [...]}` | `[{dense: [1024 floats], sparse: {indices, values}}]` |
  | `POST /score` | `{query, passages: [...]}` | `[float]` |

- Il impose un plafond sur le nombre de textes et sur leur taille par requête, pour qu'un seul appel ne monopolise pas la carte.
- Il demande une authentification par jeton (`Authorization: Bearer`), lu dans une variable d'environnement.
- Il écoute par défaut sur `127.0.0.1`, pour être joint par un tunnel SSH. Pour l'ouvrir au réseau, il faut le demander explicitement, et seulement avec TLS.
- Il réutilise le verrou de `LazyModel` : un calcul à la fois par modèle, comme en local.
- Il dépend de l'extra `http`, déjà présent pour `loom serve`.

#### Côté loom : un nouveau type de modèles

```yaml
models:
  kind: remote
  url_env: LOOM_MODELS_URL        # ex. http://127.0.0.1:8765 par tunnel SSH
  token_env: LOOM_MODELS_TOKEN
  fallback: cpu                    # cpu | none
  connect_timeout: 3               # s : une VM éteinte ne bloque pas chaque appel
```

- **`RemoteEmbedder` et `RemoteReranker`** implémentent les protocoles `Embedder` et `Reranker` de `models.py`. `load()` appelle `/health`.
- **La vérification du modèle.** Au premier contact, le nom de l'`embedder` et la dimension renvoyés par `/health` doivent être ceux de la config et de la base (table `meta`). Sinon, le chargement est refusé, avec le même message que pour une base indexée par un autre modèle.
- **Le repli.** Avec `fallback: cpu`, un serveur injoignable fait passer au calcul local : `BgeM3Embedder` et `BgeReranker`, avec les mêmes poids, donc des vecteurs compatibles. Avec `fallback: none`, l'appel échoue avec un message clair. Le serveur est re-sondé à intervalles, pour revenir au GPU quand il répond de nouveau.
- **Le registre partagé** (`shared_models`) ajoute l'URL et le repli à sa clé.

#### Allumer et éteindre la VM

Ce n'est pas le rôle de loom. Soit à la main, soit avec un script autour de la CLI du fournisseur, par exemple `tnr` pour Thunder Compute. loom envoie au GPU tant que le serveur répond, et calcule en local sinon. Pas besoin de règle du type « gros fichier = GPU » : quand le GPU est disponible, tout y passe, recherches comprises.

### Effets de bord à surveiller

- **Le repli sur le CPU charge environ 4,5 Go de RAM** au moment où il se déclenche. Avec `preload: true`, faut-il précharger les modèles locaux même quand le serveur répond ? À trancher. Je propose de ne pas le faire, et de les charger au premier repli.
- **Les délais.** Le délai propre de 300 s de l'outil reste nécessaire pour le repli. Côté réseau : un délai de connexion court, et un délai de lecture proportionné au nombre de textes.
- **L'annulation** (point 2) doit fonctionner aussi pour un appel distant. Fermer la connexion suffit côté client, mais le serveur doit arrêter le calcul entre deux lots quand le client s'en va.
- **Les données sur le réseau.** Les textes des documents et des requêtes transitent jusqu'au GPU. Il faut TLS ou un tunnel SSH, et vérifier la localisation des données chez le fournisseur : Thunder Compute tourne sur AWS EC2, sans région européenne documentée. C'est un sujet RGPD pour des données clients.
- **Les imports légers.** `kind: remote` ne doit importer ni torch ni transformers tant que le repli n'est pas déclenché. Un poste sans l'extra `memory` peut alors utiliser un GPU distant, avec `fallback: none`.
- **La compatibilité des vecteurs.** Ce sont les mêmes poids et le même code (`bge_m3.py`) des deux côtés, la seule différence étant fp16 contre fp32. Il faut vérifier par un test que l'ordre des résultats d'une recherche ne change pas selon que le calcul est local ou distant, sur un petit corpus.

### Tests à ajouter

- **Serveur** (`test_memory_models_server.py`) : `/health`, `/embed` et `/score` sur les faux torch et transformers déjà utilisés par `test_memory_models.py` ; jeton absent ou faux refusé ; plafonds appliqués.
- **Client** (`test_memory_models.py`) :
  - `RemoteEmbedder` et `RemoteReranker` contre un faux serveur ;
  - un modèle annoncé différent est refusé ;
  - un serveur injoignable passe au CPU avec `fallback: cpu`, et échoue clairement avec `fallback: none` ;
  - le retour au GPU après une nouvelle sonde.
- **Moteur** : un `add_text` puis un `search` avec `kind: remote` contre un faux serveur, sans torch importé.
- **Contrats d'import** : `kind: remote` n'importe pas torch.
- **En vrai** : sur la RTX 5060 Ti ou une VM, comparer l'ordre des résultats et les scores avec ceux du CPU sur `test15.jsonl` (`loom memory eval`), et mesurer la durée d'une recherche et d'un `reindex`.

### Pistes non retenues

- **Les serveurs d'embedding génériques** (Hugging Face TEI, Infinity) : ils servent souvent le dense et le reranking, mais rarement le lexical de BGE-M3 tel que loom le calcule. Avec le même problème de compatibilité qu'Ollama.
- **Déporter tout loom sur la VM** : c'est possible, mais la VM doit alors rester allumée tant que l'agent sert. C'est le contraire du « quand c'est nécessaire ».

---

## Limites connues et acceptées

- **Les variantes et les évals refusent toute action de la mémoire dès qu'une écriture est permise**, même `search`. C'est la règle du moteur (`strictest_side_effects`) : un outil à actions est jugé sur la pire d'entre elles. C'est le prix de l'option B, un seul outil. Si des évals de recherche deviennent nécessaires, il faudra un agent d'éval avec `actions: [search, get, list_docs]` seulement.
- **`add_file` et `add_url` ne détectent un doublon qu'à l'exécution.** Ils doivent lire le fichier ou la page pour le savoir. L'approbation est donc toujours demandée, puis `duplicate_of` est rendu sans calcul.
- **`min_score`** : 0,1 par défaut, à garder tant que le fichier de questions de `loom memory eval` ne contient pas les requêtes réelles de l'agent. Le modèle reformule, et ses requêtes scorent plus bas : 0,46 et 0,18 à test-mem-7, contre 0,87 à 0,999 pour les questions écrites à la main.
- **Recherche sur CPU** : environ 10 s avec `rerank_candidates: 8`, et 22 s avec 20 candidats (le défaut). Le reranker est le poste de coût dominant.

---

## Réglages validés pour `agent-mem` (rappel)

```yaml
- python: loom.memory
  timeout: 600
  store: { path: memoire.db }
  project: perso
  folders:
  - { name: workspace, path: workspace }
  models: { device: cpu, preload: true, offline: true }
  retrieval: { rerank_candidates: 8, full_chunk_chars: 2400 }
  actions: [ search, get, list_docs, add_text, add_url, add_file, update, delete ]
```

Le `timeout: 600` peut être retiré depuis le délai propre de 300 s, sauf pour des fichiers de plus de 150 Ko environ sur CPU (point 3).