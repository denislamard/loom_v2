# loom_ia.core.tool_threads

Module `loom_ia.core.tool_threads` de loom_v2 (fichier `src/loom_ia/core/tool_threads.py`).

Les threads des outils : un pool à part, et le travail qui survit à son délai (décision B-2 du délai et des threads).

Un outil synchrone s'exécute dans un thread. Python ne sait pas arrêter un
thread : quand le délai de l'outil expire, ou que le run est annulé, le moteur
rend la main, mais le travail continue jusqu'à son terme. Un fichier peut
s'écrire, un courriel partir, après que le modèle a lu « délai dépassé ».

Ce module donne aux outils `in_thread`, à la place d'`asyncio.to_thread` :

- **un pool à part** (`ToolThreads`, threads `loom-outil`), un par instance
  `Loom`, et non celui d'asyncio : des threads bloqués n'empêchent plus le
  journal JSONL, le stockage d'artefacts ou un autre run du process d'écrire,
  qui passent par le pool d'asyncio ;
- **le suivi de l'appel** : le travail lancé pendant un appel est noté dans son
  suivi (`tracking`, posé par le moteur autour de l'appel). Après un délai, le
  moteur sait s'il tourne encore, et peut attendre sa fin réelle ;
- **l'annulation coopérative** : le suivi porte un `threading.Event`
  (`ToolContext.cancelled`), levé au délai et à l'annulation, qu'un outil
  regarde entre deux étapes de son travail.

Les threads du pool sont des **démons** : ils n'empêchent pas le process de
s'arrêter. La fermeture de `Loom` (`ToolThreads.close`) attend ceux qui
travaillent encore, au plus `grace` secondes, en disant lesquels ; au-delà, le
process s'arrête sans eux, en le disant aussi (décision D-5 du délai et des
threads, `docs/moteur-delai-thread-analyse.md`).

La taille du pool et l'attente à la fermeture se règlent dans `loom.yaml` :
`execution.tools.threads` (16 par défaut) et `execution.tools.grace` (30 s
par défaut).

Hors du moteur (pas de suivi), `in_thread` revient à `asyncio.to_thread` :
un outil appelé directement, ou le serveur `loom-mem-gpu`, s'exécute comme avant.

Le module ne dépend que de la bibliothèque standard : un outil l'importe sans
rien tirer du moteur.

## Constantes de `loom_ia.core.tool_threads`

- `DEFAULT_THREADS = 16` : Threads du pool des outils, par défaut (`execution.tools.threads`).
- `DEFAULT_GRACE = 30.0` : Attente au plus, en secondes, du travail en cours à la fermeture, par défaut (`execution.tools.grace`).

## Classe `loom_ia.core.tool_threads.ToolThreads`

```python
class ToolThreads
```

Le pool des outils synchrones d'une instance `Loom` : des threads démons, `size` au plus.

Les threads se créent à la demande, nommés `loom-outil-<n>`, tant que le
pool n'est pas plein ; un thread libre est réutilisé. Un travail soumis
quand tous les threads sont pris attend son tour dans la file ; le délai de
l'outil court pendant ce temps.

**Arguments :**

- `size` : Nombre maximal de threads, au moins 1. Défaut : 16.
- `grace` : Attente au plus, en secondes, du travail en cours à la fermeture (`aclose`) ; `None` : sans borne. Défaut : 30.

**Attributs :**

- `size` : Nombre maximal de threads.
- `grace` : Attente au plus du travail en cours à la fermeture.

**Lève :**

- `ValueError` : `size` est inférieur à 1 (« threads : au moins 1 »).

### Méthode `loom_ia.core.tool_threads.ToolThreads.submit`

```python
def submit(self, fn: Callable[[], object], name: str) -> Future[object]
```

Confie un travail au pool.

**Arguments :**

- `fn` : Le travail, sans argument (`in_thread` y lie les arguments et le contexte).
- `name` : Nom de l'outil, repris dans les logs et dans `running`.

**Renvoie :**

Le futur du travail ; `cancel()` le retire de la file tant qu'il
n'a pas démarré.

**Lève :**

- `RuntimeError` : Le pool est fermé (« pool des outils fermé »).

### Méthode `loom_ia.core.tool_threads.ToolThreads.running`

```python
@property
def running(self) -> list[tuple[str, float]]
```

Le travail en cours : nom de l'outil et secondes écoulées, le plus ancien d'abord.

### Méthode `loom_ia.core.tool_threads.ToolThreads.close`

```python
def close(self, grace: float | None) -> list[str]
```

Ferme le pool et attend le travail en cours, au plus `grace` secondes.

Le travail qui attendait son tour est annulé. Si des outils travaillent
encore, un avertissement les nomme, avec leur durée ; s'ils travaillent
toujours à l'échéance, un second avertissement dit que leur effet
éventuel est d'état inconnu. Bloquant : depuis la boucle d'événements,
passer par `aclose`.

