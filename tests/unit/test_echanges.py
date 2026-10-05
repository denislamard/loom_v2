# SPDX-License-Identifier: Apache-2.0
"""Échanges bruts et logs par appel (J6.1b).

Ce qui s'éprouve ici :

- un échange gardé ne garde **pas** les secrets (en-têtes, paramètres
  d'adresse), ni les octets d'un fichier, et un corps trop long est coupé en
  le disant — taille et empreinte du corps entier ;
- chaque tentative d'appel sort ses échanges **avant** son ``model.retried``
  ou sa réponse, et la dernière tentative ratée aussi, avant l'erreur ;
- un échange n'est pas une tentative : le compte des tentatives ne bouge pas ;
- le client HTTP qui garde passe tout ce qui va au SDK et n'enregistre que
  dans un registre ouvert ; il décompresse ce qu'il recopie ;
- la capture se règle par client de loom, et les corps ne partent jamais
  vers un collecteur ;
- une ligne de log par appel de modèle et d'outil, sans contenu.
"""

import gzip
import hashlib
import json
import logging
from collections.abc import AsyncGenerator, AsyncIterator
from importlib.util import find_spec
from typing import Any

import pytest
from conftest import QUESTION, ConfigFactory, demo_agent

from loom_ia.access import Loom
from loom_ia.config import load_config
from loom_ia.config.models import LoomConfig
from loom_ia.core.events import Event, ModelExchanged, ModelRetried
from loom_ia.core.model import (
    DEFAULT_TENANT,
    Message,
    ModelChunk,
    ModelRequest,
    ModelResponse,
    ModelSpec,
    RetryPolicy,
    SessionId,
    TenantId,
    message_to_chunks,
)
from loom_ia.core.ports import ExchangeLog, ModelError, RawExchange, exchange_log, recording
from loom_ia.engine import Recorded
from loom_ia.engine.exchange import REMOVED, exchanged, scrubbed_headers, scrubbed_url
from loom_ia.engine.model_call import ModelCall
from loom_ia.telemetry import run_spans

sans_sdk = pytest.mark.skipif(find_spec("anthropic") is None, reason="extra 'anthropic' absent")
sans_openai = pytest.mark.skipif(find_spec("openai") is None, reason="extra 'openai' absent")

REQUEST = ModelRequest(model_id="m-1", messages=(Message.user("Bonjour"),), max_tokens=100)
ANSWER = Message.assistant("Bonjour à vous")
# Une image PNG d'un pixel, en base64.
PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)
A = TenantId("atelier-a")
B = TenantId("atelier-b")


def brut(**fields: Any) -> RawExchange:
    base: dict[str, Any] = {"method": "POST", "url": "https://api.test/v1/messages"}
    return RawExchange(**{**base, **fields})


def event_of(raw: RawExchange, max_bytes: int = 1000) -> ModelExchanged:
    return exchanged(raw, attempt=1, model_id="m-1", provider="test", max_bytes=max_bytes)


# --- Ce qui est retiré avant d'écrire ----------------------------------------------


def test_secret_headers_are_removed_and_named() -> None:
    headers = scrubbed_headers(
        {
            "X-Api-Key": "sk-ant-secret",
            "Authorization": "Bearer sk-secret",
            "Cookie": "session=1",
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
    )
    assert headers == {
        "x-api-key": REMOVED,
        "authorization": REMOVED,
        "cookie": REMOVED,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }


def test_secret_query_parameters_are_removed() -> None:
    url = scrubbed_url("https://api.test/v1/models/x:generate?key=AIza-secret&alt=sse")
    assert "AIza-secret" not in url and "alt=sse" in url and "key=" in url
    assert scrubbed_url("https://api.test/v1/messages") == "https://api.test/v1/messages"


@pytest.mark.parametrize(
    "image",
    [
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{PNG}"}},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": PNG}},
        {"type": "inline_data", "media_type": "image/png", "data": PNG},
    ],
    ids=["openai", "anthropic", "loom"],
)
def test_file_bytes_never_reach_the_journal(image: dict[str, Any]) -> None:
    body = json.dumps({"messages": [{"role": "user", "content": [image]}]}).encode()
    event = event_of(brut(request_body=body))
    assert PNG not in event.request_body
    assert "[fichier image/png, 70 octets, sha256" in event.request_body


