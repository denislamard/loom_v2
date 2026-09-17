# SPDX-License-Identifier: Apache-2.0
"""Configuration optionnelle des logs de loom-ia (#45).

La librairie n'installe aucun handler : elle écrit dans les loggers
``loom_ia.*`` et laisse l'application hôte décider de l'affichage.
``configure_logging`` sert à la CLI et au mode service, qui sont eux-mêmes
l'application.

Le contexte d'exécution passe par ``extra`` :

    logger.info("appel modèle", extra={"run_id": run_id, "span_id": span_id})

Les champs ``run_id``, ``span_id`` et ``tenant_id`` sont repris par les deux
formats quand ils sont présents.
"""

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Final, Literal, TextIO, get_args

type LogFormat = Literal["console", "json"]

CONTEXT_FIELDS: Final = ("run_id", "span_id", "tenant_id")
ROOT_LOGGER: Final = "loom_ia"

# Marqueur posé sur le handler installé par configure_logging, pour le
# retrouver et le remplacer lors d'un nouvel appel (idempotence).
_HANDLER_MARK: Final = "_loom_ia_configured"


def _context(record: logging.LogRecord) -> dict[str, object]:
    """Champs de contexte présents dans l'enregistrement (via ``extra``)."""
    context: dict[str, object] = {}
    for field in CONTEXT_FIELDS:
        value: object = getattr(record, field, None)
        if value is not None:
            context[field] = value
    return context


class ConsoleFormatter(logging.Formatter):
    """Une ligne lisible : horodatage, niveau, logger, message, contexte."""

    def __init__(self) -> None:
        super().__init__(
            fmt="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        context = _context(record)
        if not context:
            return line
        suffix = " ".join(f"{key}={value}" for key, value in context.items())
        # L'éventuelle trace d'exception suit la première ligne : le contexte
        # reste sur la ligne du message.
        first, sep, rest = line.partition("\n")
        return f"{first} [{suffix}]{sep}{rest}"


class JsonFormatter(logging.Formatter):
    """Un objet JSON par ligne, pour les collecteurs de logs."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        payload.update(_context(record))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(
    level: int | str = logging.INFO,
    *,
    format: LogFormat = "console",
    stream: TextIO | None = None,
) -> logging.Handler:
    """Affiche les logs de loom-ia sur ``stream`` (stderr par défaut).

    Seul le logger ``loom_ia`` est configuré : le logger racine de
    l'application n'est pas touché. La propagation est coupée pour éviter
    les doublons si l'application a aussi configuré le logger racine.

    Un nouvel appel remplace le handler installé par l'appel précédent.
    Renvoie le handler installé.
    """
    formatters: dict[str, type[logging.Formatter]] = {
        "console": ConsoleFormatter,
        "json": JsonFormatter,
    }
    if format not in formatters:
        allowed = ", ".join(get_args(LogFormat.__value__))
        raise ValueError(f"Format de log inconnu : {format!r} (attendu : {allowed})")

    logger = logging.getLogger(ROOT_LOGGER)
    for existing in list(logger.handlers):
        if getattr(existing, _HANDLER_MARK, False):
            logger.removeHandler(existing)
            existing.close()

    handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
    handler.setFormatter(formatters[format]())
    setattr(handler, _HANDLER_MARK, True)

    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    return handler
