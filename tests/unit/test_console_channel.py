# SPDX-License-Identifier: Apache-2.0
"""Connexion vers la console : clé, poignée de main, reconnexion, TLS et mTLS (docs/analyse-exortateur-loom.md, lot 1)."""

import asyncio
import datetime as dt
import ipaddress
import ssl
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

pytest.importorskip("websockets", reason="extra 'console' absent")

from loom_ia.config.console import ConsoleConfig
from loom_ia.console import channel as channel_module
from loom_ia.console.channel import ChannelSettings, ConsoleChannel, retry_delay, tls_context
from loom_ia.console.contract import CONTRACTS, CommandPayload, CommandResultPayload, EventsPayload, HelloPayload, SecurityPayload, SecurityUser, decode
from loom_ia.testing.console_server import FakeConsole

KEY = "s3cret"
HELLO = HelloPayload(instance_id="atelier", worker_id="worker-1", loom_version="2.0.23", contracts=CONTRACTS, tenant="default", agents=("demo",))


@pytest.fixture(autouse=True)
def quick_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Des reconnexions rapides : les essais n'attendent pas l'attente réelle."""
    monkeypatch.setattr(channel_module, "RETRY_BASE", 0.02)
    monkeypatch.setattr(channel_module, "RETRY_CAP", 0.05)
    monkeypatch.setattr(channel_module, "REFUSED_DELAY", 0.05)


async def started(channel: ConsoleChannel) -> asyncio.Task[None]:
    task = asyncio.create_task(channel.run())
    async with asyncio.timeout(5):
        await channel.wait_connected()
    return task


async def stopped(channel: ConsoleChannel, task: asyncio.Task[None]) -> None:
    await channel.aclose()
    async with asyncio.timeout(5):
        await task
    assert channel.state == "closed"


@pytest.fixture
async def console() -> AsyncIterator[FakeConsole]:
    async with FakeConsole(key=KEY) as fake:
        yield fake


async def test_loom_says_hello_and_gets_the_contract(console: FakeConsole) -> None:
    channel = ConsoleChannel(ChannelSettings(url=console.url, key=KEY, hello=HELLO))
    task = await started(channel)
    assert channel.state == "connected"
    assert channel.contract == 1
    hello = console.of("hello")[0]
    assert hello.payload == HELLO
    await stopped(channel, task)


SECURITY = SecurityPayload(tenant="default", roles=(), users=(SecurityUser(id="dev", roles=("owner",)),), fingerprint="sha256:" + "0" * 64)


async def test_the_security_is_pushed_before_anything_else(console: FakeConsole) -> None:
    channel = ConsoleChannel(ChannelSettings(url=console.url, key=KEY, hello=HELLO, security=SECURITY))
    task = await started(channel)
    assert await channel.send(EventsPayload(batch_id="b1", events=({"type": "run.started"},)))
    async with asyncio.timeout(5):
        await console.wait_for("events")
    kinds = [item.kind for item in console.received if item.kind in ("connected", "security", "events")]
    assert kinds == ["connected", "security", "events"]
    assert console.of("security")[0].payload == SECURITY
    envelope = console.of("security")[0].envelope
    assert envelope is not None and envelope.contract == 1
    await stopped(channel, task)


async def test_each_connection_gets_the_security_again(console: FakeConsole) -> None:
    channel = ConsoleChannel(ChannelSettings(url=console.url, key=KEY, hello=HELLO, security=SECURITY))
    task = await started(channel)
    async with asyncio.timeout(5):
        await console.wait_for("security")
    await console.drop()
    async with asyncio.timeout(5):
        await console.wait_for("security", 2)
    await stopped(channel, task)


async def test_without_security_nothing_is_pushed(console: FakeConsole) -> None:
    channel = ConsoleChannel(ChannelSettings(url=console.url, key=KEY, hello=HELLO))
    task = await started(channel)
    assert await channel.send(EventsPayload(batch_id="b1", events=({"type": "run.started"},)))
    async with asyncio.timeout(5):
        await console.wait_for("events")
    assert console.of("security") == []
    await stopped(channel, task)


async def test_closing_says_bye(console: FakeConsole) -> None:
    channel = ConsoleChannel(ChannelSettings(url=console.url, key=KEY, hello=HELLO))
    await stopped(channel, await started(channel))
    async with asyncio.timeout(5):
        bye = await console.wait_for("bye")
    assert bye[0].payload is not None and bye[0].payload.model_dump()["reason"] == "shutdown"


async def test_a_wrong_key_is_refused_and_never_retried_quickly(console: FakeConsole, caplog: pytest.LogCaptureFixture) -> None:
    channel = ConsoleChannel(ChannelSettings(url=console.url, key="faux", hello=HELLO))
    task = asyncio.create_task(channel.run())
    async with asyncio.timeout(5):
        await console.wait_for("refused")
    await asyncio.sleep(0.02)
    assert channel.state == "refused"
    assert console.of("refused")[0].detail == "clé refusée"
    assert "faux" not in caplog.text, "la clé n'apparaît jamais dans un log"
    assert "clé de l'instance refusée (HTTP 401)" in caplog.text
    await stopped(channel, task)


async def test_no_common_contract_is_said_clearly(caplog: pytest.LogCaptureFixture) -> None:
    async with FakeConsole(key=KEY, contracts=(2,)) as fake:
        channel = ConsoleChannel(ChannelSettings(url=fake.url, key=KEY, hello=HELLO))
        task = asyncio.create_task(channel.run())
        async with asyncio.timeout(5):
            await fake.wait_for("refused")
        await asyncio.sleep(0.05)
        assert channel.state == "refused"
        assert "aucun contrat commun" in caplog.text
        await stopped(channel, task)


async def test_a_dropped_connection_comes_back(console: FakeConsole) -> None:
    channel = ConsoleChannel(ChannelSettings(url=console.url, key=KEY, hello=HELLO))
    task = await started(channel)
    await console.drop()
    async with asyncio.timeout(5):
        await console.wait_for("connected", 2)
    async with asyncio.timeout(5):
        await channel.wait_connected()
    assert len(console.of("hello")) == 2
    await stopped(channel, task)


async def test_an_unreachable_console_never_blocks_a_sender() -> None:
    channel = ConsoleChannel(ChannelSettings(url="ws://127.0.0.1:9/loom", key=KEY, hello=HELLO))
    task = asyncio.create_task(channel.run())
    async with asyncio.timeout(1):
        assert not await channel.send(CommandResultPayload(command_id="c", outcome="accepted"))
    assert channel.state in ("connecting", "idle")
    await stopped(channel, task)


async def test_commands_are_refused_until_they_are_served(console: FakeConsole) -> None:
    channel = ConsoleChannel(ChannelSettings(url=console.url, key=KEY, hello=HELLO))
    task = await started(channel)
    command = CommandPayload(command_id="c1", expires_at=dt.datetime.now(dt.UTC), as_user="marie", name="run.cancel", args={"run_id": "r1"})
    await console.send(command)
    async with asyncio.timeout(5):
        results = await console.wait_for("command.result")
    assert results[0].payload == CommandResultPayload(command_id="c1", outcome="refused", reason="commands_disabled")
    await stopped(channel, task)


async def test_a_message_outside_the_contract_gets_an_error(console: FakeConsole) -> None:
    channel = ConsoleChannel(ChannelSettings(url=console.url, key=KEY, hello=HELLO))
    task = await started(channel)
    (connection,) = console._connections  # pyright: ignore[reportPrivateUsage]
    await connection.send('{"type": "teleport", "payload": {}}')
    async with asyncio.timeout(5):
        errors = await console.wait_for("error")
    assert errors[0].payload is not None and errors[0].payload.model_dump()["code"] == "unknown_type"
    await stopped(channel, task)


def test_the_key_stays_out_of_repr() -> None:
    assert KEY not in repr(ChannelSettings(url="wss://c.fr/loom", key=KEY, hello=HELLO))


def test_retries_grow_with_some_randomness_and_a_ceiling() -> None:
    low = [retry_delay(n, rand=lambda a, b: a) for n in range(1, 12)]
    high = [retry_delay(n, rand=lambda a, b: b) for n in range(1, 12)]
    assert low == sorted(low)
    assert low[0] == channel_module.RETRY_BASE / 2
    assert high[-1] == channel_module.RETRY_CAP
    assert all(lo <= hi for lo, hi in zip(low, high, strict=True))


def test_plain_text_has_no_tls_context() -> None:
    console = ConsoleConfig.model_validate({"enable": True, "url": "ws://127.0.0.1:8766/loom", "key_env": "K"})
    assert tls_context(console, {}) is None


def test_tls_always_verifies_the_console() -> None:
    console = ConsoleConfig.model_validate({"enable": True, "url": "wss://c.fr/loom", "key_env": "K"})
    context = tls_context(console, {})
    assert context is not None
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname
    assert context.minimum_version >= ssl.TLSVersion.TLSv1_2


def test_a_client_certificate_needs_its_key(tmp_path: Path) -> None:
    console = ConsoleConfig.model_validate(
        {"enable": True, "url": "wss://c.fr/loom", "key_env": "K", "tls": {"client_cert": str(tmp_path / "c.pem"), "client_key_env": "CERT_KEY"}}
    )
    with pytest.raises(ValueError, match="CERT_KEY"):
        tls_context(console, {})


# --- wss:// et mTLS, avec une autorité de certification d'essai -------------------------------


def _certificates(tmp_path: Path) -> dict[str, Path | str]:
    """Une autorité, un certificat de serveur pour 127.0.0.1, un certificat client ; la clé client en PEM (texte)."""
    pytest.importorskip("cryptography", reason="extra 'crypto' absent")
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    now = dt.datetime.now(dt.UTC)

    def key() -> ec.EllipticCurvePrivateKey:
        return ec.generate_private_key(ec.SECP256R1())

    def name(common: str) -> x509.Name:
        return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common)])

    def pem(private: ec.EllipticCurvePrivateKey) -> bytes:
        return private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())

    ca_key = key()
    ca = (
        x509.CertificateBuilder()
        .subject_name(name("ca d'essai"))
        .issuer_name(name("ca d'essai"))
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )

    def issued(common: str, private: ec.EllipticCurvePrivateKey, *, server: bool) -> x509.Certificate:
        builder = (
            x509.CertificateBuilder()
            .subject_name(name(common))
            .issuer_name(ca.subject)
            .public_key(private.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=5))
            .not_valid_after(now + dt.timedelta(days=1))
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(private.public_key()), critical=False)
        )
        if server:
            builder = builder.add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
        return builder.sign(ca_key, hashes.SHA256())

    server_key, client_key = key(), key()
    paths: dict[str, Path | str] = {}
    for label, content in (
        ("ca", ca.public_bytes(serialization.Encoding.PEM)),
        ("server_cert", issued("console", server_key, server=True).public_bytes(serialization.Encoding.PEM)),
        ("server_key", pem(server_key)),
        ("client_cert", issued("atelier", client_key, server=False).public_bytes(serialization.Encoding.PEM)),
    ):
        path = tmp_path / f"{label}.pem"
        path.write_bytes(content)
        paths[label] = path
    paths["client_key"] = pem(client_key).decode()
    return paths


async def test_wss_with_a_private_authority_and_mtls(tmp_path: Path) -> None:
    files = _certificates(tmp_path)
    server = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    server.load_cert_chain(files["server_cert"], files["server_key"])
    server.load_verify_locations(cafile=files["ca"])
    server.verify_mode = ssl.CERT_REQUIRED
    async with FakeConsole(key=KEY, ssl_context=server) as fake:
        console = ConsoleConfig.model_validate(
            {
                "enable": True,
                "url": fake.url,
                "key_env": "K",
                "tls": {"ca_file": str(files["ca"]), "client_cert": str(files["client_cert"]), "client_key_env": "CERT_KEY"},
            }
        )
        context = tls_context(console, {"CERT_KEY": str(files["client_key"])})
        assert fake.url.startswith("wss://")
        channel = ConsoleChannel(ChannelSettings(url=fake.url, key=KEY, hello=HELLO, ssl_context=context))
        await stopped(channel, await started(channel))


async def test_wss_refuses_an_unknown_authority(tmp_path: Path) -> None:
    files = _certificates(tmp_path)
    server = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    server.load_cert_chain(files["server_cert"], files["server_key"])
    async with FakeConsole(key=KEY, ssl_context=server) as fake:
        console = ConsoleConfig.model_validate({"enable": True, "url": fake.url, "key_env": "K"})
        channel = ConsoleChannel(ChannelSettings(url=fake.url, key=KEY, hello=HELLO, ssl_context=tls_context(console, {})))
        task = asyncio.create_task(channel.run())
        await asyncio.sleep(0.3)
        assert channel.state != "connected"
        assert not fake.of("hello")
        await stopped(channel, task)


def test_decode_is_what_the_console_reads() -> None:
    """Le faux serveur lit les trames comme le canal : par le même ``decode``."""
    assert decode('{"type": "ack", "payload": {"batch_id": "b"}}')[0].type == "ack"
