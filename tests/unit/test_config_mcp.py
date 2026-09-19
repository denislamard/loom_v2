# SPDX-License-Identifier: Apache-2.0
"""Serveurs MCP dans la config : déclaration, contrôles, montage, run de bout en bout (J2.2)."""

import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from loom_ia.access.api import Loom
from loom_ia.access.cli import main
from loom_ia.adapters.stores import InMemoryEventStore
from loom_ia.agents import AgentSpec, MainRole, McpTools, PythonTool
from loom_ia.config import ConfigError, agent_json_schema, config_json_schema, load_config
from loom_ia.core.events import RunFailed, ToolCalled, ToolCompleted, ToolSourceUnavailable
from loom_ia.core.model import McpServerSpec, RunStatus
from loom_ia.runtime import build_agent, create_mcp_pool

pytest.importorskip("mcp", reason="extra 'mcp' absent")

TIME = '''
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

mcp = FastMCP("time", log_level="WARNING")

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def maintenant(fuseau: str = "Europe/Paris") -> str:
    """Date et heure actuelles."""
    return f"2026-09-19T10:00:00 ({fuseau})"

mcp.run()
'''
MATH = '''
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

mcp = FastMCP("math", log_level="WARNING")

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def calculer(expression: str) -> str:
    """Évalue une expression arithmétique."""
    return str(eval(expression, {"__builtins__": {}}))

mcp.run()
'''
SCRIPT: list[dict[str, Any]] = [
    {
        "tool_calls": [
            {"name": "time__maintenant", "arguments": {}},
            {"name": "m__calculer", "arguments": {"expression": "121 * 24"}},
        ]
    },
    {"text": "Il est 10 h ; 2904 heures."},
]


def servers(**changes: dict[str, Any]) -> list[dict[str, Any]]:
    base: dict[str, dict[str, Any]] = {
        "time": {
            "name": "time",
            "transport": "stdio",
            "command": sys.executable,
            "args": ["serveurs/time_srv.py"],
            "connect_timeout": 20,
        },
        "math": {
            "name": "math",
            "transport": "stdio",
            "command": sys.executable,
            "args": ["math_srv.py"],
            "cwd": "serveurs",
            "scope": "run",
            "connect_timeout": 20,
        },
    }
    for name, fields in changes.items():
        base[name] = {**base[name], **fields}
    return list(base.values())


def agent(**changes: Any) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "name": "assistant",
        "main": {"model": "FAKE", "system": "Tu aides."},
        "tools": [{"mcp": "time"}, {"mcp": "math", "alias": "m", "include": ["calculer"]}],
    }
    return {**spec, **changes}


