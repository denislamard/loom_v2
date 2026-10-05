# SPDX-License-Identifier: Apache-2.0
"""Télémétrie de loom-ia : logs, spans tirés du journal, masquage, exports.

Rien ici n'importe OpenTelemetry : les spans sont ceux du journal (#29), et
l'adaptateur ``adapters.telemetry.otel`` les traduit (extra ``otel``).
"""

from loom_ia.telemetry.export import RunExporter, SpanSink
from loom_ia.telemetry.logs import configure_logging
from loom_ia.telemetry.redaction import BUILTIN_PATTERNS, Redactor
from loom_ia.telemetry.spans import SpanEvent, SpanRecord, finished, run_spans

__all__ = [
    "BUILTIN_PATTERNS",
    "Redactor",
    "RunExporter",
    "SpanEvent",
    "SpanRecord",
    "SpanSink",
    "configure_logging",
    "finished",
    "run_spans",
]
