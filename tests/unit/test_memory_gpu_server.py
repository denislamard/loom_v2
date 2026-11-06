# SPDX-License-Identifier: Apache-2.0
"""``loom-mem-gpu`` : le protocole, les réglages, le serveur, ``status``, sans torch.

Le serveur tourne pour de vrai sur la boucle locale (port choisi par le
système), avec les modèles factices de la mémoire à la place de BGE-M3 :
ce qui est éprouvé, c'est ce que fait l'application autour des modèles —
état de chargement, plafonds lus avant le corps, jeton, file bornée, erreurs,
journal sans contenu, arrêt qui laisse finir les requêtes en cours.
"""

import ast
import json
import logging
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from array import array
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest

from loom_ia.tools_builtin.memory import bge_m3
from loom_ia.tools_builtin.memory.fake import FakeEmbedder, FakeReranker
from loom_ia.tools_builtin.memory.models import (
    Embedding,
    ModelLoadError,
    ModelRunError,
    SparseVec,
)
from loom_ia.tools_builtin.memory.protocol import (
    MAX_BODY_BYTES,
    MAX_CHARS,
    MAX_TEXTS,
    PROTOCOL,
    EmbedItem,
    EmbedResponse,
    SparseWire,
    decode_dense,
    encode_dense,
    from_item,
    over_caps,
    to_item,
)
from loom_ia.tools_builtin.memory.server import app as app_module
from loom_ia.tools_builtin.memory.server import log_format, main, status
from loom_ia.tools_builtin.memory.server.app import (
    EXIT_LOAD,
    EXIT_OK,
    EXIT_PORT,
    EXIT_SETTINGS,
    Host,
    PortTaken,
    Service,
    run,
)
from loom_ia.tools_builtin.memory.server.settings import (
    ServiceSettings,
    SettingsError,
    as_environment,
    describe,
    env_name,
    is_loopback,
    load_settings,
)

SRC = Path(__file__).resolve().parents[2] / "src"
SECRET_TEXT = "Facture INV-2026-0042 de Mme Martin, chauffe-eau 200 L"

# --- Protocole -----------------------------------------------------------------------


def test_the_dense_travels_as_float32_without_loss() -> None:
    values = array("f", [0.1, -1.5, 3.25e-7, 0.0, 1.0]).tolist()
    assert decode_dense(encode_dense(values)) == values


def test_an_embedding_goes_and_comes_back_unchanged() -> None:
    dense = array("f", [0.6, 0.8]).tolist()
    embedding = Embedding(dense=dense, sparse=SparseVec(indices=[3, 17], values=[0.25, 1.5]))
    back = from_item(EmbedItem.model_validate_json(to_item(embedding).model_dump_json()))
    assert back == embedding


def test_a_broken_dense_or_lexical_is_refused() -> None:
    with pytest.raises(ValueError, match="multiple de 4"):
        decode_dense("AAAA")  # 3 octets
    with pytest.raises(ValueError, match="longueurs différentes"):
        from_item(EmbedItem(dense="", sparse=SparseWire(indices=[1], values=[])))


def test_caps_name_what_overflows() -> None:
    assert over_caps(["a"] * MAX_TEXTS) is None
    assert over_caps(["a"] * (MAX_TEXTS + 1)) == f"{MAX_TEXTS + 1} textes : au plus 32 par requête"
    said = over_caps(["a", "x" * (MAX_CHARS + 1)], "passages")
    assert said is not None and said.startswith("passages n° 2")


def test_the_client_batch_is_the_one_of_bge_m3() -> None:
    from loom_ia.tools_builtin.memory.protocol import BATCH

    assert BATCH == bge_m3.BATCH <= MAX_TEXTS


# --- Réglages ------------------------------------------------------------------------


def test_the_service_runs_on_cuda_by_default() -> None:
    settings = ServiceSettings()
    assert (settings.host, settings.port, settings.device) == ("127.0.0.1", 8765, "cuda")
    models = settings.models()
    assert models.kind == "bge-m3" and models.device == "cuda" and models.fp16
    assert (models.embedder, models.reranker) == ("BAAI/bge-m3", "BAAI/bge-reranker-v2-m3")


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "127.0.0.2"])
def test_loopback_addresses_are_accepted(host: str) -> None:
    assert is_loopback(host)
    ServiceSettings(host=host)


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "gpu.exemple.fr"])
def test_any_other_address_is_refused_for_now(host: str) -> None:
    with pytest.raises(ValueError, match="boucle locale"):
        ServiceSettings(host=host)


