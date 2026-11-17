# SPDX-License-Identifier: Apache-2.0
"""Contrats de sortie (fonctions E1, E2, E4 et E5, point 20, conception §17.4).

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
  d'un outil revient à l'orchestrateur en erreur, avec ses problèmes et la
  consigne de ne pas refaire l'appel à l'identique ;
- ``unverified`` : la sortie est gardée, marquée non vérifiée ; la réponse
  finale est gardée normalisée si la normalisation l'a changée (une sortie
  qui ne tenait que par son bloc de code devient vide), la sortie d'un rôle
  ou d'un outil telle qu'elle est arrivée ;
- ``fallback`` : ``fallback_message`` remplace la sortie.

Un contrat se déclare sous ``output`` d'un agent (réponse finale), d'un rôle,
ou d'un outil. Chaque contrôle écrit un ``guard.checked``.

Exemple, sous un agent ::

    output:
      schema_file: schemas/relance.json
      max_chars: 2000
      repair: { max_attempts: 2 }
      on_failure: fallback
      fallback_message: "Je n'ai pas pu rédiger la relance."
"""

from pathlib import Path
from typing import Literal, Self

from jsonschema import SchemaError
from pydantic import ConfigDict, Field, JsonValue, NonNegativeInt, PositiveInt, model_validator

from loom_ia.core.bounded_regex import PatternError, check_schema, compile_pattern
from loom_ia.core.model.base import DomainModel

type OnFailure = Literal["fail", "unverified", "fallback"]
"""Suite donnée à une sortie refusée, une fois les réparations épuisées : voir le module."""
type StreamOutput = Literal["live", "after_guards"]
"""Diffusion de la réponse finale (point 11) : au fil de l'eau (``live``), ou après ses contrôles (``after_guards``)."""
type RepairTools = Literal["auto", "none", "allowed"]
"""Outils de l'orchestrateur pendant la réparation de la réponse finale.

``auto`` les retire pour un échec de forme, le seul qu'un contrat détecte ;
``none`` les retire toujours ; ``allowed`` les garde.
"""


class RepairSettings(DomainModel):
    """Réparations demandées à l'auteur de la sortie refusée (``repair``, point 20).

    Chaque réparation écrit un ``policy.decided`` (``retry``) puis un
    ``message.user`` de ``kind: repair`` qui porte le diagnostic.

    Attributes:
        max_attempts: Nombre de réparations ; 0 n'en demande aucune.
            Défaut : 1.
        tools: Outils de l'orchestrateur pendant la réparation : ``auto``
            (défaut), ``none`` ou ``allowed``.
    """

    max_attempts: NonNegativeInt = 1
    tools: RepairTools = "auto"

    @property
    def keeps_tools(self) -> bool:
        """Vrai si l'orchestrateur garde ses outils pour réparer."""
        return self.tools == "allowed"


class OutputContract(DomainModel):
    """Contrat de sortie : ce qu'une sortie doit respecter (``output``).

    Une sortie vide est toujours refusée. Avec un schéma, la réponse finale
    validée devient la réponse structurée du run (``data``, fonction A7).

    Attributes:
        json_schema: Schéma JSON de la sortie, écrit ``schema`` dans la
            config : elle doit être un JSON conforme. Contrôlé au chargement.
        schema_file: Fichier du schéma, relatif au dossier de la config ; lu
            au chargement. Exclusif de ``schema``.
        must_match: Motif qui doit se trouver dans la sortie (module
            ``regex``, avec un délai d'1 s).
        must_not_match: Motif interdit dans la sortie.
        max_chars: Longueur maximale de la sortie, en caractères.
        normalize: Normalisation déterministe avant le contrôle : blocs de
            code retirés, JSON extrait, espaces nettoyés. Défaut : vrai.
        repair: Réparations demandées (``RepairSettings``) ; une par défaut.
        on_failure: ``fail`` (défaut), ``unverified`` ou ``fallback``.
        fallback_message: Sortie de remplacement quand ``on_failure`` vaut
            ``fallback`` ; obligatoire dans ce cas.

    Raises:
        ValueError: ``schema`` et ``schema_file`` donnés ensemble ; schéma
            invalide ; motif invalide ; ``fallback`` sans
            ``fallback_message``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", validate_by_name=True)

    json_schema: dict[str, JsonValue] | None = Field(default=None, alias="schema")
    schema_file: Path | None = None
    must_match: str | None = None
    must_not_match: str | None = None
    max_chars: PositiveInt | None = None
    normalize: bool = True
    repair: RepairSettings = RepairSettings()
    on_failure: OnFailure = "fail"
    fallback_message: str | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        """Contrôle le schéma et les motifs au chargement, et exige ``fallback_message`` avec ``fallback``."""
        if self.json_schema is not None and self.schema_file is not None:
            raise ValueError("'schema' et 'schema_file' ne peuvent pas être donnés ensemble")
        if self.json_schema is not None:
            try:
                check_schema(self.json_schema)
            except SchemaError as exc:
                raise ValueError(f"schema invalide : {exc.message}") from exc
        for name in ("must_match", "must_not_match"):
            pattern: str | None = getattr(self, name)
            if pattern is not None:
                try:
                    compile_pattern(pattern)
                except PatternError as exc:
                    raise ValueError(f"{name} : expression régulière invalide ({exc})") from exc
        if self.on_failure == "fallback" and not self.fallback_message:
            raise ValueError("on_failure: fallback demande un 'fallback_message'")
        return self
