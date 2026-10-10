# SPDX-License-Identifier: Apache-2.0
"""Rejeu : la logique retourne, le monde vient du journal (K6, #31, J6.2a, J6.2b, J6.3b).

Ce qui s'éprouve ici, à l'identique :

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

Et en variante :

- ce qui ne change rien est servi en entier ; un autre modèle part pour de
  vrai, et une requête identique reste servie, même après la divergence ;
- un outil à effets de bord n'est **jamais** réexécuté : lu au journal avec
  sa décision d'approbation, remplacé par sa doublure, ou refusé ; un outil
  sans effets de bord s'exécute ; une exécution réelle n'est jamais approuvée
  par le rejeu ;
- un sous-agent dont l'appel change est relancé ; sinon il est lu ;
- la dépense est celle des vrais appels ; une variante mal dite est refusée ;
- le rejeu dit sa divergence une fois, le moteur ne la crie pas ;
- la commande compare, et dit si la variante est allée au bout.

Et depuis un journal exporté (J6.3b) :

- chaque run fini du fichier se rejoue, hors du journal de l'instance, avec la
  config d'aujourd'hui ; un run inachevé est nommé, pas rejoué ; un fichier
  qui ne se lit pas, ou un run qu'il ne peut pas rejouer, est refusé en le
  disant ;
- ``assert_replays`` rejoue à part et lève avec chaque écart ;
- ``loom replay --journal`` rend 0, 1 ou 2.
"""

import asyncio
import json
import logging
import shutil
from collections.abc import AsyncGenerator, Sequence
from pathlib import Path
from typing import Any

import pytest
import yaml
from conftest import MODEL, PNG, ConfigFactory, demo_agent
from pydantic import JsonValue

from loom_ia.access import Loom
from loom_ia.access.cli import main
from loom_ia.adapters.artifacts import InMemoryArtifactStore
from loom_ia.adapters.stores import InMemoryEventStore
from loom_ia.config import ConfigError, load_config
from loom_ia.core.events import (
    ApprovalGranted,
    ApprovalRejected,
    Event,
    ModelResponded,
    RunStarted,
    ToolCalled,
    ToolCompleted,
)
from loom_ia.core.model import (
    Approved,
    ArtifactRefBlock,
    Attachment,
    InlineDataBlock,
    Message,
    ModelCapabilities,
    ModelChunk,
    ModelRequest,
    ModelResponse,
    ModelSpec,
    PendingApproval,
    PendingCall,
    Rejected,
    RunId,
    RunStatus,
    SessionId,
    TextBlock,
    ToolCallBlock,
    ToolDefinition,
    ToolOutput,
    Usage,
    artifact_uri,
)
from loom_ia.core.ports import AnsweringClient, ModelClient, ModelError, stopped_by_client
from loom_ia.engine import RunContext, begin_run, drive
from loom_ia.engine.hooks import PolicyFailure
from loom_ia.engine.model_call import ModelCall
from loom_ia.replay import (
    ReplayBook,
    ReplayError,
    ReplayModelClient,
    VariantModelClient,
    VariantTools,
    journal_runs,
    read_journal,
)
from loom_ia.testing import (
    RunJournal,
    ScriptedModel,
    assert_replays,
    message_to_chunks,
    tool_call_message,
)
from loom_ia.tools import tool

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


@pytest.mark.parametrize("vision", [False, True], ids=["mention", "octets"])
async def test_a_run_with_an_attachment_replays_identically(
    demo: ConfigFactory, vision: bool
) -> None:
    """Le journal porte la requête avec ses références de fichiers, pas leurs octets : le rejeu,
    à l'identique comme en variante, retrouve la réponse sous cette empreinte (modèle aveugle ou
    non : mention textuelle ou image)."""
    model = {**MODEL, "capabilities": {"vision": vision}}
    photo = Attachment(data=PNG, name="photo.png")
    async with Loom(load_config(demo(models=[model]))) as loom:
        result = await loom.run("demo", QUESTION, attachments=[photo])
        exact = await loom.replay(result.run_id)
        variant = await loom.replay(result.run_id, mode="variant")
    assert exact.identical, exact.divergence
    assert exact.model_calls[0] == exact.model_calls[1] > 0
    assert variant.identical, variant.divergence
    assert variant.comparison is not None
    assert (variant.comparison.real_models, variant.comparison.served_models) == (0, 2)


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
    assert divergence.rank == 1
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
    # Après deux appels de l'orchestrateur, servis : le rôle est le troisième.
    assert divergence.rank == 3
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
    run_id = events[0].run_id
    output, _ = book.tool_output(run_id, "c1", "calculer", {"expr": "1+2"})
    assert output.is_error
    assert book.divergence is not None and book.divergence.kind == "tool"
    assert "autres arguments" in book.divergence.detail
    # Après la première divergence, plus rien n'est servi.
    again, _ = book.tool_output(run_id, "c1", "calculer", {"expr": "1+1"})
    assert again.is_error and "arrêté" in again.as_text


