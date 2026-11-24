# SPDX-License-Identifier: Apache-2.0
"""Contrat du canal : messages, schémas figés, format des événements exportés (docs/analyse-exortateur-loom.md, lot 1)."""

import json
from datetime import UTC, datetime

import jsonschema
import pytest
from pydantic import ValidationError

from loom_ia.agents.spec import AgentAccess, ApproveAccess
from loom_ia.config.models import AccessRole, ApiKey, UserSpec
from loom_ia.console.contract import (
    CONTRACTS,
    PAYLOADS,
    SCHEMAS_DIR,
    AckPayload,
    ByePayload,
    CommandPayload,
    CommandResultPayload,
    ContractError,
    ErrorPayload,
    EventsPayload,
    FileDataPayload,
    HelloPayload,
    Payload,
    SecurityAgent,
    SecurityGateway,
    SecurityKey,
    SecurityPayload,
    SecurityRole,
    SecuritySyncKey,
    SecuritySyncPayload,
    SecurityUser,
    SessionDeletedPayload,
    WelcomePayload,
    contract_schemas,
    decode,
    dumped,
    encode,
)
from loom_ia.console.translate import public_event
from loom_ia.core.events import Event
from loom_ia.core.events.payloads import ApprovalRequested, ModelExchanged
from loom_ia.core.events.redaction import redacted
from loom_ia.core.model import Message, RateLimit, TextBlock, ToolOutput
from loom_ia.telemetry.redaction import Redactor
from loom_ia.testing import RunJournal, tool_call_message

NOW = datetime(2026, 11, 20, 7, 0, tzinfo=UTC)

SAMPLES: list[Payload] = [
    HelloPayload(instance_id="atelier", worker_id="worker-1", loom_version="2.0.23", contracts=CONTRACTS, tenant="default", agents=("demo",)),
    WelcomePayload(contract=1),
    EventsPayload(batch_id="b1", events=({"type": "run.started"},)),
    AckPayload(batch_id="b1"),
    SessionDeletedPayload(tenant="default", session="s1"),
    CommandPayload(command_id="c1", expires_at=NOW, as_user="marie", name="run.cancel", args={"run_id": "r1"}),
    CommandResultPayload(command_id="c1", outcome="refused", reason="commands_disabled"),
    FileDataPayload(command_id="c1", part=0, last=True, data="aGVsbG8=", name="devis.docx", size=5, sha256="00"),
    ErrorPayload(code="unknown_type", message="type inconnu"),
    ByePayload(reason="shutdown"),
    SecurityPayload(
        tenant="default",
        roles=(SecurityRole(id="owner", permissions=("run", "read")), SecurityRole(id="console", permissions=("act_as",), act_as_roles=("owner",))),
        users=(SecurityUser(id="dev", name="Denis", roles=("owner",)), SecurityUser(id="console-gateway", roles=("console",))),
        api_keys=(SecurityKey(id="k1", user="dev", scopes=("read",), expires=NOW),),
        agents=(SecurityAgent(name="devis", approve_default=("owner",), approve_tools={"envoyer": ("owner",)}, self_approval=False),),
        gateway=SecurityGateway(user="console-gateway", act_as_roles=("owner",), rate=60, max_ttl=300),
        fingerprint="sha256:" + "a" * 64,
    ),
    SecuritySyncPayload(
        version=13,
        tenant="default",
        roles=(SecurityRole(id="owner", permissions=("run", "read")), SecurityRole(id="console", permissions=("act_as",), act_as_roles=("owner",))),
        users=(SecurityUser(id="dev", roles=("owner",)), SecurityUser(id="console-gateway", roles=("console",))),
        api_keys=(SecuritySyncKey(id="k1", user="dev", hash="sha256:" + "b" * 64, scopes=("read",), rate_limit=RateLimit(per_minute=60)),),
        fingerprint="sha256:" + "c" * 64,
    ),
]


def test_every_message_type_has_a_sample() -> None:
    assert {type(sample) for sample in SAMPLES} == set(PAYLOADS.values())


