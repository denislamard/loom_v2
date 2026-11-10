# SPDX-License-Identifier: Apache-2.0
"""Le document validé, prêt à rendre : texte découpé en segments, cellules mises en forme.

Ce qui ne dépend pas du format : les valeurs d'une colonne typée écrites dans la
langue du style (``1 250,50 €``, ``02/10/2026``, ``15,0 %`` en français ;
``€1,250.50``, ``10/02/2026``, ``15.0%`` en anglais), leur alignement, la ligne
de total (« Total », ``labels.total``) calculée par l'outil (un
document n'a pas de formules), la largeur relative des colonnes. Chaque format
(``word``, ``pdf``) ne fait plus que poser ces éléments sur la page. La taille
d'une image dans la page aussi (``placed``) ; ses octets sont lus avant, par
l'outil, et ``prepare`` ne touche jamais au disque.

Dans un texte, ``**gras**``, ``*italique*`` et ``<u>souligné</u>`` marquent l'emphase ;
un saut de ligne reste un saut de ligne dans le même paragraphe.
"""

import datetime as dt
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal
from zoneinfo import ZoneInfo

from loom_ia.tools_builtin._document.arguments import (
    MAX_CATEGORIES,
    MAX_IMAGES,
    MAX_IMAGES_BYTES,
    MAX_ITEMS,
    MAX_SERIES,
    MAX_SLICES,
    MAX_TABLE_COLUMNS,
    MAX_TABLE_ROWS,
    MAX_TEXT,
    Chart,
    ChartKind,
    CheckList,
    Contents,
    Heading,
    Image,
    ImageWidth,
    Item,
    ListBlock,
    Note,
    Paragraph,
    Problems,
    Quote,
    Signature,
    Table,
    WriteArguments,
)
from loom_ia.tools_builtin._document.picture import Picture
from loom_ia.tools_builtin._style_params import FRENCH, Labels, Language
from loom_ia.tools_builtin._templates import Template, plan_faults
from loom_ia.tools_builtin._values import Aggregate, ColumnType, Stored, convert

MAX_TITLE: Final = 300
# Largeur d'une colonne, en caractères, pour en tirer la part de la page.
MIN_COLUMN_CHARS: Final = 4
MAX_COLUMN_CHARS: Final = 40
# Ce qu'une colonne prend en plus de son texte : les marges de la cellule, et l'en-tête
# en gras, plus large que le corps.
COLUMN_PADDING: Final = 2
# Ce que tient la largeur utile d'une page A4 (17 cm), en caractères de tableau : 90
# (Helvetica 9,5 pt en fait une centaine en moyenne ; Calibri 10 pt, davantage).
PAGE_CHARS: Final = 90
# Un en-tête est en gras : ses lettres sont plus larges que celles du corps.
BOLD_WIDTH: Final = 1.15
# Une valeur jusqu'à cette longueur (un rang, un code, un montant, une date) n'est
# jamais coupée ; une plus longue (un nom, un texte) peut passer à la ligne.
SHORT_VALUE_CHARS: Final = 14
NUMERIC: Final[frozenset[ColumnType]] = frozenset(
    {"integer", "decimal", "amount_eur", "amount_keur", "percent"}
)
ORDERED_TYPES: Final[frozenset[ColumnType]] = NUMERIC | frozenset[ColumnType]({"date", "datetime"})
CENTERED: Final[frozenset[ColumnType]] = frozenset({"date", "datetime", "year", "boolean"})
# Espace insécable : présente dans toutes les polices, y compris Helvetica (WinAnsi),
# contrairement à l'espace fine insécable (U+202F) de la typographie française.
NBSP: Final = "\u00a0"
# Ce qu'un fichier XML ne peut pas contenir, et qu'aucun document n'affiche.
_CONTROL: Final = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")

type Align = Literal["left", "center", "right"]


@dataclass(frozen=True, slots=True)
class Run:
    """Un segment de texte et son emphase ; ``\\n`` y est un saut de ligne."""

    text: str
    bold: bool = False
    italic: bool = False
    underline: bool = False


type Inline = tuple[Run, ...]


@dataclass(frozen=True, slots=True)
class TitleOut:
    runs: Inline


@dataclass(frozen=True, slots=True)
class HeadingOut:
    level: int
    runs: Inline


@dataclass(frozen=True, slots=True)
class ParagraphOut:
    runs: Inline


