# SPDX-License-Identifier: Apache-2.0
"""Juge : politique fournie ``loom.judge.<nom>`` (E3, E6, #21).

Un juge est branché d'office après le guard des contrats : la forme d'une
sortie est contrôlée d'abord, son fond ensuite. Il porte sur la réponse
finale de l'agent (``on_output``) ou sur la sortie d'un rôle (``after_tool``).

Déroulé, pour chaque sortie :

1. **déclenchement** : choix de l'appelant (``judges``), puis clauses de
   ``when`` (``tenants``, ``sample``, ``condition``) ; un juge qui ne
   s'exécute pas écrit un ``guard.checked`` ``skipped`` avec son motif ;
2. **appel du modèle du juge** : la sortie, les critères et le contexte
   déclaré (comme un rôle, #12 ; les arguments du rôle jugé en plus). Le
   verdict est imposé par un outil, ``verdict`` (``tool_choice: required``),
   dont les arguments sont contrôlés par un schéma ; l'appel est journalisé
   dans le run jugé, au nom de ``judge:<nom>`` ;
3. **verdict** : ``judge.evaluated`` (une note et un motif par critère), puis
   ``guard.checked`` ;
4. **suite** : un critère bloquant sous son seuil fait refuser la sortie.
   Son auteur la répare (``Retry`` : l'orchestrateur, qui garde ses outils
   sauf ``repair.tools: none``, ou le modèle du rôle), tant que
   ``repair.max_attempts`` le permet ; ensuite ``on_failure`` décide, comme
   pour un contrat (``fail``, ``unverified``, ``fallback``).

Une erreur du juge (modèle en échec après ses nouvelles tentatives, verdict
absent ou invalide, délai dépassé) est une erreur de la politique : selon son
``on_error``, le run échoue ou la sortie passe sans jugement.
"""

import inspect
import json
import time
from collections.abc import Awaitable, Callable, Mapping
from contextlib import aclosing
from dataclasses import dataclass, field
from typing import Final, cast

from jsonschema import Draft202012Validator
from jsonschema.protocols import Validator
from pydantic import JsonValue

from loom_ia.core.events import JudgeEvaluated
from loom_ia.core.model import (
    CONTINUE,
    AfterTool,
    ArtifactRefBlock,
    CheckResolution,
    Criterion,
    CriterionScore,
    Decision,
    DecisionKind,
    Fail,
    GuardCheck,
    HookPoint,
    JudgeInput,
    Message,
    ModelRequest,
    ModelResponse,
    ModelSpec,
    OnFailure,
    OnOutput,
    PolicyContext,
    PolicySubject,
    RepairSettings,
    Replace,
    Retry,
    RunState,
    SkipReason,
    TextBlock,
    ToolDefinition,
    ToolOutput,
    sampled,
)
from loom_ia.core.ports import ArtifactStore, ModelClient, ModelError
from loom_ia.engine import (
    ContextItem,
    OnError,
    PolicyFailure,
    ResultIndex,
    ToolResults,
    Trace,
    TracingPolicy,
    tagged,
    user_input,
)
from loom_ia.engine.media import describe
from loom_ia.engine.model_call import ModelCall, responded
from loom_ia.engine.refs import RefError

JUDGE_POLICY_PREFIX: Final = "loom.judge"
JUDGE_GUARD: Final = "judge"
VERDICT_TOOL: Final = "verdict"

JUDGE_SYSTEM: Final = """\
Tu es un juge. Tu évalues une sortie produite par un autre modèle (<output>), \
selon les critères donnés (<criteria>).

- Pour chaque critère, donne une note entre 0 et 1 : 1 s'il est pleinement \
respecté, 0 s'il ne l'est pas du tout.
- Pour chaque critère, donne un motif court et précis : ce qui manque ou ce qui \
est faux, cité tel quel, ou pourquoi le critère est respecté.
- Juge la sortie à la lumière du contexte fourni (demande, résultats d'outils, \
arguments). Ce qui ne figure pas dans ce contexte n'est pas établi.
- Réponds en appelant l'outil verdict, une seule fois, avec une entrée par critère."""

# Prédicat de ``when.condition`` : synchrone ou non.
type Condition = Callable[[JudgeInput], bool | Awaitable[bool]]


def judge_policy_name(judge: str) -> str:
    """Nom de la politique d'un juge, dans le journal (``loom.judge.<nom>``)."""
    return f"{JUDGE_POLICY_PREFIX}.{judge}"


