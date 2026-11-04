# SPDX-License-Identifier: Apache-2.0
"""Fichiers pour ``add_file`` : texte, Word et PDF, ramenés à du markdown.

- **Texte** (.md, .markdown, .txt) : UTF-8, tel quel. Titre : le premier titre
  « # », sinon le nom du fichier.
- **Word** (.docx, .docm, .dotx) : lu par le lecteur de ``loom.word``, qui ne
  dépend que de la bibliothèque standard. Ses blocs deviennent du markdown :
  titres en « # » selon leur niveau (ce qui fait les sections du découpage),
  listes, tableaux, citations. Titre : le paragraphe au style Titre, sinon le nom
  du fichier — jamais les propriétés du document (``dc:title``), souvent restées
  celles du modèle dont il est parti.
- **PDF** : lu par le lecteur de ``loom.pdf`` (pypdf, extra ``pdf``), dans un
  process borné en mémoire et en temps. Le texte vient page par page, sans titre
  repérable : le découpage se fait aux paragraphes et aux phrases. Un PDF scanné
  n'a pas de texte (pas d'OCR) : il est refusé. Titre : le nom du fichier.

Deux bornes : ``MAX_FILE_BYTES`` pour le fichier lu, ``MAX_TEXT_CHARS`` pour le
texte qu'on en tire — un PDF compressé grossit beaucoup, et chaque passage coûte
de l'ordre de 2 s de calcul sur CPU.

Les lecteurs de ``loom.word`` et ``loom.pdf`` ne sont importés qu'à la première
lecture d'un fichier de leur format : importer ``loom_ia.tools_builtin.pdf``
importe reportlab.
"""

import asyncio
import importlib
import importlib.util
import re
from collections.abc import Mapping, Sequence
from importlib.metadata import version
from pathlib import Path
from typing import Any, Final, cast

from pydantic import JsonValue

from loom_ia.core.ports import ToolError
from loom_ia.tools_builtin.memory.service import Extracted
from loom_ia.tools_builtin.pdf_params import SAFE_PYPDF

TEXT_SUFFIXES: Final = (".md", ".markdown", ".txt")
# Ceux que lit ``word.reader`` (``READABLE``).
WORD_SUFFIXES: Final = (".docx", ".docm", ".dotx")
PDF_SUFFIXES: Final = (".pdf",)
# Le fichier lu : la borne des fichiers de ``loom.word`` et ``loom.pdf``.
MAX_FILE_BYTES: Final = 20_000_000
# Le texte qu'on en tire : environ 300 passages, de 7 à 10 minutes de calcul sur CPU.
MAX_TEXT_CHARS: Final = 300_000
PDF_EXTRA: Final = "pdf"
# Un caractère UTF-8 tient en 4 octets au plus : un texte plus gros dépasse forcément.
MAX_TEXT_BYTES: Final = 4 * MAX_TEXT_CHARS
_H1: Final = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
_EMPHASIS: Final = re.compile(r"\*{1,2}|_{2}")


def unreadable() -> dict[str, str]:
    """Les formats que ce process ne sait pas lire, et pourquoi : ``{extension: raison}``."""
    refused: dict[str, str] = {}
    problem = _pdf_problem()
    if problem is not None:
        refused.update(dict.fromkeys(PDF_SUFFIXES, problem))
    problem = _word_problem()
    if problem is not None:
        refused.update(dict.fromkeys(WORD_SUFFIXES, problem))
    return refused


def _pdf_problem() -> str | None:
    for module in ("pypdf", "reportlab"):
        if importlib.util.find_spec(module) is None:
            return f"la lecture des .pdf demande l'extra loom-ia[{PDF_EXTRA}] ({module} absent)"
    found = tuple(int(part) for part in version("pypdf").split(".")[:3] if part.isdigit())
    if found < SAFE_PYPDF:
        return (
            f"pypdf {version('pypdf')} : {'.'.join(map(str, SAFE_PYPDF))} ou plus attendu pour "
            f"lire un .pdf (boucles sans fin sur des PDF forgés) — mettre à jour "
            f"loom-ia[{PDF_EXTRA}]"
        )
    return None


def _word_problem() -> str | None:
    reader: Any = importlib.import_module("loom_ia.tools_builtin.word.reader")
    problem = reader.xml_safety()
    return None if problem is None else f"lecture des documents Word impossible ici : {problem}"


def text_title(text: str, path: Path) -> str:
    """Le titre d'un fichier texte : son premier titre « # », sinon le nom du fichier."""
    heading = _H1.search(text)
    return heading.group(1).strip() if heading else path.stem


async def extract(data: bytes, path: Path, address: str) -> Extracted:
    """Le titre et le texte markdown du fichier ; ``ToolError`` qui dit pourquoi sinon."""
    suffix = path.suffix.lower()
    if suffix in WORD_SUFFIXES:
        extracted = await asyncio.to_thread(_from_word, data, path, address)
    elif suffix in PDF_SUFFIXES:
        extracted = await _from_pdf(data, path, address)
    else:
        try:
            text = data.decode("utf-8").strip()
        except UnicodeDecodeError as exc:
            raise ToolError(f"{address} n'est pas un fichier texte UTF-8.") from exc
        extracted = Extracted(title=text_title(text, path), text=text)
    if not extracted.text:
        raise ToolError(f"{address} est vide.")
    if len(extracted.text) > MAX_TEXT_CHARS:
        raise ToolError(_too_long(address))
    return extracted


