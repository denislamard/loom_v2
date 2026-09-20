# SPDX-License-Identifier: Apache-2.0
"""Contrats de sortie (E1, E2, E4, E5, #20, §17.4).

Un contrat décrit ce qu'une sortie doit respecter : un schéma JSON, des
motifs, une longueur, et toujours une sortie non vide. Il s'applique à la
réponse finale d'un agent, à la sortie d'un rôle, ou au résultat d'un outil
Python ou MCP.

Avant le contrôle, une normalisation déterministe corrige ce qui peut l'être
sans appel au modèle (``normalize``). Ensuite, en cas d'échec, l'auteur de la
sortie la répare (``repair``) : l'orchestrateur pour la réponse finale, le
modèle du rôle pour un rôle ; un outil ne se répare pas. Quand les
réparations sont épuisées, ``on_failure`` décide :

- ``fail`` : la réponse finale fait échouer le run ; la sortie d'un rôle ou
  d'un outil revient à l'orchestrateur en erreur, avec le diagnostic ;
- ``unverified`` : la sortie est gardée telle quelle, marquée non vérifiée ;
- ``fallback`` : ``fallback_message`` remplace la sortie.
"""

import re
from pathlib import Path
from typing import Literal, Self

from jsonschema import Draft202012Validator, SchemaError
from jsonschema.validators import validator_for
from pydantic import ConfigDict, Field, JsonValue, NonNegativeInt, PositiveInt, model_validator

from loom_ia.core.model.base import DomainModel

type OnFailure = Literal["fail", "unverified", "fallback"]
# Diffusion de la réponse finale (#11) : au fil de l'eau, ou après ses contrôles.
type StreamOutput = Literal["live", "after_guards"]
# Outils de l'orchestrateur pendant la réparation de la réponse finale :
# ``auto`` les retire pour un échec de forme, le seul qu'un contrat détecte.
type RepairTools = Literal["auto", "none", "allowed"]


class RepairSettings(DomainModel):
    """Réparations demandées à l'auteur de la sortie (#20)."""

    # Nombre de réparations ; 0 n'en demande aucune.
    max_attempts: NonNegativeInt = 1
    tools: RepairTools = "auto"

    @property
    def keeps_tools(self) -> bool:
        """Vrai si l'orchestrateur garde ses outils pour réparer."""
        return self.tools == "allowed"


class OutputContract(DomainModel):
    """Ce qu'une sortie doit respecter."""

    model_config = ConfigDict(frozen=True, extra="forbid", validate_by_name=True)

    # Schéma JSON de la sortie : elle doit être un JSON conforme.
    json_schema: dict[str, JsonValue] | None = Field(default=None, alias="schema")
    # Fichier du schéma, relatif au dossier de la config ; lu au chargement.
    schema_file: Path | None = None
    # Motif qui doit se trouver dans la sortie (``re.search``).
    must_match: str | None = None
    # Motif interdit dans la sortie.
    must_not_match: str | None = None
    max_chars: PositiveInt | None = None
    # Normalisation déterministe avant le contrôle : blocs de code retirés,
    # JSON extrait, espaces nettoyés.
    normalize: bool = True
    repair: RepairSettings = RepairSettings()
    on_failure: OnFailure = "fail"
    # Sortie de remplacement quand ``on_failure`` vaut ``fallback``.
    fallback_message: str | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.json_schema is not None and self.schema_file is not None:
            raise ValueError("'schema' et 'schema_file' ne peuvent pas être donnés ensemble")
        if self.json_schema is not None:
            try:
                validator_for(self.json_schema, default=Draft202012Validator).check_schema(
                    self.json_schema
                )
            except SchemaError as exc:
                raise ValueError(f"schema invalide : {exc.message}") from exc
        for name in ("must_match", "must_not_match"):
            pattern: str | None = getattr(self, name)
            if pattern is not None:
                try:
                    re.compile(pattern)
                except re.error as exc:
                    raise ValueError(f"{name} : expression régulière invalide ({exc})") from exc
        if self.on_failure == "fallback" and not self.fallback_message:
            raise ValueError("on_failure: fallback demande un 'fallback_message'")
        return self
