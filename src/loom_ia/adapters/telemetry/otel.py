# SPDX-License-Identifier: Apache-2.0
"""Les spans de loom, envoyés à un collecteur OpenTelemetry en OTLP (K4, #29).

Une traduction, rien de plus : ``telemetry.spans`` a déjà tout décidé — les
noms, les parents, les durées, ce qui part et ce qui est masqué. Ce module
convertit un ``SpanRecord`` en span OTel **sans passer par un traceur** : les
identifiants viennent du journal, et c'est ce qui fait qu'un run se retrouve
dans le collecteur sous son propre identifiant.

- ``trace_id`` : le ``root_run_id`` de l'arbre (un UUID tient en 128 bits,
  pile ce qu'il faut) ; un sous-agent est donc dans la trace de son parent ;
- ``span_id`` : les 64 bits de poids faible du ``span_id`` de loom (UUIDv7 :
  la partie aléatoire). Un identifiant qui n'est pas un UUID — un run nommé
  par l'appelant — passe par un condensé.

L'envoi est celui du SDK : un ``BatchSpanProcessor`` qui regroupe, envoie
dans son propre fil et réessaie ; ``aclose`` le vide avant de fermer.

Demande l'extra ``otel`` (``opentelemetry-sdk``, ``opentelemetry-exporter-otlp``).
"""

import asyncio
import hashlib
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Final
from urllib.parse import unquote

from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import Event as OtelEvent
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from opentelemetry.sdk.util.instrumentation import InstrumentationScope
from opentelemetry.trace import SpanContext, SpanKind, Status, StatusCode, TraceFlags

from loom_ia.telemetry.spans import SpanRecord

SCOPE: Final = InstrumentationScope("loom_ia")
TRACES_PATH: Final = "/v1/traces"
_SPAN_MASK: Final = (1 << 64) - 1
_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)


def trace_id(value: str) -> int:
    """Identifiant de trace OTel (128 bits) d'un identifiant de loom."""
    try:
        number = uuid.UUID(value).int
    except ValueError:
        number = int.from_bytes(hashlib.blake2b(value.encode(), digest_size=16).digest())
    return number or 1


def span_id(value: str) -> int:
    """Identifiant de span OTel (64 bits) d'un identifiant de loom."""
    try:
        number = uuid.UUID(value).int & _SPAN_MASK
    except ValueError:
        number = int.from_bytes(hashlib.blake2b(value.encode(), digest_size=8).digest())
    return number or 1


def readable(record: SpanRecord, resource: Resource) -> ReadableSpan:
    """Un span de loom, en span OTel prêt à exporter."""
    trace = trace_id(record.trace_id)
    context = SpanContext(
        trace, span_id(record.span_id), is_remote=False, trace_flags=TraceFlags(TraceFlags.SAMPLED)
    )
    parent = (
        None
        if record.parent_span_id is None
        else SpanContext(
            trace,
            span_id(record.parent_span_id),
            is_remote=False,
            trace_flags=TraceFlags(TraceFlags.SAMPLED),
        )
    )
    status = (
        Status(StatusCode.ERROR, record.error)
        if record.error is not None
        else Status(StatusCode.OK)
    )
    return ReadableSpan(
        name=record.name,
        context=context,
        parent=parent,
        resource=resource,
        attributes=dict(record.attributes),
        events=[
            OtelEvent(event.name, dict(event.attributes), _nanos(event.at))
            for event in record.events
        ],
        kind=SpanKind.CLIENT if record.kind == "chat" else SpanKind.INTERNAL,
        status=status,
        start_time=_nanos(record.start),
        end_time=max(_nanos(record.end), _nanos(record.start)),
        instrumentation_scope=SCOPE,
    )


def parse_headers(value: str | None) -> dict[str, str]:
    """En-têtes au format de ``OTEL_EXPORTER_OTLP_HEADERS`` : ``clé=valeur,clé=valeur``."""
    headers: dict[str, str] = {}
    for item in (value or "").split(","):
        key, sep, raw = item.partition("=")
        if sep and key.strip():
            headers[key.strip().lower()] = unquote(raw.strip())
    return headers


def traces_url(endpoint: str) -> str:
    """Adresse d'envoi en OTLP/HTTP : la variable générique nomme la racine du collecteur."""
    trimmed = endpoint.rstrip("/")
    return trimmed if trimmed.endswith(TRACES_PATH) else f"{trimmed}{TRACES_PATH}"


class OtelSpanSink:
    """Un collecteur OTLP, servi par le processeur par lots du SDK."""

    def __init__(
        self,
        exporter: SpanExporter,
        *,
        service_name: str = "loom-ia",
        name: str = "otel",
        schedule_delay: float = 1.0,
    ) -> None:
        self._name = name
        self._resource = Resource.create({"service.name": service_name})
        self._processor = BatchSpanProcessor(exporter, schedule_delay_millis=schedule_delay * 1000)

    @classmethod
    def otlp(
        cls,
        endpoint: str,
        *,
        protocol: str = "http/protobuf",
        headers: Mapping[str, str] | None = None,
        timeout: float = 10.0,
        service_name: str = "loom-ia",
    ) -> OtelSpanSink:
        """Collecteur joint en OTLP, par HTTP (protobuf) ou gRPC."""
        exporter: SpanExporter
        if protocol == "grpc":
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                OTLPSpanExporter as GrpcExporter,
            )

            exporter = GrpcExporter(
                endpoint=endpoint,
                insecure=endpoint.startswith("http://"),
                headers=dict(headers or {}),
                timeout=timeout,
            )
        else:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter as HttpExporter,
            )

            exporter = HttpExporter(
                endpoint=traces_url(endpoint), headers=dict(headers or {}), timeout=timeout
            )
        return cls(exporter, service_name=service_name, name=f"otel {endpoint}")

    @property
    def name(self) -> str:
        return self._name

    def export(self, spans: Sequence[SpanRecord]) -> None:
        for record in spans:
            self._processor.on_end(readable(record, self._resource))

    async def aclose(self, grace: float) -> None:
        # Le SDK attend dans son propre fil : on ne bloque pas la boucle.
        await asyncio.to_thread(self._processor.force_flush, int(grace * 1000))
        await asyncio.to_thread(self._processor.shutdown)


def _nanos(moment: datetime) -> int:
    """Nanosecondes depuis l'époque, sans passer par un flottant (qui arrondit)."""
    return (moment - _EPOCH) // timedelta(microseconds=1) * 1000
