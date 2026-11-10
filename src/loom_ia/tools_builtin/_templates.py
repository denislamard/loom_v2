# SPDX-License-Identifier: Apache-2.0
"""Les modèles de document d'un outil ``word`` ou ``pdf`` : un fichier YAML.

    description: Note courte à l'équipe ou aux clients.
    instructions: |
      Fichier : workspace/note-<objet>.docx ; n'invente rien.
    style: styles/note.yaml          # facultatif : un fichier de style, ou un style en ligne
    sections:
      Destinataires:
        optional: true
        show_title: false
        instructions: |
          Un paragraphe : « À : », « De : », « Le : ».
      Message:
        styles: { heading1: message_title }
        instructions: |
          Un ou deux paragraphes courts.
      Conditions:
        text: |
          Paiement à 30 jours par virement.

**Les sections sont celles du document**, dans l'ordre du fichier. Leur nom est à la
fois le titre que l'outil imprime (``show_title: false`` pour ne pas l'imprimer) et la
clé que remplit le modèle de langage (``sections`` dans l'appel) ; la comparaison ignore
la numérotation (« 1. »), la casse, les accents et les espaces : « 1. contexte » est la
section « Contexte ». Une section au texte fixe (``text``) est écrite par l'outil seul :
le modèle de langage n'en voit rien. Le reste règle la mise en page de la section
(``box``, ``beside``, ``width``, ``page_break``, ``columns``, ``landscape``, ``cover``,
``space_before``) et ses styles (``styles`` : le style nommé de chaque élément).

Le modèle de langage ne voit que les noms et les descriptions des modèles ; il lit un
modèle à la demande (``action: template``) : ses consignes, et le nom et les consignes de
chaque section qu'il remplit.

Module léger — Pydantic et PyYAML — : le chargeur de config lit les fichiers et appelle
``parse`` ; une faute de modèle se voit dès ``loom validate``.
"""

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated, Any, Final, Literal, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

MAX_TEMPLATES: Final = 20
MAX_TEMPLATE_CHARS: Final = 20_000
MAX_DESCRIPTION: Final = 200
MAX_SECTIONS: Final = 30
MAX_SECTION_NAME: Final = 80
MAX_INSTRUCTIONS: Final = 8_000
MAX_FIXED_TEXT: Final = 8_000
NAME: Final = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
MAX_NAME: Final = 40
# Le nom qui choisit un document libre, quand l'outil a des modèles.
NONE: Final = "none"
# Une section côte à côte : 3 au plus sur une ligne.
MAX_BESIDE: Final = 3
# « 1. », « 2.3 - », « 4) », « a) », « IV. » : la numérotation que le modèle de langage ajoute.
_NUMBERING: Final = re.compile(
    r"^\s*(?:\d+(?:\.\d+)*\s*[.)\-\u2013\u2014:]?|[a-z]\)|[ivx]{1,4}[.)])\s+"
)
_STYLE_NAME: Final = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_CONTROL: Final = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
# Le message de PyYAML devant un texte qui contient « : » sans guillemets.
COLON: Final = "mapping values are not allowed here"
COLON_HINT: Final = (
    "un texte qui contient « : » ou « # » s'écrit entre guillemets, ou après | à la ligne"
)

# Les éléments dont une section choisit le style nommé (``styles``).
type Element = Literal[
    "heading1",
    "heading2",
    "heading3",
    "paragraph",
    "list",
    "note",
    "quote",
    "caption",
    "table",
    "box",
]
ELEMENTS: Final[tuple[str, ...]] = (
    "heading1",
    "heading2",
    "heading3",
    "paragraph",
    "list",
    "note",
    "quote",
    "caption",
    "table",
    "box",
)


class _Spec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


type Text = Annotated[str, Field(max_length=MAX_INSTRUCTIONS)]


class SectionSpec(_Spec):
    """Une section, telle qu'écrite dans le modèle."""

    instructions: Text | None = None
    optional: bool = False
    show_title: bool = True
    text: Annotated[str, Field(min_length=1, max_length=MAX_FIXED_TEXT)] | None = None
    styles: dict[Element, str] = {}
    box: bool = False
    beside: bool = False
    width: Annotated[int, Field(ge=10, le=90)] | None = None
    page_break: bool = False
    columns: Annotated[int, Field(ge=1, le=3)] = 1
    landscape: bool = False
    cover: bool = False
    # En cm : l'adresse d'un courrier dans la fenêtre de l'enveloppe.
    space_before: Annotated[float, Field(ge=0, le=20)] | None = None


class TemplateSpec(_Spec):
    """Le fichier du modèle."""

    description: Annotated[str, Field(max_length=MAX_DESCRIPTION)] | None = None
    instructions: Text | None = None
    # Un fichier de style (chemin relatif à resources_dir), ou un style écrit en ligne.
    style: str | dict[str, Any] | None = None
    sections: Annotated[dict[str, SectionSpec | None], Field(min_length=1)]


