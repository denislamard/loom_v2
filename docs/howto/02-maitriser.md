# Niveau 2 : Intermédiaire — maîtriser le comportement d'un agent

> Rappel du niveau 1 ([`01-demarrer.md`](01-demarrer.md)) : vous avez un projet `mon-agent/` avec un `loom.yaml`, un `outils.py` qui expose l'outil `prix_ttc`, un agent `agents/devis.yaml` et son prompt `prompts/devis.md`. Le modèle s'appelle `SIMULE` (`sdk: fake`, aucune clé d'API) et le journal des événements est un fichier JSONL dans `data/`.

Au niveau 1, un agent répondait à une question. Ici, il faut qu'il se comporte comme prévu : se souvenir de la conversation (chapitre 7), s'adapter à son environnement (8), demander l'accord d'un humain avant d'agir (9), respecter des règles (10), rendre une réponse au bon format (11), déléguer à des rôles spécialisés (12), utiliser des outils venus d'ailleurs (13) et faire contrôler le fond de ses réponses (14).

Fil rouge : la Plomberie Dupont relance Mme Martin, qui n'a pas répondu au devis **D-2026-042** (remplacement de chauffe-eau, 1 840 € TTC). Les chapitres 12, 13 et 14 convergent vers un même flux : un outil `chercher_devis` retrouve le devis, un rôle `rediger_relance` écrit l'e-mail, un contrat en vérifie la forme et un juge en vérifie le fond.

## Avant de commencer

Tout ce chapitre a été exécuté avec **loom-ia 2.0.0** installé par `uv` (aucun `pip`), avec les extras utilisés plus loin :

```console
$ uv add "loom-ia[sqlite,mcp]"
```

Chaque exemple vit dans son propre sous-dossier de `mon-agent/` (`ch07a`, `ch07b`, `ch08`, …) : ainsi un exemple ne pollue pas le suivant. Le dossier contient son propre `loom.yaml`, et toutes les commandes se lancent depuis ce dossier avec `uv run loom …`. Les chemins écrits dans un `loom.yaml` (`agents_dir`, `imports`, `storage`, `args` d'un serveur MCP) sont relatifs à ce fichier.

Les sorties reproduites sont des sorties réelles. Seules deux choses ont été retouchées : les chemins absolus sont remplacés par `/chemin/vers/mon-agent`, et les identifiants de run sont ceux que l'on fixe avec `--run-id` (la commande accepte n'importe quelle chaîne ; sans cette option, loom en génère un). Les lignes techniques sans intérêt (`Paquets`, `Journal`, `Artefacts`, …) d'un `loom validate` sont abrégées en `…`.

### Lire un script du modèle simulé

Tous les exemples utilisent des modèles `sdk: fake`, ce qui les rend reproductibles à l'identique. Leur comportement est écrit dans `params.script`, une liste de réponses jouées dans l'ordre :

| Clé d'une réponse | Effet |
|---|---|
| `text` | le texte que « répond » le modèle |
| `tool_calls` | liste d'appels d'outils `{name, arguments}` demandés par le modèle |
| `with_text: xxx` | la réponse n'est candidate que si le dernier message de l'utilisateur contient `xxx` |
| `without_tool` / `with_tool` | candidate seulement si l'outil est absent / présent (utilisé au chapitre 12) |

La position dans le script est le nombre de réponses déjà données depuis la dernière demande de l'utilisateur, **après** avoir écarté les réponses non candidates. C'est ce qui permet à un seul modèle simulé de jouer plusieurs scénarios selon la question posée. Avec un vrai modèle, tout ce script disparaît : seule la ligne `model:` change.

---

## Chapitre 7 — Conversations : sessions, journaux, compaction

Une **session** est le fil d'une conversation : l'ensemble des événements (messages, appels de modèle, appels d'outils) de tous les runs qui partagent le même `session_id`. Quand un run démarre dans une session existante, loom relit l'historique et l'envoie au modèle avec la nouvelle demande. Sans `session_id`, chaque run est sa propre session (l'identifiant de session est alors l'identifiant du run) et le modèle n'a aucun souvenir de ce qui précède.

Deux réglages gouvernent la durée de vie d'une conversation :

- le **backend du journal** (`storage.events`) : `memory` (le défaut : tout disparaît à la fin du process), `jsonl` (un fichier par session), `sqlite` (une base) ou `postgres` (voir [`03-production.md`](03-production.md)) ;
- la **compaction** (`sessions.compaction`) : quand l'historique devient trop long, les échanges anciens sont remplacés par un résumé.

### 7.1 Reprendre une conversation avec `--session`

#### Pourquoi

Un client écrit : « 1 250 € HT en rénovation, TVA à 10 % ». Dix minutes plus tard : « Et avec une TVA à 20 % ? ». Cette deuxième question ne veut rien dire seule. Sans mémoire, il faudrait recopier tout le contexte à chaque message.

#### Objectif

Poser deux questions en deux commandes séparées, dans la même session, puis lister, exporter et supprimer cette session depuis la CLI.

#### Mise en place

On repart du projet du niveau 1 : même `outils.py`, même prompt. Seuls le modèle simulé et le stockage changent.

```console
$ cd mon-agent
$ mkdir -p ch07a/agents ch07a/prompts
$ cp outils.py ch07a/
$ cp prompts/devis.md ch07a/prompts/
$ cd ch07a
```

Fichier `ch07a/loom.yaml`. Le script joue deux scénarios : une question contenant « 1 250 » déclenche l'outil puis la réponse, une question contenant « 20 % » reçoit directement sa réponse. `storage.events` en `jsonl` rend la conversation durable.

```yaml
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
        # Question sur 1 250 € HT : l'outil, puis la réponse
        - with_text: 1 250
          text: Je calcule le prix TTC.
          tool_calls:
            - name: prix_ttc
              arguments: {montant_ht: 1250, taux_tva: 10}
        - with_text: 1 250
          text: Pour 1 250 € HT avec une TVA à 10 %, le client paie 1 375 € TTC.
        # Question de suivi sur la TVA à 20 %
        - with_text: 20 %
          text: Avec une TVA à 20 %, le client paie 1 500 € TTC.

telemetry:
  logging: {level: WARNING}

storage:
  events: {backend: jsonl, path: data}   # un fichier JSONL par session
```

Fichier `ch07a/agents/devis.yaml`, :

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

Fichier `ch07a/evenements.py`, un petit lecteur du journal exporté :

```python
import json
import sys

# Une ligne du JSONL = un événement ; on n'en garde que l'essentiel.
for ligne in open(sys.argv[1], encoding="utf-8"):
    evenement = json.loads(ligne)
    print(evenement["seq"], evenement["type"], evenement["run_id"])
```

#### Exécution

Premier message, puis le second dans la même session :

```console
$ uv run loom run devis "Quel prix TTC pour 1 250 € HT en rénovation (TVA à 10 %) ?" --session chantier-durand --run-id durand-1
Pour 1 250 € HT avec une TVA à 10 %, le client paie 1 375 € TTC.

Statut     : completed · itérations : 2 · tokens : 734/109 · coût : 0.0000 $
Run        : durand-1
$ uv run loom run devis "Et avec une TVA à 20 % ?" --session chantier-durand --run-id durand-2
Avec une TVA à 20 %, le client paie 1 500 € TTC.

Statut     : completed · itérations : 1 · tokens : 522/37 · coût : 0.0000 $
Run        : durand-2
```

Le second run n'a eu besoin que d'une itération : l'historique (question, appel d'outil, résultat, réponse) lui a été fourni avant sa propre question. Regardons ce que loom a gardé :

```console
$ uv run loom sessions list
chantier-durand              25 événements   2026-10-10 17:38
$ ls data/default
chantier-durand.jsonl
```

Le journal est un fichier `data/default/chantier-durand.jsonl` (`default` est le client, voir le chapitre 8). Pour l'examiner sans toucher au journal, on l'exporte :

```console
$ uv run loom sessions export chantier-durand --out durand.jsonl
25 événements écrits dans durand.jsonl
$ uv run python evenements.py durand.jsonl | grep -v -E "step\.|transitioned|claimed"
1 run.started durand-1
2 message.user durand-1
5 model.responded durand-1
9 tool.called durand-1
10 tool.completed durand-1
14 model.responded durand-1
17 run.completed durand-1
18 run.started durand-2
19 message.user durand-2
22 model.responded durand-2
25 run.completed durand-2
```

Deux runs, 25 événements (les lignes `step.*`, `transitioned` et `claimed` ont été filtrées par `grep` : ce sont les événements internes de la boucle d'exécution). On retrouve pour chaque run `run.started`, `message.user`, `model.responded`, l'appel d'outil pour le premier, puis `run.completed`.

Enfin, la suppression. Sans `--yes`, loom demande confirmation ; une réponse négative ne supprime rien :

```console
$ echo n | uv run loom sessions delete chantier-durand
Supprimer définitivement la session chantier-durand (journal, fichiers et clés d'idempotence) ? [o/N] Rien n'a été supprimé.
$ uv run loom sessions delete chantier-durand --yes
Session chantier-durand supprimée : 25 événement(s), 0 fichier(s), 0 clé(s).
$ uv run loom sessions list
Aucune session.
```

#### À retenir

- `--session <id>` (CLI) et `session_id=` (Python, exemple suivant) rattachent un run à une conversation. Sans eux, la session porte l'identifiant du run.
- Le backend par défaut est `memory`. Avec lui, `loom sessions list` ne montre rien d'une commande à l'autre : **pour reprendre une conversation entre deux commandes, utilisez `jsonl` ou `sqlite`.**
- `loom sessions list`, `export <id> [--out FICHIER]` et `delete <id> [--yes]` lisent le journal configuré dans le `loom.yaml` du dossier courant. Si la liste est vide, vérifiez d'abord que vous êtes dans le bon dossier.
- Piège : `delete` est définitif (journal, fichiers joints et clés d'idempotence). Exportez d'abord ce que vous voulez garder. Refuser la confirmation sort avec le code 2.

### 7.2 Le même flux en Python, avec un journal SQLite

#### Pourquoi

Une application (l'appli Flutter de l'artisan, un service web) n'appelle pas la CLI : elle appelle loom depuis Python et doit pouvoir inspecter la conversation. Un fichier JSONL par session devient encombrant dès qu'il y a beaucoup de clients ; une base SQLite tient en un seul fichier.

#### Objectif

Enchaîner deux runs avec `session_id=`, relire la fiche de la session et l'historique structuré tel que le prochain run l'enverra au modèle.

#### Mise en place

```console
$ cd ..
$ mkdir -p ch07b/agents ch07b/prompts
$ cp outils.py ch07b/
$ cp prompts/devis.md ch07b/prompts/
$ cp ch07a/agents/devis.yaml ch07b/agents/
$ cd ch07b
```

Fichier `ch07b/loom.yaml`. Seule la dernière ligne change par rapport à 7.1 (backend `sqlite`, extra `sqlite` installé plus haut) :

```yaml
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
        # Question sur 1 250 € HT : l'outil, puis la réponse
        - with_text: 1 250
          text: Je calcule le prix TTC.
          tool_calls:
            - name: prix_ttc
              arguments: {montant_ht: 1250, taux_tva: 10}
        - with_text: 1 250
          text: Pour 1 250 € HT avec une TVA à 10 %, le client paie 1 375 € TTC.
        # Question de suivi sur la TVA à 20 %
        - with_text: 20 %
          text: Avec une TVA à 20 %, le client paie 1 500 € TTC.

telemetry:
  logging: {level: WARNING}

storage:
  events: {backend: sqlite, path: data/journal.db}   # une base SQLite (extra « sqlite »)
```

Fichier `ch07b/agents/devis.yaml` : identique à celui de 7.1. Fichier `ch07b/conversation.py` :

```python
import asyncio

from loom_ia.access import Loom
from loom_ia.core.projections import history


async def main() -> None:
    async with Loom.from_config("loom.yaml") as loom:
        premier = await loom.run(
            "devis",
            "Quel prix TTC pour 1 250 € HT en rénovation (TVA à 10 %) ?",
            session_id="chantier-durand",
        )
        second = await loom.run(
            "devis", "Et avec une TVA à 20 % ?", session_id="chantier-durand"
        )
        print(premier.text)
        print(second.text)
        print("même session :", premier.session_id == second.session_id)

        fiche = await loom.session("chantier-durand")
        print(f"{len(fiche.runs)} runs, {fiche.last_seq} événements")

        # L'historique que le prochain run enverra au modèle, avec sa structure.
        evenements = await loom.export_session("chantier-durand")
        for message in history(evenements):
            blocs = [type(bloc).__name__ for bloc in message.blocks]
            print(f"{message.role:10} {blocs}")


asyncio.run(main())
```

#### Exécution

```console
$ uv run python conversation.py
Pour 1 250 € HT avec une TVA à 10 %, le client paie 1 375 € TTC.
Avec une TVA à 20 %, le client paie 1 500 € TTC.
même session : True
2 runs, 25 événements
user       ['TextBlock']
assistant  ['TextBlock', 'ToolCallBlock']
tool       ['ToolResultBlock']
assistant  ['TextBlock']
user       ['TextBlock']
assistant  ['TextBlock']
```

`history()` transforme le journal en messages. On y voit la forme réelle d'une conversation avec un outil : le message `assistant` contient un bloc de texte **et** un bloc d'appel d'outil, le résultat revient dans un message `tool`, puis la réponse finale. C'est cette structure, et non un simple texte concaténé, qui est renvoyée au modèle.

Et la CLI relit la même base, sans rien changer :

```console
$ uv run loom sessions list
chantier-durand              25 événements   2026-10-10 17:39
$ ls data
journal.db
```

#### À retenir

- `Loom.from_config(...)` s'utilise avec `async with` : la sortie du bloc ferme proprement les connexions.
- `loom.session(id)` donne la fiche (`runs`, `last_seq`), `loom.export_session(id)` la liste des événements, et `history(evenements)` (de `loom_ia.core.projections`) les messages structurés. Les trois fonctionnent quel que soit le backend.
- Choix pratique : `jsonl` pour le développement (lisible, on peut le `grep`), `sqlite` pour une petite installation sur une seule machine. Pour plusieurs machines, voir Postgres dans [`03-production.md`](03-production.md).

### 7.3 Compacter une conversation qui s'allonge

#### Pourquoi

Chaque run renvoie tout l'historique au modèle : au bout de vingt échanges avec Mme Martin, vous payez vingt fois les mêmes anciens messages. La compaction remplace les vieux échanges par un résumé. Le risque est qu'un résumé oublie l'essentiel : un numéro de devis, un e-mail, un montant. loom le vérifie.

#### Objectif

Provoquer une compaction avec un seuil volontairement bas, observer le contrôle de fidélité rejeter un premier résumé incomplet, puis voir l'historique que recevra le modèle.

#### Mise en place

```console
$ cd ..
$ mkdir -p ch07c/agents
$ cd ch07c
```

Fichier `ch07c/loom.yaml`. Le modèle `RESUME` est un second modèle simulé, dédié aux résumés ; son premier résumé oublie volontairement le numéro de devis et l'e-mail, pour que le contrôle de fidélité ait quelque chose à refuser. Le seuil `over_tokens: 150` est artificiellement bas (voir plus bas pour les valeurs par défaut).

```yaml
version: 1

agents_dir: agents/

models:
  - id: SIMULE
    sdk: fake
    model: fake-1
    params:
      script:
        - with_text: D-2026-042
          text: >-
            Le devis D-2026-042 de Mme Martin (martin@example.fr) s'élève à 1 840 €
            pour le remplacement du chauffe-eau. Il a été envoyé début septembre.
        - with_text: délai
          text: La pose est prévue 15 jours après l'acceptation du devis.
        - with_text: relance
          text: Je prépare une relance cordiale à Mme Martin.
  - id: RESUME
    sdk: fake
    model: fake-resume
    params:
      script:
        # 1re tentative : le numéro de devis et l'e-mail manquent
        - text: Mme Martin a demandé le détail de son devis de 1 840 € et le délai de pose.
        # 2e tentative, après le diagnostic du contrôle de fidélité
        - text: >-
            Mme Martin (martin@example.fr) a demandé le détail du devis D-2026-042
            (1 840 €, remplacement du chauffe-eau) puis le délai de pose : 15 jours
            après acceptation.

sessions:
  compaction:
    model: RESUME          # un modèle dédié, moins cher que celui de l'agent
    over_tokens: 150       # seuil bas, pour voir la compaction sur trois échanges
    keep_last: 1           # le dernier échange reste tel quel
    fidelity_check: true   # vérifie que numéros, e-mails et montants survivent au résumé

telemetry:
  logging: {level: WARNING}

storage:
  events: {backend: jsonl, path: data}
```

Fichier `ch07c/agents/devis.yaml` (le prompt est écrit directement dans `system`, pour ne pas dépendre de `prompts/`) :

```yaml
name: devis
description: Répond aux questions sur les devis.

main:
  model: SIMULE
  system: Tu es l'assistant de la Plomberie Dupont. Tu réponds en une ou deux phrases.

max_iterations: 3
```

Fichier `ch07c/historique.py` :

```python
import asyncio

from loom_ia.access import Loom
from loom_ia.core.projections import history


async def main() -> None:
    async with Loom.from_config("loom.yaml") as loom:
        evenements = await loom.export_session("martin")

        print("--- ce que la compaction a écrit")
        for evenement in evenements:
            charge = evenement.payload
            if evenement.type == "guard.checked" and charge.guard == "fidelity":
                print(f"contrôle de fidélité : {charge.outcome} (tentative {charge.attempt}) {charge.reason}")
            if evenement.type == "session.compacted":
                print(
                    f"résumé jusqu'à l'événement {charge.up_to_seq} : "
                    f"{charge.tokens_before} -> {charge.tokens_after} tokens, fidélité {charge.fidelity}"
                )

        print("--- ce que verra le modèle au prochain run")
        for message in history(evenements):
            print(f"[{message.role}] {message.text[:100]}")


asyncio.run(main())
```

#### Exécution

Trois échanges dans la même session :

```console
$ uv run loom run devis "Rappelle-moi le devis D-2026-042 de Mme Martin (martin@example.fr)." --session martin --run-id martin-1
Le devis D-2026-042 de Mme Martin (martin@example.fr) s'élève à 1 840 € pour le remplacement du chauffe-eau. Il a été envoyé début septembre.

Statut     : completed · itérations : 1 · tokens : 181/60 · coût : 0.0000 $
Run        : martin-1
$ uv run loom run devis "Quel est le délai de pose ?" --session martin --run-id martin-2
La pose est prévue 15 jours après l'acceptation du devis.

Statut     : completed · itérations : 1 · tokens : 273/39 · coût : 0.0000 $
Run        : martin-2
$ uv run loom run devis "Prépare une relance." --session martin --run-id martin-3
Je prépare une relance cordiale à Mme Martin.

Statut     : completed · itérations : 1 · tokens : 319/36 · coût : 0.0000 $
Run        : martin-3
```

La compaction tourne en tâche de fond après le run ; la CLI attend sa fin avant de rendre la main. Voici ce qu'elle a écrit :

```console
$ uv run python historique.py
--- ce que la compaction a écrit
contrôle de fidélité : failed (tentative 1) repères absents du résumé : D-2026-042, 2026, 042, martin@example.fr
contrôle de fidélité : passed (tentative 2) 
résumé jusqu'à l'événement 8 : 101 -> 64 tokens, fidélité ok
contrôle de fidélité : failed (tentative 1) repères absents du résumé : martin@example.fr, D-2026-042, 2026, 042
contrôle de fidélité : passed (tentative 2) 
résumé jusqu'à l'événement 33 : 149 -> 64 tokens, fidélité ok
--- ce que verra le modèle au prochain run
[user] [Résumé des échanges précédents de cette conversation.]

Mme Martin (martin@example.fr) a demandé le
[user] Prépare une relance.
[assistant] Je prépare une relance cordiale à Mme Martin.
```

Lecture, de haut en bas :

1. Premier résumé, première tentative : refusé, car `D-2026-042` et `martin@example.fr` n'y figurent plus (les `2026` et `042` sont les morceaux du numéro, comptés séparément).
2. Deuxième tentative, avec le diagnostic : acceptée. Le résumé couvre les événements jusqu'au numéro 8 et passe de 101 à 64 tokens.
3. Le run suivant dépasse de nouveau le seuil : un second résumé est produit, qui inclut le premier (jusqu'à l'événement 33).
4. Au prochain run, le modèle verra un seul message `user` de résumé, puis le dernier échange conservé tel quel (`keep_last: 1`).

#### À retenir

- Réglages de `sessions.compaction` : `model` (le modèle qui résume, souvent un modèle moins cher), `over_tokens` (seuil de déclenchement, 12 000 par défaut), `hard_tokens` (limite dure, 150 000 par défaut), `keep_last` (échanges conservés intacts, 6 par défaut), `fidelity_check`. Ici 150 et 1 sont de simples valeurs de démonstration : en vrai, partez des défauts.
- Le contrôle de fidélité est **déterministe** (aucun appel de modèle) : il compare des repères textuels (références comme `D-2026-042`, adresses e-mail, nombres d'au moins trois chiffres) entre les messages d'origine et le résumé. Il réessaie une fois avec le diagnostic ; si le second résumé échoue encore, il est gardé avec `fidelity: warning` dans l'événement `session.compacted`.
- Piège : ce contrôle garantit que les identifiants survivent, pas que le sens est juste. Un résumé peut citer `D-2026-042` et se tromper sur le délai.
- En Python, `loom.compact(session_id)` déclenche la compaction à la demande et renvoie `None` s'il n'y a rien de nouveau à résumer.
- Les événements `session.compacted` et `guard.checked` restent dans le journal : la compaction se vérifie après coup, sans rejouer quoi que ce soit.

---

## Chapitre 8 — Variables `{{ }}` et profils dev/prod

Deux mécanismes évitent de dupliquer des fichiers : les **variables** (un même prompt, des valeurs différentes selon le client) et les **profils** (une même configuration, des exigences différentes selon l'environnement).

### 8.1 Variables dans un prompt

#### Pourquoi

Le prompt du niveau 1 dit « Tu es l'assistant d'un artisan ». Si demain la Plomberie Dupont et un autre artisan utilisent le même agent, vous ne voulez pas maintenir deux copies du prompt : vous voulez écrire `{{ entreprise }}` une fois et fournir la valeur à part.

#### Objectif

Mettre `{{ entreprise }}` et `{{ signature }}` dans le prompt, comprendre l'erreur qui apparaît tant que ces variables ne sont pas déclarées, puis vérifier le prompt réellement envoyé au modèle.

#### Mise en place

```console
$ cd ..
$ mkdir -p ch08/agents ch08/prompts
$ cp outils.py ch08/
$ cp ch07a/agents/devis.yaml ch08/agents/
$ cd ch08
```

Fichier `ch08/prompts/devis.md` :

```markdown
Tu es l'assistant de {{ entreprise }}. Tu réponds en français, en une ou deux phrases.

Pour tout calcul de prix, appelle l'outil `prix_ttc` au lieu de calculer
toi-même, et reprends les montants qu'il renvoie. Signe : {{ signature }}.
```

Fichier `ch08/agents/devis.yaml` (inchangé) :

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

Premier `ch08/loom.yaml`, **sans** déclaration des variables. `raw_exchanges: true` conserve dans le journal les requêtes envoyées au modèle, ce qui servira à vérifier le prompt rendu :

```yaml
version: 1

imports: [outils]
agents_dir: agents/
prompts_dir: prompts/

models:
  - id: SIMULE
    sdk: fake
    model: fake-1

telemetry:
  logging: {level: WARNING}
  capture: {raw_exchanges: true}   # garde les requêtes envoyées au modèle, pour le débogage

storage:
  events: {backend: jsonl, path: data}
```

Fichier `ch08/systeme.py`, qui relit dans le journal le prompt système effectivement envoyé :

```python
import asyncio
import json

from loom_ia.access import Loom


async def main() -> None:
    async with Loom.from_config("loom.yaml") as loom:
        resultat = await loom.run("devis", "Bonjour")
        for evenement in await loom.export_session(resultat.session_id):
            if evenement.type == "model.exchanged":
                requete = json.loads(evenement.payload.request_body)
                print(requete["system"])


asyncio.run(main())
```

#### Exécution

```console
$ uv run loom validate
Configuration : /chemin/vers/mon-agent/ch08/loom.yaml: Agent 'devis', main.system — {{ entreprise }} : variable non définie pour le client 'default' (tenants[].variables)
```

L'erreur est détectée au chargement, avant tout appel de modèle. Seules les variables déclarées dans `tenants[].variables` sont autorisées dans un prompt système. Même avec un seul client, il faut nommer le client par défaut (`id: default`) pour lui donner des variables. Second `ch08/loom.yaml`, avec le bloc `tenants` ajouté à la fin :

```yaml
version: 1

imports: [outils]
agents_dir: agents/
prompts_dir: prompts/

models:
  - id: SIMULE
    sdk: fake
    model: fake-1

telemetry:
  logging: {level: WARNING}
  capture: {raw_exchanges: true}   # garde les requêtes envoyées au modèle, pour le débogage

storage:
  events: {backend: jsonl, path: data}

tenants:
  - id: default                       # le client par défaut, nommé pour lui donner des variables
    variables:
      entreprise: la Plomberie Dupont
      signature: "L'équipe Dupont"
```

```console
$ uv run loom validate
Config     : loom.yaml
Profil     : aucun (ni --profile, ni LOOM_PROFILE, ni 'profile:') ; surcharges : aucune
Modèles    : SIMULE
Agents     : devis
Outils     : prix_ttc
…
Clients    : default

  client default
    variables : entreprise, signature
    devis : modèle SIMULE, 1 outil(s) Python

1 agent(s) monté(s) sans erreur.
$ uv run python systeme.py
Tu es l'assistant de la Plomberie Dupont. Tu réponds en français, en une ou deux phrases.

Pour tout calcul de prix, appelle l'outil `prix_ttc` au lieu de calculer
toi-même, et reprends les montants qu'il renvoie. Signe : L'équipe Dupont.
```

Le prompt envoyé au modèle contient bien « la Plomberie Dupont » et « L'équipe Dupont » à la place des variables.

#### À retenir

- Un `tenant` est un client de votre application ; `variables` lui donne ses valeurs. Un second client se déclare avec un second `id` et d'autres valeurs : le prompt reste le même fichier. Le chapitre sur le multi-client est dans [`03-production.md`](03-production.md).
- Piège : une variable inconnue **bloque le chargement** (`variable non définie pour le client 'default'`), elle n'est pas remplacée par une chaîne vide. C'est voulu : un prompt qui commencerait par « Tu es l'assistant de . » passerait inaperçu en production.
- Écart doc/réel : le README présente les variables sans préciser qu'il faut déclarer explicitement `tenants: [{id: default, ...}]` même pour un seul client. Sans ce bloc, l'erreur ci-dessus apparaît.
- Seule la première partie de l'expression compte pour savoir si la variable existe : le nom `entreprise` doit être clé de `variables`.
- Gardez `raw_exchanges` pour le développement : il stocke le contenu complet des échanges avec le modèle dans le journal.

### 8.2 Profils : dev souple, prod exigeant

#### Pourquoi

En développement, on veut avancer : un budget exprimé en dollars sur un modèle simulé qui n'a pas de tarif, ce n'est qu'un détail. En production, c'est une erreur silencieuse : votre plafond de coût ne plafonne rien. Un profil `prod` transforme ces détails en erreurs, avant que l'agent ne serve un client.

#### Objectif

Déclarer un plafond de coût par run, voir `loom validate` l'accepter en `dev` et le refuser en `prod`, apprendre l'ordre de priorité entre `--profile`, `LOOM_PROFILE` et `profile:`, puis corriger en ajoutant un tarif dans le profil `prod`.

#### Mise en place

On reste dans `ch08/`. Fichier `ch08/loom.yaml` (troisième version) : `profile: dev` fixe le profil par défaut, `budgets.run.max_cost` pose un plafond de 0,05 $, et `profiles.prod` liste ce qui change quand on active `prod`.

```yaml
version: 1
profile: dev                  # profil par défaut de ce fichier

imports: [outils]
agents_dir: agents/
prompts_dir: prompts/

models:
  - id: SIMULE
    sdk: fake
    model: fake-1

budgets:
  run: {max_cost: 0.05}       # un plafond en dollars...

telemetry:
  logging: {level: WARNING}
  capture: {raw_exchanges: true}

storage:
  events: {backend: jsonl, path: data}

tenants:
  - id: default
    variables:
      entreprise: la Plomberie Dupont
      signature: "L'équipe Dupont"

profiles:
  prod:                       # surcharges appliquées avec --profile prod
    telemetry:
      logging: {level: INFO}
      capture: {raw_exchanges: false}
```

#### Exécution

Profil par défaut du fichier (`dev`) : la configuration est acceptée, avec un avertissement :

```console
$ uv run loom validate
Config     : loom.yaml
Profil     : dev (par config) — assoupli ; surcharges : prod
Modèles    : SIMULE
Agents     : devis
Outils     : prix_ttc
…
Clients    : default

  client default
    variables : entreprise, signature
    devis : modèle SIMULE, 1 outil(s) Python
      politique loom.budget : before_model
      budget : run max_cost 0.05 ; stop

1 agent(s) monté(s) sans erreur.
2026-10-10 17:39:08 WARNING  loom_ia.runtime.wiring — Agent 'devis' : budget en dollars, mais sans tarif pour SIMULE : leurs appels comptent 0 $
```

En `prod`, ce même avertissement devient une erreur. Trois façons de choisir le profil, par ordre de priorité décroissante : l'option `--profile`, la variable d'environnement `LOOM_PROFILE`, la clé `profile:` du fichier.

```console
$ uv run loom --profile prod validate
Config     : loom.yaml
Profil     : prod (par option) — les avertissements sont des erreurs ; surcharges : prod
Modèles    : SIMULE
Agents     : devis
Outils     : prix_ttc
…
Clients    : default

  client default
    variables : entreprise, signature
Configuration : Profil prod : Agent 'devis' : budget en dollars, mais sans tarif pour SIMULE : leurs appels comptent 0 $
$ LOOM_PROFILE=prod uv run loom validate
Config     : loom.yaml
Profil     : prod (par LOOM_PROFILE) — les avertissements sont des erreurs ; surcharges : prod
Modèles    : SIMULE
Agents     : devis
Outils     : prix_ttc
…
Clients    : default

  client default
    variables : entreprise, signature
Configuration : Profil prod : Agent 'devis' : budget en dollars, mais sans tarif pour SIMULE : leurs appels comptent 0 $
```

L'option l'emporte sur la variable :

```console
$ LOOM_PROFILE=prod uv run loom --profile dev validate
Config     : loom.yaml
Profil     : dev (par option) — assoupli ; surcharges : prod
Modèles    : SIMULE
Agents     : devis
Outils     : prix_ttc
…
Clients    : default

  client default
    variables : entreprise, signature
    devis : modèle SIMULE, 1 outil(s) Python
      politique loom.budget : before_model
      budget : run max_cost 0.05 ; stop

1 agent(s) monté(s) sans erreur.
2026-10-10 17:39:10 WARNING  loom_ia.runtime.wiring — Agent 'devis' : budget en dollars, mais sans tarif pour SIMULE : leurs appels comptent 0 $
```

Pour corriger, on déclare un tarif pour le modèle dans le profil `prod`. Une liste (`models`) est **remplacée** en entier par la surcharge, d'où la redéfinition complète du modèle. Quatrième version de `ch08/loom.yaml` :

```yaml
version: 1
profile: dev                  # profil par défaut de ce fichier

imports: [outils]
agents_dir: agents/
prompts_dir: prompts/

models:
  - id: SIMULE
    sdk: fake
    model: fake-1

budgets:
  run: {max_cost: 0.05}       # un plafond en dollars...

telemetry:
  logging: {level: WARNING}
  capture: {raw_exchanges: true}

storage:
  events: {backend: jsonl, path: data}

tenants:
  - id: default
    variables:
      entreprise: la Plomberie Dupont
      signature: "L'équipe Dupont"

profiles:
  prod:                       # surcharges appliquées avec --profile prod
    telemetry:
      logging: {level: INFO}
      capture: {raw_exchanges: false}
    models:                   # une liste est remplacée en entier, pas fusionnée
      - id: SIMULE
        sdk: fake
        model: fake-1
        pricing: {input: 0.10, output: 0.50}   # ... qui exige un tarif
```

```console
$ uv run loom --profile prod validate
Config     : loom.yaml
Profil     : prod (par option) — les avertissements sont des erreurs ; surcharges : prod
Modèles    : SIMULE
Agents     : devis
Outils     : prix_ttc
…
Clients    : default

  client default
    variables : entreprise, signature
    devis : modèle SIMULE, 1 outil(s) Python
      politique loom.budget : before_model
      budget : run max_cost 0.05 ; stop

1 agent(s) monté(s) sans erreur.
```

Un run en `prod` applique les surcharges : le niveau de logs passe à `INFO` (on voit les lignes de journal) et la capture des échanges est coupée.

```console
$ uv run loom --profile prod run devis "Bonjour" --run-id bonjour-prod
Écho : Bonjour
2026-10-10 17:39:11 INFO     loom_ia.engine.loop — Modèle fake-1 (rôle main) : 0.00 s, 228 tokens, 0.00003 $ [run_id=bonjour-prod span_id=01a12677-dcda-7640-aaa3-aa0d0d948225 tenant_id=default]
2026-10-10 17:39:11 INFO     loom_ia.engine.loop — Transition ready_for_model → completed (agent devis) [run_id=bonjour-prod span_id=01a12677-dcd4-742a-85af-8a4c1db73b5b tenant_id=default]

Statut     : completed · itérations : 1 · tokens : 200/28 · coût : 0.0000 $
Run        : bonjour-prod
```

(« Écho : Bonjour » est la réponse par défaut d'un modèle simulé sans script.)

#### À retenir

- Priorité : `--profile` > `LOOM_PROFILE` > `profile:`. La ligne `Profil` de `loom validate` indique laquelle a gagné (« par option », « par LOOM_PROFILE », « par config »).
- `profiles.<nom>` est fusionné dans la configuration de base : les objets sont fusionnés clé par clé, les **listes sont remplacées**.
- Le profil `prod` transforme les avertissements en erreurs. Exemples rencontrés dans ce guide : un budget en dollars sans tarif de modèle (ici), un juge bloquant qui ne juge qu'une partie des runs ou qui utilise le même modèle que la sortie jugée (chapitre 14), `--judges skip` refusé (chapitre 14). D'autres vérifications concernent le déploiement (REST sans clés d'API, exporteur sans point d'arrivée, `serve --reload` refusé) : voir [`03-production.md`](03-production.md).
- Le profil `dev` n'assouplit qu'un point : l'exigence d'un journal durable pour les approbations (chapitre 9) devient un avertissement.
- Bonne pratique : lancez `uv run loom --profile prod validate` dans votre intégration continue, même si vous développez en `dev`.

---

## Chapitre 9 — Validation humaine

Un agent qui envoie un e-mail à un client ne se rattrape pas. loom permet de marquer un outil comme nécessitant l'accord d'un humain : le run se **met en pause** avant l'exécution de l'outil, et reprend quand quelqu'un approuve (l'outil s'exécute) ou refuse (l'outil ne s'exécute pas, le modèle en est informé).

Le décorateur `@tool` accepte pour cela `side_effects` (`none`, `reversible`, `irreversible`) et `approval` (`never`, `always` ou `policy`).

### 9.1 Approuver ou refuser depuis la CLI

#### Pourquoi

La relance de Mme Martin sera lue par une vraie cliente. Autant l'artisan relise l'e-mail avant son départ, et puisse dire « non » : M. Durand a déjà payé, il ne faut surtout pas le relancer.

#### Objectif

Marquer l'outil `envoyer_email` comme à validation obligatoire, lancer deux relances qui se mettent en pause, approuver l'une et refuser l'autre avec un motif.

#### Mise en place

```console
$ cd ..
$ mkdir -p ch09/agents ch09/prompts
$ cd ch09
```

Fichier `ch09/outils.py` :

```python
from loom_ia.tools import tool


@tool(side_effects="irreversible", approval="always")
def envoyer_email(destinataire: str, objet: str, corps: str) -> str:
    """Envoie un e-mail au client. Action irréversible : une validation humaine est requise."""
    return f"E-mail envoyé à {destinataire} (objet : {objet})"
```

Fichier `ch09/prompts/relance.md` :

```markdown
Tu es l'assistant de la Plomberie Dupont. Quand l'artisan demande une relance,
envoie-la avec l'outil `envoyer_email`, puis confirme en une phrase.
```

Fichier `ch09/agents/relance.yaml` :

```yaml
name: relance
description: Envoie les relances de devis de la Plomberie Dupont.

main:
  model: SIMULE
  system_file: relance.md

max_iterations: 5

tools:
  - python: envoyer_email
```

Fichier `ch09/loom.yaml`. Le script traite deux demandes : le devis D-2026-042 (Mme Martin) et le devis D-2026-043 (M. Durand). Dans les deux cas le modèle demande l'envoi ; la seconde réponse n'est jouée qu'après la décision.

```yaml
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
        # Demande sur le devis D-2026-042 : le modèle envoie la relance...
        - with_text: D-2026-042
          text: J'envoie la relance.
          tool_calls:
            - name: envoyer_email
              arguments:
                destinataire: mme.martin@example.fr
                objet: Votre devis D-2026-042
                corps: Bonjour Mme Martin, votre devis D-2026-042 de 1 840 € est toujours valable.
        # ... puis confirme, une fois l'outil exécuté
        - with_text: D-2026-042
          text: La relance du devis D-2026-042 est partie.
        # Demande sur le devis D-2026-043 : même scénario, autre destinataire
        - with_text: D-2026-043
          text: J'envoie la relance.
          tool_calls:
            - name: envoyer_email
              arguments:
                destinataire: m.durand@example.fr
                objet: Votre devis D-2026-043
                corps: Bonjour M. Durand, votre devis D-2026-043 est toujours valable.
        - with_text: D-2026-043
          text: "La relance n'est pas partie : la validation n'a pas été accordée."

telemetry:
  logging: {level: WARNING}

storage:
  events: {backend: jsonl, path: data}
```

#### Exécution

```console
$ uv run loom validate
Config     : loom.yaml
Profil     : aucun (ni --profile, ni LOOM_PROFILE, ni 'profile:') ; surcharges : aucune
Modèles    : SIMULE
Agents     : relance
Outils     : envoyer_email
…
  relance : modèle SIMULE, 1 outil(s) Python

1 agent(s) monté(s) sans erreur.
```

Le journal est durable (`jsonl`), condition nécessaire pour une pause (voir plus bas). Lançons la relance :

```console
$ uv run loom run relance "Relance Mme Martin pour le devis D-2026-042." --run-id relance-042
—

Statut     : paused · itérations : 1 · tokens : 380/100 · coût : 0.0000 $
Run        : relance-042
En attente : envoyer_email (fake_0_0) — loom approve relance-042 --call fake_0_0
```

Le run s'arrête avec le statut `paused` (code de sortie 1) et dit exactement ce qui attend : l'outil `envoyer_email`, l'identifiant d'appel `fake_0_0` et la commande à lancer. Le journal montre l'état :

```console
$ uv run loom inspect relance-042
Run        : relance-042 (agent relance, client default)
Session    : relance-042
Statut     : paused, 1 itération(s) — run inachevé : durées provisoires
Usage      : 380 → 100 tokens, 0.000000 $, 9 ms de pilotage

run relance — inachevé, 21 ms (ouvert)
  étape 1
    modèle fake-1 (main) — 2 ms, 380 → 100 tokens, 0.000000 $
      · répond    : J'envoie la relance.
      · appelle   : envoyer_email({"destinataire": "mme.martin@example.fr", "objet": "Votre devis D-2026-042", "corps…
  étape 2
    approbation pour envoyer_email — en attente

Bilan      : 1 appel(s) de modèle, 0 appel(s) d'outil, 1 approbation(s)
```

L'outil n'a pas été appelé (« 0 appel(s) d'outil »). On approuve :

```console
$ uv run loom approve relance-042 --call fake_0_0 --by denis --reason "ok pour la relance"
La relance du devis D-2026-042 est partie.
Accordé : fake_0_0

Statut     : completed · itérations : 2 · tokens : 950/135 · coût : 0.0000 $
Run        : relance-042
```

La commande reprend le run là où il s'était arrêté : l'outil s'exécute, le modèle reçoit son résultat et conclut. L'inspection montre la décision :

```console
$ uv run loom inspect relance-042
Run        : relance-042 (agent relance, client default)
Session    : relance-042
Statut     : completed, 2 itération(s)
Usage      : 950 → 135 tokens, 0.000000 $, 17 ms de pilotage

run relance — completed, 1.6 s, 0.000000 $
  étape 1
    modèle fake-1 (main) — 2 ms, 380 → 100 tokens, 0.000000 $
      · répond    : J'envoie la relance.
      · appelle   : envoyer_email({"destinataire": "mme.martin@example.fr", "objet": "Votre devis D-2026-042", "corps…
  étape 2
    approbation pour envoyer_email — accordée par denis
  étape 3
    outil envoyer_email — 2 ms
      · arguments : {"destinataire": "mme.martin@example.fr", "objet": "Votre devis D-2026-042", "corps": "Bonjour Mm…
      · résultat  : E-mail envoyé à mme.martin@example.fr (objet : Votre devis D-2026-042)
  étape 4
    modèle fake-1 (main) — 1 ms, 570 → 35 tokens, 0.000000 $
      · répond    : La relance du devis D-2026-042 est partie.

Réponse finale :
  La relance du devis D-2026-042 est partie.
Bilan      : 2 appel(s) de modèle, 1 appel(s) d'outil, 1 approbation(s)
```

Pour M. Durand, on refuse, en donnant un motif :

```console
$ uv run loom run relance "Relance M. Durand pour le devis D-2026-043." --run-id relance-043
—

Statut     : paused · itérations : 1 · tokens : 380/97 · coût : 0.0000 $
Run        : relance-043
En attente : envoyer_email (fake_0_0) — loom approve relance-043 --call fake_0_0
$ uv run loom reject relance-043 --call fake_0_0 --by denis --reason "M. Durand a déjà payé, inutile de le relancer."
La relance n'est pas partie : la validation n'a pas été accordée.
Refusé : fake_0_0

Statut     : completed · itérations : 2 · tokens : 948/138 · coût : 0.0000 $
Run        : relance-043
```

Le modèle reçoit le refus comme résultat d'outil, motif compris, et ne peut pas contourner le refus :

```console
$ uv run loom inspect relance-043
Run        : relance-043 (agent relance, client default)
Session    : relance-043
Statut     : completed, 2 itération(s)
Usage      : 948 → 138 tokens, 0.000000 $, 14 ms de pilotage

run relance — completed, 780 ms, 0.000000 $
  étape 1
    modèle fake-1 (main) — 1 ms, 380 → 97 tokens, 0.000000 $
      · répond    : J'envoie la relance.
      · appelle   : envoyer_email({"destinataire": "m.durand@example.fr", "objet": "Votre devis D-2026-043", "corps":…
  étape 2
    approbation pour envoyer_email — refusée par denis
  étape 3
    appel envoyer_email — refusé avant exécution
      · résultat  : (erreur) Appel à envoyer_email refusé : M. Durand a déjà payé, inutile de le relancer.
  étape 4
    modèle fake-1 (main) — 1 ms, 568 → 41 tokens, 0.000000 $
      · répond    : La relance n'est pas partie : la validation n'a pas été accordée.

Réponse finale :
  La relance n'est pas partie : la validation n'a pas été accordée.
Bilan      : 2 appel(s) de modèle, 1 appel(s) d'outil dont 1 refusé(s) avant exécution, 1 approbation(s)
```

**La contrainte de journal.** Une pause doit survivre à la fin du process : il faut un journal durable. Avec un journal `memory`, loom refuse de monter l'agent :

```console
$ sed 's#events: {backend: jsonl, path: data}#events: {backend: memory}#' loom.yaml > loom.memoire.yaml
$ uv run loom --config loom.memoire.yaml validate | tail -3
Configuration : Agent 'relance' : outil 'envoyer_email' en approval: always — une approbation exige un journal durable (jsonl ou sqlite ou postgres), pas 'memory' (#28)
…
```

En profil `dev`, ce même problème n'est qu'un avertissement (le seul point que `dev` assouplit) :

```console
$ uv run loom --config loom.memoire.yaml --profile dev validate | tail -3
2026-10-10 17:41:19 WARNING  loom_ia.runtime.wiring — Agent 'relance' : outil 'envoyer_email' en approval: always — une approbation exige un journal durable (jsonl ou sqlite ou postgres), pas 'memory' (#28)
  relance : modèle SIMULE, 1 outil(s) Python

1 agent(s) monté(s) sans erreur.
```

#### À retenir

- `loom approve <run_id> --call <call_id>` et `loom reject <run_id> --call <call_id> --reason "…"`. Options utiles : `--by` (qui décide, tracé dans le journal), `--reason`, `--session`, `--json`. `--call` est facultatif quand une seule approbation attend.
- Un run en pause se termine avec le code de sortie 1, une configuration refusée avec le code 2. Pensez-y dans un script.
- `loom approve` sur un run sans approbation en attente répond « rien n'attend de décision » (code 2) : l'opération est donc sans danger si on la lance deux fois.
- Piège : le journal par défaut est `memory`. Sans `profile: dev`, un outil en `approval: always` sur un journal en mémoire est une **erreur de configuration** (`une approbation exige un journal durable`). Écart doc/réel : le README dit que `dev` tolère un agent qui se met en pause sur un journal non durable, ce qui est exact, mais ne dit pas que, sans aucun profil, c'est une erreur, et que le journal par défaut est justement `memory`.
- Les outils d'un serveur MCP déclarés irréversibles ne demandent pas d'approbation tant que vous n'avez pas écrit `approval: always` (chapitre 13).

### 9.2 Corriger l'e-mail avant l'envoi, et garder la trace

#### Pourquoi

Souvent l'e-mail est presque bon : une adresse à changer, une phrase à adoucir. Refuser puis relancer tout le run serait lourd. Autant approuver **avec des arguments corrigés**. Et pour un audit, il faut pouvoir retrouver qui a décidé quoi, avec quels arguments.

#### Objectif

Approuver un envoi en remplaçant les arguments de l'outil, vérifier ce qui part réellement, puis relire la trace d'audit dans le journal exporté.

#### Mise en place

On reste dans `ch09/`. Fichier `ch09/approbations.py`, qui n'affiche que les événements `approval.*` d'un journal exporté :

```python
import json
import sys

# Les événements d'approbation d'une session : demande, décision, expiration.
for ligne in open(sys.argv[1], encoding="utf-8"):
    evenement = json.loads(ligne)
    if evenement["type"].startswith("approval."):
        charge = evenement["payload"]
        charge.pop("type")
        print(evenement["type"], json.dumps(charge, ensure_ascii=False))
```

#### Exécution

On relance la demande pour Mme Martin (nouvelle session), puis on approuve en corrigeant adresse et texte :

```console
$ uv run loom run relance "Relance Mme Martin pour le devis D-2026-042." --run-id relance-042b
—

Statut     : paused · itérations : 1 · tokens : 380/100 · coût : 0.0000 $
Run        : relance-042b
En attente : envoyer_email (fake_0_0) — loom approve relance-042b --call fake_0_0
$ uv run loom approve relance-042b --call fake_0_0 --by denis --arguments '{"destinataire": "martin.chauffe-eau@example.fr", "objet": "Votre devis D-2026-042", "corps": "Bonjour Mme Martin, votre devis D-2026-042 de 1 840 € reste valable jusqu a fin octobre."}'
La relance du devis D-2026-042 est partie.
Accordé : fake_0_0

Statut     : completed · itérations : 2 · tokens : 957/135 · coût : 0.0000 $
Run        : relance-042b
```

On vérifie ce qui est réellement parti, en demandant le détail de l'inspection :

```console
$ uv run loom inspect relance-042b --full
Run        : relance-042b (agent relance, client default)
Session    : relance-042b
Statut     : completed, 2 itération(s)
Usage      : 957 → 135 tokens, 0.000000 $, 21 ms de pilotage

run relance — completed, 743 ms, 0.000000 $
  étape 1
    modèle fake-1 (main) — 1 ms, 380 → 100 tokens, 0.000000 $
      · répond    : J'envoie la relance.
      · appelle   : envoyer_email({"destinataire": "mme.martin@example.fr", "objet": "Votre devis D-2026-042", "corps": "Bonjour Mme Martin, votre devis D-2026-042 de 1 840 € est toujours valable."})
  étape 2
    approbation pour envoyer_email — accordée par denis
  étape 3
    outil envoyer_email — 2 ms
      · arguments : {"destinataire": "mme.martin@example.fr", "objet": "Votre devis D-2026-042", "corps": "Bonjour Mme Martin, votre devis D-2026-042 de 1 840 € est toujours valable."}
      · résultat  : E-mail envoyé à martin.chauffe-eau@example.fr (objet : Votre devis D-2026-042)
  étape 4
    modèle fake-1 (main) — 1 ms, 577 → 35 tokens, 0.000000 $
      · répond    : La relance du devis D-2026-042 est partie.

Réponse finale :
  La relance du devis D-2026-042 est partie.
Bilan      : 2 appel(s) de modèle, 1 appel(s) d'outil, 1 approbation(s)
```

Observez l'écart entre les deux lignes : `arguments` affiche ce que le **modèle** avait demandé (adresse `mme.martin@example.fr`), mais le résultat (« E-mail envoyé à martin.chauffe-eau@example.fr ») prouve que c'est la version corrigée qui a été exécutée. La trace complète est dans le journal :

```console
$ uv run loom sessions export relance-042b --out relance-042b.jsonl
25 événements écrits dans relance-042b.jsonl
$ uv run python approbations.py relance-042b.jsonl
approval.requested {"call_id": "fake_0_0", "tool_name": "envoyer_email", "arguments": {"destinataire": "mme.martin@example.fr", "objet": "Votre devis D-2026-042", "corps": "Bonjour Mme Martin, votre devis D-2026-042 de 1 840 € est toujours valable."}, "reason": "outil déclaré à approbation obligatoire (effets : irreversible)", "policy": null, "scope": "approve", "expire_at": "2026-10-11T15:41:15.029790Z"}
approval.granted {"call_id": "fake_0_0", "tool_name": "envoyer_email", "by": "denis", "reason": "", "arguments": {"destinataire": "martin.chauffe-eau@example.fr", "objet": "Votre devis D-2026-042", "corps": "Bonjour Mme Martin, votre devis D-2026-042 de 1 840 € reste valable jusqu a fin octobre."}}
```

L'événement `approval.requested` contient la demande d'origine, son motif et une date d'expiration ; `approval.granted` contient `by` et les arguments corrigés. Enfin, une seconde validation du même run ne fait rien :

```console
$ uv run loom approve relance-042b
Run relance-042b : rien n'attend de décision.
```

#### À retenir

- `--arguments '<JSON>'` remplace **tous** les arguments de l'appel (pas une fusion partielle) et exige `--call`. Écrivez le JSON complet.
- Piège : `loom inspect` continue d'afficher les arguments d'origine dans la ligne de l'appel d'outil ; la source de vérité pour l'audit est l'événement `approval.granted` et le résultat de l'outil.
- Pour un audit, exportez avec `loom sessions export` (chapitre 7) et filtrez `approval.*`, comme ci-dessus.

### 9.3 Décider depuis du code, et laisser une demande expirer

#### Pourquoi

Tous les e-mails ne méritent pas un humain : une relance à un client connu peut partir seule, tandis que tout destinataire inconnu doit s'arrêter. Par ailleurs, une validation oubliée ne doit pas rester en attente éternellement.

#### Objectif

Piloter les approbations depuis Python, sous deux formes : asynchrone (le run rend la main, on décide plus tard) et en ligne (une fonction décide pendant le run). Puis laisser expirer une demande.

#### Mise en place

On reste dans `ch09/`. Fichier `ch09/validation.py` :

```python
import asyncio

from loom_ia.access import Loom
from loom_ia.core.model import Approved, PendingApproval, Rejected


async def pas_de_relance_hors_mme_martin(demande: PendingApproval):
    """Approbateur en ligne : décide dans la boucle, sans pause."""
    if demande.arguments["destinataire"] == "mme.martin@example.fr":
        return Approved(by="regle-auto", reason="destinataire connu")
    return Rejected(by="regle-auto", reason="destinataire inconnu")


async def main() -> None:
    async with Loom.from_config("loom.yaml") as loom:
        # 1. Asynchrone : le run se met en pause et rend ce qu'il attend.
        resultat = await loom.run("relance", "Relance Mme Martin pour le devis D-2026-042.")
        print(resultat.status, [a.tool_name for a in resultat.pending_approvals])
        attente = resultat.pending_approvals[0]

        # 2. Un humain tranche plus tard (ici, tout de suite) ; le run reprend.
        accordes = await loom.approve(resultat.run_id, call_id=attente.call_id, by="denis")
        print("accordé :", accordes)
        await loom.drain()
        final = await loom.result(resultat.run_id)
        print(final.status, final.text)

        # 3. En ligne : l'approbateur décide pendant le run, sans pause.
        for demande in (
            "Relance Mme Martin pour le devis D-2026-042.",
            "Relance M. Durand pour le devis D-2026-043.",
        ):
            direct = await loom.run("relance", demande, approver=pas_de_relance_hors_mme_martin)
            print(direct.status, direct.text)


asyncio.run(main())
```

Fichier `ch09/agents/relance_vite.yaml`, un agent identique dont les demandes expirent en 2 secondes :

```yaml
name: relance_vite
description: Comme « relance », mais une validation non donnée sous 2 secondes expire.

main:
  model: SIMULE
  system_file: relance.md

max_iterations: 5

tools:
  - python: envoyer_email

approval: {expires_in: 2, on_expiry: deny}   # 24 h et « deny » par défaut
```

#### Exécution

```console
$ uv run python validation.py
paused ['envoyer_email']
accordé : ('fake_0_0',)
completed La relance du devis D-2026-042 est partie.
completed La relance du devis D-2026-042 est partie.
completed La relance n'est pas partie : la validation n'a pas été accordée.
```

Dans l'ordre : le run rend `paused` avec la liste de ce qu'il attend ; `loom.approve(...)` renvoie les identifiants d'appel accordés ; `loom.drain()` laisse le run reprendre et `loom.result(run_id)` donne le résultat final ; enfin, avec `approver=`, la fonction répond `Approved` ou `Rejected` pendant le run, sans pause. Le dernier cas refuse M. Durand, d'où le texte de refus.

Expiration. On lance la relance, on attend 3 secondes (plus que les 2 secondes de `expires_in`) et on reprend le run :

```console
$ uv run loom run relance_vite "Relance M. Durand pour le devis D-2026-043." --run-id vite-043
—

Statut     : paused · itérations : 1 · tokens : 380/97 · coût : 0.0000 $
Run        : vite-043
En attente : envoyer_email (fake_0_0) — loom approve vite-043 --call fake_0_0
$ sleep 3 && uv run loom resume vite-043
La relance n'est pas partie : la validation n'a pas été accordée.

Statut     : completed · itérations : 2 · tokens : 955/138 · coût : 0.0000 $
Run        : vite-043
$ uv run loom inspect vite-043
Run        : vite-043 (agent relance_vite, client default)
Session    : vite-043
Statut     : completed, 2 itération(s)
Usage      : 955 → 138 tokens, 0.000000 $, 12 ms de pilotage

run relance_vite — completed, 3.7 s, 0.000000 $
  · approbation expirée
  étape 1
    modèle fake-1 (main) — 1 ms, 380 → 97 tokens, 0.000000 $
      · répond    : J'envoie la relance.
      · appelle   : envoyer_email({"destinataire": "m.durand@example.fr", "objet": "Votre devis D-2026-043", "corps":…
  étape 2
    approbation pour envoyer_email — en attente
  étape 3
    appel envoyer_email — refusé avant exécution
      · résultat  : (erreur) Appel à envoyer_email sans approbation dans le délai imparti : sans réponse avant 2026-1…
  étape 4
    modèle fake-1 (main) — 1 ms, 575 → 41 tokens, 0.000000 $
      · répond    : La relance n'est pas partie : la validation n'a pas été accordée.

Réponse finale :
  La relance n'est pas partie : la validation n'a pas été accordée.
Bilan      : 2 appel(s) de modèle, 1 appel(s) d'outil dont 1 refusé(s) avant exécution, 1 approbation(s)
```

Le run a repris, l'approbation est marquée « expirée » et l'appel est refusé : le modèle en est informé et l'e-mail n'est jamais parti.

#### À retenir

- Python : `loom.approve(run_id, call_id=, by=, reason=, arguments=)` et `loom.reject(...)`. `Approved(by, reason, arguments)` et `Rejected(reason, by)` viennent de `loom_ia.core.model`.
- `approval: {expires_in, on_expiry: deny|fail}` se règle dans l'agent. Par défaut : 24 heures et `deny` (le modèle est informé du refus et continue) ; `fail` fait échouer le run.
- Écart doc/réel : l'expiration n'est évaluée que **quand le run est repris ou consulté** (`loom resume`), pas par une horloge de fond. Constaté : `loom approve` lancé après l'échéance, mais avant toute reprise du run, est encore accepté. Si une échéance doit être stricte, ne comptez pas sur la seule date : refusez dans votre propre code (une politique `before_tool`, chapitre 10) ou reprenez régulièrement les runs en attente.
- Un approbateur en ligne convient aux règles automatiques ; gardez la voie asynchrone (pause) pour les décisions qui demandent vraiment un humain.

---

## Chapitre 10 — Politiques : des règles qui s'exécutent à des points précis

Une **politique** est une fonction Python que loom appelle à un point précis de la boucle d'exécution, et qui décide de la suite. Contrairement à une consigne dans le prompt, qu'un modèle peut ignorer, une politique est du code : elle s'applique à tous les coups.

Cinq points existent, et chacun n'autorise qu'un jeu de décisions :

| Point | Moment | Décisions permises |
|---|---|---|
| `before_model` | avant chaque appel de modèle | `continue`, `replace`, `stop`, `fail` |
| `after_model` | juste après la réponse du modèle | `continue`, `retry`, `stop`, `fail` |
| `before_tool` | avant l'exécution d'un outil | `continue`, `replace`, `deny`, `pause`, `fail` |
| `after_tool` | après l'exécution d'un outil | `continue`, `replace`, `retry`, `fail` |
| `on_output` | sur la réponse finale | `continue`, `replace`, `retry`, `fail` |

Les décisions se construisent avec `CONTINUE`, `Replace`, `Deny`, `Retry`, `Stop`, `Fail` et `Pause`, importés de `loom_ia.policies`. Écart doc/réel : le README nomme « Réparer » (« Repair ») la décision qui renvoie un diagnostic au modèle pour qu'il recommence ; dans le code et dans les journaux, elle s'appelle `Retry` (`decisions=["retry"]`, `politique X (point) : retry`).

### 10.1 Refuser et corriger les appels d'outils

#### Pourquoi

Le modèle propose `prix_ttc(1250, taux_tva=15)`. Or il n'existe pas de TVA à 15 % en France pour les travaux de plomberie. Plutôt que d'espérer que le prompt soit assez clair, une politique refuse l'appel et explique pourquoi ; le modèle se corrige tout seul. Autre cas : il écrit le numéro de devis `" d-2026-042 "`. Une politique le normalise avant l'exécution.

#### Objectif

Écrire deux politiques `before_tool` : `taux_de_tva` (qui refuse) et `numero_devis` (qui corrige), les brancher dans deux agents, et lire leurs effets dans `loom inspect`.

#### Mise en place

```console
$ cd ..
$ mkdir -p ch10/agents ch10/prompts
$ cp prompts/devis.md ch10/prompts/
$ cd ch10
```

Fichier `ch10/outils.py` : `prix_ttc` du niveau 1, plus `chercher_devis`. L'exception `ToolError` signale une erreur « normale » (devis introuvable) que le modèle peut lire et corriger. Elle s'importe de `loom_ia.core.ports`.

```python
from loom_ia.core.ports import ToolError
from loom_ia.tools import tool


@tool
def prix_ttc(montant_ht: float, taux_tva: float = 20.0) -> dict[str, float]:
    """Calcule la TVA et le montant TTC d'un devis. Le taux est en pourcentage."""
    tva = round(montant_ht * taux_tva / 100, 2)
    return {"montant_ht": montant_ht, "tva": tva, "montant_ttc": round(montant_ht + tva, 2)}


DEVIS = {
    "D-2026-042": {
        "numero": "D-2026-042",
        "client": "Mme Martin",
        "montant_ttc": 1840.0,
    },
}


@tool
def chercher_devis(numero: str) -> dict:
    """Retourne le devis d'un client à partir de son numéro (ex. D-2026-042)."""
    if numero not in DEVIS:
        raise ToolError(f"Devis {numero} introuvable.")
    return DEVIS[numero]
```

Fichier `ch10/politiques.py` :

```python
import re

from loom_ia.policies import (
    CONTINUE,
    BeforeTool,
    Decision,
    Deny,
    PolicyContext,
    Replace,
    policy,
)


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


@policy(points=["before_tool"], decisions=["replace", "deny"])
def numero_devis(subject: BeforeTool) -> Decision:
    """Normalise le numéro de devis, refuse un numéro mal formé."""
    if subject.spec.name != "chercher_devis":
        return CONTINUE
    brut = str(subject.arguments.get("numero", ""))
    numero = brut.strip().upper()
    if not re.fullmatch(r"D-\d{4}-\d{3}", numero):
        return Deny(f"numéro de devis {brut!r} mal formé ; format attendu : D-AAAA-NNN")
    if numero == brut:
        return CONTINUE
    return Replace({**subject.arguments, "numero": numero}, reason="numéro normalisé")
```

Une politique est une fonction décorée par `@policy(points=[...], decisions=[...])`. Elle reçoit le sujet du point (ici `BeforeTool`, avec `spec.name` et `arguments`) et, si on le demande, un `PolicyContext` (ici `context.params`, venu du YAML). `decisions` liste ce qu'elle peut renvoyer : c'est vérifié au chargement.

Fichier `ch10/agents/devis.yaml` :

```yaml
name: devis
description: Répond aux questions de prix sur les devis.

main:
  model: SIMULE
  system_file: devis.md

max_iterations: 6

tools:
  - python: prix_ttc

policies:
  - hook: taux_de_tva
    params: {taux: [20, 10, 5.5, 2.1]}
```

Fichier `ch10/agents/consultation.yaml` :

```yaml
name: consultation
description: Retrouve un devis par son numéro.

main:
  model: SIMULE_NUMERO
  system: Tu retrouves les devis avec l'outil `chercher_devis`.

max_iterations: 5

tools:
  - python: chercher_devis

policies:
  - hook: numero_devis
```

Fichier `ch10/loom.yaml`. Dans le premier scénario, le modèle commence par se tromper de taux (15), puis se corrige après le refus. Dans le second, il écrit le numéro de façon approximative.

```yaml
version: 1

imports: [outils, politiques]
agents_dir: agents/
prompts_dir: prompts/

models:
  - id: SIMULE
    sdk: fake
    model: fake-1
    params:
      script:
        # 1er essai : le modèle se trompe de taux
        - text: Je calcule le prix TTC.
          tool_calls:
            - name: prix_ttc
              arguments: {montant_ht: 1250, taux_tva: 15}
        # 2e essai, après le refus de la politique
        - text: Le taux de 15 % n'existe pas, je corrige.
          tool_calls:
            - name: prix_ttc
              arguments: {montant_ht: 1250, taux_tva: 10}
        - text: Pour 1 250 € HT avec une TVA à 10 %, votre client paiera 1 375 € TTC.
  - id: SIMULE_NUMERO
    sdk: fake
    model: fake-numero
    params:
      script:
        # Le modèle écrit le numéro à sa façon
        - tool_calls:
            - name: chercher_devis
              arguments: {numero: " d-2026-042 "}
        - text: Le devis D-2026-042 de Mme Martin s'élève à 1 840 € TTC.

telemetry:
  logging: {level: WARNING}

storage:
  events: {backend: jsonl, path: data}
```

#### Exécution

```console
$ uv run loom validate | tail -6
  consultation : modèle SIMULE_NUMERO, 1 outil(s) Python
    politique numero_devis : before_tool
  devis : modèle SIMULE, 1 outil(s) Python
    politique taux_de_tva : before_tool

2 agent(s) monté(s) sans erreur.
```

Le modèle se trompe de taux, la politique refuse, le modèle corrige :

```console
$ uv run loom run devis "Quel est le prix TTC de 1 250 € HT ?" --run-id tva-1
Pour 1 250 € HT avec une TVA à 10 %, votre client paiera 1 375 € TTC.

Statut     : completed · itérations : 3 · tokens : 1348/183 · coût : 0.0000 $
Run        : tva-1
$ uv run loom inspect tva-1
Run        : tva-1 (agent devis, client default)
Session    : tva-1
Statut     : completed, 3 itération(s)
Usage      : 1348 → 183 tokens, 0.000000 $, 17 ms de pilotage

run devis — completed, 31 ms, 0.000000 $
  étape 1
    modèle fake-1 (main) — 1 ms, 284 → 68 tokens, 0.000000 $
      · répond    : Je calcule le prix TTC.
      · appelle   : prix_ttc({"montant_ht": 1250, "taux_tva": 15})
  étape 2
    appel prix_ttc — refusé avant exécution
      · politique taux_de_tva (before_tool) : deny
      · résultat  : (erreur) Appel refusé (taux_de_tva) : taux de TVA 15 % inconnu ; taux possibles : [20, 10, 5.5, 2…
  étape 3
    modèle fake-1 (main) — 1 ms, 447 → 73 tokens, 0.000000 $
      · répond    : Le taux de 15 % n'existe pas, je corrige.
      · appelle   : prix_ttc({"montant_ht": 1250, "taux_tva": 10})
  étape 4
    outil prix_ttc — 2 ms
      · arguments : {"montant_ht": 1250, "taux_tva": 10}
      · résultat  : {"montant_ht": 1250.0, "tva": 125.0, "montant_ttc": 1375.0}
  étape 5
    modèle fake-1 (main) — 1 ms, 617 → 42 tokens, 0.000000 $
      · répond    : Pour 1 250 € HT avec une TVA à 10 %, votre client paiera 1 375 € TTC.

Réponse finale :
  Pour 1 250 € HT avec une TVA à 10 %, votre client paiera 1 375 € TTC.
Bilan      : 3 appel(s) de modèle, 2 appel(s) d'outil dont 1 refusé(s) avant exécution
```

Dans `inspect`, l'étape 2 montre le refus (« politique taux_de_tva (before_tool) : deny ») et son message, que le modèle reçoit comme résultat d'outil. Le run n'échoue pas : le modèle s'adapte à la 3e itération. Seconde politique :

```console
$ uv run loom run consultation "Où en est le devis D-2026-042 ?" --run-id num-1
Le devis D-2026-042 de Mme Martin s'élève à 1 840 € TTC.

Statut     : completed · itérations : 2 · tokens : 512/83 · coût : 0.0000 $
Run        : num-1
$ uv run loom inspect num-1
Run        : num-1 (agent consultation, client default)
Session    : num-1
Statut     : completed, 2 itération(s)
Usage      : 512 → 83 tokens, 0.000000 $, 14 ms de pilotage

run consultation — completed, 29 ms, 0.000000 $
  étape 1
    modèle fake-numero (main) — 1 ms, 182 → 44 tokens, 0.000000 $
      · appelle   : chercher_devis({"numero": " d-2026-042 "})
  étape 2
    outil chercher_devis — 3 ms
      · politique numero_devis (before_tool) : replace
      · arguments : {"numero": " d-2026-042 "}
      · résultat  : {"numero": "D-2026-042", "client": "Mme Martin", "montant_ttc": 1840.0}
  étape 3
    modèle fake-numero (main) — 1 ms, 330 → 39 tokens, 0.000000 $
      · répond    : Le devis D-2026-042 de Mme Martin s'élève à 1 840 € TTC.

Réponse finale :
  Le devis D-2026-042 de Mme Martin s'élève à 1 840 € TTC.
Bilan      : 2 appel(s) de modèle, 1 appel(s) d'outil
```

Ici la politique n'a pas refusé : elle a remplacé les arguments (`replace`). L'outil a reçu `D-2026-042` bien que le modèle ait demandé `" d-2026-042 "`.

#### À retenir

- `Deny(message)` : l'appel n'a pas lieu, le message part au modèle comme résultat d'erreur. `Replace(arguments, reason=)` : l'appel a lieu avec d'autres arguments.
- `params:` dans le YAML (`taux: [20, 10, 5.5, 2.1]`) arrive dans `context.params` : une même politique, configurable par agent.
- Une politique peut être synchrone ou asynchrone. Le message d'un `Deny` est lu par le modèle : écrivez-le comme une instruction (« taux possibles : … »), pas comme un code d'erreur.
- Un `Replace` se repère dans `inspect` par la ligne « politique … : replace » : l'appel affiché est celui que le modèle avait écrit, le résultat est celui de l'appel corrigé.
- Piège : une politique ne doit renvoyer que les décisions déclarées dans `decisions=[...]` **et** permises au point choisi.

### 10.2 Garde-fous de bout en bout : forcer, borner, réparer

#### Pourquoi

Les politiques de 10.1 corrigent un appel. D'autres règles portent sur le run entier : « ce modèle doit toujours appeler un outil avant de répondre », « pas plus de 5 appels de modèle », « toute réponse doit citer un montant ». Et il faut savoir ce qui se passe quand une politique est lente ou plante.

#### Objectif

Brancher quatre politiques sur un agent (`loom.require_tool`, une limite d'appels, une consultation externe lente, un contrôle de réponse), observer chacune dans `inspect`, puis provoquer les deux erreurs typiques : un timeout bloquant et une décision interdite à un point.

#### Mise en place

On reste dans `ch10/`. Fichier `ch10/garde_fous.py` :

```python
import asyncio

from loom_ia.policies import (
    CONTINUE,
    BeforeModel,
    BeforeTool,
    Decision,
    OnOutput,
    PolicyContext,
    Retry,
    Stop,
    policy,
)


@policy(points=["on_output"], decisions=["retry"])
def citer_le_montant(subject: OnOutput) -> Decision:
    """Une réponse de prix doit citer un montant en euros."""
    if "€" in subject.output.text:
        return CONTINUE
    return Retry("Ta réponse ne cite aucun montant en euros. Reprends le montant TTC de l'outil.")


@policy(points=["before_model"], decisions=["stop"])
def pas_plus_de_appels(subject: BeforeModel, context: PolicyContext) -> Decision:
    """Coupe les outils au-delà d'un nombre d'appels de modèle."""
    maximum = context.params.get("maximum", 5)
    if isinstance(maximum, int) and subject.state.iterations >= maximum:
        return Stop(f"plus de {maximum} appels de modèle dans ce run")
    return CONTINUE


@policy(points=["before_tool"], decisions=["deny"])
async def annuaire_tva(subject: BeforeTool) -> Decision:
    """Consulte (très lentement) un service externe de taux de TVA."""
    await asyncio.sleep(1)
    return CONTINUE
```

Fichier `ch10/agents/devis_garde.yaml` :

```yaml
name: devis_garde
description: Répond aux questions de prix, avec des garde-fous.

main:
  model: SIMULE_GARDE
  system_file: devis.md

max_iterations: 6

tools:
  - python: prix_ttc

policies:
  - hook: loom.require_tool            # fournie par loom-ia
  - hook: pas_plus_de_appels
    params: {maximum: 5}
  - hook: annuaire_tva
    timeout: 0.2                       # 5 s par défaut
    on_error: allow                    # block par défaut
  - hook: citer_le_montant
```

`loom.require_tool` est fournie par loom : elle place `tool_choice: required` sur l'appel de modèle pour forcer un appel d'outil. `timeout` (5 s par défaut) borne chaque politique ; `on_error` dit quoi faire si elle plante ou dépasse le délai : `block` (défaut, le run échoue) ou `allow` (on continue en journalisant un avertissement).

Nouvelle version de `ch10/loom.yaml`, avec `garde_fous` importé et un modèle `SIMULE_GARDE` :

```yaml
version: 1

imports: [outils, politiques, garde_fous]
agents_dir: agents/
prompts_dir: prompts/

models:
  - id: SIMULE
    sdk: fake
    model: fake-1
    params:
      script:
        # 1er essai : le modèle se trompe de taux
        - text: Je calcule le prix TTC.
          tool_calls:
            - name: prix_ttc
              arguments: {montant_ht: 1250, taux_tva: 15}
        # 2e essai, après le refus de la politique
        - text: Le taux de 15 % n'existe pas, je corrige.
          tool_calls:
            - name: prix_ttc
              arguments: {montant_ht: 1250, taux_tva: 10}
        - text: Pour 1 250 € HT avec une TVA à 10 %, votre client paiera 1 375 € TTC.
  - id: SIMULE_GARDE
    sdk: fake
    model: fake-garde
    params:
      script:
        - tool_calls:
            - name: prix_ttc
              arguments: {montant_ht: 1250, taux_tva: 10}
        # 1re réponse : aucun montant, la politique on_output la refuse
        - text: Voici le prix demandé, TVA comprise.
        - text: Pour 1 250 € HT avec une TVA à 10 %, votre client paiera 1 375 € TTC.
  - id: SIMULE_NUMERO
    sdk: fake
    model: fake-numero
    params:
      script:
        # Le modèle écrit le numéro à sa façon
        - tool_calls:
            - name: chercher_devis
              arguments: {numero: " d-2026-042 "}
        - text: Le devis D-2026-042 de Mme Martin s'élève à 1 840 € TTC.

telemetry:
  logging: {level: WARNING}

storage:
  events: {backend: jsonl, path: data}
```

Les deux variantes suivantes ne sont pas des fichiers à écrire à la main : on les fabrique avec `sed`.

#### Exécution

Run normal :

```console
$ uv run loom run devis_garde "Quel est le prix TTC de 1 250 € HT ?" --run-id garde-1
Pour 1 250 € HT avec une TVA à 10 %, votre client paiera 1 375 € TTC.
2026-10-10 17:51:42 WARNING  loom_ia.engine.hooks — Politique annuaire_tva (before_tool) en erreur : délai de 0.2 s dépassé [run_id=garde-1]

Statut     : completed · itérations : 3 · tokens : 1143/121 · coût : 0.0000 $
Run        : garde-1
$ uv run loom inspect garde-1
Run        : garde-1 (agent devis_garde, client default)
Session    : garde-1
Statut     : completed, 3 itération(s)
Usage      : 1143 → 121 tokens, 0.000000 $, 223 ms de pilotage

run devis_garde — completed, 236 ms, 0.000000 $
  · politique citer_le_montant (on_output) : retry
  étape 1
    · politique loom.require_tool (before_model) : replace
    modèle fake-garde (main) — 2 ms, 256 → 45 tokens, 0.000000 $
      · appelle   : prix_ttc({"montant_ht": 1250, "taux_tva": 10})
  étape 2
    outil prix_ttc — 8 ms
      · politique annuaire_tva (before_tool) : continue
      · arguments : {"montant_ht": 1250, "taux_tva": 10}
      · résultat  : {"montant_ht": 1250.0, "tva": 125.0, "montant_ttc": 1375.0}
  étape 3
    modèle fake-garde (main) — 1 ms, 398 → 34 tokens, 0.000000 $
      · répond    : Voici le prix demandé, TVA comprise.
  étape 4
    modèle fake-garde (main) — 1 ms, 489 → 42 tokens, 0.000000 $
      · répond    : Pour 1 250 € HT avec une TVA à 10 %, votre client paiera 1 375 € TTC.

Réponse finale :
  Pour 1 250 € HT avec une TVA à 10 %, votre client paiera 1 375 € TTC.
Bilan      : 3 appel(s) de modèle, 1 appel(s) d'outil
```

Lecture de l'`inspect` :

- `loom.require_tool (before_model) : replace` : la politique a imposé l'appel d'outil au premier tour ;
- `annuaire_tva (before_tool) : continue` : la politique lente a quand même rendu `continue` ;
- étape 3 : le modèle répond sans montant (« Voici le prix demandé, TVA comprise. »), la politique `citer_le_montant` (`on_output`) renvoie `retry` avec son diagnostic, et le modèle se corrige à l'étape 4 ;
- `inspect` ne le dit pas, mais le run affiche un avertissement : `annuaire_tva` a dépassé son délai de 0,2 s, et `on_error: allow` a laissé passer.

Même agent avec `on_error: block` :

```console
$ sed -e 's/^name: devis_garde/name: devis_strict/' -e 's/on_error: allow.*/on_error: block/' agents/devis_garde.yaml > agents/devis_strict.yaml
$ uv run loom run devis_strict "Quel est le prix TTC de 1 250 € HT ?" --run-id garde-2
politique annuaire_tva en erreur : délai de 0.2 s dépassé
2026-10-10 17:51:43 WARNING  loom_ia.engine.hooks — Politique annuaire_tva (before_tool) en erreur : délai de 0.2 s dépassé [run_id=garde-2]

Statut     : failed · itérations : 1 · tokens : 256/45 · coût : 0.0000 $
Run        : garde-2
Erreur     : politique annuaire_tva en erreur : délai de 0.2 s dépassé (policy.annuaire_tva)
```

Avec `block`, le dépassement du délai fait échouer le run (`Statut : failed`, avec la politique nommée dans l'erreur). Maintenant la limite d'appels, ramenée à 1 :

```console
$ sed -e 's/^name: devis_garde/name: devis_court/' -e 's/maximum: 5/maximum: 1/' agents/devis_garde.yaml > agents/devis_court.yaml
$ uv run loom run devis_court "Quel est le prix TTC de 1 250 € HT ?" --run-id garde-3
Pour 1 250 € HT avec une TVA à 10 %, votre client paiera 1 375 € TTC.
2026-10-10 17:51:44 WARNING  loom_ia.engine.hooks — Politique annuaire_tva (before_tool) en erreur : délai de 0.2 s dépassé [run_id=garde-3]

Statut     : completed · itérations : 3 · tokens : 1276/121 · coût : 0.0000 $
Run        : garde-3
$ uv run loom inspect garde-3
Run        : garde-3 (agent devis_court, client default)
Session    : garde-3
Statut     : completed, 3 itération(s)
Usage      : 1276 → 121 tokens, 0.000000 $, 224 ms de pilotage

run devis_court — completed, 243 ms, 0.000000 $
  · politique citer_le_montant (on_output) : retry
  étape 1
    · politique loom.require_tool (before_model) : replace
    modèle fake-garde (main) — 3 ms, 256 → 45 tokens, 0.000000 $
      · appelle   : prix_ttc({"montant_ht": 1250, "taux_tva": 10})
  étape 2
    outil prix_ttc — 4 ms
      · politique annuaire_tva (before_tool) : continue
      · arguments : {"montant_ht": 1250, "taux_tva": 10}
      · résultat  : {"montant_ht": 1250.0, "tva": 125.0, "montant_ttc": 1375.0}
  étape 3
    · politique pas_plus_de_appels (before_model) : stop
  étape 4
    modèle fake-garde (main) — 1 ms, 464 → 34 tokens, 0.000000 $
      · répond    : Voici le prix demandé, TVA comprise.
  étape 5
    modèle fake-garde (main) — 1 ms, 556 → 42 tokens, 0.000000 $
      · répond    : Pour 1 250 € HT avec une TVA à 10 %, votre client paiera 1 375 € TTC.

Réponse finale :
  Pour 1 250 € HT avec une TVA à 10 %, votre client paiera 1 375 € TTC.
Bilan      : 3 appel(s) de modèle, 1 appel(s) d'outil
```

À l'étape 3, `pas_plus_de_appels` renvoie `stop` : le run cesse d'enchaîner les outils et part vers une réponse finale (étapes 4 et 5, la première étant encore refusée par `citer_le_montant`).

Enfin, une décision interdite à un point est détectée au chargement. Fichier `ch10/ko.py`, une politique qui déclare une décision `pause` sur un point `after_model` :

```python
from loom_ia.policies import AfterModel, Decision, Pause, policy


@policy(points=["after_model"], decisions=["pause"])
def trop_ambitieuse(subject: AfterModel) -> Decision:
    """Demande une pause là où elle n'est pas permise."""
    return Pause("validation demandée")
```

Fichier `ch10/agents/ko.yaml` :

```yaml
name: ko
description: Agent dont la politique déclare une décision interdite à son point.

main:
  model: SIMULE

tools:
  - python: prix_ttc

policies:
  - hook: trop_ambitieuse
```

On l'ajoute aux `imports` d'une copie de la configuration :

```console
$ sed 's/imports: \[outils, politiques, garde_fous\]/imports: [outils, politiques, garde_fous, ko]/' loom.yaml > loom.ko.yaml
$ uv run loom --config loom.ko.yaml validate | grep Configuration
Configuration : Agent 'ko', politique 'trop_ambitieuse' : décision(s) pause non permise(s) au point after_model
```

#### À retenir

- `Retry(feedback)` (la « réparation ») : au point `on_output`, le modèle reçoit le diagnostic et réécrit sa réponse ; `max_attempts` (YAML) borne le nombre de tentatives.
- Le YAML d'une politique accepte `hook` (nom), `name`, `points`, `params`, `timeout` (5 s par défaut), `on_error` (`block` par défaut, `allow` possible) et `max_attempts`.
- `subject.state.iterations` donne le nombre d'appels de modèle déjà faits : c'est de quoi écrire des plafonds sur mesure.
- Piège : un service externe lent dans une politique ralentit **chaque** run. Réglez `timeout` au plus juste et choisissez `allow` ou `block` en connaissance de cause : `allow` privilégie la disponibilité, `block` la sûreté.
- Piège : `Stop` n'est pas un échec. Dans l'essai ci-dessus le run reste `completed`. Si une limite doit faire échouer le run, renvoyez `Fail(...)`.

---

## Chapitre 11 — Contrats de sortie

Un contrat de sortie (`output:`) décrit la forme que doit avoir la réponse finale : un schéma JSON, des motifs interdits ou obligatoires, une longueur maximale. loom le vérifie sans appeler de modèle ; si la réponse n'y est pas conforme, il renvoie le diagnostic à l'auteur de la réponse pour qu'il la réécrive.

Clés d'un contrat : `schema` (ou `schema_file`), `must_match`, `must_not_match`, `max_chars`, `normalize` (vrai par défaut), `repair: {max_attempts, tools}`, `on_failure` (`fail`, `unverified` ou `fallback`) et `fallback_message`. Le contrat se place sur l'agent, sur un rôle (chapitre 12) ou sur un outil Python.

### 11.1 Un e-mail en JSON : normalisation et réparation

#### Pourquoi

L'application de l'artisan a besoin de deux champs distincts, l'objet et le corps de l'e-mail, pour remplir son formulaire. Un modèle qui répond en prose, ou qui enveloppe son JSON dans un bloc de code, casserait l'application. Autre risque : le brouillon « À compléter » qui part tel quel.

#### Objectif

Définir un contrat (objet de 5 caractères au moins, corps de 20 au moins, aucun texte « à compléter »), voir loom nettoyer un JSON enveloppé dans un bloc de code, puis réparer une première version non conforme.

#### Mise en place

```console
$ cd ..
$ mkdir -p ch11/agents ch11/prompts
$ cd ch11
```

Fichier `ch11/prompts/courriel.md` :

```markdown
Tu rédiges l'e-mail de relance d'un devis pour la Plomberie Dupont.
Réponds uniquement par un objet JSON : {"objet": "...", "corps": "..."}.
```

Fichier `ch11/agents/courriel.yaml`. Le schéma JSON décrit la forme ; `must_not_match` est une expression régulière (insensible à la casse grâce à `(?i)`) ; `repair.max_attempts: 1` autorise une seule réécriture.

```yaml
name: courriel
description: Rédige l'e-mail de relance d'un devis, au format JSON.

main:
  model: SIMULE
  system_file: courriel.md

max_iterations: 3

output:
  schema:
    type: object
    properties:
      objet: {type: string, minLength: 5}
      corps: {type: string, minLength: 20}
    required: [objet, corps]
    additionalProperties: false
  must_not_match: "(?i)à compléter|xxx"   # expression régulière
  max_chars: 600
  repair: {max_attempts: 1}
  on_failure: fail
```

Fichier `ch11/loom.yaml`. Trois cas, choisis par un mot-clé dans la demande : **A** (JSON correct mais dans un bloc de code), **B** (première version non conforme, puis conforme), **C** (le modèle n'y arrive pas, utilisé en 11.2).

```yaml
version: 1

agents_dir: agents/
prompts_dir: prompts/

models:
  - id: SIMULE
    sdk: fake
    model: fake-1
    params:
      script:
        # Cas A : JSON correct, mais enveloppé dans un bloc de code
        - with_text: cas A
          text: |
            ```json
            {"objet": "Votre devis D-2026-042", "corps": "Bonjour Mme Martin, votre devis D-2026-042 de 1 840 € est toujours valable."}
            ```
        # Cas B : 1re version non conforme, 2e conforme après réparation
        - with_text: cas B
          text: '{"objet": "Devis", "corps": "À compléter"}'
        - with_text: cas B
          text: '{"objet": "Votre devis D-2026-042", "corps": "Bonjour Mme Martin, votre devis D-2026-042 de 1 840 € est toujours valable."}'
        # Cas C : le modèle n'y arrive pas
        - with_text: cas C
          text: '{"objet": "Devis", "corps": "À compléter"}'
        - with_text: cas C
          text: Je n'ai pas pu rédiger l'e-mail.

telemetry:
  logging: {level: WARNING}

storage:
  events: {backend: jsonl, path: data}
```

#### Exécution

Cas A, un JSON enveloppé dans un bloc de code Markdown :

```console
$ uv run loom run courriel "cas A : relance Mme Martin" --run-id mail-a
{"objet": "Votre devis D-2026-042", "corps": "Bonjour Mme Martin, votre devis D-2026-042 de 1 840 € est toujours valable."}

Statut     : completed · itérations : 1 · tokens : 287/62 · coût : 0.0000 $
Run        : mail-a
```

La réponse a été nettoyée : le bloc de code a disparu, sans aucun appel de modèle supplémentaire. Avec `--json`, on récupère de plus l'objet validé dans le champ `data` :

```console
$ uv run loom run courriel "cas A : relance Mme Martin" --run-id mail-a2 --json
…
  "data": {
    "objet": "Votre devis D-2026-042",
    "corps": "Bonjour Mme Martin, votre devis D-2026-042 de 1 840 € est toujours valable."
  },
  "unverified": false,
…
```

Cas B, une première version qui a bien les deux champs, mais un corps trop court (« À compléter », 11 caractères pour 20 au minimum) et un texte interdit :

```console
$ uv run loom run courriel "cas B : relance Mme Martin" --run-id mail-b
{"objet": "Votre devis D-2026-042", "corps": "Bonjour Mme Martin, votre devis D-2026-042 de 1 840 € est toujours valable."}

Statut     : completed · itérations : 2 · tokens : 751/95 · coût : 0.0000 $
Run        : mail-b
$ uv run loom inspect mail-b
Run        : mail-b (agent courriel, client default)
Session    : mail-b
Statut     : completed, 2 itération(s)
Usage      : 751 → 95 tokens, 0.000000 $, 7 ms de pilotage

run courriel — completed, 19 ms, 0.000000 $
  · contrôle contract : failed → retry
  · politique loom.contract (on_output) : retry
  · contrôle contract : passed
  étape 1
    modèle fake-1 (main) — 1 ms, 287 → 37 tokens, 0.000000 $
      · répond    : {"objet": "Devis", "corps": "À compléter"}
  étape 2
    modèle fake-1 (main) — 0 ms, 464 → 58 tokens, 0.000000 $
      · répond    : {"objet": "Votre devis D-2026-042", "corps": "Bonjour Mme Martin, votre devis D-2026-042 de 1 840…

Réponse finale :
  {"objet": "Votre devis D-2026-042", "corps": "Bonjour Mme Martin, votre devis D-2026-042 de 1 840 € est toujours valable."}
Bilan      : 2 appel(s) de modèle, 0 appel(s) d'outil
```

Dans `inspect` : le contrôle `contract` échoue, la politique interne `loom.contract` renvoie `retry`, le modèle réécrit, et le second contrôle passe. Le texte final est la seconde version.

#### À retenir

- Le contrat est appliqué par la politique interne `loom.contract` (point `on_output`) : on retrouve donc son effet dans `inspect` comme n'importe quelle politique du chapitre 10.
- `normalize` (vrai par défaut) retire une clôture de bloc de code (```json) et extrait le JSON de la réponse, sans appel de modèle.
- La réparation envoie le diagnostic à l'auteur de la réponse : le modèle principal pour la réponse finale d'un agent, le modèle propre du rôle pour un rôle (chapitre 12). Un **outil Python** n'est jamais réparé.
- Piège : le schéma `additionalProperties: false` est strict, un champ en trop (par exemple `"salutations"`) provoque un échec. Décidez si vous voulez la rigueur ou la tolérance.
- Piège : le contrat ne dit rien du **fond**. Un e-mail peut respecter le schéma et citer un montant faux. C'est le rôle du juge (chapitre 14).

### 11.2 Quand le modèle n'y arrive pas : `fail`, `unverified`, `fallback`

#### Pourquoi

Même avec une réparation, un modèle peut échouer. Que faire ? Selon le contexte, la bonne réponse n'est pas la même : arrêter net (un système qui automatise l'envoi), garder la réponse en la marquant comme non vérifiée (un brouillon qu'un humain relira), ou la remplacer par un message de repli.

#### Objectif

Comparer les trois valeurs de `on_failure` sur le même cas d'échec (cas C), puis lire le résultat en Python (`data`, `unverified`).

#### Mise en place

On reste dans `ch11/`. Deux variantes de l'agent, où seul `on_failure` change. Fichier `ch11/agents/courriel_souple.yaml` :

```yaml
name: courriel_souple
description: Rédige l'e-mail de relance d'un devis, au format JSON.

main:
  model: SIMULE
  system_file: courriel.md

max_iterations: 3

output:
  schema:
    type: object
    properties:
      objet: {type: string, minLength: 5}
      corps: {type: string, minLength: 20}
    required: [objet, corps]
    additionalProperties: false
  must_not_match: "(?i)à compléter|xxx"   # expression régulière
  max_chars: 600
  repair: {max_attempts: 1}
  on_failure: unverified
```

Fichier `ch11/agents/courriel_repli.yaml` :

```yaml
name: courriel_repli
description: Rédige l'e-mail de relance d'un devis, au format JSON.

main:
  model: SIMULE
  system_file: courriel.md

max_iterations: 3

output:
  schema:
    type: object
    properties:
      objet: {type: string, minLength: 5}
      corps: {type: string, minLength: 20}
    required: [objet, corps]
    additionalProperties: false
  must_not_match: "(?i)à compléter|xxx"   # expression régulière
  max_chars: 600
  repair: {max_attempts: 1}
  on_failure: fallback
  fallback_message: "Relance à rédiger à la main : le brouillon automatique a échoué."
```

Fichier `ch11/courriel.py` :

```python
import asyncio

from loom_ia.access import Loom


async def main() -> None:
    async with Loom.from_config("loom.yaml") as loom:
        resultat = await loom.run("courriel", "cas B : relance Mme Martin")
        print(resultat.status, "| vérifiée :", not resultat.unverified)
        print(type(resultat.data).__name__, "->", resultat.data["objet"])

        souple = await loom.run("courriel_souple", "cas C : relance Mme Martin")
        print(souple.status, "| vérifiée :", not souple.unverified, "| data :", souple.data)

        echec = await loom.run("courriel", "cas C : relance Mme Martin")
        print(echec.status, echec.error_type)


asyncio.run(main())
```

#### Exécution

Dans le cas C, le modèle répond d'abord par un JSON « À compléter », puis, après réparation, par une phrase qui n'est même plus du JSON. Avec `on_failure: fail` (agent `courriel`) :

```console
$ uv run loom run courriel "cas C : relance Mme Martin" --run-id mail-c; echo "code de sortie : $?"
réponse finale non conforme à son contrat : ce n'est pas un JSON valide (Expecting value, ligne 1, colonne 1)

Statut     : failed · itérations : 2 · tokens : 751/70 · coût : 0.0000 $
Run        : mail-c
Erreur     : réponse finale non conforme à son contrat : ce n'est pas un JSON valide (Expecting value, ligne 1, colonne 1) (guard.contract)
code de sortie : 1
```

Le run est `failed` (code de sortie 1) et l'erreur nomme le contrat (`guard.contract`). Avec `unverified`, la réponse est gardée et marquée :

```console
$ uv run loom run courriel_souple "cas C : relance Mme Martin" --run-id mail-c2
Je n'ai pas pu rédiger l'e-mail.

Statut     : completed · itérations : 2 · tokens : 751/70 · coût : 0.0000 $
Run        : mail-c2
Vérifiée   : non (gardée malgré son contrat ou son juge)
```

Avec `fallback`, c'est le message de repli qui est rendu :

```console
$ uv run loom run courriel_repli "cas C : relance Mme Martin" --run-id mail-c3
Relance à rédiger à la main : le brouillon automatique a échoué.

Statut     : completed · itérations : 2 · tokens : 751/70 · coût : 0.0000 $
Run        : mail-c3
```

Et en Python :

```console
$ uv run python courriel.py
completed | vérifiée : True
dict -> Votre devis D-2026-042
completed | vérifiée : False | data : None
failed guard.contract
```

#### À retenir

- `fail` : le run échoue, rien d'invalide ne sort. `unverified` : la réponse est gardée, `RunResult.unverified` vaut `True` et la CLI imprime « Vérifiée : non ». `fallback` : la réponse est remplacée par `fallback_message`.
- `RunResult.data` contient l'objet validé quand le contrat a un schéma, et `None` sinon. En `--json`, la clé `data` est omise si elle est vide.
- Piège : `unverified` est trompeur à l'usage. La CLI affiche le texte comme une réponse normale ; seul le champ `unverified` (ou la ligne « Vérifiée : non ») le distingue. Si vous faites suivre la réponse à un autre système, testez-le.
- Choix par défaut raisonnable : `fail` pour ce qui déclenche une action, `unverified` pour ce qu'un humain relit, `fallback` pour une interface qui doit toujours afficher quelque chose.

---

## Chapitre 12 — Rôles délégués

Un **rôle** est un sous-agent appelé par l'agent principal comme un outil. Il a son propre modèle, son propre prompt et, surtout, **son propre contexte** : il ne voit pas toute la conversation, seulement ce que vous avez choisi de lui transmettre. Deux bénéfices : un modèle moins cher ou plus adapté pour chaque tâche, et moins de tokens (donc moins de coût) à chaque appel.

Un rôle se déclare sous `roles:` dans l'agent :

| Clé | Rôle |
|---|---|
| `name`, `description` | le nom sous lequel l'orchestrateur l'appelle, et à quoi il sert |
| `model`, `fallbacks` | son modèle (et ses modèles de secours) |
| `system` / `system_file` | son prompt |
| `input_schema` | les arguments que l'orchestrateur doit fournir (schéma JSON) |
| `context` | ce que le rôle reçoit en plus : `user_input`, `attachments`, `tool_results`, `session_summary`, `last_turns`, `caller_context` |
| `input_template` | le message envoyé au rôle (gabarit `{{ }}`) |
| `terminal` | si vrai, la réponse du rôle est la réponse finale du run |
| `output`, `judge`, `timeout` | contrat (chapitre 11), juge (chapitre 14), délai |

### 12.1 Déléguer la rédaction d'une relance

#### Pourquoi

Le flux est toujours le même : retrouver le devis, rédiger la relance. Rédiger est un travail de style, qu'un modèle économique fait très bien ; l'orchestrateur, lui, n'a pas à relire toute la conversation à chaque fois. Et surtout, on veut que le rédacteur n'ait **que** les faits du devis, pour qu'il n'invente rien.

#### Objectif

Un orchestrateur appelle l'outil `chercher_devis`, puis délègue au rôle `rediger_relance`, qui reçoit exactement : la demande de l'artisan, le résultat de l'outil et le ton souhaité. Vérifier ensuite ce que le rôle a reçu et ce que cela a coûté.

#### Mise en place

```console
$ cd ..
$ mkdir -p ch12/agents ch12/prompts
$ cd ch12
```

Fichier `ch12/outils.py` :

```python
from loom_ia.core.ports import ToolError
from loom_ia.tools import tool

DEVIS = {
    "D-2026-042": {
        "numero": "D-2026-042",
        "client": "Mme Martin",
        "email": "mme.martin@example.fr",
        "objet": "Remplacement du chauffe-eau",
        "montant_ttc": 1840.0,
        "envoye_le": "2026-09-03",
        "valable_jusqu_au": "2026-10-31",
    },
}


@tool
def chercher_devis(numero: str) -> dict:
    """Retourne le devis d'un client à partir de son numéro (ex. D-2026-042)."""
    if numero not in DEVIS:
        raise ToolError(f"Devis {numero} introuvable.")
    return DEVIS[numero]
```

Fichier `ch12/prompts/relance.md`, le prompt de l'orchestrateur :

```markdown
Tu es l'assistant de la Plomberie Dupont. Pour relancer un client :
1. cherche le devis avec `chercher_devis` ;
2. confie la rédaction au rôle `rediger_relance`.
```

Fichier `ch12/prompts/redacteur.md`, celui du rôle :

```markdown
Tu rédiges des e-mails de relance courts et polis pour un artisan.
N'utilise que les informations qu'on te donne : n'invente aucun montant, date ou délai.
```

Fichier `ch12/agents/relance.yaml`. `context` liste ce que le rôle reçoit : la demande initiale et le résultat de l'outil `chercher_devis`. Chaque élément du contexte doit être utilisé dans `input_template` (un contexte déclaré mais inutilisé est refusé au chargement, sauf `attachments`). `terminal: true` fait de la sortie du rôle la réponse finale.

```yaml
name: relance
description: Prépare la relance d'un devis resté sans réponse.

main:
  model: ORCHESTRATEUR
  system_file: relance.md

max_iterations: 6

tools:
  - python: chercher_devis

roles:
  - name: rediger_relance
    description: Rédige l'e-mail de relance d'un devis.
    model: REDACTEUR
    system_file: redacteur.md
    input_schema:
      type: object
      properties:
        ton: {type: string, description: "cordial, ferme, bref…"}
      required: [ton]
    context:
      - user_input
      - tool_results: [chercher_devis]
    input_template: |-
      Demande de l'artisan : {{ context.user_input }}
      Devis : {{ context.tool_results.chercher_devis }}
      Ton : {{ args.ton }}
    terminal: true
```

Fichier `ch12/loom.yaml`. L'orchestrateur appelle l'outil puis le rôle ; le rédacteur répond. Le script de l'orchestrateur est écrit pour cet exemple ; avec un vrai modèle, c'est lui qui décide, d'après le prompt. `raw_exchanges` permet de relire les messages échangés.

```yaml
version: 1

imports: [outils]
agents_dir: agents/
prompts_dir: prompts/

models:
  - id: ORCHESTRATEUR
    sdk: fake
    model: fake-orchestrateur
    params:
      script:
        - text: Je cherche le devis.
          tool_calls:
            - name: chercher_devis
              arguments: {numero: D-2026-042}
        - text: Je confie la rédaction au rédacteur.
          tool_calls:
            - name: rediger_relance
              arguments: {ton: cordial}

  - id: REDACTEUR
    sdk: fake
    model: fake-redacteur
    params:
      script:
        - text: |-
            Objet : Votre devis D-2026-042

            Bonjour Mme Martin,

            Je me permets de revenir vers vous au sujet du devis D-2026-042
            (remplacement du chauffe-eau, 1 840 € TTC), envoyé le 3 septembre
            et valable jusqu'au 31 octobre. Je reste à votre disposition.

            Cordialement,
            La Plomberie Dupont

telemetry:
  logging: {level: WARNING}
  capture: {raw_exchanges: true}

storage:
  events: {backend: jsonl, path: data}
```

Fichier `ch12/message_du_role.py`, qui relit dans le journal le prompt et le message reçus par le rédacteur :

```python
import asyncio
import json
import sys

from loom_ia.access import Loom


async def main(run_id: str) -> None:
    async with Loom.from_config("loom.yaml") as loom:
        for evenement in await loom.export_session(run_id):
            if evenement.type != "model.exchanged":
                continue
            echange = evenement.payload
            if echange.url != "fake://fake-redacteur":
                continue
            requete = json.loads(echange.request_body)
            print("--- prompt système du rôle")
            print(requete["system"])
            print("--- message reçu par le rôle")
            print(requete["messages"][0]["blocks"][0]["text"])


asyncio.run(main(sys.argv[1]))
```

#### Exécution

```console
$ uv run loom validate | tail -5
…
  relance : modèle ORCHESTRATEUR, 1 outil(s) Python, rôle rediger_relance (REDACTEUR)

1 agent(s) monté(s) sans erreur.
$ uv run loom run relance "Relance Mme Martin pour le devis D-2026-042." --run-id rel-1
Objet : Votre devis D-2026-042

Bonjour Mme Martin,

Je me permets de revenir vers vous au sujet du devis D-2026-042
(remplacement du chauffe-eau, 1 840 € TTC), envoyé le 3 septembre
et valable jusqu'au 31 octobre. Je reste à votre disposition.

Cordialement,
La Plomberie Dupont

Statut     : completed · itérations : 2 · tokens : 1313/232 · coût : 0.0000 $
Run        : rel-1
```

Le rôle a rendu l'e-mail, et comme il est `terminal`, c'est directement la réponse finale. Dans `inspect`, un rôle apparaît comme un appel d'outil, avec ses propres appels de modèle imbriqués :

```console
$ uv run loom inspect rel-1
Run        : rel-1 (agent relance, client default)
Session    : rel-1
Statut     : completed, 2 itération(s)
Usage      : 1313 → 232 tokens, 0.000000 $, 26 ms de pilotage

run relance — completed, 39 ms, 0.000000 $
  étape 1
    modèle fake-orchestrateur (main) — 2 ms, 402 → 66 tokens, 0.000000 $
      · répond    : Je cherche le devis.
      · appelle   : chercher_devis({"numero": "D-2026-042"})
  étape 2
    outil chercher_devis — 2 ms
      · arguments : {"numero": "D-2026-042"}
      · résultat  : {"numero": "D-2026-042", "client": "Mme Martin", "email": "mme.martin@example.fr", "objet": "Remp…
  étape 3
    modèle fake-orchestrateur (main) — 3 ms, 655 → 69 tokens, 0.000000 $
      · répond    : Je confie la rédaction au rédacteur.
      · appelle   : rediger_relance({"ton": "cordial"})
  étape 4
    rôle rediger_relance — 5 ms
      · arguments : {"ton": "cordial"}
      · résultat  : Objet : Votre devis D-2026-042 Bonjour Mme Martin, Je me permets de revenir vers vous au sujet du…
      appels du rôle rediger_relance
        modèle fake-redacteur (rediger_relance) — 1 ms, 256 → 97 tokens, 0.000000 $
          · répond    : Objet : Votre devis D-2026-042 Bonjour Mme Martin, Je me permets de revenir vers vous au sujet du…

Réponse finale :
  Objet : Votre devis D-2026-042
  
  Bonjour Mme Martin,
  
  Je me permets de revenir vers vous au sujet du devis D-2026-042
  (remplacement du chauffe-eau, 1 840 € TTC), envoyé le 3 septembre
  et valable jusqu'au 31 octobre. Je reste à votre disposition.
  
  Cordialement,
  La Plomberie Dupont
Bilan      : 3 appel(s) de modèle, 2 appel(s) d'outil
```

Voici ce que le rôle a **réellement** reçu :

```console
$ uv run python message_du_role.py rel-1
--- prompt système du rôle
Tu rédiges des e-mails de relance courts et polis pour un artisan.
N'utilise que les informations qu'on te donne : n'invente aucun montant, date ou délai.

--- message reçu par le rôle
Demande de l'artisan : Relance Mme Martin pour le devis D-2026-042.
Devis : {"numero": "D-2026-042", "client": "Mme Martin", "email": "mme.martin@example.fr", "objet": "Remplacement du chauffe-eau", "montant_ttc": 1840.0, "envoye_le": "2026-09-03", "valable_jusqu_au": "2026-10-31"}
Ton : cordial
```

Le rédacteur n'a eu ni l'historique, ni les outils de l'orchestrateur : la demande, le devis et le ton, rien d'autre. D'où le coût :

```console
$ uv run loom report rel-1
Consommation — run rel-1
  Total                3 appels ·   1313/232 tokens · 0,00000 $
  Par rôle :
    main                 2 appels ·   1057/135 tokens · 0,00000 $
    rediger_relance       1 appel ·     256/97 tokens · 0,00000 $
  Par modèle :
    fake-orchestrateur   2 appels ·   1057/135 tokens · 0,00000 $
    fake-redacteur        1 appel ·     256/97 tokens · 0,00000 $
```

Le rôle `rediger_relance` a consommé 256 tokens d'entrée, contre 655 pour le deuxième appel de l'orchestrateur, qui porte tout le contexte.

#### À retenir

- `loom report <run_id>` ventile les tokens et le coût par rôle et par modèle. C'est l'outil pour décider où un modèle moins cher suffit.
- `{{ args.ton }}` lit un argument fourni par l'orchestrateur (déclaré dans `input_schema`) ; `{{ context.user_input }}` et `{{ context.tool_results.chercher_devis }}` lisent le contexte déclaré.
- Sans `input_template`, le message du rôle est assemblé automatiquement avec des blocs balisés (`<caller_context>`, `<last_turns>`, `<arguments>`). Un gabarit explicite est plus prévisible.
- Piège : le rôle ne voit **que** son contexte. Si le résultat d'un outil n'est pas dans `context.tool_results`, il l'ignore, et il peut alors inventer. Écrivez un prompt qui lui interdit d'inventer (comme ci-dessus), et mettez un juge (chapitre 14).
- Limite connue : `ToolError` s'importe de `loom_ia.core.ports`, pas de `loom_ia.tools` (l'essai `from loom_ia.tools import ToolError` échoue). Le README mentionne `ToolError` sans dire d'où l'importer.

### 12.2 Passer un résultat d'outil par référence (`$ref`)

#### Pourquoi

Dans 12.1, le rôle reçoit le devis via `context`. Mais parfois l'orchestrateur doit choisir lui-même quoi transmettre. S'il recopie un gros résultat dans les arguments du rôle, il dépense des tokens de sortie (les plus chers) et peut se tromper en recopiant. Une **référence** évite la recopie.

#### Objectif

Faire passer le devis au rôle par `{"$ref": "result:1"}`, sans que le modèle le recopie, et comprendre la règle de typage qui l'accompagne.

#### Mise en place

On reste dans `ch12/`. Fichier `ch12/agents/relance_ref.yaml`. Le rôle n'est plus `terminal`, n'a plus que `user_input` en contexte, et reçoit le devis dans un argument `devis` de type **objet** :

```yaml
name: relance_ref
description: Prépare la relance d'un devis, en passant le devis au rôle par référence.

main:
  model: ORCHESTRATEUR_REF
  system_file: relance.md

max_iterations: 6

tools:
  - python: chercher_devis

roles:
  - name: rediger_relance
    description: Rédige l'e-mail de relance d'un devis.
    model: REDACTEUR
    system_file: redacteur.md
    input_schema:
      type: object
      properties:
        ton: {type: string, description: "cordial, ferme, bref…"}
        devis: {type: object, description: "Le devis tel que rendu par chercher_devis."}
      required: [ton, devis]
    context: [user_input]
    input_template: |-
      Demande de l'artisan : {{ context.user_input }}
      Devis : {{ args.devis }}
      Ton : {{ args.ton }}
```

Fichier `ch12/loom.yaml` mis à jour, avec le modèle `ORCHESTRATEUR_REF` :

```yaml
version: 1

imports: [outils]
agents_dir: agents/
prompts_dir: prompts/

models:
  - id: ORCHESTRATEUR
    sdk: fake
    model: fake-orchestrateur
    params:
      script:
        - text: Je cherche le devis.
          tool_calls:
            - name: chercher_devis
              arguments: {numero: D-2026-042}
        - text: Je confie la rédaction au rédacteur.
          tool_calls:
            - name: rediger_relance
              arguments: {ton: cordial}

  - id: ORCHESTRATEUR_REF
    sdk: fake
    model: fake-orchestrateur-ref
    params:
      script:
        - text: Je cherche le devis.
          tool_calls:
            - name: chercher_devis
              arguments: {numero: D-2026-042}
        - text: Je transmets le devis au rédacteur, sans le recopier.
          tool_calls:
            - name: rediger_relance
              arguments:
                ton: cordial
                devis: {"$ref": "result:1"}
        - text: L'e-mail de relance est prêt, je vous le présente ci-dessous.

  - id: REDACTEUR
    sdk: fake
    model: fake-redacteur
    params:
      script:
        - text: |-
            Objet : Votre devis D-2026-042

            Bonjour Mme Martin,

            Je me permets de revenir vers vous au sujet du devis D-2026-042
            (remplacement du chauffe-eau, 1 840 € TTC), envoyé le 3 septembre
            et valable jusqu'au 31 octobre. Je reste à votre disposition.

            Cordialement,
            La Plomberie Dupont

telemetry:
  logging: {level: WARNING}
  capture: {raw_exchanges: true}

storage:
  events: {backend: jsonl, path: data}
```

#### Exécution

```console
$ uv run loom run relance_ref "Relance Mme Martin pour le devis D-2026-042." --run-id rel-2
L'e-mail de relance est prêt, je vous le présente ci-dessous.

Statut     : completed · itérations : 3 · tokens : 2291/283 · coût : 0.0000 $
Run        : rel-2
```

Dans le script, l'orchestrateur passe `devis: {"$ref": "result:1"}`. `result:1` désigne le résultat du premier appel d'outil du run (ici `chercher_devis`). loom remplace la référence par la valeur avant d'appeler le rôle. Le message reçu par le rôle le prouve :

```console
$ uv run python message_du_role.py rel-2
--- prompt système du rôle
Tu rédiges des e-mails de relance courts et polis pour un artisan.
N'utilise que les informations qu'on te donne : n'invente aucun montant, date ou délai.

--- message reçu par le rôle
Demande de l'artisan : Relance Mme Martin pour le devis D-2026-042.
Devis : {"numero": "D-2026-042", "client": "Mme Martin", "email": "mme.martin@example.fr", "objet": "Remplacement du chauffe-eau", "montant_ttc": 1840.0, "envoye_le": "2026-09-03", "valable_jusqu_au": "2026-10-31"}
Ton : cordial
```

Le devis complet est arrivé, sans que le modèle l'ait recopié. Le rôle n'étant plus `terminal`, l'orchestrateur fait un dernier appel pour présenter le résultat (d'où un total de 4 appels au lieu de 3) :

```console
$ uv run loom report rel-2
Consommation — run rel-2
  Total                    4 appels ·   2291/283 tokens · 0,00000 $
  Par rôle :
    main                     3 appels ·   2035/186 tokens · 0,00000 $
    rediger_relance           1 appel ·     256/97 tokens · 0,00000 $
  Par modèle :
    fake-orchestrateur-ref   3 appels ·   2035/186 tokens · 0,00000 $
    fake-redacteur            1 appel ·     256/97 tokens · 0,00000 $
```

Qu'arrive-t-il si le champ `devis` est déclaré `type: string` dans `input_schema` ? Même fichier `relance_ref.yaml`, où seule la ligne `devis: {type: string, …}` change, lancé avec le même script :

```console
$ uv run loom run relance_ref "Relance Mme Martin pour le devis D-2026-042." --run-id rel-3
L'e-mail de relance est prêt, je vous le présente ci-dessous.

Statut     : completed · itérations : 3 · tokens : 2005/186 · coût : 0.0000 $
Run        : rel-3
```

Le run se termine « normalement », mais l'appel du rôle a été refusé. L'`inspect` détaillé montre pourquoi :

```console
$ uv run loom inspect rel-3 --full | grep -A3 "refusé avant exécution" | head -4
    appel rediger_relance — refusé avant exécution
      · résultat  : (erreur) Arguments non conformes au schéma de l'outil :
                    - devis : la référence result:1 (chercher_devis) transmet un objet JSON ; ce champ attend du texte. Une référence passe le résultat tel quel : si le champ attend autre chose, écris la valeur toi-même.
  étape 5
```

#### À retenir

- `{"$ref": "result:N"}` désigne le résultat du N-ième appel d'outil du run. loom ajoute lui-même au prompt de l'orchestrateur une consigne qui explique les références.
- Écart doc/réel : la référence transmet le résultat structuré de l'outil (un **objet JSON**), pas un texte. Le champ cible du rôle doit donc être `type: object`. Avec `type: string`, l'appel du rôle est refusé : « la référence result:1 (chercher_devis) transmet un objet JSON ; ce champ attend du texte ». Le README ne mentionne pas cette règle.
- Piège : le run ne **plante pas** dans ce cas ; l'erreur revient au modèle comme résultat d'outil. Dans un test, lisez `inspect` ou vérifiez que le rôle a bien été appelé.
- Une référence sert pour des données volumineuses ou exactes (devis, tableaux) ; pour un simple texte court, `context.tool_results` (12.1) est plus simple.

### 12.3 Un rôle « vision » qui n'existe que s'il y a une photo

#### Pourquoi

Un client envoie la photo de son chauffe-eau. Il faut un modèle capable de lire les images ; mais seulement quand il y a une image, et il serait absurde de proposer ce rôle à l'orchestrateur quand le client n'a rien joint.

#### Objectif

Déclarer un rôle à contexte `attachments` sur un modèle `vision`, joindre une image avec `--attach`, et constater que le rôle disparaît quand il n'y a pas de pièce jointe.

#### Mise en place

On reste dans `ch12/`. Fichier `ch12/photo.py`, qui fabrique une petite image PNG (pas besoin d'outil externe) :

```python
"""Fabrique une petite image PNG grise (16 x 16) pour l'exemple."""
import struct
import zlib

largeur = hauteur = 16
lignes = b"".join(b"\x00" + bytes([200, 200, 210]) * largeur for _ in range(hauteur))


def bloc(type_: bytes, donnees: bytes) -> bytes:
    contenu = type_ + donnees
    return struct.pack(">I", len(donnees)) + contenu + struct.pack(">I", zlib.crc32(contenu) & 0xFFFFFFFF)


png = (
    b"\x89PNG\r\n\x1a\n"
    + bloc(b"IHDR", struct.pack(">IIBBBBB", largeur, hauteur, 8, 2, 0, 0, 0))
    + bloc(b"IDAT", zlib.compress(lignes))
    + bloc(b"IEND", b"")
)
open("chauffe-eau.png", "wb").write(png)
```

Fichier `ch12/agents/releve.yaml` :

```yaml
name: releve
description: Décrit la photo d'une installation envoyée par un client.

main:
  model: ORCH_PHOTO
  system: |-
    Tu es l'assistant de la Plomberie Dupont. Si le client joint une photo,
    demande sa description au rôle `decrire_photo`, puis résume en une phrase.

max_iterations: 4

roles:
  - name: decrire_photo
    description: Décrit ce que montre la photo jointe à la demande.
    model: VISION
    system: Tu décris précisément des installations de plomberie à partir de photos.
    input_schema:
      type: object
      properties:
        consigne: {type: string, description: "Ce qu'il faut regarder sur la photo."}
      required: [consigne]
    context: [attachments]
```

Fichier `ch12/loom.yaml` (version finale). Le modèle `VISION` déclare `capabilities: {vision: true}`. Dans le script de l'orchestrateur, `with_tool: decrire_photo` / `without_tool: decrire_photo` simulent ce que ferait un vrai modèle : ne proposer l'appel au rôle que lorsqu'il est disponible.

```yaml
version: 1

imports: [outils]
agents_dir: agents/
prompts_dir: prompts/

models:
  - id: ORCHESTRATEUR
    sdk: fake
    model: fake-orchestrateur
    params:
      script:
        - text: Je cherche le devis.
          tool_calls:
            - name: chercher_devis
              arguments: {numero: D-2026-042}
        - text: Je confie la rédaction au rédacteur.
          tool_calls:
            - name: rediger_relance
              arguments: {ton: cordial}

  - id: ORCHESTRATEUR_REF
    sdk: fake
    model: fake-orchestrateur-ref
    params:
      script:
        - text: Je cherche le devis.
          tool_calls:
            - name: chercher_devis
              arguments: {numero: D-2026-042}
        - text: Je transmets le devis au rédacteur, sans le recopier.
          tool_calls:
            - name: rediger_relance
              arguments:
                ton: cordial
                devis: {"$ref": "result:1"}
        - text: L'e-mail de relance est prêt, je vous le présente ci-dessous.

  - id: ORCH_PHOTO
    sdk: fake
    model: fake-orch-photo
    params:
      script:
        # Avec une photo, le rôle vision est proposé
        - with_tool: decrire_photo
          text: Je regarde la photo.
          tool_calls:
            - name: decrire_photo
              arguments: {consigne: "Décris l'installation et son état."}
        - with_tool: decrire_photo
          text: Chauffe-eau mural d'environ 10 ans, traces de corrosion au raccord.
        # Sans photo, le rôle est masqué
        - without_tool: decrire_photo
          text: Je n'ai pas de photo à regarder. Pouvez-vous en joindre une ?

  - id: VISION
    sdk: fake
    model: fake-vision
    capabilities: {vision: true}
    params:
      script:
        - text: Chauffe-eau mural gris, environ 10 ans, traces de corrosion au niveau du raccord d'arrivée d'eau froide.

  - id: REDACTEUR
    sdk: fake
    model: fake-redacteur
    params:
      script:
        - text: |-
            Objet : Votre devis D-2026-042

            Bonjour Mme Martin,

            Je me permets de revenir vers vous au sujet du devis D-2026-042
            (remplacement du chauffe-eau, 1 840 € TTC), envoyé le 3 septembre
            et valable jusqu'au 31 octobre. Je reste à votre disposition.

            Cordialement,
            La Plomberie Dupont

telemetry:
  logging: {level: WARNING}
  capture: {raw_exchanges: true}

storage:
  events: {backend: jsonl, path: data}
```

#### Exécution

```console
$ uv run python photo.py && ls -l chauffe-eau.png
-rw-r--r-- 1 root root 79 Oct 10 17:42 chauffe-eau.png
```

Avec la photo :

```console
$ uv run loom run releve "Voici le chauffe-eau du client, que voyez-vous ?" --attach chauffe-eau.png --run-id photo-1
Chauffe-eau mural d'environ 10 ans, traces de corrosion au raccord.

Statut     : completed · itérations : 2 · tokens : 1180/166 · coût : 0.0000 $
Run        : photo-1
$ uv run loom inspect photo-1
Run        : photo-1 (agent releve, client default)
Session    : photo-1
Statut     : completed, 2 itération(s)
Usage      : 1180 → 166 tokens, 0.000000 $, 15 ms de pilotage

run releve — completed, 24 ms, 0.000000 $
  · fichier rangé : artifact://default/photo-1/b798aad6ec0e1881cb4b8c27bff75d185dc26f9d7d7f1442ce3f07dfdcab0ccf.png
  étape 1
    modèle fake-orch-photo (main) — 2 ms, 392 → 73 tokens, 0.000000 $
      · répond    : Je regarde la photo.
      · appelle   : decrire_photo({"consigne": "Décris l'installation et son état."})
  étape 2
    rôle decrire_photo — 5 ms
      · arguments : {"consigne": "Décris l'installation et son état."}
      · résultat  : Chauffe-eau mural gris, environ 10 ans, traces de corrosion au niveau du raccord d'arrivée d'eau …
      appels du rôle decrire_photo
        modèle fake-vision (decrire_photo) — 1 ms, 205 → 51 tokens, 0.000000 $
          · répond    : Chauffe-eau mural gris, environ 10 ans, traces de corrosion au niveau du raccord d'arrivée d'eau …
  étape 3
    modèle fake-orch-photo (main) — 2 ms, 583 → 42 tokens, 0.000000 $
      · répond    : Chauffe-eau mural d'environ 10 ans, traces de corrosion au raccord.

Réponse finale :
  Chauffe-eau mural d'environ 10 ans, traces de corrosion au raccord.
Bilan      : 3 appel(s) de modèle, 1 appel(s) d'outil
```

L'image est rangée dans les artefacts de la session (`artifact://default/photo-1/…`), et le rôle `decrire_photo` en reçoit le contenu. Sans photo :

```console
$ uv run loom run releve "Mon chauffe-eau fuit, que voyez-vous ?" --run-id photo-2
Je n'ai pas de photo à regarder. Pouvez-vous en joindre une ?

Statut     : completed · itérations : 1 · tokens : 200/40 · coût : 0.0000 $
Run        : photo-2
```

L'orchestrateur répond qu'il n'a pas de photo : le rôle n'était pas dans ses outils. Enfin, si le modèle du rôle ne déclare pas la capacité vision (on le simule en la retirant du fichier) :

```console
$ sed 's/capabilities: {vision: true}/capabilities: {}/' loom.yaml > loom.sans-vision.yaml
$ uv run loom --config loom.sans-vision.yaml validate | tail -2
Configuration : /chemin/vers/mon-agent/ch12/loom.sans-vision.yaml: (racine) — Agent 'releve', rôle 'decrire_photo' : il reçoit les pièces jointes, mais le modèle 'VISION' n'a pas la capacité vision (capabilities.vision: true)
```

#### À retenir

- `--attach FICHIER` (répétable) en CLI ; en Python, `loom.run(..., attachments=[...])` (liste d'objets `Attachment`). Les pièces jointes sont stockées comme artefacts de la session.
- Un rôle dont `context` contient `attachments` exige un modèle `capabilities: {vision: true}` : l'erreur est détectée au chargement, pas au premier appel.
- Un rôle qui reçoit les pièces jointes est automatiquement masqué quand le run n'en a pas.
- `caller_context` transmet au rôle les informations sur l'appelant (client, utilisateur, métadonnées) que vous avez passées par `loom.run(..., context=CallerContext(...))` (de `loom_ia.core.model`). L'exemple 12.4 le met en pratique.

### 12.4 Savoir qui appelle : `caller_context`, `ToolContext` et rôles

#### Pourquoi

Mme Martin ouvre le portail de la Plomberie Dupont et écrit : « Où en est mon devis ? ». L'application sait qui elle est, puisqu'elle s'est connectée. L'agent, lui, ne le sait pas. Deux mauvaises solutions : demander à la cliente son numéro de devis (elle ne le connaît pas toujours), ou laisser le modèle le deviner (et un client malin obtiendrait le devis d'un autre en changeant un numéro). L'identité doit venir de l'application, pas de la conversation, et arriver telle quelle aux outils.

#### Objectif

Passer le contexte de l'appelant (utilisateur, métadonnées) depuis Python et depuis l'API REST, le lire dans un outil grâce à `ToolContext`, le donner à un rôle par `context: [caller_context]`, et retrouver ce contexte dans le journal.

#### Mise en place

On reste dans `ch12/`. Dans `outils.py`, complétez la ligne d'import de `ToolError` pour importer aussi `ToolContext` :

```python
from loom_ia.core.ports import ToolContext, ToolError
```

Puis ajoutez en fin de fichier un outil qui n'a **aucun argument** pour le modèle. Le paramètre annoté `ToolContext` est rempli par le moteur et n'apparaît pas dans le schéma de l'outil (voir l'exemple 2.2) ; son attribut `caller` porte le contexte de l'appelant (`tenant_id`, `user_id`, `metadata`).

```python
@tool
def mon_devis(ctx: ToolContext) -> dict:
    """Retourne le devis du client qui pose la question, sans qu'on ait à lui demander son numéro."""
    if ctx.caller.user_id != "mme.martin":
        raise ToolError("Aucun devis n'est rattaché à cet utilisateur.")
    return DEVIS["D-2026-042"]
```

(Le « mme.martin » écrit en dur tient lieu de table de correspondance entre utilisateurs et devis, que votre application aurait en base.) Fichier `ch12/agents/accueil.yaml`. Le rôle reçoit le contexte de l'appelant et le devis ; `{{ context.caller_context }}` rend le contexte en JSON.

```yaml
name: accueil
description: Répond à un client connecté sur le portail de la Plomberie Dupont.

main:
  model: ORCH_PORTAIL
  system: Tu es l'assistant du portail de la Plomberie Dupont. Tu réponds en français.

max_iterations: 4

tools:
  - python: mon_devis

roles:
  - name: ecrire_accueil
    description: Écrit la réponse au client connecté.
    model: REDACTEUR_PORTAIL
    system: Tu écris des réponses courtes et polies pour la Plomberie Dupont. N'invente rien.
    input_schema:
      type: object
      properties:
        sujet: {type: string, description: "ce dont le client a besoin"}
      required: [sujet]
    context:
      - caller_context
      - tool_results: [mon_devis]
    input_template: |-
      Appelant : {{ context.caller_context }}
      Devis : {{ context.tool_results.mon_devis }}
      Sujet : {{ args.sujet }}
    terminal: true
```

Dans `ch12/loom.yaml`, ajoutez ces deux modèles avant la section `telemetry:`. Le script de l'orchestrateur a trois réponses : la troisième ne sert que si l'outil refuse de répondre (cas de l'appelant inconnu).

```yaml
  - id: ORCH_PORTAIL
    sdk: fake
    model: fake-orch-portail
    params:
      script:
        - text: Je retrouve le devis de ce client.
          tool_calls:
            - name: mon_devis
        - text: Je rédige la réponse.
          tool_calls:
            - name: ecrire_accueil
              arguments: {sujet: "où en est mon devis"}
        - text: Je ne peux pas retrouver votre devis, car vous n'êtes pas identifié.

  - id: REDACTEUR_PORTAIL
    sdk: fake
    model: fake-redacteur-portail
    params:
      script:
        - text: Bonjour Mme Martin, votre devis D-2026-042 (1 840 € TTC) est valable jusqu'au 31 octobre.
```

Fichier `ch12/portail.py`. `CallerContext` se construit avec `user_id` et `metadata` (un dictionnaire JSON libre). Le script lance l'agent une fois avec le contexte, une fois sans, puis relit dans le journal le contexte du run et le message reçu par le rôle.

```python
import asyncio
import json

from loom_ia.access import Loom
from loom_ia.core.model import CallerContext


async def main() -> None:
    async with Loom.from_config("loom.yaml") as loom:
        contexte = CallerContext(
            user_id="mme.martin",
            metadata={"canal": "portail-client", "page": "mes-devis"},
        )
        result = await loom.run("accueil", "Où en est mon devis ?", context=contexte, run_id="portail-1")
        print(result.status, "-", result.text)

        # Le même agent sans contexte : l'outil ne sait pas pour qui travailler.
        anonyme = await loom.run("accueil", "Où en est mon devis ?", run_id="portail-2")
        print(anonyme.status, "-", anonyme.text)

        for evenement in await loom.events("portail-1"):
            if evenement.type == "run.started":
                print("run.started :", json.dumps(evenement.payload.context.model_dump(mode="json"), ensure_ascii=False))
            if evenement.type == "model.exchanged" and evenement.payload.url == "fake://fake-redacteur-portail":
                message = json.loads(evenement.payload.request_body)["messages"][0]["blocks"][0]["text"]
                print("--- message reçu par le rôle")
                print(message)


asyncio.run(main())
```

#### Exécution

```console
$ uv run loom validate | grep accueil
  accueil : modèle ORCH_PORTAIL, 1 outil(s) Python, rôle ecrire_accueil (REDACTEUR_PORTAIL)
$ uv run python portail.py
completed - Bonjour Mme Martin, votre devis D-2026-042 (1 840 € TTC) est valable jusqu'au 31 octobre.
completed - Je ne peux pas retrouver votre devis, car vous n'êtes pas identifié.
run.started : {"tenant_id": "default", "user_id": "mme.martin", "metadata": {"canal": "portail-client", "page": "mes-devis"}}
--- message reçu par le rôle
Appelant : {"tenant_id": "default", "user_id": "mme.martin", "metadata": {"canal": "portail-client", "page": "mes-devis"}}
Devis : {"numero": "D-2026-042", "client": "Mme Martin", "email": "mme.martin@example.fr", "objet": "Remplacement du chauffe-eau", "montant_ttc": 1840.0, "envoye_le": "2026-09-03", "valable_jusqu_au": "2026-10-31"}
Sujet : où en est mon devis
```

Pour la cliente connectée, l'outil `mon_devis` a trouvé le devis sans qu'on lui donne de numéro, et le rôle a reçu le contexte de l'appelant et le devis. Pour le run sans contexte, l'outil a refusé, et l'orchestrateur l'a su :

```console
$ uv run loom inspect portail-2
Run        : portail-2 (agent accueil, client default)
Session    : portail-2
Statut     : completed, 3 itération(s)
Usage      : 1577 → 174 tokens, 0.000000 $, 21 ms de pilotage

run accueil — completed, 35 ms, 0.000000 $
  étape 1
    modèle fake-orch-portail (main) — 2 ms, 374 → 63 tokens, 0.000000 $
      · répond    : Je retrouve le devis de ce client.
      · appelle   : mon_devis({})
  étape 2
    outil mon_devis — 1 ms (erreur)
      · résultat  : (erreur) Aucun devis n'est rattaché à cet utilisateur.
  étape 3
    modèle fake-orch-portail (main) — 2 ms, 520 → 69 tokens, 0.000000 $
      · répond    : Je rédige la réponse.
      · appelle   : ecrire_accueil({"sujet": "où en est mon devis"})
  étape 4
    appel ecrire_accueil — refusé avant exécution
      · résultat  : (erreur) Le rôle ecrire_accueil a besoin d'un résultat de mon_devis : appelle d'abord cet outil.
  étape 5
    modèle fake-orch-portail (main) — 3 ms, 683 → 42 tokens, 0.000000 $
      · répond    : Je ne peux pas retrouver votre devis, car vous n'êtes pas identifié.

Réponse finale :
  Je ne peux pas retrouver votre devis, car vous n'êtes pas identifié.
Bilan      : 3 appel(s) de modèle, 2 appel(s) d'outil dont 1 refusé(s) avant exécution
```

(Les durées en millisecondes varient d'une exécution à l'autre.) Le rôle n'a même pas été appelé : son contexte déclare le résultat de `mon_devis`, et tant que cet outil n'a rien donné dans le run, le rôle est refusé avec le message « appelle d'abord cet outil ».

La trace du run (`loom inspect --json`, la même que `GET /v1/traces/{run_id}`) porte le contexte dans l'événement `run.started` du span racine. L'identifiant d'utilisateur y est en clair ; les métadonnées sont rangées à part, dans `content`, et signalées dans `redacted` :

```console
$ uv run loom inspect portail-1 --json | jq -c '.spans[0].events[0] | {name, context: .data.context, redacted: .data.redacted, content}'
{"name":"run.started","context":{"tenant_id":"default","user_id":"mme.martin"},"redacted":["context.metadata"],"content":{"context.metadata":{"canal":"portail-client","page":"mes-devis"}}}
```

**En ligne de commande.** `loom run` n'a pas d'option pour l'utilisateur ni pour les métadonnées : ses options sont `--tenant`, `--session`, `--run-id`, `--attach` et `--judges`. Le run part donc avec un contexte vide, comme le run anonyme ci-dessus :

```console
$ uv run loom run accueil "Où en est mon devis ?" --run-id portail-cli
Je ne peux pas retrouver votre devis, car vous n'êtes pas identifié.

Statut     : completed · itérations : 3 · tokens : 1577/174 · coût : 0.0000 $
Run        : portail-cli
```

**En REST.** Le corps de `POST /v1/agents/{agent}/runs` accepte `user_id` et `metadata`. Dans un premier terminal :

```console
$ uv run loom serve --host 127.0.0.1 --port 18402
Profil     : aucun (ni --profile, ni LOOM_PROFILE, ni 'profile:') ; surcharges : aucune
API REST   : http://127.0.0.1:18402/v1
Agents     : accueil, relance, relance_ref, releve
```

Dans un second :

```console
$ curl -s -X POST http://127.0.0.1:18402/v1/agents/accueil/runs -H 'Content-Type: application/json' \
    -d '{"message":"Où en est mon devis ?","run_id":"portail-rest","user_id":"mme.martin","metadata":{"canal":"portail-client","page":"mes-devis"}}' \
    | jq '{run_id, status, text}'
{
  "run_id": "portail-rest",
  "status": "completed",
  "text": "Bonjour Mme Martin, votre devis D-2026-042 (1 840 € TTC) est valable jusqu'au 31 octobre."
}
$ curl -s -X POST http://127.0.0.1:18402/v1/agents/accueil/runs -H 'Content-Type: application/json' \
    -d '{"message":"Où en est mon devis ?","run_id":"portail-rest-anonyme"}' | jq '{status, text}'
{
  "status": "completed",
  "text": "Je ne peux pas retrouver votre devis, car vous n'êtes pas identifié."
}
$ curl -s http://127.0.0.1:18402/v1/sessions/portail-rest/events | jq -c 'select(.type=="run.started") | .payload.context'
{"tenant_id":"default","user_id":"mme.martin","metadata":{"canal":"portail-client","page":"mes-devis"}}
```

Le client (`tenant_id`) n'est pas dans le corps. Si l'appelant l'y glisse, la requête est refusée :

```console
$ curl -s -i -X POST http://127.0.0.1:18402/v1/agents/accueil/runs -H 'Content-Type: application/json' \
    -d '{"message":"x","tenant_id":"autre"}' | sed -n '1p;/^{/p'
HTTP/1.1 422 Unprocessable Content
{"detail":[{"type":"extra_forbidden","loc":["tenant_id"],"msg":"Extra inputs are not permitted","input":"autre"}]}
```

(Arrêtez le serveur avec Ctrl+C.)

#### À retenir

- **Le contexte de l'appelant est un `CallerContext`** (`from loom_ia.core.model import CallerContext`) : `tenant_id` (le client, `default` si rien n'est dit), `user_id` (texte libre ou rien) et `metadata` (un dictionnaire JSON). On le passe avec `loom.run(..., context=…)` et `loom.stream(..., context=…)`.
- **L'identité vient de l'application, jamais du modèle.** L'outil `mon_devis` ne prend aucun argument : même un modèle qui voudrait lire le devis d'un autre client n'a aucun moyen de le demander. C'est le bon endroit pour les droits d'accès par utilisateur.
- **Un outil le lit par `ToolContext`.** Un paramètre annoté `ToolContext` (de `loom_ia.core.ports`) est invisible pour le modèle ; `ctx.caller` rend le contexte, et `ctx.tenant_id`, `ctx.session_id`, `ctx.run_id`, `ctx.call_id` et `ctx.agent` situent l'appel.
- **Un rôle le reçoit par `context: [caller_context]`.** Dans `input_template`, `{{ context.caller_context }}` donne tout le contexte en JSON (`tenant_id`, `user_id`, `metadata`). Sans gabarit, il arrive dans un bloc balisé `<caller_context>`. Le chemin pointé fonctionne aussi : `{{ context.caller_context.user_id }}` rend `mme.martin` et `{{ context.caller_context.metadata.canal }}` rend `portail-client` (essayé avec ce même agent).
- **Le contexte est écrit au journal et passe dans les traces**, dans l'événement `run.started` (champ `context`) : on sait pour qui chaque run a tourné. Les `metadata` y sont traitées comme du *contenu*, au même titre que les textes des messages : dans la trace elles sont séparées du reste (`content`) et le champ `redacted` le dit. Les exports vers un collecteur, au niveau `capture.exports: metadata` (chapitre 25), n'emportent que la forme du run, et le journal garde tout en clair. Ne mettez donc dans `metadata` que ce que vous acceptez de retrouver dans un journal.
- **REST : `user_id` et `metadata` seulement.** Le client vient de la clé d'API (chapitre 22), jamais du corps de la requête ; un `tenant_id` dans le corps est refusé en 422.
- **En ligne de commande : pas de `user_id` ni de `metadata`.** Seul `--tenant` existe. Pour des runs au nom d'un utilisateur, passez par Python ou par REST.
- **Piège : `user_id` n'est pas une authentification.** Loom-IA transmet ce que l'appelant déclare. Sur une API ouverte, n'importe qui peut écrire `"user_id": "mme.martin"`. C'est à votre application (ou à une passerelle devant `loom serve`) de n'envoyer que l'identité qu'elle a vérifiée ; sans cela, le contrôle de l'outil `mon_devis` ne vaut rien. Le chapitre 22 montre les clés d'API par client.

---

## Chapitre 13 — Outils MCP

MCP (Model Context Protocol) est un standard pour exposer des outils à un agent depuis un autre programme : un serveur lancé par loom (transport `stdio`) ou joignable par le réseau (transport `http`). L'intérêt : réutiliser des outils qui existent déjà, ou les isoler dans leur propre process, sans les réécrire en Python dans `outils.py`.

Un serveur se déclare dans `mcp_servers:` du `loom.yaml`, puis chaque agent choisit ceux qu'il utilise dans sa liste `tools:`. Les outils d'un serveur portent le préfixe du serveur : `devis__chercher_devis`.

Clés d'un serveur : `name`, `transport` (`stdio` ou `http`), `command`, `args`, `env`, `env_from`, `cwd` (stdio), `url`, `headers_env` (http), `scope` (`shared`, `run` ou `tenant`), `connect_timeout`, `idle_timeout`, `tools` (surcharges par outil) et `circuit_breaker`. Clés d'un usage côté agent : `mcp` (nom du serveur), `alias`, `include` ou `exclude` (exclusifs), `required`, `tools`.

### 13.1 Brancher un petit serveur MCP

#### Pourquoi

Les devis de la Plomberie Dupont sont dans un autre programme (demain, un vrai logiciel de facturation). Vous voulez que l'agent les consulte sans importer ce code dans le projet, et sans lui donner accès à tout ce que le serveur sait faire.

#### Objectif

Écrire un serveur MCP stdio minimal avec FastMCP (trois outils), le déclarer dans `loom.yaml`, donner à l'agent seulement deux outils de lecture, et lancer une consultation.

#### Mise en place

```console
$ cd ..
$ mkdir -p ch13/agents ch13/prompts ch13/serveurs
$ cd ch13
```

Fichier `ch13/serveurs/serveur_devis.py`. Le package `mcp` vient de l'extra `mcp` installé plus haut. Chaque outil porte des **annotations** MCP qui disent s'il lit ou modifie ; `supprimer_devis` n'en porte aucune, ce qui servira en 13.2. `log_level="WARNING"` évite que FastMCP écrive des lignes d'information sur la sortie d'erreur à chaque requête.

```python
"""Petit serveur MCP (stdio) qui expose les devis de la Plomberie Dupont."""

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

mcp = FastMCP("devis", log_level="WARNING")

DEVIS = {
    "D-2026-042": {
        "numero": "D-2026-042",
        "client": "Mme Martin",
        "email": "mme.martin@example.fr",
        "objet": "Remplacement du chauffe-eau",
        "montant_ttc": 1840.0,
        "envoye_le": "2026-09-03",
        "valable_jusqu_au": "2026-10-31",
    },
    "D-2026-043": {
        "numero": "D-2026-043",
        "client": "M. Durand",
        "email": "durand@example.fr",
        "objet": "Dépannage fuite sous évier",
        "montant_ttc": 215.0,
        "envoye_le": "2026-09-10",
        "valable_jusqu_au": "2026-10-10",
    },
}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
def chercher_devis(numero: str) -> dict:
    """Retourne le devis d'un client à partir de son numéro (ex. D-2026-042)."""
    if numero not in DEVIS:
        raise ValueError(f"Devis {numero} introuvable.")
    return DEVIS[numero]


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
def lister_devis() -> list[str]:
    """Liste les numéros de devis connus."""
    return sorted(DEVIS)


@mcp.tool()  # aucune annotation : la spec MCP le suppose destructeur
def supprimer_devis(numero: str) -> str:
    """Supprime définitivement un devis."""
    DEVIS.pop(numero, None)
    return f"Devis {numero} supprimé."


if __name__ == "__main__":
    mcp.run()  # transport stdio par défaut
```

Fichier `ch13/prompts/devis.md` :

```markdown
Tu es l'assistant de la Plomberie Dupont. Utilise les outils pour répondre
aux questions sur les devis, en une ou deux phrases.
```

Fichier `ch13/agents/devis_mcp.yaml`. `include` limite l'agent à une liste blanche d'outils du serveur :

```yaml
name: devis_mcp
description: Répond aux questions sur les devis, via le serveur MCP « devis ».

main:
  model: SIMULE
  system_file: devis.md

max_iterations: 5

tools:
  - mcp: devis
    include: [chercher_devis, lister_devis]
```

Fichier `ch13/loom.yaml`. `command: python` est le Python de l'environnement `uv` (la commande se lance avec `uv run`), `args` est relatif au dossier de ce fichier. Le modèle `SIMULE_GESTION` sert aux exemples suivants.

```yaml
version: 1

agents_dir: agents/
prompts_dir: prompts/

models:
  - id: SIMULE
    sdk: fake
    model: fake-1
    params:
      script:
        - text: Je cherche le devis.
          tool_calls:
            - name: devis__chercher_devis
              arguments: {numero: D-2026-042}
        - text: Le devis D-2026-042 de Mme Martin (remplacement du chauffe-eau) s'élève à 1 840 € TTC.
  - id: SIMULE_GESTION
    sdk: fake
    model: fake-gestion
    params:
      script:
        - text: Je supprime le devis D-2026-043.
          tool_calls:
            - name: d__supprimer_devis
              arguments: {numero: D-2026-043}
        - text: J'ai traité la demande pour le devis D-2026-043.

mcp_servers:
  - name: devis                    # préfixe des outils : devis__chercher_devis
    transport: stdio               # le serveur est un programme lancé par loom
    command: python
    args: [serveurs/serveur_devis.py]   # relatif au dossier de loom.yaml
    scope: shared                  # une seule connexion pour tout le process

telemetry:
  logging: {level: WARNING}

storage:
  events: {backend: jsonl, path: data}
```

La sortie de `loom validate` ci-dessous liste aussi `gestion`, `gestion_nue` et `devis_requis`, écrits dans les sections suivantes : en suivant pas à pas, vous ne verrez d'abord que `devis_mcp`.

#### Exécution

```console
$ uv run loom validate | sed -n '/^Agents/p;/^  /p'
Agents     : devis_mcp, devis_requis, gestion, gestion_nue
  devis_mcp : modèle SIMULE, 0 outil(s) Python
    MCP : devis__chercher_devis, devis__lister_devis
  devis_requis : modèle SIMULE, 0 outil(s) Python
    MCP : devis__chercher_devis, devis__lister_devis
  gestion : modèle SIMULE_GESTION, 0 outil(s) Python
    MCP : d__chercher_devis, d__supprimer_devis
  gestion_nue : modèle SIMULE_GESTION, 0 outil(s) Python
    MCP : d__chercher_devis, d__supprimer_devis
```

`loom validate` démarre les serveurs pour lister les outils effectivement offerts à chaque agent : `devis_mcp` n'a que `chercher_devis` et `lister_devis` (pas `supprimer_devis`). Consultation :

```console
$ uv run loom run devis_mcp "Où en est le devis D-2026-042 ?" --run-id mcp-1
Le devis D-2026-042 de Mme Martin (remplacement du chauffe-eau) s'élève à 1 840 € TTC.

Statut     : completed · itérations : 2 · tokens : 691/114 · coût : 0.0000 $
Run        : mcp-1
$ uv run loom inspect mcp-1
Run        : mcp-1 (agent devis_mcp, client default)
Session    : mcp-1
Statut     : completed, 2 itération(s)
Usage      : 691 → 114 tokens, 0.000000 $, 18 ms de pilotage

run devis_mcp — completed, 508 ms, 0.000000 $
  étape 1
    modèle fake-1 (main) — 1 ms, 243 → 68 tokens, 0.000000 $
      · répond    : Je cherche le devis.
      · appelle   : devis__chercher_devis({"numero": "D-2026-042"})
  étape 2
    outil devis__chercher_devis — 5 ms
      · arguments : {"numero": "D-2026-042"}
      · résultat  : { "numero": "D-2026-042", "client": "Mme Martin", "email": "mme.martin@example.fr", "objet": "Rem…
  étape 3
    modèle fake-1 (main) — 1 ms, 448 → 46 tokens, 0.000000 $
      · répond    : Le devis D-2026-042 de Mme Martin (remplacement du chauffe-eau) s'élève à 1 840 € TTC.

Réponse finale :
  Le devis D-2026-042 de Mme Martin (remplacement du chauffe-eau) s'élève à 1 840 € TTC.
Bilan      : 2 appel(s) de modèle, 1 appel(s) d'outil
```

L'outil `devis__chercher_devis` a été exécuté par le serveur MCP, dans un process séparé, et son résultat est revenu au modèle.

#### À retenir

- `include: [a, b]` ou `exclude: [c]`, jamais les deux. Un `include` qui cite un outil inexistant produit un avertissement.
- Le préfixe `devis__` évite les collisions de noms entre serveurs. `alias:` le raccourcit (13.2).
- `scope` règle la durée de vie de la connexion : `shared` (une seule pour tout le process, ce qui convient à un serveur sans état comme celui-ci), `run` ou `tenant` (voir [`03-production.md`](03-production.md) pour le multi-client).
- Transport `http` : `url:` à la place de `command:`, avec les en-têtes secrets lus dans l'environnement (`headers_env`). Non exécuté ici : aucun serveur MCP distant n'est disponible dans cet environnement. La déclaration reste du même modèle.

### 13.2 Qui doit valider quoi : annotations et surcharges

#### Pourquoi

`supprimer_devis` détruit un document. Le serveur ne dit rien de plus que « outil sans annotation ». Si loom prend ça pour une action sans importance, l'agent peut supprimer un devis sans que personne ne soit prévenu. Il faut savoir ce que loom en déduit, et le corriger depuis chez vous (sans toucher au serveur).

#### Objectif

Voir ce qui se passe quand un outil non annoté est utilisé tel quel, puis ajouter `approval: always` côté agent pour déclencher une validation humaine (chapitre 9).

#### Mise en place

On reste dans `ch13/`. Fichier `ch13/agents/gestion_nue.yaml` (rien n'est déclaré sur `supprimer_devis`) :

```yaml
name: gestion_nue
description: Comme « gestion », mais sans rien déclarer sur supprimer_devis.

main:
  model: SIMULE_GESTION
  system_file: devis.md

max_iterations: 5

tools:
  - mcp: devis
    alias: d                    # préfixe raccourci : d__chercher_devis
    exclude: [lister_devis]     # tout, sauf cet outil
```

Fichier `ch13/agents/gestion.yaml` (la surcharge `tools:` déclare `supprimer_devis` irréversible et à validation obligatoire) :

```yaml
name: gestion
description: Gère les devis (consultation et suppression).

main:
  model: SIMULE_GESTION
  system_file: devis.md

max_iterations: 5

tools:
  - mcp: devis
    alias: d                    # préfixe raccourci : d__chercher_devis
    exclude: [lister_devis]     # tout, sauf cet outil
    tools:
      supprimer_devis: {side_effects: irreversible, approval: always}
```

#### Exécution

Agent sans surcharge :

```console
$ uv run loom run gestion_nue "Supprime le devis D-2026-043." --run-id mcp-2
J'ai traité la demande pour le devis D-2026-043.

Statut     : completed · itérations : 2 · tokens : 662/107 · coût : 0.0000 $
Run        : mcp-2
$ uv run loom inspect mcp-2
Run        : mcp-2 (agent gestion_nue, client default)
Session    : mcp-2
Statut     : completed, 2 itération(s)
Usage      : 662 → 107 tokens, 0.000000 $, 12 ms de pilotage

run gestion_nue — completed, 529 ms, 0.000000 $
  étape 1
    modèle fake-gestion (main) — 1 ms, 252 → 70 tokens, 0.000000 $
      · répond    : Je supprime le devis D-2026-043.
      · appelle   : d__supprimer_devis({"numero": "D-2026-043"})
  étape 2
    outil d__supprimer_devis — 4 ms
      · arguments : {"numero": "D-2026-043"}
      · résultat  : Devis D-2026-043 supprimé.
  étape 3
    modèle fake-gestion (main) — 1 ms, 410 → 37 tokens, 0.000000 $
      · répond    : J'ai traité la demande pour le devis D-2026-043.

Réponse finale :
  J'ai traité la demande pour le devis D-2026-043.
Bilan      : 2 appel(s) de modèle, 1 appel(s) d'outil
```

L'outil a été **exécuté sans validation** : `supprimer_devis` est bien traité comme irréversible, mais une action irréversible ne déclenche pas à elle seule de pause pour un outil MCP. Avec la surcharge :

```console
$ uv run loom run gestion "Supprime le devis D-2026-043." --run-id mcp-3
—

Statut     : paused · itérations : 1 · tokens : 252/70 · coût : 0.0000 $
Run        : mcp-3
En attente : d__supprimer_devis (fake_0_0) — loom approve mcp-3 --call fake_0_0
$ uv run loom inspect mcp-3 | sed -n 6,14p
run gestion — inachevé, 473 ms (ouvert)
  étape 1
    modèle fake-gestion (main) — 1 ms, 252 → 70 tokens, 0.000000 $
      · répond    : Je supprime le devis D-2026-043.
      · appelle   : d__supprimer_devis({"numero": "D-2026-043"})
  étape 2
    approbation pour d__supprimer_devis — en attente

Bilan      : 1 appel(s) de modèle, 0 appel(s) d'outil, 1 approbation(s)
```

Le run est en pause avant la suppression. On approuve (voir chapitre 9) :

```console
$ uv run loom approve mcp-3 --call fake_0_0 --by denis
J'ai traité la demande pour le devis D-2026-043.
Accordé : fake_0_0

Statut     : completed · itérations : 2 · tokens : 662/107 · coût : 0.0000 $
Run        : mcp-3
```

#### À retenir

- Déduction faite des annotations MCP : `readOnlyHint` donne un outil sans effet et idempotent ; sinon l'outil est traité comme irréversible, sauf si `destructiveHint` vaut explicitement `False` (alors réversible). Un outil **sans annotation** est donc traité comme irréversible.
- Ordre de priorité des surcharges, de la plus faible à la plus forte : annotations du serveur, puis `tools:` du serveur dans `loom.yaml`, puis `tools:` de l'usage dans l'agent.
- Piège : « irréversible » ne signifie pas « validation obligatoire » pour un outil MCP. Si l'action est dangereuse, écrivez `approval: always` : le journal doit alors être durable (chapitre 9).
- `alias: d` renomme le préfixe : `d__chercher_devis` au lieu de `devis__chercher_devis`. Pensez-y dans vos scripts de test.
- Les surcharges portent sur les propriétés d'un outil : `side_effects`, `approval`, `idempotent`, `on_unknown`, `timeout`, `offload_over` (les mêmes que pour `@tool`).

### 13.3 Quand le serveur est absent

#### Pourquoi

Un serveur MCP peut être arrêté, mal installé, ou changer de chemin. L'agent doit-il continuer sans l'outil, ou refuser de travailler ? Cela dépend de ce qu'il fait : un agent qui répond à une question de prix peut dire « je ne sais pas » ; un agent qui ne sert qu'à lire les devis ne doit pas inventer.

#### Objectif

Simuler un serveur injoignable, voir l'agent continuer sans l'outil (comportement par défaut), puis le rendre obligatoire avec `required: true`.

#### Mise en place

On reste dans `ch13/`. Fichier `ch13/agents/devis_requis.yaml` :

```yaml
name: devis_requis
description: Répond aux questions sur les devis, et refuse de tourner si le serveur est absent.

main:
  model: SIMULE
  system_file: devis.md

max_iterations: 5

tools:
  - mcp: devis
    include: [chercher_devis, lister_devis]
    required: true              # sans ce serveur, le run échoue
```

La panne se simule en pointant `args` vers un fichier qui n'existe pas, dans une copie du `loom.yaml` :

```console
$ sed 's#serveurs/serveur_devis.py#serveurs/serveur_absent.py#' loom.yaml > loom.panne.yaml
```

#### Exécution

Par défaut, le serveur est facultatif :

```console
$ uv run loom --config loom.panne.yaml run devis_mcp "Où en est le devis D-2026-042 ?" --run-id mcp-4
Le devis D-2026-042 de Mme Martin (remplacement du chauffe-eau) s'élève à 1 840 € TTC.
python: can't open file '/chemin/vers/mon-agent/ch13/serveurs/serveur_absent.py': [Errno 2] No such file or directory
2026-10-10 17:43:38 WARNING  loom_ia.engine.executor — Source d'outils devis indisponible : Connection closed [run_id=mcp-4]

Statut     : completed · itérations : 2 · tokens : 462/114 · coût : 0.0000 $
Run        : mcp-4
$ uv run loom inspect mcp-4
Run        : mcp-4 (agent devis_mcp, client default)
Session    : mcp-4
Statut     : completed, 2 itération(s)
Usage      : 462 → 114 tokens, 0.000000 $, 10 ms de pilotage

run devis_mcp — completed, 68 ms, 0.000000 $
  · tool.source_unavailable (warning)
  étape 1
    modèle fake-1 (main) — 1 ms, 152 → 68 tokens, 0.000000 $
      · répond    : Je cherche le devis.
      · appelle   : devis__chercher_devis({"numero": "D-2026-042"})
  étape 2
    appel devis__chercher_devis — refusé avant exécution
      · résultat  : (erreur) Outil inconnu : 'devis__chercher_devis'. Outils disponibles : aucun.
  étape 3
    modèle fake-1 (main) — 0 ms, 310 → 46 tokens, 0.000000 $
      · répond    : Le devis D-2026-042 de Mme Martin (remplacement du chauffe-eau) s'élève à 1 840 € TTC.

Réponse finale :
  Le devis D-2026-042 de Mme Martin (remplacement du chauffe-eau) s'élève à 1 840 € TTC.
Bilan      : 2 appel(s) de modèle, 1 appel(s) d'outil dont 1 refusé(s) avant exécution
```

La commande d'abord se plaint (`python: can't open file …`), mais le run a **continué** : l'outil a disparu de la liste, l'événement `tool.source_unavailable` est journalisé, et le modèle a reçu « Outil inconnu … Outils disponibles : aucun ». Dans notre script, le modèle simulé répond quand même, comme s'il avait lu le devis : un vrai modèle devrait constater l'absence, mais rien ne l'y oblige. Avec `required: true` :

```console
$ uv run loom --config loom.panne.yaml run devis_requis "Où en est le devis D-2026-042 ?" --run-id mcp-5; echo "code de sortie : $?"
source devis requise et indisponible : Connection closed
python: can't open file '/chemin/vers/mon-agent/ch13/serveurs/serveur_absent.py': [Errno 2] No such file or directory
2026-10-10 17:43:40 WARNING  loom_ia.engine.executor — Source d'outils devis indisponible : Connection closed [run_id=mcp-5]

Statut     : failed · itérations : 0 · tokens : 0/0 · coût : 0.0000 $
Run        : mcp-5
Erreur     : source devis requise et indisponible : Connection closed (tool.source_unavailable)
code de sortie : 1
```

Le run échoue avant tout appel de modèle (`itérations : 0`).

#### À retenir

- Par défaut, un serveur MCP indisponible n'arrête pas le run : l'outil disparaît et un événement d'avertissement est écrit. **Un agent qui ne peut pas se passer de cet outil doit déclarer `required: true`.**
- Piège : avec un serveur facultatif absent, le modèle peut répondre quand même, en se fiant à sa mémoire ou à ce que son prompt suggère. Écrivez dans le prompt de l'agent ce qu'il doit faire quand l'outil est absent (« réponds que tu ne peux pas consulter les devis »).
- `circuit_breaker`, `connect_timeout` et `idle_timeout` du serveur évitent de retenter indéfiniment un serveur en panne : voir [`03-production.md`](03-production.md).

---

## Chapitre 14 — Juges : contrôler le fond

Un contrat vérifie la **forme**. Un **juge** vérifie le **fond** : un second modèle lit la réponse et la note, critère par critère, selon des règles que vous écrivez. Le juge répond en appelant un outil interne `verdict` avec, pour chaque critère, un score entre 0 et 1 et une raison.

Un critère a un `name`, une `rule` (en français, comme une consigne), un `min_score` (0,8 par défaut) et un indicateur `blocking` (vrai par défaut : un score trop bas refuse la réponse ; faux : le score est journalisé mais ne bloque pas). Quand le juge refuse, ses raisons sont renvoyées à l'auteur de la réponse, qui réécrit (`repair`), puis `on_failure` décide quoi faire si cela échoue encore.

### 14.1 Le flux complet : outil, rôle, contrat, juge

#### Pourquoi

C'est l'exemple qui réunit les chapitres 12 à 14. Le rédacteur de 12.1 écrit un e-mail à partir du devis. Mais un modèle peut se tromper de montant : écrire 2 100 € alors que le devis dit 1 840 €. Un contrat ne verrait rien (« 2 100 € » est un texte valide) ; l'envoi serait faux, devant une vraie cliente. Le juge compare l'e-mail au devis.

#### Objectif

Brancher sur le rôle `rediger_relance` un contrat (forme) puis un juge (fond), observer le juge refuser un montant inventé, le rédacteur corriger, puis récupérer le résultat en Python.

#### Mise en place

```console
$ cd ..
$ mkdir -p ch14/agents ch14/prompts
$ cd ch14
```

Fichier `ch14/outils.py` (l'outil `chercher_devis` du chapitre 12) :

```python
from loom_ia.core.ports import ToolError
from loom_ia.tools import tool

DEVIS = {
    "D-2026-042": {
        "numero": "D-2026-042",
        "client": "Mme Martin",
        "email": "mme.martin@example.fr",
        "objet": "Remplacement du chauffe-eau",
        "montant_ttc": 1840.0,
        "envoye_le": "2026-09-03",
        "valable_jusqu_au": "2026-10-31",
    },
}


@tool
def chercher_devis(numero: str) -> dict:
    """Retourne le devis d'un client à partir de son numéro (ex. D-2026-042)."""
    if numero not in DEVIS:
        raise ToolError(f"Devis {numero} introuvable.")
    return DEVIS[numero]
```

Fichiers `ch14/prompts/relance.md` (orchestrateur) et `ch14/prompts/redacteur.md` (rôle) :

```markdown
Tu es l'assistant de la Plomberie Dupont. Pour relancer un client :
1. cherche le devis avec `chercher_devis` ;
2. confie la rédaction au rôle `rediger_relance`.
```

```markdown
Tu rédiges des e-mails de relance courts et polis pour un artisan.
Réponds uniquement par un objet JSON : {"objet": "...", "corps": "..."}.
N'utilise que les informations qu'on te donne : n'invente aucun montant, date ou délai.
```

Fichier `ch14/agents/relance.yaml`. Le rôle est `terminal` ; son `output` est le contrat du chapitre 11, son `judge` le contrôle de fond. `context` donne au juge la demande et le devis : sans eux il ne pourrait pas savoir si un montant est exact. Le critère `fidele` est bloquant (valeurs par défaut) ; `ton` ne l'est pas et se contente de 0,6.

```yaml
name: relance
description: Prépare la relance d'un devis resté sans réponse.

main:
  model: ORCHESTRATEUR
  system_file: relance.md

max_iterations: 6

tools:
  - python: chercher_devis

roles:
  - name: rediger_relance
    description: Rédige l'e-mail de relance d'un devis.
    model: REDACTEUR
    system_file: redacteur.md
    input_schema:
      type: object
      properties:
        ton: {type: string, description: "cordial, ferme, bref…"}
      required: [ton]
    context:
      - user_input
      - tool_results: [chercher_devis]
    input_template: |-
      Demande de l'artisan : {{ context.user_input }}
      Devis : {{ context.tool_results.chercher_devis }}
      Ton : {{ args.ton }}
    terminal: true

    # 1. La forme : un contrat de sortie
    output:
      schema:
        type: object
        properties:
          objet: {type: string, minLength: 5}
          corps: {type: string, minLength: 20}
        required: [objet, corps]
        additionalProperties: false
      repair: {max_attempts: 1}
      on_failure: fail

    # 2. Le fond : un juge
    judge:
      model: JUGE
      context:
        - user_input
        - tool_results: [chercher_devis]
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

Fichier `ch14/loom.yaml`. Trois modèles simulés : l'orchestrateur ; le rédacteur, dont la première version invente 2 100 € ; le juge, dont la réponse dépend de la sortie qu'il lit (`with_text`) : il condamne « 2 100 € » et accepte « 1 840 € ».

```yaml
version: 1

imports: [outils]
agents_dir: agents/
prompts_dir: prompts/

models:
  - id: ORCHESTRATEUR
    sdk: fake
    model: fake-orchestrateur
    params:
      script:
        - text: Je cherche le devis.
          tool_calls:
            - name: chercher_devis
              arguments: {numero: D-2026-042}
        - text: Je confie la rédaction au rédacteur.
          tool_calls:
            - name: rediger_relance
              arguments: {ton: cordial}

  - id: REDACTEUR
    sdk: fake
    model: fake-redacteur
    params:
      script:
        # 1re version : le modèle invente un montant (2 100 € au lieu de 1 840 €)
        - text: |-
            {"objet": "Votre devis D-2026-042", "corps": "Bonjour Mme Martin, votre devis D-2026-042 de 2 100 € est toujours valable jusqu'au 31 octobre."}
        # 2e version, après le refus du juge
        - text: |-
            {"objet": "Votre devis D-2026-042", "corps": "Bonjour Mme Martin, votre devis D-2026-042 de 1 840 € est toujours valable jusqu'au 31 octobre. Je reste à votre disposition."}

  - id: JUGE
    sdk: fake
    model: fake-juge
    params:
      script:
        # Le juge répond selon la sortie qu'il lit
        - with_text: 2 100 €
          tool_calls:
            - name: verdict
              arguments:
                criteria:
                  - {name: fidele, score: 0.1, reason: "Le montant de 2 100 € ne figure pas dans le devis (1 840 €)."}
                  - {name: ton, score: 0.9, reason: Ton cordial.}
        - with_text: 1 840 €
          tool_calls:
            - name: verdict
              arguments:
                criteria:
                  - {name: fidele, score: 1.0, reason: "Montant, numéro et date de validité conformes au devis."}
                  - {name: ton, score: 0.9, reason: Ton cordial.}

telemetry:
  logging: {level: WARNING}

storage:
  events: {backend: jsonl, path: data}
```

Fichier `ch14/relance.py` :

```python
import asyncio

from loom_ia.access import Loom


async def main() -> None:
    async with Loom.from_config("loom.yaml") as loom:
        resultat = await loom.run(
            "relance", "Relance Mme Martin pour le devis D-2026-042, sur un ton cordial."
        )
        print(resultat.status, "| vérifiée :", not resultat.unverified)
        print("data  :", resultat.data)
        for verdict in resultat.verdicts:
            print(verdict.judge, verdict.attempt, verdict.passed, verdict.blocked)
            for critere in verdict.criteria:
                print("   ", critere.name, critere.score)


asyncio.run(main())
```

#### Exécution

```console
$ uv run loom run relance "Relance Mme Martin pour le devis D-2026-042, sur un ton cordial." --run-id juge-1
{"objet": "Votre devis D-2026-042", "corps": "Bonjour Mme Martin, votre devis D-2026-042 de 1 840 € est toujours valable jusqu'au 31 octobre. Je reste à votre disposition."}

Statut     : completed · itérations : 2 · tokens : 3257/423 · coût : 0.0000 $
Run        : juge-1
Juge       : rediger_relance (rôle rediger_relance, appel 1), tentative 1 — refusée : fidele 0,10 (seuil 0,80), ton 0,90
Juge       : rediger_relance (rôle rediger_relance, appel 1), tentative 2 — acceptée : fidele 1,00, ton 0,90
```

La CLI imprime une ligne `Juge` par tentative : la première est refusée sur le critère `fidele` (0,10 pour un seuil de 0,80) ; le rédacteur a corrigé ; la seconde est acceptée. Le détail dans `inspect` :

```console
$ uv run loom inspect juge-1
Run        : juge-1 (agent relance, client default)
Session    : juge-1
Statut     : completed, 2 itération(s)
Usage      : 3257 → 423 tokens, 0.000000 $, 44 ms de pilotage

run relance — completed, 61 ms, 0.000000 $
  étape 1
    modèle fake-orchestrateur (main) — 1 ms, 407 → 66 tokens, 0.000000 $
      · répond    : Je cherche le devis.
      · appelle   : chercher_devis({"numero": "D-2026-042"})
  étape 2
    outil chercher_devis — 1 ms
      · arguments : {"numero": "D-2026-042"}
      · résultat  : {"numero": "D-2026-042", "client": "Mme Martin", "email": "mme.martin@example.fr", "objet": "Remp…
  étape 3
    modèle fake-orchestrateur (main) — 1 ms, 660 → 69 tokens, 0.000000 $
      · répond    : Je confie la rédaction au rédacteur.
      · appelle   : rediger_relance({"ton": "cordial"})
  étape 4
    rôle rediger_relance — 25 ms
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
      · résultat  : {"objet": "Votre devis D-2026-042", "corps": "Bonjour Mme Martin, votre devis D-2026-042 de 1 840…
      appels du rôle rediger_relance
        modèle fake-redacteur (rediger_relance) — 0 ms, 337 → 63 tokens, 0.000000 $
          · répond    : {"objet": "Votre devis D-2026-042", "corps": "Bonjour Mme Martin, votre devis D-2026-042 de 2 100…
        modèle fake-redacteur (rediger_relance) — 0 ms, 492 → 70 tokens, 0.000000 $
          · répond    : {"objet": "Votre devis D-2026-042", "corps": "Bonjour Mme Martin, votre devis D-2026-042 de 1 840…
      juge rediger_relance
        modèle fake-juge (judge:rediger_relance) — 0 ms, 677 → 78 tokens, 0.000000 $
          · appelle   : verdict({"criteria": [{"name": "fidele", "score": 0.1, "reason": "Le montant de 2 100 € ne figure…
      juge rediger_relance
        modèle fake-juge (judge:rediger_relance) — 0 ms, 684 → 77 tokens, 0.000000 $
          · appelle   : verdict({"criteria": [{"name": "fidele", "score": 1.0, "reason": "Montant, numéro et date de vali…

Réponse finale :
  {"objet": "Votre devis D-2026-042", "corps": "Bonjour Mme Martin, votre devis D-2026-042 de 1 840 € est toujours valable jusqu'au 31 octobre. Je reste à votre disposition."}
Bilan      : 6 appel(s) de modèle, 2 appel(s) d'outil
```

On y lit toute la chaîne de 12.1 à 14.1 : l'outil, le rôle, les deux contrôles de contrat (`passed`), le verdict refusé (`fidele 0.10`), la politique de réparation `loom.judge.rediger_relance`, la seconde version du rédacteur, le second verdict accepté. Le résultat en Python :

```console
$ uv run python relance.py
completed | vérifiée : True
data  : {'objet': 'Votre devis D-2026-042', 'corps': "Bonjour Mme Martin, votre devis D-2026-042 de 1 840 € est toujours valable jusqu'au 31 octobre. Je reste à votre disposition."}
rediger_relance 1 False True
    fidele 0.1
    ton 0.9
rediger_relance 2 True False
    fidele 1.0
    ton 0.9
```

`resultat.data` contient l'objet validé par le contrat ; `resultat.verdicts` liste chaque jugement avec sa tentative, `passed`, `blocked` et ses scores. Le coût de la qualité se lit dans le rapport :

```console
$ uv run loom report juge-1
Consommation — run juge-1
  Total                   6 appels ·   3257/423 tokens · 0,00000 $
  Par rôle :
    main                    2 appels ·   1067/135 tokens · 0,00000 $
    rediger_relance         2 appels ·    829/133 tokens · 0,00000 $
    judge:rediger_relance   2 appels ·   1361/155 tokens · 0,00000 $
  Par modèle :
    fake-orchestrateur      2 appels ·   1067/135 tokens · 0,00000 $
    fake-redacteur          2 appels ·    829/133 tokens · 0,00000 $
    fake-juge               2 appels ·   1361/155 tokens · 0,00000 $
```

Le juge représente 2 des 6 appels de modèle de ce run. Voyons ce qui se serait passé sans lui :

```console
$ uv run loom run relance "Relance Mme Martin pour le devis D-2026-042, sur un ton cordial." --judges skip --run-id juge-2
{"objet": "Votre devis D-2026-042", "corps": "Bonjour Mme Martin, votre devis D-2026-042 de 2 100 € est toujours valable jusqu'au 31 octobre."}

Statut     : completed · itérations : 2 · tokens : 1404/198 · coût : 0.0000 $
Run        : juge-2
```

L'e-mail à 2 100 € est parti tel quel : le contrat l'a validé (c'est un texte conforme), personne n'a regardé le fond.

#### À retenir

- Le contrat et le juge se complètent : le contrat rejette ce qui est mal formé (sans appel de modèle, gratuit), le juge rejette ce qui est faux (un appel de modèle, payant).
- `RunResult.verdicts` : `.judge`, `.attempt`, `.passed`, `.blocked`, `.criteria[].name/.score`. L'événement du journal correspondant est `judge.evaluated`.
- Un rôle `terminal` avec contrat et juge donne directement `RunResult.data`.
- `loom run --judges auto|force|skip` (Python : `judges=`) règle l'usage des juges pour un run : `skip` est un outil de mise au point, jamais une option de production (14.2).
- Rédigez les `rule` comme des consignes précises et vérifiables (« chaque montant figure dans le devis »), pas comme des vœux (« l'e-mail est bon »).
- Piège : un juge est lui aussi un modèle. Donnez-lui le contexte qui permet de vérifier (ici le devis) ; sans lui, il ne peut que deviner.

### 14.2 Un juge économique et fiable : `when`, profils et indépendance

#### Pourquoi

Un juge coûte un appel de modèle à chaque run. Vous pouvez décider de ne pas juger tous les runs, ou seulement en production. Mais un juge mal réglé est pire que pas de juge : il donne une fausse assurance.

#### Objectif

Limiter l'usage du juge avec `when` (échantillon, condition Python, profils), comprendre pourquoi un juge restreint à `prod` est silencieusement ignoré sans profil, et découvrir les trois contrôles que le profil `prod` impose à un juge.

#### Mise en place

On reste dans `ch14/`. Fichier `ch14/conditions.py`, une condition qui n'évalue que les e-mails citant un montant :

```python
from loom_ia.core.model import JudgeInput


def cite_un_montant(entree: JudgeInput) -> bool:
    """Ne paie un juge que pour un e-mail qui cite un montant."""
    return "€" in entree.output
```

Fichier `ch14/agents/relance_eco.yaml`. Seul le bloc `when` du juge est nouveau :

```yaml
name: relance_eco
description: Prépare la relance d'un devis resté sans réponse.

main:
  model: ORCHESTRATEUR
  system_file: relance.md

max_iterations: 6

tools:
  - python: chercher_devis

roles:
  - name: rediger_relance
    description: Rédige l'e-mail de relance d'un devis.
    model: REDACTEUR
    system_file: redacteur.md
    input_schema:
      type: object
      properties:
        ton: {type: string, description: "cordial, ferme, bref…"}
      required: [ton]
    context:
      - user_input
      - tool_results: [chercher_devis]
    input_template: |-
      Demande de l'artisan : {{ context.user_input }}
      Devis : {{ context.tool_results.chercher_devis }}
      Ton : {{ args.ton }}
    terminal: true

    # 1. La forme : un contrat de sortie
    output:
      schema:
        type: object
        properties:
          objet: {type: string, minLength: 5}
          corps: {type: string, minLength: 20}
        required: [objet, corps]
        additionalProperties: false
      repair: {max_attempts: 1}
      on_failure: fail

    # 2. Le fond : un juge
    judge:
      model: JUGE
      when:
        sample: 1.0                      # 0.2 = un run sur cinq
        condition: conditions:cite_un_montant
        profiles: [prod]                 # inactif hors production
      context:
        - user_input
        - tool_results: [chercher_devis]
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

`when` a quatre clés, combinées par « et » : `sample` (probabilité de juger, de 0 à 1), `condition` (une fonction `module:fonction` qui reçoit un `JudgeInput`, avec entre autres `output`, `data`, `request`, `arguments` et `caller`, et rend un booléen), `tenants` (restriction à certains clients) et `profiles` (restriction à certains profils).

#### Exécution

Sans profil, le juge de `relance_eco` est restreint à `prod` :

```console
$ uv run loom run relance_eco "Relance Mme Martin pour le devis D-2026-042, sur un ton cordial." --run-id juge-3
{"objet": "Votre devis D-2026-042", "corps": "Bonjour Mme Martin, votre devis D-2026-042 de 2 100 € est toujours valable jusqu'au 31 octobre."}

Statut     : completed · itérations : 2 · tokens : 1404/198 · coût : 0.0000 $
Run        : juge-3
$ uv run loom inspect juge-3 | grep -E "contrôle|juge|Statut"
Run        : juge-3 (agent relance_eco, client default)
Session    : juge-3
Statut     : completed, 2 itération(s)
      · contrôle contract : passed
      · contrôle judge : skipped
```

Le montant inventé (2 100 €) est passé, et `inspect` dit seulement `contrôle judge : skipped`. La raison exacte figure dans l'événement `guard.checked` (raison `other_profile`). Avec le profil `prod` :

```console
$ uv run loom --profile prod run relance_eco "Relance Mme Martin pour le devis D-2026-042, sur un ton cordial." --run-id juge-4
{"objet": "Votre devis D-2026-042", "corps": "Bonjour Mme Martin, votre devis D-2026-042 de 1 840 € est toujours valable jusqu'au 31 octobre. Je reste à votre disposition."}

Statut     : completed · itérations : 2 · tokens : 3257/423 · coût : 0.0000 $
Run        : juge-4
Juge       : rediger_relance (rôle rediger_relance, appel 1), tentative 1 — refusée : fidele 0,10 (seuil 0,80), ton 0,90
Juge       : rediger_relance (rôle rediger_relance, appel 1), tentative 2 — acceptée : fidele 1,00, ton 0,90
$ uv run loom inspect juge-4 | grep -E "contrôle|juge|Statut"
Run        : juge-4 (agent relance_eco, client default)
Session    : juge-4
Statut     : completed, 2 itération(s)
      · contrôle contract : passed
      · contrôle judge : failed → retry
      · contrôle contract : passed
      · contrôle judge : passed
      juge rediger_relance
        modèle fake-juge (judge:rediger_relance) — 1 ms, 677 → 78 tokens, 0.000000 $
      juge rediger_relance
        modèle fake-juge (judge:rediger_relance) — 0 ms, 684 → 77 tokens, 0.000000 $
```

Cette fois le juge est actif : même scénario qu'en 14.1, refus puis acceptation. Le profil `prod` interdit aussi de contourner un juge depuis la ligne de commande :

```console
$ uv run loom --profile prod run relance "Relance Mme Martin pour le devis D-2026-042, sur un ton cordial." --judges skip --run-id juge-5; echo "code de sortie : $?"
Configuration : Profil prod : judges='skip' retire un contrôle et n'y est pas permis
code de sortie : 2
```

Deux autres exigences du profil `prod`. Il refuse un juge qui utilise **le même modèle** que la sortie qu'il évalue : un modèle a tendance à approuver ses propres erreurs. On fabrique la variante avec `sed` (le juge passe sur le modèle `REDACTEUR`) :

```console
$ sed -e 's/^name: relance$/name: relance_meme/' -e 's/model: JUGE/model: REDACTEUR/' agents/relance.yaml > agents/relance_meme.yaml
$ uv run loom validate | grep -E "WARNING|monté"
2026-10-10 17:50:00 WARNING  loom_ia.runtime.wiring — Agent 'relance_meme', juge 'rediger_relance' : même modèle que la sortie qu'il évalue (REDACTEUR) : juge corrélé
3 agent(s) monté(s) sans erreur.
$ uv run loom --profile prod validate | grep -E "Configuration|monté"
Configuration : Profil prod : Agent 'relance_meme', juge 'rediger_relance' : même modèle que la sortie qu'il évalue (REDACTEUR) : juge corrélé
```

En développement, c'est un simple avertissement ; en production, c'est une erreur. Enfin, il refuse un juge **bloquant mais échantillonné** : s'il ne juge qu'un run sur cinq, les quatre autres passent sans contrôle, alors que le critère est censé bloquer.

```console
$ sed -e 's/^name: relance_eco$/name: relance_echant/' -e 's/sample: 1.0 /sample: 0.2 /' agents/relance_eco.yaml > agents/relance_echant.yaml
$ uv run loom --profile prod validate | grep -E "Configuration|monté"
Configuration : Profil prod : Agent 'relance_echant', juge 'rediger_relance' : bloquant, mais ne juge qu'une partie des runs (sample 0.2)
```

#### À retenir

- `sample` fixe la part de runs jugés. Pour un juge bloquant en production, gardez `sample: 1.0` : réduisez plutôt le coût avec `condition` (ne juger que ce qui mérite de l'être) ou avec un modèle de juge moins cher que le rédacteur, mais **différent** de lui.
- Écart doc/réel : le README décrit le juge de même modèle comme refusé en `prod`, mais pas qu'un juge bloquant avec `sample` inférieur à 1 l'est aussi (« bloquant, mais ne juge qu'une partie des runs »).
- Piège important : un juge avec `profiles: [prod]` est **silencieusement ignoré** quand aucun profil n'est actif, comme dans l'essai ci-dessus. Aucun avertissement, seulement `skipped` dans `inspect` et la raison `other_profile` dans le journal. Testez vos juges avec `--profile prod` (et `uv run loom --profile prod validate` en intégration continue).
- Les raisons de saut d'un juge, dans l'événement `guard.checked` : `other_profile`, `filtered`, `sampled_out`, `condition_false`, `caller_skip` (`--judges skip`).
- `timeout` et `on_error: block|allow` s'appliquent au juge lui-même : si le modèle juge plante, `block` (défaut) échoue le run, `allow` le laisse passer. En production, `block`.
- Le juge accepte aussi `fallbacks` (modèles de secours) et `name` ; son `on_failure` (`fail`, `unverified`, `fallback`, avec `fallback_message`) fonctionne comme celui d'un contrat (11.2).

---

## Écarts constatés entre la documentation et la version installée

Ces points ont été vérifiés par exécution avec loom-ia 2.0.0 ; les sources de lecture disponibles correspondent à la 2.0.1 et diffèrent par endroits du paquet installé (par exemple, certains modules d'accès et d'adaptateurs de la 2.0.1 n'existent pas dans la 2.0.0).

| # | Sujet | Documentation | Constaté | Voir |
|---|---|---|---|---|
| 1 | Décision de réparation | « Réparer » / « Repair » dans le README | `Retry` / `retry` dans le code et les journaux | chapitre 10 |
| 2 | Variables `{{ }}` | présentées sans préalable | exigent `tenants: [{id: default, variables: …}]`, même pour un seul client, sinon erreur au chargement | 8.1 |
| 3 | Approbation et journal | `dev` tolère un journal non durable | vrai, mais sans profil, c'est une erreur et le journal par défaut est `memory` | 9.1 |
| 4 | Expiration d'une approbation | délai de 24 h, `on_expiry: deny\|fail` | évaluée seulement quand le run est repris (`loom resume`) ; `loom approve` après l'échéance, avant reprise, est accepté | 9.3 |
| 5 | `ToolError` | mentionné sans chemin d'import | `from loom_ia.core.ports import ToolError` ; `loom_ia.tools` échoue | 10.1, 12.1 |
| 6 | `$ref` | non précisé | transmet le résultat structuré : le champ cible doit être `type: object` | 12.2 |
| 7 | Outils MCP irréversibles | (à connaître) | exécutés sans validation tant que `approval: always` n'est pas déclaré | 13.2 |
| 8 | Juge échantillonné en `prod` | juge de même modèle refusé | un juge bloquant avec `sample` < 1 est aussi refusé | 14.2 |
| 9 | Juge limité à un profil | non précisé | silencieusement ignoré sans profil actif (`other_profile`) | 14.2 |
| 10 | `loom approve --no-wait` | option de la CLI | dans l'essai, le run s'est tout de même terminé dans la commande ; non élucidé, option non utilisée dans ce guide | 9.1 |

Non exécuté ici : les modèles réels (Anthropic, OpenAI) et les serveurs MCP distants en `http`, faute de clés d'API et de serveur distant. Tous les exemples de ce niveau ont été exécutés avec des modèles `sdk: fake` et un serveur MCP `stdio` local. Les journaux Postgres, la file RabbitMQ, le bus Redis et l'isolation Firecracker relèvent du niveau suivant.

## Et ensuite

Votre agent se souvient, s'adapte à son environnement, demande l'accord d'un humain, respecte des règles, rend une réponse bien formée, délègue et se fait contrôler. Il tourne encore sur votre poste, pour un seul client.

Le niveau 3, [`03-production.md`](03-production.md), le met en service : journaux Postgres, files et bus, plusieurs clients, API REST et clés, chiffrement, rétention, surveillance, isolation des outils. Le niveau 4, [`04-qualite-et-expert.md`](04-qualite-et-expert.md), traite de la qualité et des sujets avancés, annexes comprises.
