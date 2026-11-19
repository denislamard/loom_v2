# Analyse d'impact — reprise d'un run tenu par un process mort, file en mémoire (loom_v2)

19 novembre 2026 · Denis Lamard

Code de référence : loom_v2 2.0.22. Sources relues : `access/api.py` (`_piloted_job`, `_after_the_lease`, `approve`, `reject`, `answer`, `recover`, `_piloted`, `_holding`), `adapters/queue/asyncio_queue.py`, `engine/loop.py` (`_claim`, `_not_taken`, rendu de la concession), `engine/writer.py` (`ClaimConflict`), `access/cli/main.py` (`loom approve`, `loom worker`), loom-chat (`engine.resume_when_free`). Point relevé pendant la revue de loom-chat.

## En bref

**Verdict : défaut confirmé, correction petite et sans risque pour le chemin RabbitMQ.** Avec la file en mémoire (`storage.queue.backend` par défaut), la reprise mise en file après une décision (`approve`, `reject`), une réponse (`answer`) ou une expiration est **abandonnée** quand le run est encore tenu par un process mort : le run reste en plan jusqu'à ce que quelqu'un appelle `resume()` à la main. loom-chat compense aujourd'hui par sa propre boucle de reprise ; un autre programme bâti sur la librairie ne le fait pas.

La correction tient en deux changements : `_after_the_lease` repose le travail pour l'après-bail avec la file en mémoire aussi, et la file en mémoire laisse un travail se reposer lui-même. Prototypée sur une copie : le run reprend 3,5 s après l'approbation (bail de 3 s + marge) au lieu de rester en plan.

## Décisions prises (19/11/2026)

| # | Décision |
|---|---|
| M-1 | Corriger dans loom : `_after_the_lease` repose le travail après le bail quelle que soit la file, avec la même borne (`MAX_CLAIM_WAITS`) qu'avec un courtier. |
| M-2 | Version **2.0.23**, pour loom-ia et loom-chat (mêmes numéros). |
| M-3 | loom-chat retire, dans le même lot, sa relance devenue inutile après une décision ou une réponse différée ; il la garde pour Ctrl+R (« Reprendre »), qui appelle `resume()` directement. |

## Le défaut

Scénario reproduit (prototype, bail réglé à 3 s) :

1. un run attend une approbation ; son process est tué (`os._exit`) : sa concession (`run.claimed`, worker A) court encore ;
2. un nouveau process (worker B) approuve : `approve` écrit la décision et met une reprise en file (`Job(kind="resume")`, clé `run:<id>`) ;
3. le travail part aussitôt : `_piloted_job` → `_piloted` → `drive` → `_claim` lève `ClaimConflict` (le bail de A court) ;
4. `_piloted_job` appelle `_after_the_lease`, qui sort tout de suite : `if not self._config.storage.queue.brokered: return`.

Résultat : 15 s après l'approbation, le run est toujours `awaiting_tools`, et le restera.

### Causes

