# SPDX-License-Identifier: Apache-2.0
"""Rôles délégués (C1 à C3, #12, #13).

Un rôle est un outil que l'orchestrateur appelle : un seul appel de modèle,
avec son propre prompt système et ses réglages, sur un message construit à
partir de ses arguments et du contexte qu'il déclare. Il ne reçoit rien
d'autre : ni l'historique, ni les outils de l'orchestrateur.

Contexte déclarable (#12) : ``user_input`` (la demande de l'utilisateur, mot
pour mot), ``caller_context`` (le contexte de l'appelant), ``tool_results``
(tous les résultats réussis des outils nommés, dans l'ordre des appels, en
entier même s'ils ont été déportés) et ``attachments`` (les pièces jointes du
run). Si un outil nommé n'a encore rien donné, le rôle n'est pas appelé :
l'orchestrateur reçoit une erreur qui lui dit quoi faire d'abord.

Rôle vision (C4) : un rôle qui déclare ``attachments`` reçoit les images
jointes à la demande, à la suite de son texte ; son modèle les voit s'il a la
capacité ``vision``. Sans pièce jointe dans le run, il est masqué à
l'orchestrateur.

Construction du message : avec un ``input_template``, le texte rendu ; sans,
un bloc balisé par contexte, dans l'ordre déclaré, puis les arguments. Les
pièces jointes suivent le texte, en références que l'appel résout.

Ce que l'orchestrateur voit : la description du rôle est complétée par la
liste du contexte qu'il reçoit déjà. Sans elle, l'orchestrateur ne le sait que
si son prompt le dit, et tend à transmettre en arguments ce que le rôle a déjà.

Arguments : sauf déclaration contraire dans ``input_schema``, un rôle refuse
ceux que son schéma ne prévoit pas (``additionalProperties: false``), comme un
outil Python. Un argument inventé par l'orchestrateur lui revient en erreur
au lieu d'être ignoré sans bruit.

Appel : ``model.retried`` et ``model.responded`` sont journalisés dans le span
de l'appel, avec son ``call_id``. Une erreur du modèle, après ses nouvelles
tentatives, ou une sortie vide deviennent un résultat d'erreur pour
l'orchestrateur (D5).
"""

import json
import logging
import time
from collections.abc import AsyncGenerator, Mapping
from contextlib import aclosing
from dataclasses import dataclass, field
from typing import Final, Literal

from pydantic import JsonValue

from loom_ia.core.model import (
    ContentBlock,
    Message,
    ModelRequest,
    ModelResponse,
    ModelSpec,
    TextBlock,
    ToolCallBlock,
    ToolOutput,
    ToolSpec,
)
from loom_ia.core.ports import ModelClient, ModelError, ToolContext
from loom_ia.core.template import Template
from loom_ia.engine.delegated import DelegatedPayload, DelegatedTool, RunView
from loom_ia.engine.media import describe
from loom_ia.engine.model_call import ModelCall, responded
from loom_ia.engine.refs import RefError

logger = logging.getLogger(__name__)

# Mention ajoutée à la description d'un rôle qui déclare du contexte.
CONTEXT_HINT: Final = "Reçoit déjà, inutile de les transmettre"


@dataclass(frozen=True, slots=True)
class ToolResults:
    """Contexte ``tool_results`` : résultats des outils nommés."""

    tools: tuple[str, ...]


type ContextItem = Literal["user_input", "caller_context", "attachments"] | ToolResults


def _empty_object() -> dict[str, JsonValue]:
    return {"type": "object", "properties": {}}


@dataclass(frozen=True, kw_only=True)
class RoleDefinition:
    """Un rôle tel que le moteur l'exécute : prompt lu, template analysé."""

    name: str
    description: str
    system: str = ""
    # Sans ``additionalProperties`` déclaré, les arguments non prévus sont refusés.
    input_schema: dict[str, JsonValue] = field(default_factory=_empty_object)
    template: Template | None = None
    context: tuple[ContextItem, ...] = ()
    terminal: bool = False
    # Délai de l'appel ; sans lui, ceux du modèle et son retry le bornent.
    timeout: float | None = None
    # Réglages propres au rôle (B6) : remplacent ceux du modèle.
    max_tokens: int | None = None
    params: Mapping[str, JsonValue] = field(default_factory=dict[str, JsonValue])


