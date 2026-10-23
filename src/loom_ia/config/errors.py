# SPDX-License-Identifier: Apache-2.0
"""Erreurs de configuration, lisibles au démarrage (M1).

Le message nomme le fichier et l'endroit dans le YAML :
``agents/demo.yaml: main.model — modèle 'X' non déclaré``. Quand la
validation trouve plusieurs erreurs, elles viennent une par ligne, et seule la
première porte le nom du fichier.
"""

from pathlib import Path

from pydantic import ValidationError


class ConfigError(Exception):
    """Configuration inutilisable : le run ne démarre pas."""

    def __init__(self, message: str, *, source: Path | None = None) -> None:
        super().__init__(f"{source}: {message}" if source else message)
        self.reason = message
        self.source = source


def from_validation(
    error: ValidationError, *, source: Path | None = None, root: str | None = "(racine)"
) -> ConfigError:
    """Traduit les erreurs Pydantic en une erreur lisible.

    ``root`` nomme l'emplacement d'une erreur sans chemin (un contrôle de tout
    l'objet) ; ``None`` la laisse sans préfixe — pour un agent rapporté à son
    fichier, dont le message nomme déjà l'agent.
    """
    lines: list[str] = []
    for item in error.errors(include_url=False):
        location = ".".join(str(part) for part in item["loc"]) or root
        message = item["msg"].removeprefix("Value error, ")
        lines.append(f"{location} — {message}" if location else message)
    return ConfigError("\n".join(lines), source=source)
