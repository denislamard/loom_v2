# SPDX-License-Identifier: Apache-2.0
"""``loom serve --reload`` avec de vrais process (#48, J6.4a).

Le superviseur garde la socket ; à chaque changement, un nouveau process
charge la config et monte les agents pendant que l'ancien sert encore. S'il
échoue, l'ancien continue ; sinon il prend la main et l'ancien finit ce qu'il
a en cours. Ces essais le vérifient de l'extérieur, par l'API REST et par ce
que la console dit.
"""

import json
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

pytest.importorskip("fastapi", reason="extra 'http' absent")
pytest.importorskip("watchfiles", reason="extra 'http' absent")

pytestmark = pytest.mark.integration

LANCEUR = "import sys; from loom_ia.access.cli import main; sys.exit(main(sys.argv[1:]))"
# Assez pour un démarrage de process sur une machine chargée.
DELAI = 30.0
# Plus que l'attente de regroupement de watchfiles (1,6 s) : ce qui n'a pas
# relancé après ça ne relancera pas.
CALME = 3.0

LENT = '''
import asyncio

from loom_ia.tools import tool


@tool
async def attendre(secondes: float) -> str:
    """Attend, puis le dit."""
    await asyncio.sleep(secondes)
    return "attendu"
'''

OUTILS = '''
from loom_ia.tools import tool


@tool
def calculer(expr: str) -> str:
    """Calcule une expression arithmétique."""
    return str(eval(expr))
'''
SCRIPT: list[dict[str, Any]] = [
    {"text": "Je calcule.", "tool_calls": [{"name": "calculer", "arguments": {"expr": "12*7+3"}}]},
    {"text": "12 fois 7, plus 3, font 87."},
]

type ConfigFactory = Callable[..., Path]

# Sans proxy : le serveur est local.
OUVREUR = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class Served:
    """Un ``loom serve --reload`` lancé en sous-process, sa console dans un fichier."""

    def __init__(self, config: Path, log: Path) -> None:
        self.port = free_port()
        self.log = log
        self._out = log.open("w", encoding="utf-8")
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                LANCEUR,
                "--config",
                str(config),
                "serve",
                "--reload",
                "--port",
                str(self.port),
            ],
            stdout=self._out,
            stderr=subprocess.STDOUT,
        )

    @property
    def said(self) -> str:
        return self.log.read_text(encoding="utf-8")

    def call(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/v1{path}",
            data=data,
            method=method,
            headers={"content-type": "application/json"},
        )
        with OUVREUR.open(request, timeout=DELAI) as response:
            return json.loads(response.read())

    def agents(self) -> dict[str, str]:
        return {agent["name"]: agent["description"] for agent in self.call("GET", "/agents")}

    def until(self, what: Callable[[], bool], why: str, *, every: float = 0.1) -> None:
        limit = time.monotonic() + DELAI
        while time.monotonic() < limit:
            try:
                if what():
                    return
            except urllib.error.URLError, ConnectionError:
                pass
            if self.process.poll() is not None:
                break
            time.sleep(every)
        raise AssertionError(f"{why} — console :\n{self.said}")

    def ready(self) -> None:
        self.until(lambda: "Rechargement : surveille" in self.said, "pas prêt")

    def stop(self) -> int:
        if self.process.poll() is None:
            self.process.send_signal(signal.SIGINT)
        code = self.process.wait(timeout=DELAI)
        self._out.close()
        return code


def demo_agent(**changes: Any) -> dict[str, Any]:
    agent: dict[str, Any] = {
        "name": "demo",
        "description": "Répond aux questions de calcul.",
        "main": {"model": "FAKE", "system_file": "demo.md"},
        "max_iterations": 4,
        "tools": [{"python": "calculer"}],
    }
    return {**agent, **changes}


