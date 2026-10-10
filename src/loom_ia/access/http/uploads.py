# SPDX-License-Identifier: Apache-2.0
"""Corps d'une demande de run : JSON, ou formulaire multipart avec des fichiers (G1).

La route ``POST /v1/agents/{name}/runs`` accepte deux formes :

- ``application/json`` : un ``RunRequest``, sans pièce jointe ;
- ``multipart/form-data`` : les mêmes champs en texte (``metadata`` en JSON)
  et les fichiers sous le nom ``attachments``, répété pour chacun ; un
  formulaire sans fichier (``application/x-www-form-urlencoded``) passe aussi.

    curl -F message='Que montre la photo ?' -F attachments=@photo.jpg \\
         http://127.0.0.1:8000/v1/agents/assistant/runs

Les limites viennent de ``execution.attachments`` : un envoi plus gros que
``max_files`` fichiers de ``max_bytes`` est refusé sur son en-tête (413), avant
d'être lu ; sans ``Content-Length`` (envoi en morceaux), il est compté à mesure
qu'il arrive et refusé au même plafond, avant d'être écrit sur disque. Un
fichier de trop est refusé dès l'en-tête de sa partie, et chaque fichier n'est
lu que jusqu'à sa limite. Le contrôle du contenu (signature, type accepté)
reste celui du moteur.

Tous les corps, ceux-là compris, passent par ``BodyLimit`` : JSON, déclencheurs et
MCP n'ont pas de fichier à compter, mais ``server.http.max_body_bytes`` les borne.
"""

import json
import re
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping, MutableMapping
from contextlib import asynccontextmanager
from typing import Any, Final, NoReturn

from fastapi import HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from python_multipart.exceptions import FormParserError
from starlette.datastructures import FormData, Headers, UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from loom_ia.access.http.schemas import RunRequest, shallow
from loom_ia.core.model import JUDGES_MODES, Attachment, AttachmentPolicy

JSON: Final = "application/json"
MULTIPART: Final = "multipart/form-data"
FORM: Final = "application/x-www-form-urlencoded"
# Nom des parties qui portent les fichiers.
FILES_FIELD: Final = "attachments"
# Place laissée aux champs texte et aux en-têtes des parties.
FORM_OVERHEAD: Final = 256 * 1024
MAX_FIELDS: Final = 16
# Type envoyé quand le client ne sait pas : la signature décidera.
UNDECLARED: Final = frozenset({"", "application/octet-stream"})

# Description OpenAPI des deux corps acceptés.
RUN_BODY: Final[dict[str, Any]] = {
    "requestBody": {
        "required": True,
        "content": {
            JSON: {"schema": RunRequest.model_json_schema()},
            MULTIPART: {
                "schema": {
                    "type": "object",
                    "properties": {
                        "message": {"type": "string", "minLength": 1},
                        "session_id": {"type": "string"},
                        "run_id": {"type": "string"},
                        "user_id": {"type": "string"},
                        "metadata": {"type": "string", "description": "Objet JSON"},
                        "judges": {"type": "string", "enum": list(JUDGES_MODES)},
                        "background": {"type": "boolean"},
                        FILES_FIELD: {
                            "type": "array",
                            "items": {"type": "string", "format": "binary"},
                            "description": "Images jointes, un fichier par partie",
                        },
                    },
                    "required": ["message"],
                }
            },
        },
    }
}


async def run_request(
    request: Request, policy: AttachmentPolicy
) -> tuple[RunRequest, list[Attachment]]:
    """Demande de run et pièces jointes, lues selon le type du corps.

    Un corps invalide donne 422, un type de corps inconnu 415, un envoi trop
    gros 413. Une pièce jointe refusée lève ``AttachmentError``.
    """
    kind = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if kind in ("", JSON):
        return _validated(await request.body()), []
    if kind not in (MULTIPART, FORM):
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"Type de corps non pris en charge : {kind} (acceptés : {JSON}, {MULTIPART})",
        )
    _check_length(request, policy)
    # Le même plafond vaut pour un corps sans ``Content-Length`` (chunked) : il
    # est compté à mesure qu'il arrive, avant que Starlette ne l'écrive sur disque.
    bounded = Request(request.scope, _Bounded(request.receive, _ceiling(policy)))
    async with _form(bounded, policy) as form:
        fields: dict[str, object] = {}
        files: list[UploadFile] = []
        for name, value in form.multi_items():
            if isinstance(value, UploadFile):
                if name != FILES_FIELD:
                    _refuse(name, f"fichier inattendu (les fichiers vont dans '{FILES_FIELD}')")
                files.append(value)
            elif name == FILES_FIELD:
                _refuse(name, "un fichier est attendu")
            elif name in fields:
                _refuse(name, "champ répété")
            else:
                fields[name] = _metadata(value) if name == "metadata" else value
        body = _validated(fields)
        policy.check_count(len(files))
        return body, [await _attachment(upload, policy) for upload in files]


@asynccontextmanager
async def _form(request: Request, policy: AttachmentPolicy) -> AsyncGenerator[FormData]:
    """Le formulaire du corps, lu jusqu'aux limites ; un refus de la lecture devient le nôtre.

    Starlette arrête la lecture au fichier de trop, dès l'en-tête de sa partie et
    avant d'en écrire un octet ; c'est alors le refus du moteur (422, son message)
    qui part. Un formulaire que l'analyseur ne sait pas lire est une demande mal
    formée (400), non un conflit.
    """
    try:
        async with request.form(max_files=policy.max_files, max_fields=MAX_FIELDS) as form:
            yield form
    except FormParserError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Formulaire illisible : {exc}") from exc
    except StarletteHTTPException as exc:
        if exc.status_code == status.HTTP_400_BAD_REQUEST and str(exc.detail).startswith(
            "Too many files"
        ):
            policy.check_count(policy.max_files + 1)
        raise


