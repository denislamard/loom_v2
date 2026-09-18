# SPDX-License-Identifier: Apache-2.0
"""Phase 1.6 : le même agent par les trois accès.

    uv run --extra http --extra mcp python examples/j1/acces.py

L'agent ``demo`` (config ``examples/j1/demo/``) répond à la même question
trois fois : par l'API Python, par l'API REST — un vrai serveur, sur un port
libre de la machine — et par le serveur MCP, client et serveur dans le même
process. Les trois journaux sont ensuite comparés : même suite d'événements,
même réponse.

Le modèle est simulé (``sdk: fake``) : ni clé, ni réseau. Les journaux sont
écrits en JSONL dans ``examples/j1/demo/data/`` (ignoré par git), ce qui
permet aux trois accès de partager le même stockage.
"""

import argparse
import asyncio
import json
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

from loom_ia.access import Loom
from loom_ia.core.events import Event
from loom_ia.core.model import RunId, TextDelta

CONFIG = Path(__file__).parent / "demo" / "loom.yaml"
QUESTION = "Combien font 12 fois 7, plus 3 ?"
# Les appels vont sur la boucle locale : pas de proxy.
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Le même agent par les trois accès")
    parser.add_argument("--config", type=Path, default=CONFIG)
    args = parser.parse_args(argv)

    runs: dict[str, RunId] = {}
    print("— Accès Python —")
    runs["python"] = await by_python(args.config)
    print("\n— Accès REST —")
    runs["rest"] = await asyncio.to_thread(by_rest, args.config)
    print("\n— Accès MCP —")
    runs["mcp"] = await by_mcp(args.config)

    print("\n— Comparaison des journaux —")
    async with Loom.from_config(args.config) as loom:
        journals = {name: await loom.events(run_id) for name, run_id in runs.items()}
    kinds = {name: [event.type for event in events] for name, events in journals.items()}
    for name, types in kinds.items():
        print(f"{name:<7}: {len(types)} événements, {runs[name]}")
    same = len({tuple(types) for types in kinds.values()}) == 1
    print("\nMême suite d'événements par les trois accès." if same else "\nJournaux différents !")
    print(" → " + " ".join(kinds["python"]))
    return 0 if same else 1


async def by_python(config: Path) -> RunId:
    """``loom.run()`` puis ``loom.stream()`` : la façade, en direct."""
    async with Loom.from_config(config) as loom:
        result = await loom.run("demo", QUESTION)
        print(f"run()    : {result.text} ({result.status}, {result.iterations} itérations)")

        print("stream() : ", end="", flush=True)
        async for item in loom.stream("demo", QUESTION):
            if isinstance(item, TextDelta):
                print(item.text, end="", flush=True)
            elif isinstance(item, Event) and item.type == "tool.called":
                print(" [outil] ", end="", flush=True)
        print()
        return result.run_id


def by_rest(config: Path) -> RunId:
    """Un vrai serveur uvicorn, interrogé avec la librairie standard."""
    import uvicorn

    from loom_ia.access.http import create_app

    port = free_port()
    loom = Loom.from_config(config)
    settings = uvicorn.Config(
        create_app(loom, own=True), host="127.0.0.1", port=port, log_level="warning"
    )
    server = uvicorn.Server(settings)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}/v1"
    try:
        while not server.started:
            time.sleep(0.05)
        agents = fetch(f"{base}/agents")
        print(f"agents   : {', '.join(agent['name'] for agent in agents)}")
        result = fetch(f"{base}/agents/demo/runs", payload={"message": QUESTION})
        print(f"POST run : {result['text']} ({result['status']})")
        names = sse(f"{base}/runs/{result['run_id']}/events")
        print(f"SSE      : {len(names)} événements, du {names[0]} au {names[-1]}")
        return RunId(result["run_id"])
    finally:
        server.should_exit = True
        thread.join(timeout=5)


async def by_mcp(config: Path) -> RunId:
    """Client et serveur MCP dans le même process, sans stdio."""
    from mcp.shared.memory import create_connected_server_and_client_session as connected
    from mcp.types import TextContent

    from loom_ia.access.mcp_server import create_server

    async with Loom.from_config(config) as loom:
        async with connected(create_server(loom)) as client:
            listed = await client.list_tools()
            print(f"outils   : {', '.join(tool.name for tool in listed.tools)}")
            answer = await client.call_tool("demo", {"message": QUESTION})
            for part in answer.content:
                if isinstance(part, TextContent):
                    print(f"call     : {part.text}")
            result = answer.structuredContent or {}
            print(f"statut   : {result.get('status')}")
            return RunId(str(result["run_id"]))


# --- Petits utilitaires HTTP, sans dépendance --------------------------------


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def fetch(url: str, payload: dict[str, Any] | None = None) -> Any:
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    request.add_header("Content-Type", "application/json")
    with OPENER.open(request, timeout=30) as response:
        return json.loads(response.read().decode())


def sse(url: str) -> list[str]:
    """Noms des événements lus sur un flux SSE, jusqu'à sa fin."""
    names: list[str] = []
    with OPENER.open(urllib.request.Request(url), timeout=30) as response:
        for raw in response:
            line = raw.decode().strip()
            if line.startswith("event:"):
                names.append(line.removeprefix("event:").strip())
    return names


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