def test_a_vram_cap_needs_a_card() -> None:
    with pytest.raises(ValueError, match="device cuda"):
        ServiceSettings(device="cpu", max_vram_fraction=0.3)
    assert ServiceSettings(device="cuda:1", max_vram_fraction=0.3).max_vram_fraction == 0.3


def test_options_win_over_the_file(tmp_path: Path) -> None:
    config = tmp_path / "loom-mem-gpu.yaml"
    config.write_text("device: cpu\noffline: true\nport: 9000\n", encoding="utf-8")
    settings = load_settings(config, {"port": 9100})
    assert (settings.device, settings.offline, settings.port) == ("cpu", True, 9100)


@pytest.mark.parametrize(
    ("text", "said"),
    [
        ("- une liste\n", "objet YAML"),
        ("device: [\n", "YAML illisible"),
        ("devise: cpu\n", "devise"),
        ("port: 0\n", "port"),
    ],
)
def test_a_bad_file_says_why(tmp_path: Path, text: str, said: str) -> None:
    config = tmp_path / "f.yaml"
    config.write_text(text, encoding="utf-8")
    with pytest.raises(SettingsError, match=said):
        load_settings(config, {})


def test_a_missing_file_is_named(tmp_path: Path) -> None:
    with pytest.raises(SettingsError, match=r"absent\.yaml"):
        load_settings(tmp_path / "absent.yaml", {})


def test_variables_sit_between_the_file_and_the_options(tmp_path: Path) -> None:
    config = tmp_path / "f.yaml"
    config.write_text("device: cpu\nport: 9000\noffline: false\n", encoding="utf-8")
    environ = {
        "LOOM_MEM_GPU_PORT": "9100",
        "LOOM_MEM_GPU_OFFLINE": "true",
        "LOOM_MEM_GPU_FP16": "false",
        "LOOM_MEM_GPU_DEVICE": " ",  # vide : comme absente
    }
    settings = load_settings(config, {"port": 9200}, environ)
    assert (settings.device, settings.port, settings.offline, settings.fp16) == (
        "cpu",
        9200,
        True,
        False,
    )
    assert load_settings(config, {}, environ).port == 9100
    # Sans ``environ``, aucune variable n'est lue.
    assert load_settings(None, {}) == ServiceSettings()


def test_a_bad_variable_is_named() -> None:
    with pytest.raises(SettingsError, match=r"environnement.*port \(LOOM_MEM_GPU_PORT\)"):
        load_settings(None, {}, {"LOOM_MEM_GPU_PORT": "huit"})


def test_an_unknown_variable_is_refused_but_not_the_token_one() -> None:
    with pytest.raises(SettingsError, match="inconnue : LOOM_MEM_GPU_PROT"):
        load_settings(None, {}, {"LOOM_MEM_GPU_PROT": "9000"})
    environ = {
        "LOOM_MEM_GPU_TOKEN_ENV": "LOOM_MEM_GPU_TOKEN",
        "LOOM_MEM_GPU_TOKEN": "s3cret",
        "HF_HOME": "/data/hf",
    }
    settings = load_settings(None, {}, environ)
    assert settings.token(environ) == "s3cret"


def _as_systemd_reads_it(line: str) -> tuple[str, str]:
    """Une ligne ``Environment=`` telle que systemd la lit : guillemets, ``%%``."""
    value = line.removeprefix("Environment=")
    if value.startswith('"'):
        value = value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    name, _, text = value.replace("%%", "%").partition("=")
    return name, text


