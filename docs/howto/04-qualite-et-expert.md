# Loom-IA : guide d'apprentissage

# Niveau 5 : Expert — observabilité, qualité, sandbox

> **Dans ce fichier** : relire ce qu'un run a fait (traces, journal, OpenTelemetry), le rejouer, le noter, le tester, brancher des sources d'outils (paquet Python, sandbox `forge`, mémoire `loom-notes`), puis assembler le tout dans un projet de synthèse (chapitres 25 à 30), suivis des annexes de référence.
>
> **Navigation** : précédent : [03-production.md](03-production.md) · [04-qualite-et-expert.md](04-qualite-et-expert.md) (vous êtes ici)

Le niveau 4 a mis l'agent en service : plusieurs clients, une API, un journal durable, des budgets. Ce dernier fichier répond à la question qui vient ensuite : comment savoir, une semaine plus tard, ce que l'agent a vraiment fait, et comment être sûr qu'une modification de prompt ou de modèle ne l'a pas dégradé ? Il suppose les chapitres 1 à 24 lus, en particulier les approbations (chapitre 9), les juges (chapitre 14), l'idempotence (chapitre 19) et les clients (chapitre 22).

---

## Sommaire

- [Avant de commencer](#avant-de-commencer)
- [25. Traces et observabilité](#25-traces-et-observabilité)
- [26. Rejouer un run](#26-rejouer-un-run)
- [27. Évaluer un agent](#27-évaluer-un-agent)
- [28. Tests et non-régression](#28-tests-et-non-régression)
- [29. Sources d'outils : paquet, sandbox `forge`, mémoire `loom-notes`](#29-sources-doutils--paquet-sandbox-forge-mémoire-loom-notes)
- [30. Projet de synthèse : `relance/`](#30-projet-de-synthèse--relance)
- [Annexe A. Référence de la ligne de commande](#annexe-a-référence-de-la-ligne-de-commande)
- [Annexe B. Routes de l'API REST](#annexe-b-routes-de-lapi-rest)
- [Annexe C. Matrice de couverture des fonctions](#annexe-c-matrice-de-couverture-des-fonctions)
- [Annexe D. Pièges et dépannage](#annexe-d-pièges-et-dépannage)
- [Annexe E. Glossaire](#annexe-e-glossaire)
- [Pour conclure](#pour-conclure)

---

## Avant de commencer

### Un projet neuf pour les chapitres 25 à 29

Les chapitres 25 à 29 ne reprennent pas `mon-agent/` : ils travaillent dans un projet à part, `qualite/`, qui ne contient que ce dont ils ont besoin (un agent de relance, ses deux outils, un modèle simulé). Cela évite d'hériter des agents et des modèles de vos essais précédents, et chaque commande de ce fichier se lance donc depuis `qualite/`. Le chapitre 29 y ajoute un paquet voisin, `carnet-devis/`, et le chapitre 30 repart d'un projet entièrement neuf, `relance/`.

### Ce qui a été exécuté, et ce qui ne l'a pas été

Comme dans les fichiers précédents, les sorties sont réelles : `loom-ia` **2.0.0** (PyPI), Linux, Python 3.14.6, modèles simulés (`sdk: fake`). Les identifiants de run (`01a1…`) et les durées en millisecondes changeront chez vous ; les chemins absolus sont abrégés en `/home/denis/…`. Ce qui demande une ressource absente de la machine d'essai est marqué **« Non exécuté ici »**, avec la raison. Dans ce fichier :

| Sujet | État |
|---|---|
| Export OTLP | Exécuté vers un mini collecteur local ; **non exécuté** vers un vrai collecteur (Jaeger, Grafana Tempo, Datadog…) |
| Sandbox `forge` | Contrôles de l'hôte et erreur de configuration exécutés ; **non exécuté** dans une micro-VM (la machine d'essai n'a ni `/dev/kvm` ni `sudo`) |
| `loom-notes` | Exécuté avec ses modèles factices ; **non exécuté** avec les vrais modèles d'embedding (plusieurs Go de dépendances) |
| Vrais modèles Anthropic ou OpenAI | **Non exécuté** : aucune clé d'API. Tout tourne avec `sdk: fake` |
| Postgres, Redis, RabbitMQ | **Non exécuté** : aucun service. SQLite et fichiers JSONL à la place |

---

## 25. Traces et observabilité

Chaque fait d'un run est écrit dans le journal au moment où il se produit. Tout ce chapitre consiste à relire ce journal sous quatre angles : un arbre lisible par un humain (`loom inspect`), des appels programmables (Python, REST, MCP), un export vers un outil de supervision (OpenTelemetry) et des logs d'exploitation.

### Exemple 25.1 : lire un run avec `loom inspect`

#### Pourquoi

Mme Martin affirme n'avoir jamais reçu la relance du devis D-2026-042. Jean Dupont ne se souvient plus de l'avoir validée. Avant de lui répondre, il veut savoir ce que l'agent a fait, dans quel ordre, qui a approuvé l'envoi et combien cela a coûté.

#### Objectif

Créer le projet `qualite/`, lancer une relance, l'approuver, puis la relire avec `loom inspect` sous trois formes : l'arbre, le JSON, et le détail complet.

#### Mise en place

```bash
uv init --bare --python 3.14 qualite
cd qualite
uv add "loom-ia[http,mcp,sqlite]"
uv add --dev pytest pytest-asyncio
mkdir agents prompts
```

`--bare` crée un `pyproject.toml` sans fichier d'exemple. Les trois extras couvrent ce fichier : `http` pour l'API REST, `mcp` pour le serveur MCP, `sqlite` pour le magasin des chapitres suivants. Les dépendances de développement servent au chapitre 28.

Créez `outils.py`. Il contient le carnet de devis, deux outils et une doublure de l'envoi (utile aux chapitres 26 à 28) :

```python
from datetime import datetime
from pathlib import Path

from loom_ia.tools import tool

CARNET = {
    "D-2026-042": {
        "client": "Mme Martin",
        "email": "mme.martin@example.fr",
        "objet": "Remplacement du chauffe-eau (200 L)",
        "montant_ttc": 1840.0,
        "envoye_le": "2026-09-02",
        "statut": "en attente",
    },
}


@tool
def chercher_devis(numero: str) -> dict[str, str | float]:
    """Cherche un devis de la Plomberie Dupont par son numéro (ex. D-2026-042)."""
    devis = CARNET.get(numero)
    if devis is None:
        raise ValueError(f"Devis {numero} introuvable")
    return {"numero": numero, **devis}


@tool(side_effects="irreversible", approval="always")
def envoyer_email(destinataire: str, objet: str, corps: str) -> str:
    """Envoie un e-mail au client. Action définitive : demande l'accord de l'artisan."""
    ligne = f"{datetime.now():%Y-%m-%d %H:%M:%S} à {destinataire} : {objet}\n"
    with Path("boite_envoi.txt").open("a", encoding="utf-8") as boite:
        boite.write(ligne)
    return f"E-mail envoyé à {destinataire}"


def faux_envoi(destinataire: str, objet: str, corps: str) -> str:
    """Doublure de envoyer_email : rien ne part, la réponse ressemble à la vraie."""
    return f"[doublure] E-mail simulé à {destinataire}"
```

Créez `loom.yaml`. Le modèle simulé joue la relance en trois temps : il cherche le devis, envoie l'e-mail, confirme. Le bloc `pricing` donne un prix à ses tokens, pour que les coûts affichés ne soient pas nuls :

```yaml
version: 1

imports: [outils]
agents_dir: agents/
prompts_dir: prompts/

models:
  - id: SIMULE_RELANCE
    sdk: fake
    model: fake-relance
    pricing: {input: 3.0, output: 15.0}
    params:
      script:
        - text: Je cherche le devis.
          tool_calls:
            - name: chercher_devis
              arguments: {numero: D-2026-042}
        - text: J'envoie la relance.
          tool_calls:
            - name: envoyer_email
              arguments:
                destinataire: mme.martin@example.fr
                objet: Votre devis D-2026-042
                corps: >-
                  Bonjour Mme Martin, nous revenons vers vous au sujet du devis D-2026-042
                  (remplacement du chauffe-eau, 1 840 € TTC) envoyé le 2 septembre.
                  Restons à votre disposition. La Plomberie Dupont
        - text: La relance du devis D-2026-042 (1 840 € TTC) est partie chez Mme Martin.

telemetry:
  logging: {level: WARNING}

storage:
  events: {backend: jsonl, path: data}
```

Créez `agents/relance_simple.yaml` :

```yaml
name: relance_simple
description: Relance un client dont le devis est resté sans réponse.

main:
  model: SIMULE_RELANCE
  system_file: relance_simple.md

max_iterations: 6

tools:
  - python: chercher_devis
  - python: envoyer_email
```

Créez `prompts/relance_simple.md` :

```markdown
Tu es l'assistant de la Plomberie Dupont. Tu relances les clients dont le devis est resté sans réponse.

1. Cherche le devis avec `chercher_devis`.
2. Envoie la relance avec `envoyer_email`, en reprenant le montant et la date du devis, sans rien inventer.
3. Confirme à l'artisan, en une phrase, ce qui est parti.
```

#### Exécution

```bash
uv run loom validate
```

```text
Config     : loom.yaml
Profil     : aucun (ni --profile, ni LOOM_PROFILE, ni 'profile:') ; surcharges : aucune
Modèles    : SIMULE_RELANCE
Agents     : relance_simple
Outils     : chercher_devis, envoyer_email
Paquets    : forge (loom-ia 2.0.0) (groupe loom_ia.tools)
Journal    : jsonl (/home/denis/qualite/data)
Artefacts  : local (/home/denis/qualite/data/.artifacts)
Idempotence: journal
File       : asyncio
Bus        : memory (les nouvelles ne sortent pas de ce process)
Chiffrement: aucun (contenus en clair au repos)
Rétention  : aucune (rien ne s'efface)
Clés d'API : aucune (API REST ouverte)
  relance_simple : modèle SIMULE_RELANCE, 2 outil(s) Python

1 agent(s) monté(s) sans erreur.
```

Lancez la relance. Elle s'arrête sur l'approbation de `envoyer_email`, déclaré `approval: always` :

```bash
uv run loom run relance_simple "Relance Mme Martin pour le devis D-2026-042, sur un ton cordial."
```

```text
—

Statut     : paused · itérations : 2 · tokens : 1138/194 · coût : 0.0063 $
Run        : 01a1267b-1639-73c5-bfe4-6a953e9ff10e
En attente : envoyer_email (fake_1_0) — loom approve 01a1267b-1639-73c5-bfe4-6a953e9ff10e --call fake_1_0
```

La commande sort avec le code 1 : un run `paused` n'est pas un succès. Jean Dupont approuve, ce qui reprend le run :

```bash
uv run loom approve 01a1267b-1639-73c5-bfe4-6a953e9ff10e --by "Jean Dupont"
```

```text
Accordé : fake_1_0
La relance du devis D-2026-042 (1 840 € TTC) est partie chez Mme Martin.

Statut     : completed · itérations : 3 · tokens : 2033/237 · coût : 0.0097 $
Run        : 01a1267b-1639-73c5-bfe4-6a953e9ff10e
```

Le texte de la réponse sort sur la sortie standard, le statut et l'identifiant du run sur la sortie d'erreur. Relisez maintenant le run :

```bash
uv run loom inspect 01a1267b-1639-73c5-bfe4-6a953e9ff10e
```

```text
Run        : 01a1267b-1639-73c5-bfe4-6a953e9ff10e (agent relance_simple, client default)
Session    : 01a1267b-1639-73c5-bfe4-6a953e9ff10e
Statut     : completed, 3 itération(s)
Usage      : 2033 → 237 tokens, 0.009654 $, 25 ms de pilotage

run relance_simple — completed, 3.9 s, 0.009654 $
  étape 1
    modèle fake-relance (main) — 1 ms, 453 → 66 tokens, 0.002349 $
      · répond    : Je cherche le devis.
      · appelle   : chercher_devis({"numero": "D-2026-042"})
  étape 2
    outil chercher_devis — 2 ms
      · arguments : {"numero": "D-2026-042"}
      · résultat  : {"numero": "D-2026-042", "client": "Mme Martin", "email": "mme.martin@example.fr", "objet": "Remp…
  étape 3
    modèle fake-relance (main) — 1 ms, 685 → 128 tokens, 0.003975 $
      · répond    : J'envoie la relance.
      · appelle   : envoyer_email({"destinataire": "mme.martin@example.fr", "objet": "Votre devis D-2026-042", "corps…
  étape 4
    approbation pour envoyer_email — accordée par Jean Dupont
  étape 5
    outil envoyer_email — 2 ms
      · arguments : {"destinataire": "mme.martin@example.fr", "objet": "Votre devis D-2026-042", "corps": "Bonjour Mm…
      · résultat  : E-mail envoyé à mme.martin@example.fr
  étape 6
    modèle fake-relance (main) — 1 ms, 895 → 43 tokens, 0.003330 $
      · répond    : La relance du devis D-2026-042 (1 840 € TTC) est partie chez Mme Martin.

Réponse finale :
  La relance du devis D-2026-042 (1 840 € TTC) est partie chez Mme Martin.
Bilan      : 3 appel(s) de modèle, 2 appel(s) d'outil, 1 approbation(s)
```

La réponse à Mme Martin est là : l'e-mail est parti à l'étape 5, après l'approbation de Jean Dupont à l'étape 4. Avec `--json`, la même trace est un objet que l'on peut traiter par programme. Voici son début :

```bash
uv run loom inspect 01a1267b-1639-73c5-bfe4-6a953e9ff10e --json | head -48
```

```json
{
  "run_id": "01a1267b-1639-73c5-bfe4-6a953e9ff10e",
  "session_id": "01a1267b-1639-73c5-bfe4-6a953e9ff10e",
  "tenant_id": "default",
  "trace_id": "01a1267b-1639-73c5-bfe4-6a953e9ff10e",
  "agent": "relance_simple",
  "status": "completed",
  "finished": true,
  "iterations": 3,
  "usage": {
    "input_tokens": 2033,
    "output_tokens": 237,
    "cache_read_tokens": 0,
    "cache_write_tokens": 0,
    "reasoning_tokens": 0
  },
  "cost_usd": 0.009654,
  "active_ms": 24.61363100019298,
  "output": "La relance du devis D-2026-042 (1 840 € TTC) est partie chez Mme Martin.",
  "error_type": null,
  "content": true,
  "spans": [
    {
      "span_id": "01a1267b-1644-7610-b429-4103d8b75df7",
      "parent_span_id": null,
      "run_id": "01a1267b-1639-73c5-bfe4-6a953e9ff10e",
      "name": "invoke_agent relance_simple",
      "kind": "run",
      "start": "2026-10-10T15:42:43.271757Z",
      "end": "2026-10-10T15:42:47.191127Z",
      "duration_ms": 3919.37,
      "open": false,
      "error": null,
      "attributes": {
        "loom.tenant_id": "default",
        "loom.session_id": "01a1267b-1639-73c5-bfe4-6a953e9ff10e",
        "loom.run_id": "01a1267b-1639-73c5-bfe4-6a953e9ff10e",
        "gen_ai.agent.name": "relance_simple",
        "gen_ai.operation.name": "invoke_agent",
        "loom.run.kind": "normal",
        "loom.run.depth": 0,
        "loom.run.status": "completed",
        "loom.iterations": 3,
        "loom.cost_usd": 0.009654,
        "gen_ai.usage.input_tokens": 2033,
        "gen_ai.usage.output_tokens": 237,
        "loom.usage.cache_read_tokens": 0,
        "loom.usage.cache_write_tokens": 0,
```

La suite du JSON liste les `spans` (une entrée par span, avec ses attributs `gen_ai.*` et `loom.*`). Enfin, l'arbre abrège les arguments et les résultats longs à une centaine de caractères. `--full` les affiche en entier :

```bash
uv run loom inspect 01a1267b-1639-73c5-bfe4-6a953e9ff10e --full | grep -A1 "outil envoyer_email"
```

```text
    outil envoyer_email — 2 ms
      · arguments : {"destinataire": "mme.martin@example.fr", "objet": "Votre devis D-2026-042", "corps": "Bonjour Mme Martin, nous revenons vers vous au sujet du devis D-2026-042 (remplacement du chauffe-eau, 1 840 € TTC) envoyé le 2 septembre. Restons à votre disposition. La Plomberie Dupont"}
```

#### À retenir

- `loom inspect <run_id>` reconstruit l'arbre depuis le journal : étapes, appels de modèle (avec tokens et coût), appels d'outil, approbations, verdicts de juges. Il ne rappelle ni le modèle ni les outils.
- Les spans sont de quatre sortes : `run` (`invoke_agent <agent>`), `step` (`step N`), `chat` (`chat <modèle>`) et `tool` (`execute_tool <outil>`). Un événement sans équivalent, comme `approval.requested`, apparaît sous son propre nom.
- Les durées sont celles du temps réel. Ici le run « dure » 3,9 s alors que le pilotage n'a pris que 25 ms (ligne `Usage`) : le reste est l'attente de l'approbation. Pour mesurer ce que Loom-IA consomme, lisez `active_ms`, pas la durée du span `run`.
- Pour relire le run d'un autre client, ajoutez `--tenant`. Une session précise se désigne par `--session`.
- **Piège** : le README montre `Statut` et `Run` avant le texte de la réponse. En réalité, le texte sort d'abord sur la sortie standard, puis `Statut`, `Run` et `En attente` sur la sortie d'erreur. Si vous redirigez `> reponse.txt`, vous ne récupérez donc que le texte.

### Exemple 25.2 : relire un run par Python, REST et MCP

#### Pourquoi

L'outil de suivi de l'atelier est une application Flutter qui affiche l'historique des relances. Elle n'ira pas lire des fichiers JSONL : elle appelle l'API. Selon l'endroit où tourne le code, la même information est accessible de trois façons.

#### Objectif

Lire la trace et les événements d'un run depuis un script Python, depuis l'API REST et depuis une ressource MCP, puis retrouver tous les appels d'un outil dans le journal.

#### Mise en place

Créez `lire_trace.py`, qui ouvre l'instance Loom, relit la trace, liste les événements d'un run, puis cherche dans tout le journal du client :

```python
"""Relit un run : sa trace, ses événements, puis une recherche dans le journal."""

import asyncio
import sys

from loom_ia.access import Loom
from loom_ia.core.events import EventQuery
from loom_ia.core.model import RunId


async def main(run_id: str) -> None:
    async with Loom.from_config("loom.yaml") as loom:
        trace = await loom.trace(RunId(run_id))
        print(f"{trace.agent} : {trace.status}, {trace.iterations} itérations, {trace.cost_usd} $")
        for span in trace.spans:
            retrait = "  " if span.parent_span_id else ""
            print(f"{retrait}{span.kind:<5} {span.name} ({span.duration_ms:.1f} ms)")

        print()
        for event in await loom.events(RunId(run_id)):
            print(f"{event.seq:>2} {event.type:<18} {event.status}")

        print()
        requete = EventQuery(tenant_id=loom.tenant().id, types=("tool.completed",))
        for event in await loom.query(requete):
            print("outil terminé :", event.facets["tool_name"], "dans le run", event.run_id[:8])


asyncio.run(main(sys.argv[1]))
```

Créez `lire_trace_mcp.py`, un client MCP minimal qui lance `loom mcp` en sous-process et lit la ressource `loom://traces/<run_id>` :

```python
"""Lit la trace d'un run par la ressource MCP loom://traces/{run_id}."""

import asyncio
import json
import shutil
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main(run_id: str) -> None:
    serveur = StdioServerParameters(command=shutil.which("loom") or "loom", args=["mcp"])
    async with stdio_client(serveur) as (lecture, ecriture):
        async with ClientSession(lecture, ecriture) as session:
            await session.initialize()
            modeles = await session.list_resource_templates()
            for modele in modeles.resourceTemplates:
                print("gabarit :", modele.uriTemplate)
            lu = await session.read_resource(f"loom://traces/{run_id}")
            trace = json.loads(lu.contents[0].text)
            print(f"\n{trace['agent']} : {trace['status']}, {len(trace['spans'])} spans")
            for span in trace["spans"]:
                print(f"  {span['kind']:<5} {span['name']}")


asyncio.run(main(sys.argv[1]))
```

#### Exécution

L'accès Python :

```bash
uv run python lire_trace.py 01a1267b-1639-73c5-bfe4-6a953e9ff10e
```

```text
relance_simple : completed, 3 itérations, 0.009654 $
run   invoke_agent relance_simple (3919.4 ms)
  step  step 1 (4.6 ms)
  chat  chat fake-relance (0.9 ms)
  step  step 2 (4.9 ms)
  tool  execute_tool chercher_devis (1.8 ms)
  step  step 3 (4.0 ms)
  chat  chat fake-relance (1.0 ms)
  step  step 4 (3.0 ms)
  other approval.requested (3860.7 ms)
  step  step 5 (4.6 ms)
  tool  execute_tool envoyer_email (1.8 ms)
  step  step 6 (3.6 ms)
  chat  chat fake-relance (0.8 ms)

 1 run.started        ok
 2 message.user       ok
 3 run.claimed        ok
 4 step.started       ok
 5 model.responded    ok
 6 step.completed     ok
 7 run.transitioned   ok
 8 step.started       ok
 9 tool.called        ok
10 tool.completed     ok
11 step.completed     ok
12 run.transitioned   ok
13 step.started       ok
14 model.responded    ok
15 step.completed     ok
16 run.transitioned   ok
17 step.started       ok
18 approval.requested warning
19 step.completed     ok
20 run.transitioned   ok
21 run.claimed        ok
22 approval.granted   ok
23 run.claimed        ok
24 run.transitioned   ok
25 step.started       ok
26 tool.called        ok
27 tool.completed     ok
28 step.completed     ok
29 run.transitioned   ok
30 step.started       ok
31 model.responded    ok
32 step.completed     ok
33 run.transitioned   ok
34 run.completed      ok

outil terminé : chercher_devis dans le run 01a1267b
outil terminé : envoyer_email dans le run 01a1267b
outil terminé : chercher_devis dans le run 01a1267b
outil terminé : envoyer_email dans le run 01a1267b
outil terminé : chercher_devis dans le run 01a1267b
outil terminé : envoyer_email dans le run 01a1267b
outil terminé : chercher_devis dans le run 01a1267b
outil terminé : chercher_devis dans le run 01a1267b
outil terminé : chercher_devis dans le run 01a1267c
outil terminé : envoyer_email dans le run 01a1267c
```

Chaque ligne d'événement porte son numéro de séquence, son type et son statut. Les événements `approval.requested` sont en `warning` : c'est le seul signe visible qu'un humain a été sollicité.

L'accès REST. Démarrez le serveur (sans clé d'API configurée, l'API est ouverte : c'est acceptable en local, pas en production) :

```bash
uv run loom serve --port 18430
```

```text
Profil     : aucun (ni --profile, ni LOOM_PROFILE, ni 'profile:') ; surcharges : aucune
API REST   : http://127.0.0.1:18430/v1
Agents     : relance_simple
```

Dans un autre terminal :

```bash
curl -s http://127.0.0.1:18430/v1/traces/01a1267b-1639-73c5-bfe4-6a953e9ff10e
```

La réponse est le même objet que `loom inspect --json`. Le filtre `GET /v1/events` retrouve les événements par type, par agent, par outil, par période :

```bash
curl -s "http://127.0.0.1:18430/v1/events?run_id=01a1267b-1639-73c5-bfe4-6a953e9ff10e&type=tool.completed"
```

Voici les champs utiles de chaque événement rendu :

```text
10 tool.completed {'tool_name': 'chercher_devis', 'latency_ms': 0.516402999892307, 'size': 538, 'is_error': False}
27 tool.completed {'tool_name': 'envoyer_email', 'latency_ms': 0.5216810000092664, 'size': 201, 'is_error': False}
```

Arrêtez le serveur (`Ctrl+C`), puis l'accès MCP :

```bash
uv run python lire_trace_mcp.py 01a1267b-1639-73c5-bfe4-6a953e9ff10e
```

```text
gabarit : loom://runs/{run_id}{?session_id}
gabarit : loom://runs/{run_id}/events{?session_id}
gabarit : loom://traces/{run_id}{?session_id}
gabarit : loom://sessions/{session_id}
gabarit : loom://sessions/{session_id}/events
gabarit : loom://artifacts/{client}/{session}/{fichier}

relance_simple : completed, 13 spans
  run   invoke_agent relance_simple
  step  step 1
  chat  chat fake-relance
  step  step 2
  tool  execute_tool chercher_devis
  step  step 3
  chat  chat fake-relance
  step  step 4
  other approval.requested
  step  step 5
  tool  execute_tool envoyer_email
  step  step 6
  chat  chat fake-relance
```

#### À retenir

- Trois accès, une seule source : `Loom.trace()` / `Loom.events()` / `Loom.query()` en Python, `GET /v1/traces/{id}` et `GET /v1/events` en REST, la ressource `loom://traces/{run_id}` en MCP. Les gabarits `loom://runs/{run_id}/events` et `loom://sessions/{session_id}/events` donnent les événements.
- `Loom.query(EventQuery(...))` cherche dans le journal d'un client : type, session, run, agent, outil, période. C'est l'outil pour des questions comme « combien de relances sont parties ce mois-ci ? ».
- Une clé d'API sans la portée `read_content` reçoit la trace sans contenu : le JSON porte alors `"content": false` et `"output": null`. Le chapitre 30 le montre avec deux clés.
- **Piège** : `loom schema` imprime le schéma de la **configuration**, pas celui des événements. Le schéma des événements s'obtient en Python (exemple 25.4).

### Exemple 25.3 : exporter vers OpenTelemetry, sans fuite de données

#### Pourquoi

Jean Dupont a un tableau de bord de supervision, et il veut y voir les relances. Mais l'e-mail de Mme Martin, son numéro de téléphone et le numéro de ses devis n'ont rien à faire chez un prestataire de supervision. Il faut exporter la forme du run, et masquer le fond.

#### Objectif

Brancher l'export OpenTelemetry, d'abord en métadonnées seules, puis avec le contenu masqué, et vérifier ce qui part en recevant les spans dans un collecteur local.

#### Mise en place

L'export demande l'extra `otel`. `uv add` fusionne les extras avec ceux déjà déclarés :

```bash
uv add "loom-ia[otel]"
```

Dans `pyproject.toml`, la dépendance devient `loom-ia[http,mcp,otel,sqlite]>=2.0.0`.

Un vrai collecteur (Jaeger, Tempo, Grafana Alloy…) reçoit les spans en OTLP. Pour voir ce qui part sans en installer un, créez un mini collecteur, `collecteur.py`. Il écoute en HTTP, décode les messages OTLP et affiche les spans :

```python
"""Mini collecteur OTLP/HTTP : reçoit les spans de Loom-IA et les affiche.

    uv run python collecteur.py 18411
"""

import gzip
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
    ExportTraceServiceResponse,
)


def valeur(any_value) -> str:
    return str(getattr(any_value, any_value.WhichOneof("value")))


# Attributs de span affichés ; le reste (identifiants, hachages…) est ignoré ici.
CLES = {
    "gen_ai.request.model",
    "gen_ai.tool.name",
    "gen_ai.usage.input_tokens",
    "gen_ai.usage.output_tokens",
    "loom.cost_usd",
}


class Collecteur(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        corps = self.rfile.read(int(self.headers["Content-Length"]))
        if self.headers.get("Content-Encoding") == "gzip":
            corps = gzip.decompress(corps)
        requete = ExportTraceServiceRequest.FromString(corps)
        for ressource in requete.resource_spans:
            service = {a.key: valeur(a.value) for a in ressource.resource.attributes}
            print(f"== lot reçu de {service.get('service.name')}", flush=True)
            for portee in ressource.scope_spans:
                for span in portee.spans:
                    attributs = {a.key: valeur(a.value) for a in span.attributes}
                    retenus = {cle: v for cle, v in attributs.items() if cle in CLES}
                    print(f"span {span.name} {retenus or ''}", flush=True)
                    for evenement in span.events:
                        contenu = {
                            a.key: valeur(a.value)
                            for a in evenement.attributes
                            if not a.key.startswith("loom.") or a.key.startswith("loom.content")
                        }
                        if contenu:
                            print(f"    {evenement.name} {contenu}", flush=True)
        self.send_response(200)
        self.send_header("Content-Type", "application/x-protobuf")
        self.end_headers()
        self.wfile.write(ExportTraceServiceResponse().SerializeToString())

    def log_message(self, *args) -> None:
        pass


if __name__ == "__main__":
    port = int(sys.argv[1])
    print(f"collecteur OTLP sur 127.0.0.1:{port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Collecteur).serve_forever()
```

Créez `loom-otel.yaml`, une variante de `loom.yaml` qui ajoute la capture, le masquage et l'exporteur. Gardez `loom.yaml` tel quel : sans variable d'adresse, chaque commande afficherait un avertissement.

```yaml
version: 1

imports: [outils]
agents_dir: agents/
prompts_dir: prompts/

models:
  - id: SIMULE_RELANCE
    sdk: fake
    model: fake-relance
    pricing: {input: 3.0, output: 15.0}
    params:
      script:
        - text: Je cherche le devis.
          tool_calls:
            - name: chercher_devis
              arguments: {numero: D-2026-042}
        - text: J'envoie la relance.
          tool_calls:
            - name: envoyer_email
              arguments:
                destinataire: mme.martin@example.fr
                objet: Votre devis D-2026-042
                corps: >-
                  Bonjour Mme Martin, nous revenons vers vous au sujet du devis D-2026-042
                  (remplacement du chauffe-eau, 1 840 € TTC) envoyé le 2 septembre.
                  Restons à votre disposition. La Plomberie Dupont
        - text: La relance du devis D-2026-042 (1 840 € TTC) est partie chez Mme Martin.

telemetry:
  logging: {level: WARNING}
  capture: {exports: metadata}
  redaction:
    patterns: [email, phone, iban, {name: devis, regex: "D-\\d{4}-\\d{3}"}]
  exporters:
    - type: otel
      endpoint_env: OTEL_EXPORTER_OTLP_ENDPOINT
      protocol: http/protobuf
      service_name: plomberie-dupont

storage:
  events: {backend: jsonl, path: data}
```

Trois réglages comptent :

- `capture.exports` : `metadata` (par défaut) n'exporte que la forme du run (durées, tokens, coûts, noms d'outils) ; `content` ajoute les messages, les arguments et les résultats, sous forme d'événements de span.
- `redaction.patterns` : les motifs masqués dans le contenu exporté. `email`, `phone` et `iban` sont fournis ; un motif maison s'écrit `{name, regex}`. Ici, `devis` masque tout numéro de la forme `D-2026-042`.
- `exporters` : le collecteur. La config nomme la **variable d'environnement** qui porte son adresse (`endpoint_env`), jamais l'adresse elle-même. Le protocole est `http/protobuf` ou `grpc`. `headers_env` (même principe) porte les en-têtes d'authentification et `timeout` la patience, en secondes.

#### Exécution

Lancez le collecteur dans un premier terminal :

```bash
uv run python collecteur.py 18431
```

Dans un second, indiquez l'adresse du collecteur et lancez une relance avec la configuration d'export. L'export a lieu **à la clôture du run** : il faut donc l'approuver, avec la même variable présente :

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:18431
uv run loom --config loom-otel.yaml run relance_simple "Relance Mme Martin pour le devis D-2026-042, sur un ton cordial."
uv run loom --config loom-otel.yaml approve 01a1267e-734d-7229-868a-02ff01444400 --by "Jean Dupont"
```

Le collecteur affiche, en mode `metadata` :

```text
== lot reçu de plomberie-dupont
span invoke_agent relance_simple {'loom.cost_usd': '0.009654', 'gen_ai.usage.input_tokens': '2033', 'gen_ai.usage.output_tokens': '237'}
span step 1 
span chat fake-relance {'gen_ai.request.model': 'fake-relance', 'loom.cost_usd': '0.002349', 'gen_ai.usage.input_tokens': '453', 'gen_ai.usage.output_tokens': '66'}
span step 2 
span execute_tool chercher_devis {'gen_ai.tool.name': 'chercher_devis'}
span step 3 
span chat fake-relance {'gen_ai.request.model': 'fake-relance', 'loom.cost_usd': '0.003975', 'gen_ai.usage.input_tokens': '685', 'gen_ai.usage.output_tokens': '128'}
span step 4 
span approval.requested 
span step 5 
span execute_tool envoyer_email {'gen_ai.tool.name': 'envoyer_email'}
span step 6 
span chat fake-relance {'gen_ai.request.model': 'fake-relance', 'loom.cost_usd': '0.00333', 'gen_ai.usage.input_tokens': '895', 'gen_ai.usage.output_tokens': '43'}
```

Seuls les noms, les modèles, les tokens et les coûts sont partis. Passez maintenant `capture: {exports: content}` dans `loom-otel.yaml`, et relancez avec une demande qui contient une adresse e-mail et un numéro de téléphone :

```bash
uv run loom --config loom-otel.yaml run relance_simple "Relance Mme Martin (mme.martin@example.fr, 06 12 34 56 78) pour le devis D-2026-042."
```

Après approbation, le collecteur reçoit le contenu, masqué (les lignes sont raccourcies ici) :

```text
== lot reçu de plomberie-dupont
span invoke_agent relance_simple {'loom.cost_usd': '0.009699', 'gen_ai.usage.input_tokens': '2048', 'gen_ai.usage.output_tokens': '237'}
    message.user {'loom.content.message': '{"blocks": [{"cache_breakpoint": false, "provider_meta": {}, "text": "Relance Mme Martin ([email], [phone]) pour le devis [devis].", "type": "text"}], "role": "user"}'}
    run.completed {'loom.content.output': '{"blocks": [{"cache_breakpoint": false, "provider_meta": {}, "text": "La relance du devis [devis] (1 840 € TTC) est partie chez Mme Martin.", "type": "text"}], "role": "assistant"}'}
span step 1 
span chat fake-relance {'gen_ai.request.model': 'fake-relance', 'loom.cost_usd': '0.002364', 'gen_ai.usage.input_tokens': '458', 'gen_ai.usage.output_tokens': '66'}
    model.responded {'loom.content.message': '{"blocks": [{"cache_breakpoint": false, "provider_meta": {}, "text": "Je cherche le devis.", "type": "text"}, {"arguments": {"numero": "[devis]"}, "cache_breakpoint": false, "call_id": "fake_0_0", "name": "chercher_devis", "provider_meta": {}, "type": "t
span step 2 
span execute_tool chercher_devis {'gen_ai.tool.name': 'chercher_devis'}
    tool.called {'loom.content.arguments': '{"numero": "[devis]"}'}
    tool.completed {'loom.content.output': '{"artifacts": [], "blocks": [{"cache_breakpoint": false, "data": {"client": "Mme Martin", "email": "[email]", "envoye_le": "2026-09-02", "montant_ttc": 1840.0, "numero": "[devis]", "objet": "Remplacement du chauffe-eau (200 L)", "statut": "en attente"}, "p
span step 3 
span chat fake-relance {'gen_ai.request.model': 'fake-relance', 'loom.cost_usd': '0.00399', 'gen_ai.usage.input_tokens': '690', 'gen_ai.usage.output_tokens': '128'}
    model.responded {'loom.content.message': '{"blocks": [{"cache_breakpoint": false, "provider_meta": {}, "text": "J\'envoie la relance.", "type": "text"}, {"arguments": {"corps": "Bonjour Mme Martin, nous revenons vers vous au sujet du devis [devis] (remplacement du chauffe-eau, 1 840 € TTC) env
span step 4 
span approval.requested 
    approval.requested {'loom.content.arguments': '{"corps": "Bonjour Mme Martin, nous revenons vers vous au sujet du devis [devis] (remplacement du chauffe-eau, 1 840 € TTC) envoyé le 2 septembre. Restons à votre disposition. La Plomberie Dupont", "destinataire": "[email]", "objet": "Votre devi
span step 5 
span execute_tool envoyer_email {'gen_ai.tool.name': 'envoyer_email'}
    tool.called {'loom.content.arguments': '{"corps": "Bonjour Mme Martin, nous revenons vers vous au sujet du devis [devis] (remplacement du chauffe-eau, 1 840 € TTC) envoyé le 2 septembre. Restons à votre disposition. La Plomberie Dupont", "destinataire": "[email]", "objet": "Votre devis [devi
    tool.completed {'loom.content.output': '{"artifacts": [], "blocks": [{"cache_breakpoint": false, "provider_meta": {}, "text": "E-mail envoyé à [email]", "type": "text"}], "data": null, "is_error": false, "offloaded": null, "unverified": false}'}
span step 6 
span chat fake-relance {'gen_ai.request.model': 'fake-relance', 'loom.cost_usd': '0.003345', 'gen_ai.usage.input_tokens': '900', 'gen_ai.usage.output_tokens': '43'}
    model.responded {'loom.content.message': '{"blocks": [{"cache_breakpoint": false, "provider_meta": {}, "text": "La relance du devis [devis] (1 840 € TTC) est partie chez Mme Martin.", "type": "text"}], "role": "assistant"}'}
```

Les numéros de devis, l'adresse et le téléphone ont été remplacés par `[devis]`, `[email]` et `[phone]`. En revanche, le journal local est resté intact : six de ses lignes contiennent l'adresse en clair, et une le numéro de téléphone.

```bash
grep -c "mme.martin@example.fr" data/default/01a1267e-8227-7000-bd19-72f68e3f6584.jsonl
```

```text
6
```

Si vous oubliez la variable `OTEL_EXPORTER_OTLP_ENDPOINT`, rien ne casse : Loom-IA écrit un avertissement et n'exporte rien.

```bash
unset OTEL_EXPORTER_OTLP_ENDPOINT
uv run loom --config loom-otel.yaml validate | tail -4
```

```text
2026-10-10 17:46:37 WARNING  loom_ia.runtime.wiring — Télémétrie : la variable 'OTEL_EXPORTER_OTLP_ENDPOINT' est vide ou absente — ce collecteur n'est pas monté, aucune trace n'en partira
  relance_simple : modèle SIMULE_RELANCE, 2 outil(s) Python

1 agent(s) monté(s) sans erreur.
```

#### À retenir

- Le masquage ne s'applique qu'à ce qui **sort** : exports `content` vers un collecteur. Le journal reste en clair, parce qu'il fait foi pour la reprise et le rejeu. Pour le protéger, voyez le chiffrement et la rétention du chapitre 24.
- Le choix `metadata` / `content` peut être fait par client (`tenants[].telemetry.capture`), pour exporter le contenu d'un client qui l'accepte et pas d'un autre.
- `raw_exchanges: true` ajoute au **journal** (pas aux exports) la requête et la réponse HTTP de chaque appel de modèle (événement `model.exchanged`), bornées par `raw_max_bytes`. Utile pour déboguer un fournisseur ; avec le modèle simulé, ces échanges sont synthétiques. **Non exécuté ici** contre un vrai fournisseur.
- **Piège** : une variable d'adresse absente donne un simple avertissement (« ce collecteur n'est pas monté »). Une faute de frappe dans son nom ne se verra donc que dans les logs : surveillez cette ligne au démarrage d'un serveur.
- **Piège** : `service_name` vaut `loom-ia` par défaut. Avec plusieurs services dans le même outil de supervision, donnez-en un par déploiement.

### Exemple 25.4 : les logs et le schéma des événements

#### Pourquoi

Quand le serveur de l'atelier tourne depuis trois semaines, personne ne lance `loom inspect` : on lit les logs, et on branche l'outil de collecte de logs sur un format qu'il comprend. Et quand l'application Flutter lit les événements de l'API, elle a besoin d'un contrat stable pour ne pas casser à la prochaine mise à jour.

#### Objectif

Choisir le niveau et le format des logs (console ou JSON), les brancher soi-même dans un script, et afficher la version et les types d'événements que Loom-IA promet.

#### Mise en place

Dans `loom.yaml`, le bloc `telemetry.logging` règle les logs des commandes `loom` et du serveur :

```yaml
telemetry:
  logging: {level: INFO, format: json}   # level : DEBUG, INFO, WARNING, ERROR ; format : console ou json
```

Dans votre propre programme, Loom-IA n'impose rien : vous appelez `configure_logging`. Créez `logs_python.py` :

```python
"""Les logs de Loom-IA dans une application Python : on branche soi-même l'affichage."""

import asyncio
import logging
import sys

from loom_ia.access import Loom
from loom_ia.telemetry import configure_logging


async def main(format: str) -> None:
    configure_logging(logging.INFO, format=format)
    async with Loom.from_config("loom.yaml") as loom:
        resultat = await loom.run("relance_simple", "Relance Mme Martin pour le devis D-2026-042.")
        print("statut :", resultat.status)


asyncio.run(main(sys.argv[1]))
```

Créez `schema_evenements.py` :

```python
"""Le contrat des événements : version du schéma, types, catégories."""

from loom_ia.core.events import DURABLE_PAYLOADS, SCHEMA_VERSION, event_json_schema

print("version du schéma d'événements :", SCHEMA_VERSION)
schema = event_json_schema()
print("propriétés de l'enveloppe :", ", ".join(schema["properties"]))
print()
for charge in DURABLE_PAYLOADS:
    champs = charge.model_fields
    print(f"{champs['type'].default:<24} {charge.category}")
```

#### Exécution

Le format `console` :

```bash
uv run python logs_python.py console
```

```text
2026-10-10 17:46:46 INFO     loom_ia.engine.loop — Modèle fake-relance (rôle main) : 0.00 s, 514 tokens, 0.00233 $ [run_id=01a1267e-cadc-750e-95f0-dd2f96da607f span_id=01a1267e-cae2-7604-ac24-c453e7410ea7 tenant_id=default]
2026-10-10 17:46:46 INFO     loom_ia.engine.loop — Transition ready_for_model → awaiting_tools (agent relance_simple) [run_id=01a1267e-cadc-750e-95f0-dd2f96da607f span_id=01a1267e-cadc-750e-95f0-dd303bb27621 tenant_id=default]
2026-10-10 17:46:46 INFO     loom_ia.engine.loop — Outil chercher_devis : 0 ms [run_id=01a1267e-cadc-750e-95f0-dd2f96da607f span_id=01a1267e-caea-7726-8a01-89dc2e488fa0 tenant_id=default]
2026-10-10 17:46:46 INFO     loom_ia.engine.loop — Transition awaiting_tools → ready_for_model (agent relance_simple) [run_id=01a1267e-cadc-750e-95f0-dd2f96da607f span_id=01a1267e-cadc-750e-95f0-dd303bb27621 tenant_id=default]
2026-10-10 17:46:46 INFO     loom_ia.engine.loop — Modèle fake-relance (rôle main) : 0.00 s, 808 tokens, 0.00396 $ [run_id=01a1267e-cadc-750e-95f0-dd2f96da607f span_id=01a1267e-caef-7542-9cdf-116deed154d3 tenant_id=default]
2026-10-10 17:46:46 INFO     loom_ia.engine.loop — Transition ready_for_model → awaiting_tools (agent relance_simple) [run_id=01a1267e-cadc-750e-95f0-dd2f96da607f span_id=01a1267e-cadc-750e-95f0-dd303bb27621 tenant_id=default]
2026-10-10 17:46:46 INFO     loom_ia.engine.loop — Transition awaiting_tools → paused (agent relance_simple) [run_id=01a1267e-cadc-750e-95f0-dd2f96da607f span_id=01a1267e-cadc-750e-95f0-dd303bb27621 tenant_id=default]
statut : paused
```

Le format `json`, une ligne par enregistrement, prêt pour un collecteur de logs :

```bash
uv run python logs_python.py json
```

```text
{"ts": "2026-10-10T15:46:46.695+00:00", "level": "INFO", "logger": "loom_ia.engine.loop", "message": "Modèle fake-relance (rôle main) : 0.00 s, 514 tokens, 0.00233 $", "run_id": "01a1267e-cd1c-7101-bad3-7ba44088e094", "span_id": "01a1267e-cd22-74ed-9927-3e55a3914a02", "tenant_id": "default"}
{"ts": "2026-10-10T15:46:46.699+00:00", "level": "INFO", "logger": "loom_ia.engine.loop", "message": "Transition ready_for_model → awaiting_tools (agent relance_simple)", "run_id": "01a1267e-cd1c-7101-bad3-7ba44088e094", "span_id": "01a1267e-cd1c-7101-bad3-7ba529d23def", "tenant_id": "default"}
{"ts": "2026-10-10T15:46:46.704+00:00", "level": "INFO", "logger": "loom_ia.engine.loop", "message": "Outil chercher_devis : 0 ms", "run_id": "01a1267e-cd1c-7101-bad3-7ba44088e094", "span_id": "01a1267e-cd2d-7330-bcbc-f79f5998b7b0", "tenant_id": "default"}
{"ts": "2026-10-10T15:46:46.708+00:00", "level": "INFO", "logger": "loom_ia.engine.loop", "message": "Transition awaiting_tools → ready_for_model (agent relance_simple)", "run_id": "01a1267e-cd1c-7101-bad3-7ba44088e094", "span_id": "01a1267e-cd1c-7101-bad3-7ba529d23def", "tenant_id": "default"}
{"ts": "2026-10-10T15:46:46.712+00:00", "level": "INFO", "logger": "loom_ia.engine.loop", "message": "Modèle fake-relance (rôle main) : 0.00 s, 808 tokens, 0.00396 $", "run_id": "01a1267e-cd1c-7101-bad3-7ba44088e094", "span_id": "01a1267e-cd34-7407-aa4f-0053e9ef678a", "tenant_id": "default"}
{"ts": "2026-10-10T15:46:46.713+00:00", "level": "INFO", "logger": "loom_ia.engine.loop", "message": "Transition ready_for_model → awaiting_tools (agent relance_simple)", "run_id": "01a1267e-cd1c-7101-bad3-7ba44088e094", "span_id": "01a1267e-cd1c-7101-bad3-7ba529d23def", "tenant_id": "default"}
{"ts": "2026-10-10T15:46:46.717+00:00", "level": "INFO", "logger": "loom_ia.engine.loop", "message": "Transition awaiting_tools → paused (agent relance_simple)", "run_id": "01a1267e-cd1c-7101-bad3-7ba44088e094", "span_id": "01a1267e-cd1c-7101-bad3-7ba529d23def", "tenant_id": "default"}
statut : paused
```

Le contrat des événements :

```bash
uv run python schema_evenements.py
```

```text
version du schéma d'événements : 1
propriétés de l'enveloppe : event_id, ts, schema_version, tenant_id, session_id, run_id, root_run_id, span_id, parent_span_id, type, category, status, agent, role, facets, payload, seq

run.started              run
step.started             run
step.completed           run
run.transitioned         run
run.completed            run
run.failed               run
run.cancelled            run
run.claimed              run
message.user             message
model.responded          model
model.retried            model
model.exchanged          model
model.fell_back          model
circuit.opened           circuit
tool.called              tool
tool.completed           tool
tool.source_unavailable  tool
policy.decided           policy
guard.checked            guard
judge.evaluated          guard
budget.exceeded          policy
approval.requested       approval
approval.granted         approval
approval.rejected        approval
approval.expired         approval
idempotency.recorded     idempotency
idempotency.reused       idempotency
artifact.stored          artifact
session.snapshot         session
session.compacted        session
session.trimmed          session
```

#### À retenir

- À `INFO`, Loom-IA écrit une ligne par appel de modèle et par appel d'outil, **et** une ligne par transition d'état (`ready_for_model → awaiting_tools`…). Le README ne parle que des deux premières. Chaque ligne porte `run_id`, `span_id` et `tenant_id` : c'est ce qui permet de retrouver, dans un outil de logs, toutes les lignes d'un run.
- Pour la production, `WARNING` suffit à surveiller et le format `json` se branche partout. `INFO` sert au diagnostic.
- Le journal a un **schéma versionné** (`SCHEMA_VERSION`, ici 1). L'enveloppe d'un événement a dix-sept champs, et son champ `type` désigne l'une des 31 charges utiles. Un consommateur d'événements (votre application, un entrepôt de données) doit lire `schema_version` et refuser ce qu'il ne connaît pas.
- `event_json_schema()` renvoie le schéma JSON complet de l'enveloppe, à donner à un validateur ou à un générateur de modèles (par exemple pour produire les classes Dart de l'application Flutter).

---

## 26. Rejouer un run

Le journal contient tout ce qu'un run a reçu du modèle et des outils. Loom-IA peut donc le **rejouer** : refaire tourner l'agent en lui resservant, à la place du modèle et des outils, les réponses d'origine. Rien ne part vers le fournisseur, aucun outil ne s'exécute, et pourtant le moteur refait chaque pas. C'est la meilleure façon de savoir si ce que vous venez de modifier change quelque chose.

### Exemple 26.1 : rejouer un run à l'identique

#### Pourquoi

Jean Dupont vient de mettre à jour Loom-IA. Il se demande si l'agent de relance se comporte encore comme avant, mais il ne veut pas relancer vraiment Mme Martin pour le savoir. Un run de référence existe dans le journal : le rejeu doit retomber dessus, pas à peu près, mais appel par appel.

#### Objectif

Rejouer le run du chapitre 25 et lire le verdict « identique ».

#### Mise en place

Rien de nouveau : le rejeu s'appuie sur le projet `qualite/` et sur le run approuvé de l'exemple 25.1. Notez seulement combien de lignes contient la boîte d'envoi, pour vérifier ensuite que le rejeu n'a rien envoyé :

```bash
wc -l boite_envoi.txt
```

```text
6 boite_envoi.txt
```

#### Exécution

```bash
uv run loom replay 01a1267b-1639-73c5-bfe4-6a953e9ff10e
```

```text
Run        : 01a1267b-1639-73c5-bfe4-6a953e9ff10e (agent relance_simple, client default)
Session    : 01a1267b-1639-73c5-bfe4-6a953e9ff10e
Issue      : completed au journal, completed au rejeu
Modèles    : 3 appel(s) au journal, 3 servi(s) au rejeu
Outils     : 2 résultat(s) au journal (rôles à part), 2 servi(s) au rejeu
Identique  : le run se rejoue tel qu'il a été, appel par appel.
```

Le rejeu a servi les 3 réponses du modèle et les 2 résultats d'outil depuis le journal, et le moteur est arrivé au même résultat. La boîte d'envoi, elle, n'a pas bougé :

```bash
wc -l boite_envoi.txt
```

```text
6 boite_envoi.txt
```

#### À retenir

- Un rejeu **à l'identique** (le mode par défaut) compare, appel de modèle par appel de modèle, l'empreinte de la requête que le moteur construit aujourd'hui avec celle qui est au journal. Si tout concorde, le verdict est « Identique ».
- Aucun outil n'est exécuté et aucun modèle n'est appelé : le rejeu est gratuit, sans effet de bord, et fonctionne hors ligne.
- Seul un run **terminé** se rejoue. Un run en pause ou en cours donne une erreur (voir l'exemple 26.3).
- `--json` rend le même rapport en JSON (`mode`, `identical`, `divergence`…), pour un script ou une intégration continue.

### Exemple 26.2 : repérer une divergence

#### Pourquoi

Jean Dupont retouche le prompt de l'agent : il ajoute une consigne. Il se dit que ça ne change rien aux anciens cas. Le rejeu va lui dire si c'est vrai, et surtout **où** exactement les choses ont changé.

#### Objectif

Modifier le prompt, rejouer le même run, et lire la divergence.

#### Mise en place

Ajoutez une ligne à `prompts/relance_simple.md` :

```bash
printf '\nSi le client a déjà répondu, ne relance pas.\n' >> prompts/relance_simple.md
```

#### Exécution

```bash
uv run loom replay 01a1267b-1639-73c5-bfe4-6a953e9ff10e
```

```text
2026-10-10 17:47:15 WARNING  loom_ia.replay.runner — Rejeu du run 01a1267b-1639-73c5-bfe4-6a953e9ff10e : divergence, appel de modèle n°1 (main au journal) : la requête a changé — ce qui diffère : le prompt système [run_id=01a1267b-1639-73c5-bfe4-6a953e9ff10e tenant_id=default]
Run        : 01a1267b-1639-73c5-bfe4-6a953e9ff10e (agent relance_simple, client default)
Session    : 01a1267b-1639-73c5-bfe4-6a953e9ff10e
Issue      : completed au journal, failed au rejeu
Modèles    : 3 appel(s) au journal, 0 servi(s) au rejeu
Outils     : 2 résultat(s) au journal (rôles à part), 0 servi(s) au rejeu
Divergence : appel de modèle n°1 (main au journal) : la requête a changé
             ce qui diffère : le prompt système
             empreinte eecc0a69d610 au journal, 2ca7c5a09daf au rejeu
```

La première ligne est un avertissement écrit dans les logs (sortie d'erreur) ; le rapport suit. Le code de sortie est 1. Rétablissez ensuite le prompt d'origine, en retirant la ligne ajoutée :

```bash
head -n -2 prompts/relance_simple.md > /tmp/prompt.md && mv /tmp/prompt.md prompts/relance_simple.md
```

#### À retenir

- Le rapport dit **à quel appel** la requête a changé (« appel de modèle n°1, main »), **quelle partie** a changé (« le prompt système ») et donne les deux empreintes. Les parties possibles sont le modèle, le prompt système, les outils proposés, les messages et les réglages (choix d'outil, plafond de tokens, paramètres, schéma de sortie). La divergence porte sur un appel de modèle, un outil, une approbation ou la fin du run (champ `kind` du JSON).
- Le rejeu s'arrête à la première divergence : « 0 servi(s) au rejeu », le run rejoué est en échec (`failed`).
- Ce n'est pas forcément mauvais : la divergence prouve seulement que **l'ancien journal ne répond plus à la nouvelle requête**. Si le changement est voulu, ce journal n'est plus un test de non-régression valable pour cet agent. C'est le point de départ du chapitre 28.
- Le rejeu ne dit rien de la qualité d'une réponse. Pour savoir si un changement donne une meilleure ou une moins bonne relance, il faut une variante (exemple suivant), puis une évaluation (chapitre 27).

### Exemple 26.3 : essayer un autre modèle sur un vrai run, sans rien envoyer

#### Pourquoi

Un fournisseur propose un modèle moins cher. Jean Dupont voudrait savoir ce qu'il ferait de la relance de Mme Martin, avec **le même devis et les mêmes résultats d'outil**, sans renvoyer d'e-mail à la cliente. Un rejeu en **variante** remplace une partie de la configuration et laisse partir pour de vrai les appels qui ne peuvent plus être servis par le journal.

#### Objectif

Rejouer le run avec un autre modèle pour l'orchestrateur (`--model`), voir ce qui est lu au journal, ce qui est exécuté, ce qui est remplacé par une doublure, et ce que le changement coûte.

#### Mise en place

Remplacez `loom.yaml` par la version complète suivante. Elle ajoute deux modèles simulés : `SIMULE_BREF`, qui envoie une relance plus courte, et `SIMULE_OUBLIEUR`, qui oublie d'envoyer l'e-mail.

```yaml
version: 1

imports: [outils]
agents_dir: agents/
prompts_dir: prompts/

models:
  - id: SIMULE_RELANCE
    sdk: fake
    model: fake-relance
    pricing: {input: 3.0, output: 15.0}
    params:
      script:
        - text: Je cherche le devis.
          tool_calls:
            - name: chercher_devis
              arguments: {numero: D-2026-042}
        - text: J'envoie la relance.
          tool_calls:
            - name: envoyer_email
              arguments:
                destinataire: mme.martin@example.fr
                objet: Votre devis D-2026-042
                corps: >-
                  Bonjour Mme Martin, nous revenons vers vous au sujet du devis D-2026-042
                  (remplacement du chauffe-eau, 1 840 € TTC) envoyé le 2 septembre.
                  Restons à votre disposition. La Plomberie Dupont
        - text: La relance du devis D-2026-042 (1 840 € TTC) est partie chez Mme Martin.
  - id: SIMULE_BREF
    sdk: fake
    model: fake-bref
    pricing: {input: 3.0, output: 15.0}
    params:
      script:
        - text: Je cherche le devis.
          tool_calls:
            - name: chercher_devis
              arguments: {numero: D-2026-042}
        - text: J'envoie une relance brève.
          tool_calls:
            - name: envoyer_email
              arguments:
                destinataire: mme.martin@example.fr
                objet: Votre devis D-2026-042
                corps: Bonjour Mme Martin, avez-vous pu regarder le devis D-2026-042 (1 840 € TTC) ?
        - text: Relance brève envoyée à Mme Martin pour le devis D-2026-042.
  - id: SIMULE_OUBLIEUR
    sdk: fake
    model: fake-oublieur
    pricing: {input: 3.0, output: 15.0}
    params:
      script:
        - text: Je cherche le devis.
          tool_calls:
            - name: chercher_devis
              arguments: {numero: D-2026-042}
        - text: Le devis D-2026-042 (1 840 € TTC) est toujours en attente depuis le 2 septembre.

telemetry:
  logging: {level: WARNING}

storage:
  events: {backend: jsonl, path: data}
```

#### Exécution

Le rejeu en variante demande `--mode variant`. `--model ETAPE=MODELE` désigne l'étape (`main`, ou le nom d'un rôle) et le modèle qui la remplace :

```bash
uv run loom replay 01a1267b-1639-73c5-bfe4-6a953e9ff10e --mode variant --model main=SIMULE_BREF
```

```text
Run        : 01a1267b-1639-73c5-bfe4-6a953e9ff10e (agent relance_simple, client default)
Session    : 01a1267b-1639-73c5-bfe4-6a953e9ff10e
Variante   : main → SIMULE_BREF
Écart      : appel de modèle n°1 (main au journal) : la requête a changé
             ce qui diffère : le modèle, les réglages (choix d'outil, plafond de tokens, paramètres, schéma de sortie)
             origine                 variante
Issue      : completed               completed
Modèles    : 3 appel(s)              3 appel(s) : 0 servi(s) par le journal, 3 parti(s) pour de vrai
Outils     : 2 appel(s)              2 appel(s) : 1 lu au journal, 1 non exécuté : effets de bord
Tokens     : 2033 / 237              1952 / 208 (entrée / sortie)
Coût       : 0.009654 $              0.008976 $ (dépensé pour de vrai : 0.008976 $)
Durée      : 0.0 s                   0.0 s
Réponse    : différente
--- au journal
La relance du devis D-2026-042 (1 840 € TTC) est partie chez Mme Martin.
--- en variante
Relance brève envoyée à Mme Martin pour le devis D-2026-042.
```

Lecture du rapport :

- **Écart** : où le rejeu quitte le journal. Dès l'appel n°1, le modèle a changé : plus rien ne peut être servi depuis le journal pour ce modèle, qui est donc appelé « pour de vrai ».
- **Outils** : `chercher_devis` est une lecture, son résultat est relu au journal. `envoyer_email` a des effets de bord irréversibles : il n'est **jamais** réexécuté en rejeu. Sans doublure, il est « non exécuté ».
- **Tokens, Coût, Durée** : les deux colonnes se comparent. La colonne de droite ajoute ce qui a été dépensé pour de vrai.
- **Réponse** : « différente », et les deux textes sont imprimés.

Avec la doublure `faux_envoi` de `outils.py`, l'appel d'envoi est joué, mais par la doublure :

```bash
uv run loom replay 01a1267b-1639-73c5-bfe4-6a953e9ff10e --mode variant --model main=SIMULE_BREF --double envoyer_email=outils:faux_envoi
```

```text
Run        : 01a1267b-1639-73c5-bfe4-6a953e9ff10e (agent relance_simple, client default)
Session    : 01a1267b-1639-73c5-bfe4-6a953e9ff10e
Variante   : main → SIMULE_BREF
Écart      : appel de modèle n°1 (main au journal) : la requête a changé
             ce qui diffère : le modèle, les réglages (choix d'outil, plafond de tokens, paramètres, schéma de sortie)
             origine                 variante
Issue      : completed               completed
Modèles    : 3 appel(s)              3 appel(s) : 0 servi(s) par le journal, 3 parti(s) pour de vrai
Outils     : 2 appel(s)              2 appel(s) : 1 lu au journal, 1 remplacé par sa doublure
Tokens     : 2033 / 237              1922 / 208 (entrée / sortie)
Coût       : 0.009654 $              0.008886 $ (dépensé pour de vrai : 0.008886 $)
Durée      : 0.0 s                   0.0 s
Réponse    : différente
--- au journal
La relance du devis D-2026-042 (1 840 € TTC) est partie chez Mme Martin.
--- en variante
Relance brève envoyée à Mme Martin pour le devis D-2026-042.
```

`--double OUTIL=REF` désigne la doublure par `module:fonction`. La ligne « Outils » devient « 1 lu au journal, 1 remplacé par sa doublure ».

Le second modèle, `SIMULE_OUBLIEUR`, ne rédige pas d'e-mail :

```bash
uv run loom replay 01a1267b-1639-73c5-bfe4-6a953e9ff10e --mode variant --model main=SIMULE_OUBLIEUR
```

```text
Run        : 01a1267b-1639-73c5-bfe4-6a953e9ff10e (agent relance_simple, client default)
Session    : 01a1267b-1639-73c5-bfe4-6a953e9ff10e
Variante   : main → SIMULE_OUBLIEUR
Écart      : appel de modèle n°1 (main au journal) : la requête a changé
             ce qui diffère : le modèle, les réglages (choix d'outil, plafond de tokens, paramètres, schéma de sortie)
             origine                 variante
Issue      : completed               completed
Modèles    : 3 appel(s)              2 appel(s) : 0 servi(s) par le journal, 2 parti(s) pour de vrai
Outils     : 2 appel(s)              1 appel(s) : 1 lu au journal
Tokens     : 2033 / 237              965 / 111 (entrée / sortie)
Coût       : 0.009654 $              0.004560 $ (dépensé pour de vrai : 0.004560 $)
Durée      : 0.0 s                   0.0 s
Réponse    : différente
--- au journal
La relance du devis D-2026-042 (1 840 € TTC) est partie chez Mme Martin.
--- en variante
Le devis D-2026-042 (1 840 € TTC) est toujours en attente depuis le 2 septembre.
```

Le rejeu a bien constaté que la variante n'appelle qu'un outil au lieu de deux, mais il sort pourtant avec le code 0 : pour lui, un run « completed » est un run réussi. Détecter qu'un outil attendu n'est pas appelé est le travail des évaluations (chapitre 27).

Une variante qui ne change rien se rejoue entièrement depuis le journal :

```bash
uv run loom replay 01a1267b-1639-73c5-bfe4-6a953e9ff10e --mode variant
```

```text
Run        : 01a1267b-1639-73c5-bfe4-6a953e9ff10e (agent relance_simple, client default)
Session    : 01a1267b-1639-73c5-bfe4-6a953e9ff10e
Variante   : la config, telle quelle
Écart      : aucun — la variante n'a rien demandé que le journal ne connaisse
             origine                 variante
Issue      : completed               completed
Modèles    : 3 appel(s)              3 appel(s) : 3 servi(s) par le journal, 0 parti(s) pour de vrai
Outils     : 2 appel(s)              2 appel(s) : 2 lu au journal
Tokens     : 2033 / 237              2033 / 237 (entrée / sortie)
Coût       : 0.009654 $              0.009654 $ (dépensé pour de vrai : 0.000000 $)
Durée      : 0.0 s                   0.0 s
Réponse    : la même
```

`--export fichier.jsonl` garde le journal du run rejoué dans un fichier (le chapitre 28 s'en sert pour fabriquer des journaux de référence) et `--json` donne le rapport en JSON. Les erreurs de demande sortent avec le code 2 :

```bash
uv run loom replay 01a1267b-0000-7000-8000-000000000000
uv run loom replay 01a1267f-7cee-7719-b8f6-38687641850b
uv run loom replay 01a1267b-1639-73c5-bfe4-6a953e9ff10e --mode variant --model main=INCONNU
uv run loom replay 01a1267b-1639-73c5-bfe4-6a953e9ff10e --model main=SIMULE_BREF
```

```text
$ loom replay 01a1267b-0000-7000-8000-000000000000
Rejeu impossible : Run 01a1267b-0000-7000-8000-000000000000 introuvable dans cette session
exit=2

$ loom replay 01a1267f-7cee-7719-b8f6-38687641850b
Rejeu impossible : Run 01a1267f-7cee-7719-b8f6-38687641850b : inachevé (en cours ou en pause) — un rejeu compare un run fini
exit=2

$ loom replay 01a1267b-1639-73c5-bfe4-6a953e9ff10e --mode variant --model main=INCONNU
Rejeu impossible : Variante : modèle(s) non déclaré(s) dans la config : INCONNU
exit=2

$ loom replay 01a1267b-1639-73c5-bfe4-6a953e9ff10e --model main=SIMULE_BREF
Rejeu impossible : Rejeu identique : 'models' et 'doubles' ne servent qu'en variante (mode='variant')
exit=2

```

(Les lignes `$ loom …` et `exit=…` ne viennent pas de Loom-IA : elles ont été ajoutées pour montrer la commande et son code de sortie.)

Le deuxième identifiant est celui d'un run laissé en pause (lancé sans l'approuver) : un rejeu compare un run fini.

#### À retenir

- Deux modes : `exact` (par défaut) et `variant`. `--model` et `--double` n'existent qu'en `variant` ; en `exact`, ils sont refusés (code 2).
- Un rejeu en variante **dépense de l'argent** : les appels de modèle que le journal ne peut pas servir partent vraiment chez le fournisseur. Avec un modèle simulé, c'est gratuit ; avec un vrai modèle, le rapport indique le coût « dépensé pour de vrai ».
- Les outils à effets de bord ne sont jamais réexécutés : soit une doublure les remplace (`--double`), soit ils restent « non exécuté ». C'est ce qui rend le rejeu sûr sur un agent qui envoie des e-mails.
- Codes de sortie de `loom replay` : **0** identique (ou variante terminée), **1** divergence en mode exact ou variante en échec, **2** rejeu impossible (run inconnu, run inachevé, option refusée, modèle inconnu).
- **Piège** : le rapport « Identique » ne prouve pas que l'agent est bon, seulement qu'il est **resté le même**. Et un rejeu en variante qui sort en 0 ne prouve pas que la variante fait le travail demandé : lisez la ligne « Outils ».

---

## 27. Évaluer un agent

Un rejeu dit si l'agent est resté le même. Une **évaluation** dit s'il fait ce qu'on attend de lui. Elle joue des **cas** (une demande et ce qu'on en attend) pour une ou plusieurs **variantes** (la configuration actuelle, un autre modèle, une autre configuration), un nombre de fois choisi, et rend un verdict que l'on peut lire ou brancher sur une intégration continue.

### Exemple 27.1 : une première suite d'évaluation

#### Pourquoi

Avant chaque modification de l'agent de relance, Jean Dupont se pose les mêmes questions : l'e-mail part-il bien chez Mme Martin ? Le montant est-il repris ? L'agent a-t-il bien cherché le devis avant d'écrire ? Au lieu de relancer à la main et de relire, il veut les écrire une fois pour toutes.

#### Objectif

Écrire une suite d'un cas, avec des contrôles déterministes, la jouer avec `loom eval`, puis voir ce qu'elle dit quand un attendu tombe.

#### Mise en place

Remplacez `loom.yaml` par la version complète suivante. Elle reprend celle du chapitre 26 et ajoute `SIMULE_JUGE`, un juge simulé dont l'exemple 27.2 se sert :

```yaml
version: 1

imports: [outils]
agents_dir: agents/
prompts_dir: prompts/

models:
  - id: SIMULE_RELANCE
    sdk: fake
    model: fake-relance
    pricing: {input: 3.0, output: 15.0}
    params:
      script:
        - text: Je cherche le devis.
          tool_calls:
            - name: chercher_devis
              arguments: {numero: D-2026-042}
        - text: J'envoie la relance.
          tool_calls:
            - name: envoyer_email
              arguments:
                destinataire: mme.martin@example.fr
                objet: Votre devis D-2026-042
                corps: >-
                  Bonjour Mme Martin, nous revenons vers vous au sujet du devis D-2026-042
                  (remplacement du chauffe-eau, 1 840 € TTC) envoyé le 2 septembre.
                  Restons à votre disposition. La Plomberie Dupont
        - text: La relance du devis D-2026-042 (1 840 € TTC) est partie chez Mme Martin.
  - id: SIMULE_BREF
    sdk: fake
    model: fake-bref
    pricing: {input: 3.0, output: 15.0}
    params:
      script:
        - text: Je cherche le devis.
          tool_calls:
            - name: chercher_devis
              arguments: {numero: D-2026-042}
        - text: J'envoie une relance brève.
          tool_calls:
            - name: envoyer_email
              arguments:
                destinataire: mme.martin@example.fr
                objet: Votre devis D-2026-042
                corps: Bonjour Mme Martin, avez-vous pu regarder le devis D-2026-042 (1 840 € TTC) ?
        - text: Relance brève envoyée à Mme Martin pour le devis D-2026-042.
  - id: SIMULE_OUBLIEUR
    sdk: fake
    model: fake-oublieur
    pricing: {input: 3.0, output: 15.0}
    params:
      script:
        - text: Je cherche le devis.
          tool_calls:
            - name: chercher_devis
              arguments: {numero: D-2026-042}
        - text: Le devis D-2026-042 (1 840 € TTC) est toujours en attente depuis le 2 septembre.
  - id: SIMULE_JUGE
    sdk: fake
    model: fake-juge
    pricing: {input: 1.0, output: 5.0}
    params:
      script:
        - with_text: "1 840"
          tool_calls:
            - name: verdict
              arguments:
                criteria:
                  - {name: fidele, score: 1.0, reason: Le montant de 1 840 € figure dans le devis.}
        - without_text: "1 840"
          tool_calls:
            - name: verdict
              arguments:
                criteria:
                  - {name: fidele, score: 0.2, reason: Le montant du devis n'est pas repris.}

telemetry:
  logging: {level: WARNING}

storage:
  events: {backend: jsonl, path: data}
```

Créez `evals-simple.yaml` :

```yaml
version: 1
name: relance-simple
agent: relance_simple

doubles:
  envoyer_email: outils:faux_envoi

cases:
  - name: relance-martin
    input: Relance Mme Martin pour le devis D-2026-042, sur un ton cordial.
    expect:
      status: completed
      contains: ["D-2026-042", "1 840"]
      not_contains: ["erreur"]
      matches: ["(?i)mme martin"]
      called:
        - chercher_devis
        - {name: envoyer_email, arguments: {destinataire: mme.martin@example.fr}}
```

- `agent` désigne l'agent évalué ; `config`, absent ici, désigne la configuration jouée (par défaut celle de `--config`).
- `doubles` associe à un outil à effets de bord sa doublure, `module:fonction`. **Un outil à effets de bord n'est jamais exécuté pendant une évaluation** : sa doublure répond à sa place, et sans doublure le modèle reçoit une erreur. C'est ce qui permet d'évaluer un agent qui envoie des e-mails. Les outils sans effets de bord (`chercher_devis`) s'exécutent normalement.
- `cases` liste les cas. Chaque cas a un `name`, une demande (`input`) et des attendus (`expect`).

Les attendus disponibles sont : `status` (par défaut `completed`), `contains` et `not_contains` (des textes dans la réponse finale), `matches` (des expressions régulières, cherchées avec `re.search`), `fields` (des champs d'une sortie structurée, par chemin pointé), `called` (des outils appelés, avec au moins ces arguments ; un nom seul suffit) et `not_called` (des outils qui ne doivent jamais être appelés).

#### Exécution

```bash
uv run loom eval evals-simple.yaml
```

```text
Suite      : relance-simple — agent relance_simple, 1 cas, 1 variante(s), 1 répétition(s) par cas

Variante base (config de base) — 1/1 cas réussi(s), 0.009663 $ de runs, 0.000000 $ de juge
  ok       relance-martin (1/1)

Bilan      : 1/1 run(s) réussi(s), 0.009663 $ dépensé(s)
Verdict    : tout passe
```

Le code de sortie est 0 quand tout passe. Pour voir un échec, remplacez dans une copie le destinataire attendu par `m.bernard@example.fr` et la réponse attendue par « 2 100 » :

```bash
sed -e 's/destinataire: mme.martin@example.fr/destinataire: m.bernard@example.fr/' \
    -e 's/contains: \["D-2026-042", "1 840"\]/contains: ["D-2026-042", "2 100"]/' \
    evals-simple.yaml > evals-casse.yaml
uv run loom eval evals-casse.yaml
```

```text
Suite      : relance-simple — agent relance_simple, 1 cas, 1 variante(s), 1 répétition(s) par cas

Variante base (config de base) — 0/1 cas réussi(s), 0.009663 $ de runs, 0.000000 $ de juge
  ÉCHEC    relance-martin (0/1)
      essai 1 : run 01a12680-83e6-767a-a75e-d5bcae364f09, completed
        ✗ contient « 2 100 »
        ✗ appelle envoyer_email avec {"destinataire": "m.bernard@example.fr"} — arguments reçus : {"destinataire": "mme.martin@example.fr", "objet": "Votre devis D-2026-042", "corps": "Bonjour Mme Martin, nous revenons vers vous au sujet du devis D-2026-042 (remplacement du chauffe-eau, 1 840 € TTC) envoyé le 2 septembre. Restons à votre disposition. La Plomberie Dupont"}
        texte :
          La relance du devis D-2026-042 (1 840 € TTC) est partie chez Mme Martin.

Bilan      : 0/1 run(s) réussi(s), 0.009663 $ dépensé(s)
Verdict    : des attendus tombent, ou des runs n'ont pas été joués
```

Chaque contrôle qui tombe est nommé, avec ce qui a été reçu à la place : le texte de la réponse, les arguments réels de l'appel. Le code de sortie est 1.

#### À retenir

- Sans `status`, un cas exige que le run finisse `completed` : un run échoué, annulé ou en pause fait tomber le cas, même si ses autres contrôles tiennent. Avec `status`, c'est ce contrôle seul qui décide (`status: failed` attend un échec).
- Un cas sans aucun attendu est refusé au chargement : il n'éprouverait rien.
- `repeat: N` joue chaque cas N fois, et le cas ne réussit que si toutes les répétitions passent. Avec un vrai modèle, qui n'est pas régulier, c'est ce qui sépare un succès de hasard d'un comportement fiable.
- `max_cost_usd` plafonne la dépense de la suite. Une fois atteint, les runs suivants ne partent pas et le rapport dit combien n'ont pas été joués.
- L'évaluation monte chaque variante **en mémoire**, hors du journal de l'instance et hors des quotas des clients : elle ne laisse rien dans `data/`.
- **Piège** : `contains` cherche un texte exact. « 1 840 » avec une espace insécable (ce que produisent certains modèles) ne correspond pas à « 1 840 » avec une espace normale. Pour tolérer les deux, utilisez `matches: ["1.840"]`.

### Exemple 27.2 : comparer des variantes, avec un juge

#### Pourquoi

Un autre modèle est moins cher, un autre plus rapide. Jean Dupont veut savoir lequel peut remplacer l'actuel **sans dégrader la relance**. Deux dégradations sont à craindre : une relance qui ne reprend plus le montant du devis, et une relance qui n'est tout simplement jamais envoyée. La première est une affaire de fond, qu'un contrôle de texte attrape mal : c'est le travail d'un juge. La seconde se voit dans les outils appelés.

#### Objectif

Comparer trois variantes sur le même cas, noter le fond avec un juge d'évaluation, et voir qu'une variante qui oublie l'envoi est démasquée.

#### Mise en place

Créez `evals.yaml` :

```yaml
version: 1
name: relance
agent: relance_simple
repeat: 2
max_cost_usd: 0.50

doubles:
  envoyer_email: outils:faux_envoi

judge:
  model: SIMULE_JUGE
  tool_results: [chercher_devis]
  criteria:
    - name: fidele
      rule: Le montant et la date de la réponse figurent dans le devis, rien n'est inventé.

variants:
  - {name: actuel}
  - {name: bref, models: {main: SIMULE_BREF}}
  - {name: oublieur, models: {main: SIMULE_OUBLIEUR}}

cases:
  - name: relance-martin
    input: Relance Mme Martin pour le devis D-2026-042, sur un ton cordial.
    expect:
      status: completed
      contains: ["D-2026-042"]
      called:
        - chercher_devis
        - {name: envoyer_email, arguments: {destinataire: mme.martin@example.fr}}
```

Ce qui change par rapport à la suite précédente :

- `variants` : trois façons de jouer le cas. `actuel` ne change rien ; `bref` remplace le modèle de l'étape `main` par `SIMULE_BREF` ; `oublieur` par `SIMULE_OUBLIEUR`. Une variante peut aussi désigner un autre fichier de configuration (`config:`) ou changer le modèle d'un rôle ou d'un juge de l'agent (`models: {rediger_relance: AUTRE}`, `judge:<nom>`).
- `judge` : le **juge d'évaluation**. Il lit la demande et la réponse finale (et, avec `tool_results`, les résultats des outils cités) et note chaque critère de 0 à 1. Il juge **hors du run** : il ne le modifie pas, et sa dépense est comptée à part. Un critère a un `name`, une `rule` et un `min_score` (0,8 par défaut). Des `criteria` propres à un cas peuvent s'y ajouter.
- `repeat: 2` joue chaque cas deux fois par variante.

Un mot sur le juge de cet exemple : `SIMULE_JUGE` est un modèle simulé, qui note 1,0 une réponse contenant « 1 840 » et 0,2 sinon. Il ne comprend rien ; il rend l'exemple reproductible. Avec un vrai juge, la règle écrite dans `rule` fait le travail.

#### Exécution

```bash
uv run loom eval evals.yaml
```

```text
Suite      : relance — agent relance_simple, 1 cas, 3 variante(s), 2 répétition(s) par cas
Juge       : SIMULE_JUGE
Plafond    : 0.5000 $

Variante actuel (config de base) — 1/1 cas réussi(s), 0.019326 $ de runs, 0.001776 $ de juge
  ok       relance-martin (2/2)

Variante bref (main=SIMULE_BREF) — 0/1 cas réussi(s), 0.017772 $ de runs, 0.001760 $ de juge
  ÉCHEC    relance-martin (0/2)
      essai 1 : run 01a12680-96a3-77a7-ae49-b172b50d170c, completed
        ✗ juge : fidele ≥ 0.80 — note 0.20 — Le montant du devis n'est pas repris.
      essai 2 : run 01a12680-96cc-76ab-8300-a1a1cc871acf, completed
        ✗ juge : fidele ≥ 0.80 — note 0.20 — Le montant du devis n'est pas repris.

Variante oublieur (main=SIMULE_OUBLIEUR) — 0/1 cas réussi(s), 0.009120 $ de runs, 0.001780 $ de juge
  ÉCHEC    relance-martin (0/2)
      essai 1 : run 01a12680-96fb-720e-a7c2-976eeb1e039b, completed
        ✗ appelle envoyer_email avec {"destinataire": "mme.martin@example.fr"} — appels : chercher_devis
      essai 2 : run 01a12680-9716-7028-97df-9e3b9fb4692c, completed
        ✗ appelle envoyer_email avec {"destinataire": "mme.martin@example.fr"} — appels : chercher_devis

Bilan      : 2/6 run(s) réussi(s), 0.051534 $ dépensé(s)
Verdict    : des attendus tombent, ou des runs n'ont pas été joués
```

La variante `actuel` passe ses deux répétitions. `bref` est écartée par le juge : la relance est envoyée, mais sa confirmation ne reprend pas le montant (note 0,20 contre un seuil de 0,80). `oublieur` est écartée par un contrôle déterministe : `envoyer_email` n'a jamais été appelé (« appels : chercher_devis »). C'est exactement la dégradation que le rejeu de l'exemple 26.3 ne signalait pas.

Le même rapport, en Python, par exemple pour bloquer un déploiement. Créez `evaluer.py` :

```python
"""Joue la suite d'évaluation depuis Python, par exemple avant un déploiement."""

import asyncio
import sys

from loom_ia.access import Loom


async def main() -> int:
    async with Loom.from_config("loom.yaml") as loom:
        rapport = await loom.evaluate("evals.yaml")
        for nom in rapport.variants:
            bilan = rapport.summary(nom)
            print(f"{nom:<10} {bilan.passed_cases}/{len(bilan.cases)} cas, {bilan.cost_usd:.4f} $")
        print("tout passe :", rapport.passed)
        return 0 if rapport.passed else 1


sys.exit(asyncio.run(main()))
```

```bash
uv run python evaluer.py
```

```text
actuel     1/1 cas, 0.0193 $
bref       0/1 cas, 0.0178 $
oublieur   0/1 cas, 0.0091 $
tout passe : False
```

Pour ne jouer qu'une partie de la suite, `--case NOM` et `--variant NOM` se répètent, et `--json` donne le rapport complet pour une machine.

#### À retenir

- Les contrôles déterministes (`called`, `contains`…) sont gratuits, rapides et sans appel : mettez-les d'abord. Le juge sert pour ce qu'ils ne savent pas dire (le fond, le ton), au prix d'un appel de modèle par run.
- Un juge d'évaluation n'est pas un juge de l'agent. Les juges de l'agent (chapitre 14) tournent **dans** le run, comme configurés ; le juge de la suite note le résultat **après**.
- Le code de sortie de `loom eval` est 1 dès qu'un attendu tombe ou qu'un run n'a pas été joué. Branchez-le tel quel sur votre intégration continue.
- **Piège** : une évaluation avec un modèle simulé vérifie votre configuration, vos outils et vos attendus, pas la qualité d'un vrai modèle. Refaites-la avec les vrais modèles (et `repeat` supérieur à 1) avant de conclure quoi que ce soit sur un changement de modèle. **Non exécuté ici** avec de vrais modèles.

### Exemple 27.3 : garder des runs de référence et les rejouer

#### Pourquoi

Les cas précédents appellent le modèle à chaque fois. Or Jean Dupont a aussi des runs dont il est content : la relance de Mme Martin, validée. Il voudrait les garder tels quels et être prévenu dès qu'une modification de l'agent ne les reproduit plus, sans payer un seul appel de modèle.

#### Objectif

Enregistrer les journaux des runs d'une évaluation, puis écrire une suite de non-régression qui les rejoue, et voir ce qu'elle dit quand le prompt change.

#### Mise en place

Créez `non-regression.yaml`. Un cas de type `replay:` n'a ni demande, ni attendus, ni critères, ni client :

```yaml
version: 1
name: non-regression
agent: relance_simple

cases:
  - name: journaux-de-reference
    replay: journaux/*.jsonl
```

#### Exécution

`--export DOSSIER` écrit le journal de chaque run, un fichier par variante, cas et répétition. Gardez seulement la variante qui vous convient :

```bash
uv run loom eval evals.yaml --variant actuel --export journaux
ls journaux
```

```text
Suite      : relance — agent relance_simple, 1 cas, 1 variante(s), 2 répétition(s) par cas
Juge       : SIMULE_JUGE
Plafond    : 0.5000 $

Variante actuel (config de base) — 1/1 cas réussi(s), 0.019326 $ de runs, 0.001776 $ de juge
  ok       relance-martin (2/2)

Bilan      : 2/2 run(s) réussi(s), 0.021102 $ dépensé(s)
Verdict    : tout passe
actuel--relance-martin--1.jsonl
actuel--relance-martin--2.jsonl
```

Les fichiers se nomment `<variante>--<cas>--<n>.jsonl`. Lancez la suite de non-régression :

```bash
uv run loom eval non-regression.yaml
```

```text
Suite      : non-regression — agent relance_simple, 1 cas, 1 variante(s), 1 répétition(s) par cas

Variante base (config de base) — 1/1 cas réussi(s), 0.000000 $ de runs, 0.000000 $ de juge
  ok       journaux-de-reference (2/2 run(s) rejoué(s) à l'identique)

Bilan      : 2/2 run(s) réussi(s), 0.000000 $ dépensé(s)
Verdict    : tout passe
```

Les deux runs se rejouent à l'identique, sans appeler personne : 0 $. Ajoutez maintenant une consigne au prompt, et relancez :

```bash
printf '\nSi le client a déjà répondu, ne relance pas.\n' >> prompts/relance_simple.md
uv run loom eval non-regression.yaml
```

```text
2026-10-10 17:48:52 WARNING  loom_ia.replay.runner — Rejeu du run 01a12680-b27c-7509-9274-5a725c27d4f1 : divergence, appel de modèle n°1 (main au journal) : la requête a changé — ce qui diffère : le prompt système [run_id=01a12680-b27c-7509-9274-5a725c27d4f1 tenant_id=default]
2026-10-10 17:48:52 WARNING  loom_ia.replay.runner — Rejeu du run 01a12680-b2ab-753c-8598-ce46625bf88a : divergence, appel de modèle n°1 (main au journal) : la requête a changé — ce qui diffère : le prompt système [run_id=01a12680-b2ab-753c-8598-ce46625bf88a tenant_id=default]
Suite      : non-regression — agent relance_simple, 1 cas, 1 variante(s), 1 répétition(s) par cas

Variante base (config de base) — 0/1 cas réussi(s), 0.000000 $ de runs, 0.000000 $ de juge
  ÉCHEC    journaux-de-reference (0/2 run(s) rejoué(s) à l'identique)
      journaux/actuel--relance-martin--1.jsonl, run 01a12680-b27c-7509-9274-5a725c27d4f1 :
        ✗ se rejoue à l'identique — appel de modèle n°1 (main au journal) : la requête a changé ; ce qui diffère : le prompt système
      journaux/actuel--relance-martin--2.jsonl, run 01a12680-b2ab-753c-8598-ce46625bf88a :
        ✗ se rejoue à l'identique — appel de modèle n°1 (main au journal) : la requête a changé ; ce qui diffère : le prompt système

Bilan      : 0/2 run(s) réussi(s), 0.000000 $ dépensé(s)
Verdict    : des attendus tombent, ou des runs n'ont pas été joués
```

Les avertissements du début (sortie d'erreur) viennent du moteur de rejeu, le rapport suit. Chaque journal en échec est nommé, avec l'appel où la requête a changé et la partie qui diffère. Retirez la consigne ajoutée :

```bash
head -n -2 prompts/relance_simple.md > /tmp/prompt.md && mv /tmp/prompt.md prompts/relance_simple.md
```

#### À retenir

- Un cas `replay:` passe si chaque run fini de chaque journal du motif se rejoue à l'identique. Le motif est relatif au fichier de la suite. Un motif qui ne correspond à aucun fichier fait tomber le cas, pour qu'un dossier vidé par erreur ne passe pas pour un succès.
- C'est le filet de sécurité le moins cher qui soit : aucun modèle, aucun outil, aucune dépense. Il détecte tout changement de prompt, d'outils proposés, de modèle ou de réglages.
- Il ne dit pas qu'un changement est mauvais, seulement qu'il est **visible**. Quand le changement est voulu, régénérez les journaux de référence (`loom eval … --export`) et relisez le diff de comportement avec une évaluation à variantes.
- Un cas `replay:` peut cohabiter avec des cas normaux dans la même suite, et se joue pour chaque variante.

---

## 28. Tests et non-régression

Une évaluation joue l'agent. Un test pytest garde la même discipline dans le dépôt : il tourne à chaque commit, sans réseau, sans clé et sans rien envoyer. Le module `loom_ia.testing` fournit ce qu'il faut : un banc qui monte l'agent avec des modèles scriptés et des outils doublés, et une assertion qui rejoue des journaux de référence.

### Exemple 28.1 : un premier test d'agent avec le banc

#### Pourquoi

Jean Dupont retouche son prompt de relance une fois par mois. À chaque fois, la même crainte : l'agent va-t-il encore chercher le devis avant d'écrire à la cliente, et enverra-t-il l'e-mail au bon destinataire ? Il veut que `uv run pytest` le lui dise en une seconde.

#### Objectif

Écrire trois tests : un qui joue le scénario de la configuration, un qui impose les réponses du modèle, un qui vérifie qu'un orchestrateur qui oublie d'envoyer l'e-mail est détecté.

#### Mise en place

`pytest` et `pytest-asyncio` ont été installés au chapitre 25 (`uv add --dev pytest pytest-asyncio`). Ajoutez à la fin de `pyproject.toml` :

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
pythonpath = ["."]
```

`asyncio_mode = "auto"` évite de décorer chaque test `async`. `pythonpath = ["."]` permet aux tests d'importer `outils.py`, à la racine du projet.

Créez le dossier `tests/` et le fichier `tests/test_relance.py` :

```python
import pytest

from loom_ia.core.model import Message
from loom_ia.testing import Bench, ScriptedModel, tool_call_message
from outils import faux_envoi

DEMANDE = "Relance Mme Martin pour le devis D-2026-042, sur un ton cordial."
EMAIL = {
    "destinataire": "mme.martin@example.fr",
    "objet": "Votre devis D-2026-042",
    "corps": "Bonjour Mme Martin, où en êtes-vous du devis D-2026-042 (1 840 € TTC) ?",
}


async def test_la_relance_du_scenario_de_la_config() -> None:
    async with Bench("loom.yaml", tools={"envoyer_email": faux_envoi}) as banc:
        result = await banc.run("relance_simple", DEMANDE)
        banc.expect(
            result,
            status="completed",
            contains=["1 840"],
            called=["chercher_devis", "envoyer_email"],
        )


async def test_la_relance_part_avec_les_bons_arguments() -> None:
    orchestrateur = ScriptedModel(
        tool_call_message(("c1", "chercher_devis", {"numero": "D-2026-042"})),
        tool_call_message(("c2", "envoyer_email", EMAIL)),
        Message.assistant("La relance du devis D-2026-042 (1 840 € TTC) est partie."),
    )
    models = {"SIMULE_RELANCE": orchestrateur}
    async with Bench("loom.yaml", models=models, tools={"envoyer_email": faux_envoi}) as banc:
        result = await banc.run("relance_simple", DEMANDE)
        banc.expect(
            result,
            status="completed",
            contains=["1 840"],
            called=["chercher_devis", {"name": "envoyer_email", "arguments": EMAIL}],
        )
        assert len(banc.calls("envoyer_email")) == 1


async def test_un_orchestrateur_qui_oublie_l_envoi_est_vu() -> None:
    oublieux = ScriptedModel(
        tool_call_message(("c1", "chercher_devis", {"numero": "D-2026-042"})),
        Message.assistant("Le devis D-2026-042 est en attente."),
    )
    async with Bench("loom.yaml", models={"SIMULE_RELANCE": oublieux}) as banc:
        result = await banc.run("relance_simple", DEMANDE)
        with pytest.raises(AssertionError, match="envoyer_email"):
            banc.expect(result, called=["envoyer_email"])
```

Le banc `Bench` prend la configuration (un chemin, ou un objet `LoomConfig`) et trois réglages utiles :

- `models={"ID": modèle}` remplace le modèle déclaré sous cet identifiant par un `ScriptedModel`, qui rend ses réponses dans l'ordre : `tool_call_message((id, nom, arguments))` pour demander un outil, `Message.assistant(texte)` pour répondre.
- `tools={"nom": fonction}` remplace un outil par une doublure.
- `register={"nom": objet}` rend référençable un objet que la configuration ne trouverait pas dans ses `imports`.

`banc.run(agent, message)` joue le run ; `banc.expect(result, ...)` pose les mêmes contrôles qu'une suite d'évaluation (`status`, `contains`, `not_contains`, `matches`, `fields`, `called`, `not_called`) et lève un `AssertionError` qui dit ce qui est tombé. `banc.calls("outil")` rend les appels d'un outil, avec leurs arguments, leur sort et leur résultat.

#### Exécution

```bash
uv run pytest -v
```

```text
============================= test session starts ==============================
platform linux -- Python 3.14.6, pytest-9.1.1, pluggy-1.6.0 -- /home/denis/qualite/.venv/bin/python3
cachedir: .pytest_cache
rootdir: /home/denis/qualite
configfile: pyproject.toml
testpaths: tests
plugins: asyncio-1.4.0, anyio-4.15.1
asyncio: mode=Mode.AUTO, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collecting ... collected 3 items

tests/test_relance.py::test_la_relance_du_scenario_de_la_config PASSED   [ 33%]
tests/test_relance.py::test_la_relance_part_avec_les_bons_arguments PASSED [ 66%]
tests/test_relance.py::test_un_orchestrateur_qui_oublie_l_envoi_est_vu PASSED [100%]

============================== 3 passed in 0.51s ===============================
```

Voici ce que le banc dit quand le test échoue, par exemple si l'on retire `envoyer_email` du script du modèle et que l'on attend en plus « 1 840 » dans la réponse :

```text
Banc : 2 contrôle(s) tombé(s) sur 2, run 01a12681-fb21-74d0-9ac0-85acf4edb63a (completed)
  ✗ contient « 1 840 »
  ✗ appelle envoyer_email — appels : chercher_devis
  texte :
    Le devis D-2026-042 est en attente.
```

Chaque contrôle tombé est nommé, avec ce qui a été observé à la place (les outils réellement appelés, le texte de la réponse).

#### À retenir

- Le premier test n'impose aucun modèle : il joue le scénario du modèle simulé de `loom.yaml`. C'est le plus simple, et il vérifie que la configuration monte et que les outils sont appelés. Les deux suivants imposent le comportement du modèle, ce qui permet de tester des cas que le scénario de la configuration ne contient pas.
- Un `ScriptedModel` ne voit pas le prompt : il rend ce qu'on lui a écrit. Les tests vérifient donc vos outils, vos politiques et votre configuration, pas la qualité du prompt. Pour juger un prompt, il faut un vrai modèle et une évaluation (chapitre 27).
- Le banc monte l'agent à part : rien n'est écrit dans `data/`, et aucun fichier de la configuration n'est touché.
- **Piège** : sans `asyncio_mode = "auto"` (ou un `@pytest.mark.asyncio` sur chaque test), pytest ne sait pas exécuter les tests `async def`, et la suite ne teste rien de ce que vous croyez.

### Exemple 28.2 : les garde-fous du banc

#### Pourquoi

Un test qui enverrait vraiment un e-mail à Mme Martin, ou qui appellerait un vrai modèle à chaque `pytest`, serait pire que pas de test. Jean Dupont veut être certain que le banc ne peut pas le faire par accident.

#### Objectif

Vérifier, par deux tests, qu'un outil à effets de bord ne s'exécute jamais sans doublure et qu'un vrai modèle non remplacé est refusé.

#### Mise en place

Créez `tests/test_securite.py` :

```python
from pathlib import Path

from loom_ia.agents import AgentSpec, MainRole, PythonTool
from loom_ia.config import LoomConfig
from loom_ia.core.model import Message, ModelSpec
from loom_ia.testing import Bench, ScriptedModel, tool_call_message
from outils import chercher_devis

DEMANDE = "Relance Mme Martin pour le devis D-2026-042, sur un ton cordial."
EMAIL = {
    "destinataire": "mme.martin@example.fr",
    "objet": "Votre devis D-2026-042",
    "corps": "Bonjour Mme Martin, où en êtes-vous du devis D-2026-042 (1 840 € TTC) ?",
}


def lignes_envoyees() -> int:
    boite = Path("boite_envoi.txt")
    return len(boite.read_text().splitlines()) if boite.exists() else 0


async def test_sans_double_l_email_n_est_jamais_envoye() -> None:
    avant = lignes_envoyees()
    orchestrateur = ScriptedModel(
        tool_call_message(("c1", "chercher_devis", {"numero": "D-2026-042"})),
        tool_call_message(("c2", "envoyer_email", EMAIL)),
        Message.assistant("La relance est partie."),
    )
    async with Bench("loom.yaml", models={"SIMULE_RELANCE": orchestrateur}) as banc:
        await banc.run("relance_simple", DEMANDE)
        [appel] = banc.calls("envoyer_email")
        assert appel.fate == "refused"
    assert lignes_envoyees() == avant


async def test_un_modele_reel_n_est_pas_appele() -> None:
    config = LoomConfig(
        version=1,
        models=(ModelSpec(id="HAIKU", sdk="anthropic", model="claude-haiku-5-5"),),
        agents=(
            AgentSpec(
                name="reel",
                main=MainRole(model="HAIKU", system="Tu relances des clients."),
                tools=(PythonTool(python="chercher_devis"),),
            ),
        ),
    )
    async with Bench(config, register={"chercher_devis": chercher_devis}) as banc:
        result = await banc.run("reel", DEMANDE)
        assert result.status == "failed"
        assert "n'est pas remplacé" in (result.error or "")
```

Le second test construit la configuration en Python (voir le chapitre 6) : un agent qui utiliserait le modèle réel `HAIKU`. Les noms `AgentSpec`, `MainRole` et `PythonTool` viennent de `loom_ia.agents`.

#### Exécution

```bash
uv run pytest -v tests/test_securite.py
```

```text
============================= test session starts ==============================
platform linux -- Python 3.14.6, pytest-9.1.1, pluggy-1.6.0 -- /home/denis/qualite/.venv/bin/python3
cachedir: .pytest_cache
rootdir: /home/denis/qualite
configfile: pyproject.toml
plugins: asyncio-1.4.0, anyio-4.15.1
asyncio: mode=Mode.AUTO, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collecting ... collected 2 items

tests/test_securite.py::test_sans_double_l_email_n_est_jamais_envoye PASSED [ 50%]
tests/test_securite.py::test_un_modele_reel_n_est_pas_appele PASSED      [100%]

============================== 2 passed in 0.52s ===============================
```

Dans le second test, le message exact de l'erreur est :

```text
failed model.auth
Banc : le modèle HAIKU (anthropic, claude-haiku-5-5) est réel et n'est pas remplacé — le remplacer (models={…}), ou l'appeler pour de vrai (real_models=True)
```

#### À retenir

- Un outil à effets de bord sans doublure n'est jamais exécuté : son appel reçoit le sort `refused`, et le modèle reçoit une erreur qui le dit. Avec une doublure, l'approbation est accordée automatiquement, puisque rien ne part pour de vrai.
- Un modèle réel que vous n'avez pas remplacé est refusé (`model.auth`, « est réel et n'est pas remplacé »). Pour l'appeler quand même, `real_models=True` : réservez-le à un test d'intégration lancé à la main, avec la clé dans l'environnement.
- Ces deux garde-fous font du banc un endroit sûr : on peut y essayer n'importe quel agent sans craindre ni la facture ni l'e-mail.
- **Non exécuté ici** : un test avec `real_models=True`, faute de clé d'API.

### Exemple 28.3 : des journaux de référence pour ne rien casser

#### Pourquoi

Les tests précédents imposent des réponses. Mais le plus précieux, ce sont les **vrais runs** que Jean Dupont a validés. Les rejouer à chaque commit prouve que ni le prompt, ni les outils, ni la configuration n'ont changé sous leurs pieds, avec aucun appel de modèle.

#### Objectif

Rejouer dans pytest les journaux enregistrés par l'évaluation du chapitre 27, fabriquer un journal de référence à partir d'un test, et en exporter un depuis une session réelle.

#### Mise en place

Créez `tests/test_non_regression.py` :

```python
from pathlib import Path

from loom_ia.testing import Bench, assert_replays
from outils import faux_envoi

DEMANDE = "Relance Mme Martin pour le devis D-2026-042, sur un ton cordial."


async def test_les_runs_de_reference_se_rejouent() -> None:
    journaux = sorted(Path("journaux").glob("*.jsonl"))
    assert journaux, "aucun journal de référence dans journaux/"
    await assert_replays("loom.yaml", *journaux)


async def test_le_journal_du_banc_devient_un_test_de_non_regression(tmp_path: Path) -> None:
    async with Bench("loom.yaml", tools={"envoyer_email": faux_envoi}) as banc:
        result = await banc.run("relance_simple", DEMANDE)
        journal = await banc.export(result, tmp_path / "relance-042.jsonl")
    await assert_replays("loom.yaml", journal)
```

Le dossier `journaux/` a été rempli à l'exemple 27.3 (`loom eval evals.yaml --variant actuel --export journaux`). Le second test montre l'autre façon d'obtenir un journal : `banc.export(result, chemin)` écrit le journal d'un run du banc, que l'on peut aussitôt rejouer.

#### Exécution

```bash
uv run pytest -v
```

```text
============================= test session starts ==============================
platform linux -- Python 3.14.6, pytest-9.1.1, pluggy-1.6.0 -- /home/denis/qualite/.venv/bin/python3
cachedir: .pytest_cache
rootdir: /home/denis/qualite
configfile: pyproject.toml
testpaths: tests
plugins: asyncio-1.4.0, anyio-4.15.1
asyncio: mode=Mode.AUTO, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collecting ... collected 7 items

tests/test_non_regression.py::test_les_runs_de_reference_se_rejouent PASSED [ 14%]
tests/test_non_regression.py::test_le_journal_du_banc_devient_un_test_de_non_regression PASSED [ 28%]
tests/test_relance.py::test_la_relance_du_scenario_de_la_config PASSED   [ 42%]
tests/test_relance.py::test_la_relance_part_avec_les_bons_arguments PASSED [ 57%]
tests/test_relance.py::test_un_orchestrateur_qui_oublie_l_envoi_est_vu PASSED [ 71%]
tests/test_securite.py::test_sans_double_l_email_n_est_jamais_envoye PASSED [ 85%]
tests/test_securite.py::test_un_modele_reel_n_est_pas_appele PASSED      [100%]

============================== 7 passed in 0.70s ===============================
```

Modifiez maintenant le prompt (une ligne de plus) et relancez le test de non-régression :

```bash
printf '\nSi le client a déjà répondu, ne relance pas.\n' >> prompts/relance_simple.md
uv run pytest -q --tb=short tests/test_non_regression.py
```

```text
F.                                                                       [100%]
=================================== FAILURES ===================================
____________________ test_les_runs_de_reference_se_rejouent ____________________
tests/test_non_regression.py:12: in test_les_runs_de_reference_se_rejouent
    await assert_replays("loom.yaml", *journaux)
.venv/lib/python3.14/site-packages/loom_ia/testing/replays.py:61: in assert_replays
    raise AssertionError(
E   AssertionError: Rejeu : 2 écart(s) sur 2 journal(aux)
E     journaux/actuel--relance-martin--1.jsonl, run 01a12680-b27c-7509-9274-5a725c27d4f1 : appel de modèle n°1 (main au journal) : la requête a changé ; ce qui diffère : le prompt système
E     journaux/actuel--relance-martin--2.jsonl, run 01a12680-b2ab-753c-8598-ce46625bf88a : appel de modèle n°1 (main au journal) : la requête a changé ; ce qui diffère : le prompt système
------------------------------ Captured log call -------------------------------
WARNING  loom_ia.replay.runner:runner.py:402 Rejeu du run 01a12680-b27c-7509-9274-5a725c27d4f1 : divergence, appel de modèle n°1 (main au journal) : la requête a changé — ce qui diffère : le prompt système
WARNING  loom_ia.replay.runner:runner.py:402 Rejeu du run 01a12680-b2ab-753c-8598-ce46625bf88a : divergence, appel de modèle n°1 (main au journal) : la requête a changé — ce qui diffère : le prompt système
=========================== short test summary info ============================
FAILED tests/test_non_regression.py::test_les_runs_de_reference_se_rejouent
1 failed, 1 passed in 0.56s
```

Retirez la ligne ajoutée (`head -n -2 prompts/relance_simple.md > /tmp/prompt.md && mv /tmp/prompt.md prompts/relance_simple.md`). Le test échoue et dit pourquoi : chaque journal en écart est nommé, avec l'appel et la partie de la requête qui a changé. Le second test, qui se fabrique son journal avec le prompt courant, passe toujours.

Les mêmes journaux se rejouent en ligne de commande, sans pytest :

```bash
uv run loom replay --journal journaux/actuel--relance-martin--1.jsonl
```

```text
Journal    : journaux/actuel--relance-martin--1.jsonl — 1 run(s) à rejouer

Run        : 01a12680-b27c-7509-9274-5a725c27d4f1 (agent relance_simple, client default)
Session    : 01a12680-b27c-7509-9274-5a725c27d4f1
Issue      : completed au journal, completed au rejeu
Modèles    : 3 appel(s) au journal, 3 servi(s) au rejeu
Outils     : 2 résultat(s) au journal (rôles à part), 2 servi(s) au rejeu
Identique  : le run se rejoue tel qu'il a été, appel par appel.

Bilan      : 1/1 run(s) identique(s)
```

Enfin, un run **réel** devient un journal de référence avec `loom sessions export`. Cherchez la session (la liste ci-dessous est abrégée à ses premières lignes), puis exportez-la :

```bash
uv run loom sessions list
uv run loom sessions export 01a1267b-1639-73c5-bfe4-6a953e9ff10e --out reference-042.jsonl
```

```text
01a1267f-7cee-7719-b8f6-38687641850b     21 événements   2026-10-10 17:47
01a1267e-cd1c-7101-bad3-7ba44088e094     21 événements   2026-10-10 17:46
01a1267e-cadc-750e-95f0-dd2f96da607f     21 événements   2026-10-10 17:46
01a1267e-ad25-71f3-9a51-593221a19ab6     21 événements   2026-10-10 17:46
01a1267e-aa81-719c-9bc4-ecb4adfcf1b0     21 événements   2026-10-10 17:46
01a1267e-8227-7000-bd19-72f68e3f6584     34 événements   2026-10-10 17:46
01a1267e-734d-7229-868a-02ff01444400     34 événements   2026-10-10 17:46
01a1267c-c040-744e-a8c5-25e2301a6e42     34 événements   2026-10-10 17:44
34 événements écrits dans reference-042.jsonl
```

Le fichier exporté est lui aussi rejouable :

```bash
uv run loom replay --journal reference-042.jsonl
```

```text
Journal    : reference-042.jsonl — 1 run(s) à rejouer

Run        : 01a1267b-1639-73c5-bfe4-6a953e9ff10e (agent relance_simple, client default)
Session    : 01a1267b-1639-73c5-bfe4-6a953e9ff10e
Issue      : completed au journal, completed au rejeu
Modèles    : 3 appel(s) au journal, 3 servi(s) au rejeu
Outils     : 2 résultat(s) au journal (rôles à part), 2 servi(s) au rejeu
Identique  : le run se rejoue tel qu'il a été, appel par appel.

Bilan      : 1/1 run(s) identique(s)
```

Rangez les fichiers que vous voulez garder dans `journaux/` : le test et la suite `non-regression.yaml` les trouveront.

#### À retenir

- Un journal de référence est un **fichier du dépôt**. Il contient les contenus du run (demande, e-mail, nom de la cliente) : n'y mettez que des cas sans données personnelles réelles, ou exportez-les depuis un environnement de test.
- Trois sources de journaux : `loom eval … --export` (runs d'une évaluation), `banc.export()` (runs d'un test), `loom sessions export` (runs réels). `loom sessions export` écrit tous les événements de la session ; `loom replay --journal` rejoue chacun des runs finis qu'elle contient.
- Que faire quand le test échoue parce que le changement est voulu : relisez le changement avec une variante (chapitre 26), puis régénérez les journaux de référence. Ne rangez jamais un journal qui ne se rejoue pas : le test serait rouge pour toujours.
- Pour brancher tout cela sur une intégration continue : `uv run pytest` et `uv run loom eval evals.yaml` sortent en code non nul au premier écart.

---

## 29. Sources d'outils : paquet, sandbox `forge`, mémoire `loom-notes`

Jusqu'ici, un outil était soit une fonction Python du projet (`@tool`), soit un serveur MCP. Une troisième voie existe : une **source d'outils**, un paquet Python installé qui déclare un point d'entrée et fournit ses outils au début de chaque run. C'est ainsi que Loom-IA livre ses propres sources (la sandbox `forge`), et c'est ainsi que vous pouvez partager un jeu d'outils entre plusieurs projets.

### Exemple 29.1 : empaqueter les outils du carnet de devis

#### Pourquoi

La Plomberie Dupont a maintenant trois projets Loom-IA (relances, devis, planning), et tous ont besoin de lire le carnet de devis. Copier `chercher_devis` dans chacun, c'est la corriger trois fois le jour où le carnet change de format. Jean Dupont veut un paquet `carnet-devis`, installé là où il sert.

#### Objectif

Écrire un paquet Python qui déclare un point d'entrée `loom_ia.tools`, l'installer dans `qualite/`, et voir un agent appeler son outil.

#### Mise en place

Le paquet est un projet frère de `qualite/` :

```bash
cd ..
uv init --lib --python 3.14 carnet-devis
cd carnet-devis
uv add loom-ia
```

```text
Initialized project `carnet-devis` at `/home/denis/carnet-devis`
```

`--lib` crée un paquet avec une disposition `src/`. Remplacez son `pyproject.toml`. La seule nouveauté est la section `[project.entry-points."loom_ia.tools"]` : elle associe le nom `carnet` à la fabrique `carnet_devis:fabrique`. (Les valeurs de `authors` viennent de votre identité Git ; gardez les vôtres.)

```toml
[project]
name = "carnet-devis"
version = "0.1.0"
description = "Source d'outils Loom-IA : le carnet de devis de la Plomberie Dupont."
readme = "README.md"
authors = [
    { name = "Jean Dupont", email = "jean@plomberie-dupont.example" }
]
requires-python = ">=3.14"
dependencies = [
    "loom-ia>=2.0.0",
]

[project.entry-points."loom_ia.tools"]
carnet = "carnet_devis:fabrique"

[build-system]
requires = ["uv_build>=0.11.32,<0.12.0"]
build-backend = "uv_build"
```

Remplacez `src/carnet_devis/__init__.py` :

```python
"""Source d'outils Loom-IA : le carnet de devis, lu dans un fichier JSON."""

import json
import sys
from collections.abc import AsyncGenerator, Mapping, Sequence
from contextlib import asynccontextmanager
from pathlib import Path

from pydantic import JsonValue

from loom_ia.core.ports import SourceContext, Tool
from loom_ia.tools import tool


def _dire(message: str) -> None:
    # Traces de démonstration : elles montrent quand Loom-IA appelle la source.
    print(f"[carnet] {message}", file=sys.stderr)


class Carnet:
    """Une source d'outils : elle fournit ses outils au début de chaque run."""

    name = "carnet"
    required = False

    def __init__(self, fichier: Path) -> None:
        self.fichier = fichier

    @asynccontextmanager
    async def open(self, context: SourceContext) -> AsyncGenerator[Sequence[Tool]]:
        _dire(f"ouverture pour le run {context.run_id[:8]} (client {context.tenant_id})")
        devis = json.loads(self.fichier.read_text(encoding="utf-8"))

        @tool
        def chercher_devis(numero: str) -> dict[str, str | float]:
            """Cherche un devis dans le carnet de la Plomberie Dupont par son numéro."""
            if numero not in devis:
                raise ValueError(f"Devis {numero} absent du carnet")
            return {"numero": numero, **devis[numero]}

        try:
            yield [chercher_devis]
        finally:
            _dire(f"fermeture du run {context.run_id[:8]}")

    async def aclose(self) -> None:
        _dire("source fermée avec l'instance")


def fabrique(
    *,
    name: str,
    params: Mapping[str, JsonValue],
    secrets: Mapping[str, str],
    base_dir: Path,
) -> Carnet:
    """Point d'entrée `carnet` : vérifie les paramètres et rend la source, sans rien lire."""
    inconnus = sorted(set(params) - {"fichier"})
    if inconnus:
        raise ValueError(f"paramètres inconnus : {', '.join(inconnus)} (attendu : fichier)")
    fichier = params.get("fichier")
    if not isinstance(fichier, str):
        raise ValueError("paramètre 'fichier' obligatoire (chemin du carnet JSON)")
    _dire(f"fabrique pour {name!r}")
    return Carnet(base_dir / fichier)
```

Le contrat d'une source tient en peu de choses :

- La **fabrique** (`fabrique`) reçoit des arguments nommés : `name` (le nom donné dans la configuration), `params` (les paramètres de la configuration, remis tels quels), `secrets` (les secrets du client) et `base_dir` (le dossier de la configuration). Elle **vérifie** ses paramètres et rend la source, sans rien lire ni ouvrir. Une erreur levée ici refuse le chargement de la configuration, avec ce message.
- La **source** a un `name`, un booléen `required` et une méthode `open(context)`, un gestionnaire de contexte asynchrone. Il est appelé **au début de chaque run**, reçoit le contexte (`run_id`, `tenant_id`…) et fournit la liste des outils du run. Ce qui est après le `yield` s'exécute à la fin du run. Une méthode `aclose()` facultative est appelée quand l'instance Loom se ferme.
- Les outils sont ceux de `@tool`, comme dans le chapitre 2. Ils peuvent donc capturer ce que `open()` a préparé (ici, le contenu du carnet), propre à ce run.

Revenez dans `qualite/` et installez le paquet en mode éditable :

```bash
cd ../qualite
uv add --editable ../carnet-devis
```

```text
Resolved 61 packages in 185ms
   Building carnet-devis @ file:///home/denis/carnet-devis
      Built carnet-devis @ file:///home/denis/carnet-devis
Prepared 1 package in 7ms
Installed 1 package in 0.75ms
 + carnet-devis==0.1.0 (from file:///home/denis/carnet-devis)
```

Créez le carnet, `carnet.json` :

```json
{
  "D-2026-042": {
    "client": "Mme Martin",
    "objet": "Remplacement du chauffe-eau (200 L)",
    "montant_ttc": 1840.0,
    "envoye_le": "2026-09-02"
  },
  "D-2026-043": {
    "client": "M. Bernard",
    "objet": "Remplacement d'un robinet thermostatique",
    "montant_ttc": 269.35,
    "envoye_le": "2026-09-09"
  }
}
```

Remplacez `loom.yaml` par la version complète suivante. Elle ajoute le modèle simulé `SIMULE_CARNET` et la déclaration de la source :

```yaml
version: 1

imports: [outils]
agents_dir: agents/
prompts_dir: prompts/

models:
  - id: SIMULE_RELANCE
    sdk: fake
    model: fake-relance
    pricing: {input: 3.0, output: 15.0}
    params:
      script:
        - text: Je cherche le devis.
          tool_calls:
            - name: chercher_devis
              arguments: {numero: D-2026-042}
        - text: J'envoie la relance.
          tool_calls:
            - name: envoyer_email
              arguments:
                destinataire: mme.martin@example.fr
                objet: Votre devis D-2026-042
                corps: >-
                  Bonjour Mme Martin, nous revenons vers vous au sujet du devis D-2026-042
                  (remplacement du chauffe-eau, 1 840 € TTC) envoyé le 2 septembre.
                  Restons à votre disposition. La Plomberie Dupont
        - text: La relance du devis D-2026-042 (1 840 € TTC) est partie chez Mme Martin.
  - id: SIMULE_BREF
    sdk: fake
    model: fake-bref
    pricing: {input: 3.0, output: 15.0}
    params:
      script:
        - text: Je cherche le devis.
          tool_calls:
            - name: chercher_devis
              arguments: {numero: D-2026-042}
        - text: J'envoie une relance brève.
          tool_calls:
            - name: envoyer_email
              arguments:
                destinataire: mme.martin@example.fr
                objet: Votre devis D-2026-042
                corps: Bonjour Mme Martin, avez-vous pu regarder le devis D-2026-042 (1 840 € TTC) ?
        - text: Relance brève envoyée à Mme Martin pour le devis D-2026-042.
  - id: SIMULE_OUBLIEUR
    sdk: fake
    model: fake-oublieur
    pricing: {input: 3.0, output: 15.0}
    params:
      script:
        - text: Je cherche le devis.
          tool_calls:
            - name: chercher_devis
              arguments: {numero: D-2026-042}
        - text: Le devis D-2026-042 (1 840 € TTC) est toujours en attente depuis le 2 septembre.
  - id: SIMULE_JUGE
    sdk: fake
    model: fake-juge
    pricing: {input: 1.0, output: 5.0}
    params:
      script:
        - with_text: "1 840"
          tool_calls:
            - name: verdict
              arguments:
                criteria:
                  - {name: fidele, score: 1.0, reason: Le montant de 1 840 € figure dans le devis.}
        - without_text: "1 840"
          tool_calls:
            - name: verdict
              arguments:
                criteria:
                  - {name: fidele, score: 0.2, reason: Le montant du devis n'est pas repris.}
  - id: SIMULE_CARNET
    sdk: fake
    model: fake-carnet
    pricing: {input: 3.0, output: 15.0}
    params:
      script:
        - text: Je consulte le carnet.
          tool_calls:
            - name: carnet__chercher_devis
              arguments: {numero: D-2026-042}
        - text: Le devis D-2026-042 de Mme Martin (1 840 € TTC) est en attente depuis le 2 septembre.

tool_sources:
  - name: carnet
    entry_point: carnet
    params: {fichier: carnet.json}

telemetry:
  logging: {level: WARNING}

storage:
  events: {backend: jsonl, path: data}
```

Dans `tool_sources`, `name` est le nom de la source dans cette configuration (il préfixe ses outils), `entry_point` le nom du point d'entrée, et `params` ce que la fabrique recevra. Créez `agents/carnet.yaml`. Un agent référence la source par `- source: carnet` :

```yaml
name: carnet
description: Répond aux questions sur les devis du carnet de la Plomberie Dupont.

main:
  model: SIMULE_CARNET
  system: Tu réponds aux questions sur les devis, en une phrase, d'après le carnet.

max_iterations: 4

tools:
  - source: carnet
```

#### Exécution

```bash
uv run loom validate
```

```text
Config     : loom.yaml
Profil     : aucun (ni --profile, ni LOOM_PROFILE, ni 'profile:') ; surcharges : aucune
Modèles    : SIMULE_RELANCE, SIMULE_BREF, SIMULE_OUBLIEUR, SIMULE_JUGE, SIMULE_CARNET
Agents     : carnet, relance_simple
Outils     : chercher_devis, envoyer_email
Source     : carnet → point d'entrée carnet
Paquets    : carnet (carnet-devis 0.1.0, utilisé), forge (loom-ia 2.0.0) (groupe loom_ia.tools)
Journal    : jsonl (/home/denis/qualite/data)
Artefacts  : local (/home/denis/qualite/data/.artifacts)
Idempotence: journal
File       : asyncio
Bus        : memory (les nouvelles ne sortent pas de ce process)
Chiffrement: aucun (contenus en clair au repos)
Rétention  : aucune (rien ne s'efface)
Clés d'API : aucune (API REST ouverte)
[carnet] fabrique pour 'carnet'
  carnet : modèle SIMULE_CARNET, 0 outil(s) Python
[carnet] ouverture pour le run validate (client default)
    source carnet : carnet__chercher_devis
[carnet] fermeture du run validate
  relance_simple : modèle SIMULE_RELANCE, 2 outil(s) Python
[carnet] source fermée avec l'instance

2 agent(s) monté(s) sans erreur.
```

Trois lignes méritent d'être lues. `Paquets` liste les paquets qui déclarent le groupe `loom_ia.tools` : `carnet` (utilisé par un agent) et `forge` (livré avec Loom-IA). `source carnet : carnet__chercher_devis` montre le nom que le modèle verra : `<source>__<outil>`. Les lignes `[carnet]` sont celles que notre source écrit sur la sortie d'erreur : la fabrique est appelée **une fois par agent**, `open` **une fois par run** (ici, celui de `validate`), `aclose` à la fin.

Lancez l'agent, puis un agent qui n'utilise pas la source :

```bash
uv run loom run carnet "Où en est le devis D-2026-042 ?"
```

```text
[carnet] fabrique pour 'carnet'
[carnet] ouverture pour le run 01a12683 (client default)
[carnet] fermeture du run 01a12683
[carnet] source fermée avec l'instance
Le devis D-2026-042 de Mme Martin (1 840 € TTC) est en attente depuis le 2 septembre.

Statut     : completed · itérations : 2 · tokens : 620/115 · coût : 0.0036 $
Run        : 01a12683-9312-72c1-ab83-050cfc8cc9b5
```

```bash
uv run loom run relance_simple "Relance Mme Martin pour le devis D-2026-042."
```

```text
—

Statut     : paused · itérations : 2 · tokens : 1128/194 · coût : 0.0063 $
Run        : 01a12683-95d7-7345-a2e8-9078d57fd9e4
En attente : envoyer_email (fake_1_0) — loom approve 01a12683-95d7-7345-a2e8-9078d57fd9e4 --call fake_1_0
```

Le second run ne produit aucune ligne `[carnet]` : tant qu'aucun agent n'utilise la source, son paquet n'est même pas importé. (Ce run s'est arrêté sur l'approbation de l'e-mail : vous pouvez le refuser avec `loom reject`, ou l'ignorer.)

Deux erreurs fréquentes, avec leurs messages. Un point d'entrée que personne ne déclare (remplacez `entry_point: carnet` par `carnet-ancien` dans une copie de la configuration) :

```text
Rétention  : aucune (rien ne s'efface)
Clés d'API : aucune (API REST ouverte)
Configuration : Point d'entrée 'carnet-ancien' absent du groupe loom_ia.tools : aucun paquet installé ne le déclare (installés : carnet, forge)
```

Un paramètre que la fabrique ne connaît pas (`fichiers` au lieu de `fichier`) :

```text
Chiffrement: aucun (contenus en clair au repos)
Rétention  : aucune (rien ne s'efface)
Clés d'API : aucune (API REST ouverte)
Configuration : Agent 'carnet', source 'carnet' (carnet-devis 0.1.0, carnet_devis:fabrique) : refusée par sa fabrique — paramètres inconnus : fichiers (attendu : fichier)
```

Les deux sortent avec le code 2 : la configuration est en cause, aucun run n'a eu lieu.

#### À retenir

- Pour qu'un projet utilise une source, trois choses : le paquet est installé dans l'environnement du projet (`uv add`), le point d'entrée est déclaré dans `tool_sources`, et un agent la référence dans `tools`.
- Une référence d'agent accepte des réglages : `alias` (un autre préfixe : `alias: registre` donne `registre__chercher_devis`), `include` ou `exclude` (ne garder que certains outils), `required` (déclare que le run ne peut pas se passer de la source : si elle est indisponible, l'événement `tool.source_unavailable` le signale comme tel, ce que vous retrouvez dans le journal et dans `loom inspect`) et `tools` (réécrire la description ou l'approbation d'un outil pour cet agent).
- La fabrique ne travaille pas : elle est appelée à chaque chargement de la configuration, y compris par `loom validate`. Ouvrez vos connexions dans `open()`, pas dans la fabrique.
- Un secret (clé d'API, mot de passe) ne s'écrit jamais dans `params` : la fabrique reçoit la table `secrets` du client. Voir les clients et leurs secrets au chapitre 22.
- **Piège** : un paquet installé mais non référencé n'est pas importé, donc ses erreurs d'import ne se voient pas à la validation. Référencez-le dans un agent, même minimal, pour que `loom validate` le charge.

### Exemple 29.2 : forger des outils dans une sandbox (`forge`)

> **Non exécuté ici** : l'exécution d'un outil forgé demande une micro-VM Firecracker, donc l'accès à KVM (`/dev/kvm`) et des droits administrateur (`sudo`) pour la construire. La machine sur laquelle ce guide a été vérifié n'a ni l'un ni l'autre. Ont été exécutés : le chargement de la source, ses contrôles de configuration, les contrôles que l'hôte fait sur le code d'un outil, la lecture du catalogue, et l'erreur que l'on obtient quand la VM manque. Les commandes de construction de la VM, elles, sont données d'après le script livré avec Loom-IA et n'ont pas été lancées.

#### Pourquoi

Mme Martin demande un devis pour un chantier où la TVA est à 5,5 % sur une partie des travaux et à 20 % sur le reste. Ce calcul n'existe pas dans le carnet. Jean Dupont aimerait que l'agent **écrive lui-même** la petite fonction qui manque, la teste sur des exemples, et la garde pour la prochaine fois, sans que du code écrit par un modèle ne tourne jamais sur son serveur.

#### Objectif

Comprendre la source `forge` : ce qu'elle fournit à l'agent, où vit le code forgé, ce que l'hôte contrôle avant d'envoyer quoi que ce soit à la VM, et comment la configurer. Valider la configuration sans VM.

#### Mise en place

Le principe : l'agent **forge** un outil (un nom, une description, un schéma d'entrée, le code d'un module qui définit une fonction du même nom, de un à dix exemples), puis l'**appelle**. Le code ne s'exécute jamais sur l'hôte. L'hôte vérifie la forme du code (syntaxe, signature), le range dans un catalogue, puis l'envoie à un service, **execd**, qui tourne dans une micro-VM Firecracker et l'exécute sous limites de ressources. La VM ne garde aucun catalogue : chaque appel transporte son code.

La source fournit à chaque run :

| Outil | Rôle |
|---|---|
| `forge__forge` | Forge (ou remplace) un outil. Contrôles de l'hôte d'abord, puis chaque exemple est exécuté dans la VM et comparé à la sortie attendue. Au premier écart, l'outil est refusé et le modèle apprend pourquoi. Accepté, il est écrit dans le catalogue. |
| `forge__call` | Appelle un outil du catalogue par son nom, avec ses arguments. |
| `forge__<outil>` | Chaque outil du catalogue du client, lu à l'ouverture du run : un outil forgé pendant un run n'apparaît donc sous son nom qu'au run suivant (en attendant, `forge__call` l'appelle). |

Les paramètres de la source sont `vm_dir` (le dossier de la VM), `catalog_dir` (le catalogue) et `limits` (`wall_ms`, `cpu_ms`, `mem_bytes`, `fsize_bytes`, `nofile`, `nproc`, `out_files`). Le catalogue range chaque outil sous `catalog_dir/<client>/<nom>/`, dans deux fichiers : `<nom>.py` et `outil.json`.

**Construire la VM** (non exécuté ici). Le script `firecracker/make_vm.sh` se trouve dans le dépôt du projet, pas dans le paquet PyPI. Il télécharge Firecracker, un noyau et un système Ubuntu 24.04, construit une image disque et y injecte le service execd (dossier `firecracker/service/` du dépôt). Il demande `sudo` (pour `unsquashfs` et `mkfs.ext4`) et KVM pour lancer la VM ensuite :

```bash
./make_vm.sh /srv/vms/dupont-forge --execd ./service
```

Ses options : `--vcpu N`, `--mem MIB`, `--cid N`, `--rootfs-size`, `--data-size`, `--data-label`, `--execd DIR`, `--execd-port N` (5100 par défaut), `--no-execd`, `--reset-data`, `--reset-tree`, `--slim` et `--cache DIR`. Le dossier produit contient `vm.env`, `run.sh` et l'image disque ; c'est lui que `vm_dir` désigne. Pour lancer la VM sous `jailer` (le VMM ne tourne alors jamais en root), le dépôt fournit `firecracker/jailer-run.sh`. Loom-IA démarre la VM tout seul au premier appel qui exécute du code, et l'arrête avec l'instance.

**Ce qui s'exécute ici : la configuration.** Créez un sous-projet `forge/` dans `qualite/`, avec ses propres fichiers (la source `forge` ne doit pas être déclarée dans le `loom.yaml` du projet, qui n'a pas de VM) :

```bash
mkdir -p forge/agents forge/catalogue/default/total_ttc
```

`forge/loom.yaml` :

```yaml
version: 1

agents_dir: agents/

models:
  - id: SIMULE_ATELIER
    sdk: fake
    model: fake-atelier
    params:
      script:
        - text: Je suis prêt à forger un outil de calcul.

tool_sources:
  - name: forge
    entry_point: forge
    params:
      vm_dir: vm
      catalog_dir: catalogue
      limits: {wall_ms: 5000}

telemetry:
  logging: {level: WARNING}

storage:
  events: {backend: jsonl, path: data}
```

`forge/agents/atelier.yaml` :

```yaml
name: atelier
description: Forge des petits outils de calcul pour l'artisan, dans une VM isolée.

main:
  model: SIMULE_ATELIER
  system: Tu forges des outils de calcul avec `forge`, puis tu les appelles avec `call`.

max_iterations: 6

tools:
  - source: forge
```

Un outil déjà forgé, rangé dans le catalogue du client `default`. `forge/catalogue/default/total_ttc/total_ttc.py` :

```python
def total_ttc(montant_ht, taux_tva):
    tva = round(montant_ht * taux_tva / 100, 2)
    return {"tva": tva, "montant_ttc": round(montant_ht + tva, 2)}
```

`forge/catalogue/default/total_ttc/outil.json` :

```json
{
  "description": "Calcule la TVA et le total TTC d'un montant HT.",
  "input_schema": {
    "type": "object",
    "properties": {
      "montant_ht": {"type": "number"},
      "taux_tva": {"type": "number"}
    },
    "required": ["montant_ht", "taux_tva"]
  },
  "examples": [
    {"arguments": {"montant_ht": 1250, "taux_tva": 10}, "expected": {"tva": 125.0, "montant_ttc": 1375.0}}
  ],
  "forged_by": "atelier",
  "forged_at": "2026-10-10T09:00:00Z"
}
```

#### Exécution

Sans dossier de VM, la configuration est refusée :

```bash
uv run loom --config forge/loom.yaml validate
```

```text
Rétention  : aucune (rien ne s'efface)
Clés d'API : aucune (API REST ouverte)
Configuration : Agent 'atelier', source 'forge' (loom-ia 2.0.0, loom_ia.adapters.firecracker.forge:forge_source) : refusée par sa fabrique — 'vm_dir' : /home/denis/qualite/forge/vm/vm.env introuvable — dossier de VM invalide
```

Un dossier `forge/vm/` contenant seulement un `vm.env` (les six variables que lit Loom-IA : le nom, le CID vsock, les chemins des deux sockets, le port d'execd et la source d'execd) suffit à **valider** la configuration, puisque la validation n'exécute rien et ne démarre pas la VM. Ce dossier factice remplace, pour cet essai seulement, celui que produit `make_vm.sh`. Créez `forge/vm/vm.env` :

```bash
VM_NAME="dupont-forge"
GUEST_CID="3"
API_SOCK="/tmp/dupont-forge/api.sock"
VSOCK_UDS="/tmp/dupont-forge/vsock.sock"
EXECD_PORT="5100"
EXECD_SRC="firecracker/service"
```

```bash
uv run loom --config forge/loom.yaml validate
```

```text
Journal    : jsonl (/home/denis/qualite/forge/data)
Artefacts  : local (/home/denis/qualite/forge/data/.artifacts)
Idempotence: journal
File       : asyncio
Bus        : memory (les nouvelles ne sortent pas de ce process)
Chiffrement: aucun (contenus en clair au repos)
Rétention  : aucune (rien ne s'efface)
Clés d'API : aucune (API REST ouverte)
  atelier : modèle SIMULE_ATELIER, 0 outil(s) Python
    source forge : forge__forge, forge__call, forge__total_ttc

1 agent(s) monté(s) sans erreur.
```

(Fin de la sortie.) L'agent voit trois outils : `forge__forge`, `forge__call`, et `forge__total_ttc`, lu dans le catalogue. Si l'agent essaie maintenant d'exécuter `forge__total_ttc` (par un modèle simulé qui l'appelle), la VM manque et le modèle reçoit l'erreur au lieu d'un plantage. Voici la variante de configuration qui l'y pousse, `forge/loom-essai.yaml` :

```yaml
version: 1

agents_dir: agents/

models:
  - id: SIMULE_ATELIER
    sdk: fake
    model: fake-atelier
    params:
      script:
        - text: J'appelle l'outil.
          tool_calls:
            - name: forge__total_ttc
              arguments: {montant_ht: 1250, taux_tva: 10}
        - text: Terminé.

tool_sources:
  - name: forge
    entry_point: forge
    params:
      vm_dir: vm
      catalog_dir: catalogue
      limits: {wall_ms: 5000}

telemetry:
  logging: {level: WARNING}

storage:
  events: {backend: jsonl, path: data}
```

```bash
uv run loom --config forge/loom-essai.yaml run atelier "Calcule le TTC de 1250 € HT à 10 %."
```

```text
2026-10-10 17:52:59 WARNING  loom_ia.adapters.firecracker.forge — Source forge : VM ou execd indisponible — [Errno 2] No such file or directory: '/home/denis/qualite/forge/vm/run.sh'
Terminé.

Statut     : completed · itérations : 2 · tokens : 1398/96 · coût : 0.0000 $
Run        : 01a12684-7cde-7555-8d50-f48fd12e4723
```

L'avertissement vient de la source ; dans la trace, l'appel est en erreur et le modèle suivant continue (`inspect --full` donne le message en entier) :

```text
run atelier — completed, 37 ms, 0.000000 $
  étape 1
    modèle fake-atelier (main) — 1 ms, 606 → 69 tokens, 0.000000 $
      · répond    : J'appelle l'outil.
      · appelle   : forge__total_ttc({"montant_ht": 1250, "taux_tva": 10})
  étape 2
    outil forge__total_ttc — 3 ms (erreur)
      · arguments : {"montant_ht": 1250, "taux_tva": 10}
      · résultat  : (erreur) VM indisponible : FileNotFoundError: [Errno 2] No such file or directory: '/home/denis/qualite/forge/vm/run.sh'
  étape 3
    modèle fake-atelier (main) — 1 ms, 792 → 27 tokens, 0.000000 $
      · répond    : Terminé.

Réponse finale :
  Terminé.
```

Enfin, l'hôte contrôle le code d'un outil avant de l'envoyer. `check_forged` est exécutable sans VM. Créez `controles_forge.py` :

```python
"""Les contrôles que l'hôte fait sur un outil forgé, avant d'envoyer quoi que ce soit à la VM.

Ils ne demandent ni KVM ni VM : seule l'exécution des exemples en demande une.
"""

from loom_ia.adapters.firecracker.forge import check_forged
from loom_ia.core.ports import ToolError

SCHEMA = {
    "type": "object",
    "properties": {
        "montant_ht": {"type": "number"},
        "taux_tva": {"type": "number"},
    },
    "required": ["montant_ht", "taux_tva"],
}
EXEMPLES = [
    {
        "arguments": {"montant_ht": 1250, "taux_tva": 10},
        "expected": {"tva": 125.0, "montant_ttc": 1375.0},
    }
]
BON = '''
def total_ttc(montant_ht, taux_tva):
    tva = round(montant_ht * taux_tva / 100, 2)
    return {"tva": tva, "montant_ttc": round(montant_ht + tva, 2)}
'''
ESSAIS = {
    "code correct": ("total_ttc", BON),
    "fonction mal nommée": ("total_ttc", BON.replace("def total_ttc", "def calcul")),
    "paramètre manquant": ("total_ttc", BON.replace("montant_ht, taux_tva", "montant_ht")),
    "syntaxe cassée": ("total_ttc", "def total_ttc(montant_ht, taux_tva)\n    return 0"),
    "nom réservé": ("forge", BON),
    "nom de module standard": ("json", BON.replace("total_ttc", "json")),
}

for libelle, (nom, code) in ESSAIS.items():
    try:
        paires = check_forged(nom, SCHEMA, code, EXEMPLES)
        print(f"{libelle:<24} accepté ({len(paires)} exemple à jouer dans la VM)")
    except ToolError as erreur:
        print(f"{libelle:<24} refusé : {erreur.message}")
```

```bash
uv run python controles_forge.py
```

```text
code correct             accepté (1 exemple à jouer dans la VM)
fonction mal nommée      refusé : code : pas de fonction total_ttc() au premier niveau du module
paramètre manquant       refusé : code : total_ttc() ne reçoit pas taux_tva, que input_schema propose
syntaxe cassée           refusé : code : erreur de syntaxe ligne 1 — expected ':'
nom réservé              refusé : name 'forge' : nom réservé
nom de module standard   refusé : name 'json' : c'est un module de la bibliothèque standard, que le module de l'outil masquerait ; choisis un autre nom
```

#### À retenir

- Ce que l'hôte refuse n'atteint jamais la VM : une fonction qui ne porte pas le nom de l'outil, un paramètre que le schéma propose mais que la fonction ne reçoit pas, une erreur de syntaxe, un nom réservé (`forge`, `call`), un nom de module de la bibliothèque standard. Le modèle reçoit le motif en clair et peut corriger.
- `input_schema` et `examples` peuvent être donnés en objet JSON ou en **texte JSON** : certains fournisseurs déforment les objets libres d'un appel d'outil, alors qu'une chaîne arrive intacte.
- Les sorties sont bornées (début et fin de `stdout` et `stderr`, 4 000 caractères chacun) et le temps d'exécution aussi (`wall_ms`, 30 s par défaut).
- Un run qui a forgé un outil se rejoue à l'identique **sans démarrer la VM** : le rejeu lit les résultats au journal.
- La VM démarre au premier appel qui exécute du code, pas à la validation ni au rejeu. Prévoyez environ une minute d'attente maximale pour le démarrage (`BOOT_WAIT`, 60 s).
- **Piège** : le catalogue est rangé par client (`catalogue/<client>/…`). Un outil forgé pour la Plomberie Dupont n'existe pas pour le Chauffage Martin. Et il contient du code écrit par un modèle : versionnez-le et relisez-le avant de le promouvoir en production.
- **Non exécuté ici** : la construction de la VM, le démarrage, l'exécution des exemples, la forge réelle d'un outil.

### Exemple 29.3 : une mémoire long terme avec `loom-notes`

> **Non exécuté ici** : les vrais modèles d'embedding de `loom-notes` (BGE-M3 et un reclasseur) demandent l'extra `[models]`, c'est-à-dire PyTorch et plusieurs Go de téléchargements. Cet exemple a été exécuté avec les **modèles factices** que le serveur propose (`LOOM_NOTES_FAKE_MODELS=1`) : les écritures, les lectures, les approbations et l'isolation entre clients sont réelles, mais la recherche sémantique ne l'est pas.

#### Pourquoi

Jean Dupont dit à l'assistant : « Mme Martin préfère qu'on l'appelle le matin ». Le lendemain, l'assistant ne s'en souvient pas : une conversation (chapitre 7) n'est pas une mémoire. Il lui faut un carnet de notes durable, consultable par recherche, que l'agent peut lire librement mais où il n'écrit qu'avec l'accord de l'artisan, et qui ne mélange pas les notes de la Plomberie Dupont et celles du Chauffage Martin.

#### Objectif

Brancher le serveur MCP `loom-notes` comme n'importe quel serveur MCP (chapitre 13), exiger l'approbation de toute écriture, donner à chaque client sa propre base, et vérifier qu'un refus n'écrit rien.

#### Mise en place

`loom-notes` est un serveur MCP publié sur PyPI (version 1.1.0 au moment de l'essai), qui s'exécute sur la sortie standard (`stdio`). Il ne s'ajoute **pas** aux dépendances du projet : ses propres dépendances (FastMCP 4) sont incompatibles avec la version de `mcp` qu'exige `loom-ia`. On le lance donc par `uvx`, dans un environnement isolé, en process séparé. Aucune installation préalable : `uvx` la fait au premier lancement.

Ses neuf outils : `search`, `get`, `list_docs`, `projects` (lectures, annoncées en lecture seule) et `add_text`, `add_url`, `add_file`, `update`, `delete` (écritures). Il ne lit son dossier de données que dans la variable `LOOM_NOTES_DATA_DIR`.

Créez un sous-projet `memoire/`, avec sa configuration. Le modèle simulé joue deux scénarios selon la demande : « Mémorise… » écrit une note, « Que sait-on… » liste les documents.

```bash
mkdir -p memoire/agents memoire/prompts
```

`memoire/loom.yaml` :

```yaml
version: 1

agents_dir: agents/
prompts_dir: prompts/

models:
  - id: SIMULE_MEMOIRE
    sdk: fake
    model: fake-memoire
    params:
      script:
        - with_text: Mémorise
          tool_calls:
            - name: memoire__add_text
              arguments:
                text: Mme Martin préfère être contactée le matin, avant 10 h.
                title: Préférence de contact de Mme Martin
                project: clients
        - with_text: Mémorise
          text: C'est noté dans la mémoire de l'entreprise.
        - with_text: Que sait-on
          tool_calls:
            - name: memoire__list_docs
              arguments: {}
        - with_text: Que sait-on
          text: Voici ce que la mémoire contient, d'après la liste des documents.

mcp_servers:
  - name: memoire
    transport: stdio
    command: uvx
    args: [--from, loom-notes, loom-notes-mcp]
    env: {LOOM_NOTES_FAKE_MODELS: "1", LOOM_NOTES_WARMUP_ON_START: "false"}
    env_from: {LOOM_NOTES_DATA_DIR: MEMOIRE_DOSSIER}
    scope: tenant
    connect_timeout: 60
    tools:
      add_text: {approval: always}
      add_url: {approval: always}
      add_file: {approval: always}
      update: {approval: always}
      delete: {approval: always}

tenants:
  - id: dupont-plomberie
    secrets: {MEMOIRE_DOSSIER: DUPONT_MEMOIRE}
  - id: martin-chauffage
    secrets: {MEMOIRE_DOSSIER: MARTIN_MEMOIRE}

telemetry:
  logging: {level: WARNING}

storage:
  events: {backend: jsonl, path: data}
```

Lecture de la configuration :

- `command: uvx` avec `args: [--from, loom-notes, loom-notes-mcp]` lance le serveur. `env` fixe ses variables : les modèles factices, et pas de préchauffage au démarrage.
- `env_from: {LOOM_NOTES_DATA_DIR: MEMOIRE_DOSSIER}` donne au serveur son dossier de données, **lu dans les secrets du client** : la variable `MEMOIRE_DOSSIER` désigne, pour chaque client, une variable d'environnement différente (`tenants[].secrets`).
- `scope: tenant` : un serveur par client. C'est ce qui donne à chacun sa base, et donc leur isolation.
- `tools` : les cinq écritures sont en `approval: always`. Les lectures n'ont rien à demander.
- `connect_timeout: 60` laisse au premier lancement de `uvx` le temps de préparer son environnement.

`memoire/agents/memoire.yaml` :

```yaml
name: memoire
description: Assistant qui consulte la mémoire de l'entreprise, et n'y écrit qu'avec l'accord de l'artisan.

main:
  model: SIMULE_MEMOIRE
  system_file: memoire.md

max_iterations: 4

tools:
  - mcp: memoire
```

`memoire/prompts/memoire.md` :

```markdown
Tu es l'assistant d'une entreprise d'artisan. Tu disposes d'une mémoire de notes.

Avant de répondre à une question sur un client, un devis ou une décision passée, consulte la mémoire.
N'y écris jamais de ta propre initiative : seulement quand l'artisan le demande dans son message.
```

Ce prompt reprend, avec les mots de l'atelier, ce que le serveur dit de lui-même dans ses `instructions` MCP. Loom-IA ne transmet pas ces instructions au modèle : si elles comptent, recopiez-les dans votre prompt.

Chaque client a son dossier de données, désigné par une variable d'environnement :

```bash
export DUPONT_MEMOIRE=$PWD/memoire/donnees/dupont
export MARTIN_MEMOIRE=$PWD/memoire/donnees/martin
```

#### Exécution

Validez. Le premier lancement télécharge le serveur (quelques secondes), et un serveur démarre par client :

```bash
uv run loom --config memoire/loom.yaml validate
```

```text
File       : asyncio
Bus        : memory (les nouvelles ne sortent pas de ce process)
Chiffrement: aucun (contenus en clair au repos)
Rétention  : aucune (rien ne s'efface)
Clés d'API : aucune (API REST ouverte)
Clients    : dupont-plomberie, martin-chauffage

  client dupont-plomberie
    secrets : MEMOIRE_DOSSIER → DUPONT_MEMOIRE
    memoire : modèle SIMULE_MEMOIRE, 0 outil(s) Python
    MCP : memoire__search, memoire__get, memoire__list_docs, memoire__projects, memoire__add_text, memoire__add_url, memoire__add_file, memoire__update, memoire__delete

  client martin-chauffage
    secrets : MEMOIRE_DOSSIER → MARTIN_MEMOIRE
    memoire : modèle SIMULE_MEMOIRE, 0 outil(s) Python
    MCP : memoire__search, memoire__get, memoire__list_docs, memoire__projects, memoire__add_text, memoire__add_url, memoire__add_file, memoire__update, memoire__delete

2 agent(s) monté(s) sans erreur.
```

(Fin de la sortie.) Chaque client voit les neuf outils, préfixés `memoire__`. Demandez maintenant d'écrire une note. Chaque commande affiche aussi deux lignes `Starting MCP server 'loom-notes'`, écrites par le serveur lui-même sur sa sortie d'erreur :

```bash
uv run loom --config memoire/loom.yaml run memoire "Mémorise que Mme Martin préfère être contactée le matin, avant 10 h." --tenant dupont-plomberie
```

```text
[10/10/26 15:54:01] INFO     Starting MCP server 'loom-notes'   transport.py:241
                             with transport 'stdio'                             
—

Statut     : paused · itérations : 1 · tokens : 1450/72 · coût : 0.0000 $
Run        : 01a12685-6879-7136-8666-86efe4bb7246
En attente : memoire__add_text (fake_0_0) — loom approve 01a12685-6879-7136-8666-86efe4bb7246 --call fake_0_0
```

L'écriture est en attente. Demandez ce que la mémoire contient **avant** l'accord :

```bash
uv run loom --config memoire/loom.yaml run memoire "Que sait-on de Mme Martin ?" --tenant dupont-plomberie
```

```text
[10/10/26 15:54:04] INFO     Starting MCP server 'loom-notes'   transport.py:241
                             with transport 'stdio'                             
Voici ce que la mémoire contient, d'après la liste des documents.

Statut     : completed · itérations : 2 · tokens : 2977/81 · coût : 0.0000 $
Run        : 01a12685-74a1-7287-96e6-5932bdcee61a
```

Le résultat de l'outil `list_docs`, lu dans la trace (`loom inspect … --full`) :

```text
    outil memoire__list_docs — 10 ms
      · résultat  : {"result": []}
  étape 3
    modèle fake-memoire (main) — 1 ms, 1537 → 41 tokens, 0.000000 $
```

La liste est vide : rien n'a été écrit pendant l'attente. Jean Dupont approuve :

```bash
uv run loom --config memoire/loom.yaml approve 01a12685-6879-7136-8666-86efe4bb7246 --tenant dupont-plomberie --by "Jean Dupont"
```

```text
[10/10/26 15:54:15] INFO     Starting MCP server 'loom-notes'   transport.py:241
                             with transport 'stdio'                             
Accordé : fake_0_0
C'est noté dans la mémoire de l'entreprise.

Statut     : completed · itérations : 2 · tokens : 3129/108 · coût : 0.0000 $
Run        : 01a12685-6879-7136-8666-86efe4bb7246
```

Même question, après l'accord :

```text
    outil memoire__list_docs — 11 ms
      · résultat  : [{"doc_id":"684396ba-9595-476e-99dd-a0006360cb6d","title":"Préférence de contact de Mme Martin","project":"clients","tags":[],"source_kind":"text","source":null,"added_at":"2026-10-10T15:54:15.358462+00:00","updated_at":null,"chars":55}]
  étape 3
```

La note est là. Chez le Chauffage Martin, la mémoire reste vide :

```bash
uv run loom --config memoire/loom.yaml run memoire "Que sait-on de Mme Martin ?" --tenant martin-chauffage
```

```text
    outil memoire__list_docs — 10 ms
      · résultat  : {"result": []}
  étape 3
```

Enfin, un refus. Le Chauffage Martin demande la même écriture, et Paul Martin la refuse :

```bash
uv run loom --config memoire/loom.yaml run memoire "Mémorise que Mme Martin préfère être contactée le matin, avant 10 h." --tenant martin-chauffage
uv run loom --config memoire/loom.yaml reject 01a12685-cfd2-7555-8f60-54a98cf215fc --tenant martin-chauffage --by "Paul Martin" --reason "Pas de ce client"
```

```text
[10/10/26 15:54:31] INFO     Starting MCP server 'loom-notes'   transport.py:241
                             with transport 'stdio'                             
Refusé : fake_0_0
C'est noté dans la mémoire de l'entreprise.

Statut     : completed · itérations : 2 · tokens : 3057/108 · coût : 0.0000 $
Run        : 01a12685-cfd2-7555-8f60-54a98cf215fc
```

La mémoire de Martin reste vide : un refus n'écrit rien. (Le modèle simulé répond « C'est noté » parce que son script le dit ; un vrai modèle recevrait le refus et répondrait en conséquence.)

#### À retenir

- Une mémoire se branche comme n'importe quel serveur MCP : tout ce que vous savez des chapitres 9 et 13 s'applique. Les écritures en `approval: always` s'arrêtent, les lectures passent.
- L'isolation entre clients tient à deux réglages : `scope: tenant` (un serveur par client) et un dossier de données différent par client, lu dans leurs secrets. Sans le second, deux serveurs écriraient dans la même base.
- Un serveur par client a un coût. Avec les vrais modèles d'embedding, chaque serveur charge, d'après les notes du projet, de l'ordre de 5 Go en mémoire : pour beaucoup de clients, prévoyez une machine en conséquence ou une base partagée avec des projets séparés.
- Le run qui a écrit se rejoue à l'identique **sans réécrire la note** : le rejeu lit les résultats au journal.
- **Piège** : `uvx` démarre dans l'environnement que Loom-IA lui fournit, et Loom-IA ne transmet que les variables que la configuration nomme. Derrière un proxy d'entreprise qui présente son propre certificat, `uvx` échoue dans le sous-process avec `invalid peer certificate: UnknownIssuer` et le serveur apparaît « indisponible : Connection closed ». Transmettez le certificat avec `env_from: {SSL_CERT_FILE: SSL_CERT_FILE}` (c'est ce qu'a demandé la machine d'essai de ce guide). Hors proxy, vous n'avez rien à ajouter.
- **Piège** : le premier lancement télécharge le serveur. `connect_timeout` doit lui laisser ce temps, ou le premier run échouera et les suivants passeront.
- **Non exécuté ici** : la recherche sémantique réelle (`search`), qui demande les vrais modèles.

---

## 30. Projet de synthèse : `relance/`

Ce dernier chapitre assemble ce que les vingt-neuf précédents ont montré, dans un projet neuf et autonome, `relance/`. Il n'a rien à voir avec `mon-agent/` ni avec `qualite/` : vous pouvez le créer de zéro. Il reprend le fil rouge, en deux clients :

- la **Plomberie Dupont**, qui relance Mme Martin pour le devis D-2026-042 (1 840 € TTC) ;
- le **Chauffage Martin**, qui relance M. Bernard pour son entretien de chaudière, devis M-2026-017 (189 € TTC).

Le projet met en œuvre : un orchestrateur et un rôle rédacteur (chapitre 12) sous contrat de sortie (chapitre 11) et sous un juge qui répare (chapitre 14) ; un envoi d'e-mail irréversible, validé par un humain (chapitre 9) et idempotent par clé métier (chapitre 19) ; deux clients aux modèles, aux variables et aux journaux distincts (chapitre 22) ; des budgets et des quotas (chapitre 16) ; l'API REST et le serveur MCP (chapitres 20 et 21) avec des clés d'API à portées différentes ; un journal SQLite ; des tests (chapitre 28). Les modèles sont simulés, pour que chaque sortie de ce chapitre se retrouve à l'identique.

### Exemple 30.1 : le projet complet, et une relance de bout en bout

#### Pourquoi

Jean Dupont a vu chaque brique séparément. Il veut maintenant un projet qu'il pourrait déployer : l'agent cherche le devis, fait rédiger l'e-mail par un rédacteur contrôlé, ne l'envoie qu'après son accord, et ne l'envoie qu'une fois, même si l'on relance deux fois la même demande.

#### Objectif

Créer le projet, le valider, créer les clés d'API, puis lancer et approuver une relance du côté de la Plomberie Dupont.

#### Mise en place

```bash
uv init --bare --python 3.14 relance
cd relance
uv add "loom-ia[http,mcp,sqlite]"
uv add --dev pytest pytest-asyncio
mkdir agents prompts tests
```

Créez d'abord les trois clés d'API. La commande n'a pas besoin de configuration : elle affiche la clé **une seule fois** et l'empreinte à copier dans `loom.yaml`. Conservez les clés dans un fichier hors du dépôt.

```bash
uv run loom keys create app-dupont --tenant dupont-plomberie --scope run --scope read --scope approve
uv run loom keys create lecture-dupont --tenant dupont-plomberie --scope read
uv run loom keys create app-martin --tenant martin-chauffage --scope run --scope read --scope approve
```

```text
Clé        : lk_54SkDo…
Elle n'est affichée qu'ici : la config ne garde que son empreinte.

À ajouter dans la configuration :

security:
  api_keys:
    - id: app-dupont
      hash: sha256:4fd79dc379ee9babca24c56573557559e40304e4816adb4fdeed198b9afb1f06
      tenant: dupont-plomberie
      scopes: [run, read, approve]

Cette clé peut approuver sans pouvoir lire : sans 'read_content', les arguments d'un appel en attente lui sont masqués.
```

(La première clé est abrégée ici ; les deux autres sortent au même format. Les trois empreintes sont dans le `loom.yaml` ci-dessous : remplacez-les par les vôtres.) Les portées sont `run`, `read`, `read_content`, `approve` et `admin`. Dans le fichier ci-dessous, on ajoute `read_content` aux deux clés d'application ; la clé `lecture-dupont` est une clé de supervision qui ne lit que les métadonnées.

Ajoutez à la fin de `pyproject.toml` la configuration de pytest :

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
pythonpath = ["."]
```

Créez `outils.py`. Chaque client a son carnet de devis ; l'outil le choisit d'après le client du run (`ctx.tenant_id`). L'envoi est irréversible, approuvé par un humain, et protégé par une **clé d'idempotence métier** : le même devis ne part qu'une fois par client, quel que soit le nombre de runs.

```python
from datetime import datetime
from pathlib import Path

from loom_ia.core.ports import ToolContext, ToolError
from loom_ia.tools import idempotent, tool

# Le carnet de devis de chaque client. En production, ce serait son CRM.
CARNETS: dict[str, dict[str, dict[str, str | float]]] = {
    "dupont-plomberie": {
        "D-2026-042": {
            "client": "Mme Martin",
            "email": "mme.martin@example.fr",
            "objet": "Remplacement du chauffe-eau (200 L)",
            "montant_ttc": 1840.0,
            "envoye_le": "2026-09-02",
            "statut": "en attente",
        },
    },
    "martin-chauffage": {
        "M-2026-017": {
            "client": "M. Bernard",
            "email": "m.bernard@example.fr",
            "objet": "Entretien annuel de la chaudière gaz",
            "montant_ttc": 189.0,
            "envoye_le": "2026-09-15",
            "statut": "en attente",
        },
    },
}


@tool
def chercher_devis(numero: str, ctx: ToolContext) -> dict[str, str | float]:
    """Cherche un devis du client par son numéro (ex. D-2026-042) : destinataire, objet, montant."""
    carnet = CARNETS.get(ctx.tenant_id, {})
    if numero not in carnet:
        connus = ", ".join(carnet) or "aucun"
        raise ToolError(f"Aucun devis {numero!r} chez ce client. Devis connus : {connus}.")
    return {"numero": numero, **carnet[numero]}


@idempotent(key=lambda args: f"relance:{args['numero']}")
@tool(side_effects="irreversible", approval="always")
async def envoyer_email(
    numero: str, destinataire: str, objet: str, corps: str, ctx: ToolContext
) -> str:
    """Envoie au client l'e-mail de relance d'un devis. Définitif : l'artisan doit l'approuver."""
    boite = Path("data") / f"boite-{ctx.tenant_id}.txt"
    boite.parent.mkdir(exist_ok=True)
    with boite.open("a", encoding="utf-8") as fichier:
        fichier.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} | {destinataire} | {objet}\n")
    return f"E-mail de relance du devis {numero} envoyé à {destinataire}"
```

Créez `prompts/relance.md`, celui de l'orchestrateur. `{{ entreprise }}` est une variable du client :

```markdown
Tu es l'assistant de {{ entreprise }}. Tu relances les clients dont le devis est resté sans réponse.

Procède dans cet ordre, un appel à la fois :

1. Cherche le devis avec `chercher_devis`.
2. Fais rédiger l'e-mail par `rediger_relance`, avec le ton demandé par l'artisan.
3. Envoie l'e-mail avec `envoyer_email`, en reprenant tel quel l'objet et le corps rédigés.
4. Confirme à l'artisan, en une phrase, ce qui est parti.

N'invente jamais un montant, une date ou un délai.
```

Créez `prompts/rediger.md`, celui du rôle rédacteur :

```markdown
Tu rédiges, pour {{ entreprise }}, l'e-mail qui relance un client dont le devis est resté sans réponse.

Réponds uniquement par un objet JSON de la forme {"objet": "...", "corps": "..."}.
Reprends le montant, la date et le numéro du devis tels qu'ils figurent dans le devis. N'invente rien.
```

Créez `agents/relance.yaml`. Le rôle `rediger_relance` rend un objet JSON `{objet, corps}` sous contrat, et un juge vérifie que rien n'est inventé :

```yaml
name: relance
description: Relance un client dont le devis est resté sans réponse, avec l'accord de l'artisan.

main:
  model: ORCHESTRATEUR
  system_file: relance.md

max_iterations: 8

tools:
  - python: chercher_devis
  - python: envoyer_email

roles:
  - name: rediger_relance
    description: Rédige l'e-mail de relance d'un devis, à partir du devis et de la demande de l'artisan.
    model: REDACTEUR
    system_file: rediger.md
    input_schema:
      type: object
      properties:
        ton: {type: string, description: "cordial, ferme, bref…"}
      required: [ton]
    context: [user_input, {tool_results: [chercher_devis]}]
    input_template: |-
      Demande de l'artisan : {{ context.user_input }}
      Devis : {{ context.tool_results.chercher_devis }}
      Ton : {{ args.ton }}
    output:
      schema:
        type: object
        properties:
          objet: {type: string, minLength: 5}
          corps: {type: string, minLength: 20}
        required: [objet, corps]
        additionalProperties: false
      must_not_match: "(?i)à compléter|xxx"
      max_chars: 3000
      repair: {max_attempts: 1}
      on_failure: fail
    judge:
      model: JUGE
      context: [user_input, {tool_results: [chercher_devis]}]
      criteria:
        - name: fidele
          rule: >-
            Chaque montant, date et délai de l'e-mail figure dans le devis ou dans la
            demande de l'artisan : rien n'est inventé.
        - name: ton
          rule: Le ton de l'e-mail est celui demandé par l'artisan.
          min_score: 0.6
          blocking: false
      repair: {max_attempts: 1}
      on_failure: fail
```

Créez enfin `loom.yaml`. Il rassemble les quatre modèles simulés (l'orchestrateur, le rédacteur, le rédacteur du Chauffage Martin qui se trompe de montant, le juge), les budgets, les clients, le stockage et les clés :

```yaml
version: 1

imports: [outils]

models:
  - id: ORCHESTRATEUR
    sdk: fake
    model: fake-orchestrateur
    pricing: {input: 3.0, output: 15.0}
    params:
      script:
        - with_text: D-2026-042
          text: Je cherche le devis.
          tool_calls:
            - name: chercher_devis
              arguments: {numero: D-2026-042}
        - with_text: D-2026-042
          text: Je fais rédiger la relance.
          tool_calls:
            - name: rediger_relance
              arguments: {ton: cordial}
        - with_text: D-2026-042
          text: J'envoie la relance.
          tool_calls:
            - name: envoyer_email
              arguments:
                numero: D-2026-042
                destinataire: mme.martin@example.fr
                objet: Votre devis D-2026-042
                corps: "Bonjour Madame Martin, je me permets de revenir vers vous au sujet du devis D-2026-042 (remplacement du chauffe-eau, 1 840 € TTC), envoyé le 2 septembre. Restant à votre disposition, Jean Dupont."
        - with_text: D-2026-042
          text: La relance du devis D-2026-042 (1 840 € TTC) est partie chez Mme Martin.
        - with_text: M-2026-017
          text: Je cherche le devis.
          tool_calls:
            - name: chercher_devis
              arguments: {numero: M-2026-017}
        - with_text: M-2026-017
          text: Je fais rédiger la relance.
          tool_calls:
            - name: rediger_relance
              arguments: {ton: cordial}
        - with_text: M-2026-017
          text: J'envoie la relance.
          tool_calls:
            - name: envoyer_email
              arguments:
                numero: M-2026-017
                destinataire: m.bernard@example.fr
                objet: Votre devis M-2026-017
                corps: "Bonjour Monsieur Bernard, je reviens vers vous au sujet du devis M-2026-017 (entretien annuel de la chaudière gaz, 189 € TTC), envoyé le 15 septembre. Bien cordialement, Chauffage Martin."
        - with_text: M-2026-017
          text: La relance du devis M-2026-017 (189 € TTC) est partie chez M. Bernard.

  - id: REDACTEUR
    sdk: fake
    model: fake-redacteur
    pricing: {input: 1.0, output: 5.0}
    params:
      script:
        - text: '{"objet": "Votre devis D-2026-042", "corps": "Bonjour Madame Martin, je me permets de revenir vers vous au sujet du devis D-2026-042 (remplacement du chauffe-eau, 1 840 € TTC), envoyé le 2 septembre. Restant à votre disposition, Jean Dupont."}'
        - text: '{"objet": "Votre devis M-2026-017", "corps": "Bonjour Monsieur Bernard, je reviens vers vous au sujet du devis M-2026-017 (entretien annuel de la chaudière gaz, 189 € TTC), envoyé le 15 septembre. Bien cordialement, Chauffage Martin."}'

  - id: REDACTEUR_MARTIN
    sdk: fake
    model: fake-redacteur-martin
    pricing: {input: 1.0, output: 5.0}
    params:
      script:
        - text: '{"objet": "Votre devis M-2026-017", "corps": "Bonjour Monsieur Bernard, je reviens vers vous au sujet du devis M-2026-017 (entretien annuel de la chaudière gaz, 198 € TTC), envoyé le 15 septembre. Bien cordialement, Chauffage Martin."}'
        - text: '{"objet": "Votre devis M-2026-017", "corps": "Bonjour Monsieur Bernard, je reviens vers vous au sujet du devis M-2026-017 (entretien annuel de la chaudière gaz, 189 € TTC), envoyé le 15 septembre. Bien cordialement, Chauffage Martin."}'

  - id: JUGE
    sdk: fake
    model: fake-juge
    pricing: {input: 1.0, output: 5.0}
    params:
      script:
        - with_text: "198 €"
          tool_calls:
            - name: verdict
              arguments:
                criteria:
                  - {name: fidele, score: 0.1, reason: Le montant de l'e-mail (198 €) n'est pas celui du devis (189 €).}
                  - {name: ton, score: 0.9, reason: Le ton est cordial.}
        - without_text: "198 €"
          tool_calls:
            - name: verdict
              arguments:
                criteria:
                  - {name: fidele, score: 1.0, reason: Les montants et les dates figurent dans le devis.}
                  - {name: ton, score: 0.9, reason: Le ton est cordial.}

agents_dir: agents
prompts_dir: prompts

budgets:
  run: {max_cost: 0.10}
  session: {max_cost: 0.50}
  on_exceed: stop

storage:
  events: {backend: sqlite, path: data/journal.db}
  idempotency: {backend: sqlite, path: data/cles.db}

telemetry:
  logging: {level: WARNING}

tenants:
  - id: dupont-plomberie
    variables: {entreprise: Plomberie Dupont}
    budgets:
      tenant: {max_cost_per_day: 5.0}
    quotas: {runs_per_minute: 30}
  - id: martin-chauffage
    variables: {entreprise: Chauffage Martin}
    models: {REDACTEUR: REDACTEUR_MARTIN}
    storage:
      events: {backend: sqlite, path: data/martin.db}

security:
  api_keys:
    - id: app-dupont
      hash: sha256:4fd79dc379ee9babca24c56573557559e40304e4816adb4fdeed198b9afb1f06
      tenant: dupont-plomberie
      scopes: [run, read, read_content, approve]
    - id: lecture-dupont
      hash: sha256:047d42b6fac27f2fad3e229939933b78261609d3ae48838e4dc37ede1757be02
      tenant: dupont-plomberie
      scopes: [read]
    - id: app-martin
      hash: sha256:4042e9b15549d2aa0a290196d944e08f7323e1851c82d2f732c6779fc9728878
      tenant: martin-chauffage
      scopes: [run, read, read_content, approve]
```

Quelques lectures :

- Les scripts de l'orchestrateur portent un filtre `with_text` : les réponses « D-2026-042 » ne sont jouées que si la demande contient ce numéro, celles de « M-2026-017 » que si elle contient l'autre. Un modèle simulé n'a pas d'autre moyen de savoir à quel client il parle.
- `tenants[].models: {REDACTEUR: REDACTEUR_MARTIN}` : le Chauffage Martin ne paie pas le même rédacteur. C'est la redirection par client du chapitre 22. Son premier jet contient « 198 € » au lieu de « 189 € ».
- `budgets` plafonne chaque run à 0,10 $ et chaque session à 0,50 $ ; `tenants[].budgets.tenant.max_cost_per_day` plafonne la journée de la Plomberie Dupont à 5 $, et `quotas.runs_per_minute` à 30 runs par minute.
- `storage.idempotency` en SQLite est obligatoire pour une clé métier (voir le piège plus bas). Le Chauffage Martin a son **propre journal** (`data/martin.db`) : aucune requête ne peut mélanger les deux.
- Le rôle n'est pas terminal, et c'est volontaire (voir « À retenir »).

#### Exécution

```bash
uv run loom validate
```

```text
Config     : loom.yaml
Profil     : aucun (ni --profile, ni LOOM_PROFILE, ni 'profile:') ; surcharges : aucune
Modèles    : ORCHESTRATEUR, REDACTEUR, REDACTEUR_MARTIN, JUGE
Agents     : relance
Outils     : chercher_devis, envoyer_email
Paquets    : forge (loom-ia 2.0.0) (groupe loom_ia.tools)
Journal    : sqlite (/home/denis/relance/data/journal.db)
Artefacts  : local (/home/denis/relance/data/.artifacts)
Idempotence: sqlite (/home/denis/relance/data/cles.db)
File       : asyncio
Bus        : memory (les nouvelles ne sortent pas de ce process)
Chiffrement: aucun (contenus en clair au repos)
Rétention  : aucune (rien ne s'efface)
Clés d'API : app-dupont, lecture-dupont, app-martin
    app-dupont : client dupont-plomberie, portées run, read, read_content, approve
    lecture-dupont : client dupont-plomberie, portées read
    app-martin : client martin-chauffage, portées run, read, read_content, approve
Clients    : dupont-plomberie, martin-chauffage

  client dupont-plomberie
    variables : entreprise
    budget : max_cost par jour 5,00000 $
    quota : 30 run(s) par minute
    relance : modèle ORCHESTRATEUR, 2 outil(s) Python, rôle rediger_relance (REDACTEUR)
      politique loom.contract : after_tool
      politique loom.judge.rediger_relance : after_tool
      politique loom.budget : before_model
      budget : run max_cost 0.1 ; session max_cost 0.5 ; stop
      juge rediger_relance (rôle rediger_relance) : modèle JUGE, 2 critère(s)

  client martin-chauffage
    modèles : REDACTEUR → REDACTEUR_MARTIN
    variables : entreprise
    stockage : sqlite (/home/denis/relance/data/martin.db)
    relance : modèle ORCHESTRATEUR, 2 outil(s) Python, rôle rediger_relance (REDACTEUR)
      politique loom.contract : after_tool
      politique loom.judge.rediger_relance : after_tool
      politique loom.budget : before_model
      budget : run max_cost 0.1 ; session max_cost 0.5 ; stop
      juge rediger_relance (rôle rediger_relance) : modèle JUGE, 2 critère(s)

2 agent(s) monté(s) sans erreur.
```

Chaque agent monte les politiques que sa configuration implique : `loom.contract` (contrat de sortie du rôle), `loom.judge.rediger_relance` (juge) et `loom.budget`. Lancez la relance de la Plomberie Dupont :

```bash
uv run loom run relance "Relance Mme Martin pour le devis D-2026-042, sur un ton cordial." --tenant dupont-plomberie
```

```text
—

Statut     : paused · itérations : 3 · tokens : 4681/434 · coût : 0.0167 $
Run        : 01a12687-dedc-76aa-bce5-bba026b13e1c
Juge       : rediger_relance (rôle rediger_relance, appel 1), tentative 1 — acceptée : fidele 1,00, ton 0,90
En attente : envoyer_email (fake_2_0) — loom approve 01a12687-dedc-76aa-bce5-bba026b13e1c --call fake_2_0
```

Le rédacteur a répondu, le juge a accepté (`fidele 1,00`), et l'envoi attend Jean Dupont. Il approuve :

```bash
uv run loom approve 01a12687-dedc-76aa-bce5-bba026b13e1c --tenant dupont-plomberie --by "Jean Dupont" --reason "Relance validée"
```

```text
Accordé : fake_2_0
La relance du devis D-2026-042 (1 840 € TTC) est partie chez Mme Martin.

Statut     : completed · itérations : 4 · tokens : 6391/477 · coût : 0.0225 $
Run        : 01a12687-dedc-76aa-bce5-bba026b13e1c
Juge       : rediger_relance (rôle rediger_relance, appel 1), tentative 1 — acceptée : fidele 1,00, ton 0,90
```

La boîte d'envoi du client contient une ligne :

```bash
cat data/boite-dupont-plomberie.txt
```

```text
2026-10-10 17:56:41 | mme.martin@example.fr | Votre devis D-2026-042
```

La trace du run raconte la suite des étapes, rôle et juge compris :

```bash
uv run loom inspect 01a12687-dedc-76aa-bce5-bba026b13e1c --tenant dupont-plomberie
```

```text
Run        : 01a12687-dedc-76aa-bce5-bba026b13e1c (agent relance, client dupont-plomberie)
Session    : 01a12687-dedc-76aa-bce5-bba026b13e1c
Statut     : completed, 4 itération(s)
Usage      : 6391 → 477 tokens, 0.022476 $, 64 ms de pilotage

run relance — completed, 782 ms, 0.022476 $
  étape 1
    modèle fake-orchestrateur (main) — 1 ms, 931 → 66 tokens, 0.003783 $
      · répond    : Je cherche le devis.
      · appelle   : chercher_devis({"numero": "D-2026-042"})
  étape 2
    outil chercher_devis — 3 ms
      · arguments : {"numero": "D-2026-042"}
      · résultat  : {"numero": "D-2026-042", "client": "Mme Martin", "email": "mme.martin@example.fr", "objet": "Remp…
  étape 3
    modèle fake-orchestrateur (main) — 1 ms, 1184 → 67 tokens, 0.004557 $
      · répond    : Je fais rédiger la relance.
      · appelle   : rediger_relance({"ton": "cordial"})
  étape 4
    rôle rediger_relance — 18 ms
      · arguments : {"ton": "cordial"}
      · contrôle contract : passed
      · politique loom.contract (after_tool) : replace
      · verdict rediger_relance : fidele 1.00, ton 0.90 — accepté (essai 1)
      · contrôle judge : passed
      · résultat  : {"objet": "Votre devis D-2026-042", "corps": "Bonjour Madame Martin, je me permets de revenir ver…
      appels du rôle rediger_relance
        modèle fake-redacteur (rediger_relance) — 0 ms, 393 → 88 tokens, 0.000833 $
          · répond    : {"objet": "Votre devis D-2026-042", "corps": "Bonjour Madame Martin, je me permets de revenir ver…
      juge rediger_relance
        modèle fake-juge (judge:rediger_relance) — 1 ms, 708 → 77 tokens, 0.001093 $
          · appelle   : verdict({"criteria": [{"name": "fidele", "score": 1.0, "reason": "Les montants et les dates figur…
  étape 5
    modèle fake-orchestrateur (main) — 1 ms, 1465 → 136 tokens, 0.006435 $
      · répond    : J'envoie la relance.
      · appelle   : envoyer_email({"numero": "D-2026-042", "destinataire": "mme.martin@example.fr", "objet": "Votre d…
  étape 6
    approbation pour envoyer_email — accordée par Jean Dupont
  étape 7
    outil envoyer_email — 9 ms
      · arguments : {"numero": "D-2026-042", "destinataire": "mme.martin@example.fr", "objet": "Votre devis D-2026-04…
      · résultat  : E-mail de relance du devis D-2026-042 envoyé à mme.martin@example.fr
  étape 8
    modèle fake-orchestrateur (main) — 1 ms, 1710 → 43 tokens, 0.005775 $
      · répond    : La relance du devis D-2026-042 (1 840 € TTC) est partie chez Mme Martin.

Réponse finale :
  La relance du devis D-2026-042 (1 840 € TTC) est partie chez Mme Martin.
Bilan      : 6 appel(s) de modèle, 3 appel(s) d'outil, 1 approbation(s)
```

#### À retenir

- **Un rôle non terminal.** Dans ce projet, `rediger_relance` n'est pas `terminal: true`. Un rôle terminal **achève** le run avec sa sortie : l'orchestrateur ne reprend jamais la main, et l'e-mail ne serait donc jamais envoyé. Réservez `terminal` aux rôles dont la sortie **est** la réponse (un résumé, un devis rédigé), pas à ceux qui alimentent un outil d'envoi. La vérification se fait en ajoutant `terminal: true` à ce rôle : le run se termine avec le JSON du rédacteur pour réponse, et la boîte d'envoi ne bouge pas.
- Les politiques vues par `loom validate` sont dans l'ordre où elles s'exécutent : le contrat d'abord, le juge ensuite, le budget avant chaque appel de modèle.
- Les clés d'API ne sont jamais écrites en clair dans `loom.yaml` : seul le `hash: sha256:…` y figure. Perdre une clé veut dire en créer une autre.
- **Piège** : la clé d'idempotence métier (`@idempotent(key=…)`) est préfixée par le client, mais elle exige un magasin partagé et durable. Avec le magasin par défaut (`journal`), le chargement de la configuration est refusé (code 2) et le dit :

```text
Configuration : Agent 'relance' : outil(s) 'envoyer_email' à clé métier — il leur faut un magasin d'idempotence partagé et durable (sqlite ou postgres ou redis), pas 'journal' (#49)
```

### Exemple 30.2 : deux clients, un juge qui répare, une relance qui ne part qu'une fois

#### Pourquoi

Le Chauffage Martin utilise un rédacteur moins fiable, qui se trompe parfois de montant. Paul Martin ne veut pas relire chaque e-mail : il veut que l'erreur soit attrapée avant qu'il ne voie quoi que ce soit. De son côté, Jean Dupont a une application qui relance parfois deux fois le même devis : Mme Martin ne doit pas recevoir deux e-mails.

#### Objectif

Voir le juge refuser un premier jet faux et faire corriger le rédacteur, puis relancer deux fois le même devis par l'API REST et constater que l'e-mail n'est parti qu'une fois. Enfin, vérifier ce que chacune des clés d'API voit.

#### Mise en place

Le côté Chauffage Martin se joue en Python. Créez `relancer_martin.py`. Notez la dernière étape : `approve()` remet déjà la reprise du run en file, il suffit donc de l'attendre (`drain()`) et de relire le résultat.

```python
"""Relance de Chauffage Martin, depuis Python : le rédacteur de ce client se trompe de montant."""

import asyncio

from loom_ia.access import Loom


async def main() -> None:
    async with Loom.from_config("loom.yaml") as loom:
        resultat = await loom.run(
            "relance",
            "Relance M. Bernard pour le devis M-2026-017, sur un ton cordial.",
            tenant="martin-chauffage",
        )
        print("statut :", resultat.status, "· run :", resultat.run_id)
        for verdict in resultat.verdicts:
            notes = ", ".join(f"{c.name} {c.score:.2f}" for c in verdict.criteria)
            etat = "acceptée" if verdict.passed else "refusée"
            print(f"juge   : essai {verdict.attempt} {etat} ({notes})")
        for attente in resultat.pending_approvals:
            print("attend :", attente.tool_name, attente.call_id)
            await loom.approve(
                resultat.run_id,
                call_id=attente.call_id,
                by="Paul Martin",
                tenant_id="martin-chauffage",
            )
        # approve() remet déjà la reprise en file : on l'attend, on ne la relance pas.
        await loom.drain()
        final = await loom.result(resultat.run_id, tenant_id="martin-chauffage")
        print("final  :", final.status, "·", final.text)
        print(f"coût   : {final.cost_usd:.4f} $")


asyncio.run(main())
```

#### Exécution

```bash
uv run python relancer_martin.py
```

```text
statut : paused · run : 01a12687-f633-71da-b2fa-e96068a189e6
juge   : essai 1 refusée (fidele 0.10, ton 0.90)
juge   : essai 2 acceptée (fidele 1.00, ton 0.90)
attend : envoyer_email fake_2_0
final  : completed · La relance du devis M-2026-017 (189 € TTC) est partie chez M. Bernard.
coût   : 0.0245 $
```

Le juge a refusé le premier jet (`fidele 0.10`) et le rédacteur l'a corrigé à son second essai. La trace montre le détail, avec l'écart cité par le juge :

```bash
uv run loom inspect 01a12687-f633-71da-b2fa-e96068a189e6 --tenant martin-chauffage --full
```

(Extrait, l'étape du rôle.)

```text
  étape 4
    rôle rediger_relance — 33 ms
      · arguments : {"ton": "cordial"}
      · contrôle contract : passed
      · politique loom.contract (after_tool) : replace
      · verdict rediger_relance : fidele 0.10, ton 0.90 — refusé (essai 1)
      · contrôle judge : failed → retry
      · politique loom.judge.rediger_relance (after_tool) : retry
      · contrôle contract : passed
      · politique loom.contract (after_tool) : replace
      · verdict rediger_relance : fidele 1.00, ton 0.90 — accepté (essai 2)
      · contrôle judge : passed
      · résultat  : {"objet": "Votre devis M-2026-017", "corps": "Bonjour Monsieur Bernard, je reviens vers vous au sujet du devis M-2026-017 (entretien annuel de la chaudière gaz, 189 € TTC), envoyé le 15 septembre. Bien cordialement, Chauffage Martin."}
      appels du rôle rediger_relance
        modèle fake-redacteur-martin (rediger_relance) — 0 ms, 393 → 86 tokens, 0.000823 $
          · répond    : {"objet": "Votre devis M-2026-017", "corps": "Bonjour Monsieur Bernard, je reviens vers vous au sujet du devis M-2026-017 (entretien annuel de la chaudière gaz, 198 € TTC), envoyé le 15 septembre. Bien cordialement, Chauffage Martin."}
        modèle fake-redacteur-martin (rediger_relance) — 0 ms, 572 → 86 tokens, 0.001002 $
          · répond    : {"objet": "Votre devis M-2026-017", "corps": "Bonjour Monsieur Bernard, je reviens vers vous au sujet du devis M-2026-017 (entretien annuel de la chaudière gaz, 189 € TTC), envoyé le 15 septembre. Bien cordialement, Chauffage Martin."}
      juge rediger_relance
        modèle fake-juge (judge:rediger_relance) — 0 ms, 706 → 81 tokens, 0.001111 $
          · appelle   : verdict({"criteria": [{"name": "fidele", "score": 0.1, "reason": "Le montant de l'e-mail (198 €) n'est pas celui du devis (189 €)."}, {"name": "ton", "score": 0.9, "reason": "Le ton est cordial."}]})
      juge rediger_relance
        modèle fake-juge (judge:rediger_relance) — 1 ms, 706 → 77 tokens, 0.001091 $
          · appelle   : verdict({"criteria": [{"name": "fidele", "score": 1.0, "reason": "Les montants et les dates figurent dans le devis."}, {"name": "ton", "score": 0.9, "reason": "Le ton est cordial."}]})
```

Passons à l'API REST. Démarrez le serveur (les clés sont lues dans `loom.yaml`) :

```bash
uv run loom serve --port 18420
```

```text
Profil     : aucun (ni --profile, ni LOOM_PROFILE, ni 'profile:') ; surcharges : aucune
API REST   : http://127.0.0.1:18420/v1
Agents     : relance
```

La documentation interactive est servie sur `/docs`. Dans un autre terminal, chargez vos clés dans l'environnement (`APP_DUPONT`, `LECTURE_DUPONT`, `APP_MARTIN`) et appelez l'API. Sans clé :

```bash
curl -s -i http://127.0.0.1:18420/v1/agents | head -1
```

```text
HTTP/1.1 401 Unauthorized
```

Avec la clé d'application :

```bash
curl -s -H "Authorization: Bearer $APP_DUPONT" http://127.0.0.1:18420/v1/agents
```

```text
[{"name":"relance","description":"Relance un client dont le devis est resté sans réponse, avec l'accord de l'artisan."}]
```

Lancez la relance de la même demande pour Dupont (c'est le deuxième run sur ce devis) :

```bash
curl -s -X POST -H "Authorization: Bearer $APP_DUPONT" http://127.0.0.1:18420/v1/agents/relance/runs \
  -H 'content-type: application/json' \
  -d '{"message":"Relance Mme Martin pour le devis D-2026-042, sur un ton cordial."}'
```

La réponse est le run en pause (`status: paused`), avec l'approbation en attente dans `pending_approvals`. Voici ce que voit la clé de supervision, qui n'a que la portée `read` :

```bash
curl -s -H "Authorization: Bearer $LECTURE_DUPONT" http://127.0.0.1:18420/v1/runs/01a12688-304c-76fd-9ba3-a1d0ed5f270b
```

```text
{'status': 'paused', 'text': '', 'iterations': 3, 'cost_usd': 0.016701}
[{'call_id': 'fake_2_0', 'tool_name': 'envoyer_email', 'arguments': {}, 'reason': '', 'policy': None, 'scope': 'approve', 'expire_at': '2026-10-11T15:57:01.964342Z', 'outcome': None}]
```

Le statut et la consommation sont lisibles, mais pas les arguments de l'e-mail (`'arguments': {}`) ni la raison. Cette clé ne peut pas non plus approuver :

```bash
curl -s -X POST -H "Authorization: Bearer $LECTURE_DUPONT" http://127.0.0.1:18420/v1/runs/01a12688-304c-76fd-9ba3-a1d0ed5f270b/approve \
  -H 'content-type: application/json' -d '{"by":"x"}'
```

```text
{"detail":"Clé sans la portée 'approve'"}
```

La clé d'application approuve :

```bash
curl -s -X POST -H "Authorization: Bearer $APP_DUPONT" http://127.0.0.1:18420/v1/runs/01a12688-304c-76fd-9ba3-a1d0ed5f270b/approve \
  -H 'content-type: application/json' -d '{"by":"Jean Dupont","reason":"ok"}'
```

```text
{"run_id":"01a12688-304c-76fd-9ba3-a1d0ed5f270b","calls":["fake_2_0"]}
```

Le run reprend. Combien de lignes dans la boîte d'envoi, après **deux** relances approuvées du même devis ?

```bash
wc -l data/boite-dupont-plomberie.txt
```

```text
1 data/boite-dupont-plomberie.txt
```

Une seule. Le second run a atteint l'étape d'envoi, a reconnu la clé `relance:D-2026-042` et a rendu le résultat mémorisé du premier envoi, sans rien envoyer. Le journal l'a noté (`GET /v1/events` avec le filtre `type=idempotency.reused`) :

```bash
curl -s -H "Authorization: Bearer $APP_DUPONT" "http://127.0.0.1:18420/v1/events?run_id=01a12688-304c-76fd-9ba3-a1d0ed5f270b&type=idempotency.reused"
```

```json
[
    {
        "event_id": "01a12688-510f-7043-b8fc-1d780b1b14a3",
        "ts": "2026-10-10T15:57:10.287654Z",
        "schema_version": 1,
        "tenant_id": "dupont-plomberie",
        "session_id": "01a12688-304c-76fd-9ba3-a1d0ed5f270b",
        "run_id": "01a12688-304c-76fd-9ba3-a1d0ed5f270b",
        "root_run_id": "01a12688-304c-76fd-9ba3-a1d0ed5f270b",
        "span_id": "01a12688-510b-759d-8640-34fb7791a131",
        "parent_span_id": "01a12688-5109-707f-9efa-3ab116d80f67",
        "type": "idempotency.reused",
        "category": "idempotency",
        "status": "ok",
        "agent": "relance",
        "role": null,
        "facets": {
            "tool_name": "envoyer_email",
            "key": "dupont-plomberie:relance:D-2026-042"
        },
        "payload": {
            "type": "idempotency.reused",
            "key": "dupont-plomberie:relance:D-2026-042",
            "call_id": "fake_2_0",
            "tool_name": "envoyer_email"
        },
        "seq": 42
    }
]
```

La trace, avec chaque clé :

```text
content: False | output: None | spans: 21
content: True | output: 'La relance du devis D-2026-042 (1 840 € TTC) est partie chez Mme Martin.' | spans: 21
```

La première ligne est celle de `lecture-dupont` : `content: False`, pas de texte de sortie. La seconde, celle de `app-dupont` (qui a `read_content`), a le texte. Enfin, l'isolation entre clients : la clé de Martin ne voit pas le run de Dupont, et chaque clé ne liste que les runs de son client.

```bash
curl -s -H "Authorization: Bearer $APP_MARTIN" http://127.0.0.1:18420/v1/runs/01a12688-304c-76fd-9ba3-a1d0ed5f270b
```

```text
{"detail":"Run 01a12688-304c-76fd-9ba3-a1d0ed5f270b inconnu"}

```

```text
APP_DUPONT :
  01a12688-304c-76fd-9ba3-a1d0ed5f270b relance completed
  01a12687-dedc-76aa-bce5-bba026b13e1c relance completed
APP_MARTIN :
  01a12687-f633-71da-b2fa-e96068a189e6 relance completed
```

Arrêtez le serveur (`Ctrl+C`).

#### À retenir

- Le juge qui répare (`repair: {max_attempts: 1}`, `on_failure: fail`) rend le rédacteur responsable de son erreur : le premier jet est refusé avec le motif du juge, le rédacteur reçoit sa sortie et le diagnostic et corrige dans sa propre conversation. Si le second essai échouait aussi, le run échouerait (`guard.judge`) au lieu d'envoyer un e-mail faux.
- Une clé d'idempotence **métier** (`relance:D-2026-042`, préfixée par le client) protège contre la double relance quelle que soit son origine : deux runs, deux sessions, deux processus. Elle ne protège pas d'une relance volontaire un mois plus tard : si c'est ce que vous voulez, mettez la date, ou le numéro de relance, dans la clé.
- Les portées séparent les rôles : `read` pour une supervision, `read_content` pour voir les contenus, `run` pour lancer, `approve` pour trancher, `admin` pour supprimer des sessions. Une clé qui n'a qu'`approve` ne voit pas les arguments de l'appel qu'elle autorise (la commande `loom keys create` le signale).
- Un client ne sait rien de l'autre : un identifiant de run d'un autre client répond « inconnu » comme s'il n'existait pas, et les listes sont filtrées.
- **Piège** : n'appelez pas `Loom.resume()` après `Loom.approve()`. `approve()` remet déjà la reprise en file, et la lancer en plus fait piloter le même run par deux tâches ; le journal est alors incohérent et la liste des runs du client échoue (`transition depuis paused alors que l'état reconstruit est awaiting_tools`). Ce piège a été rencontré en écrivant ce chapitre.

### Exemple 30.3 : un client MCP, les coûts, les tests, et la route vers la production

#### Pourquoi

L'assistant vocal de Paul Martin parle MCP, pas REST. Il faut aussi que Jean Dupont sache ce que lui coûtent ses relances, et qu'il puisse modifier ce projet sans rien casser. Enfin, avant de déployer, il veut une liste de ce qui reste à changer, car tout ce qui précède tourne avec des modèles simulés et des fichiers locaux.

#### Objectif

Lancer et suivre une relance par MCP, lire les coûts, tester le projet avec pytest, et dresser la liste de ce qu'il faut changer pour la production.

#### Mise en place

Le serveur MCP (`loom mcp`) parle sur l'entrée et la sortie standard, au nom d'un client (`--tenant`). Créez `appel_mcp.py`, un client MCP minimal qui lance une relance, ou relit un run déjà lancé :

```python
"""Un client MCP (stdio) du Chauffage Martin : lance une relance, ou relit un run déjà lancé."""

import asyncio
import shutil
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main(run_id: str | None) -> None:
    serveur = StdioServerParameters(
        command=shutil.which("loom") or "loom", args=["mcp", "--tenant", "martin-chauffage"]
    )
    async with stdio_client(serveur) as (lecture, ecriture):
        async with ClientSession(lecture, ecriture) as session:
            await session.initialize()
            if run_id is None:
                for outil in (await session.list_tools()).tools:
                    print("outil :", outil.name, list(outil.inputSchema["properties"]))
                message = "Relance M. Bernard pour le devis M-2026-017, sur un ton cordial."
                reponse = await session.call_tool("relance", {"message": message})
            else:
                reponse = await session.call_tool("run_status", {"run_id": run_id})
            print("erreur :" if reponse.isError else "réponse :", reponse.content[0].text)


asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else None))
```

Créez `tests/test_relance.py`. Trois tests : la relance de Dupont, la réparation du montant de Martin, et l'isolation des carnets.

```python
from loom_ia.testing import Bench


def faux_envoi(numero: str, destinataire: str, objet: str, corps: str) -> str:
    return f"[double] relance de {numero} non envoyée à {destinataire}"


def demande(numero: str, nom: str) -> str:
    return f"Relance {nom} pour le devis {numero}, sur un ton cordial."


async def test_dupont_relance_son_devis() -> None:
    async with Bench("loom.yaml", tools={"envoyer_email": faux_envoi}) as banc:
        result = await banc.run(
            "relance", demande("D-2026-042", "Mme Martin"), tenant="dupont-plomberie"
        )
        banc.expect(
            result,
            status="completed",
            contains=["1 840"],
            called=["chercher_devis", "rediger_relance", {"name": "envoyer_email", "arguments": {"numero": "D-2026-042"}}],
        )
        assert len(result.verdicts) == 1


async def test_le_juge_fait_corriger_le_montant_de_martin() -> None:
    async with Bench("loom.yaml", tools={"envoyer_email": faux_envoi}) as banc:
        result = await banc.run(
            "relance", demande("M-2026-017", "M. Bernard"), tenant="martin-chauffage"
        )
        banc.expect(result, status="completed", contains=["189"], not_contains=["198"])
        assert [v.passed for v in result.verdicts] == [False, True]


async def test_un_client_ne_voit_pas_le_devis_de_l_autre() -> None:
    async with Bench("loom.yaml", tools={"envoyer_email": faux_envoi}) as banc:
        await banc.run("relance", demande("D-2026-042", "Mme Martin"), tenant="martin-chauffage")
        [recherche] = banc.calls("chercher_devis")
        assert recherche.is_error
        assert "Devis connus : M-2026-017" in recherche.result
```

#### Exécution

```bash
uv run python appel_mcp.py
```

```text
outil : relance ['message', 'session_id', 'attachments']
outil : run_status ['run_id', 'session_id']
outil : run_report ['run_id', 'session_id']
outil : cancel ['run_id', 'session_id', 'by']
réponse : Run 01a12688-c2a2-744d-9f35-79d9cfa9cbc8 en attente d'approbation : envoyer_email (fake_2_0). Un humain doit trancher (API REST ou « loom approve »), puis run_status relit le run (run_id=01a12688-c2a2-744d-9f35-79d9cfa9cbc8, session_id=01a12688-c2a2-744d-9f35-79d9cfa9cbc8).
```

Le serveur expose quatre outils : l'agent lui-même (`relance`, au nom de l'agent) et trois outils de suivi, `run_status`, `run_report` et `cancel`. L'agent attend une approbation : l'assistant vocal ne peut pas la donner. Paul Martin approuve de son côté, puis l'assistant relit le run.

```bash
uv run loom approve 01a12688-c2a2-744d-9f35-79d9cfa9cbc8 --tenant martin-chauffage --by "Paul Martin"
```

```text
Accordé : fake_2_0
La relance du devis M-2026-017 (189 € TTC) est partie chez M. Bernard.

Statut     : completed · itérations : 4 · tokens : 7654/638 · coût : 0.0245 $
Run        : 01a12688-c2a2-744d-9f35-79d9cfa9cbc8
Juge       : rediger_relance (rôle rediger_relance, appel 1), tentative 1 — refusée : fidele 0,10 (seuil 0,80), ton 0,90
Juge       : rediger_relance (rôle rediger_relance, appel 1), tentative 2 — acceptée : fidele 1,00, ton 0,90
```

```bash
uv run python appel_mcp.py 01a12688-c2a2-744d-9f35-79d9cfa9cbc8
```

```text
réponse : La relance du devis M-2026-017 (189 € TTC) est partie chez M. Bernard.
```

La boîte de Martin ne contient toujours qu'une ligne : ce run a retrouvé la clé `relance:M-2026-017` du run Python précédent. Les coûts, par rôle et par modèle :

```bash
uv run loom report 01a12688-c2a2-744d-9f35-79d9cfa9cbc8 --tenant martin-chauffage
```

```text
Consommation — run 01a12688-c2a2-744d-9f35-79d9cfa9cbc8
  Total                   8 appels ·   7654/638 tokens · 0,02448 $
  Par rôle :
    main                    4 appels ·   5277/308 tokens · 0,02045 $
    rediger_relance         2 appels ·    965/172 tokens · 0,00183 $
    judge:rediger_relance   2 appels ·   1412/158 tokens · 0,00220 $
  Par modèle :
    fake-orchestrateur      4 appels ·   5277/308 tokens · 0,02045 $
    fake-redacteur-martin   2 appels ·    965/172 tokens · 0,00183 $
    fake-juge               2 appels ·   1412/158 tokens · 0,00220 $
```

La consommation de la journée d'un client, avec son plafond :

```bash
uv run loom report --tenant dupont-plomberie --periode jour
```

```text
Client     : dupont-plomberie
Période    : journée en cours, depuis 2026-10-10 00:00 UTC
Runs       : 2
Dépense    : 0.044952 $, 13736 tokens
  plafond max_cost : 5,00000 $ — reste 4,95505 $
Remise à 0 : 2026-10-11 00:00 UTC
```

Les tests :

```bash
uv run pytest -v
```

```text
============================= test session starts ==============================
platform linux -- Python 3.14.6, pytest-9.1.1, pluggy-1.6.0 -- /home/denis/relance/.venv/bin/python3
cachedir: .pytest_cache
rootdir: /home/denis/relance
configfile: pyproject.toml
testpaths: tests
plugins: asyncio-1.4.0, anyio-4.15.1
asyncio: mode=Mode.AUTO, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collecting ... collected 3 items

tests/test_relance.py::test_dupont_relance_son_devis PASSED              [ 33%]
tests/test_relance.py::test_le_juge_fait_corriger_le_montant_de_martin PASSED [ 66%]
tests/test_relance.py::test_un_client_ne_voit_pas_le_devis_de_l_autre PASSED [100%]

============================== 3 passed in 0.60s ===============================
```

Enfin, le profil `prod`. Il transforme certains avertissements en erreurs ; sur ce projet, il passe :

```bash
uv run loom --profile prod validate
```

```text
Config     : loom.yaml
Profil     : prod (par option) — les avertissements sont des erreurs ; surcharges : aucune
Modèles    : ORCHESTRATEUR, REDACTEUR, REDACTEUR_MARTIN, JUGE

2 agent(s) monté(s) sans erreur.
```

(Début et fin de la sortie.) Voyons ce qu'il refuse. Dans une copie de `loom.yaml`, remplacez `events: {backend: sqlite, path: data/journal.db}` par `events: {backend: memory}` :

```bash
uv run loom --config loom-fragile.yaml --profile prod validate
```

```text
Configuration : Profil prod : Agent 'relance' : outil 'envoyer_email' en approval: always — une approbation exige un journal durable (jsonl ou sqlite ou postgres), pas 'memory' (#28)
```

Le code de sortie est 2. Un outil `approval: always` qui peut mettre le run en pause pendant des heures ne peut pas vivre sur un journal en mémoire, que le prochain redémarrage efface.

#### À retenir

- Les trois accès MCP (outil, ressources, suivi) partagent tout avec l'API REST et la CLI : le même journal, les mêmes approbations, les mêmes clés. Un run lancé par MCP s'approuve par `loom approve` ou par REST.
- Le serveur MCP expose un outil par agent, dont le nom est celui de l'agent, plus `run_status`, `run_report` et `cancel`. Les ressources `loom://runs`, `loom://sessions` et leurs gabarits (chapitre 25) donnent la lecture.
- **Sans profil, la configuration refuse déjà ce journal en mémoire** : le README annonce que, sans profil, « rien n'est durci ni assoupli », mais la combinaison approbation + journal non durable est refusée sans `--profile` aussi ; seul `--profile dev` la tolère. Relevé en écrivant ce chapitre.

#### Passer en production : ce qu'il faut changer

Tout ce que ce chapitre a exécuté est volontairement local et simulé. Voici ce qui change entre `relance/` et un déploiement, avec le chapitre où chaque point est traité.

| Sujet | Dans `relance/` | En production |
|---|---|---|
| Profil | Aucun | `--profile prod` (ou `profile: prod`) : les avertissements deviennent des erreurs |
| Modèles | `sdk: fake`, scripts | `anthropic` ou `openai` (chapitre 5), avec `pricing` pour chaque modèle, sinon un budget en dollars ne se déclenche jamais |
| Juge | Un modèle simulé | Un modèle **différent** de celui qu'il juge : le même modèle est une erreur en `prod` (chapitre 14) |
| Journal et idempotence | SQLite local | Postgres, avec la sécurité par ligne par client (chapitre 24) ; l'idempotence en magasin partagé durable |
| Chiffrement et rétention | Aucun | Chiffrement du journal par client, rétention en jours, effacement RGPD (chapitre 24) |
| File et bus | En mémoire, dans le process | RabbitMQ pour la file, Redis ou Postgres pour le bus, plusieurs workers (chapitre 23) |
| API REST | Clés dans `loom.yaml` | Clés à durée de vie (`--expires`), limites de débit (`--rate-limit`), HTTPS devant le serveur, jamais d'API ouverte (chapitres 20 et 22) |
| Observabilité | Aucune | Export OpenTelemetry (`telemetry.exporters`), contenu masqué (chapitre 25), logs au format JSON |
| Budgets | Run, session, journée | Réglés sur des coûts réels, avec `on_exceed: stop` et un quota par client (chapitre 16) |
| Sandbox et mémoire | Non utilisées ici | `forge` sur une VM Firecracker réelle ; `loom-notes` avec `[models]` si l'agent doit chercher dans une mémoire (chapitre 29) |
| Contrôle continu | `pytest` en local | `uv run pytest`, `loom eval` et un rejeu des journaux de référence dans l'intégration continue (chapitres 26 à 28) |

**Non exécuté ici** : tout ce qui est de la colonne de droite, faute de clé d'API, de Postgres, de Redis, de RabbitMQ, de collecteur OpenTelemetry et de VM.

---


---

## Annexe A. Référence de la ligne de commande

Relevée avec `uv run loom <commande> --help` (loom-ia 2.0.0). Les options globales se placent **avant** la commande : `uv run loom --config autre.yaml --profile prod run …`.

| Option globale | Rôle |
|---|---|
| `--config FICHIER` | Fichier de configuration (défaut : `loom.yaml`) |
| `--profile {dev,prod}` | Profil appliqué à la configuration (chapitre 8) |

| Commande | Arguments et options | Usage |
|---|---|---|
| `validate` | (aucun) | Charge la configuration, vérifie les références et affiche un résumé. Ouvre une fois chaque source d'outils |
| `run` | `agent message` · `--tenant` · `--stream` · `--json` · `--session` · `--run-id` · `--attach FICHIER` · `--judges auto\|force\|skip` | Lance un run |
| `resume` | `run_id` · `--tenant` · `--json` · `--session` | Reprend un run interrompu |
| `approve` | `run_id` · `--tenant` · `--call` · `--by` · `--reason` · `--arguments JSON` (exige `--call`) · `--session` · `--no-wait` · `--json` | Approuve un appel d'outil en attente |
| `reject` | comme `approve`, sans `--arguments` | Refuse un appel d'outil en attente |
| `replay` | `[run_id]` · `--tenant` · `--session` · `--journal FICHIER` · `--mode exact\|variant` · `--model ETAPE=MODELE` · `--double OUTIL=REF` · `--export FICHIER` · `--json` | Rejoue un run (chapitre 26) |
| `inspect` | `run_id` · `--tenant` · `--session` · `--full` · `--json` | Affiche la trace d'un run (chapitre 25) |
| `eval` | `suite` · `--case NOM` · `--variant NOM` · `--export DOSSIER` · `--json` | Exécute une suite d'évaluations (chapitre 27) |
| `report` | `[run_id]` · `--tenant` · `--session` · `--periode jour\|mois` · `--json` | Rapport de consommation |
| `sessions list` | `--tenant` · `--json` | Liste les sessions |
| `sessions export` | `session_id` · `--tenant` · `--out FICHIER` | Exporte le journal d'une session |
| `sessions delete` | `session_id` · `--tenant` · `--yes` | Supprime une session |
| `retention` | `--tenant` · `--yes` · `--json` | Applique la rétention configurée |
| `keys create` | `id` · `--scope` (répétable) · `--agent` · `--tenant` · `--expires` · `--rate-limit N` | Crée une clé d'API ; fonctionne sans configuration |
| `serve` | `--host` · `--port` · `--reload` (refusé en `prod`) | Serveur REST (extra `http`) |
| `mcp` | `--tenant` | Serveur MCP en stdio (extra `mcp`) |
| `worker` | `--jobs N` | Worker de file de messages |
| `schema` | (aucun) | Imprime le schéma JSON de la **configuration** (pas celui des événements : voir chapitre 25) |
| `storage sql` | (aucun) | Imprime le SQL du stockage Postgres |

### Codes de sortie

| Code | Sens |
|---|---|
| 0 | Succès |
| 1 | Échec du run, ou divergence au rejeu, ou attendu d'évaluation non tenu. Un run **en pause** (attente d'approbation) sort aussi en 1 |
| 2 | Configuration ou demande invalide : fichier illisible, clé inconnue, agent ou client inconnu, run introuvable, option refusée |

### Flux de sortie de `run`

Le texte de la réponse sort sur **stdout** ; `Statut`, `Run` et `En attente` sortent sur **stderr**. Pour ne garder que la réponse : `uv run loom run … 2>/dev/null`. Avec `--json`, tout est sur stdout.

---

## Annexe B. Routes de l'API REST

Relevées dans `/openapi.json` du serveur (`uv run loom serve`, extra `http`) et confirmées dans le code de l'application. La documentation interactive est servie sur `/docs`. Sans clé d'API valide, toute route répond **401**. La documentation du dépôt annonce 17 routes ; l'OpenAPI en décrit 16.

### Portées des clés

| Portée | Donne accès à |
|---|---|
| `run` | Lancer et annuler des runs, recevoir des webhooks |
| `read` | Lire agents, runs, événements, traces, sessions, rapports (contenu masqué sans `read_content`) |
| `read_content` | Voir le contenu (messages, arguments, résultats) au lieu de la version masquée |
| `approve` | Approuver ou refuser des appels d'outil |
| `admin` | Supprimer une session, contourner les juges (`judges: skip`) |

Sans `read_content`, les réponses portent `content: false`, `output: null` et `arguments: {}`.

### Routes

| Méthode et chemin | Portée | Rôle |
|---|---|---|
| `GET /v1/agents` | read | Liste des agents |
| `POST /v1/agents/{name}/runs` | run (sur l'agent) | Lance un run. Réponse 201, ou 202 avec `background` |
| `POST /v1/hooks/{name}` | run | Webhook. 202, ou 200 si déjà reçu |
| `GET /v1/runs` | read | Liste des runs. Filtres : `agent`, `status`, `since`, `until`, `limit`, `sessions` |
| `GET /v1/runs/{run_id}` | read | Détail d'un run |
| `GET /v1/runs/{run_id}/events` | read | Événements en SSE. Paramètres : `after_seq`, `subruns` |
| `POST /v1/runs/{run_id}/approve` | approve | Approuve. Corps : `call_id`, `by`, `reason`, `arguments` |
| `POST /v1/runs/{run_id}/reject` | approve | Refuse. Corps : `call_id`, `by`, `reason` |
| `POST /v1/runs/{run_id}/cancel` | run | Annule. Corps : `by` |
| `GET /v1/events` | read | Événements filtrés : `session_id`, `run_id`, `type`, `category`, `status`, `agent`, `role`, `tool_name`, `model_id`, `since`, `until`, `after`, `limit` |
| `GET /v1/traces/{run_id}` | read | Trace hiérarchique |
| `GET /v1/sessions` | read | Liste des sessions |
| `GET /v1/sessions/{id}` | read | Détail d'une session |
| `DELETE /v1/sessions/{id}` | admin | Supprime une session |
| `GET /v1/sessions/{id}/events` | read | Journal de la session en JSONL |
| `GET /v1/sessions/{id}/report` | read | Rapport de consommation |

Corps de `POST /v1/agents/{name}/runs` : `message`, `session_id`, `run_id`, `user_id`, `metadata`, `judges`, `background`. La valeur `judges: skip` exige la portée `admin`.

### Serveur MCP (`loom mcp`)

| Élément | Détail |
|---|---|
| Outils | Un par agent (nommé comme l'agent), plus `run_status(run_id, session_id)`, `run_report(run_id, session_id)`, `cancel(run_id, session_id, by)` |
| Ressources | `loom://runs/{run_id}{?session_id}` (et `…/events`), `loom://traces/{run_id}{?session_id}`, `loom://sessions/{session_id}` (et `…/events`), `loom://artifacts/{client}/{session}/{fichier}` |

Limite connue : un seul serveur MCP HTTP par process (voir l'annexe D).

---

## Annexe C. Matrice de couverture des fonctions

Chaque fonction de `docs/fonctions.md` est rattachée aux chapitres qui la mettent en pratique. « Non couvert » signifie qu'aucun exemple de ce guide ne l'exerce ; la fonction existe dans la documentation du dépôt, mais ce guide n'affirme rien dessus. Les chapitres 1 à 6 sont dans `01-demarrer.md`, 7 à 14 dans `02-maitriser.md`, 15 à 24 dans `03-production.md`, 25 à 30 dans ce fichier.

| Fonction | Chapitres |
|---|---|
| A1 Lancer un run | 1, 4, 20 |
| A2 Boucle agentique | 1, 2 |
| A3 Appels d'outils parallèles | 2 |
| A4 Arrêts | 2, 9, 12, 16 |
| A5 Annuler un run | 19, 20, 21 |
| A6 Timeout global | 19 |
| A7 Réponse structurée | 11 |
| A8 Contexte de l'appelant | 12 (Python et REST ; la CLI n'a pas d'option pour `user_id` ni `metadata`) |
| A9 Plusieurs agents | 1, 20, 30 |
| B1 Plusieurs fournisseurs | 5 |
| B2 Format neutre | 5, 26 |
| B3 Retry et erreurs classées | 15 |
| B4 Modèle de secours | 15 |
| B5 Cache de prompt | 18 |
| B6 Réglages par appel | 5 |
| B7 Fenêtre de contexte | 7 |
| B8 Streaming des tokens | 4, 20 |
| B9 Capacités par modèle | 5 |
| C1 Déclarer un rôle | 12 |
| C2 Rôle appelé comme un outil | 12, 30 |
| C3 Rôle terminal | 12, 30 (écart signalé en annexe D) |
| C4 Rôle vision | 12 |
| C5 Sous-agent | 17 |
| C6 `main` est un rôle | 12 |
| D1 Contrat d'outil | 2 |
| D2 Outils Python | 2, 29 |
| D3 Outils MCP | 13, 29 |
| D4 Validation des arguments | 2, 28 |
| D5 Timeout et erreurs isolées | 2, 28 |
| D6 Gros résultats | 18 |
| D7 Outils exposés par run | 22, 29 |
| D8 Résultats riches | 18 |
| D9 Sandbox | 29 (partiellement exécuté) |
| D10 Approbation requise | 9, 30 |
| D11 Idempotence | 19, 30 |
| E1 Contrat de sortie | 11 |
| E2 Réparation | 11, 30 |
| E3 Juge LLM | 14, 27, 30 |
| E4 Politique d'échec | 10, 11 |
| E5 Contrats sur tout rôle | 11, 14 |
| E6 Alerte juge corrélé | 14 |
| F1 Sessions | 7 |
| F2 Historique structuré | 7, 26 |
| F3 Compaction | 7 |
| F4 Nettoyage | 7 |
| F5 Backends | 7, 24 (Firestore : non couvert) |
| F6 Mémoire long terme | 29 |
| F7 Gestion des sessions | 7, 24, 28 |
| G1 Entrées validées | 18 |
| G2 Artefacts | 18 |
| G3 Artefacts d'outils | 18, 21 |
| H1 État sérialisable | 19, 26 |
| H2 Checkpoint | 19 |
| H3 Reprise après plantage | 19 |
| H4 Validation humaine | 9, 30 |
| H5 Arrière-plan | 20, 23 |
| H6 Déclencheurs | 23 |
| I1 Événements typés | 4, 25 |
| I2 Python et SSE | 4, 20 |
| I3 Latence voix | 15 (mesure du premier `TextDelta` et délais `first_token` / `idle` contre un faux fournisseur local ; ni vrai fournisseur ni audio) |
| J1 Comptage des tokens | 16 |
| J2 Coût | 16 |
| J3 Ventilation | 16, 22 |
| J4 Plafonds | 16, 22, 30 |
| J5 Rapport | 3, 16, 30 |
| K1 Trace hiérarchique | 25 |
| K2 Schéma versionné | 25 |
| K3 Capture et masquage | 25 |
| K4 Exports | 25 (JSONL et OpenTelemetry ; DuckDB : non couvert) |
| K5 API de lecture | 20, 25 |
| K6 Rejeu | 26 |
| K7 Logs techniques | 25 |
| L1 Espace par client | 22, 30 |
| L2 Secrets par client | 22, 29 |
| L3 Quotas | 22, 30 |
| M1 Configuration validée | 1, 3 |
| M2 Construction en Python | 6 |
| M3 Secrets par l'environnement | 5, 22 |
| M4 Profils | 8, 30 |
| M5 Contrôles de cohérence | 3, 8, 14 |
| N1 Bibliothèque Python | 4 |
| N2 Serveur HTTP | 20 |
| N3 Clés d'API | 22, 30 |
| N4 CLI | 1, 3, annexe A |
| N5 Serveur MCP | 21, 30 |
| O1 Évaluations | 27 |
| O2 Faux modèles et outils | 28 |
| O3 Non-régression | 26, 28 |

---

## Annexe D. Pièges et dépannage

Tous les messages ci-dessous ont été relevés sur loom-ia 2.0.0. Sauf mention, la commande sort avec le code 2.

### Extras manquants

| Situation | Message |
|---|---|
| Journal `sqlite` | `le paquet 'aiosqlite' n'est pas installé (installer l'extra : loom-ia[sqlite])` |
| `loom serve` | `'loom serve' demande l'extra 'http' : uv sync --extra http` |
| `loom mcp` | même forme, avec l'extra `mcp` |
| Modèle `anthropic` | `Demande refusée : Modèle 'M' : le SDK 'anthropic' n'est pas installé (installer l'extra : loom-ia[anthropic])` |
| Exporteur OpenTelemetry | `'telemetry.exporters' demande OpenTelemetry, qui n'est pas installé (installer l'extra : loom-ia[otel])` |

`uv sync --extra …` ne vaut que dans le dépôt de loom-ia. Dans votre projet : `uv add "loom-ia[http]"`. Les extras s'additionnent : le projet du chapitre 25 porte `loom-ia[http,mcp,otel,sqlite]`.

### Erreurs de chargement fréquentes

| Cause | Message |
|---|---|
| Modèle non déclaré | `Agent 'a' : modèle 'INCONNU' non déclaré (modèles connus : M)` |
| Outil introuvable | `Référence 'absent' introuvable : aucun objet de ce nom (enregistrés : aucun). Utiliser 'imports' ou un chemin 'module:attr'` |
| Clé inconnue | `max_iteration — Extra inputs are not permitted` |
| Prompt absent | `main.system_file — prompt introuvable : …/prompts/absent.md` |
| Variable de prompt manquante | `{{ entreprise }} : variable non définie pour le client 'default' (tenants[].variables)` |
| Agent inconnu | `Agent 'b' inconnu (agents : a)` |
| Client inconnu | `Client 'nul' non déclaré (clients : default)` |
| Run inconnu | `Run … introuvable` |
| Configuration absente | `Fichier illisible : [Errno 2] …` |
| YAML invalide | code 2 |

### Journal non durable et approbation

Une approbation exige un journal durable. Avec `storage` en `memory`, le chargement est refusé avec un profil `prod` : `Profil prod : … une approbation exige un journal durable (jsonl ou sqlite ou postgres), pas 'memory' (#28)`. Constaté aussi **sans profil** ; seul `--profile dev` le tolère. Le README dit pourtant que sans profil « rien n'est durci ni assoupli » : l'écart est réel.

### Idempotence sans magasin partagé

`@idempotent(key=…)` sans magasin durable : `outil(s) 'envoyer_email' à clé métier — il leur faut un magasin d'idempotence partagé et durable (sqlite ou postgres ou redis), pas 'journal' (#49)`. La clé est préfixée par le client (`dupont-plomberie:relance:D-2026-042`) et un second run écrit `idempotency.reused`.

### `approve` puis `resume` : journal corrompu

`Loom.approve()` remet déjà la reprise en file. Appeler ensuite `Loom.resume()` pilote le run **deux fois** : le journal devient incohérent et `GET /v1/runs` répond 500 (`transition depuis paused alors que l'état reconstruit est awaiting_tools`). Écrire plutôt `approve`, puis `await loom.drain()`, puis `await loom.result(run_id, tenant_id=…)` (chapitre 30).

### Rôle `terminal`

Un rôle `terminal: true` termine le run avec sa propre sortie : les outils suivants ne s'exécutent pas. Dans le projet de synthèse, marquer `rediger_relance` terminal court-circuite l'envoi de l'e-mail (vérifié). Il reste donc non terminal.

### Rejeu

- `--model` et `--double` exigent `--mode variant`, sinon code 2 (`Rejeu identique : 'models' et 'doubles' ne servent qu'en variante`).
- Une variante qui ne tient pas ses attendus sort en 1 ; une variante qui change le comportement mais n'en viole aucun sort en 0.
- Les outils à effets de bord ne sont jamais réexécutés.

### OpenTelemetry

Variable d'environnement de l'exporteur absente : simple WARNING (« ce collecteur n'est pas monté »), pas une erreur. Un exporteur déclaré dans `loom.yaml` ajoute ce WARNING à chaque commande ; l'isoler dans une configuration à part, passée par `--config`. Le masquage (`redaction.patterns`) ne vaut que pour les exports : le journal reste en clair.

### `loom-notes` et le conflit de dépendances

`loom-ia` demande `mcp<2` alors que `loom-notes` s'appuie sur FastMCP 4 : installer les deux dans le même projet échoue. Lancer `loom-notes` en sous-processus par `uvx --from loom-notes loom-notes-mcp`. Loom ne transmet à ce sous-processus que les variables nommées dans `env` ou `env_from`. Derrière un proxy TLS, `uvx` échoue (`invalid peer certificate: UnknownIssuer` ou `Connection closed`) tant que `env_from: {SSL_CERT_FILE: SSL_CERT_FILE}` n'est pas ajouté. `loom reject` n'écrit rien dans la mémoire, mais le modèle simulé répond quand même « C'est noté ».

### Sorties qui surprennent

- `Starting MCP server` apparaît sur stderr au lancement d'un serveur MCP en stdio ; stdout ne porte que le protocole.
- À INFO, les logs contiennent une ligne par appel de modèle, par outil **et** par `Transition`.
- Dans `run`, le texte est sur stdout, `Statut` et `Run` sur stderr.
- `pytest` doit être configuré avec `asyncio_mode = "auto"` pour exécuter les tests `async` du banc (chapitre 28).

### Limites connues du dépôt (`docs/backlog.md`)

| Réf. | Limite |
|---|---|
| #013, #014 | Pièces jointes autres que les images (PDF, audio, `file_id`) non gérées |
| #015 | `gpt-oss` chez Together inutilisable avec des outils |
| #017 | Le journal d'une session est relu en entier à chaque run |
| #018 | Le contrôle de fidélité de la compaction ne regarde que dans un sens |
| #020 | Un second serveur MCP HTTP dans le même process ne répond plus : un serveur MCP par process |
| #021 | La table d'idempotence est hors de la politique de lignes Postgres |
| #019 | Clos : l'empreinte de requête peut être commune à deux clients ; le rejeu apparie par run |
| #010 | Budget en dollars avec un modèle sans tarif : erreur en profil `prod` |

### Écarts entre la documentation et la version installée

1. Le README montre `Statut` et `Run` avant le texte ; en réel le texte est sur stdout, le reste sur stderr.
2. `loom schema` imprime le schéma de la configuration, pas celui des événements (`loom_ia.core.events.event_json_schema()`).
3. Le README parle d'une ligne par appel de modèle et d'outil à INFO ; il y a aussi les lignes `Transition`.
4. Un run en pause sort avec le code 1.
5. Le README renvoie à un dossier `examples/` absent de l'archive.
6. 17 routes annoncées, 16 dans l'OpenAPI.
7. Voir ci-dessus pour le profil et le journal non durable.
8. Un run échoué pour `timeout` est décrit comme « reprenable » (commentaire du code, backlog) ; `loom resume` le rend tel quel, même avec un délai relevé, en 2.0.0 comme avec les sources de la 2.0.1. Il faut lancer un nouveau run (exemple 19.6).

---

## Annexe E. Glossaire

| Terme | Définition |
|---|---|
| Agent | Configuration nommée (modèle, prompt, outils, politiques) qu'on lance avec un message |
| Orchestrateur | Rôle `main` d'un agent : il choisit les outils et les rôles à appeler |
| Rôle | Appel de modèle déclaré, exposé à l'orchestrateur comme un outil |
| Sous-agent | Rôle qui a ses propres outils, sa boucle et son budget |
| Run | Une exécution d'un agent, de la demande à la réponse |
| Session | Suite de runs partageant un historique |
| Journal | Suite ordonnée d'événements d'un run ou d'une session ; source de vérité |
| Enveloppe | Les 17 champs communs à tout événement du journal |
| Span | Un intervalle de la trace : `invoke_agent`, `step`, `chat`, `execute_tool` |
| Trace | Vue hiérarchique d'un run reconstruite depuis le journal |
| Politique | Règle exécutée à un point précis du run |
| Contrat de sortie | Schéma, regex ou longueur que la réponse doit respecter |
| Réparation | Nouvel essai où le modèle voit sa sortie fautive et le diagnostic |
| Juge | Modèle qui note une sortie selon des critères |
| Juge d'évaluation | Juge déclaré dans une suite d'évals (`judge:`), distinct des juges du run |
| Approbation | Pause d'un appel d'outil jusqu'à décision humaine |
| Rejeu | Réexécution d'un run depuis le journal, à l'identique ou en variante |
| Variante | Rejeu ou évaluation avec un autre modèle ou une autre configuration |
| Doublure | Substitut d'un outil à effets de bord, dont le résultat est enregistré |
| Banc | `loom_ia.testing.Bench` : faux modèles et faux outils pour des tests déterministes |
| Suite d'évaluations | Fichier de cas, d'attendus et de variantes lancé par `loom eval` |
| Source d'outils | Fournisseur d'outils chargé à l'exécution (paquet, `forge`, MCP) |
| Point d'entrée | Déclaration de paquet Python (groupe `loom_ia.tools`) qui nomme une fabrique de source |
| Forge | Source d'outils exécutant du code dans une micro-VM |
| execd | Démon à l'intérieur de la micro-VM qui exécute les appels |
| Client (tenant) | Espace isolé : variables, secrets, journal, budgets, quotas |
| Idempotence | Garantie qu'un outil à effet de bord n'agit qu'une fois pour une même clé |
| Empreinte de requête | Condensé de la demande servant à reconnaître un rejeu ou un doublon |
| Portée | Droit attaché à une clé d'API : `run`, `read`, `read_content`, `approve`, `admin` |
| Profil | Jeu de réglages `dev` ou `prod` appliqué à la configuration |
| OTLP | Protocole OpenTelemetry d'export des traces vers un collecteur |
| Masquage | Remplacement de motifs sensibles dans les exports (journal inchangé) |

---

## Pour conclure

Vous savez maintenant relire un run (`loom inspect`), l'exporter vers un collecteur, le rejouer à l'identique ou avec un autre modèle, le noter avec une suite d'évaluations, figer un comportement dans un test, brancher des outils venus d'un paquet, d'une sandbox ou d'une mémoire, et assembler le tout dans un projet de deux clients (chapitre 30). Les annexes A à E servent de référence : commandes, routes, couverture des fonctions, dépannage, vocabulaire.

Navigation : [01-demarrer.md](01-demarrer.md) · [02-maitriser.md](02-maitriser.md) · [03-production.md](03-production.md) · [04-qualite-et-expert.md](04-qualite-et-expert.md)