@pytest.mark.parametrize("payload", SAMPLES, ids=lambda payload: type(payload).__name__)
def test_a_message_reads_back_as_it_was_written(payload: Payload) -> None:
    text = encode(payload, message_id="m1")
    envelope, read = decode(text)
    assert read == payload
    assert envelope.id == "m1"
    assert envelope.contract == (None if isinstance(payload, HelloPayload) else 1)


def test_hello_proposes_rather_than_declares_a_contract() -> None:
    assert "contract" not in json.loads(encode(SAMPLES[0]))


@pytest.mark.parametrize(
    ("frame", "code"),
    [
        ("pas du json", "invalid_message"),
        ("[1, 2]", "invalid_message"),
        ('{"type": "teleport", "payload": {}}', "unknown_type"),
        ('{"type": "ack", "payload": {}}', "invalid_message"),
        ('{"type": "command", "payload": {"command_id": "c", "expires_at": "2026-11-20T07:00:00Z", "as_user": "m", "name": "rm.rf"}}', "invalid_message"),
    ],
)
def test_a_message_outside_the_contract_is_refused_with_its_code(frame: str, code: str) -> None:
    with pytest.raises(ContractError) as refused:
        decode(frame)
    assert refused.value.code == code


def test_the_published_schemas_are_up_to_date() -> None:
    """Un changement d'une charge, interne ou du canal, ne passe pas sans qu'on décide de son sort.

    Pour mettre à jour : ``uv run python -c "from loom_ia.console.contract import write_schemas; write_schemas()"``.
    """
    expected = contract_schemas()
    found = {path.name: path.read_text(encoding="utf-8") for path in SCHEMAS_DIR.glob("*.json")}
    assert sorted(found) == sorted(expected), "schémas en trop ou manquants dans " + str(SCHEMAS_DIR)
    stale = [name for name, schema in expected.items() if found[name] != dumped(schema)]
    assert not stale, f"schémas périmés : {', '.join(stale)}"


def test_a_hello_from_a_newer_loom_is_read_without_its_unknown_fields() -> None:
    """Le hello précède la négociation : un champ ajouté par un loom plus récent n'y bloque pas la connexion."""
    newer = json.loads(encode(SAMPLES[0]))
    newer["payload"]["capabilities"] = {"files": True}
    envelope, read = decode(json.dumps(newer))
    assert read == SAMPLES[0] and envelope.type == "hello"
    assert "capabilities" not in read.model_dump()


def test_a_known_field_of_the_hello_is_still_checked() -> None:
    wrong = json.loads(encode(SAMPLES[0]))
    wrong["payload"]["export_mode"] = "tout"
    with pytest.raises(ContractError) as refused:
        decode(json.dumps(wrong))
    assert refused.value.code == "invalid_message"


def test_the_hello_model_stays_strict_for_the_code_that_builds_it() -> None:
    with pytest.raises(ValueError, match="comands"):
        HelloPayload.model_validate({**SAMPLES[0].model_dump(), "comands": True})


@pytest.mark.parametrize("payload", [sample for sample in SAMPLES if not isinstance(sample, HelloPayload)], ids=lambda payload: type(payload).__name__)
def test_every_other_message_still_refuses_unknown_fields(payload: Payload) -> None:
    extended = json.loads(encode(payload))
    extended["payload"]["surprise"] = 1
    with pytest.raises(ContractError) as refused:
        decode(json.dumps(extended))
    assert refused.value.code == "invalid_message"


def test_the_published_hello_schema_accepts_more_fields() -> None:
    schemas = contract_schemas()
    assert "additionalProperties" not in schemas["message.hello.json"]
    assert schemas["message.ack.json"]["additionalProperties"] is False
    newer = {**SAMPLES[0].model_dump(mode="json"), "capabilities": {"files": True}}
    jsonschema.validate(newer, schemas["message.hello.json"])


