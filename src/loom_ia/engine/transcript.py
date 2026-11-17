# SPDX-License-Identifier: Apache-2.0
"""Historique rendu en texte, pour un modèle qui le lit plutôt qu'il ne le poursuit.

Un segment de conversation ne peut pas être envoyé tel quel à un rôle ou à un
agent de compaction : ce sont ses messages, pas les leurs. On le rend donc en
texte suivi — qui a dit quoi, quel outil a été appelé, ce qu'il a répondu.

Les résultats d'outils sont tronqués : ce qui compte ici est le fil de la
conversation, pas le détail d'un gros JSON. Le texte rendu est celui que le
contrôle de fidélité compare au résumé (point 23), donc la troncature fait partie
de ce qui est vérifié.

Utilisé par la compaction (``sessions/compaction.py``), les rôles qui
reçoivent l'historique (``engine/roles.py``) et les juges
(``guards/judge.py``).
"""

import json
from collections.abc import Sequence
from typing import Final

from loom_ia.core.model import (
    ArtifactRefBlock,
    JsonBlock,
    Message,
    TextBlock,
    ToolOutput,
    ToolResultBlock,
)

MAX_RESULT_CHARS: Final = 800
"""Longueur maximale d'un résultat d'outil dans le texte rendu, en caractères."""
CUT_MARK: Final = "… (résultat tronqué)"
"""Marque ajoutée à un résultat coupé."""
TURN_SEPARATOR: Final = "\n\n---\n\n"
"""Séparateur entre deux tours."""


def rendered(messages: Sequence[Message]) -> str:
    """Suite de messages rendue en texte suivi.

    Chaque message devient une ou plusieurs lignes : « Utilisateur : … »,
    « Agent : … », « Agent appelle <outil> : <arguments JSON> », « Résultat :
    … » (coupé à 800 caractères). Le raisonnement n'y figure pas ; un fichier
    se dit « [fichier <nom>] ».

    Args:
        messages: Les messages, dans l'ordre.

    Returns:
        Le texte, paragraphes séparés par une ligne vide.
    """
    parts: list[str] = []
    for message in messages:
        match message.role:
            case "user":
                parts.append(f"Utilisateur : {_body(message)}")
            case "assistant":
                body = _body(message)
                if body:
                    parts.append(f"Agent : {body}")
                for call in message.tool_calls:
                    arguments = json.dumps(call.arguments, ensure_ascii=False)
                    parts.append(f"Agent appelle {call.name} : {arguments}")
            case "tool":
                for block in message.blocks:
                    if isinstance(block, ToolResultBlock):
                        parts.append(f"Résultat : {_short(_output(block.output))}")
    return "\n\n".join(part for part in parts if part.strip())


def transcript(turns: Sequence[Sequence[Message]]) -> str:
    """Tours rendus en texte, séparés par une ligne ``---``.

    Args:
        turns: Les tours, chacun une suite de messages.

    Returns:
        Le texte ; un tour vide n'y laisse pas de trace.
    """
    texts = [rendered(turn) for turn in turns]
    return TURN_SEPARATOR.join(text for text in texts if text)


def _body(message: Message) -> str:
    """Le texte d'un message : textes, JSON et fichiers, sans raisonnement ni appels."""
    pieces: list[str] = []
    for block in message.blocks:
        match block:
            case TextBlock(text=text) if text:
                pieces.append(text)
            case JsonBlock(data=data):
                pieces.append(json.dumps(data, ensure_ascii=False))
            case ArtifactRefBlock(uri=uri, name=name):
                pieces.append(f"[fichier {name or uri}]")
            case _:
                continue
    return "\n".join(pieces)


def _output(output: ToolOutput) -> str:
    """Le texte d'un résultat d'outil ; « erreur — <message> » pour un résultat en erreur."""
    if output.is_error:
        return f"erreur — {output.as_text}"
    pieces: list[str] = []
    for block in output.blocks:
        match block:
            case JsonBlock(data=data):
                pieces.append(json.dumps(data, ensure_ascii=False))
            case ArtifactRefBlock(uri=uri, name=name):
                pieces.append(f"[fichier {name or uri}]")
            case TextBlock(text=text):
                pieces.append(text)
            case _:
                continue
    return "\n".join(piece for piece in pieces if piece)


def _short(text: str) -> str:
    """Le texte coupé à ``MAX_RESULT_CHARS``, marqué s'il l'est."""
    return text if len(text) <= MAX_RESULT_CHARS else text[:MAX_RESULT_CHARS] + CUT_MARK
