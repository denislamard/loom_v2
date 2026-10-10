# SPDX-License-Identifier: Apache-2.0
"""``loom serve --reload`` (#48, J6.4a) : ce qui est surveillé, ce qui est dit, ce qui est refusé.

La relève elle-même — deux process, une socket — est éprouvée avec de vrais
sous-process dans ``tests/integration/test_rechargement_process.py``.
"""
# pyright: reportPrivateUsage=false

import importlib
import multiprocessing
import os
import signal
import sys
import threading
import time
from collections.abc import Callable, Iterator
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any, cast

import pytest
import yaml
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
    _served,
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
    before = {a: (1, 10, 1), c: (1, 10, 3)}
    after = {a: (2, 12, 4), b: (2, 10, 5)}
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
    assert changed({(Change.added, str(tmp))}, after, after, tmp_path) == ""


def test_a_long_list_of_changes_is_counted(tmp_path: Path) -> None:
    paths = [tmp_path / f"prompts/p{i}.md" for i in range(8)]
    same = dict.fromkeys(paths, (1, 10, 1))
    told = changed({(Change.modified, str(p)) for p in paths}, same, same, tmp_path)
    assert told.endswith("prompts/p4.md modifié, et 3 autre(s)")
    assert told.count("modifié") == 5


def test_the_state_says_what_no_event_said(tmp_path: Path) -> None:
    """Un changement fait avant que le guetteur soit armé : seul l'état le montre."""
    a, b, c, d = (tmp_path / n for n in ("loom.yaml", "agents/b.yaml", "outils.py", "notes.md"))
    before = {a: (1, 10, 1), c: (1, 10, 3), d: (1, 10, 4)}
    after = {a: (2, 10, 1), b: (2, 10, 5), d: (1, 10, 4)}
    assert changed(set(), before, after, tmp_path) == (
        "agents/b.yaml ajouté, loom.yaml modifié, outils.py supprimé"
    )
    # L'événement l'emporte : un état inchangé (même taille, même tic) n'efface rien.
    assert changed({(Change.modified, str(d))}, before, after, tmp_path).endswith(
        "notes.md modifié, outils.py supprimé"
    )
    assert changed(set(), after, after, tmp_path) == ""


def test_the_state_is_that_of_each_file_on_the_disk(demo: ConfigFactory) -> None:
    spec = watched(load_config(demo()))
    base = spec.roots[0]
    first = spec.state()
    assert set(first) == spec.present()
    prompt = base / "prompts" / "demo.md"
    prompt.write_text("Tu calcules, et tu le dis.", encoding="utf-8")
    second = spec.state()
    assert second[prompt] != first[prompt]
    assert {p: v for p, v in second.items() if p != prompt} == {
        p: v for p, v in first.items() if p != prompt
    }


# --- Ce que lit le process qui démarre ----------------------------------------------


class _Reading:
    """``load_config`` qui touche le disque à la lecture voulue : avant elle ou juste après."""

    def __init__(self, at: dict[int, Callable[[], object]], *, after: bool) -> None:
        self.at = at
        self.after = after
        self.reads = 0

    def __call__(self, path: Path, *, profile: str | None = None) -> Any:
        self.reads += 1
        touch = self.at.get(self.reads)
        if touch is not None and not self.after:
            touch()
        config = load_config(path, profile=profile)
        if touch is not None and self.after:
            touch()
        return config


def _description(path: Path, text: str) -> Callable[[], object]:
    def touch() -> None:
        agent = path.parent / "agents" / "demo.yaml"
        data = yaml.safe_load(agent.read_text(encoding="utf-8"))
        agent.write_text(yaml.safe_dump({**data, "description": text}), encoding="utf-8")

    return touch