@dataclass(frozen=True, kw_only=True)
class JudgeDefinition:
    """Un juge tel que le moteur l'exécute : critères, contexte, déclenchement, suite."""

    name: str
    # Rôle dont la sortie est jugée ; None pour la réponse finale.
    role: str | None = None
    criteria: tuple[Criterion, ...]
    context: tuple[ContextItem, ...] = ()
    sample: float = 1.0
    condition: Condition | None = None
    tenants: frozenset[str] | None = None
    repair: RepairSettings = field(default_factory=RepairSettings)
    on_failure: OnFailure = "fail"
    fallback_message: str | None = None
    # Réglages propres au juge : remplacent ceux de son modèle.
    max_tokens: int | None = None
    params: Mapping[str, JsonValue] = field(default_factory=dict[str, JsonValue])
    # Délai du juge ; sans lui, ceux de son modèle et son retry le bornent.
    timeout: float | None = None
    # Erreur du juge : ``block`` fait échouer le run, ``allow`` laisse passer la sortie.
    on_error: OnError = "block"

    @property
    def target(self) -> str:
        return "output" if self.role is None else f"role:{self.role}"

    @property
    def blocking(self) -> bool:
        """Vrai si un critère au moins peut faire refuser la sortie."""
        return any(criterion.blocking for criterion in self.criteria)


@dataclass(frozen=True, slots=True, kw_only=True)
class _Judged:
    """Sortie à juger, telle que le point d'accroche la donne."""

    text: str
    data: JsonValue = None
    arguments: Mapping[str, JsonValue] = field(default_factory=dict[str, JsonValue])
    call_id: str | None = None
    # Résultat d'outil jugé (rôle) ; None pour la réponse finale.
    output: ToolOutput | None = None