async def test_an_approval_absent_from_the_journal_diverges() -> None:
    book = ReplayBook()
    decision = await book.approve("r1", PendingApproval(call_id="c9", tool_name="envoyer"))
    assert type(decision).__name__ == "Rejected"
    assert book.divergence is not None and book.divergence.kind == "approval"


async def test_a_call_approved_twice_replays_with_its_first_decision() -> None:
    """Un plantage après l'accord redemande une décision ; le rejeu ne lance l'appel qu'une fois."""
    journal_ = RunJournal()
    journal_.start(QUESTION)
    journal_.model_turn(tool_call_message(("c1", "envoyer", {})))
    drafts = [
        *journal_.take(),
        journal_.scope.draft(ApprovalGranted(call_id="c1", tool_name="envoyer", by="denis")),
        journal_.scope.draft(ApprovalRejected(call_id="c1", tool_name="envoyer", by="marie")),
    ]
    book = ReplayBook.of([d.to_event(i + 1) for i, d in enumerate(drafts)])
    decision = await book.approve(
        drafts[0].run_id, PendingApproval(call_id="c1", tool_name="envoyer")
    )
    assert decision == Approved(by="denis")
    assert book.divergence is None


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


TRUNCATED = ModelResponse(
    model_id="m",
    provider="fake",
    message=Message.assistant("Le total TTC est de 1 5"),
    usage=Usage(input_tokens=9, output_tokens=8),
    stop_reason="max_tokens",
)


class Written(AnsweringClient):
    """Le monde d'avant le contrôle de ``max_tokens`` : la réponse coupée, rendue entière."""

    provider = "fake"

    async def answer(self, request: ModelRequest) -> ModelResponse:
        return TRUNCATED

    async def stream(self, request: ModelRequest) -> AsyncGenerator[ModelChunk]:
        raise AssertionError("la réponse est connue")
        yield  # pragma: no cover

    async def aclose(self) -> None:
        pass


class Live:
    """Le même flux, lu pour de vrai."""

    provider = "fake"

    async def stream(self, request: ModelRequest) -> AsyncGenerator[ModelChunk]:
        for chunk in message_to_chunks(
            TRUNCATED.message, usage=TRUNCATED.usage, stop_reason=TRUNCATED.stop_reason
        ):
            yield chunk

    async def aclose(self) -> None:
        pass


async def test_a_truncated_answer_in_an_older_journal_still_replays_identically() -> None:
    """Le contrôle de ``max_tokens`` est celui du vrai appel, pas de la réponse d'un journal.

    Un journal écrit avant lui, où la réponse coupée a fini en run ``completed``, se
    rejoue à l'identique ; le même flux, lu pour de vrai, échoue.
    """
    model = ModelSpec(id="M", sdk="fake", model="m")
    store = InMemoryEventStore()
    old = RunContext(agent="demo", store=store, model=Written(), model_spec=model)
    original = await drive(old, (await begin_run(old, QUESTION)).run_id)
    assert original.status is RunStatus.COMPLETED
    events = await store.read(original.context.tenant_id, original.session_id)
    [recorded] = [e.payload for e in events if isinstance(e.payload, ModelResponded)]
    assert recorded.stop_reason == "max_tokens"

    book = ReplayBook.of(events)
    replay = RunContext(
        agent="demo",
        store=InMemoryEventStore(),
        model=ReplayModelClient(book, model),
        model_spec=model,
    )
    replayed = await drive(replay, (await begin_run(replay, QUESTION)).run_id)
    assert replayed.status is RunStatus.COMPLETED
    assert replayed.output is not None and original.output is not None
    assert replayed.output.text == original.output.text == "Le total TTC est de 1 5"
    assert book.divergence is None and book.served_responses == 1

    live = RunContext(agent="demo", store=InMemoryEventStore(), model=Live(), model_spec=model)
    failed = await drive(live, (await begin_run(live, QUESTION)).run_id)
    assert (failed.status, failed.error_type) == (RunStatus.FAILED, "model.truncated")


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


# --- Depuis un journal exporté (J6.3b) --------------------------------------------


def ecrit(path: Path, events: Sequence[Event]) -> Path:
    """Un journal JSONL, tel que ``loom sessions export`` l'écrit."""
    path.write_text("".join(f"{event.model_dump_json()}\n" for event in events), encoding="utf-8")
    return path


async def en_pause() -> list[Event]:
    """Un run racine commencé et jamais fini, dans sa propre session."""
    store = InMemoryEventStore()
    started = RunJournal(agent="demo", session_id=SessionId("en-pause")).start("Envoie.")
    return await store.append(started.take(), expected_seq=0)


async def exporte(path: Path, tmp_path: Path) -> tuple[Path, list[RunId]]:
    """Deux runs d'une session, exportés ; le journal de la config, effacé."""
    run_ids = await deux_runs(path)
    fichier = ecrit(tmp_path / "session.jsonl", await journal(path))
    shutil.rmtree(tmp_path / "data")
    return fichier, run_ids


