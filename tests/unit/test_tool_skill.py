# SPDX-License-Identifier: Apache-2.0
"""Skill d'un outil : un mode d'emploi donné par la config, ajouté à sa description (2.0.14).

Les outils fournis montés ici (``local_files``, ``current_datetime``) sont du
noyau : ces tests tournent aussi dans le job CI sans extra.
"""

import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import JsonValue, ValidationError

from loom_ia.access.cli import main
from loom_ia.adapters.stores import InMemoryEventStore
from loom_ia.agents import PythonTool
from loom_ia.config import ConfigError, load_config
from loom_ia.core.model import (
    SKILL_WARN_CHARS,
    TERMINAL_HINT,
    Message,
    ModelRequest,
    RunId,
    SessionId,
    TenantId,
    ToolOutput,
    ToolOverrides,
    ToolSpec,
)
from loom_ia.core.ports import SourceContext, ToolContext
from loom_ia.runtime import build_agent, declared_skills, skill_warnings
from loom_ia.runtime.sources import PackagedSource
from loom_ia.tools import ConfiguredTool, configure, innermost, tool

SKILL = "## Fichiers\n- Un compte rendu par fichier, nommé AAAA-MM-JJ-client.md."
FILES: dict[str, Any] = {
    "python": "loom.local_files",
    "folders": [{"name": "w", "path": "workspace", "rights": ["read", "write"]}],
}
LABEL = r"tools\[loom.local_files\]"


@tool
def lire(cle: str) -> str:
    """Lit une valeur."""
    return cle


