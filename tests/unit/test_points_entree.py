# SPDX-License-Identifier: Apache-2.0
"""Sources d'outils fournies par des paquets installés : le groupe ``loom_ia.tools`` (J6.4b).

Les paquets sont de vrais paquets installés pour ``importlib.metadata`` : un
module et son ``dist-info`` (``METADATA``, ``entry_points.txt``) dans un
dossier mis en tête du chemin de Python le temps de l'essai.
"""

import importlib
import json
import sys
import textwrap
from collections.abc import Callable, Iterator
from importlib.metadata import EntryPoint, EntryPoints
from pathlib import Path
from typing import Any

import pytest
import yaml

from loom_ia.access import Loom
from loom_ia.access.cli import main
from loom_ia.config import ConfigError, load_config
from loom_ia.core.events import ToolCalled, ToolSourceUnavailable
from loom_ia.core.model import DEFAULT_TENANT, RunStatus, TenantId
from loom_ia.runtime import sources
from loom_ia.runtime.sources import GROUP, installed, source_factory

CARNET = {
    "D-2026-042": {"client": "Mme Martin", "objet": "chauffe-eau 200 L", "montant": 1840},
}

# Le paquet du carnet : une source, deux outils ; ce qu'il voit est noté au niveau du module.
CARNET_DEVIS = '''
import json
from contextlib import asynccontextmanager

from loom_ia.tools import tool

RECUS = []
OUVERTURES = []
FERMETURES = []
APPELS = []
CLOS = []


class Carnet:
    name = "nom-ignore"
    required = False

    def __init__(self, fichier):
        self.fichier = fichier

    @asynccontextmanager
    async def open(self, context):
        devis = json.loads(self.fichier.read_text(encoding="utf-8"))
        OUVERTURES.append(context.run_id)

        @tool
        def chercher_devis(numero: str) -> str:
            """Cherche un devis du carnet par son numéro."""
            APPELS.append(numero)
            return json.dumps(devis.get(numero, {"erreur": "inconnu"}), ensure_ascii=False)

        @tool
        def noter(numero: str, note: str) -> str:
            """Note quelque chose sur un devis."""
            return "noté"

        try:
            yield [chercher_devis, noter]
        finally:
            FERMETURES.append(context.run_id)

    async def aclose(self):
        CLOS.append(self.fichier.name)


def fabrique(*, name, params, secrets, base_dir):
    RECUS.append({"name": name, "params": dict(params), "jeton": secrets.get("CARNET_JETON")})
    fichier = params.get("fichier")
    if not isinstance(fichier, str):
        raise ValueError("'fichier' : le chemin du carnet est attendu")
    return Carnet(base_dir / fichier)
'''

# Un module qui ne doit jamais être importé : s'il l'est, l'essai le voit.
PIEGE = 'raise RuntimeError("ce paquet a été importé")\n'


class Paquets:
    """Des paquets installés le temps d'un essai, dans un dossier du chemin de Python."""

    def __init__(self, site: Path) -> None:
        self.site = site
        self.modules: list[str] = []

    def installe(
        self, paquet: str, module: str, code: str, points: dict[str, str], version: str = "0.1"
    ) -> None:
        (self.site / f"{module}.py").write_text(textwrap.dedent(code), encoding="utf-8")
        info = self.site / f"{paquet.replace('-', '_')}-{version}.dist-info"
        info.mkdir()
        (info / "METADATA").write_text(
            f"Metadata-Version: 2.1\nName: {paquet}\nVersion: {version}\n", encoding="utf-8"
        )
        lignes = "".join(f"{nom} = {cible}\n" for nom, cible in points.items())
        (info / "entry_points.txt").write_text(f"[{GROUP}]\n{lignes}", encoding="utf-8")
        self.modules.append(module)
        importlib.invalidate_caches()

    def module(self, nom: str) -> Any:
        return sys.modules[nom]


