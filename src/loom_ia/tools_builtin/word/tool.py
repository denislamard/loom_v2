# SPDX-License-Identifier: Apache-2.0
"""L'outil ``word`` monté : écrire et lire des .docx ; et sa fabrique."""

import asyncio
from typing import Annotated, ClassVar, Final
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, JsonValue

from loom_ia.core.ports import ToolError
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
from loom_ia.tools_builtin.word import reader, writer
from loom_ia.tools_builtin.word_params import WordParams

NAME: Final = "word"


class WordCall(DocumentCall):
    """Les arguments de ``word`` : l'écriture commune, et où commencer une lecture."""

    READ_FIELDS: ClassVar[tuple[str, ...]] = ("start",)

    path: Annotated[str, Field(description="« <dossier>/<chemin>.docx »")]
    start: Annotated[
        int | None,
        Field(ge=0, description="Pour read : le bloc où commencer (0 par défaut ; next_start)."),
    ] = None


# ``blocks`` élargi au bloc graphique : voulu, comme dans ``WithCharts``.
class WordChartCall(WithCharts, WordCall):  # pyright: ignore[reportIncompatibleVariableOverride]
    """Les arguments de ``word`` avec le bloc graphique (``charts: true``)."""


class WordTool(DocumentTool):
    """L'outil ``word`` monté avec ses dossiers et son fuseau."""

    NAME: ClassVar[str] = NAME
    CALL: ClassVar[type[DocumentCall]] = WordCall
    CHART_CALL: ClassVar[type[DocumentCall]] = WordChartCall
    SUFFIX: ClassVar[str] = ".docx"
    LABEL: ClassVar[str] = "document Word"

    def __init__(
        self,
        space: Folders,
        zone: ZoneInfo,
        unsafe: str | None = None,
        *,
        charts: bool = False,
    ) -> None:
        # Pourquoi les documents ne se lisent pas ici (version d'expat).
        self._unsafe = unsafe
        super().__init__(space, zone, charts=charts)

    def _render(self, prepared: Prepared) -> tuple[bytes, dict[str, JsonValue], list[str]]:
        return writer.render(prepared), {}, []

    async def _read(self, request: ReadArguments, where: str, data: bytes) -> dict[str, JsonValue]:
        found = await asyncio.to_thread(reader.read, data, where, request.start or 0)
        if not self._charts:
            _warn_charts(found)
        return found

    def _check_readable(self, path: str) -> None:
        reader.check_suffix(path)
        if self._unsafe:
            raise ToolError(f"{path} : lecture des documents désactivée ici — {self._unsafe}.")

    def _reading_help(self, folders: str) -> str:
        if self._unsafe:
            return f"Lecture (action=read) : indisponible ici ({self._unsafe})."
        return (
            f"Lecture (action=read, dans : {folders}) : un .docx, rendu en blocs du même "
            "vocabulaire (titre level 0 : titre du document), l'emphase en **…** et *…*. Depuis "
            f"le bloc 'start' (0 par défaut), {READ_CHARS} caractères au plus ; au-delà, "
            "'next_start' et 'outline' (le plan, avec l'indice de chaque titre)."
        )

    def _options(self) -> tuple[tuple[str, str], ...]:
        reads = any("read" in folder.rights for folder in self._space.folders)
        reading = (
            f"non ({self._unsafe})" if self._unsafe else "oui" if reads else "aucun dossier read"
        )
        return (*super()._options(), ("lecture des documents", reading))


def _warn_charts(found: dict[str, JsonValue]) -> None:
    """Des graphiques lus par un outil qui n'en écrit pas : le dire avant une réécriture.

    Sinon, le bloc renvoyé tel quel serait refusé par le schéma, sans dire pourquoi.
    """
    blocks = found.get("blocks")
    if not isinstance(blocks, list):
        return
    count = sum(
        1 for block in blocks if isinstance(block, dict) and block.get("type") == "graphique"
    )
    if not count:
        return
    notes = found.setdefault("notes", list[JsonValue]())
    if isinstance(notes, list):
        notes.append(
            f"{count} graphique(s) lu(s) : cet outil n'en écrit pas ; les retirer avant de "
            "réécrire le document (ou charts: true dans la config de l'outil)."
        )


def build(params: BaseModel, context: BuiltinContext) -> WordTool:
    """La fabrique de ``loom.word`` : les dossiers comme ``local_files``."""
    if not isinstance(params, WordParams):
        raise TypeError(f"paramètres de {NAME} attendus, reçu {type(params).__name__}")
    space = Folders(mount(params.folders, context))
    unsafe = reader.xml_safety() if any("read" in f.rights for f in space.folders) else None
    return WordTool(space, ZoneInfo(params.timezone), unsafe, charts=params.charts)
