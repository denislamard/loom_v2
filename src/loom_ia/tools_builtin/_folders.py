# SPDX-License-Identifier: Apache-2.0
"""Dossiers permis des outils fournis : désigner un fichier, juger ses droits, l'écrire.

Partagé par ``loom.local_files`` et ``loom.excel`` : un seul endroit décide
de ce qu'un outil fourni peut atteindre sur le disque.

Le modèle désigne un fichier par ``<dossier>/<chemin>`` : il ne voit jamais
un chemin réel, et ne nomme que les dossiers déclarés.

Ce qui décide d'un appel ne dépend que de ses arguments et de la config : le
dossier qui gouverne le chemin est le dossier déclaré le plus profond qui le
contient, **chemin pris tel qu'écrit** (``Folders.locate``). C'est sur lui que
se jugent les droits et l'approbation, à l'évaluation de l'appel comme à son
exécution. À l'exécution, le chemin est résolu (liens suivis,
``Folders.resolved``) et doit rester dans ce même dossier déclaré : un lien
qui mène ailleurs — hors des dossiers permis, ou dans un autre dossier
déclaré, qui aurait ses propres droits — est refusé. Ainsi un lien posé entre
l'évaluation et l'exécution ne peut pas faire passer un appel sous une
approbation qui n'était pas la sienne.

Reste une course, documentée : entre le contrôle du chemin résolu et
l'accès, un autre programme qui remplacerait un dossier par un lien pourrait
faire sortir l'accès du dossier. Les outils ne s'en gardent pas (pas
d'``openat``) ; qu'aucun autre programme n'écrive de liens dans les dossiers
exposés.
"""

import os
import stat
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final, Literal

from pydantic import JsonValue

from loom_ia.core.ports import ToolError
from loom_ia.tools_builtin import BuiltinContext, Grant, Place, Protected
from loom_ia.tools_builtin.local_files_params import (
    DESTRUCTIVE,
    RIGHTS,
    FolderSpec,
    Right,
    RightApproval,
)

# Fichier temporaire d'une écriture : caché, donc invisible du modèle.
TEMP_PREFIX: Final = ".loom-"


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
class Located:
    """Un chemin donné par le modèle, rapporté à son dossier, sans rien lire sur le disque."""

    lexical: Path
    folder: Folder

    @property
    def address(self) -> str:
        relative = self.lexical.relative_to(self.folder.root).as_posix()
        return self.folder.name if relative == "." else f"{self.folder.name}/{relative}"


