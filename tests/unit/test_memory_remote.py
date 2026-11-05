# SPDX-License-Identifier: Apache-2.0
"""``loom.memory`` avec ``models.server`` : la config, le client, l'outil, sans torch.

Le client parle à un vrai ``loom-mem-gpu`` lancé sur la boucle locale, dont
les modèles factices annoncent les noms de BGE-M3 : ce qui est éprouvé, c'est
ce que fait loom autour de l'application — premier contact et contrôle des
modèles, lots et annulation, attente du chargement, file pleine, jeton,
erreurs lisibles —, puis l'outil monté sans torch, qui range et retrouve un
document par elle.
"""

import sys
import threading
import time
from array import array
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any, Final

import pytest
from pydantic import JsonValue, ValidationError
from test_memory_gpu_server import Running

import loom_ia.tools_builtin.memory.remote as remote_module
from loom_ia.core.model import DEFAULT_TENANT, RunId, SessionId
from loom_ia.core.ports import ToolContext, ToolError
from loom_ia.tools_builtin import BuiltinContext
from loom_ia.tools_builtin.memory import MemoryTool, bge_m3, build
from loom_ia.tools_builtin.memory.fake import FakeEmbedder, FakeReranker
from loom_ia.tools_builtin.memory.models import (
    Embedding,
    ModelCancelled,
    ModelLoadError,
    ModelRunError,
    shared_models,
)
from loom_ia.tools_builtin.memory.remote import Connection, RemoteEmbedder, RemoteReranker, Target
from loom_ia.tools_builtin.memory.server import app as app_module
from loom_ia.tools_builtin.memory.server.app import Host
from loom_ia.tools_builtin.memory_params import MemoryParams, ModelsSpec, ServerSpec

SERVED: Final = ModelsSpec(server=ServerSpec(url="http://127.0.0.1:8765"))
CONTEXT: Final = ToolContext(
    tenant_id=DEFAULT_TENANT, session_id=SessionId("s"), run_id=RunId("r"), call_id="c", agent="a"
)
NOTE: Final = "Le devis D-2026-042 de Mme Martin porte sur un chauffe-eau de 200 L."

# --- Des modèles factices qui se présentent comme BGE-M3 --------------------------------


class BgeLike(FakeEmbedder):
    """Le dense factice, en 1024 dimensions, sous le nom de BGE-M3 ; ses lots sont comptés."""

    def __init__(self, name: str = "BAAI/bge-m3", device: str | None = "cuda:0") -> None:
        super().__init__(dim=bge_m3.DENSE_DIM)
        self._name = name
        self._device = device
        self.batches: list[int] = []
        self.on_batch: Callable[[], None] | None = None

    @property
    def model_name(self) -> str:
        return self._name

    @property
    def device(self) -> str | None:
        return self._device

    def embed_documents(
        self, texts: Sequence[str], cancel: threading.Event | None = None
    ) -> list[Embedding]:
        self.batches.append(len(texts))
        if self.on_batch is not None:
            self.on_batch()
        return super().embed_documents(texts, cancel)


class RerankerLike(FakeReranker):
    def __init__(self) -> None:
        self.batches: list[int] = []

    @property
    def model_name(self) -> str:
        return "BAAI/bge-reranker-v2-m3"

    def score(
        self, query: str, passages: Sequence[str], cancel: threading.Event | None = None
    ) -> list[float]:
        self.batches.append(len(passages))
        return super().score(query, passages, cancel)


def bge_host(**changes: Any) -> Host:
    values: dict[str, Any] = {
        "embedder": BgeLike(),
        "reranker": RerankerLike(),
        "algo": bge_m3.ALGO,
        "fp16": True,
    }
    values.update(changes)
    return Host(**values)


@pytest.fixture
def serve() -> Iterator[Callable[..., Running]]:
    opened: list[Running] = []

    def start(host: Host | None = None, *, token: str | None = None, load: bool = True) -> Running:
        found = host if host is not None else bge_host()
        if load:
            found.load()
        running = Running(found, token)
        opened.append(running)
        return running

    yield start
    for running in opened:
        running.close()


def url(running: Running) -> str:
    host, port = running.service.address
    return f"http://{host}:{port}"


def client(
    running: Running, *, token: str | None = None, spec: ModelsSpec = SERVED
) -> tuple[RemoteEmbedder, RemoteReranker]:
    connection = Connection(Target(url(running), token, read_timeout=5.0), spec)
    return RemoteEmbedder(connection, spec), RemoteReranker(connection, spec)


