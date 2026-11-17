# loom_ia.engine.pending

Module `loom_ia.engine.pending` de loom_v2 (fichier `src/loom_ia/engine/pending.py`).

Ce qui attend un humain dans la conversation, dit au modèle d'un nouveau tour.

L'historique d'une session ne reprend que les runs racine terminés : un run en
pause porte un appel sans résultat, que les API refusent (`history.py`). Un
tour lancé pendant cette pause ne voit donc rien de l'action en attente — au
test du 18/10, Haiku a proposé d'« envoyer une relance » qui attendait déjà
l'approbation de Denis. La requête reçoit une note qui la nomme
(« [Note du système : …] »).

La note est tirée des événements de la session **antérieurs au run** : la
reprise et le rejeu la retrouvent à l'identique. C'est une photo du début du
tour — une décision prise pendant qu'il tourne ne la change pas. Elle n'existe
que dans la requête, comme le state de session, et se place entre l'historique
et la demande du tour : le cache de l'historique reste valable, et les
adaptateurs la fusionnent avec la demande. Les arguments y figurent, coupés :
ils viennent de la même conversation, et ce sont eux qui disent **quelle**
action attend.

## Constantes de `loom_ia.engine.pending`

- `ARGUMENTS_CHARS = 300` : Longueur maximale des arguments d'une action en attente dans la note, en caractères de JSON.

## Fonction `loom_ia.engine.pending.pending_note`

```python
def pending_note(approvals: Sequence[PendingApproval]) -> Message | None
```

Note qui dit au modèle les actions qui attendent la décision d'un humain, à placer avant la demande du tour.

**Arguments :**

- `approvals` : Les demandes d'approbation encore ouvertes dans la session, au début du tour.

**Renvoie :**

Un message `user` « [Note du système : …] » qui nomme chaque action
(outil, appel, arguments coupés à 300 caractères) et demande de ne
pas la redemander ; `None` s'il n'y a rien en attente.

## Fonction `loom_ia.engine.pending._item`

```python
def _item(approval: PendingApproval) -> str
```

Une action en attente, en texte : outil, appel et arguments coupés.

## Fonction `loom_ia.engine.pending.question_note`

```python
def question_note(questions: Sequence[PendingQuestion]) -> Message | None
```

Message à placer avant la demande du tour quand une question attend encore sa réponse.

Cas d'un tour qui n'a pas été pris comme réponse (`docs/wait_answer.md`) :
la question reste posée, et le modèle doit le savoir pour ne pas la
reposer.

**Arguments :**

- `questions` : Les questions encore ouvertes dans la session.

**Renvoie :**

Un message `user` « [Note du système : …] » qui cite les questions ;
`None` s'il n'y en a pas.
