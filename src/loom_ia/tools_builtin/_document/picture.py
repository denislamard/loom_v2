# SPDX-License-Identifier: Apache-2.0
"""Une image à poser dans un document : son format et ses dimensions, lus dans ses octets.

Sans bibliothèque d'images : un PNG et un JPEG disent leurs dimensions dans
leur en-tête, et la bibliothèque standard suffit à vérifier qu'ils sont
entiers. Le format vient de la signature (``sniff``), jamais du nom du
fichier. Ni disque ni rendu ici : des octets en entrée, une ``Picture`` ou une
``PictureError`` qui dit quoi corriger.

Ce qui est vérifié :

- **PNG** : chaque bloc (somme de contrôle), l'en-tête (dimensions, couleurs),
  la palette quand il en faut une, la fin (``IEND``), et les pixels compressés,
  décompressés **sans être gardés** pour compter qu'il y en a autant que
  l'en-tête l'annonce : une image tronquée ou abîmée est refusée ;
- **JPEG** : la suite des segments jusqu'aux données, les dimensions, un codage
  que Word et LibreOffice affichent (de base, étendu ou progressif), et la fin de
  l'image ; ses pixels ne sont pas décodés.

La résolution annoncée (``pHYs``, JFIF, EXIF) n'est pas lue : la taille dans la
page est sémantique (``width``), et la densité affichée se calcule sur elle.
"""

import hashlib
import zlib
from dataclasses import dataclass, field
from typing import Final, Literal

from loom_ia.core.model.media import sniff
from loom_ia.tools_builtin._document.arguments import MAX_PIXELS

type PictureFormat = Literal["png", "jpeg"]

PNG_SIGNATURE: Final = b"\x89PNG\r\n\x1a\n"
# Bits par échantillon permis, par type de couleur ; et le nombre d'échantillons.
_PNG_DEPTHS: Final = {0: {1, 2, 4, 8, 16}, 2: {8, 16}, 3: {1, 2, 4, 8}, 4: {8, 16}, 6: {8, 16}}
_PNG_SAMPLES: Final = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
# Les sept passes d'un PNG entrelacé (Adam7) : départ et pas, en colonnes puis en lignes.
_ADAM7: Final = ((0, 8, 0, 8), (4, 8, 0, 8), (0, 4, 4, 8), (2, 4, 0, 4), (0, 2, 2, 4),
                 (1, 2, 0, 2), (0, 1, 1, 2))  # fmt: skip
# Décompression par tranches : la mémoire ne suit pas la taille de l'image.
_CHUNK: Final = 1 << 20
# Codages JPEG affichés partout : de base, étendu, progressif (Huffman).
_JPEG_SOF: Final = frozenset({0xC0, 0xC1, 0xC2})
# Les autres débuts d'image : sans perte, hiérarchiques, arithmétiques.
_JPEG_OTHER_SOF: Final = frozenset({0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF})
_JPEG_STANDALONE: Final = frozenset({0x01, *range(0xD0, 0xD8)})
_EXIF_ORIENTATION: Final = 0x0112


class PictureError(ValueError):
    """L'image ne peut pas entrer dans le document ; le message dit quoi faire."""


@dataclass(frozen=True, slots=True)
class Picture:
    """Une image reconnue : son format, ses pixels, ses octets et leur empreinte."""

    format: PictureFormat
    width: int
    height: int
    data: bytes = field(repr=False)
    digest: str
    # L'orientation EXIF d'une photo (1 : droite ; 6 et 8 : prise de côté).
    orientation: int = 1
    # Un PNG animé (APNG) : seule sa première image s'affiche.
    animated: bool = False

    @property
    def extension(self) -> str:
        return "png" if self.format == "png" else "jpeg"


