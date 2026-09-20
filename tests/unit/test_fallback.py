# SPDX-License-Identifier: Apache-2.0
"""Secours et disjoncteurs : chaîne, adhérence, raisonnement, config et accès (J3.5a)."""

import logging
from collections.abc import AsyncGenerator, Sequence
from contextlib import AbstractAsyncContextManager
from typing import Any

import pytest
from conftest import ANSWER, MODEL, QUESTION, ConfigFactory, demo_agent

from loom_ia.access import Loom
from loom_ia.access.cli import main
from loom_ia.access.progress import Progress
from loom_ia.adapters.stores import InMemoryEventStore
from loom_ia.config import ConfigError, load_config
from loom_ia.core.events import (
    CircuitOpened,
    Event,
    JudgeEvaluated,
    ModelFellBack,
    ModelResponded,
    ModelRetried,
    ToolSourceUnavailable,
)
from loom_ia.core.model import (
    DEFAULT_TENANT,
    MAIN_ROLE,
    CircuitBreaker,
    McpServerSpec,
    Message,
    ModelChunk,
    ModelRequest,
    ModelSpec,
    ReasoningBlock,
    RunId,
    RunStatus,
    SessionId,
    StreamReset,
    TextBlock,
    TextDelta,
)
from loom_ia.core.ports import ModelError, SourceContext, SourceUnavailable, Tool
from loom_ia.core.projections import fold
from loom_ia.engine import (
    Answered,
    CircuitBreakers,
    ModelChain,
    ModelLink,
    RunContext,
    ToolExecutor,
    Tripped,
    begin_run,
    drive,
)
from loom_ia.runtime import build_agent
from loom_ia.testing import ScriptedModel, tool_call_message
from loom_ia.tools import tool

DOWN = ModelError("overloaded", "surchargé", http_status=529)
REQUEST = ModelRequest(model_id="a", messages=(Message.user("Bonjour"),))


