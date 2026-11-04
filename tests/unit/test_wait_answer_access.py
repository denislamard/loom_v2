# SPDX-License-Identifier: Apache-2.0
"""``loom.ask`` par la CLI et le REST (wait_answer, lot 3)."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from conftest import DEV
from test_wait_answer import config

from loom_ia.access import Loom
from loom_ia.access.cli import main
from loom_ia.config.keys import fingerprint, new_api_key

pytest.importorskip("fastapi", reason="extra 'http' absent")
pytest.importorskip("httpx2", reason="client HTTP de test absent")

import httpx2
import yaml

from loom_ia.access.http import create_app

# --- CLI -------------------------------------------------------------------------------


def test_cli_shows_the_question_and_a_message_answers_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = config(tmp_path)
    code = main(["--config", str(path), "run", "demo", "Prépare un devis.", "--session", "s1"])
    out, err = capsys.readouterr()
    # Un run qui attend la réponse de l'utilisateur n'a pas échoué.
    assert code == 0
    assert "Pour quel client" in out and "1. Dupont" in out and "2. Martin" in out
    assert "Statut     : paused" in err and 'loom run demo "<réponse>" --session s1' in err
    code = main(["--config", str(path), "run", "demo", "Dupont", "--session", "s1"])
    out, _ = capsys.readouterr()
    assert code == 0 and "Devis préparé." in out


def test_cli_prints_what_the_model_wrote_before_the_question(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from test_wait_answer import QUESTION

    script = [
        {"text": "Il est 12 h 43.", "tool_calls": [{"name": "ask", "arguments": QUESTION}]},
        {"text": "Devis préparé."},
    ]
    path = config(tmp_path, script)
    assert main(["--config", str(path), "run", "demo", "Prépare un devis.", "--session", "s1"]) == 0
    out, _ = capsys.readouterr()
    assert out.index("Il est 12 h 43.") < out.index("Pour quel client") < out.index("2. Martin")


def test_cli_answer_command(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = config(tmp_path)
    main(["--config", str(path), "run", "demo", "Prépare un devis.", "--json"])
    paused = capsys.readouterr().out
    import json

    run_id = json.loads(paused)["run_id"]
    assert main(["--config", str(path), "answer", run_id, "--answer", "Dupont;Martin"]) == 2
    assert "un seul choix" in capsys.readouterr().err
    assert main(["--config", str(path), "answer", run_id, "--answer", "Martin"]) == 0
    out, err = capsys.readouterr()
    assert "Répondu" in err and "Devis préparé" in out


def test_cli_shows_a_refusal_without_driving_the_terminal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Le refus nomme les questions par leur intitulé, écrit par le modèle : la CLI
    # le montre (« \\x1b ») sans l'exécuter (séquence OSC 52 : le presse-papier).
    import json

    two = {
        "questions": [
            {"question": "Relances faites ?", "header": "Rel\x1b]52;c;eA==\x07"},
            {"question": "Acompte versé ?", "header": "Acompte"},
        ]
    }
    script = [{"text": "", "tool_calls": [{"name": "ask", "arguments": two}]}, {"text": "Fait."}]
    path = config(tmp_path, script)
    main(["--config", str(path), "run", "demo", "Prépare la lettre.", "--json"])
    run_id = json.loads(capsys.readouterr().out)["run_id"]
    assert main(["--config", str(path), "answer", run_id, "--answer", "Oui"]) == 2
    err = capsys.readouterr().err
    assert "1 réponse pour 2 questions (" in err and "Acompte) : une réponse par question" in err
    assert "\x1b" not in err and "\\x1b" in err


def test_cli_answer_takes_the_number_shown(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # loom run affiche « 2. Martin » : --answer 2 répond « Martin » (essai Haiku, ask-07).
    path = config(tmp_path)
    main(["--config", str(path), "run", "demo", "Prépare un devis.", "--session", "s1"])
    _, err = capsys.readouterr()
    assert "--answer <n°>" in err
    run_id = next(
        line.split(":", 1)[1].strip() for line in err.splitlines() if line.startswith("Run ")
    )
    assert main(["--config", str(path), "answer", run_id, "--answer", "2", "--session", "s1"]) == 0
    assert "Devis préparé" in capsys.readouterr().out
    lines = (tmp_path / "data" / "default" / "s1.jsonl").read_text(encoding="utf-8")
    import json

    [answered] = [
        json.loads(line)["payload"]
        for line in lines.splitlines()
        if json.loads(line)["payload"]["type"] == "question.answered"
    ]
    assert answered["answers"] == [["Martin"]]


def test_numbers_become_labels_only_when_they_name_a_choice() -> None:
    from loom_ia.access.cli.main import _numbered  # pyright: ignore[reportPrivateUsage]
    from loom_ia.core.model import Question, QuestionOption

    clients = Question(
        question="Quel client ?",
        header="Client",
        options=(QuestionOption(label="Dupont"), QuestionOption(label="Martin")),
        multiple=True,
    )
    pieces = Question(
        question="Combien ?",
        header="Nombre",
        options=(QuestionOption(label="2"), QuestionOption(label="1")),
    )
    libre = Question(question="Quelle adresse ?", header="Adresse")
    assert _numbered([clients], [["2", "1"]]) == [["Martin", "Dupont"]]
    # Hors des choix, ou une question sans choix : le texte reste tel quel.
    assert _numbered([clients], [["3"]]) == [["3"]]
    assert _numbered([libre], [["12"]]) == [["12"]]
    # Un libellé qui est ce nombre l'emporte : « 2 » reste « 2 », « 1 » aussi.
    assert _numbered([pieces], [["1"]]) == [["1"]]
    # Pas autant de réponses que de questions : Loom.answer dira ce qui ne va pas.
    assert _numbered([clients, libre], [["1"]]) == [["1"]]


def test_cli_stream_routes_the_answer(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = config(tmp_path)
    main(["--config", str(path), "run", "demo", "Prépare un devis.", "--session", "s1", "--stream"])
    _, err = capsys.readouterr()
    assert "question à l'utilisateur : Pour quel client" in err
    code = main(["--config", str(path), "run", "demo", "Dupont", "--session", "s1", "--stream"])
    out, err = capsys.readouterr()
    assert code == 0 and "Devis préparé." in out
    assert "réponse de l'utilisateur reçue" in err


# Deux questions dans un même ask, dont une à choix multiples (essai réel t-2000, 02/11).
TWO = {
    "questions": [
        {
            "question": "Les relances ont-elles été faites ?",
            "header": "Relances",
            "options": [{"label": "Oui"}, {"label": "Non"}],
        },
        {
            "question": "Quelles pièces changer ?",
            "header": "Pièces",
            "options": [{"label": "Joint"}, {"label": "Robinet"}, {"label": "Siphon"}],
            "multiple": True,
        },
    ]
}
TWO_SCRIPT = [{"text": "", "tool_calls": [{"name": "ask", "arguments": TWO}]}, {"text": "Fait."}]


def test_cli_numbers_several_questions_and_gives_the_exact_command(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = config(tmp_path, TWO_SCRIPT)
    code = main(["--config", str(path), "run", "demo", "Prépare la lettre.", "--session", "s1"])
    out, err = capsys.readouterr()
    assert code == 0
    first = out.index("Question 1/2 · Relances")
    second = out.index("Question 2/2 · Pièces")
    # Chaque question a ses choix, et une ligne vide la sépare de la suivante.
    assert first < out.index("2. Non") < out.index("\n\nQuestion 2/2") < second
    assert out.index("3. Siphon") < out.index("plusieurs choix : les séparer par « ; »")
    run_id = next(
        line.split(":", 1)[1].strip() for line in err.splitlines() if line.startswith("Run ")
    )
    assert "Questions  : 2 en attente (" in err
    assert f"loom answer {run_id} --answer <Relances> --answer <Pièces> --session s1" in err
    assert 'loom run demo "<réponse>" --session s1' in err
    assert "--answer <n°>" not in err


def test_cli_one_question_keeps_its_rendering(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Une question seule : ni « Question 1/1 », ni intitulé, ni nouvelle ligne d'aide.
    path = config(tmp_path)
    main(["--config", str(path), "run", "demo", "Prépare un devis.", "--session", "s1"])
    out, err = capsys.readouterr()
    assert out.startswith("Pour quel client faut-il préparer le devis ?\n  1. Dupont\n  2. Martin")
    assert "Question 1/1" not in out and "Client" not in out
    assert "Question   : " in err and "--answer <n°> --session s1)" in err
    assert "--call" not in err and "Questions  :" not in err


def test_cli_stream_names_several_questions(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = config(tmp_path, TWO_SCRIPT)
    main(["--config", str(path), "run", "demo", "Prépare.", "--session", "s1", "--stream"])
    _, err = capsys.readouterr()
    assert "questions à l'utilisateur (2) : Relances ; Pièces" in err


def test_cli_marks_a_free_question_and_asks_no_number_for_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Essai réel t-4000 (02/11) : « --answer 1 » sur une question sans choix est parti en
    # texte « 1 » ; rien ne la distinguait d'une question à choix.
    asked = {
        "questions": [
            {
                "question": "Remplacer le fichier existant ?",
                "header": "Fichier",
                "options": [{"label": "Remplacer"}, {"label": "Autre nom"}],
            },
            {"question": "Vos coordonnées ?", "header": "Identité"},
        ]
    }
    script = [{"text": "", "tool_calls": [{"name": "ask", "arguments": asked}]}, {"text": "Ok."}]
    path = config(tmp_path, script)
    main(["--config", str(path), "run", "demo", "La lettre.", "--session", "s1"])
    out, _ = capsys.readouterr()
    assert "Question 2/2 · Identité\nVos coordonnées ?\n  réponse libre, en texte" in out
    assert out.count("réponse libre, en texte") == 1  # pas sous la question à choix

    # Une seule question, sans choix : pas de numéro à donner.
    one = {"questions": [{"question": "Vos coordonnées ?", "header": "Identité"}]}
    script = [{"text": "", "tool_calls": [{"name": "ask", "arguments": one}]}, {"text": "Ok."}]
    (tmp_path / "seule").mkdir()
    path = config(tmp_path / "seule", script)
    main(["--config", str(path), "run", "demo", "La lettre.", "--session", "s2"])
    out, err = capsys.readouterr()
    assert out.startswith("Vos coordonnées ?") and "réponse libre" not in out
    assert "--answer <réponse> --session s2)" in err and "<n°>" not in err


def test_placeholders_name_each_question() -> None:
    from loom_ia.access.cli.main import _placeholders  # pyright: ignore[reportPrivateUsage]
    from loom_ia.core.model import Question

    asked = (
        Question(question="Quel client ?", header="Client"),
        Question(question="Et le second ?", header="Client"),
        Question(question="Quelle date ?"),
        Question(question="Quel ton ?", header=" Ton "),
    )
    assert _placeholders(asked) == ["<Client 1>", "<Client 2>", "<réponse 3>", "<Ton>"]


def _paused(path: Path, capsys: pytest.CaptureFixture[str]) -> str:
    """Lance le run jusqu'à sa question ; rend son run_id."""
    import json

    main(["--config", str(path), "run", "demo", "Prépare.", "--json"])
    return json.loads(capsys.readouterr().out)["run_id"]


