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

Le format ``console`` est lu dans un terminal : un message qui cite un modèle,
un outil ou un serveur y montre ses caractères de contrôle au lieu de les
exécuter (``printable``, qui sert aussi à la CLI). Le format ``json`` est pour
les machines, et JSON échappe déjà les contrôles C0.
"""

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Final, Literal, TextIO, get_args

type LogFormat = Literal["console", "json"]

CONTEXT_FIELDS: Final = ("run_id", "span_id", "tenant_id")
ROOT_LOGGER: Final = "loom_ia"
# Le SDK OpenTelemetry, qui envoie les spans aux collecteurs.
SDK_LOGGER: Final = "opentelemetry"

# Marqueur posé sur le handler installé par configure_logging, pour le
# retrouver et le remplacer lors d'un nouvel appel (idempotence).
_HANDLER_MARK: Final = "_loom_ia_configured"

# Caractères qu'un terminal exécute au lieu de les afficher — C0 sauf la
# tabulation et le saut de ligne, DEL, C1 —, et ce qui les montre à leur place.
_CONTROLS: Final = {
    code: f"\\x{code:02x}" for code in (*range(0x09), *range(0x0B, 0x20), *range(0x7F, 0xA0))
}


def printable(text: str) -> str:
    """Le texte pour un terminal : chaque caractère de contrôle montré (``\\x1b``), pas exécuté.

    Ce que rendent un modèle, un outil ou un serveur peut porter des séquences
    qui pilotent le terminal (presse-papier, écran, titre). Montrées, elles se
    lisent sans agir. ``\\n`` et ``\\t`` restent ce qu'ils sont.
    """
    return text.translate(_CONTROLS)


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
            return printable(line)
        suffix = " ".join(f"{key}={value}" for key, value in context.items())
        # L'éventuelle trace d'exception suit la première ligne : le contexte
        # reste sur la ligne du message.
        first, sep, rest = line.partition("\n")
        return printable(f"{first} [{suffix}]{sep}{rest}")


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

    Seuls les loggers ``loom_ia`` et ``opentelemetry`` sont configurés : le
    logger racine de l'application n'est pas touché. Le second est celui du
    SDK qui envoie les spans aux collecteurs (``telemetry.exporters``) : ses
    lignes prennent le format et le niveau de loom (au test K4 du 21/10, elles
    sortaient brutes, hors de ``telemetry.logging``). La propagation est
    coupée pour éviter les doublons si l'application a aussi configuré le
    logger racine.

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

    handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
    handler.setFormatter(formatters[format]())
    setattr(handler, _HANDLER_MARK, True)
    for name in (ROOT_LOGGER, SDK_LOGGER):
        logger = logging.getLogger(name)
        for existing in list(logger.handlers):
            if getattr(existing, _HANDLER_MARK, False):
                logger.removeHandler(existing)
                existing.close()
        logger.addHandler(handler)
        logger.setLevel(level)
        logger.propagate = False
    return handler
