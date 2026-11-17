# loom_ia.access.progress

Module `loom_ia.access.progress` de loom_v2 (fichier `src/loom_ia/access/progress.py`).

Déroulé d'un run en lignes courtes (fonctions N4 et D4).

Le direct de la CLI (`loom run --stream`) et les notifications de
progression du serveur MCP décrivent un run de la même façon : un appel
d'outil et son issue, un fichier rangé, un serveur indisponible, une
décision de politique, un contrôle de sortie et le verdict d'un juge, une
limite de budget atteinte, une bascule vers un modèle de secours, un
disjoncteur qui s'ouvre, un sous-agent qui démarre puis se termine. Les
lignes d'un sous-run sont décalées selon sa profondeur :

    · verifier(message='Vérifie : …')
      · sous-agent verificateur : démarré
      · math__calculer(expression='121 * 24')
      · math__calculer : fait
      · sous-agent verificateur : terminé
    · verifier : fait

## Constantes de `loom_ia.access.progress`

- `INDENT = ' '` : Décalage d'une ligne par niveau de sous-run.
- `ERROR_CHARS = 160` : Longueur au-delà de laquelle un message d'erreur est coupé.
- `STORED = {'attachment': 'pièce jointe rangée', 'tool_output': 'fichier rangé', 'offload': 'résultat déporté'}` : Comment le déroulé nomme un fichier rangé, selon son origine.
- `DECISIONS = {'continue': 'laissé passer', 'replace': 'remplacé', 'retry': 'réparation demandée', 'deny': 'refusé', 'pause': 'pause'…` : Décisions de politique, telles qu'une ligne du déroulé les nomme.
- `RESOLUTIONS = {'retry': 'réparation demandée', 'fail': 'refusée', 'unverified': 'gardée, non vérifiée', 'fallback': 'remplacée par le…` : Issue d'un contrôle de sortie, telle qu'une ligne du déroulé la nomme.
- `FIDELITY = {'ok': 'fidélité vérifiée', 'warning': 'fidélité douteuse', 'skipped': 'fidélité non vérifiée'}` : Ce que dit le contrôle de fidélité d'un résumé.
- `SKIP_REASONS = {'caller_skip': "sauté à la demande de l'appelant (judges: skip)", 'sampled_out': 'hors échantillon (when.sample)', 'fi…` : Motif d'un juge sauté (`guard.checked` `skipped`), en clair.

## Classe `loom_ia.access.progress.Progress`

```python
class Progress
```

Traduit les événements d'un arbre de runs en lignes, vus dans l'ordre du journal.

### Méthode `loom_ia.access.progress.Progress.depth`

```python
def depth(self, event: Event) -> int
```

Profondeur du run de l'événement (0 pour un run inconnu).

### Méthode `loom_ia.access.progress.Progress.line`

```python
def line(self, event: Event) -> str | None
```

Ligne qui décrit l'événement, ou `None` s'il ne se montre pas.

## Fonction `loom_ia.access.progress.describe`

```python
def describe(event: Event, *, subrun: bool=False) -> str | None
```

Ce que l'événement apprend du déroulé ; début et fin ne se montrent que pour un sous-run.

## Fonction `loom_ia.access.progress.signed`

```python
def signed(by: str, via: str | None=None, *, self_approved: bool=False) -> str
```

Qui a agi, tel que le déroulé le dit : `marie (via app-crm)` (§ 7).

Une auto-approbation se dit : celui qui tranche a lancé le run (décision D-7 des utilisateurs et rôles).

## Fonction `loom_ia.access.progress.describe_compaction`

```python
def describe_compaction(payload: SessionCompacted) -> str
```

Un résumé de session en une ligne : « 233 → 105 tokens estimés (jusqu'à … ) ».

## Fonction `loom_ia.access.progress.notes`

```python
def notes(criteria: Iterable[CriterionScore]) -> str
```

Notes d'un juge, avec le seuil des critères qui ne l'atteignent pas.

Un critère non bloquant sous son seuil le dit : il n'a pas fait refuser la sortie.

## Fonction `loom_ia.access.progress.arguments`

```python
def arguments(called: ToolCalled) -> str
```

Arguments d'un appel, en `nom=valeur`.

## Fonction `loom_ia.access.progress._short`

```python
def _short(text: str) -> str
```

Message d'erreur sur une ligne, coupé s'il est long.

## Fonction `loom_ia.access.progress._withdrawn`

```python
def _withdrawn(cancelled: RunCancelled) -> str
```

Les demandes d'approbation que l'annulation a closes, s'il y en avait.
