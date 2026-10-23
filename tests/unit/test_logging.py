# SPDX-License-Identifier: Apache-2.0
"""Logs de la librairie : silence par défaut, configuration optionnelle (#45)."""

import io
import json
import logging
import subprocess
import sys

import pytest
from conftest import HOSTILE, HOSTILE_SHOWN

# Importer un sous-module exécute d'abord loom_ia/__init__.py (NullHandler).
from loom_ia.telemetry import configure_logging

ROOT = logging.getLogger("loom_ia")


def test_null_handler_is_installed() -> None:
    assert any(isinstance(h, logging.NullHandler) for h in ROOT.handlers)


def test_library_is_silent_without_configuration() -> None:
    # Process séparé : pytest installe ses propres handlers sur le logger racine.
    code = (
        "import logging, loom_ia; "
        "logging.getLogger('loom_ia.engine').warning('ne doit pas apparaître')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert result.stdout == ""
    assert result.stderr == ""


def test_console_format_includes_present_context_only() -> None:
    stream = io.StringIO()
    configure_logging("INFO", format="console", stream=stream)

    logging.getLogger("loom_ia.engine").info(
        "appel modèle", extra={"run_id": "r-1", "tenant_id": "dupont"}
    )

    line = stream.getvalue().strip()
    assert "INFO" in line
    assert "loom_ia.engine" in line
    assert line.endswith("appel modèle [run_id=r-1 tenant_id=dupont]")
    assert "span_id" not in line


def test_console_format_without_context_has_no_suffix() -> None:
    stream = io.StringIO()
    configure_logging(format="console", stream=stream)

    logging.getLogger("loom_ia").info("démarrage")

    assert stream.getvalue().strip().endswith("— démarrage")


def test_console_format_shows_control_characters_without_obeying_them() -> None:
    """Un message qui cite un modèle, un outil ou un serveur ne pilote pas le terminal."""
    stream = io.StringIO()
    configure_logging(format="console", stream=stream)

    logging.getLogger("loom_ia.engine").warning(
        "outil inconnu : %s", HOSTILE, extra={"run_id": "r\x1b-1"}
    )

    assert stream.getvalue().endswith(f"outil inconnu : {HOSTILE_SHOWN} [run_id=r\\x1b-1]\n")


def test_json_format_is_one_object_per_line() -> None:
    stream = io.StringIO()
    configure_logging("DEBUG", format="json", stream=stream)

    log = logging.getLogger("loom_ia.models")
    log.debug("requête", extra={"run_id": "r-2", "span_id": "s-9"})
    log.warning("retry %d", 2)

    lines = stream.getvalue().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    second = json.loads(lines[1])

    assert first["level"] == "DEBUG"
    assert first["logger"] == "loom_ia.models"
    assert first["message"] == "requête"
    assert first["run_id"] == "r-2"
    assert first["span_id"] == "s-9"
    assert "tenant_id" not in first
    assert first["ts"].endswith("+00:00")

    assert second["message"] == "retry 2"
    assert "run_id" not in second


def test_json_format_includes_exception() -> None:
    stream = io.StringIO()
    configure_logging(format="json", stream=stream)

    try:
        raise RuntimeError("panne simulée")
    except RuntimeError:
        logging.getLogger("loom_ia").exception("échec")

    payload = json.loads(stream.getvalue())
    assert payload["level"] == "ERROR"
    assert "RuntimeError: panne simulée" in payload["exception"]


def test_level_filters_messages() -> None:
    stream = io.StringIO()
    configure_logging(logging.WARNING, stream=stream)

    log = logging.getLogger("loom_ia")
    log.info("ignoré")
    log.warning("affiché")

    output = stream.getvalue()
    assert "ignoré" not in output
    assert "affiché" in output


def test_configure_logging_is_idempotent() -> None:
    first = io.StringIO()
    second = io.StringIO()
    configure_logging(stream=first)
    configure_logging(stream=second)

    logging.getLogger("loom_ia").info("une seule fois")

    assert first.getvalue() == ""
    assert second.getvalue().count("une seule fois") == 1
    assert ROOT.propagate is False


def test_the_sdk_logs_take_the_format_and_level_of_loom() -> None:
    """Test K4 du 21/10 : les lignes du SDK OpenTelemetry sortaient brutes, hors de loom."""
    stream = io.StringIO()
    configure_logging(logging.WARNING, stream=stream)
    sdk = logging.getLogger("opentelemetry.sdk.trace.export")
    sdk.info("ignoré")
    sdk.warning("Queue is full, likely spans will be dropped.")
    assert stream.getvalue().count("WARNING  opentelemetry.sdk.trace.export — Queue is full") == 1
    assert "ignoré" not in stream.getvalue()
    assert logging.getLogger("opentelemetry").propagate is False


def test_unknown_format_is_rejected() -> None:
    with pytest.raises(ValueError, match="Format de log inconnu"):
        configure_logging(format="xml")  # pyright: ignore[reportArgumentType]
