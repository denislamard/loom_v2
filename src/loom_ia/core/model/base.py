# SPDX-License-Identifier: Apache-2.0
"""Base commune des types du domaine : ``DomainModel`` et l'exception ``Unknown``."""

from pydantic import BaseModel, ConfigDict


class DomainModel(BaseModel):
    """Modèle Pydantic immuable, qui refuse les champs inconnus (point 6).

    Base de tous les types du domaine et de la config : une faute de frappe
    dans une clé YAML est une erreur, jamais une option ignorée. Une copie
    modifiée se fait par ``model_copy(update=…)``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")


class Unknown(KeyError):
    """Ce qu'on demande n'existe pas : une ``KeyError`` dont le message se lit tel quel.

    ``str(KeyError("…"))`` rend le message entre guillemets (``repr``) ; ici,
    ``str`` rend le texte, comme pour toute autre exception.
    """

    def __str__(self) -> str:
        """Le message tel quel, sans les guillemets d'une ``KeyError``."""
        return str(self.args[0]) if self.args else super().__str__()
