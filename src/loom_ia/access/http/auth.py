# SPDX-License-Identifier: Apache-2.0
"""Clés d'API de l'accès REST (N2, #39).

La configuration ne garde que l'empreinte des clés (``sha256:…``) : la clé
elle-même n'existe qu'une fois, à sa création. Une instance sans clé
déclarée est ouverte — c'est le cas de l'usage local ; ``create_app``
prévient si elle écoute ailleurs que sur la machine.

Portées vérifiées : ``run`` pour lancer un run, ``read`` pour lire agents,
statuts, événements et rapports, ``admin`` pour lancer un run sans ses juges
(``judges: skip``, J3). Une clé peut en outre être limitée à certains agents.
"""

from dataclasses import dataclass
from typing import Final

from fastapi import HTTPException, Request, status

from loom_ia.config.models import ApiKey, Scope, SecurityConfig

BEARER: Final = "bearer "
API_KEY_HEADER: Final = "x-api-key"
_CHALLENGE: Final = {"WWW-Authenticate": "Bearer"}


@dataclass(frozen=True, slots=True)
class Caller:
    """Qui appelle : une clé reconnue, ou personne sur une instance ouverte."""

    key: ApiKey | None = None

    @property
    def anonymous(self) -> bool:
        return self.key is None

    def may(self, scope: Scope) -> bool:
        return self.key is None or scope in self.key.scopes

    def allows(self, agent: str) -> bool:
        return self.key is None or self.key.allows(agent)


def presented(request: Request) -> str | None:
    """Clé présentée par l'appelant, dans ``Authorization`` ou ``X-API-Key``."""
    header = request.headers.get("authorization", "")
    if header.lower().startswith(BEARER):
        return header[len(BEARER) :].strip() or None
    return request.headers.get(API_KEY_HEADER)


def identify(security: SecurityConfig, request: Request) -> Caller:
    """Reconnaît l'appelant, ou refuse la requête."""
    if not security.api_keys:
        return Caller()
    given = presented(request)
    if not given:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Clé d'API absente : en-tête 'Authorization: Bearer …' ou 'X-API-Key'",
            headers=_CHALLENGE,
        )
    for key in security.api_keys:
        if key.accepts(given):
            return Caller(key)
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Clé d'API refusée", headers=_CHALLENGE)


def require(caller: Caller, scope: Scope, agent: str | None = None) -> None:
    """Vérifie la portée demandée, et l'agent visé s'il y en a un."""
    if not caller.may(scope):
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Clé sans la portée {scope!r}")
    if agent is not None and not caller.allows(agent):
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Clé non autorisée sur l'agent {agent!r}")
