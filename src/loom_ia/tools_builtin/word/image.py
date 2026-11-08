# SPDX-License-Identifier: Apache-2.0
"""Une image dans un .docx : le dessin en ligne, centré, qui renvoie à sa partie ``word/media``.

Une image est un ``<w:drawing><wp:inline>`` qui porte une image DrawingML
(``pic:pic``) ; ses octets sont une partie de l'archive (``word/media/imageN.png``),
nommée par une relation du corps (``r:embed``). La même image donnée deux fois
n'est stockée qu'une fois : les parties sont numérotées dans l'ordre où chaque
image paraît la première fois, ce qui rend le fichier reproductible.

L'adresse du fichier lu (« <dossier>/<chemin> ») est le nom du dessin
(``docPr name``) : la relecture la rend en ``path``, et le document se réécrit
tel quel tant que le fichier est là.
"""

from typing import Final
from xml.sax.saxutils import escape

from loom_ia.tools_builtin._document.content import ImageOut

_WP: Final = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
_A: Final = "http://schemas.openxmlformats.org/drawingml/2006/main"
_PIC: Final = "http://schemas.openxmlformats.org/drawingml/2006/picture"
_R: Final = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
# Un centième de millimètre en EMU (English Metric Units) : 360.
EMU: Final = 360


def _attribute(text: str) -> str:
    return escape(text, {'"': "&quot;", "\n": "&#10;", "\t": "&#9;"})


def inline(relation: str, drawing: int, image: ImageOut, *, keep_next: bool) -> str:
    """Le paragraphe centré qui porte l'image ; ``keep_next`` la garde avec sa légende."""
    cx, cy = image.width * EMU, image.height * EMU
    name = _attribute(image.source)
    descr = _attribute(image.alt)
    keep = "<w:keepNext/>" if keep_next else ""
    return (
        f'<w:p><w:pPr>{keep}<w:spacing w:before="120" w:after="60"/><w:jc w:val="center"/>'
        "</w:pPr><w:r><w:drawing>"
        f'<wp:inline xmlns:wp="{_WP}" distT="0" distB="0" distL="0" distR="0">'
        f'<wp:extent cx="{cx}" cy="{cy}"/><wp:effectExtent l="0" t="0" r="0" b="0"/>'
        f'<wp:docPr id="{drawing}" name="{name}" descr="{descr}"/>'
        f'<wp:cNvGraphicFramePr><a:graphicFrameLocks xmlns:a="{_A}" noChangeAspect="1"/>'
        "</wp:cNvGraphicFramePr>"
        f'<a:graphic xmlns:a="{_A}"><a:graphicData uri="{_PIC}">'
        f'<pic:pic xmlns:pic="{_PIC}"><pic:nvPicPr>'
        f'<pic:cNvPr id="{drawing}" name="{name}" descr="{descr}"/><pic:cNvPicPr/>'
        "</pic:nvPicPr>"
        f'<pic:blipFill><a:blip xmlns:r="{_R}" r:embed="{relation}"/>'
        "<a:stretch><a:fillRect/></a:stretch></pic:blipFill>"
        f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>'
        "</pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>"
    )
