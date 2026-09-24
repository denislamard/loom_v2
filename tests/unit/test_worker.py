# SPDX-License-Identifier: Apache-2.0
"""File servie par un courtier et `loom worker` : ce qui se vérifie sans RabbitMQ (J5.3b).

Le courtier lui-même est éprouvé dans ``tests/integration/test_worker_rabbitmq.py``,
qui parle à un vrai RabbitMQ quand ``LOOM_TEST_RABBITMQ`` en désigne un.
"""

from importlib.util import find_spec
from typing import Any

import pytest
from conftest import ConfigFactory

from loom_ia.access.api import Loom
from loom_ia.access.cli import main
from loom_ia.adapters.queue import AsyncioTaskQueue
from loom_ia.config import ConfigError, load_config
from loom_ia.config.models import BROKERED_QUEUES, QUEUE_BACKENDS, QueueStorage
from loom_ia.core.model import RunId, SessionId, TenantId
from loom_ia.core.ports import Job, ServedQueue, TaskQueue
from loom_ia.runtime import create_task_queue

URL = "amqp://loom:secret@127.0.0.1:5672/"
VARIABLE = "LOOM_RABBITMQ_URL_ESSAI"
RABBITMQ: dict[str, Any] = {"queue": {"backend": "rabbitmq", "url_env": VARIABLE}}

sans_extra = pytest.mark.skipif(find_spec("aio_pika") is None, reason="extra 'rabbitmq' absent")
# L'inverse : ce qui ne se voit que sans l'extra, dans la passe noyau seul.
avec_extra = pytest.mark.skipif(
    find_spec("aio_pika") is not None, reason="extra 'rabbitmq' présent"
)


def queue_of(path: Any) -> TaskQueue:
    return create_task_queue(load_config(path), {})


# --- La config ---------------------------------------------------------------


def test_the_default_queue_runs_where_it_is_filled(demo: ConfigFactory) -> None:
    declared = QueueStorage()
    assert declared.backend == "asyncio"
    assert not declared.brokered
    assert isinstance(queue_of(demo()), AsyncioTaskQueue)


def test_rabbitmq_is_the_only_brokered_backend() -> None:
    assert BROKERED_QUEUES == ("rabbitmq",)
    assert set(BROKERED_QUEUES) <= set(QUEUE_BACKENDS)
    assert QueueStorage(backend="rabbitmq", url_env=VARIABLE).brokered


def test_the_config_names_the_variable_never_the_url() -> None:
    declared = QueueStorage(backend="rabbitmq", url_env=VARIABLE)
    assert declared.url_env == VARIABLE
    assert URL not in repr(declared)


# --- Le câblage --------------------------------------------------------------


@sans_extra
def test_an_empty_variable_is_named_in_the_error(
    demo: ConfigFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(VARIABLE, raising=False)
    with pytest.raises(ConfigError, match=rf"{VARIABLE}.* est vide ou absente"):
        queue_of(demo(storage=RABBITMQ))


@sans_extra
def test_the_queue_is_built_from_the_variable(
    demo: ConfigFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(VARIABLE, URL)
    queue = queue_of(demo(storage=RABBITMQ))
    # Rien n'est ouvert avant la première mise en file : le courtier peut être absent.
    assert repr(queue) == "RabbitMqTaskQueue('loom.jobs')"
    assert isinstance(queue, ServedQueue)
    assert not isinstance(AsyncioTaskQueue({}), ServedQueue)


@sans_extra
async def test_a_brokered_queue_answers_without_a_broker(
    demo: ConfigFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``state`` et ``cancel`` ne consultent rien : le journal fait foi, pas la file."""
    monkeypatch.setenv(VARIABLE, URL)
    queue = queue_of(demo(storage=RABBITMQ))
    assert await queue.state("01a0") == "unknown"
    assert await queue.cancel("01a0") is False
    # Ce qui est publié tourne ailleurs : il n'y a rien à attendre ici.
    assert await queue.drain() is None


@avec_extra
def test_without_the_extra_the_refusal_names_it(
    demo: ConfigFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ce que voit qui déclare une file rabbitmq sans avoir installé l'extra."""
    monkeypatch.setenv(VARIABLE, URL)
    with pytest.raises(ConfigError, match=r"loom-ia\[rabbitmq\]"):
        queue_of(demo(storage=RABBITMQ))


# --- Le message --------------------------------------------------------------


@sans_extra
def test_a_job_travels_as_readable_json() -> None:
    from loom_ia.adapters.queue.rabbitmq import decoded, encoded

    job = Job(
        kind="compaction",
        tenant_id=TenantId("dupont-plomberie"),
        session_id=SessionId("s-42"),
        run_id=RunId("r-7"),
        params={"limite": 3},
    )
    body = encoded("j-1", job)
    assert b"dupont-plomberie" in body
    job_id, back = decoded(body)
    assert job_id == "j-1"
    assert back == job


@sans_extra
def test_a_job_that_names_no_run_travels_too() -> None:
    from loom_ia.adapters.queue.rabbitmq import decoded, encoded

    job = Job(kind="compaction", tenant_id=TenantId("t"), session_id=SessionId("s"))
    _, back = decoded(encoded("j-2", job))
    assert back.run_id is None
    assert back.params == {}


@sans_extra
@pytest.mark.parametrize(
    ("body", "message"),
    [
        (b"pas du json", "Message illisible"),
        (b'"une chaine"', "qui n'est pas un objet"),
        (b'{"kind": "menage"}', "type inconnu"),
    ],
)
def test_a_message_that_is_not_a_job_says_why(body: bytes, message: str) -> None:
    from loom_ia.adapters.queue.rabbitmq import decoded

    with pytest.raises(ValueError, match=message):
        decoded(body)


# --- Le worker ---------------------------------------------------------------


async def test_a_queue_that_runs_at_home_has_nothing_to_serve(demo: ConfigFactory) -> None:
    """`loom worker` sur une file en mémoire : il n'y a rien à consommer."""
    async with Loom(load_config(demo())) as loom:
        with pytest.raises(ConfigError, match=r"storage.queue.backend: rabbitmq"):
            await loom.work()
        with pytest.raises(ConfigError, match="'asyncio'"):
            await loom.stop_work()


def test_the_worker_refuses_a_queue_that_is_not_served(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--config", str(demo()), "worker"]) == 2
    captured = capsys.readouterr()
    assert "File     : asyncio" in captured.out
    assert "storage.queue.backend: rabbitmq" in captured.err


def test_the_worker_refuses_less_than_one_job(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--config", str(demo()), "worker", "--jobs", "0"]) == 2
    assert "--jobs : au moins 1" in capsys.readouterr().err


@sans_extra
def test_validate_shows_the_queue_and_says_a_worker_is_needed(
    demo: ConfigFactory, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(VARIABLE, URL)
    assert main(["--config", str(demo(storage=RABBITMQ)), "validate"]) == 0
    out = capsys.readouterr().out
    assert f"File       : rabbitmq (URL dans {VARIABLE} : renseignée)" in out
    assert "les tâches de fond attendent un worker : loom worker" in out
    # L'URL porte un mot de passe : elle n'est jamais imprimée.
    assert "secret" not in out


def test_validate_says_when_the_variable_is_missing(
    demo: ConfigFactory, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(VARIABLE, raising=False)
    # La file n'est ouverte qu'à la première mise en file : `validate` va au bout.
    assert main(["--config", str(demo(storage=RABBITMQ)), "validate"]) == 2
    assert f"URL dans {VARIABLE} : ABSENTE" in capsys.readouterr().out
