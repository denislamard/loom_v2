# SPDX-License-Identifier: Apache-2.0
"""Commandes et lectures de la console (docs/analyse-exortateur-loom.md, lot 3)."""

import asyncio
import base64
import hashlib
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest

pytest.importorskip("websockets", reason="extra 'console' absent")

import yaml
from conftest import DEV, OWNER, SCRIPT, audited, boot, secured

from loom_ia.access import Loom
from loom_ia.access.cli import console as console_cli
from loom_ia.console.channel import ConsoleChannel
from loom_ia.console.commands import CommandExecutor
from loom_ia.console.contract import CommandPayload, CommandResultPayload, FileDataPayload, Payload
from loom_ia.core.model import RunId, RunStatus, SessionId, new_id
from loom_ia.testing.console_server import FakeConsole

KEY = "s3cret"
ENV = {"LOOM_CONSOLE_KEY": KEY}
GATEWAY = "console-gateway"
READER = "paul"
LAUNCHER = "lea"
PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(40))

OUTILS = f'''
from loom_ia.tools import Image, tool


@tool
def calculer(expr: str) -> str:
    """Calcule une expression arithmétique."""
    return str(eval(expr))


@tool
async def envoyer_email(destinataire: str) -> str:
    """Envoie un e-mail."""
    return f"envoyé à {{destinataire}}"


@tool
def dessiner() -> Image:
    """Dessine le graphique."""
    return Image(data={PNG!r}, name="graphique.png")
'''

QUESTION: dict[str, Any] = {"questions": [{"question": "Pour quel client ?", "header": "Client", "options": [{"label": "Dupont"}, {"label": "Martin"}]}]}
SCRIPTS: dict[str, list[dict[str, Any]]] = {
    "demo": SCRIPT,
    "relance": [{"text": "J'envoie.", "tool_calls": [{"name": "envoyer_email", "arguments": {"destinataire": "mme.martin@example.com"}}]}, {"text": "Envoyé."}],
    "devis": [{"text": "", "tool_calls": [{"name": "ask", "arguments": QUESTION}]}, {"text": "Devis préparé."}],
    "dessin": [{"text": "Je dessine.", "tool_calls": [{"name": "dessiner", "arguments": {}}]}, {"text": "Voilà."}],
}
TOOLS: dict[str, list[dict[str, Any]]] = {
    "demo": [{"python": "calculer"}],
    "relance": [{"python": "envoyer_email", "side_effects": "irreversible", "approval": "always"}],
    "devis": [{"python": "loom.ask"}],
    "dessin": [{"python": "dessiner"}],
}

type Build = Callable[..., Path]


@pytest.fixture
def atelier(tmp_path: Path) -> Build:
    """Quatre agents (calcul, approbation, question, fichier) ; ``dev`` est propriétaire, ``paul`` lecteur sans contenu, ``lea`` lance sans lire."""

    def build(*, commands: dict[str, Any] | None = None, **console: Any) -> Path:
        (tmp_path / "agents").mkdir(exist_ok=True)
        (tmp_path / "outils_console.py").write_text(OUTILS, encoding="utf-8")
        config: dict[str, Any] = {
            "version": 1,
            "profile": "dev",
            "imports": ["outils_console"],
            "models": [{"id": name.upper(), "sdk": "fake", "model": f"{name}-1", "params": {"script": script}} for name, script in SCRIPTS.items()],
            "storage": {"events": {"backend": "jsonl", "path": "data"}},
            "telemetry": {"logging": {"level": "CRITICAL"}},
            "security": {
                "roles": [
                    OWNER,
                    {"id": "lecteur", "permissions": ["read"]},
                    {"id": "lanceur", "permissions": ["run"]},
                    {"id": "gateway", "permissions": ["act_as"], "act_as_roles": ["owner", "lecteur", "lanceur"]},
                ],
                "users": [
                    {"id": DEV, "roles": ["owner"]},
                    {"id": READER, "roles": ["lecteur"]},
                    {"id": LAUNCHER, "roles": ["lanceur"]},
                    {"id": GATEWAY, "roles": ["gateway"]},
                ],
            },
            "console": {
                "enable": True,
                "url": "ws://127.0.0.1:9/loom",
                "key_env": "LOOM_CONSOLE_KEY",
                "commands": {"user": GATEWAY} if commands is None else commands,
                **console,
            },
        }
        (tmp_path / "loom.yaml").write_text(yaml.safe_dump(secured(config)), encoding="utf-8")
        for name in SCRIPTS:
            agent = {"name": name, "description": f"Agent {name}.", "main": {"model": name.upper(), "system": "Tu travailles."}, "tools": TOOLS[name]}
            (tmp_path / "agents" / f"{name}.yaml").write_text(yaml.safe_dump(agent), encoding="utf-8")
        return boot(tmp_path / "loom.yaml")

    return build