def test_a_fingerprint_is_required_in_its_form() -> None:
    with pytest.raises(ValueError, match="fingerprint"):
        SecurityPayload(tenant="default", roles=(), users=(), fingerprint="md5:00")


def journal() -> list[Event]:
    """Un run complet, une demande d'approbation et un échange brut, comme le moteur les écrit."""
    run = RunJournal(agent="demo")
    run.start("Écris à jean@exemple.fr").model_turn(tool_call_message(("c1", "envoyer", {"to": "jean@exemple.fr"})))
    run.tool_results({"c1": ToolOutput(blocks=(TextBlock(text="envoyé à jean@exemple.fr"),))})
    run.model_turn(Message.assistant("Fait.")).complete()
    drafts = run.take()
    drafts.append(
        run.scope.draft(ApprovalRequested(call_id="c2", tool_name="loom.word", arguments={"path": "devis/d.docx", "mail": "jean@exemple.fr"}, reason="écrire"))
    )
    drafts.append(run.scope.draft(ModelExchanged(model_id="FAKE", provider="fake", attempt=1, method="POST", url="https://x", request_body="secret")))
    return [draft.to_event(seq) for seq, draft in enumerate(drafts, start=1)]


def test_on_demand_sends_what_the_rest_shows_without_read_content() -> None:
    for event in journal():
        assert public_event(event, mode="on_demand") == redacted(event)


def test_push_sends_the_content_masked() -> None:
    approval = next(event for event in journal() if event.type == "approval.requested")
    sent = public_event(approval, mode="push", redactor=Redactor.of(["email"]))
    assert sent["payload"]["arguments"] == {"path": "devis/d.docx", "mail": "[email]"}  # type: ignore[index]
    assert sent == {**approval.model_dump(mode="json"), "payload": sent["payload"]}
    assert isinstance(approval.payload, ApprovalRequested)
    assert approval.payload.arguments["mail"] == "jean@exemple.fr", "le journal n'est jamais modifié"


def test_raw_exchanges_never_send_their_content() -> None:
    exchange = next(event for event in journal() if event.type == "model.exchanged")
    sent = public_event(exchange, mode="push", redactor=Redactor.of(["email"]))
    assert "request_body" not in sent["payload"]  # type: ignore[operator]
    assert sent == redacted(exchange)


@pytest.mark.parametrize("mode", ["on_demand", "push"])
def test_exported_events_match_the_published_schemas(mode: str) -> None:
    schemas = contract_schemas()
    redactor = Redactor.of(["email"])
    for event in journal():
        sent = public_event(event, mode="push" if mode == "push" else "on_demand", redactor=redactor)
        jsonschema.validate(sent, schemas["event.json"])
        jsonschema.validate(sent["payload"], schemas[f"event.{event.type}.json"])


def test_every_message_matches_its_published_schema() -> None:
    schemas = contract_schemas()
    for payload in SAMPLES:
        envelope = json.loads(encode(payload))
        jsonschema.validate(envelope, schemas["envelope.json"])
        jsonschema.validate(envelope["payload"], schemas[f"message.{envelope['type']}.json"])


def test_a_sync_key_carries_a_well_formed_hash() -> None:
    with pytest.raises(ValueError, match="pattern"):
        SecuritySyncKey(id="k1", user="dev", hash="sha256:court")


def test_the_hello_of_a_shared_instance_says_so_and_names_its_gateway() -> None:
    hello = HelloPayload(
        instance_id="atelier",
        worker_id="w1",
        loom_version="2.0.25",
        contracts=CONTRACTS,
        tenant="default",
        security_source="console",
        gateway_user="console-gateway",
    )
    _, read = decode(encode(hello, contract=None))
    assert isinstance(read, HelloPayload)
    assert (read.security_source, read.gateway_user) == ("console", "console-gateway")


