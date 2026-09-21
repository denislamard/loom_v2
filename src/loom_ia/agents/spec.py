# SPDX-License-Identifier: Apache-2.0
"""Définition d'un agent (A9, #50).

Sous-ensemble des jalons J1 à J3 : orchestrateur (``main``), outils Python,
serveurs MCP, rôles délégués (dont le rôle vision, qui reçoit les pièces
jointes), sous-agents, politiques (J3.1), contrats de sortie (J3.2), juges
(J3.3) et budgets (J3.4). Ce qui arrive plus tard (approbations, délai d'un
run, secours) donne une erreur qui nomme sa phase.

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
    CRITERION_NAME_PATTERN,
    MAIN_ROLE,
    MCP_NAME_PATTERN,
    MCP_PREFIX_SEPARATOR,
    POLICY_NAME_PATTERN,
    RESERVED_PREFIX,
    TOOL_NAME_PATTERN,
    Approval,
    Budgets,
    Criterion,
    DomainModel,
    HookPoint,
    JudgeWhen,
    OnFailure,
    OutputContract,
    RepairSettings,
    SideEffects,
    StreamOutput,
    ToolOverrides,
    UnsupportedKey,
    reject_later,
)
from loom_ia.core.template import Template, TemplateError

AGENT_NAME_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"

# Clés du schéma complet d'un agent, prévues pour plus tard (§17.4).
LATER_AGENT: Final[dict[str, str]] = {
    "approval": "J4.3 (approbations)",
    "timeout": "J4.2 (cycle de vie des runs : délai, annulation)",
}
LATER_MAIN: Final[dict[str, str]] = {}
LATER_ROLE: Final[dict[str, str]] = {}
LATER_SUBAGENT: Final[dict[str, str]] = {}
LATER_CONTEXT: Final[dict[str, str]] = {}


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


def _check_chain(model: str, fallbacks: tuple[str, ...]) -> None:
    """Chaîne de secours sans doublon, sans le modèle lui-même (B4)."""
    chain = [model, *fallbacks]
    doubles = sorted({name for name in chain if chain.count(name) > 1})
    if doubles:
        raise ValueError(f"Modèle en double dans la chaîne de secours : {', '.join(doubles)}")


class BaseRole(DomainModel):
    """Ce que ``main`` et les rôles délégués ont en commun (C6)."""

    # Identifiant d'un modèle déclaré dans ``models``.
    model: str = Field(min_length=1)
    # Modèles de secours, dans l'ordre (B4, #10) : identifiants déclarés dans ``models``.
    fallbacks: tuple[str, ...] = ()
    system: str = ""
    # Chemin relatif à ``prompts_dir`` ; lu au chargement.
    system_file: Path | None = None
    llm: LlmSettings = LlmSettings()

    @model_validator(mode="after")
    def _check_fallbacks(self) -> Self:
        _check_chain(self.model, self.fallbacks)
        return self

    @property
    def chain(self) -> tuple[str, ...]:
        """Modèle, puis ses secours."""
        return (self.model, *self.fallbacks)


class MainRole(BaseRole):
    """Rôle orchestrateur : le modèle qui mène le run."""

    @model_validator(mode="before")
    @classmethod
    def _check_input(cls, data: object) -> object:
        _reject_two_prompts(data)
        reject_later(data, LATER_MAIN)
        return data


type ContextScope = Literal["run", "session"]


class ToolResultsContext(DomainModel):
    """Contexte ``tool_results`` : résultats réussis des outils nommés."""

    tool_results: tuple[str, ...] = Field(min_length=1)
    # ``run`` : seulement ce que le run a obtenu. ``session`` : à défaut, le
    # dernier résultat connu de la session — qui peut dater d'un tour ancien.
    scope: ContextScope = "run"


class LastTurnsContext(DomainModel):
    """Contexte ``last_turns`` : derniers tours de la session (un tour = un run)."""

    last_turns: PositiveInt


type ContextName = Literal["user_input", "caller_context", "attachments", "session_summary"]
type ContextItem = ContextName | ToolResultsContext | LastTurnsContext

# Nom d'un juge pour la réponse finale, quand il n'en déclare pas.
OUTPUT_JUDGE: Final = "output"


class JudgeSpec(DomainModel):
    """Juge d'une sortie (E3, E6, #21) : la réponse finale (agent) ou la sortie d'un rôle.

    Chaque critère reçoit une note entre 0 et 1 ; un critère bloquant sous son
    seuil fait refuser la sortie, que son auteur répare, puis ``on_failure``
    décide. Le juge voit la sortie, ses critères, le contexte déclaré (liste
    fixe du #12) et, pour un rôle, ses arguments.
    """

    # Identifiant d'un modèle déclaré dans ``models``.
    model: str = Field(min_length=1)
    # Modèles de secours, dans l'ordre (B4, #10).
    fallbacks: tuple[str, ...] = ()
    # Nom dans le journal (tirage, rôle ``judge:<nom>``) ; par défaut ``output``
    # pour la réponse finale, le nom du rôle pour un rôle.
    name: str | None = Field(default=None, pattern=CRITERION_NAME_PATTERN)
    criteria: tuple[Criterion, ...] = Field(min_length=1)
    context: tuple[ContextItem, ...] = ()
    when: JudgeWhen = JudgeWhen()
    repair: RepairSettings = RepairSettings()
    on_failure: OnFailure = "fail"
    fallback_message: str | None = None
    llm: LlmSettings = LlmSettings()
    # Délai du juge ; sans lui, ceux de son modèle et son retry le bornent.
    timeout: PositiveFloat | None = None
    # Erreur du juge (modèle, verdict) : ``block`` fait échouer le run, ``allow`` laisse passer.
    on_error: Literal["block", "allow"] = "block"

    @model_validator(mode="before")
    @classmethod
    def _check_input(cls, data: object) -> object:
        _reject_later_context(data)
        return data

    @model_validator(mode="after")
    def _check(self) -> Self:
        _check_chain(self.model, self.fallbacks)
        names = [criterion.name for criterion in self.criteria]
        doubles = sorted({name for name in names if names.count(name) > 1})
        if doubles:
            raise ValueError(f"Critère déclaré deux fois : {', '.join(doubles)}")
        declared = _declared_context(self.context)
        doubles = sorted({name for name in declared if declared.count(name) > 1})
        if doubles:
            raise ValueError(f"Contexte déclaré deux fois : {', '.join(doubles)}")
        if self.on_failure == "fallback" and not self.fallback_message:
            raise ValueError("on_failure: fallback demande un 'fallback_message'")
        return self

    @property
    def blocking(self) -> bool:
        """Vrai si un critère au moins peut faire refuser la sortie."""
        return any(criterion.blocking for criterion in self.criteria)

    @property
    def wants_attachments(self) -> bool:
        return "attachments" in self.context

    @property
    def chain(self) -> tuple[str, ...]:
        """Modèle, puis ses secours."""
        return (self.model, *self.fallbacks)


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
    # Contrat de sortie (E5) : réparé par le modèle du rôle, puis ``on_failure``.
    output: OutputContract | None = None
    # Juge de la sortie (E5, #21), après le contrat : réparée par le modèle du rôle.
    judge: JudgeSpec | None = None

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
        return _declared_context(self.context)

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


def _declared_context(context: tuple[ContextItem, ...]) -> list[str]:
    """Noms d'un contexte déclaré : ``tool_results.<outil>`` pour chaque outil."""
    names: list[str] = []
    for item in context:
        if isinstance(item, ToolResultsContext):
            names += [f"tool_results.{tool}" for tool in item.tool_results]
        elif isinstance(item, LastTurnsContext):
            names.append("last_turns")
        else:
            names.append(item)
    return names


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
            case (
                "context",
                "user_input"
                | "caller_context"
                | "attachments"
                | "session_summary"
                | "last_turns" as name,
                *rest,
            ):
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
                    "context.user_input, context.caller_context, context.attachments, "
                    "context.session_summary, context.last_turns "
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
    # Contrat de sortie (E5) : sans réparation, un résultat non conforme revient
    # à l'orchestrateur en erreur.
    output: OutputContract | None = None


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
    # Part de ce qui reste au budget du run appelant au moment de l'appel (J4).
    budget_share: float | None = Field(default=None, gt=0.0, le=1.0)

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


class PolicyRef(DomainModel):
    """Politique branchée sur l'agent (#1, #2, §17.4).

    ``hook`` : un nom enregistré (module de ``imports``), un chemin
    ``module:attr``, ou une politique fournie (``loom.require_tool``).
    ``points`` : ceux où l'agent la branche, parmi ceux qu'elle déclare ; par
    défaut, tous. Les politiques d'un point s'exécutent dans l'ordre déclaré.
    """

    hook: str = Field(min_length=1)
    # Nom dans le journal ; par défaut celui de la politique.
    name: str | None = Field(default=None, pattern=POLICY_NAME_PATTERN)
    points: tuple[HookPoint, ...] | None = Field(default=None, min_length=1)
    params: dict[str, JsonValue] = Field(default_factory=dict)
    # Délai de la politique ; ``null`` le retire.
    timeout: PositiveFloat | None = 5.0
    # Erreur de la politique : ``block`` fait échouer le run, ``allow`` l'ignore.
    on_error: Literal["block", "allow"] = "block"
    # Réparations (``Retry``) que la politique peut demander dans un run.
    max_attempts: PositiveInt = 1

    @model_validator(mode="after")
    def _check_name(self) -> Self:
        if self.name is not None and self.name.startswith(RESERVED_PREFIX):
            raise ValueError(
                f"Politique {self.name!r} : le préfixe {RESERVED_PREFIX!r} est réservé à loom-ia"
            )
        return self


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
    # Contrat de la réponse finale (A7, E1) : réparée par l'orchestrateur.
    output: OutputContract | None = None
    # Juge de la réponse finale (E3, #21), après son contrat.
    judge: JudgeSpec | None = None
    # Budgets de l'agent (J4) : surchargent ceux de la racine, clé par clé.
    budget: Budgets | None = None
    # Diffusion de la réponse finale ; par défaut ``after_guards`` si elle est
    # contrôlée (contrat, juge, politique on_output, rôle terminal sous contrat
    # ou jugé), sinon ``live`` (#11).
    stream_output: StreamOutput | None = None
    # Outils Python et serveurs MCP, dans l'ordre de déclaration.
    tools: tuple[ToolRef, ...] = ()
    roles: tuple[RoleSpec, ...] = ()
    subagents: tuple[SubAgentRef, ...] = ()
    # Politiques, dans l'ordre d'exécution à chaque point (#2).
    policies: tuple[PolicyRef, ...] = ()
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
        judges = [name for name, _, _ in self.judges]
        doubles = {name for name in judges if judges.count(name) > 1}
        if doubles:
            raise ValueError(
                f"Juge déclaré deux fois : {', '.join(sorted(doubles))} "
                "(donner un 'name' à l'un des juges)"
            )
        return self

    @property
    def judges(self) -> tuple[tuple[str, RoleSpec | None, JudgeSpec], ...]:
        """Juges de l'agent : nom, rôle jugé (None pour la réponse finale), définition."""
        found: list[tuple[str, RoleSpec | None, JudgeSpec]] = []
        if self.judge is not None:
            found.append((self.judge.name or OUTPUT_JUDGE, None, self.judge))
        found += [
            (role.judge.name or role.name, role, role.judge)
            for role in self.roles
            if role.judge is not None
        ]
        return tuple(found)

    @property
    def contracts(self) -> bool:
        """Vrai si l'agent déclare un contrat de sortie (réponse, rôle ou outil)."""
        return (
            self.output is not None
            or any(role.output is not None for role in self.roles)
            or any(tool.output is not None for tool in self.python_tools)
            or any(o.output is not None for ref in self.mcp_tools for o in ref.tools.values())
        )

    @property
    def python_tools(self) -> tuple[PythonTool, ...]:
        return tuple(tool for tool in self.tools if isinstance(tool, PythonTool))

    @property
    def mcp_tools(self) -> tuple[McpTools, ...]:
        return tuple(tool for tool in self.tools if isinstance(tool, McpTools))
