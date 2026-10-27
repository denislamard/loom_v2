# SPDX-License-Identifier: Apache-2.0
"""Outil local_files : lire, lister, écrire et supprimer des fichiers dans des dossiers.

Les dossiers, leurs droits et l'approbation de chaque droit viennent de la
config (``local_files_params``). Le modèle désigne un fichier par
``<dossier>/<chemin>`` : il ne voit jamais un chemin réel, et ne nomme que
les dossiers déclarés.

Les règles des dossiers — chemin tel qu'écrit pour juger droits et
approbation, chemin résolu qui doit rester dans son dossier, écriture
atomique — sont dans ``_folders``, partagé avec ``loom.excel`` ; sa
docstring dit aussi la course connue.

``write`` écrit du texte (``content``), ou copie un fichier que l'agent a vu
passer (``file``) : une pièce jointe, un fichier rendu par un outil — un
outil forgé, un sous-agent. Le fichier est relu dans le stockage d'artefacts
par son nom, parmi ceux de la conversation seulement (``ToolContext.files``).
"""

import asyncio
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Final, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from loom_ia.core.model import SideEffects, ToolOutput, ToolSpec
from loom_ia.core.ports import CallAssessment, ToolContext, ToolError
from loom_ia.tools import to_output
from loom_ia.tools_builtin import BuiltinContext
from loom_ia.tools_builtin._folders import TEMP_PREFIX, Folder, Folders, Located, mount, write_bytes
from loom_ia.tools_builtin.local_files_params import LocalFilesParams, Right

# TEMP_PREFIX, préfixe des fichiers temporaires d'une écriture, reste importable d'ici.
__all__ = [
    "MAX_COPY_BYTES",
    "MAX_READ_BYTES",
    "MAX_WRITE_BYTES",
    "NAME",
    "TEMP_PREFIX",
    "LocalFilesTool",
    "build",
]
NAME: Final = "local_files"
MAX_READ_BYTES: Final = 1_000_000
MAX_WRITE_BYTES: Final = 1_000_000
MAX_ENTRIES: Final = 500
# Copie d'un fichier de la conversation (write avec file) : binaire permis.
MAX_COPY_BYTES: Final = 16 << 20

type Action = Literal["list", "read", "write", "delete"]

_SAID: Final[Mapping[Action, str]] = {
    "list": "lister",
    "read": "lire",
    "write": "écrire",
    "delete": "supprimer",
}


