# loom_ia.sessions.snapshot

Module `loom_ia.sessions.snapshot` de loom_v2 (fichier `src/loom_ia/sessions/snapshot.py`).

Snapshot de l'historique d'une session (point 22, conception §11.2).

Rejouer tout le journal à chaque run coûte de plus en plus cher à mesure que
la conversation s'allonge. À la fin d'un run, l'historique déjà calculé est
donc écrit tel quel dans le journal, en `session.snapshot` : le run suivant
en repart et ne rejoue que la suite.

Le snapshot n'est qu'une vue : un lecteur qui l'ignore reconstruit le même
historique en relisant tout. Il n'est donc jamais nécessaire, et on ne
l'écrit que lorsqu'il fait gagner quelque chose — au moins
`snapshot_every` événements depuis le dernier marqueur (`sessions.snapshot_every`,
50 par défaut) — pour ne pas
recopier l'historique dans le journal à chaque run.

Sa position s'arrête avant le premier événement d'un run encore en cours :
un run repris au milieu ne se projette pas. Un run de compaction, qui ne se
reprend pas, n'y compte pas (`Cuts`).

À ne pas confondre avec la compaction (point 23) : le snapshot est une vue
matérialisée sans LLM, pour lire vite ; la compaction est un résumé par LLM,
pour réduire le contexte.

## Fonction `loom_ia.sessions.snapshot.estimate_tokens`

```python
def estimate_tokens(messages: Sequence[Message]) -> int
```

Taille approximative d'un historique, en tokens.

Sans tokenizer : la sérialisation JSON de chaque message, divisée par
`CHARS_PER_TOKEN`. Elle sert aux seuils, pas à une facturation.

**Arguments :**

- `messages` : L'historique.

**Renvoie :**

Le nombre estimé de tokens.

## Fonction `loom_ia.sessions.snapshot.snapshot`

```python
def snapshot(events: Sequence[Event]) -> SessionSnapshot
```

Snapshot de l'historique porté par ces événements, sans condition.

**Arguments :**

- `events` : Le journal de la session.

**Renvoie :**

Le `session.snapshot` : position couverte (`boundary`), messages de
l'historique jusque-là, taille estimée en tokens.

## Fonction `loom_ia.sessions.snapshot.due`

```python
def due(events: Sequence[Event], *, every: int) -> bool
```

Vrai si un snapshot ferait gagner assez de relecture pour être écrit.

**Arguments :**

- `events` : Le journal de la session.
- `every` : Événements au moins entre le dernier marqueur et la coupe possible.

## Fonction `loom_ia.sessions.snapshot.marked`

```python
def marked(events: Sequence[Event]) -> int
```

Position couverte par le dernier marqueur de session écrit, 0 s'il n'y en a pas.

## Fonction `loom_ia.sessions.snapshot.boundary`

```python
def boundary(events: Sequence[Event]) -> int
```

Dernière position où l'on peut couper le journal sans couper un run.

## Classe `loom_ia.sessions.snapshot.Cuts`

```python
class Cuts
```

Positions où couper le journal d'une session : jamais au milieu d'un run.

Une coupe tombe avant tout run encore en cours, et jamais à l'intérieur
d'un run clos : un run qui a des événements des deux côtés serait lu sans
son `run.started` à la reprise, donc perdu de l'historique. Deux runs de
la session qui se chevauchent (le premier fini, le second encore en cours)
reculent donc la coupe avant le premier.

Les marqueurs de session n'y comptent pas : sans cela, écrire un snapshot
repousserait la position et en appellerait aussitôt un autre. Un marqueur
écrit hors de tout run (coupe de sécurité) porte le run_id de la session :
ce n'est pas un run, il n'est jamais « en cours ».

Un seul parcours du journal, sans projeter les runs : seule compte la
clôture (`run.completed`, `run.failed` ou `run.cancelled`), que
`fold_all` ne faisait que retrouver au prix d'une copie d'état par
événement.

Un sous-run ne repart que par l'appel de son parent : le parent clos, il
ne repartira plus, et il est clos de fait, à son dernier événement. C'est
ce que sont les orphelins des journaux d'avant la fermeture des sous-runs
avec leur parent (2.0.0) : sans cela, leur parent clos, ils figeraient la
frontière pour toujours. Rien n'est écrit : le journal reste tel quel.

Un run de compaction n'y compte pas non plus : l'historique ne le lit
jamais, et une coupe qui le traverse ne perd rien. Interrompu — process
fermé pendant le résumé —, il reste sans fin au journal, puisque rien ne le
reprend ; compté, il figerait la frontière avant lui, et avec elle les
compactions, les snapshots et la coupe de sécurité de la session.

### Méthode `loom_ia.sessions.snapshot.Cuts.__init__`

```python
def __init__(self, events: Sequence[Event]) -> None
```

Lit le journal une fois : début et fin de chaque run, sous-runs, runs de compaction.

**Arguments :**

- `events` : Le journal de la session.

### Méthode `loom_ia.sessions.snapshot.Cuts.at_most`

```python
def at_most(self, position: int | None=None) -> int
```

Plus grande coupe qui ne dépasse pas `position` (le journal entier par défaut).

Elle ne fait que reculer, et un run ne la recoupe plus une fois qu'elle
est passée avant lui : un seul passage, du dernier run au premier.

**Arguments :**

- `position` : Position à ne pas dépasser ; `None` : la fin du journal.

**Renvoie :**

La position de la coupe ; 0 si aucune n'est possible.

## Fonction `loom_ia.sessions.snapshot.write_snapshot`

```python
async def write_snapshot(writer: SessionWriter, events: Sequence[Event], run: RunState, *, every: int) -> SessionSnapshot | None
```

Écrit un snapshot si l'un est dû ; rend celui qui a été écrit.

Appelé à la fin d'un run.

**Arguments :**

- `writer` : Écrivain de la session.
- `events` : Le journal de la session.
- `run` : Le run qui vient de finir ; le snapshot est écrit sous lui.
- `every` : `sessions.snapshot_every`.

**Renvoie :**

Le snapshot écrit ; `None` s'il n'était pas dû ou serait vide.
