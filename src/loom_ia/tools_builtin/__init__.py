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
from loom_ia.core.state_extract import ModelKey


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
    # Vrai si l'outil tire sa description de ses paramètres (dossiers, droits,
    # tables) : ``description`` l'effacerait, et le modèle ne saurait plus où
    # agir. Du texte s'ajoute avec ``skill``.
    own_description: bool = False
    # L'extra de loom-ia qui apporte ses dépendances, s'il en faut un : le dire
    # quand le montage échoue faute d'elles.
    extra: str | None = None


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
    # Table des secrets du client pour qui l'agent est monté, en lecture seule —
    # celle que reçoivent les fabriques de ``tool_sources`` ; ``None`` : l'environnement.
    secrets: Mapping[str, str] | None = None
    # Variables des DSN du stockage de loom (journal, idempotence) : une base
    # qu'un outil fourni expose ne doit pas être celle-là.
    protected_dsns: tuple[str, ...] = ()
    # Clés du state que le modèle tient à jour (``writers: [model]``), pour
    # ``loom.state`` ; et la taille permise d'une valeur (``sessions.state``).
    state_keys: tuple[ModelKey, ...] = ()
    state_max_value_bytes: int = 1024


@dataclass(frozen=True, slots=True)
class Grant:
    """Un droit accordé, pour ``loom validate`` : s'il attend une approbation, s'il détruit."""

    right: str
    approval: bool
    destructive: bool = False

    @property
    def unguarded(self) -> bool:
        """Destructif et sans approbation : un choix légitime, qui doit se voir."""
        return self.destructive and not self.approval


@dataclass(frozen=True, slots=True)
class Place:
    """Un dossier ou une base qu'expose un outil fourni : où, ce qu'on y peut, ses bornes.

    Un dossier a ses droits sous la cible ``""`` ; une base, table par table.
    ``path`` est montré relatif au dossier de la config quand il s'y trouve.
    """

    name: str
    # « sqlite », « postgres » ; vide pour un dossier.
    kind: str = ""
    path: Path | None = None
    # Dit après l'emplacement, sur la même ligne (variable du DSN, schéma).
    notes: tuple[str, ...] = ()
    grants: tuple[tuple[str, tuple[Grant, ...]], ...] = ()
    # L'ordre où dire les droits (les colonnes, pour une base).
    rights: tuple[str, ...] = ()
    # Une ligne chacune (lignes lues au plus, délai…).
    limits: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Settings:
    """Ce qu'un outil fourni montre à ``loom validate`` ; la CLI en fait la mise en page.

    ``rows`` : un réglage par ligne, alignés (les clés du state) ; ``options`` :
    des réglages courts, dits sur une ligne (fuseau, recalcul…).
    """

    places: tuple[Place, ...] = ()
    rows: tuple[tuple[str, str], ...] = ()
    options: tuple[tuple[str, str], ...] = ()


@runtime_checkable
class ShowsSettings(Protocol):
    """Outil fourni qui dit ses réglages à ``loom validate``."""

    def settings(self) -> Settings: ...


@runtime_checkable
class NamesSecrets(Protocol):
    """Paramètres d'un outil fourni qui lisent des secrets (``dsn_env``…).

    Un client peut rediriger ces noms vers ses variables (``secrets``), et sa
    table de secrets les contient : sans cela, elle ne garderait que ce que
    ``loom.yaml`` nomme.
    """

    def secret_names(self) -> tuple[str, ...]: ...


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
        refused_because=(
            "les droits de chaque dossier les expriment (folders[].rights), et "
            "l'approbation se règle par dossier (folders[].approval)"
        ),
        own_description=True,
    ),
    "loom.excel": Builtin(
        target="loom_ia.tools_builtin.excel:build",
        params="loom_ia.tools_builtin.excel_params:ExcelParams",
        refused=("approval", "side_effects", "idempotent"),
        refused_because=(
            "les droits de chaque dossier les expriment (folders[].rights), et "
            "l'approbation se règle par dossier (folders[].approval)"
        ),
        extra="office",
        own_description=True,
    ),
    "loom.word": Builtin(
        target="loom_ia.tools_builtin.word:build",
        params="loom_ia.tools_builtin.word_params:WordParams",
        refused=("approval", "side_effects", "idempotent"),
        refused_because=(
            "les droits de chaque dossier les expriment (folders[].rights), et "
            "l'approbation se règle par dossier (folders[].approval)"
        ),
        own_description=True,
    ),
    "loom.pdf": Builtin(
        target="loom_ia.tools_builtin.pdf:build",
        params="loom_ia.tools_builtin.pdf_params:PdfParams",
        refused=("approval", "side_effects", "idempotent"),
        refused_because=(
            "les droits de chaque dossier les expriment (folders[].rights), et "
            "l'approbation se règle par dossier (folders[].approval)"
        ),
        extra="pdf",
        own_description=True,
    ),
    "loom.sql": Builtin(
        target="loom_ia.tools_builtin.sql:build",
        params="loom_ia.tools_builtin.sql_params:SqlParams",
        refused=("approval", "side_effects", "idempotent"),
        refused_because=(
            "les droits de chaque table les expriment (tables), et l'approbation se règle "
            "par table (tables.<table>.approval)"
        ),
        extra="sql",
        own_description=True,
    ),
    "loom.state": Builtin(
        target="loom_ia.tools_builtin.state:build",
        params="loom_ia.tools_builtin.state_params:StateParams",
        refused=("approval", "side_effects", "idempotent"),
        refused_because=(
            "l'outil n'agit que sur le state de la session, sans effet hors de loom ; "
            "qui peut écrire chaque clé se règle sur la clé (state.<clé>.writers)"
        ),
        own_description=True,
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
