# SPDX-License-Identifier: Apache-2.0
"""L'outil ``word`` monté : écrire et lire des .docx ; et sa fabrique."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
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
from loom_ia.tools_builtin._document.picture import PictureError
from loom_ia.tools_builtin._document.picture import parse as parse_picture
from loom_ia.tools_builtin._document.tool import DocumentTool
from loom_ia.tools_builtin._folders import Folders, mount
from loom_ia.tools_builtin._office_params import read_templates
from loom_ia.tools_builtin._style_params import (
    HOUSE,
    Style,
    StyleRef,
    StyleSpec,
    check_charte,
    check_layout,
    logos,
    merged,
    resolve,
    unsafe_fonts,
)
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


@dataclass(frozen=True, slots=True)
class Styles:
    """Les styles résolus d'un outil : la charte, et charte + disposition par modèle."""

    default: Style = HOUSE
    by_template: Mapping[str, Style] = field(default_factory=dict[str, Style])
    # Ce que ``loom validate`` en dit.
    said: tuple[tuple[str, str], ...] = ()

    def of(self, template: str | None) -> Style:
        """Le style d'un document : celui de son modèle, sinon la charte."""
        return self.by_template.get(template or "", self.default)


NO_STYLES: Final = Styles()


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
        styles: Styles = NO_STYLES,
    ) -> None:
        # Pourquoi les documents ne se lisent pas ici (version d'expat).
        self._unsafe = unsafe
        self._styles = styles
        super().__init__(space, zone, blocks=blocks, templates=templates)

    def _render(self, prepared: Prepared) -> tuple[bytes, dict[str, JsonValue], list[str]]:
        return writer.render(prepared, self._styles.of(prepared.template)), {}, []

    async def _read(self, request: ReadArguments, where: str, data: bytes) -> dict[str, JsonValue]:
        found = await in_thread(
            reader.read,
            data,
            where,
            request.start or 0,
            images=self._images,
            templates=tuple(self._templates) if self._templates else None,
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
    liste à cocher) : le dire avant une réécriture, que le schéma refuserait sans dire
    pourquoi."""
    read = found.get("blocks")
    if not isinstance(read, list):
        return
    missing: dict[str, int] = {}
    for block in read:
        if not isinstance(block, dict):
            continue
        name = "checkboxes" if block.get("checkboxes") else str(block.get("type"))
        if name in ("chart", "signature", "toc", "checkboxes") and name not in blocks:
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
        styles=read_styles(params, templates),
    )


def read_styles(params: WordParams, templates: Mapping[str, Template]) -> Styles:
    """Les styles de l'outil, vérifiés et résolus une fois, au montage.

    Le chargeur de config a lu les fichiers (``spec``) et leurs logos ; une référence
    construite en Python sans passer par lui garde ``file`` : c'est une faute de montage.
    Les logos sont reconnus ici (PNG ou JPEG) : une faute se voit dès ``loom validate``.
    """
    charte = _spec(params.style, f"{REFERENCE}.style")
    if charte is not None:
        check_charte(charte, f"{REFERENCE}.style")
    layouts: dict[str, StyleSpec] = {}
    for name, ref in params.templates.items():
        where = f"{REFERENCE}.templates[{name}].style"
        spec = _spec(ref.style, where)
        if spec is not None:
            check_layout(spec, templates[name], where)
            layouts[name] = spec
    declared = [spec for spec in (charte, *layouts.values()) if spec is not None]
    if not declared:
        return NO_STYLES
    fonts: list[str] = []
    for spec in declared:
        fonts += unsafe_fonts(spec)
        for band, logo in logos(spec):
            if logo.data is None:
                raise ValueError(f"{REFERENCE} : logo de {band} ({logo.file}) non lu")
            try:
                parse_picture(logo.data)
            except PictureError as exc:
                raise ValueError(f"{REFERENCE} : logo de {band} : {exc}") from None
    said = [
        (
            "style",
            ("charte" if charte is not None else "style maison")
            + (f" ; disposition : {', '.join(layouts)}" if layouts else ""),
        )
    ]
    if fonts:
        said.append(
            (
                "polices",
                f"{', '.join(dict.fromkeys(fonts))} : hors des polices de tous les postes, "
                "remplacée(s) chez qui ne les a pas",
            )
        )
    return Styles(
        default=resolve(merged(charte)),
        by_template={name: resolve(merged(charte, spec)) for name, spec in layouts.items()},
        said=tuple(said),
    )


def _spec(ref: StyleRef | None, where: str) -> StyleSpec | None:
    if ref is None:
        return None
    if ref.spec is None:
        raise ValueError(
            f"{where} ({ref.file}) non lu — les styles se lisent au chargement de la config "
            "(load_config), ou se donnent par leur contenu (spec)"
        )
    return ref.spec
