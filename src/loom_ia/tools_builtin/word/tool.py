# SPDX-License-Identifier: Apache-2.0
"""L'outil ``word`` monté : écrire et lire des .docx ; et sa fabrique."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Annotated, ClassVar, Final
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, JsonValue, ValidationError

from loom_ia.core.ports import ToolError
from loom_ia.core.tool_threads import in_thread
from loom_ia.tools_builtin import BuiltinContext
from loom_ia.tools_builtin._document.arguments import (
    READ_CHARS,
    DocumentCall,
    ReadArguments,
)
from loom_ia.tools_builtin._document.content import Allowed, Prepared
from loom_ia.tools_builtin._document.picture import PictureError
from loom_ia.tools_builtin._document.picture import parse as parse_picture
from loom_ia.tools_builtin._document.tool import DocumentTool
from loom_ia.tools_builtin._folders import Folders, mount
from loom_ia.tools_builtin._office_params import check_section_styles, read_templates
from loom_ia.tools_builtin._style_params import (
    Labels,
    Style,
    StyleFile,
    StyleRef,
    describe,
    house,
    logos,
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
    """Les styles résolus d'un outil : la charte, et charte + style du modèle, par modèle ;
    le style maison sans charte (``default`` vide)."""

    default: Style | None = None
    by_template: Mapping[str, Style] = field(default_factory=dict[str, Style])
    # Ce que ``loom validate`` en dit.
    said: tuple[tuple[str, str], ...] = ()

    def of(self, template: str | None) -> Style:
        """Le style d'un document : celui de son modèle, sinon la charte."""
        return self.by_template.get(template or "", self.default or house())


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
        style = self._styles.of(prepared.template)
        template = self._templates.get(prepared.template or "")
        return writer.render(prepared, style, template), {}, []

    def _labels(self, template: str | None) -> Labels:
        return self._styles.of(template).labels

    def _proposed_styles(self) -> Mapping[str, Iterable[str]]:
        found: dict[str, list[str]] = {}
        styles = [self._styles.default, *self._styles.by_template.values()]
        for style in styles:
            for kind, names in (style.proposed() if style is not None else {}).items():
                found.setdefault(kind, []).extend(name for name, _ in names)
        return found

    def _allowed(self, template: str | None) -> Allowed:
        style = self._styles.of(template)
        allowed: dict[str, dict[str, int]] = {}
        for kind, names in style.proposed().items():
            for name, _ in names:
                level = (style.block_type(name) or (kind, 0))[1]
                allowed.setdefault(kind, {})[name] = level
        return allowed

    def _template_styles(self, name: str) -> dict[str, JsonValue]:
        own = self._styles.by_template.get(name)
        if own is None:
            return {}
        charte = (self._styles.default or house()).proposed()
        found: dict[str, JsonValue] = {}
        for kind, names in own.proposed().items():
            added = {
                style: said for style, said in names if (style, said) not in charte.get(kind, [])
            }
            if added:
                found[kind] = dict(added)
        return found

    def _styles_help(self) -> list[str]:
        proposed = (self._styles.default or house()).proposed()
        if not proposed:
            return []
        return [
            "Styles proposés ('style' d'un bloc, facultatif ; ceux d'un modèle se lisent par "
            "action=template) : "
            + " ; ".join(
                f"{kind} : " + ", ".join(f"{name} ({said})" for name, said in names)
                for kind, names in proposed.items()
            )
        ]

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
    Un modèle qui écrit son style en ligne le donne ici. Les logos sont reconnus (PNG ou
    JPEG), la cascade résolue (couleurs nommées, bases), et les styles que choisissent
    les sections cherchés : une faute se voit dès ``loom validate``.
    """
    charte = _spec(params.style, f"{REFERENCE}.style")
    own: dict[str, StyleFile] = {}
    for name, ref in params.templates.items():
        spec = _spec(ref.style, f"{REFERENCE}.templates[{name}].style") or _written(
            templates[name], f"{REFERENCE}.templates[{name}].style"
        )
        if spec is not None:
            own[name] = spec
    declared = [spec for spec in (charte, *own.values()) if spec is not None]
    for spec in declared:
        for style, logo in logos(spec):
            if isinstance(logo, str):
                raise ValueError(f"{REFERENCE} : logo de {style} ({logo}) non lu")
            try:
                parse_picture(logo)
            except PictureError as exc:
                raise ValueError(f"{REFERENCE} : logo de {style} : {exc}") from None
    default = _resolved(f"{REFERENCE}.style", charte)
    by_template = {
        name: _resolved(f"{REFERENCE}.templates[{name}].style", charte, spec)
        for name, spec in own.items()
    }
    for name, template in templates.items():
        check_section_styles(
            template, by_template.get(name, default), f"{REFERENCE}.templates[{name}]"
        )
    if not declared:
        return NO_STYLES
    said = [
        (
            "style",
            ("charte" if charte is not None else "style maison")
            + (f" ; style de modèle : {', '.join(own)}" if own else ""),
        )
    ]
    fonts = sorted(
        {font for style in (default, *by_template.values()) for font in unsafe_fonts(style)}
    )
    if fonts:
        said.append(
            (
                "polices",
                f"{', '.join(fonts)} : hors des polices de tous les postes, "
                "remplacée(s) chez qui ne les a pas",
            )
        )
    proposed = default.proposed()
    if proposed:
        said.append(
            (
                "styles proposés",
                " · ".join(
                    f"{kind} : {', '.join(name for name, _ in names)}"
                    for kind, names in proposed.items()
                ),
            )
        )
    return Styles(default=default, by_template=by_template, said=tuple(said))


def _written(template: Template, where: str) -> StyleFile | None:
    """Le style qu'un modèle écrit en ligne ; un chemin doit avoir été lu au chargement."""
    if template.style is None:
        return None
    if isinstance(template.style, str):
        raise ValueError(
            f"{where} ({template.style}) non lu — les styles se lisent au chargement de la "
            "config (load_config), ou se donnent par leur contenu (spec)"
        )
    try:
        return StyleFile.model_validate(template.style)
    except ValidationError as exc:
        raise ValueError("\n".join(describe(exc, where))) from None


def _resolved(where: str, *files: StyleFile | None) -> Style:
    try:
        return resolve(*files)
    except ValueError as exc:
        raise ValueError(f"{where} : {exc}") from None


def _spec(ref: StyleRef | None, where: str) -> StyleFile | None:
    if ref is None:
        return None
    if ref.spec is None:
        raise ValueError(
            f"{where} ({ref.file}) non lu — les styles se lisent au chargement de la config "
            "(load_config), ou se donnent par leur contenu (spec)"
        )
    return ref.spec
