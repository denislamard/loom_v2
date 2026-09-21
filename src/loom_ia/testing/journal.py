# SPDX-License-Identifier: Apache-2.0
"""Fabrique de journaux de runs fictifs, pour les tests et les exemples (O2).

Les événements produits suivent la machine à états (#3) : étapes,
transitions et clôture sont écrits comme le moteur le fera.
"""

from collections.abc import Mapping
from typing import Self

from pydantic import JsonValue

from loom_ia.core.events import (
    Effect,
    EventDraft,
    ModelResponded,
    RunCompleted,
    RunFailed,
    RunScope,
    RunStarted,
    RunTransitioned,
    StepCompleted,
    StepStarted,
    ToolCalled,
    ToolCompleted,
    UserMessage,
)
from loom_ia.core.model import (
    DEFAULT_TENANT,
    CallerContext,
    EventId,
    Message,
    RunId,
    RunStatus,
    SessionId,
    TenantId,
    TextBlock,
    ToolCallBlock,
    ToolOutput,
    Usage,
    new_run_id,
)


def tool_call_message(
    *calls: tuple[str, str, dict[str, JsonValue]], text: str | None = None
) -> Message:
    """Réponse ``assistant`` qui demande des outils : ``(call_id, nom, arguments)``."""
    blocks: list[TextBlock | ToolCallBlock] = [TextBlock(text=text)] if text else []
    blocks += [ToolCallBlock(call_id=c, name=n, arguments=a) for c, n, a in calls]
    return Message(role="assistant", blocks=tuple(blocks))


class RunJournal:
    """Construit, étape par étape, les événements d'un run."""

    def __init__(
        self,
        *,
        agent: str = "demo",
        tenant_id: TenantId = DEFAULT_TENANT,
        session_id: SessionId | None = None,
        run_id: RunId | None = None,
        root_run_id: RunId | None = None,
        step_ms: float = 1.0,
    ) -> None:
        run_id = run_id or new_run_id()
        self.scope = RunScope(
            tenant_id=tenant_id,
            session_id=session_id or SessionId(run_id),
            run_id=run_id,
            root_run_id=root_run_id or run_id,
            agent=agent,
        )
        self.status = RunStatus.READY_FOR_MODEL
        # Durée écrite dans chaque ``step.completed`` : c'est elle que cumule
        # ``RunState.active_ms``, donc ce que borne le délai d'un run (A6).
        self.step_ms = step_ms
        self.step = 0
        self.iterations = 0
        self.usage = Usage()
        self.cost_usd = 0.0
        self._pending: dict[str, str] = {}
        self._last_answer: Message | None = None
        self._drafts: list[EventDraft] = []

    @property
    def run_id(self) -> RunId:
        return self.scope.run_id

    def take(self) -> list[EventDraft]:
        """Brouillons produits depuis le dernier appel."""
        drafts, self._drafts = self._drafts, []
        return drafts

    # --- Construction ----------------------------------------------------

    def start(
        self,
        prompt: str,
        *,
        context: CallerContext | None = None,
        parent_run_id: RunId | None = None,
        parent_call_id: str | None = None,
        depth: int = 0,
    ) -> Self:
        self._add(
            RunStarted(
                context=context or CallerContext(tenant_id=self.scope.tenant_id),
                parent_run_id=parent_run_id,
                parent_call_id=parent_call_id,
                depth=depth,
            )
        )
        self._add(UserMessage(message=Message.user(prompt)))
        return self

    def model_turn(
        self,
        message: Message,
        *,
        usage: Usage | None = None,
        cost_usd: float = 0.0,
        model_id: str = "fake-model",
    ) -> Self:
        """Appel du modèle ; passe en attente d'outils s'il en demande."""
        usage = usage or Usage()
        self._begin_step("model_call")
        stop = "tool_use" if message.tool_calls else "end"
        self._add(
            ModelResponded(
                model_id=model_id,
                provider="fake",
                message=message,
                usage=usage,
                cost_usd=cost_usd,
                stop_reason=stop,
                request_hash=f"fake-{self.step}",
            )
        )
        self.iterations += 1
        self.usage += usage
        self.cost_usd += cost_usd
        self._pending = {c.call_id: c.name for c in message.tool_calls}
        self._last_answer = None if message.tool_calls else message
        self._end_step()
        if message.tool_calls:
            self.transition(RunStatus.AWAITING_TOOLS, cause="model.responded")
        return self

    def tool_results(self, outputs: Mapping[str, ToolOutput]) -> Self:
        """Exécution du lot d'outils en attente, puis retour au modèle."""
        self._begin_step("tool_batch")
        for call_id in outputs:
            self._add(
                ToolCalled(call_id=call_id, tool_name=self._pending[call_id], tool_kind="python")
            )
        for call_id, output in outputs.items():
            self._add(
                ToolCompleted(
                    call_id=call_id,
                    tool_name=self._pending.pop(call_id),
                    output=output,
                    size=len(output.as_text),
                )
            )
        self._end_step()
        self.transition(RunStatus.READY_FOR_MODEL, cause="tool.completed")
        return self

    def transition(
        self,
        to_state: RunStatus,
        *,
        cause: str | None = None,
        cause_event_id: EventId | None = None,
    ) -> Self:
        self._add(
            RunTransitioned(
                from_state=self.status,
                to_state=to_state,
                step_no=self.step,
                cause_type=cause,
                cause_event_id=cause_event_id,
            )
        )
        self.status = to_state
        return self

    def complete(self) -> Self:
        self.transition(RunStatus.COMPLETED, cause="model.responded")
        self._add(
            RunCompleted(
                output=self._last_answer,
                iterations=self.iterations,
                usage=self.usage,
                cost_usd=self.cost_usd,
            )
        )
        return self

    def fail(self, error_type: str, error: str) -> Self:
        self.transition(RunStatus.FAILED, cause=error_type)
        self._add(
            RunFailed(
                error_type=error_type,
                error=error,
                iterations=self.iterations,
                usage=self.usage,
                cost_usd=self.cost_usd,
            )
        )
        return self

    # --- Interne ---------------------------------------------------------

    def _add(
        self,
        payload: RunStarted
        | UserMessage
        | ModelResponded
        | ToolCalled
        | ToolCompleted
        | StepStarted
        | StepCompleted
        | RunTransitioned
        | RunCompleted
        | RunFailed,
    ) -> None:
        self._drafts.append(self.scope.draft(payload))

    def _begin_step(self, effect: Effect) -> None:
        self.step += 1
        self._add(StepStarted(step_no=self.step, state=self.status, effect=effect))

    def _end_step(self) -> None:
        self._add(StepCompleted(step_no=self.step, duration_ms=self.step_ms))
