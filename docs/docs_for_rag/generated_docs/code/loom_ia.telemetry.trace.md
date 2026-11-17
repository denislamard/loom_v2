# loom_ia.telemetry.trace

Module `loom_ia.telemetry.trace` de loom_v2 (fichier `src/loom_ia/telemetry/trace.py`).

La trace d'un run, à relire : ses spans, tirés du journal (fonctions K5 et K6, point 29).

Ce sont les spans de l'export (`run_spans`, 6.1a) — mêmes noms, mêmes
attributs `gen_ai.*` et `loom.*` —, pour le run **et ses sous-runs**, avec
un en-tête qui dit où en est le run et ce qu'il a coûté. Trois différences
avec l'export, qui tiennent à ce qu'on relit au lieu d'envoyer :

- un événement n'est pas aplati en attributs : il porte sa charge **sans son
  contenu** (`data` — ce que rend `/events` sans `read_content` : qui a
  approuvé, les notes d'un juge, les tailles…), et son contenu **à part**
  (`content`), champ par champ (`content_fields`), ou `None` quand
  l'appelant n'a pas le droit de le voir ;
- **pas de masquage par motifs** : c'est la portée qui décide (décision du
  05/10, 6.2c) — sans `read_content` aucun contenu, avec, le contenu en
  clair, comme `/runs/{id}` et `/events` ; les motifs restent aux exports ;
- un run **inachevé** a sa trace : ses spans encore ouverts le disent
  (`open`), et leur fin est le dernier événement écrit.

Les corps d'un échange brut (`model.exchanged`) n'y sont jamais, comme à
l'export : le journal est leur place (`/events`).

## Classe `loom_ia.telemetry.trace.TraceEvent`

```python
class TraceEvent(DomainModel)
```

Un événement du journal, sur le span où il s'est produit.

**Attributs :**

- `name` : Type de l'événement.
- `at` : Horodatage.
- `seq` : Position dans le journal de la session.
- `status` : Statut de l'événement (`ok`, `error`…).
- `data` : Sa charge, privée de ses champs de contenu (`redacted`).
- `content` : Ses champs de contenu, par chemin ; `None` sans le droit de les voir.

## Classe `loom_ia.telemetry.trace.TraceSpan`

```python
class TraceSpan(DomainModel)
```

Un span : ce qu'il est, quand, ce qu'il a coûté, ce qui s'y est passé.

**Attributs :**

- `span_id` : Identifiant du span.
- `parent_span_id` : Span parent.
- `run_id` : Run du span.
- `name` : Nom du span.
- `kind` : Genre du span.
- `start` : Début.
- `end` : Fin, provisoire si le span est ouvert.
- `duration_ms` : Durée, en millisecondes.
- `open` : Pas encore fermé (run, étape ou appel en cours) : `end` est provisoire.
- `error` : Type de ce qui a échoué, jamais le message.
- `attributes` : Attributs : modèle, tokens, coût, outil…
- `events` : Les événements du span.

## Classe `loom_ia.telemetry.trace.Trace`

```python
class Trace(DomainModel)
```

La trace d'un run et de ses sous-runs.

**Attributs :**

- `run_id` : Le run.
- `session_id` : Sa session.
- `tenant_id` : Son client.
- `trace_id` : Identifiant de la trace : le run racine.
- `agent` : Agent du run.
- `status` : Statut du run.
- `finished` : Vrai si le run est clos.
- `iterations` : Appels de l'orchestrateur.
- `usage` : Tokens consommés, sous-runs compris.
- `cost_usd` : Coût en dollars.
- `active_ms` : Temps de pilotage, sans attente ni pause.
- `output` : Réponse finale, si le contenu est montré.
- `error_type` : Nature de l'échec, s'il a échoué.
- `content` : Vrai si les événements portent leur contenu (droit `read_content`).
- `spans` : Les spans du run et de ses sous-runs, dans l'ordre où ils s'ouvrent.

## Fonction `loom_ia.telemetry.trace.run_trace`

```python
def run_trace(events: Sequence[Event], run_id: RunId, *, content: bool) -> Trace
```

La trace du run `run_id`, tirée de ses événements et de ceux de ses sous-runs.

`events` sont ceux de l'arbre du run, dans l'ordre du journal
(`Loom.events`). Les spans sont rendus dans l'ordre où ils s'ouvrent.

**Arguments :**

- `events` : Les événements de l'arbre du run.
- `run_id` : Le run.
- `content` : Vrai pour joindre le contenu (droit `read_content`).

**Renvoie :**

La trace.

**Lève :**

- `ValueError` : Aucun événement de ce run.

## Fonction `loom_ia.telemetry.trace._closed`

```python
def _closed(members: Sequence[Event]) -> set[str]
```

Spans fermés d'un run : run clos, étape conclue, appel rendu.

## Fonction `loom_ia.telemetry.trace._span`

```python
def _span(record: SpanRecord, run_id: RunId, by_id: dict[str, Event], closed: set[str], content: bool) -> TraceSpan
```

Un span de la trace ; un run, une étape ou un appel d'outil sans clôture reste ouvert.
