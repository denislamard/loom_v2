# SPDX-License-Identifier: Apache-2.0
"""Section ``console`` : forme, activation, contrôles croisés, profil (docs/analyse-exortateur-loom.md, lot 1)."""

from pathlib import Path
from typing import Any

import pytest
import yaml
from conftest import OWNER, boot, secured
from pydantic import ValidationError

from loom_ia.config import ConfigError, LoomConfig, load_config
from loom_ia.config.console import ConsoleConfig, plain_text_refusal
from loom_ia.runtime import console_advice, console_export_mode, console_instance_id, console_warnings

GATEWAY_ROLE: dict[str, Any] = {"id": "gateway", "permissions": ["act_as"], "act_as_roles": ["owner"]}
SECURITY: dict[str, Any] = {
    "roles": [OWNER, GATEWAY_ROLE],
    "users": [{"id": "dev", "roles": ["owner"]}, {"id": "console-gateway", "roles": ["gateway"]}],
    "audit": {"path": "audit.jsonl"},
}
ENABLED: dict[str, Any] = {"enable": True, "url": "wss://console.exemple.fr/loom", "key_env": "LOOM_CONSOLE_KEY"}


def config(console: dict[str, Any] | None, **root: Any) -> LoomConfig:
    data: dict[str, Any] = {"version": 1, "security": SECURITY, **root}
    if console is not None:
        data["console"] = console
    return LoomConfig.model_validate(data)


def test_without_section_nothing_is_declared() -> None:
    assert config(None).console is None


def test_enable_is_required() -> None:
    with pytest.raises(ValidationError, match="enable"):
        ConsoleConfig.model_validate({"url": "wss://c.fr/loom"})


def test_a_disabled_section_needs_nothing_else() -> None:
    console = config({"enable": False}).console
    assert console is not None
    assert not console.enable


def test_a_disabled_section_still_checks_the_form_of_what_it_says() -> None:
    """Une faute de frappe ne dort pas jusqu'au jour où l'on passe à ``true``."""
    with pytest.raises(ValidationError, match="Extra inputs"):
        config({"enable": False, "urll": "wss://c.fr"})
    with pytest.raises(ValidationError, match="wss://"):
        config({"enable": False, "url": "http://c.fr/loom"})


def test_an_enabled_section_needs_an_address_and_a_key_variable() -> None:
    with pytest.raises(ValidationError, match="'url' et 'key_env'"):
        config({"enable": True})
    with pytest.raises(ValidationError, match="'key_env'"):
        config({"enable": True, "url": "wss://c.fr/loom"})


@pytest.mark.parametrize(
    ("url", "said"),
    [
        ("https://c.fr/loom", "wss://"),
        ("wss:///loom", "l'hôte manque"),
        ("wss://user:secret@c.fr/loom", "pas d'identifiants"),
        ("wss://c.fr/loom?key=secret", "ni paramètres"),
    ],
)
def test_the_key_never_goes_through_the_address(url: str, said: str) -> None:
    with pytest.raises(ValidationError, match=said):
        config({**ENABLED, "url": url})


def test_plain_text_only_towards_the_loopback() -> None:
    assert config({**ENABLED, "url": "ws://127.0.0.1:8766/loom"}).console is not None
    with pytest.raises(ValidationError, match="boucle locale"):
        config({**ENABLED, "url": "ws://console.exemple.fr/loom"})


@pytest.mark.parametrize(("profile", "refused"), [("dev", False), ("prod", True), (None, True)])
def test_plain_text_only_in_the_dev_profile(profile: str | None, refused: bool) -> None:
    console = ConsoleConfig.model_validate({**ENABLED, "url": "ws://localhost:8766/loom"})
    said = plain_text_refusal(console, profile)
    assert (said is not None) is refused
    if said is not None:
        assert "wss://" in said


def test_tls_is_never_refused_by_the_profile() -> None:
    assert plain_text_refusal(ConsoleConfig.model_validate(ENABLED), "prod") is None
    assert plain_text_refusal(ConsoleConfig.model_validate({**ENABLED, "url": "ws://localhost/loom", "enable": False}), "prod") is None


def test_the_tenant_must_be_declared() -> None:
    with pytest.raises(ValidationError, match="client non déclaré"):
        config({**ENABLED, "tenant": "dupont"})
    users = [{**user, "tenant": "dupont"} for user in SECURITY["users"]]
    declared = config({**ENABLED, "tenant": "dupont"}, tenants=[{"id": "dupont"}], security={**SECURITY, "users": users})
    assert declared.console is not None and declared.console.tenant == "dupont"


