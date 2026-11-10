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
un saut de ligne reste un saut de ligne dans le même paragraphe. Un paragraphe, un élément
de liste, une note, une citation, une cellule de texte et la valeur d'un champ prennent
aussi un lien (``[texte](https://…)``), une note de bas de page (``^[texte]``) et un renvoi
à un titre du document (``§[Titre]``) : ``rich``. Un titre, une légende, un libellé n'ont
que l'emphase, et un bloc ``code`` n'a aucun balisage.

Les calculs sont ceux de l'outil, jamais du modèle de langage, en ``Decimal`` et arrondis
au demi supérieur : une colonne calculée (``compute`` : ``+ - * /``, des nombres et des
``{colonnes}`` de la ligne, lus par ``ast``, jamais par ``eval``), le sous-total de chaque
groupe de lignes, et le récapitulatif de TVA d'une facture (``invoice_totals`` : la TVA de
chaque taux sur le total HT de ce taux). Un montant en euros s'arrondit au centime dès la
lecture : ce que le document additionne est ce qu'il affiche.
"""

import ast
import dataclasses
import datetime as dt
import json
import math
import re
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Final, Literal
from zoneinfo import ZoneInfo

from loom_ia.tools_builtin._document.arguments import (
    MAX_CATEGORIES,
    MAX_CODE_LINES,
    MAX_FIELD_LABEL,
    MAX_FIELD_VALUE,
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
    Code,
    Column,
    Contents,
    Fields,
    Group,
    Heading,
    Image,
    ImageWidth,
    InvoiceTotals,
    Item,
    ListBlock,
    Note,
    Paragraph,
    Problems,
    Quote,
    Signature,
    SubItem,
    Table,
    WriteArguments,
    WrittenBlock,
    style_of,
)
from loom_ia.tools_builtin._document.picture import Picture
from loom_ia.tools_builtin._style_params import FRENCH, Labels, Language
from loom_ia.tools_builtin._templates import Template, fixed_paragraphs, key
from loom_ia.tools_builtin._values import Aggregate, ColumnType, Scalar, Stored, convert

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
    """Un segment de texte et son emphase ; ``\\n`` y est un saut de ligne.

    ``link`` : l'adresse d'un lien. ``note`` : une note de bas de page, son texte ; la
    marque n'a pas de texte à elle (``text`` vide), le format la numérote. ``ref`` : un
    renvoi, le rang du titre visé parmi les titres du document (1 pour le premier) ;
    ``text`` est ce titre, que le format écrit, ou remplace par son numéro.
    """

    text: str
    bold: bool = False
    italic: bool = False
    underline: bool = False
    link: str | None = None
    note: tuple[Run, ...] | None = None
    ref: int | None = None


type Inline = tuple[Run, ...]


@dataclass(frozen=True, slots=True)
class TitleOut:
    runs: Inline


@dataclass(frozen=True, slots=True)
class HeadingOut:
    level: int
    runs: Inline
    # Le style proposé que le modèle de langage a choisi (``style`` du bloc).
    style: str | None = None
    # Le titre d'une section du modèle, que l'outil écrit : la place de la section.
    section: int | None = None


@dataclass(frozen=True, slots=True)
class ParagraphOut:
    runs: Inline
    style: str | None = None


@dataclass(frozen=True, slots=True)
class Entry:
    """Un élément de liste, et sa sous-liste (trois niveaux en tout)."""

    runs: Inline
    children: tuple[Entry, ...] = ()
    # Une liste à cocher : l'élément est coché.
    checked: bool = False


@dataclass(frozen=True, slots=True)
class ListOut:
    ordered: bool
    entries: tuple[Entry, ...]
    # Une liste à cocher (``blocks: [checkboxes]``) : une case devant chaque élément.
    checkboxes: bool = False
    style: str | None = None


@dataclass(frozen=True, slots=True)
class FieldsOut:
    """Des libellés et leurs valeurs ; une valeur vide laisse une ligne à remplir."""

    items: tuple[tuple[str, Inline], ...]


@dataclass(frozen=True, slots=True)
class CodeOut:
    """Du texte préformaté : ses lignes telles quelles, espaces comprises."""

    text: str


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
    # Les cellules des lignes, en segments (l'emphase, les liens, les notes d'une colonne
    # de texte) ; ``rows`` en garde le texte seul.
    cells: tuple[tuple[Inline, ...], ...]
    # La largeur que le contenu demande, en part de la largeur utile (1 au plus) : celle
    # d'un tableau qu'un style veut à sa largeur naturelle (``width: content``).
    natural: float = 1.0
    style: str | None = None
    # La sorte de chaque ligne de ``cells`` (une ligne de groupe n'a qu'une cellule, son
    # libellé) ; vide : toutes des lignes de données.
    kinds: tuple[RowKind, ...] = ()
    # Ce que la relecture retrouve d'un tableau que l'outil calcule (le récapitulatif de
    # TVA : ses colonnes, l'acompte), en JSON.
    data: str | None = None


# Une ligne de tableau : de données, de groupe, de sous-total, de total (en gras).
type RowKind = Literal["body", "group", "subtotal", "total"]


@dataclass(frozen=True, slots=True)
class NoteOut:
    title: str | None
    runs: Inline
    style: str | None = None


@dataclass(frozen=True, slots=True)
class QuoteOut:
    runs: Inline
    source: str | None
    style: str | None = None


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
    | FieldsOut
    | CodeOut
)


@dataclass(frozen=True, slots=True)
class SectionOut:
    """Une section d'un document qui suit un modèle : sa place dans le modèle, son nom, son
    titre (``None`` s'il n'est pas imprimé), ses blocs ; ``fixed`` : un texte fixe."""

    position: int
    name: str
    title: HeadingOut | None
    blocks: tuple[BlockOut, ...]
    fixed: bool = False


@dataclass(frozen=True, slots=True)
class Prepared:
    """Le document validé : son titre (pour les propriétés), ses blocs, les avertissements.

    ``blocks`` : tous les blocs, à plat, dans l'ordre du document (le titre du document,
    puis le titre et les blocs de chaque section) ; ``sections`` : les mêmes, par section,
    pour un document qui suit un modèle."""

    title: str | None
    blocks: tuple[BlockOut, ...]
    warnings: tuple[str, ...]
    # Le modèle suivi, que le document garde dans ses propriétés ; ``None`` sans modèle.
    template: str | None = None
    sections: tuple[SectionOut, ...] | None = None


# Les styles proposés qu'un document peut choisir, par type de bloc : leur nom, et pour un
# titre son niveau (0 pour les autres blocs).
type Allowed = Mapping[str, Mapping[str, int]]
NO_STYLES: Final[Allowed] = {}


# Ce qu'a donné la lecture de chaque image, par chemin tel que donné : l'image, ou ce
# qui l'empêche (droit, fichier absent, format…). Lue par l'outil, avant ``prepare``.
type Pictures = Mapping[str, Picture | str]
NO_PICTURES: Final[Pictures] = {}


@dataclass(slots=True)
class _State:
    """Ce que la validation garde d'un bloc à l'autre, sections comprises."""

    problems: Problems
    zone: ZoneInfo
    pictures: Pictures
    labels: Labels
    allowed: Allowed
    # Le niveau du dernier titre, et le dernier tableau écrit : la source d'un graphique.
    level: int = 0
    last_table: tuple[Table, list[list[Stored]]] | None = None
    # Les titres du document qu'un renvoi peut viser, et ceux qui ne s'impriment pas.
    targets: Targets = field(default_factory=lambda: Targets({}, frozenset(), ()))
    # Les notes de bas de page déjà écrites.
    notes: int = 0


def prepare(
    arguments: WriteArguments,
    zone: ZoneInfo,
    pictures: Pictures = NO_PICTURES,
    template: Template | None = None,
    labels: Labels = FRENCH,
    allowed: Allowed = NO_STYLES,
) -> Prepared:
    """Valide le document entier ; ``ToolError`` qui liste toutes les fautes trouvées.

    Sans disque : les images sont lues avant, par l'outil (``pictures``). ``template`` :
    le modèle que le document suit ; il s'écrit alors par sections (``sections``), et
    l'outil pose les sections dans l'ordre du modèle, avec leurs titres et leur texte
    fixe. ``labels`` : la langue du style, et le libellé de la ligne de total.
    ``allowed`` : les styles proposés que les blocs peuvent choisir.
    """
    state = _State(Problems(), zone, pictures, labels, allowed)
    state.targets = _targets(arguments, template)
    problems = state.problems
    blocks: list[BlockOut] = []
    title = lines(arguments.title).strip() if arguments.title else None
    if title is not None:
        if len(title) > MAX_TITLE:
            problems.error(f"title : {len(title)} caractères, au-delà de {MAX_TITLE}")
        _clean(title, "title", problems)
        _bare(title, "title", problems)
        blocks.append(TitleOut(inline(title)))
    sections: list[SectionOut] | None = None
    if template is None:
        blocks += _blocks(arguments.blocks, "bloc", state)
    else:
        sections = _sections(arguments, template, state)
        for section in sections:
            blocks += [*([section.title] if section.title else []), *section.blocks]
    _check_images(arguments, pictures, problems)
    contents = [b for b in blocks if isinstance(b, ContentsOut)]
    if len(contents) > 1:
        problems.error("un seul sommaire par document")
    if contents and not any(isinstance(b, HeadingOut) for b in blocks):
        problems.warn("sommaire : le document n'a aucun titre, le sommaire sera vide")
    if not blocks:
        problems.error("document vide : ni titre, ni bloc de contenu")
    if state.notes > MAX_NOTES:
        problems.error(f"{state.notes} notes de bas de page, au-delà de {MAX_NOTES}")
    problems.raise_if_any()
    return Prepared(
        title=title,
        blocks=tuple(blocks),
        warnings=tuple(problems.warnings),
        template=template.name if template is not None else None,
        sections=tuple(sections) if sections is not None else None,
    )


def _sections(arguments: WriteArguments, template: Template, state: _State) -> list[SectionOut]:
    """Les sections du document, dans l'ordre du modèle : celles qu'il remplit, et celles au
    texte fixe ; les fautes du plan sont dites avec les sections attendues."""
    problems = state.problems
    where = f"modèle {template.name}"
    given: dict[int, tuple[str, list[WrittenBlock]]] = {}
    named: set[int] = set()
    faults = 0
    for name, written in (arguments.sections or {}).items():
        section = template.section(name)
        if section is not None and section.position not in given:
            named.add(section.position)
        if section is None:
            problems.error(f"{where} : section inconnue « {name} »")
            faults += 1
        elif section.position in given:
            problems.error(
                f"{where} : section donnée deux fois : « {given[section.position][0]} » et "
                f"« {name} »"
            )
            faults += 1
        elif section.fixed:
            problems.error(
                f"{where} : « {section.name} » est une section au texte fixe, écrite par "
                "l'outil : ne pas la remplir"
            )
            faults += 1
        elif not written:
            problems.error(
                f"{where} : « {section.name} » est vide — "
                + ("la retirer (elle est facultative)" if section.optional else "la remplir")
            )
            faults += 1
        else:
            given[section.position] = (name, written)
    missing = [
        s.name
        for s in template.sections
        if not s.optional and not s.fixed and s.position not in named
    ]
    if missing:
        problems.error(
            f"{where} : section(s) absente(s) : {', '.join(f'« {m} »' for m in missing)}"
        )
        faults += 1
    if faults:
        problems.error(f"{where}, sections attendues : {template.outline()}")
    found: list[SectionOut] = []
    for section in template.sections:
        fixed = section.spec.text
        if fixed is not None:
            at = f"section « {section.name} », texte fixe"
            blocks: list[BlockOut] = [
                ParagraphOut(rich(text, at, state, allow=LINKS_ONLY))
                for text in fixed_paragraphs(fixed)
            ]
        elif section.position in given:
            state.level = 1
            label = f"section « {section.name} », bloc"
            blocks = _blocks(given[section.position][1], label, state, in_section=True)
        else:
            continue
        title = (
            HeadingOut(1, inline(section.name), section=section.position)
            if section.spec.show_title
            else None
        )
        found.append(
            SectionOut(section.position, section.name, title, tuple(blocks), section.fixed)
        )
    return found


def _blocks(
    written: Sequence[WrittenBlock], label: str, state: _State, *, in_section: bool = False
) -> list[BlockOut]:
    """Les blocs validés ; ``label`` nomme leur place dans les messages (« bloc »,
    « section « Message », bloc »)."""
    problems = state.problems
    zone = state.zone
    blocks: list[BlockOut] = []
    for index, block in enumerate(written, start=1):
        where = f"{label} {index} ({block.type})"
        style = _style(block, where, state)
        match block:
            case Heading():
                text = _text(block.text, where, problems, required=True)
                _bare(text, where, problems)
                if in_section and block.level == 1:
                    problems.error(
                        f"{where} : le niveau 1 est le titre de la section, que l'outil écrit ; "
                        "dans une section, les titres sont de niveau 2 ou 3"
                    )
                elif block.level > state.level + 1:
                    problems.warn(
                        f"{where} : titre de niveau {block.level} après un niveau {state.level} "
                        "— un niveau sauté brouille le plan du document"
                    )
                state.level = block.level
                blocks.append(HeadingOut(block.level, inline(text), style))
            case Paragraph():
                text = _text(block.text, where, problems, required=True)
                blocks.append(ParagraphOut(rich(text, where, state), style))
            case ListBlock():
                found = _list(block, where, state)
                blocks.append(dataclasses.replace(found, style=style) if style else found)
            case Table():
                table, stored = _table(block, where, state)
                blocks.append(dataclasses.replace(table, style=style) if style else table)
                state.last_table = (block, stored)
            case Chart():
                blocks.append(
                    _chart(block, state.last_table, where, problems, zone, state.labels.language)
                )
            case InvoiceTotals():
                invoice = _invoice(block, where, state)
                if invoice is not None:
                    blocks.append(invoice)
            case Note():
                text = _text(block.text, where, problems, required=True)
                note_title = _text(block.title, f"{where}, title", problems) or None
                _bare(note_title or "", f"{where}, title", problems)
                blocks.append(NoteOut(note_title, rich(text, where, state), style))
            case Quote():
                text = _text(block.text, where, problems, required=True)
                source = _text(block.source, f"{where}, source", problems) or None
                _bare(source or "", f"{where}, source", problems)
                blocks.append(QuoteOut(rich(text, where, state), source, style))
            case Fields():
                blocks.append(_fields(block, where, state))
            case Code():
                blocks.append(_code(block, where, problems))
            case Image():
                found_image = _image(block, state.pictures, where, problems)
                if found_image is not None:
                    blocks.append(found_image)
            case Signature():
                blocks.append(_signature(block, where, problems))
            case Contents():
                blocks.append(ContentsOut())
    return blocks


def _style(block: WrittenBlock, where: str, state: _State) -> str | None:
    """Le style proposé qu'un bloc choisit : permis pour son type (et, pour un titre, son
    niveau) dans ce document ; sinon une faute qui dit les styles permis."""
    style = style_of(block)
    if style is None:
        return None
    kind = "list" if block.type == "list" else block.type
    names = state.allowed.get(kind, {})
    if style not in names:
        permitted = ", ".join(sorted(names)) or "aucun"
        state.problems.error(
            f"{where} : style « {style} » : pas proposé pour un bloc {kind} dans ce document "
            f"(styles : {permitted})"
        )
        return None
    level = names[style]
    if isinstance(block, Heading) and level and block.level != level:
        state.problems.error(
            f"{where} : style « {style} » : un titre de niveau {level}, pas {block.level}"
        )
        return None
    return style


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


def _list(block: ListBlock, where: str, state: _State) -> ListOut:
    problems = state.problems
    count = 0
    checkboxes = isinstance(block, CheckList) and block.checkboxes
    if checkboxes and block.ordered:
        problems.error(f"{where} : une liste à cocher n'est pas numérotée (ordered)")

    def entry(item: str | Item | SubItem, at: str, depth: int) -> Entry:
        nonlocal count
        count += 1
        text = item if isinstance(item, str) else item.text
        said = _text(text, at, problems, required=True)
        checked = False
        if checkboxes:
            found = _CHECKBOX.match(said)
            if found is None:
                problems.error(
                    f"{at} : dans une liste à cocher (checkboxes), chaque élément commence par "
                    "[x] (coché) ou [ ] (vide)"
                )
            else:
                said, checked = said[found.end() :], found.group(1) != " "
        children: list[Entry] = []
        if not isinstance(item, str):
            if checkboxes and depth == 1:
                problems.error(f"{at} : une liste à cocher a deux niveaux")
            children = [
                entry(child, f"{at}.{sub}", depth + 1)
                for sub, child in enumerate(item.items, start=1)
            ]
        return Entry(rich(said, at, state), tuple(children), checked)

    entries = [
        entry(item, f"{where}, élément {number}", 0)
        for number, item in enumerate(block.items, start=1)
    ]
    if count > MAX_ITEMS:
        problems.error(f"{where} : {count} éléments, au-delà de {MAX_ITEMS}")
    return ListOut(block.ordered, tuple(entries), checkboxes)


def _fields(block: Fields, where: str, state: _State) -> FieldsOut:
    """Des libellés et leurs valeurs : un libellé est du texte simple ; une valeur prend le
    balisage d'un paragraphe, et vide, elle laisse une ligne à remplir."""
    problems = state.problems
    items: list[tuple[str, Inline]] = []
    for number, (label, value) in enumerate(block.items, start=1):
        at = f"{where}, paire {number}"
        said = _text(label, f"{at}, libellé", problems, required=True)
        _bare(said, f"{at}, libellé", problems)
        if len(said) > MAX_FIELD_LABEL:
            problems.error(f"{at}, libellé : {len(said)} caractères, au-delà de {MAX_FIELD_LABEL}")
        shown = _text(value, f"{at}, valeur", problems)
        if len(shown) > MAX_FIELD_VALUE:
            problems.error(f"{at}, valeur : {len(shown)} caractères, au-delà de {MAX_FIELD_VALUE}")
        items.append((said, rich(shown, f"{at}, valeur", state) if shown else ()))
    return FieldsOut(tuple(items))


