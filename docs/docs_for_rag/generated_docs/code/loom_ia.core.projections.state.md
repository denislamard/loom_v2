# loom_ia.core.projections.state

Module `loom_ia.core.projections.state` de loom_v2 (fichier `src/loom_ia/core/projections/state.py`).

State d'une session : les valeurs que l'application et les outils y ont posées.

Le state n'a pas d'événement à lui : il voyage dans ceux qui existent. Le
`run.started` d'un run porte ce que l'application a posé en l'ouvrant, et
le `tool.completed` d'un appel ce que l'outil a écrit (`output.state`).
Le journal fait foi ; le state en est une projection, recalculée sur tout le
journal, que les marqueurs de session (snapshot, compaction, coupe) ne
changent donc pas.

Ce qui compte, dans l'ordre du journal :

- le state posé par l'application, à l'ouverture de chaque run racine,
  quel que soit le sort du run ;
- les écritures d'un run racine terminé avec succès, à sa clôture, sans
  celles des appels qu'une réparation a retirés de l'historique (décision D2
  du state de session) ;
- en dernier, les écritures du run en cours, s'il est désigné : il voit
  les siennes avant d'être terminé.

Les sous-runs et les runs de compaction n'y entrent pas (décision D3), comme
dans l'historique. `None` efface une clé (décision D10). Les valeurs par
défaut valent pour une clé absente ; elles ne sont jamais écrites.

Voir `docs/state-et-context.md` et `docs/state_v2_analyse.md`.

## Fonction `loom_ia.core.projections.state.session_state`

```python
def session_state(events: Iterable[Event], *, run_id: RunId | None=None, defaults: Mapping[str, JsonValue] | None=None) -> dict[str, JsonValue]
```

State de la session après ces événements.

**Arguments :**

- `events` : Le journal de la session, dans l'ordre.
- `run_id` : Run en cours, dont les écritures comptent déjà, même avant sa fin.
- `defaults` : Valeur de chaque clé absente du state (`default:` d'une clé déclarée).

**Renvoie :**

Le state, clé → valeur. C'est une copie : la modifier ne touche ni
aux événements lus, ni aux valeurs par défaut.

## Fonction `loom_ia.core.projections.state.state_of_run`

```python
def state_of_run(base: Mapping[str, JsonValue], run: RunState, *, before: int | None=None) -> dict[str, JsonValue]
```

State que voit un run en cours : la session avant lui, ce qui est posé, ce qu'il a écrit.

Un outil voit ainsi le state au début de son lot, quel que soit l'ordre
dans lequel ses voisins finissent, et la reprise d'un lot interrompu lui
montre le même.

**Arguments :**

- `base` : Le state de la session avant le run (`session_state` des événements qui le précèdent).
- `run` : L'état du run.
- `before` : Position, dans les messages du run, de la réponse du modèle dont le lot s'exécute ; seules comptent alors les écritures des lots précédents. `None` : toutes.

**Renvoie :**

Le state, en copie.

## Fonction `loom_ia.core.projections.state._counted`

```python
def _counted(run: RunState) -> bool
```

Run dont le state compte pour la session : un run racine ordinaire (décision D3 du state de session).

## Fonction `loom_ia.core.projections.state._kept`

```python
def _kept(run: RunState) -> dict[str, JsonValue]
```

Écritures d'un run terminé que garde l'historique de la session (décision D2 du state de session).

Une réparation retire de l'historique la réponse refusée et les résultats
de ses appels : leurs écritures sont écartées avec eux, pour que le
modèle ne lise jamais un state que l'historique ne justifie pas. C'est la
position de la réponse qui le dit, et non l'identifiant de l'appel, qu'un
fournisseur peut redonner après la réparation.

## Fonction `loom_ia.core.projections.state._merge`

```python
def _merge(values: dict[str, JsonValue], delta: Mapping[str, JsonValue]) -> None
```

Applique un delta au state : `None` efface la clé (décision D10 du state de session).