@pytest.fixture
def paquets(tmp_path: Path) -> Iterator[Paquets]:
    site = tmp_path / "site"
    site.mkdir()
    sys.path.insert(0, str(site))
    found = Paquets(site)
    try:
        yield found
    finally:
        sys.path.remove(str(site))
        for name in found.modules:
            sys.modules.pop(name, None)
        importlib.invalidate_caches()


@pytest.fixture
def masque(paquets: Paquets, monkeypatch: pytest.MonkeyPatch) -> Paquets:
    """Seuls les paquets de l'essai sont vus : ceux de l'environnement sont masqués.

    Le dépôt en installe un pour de bon (``loom-firecracker``, 6.4c) ; un essai
    qui lit la liste entière ne doit pas dépendre de ce qui est installé.
    """
    real = sources.entry_points

    def of_the_test(*, group: str) -> EntryPoints:
        def ours(point: EntryPoint) -> bool:
            return point.dist is not None and Path(str(point.dist.locate_file(""))) == paquets.site

        return EntryPoints(point for point in real(group=group) if ours(point))

    monkeypatch.setattr(sources, "entry_points", of_the_test)
    return paquets


@pytest.fixture
def carnet(paquets: Paquets) -> Paquets:
    paquets.installe(
        "carnet-devis", "carnet_devis", CARNET_DEVIS, {"carnet": "carnet_devis:fabrique"}
    )
    return paquets


def script(*calls: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        *({"text": "Je cherche.", "tool_calls": [call]} for call in calls),
        {"text": "C'est fait."},
    ]


CHERCHE = {"name": "carnet__chercher_devis", "arguments": {"numero": "D-2026-042"}}


def config_file(
    tmp_path: Path,
    *,
    sources: list[dict[str, Any]] | None = None,
    tools: list[dict[str, Any]] | None = None,
    calls: tuple[dict[str, Any], ...] = (CHERCHE,),
    **root: Any,
) -> Path:
    base = tmp_path / "config"
    (base / "agents").mkdir(parents=True, exist_ok=True)
    (base / "devis.json").write_text(json.dumps(CARNET), encoding="utf-8")
    config: dict[str, Any] = {
        "version": 1,
        "models": [
            {"id": "FAKE", "sdk": "fake", "model": "fake-1", "params": {"script": script(*calls)}}
        ],
        "tool_sources": (
            sources
            if sources is not None
            else [{"name": "carnet", "entry_point": "carnet", "params": {"fichier": "devis.json"}}]
        ),
        **root,
    }
    (base / "loom.yaml").write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    agent: dict[str, Any] = {
        "name": "relance",
        "main": {"model": "FAKE", "system": "Tu relances."},
        "max_iterations": 4,
        "tools": tools if tools is not None else [{"source": "carnet"}],
    }
    (base / "agents" / "relance.yaml").write_text(yaml.safe_dump(agent), encoding="utf-8")
    return base / "loom.yaml"


# --- La config ---------------------------------------------------------------------


def test_a_source_is_declared_once_and_referenced_by_name(tmp_path: Path) -> None:
    config = load_config(config_file(tmp_path))
    [declared] = config.tool_sources
    assert (declared.name, declared.entry_point) == ("carnet", "carnet")
    assert declared.params == {"fichier": "devis.json"}
    [ref] = config.agents[0].source_tools
    assert (ref.source, ref.prefix, ref.required) == ("carnet", "carnet", False)


@pytest.mark.parametrize(
    ("sources", "tools", "said"),
    [
        (None, [{"source": "absente"}], "source d'outils 'absente' non déclarée"),
        (
            [{"name": "carnet", "entry_point": "a"}, {"name": "carnet", "entry_point": "b"}],
            [],
            "Nom de source d'outils déclaré deux fois : carnet",
        ),
        (
            None,
            [{"source": "carnet", "include": ["a"], "exclude": ["b"]}],
            "'include' ou 'exclude', pas les deux",
        ),
        (
            None,
            [{"source": "carnet"}, {"source": "carnet"}],
            "Préfixe d'outils déclaré deux fois : carnet",
        ),
    ],
)
def test_a_badly_declared_source_is_refused(
    tmp_path: Path, sources: list[dict[str, Any]] | None, tools: list[dict[str, Any]], said: str
) -> None:
    with pytest.raises(ConfigError, match=said):
        load_config(config_file(tmp_path, sources=sources, tools=tools))


