# loom_ia.engine.media

Module `loom_ia.engine.media` de loom_v2 (fichier `src/loom_ia/engine/media.py`).

Fichiers dans les appels de modèle : références résolues selon les capacités (point 14).

Le journal et le `RunState` ne portent que des références
(`ArtifactRefBlock`). Juste avant l'appel, chaque référence est remplacée
selon les capacités du modèle :

- **image, modèle avec `vision`** : les octets, lus dans le stockage
  d'artefacts (`InlineDataBlock`, envoyé en base64 par l'adaptateur), après
  contrôle du format (`image_formats`) et de la taille
  (`max_image_bytes`) ; une pièce jointe au format ou à la taille refusés
  donne une erreur explicite avant l'appel, une image rendue par un outil une
  mention (voir plus bas) ;
- **sinon** (modèle sans vision, fichier qui n'est pas une image) : une
  mention textuelle, qui dit au modèle qu'un fichier existe sans le lui
  montrer.

Une image renvoyée par un outil reste dans son résultat si le modèle
l'accepte là (`tool_result_media`) ; sinon, le résultat la mentionne et
elle part dans un message utilisateur placé après les résultats du tour.
Une image renvoyée par un outil que le modèle refuse (format, taille)
devient une mention qui dit pourquoi : l'outil n'a pas choisi le modèle, et
une erreur ferait échouer le run à chaque appel suivant, puisque l'image
reste dans l'historique. Une pièce jointe refusée, elle, reste une erreur :
celui qui l'a jointe peut en joindre une autre.

Images des tours précédents (fonction F4, `sessions.media.keep_images`) : par
défaut (`all`), toutes sont résolues comme celles du run, à chaque appel.
Avec `last_run`, l'historique de la session les remplace par une note
avant de former la requête (`earlier_images`) ; seules les images du run en
cours partent en octets. Le journal et les snapshots gardent les références.

L'empreinte de la requête (`request_hash`) est calculée sur la requête
avant résolution : elle ne dépend pas des octets des fichiers.

## Constantes de `loom_ia.engine.media`

- `IMAGE_TOKENS = 1600` : Tokens comptés par image dans l'estimation de la fenêtre de contexte (fonction B7).  Ordre de grandeur d'une image d'un mégapixel chez Anthropic.

## Fonction `loom_ia.engine.media.size_label`

```python
def size_label(size: int | None) -> str
```

Taille lisible : octets, Ko ou Mo.

## Fonction `loom_ia.engine.media.describe`

```python
def describe(block: ArtifactRefBlock) -> str
```

Nom, type et taille d'un fichier : `photo.jpg (image/jpeg, 45 Ko)`.

## Fonction `loom_ia.engine.media.earlier_images`

```python
def earlier_images(messages: Sequence[Message]) -> list[Message]
```

Historique où chaque image d'un tour précédent devient une mention (fonction F4).

Pièce jointe ou image rendue par un outil : le modèle apprend qu'elle a
existé (nom, type, taille) sans la recevoir ; elle n'est pas relue dans le
stockage. Les autres fichiers gardent leur référence, que la résolution
mentionne déjà.

Utilisé avec `sessions.media.keep_images: last_run`.

**Arguments :**

- `messages` : L'historique de la session, avant le run en cours.

**Renvoie :**

Les messages ; chaque image devient une « [Note du système : ce
message contenait l'image … Elle a été retirée de l'historique …] ».

## Fonction `loom_ia.engine.media.has_refs`

```python
def has_refs(messages: Sequence[Message]) -> bool
```

Vrai si des références de fichiers sont à résoudre dans ces messages.

## Classe `loom_ia.engine.media.MediaResolver`

```python
class MediaResolver
```

Remplace les références de fichiers d'une requête, pour un modèle donné.

**Arguments :**

- `spec` : Le modèle : `capabilities.vision`, `image_formats`, `max_image_bytes`, `tool_result_media`.
- `store` : Magasin de fichiers où lire les images ; `None` : une image à envoyer est une erreur.

**Attributs :**

- `spec` : Le modèle.
- `store` : Magasin de fichiers.

### Méthode `loom_ia.engine.media.MediaResolver.sees`

```python
def sees(self, block: ArtifactRefBlock) -> bool
```

Vrai si le modèle reçoit les octets de ce fichier, pas une mention.

### Méthode `loom_ia.engine.media.MediaResolver.images`

```python
def images(self, request: ModelRequest) -> int
```

Nombre d'images que le modèle recevra (estimation de la fenêtre).

**Arguments :**

- `request` : La requête, références non résolues.

### Méthode `loom_ia.engine.media.MediaResolver.resolve`

```python
async def resolve(self, request: ModelRequest) -> ModelRequest
```

Requête prête à partir, chaque référence remplacée selon les capacités du modèle.

Une image vue par le modèle devient ses octets (`InlineDataBlock`) ;
un autre fichier, ou une image que le modèle ne voit pas, une mention
« [fichier …] » ou « [image … : non visible par ce modèle] ». Les
images d'un résultat d'outil que le modèle n'accepte pas là partent
dans un message `user` placé après les résultats du tour.

**Arguments :**

- `request` : La requête, références non résolues.

**Renvoie :**

La requête résolue ; la même s'il n'y a pas de référence.

**Lève :**

- `ModelError` : `invalid_request` pour une pièce jointe au format ou à la taille refusés par le modèle, ou introuvable dans le stockage. Une image rendue par un outil n'en lève pas : elle devient une mention qui dit pourquoi.

### Méthode `loom_ia.engine.media.MediaResolver._resolved`

```python
async def _resolved(self, block: ArtifactRefBlock, verb: str, *, refused_as_text: bool=False) -> TextBlock | InlineDataBlock
```

Une référence résolue : octets de l'image, ou mention.

**Arguments :**

- `block` : La référence.
- `verb` : Origine dite dans la mention (« jointe », « rendue »…).
- `refused_as_text` : Vrai pour une image rendue par un outil : un refus devient une mention au lieu d'une erreur.

**Lève :**

- `ModelError` : Image refusée (format, taille) ou illisible, si `refused_as_text` est faux, ou introuvable.

## Fonction `loom_ia.engine.media._without_images`

```python
def _without_images(message: Message) -> Message
```

Le message, chaque image (pièce jointe ou rendue par un outil) remplacée par une note.

## Classe `loom_ia.engine.media.ConversationFiles`

```python
class ConversationFiles
```

Les fichiers d'une conversation, pour les outils (`SeenFiles`).

Ce sont les références que portent ses messages : pièces jointes, et
fichiers rendus par les outils, ceux d'un sous-agent compris. Un outil ne
relit que ceux-là : nommer un fichier d'une autre session, ou deviner un
condensé, ne mène à rien. Une référence d'un autre client ou d'une autre
session (un message construit par l'application, par exemple) est ignorée.

**Arguments :**

- `messages` : Les messages de la conversation.
- `store` : Magasin de fichiers où les relire.
- `tenant` : Client de la session.
- `session` : Session ; seules ses références sont retenues.

### Méthode `loom_ia.engine.media.ConversationFiles.find`

```python
def find(self, name: str) -> ArtifactRefBlock | None
```

Le plus récent du nom donné ; à défaut, celui dont c'est le nom dans le stockage.

**Arguments :**

- `name` : Nom du fichier (`name` de la référence, ou dernier segment de son URI).

**Renvoie :**

La référence ; `None` si aucun fichier de la conversation ne
porte ce nom.

### Méthode `loom_ia.engine.media.ConversationFiles.names`

```python
def names(self) -> tuple[str, ...]
```

Noms des fichiers de la conversation, sans doublon, dans l'ordre d'apparition.

### Méthode `loom_ia.engine.media.ConversationFiles.read`

```python
async def read(self, file: ArtifactRefBlock, *, max_bytes: int) -> bytes
```

Octets d'un fichier de la conversation.

**Arguments :**

- `file` : La référence, obtenue par `find`.
- `max_bytes` : Taille maximale acceptée, en octets.

**Renvoie :**

Le contenu du fichier.

**Lève :**

- `ValueError` : Fichier plus gros que `max_bytes`.
- `LookupError` : Fichier absent du stockage.

## Fonction `loom_ia.engine.media._refs`

```python
def _refs(blocks: Sequence[ContentBlock] | Sequence[OutputBlock]) -> list[ArtifactRefBlock]
```

Références de fichiers de ces blocs, résultats d'outils compris.