def _validated(data: bytes | dict[str, object]) -> RunRequest:
    try:
        if isinstance(data, bytes):
            return RunRequest.model_validate_json(data)
        return RunRequest.model_validate(data)
    except ValidationError as exc:
        errors = exc.errors(include_url=False)
        for error in errors:
            # Un JSON qui n'est pas de l'UTF-8 garde ses octets pour ``input`` : la
            # réponse 422 ne saurait pas les écrire (500).
            shown = error["input"]
            if isinstance(shown, bytes):
                error["input"] = shown.decode(errors="replace")
        raise RequestValidationError(errors) from exc


def _metadata(value: str) -> object:
    try:
        return shallow(json.loads(value))
    except json.JSONDecodeError as exc:
        _refuse("metadata", f"JSON invalide ({exc.msg})")
    except RecursionError:
        _refuse("metadata", "JSON invalide (trop imbriqué)")
    except ValueError as exc:
        _refuse("metadata", f"JSON invalide ({exc})")


def _refuse(field: str, reason: str) -> NoReturn:
    raise RequestValidationError(
        [{"type": "value_error", "loc": ("body", field), "msg": reason, "input": None}]
    )


def _ceiling(policy: AttachmentPolicy) -> int:
    """Octets au-delà desquels un envoi ne peut pas tenir dans les limites."""
    return policy.max_files * policy.max_bytes + FORM_OVERHEAD


def declared_length(headers: Mapping[str, str]) -> int | None:
    """Longueur annoncée par ``Content-Length``, ou ``None`` sans en-tête lisible.

    Seuls les chiffres ASCII comptent (``str.isdigit`` accepte « ² », que ``int``
    refuse), et pas plus de 18 : au-delà l'en-tête est ignoré, et le corps compté
    à mesure qu'il arrive.
    """
    value = headers.get("content-length", "")
    return int(value) if re.fullmatch(r"[0-9]{1,18}", value) else None


def _check_length(request: Request, policy: AttachmentPolicy) -> None:
    """Refuse sur son en-tête un envoi qui ne peut pas tenir dans les limites."""
    declared = declared_length(request.headers)
    limit = _ceiling(policy)
    if declared is not None and declared > limit:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"Envoi de {declared} octets, au-delà de la limite de {limit}",
        )


type _Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]


class _Bounded:
    """``receive`` d'une requête qui refuse (413) un corps plus long que ``limit`` octets.

    ``Content-Length`` est contrôlé avant la lecture ; un envoi en morceaux n'en
    a pas, et ne se mesure qu'en arrivant.
    """

    def __init__(self, receive: _Receive, limit: int) -> None:
        self._receive = receive
        self._limit = limit
        self._seen = 0

    async def __call__(self) -> MutableMapping[str, Any]:
        message = await self._receive()
        if message["type"] == "http.request":
            self._seen += len(message.get("body", b""))
            if self._seen > self._limit:
                raise HTTPException(
                    status.HTTP_413_CONTENT_TOO_LARGE,
                    f"Envoi de plus de {self._limit} octets, au-delà de la limite",
                )
        return message


class _TooLarge(Exception):
    """Le corps a dépassé le plafond pendant sa lecture."""


class BodyLimit:
    """Middleware ASGI : refuse (413) toute requête dont le corps dépasse ``limit`` octets.

    Il vaut pour tout ce que l'application sert — JSON, déclencheurs, MCP — et il
    est posé avant elle, donc avant la clé, la route et le protocole. Une longueur
    annoncée au-delà du plafond est refusée sans rien lire ; sans longueur (envoi
    en morceaux), le corps est compté à mesure qu'il arrive, et la lecture s'arrête
    au dépassement. Ce refus est alors la seule réponse : celle que l'application
    aurait tirée de l'échec de sa lecture (une erreur 500 du transport MCP, par
    exemple) est écartée.
    """

    def __init__(self, app: ASGIApp, limit: int) -> None:
        self.app = app
        self.limit = limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = declared_length(Headers(scope=scope))
        if declared is not None and declared > self.limit:
            await self._refuse(f"Corps de {declared} octets", scope, receive, send)
            return
        seen = 0
        exceeded = False
        started = False

        async def counted() -> Message:
            nonlocal seen, exceeded
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > self.limit:
                    exceeded = True
                    raise _TooLarge
            return message

        async def guarded(message: Message) -> None:
            nonlocal started
            if exceeded and not started:
                return
            started = started or message["type"] == "http.response.start"
            await send(message)

        try:
            await self.app(scope, counted, guarded)
        except Exception:
            if not exceeded or started:
                raise
        if exceeded and not started:
            await self._refuse("Corps trop long", scope, receive, send)

    async def _refuse(self, what: str, scope: Scope, receive: Receive, send: Send) -> None:
        detail = f"{what}, au-delà de la limite de {self.limit} octets (server.http.max_body_bytes)"
        refusal = JSONResponse({"detail": detail}, status_code=status.HTTP_413_CONTENT_TOO_LARGE)
        await refusal(scope, receive, send)


async def _attachment(upload: UploadFile, policy: AttachmentPolicy) -> Attachment:
    """Pièce jointe d'une partie, lue au plus jusqu'à la limite de taille."""
    label = upload.filename or "pièce jointe"
    if upload.size is not None:
        policy.check_size(label, upload.size)
    data = await upload.read(policy.max_bytes + 1)
    policy.check_size(label, len(data))
    declared = (upload.content_type or "").lower()
    return Attachment(
        data=data,
        media_type=None if declared in UNDECLARED else declared,
        name=upload.filename,
    )