def test_the_config_that_serves_is_read_after_the_state(
    demo: ConfigFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Changée après la première lecture, avant le relevé : c'est la nouvelle qui sert."""
    path = demo()
    reading = _Reading({1: _description(path, "Calcule, version 2.")}, after=True)
    monkeypatch.setattr(http_serve, "load_config", reading)
    config, spec, seen = http_serve._read(path, None)
    assert reading.reads == 2
    assert [a.description for a in config.agents] == ["Calcule, version 2."]
    # Le relevé est celui de ce qu'elle a lu : rien à rattraper.
    assert changed(set(), seen, spec.state(), spec.roots[0]) == ""


def test_a_change_after_the_state_is_left_to_the_supervisor(
    demo: ConfigFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Changée après le relevé : elle sert peut-être déjà, le superviseur la voit quand même."""
    path = demo()
    reading = _Reading({2: _description(path, "Calcule, version 3.")}, after=False)
    monkeypatch.setattr(http_serve, "load_config", reading)
    config, spec, seen = http_serve._read(path, None)
    assert [a.description for a in config.agents] == ["Calcule, version 3."]
    assert changed(set(), seen, spec.state(), spec.roots[0]) == "agents/demo.yaml modifié"


def test_the_state_covers_the_folders_of_the_config_that_serves(
    demo: ConfigFactory, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Relue, la config fait surveiller d'autres dossiers : le relevé est refait sur eux."""
    path = demo()
    shared = tmp_path.parent / f"{tmp_path.name}-prompts"
    shared.mkdir()
    (shared / "demo.md").write_text("Tu calcules, ailleurs.", encoding="utf-8")

    def moved() -> None:
        root = path.read_text(encoding="utf-8")
        path.write_text(f"prompts_dir: {shared}\n{root}", encoding="utf-8")

    reading = _Reading({1: moved}, after=True)
    monkeypatch.setattr(http_serve, "load_config", reading)
    _, spec, seen = http_serve._read(path, None)
    assert reading.reads == 3
    assert spec.roots == (tmp_path, shared)
    assert shared / "demo.md" in seen
    assert changed(set(), seen, spec.state(), tmp_path) == ""


def test_the_config_is_read_three_times_at_most(
    demo: ConfigFactory, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = demo()
    root = path.read_text(encoding="utf-8")
    elsewhere = [tmp_path.parent / f"{tmp_path.name}-p{i}" for i in range(4)]
    for folder in elsewhere:
        folder.mkdir()
        (folder / "demo.md").write_text("Tu calcules.", encoding="utf-8")

    def move(i: int) -> Callable[[], object]:
        return lambda: path.write_text(f"prompts_dir: {elsewhere[i]}\n{root}", encoding="utf-8")

    reading = _Reading({i + 1: move(i) for i in range(4)}, after=True)
    monkeypatch.setattr(http_serve, "load_config", reading)
    config, spec, _ = http_serve._read(path, None)
    assert reading.reads == http_serve.READS == 3
    assert spec == watched(config)


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


# --- Le superviseur et son guetteur -------------------------------------------------
#
# Le superviseur tourne ici, dans le process de l'essai : le lanceur est faux
# (aucun process ne sert), le guetteur est le vrai ``watchfiles``. Les
# changements sont faits avant que chaque guetteur soit créé — le moment où un
# guetteur ne voit rien —, sans course à gagner.


class _Gone:
    """Le process d'un faux lancement : déjà sorti, il dit « sourd » quand on le lui demande."""

    def is_alive(self) -> bool:
        return False

    def terminate(self) -> None:
        pass

    def join(self) -> None:
        pass


class _Told:
    def poll(self, timeout: float) -> bool:
        return True

    def recv(self) -> str:
        return DEAF

    def close(self) -> None:
        pass


def _no_banner(*args: object) -> None:
    pass


class _Supervised:
    """Le superviseur lancé sur une config, avec ce qu'il a dit, et quand.

    ``launches`` : ce que rend chaque lancement — un ``Watched``, ou ``None``
    pour arrêter là (le superviseur s'arrête alors, comme sur un Ctrl+C).
    ``before_watch`` : ce qui est fait avant chaque guetteur, dans l'ordre.
    ``during`` : ce qui est fait pendant le lancement n, après le relevé de
    son process — pendant qu'il monte ses agents.
    """

    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        path: Path,
        launches: list[Watched | None],
        before_watch: list[Callable[[], object]],
        during: dict[int, Callable[[], object]] | None = None,
    ) -> None:
        import watchfiles

        self.path = path
        self.said: list[tuple[str, bool]] = []
        self.launched = 0
        self.armed = False
        stopping: list[threading.Event] = []
        real = watchfiles.watch

        def launcher(*args: Any) -> Callable[[], Any]:
            stopping.append(args[-1])

            def launch() -> Any:
                self.launched += 1
                spec = launches.pop(0)
                if spec is None:
                    stopping[0].set()
                    return None
                seen = spec.state()
                (during or {}).get(self.launched, lambda: None)()
                return _Serving(cast(Any, _Gone()), cast(Any, _Told())), spec, seen

            return launch

        def watch(*paths: Path, **options: Any) -> Iterator[set[Any]]:
            self.armed = False
            if before_watch:
                before_watch.pop(0)()
            guard = real(*paths, **options)
            try:
                for changes in guard:
                    self.armed = True
                    yield changes
            finally:
                guard.close()

        monkeypatch.setattr(http_serve, "_Launcher", launcher)
        monkeypatch.setattr(watchfiles, "watch", watch)

        def say(line: str) -> None:
            self.said.append((line, self.armed))

        monkeypatch.setattr(http_serve, "_say", say)

    def run(self, *, within: float = 5.0) -> int:
        # Si rien ne l'arrête, un Ctrl+C au bout de ``within`` : l'essai tombe, il ne pend pas.
        alarm = threading.Timer(within, lambda: os.kill(os.getpid(), signal.SIGINT))
        alarm.start()
        try:
            return http_serve.serve_reloading(
                self.path, profile=None, host="127.0.0.1", port=0, banner=_no_banner
            )
        finally:
            alarm.cancel()

    @property
    def lines(self) -> list[str]:
        return [line for line, _ in self.said]


def test_watching_is_said_once_the_watch_is_armed(
    demo: ConfigFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = demo()
    spec = watched(load_config(path))
    supervised = _Supervised(monkeypatch, path, [spec, None], [])
    stop = threading.Timer(0.5, lambda: os.kill(os.getpid(), signal.SIGINT))
    stop.start()
    assert supervised.run() == 0
    stop.cancel()
    assert supervised.said[0] == (f"Rechargement : surveille {spec.describe()}", True)


def _edited(base: Path) -> None:
    (base / "prompts" / "demo.md").write_text("Tu calcules bien.", encoding="utf-8")


def _added(base: Path) -> None:
    (base / "notes.md").write_text("à lire", encoding="utf-8")


def _removed(base: Path) -> None:
    (base / "outils_acces.py").unlink()


@pytest.mark.parametrize(
    ("touch", "told"),
    [
        (_edited, "prompts/demo.md modifié"),
        (_added, "notes.md ajouté"),
        (_removed, "outils_acces.py supprimé"),
    ],
)
def test_a_change_made_before_the_watch_is_armed_reloads(
    demo: ConfigFactory,
    monkeypatch: pytest.MonkeyPatch,
    touch: Callable[[Path], None],
    told: str,
) -> None:
    """Fait après le relevé du process qui sert, avant le guetteur : aucun événement ne le dit."""
    path = demo()
    spec = watched(load_config(path))
    supervised = _Supervised(monkeypatch, path, [spec, None], [lambda: touch(path.parent)])
    assert supervised.run() == 0
    assert supervised.lines == [
        f"Rechargement : surveille {spec.describe()}",
        f"Rechargement : {told}",
    ]
    assert supervised.launched == 2


def test_a_folder_newly_watched_is_caught_up_when_its_watch_is_armed(
    demo: ConfigFactory, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Entre l'ancien guetteur fermé et le nouveau armé, rien ne passe."""
    path = demo()
    shared = tmp_path.parent / f"{tmp_path.name}-prompts"
    shared.mkdir()
    (shared / "demo.md").write_text("Tu calcules, ailleurs.", encoding="utf-8")
    first = watched(load_config(path))
    second = Watched(roots=(tmp_path, shared), dirs=first.dirs, files=first.files)
    supervised = _Supervised(
        monkeypatch,
        path,
        [first, second, None],
        [
            # Avant le premier guetteur : rien. Un changement vu par lui relance…
            lambda: None,
            # … et le process neuf fait surveiller ``shared`` ; avant son guetteur :
            lambda: (shared / "autre.md").write_text("Tu notes."),
        ],
        # Pendant que le process neuf monte ses agents, après son relevé.
        during={2: lambda: (shared / "demo.md").write_text("Tu calcules, ailleurs, autrement.")},
    )

    def said(line: str) -> None:
        supervised.said.append((line, supervised.armed))
        if line.startswith("Rechargement : surveille") and supervised.launched == 1:
            (tmp_path / "prompts" / "demo.md").write_text("Tu calcules vite.", encoding="utf-8")

    monkeypatch.setattr(http_serve, "_say", said)
    assert supervised.run() == 0
    assert supervised.lines == [
        f"Rechargement : surveille {first.describe()}",
        "Rechargement : prompts/demo.md modifié",
        "Rechargé : le nouveau process sert ; l'ancien finit ce qu'il a en cours.",
        f"Rechargement : surveille {second.describe()}",
        f"Rechargement : {shared / 'autre.md'} ajouté, {shared / 'demo.md'} modifié",
    ]
    assert all(armed for line, armed in supervised.said if "surveille" in line)
    assert supervised.launched == 3


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


async def test_the_process_that_serves_judges_the_host_it_listens_on(
    demo: ConfigFactory, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Sous ``--reload --host``, l'API sans clé avertit de l'adresse servie, pas de la config."""

    class Listening:
        started = True

        def __init__(self, *args: Any) -> None:
            pass

        async def serve(self, sockets: Any = None) -> None:
            pass

    class Ready:
        def close(self) -> None:
            pass

    def banner(config: Any, given: str | None, host: str, port: int) -> None:
        pass

    monkeypatch.setattr(http_serve, "_Taking", Listening)
    started = await _served(
        demo(), None, "0.0.0.0", 8000, cast(Any, None), banner, cast(Any, Ready()), 0
    )
    assert started
    assert "API REST ouverte sur 0.0.0.0 sans clé déclarée" in capsys.readouterr().err


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