class Sent:
    """Le canal, remplacé : ce que l'exécuteur envoie, dans l'ordre."""

    def __init__(self, contract: int = 2) -> None:
        self.payloads: list[Payload] = []
        self.contract: int | None = contract

    async def send(self, payload: Payload, *, message_id: str | None = None) -> bool:
        del message_id
        self.payloads.append(payload)
        return True


class Clock:
    def __init__(self) -> None:
        self.now = datetime.now(UTC)

    def __call__(self) -> datetime:
        return self.now


@asynccontextmanager
async def mounted(config: Path) -> AsyncGenerator[tuple[Loom, CommandExecutor, Sent, Clock]]:
    async with Loom.from_config(config, environ=ENV) as loom:
        assert loom.config.console is not None
        sent, clock = Sent(), Clock()
        executor = CommandExecutor(loom, cast("ConsoleChannel", sent), loom.config.console, clock=clock)
        yield loom, executor, sent, clock
        await executor.aclose()


def command(name: str, args: dict[str, Any], *, as_user: str = DEV, ttl: float = 60, now: datetime | None = None, **more: Any) -> CommandPayload:
    expires = (now or datetime.now(UTC)) + timedelta(seconds=ttl)
    return CommandPayload.model_validate({"command_id": new_id(), "expires_at": expires, "as_user": as_user, "name": name, "args": args, **more})


def refused(result: CommandResultPayload, code: str) -> None:
    assert result.outcome == "refused", result
    assert (result.reason or "").startswith(code), result.reason


async def paused(loom: Loom, agent: str, session: str) -> RunId:
    result = await loom.run(agent, "Vas-y.", session_id=SessionId(session), user=DEV)
    assert result.status is RunStatus.PAUSED, result
    return result.run_id


# --- Contrôles avant Loom -------------------------------------------------------------


async def test_run_start_replayed_starts_one_run(atelier: Build) -> None:
    async with mounted(atelier()) as (loom, executor, _, clock):
        start = command("run.start", {"agent": "demo", "message": "12 fois 7 ?", "run_id": "r-console-1", "session_id": "s1"}, now=clock.now)
        first = await executor.execute(start)
        assert first.outcome == "accepted" and first.result == {"run_id": "r-console-1"}
        assert await executor.execute(start) == first, "rejouée : le même résultat"
        await loom.drain()
        info = await loom.session(SessionId("s1"))
        assert [run.run_id for run in info.runs] == ["r-console-1"]


async def test_run_start_replayed_by_another_process_is_refused_by_the_journal(atelier: Build) -> None:
    """D-10 tient sans la table en mémoire : un autre process, ou un redémarrage, ne relance rien."""
    async with mounted(atelier()) as (loom, executor, _, clock):
        start = command("run.start", {"agent": "demo", "message": "x", "run_id": "r-autre", "session_id": "p1"}, now=clock.now)
        assert (await executor.execute(start)).outcome == "accepted"
        await loom.drain()
        assert loom.config.console is not None
        fresh = CommandExecutor(loom, cast("ConsoleChannel", Sent()), loom.config.console, clock=clock)
        refused(await fresh.execute(start), "RunExists")


async def test_the_same_command_twice_at_once_runs_once(atelier: Build) -> None:
    async with mounted(atelier()) as (loom, executor, _, clock):
        start = command("run.start", {"agent": "demo", "message": "12 fois 7 ?", "run_id": "r-double", "session_id": "s2"}, now=clock.now)
        one, two = await asyncio.gather(executor.execute(start), executor.execute(start))
        assert one == two and one.outcome == "accepted"
        await loom.drain()
        assert len((await loom.session(SessionId("s2"))).runs) == 1


async def test_expired_and_too_far_commands_are_refused(atelier: Build) -> None:
    async with mounted(atelier()) as (_, executor, _, clock):
        args = {"agent": "demo", "message": "x", "run_id": "r1"}
        refused(await executor.execute(command("run.start", args, ttl=-1, now=clock.now)), "expired")
        refused(await executor.execute(command("run.start", args, ttl=301, now=clock.now)), "ttl_too_long")


async def test_a_replay_after_expiry_is_refused(atelier: Build) -> None:
    async with mounted(atelier()) as (_, executor, _, clock):
        start = command("run.start", {"agent": "demo", "message": "x", "run_id": "r-tard"}, ttl=10, now=clock.now)
        assert (await executor.execute(start)).outcome == "accepted"
        clock.now += timedelta(seconds=11)
        refused(await executor.execute(start), "expired")