class RoleTool(DelegatedTool):
    """Outil qui délègue l'appel à un rôle."""

    def __init__(self, definition: RoleDefinition, model: ModelClient, model_spec: ModelSpec):
        self.definition = definition
        self.model = model
        self.model_spec = model_spec
        self._spec = ToolSpec(
            name=definition.name,
            description=described(definition),
            input_schema=strict_schema(definition.input_schema),
            kind="role",
            side_effects="none",
            timeout=definition.timeout,
            terminal=definition.terminal,
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def __repr__(self) -> str:
        return f"RoleTool({self.definition.name!r}, modèle {self.model_spec.id!r})"

    @property
    def wants_attachments(self) -> bool:
        return "attachments" in self.definition.context

    def available(self, run: RunView) -> bool:
        """Un rôle qui attend les pièces jointes est masqué quand le run n'en a pas (C4)."""
        return not self.wants_attachments or bool(run.state.attachments)

    async def check(self, arguments: dict[str, JsonValue], run: RunView) -> str | None:
        built = await self.message(arguments, run)
        return built if isinstance(built, str) else None

    async def message(self, arguments: dict[str, JsonValue], run: RunView) -> Message | str:
        """Message envoyé au rôle, ou motif de refus destiné à l'orchestrateur."""
        role = self.definition
        sections: list[str] = []
        values: dict[str, JsonValue] = {}
        results: dict[str, JsonValue] = {}
        files: list[ContentBlock] = []
        for item in role.context:
            match item:
                case "user_input":
                    text = _user_input(run)
                    values["user_input"] = text
                    sections.append(_tagged("user_input", text))
                case "caller_context":
                    data = run.state.context.model_dump(mode="json")
                    values["caller_context"] = data
                    sections.append(_tagged("caller_context", json.dumps(data, ensure_ascii=False)))
                case "attachments":
                    files += run.attachments
                    if not files:
                        return (
                            f"Le rôle {role.name} n'a aucune pièce jointe à regarder dans ce run."
                        )
                    listing = "\n".join(f"- {describe(block)}" for block in run.attachments)
                    values["attachments"] = listing
                    sections.append(_tagged("attachments", listing))
                case ToolResults(tools=names):
                    for name in names:
                        records = run.results.results_of(name)
                        if not records:
                            return (
                                f"Le rôle {role.name} a besoin d'un résultat de {name} : "
                                "appelle d'abord cet outil."
                            )
                        try:
                            texts = [await run.results.text(record) for record in records]
                        except RefError as exc:
                            return exc.message
                        results[name] = "\n\n".join(texts)
                        sections += [
                            _tagged("tool_result", text, tool=name, ref=record.ref)
                            for record, text in zip(records, texts, strict=True)
                        ]
        if results:
            values["tool_results"] = results
        if role.template is not None:
            text = role.template.render({"args": arguments, "context": values})
        else:
            if arguments:
                sections.append(_tagged("arguments", json.dumps(arguments, ensure_ascii=False)))
            text = "\n\n".join(sections)
        if not text.strip() and not files:
            return f"Le rôle {role.name} n'a rien reçu : donne-lui ses arguments."
        return Message(role="user", blocks=(TextBlock(text=text), *files))

    async def run(
        self, arguments: dict[str, JsonValue], context: ToolContext, run: RunView
    ) -> AsyncGenerator[DelegatedPayload | ToolOutput]:
        role, spec = self.definition, self.model_spec
        built = await self.message(arguments, run)
        if isinstance(built, str):
            yield ToolOutput.error(built)
            return
        request = ModelRequest(
            model_id=spec.model,
            system=role.system,
            messages=(built,),
            max_tokens=role.max_tokens or spec.max_tokens,
            params={**spec.params, **role.params},
        )
        started = time.perf_counter()
        attempts = 0
        response: ModelResponse | None = None
        try:
            call = ModelCall(self.model, spec, artifacts=run.artifacts)
            async with aclosing(call.run(request)) as outcomes:
                async for outcome in outcomes:
                    attempts += 1
                    if isinstance(outcome, ModelResponse):
                        response = outcome
                    else:
                        yield outcome.model_copy(update={"call_id": context.call_id})
        except ModelError as exc:
            # Erreur classée du fournisseur : son message suffit, sans pile d'appels.
            logger.warning(
                "Échec du rôle %s (modèle %s) : model.%s — %s",
                role.name,
                spec.id,
                exc.kind,
                exc.message,
                extra={"run_id": context.run_id},
            )
            yield ToolOutput.error(
                f"Le rôle {role.name} n'a pas pu répondre (model.{exc.kind}) : {exc.message}"
            )
            return
        if response is None:
            raise RuntimeError(f"Rôle {role.name} : appel de modèle terminé sans réponse")
        message = _without_tool_calls(response.message)
        yield responded(
            request,
            response,
            spec,
            attempts=attempts,
            latency_ms=(time.perf_counter() - started) * 1000,
            message=message,
            call_id=context.call_id,
        )
        if not message.text.strip():
            yield ToolOutput.error(
                f"Le rôle {role.name} n'a rien produit (arrêt : {response.stop_reason})."
            )
            return
        yield ToolOutput.text(message.text)


def described(definition: RoleDefinition) -> str:
    """Description vue par l'orchestrateur : celle du rôle, puis le contexte qu'il reçoit."""
    received: list[str] = []
    for item in definition.context:
        match item:
            case "user_input":
                received.append("la demande de l'utilisateur")
            case "caller_context":
                received.append("le contexte de l'appelant")
            case "attachments":
                received.append("les pièces jointes de la demande (images)")
            case ToolResults(tools=names):
                received.append(f"les résultats de {', '.join(names)}")
    if not received:
        return definition.description
    return f"{definition.description}\n\n{CONTEXT_HINT} : {' ; '.join(received)}."


def strict_schema(schema: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """Schéma d'entrée qui refuse les arguments non prévus, sauf choix contraire déclaré."""
    if "additionalProperties" in schema:
        return schema
    return {**schema, "additionalProperties": False}


def _user_input(run: RunView) -> str:
    """Demande de l'utilisateur qui a lancé le run, mot pour mot."""
    first = next((m for m in run.state.messages if m.role == "user"), None)
    return first.text if first is not None else ""


def _tagged(tag: str, body: str, **attributes: str) -> str:
    attrs = "".join(f' {name}="{value}"' for name, value in attributes.items())
    return f"<{tag}{attrs}>\n{body}\n</{tag}>"


def _without_tool_calls(message: Message) -> Message:
    """Un rôle n'a pas d'outils : d'éventuels appels sont écartés."""
    if not message.tool_calls:
        return message
    kept = tuple(b for b in message.blocks if not isinstance(b, ToolCallBlock))
    return message.model_copy(update={"blocks": kept or (TextBlock(text=""),)})
