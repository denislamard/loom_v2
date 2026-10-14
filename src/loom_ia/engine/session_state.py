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
"""

import copy
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Final

from jsonschema import ValidationError
from jsonschema.protocols import Validator
from pydantic import JsonValue

from loom_ia.core.bounded_regex import RegexTimeout, bounded_validator
from loom_ia.core.model import ToolOutput, state_size
from loom_ia.core.schema_errors import schema_problem

logger = logging.getLogger(__name__)

# Tailles par défaut (D7), en octets de JSON compact : celles de ``sessions.state``.
DEFAULT_MAX_VALUE_BYTES: Final = 1024
DEFAULT_MAX_TOTAL_BYTES: Final = 4096


@dataclass(frozen=True, slots=True)
class StateSourceRule:
    """Ce que pose un résultat réussi de l'outil : ``path`` dans ses données, ou ``value``."""

    path: str | None = None
    value: JsonValue = None


@dataclass(frozen=True, slots=True, kw_only=True)
class StateKeyRule:
    """Une clé déclarée : son schéma, ce qu'elle désigne, ses sources par outil, son défaut."""

    schema: Mapping[str, JsonValue]
    description: str = ""
    sources: Mapping[str, StateSourceRule] = field(default_factory=dict[str, StateSourceRule])
    default: JsonValue = None


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
