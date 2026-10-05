# SPDX-License-Identifier: Apache-2.0
"""Ce que les exports emportent, ce qu'ils masquent, où ils vont (K3, K4, #29, #30, §17.7).

Le journal garde tout, toujours (#30) : rien ici ne change ce qui y est écrit.
Ces réglages disent ce qui **sort** du journal vers un collecteur.

- ``capture.exports`` : ``metadata`` (le défaut) n'emporte que l'enveloppe et
  ce qui n'est pas du contenu — types, statuts, durées, coûts, modèles, noms
  d'outils ; ``content`` emporte aussi les champs que chaque charge déclare
  comme contenu (``content_fields``), après masquage. Un client peut avoir
  sa propre valeur (``tenants[].telemetry.capture``).
- ``redaction.patterns`` : ce qui est masqué dans le contenu exporté. Trois
  motifs fournis (``email``, ``phone``, ``iban``) et des motifs à soi
  (``{name, regex}``). Le masquage ne touche que les exports : le journal et
  l'API (portée ``read_content``) ne le voient pas.
- ``exporters`` : les collecteurs. Un seul type à ce jalon, ``otel`` (OTLP).
"""

import re
from typing import Annotated, Final, Literal, Self

from pydantic import Discriminator, Field, PositiveFloat, Tag, model_validator

from loom_ia.core.model import DomainModel, reject_later
from loom_ia.telemetry.redaction import BUILTIN_PATTERNS, Redactor

type CaptureLevel = Literal["metadata", "content"]
type OtlpProtocol = Literal["http/protobuf", "grpc"]

EXPORTER_TYPES: Final = ("otel",)
# Variable lue par défaut : celle que nomme la spécification OTLP.
OTLP_ENDPOINT_ENV: Final = "OTEL_EXPORTER_OTLP_ENDPOINT"

# Clés de ``capture`` prévues pour une phase suivante.
LATER_CAPTURE: Final[dict[str, str]] = {"raw_exchanges": "J6.1b (échanges bruts)"}


class CaptureConfig(DomainModel):
    """Niveau de détail des exports (§14.2)."""

    exports: CaptureLevel = "metadata"

    @model_validator(mode="before")
    @classmethod
    def _later(cls, data: object) -> object:
        reject_later(data, LATER_CAPTURE)
        return data


class CaptureOverride(DomainModel):
    """Capture propre à un client : chaque clé absente reste celle de la racine."""

    exports: CaptureLevel | None = None

    @model_validator(mode="before")
    @classmethod
    def _later(cls, data: object) -> object:
        reject_later(data, LATER_CAPTURE)
        return data

    def over(self, root: CaptureConfig) -> CaptureConfig:
        return CaptureConfig(exports=self.exports if self.exports is not None else root.exports)


class TenantTelemetry(DomainModel):
    """Ce qu'un client surcharge de la télémétrie : sa capture, rien d'autre.

    Les motifs de masquage et les collecteurs sont ceux du déploiement : un
    client choisit si son contenu part, pas où il va.
    """

    capture: CaptureOverride = CaptureOverride()


class RedactionPattern(DomainModel):
    """Motif de masquage déclaré par la config : un nom, une expression régulière."""

    name: str = Field(min_length=1)
    regex: str = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.name in BUILTIN_PATTERNS:
            raise ValueError(
                f"Masquage : {self.name!r} est un motif fourni — le citer par son nom, ou "
                "donner un autre nom à celui-ci"
            )
        try:
            compiled = re.compile(self.regex)
        except re.error as error:
            raise ValueError(f"Masquage {self.name!r} : expression invalide ({error})") from error
        if compiled.search(""):
            # Un motif qui accepte le vide masquerait entre chaque caractère.
            raise ValueError(f"Masquage {self.name!r} : l'expression accepte une chaîne vide")
        return self


def _pattern_kind(value: object) -> str:
    return "builtin" if isinstance(value, str) else "own"


# Un nom de motif fourni, ou un motif à soi : la forme décide de la branche,
# pour que l'erreur porte sur celle-là seule.
type DeclaredPattern = Annotated[
    Annotated[str, Tag("builtin")] | Annotated[RedactionPattern, Tag("own")],
    Discriminator(_pattern_kind),
]


class RedactionConfig(DomainModel):
    """Motifs masqués dans le contenu exporté, dans l'ordre déclaré."""

    patterns: tuple[DeclaredPattern, ...] = BUILTIN_PATTERNS

    @model_validator(mode="after")
    def _check(self) -> Self:
        names: list[str] = []
        for pattern in self.patterns:
            if isinstance(pattern, str):
                if pattern not in BUILTIN_PATTERNS:
                    raise ValueError(
                        f"Masquage : motif {pattern!r} inconnu (fournis : "
                        f"{', '.join(BUILTIN_PATTERNS)} ; ou {{name, regex}} pour le sien)"
                    )
                names.append(pattern)
            else:
                names.append(pattern.name)
        doubles = sorted({name for name in names if names.count(name) > 1})
        if doubles:
            raise ValueError(f"Masquage : motif déclaré deux fois : {', '.join(doubles)}")
        return self

    def redactor(self) -> Redactor:
        return Redactor.of(p if isinstance(p, str) else (p.name, p.regex) for p in self.patterns)


class ExporterConfig(DomainModel):
    """Un collecteur OpenTelemetry, joint en OTLP (#29).

    La config nomme les **variables** qui portent l'adresse et les en-têtes,
    jamais leurs valeurs : un en-tête d'authentification est un secret. Les
    en-têtes s'écrivent comme ``OTEL_EXPORTER_OTLP_HEADERS``
    (``clé=valeur,clé=valeur``).
    """

    type: str
    endpoint_env: str = OTLP_ENDPOINT_ENV
    protocol: OtlpProtocol = "http/protobuf"
    headers_env: str | None = None
    service_name: str = Field(default="loom-ia", min_length=1)
    # Délai d'un envoi au collecteur, en secondes.
    timeout: PositiveFloat = 10.0

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.type not in EXPORTER_TYPES:
            raise ValueError(
                f"Exporteur {self.type!r} : seul {', '.join(map(repr, EXPORTER_TYPES))} existe à "
                "ce jalon (le journal lui-même s'exporte par 'loom sessions export')"
            )
        return self
