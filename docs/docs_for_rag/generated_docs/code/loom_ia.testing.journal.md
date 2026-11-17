# loom_ia.testing.journal

Module `loom_ia.testing.journal` de loom_v2 (fichier `src/loom_ia/testing/journal.py`).

Fabrique de journaux de runs fictifs, pour les tests et les exemples (fonction O2).

Les événements produits suivent la machine à états (point 3) : étapes,
transitions et clôture sont écrits comme le moteur le fera.

## Fonction `loom_ia.testing.journal.tool_call_message`

```python
def tool_call_message(*calls: tuple[str, str, dict[str, JsonValue]], text: str | None=None) -> Message
```

Réponse `assistant` qui demande des outils : `(call_id, nom, arguments)`.

## Classe `loom_ia.testing.journal.RunJournal`

```python
class RunJournal
```

Construit, étape par étape, les événements d'un run.

### Méthode `loom_ia.testing.journal.RunJournal.__init__`

```python
def __init__(self, *, agent: str='demo', tenant_id: TenantId=DEFAULT_TENANT, session_id: SessionId | None=None, run_id: RunId | None=None, root_run_id: RunId | None=None, step_ms: float=1.0) -> None
```

Prépare un run fictif.

**Arguments :**

- `agent` : Nom de l'agent.
- `tenant_id` : Client.
- `session_id` : Session ; à défaut, celle du run.
- `run_id` : Identifiant du run ; à défaut, un nouveau.
- `root_run_id` : Run racine ; à défaut, le run lui-même.
- `step_ms` : Durée donnée à chaque étape, en millisecondes : elle compte dans `RunState.active_ms`, donc dans le délai d'un run (fonction A6).

### Méthode `loom_ia.testing.journal.RunJournal.run_id`

```python
@property
def run_id(self) -> RunId
```

Identifiant du run.

### Méthode `loom_ia.testing.journal.RunJournal.take`

```python
def take(self) -> list[EventDraft]
```

Brouillons produits depuis le dernier appel.

### Méthode `loom_ia.testing.journal.RunJournal.start`

```python
def start(self, prompt: str, *, context: CallerContext | None=None, parent_run_id: RunId | None=None, parent_call_id: str | None=None, depth: int=0, state: Mapping[str, JsonValue] | None=None) -> Self
```

Ouverture du run et demande ; `state` : state de session posé par l'application.

### Méthode `loom_ia.testing.journal.RunJournal.model_turn`

```python
def model_turn(self, message: Message, *, usage: Usage | None=None, cost_usd: float=0.0, model_id: str='fake-model', model: str | None=None) -> Self
```

Appel du modèle ; passe en attente d'outils s'il en demande.

### Méthode `loom_ia.testing.journal.RunJournal.tool_results`

```python
def tool_results(self, outputs: Mapping[str, ToolOutput]) -> Self
```

Exécution du lot d'outils en attente, puis retour au modèle.

### Méthode `loom_ia.testing.journal.RunJournal.transition`

```python
def transition(self, to_state: RunStatus, *, cause: str | None=None, cause_event_id: EventId | None=None) -> Self
```

Transition vers `to_state`.

**Arguments :**

- `to_state` : L'état visé.
- `cause` : Type de l'événement qui la provoque.
- `cause_event_id` : Son identifiant.

### Méthode `loom_ia.testing.journal.RunJournal.complete`

```python
def complete(self) -> Self
```

Clôture réussie : transition vers `COMPLETED` et `run.completed` (réponse : le dernier message de l'assistant).

### Méthode `loom_ia.testing.journal.RunJournal.fail`

```python
def fail(self, error_type: str, error: str) -> Self
```

Clôture en échec : transition vers `FAILED` et `run.failed`.

**Arguments :**

- `error_type` : Nature de l'échec.
- `error` : Message.
