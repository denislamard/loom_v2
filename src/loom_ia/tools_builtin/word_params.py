# SPDX-License-Identifier: Apache-2.0
"""Paramètres de ``loom.word`` : les dossiers permis, et le fuseau des dates-heures.

Module léger — Pydantic et ``_office_params`` seulement — : la config
l'importe pour valider la référence au chargement, sans importer l'outil.

    - python: loom.word
      folders:
      - name: modeles
        path: modeles
        rights: [ read ]               # les fichiers que l'outil peut lire
      - name: sortie
        path: sortie
        rights: [ write, overwrite ]   # remplacer un fichier : approbation par défaut
      timezone: Europe/Paris           # par défaut : le fuseau des dates-heures écrites
      charts: true                     # le bloc graphique ; false par défaut
      images: true                     # le bloc image ; false par défaut
      templates:                       # modèles de document, relatifs à prompts_dir
        cahier-des-charges: modele-cdc.md

Les dossiers suivent la forme de ``loom.local_files`` (``name``, ``path``,
``rights``, ``approval``) ; le droit ``delete`` n'a pas de sens ici et est refusé.
"""

from typing import Annotated, Final, Self

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from loom_ia.tools_builtin._office_params import (
    DEFAULT_TIMEZONE,
    Templates,
    check_timezone,
    checked_folders,
    no_delete,
)
from loom_ia.tools_builtin.local_files_params import FolderSpec

REFERENCE: Final = "loom.word"


class WordParams(BaseModel):
    """Paramètres de ``loom.word``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    folders: Annotated[
        tuple[FolderSpec, ...],
        BeforeValidator(no_delete(REFERENCE)),
        AfterValidator(checked_folders(REFERENCE)),
        Field(json_schema_extra={"minItems": 1}),
    ]
    # Fuseau IANA où passe une date-heure qui en porte un : le document l'écrit
    # à l'heure de ce fuseau, sans le nommer.
    timezone: str = DEFAULT_TIMEZONE
    # Le bloc graphique : environ 500 tokens de plus par appel au modèle, d'où false.
    charts: bool = False
    # Le bloc image : environ 230 tokens de plus par appel au modèle, d'où false.
    images: bool = False
    # Les modèles de document, par nom : fichiers relatifs à prompts_dir, lus au chargement.
    templates: Templates = {}

    @model_validator(mode="after")
    def _check(self) -> Self:
        check_timezone(self.timezone)
        return self