@dataclass(frozen=True, slots=True)
class Entry:
    runs: Inline
    children: tuple[Inline, ...] = ()
    # Une liste à cocher : l'élément est coché, et chacun de ses sous-éléments.
    checked: bool = False
    children_checked: tuple[bool, ...] = ()


@dataclass(frozen=True, slots=True)
class ListOut:
    ordered: bool
    entries: tuple[Entry, ...]
    # Une liste à cocher (``blocks: [checkboxes]``) : une case devant chaque élément.
    checkboxes: bool = False


@dataclass(frozen=True, slots=True)
class SignatureOut:
    """La zone de signature : un libellé par signataire, et la mention qu'ils écrivent."""

    signers: tuple[str, ...]
    mention: str | None


@dataclass(frozen=True, slots=True)
class ContentsOut:
    """Le sommaire : la table des matières, tirée des titres par le format."""


@dataclass(frozen=True, slots=True)
class TableOut:
    headers: tuple[str, ...]
    align: tuple[Align, ...]
    rows: tuple[tuple[str, ...], ...]
    total: tuple[str, ...] | None
    # La part de la largeur utile de chaque colonne ; leur somme vaut 1.
    widths: tuple[float, ...]
    caption: Inline | None
    # La largeur que le contenu demande, en part de la largeur utile (1 au plus) : celle
    # d'un tableau qu'un style veut à sa largeur naturelle (``width: contenu``).
    natural: float = 1.0


@dataclass(frozen=True, slots=True)
class NoteOut:
    title: str | None
    runs: Inline


@dataclass(frozen=True, slots=True)
class QuoteOut:
    runs: Inline
    source: str | None


@dataclass(frozen=True, slots=True)
class ChartOut:
    """Un graphique : ses libellés, ses séries de nombres, et le type de ces nombres."""

    kind: ChartKind
    title: str | None
    categories: tuple[str, ...]
    # (nom, valeurs) ; une valeur manquante est ``None`` : un trou dans la courbe.
    series: tuple[tuple[str, tuple[float | None, ...]], ...]
    value_type: ColumnType
    caption: Inline | None


@dataclass(frozen=True, slots=True)
class ImageOut:
    """Une image posée dans le texte : ses octets, sa taille dans la page, ses textes.

    ``source`` : l'adresse du fichier lu (« <dossier>/<chemin> »), que garde le .docx.
    """

    picture: Picture
    source: str
    # Largeur et hauteur dans la page, en centièmes de millimètre.
    width: int
    height: int
    alt: str
    caption: Inline | None
    # La largeur demandée : une grille d'images (style) pose côte à côte les « half ».
    wanted: ImageWidth = "full"


type BlockOut = (
    TitleOut
    | HeadingOut
    | ParagraphOut
    | ListOut
    | TableOut
    | NoteOut
    | QuoteOut
    | ChartOut
    | ImageOut
    | SignatureOut
    | ContentsOut
)


@dataclass(frozen=True, slots=True)
class Prepared:
    """Le document validé : son titre (pour les propriétés), ses blocs, les avertissements."""

    title: str | None
    blocks: tuple[BlockOut, ...]
    warnings: tuple[str, ...]
    # Le modèle suivi, que le document garde dans ses propriétés ; ``None`` sans modèle.
    template: str | None = None


# Ce qu'a donné la lecture de chaque image, par chemin tel que donné : l'image, ou ce
# qui l'empêche (droit, fichier absent, format…). Lue par l'outil, avant ``prepare``.
type Pictures = Mapping[str, Picture | str]
NO_PICTURES: Final[Pictures] = {}


