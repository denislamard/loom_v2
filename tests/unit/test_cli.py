# SPDX-License-Identifier: Apache-2.0
"""Ligne de commande : ``validate``, ``run``, ``resume``, ``approve``, ``keys``, ``schema``."""

import asyncio
import io
import json
import sys
from datetime import UTC, datetime, timedelta
from importlib.util import find_spec
from pathlib import Path
from typing import Any, NoReturn

import pytest
from conftest import (
    ANSWER,
    HOSTILE,
    HOSTILE_SHOWN,
    MODEL,
    QUESTION,
    ConfigFactory,
    demo_agent,
)

from loom_ia.access.api import Loom
from loom_ia.access.cli import main
from loom_ia.access.cli.main import _state_item  # pyright: ignore[reportPrivateUsage]
from loom_ia.adapters.stores import JsonlEventStore
from loom_ia.config.keys import matches
from loom_ia.config.models import QueueStorage
from loom_ia.core.events import Event, EventDraft, RunClaimed
from loom_ia.core.model import DEFAULT_TENANT, Message, RunStatus, SessionId, ToolOutput
from loom_ia.core.ports import JournalBusy, JournalCorrupted
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


def test_validate_tells_what_is_wrong_with_the_declared_state(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    """Au chargement (schéma de la clé) comme au montage (source ``from``)."""
    loaded: dict[str, Any] = {"k": {"schema": {"type": "chaine"}}}
    assert main(["--config", str(demo(agents=[demo_agent(state=loaded)])), "validate"]) == 2
    assert "state.k — schema invalide" in capsys.readouterr().err

    # « calculer » rend du texte : rien à lire au chemin « /resultat ».
    mounted: dict[str, Any] = {
        "k": {"schema": {}, "from": [{"tool": "calculer", "path": "/resultat"}]}
    }
    assert main(["--config", str(demo(agents=[demo_agent(state=mounted)])), "validate"]) == 2
    assert "state 'k' : l'outil 'calculer' ne rend pas de données" in capsys.readouterr().err

    fixed: dict[str, Any] = {"k": {"schema": {}, "from": [{"tool": "calculer", "value": "fait"}]}}
    assert main(["--config", str(demo(agents=[demo_agent(state=fixed)])), "validate"]) == 0
    assert "1 agent(s) monté(s) sans erreur." in capsys.readouterr().out


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

    # L'option s'écrit en anglais ; l'écriture d'avant reste comprise.
    for flag in ("--no-keys", "--sans-cles"):
        assert main(["--config", str(path), "validate", flag]) == 0
        out = capsys.readouterr().out
        assert "clients de modèle non créés (--no-keys)" in out
        assert "1 agent(s) monté(s) sans erreur." in out


def test_run_prints_the_answer(demo: ConfigFactory, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--config", str(demo()), "run", "demo", QUESTION]) == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == ANSWER
    assert "Statut     : completed" in captured.err


@pytest.mark.parametrize("flag", ["-q", "--quiet"])
def test_run_quiet_prints_only_the_answer(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str], flag: str
) -> None:
    assert main(["--config", str(demo()), "run", "demo", QUESTION, flag]) == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == ANSWER
    # Les logs suivent leur propre niveau ; seul le bilan se tait.
    assert "Statut" not in captured.err and "Run        :" not in captured.err


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


def test_the_answer_reaches_a_pipe_whole_and_a_terminal_without_driving_it(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """La réponse d'un run va telle quelle dans un tube ; un terminal voit ses contrôles sans agir.

    Le déroulé et le bilan, sur la sortie d'erreur, ne sont lus que par un humain :
    leurs contrôles se montrent toujours. Ici, un appel refusé porte le nom
    hostile que le modèle a demandé.
    """
    script: list[dict[str, Any]] = [
        {"tool_calls": [{"name": f"inconnu{HOSTILE}", "arguments": {}}]},
        {"text": HOSTILE},
    ]
    path = demo(models=[{**MODEL, "params": {"script": script}}])
    command = ["--config", str(path), "run", "demo", QUESTION]
    for flags in ([], ["--stream"]):
        assert main([*command, *flags]) == 0
        captured = capsys.readouterr()
        assert HOSTILE in captured.out
        assert not {"\x1b", "\x07", "\x9b"} & set(captured.err)
    assert f"· inconnu{HOSTILE_SHOWN} : refusé" in captured.err

    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    for flags in ([], ["--stream"]):
        assert main([*command, *flags]) == 0
        captured = capsys.readouterr()
        assert HOSTILE_SHOWN in captured.out
        assert not {"\x1b", "\x07", "\x9b"} & set(captured.out + captured.err)


# State : ``client_id`` obligatoire, ``rang`` un entier, ``calcul`` posé par ``calculer``.
STATE: dict[str, Any] = {
    "client_id": {"schema": {"type": "string"}, "required": True},
    "rang": {"schema": {"type": "integer"}},
    "calcul": {"schema": {"type": "string"}, "from": [{"tool": "calculer", "value": "fait"}]},
}


def test_run_poses_the_state_and_state_reads_it(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--state`` lit sa valeur en JSON, sinon en texte ; ``loom state`` montre la session."""
    path = str(demo(storage=JOURNAL, agents=[demo_agent(state=STATE)]))
    asked = ["--state", "client_id=CL 42", "--state", "rang=3", "--session", "atelier"]
    assert main(["--config", path, "run", "demo", QUESTION, *asked]) == 0
    capsys.readouterr()

    assert main(["--config", path, "state", "atelier"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown == {"calcul": "fait", "client_id": "CL 42", "rang": 3}

    # ``null`` efface : la clé obligatoire manque, le run est refusé avant tout écrit.
    erase = ["--state", "client_id=null", "--session", "atelier"]
    assert main(["--config", path, "run", "demo", QUESTION, *erase]) == 2
    assert capsys.readouterr().err == (
        "Demande refusée : State refusé : client_id : clé obligatoire sans valeur\n"
    )
    assert main(["--config", path, "state", "inconnue"]) == 1
    assert capsys.readouterr().err == "Session inconnue inconnue\n"


@pytest.mark.parametrize(
    ("given", "value"),
    [
        ("note=b=c", "b=c"),
        ("note=", ""),
        ("note=007", "007"),
        ("note=NaN", "NaN"),
        ("note=-Infinity", "-Infinity"),
        ("note=1e999", "1e999"),
        ('note="3"', "3"),
        ("note=[1, 2]", [1, 2]),
        ("note=null", None),
    ],
)
def test_a_state_value_is_json_or_else_text(given: str, value: object) -> None:
    """NaN et les infinis ne sont pas du JSON : ils restent du texte."""
    assert _state_item(given) == ("note", value)


def test_state_of_an_unreadable_session_is_one_line(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    path = demo(storage=JOURNAL)
    journal = RunJournal(agent="demo", session_id=SessionId("casse"))
    journal.start(QUESTION).model_turn(Message.assistant(ANSWER)).complete()
    journal.transition(RunStatus.READY_FOR_MODEL)
    _write(path.parent / "journaux", journal)

    assert main(["--config", str(path), "state", "casse"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("Historique de la session casse illisible")
    assert "Traceback" not in err


@pytest.mark.parametrize(
    ("given", "said"),
    [
        (["--state", "client_id"], "'client_id' : attendu CLE=VALEUR"),
        (["--state", "=x"], "'=x' : attendu CLE=VALEUR"),
        (["--state", "a=1", "--state", "a=2"], "--state : clé donnée deux fois : a"),
    ],
)
def test_a_malformed_state_is_refused(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str], given: list[str], said: str
) -> None:
    path = str(demo(agents=[demo_agent(state=STATE)]))
    with pytest.raises(SystemExit) as exited:
        code = main(["--config", path, "run", "demo", QUESTION, *given])
        raise SystemExit(code)
    assert exited.value.code == 2
    assert said in capsys.readouterr().err


@pytest.mark.skipif(find_spec("aiosqlite") is None, reason="extra 'sqlite' absent")
def test_sessions_list_shows_a_session_id_without_obeying_it(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    """Un journal SQLite accepte tout identifiant de session : la liste ne le laisse pas agir."""
    path = demo(storage={"events": {"backend": "sqlite", "path": "loom.db"}})
    assert main(["--config", str(path), "run", "demo", QUESTION, "--session", HOSTILE]) == 0
    capsys.readouterr()
    assert main(["--config", str(path), "sessions", "list"]) == 0
    out = capsys.readouterr().out
    assert out.startswith(HOSTILE_SHOWN) and not {"\x1b", "\x07", "\x9b"} & set(out)


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


def test_a_session_with_an_unreadable_run_is_refused_in_one_line(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    path = demo(storage=JOURNAL)
    journal = RunJournal(agent="demo", session_id=SessionId("casse"))
    journal.start(QUESTION).model_turn(Message.assistant(ANSWER)).complete()
    journal.transition(RunStatus.READY_FOR_MODEL)
    _write(path.parent / "journaux", journal)

    assert main(["--config", str(path), "run", "demo", QUESTION, "--session", "casse"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    # Le journal de l'engine garde sa ligne d'erreur ; la commande en ajoute une, sans trace.
    last = captured.err.strip().splitlines()[-1]
    assert last.startswith("Historique de la session casse illisible")
    assert "Traceback" not in captured.err


@pytest.mark.parametrize(
    ("error", "code", "words"),
    [
        (JournalBusy("loom_events : un verrou n'a pas pu être pris"), 2, "verrou"),
        (JournalCorrupted("/x/s.jsonl : dernière ligne illisible"), 1, "Journal illisible"),
    ],
)
def test_a_busy_or_corrupted_journal_is_one_line_not_a_traceback(
    demo: ConfigFactory,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    code: int,
    words: str,
) -> None:
    async def failing(self: Loom, *args: object, **kwargs: object) -> NoReturn:
        raise error

    monkeypatch.setattr(Loom, "run", failing)
    assert main(["--config", str(demo()), "run", "demo", QUESTION]) == code
    captured = capsys.readouterr()
    assert words in captured.err and "Traceback" not in captured.err
    assert captured.err.count("\n") == 1


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


@pytest.mark.parametrize("flags", [["--yes"], []])
@pytest.mark.parametrize("unusable", [""])
def test_deleting_a_session_with_an_unusable_id_is_refused_in_one_line(
    demo: ConfigFactory,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    unusable: str,
    flags: list[str],
) -> None:
    """``loom sessions delete ""`` effaçait tout le client : code 2, une ligne, sans question."""
    path = demo(storage=JOURNAL)
    assert main(["--config", str(path), "run", "demo", QUESTION, "--session", "atelier"]) == 0
    capsys.readouterr()
    # Les fichiers d'une autre session du même client : ils ne doivent pas partir.
    kept = path.parent / "journaux" / ".artifacts" / "default" / "autre" / "garde.png"
    kept.parent.mkdir(parents=True)
    kept.write_bytes(b"a garder")

    def no_question(prompt: str = "") -> NoReturn:
        raise AssertionError(f"question posée : {prompt}")

    monkeypatch.setattr("builtins.input", no_question)
    assert main(["--config", str(path), "sessions", "delete", unusable, *flags]) == 2
    err = capsys.readouterr().err
    assert err.count("\n") == 1 and "inutilisable" in err

    assert main(["--config", str(path), "sessions", "list"]) == 0
    assert "atelier" in capsys.readouterr().out
    assert kept.read_bytes() == b"a garder"


def test_keys_create_refuses_an_expiry_that_is_not_a_number(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """« ²j » passait ``isdigit`` et ``int`` le refusait : le refus nomme l'option."""
    assert main(["keys", "create", "atelier", "--scope", "run", "--expires", "²j"]) == 2
    assert "--expires" in capsys.readouterr().err


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


def test_run_quiet_still_says_what_a_paused_run_is_waiting_for(
    atelier: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    """``-q`` tait le bilan, pas ce qu'il reste à faire pour que le run aboutisse."""
    assert main(["--config", str(atelier()), "run", "demo", "Relance.", "-q"]) == 1
    erreur = capsys.readouterr().err
    assert "Statut" not in erreur and "Run        :" not in erreur
    assert "En attente : envoyer_email" in erreur and "loom approve" in erreur


def test_the_proposed_approve_command_names_its_session_and_tenant(
    atelier: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    """Copiée telle quelle, la commande doit trouver le run (test D7 du 18/10)."""
    path = str(atelier(tenants=[{"id": "default"}, {"id": "dupont"}]))
    args = ["--config", path, "run", "demo", "Relance.", "--session", "s1", "--tenant", "dupont"]
    assert main(args) == 1
    erreur = capsys.readouterr().err
    assert "--call " in erreur and erreur.rstrip().endswith("--session s1 --tenant dupont")
    # Sans session ni client donnés, la commande n'en porte pas : le run est son journal.
    assert main(["--config", path, "run", "demo", "Relance."]) == 1
    seule = capsys.readouterr().err
    assert "loom approve" in seule and "--session" not in seule and "--tenant" not in seule


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


@pytest.mark.parametrize("verb", ["approve", "reject"])
def test_with_a_broker_the_resumption_is_left_to_the_worker(
    atelier: ConfigFactory,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    verb: str,
) -> None:
    """Test H7 du 21/10 : la commande relisait la pause et sortait en 1."""
    path = atelier()
    run_id = _paused(path, capsys)
    # Le travail part au courtier : rien n'est à attendre dans ce terminal.
    monkeypatch.setattr(QueueStorage, "brokered", property(lambda self: True))
    monkeypatch.setattr(Loom, "drain", _never_drained)

    assert main(["--config", str(path), verb, run_id, "--session", run_id]) == 0
    sortie = capsys.readouterr()
    assert sortie.out == ""
    assert sortie.err.splitlines()[-1] == (
        f"Reprise confiée au worker (file asyncio) : suivre avec loom sessions show {run_id}"
    )


async def _never_drained(self: Loom) -> None:
    raise AssertionError("avec un courtier, la commande n'attend pas la reprise")


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


# --- Version et point d'entrée du paquet (CIP-6) -------------------------------------------


def test_version_prints_the_installed_version_and_exits_zero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from loom_ia import __version__

    with pytest.raises(SystemExit) as stop:
        main(["--version"])
    assert stop.value.code == 0
    assert capsys.readouterr().out.strip() == f"loom {__version__}"


def test_the_package_runs_as_a_module() -> None:
    """``python -m loom_ia`` est le même programme que ``loom``."""
    import subprocess
    import sys

    from loom_ia import __version__

    done = subprocess.run(
        [sys.executable, "-m", "loom_ia", "--version"], capture_output=True, text=True, timeout=60
    )
    assert (done.returncode, done.stdout.strip()) == (0, f"loom {__version__}")


def test_help_does_not_leak_internal_milestone_names(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["report", "--help"])
    assert "J5" not in capsys.readouterr().out


def test_a_missing_extra_names_the_package_not_the_tool(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], demo: ConfigFactory
) -> None:
    """Le message vaut pour pip comme pour uv (CIP-5) : l'extra, pas ``uv sync``."""
    import builtins

    real = builtins.__import__

    def refuse(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "loom_ia.access.http":
            raise ImportError(name)
        return real(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse)
    assert main(["--config", str(demo()), "serve"]) == 2
    err = capsys.readouterr().err
    assert "loom-ia[http]" in err and "uv sync" not in err


# --- Journal en mémoire, contrats et correspondance des modèles dans `validate` (19/10) ---


MEMORY_SAID = (
    "Journal en mémoire : il ne vit que le temps d'un process ; "
    "cette commande ne voit pas les runs des autres commandes loom.\n"
)


@pytest.mark.parametrize(
    "command",
    [
        ["sessions", "list"],
        ["sessions", "list", "--json"],
        ["sessions", "show", "s-1"],
        ["sessions", "files", "s-1"],
        ["sessions", "export", "s-1"],
        ["sessions", "delete", "s-1", "--yes"],
    ],
)
def test_a_memory_journal_is_said_to_keep_nothing_between_commands(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str], command: list[str]
) -> None:
    """Test F5 du 19/10 : « Aucune session. » juste après un run, sans dire pourquoi."""
    main(["--config", str(demo()), *command])
    captured = capsys.readouterr()
    assert captured.err.startswith(MEMORY_SAID)
    assert "Journal en mémoire" not in captured.out  # stdout reste lisible par un script


def test_a_lasting_journal_says_nothing_about_memory(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--config", str(demo(storage=JOURNAL)), "sessions", "list"]) == 0
    assert "Journal en mémoire" not in capsys.readouterr().err


def test_the_journal_of_the_named_client_is_the_one_looked_at(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    """La racine est en JSONL, mais ce client a son journal à lui, en mémoire."""
    path = demo(
        storage=JOURNAL,
        tenants=[{"id": "dupont", "storage": {"events": {"backend": "memory"}}}, {"id": "martin"}],
    )
    assert main(["--config", str(path), "sessions", "list", "--tenant", "martin"]) == 0
    assert "Journal en mémoire" not in capsys.readouterr().err
    assert main(["--config", str(path), "sessions", "list", "--tenant", "dupont"]) == 0
    assert capsys.readouterr().err.startswith(MEMORY_SAID)


def test_validate_shows_what_each_contract_requires(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    """Test E1 du 19/10 : `validate` disait que `loom.contract` était posé, pas ce qu'il
    exigeait."""
    agent = demo_agent(
        output={
            "must_match": r"\d{2}/\d{2}/\d{4}",
            "must_not_match": "(?i)sk-",
            "max_chars": 400,
            "repair": {"max_attempts": 0},
            "on_failure": "fallback",
            "fallback_message": "Réponse indisponible pour le moment, merci de réessayer.",
        },
        tools=[
            {
                "python": "calculer",
                "output": {"schema": {"type": "object", "required": ["valeur", "unite"]}},
            }
        ],
    )
    assert main(["--config", str(demo(agents=[agent])), "validate"]) == 0
    out = capsys.readouterr().out
    assert (
        "    contrat (réponse finale) : must_match \\d{2}/\\d{2}/\\d{4}, must_not_match (?i)sk-, "
        "max_chars 400 ; réparations 0, échec → fallback "
        "« Réponse indisponible pour le moment, me… »\n"
    ) in out
    # Un outil ne se répare pas : pas de « réparations ».
    assert "    contrat (outil calculer) : schéma (requis : valeur, unite) ; échec → fail\n" in out


def test_validate_writes_a_client_model_mapping_apart_from_the_fallbacks(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    """Test E6 du 19/10 : sous « client dupont », le juge affichait « modèle OPUS » sans dire
    que ce client le fait tourner sur HAIKU ; et « → » est déjà le signe des secours."""
    models = [
        MODEL,
        {**MODEL, "id": "OPUS", "model": "fake-opus"},
        {**MODEL, "id": "SONNET", "model": "fake-sonnet"},
    ]
    agent = demo_agent(
        judge={
            "model": "OPUS",
            "fallbacks": ["SONNET"],
            "criteria": [{"name": "juste", "rule": "La réponse est juste."}],
        }
    )
    path = demo(
        models=models,
        agents=[agent],
        tenants=[{"id": "dupont", "models": {"OPUS": "FAKE"}}, {"id": "martin"}],
    )
    assert main(["--config", str(path), "validate"]) == 0
    out = capsys.readouterr().out
    dupont, martin = out.split("client martin")
    assert "juge output (réponse finale) : modèle OPUS (FAKE pour ce client) → SONNET" in dupont
    assert "juge output (réponse finale) : modèle OPUS → SONNET," in martin


def test_the_run_summary_names_the_cache_when_there_is_some() -> None:
    """Test J1 du 21/10 : « tokens : 50/543 » ne disait pas que 4 449 tokens d'entrée
    étaient passés par le cache, que le rapport comptait, lui."""
    from loom_ia.access.cli.main import _cache_said  # pyright: ignore[reportPrivateUsage]
    from loom_ia.core.model import Usage

    assert _cache_said(Usage(input_tokens=50, output_tokens=543)) == ""
    with_cache = Usage(
        input_tokens=50, output_tokens=543, cache_read_tokens=4300, cache_write_tokens=149
    )
    assert _cache_said(with_cache) == " · cache : lu 4300, écrit 149"


@pytest.mark.parametrize("action", ["stop", "warn"])
def test_the_run_summary_says_a_budget_was_reached(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str], action: str
) -> None:
    """Test J4 du 21/10 : un plafond atteint ne se voyait que dans le journal."""
    path = demo(budgets={"run": {"max_calls": 1}, "on_exceed": action})
    code = main(["--config", str(path), "run", "demo", QUESTION])
    err = capsys.readouterr().err

    assert code == 0
    said = "run arrêté" if action == "stop" else "avertissement"
    budget_lines = [line for line in err.splitlines() if line.startswith("Budget     : ")]
    assert len(budget_lines) == 1 and budget_lines[0].endswith(said)


def test_the_run_summary_has_no_budget_line_without_a_ceiling_reached(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--config", str(demo()), "run", "demo", QUESTION]) == 0
    assert "Budget     :" not in capsys.readouterr().err


@pytest.mark.parametrize("claimed", [None, -60, 600], ids=["sans concession", "échue", "vivante"])
def test_a_run_nobody_pilots_is_said_to_resume(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str], claimed: int | None
) -> None:
    """Test H1 du 20/10 : un run arrêté en plein vol (process tué) ne se signalait nulle part."""
    path = demo(storage=JOURNAL)
    journal = RunJournal(agent="demo", session_id=SessionId("atelier"))
    journal.start(QUESTION)
    drafts: list[EventDraft] = list(journal.take())
    if claimed is not None:
        until = datetime.now(UTC) + timedelta(seconds=claimed)
        drafts.append(journal.scope.draft(RunClaimed(worker_id="worker-1", lease_until=until)))
    store = JsonlEventStore(path.parent / "journaux")

    async def written() -> None:
        await store.append(drafts, expected_seq=0)
        await store.aclose()

    asyncio.run(written())
    hint = f"loom resume {journal.run_id} --session atelier"

    assert main(["--config", str(path), "sessions", "show", "atelier"]) == 0
    shown = capsys.readouterr().out
    assert main(["--config", str(path), "inspect", journal.run_id, "--session", "atelier"]) == 0
    inspected = capsys.readouterr().out
    piloted = claimed == 600
    assert (hint in shown) is not piloted
    assert (hint in inspected) is not piloted


def test_a_finished_run_is_not_said_to_resume(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    path = demo(storage=JOURNAL)
    assert main(["--config", str(path), "run", "demo", QUESTION, "--session", "atelier"]) == 0
    capsys.readouterr()
    assert main(["--config", str(path), "sessions", "show", "atelier"]) == 0
    assert "loom resume" not in capsys.readouterr().out


# --- Lot B du 23/10 : dire une fois, dire où ----------------------------------


def test_a_failed_run_says_its_error_once_on_stderr(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    """Test L2 du 22/10 : un 401 se lisait trois fois (log ERROR, « Erreur », stdout)."""
    panne = {**MODEL, "params": {"script": [{"error": "auth"}]}}
    path = demo(models=[panne], telemetry={"logging": {"level": "WARNING"}})
    assert main(["--config", str(path), "run", "demo", QUESTION]) == 1
    printed = capsys.readouterr()
    assert printed.out == "—\n"
    assert printed.err.count("Panne simulée") == 1
    assert "Erreur     : Panne simulée" in printed.err and "ERROR" not in printed.err


def test_global_options_are_read_after_the_subcommand(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test M4 du 23/10 : ``loom validate --profile prod`` était refusé par argparse."""
    path = demo()
    monkeypatch.chdir(path.parent.parent)
    assert main(["validate", "--config", str(path), "--profile", "prod", "--no-env-file"]) == 0
    out = capsys.readouterr().out
    assert f"Config     : {path}" in out and "prod (par option)" in out
    # Avant la sous-commande, comme avant ; après, elle l'emporte.
    assert main(["--profile", "prod", "--config", str(path), "validate", "--profile", "dev"]) == 0
    assert "dev (par option)" in capsys.readouterr().out
    assert main(["--config", str(path), "sessions", "list", "--profile", "dev"]) == 0


def test_a_reading_command_does_not_repeat_the_setup_warnings(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tests H7 et K4 : ``sessions show`` répétait les avertissements de montage."""
    pytest.importorskip("opentelemetry.sdk")
    monkeypatch.delenv("LOOM_OTLP_VIDE", raising=False)
    otel = {"exporters": [{"type": "otel", "endpoint_env": "LOOM_OTLP_VIDE"}]}
    path = str(demo(storage=JOURNAL, telemetry=otel))
    assert main(["--config", path, "run", "demo", QUESTION, "--session", "s"]) == 0
    assert "LOOM_OTLP_VIDE" in capsys.readouterr().err
    for command in (["sessions", "show", "s"], ["sessions", "list"], ["report", "--session", "s"]):
        assert main(["--config", path, *command]) == 0
        assert "LOOM_OTLP_VIDE" not in capsys.readouterr().err, command


def test_storage_sql_says_when_the_config_declares_no_postgres(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    """Test N4 du 23/10 : le SQL sortait sans dire qu'il n'était pas celui de la config."""
    assert main(["--config", str(demo(storage=JOURNAL)), "storage", "sql"]) == 0
    printed = capsys.readouterr()
    assert printed.err.startswith("Aucun stockage Postgres déclaré")
    assert "CREATE TABLE" in printed.out


def test_an_unknown_agent_does_not_offer_the_internal_ones(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    """Test N4 du 23/10 : « _compaction » était proposé à qui s'était trompé de nom."""
    models = [MODEL, {**MODEL, "id": "RESUME", "model": "fake-resume"}]
    path = demo(models=models, sessions={"compaction": {"model": "RESUME"}})
    assert main(["--config", str(path), "run", "demoo", QUESTION]) == 2
    err = capsys.readouterr().err
    assert "demo" in err and "_compaction" not in err


def test_a_config_error_names_a_model_by_its_id(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    """Test M1 du 21/10 : « models.0.max_token » obligeait à compter les modèles."""
    path = demo(models=[MODEL, {**MODEL, "id": "HAIKU", "max_token": 3}])
    assert main(["--config", str(path), "validate"]) == 2
    assert "models[HAIKU].max_token — Extra inputs are not permitted" in capsys.readouterr().err


def test_the_published_schema_leaves_out_base_dir(capsys: pytest.CaptureFixture[str]) -> None:
    """Test M1 du 21/10 : ``base_dir``, que le chargeur pose lui-même, était publié."""
    assert main(["schema"]) == 0
    assert "base_dir" not in json.loads(capsys.readouterr().out)["properties"]


def test_an_undefined_variable_names_the_prompt_file(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    """Test M7 du 23/10 : le refus nommait « main.system », pas le fichier à corriger."""
    path = demo()
    (path.parent / "prompts" / "demo.md").write_text("Pour {{ entreprise }}.", encoding="utf-8")
    # ``demo()`` réécrit le prompt : la config est relue telle quelle.
    assert main(["--config", str(path), "validate"]) == 2
    err = capsys.readouterr().err
    assert "Agent 'demo', main.system_file (prompts/demo.md) — {{ entreprise }}" in err


# --- Lot C du 23/10 : ce qui sort du journal ----------------------------------


def test_validate_says_there_is_no_collector(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--config", str(demo()), "validate"]) == 0
    out = capsys.readouterr().out
    assert "Télémétrie : logs " in out and "exports sans contenu (metadata)" in out
    assert "collecteur : aucun — rien ne sort du journal que les logs" in out


def test_validate_shows_the_collector_the_capture_and_the_overrides(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test K4 du 21/10 : ni exporteur, ni capture, ni motifs, et « rien de surchargé »."""
    pytest.importorskip("opentelemetry.sdk")
    monkeypatch.delenv("LOOM_OTLP_VIDE", raising=False)
    telemetry = {
        "logging": {"level": "WARNING"},
        "capture": {"raw_exchanges": True},
        "redaction": {"patterns": [{"name": "devis", "regex": r"D-\d+"}]},
        "exporters": [{"type": "otel", "endpoint_env": "LOOM_OTLP_VIDE"}],
    }
    tenants = [
        {"id": "default"},
        {"id": "dupont", "telemetry": {"capture": {"exports": "content"}}},
    ]
    path = demo(telemetry=telemetry, tenants=tenants)
    assert main(["--config", str(path), "validate"]) == 0
    printed = capsys.readouterr()
    assert (
        "Télémétrie : logs WARNING (console) ; exports sans contenu (metadata) ; "
        "échanges bruts au journal (256 Kio par corps au plus)"
    ) in printed.out
    assert (
        "collecteur otel : adresse dans LOOM_OTLP_VIDE (vide — non monté, aucune trace n'en "
        "partira), http/protobuf, service loom-ia, délai 10 s"
    ) in printed.out
    assert "masquage du contenu exporté : secret, email, phone, iban, devis" in printed.out
    client = printed.out[printed.out.index("client dupont") :]
    assert "capture : exports avec le contenu, masqué" in client
    assert "rien de surchargé" not in client
    # Dit une fois : la ligne de validate, pas aussi le log du montage.
    assert "LOOM_OTLP_VIDE" not in printed.err

    monkeypatch.setenv("LOOM_OTLP_VIDE", "http://127.0.0.1:9")
    assert main(["--config", str(path), "validate"]) == 0
    assert "adresse dans LOOM_OTLP_VIDE (renseignée)" in capsys.readouterr().out