# --- Config --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("given", "match"),
    [
        ({"device": "cuda"}, "device se règle"),
        ({"offline": True, "fp16": False}, "fp16, offline se règle"),
        ({"preload": True}, "preload ne sert qu'au calcul dans loom"),
        ({"kind": "fake"}, "kind: bge-m3"),
    ],
)
def test_what_the_application_sets_is_refused_beside_server(
    given: dict[str, Any], match: str
) -> None:
    with pytest.raises(ValidationError, match=match):
        ModelsSpec.model_validate({"server": {"url": "http://127.0.0.1:8765"}, **given})


@pytest.mark.parametrize(
    ("server", "match"),
    [
        ({}, "'url' ou 'url_env'"),
        ({"url": "http://127.0.0.1:8765", "url_env": "MEM_URL"}, "'url' ou 'url_env'"),
        ({"url": "http://192.168.1.20:8765"}, "boucle locale"),
        ({"url": "https://127.0.0.1:8765"}, "http://"),
        ({"url": "http://127.0.0.1:8765/v1"}, "sans chemin"),
        ({"url": "http://127.0.0.1"}, "le port est attendu"),
        ({"url": "http://127.0.0.1:8765", "read_timeout": 0}, "greater than 0"),
    ],
)
def test_a_bad_server_is_refused_at_load(server: dict[str, Any], match: str) -> None:
    with pytest.raises(ValidationError, match=match):
        ModelsSpec.model_validate({"server": server})


def test_the_expected_models_stay_beside_server() -> None:
    spec = ModelsSpec.model_validate(
        {"embedder": "/modeles/bge-m3", "server": {"url": "http://localhost:9000/"}}
    )
    assert spec.embedder == "/modeles/bge-m3" and spec.server is not None


def test_server_variables_are_secrets_a_tenant_can_redirect() -> None:
    params = MemoryParams.model_validate(
        {
            "store": {"path_env": "MEM_DB"},
            "models": {"server": {"url_env": "MEM_URL", "token_env": "MEM_TOKEN"}},
            "actions": ["search"],
        }
    )
    assert params.secret_names() == ("MEM_DB", "MEM_URL", "MEM_TOKEN")


# --- Client --------------------------------------------------------------------------


def test_vectors_come_back_as_the_model_computed_them(serve: Callable[..., Running]) -> None:
    embedder, reranker = client(serve())
    texts = ["devis chauffe-eau", "facture plomberie"]
    found = embedder.embed_documents(texts)
    expected = BgeLike().embed_documents(texts)
    assert [e.sparse for e in found] == [e.sparse for e in expected]
    assert [e.dense for e in found] == [array("f", e.dense).tolist() for e in expected]
    assert embedder.embed_query("devis").sparse == BgeLike().embed_query("devis").sparse
    passages = ["devis chauffe-eau neuf", "rien"]
    assert reranker.score("devis chauffe-eau", passages) == FakeReranker().score(
        "devis chauffe-eau", passages
    )
    assert (embedder.dim, embedder.model_name, embedder.device) == (1024, "BAAI/bge-m3", "cuda:0")


def test_one_request_per_batch_of_sixteen(serve: Callable[..., Running]) -> None:
    host = bge_host()
    embedder, reranker = client(serve(host))
    assert len(embedder.embed_documents([f"mot{i}" for i in range(40)])) == 40
    assert len(reranker.score("q", [f"p{i}" for i in range(20)])) == 20
    assert isinstance(host.embedder, BgeLike) and isinstance(host.reranker, RerankerLike)
    assert host.embedder.batches == [16, 16, 8]
    assert host.reranker.batches == [16, 4]
    assert embedder.embed_documents([]) == [] and reranker.score("q", []) == []


def test_a_cancelled_call_stops_between_two_batches(serve: Callable[..., Running]) -> None:
    host = bge_host()
    cancel = threading.Event()
    assert isinstance(host.embedder, BgeLike)
    host.embedder.on_batch = cancel.set
    embedder, _ = client(serve(host))
    with pytest.raises(ModelCancelled):
        embedder.embed_documents([f"mot{i}" for i in range(40)], cancel)
    assert host.embedder.batches == [16]


@pytest.mark.parametrize(
    ("host", "said"),
    [
        (lambda: bge_host(embedder=BgeLike(name="BAAI/autre")), "sert embedder 'BAAI/autre'"),
        (lambda: bge_host(algo="bge-m3/2"), "sert algorithme 'bge-m3/2'"),
    ],
)
def test_an_application_that_computes_otherwise_is_refused(
    serve: Callable[..., Running], host: Callable[[], Host], said: str
) -> None:
    embedder, _ = client(serve(host()))
    with pytest.raises(ModelLoadError, match=said):
        embedder.embed_query("x")


