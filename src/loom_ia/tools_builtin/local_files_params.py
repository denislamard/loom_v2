# SPDX-License-Identifier: Apache-2.0
"""Paramètres de ``loom.local_files`` : les dossiers permis, et pour chacun ses droits.

Module léger — Pydantic seulement — : la config l'importe pour valider la
référence au chargement, sans importer l'outil.

    - python: loom.local_files
      folders:
      - name: work
        path: /home/denis/temp/test
        rights: [ read, write, overwrite, delete ]
        approval: { overwrite: never, delete: never }   # rien ne bloque
      - name: devis
        path: devis
        rights: [ read, write ]        # approbations par défaut

``rights`` liste les droits **accordés** ; un droit absent est refusé.
``approval``, facultatif, dit lesquels attendent un humain : ``never`` (l'appel
passe) ou ``always`` (validation humaine). Un droit qui n'y figure pas garde son
approbation par défaut : ``read`` et ``write`` à ``never``, ``overwrite`` et
``delete`` à ``always``. Le modèle garde la forme que lit l'outil, ``rights``
en table droit → approbation (``_rights``).
"""

from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Any, Final, Literal, Self, cast

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    WithJsonSchema,
    model_validator,
)

from loom_ia.tools_builtin._rights import resolve

type Right = Literal["read", "write", "overwrite", "delete"]
type RightApproval = Literal["never", "always"]

# Dans l'ordre où on les dit.
RIGHTS: Final[tuple[Right, ...]] = ("read", "write", "overwrite", "delete")
DEFAULT_APPROVAL: Final[Mapping[Right, RightApproval]] = {
    "read": "never",
    "write": "never",
    "overwrite": "always",
    "delete": "always",
}
FOLDER_NAME_PATTERN: Final = r"^[a-z0-9][a-z0-9_-]{0,31}$"


_RIGHTS_SCHEMA: Final = {
    "description": (
        "Droits accordés sur le dossier ; un droit absent est refusé. Leur approbation "
        "se règle dans 'approval' (par défaut, read et write : never ; overwrite et "
        "delete : always)."
    ),
    "type": "array",
    "items": {"enum": list(RIGHTS)},
    "uniqueItems": True,
    "minItems": 1,
}
_APPROVAL_SCHEMA: Final = {
    "description": (
        "Approbation de chaque droit accordé : never (l'appel passe) ou always "
        "(validation humaine) ; un droit absent garde son défaut."
    ),
    "type": "object",
    "propertyNames": {"enum": list(RIGHTS)},
    "additionalProperties": {"enum": ["never", "always"]},
}


def _with_approval(schema: dict[str, Any]) -> None:
    # ``approval`` n'est pas un champ : la normalisation le consomme. Le schéma
    # le montre quand même, sans quoi un éditeur le refuserait.
    properties = cast(dict[str, Any], schema.setdefault("properties", {}))
    properties["approval"] = _APPROVAL_SCHEMA


class FolderSpec(BaseModel):
    """Un dossier permis : son nom pour le modèle, son chemin, ses droits."""

    model_config = ConfigDict(frozen=True, extra="forbid", json_schema_extra=_with_approval)

    # Ce que voit le modèle : il désigne un fichier par « <name>/<chemin> », et
    # le chemin réel ne lui est jamais montré.
    name: str = Field(pattern=FOLDER_NAME_PATTERN)
    # Absolu, ou relatif au dossier du fichier de config. Ni « ~ » ni variable
    # (« $HOME ») : la config n'interprète rien.
    path: Path
    # En entrée, une liste et ``approval`` ; ici, la table droit → approbation.
    rights: Annotated[dict[Right, RightApproval], WithJsonSchema(_RIGHTS_SCHEMA)]

    @model_validator(mode="before")
    @classmethod
    def _resolve(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        fields = dict(cast(dict[str, object], data))
        approval = fields.pop("approval", None)
        if "rights" in fields:
            fields["rights"] = resolve(
                fields["rights"],
                approval,
                known=RIGHTS,
                defaults=DEFAULT_APPROVAL,
                where=f"dossier {fields.get('name')!r}",
                form="folder",
            )
        return fields

    @model_validator(mode="after")
    def _check(self) -> Self:
        said = str(self.path)
        if said.startswith("~") or "$" in said:
            raise ValueError(
                f"dossier {self.name!r} : {said!r} — ni « ~ » ni variable ($HOME…) dans "
                "'path', qui n'est pas interprété : donner un chemin absolu, ou relatif "
                "au fichier de config"
            )
        if not self.rights:
            raise ValueError(f"dossier {self.name!r} : 'rights' ne peut pas être vide")
        if "overwrite" in self.rights and "write" not in self.rights:
            raise ValueError(
                f"dossier {self.name!r} : 'overwrite' (remplacer un fichier) exige 'write'"
            )
        return self

    def approval(self, right: Right) -> RightApproval | None:
        """L'approbation du droit, ou ``None`` s'il n'est pas accordé."""
        return self.rights.get(right)


def _unique_names(folders: tuple[FolderSpec, ...]) -> tuple[FolderSpec, ...]:
    # Après la validation des dossiers : un dossier fautif ne fait pas dire en plus
    # « au moins un dossier ».
    if not folders:
        raise ValueError("au moins un dossier est attendu")
    names = [folder.name for folder in folders]
    doubles = sorted({name for name in names if names.count(name) > 1})
    if doubles:
        raise ValueError(f"nom de dossier donné deux fois : {', '.join(doubles)}")
    return folders


class LocalFilesParams(BaseModel):
    """Paramètres de ``loom.local_files``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    folders: Annotated[
        tuple[FolderSpec, ...],
        AfterValidator(_unique_names),
        Field(json_schema_extra={"minItems": 1}),
    ]