# Les lignes vides (ou d'espaces) au début d'un texte.
_BLANK_START: Final = re.compile(r"\A(?:[ \t]*\n)+")


def _code(block: Code, where: str, problems: Problems) -> CodeOut:
    """Du texte préformaté : ni balisage ni espaces retirées ; les lignes vides du début et
    de la fin seules sont ôtées (et les espaces qui finissent la dernière)."""
    text = _BLANK_START.sub("", lines(block.text).rstrip())
    if not text.strip():
        problems.error(f"{where} : texte vide")
    count = text.count("\n") + 1
    if count > MAX_CODE_LINES:
        problems.error(f"{where} : {count} lignes, au-delà de {MAX_CODE_LINES}")
    if len(text) > MAX_TEXT:
        problems.error(f"{where} : {len(text)} caractères, au-delà de {MAX_TEXT}")
    _clean(text, where, problems)
    return CodeOut(text)


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


def _table(block: Table, where: str, state: _State) -> tuple[TableOut, list[list[Stored]]]:
    problems, labels = state.problems, state.labels
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
        _bare(header, f"{where}, en-tête « {header} »", problems)
    formulas = _formulas(columns, headers, where, problems)
    groups = _groups(block, where, problems)
    count = sum(len(rows) for _, _, rows in groups)
    if count > MAX_TABLE_ROWS:
        problems.error(
            f"{where} : {count} lignes, au-delà de {MAX_TABLE_ROWS} — un tableau "
            "plus long se lit mieux dans un classeur (excel)"
        )
    given = [index for index, column in enumerate(columns) if column.compute is None]
    members = [
        [
            _row(row, columns, given, formulas, f"{at}, ligne {number}", state)
            for number, row in enumerate(rows, start=1)
        ]
        for _, at, rows in groups
    ]
    stored = [row for rows in members for row in rows]
    _check_totals(block, where, problems)
    total = _total_row(block, stored, labels.total, labels.language) if block.totals else None
    if block.totals and not stored:
        problems.warn(f"{where} : aucune ligne, la ligne de total n'est pas écrite")
        total = None

    # Une cellule de texte prend le balisage d'un paragraphe ; une cellule typée est une
    # valeur.
    def cells(row: Sequence[Stored], at: str) -> tuple[Inline, ...]:
        return tuple(
            rich(value, f"{at}, « {column.header} »", state)
            if column.type == "text" and isinstance(value, str)
            else _plain_cell(display(value, column.type, labels.language))
            for value, column in zip(row, columns, strict=True)
        )

    found: list[tuple[RowKind, tuple[Inline, ...]]] = []
    for (label, at, _), rows in zip(groups, members, strict=True):
        if label is not None:
            found.append(("group", (inline(label),)))
        for number, row in enumerate(rows, start=1):
            found.append(("body", cells(row, f"{at}, ligne {number}")))
        if label is not None and block.totals and rows:
            subtotal = _total_row(block, rows, labels.subtotal, labels.language)
            found.append(("subtotal", tuple(_plain_cell(cell) for cell in subtotal)))
    shown = tuple(tuple(plain(cell) for cell in row) for _, row in found)
    measured = [row for (kind, _), row in zip(found, shown, strict=True) if kind != "group"]
    widths, natural = _widths(headers, [*measured, *([total] if total else [])])
    caption = _text(block.caption, f"{where}, caption", problems) or None
    _bare(caption or "", f"{where}, caption", problems)
    grouped = any(label is not None for label, _, _ in groups)
    table = TableOut(
        headers=tuple(headers),
        align=tuple(_align(column.type) for column in columns),
        rows=shown,
        total=total,
        widths=widths,
        caption=inline(caption) if caption else None,
        natural=natural,
        cells=tuple(row for _, row in found),
        kinds=tuple(kind for kind, _ in found) if grouped else (),
    )
    return table, stored


