# SPDX-License-Identifier: Apache-2.0
"""Paramètres communs des outils de documents (``loom.excel``, ``loom.word``, ``loom.pdf``).

Leurs dossiers suivent la forme de ``loom.local_files`` (``name``, ``path``,
``rights``, ``approval``), sans le droit ``delete`` : ces outils ne suppriment
rien. Leur fuseau est un nom IANA, vérifié au chargement.

Module léger — Pydantic et ``local_files_params`` seulement — : la config
l'importe pour valider une référence, sans importer aucun des outils.
"""

from collections.abc import Callable, Mapping, Sequence
from typing import Final, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from loom_ia.tools_builtin.local_files_params import FolderSpec

DEFAULT_TIMEZONE: Final = "Europe/Paris"


def no_delete(reference: str) -> Callable[[object], object]:
    """Avant les règles communes : ``delete`` dans ``rights`` ou ``approval`` est refusé.

    Sinon, ``approval: { delete: … }`` sans ``delete`` accordé ferait conseiller
    de l'ajouter à ``rights``, où il est refusé. ``reference`` : « loom.excel »…
    """

    def check(value: object) -> object:
        if not isinstance(value, list | tuple):
            return value
        items = cast("Sequence[object]", value)
        deleting: list[str] = []
        for item in items:
            if not isinstance(item, Mapping):
                continue
            folder = cast(Mapping[str, object], item)
            rights, approval = folder.get("rights"), folder.get("approval")
            listed = isinstance(rights, list | tuple) and "delete" in cast(
                "Sequence[object]", rights
            )
            approved = isinstance(approval, Mapping) and "delete" in cast(
                Mapping[str, object], approval
            )
            if listed or approved:
                deleting.append(str(folder.get("name")))
        if deleting:
            raise _refused(reference, deleting)
        return items

    return check


def checked_folders(
    reference: str,
) -> Callable[[tuple[FolderSpec, ...]], tuple[FolderSpec, ...]]:
    """Après la validation des dossiers : au moins un, noms uniques, jamais ``delete``."""

    def check(folders: tuple[FolderSpec, ...]) -> tuple[FolderSpec, ...]:
        if not folders:
            raise ValueError("au moins un dossier est attendu")
        names = [folder.name for folder in folders]
        doubles = sorted({name for name in names if names.count(name) > 1})
        if doubles:
            raise ValueError(f"nom de dossier donné deux fois : {', '.join(doubles)}")
        deleting = [folder.name for folder in folders if "delete" in folder.rights]
        if deleting:
            raise _refused(reference, deleting)
        return folders

    return check


def _refused(reference: str, names: Sequence[str]) -> ValueError:
    return ValueError(
        f"dossier {', '.join(names)} : le droit 'delete' ne sert pas à {reference}, "
        "qui ne supprime rien (droits permis : read, write, overwrite)"
    )


def check_timezone(name: str) -> None:
    """``ValueError`` si ``name`` n'est pas un fuseau IANA connu."""
    try:
        ZoneInfo(name)
    except ZoneInfoNotFoundError, ValueError:
        raise ValueError(
            f"fuseau inconnu : {name!r} — un nom IANA, par exemple Europe/Paris"
        ) from None
