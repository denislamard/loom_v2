# SPDX-License-Identifier: Apache-2.0
"""Rejeu identique : la logique retourne, le monde vient du journal (K6, #31, J6.2a).

Ce qui s'éprouve ici :

- un run fini se rejoue **appel par appel**, sans appeler personne — outils,
  rôle, juge, sous-agent, approbation, et un second run de session dont
  l'historique doit se reconstruire ;
- une config qui a changé fait **diverger** le rejeu au premier appel touché,
  et le rapport dit quelle partie de la requête a changé ;
- un journal d'avant les empreintes par partie se rejoue, avec un diagnostic
  moins fin, qu'il dit ;
- rien n'est écrit dans le vrai journal, et le journal du rejeu s'exporte ;
- ce qui ne se rejoue pas est refusé en le disant ;
- la commande rend 0, 1 ou 2.
"""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from conftest import ConfigFactory, demo_agent

from loom_ia.access import Loom
from loom_ia.access.cli import main
from loom_ia.config import load_config
from loom_ia.core.events import Event, ModelResponded, RunStarted, ToolCalled
from loom_ia.core.model import (
    Message,
    ModelRequest,
    ModelResponse,
    ModelSpec,
    PendingApproval,
    RunId,
    SessionId,
    TextBlock,
    ToolCallBlock,
    ToolDefinition,
    ToolOutput,
)
from loom_ia.core.ports import ModelError
from loom_ia.engine.model_call import ModelCall
from loom_ia.replay import ReplayBook, ReplayError, ReplayModelClient
from loom_ia.testing import RunJournal, tool_call_message

SESSION = SessionId("atelier")
QUESTION = "Combien font 12 fois 7, plus 3 ?"

OUTILS = '''
from loom_ia.tools import tool


@tool
def calculer(expr: str) -> str:
    """Calcule une expression."""
    return str(eval(expr))


@tool
async def envoyer(destinataire: str) -> str:
    """Envoie la réponse."""
    return f"envoyé à {destinataire}"
'''

MAIN_SCRIPT: list[dict[str, Any]] = [
    {"tool_calls": [{"name": "calculer", "arguments": {"expr": "12*7+3"}}]},
    {
        "tool_calls": [
            {"name": "rediger", "arguments": {"ton": "poli", "calcul": {"$ref": "result:1"}}}
        ]
    },
]
VERDICT: dict[str, Any] = {
    "tool_calls": [
        {
            "name": "verdict",
            "arguments": {"criteria": [{"name": "exact", "score": 1.0, "reason": "juste"}]},
        }
    ]
}


