# SPDX-License-Identifier: Apache-2.0
"""``loom-mem-gpu`` : une petite application à part, qui garde BGE-M3 et son reranker en VRAM.

    loom-mem-gpu [serve] [--device cuda] [--offline] [--port 8765] [--config f.yaml] …
    loom-mem-gpu serve --check [--env]
    loom-mem-gpu status  [--url http://127.0.0.1:8765] [--token-env VAR]

Chaque réglage a sa variable (``port`` → ``LOOM_MEM_GPU_PORT``) : une option donnée
l'emporte sur la variable, la variable sur ``--config``. ``serve --check --env``
écrit les réglages en lignes ``Environment=`` pour l'unité systemd.

Les process loom lui confient leurs calculs (``models.server`` de
``loom.memory``) au lieu de charger les modèles chacun. Elle ne lit jamais
``loom.yaml`` et ne voit aucune base : elle calcule des vecteurs et des scores
pour les textes qu'on lui envoie, et n'en garde rien.

Elle n'importe de loom que la mémoire (modèles, protocole, réglages des
modèles) : un contrat import-linter le vérifie. Conception :
``docs/loom-mem-gpu.md``.

Codes de sortie : 0 arrêt demandé, 1 ``status`` sans application prête,
2 réglages refusés ou dépendance absente, 3 port déjà pris, 4 modèles non chargés.
"""

import argparse
import json
import logging
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final

from pydantic import ValidationError

from loom_ia.tools_builtin.memory.protocol import APP, Health
from loom_ia.tools_builtin.memory.server.app import EXIT_SETTINGS, preflight, run
from loom_ia.tools_builtin.memory.server.settings import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    ServiceSettings,
    SettingsError,
    as_environment,
    load_settings,
)

PROGRAM: Final = APP
EXIT_NOT_READY: Final = 1
STATUS_TIMEOUT: Final = 5.0
_COMMANDS: Final = ("serve", "status")


def main(argv: Sequence[str] | None = None) -> int:
    given = list(sys.argv[1:] if argv is None else argv)
    # ``serve`` est la commande par défaut : ``loom-mem-gpu --device cuda`` sert.
    if not given or (given[0] not in _COMMANDS and given[0] not in ("-h", "--help")):
        given.insert(0, "serve")
    args = _parser().parse_args(given)
    logging.basicConfig(level=getattr(logging, args.log_level), format=log_format(os.environ))
    if args.command == "status":
        return status(args.url, args.token_env)
    options = {
        key: value
        for key, value in vars(args).items()
        if key not in ("command", "config", "log_level", "check", "env") and value is not None
    }
    try:
        settings = load_settings(args.config, options, os.environ)
    except SettingsError as exc:
        print(f"{PROGRAM} : {exc}", file=sys.stderr)
        return EXIT_SETTINGS
    if args.check or args.env:
        return check(settings, environment=args.env)
    return run(settings)


def check(settings: ServiceSettings, *, environment: bool = False) -> int:
    """``serve --check`` : les réglages et les dépendances, sans rien charger ni ouvrir.

    Avec ``environment`` (``--env``), les réglages en lignes ``Environment=`` au lieu
    du message.
    """
    problem = preflight(settings)
    if problem is not None:
        print(f"{PROGRAM} : {problem}", file=sys.stderr)
        return EXIT_SETTINGS
    if environment:
        print("\n".join(as_environment(settings)))
        return 0
    print(
        f"{PROGRAM} : réglages valides — {settings.host}:{settings.port}, "
        f"device {settings.device}, {'fp16' if settings.fp16 else 'float32'}"
        f"{', hors ligne' if settings.offline else ''}"
    )
    return 0