def prepare(
    arguments: WriteArguments,
    zone: ZoneInfo,
    pictures: Pictures = NO_PICTURES,
    template: Template | None = None,
    labels: Labels = FRENCH,
) -> Prepared:
    """Valide le document entier ; ``ToolError`` qui liste toutes les fautes trouvées.

    Sans disque : les images sont lues avant, par l'outil (``pictures``). ``template`` :
    le modèle que le document dit suivre ; ses titres de niveau 1 doivent suivre son plan.
    ``labels`` : la langue du style, et le libellé de la ligne de total.
    """
    problems = Problems()
    blocks: list[BlockOut] = []
    title = lines(arguments.title).strip() if arguments.title else None
    if title is not None:
        if len(title) > MAX_TITLE:
            problems.error(f"title : {len(title)} caractères, au-delà de {MAX_TITLE}")
        _clean(title, "title", problems)
        blocks.append(TitleOut(inline(title)))
    level = 0
    # Le dernier tableau écrit, et ses valeurs : la source d'un graphique ``from_table``.
    last_table: tuple[Table, list[list[Stored]]] | None = None
    for index, block in enumerate(arguments.blocks, start=1):
        where = f"bloc {index} ({block.type})"
        match block:
            case Heading():
                text = _text(block.text, where, problems, required=True)
                if block.level > level + 1:
                    problems.warn(
                        f"{where} : titre de niveau {block.level} après un niveau {level} — "
                        "un niveau sauté brouille le plan du document"
                    )
                level = block.level
                blocks.append(HeadingOut(block.level, inline(text)))
            case Paragraph():
                text = _text(block.text, where, problems, required=True)
                blocks.append(ParagraphOut(inline(text)))
            case ListBlock():
                blocks.append(_list(block, where, problems))
            case Table():
                table, stored = _table(block, where, problems, zone, labels)
                blocks.append(table)
                last_table = (block, stored)
            case Chart():
                blocks.append(_chart(block, last_table, where, problems, zone, labels.language))
            case Note():
                text = _text(block.text, where, problems, required=True)
                note_title = _text(block.title, f"{where}, title", problems) or None
                blocks.append(NoteOut(note_title, inline(text)))
            case Quote():
                text = _text(block.text, where, problems, required=True)
                source = _text(block.source, f"{where}, source", problems) or None
                blocks.append(QuoteOut(inline(text), source))
            case Image():
                found = _image(block, pictures, where, problems)
                if found is not None:
                    blocks.append(found)
            case Signature():
                blocks.append(_signature(block, where, problems))
            case Contents():
                if any(isinstance(b, ContentsOut) for b in blocks):
                    problems.error(f"{where} : un seul sommaire par document")
                blocks.append(ContentsOut())
    _check_images(arguments, pictures, problems)
    if any(isinstance(b, ContentsOut) for b in blocks) and not any(
        isinstance(b, HeadingOut) for b in blocks
    ):
        problems.warn("sommaire : le document n'a aucun titre, le sommaire sera vide")
    if template is not None:
        _check_plan(blocks, template, problems)
    if not blocks:
        problems.error("document vide : ni titre, ni bloc de contenu")
    problems.raise_if_any()
    return Prepared(
        title=title,
        blocks=tuple(blocks),
        warnings=tuple(problems.warnings),
        template=template.name if template is not None else None,
    )


def _check_plan(blocks: Sequence[BlockOut], template: Template, problems: Problems) -> None:
    """Les titres de niveau 1 du document, comparés au plan du modèle."""
    headings = [
        plain(block.runs) for block in blocks if isinstance(block, HeadingOut) and block.level == 1
    ]
    faults = plan_faults(template, headings)
    for fault in faults:
        problems.error(f"modèle {template.name} : {fault}")
    if faults:
        problems.error(
            f"modèle {template.name}, plan attendu (titres de niveau 1, dans l'ordre) : "
            f"{template.outline()}"
        )