@pytest.fixture
def atelier(tmp_path: Path) -> ConfigFactory:
    """Un agent à outil, rôle terminal jugé (tiré au sort à moitié), journal JSONL."""

    def build(
        *,
        system: str = "Tu orchestres.",
        rediger: str = "Tu rédiges.",
        template: str = "Ton : {{ args.ton }}\nCalcul : {{ context.tool_results.calculer }}",
        extra_tools: list[dict[str, Any]] | None = None,
        sample: float = 0.5,
    ) -> Path:
        (tmp_path / "agents").mkdir(exist_ok=True)
        (tmp_path / "prompts").mkdir(exist_ok=True)
        (tmp_path / "prompts" / "rediger.md").write_text(rediger, encoding="utf-8")
        (tmp_path / "outils_rejeu.py").write_text(OUTILS, encoding="utf-8")
        config: dict[str, Any] = {
            "version": 1,
            "imports": ["outils_rejeu"],
            "models": [
                {"id": "MAIN", "sdk": "fake", "model": "main-1", "params": {"script": MAIN_SCRIPT}},
                {
                    "id": "ROLE",
                    "sdk": "fake",
                    "model": "role-1",
                    "params": {"script": [{"text": "Cela fait 87."}]},
                },
                {"id": "JUDGE", "sdk": "fake", "model": "judge-1", "params": {"script": [VERDICT]}},
            ],
            "storage": {"events": {"backend": "jsonl", "path": "data"}},
            "telemetry": {"logging": {"level": "CRITICAL"}},
        }
        (tmp_path / "loom.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
        role = {
            "name": "rediger",
            "description": "Rédige la réponse finale.",
            "model": "ROLE",
            "system_file": "rediger.md",
            "input_schema": {
                "type": "object",
                "properties": {"ton": {"type": "string"}, "calcul": {}},
                "required": ["ton"],
            },
            "context": [{"tool_results": ["calculer"]}],
            "input_template": template,
            "terminal": True,
            "judge": {
                "model": "JUDGE",
                "criteria": [{"name": "exact", "rule": "Le calcul est juste.", "blocking": False}],
                "when": {"sample": sample},
            },
        }
        agent = {
            "name": "demo",
            "description": "Calcule et rédige.",
            "main": {"model": "MAIN", "system": system},
            "tools": [{"python": "calculer"}, *(extra_tools or [])],
            "roles": [role],
        }
        (tmp_path / "agents" / "demo.yaml").write_text(yaml.safe_dump(agent), encoding="utf-8")
        return tmp_path / "loom.yaml"

    return build


async def deux_runs(path: Path) -> list[RunId]:
    """Deux runs dans la même session : le second a un historique à reconstruire."""
    async with Loom.from_config(path) as loom:
        first = await loom.run("demo", QUESTION, session_id=SESSION)
        second = await loom.run("demo", "Et 12 fois 8 ?", session_id=SESSION)
    assert first.ok and second.ok
    return [first.run_id, second.run_id]


async def journal(path: Path) -> list[Event]:
    async with Loom.from_config(path) as loom:
        return await loom.export_session(SESSION)


# --- À l'identique ----------------------------------------------------------------


async def test_a_finished_run_replays_call_by_call(atelier: ConfigFactory) -> None:
    path = atelier()
    run_ids = await deux_runs(path)
    async with Loom.from_config(path) as loom:
        for run_id in run_ids:
            report = await loom.replay(run_id, session_id=SESSION)
            assert report.identical, report.divergence
            assert report.divergence is None
            assert (report.original_end, report.replay_end) == ("completed", "completed")
            # Chaque appel a été refait, aucun n'a été servi deux fois.
            assert report.model_calls[0] == report.model_calls[1] > 0
            assert report.tool_calls[0] == report.tool_calls[1] > 0


async def test_the_replayed_run_keeps_its_identity_and_its_judges_draw(
    atelier: ConfigFactory,
) -> None:
    """Le tirage d'un juge se fait sur le ``run_id`` : rejoué sous le même, il retombe pareil."""
    path = atelier(sample=0.5)
    run_ids = await deux_runs(path)
    original = await journal(path)
    async with Loom.from_config(path) as loom:
        for run_id in run_ids:
            report = await loom.replay(run_id, session_id=SESSION)
            assert report.identical
            juges = [e for e in report.events if e.type == "judge.evaluated"]
            avant = [e for e in original if e.run_id == run_id and e.type == "judge.evaluated"]
            assert len(juges) == len(avant)
            assert {e.run_id for e in report.events} == {run_id}


async def test_nothing_is_written_to_the_journal(atelier: ConfigFactory, tmp_path: Path) -> None:
    path = atelier()
    [run_id, _] = await deux_runs(path)
    avant = await journal(path)
    sortie = tmp_path / "rejeu.jsonl"
    async with Loom.from_config(path) as loom:
        report = await loom.replay(run_id, session_id=SESSION, export=sortie)
    assert len(await journal(path)) == len(avant)
    lignes = sortie.read_text(encoding="utf-8").splitlines()
    assert len(lignes) == len(report.events) > 0
    assert json.loads(lignes[0])["type"] == "run.started"


async def test_an_approval_is_decided_as_it_was(tmp_path: Path) -> None:
    """La décision vient du journal, en ligne : le rejeu ne passe pas par la pause.

    Le parcours se compare sans les pauses — elles tiennent à la façon de
    piloter, pas à ce que le run a fait.
    """
    (tmp_path / "agents").mkdir()
    (tmp_path / "outils_rejeu.py").write_text(OUTILS, encoding="utf-8")
    script = [
        {"tool_calls": [{"name": "envoyer", "arguments": {"destinataire": "mme.martin"}}]},
        {"text": "C'est envoyé."},
    ]
    config = {
        "version": 1,
        "imports": ["outils_rejeu"],
        "models": [{"id": "F", "sdk": "fake", "model": "f", "params": {"script": script}}],
        "storage": {"events": {"backend": "jsonl", "path": "data"}},
        "telemetry": {"logging": {"level": "CRITICAL"}},
    }
    (tmp_path / "loom.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    agent = {
        "name": "demo",
        "main": {"model": "F", "system": "Tu envoies."},
        "tools": [
            {"python": "envoyer", "side_effects": "irreversible", "approval": "always"},
        ],
    }
    (tmp_path / "agents" / "demo.yaml").write_text(yaml.safe_dump(agent), encoding="utf-8")
    path = tmp_path / "loom.yaml"
    async with Loom.from_config(path) as loom:
        run = await loom.run("demo", "Envoie.", session_id=SESSION)
        await loom.approve(
            run.run_id,
            call_id=run.pending_approvals[0].call_id,
            by="denis",
            arguments={"destinataire": "compta"},
            session_id=SESSION,
        )
        await loom.drain()
        report = await loom.replay(run.run_id, session_id=SESSION)
    assert report.identical, report.divergence
    accord = next(e for e in report.events if e.type == "approval.granted")
    assert accord.payload.by == "denis"  # type: ignore[union-attr]
    assert "paused" not in [
        e.payload.to_state  # type: ignore[union-attr]
        for e in report.events
        if e.type == "run.transitioned"
    ]


async def test_a_subagent_is_read_not_relaunched(tree: ConfigFactory) -> None:
    async with Loom(load_config(tree())) as loom:
        result = await loom.run("demo", "Combien font 2 + 2 ?")
        report = await loom.replay(result.run_id)
    assert report.identical, report.divergence
    # Un seul run rejoué : l'enfant n'a pas été relancé, son résultat a été lu.
    assert {e.run_id for e in report.events} == {result.run_id}
    [appel] = [e.payload for e in report.events if isinstance(e.payload, ToolCalled)]
    assert appel.tool_name == "verifier" and appel.child_run_id is None


# --- Les divergences ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("change", "part"),
    [
        ({"system": "Tu orchestres, brièvement."}, "system"),
        ({"extra_tools": [{"python": "envoyer"}]}, "tools"),
    ],
    ids=["prompt", "outils"],
)
async def test_a_changed_config_diverges_at_the_first_call_and_names_the_part(
    atelier: ConfigFactory, change: dict[str, Any], part: str
) -> None:
    path = atelier()
    [run_id, _] = await deux_runs(path)
    changed = atelier(**change)
    async with Loom.from_config(changed) as loom:
        report = await loom.replay(run_id, session_id=SESSION)
    assert not report.identical
    divergence = report.divergence
    assert divergence is not None and divergence.kind == "model"
    assert divergence.parts == (part,)
    assert "appel de modèle n°1 (main au journal)" in divergence.where
    assert divergence.expected_hash != divergence.actual_hash
    # Arrêt à la première : rien n'a été servi après.
    assert report.model_calls[1] == 0


