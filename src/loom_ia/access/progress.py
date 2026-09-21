# SPDX-License-Identifier: Apache-2.0
"""Déroulé d'un run en lignes courtes (N4, D4).

Le direct de la CLI (``loom run --stream``) et les notifications de
progression du serveur MCP décrivent un run de la même façon : un appel
d'outil et son issue, un fichier rangé, un serveur indisponible, une
décision de politique, un contrôle de sortie et le verdict d'un juge, une
limite de budget atteinte, une bascule vers un modèle de secours, un
disjoncteur qui s'ouvre, un sous-agent qui démarre puis se termine. Les
lignes d'un sous-run sont décalées selon sa profondeur :

    · verifier(message='Vérifie : …')
      · sous-agent verificateur : démarré
      · math__calculer(expression='121 * 24')
      · math__calculer : fait
      · sous-agent verificateur : terminé
    · verifier : fait
"""

from collections.abc import Iterable
from typing import Final

from loom_ia.core.events import (
    ArtifactStored,
    BudgetExceeded,
    CircuitOpened,
    Event,
    GuardChecked,
    JudgeEvaluated,
    ModelFellBack,
    PolicyDecided,
    RunCancelled,
    RunCompleted,
    RunFailed,
    RunStarted,
    ToolCalled,
    ToolCompleted,
    ToolSourceUnavailable,
)
from loom_ia.core.model import CriterionScore, RunId
from loom_ia.usage import describe as describe_budget

INDENT: Final = "  "
# Longueur au-delà de laquelle un message d'erreur est coupé.
ERROR_CHARS: Final = 160
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


# Issue d'un contrôle de sortie, telle qu'une ligne du déroulé la nomme.
RESOLUTIONS: Final = {
    "retry": "réparation demandée",
    "fail": "refusée",
    "unverified": "gardée, non vérifiée",
    "fallback": "remplacée par le message de repli",
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
        case GuardChecked(outcome="passed"):
            normalized = " (après normalisation)" if payload.normalized else ""
            reason = f" — {payload.reason}" if payload.reason else ""
            return f"contrôle {payload.guard} {payload.target} : conforme{normalized}{reason}"
        case GuardChecked(outcome="failed", resolution=resolution):
            then = f", {RESOLUTIONS[resolution]}" if resolution is not None else ""
            return (
                f"contrôle {payload.guard} {payload.target} : non conforme{then} — {payload.reason}"
            )
        case GuardChecked():
            return f"contrôle {payload.guard} {payload.target} : ignoré — {payload.reason}"
        case JudgeEvaluated():
            return f"juge {payload.judge} ({payload.model_id}) : {notes(payload.criteria)}"
        case ModelFellBack(reason="circuit_open"):
            return (
                f"secours {payload.slot} : {payload.from_model} → {payload.to_model} — "
                f"{payload.error}"
            )
        case ModelFellBack():
            return (
                f"secours {payload.slot} : {payload.from_model} → {payload.to_model} — "
                f"model.{payload.reason} : {_short(payload.error)}"
            )
        case CircuitOpened():
            target = "modèle" if payload.target_kind == "model" else "serveur"
            return (
                f"disjoncteur ouvert : {target} {payload.target} écarté {payload.cooldown_s:g} s "
                f"(échecs de suite : {payload.failures})"
            )
        case BudgetExceeded():
            action = "arrêt" if payload.action == "stop" else "avertissement"
            text = describe_budget(payload.scope, payload.limit, payload.value, payload.spent)
            return f"{text} — {action}"
        case PolicyDecided(policy=policy) if policy in {
            "loom.contract",
            "loom.budget",
        } or policy.startswith("loom.judge."):
            # La ligne du contrôle ou du budget dit déjà tout.
            return None
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
        case RunCancelled(reason="parent") if subrun:
            return f"sous-agent {event.agent} : annulé avec son parent"
        case RunCancelled(by=str() as author):
            return f"run annulé par {author}"
        case RunCancelled():
            return "run annulé"
        case _:
            return None


def notes(criteria: Iterable[CriterionScore]) -> str:
    """Notes d'un juge, avec le seuil des critères qui ne l'atteignent pas."""
    return ", ".join(
        f"{c.name} {_score(c.score)}" + ("" if c.passed else f" (seuil {_score(c.min_score)})")
        for c in criteria
    )


def _score(value: float) -> str:
    return f"{value:.2f}".replace(".", ",")


def arguments(called: ToolCalled) -> str:
    """Arguments d'un appel, en ``nom=valeur``."""
    return ", ".join(f"{name}={value!r}" for name, value in called.arguments.items())


def _short(text: str) -> str:
    """Message d'erreur sur une ligne, coupé s'il est long."""
    line = " ".join(text.split())
    return line if len(line) <= ERROR_CHARS else f"{line[: ERROR_CHARS - 1]}…"
