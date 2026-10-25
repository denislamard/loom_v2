# SPDX-License-Identifier: Apache-2.0
"""Outil local_files : lire, lister, écrire et supprimer des fichiers texte dans des dossiers.

Les dossiers, leurs droits et l'approbation de chaque droit viennent de la
config (``local_files_params``). Le modèle désigne un fichier par
``<dossier>/<chemin>`` : il ne voit jamais un chemin réel, et ne nomme que
les dossiers déclarés.

Ce qui décide d'un appel ne dépend que de ses arguments et de la config : le
dossier qui gouverne le chemin est le dossier déclaré le plus profond qui le
contient, **chemin pris tel qu'écrit**. C'est sur lui que se jugent les
droits et l'approbation, à l'évaluation de l'appel (``assess_call``) comme à
son exécution. À l'exécution, le chemin est résolu (liens suivis) et doit
rester dans ce même dossier déclaré : un lien qui mène ailleurs — hors des
dossiers permis, ou dans un autre dossier déclaré, qui aurait ses propres
droits — est refusé. Ainsi un lien posé entre l'évaluation et l'exécution ne
peut pas faire passer un appel sous une approbation qui n'était pas la
sienne.

Reste une course, documentée : entre le contrôle du chemin résolu et
l'accès, un autre programme qui remplacerait un dossier par un lien pourrait
faire sortir l'accès du dossier. L'outil ne s'en garde pas (pas d'``openat``) ;
qu'aucun autre programme n'écrive de liens dans les dossiers exposés.
"""

import asyncio
import os
import stat
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Annotated, Final, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from loom_ia.core.model import SideEffects, ToolOutput, ToolSpec
from loom_ia.core.ports import CallAssessment, ToolContext, ToolError
from loom_ia.tools import to_output
from loom_ia.tools_builtin import BuiltinContext, Protected
from loom_ia.tools_builtin.local_files_params import (
    RIGHTS,
    LocalFilesParams,
    Right,
    RightApproval,
)

NAME: Final = "local_files"
MAX_READ_BYTES: Final = 1_000_000
MAX_WRITE_BYTES: Final = 1_000_000
MAX_ENTRIES: Final = 500
# Fichier temporaire d'une écriture : caché, donc invisible du modèle.
TEMP_PREFIX: Final = ".loom-"

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
    overwrite: Annotated[
        bool,
        Field(description="Pour write : remplacer le fichier s'il existe déjà (droit overwrite)."),
    ] = False


@dataclass(frozen=True, slots=True)
class Folder:
    """Un dossier permis, tel que l'outil l'utilise : sa racine résolue et ses droits."""

    name: str
    root: Path
    rights: Mapping[Right, RightApproval]

    def granted(self) -> str:
        """« read, write » : ses droits, dans l'ordre où on les dit."""
        return ", ".join(right for right in RIGHTS if right in self.rights)

    def asking(self) -> list[Right]:
        """Ses droits qui attendent une approbation."""
        return [right for right in RIGHTS if self.rights.get(right) == "always"]


