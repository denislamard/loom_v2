# SPDX-License-Identifier: Apache-2.0
"""Outils écrits en Python (D1).

Le schéma d'entrée est déduit de la signature ; la description vient de la
docstring. Un paramètre annoté ``ToolContext`` reçoit le contexte de l'appel
et n'apparaît pas dans le schéma.

    @tool
    def calculer(expr: str) -> str:
        '''Évalue une expression arithmétique.'''

    @tool(side_effects="irreversible", timeout=10)
    async def envoyer_email(to: str, body: str, ctx: ToolContext) -> None: ...

Conversion du résultat (#15) : ``str`` donne un bloc texte ; ``None`` un
résultat vide ; un ``ToolOutput`` est repris tel quel ; une ``Image`` est
rangée dans le stockage d'artefacts par le moteur et le modèle en reçoit la
référence ; toute autre valeur sérialisable en JSON (``dict``, ``list``,
nombre, modèle Pydantic…) donne un bloc JSON, recopié dans ``data``.

    @tool
    def graphique(serie: list[float]) -> Image:
        '''Trace la série.'''
        return Image(tracer_png(serie))

Une fonction synchrone s'exécute dans un thread : un timeout rend la main au
moteur, mais ne peut pas interrompre le thread.
"""

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Self, cast, get_type_hints, overload

from pydantic import BaseModel, ConfigDict, JsonValue, TypeAdapter, ValidationError, create_model

from loom_ia.core.model import (
    Approval,
    InlineDataBlock,
    JsonBlock,
    SideEffects,
    ToolKind,
    ToolOutput,
    ToolSpec,
    sniff,
)
from loom_ia.core.ports import ToolContext, ToolError

_JSON: TypeAdapter[Any] = TypeAdapter(Any)


@dataclass(frozen=True, slots=True)
class Image:
    """Image renvoyée par un outil : rangée comme artefact, référencée dans le résultat (G3).

    Le type se déduit de la signature binaire (JPEG, PNG, GIF, WebP) ;
    ``media_type`` ne sert que si elle n'est pas reconnue.
    """

    data: bytes
    media_type: str | None = None
    name: str | None = None

    @classmethod
    def from_path(cls, path: str | Path) -> Self:
        file = Path(path)
        return cls(data=file.read_bytes(), name=file.name)

    def block(self) -> InlineDataBlock:
        media_type = sniff(self.data) or self.media_type
        if media_type is None:
            raise ValueError(
                f"Image {self.name or ''} : format non reconnu (JPEG, PNG, GIF ou WebP attendu)"
            )
        return InlineDataBlock(media_type=media_type, data=self.data, name=self.name)


