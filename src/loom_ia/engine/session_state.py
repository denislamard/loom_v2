# SPDX-License-Identifier: Apache-2.0
"""State de la session côté moteur : clés déclarées, écritures des outils (option C).

Les clés viennent de la config (``state:``) ; le montage les traduit en
``StateRules``, que l'exécuteur applique au résultat réussi de chaque outil,
après les politiques ``after_tool`` et avant le déport :

- les sources ``from`` de l'outil donnent chacune une valeur, lue dans les
  données du résultat (``path``, pointeur JSON) ou fixe (``value``). Ce que
  l'outil écrit lui-même (``ToolOutput.state``) l'emporte sur une même clé (D12) ;
- chaque écriture est contrôlée : clé déclarée, schéma (D6), taille de la
  valeur et du state de la session (D7). ``None`` efface la clé.

Une écriture refusée n'est pas posée, les autres le sont, et le résultat reste
un succès : l'outil a pu produire son effet, et le dire en erreur pousserait
le modèle à le refaire. Le refus est gardé dans le résultat
(``ToolOutput.state_refused``), que le modèle lit à sa suite, et dit au log
(D8, décision du 14/10).

Le state que l'application pose à l'ouverture d'un run suit les mêmes
contrôles, mais un refus y refuse le run, avant tout écrit (``StateRefused``) ;
de même une clé ``required`` restée sans valeur (D13).

L'orchestrateur qui déclare ``main.context`` reçoit le state en dernier message
de chaque requête (D4, ``state_message``), jamais journalisé comme message, et,
dans son prompt système, ce que désigne chaque clé (``StateRules.hint``) : le
système ne change pas d'un appel à l'autre, seul le bloc de fin est payé hors cache.
"""

import copy
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Final

from jsonschema import ValidationError
from jsonschema.protocols import Validator
from pydantic import JsonValue

from loom_ia.core.bounded_regex import RegexTimeout, bounded_validator
from loom_ia.core.model import (
    SESSION_STATE_TAG,
    Message,
    RunState,
    ToolOutput,
    finite_json,
    state_json,
    state_size,
)
from loom_ia.core.schema_errors import schema_problem
from loom_ia.engine.roles import tagged

logger = logging.getLogger(__name__)

# Tailles par défaut (D7), en octets de JSON compact : celles de ``sessions.state``.
DEFAULT_MAX_VALUE_BYTES: Final = 1024
DEFAULT_MAX_TOTAL_BYTES: Final = 4096

# Début de la consigne du prompt système d'un orchestrateur qui reçoit le state ;
# suit une ligne par clé montrée, avec ce qu'elle désigne.
STATE_HINT: Final = (
    "La fin de chaque requête donne l'état courant de la session, entre balises "
    f"<{SESSION_STATE_TAG}> : un objet JSON tenu à jour par l'application et les outils. "
    f"Seul le dernier bloc <{SESSION_STATE_TAG}> de la requête fait foi ; une clé absente "
    "n'a pas de valeur. Clés :"
)


@dataclass(frozen=True, slots=True)
class StateSourceRule:
    """Ce que pose un résultat réussi de l'outil : ``path`` dans ses données, ou ``value``."""

    path: str | None = None
    value: JsonValue = None


@dataclass(frozen=True, slots=True, kw_only=True)
class StateKeyRule:
    """Une clé déclarée : son schéma, ce qu'elle désigne, ses sources par outil, son défaut.

    ``required`` : un run racine ne s'ouvre pas tant qu'elle n'a pas de valeur (D13).
    """

    schema: Mapping[str, JsonValue]
    description: str = ""
    sources: Mapping[str, StateSourceRule] = field(default_factory=dict[str, StateSourceRule])
    required: bool = False
    default: JsonValue = None


class StateRefused(ValueError):
    """State refusé à l'ouverture d'un run (D6, D7, D13) : rien n'est écrit.

    ``problems`` : un motif par ligne, qui nomme la clé.
    """

    def __init__(self, problems: Sequence[str]) -> None:
        self.problems = tuple(problems)
        super().__init__(f"State refusé : {' ; '.join(self.problems)}")

    def __reduce__(self) -> tuple[type[StateRefused], tuple[tuple[str, ...]]]:
        # Copie et pickle refont l'exception à partir de ses motifs, pas de son message.
        return type(self), (self.problems,)