async def test_a_changed_role_template_diverges_at_the_role(atelier: ConfigFactory) -> None:
    path = atelier()
    [run_id, _] = await deux_runs(path)
    changed = atelier(
        template="Ton : {{ args.ton }}\nRésultat : {{ context.tool_results.calculer }}"
    )
    async with Loom.from_config(changed) as loom:
        report = await loom.replay(run_id, session_id=SESSION)
    divergence = report.divergence
    assert divergence is not None and divergence.kind == "model"
    assert "(rediger au journal)" in divergence.where
    assert divergence.parts == ("messages",)
    assert "1 message(s) au journal, 1 maintenant" in divergence.detail


async def test_an_older_journal_replays_with_a_coarser_diagnosis(
    atelier: ConfigFactory,
) -> None:
    """Sans ``request_parts`` (journal d'avant 6.2a), la divergence se dit sans détail."""
    path = atelier()
    [run_id, _] = await deux_runs(path)
    events = await journal(path)
    anciens: list[Event] = []
    for event in events:
        if isinstance(event.payload, ModelResponded):
            payload = event.payload.model_copy(update={"request_parts": {}})
            event = event.model_copy(update={"payload": payload})
        anciens.append(event)
    book = ReplayBook.of([e for e in anciens if e.run_id == run_id])
    client = ReplayModelClient(book, load_config(path).model_spec("MAIN"))
    with pytest.raises(Exception, match="la requête a changé"):
        await client.answer(ModelRequest(model_id="main-1", messages=(Message.user("?"),)))
    assert book.divergence is not None
    assert "antérieur aux empreintes par partie" in book.divergence.detail


