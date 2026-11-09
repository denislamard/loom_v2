# SPDX-License-Identifier: Apache-2.0
"""Le style d'un document ``word`` : la charte de l'outil, la disposition d'un modèle.

Deux fichiers YAML, relatifs à ``prompts_dir`` et lus au chargement de la config :

- **la charte**, sur l'outil (``style:``) : page, polices, couleurs, titres,
  tableaux, encadré, légendes, en-tête et pied de page, filigrane ;
- **la disposition**, sur un modèle de document (``templates.<nom>.style``) : les
  mêmes clés, qui complètent la charte clé par clé, plus ``sections`` (la mise en
  page de chaque section du modèle, par son titre) et ``beside`` (des sections
  côte à côte).

Le modèle de langage n'en voit rien : il écrit les mêmes blocs, et le style les
pose. Sans style, le style maison (``Style()``), le même octet pour octet
qu'avant les styles. Les clés sont en anglais, comme la config ; les valeurs
d'une liste fermée, en français (``filets``, ``masque``).

Module léger — Pydantic et ``_templates`` (la bibliothèque standard) — : la
config l'importe pour valider une référence, sans importer l'outil.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, Final, Literal, Self, cast

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from loom_ia.tools_builtin._templates import Template, key

MAX_STYLE_CHARS: Final = 20_000
MAX_LOGO_BYTES: Final = 2_000_000
MAX_BAND_TEXT: Final = 200
MAX_BAND_LINES: Final = 4
# Les polices présentes sur presque tous les postes (Windows, macOS, LibreOffice les
# remplace par des équivalentes métriques) : une autre s'affiche ailleurs dans une
# police de remplacement, et ``loom validate`` le signale.
SAFE_FONTS: Final = frozenset(
    {
        "arial",
        "calibri",
        "cambria",
        "georgia",
        "times new roman",
        "verdana",
        "tahoma",
        "trebuchet ms",
        "garamond",
        "courier new",
    }
)
# Les champs d'un en-tête ou d'un pied de page.
PLACEHOLDERS: Final = frozenset({"page", "pages", "titre"})
_PLACEHOLDER: Final = re.compile(r"\{([^{}]*)\}")
_HEX: Final = re.compile(r"^#?[0-9A-Fa-f]{6}$")
_FONT: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 \-]{0,59}$")
# Les couleurs nommées de la charte, reprises ailleurs par leur nom.
type ColorName = Literal["text", "primary", "accent", "muted", "rule"]
COLOR_NAMES: Final[frozenset[str]] = frozenset({"text", "primary", "accent", "muted", "rule"})

type Position = Literal["gauche", "centre", "droite"]
type Align = Literal["gauche", "centre", "droite", "justifie"]
type Borders = Literal["grille", "filets", "horizontales", "aucune"]
type TableWidth = Literal["pleine", "contenu"]
type TitleMode = Literal["visible", "masque", "discret"]
type TextSize = Literal["normal", "petit"]
type Numbering = Literal["aucune", "decimale", "article"]
type PageSize = Literal["A4", "A5", "Letter"]
type Orientation = Literal["portrait", "paysage"]

# Un centimètre en vingtièmes de point (twips).
CM: Final = 567
PAGE_SIZES: Final[Mapping[str, tuple[int, int]]] = {
    "A4": (11906, 16838),
    "A5": (8391, 11906),
    "Letter": (12240, 15840),
}


def _color(value: str) -> str:
    if value in COLOR_NAMES:
        return value
    if not _HEX.match(value):
        raise ValueError(
            f"couleur {value!r} : six chiffres hexadécimaux (« 1F3864 »), ou une couleur "
            f"nommée de la charte ({', '.join(sorted(COLOR_NAMES))})"
        )
    return value.lstrip("#").upper()


def _hex(value: str) -> str:
    if not _HEX.match(value):
        raise ValueError(f"couleur {value!r} : six chiffres hexadécimaux (« 1F3864 »)")
    return value.lstrip("#").upper()


def _font(value: str) -> str:
    if not _FONT.match(value):
        raise ValueError(
            f"police {value!r} : un nom de police (lettres, chiffres, espaces, tirets ; "
            "60 caractères au plus)"
        )
    return value


def _band_text(value: str) -> str:
    if len(value) > MAX_BAND_TEXT:
        raise ValueError(f"texte de {len(value)} caractères, au-delà de {MAX_BAND_TEXT}")
    unknown = sorted({name for name in _PLACEHOLDER.findall(value) if name not in PLACEHOLDERS})
    if unknown:
        raise ValueError(
            f"champ inconnu {', '.join('{' + n + '}' for n in unknown)} : seuls {{page}}, "
            "{pages} et {titre} sont remplacés (une date fait partie du contenu)"
        )
    return value


def _text_color(value: object) -> object:
    """Les couleurs YAML écrites sans guillemets : ``1F3864`` reste une chaîne, ``000000`` aussi."""
    return f"{value:06d}" if isinstance(value, int) and not isinstance(value, bool) else value


type Color = Annotated[str, BeforeValidator(_text_color), AfterValidator(_color)]
type Hex = Annotated[str, BeforeValidator(_text_color), AfterValidator(_hex)]
type FontName = Annotated[str, AfterValidator(_font)]
type BandText = Annotated[str, AfterValidator(_band_text)]


class _Spec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)


# --- Le fichier de style ------------------------------------------------------------


class MarginsSpec(_Spec):
    top: Annotated[float, Field(ge=0.5, le=5)] | None = None
    bottom: Annotated[float, Field(ge=0.5, le=5)] | None = None
    left: Annotated[float, Field(ge=0.5, le=5)] | None = None
    right: Annotated[float, Field(ge=0.5, le=5)] | None = None


class PageSpec(_Spec):
    size: PageSize | None = None
    orientation: Orientation | None = None
    margins: MarginsSpec | None = None


class BodyFontSpec(_Spec):
    name: FontName | None = None
    size: Annotated[float, Field(ge=7, le=16)] | None = None


class HeadingFontSpec(_Spec):
    name: FontName | None = None


class FontsSpec(_Spec):
    body: BodyFontSpec | None = None
    headings: HeadingFontSpec | None = None


class ColorsSpec(_Spec):
    text: Hex | None = None
    primary: Hex | None = None
    accent: Hex | None = None
    muted: Hex | None = None
    rule: Hex | None = None


class ParagraphsSpec(_Spec):
    line: Annotated[float, Field(ge=1, le=3)] | None = None
    after: Annotated[float, Field(ge=0, le=24)] | None = None
    justify: bool | None = None


class TitleSpec(_Spec):
    size: Annotated[float, Field(ge=10, le=40)] | None = None
    color: Color | None = None
    align: Position | None = None
    uppercase: bool | None = None
    rule: bool | None = None


class LevelSpec(_Spec):
    size: Annotated[float, Field(ge=8, le=32)] | None = None
    color: Color | None = None
    uppercase: bool | None = None


def _level_keys(value: object) -> object:
    """``1:`` en YAML est un nombre : la clé d'un niveau de titre se lit en texte."""
    if not isinstance(value, Mapping):
        return value
    entries = cast(Mapping[object, object], value)
    return {str(name) if isinstance(name, int) else name: given for name, given in entries.items()}


