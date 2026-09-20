# SPDX-License-Identifier: Apache-2.0
"""Historique d'une session envoyé au modèle (#7, #22).

Seuls les runs racine terminés avec succès y figurent : un run échoué peut
contenir un appel d'outil sans résultat, que les API refusent. Les sous-runs,
écrits dans le même journal, n'en font pas partie. Le raisonnement et les
textes vides sont retirés ; un message qui ne contient plus rien est omis
(certaines API refusent un bloc de texte vide).

Sortie d'un outil terminal (#13) : le résultat est remplacé par un marqueur,
et la sortie suit en message ``assistant``. Elle n'est pas dupliquée, et la
séquence reste valide pour les API (résultat d'outil, puis assistant).

Réparations (#20) : une réponse refusée par une politique et le diagnostic
qui la suit (``message.user`` de ``kind: repair``) restent dans le journal,
mais pas dans l'historique : seule la réponse acceptée y figure. Une réponse
finale remplacée par une politique y figure sous sa forme retenue.

Les snapshots et la compaction (J4) s'ajouteront ici.
"""

from collections.abc import Iterable
from typing import Final

from loom_ia.core.events import Event
from loom_ia.core.model import (
    Message,
    RunState,
    RunStatus,
    TextBlock,
    ToolOutput,
    ToolResultBlock,
)
from loom_ia.core.projections.run_state import fold_all

TERMINAL_MARKER: Final = "[Sortie transmise telle quelle comme réponse finale.]"


def history(events: Iterable[Event]) -> list[Message]:
    """Messages des runs racine terminés, dans l'ordre du journal."""
    messages: list[Message] = []
    for state in fold_all(events).values():
        if state.parent_run_id is not None or state.status is not RunStatus.COMPLETED:
            continue
        for message in _conversation(state):
            kept = message.without_reasoning()
            if kept is not None:
                kept = _without_empty_text(kept)
            if kept is not None:
                messages.append(kept)
    return messages


def _conversation(state: RunState) -> tuple[Message, ...]:
    """Messages du run, sortie terminale comprise, sans les tentatives refusées."""
    messages = _accepted(state)
    call_id = state.terminal_call_id
    if call_id is None or state.output is None:
        if state.output is not None and messages and messages[-1].role == "assistant":
            # Réponse finale, éventuellement remplacée par une politique.
            return (*messages[:-1], state.output)
        return messages
    marker = ToolOutput.text(TERMINAL_MARKER)
    kept = tuple(
        message.model_copy(
            update={
                "blocks": tuple(
                    ToolResultBlock(call_id=call_id, output=marker)
                    if isinstance(block, ToolResultBlock) and block.call_id == call_id
                    else block
                    for block in message.blocks
                )
            }
        )
        if message.role == "tool"
        else message
        for message in messages
    )
    return (*kept, state.output)


def _accepted(state: RunState) -> tuple[Message, ...]:
    """Messages du run sans les réponses refusées ni leurs diagnostics.

    Chaque réparation retire la dernière réponse de l'assistant qui la
    précède, les résultats d'outils qui suivent cette réponse, et le
    diagnostic : la séquence reste valide pour les API.
    """
    if not state.repairs:
        return state.messages
    dropped: set[int] = set()
    for repair in state.repairs:
        start = next(
            (i for i in range(repair - 1, -1, -1) if state.messages[i].role == "assistant"),
            repair,
        )
        dropped.update(range(start, repair + 1))
    return tuple(m for i, m in enumerate(state.messages) if i not in dropped)


def _without_empty_text(message: Message) -> Message | None:
    kept = tuple(b for b in message.blocks if not (isinstance(b, TextBlock) and not b.text))
    if len(kept) == len(message.blocks):
        return message
    if not kept:
        return None
    return message.model_copy(update={"blocks": kept})
