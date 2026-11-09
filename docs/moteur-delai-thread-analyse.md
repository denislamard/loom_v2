# Analyse d'impact — un outil qui dépasse son délai continue de travailler (B-2, loom_v2)

9 novembre 2026 · Denis Lamard

> **Décisions prises le 09/11/2026** : D-1, D-2, D-3, D-7, D-8, D-9 : (a). D-4 : (b), un pool par instance. D-5 : (c), attendre les outils au plus `grace` à la fermeture, puis s'arrêter sans eux. D-6 : (b), dans ce lot, avec l'étude du SDK MCP. D-10 : oui. D-11 : `execution.tools.threads: 16`, `execution.tools.grace: 30`. D-12 : la version 2.0.20. D-13, non tranchée : (b), ma recommandation, appliquée. B-5 n'est pas dans ce lot. Mise en œuvre : § Mise en œuvre, en fin de document.

## En bref

L'analyse de l'ordre des appels a relevé B-2 : un outil synchrone qui dépasse son délai continue de travailler. Le moteur rend « Délai dépassé : pas de réponse en 0.2 s » au modèle, mais Python ne sait pas arrêter un thread, et l'outil va au bout de son travail : le fichier s'écrit, le courriel part, plus tard. L'analyse complète montre que le problème est plus large, et plus grave, que ce qu'on en savait :

1. **Le modèle est trompé.** Il lit « pas de réponse », alors que l'effet a lieu. S'il relance, l'effet a lieu deux fois : deux courriels à la même cliente dans l'essai.
2. **L'ordre des appels ne tient plus.** La lecture qui suit dans le tour part pendant que le thread écrit encore, et rend l'ancienne version.
3. **Un serveur MCP fait pareil.** Le client MCP n'envoie pas d'annulation au serveur quand loom abandonne l'appel : l'outil du serveur, asynchrone ou non, va au bout et écrit une seconde plus tard.
4. **Des threads bloqués paralysent le process.** Les outils et le journal JSONL partagent le pool de threads d'asyncio : six appels bloqués suffisent, sur une machine à deux cœurs, pour que le journal n'écrive plus rien pendant 5 s. Un autre run du même process (`loom serve`) passe de 0,02 s à 4,7 s.
5. **Le process ne s'arrête pas.** `loom run` finit son run en 1 s, mais la commande rend la main après 21 s, le temps que le thread finisse.

**Verdict : la correction tient en trois pièces, prototypées et testées.** Un pool de threads à part pour les outils (`loom-outil`), un suivi du travail de chaque appel, et une règle : **un délai dépassé sur un appel à effet est un état inconnu**, comme à la reprise d'un appel interrompu (`on_unknown`). Tant que ce travail tourne, la chaîne attend sa fin réelle, au plus 30 s ; au-delà, les appels suivants du tour reviennent au modèle sans être lancés. Les points 1, 2 et 4 sont corrigés. Pour MCP (point 3), le modèle est prévenu, mais le serveur n'est pas arrêté (D-6). Le point 5 demande une décision (D-5).

| Point | Constat | Preuve |
| --- | --- | --- |
| Écriture après le délai | Un outil synchrone de 0,5 s, délai 0,2 s : « Délai dépassé » au modèle, le courriel part 0,3 s plus tard | Démontré : `loom run` sur `741f887` (§ Reproduire) |
| Effet en double | Le modèle relance après « Délai dépassé » : deux courriels | Démontré avec un modèle simulé qui relance ; qu'un vrai modèle relance est à vérifier |
| Ordre des appels | La lecture qui suit l'écriture en retard rend « aucun » au lieu du courriel envoyé | Démontré sur `741f887` ; corrigé par le prototype (la lecture attend la fin réelle) |
| Pool partagé | 6 threads bloqués : les `tool.completed` s'écrivent 5 s plus tard ; un run voisin passe de 0,02 s à 4,73 s | Démontré ; avec le prototype, 0,01 s |
| Fin du process | `loom run` rend la main après 21,2 s pour un run fini en 1 s | Démontré ; inchangé par le prototype (D-5) |
| MCP | Outil MCP asynchrone et synchrone, délai 0,3 s : les deux écrivent 1 s plus tard | Démontré (serveur FastMCP en stdio) ; le prototype dit l'état inconnu, il n'arrête pas le serveur (D-6) |
| Outil asynchrone | Annulé à son prochain `await`, il n'écrit pas | Démontré ; mais rien ne dit où il en était : même règle d'état inconnu |
| Non-régression | Tous les tests passent, un seul change d'attente ; 10 essais nouveaux | Démontré (§ Mesures) |
| Coût | `in_thread` : 30 µs par appel, contre 32 µs pour `asyncio.to_thread` | Démontré (2 000 appels, médiane de 5 séries) |

**Trois points demandent une décision avant tout :**

1. **La règle d'état inconnu (D-2).** Un délai dépassé sur un appel à effet devient un état inconnu, pour tous les outils : Python, fournis, MCP. ⚠ Un outil déclaré `on_unknown: pause` (dont `loom.sql` en écriture) met alors le run en pause à son délai, au lieu de rendre une erreur.
2. **L'attente de la chaîne (D-3)** : 30 s par défaut, réglable.
3. **La fin du process (D-5)** : attendre les threads en le disant, ou sortir après un délai.

## Périmètre et état de référence