def test_the_settings_become_the_lines_of_the_unit_and_come_back() -> None:
    settings = ServiceSettings(port=8800, offline=True, embedder="/data/mes modèles/bge 100%")
    lines = as_environment(settings)
    assert "Environment=LOOM_MEM_GPU_PORT=8800" in lines
    assert "Environment=LOOM_MEM_GPU_FP16=true" in lines
    assert 'Environment="LOOM_MEM_GPU_EMBEDDER=/data/mes modèles/bge 100%%"' in lines
    assert [line for line in lines if line.startswith("# Environment=")] == [
        "# Environment=LOOM_MEM_GPU_TOKEN_ENV=LOOM_MEM_GPU_TOKEN",
        "# Environment=LOOM_MEM_GPU_MAX_VRAM_FRACTION=0.3",
    ]
    environ = dict(_as_systemd_reads_it(line) for line in lines if line.startswith("Environment"))
    assert load_settings(None, {}, environ) == settings


def test_every_setting_has_its_variable_in_the_unit_with_what_it_does() -> None:
    # Un réglage ajouté sans description ou, sans défaut, sans exemple : refusé ici.
    lines = as_environment(ServiceSettings())
    assert len(lines) == 2 * len(ServiceSettings.model_fields)
    for field, comment, line in zip(
        ServiceSettings.model_fields, lines[::2], lines[1::2], strict=True
    ):
        assert comment.startswith(f"# {describe(field)}"), field
        assert line.removeprefix("# ").startswith(f"Environment={env_name(field)}="), field
    unset = [c for c in lines[::2] if c.endswith("— non réglé par défaut")]
    assert len(unset) == 2


def test_the_token_is_read_from_its_variable_never_given() -> None:
    settings = ServiceSettings(token_env="MEM_GPU_TOKEN")
    assert settings.token({"MEM_GPU_TOKEN": " s3cret "}) == "s3cret"
    with pytest.raises(SettingsError, match="MEM_GPU_TOKEN"):
        settings.token({})
    assert ServiceSettings().token({}) is None


# --- Serveur -------------------------------------------------------------------------


class Reply:
    def __init__(self, status: int, headers: dict[str, str], body: bytes) -> None:
        self.status = status
        self.headers = headers
        self.body = body

    def json(self) -> Any:
        return json.loads(self.body)


