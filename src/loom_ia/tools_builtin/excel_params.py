# SPDX-License-Identifier: Apache-2.0
"""Paramètres de ``loom.excel`` : les dossiers permis, le recalcul par LibreOffice, les graphiques.

Module léger — Pydantic et ``_office_params`` seulement — : la config
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
      - name: brouillons
        path: brouillons
        rights: [ write, overwrite ]
        approval: { overwrite: never } # remplacer sans validation : dit explicitement
      timezone: Europe/Paris           # par défaut : le fuseau des dates-heures écrites
      recalc: true                     # vérifier les formules avec LibreOffice
      charts: true                     # des graphiques dans les onglets ; false par défaut

Les dossiers suivent la forme de ``loom.local_files`` (``name``, ``path``,
``rights``, ``approval``) ; le droit ``delete`` n'a pas de sens ici et est refusé.
"""

from pathlib import Path
from typing import Annotated, Final, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PositiveFloat,
    model_validator,
)

from loom_ia.tools_builtin._office_params import (
    DEFAULT_TIMEZONE,
    check_timezone,
    checked_folders,
    no_delete,
)
from loom_ia.tools_builtin.local_files_params import FolderSpec

DEFAULT_RECALC_TIMEOUT: Final = 60.0
"""Délai du recalcul par LibreOffice sans réglage, en secondes."""
REFERENCE: Final = "loom.excel"
"""Référence de l'outil dans la config."""


class ExcelParams(BaseModel):
    """Paramètres de ``loom.excel``.

    Attributes:
        folders: Dossiers permis, au moins un ; le droit ``delete`` n'y a pas
            cours.
        timezone: Fuseau IANA où passe une date-heure qui en porte un : Excel
            n'en connaît pas, la cellule reçoit l'heure de ce fuseau, sans
            fuseau.
        recalc: Recalculer le classeur avec LibreOffice avant de l'écrire : une
            formule en erreur (#DIV/0!, #NAME?…) fait échouer l'appel, et le
            fichier n'est pas écrit.
        soffice: L'exécutable de LibreOffice ; par défaut ``soffice`` cherché
            dans le PATH.
        recalc_timeout: Délai du recalcul, en secondes.
        charts: Des graphiques Excel liés aux cellules de leur onglet. Faux par
            défaut : leur mode d'emploi et leur schéma coûtent à chaque appel
            du modèle.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    folders: Annotated[
        tuple[FolderSpec, ...],
        BeforeValidator(no_delete(REFERENCE)),
        AfterValidator(checked_folders(REFERENCE)),
        Field(json_schema_extra={"minItems": 1}),
    ]
    timezone: str = DEFAULT_TIMEZONE
    recalc: bool = False
    soffice: Path | None = None
    recalc_timeout: PositiveFloat = DEFAULT_RECALC_TIMEOUT
    charts: bool = False

    @model_validator(mode="after")
    def _check(self) -> Self:
        check_timezone(self.timezone)
        if not self.recalc and self.soffice is not None:
            raise ValueError("'soffice' ne sert qu'avec recalc: true")
        if not self.recalc and self.recalc_timeout != DEFAULT_RECALC_TIMEOUT:
            raise ValueError("'recalc_timeout' ne sert qu'avec recalc: true")
        return self