async def test_an_exported_journal_replays_each_finished_run(
    atelier: ConfigFactory, tmp_path: Path
) -> None:
    path = atelier()
    fichier, run_ids = await exporte(path, tmp_path)
    async with Loom.from_config(path) as loom:
        rejoue = await loom.replay_journal(fichier)
        assert rejoue.identical and rejoue.journal == fichier and not rejoue.unfinished
        assert [r.run_id for r in rejoue.reports] == run_ids
        assert all(r.model_calls[0] == r.model_calls[1] > 0 for r in rejoue.reports)
        # Ses événements déjà lus, ou un seul run, nommé.
        lus = await loom.replay_journal(read_journal(fichier))
        assert lus.journal is None and lus.identical and len(lus.reports) == 2
        [seul] = (await loom.replay_journal(str(fichier), run_id=run_ids[1])).reports
        assert seul.run_id == run_ids[1] and seul.identical
        # En variante aussi : rien n'a changé, tout est servi, et les runs se comparent.
        variante = await loom.replay_journal(fichier, mode="variant")
        assert all(
            r.comparison is not None and r.comparison.real_models == 0 for r in variante.reports
        )
        # Le journal de l'instance n'a rien reçu : le fichier seul a servi.
        assert await loom.sessions() == []


async def test_a_journal_diverges_with_todays_config(
    atelier: ConfigFactory, tmp_path: Path
) -> None:
    path = atelier()
    fichier, _ = await exporte(path, tmp_path)
    atelier(system="Autre prompt.")
    async with Loom.from_config(path) as loom:
        rejoue = await loom.replay_journal(fichier)
    assert not rejoue.identical and len(rejoue.reports) == 2
    for report in rejoue.reports:
        divergence = report.divergence
        assert divergence is not None and divergence.parts == ("system",)
        assert "appel de modèle n°1 (main au journal)" in divergence.where


async def test_a_journal_gives_its_root_runs_only(tree: ConfigFactory, tmp_path: Path) -> None:
    """Un sous-run se rejoue avec sa racine ; un run système n'est pas un run demandé."""
    path = tree()
    async with Loom(load_config(path)) as loom:
        result = await loom.run("demo", "Combien font 2 + 2 ?")
        events = await loom.export_session(result.session_id)
    assert any(
        isinstance(e.payload, RunStarted) and e.payload.parent_run_id is not None for e in events
    )
    resume = RunJournal(agent="demo", session_id=SessionId("resume")).start("Résume.").complete()
    systeme = [
        e.model_copy(update={"payload": e.payload.model_copy(update={"kind": "compaction"})})
        if isinstance(e.payload, RunStarted)
        else e
        for e in await InMemoryEventStore().append(resume.take(), expected_seq=0)
    ]
    assert [r.run_id for r in journal_runs([*events, *systeme])] == [result.run_id]
    fichier = ecrit(tmp_path / "arbre.jsonl", events)
    async with Loom(load_config(path)) as loom:
        [report] = (await loom.replay_journal(fichier)).reports
    assert report.run_id == result.run_id and report.identical


async def test_an_unfinished_run_is_named_not_replayed(
    atelier: ConfigFactory, tmp_path: Path
) -> None:
    path = atelier()
    run_ids = await deux_runs(path)
    pause = await en_pause()
    mele = ecrit(tmp_path / "mele.jsonl", [*await journal(path), *pause])
    async with Loom.from_config(path) as loom:
        rejoue = await loom.replay_journal(mele)
        assert [r.run_id for r in rejoue.reports] == run_ids
        assert all(r.identical for r in rejoue.reports)
        # Le journal ne se rejoue pas en entier : il ne passe pas.
        assert rejoue.unfinished == (pause[0].run_id,) and not rejoue.identical
        with pytest.raises(ReplayError, match=r"aucun run fini \(1 inachevé\(s\)\)"):
            await loom.replay_journal(pause)
        with pytest.raises(ReplayError, match="inachevé"):
            await loom.replay_journal(mele, run_id=pause[0].run_id)


@pytest.mark.parametrize(
    ("contenu", "message"),
    [
        (None, "illisible"),
        ("", "aucun événement"),
        ("{pas du json\n", "ligne 1 : pas un événement"),
        ('\n{"type": "run.started"}\n', "ligne 2 : pas un événement"),
    ],
)
def test_a_journal_that_does_not_read_is_refused(
    tmp_path: Path, contenu: str | None, message: str
) -> None:
    fichier = tmp_path / "journal.jsonl"
    if contenu is not None:
        fichier.write_text(contenu, encoding="utf-8")
    with pytest.raises(ReplayError, match=message):
        read_journal(fichier)


async def test_what_a_journal_cannot_replay_is_refused(
    atelier: ConfigFactory, tmp_path: Path
) -> None:
    path = atelier()
    fichier, _ = await exporte(path, tmp_path)
    ailleurs = [
        event.model_copy(update={"tenant_id": "inconnu"}) for event in read_journal(fichier)
    ]
    async with Loom.from_config(path) as loom:
        with pytest.raises(ReplayError, match="introuvable dans le journal"):
            await loom.replay_journal(fichier, run_id=RunId("absent"))
        with pytest.raises(ReplayError, match="a 2 runs à rejouer, en nommer un"):
            await loom.replay_journal(fichier, export=tmp_path / "rejeu.jsonl")
        with pytest.raises(ReplayError, match="ne servent qu'en variante"):
            await loom.replay_journal(fichier, models={"main": "MAIN"})
        with pytest.raises(ReplayError, match="Client 'inconnu' non déclaré"):
            await loom.replay_journal(ailleurs)