MAX_GROUPS: Final = 20
MAX_GROUP_LABEL: Final = 200


def _groups(
    block: Table, where: str, problems: Problems
) -> list[tuple[str | None, str, list[list[Scalar]]]]:
    """Les lignes du tableau, par groupe : le libellé du groupe (``None`` sans groupe), sa
    place dans les messages, ses lignes. Pas de lignes simples entre des groupes."""
    groups = [row for row in block.rows if isinstance(row, Group)]
    simple = [row for row in block.rows if not isinstance(row, Group)]
    if not groups:
        return [(None, where, simple)]
    if simple:
        problems.error(
            f"{where} : des groupes ({{group, rows}}) et des lignes simples mêlés — toutes les "
            "lignes dans des groupes, ou aucun groupe"
        )
    if len(groups) > MAX_GROUPS:
        problems.error(f"{where} : {len(groups)} groupes, au-delà de {MAX_GROUPS}")
    found: list[tuple[str | None, str, list[list[Scalar]]]] = []
    for number, group in enumerate(groups, start=1):
        at = f"{where}, groupe {number}"
        label = _text(group.group, at, problems, required=True)
        _bare(label, at, problems)
        if len(label) > MAX_GROUP_LABEL:
            problems.error(f"{at} : {len(label)} caractères, au-delà de {MAX_GROUP_LABEL}")
        if not group.rows:
            problems.error(f"{at} « {label} » : aucune ligne")
        found.append((label, f"{at} « {label} »", group.rows))
    return found