**Arguments :**

- `grace` : Attente au plus, en secondes ; `None` : sans borne.

**Renvoie :**

Les noms des outils encore au travail à l'échéance : le process
s'arrêtera sans les attendre.

### Méthode `loom_ia.core.tool_threads.ToolThreads.aclose`

```python
async def aclose(self) -> None
```

Ferme le pool avec `close(self.grace)`, dans un thread : l'attente ne bloque pas la boucle.

### Méthode `loom_ia.core.tool_threads.ToolThreads._work`

```python
def _work(self) -> None
```

Boucle d'un thread du pool : exécute les travaux de la file jusqu'au signal de fin (`None`).

## Classe `loom_ia.core.tool_threads.CallThreads`

```python
@dataclass(slots=True)
class CallThreads
```

Le suivi du travail en thread d'un appel d'outil : son pool, ses futurs, son signal d'annulation.

Le moteur en crée un par appel et le pose avec `tracking` ; `in_thread`
y note chaque travail lancé.

**Attributs :**

- `name` : Nom de l'outil, repris dans les logs.
- `pool` : Le pool de l'instance ; `None` : `in_thread` passe par `asyncio.to_thread`.
- `cancelled` : Événement levé au délai et à l'annulation de l'appel ; l'outil le lit par `ToolContext.cancelled` et s'arrête quand il le peut (décision D-8 du délai et des threads).
- `futures` : Les travaux confiés au pool pendant l'appel.
- `abandoned_at` : Instant (horloge monotone) où l'appel a rendu la main sans attendre son travail ; `None` tant qu'il ne l'a pas fait. Un travail qui finit après est « en retard » et le dit dans les logs.

### Méthode `loom_ia.core.tool_threads.CallThreads.running`

```python
@property
def running(self) -> list[Future[object]]
```

Les travaux de l'appel qui ne sont pas finis : en cours, ou encore dans la file.

### Méthode `loom_ia.core.tool_threads.CallThreads.never_started`

```python
@property
def never_started(self) -> bool
```

Vrai si l'appel a confié du travail au pool et qu'aucun n'a démarré.

Tout a alors été annulé dans la file, faute de thread libre avant le
délai : le moteur sait que l'outil n'a produit aucun effet.

### Méthode `loom_ia.core.tool_threads.CallThreads.abandon`

```python
def abandon(self) -> None
```

Rend la main sans attendre le travail de l'appel.

Lève `cancelled` ; à la première fois, note l'instant et attache à
chaque travail en cours un rappel qui, à sa fin, écrit un avertissement
(« travail fini … après que l'appel a rendu la main ; son résultat est
perdu »). Les appels suivants ne font que lever `cancelled`.

### Méthode `loom_ia.core.tool_threads.CallThreads._late`

```python
def _late(self, future: Future[object]) -> None
```

Rappel d'un travail fini après `abandon` : le dit dans les logs, avec son retard.

## Fonction `loom_ia.core.tool_threads.tracking`

```python
@contextmanager
def tracking(threads: CallThreads) -> Generator[CallThreads]
```

Suit le travail en thread lancé pendant l'appel d'un outil ; posé par le moteur.

Le suivi est porté par une `contextvars.ContextVar` : `in_thread` le
trouve sans qu'on le lui passe, dans la tâche de l'appel.

**Arguments :**

- `threads` : Le suivi de l'appel.

**Produit :**

Le même suivi, actif jusqu'à la sortie du `with`.

## Fonction `loom_ia.core.tool_threads.current`

```python
def current() -> CallThreads | None
```

Le suivi de l'appel d'outil en cours, ou `None` hors du moteur.

## Fonction `loom_ia.core.tool_threads.in_thread`

```python
async def in_thread[**P, R](fn: Callable[P, R], /, *args: P.args, **kwargs: P.kwargs) -> R
```

Exécute une fonction synchrone dans le pool des outils, suivie par l'appel en cours.

À utiliser dans un outil à la place d'`asyncio.to_thread`. Le contexte
(`contextvars`) passe au thread, comme avec `to_thread`. Hors du moteur,
ou sans pool, c'est `asyncio.to_thread`.

Si la tâche qui attend est annulée (délai dépassé, run arrêté), un travail
encore dans la file est retiré ; un travail déjà lancé va au bout, car
Python ne sait pas arrêter un thread.

**Arguments :**

- `fn` : La fonction à exécuter.
- `*args` : Ses arguments positionnels.
- `**kwargs` : Ses arguments nommés.

**Renvoie :**

Ce que renvoie `fn`.

**Lève :**

- `asyncio.CancelledError` : La tâche qui attend a été annulée.
