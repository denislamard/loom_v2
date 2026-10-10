# Loom-IA

Loom-IA est un moteur d'agents IA écrit en Python asynchrone. Vous décrivez un agent : le modèle qui pilote, les outils qu'il peut appeler, les règles qu'il doit respecter. Loom-IA le fait tourner et garde la trace de tout ce qui se passe. Le même moteur s'utilise comme librairie Python, comme API REST ou comme serveur MCP.

Il a été conçu pour des agents qui travaillent pour de vrai, chez des artisans et dans des PME : relancer un devis, répondre à un client, préparer un document. Ce sont des tâches où un montant inventé ou un e-mail envoyé deux fois ne se rattrapent pas avec des excuses.

```bash
pip install "loom-ia[anthropic,http,mcp]"
```

**Sommaire**

1. [Qu'est-ce que Loom-IA ?](#quest-ce-que-loom-ia-)
2. [Loom-IA sur le terrain](#loom-ia-sur-le-terrain)
3. [Guide d'installation](#guide-dinstallation)
4. [Prise en main : votre premier agent](#prise-en-main--votre-premier-agent)
5. [Partie technique : les fonctions de Loom-IA](#partie-technique--les-fonctions-de-loom-ia)
6. [Développer et tester](#développer-et-tester)
7. [Licence](#licence)

---

## Qu'est-ce que Loom-IA ?

### Le problème

Faire tourner un agent dans un notebook prend un après-midi. Le confier à un plombier qui s'en servira tous les jours, c'est une autre affaire. L'agent ne doit pas inventer de montant ni coûter plus cher qu'il ne rapporte. Il doit demander la permission avant d'envoyer quoi que ce soit et survivre à un redémarrage du serveur. Et il faut pouvoir comprendre après coup pourquoi il a répondu ce qu'il a répondu.

Loom-IA prend en charge cette partie-là. Le métier, c'est-à-dire les outils, les prompts et les règles propres à votre activité, reste de votre côté.

### Le principe

Un agent reçoit une demande : du texte, parfois une photo. Un premier modèle, l'orchestrateur, la lit et choisit entre répondre tout de suite et appeler des outils. Ces outils peuvent être des fonctions Python, des outils exposés par des serveurs MCP, d'autres modèles spécialisés dans une tâche, ou des sous-agents complets. Les résultats reviennent à l'orchestrateur, qui recommence jusqu'à pouvoir répondre.

Ce schéma est classique. Ce qui change, c'est la façon dont Loom-IA encadre la boucle, sur trois points.

**Le journal fait foi.** Chaque fait d'un run est écrit dans un journal d'événements au moment où il se produit : un message, un appel de modèle, un appel d'outil, une décision, un coût. L'état du run, l'historique de la conversation, les coûts et les traces se déduisent tous de ce journal. Un run peut donc reprendre après un plantage, rester des heures en pause en attendant une validation, ou être rejoué à l'identique une semaine plus tard pour comprendre ce qui s'est passé.

**Le code décide, le modèle propose.** Les budgets, les contrôles de sortie, les demandes d'approbation et le déclenchement d'un juge sont du code déterministe, branché à des points précis de la boucle. Un modèle ne peut pas choisir de sauter une validation. Il ne fait pas non plus les calculs : un outil s'en charge, et un contrôle vérifie ce que le modèle en a retenu.

**Chaque tâche va au modèle qui lui convient.** L'orchestrateur peut confier une tâche bien délimitée, comme rédiger un e-mail ou décrire une photo, à un modèle moins cher. Ce modèle ne reçoit que ce dont il a besoin, sans tout l'historique. Le modèle le plus cher sert à réfléchir, pas à recopier un devis.

### Trois façons de s'en servir

| Accès | Pour qui | Ce qu'il apporte |
|---|---|---|
| Librairie Python | Votre application Python | Vos outils Python, le déroulé en direct, la validation humaine dans le code |
| API REST | Une appli mobile, un front, un autre service | Runs synchrones ou en arrière-plan, flux SSE, approbations, lecture des sessions et des traces |
| Serveur MCP | Claude Desktop, Claude Code, un autre agent | Chaque agent devient un outil MCP |

La commande `loom` complète le tout. Elle sert à lancer un run, le suivre, l'inspecter, le rejouer ou valider une configuration.

Les trois accès partagent la même configuration et le même moteur. Un même agent, interrogé par l'API Python, par REST ou par MCP, laisse la même suite d'événements dans le journal.

### Ce que Loom-IA ne fait pas

**Pas d'interface graphique.** Loom-IA fournit en revanche de quoi en construire une : des traces structurées en arbre, une API de lecture et un schéma d'événements versionné.

**Pas de logique métier.** Elle reste dans vos outils, vos prompts et vos règles.

**Pas de framework multi-agents.** Les agents ne discutent pas entre eux. La délégation est hiérarchique : l'orchestrateur confie des tâches à des rôles et à des sous-agents, qui lui rendent un résultat.

### Quelques mots de vocabulaire

| Terme | Sens |
|---|---|
| **Agent** | Une définition nommée : un orchestrateur, des outils, des rôles, des règles. Une instance en héberge plusieurs. |
| **Orchestrateur** | Le modèle qui pilote la boucle et choisit les outils. Dans la configuration, c'est le rôle `main`. |
| **Rôle** | Un modèle spécialisé, appelé par l'orchestrateur comme un outil, avec son propre prompt. |
| **Sous-agent** | Un agent complet appelé comme un outil, avec ses propres outils et sa propre boucle. |
| **Outil** | Ce que l'orchestrateur peut appeler : fonction Python, outil MCP, rôle, sous-agent. |
| **Run** | Une exécution d'un agent pour une demande. |
| **Session** | Une conversation : plusieurs runs qui partagent le même journal. |
| **Journal** | La suite ordonnée et immuable des événements d'une session. |
| **Politique** | Du code branché sur un point de la boucle, qui laisse passer, corrige, refuse ou arrête. |
| **Juge** | Un modèle qui note une sortie selon des critères écrits. |
| **Client** | Un espace isolé (configuration, secrets, journal, budgets) qui permet de servir plusieurs entreprises avec une seule instance. |

---

## Loom-IA sur le terrain

Tous les cas qui suivent viennent des exemples du dépôt, dans `examples/`. La plupart tournent avec des modèles simulés, sans clé ni réseau. L'option `--reel` les relance avec de vrais modèles, dont les clés sont lues dans l'environnement.

### Relancer un devis resté sans réponse

La Plomberie Dupont a envoyé début septembre un devis de 1 840 € à Mme Martin pour remplacer son chauffe-eau. Pas de nouvelles depuis. L'artisan écrit à son agent : « Relance Mme Martin pour le devis D-2026-042, sur un ton cordial. »

L'orchestrateur commence par appeler `chercher_devis`, un outil Python branché sur les données de l'entreprise. Il confie ensuite la rédaction au rôle `rediger_relance`, servi par un modèle plus léger. Ce rôle reçoit la demande de l'artisan mot pour mot et le devis, rien d'autre.

L'e-mail doit sortir en JSON, avec un objet et un corps. Un contrat vérifie ce format. Un juge, troisième modèle, contrôle ensuite qu'aucun montant, date ou délai n'a été inventé. S'il refuse, le rôle corrige sa copie.

Reste l'envoi. L'outil `envoyer_email` est déclaré à effet de bord et soumis à approbation : le run se met en pause et attend que l'artisan valide. Si le serveur redémarre entre-temps, ou si le process est tué en pleine exécution, le run reprend là où il s'était arrêté. La clé d'idempotence garantit que l'e-mail ne part qu'une fois. Le journal garde toute la chronologie : les appels, les coûts, la validation, qui l'a donnée et quand.

À voir dans `examples/j3/` (contrats, juge, budget, modèle de secours) et `examples/j4/` (conversation, approbation, reprise après un `kill -9`).

### Un assistant qui lit l'heure, compte et regarde une photo

Dans `examples/j2/`, l'orchestrateur lit l'heure grâce à un serveur MCP et fait ses calculs avec un second. Quand on lui joint une photo, il en confie la description à un rôle « vision », qui n'est proposé au modèle que dans ce cas. Avant de répondre, il fait vérifier ses dates et ses calculs par un sous-agent qui a ses propres outils. Chaque appel, chez le parent comme chez l'enfant, se retrouve dans l'arbre du run.

### Un même service pour plusieurs entreprises

La Plomberie Dupont et le Chauffage Martin utilisent le même agent de relance. Chacune a ses modèles, son budget, le jeton de son propre CRM et son propre journal. Chacune a aussi sa clé d'API, et c'est la clé seule qui décide au nom de qui une requête agit.

Avec un journal Postgres, une politique de sécurité au niveau des lignes empêche une requête de lire les données d'un autre client. Le journal peut aussi être chiffré avec une clé par client : effacer cette clé rend ses données illisibles, sauvegardes comprises.

En production, les runs passent par une file RabbitMQ et plusieurs workers : si l'un d'eux tombe, un autre reprend le run. Un webhook peut aussi ouvrir un run sans que l'appelant connaisse l'API de Loom-IA. C'est le cas d'un CRM qui signale un devis signé, ou d'un planificateur qui lance chaque matin la tournée des relances. À voir dans `examples/j5/`.

### Un agent qui fabrique ses propres outils

Avec la source d'outils `forge`, l'agent écrit lui-même une petite fonction Python, accompagnée d'exemples. C'est le cas de `total_ttc`, qui calcule le HT, la TVA et le TTC d'un devis.

Ce code ne s'exécute jamais sur la machine hôte. Chaque exemple tourne dans une microVM Firecracker, et l'outil n'est accepté que si tous les exemples donnent le résultat attendu. Il rejoint alors le catalogue du client et devient, dès le run suivant, un outil comme les autres. À voir dans `examples/j6/forge.py` (Linux et KVM nécessaires).

### Une mémoire qui ne retient que ce qu'on lui autorise

La mémoire long terme passe par un serveur MCP, loom-notes, branché comme n'importe quel autre. L'agent y cherche librement, mais chaque écriture attend l'accord de l'artisan, et chaque client a sa propre base. À voir dans `examples/j6/memoire.py`.

### Vos agents dans Claude Desktop ou Claude Code

`loom mcp` publie chaque agent comme un outil MCP. Vous demandez à Claude Code de relancer le devis 042, et c'est votre agent qui fait le travail, avec ses règles et ses contrôles.

Quand un appel demande une approbation, Loom-IA pose la question à l'utilisateur si le client MCP sait le faire. Sinon, le run attend en pause qu'un humain tranche par l'API REST ou en ligne de commande. Une approbation ne passe jamais par un outil MCP : un modèle ne peut pas valider lui-même une action sensible.

### Changer de modèle sans rien casser

Un nouveau modèle sort, moins cher. Avant de basculer, vous rejouez des runs réels avec lui :

```bash
loom replay <run_id> --mode variant --model main=NOUVEAU_MODELE
```

Les requêtes que le journal connaît déjà sont servies sans appel au fournisseur, et les outils à effet de bord ne sont jamais réexécutés : personne ne recevra un second e-mail parce que vous testez un modèle. Un rapport compare ensuite les deux versions : issue, réponse, appels, coût, durée.

Une suite d'évaluations (`loom eval`) fait la même chose sur une série de cas, avec des contrôles déterministes et un juge. À voir dans `examples/j6/replay.py` et `examples/j6/evals.py`.

---

## Guide d'installation

### Prérequis

- **Python 3.14** ou plus récent.
- **[uv](https://docs.astral.sh/uv/)** de préférence ; pip fonctionne aussi.
- **Linux**, la plateforme sur laquelle Loom-IA est développé et testé. La sandbox Firecracker l'exige.
- **Une clé d'API** pour chaque fournisseur de modèles que vous comptez utiliser. Les essais avec le modèle simulé n'en demandent aucune.

### Installer le paquet

Avec pip, dans un environnement virtuel :

```bash
python3.14 -m venv .venv
source .venv/bin/activate
pip install "loom-ia[anthropic,http,mcp]"
```

Avec uv, dans votre projet :

```bash
uv add "loom-ia[anthropic,http,mcp]"
```

Le paquet s'appelle `loom-ia`, s'importe sous le nom `loom_ia` et installe la commande `loom`.

Le noyau ne dépend que de pydantic, pyyaml, jsonschema et regex. Tout le reste arrive par des extras, à choisir selon ce que vous utilisez :

| Extra | À installer si vous utilisez |
|---|---|
| `anthropic` | Claude, ou un fournisseur qui expose l'API d'Anthropic (MiniMax, par exemple) |
| `openai` | OpenAI ou un fournisseur compatible (Together, vLLM, Ollama…) |
| `mcp` | Des serveurs MCP comme outils, ou `loom mcp` |
| `http` | L'API REST et le serveur MCP en HTTP (`loom serve`) |
| `sqlite` | Le journal ou le magasin d'idempotence en SQLite |
| `postgres` | Le journal, l'idempotence ou le bus en Postgres |
| `redis` | Le bus ou l'idempotence en Redis |
| `rabbitmq` | La file de tâches RabbitMQ et `loom worker` |
| `otel` | L'export des traces vers OpenTelemetry |
| `crypto` | Le chiffrement du journal et des fichiers, client par client |
| `all` | Tous les extras |

Si la configuration demande un extra absent, Loom-IA refuse de la charger et nomme l'extra à installer :

```
Configuration : Journal 'postgres' : le paquet 'asyncpg' n'est pas installé (installer l'extra : loom-ia[postgres])
```

### Depuis les sources

```bash
git clone https://github.com/denislamard/loom_v2.git
cd loom_v2
uv sync --all-extras
```

uv installe Python 3.14 s'il le faut et crée l'environnement. Pour vérifier que tout fonctionne, lancez l'agent de démonstration, qui tourne sur un modèle simulé :

```bash
uv run loom --config examples/j1/demo/loom.yaml run demo "Combien font 12 × 7 + 3 ?"
```

```
Statut     : completed · itérations : 2 · tokens : 530/93 · coût : 0.0000 $
Run        : 01a11f3e-8783-75c1-8bcc-817c81f9d0d7
12 fois 7, plus 3, font 87.
```

Les exemples qui appellent de vrais modèles lisent leurs clés dans l'environnement. Le plus simple est un fichier `.env` à la racine du dépôt, passé à uv :

```bash
uv run --env-file .env python examples/j3/juge.py --reel
```

### Pour un déploiement en service

En local, Loom-IA n'a besoin d'aucun service : le journal tient dans des fichiers JSONL et les tâches de fond tournent dans le process. Un service à plusieurs workers s'appuie sur trois briques :

| Service | Rôle |
|---|---|
| Postgres | Journal, idempotence, bus |
| Redis | Bus, idempotence |
| RabbitMQ | File de tâches |

La CI les teste avec Postgres 16, Redis 7 et RabbitMQ 3.12.

### La sandbox Firecracker (facultatif)

Le client qui pilote les microVM fait partie du paquet. La plateforme, elle, vit dans le dossier `firecracker/` du dépôt et ne s'installe pas avec pip : il faut cloner le dépôt. Elle demande :

- Linux ;
- la virtualisation matérielle, avec `/dev/kvm` accessible ;
- `sudo`, pour construire l'image.

```bash
cd firecracker
./make_vm.sh agent-01
```

Le script télécharge dans un cache le binaire Firecracker, un noyau et une image Ubuntu 24.04. Il construit ensuite une VM autonome dans `firecracker/vms/agent-01/`, avec le service d'exécution `execd` dans son image. C'est ce dossier que vous donnerez à la source `forge` (voir [Les outils](#les-outils)).

---

## Prise en main : votre premier agent

On va construire un petit agent qui répond aux questions de prix d'un artisan. Pour calculer un montant TTC, il passera par un outil Python plutôt que de compter de tête. On le fait d'abord tourner sur un modèle simulé, pour tout vérifier sans clé ni réseau, puis on le branche sur un vrai modèle.

Il vous faut `loom-ia[anthropic,http,mcp]`, installé comme indiqué plus haut.

### 1. Le dossier du projet

Un projet Loom-IA tient en un fichier de configuration racine, un dossier d'agents et un dossier de prompts :

```
mon-agent/
├── loom.yaml          modèles, stockage, modules à importer
├── outils.py          vos outils Python
├── agents/
│   └── devis.yaml     un fichier par agent
└── prompts/
    └── devis.md       le prompt système de l'agent
```

### 2. Écrire un outil

`outils.py` :

```python
from loom_ia.tools import tool


@tool
def prix_ttc(montant_ht: float, taux_tva: float = 20.0) -> dict[str, float]:
    """Calcule la TVA et le montant TTC d'un devis. Le taux est en pourcentage."""
    tva = round(montant_ht * taux_tva / 100, 2)
    return {"montant_ht": montant_ht, "tva": tva, "montant_ttc": round(montant_ht + tva, 2)}
```

Le décorateur `@tool` suffit. Loom-IA déduit de la signature le schéma des arguments, et la docstring devient la description que lit le modèle. Soignez-la : c'est elle qui indique au modèle quand appeler l'outil.

La fonction peut être synchrone ou `async`. Elle peut renvoyer :

- du texte ;
- un dictionnaire ou un modèle Pydantic, transmis en JSON ;
- une image.

### 3. Décrire l'agent

`agents/devis.yaml` :

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

`main` désigne l'orchestrateur, avec son modèle et son prompt système. `max_iterations` borne le nombre de tours de la boucle ; une fois la borne atteinte, l'agent doit répondre sans outils.

`prompts/devis.md` :

```markdown
Tu es l'assistant d'un artisan. Tu réponds en français, en une ou deux phrases.

Pour tout calcul de prix, appelle l'outil `prix_ttc` au lieu de calculer
toi-même, et reprends les montants qu'il renvoie.
```

### 4. La configuration racine

`loom.yaml` :

```yaml
# yaml-language-server: $schema=./loom.schema.json
version: 1

imports: [outils]              # le module outils.py, à côté de ce fichier
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

telemetry:
  logging: {level: WARNING}    # en INFO, une ligne par appel de modèle et d'outil

storage:
  events: {backend: jsonl, path: data}   # le journal : un fichier JSONL par session
```

Le modèle `sdk: fake` joue un script : à son premier tour il appelle `prix_ttc`, au second il répond. Rien ne dépend d'un fournisseur, ce qui le rend pratique pour mettre un agent en place ou écrire des tests.

La première ligne relie votre éditeur au schéma de la configuration, pour la complétion et les erreurs en direct (dans VS Code, avec l'extension YAML, par exemple). Générez ce schéma une fois :

```bash
loom schema > loom.schema.json
```

### 5. Valider, puis lancer

```bash
cd mon-agent
loom validate
```

`loom validate` charge la configuration et vérifie sa cohérence : modèle inconnu, outil introuvable, extra manquant… Il monte ensuite chaque agent et affiche ce qu'il a compris :

```
Config     : loom.yaml
Profil     : aucun (ni --profile, ni LOOM_PROFILE, ni 'profile:') ; surcharges : aucune
Modèles    : SIMULE
Agents     : devis
Outils     : prix_ttc
…
  devis : modèle SIMULE, 1 outil(s) Python

1 agent(s) monté(s) sans erreur.
```

Lancez ensuite un run :

```bash
loom run devis "Quel prix TTC pour un devis de 1 250 € HT en rénovation (TVA à 10 %) ?"
```

```
Statut     : completed · itérations : 2 · tokens : 684/110 · coût : 0.0000 $
Run        : 01a11f47-ae95-7161-91ab-ee7c2558ef41
Pour 1 250 € HT avec une TVA à 10 %, votre client paiera 1 375 € TTC.
```

Avec `--stream`, la réponse s'affiche au fil de l'eau, appels d'outils compris.

### 6. Regarder ce qui s'est passé

Tout est dans le journal, `data/default/<session>.jsonl`. Pour le lire sans plonger dans le JSON :

```bash
loom inspect 01a11f47-ae95-7161-91ab-ee7c2558ef41
```

```
run devis — completed, 80 ms, 0.000000 $
  étape 1
    modèle fake-1 (main) — 1 ms, 259 → 68 tokens, 0.000000 $
      · répond    : Je calcule le prix TTC.
      · appelle   : prix_ttc({"montant_ht": 1250, "taux_tva": 10})
  étape 2
    outil prix_ttc — 5 ms
      · arguments : {"montant_ht": 1250, "taux_tva": 10}
      · résultat  : {"montant_ht": 1250.0, "tva": 125.0, "montant_ttc": 1375.0}
  étape 3
    modèle fake-1 (main) — 0 ms, 425 → 42 tokens, 0.000000 $
      · répond    : Pour 1 250 € HT avec une TVA à 10 %, votre client paiera 1 375 € TTC.
```

`loom report <run_id>` détaille la consommation par rôle et par modèle. Avec `--session`, il couvre toute une conversation.

### 7. Brancher un vrai modèle

Ajoutez un modèle dans la liste `models` de `loom.yaml` :

```yaml
  - id: HAIKU
    sdk: anthropic
    model: claude-haiku-5-5
    api_key_env: ANTHROPIC_API_KEY   # le nom de la variable, jamais la clé elle-même
    max_tokens: 16000                # la réflexion du modèle compte dedans
    pricing: {input: 0.10, output: 0.50, cache_read: 0.01, cache_write: 0.125}   # $ par million de tokens
```

Faites-le ensuite utiliser par l'agent, dans `agents/devis.yaml` :

```yaml
main:
  model: HAIKU
  system_file: devis.md
```

Puis lancez le même run :

```bash
export ANTHROPIC_API_KEY=...
loom run devis "Quel prix TTC pour un devis de 1 250 € HT en rénovation (TVA à 10 %) ?"
```

`pricing` est facultatif. Sans lui, Loom-IA ne sait pas calculer de coût, et ne peut donc pas appliquer un budget en dollars.

Les fournisseurs compatibles avec OpenAI passent par `sdk: openai` et une adresse. Un modèle local servi par Ollama, par exemple, n'a besoin d'aucune clé :

```yaml
  - id: LOCAL
    sdk: openai
    api: chat
    base_url: http://localhost:11434/v1
    model: qwen3:8b
```

### 8. Appeler l'agent depuis Python

```python
import asyncio

from loom_ia.access import Loom


async def main() -> None:
    async with Loom.from_config("loom.yaml") as loom:
        result = await loom.run("devis", "Quel prix TTC pour 1 250 € HT à 10 % ?")
        print(result.status, result.text)


asyncio.run(main())
```

`run()` attend la fin du run et renvoie un `RunResult` : le statut, le texte de la réponse, la consommation, et les appels en attente d'approbation s'il y en a.

Pour suivre le run en direct, `stream()` mêle les morceaux de texte du modèle et les événements du journal :

```python
from loom_ia.core.events import Event
from loom_ia.core.model import TextDelta

# dans le bloc « async with » de l'exemple précédent
async for item in loom.stream("devis", "Quel prix TTC pour 1 250 € HT à 10 % ?"):
    if isinstance(item, TextDelta):
        print(item.text, end="", flush=True)
    elif isinstance(item, Event) and item.type == "tool.called":
        print("\n[appel d'outil]")
```

Loom-IA écrit ses logs avec le module `logging` de la bibliothèque standard et ne touche jamais aux handlers de votre application. Si vous voulez les siens, appelez `loom_ia.telemetry.configure_logging()`.

### 9. L'ouvrir en REST et en MCP

```bash
loom serve
```

L'API écoute sur `http://127.0.0.1:8000`, et sa documentation interactive se trouve sur `/docs`.

```bash
curl -X POST http://127.0.0.1:8000/v1/agents/devis/runs \
  -H 'Content-Type: application/json' \
  -d '{"message": "Quel prix TTC pour 1 250 € HT à 10 % ?"}'
```

La réponse contient le statut, le texte, l'usage, le coût et sa ventilation. Avec `"background": true`, l'appel rend tout de suite l'identifiant du run (code 202), et vous suivez son déroulé en SSE :

```bash
curl -N http://127.0.0.1:8000/v1/runs/<run_id>/events
```

Tant qu'aucune clé d'API n'est déclarée, l'API est ouverte. C'est commode sur votre machine, pas sur un serveur : la section [Plusieurs clients et sécurité](#plusieurs-clients-et-sécurité) explique comment créer des clés.

Pour brancher l'agent sur Claude Code :

```bash
claude mcp add loom -- loom --config /chemin/vers/mon-agent/loom.yaml mcp
```

Pour Claude Desktop, dans `claude_desktop_config.json` :

```json
{
  "mcpServers": {
    "loom": {
      "command": "/chemin/vers/.venv/bin/loom",
      "args": ["--config", "/chemin/vers/mon-agent/loom.yaml", "mcp"]
    }
  }
}
```

L'agent `devis` devient un outil MCP qui prend un `message`. Trois outils de contrôle l'accompagnent : `run_status`, `run_report` et `cancel`.

### 10. Et ensuite

Trois ajouts reviennent presque toujours.

**Une conversation.** Avec un identifiant de session, chaque run relit l'historique et y ajoute ses échanges :

```bash
loom run devis "Quel prix TTC pour 1 250 € HT à 10 % ?" --session chantier-durand
loom run devis "Et avec une TVA à 20 % ?" --session chantier-durand
```

**Une validation humaine.** Un outil qui agit sur le monde le déclare :

```python
@tool(side_effects="irreversible", approval="always")
def envoyer_email(destinataire: str, objet: str, corps: str) -> str:
    """Envoie un e-mail au client."""
    ...
```

Le run s'arrête alors avant l'envoi, en statut `paused`, et `loom run` indique la commande à lancer pour trancher :

```
Statut     : paused · itérations : 1 · tokens : 193/57 · coût : 0.0000 $
Run        : 01a11f3e-5a5e-7338-a41d-8b0c1ae05e25
En attente : envoyer_email (fake_0_0) — loom approve 01a11f3e-5a5e-7338-a41d-8b0c1ae05e25 --call fake_0_0
```

`loom approve <run_id>` reprend le run. `loom reject <run_id> --reason "…"` renvoie le refus et son motif au modèle. Une pause exige un journal durable, puisque le run peut attendre des heures et le process s'arrêter entre-temps.

**Un rôle, un contrat, un juge.** Un rôle délégué rédige, un contrat vérifie la forme de sa sortie, un juge en vérifie le fond. Ces trois mécanismes sont détaillés dans la partie technique.

---

## Partie technique : les fonctions de Loom-IA

Cette partie fait le tour de ce que Loom-IA sait faire, domaine par domaine. Les extraits de configuration sont partiels ; le schéma complet s'obtient avec `loom schema`.

- [Architecture](#architecture)
- [Le run : une machine à états et un journal](#le-run--une-machine-à-états-et-un-journal)
- [La configuration](#la-configuration)
- [Les modèles](#les-modèles)
- [Rôles et sous-agents](#rôles-et-sous-agents)
- [Les outils](#les-outils)
- [Politiques, contrats et juges](#politiques-contrats-et-juges)
- [Sessions et historique](#sessions-et-historique)
- [Exécution durable](#exécution-durable)
- [Coûts, budgets et quotas](#coûts-budgets-et-quotas)
- [Plusieurs clients et sécurité](#plusieurs-clients-et-sécurité)
- [Observabilité, rejeu et tests](#observabilité-rejeu-et-tests)
- [Les accès en détail](#les-accès-en-détail)
- [Les stockages](#les-stockages)

### Architecture

```
Accès          API Python · REST · MCP · CLI
Application    Agents · Runtime · Sessions · Clients · Rejeu et évaluations
Moteur         Boucle · Exécution des outils · Rôles · Contrôles · Politiques
Noyau          Modèle de domaine · Ports · Événements
Adaptateurs    Modèles · MCP · Journaux · Bus · Files · Sandbox
```

Le noyau ne fait aucune entrée-sortie. Il définit des types (messages, état d'un run, événements) et des interfaces, les « ports » : appeler un modèle, fournir des outils, écrire le journal, ranger un fichier, mettre une tâche en file, lire un secret. Les adaptateurs implémentent ces ports en périphérie. Chacun ne charge son SDK que si la configuration le demande. Ni le noyau ni le moteur n'importent un SDK de modèle, FastAPI ou MCP. La CI vérifie ces règles à chaque passage avec `import-linter`.

Tous les types du domaine sont des modèles Pydantic. Ils se sérialisent tels quels dans le journal et en HTTP, et ils produisent les schémas JSON de la configuration et des événements.

Un message est une liste de blocs typés, indépendante du fournisseur : du texte, une image ou un fichier (par référence, jamais les octets), un appel d'outil, un résultat d'outil, un raisonnement. Chaque adaptateur traduit ce format au moment de l'appel. Une conversation peut donc changer de fournisseur en cours de route, et un run peut basculer sur un modèle de secours sans perdre son historique.

### Le run : une machine à états et un journal

Un run avance d'état en état :

| État | Ce qui se passe |
|---|---|
| `ready_for_model` | L'orchestrateur est appelé |
| `awaiting_tools` | Les outils demandés s'exécutent, en parallèle |
| `waiting_child` | Un sous-agent attend une validation |
| `paused` | Le run attend une décision humaine ; rien ne tourne |
| `finalizing` | Itérations ou budget épuisés : une dernière réponse, sans outils |
| `completed`, `failed`, `cancelled` | Fin du run |

Le pilote suit une règle simple. Il recalcule l'état à partir du journal, exécute un seul effet par étape (un appel de modèle ou un lot d'outils), et écrit chaque événement avant de l'appliquer à l'état.

Un run n'est donc jamais stocké en tant que tel : comme l'historique de la conversation, les coûts ou l'arbre des traces, c'est une projection de son journal. On reprend un run en relisant son journal, une pause ne coûte rien puisque rien ne tourne, et n'importe quel worker peut piloter n'importe quel run.

Chaque événement a une enveloppe commune et une charge typée. L'enveloppe porte un identifiant UUIDv7 triable dans le temps, la position de l'événement dans la session, le client, la session, le run, le span, le type et le statut. Les types suivent la forme `<catégorie>.<action>` : `run.started`, `model.responded`, `tool.called`, `tool.completed`, `guard.checked`, `judge.evaluated`, `approval.requested`, `budget.exceeded`, `run.completed`… Le schéma est versionné. C'est le contrat sur lequel une interface de suivi peut s'appuyer.

Un run se termine quand l'orchestrateur répond sans appeler d'outil, quand un outil terminal a rendu sa sortie, ou quand on l'arrête. Quand les itérations ou le budget s'épuisent, Loom-IA force une dernière réponse sans outils plutôt que de laisser l'utilisateur sans rien. Le `timeout` d'un agent borne le temps de pilotage cumulé de ses runs ; au-delà, le run échoue mais reste reprenable.

Un run s'arrête de l'extérieur par `Loom.cancel()`, par `POST /v1/runs/{id}/cancel` ou par l'outil MCP `cancel`. L'arrêt se propage à ses sous-agents.

### La configuration

La configuration s'écrit en YAML : un fichier racine, `loom.yaml`, un fichier par agent dans `agents/`, et les prompts à part dans `prompts/`. Pydantic la valide strictement au chargement, et chaque erreur dit ce qui ne va pas et où. Les secrets n'y figurent jamais : la configuration ne contient que des noms de variables d'environnement.

Le fichier racine regroupe les sections suivantes :

```yaml
version: 1
profile: dev                  # dev | prod
imports: [outils, politiques] # modules Python chargés au démarrage
agents_dir: agents/
prompts_dir: prompts/

models: [...]                 # les modèles disponibles
mcp_servers: [...]            # les serveurs MCP
tool_sources: [...]           # les sources d'outils de paquets installés
storage: {...}                # journal, fichiers, idempotence, file, bus, chiffrement, rétention
sessions: {...}               # snapshots et résumés de conversation
execution: {...}              # délais, pièces jointes
budgets: {...}                # plafonds par défaut
telemetry: {...}              # logs, capture, masquage, exports
tenants: [...]                # les clients
security: {...}               # clés d'API
server: {...}                 # adresse HTTP, MCP en HTTP
triggers: [...]               # webhooks entrants
profiles: {prod: {...}}       # surcharges par profil
```

**Références vers votre code.** Un outil, une politique ou une condition de juge se désigne par un nom enregistré, ou par un chemin `module:attribut`. Les modules voisins de `loom.yaml` s'importent directement, et deux configurations chargées dans un même process gardent chacune les leurs.

**Variables dans les prompts.** Les prompts et les gabarits acceptent des variables `{{ … }}`. Une variable inconnue est une erreur de chargement, pas un trou silencieux dans le prompt.

**Profils.** Il y en a deux, `dev` et `prod`. Le profil actif se choisit, du plus fort au plus faible, par `--profile`, par la variable `LOOM_PROFILE`, puis par la clé `profile:` du fichier. `prod` change certains avertissements en erreurs : un juge qui utilise le même modèle que ce qu'il juge, un modèle sans tarif sous un budget en dollars, une API REST sans clé. `dev` tolère au contraire ce que la production refuserait, comme un agent qui peut se mettre en pause sur un journal non durable. Sans profil, rien n'est durci ni assoupli.

**Contrôles au démarrage.** Le chargement refuse notamment :

- un modèle ou un rôle inconnu ;
- une référence Python introuvable ;
- un extra manquant ;
- une décision de politique interdite à son point ;
- un serveur MCP référencé mais non déclaré ;
- un modèle de secours incapable de faire ce qu'on lui demande.

**Rechargement en développement.** `loom serve --reload` surveille le dossier de la configuration. À chaque changement, un nouveau process la charge pendant que l'ancien continue de servir. Si la nouvelle configuration est cassée, elle est refusée avec la raison, et l'ancien process continue. Cette option est refusée en profil `prod`.

**Sans fichier.** Tout se construit aussi en Python, avec les mêmes modèles :

```python
from loom_ia.access import Loom
from loom_ia.agents import AgentSpec, MainRole, PythonTool
from loom_ia.config import LoomConfig
from loom_ia.core.model import ModelSpec

config = LoomConfig(
    version=1,
    models=(
        ModelSpec(
            id="HAIKU", sdk="anthropic", model="claude-haiku-5-5", api_key_env="ANTHROPIC_API_KEY"
        ),
    ),
    agents=(
        AgentSpec(
            name="devis",
            main=MainRole(model="HAIKU", system="Tu es l'assistant d'un artisan."),
            tools=(PythonTool(python="prix_ttc"),),
        ),
    ),
)
loom = Loom(config)
loom.register("prix_ttc", prix_ttc)
```

### Les modèles

Deux adaptateurs couvrent l'essentiel du marché. `sdk: anthropic` sert Claude et les fournisseurs qui exposent la même API. `sdk: openai` sert OpenAI et les fournisseurs compatibles (Together, vLLM, Ollama…), avec deux API au choix, `api: chat` ou `api: responses`. Sans `base_url`, c'est l'adresse officielle du fournisseur qui est utilisée.

```yaml
models:
  - id: M3
    sdk: anthropic
    base_url: https://api.minimax.io/anthropic
    model: MiniMax-M3
    api_key_env: M3_API_KEY
    max_tokens: 4096
    params: {temperature: 0.3}                   # transmis tel quel au fournisseur
    timeouts: {first_token: 60, idle: 60, total: 300}
    retry: {max_attempts: 3, max_delay: 30}
    circuit_breaker: {failures: 5, cooldown: 60}
    pricing: {input: 0.30, output: 1.20, cache_read: 0.06}
```

Et dans l'agent :

```yaml
main:
  model: M3
  fallbacks: [HAIKU]
```

**Erreurs et secours.** Loom-IA classe les erreurs avant de décider quoi faire :

- une erreur passagère (429, 5xx, délai dépassé, réseau) est retentée avec un délai exponentiel, en respectant `Retry-After` ;
- un quota épuisé bascule tout de suite sur le modèle de secours ;
- une erreur d'authentification ou une requête invalide ne sont jamais retentées.

La chaîne de secours se déclare pour l'orchestrateur, pour chaque rôle et pour chaque juge. Un run qui a basculé reste sur le secours jusqu'à la fin.

Un disjoncteur par modèle écarte un fournisseur en panne pour tous les runs de l'instance. Par défaut, il s'ouvre après 5 échecs et le fournisseur est écarté pendant 60 secondes.

**Streaming.** Le streaming est la primitive de base : chaque appel de modèle est un flux, et une réponse complète n'est que ce flux rassemblé. Trois délais le surveillent : l'attente du premier token, le silence entre deux morceaux et la durée totale.

**Capacités.** Chaque modèle déclare ce qu'il sait faire : outils, vision, raisonnement, JSON natif, taille de la fenêtre de contexte. Loom-IA vérifie au chargement qu'on ne demande pas à un modèle ce qu'il ne sait pas faire. Les images sont transmises en base64 aux modèles capables de les lire ; les autres reçoivent une mention du fichier.

**Raisonnement et cache.** Le raisonnement du modèle est conservé sous une forme neutre et renvoyé quand le fournisseur l'exige pendant une boucle d'outils. Avec `sdk: anthropic`, `cache: {system: true, tools: true, messages: true}` pose les points de cache de prompt. Quand le modèle le permet, une sortie structurée part avec le schéma JSON natif du fournisseur.

### Rôles et sous-agents

Un rôle est un modèle spécialisé que l'orchestrateur appelle comme un outil. Il fait un seul appel de modèle, sans historique ni outils, et ne reçoit que ce qu'il déclare :

```yaml
roles:
  - name: rediger_email
    description: Rédige l'e-mail qui accompagne le devis.
    model: HAIKU
    system: Tu rédiges des e-mails courts et polis pour un artisan.
    input_schema:
      type: object
      properties:
        ton: {type: string, description: "cordial, ferme, bref…"}
      required: [ton]
    context: [user_input, {tool_results: [prix_ttc]}]
    input_template: |-
      Demande de l'artisan : {{ context.user_input }}
      Montants calculés : {{ context.tool_results.prix_ttc }}
      Ton : {{ args.ton }}
    terminal: true
```

Ce que le rôle peut recevoir :

| Contexte | Contenu |
|---|---|
| `user_input` | La demande de l'utilisateur, mot pour mot |
| `attachments` | Les pièces jointes du run |
| `tool_results: [noms]` | Les résultats de ces outils dans le run (`scope: session` remonte aussi dans la conversation) |
| `session_summary` | Le dernier résumé de la conversation |
| `last_turns: N` | Les N derniers échanges de la session |
| `caller_context` | Les métadonnées passées par l'appelant |

Plutôt que de recopier un long résultat dans les arguments d'un rôle, l'orchestrateur peut passer une référence, `{"$ref": "result:3"}`. Loom-IA la résout au moment de l'appel : le contenu complet arrive au rôle, et l'orchestrateur ne paie pas deux fois les mêmes tokens.

Il existe deux sortes de rôles particuliers :

- **Le rôle terminal** (`terminal: true`) : quand il est appelé seul dans son tour et réussit, sa sortie devient la réponse finale telle quelle, sans repasser par l'orchestrateur.
- **Le rôle vision** : il déclare le contexte `attachments`. Il n'est proposé à l'orchestrateur que si le run contient une image, et son modèle doit avoir la capacité `vision`.

Un sous-agent est un agent complet, appelé comme un outil :

```yaml
subagents:
  - agent: verificateur
    name: verifier
    description: Vérifie les dates et les calculs de la réponse.
    budget_share: 0.3          # part de ce qui reste au budget du parent
```

Le sous-agent tourne avec ses propres outils et sa propre boucle, dans un run enfant écrit dans le même journal. Seule sa réponse finale revient au parent, et sa consommation s'ajoute à celle du parent. `max_depth`, sur l'agent, limite la profondeur d'imbrication (1 par défaut). L'annulation du parent se propage aux enfants. Quand un enfant attend une validation, ses demandes remontent jusqu'au run racine, et c'est là qu'on les tranche.

### Les outils

**Outils Python.** Le décorateur `@tool` accepte des déclarations qui changent la façon dont Loom-IA traite l'outil :

```python
from loom_ia.core.ports import ToolContext
from loom_ia.tools import tool


@tool(side_effects="irreversible", approval="always", timeout=20)
async def envoyer_email(destinataire: str, objet: str, corps: str, ctx: ToolContext) -> str:
    """Envoie un e-mail au client."""
    ...
```

| Déclaration | Valeurs | Effet |
|---|---|---|
| `side_effects` | `none`, `reversible`, `irreversible` | Décide de ce qu'on peut relancer après un plantage, et de ce qu'un rejeu ne réexécutera jamais |
| `approval` | `never`, `always`, `policy` | Validation humaine avant l'appel |
| `idempotent` | `true`, `false` | L'outil peut être relancé sans risque |
| `on_unknown` | `error`, `pause` | Que faire d'un appel interrompu dont on ne sait pas s'il a abouti |
| `timeout`, `offload_over` | secondes, caractères | Délai de l'appel, seuil de déport des gros résultats |

La plupart de ces réglages peuvent aussi se poser dans la configuration de l'agent, qui a le dernier mot. Un paramètre annoté `ToolContext` reçoit le contexte de l'appel, avec notamment sa clé d'idempotence, et n'apparaît pas dans le schéma. Une exception devient un résultat d'erreur pour le modèle ; `ToolError("…")` lui donne un message qu'il peut exploiter.

**Serveurs MCP.** Un serveur se déclare une fois à la racine, puis plusieurs agents peuvent s'en servir :

```yaml
mcp_servers:
  - name: crm
    transport: http                          # stdio | http
    url: https://crm.example/mcp
    headers_env: {Authorization: CRM_TOKEN}  # en-tête ← variable d'environnement
    scope: tenant                            # shared | tenant | run
    tools:
      envoyer_email: {side_effects: irreversible, approval: always}
  - name: math
    transport: stdio
    command: python
    args: [serveurs/serveur_math.py]
    scope: run
```

Dans l'agent :

```yaml
tools:
  - mcp: crm
    include: [rechercher, fiche_client, envoyer_email]
  - mcp: math
    alias: m                                 # préfixe raccourci : m__calculer
    required: true                           # sans lui, le run échoue
```

Les outils sont préfixés par leur serveur (`crm__rechercher`), ce qui évite les collisions entre serveurs. La portée règle la durée de vie de la connexion :

| Portée | Connexion |
|---|---|
| `shared` | Une seule pour tout le process |
| `tenant` | Une par client, avec ses propres identifiants |
| `run` | Ouverte et fermée avec chaque run, pour les serveurs qui gardent un état |

Les annotations MCP (`readOnlyHint`, `destructiveHint`, `idempotentHint`) donnent les valeurs par défaut, et la configuration les surcharge. Un serveur injoignable voit ses outils retirés du run, avec une trace dans le journal ; les autres serveurs restent utilisables. La reconnexion se fait avec un délai croissant, sous la garde d'un disjoncteur.

**Sources d'outils de paquets installés.** Un paquet Python peut fournir des outils par un point d'entrée du groupe `loom_ia.tools`. La configuration déclare la source, et un agent la référence comme un serveur MCP :

```yaml
tool_sources:
  - name: carnet
    entry_point: carnet
    params: {fichier: carnet.json}

# dans l'agent
tools:
  - source: carnet
```

Rien n'est importé tant qu'aucun agent n'utilise la source. `loom validate` liste les sources installées et leurs outils.

**La sandbox `forge`.** Loom-IA déclare lui-même une source d'outils, `forge`, adossée à une microVM Firecracker :

```yaml
tool_sources:
  - name: forge
    entry_point: forge
    params:
      vm_dir: /chemin/vers/firecracker/vms/agent-01
      catalog_dir: outils_forges     # relatif à loom.yaml
      limits: {wall_ms: 10000}
```

L'agent qui la référence voit `forge__forge` pour écrire un outil, `forge__call` pour l'appeler tout de suite, et chaque outil déjà accepté, comme `forge__total_ttc`. L'hôte se contente de lire le code reçu (syntaxe, signature) ; seule la VM l'exécute. La VM démarre au premier appel qui en a besoin et s'arrête avec l'instance.

**Ce qui se passe à chaque appel.**

1. Les arguments sont validés contre le schéma ; une erreur revient au modèle pour qu'il corrige.
2. Les politiques `before_tool` passent : droits, approbation, refus.
3. L'outil s'exécute, avec un délai de 30 secondes par défaut.
4. Les appels d'un même tour tournent en parallèle, mais leurs résultats reviennent au modèle dans l'ordre des appels.
5. Un résultat de plus de 50 000 caractères est déporté dans le stockage de fichiers. Le modèle n'en voit qu'un aperçu, et un outil `artifact_read` apparaît pour lire la suite.

**Fichiers et pièces jointes.** Les pièces jointes sont contrôlées à l'entrée : images JPEG, PNG, GIF ou WebP, reconnues par leur signature binaire, 5 Mio et 10 fichiers au plus par défaut. Elles sont rangées hors du journal, sous une adresse `artifact://<client>/<session>/<empreinte>.<ext>`. Un outil peut aussi produire des fichiers ; l'appelant les récupère dans `RunResult.artifacts`.

### Politiques, contrats et juges

**Politiques.** Une politique est une fonction branchée sur un point de la boucle, qui rend une décision :

| Point | Continuer | Remplacer | Réparer | Refuser | Pause | Arrêter | Échouer |
|---|---|---|---|---|---|---|---|
| `before_model` | ✓ | la requête | | | | ✓ | ✓ |
| `after_model` | ✓ | | ✓ | | | ✓ | ✓ |
| `before_tool` | ✓ | les arguments | | ✓ | ✓ | | ✓ |
| `after_tool` | ✓ | le résultat | ✓ | | | | ✓ |
| `on_output` | ✓ | la réponse | ✓ | | | | ✓ |

Voici, par exemple, une politique qui refuse un taux de TVA qui n'existe pas en France :

```python
from loom_ia.policies import CONTINUE, BeforeTool, Decision, Deny, PolicyContext, policy


@policy(points=["before_tool"], decisions=["deny"])
def taux_de_tva(subject: BeforeTool, context: PolicyContext) -> Decision:
    """Refuse un taux de TVA qui n'existe pas en France."""
    if subject.spec.name != "prix_ttc":
        return CONTINUE
    autorises = context.params.get("taux", [20, 10, 5.5, 2.1])
    taux = subject.arguments.get("taux_tva", 20)
    if isinstance(autorises, list) and taux not in autorises:
        return Deny(f"taux de TVA {taux} % inconnu ; taux possibles : {autorises}")
    return CONTINUE
```

Elle se branche dans l'agent :

```yaml
policies:
  - hook: taux_de_tva
    params: {taux: [20, 10, 5.5, 2.1]}
```

Le modèle reçoit le refus et son motif, et peut corriger son appel.

Les politiques s'exécutent dans l'ordre déclaré. Chacune a un délai (5 secondes par défaut) et un comportement en cas d'exception (`on_error: block` par défaut). Toute décision autre que « continuer » est écrite dans le journal, et une décision interdite à un point est une erreur de configuration, détectée au démarrage.

Loom-IA fournit `loom.require_tool`, qui impose au modèle d'appeler un outil tant qu'il n'en a appelé aucun. Les contrats, les juges et les budgets sont eux aussi des politiques fournies.

**Contrats.** Un contrat vérifie la forme d'une sortie. Il se pose sur la réponse finale de l'agent, sur un rôle ou sur un outil :

```yaml
output:
  schema:
    type: object
    properties:
      objet: {type: string, minLength: 5}
      corps: {type: string, minLength: 20}
    required: [objet, corps]
  must_not_match: "(?i)à compléter|xxx"   # expression régulière
  max_chars: 4000
  repair: {max_attempts: 1}
  on_failure: fail               # fail | unverified | fallback
```

Une sortie non conforme passe par trois étapes :

1. **La normalisation.** La sortie est d'abord nettoyée sans modèle : bloc de code retiré, JSON extrait du texte.
2. **La réparation.** Si la sortie reste non conforme, le modèle qui l'a produite reçoit sa sortie et le diagnostic, et corrige dans sa propre conversation.
3. **`on_failure`.** Si la réparation échoue, ce réglage décide : échec, sortie gardée mais marquée `unverified`, ou message de repli.

Avec un schéma, l'objet JSON validé arrive dans `RunResult.data`. Un outil, lui, n'est jamais réparé : l'orchestrateur reçoit son résultat en erreur, avec le diagnostic.

**Juges.** Un juge est un modèle qui note une sortie selon des critères écrits, entre 0 et 1 :

```yaml
judge:
  model: HAIKU
  context: [user_input, {tool_results: [chercher_devis]}]
  criteria:
    - name: fidele
      rule: >-
        Chaque montant, date et délai de l'e-mail figure dans le devis ou
        dans la demande de l'artisan : rien d'inventé.
    - name: ton
      rule: Le ton de l'e-mail est celui demandé.
      min_score: 0.6
      blocking: false
  repair: {max_attempts: 1}
  on_failure: fail
```

Un critère bloquant sous son seuil fait refuser la sortie, qui passe alors en réparation. Un critère non bloquant est seulement signalé.

C'est le code qui décide si le juge passe, jamais le modèle :

- `when` peut combiner un échantillon tiré de façon déterministe (`sample: 0.2` pour un run sur cinq), une condition Python, une liste de clients et une liste de profils ;
- l'appelant peut forcer tous les juges (`judges="force"`) ou les sauter (`judges="skip"`, refusé en profil `prod`) ;
- un juge qui ne passe pas laisse quand même une trace dans le journal, avec la raison.

Si un juge utilise le même modèle que ce qu'il juge, Loom-IA le signale ; en `prod`, c'est une erreur.

### Sessions et historique

Avec un `session_id`, les runs d'une conversation partagent un journal. Chaque run relit l'historique, qui garde sa structure (appels d'outils compris) quel que soit le fournisseur. Les tentatives refusées et le raisonnement en sont exclus. Un snapshot de l'historique, écrit à la fin des runs, évite de tout reconstruire à chaque fois.

Quand une conversation s'allonge, elle est résumée en tâche de fond, après le run, sans faire attendre l'utilisateur :

```yaml
sessions:
  compaction:
    model: HAIKU
    over_tokens: 12000         # au-delà, la session est résumée après le run
    hard_tokens: 150000        # au-delà, résumé immédiat avant le run
    keep_last: 6               # derniers échanges gardés tels quels
    fidelity_check: true
```

Le résumé ne réécrit rien : il s'ajoute au journal. Un contrôle déterministe vérifie que les références, les adresses e-mail et les nombres du passage résumé se retrouvent dans le résumé. Si l'un d'eux manque, le modèle recommence.

Pour le RGPD, une session se liste (`loom sessions list`), s'exporte (`loom sessions export <id>`, `GET /v1/sessions/{id}/events`) et se supprime (`loom sessions delete <id>`, `DELETE /v1/sessions/{id}`). La suppression emporte aussi ses fichiers et ses clés d'idempotence.

### Exécution durable

**Reprise après plantage.** Chaque événement écrit est un point de sauvegarde. `loom resume <run_id>` (ou `Loom.resume()`) reprend un run interrompu là où son journal s'est arrêté. Au démarrage d'un process, un appel à `Loom.recover()` remet en file tous les runs restés en plan.

Un outil déjà terminé n'est jamais relancé. Un outil interrompu n'est relancé que s'il est sans effet de bord ou idempotent. Dans les autres cas, on ne sait pas s'il a agi. L'outil choisit alors avec `on_unknown` : `error` (par défaut) en informe le modèle, `pause` fait vérifier un humain avant toute nouvelle tentative.

**Un seul pilote par run.** Le worker qui pilote un run prend une concession et la renouvelle tant qu'il travaille ; le bail dure 60 secondes par défaut. Si le worker meurt, la concession expire et un autre reprend le run.

**Arrière-plan.** `Loom.submit()` ou `"background": true` en REST inscrivent le run au journal avant de rendre la main : il est aussitôt suivable, interrogeable et annulable. `Loom.result(run_id)` relit ensuite ce qu'il a produit.

**Validation humaine.** Par défaut, l'approbation est asynchrone. Le run passe en `paused` et rend `pending_approvals` ; un humain tranche plus tard, en corrigeant les arguments s'il le faut, et le run reprend, sur ce process ou sur un autre. L'auteur de chaque décision est inscrit au journal. Pour les scripts et les tests, un approbateur en ligne décide dans la boucle, sans pause : `loom.run(..., approver=fonction)`.

Les demandes expirent après 24 heures par défaut. Le délai et le sort d'une demande expirée se règlent sur l'agent :

```yaml
approval: {expires_in: 3600, on_expiry: deny}   # deny | fail
```

**Idempotence.** `@idempotent` mémorise l'effet d'un outil sous une clé. Par défaut, la clé est technique : elle identifie l'appel, et protège la reprise de ce même appel. Une clé métier empêche en plus de refaire la même action depuis deux runs différents :

```python
from loom_ia.tools import idempotent, tool


@idempotent(key=lambda args: f"relance:{args['numero']}")
@tool(side_effects="irreversible", approval="always")
async def envoyer_relance(numero: str, destinataire: str, corps: str) -> str:
    """Envoie la relance d'un devis au client."""
    ...
```

Une clé métier exige un magasin partagé et durable (`sqlite`, `postgres` ou `redis`), ce que le chargement vérifie :

```yaml
storage:
  idempotency: {backend: sqlite, path: data/cles.db}
```

**Webhooks entrants.** Une porte déclarée ouvre un run en arrière-plan quand on l'appelle :

```yaml
triggers:
  - name: devis-signe                       # POST /v1/hooks/devis-signe
    agent: relance
    message: "Le devis {{ payload.devis.numero }} de {{ payload.client.nom }} vient d'être signé."
    session: "devis-{{ payload.devis.numero }}"
    delivery_header: X-Delivery-Id          # une relivraison retrouve son run au lieu d'en ouvrir un autre
```

Le client du run vient de la clé d'API de l'appelant. Loom-IA ne tient aucun cron : une tâche planifiée appelle simplement la porte à l'heure voulue.

**Workers.** Avec `storage: {queue: {backend: rabbitmq, url_env: LOOM_AMQP}}`, l'instance qui reçoit les demandes publie les tâches, et `loom worker` les exécute. Lancez-en autant que nécessaire. Un run passe d'un worker mort à un worker vivant sans intervention.

### Coûts, budgets et quotas

Loom-IA compte les tokens de chaque appel : entrée, sortie, cache, raisonnement. Il les multiplie par le tarif du modèle réellement utilisé, secours compris, avec des paliers possibles au-delà d'un certain volume d'entrée. Le coût se ventile par run, par rôle, par modèle, par session et par client. Celui d'un sous-agent remonte à son parent.

```yaml
budgets:
  run:     {max_cost: 0.05, max_tokens: 200000, max_calls: 25}
  session: {max_cost: 1.0}
  tenant:  {max_cost_per_day: 10.0, max_tokens_per_month: 20000000}
  on_exceed: stop              # warn | stop
```

Un agent surcharge ces valeurs clé par clé avec sa propre section `budget`. Un sous-agent reçoit une part de ce qui reste à son parent (`budget_share`).

Les budgets ne se contrôlent pas tous au même moment :

- **Budget de run et de session** : contrôlé avant chaque appel de l'orchestrateur. S'il est dépassé avec `stop`, le run passe en `finalizing` et produit une dernière réponse sans outils, ce qui borne le dépassement à une génération.
- **Budget de client** : contrôlé au lancement du run, sur des fenêtres calendaires (jour, mois) en UTC. Un run refusé n'écrit rien.

Deux limites de débit complètent les budgets : un quota de runs par minute pour chaque client (`quotas: {runs_per_minute: 30}`) et un débit par clé d'API. Les deux reposent sur une fenêtre glissante d'une minute. En REST, un dépassement rend un 429 avec `Retry-After`.

Le rapport de consommation se lit avec `loom report` (d'un run, d'une session avec `--session`, ou d'un client sur la journée ou le mois avec `--tenant` et `--periode`), en Python avec `Loom.report()` et `Loom.consumption()`, en REST sur `GET /v1/sessions/{id}/report` et en MCP avec l'outil `run_report`.

### Plusieurs clients et sécurité

Tant que la configuration ne déclare aucun client, tout se passe chez le client `default`. Dès qu'elle en déclare, la liste devient fermée, et un client inconnu est refusé.

```yaml
tenants:
  - id: dupont-plomberie
    variables: {entreprise: Plomberie Dupont}     # injectées dans les prompts
    secrets: {CRM_TOKEN: DUPONT_CRM_TOKEN}        # son jeton CRM, dans sa propre variable
    budgets: {tenant: {max_cost_per_day: 5.0}}
    quotas: {runs_per_minute: 30}
  - id: martin-chauffage
    variables: {entreprise: Chauffage Martin}
    models: {GLM_FLASH: HAIKU}                    # un autre modèle pour ce client
    approvals: {envoyer_email: always}
    secrets: {CRM_TOKEN: MARTIN_CRM_TOKEN}
    storage:
      events: {backend: jsonl, path: data/martin}  # son propre journal
```

Un client ne surcharge qu'une liste fermée de réglages : les agents autorisés et les outils retirés, la correspondance des modèles, les budgets et les quotas, les approbations, les secrets, les variables des prompts et le stockage. Les prompts eux-mêmes ne se surchargent pas ; seules les variables qu'ils contiennent changent d'un client à l'autre. Un client qui déclare `storage` y déclare aussi `events`, sans quoi son journal serait en mémoire et la config est refusée ; sans bloc `storage`, il partage le journal de la racine. Un serveur MCP en `scope: tenant` ouvre une connexion par client, avec ses identifiants.

**Clés d'API.** Une clé se fabrique en ligne de commande :

```bash
loom keys create app-dupont --tenant dupont-plomberie \
  --scope run --scope read --scope read_content --scope approve --expires 90j
```

La clé s'affiche une seule fois. La configuration n'en garde que l'empreinte :

```yaml
security:
  api_keys:
    - id: app-dupont
      tenant: dupont-plomberie
      hash: sha256:…
      scopes: [run, read, read_content, approve]
      agents: [relance]
      rate_limit: {per_minute: 60}
      expires: 2027-01-08T00:00:00Z
```

Le `sha256:…` du modèle est à remplacer par l'empreinte que la commande imprime : `sha256:` suivi de 64 chiffres hexadécimaux minuscules. Toute autre forme, le modèle recopié tel quel compris, est refusée au chargement.

Une requête présente sa clé dans `Authorization: Bearer lk_…` ou dans `X-API-Key`. La clé détermine le client, et rien dans le corps de la requête ne peut le changer.

| Portée | Autorise |
|---|---|
| `run` | Lancer et arrêter un run |
| `read` | Lire les agents, statuts, événements et rapports, sans le contenu |
| `read_content` | Lire aussi le contenu : demandes, réponses, arguments |
| `approve` | Trancher une demande d'approbation (avec `read_content`, pour voir ce qu'on approuve) |
| `admin` | Effacer une session, lancer un run sans ses juges |

**Isolation des données.** Avec un journal Postgres, une politique de sécurité au niveau des lignes filtre chaque requête sur le client courant. L'application tourne sous un rôle sans droit de modification sur le journal. `loom storage sql` imprime le schéma à faire appliquer par un DBA, si le rôle ne peut pas le créer lui-même.

**Chiffrement.** `storage: {encryption: {keys: [LOOM_JOURNAL_KEY]}}` scelle en AES-256-GCM le contenu de chaque événement et de chaque fichier. Il faut l'extra `crypto`. Chaque client redirige ce nom de secret vers sa propre clé : effacer celle d'un client rend son journal illisible pour de bon, sans toucher aux autres. Les sessions restent listables et supprimables même sans la clé.

**Rétention.** `storage: {retention: {events_days: 365}}` fixe la durée de conservation ; chaque client peut avoir la sienne. `loom retention` liste les sessions restées sans activité au-delà de cette durée. L'option `--yes` les efface.

**MCP en HTTP.** Avec `server: {mcp: {http: true}}`, le serveur MCP est monté dans l'application REST, sous `/mcp`. Il exige une clé à chaque requête et applique les mêmes portées qu'en REST ; les en-têtes `Origin` et `Host` sont validés. Un seul serveur sert ainsi tous les clients.

### Observabilité, rejeu et tests

**Traces.** Chaque run se lit comme un arbre : le run, ses étapes, les appels de modèles, les outils, les validations, les sous-agents. Chaque élément porte sa durée, son statut et son coût. On y accède de plusieurs façons :

- en ligne de commande : `loom inspect <run_id>` affiche l'arbre lisible (`--full` pour tout voir, `--json` pour la version brute) ;
- en Python : `Loom.trace(run_id)` ;
- en REST : `GET /v1/traces/{run_id}` ;
- en MCP : la ressource `loom://traces/{run_id}`.

L'API rend la même trace, sans le contenu si la clé n'a pas `read_content`.

**OpenTelemetry.** Les traces s'exportent vers n'importe quel collecteur OTLP, à la clôture de chaque run, avec les conventions GenAI d'OpenTelemetry :

```yaml
telemetry:
  capture: {exports: metadata}             # metadata | content
  redaction:
    patterns: [email, phone, iban, {name: devis, regex: "D-\\d{4}-\\d{3}"}]
  exporters:
    - type: otel
      endpoint_env: OTEL_EXPORTER_OTLP_ENDPOINT
      protocol: http/protobuf
```

En `metadata` (par défaut), seuls la structure, les durées, l'usage et les coûts quittent la machine. En `content`, le contenu sort aussi, masqué selon les motifs déclarés. Pour le débogage, `capture: {raw_exchanges: true}` ajoute au journal chaque échange HTTP avec le fournisseur. Les secrets et les octets des fichiers en sont retirés.

**Rejeu.** Un run se rejoue de deux façons :

- **À l'identique**, avec `loom replay <run_id>` ou `Loom.replay()`. Le rejeu se fait en mémoire, sans aucun appel réseau : les réponses des modèles et les résultats des outils sont lus dans le journal, et la logique de Loom-IA tourne avec la configuration d'aujourd'hui. Si quelque chose a changé (un prompt, un outil, une politique), le rejeu s'arrête sur la première divergence et dit quelle partie de la requête a bougé.
- **En variante**, avec `--mode variant`. Le rejeu peut alors utiliser un autre modèle par étape (`--model main=HAIKU`) ou une autre configuration. Ce que le journal connaît est servi ; le reste part pour de vrai. Un outil à effet de bord n'est jamais réexécuté : il est lu dans le journal, remplacé par une doublure (`--double`) ou refusé.

Les codes de sortie sont 0, 1 ou 2, selon que le run se rejoue, diverge ou ne peut pas être rejoué.

**Évaluations.** Une suite décrit des cas et ce qu'on en attend :

```yaml
version: 1
config: loom.yaml
agent: devis
repeat: 2
variants:
  - {name: actuel}
  - {name: haiku, models: {main: HAIKU}}
cases:
  - name: tva-reduite
    input: Quel prix TTC pour 1 250 € HT en rénovation (TVA à 10 %) ?
    expect:
      status: completed
      contains: ["1 375"]
      called:
        - {name: prix_ttc, arguments: {montant_ht: 1250, taux_tva: 10}}
```

`loom eval suite.yaml` joue chaque cas pour chaque variante, dans un journal temporaire, et rend un rapport qui compare les variantes cas par cas. Les outils à effet de bord y sont doublés ou refusés, jamais exécutés. Une section `judge` ajoute des critères notés par un modèle, et `max_cost_usd` plafonne la dépense de la suite.

**Non-régression.** Un journal exporté (`loom sessions export`) devient un test. `loom replay --journal fichier.jsonl` le rejoue avec la configuration d'aujourd'hui. Un cas `replay:` fait de même dans une suite d'évaluations, et `loom_ia.testing.assert_replays()` dans un test pytest.

**Kit de test.** `Bench` met un agent au banc, isolé de ses données réelles :

```python
from loom_ia.testing import Bench


async def test_le_ttc_passe_par_l_outil() -> None:
    async with Bench("loom.yaml") as banc:
        result = await banc.run("devis", "Quel prix TTC pour 1 250 € HT à 10 % ?")
        banc.expect(
            result,
            status="completed",
            contains=["1 375"],
            called=[{"name": "prix_ttc", "arguments": {"montant_ht": 1250, "taux_tva": 10}}],
        )
```

Les modèles s'y remplacent par des `ScriptedModel`, les outils par des doublures. Un modèle réel non remplacé est refusé, sauf `real_models=True`, pour qu'un test ne dépense rien par mégarde, et un outil à effet de bord sans doublure n'est jamais exécuté.

**Logs.** Les logs techniques restent séparés des traces. En niveau `INFO`, Loom-IA écrit une ligne par appel de modèle et d'outil, sans aucun contenu. Chaque ligne porte `run_id`, `span_id` et `tenant_id`, au format console ou JSON.

### Les accès en détail

**API Python** (`loom_ia.access.Loom`) :

| Méthode | Rôle |
|---|---|
| `Loom.from_config(chemin)`, `Loom(config)` | Charger une configuration |
| `run()`, `stream()`, `submit()` | Lancer un run : attendre, suivre en direct, ou mettre en arrière-plan |
| `result()`, `state()`, `events()`, `follow()` | Relire un run, suivre son journal |
| `cancel()`, `resume()`, `recover()` | Arrêter, reprendre, remettre en file les runs en plan |
| `approve()`, `reject()` | Trancher une demande d'approbation |
| `session()`, `sessions()`, `export_session()`, `delete_session()` | Gérer les conversations |
| `runs()`, `query()`, `trace()` | Lister les runs, chercher dans le journal, lire une trace |
| `report()`, `consumption()` | Consommation d'un run, d'une session, d'un client |
| `replay()`, `replay_journal()`, `evaluate()` | Rejeu et évaluations |
| `trigger()` | Ouvrir un run par une porte déclarée |
| `register()` | Rendre un objet Python référençable depuis la configuration |

**API REST** (`loom serve`, ou `create_app(loom)` de `loom_ia.access.http` pour l'intégrer à une application FastAPI existante) :

| Méthode | Route | Rôle | Portée |
|---|---|---|---|
| `GET` | `/v1/agents` | Lister les agents | `read` |
| `POST` | `/v1/agents/{name}/runs` | Lancer un run (JSON ou multipart avec pièces jointes) | `run` |
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

Le document OpenAPI est généré, ce qui permet de générer le client d'une interface.

**Serveur MCP** (`loom mcp` en stdio, ou en HTTP dans l'application de `loom serve`) :

- chaque agent publié devient un outil, qui prend un `message`, un `session_id` et des pièces jointes : image en base64, lien `artifact://`, ou lien `file://` sous les dossiers autorisés par `server.mcp.file_roots` ;
- trois outils de contrôle s'y ajoutent : `run_status`, `run_report` et `cancel` ;
- le journal se lit en ressources `loom://` : runs, sessions, événements, traces, fichiers ;
- si le client fournit un jeton de progression, le déroulé du run lui arrive en notifications.

En stdio, un serveur sert un seul client, choisi avec `--tenant`.

`expose: {rest: false, mcp: true}` choisit, agent par agent, sur quels accès il est publié.

**Ligne de commande** :

| Commande | Rôle |
|---|---|
| `loom validate` | Vérifier la configuration et monter les agents |
| `loom run <agent> "<message>"` | Lancer un run (`--stream`, `--session`, `--attach`, `--json`) |
| `loom resume <run_id>` | Reprendre un run interrompu |
| `loom approve` · `loom reject <run_id>` | Trancher une demande d'approbation |
| `loom inspect <run_id>` | Afficher l'arbre d'un run |
| `loom report` | Consommation d'un run, d'une session ou d'un client |
| `loom replay <run_id>` | Rejouer un run, à l'identique ou en variante |
| `loom eval <suite.yaml>` | Jouer une suite d'évaluations |
| `loom serve` | Servir l'API REST (`--reload` en développement) |
| `loom mcp` | Servir les agents en MCP sur stdio |
| `loom worker` | Consommer la file de tâches |
| `loom sessions list` · `export` · `delete` | Gérer les sessions |
| `loom keys create <id>` | Fabriquer une clé d'API |
| `loom retention` | Effacer les sessions trop anciennes |
| `loom storage sql` | Imprimer le SQL du stockage Postgres |
| `loom schema` | Imprimer le JSON Schema de la configuration |

Les options globales `--config` (par défaut `loom.yaml`) et `--profile` se placent avant la commande.

### Les stockages

| Stockage | Backends | Par défaut |
|---|---|---|
| Journal (`storage.events`) | `memory`, `jsonl`, `sqlite`, `postgres` | `memory` |
| Fichiers (`storage.artifacts`) | `local`, `memory` | suit le journal |
| Idempotence (`storage.idempotency`) | `journal`, `memory`, `sqlite`, `postgres`, `redis` | `journal` |
| File de tâches (`storage.queue`) | `asyncio`, `rabbitmq` | `asyncio` |
| Bus entre process (`storage.bus`) | `memory`, `postgres`, `redis` | `memory` |

Pour une base de données, la configuration ne contient que le nom de la variable qui porte l'adresse de connexion (`dsn_env`, `url_env`).

Quelques repères pour choisir :

- **En local**, un journal JSONL suffit. Les fichiers sont alors rangés à côté du journal, dans `.artifacts`.
- **En service**, Postgres tient le journal et l'idempotence, Redis ou Postgres le bus, RabbitMQ la file.
- **Avec un journal en mémoire**, rien ne survit au process. Les agents qui peuvent se mettre en pause sont refusés, sauf en profil `dev` : outils MCP et de paquets compris, d'après ce que la config en déclare, et sur le journal du client pour qui l'agent est monté.

---

## Développer et tester

```bash
git clone https://github.com/denislamard/loom_v2.git
cd loom_v2
uv sync --all-extras --group types
```

Les contrôles sont ceux de la CI :

```bash
uv run pytest                    # la suite de tests
uv run ruff check .              # lint
uv run ruff format --check .     # format
uv run pyright                   # typage strict
uv run lint-imports              # règles de dépendances entre couches
```

Les tests qui demandent un service réel (Postgres, RabbitMQ, Redis) lisent son adresse dans une variable d'environnement, et sont sautés s'il n'est pas là :

```bash
export LOOM_TEST_POSTGRES=postgresql://loom:loom@127.0.0.1:5432/loom
export LOOM_TEST_RABBITMQ=amqp://loom:loom@127.0.0.1:5672/
export LOOM_TEST_REDIS=redis://127.0.0.1:6379/1
uv run pytest --require-services
```

Avec `--require-services`, un service absent fait échouer le test au lieu de le sauter, pour qu'une suite verte prouve vraiment quelque chose. Le rôle Postgres ne doit pas être superutilisateur, puisqu'un superutilisateur contourne la sécurité au niveau des lignes. Il lui faut en revanche le droit `CREATEROLE`, car le stockage crée son rôle applicatif à la première requête.

La CI, sur GitHub Actions, compte cinq jobs :

1. la qualité : ruff, pyright et les contrats d'import ;
2. les tests du noyau seul, sans aucun extra, pour vérifier que le noyau s'importe sans SDK ;
3. les tests avec tous les extras, Postgres 16, RabbitMQ 3.12 et Redis 7 en conteneurs, et la couverture ;
4. les mêmes tests avec chaque dépendance directe à la plus basse version que `pyproject.toml` autorise, pour que les bornes déclarées soient vraies ;
5. la construction du paquet.

Un workflow à part rejoue chaque semaine les tests avec les versions les plus récentes permises. La publication (étiquette `v*`) attend la CI complète.

Les exemples de `examples/` servent aussi de recette : la plupart tournent en simulé, et presque tous acceptent `--reel` pour un passage avec de vrais modèles.

---

## Licence

Loom-IA est distribué sous licence [Apache 2.0](https://github.com/denislamard/loom_v2/blob/main/LICENSE).