def test_what_is_not_a_file_is_left_alone() -> None:
    # Une signature de raisonnement est longue et en base64, mais pas un fichier.
    signature = "EqQBCkgIARABGAIiQL" * 40
    body = json.dumps({"thinking": {"signature": signature}, "text": "data: rien"}).encode()
    event = event_of(brut(request_body=body), max_bytes=10_000)
    assert event.request_body == body.decode()


def test_a_long_body_is_cut_and_says_so() -> None:
    texte = "é" * 600  # 1 200 octets en UTF-8
    event = event_of(brut(request_body=texte.encode(), response_body=b"ok"), max_bytes=1001)
    assert event.request_truncated and not event.response_truncated
    # Coupé sur une frontière de caractère, jamais au milieu.
    assert event.request_body == "é" * 500
    assert event.request_bytes == 1200
    assert event.request_sha256 == hashlib.sha256(texte.encode()).hexdigest()
    assert event.response_body == "ok" and event.response_bytes == 2


def test_a_failed_exchange_is_a_warning() -> None:
    assert event_of(brut(request_body=b"{}", status=529)).event_status == "warning"
    assert event_of(brut(request_body=b"{}", error="ConnectError")).event_status == "warning"
    assert event_of(brut(request_body=b"{}", status=200)).event_status == "ok"


# --- Les tentatives -------------------------------------------------------------------


class Deposant:
    """Modèle qui dépose un échange par appel, puis échoue ou répond selon son plan."""

    provider = "test"

    def __init__(self, *plan: ModelError | Message) -> None:
        self.plan = list(plan)
        self.calls = 0

    async def aclose(self) -> None:
        pass

    async def stream(self, request: ModelRequest) -> AsyncGenerator[ModelChunk]:
        step = self.plan[self.calls]
        self.calls += 1
        log = exchange_log()
        if log is not None:
            status = 529 if isinstance(step, ModelError) else 200
            log.record(brut(request_body=f"appel {self.calls}".encode(), status=status))
        if isinstance(step, ModelError):
            raise step
        for chunk in message_to_chunks(step):
            yield chunk


SPEC = ModelSpec(
    id="M",
    sdk="fake",
    model="m-1",
    retry=RetryPolicy(max_attempts=3, initial_delay=0.0),
)


async def no_sleep(_: float) -> None:
    pass


async def outcomes(client: Deposant | Recorded) -> list[object]:
    call = ModelCall(client, SPEC, sleep=no_sleep, jitter=lambda: 0.0)
    return [item async for item in call.run(REQUEST)]


async def test_each_attempt_tells_its_exchanges_first() -> None:
    surcharge = ModelError("overloaded", "surchargé", http_status=529)
    seen = await outcomes(Recorded(Deposant(surcharge, ANSWER), 1000))
    kinds = [type(item).__name__ for item in seen]
    assert kinds == ["ModelExchanged", "ModelRetried", "ModelExchanged", "ModelResponse"]
    first, _, second, _ = seen
    assert isinstance(first, ModelExchanged) and isinstance(second, ModelExchanged)
    assert (first.attempt, first.status_code, first.request_body) == (1, 529, "appel 1")
    assert (second.attempt, second.status_code, second.request_body) == (2, 200, "appel 2")


async def test_the_last_failed_attempt_is_told_before_the_error() -> None:
    surcharge = ModelError("overloaded", "surchargé", http_status=529)
    call = ModelCall(
        Recorded(Deposant(surcharge, surcharge, surcharge), 1000), SPEC, sleep=no_sleep
    )
    seen: list[object] = []
    with pytest.raises(ModelError):
        async for item in call.run(REQUEST):
            seen.append(item)
    assert [type(i).__name__ for i in seen][-1] == "ModelExchanged"
    assert sum(isinstance(i, ModelExchanged) for i in seen) == 3
    assert sum(isinstance(i, ModelRetried) for i in seen) == 2


