# loom_ia.tools_builtin._document.picture

Module `loom_ia.tools_builtin._document.picture` de loom_v2 (fichier `src/loom_ia/tools_builtin/_document/picture.py`).

Une image à poser dans un document : son format et ses dimensions, lus dans ses octets.

Sans bibliothèque d'images : un PNG et un JPEG disent leurs dimensions dans
leur en-tête, et la bibliothèque standard suffit à vérifier qu'ils sont
entiers. Le format vient de la signature (`sniff`), jamais du nom du
fichier. Ni disque ni rendu ici : des octets en entrée, une `Picture` ou une
`PictureError` qui dit quoi corriger.

Ce qui est vérifié :

- **PNG** : chaque bloc (somme de contrôle), l'en-tête (dimensions, couleurs),
  la palette quand il en faut une, la fin (`IEND`), et les pixels compressés,
  décompressés **sans être gardés** pour compter qu'il y en a autant que
  l'en-tête l'annonce : une image tronquée ou abîmée est refusée ;
- **JPEG** : la suite des segments jusqu'aux données, les dimensions, un codage
  que Word et LibreOffice affichent (de base, étendu ou progressif), et la fin de
  l'image ; ses pixels ne sont pas décodés.

La résolution annoncée (`pHYs`, JFIF, EXIF) n'est pas lue : la taille dans la
page est sémantique (`width`), et la densité affichée se calcule sur elle.

## Constantes de `loom_ia.tools_builtin._document.picture`

- `PictureFormat = Literal['png', 'jpeg']` : Format d'image reconnu.
- `PNG_SIGNATURE = b'\x89PNG\r\n\x1a\n'` : Les huit octets qui ouvrent un PNG.
- `_PNG_DEPTHS = {0: {1, 2, 4, 8, 16}, 2: {8, 16}, 3: {1, 2, 4, 8}, 4: {8, 16}, 6: {8, 16}}` : Bits par échantillon permis, par type de couleur ; et le nombre d'échantillons.
- `_ADAM7 = ((0, 8, 0, 8), (4, 8, 0, 8), (0, 4, 4, 8), (2, 4, 0, 4), (0, 2, 2, 4), (1, 2, 0, 2), (0, 1, 1, 2))` : Les sept passes d'un PNG entrelacé (Adam7) : départ et pas, en colonnes puis en lignes.
- `_CHUNK = 1 << 20` : Décompression par tranches : la mémoire ne suit pas la taille de l'image.
- `_JPEG_SOF = frozenset({192, 193, 194})` : Codages JPEG affichés partout : de base, étendu, progressif (Huffman).
- `_JPEG_OTHER_SOF = frozenset({195, 197, 198, 199, 201, 202, 203, 205, 206, 207})` : Les autres débuts d'image : sans perte, hiérarchiques, arithmétiques.

## Classe `loom_ia.tools_builtin._document.picture.PictureError`

```python
class PictureError(ValueError)
```

L'image ne peut pas entrer dans le document ; le message dit quoi faire.

## Classe `loom_ia.tools_builtin._document.picture.Picture`

```python
@dataclass(frozen=True, slots=True)
class Picture
```

Une image reconnue : son format, ses pixels, ses octets et leur empreinte.

**Attributs :**

- `format` : `png` ou `jpeg`.
- `width` : Largeur, en pixels.
- `height` : Hauteur, en pixels.
- `data` : Ses octets.
- `digest` : L'empreinte de ses octets.
- `orientation` : L'orientation EXIF d'une photo (1 : droite ; 6 et 8 : prise de côté).
- `animated` : Un PNG animé (APNG) : seule sa première image s'affiche.

### Méthode `loom_ia.tools_builtin._document.picture.Picture.extension`

```python
@property
def extension(self) -> str
```

L'extension du fichier : `png` ou `jpeg`.

## Fonction `loom_ia.tools_builtin._document.picture.parse`

```python
def parse(data: bytes) -> Picture
```

L'image reconnue dans `data` ; `PictureError` sinon.

## Fonction `loom_ia.tools_builtin._document.picture.dimensions`

```python
def dimensions(data: bytes) -> tuple[int, int] | None
```

Les pixels d'une image PNG ou JPEG, d'après son en-tête seul ; `None` sinon.

## Fonction `loom_ia.tools_builtin._document.picture._png`

```python
def _png(data: bytes) -> tuple[int, int, bool]
```

Largeur, hauteur et animation d'un PNG, lus dans ses segments (sommes de contrôle vérifiées) ; `PictureError` s'il est abîmé.

## Fonction `loom_ia.tools_builtin._document.picture._png_raw_size`

```python
def _png_raw_size(width: int, height: int, bits: int, interlace: int) -> int
```

La taille des pixels décompressés : chaque ligne et son octet de filtre.

## Fonction `loom_ia.tools_builtin._document.picture._jpeg`

```python
def _jpeg(data: bytes) -> tuple[int, int, int]
```

Largeur, hauteur et orientation EXIF d'un JPEG, lues dans ses segments ; `PictureError` s'il est abîmé.

## Fonction `loom_ia.tools_builtin._document.picture._orientation`

```python
def _orientation(tiff: bytes) -> int
```

L'orientation EXIF (1 à 8) ; 1 si elle est absente ou illisible.