def test_a_console_of_2_0_24_reads_the_hello_of_a_shared_instance() -> None:
    """Les champs ajoutés au ``hello`` sont ignorés par une lecture qui ne les connaît pas (2.0.24)."""
    frame = json.loads(
        encode(
            HelloPayload(instance_id="a", worker_id="w", loom_version="2.0.25", contracts=CONTRACTS, tenant="default", security_source="console"), contract=None
        )
    )
    known = {"instance_id", "worker_id", "loom_version", "contracts", "tenant", "agents", "commands", "export_mode"}
    frame["payload"] = {name: value for name, value in frame["payload"].items() if name in known} | {"nouveau": 1}
    _, read = decode(json.dumps(frame))
    assert isinstance(read, HelloPayload)
    assert read.security_source == "file"


def test_security_carries_the_applied_part_only_when_given() -> None:
    local = SAMPLES[10]
    assert isinstance(local, SecurityPayload)
    assert "version" not in json.loads(encode(local))["payload"]
    shared = local.model_copy(update={"version": 13, "part_fingerprint": "sha256:" + "c" * 64})
    _, read = decode(encode(shared))
    assert isinstance(read, SecurityPayload)
    assert (read.version, read.part_fingerprint) == (13, "sha256:" + "c" * 64)


# --- Conversions entre la config et le contrat (point 2 des fonctions communes avec Heddlo) ---


def test_a_role_and_its_message_have_the_same_fields() -> None:
    """Un champ ajouté au rôle de la config, et pas au message (ou l'inverse), se perdrait en route."""
    assert set(AccessRole.model_fields) == set(SecurityRole.model_fields)


def test_a_user_and_its_message_have_the_same_fields_but_the_tenant() -> None:
    """Le client d'un utilisateur est celui du message, pas un champ de l'utilisateur."""
    assert set(UserSpec.model_fields) - {"tenant"} == set(SecurityUser.model_fields)


def test_a_key_of_a_part_has_the_fields_of_a_key_of_the_config() -> None:
    assert set(ApiKey.model_fields) == set(SecuritySyncKey.model_fields)


ACCESS_SENT = {
    "run": "run",
    "answer": "answer",
    "cancel": "cancel",
    "approve.default": "approve_default",
    "approve.tools": "approve_tools",
    "self_approval": "self_approval",
}
"""Chaque champ du bloc ``access`` d'un agent, et celui de ``SecurityAgent`` qui le porte."""


def test_the_access_block_of_an_agent_and_its_message_carry_the_same_fields() -> None:
    """Une liste ajoutée au bloc ``access`` (``access.read``…) doit l'être à ``SecurityAgent``, ``of`` et ``access``."""
    declared = {name for name in AgentAccess.model_fields if name != "approve"} | {f"approve.{name}" for name in ApproveAccess.model_fields}
    assert declared == set(ACCESS_SENT), "champ nouveau : le mettre dans SecurityAgent, SecurityAgent.of, SecurityAgent.access et ACCESS_SENT"
    assert set(SecurityAgent.model_fields) == {"name", *ACCESS_SENT.values()}


def test_a_role_goes_and_comes_back_unchanged() -> None:
    role = AccessRole(id="passerelle", description="Console", permissions=("run", "act_as"), agents=("devis",), act_as_roles=("lecteur",))
    sent = SecurityRole.of(role)
    assert sent.model_dump() == role.model_dump()
    assert sent.access_role() == role


def test_a_role_the_config_refuses_is_refused_on_its_way_back() -> None:
    with pytest.raises(ValidationError, match="'self_approve' sans 'approve'"):
        SecurityRole(id="pont", permissions=("self_approve",)).access_role()


def test_an_access_block_goes_and_comes_back_unchanged() -> None:
    access = AgentAccess.model_validate(
        {
            "run": ["owner"],
            "answer": ["owner", "assistante"],
            "cancel": ["owner"],
            "approve": {"default": ["superviseur"], "tools": {"send_mail": ["owner"]}},
            "self_approval": False,
        }
    )
    sent = SecurityAgent.of("devis", access)
    assert sent.name == "devis"
    assert sent.access() == access
    assert SecurityAgent.of("relance", AgentAccess()) == SecurityAgent(name="relance")