REFUS = '''
from loom_ia.core.model import Decision, Fail, OnOutput
from loom_ia.policies import policy


@policy(points=["on_output"], decisions=["fail"])
def refuse(subject: OnOutput) -> Decision:
    """Refuse toute réponse finale."""
    return Fail("réponse refusée")
'''


async def test_same_calls_but_another_ending_diverge(demo: ConfigFactory, tmp_path: Path) -> None:
    """Tous les appels se rejouent, mais une politique ajoutée refuse la réponse finale."""
    jsonl = {"events": {"backend": "jsonl", "path": "data"}}
    path = demo(storage=jsonl)
    async with Loom.from_config(path) as loom:
        result = await loom.run("demo", QUESTION)
    (tmp_path / "politiques_rejeu.py").write_text(REFUS, encoding="utf-8")
    refusant = demo(
        storage=jsonl,
        imports=["outils_acces", "politiques_rejeu"],
        agents=[demo_agent(policies=[{"hook": "refuse"}])],
    )
    async with Loom.from_config(refusant) as loom:
        report = await loom.replay(result.run_id)
    assert report.model_calls[0] == report.model_calls[1]
    divergence = report.divergence
    assert divergence is not None and divergence.kind == "end"
    assert "ne finit pas de la même façon" in divergence.where
    assert "completed au journal, failed au rejeu" in divergence.detail


def test_nothing_is_served_after_the_first_divergence() -> None:
    """Une requête qui retrouve son empreinte n'est plus servie après une divergence."""
    connue = ModelRequest(model_id="m", messages=(Message.user("connue"),))
    journal_ = RunJournal()
    journal_.start("?")
    events = [d.to_event(i + 1) for i, d in enumerate(journal_.take())]
    responded = ModelResponded(
        model_id="m",
        provider="fake",
        message=Message.assistant("ok"),
        request_hash=connue.request_hash(),
    )
    events.append(journal_.scope.draft(responded).to_event(len(events) + 1))
    book = ReplayBook.of(events)
    with pytest.raises(ModelError):
        book.answer(ModelRequest(model_id="m", messages=(Message.user("autre"),)))
    with pytest.raises(ModelError, match="arrêté"):
        book.answer(connue)
    assert book.served_responses == 0


def test_a_tool_call_must_match_its_journal_entry() -> None:
    journal_ = RunJournal()
    journal_.start(QUESTION)
    journal_.model_turn(tool_call_message(("c1", "calculer", {"expr": "1+1"})))
    journal_.tool_results({"c1": ToolOutput.text("2")})
    drafts = journal_.take()
    events = [d.to_event(i + 1) for i, d in enumerate(drafts)]
    book = ReplayBook.of(events)
    output, _ = book.tool_output("c1", "calculer", {"expr": "1+2"})
    assert output.is_error
    assert book.divergence is not None and book.divergence.kind == "tool"
    assert "autres arguments" in book.divergence.detail
    # Après la première divergence, plus rien n'est servi.
    again, _ = book.tool_output("c1", "calculer", {"expr": "1+1"})
    assert again.is_error and "arrêté" in again.as_text


async def test_an_approval_absent_from_the_journal_diverges() -> None:
    book = ReplayBook()
    decision = await book.approve(PendingApproval(call_id="c9", tool_name="envoyer"))
    assert type(decision).__name__ == "Rejected"
    assert book.divergence is not None and book.divergence.kind == "approval"


async def test_the_replayed_answer_keeps_what_chunks_would_lose() -> None:
    """La réponse est rendue entière : des métadonnées qu'aucun morceau ne porte restent."""
    meta = {"openai": {"item_id": "msg_1"}}
    message = Message(
        role="assistant",
        blocks=(
            TextBlock.model_validate({"text": "Oui.", "provider_meta": meta}),
            ToolCallBlock.model_validate(
                {"call_id": "c1", "name": "t", "arguments": {}, "provider_meta": meta}
            ),
        ),
    )
    request = ModelRequest(model_id="m", messages=(Message.user("?"),))
    journal_ = RunJournal()
    journal_.start("?")
    drafts = journal_.take()
    responded = ModelResponded(
        model_id="m", provider="fake", message=message, request_hash=request.request_hash()
    )
    events = [d.to_event(i + 1) for i, d in enumerate(drafts)]
    events.append(journal_.scope.draft(responded).to_event(len(events) + 1))
    book = ReplayBook.of(events)
    spec = ModelSpec(id="M", sdk="fake", model="m")
    # Par le chemin du moteur : ``ModelCall`` reconnaît le client et prend sa
    # réponse entière ; par le flux, les métadonnées seraient perdues.
    call = ModelCall(ReplayModelClient(book, spec), spec)
    [response] = [item async for item in call.run(request)]
    assert isinstance(response, ModelResponse)
    assert response.message == message