def write(
    tmp_path: Path,
    spec: dict[str, Any],
    *,
    mcp_servers: list[dict[str, Any]] | None = None,
) -> Path:
    (tmp_path / "agents").mkdir(exist_ok=True)
    (tmp_path / "prompts").mkdir(exist_ok=True)
    (tmp_path / "serveurs").mkdir(exist_ok=True)
    (tmp_path / "serveurs" / "time_srv.py").write_text(TIME, encoding="utf-8")
    (tmp_path / "serveurs" / "math_srv.py").write_text(MATH, encoding="utf-8")
    config = {
        "version": 1,
        "models": [{"id": "FAKE", "sdk": "fake", "model": "f", "params": {"script": SCRIPT}}],
        "mcp_servers": servers() if mcp_servers is None else mcp_servers,
    }
    (tmp_path / "loom.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    (tmp_path / "agents" / "assistant.yaml").write_text(yaml.safe_dump(spec), encoding="utf-8")
    return tmp_path / "loom.yaml"


# --- Déclaration ---------------------------------------------------------------


def test_servers_and_references_are_loaded(tmp_path: Path) -> None:
    config = load_config(write(tmp_path, agent(tools=[{"python": "x:y"}, *agent()["tools"]])))
    time_, math = config.mcp_servers
    # stdio : lancé depuis le dossier de la config, ou depuis son cwd relatif.
    assert (time_.cwd, math.cwd) == (tmp_path, tmp_path / "serveurs")
    assert (time_.scope, math.scope) == ("shared", "run")
    spec = config.agents[0]
    assert isinstance(spec.tools[0], PythonTool)
    assert [ref.prefix for ref in spec.mcp_tools] == ["time", "m"]
    assert isinstance(spec.tools[2], McpTools) and spec.tools[2].include == ("calculer",)
    assert config.mcp_server("math") is math
    # Les schémas pour l'éditeur savent décrire les deux formes de référence.
    assert "McpServerSpec" in str(config_json_schema())
    assert "McpTools" in str(agent_json_schema())


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"transport": "stdio"}, "'command' obligatoire en stdio"),
        ({"transport": "http"}, "'url' obligatoire en http"),
        ({"transport": "stdio", "command": "x", "url": "http://a"}, "url sans effet en stdio"),
        (
            {"transport": "http", "url": "http://a", "command": "x", "env": {"A": "1"}},
            "command, env sans effet en http",
        ),
        (
            {"transport": "stdio", "command": "x", "scope": "tenant"},
            "scope 'tenant' : prévu pour le jalon J5.1",
        ),
        ({"transport": "stdio", "command": "x", "name": "a__b"}, "String should match pattern"),
    ],
)
def test_server_declaration_checks(fields: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        McpServerSpec.model_validate({"name": "s", **fields})


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        (
            agent(tools=[{"mcp": "time", "include": ["a"], "exclude": ["b"]}]),
            "Serveur MCP 'time' : 'include' ou 'exclude', pas les deux",
        ),
        (
            agent(tools=[{"mcp": "time"}, {"mcp": "math", "alias": "time"}]),
            "Préfixe MCP déclaré deux fois : time",
        ),
        (agent(tools=[{"mcp": "crm"}]), "serveur MCP 'crm' non déclaré dans mcp_servers"),
        (agent(tools=[{"mcp": "time", "alias": "t__x"}]), "String should match pattern"),
        (agent(tools=[{"mcp": "time", "offload_over": 10}]), "Extra inputs are not permitted"),
    ],
)
def test_reference_checks(tmp_path: Path, spec: dict[str, Any], message: str) -> None:
    with pytest.raises(ConfigError) as caught:
        load_config(write(tmp_path, spec))
    assert message in str(caught.value)


def test_duplicate_servers(tmp_path: Path) -> None:
    doubled = [*servers(), servers()[0]]
    with pytest.raises(ConfigError, match="Serveur MCP déclaré deux fois : time"):
        load_config(write(tmp_path, agent(), mcp_servers=doubled))


# --- Montage -------------------------------------------------------------------


async def test_mounting_builds_one_source_per_reference(tmp_path: Path) -> None:
    config = load_config(write(tmp_path, agent()))
    built = build_agent(config, "assistant", InMemoryEventStore())
    sources = built.context.tools.sources
    assert [(s.name, s.required) for s in sources] == [("time", False), ("math", False)]
    # Sans pool fourni, l'agent possède le sien pour la portée shared.
    assert len(built.owned) == 1
    await built.aclose()


def test_missing_secret_stops_the_mounting(tmp_path: Path) -> None:
    secret = servers(time={"env_from": {"JETON": "LOOM_ABSENT"}})
    config = load_config(write(tmp_path, agent(), mcp_servers=secret))
    with pytest.raises(ConfigError, match="LOOM_ABSENT est absente ou vide"):
        build_agent(config, "assistant", InMemoryEventStore(), environ={})


def test_role_can_read_results_of_a_prefixed_tool(tmp_path: Path) -> None:
    role = {
        "name": "resumer",
        "description": "Résume l'heure.",
        "model": "FAKE",
        "context": [{"tool_results": ["time__maintenant"]}],
    }
    config = load_config(write(tmp_path, agent(roles=[role])))
    built = build_agent(config, "assistant", InMemoryEventStore())
    assert built.context.tools.get("resumer") is not None

    unknown = {**role, "context": [{"tool_results": ["heure__maintenant"]}]}
    config = load_config(write(tmp_path, agent(roles=[unknown])))
    with pytest.raises(ConfigError, match=r"outils : resumer, time__…, m__…"):
        build_agent(config, "assistant", InMemoryEventStore())


# --- Runs ------------------------------------------------------------------------


