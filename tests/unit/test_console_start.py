# SPDX-License-Identifier: Apache-2.0
"""Ouverture du canal par ``Loom``, ``loom validate`` et ``loom console listen`` (docs/analyse-exortateur-loom.md, lot 1)."""

import argparse
import asyncio
from typing import Any

import pytest

pytest.importorskip("websockets", reason="extra 'console' absent")

from conftest import DEV, OWNER, ConfigFactory

from loom_ia.access import Loom
from loom_ia.access.cli import console as console_cli
from loom_ia.access.cli.main import main
from loom_ia.config import ConfigError
from loom_ia.console.contract import ByePayload, EventsPayload, HelloPayload
from loom_ia.testing.console_server import FakeConsole, Received

KEY = "s3cret"
ENV = {"LOOM_CONSOLE_KEY": KEY}
SECURITY: dict[str, Any] = {
    "roles": [OWNER, {"id": "gateway", "permissions": ["act_as"], "act_as_roles": ["owner"]}],
    "users": [{"id": DEV, "roles": ["owner"]}, {"id": "console-gateway", "roles": ["gateway"]}],
    "audit": {"path": "audit.jsonl"},
}


def section(url: str, **more: Any) -> dict[str, Any]:
    return {"enable": True, "url": url, "key_env": "LOOM_CONSOLE_KEY", **more}


async def test_a_library_opens_no_connection_on_its_own(demo: ConfigFactory) -> None:
    """Comme la lecture du ``.env`` : en librairie, le canal s'ouvre par un appel explicite (D-6)."""
    async with FakeConsole(key=KEY) as fake:
        async with Loom.from_config(demo(profile="dev", console=section(fake.url)), environ=ENV) as loom:
            await asyncio.sleep(0.1)
            assert loom.console is None
        assert not fake.of("hello")


async def test_start_console_says_hello_then_bye(demo: ConfigFactory) -> None:
    async with FakeConsole(key=KEY) as fake:
        config = demo(profile="dev", security=SECURITY, console=section(fake.url, instance_id="atelier", commands={"user": "console-gateway"}))
        async with Loom.from_config(config, environ=ENV) as loom:
            assert await loom.start_console()
            assert await loom.start_console(), "une seconde fois ne rouvre rien"
            async with asyncio.timeout(5):
                hello = (await fake.wait_for("hello"))[0].payload
            assert isinstance(hello, HelloPayload)
            assert (hello.instance_id, hello.worker_id, hello.tenant, hello.agents) == ("atelier", loom.worker_id, "default", ("demo",))
            assert hello.commands
            assert hello.export_mode == "on_demand"
            assert loom.console is not None
            async with asyncio.timeout(5):
                await loom.console.wait_connected()
        async with asyncio.timeout(5):
            bye = (await fake.wait_for("bye"))[0].payload
        assert bye == ByePayload(reason="shutdown")
        assert len(fake.of("hello")) == 1


async def test_a_disabled_console_opens_nothing(demo: ConfigFactory) -> None:
    async with Loom.from_config(demo(console={"enable": False}), environ=ENV) as loom:
        assert not await loom.start_console()
        assert loom.console is None


async def test_without_a_key_nothing_opens_in_dev(demo: ConfigFactory, caplog: pytest.LogCaptureFixture) -> None:
    async with Loom.from_config(demo(profile="dev", console=section("ws://127.0.0.1:9/loom")), environ={}) as loom:
        assert not await loom.start_console()
    assert "variable LOOM_CONSOLE_KEY absente" in caplog.text


async def test_without_a_key_production_refuses(demo: ConfigFactory) -> None:
    async with Loom.from_config(demo(profile="prod", console=section("wss://console.exemple.fr/loom")), environ={}) as loom:
        with pytest.raises(ConfigError, match="LOOM_CONSOLE_KEY"):
            await loom.start_console()