def test_request_parts_change_one_by_one() -> None:
    base = ModelRequest(model_id="m", system="s", messages=(Message.user("a"),))
    parts = base.request_parts()
    assert set(parts) == {"model", "system", "tools", "messages", "settings", "messages_count"}
    variantes = {
        "model": base.model_copy(update={"model_id": "n"}),
        "system": base.model_copy(update={"system": "t"}),
        "tools": base.model_copy(update={"tools": (ToolDefinition(name="x", description="x"),)}),
        "messages": base.model_copy(update={"messages": (Message.user("b"),)}),
        "settings": base.model_copy(update={"max_tokens": 10}),
    }
    for name, changed in variantes.items():
        now = changed.request_parts()
        assert [p for p in parts if parts[p] != now[p]] == [name], name
    # L'empreinte d'ensemble ne bouge pas : les journaux d'avant gardent la leur.
    assert (
        base.request_hash()
        == ModelRequest(model_id="m", system="s", messages=(Message.user("a"),)).request_hash()
    )


# --- Ce qui ne se rejoue pas --------------------------------------------------------


async def test_what_cannot_be_replayed_is_refused(
    atelier: ConfigFactory, tree: ConfigFactory
) -> None:
    path = atelier()
    async with Loom.from_config(path) as loom:
        with pytest.raises(ReplayError, match="introuvable"):
            await loom.replay(RunId("absent"), session_id=SESSION)
    async with Loom(load_config(tree())) as loom:
        result = await loom.run("demo", "Combien font 2 + 2 ?")
        events = await loom.events(result.run_id)
        enfant = next(
            e.run_id
            for e in events
            if isinstance(e.payload, RunStarted) and e.payload.parent_run_id is not None
        )
        with pytest.raises(ReplayError, match="sous-run"):
            await loom.replay(enfant, session_id=result.session_id)


async def test_an_unfinished_run_is_refused(tmp_path: Path) -> None:
    (tmp_path / "agents").mkdir()
    (tmp_path / "outils_rejeu.py").write_text(OUTILS, encoding="utf-8")
    script = [{"tool_calls": [{"name": "envoyer", "arguments": {"destinataire": "x"}}]}]
    config = {
        "version": 1,
        "imports": ["outils_rejeu"],
        "models": [{"id": "F", "sdk": "fake", "model": "f", "params": {"script": script}}],
        "storage": {"events": {"backend": "jsonl", "path": "data"}},
        "telemetry": {"logging": {"level": "CRITICAL"}},
    }
    (tmp_path / "loom.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    agent = {
        "name": "demo",
        "main": {"model": "F", "system": "Tu envoies."},
        "tools": [{"python": "envoyer", "approval": "always"}],
    }
    (tmp_path / "agents" / "demo.yaml").write_text(yaml.safe_dump(agent), encoding="utf-8")
    async with Loom.from_config(tmp_path / "loom.yaml") as loom:
        run = await loom.run("demo", "Envoie.", session_id=SESSION)
        with pytest.raises(ReplayError, match="inachevé"):
            await loom.replay(run.run_id, session_id=SESSION)


# --- La commande -------------------------------------------------------------------


async def test_the_command_says_identical_divergent_or_impossible(
    atelier: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    path = atelier()
    [run_id, _] = await deux_runs(path)
    base = ["--config", str(path), "replay", run_id, "--session", SESSION]
    assert await _cli(base) == 0
    assert "Identique" in capsys.readouterr().out
    atelier(system="Autre prompt.")
    assert await _cli([*base, "--json"]) == 1
    sortie = json.loads(capsys.readouterr().out)
    assert sortie["identical"] is False and sortie["divergence"]["parts"] == ["system"]
    assert await _cli(["--config", str(path), "replay", "absent", "--session", SESSION]) == 2
    assert "Rejeu impossible" in capsys.readouterr().err


async def _cli(argv: list[str]) -> int:
    """La commande lance sa propre boucle : on la fait tourner hors de celle de l'essai."""
    return await asyncio.to_thread(main, argv)
