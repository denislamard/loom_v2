# SPDX-License-Identifier: Apache-2.0
"""D'un échange brut à l'événement qui le garde (#31, J6.1b).

Ce qu'un adaptateur dépose dans le registre (``RawExchange``) est exactement
ce qui a circulé, secrets compris. Avant d'en faire un ``model.exchanged``,
trois retouches, et seulement trois :

1. **les secrets partent** : un en-tête dont le nom dit une clé, un jeton,
   un mot de passe ou un cookie, et un paramètre d'adresse du même genre
   (``?key=…``), sont remplacés par ``[retiré]`` — le nom reste, pour qu'on
   voie qu'il y était ;
2. **les octets d'un fichier partent** : une URI ``data:…;base64,…`` (la
   forme des images chez OpenAI), un objet ``{"type": "base64", "data": …}``
   (chez Anthropic) et un bloc ``{"type": "inline_data", "data": …}`` (celui
   de loom, que montre l'échange simulé) sont remplacés par leur type, leur
   taille et leur empreinte — le journal ne porte pas d'octets de fichier (#16) ;
3. **un corps trop long est coupé** à ``max_bytes``, sur une frontière de
   caractère, et l'événement le dit ; sa taille et son empreinte restent
   celles du corps entier.

Rien d'autre n'est touché : une signature de raisonnement, un identifiant,
une chaîne longue qui n'est pas un fichier restent tels quels.
"""

import base64
import binascii
import hashlib
import json
import re
from collections.abc import AsyncGenerator, Mapping
from typing import Any, Final, cast
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from loom_ia.core.events import ModelExchanged
from loom_ia.core.model import ModelChunk, ModelRequest
from loom_ia.core.ports import ModelClient, RawExchange

REMOVED: Final = "[retiré]"
# Un nom d'en-tête ou de paramètre qui porte un secret.
_SECRET_NAME: Final = re.compile(
    r"authorization|api[-_]?key|(?:^|[-_])key$|token|secret|password|passwd|cookie|signature"
    r"|credential",
    re.IGNORECASE,
)
# Une URI de données en base64, telle qu'elle s'écrit dans un corps JSON.
_DATA_URI: Final = re.compile(r"data:(?P<mime>[\w.+-]+/[\w.+-]+);base64,(?P<data>[A-Za-z0-9+/=]+)")


class Recorded:
    """Un client de modèle dont les échanges bruts sont gardés, et leur borne.

    Il ne fait rien de plus que le client qu'il enrobe : c'est ``ModelCall``
    qui, le reconnaissant, ouvre un registre autour de chaque tentative. Le
    client enrobé, lui, doit savoir y déposer — un ``RecordingClient`` HTTP
    pour un SDK, de lui-même pour le modèle simulé.
    """

    def __init__(self, inner: ModelClient, raw_max_bytes: int) -> None:
        self.inner = inner
        self.raw_max_bytes = raw_max_bytes

    @property
    def provider(self) -> str:
        return self.inner.provider

    def stream(self, request: ModelRequest) -> AsyncGenerator[ModelChunk]:
        return self.inner.stream(request)

    async def aclose(self) -> None:
        await self.inner.aclose()

    def __repr__(self) -> str:
        return f"Recorded({self.inner!r}, {self.raw_max_bytes} octets)"


def exchanged(
    raw: RawExchange,
    *,
    attempt: int,
    model_id: str,
    provider: str,
    max_bytes: int,
) -> ModelExchanged:
    """L'événement d'un échange : secrets et fichiers retirés, corps bornés."""
    request, request_bytes, request_sha, request_cut = _body(raw.request_body, max_bytes)
    response, response_bytes, response_sha, response_cut = _body(raw.response_body, max_bytes)
    return ModelExchanged(
        model_id=model_id,
        provider=provider,
        attempt=attempt,
        method=raw.method,
        url=scrubbed_url(raw.url),
        status_code=raw.status,
        duration_ms=max(raw.duration_ms, 0.0),
        request_headers=scrubbed_headers(raw.request_headers),
        request_body=request,
        request_bytes=request_bytes,
        request_sha256=request_sha,
        request_truncated=request_cut,
        response_headers=scrubbed_headers(raw.response_headers),
        response_body=response,
        response_bytes=response_bytes,
        response_sha256=response_sha,
        response_truncated=response_cut,
        error=raw.error,
        synthetic=raw.synthetic,
    )


def scrubbed_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {
        name.lower(): REMOVED if _SECRET_NAME.search(name) else value
        for name, value in headers.items()
    }


def scrubbed_url(url: str) -> str:
    """L'adresse, sans la valeur des paramètres qui portent un secret."""
    parts = urlsplit(url)
    if not parts.query:
        return url
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    if not any(_SECRET_NAME.search(name) for name, _ in pairs):
        return url
    kept = [(name, REMOVED if _SECRET_NAME.search(name) else value) for name, value in pairs]
    return urlunsplit(parts._replace(query=urlencode(kept, safe="[]")))


def without_files(text: str) -> str:
    """Le corps, ses octets de fichier remplacés par une description."""
    try:
        parsed: object = json.loads(text)
    except ValueError:
        return _DATA_URI.sub(_describe_uri, text)
    if not _carries_base64(parsed) and not _DATA_URI.search(text):
        return text
    return json.dumps(_walk(parsed), ensure_ascii=False)


def _body(raw: bytes, max_bytes: int) -> tuple[str, int, str, bool]:
    """Texte retouché, taille et empreinte du corps entier, et s'il a été coupé."""
    text = without_files(raw.decode("utf-8", errors="replace"))
    encoded = text.encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest() if encoded else ""
    if len(encoded) <= max_bytes:
        return text, len(encoded), digest, False
    # Une coupe au milieu d'un caractère multi-octets est retirée, pas remplacée.
    cut = encoded[:max_bytes].decode("utf-8", errors="ignore")
    return cut, len(encoded), digest, True


def _describe(mime: str, data: str) -> str:
    try:
        octets = base64.b64decode(data, validate=False)
    except binascii.Error, ValueError:
        octets = data.encode("ascii", errors="ignore")
    empreinte = hashlib.sha256(octets).hexdigest()[:16]
    return f"[fichier {mime}, {len(octets)} octets, sha256 {empreinte}…]"


def _describe_uri(match: re.Match[str]) -> str:
    return _describe(match.group("mime"), match.group("data"))


def _carries_base64(value: object) -> bool:
    if isinstance(value, dict):
        table = cast(dict[str, Any], value)
        if _file_object(table):
            return True
        return any(_carries_base64(item) for item in table.values())
    if isinstance(value, list):
        return any(_carries_base64(item) for item in cast(list[object], value))
    return False


def _walk(value: object) -> object:
    if isinstance(value, str):
        return _DATA_URI.sub(_describe_uri, value)
    if isinstance(value, list):
        return [_walk(item) for item in cast(list[object], value)]
    if isinstance(value, dict):
        table = cast(dict[str, Any], value)
        if _file_object(table):
            mime = str(table.get("media_type", "application/octet-stream"))
            return {**table, "data": _describe(mime, cast(str, table["data"]))}
        return {key: _walk(item) for key, item in table.items()}
    return value


def _file_object(table: dict[str, Any]) -> bool:
    """Un objet qui porte les octets d'un fichier en base64 sous ``data``."""
    return table.get("type") in ("base64", "inline_data") and isinstance(table.get("data"), str)
