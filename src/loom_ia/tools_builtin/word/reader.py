# SPDX-License-Identifier: Apache-2.0
"""Lire un .docx : ses blocs dans l'ordre, rendus dans le vocabulaire de l'écriture.

Un document Word est un zip de XML qui vient de n'importe où. Comme pour un
classeur (``excel/reader``) :

- le zip est contrôlé avant tout (nombre de membres, taille décompressée,
  taille de chaque partie lue) : une bombe zip est refusée sans être ouverte ;
- le XML passe par le parseur de la bibliothèque standard (expat), qui refuse
  l'amplification d'entités depuis 2.4.0 et ne charge aucune entité externe ;
- le corps est lu **au fil de l'eau** (``XMLPullParser``) et chaque bloc est
  libéré une fois rendu : la mémoire ne suit pas la taille du document ;
- la lecture s'arrête d'elle-même à une échéance.

Ce qui revient au modèle reprend le vocabulaire de l'écriture : ``titre``
(``level`` 1 à 3 et plus, 0 pour le titre du document), ``paragraphe``,
``liste`` (``items`` : textes, ou ``{text, items}``), ``tableau`` (``rows``).
L'emphase revient en ``**gras**`` et ``*italique*`` : ce qui est lu peut être
réécrit tel quel. Les images, en-têtes, pieds de page et commentaires ne sont
pas lus ; le résultat le dit.
"""

import io
import json
import posixpath
import time
import zipfile
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Final, cast
from xml.etree.ElementTree import Element, ParseError, XMLPullParser

from pydantic import JsonValue

from loom_ia.core.ports import ToolError
from loom_ia.tools_builtin._document.arguments import MAX_SOURCE_BYTES, READ_CHARS

READABLE: Final = (".docx", ".docm", ".dotx")
# Formats qui ne sont pas un zip OOXML : le dire plutôt que « pas un zip ».
OTHER_FORMATS: Final = {
    ".doc": "Word 97-2003 (.doc)",
    ".odt": "OpenDocument (.odt)",
    ".rtf": "RTF (.rtf)",
}
MAX_MEMBERS: Final = 10_000
MAX_UNCOMPRESSED: Final = 10 * MAX_SOURCE_BYTES
# Le corps se lit au fil de l'eau ; styles, numérotation et relations, en entier.
MAX_BODY: Final = 100_000_000
MAX_PART: Final = 10_000_000
MAX_CELL_CHARS: Final = 1_000
MAX_COLUMNS: Final = 50
MAX_OUTLINE: Final = 100
# Le plan s'ajoute aux blocs (READ_CHARS) : à eux deux, sous le seuil de déport (50 000).
MAX_OUTLINE_CHARS: Final = 8_000
READ_DEADLINE: Final = 20.0
SAFE_EXPAT: Final = (2, 4, 0)
# WordprocessingML transitionnel (ce qu'écrivent Word et LibreOffice), et strict.
_NAMESPACES: Final = (
    "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "http://purl.oclc.org/ooxml/wordprocessingml/main",
)
_REL_NS: Final = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_DC_TITLE: Final = "{http://purl.org/dc/elements/1.1/}title"
_FALSE: Final = frozenset({"0", "false", "off"})
# Sous-arbres d'un paragraphe qui ne sont pas son texte.
_SKIPPED: Final = frozenset(
    {"del", "delText", "moveFrom", "instrText", "drawing", "pict", "object", "footnoteReference",
     "endnoteReference", "commentReference", "pPr", "rPr", "fldChar"}
)  # fmt: skip


def check_suffix(path: str) -> None:
    lowered = path.lower()
    for suffix, name in OTHER_FORMATS.items():
        if lowered.endswith(suffix):
            raise ToolError(
                f"{path} : format {name} non lu — l'enregistrer en .docx depuis Word "
                "ou LibreOffice."
            )
    if not lowered.endswith(READABLE):
        raise ToolError(f"{path} : seuls les documents .docx, .docm et .dotx sont lus.")


def xml_safety() -> str | None:
    """Pourquoi un .docx ne peut pas être lu sans risque ici ; ``None`` s'il le peut."""
    import pyexpat

    if pyexpat.version_info < SAFE_EXPAT:
        found = ".".join(map(str, pyexpat.version_info))
        return f"expat {found} : 2.4.0 ou plus attendu (amplification d'entités XML)"
    return None


