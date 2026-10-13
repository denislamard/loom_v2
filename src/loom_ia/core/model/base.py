# SPDX-License-Identifier: Apache-2.0
"""Base commune des types du domaine."""

from pydantic import BaseModel, ConfigDict


class DomainModel(BaseModel):
    """Modèle Pydantic immuable, qui refuse les champs inconnus (#6)."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class Unknown(KeyError):
    """Ce qu'on demande n'existe pas : une ``KeyError`` dont le message se lit tel quel.

    ``str(KeyError("…"))`` rend le message entre guillemets (``repr``) ; ici,
    ``str`` rend le texte, comme pour toute autre exception.
    """

    def __str__(self) -> str:
        return str(self.args[0]) if self.args else super().__str__()
