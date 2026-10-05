# SPDX-License-Identifier: Apache-2.0
"""Enregistrement des échanges bruts avec un fournisseur (#31, J6.1b).

Un échange est une requête HTTP et sa réponse, telles qu'elles ont circulé :
méthode, adresse, en-têtes, corps. C'est l'équivalent de ``log_response`` en
V1, en opt-in (``telemetry.capture.raw_exchanges``) : de quoi déboguer un
fournisseur sans rejouer l'appel.

Le moteur ouvre un **registre** autour de chaque tentative d'appel
(``recording``) ; le client HTTP d'un adaptateur, s'il en trouve un dans le
contexte (``exchange_log``), y dépose ce qu'il a envoyé et reçu. Le moteur
n'a ainsi rien à savoir du transport, et un adaptateur rien à savoir du
journal. Sans registre ouvert, rien n'est gardé.

Un ``ContextVar`` et non un argument : l'appel HTTP a lieu tout au fond du
SDK, que loom ne contrôle pas, mais dans la même tâche que la tentative.
"""

from collections.abc import Generator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Final


@dataclass(frozen=True, slots=True)
class RawExchange:
    """Une requête et sa réponse, telles qu'elles ont circulé.

    ``status`` est absent quand aucune réponse n'est arrivée (réseau, délai) ;
    ``error`` dit alors pourquoi. ``synthetic`` marque l'échange d'un modèle
    simulé, qui n'a pas fait d'HTTP.
    """

    method: str
    url: str
    request_body: bytes
    response_body: bytes = b""
    status: int | None = None
    request_headers: Mapping[str, str] = field(default_factory=dict[str, str])
    response_headers: Mapping[str, str] = field(default_factory=dict[str, str])
    duration_ms: float = 0.0
    error: str | None = None
    synthetic: bool = False


class ExchangeLog:
    """Les échanges d'une tentative, dans l'ordre où ils se sont terminés."""

    def __init__(self) -> None:
        self.exchanges: list[RawExchange] = []

    def record(self, exchange: RawExchange) -> None:
        self.exchanges.append(exchange)


_CURRENT: Final[ContextVar[ExchangeLog | None]] = ContextVar("loom_exchange_log", default=None)


def exchange_log() -> ExchangeLog | None:
    """Le registre ouvert pour la tentative en cours, s'il y en a un."""
    return _CURRENT.get()


@contextmanager
def recording(log: ExchangeLog) -> Generator[ExchangeLog]:
    """Ouvre ``log`` pour ce qui s'exécute dans le bloc, dans cette tâche."""
    token = _CURRENT.set(log)
    try:
        yield log
    finally:
        _CURRENT.reset(token)
