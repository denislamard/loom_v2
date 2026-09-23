# SPDX-License-Identifier: Apache-2.0
"""Serveur MCP en HTTP, monté dans l'application REST (N5, #39, J5.2b).

Ce module demande les deux extras, ``http`` et ``mcp`` : il monte le serveur
MCP dans l'application REST. ``loom_ia.access.mcp_server`` ne l'importe donc
pas — le stdio se contente de l'extra ``mcp``.

Ce que le transport HTTP change, et c'est l'essentiel : **la clé arrive à
chaque requête**. En stdio, rien dans le protocole ne dit au nom de qui on
parle, donc un serveur sert un client, choisi à son lancement. Ici, un seul
serveur sert tous les clients — il publie à chacun les agents que sa clé lui
ouvre, et refuse ce qu'elle ne permet pas.

L'authentification a lieu **avant** le protocole, dans une couche ASGI : une
clé absente, inconnue ou expirée reçoit un 401 ordinaire, avec son
``WWW-Authenticate``, plutôt qu'une erreur d'outil que le LLM du client
essaierait d'interpréter. L'appelant reconnu est posé dans une variable de
contexte, où les gestionnaires du serveur le relisent.

Protection du transport (spec MCP) : le SDK valide ``Origin`` et ``Host``
contre le rebinding DNS. Une requête **sans** ``Origin`` passe — un client
natif n'en envoie pas ; avec un ``Origin``, il doit être déclaré. Le ``Host``,
lui, doit toujours figurer dans la liste : loom y met son adresse d'écoute,
et ``server.mcp.allowed_hosts`` l'étend.
"""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Final

from fastapi import HTTPException
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

from loom_ia.access.api import Loom
from loom_ia.access.caller import Caller
from loom_ia.access.http.auth import identify
from loom_ia.access.mcp_server.server import SERVER_NAME, create_server
from loom_ia.config.models import HttpServer, McpAccess, SecurityConfig

logger = logging.getLogger(__name__)

# Chemin du serveur MCP sous le préfixe commun de l'application.
MCP_PATH: Final = "/mcp"
# Hôtes toujours acceptés : l'instance servie depuis la machine même.
LOCAL_HOSTS: Final = ("127.0.0.1:*", "localhost:*", "[::1]:*", "127.0.0.1", "localhost")

_caller: ContextVar[Caller] = ContextVar("loom_mcp_caller")


def current_caller() -> Caller:
    """Appelant de la requête MCP en cours, posé par la couche ASGI."""
    return _caller.get()


def allowed_hosts(mcp: McpAccess, http: HttpServer) -> list[str]:
    """Hôtes acceptés : l'adresse d'écoute, plus ce que la config ajoute.

    Une liste vide ferait tout refuser par le SDK : loom la remplit donc
    lui-même, pour qu'un essai local marche sans rien régler.
    """
    hosts = list(LOCAL_HOSTS)
    if http.host not in ("127.0.0.1", "localhost", "0.0.0.0"):
        hosts += [http.host, f"{http.host}:*"]
    hosts += [host for host in mcp.allowed_hosts if host not in hosts]
    return hosts


def transport_security(mcp: McpAccess, http: HttpServer) -> TransportSecuritySettings:
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts(mcp, http),
        allowed_origins=list(mcp.allowed_origins),
    )


class McpHttp:
    """Le serveur MCP d'une instance, servi en HTTP sous ``<base_path>/mcp``."""

    # Chemin de montage, sous le préfixe commun de l'application.
    path: Final = MCP_PATH

    def __init__(self, loom: Loom, *, name: str = SERVER_NAME) -> None:
        config = loom.config
        self.security: SecurityConfig = config.security
        self.server = create_server(loom, name=name, callers=current_caller)
        self.manager = StreamableHTTPSessionManager(
            app=self.server,
            stateless=True,
            security_settings=transport_security(config.server.mcp, config.server.http),
        )

    @asynccontextmanager
    async def running(self) -> AsyncGenerator[None]:
        """Cycle de vie du gestionnaire de session, accroché à celui de l'app."""
        async with self.manager.run():
            yield

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            caller = identify(self.security, Request(scope, receive))
        except HTTPException as refus:
            # Un 401 ordinaire, pas une erreur d'outil : le LLM du client n'a
            # pas à interpréter un refus d'authentification.
            response = JSONResponse(
                {"detail": refus.detail},
                status_code=refus.status_code,
                headers=dict(refus.headers or {}),
            )
            await response(scope, receive, send)
            return
        token = _caller.set(caller)
        try:
            await self.manager.handle_request(scope, receive, send)
        finally:
            _caller.reset(token)
