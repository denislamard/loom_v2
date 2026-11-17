# loom_ia.replay.runner

Module `loom_ia.replay.runner` de loom_v2 (fichier `src/loom_ia/replay/runner.py`).

Rejouer un run, à l'identique ou en variante, et dire où il s'écarte de son journal (fonction K6, point 31).

Le rejeu se fait **en mémoire**, sur une copie de la session arrêtée juste
après la demande du run : tout ce qui précédait — les runs d'avant, leurs
résumés, les instantanés — est là, si bien que l'historique et la requête se
reconstruisent comme la première fois. Le run garde **son identifiant** : un
juge tiré au sort (`sample`) l'est sur lui, et le tirage est donc le même.

Puis le moteur reprend le run là où la demande l'a laissé (`drive`), avec un
agent monté comme d'habitude, sauf pour le monde : au rejeu **identique**, ses
clients de modèle, ses outils (sauf les rôles, qui sont de la logique) et ses
approbations sont servis par le journal, et la première divergence arrête
tout ; en **variante** (`variant`), ce que le journal connaît est servi, le
reste part pour de vrai — outils à effets de bord exceptés —, et le rapport
compare les deux runs.

Rien n'est écrit dans le vrai journal. `export` garde le journal du rejeu.

## Constantes de `loom_ia.replay.runner`

- `ReplayMode = Literal['exact', 'variant']` : Mode du rejeu : `exact` (identique) ou `variant`.
- `_ASIDE = frozenset({RunStatus.PAUSED, RunStatus.WAITING_CHILD})` : États qui dépendent de la façon de piloter, pas de ce que le run a fait : une approbation tranchée en ligne ne passe pas par la pause, un sous-agent rejoué depuis le journal ne fait pas attendre son parent.
- `ContextFactory = Callable[[EventStore, ReplayBook, JournalTools | VariantTools, str, TenantId], RunContext]` : Monte l'agent d'un run pour le rejeu : (journal en mémoire, livre, outils du rejeu, agent, client) → contexte du run.

## Classe `loom_ia.replay.runner.Verdict`

```python
@dataclass(frozen=True, slots=True)
class Verdict
```

Un verdict de juge, tel que le run l'a reçu.

**Attributs :**

- `judge` : Nom du juge.
- `attempt` : Tentative jugée.
- `passed` : Le verdict est favorable.
- `scores` : (critère, note) pour chaque critère.

## Classe `loom_ia.replay.runner.RunSide`

```python
@dataclass(frozen=True, slots=True)
class RunSide
```

Un des deux runs comparés : ce qu'il a fait et ce qu'il a coûté.

**Attributs :**

- `end` : Issue du run : `completed`, `failed`, `cancelled`, ou `inachevé`.
- `text` : Texte de la réponse ; `None` sans sortie.
- `data` : Sortie structurée.
- `error_type` : Type d'erreur d'un run en échec.
- `model_calls` : Appels de modèle de son arbre (sous-agents et rôles compris).
- `tool_calls` : Appels d'outil conclus de son arbre, rôles à part : leurs appels de modèle sont comptés avec les autres.
- `usage` : Tokens du run.
- `cost_usd` : Coût du run, en dollars.
- `active_ms` : Temps actif, en millisecondes.
- `verdicts` : Verdicts des juges du run racine.

### Méthode `loom_ia.replay.runner.RunSide.as_json`

```python
def as_json(self) -> dict[str, JsonValue]
```

Ce côté de la comparaison en JSON.

## Classe `loom_ia.replay.runner.Comparison`

```python
@dataclass(frozen=True, slots=True)
class Comparison
```

Le run d'origine et sa variante, côte à côte (jalon J6.2b).

**Attributs :**

- `original` : Le run d'origine.
- `variant` : Le run de la variante.
- `served_models` : Appels de modèle de la variante servis par le journal.
- `real_models` : Appels de modèle de la variante partis pour de vrai.
- `spent_usd` : Ce que les vrais appels ont coûté : la seule dépense de la variante.
- `real_usage` : Tokens de ces vrais appels.
- `tools` : Sort des appels d'outil conclus de la variante (rôles à part), compté.
- `calls` : Les mêmes, appel par appel : (outil, sort), dans l'ordre du journal du rejeu.
- `swapped` : Modèles changés par étape (`main`, rôle, `judge:<nom>`).

### Méthode `loom_ia.replay.runner.Comparison.same_answer`

```python
@property
def same_answer(self) -> bool
```

Même fin, même réponse ; pour deux échecs, la même cause (comme au rejeu identique).

### Méthode `loom_ia.replay.runner.Comparison.as_json`

```python
def as_json(self) -> dict[str, JsonValue]
```

La comparaison en JSON.

## Classe `loom_ia.replay.runner.ReplayReport`

```python
@dataclass(frozen=True, slots=True)
class ReplayReport
```

Ce que le rejeu a trouvé, comparé au journal.

**Attributs :**

