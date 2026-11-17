# SPDX-License-Identifier: Apache-2.0
"""Télémétrie de loom-ia : logs, spans tirés du journal, masquage, exports, traces à relire.

Rien ici n'importe OpenTelemetry : les spans sont ceux du journal (point 29), et
l'adaptateur ``adapters.telemetry.otel`` les traduit (extra ``otel``).

- ``logs`` : ``configure_logging``, formats ``console`` et ``json`` ;
- ``spans`` : les spans d'un run, tirés de son journal (``run_spans``) ;
- ``export`` : ``RunExporter``, qui remet les runs clos aux collecteurs ;
- ``redaction`` : ``Redactor``, masquage par motifs du contenu exporté ;
- ``trace`` et ``inspect`` : la trace d'un run à relire (``loom inspect``,
  ``GET /v1/runs/{id}/trace``).

Config : ``telemetry`` (``logging``, ``capture``, ``redaction``,
``exporters``).
"""

from loom_ia.telemetry.export import RunExporter, SpanSink
from loom_ia.telemetry.inspect import render_trace
from loom_ia.telemetry.logs import configure_logging
from loom_ia.telemetry.redaction import BUILTIN_PATTERNS, Redactor
from loom_ia.telemetry.spans import SpanEvent, SpanRecord, finished, run_spans
from loom_ia.telemetry.trace import Trace, TraceEvent, TraceSpan, run_trace

__all__ = [
    "BUILTIN_PATTERNS",
    "Redactor",
    "RunExporter",
    "SpanEvent",
    "SpanRecord",
    "SpanSink",
    "Trace",
    "TraceEvent",
    "TraceSpan",
    "configure_logging",
    "finished",
    "render_trace",
    "run_spans",
    "run_trace",
]
