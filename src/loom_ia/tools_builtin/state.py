# SPDX-License-Identifier: Apache-2.0
"""``loom.state`` : le modèle tient à jour le state de la session (state v2).

Le modèle **désigne** un résultat d'outil du run par sa référence (``result:3``,
celle qui précède chaque résultat) ; l'outil le relit (``ToolContext.results``)
et en **tire** la valeur selon la règle déclarée sur la clé (``extract:``). Le
modèle ne transmet aucune donnée : il ne peut ni mal recopier un identifiant,
ni en inventer un.

    {"set": "client", "result": "result:3", "match": {"code": "C-261"}}
    {"set": "selection", "result": "result:5", "label": "factures en retard"}
    {"clear": "document"}

L'outil est tolérant sur la forme de ce qu'il reçoit, strict sur le fond :
``result`` vaut ``"result:3"``, ``"3"``, ``3`` ou ``{"$ref": "result:3"}`` (le
moteur ne remplace pas cette référence par des données, pour cet outil) ; la
ligne se désigne par ``match`` (des colonnes et leurs valeurs, que doit porter
une seule ligne) plutôt que par ``row``, qu'un modèle compte mal. Poser une clé
efface aussi celles de son ``clears:``.

Seules les clés qui ont ``model`` dans leurs ``writers`` sont proposées. Un
refus — clé inconnue, résultat introuvable, extraction ou schéma — est un
résultat en erreur qui dit quoi corriger (S4) : l'outil n'a pas d'autre effet
que l'écriture. L'écriture passe ensuite par les contrôles du moteur, comme
celle de tout outil (D6 à D9) : seule la taille totale du state, qui dépend des
voisins du lot, peut encore la refuser, en le disant à la suite du résultat.
"""

import json
import re
from collections.abc import Mapping
from typing import Final, cast

from jsonschema.protocols import Validator
from pydantic import BaseModel, JsonValue

from loom_ia.core.bounded_regex import RegexTimeout, bounded_validator
from loom_ia.core.model import JsonBlock, ToolOutput, ToolSpec, finite_json, state_size
from loom_ia.core.ports import ToolContext, ToolError
from loom_ia.core.schema_errors import schema_problem
from loom_ia.core.state_extract import ExtractError, Extraction, ModelKey, extract
from loom_ia.tools_builtin import BuiltinContext
from loom_ia.tools_builtin.state_params import StateParams

NAME: Final = "state"
MAX_LABEL: Final = 200

_ACTIONS: Final = ("set", "clear")
_REF: Final = re.compile(r"(?:result:)?\s*([1-9][0-9]*)")