def _local(tag: str) -> str | None:
    """Le nom WordprocessingML d'une balise, ``None`` pour un autre espace de noms."""
    namespace, _, name = tag[1:].partition("}")
    return name if namespace in _NAMESPACES else None


def _attribute(element: Element, name: str) -> str | None:
    for namespace in _NAMESPACES:
        found = element.get(f"{{{namespace}}}{name}")
        if found is not None:
            return found
    return None


def _child(element: Element, name: str) -> Element | None:
    return next((child for child in element if _local(child.tag) == name), None)


def _on(element: Element | None) -> bool:
    """Une propriété booléenne (``<w:b/>``, ``<w:b w:val="0"/>``) : posée et vraie."""
    if element is None:
        return False
    value = _attribute(element, "val")
    return value is None or value.lower() not in _FALSE


@contextmanager
def _guarded(where: str) -> Generator[None]:
    """Toute faute du fichier devient une ``ToolError``, jamais une erreur interne."""
    try:
        yield
    except ToolError:
        raise
    except ParseError as exc:
        raise ToolError(f"{where} : XML refusé ({exc}).") from None
    except RecursionError:
        raise ToolError(f"{where} : XML trop imbriqué.") from None
    except Exception as exc:
        raise ToolError(f"{where} : document illisible ({type(exc).__name__}: {exc}).") from None


# --- Archive -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Style:
    name: str
    based_on: str | None
    outline: int | None
    # La numérotation que le style porte (un style de liste), ou ``None``.
    num_id: str | None


@dataclass(slots=True)
class _Package:
    archive: zipfile.ZipFile
    body: str
    styles: dict[str, _Style]
    # numId → numéroté (vrai) ou à puces, par niveau.
    numbering: dict[str, dict[int, bool]]
    title: str | None
    comments: bool


def _open(data: bytes, where: str) -> _Package:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        # Un document protégé par mot de passe est un conteneur OLE, pas un zip.
        raise ToolError(
            f"{where} : pas un document .docx lisible (fichier vide, renommé, ou protégé "
            "par mot de passe)."
        ) from None
    members = archive.infolist()
    if len(members) > MAX_MEMBERS:
        raise ToolError(f"{where} : {len(members)} fichiers dans le document, refusé.")
    total = sum(member.file_size for member in members)
    if total > MAX_UNCOMPRESSED:
        raise ToolError(
            f"{where} : {total} octets une fois décompressé, au-delà de {MAX_UNCOMPRESSED}."
        )
    names = {member.filename for member in members}
    body = _main_part(archive, names) or "word/document.xml"
    if body not in names:
        raise ToolError(f"{where} : pas un document Word ({body} absent).")
    if archive.getinfo(body).file_size > MAX_BODY:
        raise ToolError(f"{where} : corps de {archive.getinfo(body).file_size} octets, refusé.")
    related = _relations(archive, names, body)
    styles_part = related.get("styles")
    numbering_part = related.get("numbering")
    return _Package(
        archive=archive,
        body=body,
        styles=_styles(_whole(archive, styles_part, where)) if styles_part else {},
        numbering=_numbering(_whole(archive, numbering_part, where)) if numbering_part else {},
        title=_title(archive, names, where),
        comments="comments" in related,
    )


def _whole(archive: zipfile.ZipFile, name: str, where: str) -> Element | None:
    """Une partie lue en entier, si elle est là et de taille raisonnable."""
    try:
        info = archive.getinfo(name)
    except KeyError:
        return None
    if info.file_size > MAX_PART:
        raise ToolError(f"{where} : {name} fait {info.file_size} octets, au-delà de {MAX_PART}.")
    return _parse(archive.read(info))


def _main_part(archive: zipfile.ZipFile, names: set[str]) -> str | None:
    if "_rels/.rels" not in names or archive.getinfo("_rels/.rels").file_size > MAX_PART:
        return None
    root = _parse(archive.read("_rels/.rels"))
    for relation in root.iter(f"{_REL_NS}Relationship"):
        if relation.get("Type", "").endswith("/officeDocument"):
            return relation.get("Target", "").lstrip("/")
    return None


