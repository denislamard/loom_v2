# SPDX-License-Identifier: Apache-2.0
"""Paramètres de ``loom.pdf`` : les dossiers permis, et le fuseau des dates-heures.

Module léger — Pydantic et ``_office_params`` seulement — : la config
l'importe pour valider la référence au chargement, sans importer l'outil ni pypdf ni reportlab.

    - python: loom.pdf
      folders:
      - name: modeles
        path: modeles
        rights: [ read ]               # les fichiers que l'outil peut lire
      - name: sortie
        path: sortie
        rights: [ write, overwrite ]   # remplacer un fichier : approbation par défaut
      timezone: Europe/Paris           # par défaut : le fuseau des dates-heures écrites

Les dossiers suivent la forme de ``loom.local_files`` (``name``, ``path``,
``rights``, ``approval``) ; le droit ``delete`` n'a pas de sens ici et est refusé.
"""

from collections.abc import Mapping
from typing import Annotated, Final, Self, cast

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from loom_ia.tools_builtin._office_params import (
    DEFAULT_TIMEZONE,
    Templates,
    check_timezone,
    checked_folders,
    no_delete,
)
from loom_ia.tools_builtin.local_files_params import FolderSpec

REFERENCE: Final = "loom.pdf"
# Plancher de pypdf pour lire un PDF : les versions antérieures bouclent sans fin sur des
# PDF forgés (CVE-2026-59935, -59937). Ici, et non dans l'outil, pour que ``loom.memory``
# le vérifie sans importer reportlab.
SAFE_PYPDF: Final = (6, 14, 2)


def _mapping(value: object) -> Mapping[object, object]:
    """``value`` s'il est une table (le YAML d'un outil, d'un modèle), sinon une table vide."""
    return cast(Mapping[object, object], value) if isinstance(value, Mapping) else {}


class PdfParams(BaseModel):
    """Paramètres de ``loom.pdf``."""

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

    @model_validator(mode="before")
    @classmethod
    def _no_style(cls, value: object) -> object:
        """Les styles ne servent qu'à ``loom.word`` pour l'instant : le dire, pas « extra »."""
        templates = _mapping(value).get("templates")
        styled = "style" in _mapping(value) or any(
            "style" in _mapping(ref) for ref in _mapping(templates).values()
        )
        if styled:
            raise ValueError(
                "style : les styles de document ne servent qu'à loom.word pour l'instant "
                "(loom.pdf écrit dans son style maison) ; retirer style"
            )
        return value

    @model_validator(mode="after")
    def _check(self) -> Self:
        check_timezone(self.timezone)
        return self