def en_service(config: Path) -> None:
    """Le journal de la config dans Postgres, par un DSN que l'environnement n'a pas."""
    data = yaml.safe_load(config.read_text(encoding="utf-8"))
    data["storage"]["events"] = {"backend": "postgres", "dsn_env": "LOOM_REJEU_DSN_ABSENT"}
    data["storage"]["artifacts"] = {"backend": "memory"}
    config.write_text(yaml.safe_dump(data), encoding="utf-8")


async def test_the_kit_asserts_that_journals_replay(atelier: ConfigFactory, tmp_path: Path) -> None:
    path = atelier()
    fichier, _ = await exporte(path, tmp_path)
    en_service(path)
    with pytest.raises(ConfigError, match="Journal 'postgres'"):
        async with Loom.from_config(path):
            pass
    # Monté à part : le journal que déclare la config n'est pas ouvert — ni base,
    # ni DSN, ni même le paquet qui la sert.
    [rejoue] = await assert_replays(path, fichier)
    assert rejoue.identical and len(rejoue.reports) == 2
    mele = ecrit(tmp_path / "mele.jsonl", [*read_journal(fichier), *await en_pause()])
    with pytest.raises(AssertionError, match=r"Rejeu : 1 écart\(s\)") as reste:
        await assert_replays(path, mele)
    assert "inachevé au journal" in str(reste.value)
    atelier(system="Autre prompt.")
    with pytest.raises(AssertionError, match=r"Rejeu : 2 écart\(s\) sur 1 journal") as ecart:
        await assert_replays(load_config(path), fichier)
    assert str(ecart.value).count("le prompt système") == 2
    with pytest.raises(AssertionError, match="aucun journal"):
        await assert_replays(path)
    pause = ecrit(tmp_path / "pause.jsonl", await en_pause())
    with pytest.raises(AssertionError, match="illisible") as manque:
        await assert_replays(path, tmp_path / "absent.jsonl", pause)
    assert "aucun run fini (1 inachevé(s))" in str(manque.value)


async def test_the_kit_lends_registered_objects(atelier: ConfigFactory, tmp_path: Path) -> None:
    """Un outil enregistré en Python fait partie des requêtes : sans lui, l'agent ne monte pas."""

    def noter(texte: str) -> str:
        """Note un texte."""
        return texte

    path = atelier(extra_tools=[{"python": "outil_enregistre"}])
    async with Loom.from_config(path) as loom:
        loom.register("outil_enregistre", tool(noter))
        result = await loom.run("demo", QUESTION, session_id=SESSION)
        assert result.ok
        fichier = ecrit(tmp_path / "session.jsonl", await loom.export_session(SESSION))
    [rejoue] = await assert_replays(path, fichier, register={"outil_enregistre": tool(noter)})
    assert rejoue.identical
    with pytest.raises(ConfigError, match="outil_enregistre"):
        await assert_replays(path, fichier)