def _relations(archive: zipfile.ZipFile, names: set[str], body: str) -> dict[str, str]:
    """Les parties liées au corps, par type (« styles », « numbering »…)."""
    folder, _, file = body.rpartition("/")
    rels = f"{folder}/_rels/{file}.rels" if folder else f"_rels/{file}.rels"
    if rels not in names or archive.getinfo(rels).file_size > MAX_PART:
        return {}
    found: dict[str, str] = {}
    for relation in _parse(archive.read(rels)).iter(f"{_REL_NS}Relationship"):
        kind = relation.get("Type", "").rpartition("/")[2]
        target = relation.get("Target", "")
        if relation.get("TargetMode") == "External" or not target:
            continue
        path = target.lstrip("/") if target.startswith("/") else posixpath.join(folder, target)
        found.setdefault(kind, posixpath.normpath(path))
    return found


def _parse(data: bytes) -> Element:
    parser: XMLPullParser[Element] = XMLPullParser(events=("end",))
    parser.feed(data)
    parser.close()
    root: Element | None = None
    for _, element in cast(Iterator[tuple[str, Element]], parser.read_events()):
        root = element
    if root is None:
        raise ParseError("document vide")
    return root


def _styles(root: Element | None) -> dict[str, _Style]:
    found: dict[str, _Style] = {}
    if root is None:
        return found
    for style in root:
        if _local(style.tag) != "style" or _attribute(style, "type") != "paragraph":
            continue
        style_id = _attribute(style, "styleId")
        if style_id is None:
            continue
        name_element = _child(style, "name")
        name = (_attribute(name_element, "val") if name_element is not None else None) or ""
        based = _child(style, "basedOn")
        properties = _child(style, "pPr")
        outline = None
        num_id: str | None = None
        if properties is not None:
            level = _child(properties, "outlineLvl")
            value = _attribute(level, "val") if level is not None else None
            outline = int(value) if value is not None and value.isdigit() else None
            numbering = _child(properties, "numPr")
            if numbering is not None:
                number = _child(numbering, "numId")
                value = _attribute(number, "val") if number is not None else None
                num_id = value if value not in (None, "0") else None
        found[style_id] = _Style(
            name=name.lower(),
            based_on=_attribute(based, "val") if based is not None else None,
            outline=outline,
            num_id=num_id,
        )
    return found


def _numbering(root: Element | None) -> dict[str, dict[int, bool]]:
    if root is None:
        return {}
    abstract: dict[str, dict[int, bool]] = {}
    for element in root:
        if _local(element.tag) != "abstractNum":
            continue
        levels: dict[int, bool] = {}
        for level in element:
            if _local(level.tag) != "lvl":
                continue
            number = _attribute(level, "ilvl") or "0"
            kind = _child(level, "numFmt")
            fmt = (_attribute(kind, "val") if kind is not None else None) or "decimal"
            if number.isdigit():
                levels[int(number)] = fmt not in {"bullet", "none"}
        abstract[_attribute(element, "abstractNumId") or ""] = levels
    found: dict[str, dict[int, bool]] = {}
    for element in root:
        if _local(element.tag) != "num":
            continue
        reference = _child(element, "abstractNumId")
        key = _attribute(reference, "val") if reference is not None else None
        found[_attribute(element, "numId") or ""] = abstract.get(key or "", {})
    return found


def _title(archive: zipfile.ZipFile, names: set[str], where: str) -> str | None:
    if "docProps/core.xml" not in names:
        return None
    root = _whole(archive, "docProps/core.xml", where)
    if root is None:
        return None
    element = root.find(_DC_TITLE)
    text = (element.text or "").strip() if element is not None else ""
    return text[:MAX_CELL_CHARS] or None


# --- Corps ---------------------------------------------------------------------------


@dataclass(slots=True)
class _Reading:
    """Ce que la lecture rend, et ce qu'elle a vu en passant."""

    start: int
    blocks: list[JsonValue] = field(default_factory=list[JsonValue])
    used: int = 0
    full: bool = False
    next_start: int | None = None
    count: int = 0
    outline: list[JsonValue] = field(default_factory=list[JsonValue])
    images: int = 0
    revisions: int = 0
    cut: int = 0
    # Une liste en cours : ses éléments s'ajoutent jusqu'au bloc suivant.
    pending: dict[str, JsonValue] | None = None
    # Sa numérotation : deux listes voisines (puces, puis numéros) restent deux listes.
    pending_id: str | None = None