def write(
    tmp_path: Path,
    tools: list[dict[str, Any]],
    *,
    inline: bool = False,
    skill: str = f"\n{SKILL}\n\n",
    **root: Any,
) -> Path:
    """Une config avec un agent : dans agents/ par défaut, dans loom.yaml avec ``inline``."""
    for folder in ("agents", "prompts", "workspace"):
        (tmp_path / folder).mkdir(exist_ok=True)
    (tmp_path / "prompts" / "outil.md").write_text(skill, encoding="utf-8")
    spec = {"name": "agent", "main": {"model": "FAKE", "system": "Tu aides."}, "tools": tools}
    config: dict[str, Any] = {
        "version": 1,
        "models": [{"id": "FAKE", "sdk": "fake", "model": "f"}],
        **root,
    }
    if inline:
        config["agents"] = [spec]
    else:
        (tmp_path / "agents" / "agent.yaml").write_text(yaml.safe_dump(spec), encoding="utf-8")
    (tmp_path / "loom.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    return tmp_path / "loom.yaml"


def rewrite_agent(path: Path, **changes: Any) -> None:
    agent = path.parent / "agents" / "agent.yaml"
    spec = yaml.safe_load(agent.read_text(encoding="utf-8"))
    agent.write_text(yaml.safe_dump({**spec, **changes}), encoding="utf-8")


# --- Déclaration -----------------------------------------------------------------


def test_skill_and_skill_file_exclude_each_other() -> None:
    with pytest.raises(ValidationError, match="'skill' et 'skill_file'"):
        PythonTool(python="x:y", skill="a", skill_file=Path("s.md"))
    with pytest.raises(ValidationError, match="'skill' et 'skill_file'"):
        ToolOverrides(skill="a", skill_file=Path("s.md"))


@pytest.mark.parametrize(
    ("given", "message"),
    [("   \n ", "skill vide"), ("Pour {{ entreprise }}.", "variables {{ }} ne sont pas rendues")],
)
def test_an_inline_skill_is_stripped_and_checked(given: str, message: str) -> None:
    assert PythonTool(python="x:y", skill="  Lis-moi.\n").skill == "Lis-moi."
    assert ToolOverrides(skill="\nLis-moi. ").skill == "Lis-moi."
    for build in (
        lambda: PythonTool(python="x:y", skill=given),
        lambda: ToolOverrides(skill=given),
        lambda: ToolSpec(name="t", description="d", kind="python", skill=given),
    ):
        with pytest.raises(ValidationError, match=message):
            build()
    # model_copy ne valide rien : le contrôle a lieu là où le skill sert.
    with pytest.raises(ValueError, match=message):
        configure(lire, skill=given)
    spec = ToolSpec(name="t", description="d", kind="mcp")
    with pytest.raises(ValueError, match=message):
        spec.overridden(ToolOverrides.model_construct(skill=given))


def test_the_file_is_read_at_load_relative_to_prompts_dir(tmp_path: Path) -> None:
    config = load_config(write(tmp_path, [{**FILES, "skill_file": "outil.md"}]))
    declared = config.agents[0].python_tools[0]
    assert declared.skill == SKILL
    assert declared.skill_file is None


@pytest.mark.parametrize(
    ("name", "content", "message"),
    [
        ("absent.md", None, "skill introuvable"),
        ("prompts/outil.md", None, r"skill introuvable : .* écrire outil\.md"),
        ("vide.md", "  \n", "skill vide"),
        ("variable.md", "Pour {{ entreprise }}.", r"les variables \{\{ \}\} ne sont pas rendues"),
        ("latin1.md", b"caf\xe9", "skill illisible"),
    ],
)
def test_a_bad_skill_file_stops_the_load(
    tmp_path: Path, name: str, content: str | bytes | None, message: str
) -> None:
    path = write(tmp_path, [{**FILES, "skill_file": name}])
    if isinstance(content, str):
        (tmp_path / "prompts" / name).write_text(content, encoding="utf-8")
    elif content is not None:
        (tmp_path / "prompts" / name).write_bytes(content)
    with pytest.raises(ConfigError, match=rf"{LABEL}.skill_file — {message}"):
        load_config(path)


@pytest.mark.parametrize("written", ["../workspace/outil.md", "ABSOLU", "LIEN"])
def test_a_skill_outside_the_prompts_dir_is_refused(tmp_path: Path, written: str) -> None:
    """Un dossier ouvert à local_files laisserait l'agent réécrire son propre skill."""
    (tmp_path / "workspace").mkdir()
    outside = tmp_path / "workspace" / "outil.md"
    outside.write_text(SKILL, encoding="utf-8")
    if written == "ABSOLU":
        written = str(outside)
    elif written == "LIEN":
        (tmp_path / "prompts").mkdir()
        (tmp_path / "prompts" / "lien.md").symlink_to(outside)
        written = "lien.md"
    path = write(tmp_path, [{**FILES, "skill_file": written}])
    with pytest.raises(ConfigError, match="doit être dans le dossier des prompts"):
        load_config(path)


def test_a_server_skill_outside_the_prompts_dir_is_refused(tmp_path: Path) -> None:
    server = {"name": "time", "transport": "stdio", "command": "x"}
    server["tools"] = {"maintenant": {"skill_file": "../workspace/s.md"}}  # type: ignore[assignment]
    path = write(tmp_path, [{"mcp": "time"}], mcp_servers=[server])
    with pytest.raises(
        ConfigError, match=r"mcp_servers\[time\]\.tools\[maintenant\]\.skill_file — le fichier"
    ):
        load_config(path)


@pytest.mark.parametrize("where", ["main", "role", "compaction"])
def test_a_system_file_outside_the_prompts_dir_is_refused(tmp_path: Path, where: str) -> None:
    """Même règle pour le prompt d'un agent, d'un rôle, et pour celui de la compaction."""
    path = write(tmp_path, [FILES])
    (tmp_path / "workspace" / "sys.md").write_text("Tu aides.", encoding="utf-8")
    outside = {"model": "FAKE", "system_file": "../workspace/sys.md"}
    if where == "main":
        rewrite_agent(path, main=outside)
        label = r"main\.system_file"
    elif where == "role":
        schema = {"type": "object", "properties": {"texte": {"type": "string"}}}
        role = {"name": "rediger", "description": "Rédige.", "input_schema": schema}
        rewrite_agent(path, roles=[{**role, **outside}])
        label = r"roles\[rediger\]\.system_file"
    else:
        root = yaml.safe_load(path.read_text(encoding="utf-8"))
        root["sessions"] = {"compaction": outside}
        path.write_text(yaml.safe_dump(root), encoding="utf-8")
        label = r"compaction\.system_file"
    with pytest.raises(ConfigError, match=rf"{label} — le fichier doit être dans"):
        load_config(path)


def test_a_system_file_that_repeats_the_prompts_dir_gets_a_hint(tmp_path: Path) -> None:
    path = write(tmp_path, [FILES])
    (tmp_path / "prompts" / "sys.md").write_text("Tu aides.", encoding="utf-8")
    rewrite_agent(path, main={"model": "FAKE", "system_file": "prompts/sys.md"})
    with pytest.raises(ConfigError, match=r"relatif à prompts_dir : écrire sys\.md"):
        load_config(path)


def test_an_agent_written_in_loom_yaml_follows_the_same_rules(tmp_path: Path) -> None:
    """Prompt, skill et contrat d'un agent de loom.yaml : lus et bornés comme ceux d'agents_dir."""
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "sys.md").write_text("Tu aides.", encoding="utf-8")
    (tmp_path / "contrat.json").write_text('{"type": "object"}', encoding="utf-8")
    path = write(
        tmp_path,
        [{**FILES, "skill_file": "outil.md", "output": {"schema_file": "contrat.json"}}],
        inline=True,
    )
    root = yaml.safe_load(path.read_text(encoding="utf-8"))
    root["agents"][0]["main"] = {"model": "FAKE", "system_file": "sys.md"}
    path.write_text(yaml.safe_dump(root), encoding="utf-8")
    spec = load_config(path).agents[0]
    assert spec.main.system_file == tmp_path / "prompts" / "sys.md"
    declared = spec.python_tools[0]
    assert declared.skill == SKILL
    assert declared.output is not None and declared.output.json_schema == {"type": "object"}

    root["agents"][0]["tools"][0]["skill_file"] = "../workspace/outil.md"
    path.write_text(yaml.safe_dump(root), encoding="utf-8")
    with pytest.raises(ConfigError, match="doit être dans le dossier des prompts"):
        load_config(path)


