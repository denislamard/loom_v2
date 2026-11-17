# loom_ia.telemetry.export

Module `loom_ia.telemetry.export` de loom_v2 (fichier `src/loom_ia/telemetry/export.py`).

Export des runs terminés vers les collecteurs (fonction K4, point 29, conception §14.1).

Un abonné du journal, qui ne regarde qu'une chose : l'événement qui **clôt**
un run (`run.completed`, `run.failed`, `run.cancelled`). À ce moment, il
relit le run dans le journal, en tire les spans (`run_spans`) et les remet
aux collecteurs. Trois conséquences, voulues :

- **un run part entier, une fois** — même repris par un autre worker après
  une pause ou une panne, c'est le process qui le clôt qui l'exporte, et il
  le relit depuis le journal, pas depuis ce qu'il a vu passer ;
- **chaque process exporte ce qu'il écrit** (`listen(own=True)`) : deux
  workers suivis par un même bus n'envoient jamais la même trace deux fois ;
- **un run inachevé n'est pas exporté** — en pause, il n'apparaît qu'à sa
  fin ; et un process qui meurt entre sa dernière écriture et l'envoi perd
  cette trace, faute de curseur gardé quelque part. Le journal, lui, a tout.

L'export ne met jamais un run en échec : une panne de collecteur est un
avertissement dans les logs.

## Classe `loom_ia.telemetry.export.SpanSink`

```python
class SpanSink(Protocol)
```

Un collecteur : reçoit des spans, les envoie à son rythme.

Implémentation fournie : `OtelSpanSink` (`adapters/telemetry/otel.py`).

### Méthode `loom_ia.telemetry.export.SpanSink.name`

```python
@property
def name(self) -> str
```

Nom du collecteur, pour les logs.

### Méthode `loom_ia.telemetry.export.SpanSink.export`

```python
def export(self, spans: Sequence[SpanRecord]) -> None
```

Prend les spans en charge sans attendre le réseau.

### Méthode `loom_ia.telemetry.export.SpanSink.aclose`

```python
async def aclose(self, grace: float) -> None
```

Envoie ce qui reste, dans la limite du délai, et ferme.

## Classe `loom_ia.telemetry.export.RunExporter`

```python
class RunExporter
```

Remet aux collecteurs les spans de chaque run qui se termine ici.

`content_for` dit, client par client, si le contenu part
(`capture.exports: content`) ; `redactor` masque ce contenu.

Monté par `create_run_exporter` quand `telemetry.exporters` déclare
un collecteur monté, et abonné au journal de l'instance.

**Arguments :**

- `store` : Le journal, où relire le run.
- `sinks` : Les collecteurs.
- `content_for` : Vrai si le contenu du client part.
- `redactor` : Le masqueur du contenu.

### Méthode `loom_ia.telemetry.export.RunExporter.sinks`

```python
@property
def sinks(self) -> tuple[SpanSink, ...]
```

Les collecteurs.

### Méthode `loom_ia.telemetry.export.RunExporter.pending`

```python
@property
def pending(self) -> int
```

Exports en cours.

### Méthode `loom_ia.telemetry.export.RunExporter.__call__`

```python
def __call__(self, event: Event) -> None
```

Abonné du journal : appelé à chaque écriture, n'attend rien.

### Méthode `loom_ia.telemetry.export.RunExporter.export_run`

```python
async def export_run(self, tenant_id: TenantId, session_id: SessionId, run_id: RunId) -> int
```

Exporte un run tel qu'il est au journal ; rend le nombre de spans remis.

**Arguments :**

- `tenant_id` : Client du run.
- `session_id` : Sa session.
- `run_id` : Le run.

### Méthode `loom_ia.telemetry.export.RunExporter.aclose`

```python
async def aclose(self, grace: float) -> None
```

Attend les exports en cours, puis vide et ferme les collecteurs.

**Arguments :**

- `grace` : Délai laissé aux exports et aux collecteurs, en secondes.
