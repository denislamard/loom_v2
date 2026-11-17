# loom_ia.core.projections.tree

Module `loom_ia.core.projections.tree` de loom_v2 (fichier `src/loom_ia/core/projections/tree.py`).

Arbre d'un run : le run et ses sous-runs, reconnus au fil du journal (fonction C5).

Un sous-run s'écrit dans le journal de la session de son parent (point 4). Pour
suivre un run avec ce qu'il délègue, on garde l'ensemble des runs de
l'arbre : un `run.started` dont le parent est déjà dans l'ensemble y fait
entrer son run. Comme un enfant démarre toujours après l'appel de son
parent, un seul passage dans l'ordre du journal suffit, y compris en direct.

Les marqueurs de session (`session.snapshot`, phase 4.1) n'en font pas partie :
ils portent le `run_id` du run qui les écrit, mais parlent de la session.

## Classe `loom_ia.core.projections.tree.RunTree`

```python
class RunTree
```

Runs d'un arbre, enrichi par `admit` à chaque événement vu dans l'ordre.

Le moteur, l'API et le rapport de consommation s'en servent pour lire un
run avec ce qu'il délègue.

**Arguments :**

- `run_id` : Le run racine de l'arbre suivi.
- `subruns` : Faux pour réduire l'arbre au run lui-même. Défaut : vrai.

**Attributs :**

- `run_id` : Le run racine.
- `subruns` : Vrai si les sous-runs entrent dans l'arbre.

### Méthode `loom_ia.core.projections.tree.RunTree.runs`

```python
@property
def runs(self) -> frozenset[RunId]
```

Les runs reconnus jusqu'ici : le run lui-même et ses sous-runs déjà démarrés.

### Méthode `loom_ia.core.projections.tree.RunTree.admit`

```python
def admit(self, event: Event) -> bool
```

Dit si un événement appartient à l'arbre ; un sous-run qui démarre y entre.

Les événements doivent être vus dans l'ordre du journal.

**Arguments :**

- `event` : L'événement suivant du journal.

**Renvoie :**

Vrai si l'événement est d'un run de l'arbre ; faux pour un
marqueur de session ou un autre run.

### Méthode `loom_ia.core.projections.tree.RunTree.select`

```python
def select(self, events: Iterable[Event]) -> list[Event]
```

Événements de l'arbre, dans l'ordre donné (celui du journal), par `admit`.

## Classe `loom_ia.core.projections.tree.OpeningSummaries`

```python
class OpeningSummaries
```

Résumés de session faits à l'ouverture d'un run, reconnus au fil du journal.

Avant son premier tour, un run peut faire résumer la session (compaction
bloquante, ou filet `hard_tokens`) : un run de compaction démarre avec
`triggered_by` égal à ce run, et écrit `session.compacted`. Ni l'un ni
l'autre ne sont dans l'arbre du run — un marqueur de session n'en fait pas
partie, et le run de compaction n'est pas un sous-run. À l'essai de la
fonction F3 du 19/10, ni la console ni `--stream` ne disaient donc qu'un
résumé avait eu lieu. Seuls comptent ceux d'avant le `run.started` du
run : un résumé de fond qu'il déclenche en finissant parle du run suivant.

**Arguments :**

- `run_id` : Le run dont l'ouverture est suivie.

**Attributs :**

- `run_id` : Le même run.

### Méthode `loom_ia.core.projections.tree.OpeningSummaries.admit`

```python
def admit(self, event: Event) -> bool
```

Dit si un événement est un `session.compacted` écrit à l'ouverture du run.

**Arguments :**

- `event` : L'événement suivant du journal.

**Renvoie :**

Vrai pour un résumé fait par un run de compaction déclenché par
ce run, avant son `run.started` ; faux pour tout le reste.

### Méthode `loom_ia.core.projections.tree.OpeningSummaries.select`

```python
def select(self, events: Iterable[Event]) -> list[Event]
```

Les `session.compacted` de l'ouverture du run, dans l'ordre du journal.