SQL: dict[str, Any] = {
    "python": "loom.sql",
    "databases": [{"name": "crm", "sqlite": {"path": "crm.db"}, "tables": {"devis": ["select"]}}],
}


@pytest.mark.parametrize("declared", [FILES, {**FILES, "python": "loom.excel"}, SQL])
def test_a_builtin_that_describes_its_params_refuses_description(
    tmp_path: Path, declared: dict[str, Any]
) -> None:
    """Sa description dit les dossiers, droits ou tables : la remplacer les cacherait au modèle."""
    path = write(tmp_path, [{**declared, "description": "Fichiers."}])
    reference = declared["python"]
    with pytest.raises(ConfigError, match=f"{reference} : description ne se règle pas"):
        load_config(path)


# --- Ce que voit le modèle --------------------------------------------------------


def test_the_skill_follows_the_description_and_precedes_the_terminal_hint() -> None:
    spec = ToolSpec(name="t", description="Fait.", kind="python", skill=SKILL, terminal=True)
    assert spec.definition().description == f"Fait.\n\n{SKILL}\n\n{TERMINAL_HINT}"
    assert spec.description == "Fait."  # la description de l'outil reste la sienne


def test_configure_with_a_skill_or_a_description_only_wraps_the_tool() -> None:
    wrapped = configure(lire, skill=f" {SKILL}\n", description="Lit une clé du registre.")
    assert isinstance(wrapped, ConfiguredTool)
    assert wrapped.fixed == frozenset()  # rien de ce que l'outil évalue n'est fixé
    assert wrapped.spec.definition().description == f"Lit une clé du registre.\n\n{SKILL}"
    assert innermost(wrapped) is lire
    assert configure(lire) is lire


