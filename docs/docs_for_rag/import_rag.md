# Importer la doc de loom_v2 dans un RAG (`loom.memory`)

Ce guide met toute la documentation de loom_v2 dans la mémoire d'un agent (`loom.memory`). L'agent peut alors répondre aux questions sur loom_v2 : sa configuration, ses outils fournis, sa CLI, son API, son code et ses choix de conception.

## 1. Ce que contient ce dossier

| Fichier | Rôle |
|---|---|
| `build_rag_docs.py` | Le générateur : il écrit les pages et le JSONL. |
| `generated_docs/` | Les pages Markdown générées : `code/`, `config/`, `tools/`, `cli/`, `rest/`. |
| `generated_docs.jsonl` | Ce que `loom memory import` charge : les pages générées et les documents existants retenus. |
| `questions_test_rag.jsonl` | Le jeu doré de `loom memory eval` : les 57 questions de `questions.md`, chacune avec la page qui y répond. |
| `questions.md` | Les questions de référence et les faits clés de chaque réponse. |
| `verif_docs.py` | Vérifie qu'un lot de docstrings n'a changé que de la documentation. |
| `conventions.md`, `glossaire.md` | Les règles d'écriture des docstrings, et le sens des codes (fonctions, points, décisions). |

## 2. Générer la doc

Depuis la racine du dépôt :

```bash
uv run python docs/docs_for_rag/build_rag_docs.py
```

Le script lit le code sans l'importer, ou en n'important que des modules légers. Il tourne donc sans les extras (`office`, `pdf`, `sql`, `memory`…). Son bilan ressemble à ceci :

```
355 pages (cli 21, code 280, config 28, doc 15, outil 10, rest 1) ; 0 écrite(s), 0 supprimée(s) ; docs/docs_for_rag/generated_docs.jsonl : 2452 documents
```

Ce qu'il produit :

- **`code/`** : une page par module de `src/loom_ia`, tirée des docstrings. Un module qui ne contient que des constantes n'a pas de page.
- **`config/`** : la référence de `loom.yaml` et `agent.yaml`. Chaque clé y figure avec son chemin complet (`execution.tools.timeout`), son type, son défaut, ses bornes et sa description.
- **`tools/`** : une page par outil fourni (`loom.sql`, `loom.memory`…). Elle dit ce que fait l'outil et donne son mode d'emploi, ses paramètres dans `agent.yaml` et les arguments que le modèle lui passe.
- **`cli/`** : une page par commande `loom`.
- **`rest/`** : les routes HTTP.

Le JSONL reprend ces pages, plus les documents existants listés dans `EXISTING_DOCS`, en tête du script :

- `README.md` ;
- `configuration.md`, `conception.md`, `fonctions.md`, `style_format.md` ;
- `loom-mem-gpu.md`, `loom_role_user.md`, `state-et-context.md` ;
- `glossaire.md` ;
- les six guides de `howto/`.

Les analyses d'impact n'y sont pas : elles décrivent des options écartées et des états dépassés.

Bon à savoir :

- **Découpage.** Dans le JSONL, un document de plus de 8 000 caractères (`SPLIT_OVER`) est coupé à ses titres `##`, puis à ses `###` si une section reste trop grosse. Le titre d'une section devient « titre de la page > titre de la section ». Ainsi, `get` rend une section lisible, et la recherche trouve des passages dans plus de sections. Les fichiers sur le disque ne sont pas découpés.
- **Identifiants stables.** L'identifiant d'un document est tiré de son chemin et de sa section, par exemple `docs/configuration.md#security`. Il reste le même d'une génération à l'autre, tant que la section garde son titre.
- **Écritures sur le disque.** Le script n'écrit une page que si son texte change, et supprime une page qui n'est plus générée.

## 3. Configurer l'agent

L'agent a besoin de `loom.memory` avec trois choses :

- **le projet `loom_v2`**, imposé : c'est le projet de tous les documents du JSONL. Avec un autre projet, l'agent ne verrait rien ;
- **les modèles BGE-M3** : soit calculés par `loom-mem-gpu`, soit dans loom avec l'extra `memory` ;
- **des actions en lecture seule**, pour que la base de doc ne bouge pas.

`agent.yaml` :

```yaml
name: agent
description: Répond aux questions sur loom_v2 à partir de sa documentation, rangée dans la mémoire.

main:
  model: HAIKU
  system_file: agent.md

max_iterations: 8

tools:
- python: loom.memory
  store: { path: data/memory.db }                    # le dossier doit exister
  project: loom_v2                                   # le projet des documents du JSONL
  models: { server: { url: http://127.0.0.1:8765 } } # BGE-M3 calculé par loom-mem-gpu
  actions: [ search, get, list_docs ]                # lecture seule
```

Pour calculer dans loom au lieu de `loom-mem-gpu`, remplace `models` par `{ device: cuda }` (ou `cpu`) et installe `loom-ia[memory]`. Pour garder `loom-mem-gpu` avec un repli dans loom quand il est arrêté, ajoute `fallback: { device: cuda }` sous `server`.

Le prompt (`prompts/agent.md`) qui a donné les résultats de la section 6 :