@pytest.fixture
def demo(tmp_path: Path) -> ConfigFactory:
    """La config ``demo`` des essais d'accès, écrite dans ``tmp_path`` ; la racine se complète."""

    def build(*, agents: list[dict[str, Any]] | None = None, **root: Any) -> Path:
        (tmp_path / "agents").mkdir(exist_ok=True)
        (tmp_path / "prompts").mkdir(exist_ok=True)
        (tmp_path / "prompts" / "demo.md").write_text("Tu calcules.", encoding="utf-8")
        (tmp_path / "outils_acces.py").write_text(OUTILS, encoding="utf-8")
        config: dict[str, Any] = {
            "version": 1,
            "imports": ["outils_acces"],
            "models": [
                {"id": "FAKE", "sdk": "fake", "model": "fake-1", "params": {"script": SCRIPT}}
            ],
            "telemetry": {"logging": {"level": "WARNING"}},
            **root,
        }
        (tmp_path / "loom.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
        for agent in agents or [demo_agent()]:
            path = tmp_path / "agents" / f"{agent['name']}.yaml"
            path.write_text(yaml.safe_dump(agent, allow_unicode=True), encoding="utf-8")
        return tmp_path / "loom.yaml"

    return build


@pytest.fixture
def served(tmp_path: Path) -> Iterator[Callable[[Path], Served]]:
    started: list[Served] = []

    def start(config: Path) -> Served:
        server = Served(config, tmp_path.parent / f"{tmp_path.name}-console.log")
        started.append(server)
        return server

    yield start
    for server in started:
        if server.process.poll() is None:
            server.process.kill()
            server.process.wait()


def rewrite(path: Path, change: Callable[[dict[str, Any]], None]) -> None:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")


def test_a_change_is_served_by_a_new_process_and_data_does_not_reload(
    demo: ConfigFactory, served: Callable[[Path], Served]
) -> None:
    config = demo(storage={"events": {"backend": "jsonl", "path": "data"}})
    server = served(config)
    server.ready()
    assert server.agents() == {"demo": "Répond aux questions de calcul."}

    # Un run écrit son journal sous data/ : rien ne se recharge.
    result = server.call(
        "POST", "/agents/demo/runs", {"message": "Combien font 12 fois 7, plus 3 ?"}
    )
    assert result["status"] == "completed"
    assert any((config.parent / "data").rglob("*.jsonl"))
    # Un fichier qui apparaît et disparaît dans le même lot n'a rien changé.
    brouillon = config.parent / "agents" / "brouillon.yaml"
    brouillon.write_text("name: brouillon\n", encoding="utf-8")
    brouillon.unlink()
    time.sleep(CALME)
    assert server.said.count("Rechargement :") == 1, server.said

    agent = config.parent / "agents" / "demo.yaml"
    rewrite(agent, lambda data: data.update(description="Calcule, version 2."))
    # Lu au plus près : la console guettée toutes les 5 ms.
    server.until(lambda: "Rechargé" in server.said, "pas rechargé", every=0.005)
    # Dès que la console le dit, l'ancien ne prend plus rien : chaque requête
    # va au nouveau, la toute première comprise.
    assert [server.agents()["demo"] for _ in range(20)] == ["Calcule, version 2."] * 20
    said = server.said
    assert "Rechargement : agents/demo.yaml modifié" in said
    assert "Rechargé : le nouveau process sert" in said
    # Le nouveau process a imprimé son bandeau.
    assert said.count("API REST   :") == 2

    assert server.stop() == 0
    # Plus personne ne sert : aucun process n'a survécu au superviseur.
    with pytest.raises(urllib.error.URLError):
        server.agents()


def test_a_broken_config_leaves_the_old_process_serving(
    demo: ConfigFactory, served: Callable[[Path], Served]
) -> None:
    config = demo()
    server = served(config)
    server.ready()
    agent = config.parent / "agents" / "demo.yaml"
    good = agent.read_text(encoding="utf-8")

    rewrite(agent, lambda data: data.update(tools=[{"python": "inexistant"}]))
    server.until(lambda: "Rechargement refusé" in server.said, "pas refusé")
    assert "Config refusée : Référence 'inexistant' introuvable" in server.said
    assert server.agents() == {"demo": "Répond aux questions de calcul."}

    agent.write_text(good.replace("Répond aux questions", "Répond enfin aux questions"))
    server.until(
        lambda: server.agents()["demo"] == "Répond enfin aux questions de calcul.",
        "pas rechargé après la correction",
    )

    # La config qui passe en prod : refusée, comme au lancement.
    root = config.read_text(encoding="utf-8")
    refused = server.said.count("Rechargement refusé")
    config.write_text(f"profile: prod\n{root}", encoding="utf-8")
    server.until(lambda: server.said.count("Rechargement refusé") > refused, "prod pas refusé")
    assert "Config refusée : --reload est refusé en profil prod" in server.said
    assert server.agents() == {"demo": "Répond enfin aux questions de calcul."}
    assert server.stop() == 0


def test_a_folder_that_starts_being_watched_is_watched(
    demo: ConfigFactory, served: Callable[[Path], Served], tmp_path: Path
) -> None:
    """Des prompts déplacés hors du dossier de la config : le guetteur les suit."""
    config = demo()
    server = served(config)
    server.ready()
    shared = tmp_path.parent / f"{tmp_path.name}-prompts"
    shared.mkdir()
    (shared / "demo.md").write_text("Tu calcules, ailleurs.", encoding="utf-8")
    rewrite(config, lambda data: data.update(prompts_dir=str(shared)))
    server.until(lambda: f"surveille {tmp_path}, {shared}" in server.said, "pas suivi")
    done = server.said.count("Rechargé")
    (shared / "demo.md").write_text("Tu calcules, ailleurs, autrement.", encoding="utf-8")
    server.until(lambda: server.said.count("Rechargé") > done, "le nouveau dossier n'est pas vu")
    assert "demo.md modifié" in server.said
    assert server.stop() == 0


def test_a_run_in_flight_ends_in_the_old_process(
    demo: ConfigFactory, served: Callable[[Path], Served]
) -> None:
    script: list[dict[str, Any]] = [
        {"tool_calls": [{"name": "attendre", "arguments": {"secondes": 4}}]},
        {"text": "C'est fait."},
    ]
    config = demo(
        imports=["outils_acces", "outils_lents"],
        models=[{"id": "FAKE", "sdk": "fake", "model": "fake-1", "params": {"script": script}}],
        storage={"events": {"backend": "jsonl", "path": "data"}},
        agents=[demo_agent(tools=[{"python": "attendre", "side_effects": "none"}])],
    )
    (config.parent / "outils_lents.py").write_text(LENT, encoding="utf-8")
    server = served(config)
    server.ready()
    run = server.call("POST", "/agents/demo/runs", {"message": "Attends.", "background": True})
    run_id = run["run_id"]
    server.until(
        lambda: server.call("GET", f"/runs/{run_id}")["status"] == "awaiting_tools",
        "le run n'attend pas son outil",
    )

    (config.parent / "prompts" / "demo.md").write_text("Tu attends, autrement.", encoding="utf-8")
    server.until(lambda: "Rechargé" in server.said, "pas rechargé")
    # Le nouveau sert tout de suite, et lit au journal le run de l'ancien.
    begun = time.monotonic()
    assert server.agents() == {"demo": "Répond aux questions de calcul."}
    assert time.monotonic() - begun < 1.0
    assert server.call("GET", f"/runs/{run_id}")["status"] == "awaiting_tools"

    # L'ancien le mène au bout avant de sortir.
    server.until(
        lambda: server.call("GET", f"/runs/{run_id}")["status"] == "completed",
        "le run de l'ancien process n'est pas allé au bout",
    )
    assert server.call("GET", f"/runs/{run_id}")["text"] == "C'est fait."
    assert server.stop() == 0


def test_a_killed_supervisor_takes_its_processes_along(
    demo: ConfigFactory, served: Callable[[Path], Served]
) -> None:
    """Un ``kill -9`` du superviseur : ses process le voient et s'arrêtent, le port se libère."""
    server = served(demo())
    server.ready()
    server.process.kill()
    server.process.wait(timeout=DELAI)
    limit = time.monotonic() + DELAI
    while time.monotonic() < limit:
        try:
            server.agents()
        except urllib.error.URLError, ConnectionError:
            break
        time.sleep(0.1)
    else:
        raise AssertionError(f"un process sert encore — console :\n{server.said}")
    server.until(lambda: "Le superviseur a disparu" in server.said, "rien dit")
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", server.port))


def test_a_refused_first_start_exits_with_2(
    demo: ConfigFactory, served: Callable[[Path], Served]
) -> None:
    server = served(demo(agents=[demo_agent(tools=[{"python": "inexistant"}])]))
    code = server.process.wait(timeout=DELAI)
    assert code == 2
    assert "Config refusée : Référence 'inexistant' introuvable" in server.said
    assert "Le serveur n'a pas démarré." in server.said
