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
L'emphase revient en ``**gras**``, ``*italique*`` et ``<u>souligné</u>`` : ce qui est lu peut être
réécrit tel quel. Ce qu'un style de document a posé ne revient pas, puisque le style
le repose à la réécriture : un tableau de disposition (style « loom layout » : des
sections côte à côte, un encadré, une grille d'images) rend les blocs de ses
cellules, dans l'ordre ; une légende numérotée (« Figure 2 — Avant ») rend son texte
sans le numéro. Un document qui suit un modèle revient par sections (``template``,
``sections``) : chaque section commence à son signet (``_loom_s<n>``) et court jusqu'au
signet de la suivante ; son titre,
que l'outil écrit, ne revient pas, ni une section au texte fixe ; un style proposé au
modèle de langage revient dans ``style``. Une zone de signature revient en
bloc ``signature``, un sommaire en bloc ``sommaire`` (ses entrées ne reviennent pas),
une liste à cocher en ``liste`` avec ``checkboxes`` et ses cases en [x] ou [ ], des
champs (tableau « loom fields ») en bloc ``fields``, du code (style « loom code ») en bloc
``code``. Dans un texte, un lien revient en ``[texte](adresse)``, une note de bas de
page (ou de fin) en ``^[texte]``, un renvoi à un titre (un champ ``REF``, ou un lien
interne, vers un signet de ce titre) en ``§[Titre]`` ; dans un tableau, l'emphase d'une
ligne d'en-tête ou d'une ligne toute en gras (un total) ne revient pas. Un tableau à
groupes revient en ``{group, rows}``, sans ses sous-totaux, que l'outil recalcule ; un
récapitulatif de TVA, en bloc ``invoice_totals`` (ce que l'outil en a gardé dans sa
description). Avec ``shown`` (``loom.memory``), tout cela revient tel qu'affiché, en
lignes de tableau. Avec
``images``, une image du corps revient en bloc ``image`` (``path`` : l'adresse du
fichier lu à l'écriture, gardée dans le nom du dessin) ; sans, elle est comptée. Les
en-têtes, pieds de page et commentaires ne sont pas lus ; le résultat le dit.
"""

import io
import json
import posixpath
import re
import time
import zipfile
from collections.abc import Generator, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Final, cast
from xml.etree.ElementTree import Element, ParseError, XMLPullParser

from pydantic import JsonValue

from loom_ia.core.ports import ToolError
from loom_ia.tools_builtin._document.arguments import (
    MAX_IMAGE_BYTES,
    MAX_SOURCE_BYTES,
    READ_CHARS,
    ImageWidth,
)
from loom_ia.tools_builtin._document.content import IMAGE_WIDTHS, LINK_SCHEMES, placed
from loom_ia.tools_builtin._document.picture import dimensions
from loom_ia.tools_builtin._templates import Template, key

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
_CP_CATEGORY: Final = (
    "{http://schemas.openxmlformats.org/package/2006/metadata/core-properties}category"
)
_FALSE: Final = frozenset({"0", "false", "off"})
# Le nom du style de tableau de disposition qu'écrit l'outil (``word/style.py``).
LAYOUT_STYLE: Final = "loom layout"
FIELDS_STYLE: Final = "loom fields"
# Les lignes d'un groupe et de son sous-total, et le récapitulatif de TVA : le style de
# paragraphe de leurs cellules.
GROUP_STYLE: Final = "loom table group"
SUBTOTAL_STYLE: Final = "loom table subtotal"
INVOICE_STYLE: Final = "loom invoice totals"
# Les styles des blocs signature et toc, et de la liste à cocher (``word/style.py``), par leur nom.
SIGNATURE_STYLE: Final = "loom signature"
SIGNER_STYLE: Final = "loom signer"
MENTION_STYLE: Final = "loom mention"
CHECK_STYLES: Final = {"loom checkbox": 0, "loom checkbox 2": 1}
# Une case cochée (☒, ☑, ✓, ✔) ou vide (☐), en tête d'un élément de liste à cocher.
_CHECKED: Final = frozenset("\u2612\u2611\u2713\u2714")
_BOXES: Final = _CHECKED | {"\u2610"}
# Un tableau de disposition dans un autre (une grille d'images dans une colonne) : borné.
MAX_LAYOUT_DEPTH: Final = 4
# Ce qui sépare le numéro d'une légende de son texte (« Figure 2 — Avant »).
_CAPTION_SEPARATORS: Final = " \u2014\u2013-:."
# La version de secours d'un contenu « compatible » (``mc:AlternateContent``) : Word y
# range le même dessin en VML, que ``mc:Choice`` porte déjà ; il ne compte pas deux fois.
_MC_FALLBACK: Final = "{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback"
# Sous-arbres d'un paragraphe qui ne sont pas son texte.
_SKIPPED: Final = frozenset(
    {"del", "delText", "moveFrom", "drawing", "pict", "object", "commentReference", "pPr", "rPr"}
)  # fmt: skip
# Un segment de texte : son texte, gras, italique, souligné ; ou déjà en balisage (un lien,
# une note, un renvoi : ``raw``), que l'emphase n'enveloppe pas.
type _Segment = tuple[str, bool, bool, bool, bool]
# Un renvoi lu avant de connaître les titres du document : le signet qu'il vise et le texte
# qu'il affiche, entre deux caractères qu'un XML ne peut pas porter ; remplacé à la fin de
# la lecture par ``§[Titre]``, ou par son texte si le signet n'est pas celui d'un titre.
_PENDING_REF: Final = re.compile("\x00([^\x00\x01]*)\x01([^\x00]*)\x00")


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
    # Le style annule la numérotation de son parent (``numId 0``) : un titre masqué
    # d'un document aux titres numérotés, l'en-tête de table des matières de Word.
    numbering_off: bool = False


@dataclass(slots=True)
class _Package:
    archive: zipfile.ZipFile
    body: str
    styles: dict[str, _Style]
    # Les styles de tableau, par identifiant : leur nom (« loom layout »…), en minuscules.
    tables: dict[str, str]
    # numId → numéroté (vrai) ou à puces, par niveau.
    numbering: dict[str, dict[int, bool]]
    title: str | None
    # La catégorie du document : le modèle suivi, pour un document écrit avec un modèle.
    category: str | None
    comments: bool
    # Identifiant de relation → partie d'un graphique (``word/charts/chartN.xml``).
    charts: dict[str, str] = field(default_factory=dict[str, str])
    # Identifiant de relation → partie d'une image (``word/media/imageN.png``).
    media: dict[str, str] = field(default_factory=dict[str, str])
    # De quoi lire les liens et les notes du corps.
    inline: _Inline | None = None


@dataclass(frozen=True, slots=True)
class _Inline:
    """Ce que demande le texte enrichi d'une partie : l'adresse de ses liens (par relation),
    et le texte des notes du document (« f3 » : la note de bas de page 3, « e1 » : la note
    de fin 1)."""

    links: Mapping[str, str]
    notes: Mapping[str, str]


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
    title, category = _properties(archive, names, where)
    styles_part = related.get("styles")
    numbering_part = related.get("numbering")
    styles_root = _whole(archive, styles_part, where) if styles_part else None
    notes = _note_texts(archive, names, related, where)
    return _Package(
        archive=archive,
        body=body,
        styles=_styles(styles_root),
        tables=_table_styles(styles_root),
        numbering=_numbering(_whole(archive, numbering_part, where)) if numbering_part else {},
        title=title,
        category=category,
        comments="comments" in related,
        charts=_relations(archive, names, body, by_id="chart"),
        media=_relations(archive, names, body, by_id="image"),
        inline=_Inline(_external(archive, names, body), notes),
    )


def _external(archive: zipfile.ZipFile, names: set[str], part: str) -> dict[str, str]:
    """Les liens d'une partie : l'adresse de chacun, par identifiant de relation."""
    folder, _, file = part.rpartition("/")
    rels = f"{folder}/_rels/{file}.rels" if folder else f"_rels/{file}.rels"
    if rels not in names or archive.getinfo(rels).file_size > MAX_PART:
        return {}
    return {
        relation.get("Id", ""): relation.get("Target", "")
        for relation in _parse(archive.read(rels)).iter(f"{_REL_NS}Relationship")
        if relation.get("TargetMode") == "External"
        and relation.get("Type", "").endswith("/hyperlink")
    }


def _note_texts(
    archive: zipfile.ZipFile, names: set[str], related: Mapping[str, str], where: str
) -> dict[str, str]:
    """Le texte de chaque note de bas de page et de fin (sans ses séparateurs), ses liens
    et ses renvois compris, ses paragraphes en lignes."""
    found: dict[str, str] = {}
    for kind, prefix in (("footnotes", "f"), ("endnotes", "e")):
        part = related.get(kind)
        root = _whole(archive, part, where) if part else None
        if part is None or root is None:
            continue
        inline = _Inline(_external(archive, names, part), {})
        for note in root:
            if _local(note.tag) not in ("footnote", "endnote"):
                continue
            if _attribute(note, "type") not in (None, "normal"):
                continue
            lines = (
                _markdown(_segments(paragraph, inline))
                for paragraph in note.iter()
                if _local(paragraph.tag) == "p"
            )
            text = "\n".join(line for line in lines if line)
            if text:
                found[f"{prefix}{_attribute(note, 'id')}"] = text
    return found


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


def _relations(
    archive: zipfile.ZipFile, names: set[str], body: str, *, by_id: str | None = None
) -> dict[str, str]:
    """Les parties liées au corps, par type (« styles », « numbering »…) ; avec ``by_id``,
    celles de ce type, par identifiant de relation (les graphiques)."""
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
        if by_id is None:
            found.setdefault(kind, posixpath.normpath(path))
        elif kind == by_id:
            found[relation.get("Id", "")] = posixpath.normpath(path)
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
        off = False
        if properties is not None:
            level = _child(properties, "outlineLvl")
            value = _attribute(level, "val") if level is not None else None
            outline = int(value) if value is not None and value.isdigit() else None
            numbering = _child(properties, "numPr")
            if numbering is not None:
                number = _child(numbering, "numId")
                value = _attribute(number, "val") if number is not None else None
                num_id = value if value not in (None, "0") else None
                off = value == "0"
        found[style_id] = _Style(
            name=name.lower(),
            based_on=_attribute(based, "val") if based is not None else None,
            outline=outline,
            num_id=num_id,
            numbering_off=off,
        )
    return found


def _table_styles(root: Element | None) -> dict[str, str]:
    """Les styles de tableau : leur nom, par identifiant (« loom layout », « loom signature »)."""
    if root is None:
        return {}
    found: dict[str, str] = {}
    for style in root:
        if _local(style.tag) != "style" or _attribute(style, "type") != "table":
            continue
        name_element = _child(style, "name")
        name = (_attribute(name_element, "val") if name_element is not None else None) or ""
        style_id = _attribute(style, "styleId")
        if style_id is not None:
            found[style_id] = name.lower()
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


def _properties(
    archive: zipfile.ZipFile, names: set[str], where: str
) -> tuple[str | None, str | None]:
    """Le titre et la catégorie du document (où l'écriture range le modèle suivi)."""
    if "docProps/core.xml" not in names:
        return None, None
    root = _whole(archive, "docProps/core.xml", where)
    if root is None:
        return None, None
    said: list[str | None] = []
    for tag in (_DC_TITLE, _CP_CATEGORY):
        element = root.find(tag)
        text = (element.text or "").strip() if element is not None else ""
        said.append(text[:MAX_CELL_CHARS] or None)
    return said[0], said[1]


# --- Corps ---------------------------------------------------------------------------


@dataclass(slots=True)
class _Reading:
    """Ce que la lecture rend, et ce qu'elle a vu en passant."""

    start: int
    # Taille du résultat, en caractères JSON : au-delà, la lecture s'arrête (``next_start``).
    budget: int = READ_CHARS
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
    # Avec ``images`` : les images rendues en blocs.
    pictures: bool = False
    # La dernière image rendue : un paragraphe de légende qui la suit devient sa caption.
    last_image: dict[str, JsonValue] | None = None
    # Dans un sommaire : ses entrées ne sont pas des blocs, le bloc ``sommaire`` est rendu.
    in_toc: bool = False
    # Le modèle que le document suit (si l'outil le déclare), et les styles proposés.
    template: Template | None = None
    proposed: frozenset[str] = frozenset()
    # La section en cours (sa place dans le modèle), par son signet ; celles vues.
    section: int | None = None
    seen: set[int] = field(default_factory=set[int])
    # La section de chaque bloc rendu (``None`` hors des sections).
    where: list[int | None] = field(default_factory=list[int | None])
    # Les signets des titres : le texte du titre, que vise un renvoi à ce signet.
    anchors: dict[str, str] = field(default_factory=dict[str, str])
    # Ce qui s'affiche, plutôt que ce qui se réécrit : les groupes, leurs sous-totaux et le
    # récapitulatif de TVA en lignes de tableau.
    shown: bool = False


def read(
    data: bytes,
    where: str,
    start: int,
    budget: int = READ_CHARS,
    *,
    images: bool = False,
    templates: Mapping[str, Template] | None = None,
    styles: frozenset[str] = frozenset(),
    shown: bool = False,
) -> dict[str, JsonValue]:
    """Les blocs du document à partir de ``start``, dans un budget de taille.

    ``budget`` : celui d'un résultat d'outil par défaut ; ``loom.memory``, qui lit
    le document entier, en donne un plus grand. ``images`` : les images du corps
    reviennent en blocs ``image`` (un outil monté avec ``images``) ; sinon, comptées.
    ``templates`` : les modèles de l'outil ; un document qui en suit un revient par
    sections (``template``, ``sections``). Sans modèles (``None``), il revient à plat.
    ``styles`` : les styles proposés au modèle de langage, rendus dans ``style``.
    ``shown`` : les tableaux tels qu'affichés (``loom.memory``, qui indexe le texte et ne
    réécrit rien).
    """
    deadline = time.monotonic() + READ_DEADLINE
    with _guarded(where):
        package = _open(data, where)
        with package.archive:
            followed = (templates or {}).get(package.category or "")
            reading = _Reading(
                start=start,
                budget=budget,
                pictures=images,
                template=followed,
                proposed=styles,
                shown=shown,
            )
            _walk(package, reading, deadline, where)
    reading.blocks = [_resolved(block, reading.anchors) for block in reading.blocks]
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
    template = reading.template
    if template is not None and reading.seen:
        _by_section(result, reading, template, notes)
    elif template is not None:
        notes.append(
            f"Le document dit suivre le modèle « {template.name} », mais ses sections ne se "
            "retrouvent pas (un document d'avant 2.1.0, ou des sections coupées et "
            "recollées) : il est relu en document libre ; pour le réécrire, template none, ou "
            "son contenu par sections."
        )
    elif templates is not None and package.category is not None:
        result["template"] = package.category
        notes.append(
            f"Le document dit suivre le modèle « {package.category} », que cet outil ne "
            "déclare pas : pour le réécrire, nommer un modèle déclaré, ou none."
        )
    if notes:
        result["notes"] = notes
    return result


def _resolved(value: JsonValue, anchors: Mapping[str, str]) -> JsonValue:
    """Les renvois lus, une fois les titres connus : ``§[Titre]`` vers un titre, le texte
    affiché vers un autre signet."""

    def replaced(found: re.Match[str]) -> str:
        title = anchors.get(found.group(1))
        return f"§[{title}]" if title else found.group(2)

    if isinstance(value, str):
        return _PENDING_REF.sub(replaced, value) if "\x00" in value else value
    if isinstance(value, list):
        return [_resolved(item, anchors) for item in value]
    if isinstance(value, dict):
        return {name: _resolved(item, anchors) for name, item in value.items()}
    return value


def _by_section(
    result: dict[str, JsonValue], reading: _Reading, template: Template, notes: list[JsonValue]
) -> None:
    """Un document qui suit un modèle : ses blocs rangés par section, dans l'ordre ; le
    titre du document est déjà dans ``title`` (il est dans la section d'une page de garde,
    qui le porte : il n'en est pas un bloc)."""
    names = {section.position: section.name for section in template.sections}
    sections: dict[str, JsonValue] = {}
    outside: list[JsonValue] = []
    for block, at in zip(reading.blocks, reading.where, strict=True):
        if isinstance(block, dict) and block.get("type") == "heading" and block.get("level") == 0:
            continue
        if at is not None:
            found = sections.setdefault(names.get(at, f"section {at}"), list[JsonValue]())
            cast(list[JsonValue], found).append(block)
        else:
            outside.append(block)
    result["template"] = template.name
    result["sections"] = sections
    del result["blocks"]
    if outside:
        result["blocks"] = outside
        notes.append(
            f"{len(outside)} bloc(s) hors des sections du modèle (dans 'blocks') : à mettre "
            "dans une section pour réécrire le document."
        )


def _mark(reading: _Reading, element: Element) -> None:
    """Un signet : le début d'une section du modèle (``_loom_s<n>``), qui court jusqu'au
    début de la suivante. Sa fin ne compte pas : LibreOffice, qui reprend un document,
    dédouble un signet en plusieurs, certains vides ; le premier début est à sa place."""
    if reading.template is None or _local(element.tag) != "bookmarkStart":
        return
    found = _SECTION_MARK.match(_attribute(element, "name") or "")
    if found is None:
        return
    section = int(found.group(1))
    if section != reading.section:
        _close_list(reading)
    reading.section = section
    reading.seen.add(section)


def _marks_in(reading: _Reading, element: Element) -> None:
    """Les signets de section d'un tableau (dans le premier paragraphe d'une cellule,
    quand la section commence par lui) : avant le tableau, qui est de la section."""
    for child in element.iter():
        if _local(child.tag) == "bookmarkStart":
            _mark(reading, child)


# Le signet d'une section du modèle : sa place.
_SECTION_MARK: Final = re.compile(r"^_loom_s(\d+)$")


def _walk(package: _Package, reading: _Reading, deadline: float, where: str) -> None:
    parser: XMLPullParser[Element] = XMLPullParser(events=("start", "end"))
    depth = 0
    body_depth: int | None = None
    body: Element | None = None
    seen = 0
    # Dans un ``mc:Fallback`` : ses dessins sont ceux du ``mc:Choice`` qui précède ; ils ne
    # sont pas comptés (B-2).
    fallback = 0
    with package.archive.open(package.body) as stream:
        while chunk := stream.read(1 << 16):
            parser.feed(chunk)
            for event, element in cast(Iterator[tuple[str, Element]], parser.read_events()):
                if event == "start":
                    depth += 1
                    name = _local(element.tag)
                    if element.tag == _MC_FALLBACK:
                        fallback += 1
                    elif name == "body" and body_depth is None:
                        body_depth, body = depth, element
                    elif name in {"drawing", "pict"} and not fallback:
                        reading.images += 1
                    elif name in {"ins", "del", "moveFrom", "moveTo"}:
                        reading.revisions += 1
                    continue
                if element.tag == _MC_FALLBACK:
                    fallback -= 1
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


def _block(package: _Package, reading: _Reading, element: Element, depth: int = 0) -> None:
    match _local(element.tag):
        case "bookmarkStart" | "bookmarkEnd":
            _mark(reading, element)
        case "p":
            for child in element:
                if _local(child.tag) == "bookmarkStart":
                    _mark(reading, child)
            _paragraph(package, reading, element)
        case "tbl" if _table_style(package, element) == SIGNATURE_STYLE:
            _close_list(reading)
            reading.in_toc = False
            _marks_in(reading, element)
            _emit(reading, _signature(package, element))
        case "tbl" if not reading.shown and (invoice := _invoice(package, element)) is not None:
            _close_list(reading)
            reading.in_toc = False
            _marks_in(reading, element)
            _emit(reading, invoice)
        case "tbl" if _table_style(package, element) == FIELDS_STYLE:
            _close_list(reading)
            reading.in_toc = False
            _marks_in(reading, element)
            _emit(reading, _fields(package, element))
        case "tbl" if depth < MAX_LAYOUT_DEPTH and _table_style(package, element) == LAYOUT_STYLE:
            # Un tableau de disposition : ses cellules, dans l'ordre, sont des blocs.
            for row in element:
                for cell in row if _local(row.tag) == "tr" else ():
                    for child in cell if _local(cell.tag) == "tc" else ():
                        _block(package, reading, child, depth + 1)
        case "tbl":
            _close_list(reading)
            reading.in_toc = False
            _marks_in(reading, element)
            table: dict[str, JsonValue] = {
                "type": "table",
                "rows": _table(package, reading, element),
            }
            styled = _table_style_name(package, reading, element)
            if styled is not None:
                table["style"] = styled
            _emit(reading, table)
        case "sdt":
            content = _child(element, "sdtContent")
            for child in list(content) if content is not None else []:
                _block(package, reading, child, depth)
        case _:
            return


def _table_style(package: _Package, table: Element) -> str | None:
    """Le nom du style d'un tableau, en minuscules ; ``None`` sans style."""
    properties = _child(table, "tblPr")
    style = _child(properties, "tblStyle") if properties is not None else None
    style_id = _attribute(style, "val") if style is not None else None
    return package.tables.get(style_id or "")


def _table_style_name(package: _Package, reading: _Reading, table: Element) -> str | None:
    """Le style proposé d'un tableau : celui du texte de sa première ligne de données (pas
    d'une ligne de groupe ni de sous-total)."""
    rows = [row for row in table if _local(row.tag) == "tr"]
    for row in rows[1:]:
        name = _row_style(package, row)
        if name in (GROUP_STYLE, SUBTOTAL_STYLE):
            continue
        return name if name in reading.proposed else None
    return None


def _row_style(package: _Package, row: Element) -> str:
    """Le nom du style de paragraphe d'une ligne de tableau : celui de son premier
    paragraphe ; « » sans paragraphe ni style."""
    for paragraph in row.iter():
        if _local(paragraph.tag) == "p":
            properties = _child(paragraph, "pPr")
            style = _child(properties, "pStyle") if properties is not None else None
            return _style_name(
                package.styles, _attribute(style, "val") if style is not None else None
            )
    return ""


def _invoice(package: _Package, table: Element) -> dict[str, JsonValue] | None:
    """Un récapitulatif de TVA écrit par l'outil : le bloc ``invoice_totals`` que sa
    description garde (ses colonnes, l'acompte) ; ``None`` pour un autre tableau, ou un
    récapitulatif dont la description manque."""
    rows = [row for row in table if _local(row.tag) == "tr"]
    if not rows or _row_style(package, rows[0]) != INVOICE_STYLE:
        return None
    properties = _child(table, "tblPr")
    described = _child(properties, "tblDescription") if properties is not None else None
    try:
        data = json.loads(_attribute(described, "val") or "") if described is not None else None
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    found = cast(dict[str, JsonValue], data)
    amount, rate, paid = found.get("amount"), found.get("rate"), found.get("paid")
    if not isinstance(amount, str) or not isinstance(rate, str):
        return None
    said: dict[str, JsonValue] = {
        "type": "invoice_totals",
        "from_table": {"amount": amount, "rate": rate},
    }
    if isinstance(paid, int | float) and not isinstance(paid, bool):
        said["paid"] = paid
    return said


def _fields(package: _Package, table: Element) -> dict[str, JsonValue]:
    """Des champs : le libellé et la valeur de chaque ligne (la valeur avec son emphase,
    ses liens, ses notes)."""
    items: list[JsonValue] = []
    for row in table:
        cells = (
            [cell for cell in row if _local(cell.tag) == "tc"] if _local(row.tag) == "tr" else []
        )
        if len(cells) < 2:
            continue
        value = "\n".join(
            said
            for paragraph in cells[1].iter()
            if _local(paragraph.tag) == "p"
            and (said := _markdown(_segments(paragraph, package.inline)))
        )
        items.append([_cell_text(cells[0]), value])
    return {"type": "fields", "items": items}


def _signature(package: _Package, table: Element) -> dict[str, JsonValue]:
    """La zone de signature : le libellé de chaque signataire, et la mention."""
    signers: list[JsonValue] = []
    mention: str | None = None
    for paragraph in table.iter():
        if _local(paragraph.tag) != "p":
            continue
        properties = _child(paragraph, "pPr")
        style = _child(properties, "pStyle") if properties is not None else None
        name = _style_name(package.styles, _attribute(style, "val") if style is not None else None)
        text = "".join(piece for piece, *_ in _segments(paragraph)).strip()
        if name == SIGNER_STYLE and text:
            signers.append(text)
        elif name == MENTION_STYLE and text and mention is None:
            mention = text
    found: dict[str, JsonValue] = {"type": "signature", "signers": signers}
    if mention is not None:
        found["mention"] = mention
    return found


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
    name = _style_name(package.styles, style_id)
    if name == "toc heading" or _TOC_ENTRY.match(name):
        # Le sommaire : son titre et ses entrées viennent des titres du document ; un
        # seul bloc ``sommaire`` les remplace.
        if not reading.in_toc:
            _close_list(reading)
            _emit(reading, {"type": "toc"})
            reading.in_toc = True
        return
    reading.in_toc = False
    for chart_id in _chart_ids(element):
        _close_list(reading)
        reading.images -= 1
        _emit(reading, _chart(package, chart_id))
    if _kind(package.styles, style_id) == "code":
        # Un bloc de code : un paragraphe, ses lignes séparées par des sauts de ligne.
        _close_list(reading)
        code = "".join(text for text, *_ in _segments(element))
        if code.strip():
            _emit(reading, {"type": "code", "text": code})
        return
    text = _markdown(_segments(_without_number(element), package.inline))
    if name in CHECK_STYLES and text[:1] in _BOXES:
        _checkbox(reading, text, CHECK_STYLES[name])
        return
    pictures = _pictures(package, element) if reading.pictures else []
    if text.strip() and reading.last_image is not None and reading.pending is None and not pictures:
        if _style_name(package.styles, style_id) == "caption":
            # La légende de l'image qui précède : sa caption, pas un bloc.
            image, reading.last_image = reading.last_image, None
            if image.get("alt") == text:
                del image["alt"]
            image["caption"] = text
            return
    reading.last_image = None
    for picture in pictures:
        _close_list(reading)
        reading.images -= 1
        _emit(reading, picture)
        reading.last_image = picture
    if not text.strip():
        return
    reading.last_image = None
    title, heading, styled_list = _style_meaning(package.styles, style_id)
    if numbering is None and styled_list is not None:
        numbering = (styled_list, 0)
    level = outline if outline is not None else heading
    own = name if name in reading.proposed else None
    if level is not None and level < 9 and not title:
        # Les signets d'un titre : un renvoi vers l'un d'eux est un renvoi à ce titre.
        said = "".join(text for text, *_ in _segments(_without_number(element))).strip()
        for mark in element.iter():
            if _local(mark.tag) == "bookmarkStart":
                reading.anchors.setdefault(_attribute(mark, "name") or "", said)
    if title:
        _close_list(reading)
        _emit(reading, {"type": "heading", "level": 0, "text": text}, heading=(0, text))
    elif level is not None and level < 9:
        _close_list(reading)
        if level == 0 and _section_title(reading, text):
            # Le titre de la section : l'outil l'écrit, il ne revient pas.
            return
        block: dict[str, JsonValue] = {"type": "heading", "level": level + 1, "text": text}
        _emit(reading, _styled(block, own), heading=(level + 1, text))
    elif numbering is not None:
        number_id, ilvl = numbering
        ordered = package.numbering.get(number_id, {}).get(ilvl, False)
        if reading.pending is not None and reading.pending_id != number_id:
            _close_list(reading)
        reading.pending_id = number_id
        _list_item(reading, text, ilvl, ordered, own)
    else:
        _close_list(reading)
        kind = _kind(package.styles, style_id)
        _emit(reading, _styled({"type": kind, "text": text}, own))


# Une entrée de sommaire (« toc 1 » à « toc 9 »).
_TOC_ENTRY: Final = re.compile(r"^toc [1-9]$")


def _checkbox(reading: _Reading, text: str, level: int) -> None:
    """Un élément de liste à cocher : « [x] » ou « [ ] » à la place de la case."""
    mark = "[x]" if text[0] in _CHECKED else "[ ]"
    item = f"{mark} {text[1:].lstrip()}"
    if reading.pending is not None and reading.pending_id != _CHECKLIST:
        _close_list(reading)
    if reading.pending is None:
        reading.pending = {"type": "list", "ordered": False, "checkboxes": True, "items": []}
    reading.pending_id = _CHECKLIST
    _list_item(reading, item, level, False)


_CHECKLIST: Final = "checkboxes"


# Les styles qui disent un autre bloc que le paragraphe : ceux qu'écrit l'outil, et
# leurs équivalents intégrés de Word.
_KIND: Final = {
    "loom code": "code",
    "quote": "quote",
    "intense quote": "quote",
    "quote source": "quote",
    "note": "note",
}


def _without_number(paragraph: Element) -> Element:
    """Une légende numérotée sans son numéro : ce qui suit le champ ``SEQ`` (« Figure 2 »).

    Le numéro vient du style (``captions.numbered``) : relu, il se doublerait à la
    réécriture. Un champ simple (``fldSimple``) ou complexe (``fldChar``) ; un paragraphe
    sans champ ``SEQ`` est rendu tel quel.
    """
    children = list(paragraph)
    after: int | None = None
    sequence = False
    for index, child in enumerate(children):
        name = _local(child.tag)
        if name == "fldSimple" and (_attribute(child, "instr") or "").split()[:1] == ["SEQ"]:
            after = index + 1
            break
        if name == "r":
            for part in child:
                if _local(part.tag) == "instrText" and (part.text or "").split()[:1] == ["SEQ"]:
                    sequence = True
                elif (
                    sequence
                    and _local(part.tag) == "fldChar"
                    and _attribute(part, "fldCharType") == "end"
                ):
                    after = index + 1
            if after is not None:
                break
    if after is None:
        return paragraph
    kept = Element(paragraph.tag)
    kept.extend(children[after:])
    # Le séparateur (« — ») ouvre le texte qui suit le numéro.
    for node in kept.iter():
        if _local(node.tag) == "t" and node.text:
            node.text = node.text.lstrip(_CAPTION_SEPARATORS)
            if node.text:
                break
    return kept


def _style_name(styles: dict[str, _Style], style_id: str | None) -> str:
    style = styles.get(style_id or "")
    return style.name if style is not None else ""


_CHART_NS: Final = "{http://schemas.openxmlformats.org/drawingml/2006/chart}"
_DRAWING_NS: Final = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_R_ID: Final = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
MAX_CHART_POINTS: Final = 200


def _chart_ids(element: Element) -> list[str]:
    """Les graphiques ancrés dans un paragraphe : l'identifiant de relation de chacun."""
    return [found.get(_R_ID, "") for found in element.iter(f"{_CHART_NS}chart") if found.get(_R_ID)]


def _chart(package: _Package, relation: str) -> dict[str, JsonValue]:
    """Un graphique Word, rendu dans le vocabulaire de l'écriture, d'après son cache.

    Un graphique qu'on ne sait pas lire (partie absente, type inconnu) reste un bloc
    ``graphique`` sans données : sa place dans le document est connue.
    """
    block: dict[str, JsonValue] = {"type": "chart"}
    name = package.charts.get(relation)
    root = _whole(package.archive, name, "chart") if name else None
    if root is None:
        return block
    plot = root.find(f"{_CHART_NS}chart/{_CHART_NS}plotArea")
    kinds = {
        "barChart": "bar",
        "bar3DChart": "bar",
        "lineChart": "line",
        "line3DChart": "line",
        "pieChart": "pie",
        "pie3DChart": "pie",
        "doughnutChart": "pie",
    }
    children: list[Element] = list(plot) if plot is not None else []
    found = next((child for child in children if child.tag.endswith("Chart")), None)
    if found is None:
        return block
    local = found.tag.rpartition("}")[2]
    kind = kinds.get(local, local)
    direction = found.find(f"{_CHART_NS}barDir")
    if kind == "bar" and direction is not None and direction.get("val") == "bar":
        kind = "horizontal_bar"
    block["kind"] = kind
    title = root.find(f"{_CHART_NS}chart/{_CHART_NS}title")
    if title is not None:
        said = "".join(t.text or "" for t in title.iter(f"{_DRAWING_NS}t")).strip()
        if said:
            block["title"] = said[:MAX_CELL_CHARS]
    categories: list[JsonValue] = []
    series: list[JsonValue] = []
    for ser in found.findall(f"{_CHART_NS}ser"):
        cat = ser.find(f"{_CHART_NS}cat")
        if not categories and cat is not None:
            categories = [str(v) for v in _points(cat)]
        name_at = ser.find(f"{_CHART_NS}tx")
        series_name = (
            "".join(v.text or "" for v in name_at.iter(f"{_CHART_NS}v"))
            if name_at is not None
            else ""
        )
        val = ser.find(f"{_CHART_NS}val")
        values = [_float(v) for v in _points(val)] if val is not None else []
        series.append({"name": series_name[:MAX_CELL_CHARS], "values": values})
    block["categories"] = categories
    block["series"] = series
    return block


def _points(source: Element) -> list[str | None]:
    """Les points d'un cache (``strCache``, ``numCache``) ou d'un littéral, à leur rang."""
    count = 0
    for counted in source.iter(f"{_CHART_NS}ptCount"):
        value = counted.get("val", "0")
        count = min(int(value) if value.isdigit() else 0, MAX_CHART_POINTS)
        break
    found: list[str | None] = [None] * count
    for point in source.iter(f"{_CHART_NS}pt"):
        index = point.get("idx", "")
        text = point.findtext(f"{_CHART_NS}v")
        if index.isdigit() and int(index) < count:
            found[int(index)] = text
    return found


def _float(value: str | None) -> JsonValue:
    try:
        number = float(value) if value is not None else None
    except ValueError:
        return None
    if number is None or number != number or number in (float("inf"), float("-inf")):
        return None
    return int(number) if number.is_integer() and abs(number) < 2**53 else number


_WP_NS: Final = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"
_PIC_NS: Final = "{http://schemas.openxmlformats.org/drawingml/2006/picture}"
_EMBED: Final = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed"
_EMU: Final = 360


def _pictures(package: _Package, element: Element) -> list[dict[str, JsonValue]]:
    """Les images d'un paragraphe, dans le vocabulaire de l'écriture.

    ``path`` est l'adresse du fichier que l'outil a lu à l'écriture (le nom du dessin) ;
    une image insérée ailleurs n'en a pas. ``width`` est celle des trois qui donne la
    taille lue, d'après les pixels de l'image ; la plus proche sinon.
    """
    found: list[dict[str, JsonValue]] = []
    for drawing in element.iter():
        if _local(drawing.tag) != "drawing":
            continue
        for frame in drawing:
            if frame.tag not in {f"{_WP_NS}inline", f"{_WP_NS}anchor"}:
                continue
            blip = next(frame.iter(f"{_DRAWING_NS}blip"), None)
            if blip is None or next(frame.iter(f"{_PIC_NS}pic"), None) is None:
                continue
            properties = frame.find(f"{_WP_NS}docPr")
            name = (properties.get("name") if properties is not None else None) or ""
            alt = (properties.get("descr") if properties is not None else None) or ""
            block: dict[str, JsonValue] = {"type": "image"}
            if "/" in name and not name.startswith("/"):
                block["path"] = name[:MAX_CELL_CHARS]
            block["width"] = _width(package, frame, blip.get(_EMBED, ""))
            if alt.strip():
                block["alt"] = alt.strip()[:MAX_CELL_CHARS]
            found.append(block)
    return found


def _width(package: _Package, frame: Element, relation: str) -> ImageWidth:
    extent = frame.find(f"{_WP_NS}extent")
    shown = _float(extent.get("cx") if extent is not None else None)
    width = (shown if isinstance(shown, int | float) else 0) / _EMU
    part = package.media.get(relation)
    pixels: tuple[int, int] | None = None
    if part is not None:
        try:
            info = package.archive.getinfo(part)
            if info.file_size <= MAX_IMAGE_BYTES:
                pixels = dimensions(package.archive.read(info))
        except KeyError:
            pixels = None
    if pixels is not None:
        for wanted in IMAGE_WIDTHS:
            if abs(placed(wanted, *pixels)[0] - width) <= 2:
                return wanted
    return min(IMAGE_WIDTHS, key=lambda wanted: abs(IMAGE_WIDTHS[wanted] - width))


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
    # Un style qui annule la numérotation (``numId 0``) arrête la recherche : celle d'un
    # style parent ne s'applique plus (B-4).
    cancelled = False
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
        if not cancelled:
            listed = listed or style.num_id
            cancelled = listed is None and style.numbering_off
        current = style.based_on
    return title, outline, listed


def _segments(element: Element, inline: _Inline | None = None) -> list[_Segment]:
    """Le texte d'un paragraphe, en segments (texte, gras, italique, souligné), dans
    l'ordre. Avec ``inline`` : un lien, une note, un renvoi en balisage (``raw``) ; sans,
    le texte qu'ils affichent (une note, rien)."""
    found: list[_Segment] = []
    # Les champs complexes ouverts (``fldChar``) : leur instruction, et ce qu'affiche un
    # renvoi (``REF``), qui ne revient pas tel quel.
    fields: list[_Field] = []

    def emit(segment: _Segment, out: list[_Segment]) -> None:
        showing = next((f for f in reversed(fields) if f.result), None)
        if showing is not None and _ref_target(showing.instr) is not None:
            showing.shown.append(segment[0])
        else:
            out.append(segment)

    def inner(node: Element, emphasis: tuple[bool, bool, bool]) -> list[_Segment]:
        said: list[_Segment] = []
        for child in node:
            walk(child, *emphasis, said)
        return said

    def walk(node: Element, bold: bool, italic: bool, underline: bool, out: list[_Segment]) -> None:
        name = _local(node.tag)
        if name in _SKIPPED:
            return
        match name:
            case "fldChar":
                kind = _attribute(node, "fldCharType")
                if kind == "begin":
                    fields.append(_Field())
                elif kind == "separate" and fields:
                    fields[-1].result = True
                elif kind == "end" and fields:
                    done = fields.pop()
                    target = _ref_target(done.instr)
                    if target is not None:
                        emit(_reference(target, "".join(done.shown), inline), out)
                return
            case "instrText":
                if fields and not fields[-1].result:
                    fields[-1].instr += node.text or ""
                return
            case "footnoteReference" | "endnoteReference":
                said = inline.notes.get(f"{name[0]}{_attribute(node, 'id')}") if inline else None
                if said:
                    emit((f"^[{said}]", False, False, False, True), out)
                return
            case "fldSimple":
                target = _ref_target(_attribute(node, "instr") or "")
                if target is not None:
                    shown = "".join(text for text, *_ in inner(node, (bold, italic, underline)))
                    emit(_reference(target, shown, inline), out)
                    return
            case "hyperlink" if inline is not None:
                label = inner(node, (bold, italic, underline))
                address = inline.links.get(node.get(_R_ID) or "")
                anchor = _attribute(node, "anchor")
                if address is not None and _linkable(address) and _markdown(label):
                    emit((f"[{_markdown(label)}]({address})", False, False, False, True), out)
                elif address is None and anchor:
                    shown = "".join(text for text, *_ in label)
                    emit(_reference(anchor, shown, inline), out)
                else:
                    for segment in label:
                        emit(segment, out)
                return
            case "r":
                properties = _child(node, "rPr")
                if properties is not None:
                    bold = _on(_child(properties, "b"))
                    italic = _on(_child(properties, "i"))
                    underline = _underlined(_child(properties, "u"))
            case "t":
                emit((node.text or "", bold, italic, underline, False), out)
                return
            case "tab":
                emit(("\t", bold, italic, underline, False), out)
                return
            case "br" | "cr":
                if _attribute(node, "type") != "page":
                    emit(("\n", bold, italic, underline, False), out)
                return
            case "noBreakHyphen":
                emit(("-", bold, italic, underline, False), out)
                return
            case None if node.tag.endswith("}AlternateContent"):
                # Le choix d'un logiciel récent (zone de texte, forme) : rien de lisible ici.
                return
            case _:
                pass
        for child in node:
            walk(child, bold, italic, underline, out)

    for child in element:
        walk(child, False, False, False, found)
    return found


@dataclass(slots=True)
class _Field:
    """Un champ complexe en cours : son instruction ; passé son séparateur, ce qu'il
    affiche."""

    instr: str = ""
    result: bool = False
    shown: list[str] = field(default_factory=list[str])


def _ref_target(instruction: str) -> str | None:
    """Le signet que vise un champ ``REF`` ; ``None`` pour un autre champ."""
    parts = instruction.split()
    return parts[1] if len(parts) > 1 and parts[0].upper() == "REF" else None


def _reference(target: str, shown: str, inline: _Inline | None) -> _Segment:
    """Un renvoi : en attente des titres (``_resolved``) ; sans ``inline``, son texte."""
    if inline is None:
        return (shown, False, False, False, False)
    return (f"\x00{target}\x01{shown}\x00", False, False, False, True)


def _linkable(address: str) -> bool:
    """Une adresse qu'un lien peut réécrire : https, http ou mailto."""
    scheme, colon, _ = address.partition(":")
    return bool(colon) and scheme.lower() in LINK_SCHEMES


def _underlined(element: Element | None) -> bool:
    """``<w:u w:val="single"/>`` (ou double, pointillé…) : souligné ; ``none`` : non."""
    return element is not None and _attribute(element, "val") not in (None, "none")


def _markdown(segments: list[_Segment]) -> str:
    """Les segments joints, l'emphase en ``**``, ``*`` et ``<u>`` comme à l'écriture ; un
    ``^[`` ou un ``§[`` du texte échappé, pour ne pas se relire en note ou en renvoi."""
    merged: list[_Segment] = []
    for segment in segments:
        if merged and not segment[4] and not merged[-1][4] and merged[-1][1:] == segment[1:]:
            merged[-1] = (merged[-1][0] + segment[0], *segment[1:])
        else:
            merged.append(segment)
    said: list[str] = []
    for text, bold, italic, underline, raw in merged:
        if raw:
            said.append(text)
            continue
        text = text.replace("^[", "\\^[").replace("§[", "\\§[")
        core = text.strip()
        if not core or not (bold or italic or underline):
            said.append(text)
            continue
        mark = ("**" if bold else "") + ("*" if italic else "")
        lead = text[: len(text) - len(text.lstrip())]
        tail = text[len(text.rstrip()) :]
        if underline:
            core = f"<u>{core}</u>"
        said.append(f"{lead}{mark}{core}{mark[::-1]}{tail}")
    return "".join(said).strip()


def _table(package: _Package, reading: _Reading, element: Element) -> list[JsonValue]:
    """Les lignes d'un tableau : chaque cellule en texte, son emphase, ses liens, ses notes
    et ses renvois compris ; l'emphase d'une ligne d'en-tête, ou d'une ligne toute en gras
    (un total), est celle de son style, que la réécriture repose : elle ne revient pas."""
    rows: list[JsonValue] = []
    # Le groupe en cours : ses lignes, jusqu'à son sous-total ou au groupe suivant.
    group: list[JsonValue] | None = None
    for row in element:
        if _local(row.tag) != "tr":
            continue
        kind = "" if reading.shown else _row_style(package, row)
        if kind == SUBTOTAL_STYLE:
            group = None
            continue
        tiles = [
            [
                _segments(paragraph, package.inline)
                for paragraph in cell.iter()
                if _local(paragraph.tag) == "p"
            ]
            for cell in row
            if _local(cell.tag) == "tc"
        ]
        if kind == GROUP_STYLE:
            group = []
            label = _cell_markdown(tiles[0], emphasis=True) if tiles else ""
            rows.append({"group": label, "rows": group})
            continue
        properties = _child(row, "trPr")
        plain = (properties is not None and _child(properties, "tblHeader") is not None) or (
            _all_bold([segment for tile in tiles for lines in tile for segment in lines])
        )
        texts = iter(_cell_markdown(tile, emphasis=not plain) for tile in tiles)
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
            text = next(texts)
            text = "" if merged else text
            if len(text) > MAX_CELL_CHARS:
                text = text[:MAX_CELL_CHARS] + "…"
                reading.cut += 1
            cells.append(text)
            cells.extend("" for _ in range(min(span, MAX_COLUMNS) - 1))
        (group if group is not None else rows).append(cells[:MAX_COLUMNS])
    return rows


def _all_bold(segments: list[_Segment]) -> bool:
    """Tout le texte (hors liens, notes, renvois) en gras ; et du texte."""
    shown = [segment for segment in segments if not segment[4] and segment[0].strip()]
    return bool(shown) and all(segment[1] for segment in shown)


def _cell_markdown(paragraphs: list[list[_Segment]], *, emphasis: bool) -> str:
    """Le texte d'une cellule, ses paragraphes en lignes ; sans ``emphasis``, sans
    l'emphase."""
    lines: list[str] = []
    for segments in paragraphs:
        if not emphasis:
            segments = [(text, False, False, False, raw) for text, *_, raw in segments]
        said = _markdown(segments)
        if said:
            lines.append(said)
    return "\n".join(lines)


def _cell_text(cell: Element) -> str:
    """Le texte d'une cellule : ses paragraphes, et ceux d'un tableau imbriqué, en lignes."""
    lines: list[str] = []
    for paragraph in cell.iter():
        if _local(paragraph.tag) == "p":
            said = "".join(text for text, *_ in _segments(paragraph)).strip()
            if said:
                lines.append(said)
    return "\n".join(lines)


def _list_item(
    reading: _Reading, text: str, level: int, ordered: bool, style: str | None = None
) -> None:
    pending = reading.pending
    if pending is None:
        pending = reading.pending = _styled(
            {"type": "list", "ordered": ordered, "items": []}, style
        )
    items = cast(list[JsonValue], pending["items"])
    # Une sous-liste : sous l'élément précédent, trois niveaux en tout (au-delà, aplatie
    # au troisième).
    for _ in range(min(level, 2)):
        if not items:
            break
        last = items[-1]
        if isinstance(last, str):
            last = items[-1] = {"text": last, "items": []}
        items = cast(list[JsonValue], cast(dict[str, JsonValue], last)["items"])
    items.append(text)


def _styled(block: dict[str, JsonValue], style: str | None) -> dict[str, JsonValue]:
    """Le bloc, avec le style proposé que le document lui a donné."""
    if style is not None:
        block["style"] = style
    return block


def _section_title(reading: _Reading, text: str) -> bool:
    """Un titre de niveau 1 dans une section : son titre, si c'est son nom."""
    template = reading.template
    if template is None or reading.section is None:
        return False
    section = next((s for s in template.sections if s.position == reading.section), None)
    return section is not None and key(text) == section.key


def _kind(styles: dict[str, _Style], style_id: str | None) -> str:
    """Le bloc que dit un style, en suivant ses bases : un style à toi qui part de Note est
    une note."""
    seen: set[str] = set()
    current = style_id
    while current is not None and current not in seen and len(seen) < 20:
        seen.add(current)
        style = styles.get(current)
        if style is None:
            break
        if style.name in _KIND:
            return _KIND[style.name]
        current = style.based_on
    return "paragraph"


def _close_list(reading: _Reading) -> None:
    if reading.pending is not None:
        pending, reading.pending = reading.pending, None
        _emit(reading, pending)


def _emit(
    reading: _Reading, block: dict[str, JsonValue], heading: tuple[int, str] | None = None
) -> None:
    # Une légende ne se rattache qu'à l'image qui la précède immédiatement.
    reading.last_image = None
    template = reading.template
    if template is not None and reading.section is not None:
        section = next((s for s in template.sections if s.position == reading.section), None)
        if section is not None and section.fixed:
            # Le texte fixe d'une section : l'outil l'écrit, il ne revient pas.
            return
    index = reading.count
    reading.count += 1
    if heading is not None and len(reading.outline) < MAX_OUTLINE:
        level, text = heading
        reading.outline.append({"index": index, "level": level, "text": text[:200]})
    if index < reading.start or reading.full:
        return
    size = len(json.dumps(block, ensure_ascii=False))
    if reading.blocks and reading.used + size > reading.budget:
        # Le bloc ne tient plus : il sera le premier de la suite.
        reading.full = True
        reading.next_start = index
        return
    if size > reading.budget:
        block = _shortened(block, reading.budget)
        reading.cut += 1
    reading.blocks.append(block)
    reading.where.append(reading.section)
    reading.used += size


def _shortened(block: dict[str, JsonValue], budget: int) -> dict[str, JsonValue]:
    """Un bloc seul plus grand que le budget : coupé, pour que la lecture avance."""
    said = json.dumps(block, ensure_ascii=False)
    return {"type": block.get("type"), "text": said[: budget - 200] + "…", "cut": True}


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
            f"Lecture coupée par la taille du résultat ({reading.budget} caractères) : la suite "
            f"commence au bloc {reading.next_start} (start) ; 'outline' donne le plan."
        )
    if reading.images:
        notes.append(f"{reading.images} image(s) ou forme(s) non lue(s).")
    sourceless = sum(
        1
        for block in reading.blocks
        if isinstance(block, dict) and block.get("type") == "image" and "path" not in block
    )
    if sourceless:
        notes.append(
            f"{sourceless} image(s) sans fichier source (pas de path) : pour réécrire le "
            "document, donner un path vers un fichier d'un dossier read, ou retirer le bloc."
        )
    if reading.revisions:
        notes.append(
            "Modifications suivies présentes : le texte inséré est rendu, le texte supprimé omis."
        )
    if package.comments:
        notes.append("Le document a des commentaires, non lus.")
    if reading.cut:
        notes.append(f"{reading.cut} texte(s) coupé(s).")
    return notes