class FunctionTool[**P, R]:
    """Outil construit à partir d'une fonction ; reste appelable comme elle."""

    kind: ToolKind = "python"

    def __init__(
        self,
        fn: Callable[P, R],
        *,
        name: str | None = None,
        description: str | None = None,
        side_effects: SideEffects = "none",
        approval: Approval = "never",
        idempotent: bool = False,
        timeout: float | None = None,
        offload_over: int | None = None,
    ) -> None:
        self.fn = fn
        self._is_async = inspect.iscoroutinefunction(fn)
        tool_name = name or fn.__name__
        text = description if description is not None else inspect.getdoc(fn)
        if not text:
            raise ValueError(f"Outil {tool_name!r} : description manquante (docstring vide)")
        self._context_param, self._arguments = _arguments_model(fn, tool_name)
        schema = self._arguments.model_json_schema()
        schema.pop("title", None)
        self._spec = ToolSpec(
            name=tool_name,
            description=text,
            input_schema=cast(dict[str, JsonValue], schema),
            kind=self.kind,
            side_effects=side_effects,
            approval=approval,
            idempotent=idempotent,
            timeout=timeout,
            offload_over=offload_over,
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> R:
        return self.fn(*args, **kwargs)

    async def invoke(self, arguments: dict[str, JsonValue], context: ToolContext) -> ToolOutput:
        try:
            parsed = self._arguments.model_validate(arguments)
        except ValidationError as exc:
            raise ToolError(_describe(exc)) from exc
        kwargs: dict[str, Any] = {
            field: getattr(parsed, field) for field in type(parsed).model_fields
        }
        if self._context_param is not None:
            kwargs[self._context_param] = context
        fn = cast(Callable[..., Any], self.fn)
        if self._is_async:
            result = await cast(Awaitable[object], fn(**kwargs))
        else:
            result = await asyncio.to_thread(fn, **kwargs)
        return to_output(result)

    def __repr__(self) -> str:
        return f"FunctionTool({self._spec.name!r})"


def to_output(result: object) -> ToolOutput:
    """Traduit la valeur renvoyée par une fonction en ``ToolOutput``."""
    match result:
        case ToolOutput():
            return result
        case Image():
            return ToolOutput(blocks=(result.block(),))
        case str():
            return ToolOutput.text(result)
        case None:
            return ToolOutput()
        case _:
            data = cast(JsonValue, _JSON.dump_python(result, mode="json"))
            return ToolOutput(blocks=(JsonBlock(data=data),), data=data)


@overload
def tool[**P, R](fn: Callable[P, R], /) -> FunctionTool[P, R]: ...


@overload
def tool[**P, R](
    *,
    name: str | None = None,
    description: str | None = None,
    side_effects: SideEffects = "none",
    approval: Approval = "never",
    idempotent: bool = False,
    timeout: float | None = None,
    offload_over: int | None = None,
) -> Callable[[Callable[P, R]], FunctionTool[P, R]]: ...


def tool[**P, R](
    fn: Callable[P, R] | None = None,
    /,
    *,
    name: str | None = None,
    description: str | None = None,
    side_effects: SideEffects = "none",
    approval: Approval = "never",
    idempotent: bool = False,
    timeout: float | None = None,
    offload_over: int | None = None,
) -> FunctionTool[P, R] | Callable[[Callable[P, R]], FunctionTool[P, R]]:
    """Déclare une fonction comme outil, avec ou sans options."""

    def wrap(target: Callable[P, R]) -> FunctionTool[P, R]:
        return FunctionTool(
            target,
            name=name,
            description=description,
            side_effects=side_effects,
            approval=approval,
            idempotent=idempotent,
            timeout=timeout,
            offload_over=offload_over,
        )

    return wrap if fn is None else wrap(fn)


# --- Interne -----------------------------------------------------------------


def _arguments_model(fn: Callable[..., object], name: str) -> tuple[str | None, type[BaseModel]]:
    """Modèle Pydantic des arguments, et nom du paramètre de contexte éventuel."""
    hints = get_type_hints(fn, include_extras=True)
    context_param: str | None = None
    fields: dict[str, Any] = {}
    for param in inspect.signature(fn).parameters.values():
        annotation = hints.get(param.name, Any)
        if annotation is ToolContext:
            context_param = param.name
            continue
        if param.kind not in (param.POSITIONAL_OR_KEYWORD, param.KEYWORD_ONLY):
            raise TypeError(
                f"Outil {name!r} : paramètre {param.name!r} non pris en charge "
                "(seuls les paramètres nommés le sont)"
            )
        default = ... if param.default is param.empty else param.default
        fields[param.name] = (annotation, default)
    model = create_model(
        f"{name}_arguments",
        __config__=ConfigDict(extra="forbid"),
        **fields,
    )
    return context_param, model


def _describe(error: ValidationError) -> str:
    """Message d'erreur de validation lisible par le modèle."""
    lines = ["Arguments invalides :"]
    for item in error.errors(include_url=False):
        location = ".".join(str(part) for part in item["loc"]) or "(racine)"
        lines.append(f"- {location} : {item['msg']}")
    return "\n".join(lines)