class StateTool:
    """Pose ou efface une clé du state que le modèle tient à jour."""

    def __init__(self, keys: tuple[ModelKey, ...], max_value_bytes: int) -> None:
        if not keys:
            raise ValueError(
                "aucune clé du state n'a 'model' dans ses writers : loom.state n'aurait rien "
                "à écrire (state.<clé>.writers: [model] et extract:)"
            )
        self.keys: dict[str, ModelKey] = {key.name: key for key in keys}
        self.max_value_bytes = max_value_bytes
        self._validators: dict[str, Validator] = {
            key.name: bounded_validator(key.schema) for key in keys
        }
        names: list[JsonValue] = list(self.keys)
        schema: dict[str, JsonValue] = {
            "type": "object",
            "properties": {
                "set": {
                    "type": "string",
                    "enum": names,
                    "description": "Clé à poser depuis un résultat (avec 'result').",
                },
                "clear": {"type": "string", "enum": names, "description": "Clé à effacer."},
                "result": {
                    "type": ["string", "integer", "object"],
                    "description": (
                        "Référence d'un résultat d'outil reçu à un tour précédent de ce run, "
                        "ex. result:3."
                    ),
                },
                "match": {
                    "type": "object",
                    "description": (
                        "Pour un résultat de plusieurs lignes : une ou des colonnes et leurs "
                        'valeurs, qui désignent la ligne voulue, ex. {"code": "C-261"}.'
                    ),
                },
                "row": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "Sinon, le numéro de la ligne (à partir de 0).",
                },
                "label": {
                    "type": "string",
                    "maxLength": MAX_LABEL,
                    "description": "Libellé, pour une clé qui en prend un.",
                },
            },
            "additionalProperties": False,
        }
        self._spec = ToolSpec(
            name=NAME,
            description=_description(keys),
            input_schema=schema,
            kind="python",
            side_effects="none",
            idempotent=True,
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def settings_lines(self) -> tuple[str, ...]:
        """Une ligne par clé, pour ``loom validate``."""
        return tuple(f"{key.name} : {_rule(key.extraction)}" for key in self.keys.values())

    async def invoke(self, arguments: dict[str, JsonValue], context: ToolContext) -> ToolOutput:
        if not context.keeps_state:
            raise ToolError(
                "Ce run ne tient pas le state de la session (sous-agent, ou hors d'une "
                "session) : rien n'est posé."
            )
        actions = [action for action in _ACTIONS if action in arguments]
        if len(actions) != 1:
            raise ToolError("Donner 'set' (avec 'result') ou 'clear', l'un des deux.")
        name = _text(arguments[actions[0]], actions[0])
        key = self.keys.get(name)
        if key is None:
            raise ToolError(f"Clé {name!r} inconnue ici (clés : {', '.join(self.keys)}).")
        if actions[0] == "clear":
            extra = sorted(set(arguments) - {"clear"})
            if extra:
                raise ToolError(f"'clear' ne prend rien d'autre (retirer : {', '.join(extra)}).")
            if key.required:
                raise ToolError(f"{name} : clé obligatoire, elle ne s'efface pas.")
            return _output({"cleared": name}, {name: None})
        return await self._set(key, arguments, context)

    async def _set(
        self, key: ModelKey, arguments: Mapping[str, JsonValue], context: ToolContext
    ) -> ToolOutput:
        if "result" not in arguments:
            raise ToolError(f"{key.name} : donner 'result', la référence d'un résultat (result:3).")
        ref = _ref(arguments["result"])
        row = arguments.get("row")
        if row is not None and (not isinstance(row, int) or isinstance(row, bool) or row < 0):
            raise ToolError("'row' : un entier, à partir de 0.")
        match = arguments.get("match")
        if match is not None and (
            not isinstance(match, dict)
            or not match
            or not all(isinstance(v, str | int | float) for v in match.values())
        ):
            raise ToolError("'match' : un objet {colonne: valeur}, valeurs simples.")
        label = arguments.get("label")
        if label is not None:
            label = _text(label, "label")
        if context.results is None:
            raise ToolError("Aucun résultat lisible hors d'un run.")
        try:
            data = await context.results.read(ref)
        except ToolError as exc:
            hint = (
                " Appelle state au tour suivant, une fois le résultat reçu."
                if "pas encore de résultat" in str(exc)
                else ""
            )
            raise ToolError(f"{key.name} non posé : {exc}{hint}") from None
        try:
            value = extract(data, key.extraction, row=row, match=match, label=label)
        except ExtractError as exc:
            raise ToolError(f"{key.name} non posé : {exc}") from None
        problem = self._problem(key.name, value)
        if problem is not None:
            raise ToolError(f"{key.name} non posé : {problem}")
        cleared = [name for name in key.clears if name in context.state]
        echo: dict[str, JsonValue] = {"set": key.name, "value": value, "from": ref}
        if cleared:
            echo["cleared"] = list[JsonValue](cleared)
        return _output(echo, {key.name: value, **dict.fromkeys(key.clears)})

    def _problem(self, name: str, value: JsonValue) -> str | None:
        """Ce qui empêche de poser cette valeur, sans la recopier (elle peut porter un secret)."""
        if value is None:
            return "la valeur extraite est vide (null)"
        if not finite_json(value):
            return "la valeur contient NaN ou un infini"
        try:
            errors = sorted(
                self._validators[name].iter_errors(value), key=lambda e: list(e.absolute_path)
            )
        except RegexTimeout as exc:
            return f"son schéma ne s'applique pas ({exc})"
        if errors:
            error = errors[0]
            location = ".".join(str(part) for part in error.absolute_path)
            said = schema_problem(error)
            return (
                f"ne suit pas son schéma — {location} : {said}"
                if location
                else (f"ne suit pas son schéma — {said}")
            )
        size = state_size(value)
        if size > self.max_value_bytes:
            return (
                f"{size} octets, au-delà de sessions.state.max_value_bytes ({self.max_value_bytes})"
            )
        return None


def build(params: BaseModel, context: BuiltinContext) -> StateTool:
    """La fabrique de ``loom.state`` : les clés viennent du state de l'agent, par le montage."""
    if not isinstance(params, StateParams):
        raise TypeError(f"paramètres de loom.{NAME} attendus, reçu {type(params).__name__}")
    return StateTool(context.state_keys, context.state_max_value_bytes)


def _output(data: dict[str, JsonValue], state: dict[str, JsonValue]) -> ToolOutput:
    value = cast(JsonValue, data)
    return ToolOutput(blocks=(JsonBlock(data=value),), data=value, state=state)


def _ref(value: JsonValue) -> str:
    """``result:N`` d'après ce que le modèle a écrit : texte, nombre, ``$ref`` ou son JSON."""
    if isinstance(value, str) and value.strip().startswith("{"):
        try:
            value = cast(JsonValue, json.loads(value))
        except ValueError:
            pass
    if isinstance(value, dict) and set(value) == {"$ref"}:
        value = value["$ref"]
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return f"result:{value}"
    if isinstance(value, str):
        found = _REF.fullmatch(value.strip())
        if found is not None:
            return f"result:{found.group(1)}"
    raise ToolError("'result' : la référence d'un résultat de ce run, ex. result:3.")


def _text(value: JsonValue, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ToolError(f"'{field}' : un texte non vide.")
    return value


def _rule(extraction: Extraction) -> str:
    if extraction.value is not None:
        return f"une ligne, colonne {extraction.value}"
    if extraction.fields:
        shown = ", ".join(
            name if name == column else f"{name} ← {column}"
            for name, column in extraction.fields.items()
        )
        rule = f"une ligne, colonnes {shown}"
    else:
        shown = ", ".join(f"{name} ← {column}" for name, column in extraction.columns.items())
        rule = f"toutes les lignes, {shown}"
    if extraction.label is not None:
        rule += f" ; libellé dans {extraction.label} ('label')"
    return rule


def _description(keys: tuple[ModelKey, ...]) -> str:
    lines = [
        "Tient à jour le state de la session (le bloc <session_state>), pour qu'il dise "
        "toujours de quoi parle la conversation.",
        "- set : pose une clé depuis un résultat d'outil que tu as déjà reçu dans ce run, "
        "désigné par sa référence (result, ex. result:3, celle qui précède le résultat). Loom "
        "en tire la valeur : ne la recopie jamais. Si le résultat a plusieurs lignes, désigne "
        'la bonne avec match (une colonne et sa valeur, ex. {"code": "C-261"}).',
        "- clear : efface une clé quand la conversation n'en parle plus.",
        "Pose une clé dès qu'un résultat désigne ce dont on parle, avant de répondre. Le "
        "résultat doit être arrivé : appelle state au tour qui suit l'outil qui l'a rendu, "
        "éventuellement avec d'autres outils.",
        "Le state est ta tenue de dossier interne : pose ou efface une clé sans demander "
        "l'accord de l'utilisateur, et sans en parler dans ta réponse.",
        "Si ta réponse n'appelle que state, son texte est ta réponse finale : le run s'arrête "
        "après l'écriture. Écris donc ta réponse dans ce même tour, ou n'écris rien et "
        "réponds au tour suivant.",
        "Clés :",
    ]
    for key in keys:
        said = f" {key.description}" if key.description else ""
        clears = f" ; la poser efface {', '.join(key.clears)}" if key.clears else ""
        lines.append(f"- {key.name} :{said} ({_rule(key.extraction)}{clears})")
    return "\n".join(lines)