@dataclass(frozen=True, slots=True)
class Section:
    """Une section du modèle : son nom, sa place (à partir de 1), ce qu'elle dit."""

    name: str
    position: int
    spec: SectionSpec

    @property
    def key(self) -> str:
        return key(self.name)

    @property
    def optional(self) -> bool:
        return self.spec.optional

    @property
    def fixed(self) -> bool:
        """Une section au texte fixe : écrite par l'outil, jamais par le modèle de langage."""
        return self.spec.text is not None

    @property
    def said(self) -> str:
        """Son nom, et ce qui la distingue : « Photos (optional) », « Conditions (fixed) »."""
        return self.name + (" (optional)" if self.optional else " (fixed)" if self.fixed else "")


@dataclass(frozen=True, slots=True)
class Template:
    """Un modèle lu : son nom, sa description, ses consignes, son style, ses sections."""

    name: str
    description: str | None
    instructions: str | None
    # Le style du modèle tel qu'écrit : un chemin, un style en ligne, ou rien.
    style: str | dict[str, Any] | None
    sections: tuple[Section, ...]
    text: str = ""

    def outline(self) -> str:
        """« Contexte · Photos (optional) · Conditions (fixed) »."""
        return " · ".join(section.said for section in self.sections)

    def section(self, name: str) -> Section | None:
        """La section de ce nom (comparé sans numérotation, casse ni accents)."""
        wanted = key(name)
        return next((s for s in self.sections if s.key == wanted), None)

    def rows(self) -> dict[int, int]:
        """La ligne de chaque section, par sa place : la place de la première de la ligne
        (une section ``beside`` est sur la ligne de la précédente)."""
        rows: dict[int, int] = {}
        current = 1
        for section in self.sections:
            if not section.spec.beside:
                current = section.position
            rows[section.position] = current
        return rows


def key(title: str) -> str:
    """Un titre comparable : sans numérotation, emphase, casse, accents ni espaces superflus."""
    said = re.sub(r"</?u>", "", title, flags=re.IGNORECASE)
    said = unicodedata.normalize("NFKD", said)
    said = "".join(c for c in said if not unicodedata.combining(c)).casefold()
    said = _NUMBERING.sub("", said, count=1)
    said = re.sub(r"[*_`]", "", said)
    return " ".join(said.split()).strip(" .:;")


def check_name(name: str) -> None:
    if not NAME.match(name) or len(name) > MAX_NAME or name == NONE:
        raise ValueError(
            f"nom de modèle {name!r} : minuscules, chiffres et tirets ({MAX_NAME} caractères "
            f"au plus), et pas « {NONE} », qui choisit un document libre"
        )


class _Loader(yaml.SafeLoader):
    """Le chargeur YAML d'un modèle : une clé donnée deux fois est une faute."""


def _mapping(loader: yaml.SafeLoader, node: yaml.MappingNode) -> dict[Any, Any]:
    seen: set[object] = set()
    for key_node, _ in node.value:
        name: object = cast(Any, loader).construct_object(key_node, deep=True)
        if name in seen:
            raise yaml.constructor.ConstructorError(
                None, None, f"clé donnée deux fois : {name}", key_node.start_mark
            )
        seen.add(name)
    return cast(dict[Any, Any], loader.construct_mapping(node, deep=True))


_Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def _old_format(text: str) -> bool:
    """Un modèle markdown (avant 2.1.0) : un en-tête ``---``, des titres ``# Section``."""
    return text.lstrip().startswith("---") or bool(re.search(r"^#\s*\S", text, re.MULTILINE))


OLD_FORMAT: Final = (
    "un modèle est un fichier YAML depuis 2.1.0 (description, instructions, sections) : "
    "chaque titre « # Section » devient une clé de sections, et son texte, ses "
    "instructions (après |)"
)


def parse(name: str, text: str) -> Template:
    """Le modèle et ses sections ; ``ValueError`` qui dit quoi corriger."""
    check_name(name)
    where = f"modèle {name}"
    if len(text) > MAX_TEMPLATE_CHARS:
        raise ValueError(f"{where} : {len(text)} caractères, au-delà de {MAX_TEMPLATE_CHARS}")
    try:
        data = yaml.load(text, Loader=_Loader)
    except yaml.YAMLError as exc:
        if _old_format(text):
            raise ValueError(f"{where} : {OLD_FORMAT}") from None
        hint = f" — {COLON_HINT}" if COLON in str(exc) else ""
        raise ValueError(f"{where} : YAML illisible : {exc}{hint}") from None
    if not isinstance(data, Mapping) or "sections" not in cast(Mapping[object, object], data):
        if _old_format(text) or isinstance(data, str):
            raise ValueError(f"{where} : {OLD_FORMAT}")
        raise ValueError(f"{where} : un modèle a des sections (sections: …)")
    try:
        spec = TemplateSpec.model_validate(data)
    except ValidationError as exc:
        lines = [
            f"{where} : {'.'.join(str(part) for part in item['loc'])} — "
            + item["msg"].removeprefix("Value error, ")
            for item in exc.errors(include_url=False)
        ]
        raise ValueError("\n".join(lines)) from None
    sections = _sections(where, spec)
    _check_layout(where, sections)
    return Template(
        name=name,
        description=spec.description.strip() if spec.description else None,
        instructions=spec.instructions,
        style=spec.style,
        sections=tuple(sections),
        text=text,
    )