def _row(
    row: Sequence[Scalar],
    columns: Sequence[Column],
    given: Sequence[int],
    formulas: Mapping[int, _Formula],
    at: str,
    state: _State,
) -> list[Stored]:
    """Une ligne : ses valeurs, dans l'ordre des colonnes données, puis celles que l'outil
    calcule ; un montant en euros arrondi au centime."""
    problems = state.problems
    if len(row) > len(given):
        computed = [column.header for column in columns if column.compute is not None]
        problems.error(
            f"{at} : {len(row)} valeurs pour {len(given)} colonnes"
            + (f" données ({', '.join(computed)} : calculée par l'outil)" if computed else "")
        )
    cells: list[Stored] = [None] * len(columns)
    for position, index in enumerate(given):
        column = columns[index]
        value = row[position] if position < len(row) else None
        place = f"{at}, « {column.header} »"
        if isinstance(value, str):
            value = lines(value)
            _clean(value, place, problems)
        converted = convert(value, column.type, place, problems, state.zone)
        cells[index] = _cents(converted) if column.type == "amount_eur" else converted
    for index, formula in formulas.items():
        place = f"{at}, « {columns[index].header} »"
        cells[index] = _computed(formula, cells, columns[index].type, place, problems)
    return cells


# --- Calculs ---------------------------------------------------------------------------

