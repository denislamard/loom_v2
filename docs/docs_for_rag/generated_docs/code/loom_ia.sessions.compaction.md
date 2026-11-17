# loom_ia.sessions.compaction

Module `loom_ia.sessions.compaction` de loom_v2 (fichier `src/loom_ia/sessions/compaction.py`).

Compaction d'une session : résumer les échanges anciens (point 23, conception §11.3).

Compacter ne réécrit rien. On ajoute un `session.compacted` qui porte le
résumé et la position qu'il couvre ; l'historique repart de ce résumé, et les
`keep_last` derniers tours restent intacts. Un tour est un run terminé avec
succès, le seul que l'historique garde : un run échoué, annulé ou en cours n'en
est pas un. La coupe tombe toujours sur une frontière de run, et la séquence
envoyée au modèle reste valide.

Le résumé est produit par un agent ordinaire, `_compaction`, monté comme
les autres : il hérite du retry, du modèle de secours, du suivi des coûts et
de la réparation. Son run vit dans le journal de la session (``kind:
compaction`, `triggered_by``) mais n'entre pas dans son historique.

Après le marqueur, le job écrit un snapshot rafraîchi qui contient déjà le
résumé : c'est ce qui fait que « le marqueur au plus grand `up_to_seq`
gagne » reste vrai. Un plantage entre les deux laisse un historique non
réduit mais juste, et la compaction sera refaite.

Le résumé se fait en tâche de fond, à la fin du run qui a fait dépasser
`over_tokens` ; avec `blocking`, à l'ouverture du run suivant, qui l'attend
(`before_run`).

Filet de sécurité : au démarrage d'un run, si l'historique dépasse
`hard_tokens`, `ensure_fits` compacte en synchrone ; si cela échoue, les
tours les plus anciens sont retirés (`session.trimmed`), avec un
avertissement dans le journal.

Config : `sessions.compaction` (`model`, `over_tokens`, `hard_tokens`,
`keep_last`, `fidelity_check`, `blocking`, `system_file`), voir
`config/compaction.py`.

## Constantes de `loom_ia.sessions.compaction`

- `SummaryResolver = Callable[[str, TenantId], RunContext]` : Monte l'agent interne pour le client dont on résume la session (fonction L1).  Deux clients ne résument pas leurs conversations avec le même modèle ni les mêmes identifiants. Reçoit le nom de l'agent et le client.

