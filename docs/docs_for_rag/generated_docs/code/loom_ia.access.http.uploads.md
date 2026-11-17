# loom_ia.access.http.uploads

Module `loom_ia.access.http.uploads` de loom_v2 (fichier `src/loom_ia/access/http/uploads.py`).

Corps d'une demande de run : JSON, ou formulaire multipart avec des fichiers (fonction G1).

La route `POST /v1/agents/{name}/runs` accepte deux formes :

- `application/json` : un `RunRequest`, sans pièce jointe ;
- `multipart/form-data` : les mêmes champs en texte (`metadata` en JSON)
  et les fichiers sous le nom `attachments`, répété pour chacun ; un
  formulaire sans fichier (`application/x-www-form-urlencoded`) passe aussi.

    curl -F message='Que montre la photo ?' -F attachments=@photo.jpg \
         http://127.0.0.1:8000/v1/agents/assistant/runs

Les limites viennent de `execution.attachments` : un envoi plus gros que
`max_files` fichiers de `max_bytes` est refusé sur son en-tête (413), avant
d'être lu ; sans `Content-Length` (envoi en morceaux), il est compté à mesure
qu'il arrive et refusé au même plafond, avant d'être écrit sur disque. Un
fichier de trop est refusé dès l'en-tête de sa partie, et chaque fichier n'est
lu que jusqu'à sa limite. Le contrôle du contenu (signature, type accepté)
reste celui du moteur.

Tous les corps, ceux-là compris, passent par `BodyLimit` : JSON, déclencheurs et
MCP n'ont pas de fichier à compter, mais `server.http.max_body_bytes` les borne.

## Constantes de `loom_ia.access.http.uploads`

- `JSON = 'application/json'` : Type d'un corps JSON.
- `MULTIPART = 'multipart/form-data'` : Type d'un formulaire multipart, avec des fichiers.
- `FORM = 'application/x-www-form-urlencoded'` : Type d'un formulaire sans fichier.
- `FILES_FIELD = 'attachments'` : Nom des parties qui portent les fichiers.
- `FORM_OVERHEAD = 256 * 1024` : Place laissée aux champs texte et aux en-têtes des parties.
- `MAX_FIELDS = 16` : Champs texte acceptés au plus dans un formulaire.
- `UNDECLARED = frozenset({'', 'application/octet-stream'})` : Type envoyé quand le client ne sait pas : la signature décidera.
- `RUN_BODY = {'requestBody': {'required': True, 'content': {JSON: {'schema': RunRequest.model_json_schema()}, MULTIPART: {'schema': …` : Description OpenAPI des deux corps acceptés.

## Fonction `loom_ia.access.http.uploads.run_request`

```python
async def run_request(request: Request, policy: AttachmentPolicy) -> tuple[RunRequest, list[Attachment]]
```

Demande de run et pièces jointes, lues selon le type du corps.

Un corps invalide donne 422, un type de corps inconnu 415, un envoi trop
gros 413. Une pièce jointe refusée lève `AttachmentError`.

## Fonction `loom_ia.access.http.uploads._form`

```python
@asynccontextmanager
async def _form(request: Request, policy: AttachmentPolicy) -> AsyncGenerator[FormData]
```

Le formulaire du corps, lu jusqu'aux limites ; un refus de la lecture devient le nôtre.

Starlette arrête la lecture au fichier de trop, dès l'en-tête de sa partie et
avant d'en écrire un octet ; c'est alors le refus du moteur (422, son message)
qui part. Un formulaire que l'analyseur ne sait pas lire est une demande mal
formée (400), non un conflit, que l'analyseur lève son erreur ou que Starlette
(1.7 et après) la convertisse lui-même en 400.

## Fonction `loom_ia.access.http.uploads._ceiling`

```python
def _ceiling(policy: AttachmentPolicy) -> int
```

Octets au-delà desquels un envoi ne peut pas tenir dans les limites.

## Fonction `loom_ia.access.http.uploads.declared_length`

```python
def declared_length(headers: Mapping[str, str]) -> int | None
```

Longueur annoncée par `Content-Length`, ou `None` sans en-tête lisible.

Seuls les chiffres ASCII comptent (`str.isdigit` accepte « ² », que `int`
refuse), et pas plus de 18 : au-delà l'en-tête est ignoré, et le corps compté
à mesure qu'il arrive.

## Fonction `loom_ia.access.http.uploads._check_length`

```python
def _check_length(request: Request, policy: AttachmentPolicy) -> None
```

Refuse sur son en-tête un envoi qui ne peut pas tenir dans les limites.

## Classe `loom_ia.access.http.uploads._Bounded`

```python
class _Bounded
```

`receive` d'une requête qui refuse (413) un corps plus long que `limit` octets.

`Content-Length` est contrôlé avant la lecture ; un envoi en morceaux n'en
a pas, et ne se mesure qu'en arrivant.

## Classe `loom_ia.access.http.uploads._TooLarge`

```python
class _TooLarge(Exception)
```

Le corps a dépassé le plafond pendant sa lecture.

## Classe `loom_ia.access.http.uploads.BodyLimit`

```python
class BodyLimit
```

Middleware ASGI : refuse (413) toute requête dont le corps dépasse `limit` octets.

Il vaut pour tout ce que l'application sert — JSON, déclencheurs, MCP — et il
est posé avant elle, donc avant la clé, la route et le protocole. Une longueur
annoncée au-delà du plafond est refusée sans rien lire ; sans longueur (envoi
en morceaux), le corps est compté à mesure qu'il arrive, et la lecture s'arrête
au dépassement. Ce refus est alors la seule réponse : celle que l'application
aurait tirée de l'échec de sa lecture (une erreur 500 du transport MCP, par
exemple) est écartée.

### Méthode `loom_ia.access.http.uploads.BodyLimit.__call__`

```python
async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None
```

Laisse passer une requête dont le corps tient sous `limit` octets ; sinon, répond 413.

Une longueur annoncée trop grande est refusée sans rien lire ; sinon le
corps est compté à mesure, et la lecture s'arrête au dépassement.

## Fonction `loom_ia.access.http.uploads._attachment`

```python
async def _attachment(upload: UploadFile, policy: AttachmentPolicy) -> Attachment
```

Pièce jointe d'une partie, lue au plus jusqu'à la limite de taille.