def test_a_source_and_a_server_do_not_share_a_name(tmp_path: Path) -> None:
    server = {"name": "carnet", "transport": "stdio", "command": "true"}
    with pytest.raises(ConfigError, match="Source d'outils et serveur MCP de même nom : carnet"):
        load_config(config_file(tmp_path, mcp_servers=[server]))


def test_a_source_prefix_cannot_be_an_mcp_prefix(tmp_path: Path) -> None:
    server = {"name": "crm", "transport": "stdio", "command": "true"}
    tools = [{"mcp": "crm"}, {"source": "carnet", "alias": "crm"}]
    with pytest.raises(ConfigError, match="Préfixe d'outils déclaré deux fois : crm"):
        load_config(config_file(tmp_path, tools=tools, mcp_servers=[server]))


def test_a_contract_given_by_file_is_read(tmp_path: Path) -> None:
    path = config_file(
        tmp_path,
        sources=[
            {
                "name": "carnet",
                "entry_point": "carnet",
                "tools": {"chercher_devis": {"output": {"schema_file": "devis.schema.yaml"}}},
            }
        ],
    )
    (path.parent / "devis.schema.yaml").write_text("type: object\n", encoding="utf-8")
    [declared] = load_config(path).tool_sources
    output = declared.tools["chercher_devis"].output
    assert output is not None and output.json_schema == {"type": "object"}


# --- Les points d'entrée -------------------------------------------------------------


def test_installed_entry_points_are_listed_without_importing_anything(paquets: Paquets) -> None:
    paquets.installe("piege", "piege_loom", PIEGE, {"piege": "piege_loom:fabrique"}, "2.0")
    paquets.installe(
        "carnet-devis", "carnet_devis", CARNET_DEVIS, {"carnet": "carnet_devis:fabrique"}
    )
    listed = [(p.name, p.package, p.version, p.value) for p in installed()]
    assert ("carnet", "carnet-devis", "0.1", "carnet_devis:fabrique") in listed
    assert ("piege", "piege", "2.0", "piege_loom:fabrique") in listed
    assert "piege_loom" not in sys.modules and "carnet_devis" not in sys.modules


def test_an_absent_entry_point_names_the_installed_ones(carnet: Paquets) -> None:
    with pytest.raises(ConfigError, match=r"'ailleurs' absent du groupe loom_ia\.tools.*carnet"):
        source_factory("ailleurs")


def test_two_packages_with_the_same_name_are_both_named(carnet: Paquets) -> None:
    carnet.installe("autre-carnet", "autre_carnet", PIEGE, {"carnet": "autre_carnet:fabrique"})
    with pytest.raises(ConfigError) as caught:
        source_factory("carnet")
    said = str(caught.value)
    assert "déclaré par plusieurs paquets" in said
    assert "carnet-devis 0.1, carnet_devis:fabrique" in said
    assert "autre-carnet 0.1, autre_carnet:fabrique" in said
    assert "autre_carnet" not in sys.modules


def test_a_package_that_does_not_import_says_why(paquets: Paquets) -> None:
    paquets.installe("piege", "piege_loom", PIEGE, {"piege": "piege_loom:fabrique"})
    with pytest.raises(
        ConfigError, match=r"\(piege 0\.1, piege_loom:fabrique\) : import impossible"
    ):
        source_factory("piege")


def test_an_entry_point_must_name_something_callable(paquets: Paquets) -> None:
    paquets.installe("plat", "plat_loom", "VALEUR = 3\n", {"plat": "plat_loom:VALEUR"})
    with pytest.raises(ConfigError, match="n'est pas une fabrique de source d'outils \\(int\\)"):
        source_factory("plat")


