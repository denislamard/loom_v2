# SPDX-License-Identifier: Apache-2.0
"""Configuration : schéma, lecture YAML, contrôles au démarrage, références Python."""

from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from loom_ia.agents import AgentRegistry, AgentSpec, MainRole, UnknownAgent
from loom_ia.config import (
    ConfigError,
    LoomConfig,
    Registry,
    UnsupportedKey,
    agent_json_schema,
    config_json_schema,
    import_modules,
    load_config,
    resolve,
)
from loom_ia.runtime import system_prompt

MODEL: dict[str, Any] = {"id": "FAKE", "sdk": "fake", "model": "fake-1"}
AGENT: dict[str, Any] = {"name": "demo", "main": {"model": "FAKE", "system": "Tu calcules."}}
OUTILS = """
from loom_ia.tools import tool


@tool
def calculer(expr: str) -> str:
    '''Calcule.'''
    return str(eval(expr))


def brut(x: int) -> int:
    '''Double un nombre.'''
    return 2 * x
"""


def write(
    tmp_path: Path,
    *,
    root: dict[str, Any] | None = None,
    agents: dict[str, dict[str, Any]] | None = None,
    prompts: dict[str, str] | None = None,
    modules: dict[str, str] | None = None,
) -> Path:
    """Écrit une arborescence de config et renvoie le chemin du fichier racine."""
    base = tmp_path
    (base / "agents").mkdir(exist_ok=True)
    (base / "prompts").mkdir(exist_ok=True)
    config = {"version": 1, "models": [MODEL], **(root or {})}
    (base / "loom.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    for name, spec in (agents if agents is not None else {"demo": AGENT}).items():
        (base / "agents" / f"{name}.yaml").write_text(yaml.safe_dump(spec), encoding="utf-8")
    for name, text in (prompts or {}).items():
        (base / "prompts" / name).write_text(text, encoding="utf-8")
    for name, source in (modules or {}).items():
        (base / f"{name}.py").write_text(source, encoding="utf-8")
    return base / "loom.yaml"


def test_load_resolves_paths_and_agents(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        root={
            "agents_dir": "agents/",
            "prompts_dir": "prompts/",
            "storage": {"events": {"backend": "jsonl", "path": "data/events"}},
            "execution": {"tools": {"timeout": 5}},
            "telemetry": {"logging": {"level": "debug"}},
        },
        agents={
            "demo": {**AGENT, "main": {"model": "FAKE", "system_file": "demo.md"}},
            "autre": {"name": "autre", "expose": {"mcp": False}, "main": {"model": "FAKE"}},
        },
        prompts={"demo.md": "Tu calcules."},
    )
    config = load_config(path)

    assert config.base_dir == tmp_path
    assert config.agents_dir == tmp_path / "agents"
    assert config.storage.events.path == tmp_path / "data/events"
    assert config.execution.tools.timeout == 5
    assert config.telemetry.logging.level == "debug"
    assert [agent.name for agent in config.agents] == ["autre", "demo"]
    assert config.agents[1].main.system_file == tmp_path / "prompts" / "demo.md"
    assert config.model_spec("FAKE").sdk == "fake"

    registry = AgentRegistry.from_config(config)
    assert registry.names == ("autre", "demo")
    assert [a.name for a in registry.exposed("mcp")] == ["demo"]
    assert [a.name for a in registry.exposed("rest")] == ["autre", "demo"]
    assert "demo" in registry and len(registry) == 2
    assert registry.get("demo").main.system == ""
    with pytest.raises(UnknownAgent, match="agents : autre, demo"):
        registry.get("absent")


def test_config_can_be_written_in_python() -> None:
    config = LoomConfig(
        version=1,
        models=(LoomConfig.model_validate({"version": 1, "models": [MODEL]}).models[0],),
        agents=(AgentSpec.model_validate(AGENT),),
    )
    assert config.agents[0].main.system == "Tu calcules."
    assert config.storage.events.backend == "memory"


@pytest.mark.parametrize(
    ("root", "agents", "message"),
    [
        ({"version": 2}, None, "Version de config 2 non prise en charge"),
        ({"budgets": {}}, None, "'budgets' : prévu pour le jalon J3"),
        ({"profiles": {}}, None, "'profiles' : prévu pour le jalon J5"),
        ({"storage": {"idempotency": {}}}, None, "'idempotency' : prévu pour le jalon J4"),
        (
            {"storage": {"artifacts": {"backend": "local"}}},
            None,
            "'path' est obligatoire quand le journal n'est pas en fichiers",
        ),
        (
            {"storage": {"artifacts": {"backend": "memory", "path": "a"}}},
            None,
            "'path' n'a pas de sens",
        ),
        (
            {"execution": {"attachments": {"types": ["image/png", "application/pdf"]}}},
            None,
            "types : application/pdf non pris en charge",
        ),
        ({"execution": {"tools": {"offload_over": 0}}}, None, "greater than 0"),
        ({"telemetry": {"redaction": {}}}, None, "'redaction' : prévu pour le jalon J4"),
        ({"inconnu": 1}, None, "Extra inputs are not permitted"),
        ({"storage": {"events": {"backend": "sqlite"}}}, None, "seuls memory et jsonl"),
        ({"storage": {"events": {"backend": "jsonl"}}}, None, "'path' est obligatoire"),
        ({"telemetry": {"logging": {"level": "BAVARD"}}}, None, "Niveau de log 'BAVARD' inconnu"),
        (
            {},
            {"demo": {"name": "demo", "main": {"model": "ABSENT"}}},
            "modèle 'ABSENT' non déclaré (modèles connus : FAKE)",
        ),
        (
            {},
            {"demo": {**AGENT, "judge": {"model": "FAKE"}}},
            "'judge' : prévu pour le jalon J3",
        ),
        (
            {},
            {"demo": {"name": "demo", "main": {"model": "FAKE", "fallbacks": ["FAKE"]}}},
            "'fallbacks' : prévu pour le jalon J3",
        ),
        (
            {},
            {
                "demo": {
                    "name": "demo",
                    "main": {"model": "FAKE", "system": "a", "system_file": "b"},
                }
            },
            "'system' et 'system_file' ne peuvent pas être donnés ensemble",
        ),
        (
            {},
            {"demo": {**AGENT, "tools": [{"mcp": "crm"}]}},
            "Agent 'demo' : serveur MCP 'crm' non déclaré dans mcp_servers (serveurs : aucun)",
        ),
        (
            {},
            {"demo": {**AGENT, "tools": [{"python": "a"}, {"python": "a"}]}},
            "Outil déclaré deux fois : a",
        ),
        (
            {},
            {"demo": {"name": "espace interdit", "main": {"model": "FAKE"}}},
            "String should match pattern",
        ),
        (
            {},
            {"demo": {**AGENT, "main": {"model": "FAKE", "system_file": "absent.md"}}},
            "prompt introuvable",
        ),
    ],
)
def test_startup_checks(
    tmp_path: Path,
    root: dict[str, Any] | None,
    agents: dict[str, dict[str, Any]] | None,
    message: str,
) -> None:
    path = write(tmp_path, root=root, agents=agents)
    with pytest.raises(ConfigError) as caught:
        load_config(path)
    assert message in str(caught.value)
    assert str(tmp_path) in str(caught.value)


def test_duplicate_agents_across_files(tmp_path: Path) -> None:
    path = write(tmp_path, agents={"a": AGENT, "b": AGENT})
    with pytest.raises(ConfigError, match="Agent déclaré deux fois : demo"):
        load_config(path)


def test_duplicate_models(tmp_path: Path) -> None:
    path = write(tmp_path, root={"models": [MODEL, MODEL]})
    with pytest.raises(ConfigError, match="Modèle déclaré deux fois : FAKE"):
        load_config(path)


def test_missing_agents_dir(tmp_path: Path) -> None:
    path = write(tmp_path, root={"agents_dir": "absents/"})
    with pytest.raises(ConfigError, match="Dossier d'agents introuvable"):
        load_config(path)


def test_unreadable_files(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="Fichier illisible"):
        load_config(tmp_path / "absent.yaml")

    path = write(tmp_path)
    path.write_text("version: 1\n  décalé: 2\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="YAML invalide"):
        load_config(path)

    path.write_text("- une liste\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="doit contenir un objet YAML"):
        load_config(path)


def test_empty_agent_file_is_reported(tmp_path: Path) -> None:
    path = write(tmp_path, agents={})
    (tmp_path / "agents" / "vide.yaml").write_text("", encoding="utf-8")
    with pytest.raises(ConfigError, match=r"vide\.yaml"):
        load_config(path)


def test_references_by_name_and_path(tmp_path: Path) -> None:
    path = write(tmp_path, root={"imports": ["outils_demo"]}, modules={"outils_demo": OUTILS})
    config = load_config(path)
    registry = import_modules(config.imports, base_dir=config.base_dir)

    assert registry.names == ("calculer",)
    assert len(registry) == 1
    par_nom = resolve("calculer", registry)
    par_chemin = resolve("outils_demo:calculer", registry, base_dir=config.base_dir)
    assert par_nom is par_chemin
    brut = resolve("outils_demo:brut", registry, base_dir=config.base_dir)
    assert callable(brut)


def test_reference_errors(tmp_path: Path) -> None:
    write(tmp_path, modules={"outils_vides": OUTILS})
    registry = Registry({"connu": object()})
    with pytest.raises(ConfigError, match="aucun objet de ce nom \\(enregistrés : connu\\)"):
        resolve("inconnu", registry)
    with pytest.raises(ConfigError, match="module introuvable"):
        resolve("module_absent:x", registry, base_dir=tmp_path)
    with pytest.raises(ConfigError, match="'absent' absent du module"):
        resolve("outils_vides:absent", registry, base_dir=tmp_path)
    with pytest.raises(ConfigError, match="Module 'module_absent' introuvable"):
        import_modules(["module_absent"], base_dir=tmp_path)


def test_two_tools_with_the_same_name(tmp_path: Path) -> None:
    write(tmp_path, modules={"outils_un": OUTILS, "outils_deux": OUTILS})
    with pytest.raises(ConfigError, match="Deux objets portent le nom 'calculer'"):
        import_modules(["outils_un", "outils_deux"], base_dir=tmp_path)


def test_json_schema_describes_the_files() -> None:
    root = config_json_schema()
    assert root["additionalProperties"] is False
    assert {"version", "models", "agents", "storage"} <= root["properties"].keys()
    assert "AgentSpec" in root["$defs"]

    agent = agent_json_schema()
    assert {"name", "main", "tools"} <= agent["properties"].keys()
    assert agent["required"] == ["name", "main"]


def test_registry_rejects_duplicates_and_iterates() -> None:
    spec = AgentSpec.model_validate(AGENT)
    registry = AgentRegistry([spec])
    assert list(registry) == [spec]
    with pytest.raises(ValueError, match="Agent déclaré deux fois : demo"):
        AgentRegistry([spec, spec])


def test_unknown_model_id(tmp_path: Path) -> None:
    config = load_config(write(tmp_path))
    assert config.model_spec("FAKE").model == "fake-1"
    with pytest.raises(KeyError):
        config.model_spec("ABSENT")


def test_unreadable_prompt(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        agents={"demo": {**AGENT, "main": {"model": "FAKE", "system_file": "prompt"}}},
    )
    (tmp_path / "prompts" / "prompt").mkdir()
    with pytest.raises(ConfigError, match="prompt introuvable"):
        load_config(path)


def test_unreadable_prompt_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = write(
        tmp_path,
        agents={"demo": {**AGENT, "main": {"model": "FAKE", "system_file": "demo.md"}}},
        prompts={"demo.md": "Tu calcules."},
    )

    lisible = Path.read_text

    def refuse(self: Path, **kwargs: Any) -> str:
        if self.name == "demo.md":
            raise OSError("disque en panne")
        return lisible(self, **kwargs)

    monkeypatch.setattr(Path, "read_text", refuse)
    with pytest.raises(ConfigError, match="prompt illisible"):
        load_config(path)


def test_main_role_needs_an_object() -> None:
    with pytest.raises(ValidationError):
        MainRole.model_validate("Tu calcules.")


def test_prompt_is_read_only_when_needed(tmp_path: Path) -> None:
    # Un agent sans fichier de prompt garde le texte en ligne.
    config = load_config(write(tmp_path))
    assert system_prompt(config.agents[0]) == "Tu calcules."


def test_unsupported_key_carries_its_phase() -> None:
    error = UnsupportedKey("budgets", "J3 (coûts et budgets)")
    assert (error.key, error.phase) == ("budgets", "J3 (coûts et budgets)")
