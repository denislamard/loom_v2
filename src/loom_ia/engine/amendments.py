# SPDX-License-Identifier: Apache-2.0
"""Mention, dans la requête, des appels partis avec d'autres arguments que ceux du modèle.

Un approbateur qui corrige un appel, ou une politique ``before_tool`` qui en
remplace les arguments, réécrit l'appel dans la conversation : le modèle lit
ce qui est parti (21/09). Mais il croit alors l'avoir écrit, et s'attribue la
correction (« je l'ai inventé ») ou cherche à la défaire. Une note du système
dit donc qui a changé quoi.

Elle suit les résultats du tour, dans un message à elle, et non dans le
résultat de l'outil : placée dans le résultat, elle se lit comme une donnée de
l'outil, dont le modèle se méfie. Au test H4 du 20/10, une correction qui
contredisait la demande (95 € au lieu des 90 € demandés) a été prise pour une
erreur du modèle (« une modification faite par « denis » que je ne peux pas
vérifier »), qui proposait de la défaire.

Comme les références ``[result:n]``, la mention n'existe que dans la requête :
le journal garde les résultats tels quels, et un résultat devenu réponse finale
(outil terminal) n'en porte pas. Elle ne nomme que les champs : les nouvelles
valeurs sont dans l'appel réécrit, les anciennes peuvent être longues ou
sensibles — c'est aussi ce qui permet à une politique de filtrer sans dire quoi.
"""

from collections.abc import Sequence

from loom_ia.core.model import (
    SYSTEM_NOTE,
    Amendment,
    Message,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
)

PREFIX = SYSTEM_NOTE
EXECUTED = "c'est exactement ce qui a été exécuté. Tes arguments d'origine n'ont pas été utilisés."


def amendment_note(amendment: Amendment, tool: str | None = None) -> str:
    """Texte de la mention d'un changement d'arguments.

    Le modèle lit l'appel réécrit dans son propre tour, et croit d'abord l'avoir
    écrit. Au test du 18/10, « Appel modifié par denis » laissait Haiku
    s'attribuer la signature ajoutée par l'approbateur ; « L'appel ci-dessus
    n'est pas celui que tu as écrit » lui faisait croire qu'une autre version,
    invisible, était partie. La mention dit donc les trois choses : qui a
    remplacé quoi, que l'appel affiché porte déjà ces arguments, et que les
    siens n'ont pas servi. Pour un approbateur, elle ajoute que c'est sa
    décision, à ne pas défaire (test H4 du 20/10 : sans cela, une correction
    contraire à la demande était prise pour une erreur à réparer).
    """
    call = f"ton appel à {tool}" if tool else "ton appel"
    details = f"champs : {', '.join(amendment.fields)}"
    if amendment.reason:
        details += f" ; motif : {amendment.reason}"
    executed = EXECUTED
    match amendment.origin:
        case "approval":
            if amendment.by:
                done = f"{amendment.by} a remplacé les arguments de {call}"
                shown = f"les arguments de {amendment.by}"
                who, told = f"de {amendment.by}", f"{amendment.by} a corrigé l'appel"
            else:
                done = f"les arguments de {call} ont été remplacés à l'approbation"
                shown = "les arguments corrigés"
                who, told = "de l'approbateur", "l'appel a été corrigé à l'approbation"
            executed += (
                f" C'est une décision {who}, qui valide les appels de cette application : "
                "ne propose pas de revenir à tes arguments d'origine ; si elle diffère de "
                f"ce que l'utilisateur avait demandé, dis-lui que {told}."
            )
        case "policy":
            # Nommée, avec son motif (test E7 du 19/10) : sans lui, Haiku a cru
            # à son erreur, refait l'appel identique, puis écarté le résultat.
            names = (amendment.by or "").split(", ") if amendment.by else []
            if len(names) > 1:
                done = f"les politiques {amendment.by} ont remplacé les arguments de {call}"
            elif names:
                done = f"la politique {amendment.by} a remplacé les arguments de {call}"
            else:
                done = f"une politique a remplacé les arguments de {call}"
            shown = "les arguments remplacés"
            executed = (
                EXECUTED.removesuffix(".")
                + ", et un nouvel appel avec eux serait remplacé de même."
            )
    return (
        f"[{PREFIX} : {done} avant exécution ({details}). "
        f"L'appel affiché plus haut porte déjà {shown} : {executed}]"
    )


def note_amendments(
    messages: Sequence[Message], amendments: Sequence[Amendment]
) -> tuple[Message, ...]:
    """Messages d'une requête, avec la note de chaque appel modifié après les résultats de son tour.

    La note part dans un message ``user`` placé après le dernier message
    ``tool`` du tour (les adaptateurs le fusionnent derrière les résultats) :
    même place que les images renvoyées hors des résultats (``engine/media.py``).
    """
    if not amendments:
        return tuple(messages)
    names = {
        block.call_id: block.name
        for message in messages
        for block in message.blocks
        if isinstance(block, ToolCallBlock)
    }
    # Une approbation et une politique peuvent corriger le même appel : une note chacune.
    pending: dict[str, list[Amendment]] = {}
    for amendment in amendments:
        pending.setdefault(amendment.call_id, []).append(amendment)
    noted: list[Message] = []
    notes: list[str] = []
    for index, message in enumerate(messages):
        noted.append(message)
        if message.role != "tool":
            continue
        for block in message.blocks:
            if isinstance(block, ToolResultBlock):
                for amendment in pending.pop(block.call_id, ()):
                    notes.append(amendment_note(amendment, names.get(block.call_id)))
        following = messages[index + 1] if index + 1 < len(messages) else None
        if notes and (following is None or following.role != "tool"):
            noted.append(Message(role="user", blocks=(TextBlock(text="\n".join(notes)),)))
            notes = []
    return tuple(noted)