class _Arguments(BaseModel):
    """Arguments d'un appel : ceux que voit le modèle."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action: Annotated[
        Action,
        Field(
            description="list : contenu d'un dossier ; read : lire un fichier ; write : écrire "
            "un fichier ; delete : supprimer un fichier."
        ),
    ]
    path: Annotated[
        str,
        Field(
            description="« <dossier>/<chemin> », par exemple work/devis/D-042.txt ; "
            "pour list, « <dossier> » seul est permis."
        ),
    ]
    content: Annotated[
        str | None, Field(description="Texte à écrire, en UTF-8 ; pour write seulement.")
    ] = None
    file: Annotated[
        str | None,
        Field(
            description="Pour write, à la place de content : nom d'un fichier de la "
            "conversation (joint, ou rendu par un outil), copié tel quel."
        ),
    ] = None
    overwrite: Annotated[
        bool,
        Field(description="Pour write : remplacer le fichier s'il existe déjà (droit overwrite)."),
    ] = False


def _needs(request: _Arguments) -> tuple[Right, ...]:
    match request.action:
        case "list" | "read":
            return ("read",)
        case "write":
            return ("write", "overwrite") if request.overwrite else ("write",)
        case "delete":
            return ("delete",)


def _effects(request: _Arguments) -> tuple[SideEffects, bool]:
    """Effets de l'appel, et s'il est idempotent : réécrire le même contenu ne change rien."""
    match request.action:
        case "list" | "read":
            return "none", False
        case "write":
            return ("irreversible" if request.overwrite else "reversible"), True
        case "delete":
            return "irreversible", False


class LocalFilesTool:
    """L'outil ``local_files`` monté avec ses dossiers."""

    def __init__(self, folders: tuple[Folder, ...]) -> None:
        if not folders:
            raise ValueError("au moins un dossier est attendu")
        self.folders = folders
        self._space = Folders(folders)
        granted = {right for folder in folders for right in folder.rights}
        effects: SideEffects = (
            "irreversible"
            if granted & {"overwrite", "delete"}
            else "reversible"
            if "write" in granted
            else "none"
        )
        schema = _Arguments.model_json_schema()
        schema.pop("title", None)
        self._spec = ToolSpec(
            name=NAME,
            description=_description(self._space),
            input_schema=cast(dict[str, JsonValue], schema),
            kind="python",
            # Le pire des cas, pour ce qui ne regarde pas l'appel lui-même (la garde
            # du journal durable, l'affichage) ; chaque appel est évalué à part.
            side_effects=effects,
            approval="always" if any(folder.asking() for folder in folders) else "never",
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def settings_lines(self) -> tuple[str, ...]:
        """Une ligne par dossier, pour ``loom validate``."""
        return self._space.settings_lines()

    def assess_call(self, arguments: Mapping[str, JsonValue]) -> CallAssessment:
        """Approbation et effets de cet appel, d'après ses arguments et les droits du dossier."""
        try:
            request = _parse(arguments)
            located = self._locate(request)
            self._check_rights(request, located)
        except ToolError:
            # L'appel sera refusé à l'exécution, pour la même raison : aucun effet.
            # (Le moteur évalue les arguments résolus et définitifs ; le rejeu, qui
            # juge avant, ne laisse l'évaluation que durcir la déclaration.)
            return CallAssessment(side_effects="none")
        effects, idempotent = _effects(request)
        asking = Folders.asked(located, _needs(request))
        if not asking:
            return CallAssessment(side_effects=effects, idempotent=idempotent)
        return CallAssessment(
            approval="always",
            side_effects=effects,
            idempotent=idempotent,
            reason=(
                f"{_SAID[request.action]} {located.address} : le dossier {located.folder.name} "
                f"demande une approbation pour {', '.join(asking)}"
            ),
        )

    async def invoke(self, arguments: dict[str, JsonValue], context: ToolContext) -> ToolOutput:
        request = _parse(arguments)
        located = self._locate(request)
        self._check_rights(request, located)
        if request.file is not None:
            data = await _copied(request.file, context)
            result = await asyncio.to_thread(self._copy, data, located, request.overwrite)
            return to_output(result)
        # Lire et écrire sur le disque bloque : hors de la boucle asyncio.
        result = await asyncio.to_thread(self._perform, request, located)
        return to_output(result)

    # --- Chemins et droits ------------------------------------------------------

    def _locate(self, request: _Arguments) -> Located:
        """Le dossier qui gouverne le chemin, tel qu'écrit : ni disque, ni lien suivi."""
        return self._space.locate(request.path, folder_allowed=request.action == "list")

    def _check_rights(self, request: _Arguments, located: Located) -> None:
        if request.action == "write" and (request.content is None) == (request.file is None):
            raise ToolError(
                "write attend 'content', le texte à écrire, ou 'file', un fichier de la "
                "conversation à copier — l'un des deux."
            )
        if request.action != "write" and request.content is not None:
            raise ToolError("'content' ne sert qu'à write.")
        if request.action != "write" and request.file is not None:
            raise ToolError("'file' ne sert qu'à write.")
        if request.action != "write" and request.overwrite:
            raise ToolError("'overwrite' ne sert qu'à write.")
        Folders.require(located, _needs(request))

    # --- Opérations ---------------------------------------------------------------

    def _copy(self, data: bytes, located: Located, overwrite: bool) -> dict[str, JsonValue]:
        try:
            return write_bytes(self._space.resolved(located), located, data, overwrite)
        except OSError as exc:
            reason = exc.strerror or type(exc).__name__
            raise ToolError(f"{located.address} : {reason}.") from exc

    def _perform(self, request: _Arguments, located: Located) -> dict[str, JsonValue]:
        try:
            if request.action == "delete" and located.lexical.is_symlink():
                return self._unlinked(located)
            target = self._space.resolved(located)
            match request.action:
                case "list":
                    return self._listed(target, located)
                case "read":
                    return _read(target, located)
                case "write":
                    return _written(target, located, cast(str, request.content), request.overwrite)
                case "delete":
                    return _deleted(target, located)
        except OSError as exc:
            # Le message du système, sans le chemin réel qu'il citerait.
            reason = exc.strerror or type(exc).__name__
            raise ToolError(f"{located.address} : {reason}.") from exc
        except UnicodeError as exc:
            raise ToolError(f"{located.address} : nom illisible en UTF-8.") from exc

    def _listed(self, target: Path, located: Located) -> dict[str, JsonValue]:
        if not target.is_dir():
            raise ToolError(f"{located.address} n'est pas un dossier.")
        entries: list[JsonValue] = []
        truncated = False
        for entry in sorted(target.iterdir(), key=lambda p: p.name):
            if entry.name.startswith("."):
                continue
            # Un lien qui mène hors du dossier n'est pas montré : on ne pourrait pas
            # l'ouvrir, et sa taille dirait quelque chose de ce qui est dehors.
            if (
                entry.is_symlink()
                and self._space.outside(entry.resolve(), located.folder) is not None
            ):
                continue
            if len(entries) >= MAX_ENTRIES:
                truncated = True
                break
            if entry.is_dir():
                entries.append({"name": entry.name, "type": "dir"})
            elif entry.is_file():
                entries.append({"name": entry.name, "type": "file", "size": entry.stat().st_size})
        return {"path": located.address, "entries": entries, "truncated": truncated}

    def _unlinked(self, located: Located) -> dict[str, JsonValue]:
        """Supprimer un lien, c'est supprimer le lien, pas ce qu'il désigne."""
        parent = self._space.real(located.lexical.parent, located)
        (parent / located.lexical.name).unlink()
        return {"path": located.address, "deleted": True}


def _read(target: Path, located: Located) -> dict[str, JsonValue]:
    if target.is_dir():
        raise ToolError(f"{located.address} est un dossier : list pour son contenu.")
    if not target.is_file():
        raise ToolError(f"{located.address} : fichier introuvable.")
    size = target.stat().st_size
    if size > MAX_READ_BYTES:
        raise ToolError(f"{located.address} fait {size} octets, au-delà de {MAX_READ_BYTES}.")
    with target.open("rb") as handle:
        # Un fichier qui grossit entre-temps reste borné.
        data = handle.read(MAX_READ_BYTES + 1)
    if len(data) > MAX_READ_BYTES:
        raise ToolError(f"{located.address} dépasse {MAX_READ_BYTES} octets.")
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        hint = (
            " ; un classeur se lit avec l'outil excel (action=read), s'il est accordé"
            if located.address.lower().endswith((".xlsx", ".xlsm"))
            else ""
        )
        raise ToolError(f"{located.address} n'est pas un fichier texte UTF-8{hint}.") from exc
    return {"path": located.address, "content": content, "size": len(data)}


def _written(target: Path, located: Located, content: str, overwrite: bool) -> dict[str, JsonValue]:
    try:
        data = content.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ToolError(
            "'content' contient des caractères qui ne s'écrivent pas en UTF-8."
        ) from exc
    if len(data) > MAX_WRITE_BYTES:
        raise ToolError(f"Contenu de {len(data)} octets, au-delà de {MAX_WRITE_BYTES}.")
    return write_bytes(target, located, data, overwrite)


async def _copied(name: str, context: ToolContext) -> bytes:
    """Octets d'un fichier de la conversation, désigné par son nom."""
    files = context.files
    if files is None:
        raise ToolError("Aucun fichier de la conversation n'est lisible ici (pas de stockage).")
    found = files.find(name)
    if found is None:
        known = ", ".join(files.names()) or "aucun"
        raise ToolError(f"Fichier {name!r} inconnu ; fichiers de la conversation : {known}.")
    try:
        return await files.read(found, max_bytes=MAX_COPY_BYTES)
    except (ValueError, LookupError) as exc:
        raise ToolError(f"{exc}.") from exc


def _deleted(target: Path, located: Located) -> dict[str, JsonValue]:
    if target == located.folder.root or target.is_dir():
        raise ToolError(f"{located.address} est un dossier : delete ne supprime que des fichiers.")
    if not target.is_file():
        raise ToolError(f"{located.address} : fichier introuvable.")
    target.unlink()
    return {"path": located.address, "deleted": True}


def _parse(arguments: Mapping[str, JsonValue]) -> _Arguments:
    try:
        return _Arguments.model_validate(dict(arguments))
    except ValidationError as exc:
        lines = ["Arguments invalides :"]
        for item in exc.errors(include_url=False):
            location = ".".join(str(part) for part in item["loc"]) or "(racine)"
            lines.append(f"- {location} : {item['msg']}")
        raise ToolError("\n".join(lines)) from None


def _description(space: Folders) -> str:
    lines = [
        "Lit, liste, écrit et supprime des fichiers dans des dossiers permis. "
        "Un fichier se désigne par « <dossier>/<chemin> », jamais par un chemin absolu.",
        "Dossiers et droits :",
        *space.described(),
    ]
    lines.append(
        "Actions : list (contenu d'un dossier) et read (lire un fichier) demandent read ; "
        "write écrit 'content' et crée les sous-dossiers au besoin (droit write) — un fichier "
        "existant n'est remplacé qu'avec overwrite=true (droit overwrite) ; avec 'file' au lieu "
        "de 'content', write copie un fichier de la conversation (joint, ou rendu par un outil), "
        "désigné par son nom, binaire compris (un fichier rendu par un outil se copie au tour "
        "suivant, pas dans le même lot d'appels) ; delete supprime un fichier, jamais un dossier "
        "(droit delete). Les fichiers et dossiers cachés (nom qui commence par « . ») sont "
        f"inaccessibles. read ne lit que du texte UTF-8. Au plus {MAX_READ_BYTES} octets lus ou "
        f"écrits par appel, {MAX_COPY_BYTES} pour une copie."
    )
    return "\n".join(lines)


def build(params: BaseModel, context: BuiltinContext) -> LocalFilesTool:
    """La fabrique de ``loom.local_files`` : vérifie les dossiers, ne lit ni n'écrit rien."""
    if not isinstance(params, LocalFilesParams):
        raise TypeError(f"paramètres de {NAME} attendus, reçu {type(params).__name__}")
    return LocalFilesTool(mount(params.folders, context))
