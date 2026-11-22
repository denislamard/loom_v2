# SPDX-License-Identifier: Apache-2.0
"""Contrat du canal : messages, schémas figés, format des événements exportés (docs/analyse-exortateur-loom.md, lot 1)."""

import hashlib
import json
from datetime import UTC, datetime

import jsonschema
import pytest

from loom_ia.console.contract import (
    CONTRACTS,
    FROZEN_SCHEMAS_DIR,
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
from loom_ia.core.model import Message, TextBlock, ToolOutput
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
]


def test_every_message_type_has_a_sample() -> None:
    assert {type(sample) for sample in SAMPLES} == set(PAYLOADS.values())


@pytest.mark.parametrize("payload", SAMPLES, ids=lambda payload: type(payload).__name__)
def test_a_message_reads_back_as_it_was_written(payload: Payload) -> None:
    text = encode(payload, message_id="m1")
    envelope, read = decode(text)
    assert read == payload
    assert envelope.id == "m1"
    assert envelope.contract == (None if isinstance(payload, HelloPayload) else 2)


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


FROZEN_V1_DIGEST = "4199b510db263cb7270d6ccbd18fd83a9b9026bb2f434a010155f7489e63f1c4"
"""Empreinte des 46 schémas du contrat 1, lus dans l'ordre de leur nom : ils ne changent plus."""


def test_the_schemas_of_contract_1_are_frozen() -> None:
    files = sorted(FROZEN_SCHEMAS_DIR.glob("*.json"))
    assert len(files) == 46
    assert hashlib.sha256(b"".join(path.read_bytes() for path in files)).hexdigest() == FROZEN_V1_DIGEST


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