class HeadingsSpec(_Spec):
    numbering: Numbering | None = None
    level1: Annotated[LevelSpec | None, Field(alias="1")] = None
    level2: Annotated[LevelSpec | None, Field(alias="2")] = None
    level3: Annotated[LevelSpec | None, Field(alias="3")] = None

    @model_validator(mode="before")
    @classmethod
    def _keys(cls, value: object) -> object:
        return _level_keys(value)


class TableHeaderSpec(_Spec):
    fill: Hex | None = None
    color: Color | None = None


class TablesSpec(_Spec):
    borders: Borders | None = None
    header: TableHeaderSpec | None = None
    bands: Hex | None = None
    size: Annotated[float, Field(ge=7, le=14)] | None = None
    width: TableWidth | None = None
    align: Position | None = None


class NoteSpec(_Spec):
    fill: Hex | None = None
    border: Color | None = None


class CaptionsSpec(_Spec):
    numbered: bool | None = None


class ImagesSpec(_Spec):
    grid: bool | None = None


class LogoSpec(_Spec):
    """Un logo : son fichier (relatif à ``prompts_dir``), puis ses octets, lus au chargement."""

    file: Path | None = None
    data: bytes | None = None
    width: Annotated[float, Field(ge=0.5, le=17)] = 3.0
    position: Position = "gauche"

    @model_validator(mode="after")
    def _one(self) -> Self:
        if (self.file is None) == (self.data is None):
            raise ValueError("un logo se donne par son fichier (file)")
        return self