- `run_id` : Run rejoué.
- `session_id` : Sa session.
- `tenant_id` : Son client.
- `agent` : Son agent.
- `mode` : `exact` (rejeu identique) ou `variant`.
- `identical` : Aucune divergence : le rejeu a suivi le journal jusqu'au bout.
- `divergence` : Rejeu identique : là où il s'arrête ; variante : là où elle quitte le run d'origine.
- `original_end` : Issue du run d'origine : `completed`, `failed` ou `cancelled`.
- `replay_end` : Issue du rejeu, sous la même forme (`inachevé` s'il ne finit pas).
- `model_calls` : Appels de modèle : (au journal, servis au rejeu par le journal) ; en variante, ceux de l'arbre du run (sous-agents compris).
- `tool_calls` : Appels d'outil, sous la même forme : ceux que le journal sert — pas les rôles, qui tournent et se comptent par leurs appels de modèle ; en variante, ceux de l'arbre du run.
- `comparison` : Variante : les deux runs côte à côte.
- `events` : Événements du run rejoué (de son arbre en variante), demande comprise.

### Méthode `loom_ia.replay.runner.ReplayReport.as_json`

```python
def as_json(self) -> dict[str, object]
```

Le rapport en JSON (`--json` de `loom replay`).

## Classe `loom_ia.replay.runner.JournalRun`

```python
@dataclass(frozen=True, slots=True)
class JournalRun
```

Un run racine d'un journal, et où le trouver.

**Attributs :**

- `tenant_id` : Client du run.
- `session_id` : Sa session.
- `run_id` : Identifiant du run.
- `agent` : Son agent.
- `finished` : Le run est fini (complété, en échec ou annulé) : seul un run fini se rejoue.

## Classe `loom_ia.replay.runner.JournalReplay`

```python
@dataclass(frozen=True, slots=True)
class JournalReplay
```

Les runs d'un journal, rejoués un à un (jalon J6.3b).

**Attributs :**

- `journal` : Le fichier rejoué ; `None` quand ses événements ont été donnés déjà lus.
- `reports` : Le rapport de chaque run rejoué.
- `unfinished` : Runs racines inachevés : un rejeu compare un run fini, ils ne sont pas rejoués — et le journal ne se rejoue donc pas en entier.

### Méthode `loom_ia.replay.runner.JournalReplay.identical`

```python
@property
def identical(self) -> bool
```

Chaque run du journal se rejoue à l'identique, et aucun n'est resté de côté.

### Méthode `loom_ia.replay.runner.JournalReplay.as_json`

```python
def as_json(self) -> dict[str, object]
```

Le rejeu du journal en JSON (`--json` de `loom replay`) : chaque run rejoué, et les runs inachevés.

## Fonction `loom_ia.replay.runner.read_journal`

```python
def read_journal(path: Path) -> list[Event]
```

Les événements d'un journal JSONL ; `ReplayError` s'il ne se lit pas.

Le format est celui qu'écrivent `loom sessions export`, ``loom eval
--export` et `loom replay --export`` : un événement par ligne, en clair.

## Fonction `loom_ia.replay.runner.journal_runs`

```python
def journal_runs(events: Sequence[Event]) -> list[JournalRun]
```

Les runs qu'un journal donne à rejouer : ses runs racines, dans l'ordre du journal.

Un sous-run se rejoue avec son run racine, qui le sert depuis le journal ;
un run système (résumé de session) n'est pas un run demandé — ce qu'il a
écrit est relu par les runs qui le suivent. Ni l'un ni l'autre n'est listé.

## Fonction `loom_ia.replay.runner.replay_run`

```python
async def replay_run(session_events: Sequence[Event], run_id: RunId, context_for: ContextFactory, *, mode: ReplayMode='exact', doubles: Mapping[str, Double] | None=None, swapped: Mapping[str, str] | None=None, export: Path | None=None) -> ReplayReport
```

Rejoue le run `run_id` de cette session et le compare à son journal.

`context_for(store, book, tools, agent, tenant)` monte l'agent du run
pour le rejeu : le journal en mémoire, le livre qui sert les réponses, et
les outils du rejeu à poser sur ses exécuteurs — ceux du journal, ou ceux
de la variante avec ses doublures (`doubles`). `swapped` : les modèles
changés par étape, que le rapport recopie.

## Fonction `loom_ia.replay.runner._log`

```python
def _log(report: ReplayReport) -> None
```

La divergence, dite une fois — le moteur, lui, n'a noté qu'un arrêt voulu.

## Fonction `loom_ia.replay.runner._tree`

```python
def _tree(events: Sequence[Event], run_id: RunId) -> list[Event]
```

Les événements d'un run et de ses sous-runs, dans l'ordre du journal.

## Fonction `loom_ia.replay.runner._side`

```python
def _side(events: Sequence[Event], state: RunState, ending: Event | None) -> RunSide
```

Un côté de la comparaison : issue, réponse, appels, coût, et verdicts des juges du run racine.

**Arguments :**

- `events` : Événements de l'arbre du run.
- `state` : État final du run.
- `ending` : Son événement de fin ; `None` s'il n'a pas fini.

## Fonction `loom_ia.replay.runner._fates`

```python
def _fates(events: Sequence[Event], tools: VariantTools | None) -> tuple[tuple[str, ToolFate], ...]
```

Sort des appels d'outil conclus de la variante, par run et `call_id`.

## Fonction `loom_ia.replay.runner._tools_done`

```python
def _tools_done(events: Sequence[Event]) -> list[Event]
```

Appels d'outil conclus, rôles à part (leurs appels de modèle se comptent ailleurs).

## Fonction `loom_ia.replay.runner._compare`

```python
def _compare(book: ReplayBook, original: Sequence[Event], replayed: Sequence[Event], ending: Event, replay_ending: Event | None) -> None
```

Ce qui ne se voit qu'à la fin : appels non refaits, issue, réponse, parcours.

## Fonction `loom_ia.replay.runner._path`

```python
def _path(events: Sequence[Event]) -> list[str]
```

Les états traversés, sans ceux qui tiennent à la façon de piloter.
