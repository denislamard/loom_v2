# loom-chat

Console de chat dans le terminal (Textual) pour essayer [loom-ia](https://pypi.org/project/loom-ia/) en quelques minutes : une page, des sessions, des runs, et les demandes de l'agent à l'utilisateur (approbation d'un outil sensible, question du modèle).

## Installation

Python 3.14 est requis.

```bash
uv tool install loom-chat
```

## Lancement

```bash
cd mon-agent
loom-chat
```

Lancez la console depuis le dossier de votre agent. Elle y lit la config loom comme la commande `loom` : l'amorce `loom-boot.yaml`, la config qu'elle désigne (`loom.yaml`), les agents de `agents/` et leurs prompts. Ce sont des fichiers loom ordinaires : modifiez-les à votre guise, la console les relit à chaque lancement.

Dans un dossier sans `loom-boot.yaml`, la console propose de créer un agent prêt à l'emploi (après accord) :

| Fichier | Contenu |
|---|---|
| `loom-boot.yaml` | L'amorce : où lire la config. |
| `loom.yaml` | Le modèle, le journal (`data/`), l'utilisateur `local` et son rôle. |
| `agents/assistant.yaml` | L'agent : modèle, prompt, outils (heure, lecture d'une page avec approbation, question à l'utilisateur). |
| `prompts/system.md` | Le prompt système. |

Le modèle créé est Claude (`claude-haiku-5-5`) si `ANTHROPIC_API_KEY` est définie, chaque message étant alors facturé ; sinon un modèle factice qui joue une démonstration sans clé ni frais.

| Option | Rôle |
|---|---|
| `--env-file CHEMIN` | Fichier `.env` à lire. Sans l'option, celui que désigne l'amorce, ou le `.env` du dossier. Une variable déjà définie n'est jamais remplacée. |
| `--user ID` | L'utilisateur de la config au nom duquel agir ; sans l'option, le seul déclaré. Il lui faut `run`, `answer`, `approve` et `self_approve` pour tout faire depuis la console. |
| `--model fake\|anthropic` | Le modèle des fichiers créés au premier lancement. |

### Avec une clé OpenAI

La console crée un agent Claude ou la démo ; pour un modèle OpenAI, trois changements, une fois les fichiers créés :

1. Installer l'adaptateur OpenAI de loom-ia, que loom-chat n'embarque pas :

   ```bash
   uv tool install loom-chat --with "loom-ia[openai]"
   ```

2. Mettre la clé dans le `.env` du dossier de l'agent :

   ```
   OPENAI_API_KEY=sk-…
   ```

3. Dans `loom.yaml`, remplacer le modèle `model` (garder son `id`, que l'agent cite dans `agents/assistant.yaml`) :

   ```yaml
   models:
   - id: model
     sdk: openai
     model: <nom du modèle chez OpenAI>
     api_key_env: OPENAI_API_KEY
     max_tokens: 2048
     pricing: { input: <prix>, output: <prix> }   # dollars par million de tokens ; sans lui, le coût affiché reste à 0
   ```

Retirez `cache:` s'il y en a un : les points de cache sont propres à Anthropic, et refusés avec `sdk: openai`.

### Avec une clé Anthropic

C'est le modèle créé quand `ANTHROPIC_API_KEY` est définie au premier lancement ; pour passer de la démo à Claude, trois changements :

1. Rien à installer : l'adaptateur Anthropic de loom-ia est inclus dans loom-chat.

2. Mettre la clé dans le `.env` du dossier de l'agent :

   ```
   ANTHROPIC_API_KEY=sk-ant-…
   ```

3. Dans `loom.yaml`, remplacer le modèle `model` (garder son `id`, que l'agent cite dans `agents/assistant.yaml`) :

   ```yaml
   models:
   - id: model
     sdk: anthropic
     model: claude-haiku-5-5
     api_key_env: ANTHROPIC_API_KEY
     max_tokens: 2048
     pricing: { input: 0.1, output: 0.5, cache_read: 0.01, cache_write: 0.125 }   # dollars par million de tokens
     cache: { system: true, tools: true, messages: true }                         # facultatif : cache de prompt Anthropic
   ```

### Avec un serveur à une URL

Un serveur compatible OpenAI (vLLM, Ollama, LM Studio, un fournisseur hébergé…) passe par l'adaptateur OpenAI, avec l'adresse du serveur ; trois changements :

1. Installer l'adaptateur OpenAI de loom-ia, que loom-chat n'embarque pas :

   ```bash
   uv tool install loom-chat --with "loom-ia[openai]"
   ```

2. Si le serveur demande une clé, la mettre dans le `.env` du dossier de l'agent, sous le nom de votre choix ; sinon, rien à faire :

   ```
   LLM_API_KEY=…
   ```

3. Dans `loom.yaml`, remplacer le modèle `model` (garder son `id`, que l'agent cite dans `agents/assistant.yaml`) :

   ```yaml
   models:
   - id: model
     sdk: openai
     model: <nom du modèle sur le serveur>
     base_url: http://localhost:11434/v1   # l'adresse du serveur, jusqu'au /v1
     api_key_env: LLM_API_KEY              # à retirer si le serveur ne demande pas de clé
     max_tokens: 2048
   ```

L'agent a des outils : le modèle servi doit savoir les appeler. Sans `pricing`, le coût affiché reste à 0. Les variables `OPENAI_BASE_URL` et `ANTHROPIC_BASE_URL` ne sont jamais lues : seule la config dit où partent les requêtes.

Avec plusieurs agents dans `agents/`, la console demande au démarrage avec lequel parler (Ctrl+A pour en changer : une nouvelle session s'ouvre avec lui).

## Raccourcis

| Touche | Action |
|---|---|
| Entrée | Envoyer le message ; rouvre la question à laquelle vous pouvez répondre, ou l'approbation que vous pouvez trancher |
| Ctrl+N | Nouvelle session |
| Ctrl+A | Choisir l'agent |
| Ctrl+X | Annuler le run en cours |
| Ctrl+R | Reprendre le dernier run interrompu |
| Échap (question) | Répondre plus tard : la question reste ouverte |
| Ctrl+Q | Quitter |

Le journal fait foi : une session fermée en plein run se rouvre telle quelle au lancement suivant, avec ses demandes en attente.

La fenêtre d'approbation ne s'ouvre que pour une demande que l'utilisateur peut trancher (`approve`, et `self_approve` pour ses propres runs). Les autres restent affichées « en attente de la décision d'un approbateur habilité » : elles se tranchent ailleurs (console d'administration, `loom approve`), et la session reste ouverte à de nouveaux messages pendant ce temps. La fenêtre se ferme aussi quand une demande est tranchée ailleurs pendant qu'elle est ouverte.

De même, la fenêtre d'une question ne s'ouvre que si l'utilisateur peut y répondre (`answer` sur l'agent qui l'a posée). Sinon, la question est affichée « en attente de la réponse d'un utilisateur habilité », et le texte tapé part comme un nouveau message au lieu de lui répondre.

## Licence

Apache-2.0.