def call(
    service: Service,
    method: str,
    path: str,
    payload: object | None = None,
    *,
    token: str | None = None,
) -> Reply:
    host, port = service.address
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(f"http://{host}:{port}{path}", data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    if token is not None:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return Reply(response.status, dict(response.headers), response.read())
    except urllib.error.HTTPError as error:
        return Reply(error.code, dict(error.headers), error.read())


def raw(service: Service, head: str) -> tuple[int, Any]:
    """Une requête écrite à la main : en-têtes seuls, sans corps."""
    with socket.create_connection(service.address, timeout=10) as connection:
        connection.sendall(head.encode())
        received = b""
        while chunk := connection.recv(65536):
            received += chunk
    status_line, _, rest = received.partition(b"\r\n")
    _, _, body = rest.partition(b"\r\n\r\n")
    return int(status_line.split()[1]), json.loads(body)


def fake_host(**changes: Any) -> Host:
    values: dict[str, Any] = {
        "embedder": FakeEmbedder(),
        "reranker": FakeReranker(),
        "algo": "fake/1",
        "fp16": False,
    }
    values.update(changes)
    return Host(**values)


class Running:
    """Un service ouvert sur un port libre, servi dans un thread."""

    def __init__(self, host: Host, token: str | None = None) -> None:
        self.service = Service(host, token)
        self.service.bind("127.0.0.1", 0)
        self.thread = threading.Thread(target=self.service.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.service.stop()
        self.thread.join(timeout=10)
        self.service.close()


@pytest.fixture
def started() -> Iterator[Callable[..., Service]]:
    opened: list[Running] = []

    def start(host: Host | None = None, *, token: str | None = None, load: bool = True) -> Service:
        found = host if host is not None else fake_host()
        if load:
            found.load()
        running = Running(found, token)
        opened.append(running)
        return running.service

    yield start
    for running in opened:
        running.close()


def test_health_says_loading_until_the_models_are_ready(started: Callable[..., Service]) -> None:
    host = fake_host()
    service = started(host, load=False)
    reply = call(service, "GET", "/v1/health")
    assert reply.status == 503 and reply.headers["Retry-After"] == "1"
    assert reply.json() == {"protocol": PROTOCOL, "state": "loading"}
    refused = call(service, "POST", "/v1/embed", {"texts": ["x"]})
    assert refused.status == 503 and "chargement" in refused.json()["error"]
    host.load()
    ready = call(service, "GET", "/v1/health").json()
    assert ready["state"] == "ready" and ready["algo"] == "fake/1"
    assert ready["embedder"] == "fake-64" and ready["reranker"] == "fake-reranker"
    assert ready["dim"] == 64 and ready["max_texts"] == MAX_TEXTS
    assert ready["max_chars"] == MAX_CHARS and ready["fp16"] is False


def test_embed_returns_what_the_model_computed(started: Callable[..., Service]) -> None:
    service = started()
    texts = ["devis chauffe-eau", "facture plomberie"]
    reply = call(service, "POST", "/v1/embed", {"texts": texts})
    assert reply.status == 200
    found = [from_item(item) for item in EmbedResponse.model_validate(reply.json()).items]
    expected = FakeEmbedder().embed_documents(texts)
    assert [e.sparse for e in found] == [e.sparse for e in expected]
    # Le dense passe en float32 : celui du modèle, arrondi comme la base le range.
    assert [e.dense for e in found] == [array("f", e.dense).tolist() for e in expected]


def test_score_returns_one_score_per_passage(started: Callable[..., Service]) -> None:
    service = started()
    passages = ["devis chauffe-eau neuf", "rien à voir"]
    reply = call(service, "POST", "/v1/score", {"query": "devis chauffe-eau", "passages": passages})
    assert reply.status == 200
    assert reply.json() == {"scores": FakeReranker().score("devis chauffe-eau", passages)}


def test_caps_are_refused_with_413(started: Callable[..., Service]) -> None:
    service = started()
    too_many = call(service, "POST", "/v1/embed", {"texts": ["x"] * (MAX_TEXTS + 1)})
    too_long = call(service, "POST", "/v1/score", {"query": "q", "passages": ["x" * 20_001]})
    assert (too_many.status, too_long.status) == (413, 413)
    assert "au plus 32" in too_many.json()["error"]


def test_the_body_size_is_checked_before_reading_it(started: Callable[..., Service]) -> None:
    service = started()
    status_code, body = raw(
        service,
        f"POST /v1/embed HTTP/1.1\r\nHost: x\r\nContent-Length: {MAX_BODY_BYTES + 1}\r\n\r\n",
    )
    # Aucun octet de corps n'a été envoyé : la réponse vient sans l'attendre.
    assert status_code == 413 and "au plus" in body["error"]
    status_code, body = raw(service, "POST /v1/embed HTTP/1.1\r\nHost: x\r\n\r\n")
    assert status_code == 400 and body == {"error": "Content-Length absent"}


def test_bad_requests_say_why(started: Callable[..., Service]) -> None:
    service = started()
    host, port = service.address
    request = urllib.request.Request(
        f"http://{host}:{port}/v1/embed", data=b"{pas du json", method="POST"
    )
    with pytest.raises(urllib.error.HTTPError) as unreadable:
        urllib.request.urlopen(request, timeout=10)
    assert unreadable.value.code == 400
    extra = call(service, "POST", "/v1/embed", {"texts": ["x"], "kind": "query"})
    assert extra.status == 422 and "kind" in extra.json()["error"]
    unknown = call(service, "POST", "/v1/search", {"query": "x"})
    assert unknown.status == 404
    assert call(service, "GET", "/v1/embed").status == 404


def test_a_token_is_required_everywhere_once_set(started: Callable[..., Service]) -> None:
    service = started(token="s3cret")
    assert call(service, "GET", "/v1/health").status == 401
    assert call(service, "POST", "/v1/embed", {"texts": ["x"]}, token="faux").status == 401
    assert call(service, "GET", "/v1/health", token="s3cret").status == 200
    assert call(service, "POST", "/v1/embed", {"texts": ["x"]}, token="s3cret").status == 200


def test_a_full_queue_answers_503_and_when_to_retry(started: Callable[..., Service]) -> None:
    service = started()
    service.server.slots = threading.BoundedSemaphore(1)
    service.server.slots.acquire()
    reply = call(service, "POST", "/v1/embed", {"texts": ["x"]})
    assert reply.status == 503 and reply.headers["Retry-After"] == "1"
    assert "file d'attente" in reply.json()["error"]


class Failing(FakeEmbedder):
    def embed_documents(self, texts: Sequence[str], cancel: threading.Event | None = None) -> Any:
        raise ModelRunError("Calcul du modèle fake impossible : CUDA out of memory.")


def test_a_failed_computation_is_a_500_with_its_message(started: Callable[..., Service]) -> None:
    service = started(fake_host(embedder=Failing()))
    reply = call(service, "POST", "/v1/embed", {"texts": ["x"]})
    assert reply.status == 500 and "out of memory" in reply.json()["error"]
    # Le reranker, lui, sert toujours.
    assert call(service, "POST", "/v1/score", {"query": "q", "passages": ["q"]}).status == 200


class Unloadable(FakeEmbedder):
    def load(self) -> None:
        raise ModelLoadError("Modèle BAAI/bge-m3 absent du cache local de Hugging Face")


def test_a_failed_load_is_told_then_the_service_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_module, "FAILED_GRACE", 1.0)
    host = fake_host(embedder=Unloadable())
    running = Running(host)
    try:
        loading = running.service.start_loading()
        deadline = time.monotonic() + 5
        while host.state == "loading" and time.monotonic() < deadline:
            time.sleep(0.01)
        # Pendant le délai de grâce, l'erreur se lit.
        health = call(running.service, "GET", "/v1/health")
        assert health.status == 503 and "Retry-After" not in health.headers
        assert health.json()["state"] == "failed" and "absent du cache" in health.json()["error"]
        loading.join(timeout=5)
        running.thread.join(timeout=5)
        assert not running.thread.is_alive()
        assert running.service.exit_code == EXIT_LOAD
    finally:
        running.close()


def test_a_second_instance_on_the_same_port_is_refused() -> None:
    running = Running(fake_host())
    try:
        host, port = running.service.address
        with pytest.raises(PortTaken, match=str(port)):
            Service(fake_host(), None).bind(host, port)
    finally:
        running.close()


class Slow(FakeEmbedder):
    def __init__(self) -> None:
        super().__init__()
        self.inside = threading.Event()

    def embed_documents(self, texts: Sequence[str], cancel: threading.Event | None = None) -> Any:
        self.inside.set()
        time.sleep(0.3)
        return super().embed_documents(texts, cancel)


def test_stopping_lets_the_request_in_progress_finish() -> None:
    slow = Slow()
    host = fake_host(embedder=slow)
    host.load()
    running = Running(host)
    replies: list[Reply] = []
    asking = threading.Thread(
        target=lambda: replies.append(call(running.service, "POST", "/v1/embed", {"texts": ["x"]}))
    )
    asking.start()
    assert slow.inside.wait(5)
    running.service.stop()
    running.thread.join(timeout=5)
    running.service.close()
    asking.join(timeout=5)
    assert [r.status for r in replies] == [200]
    assert running.service.exit_code == EXIT_OK


def test_the_log_never_holds_the_texts(
    started: Callable[..., Service], caplog: pytest.LogCaptureFixture
) -> None:
    service = started()
    with caplog.at_level(logging.INFO, logger="loom_ia.memory.server"):
        call(service, "POST", "/v1/embed", {"texts": [SECRET_TEXT]})
        call(service, "POST", "/v1/score", {"query": SECRET_TEXT, "passages": [SECRET_TEXT]})
    assert "POST /v1/embed 200 1 texte(s)" in caplog.text
    assert "INV-2026" not in caplog.text and "Martin" not in caplog.text


# --- run, main et status -------------------------------------------------------------


def test_without_the_memory_extra_run_says_which_to_install(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(app_module, "missing_modules", lambda: ["torch"])
    with caplog.at_level(logging.ERROR, logger="loom_ia.memory.server"):
        assert run(ServiceSettings(port=1)) == EXIT_SETTINGS
    assert "loom-ia[memory]" in caplog.text


def test_a_taken_port_stops_run_before_any_loading(monkeypatch: pytest.MonkeyPatch) -> None:
    loads: list[str] = []

    class Counted(FakeEmbedder):
        def load(self) -> None:
            loads.append("embedder")

    def counted_host(_settings: ServiceSettings) -> Host:
        return fake_host(embedder=Counted())

    monkeypatch.setattr(app_module, "missing_modules", list)
    monkeypatch.setattr(app_module, "bge_host", counted_host)
    running = Running(fake_host())
    try:
        _, port = running.service.address
        assert run(ServiceSettings(port=port)) == EXIT_PORT
        assert loads == []
    finally:
        running.close()


def test_an_empty_token_variable_stops_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MEM_GPU_TOKEN", raising=False)
    assert run(ServiceSettings(token_env="MEM_GPU_TOKEN")) == EXIT_SETTINGS


def test_main_refuses_bad_settings(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--host", "0.0.0.0"]) == EXIT_SETTINGS
    assert main(["serve", "--max-vram-fraction", "2"]) == EXIT_SETTINGS
    assert "boucle locale" in capsys.readouterr().err


def test_main_serves_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[ServiceSettings] = []

    def fake_run(settings: ServiceSettings) -> int:
        seen.append(settings)
        return EXIT_OK

    monkeypatch.setattr("loom_ia.tools_builtin.memory.server.run", fake_run)
    assert main(["--device", "cpu", "--offline", "--no-fp16"]) == EXIT_OK
    assert main([]) == EXIT_OK
    first, second = seen
    assert (first.device, first.offline, first.fp16) == ("cpu", True, False)
    assert (second.device, second.offline, second.fp16) == ("cuda", False, True)


def test_check_validates_without_loading_or_listening(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def never(_settings: ServiceSettings) -> Host:
        raise AssertionError("--check ne charge rien")

    monkeypatch.setattr(app_module, "bge_host", never)
    monkeypatch.setattr(app_module, "missing_modules", list)
    assert main(["serve", "--check", "--device", "cpu", "--port", "8800", "--offline"]) == 0
    assert "réglages valides — 127.0.0.1:8800, device cpu, fp16, hors ligne" in (
        capsys.readouterr().out
    )
    # Un mauvais réglage, l'extra absent, un jeton introuvable : code 2, comme run.
    assert main(["serve", "--check", "--device", "gpu"]) == EXIT_SETTINGS
    monkeypatch.setattr(app_module, "missing_modules", lambda: ["torch"])
    assert main(["serve", "--check"]) == EXIT_SETTINGS
    assert "loom-ia[memory]" in capsys.readouterr().err
    monkeypatch.setattr(app_module, "missing_modules", list)
    monkeypatch.delenv("MEM_GPU_TOKEN", raising=False)
    assert main(["serve", "--check", "--token-env", "MEM_GPU_TOKEN"]) == EXIT_SETTINGS
    assert "MEM_GPU_TOKEN" in capsys.readouterr().err
    # --env : les mêmes vérifications, puis les lignes de l'unité au lieu du message.
    assert main(["serve", "--check", "--env", "--port", "8800"]) == 0
    out = capsys.readouterr().out
    assert "Environment=LOOM_MEM_GPU_PORT=8800" in out and "réglages valides" not in out
    assert main(["serve", "--env", "--device", "gpu"]) == EXIT_SETTINGS


def test_main_reads_the_variables_and_options_win(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(app_module, "missing_modules", list)
    monkeypatch.setenv("LOOM_MEM_GPU_DEVICE", "cpu")
    monkeypatch.setenv("LOOM_MEM_GPU_PORT", "8801")
    assert main(["serve", "--check", "--port", "8802"]) == 0
    assert "127.0.0.1:8802, device cpu" in capsys.readouterr().out
    monkeypatch.setenv("LOOM_MEM_GPU_PROT", "8803")
    assert main(["serve", "--check"]) == EXIT_SETTINGS
    assert "LOOM_MEM_GPU_PROT" in capsys.readouterr().err


def test_version_says_what_loom_checks(capsys: pytest.CaptureFixture[str]) -> None:
    from loom_ia import __version__
    from loom_ia.tools_builtin.memory.bge_m3 import ALGO
    from loom_ia.tools_builtin.memory.protocol import PROTOCOL

    with pytest.raises(SystemExit) as done:
        main(["--version"])
    assert done.value.code == 0
    out = capsys.readouterr().out
    assert out.strip() == f"loom-mem-gpu {__version__} (protocole {PROTOCOL}, {ALGO})"


def test_the_log_level_is_a_setting_like_the_others(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[ServiceSettings] = []

    def fake_run(settings: ServiceSettings) -> int:
        seen.append(settings)
        return EXIT_OK

    levels: list[object] = []

    def fake_config(**kwargs: object) -> None:
        levels.append(kwargs["level"])

    monkeypatch.setattr("loom_ia.tools_builtin.memory.server.run", fake_run)
    monkeypatch.setattr(logging, "basicConfig", fake_config)
    assert main([]) == EXIT_OK
    monkeypatch.setenv("LOOM_MEM_GPU_LOG_LEVEL", "WARNING")
    assert main([]) == EXIT_OK
    assert main(["--log-level", "DEBUG"]) == EXIT_OK
    assert [s.log_level for s in seen] == ["INFO", "WARNING", "DEBUG"]
    assert levels == ["INFO", "WARNING", "DEBUG"]  # appliqué au journal
    monkeypatch.setenv("LOOM_MEM_GPU_LOG_LEVEL", "BAVARD")
    assert main([]) == EXIT_SETTINGS


def test_under_systemd_the_log_lines_are_not_dated_twice() -> None:
    assert log_format({"JOURNAL_STREAM": "8:12345"}) == "%(levelname)s %(name)s : %(message)s"
    assert log_format({}).startswith("%(asctime)s ")


def test_status_describes_a_ready_service(
    started: Callable[..., Service], capsys: pytest.CaptureFixture[str]
) -> None:
    service = started()
    host, port = service.address
    assert status(f"http://{host}:{port}", None) == 0
    out = capsys.readouterr().out
    assert "ready (protocole 1" in out and "fake-64" in out and "fake-reranker" in out


def test_status_of_a_loading_or_absent_service_is_1(
    started: Callable[..., Service], capsys: pytest.CaptureFixture[str]
) -> None:
    service = started(load=False)
    host, port = service.address
    assert status(f"http://{host}:{port}", None) == 1
    assert "loading" in capsys.readouterr().out
    with socket.socket() as free:
        free.bind(("127.0.0.1", 0))
        _, unused = free.getsockname()
    assert status(f"http://127.0.0.1:{unused}", None) == 1
    assert "injoignable" in capsys.readouterr().err


def test_status_ignores_the_proxies_of_the_environment(
    started: Callable[..., Service], monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    host, port = started().address
    assert status(f"http://{host}:{port}", None) == 0


def test_status_sends_the_token(
    started: Callable[..., Service], monkeypatch: pytest.MonkeyPatch
) -> None:
    service = started(token="s3cret")
    host, port = service.address
    url = f"http://{host}:{port}"
    assert status(url, None) == 1
    monkeypatch.setenv("MEM_GPU_TOKEN", "s3cret")
    assert status(url, "MEM_GPU_TOKEN") == 0


# --- Isolement -----------------------------------------------------------------------


def test_the_application_loads_nothing_of_loom_beyond_the_memory() -> None:
    forbidden = (
        "access",
        "adapters",
        "agents",
        "config",
        "engine",
        "replay",
        "runtime",
        "sessions",
        "telemetry",
        "tenancy",
    )
    code = (
        "import sys, loom_ia.tools_builtin.memory.server\n"
        f"bad = [m for m in sys.modules if m.split('.')[1:2] and m.split('.')[1] in {forbidden!r}"
        " and m.startswith('loom_ia.')]\n"
        "print(sorted(bad)); print('torch' in sys.modules)"
    )
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, timeout=60
    )
    assert done.stdout.split("\n")[:2] == ["[]", "False"]


def test_nothing_in_loom_imports_the_application() -> None:
    package = SRC / "loom_ia"
    server = package / "tools_builtin" / "memory" / "server"
    importers: list[str] = []
    for path in package.rglob("*.py"):
        if path.is_relative_to(server):
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module, *(f"{node.module}.{a.name}" for a in node.names)]
            if any(n.startswith("loom_ia.tools_builtin.memory.server") for n in names):
                importers.append(str(path.relative_to(SRC)))
    assert importers == []
