# SPDX-License-Identifier: Apache-2.0
"""Mention, dans la requête, des appels partis avec d'autres arguments que ceux du modèle.

Un approbateur qui corrige un appel, ou une politique ``before_tool`` qui en
remplace les arguments, réécrit l'appel dans la conversation : le modèle lit
ce qui est parti (21/09). Mais il croit alors l'avoir écrit, et s'attribue la
correction (« je l'ai inventé ») ou cherche à la défaire. Le résultat de
l'outil est donc précédé d'une mention qui dit qui a changé quoi.

Comme les références ``[result:n]``, la mention n'existe que dans la requête :
le journal garde les résultats tels quels, et un résultat devenu réponse finale
(outil terminal) n'en porte pas. Elle ne nomme que les champs : les nouvelles
valeurs sont dans l'appel réécrit, les anciennes peuvent être longues ou
sensibles — c'est aussi ce qui permet à une politique de filtrer sans dire quoi.
"""

from collections.abc import Sequence

from loom_ia.core.model import (
    Amendment,
    JsonBlock,
    Message,
    OutputBlock,
    TextBlock,
    ToolResultBlock,
)

PREFIX = "Note du système"
EXECUTED = "c'est exactement ce qui a été exécuté. Tes arguments d'origine n'ont pas été utilisés."


def amendment_note(amendment: Amendment) -> str:
    """Texte de la mention d'un changement d'arguments.

    Le modèle lit l'appel réécrit dans son propre tour, et croit d'abord l'avoir
    écrit. Au test du 18/10, « Appel modifié par denis » laissait Haiku
    s'attribuer la signature ajoutée par l'approbateur ; « L'appel ci-dessus
    n'est pas celui que tu as écrit » lui faisait croire qu'une autre version,
    invisible, était partie. La mention dit donc les trois choses : qui a
    remplacé quoi, que l'appel affiché porte déjà ces arguments, et que les
    siens n'ont pas servi.
    """
    details = f"champs : {', '.join(amendment.fields)}"
    if amendment.reason:
        details += f" ; motif : {amendment.reason}"
    executed = EXECUTED
    match amendment.origin:
        case "approval":
            if amendment.by:
                done = f"{amendment.by} a remplacé les arguments de ton appel"
                shown = f"les arguments de {amendment.by}"
            else:
                done = "les arguments de ton appel ont été remplacés à l'approbation"
                shown = "les arguments corrigés"
        case "policy":
            # Nommée, avec son motif (test E7 du 19/10) : sans lui, Haiku a cru
            # à son erreur, refait l'appel identique, puis écarté le résultat.
            names = (amendment.by or "").split(", ") if amendment.by else []
            if len(names) > 1:
                done = f"les politiques {amendment.by} ont remplacé les arguments de ton appel"
            elif names:
                done = f"la politique {amendment.by} a remplacé les arguments de ton appel"
            else:
                done = "une politique a remplacé les arguments de ton appel"
            shown = "les arguments remplacés"
            executed = (
                EXECUTED.removesuffix(".")
                + ", et un nouvel appel avec eux serait remplacé de même."
            )
    return (
        f"[{PREFIX} : {done} avant exécution ({details}). "
        f"L'appel affiché ci-dessus porte déjà {shown} : {executed}]"
    )


def note_amendments(
    messages: Sequence[Message], amendments: Sequence[Amendment]
) -> tuple[Message, ...]:
    """Messages d'une requête, le résultat de chaque appel modifié précédé de sa mention."""
    if not amendments:
        return tuple(messages)
    notes: dict[str, list[str]] = {}
    for amendment in amendments:
        notes.setdefault(amendment.call_id, []).append(amendment_note(amendment))
    return tuple(
        _noted(message, notes) if message.role == "tool" else message for message in messages
    )


def _noted(message: Message, notes: dict[str, list[str]]) -> Message:
    blocks = tuple(
        _with_note(block, notes[block.call_id])
        if isinstance(block, ToolResultBlock) and block.call_id in notes
        else block
        for block in message.blocks
    )
    return message if blocks == message.blocks else message.model_copy(update={"blocks": blocks})


def _with_note(block: ToolResultBlock, notes: list[str]) -> ToolResultBlock:
    content: tuple[OutputBlock, ...] = block.output.blocks
    if not content and block.output.data is not None:
        # Sans blocs, les adaptateurs montrent ``data`` : il ne doit pas disparaître.
        content = (JsonBlock(data=block.output.data),)
    note = TextBlock(text="\n".join(notes))
    output = block.output.model_copy(update={"blocks": (note, *content)})
    return block.model_copy(update={"output": output})