MAX_FORMULA: Final = 200
CENT: Final = Decimal("0.01")
# Ce qu'un calcul garde de décimales, par type de colonne : ce que le document écrit.
_QUANTUM: Final[Mapping[ColumnType, Decimal]] = {
    "integer": Decimal(1),
    "decimal": CENT,
    "amount_eur": CENT,
    "amount_keur": Decimal(1),
    "percent": Decimal("0.001"),
}
# Une colonne citée dans une formule : son en-tête entre accolades.
_REFERENCE: Final = re.compile(r"\{([^{}]*)\}")
_PLACEHOLDER: Final = re.compile(r"c\d+")
_OPERATIONS: Final[Mapping[type[ast.operator], Callable[[Decimal, Decimal], Decimal]]] = {
    ast.Add: lambda first, second: first + second,
    ast.Sub: lambda first, second: first - second,
    ast.Mult: lambda first, second: first * second,
    ast.Div: lambda first, second: first / second,
}


@dataclass(frozen=True, slots=True)
class _Formula:
    """Une formule lue : son arbre (chaque colonne citée devient ``c0``, ``c1``…), et la
    colonne de chacune."""

    tree: ast.expr
    sources: tuple[int, ...]


def _formulas(
    columns: Sequence[Column], headers: Sequence[str], where: str, problems: Problems
) -> dict[int, _Formula]:
    """Les colonnes calculées, dans l'ordre : leur formule lue et vérifiée."""
    found: dict[int, _Formula] = {}
    for index, column in enumerate(columns):
        if column.compute is None:
            continue
        at = f"{where}, « {headers[index]} »"
        if column.type not in NUMERIC:
            problems.error(
                f"{at} : une colonne calculée est une colonne de nombres (integer, decimal, "
                f"amount_eur, amount_keur ou percent ; ici {column.type})"
            )
        formula = _formula(column.compute, index, columns, headers, at, problems)
        if formula is not None:
            found[index] = formula
    return found


def _formula(
    text: str,
    index: int,
    columns: Sequence[Column],
    headers: Sequence[str],
    at: str,
    problems: Problems,
) -> _Formula | None:
    said = lines(text).strip()
    if len(said) > MAX_FORMULA:
        problems.error(f"{at} : formule de {len(said)} caractères, au-delà de {MAX_FORMULA}")
        return None
    sources: list[int] = []
    faults: list[str] = []

    def named(found: re.Match[str]) -> str:
        header = found.group(1).strip()
        if header not in headers:
            faults.append(f"colonne inconnue « {header} » (colonnes : {', '.join(headers)})")
            return "0"
        position = headers.index(header)
        if columns[position].type not in NUMERIC:
            faults.append(f"« {header} » n'est pas une colonne de nombres")
        elif position == index:
            faults.append("la formule cite sa propre colonne")
        elif columns[position].compute is not None and position > index:
            faults.append(
                f"« {header} » est calculée plus loin : une formule prend les colonnes "
                "données, ou celles calculées avant elle"
            )
        sources.append(position)
        return f"c{len(sources) - 1}"

    written = _REFERENCE.sub(named, said)
    for fault in dict.fromkeys(faults):
        problems.error(f"{at} : {fault}")
    try:
        tree = ast.parse(written, mode="eval").body
    except SyntaxError, ValueError:
        tree = None
    if tree is None or not _allowed(tree, len(sources)):
        problems.error(
            f"{at} : formule {said!r} : seuls + - * / ( ), des nombres et des {{colonnes}} "
            "(une colonne s'écrit entre accolades : {Qté})"
        )
        return None
    return None if faults else _Formula(tree, tuple(sources))


def _allowed(node: ast.expr, count: int) -> bool:
    """Seulement les quatre opérations, le signe, des nombres et les colonnes citées."""
    match node:
        case ast.BinOp(left=left, op=op, right=right):
            return type(op) in _OPERATIONS and _allowed(left, count) and _allowed(right, count)
        case ast.UnaryOp(op=ast.USub() | ast.UAdd(), operand=operand):
            return _allowed(operand, count)
        case ast.Constant(value=value):
            return (
                isinstance(value, int | float)
                and not isinstance(value, bool)
                and math.isfinite(value)
            )
        case ast.Name(id=name):
            return _PLACEHOLDER.fullmatch(name) is not None and int(name[1:]) < count
        case _:
            return False


def _computed(
    formula: _Formula, cells: Sequence[Stored], kind: ColumnType, at: str, problems: Problems
) -> Stored:
    """La valeur d'une colonne calculée, arrondie à ce que le document écrit ; vide si une
    colonne citée l'est."""
    values: list[Decimal] = []
    for position in formula.sources:
        value = cells[position]
        if not isinstance(value, int | float) or isinstance(value, bool):
            return None
        values.append(Decimal(str(value)))
    try:
        result = _evaluated(formula.tree, values).quantize(
            _QUANTUM.get(kind, CENT), rounding=ROUND_HALF_UP
        )
    except ZeroDivisionError:
        problems.error(f"{at} : division par zéro")
        return None
    except InvalidOperation:
        problems.error(f"{at} : résultat hors des nombres qu'un document écrit")
        return None
    return int(result) if kind == "integer" else float(result)


