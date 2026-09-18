# SPDX-License-Identifier: Apache-2.0
"""Serveur HTTP de développement (N2).

``uvicorn`` sert l'application ; la configuration donne l'adresse et le port,
que la ligne de commande peut remplacer. La journalisation reste celle de
loom (``telemetry.logging``) : uvicorn ne réinstalle pas la sienne.
"""

from loom_ia.access.api import Loom
from loom_ia.access.http.app import create_app


def serve(loom: Loom, *, host: str | None = None, port: int | None = None) -> None:
    """Sert l'instance jusqu'à l'arrêt du process, puis la ferme."""
    import uvicorn

    http = loom.config.server.http
    uvicorn.run(
        create_app(loom, own=True),
        host=host or http.host,
        port=port or http.port,
        log_config=None,
    )
