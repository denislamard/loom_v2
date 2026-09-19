# SPDX-License-Identifier: Apache-2.0
"""Définition d'un agent (A9, #50).

Sous-ensemble des jalons J1 et J2 : orchestrateur (``main``), outils Python,
serveurs MCP, rôles délégués (dont le rôle vision, qui reçoit les pièces
jointes) et sous-agents. Les guards, le juge, le budget et les politiques
arrivent avec leurs phases ; les déclarer aujourd'hui donne une erreur qui
nomme la phase.

``main`` est lui-même un rôle (C6) : il partage avec les rôles délégués le
modèle, le prompt système et les réglages ``llm``.
"""

from pathlib import Path
from typing import Annotated, Final, Literal, Self, cast

from jsonschema import Draft202012Validator, SchemaError
from jsonschema.validators import validator_for
from pydantic import (
    Discriminator,
    Field,
    JsonValue,
    PositiveFloat,
    PositiveInt,
    Tag,
    model_validator,
)

from loom_ia.core.model import (
    MAIN_ROLE,
    MCP_NAME_PATTERN,
    MCP_PREFIX_SEPARATOR,
    TOOL_NAME_PATTERN,
    Approval,
    DomainModel,
    SideEffects,
    ToolOverrides,
    UnsupportedKey,
    reject_later,
)
from loom_ia.core.template import Template, TemplateError

AGENT_NAME_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"

# Clés du schéma complet d'un agent, prévues pour plus tard (§17.4).
LATER_AGENT: Final[dict[str, str]] = {
    "approval": "J4.3 (approbations)",
    "policies": "J3.1 (politiques)",
    "output": "J3.2 (réponse structurée)",
    "judge": "J3.3 (juge)",
    "budget": "J3.4 (coûts et budgets)",
    "stream_output": "J3.2 (guards de sortie)",
    "timeout": "J4.2 (cycle de vie des runs : délai, annulation)",
}
LATER_MAIN: Final[dict[str, str]] = {
    "fallbacks": "J3.5 (modèle de secours)",
}
LATER_ROLE: Final[dict[str, str]] = {
    "output": "J3.2 (contrats de sortie)",
    "judge": "J3.3 (juge)",
    "fallbacks": "J3.5 (modèle de secours)",
}
LATER_SUBAGENT: Final[dict[str, str]] = {
    "budget_share": "J3.4 (coûts et budgets)",
}
LATER_CONTEXT: Final[dict[str, str]] = {
    "session_summary": "J4.1 (sessions)",
    "last_turns": "J4.1 (sessions)",
}


class Expose(DomainModel):
    """Points d'accès qui publient l'agent (N1 à N5)."""

    rest: bool = True
    mcp: bool = True


class LlmSettings(DomainModel):
    """Réglages d'appel propres à un rôle (B6)."""

    # Remplace ``max_tokens`` du modèle.
    max_tokens: PositiveInt | None = None
    # Fusionnés clé par clé sur ``params`` du modèle.
    params: dict[str, JsonValue] = Field(default_factory=dict)


class BaseRole(DomainModel):
    """Ce que ``main`` et les rôles délégués ont en commun (C6)."""

    # Identifiant d'un modèle déclaré dans ``models``.
    model: str = Field(min_length=1)
    system: str = ""
    # Chemin relatif à ``prompts_dir`` ; lu au chargement.
    system_file: Path | None = None
    llm: LlmSettings = LlmSettings()


class MainRole(BaseRole):
    """Rôle orchestrateur : le modèle qui mène le run."""

    @model_validator(mode="before")
    @classmethod
    def _check_input(cls, data: object) -> object:
        _reject_two_prompts(data)
        reject_later(data, LATER_MAIN)
        return data


class ToolResultsContext(DomainModel):
    """Contexte ``tool_results`` : résultats réussis des outils nommés, dans le run."""

    tool_results: tuple[str, ...] = Field(min_length=1)


type ContextName = Literal["user_input", "caller_context", "attachments"]
type ContextItem = ContextName | ToolResultsContext


def _empty_object() -> dict[str, JsonValue]:
    return {"type": "object", "properties": {}}


