# SPDX-License-Identifier: Apache-2.0
"""Contrats de sortie : normalisation, contrôle, réparation, échec (E1, E2, E4, E5, #20).

Le contrat est une politique fournie par loom-ia, ``loom.contract``, branchée
en tête des politiques d'un agent : ``on_output`` pour la réponse finale (le
contrat ``output`` de l'agent), ``after_tool`` pour la sortie d'un rôle ou le
résultat d'un outil (le contrat porté par la déclaration de l'outil).

Pour chaque sortie contrôlée, un ``guard.checked`` est journalisé, réussi ou
non, puis la décision :

- **réussi** : ``Continue``, ou ``Replace`` par la sortie normalisée (et, avec
  un schéma, l'objet JSON dans ``data``) ;
- **échoué, réparable** (réponse finale, rôle) : ``Retry`` avec le diagnostic,
  tant que ``repair.max_attempts`` le permet ; la réponse finale est réparée
  par l'orchestrateur (sans outils, sauf ``repair.tools: allowed``), un rôle
  par son propre modèle ;
- **échoué, sans réparation possible** : ``on_failure`` (``fail``,
  ``unverified``, ``fallback``). Pour la réponse finale, ``fail`` fait échouer
  le run ; pour un rôle ou un outil, la sortie revient à l'orchestrateur en
  erreur, avec le diagnostic.

La normalisation est déterministe et ne coûte aucun appel : retrait d'un
bloc de code qui entoure la sortie, extraction du JSON quand un schéma est
attendu (bloc de code, puis première accolade ou crochet jusqu'au dernier),
espaces de début et de fin retirés.
"""

import json
import re
from dataclasses import dataclass
from typing import Final, cast

from jsonschema import Draft202012Validator
from jsonschema.validators import validator_for
from pydantic import JsonValue

from loom_ia.core.model import (
    CONTINUE,
    AfterTool,
    CheckResolution,
    Decision,
    DecisionKind,
    Fail,
    GuardCheck,
    HookPoint,
    JsonBlock,
    Message,
    OnOutput,
    OutputContract,
    PolicyContext,
    PolicySubject,
    Replace,
    Retry,
    TextBlock,
    ToolOutput,
)

CONTRACT_POLICY: Final = "loom.contract"
GUARD: Final = "contract"

_WHOLE_FENCE: Final = re.compile(r"```[\w-]*[ \t]*\n(.*?)\n?[ \t]*```", re.DOTALL)


@dataclass(frozen=True, slots=True)
class Checked:
    """Résultat du contrôle d'une sortie."""

    # Sortie retenue : normalisée si la normalisation l'a changée.
    text: str
    normalized: bool
    problems: tuple[str, ...] = ()
    # Objet JSON validé par le schéma.
    data: JsonValue = None

    @property
    def ok(self) -> bool:
        return not self.problems


def check(contract: OutputContract, text: str, data: JsonValue = None) -> Checked:
    """Contrôle une sortie ; ``data`` : ses données structurées, s'il y en a (outil)."""
    schema = contract.json_schema
    kept = normalize(text, json_expected=schema is not None) if contract.normalize else text
    normalized = kept != text
    problems: list[str] = []
    if not kept.strip() and data is None:
        problems.append("la sortie est vide")
    parsed: JsonValue = None
    if schema is not None:
        if data is not None and not normalized:
            parsed = data
        else:
            try:
                parsed = cast(JsonValue, json.loads(kept))
            except json.JSONDecodeError as exc:
                problems.append(
                    f"ce n'est pas un JSON valide ({exc.msg}, ligne {exc.lineno}, "
                    f"colonne {exc.colno})"
                )
        if parsed is not None or (data is None and not problems):
            validator = validator_for(schema, default=Draft202012Validator)(schema)
            errors = sorted(validator.iter_errors(parsed), key=lambda e: list(e.absolute_path))
            for error in errors:
                location = ".".join(str(part) for part in error.absolute_path) or "(racine)"
                problems.append(f"{location} : {error.message}")
    if contract.must_match is not None and not re.search(contract.must_match, kept):
        problems.append(f"motif attendu absent : {contract.must_match}")
    if contract.must_not_match is not None:
        found = re.search(contract.must_not_match, kept)
        if found is not None:
            problems.append(f"motif interdit présent : {found.group(0)!r}")
    if contract.max_chars is not None and len(kept) > contract.max_chars:
        problems.append(f"{len(kept)} caractères, au-delà de {contract.max_chars}")
    return Checked(
        text=kept,
        normalized=normalized,
        problems=tuple(problems),
        data=parsed if not problems else None,
    )


def normalize(text: str, *, json_expected: bool = False) -> str:
    """Sortie corrigée sans appel au modèle : bloc de code retiré, JSON extrait, espaces."""
    kept = text.strip()
    whole = _WHOLE_FENCE.fullmatch(kept)
    if whole is not None:
        kept = whole.group(1).strip()
    if not json_expected or _parses(kept):
        return kept
    candidates = [m.group(1).strip() for m in _WHOLE_FENCE.finditer(text)]
    candidates.append(_between_brackets(kept))
    for candidate in candidates:
        if candidate and _parses(candidate):
            return candidate
    return kept


def diagnostic(contract: OutputContract, problems: tuple[str, ...]) -> str:
    """Diagnostic donné à l'auteur de la sortie pour qu'il la répare."""
    lines = ["La sortie ne respecte pas son contrat :", *(f"- {p}" for p in problems)]
    if contract.json_schema is not None:
        schema = json.dumps(contract.json_schema, ensure_ascii=False, separators=(",", ":"))
        lines.append(f"Schéma JSON attendu : {schema}")
    lines.append("Réponds de nouveau avec la sortie corrigée, et seulement elle.")
    return "\n".join(lines)