def parse(data: bytes) -> Picture:
    """L'image reconnue dans ``data`` ; ``PictureError`` sinon."""
    if not data:
        raise PictureError("fichier vide")
    match sniff(data):
        case "image/png":
            width, height, animated = _png(data)
            orientation = 1
        case "image/jpeg":
            width, height, orientation = _jpeg(data)
            animated = False
        case "image/gif":
            raise PictureError("image GIF : la convertir en PNG")
        case "image/webp":
            raise PictureError("image WebP : Word ne l'affiche pas partout ; la convertir en PNG")
        case _:
            said = (
                "image SVG : la convertir en PNG"
                if data.lstrip()[:5] in (b"<?xml", b"<svg ", b"<svg>")
                else "format non reconnu : PNG ou JPEG attendu (le format se lit dans le "
                "fichier, pas dans son nom)"
            )
            raise PictureError(said)
    if width * height > MAX_PIXELS:
        raise PictureError(
            f"{width} x {height} pixels, au-delà de {MAX_PIXELS // 1_000_000} millions : la réduire"
        )
    fmt: PictureFormat = "png" if data.startswith(PNG_SIGNATURE) else "jpeg"
    return Picture(
        format=fmt,
        width=width,
        height=height,
        data=data,
        digest=hashlib.sha256(data).hexdigest(),
        orientation=orientation,
        animated=animated,
    )


def dimensions(data: bytes) -> tuple[int, int] | None:
    """Les pixels d'une image PNG ou JPEG, d'après son en-tête seul ; ``None`` sinon."""
    try:
        match sniff(data):
            case "image/png":
                if data[12:16] != b"IHDR":
                    return None
                width, height = int.from_bytes(data[16:20]), int.from_bytes(data[20:24])
            case "image/jpeg":
                width, height, _ = _jpeg(data)
            case _:
                return None
    except PictureError:
        return None
    return (width, height) if width and height else None


# --- PNG -------------------------------------------------------------------------------


def _png(data: bytes) -> tuple[int, int, bool]:
    position = len(PNG_SIGNATURE)
    header: tuple[int, int, int, int, int] | None = None
    palette = animated = ended = False
    inflater = zlib.decompressobj()
    produced = 0
    while position + 12 <= len(data):
        length = int.from_bytes(data[position : position + 4])
        kind = data[position + 4 : position + 8]
        end = position + 12 + length
        if end > len(data):
            break
        body = data[position + 8 : end - 4]
        if zlib.crc32(kind + body) != int.from_bytes(data[end - 4 : end]):
            raise PictureError(f"PNG abîmé (bloc {kind.decode('latin-1')!r} altéré)")
        if header is None and kind != b"IHDR":
            raise PictureError("PNG abîmé (en-tête absent)")
        match kind:
            case b"IHDR":
                header = _png_header(body)
            case b"PLTE":
                palette = True
            case b"acTL":
                animated = True
            case b"IDAT":
                try:
                    # Décompressé par tranches, compté, jamais gardé.
                    pending = body
                    while pending:
                        produced += len(inflater.decompress(pending, _CHUNK))
                        pending = inflater.unconsumed_tail
                except zlib.error:
                    raise PictureError("PNG abîmé (pixels illisibles)") from None
            case b"IEND":
                ended = True
                break
            case _:
                pass
        position = end
    if header is None or not ended:
        raise PictureError("PNG tronqué (fichier incomplet)")
    width, height, depth, color, interlace = header
    if color == 3 and not palette:
        raise PictureError("PNG abîmé (palette absente)")
    expected = _png_raw_size(width, height, depth * _PNG_SAMPLES[color], interlace)
    if produced != expected or not inflater.eof:
        raise PictureError("PNG tronqué ou abîmé (pixels manquants)")
    return width, height, animated