def _evaluated(node: ast.expr, values: Sequence[Decimal]) -> Decimal:
    match node:
        case ast.BinOp(left=left, op=op, right=right):
            first, second = _evaluated(left, values), _evaluated(right, values)
            if isinstance(op, ast.Div) and second == 0:
                raise ZeroDivisionError
            return _OPERATIONS[type(op)](first, second)
        case ast.UnaryOp(op=ast.USub(), operand=operand):
            return -_evaluated(operand, values)
        case ast.UnaryOp(operand=operand):
            return _evaluated(operand, values)
        case ast.Constant(value=value):
            return Decimal(str(value))
        case ast.Name(id=name):
            return values[int(name[1:])]
        case _:  # écarté par ``_allowed``
            raise InvalidOperation


def _cents(value: Stored) -> Stored:
    """Un montant au centime, au demi supérieur ; le même s'il y est déjà."""
    if not isinstance(value, int | float) or isinstance(value, bool):
        return value
    try:
        rounded = Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return value
    return value if float(rounded) == value else float(rounded)


def _decimal(value: Stored) -> Decimal:
    return Decimal(str(value))


# --- Le récapitulatif de TVA -------------------------------------------------------------


def _invoice(block: InvoiceTotals, where: str, state: _State) -> TableOut | None:
    """Total HT, la TVA de chaque taux (sur le total HT de ce taux, au centime), Total TTC,
    et, avec ``paid``, l'acompte versé et le net à payer : tirés du tableau qui précède."""
    problems, labels = state.problems, state.labels
    language = labels.language
    if state.last_table is None:
        problems.error(f"{where} : from_table, mais aucun tableau ne précède le récapitulatif")
        return None
    table, stored = state.last_table
    headers = [column.header.strip() for column in table.columns]
    amount, rate = block.from_table.amount.strip(), block.from_table.rate.strip()
    unknown = [name for name in (amount, rate) if name not in headers]
    if unknown:
        problems.error(
            f"{where} : from_table, colonne(s) inconnue(s) du tableau précédent : "
            f"{', '.join(unknown)} (colonnes : {', '.join(headers)})"
        )
        return None
    amount_at, rate_at = headers.index(amount), headers.index(rate)
    kinds = (table.columns[amount_at].type, table.columns[rate_at].type)
    if kinds[0] != "amount_eur":
        problems.error(f"{where} : « {amount} » : des montants HT, amount_eur (ici {kinds[0]})")
    if kinds[1] != "percent":
        problems.error(f"{where} : « {rate} » : des taux de TVA, percent (ici {kinds[1]})")
    if kinds != ("amount_eur", "percent"):
        return None
    bases: dict[Decimal, Decimal] = {}
    for place, row in zip(_places(table), stored, strict=False):
        value, taxed = row[amount_at], row[rate_at]
        if value is None:
            continue
        if taxed is None:
            problems.error(f"{where} : {place} du tableau, un montant sans taux de TVA")
            continue
        found = _decimal(taxed)
        bases[found] = bases.get(found, Decimal(0)) + _decimal(value)
    if not bases:
        problems.error(f"{where} : aucun montant dans « {amount} »")
        return None
    excluded = sum(bases.values(), Decimal(0))
    found_lines: list[tuple[str, Decimal, RowKind]] = [(labels.total_excl_tax, excluded, "body")]
    taxes = Decimal(0)
    for taxed in sorted(bases):
        tax = (bases[taxed] * taxed).quantize(CENT, rounding=ROUND_HALF_UP)
        taxes += tax
        label = labels.tax.format(
            rate=_rate(taxed, language), base=display(float(bases[taxed]), "amount_eur", language)
        )
        found_lines.append((label, tax, "body"))
    included = excluded + taxes
    found_lines.append((labels.total_incl_tax, included, "total"))
    data: dict[str, str | int | float] = {"amount": amount, "rate": rate}
    if block.paid is not None:
        paid = convert(block.paid, "amount_eur", f"{where}, paid", problems, state.zone)
        if isinstance(paid, int | float) and not isinstance(paid, bool):
            if paid < 0:
                problems.error(f"{where}, paid : un acompte versé se donne en positif")
            data["paid"] = paid
            deposit = _decimal(_cents(paid))
            found_lines.append((labels.paid, deposit, "body"))
            found_lines.append((labels.due, included - deposit, "total"))
    rows = tuple(
        (label, display(float(value), "amount_eur", language)) for label, value, _ in found_lines
    )
    widths, natural = _widths(("", ""), rows)
    return TableOut(
        headers=(),
        align=("left", "right"),
        rows=rows,
        total=None,
        widths=widths,
        caption=None,
        cells=tuple(tuple(_plain_cell(cell) for cell in row) for row in rows),
        natural=natural,
        style="invoice_totals",
        kinds=tuple(kind for *_, kind in found_lines),
        data=json.dumps(data, ensure_ascii=False, separators=(",", ":")),
    )


def _places(table: Table) -> list[str]:
    """La place de chaque ligne d'un tableau, dans l'ordre : « ligne 3 », ou « groupe 2
    « Cuisine », ligne 1 »."""
    groups = [row for row in table.rows if isinstance(row, Group)]
    if not groups:
        return [f"ligne {number}" for number in range(1, len(table.rows) + 1)]
    return [
        f"groupe {index} « {lines(group.group).strip()} », ligne {number}"
        for index, group in enumerate(groups, start=1)
        for number in range(1, len(group.rows) + 1)
    ]


def _rate(rate: Decimal, language: Language) -> str:
    """Un taux de TVA tel qu'un libellé l'écrit : « 10 % », « 5,5 % » (« 10% » en anglais)."""
    said = f"{(rate * 100).normalize():f}"
    return f"{said}%" if language == "en" else f"{said.replace('.', ',')}{NBSP}%"


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
    _bare(caption or "", f"{where}, caption", problems)
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
    _bare(caption or "", f"{where}, caption", problems)
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
    count = sum(1 for block in arguments.all_blocks() if isinstance(block, Image))
    if count > MAX_IMAGES:
        problems.error(f"{count} images, au-delà de {MAX_IMAGES}")
    distinct = {p.digest: len(p.data) for p in pictures.values() if isinstance(p, Picture)}
    if sum(distinct.values()) > MAX_IMAGES_BYTES:
        problems.error(
            f"images de {sum(distinct.values())} octets en tout, au-delà de {MAX_IMAGES_BYTES} "
            "— des images plus légères (JPEG pour une photo)"
        )