class Clock:
    """Horloge des disjoncteurs, avancée à la main."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def spec(model_id: str, **options: Any) -> ModelSpec:
    """Modèle ``A`` → ``a`` ; deux tentatives, sans attente."""
    return ModelSpec.model_validate(
        {
            "id": model_id,
            "sdk": "fake",
            "model": model_id.lower(),
            "retry": {"max_attempts": 2, "initial_delay": 0},
            **options,
        }
    )


A, B = spec("A"), spec("B")


async def no_sleep(delay: float) -> None:
    pass


def chain(
    *links: tuple[ModelSpec, Any],
    breakers: CircuitBreakers | None = None,
    **options: Any,
) -> ModelChain:
    return ModelChain(
        links=[ModelLink(s, m) for s, m in links],
        slot=MAIN_ROLE,
        breakers=breakers,
        sleep=no_sleep,
        **options,
    )


async def collect(
    model_chain: ModelChain, request: ModelRequest = REQUEST, *, current: str | None = None
) -> list[Any]:
    return [item async for item in model_chain.run(request, current=current)]


def answer(text: str) -> Message:
    return Message.assistant(text)


# --- Disjoncteur ---------------------------------------------------------------------------


def test_defaults_and_null_breaker() -> None:
    assert A.circuit_breaker == CircuitBreaker(failures=5, cooldown=60)
    assert spec("X", circuit_breaker=None).circuit_breaker is None
    capabilities = A.capabilities
    assert (capabilities.tools, capabilities.thinking, capabilities.native_json) == (
        True,
        False,
        False,
    )
    server = McpServerSpec.model_validate({"name": "crm", "transport": "stdio", "command": "x"})
    assert server.circuit_breaker == CircuitBreaker()


def test_a_breaker_opens_at_its_threshold_then_tries_again() -> None:
    clock = Clock()
    breakers = CircuitBreakers(clock)
    setting = CircuitBreaker(failures=2, cooldown=10)
    assert breakers.failed("model:A", setting) is None
    assert breakers.remaining("model:A") is None
    assert breakers.failed("model:A", setting) == Tripped(failures=2, cooldown=10.0)
    assert breakers.remaining("model:A") == 10
    # Échec d'un appel parti avant l'ouverture : déjà compté.
    assert breakers.failed("model:A", setting) is None
    clock.now = 10
    assert breakers.remaining("model:A") is None
    # L'essai d'après la pause rate : le disjoncteur se rouvre aussitôt.
    assert breakers.failed("model:A", setting) == Tripped(failures=1, cooldown=10.0)
    clock.now = 25
    assert breakers.remaining("model:A") is None
    breakers.succeeded("model:A")
    assert breakers.failed("model:A", setting) is None
    # Pause finie sans nouvel appel : l'échec suivant est celui de l'essai.
    assert breakers.failed("model:A", setting) == Tripped(failures=2, cooldown=10.0)
    clock.now = 40
    assert breakers.failed("model:A", setting) == Tripped(failures=1, cooldown=10.0)


# --- Chaîne --------------------------------------------------------------------------------


async def test_the_chain_falls_back_after_the_retries() -> None:
    first = ScriptedModel(DOWN, DOWN)
    second = ScriptedModel(answer("Réponse de B"))
    b = spec("B", max_tokens=500, params={"temperature": 0.1})
    earlier = Message(
        role="assistant",
        blocks=(
            ReasoningBlock(text="à moi", model_id="a"),
            ReasoningBlock(text="ancien"),
            TextBlock(text="Salut"),
        ),
    )
    request = ModelRequest(
        model_id="a",
        messages=(Message.user("Bonjour"), earlier, Message.user("Et ensuite ?")),
        max_tokens=100,
        params={"temperature": 0.9},
    )
    retried, fell, answered = await collect(
        chain((A, first), (b, second), params={"top_p": 1}), request
    )

    assert isinstance(retried, ModelRetried) and retried.error_kind == "overloaded"
    assert fell == ModelFellBack(
        slot="main", from_model="A", to_model="B", reason="overloaded", error="surchargé"
    )
    assert fell.event_status == "warning"
    assert isinstance(answered, Answered) and answered.spec is b and answered.attempts == 1
    assert answered.response.message.text == "Réponse de B"
    assert first.requests[0].messages == request.messages
    sent = second.requests[0]
    assert sent == answered.request
    # Réglages du secours, ceux de l'emplacement par-dessus.
    assert (sent.model_id, sent.max_tokens, sent.params) == (
        "b",
        500,
        {"temperature": 0.1, "top_p": 1},
    )
    # Le raisonnement d'un autre modèle est écarté ; celui sans modèle est gardé (#7).
    assert sent.messages[1].blocks == (ReasoningBlock(text="ancien"), TextBlock(text="Salut"))


async def test_quota_falls_back_at_once_and_other_errors_fail() -> None:
    first = ScriptedModel(ModelError("quota_exhausted", "crédit épuisé", http_status=402))
    items = await collect(chain((A, first), (B, ScriptedModel(answer("B")))))
    assert [type(item) for item in items] == [ModelFellBack, Answered]
    assert len(first.requests) == 1

    for kind in ("auth", "invalid_request", "content_filtered"):
        second = ScriptedModel()
        with pytest.raises(ModelError) as caught:
            await collect(chain((A, ScriptedModel(ModelError(kind, "non"))), (B, second)))
        assert caught.value.kind == kind and second.requests == []


async def test_context_overflow_goes_to_a_larger_window() -> None:
    small = spec("A", capabilities={"context_window": 1000})
    smaller = spec("B", capabilities={"context_window": 500})
    larger = spec("C", capabilities={"context_window": 100_000})
    skipped = ScriptedModel()
    items = await collect(
        chain(
            (small, ScriptedModel(ModelError("context_overflow", "trop long"))),
            (smaller, skipped),
            (larger, ScriptedModel(answer("C"))),
        )
    )
    fell = items[0]
    assert isinstance(fell, ModelFellBack)
    assert (fell.from_model, fell.to_model, fell.reason) == ("A", "C", "context_overflow")
    assert skipped.requests == []

    with pytest.raises(ModelError, match="trop long"):
        await collect(
            chain(
                (small, ScriptedModel(ModelError("context_overflow", "trop long"))),
                (smaller, skipped),
            )
        )
    # Sans fenêtre déclarée, pas de comparaison possible : pas de bascule.
    with pytest.raises(ModelError, match="trop long"):
        await collect(
            chain(
                (A, ScriptedModel(ModelError("context_overflow", "trop long"))), (larger, skipped)
            )
        )


async def test_an_open_breaker_skips_its_model() -> None:
    clock = Clock()
    breakers = CircuitBreakers(clock)
    a = spec("A", circuit_breaker={"failures": 1, "cooldown": 30})
    first = ScriptedModel(DOWN, DOWN)
    second = ScriptedModel(answer("1"), answer("2"))

    items = await collect(chain((a, first), (B, second), breakers=breakers))
    assert [type(item) for item in items] == [ModelRetried, CircuitOpened, ModelFellBack, Answered]
    assert items[1] == CircuitOpened(
        target_kind="model", target="A", failures=1, cooldown_s=30, error="surchargé"
    )
    assert breakers.remaining("model:B") is None

    clock.now = 10
    items = await collect(chain((a, first), (B, second), breakers=breakers))
    fell = items[0]
    assert isinstance(fell, ModelFellBack) and fell.reason == "circuit_open"
    assert fell.error == "disjoncteur ouvert, encore 20 s"
    # Écarté : A n'est pas rappelé.
    assert len(first.requests) == 2


async def test_a_chain_all_open_is_unavailable() -> None:
    breakers = CircuitBreakers(Clock())
    for name in ("A", "B"):
        breakers.failed(f"model:{name}", CircuitBreaker(failures=1))
    with pytest.raises(ModelError) as caught:
        await collect(chain((A, ScriptedModel()), (B, ScriptedModel()), breakers=breakers))
    assert caught.value.kind == "unavailable"
    assert "A (encore 60 s), B (encore 60 s)" in caught.value.message
    # Le modèle a échoué, son secours est écarté : la dernière erreur est dite.
    quota = ModelError("quota_exhausted", "crédit épuisé")
    with pytest.raises(ModelError) as caught:
        await collect(
            chain((spec("C"), ScriptedModel(quota)), (B, ScriptedModel()), breakers=breakers)
        )
    assert caught.value.message == (
        "écarté(s) par leur disjoncteur : B (encore 60 s) ; "
        "dernière erreur : model.quota_exhausted — crédit épuisé"
    )
    with pytest.raises(ValueError, match="Chaîne de modèles vide"):
        chain()

    # Sans disjoncteur (circuit_breaker: null), un modèle n'est jamais écarté.
    fresh = CircuitBreakers(Clock())
    unguarded = spec("A", circuit_breaker=None, retry={"max_attempts": 1})
    for _ in range(6):
        items = await collect(
            chain((unguarded, ScriptedModel(DOWN)), (B, ScriptedModel(answer("B"))), breakers=fresh)
        )
        assert [type(item) for item in items] == [ModelFellBack, Answered]
    assert fresh.remaining("model:A") is None


async def test_the_chain_starts_from_the_current_model() -> None:
    first, second = ScriptedModel(), ScriptedModel(answer("B"))
    model_chain = chain((A, first), (B, second))
    assert model_chain.link("B").spec is B
    assert model_chain.position("ABSENT") == 0
    request = REQUEST.model_copy(update={"model_id": "b"})
    [answered] = await collect(model_chain, request, current="B")
    assert isinstance(answered, Answered) and answered.spec is B
    assert first.requests == []


class HalfWay:
    """Diffuse un début de réponse, puis tombe en panne."""

    provider = "fake"

    async def aclose(self) -> None:
        pass

    async def stream(self, request: ModelRequest) -> AsyncGenerator[ModelChunk]:
        yield TextDelta(text="Début")
        raise ModelError("quota_exhausted", "crédit épuisé")


async def test_a_started_stream_is_reset_before_the_fallback() -> None:
    chunks: list[ModelChunk] = []

    async def on_chunk(chunk: ModelChunk) -> None:
        chunks.append(chunk)

    await collect(chain((A, HalfWay()), (B, ScriptedModel(answer("Fin"))), on_chunk=on_chunk))
    assert chunks[:2] == [TextDelta(text="Début"), StreamReset(attempt=2)]
    assert "".join(c.text for c in chunks if isinstance(c, TextDelta)) == "DébutFin"


# --- Boucle --------------------------------------------------------------------------------


@tool
def calculer(expr: str) -> str:
    """Calcule une expression arithmétique."""
    return str(eval(expr))


async def run(ctx: RunContext, prompt: str = "2 + 2 ?") -> tuple[Any, list[Event]]:
    state = await begin_run(ctx, prompt)
    state = await drive(ctx, state.run_id)
    events = await ctx.store.read(state.context.tenant_id, state.session_id)
    return state, events


async def test_a_run_falls_back_and_stays_on_the_fallback() -> None:
    main_model = ScriptedModel(DOWN, DOWN)
    fallback = ScriptedModel(
        tool_call_message(("c1", "calculer", {"expr": "2+2"})), answer("Cela fait 4.")
    )
    b = spec("B", pricing={"input": 1.0, "output": 1.0})
    ctx = RunContext(
        agent="demo",
        store=InMemoryEventStore(),
        model=main_model,
        model_spec=A,
        tools=ToolExecutor([calculer]),
        fallbacks=(ModelLink(b, fallback),),
        breakers=CircuitBreakers(),
    )
    state, events = await run(ctx)

    assert state.status is RunStatus.COMPLETED and state.output.text == "Cela fait 4."
    # Adhérence : la deuxième étape va directement au secours.
    assert state.models == {"main": "B"}
    assert len(main_model.requests) == 2
    [fell] = [e for e in events if isinstance(e.payload, ModelFellBack)]
    assert (fell.role, fell.status) == (MAIN_ROLE, "warning")
    assert fell.facets == {
        "slot": "main",
        "from_model": "A",
        "to_model": "B",
        "reason": "overloaded",
    }
    assert Event.model_validate_json(fell.model_dump_json()) == fell
    responded = [e.payload for e in events if isinstance(e.payload, ModelResponded)]
    assert [r.model_id for r in responded] == ["b", "b"]
    # Le coût suit le tarif du secours.
    assert state.cost_usd > 0
    assert state.cost_usd == pytest.approx(sum(r.cost_usd for r in responded))
    assert fold(events, state.run_id).models == {"main": "B"}


async def test_a_run_fails_when_its_model_is_unavailable() -> None:
    breakers = CircuitBreakers()
    breakers.failed("model:A", CircuitBreaker(failures=1))
    main_model = ScriptedModel()
    ctx = RunContext(
        agent="demo",
        store=InMemoryEventStore(),
        model=main_model,
        model_spec=A,
        breakers=breakers,
    )
    state, _ = await run(ctx)
    assert state.status is RunStatus.FAILED
    assert state.error is not None and state.error.startswith("model.unavailable")
    assert main_model.requests == []


# --- Sources d'outils ----------------------------------------------------------------------


class Source:
    """Source d'outils qui refuse toujours de s'ouvrir."""

    name = "crm"
    required = False

    def __init__(self, error: Exception) -> None:
        self.error = error
        self.calls = 0

    def open(self, context: SourceContext) -> AbstractAsyncContextManager[Sequence[Tool]]:
        self.calls += 1
        raise self.error


SOURCES = SourceContext(
    tenant_id=DEFAULT_TENANT, session_id=SessionId("s"), run_id=RunId("r"), agent="demo"
)


async def opened_events(executor: ToolExecutor) -> list[Any]:
    async with executor.opened(SOURCES) as opened:
        return list(opened.events)


async def test_a_source_breaker_opens_after_failed_connections() -> None:
    source = Source(SourceUnavailable("crm", "connexion refusée"))
    executor = ToolExecutor(
        sources=[source],
        breakers=CircuitBreakers(Clock()),
        circuits={"crm": CircuitBreaker(failures=2, cooldown=30)},
    )
    assert [type(e) for e in await opened_events(executor)] == [ToolSourceUnavailable]
    circuit, missing = await opened_events(executor)
    assert circuit == CircuitOpened(
        target_kind="mcp", target="crm", failures=2, cooldown_s=30, error="connexion refusée"
    )
    assert isinstance(missing, ToolSourceUnavailable)
    [skipped] = await opened_events(executor)
    assert isinstance(skipped, ToolSourceUnavailable)
    assert skipped.error == "disjoncteur ouvert : pas de nouvel essai avant 30 s"
    assert source.calls == 2

    # Un refus sans essai (attente avant de se reconnecter) ne compte pas.
    waiting = Source(SourceUnavailable("crm", "attente", attempted=False))
    executor = ToolExecutor(
        sources=[waiting],
        breakers=CircuitBreakers(Clock()),
        circuits={"crm": CircuitBreaker(failures=1)},
    )
    for _ in range(3):
        assert [type(e) for e in await opened_events(executor)] == [ToolSourceUnavailable]
    assert waiting.calls == 3


async def test_the_run_journals_an_opened_source_breaker() -> None:
    ctx = RunContext(
        agent="demo",
        store=InMemoryEventStore(),
        model=ScriptedModel(answer("Sans le CRM.")),
        model_spec=A,
        tools=ToolExecutor(
            sources=[Source(SourceUnavailable("crm", "connexion refusée"))],
            breakers=CircuitBreakers(),
            circuits={"crm": CircuitBreaker(failures=1)},
        ),
    )
    state, events = await run(ctx)
    assert state.status is RunStatus.COMPLETED
    kinds = [e.type for e in events if e.type in {"circuit.opened", "tool.source_unavailable"}]
    assert kinds == ["circuit.opened", "tool.source_unavailable"]
    [circuit] = [e for e in events if e.type == "circuit.opened"]
    assert (circuit.category, circuit.status) == ("circuit", "warning")


# --- Config et accès -----------------------------------------------------------------------

# Modèle simulé en panne : chaque appel lève une surcharge (script ``error``).
DOWN_MODEL: dict[str, Any] = {
    "id": "DOWN",
    "sdk": "fake",
    "model": "down-1",
    "params": {"script": [{"error": "overloaded"}]},
    "retry": {"max_attempts": 1},
    "circuit_breaker": {"failures": 2, "cooldown": 60},
}


def lines(events: Sequence[Event]) -> list[str]:
    progress = Progress()
    return [line for e in events if (line := progress.line(e)) is not None]


async def test_the_breaker_is_shared_by_the_runs_of_an_instance(demo: ConfigFactory) -> None:
    path = demo(
        models=[MODEL, DOWN_MODEL],
        agents=[
            demo_agent(main={"model": "DOWN", "fallbacks": ["FAKE"], "system": "Tu calcules."})
        ],
    )
    reasons: list[list[str]] = []
    events: list[Event] = []
    async with Loom.from_config(path) as loom:
        for _ in range(3):
            result = await loom.run("demo", QUESTION)
            assert result.text == ANSWER
            events = await loom.events(result.run_id)
            reasons.append(
                [e.type for e in events if e.type in {"model.fell_back", "circuit.opened"}]
            )
        state = await loom.state(events[-1].run_id)
    assert reasons == [
        ["model.fell_back"],
        ["circuit.opened", "model.fell_back"],
        ["model.fell_back"],
    ]
    assert state.models == {"main": "FAKE"}
    shown = lines(events)
    assert shown[0] == ("· secours main : DOWN → FAKE — disjoncteur ouvert, encore 60 s")


async def test_the_progress_lines_of_a_fallback(demo: ConfigFactory) -> None:
    path = demo(
        models=[MODEL, {**DOWN_MODEL, "circuit_breaker": {"failures": 1, "cooldown": 30}}],
        agents=[
            demo_agent(main={"model": "DOWN", "fallbacks": ["FAKE"], "system": "Tu calcules."})
        ],
    )
    async with Loom.from_config(path) as loom:
        result = await loom.run("demo", QUESTION)
        events = await loom.events(result.run_id)
    assert lines(events)[:2] == [
        "· disjoncteur ouvert : modèle DOWN écarté 30 s (échecs de suite : 1)",
        "· secours main : DOWN → FAKE — model.overloaded : Panne simulée par le script du "
        "modèle 'DOWN' (réponse n°1 : overloaded)",
    ]


def test_validate_shows_the_chains(demo: ConfigFactory, capsys: pytest.CaptureFixture[str]) -> None:
    path = demo(
        models=[MODEL, DOWN_MODEL],
        agents=[
            demo_agent(main={"model": "DOWN", "fallbacks": ["FAKE"], "system": "Tu calcules."})
        ],
    )
    assert main(["--config", str(path), "validate"]) == 0
    assert "  demo : modèle DOWN → FAKE, 1 outil(s) Python" in capsys.readouterr().out


ROLE_SCRIPT: list[dict[str, Any]] = [
    {"tool_calls": [{"name": "verifier", "arguments": {"calcul": "12*7+3"}}]},
    {"text": ANSWER},
]


def role(**changes: Any) -> dict[str, Any]:
    return {
        "name": "verifier",
        "description": "Vérifie un calcul.",
        "model": "DOWN",
        "fallbacks": ["ROLE"],
        "system": "Tu vérifies.",
        "input_schema": {"type": "object", "properties": {"calcul": {"type": "string"}}},
        **changes,
    }


async def test_a_role_falls_back_and_its_fallback_repairs(demo: ConfigFactory) -> None:
    role_model = {
        "id": "ROLE",
        "sdk": "fake",
        "model": "role-1",
        "params": {"script": [{"text": "Oui."}, {"text": "Vérifié : 87."}]},
    }
    path = demo(
        models=[{**MODEL, "params": {"script": ROLE_SCRIPT}}, DOWN_MODEL, role_model],
        agents=[
            demo_agent(
                tools=[],
                roles=[role(output={"must_match": "Vérifié", "repair": {"max_attempts": 1}})],
            )
        ],
    )
    async with Loom.from_config(path) as loom:
        result = await loom.run("demo", QUESTION)
        events = await loom.events(result.run_id)
        state = await loom.state(result.run_id)

    assert result.text == ANSWER
    [(fell, payload)] = [(e, e.payload) for e in events if isinstance(e.payload, ModelFellBack)]
    assert fell.role == "verifier" and payload.call_id is not None
    assert (payload.slot, payload.to_model) == ("verifier", "ROLE")
    # La réparation va au modèle qui a répondu, sans nouvelle bascule.
    responded = [e.payload for e in events if isinstance(e.payload, ModelResponded)]
    assert [r.model_id for r in responded if r.call_id] == ["role-1", "role-1"]
    assert state.models == {"verifier": "ROLE"}


async def test_a_judge_falls_back(demo: ConfigFactory) -> None:
    scores = [{"name": "exact", "score": 1.0, "reason": "juste"}]
    judge_model = {
        "id": "JUDGE",
        "sdk": "fake",
        "model": "judge-1",
        "params": {
            "script": [{"tool_calls": [{"name": "verdict", "arguments": {"criteria": scores}}]}]
        },
    }
    judge = {
        "model": "DOWN",
        "fallbacks": ["JUDGE"],
        "criteria": [{"name": "exact", "rule": "Le résultat est juste."}],
    }
    path = demo(models=[MODEL, DOWN_MODEL, judge_model], agents=[demo_agent(judge=judge)])
    async with Loom.from_config(path) as loom:
        result = await loom.run("demo", QUESTION)
        events = await loom.events(result.run_id)
        state = await loom.state(result.run_id)

    assert result.text == ANSWER
    [(fell, payload)] = [(e, e.payload) for e in events if isinstance(e.payload, ModelFellBack)]
    assert fell.role == "judge:output" and payload.judge == "output"
    [evaluated] = [e.payload for e in events if isinstance(e.payload, JudgeEvaluated)]
    assert evaluated.model_id == "judge-1"
    [responded] = [e for e in events if isinstance(e.payload, ModelResponded) and e.payload.judge]
    # Tentatives, bascule et réponse du juge : un seul span.
    assert fell.span_id == responded.span_id
    assert state.models == {"judge:output": "JUDGE"}


NO_TOOLS: dict[str, Any] = {
    "id": "NOTOOLS",
    "sdk": "fake",
    "model": "plain-1",
    "capabilities": {"tools": False},
}
SONNET: dict[str, Any] = {
    "id": "SONNET",
    "sdk": "anthropic",
    "model": "claude-sonnet",
    "params": {"thinking": {"type": "enabled", "budget_tokens": 2048}},
}
CRITERIA: list[dict[str, Any]] = [{"name": "exact", "rule": "Le résultat est juste."}]


@pytest.mark.parametrize(
    ("agent", "message"),
    [
        (
            demo_agent(main={"model": "FAKE", "fallbacks": ["ABSENT"]}),
            "Agent 'demo' : modèle de secours 'ABSENT' non déclaré",
        ),
        (
            demo_agent(main={"model": "FAKE", "fallbacks": ["FAKE"]}),
            "Modèle en double dans la chaîne de secours : FAKE",
        ),
        (
            demo_agent(main={"model": "FAKE", "fallbacks": ["NOTOOLS"]}),
            "il appelle des outils, mais le modèle de secours 'NOTOOLS' ne sait pas le faire",
        ),
        (
            demo_agent(judge={"model": "FAKE", "fallbacks": ["NOTOOLS"], "criteria": CRITERIA}),
            "juge 'output' : il appelle des outils, mais le modèle de secours 'NOTOOLS'",
        ),
        (
            demo_agent(judge={"model": "FAKE", "fallbacks": ["SONNET"], "criteria": CRITERIA}),
            "le modèle 'SONNET' a le raisonnement étendu activé",
        ),
        (
            demo_agent(
                roles=[
                    {
                        "name": "regarder",
                        "description": "Regarde les photos.",
                        "model": "FAKE",
                        "fallbacks": ["NOTOOLS"],
                        "context": ["attachments"],
                    }
                ]
            ),
            "rôle 'regarder' : il reçoit les pièces jointes, mais le modèle 'FAKE'",
        ),
    ],
)
def test_startup_checks_of_the_chains(
    demo: ConfigFactory, agent: dict[str, Any], message: str
) -> None:
    path = demo(models=[MODEL, NO_TOOLS, SONNET], agents=[agent])
    with pytest.raises(ConfigError) as caught:
        load_config(path)
    assert message in str(caught.value)


def test_a_chain_without_tools_is_fine_for_a_main_without_tools(demo: ConfigFactory) -> None:
    path = demo(
        models=[MODEL, NO_TOOLS],
        agents=[demo_agent(tools=[], main={"model": "FAKE", "fallbacks": ["NOTOOLS"]})],
    )
    [agent] = load_config(path).agents
    assert agent.main.chain == ("FAKE", "NOTOOLS")


def test_warnings_when_mounting(demo: ConfigFactory, caplog: pytest.LogCaptureFixture) -> None:
    large = {**MODEL, "capabilities": {"context_window": 200_000}}
    small = {
        "id": "SMALL",
        "sdk": "fake",
        "model": "small-1",
        "capabilities": {"context_window": 32_000},
    }
    judge = {
        "id": "JUDGE",
        "sdk": "fake",
        "model": "judge-1",
        "pricing": {"input": 1.0, "output": 1.0},
    }
    path = demo(
        models=[large, small, judge],
        budgets={"run": {"max_cost": 0.1}},
        agents=[
            demo_agent(
                main={"model": "FAKE", "fallbacks": ["SMALL"], "system": "Tu calcules."},
                judge={"model": "JUDGE", "fallbacks": ["SMALL"], "criteria": CRITERIA},
            )
        ],
    )
    config = load_config(path)
    with caplog.at_level(logging.WARNING, logger="loom_ia.runtime.wiring"):
        build_agent(config, "demo", InMemoryEventStore())
    messages = [r.getMessage() for r in caplog.records]
    assert (
        "Agent 'demo', juge 'output' : même modèle que la sortie qu'il évalue (SMALL) : "
        "juge corrélé" in messages
    )
    assert (
        "Agent 'demo' : budget en dollars, mais sans tarif pour FAKE, SMALL : "
        "leurs appels comptent 0 $" in messages
    )
    assert (
        "Agent 'demo' : le secours SMALL a une fenêtre de contexte plus petite que FAKE "
        "(32000 < 200000 tokens)" in messages
    )
