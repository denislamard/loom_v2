# SPDX-License-Identifier: Apache-2.0
"""L'outil ``pdf`` monté : écrire des PDF (reportlab), lire leur texte (pypdf) ; et sa fabrique."""

import importlib.util
from importlib.metadata import version
from typing import Annotated, ClassVar, Final
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, JsonValue

from loom_ia.tools_builtin import BuiltinContext
from loom_ia.tools_builtin._document.arguments import (
    READ_CHARS,
    DocumentCall,
    ReadArguments,
    WithCharts,
)
from loom_ia.tools_builtin._document.content import Prepared
from loom_ia.tools_builtin._document.tool import DocumentTool
from loom_ia.tools_builtin._folders import Folders, mount
from loom_ia.tools_builtin.pdf import reader, writer
from loom_ia.tools_builtin.pdf_params import PdfParams

NAME: Final = "pdf"
# Les versions antérieures bouclent sans fin sur des PDF forgés (CVE-2026-59935, -59937).
SAFE_PYPDF: Final = (6, 14, 2)


class PdfCall(DocumentCall):
    """Les arguments de ``pdf`` : l'écriture commune, et les pages d'une lecture."""

    READ_FIELDS: ClassVar[tuple[str, ...]] = ("pages",)

    path: Annotated[str, Field(description="« <dossier>/<chemin>.pdf »")]
    pages: Annotated[
        str | None,
        Field(description="Pour read : 3, 2-5 ou 4- (jusqu'à la fin) ; depuis la 1re sinon."),
    ] = None


# ``blocks`` élargi au bloc graphique : voulu, comme dans ``WithCharts``.
class PdfChartCall(WithCharts, PdfCall):  # pyright: ignore[reportIncompatibleVariableOverride]
    """Les arguments de ``pdf`` avec le bloc graphique (``charts: true``)."""


class PdfTool(DocumentTool):
    """L'outil ``pdf`` monté avec ses dossiers et son fuseau."""

    NAME: ClassVar[str] = NAME
    CALL: ClassVar[type[DocumentCall]] = PdfCall
    CHART_CALL: ClassVar[type[DocumentCall]] = PdfChartCall
    SUFFIX: ClassVar[str] = ".pdf"
    LABEL: ClassVar[str] = "document PDF"

    def _render(self, prepared: Prepared) -> tuple[bytes, dict[str, JsonValue], list[str]]:
        data, pages, warnings = writer.render(prepared)
        return data, {"pages": pages}, warnings

    async def _read(self, request: ReadArguments, where: str, data: bytes) -> dict[str, JsonValue]:
        return await reader.read(data, where, request.pages)

    def _check_readable(self, path: str) -> None:
        reader.check_suffix(path)
        reader.page_range(None)

    def _writing_notes(self) -> tuple[str, ...]:
        return (
            "Polices standard du PDF : le français et les symboles courants passent ; un emoji "
            "ou un idéogramme s'imprimerait en carré noir (signalé en avertissement).",
        )

    def _reading_help(self, folders: str) -> str:
        return (
            f"Lecture (action=read, dans : {folders}) : le texte de chaque page, depuis la 1re "
            f"ou selon 'pages', {READ_CHARS} caractères au plus ; au-delà, 'next_page'. Aussi "
            "le nombre de pages, le titre, et les champs d'un formulaire. Une page scannée n'a "
            "pas de texte (pas d'OCR) ; un PDF protégé par mot de passe n'est pas lu."
        )

    def _options(self) -> tuple[tuple[str, str], ...]:
        reads = any("read" in folder.rights for folder in self._space.folders)
        reading = (
            f"oui, process à part ({reader.READ_DEADLINE:g} s, {reader.MEMORY >> 30} Gio)"
            if reads
            else "aucun dossier read"
        )
        return (*super()._options(), ("lecture des PDF", reading))


def build(params: BaseModel, context: BuiltinContext) -> PdfTool:
    """La fabrique de ``loom.pdf`` : les dossiers comme ``local_files``, pypdf vérifié."""
    if not isinstance(params, PdfParams):
        raise TypeError(f"paramètres de {NAME} attendus, reçu {type(params).__name__}")
    space = Folders(mount(params.folders, context))
    if any("read" in folder.rights for folder in space.folders):
        # pypdf n'est importé que par le process de lecture : le vérifier au montage.
        if importlib.util.find_spec("pypdf") is None:
            raise ImportError("pypdf absent", name="pypdf")
        found = tuple(int(part) for part in version("pypdf").split(".")[:3] if part.isdigit())
        if found < SAFE_PYPDF:
            raise ValueError(
                f"pypdf {version('pypdf')} : {'.'.join(map(str, SAFE_PYPDF))} ou plus attendu "
                "(boucles sans fin sur des PDF forgés) — mettre à jour loom-ia[pdf]"
            )
    return PdfTool(space, ZoneInfo(params.timezone), charts=params.charts)