class ContractGuard:
    """Politique ``loom.contract`` : contrats de la réponse finale, des rôles et des outils."""

    def __init__(self, output: OutputContract | None = None) -> None:
        # Contrat de la réponse finale de l'agent ; ceux des outils sont dans leur déclaration.
        self.output = output

    @property
    def name(self) -> str:
        return CONTRACT_POLICY

    @property
    def points(self) -> frozenset[HookPoint]:
        if self.output is None:
            return frozenset({"after_tool"})
        return frozenset({"after_tool", "on_output"})

    @property
    def decisions(self) -> frozenset[DecisionKind]:
        return frozenset({"replace", "retry", "fail"})

    def __repr__(self) -> str:
        return f"ContractGuard(sortie finale : {self.output is not None})"

    async def decide(self, subject: PolicySubject, context: PolicyContext) -> Decision:
        match subject:
            case OnOutput(output=message) if self.output is not None:
                return self._final(self.output, message, context)
            case AfterTool(spec=spec, output=output) if spec.output is not None:
                if output.is_error:
                    return CONTINUE
                target = f"{'role' if spec.kind == 'role' else 'tool'}:{spec.name}"
                return self._result(spec.output, output, target, spec.kind == "role", context)
            case _:
                return CONTINUE

    def _final(
        self, contract: OutputContract, message: Message, context: PolicyContext
    ) -> Decision:
        """Réponse finale : réparée par l'orchestrateur, puis ``on_failure``."""
        checked = check(contract, message.text)
        answer = Message.assistant(checked.text)
        kept: Decision = (
            Replace(answer, reason="sortie normalisée") if checked.normalized else CONTINUE
        )
        if checked.ok:
            context.record(_passed("output", checked))
            return kept
        feedback = diagnostic(contract, checked.problems)
        reason = "; ".join(checked.problems)
        if context.attempt < contract.repair.max_attempts:
            context.record(_failed("output", checked, reason, "retry"))
            return Retry(feedback, tools=contract.repair.keeps_tools)
        match contract.on_failure:
            case "unverified":
                context.record(_failed("output", checked, reason, "unverified"))
                return (
                    Replace(answer, reason="sortie normalisée") if checked.normalized else CONTINUE
                )
            case "fallback":
                context.record(_failed("output", checked, reason, "fallback"))
                return Replace(contract.fallback_message or "", reason="message de repli")
            case "fail":
                context.record(_failed("output", checked, reason, "fail"))
                return Fail(f"réponse finale non conforme à son contrat : {reason}")

    def _result(
        self,
        contract: OutputContract,
        output: ToolOutput,
        target: str,
        repairable: bool,
        context: PolicyContext,
    ) -> Decision:
        """Sortie d'un rôle (réparée par son modèle) ou résultat d'un outil (jamais réparé)."""
        text = _visible(output)
        checked = check(contract, text, output.data)
        if checked.ok:
            context.record(_passed(target, checked))
            if checked.normalized or (checked.data is not None and checked.data != output.data):
                reason = "sortie normalisée" if checked.normalized else "objet JSON retenu"
                return Replace(_structured(output, checked), reason=reason)
            return CONTINUE
        reason = "; ".join(checked.problems)
        feedback = diagnostic(contract, checked.problems)
        if repairable and context.attempt < contract.repair.max_attempts:
            context.record(_failed(target, checked, reason, "retry"))
            return Retry(feedback)
        match contract.on_failure:
            case "unverified":
                context.record(_failed(target, checked, reason, "unverified"))
                return Replace(
                    output.model_copy(update={"unverified": True}), reason="non vérifiée"
                )
            case "fallback":
                context.record(_failed(target, checked, reason, "fallback"))
                return Replace(ToolOutput.text(contract.fallback_message or ""), reason="repli")
            case "fail":
                context.record(_failed(target, checked, reason, "fail"))
                refused = (
                    f"Sortie non conforme à son contrat.\n{feedback}\n\nSortie reçue :\n{text}"
                )
                return Replace(ToolOutput.error(refused), reason="contrat non respecté")


def _structured(output: ToolOutput, checked: Checked) -> ToolOutput:
    """Résultat normalisé : le texte retenu, et l'objet JSON validé s'il y en a un."""
    blocks = (TextBlock(text=checked.text),)
    if checked.data is not None and not output.as_text and output.data is not None:
        # Un outil qui rendait du JSON le garde en JSON.
        return output.model_copy(
            update={"blocks": (JsonBlock(data=checked.data),), "data": checked.data}
        )
    return output.model_copy(update={"blocks": blocks, "data": checked.data})


def _visible(output: ToolOutput) -> str:
    """Texte d'un résultat : ses blocs de texte, sinon ses données en JSON."""
    if output.as_text:
        return output.as_text
    if output.data is not None:
        return json.dumps(output.data, ensure_ascii=False)
    return ""


def _passed(target: str, checked: Checked) -> GuardCheck:
    return GuardCheck(guard=GUARD, target=target, outcome="passed", normalized=checked.normalized)


def _failed(target: str, checked: Checked, reason: str, resolution: CheckResolution) -> GuardCheck:
    return GuardCheck(
        guard=GUARD,
        target=target,
        outcome="failed",
        reason=reason,
        normalized=checked.normalized,
        resolution=resolution,
    )


def _parses(text: str) -> bool:
    try:
        json.loads(text)
    except json.JSONDecodeError:
        return False
    return True


def _between_brackets(text: str) -> str:
    """De la première accolade (ou crochet) à la dernière du même type."""
    starts = [i for i in (text.find("{"), text.find("[")) if i >= 0]
    if not starts:
        return ""
    start = min(starts)
    end = text.rfind("}" if text[start] == "{" else "]")
    return text[start : end + 1] if end > start else ""