def read(data: bytes, where: str, start: int) -> dict[str, JsonValue]:
    """Les blocs du document à partir de ``start``, dans un budget de taille."""
    deadline = time.monotonic() + READ_DEADLINE
    with _guarded(where):
        package = _open(data, where)
        with package.archive:
            reading = _Reading(start=start)
            _walk(package, reading, deadline, where)
    result: dict[str, JsonValue] = {
        "title": package.title,
        "start": start,
        "blocks": reading.blocks,
        "block_count": reading.count,
        "truncated": reading.next_start is not None,
    }
    if reading.next_start is not None:
        result["next_start"] = reading.next_start
        result["outline"] = _bounded(reading.outline)
    notes = _notes(package, reading, start)
    if notes:
        result["notes"] = notes
    return result


def _walk(package: _Package, reading: _Reading, deadline: float, where: str) -> None:
    parser: XMLPullParser[Element] = XMLPullParser(events=("start", "end"))
    depth = 0
    body_depth: int | None = None
    body: Element | None = None
    seen = 0
    with package.archive.open(package.body) as stream:
        while chunk := stream.read(1 << 16):
            parser.feed(chunk)
            for event, element in cast(Iterator[tuple[str, Element]], parser.read_events()):
                if event == "start":
                    depth += 1
                    name = _local(element.tag)
                    if name == "body" and body_depth is None:
                        body_depth, body = depth, element
                    elif name in {"drawing", "pict"}:
                        reading.images += 1
                    elif name in {"ins", "del", "moveFrom", "moveTo"}:
                        reading.revisions += 1
                    continue
                depth -= 1
                if body is not None and body_depth is not None and depth == body_depth:
                    seen += 1
                    if seen % 200 == 0 and time.monotonic() > deadline:
                        raise ToolError(
                            f"{where} : lecture arrêtée après {READ_DEADLINE:g} s ; "
                            "document trop long ou trop complexe."
                        )
                    _block(package, reading, element)
                    # Libéré une fois rendu : la mémoire ne suit pas la taille du corps.
                    body.remove(element)
        parser.close()
    _close_list(reading)


def _block(package: _Package, reading: _Reading, element: Element) -> None:
    match _local(element.tag):
        case "p":
            _paragraph(package, reading, element)
        case "tbl":
            _close_list(reading)
            _emit(reading, {"type": "tableau", "rows": _table(package, reading, element)})
        case "sdt":
            content = _child(element, "sdtContent")
            for child in list(content) if content is not None else []:
                _block(package, reading, child)
        case _:
            return


def _paragraph(package: _Package, reading: _Reading, element: Element) -> None:
    properties = _child(element, "pPr")
    style_id: str | None = None
    numbering: tuple[str, int] | None = None
    outline: int | None = None
    if properties is not None:
        style = _child(properties, "pStyle")
        style_id = _attribute(style, "val") if style is not None else None
        numbered = _child(properties, "numPr")
        if numbered is not None:
            number = _child(numbered, "numId")
            level = _child(numbered, "ilvl")
            number_id = _attribute(number, "val") if number is not None else None
            level_value = (_attribute(level, "val") if level is not None else None) or "0"
            if number_id not in (None, "0"):
                numbering = (number_id, int(level_value) if level_value.isdigit() else 0)
        direct = _child(properties, "outlineLvl")
        value = _attribute(direct, "val") if direct is not None else None
        outline = int(value) if value is not None and value.isdigit() else None
    text = _markdown(_segments(element))
    if not text.strip():
        return
    title, heading, styled_list = _style_meaning(package.styles, style_id)
    if numbering is None and styled_list is not None:
        numbering = (styled_list, 0)
    level = outline if outline is not None else heading
    if title:
        _close_list(reading)
        _emit(reading, {"type": "titre", "level": 0, "text": text}, heading=(0, text))
    elif level is not None and level < 9:
        _close_list(reading)
        _emit(
            reading, {"type": "titre", "level": level + 1, "text": text}, heading=(level + 1, text)
        )
    elif numbering is not None:
        number_id, ilvl = numbering
        ordered = package.numbering.get(number_id, {}).get(ilvl, False)
        if reading.pending is not None and reading.pending_id != number_id:
            _close_list(reading)
        reading.pending_id = number_id
        _list_item(reading, text, ilvl, ordered)
    else:
        _close_list(reading)
        _emit(
            reading,
            {"type": _KIND.get(_style_name(package.styles, style_id), "paragraphe"), "text": text},
        )