class BandSpec(_Spec):
    """Un en-tête ou un pied de page : trois zones, des lignes centrées, un logo."""

    left: BandText | None = None
    center: BandText | None = None
    right: BandText | None = None
    lines: Annotated[list[BandText], Field(max_length=MAX_BAND_LINES)] | None = None
    logo: LogoSpec | None = None
    first_page: bool | None = None


class SectionSpec(_Spec):
    """La disposition d'une section du modèle."""

    title: TitleMode | None = None
    align: Align | None = None
    size: TextSize | None = None
    box: bool | None = None
    columns: Annotated[int, Field(ge=1, le=3)] | None = None
    page_break: bool | None = None
    landscape: bool | None = None
    cover: bool | None = None
    space_before: Annotated[float, Field(ge=0, le=20)] | None = None
    tables: TablesSpec | None = None


class BesideSpec(_Spec):
    """Des sections posées côte à côte, et la part de chacune (en %, 100 en tout)."""

    sections: Annotated[list[str], Field(min_length=2, max_length=3)]
    widths: list[int] | None = None

    @model_validator(mode="after")
    def _widths(self) -> Self:
        if self.widths is None:
            return self
        if len(self.widths) != len(self.sections):
            raise ValueError("beside : une largeur par section")
        if any(width < 10 for width in self.widths) or sum(self.widths) != 100:
            raise ValueError("beside : des largeurs en %, de 10 au moins, qui font 100 en tout")
        return self


class StyleSpec(_Spec):
    """Un fichier de style : la charte, et pour un modèle, la disposition de ses sections."""

    page: PageSpec | None = None
    fonts: FontsSpec | None = None
    colors: ColorsSpec | None = None
    paragraphs: ParagraphsSpec | None = None
    title: TitleSpec | None = None
    headings: HeadingsSpec | None = None
    tables: TablesSpec | None = None
    note: NoteSpec | None = None
    captions: CaptionsSpec | None = None
    images: ImagesSpec | None = None
    header: BandSpec | None = None
    footer: BandSpec | None = None
    # Un texte ; "" retire le filigrane de la charte (une facture définitive).
    watermark: Annotated[str, Field(max_length=40)] | None = None
    sections: dict[str, SectionSpec] = {}
    beside: list[BesideSpec] = []


class StyleRef(BaseModel):
    """Un fichier de style (relatif à ``prompts_dir``), puis son contenu lu.

    Le chargeur de config lit le fichier (et ses logos) et range le style dans
    ``spec`` ; ``file`` n'est alors plus gardé. En config, la forme courte est le
    chemin seul : ``style: styles/charte.yaml``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    file: Path | None = None
    spec: StyleSpec | None = None

    @model_validator(mode="after")
    def _one(self) -> Self:
        if (self.file is None) == (self.spec is None):
            raise ValueError("un style se donne par son fichier (file) ou son contenu (spec)")
        return self


def short_style(value: object) -> object:
    """``style: fichier.yaml`` : la forme courte d'un style."""
    return {"file": value} if isinstance(value, str | Path) else value


type StyleField = Annotated[StyleRef | None, BeforeValidator(short_style)]


# --- Contrôles, au chargement --------------------------------------------------------


def check_charte(spec: StyleSpec, where: str) -> None:
    """La charte de l'outil : ni ``sections`` ni ``beside``, qui disposent un modèle."""
    if spec.sections or spec.beside:
        raise ValueError(
            f"{where} : sections et beside disposent les sections d'un modèle ; ils se "
            "déclarent dans le style du modèle (templates.<nom>.style), pas dans la charte"
        )