def test_a_builtin_tool_keeps_its_behaviour_under_the_skill(tmp_path: Path) -> None:
    config = load_config(write(tmp_path, [{**FILES, "skill_file": "outil.md"}]))
    built = build_agent(config, "agent", InMemoryEventStore())
    files = built.context.tools.get("local_files")
    assert isinstance(files, ConfiguredTool)
    sent = built.context.tools.definitions()[0].description
    assert sent.startswith("Lit, liste, écrit") and sent.endswith(SKILL)
    # L'évaluation par appel (droits par dossier) passe toujours sous l'enveloppe.
    found = files.assess_call({"action": "write", "path": "w/a.md", "content": "x"})
    assert found is not None and found.side_effects == "reversible"


def test_a_python_tool_description_can_be_replaced(tmp_path: Path) -> None:
    path = write(tmp_path, [{"python": "loom.current_datetime", "description": "Donne l'heure."}])
    built = build_agent(load_config(path), "agent", InMemoryEventStore())
    assert built.context.tools.definitions()[0].description == "Donne l'heure."


def test_an_unread_skill_file_on_a_python_tool_stops_the_mount(tmp_path: Path) -> None:
    config = load_config(write(tmp_path, [FILES]))
    spec = config.agents[0]
    declared = spec.python_tools[0].model_copy(update={"skill_file": Path("outil.md")})
    changed = config.model_copy(
        update={"agents": (spec.model_copy(update={"tools": (declared,)}),)}
    )
    with pytest.raises(ConfigError, match=r"skill_file outil\.md non lu"):
        build_agent(changed, "agent", InMemoryEventStore())


# --- Outils MCP et de paquet ------------------------------------------------------


def test_an_override_skill_reaches_the_spec_and_the_agent_one_wins() -> None:
    spec = ToolSpec(name="t", description="Fait.", kind="mcp")
    server, agent = ToolOverrides(skill="serveur"), ToolOverrides(skill="agent")
    assert spec.overridden(server).skill == "serveur"
    assert spec.overridden(server).overridden(agent).skill == "agent"
    # Une description remplacée garde le skill à la suite.
    replaced = spec.overridden(ToolOverrides(description="Autre.", skill="s"))
    assert replaced.definition().description == "Autre.\n\ns"


def test_an_unread_skill_file_is_refused_rather_than_lost() -> None:
    spec = ToolSpec(name="t", description="Fait.", kind="mcp")
    with pytest.raises(ValueError, match=r"skill_file .* non lu"):
        spec.overridden(ToolOverrides(skill_file=Path("s.md")))


TIME = '''
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("time", log_level="WARNING")

@mcp.tool()
def maintenant() -> str:
    """Date et heure actuelles."""
    return "2026-10-27T15:00:00"

mcp.run()
'''


def time_server(tmp_path: Path, **changes: Any) -> dict[str, Any]:
    (tmp_path / "time_srv.py").write_text(TIME, encoding="utf-8")
    return {
        "name": "time",
        "transport": "stdio",
        "command": sys.executable,
        "args": ["time_srv.py"],
        "connect_timeout": 20,
        **changes,
    }


async def test_an_mcp_tool_gets_the_skill_read_from_its_file(tmp_path: Path) -> None:
    pytest.importorskip("mcp", reason="extra 'mcp' absent")
    server = time_server(tmp_path, tools={"maintenant": {"skill_file": "outil.md"}})
    config = load_config(write(tmp_path, [{"mcp": "time"}], mcp_servers=[server]))
    assert config.mcp_servers[0].tools["maintenant"].skill == SKILL
    built = build_agent(config, "agent", InMemoryEventStore())
    context = SourceContext(
        tenant_id=TenantId("default"), session_id=SessionId("s"), run_id=RunId("r"), agent="agent"
    )
    try:
        async with built.context.tools.sources[0].open(context) as tools:
            (found,) = tools
            assert found.spec.definition().description == f"Date et heure actuelles.\n\n{SKILL}"
    finally:
        await built.aclose()