async def test_without_a_gateway_commands_are_disabled(atelier: Build) -> None:
    async with mounted(atelier(commands={})) as (_, executor, _, clock):
        refused(await executor.execute(command("run.cancel", {"run_id": "r1"}, now=clock.now)), "commands_disabled")


async def test_the_rate_is_bounded(atelier: Build) -> None:
    async with mounted(atelier(commands={"user": GATEWAY, "rate": 2})) as (_, executor, _, clock):
        for _ in range(2):
            refused(await executor.execute(command("run.cancel", {"run_id": "absent"}, now=clock.now)), "Unknown")
        refused(await executor.execute(command("run.cancel", {"run_id": "absent"}, now=clock.now)), "rate_limited")
        clock.now += timedelta(seconds=61)
        refused(await executor.execute(command("run.cancel", {"run_id": "absent"}, now=clock.now)), "Unknown")


async def test_bad_arguments_and_unknown_users_are_refused(atelier: Build) -> None:
    async with mounted(atelier()) as (_, executor, _, clock):
        refused(await executor.execute(command("run.start", {"agent": "demo", "run_id": "r1"}, now=clock.now)), "invalid_args : message")
        refused(await executor.execute(command("run.start", {"agent": "demo", "message": "x", "run_id": "r1", "extra": 1}, now=clock.now)), "invalid_args")
        refused(await executor.execute(command("run.cancel", {"run_id": "r1"}, as_user="inconnu", now=clock.now)), "AccessDenied")
        refused(await executor.execute(command("run.cancel", {"run_id": "r1"}, as_user=GATEWAY, now=clock.now)), "AccessDenied")


async def test_a_user_without_the_right_is_refused_and_audited(atelier: Build) -> None:
    config = atelier()
    async with mounted(config) as (_, executor, _, clock):
        start = command("run.start", {"agent": "demo", "message": "x", "run_id": "r1"}, as_user=READER, now=clock.now)
        refused(await executor.execute(start), "AccessDenied")
    (line,) = audited(config)
    assert (line["access"], line["user"], line["via"], line["permission"]) == ("console", READER, GATEWAY, "run")


async def test_approval_file_needs_its_arguments_and_a_pending_call(atelier: Build) -> None:
    async with mounted(atelier()) as (_, executor, _, clock):
        refused(await executor.execute(command("approval.file", {"run_id": "r1"}, now=clock.now)), "invalid_args")
        refused(await executor.execute(command("approval.preview", {"run_id": "r1", "call_id": "c1"}, now=clock.now)), "UnknownRun")


# --- Les actions ----------------------------------------------------------------------


async def test_one_question_takes_one_answer(atelier: Build) -> None:
    async with mounted(atelier()) as (loom, executor, _, clock):
        run_id = await paused(loom, "devis", "q1")
        first = command("question.answer", {"run_id": run_id, "session_id": "q1", "answers": [["Dupont"]]}, now=clock.now)
        answered = await executor.execute(first)
        assert answered.outcome == "accepted", answered
        assert await executor.execute(first) == answered, "rejouée : la même réponse, pas une seconde"
        refused(await executor.execute(command("question.answer", {"run_id": run_id, "session_id": "q1", "answers": [["Martin"]]}, now=clock.now)), "")


async def test_approval_get_then_decide(atelier: Build) -> None:
    async with mounted(atelier()) as (loom, executor, _, clock):
        run_id = await paused(loom, "relance", "a1")
        got = await executor.execute(command("approval.get", {"run_id": run_id, "session_id": "a1"}, now=clock.now))
        assert got.outcome == "accepted", got
        approvals = cast("dict[str, list[dict[str, Any]]]", got.result)["approvals"]
        assert [approval["tool_name"] for approval in approvals] == ["envoyer_email"]
        refused(await executor.execute(command("approval.get", {"run_id": run_id, "session_id": "a1"}, as_user=READER, now=clock.now)), "AccessDenied")
        decided = await executor.execute(command("approval.decide", {"run_id": run_id, "session_id": "a1", "decision": "approve"}, now=clock.now))
        assert decided.outcome == "accepted" and decided.result == {"calls": [approvals[0]["call_id"]]}
        refused(await executor.execute(command("approval.get", {"run_id": run_id, "session_id": "a1"}, now=clock.now)), "nothing_pending")


