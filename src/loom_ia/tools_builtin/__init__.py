# SPDX-License-Identifier: Apache-2.0
"""Outils fournis par loom-ia, référencés par leur nom ``loom.…`` ; aucun n'est actif d'office.

Un agent en active un en le listant dans ses outils : ``tools: [{python: loom.<outil>}]``.
Le modèle le voit sous son nom sans le préfixe ; les réglages de l'agent (``timeout``,
``approval``…) s'y appliquent comme à tout outil Python.

Un module par outil, au nom de l'outil. ``BUILTIN_TOOLS`` les recense sans les
importer : le module d'un outil, et ses dépendances, ne sont chargés que lorsqu'un
agent l'active.

Un outil peut avoir des **paramètres**, donnés sur sa référence, à côté des
réglages communs (``- python: loom.local_files`` puis ``folders: …``). Son
entrée (``Builtin``) nomme alors deux choses : le modèle Pydantic de ses
paramètres, dans un module léger — Pydantic et le noyau seulement —, que la
config importe pour valider la référence au chargement ; et la fabrique de
l'outil, qui reçoit les paramètres validés et n'est importée qu'au montage.
Un paramètre ne porte jamais le nom d'un réglage commun.
"""

import importlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol, cast, runtime_checkable

from pydantic import BaseModel

from loom_ia.core.model import RESERVED_PREFIX
from loom_ia.core.ports import Tool


@dataclass(frozen=True, slots=True)
class Builtin:
    """Un outil fourni : où le trouver, et ses paramètres s'il en a."""

    # ``module:attr`` : l'outil lui-même ; avec ``params``, sa fabrique (``BuiltinFactory``).
    target: str
    # ``module:Modèle`` : modèle Pydantic des paramètres, dans un module léger.
    params: str | None = None
    # Réglages communs que l'outil refuse sur sa référence, et pourquoi : ses
    # paramètres les expriment déjà, et deux endroits pour une même décision
    # finiraient par se contredire.
    refused: tuple[str, ...] = ()
    refused_because: str = ""


@dataclass(frozen=True, slots=True)
class Protected:
    """Un chemin de loom qu'un outil fourni ne doit jamais exposer au modèle."""

    path: Path
    # Ce que c'est, pour le message : « la config », « le journal »…
    what: str
    # Vrai pour un dossier dont aucun sous-dossier ne peut être exposé non plus ;
    # faux pour un fichier, ou pour le dossier de la config, dont on peut
    # exposer un sous-dossier qui ne contient rien de protégé.
    subtree: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class BuiltinContext:
    """Ce que reçoit la fabrique d'un outil fourni, avec ses paramètres."""

    # Dossier du fichier de config : les chemins relatifs des paramètres s'y rapportent.
    base_dir: Path | None = None
    # Config, fichier d'environnement, prompts, stockage… du client monté.
    protected: tuple[Protected, ...] = ()


@runtime_checkable
class ShowsSettings(Protocol):
    """Outil fourni qui dit ses réglages à ``loom validate``, une ligne par réglage."""

    def settings_lines(self) -> tuple[str, ...]: ...


class BuiltinFactory(Protocol):
    """Fabrique d'un outil fourni à paramètres ; ``ValueError`` dit ce qui ne va pas."""

    def __call__(self, params: BaseModel, context: BuiltinContext, /) -> Tool: ...


# Référence ``loom.<outil>`` → ``module:attr`` de l'outil, ou ``Builtin``.
BUILTIN_TOOLS: Final[dict[str, str | Builtin]] = {
    "loom.current_datetime": "loom_ia.tools_builtin.current_datetime:current_datetime",
    "loom.fetch_page": "loom_ia.tools_builtin.fetch_page:fetch_page",
    "loom.local_files": Builtin(
        target="loom_ia.tools_builtin.local_files:build",
        params="loom_ia.tools_builtin.local_files_params:LocalFilesParams",
        refused=("approval", "side_effects", "idempotent"),
        refused_because="les droits de chaque dossier les expriment (rights)",
    ),
}


def is_builtin(reference: str) -> bool:
    """Vrai si la référence désigne un outil fourni : ``loom.<outil>``.

    ``loom.x:y`` reste un chemin d'import : celui d'un paquet à soi nommé ``loom``.
    """
    return reference.startswith(RESERVED_PREFIX) and ":" not in reference


def builtin_entry(reference: str) -> Builtin:
    """L'entrée du catalogue ; ``KeyError`` si la référence n'y est pas."""
    entry = BUILTIN_TOOLS[reference]
    return Builtin(target=entry) if isinstance(entry, str) else entry


def builtin_params(reference: str) -> type[BaseModel] | None:
    """Le modèle des paramètres de l'outil, ou ``None`` s'il n'en a pas.

    ``KeyError`` s'il n'est pas au catalogue, ``ImportError`` si son module manque.
    """
    entry = builtin_entry(reference)
    if entry.params is None:
        return None
    found = _load(entry.params)
    if not (isinstance(found, type) and issubclass(found, BaseModel)):
        raise TypeError(f"{reference} : {entry.params} n'est pas un modèle Pydantic")
    return found


def parameterized_builtins() -> Mapping[str, tuple[type[BaseModel], Builtin]]:
    """Les outils fournis à paramètres, et leur modèle : seuls ces modules légers sont importés."""
    found: dict[str, tuple[type[BaseModel], Builtin]] = {}
    for reference in BUILTIN_TOOLS:
        model = builtin_params(reference)
        if model is not None:
            found[reference] = (model, builtin_entry(reference))
    return found


def builtin_tool(
    reference: str, params: BaseModel | None = None, *, context: BuiltinContext | None = None
) -> Tool:
    """L'outil fourni ``reference``, son module importé à ce moment-là.

    Un outil à paramètres est construit par sa fabrique, avec ``params``
    (validés par son modèle) et ``context``. ``KeyError`` s'il n'est pas au
    catalogue, ``ImportError`` si une dépendance de son module manque,
    ``ValueError`` si sa fabrique refuse ses paramètres.
    """
    entry = builtin_entry(reference)
    found = _load(entry.target)
    if entry.params is not None:
        if params is None:
            raise TypeError(f"{reference} : l'outil attend ses paramètres")
        if not callable(found):
            raise TypeError(f"{reference} : {entry.target} n'est pas une fabrique")
        factory = cast(BuiltinFactory, found)
        found = factory(params, context or BuiltinContext())
    elif params is not None:
        raise TypeError(f"{reference} : l'outil n'a pas de paramètres")
    if not isinstance(found, Tool):
        raise TypeError(f"{reference} : {entry.target} n'est pas un outil")
    return found


def _load(target: str) -> object:
    module_name, _, attribute = target.partition(":")
    return getattr(importlib.import_module(module_name), attribute)
