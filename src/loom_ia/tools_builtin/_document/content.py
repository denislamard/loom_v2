# SPDX-License-Identifier: Apache-2.0
"""Le document validé, prêt à rendre : texte découpé en segments, cellules mises en forme.

Ce que décide le style maison sans dépendre du format : les valeurs d'une
colonne typée écrites à la française (``1 250,50 €``, ``02/10/2026``,
``15,0 %``), leur alignement, la ligne de total calculée par l'outil (un
document n'a pas de formules), la largeur relative des colonnes. Chaque format
(``word``, ``pdf``) ne fait plus que poser ces éléments sur la page.

Dans un texte, ``**gras**`` et ``*italique*`` marquent l'emphase ; un saut de
ligne reste un saut de ligne dans le même paragraphe.
"""

import datetime as dt
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Literal
from zoneinfo import ZoneInfo

from loom_ia.tools_builtin._document.arguments import (
    MAX_CATEGORIES,
    MAX_ITEMS,
    MAX_SERIES,
    MAX_SLICES,
    MAX_TABLE_COLUMNS,
    MAX_TABLE_ROWS,
    MAX_TEXT,
    Chart,
    ChartKind,
    Heading,
    Item,
    ListBlock,
    Note,
    PageBreak,
    Paragraph,
    Problems,
    Quote,
    Table,
    WriteArguments,
)
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
    {"entier", "decimal", "montant_eur", "montant_keur", "pourcentage"}
)
ORDERED_TYPES: Final[frozenset[ColumnType]] = NUMERIC | frozenset[ColumnType](
    {"date", "date_heure"}
)
CENTERED: Final[frozenset[ColumnType]] = frozenset({"date", "date_heure", "annee", "booleen"})
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


@dataclass(frozen=True, slots=True)
class ListOut:
    ordered: bool
    entries: tuple[Entry, ...]


@dataclass(frozen=True, slots=True)
class TableOut:
    headers: tuple[str, ...]
    align: tuple[Align, ...]
    rows: tuple[tuple[str, ...], ...]
    total: tuple[str, ...] | None
    # La part de la largeur utile de chaque colonne ; leur somme vaut 1.
    widths: tuple[float, ...]
    caption: Inline | None


@dataclass(frozen=True, slots=True)
class NoteOut:
    title: str | None
    runs: Inline


@dataclass(frozen=True, slots=True)
class QuoteOut:
    runs: Inline
    source: str | None


@dataclass(frozen=True, slots=True)
class BreakOut:
    pass


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


type BlockOut = (
    TitleOut
    | HeadingOut
    | ParagraphOut
    | ListOut
    | TableOut
    | NoteOut
    | QuoteOut
    | BreakOut
    | ChartOut
)


@dataclass(frozen=True, slots=True)
class Prepared:
    """Le document validé : son titre (pour les propriétés), ses blocs, les avertissements."""

    title: str | None
    blocks: tuple[BlockOut, ...]
    warnings: tuple[str, ...]


def prepare(arguments: WriteArguments, zone: ZoneInfo) -> Prepared:
    """Valide le document entier ; ``ToolError`` qui liste toutes les fautes trouvées."""
    problems = Problems()
    blocks: list[BlockOut] = []
    title = arguments.title.strip() if arguments.title else None
    if title is not None:
        if len(title) > MAX_TITLE:
            problems.error(f"titre : {len(title)} caractères, au-delà de {MAX_TITLE}")
        _clean(title, "titre", problems)
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
                table, stored = _table(block, where, problems, zone)
                blocks.append(table)
                last_table = (block, stored)
            case Chart():
                blocks.append(_chart(block, last_table, where, problems, zone))
            case Note():
                text = _text(block.text, where, problems, required=True)
                note_title = _text(block.title, f"{where}, title", problems) or None
                blocks.append(NoteOut(note_title, inline(text)))
            case Quote():
                text = _text(block.text, where, problems, required=True)
                source = _text(block.source, f"{where}, source", problems) or None
                blocks.append(QuoteOut(inline(text), source))
            case PageBreak():
                blocks.append(BreakOut())
    if not any(not isinstance(b, BreakOut) for b in blocks):
        problems.error("document vide : ni titre, ni bloc de contenu")
    problems.raise_if_any()
    return Prepared(title=title, blocks=tuple(blocks), warnings=tuple(problems.warnings))