def test_another_protocol_is_refused(
    serve: Callable[..., Running], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app_module, "PROTOCOL", 2)
    embedder, _ = client(serve())
    with pytest.raises(ModelLoadError, match="sert protocole 2, la config attend 1"):
        embedder.load()


def test_an_absent_application_says_how_to_start_it() -> None:
    import socket

    with socket.socket() as free:
        free.bind(("127.0.0.1", 0))
        _, port = free.getsockname()
    connection = Connection(Target(f"http://127.0.0.1:{port}", connect_timeout=1.0), SERVED)
    with pytest.raises(ModelLoadError, match=r"injoignable .*systemctl --user start loom-mem-gpu"):
        RemoteEmbedder(connection, SERVED).embed_query("x")


def test_proxies_of_the_environment_are_ignored(
    serve: Callable[..., Running], monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    embedder, _ = client(serve())
    assert len(embedder.embed_query("devis").dense) == 1024


def test_a_loading_application_is_awaited(
    serve: Callable[..., Running], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(remote_module, "READY_POLL", 0.05)
    host = bge_host()
    embedder, _ = client(serve(host, load=False))
    threading.Timer(0.3, host.load).start()
    assert len(embedder.embed_query("devis").dense) == 1024


def test_an_application_loading_too_long_is_given_up(
    serve: Callable[..., Running], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(remote_module, "READY_POLL", 0.05)
    monkeypatch.setattr(remote_module, "READY_WAIT", 0.2)
    embedder, _ = client(serve(load=False))
    with pytest.raises(ModelLoadError, match="charge encore ses modèles"):
        embedder.embed_query("x")


def test_waiting_for_the_load_can_be_cancelled(
    serve: Callable[..., Running], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(remote_module, "READY_POLL", 0.05)
    embedder, _ = client(serve(load=False))
    cancel = threading.Event()
    threading.Timer(0.2, cancel.set).start()
    started = time.monotonic()
    with pytest.raises(ModelCancelled):
        embedder.embed_query("x", cancel)
    assert time.monotonic() - started < 5


class Unloadable(BgeLike):
    def load(self) -> None:
        raise ModelLoadError("Modèle BAAI/bge-m3 absent du cache local de Hugging Face")


def test_an_application_that_failed_to_load_says_why(serve: Callable[..., Running]) -> None:
    embedder, _ = client(serve(bge_host(embedder=Unloadable())))
    with pytest.raises(ModelLoadError, match=r"n'a pas chargé ses modèles : .*absent du cache"):
        embedder.embed_query("x")


def test_the_token_is_sent_and_checked(serve: Callable[..., Running]) -> None:
    running = serve(token="s3cret")
    with pytest.raises(ModelLoadError, match="jeton absent ou refusé"):
        client(running)[0].embed_query("x")
    with pytest.raises(ModelLoadError, match="jeton absent ou refusé"):
        client(running, token="faux")[0].embed_query("x")
    assert len(client(running, token="s3cret")[0].embed_query("x").dense) == 1024


def test_a_full_queue_is_tried_again(
    serve: Callable[..., Running], monkeypatch: pytest.MonkeyPatch
) -> None:
    running = serve()
    embedder, _ = client(running)
    embedder.load()
    slots = threading.BoundedSemaphore(1)
    slots.acquire()
    running.service.server.slots = slots
    threading.Timer(0.3, slots.release).start()
    assert len(embedder.embed_query("x").dense) == 1024


class Failing(BgeLike):
    def embed_documents(
        self, texts: Sequence[str], cancel: threading.Event | None = None
    ) -> list[Embedding]:
        raise ModelRunError("Calcul du modèle BAAI/bge-m3 impossible : CUDA out of memory.")


def test_a_failed_computation_is_a_model_run_error(serve: Callable[..., Running]) -> None:
    embedder, reranker = client(serve(bge_host(embedder=Failing())))
    with pytest.raises(ModelRunError, match="CUDA out of memory"):
        embedder.embed_query("x")
    assert reranker.score("devis", ["devis"]) == [1.0]


def test_the_registry_shares_one_connection_per_application() -> None:
    target = Target("http://127.0.0.1:8765")
    first = shared_models(SERVED, target)
    assert shared_models(SERVED, Target("http://127.0.0.1:8765")) is first
    assert shared_models(SERVED, Target("http://127.0.0.1:8765", token="x")) is not first
    assert isinstance(first.embedder, RemoteEmbedder)
    assert isinstance(first.reranker, RemoteReranker)
    assert first.embedder.connection is first.reranker.connection
    with pytest.raises(ValueError, match="n'a pas été lue"):
        shared_models(SERVED)


# --- L'outil -------------------------------------------------------------------------


def memory_params(server: dict[str, Any], **given: Any) -> MemoryParams:
    values: dict[str, Any] = {
        "store": {"path": "memoire.db"},
        "project": "perso",
        "models": {"server": server},
        "actions": ["search", "get", "add_text", "add_file"],
        "folders": [{"name": "workspace", "path": "workspace"}],
        **given,
    }
    return MemoryParams.model_validate(values)


def mounted(
    tmp_path: Path, server: dict[str, Any], secrets: dict[str, str] | None = None
) -> MemoryTool:
    (tmp_path / "workspace").mkdir(exist_ok=True)
    context = BuiltinContext(base_dir=tmp_path, secrets=secrets)
    return build(memory_params(server), context)


async def test_the_tool_stores_and_finds_through_the_application(
    serve: Callable[..., Running], tmp_path: Path
) -> None:
    tool = mounted(tmp_path, {"url": url(serve())})
    added = (
        await tool.invoke({"action": "add_text", "title": "Devis Martin", "text": NOTE}, CONTEXT)
    ).data
    assert isinstance(added, dict) and added["chunks"] == 1
    found = (await tool.invoke({"action": "search", "query": "devis chauffe-eau"}, CONTEXT)).data
    assert isinstance(found, list) and found
    first: Any = found[0]
    assert first["title"] == "Devis Martin"
    # Rien de torch dans ce process : l'application a tout calculé.
    assert "torch" not in sys.modules


async def test_an_absent_application_is_a_readable_tool_error(tmp_path: Path) -> None:
    import socket

    with socket.socket() as free:
        free.bind(("127.0.0.1", 0))
        _, port = free.getsockname()
    tool = mounted(tmp_path, {"url": f"http://127.0.0.1:{port}", "connect_timeout": 1})
    with pytest.raises(ToolError, match="loom-mem-gpu injoignable"):
        await tool.invoke({"action": "add_text", "title": "Devis", "text": NOTE}, CONTEXT)


def test_the_address_and_token_are_read_in_the_tenant_secrets(
    serve: Callable[..., Running], tmp_path: Path
) -> None:
    running = serve(token="s3cret")
    server = {"url_env": "MEM_URL", "token_env": "MEM_TOKEN"}
    tool = mounted(tmp_path, server, {"MEM_URL": url(running) + "/", "MEM_TOKEN": "s3cret"})
    assert tool.target == Target(url(running), "s3cret")
    tool.service.models.embedder.load()
    with pytest.raises(ValueError, match=r"MEM_TOKEN \(token_env\) est absente"):
        mounted(tmp_path, server, {"MEM_URL": url(running)})
    with pytest.raises(ValueError, match=r"MEM_URL vaut 'http://10\.0\.0\.5:8765' — 10\.0\.0\.5"):
        mounted(tmp_path, server, {"MEM_URL": "http://10.0.0.5:8765", "MEM_TOKEN": "x"})


def test_validate_shows_the_application_instead_of_the_device(tmp_path: Path) -> None:
    tool = mounted(tmp_path, {"url": "http://127.0.0.1:8765"})
    options = dict(tool.settings().options)
    assert options == {
        "modèles": "BAAI/bge-m3 + BAAI/bge-reranker-v2-m3 "
        "(loom-mem-gpu http://127.0.0.1:8765, repli : aucun)"
    }
    by_env = mounted(tmp_path, {"url_env": "MEM_URL"}, {"MEM_URL": "http://127.0.0.1:8765"})
    assert "loom-mem-gpu $MEM_URL" in dict(by_env.settings().options)["modèles"]


def test_the_approval_says_where_the_file_will_be_computed(
    serve: Callable[..., Running], tmp_path: Path
) -> None:
    tool = mounted(tmp_path, {"url": url(serve())})
    (tmp_path / "workspace" / "cgv.md").write_text(
        "# Conditions générales\n\nAcompte de 30 %.", encoding="utf-8"
    )
    tool.service.models.embedder.load()
    arguments: dict[str, JsonValue] = {"action": "add_file", "file": "workspace/cgv.md"}
    reason = tool.assess_call(arguments).reason
    assert reason is not None
    assert "moins d'une seconde de calcul sur cuda:0 (loom-mem-gpu)" in reason
