# SPDX-License-Identifier: Apache-2.0
"""Outils fournis par loom-ia (``loom.<outil>``) : catalogue, montage, ``loom validate``,
et le préfixe ``loom.`` réservé dans le registre.

Aucun n'est actif d'office : un agent n'a que les outils qu'il liste. Les essais
de montage s'appuient sur un outil fourni d'essai, ajouté au catalogue le temps
d'un test ; ``test_catalogue_is_consistent`` éprouve, lui, le vrai catalogue.
"""

import sys
import types
from pathlib import Path
from typing import Any

import pytest
from conftest import MODEL, ConfigFactory, demo_agent

from loom_ia.access.api import Loom
from loom_ia.access.cli import main
from loom_ia.adapters.stores import InMemoryEventStore
from loom_ia.config import ConfigError, load_config
from loom_ia.config.references import Registry
from loom_ia.core.events import ToolCompleted
from loom_ia.core.model import DEFAULT_TENANT, RESERVED_PREFIX
from loom_ia.engine import begin_run, drive
from loom_ia.runtime import Agent, build_agent
from loom_ia.tools import tool
from loom_ia.tools_builtin import BUILTIN_TOOLS, builtin_tool, is_builtin

# Module d'essai des outils fournis : jamais sur disque, posé dans sys.modules.
PROVIDED = "loom_essai_fourni"
# Module qu'aucun essai ne crée : l'importer échoue.
ABSENT = "loom_essai_absent"


@tool(timeout=12)
def lire(url: str) -> str:
    """Lit une adresse (outil fourni d'essai)."""
    return f"Lu : {url}"


@tool
def calculer(expr: str) -> str:
    """Homonyme de l'outil maison de la config de démonstration."""
    return expr