L'analyse porte sur ton commit `741f887` (« call order tools », égal à `origin/main`), qui contient le lot de l'ordre des appels. J'ai vérifié que ce commit est identique, fichier par fichier, à ce que j'avais déposé. Rien n'a été écrit dans `/home/denis/dev/loom_v2`, à part ce document : ni commit, ni push.

Les essais ont tourné dans l'espace de travail cloud de Claude, sur un clone de ce commit : Python 3.14.6, `uv sync --all-extras --group types`, et un environnement sans extra pour la passe « noyau seul ». La machine a 2 cœurs : le pool d'asyncio y a 6 threads (`min(32, cœurs + 4)`). Sur ton poste, il en a sans doute davantage (§ À vérifier, point 2), et il faut alors plus de threads bloqués pour le saturer ; le mécanisme est le même.

Comme pour l'ordre des appels, **le prototype est une implémentation** : complète pour les outils Python et les outils fournis qui écrivent, testée, et qui passe pyright strict, ruff et les contrats d'import.

## Reproduire

Le dossier d'essai déclare, avec un modèle simulé (`sdk: fake`) par scénario :

```python
@tool(side_effects="irreversible", timeout=0.2)
def envoyer(destinataire: str) -> str:
    """Envoie un courriel (0,5 s : il dépasse son délai de 0,2 s)."""
    time.sleep(0.5)
    with (ICI / "envoyes.txt").open("a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%H:%M:%S')} courriel à {destinataire}\n")
    return f"envoyé à {destinataire}"
```

plus `lire_envoyes` (sans effet), `bloque` (5 s, délai 0,1 s), `long` (20 s, délai 1 s), et un serveur MCP FastMCP en stdio avec deux outils qui écrivent après 1 s, l'un asynchrone, l'autre synchrone (délai 0,3 s par la config, `tools:` du serveur).

| Essai | Sur `741f887` | Avec le prototype |
| --- | --- | --- |
| **E1** — `envoyer` puis `lire_envoyes` dans la même réponse | `envoyer` : « Délai dépassé : pas de réponse en 0.2 s. » ; `lire_envoyes` part aussitôt et rend « aucun » ; le courriel part 0,3 s plus tard | `envoyer` : « Délai dépassé : pas de réponse en 0.2 s, et l'outil n'a pas pu être arrêté. Son effet est d'état inconnu… » ; `lire_envoyes` attend la fin du thread et rend le courriel ; un avertissement dit la fin tardive |
| **E2** — le modèle relance `envoyer` au tour suivant | Deux courriels dans `envoyes.txt` | Deux courriels aussi : le modèle simulé relance quoi qu'on lui dise. Le message dit maintenant de vérifier avant de relancer (à éprouver avec un vrai modèle) |
| **E3** — six `bloque` dans une réponse | Le premier `tool.completed` s'écrit à 0,12 s, les cinq autres à 5,02 s : le journal JSONL attend un thread libre | Les six s'écrivent à 0,1 s |
| **E3b** — un run court pendant qu'un autre a six threads bloqués, dans le même `Loom` | 4,73 s (0,02 s seul) | 0,01 s |
| **E4** — `long` (20 s, délai 1 s) par `loom run` | Run fini en 1 s ; la commande rend la main à 21,2 s | Inchangé (E3 : 6,0 s pour un run de 0,1 s) |
| **E6** — outil asynchrone, délai 0,1 s, écrit après 0,3 s | Annulé à son `await` : le fichier n'est jamais écrit | Même chose ; le message dit l'état inconnu, puisque rien ne dit où l'outil en était |
| **E7** — deux outils MCP, délai 0,3 s, écrivent après 1 s | Les deux fichiers s'écrivent 1 s après, le run déjà fini | Même chose : le serveur n'est pas prévenu (D-6) ; le modèle lit l'état inconnu |

## Analyse fonctionnelle

### Ce qui change pour le modèle

Un appel **sans effet** qui dépasse son délai garde son message : « Délai dépassé : pas de réponse en X s. » Son travail peut continuer en arrière-plan, sans conséquence.

Un appel **à effet** (le même critère que pour l'ordre : l'évaluation de l'appel, sinon `side_effects`) qui dépasse son délai est **d'état inconnu**, quel que soit l'outil :

```
Délai dépassé : pas de réponse en 0.2 s, et l'outil n'a pas pu être arrêté. Son effet
est d'état inconnu : il a peut-être eu lieu, ou aura lieu plus tard. Il n'a pas été
relancé ; vérifie avant de le rappeler.
```

L'outil dit ce qu'il en advient, comme pour un appel interrompu à la reprise (`on_unknown`) : `error`, le message ci-dessus revient au modèle ; `pause`, le run attend qu'un humain vérifie et tranche, avec ce message pour motif.

### Ce qui change pour les appels suivants du tour

Si le travail de l'appel à effet tourne encore, la chaîne attend sa fin réelle avant de lancer le segment suivant, **au plus `execution.tools.grace` secondes** (30 par défaut). Si le travail finit à temps, la suite part normalement : la lecture voit ce que l'outil a écrit. Sinon, les appels suivants du tour reviennent au modèle **sans être lancés**, et sans `tool.called` :

```
Non exécuté : l'appel à envoyer, qui le précède dans ta réponse, a dépassé son délai et
travaille encore. Cet appel n'est pas parti ; refais-le une fois l'effet de envoyer vérifié.
```

C'est ce que la documentation d'Anthropic recommande pour un appel d'une suite séquentielle qui n'est pas exécuté : un `tool_result` en erreur qui dit pourquoi.

### Ce qui change pour l'exploitant

- **Les outils ont leur pool de threads** (`loom-outil`, 16 threads par défaut, `execution.tools.threads`). Le journal JSONL, le stockage local des fichiers et les autres runs du process gardent le pool d'asyncio pour eux : des outils bloqués ne les arrêtent plus.
- **Une fin tardive se dit dans les logs**, en `WARNING` : « Outil envoyer : travail fini 0.3 s après que l'appel a rendu la main (effet produit si l'outil en a un) ; son résultat est perdu ».
- **Un pool plein fait attendre** les appels suivants : ils patientent dans la file du pool, et leur propre délai court pendant ce temps. Seize outils bloqués en même temps feraient donc dépasser les délais des autres outils synchrones, sans toucher au journal ni aux appels de modèle.