class RoleSpec(BaseRole):
    """Rôle délégué (C1) : l'orchestrateur l'appelle comme un outil."""

    name: str = Field(pattern=TOOL_NAME_PATTERN)
    description: str = Field(min_length=1)
    # Arguments que l'orchestrateur fournit ; objet vide par défaut.
    input_schema: dict[str, JsonValue] = Field(default_factory=_empty_object)
    # Construction du message (#12) ; sans template, blocs de contexte puis arguments.
    input_template: str | None = None
    # Contexte ajouté aux arguments, pris dans une liste fixe (#12).
    context: tuple[ContextItem, ...] = ()
    # Sortie transmise telle quelle comme réponse finale (C3, #13).
    terminal: bool = False
    # Délai de l'appel ; sans lui, ceux du modèle et son retry le bornent.
    timeout: PositiveFloat | None = None

    @model_validator(mode="before")
    @classmethod
    def _check_input(cls, data: object) -> object:
        _reject_two_prompts(data)
        reject_later(data, LATER_ROLE)
        _reject_later_context(data)
        return data

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.name == MAIN_ROLE:
            raise ValueError(f"Le nom {MAIN_ROLE!r} est réservé à l'orchestrateur")
        properties = _check_input_schema(self.input_schema)
        declared = self.declared_context
        doubles = sorted({name for name in declared if declared.count(name) > 1})
        if doubles:
            raise ValueError(f"Contexte déclaré deux fois : {', '.join(doubles)}")
        if not properties and not declared:
            raise ValueError("Rôle sans arguments ni contexte : il ne recevrait rien")
        if self.input_template is not None:
            _check_template(self.input_template, properties, declared)
        return self

    @property
    def declared_context(self) -> list[str]:
        """Noms du contexte déclaré : ``tool_results.<outil>`` pour chaque outil."""
        names: list[str] = []
        for item in self.context:
            if isinstance(item, ToolResultsContext):
                names += [f"tool_results.{tool}" for tool in item.tool_results]
            else:
                names.append(item)
        return names

    @property
    def wants_attachments(self) -> bool:
        """Vrai pour un rôle vision : il reçoit les pièces jointes du run (C4)."""
        return "attachments" in self.context

    @property
    def tool_results(self) -> tuple[str, ...]:
        """Outils dont le rôle reçoit les résultats."""
        return tuple(
            tool
            for item in self.context
            if isinstance(item, ToolResultsContext)
            for tool in item.tool_results
        )


def _reject_later_context(data: object) -> None:
    """Refuse un contexte prévu pour plus tard, sous ses deux formes (nom ou objet)."""
    if not isinstance(data, dict):
        return
    items = cast(dict[str, object], data).get("context")
    if not isinstance(items, list | tuple):
        return
    for item in cast(list[object], items):
        if isinstance(item, str) and item in LATER_CONTEXT:
            raise UnsupportedKey(item, LATER_CONTEXT[item])
        reject_later(item, LATER_CONTEXT)


def _check_input_schema(schema: dict[str, JsonValue]) -> set[str]:
    """Vérifie le schéma d'entrée et renvoie les noms de ses propriétés."""
    if schema.get("type") != "object":
        raise ValueError("input_schema : 'type: object' attendu")
    try:
        validator_for(schema, default=Draft202012Validator).check_schema(schema)
    except SchemaError as exc:
        raise ValueError(f"input_schema invalide : {exc.message}") from exc
    properties = schema.get("properties", {})
    return set(cast(dict[str, object], properties)) if isinstance(properties, dict) else set()


def _check_template(source: str, properties: set[str], declared: list[str]) -> None:
    """Variables connues, et tout le contexte déclaré utilisé (#50)."""
    try:
        template = Template.parse(source)
    except TemplateError as exc:
        raise ValueError(f"input_template : {exc}") from exc
    used: set[str] = set()
    for path in template.variables:
        shown = "{{ " + ".".join(path) + " }}"
        match path:
            case ("args",):
                pass
            case ("args", name, *_):
                if name not in properties:
                    raise ValueError(
                        f"input_template : {shown} — {name!r} absent de input_schema.properties"
                    )
            case ("context", "user_input" | "caller_context" | "attachments" as name, *rest):
                if name not in declared:
                    raise ValueError(f"input_template : {shown} — contexte {name!r} non déclaré")
                if rest and name != "caller_context":
                    raise ValueError(f"input_template : {shown} — {name} est un texte")
                used.add(name)
            case ("context", "tool_results", tool):
                if f"tool_results.{tool}" not in declared:
                    raise ValueError(f"input_template : {shown} — {tool!r} absent de tool_results")
                used.add(f"tool_results.{tool}")
            case _:
                raise ValueError(
                    f"input_template : variable inconnue {shown} (attendu args.<argument>, "
                    "context.user_input, context.caller_context, context.attachments "
                    "ou context.tool_results.<outil>)"
                )
    # Les pièces jointes partent en images après le texte : les citer est facultatif.
    unused = [name for name in declared if name not in used and name != "attachments"]
    if unused:
        raise ValueError(
            f"input_template : contexte déclaré mais non utilisé : {', '.join(unused)}"
        )


def _reject_two_prompts(data: object) -> None:
    if not isinstance(data, dict):
        return
    keys = cast(dict[object, object], data).keys()
    if {"system", "system_file"} <= keys:
        raise ValueError("'system' et 'system_file' ne peuvent pas être donnés ensemble")


class PythonTool(DomainModel):
    """Outil Python référencé par un nom enregistré ou un chemin ``module:attr``.

    Les champs renseignés ici remplacent ce que l'outil déclare lui-même.
    """

    python: str = Field(min_length=1)
    timeout: PositiveFloat | None = None
    side_effects: SideEffects | None = None
    approval: Approval | None = None
    idempotent: bool | None = None
    # Seuil de déport du résultat, en caractères (#16).
    offload_over: PositiveInt | None = None


