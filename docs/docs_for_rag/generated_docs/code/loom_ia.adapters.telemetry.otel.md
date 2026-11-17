# loom_ia.adapters.telemetry.otel

Module `loom_ia.adapters.telemetry.otel` de loom_v2 (fichier `src/loom_ia/adapters/telemetry/otel.py`).

Les spans de loom, envoyés à un collecteur OpenTelemetry en OTLP (fonction K4, point 29).

Une traduction, rien de plus : `telemetry.spans` a déjà tout décidé — les
noms, les parents, les durées, ce qui part et ce qui est masqué. Ce module
convertit un `SpanRecord` en span OTel **sans passer par un traceur** : les
identifiants viennent du journal, et c'est ce qui fait qu'un run se retrouve
dans le collecteur sous son propre identifiant.

- `trace_id` : le `root_run_id` de l'arbre (un UUID tient en 128 bits,
  pile ce qu'il faut) ; un sous-agent est donc dans la trace de son parent.
  Seul un UUID écrit sous sa forme canonique — celle des identifiants que loom
  tire — est pris tel quel ; tout autre identifiant (un run nommé par
  l'appelant, un UUID en majuscules ou sans tirets) passe par un condensé qui
  inclut le client : deux clients qui nomment pareil leurs runs ne partagent
  pas une trace ;
- `span_id` : les 64 bits de poids faible du `span_id` de loom (UUIDv7 :
  la partie aléatoire). Un identifiant qui n'est pas un UUID passe par un
  condensé.

L'envoi est celui du SDK : un `BatchSpanProcessor` qui regroupe, envoie
dans son propre fil et réessaie. Un lot qui ne part pas est dit **une fois**,
en français, avec les runs qu'il portait et la dernière erreur du SDK
(`Reported`) ; les lignes que le SDK écrit pendant cet envoi (« Transient
error … retrying », « Failed to export … ») ne sortent qu'en DEBUG.
`aclose` vide la file avant de fermer, dans le délai qu'on lui donne. Sa
file tient `MAX_QUEUE` spans (le défaut du SDK, 2 048, jetait une rafale de
fin de runs) ; `OTEL_BSP_MAX_QUEUE_SIZE`, s'il est posé, garde la main.
Une valeur d'attribut plus longue que `OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT`
caractères (`MAX_VALUE_LENGTH` sans la variable) est coupée, en le disant.

Demande l'extra `otel` (`opentelemetry-sdk`, `opentelemetry-exporter-otlp`).

## Constantes de `loom_ia.adapters.telemetry.otel`

- `SCOPE = InstrumentationScope('loom_ia')` : Portée d'instrumentation des spans de loom : `loom_ia`.
- `TRACES_PATH = '/v1/traces'` : Chemin OTLP des traces, ajouté à l'adresse du collecteur s'il n'y est pas.
- `MAX_QUEUE = 32768` : Spans que la file du processeur garde avant d'en jeter.
- `QUEUE_ENV = 'OTEL_BSP_MAX_QUEUE_SIZE'` : Variable du SDK qui fixe la taille de la file ; donnée, elle l'emporte sur `MAX_QUEUE`.
- `MAX_VALUE_LENGTH = 16384` : Longueur d'une valeur d'attribut au-delà de laquelle elle est coupée.
- `LENGTH_ENV = 'OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT'` : Variable d'OpenTelemetry qui fixe la longueur maximale d'une valeur ; donnée, elle l'emporte sur `MAX_VALUE_LENGTH`.
- `_LAST_WORD = 0.1` : Ce que garde la fermeture pour `shutdown` quand le vidage a pris tout le délai.
- `_HELD = threading.local()` : Ce que le SDK logue pendant un envoi, retenu pour être dit une fois (fil par fil : le processeur exporte dans le sien).

## Fonction `loom_ia.adapters.telemetry.otel.trace_id`

```python
def trace_id(value: str, tenant_id: str='') -> int
```

Identifiant de trace OTel (128 bits) d'un run racine de ce client.

Un UUID canonique est la trace elle-même ; le reste passe par un condensé
qui inclut le client.

## Fonction `loom_ia.adapters.telemetry.otel.span_id`

```python
def span_id(value: str) -> int
```

Identifiant de span OTel (64 bits) d'un identifiant de loom.

## Fonction `loom_ia.adapters.telemetry.otel.readable`

```python
def readable(record: SpanRecord, resource: Resource, *, max_length: int | None=None) -> ReadableSpan
```

Un span de loom, en span OTel prêt à exporter ; ses textes coupés à `max_length`.

## Fonction `loom_ia.adapters.telemetry.otel.parse_headers`

```python
def parse_headers(value: str | None) -> dict[str, str]
```

En-têtes au format de `OTEL_EXPORTER_OTLP_HEADERS` : `clé=valeur,clé=valeur`.

## Fonction `loom_ia.adapters.telemetry.otel.traces_url`

```python
def traces_url(endpoint: str) -> str
```

Adresse d'envoi en OTLP/HTTP : la variable générique nomme la racine du collecteur.

## Fonction `loom_ia.adapters.telemetry.otel._held`

```python
def _held(record: logging.LogRecord) -> bool
```

Filtre des loggers des exporteurs du SDK : retient ce qu'écrit un envoi en cours.

## Fonction `loom_ia.adapters.telemetry.otel._hold_sdk_logs`

```python
def _hold_sdk_logs() -> None
```

Pose `_held` sur les loggers des exporteurs du SDK, une fois chacun.

Un filtre de logger ne voit que ce qui est écrit sur ce logger même : il
est posé sur chacun de ceux que les modules d'export ont créés.

## Classe `loom_ia.adapters.telemetry.otel.Reported`

```python
class Reported(SpanExporter)
```

Un exporteur du SDK, dont l'échec se dit une fois, en français, avec ses runs.

À l'essai de la fonction K4 du 21/10, un collecteur arrêté donnait quatre lignes anglaises du
SDK (trois « Transient error … retrying », un « Failed to export … ») et
rien ne disait quel run n'était pas parti. Les lignes du SDK écrites pendant
l'envoi sont retenues ; si le lot part, elles ne sortent qu'en DEBUG,
sinon une seule ligne WARNING dit le collecteur, le nombre de spans, les
runs et la dernière erreur.

### Méthode `loom_ia.adapters.telemetry.otel.Reported.export`

```python
def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult
```

Envoie un lot ; un échec se dit en une ligne WARNING (collecteur, nombre de spans, runs, dernière erreur).

### Méthode `loom_ia.adapters.telemetry.otel.Reported.shutdown`

```python
def shutdown(self) -> None
```

Ferme l'exporteur enrobé.

### Méthode `loom_ia.adapters.telemetry.otel.Reported.force_flush`

```python
def force_flush(self, timeout_millis: int=30000) -> bool
```

Vide l'exporteur enrobé, en `timeout_millis` millisecondes au plus.

## Fonction `loom_ia.adapters.telemetry.otel._cause`

```python
def _cause(records: Sequence[logging.LogRecord]) -> str
```

La dernière erreur du SDK : la raison d'un réessai, sinon sa dernière ligne.

## Classe `loom_ia.adapters.telemetry.otel.OtelSpanSink`

```python
class OtelSpanSink
```

Un collecteur OTLP, servi par le processeur par lots du SDK.

### Méthode `loom_ia.adapters.telemetry.otel.OtelSpanSink.__init__`

```python
def __init__(self, exporter: SpanExporter, *, service_name: str='loom-ia', name: str='otel', schedule_delay: float=1.0) -> None
```

Branche l'exporteur sur le processeur par lots du SDK.

**Arguments :**

- `exporter` : L'exporteur du SDK (OTLP HTTP ou gRPC).
- `service_name` : Valeur de `service.name`.
- `name` : Nom du collecteur dans les logs.
- `schedule_delay` : Intervalle des envois par lots, en secondes.

### Méthode `loom_ia.adapters.telemetry.otel.OtelSpanSink.otlp`

```python
@classmethod
def otlp(cls, endpoint: str, *, protocol: str='http/protobuf', headers: Mapping[str, str] | None=None, timeout: float=10.0, service_name: str='loom-ia') -> OtelSpanSink
```

Collecteur joint en OTLP, par HTTP (protobuf) ou gRPC.

### Méthode `loom_ia.adapters.telemetry.otel.OtelSpanSink.name`

```python
@property
def name(self) -> str
```

Nom du collecteur dans les logs.

### Méthode `loom_ia.adapters.telemetry.otel.OtelSpanSink.export`

```python
def export(self, spans: Sequence[SpanRecord]) -> None
```

Met les spans dans la file du processeur ; ils partent par lots, sans attendre.

### Méthode `loom_ia.adapters.telemetry.otel.OtelSpanSink.aclose`

```python
async def aclose(self, grace: float) -> None
```

Vide la file puis ferme, en `grace` secondes au plus (et un dixième).

Ni `force_flush` — qui attend l'envoi en cours quel que soit son
délai — ni `shutdown` ne sont bornés : un collecteur muet les tenait
lot après lot. Ils tournent donc dans un fil démon, que la boucle
n'attend que le temps donné et qui ne retient pas la sortie du
process : ce qui n'est pas parti dans le délai est abandonné, et le log
le dit.

## Fonction `loom_ia.adapters.telemetry.otel._length_limit`

```python
def _length_limit() -> int
```

La longueur maximale d'une valeur : `OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT`, sinon la nôtre.

## Fonction `loom_ia.adapters.telemetry.otel._bounded`

```python
def _bounded(attributes: Mapping[str, AttributeValue], max_length: int | None) -> dict[str, AttributeValue]
```

Les attributs, chaque texte trop long coupé en le disant.

## Fonction `loom_ia.adapters.telemetry.otel._cut`

```python
def _cut(value: AttributeValue, max_length: int) -> AttributeValue
```

Un texte de plus de `max_length` caractères, ramené à cette longueur, marque comprise.

## Fonction `loom_ia.adapters.telemetry.otel._in_daemon_thread`

```python
def _in_daemon_thread(call: Callable[[], object]) -> asyncio.Future[None]
```

Lance `call` dans un fil démon ; le futur se résout quand il a fini.

## Fonction `loom_ia.adapters.telemetry.otel._nanos`

```python
def _nanos(moment: datetime) -> int
```

Nanosecondes depuis l'époque, sans passer par un flottant (qui arrondit).
