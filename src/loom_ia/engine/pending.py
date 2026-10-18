# SPDX-License-Identifier: Apache-2.0
"""Ce qui attend un humain dans la conversation, dit au modèle d'un nouveau tour.

L'historique d'une session ne reprend que les runs racine terminés : un run en
pause porte un appel sans résultat, que les API refusent (``history.py``). Un
tour lancé pendant cette pause ne voit donc rien de l'action en attente — au
test du 18/10, Haiku a proposé d'« envoyer une relance » qui attendait déjà
l'approbation de Denis. La requête reçoit une note qui la nomme.

La note est tirée des événements de la session **antérieurs au run** : la
reprise et le rejeu la retrouvent à l'identique. C'est une photo du début du
tour — une décision prise pendant qu'il tourne ne la change pas. Elle n'existe
que dans la requête, comme le state de session, et se place entre l'historique
et la demande du tour : le cache de l'historique reste valable, et les
adaptateurs la fusionnent avec la demande. Les arguments y figurent, coupés :
ils viennent de la même conversation, et ce sont eux qui disent **quelle**
action attend.
"""

import json
from collections.abc import Sequence
from typing import Final

from loom_ia.core.model import Message, PendingApproval

ARGUMENTS_CHARS: Final = 300


def pending_note(approvals: Sequence[PendingApproval]) -> Message | None:
    """Message à placer avant la demande du tour, ou rien s'il n'y a rien en attente."""
    if not approvals:
        return None
    items = "; ".join(_item(approval) for approval in approvals)
    if len(approvals) == 1:
        text = (
            "au début de ce tour, une action demandée plus tôt dans cette conversation "
            f"attend la décision d'un humain : {items}. Elle sera exécutée ou refusée "
            "sans toi ; ne la redemande pas."
        )
    else:
        text = (
            "au début de ce tour, des actions demandées plus tôt dans cette conversation "
            f"attendent la décision d'un humain : {items}. Elles seront exécutées ou "
            "refusées sans toi ; ne les redemande pas."
        )
    return Message.user(f"[Note du système : {text}]")


def _item(approval: PendingApproval) -> str:
    arguments = json.dumps(
        approval.arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    if len(arguments) > ARGUMENTS_CHARS:
        arguments = f"{arguments[: ARGUMENTS_CHARS - 1]}…"
    return f"{approval.tool_name} (appel {approval.call_id}, arguments : {arguments})"
