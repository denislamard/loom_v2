# loom_ia.adapters.firecracker.session

Module `loom_ia.adapters.firecracker.session` de loom_v2 (fichier `src/loom_ia/adapters/firecracker/session.py`).

Une session execd : une connexion au service d'exécution de l'invité.

Le protocole est celui d'execd, `firecracker/service/` dans le dépôt (version 1) :

    trame = u32 BE longueur d'en-tête | en-tête JSON | u32 BE longueur du corps | corps

Une connexion est une session : execd lui donne un dossier de travail
(`code/ in/ out/ work/`) qu'il efface quand elle se ferme. Une requête, une
réponse, dans l'ordre ; chaque requête porte un `seq` que la réponse doit
rendre, ce qui détecte un flux désynchronisé.

Deux sortes d'échec, à ne pas confondre :

- `ExecdError` : execd a répondu non (chemin refusé, extension native,
  session de trop…). La session reste utilisable.
- `ProtocolError` ou `TimeoutError` : trame illisible, réponse
  désynchronisée, connexion coupée, délai dépassé, appel annulé. La session
  est fermée ; execd arrête le job en cours et efface son dossier. (Un execd
  d'avant le 08/10 ne voit la fermeture qu'à la fin du job, au plus tard à
  son `wall_ms`.)

Un job qui échoue (exception, délai, mémoire) n'est ni l'un ni l'autre :
`exec` rend une `Execution` dont `ok` est faux et `error` dit pourquoi,
avec les sorties du job — c'est ce que le modèle doit voir pour se corriger.

## Constantes de `loom_ia.adapters.firecracker.session`

- `PROTOCOL = 1` : Version du protocole parlée par ce client ; hello la vérifie.
- `MAX_HEADER = 1 << 20` : Bornes de cadrage d'execd : au-delà, c'est un défaut de protocole.
- `MAX_BODY = 8 << 20` : Taille maximale du corps d'une trame (voir `MAX_HEADER`) ; l'invité peut en annoncer une autre au hello.
- `CHUNK = 1 << 20` : Taille des morceaux envoyés et demandés.
- `REQUEST_TIMEOUT = 30.0` : Délai d'une requête courte : hello, put, get, reset.
- `DEFAULT_WALL_MS = 30000` : wall_ms qu'execd applique quand on ne demande rien.
- `EXEC_MARGIN = 30.0` : Marge ajoutée au wall_ms d'un job : attente derrière les jobs des autres sessions (execd n'en exécute qu'un à la fois) et 5 s de mise à mort.
- `MAX_FILE = 16 << 20` : Ce que get_file accepte de rapatrier par défaut.
- `_NO_HELLO = Hello(protocol=0, session_id='', python='', runner='', max_body=MAX_BODY, max_code_bytes=0, max_upload_total=0, ceiling…` : Valeur provisoire, le temps du hello.

## Classe `loom_ia.adapters.firecracker.session.ProtocolError`

```python
class ProtocolError(Exception)
```

Dialogue impossible avec execd ; la session est fermée.

## Classe `loom_ia.adapters.firecracker.session.ExecdError`

```python
class ExecdError(Exception)
```

Refus d'execd, ou cause de l'échec d'un job : `kind` est stable, `message` lisible.

## Classe `loom_ia.adapters.firecracker.session.Hello`

```python
@dataclass(frozen=True, slots=True)
class Hello
```

Ce que l'invité annonce à l'ouverture de la session.

**Attributs :**

- `protocol` : Version du protocole d'execd.
- `session_id` : Identifiant de la session chez execd.
- `python` : Version de Python dans l'invité, et du service.
- `runner` : Exécuteur des jobs, tel qu'execd l'annonce.
- `max_body` : Taille maximale du corps d'une trame.
- `max_code_bytes` : Taille maximale d'un module déposé.
- `max_upload_total` : Taille maximale de ce qu'une session dépose en tout.
- `ceilings` : Plafonds des limites d'un job : au-delà, execd ramène au plafond.

## Classe `loom_ia.adapters.firecracker.session.Output`

```python
@dataclass(frozen=True, slots=True)
class Output
```

Un fichier produit par le job dans `out/`.

**Attributs :**

- `path` : Chemin dans `out/`.
- `size` : Taille, en octets.
- `sha256` : Empreinte SHA-256 du contenu.

## Classe `loom_ia.adapters.firecracker.session.Execution`

```python
@dataclass(frozen=True, slots=True)
class Execution
```

Le compte rendu d'un `tool.exec`.

**Attributs :**

