# SPDX-License-Identifier: Apache-2.0
"""Assemblage : de la config aux objets qui tournent, et run de bout en bout."""

import asyncio
import logging
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

from loom_ia.adapters.artifacts import InMemoryArtifactStore
from loom_ia.adapters.stores import InMemoryEventStore, JsonlEventStore
from loom_ia.agents import UnknownAgent
from loom_ia.config import ConfigError, load_config
from loom_ia.core.closing import Closing
from loom_ia.core.model import (
    DEFAULT_TENANT,
    ModelChunk,
    RunId,
    SessionId,
    TextDelta,
    ToolOutput,
)
from loom_ia.core.ports import EventStore, ToolContext
from loom_ia.engine import begin_run, drive
from loom_ia.runtime import apply_logging, build_agent, create_event_store, load_registry
from loom_ia.tools import ConfiguredTool

SCRIPT = [
    {"text": "Je calcule.", "tool_calls": [{"name": "calculer", "arguments": {"expr": "12*7+3"}}]},
    {"text": "Cela fait 87."},
]
MODEL: dict[str, Any] = {
    "id": "FAKE",
    "sdk": "fake",
    "model": "fake-1",
    "params": {"script": SCRIPT},
}
OUTILS = """
from loom_ia.tools import tool


@tool(side_effects="irreversible")
def calculer(expr: str) -> str:
    '''Calcule une expression.'''
    return str(eval(expr))


def doubler(x: int) -> int:
    '''Double un nombre.'''
    return 2 * x


def sans_doc(x: int) -> int:
    return x


valeur = 42
"""