def _png_header(body: bytes) -> tuple[int, int, int, int, int]:
    if len(body) != 13:
        raise PictureError("PNG abîmé (en-tête)")
    width, height = int.from_bytes(body[0:4]), int.from_bytes(body[4:8])
    depth, color, compression, filtering, interlace = body[8:13]
    if not width or not height:
        raise PictureError("image de dimension nulle")
    if depth not in _PNG_DEPTHS.get(color, set()) or compression or filtering or interlace > 1:
        raise PictureError("PNG abîmé (en-tête)")
    if width * height > MAX_PIXELS:
        raise PictureError(
            f"{width} x {height} pixels, au-delà de {MAX_PIXELS // 1_000_000} millions : la réduire"
        )
    return width, height, depth, color, interlace


def _png_raw_size(width: int, height: int, bits: int, interlace: int) -> int:
    """La taille des pixels décompressés : chaque ligne et son octet de filtre."""
    if not interlace:
        return height * (1 + (width * bits + 7) // 8)
    total = 0
    for x0, dx, y0, dy in _ADAM7:
        columns = (width - x0 + dx - 1) // dx if width > x0 else 0
        rows = (height - y0 + dy - 1) // dy if height > y0 else 0
        if columns and rows:
            total += rows * (1 + (columns * bits + 7) // 8)
    return total


# --- JPEG ------------------------------------------------------------------------------


def _jpeg(data: bytes) -> tuple[int, int, int]:
    position = 2
    size: tuple[int, int] | None = None
    orientation = 1
    while True:
        if position + 4 > len(data):
            raise PictureError("JPEG tronqué (fichier incomplet)")
        if data[position] != 0xFF:
            raise PictureError("JPEG abîmé (segment illisible)")
        marker = data[position + 1]
        if marker == 0xFF:
            # Octets de bourrage avant un marqueur.
            position += 1
            continue
        if marker in _JPEG_STANDALONE:
            position += 2
            continue
        if marker == 0xD9:
            raise PictureError("JPEG abîmé (fin d'image avant les pixels)")
        length = int.from_bytes(data[position + 2 : position + 4])
        segment = data[position + 4 : position + 2 + length]
        if length < 2 or len(segment) != length - 2:
            raise PictureError("JPEG tronqué (fichier incomplet)")
        if marker in _JPEG_SOF:
            if len(segment) < 6:
                raise PictureError("JPEG abîmé (en-tête)")
            height, width = int.from_bytes(segment[1:3]), int.from_bytes(segment[3:5])
            if not width or not height:
                raise PictureError("image de dimension nulle")
            size = (width, height)
        elif marker in _JPEG_OTHER_SOF:
            raise PictureError(
                "JPEG d'un codage que Word n'affiche pas (sans perte, hiérarchique ou "
                "arithmétique) : le réenregistrer en JPEG standard ou en PNG"
            )
        elif marker == 0xE1 and segment.startswith(b"Exif\x00\x00"):
            orientation = _orientation(segment[6:])
        elif marker == 0xDA:
            if size is None:
                raise PictureError("JPEG abîmé (dimensions absentes)")
            # Après les pixels, la fin de l'image ; des données peuvent suivre (photo
            # animée de certains téléphones), elles ne comptent pas.
            if data.find(b"\xff\xd9", position + 2 + length) < 0:
                raise PictureError("JPEG tronqué (fin de l'image absente)")
            return size[0], size[1], orientation
        position += 2 + length


def _orientation(tiff: bytes) -> int:
    """L'orientation EXIF (1 à 8) ; 1 si elle est absente ou illisible."""
    if tiff[:4] == b"II*\x00":
        order: Literal["little", "big"] = "little"
    elif tiff[:4] == b"MM\x00*":
        order = "big"
    else:
        return 1
    start = int.from_bytes(tiff[4:8], order)
    if start + 2 > len(tiff):
        return 1
    count = int.from_bytes(tiff[start : start + 2], order)
    for index in range(min(count, 200)):
        at = start + 2 + 12 * index
        entry = tiff[at : at + 12]
        if len(entry) < 12:
            break
        if int.from_bytes(entry[0:2], order) == _EXIF_ORIENTATION:
            value = int.from_bytes(entry[8:10], order)
            return value if 1 <= value <= 8 else 1
    return 1
