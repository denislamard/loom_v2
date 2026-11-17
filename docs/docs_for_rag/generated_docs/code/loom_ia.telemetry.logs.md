# loom_ia.telemetry.logs

Module `loom_ia.telemetry.logs` de loom_v2 (fichier `src/loom_ia/telemetry/logs.py`).

Configuration optionnelle des logs de loom-ia (point 45).

La librairie n'installe aucun handler : elle écrit dans les loggers
`loom_ia.*` et laisse l'application hôte décider de l'affichage.
`configure_logging` sert à la CLI et au mode service, qui sont eux-mêmes
l'application.

Le contexte d'exécution passe par `extra` :

    logger.info("appel modèle", extra={"run_id": run_id, "span_id": span_id})

Les champs `run_id`, `span_id` et `tenant_id` sont repris par les deux
formats quand ils sont présents.

Le format `console` est lu dans un terminal : un message qui cite un modèle,
un outil ou un serveur y montre ses caractères de contrôle au lieu de les
exécuter (`printable`, qui sert aussi à la CLI). Le format `json` est pour
les machines, et JSON échappe déjà les contrôles C0.

## Constantes de `loom_ia.telemetry.logs`

- `SDK_LOGGER = 'opentelemetry'` : Le SDK OpenTelemetry, qui envoie les spans aux collecteurs.
- `_HANDLER_MARK = '_loom_ia_configured'` : Marqueur posé sur le handler installé par configure_logging, pour le retrouver et le remplacer lors d'un nouvel appel (idempotence).
- `_CONTROLS = {code: f'\\x{code:02x}' for code in (*range(9), *range(11, 32), *range(127, 160))}` : Caractères qu'un terminal exécute au lieu de les afficher — C0 sauf la tabulation et le saut de ligne, DEL, C1 —, et ce qui les montre à leur place.

## Fonction `loom_ia.telemetry.logs.printable`

```python
def printable(text: str) -> str
```

Le texte pour un terminal : chaque caractère de contrôle montré (`\x1b`), pas exécuté.

Ce que rendent un modèle, un outil ou un serveur peut porter des séquences
qui pilotent le terminal (presse-papier, écran, titre). Montrées, elles se
lisent sans agir. `\n` et `\t` restent ce qu'ils sont.

**Arguments :**

- `text` : Le texte.

## Fonction `loom_ia.telemetry.logs._context`

```python
def _context(record: logging.LogRecord) -> dict[str, object]
```

Champs de contexte présents dans l'enregistrement (via `extra`).

## Classe `loom_ia.telemetry.logs.ConsoleFormatter`

```python
class ConsoleFormatter(logging.Formatter)
```

Une ligne lisible : horodatage, niveau, logger, message, contexte.

### Méthode `loom_ia.telemetry.logs.ConsoleFormatter.format`

```python
def format(self, record: logging.LogRecord) -> str
```

La ligne, suivie du contexte `[clé=valeur …]` ; les caractères de contrôle sont montrés.

## Classe `loom_ia.telemetry.logs.JsonFormatter`

```python
class JsonFormatter(logging.Formatter)
```

Un objet JSON par ligne, pour les collecteurs de logs.

### Méthode `loom_ia.telemetry.logs.JsonFormatter.format`

```python
def format(self, record: logging.LogRecord) -> str
```

Un objet JSON : `ts`, `level`, `logger`, `message`, contexte, `audit`, `exception`.

## Fonction `loom_ia.telemetry.logs.configure_logging`

```python
def configure_logging(level: int | str=logging.INFO, *, format: LogFormat='console', stream: TextIO | None=None) -> logging.Handler
```

Affiche les logs de loom-ia sur `stream` (stderr par défaut).

Seuls les loggers `loom_ia` et `opentelemetry` sont configurés : le
logger racine de l'application n'est pas touché. Le second est celui du
SDK qui envoie les spans aux collecteurs (`telemetry.exporters`) : ses
lignes prennent le format et le niveau de loom (à l'essai de la fonction K4 du 21/10, elles
sortaient brutes, hors de `telemetry.logging`). La propagation est
coupée pour éviter les doublons si l'application a aussi configuré le
logger racine.

Un nouvel appel remplace le handler installé par l'appel précédent.
Renvoie le handler installé.

**Arguments :**

- `level` : Niveau (`INFO` par défaut).
- `format` : `console` ou `json`.
- `stream` : Flux de sortie ; `sys.stderr` par défaut.

**Renvoie :**

Le handler installé.

**Lève :**

- `ValueError` : Format inconnu.
