# SPDX-License-Identifier: Apache-2.0
"""Déroulé d'un run en lignes courtes (N4, D4).

Le direct de la CLI (``loom run --stream``) et les notifications de
progression du serveur MCP décrivent un run de la même façon : un appel
d'outil et son issue, un fichier rangé, un serveur indisponible, une
décision de politique, un sous-agent qui démarre puis se termine. Les lignes d'un sous-run sont
décalées selon sa profondeur :

    · verifier(message='Vérifie : …')
      · sous-agent verificateur : démarré
      · math__calculer(expression='121 * 24')
      · math__calculer : fait
      · sous-agent verificateur : terminé
    · verifier : fait
"""

from typing import Final

from loom_ia.core.events import (
    ArtifactStored,
    Event,
    PolicyDecided,
    RunCompleted,
    RunFailed,
    RunStarted,
    ToolCalled,
    ToolCompleted,
    ToolSourceUnavailable,
)
from loom_ia.core.model import RunId

INDENT: Final = "  "
STORED: Final = {
    "attachment": "pièce jointe rangée",
    "tool_output": "fichier rangé",
    "offload": "résultat déporté",
}


# Décisions de politique, telles qu'une ligne du déroulé les nomme.
DECISIONS: Final = {
    "continue": "laissé passer",
    "replace": "remplacé",
    "retry": "réparation demandée",
    "deny": "refusé",
    "pause": "pause",
    "stop": "arrêt",
    "fail": "échec",
}


class Progress:
    """Traduit les événements d'un arbre de runs en lignes, vus dans l'ordre du journal."""

    def __init__(self) -> None:
        self._depths: dict[RunId, int] = {}

    def depth(self, event: Event) -> int:
        """Profondeur du run de l'événement (0 pour un run inconnu)."""
        return self._depths.get(event.run_id, 0)

    def line(self, event: Event) -> str | None:
        """Ligne qui décrit l'événement, ou ``None`` s'il ne se montre pas."""
        if isinstance(event.payload, RunStarted):
            self._depths[event.run_id] = event.payload.depth
        depth = self.depth(event)
        text = describe(event, subrun=depth > 0)
        return None if text is None else f"{INDENT * depth}· {text}"


def describe(event: Event, *, subrun: bool = False) -> str | None:
    """Ce que l'événement apprend du déroulé ; début et fin ne se montrent que pour un sous-run."""
    payload = event.payload
    match payload:
        case ToolCalled():
            return f"{payload.tool_name}({arguments(payload)})"
        case ToolCompleted():
            return f"{payload.tool_name} : fait{' (erreur)' if payload.output.is_error else ''}"
        case ToolSourceUnavailable():
            required = " (requis)" if payload.required else ""
            return f"serveur {payload.source} indisponible{required} : {payload.error}"
        case PolicyDecided():
            reason = f" — {payload.reason}" if payload.reason else ""
            return (
                f"politique {payload.policy} ({payload.point}) : "
                f"{DECISIONS[payload.decision]}{reason}"
            )
        case ArtifactStored():
            return (
                f"{STORED[payload.origin]} : {payload.name or payload.uri} "
                f"({payload.media_type}, {payload.size} octets)"
            )
        case RunStarted() if subrun:
            return f"sous-agent {event.agent} : démarré"
        case RunCompleted() if subrun:
            return f"sous-agent {event.agent} : terminé"
        case RunFailed() if subrun:
            return f"sous-agent {event.agent} : échec — {payload.error}"
        case _:
            return None


def arguments(called: ToolCalled) -> str:
    """Arguments d'un appel, en ``nom=valeur``."""
    return ", ".join(f"{name}={value!r}" for name, value in called.arguments.items())
