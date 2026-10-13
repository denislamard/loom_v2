# SPDX-License-Identifier: Apache-2.0
"""Les spans de loom, envoyés à un collecteur OpenTelemetry en OTLP (K4, #29).

Une traduction, rien de plus : ``telemetry.spans`` a déjà tout décidé — les
noms, les parents, les durées, ce qui part et ce qui est masqué. Ce module
convertit un ``SpanRecord`` en span OTel **sans passer par un traceur** : les
identifiants viennent du journal, et c'est ce qui fait qu'un run se retrouve
dans le collecteur sous son propre identifiant.

- ``trace_id`` : le ``root_run_id`` de l'arbre (un UUID tient en 128 bits,
  pile ce qu'il faut) ; un sous-agent est donc dans la trace de son parent.
  Seul un UUID écrit sous sa forme canonique — celle des identifiants que loom
  tire — est pris tel quel ; tout autre identifiant (un run nommé par
  l'appelant, un UUID en majuscules ou sans tirets) passe par un condensé qui
  inclut le client : deux clients qui nomment pareil leurs runs ne partagent
  pas une trace ;
- ``span_id`` : les 64 bits de poids faible du ``span_id`` de loom (UUIDv7 :
  la partie aléatoire). Un identifiant qui n'est pas un UUID passe par un
  condensé.

L'envoi est celui du SDK : un ``BatchSpanProcessor`` qui regroupe, envoie
dans son propre fil et réessaie ; ``aclose`` le vide avant de fermer, dans le
délai qu'on lui donne. Sa file tient ``MAX_QUEUE`` spans (le défaut du SDK,
2 048, jetait une rafale de fin de runs) ; ``OTEL_BSP_MAX_QUEUE_SIZE``, s'il
est posé, garde la main. Une valeur d'attribut plus longue que
``OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT`` caractères (``MAX_VALUE_LENGTH`` sans
la variable) est coupée, en le disant.

Demande l'extra ``otel`` (``opentelemetry-sdk``, ``opentelemetry-exporter-otlp``).
"""

import asyncio
import hashlib
import logging
import os
import threading
import uuid
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Final
from urllib.parse import unquote

from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import Event as OtelEvent
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from opentelemetry.sdk.util.instrumentation import InstrumentationScope
from opentelemetry.trace import SpanContext, SpanKind, Status, StatusCode, TraceFlags

from loom_ia.telemetry.spans import AttributeValue, SpanRecord

logger = logging.getLogger(__name__)

SCOPE: Final = InstrumentationScope("loom_ia")
TRACES_PATH: Final = "/v1/traces"
# Spans que la file du processeur garde avant d'en jeter.
MAX_QUEUE: Final = 32_768
QUEUE_ENV: Final = "OTEL_BSP_MAX_QUEUE_SIZE"
# Longueur d'une valeur d'attribut au-delà de laquelle elle est coupée.
MAX_VALUE_LENGTH: Final = 16_384
LENGTH_ENV: Final = "OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT"
_SPAN_MASK: Final = (1 << 64) - 1
_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)
# Ce que garde la fermeture pour ``shutdown`` quand le vidage a pris tout le délai.
_LAST_WORD: Final = 0.1


def trace_id(value: str, tenant_id: str = "") -> int:
    """Identifiant de trace OTel (128 bits) d'un run racine de ce client.

    Un UUID canonique est la trace elle-même ; le reste passe par un condensé
    qui inclut le client.
    """
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        parsed = None
    if parsed is not None and str(parsed) == value:
        return parsed.int or 1
    digest = hashlib.blake2b(f"{tenant_id}\0{value}".encode(), digest_size=16).digest()
    return int.from_bytes(digest) or 1


def span_id(value: str) -> int:
    """Identifiant de span OTel (64 bits) d'un identifiant de loom."""
    try:
        number = uuid.UUID(value).int & _SPAN_MASK
    except ValueError:
        number = int.from_bytes(hashlib.blake2b(value.encode(), digest_size=8).digest())
    return number or 1


