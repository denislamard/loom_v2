# SPDX-License-Identifier: Apache-2.0
"""Définition d'un agent (fonction A9, point 50).

Sous-ensemble des jalons J1 à J3 : orchestrateur (``main``), outils Python,
serveurs MCP, rôles délégués (dont le rôle vision, qui reçoit les pièces
jointes), sous-agents, politiques (jalon J3.1), contrats de sortie (jalon
J3.2), juges (jalon J3.3) et budgets (jalon J3.4). Ce qui arrive plus tard (approbations, délai d'un
run, secours) donne une erreur qui nomme sa phase.

``main`` est lui-même un rôle (fonction C6) : il partage avec les rôles délégués le
modèle, le prompt système et les réglages ``llm``.

State de la session (option C) : ``state`` déclare les clés que la session
peut porter, chacune avec son schéma, et les outils dont un résultat la pose
(``from``) ; ``main.context`` dit ce que l'orchestrateur en reçoit, et le
``context`` d'un rôle ce que ce rôle en reçoit (point 50 : déclaré, il est cité par
son ``input_template``). Un juge ne reçoit pas le state dans cette version
(décision D11 du state de session).

Un agent se déclare dans un fichier ``agents/<nom>.yaml`` (``AgentSpec``) ;
``loom schema`` donne le schéma JSON de ces fichiers.
"""

import operator
import re
from collections.abc import Callable
from functools import reduce
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Final, Literal, Self, cast

from jsonschema import Draft202012Validator, SchemaError, ValidationError
from jsonschema.protocols import Validator
from jsonschema.validators import validator_for
from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    JsonValue,
    PositiveFloat,
    PositiveInt,
    Tag,
    create_model,
    model_validator,
)
from pydantic import ValidationError as ModelValidationError

from loom_ia.core.bounded_regex import RegexTimeout, bounded_validator, check_schema
from loom_ia.core.model import (
    CRITERION_NAME_PATTERN,
    MAIN_ROLE,
    MCP_NAME_PATTERN,
    MCP_PREFIX_SEPARATOR,
    POLICY_NAME_PATTERN,
    RESERVED_PREFIX,
    TOOL_NAME_PATTERN,
    Approval,
    ApprovalSettings,
    Approvers,
    Budgets,
    Criterion,
    DomainModel,
    HookPoint,
    JudgeWhen,
    OnFailure,
    OutputContract,
    RepairSettings,
    SideEffects,
    SkillText,
    StreamOutput,
    ToolOverrides,
    exclusive_skill,
    finite_json,
)
from loom_ia.core.template import Template, TemplateError
from loom_ia.tools_builtin import BUILTIN_TOOLS, Builtin, is_builtin, parameterized_builtins

AGENT_NAME_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"
"""Nom d'un agent : lettres, chiffres, ``_`` et ``-``, 64 au plus."""


class Expose(DomainModel):
    """Points d'accès qui publient l'agent (fonctions N1 à N5).

    L'API Python lance tout agent ; ``expose`` ne règle que REST et MCP.

    Attributes:
        rest: Publié par le serveur HTTP (``/v1/agents/<nom>/…``). Défaut :
            vrai.
        mcp: Publié comme outil par le serveur MCP. Défaut : vrai.
    """

    rest: bool = True
    mcp: bool = True


class LlmSettings(DomainModel):
    """Réglages d'appel propres à un rôle (``llm`` ; fonction B6).

    Attributes:
        max_tokens: Remplace ``max_tokens`` du modèle.
        params: Paramètres du fournisseur (température, raisonnement…),
            fusionnés clé par clé sur ``params`` du modèle.
    """

    max_tokens: PositiveInt | None = None
    params: dict[str, JsonValue] = Field(default_factory=dict)


def _check_chain(model: str, fallbacks: tuple[str, ...]) -> None:
    """Chaîne de secours sans doublon, sans le modèle lui-même (fonction B4).

    Raises:
        ValueError: Un modèle cité deux fois dans la chaîne.
    """
    chain = [model, *fallbacks]
    doubles = sorted({name for name in chain if chain.count(name) > 1})
    if doubles:
        raise ValueError(f"Modèle en double dans la chaîne de secours : {', '.join(doubles)}")


class BaseRole(DomainModel):
    """Ce que ``main`` et les rôles délégués ont en commun (fonction C6).

    ``system`` et ``system_file`` s'excluent.

    Attributes:
        model: Identifiant d'un modèle déclaré dans ``models``.
        fallbacks: Modèles de secours, dans l'ordre (fonction B4, point 10) :
            identifiants déclarés dans ``models``, sans doublon ni le modèle
            lui-même.
        system: Prompt système, en ligne ; peut citer des variables
            ``{{ … }}``.
        system_file: Prompt système dans un fichier, relatif à
            ``resources_dir`` ; lu au chargement.
        llm: Réglages d'appel du rôle.
    """

    model: str = Field(min_length=1)
    fallbacks: tuple[str, ...] = ()
    system: str = ""
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


def system_text(role: BaseRole) -> str:
    """Prompt système d'un rôle : texte en ligne, ou fichier relu à chaque montage."""
    if role.system_file is None:
        return role.system
    return role.system_file.read_text(encoding="utf-8")


class SessionStateContext(DomainModel):
    """Contexte ``session_state`` limité à certaines clés du state de la session.

    S'écrit ``- session_state: [client, devis]`` dans ``context`` ; ``- session_state`` seul donne tout le state.

    Attributes:
        session_state: Les clés reçues, déclarées dans ``state`` ; une au
            moins.
    """

    session_state: tuple[str, ...] = Field(min_length=1)


type MainContextItem = Literal["session_state"] | SessionStateContext
"""Ce que l'orchestrateur reçoit en plus de ses messages : le state de la session, entier (``session_state``) ou certaines de ses clés."""

SESSION_STATE: Final = "session_state"
"""Nom du contexte qui donne le state de la session."""