def _text(text: str | None, where: str, problems: Problems, *, required: bool = False) -> str:
    said = (text or "").strip()
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


def _list(block: ListBlock, where: str, problems: Problems) -> ListOut:
    entries: list[Entry] = []
    count = 0
    for number, item in enumerate(block.items, start=1):
        at = f"{where}, élément {number}"
        if isinstance(item, Item):
            children = tuple(
                inline(_text(child, f"{at}.{sub}", problems, required=True))
                for sub, child in enumerate(item.items, start=1)
            )
            count += len(children)
            entries.append(Entry(inline(_text(item.text, at, problems, required=True)), children))
        else:
            entries.append(Entry(inline(_text(item, at, problems, required=True))))
        count += 1
    if count > MAX_ITEMS:
        problems.error(f"{where} : {count} éléments, au-delà de {MAX_ITEMS}")
    return ListOut(block.ordered, tuple(entries))


def _table(
    block: Table, where: str, problems: Problems, zone: ZoneInfo
) -> tuple[TableOut, list[list[Stored]]]:
    columns = block.columns
    headers = [column.header.strip() for column in columns]
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
                _clean(value, at, problems)
            cells.append(convert(value, column.type, at, problems, zone))
        stored.append(cells)
    total = _totals(block, stored, where, problems)
    if block.totals and not block.rows:
        problems.warn(f"{where} : aucune ligne, la ligne de total n'est pas écrite")
        total = None
    shown = tuple(
        tuple(display(value, column.type) for value, column in zip(cells, columns, strict=True))
        for cells in stored
    )
    widths = _widths(headers, [*shown, *([total] if total else [])])
    caption = _text(block.caption, f"{where}, caption", problems) or None
    found = TableOut(
        headers=tuple(headers),
        align=tuple(_align(column.type) for column in columns),
        rows=shown,
        total=total,
        widths=widths,
        caption=inline(caption) if caption else None,
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
) -> ChartOut:
    """Le graphique validé, ses données en ligne ou tirées du tableau qui le précède."""
    found = True
    if block.from_table is not None:
        taken = _from_table(block, last_table, where, problems)
        # Sans tableau ou sans ses colonnes, rien à vérifier de plus : la faute est dite.
        found = taken is not None
        categories, series, kind = taken or ([], [], "decimal")
    else:
        kind: ColumnType = block.value_type or "decimal"
        if kind not in NUMERIC:
            problems.error(f"{where} : value_type {kind} : un graphique porte des nombres")
        categories = [str(label) for label in block.categories or []]
        series: list[tuple[str, tuple[float | None, ...]]] = []
        for number, given in enumerate(block.series or [], start=1):
            at = f"{where}, série {number} « {given.name} »"
            if len(given.values) != len(categories):
                problems.error(
                    f"{at} : {len(given.values)} valeurs pour {len(categories)} libellés"
                )
            values = [_numeric(convert(value, kind, at, problems, zone)) for value in given.values]
            series.append((given.name.strip(), tuple(values)))
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
    categories = [display(row[label_at], label_kind) for row in stored]
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
    if kind == "secteurs":
        if len(series) > 1:
            problems.error(f"{where} : un graphique en secteurs n'a qu'une série")
        if len(categories) > MAX_SLICES:
            problems.error(
                f"{where} : {len(categories)} secteurs, au-delà de {MAX_SLICES} — illisible ; "
                "garder les principaux et regrouper le reste en « Autres », ou des barres"
            )
        values = [value for _, values in series[:1] for value in values if value is not None]
        if any(value < 0 for value in values):
            problems.error(f"{where} : une valeur négative n'a pas de secteur")
        elif values and not any(values):
            problems.error(f"{where} : toutes les valeurs sont nulles")