# --- Au montage et au run ---------------------------------------------------------


async def test_the_agent_uses_the_tools_of_the_package(carnet: Paquets, tmp_path: Path) -> None:
    config = load_config(config_file(tmp_path))
    # Lire la config n'importe rien : seul le montage d'un agent qui la référence le fait.
    assert "carnet_devis" not in sys.modules
    async with Loom(config, environ={"CARNET_JETON": "j-1"}) as loom:
        first = await loom.run("relance", "Relance le devis D-2026-042.")
        second = await loom.run("relance", "Relance le devis D-2026-042.")
        events = await loom.events(first.run_id, session_id=first.session_id)
    module = carnet.module("carnet_devis")
    assert first.status == second.status == RunStatus.COMPLETED
    called = [e.payload for e in events if isinstance(e.payload, ToolCalled)]
    assert [(c.tool_name, c.arguments) for c in called] == [
        ("carnet__chercher_devis", {"numero": "D-2026-042"})
    ]
    assert module.APPELS == ["D-2026-042", "D-2026-042"]
    # La fabrique : une fois au montage, avec le nom, les params et les secrets du client.
    assert module.RECUS == [{"name": "carnet", "params": {"fichier": "devis.json"}, "jeton": "j-1"}]
    # La source : ouverte et fermée à chaque run, fermée avec l'instance.
    assert module.OUVERTURES == module.FERMETURES == [first.run_id, second.run_id]
    assert module.CLOS == ["devis.json"]


async def test_only_the_referenced_package_is_imported(carnet: Paquets, tmp_path: Path) -> None:
    carnet.installe("piege", "piege_loom", PIEGE, {"piege": "piege_loom:fabrique"})
    async with Loom(load_config(config_file(tmp_path))) as loom:
        result = await loom.run("relance", "Relance.")
    assert result.status == RunStatus.COMPLETED
    assert "carnet_devis" in sys.modules and "piege_loom" not in sys.modules


async def test_choices_and_declarations_of_the_config_apply(
    carnet: Paquets, tmp_path: Path
) -> None:
    sources = [
        {
            "name": "carnet",
            "entry_point": "carnet",
            "params": {"fichier": "devis.json"},
            "tools": {
                "chercher_devis": {
                    "side_effects": "reversible",
                    "timeout": 3,
                    "description": "Le devis d'un client, par son numéro.",
                }
            },
        }
    ]
    tools = [
        {
            "source": "carnet",
            "alias": "c",
            "exclude": ["noter"],
            "tools": {"chercher_devis": {"timeout": 7}},
        }
    ]
    path = config_file(tmp_path, sources=sources, tools=tools)
    async with Loom(load_config(path)) as loom:
        context = loom.context("relance")
        from loom_ia.core.model import RunId, SessionId
        from loom_ia.core.ports import SourceContext

        opened_for = SourceContext(
            tenant_id=DEFAULT_TENANT, session_id=SessionId("s"), run_id=RunId("r"), agent="relance"
        )
        async with context.tools.opened(opened_for) as opened:
            specs = {spec.name: spec for spec in opened.tools.specs}
    assert "c__noter" not in specs
    found = specs["c__chercher_devis"]
    # Le nom préfixé, le genre d'un outil Python, la source puis l'agent par-dessus.
    assert (found.kind, found.side_effects, found.timeout) == ("python", "reversible", 7)
    assert found.description == "Le devis d'un client, par son numéro."


@pytest.mark.parametrize(
    ("params", "said"),
    [
        ({}, "refusée par sa fabrique — 'fichier' : le chemin du carnet est attendu"),
    ],
)
async def test_params_refused_by_the_package_refuse_the_mount(
    carnet: Paquets, tmp_path: Path, params: dict[str, Any], said: str
) -> None:
    sources = [{"name": "carnet", "entry_point": "carnet", "params": params}]
    async with Loom(load_config(config_file(tmp_path, sources=sources))) as loom:
        with pytest.raises(ConfigError) as caught:
            loom.context("relance")
    assert said in str(caught.value)
    assert "Agent 'relance', source 'carnet' (carnet-devis 0.1, carnet_devis:fabrique)" in str(
        caught.value
    )


