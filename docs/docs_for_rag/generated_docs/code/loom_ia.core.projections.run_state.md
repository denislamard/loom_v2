# loom_ia.core.projections.run_state

Module `loom_ia.core.projections.run_state` de loom_v2 (fichier `src/loom_ia/core/projections/run_state.py`).

Projection de l'état d'un run : le `RunState` rejoué depuis le journal (point 3, fonctions H1 à H3).

C'est ce qui rend un run sérialisable et reprenable : après un plantage, le
moteur relit le journal, rejoue l'état par `fold` et reprend où le run en
était (appels lancés, approbations, questions, réparations).

`apply` enregistre des faits : il ne décide rien. Les décisions (appeler
un outil, changer d'état) sont prises par le moteur et arrivent ici sous
forme d'événements, dont `run.transitioned`.

Une réponse de modèle qui sert un appel d'outil (rôle délégué, C2) n'entre
pas dans la conversation de l'orchestrateur : seuls son usage et son coût
s'ajoutent au run (fonction C2). Un sous-agent (fonction C5) a son propre run ; sa consommation
arrive avec le `tool.completed` de l'appel.

Politiques (point 2) : `policy.decided` enregistre ce qui doit survivre à une
reprise — une réparation décidée mais pas encore demandée (`Retry`), les
arguments remplacés d'un appel, la réponse finale remplacée — et compte les
réparations de chaque politique. Le message de réparation (`message.user`
de `kind: repair`) entre dans la conversation du run, et sa position est
retenue pour l'exclure de l'historique de session.

State de la session : le résultat réussi d'un outil peut porter des
écritures (`output.state`). Elles sont retirées du résultat que lit le
modèle et rangées dans `state_writes`, dans l'ordre des appels ;
`session_state` en tire le state de la session.

Les événements de catégorie `session` (phase 4.1) sont ignorés : ils décrivent
la session, pas le run, et s'écrivent après sa clôture. Une concession
(`run.claimed`) écrite après l'état final est absorbée de même : elle
décrit le pilote, pas le run, et rend sa lecture possible aux journaux de la
2.0.0 qui en portent une.

## Classe `loom_ia.core.projections.run_state.ProjectionError`

```python
class ProjectionError(Exception)
```

Le journal est incohérent avec l'état reconstruit (divergence).

Exemples : un événement avant `run.started`, un `run.started` en
double, un événement après l'état final, une transition depuis un autre
état que l'état reconstruit, un résultat pour un appel inconnu, une
décision d'approbation sans demande ou déjà tranchée. Le message nomme le
run, le type d'événement et son `seq`.

## Fonction `loom_ia.core.projections.run_state.apply`

```python
def apply(state: RunState | None, event: Event) -> RunState
```

Applique un événement à l'état d'un run : enregistre un fait, sans rien décider.

Ce qui change selon l'événement : la conversation (demande, réponses de
l'orchestrateur, résultats d'outils), les appels en attente, la
consommation, le statut (par `run.transitioned` et les événements
terminaux), les approbations et questions ouvertes ou tranchées, les
réparations, les écritures du state, les artefacts, les bascules de
modèle, les budgets dépassés. Les marqueurs de session, une concession ou
une décision écrite après l'état final ne font qu'avancer `last_seq`.

**Arguments :**

- `state` : L'état avant l'événement ; `None` pour le premier événement du run, qui doit être son `run.started`.
- `event` : L'événement suivant du run.

**Renvoie :**

Le nouvel état (copie ; l'ancien n'est pas modifié).

**Lève :**

- `ProjectionError` : L'événement est incohérent avec l'état reconstruit.

## Fonction `loom_ia.core.projections.run_state._restated`

```python
def _restated(messages: tuple[Message, ...], call_id: str, arguments: dict[str, JsonValue]) -> tuple[Message, ...]
```

Réécrit un appel de la conversation avec les arguments qui vont vraiment partir (point 17).

Le modèle doit lire ce qui a eu lieu, et non ce qu'il avait demandé :
sinon le résultat de l'outil contredit son propre appel, et il en conclut
à une panne. Vu au run réel du 21/09 — l'approbateur avait corrigé le
destinataire d'un e-mail, et MiniMax, voyant partir une adresse qu'il
n'avait pas écrite, a proposé de renvoyer l'e-mail déjà parti.

Le journal ne bouge pas : `tool.called` garde les arguments du modèle,
et la décision qui les a corrigés dit qui a voulu quoi.

## Fonction `loom_ia.core.projections.run_state._settled`

```python
def _settled(state: RunState, call_id: str, event: Event, outcome: ApprovalOutcome) -> tuple[PendingApproval, ...]
```

Demande refermée par sa décision ; les autres sont laissées telles quelles.

Une décision qui ne désigne aucune demande est une incohérence du journal,
pas un cas à absorber : deux décisions pour un même appel s'annuleraient
en silence.

## Fonction `loom_ia.core.projections.run_state._answered`

```python
def _answered(state: RunState, call_id: str, event: Event, change: dict[str, object]) -> tuple[PendingQuestion, ...]
```

Question refermée par sa réponse ou son expiration ; une seule fois.

## Fonction `loom_ia.core.projections.run_state._launched`

```python
def _launched(state: RunState, call_id: str) -> tuple[PendingApproval, ...]
```

Accords de cet appel marqués comme ayant servi : l'appel part sur eux.