### La configuration

```yaml
execution:
  tools:
    timeout: 30
    threads: 16   # pool des outils synchrones, un par process
    grace: 30     # attente au plus d'un effet en retard avant le reste du tour ; null : sans borne
```

### Ce qui ne change pas

- **Le thread n'est pas arrêté.** Rien ne peut le faire en Python. L'effet a lieu, plus tard ; la correction le dit et en tient compte, elle ne l'empêche pas.
- **La fin du process** attend toujours les threads (D-5).
- **Le serveur MCP** n'est pas prévenu (D-6).

## Analyse technique

### État des lieux

**Où le délai s'applique** (`engine/executor.py`) :

- `_invoked` entoure `tool.invoke` d'un `asyncio.timeout(limit)` : le délai de l'outil (`spec.timeout`), sinon celui de l'exécuteur (`execution.tools.timeout`, 30 s). À l'expiration, la tâche est annulée et l'appel rend `ToolOutput.error("Délai dépassé : pas de réponse en X s.")`, un résultat d'erreur ordinaire.
- `_delegated` fait de même pour un rôle (son `timeout`) ; un sous-agent n'a pas de délai d'outil.
- Le délai du run (A6) et `cancel` annulent la tâche du lot entier ; les tâches des outils sont annulées dans le `finally` du lot.

**Ce que devient le travail annulé** :

| Outil | À l'annulation | Ce qui continue |
| --- | --- | --- |
| Python asynchrone (`async def`) | `CancelledError` au prochain `await` | Rien ; mais un effet a pu partir avant (une requête HTTP envoyée, dont on attendait la réponse) |
| Python synchrone (`def`) | `FunctionTool.invoke` attend `asyncio.to_thread` ; l'attente est annulée | **Le thread, jusqu'au bout** |
| `loom.word`, `loom.pdf` (`_document`), `loom.local_files`, `loom.excel`, `loom.fetch_page` | Même chose : `asyncio.to_thread` | **Le thread** |
| `loom.sql` (SQLite) | `asyncio.shield`, puis `connection.interrupt()` | Le thread s'arrête à la prochaine instruction ; la transaction est annulée |
| `loom.memory` | Un `threading.Event` que le calcul regarde entre ses lots | Le thread s'arrête au lot suivant |
| Outil MCP | L'attente de la réponse est annulée ; le SDK n'envoie pas `notifications/cancelled` | **Le serveur, jusqu'au bout** (E7) |
| Forge (Firecracker) | Son délai loom est calculé au-dessus de celui d'`execd` dans la VM (`_timeout`) | Hors du champ de cet essai |

`loom.sql` et `loom.memory` ont donc déjà une **annulation coopérative**. Les autres outils fournis qui écrivent n'en ont pas.

**Le pool partagé.** `asyncio.to_thread` utilise le pool par défaut de la boucle : `min(32, cœurs + 4)` threads. Les outils y côtoient le journal JSONL (`adapters/stores/jsonl.py`, lecture et écriture), le stockage local des artefacts (`adapters/artifacts/local.py`), l'export des journaux. Un thread d'outil bloqué occupe une place jusqu'à sa fin ; quand toutes sont prises, chaque écriture au journal attend (E3), et tous les runs du process avec elle (E3b). Un journal Postgres (asyncpg) n'y passe pas, mais le stockage local des artefacts, si.