def test_an_unread_skill_file_on_an_mcp_reference_stops_the_mount(tmp_path: Path) -> None:
    """Sans load_config, l'erreur ne viendrait qu'à l'ouverture du serveur, en plein run."""
    config = load_config(write(tmp_path, [{"mcp": "time"}], mcp_servers=[time_server(tmp_path)]))
    spec = config.agents[0]
    ref = spec.mcp_tools[0].model_copy(
        update={"tools": {"maintenant": ToolOverrides.model_construct(skill_file=Path("s.md"))}}
    )
    changed = config.model_copy(update={"agents": (spec.model_copy(update={"tools": (ref,)}),)})
    with pytest.raises(ConfigError, match=r"skill_file non lu — outil time__maintenant \(s\.md\)"):
        build_agent(changed, "agent", InMemoryEventStore())


def test_declared_skills_follow_priority_and_selection(tmp_path: Path) -> None:
    """Un skill par outil exposé : celui de l'agent l'emporte, un outil écarté ne compte pas."""
    server = time_server(
        tmp_path,
        tools={"maintenant": {"skill": "serveur"}, "hier": {"skill": "écarté"}},
    )
    tools = [
        {"mcp": "time", "include": ["maintenant"]},
        {"mcp": "time", "alias": "t", "exclude": ["hier"], "tools": {"maintenant": {"skill": "a"}}},
    ]
    config = load_config(write(tmp_path, tools, mcp_servers=[server]))
    assert declared_skills(config, config.agents[0]) == [
        ("time__maintenant", "serveur"),
        ("t__maintenant", "a"),
    ]


@dataclass
class _Echo:
    spec: ToolSpec

    async def invoke(self, arguments: dict[str, JsonValue], context: ToolContext) -> ToolOutput:
        return ToolOutput.text("ok")


def test_a_packaged_source_tool_gets_its_skill() -> None:
    source = PackagedSource(
        _Echo(ToolSpec(name="unused", description="", kind="python")),  # type: ignore[arg-type]
        name="crm",
        prefix="crm",
        declared={"chercher": ToolOverrides(skill="source")},
        chosen={"chercher": ToolOverrides(skill="agent")},
    )
    (found,) = source.select([_Echo(ToolSpec(name="chercher", description="Cherche.", kind="mcp"))])
    assert found.spec.definition().description == "Cherche.\n\nagent"


# --- Validate, avertissement, rejeu -------------------------------------------------


def test_validate_shows_each_skill_and_its_size(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pytest.importorskip("mcp", reason="extra 'mcp' absent")
    server = time_server(tmp_path, tools={"maintenant": {"skill": "Heure de Paris."}})
    path = write(
        tmp_path, [{**FILES, "skill_file": "outil.md"}, {"mcp": "time"}], mcp_servers=[server]
    )
    assert main(["validate", "--config", str(path), "--no-env-file"]) == 0
    out = capsys.readouterr().out
    size = f"{len(SKILL)} caractères (environ {len(SKILL) // 4} tokens)"
    # Sous l'outil qui le porte, après ses réglages.
    assert f"  │                   read, write\n  │                   skill, {size}\n" in out
    assert "  ├ time__maintenant  skill, 15 caractères (environ 3 tokens)\n" in out


def test_a_long_skill_warns_without_refusing_even_in_prod(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    long = "x" * (SKILL_WARN_CHARS + 1)
    path = write(tmp_path, [{**FILES, "skill_file": "outil.md"}], skill=long, profile="prod")
    config = load_config(path)
    (said,) = skill_warnings(config, config.agents[0])
    assert f"outil 'local_files' : skill de {SKILL_WARN_CHARS + 1} caractères" in said
    with caplog.at_level(logging.WARNING):
        build_agent(config, "agent", InMemoryEventStore())
    assert said in caplog.text


def test_changing_the_skill_changes_only_the_tools_part_of_the_request() -> None:
    def request(skill: str) -> ModelRequest:
        spec = ToolSpec(name="t", description="Fait.", kind="python", skill=skill)
        return ModelRequest(
            model_id="m", system="s", messages=(Message.user("u"),), tools=(spec.definition(),)
        )

    before, after = request("v1").request_parts(), request("v2").request_parts()
    assert [part for part in before if before[part] != after[part]] == ["tools"]
