# SPDX-License-Identifier: Apache-2.0
"""Clés d'API de l'accès REST (N2, #39).

La configuration ne garde que l'empreinte des clés (``sha256:…``) : la clé
elle-même n'existe qu'une fois, à sa création. Une instance sans clé
déclarée est ouverte — c'est le cas de l'usage local ; ``create_app``
prévient si elle écoute ailleurs que sur la machine.

Portées vérifiées : ``run`` pour lancer un run, ``read`` pour lire agents,
statuts, événements et rapports, ``admin`` pour lancer un run sans ses juges
(``judges: skip``, J3). Une clé peut en outre être limitée à certains agents.

Client (L1, #34) : une clé agit **au nom d'un client**, et c'est la seule
chose qui le dit en REST — rien dans le corps d'une requête ne peut le
changer. Toute lecture est donc bornée au client de la clé : on ne peut pas
demander le journal d'un autre, faute de façon de le nommer.

Débit (#39, J5.1b) : une clé peut porter un ``rate_limit``. Il protège le
serveur, il ne dit rien du métier — c'est le quota du client qui le fait, et
lui vaut par tous les accès. Vérifié à l'identification, donc sur **toutes**
les routes, et répondu par un 429 avec un ``Retry-After``.

Expiration et contenu (J5.2a) : une clé peut porter une date de fin — elle
est alors reconnue puis **refusée** (401), pour que le message dise « expirée »
et non « inconnue ». Et la portée ``read_content`` décide de ce qu'une lecture
montre : sans elle, les routes répondent, mais privées de ce qu'un utilisateur
a écrit et de ce qu'un modèle a répondu (§14.2). Ce qu'une clé **lance**, elle
le reçoit : c'est la relecture qui demande la portée.
"""

import math
from dataclasses import dataclass
from typing import Final

from fastapi import HTTPException, Request, status

from loom_ia.config.models import ApiKey, Scope, SecurityConfig
from loom_ia.core.model import DEFAULT_TENANT, TenantId
from loom_ia.tenancy import RateWindow

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

    @property
    def tenant(self) -> TenantId:
        """Client au nom duquel cette clé agit ; ``default`` sur une instance ouverte."""
        return self.key.tenant if self.key is not None else DEFAULT_TENANT

    def may(self, scope: Scope) -> bool:
        return self.key is None or scope in self.key.scopes

    @property
    def masks(self) -> bool:
        """Vrai si les lectures de cet appelant doivent être privées de contenu."""
        return not self.may("read_content")

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
        if not key.accepts(given):
            continue
        if key.expired():
            # Reconnue, puis refusée : « expirée » se corrige, « inconnue »
            # envoie chercher au mauvais endroit.
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                f"Clé {key.id!r} expirée le {key.expires:%Y-%m-%d %H:%M} UTC",
                headers=_CHALLENGE,
            )
        return Caller(key)
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Clé d'API refusée", headers=_CHALLENGE)


def throttle(caller: Caller, window: RateWindow) -> None:
    """Compte la requête de cette clé, ou refuse en 429 avec son ``Retry-After``."""
    key = caller.key
    if key is None or key.rate_limit is None:
        return
    waiting = window.take(f"key:{key.id}", key.rate_limit.per_minute)
    if waiting is None:
        return
    raise HTTPException(
        status.HTTP_429_TOO_MANY_REQUESTS,
        f"Clé {key.id!r} : {key.rate_limit.per_minute} requêtes par minute dépassé",
        headers={"Retry-After": str(max(1, math.ceil(waiting)))},
    )


def require(caller: Caller, scope: Scope, agent: str | None = None) -> None:
    """Vérifie la portée demandée, et l'agent visé s'il y en a un."""
    if not caller.may(scope):
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Clé sans la portée {scope!r}")
    if agent is not None and not caller.allows(agent):
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Clé non autorisée sur l'agent {agent!r}")