def write(tmp_path: Path, *, root: dict[str, Any], agent: dict[str, Any]) -> Path:
    (tmp_path / "agents").mkdir(exist_ok=True)
    (tmp_path / "prompts").mkdir(exist_ok=True)
    (tmp_path / "prompts" / "demo.md").write_text("Tu calcules.", encoding="utf-8")
    (tmp_path / "outils_wiring.py").write_text(OUTILS, encoding="utf-8")
    config = {"version": 1, "models": [MODEL], "imports": ["outils_wiring"], **root}
    (tmp_path / "loom.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    (tmp_path / "agents" / "demo.yaml").write_text(yaml.safe_dump(agent), encoding="utf-8")
    return tmp_path / "loom.yaml"


def demo_agent(**changes: Any) -> dict[str, Any]:
    agent: dict[str, Any] = {
        "name": "demo",
        "main": {"model": "FAKE", "system_file": "demo.md"},
        "max_iterations": 4,
        "tools": [{"python": "calculer"}],
    }
    return {**agent, **changes}


def test_event_store_follows_the_config(tmp_path: Path) -> None:
    memoire = load_config(write(tmp_path, root={}, agent=demo_agent()))
    assert isinstance(create_event_store(memoire), InMemoryEventStore)

    jsonl = load_config(
        write(
            tmp_path,
            root={"storage": {"events": {"backend": "jsonl", "path": "journaux"}}},
            agent=demo_agent(),
        )
    )
    store = create_event_store(jsonl)
    assert isinstance(store, JsonlEventStore)
    journal = store.path(DEFAULT_TENANT, SessionId("s-1"))
    assert journal.parent.parent == tmp_path / "journaux"


async def test_build_agent_wires_everything(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        root={"execution": {"tools": {"timeout": 7, "validate_arguments": False}}},
        agent=demo_agent(
            tools=[
                {"python": "calculer", "side_effects": "none", "idempotent": True},
                {"python": "outils_wiring:doubler", "timeout": 2},
            ]
        ),
    )
    config = load_config(path)
    store = InMemoryEventStore()
    agent = build_agent(config, "demo", store)

    context = agent.context
    assert agent.spec.name == "demo"
    assert context.system == "Tu calcules."
    assert context.max_iterations == 4
    assert context.model_spec.id == "FAKE"
    assert context.model.provider == "fake"
    assert context.tools.default_timeout == 7
    assert context.tools.validate_arguments is False

    calculer, doubler = context.tools.specs
    # La config remplace ce que l'outil déclare.
    assert (calculer.side_effects, calculer.idempotent) == ("none", True)
    assert calculer.safe_to_retry
    assert isinstance(context.tools.get("calculer"), ConfiguredTool)
    # Une fonction sans décorateur devient un outil.
    assert (doubler.name, doubler.timeout, doubler.kind) == ("doubler", 2, "python")
    assert "x" in str(doubler.input_schema)
    await agent.aclose()


async def test_run_from_a_configuration(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        root={"storage": {"events": {"backend": "jsonl", "path": "journaux"}}},
        agent=demo_agent(),
    )
    config = load_config(path)
    store = create_event_store(config)
    chunks: list[ModelChunk] = []

    async def on_chunk(chunk: ModelChunk) -> None:
        chunks.append(chunk)

    agent = build_agent(config, "demo", store, on_chunk=on_chunk)
    try:
        run = await begin_run(agent.context, "Combien font 12 fois 7, plus 3 ?")
        state = await drive(agent.context, run.run_id)
    finally:
        await agent.aclose()

    assert state.output is not None and state.output.text == "Cela fait 87."
    assert state.iterations == 2
    events = await store.read(DEFAULT_TENANT, run.session_id)
    assert [e.type for e in events if e.category == "tool"] == ["tool.called", "tool.completed"]
    assert "".join(c.text for c in chunks if isinstance(c, TextDelta)).startswith("Je calcule.")
    assert (tmp_path / "journaux").is_dir()
    await store.aclose()


async def test_configured_tool_runs_the_original(tmp_path: Path) -> None:
    config = load_config(
        write(
            tmp_path,
            root={},
            agent=demo_agent(tools=[{"python": "calculer", "timeout": 3}]),
        )
    )
    store: EventStore = InMemoryEventStore()
    agent = build_agent(config, "demo", store)
    tool = agent.context.tools.get("calculer")
    assert isinstance(tool, ConfiguredTool)
    context = ToolContext(
        tenant_id=DEFAULT_TENANT,
        session_id=SessionId("s"),
        run_id=RunId("r"),
        call_id="c1",
        agent="demo",
    )
    assert await tool.invoke({"expr": "12*7+3"}, context) == ToolOutput.text("87")
    await agent.aclose()


async def test_offload_settings_reach_the_executor(tmp_path: Path) -> None:
    root = {"execution": {"tools": {"offload_over": 2000}}}
    agent_spec = demo_agent(tools=[{"python": "calculer", "offload_over": 500}])
    config = load_config(write(tmp_path, root=root, agent=agent_spec))
    files = InMemoryArtifactStore()
    agent = build_agent(config, "demo", InMemoryEventStore(), artifacts=files)
    tools = agent.context.tools
    assert (tools.offload_over, tools.artifacts, agent.context.artifacts) == (2000, files, files)
    calculer = tools.get("calculer")
    assert calculer is not None and calculer.spec.offload_over == 500
    # Avec un stockage, l'outil intégré artifact_read est déclaré (montré après un déport).
    assert [spec.name for spec in tools.specs] == ["calculer", "artifact_read"]
    await agent.aclose()

    bare = build_agent(config, "demo", InMemoryEventStore())
    assert bare.context.artifacts is None
    assert [spec.name for spec in bare.context.tools.specs] == ["calculer"]
    await bare.aclose()


def test_registry_is_reused(tmp_path: Path) -> None:
    config = load_config(write(tmp_path, root={}, agent=demo_agent()))
    registry = load_registry(config)
    assert registry.names == ("calculer",)
    store: EventStore = InMemoryEventStore()
    assert build_agent(config, "demo", store, registry=registry).spec.name == "demo"


def test_wiring_errors(tmp_path: Path) -> None:
    store: EventStore = InMemoryEventStore()
    config = load_config(write(tmp_path, root={}, agent=demo_agent()))
    with pytest.raises(UnknownAgent, match="agents : demo"):
        build_agent(config, "absent", store)

    non_outil = load_config(
        write(tmp_path, root={}, agent=demo_agent(tools=[{"python": "outils_wiring:valeur"}]))
    )
    with pytest.raises(ConfigError, match="ni un outil ni une fonction"):
        build_agent(non_outil, "demo", store)

    sans_docstring = load_config(
        write(tmp_path, root={}, agent=demo_agent(tools=[{"python": "outils_wiring:sans_doc"}]))
    )
    with pytest.raises(ConfigError, match="description manquante"):
        build_agent(sans_docstring, "demo", store)

    inconnu = load_config(write(tmp_path, root={}, agent=demo_agent(tools=[{"python": "absent"}])))
    with pytest.raises(ConfigError, match="Référence 'absent' introuvable"):
        build_agent(inconnu, "demo", store)


def test_logging_follows_the_config(tmp_path: Path) -> None:
    config = load_config(
        write(
            tmp_path,
            root={"telemetry": {"logging": {"level": "debug", "format": "json"}}},
            agent=demo_agent(),
        )
    )
    handler = apply_logging(config)
    try:
        assert logging.getLogger("loom_ia").level == logging.DEBUG
        assert type(handler.formatter).__name__ == "JsonFormatter"
    finally:
        logging.getLogger("loom_ia").removeHandler(handler)
        logging.getLogger("loom_ia").setLevel(logging.NOTSET)


# --- Fermeture : tout est tenté, même si une étape échoue ----------------------


class Resource:
    """Ressource qui note sa fermeture, et lève ``error`` si on le lui demande."""

    def __init__(self, closed: list[str], label: str, error: BaseException | None = None) -> None:
        self.closed, self.label, self.error = closed, label, error

    async def aclose(self) -> None:
        self.closed.append(self.label)
        if self.error is not None:
            raise self.error


async def test_closing_tries_every_step_then_raises_the_first_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Le premier échec est levé, une fois tout fermé ; les autres sont journalisés."""
    closed: list[str] = []
    resources = [
        Resource(closed, "a"),
        Resource(closed, "b", RuntimeError("b ne ferme pas")),
        Resource(closed, "c", ValueError("c non plus")),
        Resource(closed, "d"),
    ]
    closing = Closing()
    for resource in resources:
        with closing:
            await resource.aclose()
    with caplog.at_level(logging.WARNING), pytest.raises(RuntimeError, match="b ne ferme pas"):
        closing.raise_if_failed()

    assert closed == ["a", "b", "c", "d"]
    [warning] = [r for r in caplog.records if r.name == "loom_ia.core.closing"]
    assert warning.exc_info is not None and warning.exc_info[1] is resources[2].error


async def test_closing_without_failure_raises_nothing() -> None:
    closing = Closing()
    with closing:
        await Resource([], "a").aclose()
    closing.raise_if_failed()


async def test_a_cancellation_is_not_swallowed_by_a_later_error() -> None:
    """Annulé en pleine fermeture, l'appelant le reste : une erreur ordinaire ne le masque pas."""
    closed: list[str] = []
    closing = Closing()
    for resource in (
        Resource(closed, "a", RuntimeError("ordinaire")),
        Resource(closed, "b", asyncio.CancelledError()),
        Resource(closed, "c", ValueError("ordinaire aussi")),
    ):
        with closing:
            await resource.aclose()

    with pytest.raises(asyncio.CancelledError):
        closing.raise_if_failed()
    assert closed == ["a", "b", "c"]


async def test_closing_an_agent_closes_every_resource_even_if_one_fails(tmp_path: Path) -> None:
    config = load_config(write(tmp_path, root={}, agent=demo_agent()))
    built = build_agent(config, "demo", InMemoryEventStore())
    closed: list[str] = []
    agent = replace(
        built,
        clients=cast(
            Any, (Resource(closed, "modèle 1", RuntimeError("1")), Resource(closed, "m 2"))
        ),
        owned=(Resource(closed, "pool"),),
    )

    with pytest.raises(RuntimeError, match=r"^1$"):
        await agent.aclose()

    assert closed == ["modèle 1", "m 2", "pool"]
