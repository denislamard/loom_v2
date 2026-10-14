# SPDX-License-Identifier: Apache-2.0
"""Configuration : schéma, lecture YAML, contrôles au démarrage, références Python."""

import re
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from loom_ia.access import Loom
from loom_ia.adapters.stores import InMemoryEventStore
from loom_ia.agents import (
    AgentRegistry,
    AgentSpec,
    MainRole,
    RoleSpec,
    SessionStateContext,
    StateKey,
    StateSource,
    UnknownAgent,
)
from loom_ia.config import (
    ConfigError,
    LoomConfig,
    Registry,
    agent_json_schema,
    config_json_schema,
    import_modules,
    load_config,
    resolve,
)
from loom_ia.core.events import ToolCompleted
from loom_ia.core.model import SessionId, state_size
from loom_ia.core.projections import session_state
from loom_ia.runtime import build_agent, system_prompt

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
        (
            {"storage": {"bus": {"backend": "kafka"}}},
            None,
            "Bus 'kafka' : seuls memory, postgres, redis",
        ),
        (
            {"storage": {"bus": {"backend": "redis"}}},
            None,
            "Bus 'redis' : 'url_env' est obligatoire",
        ),
        (
            {"storage": {"bus": {"backend": "postgres", "url_env": "R"}}},
            None,
            "Bus 'postgres' : 'url_env' n'a pas de sens",
        ),
        (
            {"storage": {"queue": {"backend": "sqs"}}},
            None,
            "File 'sqs' : seuls asyncio, rabbitmq",
        ),
        (
            {"storage": {"queue": {"backend": "rabbitmq"}}},
            None,
            "File 'rabbitmq' : 'url_env' est obligatoire",
        ),
        (
            {"storage": {"queue": {"backend": "asyncio", "url_env": "URL"}}},
            None,
            "File 'asyncio' : 'url_env' n'a pas de sens",
        ),
        (
            {"storage": {"idempotency": {"backend": "firestore"}}},
            None,
            "Magasin d'idempotence 'firestore' : seuls journal, memory, sqlite, postgres",
        ),
        (
            {"storage": {"idempotency": {"backend": "postgres"}}},
            None,
            "Magasin d'idempotence 'postgres' : 'dsn_env' est obligatoire",
        ),
        (
            {"storage": {"idempotency": {"backend": "memory", "dsn_env": "PG"}}},
            None,
            "Magasin d'idempotence 'memory' : 'dsn_env' n'a pas de sens",
        ),
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
        ({"telemetry": {"capture": {"raw_max_bytes": 0}}}, None, "greater than 0"),
        ({"telemetry": {"bus": {}}}, None, "le bus se déclare dans 'storage.bus'"),
        ({"inconnu": 1}, None, "Extra inputs are not permitted"),
        (
            {"storage": {"events": {"backend": "firestore"}}},
            None,
            "seuls memory, jsonl, sqlite, postgres",
        ),
        (
            {"storage": {"events": {"backend": "postgres"}}},
            None,
            "Journal 'postgres' : 'dsn_env' est obligatoire",
        ),
        (
            {"storage": {"events": {"backend": "postgres", "dsn_env": "PG", "path": "a"}}},
            None,
            "Journal 'postgres' : 'path' n'a pas de sens",
        ),
        (
            {"storage": {"events": {"backend": "sqlite", "path": "a", "dsn_env": "PG"}}},
            None,
            "Journal 'sqlite' : 'dsn_env' n'a pas de sens",
        ),
        (
            {"storage": {"events": {"backend": "postgres", "dsn_env": "PG"}}},
            None,
            "Journal 'postgres' : déclarer 'storage.artifacts'",
        ),
        ({"storage": {"events": {"backend": "jsonl"}}}, None, "'path' est obligatoire"),
        ({"storage": {"events": {"backend": "sqlite"}}}, None, "'path' est obligatoire"),
        ({"telemetry": {"logging": {"level": "BAVARD"}}}, None, "Niveau de log 'BAVARD' inconnu"),
        (
            {},
            {"demo": {"name": "demo", "main": {"model": "ABSENT"}}},
            "modèle 'ABSENT' non déclaré (modèles connus : FAKE)",
        ),
        (
            {},
            {"demo": {**AGENT, "approval": {"on_expiry": "ignorer"}}},
            "on_expiry",
        ),
        (
            {},
            {"demo": {"name": "demo", "main": {"model": "FAKE", "fallbacks": ["FAKE"]}}},
            "Modèle en double dans la chaîne de secours : FAKE",
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


VOISIN = '''
from loom_ia.tools import tool

ORIGINE = "{origine}"


@tool
def lire() -> str:
    """Dit de quel dossier il vient."""
    return ORIGINE
'''

POLITIQUE_VOISINE = """
from voisin_50 import ORIGINE

VUE = ORIGINE
"""


def test_each_config_gets_its_own_neighbour_modules(tmp_path: Path) -> None:
    """Deux configs d'un même process, chacune son ``voisin_50.py`` (#50)."""
    a, b, c = tmp_path / "a", tmp_path / "b", tmp_path / "c"
    for folder in (a, b, c):
        folder.mkdir()
    (a / "voisin_50.py").write_text(VOISIN.format(origine="a"), encoding="utf-8")
    (b / "voisin_50.py").write_text(VOISIN.format(origine="b"), encoding="utf-8")
    # Un paquet voisin, et son sous-module : remplacés ensemble.
    for folder in (a, b):
        (folder / "paquet_50").mkdir()
        (folder / "paquet_50" / "__init__.py").write_text("", encoding="utf-8")
        (folder / "paquet_50" / "sous.py").write_text(
            f"ORIGINE = {folder.name!r}\n", encoding="utf-8"
        )
    import_modules(["paquet_50.sous"], base_dir=a)
    import_modules(["paquet_50.sous"], base_dir=b)
    assert sys.modules["paquet_50.sous"].ORIGINE == "b"
    # Un module voisin qui en importe un autre trouve celui de son dossier, même
    # importé le premier.
    (b / "politique_50.py").write_text(POLITIQUE_VOISINE, encoding="utf-8")
    premier = import_modules(["voisin_50"], base_dir=a)
    second = import_modules(["politique_50", "voisin_50"], base_dir=b)
    lire_a, lire_b = premier.get("lire"), second.get("lire")
    assert lire_a is not None and lire_b is not lire_a
    assert sys.modules["voisin_50"].ORIGINE == "b"
    assert sys.modules["politique_50"].VUE == "b"
    assert resolve("voisin_50:ORIGINE", Registry(), base_dir=b) == "b"
    # La première config garde ce qu'elle a pris, et retrouve le sien.
    assert resolve("voisin_50:ORIGINE", Registry(), base_dir=a) == "a"
    assert premier.get("lire") is lire_a
    # Un dossier sans module de ce nom reçoit le dernier importé, comme avant.
    assert resolve("voisin_50:ORIGINE", Registry(), base_dir=c) == "a"


def test_a_module_loom_did_not_import_as_a_neighbour_is_never_replaced(tmp_path: Path) -> None:
    """Un module de la bibliothèque standard que deux dossiers de config ombreraient reste le
    même."""
    avant = sys.modules["json"]
    for folder in (tmp_path / "a", tmp_path / "b"):
        folder.mkdir()
        (folder / "json.py").write_text("OMBRE = True\n", encoding="utf-8")
        import_modules(["json"], base_dir=folder)
        assert sys.modules["json"] is avant and not hasattr(avant, "OMBRE")


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


# --- State de session : déclaration (option C) --------------------------------

STATE_TOOLS = """
from loom_ia.core.ports import ToolContext
from loom_ia.tools import configure, idempotent, tool


@tool
def lire_client(ctx: ToolContext) -> str:
    '''Dit le client courant.'''
    return f"{ctx.state.get('client_courant', 'aucun')} / {ctx.state.get('langue')}"


@tool
def get_client(nom: str) -> dict[str, str]:
    '''Fiche du client.'''
    return {"code": "C-002", "nom": nom}


@tool
def cloturer(code: str) -> str:
    '''Clôt le dossier.'''
    return f"Dossier {code} clos."


@idempotent
@tool
def archiver(code: str) -> str:
    '''Archive le dossier.'''
    return f"Dossier {code} archivé."


def _resumer(code: str) -> str:
    '''Résume le dossier.'''
    return code


# Réglé dans le code ; la config peut le régler encore : deux couches.
resumer = configure(tool(name="resumer")(_resumer), timeout=3)
"""


def state_agent(**changes: Any) -> dict[str, Any]:
    """Agent qui déclare un state : une clé posée par ses outils, une par défaut, une requise."""
    spec: dict[str, Any] = {
        "name": "demo",
        "main": {"model": "FAKE", "system": "Tu aides.", "context": ["session_state"]},
        "tools": [{"python": "get_client"}, {"python": "cloturer"}],
        "state": {
            "client_courant": {
                "schema": {"type": "string", "pattern": "^C-[0-9]+$"},
                "description": "Code du client dont on parle.",
                "from": [
                    {"tool": "get_client", "path": "/code"},
                    {"tool": "cloturer", "value": None},
                ],
            },
            "langue": {"schema": {"enum": ["fr", "en"]}, "default": "fr"},
            "client_id": {"schema": {"type": "string"}, "required": True},
        },
    }
    return {**spec, **changes}


def write_state(
    tmp_path: Path, *agents: dict[str, Any], root: dict[str, Any] | None = None
) -> Path:
    return write(
        tmp_path,
        root={"imports": ["outils_state"], **(root or {})},
        agents={agent["name"]: agent for agent in agents or (state_agent(),)},
        modules={"outils_state": STATE_TOOLS},
    )


def with_key(name: str, key: dict[str, Any], **changes: Any) -> dict[str, Any]:
    """L'agent du state, avec cette seule clé."""
    return state_agent(state={name: key}, **changes)


async def test_the_session_state_is_declared_by_the_agent(tmp_path: Path) -> None:
    config = load_config(write_state(tmp_path))
    spec = config.agents[0]
    client = spec.state["client_courant"]
    assert client.json_schema == {"type": "string", "pattern": "^C-[0-9]+$"}
    assert client.description == "Code du client dont on parle."
    assert [(s.tool, s.path, s.value) for s in client.sources] == [
        ("get_client", "/code", None),
        ("cloturer", None, None),
    ]
    assert (spec.state["langue"].default, spec.state["langue"].required) == ("fr", False)
    assert (spec.state["client_id"].default, spec.state["client_id"].required) == (None, True)
    # Tout le state, ou certaines clés.
    assert spec.main.state_keys == ()
    limits = config.sessions.state
    assert (limits.max_value_bytes, limits.max_total_bytes) == (1024, 4096)
    # Le schéma pour l'éditeur parle comme le YAML : « schema », « from ».
    definitions = agent_json_schema()["$defs"]
    assert {"schema", "from", "required", "default"} <= set(definitions["StateKey"]["properties"])
    assert {"tool", "path", "value"} == set(definitions["StateSource"]["properties"])
    assert "StateLimits" in config_json_schema()["$defs"]
    agent = build_agent(config, "demo", InMemoryEventStore())
    await agent.aclose()

    some = state_agent(main={"model": "FAKE", "context": [{"session_state": ["langue"]}]})
    assert load_config(write_state(tmp_path, some)).agents[0].main.state_keys == ("langue",)
    plain = load_config(write_state(tmp_path, state_agent(main={"model": "FAKE"}, state={})))
    assert (plain.agents[0].state, plain.agents[0].main.state_keys) == ({}, None)


STRING: dict[str, Any] = {"type": "string"}


@pytest.mark.parametrize(
    ("agent", "message"),
    [
        (with_key("1client", {"schema": STRING}), "clé '1client' invalide"),
        (with_key("k", {"schema": {"type": "chaine"}}), "schema invalide"),
        (
            with_key("k", {"schema": STRING, "required": True, "default": "x"}),
            "'required' et 'default' s'excluent",
        ),
        (with_key("k", {"schema": STRING, "default": 3}), "default ne suit pas le schéma"),
        (
            with_key("k", {"schema": STRING, "from": [{"tool": "get_client"}]}),
            "from, outil 'get_client' : 'path' ou 'value', l'un des deux",
        ),
        (
            with_key(
                "k",
                {"schema": STRING, "from": [{"tool": "get_client", "path": "/a", "value": "x"}]},
            ),
            "'path' ou 'value', l'un des deux",
        ),
        (
            with_key("k", {"schema": STRING, "from": [{"tool": "get_client", "path": "code"}]}),
            "path 'code' n'est pas un pointeur JSON",
        ),
        (
            with_key("k", {"schema": STRING, "from": [{"tool": "cloturer", "value": 3}]}),
            "from, outil 'cloturer' : value ne suit pas le schéma",
        ),
        (
            with_key(
                "k",
                {
                    "schema": STRING,
                    "from": [
                        {"tool": "cloturer", "value": None},
                        {"tool": "cloturer", "value": "x"},
                    ],
                },
            ),
            "outil cité deux fois : cloturer",
        ),
        # La proposition d'origine disait « writers » : remplacé par « from ».
        (with_key("k", {"schema": STRING, "writers": ["get_client"]}), "writers"),
        (
            state_agent(main={"model": "FAKE", "context": [{"session_state": ["inconnue"]}]}),
            "session_state cite 'inconnue', absente(s) de 'state'",
        ),
        (
            state_agent(state={}),
            "main.context : session_state, mais aucune clé déclarée dans 'state'",
        ),
        (
            state_agent(main={"model": "FAKE", "context": ["session_summary"]}),
            "main.context : 'session_summary' refusé, seul 'session_state' est accepté",
        ),
        (
            state_agent(
                main={"model": "FAKE", "context": ["session_state", {"session_state": ["langue"]}]}
            ),
            "session_state déclaré deux fois",
        ),
    ],
)
def test_a_declared_state_is_checked_at_load(
    tmp_path: Path, agent: dict[str, Any], message: str
) -> None:
    with pytest.raises(ConfigError, match=re.escape(message)):
        load_config(write_state(tmp_path, agent))


def test_the_state_values_of_the_config_stay_within_the_session_limits(tmp_path: Path) -> None:
    """D7 : une valeur par défaut ou fixe ne dépasse pas les limites de la session."""
    # Une valeur : son JSON compact, en octets UTF-8.
    assert state_size({"k": "é"}) == len('{"k":"é"}'.encode()) == 10
    limits = {"sessions": {"state": {"max_value_bytes": 16, "max_total_bytes": 32}}}
    long = "x" * 20

    def refused(agent: dict[str, Any], message: str) -> None:
        with pytest.raises(ConfigError, match=re.escape(message)):
            load_config(write_state(tmp_path, agent, root=limits))

    refused(
        with_key("k", {"schema": STRING, "default": long}),
        "state 'k' : default fait 22 octets, au-delà de sessions.state.max_value_bytes (16)",
    )
    refused(
        with_key("k", {"schema": STRING, "from": [{"tool": "cloturer", "value": long}]}),
        "from, outil 'cloturer' : value fait 22 octets",
    )
    ten = "x" * 10
    both = state_agent(
        main={"model": "FAKE"},
        state={"a": {"schema": STRING, "default": ten}, "b": {"schema": STRING, "default": ten}},
    )
    refused(
        both, "les valeurs par défaut font 35 octets, au-delà de sessions.state.max_total_bytes"
    )
    load_config(
        write_state(tmp_path, with_key("k", {"schema": STRING, "default": ten}), root=limits)
    )

    inverted = {"sessions": {"state": {"max_value_bytes": 64, "max_total_bytes": 32}}}
    with pytest.raises(ConfigError, match=re.escape("max_value_bytes (64) dépasse")):
        load_config(write_state(tmp_path, root=inverted))


ROLE: dict[str, Any] = {
    "name": "rediger",
    "description": "Rédige un message.",
    "model": "FAKE",
    "input_schema": {"type": "object", "properties": {"sujet": {"type": "string"}}},
}
HELPER: dict[str, Any] = {
    "name": "verifier",
    "description": "Vérifie un dossier.",
    "main": {"model": "FAKE"},
}
OBJECT_CONTRACT: dict[str, Any] = {"schema": {"type": "object"}}


def mounted(**changes: Any) -> dict[str, Any]:
    """L'agent du state, avec un rôle et un sous-agent."""
    base: dict[str, Any] = {
        "main": {"model": "FAKE"},
        "roles": [ROLE],
        "subagents": [{"agent": "verifier"}],
    }
    return state_agent(**{**base, **changes})


def sourced(source: dict[str, Any], **changes: Any) -> dict[str, Any]:
    return mounted(state={"k": {"schema": {}, "from": [source]}}, **changes)


@pytest.mark.parametrize(
    ("agent", "message"),
    [
        (
            sourced({"tool": "absent", "value": "x"}),
            "state 'k' : from désigne 'absent', qui n'est pas un outil de l'agent "
            "(outils : get_client, cloturer, rediger, verifier)",
        ),
        (sourced({"tool": "cloturer", "path": ""}), "l'outil 'cloturer' ne rend pas de données"),
        (sourced({"tool": "rediger", "path": "/a"}), "le rôle 'rediger' rend du texte"),
        (sourced({"tool": "verifier", "path": "/a"}), "le sous-agent 'verifier' rend du texte"),
        # Ce qui a des données, ou n'en demande pas, passe.
        (sourced({"tool": "get_client", "path": "/code"}), None),
        (sourced({"tool": "cloturer", "value": None}), None),
        (sourced({"tool": "rediger", "value": "x"}), None),
        (sourced({"tool": "verifier", "value": "x"}), None),
        # Ce que la signature dit reste vrai à travers les réglages et la mémorisation.
        (
            sourced(
                {"tool": "cloturer", "path": "/a"},
                tools=[{"python": "cloturer", "timeout": 5}],
            ),
            "l'outil 'cloturer' ne rend pas de données",
        ),
        (
            sourced({"tool": "archiver", "path": "/a"}, tools=[{"python": "archiver"}]),
            "l'outil 'archiver' ne rend pas de données",
        ),
        (
            sourced({"tool": "resumer", "path": "/a"}, tools=[{"python": "resumer", "timeout": 5}]),
            "l'outil 'resumer' ne rend pas de données",
        ),
        # Un contrat avec schéma range l'objet JSON validé dans les données.
        (
            sourced(
                {"tool": "cloturer", "path": "/a"},
                tools=[{"python": "get_client"}, {"python": "cloturer", "output": OBJECT_CONTRACT}],
            ),
            None,
        ),
        # Donné par fichier, le schéma est lu au chargement : il compte de même.
        (
            sourced(
                {"tool": "cloturer", "path": "/a"},
                tools=[{"python": "cloturer", "output": {"schema_file": "objet.json"}}],
            ),
            None,
        ),
        (
            mounted(
                roles=[{**ROLE, "output": OBJECT_CONTRACT}],
                state={"k": {"schema": {}, "from": [{"tool": "rediger", "path": "/a"}]}},
            ),
            None,
        ),
    ],
)
async def test_the_sources_of_the_state_are_checked_at_mount(
    tmp_path: Path, agent: dict[str, Any], message: str | None
) -> None:
    (tmp_path / "objet.json").write_text('{"type": "object"}', encoding="utf-8")
    config = load_config(write_state(tmp_path, agent, HELPER))
    if message is not None:
        with pytest.raises(ConfigError, match=re.escape(message)):
            build_agent(config, "demo", InMemoryEventStore())
        return
    built = build_agent(config, "demo", InMemoryEventStore())
    await built.aclose()


def test_a_path_is_a_json_pointer() -> None:
    """RFC 6901 : « ~0 » pour « ~ », « ~1 » pour « / » ; rien d'autre après un « ~ »."""
    for path in ("", "/", "/code", "/lignes/0/montant", "/a~0b~1c"):
        assert StateSource.model_validate({"tool": "t", "path": path}).path == path
    for path in ("code", "/a~2", "/a~"):
        with pytest.raises(ValidationError, match="pointeur JSON"):
            StateSource.model_validate({"tool": "t", "path": path})
    # « null » efface la clé : c'est une valeur donnée, pas une valeur absente.
    erase = StateSource.model_validate({"tool": "t", "value": None})
    assert (erase.path, erase.value) == (None, None)


def test_the_state_can_be_declared_in_python() -> None:
    main = MainRole(model="FAKE", context=(SessionStateContext(session_state=("langue",)),))
    assert main.state_keys == ("langue",)
    key = StateKey.model_validate({"schema": {"enum": ["fr", "en"]}, "default": "fr"})
    spec = AgentSpec(name="demo", main=main, state={"langue": key})
    assert spec.state["langue"].default == "fr"
    with pytest.raises(ValidationError, match="clé citée deux fois : langue"):
        MainRole(model="FAKE", context=(SessionStateContext(session_state=("langue", "langue")),))
    with pytest.raises(ValidationError, match="pas encore disponible pour un rôle"):
        RoleSpec.model_validate(
            {
                "name": "rediger",
                "description": "Rédige.",
                "model": "FAKE",
                "context": [SessionStateContext(session_state=("langue",))],
            }
        )


def test_a_null_value_weighs_nothing(tmp_path: Path) -> None:
    """``value: null`` efface la clé : il ne compte pas dans les limites."""
    tiny = {"sessions": {"state": {"max_value_bytes": 3, "max_total_bytes": 3}}}
    agent = with_key("k", {"schema": STRING, "from": [{"tool": "cloturer", "value": None}]})
    load_config(write_state(tmp_path, agent, root=tiny))


async def test_a_tool_reads_what_an_earlier_run_of_the_session_wrote(tmp_path: Path) -> None:
    """De bout en bout : la config déclare, l'exécuteur pose, le journal garde, l'outil relit.

    Et le run se rejoue à l'identique : le rejeu sert les résultats du journal,
    leur state et leurs refus compris, sans les recalculer.
    """
    # Le modèle scripté répond selon la demande : un tour pour chercher, un pour relire.
    script: list[dict[str, Any]] = [
        {"with_text": "Martin", "tool_calls": [{"name": "get_client", "arguments": {"nom": "M"}}]},
        {"with_text": "Martin", "text": "Client trouvé."},
        {"with_text": "maintenant", "tool_calls": [{"name": "lire_client", "arguments": {}}]},
        {"with_text": "maintenant", "text": "C'est lui."},
    ]
    scripted = {**MODEL, "params": {"script": script}}
    agent = state_agent(
        main={"model": "FAKE"},
        tools=[{"python": "get_client"}, {"python": "lire_client"}],
        state={
            "client_courant": {
                "schema": {"type": "string"},
                "from": [{"tool": "get_client", "path": "/code"}],
            },
            "langue": {"schema": {"enum": ["fr", "en"]}, "default": "fr"},
            # Refusée à chaque fois : « nom » est un texte.
            "rang": {
                "schema": {"type": "integer"},
                "from": [{"tool": "get_client", "path": "/nom"}],
            },
        },
    )
    path = write_state(tmp_path, agent, root={"models": [scripted]})
    session = SessionId("conversation")
    async with Loom(load_config(path)) as loom:
        first = await loom.run("demo", "Qui est Martin ?", session_id=session)
        second = await loom.run("demo", "Et maintenant ?", session_id=session)
        before = await loom.events(first.run_id, session_id=session)
        after = await loom.events(second.run_id, session_id=session)
        for run in (first, second):
            report = await loom.replay(run.run_id, session_id=session)
            assert report.identical, report.divergence

    [found] = [e.payload for e in before if isinstance(e.payload, ToolCompleted)]
    assert found.output.state == {"client_courant": "C-002"}
    assert found.output.state_refused == (
        "rang non posé : ne suit pas son schéma — doit être un entier (reçu : du texte)",
    )
    [read] = [e.payload for e in after if isinstance(e.payload, ToolCompleted)]
    assert read.output.as_text == "C-002 / fr"
    assert session_state([*before, *after]) == {"client_courant": "C-002"}
