# SPDX-License-Identifier: Apache-2.0
"""Erreurs de configuration, lisibles au démarrage (M1).

Le message nomme le fichier et l'endroit dans le YAML :
``agents/demo.yaml: main.model — modèle 'X' non déclaré``.
"""

from pathlib import Path

from pydantic import ValidationError


class ConfigError(Exception):
    """Configuration inutilisable : le run ne démarre pas."""

    def __init__(self, message: str, *, source: Path | None = None) -> None:
        super().__init__(f"{source}: {message}" if source else message)
        self.reason = message
        self.source = source


def from_validation(error: ValidationError, *, source: Path | None = None) -> ConfigError:
    """Traduit les erreurs Pydantic en une erreur lisible."""
    lines: list[str] = []
    for item in error.errors(include_url=False):
        location = ".".join(str(part) for part in item["loc"]) or "(racine)"
        lines.append(f"{location} — {item['msg'].removeprefix('Value error, ')}")
    return ConfigError("\n".join(lines), source=source)
