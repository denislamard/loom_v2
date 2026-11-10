# SPDX-License-Identifier: Apache-2.0
"""L'outil ``word`` monté : écrire et lire des .docx ; et sa fabrique."""

from collections.abc import Iterable, Mapping, Sequence
from typing import Annotated, ClassVar, Final
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, JsonValue

from loom_ia.core.ports import ToolError
from loom_ia.core.tool_threads import in_thread
from loom_ia.tools_builtin import BuiltinContext
from loom_ia.tools_builtin._document.arguments import (
    READ_CHARS,
    DocumentCall,
    ReadArguments,
)
from loom_ia.tools_builtin._document.content import Prepared
from loom_ia.tools_builtin._document.tool import HOUSE_STYLES, DocumentTool, Styles, read_styles
from loom_ia.tools_builtin._folders import Folders, mount
from loom_ia.tools_builtin._office_params import read_templates
from loom_ia.tools_builtin._style_params import Style, unsafe_fonts
from loom_ia.tools_builtin._templates import Template
from loom_ia.tools_builtin.word import reader, writer
from loom_ia.tools_builtin.word_params import REFERENCE, WordParams

NAME: Final = "word"


class WordCall(DocumentCall):
    """Les arguments de ``word`` : l'écriture commune, et où commencer une lecture."""

    READ_FIELDS: ClassVar[tuple[str, ...]] = ("start",)

    path: Annotated[str, Field(description="« <dossier>/<chemin>.docx »")]
    start: Annotated[
        int | None,
        Field(ge=0, description="Pour read : le bloc où commencer (0 par défaut ; next_start)."),
    ] = None


class WordTool(DocumentTool):
    """L'outil ``word`` monté avec ses dossiers et son fuseau."""

    NAME: ClassVar[str] = NAME
    CALL: ClassVar[type[DocumentCall]] = WordCall
    SUFFIX: ClassVar[str] = ".docx"
    LABEL: ClassVar[str] = "document Word"

    def __init__(
        self,
        space: Folders,
        zone: ZoneInfo,
        unsafe: str | None = None,
        *,
        blocks: Iterable[str] = (),
        templates: Mapping[str, Template] | None = None,
        styles: Styles = HOUSE_STYLES,
    ) -> None:
        # Pourquoi les documents ne se lisent pas ici (version d'expat).
        self._unsafe = unsafe
        super().__init__(space, zone, blocks=blocks, templates=templates, styles=styles)

    def _render(self, prepared: Prepared) -> tuple[bytes, dict[str, JsonValue], list[str]]:
        style = self._styles.of(prepared.template)
        template = self._templates.get(prepared.template or "")
        return writer.render(prepared, style, template), {}, []

    async def _read(self, request: ReadArguments, where: str, data: bytes) -> dict[str, JsonValue]:
        found = await in_thread(
            reader.read,
            data,
            where,
            request.start or 0,
            images=self._images,
            templates=self._templates or None,
            styles=frozenset(name for names in self._proposed_styles().values() for name in names),
        )
        _warn_unwritten(found, self._blocks)
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
            "vocabulaire (heading level 0 : titre du document), l'emphase en **…** et *…*. Depuis "
            f"le bloc 'start' (0 par défaut), {READ_CHARS} caractères au plus ; au-delà, "
            "'next_start' et 'outline' (le plan, avec l'indice de chaque titre)."
        )

    def _options(self) -> tuple[tuple[str, str], ...]:
        reads = any("read" in folder.rights for folder in self._space.folders)
        reading = (
            f"non ({self._unsafe})" if self._unsafe else "oui" if reads else "aucun dossier read"
        )
        return (
            *super()._options(),
            ("lecture des documents", reading),
            *self._styles.said,
        )


def _warn_unwritten(found: dict[str, JsonValue], blocks: tuple[str, ...]) -> None:
    """Des blocs lus que cet outil n'écrit pas (un graphique, une signature, un sommaire, une
    liste à cocher, des champs, du code, un récapitulatif de TVA) : le dire avant une
    réécriture, que le schéma refuserait sans dire pourquoi."""
    read = found.get("blocks")
    if not isinstance(read, list):
        return
    missing: dict[str, int] = {}
    for block in read:
        if not isinstance(block, dict):
            continue
        name = "checkboxes" if block.get("checkboxes") else str(block.get("type"))
        optional = ("chart", "signature", "toc", "checkboxes", "fields", "code", "invoice_totals")
        if name in optional and (name not in blocks):
            missing[name] = missing.get(name, 0) + 1
    if not missing:
        return
    said = ", ".join(f"{name} ({count})" for name, count in missing.items())
    notes = found.setdefault("notes", list[JsonValue]())
    if isinstance(notes, list):
        notes.append(
            f"Lu : {said} ; cet outil n'en écrit pas : les retirer avant de réécrire le "
            f"document (ou blocks: [{', '.join(missing)}] dans la config de l'outil)."
        )


def build(params: BaseModel, context: BuiltinContext) -> WordTool:
    """La fabrique de ``loom.word`` : les dossiers comme ``local_files``."""
    if not isinstance(params, WordParams):
        raise TypeError(f"paramètres de {NAME} attendus, reçu {type(params).__name__}")
    space = Folders(mount(params.folders, context))
    unsafe = reader.xml_safety() if any("read" in f.rights for f in space.folders) else None
    templates = read_templates(params.templates, REFERENCE)
    return WordTool(
        space,
        ZoneInfo(params.timezone),
        unsafe,
        blocks=params.blocks,
        templates=templates,
        styles=read_styles(params, templates, REFERENCE, _fonts_said),
    )


def _fonts_said(styles: Sequence[Style]) -> str | None:
    """Les polices hors de la liste sûre : Word les nomme, sans les embarquer."""
    fonts = sorted({font for style in styles for font in unsafe_fonts(style)})
    if not fonts:
        return None
    return (
        f"{', '.join(fonts)} : hors des polices de tous les postes, remplacée(s) chez qui "
        "ne les a pas"
    )
