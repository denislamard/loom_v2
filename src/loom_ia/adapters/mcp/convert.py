# SPDX-License-Identifier: Apache-2.0
"""Traductions entre MCP et loom-ia : déclarations d'outils, résultats (#15, #19).

**Annotations → déclarations.** Les annotations d'un serveur sont des
indications, pas des garanties ; elles donnent seulement les valeurs par
défaut, que la config remplace. Sans annotation, la spec MCP considère un
outil comme modifiant (``readOnlyHint: false``) et destructeur
(``destructiveHint: true``) : il est donc traité comme irréversible.

**Schéma d'entrée.** Il est transmis tel quel, sauf son ``title`` racine
(``maintenantArguments`` chez FastMCP) : un nom technique sans intérêt pour le
modèle, que les outils Python retirent déjà.

**Résultats.** Les blocs ``text`` deviennent des blocs texte, et le contenu
structuré (``structuredContent``) le ``data`` du résultat. Images, audio et
ressources binaires sont remplacés par une mention en attendant les
artefacts (phase 2.3).
"""

from typing import cast

from mcp import types
from pydantic import JsonValue

from loom_ia.core.model import OutputBlock, SideEffects, TextBlock, ToolOutput


def declared(annotations: types.ToolAnnotations | None) -> tuple[SideEffects, bool]:
    """Effets de bord et idempotence annoncés par le serveur."""
    hints = annotations or types.ToolAnnotations()
    if hints.readOnlyHint:
        return "none", True
    side_effects: SideEffects = "reversible" if hints.destructiveHint is False else "irreversible"
    return side_effects, bool(hints.idempotentHint)


def input_schema(tool: types.Tool) -> dict[str, JsonValue]:
    """Schéma d'entrée montré au modèle : celui du serveur, sans son ``title`` racine."""
    schema = cast(dict[str, JsonValue], dict(tool.inputSchema))
    schema.pop("title", None)
    return schema


def description(tool: types.Tool) -> str:
    """Description montrée au modèle : celle de l'outil, sinon son titre."""
    title = tool.title or (tool.annotations.title if tool.annotations else None)
    return tool.description or title or ""


def to_output(result: types.CallToolResult) -> ToolOutput:
    """Résultat MCP traduit en ``ToolOutput``."""
    blocks = tuple(_block(content) for content in result.content)
    data = cast(JsonValue, result.structuredContent)
    return ToolOutput(blocks=blocks, data=data, is_error=result.isError)


def _block(content: types.ContentBlock) -> OutputBlock:
    match content:
        case types.TextContent(text=text):
            return TextBlock(text=text)
        case types.ImageContent(mimeType=mime, data=data):
            return TextBlock(text=_withheld("image", mime, data))
        case types.AudioContent(mimeType=mime, data=data):
            return TextBlock(text=_withheld("audio", mime, data))
        case types.ResourceLink(uri=uri, name=name):
            return TextBlock(text=f"[ressource {name} : {uri}]")
        case types.EmbeddedResource(resource=types.TextResourceContents(text=text)):
            return TextBlock(text=text)
        case types.EmbeddedResource(resource=resource):
            mime = resource.mimeType or "application/octet-stream"
            return TextBlock(text=f"[ressource binaire {resource.uri} ({mime}) non transmise]")


def _withheld(kind: str, mime: str, data: str) -> str:
    size = len(data) * 3 // 4
    return f"[{kind} {mime}, environ {size} octets : non transmis avant les artefacts (2.3)]"