def lines(text: str) -> str:
    """Les fins de ligne ramenées à ``\\n`` : un ``\\r\\n`` ou un ``\\r`` venu du modèle (un
    texte collé d'un document Windows) laisserait un ``\\r`` brut dans le document (B-3)."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _text(text: str | None, where: str, problems: Problems, *, required: bool = False) -> str:
    said = lines(text or "").strip()
    if required and not said:
        problems.error(f"{where} : texte vide")
    if len(said) > MAX_TEXT:
        problems.error(f"{where} : {len(said)} caractères, au-delà de {MAX_TEXT}")
    _clean(said, where, problems)
    return said


def _clean(text: str, where: str, problems: Problems) -> None:
    found = _CONTROL.search(text)
    if found:
        problems.error(f"{where} : caractère de contrôle U+{ord(found.group()):04X} refusé")


# Une case, en tête d'un élément de liste à cocher : « [x] » cochée, « [ ] » vide.
_CHECKBOX: Final = re.compile(r"^\[([ xX])\]\s+")
MAX_SIGNER: Final = 80
MAX_MENTION: Final = 200


def _list(block: ListBlock, where: str, problems: Problems) -> ListOut:
    entries: list[Entry] = []
    count = 0
    checkboxes = isinstance(block, CheckList) and block.checkboxes
    if checkboxes and block.ordered:
        problems.error(f"{where} : une liste à cocher n'est pas numérotée (ordered)")

    def item_text(text: str, at: str) -> tuple[Inline, bool]:
        said = _text(text, at, problems, required=True)
        if not checkboxes:
            return inline(said), False
        found = _CHECKBOX.match(said)
        if found is None:
            problems.error(
                f"{at} : dans une liste à cocher (checkboxes), chaque élément commence par "
                "[x] (coché) ou [ ] (vide)"
            )
            return inline(said), False
        return inline(said[found.end() :]), found.group(1) != " "

    for number, item in enumerate(block.items, start=1):
        at = f"{where}, élément {number}"
        if isinstance(item, Item):
            children = [
                item_text(child, f"{at}.{sub}") for sub, child in enumerate(item.items, start=1)
            ]
            count += len(children)
            runs, checked = item_text(item.text, at)
            entries.append(
                Entry(
                    runs,
                    tuple(child for child, _ in children),
                    checked,
                    tuple(mark for _, mark in children) if checkboxes else (),
                )
            )
        else:
            runs, checked = item_text(item, at)
            entries.append(Entry(runs, checked=checked))
        count += 1
    if count > MAX_ITEMS:
        problems.error(f"{where} : {count} éléments, au-delà de {MAX_ITEMS}")
    return ListOut(block.ordered, tuple(entries), checkboxes)


def _signature(block: Signature, where: str, problems: Problems) -> SignatureOut:
    signers: list[str] = []
    for number, signer in enumerate(block.signers, start=1):
        said = _text(signer, f"{where}, signataire {number}", problems, required=True)
        if len(said) > MAX_SIGNER:
            problems.error(
                f"{where}, signataire {number} : {len(said)} caractères, au-delà de {MAX_SIGNER}"
            )
        signers.append(said)
    mention = _text(block.mention, f"{where}, mention", problems) or None
    if mention is not None and len(mention) > MAX_MENTION:
        problems.error(f"{where}, mention : {len(mention)} caractères, au-delà de {MAX_MENTION}")
    return SignatureOut(tuple(signers), mention)


def _table(
    block: Table, where: str, problems: Problems, zone: ZoneInfo, labels: Labels
) -> tuple[TableOut, list[list[Stored]]]:
    columns = block.columns
    headers = [lines(column.header).strip() for column in columns]
    if len(columns) > MAX_TABLE_COLUMNS:
        problems.error(
            f"{where} : {len(columns)} colonnes, au-delà de {MAX_TABLE_COLUMNS} — une page "
            "n'en tient pas plus ; scinder le tableau, ou en faire un classeur (excel)"
        )
    doubles = sorted({h for h in headers if headers.count(h) > 1})
    if doubles:
        problems.error(f"{where} : en-tête donné deux fois : {', '.join(doubles)}")
    for header in headers:
        _clean(header, where, problems)
    if len(block.rows) > MAX_TABLE_ROWS:
        problems.error(
            f"{where} : {len(block.rows)} lignes, au-delà de {MAX_TABLE_ROWS} — un tableau "
            "plus long se lit mieux dans un classeur (excel)"
        )
    stored: list[list[Stored]] = []
    for number, row in enumerate(block.rows, start=1):
        if len(row) > len(columns):
            problems.error(
                f"{where}, ligne {number} : {len(row)} valeurs pour {len(columns)} colonnes"
            )
        cells: list[Stored] = []
        for index, column in enumerate(columns):
            value = row[index] if index < len(row) else None
            at = f"{where}, ligne {number}, « {column.header} »"
            if isinstance(value, str):
                value = lines(value)
                _clean(value, at, problems)
            cells.append(convert(value, column.type, at, problems, zone))
        stored.append(cells)
    total = _totals(block, stored, where, problems, labels)
    if block.totals and not block.rows:
        problems.warn(f"{where} : aucune ligne, la ligne de total n'est pas écrite")
        total = None
    shown = tuple(
        tuple(
            display(value, column.type, labels.language)
            for value, column in zip(cells, columns, strict=True)
        )
        for cells in stored
    )
    widths, natural = _widths(headers, [*shown, *([total] if total else [])])
    caption = _text(block.caption, f"{where}, caption", problems) or None
    found = TableOut(
        headers=tuple(headers),
        align=tuple(_align(column.type) for column in columns),
        rows=shown,
        total=total,
        widths=widths,
        caption=inline(caption) if caption else None,
        natural=natural,
    )
    return found, stored


# --- Graphiques ----------------------------------------------------------------------------

MAX_LABEL: Final = 60


def _chart(
    block: Chart,
    last_table: tuple[Table, list[list[Stored]]] | None,
    where: str,
    problems: Problems,
    zone: ZoneInfo,
    language: Language = "fr",
) -> ChartOut:
    """Le graphique validé, ses données en ligne ou tirées du tableau qui le précède."""
    found = True
    if block.from_table is not None:
        taken = _from_table(block, last_table, where, problems, language)
        # Sans tableau ou sans ses colonnes, rien à vérifier de plus : la faute est dite.
        found = taken is not None
        categories, series, kind = taken or ([], [], "decimal")
    else:
        kind: ColumnType = block.value_type or "decimal"
        if kind not in NUMERIC:
            problems.error(f"{where} : value_type {kind} : un graphique porte des nombres")
        categories = [lines(str(label)) for label in block.categories or []]
        series: list[tuple[str, tuple[float | None, ...]]] = []
        for number, given in enumerate(block.series or [], start=1):
            at = f"{where}, série {number} « {given.name} »"
            if len(given.values) != len(categories):
                problems.error(
                    f"{at} : {len(given.values)} valeurs pour {len(categories)} libellés"
                )
            values = [_numeric(convert(value, kind, at, problems, zone)) for value in given.values]
            series.append((lines(given.name).strip(), tuple(values)))
    if found:
        _check_chart(block.kind, categories, series, where, problems)
    for label in categories:
        _clean(label, where, problems)
    title = _text(block.title, f"{where}, title", problems) or None
    caption = _text(block.caption, f"{where}, caption", problems) or None
    return ChartOut(
        kind=block.kind,
        title=title,
        categories=tuple(label.strip()[:MAX_LABEL] for label in categories),
        series=tuple(series),
        value_type=kind,
        caption=inline(caption) if caption else None,
    )


def _from_table(
    block: Chart,
    last_table: tuple[Table, list[list[Stored]]] | None,
    where: str,
    problems: Problems,
    language: Language,
) -> tuple[list[str], list[tuple[str, tuple[float | None, ...]]], ColumnType] | None:
    """Libellés, séries et type tirés du tableau ; ``None`` si on ne peut pas les tirer."""
    source = block.from_table
    assert source is not None
    if last_table is None:
        problems.error(f"{where} : from_table, mais aucun tableau ne précède le graphique")
        return None
    table, stored = last_table
    headers = [column.header.strip() for column in table.columns]
    wanted = [source.categories, *source.values]
    unknown = [name for name in wanted if name.strip() not in headers]
    if unknown:
        problems.error(
            f"{where} : from_table, colonne(s) inconnue(s) du tableau précédent : "
            f"{', '.join(unknown)} (colonnes : {', '.join(headers)})"
        )
        return None
    label_at = headers.index(source.categories.strip())
    label_kind = table.columns[label_at].type
    categories = [display(row[label_at], label_kind, language) for row in stored]
    kinds: set[ColumnType] = {
        table.columns[headers.index(name.strip())].type for name in source.values
    }
    kind: ColumnType = next(iter(kinds))
    if len(kinds) > 1 or kind not in NUMERIC:
        problems.error(
            f"{where} : from_table, les séries viennent de colonnes de nombres d'un même type "
            f"(ici : {', '.join(sorted(kinds))})"
        )
    series = [
        (
            name.strip(),
            tuple(_numeric(row[headers.index(name.strip())]) for row in stored),
        )
        for name in source.values
    ]
    return categories, series, kind


def _numeric(value: Stored) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _check_chart(
    kind: ChartKind,
    categories: Sequence[str],
    series: Sequence[tuple[str, tuple[float | None, ...]]],
    where: str,
    problems: Problems,
) -> None:
    if not categories:
        problems.error(f"{where} : aucun libellé, le graphique serait vide")
    if len(categories) > MAX_CATEGORIES:
        problems.error(
            f"{where} : {len(categories)} libellés, au-delà de {MAX_CATEGORIES} — regrouper, "
            "ou garder les principaux"
        )
    if not series:
        problems.error(f"{where} : aucune série")
    if len(series) > MAX_SERIES:
        problems.error(f"{where} : {len(series)} séries, au-delà de {MAX_SERIES}")
    if any(len(label) > MAX_LABEL for label in categories):
        problems.warn(f"{where} : libellé de plus de {MAX_LABEL} caractères, coupé")
    if kind == "pie":
        if len(series) > 1:
            problems.error(f"{where} : un graphique pie n'a qu'une série")
        if len(categories) > MAX_SLICES:
            problems.error(
                f"{where} : {len(categories)} secteurs, au-delà de {MAX_SLICES} — illisible ; "
                "garder les principaux et regrouper le reste en « Autres », ou bar"
            )
        values = [value for _, values in series[:1] for value in values if value is not None]
        if any(value < 0 for value in values):
            problems.error(f"{where} : une valeur négative n'a pas de secteur")
        elif values and not any(values):
            problems.error(f"{where} : toutes les valeurs sont nulles")


# --- Images ----------------------------------------------------------------------------

# La largeur d'une image dans la page, en centièmes de millimètre : la largeur utile
# (17 cm), sa moitié, et celle d'un logo.
IMAGE_WIDTHS: Final[Mapping[ImageWidth, int]] = {
    "full": 17_000,
    "half": 8_500,
    "small": 4_000,
}
# La hauteur au plus : la page utile (25,7 cm), moins la place d'une légende.
MAX_IMAGE_HEIGHT: Final = 21_000
# En deçà, une image s'affiche agrandie au-delà de ses pixels : floue à l'impression.
MIN_PPI: Final = 96


def _image(block: Image, pictures: Pictures, where: str, problems: Problems) -> ImageOut | None:
    path = block.path.strip()
    caption = _text(block.caption, f"{where}, caption", problems) or None
    alt = _text(block.alt, f"{where}, alt", problems)
    found = pictures.get(block.path)
    if not isinstance(found, Picture):
        problems.error(f"{where} : {found or 'image non lue'}")
        return None
    width, height = placed(block.width, found.width, found.height)
    ppi = found.width / (width / 2_540)
    if ppi < MIN_PPI:
        problems.warn(
            f"{where} : {path}, {found.width} x {found.height} pixels sur {_cm(width)} cm "
            f"({ppi:.0f} pixels par pouce) : floue à l'impression ; une image plus grande, ou "
            "une largeur plus petite"
        )
    if found.orientation in (5, 6, 7, 8):
        problems.warn(
            f"{where} : {path} est une photo prise de côté (orientation EXIF "
            f"{found.orientation}) : elle peut s'afficher couchée ; la redresser avant"
        )
    if found.animated:
        problems.warn(f"{where} : {path} est animée : seule sa première image s'affiche")
    if not alt and caption is None:
        problems.warn(
            f"{where} : ni alt ni caption : l'image n'a pas de texte pour qui ne la voit pas"
        )
    return ImageOut(
        picture=found,
        source=path,
        width=width,
        height=height,
        alt=alt or caption or "",
        caption=inline(caption) if caption else None,
        wanted=block.width,
    )


def _cm(hundredths: int) -> str:
    return f"{hundredths / 1000:.1f}".replace(".", ",")


def placed(wanted: ImageWidth, pixels_wide: int, pixels_high: int) -> tuple[int, int]:
    """Largeur et hauteur dans la page (centièmes de mm) : la largeur demandée, les
    proportions de l'image, la hauteur bornée à celle de la page."""
    width = IMAGE_WIDTHS[wanted]
    height = round(width * pixels_high / pixels_wide)
    if height > MAX_IMAGE_HEIGHT:
        width, height = round(MAX_IMAGE_HEIGHT * pixels_wide / pixels_high), MAX_IMAGE_HEIGHT
    return width, height