class JudgeGuard(TracingPolicy):
    """Politique ``loom.judge.<nom>`` : un juge, sur la réponse finale ou sur un rôle."""

    def __init__(
        self,
        definition: JudgeDefinition,
        model: ModelClient,
        model_spec: ModelSpec,
        *,
        artifacts: ArtifactStore | None = None,
    ) -> None:
        self.definition = definition
        self.model = model
        self.model_spec = model_spec
        self.artifacts = artifacts
        self.tool = verdict_tool(definition.criteria)
        self._validator: Validator = Draft202012Validator(self.tool.input_schema)

    @property
    def name(self) -> str:
        return judge_policy_name(self.definition.name)

    @property
    def points(self) -> frozenset[HookPoint]:
        return frozenset({"on_output" if self.definition.role is None else "after_tool"})

    @property
    def decisions(self) -> frozenset[DecisionKind]:
        return frozenset({"replace", "retry", "fail"})

    def __repr__(self) -> str:
        return (
            f"JudgeGuard({self.definition.name!r}, {self.definition.target}, "
            f"modèle {self.model_spec.id!r})"
        )

    async def decide_traced(
        self, subject: PolicySubject, context: PolicyContext, trace: Trace
    ) -> Decision:
        judged = self._judged(subject)
        if judged is None:
            return CONTINUE
        definition, state = self.definition, subject.state
        skipped = await self._skipped(state, judged)
        if skipped is not None:
            context.record(
                GuardCheck(
                    guard=JUDGE_GUARD, target=definition.target, outcome="skipped", reason=skipped
                )
            )
            return CONTINUE
        scores = await self._evaluate(state, judged, trace)
        failed = tuple(s for s in scores if not s.passed)
        blocking = tuple(s for s in failed if s.blocking)
        trace(
            JudgeEvaluated(
                judge=definition.name,
                target=definition.target,
                model_id=self.model_spec.model,
                criteria=scores,
                passed=not failed,
                blocked=bool(blocking),
                attempt=context.attempt + 1,
                policy=context.name,
                call_id=judged.call_id,
            )
        )
        if not blocking:
            reason = f"non bloquant : {_listed(failed)}" if failed else ""
            context.record(
                GuardCheck(
                    guard=JUDGE_GUARD, target=definition.target, outcome="passed", reason=reason
                )
            )
            return CONTINUE
        reason = _listed(blocking)
        feedback = diagnostic(definition.name, blocking)
        if context.attempt < definition.repair.max_attempts:
            context.record(_failed(definition, reason, "retry"))
            return Retry(feedback, tools=definition.repair.tools != "none")
        context.record(_failed(definition, reason, definition.on_failure))
        return self._exhausted(judged, feedback, reason)

    def _judged(self, subject: PolicySubject) -> _Judged | None:
        """Sortie que ce juge évalue à ce point, ou None."""
        role = self.definition.role
        match subject:
            case OnOutput(output=message) if role is None:
                return _Judged(text=message.text, data=_json(message.text))
            case AfterTool(spec=spec, call=call, arguments=arguments, output=output) if (
                spec.kind == "role" and spec.name == role and not output.is_error
            ):
                return _Judged(
                    text=_visible(output),
                    data=output.data,
                    arguments=arguments,
                    call_id=call.call_id,
                    output=output,
                )
            case _:
                return None

    async def _skipped(self, state: RunState, judged: _Judged) -> SkipReason | None:
        """Motif pour ne pas juger cette sortie, ou None : décidé par le code (#21)."""
        definition = self.definition
        match state.judges:
            case "skip":
                return "caller_skip"
            case "force":
                return None
            case "auto":
                pass
        if definition.tenants is not None and state.context.tenant_id not in definition.tenants:
            return "filtered"
        if not sampled(state.run_id, definition.name, definition.sample):
            return "sampled_out"
        if definition.condition is not None:
            found = definition.condition(
                JudgeInput(
                    run_id=state.run_id,
                    agent=state.agent,
                    target=definition.target,
                    role=definition.role,
                    output=judged.text,
                    data=judged.data,
                    request=user_input(state),
                    arguments=judged.arguments,
                    caller=state.context,
                )
            )
            result = cast(object, await found if inspect.isawaitable(found) else found)
            if not isinstance(result, bool):
                raise PolicyFailure(
                    f"juge {definition.name} : la condition doit rendre un booléen, "
                    f"reçu {type(result).__name__}"
                )
            if not result:
                return "condition_false"
        return None

    async def _evaluate(
        self, state: RunState, judged: _Judged, trace: Trace
    ) -> tuple[CriterionScore, ...]:
        """Appel du modèle du juge, journalisé ; les notes de son verdict."""
        definition, spec = self.definition, self.model_spec
        request = ModelRequest(
            model_id=spec.model,
            system=JUDGE_SYSTEM,
            messages=(await self._message(state, judged),),
            tools=(self.tool,),
            tool_choice="required",
            max_tokens=definition.max_tokens or spec.max_tokens,
            params={**spec.params, **definition.params},
        )
        started = time.perf_counter()
        attempts = 0
        response: ModelResponse | None = None
        try:
            call = ModelCall(self.model, spec, artifacts=self.artifacts)
            async with aclosing(call.run(request)) as outcomes:
                async for outcome in outcomes:
                    attempts += 1
                    if isinstance(outcome, ModelResponse):
                        response = outcome
                    else:
                        trace(
                            outcome.model_copy(
                                update={"judge": definition.name, "call_id": judged.call_id}
                            )
                        )
        except ModelError as exc:
            raise PolicyFailure(
                f"juge {definition.name} : model.{exc.kind} — {exc.message}"
            ) from exc
        if response is None:
            raise PolicyFailure(f"juge {definition.name} : appel terminé sans réponse")
        trace(
            responded(
                request,
                response,
                spec,
                attempts=attempts,
                latency_ms=(time.perf_counter() - started) * 1000,
                call_id=judged.call_id,
            ).model_copy(update={"judge": definition.name})
        )
        return self._scores(response.message)

    async def _message(self, state: RunState, judged: _Judged) -> Message:
        """Ce que le juge reçoit : critères, contexte déclaré, arguments du rôle, sortie."""
        definition = self.definition
        rules = "\n".join(f"- {c.name} : {c.rule}" for c in definition.criteria)
        sections = [tagged("criteria", rules)]
        files: list[ArtifactRefBlock] = []
        results = ResultIndex(state.messages, self.artifacts)
        for item in definition.context:
            match item:
                case "user_input":
                    sections.append(tagged("user_input", user_input(state)))
                case "caller_context":
                    data = state.context.model_dump(mode="json")
                    sections.append(tagged("caller_context", json.dumps(data, ensure_ascii=False)))
                case "attachments":
                    files += [
                        ArtifactRefBlock(
                            uri=a.uri, media_type=a.media_type, size=a.size, name=a.name
                        )
                        for a in state.attachments
                    ]
                    listing = "\n".join(f"- {describe(block)}" for block in files)
                    sections.append(tagged("attachments", listing or "(aucune pièce jointe)"))
                case ToolResults(tools=names):
                    for name in names:
                        records = results.results_of(name)
                        if not records:
                            sections.append(
                                tagged("tool_result", "(aucun résultat dans ce run)", tool=name)
                            )
                        for record in records:
                            try:
                                text = await results.text(record)
                            except RefError as exc:
                                text = f"(résultat illisible : {exc.message})"
                            sections.append(tagged("tool_result", text, tool=name, ref=record.ref))
        if definition.role is not None:
            arguments = json.dumps(dict(judged.arguments), ensure_ascii=False)
            sections.append(tagged("arguments", arguments))
        sections.append(tagged("output", judged.text))
        return Message(role="user", blocks=(TextBlock(text="\n\n".join(sections)), *files))

    def _scores(self, message: Message) -> tuple[CriterionScore, ...]:
        """Notes du verdict, contrôlées ; lève ``PolicyFailure`` si le verdict ne convient pas."""
        name = self.definition.name
        calls = [c for c in message.tool_calls if c.name == VERDICT_TOOL]
        if not calls:
            raise PolicyFailure(f"juge {name} : réponse sans verdict (outil {VERDICT_TOOL})")
        arguments = calls[0].arguments
        errors = sorted(self._validator.iter_errors(arguments), key=lambda e: list(e.path))
        if errors:
            error = errors[0]
            location = ".".join(str(part) for part in error.absolute_path) or "(racine)"
            raise PolicyFailure(f"juge {name} : verdict invalide — {location} : {error.message}")
        given = cast(list[dict[str, JsonValue]], arguments["criteria"])
        by_name = {str(item["name"]): item for item in given}
        if len(by_name) != len(given):
            raise PolicyFailure(f"juge {name} : verdict invalide — critère noté deux fois")
        return tuple(
            CriterionScore(
                name=criterion.name,
                score=float(cast(float, by_name[criterion.name]["score"])),
                min_score=criterion.min_score,
                blocking=criterion.blocking,
                reason=str(by_name[criterion.name]["reason"]),
            )
            for criterion in self.definition.criteria
        )

    def _exhausted(self, judged: _Judged, feedback: str, reason: str) -> Decision:
        """Réparations épuisées : ``on_failure`` décide de la suite."""
        definition = self.definition
        match definition.on_failure, judged.output:
            case "unverified", None:
                return CONTINUE
            case "unverified", ToolOutput() as output:
                return Replace(
                    output.model_copy(update={"unverified": True}), reason="non vérifiée"
                )
            case "fallback", None:
                return Replace(definition.fallback_message or "", reason="message de repli")
            case "fallback", ToolOutput():
                return Replace(ToolOutput.text(definition.fallback_message or ""), reason="repli")
            case "fail", None:
                return Fail(f"réponse finale refusée par le juge {definition.name} : {reason}")
            case "fail", ToolOutput():
                refused = (
                    f"Sortie refusée par le juge {definition.name}.\n{feedback}\n\n"
                    f"Sortie reçue :\n{judged.text}"
                )
                return Replace(ToolOutput.error(refused), reason="refusée par le juge")