@pytest.fixture
def provided(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ajoute au catalogue ``loom.lire``, ``loom.calculer``, un outil absent et un non-outil."""
    module = types.ModuleType(PROVIDED)
    vars(module).update(lire=lire, calculer=calculer, valeur=42)
    monkeypatch.setitem(sys.modules, PROVIDED, module)
    monkeypatch.setitem(BUILTIN_TOOLS, "loom.lire", f"{PROVIDED}:lire")
    monkeypatch.setitem(BUILTIN_TOOLS, "loom.calculer", f"{PROVIDED}:calculer")
    monkeypatch.setitem(BUILTIN_TOOLS, "loom.absent", f"{ABSENT}:outil")
    monkeypatch.setitem(BUILTIN_TOOLS, "loom.valeur", f"{PROVIDED}:valeur")


def _agent(demo: ConfigFactory, *tools: dict[str, Any], **root: Any) -> Agent:
    path = demo(agents=[demo_agent(tools=list(tools))], **root)
    return build_agent(load_config(path), "demo", InMemoryEventStore())


# --- Montage ------------------------------------------------------------------------


@pytest.mark.usefixtures("provided")
async def test_no_builtin_tool_is_active_by_default(demo: ConfigFactory) -> None:
    """Sans ``loom.…`` dans ses outils, l'agent n'a que les siens, et rien n'est importé."""
    agent = _agent(demo, {"python": "calculer"})
    try:
        assert [spec.name for spec in agent.context.tools.specs] == ["calculer"]
        # Le catalogue cite un module qui n'existe pas : l'importer aurait échoué.
        assert ABSENT not in sys.modules
    finally:
        await agent.aclose()


@pytest.mark.usefixtures("provided")
async def test_builtin_tool_is_mounted_under_its_own_name(demo: ConfigFactory) -> None:
    agent = _agent(demo, {"python": "calculer"}, {"python": "loom.lire"})
    try:
        _, read = agent.context.tools.specs
        # Le modèle voit le nom de l'outil, sans le préfixe ; ses déclarations tiennent.
        assert (read.name, read.kind, read.timeout) == ("lire", "python", 12)
        assert agent.context.tools.get("lire") is not None
    finally:
        await agent.aclose()


@pytest.mark.usefixtures("provided")
async def test_agent_settings_override_the_builtin_declarations(demo: ConfigFactory) -> None:
    agent = _agent(
        demo,
        {"python": "loom.lire", "timeout": 3, "offload_over": 5000, "side_effects": "reversible"},
    )
    try:
        (read,) = agent.context.tools.specs
        assert (read.timeout, read.offload_over, read.side_effects) == (3, 5000, "reversible")
    finally:
        await agent.aclose()


def test_unknown_builtin_tool_is_refused(demo: ConfigFactory) -> None:
    with pytest.raises(ConfigError, match=r"Outil fourni 'loom\.nope' inconnu \(fournis : "):
        _agent(demo, {"python": "loom.nope"})


@pytest.mark.usefixtures("provided")
def test_unknown_builtin_tool_lists_the_provided_ones(demo: ConfigFactory) -> None:
    with pytest.raises(ConfigError, match=r"inconnu \(fournis : [^)]*loom\.lire"):
        _agent(demo, {"python": "loom.nope"})


def test_empty_catalogue_says_so(demo: ConfigFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    for reference in list(BUILTIN_TOOLS):
        monkeypatch.delitem(BUILTIN_TOOLS, reference)
    with pytest.raises(ConfigError, match=r"inconnu \(fournis : aucun\)"):
        _agent(demo, {"python": "loom.nope"})


@pytest.mark.usefixtures("provided")
def test_missing_dependency_is_refused(demo: ConfigFactory) -> None:
    with pytest.raises(
        ConfigError, match=rf"Outil fourni 'loom\.absent' : dépendance '{ABSENT}' absente"
    ):
        _agent(demo, {"python": "loom.absent"})


@pytest.mark.usefixtures("provided")
def test_a_catalogue_entry_must_be_a_tool() -> None:
    with pytest.raises(TypeError, match="n'est pas un outil"):
        builtin_tool("loom.valeur")


@pytest.mark.usefixtures("provided")
def test_name_clash_with_an_own_tool_is_refused(demo: ConfigFactory) -> None:
    """Un outil fourni et un outil maison du même nom : le contrôle existant refuse."""
    with pytest.raises(ConfigError, match="s'appellent calculer"):
        _agent(demo, {"python": "calculer"}, {"python": "loom.calculer"})


async def test_import_path_in_a_package_named_loom_is_not_a_builtin(
    demo: ConfigFactory, tmp_path: Path
) -> None:
    """``loom.x:y`` reste un chemin d'import : celui d'un paquet à soi nommé ``loom``."""
    package = tmp_path / "loom"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "outils_maison.py").write_text(
        "from loom_ia.tools import tool\n\n\n"
        "@tool\ndef maison(x: int) -> int:\n    '''Outil maison.'''\n    return x\n",
        encoding="utf-8",
    )
    try:
        agent = _agent(demo, {"python": "loom.outils_maison:maison"})
        try:
            assert [spec.name for spec in agent.context.tools.specs] == ["maison"]
        finally:
            await agent.aclose()
    finally:
        for name in [m for m in sys.modules if m == "loom" or m.startswith("loom.")]:
            del sys.modules[name]


@pytest.mark.usefixtures("provided")
async def test_a_run_calls_the_builtin_tool(demo: ConfigFactory) -> None:
    script: list[dict[str, Any]] = [
        {"text": "Je lis.", "tool_calls": [{"name": "lire", "arguments": {"url": "exemple"}}]},
        {"text": "C'est lu."},
    ]
    store = InMemoryEventStore()
    path = demo(
        agents=[demo_agent(tools=[{"python": "loom.lire"}])],
        models=[{**MODEL, "params": {"script": script}}],
    )
    agent = build_agent(load_config(path), "demo", store)
    try:
        run = await begin_run(agent.context, "Lis l'exemple.")
        state = await drive(agent.context, run.run_id)
    finally:
        await agent.aclose()
    assert state.output is not None and state.output.text == "C'est lu."
    events = await store.read(DEFAULT_TENANT, run.session_id)
    [completed] = [e.payload for e in events if isinstance(e.payload, ToolCompleted)]
    assert (completed.tool_name, completed.output.as_text) == ("lire", "Lu : exemple")


# --- Ce que montre loom validate (D3) ------------------------------------------------


def test_validate_lists_the_provided_tools(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    tools = [{"python": "calculer"}, {"python": "loom.fetch_page"}]
    path = demo(agents=[demo_agent(tools=tools)])
    assert main(["--config", str(path), "validate"]) == 0
    out = capsys.readouterr().out
    assert "Outils     : calculer\nFournis    : loom.fetch_page\n" in out
    assert "  demo : modèle FAKE, 2 outil(s) Python" in out


def test_validate_says_nothing_without_provided_tools(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--config", str(demo()), "validate"]) == 0
    assert "Fournis" not in capsys.readouterr().out


@pytest.mark.parametrize(
    ("reference", "provided"),
    [
        ("loom.fetch_page", True),
        ("loom.outils:lire", False),  # chemin d'import dans un paquet à soi nommé loom
        ("fetch_page", False),
        ("outils:lire", False),
        ("loomx.lire", False),
    ],
)
def test_is_builtin(reference: str, provided: bool) -> None:
    assert is_builtin(reference) is provided


# --- Le préfixe loom. réservé dans le registre (D5) ----------------------------------


def test_the_registry_refuses_a_loom_name() -> None:
    with pytest.raises(ConfigError, match=r"Nom 'loom\.lire' refusé \(registre\) : le préfixe"):
        Registry({"loom.lire": lire})
    registry = Registry()
    with pytest.raises(ConfigError, match=r"\(module essai\) : le préfixe 'loom\.' est réservé"):
        registry.add("loom.lire", lire, source="module essai")
    assert len(registry) == 0


async def test_register_refuses_a_loom_name(demo: ConfigFactory) -> None:
    async with Loom.from_config(demo()) as loom:
        with pytest.raises(ConfigError, match=r"Nom 'loom\.fetch_page' refusé \(register\(\)\)"):
            loom.register("loom.fetch_page", lire)


# --- Catalogue ----------------------------------------------------------------------


def test_catalogue_is_consistent() -> None:
    """Chaque entrée : préfixe ``loom.``, module importable, un outil, nommé sans le préfixe."""
    for reference in BUILTIN_TOOLS:
        assert is_builtin(reference), reference
        found = builtin_tool(reference)
        assert found.spec.name == reference.removeprefix(RESERVED_PREFIX), reference
        assert found.spec.description, reference