**La fin du process.** `asyncio.run` attend le pool par défaut à la fermeture (`shutdown_default_executor`, jusqu'à 300 s), et l'interpréteur attend tout thread de `ThreadPoolExecutor` à sa sortie. D'où les 21 s de E4.

**L'idempotence.** `@idempotent` mémorise l'effet après `await inner.invoke(...)` : annulé par le délai, il ne mémorise rien, et la réservation reste (« un délai n'est pas un échec de l'outil », `tools/idempotent.py`). Le thread, lui, produit l'effet. Avec une clé d'appel (le défaut), la relance du modèle est un autre appel, donc une autre clé : l'effet a lieu deux fois. Seule une clé métier (`key=…`, magasin partagé) l'en empêcherait.

**La reprise.** Le `tool.completed` en erreur est au journal : un appel en délai dépassé n'est jamais relancé par la reprise. Le risque ne vient que du modèle.

### Les options

| Option | Justesse | Coût | Complexité | Effet sur le reste |
| --- | --- | --- | --- | --- |
| **(a) Pool à part, suivi de l'appel, état inconnu, attente bornée** (le prototype) | Le modèle sait ; l'ordre tient tant que le thread finit dans le délai de grâce ; le journal et les autres runs ne sont plus bloqués | Rien sur un appel normal ; jusqu'à `grace` s d'attente après un délai sur un effet | ~130 lignes dans `core/tool_threads.py`, ~90 dans `executor.py`, une ligne par outil fourni | ⚠ `on_unknown: pause` met le run en pause au délai ; ordre du journal (le `tool.completed` d'un effet en retard vient en fin de lot) |
| (b) Le message seul (« l'outil continue peut-être ») | Le modèle est prévenu ; rien d'autre | Nul | ~5 lignes | Ni l'ordre, ni le pool, ni le process |
| (c) Annulation coopérative : `ToolContext.cancelled`, un `threading.Event` levé au délai, que l'outil regarde | Le thread s'arrête là où l'outil le permet : jamais au milieu d'un appel système, ni d'une bibliothèque qui ne le regarde pas | Nul | Additif ; chaque outil choisit de s'en servir | À ajouter à (a), pas à la place |
| (d) Un sous-process par appel d'outil synchrone, tué au délai | Le seul arrêt vrai | Démarrage d'un process par appel ; arguments et résultat à sérialiser ; un outil qui garde un état (connexion, cache) le perd | Élevée | Un outil tué au milieu d'une écriture non atomique laisse un fichier abîmé |
| (e) Pas de délai pour un outil synchrone | L'effet et la réponse arrivent ensemble | Un outil bloqué bloque le run | Nulle | Retire une protection |
| (f) Attendre la fin du thread, sans borne, avant de répondre | Le modèle a le vrai résultat | Le délai ne sert plus à rien | Faible | Revient à (e) |

Le prototype retient (a). (c) vient par-dessus, pour les outils qui peuvent s'arrêter proprement (D-8). (d) est hors de proportion pour des outils qui écrivent des fichiers de façon atomique.

### Ce que montre le prototype

**`core/tool_threads.py` (nouveau)**, bibliothèque standard seulement :

- `in_thread(fn, *args, **kwargs)` : comme `asyncio.to_thread` (contexte compris), mais dans le pool `loom-outil` ;
- `tracking(name)` : posé par le moteur autour de l'appel, il note chaque travail lancé par `in_thread` pendant l'appel (une `ContextVar`) ;
- `CallThreads.abandon()` : quand l'appel rend la main sans son travail, chaque travail encore en cours dira sa fin tardive dans les logs ;
- `configure(threads)`, `pool()`, `shutdown()` : le pool, un par process.

**`engine/executor.py`** :

- `_invoked(..., effects=…)` : un délai dépassé sur un appel à effet lève `UnknownEffect(LATE_EFFECT)`, et la règle existante s'applique (`on_unknown`, après le lot) ;
- `_execute` pose le suivi ; si le travail tourne encore après le délai, il l'abandonne et le note dans `lingering` ; après une annulation du lot, il l'abandonne aussi ;
- avant chaque segment, `_lingering` attend le travail en cours des appels à effet, au plus `grace` ; au-delà, les appels restants reçoivent `NOT_RUN`, sans `tool.called`.

**Outils** : `FunctionTool` (outils Python synchrones), `_document` (`word`, `pdf`), `word`, `local_files`, `excel` et `fetch_page` appellent `in_thread` au lieu d'`asyncio.to_thread`. `loom.sql` et `loom.memory` ne sont pas encore passés (D-7).

**Config** : `execution.tools.threads` (16) et `execution.tools.grace` (30 s, `null` sans borne) ; `Loom` configure le pool à son ouverture.

| Essai (`tests/unit/test_tool_threads.py`) | Ce qu'il montre |
| --- | --- |
| `test_a_sync_tool_runs_in_the_tools_pool` | Le thread d'un outil s'appelle `loom-outil…` |
| `test_a_write_past_its_deadline_is_of_unknown_effect_and_the_read_waits_for_it` | `LATE_EFFECT` au modèle ; la lecture attend la fin du thread et lit « v2 » |
| `test_past_the_grace_the_next_calls_are_returned_without_running` | Au-delà de la grâce : `NOT_RUN` pour les deux lectures, qui ne partent pas |
| `test_a_late_write_a_human_must_check_pauses_the_run` | `on_unknown: pause` : une demande d'approbation avec `LATE_EFFECT` pour motif ; la lecture attend |
| `test_a_read_past_its_deadline_keeps_its_message_and_holds_nothing` | Un appel sans effet : message d'avant, et rien n'attend |
| `test_an_async_write_past_its_deadline_is_of_unknown_effect_too` | Outil asynchrone : il n'a pas écrit, mais le modèle lit l'état inconnu |
| `test_blocked_tool_threads_leave_the_asyncio_pool_free` | 40 threads d'outils bloqués : `asyncio.to_thread` répond en moins de 0,5 s |
| `test_a_late_end_is_logged` | La fin tardive donne un `WARNING` |
| `test_cancelling_the_batch_leaves_the_thread_to_its_end` | Une annulation du lot : le thread va au bout, et le dit |
| `test_the_pool_size_and_the_grace_come_from_the_config` | `threads` et `grace` de `loom.yaml` arrivent au pool et à l'exécuteur |

### Les pièges trouvés

1. **Un outil qui demande `on_unknown: pause` avec un journal en mémoire.** La garde du journal durable (`runtime/wiring.py`, `_check_durable_journal`) compte les outils à approbation et les politiques qui mettent en pause, mais pas `on_unknown: pause`. Jusqu'ici, un état inconnu ne venait que d'une reprise après un arrêt, impossible avec un journal en mémoire. Avec la règle de D-2, il vient d'un simple délai : démontré, un run avec `storage.events.backend: memory` passe en `paused` sur un outil `on_unknown: pause` qui dépasse son délai, et `loom validate` ne dit rien. La garde doit compter ces outils, quand ils ont un délai.
2. **Le pool est celui du process.** Deux instances `Loom` dans le même process partagent le pool, et la dernière ouverte fixe sa taille. C'est le prototype ; D-4 propose un pool par instance.
3. **Le `tool.completed` d'un effet en retard vient en fin de lot.** Comme tout état inconnu, il est décidé après le lot (`inconnus`) : dans le journal, il suit les appels des segments suivants. Le modèle, lui, le reçoit à sa place (`in_call_order`).

### Modifications prévues

| Fichier | Changement | Estimation |
| --- | --- | --- |
| `core/tool_threads.py` (nouveau) | Pool, `in_thread`, suivi, fin tardive | 130 (fait) |
| `engine/executor.py` | État inconnu au délai d'un appel à effet, suivi, attente bornée, `NOT_RUN`, `grace` | +87, −9 (fait) |
| `config/models.py`, `runtime/wiring.py`, `access/api.py` | `threads`, `grace` ; configuration du pool | +11 (fait) ; ~20 de plus pour un pool par instance (D-4) |
| `tools/python.py`, `tools_builtin/_document`, `word`, `local_files`, `excel`, `fetch_page` | `in_thread` | +15, −14 (fait) |
| `tools_builtin/sql/sqlite.py`, `tools_builtin/memory/*` | `in_thread`, en gardant leur annulation coopérative (D-7) | ~30 |
| `runtime/wiring.py` (`_check_durable_journal`) | Compter `on_unknown: pause` | ~10 |
| `access/api.py` (`aclose`) | La fin du process (D-5) | ~20 |
| `core/ports/tool.py`, `executor.py` | `ToolContext.cancelled` (D-8) | ~25 |
| `adapters/mcp/source.py` | `notifications/cancelled` au délai (D-6) | ~40, à étudier dans le SDK |
| `tests/unit/test_tool_threads.py` (nouveau) | Les 10 essais, plus ceux des décisions | ~250 (fait : 200) |
| `tests/unit/test_call_order.py` | Le délai dépassé d'une écriture : `LATE_EFFECT`, en fin de lot | 5 (fait) |
| `docs/configuration.md`, `conception.md`, docstring de `tools/python.py`, `loom_testing.md` (l. 331) | La règle, les deux clés ; la note « Reste à noter » devient caduque | ~20 |
| `CHANGELOG.md` | L'entrée, avec ⚠ | ~10 |

## Mesures

**Le coût de `in_thread`** : 30 µs par appel, contre 32 µs pour `asyncio.to_thread` (2 000 appels d'une fonction vide, médiane de 5 séries). Rien ne change pour un appel qui finit dans son délai.

**Les essais** : tableau du § Reproduire.

**Tests** (prototype sur `741f887`) :

| Passe | `741f887` | Prototype |
| --- | --- | --- |
| Tous les extras | 4 481 réussis, 203 sautés, 1 échec | 4 491 réussis (+10), 203 sautés, 1 échec |
| Noyau seul | 3 472 réussis, 416 sautés | 3 482 réussis (+10), 416 sautés |

L'échec est le même des deux côtés : l'essai de la forge Firecracker déjà relevé dans l'analyse de l'ordre des appels. Un test existant change d'attente : `test_call_order.py::test_a_timed_out_write_does_not_stop_the_chain`, dont l'écriture en délai dépassé rend maintenant `LATE_EFFECT`, en fin de lot (piège 3). pyright strict : 0 erreur ; `ruff check .` et `ruff format --check .` : rien à redire ; `lint-imports` : 6 contrats tenus sur 6.

**Journaux** : le schéma des événements ne change pas. Un délai dépassé reste un `tool.completed` en erreur, ou devient une demande d'approbation (`on_unknown: pause`) ; un appel rendu sans être lancé est un `tool.completed` en erreur sans `tool.called`, comme un appel refusé. Un journal d'avant se rejoue à l'identique : son résultat de délai est lu au journal, pas recalculé.

## Bugs existants trouvés en route

- **B-3 — Des threads d'outils bloqués arrêtent le journal et les autres runs du process.** Le pool d'asyncio est partagé entre les outils synchrones et les écritures du journal JSONL et des fichiers. Démontré : six appels bloqués retardent de 5 s les `tool.completed` du même lot, et un run voisin du même `Loom` passe de 0,02 s à 4,73 s. Le délai de l'outil ne protège pas : il libère le run, pas le thread. Corrigé par le pool à part.
- **B-4 — Un outil MCP continue après son délai.** Le SDK MCP n'envoie pas `notifications/cancelled` quand l'attente est annulée ; le serveur va au bout, et écrit après que le modèle a lu « Délai dépassé ». Démontré avec FastMCP (outils asynchrone et synchrone). Le prototype le dit au modèle (état inconnu) ; arrêter le serveur relève de D-6.
- **B-5 — `loom validate` avertit pour `loom.current_datetime` et `loom.fetch_page`** (lot de l'ordre des appels, D-6, dans `741f887`). Ces deux outils fournis sont écrits avec `@tool` sans `side_effects` : l'avertissement prévu pour les outils de l'utilisateur les touche aussi, et tout agent qui s'en sert le reçoit (démontré sur l'agent `shaper` du POC). Les tests ne l'ont pas vu : aucun ne monte un outil fourni écrit avec `@tool`. Correctif : `@tool(side_effects="none")` sur les deux (deux lignes), et `is_builtin` exclu du contrôle de `loom validate` par prudence, avec un test sur `loom.current_datetime`. Indépendant de B-2 : il peut passer avant.
- **B-2 lui-même** était connu (`docs/loom_testing.md`, l. 331 : « le délai rend la main au run mais Python ne peut pas interrompre le thread ») ; ses conséquences ne l'étaient pas : l'effet en double, l'ordre, le pool, la fin du process.

## Effets de bord

- **⚠ `on_unknown: pause`** : un tel outil met le run en pause à son délai. `loom.sql` le déclare pour ses écritures (`tools_builtin/sql/tool.py`) : une requête d'écriture qui dépasse le délai de loom (et non le `statement_timeout` de l'outil, qui l'arrête avant, en erreur) attend désormais un humain.
- **Le message de délai** change pour un appel à effet : un run nouveau envoie une autre requête au modèle. Un journal d'avant se rejoue à l'identique (le résultat est lu au journal).
- **Le journal** : le `tool.completed` d'un effet en retard vient après le lot ; un appel non lancé a un `tool.completed` sans `tool.called`.
- **Les évaluations et le rejeu en variante** : un outil à effet n'y est pas exécuté (doublé ou refusé) ; inchangés.
- **Les outils d'un utilisateur** qui appellent eux-mêmes `asyncio.to_thread` dans une fonction `async` ne sont ni dans le pool des outils ni suivis : ils gardent le comportement d'avant, état inconnu mis à part. `in_thread` est public pour qu'ils l'adoptent.
- **Les contrats d'import** : `core/tool_threads.py` ne dépend que de la bibliothèque standard ; le moteur et les outils l'importent depuis le noyau.

## Plan de non-régression

- **Un appel qui finit dans son délai** : même résultat, même journal (toute la suite existante passe).
- **Un appel sans effet en délai dépassé** : même message qu'avant (test).
- **Le pool d'asyncio reste libre** quand des outils sont bloqués (test).
- **Un journal d'avant** avec un délai dépassé se rejoue à l'identique (à ajouter au corpus `tests/regression/`).
- **Les essais E1, E3, E3b, E7** deviennent des tests de bout en bout (`Loom`, journal JSONL, serveur MCP de test).

## Vérifié sur le POC shaper

Le `wireframe` du POC (`tools/Wireframe.py`, copié dans l'espace cloud avec sa config, sans le `.env`) a tourné avec Chromium sans interface et Playwright 1.63, sur cinq écrans :

| Délai | Résultat | PNG écrits | Chromium |
| --- | --- | --- | --- |
| 60 s (normal) | Les cinq maquettes, en 1,9 s | 5 | Fermé (`browser.close()` du `finally`) |
| 0,8 s (dépassé) | Avec le prototype : `LATE_EFFECT`, à 0,9 s ; sur `741f887` : « Délai dépassé : pas de réponse en 0.8 s. » | **1 sur 5** | Fermé aussi : l'annulation passe par le `finally` et par la sortie d'`async_playwright` |

Deux enseignements :

- **L'effet partiel est réel.** `wireframe` est asynchrone, donc annulé à son `await` : il n'écrit plus rien ensuite, mais la première maquette était déjà sur le disque. Sur `741f887`, le modèle lit « pas de réponse » alors qu'une image existe. L'état inconnu est le bon diagnostic.
- **Le message du prototype est faux pour un outil asynchrone.** « L'outil n'a pas pu être arrêté » ne vaut que pour un thread qui tourne encore. Un outil asynchrone, lui, a été arrêté en cours de route. Il faut deux textes : l'un quand le travail continue (thread suivi), l'autre quand l'outil a été arrêté (« il a pu produire une partie de son effet ») (D-13).

`loom validate` sur la config du POC, avec le code de `741f887` : `wireframe` déclare bien ses effets, mais l'agent `shaper` reçoit « outil current_datetime : side_effects non déclaré » (B-5).

## Décisions

| N° | Question | Proposition |
| --- | --- | --- |
| D-1 | Quelle option ? | (a) pool à part, suivi, état inconnu, attente bornée (le prototype) ; (b) le message seul ; (d) un sous-process par appel. **Je recommande (a)**, et l'annulation coopérative (D-8) par-dessus |
| D-2 | Un délai dépassé sur un appel à effet | (a) est un état inconnu (`on_unknown`), pour tous les outils, Python, fournis et MCP ; (b) seulement pour les outils en thread ; (c) reste une erreur ordinaire, avec un message qui le dit. **Je recommande (a)**, ⚠ au CHANGELOG pour `on_unknown: pause` : un outil asynchrone ou MCP peut aussi avoir produit son effet |
| D-3 | L'attente de la chaîne après un effet en retard | (a) au plus `execution.tools.grace` s (30 par défaut, `null` sans borne), puis les appels suivants reviennent au modèle sans être lancés (le prototype) ; (b) pas d'attente : les appels suivants reviennent tout de suite sans être lancés ; (c) pas d'attente, ils partent. **Je recommande (a)** |
| D-4 | Le pool des outils | (a) un par process, la dernière instance `Loom` fixe sa taille (le prototype) ; (b) un par instance `Loom`, passé à l'exécuteur et à `in_thread` par le suivi de l'appel. **Je recommande (b)** : deux instances d'un même process (un test, une application qui en ouvre deux) ne se gênent plus |
| D-5 | La fin du process | (a) attendre les threads sans rien dire (aujourd'hui) ; (b) les attendre, en disant à la fermeture de `Loom` lesquels on attend (« attend la fin de 1 outil en arrière-plan : long, depuis 3 s ») ; (c) les attendre au plus `grace`, puis sortir (threads démons) en le disant. **Je recommande (b)** : (c) couperait un outil au milieu d'une écriture qui n'est pas atomique |
| D-6 | MCP | (a) l'état inconnu seulement (le prototype) ; (b) en plus, envoyer `notifications/cancelled` au serveur au délai, pour qu'un serveur qui la respecte s'arrête. **Je recommande (b), dans un lot à part** : le SDK ne donne pas l'identifiant de la requête de `call_tool`, il faut l'étudier |
| D-7 | `loom.sql` et `loom.memory` | (a) passer à `in_thread`, en gardant leur annulation coopérative ; (b) les laisser sur le pool d'asyncio. **Je recommande (a)** : un calcul d'embeddings bloqué ne doit pas non plus arrêter le journal |
| D-8 | Annulation coopérative | (a) `ToolContext.cancelled`, un `threading.Event` levé au délai et à l'annulation, que l'outil regarde quand il le peut ; (b) rien. **Je recommande (a)**, sur le modèle de `loom.memory` |
| D-9 | La fin tardive | (a) un `WARNING` dans les logs (le prototype) ; (b) un événement au journal (`tool.late`), avec la durée : le schéma change. **Je recommande (a)** |
| D-10 | Le journal durable | Compter `on_unknown: pause` dans `_check_durable_journal` (piège 1). **Je recommande de le faire dans ce lot** |
| D-11 | Noms et défauts des clés | `execution.tools.threads: 16` et `execution.tools.grace: 30` (le prototype). À toi de voir |
| D-12 | La version | 2.0.20 (l'actuelle, non publiée) ou 2.1.0 |
| D-13 | Le texte de l'état inconnu après un délai | (a) un seul texte (le prototype, faux pour un outil asynchrone) ; (b) deux : « n'a pas pu être arrêté » quand un thread suivi tourne encore, « arrêté en cours de route, il a pu produire une partie de son effet » sinon. **Je recommande (b)** |

## À vérifier sur ton poste

1. **Un vrai modèle devant `LATE_EFFECT`** : relance-t-il quand même ? Avec Haiku, un outil `envoyer` de 0,5 s et un délai de 0,2 s, et une demande qui incite à « réessayer si ça échoue ».
2. **Le pool d'asyncio de ta machine** : `python -c "import os; print(min(32, (os.cpu_count() or 1) + 4))"`. C'est le nombre de threads bloqués qui paralyse le journal aujourd'hui ; le pool des outils (16) vaut-il mieux ?
3. **`loom.sql` en écriture** (l'exemple `examples/agent_sql`, par exemple) : une requête qui dépasse le délai de loom (au-delà de son `statement_timeout`) met désormais le run en pause.
4. **Tes serveurs MCP** (`loom-memory`, `loom-plan`) : respectent-ils `notifications/cancelled` ? C'est ce qui décide de l'intérêt de D-6 (b).

## Mise en œuvre

### 9 novembre 2026 : le lot entier

Appliqué dans `/home/denis/dev/loom_v2`, sur `741f887`, sans commit.

| Décision | Ce qui est fait | Où |
| --- | --- | --- |
| D-1, D-3, D-9 | Le prototype : suivi de l'appel, attente bornée de la chaîne (`grace`), `NOT_RUN`, fin tardive en `WARNING` | `engine/executor.py`, `core/tool_threads.py` |
| D-2, D-13 | Un délai dépassé sur un appel à effet lève `UnknownEffect`, avec trois textes : `LATE_EFFECT` (le travail continue en thread), `STOPPED_EFFECT` (outil arrêté en cours de route), `REMOTE_EFFECT` (serveur MCP prévenu) ; un appel sans effet garde « Délai dépassé » | `engine/executor.py` (`_invoked`) |
| D-4 | `ToolThreads`, un pool par instance `Loom`, passé à `build_agent` puis à l'exécuteur ; un agent monté hors de `Loom` a le sien, qu'il ferme avec lui ; un `ToolExecutor` construit seul en crée un. `in_thread` prend le pool du suivi de l'appel ; hors du moteur, c'est `asyncio.to_thread` | `core/tool_threads.py`, `runtime/wiring.py`, `access/api.py` |
| D-5 | Threads démons, créés à la demande. `Loom.aclose` appelle `ToolThreads.aclose` : le travail en attente est annulé, celui en cours attendu au plus `grace`, en le disant ; au-delà, un second `WARNING` nomme les outils laissés au travail | `core/tool_threads.py`, `access/api.py` |
| D-6 | `McpServer.call` lit l'identifiant de la requête sur la session (`_request_id`, que `send_request` prend avant tout `await` dans mcp 1.30) ; à l'annulation, une tâche à part envoie `notifications/cancelled` (2 s au plus, sans lever). Un SDK qui n'exposerait plus ce compteur prive seulement de l'annulation | `adapters/mcp/server.py` |
| D-7 | `loom.sql` (SQLite) et `loom.memory` (service, outil, fichiers, pages web) passent à `in_thread`, avec leur annulation propre (`interrupt`, `threading.Event`) | `tools_builtin/sql/sqlite.py`, `tools_builtin/memory/*` |
| D-8 | `ToolContext.cancelled`, un `threading.Event` par appel, levé au délai et à l'annulation du lot | `core/ports/tool.py`, `engine/executor.py` |
| D-10 | `_check_durable_journal` compte un outil `on_unknown: pause` qui a un délai (le sien ou `execution.tools.timeout`) | `runtime/wiring.py` |
| D-11 | `execution.tools.threads: 16`, `execution.tools.grace: 30` | `config/models.py` |
| D-12 | Entrée dans la section 2.0.20 du CHANGELOG, cinq lignes ⚠ | `CHANGELOG.md` |

**Documentation** : `configuration.md` (`tools.timeout`, `tools.threads`, `tools.grace`), `conception.md` (réalisation 2.0.20, après `on_unknown`), docstrings de `tools/python.py`, `core/tool_threads.py`, `adapters/mcp/server.py`, `ToolContext`.

**Essais** : `tests/unit/test_tool_threads.py` (13), `test_mcp_client.py` (3 de plus) ; deux attentes changent (§ Mesures).

| Essai | Ce qu'il montre |
| --- | --- |
| `test_each_loom_instance_has_its_own_pool` | Deux instances, deux pools, chacun à la taille et à la grâce de sa config |
| `test_the_tool_is_told_when_the_engine_stops_waiting` | `ToolContext.cancelled` levé au délai ; l'outil qui le regarde s'arrête après 1 à 3 étapes sur 10 |
| `test_closing_waits_for_the_tools_at_most_the_grace` | Un process dont un outil dort 30 s s'arrête après la grâce (0,5 s), avec les deux messages |
| `test_a_pausing_tool_with_a_deadline_needs_a_durable_journal` | `on_unknown: pause` et un journal en mémoire : refusé au montage |
| `test_an_abandoned_call_is_cancelled_on_the_server` | Serveur FastMCP, outil asynchrone de 0,5 s, délai 0,1 s : il n'écrit pas. Sans la notification (code retiré pour l'essai), il écrivait |
| `test_a_call_that_finishes_sends_no_cancellation` | Un appel qui finit n'est pas annulé |
| `test_the_engine_says_the_remote_effect_is_unknown` | Le modèle lit `REMOTE_EFFECT` |

**Les essais de l'analyse, refaits sur le code final** :

| Essai | `741f887` | Lot |
| --- | --- | --- |
| E1 — `envoyer` en retard puis `lire_envoyes` | La lecture rend « aucun » | La lecture attend le thread et rend le courriel |
| E3b — run voisin de six threads bloqués | 4,73 s | 0,01 s |
| E4 — `loom run`, outil de 20 s, délai 1 s (grâce de 2 s pour l'essai) | 21,2 s | 4,1 s, avec « Fermeture : attend la fin de 1 outil(s) en arrière-plan (long, depuis 1 s), 2 s au plus » puis « Fermeture sans attendre 1 outil(s) encore au travail (long) » |
| E7 — serveur MCP, outil asynchrone | Écrit 1 s après le délai | N'écrit pas : le serveur a reçu `notifications/cancelled` |
| E7 — serveur MCP, outil synchrone | Écrit 1 s après le délai | Écrit quand même : FastMCP ne peut pas arrêter son propre thread. Le modèle lit `REMOTE_EFFECT` |

**Mesures** :

| Passe | `741f887` | Lot |
| --- | --- | --- |
| Tous les extras | 4 481 réussis, 203 sautés, 1 échec | 4 497 réussis (+16), 203 sautés, 1 échec (l'essai de la forge Firecracker, le même) |
| Noyau seul | 3 472 réussis, 416 sautés | 3 485 réussis (+13 : les essais MCP demandent l'extra `mcp`), 416 sautés |

Tests existants modifiés : `test_call_order.py::test_a_timed_out_write_does_not_stop_the_chain` (`STOPPED_EFFECT`, en fin de lot) et `test_config_mcp.py::test_mounting_builds_one_source_per_reference` (un agent monté seul possède aussi son pool de threads). pyright strict : 0 erreur ; `ruff check .` et `ruff format --check .` : rien à redire ; `lint-imports` : 6 contrats tenus sur 6.

**Reste à faire, hors de ce lot** :

- **B-5** (`loom validate` et les outils fournis `loom.current_datetime`, `loom.fetch_page`) : deux lignes, en attente de ta décision.
- **Les outils de l'utilisateur** qui appellent eux-mêmes `asyncio.to_thread` : `in_thread` est public (`loom_ia.core.tool_threads`), à dire dans le guide.
- **`docs/loom_testing.md`, l. 331** (« Python ne peut pas interrompre le thread ») : la note reste vraie, mais ses conséquences sont maintenant traitées ; à compléter au prochain passage.