def _check_images(arguments: WriteArguments, pictures: Pictures, problems: Problems) -> None:
    """Les bornes du document : nombre d'images, octets des images distinctes."""
    count = sum(1 for block in arguments.blocks if isinstance(block, Image))
    if count > MAX_IMAGES:
        problems.error(f"{count} images, au-delà de {MAX_IMAGES}")
    distinct = {p.digest: len(p.data) for p in pictures.values() if isinstance(p, Picture)}
    if sum(distinct.values()) > MAX_IMAGES_BYTES:
        problems.error(
            f"images de {sum(distinct.values())} octets en tout, au-delà de {MAX_IMAGES_BYTES} "
            "— des images plus légères (JPEG pour une photo)"
        )


def _totals(
    block: Table, stored: list[list[Stored]], where: str, problems: Problems, labels: Labels
) -> tuple[str, ...] | None:
    totals = block.totals or {}
    if not totals:
        return None
    headers = [column.header for column in block.columns]
    for header, aggregate in totals.items():
        if header not in headers:
            problems.error(f"{where} : total sur une colonne inconnue {header!r}")
            continue
        kind = block.columns[headers.index(header)].type
        if aggregate in {"sum", "average"} and kind not in NUMERIC:
            problems.error(
                f"{where} : {aggregate} sur « {header} », colonne {kind} : pas un nombre"
            )
        elif aggregate in {"min", "max"} and kind not in ORDERED_TYPES:
            problems.error(f"{where} : {aggregate} sur « {header} », colonne {kind} : sans ordre")
    labelled = _total_label(block)
    cells: list[str] = []
    for index, column in enumerate(block.columns):
        aggregate = totals.get(column.header)
        values = [row[index] for row in stored if row[index] is not None]
        if aggregate is not None:
            cells.append(_aggregate(aggregate, values, column.type, labels.language))
        else:
            cells.append(labels.total if index == labelled else "")
    return tuple(cells)


