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

Marqueurs de session (J4.1) : un ``session.snapshot`` porte l'historique déjà
calculé jusqu'à une position du journal. L'historique repart du marqueur au
plus grand ``up_to_seq``, puis rejoue la suite. Sans marqueur, tout le
journal est relu : le résultat est le même, la relecture plus longue.

Un run de compaction (``kind: compaction``) vit dans le journal de la
session mais n'entre pas dans son historique : il la résume, il ne la
poursuit pas.
"""

from collections.abc import Iterable
from typing import Final

from loom_ia.core.events import Event, RunStarted, SessionSnapshot
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
    base, after = _from_marker(list(events))
    messages: list[Message] = list(base)
    for state in _replayed(after):
        if state.parent_run_id is not None or state.kind != "normal":
            continue
        if state.status is not RunStatus.COMPLETED:
            continue
        for message in _conversation(state):
            kept = message.without_reasoning()
            if kept is not None:
                kept = _without_empty_text(kept)
            if kept is not None:
                messages.append(kept)
    return messages


def _marker(event: Event) -> tuple[int, tuple[Message, ...]] | None:
    """Position couverte et historique repris, pour un marqueur de session."""
    match event.payload:
        case SessionSnapshot(up_to_seq=up_to_seq, messages=messages):
            return up_to_seq, messages
        case _:
            return None


def _from_marker(events: list[Event]) -> tuple[tuple[Message, ...], list[Event]]:
    """Base de l'historique et événements restant à rejouer.

    Le marqueur retenu est celui qui couvre le plus de journal : après une
    compaction, le snapshot qui la suit la contient déjà.
    """
    best: tuple[int, tuple[Message, ...]] | None = None
    for event in events:
        marker = _marker(event)
        if marker is not None and (best is None or marker[0] >= best[0]):
            best = marker
    if best is None:
        return (), events
    up_to_seq, base = best
    return base, [event for event in events if event.seq > up_to_seq]


def _replayed(events: list[Event]) -> list[RunState]:
    """États des runs dont le journal commence dans ces événements.

    Un run commencé avant le marqueur y est déjà pris en compte : le reprendre
    au milieu lèverait une erreur de projection.
    """
    started = {e.run_id for e in events if isinstance(e.payload, RunStarted)}
    return list(fold_all(e for e in events if e.run_id in started).values())


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
