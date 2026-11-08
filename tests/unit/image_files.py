# SPDX-License-Identifier: Apache-2.0
"""Des images PNG et JPEG fabriquées octet par octet, sans bibliothèque d'images.

Pour les essais du bloc ``image`` de ``loom.word`` et ``loom.pdf`` : le noyau seul
(sans l'extra ``pdf``, donc sans Pillow) doit pouvoir les faire tourner. Les PNG
sont de vraies images, que LibreOffice affiche ; les JPEG n'ont que leur
structure (segments, dimensions, orientation), pas de pixels décodables.
"""

import zlib
from typing import Final, Literal

_ADAM7: Final = ((0, 8, 0, 8), (4, 8, 0, 8), (0, 4, 4, 8), (2, 4, 0, 4), (0, 2, 2, 4),
                 (1, 2, 0, 2), (0, 1, 1, 2))  # fmt: skip


def chunk(kind: bytes, body: bytes) -> bytes:
    return len(body).to_bytes(4) + kind + body + zlib.crc32(kind + body).to_bytes(4)


def png(
    width: int,
    height: int,
    *,
    color: int = 2,
    depth: int = 8,
    interlace: int = 0,
    fill: int = 0x80,
    extra: bytes = b"",
    palette: bool = True,
) -> bytes:
    """Un PNG uni ; ``extra`` : des blocs posés avant les pixels (``acTL``…)."""
    samples = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color]
    bits = samples * depth

    def rows(columns: int, count: int) -> bytes:
        return (b"\x00" + bytes([fill]) * ((columns * bits + 7) // 8)) * count

    if interlace:
        raw = b"".join(
            rows((width - x0 + dx - 1) // dx, (height - y0 + dy - 1) // dy)
            for x0, dx, y0, dy in _ADAM7
            if width > x0 and height > y0
        )
    else:
        raw = rows(width, height)
    header = width.to_bytes(4) + height.to_bytes(4) + bytes([depth, color, 0, 0, interlace])
    plte = chunk(b"PLTE", b"\x1f\x38\x64" * 256) if color == 3 and palette else b""
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + plte
        + extra
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def _exif(orientation: int, *, big: bool = False) -> bytes:
    order: Literal["little", "big"] = "big" if big else "little"
    entry = (0x0112).to_bytes(2, order) + (3).to_bytes(2, order)
    entry += (1).to_bytes(4, order) + orientation.to_bytes(2, order) + b"\x00\x00"
    head = b"MM\x00*" if big else b"II*\x00"
    tiff = head + (8).to_bytes(4, order) + (1).to_bytes(2, order) + entry + bytes(4)
    body = b"Exif\x00\x00" + tiff
    return b"\xff\xe1" + (len(body) + 2).to_bytes(2) + body


def jpeg(
    width: int,
    height: int,
    *,
    orientation: int | None = None,
    sof: int = 0xC0,
    big: bool = False,
    padded: bool = False,
) -> bytes:
    """La structure d'un JPEG : début, EXIF, dimensions, données, fin.

    ``big`` : l'EXIF en gros-boutiste (« MM ») ; ``padded`` : des octets de bourrage et
    un marqueur isolé avant les dimensions, que le format permet.
    """
    frame = bytes([8]) + height.to_bytes(2) + width.to_bytes(2) + bytes([3])
    frame += b"".join(bytes([n, 0x11, 0]) for n in (1, 2, 3))
    scan = bytes([3]) + b"".join(bytes([n, 0]) for n in (1, 2, 3)) + b"\x00\x3f\x00"
    return (
        b"\xff\xd8"
        + (_exif(orientation, big=big) if orientation is not None else b"")
        + (b"\xff\xff\xff\xd0" if padded else b"")
        + bytes([0xFF, sof])
        + (len(frame) + 2).to_bytes(2)
        + frame
        + b"\xff\xda"
        + (len(scan) + 2).to_bytes(2)
        + scan
        + b"\x12\x34\x56\x78" * 8
        + b"\xff\xd9"
    )