class StateRules:
    """Clés du state que déclare un agent, et limites du state de la session (D6, D7)."""

    def __init__(
        self,
        keys: Mapping[str, StateKeyRule] | None = None,
        *,
        max_value_bytes: int = DEFAULT_MAX_VALUE_BYTES,
        max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
    ) -> None:
        self.keys: dict[str, StateKeyRule] = dict(keys or {})
        self.max_value_bytes = max_value_bytes
        self.max_total_bytes = max_total_bytes
        self._validators: dict[str, Validator] = {
            name: bounded_validator(rule.schema) for name, rule in self.keys.items()
        }
        # Sources par outil, dans l'ordre de déclaration des clés.
        self._sources: dict[str, list[tuple[str, StateSourceRule]]] = {}
        for name, rule in self.keys.items():
            for tool, source in rule.sources.items():
                self._sources.setdefault(tool, []).append((name, source))

    def __bool__(self) -> bool:
        return bool(self.keys)

    @property
    def requires(self) -> bool:
        """Vrai si une clé est ``required`` : ouvrir un run demande de relire la session."""
        return any(rule.required for rule in self.keys.values())

    def __repr__(self) -> str:
        return f"StateRules({', '.join(self.keys)})"

    def writes(self, tool: str) -> bool:
        """Vrai si une source ``from`` désigne cet outil."""
        return tool in self._sources

    def visible(self, values: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
        """Ce qu'on montre du state : les clés déclarées, valeurs par défaut comprises.

        Une valeur qui ne tient plus son schéma — config modifiée depuis son
        écriture, autre agent sur la même session — est écartée, avec un
        avertissement : jamais une erreur ; la valeur par défaut de la clé la
        remplace, s'il y en a une. La copie rendue est en lecture seule ; la
        modifier ne touche à rien d'autre.
        """
        shown: dict[str, JsonValue] = {}
        for name, rule in self.keys.items():
            value = values.get(name)
            if value is not None:
                problem = self._problem(name, value)
                if problem is None:
                    shown[name] = value
                    continue
                logger.warning("State : valeur de la clé %s écartée, elle %s", name, problem)
            if rule.default is not None:
                shown[name] = rule.default
        return MappingProxyType(copy.deepcopy(shown))

    def opening(
        self, posed: Mapping[str, JsonValue], session: Mapping[str, JsonValue]
    ) -> list[str]:
        """Ce qui empêche d'ouvrir un run avec ce state posé par l'application ; vide sinon.

        Chaque écriture est contrôlée comme celle d'un outil (D6, D7), ``None``
        effaçant la clé. Puis chaque clé ``required`` doit avoir une valeur dans
        le state qui en résulte, ``session`` étant celui des runs précédents
        (D13) : une valeur gardée qui ne tient plus son schéma ne compte pas.
        """
        problems: list[str] = []
        accepted: dict[str, JsonValue] = {}
        for name, value in posed.items():
            problem = self._problem(name, value)
            if problem is None:
                accepted[name] = value
            else:
                problems.append(f"{name} : {problem}")
        merged = _merged(session, accepted)
        total = state_size(merged)
        if total > self.max_total_bytes and total > state_size(dict(session)):
            problems.append(
                f"le state de la session ferait {total} octets, au-delà de "
                f"sessions.state.max_total_bytes ({self.max_total_bytes})"
            )
        if self.requires:
            shown = self.visible(merged)
            problems += [
                f"{name} : clé obligatoire sans valeur"
                for name, rule in self.keys.items()
                if rule.required and name not in shown
            ]
        return problems

    def shown(self, keys: Sequence[str]) -> tuple[str, ...]:
        """Clés que reçoit l'orchestrateur : celles nommées, ou toutes si aucune ne l'est.

        Dans l'ordre de leur déclaration.
        """
        return tuple(name for name in self.keys if not keys or name in keys)

    def hint(self, keys: Sequence[str]) -> str:
        """Consigne du prompt système de l'orchestrateur : la balise, et ce que désigne chaque clé.

        Elle ne dépend que de la config : le préfixe de la requête, et son cache,
        ne bougent pas quand le state change.
        """
        lines = [STATE_HINT]
        for name in self.shown(keys):
            description = self.keys[name].description
            lines.append(f"- {name} : {description}" if description else f"- {name}")
        return "\n".join(lines)

    def posed(self, tool: str, output: ToolOutput, tally: dict[str, JsonValue]) -> ToolOutput:
        """Le résultat réussi d'un outil, avec les écritures qu'il pose et celles qui sont refusées.

        ``tally`` : le state de la session tel que le lot l'a laissé jusqu'ici,
        pour sa taille ; les écritures posées y sont reportées. Les appels d'un
        lot se comptent ainsi l'un après l'autre, et le lot ne dépasse pas la
        limite à lui tous.
        """
        given: dict[str, JsonValue] = {}
        refused: list[str] = []
        for name, source in self._sources.get(tool, ()):
            if name in output.state:
                # L'outil a écrit lui-même cette clé : sa valeur l'emporte (D12).
                continue
            if source.path is None:
                given[name] = source.value
                continue
            found = pointed(output.data, source.path)
            if isinstance(found, _Absent):
                refused.append(f"{name} non posé : chemin {source.path!r} absent du résultat")
            else:
                given[name] = found
        given |= output.state
        accepted: dict[str, JsonValue] = {}
        for name, value in given.items():
            problem = self._problem(name, value)
            if problem is None:
                accepted[name] = value
            else:
                refused.append(f"{name} non posé : {problem}")
        projected = _merged(tally, accepted)
        total = state_size(projected)
        if total > self.max_total_bytes and total > state_size(tally):
            # Seul ce qui fait grossir le state au-delà de la limite est refusé :
            # un effacement passe toujours, et une session déjà trop grosse (limite
            # abaissée) peut encore remplacer une valeur par une plus petite.
            grown = [name for name, value in accepted.items() if value is not None]
            refused += [
                f"{name} non posé : le state de la session ferait {total} octets, au-delà "
                f"de sessions.state.max_total_bytes ({self.max_total_bytes})"
                for name in grown
            ]
            accepted = {name: value for name, value in accepted.items() if value is None}
            projected = _merged(tally, accepted)
        tally.clear()
        tally.update(projected)
        for line in refused:
            logger.warning("State : outil %s, %s", tool, line)
        return output.model_copy(update={"state": accepted, "state_refused": tuple(refused)})

    def _problem(self, name: str, value: JsonValue) -> str | None:
        """Pourquoi cette valeur ne peut pas être posée sous cette clé, ou None."""
        validator = self._validators.get(name)
        if validator is None:
            return "clé non déclarée dans 'state'"
        if value is None:
            return None
        if not finite_json(value):
            return "contient NaN ou un infini, que le JSON du journal n'écrit pas"
        try:
            errors = sorted(validator.iter_errors(value), key=lambda e: list(e.absolute_path))
        except RegexTimeout as exc:
            return f"son schéma ne s'applique pas ({exc})"
        if errors:
            return f"ne suit pas son schéma — {_said(errors[0])}"
        size = state_size(value)
        if size > self.max_value_bytes:
            return (
                f"{size} octets, au-delà de sessions.state.max_value_bytes ({self.max_value_bytes})"
            )
        return None


def keeps_state(state: RunState) -> bool:
    """Vrai pour un run racine ordinaire : seul lui lit et écrit le state de la session (D3)."""
    return state.parent_run_id is None and state.kind == "normal"


def state_message(values: Mapping[str, JsonValue]) -> Message:
    """Le state que reçoit l'orchestrateur, en message de fin de requête (D4).

    Envoyé à chaque appel, même vide (``{}``) : le dernier message de la requête
    est toujours lui, jamais une demande ou un résultat d'outil qui l'imiterait
    (D4 amendé le 14/10). JSON compact aux clés triées, à tous les niveaux : un
    même state donne un même texte, donc une même requête. La balise est
    échappée dans les valeurs, qui ne peuvent ni fermer le bloc ni en ouvrir un autre.
    """
    return Message.user(tagged(SESSION_STATE_TAG, state_json(dict(values))))


def _merged(
    values: Mapping[str, JsonValue], writes: Mapping[str, JsonValue]
) -> dict[str, JsonValue]:
    """Le state après ces écritures : ``None`` efface la clé (D10)."""
    result = dict(values)
    for name, value in writes.items():
        if value is None:
            result.pop(name, None)
        else:
            result[name] = value
    return result


def _said(error: ValidationError) -> str:
    """Le problème, sans recopier la valeur : elle peut porter un secret."""
    location = ".".join(str(part) for part in error.absolute_path)
    problem = schema_problem(error)
    return f"{location} : {problem}" if location else problem


class _Absent:
    """Sentinelle : ``None`` est une valeur possible, l'absence n'en est pas une."""


_ABSENT: Final = _Absent()


def pointed(data: JsonValue, path: str) -> JsonValue | _Absent:
    """Valeur que désigne un pointeur JSON (RFC 6901) dans les données d'un résultat.

    Un résultat sans données n'a rien à montrer, même pour ``""``. Un indice de
    liste s'écrit en chiffres, sans zéro en tête.
    """
    if data is None:
        return _ABSENT
    current: JsonValue = data
    for raw in path.split("/")[1:]:
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            if token not in current:
                return _ABSENT
            current = current[token]
        elif isinstance(current, list):
            if not (token.isascii() and token.isdigit()) or (len(token) > 1 and token[0] == "0"):
                return _ABSENT
            index = int(token)
            if index >= len(current):
                return _ABSENT
            current = current[index]
        else:
            return _ABSENT
    return current
