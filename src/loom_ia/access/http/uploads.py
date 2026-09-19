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
``max_files`` fichiers de ``max_bytes`` est refusé sur son en-tête
(413), avant d'être lu ; chaque fichier n'est lu que jusqu'à sa limite.
Le contrôle du contenu (signature, type accepté) reste celui du moteur.
"""

import json
from typing import Any, Final, NoReturn

from fastapi import HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from starlette.datastructures import UploadFile

from loom_ia.access.http.schemas import RunRequest
from loom_ia.core.model import Attachment, AttachmentPolicy

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
    # Le nombre de fichiers est contrôlé après lecture, avec le message du moteur.
    async with request.form(max_fields=MAX_FIELDS) as form:
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


def _validated(data: bytes | dict[str, object]) -> RunRequest:
    try:
        if isinstance(data, bytes):
            return RunRequest.model_validate_json(data)
        return RunRequest.model_validate(data)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors(include_url=False)) from exc


def _metadata(value: str) -> object:
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        _refuse("metadata", f"JSON invalide ({exc.msg})")


def _refuse(field: str, reason: str) -> NoReturn:
    raise RequestValidationError(
        [{"type": "value_error", "loc": ("body", field), "msg": reason, "input": None}]
    )


def _check_length(request: Request, policy: AttachmentPolicy) -> None:
    """Refuse sur son en-tête un envoi qui ne peut pas tenir dans les limites."""
    declared = request.headers.get("content-length", "")
    limit = policy.max_files * policy.max_bytes + FORM_OVERHEAD
    if declared.isdigit() and int(declared) > limit:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"Envoi de {declared} octets, au-delà de la limite de {limit}",
        )


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
