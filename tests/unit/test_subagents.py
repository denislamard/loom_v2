# SPDX-License-Identifier: Apache-2.0
"""Sous-agents : run enfant, arbre, consommation, profondeur, reprise, annulation (C5, #4, J2.4)."""

import asyncio
from collections.abc import AsyncIterator, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import yaml

from loom_ia.access.api import Loom
from loom_ia.access.cli import main
from loom_ia.adapters.stores import InMemoryEventStore, JsonlEventStore
from loom_ia.config import ConfigError, load_config
from loom_ia.core.events import (
    Event,
    EventDraft,
    EventQuery,
    ModelResponded,
    RunCompleted,
    RunStarted,
    ToolCalled,
    ToolCompleted,
)
from loom_ia.core.model import (
    DEFAULT_TENANT,
    Message,
    ModelRequest,
    ModelSpec,
    Pricing,
    RetryPolicy,
    RunId,
    RunState,
    RunStatus,
    SessionId,
    TenantId,
    ToolResultBlock,
    Usage,
)
from loom_ia.core.ports import EventStore, ModelError
from loom_ia.core.projections import fold, history
from loom_ia.engine import (
    AGENT_HINT,
    REFS_HINT,
    AgentTool,
    RunContext,
    SubAgentDefinition,
    ToolExecutor,
    begin_run,
    drive,
)
from loom_ia.runtime import build_agent
from loom_ia.testing import ScriptedModel, tool_call_message
from loom_ia.tools import tool

MAIN_USAGE = Usage(input_tokens=1_000, output_tokens=100)
CHILD_USAGE = Usage(input_tokens=200, output_tokens=50)
MAIN_SPEC = ModelSpec(
    id="MAIN",
    sdk="fake",
    model="main-1",
    pricing=Pricing(input=1.0, output=5.0),
    retry=RetryPolicy(initial_delay=0),
)
CHILD_SPEC = ModelSpec(
    id="CHILD",
    sdk="fake",
    model="child-1",
    pricing=Pricing(input=2.0, output=10.0),
    retry=RetryPolicy(initial_delay=0),
)


@tool
def calculer(expr: str) -> str:
    """Évalue une expression arithmétique."""
    return str(eval(expr, {"__builtins__": {}}))


@pytest.fixture(params=["memory", "jsonl"])
async def store(request: pytest.FixtureRequest, tmp_path: Path) -> AsyncIterator[EventStore]:
    instance: EventStore = (
        InMemoryEventStore() if request.param == "memory" else JsonlEventStore(tmp_path)
    )
    yield instance
    await instance.aclose()


def child_context(
    store: EventStore, model: ScriptedModel, *tools: object, name: str = "verificateur"
) -> RunContext:
    return RunContext(
        agent=name,
        store=store,
        model=model,
        model_spec=CHILD_SPEC,
        tools=ToolExecutor([calculer, *tools]),  # pyright: ignore[reportArgumentType]
        system="Tu vérifies.",
    )


def verifier(agents: dict[str, RunContext], *, max_depth: int = 1) -> AgentTool:
    definition = SubAgentDefinition(
        name="verifier",
        agent="verificateur",
        description="Vérifie un calcul.",
        max_depth=max_depth,
    )
    return AgentTool(definition, agents.__getitem__)


def parent_context(store: EventStore, model: ScriptedModel, *tools: object) -> RunContext:
    return RunContext(
        agent="demo",
        store=store,
        model=model,
        model_spec=MAIN_SPEC,
        tools=ToolExecutor(list(tools)),  # pyright: ignore[reportArgumentType]
        system="Tu orchestres.",
    )


async def session(store: EventStore, state: RunState) -> list[Event]:
    return await store.read(DEFAULT_TENANT, state.session_id)


def payloads[P](events: list[Event], kind: type[P], run_id: RunId | None = None) -> list[P]:
    return [
        e.payload
        for e in events
        if isinstance(e.payload, kind) and (run_id is None or e.run_id == run_id)
    ]


# --- Aller-retour ------------------------------------------------------------------


