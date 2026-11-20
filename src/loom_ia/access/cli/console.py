# SPDX-License-Identifier: Apache-2.0
"""``loom console listen`` : une fausse console qui affiche ce que loom lui envoie (docs/analyse-exortateur-loom.md).

    loom console listen                 écoute sur ws://127.0.0.1:8766/loom
    loom console listen --raw           chaque message en JSON complet
    loom console listen --port 9000 --key-env MA_CLE

Elle contrôle la clé de l'instance (``--key-env``, ``LOOM_CONSOLE_KEY`` par
défaut), répond à la poignée de main et acquitte les lots d'événements, comme
la console. Dans la config de l'instance à observer, en profil ``dev`` :

    console:
      enable: true
      url: ws://127.0.0.1:8766/loom
      key_env: LOOM_CONSOLE_KEY

``--certfile`` et ``--keyfile`` la servent en ``wss://`` (certificat du
serveur, en PEM), pour éprouver le TLS. Elle demande l'extra ``console``.
"""

import argparse
import asyncio
import json
import os
import ssl
import sys
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Final, cast

from pydantic import JsonValue

if TYPE_CHECKING:
    from loom_ia.testing.console_server import Received

OK: Final = 0
"""Code de sortie : l'écoute s'est arrêtée normalement."""

REFUSED: Final = 2
"""Code de sortie : la demande est refusée (clé absente, extra absent, port pris)."""

DEFAULT_PORT: Final = 8766
"""Port d'écoute par défaut."""

DEFAULT_KEY_ENV: Final = "LOOM_CONSOLE_KEY"
"""Variable lue par défaut pour la clé attendue."""

_KIND: Final = 18
"""Largeur de la colonne du type, dans l'affichage."""


def add_actions(console: argparse.ArgumentParser, handler: Callable[[argparse.Namespace], int]) -> None:
    """Les actions de la commande ``console``."""
    actions = console.add_subparsers(dest="action", required=True)
    listen = actions.add_parser("listen", help="fausse console : affiche ce que loom envoie")
    listen.add_argument("--host", default="127.0.0.1", help="adresse d'écoute (127.0.0.1 par défaut)")
    listen.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"port d'écoute ({DEFAULT_PORT} par défaut)")
    listen.add_argument("--key-env", default=DEFAULT_KEY_ENV, help=f"variable qui porte la clé attendue ({DEFAULT_KEY_ENV} par défaut)")
    listen.add_argument("--raw", action="store_true", help="affiche chaque message en JSON complet")
    listen.add_argument("--certfile", type=Path, help="certificat du serveur (PEM) : écoute en wss://")
    listen.add_argument("--keyfile", type=Path, help="clé privée du certificat du serveur (PEM)")
    listen.set_defaults(handler=handler)


def run(args: argparse.Namespace, environ: Mapping[str, str] | None = None) -> int:
    """``loom console listen`` : écoute jusqu'à Ctrl-C.

    Args:
        args: Arguments de la commande.
        environ: Environnement où lire la clé ; à défaut, celui du process.

    Returns:
        ``OK`` à l'arrêt, ``REFUSED`` si l'écoute ne peut pas commencer.
    """
    variables = os.environ if environ is None else environ
    key = variables.get(args.key_env, "")
    if not key:
        print(f"loom console listen : la variable {args.key_env} est vide — y mettre la clé que l'instance présente (console.key_env)", file=sys.stderr)
        return REFUSED
    if (args.certfile is None) != (args.keyfile is None):
        print("loom console listen : --certfile et --keyfile vont ensemble", file=sys.stderr)
        return REFUSED
    try:
        from loom_ia.testing.console_server import FakeConsole
    except ImportError:
        print("loom console listen : websockets n'est pas installé (installer l'extra : loom-ia[console])", file=sys.stderr)
        return REFUSED
    context: ssl.SSLContext | None = None
    if args.certfile is not None:
        context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(certfile=args.certfile, keyfile=args.keyfile)
    printer = _Printer(raw=args.raw)
    fake = FakeConsole(key=key, host=args.host, port=args.port, ssl_context=context, watcher=printer)

    async def listen() -> None:
        try:
            await fake.start()
        except OSError as error:
            raise _NotListening(str(error)) from None
        print(f"En écoute sur {fake.url} — clé attendue : {args.key_env} · Ctrl-C pour arrêter", flush=True)
        try:
            await fake.serve_forever()
        finally:
            await fake.aclose()

    try:
        asyncio.run(listen())
    except _NotListening as error:
        print(f"loom console listen : écoute impossible sur {args.host}:{args.port} ({error})", file=sys.stderr)
        return REFUSED
    except KeyboardInterrupt:
        print("\nÉcoute arrêtée.", flush=True)
    return OK