async def test_without_the_capture_nothing_is_kept() -> None:
    seen = await outcomes(Deposant(ANSWER))
    assert [type(i).__name__ for i in seen] == ["ModelResponse"]
    # Et rien ne reste ouvert après une tentative : le registre ne fuit pas.
    await outcomes(Recorded(Deposant(ANSWER), 1000))
    assert exchange_log() is None


async def test_an_exchange_is_not_an_attempt(demo: ConfigFactory) -> None:
    config = load_config(demo(telemetry={"capture": {"raw_exchanges": True}}))
    async with Loom(config) as loom:
        result = await loom.run("demo", QUESTION)
        events = await loom.store.read(DEFAULT_TENANT, result.session_id)
    responded = [e for e in events if e.type == "model.responded"]
    assert responded and all(e.payload.attempts == 1 for e in responded)  # type: ignore[union-attr]


# --- Le client HTTP qui garde ----------------------------------------------------------


@sans_sdk
async def test_the_recording_client_keeps_what_the_sdk_sent_and_read() -> None:
    import httpx2

    from loom_ia.adapters.models.recording import RecordingClient

    sse = 'event: ping\ndata: {"type": "ping"}\n\n'
    compresse = gzip.compress(sse.encode())

    class Morceaux(httpx2.AsyncByteStream):
        """Un corps qui arrive en morceaux, comme sur le réseau : rien n'est préchargé."""

        async def __aiter__(self) -> AsyncIterator[bytes]:
            for debut in range(0, len(compresse), 7):
                yield compresse[debut : debut + 7]

    def repond(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            stream=Morceaux(),
            headers={"content-type": "text/event-stream", "content-encoding": "gzip"},
        )

    client = RecordingClient(transport=httpx2.MockTransport(repond))
    log = ExchangeLog()
    with recording(log):
        request = client.build_request(
            "POST", "https://api.test/v1/messages", json={"q": 1}, headers={"x-api-key": "sk"}
        )
        response = await client.send(request, stream=True)
        lu = b"".join([chunk async for chunk in response.aiter_bytes()])
        await response.aclose()
    assert lu == sse.encode()
    [raw] = log.exchanges
    # Ce que le SDK a lu, décompressé ; la clé est encore là, c'est l'événement qui l'ôte.
    assert raw.response_body == sse.encode() and raw.status == 200
    assert json.loads(raw.request_body) == {"q": 1}
    assert raw.request_headers["x-api-key"] == "sk"
    assert event_of(raw).request_headers["x-api-key"] == REMOVED
    # Hors d'un registre, il passe et ne garde rien.
    response = await client.send(client.build_request("GET", "https://api.test/"))
    assert response.status_code == 200 and len(log.exchanges) == 1
    await client.aclose()


@sans_sdk
async def test_a_transport_error_is_kept_then_raised() -> None:
    import httpx2

    from loom_ia.adapters.models.recording import RecordingClient

    def coupe(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("connexion refusée", request=request)

    client = RecordingClient(transport=httpx2.MockTransport(coupe))
    log = ExchangeLog()
    with recording(log), pytest.raises(httpx2.ConnectError):
        await client.send(client.build_request("POST", "https://api.test/", json={}))
    [raw] = log.exchanges
    assert raw.status is None and raw.error is not None and "connexion refusée" in raw.error
    await client.aclose()


@sans_sdk
async def test_an_anthropic_model_with_the_capture_keeps_its_exchanges() -> None:
    import httpx2

    from loom_ia.adapters.models import create_model_client
    from loom_ia.adapters.models.recording import RecordingClient

    def sse(*events: dict[str, Any]) -> str:
        return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)

    message: dict[str, Any] = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-test",
        "content": [],
        "stop_reason": None,
        "stop_sequence": None,
        "usage": {"input_tokens": 3, "output_tokens": 0},
    }
    corps = sse(
        {"type": "message_start", "message": message},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Oui"}},
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": 1},
        },
        {"type": "message_stop"},
    )
    reponses = iter(
        [
            httpx2.Response(529, json={"type": "error", "error": {"type": "overloaded_error"}}),
            httpx2.Response(200, text=corps, headers={"content-type": "text/event-stream"}),
        ]
    )
    spec = ModelSpec(
        id="CLAUDE",
        sdk="anthropic",
        model="claude-test",
        base_url="https://anthropic.test",
        api_key_env="CLE",
        retry=RetryPolicy(max_attempts=2, initial_delay=0.0),
    )
    inner = create_model_client(
        spec,
        environ={"CLE": "sk-ant-secret"},
        http_client=RecordingClient(transport=httpx2.MockTransport(lambda _: next(reponses))),
    )
    call = ModelCall(Recorded(inner, 100_000), spec, sleep=no_sleep, jitter=lambda: 0.0)
    claude = REQUEST.model_copy(update={"model_id": "claude-test"})
    seen = [item async for item in call.run(claude)]
    await inner.aclose()
    echanges = [i for i in seen if isinstance(i, ModelExchanged)]
    assert [e.status_code for e in echanges] == [529, 200]
    assert isinstance(seen[-1], ModelResponse) and seen[-1].message.text == "Oui"
    for echange in echanges:
        assert "sk-ant-secret" not in echange.model_dump_json()
        assert echange.request_headers["x-api-key"] == REMOVED
        assert json.loads(echange.request_body)["model"] == "claude-test"
        assert echange.provider == "anthropic" and not echange.synthetic
    assert "overloaded_error" in echanges[0].response_body
    assert '"text": "Oui"' in echanges[1].response_body