## Classe `loom_ia.sessions.compaction.CompactionPlan`

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class CompactionPlan
```

Réglages de la compaction, tirés de la config par le câblage.

**Attributs :**

- `agent` : Nom de l'agent interne (`_compaction`).
- `over_tokens` : Taille d'historique, en tokens estimés, au-delà de laquelle un résumé est dû.
- `hard_tokens` : Taille au-delà de laquelle le filet compacte en synchrone à l'ouverture d'un run, ou coupe.
- `keep_last` : Tours laissés intacts à la fin de l'historique.
- `fidelity_check` : Contrôle déterministe des repères du segment dans le résumé.
- `blocking` : Résumé à l'ouverture du run suivant plutôt qu'en tâche de fond après le run.

## Fonction `loom_ia.sessions.compaction.run_starts`

```python
def run_starts(events: Sequence[Event]) -> list[int]
```

Position du `run.started` de chaque tour de la session.

Un tour est un run ordinaire terminé avec succès : c'est le seul que
l'historique garde (`turns`). Un run échoué, annulé ou en cours n'est pas
compté, sinon `keep_last` en garderait de vides et résumerait les vrais
derniers tours.

## Fonction `loom_ia.sessions.compaction.summarised`

```python
def summarised(events: Sequence[Event]) -> int
```

Position déjà couverte par un résumé ou une coupe, 0 sinon.

**Arguments :**

- `events` : Le journal de la session.

## Fonction `loom_ia.sessions.compaction.cut`

```python
def cut(events: Sequence[Event], keep_last: int) -> int
```

Position jusqu'à laquelle résumer, en gardant les derniers tours intacts.

**Arguments :**

- `events` : Le journal de la session.
- `keep_last` : Tours à garder.

**Renvoie :**

La position, juste avant le premier tour gardé et sans couper un run ;
0 s'il n'y a rien à résumer.

## Fonction `loom_ia.sessions.compaction.oversized`

```python
def oversized(events: Sequence[Event], limit: int) -> bool
```

Vrai si l'historique de la session dépasse ce nombre de tokens estimé.

**Arguments :**

- `events` : Le journal de la session.
- `limit` : Le seuil, en tokens estimés.

## Classe `loom_ia.sessions.compaction.CompactionJob`

```python
class CompactionJob
```

Résume un segment de session, puis écrit le marqueur et un snapshot.

Mis en file (`TaskQueue`) à la fin d'un run, appelé à l'ouverture du
suivant (`before_run`).

**Arguments :**

- `resolve` : Monte l'agent interne pour un client.
- `store` : Le journal.
- `writers` : Registre des écrivains de l'instance.
- `plan` : Les réglages.

### Méthode `loom_ia.sessions.compaction.CompactionJob.key`

```python
def key(self, session_id: SessionId, up_to_seq: int) -> str
```

Clé de dédoublonnage : un seul résumé par session et par position.

**Arguments :**

- `session_id` : La session.
- `up_to_seq` : Position à résumer.

### Méthode `loom_ia.sessions.compaction.CompactionJob.pending`

```python
def pending(self, events: Sequence[Event], *, limit: int | None=None) -> int | None
```

Position à résumer, ou `None` s'il n'y a rien à faire.

`limit` : seuil de déclenchement ; `over_tokens` par défaut.

**Arguments :**

- `events` : Le journal de la session.
- `limit` : Le seuil, en tokens estimés.

**Renvoie :**

La position à résumer si l'historique dépasse le seuil et qu'un
segment non résumé précède les `keep_last` derniers tours.

### Méthode `loom_ia.sessions.compaction.CompactionJob.compact`

```python
async def compact(self, tenant_id: TenantId, session_id: SessionId, *, triggered_by: RunId | None=None, limit: int | None=None) -> SessionCompacted | None
```

Résume la session si un segment le mérite ; rend le marqueur écrit.

**Arguments :**

- `tenant_id` : Client de la session.
- `session_id` : La session.
- `triggered_by` : Run qui a déclenché le résumé.
- `limit` : Seuil de déclenchement ; `over_tokens` par défaut.

**Renvoie :**

Le `session.compacted` écrit ; `None` s'il n'y avait rien à
résumer ou si l'agent interne a échoué.

### Méthode `loom_ia.sessions.compaction.CompactionJob.before_run`

```python
async def before_run(self, tenant_id: TenantId, session_id: SessionId, *, run_id: RunId | None=None) -> None
```

À l'ouverture d'un run : le résumé dû, s'il est bloquant, puis le filet.

Un résumé bloquant qui échoue ne fait pas échouer le run : il part avec
l'historique non réduit, et le résumé sera retenté à l'ouverture du
suivant. Une annulation, elle, passe.

**Arguments :**

- `tenant_id` : Client de la session.
- `session_id` : La session.
- `run_id` : Le run qui s'ouvre.

### Méthode `loom_ia.sessions.compaction.CompactionJob.ensure_fits`

```python
async def ensure_fits(self, tenant_id: TenantId, session_id: SessionId, *, run_id: RunId | None=None) -> None
```

Filet de sécurité : compacte en synchrone, ou retire les vieux tours.

`run_id` : le run qui s'ouvre, noté `triggered_by` du résumé, comme
pour le résumé bloquant.

**Arguments :**

- `tenant_id` : Client de la session.
- `session_id` : La session.
- `run_id` : Le run qui s'ouvre.

### Méthode `loom_ia.sessions.compaction.CompactionJob._trim`

```python
async def _trim(self, tenant_id: TenantId, session_id: SessionId, events: Sequence[Event]) -> None
```

Retire les tours les plus anciens jusqu'à repasser sous la limite.

Si même le dernier tour seul dépasse la limite, on coupe quand même
tout ce qui le précède : un historique trop gros n'est pas envoyable,
alors qu'un historique amputé l'est.

### Méthode `loom_ia.sessions.compaction.CompactionJob._summarise`

```python
async def _summarise(self, tenant_id: TenantId, session_id: SessionId, segment: str, triggered_by: RunId | None) -> RunState | None
```

Fait tourner l'agent interne sur le segment ; `None` s'il échoue.

### Méthode `loom_ia.sessions.compaction.CompactionJob._refresh`

```python
async def _refresh(self, writer: SessionWriter, final: RunState, marker: Event) -> None
```

Snapshot qui contient déjà le résumé : il prend le pas sur le marqueur.

Il couvre le marqueur lui-même, sinon il le devancerait et l'historique
repartirait de l'ancien snapshot. Si un run tourne encore dans la
session, on s'abstient : le marqueur seul laisse un historique non
réduit mais juste, et la compaction sera refaite.

## Fonction `loom_ia.sessions.compaction._scope`

```python
def _scope(state: RunState) -> RunScope
```

Portée du run de compaction, pour écrire son marqueur et son snapshot.

## Fonction `loom_ia.sessions.compaction._session_scope`

```python
def _session_scope(tenant_id: TenantId, session_id: SessionId) -> RunScope
```

Portée d'un marqueur écrit hors de tout run (coupe de sécurité).