def _sections(where: str, spec: TemplateSpec) -> list[Section]:
    if len(spec.sections) > MAX_SECTIONS:
        raise ValueError(f"{where} : {len(spec.sections)} sections, au-delà de {MAX_SECTIONS}")
    sections: list[Section] = []
    seen: dict[str, str] = {}
    for position, (written, given) in enumerate(spec.sections.items(), start=1):
        name = str(written).strip()
        if not key(name) or "\n" in name or len(name) > MAX_SECTION_NAME:
            raise ValueError(
                f"{where} : nom de section {written!r} : un titre d'une ligne, "
                f"{MAX_SECTION_NAME} caractères au plus"
            )
        if key(name) in seen:
            raise ValueError(
                f"{where} : section donnée deux fois : « {seen[key(name)]} » et « {name} »"
            )
        seen[key(name)] = name
        section = Section(name, position, given or SectionSpec())
        for element, style in section.spec.styles.items():
            if not _STYLE_NAME.match(style):
                raise ValueError(
                    f"{where} : « {name} ».styles.{element} : {style!r} n'est pas un nom de style"
                )
        fixed = section.spec.text
        if fixed is not None:
            if section.spec.instructions is not None:
                raise ValueError(
                    f"{where} : « {name} » : text (un texte fixe, écrit par l'outil) ou "
                    "instructions (pour le modèle de langage), pas les deux"
                )
            if section.optional:
                raise ValueError(
                    f"{where} : « {name} » : une section au texte fixe est toujours écrite "
                    "(pas optional)"
                )
            control = _CONTROL.search(fixed)
            if control:
                raise ValueError(
                    f"{where} : « {name} ».text : caractère de contrôle "
                    f"U+{ord(control.group()):04X} refusé"
                )
        sections.append(section)
    return sections


def _check_layout(where: str, sections: Sequence[Section]) -> None:
    """La mise en page des sections : ce qui ne se pose pas est dit au chargement."""
    first = sections[0]
    if first.spec.beside:
        raise ValueError(
            f"{where} : « {first.name} » : beside pose une section à côté de la précédente ; "
            "la première n'en a pas"
        )
    rows: list[list[Section]] = []
    for section in sections:
        if section.spec.beside:
            rows[-1].append(section)
        else:
            rows.append([section])
    for row in rows:
        names = " et ".join(f"« {s.name} »" for s in row)
        if len(row) > MAX_BESIDE:
            raise ValueError(f"{where} : {names} : {MAX_BESIDE} sections côte à côte au plus")
        for index, section in enumerate(row):
            spec = section.spec
            if len(row) > 1 and (spec.columns > 1 or spec.landscape or spec.cover):
                raise ValueError(
                    f"{where} : « {section.name} » est côte à côte (beside) : ni columns, ni "
                    "landscape, ni cover"
                )
            if index and (spec.page_break or spec.space_before):
                raise ValueError(
                    f"{where} : « {section.name} » suit une autre section sur la même ligne "
                    "(beside) : page_break et space_before vont à la première"
                )
        widths = [s.spec.width for s in row]
        if len(row) == 1 and widths[0] is not None:
            raise ValueError(
                f"{where} : « {row[0].name} » : width ne sert qu'à des sections côte à côte "
                "(beside)"
            )
        given = [width for width in widths if width is not None]
        if given and len(given) != len(row):
            raise ValueError(
                f"{where} : {names} : width à toutes les sections de la ligne, ou à aucune"
            )
        if given and sum(given) != 100:
            raise ValueError(f"{where} : {names} : des largeurs (width, en %) qui font 100 en tout")
    for section in sections:
        spec = section.spec
        if not spec.cover:
            continue
        if section.position != 1:
            raise ValueError(
                f"{where} : cover (page de garde) va à la première section, « {first.name} »"
            )
        if spec.landscape or spec.columns > 1 or spec.box:
            raise ValueError(
                f"{where} : une page de garde (cover) n'a ni landscape, ni columns, ni box"
            )


def fixed_paragraphs(text: str) -> list[str]:
    """Le texte fixe d'une section, en paragraphes : une ligne vide les sépare ; un retour à
    la ligne simple reste dans le paragraphe."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    return [part.strip("\n") for part in re.split(r"\n[ \t]*\n", normalized) if part.strip()]