def _check_totals(block: Table, where: str, problems: Problems) -> None:
    """Un total vise une colonne du tableau, et un agrégat qui va à son type."""
    headers = [column.header for column in block.columns]
    for header, aggregate in (block.totals or {}).items():
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


def _total_row(
    block: Table, stored: Sequence[Sequence[Stored]], label: str, language: Language
) -> tuple[str, ...]:
    """La ligne de total (ou de sous-total) de ces lignes : un agrégat par colonne qui en a
    un, ``label`` à la place de la première sans."""
    totals = block.totals or {}
    labelled = _total_label(block)
    cells: list[str] = []
    for index, column in enumerate(block.columns):
        aggregate = totals.get(column.header)
        values = [row[index] for row in stored if row[index] is not None]
        if aggregate is not None:
            cells.append(_aggregate(aggregate, values, column.type, language))
        else:
            cells.append(label if index == labelled else "")
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
    """Le texte seul : sans emphase ni marque de note ; un renvoi par le titre visé."""
    return "".join(run.text for run in runs)


def _plain_cell(text: str) -> Inline:
    return (Run(text),) if text else ()


def runs_of(block: BlockOut) -> Iterator[Inline]:
    """Les textes en segments d'un bloc : paragraphe, éléments de liste (sous-listes
    comprises), note, citation, valeurs de champs, cellules d'un tableau."""
    match block:
        case ParagraphOut(runs=runs) | NoteOut(runs=runs) | QuoteOut(runs=runs):
            yield runs
        case ListOut(entries=entries):
            pending = list(entries)
            while pending:
                entry = pending.pop(0)
                yield entry.runs
                pending[:0] = entry.children
        case FieldsOut(items=items):
            yield from (value for _, value in items)
        case TableOut(cells=cells):
            yield from (cell for row in cells for cell in row)
        case _:
            pass


def referenced(blocks: Iterable[BlockOut]) -> frozenset[int]:
    """Les rangs des titres qu'un renvoi vise, notes comprises."""
    found: set[int] = set()

    def visit(runs: Inline) -> None:
        for run in runs:
            if run.ref is not None:
                found.add(run.ref)
            if run.note is not None:
                visit(run.note)

    for block in blocks:
        for runs in runs_of(block):
            visit(runs)
    return frozenset(found)


# --- Liens, notes, renvois -------------------------------------------------------------------

# L'ancre d'un titre que vise un renvoi, dans les deux formats : « _Ref » (un signet caché
# de Word), puis le rang du titre.
REF_MARK: Final = "_Ref_loom_"
# Les guillemets d'un renvoi à un titre sans numéro, par langue.
QUOTES: Final[Mapping[str, tuple[str, str]]] = {
    "fr": ("\u00ab\u00a0", "\u00a0\u00bb"),
    "en": ("\u201c", "\u201d"),
}
MAX_NOTES: Final = 100
MAX_NOTE: Final = 500
# Les schémas d'un lien : une adresse web, ou un courriel.
LINK_SCHEMES: Final = ("https", "http", "mailto")
type Markup = Literal["link", "note", "ref"]
EVERYTHING: Final[frozenset[Markup]] = frozenset({"link", "note", "ref"})
LINKS_ONLY: Final[frozenset[Markup]] = frozenset({"link"})
_LINK: Final = re.compile(r"\[([^\[\]\n]+)\]\(([^()\s]+)\)")
_SCHEME: Final = re.compile(r"^([A-Za-z][A-Za-z0-9+.\-]*):")
# Ce qui, dans un titre ou une légende, serait un lien, une note ou un renvoi.
_MARKUP: Final = re.compile(r"(?<!\\)\^\[|(?<!\\)§\[|\[[^\[\]\n]+\]\([^()\s]+\)")
# Les marques de place : un caractère d'usage privé par lien, note ou renvoi, le temps de
# lire l'emphase autour d'eux.
_PLACE: Final = 0xF0000


@dataclass(frozen=True, slots=True)
class Targets:
    """Les titres du document qu'un renvoi peut viser : leur rang, par clé (``key``), et
    leur texte ; les sections dont le nom ne s'imprime pas ; tous les titres, pour le dire."""

    by_key: Mapping[str, tuple[int, str] | None]
    hidden: frozenset[str]
    titles: tuple[str, ...]


def _targets(arguments: WriteArguments, template: Template | None) -> Targets:
    """Les titres du document, dans l'ordre où ils seront écrits : ceux des blocs d'un
    document libre ; pour un modèle, le nom de chaque section imprimée, puis ses titres.
    Deux titres de même clé : aucun renvoi ne sait lequel viser (``None``)."""
    titles: list[str] = []
    hidden: set[str] = set()

    def headings(written: Sequence[WrittenBlock]) -> None:
        titles.extend(
            plain(inline(lines(block.text).strip()))
            for block in written
            if isinstance(block, Heading)
        )

    if template is None:
        headings(arguments.blocks)
    else:
        given: dict[int, list[WrittenBlock]] = {}
        for name, written in (arguments.sections or {}).items():
            section = template.section(name)
            if section is not None and section.position not in given:
                given[section.position] = written
        for section in template.sections:
            if not section.spec.show_title:
                hidden.add(section.key)
            if section.fixed or section.position in given:
                if section.spec.show_title:
                    titles.append(section.name)
                headings(given.get(section.position, []))
    by_key: dict[str, tuple[int, str] | None] = {}
    for rank, title in enumerate(titles, start=1):
        found = key(title)
        by_key[found] = None if found in by_key else (rank, title)
    return Targets(by_key, frozenset(hidden), tuple(titles))


def _bare(text: str, where: str, problems: Problems) -> None:
    """Un titre, une légende, un libellé : ni lien, ni note, ni renvoi."""
    if _MARKUP.search(text):
        problems.error(f"{where} : ni lien, ni note (^[…]), ni renvoi (§[…]) ici")


