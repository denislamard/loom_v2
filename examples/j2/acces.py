# SPDX-License-Identifier: Apache-2.0
"""Phase 2.5 : l'assistant par les trois accès, avec une photo jointe.

    uv run --extra http --extra mcp python examples/j2/acces.py
    uv run --extra http --extra mcp python examples/j2/acces.py --image autre.png

L'agent ``assistant`` (config ``examples/j2/assistant/``, modèles simulés)
reçoit trois fois la même demande, avec la même photo :

- par l'API Python : ``loom.stream()`` montre le déroulé en direct, sous-agent
  compris ;
- par l'API REST : un vrai serveur, sur un port libre de la machine ; la
  photo part en ``multipart/form-data``, puis le flux SSE du run montre aussi
  les événements du sous-run ;
- par le serveur MCP, client et serveur dans le même process : la photo part
  en base64 dans l'argument ``attachments``, et le déroulé arrive en
  notifications de progression.

Les trois arbres de runs sont ensuite relus dans le journal et comparés :
même suite d'événements, sous-run compris, et même réponse.

Les réponses des modèles simulés sont écrites d'avance : elles décrivent un
carré bleu, quelle que soit la photo. Par défaut, la photo est
``examples/j2/files/photo.jpg``. Le journal et les fichiers rangés vont dans
``examples/j2/assistant/data/`` (ignoré par git).
"""

import argparse
import asyncio
import base64
import json
import socket
import sys
import threading
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from loom_ia.access import Loom
from loom_ia.access.progress import Progress
from loom_ia.core.events import Event
from loom_ia.core.model import Attachment, RunId, TextDelta

HERE = Path(__file__).parent
CONFIG = HERE / "assistant" / "loom.yaml"
PHOTO = HERE / "files" / "photo.jpg"
AGENT = "assistant"
QUESTION = (
    "Que montre la photo jointe ? Et au passage : quelle heure est-il à Paris, et "
    "combien d'heures y a-t-il entre le 1er septembre et le 31 décembre 2026 ?"
)
# Les appels vont sur la boucle locale : pas de proxy.
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="L'assistant par les trois accès")
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--image", type=Path, default=PHOTO, help="image à joindre")
    args = parser.parse_args(argv)
    try:
        photo = Attachment.from_path(args.image)
    except OSError as error:
        print(f"Image illisible : {error}", file=sys.stderr)
        return 2
    print(f"> {QUESTION}\n  [image jointe : {photo.name}, {len(photo.data)} octets]")

    runs: dict[str, RunId] = {}
    print("\n— Accès Python —")
    runs["python"] = await by_python(args.config, photo)
    print("\n— Accès REST —")
    runs["rest"] = await asyncio.to_thread(by_rest, args.config, photo)
    print("\n— Accès MCP —")
    runs["mcp"] = await by_mcp(args.config, photo)

    print("\n— Comparaison des journaux —")
    async with Loom.from_config(args.config) as loom:
        trees = {name: await loom.events(run_id) for name, run_id in runs.items()}
        answers = {name: (await loom.result(run_id)).text for name, run_id in runs.items()}
    kinds = {name: [event.type for event in events] for name, events in trees.items()}
    for name, events in trees.items():
        agents = list(dict.fromkeys(event.agent for event in events))
        print(
            f"{name:<7}: {len(events)} événements, {len(agents)} runs "
            f"({', '.join(filter(None, agents))}), {runs[name]}"
        )
    same = len({tuple(types) for types in kinds.values()}) == 1
    same_answer = len(set(answers.values())) == 1
    print("\nMême arbre de runs par les trois accès." if same else "\nJournaux différents !")
    print("Même réponse par les trois accès." if same_answer else "Réponses différentes !")
    print(f"\n{answers['python']}")
    return 0 if same and same_answer else 1


