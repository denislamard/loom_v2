# loom.yaml : la clé telemetry

La clé `telemetry` de `loom.yaml` et tout ce qu'elle contient.

## `telemetry`

Logs, capture des exports, masquage et collecteurs (`telemetry` ; fonctions K3, K4 et K7, conception §17.7).

Modèle Pydantic : `loom_ia.config.models.TelemetryConfig`.

- `telemetry.logging` (type `LoggingConfig` ; défaut : les défauts de la section) : Logs du process.
- `telemetry.capture` (type `CaptureConfig` ; défaut : les défauts de la section) : Ce que les exports emportent : métadonnées ou contenu.
- `telemetry.redaction` (type `RedactionConfig` ; défaut : les défauts de la section) : Motifs masqués dans ce qui sort.
- `telemetry.exporters` (type `tuple[ExporterConfig, ...]` ; défaut : `[]`) : Collecteurs OpenTelemetry. Sans collecteur, rien ne sort du journal que les logs.

## `telemetry.logging`

Logs du process (`telemetry.logging`).

Modèle Pydantic : `loom_ia.config.models.LoggingConfig`.

- `telemetry.logging.level` (type `str` ; défaut : `"INFO"`) : Niveau : `DEBUG`, `INFO` (défaut), `WARNING`, `ERROR`…
- `telemetry.logging.format` (type `LogFormat` ; défaut : `"console"`) : `console` (texte, défaut) ou `json` (une ligne JSON par log).

## `telemetry.capture`

Ce qui sort du journal vers un collecteur, et ce qui s'y ajoute en opt-in (`telemetry.capture` ; conception §14.2).

Modèle Pydantic : `loom_ia.config.telemetry.CaptureConfig`.

- `telemetry.capture.exports` (type `CaptureLevel` ; défaut : `"metadata"`) : `metadata` (défaut) : l'enveloppe et ce qui n'est pas du contenu ; `content` : aussi le contenu des charges, après masquage.
- `telemetry.capture.raw_exchanges` (type `bool` ; défaut : `false`) : Ajoute au journal chaque échange HTTP brut avec un fournisseur (`model.exchanged`). Faux par défaut.
- `telemetry.capture.raw_max_bytes` (type `int` ; défaut : `262144` ; bornes : > 0) : Taille maximale de chaque corps gardé, en octets (256 Kio par défaut).

## `telemetry.redaction`

Motifs masqués dans le contenu exporté (`telemetry.redaction`).

Modèle Pydantic : `loom_ia.config.telemetry.RedactionConfig`.

- `telemetry.redaction.patterns` (type `tuple[DeclaredPattern, ...]` ; défaut : `["secret", "email", "phone", "iban"]`) : Motifs : noms des motifs fournis (`secret`, `email`, `phone`, `iban`) et motifs à soi (`{name, regex}`).

## `telemetry.redaction.patterns[]`

Motif de masquage déclaré par la config : un nom, une expression régulière.

Modèle Pydantic : `loom_ia.config.telemetry.RedactionPattern`.

- `telemetry.redaction.patterns[].name` (type `str` ; obligatoire ; bornes : longueur ≥ 1) : Nom du motif ; pas celui d'un motif fourni.
- `telemetry.redaction.patterns[].regex` (type `str` ; obligatoire ; bornes : longueur ≥ 1) : Expression régulière (bornée en temps) ; elle ne doit pas accepter la chaîne vide.

## `telemetry.exporters[]`

Un collecteur OpenTelemetry, joint en OTLP (`telemetry.exporters` ; point 29).

Modèle Pydantic : `loom_ia.config.telemetry.ExporterConfig`.

- `telemetry.exporters[].type` (type `str` ; obligatoire) : Type du collecteur : `otel`.
- `telemetry.exporters[].endpoint_env` (type `str` ; défaut : `"OTEL_EXPORTER_OTLP_ENDPOINT"`) : Variable qui porte l'adresse du collecteur (`OTEL_EXPORTER_OTLP_ENDPOINT` par défaut) ; vide, le collecteur n'est pas monté.
- `telemetry.exporters[].protocol` (type `OtlpProtocol` ; défaut : `"http/protobuf"`) : `http/protobuf` (défaut) ou `grpc`.
- `telemetry.exporters[].headers_env` (type `str | null` ; défaut : `null`) : Variable qui porte les en-têtes (`clé=valeur,clé=valeur`).
- `telemetry.exporters[].service_name` (type `str` ; défaut : `"loom-ia"` ; bornes : longueur ≥ 1) : Nom du service dans les traces (`loom-ia` par défaut).
- `telemetry.exporters[].timeout` (type `float` ; défaut : `10.0` ; bornes : > 0) : Délai d'un envoi au collecteur, en secondes (10 par défaut).