- `ok` : Le job a réussi.
- `result` : Valeur JSON rendue par le point d'entrée, si ok.
- `error` : Pourquoi le job a échoué, si pas ok.
- `stdout` : Sortie standard du job.
- `stderr` : Sortie d'erreur du job.
- `stdout_truncated` : Sortie standard coupée par l'invité.
- `stderr_truncated` : Sortie d'erreur coupée par l'invité.
- `duration_ms` : Durée du job, en millisecondes.
- `limits_applied` : Limites réellement appliquées par l'invité (après plafonnement).
- `outputs` : Fichiers écrits dans `out/`.
- `outputs_skipped` : Entrées de out/ ignorées : liens symboliques, fichiers spéciaux.

## Fonction `loom_ia.adapters.firecracker.session.encode_frame`

```python
def encode_frame(header: Mapping[str, object], body: bytes=b'') -> bytes
```

Une trame complète ; `ProtocolError` si elle dépasse les bornes d'execd.

## Fonction `loom_ia.adapters.firecracker.session.read_frame`

```python
async def read_frame(reader: asyncio.StreamReader) -> tuple[dict[str, object], bytes]
```

La trame suivante : en-tête JSON (un objet) et corps brut.

## Fonction `loom_ia.adapters.firecracker.session._execution`

```python
def _execution(reply: Mapping[str, object]) -> Execution
```

Le compte rendu d'un `tool.exec`, lu dans la réponse d'execd.

## Classe `loom_ia.adapters.firecracker.session.Session`

```python
class Session
```

Une connexion à execd, ouverte par `Session.open` sur un flux déjà établi.

Le flux vient de `Vm.connect(port)` (vsock) ou d'une socket Unix (execd
lancé avec `EXECD_UDS`, sans VM). Les requêtes passent une à une, même
si plusieurs tâches appellent la même session.

### Méthode `loom_ia.adapters.firecracker.session.Session.open`

```python
@classmethod
async def open(cls, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, *, wait: float=REQUEST_TIMEOUT) -> Self
```

Salue execd et vérifie la version du protocole ; ferme le flux en cas d'échec.

`ExecdError` de genre `busy` si l'invité a déjà toutes les
sessions qu'il accepte.

### Méthode `loom_ia.adapters.firecracker.session.Session.closed`

```python
@property
def closed(self) -> bool
```

Vrai si la session est fermée.

### Méthode `loom_ia.adapters.firecracker.session.Session.close`

```python
async def close(self) -> None
```

Ferme la connexion : execd arrête le job en cours et efface le dossier de la session.

Le job est tué aussitôt, et sa place d'exécution rendue aux autres
sessions. Un execd d'avant le 08/10 ne voyait la fermeture qu'à la fin
du job, au plus tard à son `wall_ms`.

### Méthode `loom_ia.adapters.firecracker.session.Session.put_code`

```python
async def put_code(self, path: str, data: bytes) -> None
```

Dépose un fichier dans `code/` (`.py`, `.json`, `.txt`… ; jamais de natif).

### Méthode `loom_ia.adapters.firecracker.session.Session.put_file`

```python
async def put_file(self, path: str, data: bytes) -> None
```

Dépose un fichier d'entrée dans `in/`.

### Méthode `loom_ia.adapters.firecracker.session.Session.exec`

```python
async def exec(self, entrypoint: str, *, args: Mapping[str, object] | None=None, limits: Mapping[str, int] | None=None, env: Mapping[str, str] | None=None, wait: float | None=None) -> Execution
```

Exécute `module:fonction` de `code/` avec ces arguments nommés.

`limits` propose (`wall_ms`, `cpu_ms`, `mem_bytes`…), l'invité
plafonne ; `limits_applied` dit ce qui a été appliqué. L'attente de la
réponse (`wait`) vaut par défaut `wall_ms` plus `EXEC_MARGIN` ;
dépassée, la session est fermée et `TimeoutError` levée.

### Méthode `loom_ia.adapters.firecracker.session.Session.get_file`

```python
async def get_file(self, path: str, *, max_bytes: int=MAX_FILE) -> bytes
```

Rapatrie un fichier de `out/` produit par le dernier job.

`ValueError` s'il dépasse `max_bytes` : le manifeste de
l'`Execution` donne la taille avant de décider.

### Méthode `loom_ia.adapters.firecracker.session.Session.reset`

```python
async def reset(self) -> None
```

Vide `code/ in/ out/ work/` et tue un job en cours, sans fermer la session.

### Méthode `loom_ia.adapters.firecracker.session.Session._call`

```python
async def _call(self, method: str, header: Mapping[str, object], body: bytes=b'', *, wait: float=REQUEST_TIMEOUT) -> tuple[dict[str, object], bytes]
```

Une requête et sa réponse, sous verrou, dans le délai `wait`.

Un délai dépassé, une trame illisible, une connexion perdue ou une réponse
désynchronisée (`seq`) ferment la session ; une annulation la coupe
sur-le-champ.

**Lève :**

- `TimeoutError` : Pas de réponse dans le délai.
- `ProtocolError` : Session fermée, trame illisible, connexion perdue ou réponse désynchronisée.