def log_format(environ: Mapping[str, str]) -> str:
    """Le format du journal ; sous systemd (``JOURNAL_STREAM``), sans l'heure, déjà notée."""
    if "JOURNAL_STREAM" in environ:
        return "%(levelname)s %(name)s : %(message)s"
    return "%(asctime)s %(levelname)s %(name)s : %(message)s"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        description="Garde BGE-M3 et son reranker en VRAM et les sert aux process loom.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="charge les modèles et les sert (par défaut)")
    # Sans valeur par défaut : seule une option donnée l'emporte sur le fichier --config.
    serve.add_argument("--config", type=Path, help="fichier YAML, aux mêmes clés que les options")
    serve.add_argument("--host", help=f"adresse d'écoute, boucle locale (défaut : {DEFAULT_HOST})")
    serve.add_argument("--port", type=int, help=f"port (défaut : {DEFAULT_PORT})")
    serve.add_argument("--device", help="cuda (défaut), cuda:N, cpu ou auto")
    serve.add_argument(
        "--no-fp16", dest="fp16", action="store_const", const=False, help="float32 sur la carte"
    )
    serve.add_argument(
        "--offline",
        action="store_const",
        const=True,
        help="modèles lus dans le cache local de Hugging Face seulement",
    )
    serve.add_argument("--embedder", help="modèle BGE-M3 (nom Hugging Face ou dossier)")
    serve.add_argument("--reranker", help="reranker à classifieur de paires")
    serve.add_argument("--token-env", help="variable qui porte le jeton exigé des clients")
    serve.add_argument(
        "--max-vram-fraction", type=float, help="part de la carte permise, entre 0 et 1"
    )
    serve.add_argument(
        "--check",
        action="store_true",
        help="valide les réglages et les dépendances, sans charger ni écouter (code 0 ou 2)",
    )
    serve.add_argument(
        "--env",
        action="store_true",
        help="comme --check, puis écrit les réglages en lignes Environment= (unité systemd)",
    )
    status_parser = commands.add_parser("status", help="dit si l'application est prête")
    status_parser.add_argument(
        "--url", default=f"http://{DEFAULT_HOST}:{DEFAULT_PORT}", help="adresse de l'application"
    )
    status_parser.add_argument("--token-env", help="variable qui porte le jeton")
    for command in (serve, status_parser):
        command.add_argument(
            "--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR")
        )
    return parser


def status(url: str, token_env: str | None) -> int:
    """Interroge ``/v1/health`` et affiche l'état ; 0 si prête, 1 sinon."""
    request = urllib.request.Request(url.rstrip("/") + "/v1/health")
    if token_env is not None:
        token = os.environ.get(token_env, "").strip()
        if not token:
            print(f"{PROGRAM} : la variable {token_env} est absente ou vide", file=sys.stderr)
            return EXIT_SETTINGS
        request.add_header("Authorization", f"Bearer {token}")
    try:
        # Sans proxy : HTTP_PROXY détournerait aussi la boucle locale (le client de loom
        # les ignore de même, ``trust_env=False``).
        direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with direct.open(request, timeout=STATUS_TIMEOUT) as response:
            body: bytes = response.read()
    except urllib.error.HTTPError as exc:
        body = exc.read()
        if exc.code != 503:
            print(f"{url} : HTTP {exc.code} — {_error(body)}", file=sys.stderr)
            return EXIT_NOT_READY
    except (urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        print(f"{url} : injoignable ({reason})", file=sys.stderr)
        return EXIT_NOT_READY
    try:
        health = Health.model_validate_json(body)
    except ValidationError:
        print(f"{url} : réponse illisible", file=sys.stderr)
        return EXIT_NOT_READY
    print(_describe(url, health))
    return 0 if health.state == "ready" else EXIT_NOT_READY


def _error(body: bytes) -> str:
    try:
        found: Any = json.loads(body)
        return str(found["error"])
    except ValueError, KeyError, TypeError:
        return body.decode(errors="replace")[:200]


def _describe(url: str, health: Health) -> str:
    if health.state != "ready":
        error = f" — {health.error}" if health.error else ""
        return f"{url} : {health.state}{error}"
    vram = f"{health.vram_used_mb} Mo" if health.vram_used_mb is not None else "—"
    precision = "fp16" if health.fp16 else "float32"
    lines = [
        f"{url} : ready (protocole {health.protocol}, loom {health.loom})",
        f"  embedder  {health.embedder} (dim {health.dim}, {health.algo})",
        f"  reranker  {health.reranker}",
        f"  device    {health.device}, {precision}, VRAM réservée {vram}",
    ]
    return "\n".join(lines)