Un accord vaut pour **un** lancement. Si le run plante ensuite, l'effet de
ce lancement est d'état inconnu, et l'accord ne couvre pas un second : la
reprise le lit à ce marquage, sans que le journal ait rien de plus à dire.

## Fonction `loom_ia.core.projections.run_state._amended`

```python
def _amended(state: RunState, call_id: str) -> tuple[Amendment, ...]
```

Ce qui a changé les arguments de cet appel avant son lancement, s'il y a lieu.

Une politique `before_tool` passe d'abord, l'approbateur ensuite : chacun
est comparé à ce qu'il a reçu. Un appel relancé après une interruption
remplace sa trace au lieu de la doubler.

## Fonction `loom_ia.core.projections.run_state._changed`

```python
def _changed(before: dict[str, JsonValue], after: dict[str, JsonValue]) -> tuple[str, ...]
```

Champs dont la valeur diffère : ceux d'après dans leur ordre, puis ceux retirés.

## Fonction `loom_ia.core.projections.run_state._granted`

```python
def _granted(approval: PendingApproval) -> bool
```

Vrai pour une demande d'approbation accordée.

## Fonction `loom_ia.core.projections.run_state._decided`

```python
def _decided(state: RunState, decided: PolicyDecided, event: Event) -> dict[str, object]
```

Ce qu'une décision de politique change dans l'état.

## Fonction `loom_ia.core.projections.run_state._closes`

```python
def _closes(status: RunStatus, event: Event) -> bool
```

Vrai pour l'événement de clôture qui suit la transition vers un état final.

## Fonction `loom_ia.core.projections.run_state._terminal_result`

```python
def _terminal_result(state: RunState, event: Event) -> ToolResultBlock
```

Résultat de l'outil terminal, dernier message du run (point 13) ; `ProjectionError` s'il n'est pas unique.

## Fonction `loom_ia.core.projections.run_state._written`

```python
def _written(state: RunState, call_id: str, values: dict[str, JsonValue]) -> tuple[StateWrite, ...]
```

Écritures du run, celles de cet appel rangées à leur place dans l'ordre des appels.

Les résultats d'un lot arrivent dans l'ordre où les outils finissent : il
change d'une exécution à l'autre, et au rejeu, où ils sont instantanés.
L'ordre des appels ne dépend que de la réponse du modèle, comme celui des
résultats dans une requête (`in_call_order`) : c'est lui qui départage
deux écritures d'une même clé (décision D9 du state de session).

## Fonction `loom_ia.core.projections.run_state._require_pending`

```python
def _require_pending(state: RunState, call_id: str, event: Event) -> tuple[PendingCall, ...]
```

Les appels en attente du run ; `ProjectionError` si `call_id` n'en fait pas partie.

## Fonction `loom_ia.core.projections.run_state.fold`

```python
def fold(events: Iterable[Event], run_id: RunId) -> RunState
```

Reconstruit l'état d'un run à partir du journal.

**Arguments :**

- `events` : Le journal de la session (ou ses événements du run), dans l'ordre.
- `run_id` : Le run à reconstruire ; les autres runs et les marqueurs de session sont ignorés.

**Renvoie :**

L'état du run après son dernier événement.

**Lève :**

- `ProjectionError` : Aucun événement pour ce run, ou journal incohérent.

## Fonction `loom_ia.core.projections.run_state.fold_all`

```python
def fold_all(events: Iterable[Event]) -> dict[RunId, RunState]
```

États de tous les runs d'un journal, dans l'ordre de leur démarrage.

Les marqueurs de session n'y entrent pas : ils décrivent la session, et
celui qui les écrit peut ne pas être dans la fenêtre relue.

**Arguments :**

- `events` : Les événements, dans l'ordre du journal ; chaque run doit y commencer par son `run.started`.

**Renvoie :**

Run → état.

**Lève :**

- `ProjectionError` : Journal incohérent.

## Fonction `loom_ia.core.projections.run_state.awaiting_in_session`

```python
def awaiting_in_session(events: Iterable[Event]) -> tuple[PendingApproval, ...]
```

Demandes d'approbation sans réponse des runs racine encore ouverts d'un journal.

Celles du run racine et celles de ses sous-runs (un sous-agent en pause
arrête son parent), dans l'ordre du journal. Un run racine fini n'attend
plus rien, même si un sous-run est resté en pause (journaux d'avant leur
fermeture avec le parent) ; un run de compaction n'entre pas en compte.

Sert à `loom sessions show` et aux accès, qui montrent ce qu'une session
attend.

**Arguments :**

- `events` : Le journal de la session.

**Renvoie :**

Les demandes en attente, dans l'ordre du journal.

## Fonction `loom_ia.core.projections.run_state.asking_in_session`

```python
def asking_in_session(events: Iterable[Event]) -> tuple[PendingQuestion, ...]
```

Questions sans réponse des runs racine encore ouverts d'un journal.

Comme `awaiting_in_session` : celles du run racine et de ses sous-runs.

**Arguments :**

- `events` : Le journal de la session.

**Renvoie :**

Les questions ouvertes (outil `loom.ask`), dans l'ordre du journal.

## Fonction `loom_ia.core.projections.run_state._consumed`

```python
def _consumed(state: RunState, usage: Usage, cost: float) -> dict[str, object]
```

Un appel de modèle du run : usage, coût et compte des appels.