def _totals(
    block: Table, stored: list[list[Stored]], where: str, problems: Problems
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
        if aggregate in {"somme", "moyenne"} and kind not in NUMERIC:
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
            cells.append(_aggregate(aggregate, values, column.type))
        else:
            cells.append("Total" if index == labelled else "")
    return tuple(cells)


def _total_label(block: Table) -> int | None:
    """La colonne où écrire « Total » : la première sans agrégat, texte d'abord (comme excel)."""
    totals = block.totals or {}
    free = [(i, c) for i, c in enumerate(block.columns) if c.header not in totals]
    texts = [i for i, c in free if c.type == "texte"]
    if texts:
        return texts[0]
    return free[0][0] if free else None


def _aggregate(aggregate: Aggregate, values: Sequence[Stored], kind: ColumnType) -> str:
    if aggregate == "nombre":
        return display(len(values), "entier")
    if not values:
        return ""
    try:
        match aggregate:
            case "somme":
                return display(sum(_numbers(values)), kind)
            case "moyenne":
                numbers = _numbers(values)
                mean = sum(numbers) / len(numbers)
                return display(mean, "decimal" if kind == "entier" else kind)
            case "min":
                return display(min(values, key=_order), kind)
            case "max":
                return display(max(values, key=_order), kind)
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


def _widths(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> tuple[float, ...]:
    """La part de la largeur utile de chaque colonne, d'après ce qu'elle contient.

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
    if sum(wants) <= PAGE_CHARS:
        # Tout tient : chaque colonne selon ce qu'elle veut.
        return tuple(want / sum(wants) for want in wants)
    if sum(floors) >= PAGE_CHARS:
        # Même les planchers débordent : la page se partage selon eux.
        return tuple(floor / sum(floors) for floor in floors)
    spare = PAGE_CHARS - sum(floors)
    extra = [want - floor for want, floor in zip(wants, floors, strict=True)]
    shares = [floor + spare * more / sum(extra) for floor, more in zip(floors, extra, strict=True)]
    return tuple(share / PAGE_CHARS for share in shares)


def display(value: Stored, kind: ColumnType) -> str:
    """La valeur telle qu'un document l'écrit, à la française."""
    match value:
        case None:
            return ""
        case dt.datetime():
            return value.strftime("%d/%m/%Y %H:%M")
        case dt.date():
            return value.strftime("%d/%m/%Y")
        case bool():
            return "Oui" if value else "Non"
        case int() | float() if kind in NUMERIC:
            return _number(value, kind)
        case _:
            return str(value)


def _number(value: float, kind: ColumnType) -> str:
    match kind:
        case "entier":
            return _french(f"{round(value):,}")
        case "montant_eur":
            return _french(f"{value:,.2f}") + f"{NBSP}€"
        case "montant_keur":
            return _french(f"{value:,.0f}") + f"{NBSP}k€"
        case "pourcentage":
            return _french(f"{value * 100:,.1f}") + f"{NBSP}%"
        case _:
            return _french(f"{value:,.2f}")


def _french(said: str) -> str:
    """« 1,250.50 » → « 1 250,50 » ; le signe moins reste un trait d'union."""
    return said.replace(",", NBSP).replace(".", ",")


# --- Emphase ---------------------------------------------------------------------------

_MARK: Final = re.compile(r"\*\*|\*")


def inline(text: str) -> Inline:
    """Les segments d'un texte : ``**gras**`` et ``*italique*``, le reste tel quel.

    Un marqueur ouvre seulement s'il est suivi d'un caractère visible et refermé
    plus loin ; il ferme seulement s'il suit un caractère visible. Sinon il
    reste du texte : « 2 * 3 » ne met rien en italique.
    """
    runs: list[Run] = []
    bold = italic = False
    buffer: list[str] = []
    position = 0

    def flush() -> None:
        if buffer:
            said = "".join(buffer)
            if runs and (runs[-1].bold, runs[-1].italic) == (bold, italic):
                runs[-1] = Run(runs[-1].text + said, bold, italic)
            else:
                runs.append(Run(said, bold, italic))
            buffer.clear()

    for found in _MARK.finditer(text):
        start, end = found.span()
        mark = found.group()
        buffer.append(text[position:start])
        position = end
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