class Folders:
    """Les dossiers permis d'un outil monté."""

    def __init__(self, folders: tuple[Folder, ...]) -> None:
        if not folders:
            raise ValueError("au moins un dossier est attendu")
        self.folders = folders
        self._by_name = {folder.name: folder for folder in folders}
        # Du plus profond au moins profond : le premier qui contient un chemin le gouverne.
        self._deepest = sorted(folders, key=lambda f: len(f.root.parts), reverse=True)

    def places(self) -> tuple[Place, ...]:
        """Pour ``loom validate`` : chaque dossier et ses droits accordés.

        Un droit refusé n'est pas listé. ``granted()``, que lit le modèle, reste à part.
        """
        return tuple(
            Place(
                name=folder.name,
                path=folder.root,
                rights=RIGHTS,
                grants=(
                    (
                        "",
                        tuple(
                            Grant(right, approval == "always", right in DESTRUCTIVE)
                            for right in RIGHTS
                            if (approval := folder.rights.get(right)) is not None
                        ),
                    ),
                ),
            )
            for folder in self.folders
        )

    def described(self) -> list[str]:
        """Une ligne par dossier, pour la description que lit le modèle : jamais le chemin."""
        lines: list[str] = []
        for folder in self.folders:
            asking = folder.asking()
            waits = f" (attend une approbation humaine : {', '.join(asking)})" if asking else ""
            lines.append(f"- {folder.name} : {folder.granted()}{waits}")
        return lines

    def locate(self, path: str, *, folder_allowed: bool = False) -> Located:
        """Le dossier qui gouverne le chemin, tel qu'écrit : ni disque, ni lien suivi.

        ``folder_allowed`` : « <dossier> » seul est permis (un listage).
        """
        raw = path.strip()
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
        if not folder_allowed and not rest:
            raise ToolError(f"{name} est un dossier : donner « {name}/<chemin du fichier> ».")
        lexical = folder.root.joinpath(*rest)
        governing = next(f for f in self._deepest if lexical.is_relative_to(f.root))
        return Located(lexical=lexical, folder=governing)

    @staticmethod
    def require(located: Located, rights: Iterable[Right]) -> None:
        """``ToolError`` si le dossier qui gouverne le chemin n'accorde pas ces droits."""
        missing = [right for right in rights if right not in located.folder.rights]
        if missing:
            raise ToolError(
                f"Droit {', '.join(missing)} refusé sur {located.address} "
                f"(dossier {located.folder.name} : {located.folder.granted()})."
            )

    @staticmethod
    def asked(located: Located, rights: Iterable[Right]) -> list[Right]:
        """Parmi ces droits, ceux que le dossier soumet à une approbation."""
        return [right for right in rights if located.folder.rights.get(right) == "always"]

    def resolved(self, located: Located) -> Path:
        """Le chemin réel, liens suivis, qui doit rester dans le dossier qui le gouverne."""
        return self.real(located.lexical, located)

    def real(self, path: Path, located: Located) -> Path:
        """``path`` résolu, refusé s'il sort du dossier qui gouverne ``located``."""
        try:
            target = path.resolve()
        except (OSError, RuntimeError, UnicodeError) as exc:
            raise ToolError(
                f"{located.address} : chemin illisible ({type(exc).__name__})."
            ) from exc
        match self.outside(target, located.folder):
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

    def outside(self, real: Path, folder: Folder) -> Folder | Literal["outside", "hidden"] | None:
        """Pourquoi un chemin réel sort de ``folder`` : hors de tout, caché, ou un autre dossier."""
        governing = next((f for f in self._deepest if real.is_relative_to(f.root)), None)
        if governing is None:
            return "outside"
        if governing is not folder:
            return governing
        if any(part.startswith(".") for part in real.relative_to(governing.root).parts):
            return "hidden"
        return None


# --- Montage ------------------------------------------------------------------------


def mount(specs: Iterable[FolderSpec], context: BuiltinContext) -> tuple[Folder, ...]:
    """Les dossiers de la config, vérifiés : présents, distincts, sans rien exposer de loom.

    ``ValueError`` dit ce qui ne va pas ; rien n'est lu ni écrit.
    """
    folders: list[Folder] = []
    for spec in specs:
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
    return tuple(folders)


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


# --- Écriture -----------------------------------------------------------------------


def write_bytes(
    target: Path, located: Located, data: bytes, overwrite: bool
) -> dict[str, JsonValue]:
    """Écrit ``data`` en ``target`` (déjà résolu) : création exclusive, ou remplacement.

    Le même contenu déjà là n'est pas réécrit (``written`` faux) : c'est ce qui
    rend l'écriture sûre à refaire après une interruption. Un fichier existant
    n'est remplacé qu'avec ``overwrite``, et jamais s'il est en lecture seule.
    """
    if target == located.folder.root or target.is_dir():
        raise ToolError(f"{located.address} est un dossier.")
    existed = target.exists()
    mode: int | None = None
    if existed:
        if not target.is_file():
            raise ToolError(f"{located.address} n'est pas un fichier.")
        info = target.stat()
        # Le même contenu est déjà là : rien à faire.
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
        atomic_write(target, data, mode, replace=existed or overwrite)
    except FileExistsError:
        # Créé entre-temps, par un autre appel du même lot par exemple : sans le
        # droit de remplacer, on ne l'écrase pas, sauf s'il porte déjà ce contenu.
        if target.is_file() and target.read_bytes() == data:
            return {"path": located.address, "bytes": len(data), "written": False}
        raise _exists(located) from None
    return {"path": located.address, "bytes": len(data), "written": True, "replaced": existed}


def _exists(located: Located) -> ToolError:
    return ToolError(
        f"{located.address} existe déjà : overwrite=true pour le remplacer (droit overwrite)."
    )


_EXCLUSIVE: Final = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
_WRITE_BITS: Final = stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH


def atomic_write(target: Path, data: bytes, mode: int | None, *, replace: bool) -> None:
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
        write_new(temporary, data)
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
                write_new(target, data)
    finally:
        temporary.unlink(missing_ok=True)
    _sync_directory(target.parent)


def write_new(path: Path, data: bytes) -> None:
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