class McpTools(DomainModel):
    """Serveur MCP référencé par un agent (#19).

    Les outils prennent le préfixe ``serveur__`` (ou ``alias__``) ; ``include``
    ou ``exclude`` choisit ceux qui sont exposés, ``tools`` fixe leurs
    déclarations pour cet agent.
    """

    mcp: str = Field(pattern=MCP_NAME_PATTERN)
    # Préfixe plus court que le nom du serveur.
    alias: str | None = Field(default=None, pattern=MCP_NAME_PATTERN, max_length=40)
    include: tuple[str, ...] | None = None
    exclude: tuple[str, ...] | None = None
    # Sans ce serveur, le run échoue au lieu de continuer sans ses outils.
    required: bool = False
    tools: dict[str, ToolOverrides] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_filters(self) -> Self:
        if self.include is not None and self.exclude is not None:
            raise ValueError(f"Serveur MCP {self.mcp!r} : 'include' ou 'exclude', pas les deux")
        return self

    @property
    def prefix(self) -> str:
        return self.alias or self.mcp

    def owns(self, tool: str) -> bool:
        """Vrai si ``tool`` porte le préfixe de cette référence."""
        return tool.startswith(f"{self.prefix}{MCP_PREFIX_SEPARATOR}")


class SubAgentRef(DomainModel):
    """Sous-agent (C5) : un autre agent de la config, appelé comme un outil.

    L'orchestrateur lui passe un seul argument, ``message``. Nom et
    description de l'outil : ceux de l'agent, sauf s'ils sont donnés ici.
    """

    agent: str = Field(pattern=AGENT_NAME_PATTERN)
    # Nom de l'outil vu par l'orchestrateur ; par défaut, celui de l'agent.
    name: str | None = Field(default=None, pattern=TOOL_NAME_PATTERN)
    description: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _later(cls, data: object) -> object:
        reject_later(data, LATER_SUBAGENT)
        return data

    @property
    def tool_name(self) -> str:
        return self.name or self.agent


def _tool_kind(value: object) -> str:
    if isinstance(value, McpTools):
        return "mcp"
    if isinstance(value, dict) and "mcp" in value:
        return "mcp"
    return "python"


type ToolRef = Annotated[
    Annotated[PythonTool, Tag("python")] | Annotated[McpTools, Tag("mcp")],
    Discriminator(_tool_kind),
]


class AgentSpec(DomainModel):
    name: str = Field(pattern=AGENT_NAME_PATTERN)
    description: str = ""
    expose: Expose = Expose()
    main: MainRole
    max_iterations: PositiveInt = 10
    # Outils Python et serveurs MCP, dans l'ordre de déclaration.
    tools: tuple[ToolRef, ...] = ()
    roles: tuple[RoleSpec, ...] = ()
    subagents: tuple[SubAgentRef, ...] = ()
    # Profondeur maximale d'un run de cet agent pour appeler ses sous-agents :
    # à 1, un run racine les appelle, mais un sous-run de cet agent ne le peut pas.
    max_depth: PositiveInt = 1

    @model_validator(mode="before")
    @classmethod
    def _later(cls, data: object) -> object:
        reject_later(data, LATER_AGENT)
        return data

    @model_validator(mode="after")
    def _unique_tools(self) -> Self:
        names = [tool.python for tool in self.python_tools]
        doubles = {name for name in names if names.count(name) > 1}
        if doubles:
            raise ValueError(f"Outil déclaré deux fois : {', '.join(sorted(doubles))}")
        prefixes = [ref.prefix for ref in self.mcp_tools]
        doubles = {name for name in prefixes if prefixes.count(name) > 1}
        if doubles:
            raise ValueError(
                f"Préfixe MCP déclaré deux fois : {', '.join(sorted(doubles))} "
                "(donner un 'alias' à l'une des références)"
            )
        roles = [role.name for role in self.roles]
        doubles = {name for name in roles if roles.count(name) > 1}
        if doubles:
            raise ValueError(f"Rôle déclaré deux fois : {', '.join(sorted(doubles))}")
        subagents = [ref.tool_name for ref in self.subagents]
        doubles = {name for name in subagents if subagents.count(name) > 1}
        if doubles:
            raise ValueError(
                f"Sous-agent déclaré deux fois : {', '.join(sorted(doubles))} "
                "(donner un 'name' à l'une des références)"
            )
        return self

    @property
    def python_tools(self) -> tuple[PythonTool, ...]:
        return tuple(tool for tool in self.tools if isinstance(tool, PythonTool))

    @property
    def mcp_tools(self) -> tuple[McpTools, ...]:
        return tuple(tool for tool in self.tools if isinstance(tool, McpTools))
