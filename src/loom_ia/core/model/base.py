# SPDX-License-Identifier: Apache-2.0
"""Base commune des types du domaine, et refus des clés à venir."""

from collections.abc import Mapping
from typing import cast

from pydantic import BaseModel, ConfigDict


class DomainModel(BaseModel):
    """Modèle Pydantic immuable, qui refuse les champs inconnus (#6)."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class UnsupportedKey(ValueError):
    """Clé prévue par le schéma, mais pas encore prise en charge.

    Sert aux modèles dont le schéma complet est plus large que ce qui est
    réalisé : le message nomme la phase qui apportera la clé, au lieu du
    « champ inconnu » de Pydantic.
    """

    def __init__(self, key: str, phase: str) -> None:
        super().__init__(f"{key!r} : prévu pour le jalon {phase}, pas encore pris en charge")
        self.key = key
        self.phase = phase


def reject_later(data: object, later: Mapping[str, str]) -> None:
    """Refuse les clés d'un bloc prévu pour plus tard, en nommant son jalon."""
    if not isinstance(data, dict):
        return
    for key in cast(dict[object, object], data):
        phase = later.get(str(key))
        if phase is not None:
            raise UnsupportedKey(str(key), phase)