async def test_subagent_round_trip(store: EventStore) -> None:
    main_model = ScriptedModel(
        tool_call_message(("c1", "verifier", {"message": "Vérifie que 121 fois 24 = 2904."})),
        Message.assistant("C'est vérifié : 2 904 heures."),
        usage=MAIN_USAGE,
    )
    child_model = ScriptedModel(
        tool_call_message(("k1", "calculer", {"expr": "121*24"})),
        Message.assistant("Exact : 121 fois 24 = 2904."),
        usage=CHILD_USAGE,
    )
    agents = {"verificateur": child_context(store, child_model)}
    ctx = parent_context(store, main_model, verifier(agents))
    started = await begin_run(ctx, "Combien d'heures ? Fais vérifier.")
    state = await drive(ctx, started.run_id)

    assert state.status is RunStatus.COMPLETED
    assert state.output == Message.assistant("C'est vérifié : 2 904 heures.")
    # Les itérations restent celles de l'orchestrateur ; la consommation de l'enfant s'ajoute.
    assert state.iterations == 2
    assert state.usage == MAIN_USAGE + MAIN_USAGE + CHILD_USAGE + CHILD_USAGE
    assert state.cost_usd == pytest.approx(2 * 0.0015 + 2 * 0.0009)

    events = await session(store, state)
    [called] = payloads(events, ToolCalled, state.run_id)
    child_id = called.child_run_id
    assert child_id is not None and called.tool_kind == "agent"
    child = fold(events, child_id)
    assert (child.parent_run_id, child.parent_call_id, child.depth) == (state.run_id, "c1", 1)
    assert (child.root_run_id, child.session_id, child.agent) == (
        state.run_id,
        state.session_id,
        "verificateur",
    )
    assert child.status is RunStatus.COMPLETED and child.iterations == 2
    # Le span racine de l'enfant est rattaché à l'appel du parent.
    call_event = next(
        e for e in events if isinstance(e.payload, ToolCalled) and e.run_id == state.run_id
    )
    assert child.parent_span_id == call_event.span_id

    # Le parent attend l'enfant : tool.called, tout le run enfant, puis tool.completed.
    order = [(e.run_id == child_id, e.type) for e in events]
    first_child = order.index((True, "run.started"))
    last_child = order.index((True, "run.completed"))
    assert order.index((False, "tool.called")) < first_child
    assert last_child < order.index((False, "tool.completed"))
    [completed] = payloads(events, ToolCompleted, state.run_id)
    assert completed.output.as_text == "Exact : 121 fois 24 = 2904."
    assert (completed.usage, completed.cost_usd) == (
        CHILD_USAGE + CHILD_USAGE,
        pytest.approx(0.0018),
    )

    # L'enfant ne reçoit que le message, sans la conversation du parent.
    assert child_model.requests[0].messages == (Message.user("Vérifie que 121 fois 24 = 2904."),)
    assert child_model.requests[0].system == "Tu vérifies."
    # L'orchestrateur voit l'outil, sa consigne, et les références $ref.
    [definition] = main_model.requests[0].tools
    assert definition.description == f"Vérifie un calcul.\n\n{AGENT_HINT}"
    assert main_model.requests[0].system == f"Tu orchestres.\n\n{REFS_HINT}"
    # L'historique de la session ignore le run enfant.
    assert [m.role for m in history(events)] == ["user", "assistant", "tool", "assistant"]


async def test_failed_child_becomes_an_error_result(store: EventStore) -> None:
    main_model = ScriptedModel(
        tool_call_message(("c1", "verifier", {"message": "Vérifie."})),
        Message.assistant("Tant pis."),
    )
    child_model = ScriptedModel(ModelError("auth", "clé refusée", http_status=401))
    agents = {"verificateur": child_context(store, child_model)}
    ctx = parent_context(store, main_model, verifier(agents))
    state = await drive(ctx, (await begin_run(ctx, "?")).run_id)

    assert state.status is RunStatus.COMPLETED
    result = state.messages[2].blocks[0]
    assert isinstance(result, ToolResultBlock) and result.output.is_error
    assert result.output.as_text == ("Le sous-agent verifier a échoué : model.auth: clé refusée")


async def test_subagents_run_in_parallel_in_one_journal(store: EventStore) -> None:
    def echo(request: ModelRequest) -> Message:
        return Message.assistant(f"vu : {request.messages[0].text}")

    main_model = ScriptedModel(
        tool_call_message(
            ("c1", "verifier", {"message": "premier"}),
            ("c2", "verifier", {"message": "second"}),
        ),
        Message.assistant("Les deux sont vus."),
    )
    agents = {"verificateur": child_context(store, ScriptedModel(echo, echo))}
    ctx = parent_context(store, main_model, verifier(agents))
    state = await drive(ctx, (await begin_run(ctx, "?")).run_id)

    assert state.status is RunStatus.COMPLETED
    events = await session(store, state)
    assert [e.seq for e in events] == list(range(1, len(events) + 1))
    children = [p.child_run_id for p in payloads(events, ToolCalled, state.run_id)]
    assert len(set(children)) == 2
    outputs = {p.call_id: p.output.as_text for p in payloads(events, ToolCompleted, state.run_id)}
    assert outputs == {"c1": "vu : premier", "c2": "vu : second"}
    roots = {e.root_run_id for e in events}
    assert roots == {state.run_id}


