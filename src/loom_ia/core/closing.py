# SPDX-License-Identifier: Apache-2.0
"""Fermer plusieurs ressources sans que l'échec de l'une épargne les autres.

Une fermeture qui s'arrête à la première exception laisse ouvert tout ce
qu'elle n'a pas atteint : une connexion MCP qui plante, et c'est le journal
qui reste ouvert derrière elle — avec ``aiosqlite``, un fil qui empêche le
process de sortir.
"""

import logging
from types import TracebackType

logger = logging.getLogger(__name__)


class Closing:
    """Mène une fermeture à son terme : chaque étape est tentée, même si une précédente a levé.

    Chaque étape se place dans un ``with`` ; si elle lève, l'exception est mise
    de côté et la suivante est tentée. ``raise_if_failed`` lève ensuite **une**
    exception, celle que l'appelant aurait reçue sans ce relais : la première,
    sauf qu'une annulation ou une interruption (``CancelledError``,
    ``KeyboardInterrupt``) passe avant, pour ne jamais être avalée par une
    erreur ordinaire. Les autres sont journalisées, avec leur trace.

    ::

        closing = Closing()
        for resource in resources:
            with closing:
                await resource.aclose()
        closing.raise_if_failed()
    """

    def __init__(self) -> None:
        self._failures: list[BaseException] = []

    def __enter__(self) -> None:
        return None

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        trace: TracebackType | None,
    ) -> bool:
        if error is None:
            return False
        self._failures.append(error)
        return True

    def raise_if_failed(self) -> None:
        """Lève l'échec qui compte, s'il y en a eu un ; journalise les autres."""
        if not self._failures:
            return
        chosen = next(
            (error for error in self._failures if not isinstance(error, Exception)),
            self._failures[0],
        )
        for other in self._failures:
            if other is not chosen:
                logger.warning("Fermeture : une autre étape a échoué aussi", exc_info=other)
        raise chosen