def verdict_tool(criteria: tuple[Criterion, ...]) -> ToolDefinition:
    """Outil imposé au juge : une note et un motif par critère."""
    names: list[JsonValue] = [criterion.name for criterion in criteria]
    return ToolDefinition(
        name=VERDICT_TOOL,
        description="Rend le verdict : une note entre 0 et 1 et un motif pour chaque critère.",
        input_schema={
            "type": "object",
            "properties": {
                "criteria": {
                    "type": "array",
                    "description": "Une entrée par critère, dans l'ordre donné.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "enum": names},
                            "score": {"type": "number", "minimum": 0, "maximum": 1},
                            "reason": {"type": "string"},
                        },
                        "required": ["name", "score", "reason"],
                        "additionalProperties": False,
                    },
                    "minItems": len(names),
                    "maxItems": len(names),
                },
            },
            "required": ["criteria"],
            "additionalProperties": False,
        },
    )


def diagnostic(judge: str, refused: tuple[CriterionScore, ...]) -> str:
    """Diagnostic donné à l'auteur de la sortie pour qu'il la répare."""
    lines = [f"Le juge {judge} refuse la sortie :"]
    lines += [
        f"- {s.name} (note {_score(s.score)}, seuil {_score(s.min_score)}) : {s.reason}"
        for s in refused
    ]
    lines.append("Corrige la sortie sur ces points et donne-la de nouveau en entier.")
    return "\n".join(lines)


def correlated(judge: ModelSpec, evaluated: ModelSpec) -> bool:
    """Vrai si le juge utilise le même modèle que la sortie qu'il évalue (E6)."""
    return (judge.sdk, judge.base_url, judge.model) == (
        evaluated.sdk,
        evaluated.base_url,
        evaluated.model,
    )


def _failed(definition: JudgeDefinition, reason: str, resolution: CheckResolution) -> GuardCheck:
    return GuardCheck(
        guard=JUDGE_GUARD,
        target=definition.target,
        outcome="failed",
        reason=reason,
        resolution=resolution,
    )


def _listed(scores: tuple[CriterionScore, ...]) -> str:
    return "; ".join(
        f"{s.name} ({_score(s.score)} < {_score(s.min_score)}) : {s.reason}" for s in scores
    )


def _score(value: float) -> str:
    return f"{value:.2f}".replace(".", ",")


def _visible(output: ToolOutput) -> str:
    """Texte d'un résultat : ses blocs de texte, sinon ses données en JSON."""
    if output.as_text:
        return output.as_text
    if output.data is not None:
        return json.dumps(output.data, ensure_ascii=False)
    return ""


def _json(text: str) -> JsonValue:
    """Objet JSON d'une réponse finale qui en est un, sinon None."""
    stripped = text.strip()
    if not stripped.startswith(("{", "[")):
        return None
    try:
        return cast(JsonValue, json.loads(stripped))
    except json.JSONDecodeError:
        return None