class _NotListening(Exception):
    """Le port est pris, ou l'adresse invalide."""


class _Printer:
    """Ce que l'écouteur affiche, une ligne par chose reçue (ou le JSON complet avec ``--raw``)."""

    def __init__(self, *, raw: bool) -> None:
        self._raw = raw

    def __call__(self, item: Received) -> None:
        """Affiche une chose reçue."""
        for line in self.lines(item):
            print(line, flush=True)

    def lines(self, item: Received) -> list[str]:
        """Les lignes d'une chose reçue."""
        now = datetime.now().strftime("%H:%M:%S")
        if self._raw and item.envelope is not None:
            return [f"{now}  {item.kind}", json.dumps(item.envelope.model_dump(mode="json", exclude_none=True), ensure_ascii=False, indent=2)]
        payload = item.payload
        data: dict[str, JsonValue] = {} if payload is None else cast("dict[str, JsonValue]", payload.model_dump(mode="json"))
        match item.kind:
            case "hello":
                agents = cast("list[str]", data.get("agents") or [])
                return [
                    self._line(now, "hello", f"{data['instance_id']} / {data['worker_id']}  loom {data['loom_version']}  contrats {data['contracts']}"),
                    self._line(
                        "",
                        "",
                        f"client {data['tenant']} · agents : {', '.join(agents) or 'aucun'} · commandes : "
                        + ("oui" if data.get("commands") else "non")
                        + f" · export : {data['export_mode']}",
                    ),
                ]
            case "connected":
                return [self._line(now, "welcome", f"{item.detail} retenu")]
            case "refused":
                return [self._line(now, "refus", f"{item.peer} : {item.detail}")]
            case "closed":
                return [self._line(now, "fermeture", f"{item.peer}" + (f" ({item.detail})" if item.detail else ""))]
            case "events":
                events = cast("list[dict[str, JsonValue]]", data.get("events") or [])
                return [self._event(event) for event in events]
            case "ack":
                return [self._line(now, "ack", f"lot {item.detail[:8]}…")]
            case "session.deleted":
                return [self._line(now, "effacée", f"session {data['session']}")]
            case "command.result":
                said = f"{data['command_id']} : {data['outcome']}" + (f" — {data['reason']}" if data.get("reason") else "")
                return [self._line(now, "résultat", said)]
            case "file.data":
                part = f"morceau {data['part']}" + (" (dernier)" if data.get("last") else "")
                name = f" {data['name']}" if data.get("name") else ""
                return [self._line(now, "fichier", f"{data['command_id']}{name} · {part}")]
            case "bye":
                return [self._line(now, "bye", str(data.get("reason", "")))]
            case "error":
                return [self._line(now, "erreur", f"{data['code']} — {data['message']}")]
            case "invalid":
                return [self._line(now, "invalide", item.detail)]
            case _:
                return [self._line(now, item.kind, json.dumps(data, ensure_ascii=False))]

    def _event(self, event: dict[str, JsonValue]) -> str:
        """Un événement sur une ligne : heure, type, agent, statut, run, session, facettes."""
        stamp = str(event.get("ts", ""))
        try:
            shown = datetime.fromisoformat(stamp).astimezone().strftime("%H:%M:%S")
        except ValueError:
            shown = stamp[:8]
        facets = cast("dict[str, JsonValue]", event.get("facets") or {})
        details = " ".join(f"{name}={_shown(value)}" for name, value in facets.items() if value is not None)
        run = str(event.get("run_id", ""))[:8]
        parts = [str(event.get("agent") or "-"), str(event.get("status", "")), f"run {run}…", f"{event.get('session_id')}#{event.get('seq')}"]
        return self._line(shown, str(event.get("type", "")), "  ".join(parts) + (f"  {details}" if details else ""))

    @staticmethod
    def _line(stamp: str, kind: str, text: str) -> str:
        return f"{stamp:<8}  {kind:<{_KIND}} {text}".rstrip()


def _shown(value: JsonValue) -> str:
    """Une facette lisible : un nombre à virgule arrondi au dixième."""
    if isinstance(value, float):
        return f"{value:.1f}"
    return str(value)
