# loom_ia.telemetry

Paquet `loom_ia.telemetry` de loom_v2 (fichier `src/loom_ia/telemetry/__init__.py`).

Télémétrie de loom-ia : logs, spans tirés du journal, masquage, exports, traces à relire.

Rien ici n'importe OpenTelemetry : les spans sont ceux du journal (point 29), et
l'adaptateur `adapters.telemetry.otel` les traduit (extra `otel`).

- `logs` : `configure_logging`, formats `console` et `json` ;
- `spans` : les spans d'un run, tirés de son journal (`run_spans`) ;
- `export` : `RunExporter`, qui remet les runs clos aux collecteurs ;
- `redaction` : `Redactor`, masquage par motifs du contenu exporté ;
- `trace` et `inspect` : la trace d'un run à relire (`loom inspect`,
  `GET /v1/runs/{id}/trace`).

Config : `telemetry` (`logging`, `capture`, `redaction`,
`exporters`).
