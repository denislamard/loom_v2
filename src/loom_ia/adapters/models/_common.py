# SPDX-License-Identifier: Apache-2.0
"""Outils communs aux adaptateurs de modèles : classement des erreurs, conversions.

Ce module n'importe aucun SDK.

Images (#14) : le moteur remplace chaque référence de fichier avant l'appel,
par les octets de l'image (``inline_data``) ou par une mention. Un adaptateur
ne reçoit donc jamais de ``ArtifactRefBlock`` ; s'il en reçoit un, c'est une
erreur de montage.
"""

import base64
import json
import re
import time
from collections.abc import Mapping
from email.utils import parsedate_to_datetime
from typing import Final

from pydantic import JsonValue

from loom_ia.core.model import (
    ArtifactRefBlock,
    InlineDataBlock,
    JsonBlock,
    ModelErrorKind,
    OutputBlock,
    TextBlock,
    ToolOutput,
    is_image,
)
from loom_ia.core.ports import ModelError

# Préfixe d'un résultat d'outil en erreur, pour les API sans indicateur dédié.
ERROR_PREFIX: Final = "[erreur] "

_QUOTA_HINTS: Final = frozenset(
    {"insufficient_quota", "billing_error", "billing_hard_limit_reached"}
)
_FILTER_HINTS: Final = frozenset({"content_filter", "content_policy_violation"})
_AUTH_HINTS: Final = frozenset({"authentication_error", "permission_error", "invalid_api_key"})
_RATE_HINTS: Final = frozenset({"rate_limit_error", "rate_limit_exceeded"})
_OVERLOAD_HINTS: Final = frozenset({"overloaded_error", "server_overloaded"})
_TRANSIENT_HINTS: Final = frozenset({"api_error", "timeout_error", "server_error"})
_CONTEXT_HINTS: Final = frozenset({"context_length_exceeded", "string_above_max_length"})
_CONTEXT_MESSAGE: Final = re.compile(
    r"prompt is too long|context (length|window)|maximum context|too many (input )?tokens",
    re.IGNORECASE,
)


def classify_error(
    message: str,
    *,
    http_status: int | None,
    hints: tuple[str | None, ...] = (),
    retry_after: float | None = None,
) -> ModelError:
    """Traduit une erreur de fournisseur en ``ModelError``.

    ``hints`` porte les types et codes d'erreur lus dans la réponse
    (``overloaded_error``, ``insufficient_quota``…). Sans code HTTP (réseau,
    délai, erreur reçue en cours de flux) et sans indice, l'erreur est
    considérée comme transitoire.
    """
    found = {hint for hint in hints if hint}

    def error(kind: ModelErrorKind) -> ModelError:
        return ModelError(
            kind,
            message,
            http_status=http_status,
            retry_after=retry_after,
        )

    if found & _QUOTA_HINTS or http_status == 402:
        return error("quota_exhausted")
    if found & _CONTEXT_HINTS or (
        http_status in {None, 400, 413} and _CONTEXT_MESSAGE.search(message)
    ):
        return error("context_overflow")
    if found & _FILTER_HINTS:
        return error("content_filtered")
    if found & _AUTH_HINTS or http_status in {401, 403}:
        return error("auth")
    if found & _OVERLOAD_HINTS or http_status in {503, 529}:
        return error("overloaded")
    if found & (_RATE_HINTS | _TRANSIENT_HINTS) or http_status in {408, 409, 429}:
        return error("transient")
    if http_status is None or http_status >= 500:
        return error("transient")
    return error("invalid_request")


def retry_after(headers: Mapping[str, str] | None, *, now: float | None = None) -> float | None:
    """Délai demandé par ``retry-after-ms`` ou ``retry-after`` (secondes ou date HTTP)."""
    if not headers:
        return None
    lowered = {key.lower(): value for key, value in headers.items()}
    if (millis := lowered.get("retry-after-ms")) is not None:
        try:
            return max(0.0, float(millis) / 1000)
        except ValueError:
            pass
    value = lowered.get("retry-after")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        moment = parsedate_to_datetime(value)
    except TypeError, ValueError:
        return None
    return max(0.0, moment.timestamp() - (time.time() if now is None else now))


def output_text(output: ToolOutput) -> str:
    """Contenu d'un résultat d'outil, en texte (JSON sérialisé pour les blocs ``json``).

    Lève ``ModelError`` si le résultat contient une image : l'API ne l'accepte
    pas dans un résultat d'outil.
    """
    parts = [_block_text(block) for block in output.blocks]
    if not parts and output.data is not None:
        parts.append(json_text(output.data))
    return "\n".join(parts)


def json_text(data: JsonValue) -> str:
    return json.dumps(data, ensure_ascii=False)


def unresolved(block: ArtifactRefBlock) -> ModelError:
    return ModelError(
        "invalid_request",
        f"Référence de fichier {block.uri!r} non résolue : le moteur la remplace avant l'appel",
    )


def image_data(block: InlineDataBlock) -> str:
    """Octets d'une image, en base64 ; lève ``ModelError`` pour un autre type de fichier."""
    if not is_image(block.media_type):
        raise ModelError(
            "invalid_request",
            f"Fichier {block.media_type} : seules les images sont transmises aux modèles",
        )
    return base64.b64encode(block.data).decode("ascii")


def _block_text(block: OutputBlock) -> str:
    match block:
        case TextBlock(text=text):
            return text
        case JsonBlock(data=data):
            return json_text(data)
        case ArtifactRefBlock():
            raise unresolved(block)
        case InlineDataBlock():
            raise ModelError(
                "invalid_request",
                "Image dans un résultat d'outil : cette API ne l'accepte pas "
                "(déclarer tool_result_media: false)",
            )
