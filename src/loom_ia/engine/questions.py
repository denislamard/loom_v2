# SPDX-License-Identifier: Apache-2.0
"""Questions à l'utilisateur : lues dans l'appel, rendues au modèle.

L'outil ``ask`` (``ToolSpec.asks_user``) n'est pas exécuté : l'exécuteur lit
ses questions ici, les écrit au journal, et le run attend la réponse en
``PAUSED``. La réponse revient au modèle comme résultat de l'appel, sous la
forme que ce module lui donne.
"""

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta

from pydantic import JsonValue, ValidationError

from loom_ia.core.model import TextBlock, ToolOutput
from loom_ia.core.model.question import PendingQuestion, Question, QuestionReply


def asked(arguments: Mapping[str, JsonValue]) -> tuple[Question, ...] | str:
    """Les questions de l'appel, ou ce qui ne va pas (pour le modèle)."""
    given = arguments.get("questions")
    if not isinstance(given, list) or not given:
        return "Donner 'questions' : une liste d'au moins une question."
    try:
        return tuple(Question.model_validate(item) for item in given)
    except ValidationError as exc:
        first = exc.errors(include_url=False)[0]
        where = ".".join(str(part) for part in first["loc"])
        return f"Question invalide ({where}) : {first['msg']}"


def expiry(expires_in: float | None) -> datetime | None:
    return None if expires_in is None else datetime.now(UTC) + timedelta(seconds=expires_in)


def reply_problem(pending: PendingQuestion, reply: QuestionReply) -> str | None:
    """Pourquoi cette réponse ne convient pas à ces questions ; ``None`` si elle convient.

    Un texte libre convient toujours. Des réponses par question : une par
    question, au moins une valeur chacune, une seule sauf ``multiple``. Un
    choix proposé n'est pas exigé : l'utilisateur peut répondre autre chose.
    """
    if (reply.answers is None) == (reply.text is None):
        return "une réponse donne soit 'answers' (une par question), soit 'text'"
    if reply.text is not None:
        return None if reply.text.strip() else "réponse vide"
    answers = reply.answers or ()
    if len(answers) != len(pending.questions):
        return f"{len(answers)} réponse(s) pour {len(pending.questions)} question(s)"
    for number, (question, values) in enumerate(zip(pending.questions, answers, strict=True), 1):
        if not values or not all(value.strip() for value in values):
            return f"question {number} : réponse vide"
        if len(values) > 1 and not question.multiple:
            return f"question {number} : un seul choix attendu"
    return None


def answered_output(pending: PendingQuestion) -> ToolOutput:
    """Ce que le modèle reçoit : chaque question avec sa réponse, ou le message libre."""
    reply = pending.reply
    assert reply is not None
    data: dict[str, JsonValue]
    if reply.text is not None:
        data = {"reply": reply.text}
        text = f"Réponse de l'utilisateur, en message libre : {reply.text}"
    else:
        pairs: list[JsonValue] = [
            {"question": question.question, "answer": list(values)}
            for question, values in zip(pending.questions, reply.answers or (), strict=True)
        ]
        data = {"answers": pairs}
        text = "Réponse de l'utilisateur :\n" + "\n".join(
            f"- {question.question} → {', '.join(values)}"
            for question, values in zip(pending.questions, reply.answers or (), strict=True)
        )
    return ToolOutput(blocks=(TextBlock(text=text),), data=data)


def expired_output(pending: PendingQuestion) -> ToolOutput:
    return ToolOutput.error(
        "L'utilisateur n'a pas répondu dans le délai. Ne repose pas la question de toi-même ; "
        "continue sans cette information si tu le peux, sinon dis-lui ce qui manque."
    )


def summary(questions: Sequence[Question]) -> str:
    """Les questions en une ligne, pour une note ou un affichage."""
    return " ; ".join(q.question for q in questions)


def as_json(questions: Sequence[Question]) -> str:
    return json.dumps(
        [q.model_dump(mode="json", exclude_none=True) for q in questions], ensure_ascii=False
    )