async def by_python(config: Path, photo: Attachment) -> RunId:
    """``loom.stream()`` : le texte du modèle et le déroulé, sous-run compris."""
    progress = Progress()
    run_id: RunId | None = None
    # Du texte du modèle est en cours : la ligne suivante doit en partir.
    writing = False
    async with Loom.from_config(config) as loom:
        async for item in loom.stream(AGENT, QUESTION, attachments=[photo]):
            if isinstance(item, TextDelta):
                print(item.text if writing else f"  {item.text}", end="", flush=True)
                writing = True
            elif isinstance(item, Event):
                run_id = run_id or item.run_id
                if (line := progress.line(item)) is not None:
                    print(f"\n  {line}" if writing else f"  {line}")
                    writing = False
    if writing:
        print()
    assert run_id is not None
    return run_id


def by_rest(config: Path, photo: Attachment) -> RunId:
    """Un vrai serveur uvicorn ; la photo en multipart, le déroulé en SSE."""
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
        result = post_form(f"{base}/agents/{AGENT}/runs", {"message": QUESTION}, [photo])
        print(f"  POST run : {result['status']}, {len(result['artifacts'])} fichier(s) du run")
        events = sse(f"{base}/runs/{result['run_id']}/events")
        runs = list(dict.fromkeys((event["run_id"], event["agent"]) for _, event in events))
        print(f"  SSE      : {len(events)} événements, dont ceux de {len(runs) - 1} sous-run(s)")
        for run_id, agent in runs[1:]:
            print(f"             sous-run {agent} ({run_id})")
        return RunId(result["run_id"])
    finally:
        server.should_exit = True
        thread.join(timeout=5)


async def by_mcp(config: Path, photo: Attachment) -> RunId:
    """Client et serveur MCP dans le même process ; la photo en base64."""
    from mcp.shared.memory import create_connected_server_and_client_session as connected

    from loom_ia.access.mcp_server import create_server

    async def on_progress(progress: float, total: float | None, message: str | None) -> None:
        print(f"  {int(progress):>2} {message}")

    image = {
        "type": "image",
        "data": base64.b64encode(photo.data).decode(),
        "name": photo.name or "photo",
    }
    async with Loom.from_config(config) as loom:
        async with connected(create_server(loom)) as client:
            answer = await client.call_tool(
                AGENT,
                {"message": QUESTION, "attachments": [image]},
                progress_callback=on_progress,
            )
            result = answer.structuredContent or {}
            print(f"  statut   : {result.get('status')}, {len(result['artifacts'])} fichier(s)")
            return RunId(str(result["run_id"]))


# --- Petits utilitaires HTTP, sans dépendance --------------------------------


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def post_form(url: str, fields: dict[str, str], files: list[Attachment]) -> Any:
    """``POST`` en ``multipart/form-data`` : les champs, puis un fichier par partie."""
    boundary = uuid.uuid4().hex
    parts: list[bytes] = []
    for name, value in fields.items():
        head = f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
        parts.append(head.encode() + value.encode() + b"\r\n")
    for file in files:
        head = (
            f'Content-Disposition: form-data; name="attachments"; filename="{file.name}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        )
        parts.append(head.encode() + file.data + b"\r\n")
    body = b"".join(f"--{boundary}\r\n".encode() + part for part in parts)
    body += f"--{boundary}--\r\n".encode()
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    with OPENER.open(request, timeout=30) as response:
        return json.loads(response.read().decode())


def sse(url: str) -> list[tuple[str, dict[str, Any]]]:
    """Événements lus sur un flux SSE, jusqu'à sa fin : nom et contenu."""
    events: list[tuple[str, dict[str, Any]]] = []
    name = ""
    with OPENER.open(urllib.request.Request(url), timeout=30) as response:
        for raw in response:
            line = raw.decode().strip()
            if line.startswith("event:"):
                name = line.removeprefix("event:").strip()
            elif line.startswith("data:"):
                events.append((name, json.loads(line.removeprefix("data:"))))
    return events


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