# --- Profondeur -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("max_depth", "offered"), [(1, ["calculer"]), (2, ["calculer", "verifier"])]
)
async def test_depth_masks_subagents(max_depth: int, offered: list[str]) -> None:
    store = InMemoryEventStore()
    agents: dict[str, RunContext] = {}
    child_model = ScriptedModel(Message.assistant("Vu."))
    # L'agent vérificateur peut s'appeler lui-même : la profondeur borne la boucle.
    agents["verificateur"] = child_context(
        store, child_model, verifier(agents, max_depth=max_depth)
    )
    main_model = ScriptedModel(
        tool_call_message(("c1", "verifier", {"message": "Vérifie."})), Message.assistant("Ok.")
    )
    ctx = parent_context(store, main_model, verifier(agents, max_depth=max_depth))
    await drive(ctx, (await begin_run(ctx, "?")).run_id)
    assert [t.name for t in child_model.requests[0].tools] == offered


# --- Annulation et reprise ---------------------------------------------------------------


async def test_cancelling_the_parent_cancels_the_child_which_is_then_resumed(
    store: EventStore,
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    runs: list[str] = []

    @tool
    async def attendre() -> str:
        """Attend un signal."""
        runs.append("début")
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            runs.append("annulé")
            raise
        return "fini d'attendre"

    main_model = ScriptedModel(
        tool_call_message(("c1", "verifier", {"message": "Vérifie."})),
        Message.assistant("Vérifié après reprise."),
    )
    child_model = ScriptedModel(
        tool_call_message(("k1", "attendre", {})), Message.assistant("Tout est bon.")
    )
    agents = {"verificateur": child_context(store, child_model, attendre)}
    ctx = parent_context(store, main_model, verifier(agents))
    started = await begin_run(ctx, "?")

    task = asyncio.create_task(drive(ctx, started.run_id))
    async with asyncio.timeout(2):
        await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # L'annulation du parent a atteint l'outil de l'enfant ; rien n'a été clos.
    assert runs == ["début", "annulé"]
    events = await session(store, started)
    assert payloads(events, RunCompleted) == []
    [first_call] = payloads(events, ToolCalled, started.run_id)

    release.set()
    state = await drive(ctx, started.run_id)

    assert state.status is RunStatus.COMPLETED
    assert state.output == Message.assistant("Vérifié après reprise.")
    events = await session(store, state)
    # Le même enfant est repris : un seul run.started enfant, même identifiant.
    children = [
        e.run_id for e in events if isinstance(e.payload, RunStarted) and e.run_id != state.run_id
    ]
    assert children == [first_call.child_run_id]
    calls = payloads(events, ToolCalled, state.run_id)
    assert [(c.resumed, c.child_run_id) for c in calls] == [
        (False, first_call.child_run_id),
        (True, first_call.child_run_id),
    ]
    assert runs == ["début", "annulé", "début"]
    [completed] = payloads(events, ToolCompleted, state.run_id)
    assert completed.output.as_text == "Tout est bon."


class CrashOnToolCompleted:
    """Journal qui « plante » à la première écriture du résultat d'un appel du parent."""

    def __init__(self, inner: EventStore) -> None:
        self.inner = inner
        self.crashed = False

    async def append(
        self, drafts: Sequence[EventDraft], *, expected_seq: int | None
    ) -> list[Event]:
        if not self.crashed and any(
            isinstance(d.payload, ToolCompleted) and d.agent == "demo" for d in drafts
        ):
            self.crashed = True
            raise RuntimeError("plantage simulé")
        return await self.inner.append(drafts, expected_seq=expected_seq)

    async def read(
        self,
        tenant_id: TenantId,
        session_id: SessionId,
        *,
        after_seq: int = 0,
        run_id: RunId | None = None,
    ) -> list[Event]:
        return await self.inner.read(tenant_id, session_id, after_seq=after_seq, run_id=run_id)

    async def query(self, query: EventQuery) -> list[Event]:
        return await self.inner.query(query)

    async def last_seq(self, tenant_id: TenantId, session_id: SessionId) -> int:
        return await self.inner.last_seq(tenant_id, session_id)

    async def aclose(self) -> None:
        await self.inner.aclose()


async def test_finished_child_is_not_run_again(store: EventStore) -> None:
    main_model = ScriptedModel(
        tool_call_message(("c1", "verifier", {"message": "Vérifie."})),
        Message.assistant("Fini."),
        usage=MAIN_USAGE,
    )
    child_model = ScriptedModel(Message.assistant("Bon."), usage=CHILD_USAGE)
    agents = {"verificateur": child_context(store, child_model)}
    ctx = parent_context(store, main_model, verifier(agents))
    started = await begin_run(ctx, "?")
    # Le parent tombe juste après la fin de l'enfant, avant d'écrire le résultat.
    with pytest.raises(RuntimeError, match="plantage simulé"):
        await drive(replace(ctx, store=CrashOnToolCompleted(store)), started.run_id)

    state = await drive(ctx, started.run_id)
    assert state.output == Message.assistant("Fini.")
    # L'enfant, déjà fini, n'est pas rappelé : sa réponse et sa consommation reviennent.
    assert len(child_model.requests) == 1
    events = await session(store, state)
    [completed] = payloads(events, ToolCompleted, state.run_id)
    assert (completed.output.as_text, completed.usage) == ("Bon.", CHILD_USAGE)
    assert state.usage == MAIN_USAGE + MAIN_USAGE + CHILD_USAGE


# --- Config, Loom et CLI -----------------------------------------------------------------


def write(tmp_path: Path, **changes: Any) -> Path:
    config: dict[str, Any] = {
        "version": 1,
        "models": [
            {
                "id": "MAIN",
                "sdk": "fake",
                "model": "main-1",
                "params": {
                    "script": [
                        {
                            "tool_calls": [
                                {"name": "verifier", "arguments": {"message": "Vérifie 2 + 2."}}
                            ]
                        },
                        {"text": "Vérifié : 4."},
                    ]
                },
            },
            {
                "id": "CHILD",
                "sdk": "fake",
                "model": "child-1",
                "params": {"script": [{"text": "2 + 2 = 4, c'est exact."}]},
            },
        ],
        "storage": {"events": {"backend": "jsonl", "path": "data"}},
        "telemetry": {"logging": {"level": "WARNING"}},
    }
    demo: dict[str, Any] = {
        "name": "demo",
        "main": {"model": "MAIN", "system": "Tu orchestres."},
        "subagents": [{"agent": "verificateur", "name": "verifier"}],
        **changes,
    }
    child: dict[str, Any] = {
        "name": "verificateur",
        "description": "Vérifie un calcul.",
        "expose": {"rest": False, "mcp": False},
        "main": {"model": "CHILD", "system": "Tu vérifies."},
    }
    (tmp_path / "agents").mkdir(exist_ok=True)
    (tmp_path / "loom.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    for agent in (demo, child):
        path = tmp_path / "agents" / f"{agent['name']}.yaml"
        path.write_text(yaml.safe_dump(agent), encoding="utf-8")
    return tmp_path / "loom.yaml"


async def test_loom_runs_subagents(tmp_path: Path) -> None:
    async with Loom(load_config(write(tmp_path))) as loom:
        result = await loom.run("demo", "Combien font 2 + 2 ?")
        own = await loom.events(result.run_id, subruns=False)
        both = await loom.events(result.run_id)
        tree = await loom.store.read(DEFAULT_TENANT, result.session_id)

    assert (result.text, result.status) == ("Vérifié : 4.", RunStatus.COMPLETED)
    # events(run_id) donne l'arbre (ici tout le journal de la session) ; subruns=False, la racine.
    assert {e.run_id for e in own} == {result.run_id}
    assert both == tree
    runs = {e.run_id: e.agent or "" for e in tree}
    assert sorted(runs.values()) == ["demo", "verificateur"]
    assert {e.root_run_id for e in tree} == {result.run_id}
    responded = [e.payload for e in tree if isinstance(e.payload, ModelResponded)]
    assert [p.model_id for p in responded] == ["main-1", "child-1", "main-1"]


async def test_standalone_agents_mount_their_subagents(tmp_path: Path) -> None:
    config = load_config(write(tmp_path))
    store = InMemoryEventStore()
    agent = build_agent(config, "demo", store)
    started = await begin_run(agent.context, "?")
    state = await drive(agent.context, started.run_id)
    assert state.output == Message.assistant("Vérifié : 4.")
    await agent.aclose()


def test_cli_lists_subagents(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = str(write(tmp_path))
    assert main(["--config", path, "validate"]) == 0
    assert "demo : modèle MAIN, 0 outil(s) Python, sous-agent verifier (verificateur)" in (
        capsys.readouterr().out
    )
    assert main(["--config", path, "run", "demo", "2 + 2 ?", "--stream"]) == 0
    captured = capsys.readouterr()
    assert "· verifier(message='Vérifie 2 + 2.')" in captured.err
    assert "Vérifié : 4." in captured.out


def test_subagent_needs_a_description(tmp_path: Path) -> None:
    path = write(tmp_path)
    child = tmp_path / "agents" / "verificateur.yaml"
    data = yaml.safe_load(child.read_text(encoding="utf-8"))
    del data["description"]
    child.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(ConfigError, match="sous-agent 'verifier' : description manquante"):
        load_config(path)