```markdown
Tu réponds aux questions sur loom_v2 (paquet `loom_ia`) : sa configuration (`loom.yaml`, `agent.yaml`), ses outils fournis, sa ligne de commande, son API, son code et ses choix de conception.

Ta seule source est la documentation de loom_v2, dans ta mémoire (outil `memory`) :

- Cherche toujours avant de répondre (`search`), même si tu crois connaître la réponse. Reformule et cherche à nouveau si les passages trouvés ne répondent pas.
- Un passage marqué `truncated` se lit en entier avec `get`.
- Réponds seulement avec ce que disent les passages. Si la documentation ne donne pas la réponse, dis-le, au lieu de la deviner.
- Donne les noms exacts : clés YAML avec leur chemin (`execution.tools.timeout`), valeurs par défaut, commandes, noms de classes et de fonctions.
- Termine par les sources : le titre de chaque document utilisé.

Réponds en français, de façon précise et concise.
```

## 4. Importer

Dans le projet de l'agent, sur une base vide :

```bash
uv run loom memory import agent <chemin du dépôt>/docs/docs_for_rag/generated_docs.jsonl
```

`agent` est le nom de l'agent dont la config porte `loom.memory`. L'import découpe chaque document en passages et calcule leurs vecteurs : environ 4 600 passages, l'étape la plus longue.

`import` saute un document déjà présent, par identifiant ou par contenu, **même s'il a changé**. Pour charger une nouvelle version de la doc, il faut donc repartir d'une base vide (voir la section 7).

Pour vérifier :

```bash
sqlite3 data/memory.db "SELECT count(*) FROM documents; SELECT count(*) FROM chunks;"
uv run loom run agent "Quels sont les paramètres de l'outil fourni loom.sql ?" --user dev
```

## 5. Mesurer

**La recherche seule**, avec le jeu doré :

```bash
uv run loom memory eval agent <chemin du dépôt>/docs/docs_for_rag/questions_test_rag.jsonl --k 10
```

Ajoute `--json > eval.json` pour avoir le rang de chaque question. Le jeu doré n'accepte qu'une page attendue par question. Or la même réponse se trouve souvent dans plusieurs documents : la référence générée, `configuration.md`, un guide `howto/`. Un rang 2, ou une page « absente », ne veut donc pas forcément dire que la réponse manque. Pour juger, il faut regarder ce qui est sorti à la place.

**L'agent de bout en bout**, la mesure qui compte. L'agent répond aux 57 questions, chacune dans sa session `eval-<code>`. On vérifie ensuite chaque réponse contre les faits clés de `questions.md`.

```bash
uv run python - <<'EOF'
import json, subprocess
for line in open("<chemin du dépôt>/docs/docs_for_rag/questions_test_rag.jsonl"):
    case = json.loads(line)
    code = case["title"].split(" ")[0]
    subprocess.run(["uv", "run", "loom", "run", "agent", case["query"], "--user", "dev", "--session", f"eval-{code}", "-q"], check=False)
EOF
```

Les réponses sont dans le journal de l'agent, une session par question.

## 6. Résultats de référence (17/11/2026)

Avec Haiku, BGE-M3 servi par `loom-mem-gpu` et les réglages de recherche par défaut :

- **Recherche** (`eval`) : recall@1 0,26, recall@5 0,68, MRR 0,44. Le document sorti premier contient la réponse pour environ 45 questions sur 57.
- **De bout en bout** : 53 réponses justes et complètes sur 57, 4 incomplètes, aucune fausse. Coût : 0,37 $, soit 0,007 $ par question, avec 2 recherches en moyenne.

Les réponses incomplètes :

- **C10** : l'agent hésite sur le state d'un sous-agent.
- **U06** : il manque `queue.max_running`.
- **U12** : il manque la limite de taille du corps de requête.
- **C13** : il ne nomme pas l'état `FINALIZING`.

## 7. Mettre à jour après un changement

1. Régénère la doc (section 2).
2. Dans le projet de l'agent, mets la base de côté et recharge :

   ```bash
   mv data/memory.db data/memory.db.old
   uv run loom memory import agent <chemin du dépôt>/docs/docs_for_rag/generated_docs.jsonl
   ```

3. Si un titre de section a changé, l'identifiant de la page change aussi. Vérifie alors que les `doc_id` de `questions_test_rag.jsonl` existent toujours dans `generated_docs.jsonl`.

## 8. Limites connues

- **Sections non découpées.** 66 sections dépassent encore 8 000 caractères parce qu'elles n'ont pas de sous-titre. La plus grosse, dans `fonctions.md`, fait environ 83 000 caractères. `search` y trouve des passages, mais `get` en rend un résultat trop gros, déporté en artefact.
- **Doublons.** `configuration.md` et la référence générée `config/` couvrent les mêmes clés. Les deux sortent dans les résultats. C'est voulu : l'une donne des exemples, l'autre est exhaustive.
- **Ce que voit le modèle.** Les docstrings des modèles d'arguments de `loom.excel` (`ColumnSpec`, `RowSpec`…) sont absentes, car le modèle les verrait à chaque appel. Leur description est dans la docstring du module `excel/arguments.py`.