@dataclass(frozen=True, slots=True)
class _Located:
    """Un chemin donné par le modèle, rapporté à son dossier, sans rien lire sur le disque."""

    lexical: Path
    folder: Folder

    @property
    def address(self) -> str:
        relative = self.lexical.relative_to(self.folder.root).as_posix()
        return self.folder.name if relative == "." else f"{self.folder.name}/{relative}"


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
        self._by_name = {folder.name: folder for folder in folders}
        # Du plus profond au moins profond : le premier qui contient un chemin le gouverne.
        self._deepest = sorted(folders, key=lambda f: len(f.root.parts), reverse=True)
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
            description=_description(folders),
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
        lines: list[str] = []
        for folder in self.folders:
            asking = folder.asking()
            approval = f" ; approbation : {', '.join(asking)}" if asking else ""
            lines.append(f"{folder.name} → {folder.root} : {folder.granted()}{approval}")
        return tuple(lines)

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
        asking = [right for right in _needs(request) if located.folder.rights[right] == "always"]
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
        # Lire et écrire sur le disque bloque : hors de la boucle asyncio.
        result = await asyncio.to_thread(self._perform, request, located)
        return to_output(result)

    # --- Chemins et droits ------------------------------------------------------

    def _locate(self, request: _Arguments) -> _Located:
        """Le dossier qui gouverne le chemin, tel qu'écrit : ni disque, ni lien suivi."""
        raw = request.path.strip()
        known = ", ".join(self._by_name)
        if not raw or "\x00" in raw:
            raise ToolError(f"'path' attendu sous la forme « <dossier>/<chemin> » ({known}).")
        pure = PurePosixPath(raw)
        if pure.is_absolute():
            raise ToolError(
                f"Chemin absolu refusé : « <dossier>/<chemin> », avec un dossier parmi : {known}."
            )
        if ".." in pure.parts:
            raise ToolError("Chemin refusé : « .. » n'est pas permis.")
        if not pure.parts:
            raise ToolError(f"'path' attendu sous la forme « <dossier>/<chemin> » ({known}).")
        name, *rest = pure.parts
        folder = self._by_name.get(name)
        if folder is None:
            raise ToolError(f"Dossier inconnu : {name!r}. Dossiers permis : {known}.")
        if any(part.startswith(".") for part in rest):
            raise ToolError("Chemin refusé : les fichiers et dossiers cachés sont inaccessibles.")
        if request.action != "list" and not rest:
            raise ToolError(f"{name} est un dossier : donner « {name}/<chemin du fichier> ».")
        lexical = folder.root.joinpath(*rest)
        governing = next(f for f in self._deepest if lexical.is_relative_to(f.root))
        return _Located(lexical=lexical, folder=governing)

    def _check_rights(self, request: _Arguments, located: _Located) -> None:
        if request.action == "write" and request.content is None:
            raise ToolError("write attend 'content', le texte à écrire.")
        if request.action != "write" and request.content is not None:
            raise ToolError("'content' ne sert qu'à write.")
        if request.action != "write" and request.overwrite:
            raise ToolError("'overwrite' ne sert qu'à write.")
        missing = [right for right in _needs(request) if right not in located.folder.rights]
        if missing:
            raise ToolError(
                f"Droit {', '.join(missing)} refusé sur {located.address} "
                f"(dossier {located.folder.name} : {located.folder.granted()})."
            )

    def _resolved(self, located: _Located) -> Path:
        """Le chemin réel, liens suivis, qui doit rester dans le dossier qui le gouverne."""
        return self._real(located.lexical, located)

    def _real(self, path: Path, located: _Located) -> Path:
        try:
            target = path.resolve()
        except (OSError, RuntimeError, UnicodeError) as exc:
            raise ToolError(
                f"{located.address} : chemin illisible ({type(exc).__name__})."
            ) from exc
        match self._outside(target, located.folder):
            case None:
                return target
            case "outside":
                raise ToolError(f"Chemin refusé : {located.address} mène hors des dossiers permis.")
            case "hidden":
                raise ToolError(
                    "Chemin refusé : les fichiers et dossiers cachés sont inaccessibles."
                )
            case Folder(name=other):
                raise ToolError(
                    f"Chemin refusé : {located.address} passe par un lien vers le dossier "
                    f"{other}, qui a ses propres droits ; y accéder par « {other}/… »."
                )

    def _outside(self, real: Path, folder: Folder) -> Folder | Literal["outside", "hidden"] | None:
        """Pourquoi un chemin réel sort de ``folder`` : hors de tout, caché, ou un autre dossier."""
        governing = next((f for f in self._deepest if real.is_relative_to(f.root)), None)
        if governing is None:
            return "outside"
        if governing is not folder:
            return governing
        if any(part.startswith(".") for part in real.relative_to(governing.root).parts):
            return "hidden"
        return None

    # --- Opérations ---------------------------------------------------------------

    def _perform(self, request: _Arguments, located: _Located) -> dict[str, JsonValue]:
        try:
            if request.action == "delete" and located.lexical.is_symlink():
                return self._unlinked(located)
            target = self._resolved(located)
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

    def _listed(self, target: Path, located: _Located) -> dict[str, JsonValue]:
        if not target.is_dir():
            raise ToolError(f"{located.address} n'est pas un dossier.")
        entries: list[JsonValue] = []
        truncated = False
        for entry in sorted(target.iterdir(), key=lambda p: p.name):
            if entry.name.startswith("."):
                continue
            # Un lien qui mène hors du dossier n'est pas montré : on ne pourrait pas
            # l'ouvrir, et sa taille dirait quelque chose de ce qui est dehors.
            if entry.is_symlink() and self._outside(entry.resolve(), located.folder) is not None:
                continue
            if len(entries) >= MAX_ENTRIES:
                truncated = True
                break
            if entry.is_dir():
                entries.append({"name": entry.name, "type": "dir"})
            elif entry.is_file():
                entries.append({"name": entry.name, "type": "file", "size": entry.stat().st_size})
        return {"path": located.address, "entries": entries, "truncated": truncated}

    def _unlinked(self, located: _Located) -> dict[str, JsonValue]:
        """Supprimer un lien, c'est supprimer le lien, pas ce qu'il désigne."""
        parent = self._real(located.lexical.parent, located)
        (parent / located.lexical.name).unlink()
        return {"path": located.address, "deleted": True}


