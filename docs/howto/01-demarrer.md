# Loom-IA : guide d'apprentissage

# Niveau 1 : Débutant

> **Dans ce fichier** : comprendre ce que fait Loom-IA, l'installer, écrire un premier outil Python, valider et inspecter un agent, l'appeler depuis Python, le brancher sur un vrai modèle, puis tout construire sans fichier de configuration (chapitres 1 à 6).
>
> **Navigation** : [01-demarrer.md](01-demarrer.md) (vous êtes ici) · suivant : [02-maitriser.md](02-maitriser.md)

---

## Sommaire

- [Avant de commencer](#avant-de-commencer)
- [1. Installer et lancer un premier run](#1-installer-et-lancer-un-premier-run)
- [2. Écrire un outil Python avec `@tool`](#2-écrire-un-outil-python-avec-tool)
- [3. Valider et inspecter](#3-valider-et-inspecter)
- [4. Appeler l'agent depuis Python](#4-appeler-lagent-depuis-python)
- [5. Brancher un vrai modèle](#5-brancher-un-vrai-modèle)
- [6. Tout construire en Python, sans fichier](#6-tout-construire-en-python-sans-fichier)
- [Et ensuite](#et-ensuite)

---

## Avant de commencer

### Ce que fait Loom-IA

Loom-IA est un moteur d'agents IA écrit en Python asynchrone. Vous décrivez un agent : le modèle qui le pilote, les outils qu'il peut appeler, les règles qu'il doit respecter. Loom-IA le fait tourner et écrit tout ce qui se passe dans un journal. Le même moteur s'utilise comme librairie Python, comme API REST ou comme serveur MCP, et la commande `loom` sert à le piloter à la main.

Il existe pour des agents qui travaillent pour de vrai chez des artisans et des PME : relancer un devis, répondre à un client, préparer un document. Dans ces tâches, un montant inventé ou un e-mail parti en double ne se rattrapent pas. Le principe qui en découle tient en trois phrases :

- **le journal fait foi** : chaque fait d'un run (message, appel de modèle, appel d'outil, coût) est écrit au moment où il se produit, et tout le reste s'en déduit ;
- **le code décide, le modèle propose** : budgets, validations humaines et contrôles sont du code déterministe, pas des consignes données au modèle ;
- **chaque tâche va au modèle qui lui convient** : le modèle cher réfléchit, un modèle léger rédige.

Ce premier fichier ne couvre que le début du chemin : un agent, quelques outils, un modèle. Les validations humaines, les rôles, les contrôles de sortie et les juges arrivent dans [02-maitriser.md](02-maitriser.md).

### Le fil rouge de ce guide

Tous les exemples se passent à la **Plomberie Dupont**. La société a envoyé début septembre le devis **D-2026-042** (1 840 € TTC, remplacement du chauffe-eau) à **Mme Martin**, sans nouvelles depuis. L'artisan veut un agent qui sait calculer une TVA sans se tromper, retrouver un devis et, plus tard dans le guide, relancer Mme Martin de façon cordiale, sans jamais envoyer un e-mail qu'il n'a pas validé.

On construit cet agent par couches. Un seul projet, `mon-agent/`, sert de base à tout le guide.

### Vocabulaire

| Terme | Sens | Dans la Plomberie Dupont |
|---|---|---|
| **Agent** | Une définition nommée : un orchestrateur, des outils, des rôles, des règles. Une instance de Loom-IA en héberge plusieurs. | L'agent `devis`, qui répond aux questions de prix. |
| **Orchestrateur** | Le modèle qui pilote la boucle et choisit entre répondre et appeler un outil. Dans la configuration, c'est le rôle `main`. | Le modèle qui lit « Quel prix TTC pour 1 250 € HT ? » et décide d'appeler `prix_ttc`. |
| **Rôle** | Un modèle spécialisé, appelé par l'orchestrateur comme un outil, avec son propre prompt. | Un modèle léger qui rédige l'e-mail de relance. |
| **Sous-agent** | Un agent complet appelé comme un outil, avec ses propres outils et sa propre boucle. | Un agent « vérificateur » qui relit les dates et les calculs. |
| **Outil** | Ce que l'orchestrateur peut appeler : fonction Python, outil MCP, rôle ou sous-agent. | `prix_ttc`, `chercher_devis`. |
| **Run** | Une exécution d'un agent pour une demande. | « Relance Mme Martin pour le devis D-2026-042 » : un run. |
| **Session** | Une conversation : plusieurs runs qui partagent le même journal. | Le dossier d'un chantier, suivi sur plusieurs messages. |
| **Journal** | La suite ordonnée et immuable des événements d'une session. | Le fichier `data/default/<session>.jsonl`. |
| **Politique** | Du code branché sur un point de la boucle, qui laisse passer, corrige, refuse ou arrête. | « Refuser un taux de TVA qui n'existe pas en France. » |
| **Juge** | Un modèle qui note une sortie selon des critères écrits. | Vérifier qu'aucun montant n'a été inventé dans l'e-mail. |
| **Client** | Un espace isolé (configuration, secrets, journal, budgets) qui permet de servir plusieurs entreprises avec une seule instance. | La Plomberie Dupont et le Chauffage Martin sur le même serveur. |

Ce chapitre n'utilise que les six premiers termes et le journal. Les autres ont leur chapitre : sessions et politiques dans le niveau 2, juges et contrats aussi, clients dans le niveau 3.

### Prérequis

- **[uv](https://docs.astral.sh/uv/)**, l'outil qui gère Python, le projet et ses dépendances. Tout le guide s'en sert : `uv init`, `uv add`, `uv run`. Aucune autre commande d'installation n'apparaît dans ce guide.
- **Python 3.14** ou plus récent. uv le télécharge tout seul si votre machine ne l'a pas (`uv init --python 3.14` suffit).
- **Linux**, la plateforme sur laquelle Loom-IA est développé et testé.
- **Une clé d'API** seulement pour les vrais modèles. Les chapitres 1 à 4 et 6 tournent avec un modèle simulé, sans clé ni réseau.

### Les extras

Le paquet s'appelle `loom-ia`, s'importe sous le nom `loom_ia` et installe la commande `loom`. Le noyau ne dépend que de trois bibliothèques (pydantic, pyyaml, jsonschema). Tout le reste arrive par des extras, à choisir selon ce que vous utilisez :

| Extra | À installer si vous utilisez | Utile dans ce fichier |
|---|---|---|
| `anthropic` | Claude, ou un fournisseur qui expose l'API d'Anthropic | chapitre 5 |
| `openai` | OpenAI ou un fournisseur compatible (Together, vLLM, Ollama…) | chapitre 5 |
| `mcp` | Des serveurs MCP comme outils, ou `loom mcp` | niveau 2 |
| `http` | L'API REST et le serveur MCP en HTTP (`loom serve`) | niveau 3 |
| `sqlite` | Le journal ou le magasin d'idempotence en SQLite | niveau 3 |
| `postgres`, `redis`, `rabbitmq` | Journal, bus et file de tâches en service | niveau 3 |
| `otel` | L'export des traces vers OpenTelemetry | niveau 4 |
| `crypto` | Le chiffrement du journal, client par client | niveau 3 |
| `all` | Tous les extras | |

Si la configuration demande un extra absent, Loom-IA refuse de la charger et nomme l'extra à installer. Ce guide installe d'emblée `anthropic`, `openai`, `http`, `mcp` et `sqlite` : cela évite de revenir en arrière plus loin.

### Comment lire ce guide

Chaque exemple suit toujours le même plan, avec ces cinq sous-titres :

1. **Pourquoi** : le besoin concret, vu par un artisan.
2. **Objectif** : ce que vous saurez faire à la fin.
3. **Mise en place** : les commandes `uv` et les fichiers, en entier.
4. **Exécution** : la commande à lancer et sa sortie.
5. **À retenir** : les points clés et, quand il y en a un, le piège courant.

Les exemples vont du plus simple au plus complexe, dans ce fichier comme dans l'ensemble du guide. Quelques conventions sur les sorties :

- **Les sorties sont réelles.** Chaque exemple a été exécuté avec la version **2.0.0** de `loom-ia`, celle de PyPI, sur Linux avec Python 3.14.6. Les identifiants de run (`01a1…`) changeront chez vous, ainsi que les durées en millisecondes, et les chemins absolus sont abrégés en `/home/denis/mon-agent`. Le reste doit se retrouver à l'identique.
- **Un modèle simulé joue les exemples.** Le modèle `sdk: fake` répond selon un script écrit dans la configuration, sans lire votre question. Cela rend les sorties reproductibles et gratuites, mais cela ne teste ni la qualité d'un vrai modèle ni la façon dont il utilise vos outils. Ce qui est vérifié : vos outils, vos schémas, votre configuration, le journal.
- **Ce qui ne peut pas tourner sans ressource extérieure est signalé.** Un encadré **« Non exécuté ici »** dit alors pourquoi et quelle partie a quand même été exécutée.
- **Les sorties longues sont abrégées par `…`** et le texte le dit.
- **Les prompts sont écrits au tutoiement** parce qu'ils s'adressent au modèle ; le reste du guide vous vouvoie.
- **Vous travaillez toujours depuis le dossier du projet** (`cd mon-agent`), car la commande `loom` cherche `loom.yaml` dans le dossier courant.

### Le plan des quatre fichiers

| Fichier | Chapitres | Contenu |
|---|---|---|
| **01-demarrer.md** (celui-ci) | 1 à 6 | Installer, premier outil, valider et inspecter, appel Python, vrai modèle, config en Python |
| [02-maitriser.md](02-maitriser.md) | 7 à 14 | Sessions, variables et profils, validation humaine, politiques, contrats, rôles, MCP, juges |
| [03-production.md](03-production.md) | 15 à 24 | Résilience, budgets, sous-agents, gros résultats, exécution durable et idempotence, REST, serveur MCP, plusieurs clients, webhooks et workers, Postgres, chiffrement, RGPD |
| [04-qualite-et-expert.md](04-qualite-et-expert.md) | 25 à 30 et annexes | Traces et OpenTelemetry, rejeu, évaluations, tests, sources d'outils (forge, loom-notes), projet de synthèse |

---

## 1. Installer et lancer un premier run

### Exemple 1.1 : le plus petit projet qui tourne

#### Pourquoi

Avant d'écrire le moindre outil, l'artisan (ou plutôt son développeur) veut vérifier que la chaîne complète fonctionne : l'installation, la lecture de la configuration, un run, l'écriture du journal. S'il y a un problème, mieux vaut le trouver maintenant que dans un agent de 200 lignes.

#### Objectif

Créer le projet `mon-agent/`, installer Loom-IA, valider la configuration et lancer un premier run, sans outil, sans clé et sans réseau.

#### Mise en place

Créez le projet avec uv, puis ajoutez Loom-IA et ses extras :

```bash
uv init --bare --python 3.14 mon-agent
cd mon-agent
uv add "loom-ia[anthropic,openai,http,mcp,sqlite]"
```

`uv init --bare` ne crée que `pyproject.toml`, sans fichier d'exemple. `uv add` crée l'environnement virtuel `.venv`, installe Loom-IA et note la dépendance dans le projet. Après `uv add`, le `pyproject.toml` ressemble à ceci :

```toml
[project]
name = "mon-agent"
version = "0.1.0"
requires-python = ">=3.14"
dependencies = [
    "loom-ia[anthropic,http,mcp,openai,sqlite]>=2.0.0",
]
```

Un projet Loom-IA tient en un fichier de configuration racine, un dossier d'agents et un dossier de prompts. Créez les deux dossiers :

```bash
mkdir agents prompts
```

Puis les trois fichiers.

`loom.yaml` (la configuration racine) :

```yaml
version: 1

agents_dir: agents/
prompts_dir: prompts/

models:
  - id: SIMULE
    sdk: fake
    model: fake-1

storage:
  events: {backend: jsonl, path: data}
```

`agents/devis.yaml` (un fichier par agent) :

```yaml
name: devis
description: Répond aux questions de prix sur les devis.

main:
  model: SIMULE
  system_file: devis.md
```

`prompts/devis.md` (le prompt système, cherché dans `prompts_dir`) :

```markdown
Tu es l'assistant d'un artisan. Tu réponds en français, en une ou deux phrases.
```

Voici l'arborescence obtenue (hors `.venv`) :

```
mon-agent/
├── pyproject.toml
├── uv.lock
├── loom.yaml          modèles, stockage, modules à importer
├── agents/
│   └── devis.yaml     un fichier par agent
└── prompts/
    └── devis.md       le prompt système de l'agent
```

Que dit `loom.yaml` ? `models` déclare les modèles utilisables. Ici un seul, `SIMULE`, avec `sdk: fake` : un modèle simulé, sans script, qui se contente de renvoyer la demande en écho. `storage.events` règle le journal : `backend: jsonl` écrit un fichier JSONL par session dans le dossier `data/`. Dans l'agent, `main` désigne l'orchestrateur (son modèle et son prompt).

#### Exécution

D'abord la validation, qui charge la configuration, monte chaque agent et affiche ce qu'elle a compris :

```bash
uv run loom validate
```

```text
Config     : loom.yaml
Profil     : aucun (ni --profile, ni LOOM_PROFILE, ni 'profile:') ; surcharges : aucune
Modèles    : SIMULE
Agents     : devis
Outils     : aucun
Paquets    : forge (loom-ia 2.0.0) (groupe loom_ia.tools)
Journal    : jsonl (/home/denis/mon-agent/data)
Artefacts  : local (/home/denis/mon-agent/data/.artifacts)
Idempotence: journal
File       : asyncio
Bus        : memory (les nouvelles ne sortent pas de ce process)
Chiffrement: aucun (contenus en clair au repos)
Rétention  : aucune (rien ne s'efface)
Clés d'API : aucune (API REST ouverte)
  devis : modèle SIMULE, 0 outil(s) Python

1 agent(s) monté(s) sans erreur.
```

Les dernières lignes de la partie haute (file, bus, chiffrement, rétention, clés) décrivent ce que Loom-IA a choisi par défaut ; vous les reverrez dans les niveaux suivants. Lancez ensuite un run :

```bash
uv run loom run devis "Bonjour, ici la Plomberie Dupont."
```

```text
2026-10-10 17:31:42 INFO     loom_ia.engine.loop — Modèle fake-1 (rôle main) : 0.00 s, 120 tokens, 0.00000 $ [run_id=01a12671-0267-77f6-97e5-e53f17e051fc span_id=01a1…]
2026-10-10 17:31:42 INFO     loom_ia.engine.loop — Transition ready_for_model → completed (agent devis) [run_id=01a12671-0267-77f6-97e5-e53f17e051fc span_id=01a1… tenant_id=default]
Écho : Bonjour, ici la Plomberie Dupont.

Statut     : completed · itérations : 1 · tokens : 85/35 · coût : 0.0000 $
Run        : 01a12671-0267-77f6-97e5-e53f17e051fc
```

(Les deux premières lignes, des logs, sont abrégées à la fin par `…`.) Le journal est apparu :

```bash
ls -R data
```

```text
data:
default

data/default:
01a12671-0267-77f6-97e5-e53f17e051fc.jsonl
```

#### À retenir

- **Un projet = un `loom.yaml`, un dossier d'agents, un dossier de prompts.** Les chemins `agents_dir` et `prompts_dir` sont relatifs au fichier `loom.yaml`.
- **`loom validate` avant tout.** Il monte les agents sans appeler de modèle : c'est la vérification la moins chère.
- **Le journal est créé au premier run.** Sans identifiant de session, la session prend l'identifiant du run : un fichier `.jsonl` par run. Dossier `default` = le client par défaut (voir le niveau 3).
- **Les lignes `INFO` viennent de l'absence de bloc `telemetry`.** Sans lui, la commande `loom` affiche une ligne de log par appel de modèle et par transition. Le chapitre 2 les fait taire.
- **Mettez `data/` dans votre `.gitignore`** si le projet est versionné : le journal contient les échanges avec vos clients.
- **Piège : le dossier courant.** `uv run loom validate` lancé hors du dossier du projet cherche `loom.yaml` là où vous êtes. Soit vous vous placez dans `mon-agent/`, soit vous écrivez `uv run loom --config /chemin/vers/mon-agent/loom.yaml validate` (l'option `--config` se place **avant** le nom de la commande).

### Exemple 1.2 : lire ce que `loom run` rend

#### Pourquoi

Un script de facturation qui appelle l'agent doit récupérer la réponse, pas le décor. Et un script de surveillance doit savoir si le run a échoué. Il faut donc savoir ce que `loom run` écrit, où, et avec quel code de retour.

#### Objectif

Séparer la réponse des informations de suivi, obtenir le résultat en JSON, et reconnaître les codes de retour.

#### Mise en place

Rien à ajouter : on reprend le projet de l'exemple 1.1.

#### Exécution

La réponse de l'agent part sur la **sortie standard**, tout le reste (logs, ligne `Statut`, ligne `Run`) sur la **sortie d'erreur**. Pour ne garder que la réponse :

```bash
uv run loom run devis "Bonjour, ici la Plomberie Dupont." 2>/dev/null
echo "code de retour : $?"
```

```text
Écho : Bonjour, ici la Plomberie Dupont.
code de retour : 0
```

Avec `--json`, le résultat complet sort sur la sortie standard, au format du `RunResult` que vous retrouverez au chapitre 4 :

```bash
uv run loom run devis "Bonjour, ici la Plomberie Dupont." --json 2>/dev/null | head -8
```

```text
{
  "run_id": "01a12671-07a2-72cb-b558-73ba052148de",
  "session_id": "01a12671-07a2-72cb-b558-73ba052148de",
  "agent": "devis",
  "status": "completed",
  "text": "Écho : Bonjour, ici la Plomberie Dupont.",
  "output": {
    "role": "assistant",
```

(Sortie abrégée aux huit premières lignes.) Un agent inconnu est une erreur de demande :

```bash
uv run loom run facturation "Bonjour"
echo "code de retour : $?"
```

```text
Agent 'facturation' inconnu (agents : devis)
code de retour : 2
```

#### À retenir

- **Sortie standard = la réponse ; sortie d'erreur = le suivi.** On peut donc écrire `reponse=$(uv run loom run devis "…" 2>/dev/null)`.
- **Codes de retour** : `0` run terminé ; `1` run échoué (vous le verrez aux chapitres 4 et 5) ; `2` configuration ou demande en cause (agent inconnu, fichier invalide, clé absente).
- **`--stream` et `--json`** changent la présentation, pas le run : le journal est le même.

---

## 2. Écrire un outil Python avec `@tool`

Un agent qui ne fait que répondre est un chatbot. Un agent utile appelle du code : calculer, chercher, écrire. Dans Loom-IA, le code que le modèle a le droit d'appeler s'écrit comme une fonction Python ordinaire.

### Exemple 2.1 : un outil de calcul de TVA

#### Pourquoi

La Plomberie Dupont fait des devis à 5,5 %, 10 % ou 20 % de TVA selon le chantier. Un modèle de langage qui calcule « de tête » finit par se tromper d'un centime, et un centime faux sur un devis est un devis contestable. Le calcul doit donc être fait par du code, et le modèle doit se contenter de recopier le résultat.

#### Objectif

Écrire l'outil `prix_ttc`, le déclarer à l'agent `devis`, et voir l'agent l'appeler au cours d'un run. Vous obtenez ainsi le **projet de base** du guide.

#### Mise en place

Créez `outils.py`, à côté de `loom.yaml` :

```python
from loom_ia.tools import tool


@tool
def prix_ttc(montant_ht: float, taux_tva: float = 20.0) -> dict[str, float]:
    """Calcule la TVA et le montant TTC d'un devis. Le taux est en pourcentage."""
    tva = round(montant_ht * taux_tva / 100, 2)
    return {"montant_ht": montant_ht, "tva": tva, "montant_ttc": round(montant_ht + tva, 2)}
```

Remplacez `loom.yaml` par la version complète suivante. Trois changements : la ligne de schéma en première ligne (chapitre 3), `imports: [outils]` pour charger le module, et le script du modèle simulé.

```yaml
# yaml-language-server: $schema=./loom.schema.json
version: 1

imports: [outils]
agents_dir: agents/
prompts_dir: prompts/

models:
  - id: SIMULE
    sdk: fake
    model: fake-1
    params:
      script:
        - text: Je calcule le prix TTC.
          tool_calls:
            - name: prix_ttc
              arguments: {montant_ht: 1250, taux_tva: 10}
        - text: Pour 1 250 € HT avec une TVA à 10 %, votre client paiera 1 375 € TTC.

telemetry:
  logging: {level: WARNING}

storage:
  events: {backend: jsonl, path: data}
```

Le modèle simulé joue son script réponse après réponse : à son premier tour il demande l'appel de `prix_ttc`, à son second il donne la réponse finale. Le bloc `telemetry.logging.level: WARNING` supprime les lignes `INFO` vues au chapitre 1.

Remplacez `agents/devis.yaml` :

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

`tools` liste ce que l'agent a le droit d'appeler. `max_iterations` borne le nombre de tours de la boucle ; une fois la borne atteinte, l'agent doit répondre sans outils.

Remplacez `prompts/devis.md` :

```markdown
Tu es l'assistant d'un artisan. Tu réponds en français, en une ou deux phrases.

Pour tout calcul de prix, appelle l'outil `prix_ttc` au lieu de calculer
toi-même, et reprends les montants qu'il renvoie.
```

#### Exécution

```bash
uv run loom validate
```

La partie haute est identique à celle de l'exemple 1.1. Ce qui change :

```text
Modèles    : SIMULE
Agents     : devis
Outils     : prix_ttc
…
  devis : modèle SIMULE, 1 outil(s) Python

1 agent(s) monté(s) sans erreur.
```

(Les lignes de `Profil` et `Paquets` à `Clés d'API` sont omises.) Puis le run :

```bash
uv run loom run devis "Quel prix TTC pour un devis de 1 250 € HT en rénovation (TVA à 10 %) ?"
```

```text
Pour 1 250 € HT avec une TVA à 10 %, votre client paiera 1 375 € TTC.

Statut     : completed · itérations : 2 · tokens : 684/110 · coût : 0.0000 $
Run        : 01a12671-0ff9-77d0-bee3-0151ba688b8a
```

`itérations : 2` : un tour pour demander l'outil, un tour pour répondre. Avec `--stream`, le déroulé s'affiche au fil de l'eau, appels d'outils compris :

```bash
uv run loom run devis "Quel prix TTC pour un devis de 1 250 € HT en rénovation (TVA à 10 %) ?" --stream
```

```text
Je calcule le prix TTC.
· prix_ttc(montant_ht=1250, taux_tva=10)
· prix_ttc : fait
Pour 1 250 € HT avec une TVA à 10 %, votre client paiera 1 375 € TTC.

Statut     : completed · itérations : 2 · tokens : 684/110 · coût : 0.0000 $
Run        : 01a12671-1572-759e-a093-b80868878949
```

Un outil décoré par `@tool` reste une fonction que vous pouvez appeler et tester directement, sans agent :

```bash
uv run python -c "
from outils import prix_ttc
print(prix_ttc(1250, 10))
print(type(prix_ttc).__name__)
print(prix_ttc.spec.description)
"
```

```text
{'montant_ht': 1250, 'tva': 125.0, 'montant_ttc': 1375.0}
FunctionTool
Calcule la TVA et le montant TTC d'un devis. Le taux est en pourcentage.
```

(La première ligne montre `1250` et non `1250.0` : appelée directement, la fonction ne passe pas par la conversion que Loom-IA applique aux arguments d'un appel du modèle, d'où le `1250.0` que le journal montrera plus loin.)

#### À retenir

- **`@tool` suffit.** Le nom de la fonction devient le nom de l'outil, la signature donne le schéma des arguments (exemple suivant), la docstring donne la description que lit le modèle.
- **Soignez la docstring.** Elle indique au modèle quand appeler l'outil. Une docstring absente est refusée dès l'import : `Outil 'sans_doc' : description manquante (docstring vide)`.
- **`imports: [outils]` charge le module `outils.py`** voisin de `loom.yaml`, et enregistre tous ses outils sous leur nom. `- python: prix_ttc` dans l'agent s'y réfère. Un nom inconnu est refusé par `loom validate` (voir l'exemple 3.1).
- **Le modèle simulé n'écoute pas.** Posez-lui n'importe quelle question : il suivra son script. Ce qui est testé ici, c'est la tuyauterie.
- **Piège : un script trop court.** Le script doit avoir une réponse par tour de boucle. Si vous supprimez la seconde réponse du script, le run échoue au deuxième tour :

```text
Script du modèle 'SIMULE' épuisé : réponse n°2 demandée, 1 prévue(s) avec ces outils

Statut     : failed · itérations : 1 · tokens : 239/68 · coût : 0.0000 $
Run        : 01a12671-e1b2-7390-916a-e73cba9d8d48
Erreur     : Script du modèle 'SIMULE' épuisé : réponse n°2 demandée, 1 prévue(s) avec ces outils (model.invalid_request)
```

(Cette sortie, comme les suivantes de ce type, comporte aussi une ligne de log `ERROR` sur la sortie d'erreur, omise ici.)

### Exemple 2.2 : la signature écrit le schéma

#### Pourquoi

Pour que le modèle appelle bien l'outil, il doit savoir quels arguments fournir, de quel type, lesquels sont facultatifs et quelles valeurs sont permises. Écrire ce contrat à la main, en JSON Schema, est fastidieux et se désynchronise du code. Loom-IA le tire de la signature Python.

#### Objectif

Voir quel schéma sort d'une signature, et savoir décrire un argument, limiter ses valeurs possibles et le rendre facultatif.

#### Mise en place

Créez `essai_schema.py` (un fichier jetable, hors de l'agent) :

```python
import json
from typing import Annotated, Literal

from pydantic import Field

from loom_ia.tools import tool


@tool
def preparer_relance(
    client: Annotated[str, Field(description="Nom du client, tel qu'il figure sur le devis")],
    ton: Literal["cordial", "ferme"] = "cordial",
    jours_depuis_envoi: int | None = None,
) -> str:
    """Prépare le texte d'une relance de devis."""
    return f"Relance pour {client} ({ton})"


print(json.dumps(preparer_relance.spec.input_schema, ensure_ascii=False, indent=2))
```

#### Exécution

```bash
uv run python essai_schema.py
```

```json
{
  "additionalProperties": false,
  "properties": {
    "client": {
      "description": "Nom du client, tel qu'il figure sur le devis",
      "title": "Client",
      "type": "string"
    },
    "ton": {
      "default": "cordial",
      "enum": [
        "cordial",
        "ferme"
      ],
      "title": "Ton",
      "type": "string"
    },
    "jours_depuis_envoi": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Jours Depuis Envoi"
    }
  },
  "required": [
    "client"
  ],
  "type": "object"
}
```

#### À retenir

- **Pas de valeur par défaut = argument obligatoire** (`required`). Avec une valeur par défaut, l'argument est facultatif et le modèle voit la valeur.
- **`Literal[...]` donne une liste fermée** (`enum`) : c'est le moyen le plus fiable d'empêcher un ton ou un statut inventé.
- **`Annotated[..., Field(description=...)]` documente un argument.** Utile quand son nom ne dit pas tout.
- **`additionalProperties: false`** : un argument que l'outil ne déclare pas est refusé.
- **Seuls les paramètres nommés sont pris en charge.** Une signature avec `*args` est refusée à la déclaration : `Outil 'etoile' : paramètre 'args' non pris en charge (seuls les paramètres nommés le sont)`.
- **Un paramètre annoté `ToolContext` n'apparaît pas dans le schéma.** Il reçoit le contexte de l'appel (client, session, run, clé d'idempotence). Il sert surtout aux outils qui agissent sur le monde : voir le niveau 3.
- **L'outil est un objet `FunctionTool`**, accessible par `.spec` (nom, description, schéma, et les déclarations que vous découvrirez au chapitre 9 : effets de bord, validation humaine, délai).

### Exemple 2.3 : retourner un résultat, signaler une erreur

#### Pourquoi

Le tour de l'artisan : « Où en est le devis de Mme Martin, et combien en 4 fois ? ». L'agent doit retrouver le devis (un résultat structuré), puis diviser le montant. Mais en vrai, un modèle se trompe : il invente un numéro de devis, divise par zéro, envoie une chaîne là où un nombre est attendu. Le comportement de l'agent face à ces erreurs fait la différence entre un agent qui rebondit et un agent qui plante.

#### Objectif

Connaître les types de retour d'un outil, distinguer les trois façons dont un appel peut échouer, et vérifier dans le journal que le modèle voit chaque erreur et peut se corriger.

#### Mise en place

Remplacez `outils.py` par la version suivante, qui garde `prix_ttc` et ajoute deux outils :

```python
from pydantic import BaseModel

from loom_ia.core.ports import ToolError
from loom_ia.tools import tool


@tool
def prix_ttc(montant_ht: float, taux_tva: float = 20.0) -> dict[str, float]:
    """Calcule la TVA et le montant TTC d'un devis. Le taux est en pourcentage."""
    tva = round(montant_ht * taux_tva / 100, 2)
    return {"montant_ht": montant_ht, "tva": tva, "montant_ttc": round(montant_ht + tva, 2)}


class Devis(BaseModel):
    numero: str
    client: str
    objet: str
    montant_ttc: float
    envoye_le: str


DEVIS = {
    "D-2026-042": Devis(
        numero="D-2026-042",
        client="Mme Martin",
        objet="Remplacement du chauffe-eau",
        montant_ttc=1840.0,
        envoye_le="2026-09-02",
    ),
}


@tool
async def chercher_devis(numero: str) -> Devis:
    """Retrouve un devis à partir de son numéro, par exemple D-2026-042."""
    devis = DEVIS.get(numero)
    if devis is None:
        raise ToolError(f"Devis {numero} introuvable. Numéros connus : {', '.join(DEVIS)}.")
    return devis


@tool
def montant_echeance(montant: float, nb_echeances: int) -> float:
    """Calcule le montant de chaque échéance d'un paiement en plusieurs fois."""
    return round(montant / nb_echeances, 2)
```

Créez un second agent, `agents/atelier.yaml`, pour ne pas toucher à `devis` :

```yaml
name: atelier
description: Agent d'essai pour les outils.

main:
  model: SIMULE_ATELIER
  system: Tu es l'assistant de la Plomberie Dupont. Tu réponds en français.

tools:
  - python: chercher_devis
  - python: montant_echeance
```

Ici le prompt est donné en ligne par `system:` plutôt que dans un fichier par `system_file:`. Les deux existent.

Ajoutez ce modèle dans `loom.yaml`, à la suite de `SIMULE`, avant la section `telemetry:` :

```yaml
  - id: SIMULE_ATELIER
    sdk: fake
    model: fake-1
    params:
      script:
        - tool_calls:
            - name: chercher_devis
              arguments: {numero: D-2026-24}
        - tool_calls:
            - name: chercher_devis
              arguments: {numero: D-2026-042}
        - tool_calls:
            - name: montant_echeance
              arguments: {montant: 1840, nb_echeances: 0}
        - tool_calls:
            - name: montant_echeance
              arguments: {montant: 1840 euros, nb_echeances: 4}
        - tool_calls:
            - name: montant_echeance
              arguments: {montant: 1840, nb_echeances: 4}
        - text: Le devis D-2026-042 de Mme Martin s'élève à 1 840 €, soit 4 échéances de 460 €.
```

Le script met en scène un modèle maladroit : numéro de devis faux, division par zéro, montant écrit « 1840 euros », puis les bons appels.

#### Exécution

```bash
uv run loom run atelier "Où en est le devis de Mme Martin, et combien en 4 fois ?"
```

Sur la sortie d'erreur, Loom-IA signale l'exception de l'outil avec sa trace complète (abrégée ici) :

```text
2026-10-10 17:31:49 WARNING  loom_ia.engine.executor — Échec de l'outil montant_echeance [run_id=01a12671-1e2c-7560-bebf-0a716e8e271a]
Traceback (most recent call last):
  …
  File "/home/denis/mon-agent/outils.py", line 45, in montant_echeance
    return round(montant / nb_echeances, 2)
                 ~~~~~~~~^~~~~~~~~~~~~~
ZeroDivisionError: division by zero
```

puis, sur la sortie standard et la sortie d'erreur :

```text
Le devis D-2026-042 de Mme Martin s'élève à 1 840 €, soit 4 échéances de 460 €.

Statut     : completed · itérations : 6 · tokens : 4389/276 · coût : 0.0000 $
Run        : 01a12671-1e2c-7560-bebf-0a716e8e271a
```

Le run a abouti malgré trois appels fautifs. Voyez comment, avec `loom inspect` (détaillé au chapitre 3) :

```bash
uv run loom inspect 01a12671-20bb-7565-af16-aafae7c81422
```

```text
Run        : 01a12671-20bb-7565-af16-aafae7c81422 (agent atelier, client default)
Session    : 01a12671-20bb-7565-af16-aafae7c81422
Statut     : completed, 6 itération(s)
Usage      : 4389 → 276 tokens, 0.000000 $, 43 ms de pilotage

run atelier — completed, 74 ms, 0.000000 $
  étape 1
    modèle fake-1 (main) — 1 ms, 367 → 44 tokens, 0.000000 $
      · appelle   : chercher_devis({"numero": "D-2026-24"})
  étape 2
    outil chercher_devis — 2 ms (erreur)
      · arguments : {"numero": "D-2026-24"}
      · résultat  : (erreur) Devis D-2026-24 introuvable. Numéros connus : D-2026-042.
  étape 3
    modèle fake-1 (main) — 1 ms, 497 → 44 tokens, 0.000000 $
      · appelle   : chercher_devis({"numero": "D-2026-042"})
  étape 4
    outil chercher_devis — 2 ms
      · arguments : {"numero": "D-2026-042"}
      · résultat  : {"numero": "D-2026-042", "client": "Mme Martin", "objet": "Remplacement du chauffe-eau", "montant…
  étape 5
    modèle fake-1 (main) — 1 ms, 676 → 47 tokens, 0.000000 $
      · appelle   : montant_echeance({"montant": 1840, "nb_echeances": 0})
  étape 6
    outil montant_echeance — 4 ms (erreur)
      · arguments : {"montant": 1840, "nb_echeances": 0}
      · résultat  : (erreur) Erreur de l'outil montant_echeance : ZeroDivisionError: division by zero
  étape 7
    modèle fake-1 (main) — 1 ms, 813 → 49 tokens, 0.000000 $
      · appelle   : montant_echeance({"montant": "1840 euros", "nb_echeances": 4})
  étape 8
    appel montant_echeance — refusé avant exécution
      · résultat  : (erreur) Arguments non conformes au schéma de l'outil : - montant : '1840 euros' is not of type '…
  étape 9
    modèle fake-1 (main) — 1 ms, 958 → 47 tokens, 0.000000 $
      · appelle   : montant_echeance({"montant": 1840, "nb_echeances": 4})
  étape 10
    outil montant_echeance — 1 ms
      · arguments : {"montant": 1840, "nb_echeances": 4}
      · résultat  : 460.0
  étape 11
    modèle fake-1 (main) — 1 ms, 1078 → 45 tokens, 0.000000 $
      · répond    : Le devis D-2026-042 de Mme Martin s'élève à 1 840 €, soit 4 échéances de 460 €.

Réponse finale :
  Le devis D-2026-042 de Mme Martin s'élève à 1 840 €, soit 4 échéances de 460 €.
Bilan      : 6 appel(s) de modèle, 5 appel(s) d'outil dont 1 refusé(s) avant exécution
```

#### À retenir

Ce que peut renvoyer un outil :

| Valeur renvoyée | Ce que reçoit le modèle |
|---|---|
| `str` | un bloc de texte |
| `dict`, `list`, nombre, modèle Pydantic | un bloc JSON (et la valeur recopiée dans `data`) |
| `None` | un résultat vide |
| `Image` (`from loom_ia.tools import Image`) | l'image est rangée comme fichier du run (JPEG, PNG, GIF ou WebP) ; le modèle en reçoit la référence |

Les trois façons d'échouer, vues ci-dessus :

| Cause | Étape | Ce que voit le modèle | Journal de l'outil |
|---|---|---|---|
| `raise ToolError("…")` | 2 | Votre message, tel quel | pas de trace sur la sortie d'erreur |
| Exception quelconque | 6 | `Erreur de l'outil <nom> : <Type> : <message>` | trace complète en `WARNING` |
| Arguments qui ne respectent pas le schéma | 8 | `Arguments non conformes au schéma de l'outil : …` | « refusé avant exécution », l'outil n'est pas appelé |

- **`ToolError` est pour les erreurs que le modèle peut exploiter.** « Numéros connus : D-2026-042 » lui permet de corriger son appel au tour suivant. Importez-le de `loom_ia.core.ports`.
- **Le message d'une exception brute part vers le modèle.** N'y mettez ni mot de passe ni donnée d'un autre client. En cas de doute, attrapez l'exception et levez une `ToolError` avec un message maîtrisé.
- **Le schéma est vérifié avant l'appel.** Un montant `"1840 euros"` n'atteint jamais votre fonction : le modèle reçoit le refus et peut reformuler.
- **Une fonction synchrone s'exécute dans un thread ; une fonction `async def` s'exécute dans la boucle d'événements.** Pour de l'accès réseau ou disque lent, préférez `async def`. Un délai dépassé rend la main au moteur mais ne peut pas interrompre un thread.
- **Un outil prend 30 secondes au maximum par défaut.** Le réglage `timeout` du décorateur (`@tool(timeout=20)`) change ce délai.
- **Piège : un outil qui renvoie un énorme texte.** Au-delà de 50 000 caractères, le résultat est déporté dans un fichier et le modèle n'en voit qu'un aperçu. Voir le niveau 3.

### Exemple 2.4 : plusieurs outils dans un même tour, en parallèle

#### Pourquoi

Mme Martin demande : « Peut-on poser le chauffe-eau lundi ? ». Pour répondre, l'agent doit interroger trois sources lentes : le stock du grossiste, l'agenda de l'artisan et le délai de livraison. Si le modèle les appelle l'une après l'autre, il attend trois fois, et la cliente avec lui. Les trois questions sont indépendantes : autant les poser en même temps.

#### Objectif

Faire demander trois outils dans une seule réponse du modèle, mesurer le temps réel du tour, et voir dans quel ordre les résultats arrivent dans le journal, puis dans quel ordre le modèle les reçoit.

#### Mise en place

Ajoutez `import asyncio` en tête de `outils.py`, puis ces trois outils à la fin du fichier. Chacun dort un peu pour simuler un serveur distant : 0,9 s pour le stock, 0,3 s pour l'agenda, 0,6 s pour la livraison.

```python
@tool
async def stock_chauffe_eau(reference: str) -> str:
    """Donne le stock du chauffe-eau chez le grossiste (lent : il interroge son serveur)."""
    await asyncio.sleep(0.9)
    return f"{reference} : 4 en stock"


@tool
async def agenda_artisan(jour: str) -> str:
    """Donne les créneaux libres de l'artisan pour un jour (lent : agenda distant)."""
    await asyncio.sleep(0.3)
    return f"{jour} : libre de 14 h à 17 h"


@tool
async def delai_livraison(reference: str) -> str:
    """Donne le délai de livraison d'une référence chez le grossiste (lent)."""
    await asyncio.sleep(0.6)
    return f"{reference} : livré sous 2 jours"
```

Pour ne pas toucher à `loom.yaml`, cet exemple a sa propre configuration, `parallele.yaml`, et son propre dossier d'agents. Elle active `capture.raw_exchanges`, qui range dans le journal la requête exacte envoyée au modèle : c'est ce qui permet de voir ce que le modèle reçoit.

```bash
mkdir agents_parallele
```

`parallele.yaml` : le script du modèle simulé est une seule réponse qui porte **trois** `tool_calls`, suivie de la réponse finale.

```yaml
version: 1

imports: [outils]
agents_dir: agents_parallele/

models:
  - id: SIMULE_PARALLELE
    sdk: fake
    model: fake-parallele
    params:
      script:
        - text: Je consulte le stock, l'agenda et la livraison en même temps.
          tool_calls:
            - name: stock_chauffe_eau
              arguments: {reference: CE-200}
            - name: agenda_artisan
              arguments: {jour: "2026-10-12"}
            - name: delai_livraison
              arguments: {reference: CE-200}
        - text: Le chauffe-eau CE-200 est en stock, livrable sous 2 jours, et vous pouvez poser le lundi 12 de 14 h à 17 h.

telemetry:
  logging: {level: WARNING}
  capture: {raw_exchanges: true}

storage:
  events: {backend: jsonl, path: data}
```

`agents_parallele/parallele.yaml` :

```yaml
name: parallele
description: Interroge stock, agenda et livraison dans le même tour.

main:
  model: SIMULE_PARALLELE
  system: Tu es l'assistant de la Plomberie Dupont. Tu réponds en français.

tools:
  - python: stock_chauffe_eau
  - python: agenda_artisan
  - python: delai_livraison
```

`parallele.py` mesure la durée du run, relit l'ordre d'arrivée des résultats dans le journal, puis l'ordre dans lequel le modèle les reçoit au tour suivant. (L'API Python est présentée au chapitre 4 ; ici, le script ne sert qu'à lire le journal.)

```python
import asyncio
import json
import time

from loom_ia.access import Loom


async def main() -> None:
    async with Loom.from_config("parallele.yaml") as loom:
        debut = time.perf_counter()
        result = await loom.run("parallele", "Peut-on poser le chauffe-eau lundi ?")
        print(f"{result.status} en {time.perf_counter() - debut:.2f} s")
        events = await loom.events(result.run_id)

        print("\nOrdre d'arrivée des résultats (journal) :")
        for event in events:
            if event.type == "tool.completed":
                print(f"  {event.payload.call_id}  {event.payload.tool_name:<18} {event.payload.latency_ms:>4.0f} ms")

        # La requête du 2e appel au modèle, telle que le journal l'a gardée.
        requetes = [e.payload.request_body for e in events if e.type == "model.exchanged"]
        messages = json.loads(requetes[1])["messages"]
        print("\nOrdre dans la requête suivante, vu par le modèle :")
        for message in messages:
            for bloc in message["blocks"]:
                if bloc["type"] == "tool_result":
                    print(f"  {bloc['call_id']}  {bloc['output']['blocks'][0]['text']}")


asyncio.run(main())
```

#### Exécution

D'abord en ligne de commande, avec `--stream` pour voir le déroulé. `--config` désigne le fichier de configuration à la place de `loom.yaml` :

```bash
uv run loom --config parallele.yaml run parallele "Peut-on poser le chauffe-eau lundi ?" --stream --run-id parallele-1
```

```text
Je consulte le stock, l'agenda et la livraison en même temps.
· stock_chauffe_eau(reference='CE-200')
· agenda_artisan(jour='2026-10-12')
· delai_livraison(reference='CE-200')
· agenda_artisan : fait
· delai_livraison : fait
· stock_chauffe_eau : fait
Le chauffe-eau CE-200 est en stock, livrable sous 2 jours, et vous pouvez poser le lundi 12 de 14 h à 17 h.

Statut     : completed · itérations : 2 · tokens : 1153/202 · coût : 0.0000 $
Run        : parallele-1
```

Les trois appels partent tout de suite, dans l'ordre demandé par le modèle. Les « fait » arrivent dans l'ordre où les outils finissent : l'agenda (0,3 s), la livraison (0,6 s), puis le stock (0,9 s). `loom inspect` donne les durées :

```bash
uv run loom --config parallele.yaml inspect parallele-1
```

```text
Run        : parallele-1 (agent parallele, client default)
Session    : parallele-1
Statut     : completed, 2 itération(s)
Usage      : 1153 → 202 tokens, 0.000000 $, 920 ms de pilotage

run parallele — completed, 931 ms, 0.000000 $
  étape 1
    modèle fake-parallele (main) — 3 ms, 384 → 150 tokens, 0.000000 $
      · répond    : Je consulte le stock, l'agenda et la livraison en même temps.
      · appelle   : stock_chauffe_eau({"reference": "CE-200"})
      · appelle   : agenda_artisan({"jour": "2026-10-12"})
      · appelle   : delai_livraison({"reference": "CE-200"})
  étape 2
    outil stock_chauffe_eau — 906 ms
      · arguments : {"reference": "CE-200"}
      · résultat  : CE-200 : 4 en stock
    outil agenda_artisan — 304 ms
      · arguments : {"jour": "2026-10-12"}
      · résultat  : 2026-10-12 : libre de 14 h à 17 h
    outil delai_livraison — 604 ms
      · arguments : {"reference": "CE-200"}
      · résultat  : CE-200 : livré sous 2 jours
  étape 3
    modèle fake-parallele (main) — 3 ms, 769 → 52 tokens, 0.000000 $
      · répond    : Le chauffe-eau CE-200 est en stock, livrable sous 2 jours, et vous pouvez poser le lundi 12 de 14…

Réponse finale :
  Le chauffe-eau CE-200 est en stock, livrable sous 2 jours, et vous pouvez poser le lundi 12 de 14 h à 17 h.
Bilan      : 2 appel(s) de modèle, 3 appel(s) d'outil
```

Les trois outils forment une seule étape (étape 2), qui dure 906 ms et non 1,8 s : le tour prend le temps du plus lent, pas la somme. Reste la question de l'ordre, que le script tranche :

```bash
uv run python parallele.py
```

```text
completed en 0.94 s

Ordre d'arrivée des résultats (journal) :
  fake_0_1  agenda_artisan      301 ms
  fake_0_2  delai_livraison     601 ms
  fake_0_0  stock_chauffe_eau   901 ms

Ordre dans la requête suivante, vu par le modèle :
  fake_0_0  CE-200 : 4 en stock
  fake_0_1  2026-10-12 : libre de 14 h à 17 h
  fake_0_2  CE-200 : livré sous 2 jours
```

Le run entier dure 0,94 s, soit le temps du stock (0,9 s) plus la marge du moteur, contre 1,8 s en séquence. Le journal écrit chaque résultat **à son arrivée** (agenda, livraison, stock). Mais la requête que reçoit le modèle au tour suivant les range **dans l'ordre des appels** (stock, agenda, livraison, c'est-à-dire `fake_0_0`, `fake_0_1`, `fake_0_2`). La conversation du modèle ne dépend donc pas de la vitesse des outils.

(Le `call_id` `fake_<tour>_<rang>` est celui du modèle simulé ; un vrai fournisseur donne les siens.)

#### À retenir

- **Un tour, plusieurs appels.** Quand la réponse du modèle contient plusieurs appels d'outils, le moteur les lance tous ensemble. Avec le modèle simulé, il suffit de mettre plusieurs entrées dans les `tool_calls` d'une réponse du script. Avec un vrai modèle, c'est lui qui décide d'en demander plusieurs.
- **La durée du tour est celle du plus lent.** Ici 0,9 s pour trois outils de 0,9, 0,3 et 0,6 s. Les fonctions synchrones y gagnent aussi, car chacune tourne dans son propre thread : un essai avec deux outils synchrones de 1 s chacun a pris 1,7 s pour tout le run, démarrage du process compris, alors que les deux outils seuls auraient demandé 2 s l'un après l'autre.
- **Le journal est écrit dans l'ordre d'arrivée, le modèle lit dans l'ordre des appels.** La requête envoyée au modèle ne dépend donc pas de la lenteur de chaque outil.
- **Une erreur n'arrête pas les autres.** Dans l'essai à deux outils synchrones, un troisième appel levait une `ToolError` : il est revenu « fait (erreur) » en premier, et les deux autres sont allés au bout.
- **Piège : ne parallélisez que des appels indépendants.** Le modèle ne voit aucun résultat avant la fin du tour. Si le deuxième appel a besoin du résultat du premier (un numéro de devis à chercher avant de le lire), il doit venir au tour suivant, une fois le premier résultat lu.

---

## 3. Valider et inspecter

Un agent se met au point avec les yeux : on valide la configuration avant de lancer, on regarde le déroulé après. Loom-IA fournit pour cela quatre outils : `loom validate`, `loom schema`, `loom inspect` et `loom report`. Le journal brut, lui, se lit avec n'importe quel programme.

### Exemple 3.1 : comprendre ce que `loom validate` refuse

#### Pourquoi

Une faute de frappe dans un fichier YAML (un modèle mal orthographié, un outil renommé, une clé qui n'existe pas) ne doit pas se découvrir à l'instant où l'artisan pose sa question. Loom-IA valide strictement la configuration au chargement, et dit où est le problème.

#### Objectif

Lire un rapport de validation réussi, et reconnaître les trois erreurs les plus fréquentes : modèle inconnu, outil introuvable, clé inconnue.

#### Mise en place

Rien à créer. Vous allez casser puis réparer `agents/devis.yaml` à la main (les commandes `sed` ci-dessous le font à votre place ; un éditeur fait aussi bien l'affaire).

#### Exécution

Un rapport réussi, avec les agents des exemples précédents :

```bash
uv run loom validate
```

```text
Config     : loom.yaml
Profil     : aucun (ni --profile, ni LOOM_PROFILE, ni 'profile:') ; surcharges : aucune
Modèles    : SIMULE, SIMULE_ATELIER
Agents     : atelier, devis
Outils     : chercher_devis, montant_echeance, prix_ttc
Paquets    : forge (loom-ia 2.0.0) (groupe loom_ia.tools)
Journal    : jsonl (/home/denis/mon-agent/data)
Artefacts  : local (/home/denis/mon-agent/data/.artifacts)
Idempotence: journal
File       : asyncio
Bus        : memory (les nouvelles ne sortent pas de ce process)
Chiffrement: aucun (contenus en clair au repos)
Rétention  : aucune (rien ne s'efface)
Clés d'API : aucune (API REST ouverte)
  atelier : modèle SIMULE_ATELIER, 2 outil(s) Python
  devis : modèle SIMULE, 1 outil(s) Python

2 agent(s) monté(s) sans erreur.
```

**Erreur 1 : un modèle inconnu.** Dans `agents/devis.yaml`, écrivez `model: SIMULÉ` (avec un accent) au lieu de `SIMULE` :

```bash
sed -i 's/model: SIMULE$/model: SIMULÉ/' agents/devis.yaml
uv run loom validate; echo "code de retour : $?"
```

```text
Configuration : /home/denis/mon-agent/loom.yaml: (racine) — Agent 'devis' : modèle 'SIMULÉ' non déclaré (modèles connus : SIMULE, SIMULE_ATELIER)
code de retour : 2
```

**Erreur 2 : un outil introuvable.** Rétablissez le modèle, puis écrivez `python: prix_ht` au lieu de `prix_ttc` :

```bash
sed -i 's/model: SIMULÉ$/model: SIMULE/' agents/devis.yaml
sed -i 's/python: prix_ttc/python: prix_ht/' agents/devis.yaml
uv run loom validate
```

```text
Configuration : Référence 'prix_ht' introuvable : aucun objet de ce nom (enregistrés : chercher_devis, montant_echeance, prix_ttc). Utiliser 'imports' ou un chemin 'module:attr'
```

(Avant ce message, la sortie standard a déjà listé la partie haute du rapport et l'agent `atelier`, qui, lui, se monte : l'erreur arrive en montant `devis`.)

**Erreur 3 : une clé inconnue.** Rétablissez l'outil, puis écrivez `max_iteration: 5` au lieu de `max_iterations: 5` :

```bash
sed -i 's/python: prix_ht/python: prix_ttc/' agents/devis.yaml
sed -i 's/max_iterations: 5/max_iteration: 5/' agents/devis.yaml
uv run loom validate
```

```text
Configuration : /home/denis/mon-agent/agents/devis.yaml: max_iteration — Extra inputs are not permitted
```

Remettez le fichier en état avant de continuer :

```bash
sed -i 's/max_iteration: 5/max_iterations: 5/' agents/devis.yaml
```

#### À retenir

- **La validation est stricte.** Une clé inconnue est une erreur, pas un avertissement ignoré : une faute de frappe ne passe pas inaperçue.
- **Le message dit le fichier, la clé et la raison.** Les erreurs de référence dressent aussi la liste de ce qui existe (« enregistrés : … », « modèles connus : … »).
- **Code de retour 2 pour toute erreur de configuration.** Branchez `uv run loom validate` dans votre CI.
- **`loom validate` monte les agents un par un et s'arrête à la première erreur.** Il ne fait aucun appel de modèle.
- **Piège : la clé d'API est exigée par `validate`.** Dès qu'un agent utilise un modèle avec `api_key_env`, la variable doit exister dans votre environnement pour que `validate` passe, même si aucun appel n'est fait (voir le chapitre 5).

### Exemple 3.2 : la complétion dans l'éditeur

#### Pourquoi

Rédiger des fichiers YAML de cent lignes sans connaître les clés par cœur est pénible. Autant que l'éditeur propose les clés, signale les fautes de frappe en direct et affiche la documentation de chaque champ.

#### Objectif

Générer le schéma JSON de la configuration, le relier à `loom.yaml`, et faire de même pour les fichiers d'agents.

#### Mise en place

Générez le schéma du fichier racine :

```bash
uv run loom schema > loom.schema.json
```

La première ligne de `loom.yaml`, déjà posée à l'exemple 2.1, relie le fichier à ce schéma :

```yaml
# yaml-language-server: $schema=./loom.schema.json
```

Cette ligne est reconnue par les éditeurs qui embarquent le serveur de langage YAML (par exemple VS Code avec l'extension YAML). Le schéma racine décrit `loom.yaml`, pas les fichiers de `agents/` : `loom schema` ne sait produire que le premier. La fonction Python qui produit le second existe pourtant dans le paquet. Créez le schéma d'agent ainsi :

```bash
uv run python -c "
import json
from pathlib import Path

from loom_ia.config import agent_json_schema

Path('agent.schema.json').write_text(json.dumps(agent_json_schema(), ensure_ascii=False, indent=2))
"
```

Puis ajoutez en première ligne de `agents/devis.yaml` (le chemin est relatif au fichier d'agent, d'où le `../`) :

```yaml
# yaml-language-server: $schema=../agent.schema.json
```

#### Exécution

```bash
ls -l loom.schema.json agent.schema.json
head -3 agents/devis.yaml
uv run loom validate | tail -4
```

```text
-rw-r--r-- 1 root root 35006 Oct 10 17:31 agent.schema.json
-rw-r--r-- 1 root root 90148 Oct 10 17:31 loom.schema.json
# yaml-language-server: $schema=../agent.schema.json
name: devis
description: Répond aux questions de prix sur les devis.
  atelier : modèle SIMULE_ATELIER, 2 outil(s) Python
  devis : modèle SIMULE, 1 outil(s) Python

2 agent(s) monté(s) sans erreur.
```

La ligne de commentaire ne gêne pas la validation. Le comportement de la complétion dans l'éditeur n'a pas pu être vérifié ici (pas d'éditeur dans l'environnement de rédaction), seule la génération et l'absence d'effet de bord l'ont été.

#### À retenir

- **Le schéma ne sert qu'à l'éditeur.** Loom-IA ne le lit jamais : il valide avec ses propres modèles Pydantic. Un schéma périmé ne fausse donc jamais la validation.
- **Régénérez-le quand vous changez de version de `loom-ia`.**
- **Piège : les fichiers d'agents.** La documentation montre seulement `loom schema`, qui ne couvre que le fichier racine. Sans le schéma d'agent ci-dessus, la complétion ne marche pas dans `agents/*.yaml`.
- **Ces deux fichiers `.schema.json` se versionnent ou se régénèrent**, comme vous préférez. Ils ne contiennent aucun secret.

### Exemple 3.3 : relire un run avec `loom inspect` et `loom report`

#### Pourquoi

Le lendemain, Mme Martin se plaint : « Votre assistant m'a donné un autre prix hier. » L'artisan doit pouvoir retrouver ce que l'agent a fait, pas à pas : quel prompt, quel outil, quels arguments, quel résultat, combien de temps, combien de tokens.

#### Objectif

Relire un run terminé de trois manières : en texte lisible, en entier, en JSON ; puis lire sa consommation.

#### Mise en place

Rien à créer : on utilise le run de l'exemple 2.1 (`01a12671-12b8-7763-8510-5fee43617495` ici ; remplacez-le par l'identifiant de l'un de vos runs de l'agent `devis`, affiché par `loom run` sur la ligne `Run`).

#### Exécution

```bash
uv run loom inspect 01a12671-12b8-7763-8510-5fee43617495
```

```text
Run        : 01a12671-12b8-7763-8510-5fee43617495 (agent devis, client default)
Session    : 01a12671-12b8-7763-8510-5fee43617495
Statut     : completed, 2 itération(s)
Usage      : 684 → 110 tokens, 0.000000 $, 10 ms de pilotage

run devis — completed, 20 ms, 0.000000 $
  étape 1
    modèle fake-1 (main) — 1 ms, 259 → 68 tokens, 0.000000 $
      · répond    : Je calcule le prix TTC.
      · appelle   : prix_ttc({"montant_ht": 1250, "taux_tva": 10})
  étape 2
    outil prix_ttc — 2 ms
      · arguments : {"montant_ht": 1250, "taux_tva": 10}
      · résultat  : {"montant_ht": 1250.0, "tva": 125.0, "montant_ttc": 1375.0}
  étape 3
    modèle fake-1 (main) — 1 ms, 425 → 42 tokens, 0.000000 $
      · répond    : Pour 1 250 € HT avec une TVA à 10 %, votre client paiera 1 375 € TTC.

Réponse finale :
  Pour 1 250 € HT avec une TVA à 10 %, votre client paiera 1 375 € TTC.
Bilan      : 2 appel(s) de modèle, 1 appel(s) d'outil
```

Les trois étapes sont celles de la boucle : le modèle demande l'outil (étape 1), l'outil s'exécute (étape 2), le modèle répond (étape 3). Les nombres `259 → 68` sont les tokens en entrée et en sortie de l'appel.

Les arguments et résultats longs sont coupés dans l'affichage (voir « montant… » à l'exemple 2.3). `--full` les montre en entier :

```bash
uv run loom inspect 01a12671-20bb-7565-af16-aafae7c81422 --full | grep -A3 "refusé avant"
```

```text
    appel montant_echeance — refusé avant exécution
      · résultat  : (erreur) Arguments non conformes au schéma de l'outil :
                    - montant : '1840 euros' is not of type 'number'
  étape 9
```

(C'est le run de l'exemple 2.3.) `--json` sort la trace complète, au format que sert aussi l'API REST (`GET /v1/traces/{run_id}`) : un arbre de spans, qui sert de base aux outils de suivi. Voici ses premières lignes :

```bash
uv run loom inspect 01a12671-12b8-7763-8510-5fee43617495 --json | head -22
```

```text
{
  "run_id": "01a12671-12b8-7763-8510-5fee43617495",
  "session_id": "01a12671-12b8-7763-8510-5fee43617495",
  "tenant_id": "default",
  "trace_id": "01a12671-12b8-7763-8510-5fee43617495",
  "agent": "devis",
  "status": "completed",
  "finished": true,
  "iterations": 2,
  "usage": {
    "input_tokens": 684,
    "output_tokens": 110,
    "cache_read_tokens": 0,
    "cache_write_tokens": 0,
    "reasoning_tokens": 0
  },
  "cost_usd": 0.0,
  "active_ms": 9.735958999954164,
  "output": "Pour 1 250 € HT avec une TVA à 10 %, votre client paiera 1 375 € TTC.",
  "error_type": null,
  "content": true,
  "spans": [
```

Pour la consommation, par rôle et par modèle :

```bash
uv run loom report 01a12671-12b8-7763-8510-5fee43617495
```

```text
Consommation — run 01a12671-12b8-7763-8510-5fee43617495
  Total    2 appels ·    684/110 tokens · 0,00000 $
  Par rôle :
    main     2 appels ·    684/110 tokens · 0,00000 $
  Par modèle :
    fake-1   2 appels ·    684/110 tokens · 0,00000 $
```

Le coût est nul parce que le modèle simulé n'a pas de tarif ; l'exemple 5.1 en met un.

#### À retenir

- **`loom inspect <run_id>`** : l'arbre du run, étape par étape. C'est le premier réflexe quand un résultat surprend.
- **`--full`** pour les arguments et résultats sans coupe, **`--json`** pour les outils.
- **`loom report <run_id>`** : tokens et coût par rôle et par modèle. Avec `--session <id>`, il couvre toute une conversation ; avec `--periode jour` ou `--periode mois`, toute la consommation du client sur la période.
- **Si le run est dans une session nommée, ajoutez `--session`** pour le retrouver (le niveau 2 y revient).
- **Piège : un identifiant erroné.** `uv run loom inspect 00000000` répond simplement `Run 00000000 introuvable`. Copiez l'identifiant complet.

### Exemple 3.4 : lire le journal JSONL à la main

#### Pourquoi

Un jour, `loom inspect` ne suffira pas : un script de contrôle, un export pour le comptable, une enquête précise. Le journal est un fichier texte, vous pouvez le lire sans Loom-IA. Autant savoir ce qu'il contient.

#### Objectif

Lire un fichier de journal en Python, reconnaître la suite des événements d'un run et comprendre la forme d'un événement.

#### Mise en place

Créez `lire_journal.py` :

```python
import json
import sys
from pathlib import Path

for ligne in Path(sys.argv[1]).read_text().splitlines():
    evenement = json.loads(ligne)
    charge = evenement["payload"]
    detail = ""
    if evenement["type"] == "tool.called":
        detail = f"{charge['tool_name']} {charge['arguments']}"
    elif evenement["type"] == "tool.completed":
        detail = str(charge["output"]["data"])
    print(f"{evenement['seq']:>3}  {evenement['type']:<18} {evenement['status']:<3} {detail}")
```

#### Exécution

```bash
uv run python lire_journal.py data/default/01a12671-12b8-7763-8510-5fee43617495.jsonl
```

```text
  1  run.started        ok  
  2  message.user       ok  
  3  run.claimed        ok  
  4  step.started       ok  
  5  model.responded    ok  
  6  step.completed     ok  
  7  run.transitioned   ok  
  8  step.started       ok  
  9  tool.called        ok  prix_ttc {'montant_ht': 1250, 'taux_tva': 10}
 10  tool.completed     ok  {'montant_ht': 1250.0, 'tva': 125.0, 'montant_ttc': 1375.0}
 11  step.completed     ok  
 12  run.transitioned   ok  
 13  step.started       ok  
 14  model.responded    ok  
 15  step.completed     ok  
 16  run.transitioned   ok  
 17  run.completed      ok  
```

Chaque ligne du fichier est un événement. Voici le neuvième (`tool.called`), mis en forme :

```json
{
  "event_id": "01a12671-12cd-773f-95d5-7211b8f647d0",
  "ts": "2026-10-10T15:31:47.021235Z",
  "schema_version": 1,
  "tenant_id": "default",
  "session_id": "01a12671-12b8-7763-8510-5fee43617495",
  "run_id": "01a12671-12b8-7763-8510-5fee43617495",
  "root_run_id": "01a12671-12b8-7763-8510-5fee43617495",
  "span_id": "01a12671-12cc-7025-8eda-f378ab2d912e",
  "parent_span_id": "01a12671-12cc-7025-8eda-f3760b4271ee",
  "type": "tool.called",
  "category": "tool",
  "status": "ok",
  "agent": "devis",
  "role": null,
  "facets": {
    "tool_name": "prix_ttc",
    "tool_kind": "python"
  },
  "payload": {
    "type": "tool.called",
    "call_id": "fake_0_0",
    "tool_name": "prix_ttc",
    "tool_kind": "python",
    "arguments": {
      "montant_ht": 1250,
      "taux_tva": 10
    },
    "refs": [],
    "resumed": false,
    "child_run_id": null
  },
  "seq": 9
}
```

#### À retenir

- **Une ligne = un événement = un fait.** L'enveloppe (identifiant, horodatage, client, session, run, span, type) est commune ; `payload` porte les données propres au type.
- **`seq` est la position dans la session.** Les événements sont ordonnés et immuables : on ajoute, on ne modifie jamais.
- **`type` suit la forme `<catégorie>.<action>`** : `run.started`, `model.responded`, `tool.called`, `tool.completed`, `run.completed`… Le schéma est versionné (`schema_version`) ; c'est le contrat sur lequel une interface de suivi peut s'appuyer.
- **Tout se déduit du journal** : l'état du run, l'historique de la conversation, les coûts, l'arbre de `loom inspect`. C'est ce qui permettra de reprendre un run après un plantage (niveau 3) ou de le rejouer (niveau 4).
- **Piège : ne modifiez jamais un fichier de journal à la main.** Pour supprimer des données d'un client, il existe des commandes dédiées (`loom sessions delete`, niveau 3).

---

## 4. Appeler l'agent depuis Python

La ligne de commande sert à mettre au point. Une application (un back-end, un script, une application mobile qui parle à votre serveur) appelle l'agent depuis Python.

### Exemple 4.1 : lancer un run et lire la réponse

#### Pourquoi

L'application de l'artisan reçoit un texte du client et doit répondre. Elle n'a que faire d'un affichage de terminal : elle veut un objet Python avec la réponse.

#### Objectif

Charger la configuration, lancer un run et lire son statut et son texte.

#### Mise en place

Créez `appel.py` à la racine du projet :

```python
import asyncio

from loom_ia.access import Loom


async def main() -> None:
    async with Loom.from_config("loom.yaml") as loom:
        result = await loom.run("devis", "Quel prix TTC pour 1 250 € HT à 10 % ?")
        print(result.status, result.text)


asyncio.run(main())
```

#### Exécution

```bash
uv run python appel.py
```

```text
completed Pour 1 250 € HT avec une TVA à 10 %, votre client paiera 1 375 € TTC.
```

#### À retenir

- **Loom-IA est asynchrone.** `Loom.from_config(chemin)` charge la configuration ; `await loom.run(agent, message)` attend la fin du run.
- **`async with` ouvre et ferme l'instance proprement** (connexions MCP, tâches de fond). Sans lui, appelez `await loom.aclose()` vous-même.
- **Une instance sert de nombreux runs.** Chargez-la une fois au démarrage de votre application, pas à chaque requête.
- **Les modules `imports` s'importent depuis le dossier de `loom.yaml`**, quel que soit votre dossier courant. Mais le chemin `"loom.yaml"` que vous passez, lui, est relatif au dossier courant.
- **Le journal est écrit comme avec la ligne de commande**, au même endroit : `loom inspect` relit un run lancé depuis Python.

### Exemple 4.2 : ce que contient `RunResult`, y compris en cas d'échec

#### Pourquoi

Une application sérieuse ne se contente pas du texte : elle veut savoir si le run a réussi, combien il a coûté, s'il attend une validation, s'il a produit des fichiers. Et elle doit gérer le cas où le modèle est en panne, sans exception qui fasse tomber le serveur.

#### Objectif

Connaître chaque champ du résultat et savoir détecter un échec.

#### Mise en place

Créez `appel_complet.py` :

```python
import asyncio

from loom_ia.access import Loom


async def main() -> None:
    async with Loom.from_config("loom.yaml") as loom:
        result = await loom.run("devis", "Quel prix TTC pour 1 250 € HT à 10 % ?")
        print("run_id     :", result.run_id)
        print("statut     :", result.status)
        print("texte      :", result.text)
        print("itérations :", result.iterations)
        print("usage      :", result.usage)
        print("coût       :", result.cost_usd)
        print("data       :", result.data)
        print("artefacts  :", result.artifacts)
        print("en attente :", result.pending_approvals)
        print("erreur     :", result.error_type, result.error)


asyncio.run(main())
```

Pour provoquer un échec sans réseau, ajoutez ce modèle à `loom.yaml` (avant `telemetry:`), dont le script ne contient qu'une panne :

```yaml
  - id: SIMULE_PANNE
    sdk: fake
    model: fake-1
    params:
      script:
        - error: quota_exhausted
```

Créez `agents/devis_panne.yaml` :

```yaml
name: devis_panne
description: Comme devis, avec un modèle qui tombe en panne.

main:
  model: SIMULE_PANNE
  system_file: devis.md

tools:
  - python: prix_ttc
```

Et `appel_echec.py` :

```python
import asyncio

from loom_ia.access import Loom


async def main() -> None:
    async with Loom.from_config("loom.yaml") as loom:
        result = await loom.run("devis_panne", "Quel prix TTC pour 1 250 € HT à 10 % ?")
        print("statut     :", result.status)
        print("texte      :", repr(result.text))
        print("erreur     :", result.error_type)
        print("message    :", result.error)


asyncio.run(main())
```

#### Exécution

```bash
uv run python appel_complet.py
```

```text
run_id     : 01a12670-227a-748c-8be6-8038bcb1d593
statut     : completed
texte      : Pour 1 250 € HT avec une TVA à 10 %, votre client paiera 1 375 € TTC.
itérations : 2
usage      : input_tokens=668 output_tokens=110 cache_read_tokens=0 cache_write_tokens=0 reasoning_tokens=0
coût       : 0.0
data       : None
artefacts  : ()
en attente : ()
erreur     : None None
```

(Les 668 tokens en entrée, contre 684 en ligne de commande, tiennent à la différence de longueur de la question posée.) Puis le cas de la panne :

```bash
uv run python appel_echec.py
```

```text
statut     : failed
texte      : ''
erreur     : model.quota_exhausted
message    : Panne simulée par le script du modèle 'SIMULE_PANNE' (réponse n°1 : quota_exhausted)
```

La même panne en ligne de commande :

```bash
uv run loom run devis_panne "Quel prix TTC pour 1 250 € HT à 10 % ?"; echo "code de retour : $?"
```

```text
2026-10-10 17:30:48 ERROR    loom_ia.engine.loop — Échec de l'appel au modèle SIMULE_PANNE : model.quota_exhausted — Panne simulée par le script du modèle 'SIMULE_PANNE' (réponse n°1 : quota_exhausted) [run_id=01a12670-2f31-71ce-92dc-4a9d40c7ce47 span_id=01a1…]
Panne simulée par le script du modèle 'SIMULE_PANNE' (réponse n°1 : quota_exhausted)

Statut     : failed · itérations : 0 · tokens : 0/0 · coût : 0.0000 $
Run        : 01a12670-2f31-71ce-92dc-4a9d40c7ce47
Erreur     : Panne simulée par le script du modèle 'SIMULE_PANNE' (réponse n°1 : quota_exhausted) (model.quota_exhausted)
code de retour : 1
```

#### À retenir

| Champ de `RunResult` | Contenu |
|---|---|
| `run_id`, `session_id`, `agent` | identité du run |
| `status` | `completed`, `failed`, `cancelled`, `paused`… |
| `text` | texte de la réponse finale (vide en cas d'échec) |
| `iterations` | nombre de tours de la boucle |
| `usage`, `cost_usd` | tokens (entrée, sortie, cache, raisonnement) et coût en dollars |
| `data` | l'objet JSON validé quand l'agent déclare un schéma de sortie (niveau 2) |
| `artifacts` | les fichiers du run : pièces jointes, fichiers produits par les outils |
| `pending_approvals` | les appels qui attendent une validation humaine (niveau 2) ; le statut est alors `paused` |
| `error_type`, `error` | cause d'un échec : `model.quota_exhausted`, `model.auth`, `guard.contract`, `policy.<nom>`… |
| `report`, `verdicts`, `unverified`, `output` | ventilation des coûts, verdicts des juges, indicateur « réponse non vérifiée », message complet |

- **`run()` ne lève pas d'exception quand le run échoue.** Il renvoie un résultat au statut `failed`. Testez toujours `result.status` (ou `result.error_type`) avant de vous servir de `result.text`.
- **En ligne de commande, c'est le code de retour 1.** C'est ce qui permet à un script shell de réagir.
- **Une panne de modèle est classée par type** : `transient` (réseau, 429, 5xx : retentée automatiquement), `quota_exhausted`, `auth`, `invalid_request`… Les nouvelles tentatives, les modèles de secours et les disjoncteurs sont couverts au niveau 3.
- **Piège : lancer le script d'ailleurs.** `Loom.from_config("loom.yaml")` lu depuis un autre dossier échoue. Passez un chemin absolu, par exemple `Path(__file__).parent / "loom.yaml"`.

### Exemple 4.3 : suivre un run en direct avec `stream()`

#### Pourquoi

Un artisan qui attend devant son écran trois secondes sans rien voir croit que l'application est plantée. Afficher la réponse au fil de l'eau, avec un petit « je consulte le devis… » quand un outil part, change la perception.

#### Objectif

Consommer le flux d'un run : les morceaux de texte du modèle et les événements du journal.

#### Mise en place

Créez `suivi.py` :

```python
import asyncio

from loom_ia.access import Loom
from loom_ia.core.events import Event
from loom_ia.core.model import TextDelta


async def main() -> None:
    async with Loom.from_config("loom.yaml") as loom:
        async for item in loom.stream("devis", "Quel prix TTC pour 1 250 € HT à 10 % ?"):
            if isinstance(item, TextDelta):
                print(item.text, end="", flush=True)
            elif isinstance(item, Event) and item.type == "tool.called":
                print(f"\n[appel d'outil : {item.payload.tool_name}]")
        print()


asyncio.run(main())
```

Et `suivi_types.py`, pour voir tout ce que le flux contient :

```python
import asyncio

from loom_ia.access import Loom


async def main() -> None:
    async with Loom.from_config("loom.yaml") as loom:
        async for item in loom.stream("devis", "Quel prix TTC pour 1 250 € HT à 10 % ?"):
            print(f"{type(item).__name__:<16} {item.type}")


asyncio.run(main())
```

#### Exécution

```bash
uv run python suivi.py
```

```text
Je calcule le prix TTC.
[appel d'outil : prix_ttc]
Pour 1 250 € HT avec une TVA à 10 %, votre client paiera 1 375 € TTC.
```

```bash
uv run python suivi_types.py
```

```text
Event            run.started
Event            message.user
Event            run.claimed
Event            step.started
TextDelta        text_delta
TextDelta        text_delta
TextDelta        text_delta
ToolCallStarted  tool_call_started
ToolArgsDelta    tool_args_delta
ToolArgsDelta    tool_args_delta
ToolArgsDelta    tool_args_delta
ToolArgsDelta    tool_args_delta
ToolArgsDelta    tool_args_delta
ToolCallEnded    tool_call_ended
UsageDelta       usage
Stopped          stopped
Event            model.responded
Event            step.completed
Event            run.transitioned
Event            step.started
Event            tool.called
Event            tool.completed
Event            step.completed
Event            run.transitioned
Event            step.started
TextDelta        text_delta
TextDelta        text_delta
…
Event            run.completed
```

(Sortie abrégée au milieu : le second tour du modèle donne neuf `TextDelta`, puis `UsageDelta`, `Stopped`, `model.responded`…)

#### À retenir

- **`stream()` rend deux sortes d'éléments, dans l'ordre d'arrivée** : des morceaux du modèle (`TextDelta` pour le texte, `ReasoningDelta` pour le raisonnement, `ToolCallStarted`, `ToolArgsDelta`, `ToolCallEnded`, `UsageDelta`, `Stopped`) et des `Event`, les événements du journal.
- **`item.type` existe sur tous les éléments.** Sur un `Event`, il porte le type du journal (`tool.called`) et `item.payload` la charge typée.
- **Le flux ne donne pas de `RunResult`.** Une fois le flux fini, relisez le résultat avec `await loom.result(run_id)` ; le `run_id` figure dans chaque événement (`item.run_id`).
- **Abandonner l'itération annule le run.** Si le client se déconnecte, sortez de la boucle : le run s'arrête.
- **Les morceaux ne sont pas dans le journal**, seuls les événements le sont. `model.responded` y contient la réponse complète.

### Exemple 4.4 : les logs de Loom-IA

#### Pourquoi

En production, vous voulez des lignes de log structurées dans le même flux que le reste de l'application. Pendant la mise au point, vous voulez voir chaque appel de modèle et d'outil passer.

#### Objectif

Savoir qui configure les logs (la bibliothèque, ou vous) et activer les logs de Loom-IA depuis Python.

#### Mise en place

Créez `appel_logs.py` :

```python
import asyncio
import logging

from loom_ia.access import Loom
from loom_ia.telemetry import configure_logging


async def main() -> None:
    configure_logging(logging.INFO)
    async with Loom.from_config("loom.yaml") as loom:
        result = await loom.run("devis", "Quel prix TTC pour 1 250 € HT à 10 % ?")
        print(result.status, result.text)


asyncio.run(main())
```

#### Exécution

```bash
uv run python appel_logs.py
```

```text
2026-10-10 17:30:47 INFO     loom_ia.engine.loop — Modèle fake-1 (rôle main) : 0.00 s, 319 tokens, 0.00000 $ [run_id=01a12670-29ee-7183-8e37-8ffb56099149 span_id=01a1… tenant_id=default]
2026-10-10 17:30:47 INFO     loom_ia.engine.loop — Transition ready_for_model → awaiting_tools (agent devis) [run_id=01a12670-29ee-7183-8e37-8ffb56099149 span_id=01a1… tenant_id=default]
2026-10-10 17:30:47 INFO     loom_ia.engine.loop — Outil prix_ttc : 1 ms [run_id=01a12670-29ee-7183-8e37-8ffb56099149 span_id=01a1… tenant_id=default]
2026-10-10 17:30:47 INFO     loom_ia.engine.loop — Transition awaiting_tools → ready_for_model (agent devis) [run_id=01a12670-29ee-7183-8e37-8ffb56099149 span_id=01a1… tenant_id=default]
2026-10-10 17:30:47 INFO     loom_ia.engine.loop — Modèle fake-1 (rôle main) : 0.00 s, 459 tokens, 0.00000 $ [run_id=01a12670-29ee-7183-8e37-8ffb56099149 span_id=01a1… tenant_id=default]
2026-10-10 17:30:47 INFO     loom_ia.engine.loop — Transition ready_for_model → completed (agent devis) [run_id=01a12670-29ee-7183-8e37-8ffb56099149 span_id=01a1… tenant_id=default]
completed Pour 1 250 € HT avec une TVA à 10 %, votre client paiera 1 375 € TTC.
```

(Les `span_id` sont abrégés.)

#### À retenir

- **La bibliothèque n'installe jamais de handler de logs.** Elle écrit dans les loggers standard `loom_ia.*` et laisse votre application décider. Sans configuration de votre part, seuls les avertissements et les erreurs apparaissent (le comportement par défaut de `logging`).
- **`configure_logging(niveau, format="console" | "json", stream=...)`** installe un handler sur le seul logger `loom_ia`. Un second appel remplace le premier. Avec `format="json"`, une ligne = un objet JSON, pratique pour un collecteur.
- **Le contexte (`run_id`, `span_id`, `tenant_id`) est ajouté à chaque ligne** : on retrouve le run dans `loom inspect`.
- **Piège : le bloc `telemetry.logging` de `loom.yaml` ne s'applique qu'à la commande `loom` et au mode service.** Depuis Python, il est ignoré : avec `level: INFO` dans le YAML, `uv run python appel.py` n'affiche aucune ligne de log. Pour la ligne de commande, c'est lui qui règle le niveau et le format.

---

## 5. Brancher un vrai modèle

> **Non exécuté ici.** Aucune clé d'API Anthropic ni serveur Ollama n'était disponible dans l'environnement de rédaction. Les exemples de ce chapitre qui appellent un vrai modèle sont donc écrits d'après la documentation et le code source, et marqués comme tels. Ce qui a pu être exécuté sans clé est indiqué : le calcul du coût avec un modèle simulé tarifé, la validation avec ou sans clé, l'échec d'authentification avec une clé factice (le serveur d'Anthropic a bien été interrogé), et l'échec de connexion à un Ollama absent.
>
> Les noms de modèles (`claude-haiku-5-5`, `qwen3:8b`) sont ceux de la documentation du projet. Vérifiez qu'ils existent chez votre fournisseur avant de vous y fier.

### Exemple 5.1 : le coût d'un run, avec un tarif

#### Pourquoi

Un agent qui coûte plus cher que ce qu'il rapporte n'a pas d'avenir. Pour savoir ce qu'un run coûte, Loom-IA a besoin du tarif du modèle ; et pour poser plus tard un budget en dollars (niveau 3), le tarif est obligatoire. Autant le déclarer dès le départ.

#### Objectif

Déclarer un `pricing`, voir le coût d'un run et le lire dans `loom report`.

#### Mise en place

Ajoutez ce modèle à `loom.yaml` (avant `telemetry:`) :

```yaml
  - id: SIMULE_TARIFE
    sdk: fake
    model: fake-1
    pricing: {input: 0.10, output: 0.50}   # $ par million de tokens
    params:
      script:
        - text: Je calcule le prix TTC.
          tool_calls:
            - name: prix_ttc
              arguments: {montant_ht: 1250, taux_tva: 10}
        - text: Pour 1 250 € HT avec une TVA à 10 %, votre client paiera 1 375 € TTC.
```

Et l'agent `agents/devis_tarife.yaml` :

```yaml
name: devis_tarife
description: Comme devis, avec le modèle SIMULE_TARIFE.

main:
  model: SIMULE_TARIFE
  system_file: devis.md

max_iterations: 5

tools:
  - python: prix_ttc
```

#### Exécution

```bash
uv run loom run devis_tarife "Quel prix TTC pour un devis de 1 250 € HT en rénovation (TVA à 10 %) ?"
```

```text
Pour 1 250 € HT avec une TVA à 10 %, votre client paiera 1 375 € TTC.

Statut     : completed · itérations : 2 · tokens : 684/110 · coût : 0.0001 $
Run        : 01a12671-568c-7341-a322-7d7943744a73
```

```bash
uv run loom report 01a12671-568c-7341-a322-7d7943744a73
```

```text
Consommation — run 01a12671-568c-7341-a322-7d7943744a73
  Total    2 appels ·    684/110 tokens · 0,00012 $
  Par rôle :
    main     2 appels ·    684/110 tokens · 0,00012 $
  Par modèle :
    fake-1   2 appels ·    684/110 tokens · 0,00012 $
```

Le calcul se vérifie à la main : 684 × 0,10 / 1 000 000 + 110 × 0,50 / 1 000 000 = 0,0000684 + 0,000055 = 0,0001234 $.

#### À retenir

- **`pricing` est en dollars par million de tokens**, avec quatre prix : `input`, `output`, `cache_read`, `cache_write`. Les tokens de raisonnement sont déjà compris dans `output` chez les fournisseurs : ils ne sont pas facturés deux fois.
- **Sans `pricing`, le coût est nul.** Loom-IA ne sait pas calculer de coût, et ne pourra donc pas appliquer un budget en dollars. Un modèle sans tarif sous un budget en dollars est refusé en profil `prod` (niveau 2 pour les profils).
- **Le coût s'affiche avec quatre décimales dans `loom run`, cinq dans `loom report`.** Ne vous fiez pas à `0.0000 $` : un run à 0,00005 $ s'affiche ainsi.
- **Les tarifs changent.** Mettez à jour `pricing` quand le fournisseur change les siens.

### Exemple 5.2 : brancher Claude

#### Pourquoi

Le modèle simulé a rempli son rôle de bac à sable. Pour que l'assistant comprenne vraiment « Mme Martin » et choisisse seul ses outils, il faut un vrai modèle.

#### Objectif

Déclarer un modèle Anthropic, faire utiliser ce modèle par un agent à côté de l'agent simulé, et comprendre les messages d'erreur quand la clé manque ou est invalide.

#### Mise en place

Ajoutez ce modèle à `loom.yaml` (avant `telemetry:`) :

```yaml
  - id: HAIKU
    sdk: anthropic
    model: claude-haiku-5-5
    api_key_env: ANTHROPIC_API_KEY   # le nom de la variable, jamais la clé elle-même
    max_tokens: 16000                # la réflexion du modèle compte dedans
    pricing: {input: 0.10, output: 0.50, cache_read: 0.01, cache_write: 0.125}   # $ par million de tokens
```

Créez un **nouvel agent**, `agents/devis_reel.yaml`, plutôt que de modifier `devis` : les exemples simulés continuent de tourner.

```yaml
name: devis_reel
description: Comme devis, avec le modèle HAIKU.

main:
  model: HAIKU
  system_file: devis.md

max_iterations: 5

tools:
  - python: prix_ttc
```

#### Exécution

Sans la clé dans l'environnement, `loom validate` refuse, avant tout appel réseau :

```bash
env -u ANTHROPIC_API_KEY uv run loom validate; echo "code de retour : $?"
```

```text
…
  atelier : modèle SIMULE_ATELIER, 2 outil(s) Python
  devis : modèle SIMULE, 1 outil(s) Python
  devis_local : modèle LOCAL, 1 outil(s) Python
  devis_panne : modèle SIMULE_PANNE, 1 outil(s) Python
Demande refusée : Modèle 'HAIKU' : la variable d'environnement ANTHROPIC_API_KEY est absente ou vide
code de retour : 2
```

(Cette sortie vient du projet complet, après l'exemple 5.3, d'où l'agent `devis_local`. Le principe est le même avec ce chapitre seul.) Avec une variable présente, même factice, la validation passe, puisqu'elle ne fait aucun appel :

```bash
ANTHROPIC_API_KEY=sk-ant-factice uv run loom validate | tail -8
```

```text
  atelier : modèle SIMULE_ATELIER, 2 outil(s) Python
  devis : modèle SIMULE, 1 outil(s) Python
  devis_local : modèle LOCAL, 1 outil(s) Python
  devis_panne : modèle SIMULE_PANNE, 1 outil(s) Python
  devis_reel : modèle HAIKU, 1 outil(s) Python
  devis_tarife : modèle SIMULE_TARIFE, 1 outil(s) Python

6 agent(s) monté(s) sans erreur.
```

Avec cette clé factice, le run atteint bien l'API d'Anthropic, qui la refuse :

```bash
ANTHROPIC_API_KEY=sk-ant-factice uv run loom run devis_reel "Quel prix TTC pour un devis de 1 250 € HT en rénovation (TVA à 10 %) ?"
```

```text
2026-10-10 17:30:58 ERROR    loom_ia.engine.loop — Échec de l'appel au modèle HAIKU : model.auth — Error code: 401 - {'type': 'error', 'error': {'type': 'authentication_error', 'message': 'invalid x-api-key'}, 'request_id': 'req_…'} [run_id=01a12670-51b6-74ce-ac17-03d704d0bcbb span_id=01a1…]
Error code: 401 - {'type': 'error', 'error': {'type': 'authentication_error', 'message': 'invalid x-api-key'}, 'request_id': 'req_…'}

Statut     : failed · itérations : 0 · tokens : 0/0 · coût : 0.0000 $
Run        : 01a12670-51b6-74ce-ac17-03d704d0bcbb
Erreur     : Error code: 401 - {'type': 'error', 'error': {'type': 'authentication_error', 'message': 'invalid x-api-key'}, 'request_id': 'req_…'} (model.auth)
```

**Non exécuté ici :** le run avec une vraie clé. Avec une clé valide, la commande est :

```bash
export ANTHROPIC_API_KEY=...
uv run loom run devis_reel "Quel prix TTC pour un devis de 1 250 € HT en rénovation (TVA à 10 %) ?"
```

Le résultat attendu, d'après le comportement du modèle simulé, a la même forme que celui de l'exemple 2.1 (statut `completed`, l'outil `prix_ttc` appelé, un coût non nul). Le texte exact, le nombre d'itérations et les tokens dépendent du vrai modèle et ne sont pas garantis.

#### À retenir

- **`api_key_env` est le nom d'une variable d'environnement, jamais la clé.** La configuration ne contient aucun secret.
- **`max_tokens`** borne la sortie du modèle. Pour un modèle qui raisonne, la réflexion compte dans cette limite : prévoyez large.
- **Le même `sdk: anthropic` sert les fournisseurs qui exposent l'API d'Anthropic** (MiniMax, par exemple) en ajoutant `base_url`.
- **`params`** (dans `ModelSpec`) est transmis tel quel au fournisseur : `temperature`, par exemple.
- **Évoqués ici, détaillés plus loin** : `cache: {system: true, tools: true, messages: true}` pose des points de cache de prompt (réservé à `sdk: anthropic`, refusé avec `sdk: openai` qui cache seul) ; `capabilities` (`tools`, `vision`, `thinking`, `native_json`, `context_window`…) déclare ce que le modèle sait faire et est contrôlé au chargement ; `timeouts`, `retry`, `circuit_breaker` et les modèles de secours (`fallbacks`) font l'objet du chapitre 15 dans [03-production.md](03-production.md).
- **Piège : `loom validate` exige la variable.** Tant que `ANTHROPIC_API_KEY` n'existe pas dans votre environnement, `loom validate` échoue pour **tout** le projet, même pour les agents simulés (voir l'écart noté ci-dessous). Ce qui peut vous aider : pour une validation sans clé, exportez une valeur factice comme ci-dessus.
- **Piège : ne mettez pas la clé dans le YAML, ni dans un `.env` versionné.** Si vous utilisez un fichier `.env`, passez-le à uv : `uv run --env-file .env loom run …`.

> **Écart documentation / version 2.0.0.** Les sources du projet (2.0.1) ajoutent à `loom validate` une option `--sans-cles`, qui monte les agents sans créer les clients de modèle, donc sans exiger de clé. Elle n'existe pas dans la version 2.0.0 publiée sur PyPI et n'est pas documentée dans le README.

### Exemple 5.3 : un modèle local avec Ollama

#### Pourquoi

Certains textes ne doivent pas quitter l'atelier : un brouillon avec l'adresse d'un client, un carnet de rendez-vous. Un modèle local, servi sur la machine par Ollama, n'envoie rien chez un fournisseur et n'a besoin d'aucune clé. Il est aussi moins coûteux pour des essais en volume.

#### Objectif

Déclarer un modèle OpenAI-compatible local et savoir à quoi ressemble l'échec quand le serveur n'est pas lancé.

#### Mise en place

Ajoutez ce modèle à `loom.yaml` (avant `telemetry:`) :

```yaml
  - id: LOCAL
    sdk: openai
    api: chat
    base_url: http://localhost:11434/v1
    model: qwen3:8b
```

Créez `agents/devis_local.yaml` :

```yaml
name: devis_local
description: Comme devis, avec le modèle LOCAL.

main:
  model: LOCAL
  system_file: devis.md

max_iterations: 5

tools:
  - python: prix_ttc
```

#### Exécution

**Non exécuté ici :** il n'y avait pas de serveur Ollama dans l'environnement de rédaction, donc pas de run réussi. Sur votre machine, après avoir démarré Ollama et téléchargé le modèle, la commande est :

```bash
uv run loom run devis_local "Quel prix TTC pour un devis de 1 250 € HT en rénovation (TVA à 10 %) ?"
```

Ce qui a été exécuté, c'est le cas serveur absent. Il montre les nouvelles tentatives automatiques :

```text
2026-10-10 17:30:59 WARNING  loom_ia.engine.model_call — Tentative 1/3 du modèle LOCAL ratée (transient) : nouvel essai dans 0.8 s
2026-10-10 17:31:00 WARNING  loom_ia.engine.model_call — Tentative 2/3 du modèle LOCAL ratée (transient) : nouvel essai dans 1.6 s
2026-10-10 17:31:02 ERROR    loom_ia.engine.loop — Échec de l'appel au modèle LOCAL : model.transient — Connection error. [run_id=01a12670-592e-7058-8c9e-f10623ac9f6b span_id=01a1…]
Connection error.

Statut     : failed · itérations : 0 · tokens : 0/0 · coût : 0.0000 $
Run        : 01a12670-592e-7058-8c9e-f10623ac9f6b
Erreur     : Connection error. (model.transient)
```

#### À retenir

- **`sdk: openai` couvre OpenAI et tous les fournisseurs compatibles** (Together, vLLM, Ollama…). Le choix `api: chat` ou `api: responses` sélectionne l'API de l'interface ; sans `base_url`, l'adresse officielle d'OpenAI est utilisée. Le champ `api` n'existe que pour `sdk: openai`.
- **Pas de `api_key_env`, pas de clé** : adapté à un serveur local. Un fournisseur distant compatible OpenAI en demande une, donc ajoutez `api_key_env`.
- **Les erreurs passagères sont retentées d'elles-mêmes** : trois tentatives en tout par défaut, avec un délai croissant. Une erreur d'authentification ou une requête invalide ne sont jamais retentées.
- **Un modèle local capable d'appeler des outils est indispensable** pour un agent avec outils. Tous ne le sont pas. Si le vôtre ne l'est pas, déclarez `capabilities: {tools: false}` sur le modèle : Loom-IA refusera alors, dès `loom validate`, de l'attribuer à un agent qui a des outils (message réel : `Agent 'devis_local' : il appelle des outils, mais le modèle 'LOCAL' ne sait pas le faire (capabilities.tools: false)`).
- **Piège : l'adresse.** La documentation du projet donne `http://localhost:11434/v1` (port par défaut d'Ollama, API compatible OpenAI sous `/v1`). Si vous changez de port ou de machine, adaptez `base_url` : la ligne `Connection error.` ci-dessus est ce que vous verrez quand l'adresse ne répond pas.

---

## 6. Tout construire en Python, sans fichier

### Exemple 6.1 : une configuration écrite en code

#### Pourquoi

Parfois, aucun fichier YAML n'est souhaitable : un test unitaire qui monte un agent jetable, une application qui compose la configuration d'après ses propres réglages, un notebook. Les fichiers YAML ne sont qu'une façon d'écrire des modèles Pydantic ; ces modèles s'utilisent directement.

#### Objectif

Construire l'agent `devis` entièrement en Python, avec ses modèles, son outil et son stockage, et le faire tourner.

#### Mise en place

Créez `sans_fichier.py` :

```python
import asyncio
from pathlib import Path

from loom_ia.access import Loom
from loom_ia.agents import AgentSpec, MainRole, PythonTool
from loom_ia.config import LoomConfig
from loom_ia.config.models import EventsStorage, StorageConfig
from loom_ia.core.model import ModelSpec
from outils import prix_ttc

SCRIPT = [
    {
        "text": "Je calcule le prix TTC.",
        "tool_calls": [{"name": "prix_ttc", "arguments": {"montant_ht": 1250, "taux_tva": 10}}],
    },
    {"text": "Pour 1 250 € HT avec une TVA à 10 %, votre client paiera 1 375 € TTC."},
]

config = LoomConfig(
    version=1,
    models=(ModelSpec(id="SIMULE", sdk="fake", model="fake-1", params={"script": SCRIPT}),),
    storage=StorageConfig(events=EventsStorage(backend="jsonl", path=Path("data"))),
    agents=(
        AgentSpec(
            name="devis",
            main=MainRole(model="SIMULE", system="Tu es l'assistant d'un artisan."),
            tools=(PythonTool(python="prix_ttc"),),
        ),
    ),
)


async def main() -> None:
    async with Loom(config) as loom:
        loom.register("prix_ttc", prix_ttc)
        result = await loom.run("devis", "Quel prix TTC pour 1 250 € HT à 10 % ?")
        print(result.status, result.text)
        print("session :", result.session_id)


asyncio.run(main())
```

Trois détails :

- `LoomConfig`, `ModelSpec`, `AgentSpec`, `MainRole` et `PythonTool` sont les modèles que le YAML remplit : mêmes champs, mêmes noms.
- `Loom(config)` remplace `Loom.from_config(chemin)`.
- Comme il n'y a pas de `imports`, c'est `loom.register("prix_ttc", prix_ttc)` qui rend l'outil référençable par son nom.

#### Exécution

```bash
uv run python sans_fichier.py
```

```text
completed Pour 1 250 € HT avec une TVA à 10 %, votre client paiera 1 375 € TTC.
session : 01a12670-6717-7227-bd62-abd568e72cdc
```

Le run est dans le journal `data/` : `loom inspect 01a12670-6717-7227-bd62-abd568e72cdc` le relit.

Oubliez l'enregistrement de l'outil et le run ne démarre pas. Retirez la ligne `loom.register(...)` :

```bash
grep -v 'loom.register' sans_fichier.py > sans_enregistrement.py
uv run python sans_enregistrement.py 2>&1 | tail -1
```

```text
loom_ia.config.errors.ConfigError: Référence 'prix_ttc' introuvable : aucun objet de ce nom (enregistrés : aucun). Utiliser 'imports' ou un chemin 'module:attr'
```

(Une longue trace précède cette ligne ; `tail -1` ne garde que l'erreur. Le fichier `sans_enregistrement.py` peut être supprimé ensuite.)

#### À retenir

- **Le YAML et le code sont équivalents.** Un `LoomConfig` peut aussi être chargé d'un fichier (`from loom_ia.config import load_config`, puis `load_config(Path("loom.yaml"))`) et ajusté avec `model_copy(update=...)` avant d'être donné à `Loom(...)`.
- **Enregistrez les outils avant le premier run** de l'agent qui s'en sert : les outils sont résolus à la première mise en route de l'agent, puis gardés.
- **Piège : le journal par défaut est en mémoire.** Une configuration construite sans `storage` garde le journal en mémoire : il disparaît à la fin du process et `loom inspect` ne peut rien relire. C'est pourquoi l'exemple déclare `EventsStorage(backend="jsonl", path=Path("data"))`. Sans fichier de configuration, un chemin relatif se lit depuis le dossier courant : lancez le script depuis `mon-agent/`.
- **Le YAML vaut mieux dès que quelqu'un d'autre que le développeur touche à l'agent** (un prompt à retoucher, un modèle à changer) : il se relit, se compare et se valide à part. Le code est le bon choix pour les tests et les agents composés dynamiquement.
- **L'import de `EventsStorage` et `StorageConfig` se fait depuis `loom_ia.config.models`**, pas depuis `loom_ia.config` : c'est le chemin qui fonctionne en 2.0.0.

---

## Et ensuite

Vous savez maintenant installer Loom-IA, écrire un outil, valider et inspecter un agent, l'appeler depuis Python, le brancher sur un vrai modèle et le construire sans fichier. Le projet `mon-agent/` contient désormais :

```
mon-agent/
├── loom.yaml            modèles (simulés et réels), stockage, logs
├── outils.py            prix_ttc, chercher_devis, montant_echeance
├── agents/              devis, atelier, devis_panne, devis_tarife, devis_reel, devis_local
├── prompts/devis.md
├── appel.py, appel_complet.py, appel_echec.py, appel_logs.py, suivi.py, suivi_types.py
├── lire_journal.py, essai_schema.py, sans_fichier.py
├── loom.schema.json, agent.schema.json
└── data/                le journal (un fichier JSONL par session)
```

Un agent qui calcule et qui retrouve un devis ne suffit pas pour la Plomberie Dupont. Il manque encore ce qui rend un agent digne de confiance :

- **Une conversation** : l'agent doit se souvenir que l'on parle toujours de Mme Martin d'un message à l'autre.
- **Une validation humaine** : l'e-mail de relance ne part qu'après un « oui » de l'artisan.
- **Des règles en code** : refuser un taux de TVA qui n'existe pas, exiger que l'agent passe par l'outil.
- **Des contrôles de sortie** : exiger un e-mail avec un objet et un corps, et faire vérifier par un juge qu'aucun montant n'est inventé.
- **Des rôles** : confier la rédaction à un modèle plus léger.
- **Des serveurs MCP** : brancher l'agenda ou le CRM de l'artisan.

Tout cela est dans [02-maitriser.md](02-maitriser.md) (chapitres 7 à 14), qui part exactement du projet `mon-agent/` tel qu'il est à la fin de ce fichier.

**Navigation** : [01-demarrer.md](01-demarrer.md) · suivant : [02-maitriser.md](02-maitriser.md)
