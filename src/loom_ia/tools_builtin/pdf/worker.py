# SPDX-License-Identifier: Apache-2.0
"""Lecture d'un PDF dans un process à part, borné en mémoire et en temps.

Lancé par ``reader`` — ``python -I worker.py '<demande JSON>'`` — avec les
octets du PDF sur l'entrée standard ; il écrit le résultat en JSON sur la
sortie standard. Un PDF vient de n'importe où, et pypdf a connu des boucles
sans fin et des explosions de mémoire sur des fichiers forgés : un fil
d'exécution ne s'interrompt pas, un process si. Avant d'importer pypdf, le
process se borne lui-même (mémoire adressable, temps CPU, aucun fichier
écrit) ; le parent le tue à l'échéance.

Autonome : la bibliothèque standard et pypdf seulement, rien de loom — il est
lancé par son chemin, sans importer le paquet ni ses dépendances.
"""

import io
import json
import re
import resource
import sys
from typing import Any

_BOMB = (
    "PDF refusé : un flux dépasse, une fois décompressé, les bornes de pypdf "
    "(bombe de décompression ?)."
)


def _limit(memory: int, seconds: int) -> None:
    """Les bornes du process, posées avant toute lecture du fichier."""
    for kind, value in (
        (resource.RLIMIT_AS, memory),
        (resource.RLIMIT_CPU, seconds),
        (resource.RLIMIT_FSIZE, 0),
        (resource.RLIMIT_CORE, 0),
    ):
        _, hard = resource.getrlimit(kind)
        wanted = value if hard == resource.RLIM_INFINITY else min(value, hard)
        resource.setrlimit(kind, (wanted, hard))


# Ce qu'aucun texte n'affiche : des glyphes sans correspondance (puces…), du bruit.
_CONTROL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _clean(text: str) -> str:
    lines = [line.rstrip() for line in _CONTROL.sub("", text).splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _metadata(reader: Any) -> dict[str, str]:
    found: dict[str, str] = {}
    info: Any = reader.metadata or {}
    for key, name in (("/Title", "title"), ("/Author", "author"), ("/Subject", "subject")):
        value = info.get(key)
        if value:
            found[name] = str(value)[:500]
    return found


def _fields(reader: Any, limit: int) -> dict[str, str] | None:
    """Les champs d'un formulaire et leur valeur ; ``None`` sans formulaire."""
    fields = reader.get_fields()
    if not fields:
        return None
    found: dict[str, str] = {}
    for name, field in list(fields.items())[:limit]:
        value = field.get("/V")
        found[str(name)[:200]] = "" if value is None else str(value)[:500]
    return found


def read(data: bytes, first: int, last: int | None, budget: int) -> dict[str, Any]:
    from pypdf import PdfReader
    from pypdf.errors import FileNotDecryptedError, LimitReachedError, PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        encrypted = reader.is_encrypted
        if encrypted and not reader.decrypt(""):
            return {"error": "PDF protégé par mot de passe : il n'est pas lu."}
        count = len(reader.pages)
    except FileNotDecryptedError:
        return {"error": "PDF protégé par mot de passe : il n'est pas lu."}
    except LimitReachedError:
        return {"error": _BOMB}
    except PdfReadError as exc:
        return {"error": f"PDF illisible ({exc})."}
    if count == 0:
        return {"error": "PDF sans page."}
    if first > count:
        return {"error": f"page {first} demandée, le PDF en a {count}."}
    end = count if last is None else min(last, count)
    pages: list[dict[str, Any]] = []
    empty: list[int] = []
    used = 0
    next_page: int | None = None
    cut = False
    for number in range(first, end + 1):
        try:
            text = _clean(reader.pages[number - 1].extract_text() or "")
        except LimitReachedError:
            return {"error": f"page {number} : {_BOMB}"}
        if not text:
            empty.append(number)
        size = len(json.dumps(text, ensure_ascii=False)) + 30
        if pages and used + size > budget:
            next_page = number
            break
        if size > budget:
            # Une page seule plus grande que le budget : coupée, pour que la lecture avance.
            text = text[: budget - 200] + "…"
            cut = True
        pages.append({"number": number, "text": text})
        used += size
    return {
        "page_count": count,
        "metadata": _metadata(reader),
        "encrypted": encrypted,
        "pages": pages,
        "next_page": next_page,
        "empty_pages": empty,
        "fields": _fields(reader, 100) if first == 1 else None,
        "cut": cut,
    }


def main() -> int:
    request = json.loads(sys.argv[1])
    _limit(int(request["memory"]), int(request["seconds"]))
    data = sys.stdin.buffer.read()
    try:
        result = read(data, int(request["first"]), request["last"], int(request["budget"]))
    except MemoryError:
        result = {"error": "lecture arrêtée : mémoire épuisée (PDF trop lourd ou forgé)."}
    except RecursionError:
        result = {"error": "PDF illisible (structure trop imbriquée)."}
    except Exception as exc:
        result = {"error": f"PDF illisible ({type(exc).__name__}: {str(exc)[:300]})."}
    sys.stdout.write(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
