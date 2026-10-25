# SPDX-License-Identifier: Apache-2.0
"""Paramètres de ``loom.local_files`` : les dossiers permis, et pour chacun ses droits.

Module léger — Pydantic seulement — : la config l'importe pour valider la
référence au chargement, sans importer l'outil.

    - python: loom.local_files
      folders:
      - name: work
        path: /home/denis/temp/test
        rights: { read: never, write: never, overwrite: never, delete: never }
      - name: devis
        path: devis
        rights: [ read, write ]        # forme courte : approbations par défaut

``rights`` est une table **droit → approbation** : un droit présent est
accordé, et sa valeur dit si chaque appel qui l'exerce attend un humain. En
liste, chaque droit prend son approbation par défaut : ``read`` et ``write``
à ``never``, ``overwrite`` et ``delete`` à ``always``.
"""

from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Final, Literal, Self, cast

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    WithJsonSchema,
    model_validator,
)

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


def _short_form(value: object) -> object:
    """Une liste de droits devient la table, chacun avec son approbation par défaut."""
    if not isinstance(value, list | tuple):
        return value
    given = [str(item) for item in cast("list[object] | tuple[object, ...]", value)]
    doubles = sorted({right for right in given if given.count(right) > 1})
    if doubles:
        raise ValueError(f"droit donné deux fois : {', '.join(doubles)}")
    # Un droit inconnu garde une valeur quelconque : la clé échoue ensuite, nommée.
    return {right: DEFAULT_APPROVAL.get(cast(Right, right), "never") for right in given}


_RIGHTS_SCHEMA: Final = {
    "description": (
        "Droits accordés sur le dossier : table droit → approbation (never ou always), "
        "ou liste de droits avec leur approbation par défaut (read et write : never ; "
        "overwrite et delete : always)."
    ),
    "anyOf": [
        {
            "type": "object",
            "propertyNames": {"enum": list(RIGHTS)},
            "additionalProperties": {"enum": ["never", "always"]},
            "minProperties": 1,
        },
        {
            "type": "array",
            "items": {"enum": list(RIGHTS)},
            "uniqueItems": True,
            "minItems": 1,
        },
    ],
}


class FolderSpec(BaseModel):
    """Un dossier permis : son nom pour le modèle, son chemin, ses droits."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # Ce que voit le modèle : il désigne un fichier par « <name>/<chemin> », et
    # le chemin réel ne lui est jamais montré.
    name: str = Field(pattern=FOLDER_NAME_PATTERN)
    # Absolu, ou relatif au dossier du fichier de config. Ni « ~ » ni variable
    # (« $HOME ») : la config n'interprète rien.
    path: Path
    rights: Annotated[
        dict[Right, RightApproval],
        BeforeValidator(_short_form),
        WithJsonSchema(_RIGHTS_SCHEMA),
    ]

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
