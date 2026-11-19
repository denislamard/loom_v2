"""Point d'entrée : `loom-chat` (ou `python -m loom_chat`), lancé depuis le dossier de l'agent."""

import argparse
import os
import sys
from collections.abc import Mapping
from pathlib import Path

from loom_ia.config.env_file import load_env_file
from loom_ia.config.errors import ConfigError
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from loom_chat.settings import BOOT, ENV_KEY, ChatSettings, ModelKind, choose_model, create_project, existing, load, pick_user, project_files

# Invite de saisie : le chevron des shells modernes (U+276F).
PROMPT = chr(0x276F)
# Code de sortie d'un abandon au clavier (128 + SIGINT), comme le shell.
ABORTED = 130


def _tilde(path: Path) -> str:
    """Un chemin sous le dossier personnel écrit avec ``~``, plus court à lire."""
    try:
        return "~/" + str(path.relative_to(Path.home()))
    except ValueError:
        return str(path)


def env_help(folder: Path, kind: ModelKind, environ: Mapping[str, str]) -> Group:
    """Ce qu'il faut mettre dans le .env du dossier, et si c'est déjà fait (jamais la valeur).

    Args:
        folder: Le dossier de l'agent.
        kind: Le modèle des fichiers créés.
        environ: L'environnement, .env déjà lu.
    """
    env = folder / ".env"
    where = Text.assemble(("Fichier .env", "bold"), " : ", (_tilde(env), "bold cyan"))
    where.append("  trouvé" if env.is_file() else "  à créer", style="dim")
    where.append(" — à côté de loom-boot.yaml, à ne jamais commiter.", style="dim")
    line = Text.assemble(("  ", ""), (ENV_KEY, "bold"), ("=sk-ant-…", "dim"))
    if kind == "anthropic":
        line.append(
            "    ✓ déjà définie" if environ.get(ENV_KEY) else "    à renseigner avant le premier message", style="green" if environ.get(ENV_KEY) else "yellow"
        )
        note = Text("Votre clé d'API Anthropic (console.anthropic.com) ; loom la lit au lancement, sans jamais l'afficher.", style="dim")
    else:
        note = Text(
            "Rien d'obligatoire pour la démo. Pour passer à Claude : mettez cette ligne dans .env, "
            "puis dans loom.yaml remplacez le modèle factice par sdk: anthropic (model: claude-haiku-5-5, api_key_env: ANTHROPIC_API_KEY).",
            style="dim",
        )
    return Group(where, line, note)


def confirm_creation(folder: Path, kind: ModelKind, console: Console | None = None) -> bool:
    """Présente les fichiers loom à créer dans le dossier de l'agent, et demande l'accord.

    Args:
        folder: Le dossier de l'agent.
        kind: Le modèle des fichiers créés.
        console: Où écrire ; la sortie standard par défaut.

    Returns:
        Vrai si l'utilisateur accepte (Entrée ou « o »).

    Raises:
        EOFError: Ctrl+D à la question.
        KeyboardInterrupt: Ctrl+C à la question.
    """
    console = console or Console(highlight=False)
    files = Table.grid(padding=(0, 2))
    files.add_column(style="bold cyan", no_wrap=True)
    files.add_column(style="dim")
    roles = {
        BOOT: "l'amorce : où lire la config",
        "loom.yaml": "modèle, journal (data/), utilisateur et rôle",
        "agents/": "l'agent : son modèle, son prompt, ses outils",
        "prompts/": "le prompt système",
    }
    for name in project_files(kind):
        files.add_row(name, next((text for key, text in roles.items() if name.startswith(key)), ""))
    model = "Claude (facturé, clé ANTHROPIC_API_KEY)" if kind == "anthropic" else "factice : une démo jouée sans clé ni frais"
    body = Group(
        Text.assemble("Aucune config loom dans ", (_tilde(folder), "bold"), " : loom-chat va créer un agent prêt à l'emploi."),
        Text.assemble("Modèle : ", (model, "bold"), "."),
        Text(""),
        files,
        Text(""),
        env_help(folder, kind, os.environ),
        Text(""),
        Text("Ce sont des fichiers loom ordinaires : modifiez-les à votre guise, la console les relit à chaque lancement.", style="dim"),
    )
    console.print()
    console.print(Panel(body, title="[bold]loom-chat[/] · nouvel agent", title_align="left", border_style="cyan", padding=(1, 2), width=80))
    answer = console.input(f"Créer ces fichiers ? [bold cyan]\\[O/n][/] [bold cyan]{PROMPT}[/] ").strip().lower()
    return answer in {"", "o", "oui", "y", "yes"}


