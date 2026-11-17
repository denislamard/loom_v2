# loom_ia.core.ports.exchanges

Module `loom_ia.core.ports.exchanges` de loom_v2 (fichier `src/loom_ia/core/ports/exchanges.py`).

Enregistrement des échanges bruts avec un fournisseur (point 31, phase 6.1b).

Un échange est une requête HTTP et sa réponse, telles qu'elles ont circulé :
méthode, adresse, en-têtes, corps. C'est l'équivalent de `log_response` en
V1, en opt-in (`telemetry.capture.raw_exchanges`) : de quoi déboguer un
fournisseur sans rejouer l'appel.

Le moteur ouvre un **registre** autour de chaque tentative d'appel
(`recording`) ; le client HTTP d'un adaptateur, s'il en trouve un dans le
contexte (`exchange_log`), y dépose ce qu'il a envoyé et reçu. Le moteur
n'a ainsi rien à savoir du transport, et un adaptateur rien à savoir du
journal. Sans registre ouvert, rien n'est gardé.

Un `ContextVar` et non un argument : l'appel HTTP a lieu tout au fond du
SDK, que loom ne contrôle pas, mais dans la même tâche que la tentative.

Chaque échange gardé devient un événement `model.exchanged`, après les
retouches (secrets retirés, octets de fichier remplacés, corps coupé au-delà
de `capture.raw_max_bytes`).

## Classe `loom_ia.core.ports.exchanges.RawExchange`

```python
@dataclass(frozen=True, slots=True)
class RawExchange
```

Une requête HTTP à un fournisseur et sa réponse, telles qu'elles ont circulé.

**Attributs :**

- `method` : Méthode HTTP.
- `url` : Adresse appelée, telle quelle (les secrets sont retirés plus tard, à l'écriture de `model.exchanged`).
- `request_body` : Corps de la requête, en octets.
- `response_body` : Corps de la réponse ; vide sans réponse.
- `status` : Statut HTTP ; `None` quand aucune réponse n'est arrivée (réseau, délai), et `error` dit alors pourquoi.
- `request_headers` : En-têtes de la requête.
- `response_headers` : En-têtes de la réponse.
- `duration_ms` : Durée de l'échange, en millisecondes.
- `error` : Erreur de transport, sans réponse.
- `synthetic` : Vrai pour l'échange d'un modèle simulé, qui n'a pas fait d'HTTP.

## Classe `loom_ia.core.ports.exchanges.ExchangeLog`

```python
class ExchangeLog
```

Le registre des échanges d'une tentative d'appel, dans l'ordre où ils se sont terminés.

**Attributs :**

- `exchanges` : Les échanges déposés par le client HTTP.

### Méthode `loom_ia.core.ports.exchanges.ExchangeLog.record`

```python
def record(self, exchange: RawExchange) -> None
```

Dépose un échange terminé ; appelé par le client HTTP d'un adaptateur.

## Fonction `loom_ia.core.ports.exchanges.exchange_log`

```python
def exchange_log() -> ExchangeLog | None
```

Le registre ouvert pour la tentative en cours, ou `None` : rien n'est alors gardé.

## Fonction `loom_ia.core.ports.exchanges.recording`

```python
@contextmanager
def recording(log: ExchangeLog) -> Generator[ExchangeLog]
```

Ouvre un registre pour ce qui s'exécute dans le bloc, dans cette tâche.

**Arguments :**

- `log` : Le registre de la tentative.

**Produit :**

Le même registre, ouvert jusqu'à la sortie du `with`.
