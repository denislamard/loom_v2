# SPDX-License-Identifier: Apache-2.0
"""Logs de la librairie : silence par défaut, configuration optionnelle (#45)."""

import io
import json
import logging
import subprocess
import sys
from collections.abc import Iterator

import pytest

# Importer un sous-module exécute d'abord loom_ia/__init__.py (NullHandler).
from loom_ia.telemetry import configure_logging

ROOT = logging.getLogger("loom_ia")


@pytest.fixture(autouse=True)
def restore_root_logger() -> Iterator[None]:
    """Remet le logger ``loom_ia`` dans son état initial après chaque test."""
    handlers = list(ROOT.handlers)
    level = ROOT.level
    propagate = ROOT.propagate
    yield
    for handler in list(ROOT.handlers):
        if handler not in handlers:
            ROOT.removeHandler(handler)
            handler.close()
    ROOT.setLevel(level)
    ROOT.propagate = propagate


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


def test_unknown_format_is_rejected() -> None:
    with pytest.raises(ValueError, match="Format de log inconnu"):
        configure_logging(format="xml")  # pyright: ignore[reportArgumentType]