async def test_run_with_two_mcp_servers(tmp_path: Path) -> None:
    async with Loom(load_config(write(tmp_path, agent()))) as loom:
        result = await loom.run("assistant", "Quelle heure, et combien font 121 fois 24 ?")
        events = await loom.events(result.run_id)
        # Deuxième run : la connexion partagée à « time » est réutilisée.
        again = await loom.run("assistant", "Encore ?")

    assert result.status is RunStatus.COMPLETED and again.ok
    calls = [e.payload for e in events if isinstance(e.payload, ToolCalled)]
    assert [(c.tool_name, c.tool_kind) for c in calls] == [
        ("time__maintenant", "mcp"),
        ("m__calculer", "mcp"),
    ]
    outputs = {
        e.payload.tool_name: e.payload.output
        for e in events
        if isinstance(e.payload, ToolCompleted)
    }
    assert outputs["time__maintenant"].as_text == "2026-09-19T10:00:00 (Europe/Paris)"
    assert outputs["m__calculer"].as_text == "2904"


async def test_unavailable_server_is_journaled(tmp_path: Path) -> None:
    broken = servers(math={"args": ["absent.py"], "connect_timeout": 5})
    async with Loom(load_config(write(tmp_path, agent(), mcp_servers=broken))) as loom:
        result = await loom.run("assistant", "Quelle heure ?")
        events = await loom.events(result.run_id)
    missing = [e.payload for e in events if isinstance(e.payload, ToolSourceUnavailable)]
    assert [(m.source, m.required) for m in missing] == [("math", False)]
    # Le modèle a demandé m__calculer, absent du run : erreur d'outil inconnu, puis réponse.
    assert result.status is RunStatus.COMPLETED
    unknown = [
        e.payload.output.as_text
        for e in events
        if isinstance(e.payload, ToolCompleted) and e.payload.tool_name == "m__calculer"
    ]
    assert unknown and unknown[0].startswith("Outil inconnu : 'm__calculer'")

    required = agent(tools=[{"mcp": "time"}, {"mcp": "math", "alias": "m", "required": True}])
    async with Loom(load_config(write(tmp_path, required, mcp_servers=broken))) as loom:
        failed = await loom.run("assistant", "Quelle heure ?")
        closing = (await loom.events(failed.run_id))[-1].payload
    assert failed.status is RunStatus.FAILED
    assert isinstance(closing, RunFailed) and closing.error_type == "tool.source_unavailable"


def test_validate_lists_the_mcp_tools(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    broken = servers(math={"args": ["absent.py"], "connect_timeout": 5})
    assert main(["--config", str(write(tmp_path, agent(), mcp_servers=broken)), "validate"]) == 0
    out = capsys.readouterr().out
    assert "assistant : modèle FAKE, 0 outil(s) Python" in out
    assert "    MCP : time__maintenant" in out
    assert "    MCP math indisponible : " in out


def test_stream_shows_an_unavailable_server(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    broken = servers(math={"args": ["absent.py"], "connect_timeout": 5})
    path = str(write(tmp_path, agent(), mcp_servers=broken))
    assert main(["--config", path, "run", "assistant", "Quelle heure ?", "--stream"]) == 0
    assert "· serveur math indisponible : " in capsys.readouterr().err


def test_missing_mcp_extra(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = load_config(write(tmp_path, agent()))
    monkeypatch.setitem(sys.modules, "loom_ia.adapters.mcp", None)
    with pytest.raises(ConfigError, match=r"installer l'extra : loom-ia\[mcp\]"):
        create_mcp_pool(config)
    with pytest.raises(ConfigError, match="l'agent 'assistant' référence des serveurs MCP"):
        build_agent(config, "assistant", InMemoryEventStore())


def test_agents_written_in_python() -> None:
    spec = AgentSpec(
        name="assistant",
        main=MainRole(model="FAKE"),
        tools=(PythonTool(python="x:y"), McpTools(mcp="time", alias="t")),
    )
    assert [type(tool).__name__ for tool in spec.tools] == ["PythonTool", "McpTools"]
    assert spec.mcp_tools[0].owns("t__maintenant") and not spec.mcp_tools[0].owns("time__x")
    with pytest.raises(ValidationError):
        McpServerSpec.model_validate("time")
