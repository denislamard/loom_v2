# loom_ia.core.model.media

Module `loom_ia.core.model.media` de loom_v2 (fichier `src/loom_ia/core/model/media.py`).

Fichiers : types reconnus, pièces jointes, références d'artefacts (fonctions G1 et G2, point 14).

Le type d'une pièce jointe se déduit de sa signature binaire (acquis de V1),
jamais de son nom : un type déclaré qui ne correspond pas est refusé.

Un artefact est désigné par une URI adressée par son contenu :

    artifact://<client>/<session>/<sha256>.<ext>

Un même fichier n'est donc stocké qu'une fois par session, le rejeu retrouve
les mêmes URI, et la suppression d'une session (RGPD) touche un seul
dossier. Client et session sont encodés pour tenir dans une URI et un chemin.

Les pièces jointes acceptées à l'entrée d'un run se règlent sous
`execution.attachments` (`AttachmentPolicy`) : par défaut, des images
JPEG, PNG, GIF ou WebP, 5 Mio par fichier, 10 fichiers au plus.

## Constantes de `loom_ia.core.model.media`

- `ARTIFACT_SCHEME = 'artifact://'` : Préfixe des URI d'artefacts.
- `ArtifactOrigin = Literal['attachment', 'tool_output', 'offload']` : Origine d'un artefact : pièce jointe de la demande, fichier produit par un outil, ou résultat déporté.
- `KeepImages = Literal['last_run', 'all']` : Images des tours précédents d'une session dans une requête (`sessions.media.keep_images`, fonction F4).  `last_run` : seules celles du run en cours partent en octets ; `all` (défaut) : toutes.
- `MOVED_IMAGES = 'Images renvoyées par les outils ci-dessus :'` : Début du message utilisateur qui porte, après les résultats d'un tour, les images que le modèle n'accepte pas dans un résultat d'outil (point 15).
- `ImageFormat = Literal['jpeg', 'png', 'gif', 'webp']` : Nom court d'un format d'image, tel que `image_formats` des modèles le déclare.
- `IMAGE_TYPES = {'image/jpeg': 'jpeg', 'image/png': 'png', 'image/gif': 'gif', 'image/webp': 'webp'}` : Types d'image acceptés partout (jalon J2), et leur nom court.
- `OCTETS = 'application/octet-stream'` : Type d'un fichier dont on ne sait rien.

## Fonction `loom_ia.core.model.media.sniff`

```python
def sniff(data: bytes) -> str | None
```

Type MIME d'une image d'après sa signature binaire, jamais d'après son nom.

**Arguments :**

- `data` : Les octets du fichier.

**Renvoie :**

`image/jpeg`, `image/png`, `image/gif` ou `image/webp` ;
`None` pour une autre signature.

## Fonction `loom_ia.core.model.media.is_image`

```python
def is_image(media_type: str) -> bool
```

Vrai pour un type d'image reconnu (JPEG, PNG, GIF, WebP).

## Fonction `loom_ia.core.model.media.extension`

```python
def extension(media_type: str) -> str
```

Extension d'un fichier de ce type, sans le point ; `bin` pour un type inconnu.

Les paramètres du type (`text/csv; charset=utf-8`) et la casse ne comptent
pas. Types connus : images, SVG, texte, CSV, Markdown, HTML, XML, JSON, PDF,
ZIP, XLSX, DOCX, PPTX, WAV, MP3.

## Fonction `loom_ia.core.model.media.media_type_of`

```python
def media_type_of(name: str, data: bytes) -> str
```

Type d'un fichier produit par du code qu'on ne connaît pas.

La signature d'une image l'emporte sur le nom. Un nom d'image que la
signature ne confirme pas donne `application/octet-stream` : sinon, un
« graphe.png » qui n'en est pas un partirait comme image vers un modèle
qui les voit, et le fournisseur refuserait la requête. Hors images, le
type vient de l'extension (table de loom, pas celle du système : le même
fichier a le même type partout).

**Arguments :**

- `name` : Nom du fichier.
- `data` : Ses octets.

**Renvoie :**

Le type MIME ; `application/octet-stream` pour une extension
inconnue ou une fausse image.

## Fonction `loom_ia.core.model.media._component`

```python
def _component(value: str) -> str
```

Segment d'URI et de chemin sûr : encodé, jamais « . » ni « .. ».

## Fonction `loom_ia.core.model.media.artifact_uri`

```python
def artifact_uri(tenant: str, session: str, data: bytes, media_type: str) -> str
```

URI d'un artefact, adressée par son contenu, dans l'espace du client et de la session.

**Arguments :**

- `tenant` : Le client.
- `session` : La session.
- `data` : Les octets du fichier.
- `media_type` : Son type, qui donne l'extension.

**Renvoie :**

