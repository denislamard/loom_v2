# SPDX-License-Identifier: Apache-2.0
"""Événements : enveloppe, payloads, requêtes, schéma JSON."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from loom_ia.core.events import (
    DURABLE_PAYLOADS,
    Event,
    EventQuery,
    ModelResponded,
    PolicyDecided,
    RunScope,
    RunTransitioned,
    ToolCompleted,
    UserMessage,
    event_json_schema,
)
from loom_ia.core.model import (
    DEFAULT_TENANT,
    InlineDataBlock,
    Message,
    RunId,
    RunStatus,
    SessionId,
    SpanId,
    TenantId,
    ToolOutput,
    Usage,
)

SCOPE = RunScope(
    tenant_id=DEFAULT_TENANT,
    session_id=SessionId("s-1"),
    run_id=RunId("r-1"),
    root_run_id=RunId("r-1"),
    agent="demo",
)


def tool_error_event(seq: int = 1) -> Event:
    payload = ToolCompleted(call_id="c1", tool_name="calculer", output=ToolOutput.error("panne"))
    return SCOPE.draft(payload).to_event(seq)


def test_envelope_copies_type_category_status_and_facets() -> None:
    event = tool_error_event(seq=3)
    assert event.seq == 3
    assert event.type == "tool.completed"
    assert event.category == "tool"
    assert event.status == "error"
    assert event.agent == "demo"
    assert event.facets == {
        "tool_name": "calculer",
        "latency_ms": 0.0,
        "size": 0,
        "is_error": True,
    }


def test_model_responded_facets_include_tokens() -> None:
    payload = ModelResponded(
        model_id="m",
        provider="anthropic",
        message=Message.assistant("ok"),
        usage=Usage(input_tokens=3, output_tokens=4),
        request_hash="h",
    )
    facets = SCOPE.draft(payload).facets
    assert facets["tokens"] == 7
    assert facets["model_id"] == "m"
    assert facets["stop_reason"] == "end"


def test_spans_default_to_run_root_span() -> None:
    payload = RunTransitioned(
        from_state=RunStatus.READY_FOR_MODEL, to_state=RunStatus.AWAITING_TOOLS
    )
    root = SCOPE.draft(payload)
    assert root.span_id == SCOPE.span_id
    assert root.parent_span_id is None
    child = SCOPE.draft(payload, span_id=SpanId("step-1"))
    assert child.parent_span_id == SCOPE.span_id


def test_event_json_round_trip() -> None:
    event = tool_error_event()
    assert Event.model_validate_json(event.model_dump_json()) == event


def test_unknown_event_type_is_rejected() -> None:
    data = json.loads(tool_error_event().model_dump_json())
    data["payload"]["type"] = "tool.exploded"
    data["type"] = "tool.exploded"
    with pytest.raises(ValidationError):
        Event.model_validate(data)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("type", "tool.called"),
        ("category", "model"),
        ("status", "ok"),
        ("facets", {"tool_name": "autre"}),
    ],
)
def test_envelope_must_match_payload(field: str, value: object) -> None:
    data = json.loads(tool_error_event().model_dump_json())
    data[field] = value
    with pytest.raises(ValidationError, match="incohérent"):
        Event.model_validate(data)


def test_unsupported_schema_version_is_rejected() -> None:
    data = json.loads(tool_error_event().model_dump_json())
    data["schema_version"] = 2
    with pytest.raises(ValidationError, match="non prise en charge"):
        Event.model_validate(data)


def test_timestamp_must_be_timezone_aware() -> None:
    data = json.loads(tool_error_event().model_dump_json())
    data["ts"] = "2026-09-17T10:00:00"
    with pytest.raises(ValidationError):
        Event.model_validate(data)


def test_seq_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        SCOPE.draft(ToolCompleted(call_id="c", tool_name="t", output=ToolOutput())).to_event(0)


def test_query_matches_envelope_and_facets() -> None:
    event = tool_error_event()
    tenant = DEFAULT_TENANT
    assert EventQuery(tenant_id=tenant).matches(event)
    assert EventQuery(tenant_id=tenant, tool_name="calculer", status=("error",)).matches(event)
    assert EventQuery(tenant_id=tenant, facets={"is_error": True}).matches(event)
    assert EventQuery(tenant_id=tenant, categories=("tool",), agent="demo").matches(event)

    assert not EventQuery(tenant_id=TenantId("autre")).matches(event)
    assert not EventQuery(tenant_id=tenant, model_id="m").matches(event)
    assert not EventQuery(tenant_id=tenant, types=("model.responded",)).matches(event)
    assert not EventQuery(tenant_id=tenant, run_id=RunId("r-2")).matches(event)
    assert not EventQuery(tenant_id=tenant, session_id=SessionId("s-2")).matches(event)
    assert not EventQuery(tenant_id=tenant, role="juge").matches(event)
    later = datetime.now(UTC) + timedelta(seconds=5)
    assert not EventQuery(tenant_id=tenant, since=later).matches(event)
    assert not EventQuery(tenant_id=tenant, until=event.ts).matches(event)


def test_query_select_sorts_and_paginates() -> None:
    events = [tool_error_event(seq) for seq in range(1, 6)]
    query = EventQuery(tenant_id=DEFAULT_TENANT, limit=2)
    first = query.select(list(reversed(events)))
    assert [e.seq for e in first] == [1, 2]
    second = query.model_copy(update={"after": first[-1].event_id}).select(events)
    assert [e.seq for e in second] == [3, 4]


def test_json_schema_lists_every_durable_event_type() -> None:
    schema = event_json_schema()
    payload_types = {
        definition["properties"]["type"]["const"]
        for name, definition in schema["$defs"].items()
        if name in {p.__name__ for p in DURABLE_PAYLOADS}
    }
    expected = {p.model_fields["type"].default for p in DURABLE_PAYLOADS}
    assert payload_types == expected
    assert len(expected) == len(DURABLE_PAYLOADS)


def test_policy_decisions_have_their_own_category_and_status() -> None:
    denied = SCOPE.draft(
        PolicyDecided(policy="plafond", point="before_tool", decision="deny", call_id="c1")
    ).to_event(1)
    assert (denied.category, denied.status) == ("policy", "ok")
    assert denied.facets == {"policy": "plafond", "point": "before_tool", "decision": "deny"}
    failed = PolicyDecided(policy="plafond", point="before_model", decision="fail", reason="x")
    allowed = PolicyDecided(policy="p", point="on_output", decision="continue", error=True)
    assert (failed.event_status, allowed.event_status) == ("error", "warning")
    assert Event.model_validate_json(denied.model_dump_json()) == denied
    with pytest.raises(ValidationError, match="octets de fichier interdits"):
        PolicyDecided(
            policy="p",
            point="on_output",
            decision="replace",
            output=Message(
                role="assistant", blocks=(InlineDataBlock(media_type="image/png", data=b"x"),)
            ),
        )


def test_repair_messages_are_marked_and_old_requests_unchanged() -> None:
    request = SCOPE.draft(UserMessage(message=Message.user("?"))).to_event(1)
    assert request.facets == {}
    repair = SCOPE.draft(
        UserMessage(message=Message.user("refusé"), kind="repair", policy="p", tools=False)
    ).to_event(2)
    assert repair.facets == {"kind": "repair"}
    # Un message.user écrit avant J3.1 se relit sans changement.
    old = json.loads(request.model_dump_json())
    for field in ("kind", "policy", "tools"):
        old["payload"].pop(field)
    assert Event.model_validate(old) == request
