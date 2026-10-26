# SPDX-License-Identifier: Apache-2.0
"""Sources de données : les lignes d'un CSV ou d'un JSON, dans l'ordre des colonnes d'un onglet."""

import csv
import io
import json
from typing import cast

from loom_ia.core.ports import ToolError
from loom_ia.tools_builtin.excel.arguments import RowSpec, Scalar, SheetSpec

# --- Sources de données ----------------------------------------------------------------


def read_rows(data: bytes, suffix: str, sheet: SheetSpec, where: str) -> list[RowSpec]:
    """Les lignes d'un CSV ou d'un JSON, dans l'ordre des colonnes de l'onglet."""
    headers = [column.header for column in sheet.columns]
    match suffix:
        case ".csv":
            records = _csv_records(data, where)
        case ".json":
            records = _json_records(data, where)
        case _:
            raise ToolError(f"{where} : seuls les fichiers .csv et .json sont lus.")
    if not records:
        return []
    present: set[str] = set()
    for record in records:
        present.update(record)
    missing = [header for header in headers if header not in present]
    if missing:
        raise ToolError(
            f"{where} : colonne(s) absente(s) du fichier : {', '.join(missing)} "
            f"(trouvées : {', '.join(sorted(present))})"
        )
    return [RowSpec(cells=[record.get(header) for header in headers]) for record in records]


def _csv_records(data: bytes, where: str) -> list[dict[str, Scalar]]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ToolError(f"{where} n'est pas un fichier texte UTF-8.") from exc
    first = text.split("\n", 1)[0]
    delimiter = ";" if first.count(";") > first.count(",") else ","
    reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=delimiter)
    try:
        return [
            {str(key): value for key, value in row.items() if key is not None} for row in reader
        ]
    except csv.Error as exc:
        raise ToolError(f"{where} : CSV illisible ({exc}).") from exc


def _json_records(data: bytes, where: str) -> list[dict[str, Scalar]]:
    try:
        loaded: object = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ToolError(f"{where} : JSON illisible ({exc}).") from exc
    if not isinstance(loaded, list):
        raise ToolError(f"{where} : une liste d'objets est attendue.")
    records: list[dict[str, Scalar]] = []
    for item in cast(list[object], loaded):
        if not isinstance(item, dict):
            raise ToolError(f"{where} : une liste d'objets est attendue.")
        record: dict[str, Scalar] = {}
        for key, value in cast(dict[object, object], item).items():
            if not isinstance(value, str | int | float | bool | None):
                raise ToolError(f"{where} : valeur non scalaire pour {key!r}.")
            record[str(key)] = value
        records.append(record)
    return records
