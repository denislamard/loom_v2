# SPDX-License-Identifier: Apache-2.0
"""Appel de modèle : définition, retry, délais, fenêtre de contexte, intégration à la boucle."""

import asyncio
import hashlib
import json
from collections.abc import AsyncGenerator, Callable

import pytest
from pydantic import ValidationError

from loom_ia.adapters.stores import InMemoryEventStore
from loom_ia.core.events import ModelResponded, ModelRetried, RunFailed, StepCompleted
from loom_ia.core.model import (
    Message,
    ModelCapabilities,
    ModelChunk,
    ModelRequest,
    ModelResponse,
    ModelSpec,
    PromptCache,
    RetryPolicy,
    RunStatus,
    StreamReset,
    TextDelta,
    message_to_chunks,
)
from loom_ia.core.ports import ModelError
from loom_ia.engine import RunContext, begin_run, drive
from loom_ia.engine.model_call import ModelCall, estimate_tokens

REQUEST = ModelRequest(model_id="m-1", messages=(Message.user("Bonjour"),), max_tokens=100)
ANSWER = Message.assistant("Bonjour à vous")

type Step = Exception | float | Message


class FlakyModel:
    """Chaque appel suit un plan : erreurs avant ou après des morceaux, pauses, réponse."""

    provider = "flaky"

    def __init__(self, *plans: list[Step]) -> None:
        self.plans = list(plans)
        self.calls = 0

    async def aclose(self) -> None:
        pass

    async def stream(self, request: ModelRequest) -> AsyncGenerator[ModelChunk]:
        plan = self.plans[self.calls]
        self.calls += 1
        for step in plan:
            match step:
                case Exception():
                    raise step
                case float():
                    await asyncio.sleep(step)
                case Message():
                    for chunk in message_to_chunks(step):
                        yield chunk
                case _:
                    raise AssertionError(step)


def spec(**options: object) -> ModelSpec:
    return ModelSpec.model_validate({"id": "M", "sdk": "fake", "model": "m-1", **options})


class Recorder:
    def __init__(self) -> None:
        self.sleeps: list[float] = []
        self.chunks: list[ModelChunk] = []

    async def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)

    async def on_chunk(self, chunk: ModelChunk) -> None:
        self.chunks.append(chunk)


def call(
    model: FlakyModel,
    recorder: Recorder,
    jitter: Callable[[], float] = lambda: 1.0,
    **options: object,
) -> ModelCall:
    return ModelCall(
        model,
        spec(**options),
        on_chunk=recorder.on_chunk,
        sleep=recorder.sleep,
        jitter=jitter,
    )


async def outcomes(model_call: ModelCall) -> list[ModelRetried | ModelResponse]:
    return [item async for item in model_call.run(REQUEST)]


def test_model_spec_rules() -> None:
    assert spec().effective_api is None
    assert spec(sdk="openai").effective_api == "chat"
    assert spec(sdk="openai", api="responses").effective_api == "responses"
    with pytest.raises(ValidationError, match="'api' n'existe que pour sdk: openai"):
        spec(sdk="anthropic", api="chat")
    with pytest.raises(ValidationError):
        spec(api_key="secret")


