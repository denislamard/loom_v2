"""Événements montrés à l'interface, traduits depuis le flux et le journal de loom.

La même traduction sert au run en direct, au suivi d'un run repris et à la
relecture d'une session : l'écran montre donc la même chose dans les trois cas.
"""

import json
from dataclasses import dataclass
from typing import Any, Literal, cast

from loom_ia.access.api import StreamItem
from loom_ia.core.events.envelope import Event
from loom_ia.core.events.payloads import (
    ApprovalExpired,
    ApprovalGranted,
    ApprovalRejected,
    ApprovalRequested,
    ModelResponded,
    QuestionAnswered,
    QuestionExpired,
    RunCancelled,
    RunCompleted,
    RunFailed,
    ToolCalled,
    ToolCompleted,
    UserMessage,
)
from loom_ia.core.events.payloads import QuestionAsked as AskPayload
from loom_ia.core.model.content import ArtifactRefBlock, InlineDataBlock, JsonBlock, TextBlock, ToolOutput
from loom_ia.core.model.question import Question
from loom_ia.core.model.streaming import TextDelta as ModelText

SUMMARY_WIDTH = 160
SIGNATURE_WIDTH = 70
DETAIL_LIMIT = 4000

type ToolStatus = Literal["running", "waiting", "rejected", "expired", "done", "error"]


@dataclass(frozen=True, slots=True)
class Seen:
    """Un événement du journal est passé : sa session et son run racine sont connus.

    Attributes:
        session_id: Session de l'événement.
        run_id: Run racine de l'arbre, jamais un sous-run : c'est lui qu'on annule ou reprend.
    """

    session_id: str
    run_id: str


@dataclass(frozen=True, slots=True)
class UserLine:
    """Ce que l'utilisateur a dit : son message, ou sa réponse à une question.

    Attributes:
        text: Le texte affiché.
    """

    text: str


@dataclass(frozen=True, slots=True)
class TextChunk:
    """Un morceau de la réponse du modèle, au fil de l'eau (run en direct seulement).

    Attributes:
        text: Le morceau.
    """

    text: str


@dataclass(frozen=True, slots=True)
class ModelReply:
    """Une réponse complète du modèle, telle que le journal la garde.

    Elle remplace les morceaux reçus au fil de l'eau ; en suivi ou en relecture,
    où il n'y a pas de morceaux, elle est la seule trace du texte.

    Attributes:
        text: Le texte de la réponse.
    """

    text: str


@dataclass(frozen=True, slots=True)
class ToolStarted:
    """Un outil est appelé.

    Attributes:
        name: Nom de l'outil.
        call_id: Appel.
        arguments: Ses arguments.
    """

    name: str
    call_id: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolDone:
    """Un outil a rendu son résultat.

    Attributes:
        name: Nom de l'outil.
        call_id: Appel.
        summary: Résultat résumé sur une ligne (``summarize``).
        detail: Résultat complet, mis en forme (``detail``).
        is_error: Résultat en erreur.
        latency_ms: Durée de l'appel.
    """

    name: str
    call_id: str
    summary: str
    detail: str = ""
    is_error: bool = False
    latency_ms: float = 0.0


@dataclass(frozen=True, slots=True)
class ApprovalAsked:
    """Un outil sensible attend une décision humaine.

    Attributes:
        session_id: Session du run.
        run_id: Run qui a posé la demande (un sous-run, le cas échéant).
        call_id: Appel suspendu.
        tool: Nom de l'outil.
        arguments: Arguments de l'appel.
        reason: Pourquoi l'approbation est demandée.
    """

    session_id: str
    run_id: str
    call_id: str
    tool: str
    arguments: dict[str, Any]
    reason: str


@dataclass(frozen=True, slots=True)
class QuestionAsked:
    """Le modèle attend une réponse de l'utilisateur (outil ``ask``).

    Attributes:
        session_id: Session du run.
        run_id: Run qui a posé la question (un sous-run, le cas échéant).
        call_id: Appel de ``loom.ask``.
        questions: Les questions.
    """

    session_id: str
    run_id: str
    call_id: str
    questions: tuple[Question, ...]


