# SPDX-License-Identifier: Apache-2.0
"""Disjoncteurs des modèles et des serveurs MCP (#10, #19, backlog #011).

Un disjoncteur compte les échecs de suite d'une cible : un modèle dont
l'appel a échoué après ses nouvelles tentatives (``transient``,
``overloaded``, ``quota_exhausted``), ou un serveur MCP dont la connexion a
échoué. Au seuil (``failures``), il s'ouvre : la cible est écartée pendant
``cooldown`` secondes, pour tous les runs qui partagent les disjoncteurs
(ceux d'une instance ``Loom``). Un modèle écarté passe directement la main à
son secours ; un serveur MCP écarté est déclaré indisponible sans nouvel
essai.

À la fin de la pause, la cible est de nouveau appelée : réussi, cet essai
referme le disjoncteur ; raté, il le rouvre pour une nouvelle pause. Une
réussite remet toujours le compte à zéro.

Les erreurs propres à une requête (``invalid_request``, ``context_overflow``,
``content_filtered``) et les erreurs de config (``auth``) ne comptent pas :
elles ne disent rien de la disponibilité de la cible.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from loom_ia.core.model import CircuitBreaker, ModelErrorKind

type Clock = Callable[[], float]

# Erreurs d'un modèle qui comptent pour son disjoncteur.
CIRCUIT_ERRORS: Final[frozenset[ModelErrorKind]] = frozenset(
    {"transient", "overloaded", "quota_exhausted"}
)


def model_key(model_id: str) -> str:
    """Clé du disjoncteur d'un modèle (identifiant dans la config)."""
    return f"model:{model_id}"


def mcp_key(server: str) -> str:
    """Clé du disjoncteur d'un serveur MCP."""
    return f"mcp:{server}"


@dataclass
class _Circuit:
    failures: int = 0
    # Fin de la pause, selon l'horloge ; None : disjoncteur fermé.
    until: float | None = None
    # Essai d'après la pause : raté, le disjoncteur se rouvre aussitôt.
    trial: bool = False


@dataclass(frozen=True, slots=True)
class Tripped:
    """Disjoncteur que le dernier échec vient d'ouvrir."""

    # Échecs de suite qui l'ont ouvert ; 1 pour un essai raté après une pause.
    failures: int
    cooldown: float


class CircuitBreakers:
    """Disjoncteurs partagés par des runs, un par cible (``model:<id>``, ``mcp:<nom>``)."""

    def __init__(self, clock: Clock = time.monotonic) -> None:
        self._clock = clock
        self._circuits: dict[str, _Circuit] = {}

    def remaining(self, key: str) -> float | None:
        """Secondes de pause restantes si la cible est écartée ; None si elle peut être appelée."""
        circuit = self._circuits.get(key)
        if circuit is None or circuit.until is None:
            return None
        left = circuit.until - self._clock()
        if left > 0:
            return left
        circuit.until, circuit.trial = None, True
        return None

    def failed(self, key: str, setting: CircuitBreaker) -> Tripped | None:
        """Compte un échec ; rend le disjoncteur s'il vient de s'ouvrir."""
        circuit = self._circuits.setdefault(key, _Circuit())
        now = self._clock()
        if circuit.until is not None:
            if circuit.until > now:
                # Appel parti avant l'ouverture : l'échec est déjà compté.
                return None
            circuit.until, circuit.trial = None, True
        circuit.failures += 1
        if not circuit.trial and circuit.failures < setting.failures:
            return None
        failures = circuit.failures
        circuit.failures, circuit.trial = 0, False
        circuit.until = now + setting.cooldown
        return Tripped(failures=failures, cooldown=setting.cooldown)

    def succeeded(self, key: str) -> None:
        """Appel ou connexion réussis : le disjoncteur se referme, son compte repart de zéro."""
        self._circuits.pop(key, None)
