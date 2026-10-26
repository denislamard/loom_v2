# SPDX-License-Identifier: Apache-2.0
"""Paramètres de ``loom.excel`` : les dossiers permis, et le recalcul par LibreOffice.

Module léger — Pydantic et ``local_files_params`` seulement — : la config
l'importe pour valider la référence au chargement, sans importer l'outil ni
openpyxl.

    - python: loom.excel
      folders:
      - name: donnees
        path: donnees
        rights: [ read ]               # les CSV / JSON que l'outil peut lire
      - name: sortie
        path: sortie
        rights: [ write, overwrite ]   # remplacer un classeur : approbation par défaut
      timezone: Europe/Paris           # par défaut : le fuseau des dates-heures écrites
      recalc: true                     # vérifier les formules avec LibreOffice

Les dossiers suivent la forme de ``loom.local_files`` (``name``, ``path``,
``rights``) ; le droit ``delete`` n'a pas de sens ici et est refusé.
"""

from pathlib import Path
from typing import Annotated, Final, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, PositiveFloat, model_validator

from loom_ia.tools_builtin.local_files_params import FolderSpec

DEFAULT_RECALC_TIMEOUT: Final = 60.0
DEFAULT_TIMEZONE: Final = "Europe/Paris"


def _folders(folders: tuple[FolderSpec, ...]) -> tuple[FolderSpec, ...]:
    if not folders:
        raise ValueError("au moins un dossier est attendu")
    names = [folder.name for folder in folders]
    doubles = sorted({name for name in names if names.count(name) > 1})
    if doubles:
        raise ValueError(f"nom de dossier donné deux fois : {', '.join(doubles)}")
    deleting = [folder.name for folder in folders if "delete" in folder.rights]
    if deleting:
        raise ValueError(
            f"dossier {', '.join(deleting)} : le droit 'delete' ne sert pas à loom.excel, "
            "qui ne supprime rien (droits permis : read, write, overwrite)"
        )
    if not any("write" in folder.rights for folder in folders):
        raise ValueError("au moins un dossier avec le droit 'write' est attendu : où écrire")
    return folders


class ExcelParams(BaseModel):
    """Paramètres de ``loom.excel``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    folders: Annotated[
        tuple[FolderSpec, ...],
        AfterValidator(_folders),
        Field(json_schema_extra={"minItems": 1}),
    ]
    # Fuseau IANA où passe une date-heure qui en porte un : Excel n'en connaît pas,
    # la cellule reçoit l'heure de ce fuseau, sans fuseau.
    timezone: str = DEFAULT_TIMEZONE
    # Recalculer le classeur avec LibreOffice avant de l'écrire : une formule en
    # erreur (#DIV/0!, #NAME?…) fait échouer l'appel, et le fichier n'est pas écrit.
    recalc: bool = False
    # L'exécutable de LibreOffice ; par défaut ``soffice`` cherché dans le PATH.
    soffice: Path | None = None
    recalc_timeout: PositiveFloat = DEFAULT_RECALC_TIMEOUT

    @model_validator(mode="after")
    def _check(self) -> Self:
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError, ValueError:
            raise ValueError(
                f"fuseau inconnu : {self.timezone!r} — un nom IANA, par exemple Europe/Paris"
            ) from None
        if not self.recalc and self.soffice is not None:
            raise ValueError("'soffice' ne sert qu'avec recalc: true")
        if not self.recalc and self.recalc_timeout != DEFAULT_RECALC_TIMEOUT:
            raise ValueError("'recalc_timeout' ne sert qu'avec recalc: true")
        return self
