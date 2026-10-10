# SPDX-License-Identifier: Apache-2.0
"""Ligne de commande : ``validate``, ``run``, ``resume``, ``approve``, ``keys``, ``schema``."""

import asyncio
import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn

import pytest
from conftest import ANSWER, MODEL, QUESTION, ConfigFactory, demo_agent

from loom_ia.access.api import Loom
from loom_ia.access.cli import main
from loom_ia.adapters.stores import JsonlEventStore
from loom_ia.config.keys import matches
from loom_ia.core.events import Event, EventDraft, RunClaimed
from loom_ia.core.model import DEFAULT_TENANT, SessionId, ToolOutput
from loom_ia.testing import RunJournal, tool_call_message

JOURNAL: dict[str, Any] = {"events": {"backend": "jsonl", "path": "journaux"}}


def test_validate_shows_what_the_config_declares(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--config", str(demo()), "validate"]) == 0
    out = capsys.readouterr().out
    assert "Modèles    : FAKE" in out
    assert "Agents     : demo" in out
    assert "Outils     : calculer" in out
    assert "Clés d'API : aucune" in out
    assert "1 agent(s) monté(s) sans erreur." in out


def test_validate_mounts_the_internal_compaction_agent(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    models = [MODEL, {**MODEL, "id": "RESUME", "model": "fake-resume"}]
    path = demo(models=models, sessions={"compaction": {"model": "RESUME"}})
    assert main(["--config", str(path), "validate"]) == 0
    out = capsys.readouterr().out
    # L'agent interne est monté, mais ce n'est pas un agent que la config déclare.
    assert "_compaction : modèle RESUME" in out
    assert "1 agent(s) monté(s) sans erreur." in out


def test_validate_refuses_a_compaction_model_without_its_key(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("anthropic")
    monkeypatch.delenv("CLE_DU_RESUME", raising=False)
    resume = {
        "id": "RESUME",
        "sdk": "anthropic",
        "model": "claude-haiku-4-5-20251001",
        "api_key_env": "CLE_DU_RESUME",
    }
    path = demo(models=[MODEL, resume], sessions={"compaction": {"model": "RESUME"}})
    # Sans cela, la clé manquante ne se verrait qu'au premier résumé, dans une
    # tâche de fond qui n'échoue pas le run.
    assert main(["--config", str(path), "validate"]) == 2
    assert "CLE_DU_RESUME" in capsys.readouterr().err


ABSENTE = "LOOM_VALIDATE_CLE_ABSENTE"
REEL: dict[str, Any] = {"id": "REEL", "sdk": "openai", "model": "gpt-x", "api_key_env": ABSENTE}


def test_validate_without_keys_mounts_the_agents_a_missing_key_would_refuse(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ABSENTE, raising=False)
    path = demo(
        models=[REEL], agents=[demo_agent(main={"model": "REEL", "system_file": "demo.md"})]
    )

    assert main(["--config", str(path), "validate"]) == 2
    assert ABSENTE in capsys.readouterr().err

    assert main(["--config", str(path), "validate", "--sans-cles"]) == 0
    out = capsys.readouterr().out
    assert "clients de modèle non créés (--sans-cles)" in out
    assert "1 agent(s) monté(s) sans erreur." in out


def test_run_prints_the_answer(demo: ConfigFactory, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--config", str(demo()), "run", "demo", QUESTION]) == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == ANSWER
    assert "Statut     : completed" in captured.err


def test_run_in_json_gives_the_whole_result(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--config", str(demo()), "run", "demo", QUESTION, "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert (result["status"], result["text"], result["iterations"]) == ("completed", ANSWER, 2)


def test_run_in_stream_shows_the_answer_as_it_comes(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--config", str(demo()), "run", "demo", QUESTION, "--stream"]) == 0
    captured = capsys.readouterr()
    assert captured.out.startswith("Je calcule.")
    assert ANSWER in captured.out
    assert "· calculer(expr='12*7+3')" in captured.err


def test_resume_finishes_an_interrupted_run(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    path = demo(storage=JOURNAL)
    journal = RunJournal(agent="demo")
    journal.start(QUESTION).model_turn(
        tool_call_message(("c1", "calculer", {"expr": "12*7+3"}), text="Je calcule.")
    ).tool_results({"c1": ToolOutput.text("87")})
    _write(path.parent / "journaux", journal)

    assert main(["--config", str(path), "resume", journal.run_id]) == 0
    assert capsys.readouterr().out.strip() == ANSWER


def test_the_same_run_id_twice_is_refused(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    path = demo(storage=JOURNAL)
    command = ["--config", str(path), "run", "demo", QUESTION, "--run-id", "run-unique"]
    assert main(command) == 0
    capsys.readouterr()
    assert main(command) == 2
    assert "existe déjà" in capsys.readouterr().err


def test_an_unknown_agent_is_refused(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--config", str(demo()), "run", "absent", QUESTION]) == 2
    assert "Agent 'absent' inconnu" in capsys.readouterr().err


def test_a_missing_config_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--config", str(tmp_path / "rien.yaml"), "validate"]) == 2
    assert "Configuration :" in capsys.readouterr().err


def test_a_file_that_cannot_be_written_is_refused_not_raised(
    demo: ConfigFactory, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Un ``OSError`` d'exploitation sort en message et en code 2, sans trace."""
    path = demo(storage=JOURNAL)
    assert main(["--config", str(path), "run", "demo", QUESTION, "--session", "atelier"]) == 0
    capsys.readouterr()
    absent = tmp_path / "rien" / "atelier.jsonl"

    code = main(["--config", str(path), "sessions", "export", "atelier", "--out", str(absent)])

    assert code == 2
    err = capsys.readouterr().err
    assert err.startswith("Erreur d'entrée-sortie : ")
    assert str(absent) in err


def test_an_agent_closed_to_a_tenant_keeps_its_own_refusal(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    """``AgentNotAllowed`` est une ``PermissionError`` : l'``OSError`` ne doit pas la prendre."""
    tenants = [{"id": "dupont", "agents": ["autre"]}]
    agents = [demo_agent(), demo_agent(name="autre")]
    path = demo(agents=agents, tenants=tenants)

    assert main(["--config", str(path), "run", "demo", QUESTION, "--tenant", "dupont"]) == 2
    err = capsys.readouterr().err
    assert "non ouvert au client" in err
    assert "Erreur d'entrée-sortie" not in err


def test_resuming_a_run_held_elsewhere_is_refused_not_raised(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    """Un autre worker mène le run (concession vivante) : refus lisible, code 2, sans trace."""
    path = demo(storage=JOURNAL)
    journal = RunJournal(agent="demo", session_id=SessionId("atelier"))
    journal.start(QUESTION)
    until = datetime.now(UTC) + timedelta(seconds=600)
    drafts: list[EventDraft] = [
        *journal.take(),
        journal.scope.draft(RunClaimed(worker_id="worker-ailleurs", lease_until=until)),
    ]
    store = JsonlEventStore(path.parent / "journaux")

    async def held() -> None:
        await store.append(drafts, expected_seq=0)
        await store.aclose()

    asyncio.run(held())

    assert main(["--config", str(path), "resume", journal.run_id, "--session", "atelier"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert f"Run {journal.run_id} : piloté par worker-ailleurs" in captured.err
    assert "réessayer après l'échéance" in captured.err


def test_a_programming_error_keeps_its_traceback(
    demo: ConfigFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Seules les erreurs d'exploitation sont converties : un bogue doit se voir."""

    async def broken(self: Loom, *args: object, **kwargs: object) -> NoReturn:
        raise RuntimeError("bogue")

    monkeypatch.setattr(Loom, "run", broken)
    with pytest.raises(RuntimeError, match="bogue"):
        main(["--config", str(demo()), "run", "demo", QUESTION])


def test_a_closed_input_refuses_the_confirmation_and_deletes_nothing(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sans terminal (cron, ``< /dev/null``), ``input()`` lève ``EOFError``."""
    path = demo(storage=JOURNAL)
    assert main(["--config", str(path), "run", "demo", QUESTION, "--session", "atelier"]) == 0
    capsys.readouterr()

    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert main(["--config", str(path), "sessions", "delete", "atelier"]) == 2
    err = capsys.readouterr().err
    assert "Entrée standard fermée" in err
    assert "--yes" in err

    assert main(["--config", str(path), "sessions", "list"]) == 0
    assert "atelier" in capsys.readouterr().out


def test_keys_create_gives_a_key_and_its_fingerprint(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["keys", "create", "atelier", "--scope", "run", "--agent", "demo"]) == 0
    out = capsys.readouterr().out
    key = _field(out, "Clé        :")
    assert key.startswith("lk_")
    assert matches(key, _field(out, "hash:"))
    assert "- id: atelier" in out
    assert "scopes: [run]" in out and "agents: [demo]" in out


def test_schema_is_valid_json(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["schema"]) == 0
    schema = json.loads(capsys.readouterr().out)
    assert "AgentSpec" in schema["$defs"]
    assert schema["properties"]["version"]


def test_serve_starts_the_rest_access(
    demo: ConfigFactory, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    http = pytest.importorskip("loom_ia.access.http", reason="extra 'http' absent")
    served: dict[str, Any] = {}

    def fake_serve(loom: Any, *, host: str | None = None, port: int | None = None) -> None:
        served["agents"] = loom.names
        served["host"], served["port"] = host, port

    monkeypatch.setattr(http, "serve", fake_serve)
    assert main(["--config", str(demo()), "serve", "--port", "9100"]) == 0
    assert served == {"agents": ("demo",), "host": None, "port": 9100}
    assert "http://127.0.0.1:9100/v1" in capsys.readouterr().out


def test_mcp_serves_on_stdio(
    demo: ConfigFactory, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    server = pytest.importorskip("loom_ia.access.mcp_server", reason="extra 'mcp' absent")
    served: dict[str, Any] = {}

    async def fake_stdio(loom: Any, tenant: str = "default") -> None:
        served["agents"] = loom.names
        served["tenant"] = tenant

    monkeypatch.setattr(server, "run_stdio", fake_stdio)
    assert main(["--config", str(demo()), "mcp"]) == 0
    assert served == {"agents": ("demo",), "tenant": "default"}
    # Rien sur stdout : le protocole y passe.
    assert capsys.readouterr().out == ""


# --- Trancher une approbation depuis un autre terminal (J4.5) -----------------


def _paused(path: Path, capsys: pytest.CaptureFixture[str]) -> str:
    """Lance le run de l'atelier, qui s'arrête sur son approbation ; rend son identifiant."""
    # Le run n'a pas répondu : la commande le dit par son code de sortie.
    assert main(["--config", str(path), "run", "demo", "Relance.", "--json"]) == 1
    started = json.loads(capsys.readouterr().out)
    assert started["status"] == "paused"
    return str(started["run_id"])


def test_run_says_what_a_paused_run_is_waiting_for(
    atelier: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--config", str(atelier()), "run", "demo", "Relance."]) == 1
    erreur = capsys.readouterr().err
    assert "Statut     : paused" in erreur
    assert "En attente : envoyer_email" in erreur and "loom approve" in erreur


def test_approve_finishes_the_run_in_this_terminal(
    atelier: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    path = atelier()
    run_id = _paused(path, capsys)

    assert main(["--config", str(path), "approve", run_id, "--by", "denis"]) == 0
    fini = capsys.readouterr()
    assert fini.out.strip() == "Relance envoyée."
    assert "Accordé : " in fini.err
    assert _decisions(path, "approval.granted") == ["denis"]


def test_reject_hands_the_reason_back_to_the_model(
    atelier: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    path = atelier()
    run_id = _paused(path, capsys)

    # Un refus n'arrête pas le run : l'orchestrateur en fait ce qu'il peut.
    assert main(["--config", str(path), "reject", run_id, "--reason", "mauvais devis"]) == 0
    assert "Refusé : " in capsys.readouterr().err
    assert _decisions(path, "approval.rejected") == [None]


@pytest.mark.parametrize(
    ("verb", "decision"), [("approve", "approval.granted"), ("reject", "approval.rejected")]
)
def test_no_wait_writes_the_decision_and_leaves_the_run(
    atelier: ConfigFactory, capsys: pytest.CaptureFixture[str], verb: str, decision: str
) -> None:
    path = atelier()
    run_id = _paused(path, capsys)

    assert main(["--config", str(path), verb, run_id, "--no-wait"]) == 0
    capsys.readouterr()
    # La décision est au journal, et rien ne la suit : ni reprise ni concession.
    types = [event.type for event in _events(path)]
    assert types[-1] == decision
    assert "run.completed" not in types
    # Personne n'a piloté la reprise : le run attend toujours d'être repris.
    assert main(["--config", str(path), "resume", run_id]) == 0
    assert capsys.readouterr().out.strip() == "Relance envoyée."
    assert [event.type for event in _events(path)][-1] == "run.completed"


def test_without_no_wait_the_decision_is_followed_by_the_resumption(
    atelier: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    path = atelier()
    run_id = _paused(path, capsys)

    assert main(["--config", str(path), "approve", run_id]) == 0
    assert capsys.readouterr().out.strip() == "Relance envoyée."
    types = [event.type for event in _events(path)]
    assert types.index("approval.granted") < types.index("run.completed") == len(types) - 1


def test_approving_a_run_that_awaits_nothing_is_refused(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    path = demo(storage=JOURNAL)
    assert main(["--config", str(path), "run", "demo", QUESTION, "--json"]) == 0
    run_id = json.loads(capsys.readouterr().out)["run_id"]

    assert main(["--config", str(path), "approve", run_id]) == 2
    assert "rien n'attend de décision" in capsys.readouterr().err


def test_correcting_arguments_needs_a_designated_call(
    atelier: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    path = atelier()
    run_id = _paused(path, capsys)

    sans_call = main(["--config", str(path), "approve", run_id, "--arguments", "{}"])
    assert sans_call == 2 and "--call" in capsys.readouterr().err


def test_a_corrected_argument_is_the_one_that_goes(
    atelier: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    path = atelier()
    run_id = _paused(path, capsys)
    call_id = _awaited(path, run_id)

    code = main(
        [
            "--config",
            str(path),
            "approve",
            run_id,
            "--call",
            call_id,
            "--arguments",
            '{"destinataire": "compta@example.com"}',
        ]
    )

    assert code == 0
    events = _events(path)
    # Le journal ne réécrit pas l'appel du modèle : ``tool.called`` garde ce
    # qu'il avait demandé, et c'est le résultat qui montre ce qui est parti.
    [called] = [e.payload for e in events if e.type == "tool.called"]
    [done] = [e.payload for e in events if e.type == "tool.completed"]
    assert getattr(called, "arguments", {})["destinataire"] == "mme.martin@example.com"
    assert "compta@example.com" in str(getattr(done, "output", ""))


def test_an_unknown_call_id_is_refused(
    atelier: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    path = atelier()
    run_id = _paused(path, capsys)

    assert main(["--config", str(path), "approve", run_id, "--call", "absent"]) == 2
    assert "absent" in capsys.readouterr().err


def _events(path: Path) -> list[Event]:
    """Journal de l'unique session écrite par l'atelier."""
    store = JsonlEventStore(path.parent / "data")

    async def go() -> list[Event]:
        [record] = await store.sessions(DEFAULT_TENANT)
        events = await store.read(DEFAULT_TENANT, record.session_id)
        await store.aclose()
        return list(events)

    return asyncio.run(go())


def _decisions(path: Path, type_: str) -> list[str | None]:
    """Auteurs des décisions d'un type, tels que le journal les garde."""
    return [
        None if (by := event.facets.get("by")) is None else str(by)
        for event in _events(path)
        if event.type == type_
    ]


def _awaited(path: Path, run_id: str) -> str:
    """Identifiant du seul appel que le run attend."""
    [asked] = [e.payload for e in _events(path) if e.type == "approval.requested"]
    return str(getattr(asked, "call_id", ""))


def _write(directory: Path, journal: RunJournal) -> None:
    store = JsonlEventStore(directory)

    async def go() -> None:
        await store.append(journal.take(), expected_seq=0)
        await store.aclose()

    asyncio.run(go())


def _field(out: str, label: str) -> str:
    line = next(line for line in out.splitlines() if label in line)
    return line.split(label, 1)[1].strip()