# Les styles qui disent un autre bloc que le paragraphe : ceux qu'écrit l'outil, et
# leurs équivalents intégrés de Word.
_KIND: Final = {
    "quote": "citation",
    "intense quote": "citation",
    "quote source": "citation",
    "note": "note",
}


def _style_name(styles: dict[str, _Style], style_id: str | None) -> str:
    style = styles.get(style_id or "")
    return style.name if style is not None else ""


def _style_meaning(
    styles: dict[str, _Style], style_id: str | None
) -> tuple[bool, int | None, str | None]:
    """Le style d'un paragraphe : titre du document ? niveau de titre ? élément de liste ?

    Suivi de ``basedOn`` en ``basedOn`` (borné : une boucle ne bloque pas) ;
    les noms intégrés de Word sont en anglais dans le fichier, quelle que soit
    la langue de l'interface (« heading 1 », « Title »).
    """
    seen: set[str] = set()
    title = False
    outline: int | None = None
    listed: str | None = None
    current = style_id
    while current is not None and current not in seen and len(seen) < 20:
        seen.add(current)
        style = styles.get(current)
        if style is None:
            break
        if style.name == "title" and current == style_id:
            title = True
        if outline is None and style.outline is not None:
            outline = style.outline
        if outline is None and style.name.startswith("heading "):
            number = style.name.removeprefix("heading ")
            outline = int(number) - 1 if number.isdigit() and number != "0" else None
        listed = listed or style.num_id
        current = style.based_on
    return title, outline, listed


def _segments(element: Element) -> list[tuple[str, bool, bool]]:
    """Le texte d'un paragraphe, en segments (texte, gras, italique), dans l'ordre."""
    found: list[tuple[str, bool, bool]] = []

    def walk(node: Element, bold: bool, italic: bool) -> None:
        name = _local(node.tag)
        if name in _SKIPPED:
            return
        if name == "r":
            properties = _child(node, "rPr")
            if properties is not None:
                bold = _on(_child(properties, "b"))
                italic = _on(_child(properties, "i"))
        elif name == "t":
            found.append((node.text or "", bold, italic))
            return
        elif name == "tab":
            found.append(("\t", bold, italic))
            return
        elif name in {"br", "cr"}:
            if _attribute(node, "type") != "page":
                found.append(("\n", bold, italic))
            return
        elif name == "noBreakHyphen":
            found.append(("-", bold, italic))
            return
        elif name is None and node.tag.endswith("}AlternateContent"):
            # Le choix d'un logiciel récent (zone de texte, forme) : rien de lisible ici.
            return
        for child in node:
            walk(child, bold, italic)

    for child in element:
        walk(child, False, False)
    return found


def _markdown(segments: list[tuple[str, bool, bool]]) -> str:
    """Les segments joints, l'emphase en ``**`` et ``*`` comme à l'écriture."""
    merged: list[tuple[str, bool, bool]] = []
    for text, bold, italic in segments:
        if merged and merged[-1][1:] == (bold, italic):
            merged[-1] = (merged[-1][0] + text, bold, italic)
        else:
            merged.append((text, bold, italic))
    said: list[str] = []
    for text, bold, italic in merged:
        core = text.strip()
        if not core or not (bold or italic):
            said.append(text)
            continue
        mark = ("**" if bold else "") + ("*" if italic else "")
        lead = text[: len(text) - len(text.lstrip())]
        tail = text[len(text.rstrip()) :]
        said.append(f"{lead}{mark}{core}{mark[::-1]}{tail}")
    return "".join(said).strip()