def _total_label(block: Table) -> int | None:
    """La colonne où écrire « Total » : la première sans agrégat, texte d'abord (comme excel)."""
    totals = block.totals or {}
    free = [(i, c) for i, c in enumerate(block.columns) if c.header not in totals]
    texts = [i for i, c in free if c.type == "text"]
    if texts:
        return texts[0]
    return free[0][0] if free else None


def _aggregate(
    aggregate: Aggregate, values: Sequence[Stored], kind: ColumnType, language: Language
) -> str:
    if aggregate == "count":
        return display(len(values), "integer", language)
    if not values:
        return ""
    try:
        match aggregate:
            case "sum":
                return display(sum(_numbers(values)), kind, language)
            case "average":
                numbers = _numbers(values)
                mean = sum(numbers) / len(numbers)
                return display(mean, "decimal" if kind == "integer" else kind, language)
            case "min":
                return display(min(values, key=_order), kind, language)
            case "max":
                return display(max(values, key=_order), kind, language)
    except TypeError:
        # Types mêlés : la faute est déjà dite par la validation des totaux.
        return ""


def _numbers(values: Sequence[Stored]) -> list[float | int]:
    return [v for v in values if isinstance(v, int | float) and not isinstance(v, bool)]


def _order(value: Stored) -> float | dt.datetime:
    match value:
        case dt.datetime():
            return value
        case dt.date():
            return dt.datetime(value.year, value.month, value.day)
        case int() | float():
            return value
        case _:
            raise TypeError(value)