def main(argv: list[str] | None = None) -> int:
    """Lit le .env et la config loom du dossier courant (créée au besoin), puis ouvre la console.

    Args:
        argv: Les arguments ; ceux de la ligne de commande par défaut.

    Returns:
        Le code de sortie : 0, 1 si la création est refusée, 2 pour une erreur de lancement, 130 pour un abandon.
    """
    parser = argparse.ArgumentParser(prog="loom-chat", description="Console de chat pour loom-ia, lancée depuis le dossier de l'agent.")
    parser.add_argument("--env-file", type=Path, help="fichier .env à lire (sinon celui que désigne l'amorce, ou le .env du dossier courant)")
    parser.add_argument("--user", help="utilisateur de la config au nom duquel agir (sinon le seul déclaré)")
    parser.add_argument("--model", choices=["fake", "anthropic"], help="modèle des fichiers créés au premier lancement (sinon Claude si une clé est présente)")
    args = parser.parse_args(argv)
    console = Console(highlight=False)
    err = Console(stderr=True, highlight=False)

    folder = Path.cwd()
    boot = folder / BOOT
    env_file: Path | None = args.env_file.expanduser().resolve() if args.env_file else None
    if env_file is not None and not env_file.is_file():
        err.print(f"[red]✗[/] Fichier .env introuvable : {env_file}")
        return 2
    if env_file is None and not boot.is_file() and (folder / ".env").is_file():
        env_file = folder / ".env"  # sans amorce, loom ne saurait où chercher
    try:
        load_env_file(boot, env_file=env_file)  # une variable déjà définie n'est jamais remplacée ; aucune valeur n'est affichée
    except ConfigError as exc:
        err.print(f"[red]✗[/] {exc}")
        return 2

    if not boot.is_file():
        kind: ModelKind = args.model or choose_model(os.environ)
        if taken := existing(folder, kind):
            names = ", ".join(str(p.relative_to(folder)) for p in taken)
            err.print(f"[red]✗[/] Pas d'amorce {BOOT}, mais déjà {names} : ajoutez l'amorce, ou lancez loom-chat dans un dossier vide.")
            return 2
        if not sys.stdin.isatty():
            err.print(f"[red]✗[/] Aucune config loom ici ({BOOT}) : lancez loom-chat dans un terminal pour la créer.")
            return 2
        try:
            accepted = confirm_creation(folder, kind, console)
        except EOFError, KeyboardInterrupt:
            err.print("\nAbandon : rien n'a été créé.")
            return ABORTED
        if not accepted:
            console.print("Rien n'a été créé.")
            return 1
        create_project(folder, kind)
        console.print(f"[green]✓[/] Agent créé dans [bold]{_tilde(folder)}[/]\n")

    try:
        config = load(folder)
        user = pick_user(config, args.user)
    except (ConfigError, ValueError) as exc:
        err.print(f"[red]✗[/] Config inutilisable : {exc}")
        return 2

    from loom_chat.app import ChatApp  # import tardif : Textual n'est chargé que pour la console

    app = ChatApp(ChatSettings(config=config, user=user))
    app.run()
    return app.return_code or 0


if __name__ == "__main__":
    raise SystemExit(main())