1. **La prémisse de `_after_the_lease` est fausse.** Sa docstring dit : « Avec une file en mémoire, le pilote qui tient le run est dans ce process ». Or chaque instance `Loom` tire son `worker_id` au hasard (`worker-<id>`, `api.py`), et `_claim` ne lève `ClaimConflict` que si la concession vivante est celle d'**un autre** worker. Deux pilotages dans la même instance ne se heurtent jamais : `_holding` les met à la queue. Avec la file en mémoire, un `ClaimConflict` vient donc toujours d'un autre process (ou d'une autre instance du même process) : un process mort après un plantage, ou un process vivant qui partage le journal.
2. **La file en mémoire refuserait la remise en file.** Retirer le test ne suffit pas : `_after_the_lease` est appelé depuis le travail lui-même, encore ouvert sous la clé `run:<id>` ; `AsyncioTaskQueue.submit` y voit un doublon et rend l'identifiant du travail en cours — la remise en file est perdue de la même façon.

## La correction

| Fichier | Changement |
|---|---|
| `access/api.py` | `_after_the_lease` : retirer `if not ...brokered: return` ; réécrire la docstring (un conflit vient toujours d'un autre worker ; mort, il ne renouvellera pas son bail ; vivant, on le surveille au rythme des renouvellements, borné par `MAX_CLAIM_WAITS`). |
| `adapters/queue/asyncio_queue.py` | `submit` : un travail ouvert de même clé n'est pas un doublon s'il s'agit du travail qui appelle (`waiting.task is asyncio.current_task()`). La nouvelle entrée prend la clé ; la fin de l'ancienne ne la libère pas (le `finally` de `_work` vérifie déjà que la clé est encore la sienne). Docstring du module et de `submit` à compléter. |

Le chemin RabbitMQ ne change pas : son code est le même, seul le test qui l'isolait disparaît.

## Effets, accès par accès

Une concession n'est vivante après une pause que si son pilote est mort avant de la rendre : un run en pause rend normalement sa concession (`run.claimed` à échéance immédiate, `loop.py`). Le changement ne joue donc qu'après un arrêt brutal, ou quand deux process partagent un journal.

| Accès | Avant | Après |
|---|---|---|
| Librairie (`Loom`, file en mémoire) : `approve`, `reject`, `answer` après un arrêt brutal | reprise perdue, run en plan | reprise à la fin du bail du mort (60 s par défaut, `execution.lease`) |
| Librairie : `recover()` au démarrage après un arrêt brutal | runs remis en file puis perdus tant que le bail du mort court | repris à la fin du bail |
| Expiration d'une approbation (`expire_approval`) sur un run tenu par un mort | perdue | appliquée à la fin du bail |
| `loom approve` / `loom reject` (CLI, sans `--no-wait`) | attend la reprise, qui est perdue ; affiche le run en plan | inchangé : `drain()` n'attend pas un travail différé, et la fermeture l'abandonne — pas d'attente de 60 s à la sortie ; le run reste à reprendre (`loom resume`) |
| `loom resume` (CLI), `resume()` direct, REST `POST …/resume` | `ClaimConflict` rendu à l'appelant (code 409 en REST) | inchangé |
| `loom serve` (REST) et `loom mcp`, process longs en file mémoire | décision via REST/MCP après un redémarrage : reprise perdue | reprise à la fin du bail |
| `loom worker` (RabbitMQ) | reposé après le bail | inchangé |
| loom-chat | sa boucle `resume_when_free` compense | loom reprend lui-même ; la boucle ne sert plus qu'à Ctrl+R (M-3) |

### Effets de bord

- **Process vivant qui partage le journal** (deux consoles sur le même dossier, une console et `loom serve`) : le travail est reposé à chaque fin de bail, jusqu'à `MAX_CLAIM_WAITS` (20) fois, puis abandonné avec un avertissement — comme avec un courtier. Chaque nouvel essai lit l'état du run : un run fini entre-temps est laissé tel quel (`_piloted_job` sort sur `state.finished`).
- **Places de travail** (`storage.queue.max_running`) : un travail différé dort avant de prendre sa place, il n'en occupe aucune pendant l'attente.
- **Fermeture** : `aclose()` abandonne aussitôt les travaux différés (comportement actuel de la file) ; rien n'est perdu, le journal fait foi et `recover()` ou `resume()` reprend le run.
- **Exception de dédoublonnage** : elle ne vaut que pour la tâche asyncio du travail lui-même. Les outils et les sous-agents tournent dans la tâche de `drive`, pas dans celle de la file : un `approve` lancé depuis un outil reste dédoublonné. Aujourd'hui, seul `_after_the_lease` soumet depuis la tâche du travail une clé identique à la sienne (`_schedule` et `_expiring` utilisent `compaction:…` et `expire:…`).
- **Journalisation** : chaque conflit écrit déjà une ligne INFO ; elles apparaîtront aussi en file mémoire.
- **Sécurité, droits** : aucun changement — la reprise est celle que la décision a déjà demandée, menée par la même instance.

## Essais

- Prototype (copie de loom_v2 2.0.22 + correction) : scénario du défaut, run repris en 3,5 s.
- Tests unitaires de la copie corrigée : tous passent (les deux échecs observés viennent de fichiers absents de la copie : `docs/style_format.md`, `dependances-recentes.yml`). Tests d'intégration (RabbitMQ, Postgres, Redis) : par la CI.
- À ajouter :
  - `tests/unit` : un run tenu par un worker mort (concession écrite à la main, bail court, comme `_held` dans `test_worker_rabbitmq.py`), approuvé par une instance en file mémoire → mené au bout après le bail, pas avant ;
  - même scénario avec `answer` et avec `recover()` ;
  - `AsyncioTaskQueue` : un travail qui se repose lui-même sous sa clé est relancé ; un autre travail de même clé reste dédoublonné ;
  - un travail différé ne retient pas `aclose()`.
- loom-chat : le test de redémarrage (`test_restart_reopens_pending_approval`) passe sans la relance après décision.

## Lots

| Lot | Contenu |
|---|---|
| 1 | loom : `_after_the_lease`, `AsyncioTaskQueue.submit`, docstrings, tests unitaires ; version 2.0.23 (`pyproject.toml`, `CHANGELOG.md`). |
| 2 | loom-chat 2.0.23 : `_continue` ne relance plus après une décision ou une réponse différée (il suit le run) ; Ctrl+R garde `resume_when_free` ; dépendance `loom-ia[anthropic]==2.0.23`. |
