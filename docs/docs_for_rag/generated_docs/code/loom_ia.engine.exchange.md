# loom_ia.engine.exchange

Module `loom_ia.engine.exchange` de loom_v2 (fichier `src/loom_ia/engine/exchange.py`).

D'un échange brut à l'événement qui le garde (point 31, jalon J6.1b).

Ce qu'un adaptateur dépose dans le registre (`RawExchange`) est exactement
ce qui a circulé, secrets compris. Avant d'en faire un `model.exchanged`,
trois retouches, et seulement trois :

1. **les secrets partent** : un en-tête dont le nom dit une clé, un jeton,
   un mot de passe ou un cookie, un paramètre d'adresse du même genre
   (`?key=…`) et les identifiants d'une adresse (`https://user:pw@hôte`)
   sont remplacés par `[retiré]` — le nom, ou l'hôte, reste, pour qu'on
   voie qu'il y était. Un compteur de débit (`x-ratelimit-remaining-tokens`)
   n'est pas un jeton : il reste, c'est le diagnostic d'un refus pour débit ;
2. **les octets d'un fichier partent** : une URI `data:…;base64,…` (la
   forme des images chez OpenAI), un objet `{"type": "base64", "data": …}`
   (chez Anthropic) et un bloc `{"type": "inline_data", "data": …}` (celui
   de loom, que montre l'échange simulé) sont remplacés par leur type, leur
   taille et leur empreinte — le journal ne porte pas d'octets de fichier (point 16) ;
3. **un corps trop long est coupé** à `max_bytes`, sur une frontière de
   caractère, et l'événement le dit ; sa taille et son empreinte restent
   celles du corps entier.

Rien d'autre n'est touché : une signature de raisonnement, un identifiant,
une chaîne longue qui n'est pas un fichier restent tels quels.

## Constantes de `loom_ia.engine.exchange`

- `REMOVED = '[retiré]'` : Valeur mise à la place d'un secret.
- `_SECRET_NAME = re.compile('authorization|api[-_]?key|(?:^|[-_])key$|token|secret|password|passwd|cookie|signature|credential', re.IGNO…` : Un nom d'en-tête ou de paramètre qui porte un secret.
- `_RATE_LIMIT = re.compile('rate-?limit', re.IGNORECASE)` : Un compteur de débit : « tokens » y compte des jetons de modèle, il n'en est pas un.
- `_DATA_URI = re.compile('data:(?P<mime>[\\w.+-]+/[\\w.+-]+);base64,(?P<data>[A-Za-z0-9+/=]+)')` : Une URI de données en base64, telle qu'elle s'écrit dans un corps JSON.

## Classe `loom_ia.engine.exchange.Recorded`

```python
class Recorded
```

Un client de modèle dont les échanges bruts sont gardés, et leur borne.

Il ne fait rien de plus que le client qu'il enrobe : c'est `ModelCall`
qui, le reconnaissant, ouvre un registre autour de chaque tentative. Le
client enrobé, lui, doit savoir y déposer — un `RecordingClient` HTTP
pour un SDK, de lui-même pour le modèle simulé.

Réglage : `raw_exchanges` du modèle (`ModelSpec`) ; les échanges
deviennent des événements `model.exchanged`.

**Arguments :**

- `inner` : Le client enrobé.
- `raw_max_bytes` : Taille maximale d'un corps gardé, en octets ; au-delà, il est coupé.

**Attributs :**

- `inner` : Le client enrobé.
- `raw_max_bytes` : Taille maximale d'un corps gardé, en octets.

### Méthode `loom_ia.engine.exchange.Recorded.provider`

```python
@property
def provider(self) -> str
```

Fournisseur du client enrobé.

### Méthode `loom_ia.engine.exchange.Recorded.stream`

```python
def stream(self, request: ModelRequest) -> AsyncGenerator[ModelChunk]
```

Flux de la réponse du client enrobé, sans changement.

### Méthode `loom_ia.engine.exchange.Recorded.aclose`

```python
async def aclose(self) -> None
```

Ferme le client enrobé.

## Fonction `loom_ia.engine.exchange.exchanged`

```python
def exchanged(raw: RawExchange, *, attempt: int, model_id: str, provider: str, max_bytes: int, model: str | None=None) -> ModelExchanged
```

L'événement d'un échange : secrets et fichiers retirés, corps bornés.

**Arguments :**

- `raw` : L'échange brut déposé par l'adaptateur.
- `attempt` : Rang de la tentative, à partir de 1.
- `model_id` : Identifiant du modèle dans la config.
- `provider` : Fournisseur (`anthropic`, `openai`…).
- `max_bytes` : Taille maximale d'un corps, en octets.
- `model` : Nom du modèle chez le fournisseur, s'il est connu.

**Renvoie :**

L'événement `model.exchanged` : méthode, adresse et en-têtes sans
secrets, corps sans octets de fichier et coupés à `max_bytes`, taille
et empreinte SHA-256 des corps entiers, statut, durée, erreur.

## Fonction `loom_ia.engine.exchange.scrubbed_headers`

```python
def scrubbed_headers(headers: Mapping[str, str]) -> dict[str, str]
```

Les en-têtes, noms en minuscules, la valeur d'un en-tête secret remplacée par `[retiré]`.

## Fonction `loom_ia.engine.exchange.scrubbed_url`

```python
def scrubbed_url(url: str) -> str
```

L'adresse, sans ses identifiants ni la valeur des paramètres qui portent un secret.

**Arguments :**

- `url` : L'adresse appelée.

**Renvoie :**

L'adresse, identifiants et valeurs secrètes remplacés par
`[retiré]` ; la même chaîne, au caractère près, s'il n'y a rien à
retirer.

## Fonction `loom_ia.engine.exchange._secret`

```python
def _secret(name: str) -> bool
```

Vrai si le nom d'un en-tête ou d'un paramètre dit qu'il porte un secret.

## Fonction `loom_ia.engine.exchange.without_files`

```python
def without_files(text: str) -> str
```

Le corps, ses octets de fichier remplacés par une description.

La description est « [fichier <type>, <n> octets, sha256 <16 car.>…] ».

**Arguments :**

- `text` : Le corps, JSON ou non.

**Renvoie :**

Le corps retouché ; le même texte s'il ne porte pas de fichier.

## Fonction `loom_ia.engine.exchange._body`

```python
def _body(raw: bytes, max_bytes: int) -> tuple[str, int, str, bool]
```

Texte retouché, taille et empreinte du corps entier, et s'il a été coupé.

## Fonction `loom_ia.engine.exchange._file_object`

```python
def _file_object(table: dict[str, Any]) -> bool
```

Un objet qui porte les octets d'un fichier en base64 sous `data`.