def test_the_gateway_must_be_declared_in_the_tenant_with_act_as() -> None:
    assert config({**ENABLED, "commands": {"user": "console-gateway"}}).console is not None
    with pytest.raises(ValidationError, match="non déclaré"):
        config({**ENABLED, "commands": {"user": "personne"}})
    with pytest.raises(ValidationError, match="'act_as'"):
        config({**ENABLED, "commands": {"user": "dev"}})
    users = [{"id": "dev", "roles": ["owner"], "tenant": "dupont"}, {"id": "console-gateway", "roles": ["gateway"], "tenant": "martin"}]
    with pytest.raises(ValidationError, match="chez le client 'martin'"):
        config(
            {**ENABLED, "tenant": "dupont", "commands": {"user": "console-gateway"}},
            tenants=[{"id": "dupont"}, {"id": "martin"}],
            security={**SECURITY, "users": users},
        )


def test_excluded_events_must_exist() -> None:
    assert config({**ENABLED, "export": {"exclude_events": ["model.exchanged"]}}).console is not None
    with pytest.raises(ValidationError, match="inconnu"):
        config({**ENABLED, "export": {"exclude_events": ["model.exchange"]}})


def test_the_client_certificate_and_its_key_go_together() -> None:
    with pytest.raises(ValidationError, match="vont ensemble"):
        config({**ENABLED, "tls": {"client_cert": "client.pem"}})


def test_a_chunk_does_not_exceed_the_file() -> None:
    with pytest.raises(ValidationError, match="chunk_size"):
        config({**ENABLED, "files": {"max_size": 1024, "chunk_size": 2048}})


def test_the_export_mode_follows_capture_unless_declared() -> None:
    assert console_export_mode(config(ENABLED)) == "on_demand"
    assert console_export_mode(config(ENABLED, telemetry={"capture": {"exports": "content"}, "exporters": [{"type": "otel"}]})) == "push"
    assert (
        console_export_mode(
            config({**ENABLED, "export": {"mode": "on_demand"}}, telemetry={"capture": {"exports": "content"}, "exporters": [{"type": "otel"}]})
        )
        == "on_demand"
    )
    assert console_export_mode(config({**ENABLED, "export": {"mode": "push"}})) == "push"


def test_a_missing_key_is_said() -> None:
    assert console_warnings(config(ENABLED), {"LOOM_CONSOLE_KEY": "s3cret"}) == []
    assert console_warnings(config(ENABLED), {}) == ["Console : variable LOOM_CONSOLE_KEY absente — canal non monté"]
    assert console_warnings(config({"enable": False}), {}) == []


def test_advice_in_production_and_in_push() -> None:
    assert console_advice(config(ENABLED)) == []
    advice = console_advice(config({**ENABLED, "export": {"mode": "push"}}, profile="prod"))
    assert any("mTLS" in line for line in advice)
    assert any("push" in line for line in advice)


def test_the_instance_id_is_created_once_and_kept(tmp_path: Path) -> None:
    loaded = config(ENABLED, workpath=tmp_path)
    first = console_instance_id(loaded)
    assert first.startswith("loom-")
    assert (tmp_path / ".loom" / "console" / "instance_id").read_text(encoding="utf-8").strip() == first
    assert console_instance_id(loaded) == first
    assert console_instance_id(config({**ENABLED, "instance_id": "atelier-dupont"}, workpath=tmp_path)) == "atelier-dupont"


def test_an_instance_id_is_a_short_name() -> None:
    with pytest.raises(ValidationError, match="instance_id"):
        config({**ENABLED, "instance_id": "../autre"})


def write(tmp_path: Path, console: dict[str, Any], **boot_keys: Any) -> Path:
    (tmp_path / "agents").mkdir(exist_ok=True)
    data = secured({"version": 1, "security": SECURITY, "console": console})
    (tmp_path / "loom.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    return boot(tmp_path / "loom.yaml", **boot_keys)


def test_the_loader_refuses_plain_text_outside_dev(tmp_path: Path) -> None:
    console = {**ENABLED, "url": "ws://127.0.0.1:8766/loom"}
    assert load_config(write(tmp_path, console, profile="dev")).console is not None
    with pytest.raises(ConfigError, match="ws://"):
        load_config(write(tmp_path, console))


def test_the_loader_roots_tls_files_in_the_workpath(tmp_path: Path) -> None:
    loaded = load_config(write(tmp_path, {**ENABLED, "tls": {"ca_file": "ca.pem", "client_cert": "certs/client.pem", "client_key_env": "K"}}))
    assert loaded.console is not None
    assert loaded.console.tls.ca_file == tmp_path / "ca.pem"
    assert loaded.console.tls.client_cert == tmp_path / "certs" / "client.pem"