def _read(target: Path, located: _Located) -> dict[str, JsonValue]:
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
        raise ToolError(f"{located.address} n'est pas un fichier texte UTF-8.") from exc
    return {"path": located.address, "content": content, "size": len(data)}


def _written(
    target: Path, located: _Located, content: str, overwrite: bool
) -> dict[str, JsonValue]:
    try:
        data = content.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ToolError(
            "'content' contient des caractères qui ne s'écrivent pas en UTF-8."
        ) from exc
    if len(data) > MAX_WRITE_BYTES:
        raise ToolError(f"Contenu de {len(data)} octets, au-delà de {MAX_WRITE_BYTES}.")
    if target == located.folder.root or target.is_dir():
        raise ToolError(f"{located.address} est un dossier.")
    existed = target.exists()
    mode: int | None = None
    if existed:
        if not target.is_file():
            raise ToolError(f"{located.address} n'est pas un fichier.")
        info = target.stat()
        # Le même contenu est déjà là : rien à faire. C'est ce qui rend l'écriture
        # sûre à refaire après une interruption.
        if info.st_size == len(data) and target.read_bytes() == data:
            return {"path": located.address, "bytes": len(data), "written": False}
        if not overwrite:
            raise _exists(located)
        # Sans droit d'écriture pour personne, ou pour ce process : lecture seule. Le
        # premier test vaut aussi pour root, à qui ``os.access`` dit toujours oui.
        if not info.st_mode & _WRITE_BITS or not os.access(target, os.W_OK):
            raise ToolError(f"{located.address} est en lecture seule : il n'est pas remplacé.")
        mode = stat.S_IMODE(info.st_mode)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Avec overwrite, remplacer un fichier créé entre-temps est permis.
        _atomic_write(target, data, mode, replace=existed or overwrite)
    except FileExistsError:
        # Créé entre-temps, par un autre appel du même lot par exemple : sans le
        # droit de remplacer, on ne l'écrase pas, sauf s'il porte déjà ce contenu.
        if target.is_file() and target.read_bytes() == data:
            return {"path": located.address, "bytes": len(data), "written": False}
        raise _exists(located) from None
    return {"path": located.address, "bytes": len(data), "written": True, "replaced": existed}


def _exists(located: _Located) -> ToolError:
    return ToolError(
        f"{located.address} existe déjà : overwrite=true pour le remplacer (droit overwrite)."
    )


_EXCLUSIVE: Final = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
_WRITE_BITS: Final = stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH


def _atomic_write(target: Path, data: bytes, mode: int | None, *, replace: bool) -> None:
    """Fichier temporaire voisin, puis mise en place : jamais de fichier à moitié écrit.

    ``replace`` : le fichier existant est remplacé (renommage). Sinon la mise
    en place est **exclusive** — un lien physique, qui échoue si la cible existe
    déjà (``FileExistsError``) : deux écritures parallèles ne s'écrasent pas.
    Sans liens physiques (certains systèmes de fichiers), le fichier est créé
    directement, exclusif lui aussi, mais sans l'atomicité du renommage.

    Un fichier remplacé garde ses permissions ; un nouveau prend celles que
    donne le masque du process (``0o666`` filtré par l'umask).
    """
    temporary = target.parent / f"{TEMP_PREFIX}{uuid.uuid4().hex}.tmp"
    try:
        _write_new(temporary, data)
        if mode is not None:
            os.chmod(temporary, mode)
        if replace:
            os.replace(temporary, target)
        else:
            try:
                os.link(temporary, target, follow_symlinks=False)
            except FileExistsError:
                raise
            except OSError:
                _write_new(target, data)
    finally:
        temporary.unlink(missing_ok=True)
    _sync_directory(target.parent)