async def test_run_cancel(atelier: Build) -> None:
    async with mounted(atelier()) as (loom, executor, _, clock):
        run_id = await paused(loom, "relance", "c1")
        cancelled = await executor.execute(command("run.cancel", {"run_id": run_id, "session_id": "c1"}, now=clock.now))
        assert cancelled.outcome == "accepted" and cancelled.result == {"cancelled": True}
        assert (await loom.state(run_id, session_id=SessionId("c1"))).status is RunStatus.CANCELLED


# --- Les lectures ---------------------------------------------------------------------


async def test_events_query_masks_content_without_read_content(atelier: Build) -> None:
    async with mounted(atelier()) as (loom, executor, _, clock):
        await loom.run("demo", "12 fois 7 ?", session_id=SessionId("e1"), user=DEV)
        args = {"session_id": "e1", "types": ["message.user"]}
        full = await executor.execute(command("events.query", args, now=clock.now))
        masked = await executor.execute(command("events.query", args, as_user=READER, now=clock.now))
        assert full.outcome == masked.outcome == "accepted", (full, masked)
        (shown,) = cast("dict[str, list[dict[str, Any]]]", full.result)["events"]
        (hidden,) = cast("dict[str, list[dict[str, Any]]]", masked.result)["events"]
        assert "12 fois 7 ?" in str(shown) and "redacted" not in shown["payload"]
        assert "12 fois 7 ?" not in str(hidden) and hidden["payload"]["redacted"] == ["message"]
        refused(await executor.execute(command("events.query", {"limit": 1001}, now=clock.now)), "invalid_args : limit")


async def test_a_read_replayed_is_read_again(atelier: Build) -> None:
    async with mounted(atelier()) as (loom, executor, _, clock):
        query = command("events.query", {"session_id": "e2"}, now=clock.now)
        assert cast("dict[str, list[Any]]", (await executor.execute(query)).result)["events"] == []
        await loom.run("demo", "12 fois 7 ?", session_id=SessionId("e2"), user=DEV)
        assert cast("dict[str, list[Any]]", (await executor.execute(query)).result)["events"]


async def test_file_get_sends_the_result_then_the_parts(atelier: Build) -> None:
    async with FakeConsole(key=KEY) as fake:
        config = atelier(url=fake.url, files={"max_size": 1000, "chunk_size": 16})
        async with Loom.from_config(config, environ=ENV) as loom:
            await loom.run("dessin", "Dessine.", session_id=SessionId("f1"), user=DEV)
            (described,) = await loom.session_files(SessionId("f1"))
            assert await loom.start_console()
            async with asyncio.timeout(5):
                await fake.wait_for("connected")
            get = command("file.get", {"session_id": "f1", "file": described.file})
            await fake.send(get)
            async with asyncio.timeout(5):
                await fake.wait_until(lambda: any(isinstance(item.payload, FileDataPayload) and item.payload.last for item in fake.received))
            mine = [item.payload for item in fake.received if getattr(item.payload, "command_id", None) == get.command_id]
        result, *parts = mine
        assert isinstance(result, CommandResultPayload) and result.outcome == "accepted", result
        assert result.result == {"name": "graphique.png", "media_type": "image/png", "size": len(PNG), "sha256": hashlib.sha256(PNG).hexdigest(), "parts": 3}
        chunks = cast("list[FileDataPayload]", parts)
        assert [chunk.part for chunk in chunks] == [0, 1, 2] and [chunk.last for chunk in chunks] == [False, False, True]
        assert b"".join(base64.b64decode(chunk.data) for chunk in chunks) == PNG
        assert chunks[0].sha256 == hashlib.sha256(PNG).hexdigest() and chunks[1].name is None


async def test_file_get_refusals(atelier: Build) -> None:
    async with mounted(atelier(files={"max_size": 20, "chunk_size": 16})) as (loom, executor, sent, clock):
        await loom.run("dessin", "Dessine.", session_id=SessionId("f2"), user=DEV)
        await loom.run("demo", "12 fois 7 ?", session_id=SessionId("autre"), user=DEV)
        (described,) = await loom.session_files(SessionId("f2"))
        refused(await executor.execute(command("file.get", {"session_id": "f2", "file": described.file}, now=clock.now)), "too_large")
        refused(await executor.execute(command("file.get", {"session_id": "autre", "file": described.file}, now=clock.now)), "ArtifactNotFound")
        refused(await executor.execute(command("file.get", {"session_id": "f2", "file": described.file}, as_user=READER, now=clock.now)), "AccessDenied")
        assert not sent.payloads, "execute n'envoie rien : seul receive envoie"


# --- L'écouteur envoie une commande tapée ---------------------------------------------