async def test_a_factory_that_returns_no_source_is_refused(
    paquets: Paquets, tmp_path: Path
) -> None:
    paquets.installe(
        "vide",
        "vide_loom",
        "def fabrique(**reglages):\n    return 42\n",
        {"carnet": "vide_loom:fabrique"},
    )
    async with Loom(load_config(config_file(tmp_path))) as loom:
        with pytest.raises(ConfigError, match="n'a pas rendu une source d'outils \\(int"):
            loom.context("relance")


PANNE = """
from contextlib import asynccontextmanager

from loom_ia.core.ports import SourceUnavailable


class Panne:
    name = "panne"
    required = False

    @asynccontextmanager
    async def open(self, context):
        raise SourceUnavailable("carnet", "la plateforme ne répond pas")
        yield []


def fabrique(**reglages):
    return Panne()
"""


@pytest.mark.parametrize("required", [False, True])
async def test_an_unavailable_source_is_journaled(
    paquets: Paquets, tmp_path: Path, required: bool
) -> None:
    paquets.installe("panne", "panne_loom", PANNE, {"carnet": "panne_loom:fabrique"})
    tools = [{"source": "carnet", "required": required}]
    async with Loom(load_config(config_file(tmp_path, tools=tools, calls=()))) as loom:
        result = await loom.run("relance", "Relance.")
        events = await loom.events(result.run_id, session_id=result.session_id)
    [missing] = [e.payload for e in events if isinstance(e.payload, ToolSourceUnavailable)]
    assert (missing.source, missing.required) == ("carnet", required)
    assert "la plateforme ne répond pas" in missing.error
    assert result.status == (RunStatus.FAILED if required else RunStatus.COMPLETED)


async def test_a_run_with_a_package_replays_without_calling_it(
    carnet: Paquets, tmp_path: Path
) -> None:
    path = config_file(tmp_path, storage={"events": {"backend": "jsonl", "path": "data"}})
    async with Loom(load_config(path)) as loom:
        result = await loom.run("relance", "Relance.")
        appels = list(carnet.module("carnet_devis").APPELS)
        report = await loom.replay(result.run_id)
    assert report.identical
    # Rejoué : la source est ouverte pour lister ses outils, son outil n'est pas rappelé.
    assert carnet.module("carnet_devis").APPELS == appels == ["D-2026-042"]


async def test_an_eval_double_can_target_a_package_tool(carnet: Paquets, tmp_path: Path) -> None:
    from loom_ia.access.api import doubles_problem

    config = load_config(config_file(tmp_path))
    async with Loom(config) as loom:

        def context_of(name: str) -> Any:
            return loom.context(name)

        known = doubles_problem(config, "relance", {"carnet__chercher_devis": "x"}, context_of)
        unknown = doubles_problem(config, "relance", {"ailleurs__x": "x"}, context_of)
    assert known is None
    assert "inconnu" in str(unknown)


async def test_a_role_can_read_the_results_of_a_package_tool(
    carnet: Paquets, tmp_path: Path
) -> None:
    """Le nom préfixé d'un outil de paquet est connu au montage, avant toute ouverture."""
    role = {
        "name": "rediger",
        "description": "Rédige.",
        "model": "FAKE",
        "system": "Tu rédiges.",
        "context": [{"tool_results": ["carnet__chercher_devis"]}],
    }
    path = config_file(tmp_path)
    agent_file = path.parent / "agents" / "relance.yaml"
    agent = yaml.safe_load(agent_file.read_text(encoding="utf-8"))
    agent_file.write_text(yaml.safe_dump({**agent, "roles": [role]}), encoding="utf-8")
    async with Loom(load_config(path)) as loom:
        loom.context("relance")


# --- La ligne de commande -----------------------------------------------------------


