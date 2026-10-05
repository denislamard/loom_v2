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
- ``capture.raw_exchanges`` (6.1b) : en opt-in, chaque échange HTTP avec un
  fournisseur entre **au journal** (``model.exchanged``), sous le sceau et la
  rétention comme le reste ; il ne part jamais vers un collecteur que par ses
  métadonnées.
"""

import re
from typing import Annotated, Final, Literal, Self

from pydantic import Discriminator, Field, PositiveFloat, PositiveInt, Tag, model_validator

from loom_ia.core.model import DomainModel
from loom_ia.telemetry.redaction import BUILTIN_PATTERNS, Redactor

type CaptureLevel = Literal["metadata", "content"]
type OtlpProtocol = Literal["http/protobuf", "grpc"]

EXPORTER_TYPES: Final = ("otel",)
# Variable lue par défaut : celle que nomme la spécification OTLP.
OTLP_ENDPOINT_ENV: Final = "OTEL_EXPORTER_OTLP_ENDPOINT"

# Borne d'un corps brut par défaut : de quoi lire une requête de belle taille
# sans que chaque appel d'une longue session ne pèse des mégaoctets.
RAW_MAX_BYTES: Final = 256 * 1024


class CaptureConfig(DomainModel):
    """Ce qui sort du journal vers un collecteur, et ce qui s'y ajoute en opt-in (§14.2).

    ``raw_exchanges`` ajoute au journal — et non aux exports — chaque requête
    HTTP au fournisseur et sa réponse (``model.exchanged``, 6.1b), corps bornés
    à ``raw_max_bytes`` chacun.
    """

    exports: CaptureLevel = "metadata"
    raw_exchanges: bool = False
    raw_max_bytes: PositiveInt = RAW_MAX_BYTES


class CaptureOverride(DomainModel):
    """Capture propre à un client : chaque clé absente reste celle de la racine."""

    exports: CaptureLevel | None = None
    raw_exchanges: bool | None = None
    raw_max_bytes: PositiveInt | None = None

    def over(self, root: CaptureConfig) -> CaptureConfig:
        return CaptureConfig(
            exports=self.exports if self.exports is not None else root.exports,
            raw_exchanges=(
                self.raw_exchanges if self.raw_exchanges is not None else root.raw_exchanges
            ),
            raw_max_bytes=(
                self.raw_max_bytes if self.raw_max_bytes is not None else root.raw_max_bytes
            ),
        )


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