def check_layout(spec: StyleSpec, template: Template, where: str) -> None:
    """La disposition d'un modèle, comparée à son plan ; ``ValueError`` qui dit quoi corriger."""
    plan = [section.key for section in template.sections]
    titles = {section.key: section.title for section in template.sections}
    outline = " · ".join(section.title for section in template.sections) or "(aucune section)"
    unknown = [name for name in spec.sections if key(name) not in titles]
    for group in spec.beside:
        unknown += [name for name in group.sections if key(name) not in titles]
    if unknown:
        raise ValueError(
            f"{where} : section(s) hors du modèle {template.name} : "
            f"{', '.join(f'« {n} »' for n in dict.fromkeys(unknown))} (plan : {outline})"
        )
    by_key: dict[str, SectionSpec] = {}
    for name, section in spec.sections.items():
        if key(name) in by_key:
            raise ValueError(f"{where} : section « {name} » donnée deux fois")
        by_key[key(name)] = section
    placed: set[str] = set()
    for group in spec.beside:
        keys = [key(name) for name in group.sections]
        if len(set(keys)) != len(keys) or placed & set(keys):
            raise ValueError(f"{where} : beside, une section n'est posée qu'une fois")
        placed |= set(keys)
        positions = [plan.index(k) for k in keys]
        if positions != list(range(positions[0], positions[0] + len(positions))):
            raise ValueError(
                f"{where} : beside, {' et '.join(f'« {titles[k]} »' for k in keys)} doivent "
                "se suivre dans le plan du modèle"
            )
        for index, k in enumerate(keys):
            section = by_key.get(k)
            if section is None:
                continue
            if section.columns not in (None, 1) or section.landscape or section.cover:
                raise ValueError(
                    f"{where} : « {titles[k]} » est côte à côte (beside) : ni columns, ni "
                    "landscape, ni cover"
                )
            if index and (section.page_break or section.space_before):
                raise ValueError(
                    f"{where} : « {titles[k]} » suit une autre section sur la même ligne "
                    "(beside) : page_break et space_before vont à la première"
                )
    for k, section in by_key.items():
        if section.cover:
            if plan.index(k) != 0:
                raise ValueError(
                    f"{where} : cover (page de garde) va à la première section du modèle, "
                    f"« {titles[plan[0]]} »"
                )
            if section.landscape or section.columns not in (None, 1) or section.box:
                raise ValueError(
                    f"{where} : une page de garde (cover) n'a ni landscape, ni columns, ni box"
                )


def unsafe_fonts(spec: StyleSpec) -> list[str]:
    """Les polices du style hors de la liste sûre : remplacées chez qui ne les a pas."""
    fonts = spec.fonts
    names = [
        fonts.body.name if fonts and fonts.body else None,
        fonts.headings.name if fonts and fonts.headings else None,
    ]
    return [name for name in dict.fromkeys(names) if name and name.lower() not in SAFE_FONTS]


def logos(spec: StyleSpec) -> list[tuple[str, LogoSpec]]:
    """Les logos du style : (« header », logo), (« footer », logo)."""
    return [
        (band, logo)
        for band, given in (("header", spec.header), ("footer", spec.footer))
        if given is not None and (logo := given.logo) is not None
    ]


def merged(*specs: StyleSpec | None) -> StyleSpec:
    """Les styles dans l'ordre, chacun complétant le précédent clé par clé (la disposition
    d'un modèle sur la charte). Une liste (``lines``, ``beside``) se remplace entière."""
    said: dict[str, Any] = {}
    for spec in specs:
        if spec is not None:
            said = _deep(said, spec.model_dump(by_alias=True, exclude_none=True))
    return StyleSpec.model_validate(said)


