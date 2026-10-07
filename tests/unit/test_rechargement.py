# SPDX-License-Identifier: Apache-2.0
"""``loom serve --reload`` (#48, J6.4a) : ce qui est surveillé, ce qui est dit, ce qui est refusé.

La relève elle-même — deux process, une socket — est éprouvée avec de vrais
sous-process dans ``tests/integration/test_rechargement_process.py``.
"""
# pyright: reportPrivateUsage=false

import importlib
import multiprocessing
import os
import sys
import threading
import time
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any, cast

import pytest
from conftest import ConfigFactory, demo_agent

pytest.importorskip("fastapi", reason="extra 'http' absent")
pytest.importorskip("watchfiles", reason="extra 'http' absent")

from watchfiles import Change

from loom_ia.access import Loom
from loom_ia.access.cli import main
from loom_ia.access.http.serve import (
    DEAF,
    Watched,
    _mounted,
    _Serving,
    _Taking,
    _trace,
    changed,
    watched,
)
from loom_ia.config import ConfigError, load_config

# Le module, que le paquet ombre par sa fonction ``serve``.
http_serve = importlib.import_module("loom_ia.access.http.serve")

# --- Ce qui est surveillé ------------------------------------------------------------


def test_the_config_folder_is_watched_but_not_what_loom_writes(demo: ConfigFactory) -> None:
    config = load_config(
        demo(
            storage={
                "events": {"backend": "jsonl", "path": "data"},
                "idempotency": {"backend": "sqlite", "path": "cles.db"},
            }
        )
    )
    base = config.base_dir
    assert base is not None
    spec = watched(config)
    assert spec.roots == (base,)
    assert spec.dirs == (base / "data", base / "data" / ".artifacts")
    assert spec.files == (base / "cles.db",)

    for counted in ("loom.yaml", "agents/demo.yaml", "prompts/demo.md", "outils_acces.py"):
        assert spec.counts(base / counted), counted
    # Un nom qui commence comme un dossier de données n'en est pas.
    assert spec.counts(base / "database.yaml")
    assert spec.counts(base / "cles.db.txt")
    for written in (
        "data/default/s.jsonl",
        "data/.artifacts/default/s/rapport.md",
        "cles.db",
        "cles.db-wal",
        "cles.db-shm",
        "cles.db-journal",
    ):
        assert not spec.counts(base / written), written


def test_noise_is_not_watched(demo: ConfigFactory) -> None:
    spec = watched(load_config(demo()))
    base = spec.roots[0]
    for noise in (
        "__pycache__/outils_acces.cpython-314.pyc",
        "outils_acces.pyc",
        ".git/HEAD",
        ".venv/lib/x.py",
        "agents/.demo.yaml.swp",
        "agents/demo.yaml~",
        "prompts/.#demo.md",
        ".ruff_cache/x",
        "node_modules/x.js",
    ):
        assert not spec.counts(base / noise), noise
    assert not spec.counts(base.parent / "ailleurs.yaml")


def test_a_noise_name_above_the_config_folder_does_not_hide_it(tmp_path: Path) -> None:
    """Le bruit se cherche sous le dossier surveillé, pas dans le chemin qui y mène."""
    base = tmp_path / ".venv" / "projet"
    spec = Watched(roots=(base,), dirs=(), files=())
    assert spec.counts(base / "loom.yaml")


def test_each_client_storage_is_left_out(demo: ConfigFactory) -> None:
    config = load_config(
        demo(
            tenants=[
                {"id": "dupont"},
                {
                    "id": "martin",
                    "storage": {"events": {"backend": "sqlite", "path": "martin/journal.db"}},
                },
            ]
        )
    )
    base = config.base_dir
    assert base is not None
    spec = watched(config)
    assert base / "martin" / "journal.db" in spec.files
    assert base / "martin" / ".artifacts" in spec.dirs
    assert not spec.counts(base / "martin" / "journal.db-wal")
    assert spec.counts(base / "martin" / "notes.md")


def test_folders_outside_the_config_are_watched_too(demo: ConfigFactory, tmp_path: Path) -> None:
    shared = tmp_path.parent / f"{tmp_path.name}-partage"
    (shared / "prompts").mkdir(parents=True)
    (shared / "prompts" / "demo.md").write_text("Tu calcules.", encoding="utf-8")
    config = load_config(demo(prompts_dir=str(shared / "prompts")))
    spec = watched(config)
    assert spec.roots == (tmp_path, shared / "prompts")
    assert spec.counts(shared / "prompts" / "demo.md")


def test_data_that_contains_the_config_folder_is_refused(demo: ConfigFactory) -> None:
    config = load_config(demo(storage={"events": {"backend": "jsonl", "path": "."}}))
    with pytest.raises(ConfigError, match=r"contient .*surveillé"):
        watched(config)