@dataclass(frozen=True, slots=True)
class RequestClosed:
    """Une demande n'attend plus : tranchée, répondue ou expirée, ici ou ailleurs.

    Attributes:
        call_id: Appel de la demande.
        outcome: ``granted``, ``rejected``, ``answered`` ou ``expired``.
        text: La réponse donnée (``answered``), ou le motif du refus (``rejected``).
    """

    call_id: str
    outcome: Literal["granted", "rejected", "answered", "expired"] = "granted"
    text: str = ""


@dataclass(frozen=True, slots=True)
class ToolLine:
    """Un appel d'outil dans la conversation : un bloc, mis à jour sur place.

    Attributes:
        call_id: Appel.
        name: Nom de l'outil.
        arguments: Ses arguments.
        status: Où en est l'appel.
        summary: Résultat (ou réponse donnée) sur une ligne.
        detail: Résultat complet, montré quand le bloc est déplié.
        latency_ms: Durée de l'appel, une fois terminé.
        waiting_for: Ce qu'attend l'appel en pause : ``approbation`` ou ``question``.
    """

    call_id: str
    name: str
    arguments: dict[str, Any]
    status: ToolStatus = "running"
    summary: str = ""
    detail: str = ""
    latency_ms: float = 0.0
    waiting_for: str = ""


@dataclass(frozen=True, slots=True)
class RunEnded:
    """Fin du run racine : terminé, en échec ou annulé.

    Attributes:
        status: ``completed``, ``failed`` ou ``cancelled``.
        text: Réponse finale du run (déjà montrée, en général, par ``ModelReply``).
        cost_usd: Coût du run.
        error: Message d'échec, vide sinon.
    """

    status: str
    text: str
    cost_usd: float
    error: str


type Line = tuple[str, str] | ToolLine
type ChatEvent = Seen | UserLine | TextChunk | ModelReply | ToolStarted | ToolDone | ApprovalAsked | QuestionAsked | RequestClosed | RunEnded


def _json_line(data: object) -> str:
    """Un JSON sur une ligne : les valeurs d'un objet plat (``a · b``), sinon le JSON compact."""
    if isinstance(data, dict):
        values = list(cast(dict[str, object], data).values())
        if values and all(value is None or isinstance(value, str | int | float | bool) for value in values):
            return " · ".join(str(value) for value in values)
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def summarize(output: ToolOutput, width: int = SUMMARY_WIDTH) -> str:
    """Résume le résultat d'un outil sur une ligne : texte, valeurs d'un JSON plat, fichiers.

    Args:
        output: Résultat de l'outil.
        width: Longueur maximale du résumé.

    Returns:
        Le résumé ; ``ok`` pour un résultat vide.
    """
    parts: list[str] = []
    for block in output.blocks:
        match block:
            case TextBlock(text=text):
                parts.append(text)
            case JsonBlock(data=data):
                parts.append(_json_line(data))
            case ArtifactRefBlock(name=name, uri=uri):
                parts.append(f"fichier {name or uri}")
            case InlineDataBlock(media_type=media_type):
                parts.append(f"[{media_type}]")
    if not parts and output.data is not None:
        parts.append(_json_line(output.data))
    text = " ".join(" ".join(parts).split()) or "ok"
    return clip(text, width)


def detail(output: ToolOutput, limit: int = DETAIL_LIMIT) -> str:
    """Le résultat complet d'un outil, mis en forme pour être lu : texte tel quel, JSON indenté.

    Args:
        output: Résultat de l'outil.
        limit: Longueur maximale.
    """
    parts: list[str] = []
    for block in output.blocks:
        match block:
            case TextBlock(text=text):
                parts.append(text)
            case JsonBlock(data=data):
                parts.append(json.dumps(data, ensure_ascii=False, indent=2))
            case ArtifactRefBlock(name=name, uri=uri, media_type=media_type):
                parts.append(f"fichier {name or uri} ({media_type})")
            case InlineDataBlock(media_type=media_type):
                parts.append(f"[{media_type}]")
    if not parts and output.data is not None:
        parts.append(json.dumps(output.data, ensure_ascii=False, indent=2))
    return clip("\n".join(parts), limit)


def clip(text: str, width: int) -> str:
    """Coupe un texte à `width` caractères, points de suspension compris.

    Args:
        text: Le texte.
        width: Longueur maximale.
    """
    return text if len(text) <= width else text[: width - 1] + "…"