def test_the_listener_fills_id_and_deadline() -> None:
    typed = console_cli.command_of('{"name": "run.cancel", "as_user": "dev", "args": {"run_id": "r1"}}')
    assert (typed.name, typed.as_user, typed.args) == ("run.cancel", "dev", {"run_id": "r1"})
    assert timedelta(seconds=50) < typed.expires_at - datetime.now(UTC) <= timedelta(minutes=1)
    with pytest.raises(ValueError, match="JSON invalide"):
        console_cli.command_of("{run.cancel}")
    with pytest.raises(ValueError, match="un objet JSON est attendu"):
        console_cli.command_of("[1]")
    with pytest.raises(ValueError, match="as_user"):
        console_cli.command_of('{"name": "run.cancel", "args": {}}')


# --- sessions.list (contrat 2, docs/analyse-securite-hello.md, lot 2) ----------------------------


async def listed(executor: CommandExecutor, clock: Clock, **args: Any) -> tuple[list[str], str | None]:
    result = await executor.execute(command("sessions.list", args, now=clock.now))
    assert result.outcome == "accepted", result
    page = cast("dict[str, Any]", result.result)
    return [str(session["session_id"]) for session in page["sessions"]], page["next"]


async def test_sessions_list_pages_through_every_session(atelier: Build) -> None:
    async with mounted(atelier()) as (loom, executor, _, clock):
        for name in ("s1", "s2", "s3", "s4", "s5"):
            assert (await loom.run("demo", "12 fois 7 ?", session_id=SessionId(name), user=DEV)).ok
        newest_first = [record.session_id for record in sorted(await loom.sessions(), key=lambda r: (r.updated_at, r.session_id), reverse=True)]
        first, token = await listed(executor, clock, limit=2)
        assert first == newest_first[:2] and token is not None
        second, token = await listed(executor, clock, limit=2, after=token)
        third, token = await listed(executor, clock, limit=2, after=token)
        assert first + second + third == newest_first and token is None
        everything, token = await listed(executor, clock)
        assert everything == newest_first and token is None
        result = await executor.execute(command("sessions.list", {"limit": 1}, now=clock.now))
        (session,) = cast("dict[str, Any]", result.result)["sessions"]
        assert set(session) == {"session_id", "last_seq", "updated_at"}


async def test_sessions_list_with_no_session_is_one_empty_page(atelier: Build) -> None:
    async with mounted(atelier()) as (_, executor, _, clock):
        assert await listed(executor, clock) == ([], None)


async def test_sessions_list_checks_its_arguments(atelier: Build) -> None:
    async with mounted(atelier()) as (_, executor, _, clock):
        refused(await executor.execute(command("sessions.list", {"limit": 5001}, now=clock.now)), "invalid_args : limit")
        refused(await executor.execute(command("sessions.list", {"limit": 0}, now=clock.now)), "invalid_args : limit")
        refused(await executor.execute(command("sessions.list", {"page": 2}, now=clock.now)), "invalid_args : page")
        for token in ("pas-un-jeton", "hier|s1", "2026-11-22T07:00:00|s1", "2026-11-22T07:00:00+00:00|"):
            refused(await executor.execute(command("sessions.list", {"after": token}, now=clock.now)), "ValueError")


async def test_sessions_list_needs_read_and_is_audited(atelier: Build) -> None:
    config = atelier()
    async with mounted(config) as (_, executor, _, clock):
        refused(await executor.execute(command("sessions.list", {}, as_user=LAUNCHER, now=clock.now)), "AccessDenied")
        assert (await executor.execute(command("sessions.list", {}, as_user=READER, now=clock.now))).outcome == "accepted"
    (line,) = audited(config)
    assert (line["access"], line["user"], line["via"], line["permission"]) == ("console", LAUNCHER, GATEWAY, "read")


async def test_sessions_list_is_refused_on_a_connection_of_contract_1(atelier: Build) -> None:
    async with Loom.from_config(atelier(), environ=ENV) as loom:
        assert loom.config.console is not None
        executor = CommandExecutor(loom, cast("ConsoleChannel", Sent(contract=1)), loom.config.console)
        refused(await executor.execute(command("sessions.list", {})), "unsupported_command")
        assert (await executor.execute(command("events.query", {}))).outcome == "accepted", "les commandes du contrat 1 restent servies"


def test_sessions_list_can_be_typed_in_the_listener() -> None:
    typed = console_cli.command_of('{"name": "sessions.list", "as_user": "dev", "args": {"limit": 2}}')
    assert (typed.name, typed.args) == ("sessions.list", {"limit": 2})
