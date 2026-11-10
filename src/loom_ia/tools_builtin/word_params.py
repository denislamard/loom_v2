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
      blocks: [chart, image]           # les blocs facultatifs : chart, image, signature, toc,
                                       # checkboxes, fields, code, invoice_totals,
                                       # attendance ; aucun par défaut
      style: styles/charte.yaml        # la charte (facultatif), relative à resources_dir
      templates:                       # modèles de document, relatifs à resources_dir ; le
        facture: templates/facture.yaml  # style d'un modèle se nomme dans son fichier

Les dossiers suivent la forme de ``loom.local_files`` (``name``, ``path``,
``rights``, ``approval``) ; le droit ``delete`` n'a pas de sens ici et est refusé.
"""

from typing import Annotated, Final, Self

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from loom_ia.tools_builtin._office_params import (
    DEFAULT_TIMEZONE,
    Blocks,
    Templates,
    check_timezone,
    checked_folders,
    no_delete,
    no_old_keys,
)
from loom_ia.tools_builtin._style_params import StyleField
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
    # Les blocs facultatifs, chacun payé en tokens à chaque appel au modèle : chart
    # (environ 430), image (environ 190), signature, toc et checkboxes (environ 190 à eux
    # trois), fields (environ 115), code (environ 75), invoice_totals (environ 185),
    # attendance (environ 130). Aucun par défaut.
    blocks: Blocks = ()
    # Les modèles de document, par nom : fichiers relatifs à resources_dir, lus au chargement.
    templates: Templates = {}
    # La charte : un fichier de style relatif à resources_dir, lu au chargement, ou un style
    # écrit en ligne ; le style d'un modèle se nomme dans le fichier du modèle (style:).
    style: StyleField = None

    @classmethod
    def precheck(cls, value: object) -> object:
        """Avant toute validation, aussi sur la référence de la config (``agents.spec``) :
        les clés de la 2.0 refusées avec ce qui les remplace."""
        return no_old_keys(value)

    @model_validator(mode="before")
    @classmethod
    def _early(cls, value: object) -> object:
        return cls.precheck(value)

    @model_validator(mode="after")
    def _check(self) -> Self:
        check_timezone(self.timezone)
        return self