def test_what_is_present_leaves_out_data_and_noise(demo: ConfigFactory) -> None:
    path = demo(storage={"events": {"backend": "jsonl", "path": "data"}})
    base = path.parent
    (base / "data" / ".artifacts").mkdir(parents=True)
    (base / "data" / "s.jsonl").write_text("{}\n", encoding="utf-8")
    (base / "data" / ".artifacts" / "rapport.md").write_text("x", encoding="utf-8")
    (base / "__pycache__").mkdir()
    (base / "__pycache__" / "outils_acces.cpython-314.pyc").write_bytes(b"")
    spec = watched(load_config(path))
    assert {p.relative_to(base).as_posix() for p in spec.present()} == {
        "loom.yaml",
        "agents/demo.yaml",
        "prompts/demo.md",
        "outils_acces.py",
    }


def test_the_watched_line_names_what_is_left_out(demo: ConfigFactory) -> None:
    config = load_config(
        demo(
            storage={
                "events": {"backend": "jsonl", "path": "data"},
                "idempotency": {"backend": "sqlite", "path": "data/cles.db"},
            }
        )
    )
    spec = watched(config)
    # Les fichiers sous data/ y sont déjà : la ligne ne les répète pas.
    assert spec.describe() == f"{spec.roots[0]} (sauf data/)"
    assert Watched(roots=spec.roots, dirs=(), files=()).describe() == str(spec.roots[0])


# --- Ce qui est dit ------------------------------------------------------------------


def test_changes_are_read_on_the_disk(tmp_path: Path) -> None:
    a, b, c, tmp = (tmp_path / n for n in ("loom.yaml", "agents/b.yaml", "outils.py", "sedX1"))
    before = frozenset({a, c})
    after = frozenset({a, b})
    told = changed(
        {
            # Un éditeur qui enregistre par renommage : « ajouté », mais il était là.
            (Change.added, str(a)),
            (Change.added, str(b)),
            (Change.deleted, str(c)),
            # Apparu puis disparu dans le même lot : rien n'a changé.
            (Change.added, str(tmp)),
        },
        before,
        after,
        tmp_path,
    )
    assert told == "agents/b.yaml ajouté, loom.yaml modifié, outils.py supprimé"
    assert changed({(Change.added, str(tmp))}, before, after, tmp_path) == ""


def test_a_long_list_of_changes_is_counted(tmp_path: Path) -> None:
    paths = [tmp_path / f"prompts/p{i}.md" for i in range(8)]
    told = changed(
        {(Change.modified, str(p)) for p in paths}, frozenset(paths), frozenset(paths), tmp_path
    )
    assert told.endswith("prompts/p4.md modifié, et 3 autre(s)")
    assert told.count("modifié") == 5


def test_an_error_in_the_user_code_shows_its_own_lines(tmp_path: Path) -> None:
    voisin = tmp_path / "voisin_trace.py"
    voisin.write_text("def casse():\n    return 1 / 0\n", encoding="utf-8")
    sys.path.insert(0, str(tmp_path))
    try:
        casse = importlib.import_module("voisin_trace").casse
        with pytest.raises(ZeroDivisionError) as caught:
            casse()
    finally:
        sys.path.remove(str(tmp_path))
        sys.modules.pop("voisin_trace", None)
    told = _trace(caught.value, tmp_path)
    assert str(voisin) in told and "return 1 / 0" in told
    assert "ZeroDivisionError" in told
    assert __file__ not in told


def test_a_syntax_error_shows_its_line_without_a_stack(tmp_path: Path) -> None:
    with pytest.raises(SyntaxError) as caught:
        compile("def (:\n", str(tmp_path / "outils.py"), "exec")
    told = _trace(caught.value, tmp_path)
    assert "Traceback" not in told
    assert str(tmp_path / "outils.py") in told and "SyntaxError" in told


def test_an_error_without_user_lines_keeps_its_whole_stack(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError) as caught:
        raise RuntimeError("défaut de loom")
    told = _trace(caught.value, tmp_path / "ailleurs")
    assert "Traceback" in told and __file__ in told


# --- Le montage d'essai -------------------------------------------------------------


async def test_the_trial_mount_refuses_a_missing_tool(demo: ConfigFactory) -> None:
    config = load_config(demo(agents=[demo_agent(tools=[{"python": "inexistant"}])]))
    async with Loom(config) as loom:
        with pytest.raises(ConfigError, match="inexistant"):
            await _mounted(loom)