def test_prompt_cache_is_for_anthropic_and_the_hash_ignores_an_absent_schema() -> None:
    assert spec(sdk="anthropic", cache={"system": True}).cache == PromptCache(system=True)
    with pytest.raises(
        ValidationError, match="'cache' pose des points de cache pour sdk: anthropic"
    ):
        spec(sdk="openai", cache={"system": True})
    with pytest.raises(ValidationError):
        spec(sdk="anthropic", cache={"ttl": "2h"})
    # Sans schéma de sortie, l'empreinte est celle d'avant la phase 3.5b.
    before = REQUEST.model_dump(mode="json", exclude={"output_schema"})
    canonical = json.dumps(before, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert REQUEST.request_hash() == hashlib.sha256(canonical.encode()).hexdigest()
    with_schema = REQUEST.model_copy(update={"output_schema": {"type": "object"}})
    assert with_schema.request_hash() != REQUEST.request_hash()


def test_backoff() -> None:
    policy = RetryPolicy(initial_delay=1, multiplier=2, max_delay=5)
    assert [policy.backoff(n, 1.0) for n in (1, 2, 3, 4)] == [1, 2, 4, 5]
    assert policy.backoff(2, 0.0) == 1


async def test_transient_error_is_retried_from_scratch() -> None:
    recorder = Recorder()
    failure = ModelError("overloaded", "surchargé", http_status=529)
    model = FlakyModel([ANSWER, failure], [ANSWER])
    items = await outcomes(call(model, recorder, jitter=lambda: 0.0))

    retried, response = items
    assert isinstance(retried, ModelRetried)
    assert (retried.attempt, retried.error_kind, retried.http_status) == (1, "overloaded", 529)
    assert (retried.model_id, retried.provider, retried.delay_s) == ("m-1", "flaky", 0.5)
    assert isinstance(response, ModelResponse)
    assert response.message == ANSWER
    assert recorder.sleeps == [0.5]
    resets = [c for c in recorder.chunks if isinstance(c, StreamReset)]
    assert resets == [StreamReset(attempt=2)]
    texts = [c.text for c in recorder.chunks if isinstance(c, TextDelta)]
    assert "".join(texts) == ANSWER.text * 2


async def test_no_reset_when_nothing_was_streamed() -> None:
    recorder = Recorder()
    model = FlakyModel([ModelError("transient", "réseau")], [ANSWER])
    await outcomes(call(model, recorder))
    assert not any(isinstance(c, StreamReset) for c in recorder.chunks)


async def test_definitive_errors_are_not_retried() -> None:
    model = FlakyModel([ModelError("auth", "clé refusée", http_status=401)])
    with pytest.raises(ModelError, match="clé refusée") as caught:
        await outcomes(call(model, Recorder()))
    assert caught.value.kind == "auth"
    assert not caught.value.retryable
    assert model.calls == 1


async def test_attempts_are_bounded() -> None:
    recorder = Recorder()
    plans: list[list[Step]] = [[ModelError("transient", f"essai {n}")] for n in (1, 2, 3)]
    model = FlakyModel(*plans)
    run = call(model, recorder, retry={"max_attempts": 3, "initial_delay": 2})
    retried: list[ModelRetried | ModelResponse] = []
    with pytest.raises(ModelError, match="essai 3"):
        async for item in run.run(REQUEST):
            retried.append(item)
    assert [r.attempt for r in retried if isinstance(r, ModelRetried)] == [1, 2]
    assert recorder.sleeps == [2, 4]
    assert model.calls == 3


async def test_retry_after_is_respected() -> None:
    recorder = Recorder()
    model = FlakyModel([ModelError("transient", "429", retry_after=7)], [ANSWER])
    await outcomes(call(model, recorder, retry={"max_delay": 10}))
    assert recorder.sleeps == [7]

    too_long = FlakyModel([ModelError("transient", "429", retry_after=60)], [ANSWER])
    with pytest.raises(ModelError):
        await outcomes(call(too_long, Recorder(), retry={"max_delay": 10}))
    assert too_long.calls == 1


@pytest.mark.parametrize(
    ("plan", "message"),
    [
        ([0.2, ANSWER], "aucun premier morceau en 0.02 s"),
        ([ANSWER, 0.2, ANSWER], "aucun nouveau morceau en 0.03 s"),
    ],
)
async def test_stream_timeouts(plan: list[Step], message: str) -> None:
    model = FlakyModel(plan)
    run = call(
        model,
        Recorder(),
        timeouts={"first_token": 0.02, "idle": 0.03, "total": None},
        retry={"max_attempts": 1},
    )
    with pytest.raises(ModelError, match=message) as caught:
        await outcomes(run)
    assert caught.value.kind == "transient"


async def test_total_timeout() -> None:
    # Chaque morceau arrive à temps, mais l'ensemble dépasse le délai total.
    slow_parts: list[Step] = []
    for _ in range(10):
        slow_parts += [0.01, ANSWER]
    run = call(
        FlakyModel(slow_parts),
        Recorder(),
        timeouts={"first_token": 1, "idle": 1, "total": 0.05},
        retry={"max_attempts": 1},
    )
    with pytest.raises(ModelError, match=r"Délai total dépassé : 0\.05 s"):
        await outcomes(run)


async def test_other_timeout_errors_propagate() -> None:
    run = call(FlakyModel([TimeoutError("interne")]), Recorder(), retry={"max_attempts": 3})
    with pytest.raises(TimeoutError, match="interne"):
        await outcomes(run)


async def test_context_window_is_checked_before_calling() -> None:
    model = FlakyModel([ANSWER])
    estimated = estimate_tokens(REQUEST)
    assert 0 < estimated < 50
    fits = call(model, Recorder(), capabilities=ModelCapabilities(context_window=estimated + 100))
    assert len(await outcomes(fits)) == 1

    tight = call(model, Recorder(), capabilities={"context_window": estimated + 99})
    with pytest.raises(ModelError, match="dépasse la fenêtre de") as caught:
        await outcomes(tight)
    assert caught.value.kind == "context_overflow"
    assert model.calls == 1


async def test_loop_records_retries_and_failures() -> None:
    store = InMemoryEventStore()
    model = FlakyModel(
        [ModelError("transient", "réseau")],
        [ANSWER],
        [ModelError("overloaded", "plein", http_status=529)],
        [ModelError("overloaded", "toujours plein", http_status=529)],
    )
    ctx = RunContext(
        agent="demo",
        store=store,
        model=model,
        model_spec=spec(retry=RetryPolicy(max_attempts=2, initial_delay=0)),
    )

    ok = await drive(ctx, (await begin_run(ctx, "Bonjour")).run_id)
    assert ok.status is RunStatus.COMPLETED
    events = await store.read(ok.context.tenant_id, ok.session_id)
    retried, responded, done = (e.payload for e in events[3:6])
    assert isinstance(retried, ModelRetried) and events[3].status == "warning"
    assert isinstance(responded, ModelResponded) and responded.attempts == 2
    assert isinstance(done, StepCompleted) and done.events_emitted == 2
    assert events[3].span_id == events[2].span_id

    failed = await drive(ctx, (await begin_run(ctx, "Encore")).run_id)
    assert failed.status is RunStatus.FAILED
    assert failed.error == "model.overloaded: toujours plein"
    events = await store.read(failed.context.tenant_id, failed.session_id)
    closing = events[-1].payload
    assert isinstance(closing, RunFailed) and closing.error_type == "model.overloaded"
    assert [e.type for e in events][2:5] == ["step.started", "model.retried", "step.completed"]
    step_done = events[4].payload
    assert isinstance(step_done, StepCompleted) and step_done.events_emitted == 1


def test_model_error_repr() -> None:
    error = ModelError("quota_exhausted", "plus de crédit", http_status=402, retry_after=None)
    assert repr(error) == "ModelError('quota_exhausted', 'plus de crédit', http_status=402)"
    assert str(error) == "plus de crédit"


async def test_loop_fails_when_the_call_gives_no_response(monkeypatch: pytest.MonkeyPatch) -> None:
    async def nothing(self: ModelCall, request: ModelRequest) -> AsyncGenerator[ModelResponse]:
        return
        yield  # pragma: no cover

    monkeypatch.setattr(ModelCall, "run", nothing)
    store = InMemoryEventStore()
    ctx = RunContext(agent="demo", store=store, model=FlakyModel(), model_spec=spec())
    state = await drive(ctx, (await begin_run(ctx, "?")).run_id)
    assert state.error == "RuntimeError: Appel de modèle terminé sans réponse"
