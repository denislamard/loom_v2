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

NOT_YOURS = "L'appel ci-dessus n'est pas celui que tu as écrit"
EXECUTED = "C'est cette version qui a été exécutée."


def amendment_note(amendment: Amendment) -> str:
    """Texte de la mention d'un changement d'arguments.

    Elle commence par dire que l'appel n'est pas du modèle : il le lit dans son
    propre tour, et c'est ce qu'il croit d'abord. Au test du 18/10, une mention
    qui ne disait que « Appel modifié par denis » laissait Haiku s'attribuer la
    signature ajoutée par l'approbateur.
    """
    details = f"champs : {', '.join(amendment.fields)}"
    match amendment.origin:
        case "approval":
            if amendment.reason:
                details += f" ; motif : {amendment.reason}"
            who = (
                f"{amendment.by} l'a modifié"
                if amendment.by
                else "il a été modifié à l'approbation"
            )
            text = f"{who} avant exécution ({details})"
        case "policy":
            text = f"une politique a remplacé ses arguments avant exécution ({details})"
    return f"[{NOT_YOURS} : {text}. {EXECUTED}]"


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