def _table(package: _Package, reading: _Reading, element: Element) -> list[JsonValue]:
    rows: list[JsonValue] = []
    for row in element:
        if _local(row.tag) != "tr":
            continue
        cells: list[JsonValue] = []
        for cell in row:
            if _local(cell.tag) != "tc":
                continue
            properties = _child(cell, "tcPr")
            span = 1
            merged = False
            if properties is not None:
                spanned = _child(properties, "gridSpan")
                value = _attribute(spanned, "val") if spanned is not None else None
                span = int(value) if value is not None and value.isdigit() else 1
                vertical = _child(properties, "vMerge")
                merged = vertical is not None and _attribute(vertical, "val") != "restart"
            text = "" if merged else _cell_text(cell)
            if len(text) > MAX_CELL_CHARS:
                text = text[:MAX_CELL_CHARS] + "…"
                reading.cut += 1
            cells.append(text)
            cells.extend("" for _ in range(min(span, MAX_COLUMNS) - 1))
        rows.append(cells[:MAX_COLUMNS])
    return rows


def _cell_text(cell: Element) -> str:
    """Le texte d'une cellule : ses paragraphes, et ceux d'un tableau imbriqué, en lignes."""
    lines: list[str] = []
    for paragraph in cell.iter():
        if _local(paragraph.tag) == "p":
            said = "".join(text for text, _, _ in _segments(paragraph)).strip()
            if said:
                lines.append(said)
    return "\n".join(lines)


def _list_item(reading: _Reading, text: str, level: int, ordered: bool) -> None:
    pending = reading.pending
    if pending is None:
        pending = reading.pending = {"type": "liste", "ordered": ordered, "items": []}
    items = cast(list[JsonValue], pending["items"])
    if level == 0 or not items:
        items.append(text)
        return
    # Une sous-liste : sous l'élément précédent (au-delà d'un niveau, aplatie).
    last = items[-1]
    if isinstance(last, str):
        items[-1] = {"text": last, "items": [text]}
    else:
        cast(list[JsonValue], cast(dict[str, JsonValue], last)["items"]).append(text)


def _close_list(reading: _Reading) -> None:
    if reading.pending is not None:
        pending, reading.pending = reading.pending, None
        _emit(reading, pending)


def _emit(
    reading: _Reading, block: dict[str, JsonValue], heading: tuple[int, str] | None = None
) -> None:
    index = reading.count
    reading.count += 1
    if heading is not None and len(reading.outline) < MAX_OUTLINE:
        level, text = heading
        reading.outline.append({"index": index, "level": level, "text": text[:200]})
    if index < reading.start or reading.full:
        return
    size = len(json.dumps(block, ensure_ascii=False))
    if reading.blocks and reading.used + size > READ_CHARS:
        # Le bloc ne tient plus : il sera le premier de la suite.
        reading.full = True
        reading.next_start = index
        return
    if size > READ_CHARS:
        block = _shortened(block)
        reading.cut += 1
    reading.blocks.append(block)
    reading.used += size


def _shortened(block: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """Un bloc seul plus grand que le budget : coupé, pour que la lecture avance."""
    said = json.dumps(block, ensure_ascii=False)
    return {"type": block.get("type"), "text": said[: READ_CHARS - 200] + "…", "cut": True}


def _bounded(outline: list[JsonValue]) -> list[JsonValue]:
    """Le plan, dans sa propre borne : avec les blocs, le résultat reste sous le déport."""
    kept: list[JsonValue] = []
    used = 0
    for entry in outline:
        used += len(json.dumps(entry, ensure_ascii=False))
        if used > MAX_OUTLINE_CHARS:
            break
        kept.append(entry)
    return kept


def _notes(package: _Package, reading: _Reading, start: int) -> list[JsonValue]:
    notes: list[JsonValue] = []
    if start >= reading.count and reading.count:
        notes.append(
            f"Le document a {reading.count} bloc(s) : start va de 0 à {reading.count - 1}."
        )
    if reading.next_start is not None:
        notes.append(
            f"Lecture coupée par la taille du résultat ({READ_CHARS} caractères) : la suite "
            f"commence au bloc {reading.next_start} (start) ; 'outline' donne le plan."
        )
    if reading.images:
        notes.append(f"{reading.images} image(s) ou forme(s) non lue(s).")
    if reading.revisions:
        notes.append(
            "Modifications suivies présentes : le texte inséré est rendu, le texte supprimé omis."
        )
    if package.comments:
        notes.append("Le document a des commentaires, non lus.")
    if reading.cut:
        notes.append(f"{reading.cut} texte(s) coupé(s).")
    return notes