def signature(name: str, arguments: dict[str, Any], width: int = SIGNATURE_WIDTH) -> str:
    """L'appel écrit comme un appel de fonction : ``fetch_page(url="https://…")``.

    Args:
        name: Nom de l'outil.
        arguments: Ses arguments.
        width: Longueur maximale.
    """
    args = ", ".join(f"{key}={json.dumps(value, ensure_ascii=False, separators=(',', ':'))}" for key, value in arguments.items())
    call = f"{name}({args})"
    return call if len(call) <= width else call[: width - 2] + "…)"


def duration(ms: float) -> str:
    """Durée lisible, à la française : ``12 ms``, ``2,1 s``.

    Args:
        ms: Durée en millisecondes.
    """
    if ms < 1000:
        return f"{ms:.0f} ms"
    return f"{ms / 1000:.1f} s".replace(".", ",")


def reply_text(answers: tuple[tuple[str, ...], ...] | None, text: str | None) -> str:
    """La réponse de l'utilisateur à une question, sur une ligne.

    Args:
        answers: Les choix, une liste par question.
        text: La réponse libre, qui l'emporte.

    Returns:
        Le texte affiché.
    """
    return text or "; ".join(", ".join(chosen) for chosen in answers or ())


def to_chat_events(item: StreamItem) -> tuple[ChatEvent, ...]:
    """Traduit un élément du flux ou du journal de loom ; vide pour ce qui n'intéresse pas l'écran.

    Les demandes (approbations, questions) et les outils de tout l'arbre passent ;
    les messages, les réponses du modèle et la fin du run ne valent que pour le run
    racine — ceux d'un sous-agent restent entre lui et son parent.

    Args:
        item: Un morceau du modèle ou un événement du journal.

    Returns:
        Les événements de l'écran, dans l'ordre.
    """
    if isinstance(item, ModelText):
        return (TextChunk(text=item.text),)
    if not isinstance(item, Event):
        return ()
    seen = Seen(session_id=str(item.session_id), run_id=str(item.root_run_id))
    root = item.run_id == item.root_run_id
    sid, rid = seen.session_id, str(item.run_id)
    match item.payload:
        case UserMessage(kind="request") as payload if root:
            return (seen, UserLine(text=payload.message.text))
        case ModelResponded() as payload if root:
            return (seen, ModelReply(text=payload.message.text))
        case ToolCalled() as payload:
            return (seen, ToolStarted(name=payload.tool_name, call_id=payload.call_id, arguments=dict(payload.arguments)))
        case ToolCompleted() as payload:
            done = ToolDone(
                name=payload.tool_name,
                call_id=payload.call_id,
                summary=summarize(payload.output),
                detail=detail(payload.output),
                is_error=payload.output.is_error,
                latency_ms=payload.latency_ms,
            )
            return (seen, done)
        case ApprovalRequested() as payload:
            asked = ApprovalAsked(
                session_id=sid,
                run_id=rid,
                call_id=payload.call_id,
                tool=payload.tool_name,
                arguments=dict(payload.arguments),
                reason=payload.reason,
            )
            return (seen, asked)
        case AskPayload() as payload:
            return (seen, QuestionAsked(session_id=sid, run_id=rid, call_id=payload.call_id, questions=tuple(payload.questions)))
        case QuestionAnswered() as payload:
            return (seen, RequestClosed(call_id=payload.call_id, outcome="answered", text=reply_text(payload.answers, payload.text)))
        case ApprovalGranted() as payload:
            return (seen, RequestClosed(call_id=payload.call_id, outcome="granted"))
        case ApprovalRejected() as payload:
            return (seen, RequestClosed(call_id=payload.call_id, outcome="rejected", text=payload.reason))
        case ApprovalExpired() | QuestionExpired() as payload:
            return (seen, RequestClosed(call_id=payload.call_id, outcome="expired"))
        case RunCompleted() as payload if root:
            text = payload.output.text if payload.output is not None else ""
            return (seen, RunEnded(status="completed", text=text, cost_usd=payload.cost_usd, error=""))
        case RunFailed() as payload if root:
            return (seen, RunEnded(status="failed", text="", cost_usd=payload.cost_usd, error=payload.error))
        case RunCancelled() as payload if root:
            return (seen, RunEnded(status="cancelled", text="", cost_usd=payload.cost_usd, error=""))
        case _:
            return (seen,)