@sans_openai
async def test_an_openai_stream_is_copied_as_it_is_read() -> None:
    """Un vrai flux, morceau par morceau : rien n'est préchargé, la copie se fait en lisant."""
    import httpx2

    from loom_ia.adapters.models import create_model_client
    from loom_ia.adapters.models.recording import RecordingClient

    def chunk(delta: dict[str, Any], finish: str | None = None) -> dict[str, Any]:
        choice = {"index": 0, "delta": delta, "finish_reason": finish}
        return {
            "id": "c1",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "glm",
            "choices": [choice],
        }

    corps = (
        "".join(
            f"data: {json.dumps(c)}\n\n"
            for c in (
                chunk({"role": "assistant", "content": "Bon"}),
                chunk({"content": "jour"}, "stop"),
            )
        )
        + "data: [DONE]\n\n"
    )

    class Flux(httpx2.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            octets = corps.encode()
            for debut in range(0, len(octets), 10):
                yield octets[debut : debut + 10]

    def repond(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, stream=Flux(), headers={"content-type": "text/event-stream"})

    spec = ModelSpec(
        id="GLM",
        sdk="openai",
        api="chat",
        model="glm",
        base_url="https://together.test/v1",
        api_key_env="CLE",
    )
    inner = create_model_client(
        spec,
        environ={"CLE": "sk-together-secret"},
        http_client=RecordingClient(transport=httpx2.MockTransport(repond)),
    )
    call = ModelCall(Recorded(inner, 100_000), spec)
    glm = REQUEST.model_copy(update={"model_id": "glm"})
    seen = [item async for item in call.run(glm)]
    await inner.aclose()
    [echange] = [i for i in seen if isinstance(i, ModelExchanged)]
    assert isinstance(seen[-1], ModelResponse) and seen[-1].message.text == "Bonjour"
    assert echange.response_body == corps and echange.response_bytes == len(corps.encode())
    assert echange.request_headers["authorization"] == REMOVED
    assert "sk-together-secret" not in echange.model_dump_json()
    assert echange.url == "https://together.test/v1/chat/completions"


def test_without_the_capture_the_sdk_keeps_its_own_client() -> None:
    from loom_ia.adapters.models import create_model_client

    client = create_model_client(ModelSpec(id="F", sdk="fake", model="f"), record=True)
    # Le modèle simulé n'a pas d'HTTP : rien à remplacer, il dépose de lui-même.
    assert type(client).__name__ == "FakeModel"


# --- Par la config ----------------------------------------------------------------------


def deux_ateliers(demo: ConfigFactory) -> LoomConfig:
    return load_config(
        demo(
            tenants=[
                {"id": str(A), "telemetry": {"capture": {"raw_exchanges": True}}},
                {"id": str(B)},
            ]
        )
    )


async def journal(loom: Loom, tenant: TenantId) -> list[Event]:
    await loom.run("demo", QUESTION, tenant=tenant, session_id=SessionId(str(tenant)))
    return await loom.store.read(tenant, SessionId(str(tenant)))


async def test_the_capture_is_decided_tenant_by_tenant(demo: ConfigFactory) -> None:
    async with Loom(deux_ateliers(demo)) as loom:
        avec = await journal(loom, A)
        sans = await journal(loom, B)
    assert not [e for e in sans if e.type == "model.exchanged"]
    echanges = [e for e in avec if e.type == "model.exchanged"]
    reponses = [e for e in avec if e.type == "model.responded"]
    assert len(echanges) == len(reponses) > 0
    # Chacun juste avant sa réponse, dans le même span, au nom du même rôle.
    for echange in echanges:
        suivant = next(e for e in avec if e.seq == echange.seq + 1)
        assert suivant.type == "model.responded"
        assert (suivant.span_id, suivant.role) == (echange.span_id, echange.role)
        payload = echange.payload
        assert isinstance(payload, ModelExchanged) and payload.synthetic
        assert QUESTION in payload.request_body


async def test_an_exchange_never_leaves_its_bodies_to_a_collector(demo: ConfigFactory) -> None:
    async with Loom(deux_ateliers(demo)) as loom:
        events = await journal(loom, A)
    spans = run_spans(events, content=True)
    exchange_events = [e for s in spans for e in s.events if e.name == "model.exchanged"]
    assert exchange_events
    for event in exchange_events:
        assert not [key for key in event.attributes if key.startswith("loom.content.")]
        assert event.attributes["loom.status_code"] == 200
        assert int(event.attributes["loom.request_bytes"]) > 0
    # Les autres événements, eux, portent leur contenu en capture `content`.
    assert any(
        key.startswith("loom.content.") for s in spans for e in s.events for key in e.attributes
    )


async def test_a_resumed_run_ignores_its_exchanges(demo: ConfigFactory) -> None:
    """Le repli d'un run passe sur les échanges : un journal qui en porte se relit."""
    async with Loom(deux_ateliers(demo)) as loom:
        events = await journal(loom, A)
        state = await loom.state(events[0].run_id, session_id=SessionId(str(A)), tenant_id=A)
    assert state.finished and state.iterations == 2


# --- Les logs ---------------------------------------------------------------------------


async def test_one_log_line_per_call_and_no_content(
    demo: ConfigFactory, caplog: pytest.LogCaptureFixture
) -> None:
    agent = demo_agent(tools=[{"python": "calculer"}])
    config = load_config(demo(agents=[agent]))
    with caplog.at_level(logging.INFO, logger="loom_ia.engine.loop"):
        async with Loom(config) as loom:
            await loom.run("demo", QUESTION)
    lines = [r for r in caplog.records if r.name == "loom_ia.engine.loop"]
    models = [r.getMessage() for r in lines if r.getMessage().startswith("Modèle ")]
    tools = [r.getMessage() for r in lines if r.getMessage().startswith("Outil ")]
    assert len(models) == 2 and all("(rôle main)" in m and "tokens" in m for m in models)
    assert len(tools) == 1 and tools[0].startswith("Outil calculer : ")
    assert all(getattr(r, "run_id", None) for r in lines)
    texte = "\n".join(r.getMessage() for r in lines)
    assert QUESTION not in texte and "12*7+3" not in texte and "87" not in texte


async def test_a_tool_error_is_said_in_its_line(
    demo: ConfigFactory, caplog: pytest.LogCaptureFixture
) -> None:
    script = [
        {"text": "Je calcule.", "tool_calls": [{"name": "calculer", "arguments": {"expr": "1/0"}}]},
        {"text": "Impossible."},
    ]
    model = {"id": "FAKE", "sdk": "fake", "model": "fake-1", "params": {"script": script}}
    config = load_config(demo(models=[model]))
    with caplog.at_level(logging.INFO, logger="loom_ia.engine.loop"):
        async with Loom(config) as loom:
            await loom.run("demo", QUESTION)
    [line] = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Outil ")]
    assert line.endswith("(erreur)")