async def test_the_command_replays_a_journal_file(
    atelier: ConfigFactory, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = atelier()
    fichier, run_ids = await exporte(path, tmp_path)
    base = ["--config", str(path), "replay", "--journal", str(fichier)]
    assert await _cli(base) == 0
    sortie = capsys.readouterr().out
    assert sortie.count("Identique  :") == 2
    assert "Bilan      : 2/2 run(s) identique(s)" in sortie
    assert await _cli([*base[:3], run_ids[0], *base[3:]]) == 0
    assert capsys.readouterr().out.count("Identique  :") == 1
    mele = ecrit(tmp_path / "mele.jsonl", [*read_journal(fichier), *await en_pause()])
    assert await _cli([*base[:4], str(mele)]) == 1
    sortie = capsys.readouterr().out
    assert "Non rejoué : run" in sortie and "1 inachevé(s) non rejoué(s)" in sortie
    atelier(system="Autre prompt.")
    assert await _cli([*base, "--json"]) == 1
    rapport = json.loads(capsys.readouterr().out)
    assert rapport["identical"] is False
    assert [r["divergence"]["parts"] for r in rapport["runs"]] == [["system"], ["system"]]
    for refus, dit in (
        (["--config", str(path), "replay"], "nommer le run"),
        ([*base, "--session", "x"], "ne servent pas avec --journal"),
        ([*base[:4], str(tmp_path / "absent.jsonl")], "illisible"),
    ):
        assert await _cli(refus) == 2
        assert dit in capsys.readouterr().err


# --- En variante (J6.2b) -----------------------------------------------------------

ENVOIS = '''
import os
from pathlib import Path

from loom_ia.tools import tool


@tool
def calculer(expr: str) -> str:
    """Calcule une expression."""
    return str(eval(expr))


@tool
async def envoyer(destinataire: str) -> str:
    """Envoie la réponse : chaque envoi laisse une ligne dans envois.txt."""
    # Le module est importé une fois par process : le fichier se lit à l'appel.
    with Path(os.environ["ENVOIS_REJEU"]).open("a", encoding="utf-8") as fichier:
        fichier.write(destinataire + "\\n")
    return f"envoyé à {destinataire}"


def doublure(destinataire: str) -> str:
    """Ce que reçoit le modèle à la place d'un envoi."""
    return f"(doublure) envoi à {destinataire} simulé"
'''


def script(
    destinataire: JsonValue, *, expr: str = "12*7+3", fin: str = "Fait."
) -> list[dict[str, Any]]:
    """Calcule, fait rédiger, envoie, conclut."""
    return [
        {"tool_calls": [{"name": "calculer", "arguments": {"expr": expr}}]},
        {"tool_calls": [{"name": "rediger", "arguments": {"ton": "poli"}}]},
        {"tool_calls": [{"name": "envoyer", "arguments": {"destinataire": destinataire}}]},
        {"text": fin},
    ]


@pytest.fixture
def variante(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Outil sans effets (calculer), rôle non terminal, envoi irréversible sous approbation.

    ``MAIN`` a fait le run ; ``AUTRE`` envoie ailleurs, ``MEME`` envoie au même
    destinataire, ``AILLEURS`` calcule autre chose, ``PANNE`` ne répond pas.
    """
    (tmp_path / "agents").mkdir()
    (tmp_path / "outils_variante.py").write_text(ENVOIS, encoding="utf-8")
    monkeypatch.setenv("ENVOIS_REJEU", str(tmp_path / "envois.txt"))
    prix = {"input": 1.0, "output": 2.0}
    modeles: list[dict[str, Any]] = [
        {"id": "MAIN", "sdk": "fake", "model": "main-1", "params": {"script": script("martin")}},
        {
            "id": "AUTRE",
            "sdk": "fake",
            "model": "main-2",
            "pricing": prix,
            "params": {"script": script("dupont", fin="Fait autrement.")},
        },
        {
            "id": "MEME",
            "sdk": "fake",
            "model": "main-3",
            "params": {"script": script("martin", fin="Fait, pareil.")},
        },
        {
            "id": "AILLEURS",
            "sdk": "fake",
            "model": "main-4",
            "params": {"script": script("martin", expr="12*7+4")},
        },
        {
            "id": "PANNE",
            "sdk": "fake",
            "model": "main-5",
            "params": {"script": [{"error": "auth"}]},
        },
        {
            "id": "RENVOI",
            "sdk": "fake",
            "model": "main-6",
            "params": {"script": script({"$ref": "result:1"}, fin="Envoyé au résultat.")},
        },
        {
            "id": "ROLE",
            "sdk": "fake",
            "model": "role-1",
            "params": {"script": [{"text": "Bonjour, voici la relance."}]},
        },
    ]
    config = {
        "version": 1,
        "imports": ["outils_variante"],
        "models": modeles,
        "storage": {"events": {"backend": "jsonl", "path": "data"}},
        "telemetry": {"logging": {"level": "CRITICAL"}},
    }
    (tmp_path / "loom.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    agent = {
        "name": "demo",
        "main": {"model": "MAIN", "system": "Tu calcules, fais rédiger, envoies."},
        "tools": [
            {"python": "calculer"},
            {"python": "envoyer", "side_effects": "irreversible", "approval": "always"},
        ],
        "roles": [
            {
                "name": "rediger",
                "description": "Rédige la relance.",
                "model": "ROLE",
                "system": "Tu rédiges.",
                "input_schema": {"type": "object", "properties": {"ton": {"type": "string"}}},
                "input_template": "Ton : {{ args.ton }}",
            }
        ],
    }
    (tmp_path / "agents" / "demo.yaml").write_text(yaml.safe_dump(agent), encoding="utf-8")
    return tmp_path / "loom.yaml"


async def enregistre(path: Path) -> RunId:
    """Le run d'origine : l'envoi approuvé par denis, une ligne dans envois.txt."""
    async with Loom.from_config(path) as loom:
        run = await loom.run("demo", "Relance martin.", session_id=SESSION)
        await loom.approve(
            run.run_id,
            call_id=run.pending_approvals[0].call_id,
            by="denis",
            session_id=SESSION,
        )
        await loom.drain()
        final = await loom.state(run.run_id, session_id=SESSION)
    assert final.status == "completed"
    assert envois(path) == ["martin"]
    return run.run_id


def envois(path: Path) -> list[str]:
    fichier = path.parent / "envois.txt"
    return fichier.read_text(encoding="utf-8").splitlines() if fichier.exists() else []


async def test_a_variant_that_changes_nothing_is_served_entirely(variante: Path) -> None:
    run_id = await enregistre(variante)
    async with Loom.from_config(variante) as loom:
        report = await loom.replay(run_id, session_id=SESSION, mode="variant")
    assert report.mode == "variant" and report.identical, report.divergence
    comparison = report.comparison
    assert comparison is not None
    assert comparison.real_models == 0 and comparison.spent_usd == 0
    assert comparison.served_models == report.model_calls[0] > 0
    assert dict(comparison.tools) == {"journal": 2}
    assert comparison.same_answer
    assert envois(variante) == ["martin"]


async def test_another_model_runs_for_real_and_never_sends_again(variante: Path) -> None:
    """Le modèle change : ses appels partent ; le rôle, à demande identique, est servi ;
    le calcul se lit au journal ; l'envoi ailleurs n'est jamais exécuté."""
    run_id = await enregistre(variante)
    async with Loom.from_config(variante) as loom:
        report = await loom.replay(
            run_id, session_id=SESSION, mode="variant", models={"main": "AUTRE"}
        )
    assert not report.identical
    divergence = report.divergence
    assert divergence is not None and divergence.kind == "model"
    assert "appel de modèle n°1 (main au journal)" in divergence.where
    assert "model" in divergence.parts
    comparison = report.comparison
    assert comparison is not None and comparison.swapped == {"main": "AUTRE"}
    # Quatre appels de l'orchestrateur, partis ; celui du rôle, servi après la divergence.
    assert (comparison.real_models, comparison.served_models) == (4, 1)
    assert dict(comparison.tools) == {"journal": 1, "refused": 1}
    assert comparison.calls == (("calculer", "journal"), ("envoyer", "refused"))
    refus = [
        e.payload.output.as_text
        for e in report.events
        if isinstance(e.payload, ToolCompleted) and e.payload.output.is_error
    ]
    assert len(refus) == 1 and "jamais réexécuté" in refus[0] and "irreversible" in refus[0]
    accord = next(e.payload for e in report.events if isinstance(e.payload, ApprovalGranted))
    assert accord.by == "rejeu"
    assert envois(variante) == ["martin"]
    # La dépense est celle des vrais appels, et d'eux seuls.
    reels = [
        e.payload.cost_usd
        for e in report.events
        if isinstance(e.payload, ModelResponded) and e.payload.model_id == "main-2"
    ]
    assert len(reels) == 4 and sum(reels) > 0
    assert comparison.spent_usd == pytest.approx(sum(reels))
    assert comparison.variant.text == "Fait autrement." != comparison.original.text
    assert not comparison.same_answer


async def test_the_journal_is_asked_with_the_written_request_and_the_model_gets_the_files() -> None:
    """Une requête que le journal ne connaît pas part pour de vrai avec ses fichiers résolus,
    mais c'est la requête écrite (références) que le livre compare à ses empreintes."""
    files = InMemoryArtifactStore()
    uri = artifact_uri("default", "s1", PNG, "image/png")
    await files.put(uri, PNG)
    photo = ArtifactRefBlock(uri=uri, media_type="image/png", size=len(PNG), name="photo.png")
    request = ModelRequest(
        model_id="m",
        messages=(Message(role="user", blocks=(TextBlock(text="Regarde"), photo)),),
    )
    spec = ModelSpec(id="M", sdk="fake", model="m", capabilities=ModelCapabilities(vision=True))
    real = ScriptedModel(Message.assistant("Vu."))
    book = ReplayBook()
    call = ModelCall(VariantModelClient(book, spec, lambda: real), spec, artifacts=files)

    [response] = [item async for item in call.run(request)]

    assert isinstance(response, ModelResponse) and response.message.text == "Vu."
    [sent] = real.requests
    assert [type(b) for b in sent.messages[0].blocks] == [TextBlock, InlineDataBlock]
    assert book.divergence is not None
    assert book.divergence.actual_hash == request.request_hash() != sent.request_hash()


async def test_a_side_effect_call_found_in_the_journal_is_read_with_its_decision(
    variante: Path,
) -> None:
    run_id = await enregistre(variante)
    async with Loom.from_config(variante) as loom:
        report = await loom.replay(
            run_id, session_id=SESSION, mode="variant", models={"main": "MEME"}
        )
    comparison = report.comparison
    assert comparison is not None
    assert dict(comparison.tools) == {"journal": 2}
    accord = next(e.payload for e in report.events if isinstance(e.payload, ApprovalGranted))
    assert accord.by == "denis"
    assert envois(variante) == ["martin"]


async def test_a_double_answers_in_place_of_an_unknown_side_effect_call(variante: Path) -> None:
    run_id = await enregistre(variante)
    vus: list[str] = []

    async def doublure(destinataire: str) -> str:
        vus.append(destinataire)
        return f"(doublure) envoi à {destinataire}"

    async with Loom.from_config(variante) as loom:
        report = await loom.replay(
            run_id,
            session_id=SESSION,
            mode="variant",
            models={"main": "AUTRE"},
            doubles={"envoyer": doublure},
        )
    comparison = report.comparison
    assert comparison is not None and dict(comparison.tools) == {"journal": 1, "double": 1}
    assert vus == ["dupont"]
    sorties = [
        e.payload.output.as_text for e in report.events if isinstance(e.payload, ToolCompleted)
    ]
    assert "(doublure) envoi à dupont" in sorties
    assert envois(variante) == ["martin"]


async def test_a_double_receives_what_the_tool_would_have_received(variante: Path) -> None:
    """La doublure reçoit les arguments résolus, pas la référence que le modèle a écrite."""
    run_id = await enregistre(variante)
    vus: list[JsonValue] = []

    def doublure(destinataire: JsonValue) -> str:
        vus.append(destinataire)
        return "(doublure) envoyé"

    async with Loom.from_config(variante) as loom:
        report = await loom.replay(
            run_id,
            session_id=SESSION,
            mode="variant",
            models={"main": "RENVOI"},
            doubles={"envoyer": doublure},
        )
    comparison = report.comparison
    assert comparison is not None and comparison.tools.get("double") == 1
    assert vus == ["87"]
    assert envois(variante) == ["martin"]


async def test_a_tool_without_side_effects_runs_for_real(variante: Path) -> None:
    run_id = await enregistre(variante)
    async with Loom.from_config(variante) as loom:
        report = await loom.replay(
            run_id, session_id=SESSION, mode="variant", models={"main": "AILLEURS"}
        )
    comparison = report.comparison
    assert comparison is not None
    # Le calcul (autre expression) s'exécute ; l'envoi, identique, est lu au journal.
    assert dict(comparison.tools) == {"run": 1, "journal": 1}
    sorties = [
        e.payload.output.as_text for e in report.events if isinstance(e.payload, ToolCompleted)
    ]
    assert "88" in sorties
    assert envois(variante) == ["martin"]


async def test_a_real_execution_is_never_approved_by_the_replay() -> None:
    """Un outil sans effets de bord, absent du journal, sous approbation : personne ne dit oui."""

    @tool(approval="always")
    def lire(x: int) -> str:
        """Lit."""
        return str(x)

    tools = VariantTools(ReplayBook())
    appel = PendingCall(call_id="v1", name="lire", arguments={"x": 1})
    assert tools.serves(lire, appel, RunId("r1")) is False
    assert tools.fates == {("r1", "v1"): "run"}
    decision = await tools.approve(RunId("r1"), PendingApproval(call_id="v1", tool_name="lire"))
    assert isinstance(decision, Rejected) and "personne" in decision.reason


async def test_a_subagent_is_relaunched_when_its_call_changes(tree: ConfigFactory) -> None:
    autre = [
        {"tool_calls": [{"name": "verifier", "arguments": {"message": "Vérifie 2 + 2, vite."}}]},
        {"text": "Vérifié autrement."},
    ]
    meme = [
        {"tool_calls": [{"name": "verifier", "arguments": {"message": "Vérifie 2 + 2."}}]},
        {"text": "Vérifié, pareil."},
    ]
    base = load_config(tree())
    extra = [
        {"id": "AUTRE", "sdk": "fake", "model": "main-2", "params": {"script": autre}},
        {"id": "MEME", "sdk": "fake", "model": "main-3", "params": {"script": meme}},
    ]
    path = tree(
        models=[*(m.model_dump(mode="json", exclude_defaults=True) for m in base.models), *extra]
    )
    async with Loom(load_config(path)) as loom:
        result = await loom.run("demo", "Combien font 2 + 2 ?")
        relance = await loom.replay(result.run_id, mode="variant", models={"main": "AUTRE"})
        lu = await loom.replay(result.run_id, mode="variant", models={"main": "MEME"})
    comparison = relance.comparison
    assert comparison is not None
    assert comparison.tools.get("relaunched") == 1
    # L'enfant est relancé : deux runs ; son calcul, identique, est lu au journal.
    assert len({e.run_id for e in relance.events}) == 2
    assert comparison.tools.get("journal") == 1
    enfant = next(
        e.payload
        for e in relance.events
        if isinstance(e.payload, ToolCalled) and e.payload.tool_name == "verifier"
    )
    assert enfant.child_run_id is not None
    # Même message : l'appel est lu, l'enfant ne tourne pas, et ses appels n'ont pas à l'être.
    assert lu.comparison is not None and dict(lu.comparison.tools) == {"journal": 1}
    assert {e.run_id for e in lu.events} == {result.run_id}


async def test_a_variant_that_changes_nothing_reads_its_subagent(tree: ConfigFactory) -> None:
    """Sans changement, le sous-agent est lu, et ses appels n'ont pas à être refaits."""
    async with Loom(load_config(tree())) as loom:
        result = await loom.run("demo", "Combien font 2 + 2 ?")
        report = await loom.replay(result.run_id, mode="variant")
    assert report.identical, report.divergence
    assert {e.run_id for e in report.events} == {result.run_id}
    assert report.comparison is not None and report.comparison.real_models == 0


async def test_a_role_alone_can_change_model(variante: Path) -> None:
    """L'orchestrateur est servi jusqu'au rôle ; le rôle part, avec son autre modèle."""
    run_id = await enregistre(variante)
    async with Loom.from_config(variante) as loom:
        report = await loom.replay(
            run_id, session_id=SESSION, mode="variant", models={"rediger": "AUTRE"}
        )
    divergence = report.divergence
    assert divergence is not None and "(rediger au journal)" in divergence.where
    assert "model" in divergence.parts
    comparison = report.comparison
    assert comparison is not None and comparison.swapped == {"rediger": "AUTRE"}
    assert comparison.served_models >= 2 and comparison.real_models >= 1


def test_a_stop_by_the_client_is_seen_through_its_causes() -> None:
    arret = ModelError("invalid_request", "rejeu", by_client=True)
    panne = ModelError("invalid_request", "refusé par le fournisseur")
    try:
        raise PolicyFailure("juge : model.invalid_request") from arret
    except PolicyFailure as enrobee:
        assert stopped_by_client(enrobee)
    assert stopped_by_client(arret) and not stopped_by_client(panne)
    assert not stopped_by_client(ValueError("autre chose"))


async def test_a_variant_is_said_precisely_or_refused(variante: Path) -> None:
    run_id = await enregistre(variante)
    async with Loom.from_config(variante) as loom:
        with pytest.raises(ReplayError, match=r"étape.*inconnue.*orchestre.*main, rediger"):
            await loom.replay(
                run_id, session_id=SESSION, mode="variant", models={"orchestre": "AUTRE"}
            )
        with pytest.raises(ReplayError, match=r"non déclaré.*INCONNU"):
            await loom.replay(
                run_id, session_id=SESSION, mode="variant", models={"main": "INCONNU"}
            )
        with pytest.raises(ReplayError, match="ne servent qu'en variante"):
            await loom.replay(run_id, session_id=SESSION, models={"main": "AUTRE"})
        with pytest.raises(ReplayError, match=r"outil inconnu.*expedier"):
            await loom.replay(
                run_id, session_id=SESSION, mode="variant", doubles={"expedier": print}
            )
        with pytest.raises(ReplayError, match=r"rediger.*rôle"):
            await loom.replay(
                run_id, session_id=SESSION, mode="variant", doubles={"rediger": print}
            )


async def test_a_model_that_cannot_be_called_fails_the_variant_clearly() -> None:
    def sans_cle() -> ModelClient:
        raise ValueError("clé KEY absente")

    book = ReplayBook()
    client = VariantModelClient(book, ModelSpec(id="M", sdk="fake", model="m"), sans_cle)
    request = ModelRequest(model_id="m", messages=(Message.user("?"),))
    assert await client.answer(request) is None
    with pytest.raises(ModelError, match=r"ne peut pas être appelé.*KEY") as raised:
        _ = [chunk async for chunk in client.stream(request)]
    assert raised.value.kind == "auth"


async def test_the_replay_says_its_divergence_once_and_the_engine_does_not_cry(
    atelier: ConfigFactory, caplog: pytest.LogCaptureFixture
) -> None:
    path = atelier()
    [run_id, _] = await deux_runs(path)
    changed = atelier(rediger="Tu rédiges, en français soutenu.")
    with caplog.at_level(logging.DEBUG, logger="loom_ia"):
        async with Loom.from_config(changed) as loom:
            report = await loom.replay(run_id, session_id=SESSION)
    assert report.divergence is not None
    au_dessus = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert [r.name for r in au_dessus] == ["loom_ia.replay.runner"], [r.message for r in au_dessus]
    assert "divergence" in au_dessus[0].getMessage()
    # Le moteur a bien noté l'arrêt — en DEBUG : le rôle, puis l'orchestrateur rappelé.
    notes = [r.getMessage() for r in caplog.records if r.levelno == logging.DEBUG]
    assert any("Échec du rôle" in note for note in notes)
    assert any("Échec de l'appel au modèle" in note for note in notes)


async def test_a_variant_says_where_it_leaves_the_original_as_information(
    variante: Path, caplog: pytest.LogCaptureFixture
) -> None:
    run_id = await enregistre(variante)
    with caplog.at_level(logging.INFO, logger="loom_ia.replay"):
        async with Loom.from_config(variante) as loom:
            await loom.replay(run_id, session_id=SESSION, mode="variant", models={"main": "AUTRE"})
    [dit] = [r for r in caplog.records if r.name == "loom_ia.replay.runner"]
    assert dit.levelno == logging.INFO and "quitte le run d'origine" in dit.getMessage()


async def test_the_variant_command_compares_and_says_if_it_went_through(
    variante: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = await enregistre(variante)
    base = ["--config", str(variante), "replay", run_id, "--session", SESSION, "--mode", "variant"]
    code = await _cli(
        [*base, "--model", "main=AUTRE", "--double", "envoyer=outils_variante:doublure"]
    )
    sortie = capsys.readouterr().out
    assert code == 0, sortie
    assert "Écart      : appel de modèle n°1" in sortie
    assert "4 parti(s) pour de vrai" in sortie and "1 remplacé par sa doublure" in sortie
    assert "--- en variante\nFait autrement." in sortie
    assert await _cli([*base, "--model", "main=AUTRE", "--json"]) == 0
    rapport = json.loads(capsys.readouterr().out)
    assert rapport["mode"] == "variant" and rapport["comparison"]["tools"] == {
        "journal": 1,
        "refused": 1,
    }
    assert await _cli([*base, "--model", "main=PANNE"]) == 1
    assert "failed" in capsys.readouterr().out
    assert await _cli([*base, "--model", "main"]) == 2
    assert "ETAPE=MODELE" in capsys.readouterr().err
    assert envois(variante) == ["martin"]
