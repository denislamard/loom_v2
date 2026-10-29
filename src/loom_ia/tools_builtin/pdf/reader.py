# SPDX-License-Identifier: Apache-2.0
"""Lire un PDF : le texte de ses pages, par un process à part (``worker``).

Le process est borné (1 Gio de mémoire adressable, le temps CPU de
l'échéance, aucun fichier écrit) et tué à l'échéance, ou quand l'appel est
annulé (délai de l'outil) : un PDF forgé ne peut ni bloquer l'agent, ni
épuiser la mémoire de loom. Le prix : environ 0,2 s de démarrage par lecture.

Le texte est celui que le PDF contient : un PDF scanné n'en a pas (pas
d'OCR), et le résultat le dit, page par page.
"""

import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Final, cast

from pydantic import JsonValue

from loom_ia.core.ports import ToolError
from loom_ia.tools_builtin._document.arguments import READ_CHARS

WORKER: Final = Path(__file__).with_name("worker.py")
READ_DEADLINE: Final = 20.0
MEMORY: Final = 1 << 30
# Où chercher l'en-tête « %PDF- » : la norme tolère des octets avant lui.
HEADER_WINDOW: Final = 1024
_PAGES: Final = re.compile(r"\s*(\d+)\s*(?:(-)\s*(\d+)?)?\s*")


def check_suffix(path: str) -> None:
    if not path.lower().endswith(".pdf"):
        raise ToolError(f"{path} : seuls les fichiers .pdf sont lus.")


def page_range(said: str | None) -> tuple[int, int | None]:
    """« 3 », « 2-5 », « 4- » : la première page et la dernière (``None`` : jusqu'au bout)."""
    if said is None:
        return 1, None
    found = _PAGES.fullmatch(said)
    if found is None:
        raise ToolError(f"pages={said!r} : écrire 3, 2-5 ou 4- (jusqu'à la fin).")
    first = int(found.group(1))
    last = None if found.group(2) and not found.group(3) else int(found.group(3) or first)
    if first < 1 or (last is not None and last < first):
        raise ToolError(f"pages={said!r} : les pages commencent à 1, dans l'ordre.")
    return first, last


async def read(data: bytes, where: str, pages: str | None) -> dict[str, JsonValue]:
    """Le texte des pages demandées, dans un budget de taille ; ``ToolError`` sinon."""
    first, last = page_range(pages)
    if b"%PDF-" not in data[:HEADER_WINDOW]:
        raise ToolError(f"{where} : pas un fichier PDF (en-tête %PDF- absent).")
    if not sys.executable:
        raise ToolError(f"{where} : lecture impossible ici (interpréteur Python introuvable).")
    request = {
        "first": first,
        "last": last,
        "budget": READ_CHARS,
        "memory": MEMORY,
        "seconds": int(READ_DEADLINE) + 1,
    }
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        str(WORKER),
        json.dumps(request),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        out, _ = await asyncio.wait_for(process.communicate(data), READ_DEADLINE)
    except TimeoutError:
        raise ToolError(
            f"{where} : lecture arrêtée après {READ_DEADLINE:g} s ; demander moins de pages, "
            "ou le PDF est forgé."
        ) from None
    finally:
        # Échéance, annulation de l'appel : le process ne survit pas à la lecture.
        if process.returncode is None:
            process.kill()
            await process.wait()
    if process.returncode != 0:
        raise ToolError(
            f"{where} : lecture arrêtée par ses bornes (mémoire ou temps) — PDF trop lourd, "
            "ou forgé."
        )
    try:
        found = cast(dict[str, JsonValue], json.loads(out))
    except ValueError:
        raise ToolError(f"{where} : lecture sans résultat lisible.") from None
    error = found.get("error")
    if isinstance(error, str):
        raise ToolError(f"{where} : {error}")
    return _shaped(found)


def _shaped(found: dict[str, JsonValue]) -> dict[str, JsonValue]:
    pages = cast(list[JsonValue], found["pages"])
    next_page = found.get("next_page")
    result: dict[str, JsonValue] = {
        "page_count": found["page_count"],
        "metadata": found["metadata"],
        "pages": pages,
        "truncated": next_page is not None,
    }
    if next_page is not None:
        result["next_page"] = next_page
    if found.get("fields") is not None:
        result["fields"] = found["fields"]
    notes: list[JsonValue] = []
    empty = cast(list[int], found.get("empty_pages") or [])
    if empty:
        listed = ", ".join(map(str, empty[:20])) + (" …" if len(empty) > 20 else "")
        notes.append(
            f"Page(s) sans texte : {listed} — page scannée ou image ; le texte n'en est pas "
            "extrait (pas d'OCR)."
        )
    if next_page is not None:
        notes.append(
            f"Lecture coupée par la taille du résultat ({READ_CHARS} caractères) : la suite "
            f"commence page {next_page} (pages={next_page}-)."
        )
    if found.get("cut"):
        notes.append("Une page plus longue que le budget a été coupée.")
    if found.get("encrypted"):
        notes.append("PDF chiffré sans mot de passe d'ouverture : lu.")
    if notes:
        result["notes"] = notes
    return result