def _write_new(path: Path, data: bytes) -> None:
    """Crée ``path`` et l'écrit ; un échec en cours d'écriture ne laisse rien derrière.

    ``FileExistsError`` si le fichier existe : il n'est alors pas touché.
    """
    descriptor = os.open(path, _EXCLUSIVE, 0o666)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        # Créé par cet appel (O_EXCL) : un fichier tronqué ferait croire à un
        # contenu, et une reprise répondrait « existe déjà ».
        path.unlink(missing_ok=True)
        raise


def _sync_directory(directory: Path) -> None:
    """Pour que la mise en place survive à une coupure ; sans échouer là où c'est impossible."""
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _deleted(target: Path, located: _Located) -> dict[str, JsonValue]:
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


def _description(folders: tuple[Folder, ...]) -> str:
    lines = [
        "Lit, liste, écrit et supprime des fichiers texte (UTF-8) dans des dossiers permis. "
        "Un fichier se désigne par « <dossier>/<chemin> », jamais par un chemin absolu.",
        "Dossiers et droits :",
    ]
    for folder in folders:
        asking = folder.asking()
        waits = f" (attend une approbation humaine : {', '.join(asking)})" if asking else ""
        lines.append(f"- {folder.name} : {folder.granted()}{waits}")
    lines.append(
        "Actions : list (contenu d'un dossier) et read (lire un fichier) demandent read ; "
        "write écrit 'content' et crée les sous-dossiers au besoin (droit write) — un fichier "
        "existant n'est remplacé qu'avec overwrite=true (droit overwrite) ; delete supprime un "
        "fichier, jamais un dossier (droit delete). Les fichiers et dossiers cachés (nom qui "
        f"commence par « . ») sont inaccessibles. Au plus {MAX_READ_BYTES} octets lus ou écrits "
        "par appel."
    )
    return "\n".join(lines)


def build(params: BaseModel, context: BuiltinContext) -> LocalFilesTool:
    """La fabrique de ``loom.local_files`` : vérifie les dossiers, ne lit ni n'écrit rien."""
    if not isinstance(params, LocalFilesParams):
        raise TypeError(f"paramètres de {NAME} attendus, reçu {type(params).__name__}")
    folders: list[Folder] = []
    for spec in params.folders:
        given = (
            spec.path
            if spec.path.is_absolute() or context.base_dir is None
            else (context.base_dir / spec.path)
        )
        root = given.resolve()
        if not root.is_dir():
            raise ValueError(f"dossier {spec.name!r} : {root} n'existe pas ou n'est pas un dossier")
        problem = _exposes(root, context.protected)
        if problem is not None:
            raise ValueError(
                f"dossier {spec.name!r} : {root} {problem} — le modèle pourrait y lire des "
                "secrets ou se réécrire lui-même ; exposer un dossier qui n'en contient pas"
            )
        same = next((f for f in folders if f.root == root), None)
        if same is not None:
            raise ValueError(f"dossiers {same.name!r} et {spec.name!r} : même chemin {root}")
        folders.append(Folder(name=spec.name, root=root, rights=dict(spec.rights)))
    return LocalFilesTool(tuple(folders))


def _exposes(root: Path, protected: tuple[Protected, ...]) -> str | None:
    """Ce que le dossier exposerait de loom, ou ``None``."""
    for item in protected:
        path = item.path.resolve()
        if path == root:
            return f"est {item.what}"
        if path.is_relative_to(root):
            return f"contient {item.what} ({path})"
        if item.subtree and root.is_relative_to(path):
            return f"est dans {item.what} ({path})"
    return None
