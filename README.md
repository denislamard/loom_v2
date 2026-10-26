# Loom-IA

[![PyPI](https://img.shields.io/pypi/v/loom-ia)](https://pypi.org/project/loom-ia/)
[![Python](https://img.shields.io/pypi/pyversions/loom-ia)](https://pypi.org/project/loom-ia/)
[![CI](https://github.com/denislamard/loom_v2/actions/workflows/ci.yml/badge.svg)](https://github.com/denislamard/loom_v2/actions/workflows/ci.yml)
[![Licence](https://img.shields.io/badge/licence-Apache%202.0-blue)](LICENSE)

Loom-IA est un moteur d'agents IA écrit en Python asynchrone. Vous décrivez un agent, c'est-à-dire le modèle qui le pilote, les outils qu'il a le droit d'appeler et les règles qu'il doit respecter. Loom-IA le fait tourner et garde une trace de tout ce qui se passe. On peut s'en servir comme d'une librairie Python, l'exposer en API REST ou le brancher comme serveur MCP, et c'est le même moteur dans les trois cas.

Il a été pensé pour des agents qui travaillent pour de vrai, chez des artisans et dans des PME. Relancer un devis, répondre à un client, préparer un document : sur ce genre de tâche, un montant inventé ou un e-mail parti deux fois ne se rattrapent pas avec des excuses.

```bash
pip install "loom-ia[anthropic,http,mcp]"
```

Pour apprendre à s'en servir, une [formation en quinze modules](https://denislamard.github.io/docs_loom_v2/) construit pas à pas un agent de production.

## Pourquoi Loom-IA

Faire tourner un agent dans un notebook, c'est l'affaire d'un après-midi. Le confier à un plombier qui s'en servira tous les jours demande autre chose. L'agent ne doit pas inventer de montant, ni coûter plus cher qu'il ne rapporte. Avant d'envoyer quoi que ce soit, il demande la permission, et un redémarrage du serveur ne doit pas lui faire perdre le fil. Le jour où il répond quelque chose d'étrange, on veut pouvoir comprendre pourquoi.

C'est cette partie que Loom-IA prend en charge. Le métier reste chez vous, dans vos outils, vos prompts et vos règles.

Tout part du journal. Chaque fait d'un run y est écrit au moment où il se produit, qu'il s'agisse d'un message, d'un appel de modèle ou d'outil, d'une décision ou d'un coût. L'état du run, l'historique, les coûts et les traces ne sont que des lectures de ce journal. C'est ce qui permet de reprendre un run après un plantage, de le laisser des heures en attente d'une validation, ou de le rejouer à l'identique la semaine suivante.

Le modèle propose, mais c'est le code qui décide. Les budgets, les contrôles de sortie, les demandes d'approbation et le déclenchement d'un juge sont déterministes et branchés à des endroits précis de la boucle. Le modèle n'a aucun moyen de sauter une validation. Il ne fait pas non plus les calculs, qui sont confiés à un outil.

Enfin, une tâche bien délimitée n'a pas besoin du modèle le plus cher. Rédiger un e-mail ou décrire une photo peut partir vers un modèle plus léger, qui ne reçoit que ce dont il a besoin. Le gros modèle sert à réfléchir, pas à recopier un devis.

### Trois façons de s'en servir

| Accès | Pour qui | Ce qu'il apporte |
|---|---|---|
| Librairie Python | Votre application Python | Vos outils Python, le déroulé en direct, la validation humaine dans le code |
| API REST | Une appli mobile, un front, un autre service | Runs synchrones ou en arrière-plan, flux SSE, approbations, lecture des sessions et des traces |
| Serveur MCP | Claude Desktop, Claude Code, un autre agent | Chaque agent devient un outil MCP |

À côté de ces trois accès, la commande `loom` sert à lancer un run, à le suivre, à l'inspecter ou à le rejouer, et à valider une configuration. Quel que soit le chemin emprunté, la configuration et le moteur sont les mêmes, si bien qu'un agent laisse la même suite d'événements dans le journal.

### Ce que Loom-IA ne fait pas

Il n'y a pas d'interface graphique. Loom-IA fournit en revanche de quoi en construire une, avec des traces en arbre, une API de lecture et un schéma d'événements versionné. La logique métier n'est pas non plus de son ressort, puisqu'elle vit dans vos outils et vos prompts. Et ce n'est pas un framework où des agents discutent entre eux : la délégation reste hiérarchique, l'orchestrateur confie une tâche à un rôle ou à un sous-agent qui lui rend un résultat.

## Installation

Loom-IA tourne sous Linux, avec Python 3.14 ou plus récent. On l'installe de préférence avec [uv](https://docs.astral.sh/uv/), mais pip marche aussi.

```bash
uv add "loom-ia[anthropic,http,mcp]"
# ou, dans un environnement virtuel :
pip install "loom-ia[anthropic,http,mcp]"
```

Le paquet s'appelle `loom-ia`. Il s'importe sous le nom `loom_ia` et installe la commande `loom`. Son noyau se contente de pydantic, pyyaml, jsonschema, regex et httpx2 ; tout le reste s'ajoute par des extras, selon ce dont vous avez besoin.

| Extra | À installer si vous utilisez |
|---|---|
| `anthropic` | Claude, ou un fournisseur qui expose l'API d'Anthropic |
| `openai` | OpenAI ou un fournisseur compatible comme Together, vLLM ou Ollama |
| `mcp` | Des serveurs MCP comme outils, ou `loom mcp` |
| `http` | L'API REST et le serveur MCP en HTTP, avec `loom serve` |
| `sqlite` | Le journal ou le magasin d'idempotence en SQLite |
| `postgres` | Le journal, l'idempotence ou le bus en Postgres |
| `redis` | Le bus ou l'idempotence en Redis |
| `rabbitmq` | La file de tâches RabbitMQ et `loom worker` |
| `otel` | L'export des traces vers OpenTelemetry |
| `crypto` | Le chiffrement du journal et des fichiers, client par client |
| `office` | L'outil fourni `loom.excel`, qui écrit des classeurs Excel |
| `sql` | L'outil fourni `loom.sql`, qui lit et modifie des bases SQLite ou Postgres |
| `all` | Tous les extras |

Si votre configuration réclame un extra qui n'est pas installé, Loom-IA refuse de la charger et vous dit lequel ajouter.

Les exemples du dossier `examples/` et la sandbox Firecracker ne font pas partie du paquet. Pour les avoir, il faut cloner le dépôt :

```bash
git clone https://github.com/denislamard/loom_v2.git
cd loom_v2
uv sync --all-extras
uv run loom --config examples/j1/demo/loom.yaml run demo "Combien font 12 × 7 + 3 ?"
```

La dernière commande lance un agent de démonstration sur un modèle simulé, ce qui permet de vérifier l'installation sans clé d'API.

## Premier agent

Prenons un petit agent qui répond aux questions de prix d'un artisan. Pour calculer un TTC, il ne compte pas de tête : il appelle un outil Python. On le fait tourner sur un modèle simulé, sans clé ni réseau.

```
mon-agent/
├── loom.yaml          modèles, stockage, modules à importer
├── outils.py          vos outils Python
├── agents/devis.yaml  un fichier par agent
└── prompts/devis.md   le prompt système de l'agent
```

L'outil se trouve dans `outils.py`. Loom-IA déduit le schéma des arguments de la signature de la fonction, et la docstring devient la description que lira le modèle.

```python
from loom_ia.tools import tool


@tool
def prix_ttc(montant_ht: float, taux_tva: float = 20.0) -> dict[str, float]:
    """Calcule la TVA et le montant TTC d'un devis. Le taux est en pourcentage."""
    tva = round(montant_ht * taux_tva / 100, 2)
    return {"montant_ht": montant_ht, "tva": tva, "montant_ttc": round(montant_ht + tva, 2)}
```

L'agent est décrit dans `agents/devis.yaml` :

```yaml
name: devis
description: Répond aux questions de prix sur les devis.
main:
  model: SIMULE
  system_file: devis.md
max_iterations: 5
tools:
  - python: prix_ttc
```

Son prompt système tient en deux lignes, dans `prompts/devis.md` :

```markdown
Tu es l'assistant d'un artisan. Tu réponds en français, en une ou deux phrases.
Pour tout calcul de prix, appelle l'outil `prix_ttc` au lieu de calculer toi-même.
```

Reste la configuration racine, `loom.yaml`. Le modèle `SIMULE` y joue un script : au premier tour il appelle `prix_ttc`, au second il répond.

```yaml
version: 1
imports: [outils]
agents_dir: agents/
prompts_dir: prompts/

models:
  - id: SIMULE
    sdk: fake                  # modèle simulé : ni clé, ni réseau
    model: fake-1
    params:
      script:
        - text: Je calcule le prix TTC.
          tool_calls:
            - name: prix_ttc
              arguments: {montant_ht: 1250, taux_tva: 10}
        - text: Pour 1 250 € HT avec une TVA à 10 %, votre client paiera 1 375 € TTC.

storage:
  events: {backend: jsonl, path: data}   # le journal : un fichier JSONL par session
```

On vérifie la configuration, puis on lance un run :

```bash
cd mon-agent
loom validate
loom run devis "Quel prix TTC pour un devis de 1 250 € HT en rénovation (TVA à 10 %) ?"
```

```
Statut     : completed · itérations : 2 · tokens : 684/110 · coût : 0.0000 $
Run        : 01a11f47-ae95-7161-91ab-ee7c2558ef41
Pour 1 250 € HT avec une TVA à 10 %, votre client paiera 1 375 € TTC.
```

`loom inspect`, suivi de l'identifiant du run, affiche ensuite son arbre avec chaque appel de modèle et d'outil, sa durée et son coût.

Passer à un vrai modèle demande trois changements. On déclare le modèle dans `models` avec `sdk: anthropic` et le nom de la variable qui porte la clé, on écrit cette clé dans un fichier `.env` à côté de `loom.yaml`, puis on change le `model:` de l'agent. Pour aller plus loin avec les conversations, la validation humaine, les rôles et les juges, le plus simple est de suivre [la formation](https://denislamard.github.io/docs_loom_v2/module-01.html).

## Ce que Loom-IA sait faire

Le tableau fait le tour des fonctions, domaine par domaine. Pour chacun, il indique les modules de la formation qui en parlent et le dossier d'exemples où les voir tourner.

| Domaine | En bref | Modules | Exemples |
|---|---|---|---|
| Outils | Outils Python avec `@tool`, serveurs MCP, outils fournis par des paquets, mémoire long terme | 2, 5, 15 | `j2`, `j6` |
| Modèles | Anthropic et fournisseurs compatibles OpenAI, cache de prompt, retry, modèle de secours, disjoncteur | 3, 9 | `j1`, `j3` |
| Conversations | Sessions, compaction vérifiée, state typé, export et effacement RGPD | 4 | `j4`, `j7` |
| Rôles et sous-agents | Rédaction confiée à un modèle léger, rôle vision, sous-agents dans le même journal | 6 | `j2` |
| Fiabilité | Sortie structurée, contrats, réparation, juges, politiques | 7 | `j3` |
| Actions sensibles | Approbation humaine, idempotence, reprise après plantage, annulation | 8 | `j4` |
| Coûts | Coût par run, rôle, modèle, session et client, budgets et quotas | 9 | `j3`, `j5` |
| Accès | Librairie, API REST protégée par clé, serveur MCP, runs en arrière-plan, SSE | 10 | `j1`, `j5` |
| Automatisation | Webhooks entrants, file RabbitMQ et workers, bus entre process | 11 | `j5` |
| Plusieurs clients | Espaces isolés, secrets et quotas par client, chiffrement, rétention, RLS Postgres | 12 | `j5` |
| Observabilité | Trace en arbre, export OpenTelemetry masqué, rejeu exact ou en variante | 13 | `j6` |
| Qualité | Suites d'évaluations, kit de test `Bench`, non-régression tirée du journal | 14 | `j6` |
| Sandbox | L'agent écrit ses propres outils, testés dans une microVM Firecracker | 15 | `j6` |

Les exemples se trouvent dans `examples/`, à prendre au tag de la version que vous utilisez. La plupart tournent avec des modèles simulés, et l'option `--reel` les relance avec de vrais modèles.

## Référence rapide

Cette section rassemble les tables qu'on vient chercher le plus souvent. La référence complète de la configuration est dans [`docs/configuration.md`](docs/configuration.md) et dans [la page de référence](https://denislamard.github.io/docs_loom_v2/reference.html) de la formation, et `loom schema` imprime son JSON Schema.

### Ligne de commande

| Commande | Rôle |
|---|---|
| `loom validate` | Vérifier la configuration et monter les agents |
| `loom run <agent> "<message>"` | Lancer un run, avec `--stream`, `--session`, `--attach` ou `--json` |
| `loom resume <run_id>` | Reprendre un run interrompu |
| `loom approve` · `loom reject <run_id>` | Trancher une demande d'approbation |
| `loom inspect <run_id>` | Afficher l'arbre d'un run |
| `loom report` | Consommation d'un run, d'une session ou d'un client |
| `loom replay <run_id>` | Rejouer un run, à l'identique ou en variante |
| `loom eval <suite.yaml>` | Jouer une suite d'évaluations |
| `loom serve` | Servir l'API REST, avec `--reload` en développement |
| `loom mcp` | Servir les agents en MCP sur stdio |
| `loom worker` | Consommer la file de tâches |
| `loom sessions list` · `export` · `delete` | Gérer les sessions |
| `loom keys create <id>` | Fabriquer une clé d'API |
| `loom retention` | Effacer les sessions trop anciennes |
| `loom storage sql` | Imprimer le SQL du stockage Postgres |
| `loom schema` | Imprimer le JSON Schema de la configuration |

Quatre options sont communes à toutes les commandes : `--config`, qui vaut `loom.yaml` par défaut, `--profile`, `--env-file` et `--no-env-file`. Elles se placent indifféremment avant ou après la commande.

### API Python

Toutes ces méthodes appartiennent à la classe `Loom`, que l'on importe depuis `loom_ia.access`.

| Méthode | Rôle |
|---|---|
| `Loom.from_config(chemin)`, `Loom(config)` | Charger une configuration |
| `run()`, `stream()`, `submit()` | Lancer un run et attendre sa fin, le suivre en direct, ou le mettre en arrière-plan |
| `result()`, `state()`, `events()`, `follow()` | Relire un run, suivre son journal |
| `cancel()`, `resume()`, `recover()` | Arrêter, reprendre, remettre en file les runs restés en plan |
| `approve()`, `reject()` | Trancher une demande d'approbation |
| `session()`, `sessions()`, `export_session()`, `delete_session()` | Gérer les conversations |
| `runs()`, `query()`, `trace()` | Lister les runs, chercher dans le journal, lire une trace |
| `report()`, `consumption()` | Consommation d'un run, d'une session, d'un client |
| `replay()`, `replay_journal()`, `evaluate()` | Rejeu et évaluations |
| `trigger()` | Ouvrir un run par une porte déclarée |
| `register()` | Rendre un objet Python référençable depuis la configuration |

Contrairement à la commande `loom`, la librairie ne lit aucun fichier `.env`. Pour retrouver le même comportement, appelez `load_env_file("loom.yaml")`, fourni par `loom_ia.config`, avant `Loom.from_config`.

### API REST

`loom serve` démarre l'API, dont la documentation interactive se trouve sur `/docs`. Pour la greffer sur une application FastAPI existante, passez plutôt par `create_app(loom)`, dans `loom_ia.access.http`.

| Méthode | Route | Rôle | Portée |
|---|---|---|---|
| `GET` | `/v1/agents` | Lister les agents | `read` |
| `POST` | `/v1/agents/{name}/runs` | Lancer un run, en JSON ou en multipart avec pièces jointes | `run` |
| `GET` | `/v1/runs` | Lister les runs du client | `read` |
| `GET` | `/v1/runs/{id}` | Statut et résultat | `read` |
| `GET` | `/v1/runs/{id}/events` | Déroulé en SSE, reprise par `Last-Event-ID` | `read` |
| `POST` | `/v1/runs/{id}/approve` · `/reject` | Validation humaine | `approve` |
| `POST` | `/v1/runs/{id}/cancel` | Arrêt | `run` |
| `GET` | `/v1/sessions` · `/v1/sessions/{id}` | Sessions, fiche d'une session | `read` |
| `GET` | `/v1/sessions/{id}/events` | Journal d'une session en JSONL | `read` |
| `GET` | `/v1/sessions/{id}/report` | Consommation d'une session | `read` |
| `DELETE` | `/v1/sessions/{id}` | Effacement RGPD | `admin` |
| `GET` | `/v1/events` | Recherche dans le journal | `read` |
| `GET` | `/v1/traces/{run_id}` | Trace d'un run | `read` |
| `POST` | `/v1/hooks/{nom}` | Webhook entrant | `run` |

Une requête présente sa clé dans `Authorization: Bearer lk_…` ou dans `X-API-Key`, et c'est cette clé seule qui détermine le client. Tant qu'aucune clé n'est déclarée, l'API reste ouverte. C'est commode sur votre machine, et c'est justement ce que le profil `prod` refuse.

### Serveur MCP

`loom mcp` publie les agents en stdio. Pour les servir en HTTP, ajoutez `server: {mcp: {http: true}}` à la configuration et ils apparaîtront sous `/mcp` dans `loom serve`. Chaque agent devient un outil qui accepte un message, un identifiant de session et des pièces jointes. Trois outils de contrôle l'accompagnent, `run_status`, `run_report` et `cancel`, et le journal se lit sous forme de ressources `loom://`. Aucune approbation ne passe par un outil MCP, de sorte qu'un modèle ne peut jamais valider lui-même une action sensible.

Pour brancher vos agents sur Claude Code :

```bash
claude mcp add loom -- loom --config /chemin/vers/mon-agent/loom.yaml mcp
```

### Stockages

| Stockage | Backends | Par défaut |
|---|---|---|
| Journal, `storage.events` | `memory`, `jsonl`, `sqlite`, `postgres` | `memory` |
| Fichiers, `storage.artifacts` | `local`, `memory` | suit le journal |
| Idempotence, `storage.idempotency` | `journal`, `memory`, `sqlite`, `postgres`, `redis` | `journal` |
| File de tâches, `storage.queue` | `asyncio`, `rabbitmq` | `asyncio` |
| Bus entre process, `storage.bus` | `memory`, `postgres`, `redis` | `memory` |

Sur votre machine, un journal JSONL suffit. En service, on confie en général le journal et l'idempotence à Postgres, le bus à Redis ou à Postgres, et la file à RabbitMQ. Méfiez-vous du journal en mémoire, qui est pourtant le réglage par défaut : rien n'y survit à l'arrêt du process.

### Architecture

```
Accès          API Python · REST · MCP · CLI
Application    Agents · Runtime · Sessions · Clients · Rejeu et évaluations
Moteur         Boucle · Exécution des outils · Rôles · Contrôles · Politiques
Noyau          Modèle de domaine · Ports · Événements
Adaptateurs    Modèles · MCP · Journaux · Bus · Files · Sandbox
```

Le noyau ne fait aucune entrée-sortie. Il définit les types du domaine et des interfaces, appelées ports, que les adaptateurs implémentent en périphérie. Ni lui ni le moteur n'importent de SDK de modèle, FastAPI ou MCP, et la CI y veille à chaque passage grâce à `import-linter`.

## Documentation

Pour apprendre, commencez par [la formation](https://denislamard.github.io/docs_loom_v2/). Ses quinze modules construisent un seul agent, de l'installation jusqu'à la sandbox. Elle comprend aussi une [référence de la CLI et de la configuration](https://denislamard.github.io/docs_loom_v2/reference.html) et une page consacrée aux [pièges et au dépannage](https://denislamard.github.io/docs_loom_v2/pieges.html).

Dans le dépôt, [`docs/configuration.md`](docs/configuration.md) détaille chaque clé de `loom.yaml` et des fichiers d'agent. [`CHANGELOG.md`](CHANGELOG.md) retrace les changements d'une version à l'autre, et [`SECURITY.md`](SECURITY.md) explique comment signaler une vulnérabilité.

## Développer et tester

```bash
git clone https://github.com/denislamard/loom_v2.git
cd loom_v2
uv sync --all-extras --group types
```

Avant de pousser, lancez les mêmes contrôles que la CI :

```bash
uv run pytest                    # la suite de tests
uv run ruff check .              # lint
uv run ruff format --check .     # format
uv run pyright                   # typage strict
uv run lint-imports              # règles de dépendances entre couches
```

Certains tests ont besoin d'un vrai Postgres, RabbitMQ ou Redis. Ils lisent l'adresse du service dans une variable d'environnement et se mettent de côté si elle manque :

```bash
export LOOM_TEST_POSTGRES=postgresql://loom:loom@127.0.0.1:5432/loom
export LOOM_TEST_RABBITMQ=amqp://loom:loom@127.0.0.1:5672/
export LOOM_TEST_REDIS=redis://127.0.0.1:6379/1
uv run pytest --require-services
```

`--require-services` change ce comportement. Un service absent ou un extra manquant fait alors échouer le test, pour qu'une suite verte prouve vraiment quelque chose. Le rôle Postgres a besoin du droit `CREATEROLE`, mais il ne doit pas être superutilisateur, sinon il passerait outre la sécurité au niveau des lignes.

La CI tourne sur GitHub Actions. Elle contrôle d'abord la qualité du code avec ruff, pyright et les contrats d'import, puis lance les tests du noyau seul, sans aucun extra. Viennent ensuite les tests complets, avec Postgres, RabbitMQ et Redis en conteneurs et une couverture en branches qui doit rester au-dessus de 94 %. Un autre job installe chaque dépendance à la plus basse version autorisée, pour vérifier que les bornes de `pyproject.toml` disent vrai, et le dernier construit le paquet. Chaque semaine, un workflow séparé refait les tests avec les versions les plus récentes. Quant à la publication, déclenchée par une étiquette `v*`, elle attend que toute la CI soit passée.

## Licence

Loom-IA est distribué sous licence [Apache 2.0](LICENSE).