def rich(text: str, where: str, state: _State, *, allow: frozenset[Markup] = EVERYTHING) -> Inline:
    """Le texte d'un paragraphe, d'un élément de liste, d'une note, d'une citation, d'une
    cellule : l'emphase (``inline``), et ce que ``allow`` permet — un lien
    ``[texte](https://…)``, une note ``^[texte]``, un renvoi ``§[Titre]``. Une faute
    (note non refermée, schéma de lien refusé, titre inconnu) est dite avec ``where``.

    ``\\^[`` et ``\\§[`` s'écrivent tels quels. Un ``[texte](…)`` sans schéma (« (voir) »)
    reste du texte ; un schéma autre que https, http et mailto est refusé.
    """
    problems = state.problems
    places: list[Run | tuple[str, Inline]] = []
    said: list[str] = []
    position = 0
    while position < len(text):
        char = text[position]
        rest = text[position:]
        if char == "\\" and rest[1:3] in ("^[", "§["):
            said.append(rest[1:3])
            position += 3
            continue
        if ord(char) >= _PLACE:
            problems.error(f"{where} : caractère réservé U+{ord(char):04X}")
            position += 1
            continue
        if rest.startswith("^[") and "note" in allow:
            end = _closing(text, position + 1)
            if end is None:
                problems.error(
                    f"{where} : « ^[ » sans « ] » : une note se ferme par ] (\\^[ pour écrire "
                    "^[ tel quel)"
                )
                said.append("^[")
                position += 2
                continue
            content = text[position + 2 : end].strip()
            if not content:
                problems.error(f"{where} : note vide (^[])")
            elif len(content) > MAX_NOTE:
                problems.error(
                    f"{where} : note de {len(content)} caractères, au-delà de {MAX_NOTE}"
                )
            if "^[" in content.replace("\\^[", ""):
                problems.error(f"{where} : pas de note dans une note")
            inner = rich(content, f"{where}, note", state, allow=allow - {"note"})
            state.notes += 1
            places.append(Run("", note=inner))
            said.append(chr(_PLACE + len(places) - 1))
            position = end + 1
            continue
        if rest.startswith("§[") and "ref" in allow:
            end = text.find("]", position + 2)
            if end < 0:
                problems.error(
                    f"{where} : « §[ » sans « ] » : un renvoi se ferme par ] (\\§[ pour écrire "
                    "§[ tel quel)"
                )
                said.append("§[")
                position += 2
                continue
            target = _target(text[position + 2 : end], where, state)
            places.append(target)
            said.append(chr(_PLACE + len(places) - 1))
            position = end + 1
            continue
        found = _LINK.match(text, position) if char == "[" and "link" in allow else None
        if found is not None:
            address = found.group(2)
            scheme = _SCHEME.match(address)
            if scheme is not None:
                if scheme.group(1).lower() not in LINK_SCHEMES:
                    problems.error(
                        f"{where} : lien {address!r} : seuls https, http et mailto sont permis"
                    )
                places.append((address, inline(found.group(1))))
                said.append(chr(_PLACE + len(places) - 1))
                position = found.end()
                continue
        said.append(char)
        position += 1
    runs = inline("".join(said))
    if not places:
        return runs
    return _merged(_expanded(runs, places))


def _closing(text: str, opening: int) -> int | None:
    """La place du ``]`` qui ferme le ``[`` en ``opening`` (les crochets d'un lien dans une
    note s'y emboîtent) ; ``None`` sans lui."""
    depth = 0
    for index in range(opening, len(text)):
        if text[index] == "[":
            depth += 1
        elif text[index] == "]":
            depth -= 1
            if depth == 0:
                return index
    return None


def _target(written: str, where: str, state: _State) -> Run:
    """Le renvoi à un titre : comparé comme les sections d'un modèle (sans numérotation,
    casse, accents ni emphase)."""
    targets = state.targets
    wanted = key(plain(inline(written)))
    found = targets.by_key.get(wanted)
    if found is not None:
        rank, title = found
        return Run(title, ref=rank)
    if wanted in targets.by_key:
        state.problems.error(f"{where} : renvoi §[{written}] : deux titres de ce nom")
    elif wanted in targets.hidden:
        state.problems.error(
            f"{where} : renvoi §[{written}] : cette section n'imprime pas son titre "
            "(show_title: false), un renvoi ne peut pas la viser"
        )
    else:
        known = " · ".join(targets.titles) or "aucun"
        state.problems.error(
            f"{where} : renvoi §[{written}] : aucun titre de ce nom (titres : {known})"
        )
    return Run(written)


def _expanded(runs: Inline, places: Sequence[Run | tuple[str, Inline]]) -> list[Run]:
    """Les segments, chaque marque de place remplacée par son lien, sa note ou son renvoi,
    dans l'emphase qui l'entoure."""
    found: list[Run] = []
    for run in runs:
        piece = ""
        for char in run.text:
            index = ord(char) - _PLACE
            if index < 0:
                piece += char
                continue
            if piece:
                found.append(dataclasses.replace(run, text=piece))
                piece = ""
            place = places[index]
            if isinstance(place, Run):
                found.append(
                    dataclasses.replace(
                        place, bold=run.bold, italic=run.italic, underline=run.underline
                    )
                    if place.ref is not None
                    else place
                )
                continue
            address, label = place
            found.extend(
                Run(
                    part.text,
                    bold=part.bold or run.bold,
                    italic=part.italic or run.italic,
                    underline=part.underline or run.underline,
                    link=address,
                )
                for part in label
            )
        if piece:
            found.append(dataclasses.replace(run, text=piece))
    return found


def _merged(runs: Sequence[Run]) -> Inline:
    """Les segments voisins de même emphase et même lien, réunis."""
    merged: list[Run] = []
    for run in runs:
        last = merged[-1] if merged else None
        if (
            last is not None
            and run.note is None
            and run.ref is None
            and last.note is None
            and last.ref is None
            and (last.bold, last.italic, last.underline, last.link)
            == (run.bold, run.italic, run.underline, run.link)
        ):
            merged[-1] = dataclasses.replace(last, text=last.text + run.text)
        else:
            merged.append(run)
    return tuple(run for run in merged if run.text or run.note is not None)
