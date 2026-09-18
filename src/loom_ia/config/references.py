# SPDX-License-Identifier: Apache-2.0
"""Références vers du code Python (#50).

Deux formes, le nom d'abord :

- un **nom enregistré** : ``imports`` charge les modules listés, et les
  outils qu'ils déclarent (``@tool``) sont enregistrés sous leur nom ;
- un **chemin d'import** ``module:attr``, qui ne demande aucun ``imports``.

Les modules voisins du fichier de config sont importables : son dossier est
ajouté à ``sys.path`` le temps du chargement.
"""

import importlib
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from types import ModuleType

from loom_ia.config.errors import ConfigError
from loom_ia.core.ports import Tool


class Registry:
    """Objets Python nommés, disponibles pour la config."""

    def __init__(self, objects: Mapping[str, object] | None = None) -> None:
        self._objects: dict[str, object] = dict(objects or {})

    def add(self, name: str, obj: object, *, source: str = "") -> None:
        known = self._objects.get(name)
        if known is not None and known is not obj:
            raise ConfigError(f"Deux objets portent le nom {name!r} ({source})")
        self._objects[name] = obj

    def get(self, name: str) -> object | None:
        return self._objects.get(name)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._objects))

    def __len__(self) -> int:
        return len(self._objects)


def import_modules(names: Iterable[str], *, base_dir: Path | None = None) -> Registry:
    """Importe les modules de ``imports`` et enregistre les outils trouvés."""
    registry = Registry()
    with _importable(base_dir):
        for name in names:
            try:
                module = importlib.import_module(name)
            except ImportError as exc:
                raise ConfigError(f"Module {name!r} introuvable : {exc}") from exc
            for tool_name, tool in _tools_of(module):
                registry.add(tool_name, tool, source=f"module {name}")
    return registry


def resolve(reference: str, registry: Registry, *, base_dir: Path | None = None) -> object:
    """Objet désigné par un nom enregistré ou par ``module:attr``."""
    found = registry.get(reference)
    if found is not None:
        return found
    if ":" not in reference:
        known = ", ".join(registry.names) or "aucun"
        raise ConfigError(
            f"Référence {reference!r} introuvable : aucun objet de ce nom "
            f"(enregistrés : {known}). Utiliser 'imports' ou un chemin 'module:attr'"
        )
    module_name, _, attribute = reference.partition(":")
    with _importable(base_dir):
        try:
            module = importlib.import_module(module_name)
        except ImportError as exc:
            raise ConfigError(f"Référence {reference!r} : module introuvable ({exc})") from exc
    try:
        return getattr(module, attribute)
    except AttributeError as exc:
        raise ConfigError(
            f"Référence {reference!r} : {attribute!r} absent du module {module_name!r}"
        ) from exc


class _importable:
    """Rend un dossier importable le temps du bloc."""

    def __init__(self, base_dir: Path | None) -> None:
        self._path = str(base_dir) if base_dir is not None else None
        self._added = False

    def __enter__(self) -> None:
        if self._path is not None and self._path not in sys.path:
            sys.path.insert(0, self._path)
            self._added = True

    def __exit__(self, *exc: object) -> None:
        if self._added and self._path is not None:
            sys.path.remove(self._path)
            self._added = False


def _tools_of(module: ModuleType) -> list[tuple[str, Tool]]:
    """Outils déclarés dans un module, par leur nom."""
    return [(value.spec.name, value) for value in vars(module).values() if isinstance(value, Tool)]
