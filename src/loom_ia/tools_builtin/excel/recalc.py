# SPDX-License-Identifier: Apache-2.0
"""Recalcul par LibreOffice : vérifier qu'aucune formule n'est en erreur, sur une copie.

LibreOffice réécrirait les formats du classeur : il ne recalcule qu'une copie,
dans un dossier temporaire, et le fichier livré reste celui d'openpyxl.
"""

import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from openpyxl import load_workbook
from pydantic import JsonValue

from loom_ia.core.ports import ToolError
from loom_ia.tools_builtin.excel.arguments import MAX_ERRORS_SAID

ERRORS: Final = frozenset({"#NULL!", "#DIV/0!", "#VALUE!", "#REF!", "#NAME?", "#NUM!", "#N/A"})


def recalculate(data: bytes, formulas: Sequence[str], soffice: Path, timeout: float) -> JsonValue:
    """Fait recalculer une copie par LibreOffice ; ``ToolError`` si une formule est en erreur.

    Un profil LibreOffice neuf par appel : deux recalculs simultanés ne se
    disputent pas le verrou du profil. Le fichier d'origine n'est pas touché.
    """
    with tempfile.TemporaryDirectory(prefix="loom-excel-") as folder:
        work = Path(folder)
        source = work / "classeur.xlsx"
        source.write_bytes(data)
        command = [
            str(soffice),
            f"-env:UserInstallation={(work / 'profil').as_uri()}",
            "--headless",
            "--norestore",
            "--calc",
            "--convert-to",
            "xlsx",
            "--outdir",
            str(work / "calcul"),
            str(source),
        ]
        try:
            done = subprocess.run(
                command, capture_output=True, text=True, timeout=timeout, check=False
            )
        except subprocess.TimeoutExpired:
            raise ToolError(f"Recalcul interrompu après {timeout:g} s.") from None
        except OSError as exc:
            raise ToolError(f"LibreOffice ne démarre pas ({exc.strerror}).") from exc
        result = work / "calcul" / "classeur.xlsx"
        if done.returncode != 0 or not result.is_file():
            said = (done.stderr or done.stdout).strip().splitlines()[-1:] or ["sans message"]
            raise ToolError(f"Recalcul impossible : {said[0]}")
        computed = load_workbook(result, data_only=True)
        errors: dict[str, list[str]] = {}
        for address in formulas:
            name, _, cell = address.rpartition("!")
            value = computed[name][cell].value
            if isinstance(value, str) and value in ERRORS:
                errors.setdefault(value, []).append(address)
    count = sum(len(cells) for cells in errors.values())
    if count:
        said = "; ".join(
            f"{kind} : {', '.join(cells[:MAX_ERRORS_SAID])}" for kind, cells in errors.items()
        )
        raise ToolError(
            f"Recalcul : {count} formule(s) en erreur sur {len(formulas)} — {said}. "
            "Le classeur n'est pas écrit ; corriger ces formules (IFERROR pour un dénominateur "
            "qui peut être nul)."
        )
    return {"formulas": len(formulas), "errors": 0}


@dataclass(frozen=True, slots=True)
class Recalc:
    """Le recalcul, tel que la config le règle : l'exécutable et son délai."""

    soffice: Path
    timeout: float
