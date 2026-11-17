# SPDX-License-Identifier: Apache-2.0
"""D'un échange brut à l'événement qui le garde (point 31, jalon J6.1b).

Ce qu'un adaptateur dépose dans le registre (``RawExchange``) est exactement
ce qui a circulé, secrets compris. Avant d'en faire un ``model.exchanged``,
trois retouches, et seulement trois :

1. **les secrets partent** : un en-tête dont le nom dit une clé, un jeton,
   un mot de passe ou un cookie, un paramètre d'adresse du même genre
   (``?key=…``) et les identifiants d'une adresse (``https://user:pw@hôte``)
   sont remplacés par ``[retiré]`` — le nom, ou l'hôte, reste, pour qu'on
   voie qu'il y était. Un compteur de débit (``x-ratelimit-remaining-tokens``)
   n'est pas un jeton : il reste, c'est le diagnostic d'un refus pour débit ;
2. **les octets d'un fichier partent** : une URI ``data:…;base64,…`` (la
   forme des images chez OpenAI), un objet ``{"type": "base64", "data": …}``
   (chez Anthropic) et un bloc ``{"type": "inline_data", "data": …}`` (celui
   de loom, que montre l'échange simulé) sont remplacés par leur type, leur
   taille et leur empreinte — le journal ne porte pas d'octets de fichier (point 16) ;
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
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from loom_ia.core.events import ModelExchanged
from loom_ia.core.model import ModelChunk, ModelRequest
from loom_ia.core.ports import ModelClient, RawExchange

REMOVED: Final = "[retiré]"
"""Valeur mise à la place d'un secret."""
_SECRET_NAME: Final = re.compile(
    r"authorization|api[-_]?key|(?:^|[-_])key$|token|secret|password|passwd|cookie|signature"
    r"|credential",
    re.IGNORECASE,
)
"""Un nom d'en-tête ou de paramètre qui porte un secret."""
_RATE_LIMIT: Final = re.compile(r"rate-?limit", re.IGNORECASE)
"""Un compteur de débit : « tokens » y compte des jetons de modèle, il n'en est pas un."""
_DATA_URI: Final = re.compile(r"data:(?P<mime>[\w.+-]+/[\w.+-]+);base64,(?P<data>[A-Za-z0-9+/=]+)")
"""Une URI de données en base64, telle qu'elle s'écrit dans un corps JSON."""


class Recorded:
    """Un client de modèle dont les échanges bruts sont gardés, et leur borne.

    Il ne fait rien de plus que le client qu'il enrobe : c'est ``ModelCall``
    qui, le reconnaissant, ouvre un registre autour de chaque tentative. Le
    client enrobé, lui, doit savoir y déposer — un ``RecordingClient`` HTTP
    pour un SDK, de lui-même pour le modèle simulé.

    Réglage : ``raw_exchanges`` du modèle (``ModelSpec``) ; les échanges
    deviennent des événements ``model.exchanged``.

    Args:
        inner: Le client enrobé.
        raw_max_bytes: Taille maximale d'un corps gardé, en octets ; au-delà,
            il est coupé.

    Attributes:
        inner: Le client enrobé.
        raw_max_bytes: Taille maximale d'un corps gardé, en octets.
    """

    def __init__(self, inner: ModelClient, raw_max_bytes: int) -> None:
        self.inner = inner
        self.raw_max_bytes = raw_max_bytes

    @property
    def provider(self) -> str:
        """Fournisseur du client enrobé."""
        return self.inner.provider

    def stream(self, request: ModelRequest) -> AsyncGenerator[ModelChunk]:
        """Flux de la réponse du client enrobé, sans changement."""
        return self.inner.stream(request)

    async def aclose(self) -> None:
        """Ferme le client enrobé."""
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
    model: str | None = None,
) -> ModelExchanged:
    """L'événement d'un échange : secrets et fichiers retirés, corps bornés.

    Args:
        raw: L'échange brut déposé par l'adaptateur.
        attempt: Rang de la tentative, à partir de 1.
        model_id: Identifiant du modèle dans la config.
        provider: Fournisseur (``anthropic``, ``openai``…).
        max_bytes: Taille maximale d'un corps, en octets.
        model: Nom du modèle chez le fournisseur, s'il est connu.

    Returns:
        L'événement ``model.exchanged`` : méthode, adresse et en-têtes sans
        secrets, corps sans octets de fichier et coupés à ``max_bytes``, taille
        et empreinte SHA-256 des corps entiers, statut, durée, erreur.
    """
    request, request_bytes, request_sha, request_cut = _body(raw.request_body, max_bytes)
    response, response_bytes, response_sha, response_cut = _body(raw.response_body, max_bytes)
    return ModelExchanged(
        model_id=model_id,
        model=model,
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
    """Les en-têtes, noms en minuscules, la valeur d'un en-tête secret remplacée par ``[retiré]``."""
    return {name.lower(): REMOVED if _secret(name) else value for name, value in headers.items()}


def scrubbed_url(url: str) -> str:
    """L'adresse, sans ses identifiants ni la valeur des paramètres qui portent un secret.

    Args:
        url: L'adresse appelée.

    Returns:
        L'adresse, identifiants et valeurs secrètes remplacés par
        ``[retiré]`` ; la même chaîne, au caractère près, s'il n'y a rien à
        retirer.
    """
    parts = original = urlsplit(url)
    if "@" in parts.netloc:
        # Ce qui précède le dernier « @ » : l'utilisateur, son mot de passe, ou une clé.
        host = parts.netloc.rpartition("@")[2]
        parts = parts._replace(netloc=f"{quote(REMOVED, safe='[]')}@{host}")
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    if any(_secret(name) for name, _ in pairs):
        kept = [(name, REMOVED if _secret(name) else value) for name, value in pairs]
        parts = parts._replace(query=urlencode(kept, safe="[]"))
    # Rien à retirer : l'adresse reste telle qu'elle a été écrite, au caractère près.
    return url if parts == original else urlunsplit(parts)


def _secret(name: str) -> bool:
    """Vrai si le nom d'un en-tête ou d'un paramètre dit qu'il porte un secret."""
    return bool(_SECRET_NAME.search(name)) and not _RATE_LIMIT.search(name)


def without_files(text: str) -> str:
    """Le corps, ses octets de fichier remplacés par une description.

    La description est « [fichier <type>, <n> octets, sha256 <16 car.>…] ».

    Args:
        text: Le corps, JSON ou non.

    Returns:
        Le corps retouché ; le même texte s'il ne porte pas de fichier.
    """
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