def test_validate_names_sources_packages_and_tools(
    carnet: Paquets, masque: Paquets, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    carnet.installe("piege", "piege_loom", PIEGE, {"piege": "piege_loom:fabrique"}, "2.0")
    assert main(["--config", str(config_file(tmp_path)), "validate"]) == 0
    out = capsys.readouterr().out
    assert "Source     : carnet → point d'entrée carnet" in out
    assert (
        "Paquets    : carnet (carnet-devis 0.1, utilisé), piege (piege 2.0) (groupe loom_ia.tools)"
        in out
    )
    assert "source carnet : carnet__chercher_devis, carnet__noter" in out
    assert "piege_loom" not in sys.modules


def test_validate_says_nothing_of_packages_without_any(
    masque: Paquets, demo: Callable[..., Path], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--config", str(demo()), "validate"]) == 0
    out = capsys.readouterr().out
    assert "Paquets" not in out and "Source     :" not in out


async def test_each_client_gets_its_own_source_and_secrets(carnet: Paquets, tmp_path: Path) -> None:
    tenants = [
        {"id": "dupont", "secrets": {"CARNET_JETON": "DUPONT_JETON"}},
        {"id": "martin", "secrets": {"CARNET_JETON": "MARTIN_JETON"}},
    ]
    config = load_config(config_file(tmp_path, tenants=tenants))
    environ = {"DUPONT_JETON": "jd", "MARTIN_JETON": "jm"}
    async with Loom(config, environ=environ) as loom:
        for tenant in ("dupont", "martin"):
            result = await loom.run("relance", "Relance.", tenant=TenantId(tenant))
            assert result.status == RunStatus.COMPLETED
    recus = carnet.module("carnet_devis").RECUS
    assert [r["jeton"] for r in recus] == ["jd", "jm"]


async def test_a_package_source_has_no_circuit_breaker(paquets: Paquets, tmp_path: Path) -> None:
    """Six pannes de suite : chaque run réessaie la source, aucun disjoncteur ne s'ouvre."""
    paquets.installe("panne", "panne_loom", PANNE, {"carnet": "panne_loom:fabrique"})
    async with Loom(load_config(config_file(tmp_path, calls=()))) as loom:
        for _ in range(6):
            result = await loom.run("relance", "Relance.")
            events = await loom.events(result.run_id, session_id=result.session_id)
            types = [e.type for e in events]
            assert "tool.source_unavailable" in types and "circuit.opened" not in types
            [missing] = [e.payload for e in events if isinstance(e.payload, ToolSourceUnavailable)]
            assert "la plateforme ne répond pas" in missing.error


GENRES = '''
from contextlib import asynccontextmanager

from loom_ia.core.model import ToolOutput, ToolSpec


class Pretendu:
    """Un outil qui se dit MCP : chez loom, un outil de paquet reste un outil Python."""

    spec = ToolSpec(name="pretendu", description="Se dit MCP.", kind="mcp")

    async def invoke(self, arguments, context):
        return ToolOutput.text("ok")


class Source:
    name = "genres"
    required = False

    @asynccontextmanager
    async def open(self, context):
        yield [Pretendu()]


def fabrique(**reglages):
    return Source()
'''


async def test_a_package_tool_is_a_python_tool_whatever_it_says(
    paquets: Paquets, tmp_path: Path
) -> None:
    paquets.installe("genres", "genres_loom", GENRES, {"carnet": "genres_loom:fabrique"})
    async with Loom(load_config(config_file(tmp_path, calls=()))) as loom:
        from loom_ia.core.model import RunId, SessionId
        from loom_ia.core.ports import SourceContext

        opened_for = SourceContext(
            tenant_id=DEFAULT_TENANT, session_id=SessionId("s"), run_id=RunId("r"), agent="relance"
        )
        async with loom.context("relance").tools.opened(opened_for) as opened:
            kinds = {spec.name: spec.kind for spec in opened.tools.specs}
    assert kinds["carnet__pretendu"] == "python"