def _too_long(address: str) -> str:
    return (
        f"{address} : plus de {MAX_TEXT_CHARS} caractères de texte, la borne d'un document "
        "de la mémoire ; le découper en plusieurs fichiers."
    )


# --- Word ------------------------------------------------------------------------------


def _from_word(data: bytes, path: Path, address: str) -> Extracted:
    reader: Any = importlib.import_module("loom_ia.tools_builtin.word.reader")
    found = cast(dict[str, JsonValue], reader.read(data, address, 0, MAX_TEXT_CHARS))
    blocks = cast(list[JsonValue], found["blocks"])
    # Coupé à la borne : la suite manque (``truncated``), ou un bloc seul l'a dépassée.
    if found.get("truncated") or any(cast(dict[str, JsonValue], b).get("cut") for b in blocks):
        raise ToolError(_too_long(address))
    title, text = word_markdown(blocks)
    return Extracted(title=title or path.stem, text=text)


def word_markdown(blocks: Sequence[JsonValue]) -> tuple[str | None, str]:
    """Les blocs lus d'un .docx en markdown, et le titre du document (style Titre)."""
    title: str | None = None
    parts: list[str] = []
    for item in blocks:
        block = cast(Mapping[str, JsonValue], item)
        kind = block.get("type")
        text = str(block.get("text") or "").strip()
        match kind:
            case "titre":
                heading = _plain(text)
                level = cast(int, block.get("level", 1))
                if level == 0:
                    title = title or heading
                # Le titre du document en « # » ; les niveaux de Word un cran en dessous.
                parts.append(f"{'#' * min(level + 1, 6)} {heading}")
            case "liste":
                lines = _list_lines(
                    cast(list[JsonValue], block.get("items") or []), bool(block.get("ordered")), 0
                )
                parts.append("\n".join(lines))
            case "tableau":
                table = _table(cast(list[JsonValue], block.get("rows") or []))
                if table:
                    parts.append(table)
            case "citation":
                parts.append("\n".join(f"> {line}" for line in text.splitlines()))
            case _:
                # Paragraphe, note, bloc coupé ; un graphique n'a pas de texte.
                if text:
                    parts.append(text)
    return title, "\n\n".join(part for part in parts if part.strip())


def _plain(text: str) -> str:
    """Un titre sans son emphase : « **Offre** » ferait un chemin de titres illisible."""
    return " ".join(_EMPHASIS.sub("", text).split())


def _list_lines(items: Sequence[JsonValue], ordered: bool, depth: int) -> list[str]:
    lines: list[str] = []
    indent = "   " * depth
    for number, item in enumerate(items, start=1):
        marker = f"{number}." if ordered else "-"
        if isinstance(item, dict):
            nested = cast(Mapping[str, JsonValue], item)
            lines.append(f"{indent}{marker} {nested.get('text') or ''}")
            lines.extend(
                _list_lines(cast(list[JsonValue], nested.get("items") or []), False, depth + 1)
            )
        else:
            lines.append(f"{indent}{marker} {item}")
    return lines


def _table(rows: Sequence[JsonValue]) -> str:
    cells = [[_cell(value) for value in cast(list[JsonValue], row)] for row in rows]
    cells = [row for row in cells if any(row)]
    if not cells:
        return ""
    width = max(len(row) for row in cells)
    padded = [row + [""] * (width - len(row)) for row in cells]
    lines = [f"| {' | '.join(row)} |" for row in padded]
    lines.insert(1, f"|{'---|' * width}")
    return "\n".join(lines)


def _cell(value: JsonValue) -> str:
    return " ".join(str(value or "").split()).replace("|", "\\|")


# --- PDF -------------------------------------------------------------------------------


async def _from_pdf(data: bytes, path: Path, address: str) -> Extracted:
    reader: Any = importlib.import_module("loom_ia.tools_builtin.pdf.reader")
    found = cast(dict[str, JsonValue], await reader.read(data, address, None, MAX_TEXT_CHARS))
    notes = cast(list[str], found.get("notes") or [])
    # Coupé à la borne : la suite manque (``truncated``), ou une page seule l'a dépassée.
    if found.get("truncated") or reader.PAGE_CUT in notes:
        raise ToolError(_too_long(address))
    pages = cast(list[dict[str, JsonValue]], found["pages"])
    text = "\n\n".join(str(page["text"]).strip() for page in pages if page.get("text"))
    if not text.strip():
        raise ToolError(
            f"{address} : aucun texte dans le PDF — scanné ou fait d'images ; il n'est pas lu "
            "(pas d'OCR)."
        )
    return Extracted(title=path.stem, text=text)
