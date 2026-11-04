# SPDX-License-Identifier: Apache-2.0
"""Ce que le modèle envoie à ``memory`` : l'action et ses arguments.

Le schéma montré au modèle ne garde que les actions permises et les
arguments qu'elles utilisent ; ``project`` en disparaît quand la config
l'impose. L'analyse refuse, avec un message qui dit quoi corriger, une
action non permise, un argument manquant ou un argument qui ne sert pas à
l'action.
"""

from collections.abc import Mapping
from typing import Annotated, Any, Final, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from loom_ia.core.ports import ToolError
from loom_ia.tools_builtin.memory_params import MemoryAction, MemoryParams

NAME: Final = "memory"
WRITING: Final = frozenset({"add_text", "add_url", "add_file"})

# Arguments de chaque action : obligatoires, puis facultatifs. ``project`` est
# ajouté (obligatoire pour une écriture, facultatif pour une lecture) quand la
# config ne l'impose pas.
_REQUIRED: Final[Mapping[MemoryAction, frozenset[str]]] = {
    "search": frozenset({"query"}),
    "get": frozenset({"doc_id"}),
    "list_docs": frozenset(),
    "projects": frozenset(),
    "add_text": frozenset({"text", "title"}),
    "add_url": frozenset({"url"}),
    "add_file": frozenset({"file"}),
    "update": frozenset({"doc_id", "text"}),
    "delete": frozenset({"doc_id"}),
}
_OPTIONAL: Final[Mapping[MemoryAction, frozenset[str]]] = {
    "search": frozenset({"k", "tags"}),
    "get": frozenset(),
    "list_docs": frozenset({"n"}),
    "projects": frozenset(),
    "add_text": frozenset({"tags"}),
    "add_url": frozenset({"tags"}),
    "add_file": frozenset({"tags"}),
    "update": frozenset(),
    "delete": frozenset(),
}
_FILTERS_BY_PROJECT: Final = frozenset({"search", "list_docs"})


class MemoryArguments(BaseModel):
    """Arguments d'un appel : ceux que voit le modèle."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action: Annotated[
        MemoryAction,
        Field(
            description="search : chercher ; get : lire un document entier ; list_docs : "
            "derniers documents ; projects : projets et nombre de documents ; add_text, "
            "add_url, add_file : mémoriser un texte, une page web, un fichier ; update : "
            "remplacer le texte d'un document ; delete : supprimer un document."
        ),
    ]
    query: Annotated[
        str | None, Field(description="Pour search : la question, ou des mots-clés.")
    ] = None
    k: Annotated[int, Field(ge=1, le=10, description="Pour search : nombre de résultats.")] = 5
    tags: Annotated[
        list[str] | None,
        Field(
            description="Pour search : ne garder que les documents qui portent tous ces tags ; "
            "pour un ajout : les tags du document."
        ),
    ] = None
    project: Annotated[
        str | None,
        Field(
            description="Projet du document : obligatoire pour un ajout ; pour search et "
            "list_docs, restreint au projet (absent : tous les projets)."
        ),
    ] = None
    doc_id: Annotated[
        str | None, Field(description="Pour get, update, delete : l'identifiant du document.")
    ] = None
    text: Annotated[
        str | None,
        Field(description="Pour add_text : le texte à mémoriser ; pour update : le texte entier."),
    ] = None
    title: Annotated[
        str | None, Field(description="Pour add_text : un titre court et explicite.")
    ] = None
    url: Annotated[str | None, Field(description="Pour add_url : l'adresse http(s).")] = None
    file: Annotated[
        str | None,
        Field(
            description="Pour add_file : « <dossier>/<chemin> » d'un fichier texte, Word ou PDF "
            "(les formats lus sont dans la description de l'outil)."
        ),
    ] = None
    n: Annotated[int, Field(ge=1, le=50, description="Pour list_docs : nombre de documents.")] = 20


def expected(action: MemoryAction, params: MemoryParams) -> tuple[frozenset[str], frozenset[str]]:
    """Arguments obligatoires et facultatifs de l'action, ``action`` exclu."""
    required = set(_REQUIRED[action])
    optional = set(_OPTIONAL[action])
    if params.project is None:
        if action in WRITING:
            required.add("project")
        elif action in _FILTERS_BY_PROJECT:
            optional.add("project")
    return frozenset(required), frozenset(optional)


def schema(params: MemoryParams) -> dict[str, JsonValue]:
    """Le schéma que voit le modèle : les actions permises et leurs seuls arguments."""
    declared = params.declared()
    found = MemoryArguments.model_json_schema()
    found.pop("title", None)
    used: set[str] = {"action"}
    for action in declared:
        required, optional = expected(action, params)
        used |= required | optional
    properties = cast(dict[str, dict[str, Any]], found["properties"])
    for name in list(properties):
        if name not in used:
            del properties[name]
        else:
            properties[name].pop("title", None)
    properties["action"]["enum"] = list(declared)
    properties["action"].pop("$ref", None)
    properties["action"]["type"] = "string"
    found.pop("$defs", None)
    found["required"] = ["action"]
    return cast(dict[str, JsonValue], found)


def parse(arguments: Mapping[str, JsonValue], params: MemoryParams) -> MemoryArguments:
    """Les arguments validés pour l'action demandée ; ``ToolError`` qui dit quoi corriger."""
    try:
        request = MemoryArguments.model_validate(dict(arguments))
    except ValidationError as exc:
        lines = ["Arguments invalides :"]
        for item in exc.errors(include_url=False):
            location = ".".join(str(part) for part in item["loc"]) or "(racine)"
            lines.append(f"- {location} : {item['msg']}")
        raise ToolError("\n".join(lines)) from None
    declared = params.declared()
    if request.action not in declared:
        raise ToolError(
            f"Action {request.action!r} non permise ; actions permises : {', '.join(declared)}."
        )
    required, optional = expected(request.action, params)
    given = {name for name in request.model_fields_set if name != "action"}
    missing = sorted(name for name in required if getattr(request, name) is None)
    if missing:
        raise ToolError(f"{request.action} attend : {', '.join(missing)}.")
    stray = sorted(given - required - optional)
    if stray:
        if "project" in stray and params.project is not None:
            raise ToolError(
                f"'project' n'est pas un argument : le projet est imposé ({params.project})."
            )
        raise ToolError(f"{', '.join(stray)} ne sert pas à {request.action}.")
    return request