`artifact://<client>/<session>/<sha256>.<ext>`, client et session
encodés.

## Classe `loom_ia.core.model.media.ArtifactLocation`

```python
@dataclass(frozen=True, slots=True)
class ArtifactLocation
```

Parties d'une URI d'artefact, décodées.

**Attributs :**

- `tenant` : Le client.
- `session` : La session.
- `name` : Le nom du fichier : `<sha256>.<ext>`.

### Méthode `loom_ia.core.model.media.ArtifactLocation.parse`

```python
@classmethod
def parse(cls, uri: str) -> Self
```

Découpe une URI d'artefact.

**Arguments :**

- `uri` : L'URI, `artifact://<client>/<session>/<nom>`.

**Renvoie :**

Ses parties, client et session décodés.

**Lève :**

- `ValueError` : L'URI n'a pas cette forme, ou son nom tente de sortir de son dossier.

### Méthode `loom_ia.core.model.media.ArtifactLocation.relative_path`

```python
def relative_path(self) -> Path
```

Chemin relatif sûr (segments encodés), sous la racine d'un stockage.

Lève `ValueError` si le client ou la session est vide : un segment vide
s'effacerait du chemin, et le dossier d'une session deviendrait celui de
son client, voire la racine.

## Classe `loom_ia.core.model.media.ArtifactRecord`

```python
class ArtifactRecord(DomainModel)
```

Un artefact du run, tel que le journal le décrit (`artifact.stored`).

C'est ce que listent `RunResult.produced`, `loom sessions files` et
`GET /v1/sessions/{id}/files`.

**Attributs :**

- `uri` : Référence du fichier.
- `media_type` : Type MIME.
- `size` : Taille, en octets.
- `name` : Nom du fichier.
- `origin` : `attachment`, `tool_output` ou `offload`.
- `call_id` : Appel d'outil qui l'a produit (sortie ou déport).

## Classe `loom_ia.core.model.media.AttachmentPolicy`

```python
class AttachmentPolicy(DomainModel)
```

Pièces jointes acceptées à l'entrée d'un run (`execution.attachments`, fonction G1).

Le format se reconnaît à la signature binaire ; PDF, audio et autres
fichiers ne sont pas encore pris en charge.

**Attributs :**

- `max_bytes` : Taille maximale d'une pièce jointe, en octets. Défaut : 5 Mio, la limite d'une image chez Anthropic.
- `types` : Types MIME acceptés, parmi les images reconnues. Défaut : JPEG, PNG, GIF, WebP.
- `max_files` : Nombre de pièces jointes d'un run ; borne aussi la taille d'un envoi REST. Défaut : 10.

**Lève :**

- `ValueError` : Un type de `types` n'est pas une image reconnue.

### Méthode `loom_ia.core.model.media.AttachmentPolicy._check_types`

```python
@model_validator(mode='after')
def _check_types(self) -> Self
```

Refuse un type qui n'est pas une image reconnue.

### Méthode `loom_ia.core.model.media.AttachmentPolicy.check_count`

```python
def check_count(self, count: int) -> None
```

Lève `AttachmentError` au-delà de `max_files` pièces jointes.

### Méthode `loom_ia.core.model.media.AttachmentPolicy.check_size`

```python
def check_size(self, label: str, size: int) -> None
```

Lève `AttachmentError` si `size` dépasse `max_bytes`.

## Classe `loom_ia.core.model.media.AttachmentError`

```python
class AttachmentError(ValueError)
```

Pièce jointe refusée : format non reconnu ou non accepté, taille, type incohérent.

## Classe `loom_ia.core.model.media.Attachment`

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class Attachment
```

Fichier joint à la demande de l'utilisateur (`loom run --attach`, `run(attachments=…)`, envoi multipart).

**Attributs :**

- `data` : Les octets.
- `media_type` : Type annoncé par l'appelant ; vérifié contre la signature.
- `name` : Nom du fichier, tel que le modèle le verra.

### Méthode `loom_ia.core.model.media.Attachment.from_path`

```python
@classmethod
def from_path(cls, path: str | Path, *, media_type: str | None=None) -> Self
```

Pièce jointe lue depuis un fichier, sous son nom.

**Arguments :**

- `path` : Chemin du fichier.
- `media_type` : Type annoncé ; `None` : déduit de la signature.

**Renvoie :**

La pièce jointe.

### Méthode `loom_ia.core.model.media.Attachment.checked`

```python
def checked(self, policy: AttachmentPolicy) -> str
```

Contrôle la pièce jointe et rend son type reconnu.

**Arguments :**

- `policy` : Ce que le run accepte.

**Renvoie :**

Le type MIME reconnu à la signature.

**Lève :**

- `AttachmentError` : Fichier vide, trop gros, format non reconnu, type annoncé qui ne correspond pas au contenu, ou type non accepté.