def _deep(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for name, value in over.items():
        before = out.get(name)
        if isinstance(value, dict) and isinstance(before, dict):
            out[name] = _deep(cast(dict[str, Any], before), cast(dict[str, Any], value))
        else:
            out[name] = value
    return out


# --- Le style résolu : toutes les valeurs, le style maison par défaut ------------------


@dataclass(frozen=True, slots=True)
class Logo:
    data: bytes = field(repr=False)
    # Largeur dans la page, en cm.
    width: float
    position: Position


@dataclass(frozen=True, slots=True)
class Band:
    left: str = ""
    center: str = ""
    right: str = ""
    lines: tuple[str, ...] = ()
    logo: Logo | None = None
    first_page: bool = True

    @property
    def empty(self) -> bool:
        return not (self.left or self.center or self.right or self.lines or self.logo)


@dataclass(frozen=True, slots=True)
class Level:
    # Taille en points, couleur en hexadécimal.
    size: float
    color: str
    uppercase: bool = False


@dataclass(frozen=True, slots=True)
class TitleLook:
    size: float
    color: str
    align: Position = "gauche"
    uppercase: bool = False
    rule: bool = True


@dataclass(frozen=True, slots=True)
class TableLook:
    borders: Borders = "grille"
    header_fill: str | None = "D9E1F2"
    header_color: str | None = None
    bands: str | None = None
    size: float = 10
    width: TableWidth = "pleine"
    align: Position = "gauche"


@dataclass(frozen=True, slots=True)
class SectionLook:
    title: TitleMode = "visible"
    align: Align | None = None
    size: TextSize = "normal"
    box: bool = False
    columns: int = 1
    page_break: bool = False
    landscape: bool = False
    cover: bool = False
    # Espace avant la section, en twips.
    space_before: int = 0
    tables: TableLook | None = None


PLAIN_SECTION: Final = SectionLook()


@dataclass(frozen=True, slots=True)
class Beside:
    # Les clés des sections (``_templates.key``) et la part de chacune (somme 1).
    keys: tuple[str, ...]
    widths: tuple[float, ...]


HOUSE_PRIMARY: Final = "1F3864"
HOUSE_ACCENT: Final = "2F5496"
HOUSE_MUTED: Final = "595959"
HOUSE_RULE: Final = "A6A6A6"


@dataclass(frozen=True, slots=True)
class Style:
    """Un style complet, prêt à rendre ; ``Style()`` est le style maison."""

    page: tuple[int, int] = PAGE_SIZES["A4"]
    landscape: bool = False
    # Haut, bas, gauche, droite, en twips.
    margins: tuple[int, int, int, int] = (1134, 1134, 1134, 1134)
    font: str = "Calibri"
    font_size: float = 11
    heading_font: str | None = None
    text_color: str | None = None
    primary: str = HOUSE_PRIMARY
    accent: str = HOUSE_ACCENT
    muted: str = HOUSE_MUTED
    rule: str = HOUSE_RULE
    # Interligne en 240e de ligne, espace après en twips.
    line: int = 276
    after: int = 120
    justify: bool = False
    title: TitleLook = TitleLook(20, HOUSE_PRIMARY)
    levels: tuple[Level, Level, Level] = (
        Level(16, HOUSE_PRIMARY),
        Level(13, HOUSE_PRIMARY),
        Level(11.5, HOUSE_ACCENT),
    )
    numbering: Numbering = "aucune"
    tables: TableLook = TableLook()
    note_fill: str = "F2F2F2"
    note_border: str = HOUSE_ACCENT
    captions_numbered: bool = False
    images_grid: bool = False
    header: Band | None = None
    footer: Band | None = Band(center="Page {page} / {pages}")
    watermark: str | None = None
    # La disposition des sections, par clé de titre (``_templates.key``).
    sections: Mapping[str, SectionLook] = field(default_factory=dict[str, SectionLook])
    beside: tuple[Beside, ...] = ()

    @property
    def text_width(self) -> int:
        """La largeur utile d'une page portrait, en twips."""
        width = self.page[1] if self.landscape else self.page[0]
        return width - self.margins[2] - self.margins[3]

    def section(self, title: str) -> SectionLook:
        return self.sections.get(key(title), PLAIN_SECTION)


HOUSE: Final = Style()


def resolve(spec: StyleSpec) -> Style:
    """Le style complet : le style maison, complété par ``spec`` (charte et disposition)."""
    house = HOUSE
    colors = spec.colors or ColorsSpec()
    named = {
        "text": colors.text or "000000",
        "primary": colors.primary or house.primary,
        "accent": colors.accent or house.accent,
        "muted": colors.muted or house.muted,
        "rule": colors.rule or house.rule,
    }

    def color(value: str | None, fallback: str) -> str:
        said = value or fallback
        return named.get(said, said)

    page = spec.page or PageSpec()
    margins = page.margins or MarginsSpec()
    fonts = spec.fonts or FontsSpec()
    body = fonts.body or BodyFontSpec()
    paragraphs = spec.paragraphs or ParagraphsSpec()
    title = spec.title or TitleSpec()
    headings = spec.headings or HeadingsSpec()
    defaults = (("primary", 16.0), ("primary", 13.0), ("accent", 11.5))
    levels = tuple(
        Level(
            size=given.size if given and given.size is not None else size,
            color=color(given.color if given else None, fallback),
            uppercase=bool(given and given.uppercase),
        )
        for given, (fallback, size) in zip(
            (headings.level1, headings.level2, headings.level3), defaults, strict=True
        )
    )
    note = spec.note or NoteSpec()
    tables = _tables(spec.tables, house.tables, color)
    sections = {
        key(name): SectionLook(
            title=given.title or "visible",
            align=given.align,
            size=given.size or "normal",
            box=bool(given.box),
            columns=given.columns or 1,
            page_break=bool(given.page_break),
            landscape=bool(given.landscape),
            cover=bool(given.cover),
            space_before=round((given.space_before or 0) * CM),
            tables=_tables(given.tables, tables, color) if given.tables else None,
        )
        for name, given in spec.sections.items()
    }
    beside = tuple(
        Beside(
            keys=tuple(key(name) for name in group.sections),
            widths=tuple(
                (width / 100 for width in group.widths)
                if group.widths
                else (1 / len(group.sections) for _ in group.sections)
            ),
        )
        for group in spec.beside
    )
    return Style(
        page=PAGE_SIZES[page.size or "A4"],
        landscape=page.orientation == "paysage",
        margins=cast(
            tuple[int, int, int, int],
            tuple(
                round(value * CM) if value is not None else default
                for value, default in zip(
                    (margins.top, margins.bottom, margins.left, margins.right),
                    house.margins,
                    strict=True,
                )
            ),
        ),
        font=body.name or house.font,
        font_size=body.size or house.font_size,
        heading_font=fonts.headings.name if fonts.headings else None,
        text_color=colors.text,
        primary=named["primary"],
        accent=named["accent"],
        muted=named["muted"],
        rule=named["rule"],
        line=round(paragraphs.line * 240) if paragraphs.line is not None else house.line,
        after=round(paragraphs.after * 20) if paragraphs.after is not None else house.after,
        justify=bool(paragraphs.justify),
        title=TitleLook(
            size=title.size or house.title.size,
            color=color(title.color, "primary"),
            align=title.align or "gauche",
            uppercase=bool(title.uppercase),
            rule=title.rule if title.rule is not None else True,
        ),
        levels=cast(tuple[Level, Level, Level], levels),
        numbering=headings.numbering or "aucune",
        tables=tables,
        note_fill=note.fill or house.note_fill,
        note_border=color(note.border, "accent"),
        captions_numbered=bool(spec.captions and spec.captions.numbered),
        images_grid=bool(spec.images and spec.images.grid),
        header=_band(spec.header, None),
        footer=_band(spec.footer, house.footer),
        watermark=spec.watermark or None,
        sections=sections,
        beside=beside,
    )


def _tables(given: TablesSpec | None, base: TableLook, color: Any) -> TableLook:
    if given is None:
        return base
    header = given.header or TableHeaderSpec()
    return TableLook(
        borders=given.borders or base.borders,
        header_fill=header.fill or base.header_fill,
        header_color=color(header.color, "") if header.color else base.header_color,
        bands=given.bands or base.bands,
        size=given.size or base.size,
        width=given.width or base.width,
        align=given.align or base.align,
    )


def _band(given: BandSpec | None, base: Band | None) -> Band | None:
    """Un en-tête ou un pied de page déclaré remplace celui de la maison (« Page n / N ») :
    pour garder les numéros de page, les écrire (``right: "Page {page} / {pages}"``)."""
    if given is None:
        return base
    logo = given.logo
    return Band(
        left=given.left or "",
        center=given.center or "",
        right=given.right or "",
        lines=tuple(given.lines or ()),
        logo=Logo(logo.data, logo.width, logo.position)
        if logo is not None and logo.data is not None
        else None,
        first_page=given.first_page if given.first_page is not None else True,
    )
