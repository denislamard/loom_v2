# loom_ia.core.events.envelope

Module `loom_ia.core.events.envelope` de loom_v2 (fichier `src/loom_ia/core/events/envelope.py`).

Enveloppe commune des événements du journal (point 22).

Un événement, c'est une enveloppe (identifiants, horodatage, coordonnées dans
l'arbre des runs et des spans, champs de recherche) et une charge typée
(`core/events/payloads.py`).

Un `EventDraft` est un événement pas encore écrit : c'est le store qui lui
attribue son numéro d'ordre `seq` dans le journal de la session et le
transforme en `Event`. `type`, `category`, `status` et `facets` sont
recopiés de la charge à la construction, puis vérifiés à chaque relecture.
`RunScope` porte les coordonnées d'un run et fabrique ses brouillons.

## Constantes de `loom_ia.core.events.envelope`

- `SCHEMA_VERSION = 1` : Version du schéma des événements écrits ; une autre version est refusée à la lecture.  Ajouter un champ facultatif ne la change pas : un lecteur plus ancien ignore ce qu'il ne connaît pas.

## Fonction `loom_ia.core.events.envelope._now`

```python
def _now() -> datetime
```

Instant présent, en UTC : horodatage par défaut d'un événement.

## Classe `loom_ia.core.events.envelope._EventFields`

```python
class _EventFields(DomainModel)
```

Champs communs à un brouillon et à un événement écrit ; voir `Event`.

### Méthode `loom_ia.core.events.envelope._EventFields._reject_unknown_version`

```python
@model_validator(mode='before')
@classmethod
def _reject_unknown_version(cls, data: object) -> object
```

Refuse un événement d'une autre version de schéma, avec un message explicite (point 22).

### Méthode `loom_ia.core.events.envelope._EventFields._check_derived_fields`

```python
@model_validator(mode='after')
def _check_derived_fields(self) -> Self
```

Vérifie que `type`, `category`, `status` et `facets` sont ceux de la charge.

## Classe `loom_ia.core.events.envelope.EventDraft`

```python
class EventDraft(_EventFields)
```

Événement construit par le moteur, avant écriture : il n'a pas encore de `seq`.

Mêmes champs qu'`Event`, sauf `seq`. Le moteur l'obtient par
`RunScope.draft` ; le store le change en `Event` en l'écrivant.

### Méthode `loom_ia.core.events.envelope.EventDraft.to_event`

```python
def to_event(self, seq: int) -> Event
```

L'événement écrit, à ce rang du journal de la session.

**Arguments :**

- `seq` : Numéro d'ordre attribué par le store, à partir de 1.

**Renvoie :**

L'événement, revalidé.

## Classe `loom_ia.core.events.envelope.Event`

```python
class Event(_EventFields)
```

Événement écrit dans le journal d'une session : une enveloppe et sa charge.

Le journal fait foi : l'historique, l'état d'un run, les traces, la
consommation et le rejeu en sont déduits (point 22).

**Attributs :**

- `event_id` : Identifiant unique de l'événement.
- `ts` : Horodatage de création, avec fuseau (UTC à la création).
- `schema_version` : Version du schéma ; seule `1` est acceptée.
- `tenant_id` : Client (espace isolé, fonction L1).
- `session_id` : Session dont le journal porte l'événement.
- `run_id` : Run qui l'a écrit.
- `root_run_id` : Run racine de l'arbre : lui-même pour un run racine, l'ancêtre commun pour un sous-run.
- `span_id` : Span de l'événement : celui du run, d'une étape ou d'un appel.
- `parent_span_id` : Span parent ; `None` pour le span d'un run racine.
- `type` : Type de la charge (`model.responded`…), recopié pour la recherche.
- `category` : Catégorie de la charge, recopiée.
- `status` : Statut de la charge (`ok`, `warning`, `error`), recopié.
- `agent` : Agent du run.
- `role` : Rôle LLM délégué qui a fait l'appel de modèle, ou `judge:<nom>` pour l'appel d'un juge ; `None` pour l'orchestrateur.
- `facets` : Champs de recherche tirés de la charge (`facets()`).
- `payload` : La charge, discriminée par `type`.
- `seq` : Numéro d'ordre dans le journal de la session, à partir de 1 ; attribué par le store.

## Classe `loom_ia.core.events.envelope.RunScope`

```python
class RunScope(DomainModel)
```

Coordonnées d'un run dans le journal : fabrique ses `EventDraft`.

**Attributs :**

- `tenant_id` : Client du run.
- `session_id` : Session du run.
- `run_id` : Identifiant du run.
- `root_run_id` : Run racine de son arbre.
- `agent` : Agent du run, recopié dans chaque événement.
- `span_id` : Span racine du run : parent par défaut des spans de ses étapes. Engendré par défaut.
- `parent_span_id` : Span parent du run : celui de l'appel qui l'a lancé, pour un sous-run ; `None` pour un run racine.

### Méthode `loom_ia.core.events.envelope.RunScope.draft`

```python
def draft(self, payload: DurablePayload, *, span_id: SpanId | None=None, parent_span_id: SpanId | None=None, role: str | None=None) -> EventDraft
```

Brouillon d'événement pour ce run, avec ses champs de recherche tirés de la charge.

**Arguments :**

- `payload` : La charge de l'événement.
- `span_id` : Span de l'événement ; sans lui, l'événement appartient au span racine du run.
- `parent_span_id` : Parent de `span_id` ; par défaut, le span racine du run.
- `role` : Rôle LLM délégué qui a fait l'appel, ou `judge:<nom>`.

**Renvoie :**

Le brouillon, prêt à écrire.