def test_cli_refusal_shows_the_command_to_type(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = config(tmp_path, TWO_SCRIPT)
    run_id = _paused(path, capsys)
    assert main(["--config", str(path), "answer", run_id, "--answer", "1"]) == 2
    err = capsys.readouterr().err
    assert "1 réponse pour 2 questions (Relances, Pièces)" in err
    assert f"  loom answer {run_id} --answer <Relances> --answer <Pièces>" in err


def test_cli_refuses_a_number_that_names_no_choice(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Avant, « 3 » sur deux choix partait au modèle comme texte libre.
    path = config(tmp_path, TWO_SCRIPT)
    run_id = _paused(path, capsys)
    code = main(["--config", str(path), "answer", run_id, "--answer", "3", "--answer", "1"])
    err = capsys.readouterr().err
    assert code == 2
    assert "question 1 (Relances) : choix 3 inconnu, attendu 1 à 2 ou un libellé." in err
    assert f'l\'écrire en message : loom answer {run_id} "3"' in err
    # Un choix multiple hors liste aussi ; rien n'a été écrit, la question attend.
    code = main(["--config", str(path), "answer", run_id, "--answer", "1", "--answer", "1;4"])
    assert code == 2 and "question 2 (Pièces) : choix 4 inconnu, attendu 1 à 3" in (
        capsys.readouterr().err
    )
    assert main(["--config", str(path), "answer", run_id, "--answer", "1", "--answer", "2"]) == 0


def test_cli_a_number_stays_text_without_choices_or_with_numeric_labels(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    asked = {
        "questions": [
            {"question": "Combien de radiateurs ?", "header": "Nombre"},
            {
                "question": "Combien d'étages ?",
                "header": "Étages",
                "options": [{"label": "1"}, {"label": "2"}],
            },
        ]
    }
    script = [{"text": "", "tool_calls": [{"name": "ask", "arguments": asked}]}, {"text": "Ok."}]
    path = config(tmp_path, script)
    run_id = _paused(path, capsys)
    assert main(["--config", str(path), "answer", run_id, "--answer", "3", "--answer", "5"]) == 0
    err = capsys.readouterr().err
    assert "Répondu    : Nombre → 3" in err and "             Étages → 5" in err


def test_cli_echoes_what_was_understood(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = config(tmp_path, TWO_SCRIPT)
    run_id = _paused(path, capsys)
    assert main(["--config", str(path), "answer", run_id, "--answer", "1", "--answer", "1;3"]) == 0
    err = capsys.readouterr().err
    assert "Répondu    : Relances → Oui\n             Pièces   → Joint ; Siphon" in err
    assert "toolu" not in err and "fake_" not in err.split("Répondu")[1].split("\n")[0]


def test_cli_echo_for_one_question_and_for_a_message(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = config(tmp_path)
    run_id = _paused(path, capsys)
    assert main(["--config", str(path), "answer", run_id, "--answer", "2"]) == 0
    assert "Répondu    : Client → Martin" in capsys.readouterr().err

    (tmp_path / "libre").mkdir()
    path = config(tmp_path / "libre")
    run_id = _paused(path, capsys)
    long = "Dupont, celui de Lyon, " * 10
    assert main(["--config", str(path), "answer", run_id, long]) == 0
    err = capsys.readouterr().err
    assert "Répondu    : message libre « Dupont, celui de Lyon," in err
    assert "… »" in err and long not in err


# --- REST ------------------------------------------------------------------------------

LANCEUR = new_api_key()
LECTEUR = new_api_key()


def keyed(tmp_path: Path) -> Path:
    path = config(tmp_path)
    root = yaml.safe_load(path.read_text(encoding="utf-8"))
    root["security"] = {
        **root["security"],
        "api_keys": [
            {"id": "appli", "user": DEV, "hash": fingerprint(LANCEUR), "scopes": ["run", "read"]},
            {"id": "lecture", "user": DEV, "hash": fingerprint(LECTEUR), "scopes": ["read"]},
        ],
    }
    path.write_text(yaml.safe_dump(root), encoding="utf-8")
    return path


@asynccontextmanager
async def http_serving(path: Path) -> AsyncGenerator[tuple[Loom, httpx2.AsyncClient]]:
    async with Loom.from_config(path) as loom:
        transport = httpx2.ASGITransport(create_app(loom))
        async with httpx2.AsyncClient(transport=transport, base_url="http://loom.test") as http:
            yield loom, http


def test_rest_answer_route_needs_run_not_approve(tmp_path: Path) -> None:
    async def go() -> None:
        async with http_serving(keyed(tmp_path)) as (loom, http):
            appli = {"Authorization": f"Bearer {LANCEUR}"}
            started = await http.post(
                "/v1/agents/demo/runs",
                json={"message": "Prépare un devis.", "session_id": "s1"},
                headers=appli,
            )
            paused = started.json()
            assert started.status_code == 201 and paused["status"] == "paused"
            assert paused["pending_questions"][0]["questions"][0]["header"] == "Client"
            url = f"/v1/runs/{paused['run_id']}/answer?session_id=s1"
            lecture = {"Authorization": f"Bearer {LECTEUR}"}
            refused = await http.post(url, json={"text": "Dupont"}, headers=lecture)
            assert refused.status_code == 403
            bad = await http.post(url, json={"answers": [["a"], ["b"]]}, headers=appli)
            assert bad.status_code == 422 and "2 réponse" in bad.json()["detail"]
            done = await http.post(url, json={"answers": [["Dupont"]]}, headers=appli)
            assert done.status_code == 200
            assert done.json() == {
                "run_id": paused["run_id"],
                "call_id": paused["pending_questions"][0]["call_id"],
            }
            await loom.drain()
            finished = await http.get(f"/v1/runs/{paused['run_id']}?session_id=s1", headers=appli)
            assert finished.json()["status"] == "completed"
            unknown = await http.post(url, json={"text": "x"}, headers=appli)
            assert unknown.status_code == 404

    asyncio.run(go())


def test_rest_a_refusal_names_the_questions(tmp_path: Path) -> None:
    # Le refus du moteur nomme les questions : c'est le detail du 422 (multi-questions, lot 0).
    async def go() -> None:
        async with http_serving(config(tmp_path, TWO_SCRIPT)) as (loom, http):
            started = await http.post(
                "/v1/agents/demo/runs", json={"message": "La lettre.", "session_id": "s1"}
            )
            run_id = started.json()["run_id"]
            url = f"/v1/runs/{run_id}/answer?session_id=s1"
            bad = await http.post(url, json={"answers": [["Oui"]]})
            assert bad.status_code == 422
            assert bad.json()["detail"] == (
                "Réponse refusée : 1 réponse pour 2 questions (Relances, Pièces) : "
                "une réponse par question, dans leur ordre"
            )
            done = await http.post(url, json={"answers": [["Oui"], ["Joint", "Siphon"]]})
            assert done.status_code == 200
            await loom.drain()

    asyncio.run(go())


def test_rest_a_new_message_answers_and_resumes(tmp_path: Path) -> None:
    async def go() -> None:
        async with http_serving(config(tmp_path)) as (_, http):
            first = await http.post(
                "/v1/agents/demo/runs", json={"message": "Prépare un devis.", "session_id": "s1"}
            )
            again = await http.post(
                "/v1/agents/demo/runs", json={"message": "Dupont", "session_id": "s1"}
            )
            assert again.status_code == 201
            assert again.json()["run_id"] == first.json()["run_id"]
            assert again.json()["status"] == "completed"

    asyncio.run(go())


def test_rest_reading_without_read_content_hides_the_question(tmp_path: Path) -> None:
    async def go() -> None:
        async with http_serving(keyed(tmp_path)) as (_, http):
            appli = {"Authorization": f"Bearer {LANCEUR}"}
            started = await http.post(
                "/v1/agents/demo/runs",
                json={"message": "Prépare un devis.", "session_id": "s1"},
                headers=appli,
            )
            run_id = started.json()["run_id"]
            read = await http.get(f"/v1/runs/{run_id}?session_id=s1", headers=appli)
            body = read.json()
            assert body["pending_questions"][0]["questions"] == []
            assert "pending_questions[].questions" in body["redacted"]

    asyncio.run(go())


def test_cli_sessions_show_lists_the_question(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = config(tmp_path)
    main(["--config", str(path), "run", "demo", "Prépare un devis.", "--session", "s1"])
    capsys.readouterr()
    assert main(["--config", str(path), "sessions", "show", "s1"]) == 0
    out = capsys.readouterr().out
    assert "question (fake_0_0)" in out and "sans échéance : Pour quel client" in out
    assert 'loom run demo "<réponse>" --session s1' in out
    # Les choix, pour répondre par numéro sans relancer le run (lot 3).
    assert out.index("Pour quel client") < out.index("      1. Dupont") < out.index("2. Martin")
    assert out.index("2. Martin") < out.index('loom run demo "<réponse>"')


def test_cli_sessions_show_lists_several_questions(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = config(tmp_path, TWO_SCRIPT)
    main(["--config", str(path), "run", "demo", "Prépare la lettre.", "--session", "s1"])
    run_id = next(
        line.split(":", 1)[1].strip()
        for line in capsys.readouterr().err.splitlines()
        if line.startswith("Run ")
    )
    assert main(["--config", str(path), "sessions", "show", "s1"]) == 0
    out = capsys.readouterr().out
    assert "  question (fake_0_0) — run " in out and "sans échéance\n" in out
    first, second = out.index("    Question 1/2 · Relances"), out.index("    Question 2/2 · Pièces")
    assert first < out.index("      2. Non") < out.index("\n\n    Question 2/2") < second
    assert out.index("      3. Siphon") < out.index("    plusieurs choix")
    assert (
        f"    loom answer {run_id} --call fake_0_0 --answer <Relances> --answer <Pièces> "
        "--session s1" in out
    )
    assert '    (ou en message libre : loom run demo "<réponse>" --session s1)' in out


def test_rest_a_background_message_answers_too(tmp_path: Path) -> None:
    async def go() -> None:
        async with http_serving(config(tmp_path)) as (loom, http):
            first = await http.post(
                "/v1/agents/demo/runs", json={"message": "Prépare un devis.", "session_id": "s1"}
            )
            again = await http.post(
                "/v1/agents/demo/runs",
                json={"message": "Dupont", "session_id": "s1", "background": True},
            )
            assert again.status_code == 202
            assert again.json()["run_id"] == first.json()["run_id"]
            await loom.drain()
            finished = await http.get(f"/v1/runs/{first.json()['run_id']}?session_id=s1")
            assert finished.json()["status"] == "completed"

    asyncio.run(go())


def test_cli_inspect_tells_a_question_from_a_refusal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import json

    path = config(tmp_path)
    main(["--config", str(path), "run", "demo", "Prépare un devis.", "--session", "s1", "--json"])
    run_id = json.loads(capsys.readouterr().out)["run_id"]
    assert main(["--config", str(path), "inspect", run_id, "--session", "s1"]) == 0
    out = capsys.readouterr().out
    assert "question à l'utilisateur — en attente" in out
    assert "question  : Pour quel client faut-il préparer le devis ?" in out
    main(["--config", str(path), "run", "demo", "Dupont", "--session", "s1"])
    capsys.readouterr()
    assert main(["--config", str(path), "inspect", run_id, "--session", "s1"]) == 0
    out = capsys.readouterr().out
    assert "question à l'utilisateur — répondue" in out
    assert "question ask — réponse rendue au modèle" in out
    assert "refusé" not in out
    assert "1 question(s) à l'utilisateur" in out
