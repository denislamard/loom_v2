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

# Un message libre est routé vers la question même s'il parle d'autre chose
# (D-3) : sans cette consigne, le modèle y répond et repose sa question en
# texte, et le run se termine sans question ouverte (essai Haiku, ask-06).
# La demande de départ répétée est nommée : Haiku la prenait pour une
# nouvelle demande et finissait sur la question écrite en texte (ask-12).
OFF_TOPIC = (
    "Si ce message ne répond pas à ta question (autre sujet, ou ta demande de départ "
    "répétée), traite-le si besoin, puis repose ta question avec ask, jamais dans ta "
    "réponse en texte : le run se terminerait sans attendre la réponse."
)


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

    Le message nomme les questions par leur intitulé et dit la règle : il est
    rendu tel quel par chaque accès (``InvalidAnswer`` du SDK, ``detail`` d'un
    422 REST, refus de la CLI). Avant, « 1 réponse(s) pour 2 question(s) » ne
    disait ni lesquelles ni comment répondre (essai réel t-2000 du 02/11).
    Il ne part ni au modèle ni au journal.
    """
    if (reply.answers is None) == (reply.text is None):
        return "une réponse donne soit 'answers' (une par question), soit 'text'"
    if reply.text is not None:
        return None if reply.text.strip() else "réponse vide"
    answers = reply.answers or ()
    wanted = pending.questions
    if len(answers) != len(wanted):
        return (
            f"{_count(len(answers), 'réponse')} pour {_count(len(wanted), 'question')} "
            f"({_names(wanted)}) : une réponse par question, dans leur ordre"
        )
    for number, (question, values) in enumerate(zip(wanted, answers, strict=True), 1):
        if not values or not all(value.strip() for value in values):
            return f"{named_question(number, question)} : réponse vide"
        if len(values) > 1 and not question.multiple:
            return f"{named_question(number, question)} : un seul choix attendu"
    return None


def _count(n: int, word: str) -> str:
    """« 1 réponse », « 2 réponses » : le pluriel français dès 2."""
    return f"{n} {word}{'s' if n > 1 else ''}"


def named_question(number: int, question: Question) -> str:
    """« question 2 (Acompte) », ou « question 2 » sans intitulé."""
    header = (question.header or "").strip()
    return f"question {number} ({header})" if header else f"question {number}"


def _names(questions: Sequence[Question]) -> str:
    """Les questions attendues : leurs intitulés, sinon leurs rangs.

    « Relances, Acompte » ; sans aucun intitulé, « questions 1 et 2 » (ou
    « question 1 ») ; un intitulé absent est remplacé par « question i ».
    """
    headers = [(question.header or "").strip() for question in questions]
    if any(headers):
        return ", ".join(h or f"question {i}" for i, h in enumerate(headers, 1))
    ranks = [str(i) for i in range(1, len(questions) + 1)]
    if len(ranks) == 1:
        return "question 1"
    return f"questions {', '.join(ranks[:-1])} et {ranks[-1]}"


def answered_output(pending: PendingQuestion) -> ToolOutput:
    """Ce que le modèle reçoit : chaque question avec sa réponse, ou le message libre."""
    reply = pending.reply
    assert reply is not None
    data: dict[str, JsonValue]
    if reply.text is not None:
        data = {"reply": reply.text}
        text = f"Réponse de l'utilisateur, en message libre : {reply.text}\n\n{OFF_TOPIC}"
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