def _align(kind: ColumnType) -> Align:
    if kind in NUMERIC:
        return "right"
    return "center" if kind in CENTERED else "left"


def _widths(
    headers: Sequence[str], rows: Sequence[Sequence[str]]
) -> tuple[tuple[float, ...], float]:
    """La part de la largeur utile de chaque colonne, d'après ce qu'elle contient ; et la
    part de la page que le tableau demande (1 au plus).

    Chaque colonne a un **plancher** : son mot d'en-tête le plus long, en gras, et sa
    valeur la plus longue si elle est courte (``SHORT_VALUE_CHARS``) ; ni l'un ni
    l'autre ne se coupe. Ce qui reste de la page va aux colonnes qui veulent plus
    (un nom, un texte, qui passent à la ligne entre deux mots), en proportion. Un
    partage seulement proportionnel écrasait les petites colonnes à côté d'une large :
    « Rang », « Code » et « C-429 » coupés (essai réel du 29/10).
    """
    floors: list[int] = []
    wants: list[int] = []
    for index, header in enumerate(headers):
        longest = max((len(row[index]) for row in rows), default=0)
        word = max((len(part) for part in header.split()), default=0)
        # Un en-tête long se coupe sur deux lignes, entre deux mots : il compte pour sa moitié.
        wanted = max(longest, word, (len(header) + 1) // 2, min(len(header), 12))
        # Une valeur courte (un rang, un code, un montant) ne se coupe pas non plus.
        short = longest if longest <= SHORT_VALUE_CHARS else 0
        floor = max(math.ceil(word * BOLD_WIDTH), MIN_COLUMN_CHARS, short) + COLUMN_PADDING
        want = min(MAX_COLUMN_CHARS, max(MIN_COLUMN_CHARS, wanted)) + COLUMN_PADDING
        floors.append(floor)
        wants.append(max(want, floor))
    natural = min(1.0, sum(wants) / PAGE_CHARS)
    if sum(wants) <= PAGE_CHARS:
        # Tout tient : chaque colonne selon ce qu'elle veut.
        return tuple(want / sum(wants) for want in wants), natural
    if sum(floors) >= PAGE_CHARS:
        # Même les planchers débordent : la page se partage selon eux.
        return tuple(floor / sum(floors) for floor in floors), natural
    spare = PAGE_CHARS - sum(floors)
    extra = [want - floor for want, floor in zip(wants, floors, strict=True)]
    shares = [floor + spare * more / sum(extra) for floor, more in zip(floors, extra, strict=True)]
    return tuple(share / PAGE_CHARS for share in shares), natural


def display(value: Stored, kind: ColumnType, language: Language = "fr") -> str:
    """La valeur telle qu'un document l'écrit, dans sa langue (à la française par défaut)."""
    english = language == "en"
    match value:
        case None:
            return ""
        case dt.datetime():
            return value.strftime("%m/%d/%Y %H:%M" if english else "%d/%m/%Y %H:%M")
        case dt.date():
            return value.strftime("%m/%d/%Y" if english else "%d/%m/%Y")
        case bool():
            return ("Yes" if value else "No") if english else ("Oui" if value else "Non")
        case int() | float() if kind in NUMERIC:
            return _english(value, kind) if english else _number(value, kind)
        case str() if kind == "boolean" and english:
            # Une colonne boolean garde « Oui » et « Non » (``_values``, comme excel).
            return {"Oui": "Yes", "Non": "No"}.get(value, value)
        case _:
            return str(value)


def _number(value: float, kind: ColumnType) -> str:
    match kind:
        case "integer":
            return _french(f"{round(value):,}")
        case "amount_eur":
            return _french(f"{value:,.2f}") + f"{NBSP}€"
        case "amount_keur":
            return _french(f"{value:,.0f}") + f"{NBSP}k€"
        case "percent":
            return _french(f"{value * 100:,.1f}") + f"{NBSP}%"
        case _:
            return _french(f"{value:,.2f}")


def _english(value: float, kind: ColumnType) -> str:
    """« €1,250.50 », « 15.0% » ; le signe moins devant le symbole (« -€12.00 »)."""
    sign = "-" if value < 0 else ""
    match kind:
        case "integer":
            return f"{round(value):,}"
        case "amount_eur":
            return f"{sign}€{abs(value):,.2f}"
        case "amount_keur":
            return f"{sign}€{abs(value):,.0f}k"
        case "percent":
            return f"{value * 100:,.1f}%"
        case _:
            return f"{value:,.2f}"


def _french(said: str) -> str:
    """« 1,250.50 » → « 1 250,50 » ; le signe moins reste un trait d'union."""
    return said.replace(",", NBSP).replace(".", ",")


# --- Emphase ---------------------------------------------------------------------------

_MARK: Final = re.compile(r"\*\*|\*|<u>|</u>")


def inline(text: str) -> Inline:
    """Les segments d'un texte : ``**gras**``, ``*italique*``, ``<u>souligné</u>``, le reste
    tel quel.

    Un marqueur ouvre seulement s'il est suivi d'un caractère visible et refermé
    plus loin ; il ferme seulement s'il suit un caractère visible. Sinon il
    reste du texte : « 2 * 3 » ne met rien en italique. ``<u>`` ouvre s'il est refermé
    plus loin et ``</u>`` ferme ce qui est ouvert, sans imbrication ; sinon, la balise
    reste du texte.
    """
    runs: list[Run] = []
    bold = italic = underline = False
    buffer: list[str] = []
    position = 0

    def flush() -> None:
        if buffer:
            said = "".join(buffer)
            last = runs[-1] if runs else None
            if last is not None and (last.bold, last.italic, last.underline) == (
                bold,
                italic,
                underline,
            ):
                runs[-1] = Run(last.text + said, bold, italic, underline)
            else:
                runs.append(Run(said, bold, italic, underline))
            buffer.clear()

    for found in _MARK.finditer(text):
        start, end = found.span()
        mark = found.group()
        buffer.append(text[position:start])
        position = end
        if mark in ("<u>", "</u>"):
            # Une balise : <u> ouvre s'il est refermé plus loin, </u> ferme ce qui est ouvert.
            if (mark == "<u>" and not underline and "</u>" in text[end:]) or (
                mark == "</u>" and underline
            ):
                flush()
                underline = not underline
            else:
                buffer.append(mark)
            continue
        before = text[start - 1] if start else " "
        after = text[end] if end < len(text) else " "
        is_open = bold if mark == "**" else italic
        closing = is_open and not before.isspace()
        opening = not is_open and not after.isspace() and mark in text[end:]
        if closing or opening:
            flush()
            if mark == "**":
                bold = not bold
            else:
                italic = not italic
        else:
            buffer.append(mark)
    buffer.append(text[position:])
    # Un marqueur ouvert jamais refermé : l'emphase s'arrête en fin de texte.
    flush()
    return tuple(run for run in runs if run.text)


def plain(runs: Inline) -> str:
    return "".join(run.text for run in runs)