def readable(
    record: SpanRecord, resource: Resource, *, max_length: int | None = None
) -> ReadableSpan:
    """Un span de loom, en span OTel prêt à exporter ; ses textes coupés à ``max_length``."""
    trace = trace_id(record.trace_id, str(record.attributes.get("loom.tenant_id", "")))
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
        attributes=_bounded(record.attributes, max_length),
        events=[
            OtelEvent(event.name, _bounded(event.attributes, max_length), _nanos(event.at))
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
        self._max_length = _length_limit()
        # Sans la variable, la file de loom ; avec, celle qu'elle dit (le SDK la lit).
        queue = None if os.environ.get(QUEUE_ENV) else MAX_QUEUE
        self._processor = BatchSpanProcessor(
            exporter, max_queue_size=queue, schedule_delay_millis=schedule_delay * 1000
        )

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
            self._processor.on_end(readable(record, self._resource, max_length=self._max_length))

    async def aclose(self, grace: float) -> None:
        """Vide la file puis ferme, en ``grace`` secondes au plus (et un dixième).

        Ni ``force_flush`` — qui attend l'envoi en cours quel que soit son
        délai — ni ``shutdown`` ne sont bornés : un collecteur muet les tenait
        lot après lot. Ils tournent donc dans un fil démon, que la boucle
        n'attend que le temps donné et qui ne retient pas la sortie du
        process : ce qui n'est pas parti dans le délai est abandonné, et le log
        le dit.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + grace
        try:
            async with asyncio.timeout(grace):
                await _in_daemon_thread(lambda: self._processor.force_flush(int(grace * 1000)))
            async with asyncio.timeout(max(deadline - loop.time(), _LAST_WORD)):
                await _in_daemon_thread(self._processor.shutdown)
        except TimeoutError:
            logger.warning(
                "Collecteur %s : fermeture inachevée en %g s, spans restants abandonnés",
                self._name,
                grace,
            )


def _length_limit() -> int:
    """La longueur maximale d'une valeur : ``OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT``, sinon la nôtre."""
    try:
        limit = int(os.environ.get(LENGTH_ENV, ""))
    except ValueError:
        return MAX_VALUE_LENGTH
    return limit if limit > 0 else MAX_VALUE_LENGTH


def _bounded(
    attributes: Mapping[str, AttributeValue], max_length: int | None
) -> dict[str, AttributeValue]:
    """Les attributs, chaque texte trop long coupé en le disant."""
    if max_length is None:
        return dict(attributes)
    return {key: _cut(value, max_length) for key, value in attributes.items()}


def _cut(value: AttributeValue, max_length: int) -> AttributeValue:
    """Un texte de plus de ``max_length`` caractères, ramené à cette longueur, marque comprise."""
    if not isinstance(value, str) or len(value) <= max_length:
        return value
    said = f"… [coupé : {len(value)} caractères]"
    kept = max_length - len(said)
    return value[:max_length] if kept <= 0 else value[:kept] + said


def _in_daemon_thread(call: Callable[[], object]) -> asyncio.Future[None]:
    """Lance ``call`` dans un fil démon ; le futur se résout quand il a fini."""
    loop = asyncio.get_running_loop()
    done: asyncio.Future[None] = loop.create_future()

    def finished() -> None:
        if not done.done():
            done.set_result(None)

    def run() -> None:
        try:
            call()
        finally:
            # La boucle a pu se fermer entre-temps : plus personne n'attend.
            with suppress(RuntimeError):
                loop.call_soon_threadsafe(finished)

    threading.Thread(target=run, name="loom-otel-fermeture", daemon=True).start()
    return done


def _nanos(moment: datetime) -> int:
    """Nanosecondes depuis l'époque, sans passer par un flottant (qui arrondit)."""
    return (moment - _EPOCH) // timedelta(microseconds=1) * 1000