def test_validate_shows_the_console(demo: ConfigFactory, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOOM_CONSOLE_KEY", KEY)
    config = demo(profile="dev", security=SECURITY, console=section("ws://127.0.0.1:8766/loom", commands={"user": "console-gateway"}))
    assert main(["--config", str(config), "validate"]) == 0
    out = capsys.readouterr().out
    assert "\nCONSOLE\n  Adresse      ws://127.0.0.1:8766/loom\n  Client       default\n" in out
    assert "  Clé          LOOM_CONSOLE_KEY : présente\n" in out
    assert "  TLS          aucun : ws:// vers la boucle locale (profil dev)\n" in out
    assert "  Commandes    via console-gateway, rôles endossables : owner\n" in out
    assert "  Export       on_demand (hérité de capture.exports)\n" in out
    assert "  Fichiers     25 Mo au plus, morceaux de 256 Ko, aperçu en 30 s au plus\n" in out
    assert KEY not in out


def test_validate_says_a_missing_key(demo: ConfigFactory, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOOM_CONSOLE_KEY", raising=False)
    assert main(["--config", str(demo(profile="dev", console=section("ws://127.0.0.1:8766/loom"))), "validate"]) == 0
    out = capsys.readouterr().out
    assert "  Clé          LOOM_CONSOLE_KEY : absente\n" in out
    assert "⚠ Console" in out and "variable LOOM_CONSOLE_KEY absente — canal non monté" in out
    assert "  Commandes    refusées (console.commands.user absent)\n" in out


def test_validate_shows_a_disabled_console(demo: ConfigFactory, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--config", str(demo(console={"enable": False})), "validate"]) == 0
    assert "\nCONSOLE\n  État         désactivée (console.enable: false)\n" in capsys.readouterr().out


def listen_args(**changes: Any) -> argparse.Namespace:
    values: dict[str, Any] = {"host": "127.0.0.1", "port": 0, "key_env": "LOOM_CONSOLE_KEY", "raw": False, "certfile": None, "keyfile": None, **changes}
    return argparse.Namespace(**values)


def test_listen_needs_the_key(capsys: pytest.CaptureFixture[str]) -> None:
    assert console_cli.run(listen_args(), environ={}) == console_cli.REFUSED
    assert "LOOM_CONSOLE_KEY est vide" in capsys.readouterr().err


def test_listen_needs_both_server_files(capsys: pytest.CaptureFixture[str]) -> None:
    assert console_cli.run(listen_args(certfile="c.pem"), environ=ENV) == console_cli.REFUSED
    assert "--certfile et --keyfile vont ensemble" in capsys.readouterr().err


def test_listen_shows_one_line_per_thing() -> None:
    printer = console_cli._Printer(raw=False)  # pyright: ignore[reportPrivateUsage]
    hello = HelloPayload(instance_id="atelier", worker_id="worker-1", loom_version="2.0.23", contracts=(1,), tenant="default", agents=("demo",), commands=True)
    lines = printer.lines(Received("hello", payload=hello))
    assert lines[0].endswith("hello       atelier / worker-1  loom 2.0.23  contrats [1]")
    assert lines[1].strip() == "client default · agents : demo · commandes : oui · export : on_demand"
    event: dict[str, Any] = {
        "ts": "2026-11-20T06:55:04+00:00",
        "type": "approval.requested",
        "agent": "demo",
        "status": "warning",
        "run_id": "0193f2a0-11aa",
        "session_id": "s1",
        "seq": 17,
        "facets": {"tool_name": "loom.word"},
    }
    (line,) = printer.lines(Received("events", payload=EventsPayload(batch_id="b", events=(event,))))
    assert "approval.requested demo  warning  run 0193f2a0…  s1#17  tool_name=loom.word" in line
    assert printer.lines(Received("refused", peer="127.0.0.1:5", detail="clé refusée"))[0].endswith("refus       127.0.0.1:5 : clé refusée")