async def test_the_trial_mount_needs_no_api_key(
    demo: ConfigFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un modèle réel dont la clé manque ne bloque pas le rechargement : rien ne l'appelle."""
    monkeypatch.delenv("CLE_ABSENTE_LOOM", raising=False)
    reel: dict[str, Any] = {
        "id": "REEL",
        "sdk": "anthropic",
        "model": "claude-haiku-4-5",
        "api_key_env": "CLE_ABSENTE_LOOM",
    }
    fake: dict[str, Any] = {"id": "FAKE", "sdk": "fake", "model": "fake-1"}
    config = load_config(
        demo(
            models=[fake, reel],
            agents=[demo_agent(), demo_agent(name="reel", main={"model": "REEL", "system": "."})],
        )
    )
    async with Loom(config) as loom:
        await _mounted(loom)


async def test_the_trial_mount_covers_each_client(demo: ConfigFactory) -> None:
    """Un client qui retire l'outil d'un agent qu'il ne peut pas lancer n'est pas monté."""
    config = load_config(
        demo(
            agents=[demo_agent(), demo_agent(name="autre", tools=[{"python": "inexistant"}])],
            tenants=[{"id": "dupont", "agents": ["demo"]}],
        )
    )
    async with Loom(config) as loom:
        await _mounted(loom)
    config = load_config(
        demo(
            agents=[demo_agent(), demo_agent(name="autre", tools=[{"python": "inexistant"}])],
            tenants=[{"id": "dupont", "agents": ["demo"]}, {"id": "martin"}],
        )
    )
    async with Loom(config) as loom:
        with pytest.raises(ConfigError, match="inexistant"):
            await _mounted(loom)


# --- La relève ---------------------------------------------------------------------


class _Leaving:
    """Un process qui s'arrête : il dit « sourd » un moment après qu'on le lui demande."""

    def __init__(self, writer: Connection, after: float) -> None:
        self.writer = writer
        self.after = after
        self.asked: float | None = None

    def terminate(self) -> None:
        self.asked = time.monotonic()

        def later() -> None:
            time.sleep(self.after)
            self.writer.send(DEAF)

        threading.Thread(target=later, daemon=True).start()


def test_the_hand_passes_only_once_the_old_process_is_deaf() -> None:
    """Tant qu'il écoute, l'ancien prend encore des requêtes sur la socket commune."""
    reader, writer = multiprocessing.Pipe(duplex=False)
    old = _Leaving(writer, after=0.3)
    served = _Serving(cast(Any, old), reader)
    served.leave()
    assert old.asked is not None
    assert time.monotonic() - old.asked >= 0.3
    reader.close()
    writer.close()


def test_a_process_that_ends_without_a_word_does_not_hold_the_hand() -> None:
    reader, writer = multiprocessing.Pipe(duplex=False)

    class Gone:
        def terminate(self) -> None:
            writer.close()

    begun = time.monotonic()
    _Serving(cast(Any, Gone()), reader).leave()
    assert time.monotonic() - begun < 1.0
    reader.close()


async def test_the_server_stops_listening_then_says_so_then_ends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import uvicorn

    said: list[str] = []

    class Listening:
        def close(self) -> None:
            said.append("ferme")

    async def ended(self: uvicorn.Server, sockets: Any = None) -> None:
        said.append("finit")

    monkeypatch.setattr(uvicorn.Server, "shutdown", ended)
    server = _Taking(
        uvicorn.Config(cast(Any, None)), lambda: None, lambda: said.append("sourd"), os.getpid()
    )
    server.servers = [cast(Any, Listening())]
    await server.shutdown()
    assert said == ["ferme", "sourd", "finit"]


# --- La ligne de commande -----------------------------------------------------------


def test_reload_hands_the_config_to_the_supervisor(
    demo: ConfigFactory, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    given: dict[str, Any] = {}

    def fake(path: Path, *, profile: str | None, host: str, port: int, banner: Any) -> int:
        given.update(path=path, profile=profile, host=host, port=port, banner=banner)
        return 0

    monkeypatch.setattr(http_serve, "serve_reloading", fake)
    config = demo()
    assert main(["--config", str(config), "--profile", "dev", "serve", "--reload"]) == 0
    from loom_ia.access.cli.main import serving_banner

    assert given == {
        "path": config,
        "profile": "dev",
        "host": "127.0.0.1",
        "port": 8000,
        "banner": serving_banner,
    }
    # Le bandeau est celui du process qui prend la main, pas du superviseur.
    assert "API REST" not in capsys.readouterr().out


def test_reload_is_refused_in_prod(
    demo: ConfigFactory, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def never(*args: Any, **kwargs: Any) -> int:
        raise AssertionError("le superviseur ne doit pas démarrer")

    monkeypatch.setattr(http_serve, "serve_reloading", never)
    assert main(["--config", str(demo()), "--profile", "prod", "serve", "--reload"]) == 2
    assert "refusé en profil prod" in capsys.readouterr().err


def test_reload_says_which_extra_is_missing(
    demo: ConfigFactory, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cli = importlib.import_module("loom_ia.access.cli.main")

    def found(name: str) -> object | None:
        return None if name == "watchfiles" else object()

    monkeypatch.setattr(cli, "find_spec", found)
    assert main(["--config", str(demo()), "serve", "--reload"]) == 2
    assert "'loom serve --reload' demande l'extra 'http'" in capsys.readouterr().err


def test_reload_refuses_data_around_the_config(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    config = demo(storage={"events": {"backend": "jsonl", "path": "."}})
    assert main(["--config", str(config), "serve", "--reload"]) == 2
    assert "mettre les données dans un sous-dossier" in capsys.readouterr().err