class MainRole(BaseRole):
    """Rôle orchestrateur : le modèle qui mène le run.

    Attributes:
        context: Contexte ajouté à chaque appel de l'orchestrateur, à la fin de
            sa requête (décision D4 du state de session) : seulement
            ``session_state``, entier ou par clés.
    """

    context: tuple[MainContextItem, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def _check_input(cls, data: object) -> object:
        _reject_two_prompts(data)
        _only_session_state(data)
        return data

    @model_validator(mode="after")
    def _check_context(self) -> Self:
        if len(self.context) > 1:
            raise ValueError("main.context : session_state déclaré deux fois")
        keys = list(self.state_keys or ())
        doubles = sorted({key for key in keys if keys.count(key) > 1})
        if doubles:
            raise ValueError(f"main.context : clé citée deux fois : {', '.join(doubles)}")
        return self

    @property
    def state_keys(self) -> tuple[str, ...] | None:
        """Clés nommées dans ``main.context`` ; () pour tout le state, None sans contexte."""
        for item in self.context:
            return item.session_state if isinstance(item, SessionStateContext) else ()
        return None


type ContextScope = Literal["run", "session"]
"""Où chercher les résultats de ``tool_results`` : le run seul, ou aussi la session."""


class ToolResultsContext(DomainModel):
    """Contexte ``tool_results`` : résultats réussis des outils nommés.

    S'écrit ``- tool_results: [crm_lookup]``, avec ``scope`` en option.

    Attributes:
        tool_results: Les outils dont les résultats sont reçus ; un au moins.
        scope: ``run`` (défaut) : seulement ce que le run a obtenu. ``session``
            : à défaut, le dernier résultat connu de la session — qui peut
            dater d'un tour ancien.
    """

    tool_results: tuple[str, ...] = Field(min_length=1)
    scope: ContextScope = "run"


class LastTurnsContext(DomainModel):
    """Contexte ``last_turns`` : derniers tours de la session (un tour = un run).

    S'écrit ``- last_turns: 3`` ; les tours sont rendus en texte.

    Attributes:
        last_turns: Nombre de tours reçus.
    """

    last_turns: PositiveInt


type ContextName = Literal["user_input", "caller_context", "attachments", "session_summary"]
"""Contextes simples, nommés par une chaîne."""
type ContextItem = ContextName | ToolResultsContext | LastTurnsContext
"""Contexte d'un juge."""
type RoleContextItem = (
    Literal["user_input", "caller_context", "attachments", "session_summary", "session_state"] | ToolResultsContext | LastTurnsContext | SessionStateContext
)
"""Contexte d'un rôle : celui d'un juge, et le state de la session.

Entier (``session_state``) ou certaines de ses clés. À plat, pour que les
erreurs de la config gardent des chemins courts.
"""

OUTPUT_JUDGE: Final = "output"
"""Nom d'un juge pour la réponse finale, quand il n'en déclare pas."""


class JudgeSpec(DomainModel):
    """Juge d'une sortie (fonctions E3 et E6, point 21) : la réponse finale (agent) ou la sortie d'un rôle.

    Chaque critère reçoit une note entre 0 et 1 ; un critère bloquant sous son
    seuil fait refuser la sortie, que son auteur répare, puis ``on_failure``
    décide. Le juge voit la sortie, ses critères, le contexte déclaré (liste
    fixe du point 12) et, pour un rôle, ses arguments.

    Un juge n'est pas permis sur un outil ni sur la référence d'un sous-agent.

    Attributes:
        model: Identifiant d'un modèle déclaré dans ``models`` ; s'il est celui
            qu'il évalue, un avertissement (une erreur en profil ``prod``,
            fonction E6).
        fallbacks: Modèles de secours, dans l'ordre (fonction B4, point 10).
        name: Nom dans le journal (tirage, rôle ``judge:<nom>``) ; par défaut
            ``output`` pour la réponse finale, le nom du rôle pour un rôle.
        criteria: Les critères (``name``, ``rule``, ``min_score``,
            ``blocking``) ; un au moins.
        context: Contexte reçu : ``user_input``, ``caller_context``,
            ``attachments``, ``session_summary``, ``tool_results``,
            ``last_turns`` ; pas ``session_state`` (décision D11 du state de
            session).
        when: Déclenchement : ``sample``, ``condition``, ``tenants``,
            ``profiles``.
        repair: Réparations demandées à l'auteur de la sortie refusée
            (``max_attempts``, ``tools``).
        on_failure: Suite d'une sortie encore refusée après les réparations :
            ``fail`` (défaut), ``unverified`` ou ``fallback``.
        fallback_message: Message qui remplace la sortie avec ``on_failure:
            fallback``.
        llm: Réglages d'appel du juge.
        timeout: Délai du juge, en secondes ; sans lui, ceux de son modèle et
            son retry le bornent.
        on_error: Erreur du juge (modèle, verdict) : ``block`` (défaut) fait
            échouer le run, ``allow`` laisse passer.
    """

    model: str = Field(min_length=1)
    fallbacks: tuple[str, ...] = ()
    name: str | None = Field(default=None, pattern=CRITERION_NAME_PATTERN)
    criteria: tuple[Criterion, ...] = Field(min_length=1)
    context: tuple[ContextItem, ...] = ()
    when: JudgeWhen = JudgeWhen()
    repair: RepairSettings = RepairSettings()
    on_failure: OnFailure = "fail"
    fallback_message: str | None = None
    llm: LlmSettings = LlmSettings()
    timeout: PositiveFloat | None = None
    on_error: Literal["block", "allow"] = "block"

    @model_validator(mode="before")
    @classmethod
    def _check_input(cls, data: object) -> object:
        _reject_session_state(data, "un juge ne le reçoit pas dans cette version")
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
        """Vrai si le juge reçoit les pièces jointes du run (contexte ``attachments``)."""
        return "attachments" in self.context

    @property
    def chain(self) -> tuple[str, ...]:
        """Modèle, puis ses secours."""
        return (self.model, *self.fallbacks)


def _empty_object() -> dict[str, JsonValue]:
    return {"type": "object", "properties": {}}


class RoleSpec(BaseRole):
    """Rôle délégué (fonction C1) : l'orchestrateur l'appelle comme un outil.

    Déclaré dans ``roles`` de l'agent. Un seul appel de modèle, avec son propre
    prompt et ses réglages ; voir ``engine/roles.py``.

    Attributes:
        name: Nom de l'outil vu par l'orchestrateur ; ``main`` est réservé.
        description: Description vue par l'orchestrateur ; le contexte reçu y
            est ajouté.
        input_schema: Schéma JSON des arguments que l'orchestrateur fournit ;
            objet vide par défaut. Sans ``additionalProperties``, les arguments
            non prévus sont refusés.
        input_template: Construction du message (point 12) : variables
            ``args.<argument>`` et ``context.<contexte>`` ; sans template,
            blocs de contexte puis arguments. Tout le contexte déclaré doit y
            être cité.
        context: Contexte ajouté aux arguments, pris dans une liste fixe (point
            12) : ``user_input``, ``caller_context``, ``attachments``,
            ``session_summary``, ``session_state``, ``tool_results``,
            ``last_turns``. Un rôle sans arguments ni contexte est refusé.
        terminal: Sortie transmise telle quelle comme réponse finale quand le
            rôle est appelé seul dans son tour (fonction C3, point 13).
        timeout: Délai de l'appel, en secondes ; sans lui, ceux du modèle et
            son retry le bornent.
        output: Contrat de sortie (fonction E5) : réparé par le modèle du rôle,
            puis ``on_failure``.
        judge: Juge de la sortie (fonction E5, point 21), après le contrat :
            réparée par le modèle du rôle.
    """

    name: str = Field(pattern=TOOL_NAME_PATTERN)
    description: str = Field(min_length=1)
    input_schema: dict[str, JsonValue] = Field(default_factory=_empty_object)
    input_template: str | None = None
    context: tuple[RoleContextItem, ...] = ()
    terminal: bool = False
    timeout: PositiveFloat | None = None
    output: OutputContract | None = None
    judge: JudgeSpec | None = None

    @model_validator(mode="before")
    @classmethod
    def _check_input(cls, data: object) -> object:
        _reject_two_prompts(data)
        return data

    @model_validator(mode="after")
    def _check(self) -> Self:
        """Nom non réservé, schéma valide, contexte sans doublon, template cohérent.

        Raises:
            ValueError: Nom ``main``, contexte déclaré deux fois, rôle sans
                arguments ni contexte, template invalide.
        """
        if self.name == MAIN_ROLE:
            raise ValueError(f"Le nom {MAIN_ROLE!r} est réservé à l'orchestrateur")
        properties = _check_input_schema(self.input_schema)
        if sum(1 for item in self.context if _names_session_state(item)) > 1:
            raise ValueError("Contexte déclaré deux fois : session_state")
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
        """Noms du contexte déclaré, un par élément.

        ``tool_results.<outil>`` pour chaque outil, ``session_state.<clé>`` pour
        chaque clé nommée du state.
        """
        return _declared_context(self.context)

    @property
    def wants_attachments(self) -> bool:
        """Vrai pour un rôle vision : il reçoit les pièces jointes du run (fonction C4)."""
        return "attachments" in self.context

    @property
    def state_keys(self) -> tuple[str, ...] | None:
        """Clés du state que reçoit le rôle ; () pour toutes, None s'il ne le reçoit pas."""
        for item in self.context:
            if isinstance(item, SessionStateContext):
                return item.session_state
            if item == SESSION_STATE:
                return ()
        return None

    @property
    def tool_results(self) -> tuple[str, ...]:
        """Outils dont le rôle reçoit les résultats."""
        return tuple(tool for item in self.context if isinstance(item, ToolResultsContext) for tool in item.tool_results)


def _declared_context(context: tuple[RoleContextItem, ...]) -> list[str]:
    """Noms d'un contexte déclaré, un par élément.

    ``tool_results.<outil>`` pour chaque outil, ``session_state.<clé>`` pour
    chaque clé nommée du state.
    """
    names: list[str] = []
    for item in context:
        if isinstance(item, ToolResultsContext):
            names += [f"tool_results.{tool}" for tool in item.tool_results]
        elif isinstance(item, SessionStateContext):
            names += [f"{SESSION_STATE}.{key}" for key in item.session_state]
        elif isinstance(item, LastTurnsContext):
            names.append("last_turns")
        else:
            names.append(item)
    return names


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
    """Variables connues, et tout le contexte déclaré utilisé (point 50).

    Args:
        source: Le template.
        properties: Les arguments du schéma d'entrée.
        declared: Le contexte déclaré.

    Raises:
        ValueError: Template invalide, variable inconnue, ou contexte déclaré
            qu'il ne cite pas.
    """
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
                    raise ValueError(f"input_template : {shown} — {name!r} absent de input_schema.properties")
            case (
                "context",
                "user_input" | "caller_context" | "attachments" | "session_summary" | "last_turns" as name,
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
            case ("context", "session_state", *rest):
                used |= _state_used(shown, rest, declared)
            case _:
                raise ValueError(
                    f"input_template : variable inconnue {shown} (attendu args.<argument>, "
                    "context.user_input, context.caller_context, context.attachments, "
                    "context.session_summary, context.last_turns, "
                    "context.tool_results.<outil> ou context.session_state.<clé>)"
                )
    # Les pièces jointes partent en images après le texte : les citer est facultatif.
    unused = [name for name in declared if name not in used and name != "attachments"]
    if unused:
        raise ValueError(f"input_template : contexte déclaré mais non utilisé : {', '.join(unused)}")


def _state_used(shown: str, rest: list[str], declared: list[str]) -> set[str]:
    """Contexte déclaré qu'utilise ``context.session_state``, entier ou par l'une de ses clés.

    Avec des clés nommées, seules elles peuvent être citées. Avec le state
    entier, ce sont celles de l'agent : ``AgentSpec`` les contrôle.
    """
    prefix = f"{SESSION_STATE}."
    named = [name.removeprefix(prefix) for name in declared if name.startswith(prefix)]
    whole = SESSION_STATE in declared
    if not named and not whole:
        raise ValueError(f"input_template : {shown} — contexte 'session_state' non déclaré")
    if not rest:
        used = {prefix + key for key in named}
        return used | {SESSION_STATE} if whole else used
    key = rest[0]
    if named and key not in named:
        raise ValueError(f"input_template : {shown} — {key!r} absente de session_state")
    return {SESSION_STATE} if whole else {prefix + key}


def state_cited(source: str) -> set[str]:
    """Clés du state citées par un template (``context.session_state.<clé>``)."""
    return {path[2] for path in Template.parse(source).variables if len(path) > 2 and path[:2] == ("context", SESSION_STATE)}


def _reject_judge(data: object, what: str) -> None:
    """Un juge porte sur la réponse finale ou sur un rôle, jamais sur un outil.

    Sans ce contrôle, la clé tombait sous « Extra inputs are not permitted »,
    qui ne dit pas pourquoi (essai de la fonction E5, 19/10).
    """
    if isinstance(data, dict) and "judge" in cast(dict[object, object], data):
        raise ValueError(
            f"{what} n'a pas de juge : un juge porte sur la réponse finale (judge: de "
            "l'agent) ou sur un rôle (roles[].judge) ; le résultat d'un outil se contrôle "
            "par son contrat (output:)"
        )


def _reject_two_prompts(data: object) -> None:
    if not isinstance(data, dict):
        return
    keys = cast(dict[object, object], data).keys()
    if {"system", "system_file"} <= keys:
        raise ValueError("'system' et 'system_file' ne peuvent pas être donnés ensemble")


def _context_items(data: object) -> list[object]:
    """Éléments de ``context`` tels qu'écrits, avant validation."""
    if not isinstance(data, dict):
        return []
    context = cast(dict[object, object], data).get("context")
    if not isinstance(context, list | tuple):
        return []
    return list(cast(list[object] | tuple[object, ...], context))


def _names_session_state(item: object) -> bool:
    if isinstance(item, SessionStateContext):
        return True
    return item == SESSION_STATE or (isinstance(item, dict) and SESSION_STATE in cast(dict[object, object], item))


def _reject_session_state(data: object, reason: str) -> None:
    """``session_state`` dans un contexte qui ne le reçoit pas : refusé, et dit pourquoi.

    Sans ce refus, l'élément serait lu comme un contexte inconnu, avec la liste
    de ceux qu'attend un rôle, ou pire, accepté et ignoré à l'exécution.
    """
    if any(_names_session_state(item) for item in _context_items(data)):
        raise ValueError(f"contexte 'session_state' : {reason}")


def _only_session_state(data: object) -> None:
    """``main.context`` n'accepte que ``session_state`` : le reste est déjà dans les messages."""
    for item in _context_items(data):
        if not _names_session_state(item):
            raise ValueError(
                f"main.context : {item!r} refusé, seul 'session_state' est accepté "
                "(la demande, l'historique et les pièces jointes sont déjà dans les messages "
                "de l'orchestrateur)"
            )


_PARAMETERIZED: Final = parameterized_builtins()
"""Outils fournis à paramètres : leur référence a son propre modèle (plus bas).

Seuls les modules légers de leurs paramètres sont importés ici.
"""
_NOT_PARAMETERIZED: Final[dict[str, JsonValue] | None] = {"not": {"enum": list[JsonValue](sorted(_PARAMETERIZED))}} if _PARAMETERIZED else None
"""Pour l'éditeur : une référence à paramètres relève de son propre modèle."""


class PythonTool(DomainModel):
    """Outil Python d'un agent (``tools: - python: …``).

    Référencé par un nom enregistré, un chemin ``module:attr``, ou
    ``loom.<outil>`` pour un outil fourni par loom-ia (``loom_ia.tools_builtin``).

    Les champs renseignés ici remplacent ce que l'outil déclare lui-même. Un outil
    fourni à paramètres (``loom.local_files``) a son propre modèle, qui ajoute ses
    paramètres à ces réglages communs ; les autres n'acceptent rien de plus.

    Attributes:
        python: Référence de l'outil : nom enregistré (module de ``imports``),
            ``module:attr``, ou ``loom.<outil>``.
        description: Ce que le modèle lit de l'outil, à la place de ce que
            l'outil en dit (comme ``description`` des surcharges MCP et de
            paquet).
        timeout: Délai de l'outil, en secondes ; à défaut,
            ``execution.tools.timeout`` (30 s).
        side_effects: Effets de l'outil : ``none``, ``reversible`` ou
            ``irreversible`` ; ils décident de l'ordre des appels et de la
            reprise d'un appel interrompu.
        approval: Approbation : ``never``, ``always`` (chaque appel attend une
            décision humaine) ou ``policy`` (selon une politique
            ``before_tool``).
        idempotent: Vrai si refaire l'appel est sans risque.
        offload_over: Seuil de déport du résultat, en caractères (point 16).
        output: Contrat de sortie (fonction E5) : sans réparation, un résultat
            non conforme revient à l'orchestrateur en erreur.
        skill: Mode d'emploi ajouté à la description de l'outil (fonction D14),
            en ligne ; avec ``skill_file``, rangé ici au chargement.
        skill_file: Mode d'emploi dans un fichier relatif à ``resources_dir``,
            lu au chargement ; exclut ``skill``.
    """

    python: str = Field(min_length=1, json_schema_extra=_NOT_PARAMETERIZED)
    description: str | None = Field(default=None, min_length=1)
    timeout: PositiveFloat | None = None
    side_effects: SideEffects | None = None
    approval: Approval | None = None
    idempotent: bool | None = None
    offload_over: PositiveInt | None = None
    output: OutputContract | None = None
    skill: SkillText | None = None
    skill_file: Path | None = None

    @model_validator(mode="before")
    @classmethod
    def _no_judge(cls, data: object) -> object:
        _reject_judge(data, "un outil Python")
        return data

    @model_validator(mode="after")
    def _one_skill(self) -> Self:
        exclusive_skill(self.skill, self.skill_file, f"Outil {self.python!r} : ")
        return self

    @model_validator(mode="before")
    @classmethod
    def _builtin_without_params(cls, data: object) -> object:
        """Une clé de plus sur un outil fourni sans paramètres : le dire tel quel.

        Sans ce contrôle, elle tombait sous « Extra inputs are not permitted »,
        qui ne dit pas que l'outil n'a pas de paramètres.
        """
        if cls is not PythonTool or not isinstance(data, dict):
            return data
        fields = cast(dict[object, object], data)
        reference = fields.get("python")
        if not isinstance(reference, str) or not is_builtin(reference):
            return fields
        if reference in _PARAMETERIZED:
            # Construite en Python, la référence passerait le chargement sans ses
            # paramètres, et n'échouerait qu'au montage.
            model, _ = _PARAMETERIZED[reference]
            raise ValueError(
                f"{reference} a ses paramètres ({', '.join(model.model_fields)}) : sa référence "
                f"a son propre modèle — la donner en dict dans les outils d'un agent, ou "
                f"construire BUILTIN_REFERENCES[{reference!r}]"
            )
        # ``judge`` a son propre message (``_no_judge``).
        extra = sorted(str(key) for key in fields if key not in cls.model_fields and key != "judge")
        if not extra:
            return fields
        if reference not in BUILTIN_TOOLS:
            known = ", ".join(BUILTIN_TOOLS) or "aucun"
            raise ValueError(f"Outil fourni {reference!r} inconnu (fournis : {known})")
        raise ValueError(f"{reference} n'accepte aucun paramètre ({', '.join(extra)})")


def _builtin_reference(reference: str, params: type[BaseModel], entry: Builtin) -> type[PythonTool]:
    """Le modèle de la référence d'un outil fourni à paramètres.

    Ses paramètres s'ajoutent aux réglages communs, comme clés directes de la
    référence, et sont validés au chargement par le modèle que déclare l'outil.
    Un paramètre au nom d'un réglage commun est une faute du catalogue : elle
    est levée ici, à l'import, et non chez l'utilisateur.
    """
    common = set(PythonTool.model_fields)
    clash = sorted(common & set(params.model_fields))
    if clash:
        raise TypeError(f"{reference} : paramètre au nom d'un réglage commun : {', '.join(clash)}")
    stray = sorted(set(entry.refused) - common)
    if stray:
        raise TypeError(f"{reference} : réglage refusé inconnu : {', '.join(stray)}")
    names = tuple(params.model_fields)

    def check(self: PythonTool) -> PythonTool:
        given = [name for name in entry.refused if getattr(self, name) is not None]
        if given:
            raise ValueError(f"{reference} : {', '.join(given)} ne se règle pas sur cet outil — {entry.refused_because}")
        if entry.own_description and self.description is not None:
            raise ValueError(
                f"{reference} : description ne se règle pas sur cet outil — elle est tirée "
                "de ses paramètres (dossiers, droits, tables, clés du state), que le modèle "
                "doit connaître ; ajouter du texte avec skill"
            )
        # Les contrôles du modèle entier (noms uniques…), que les champs seuls ne font pas.
        try:
            params.model_validate({name: getattr(self, name) for name in names})
        except ModelValidationError as exc:
            raise ValueError(_validation_said(exc)) from None
        return self

    fields: dict[str, Any] = {name: (info.annotation, info) for name, info in params.model_fields.items()}
    validators: dict[str, Any] = {"_builtin_params": model_validator(mode="after")(check)}
    # Le contrôle que le modèle des paramètres fait avant tout (``precheck``) : une clé
    # retirée dite avec ce qui la remplace, plutôt qu'en « Extra inputs are not permitted ».
    precheck = cast(Callable[[object], object] | None, getattr(params, "precheck", None))
    if precheck is not None:
        early = precheck

        def first(cls: type[PythonTool], data: object) -> object:
            return early(data)

        validators["_builtin_precheck"] = model_validator(mode="before")(first)
    built: type[PythonTool] = create_model(
        f"Builtin_{reference.removeprefix(RESERVED_PREFIX)}",
        __base__=PythonTool,
        __doc__=f"Outil fourni ``{reference}``, avec ses paramètres.",
        __module__=__name__,
        __validators__=validators,
        python=(cast(Any, Literal)[reference], ...),
        **fields,
    )
    return built


def _validation_said(error: ModelValidationError) -> str:
    lines: list[str] = []
    for item in error.errors(include_url=False):
        location = ".".join(str(part) for part in item["loc"])
        message = item["msg"].removeprefix("Value error, ")
        lines.append(f"{location} — {message}" if location else message)
    return " ; ".join(lines)


BUILTIN_REFERENCES: Final[dict[str, type[PythonTool]]] = {
    reference: _builtin_reference(reference, model, entry) for reference, (model, entry) in _PARAMETERIZED.items()
}
"""Référence ``loom.<outil>`` → modèle de sa référence, pour les outils à paramètres (``loom.sql``, ``loom.local_files``…)."""


def builtin_settings(declared: PythonTool, params: type[BaseModel]) -> dict[str, object]:
    """Les paramètres d'une référence d'outil fourni, à valider par le modèle de l'outil.

    Args:
        declared: La référence, telle que l'agent la déclare.
        params: Le modèle des paramètres de l'outil.

    Returns:
        Les valeurs des champs de ``params`` présents dans la référence.
    """
    return {name: getattr(declared, name) for name in params.model_fields if hasattr(declared, name)}


class McpTools(DomainModel):
    """Serveur MCP référencé par un agent (point 19).

    Les outils prennent le préfixe ``serveur__`` (ou ``alias__``) ; ``include``
    ou ``exclude`` choisit ceux qui sont exposés, ``tools`` fixe leurs
    déclarations pour cet agent.

    Exemple ::

        tools:
          - mcp: crm
            alias: c
            include: [lookup_client]
            required: true

    Attributes:
        mcp: Nom d'un serveur déclaré dans ``mcp_servers``.
        alias: Préfixe plus court que le nom du serveur (40 caractères au
            plus).
        include: Outils exposés, par leur nom sur le serveur ; exclut
            ``exclude``.
        exclude: Outils écartés ; exclut ``include``.
        required: Sans ce serveur, le run échoue au lieu de continuer sans ses
            outils.
        tools: Surcharges par outil (``description``, ``timeout``,
            ``side_effects``, ``approval``, ``output``, ``skill``…).
    """

    mcp: str = Field(pattern=MCP_NAME_PATTERN)
    alias: str | None = Field(default=None, pattern=MCP_NAME_PATTERN, max_length=40)
    include: tuple[str, ...] | None = None
    exclude: tuple[str, ...] | None = None
    required: bool = False
    tools: dict[str, ToolOverrides] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _no_judge(cls, data: object) -> object:
        _reject_judge(data, "un serveur MCP")
        return data

    @model_validator(mode="after")
    def _check_filters(self) -> Self:
        if self.include is not None and self.exclude is not None:
            raise ValueError(f"Serveur MCP {self.mcp!r} : 'include' ou 'exclude', pas les deux")
        return self

    @property
    def prefix(self) -> str:
        """Préfixe des outils : ``alias``, sinon le nom du serveur."""
        return self.alias or self.mcp

    def owns(self, tool: str) -> bool:
        """Vrai si ``tool`` porte le préfixe de cette référence."""
        return tool.startswith(f"{self.prefix}{MCP_PREFIX_SEPARATOR}")


class SourceTools(DomainModel):
    """Source d'outils d'un paquet installé, référencée par un agent (jalon J6.4b, fonction D12).

    Comme un serveur MCP : les outils prennent le préfixe ``source__`` (ou
    ``alias__``), ``include`` ou ``exclude`` choisit ceux qui sont exposés,
    ``tools`` fixe leurs déclarations pour cet agent.

    Attributes:
        source: Nom d'une source déclarée dans ``tool_sources``.
        alias: Préfixe plus court que le nom de la source (40 caractères au
            plus).
        include: Outils exposés ; exclut ``exclude``.
        exclude: Outils écartés ; exclut ``include``.
        required: Sans cette source, le run échoue au lieu de continuer sans
            ses outils.
        tools: Surcharges par outil, comme pour un serveur MCP.
    """

    source: str = Field(pattern=MCP_NAME_PATTERN)
    alias: str | None = Field(default=None, pattern=MCP_NAME_PATTERN, max_length=40)
    include: tuple[str, ...] | None = None
    exclude: tuple[str, ...] | None = None
    required: bool = False
    tools: dict[str, ToolOverrides] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _no_judge(cls, data: object) -> object:
        _reject_judge(data, "une source d'outils")
        return data

    @model_validator(mode="after")
    def _check_filters(self) -> Self:
        if self.include is not None and self.exclude is not None:
            raise ValueError(f"Source d'outils {self.source!r} : 'include' ou 'exclude', pas les deux")
        return self

    @property
    def prefix(self) -> str:
        """Préfixe des outils : ``alias``, sinon le nom de la source."""
        return self.alias or self.source

    def owns(self, tool: str) -> bool:
        """Vrai si ``tool`` porte le préfixe de cette référence."""
        return tool.startswith(f"{self.prefix}{MCP_PREFIX_SEPARATOR}")


class SubAgentRef(DomainModel):
    """Sous-agent (fonction C5) : un autre agent de la config, appelé comme un outil.

    L'orchestrateur lui passe un seul argument, ``message``. Nom et
    description de l'outil : ceux de l'agent, sauf s'ils sont donnés ici.

    Attributes:
        agent: Nom de l'agent appelé.
        name: Nom de l'outil vu par l'orchestrateur ; par défaut, celui de
            l'agent.
        description: Description de l'outil ; par défaut, celle de l'agent.
        budget_share: Part (entre 0 exclu et 1) de ce qui reste au budget du
            run appelant au moment de l'appel (fonction J4).
        side_effects: Effets du sous-agent, ceux des outils de son agent :
            ``irreversible`` par défaut. Ils décident de l'ordre des appels
            d'un même tour : déclaré ``none``, il tourne en même temps que les
            autres appels sans effet.
    """

    agent: str = Field(pattern=AGENT_NAME_PATTERN)
    name: str | None = Field(default=None, pattern=TOOL_NAME_PATTERN)
    description: str | None = None
    budget_share: float | None = Field(default=None, gt=0.0, le=1.0)
    side_effects: SideEffects = "irreversible"

    @model_validator(mode="before")
    @classmethod
    def _no_judge(cls, data: object) -> object:
        # Le sous-agent juge sa propre réponse finale (son judge:), pas la référence.
        _reject_judge(data, "la référence d'un sous-agent")
        return data

    @property
    def tool_name(self) -> str:
        """Nom de l'outil vu par l'orchestrateur : ``name``, sinon celui de l'agent."""
        return self.name or self.agent


def _tool_kind(value: object) -> str:
    """Balise d'une référence d'outil : ``mcp``, ``source``, ``python`` ou ``loom.<outil>`` à paramètres."""
    if isinstance(value, McpTools):
        return "mcp"
    if isinstance(value, SourceTools):
        return "source"
    if isinstance(value, PythonTool):
        # Une référence déjà construite garde son modèle.
        built = BUILTIN_REFERENCES.get(value.python)
        return value.python if built is not None and isinstance(value, built) else "python"
    if isinstance(value, dict) and "mcp" in value:
        return "mcp"
    if isinstance(value, dict) and "source" in value:
        return "source"
    if isinstance(value, dict):
        reference = cast(dict[object, object], value).get("python")
        if isinstance(reference, str) and reference in BUILTIN_REFERENCES:
            return reference
    return "python"


class PolicyRef(DomainModel):
    """Politique branchée sur l'agent (points 1 et 2, conception §17.4).

    ``hook`` : un nom enregistré (module de ``imports``), un chemin
    ``module:attr``, ou une politique fournie (``loom.require_tool``).
    ``points`` : ceux où l'agent la branche, parmi ceux qu'elle déclare ; par
    défaut, tous. Les politiques d'un point s'exécutent dans l'ordre déclaré.

    Attributes:
        hook: La politique.
        name: Nom dans le journal ; par défaut celui de la politique. Le
            préfixe ``loom.`` est réservé.
        points: Points où l'agent la branche ; par défaut, tous ceux qu'elle
            déclare.
        params: Paramètres donnés à la politique (``context.params``).
        timeout: Délai de la politique, en secondes (5 par défaut) ; ``null``
            le retire.
        on_error: Erreur de la politique : ``block`` (défaut) fait échouer le
            run, ``allow`` l'ignore.
        max_attempts: Réparations (``Retry``) que la politique peut demander
            pour une sortie ; au-delà, sa décision devient ``Fail``.
    """

    hook: str = Field(min_length=1)
    name: str | None = Field(default=None, pattern=POLICY_NAME_PATTERN)
    points: tuple[HookPoint, ...] | None = Field(default=None, min_length=1)
    params: dict[str, JsonValue] = Field(default_factory=dict)
    timeout: PositiveFloat | None = 5.0
    on_error: Literal["block", "allow"] = "block"
    max_attempts: PositiveInt = 1

    @model_validator(mode="after")
    def _check_name(self) -> Self:
        if self.name is not None and self.name.startswith(RESERVED_PREFIX):
            raise ValueError(f"Politique {self.name!r} : le préfixe {RESERVED_PREFIX!r} est réservé à loom-ia")
        return self


if TYPE_CHECKING:
    type ToolRef = PythonTool | McpTools | SourceTools
else:
    ToolRef = Annotated[
        reduce(
            operator.or_,
            (
                Annotated[PythonTool, Tag("python")],
                *(Annotated[model, Tag(ref)] for ref, model in BUILTIN_REFERENCES.items()),
                Annotated[McpTools, Tag("mcp")],
                Annotated[SourceTools, Tag("source")],
            ),
        ),
        Discriminator(_tool_kind),
    ]
    """Référence d'outil d'un agent : Python, outil fourni à paramètres, serveur MCP ou source.

    Une variante par outil fourni à paramètres, sous la balise de sa
    référence.
    """


STATE_KEY_PATTERN: Final = r"^[A-Za-z_][A-Za-z0-9_]{0,63}$"
"""Nom d'une clé du state : un identifiant, que les templates citent tel quel."""
_JSON_POINTER: Final = re.compile(r"(?:/(?:[^~/]|~[01])*)*")
"""Pointeur JSON (RFC 6901) : vide, ou des segments « /… » où « ~ » s'écrit « ~0 » et « / » s'écrit « ~1 »."""
type StateWriter = Literal["app", "tools", "model"]
"""Qui peut demander une écriture d'une clé (décision S1 du state v2).

L'application à l'ouverture d'un run, un outil (``from`` ou
``ToolOutput.state``), le modèle (``loom.state``).
"""
DEFAULT_STATE_WRITERS: Final[tuple[StateWriter, ...]] = ("app", "tools")
"""Écrivains d'une clé qui n'en déclare pas : l'application et les outils."""


class StateSource(DomainModel):
    """Ce qui pose une clé du state (décision D12 du state de session) : chaque résultat réussi de l'outil ``tool``.

    ``path`` : pointeur JSON (RFC 6901) dans les données du résultat
    (``data``) ; ``""`` les prend entières. ``value`` : valeur fixe ; ``null``
    efface la clé (décision D10). L'un ou l'autre, pas les deux.

    ``on_missing`` : ce que fait un résultat où ``path`` ne mène à rien.
    ``refuse`` (défaut) en fait une écriture refusée (décision D8) : le modèle la lit à la
    suite du résultat, et un avertissement va au log — le signe qu'un outil dédié
    a changé la forme de ses données. ``ignore`` ne pose rien, sans rien dire au
    modèle (un message de débogage au log) : un outil générique, comme une
    requête SQL, dont la plupart des résultats ne parlent pas de la clé.

    Attributes:
        tool: Nom de l'outil, tel que le voit le modèle.
        path: Pointeur JSON dans les données du résultat.
        value: Valeur fixe.
        on_missing: ``refuse`` (défaut) ou ``ignore``.
    """

    tool: str = Field(min_length=1)
    path: str | None = None
    value: JsonValue = None
    on_missing: Literal["refuse", "ignore"] = "refuse"

    @model_validator(mode="after")
    def _check(self) -> Self:
        if (self.path is not None) == ("value" in self.model_fields_set):
            raise ValueError(f"from, outil {self.tool!r} : 'path' ou 'value', l'un des deux")
        if self.path is None and "on_missing" in self.model_fields_set:
            raise ValueError(f"from, outil {self.tool!r} : 'on_missing' ne vaut qu'avec 'path' (une 'value' ne manque jamais)")
        if self.path is not None and not _JSON_POINTER.fullmatch(self.path):
            raise ValueError(f"from, outil {self.tool!r} : path {self.path!r} n'est pas un pointeur JSON (« /champ/0 », ou « » pour tout le résultat)")
        return self


class StateExtract(DomainModel):
    """Ce que ``loom.state`` tire du résultat que le modèle désigne (décision S3 du state v2).

    Exactement une règle : ``value`` (la colonne d'une ligne), ``fields`` (un
    objet, champ ← colonne d'une ligne) ou ``columns`` (un objet, champ ← liste
    d'une colonne sur toutes les lignes). ``label`` : le champ qui reçoit le
    libellé donné par le modèle, avec ``fields`` ou ``columns``.

    Attributes:
        value: Colonne dont la valeur, sur la ligne désignée, devient celle de
            la clé.
        fields: Objet construit à partir d'une ligne : champ de la clé ←
            colonne.
        columns: Objet construit à partir de toutes les lignes : champ de la
            clé ← liste des valeurs d'une colonne.
        label: Champ de l'objet qui reçoit le libellé donné par le modèle.
    """

    value: str | None = Field(default=None, min_length=1)
    fields: dict[str, str] = Field(default_factory=dict[str, str])
    columns: dict[str, str] = Field(default_factory=dict[str, str])
    label: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _check(self) -> Self:
        given = [n for n in ("value", "fields", "columns") if getattr(self, n)]
        if len(given) != 1:
            raise ValueError("extract : 'value', 'fields' ou 'columns', une seule des trois")
        if self.label is not None and self.value is not None:
            raise ValueError("extract : 'label' va avec 'fields' ou 'columns', pas avec 'value'")
        named = {**self.fields, **self.columns}
        if self.label is not None and self.label in named:
            raise ValueError(f"extract : le champ {self.label!r} est à la fois label et colonne")
        if any(not field or not column for field, column in named.items()):
            raise ValueError("extract : un champ et une colonne non vides")
        return self


class StateKey(DomainModel):
    """Clé du state de la session (décision D6 du state de session) : son schéma, ce qu'elle désigne, ce qui la pose.

    Déclarée dans ``state.<clé>`` de l'agent ; le nom : lettres, chiffres et
    ``_``, 64 au plus, sans chiffre en tête.

    Exemple ::

        state:
          client_id:
            schema: {type: string}
            description: Client en cours
            from:
              - tool: crm_lookup
                path: /id

    Attributes:
        json_schema: JSON Schema de la valeur (clé ``schema``), contrôlé au
            chargement : une écriture qui ne le tient pas est refusée (décision
            D8).
        description: Ce que la clé désigne : donné avec sa valeur à qui la
            reçoit.
        sources: Outils dont un résultat réussi pose la clé (clé ``from``,
            décision D12) ; chaque outil une fois. Qui peut l'écrire, avec ou
            sans source, c'est ``writers`` : avec ``tools``, tout outil peut
            aussi la poser par ``ToolOutput.state`` (écart E1 du state v2).
        writers: Qui peut demander une écriture (décision S1 du state v2) :
            ``app``, ``tools``, ``model`` ; ``[app, tools]`` par défaut.
            ``from`` exige ``tools`` ; ``model`` exige ``extract``.
        extract: Ce que ``loom.state`` tire du résultat que le modèle désigne :
            exigé avec ``model`` dans ``writers``, refusé sans.
        clears: Clés que poser celle-ci par ``loom.state`` efface dans la même
            écriture : une nouvelle liste remplace le document dont on parlait
            (``clears: [document]``). Exige ``extract`` ; jamais une clé
            ``required``.
        required: Run refusé à l'ouverture si la clé manque au state de la
            session (décision D13) ; exclut ``default``.
        default: Valeur lue quand la clé est absente ; elle n'est jamais écrite
            (décision D13).
    """

    model_config = ConfigDict(frozen=True, extra="forbid", validate_by_name=True)

    json_schema: dict[str, JsonValue] = Field(alias="schema")
    description: str = ""
    sources: tuple[StateSource, ...] = Field(default=(), alias="from")
    writers: tuple[StateWriter, ...] = Field(default=DEFAULT_STATE_WRITERS, min_length=1)
    extract: StateExtract | None = None
    clears: tuple[str, ...] = ()
    required: bool = False
    default: JsonValue = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        """Cohérence de la clé : schéma, ``required`` et ``default``, ``writers``, ``extract``, ``clears``, ``from``.

        Raises:
            ValueError: Schéma invalide ; ``required`` avec ``default`` ;
                valeur par défaut ou fixe hors schéma ; écrivain cité deux
                fois ; ``model`` sans ``extract`` ou l'inverse ; ``clears``
                sans ``extract`` ; ``from`` sans ``tools`` ; ``value: null``
                sur une clé ``required`` ; outil cité deux fois.
        """
        try:
            check_schema(self.json_schema)
        except SchemaError as exc:
            raise ValueError(f"schema invalide : {exc.message}") from exc
        if self.required and self.default is not None:
            raise ValueError("'required' et 'default' s'excluent : une clé qui a une valeur par défaut ne manque jamais")
        validator = bounded_validator(self.json_schema)
        if self.default is not None:
            _conforms(validator, self.default, "default")
        if len(set(self.writers)) != len(self.writers):
            raise ValueError("writers : écrivain cité deux fois")
        if ("model" in self.writers) != (self.extract is not None):
            raise ValueError("'model' dans writers et 'extract' vont ensemble : le modèle désigne un résultat, extract dit ce qu'on en tire")
        if self.clears and self.extract is None:
            raise ValueError("'clears' va avec 'extract' : il s'applique quand le modèle pose la clé")
        if len(set(self.clears)) != len(self.clears):
            raise ValueError("clears : clé citée deux fois")
        if self.sources and "tools" not in self.writers:
            raise ValueError("'from' pose la clé par un outil : 'tools' doit être dans writers")
        if self.required and any(source.path is None and source.value is None for source in self.sources):
            raise ValueError("from : une 'value' null effacerait une clé obligatoire ('required'), que seule l'application remplace")
        tools = [source.tool for source in self.sources]
        doubles = sorted({tool for tool in tools if tools.count(tool) > 1})
        if doubles:
            raise ValueError(f"from : outil cité deux fois : {', '.join(doubles)}")
        for source in self.sources:
            if source.path is None and source.value is not None:
                _conforms(validator, source.value, f"from, outil {source.tool!r} : value")
        return self


def _conforms(validator: Validator, value: JsonValue, label: str) -> None:
    """La valeur suit le schéma de la clé, ou le chargement s'arrête en le disant."""
    if not finite_json(value):
        raise ValueError(f"{label} contient NaN ou un infini, que le JSON du journal n'écrit pas")
    try:
        validator.validate(value)
    except ValidationError as exc:
        raise ValueError(f"{label} ne suit pas le schéma de la clé : {exc.message}") from None
    except RegexTimeout as exc:
        raise ValueError(f"{label} : le schéma de la clé ne peut pas être appliqué ({exc})") from None


class ApproveAccess(DomainModel):
    """Qui tranche les demandes d'approbation d'un agent (décision D-2 des utilisateurs et rôles, ``docs/loom_role_user.md`` §5.2).

    Des rôles d'accès, déclarés dans ``security.roles`` de ``loom.yaml``. Sans
    liste, tout rôle qui a la permission ``approve`` sur l'agent convient.

    Attributes:
        default: Approbateurs de toute demande de l'agent, à défaut d'une liste
            par outil.
        tools: Approbateurs par outil, sous le nom que voit le modèle (préfixe
            MCP compris), comme ``tenants[].tools_deny`` : c'est le
            ``tool_name`` de ``approval.requested``.
    """

    default: tuple[str, ...] | None = None
    tools: dict[str, tuple[str, ...]] = Field(default_factory=dict[str, tuple[str, ...]])

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.default is not None:
            _check_access_list("access.approve.default", self.default)
        for tool, roles in self.tools.items():
            _check_access_list(f"access.approve.tools.{tool}", roles)
        return self


class AgentAccess(DomainModel):
    """Ce que l'agent exige de qui agit sur lui (décisions D-2 et D-7b des utilisateurs et rôles, ``docs/loom_role_user.md`` §5.2).

    Bloc ``access`` de l'agent. Il ne cite que des rôles d'accès : les
    utilisateurs se déclarent dans ``loom.yaml``, une fois pour tous les agents.
    Une liste absente laisse décider les permissions des rôles ; une liste
    présente restreint.

    Attributes:
        run: Rôles qui lancent l'agent.
        answer: Rôles qui répondent à ses questions (``loom.ask``).
        cancel: Rôles qui arrêtent ses runs.
        approve: Qui tranche ses demandes d'approbation.
        self_approval: ``false`` interdit l'auto-approbation sur cet agent,
            même à un rôle qui a ``self_approve`` (décision D-7b). L'agent ne
            fait que durcir : ``true`` laisse décider les rôles, il n'ouvre
            rien.
    """

    run: tuple[str, ...] | None = None
    answer: tuple[str, ...] | None = None
    cancel: tuple[str, ...] | None = None
    approve: ApproveAccess = ApproveAccess()
    self_approval: bool = True

    @model_validator(mode="after")
    def _check(self) -> Self:
        for key in ("run", "answer", "cancel"):
            roles: tuple[str, ...] | None = getattr(self, key)
            if roles is not None:
                _check_access_list(f"access.{key}", roles)
        return self

    def approvers(self) -> Approvers:
        """Qui tranche les demandes de l'agent, tel que le moteur le fige dans chacune (``docs/loom_role_user.md`` §5.3).

        Par outil, la liste de l'outil, à défaut celle de l'agent (``default``) ;
        une politique ``Pause`` sur n'importe quel outil suit la même règle (§5.2).
        """
        return Approvers(
            default=self.approve.default,
            tools=dict(self.approve.tools),
            self_approval=self.self_approval,
        )

    def listed(self) -> tuple[tuple[str, tuple[str, ...]], ...]:
        """Chaque liste déclarée, avec la permission qu'elle exige de ses rôles."""
        pairs: list[tuple[str, tuple[str, ...]]] = [
            (permission, roles)
            for permission, roles in (
                ("run", self.run),
                ("answer", self.answer),
                ("cancel", self.cancel),
                ("approve", self.approve.default),
            )
            if roles is not None
        ]
        pairs += [("approve", roles) for roles in self.approve.tools.values()]
        return tuple(pairs)


def _check_access_list(label: str, roles: tuple[str, ...]) -> None:
    """Une liste de rôles d'accès : non vide, sans doublon, des identifiants valides.

    Une liste vide ne laisserait personne faire : c'est le contraire de ce
    qu'on lit, puisqu'une liste absente laisse tout rôle qui a la permission.
    """
    if not roles:
        raise ValueError(f"{label} : liste vide — personne ne pourrait agir ; retirer la clé pour laisser décider les permissions des rôles")
    doubles = sorted({role for role in roles if roles.count(role) > 1})
    if doubles:
        raise ValueError(f"{label} : rôle cité deux fois : {', '.join(doubles)}")
    for role in roles:
        if not re.fullmatch(AGENT_NAME_PATTERN, role):
            raise ValueError(f"{label} : identifiant de rôle invalide : {role!r}")


class AgentToolsExecution(DomainModel):
    """Exécution des outils propre à l'agent ; ``None`` garde le réglage de la racine.

    Attributes:
        order: Ordre des appels d'un même tour : ``effects`` ou ``free``
            (``execution.tools.order`` de loom.yaml).
    """

    order: Literal["effects", "free"] | None = None


class AgentExecution(DomainModel):
    """Exécution propre à l'agent (``execution``) : remplace, clé par clé, celle de loom.yaml.

    Attributes:
        tools: Exécution des outils de l'agent.
    """

    tools: AgentToolsExecution = AgentToolsExecution()


class AgentSpec(DomainModel):
    """Un agent (fonction A9) : son orchestrateur, ses outils, rôles et sous-agents, ses contrôles et réglages.

    Un fichier ``<agents_dir>/<nom>.yaml`` par agent (conception §17.4), ou un
    objet passé en Python. Les noms d'outils, de rôles, de sous-agents, de
    juges et les préfixes MCP sont uniques dans l'agent.

    Exemple ::

        name: support
        main:
          model: SONNET
          system_file: prompts/support.md
        tools:
          - python: loom.fetch_page
          - mcp: crm
        roles:
          - name: redacteur
            model: HAIKU
            description: Rédige la réponse au client
            context: [user_input]
            terminal: true

    Attributes:
        name: Nom de l'agent : lettres, chiffres, ``_`` et ``-``, 64 au plus.
            Un nom qui commence par ``_`` est réservé aux agents internes.
        description: Description de l'agent (sous-agent, outil MCP,
            ``/v1/agents``).
        expose: Points d'accès qui le publient.
        main: L'orchestrateur : modèle, secours, prompt, ``llm``, ``context``.
        max_iterations: Appels de l'orchestrateur avant la réponse forcée sans
            outils (10 par défaut).
        timeout: Délai maximal d'un run, en secondes (fonction A6, backlog
            #006). Il borne le temps de pilotage cumulé, pas l'horloge :
            l'attente en file, une pause et le temps entre un plantage et sa
            reprise ne comptent pas. Un dépassement écrit ``run.failed`` avec
            ``error_type: timeout`` : c'est une clôture, que ``resume`` ne
            relance pas, même avec un délai relevé. Sans valeur, pas de délai.
        approval: Approbations de l'agent (point 17) : délai laissé à
            l'approbateur (``expires_in``), effet d'une demande périmée
            (``on_expiry``). Un outil les demande par sa déclaration
            (``approval: always``) ou par une politique ; qui les tranche,
            c'est ``access.approve``.
        access: Qui lance, répond, arrête et approuve (décision D-2 des
            utilisateurs et rôles) : des rôles d'accès de loom.yaml.
        output: Contrat de la réponse finale (fonctions A7 et E1) : réparée par
            l'orchestrateur.
        judge: Juge de la réponse finale (fonction E3, point 21), après son
            contrat.
        budget: Budgets de l'agent (fonction J4 ; ``run``, ``session``,
            ``on_exceed``) : surchargent ceux de la racine (``budgets``), clé
            par clé.
        stream_output: Diffusion de la réponse finale ; par défaut
            ``after_guards`` si elle est contrôlée (contrat, juge, politique
            ``on_output``, rôle terminal sous contrat ou jugé), sinon ``live``
            (point 11).
        tools: Outils Python (``python``), serveurs MCP (``mcp``) et sources
            d'outils (``source``), dans l'ordre de déclaration.
        roles: Rôles délégués.
        subagents: Sous-agents.
        policies: Politiques, dans l'ordre d'exécution à chaque point (point
            2).
        max_depth: Profondeur maximale d'un run de cet agent pour appeler ses
            sous-agents : à 1, un run racine les appelle, mais un sous-run de
            cet agent ne le peut pas.
        state: State de la session (option C) : les clés qu'elle peut porter
            (décision D6 du state de session).
        execution: Exécution des outils : remplace, clé par clé,
            ``execution.tools`` de loom.yaml.
    """

    name: str = Field(pattern=AGENT_NAME_PATTERN)
    description: str = ""
    expose: Expose = Expose()
    main: MainRole
    max_iterations: PositiveInt = 10
    timeout: PositiveFloat | None = None
    approval: ApprovalSettings = ApprovalSettings()
    access: AgentAccess = AgentAccess()
    output: OutputContract | None = None
    judge: JudgeSpec | None = None
    budget: Budgets | None = None
    stream_output: StreamOutput | None = None
    tools: tuple[ToolRef, ...] = ()
    roles: tuple[RoleSpec, ...] = ()
    subagents: tuple[SubAgentRef, ...] = ()
    policies: tuple[PolicyRef, ...] = ()
    max_depth: PositiveInt = 1
    state: dict[str, StateKey] = Field(default_factory=dict[str, StateKey])
    execution: AgentExecution = AgentExecution()

    @model_validator(mode="after")
    def _check_state(self) -> Self:
        """Clés du state valides, et citées seulement si déclarées (``main.context``, rôles, ``loom.state``, ``clears``).

        Raises:
            ValueError: Nom de clé invalide, clé citée mais non déclarée,
                ``model`` dans ``writers`` sans l'outil ``loom.state`` (ou
                l'inverse), clé écrite par le modèle sans lui être montrée,
                ``clears`` incohérent.
        """
        for key in self.state:
            if not re.fullmatch(STATE_KEY_PATTERN, key):
                raise ValueError(f"state : clé {key!r} invalide (lettres, chiffres et '_', 64 au plus, sans chiffre en tête)")
        named = self.main.state_keys
        if named is not None and not self.state:
            raise ValueError("main.context : session_state, mais aucune clé déclarée dans 'state'")
        unknown = sorted(key for key in named or () if key not in self.state)
        if unknown:
            raise ValueError(f"main.context : session_state cite {', '.join(map(repr, unknown))}, absente(s) de 'state' (clés : {', '.join(self.state)})")
        for role in self.roles:
            self._check_role_state(role)
        self._check_model_state()
        self._check_clears()
        return self

    def _check_clears(self) -> None:
        """``clears`` : des clés déclarées, que le modèle peut effacer, jamais ``required``."""
        for key, declared in self.state.items():
            for other in declared.clears:
                label = f"state {key!r}, clears {other!r}"
                target = self.state.get(other)
                if other == key:
                    raise ValueError(f"{label} : une clé ne s'efface pas en se posant")
                if target is None:
                    raise ValueError(f"{label} : clé non déclarée (clés : {', '.join(self.state)})")
                if target.required:
                    raise ValueError(f"{label} : clé obligatoire, rien ne l'efface")
                if "model" not in target.writers:
                    raise ValueError(f"{label} : l'effacement vient du modèle, 'model' doit être dans ses writers")

    def _check_model_state(self) -> None:
        """Clés tenues par le modèle : ``loom.state`` déclaré, et les clés montrées à ``main``."""
        kept = [key for key, declared in self.state.items() if "model" in declared.writers]
        provided = any(tool.python == "loom.state" for tool in self.python_tools)
        if kept and not provided:
            raise ValueError(
                f"state : {', '.join(map(repr, kept))} a 'model' dans writers, mais l'agent n'a pas l'outil loom.state (tools: - python: loom.state)"
            )
        if provided and not kept:
            raise ValueError("loom.state : aucune clé du state n'a 'model' dans writers, l'outil n'aurait rien à écrire")
        shown = self.main.state_keys
        hidden = [key for key in kept if shown is None or (shown and key not in shown)]
        if hidden:
            raise ValueError(f"state : le modèle écrit {', '.join(map(repr, hidden))} sans la voir — la montrer dans main.context (session_state)")

    def _check_role_state(self, role: RoleSpec) -> None:
        """Le state que reçoit un rôle : des clés que l'agent déclare, citées comme telles."""
        keys = role.state_keys
        if keys is None:
            return
        label = f"Rôle {role.name!r} : contexte session_state"
        if not self.state:
            raise ValueError(f"{label}, mais aucune clé déclarée dans 'state'")
        declared = f"(clés : {', '.join(self.state)})"
        unknown = sorted(key for key in keys if key not in self.state)
        if unknown:
            raise ValueError(f"{label} : {', '.join(map(repr, unknown))} absente(s) de 'state' {declared}")
        template = role.input_template
        cited: set[str] = state_cited(template) if template is not None else set()
        unknown = sorted(key for key in cited if key not in self.state)
        if unknown:
            raise ValueError(f"Rôle {role.name!r} : input_template cite {', '.join(map(repr, unknown))}, absente(s) de 'state' {declared}")

    @model_validator(mode="after")
    def _unique_tools(self) -> Self:
        """Outils, préfixes MCP et de source, rôles, sous-agents et juges uniques dans l'agent.

        Raises:
            ValueError: Un nom déclaré deux fois ; le message dit comment
                départager (``alias``, ``name``).
        """
        names = [tool.python for tool in self.python_tools]
        doubles = {name for name in names if names.count(name) > 1}
        if doubles:
            raise ValueError(f"Outil déclaré deux fois : {', '.join(sorted(doubles))}")
        prefixes = [ref.prefix for ref in self.mcp_tools]
        doubles = {name for name in prefixes if prefixes.count(name) > 1}
        if doubles:
            raise ValueError(f"Préfixe MCP déclaré deux fois : {', '.join(sorted(doubles))} (donner un 'alias' à l'une des références)")
        prefixes += [ref.prefix for ref in self.source_tools]
        doubles = {name for name in prefixes if prefixes.count(name) > 1}
        if doubles:
            raise ValueError(
                f"Préfixe d'outils déclaré deux fois : {', '.join(sorted(doubles))} (serveur MCP ou source d'outils ; donner un 'alias' à l'une des références)"
            )
        roles = [role.name for role in self.roles]
        doubles = {name for name in roles if roles.count(name) > 1}
        if doubles:
            raise ValueError(f"Rôle déclaré deux fois : {', '.join(sorted(doubles))}")
        subagents = [ref.tool_name for ref in self.subagents]
        doubles = {name for name in subagents if subagents.count(name) > 1}
        if doubles:
            raise ValueError(f"Sous-agent déclaré deux fois : {', '.join(sorted(doubles))} (donner un 'name' à l'une des références)")
        judges = [name for name, _, _ in self.judges]
        doubles = {name for name in judges if judges.count(name) > 1}
        if doubles:
            raise ValueError(f"Juge déclaré deux fois : {', '.join(sorted(doubles))} (donner un 'name' à l'un des juges)")
        return self

    @property
    def judges(self) -> tuple[tuple[str, RoleSpec | None, JudgeSpec], ...]:
        """Juges de l'agent : nom, rôle jugé (None pour la réponse finale), définition."""
        found: list[tuple[str, RoleSpec | None, JudgeSpec]] = []
        if self.judge is not None:
            found.append((self.judge.name or OUTPUT_JUDGE, None, self.judge))
        found += [(role.judge.name or role.name, role, role.judge) for role in self.roles if role.judge is not None]
        return tuple(found)

    @property
    def contracts(self) -> bool:
        """Vrai si l'agent déclare un contrat de sortie (réponse, rôle ou outil)."""
        return (
            self.output is not None
            or any(role.output is not None for role in self.roles)
            or any(tool.output is not None for tool in self.python_tools)
            or any(o.output is not None for ref in self.mcp_tools for o in ref.tools.values())
            or any(o.output is not None for ref in self.source_tools for o in ref.tools.values())
        )

    @property
    def python_tools(self) -> tuple[PythonTool, ...]:
        """Outils Python de l'agent, outils fournis compris."""
        return tuple(tool for tool in self.tools if isinstance(tool, PythonTool))

    @property
    def mcp_tools(self) -> tuple[McpTools, ...]:
        """Serveurs MCP référencés par l'agent."""
        return tuple(tool for tool in self.tools if isinstance(tool, McpTools))

    @property
    def source_tools(self) -> tuple[SourceTools, ...]:
        """Sources d'outils de paquets référencées par l'agent."""
        return tuple(tool for tool in self.tools if isinstance(tool, SourceTools))
