# SPDX-License-Identifier: Apache-2.0
"""Approbations (J4.3a) : pause, accord, refus, expiration, approbateur en ligne."""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

from loom_ia.access.api import Loom, UnknownApproval
from loom_ia.adapters.stores import InMemoryEventStore
from loom_ia.config import ConfigError, load_config
from loom_ia.core.events import (
    ApprovalGranted,
    Event,
    FacetValue,
    RunClaimed,
    RunTransitioned,
    ToolCalled,
    ToolCompleted,
)
from loom_ia.core.model import (
    ApprovalDecision,
    Approved,
    PendingApproval,
    Rejected,
    RunStatus,
    SessionId,
    ToolCallBlock,
)
from loom_ia.runtime import build_agent

type ConfigFactory = Callable[..., Path]

SESSION = SessionId("atelier")
DEMANDE = "Relance la cliente."

OUTILS = '''
from loom_ia.tools import tool


@tool
async def chercher(numero: str) -> str:
    """Cherche un devis."""
    return f"devis {numero} : en attente"


@tool
async def envoyer_email(destinataire: str) -> str:
    """Envoie un e-mail."""
    return f"envoyé à {destinataire}"
'''

POLITIQUES = '''
from loom_ia.core.model import BeforeTool, CONTINUE, Decision, Pause, Replace
from loom_ia.policies import policy


@policy(points=["before_tool"], decisions=["pause"])
def gros_montant(subject: BeforeTool) -> Decision:
    """Fait valider un envoi au-delà d'un seuil."""
    if subject.spec.name == "envoyer_email":
        return Pause("montant au-delà du seuil")
    return CONTINUE


@policy(points=["before_tool"], decisions=["replace"])
def corrige_destinataire(subject: BeforeTool) -> Decision:
    """Redirige les envois vers la comptabilité."""
    if subject.spec.name == "envoyer_email":
        return Replace({"destinataire": "compta@example.com"}, reason="envois redirigés")
    return CONTINUE
'''

# Le modèle appelle les deux outils dans le même lot : l'un passe, l'autre attend.
SCRIPT: list[dict[str, Any]] = [
    {
        "text": "Je vérifie puis j'envoie.",
        "tool_calls": [
            {"name": "chercher", "arguments": {"numero": "D-1"}},
            {"name": "envoyer_email", "arguments": {"destinataire": "mme.martin@example.com"}},
        ],
    },
    {"text": "C'est fait."},
]


@pytest.fixture
def atelier(tmp_path: Path) -> ConfigFactory:
    """Config JSONL — une approbation exige un journal durable (#28)."""

    def build(
        *,
        approval: str | None = "always",
        settings: dict[str, Any] | None = None,
        policies: list[dict[str, Any]] | None = None,
        backend: str = "jsonl",
        **root: Any,
    ) -> Path:
        (tmp_path / "agents").mkdir(exist_ok=True)
        (tmp_path / "outils_appro.py").write_text(OUTILS, encoding="utf-8")
        (tmp_path / "politiques_appro.py").write_text(POLITIQUES, encoding="utf-8")
        events: dict[str, Any] = {"backend": backend}
        if backend != "memory":
            events["path"] = "data"
        config: dict[str, Any] = {
            "version": 1,
            "imports": ["outils_appro", "politiques_appro"],
            "models": [
                {"id": "FAKE", "sdk": "fake", "model": "fake-1", "params": {"script": SCRIPT}}
            ],
            "storage": {"events": events},
            "telemetry": {"logging": {"level": "CRITICAL"}},
            **root,
        }
        (tmp_path / "loom.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
        envoi: dict[str, Any] = {"python": "envoyer_email", "side_effects": "irreversible"}
        if approval is not None:
            envoi["approval"] = approval
        agent: dict[str, Any] = {
            "name": "demo",
            "description": "Relance.",
            "main": {"model": "FAKE", "system": "Tu relances."},
            "tools": [{"python": "chercher"}, envoi],
            **({"approval": settings} if settings else {}),
            **({"policies": policies} if policies else {}),
        }
        (tmp_path / "agents" / "demo.yaml").write_text(yaml.safe_dump(agent), encoding="utf-8")
        return tmp_path / "loom.yaml"

    return build


# Le patron délègue tout au secrétaire, qui seul porte l'outil sensible.
PATRON: list[dict[str, Any]] = [
    {
        "text": "Je confie ça au secrétaire.",
        "tool_calls": [{"name": "secretaire", "arguments": {"message": "Relance la cliente."}}],
    },
    {"text": "C'est fait."},
]
SECRETAIRE: list[dict[str, Any]] = [
    {
        "text": "J'envoie.",
        "tool_calls": [
            {"name": "envoyer_email", "arguments": {"destinataire": "mme.martin@example.com"}}
        ],
    },
    {"text": "Relance envoyée."},
]


@pytest.fixture
def delegue(atelier: ConfigFactory, tmp_path: Path) -> ConfigFactory:
    """Un agent qui délègue à un sous-agent, lequel porte l'outil sensible."""

    def build(**kwargs: Any) -> Path:
        path = atelier(**kwargs)
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
        config["models"] = [
            {"id": "FAKE", "sdk": "fake", "model": "f1", "params": {"script": PATRON}},
            {"id": "FAKE_ENFANT", "sdk": "fake", "model": "f2", "params": {"script": SECRETAIRE}},
        ]
        path.write_text(yaml.safe_dump(config), encoding="utf-8")
        demo = yaml.safe_load((tmp_path / "agents" / "demo.yaml").read_text(encoding="utf-8"))
        secretaire = {
            **demo,
            "name": "secretaire",
            "description": "Écrit et envoie les relances.",
            "main": {"model": "FAKE_ENFANT", "system": "Tu envoies."},
            "expose": {"rest": False, "mcp": False},
        }
        (tmp_path / "agents" / "secretaire.yaml").write_text(
            yaml.safe_dump(secretaire), encoding="utf-8"
        )
        patron = {
            "name": "demo",
            "description": "Délègue.",
            "main": {"model": "FAKE", "system": "Tu délègues."},
            "subagents": [{"agent": "secretaire", "description": "Écrit et envoie les relances."}],
        }
        (tmp_path / "agents" / "demo.yaml").write_text(yaml.safe_dump(patron), encoding="utf-8")
        return path

    return build


# --- Pause --------------------------------------------------------------------


def test_the_rest_of_the_batch_runs_before_the_run_pauses(atelier: ConfigFactory) -> None:
    """Ce qui ne demande rien passe ; le run ne s'arrête que pour le reste (#17)."""
    types, result = asyncio.run(_requested(atelier()))
    assert result.status is RunStatus.PAUSED
    assert [a.tool_name for a in result.pending_approvals] == ["envoyer_email"]
    # `chercher` est allé au bout, `envoyer_email` n'a jamais été appelé.
    assert types.index("tool.completed") < types.index("approval.requested")
    assert types.count("tool.called") == 1
    assert types[-1] == "run.claimed"


def test_a_paused_run_hands_its_lease_back(atelier: ConfigFactory) -> None:
    """Sans quoi la reprise se ferait refuser pendant tout ce qui reste du bail (#27)."""
    events, _ = asyncio.run(_events(atelier()))
    claims = [e for e in events if e.type == "run.claimed"]
    assert len(claims) == 2
    rendue = claims[-1].payload
    assert isinstance(rendue, RunClaimed)
    assert rendue.lease_until <= claims[-1].ts


def test_a_policy_can_require_an_approval_on_any_tool(atelier: ConfigFactory) -> None:
    """Une politique `before_tool` qui rend Pause suffit, sans déclaration (#17)."""
    path = atelier(approval=None, policies=[{"hook": "gros_montant"}])
    _, result = asyncio.run(_requested(path))
    assert [a.tool_name for a in result.pending_approvals] == ["envoyer_email"]
    demande = result.pending_approvals[0]
    assert (demande.policy, demande.reason) == ("gros_montant", "montant au-delà du seuil")


# --- Décisions ----------------------------------------------------------------


def test_an_approved_call_is_replayed_and_the_run_finishes(atelier: ConfigFactory) -> None:
    async def go() -> tuple[list[str], str]:
        async with Loom.from_config(atelier()) as loom:
            run = await loom.run("demo", DEMANDE, session_id=SESSION)
            assert await loom.approve(run.run_id, by="denis", session_id=SESSION)
            await loom.drain()
            fin = await loom.result(run.run_id, session_id=SESSION)
            events = await loom.export_session(SESSION)
        return [e.type for e in events], str(fin.status)

    types, status = asyncio.run(go())
    assert status == RunStatus.COMPLETED
    assert types.count("tool.called") == 2
    assert types[-1] == "run.completed"


def test_an_approver_can_correct_the_arguments(atelier: ConfigFactory) -> None:
    """Les arguments corrigés sont ceux qui partent, et le journal dit les deux.

    ``tool.called`` garde ce que le modèle a demandé — c'est la convention de
    cet événement — et ``approval.granted`` ce que l'approbateur a corrigé.
    Entre les deux, on sait qui a voulu quoi.
    """

    async def go() -> tuple[dict[str, Any], dict[str, Any], str]:
        async with Loom.from_config(atelier()) as loom:
            run = await loom.run("demo", DEMANDE, session_id=SESSION)
            await loom.approve(
                run.run_id,
                call_id=run.pending_approvals[0].call_id,
                by="denis",
                arguments={"destinataire": "compta@example.com"},
                session_id=SESSION,
            )
            await loom.drain()
            events = await loom.export_session(SESSION)
        envoi = [e for e in events if e.facets.get("tool_name") == "envoyer_email"]
        accord = next(e.payload for e in events if e.type == "approval.granted")
        appel = next(e.payload for e in envoi if e.type == "tool.called")
        fini = next(e for e in envoi if e.type == "tool.completed")
        assert isinstance(accord, ApprovalGranted)
        assert isinstance(appel, ToolCalled)
        return dict(accord.arguments or {}), dict(appel.arguments), _text(fini)

    corrige, demande, resultat = asyncio.run(go())
    assert corrige == {"destinataire": "compta@example.com"}
    assert demande == {"destinataire": "mme.martin@example.com"}
    assert "compta@example.com" in resultat


def test_the_model_reads_the_call_that_actually_went_out(atelier: ConfigFactory) -> None:
    """Sinon le résultat de l'outil contredit son propre appel.

    Constaté au run réel du 21/09 : l'approbateur avait corrigé le
    destinataire, MiniMax a vu partir une adresse qu'il n'avait pas écrite,
    a conclu à une panne et a proposé de **renvoyer** un e-mail déjà parti.
    """

    async def go() -> list[dict[str, Any]]:
        async with Loom.from_config(atelier()) as loom:
            run = await loom.run("demo", DEMANDE, session_id=SESSION)
            await loom.approve(
                run.run_id,
                call_id=run.pending_approvals[0].call_id,
                by="denis",
                arguments={"destinataire": "compta@example.com"},
                session_id=SESSION,
            )
            await loom.drain()
            state = await loom.state(run.run_id, session_id=SESSION)
        return [
            dict(block.arguments)
            for message in state.messages
            for block in message.blocks
            if isinstance(block, ToolCallBlock) and block.name == "envoyer_email"
        ]

    assert asyncio.run(go()) == [{"destinataire": "compta@example.com"}]


def test_a_policy_replacement_is_visible_to_the_model_too(atelier: ConfigFactory) -> None:
    """Le même défaut existait depuis 3.1 pour un Replace de politique."""

    async def go() -> list[dict[str, Any]]:
        path = atelier(approval=None, policies=[{"hook": "corrige_destinataire"}])
        async with Loom.from_config(path) as loom:
            run = await loom.run("demo", DEMANDE, session_id=SESSION)
            state = await loom.state(run.run_id, session_id=SESSION)
        return [
            dict(block.arguments)
            for message in state.messages
            for block in message.blocks
            if isinstance(block, ToolCallBlock) and block.name == "envoyer_email"
        ]

    assert asyncio.run(go()) == [{"destinataire": "compta@example.com"}]


def test_a_rejected_call_never_runs_and_its_reason_goes_to_the_model(
    atelier: ConfigFactory,
) -> None:
    async def go() -> tuple[list[str], str]:
        async with Loom.from_config(atelier()) as loom:
            run = await loom.run("demo", DEMANDE, session_id=SESSION)
            await loom.reject(
                run.run_id, by="denis", reason="destinataire non vérifié", session_id=SESSION
            )
            await loom.drain()
            events = await loom.export_session(SESSION)
        refus = next(
            e
            for e in events
            if e.type == "tool.completed" and e.facets["tool_name"] == "envoyer_email"
        )
        return [e.type for e in events], _text(refus)

    types, texte = asyncio.run(go())
    # Un seul `tool.called` : celui de `chercher`. L'outil approuvé n'a pas tourné.
    assert types.count("tool.called") == 1
    assert "destinataire non vérifié" in texte
    assert types[-1] == "run.completed"


def test_deciding_an_unknown_call_is_refused(atelier: ConfigFactory) -> None:
    async def go() -> None:
        async with Loom.from_config(atelier()) as loom:
            run = await loom.run("demo", DEMANDE, session_id=SESSION)
            await loom.approve(run.run_id, call_id="inconnu", session_id=SESSION)

    with pytest.raises(UnknownApproval, match="inconnu"):
        asyncio.run(go())


def test_deciding_a_finished_run_changes_nothing(atelier: ConfigFactory) -> None:
    async def go() -> tuple[tuple[str, ...], int]:
        async with Loom.from_config(atelier()) as loom:
            run = await loom.run("demo", DEMANDE, session_id=SESSION)
            await loom.approve(run.run_id, session_id=SESSION)
            await loom.drain()
            encore = await loom.approve(run.run_id, by="denis", session_id=SESSION)
            events = await loom.export_session(SESSION)
        return encore, len([e for e in events if e.type == "approval.granted"])

    encore, accords = asyncio.run(go())
    assert encore == ()
    assert accords == 1


# --- Expiration ---------------------------------------------------------------


def test_a_request_is_bounded_by_default(atelier: ConfigFactory) -> None:
    """Sans délai, une attente ne finirait jamais : rien d'autre ne la borne.

    Ni le délai de l'agent, qui ne compte que le temps de pilotage (A6), ni la
    reprise, qui ne fait que reconstater l'attente.
    """

    async def go() -> tuple[datetime | None, datetime | None]:
        async with Loom.from_config(atelier()) as loom:
            run = await loom.run("demo", DEMANDE, session_id=SESSION)
            defaut = run.pending_approvals[0].expire_at
        async with Loom.from_config(atelier(settings={"expires_in": None})) as loom:
            run = await loom.run("demo", DEMANDE, session_id=SessionId("sans-delai"))
            retire = run.pending_approvals[0].expire_at
        return defaut, retire

    defaut, retire = asyncio.run(go())
    assert defaut is not None
    assert timedelta(hours=23) < defaut - datetime.now(UTC) <= timedelta(hours=24)
    # ``null`` le retire explicitement : c'est un choix, pas un oubli.
    assert retire is None


def test_an_unanswered_request_expires_from_the_journal(atelier: ConfigFactory) -> None:
    """Sans personne pour répondre, `expire_at` suffit : le travail ne décide rien."""

    async def go() -> tuple[list[str], str]:
        path = atelier(settings={"expires_in": 0.3})
        async with Loom.from_config(path) as loom:
            run = await loom.run("demo", DEMANDE, session_id=SESSION)
            assert run.status is RunStatus.PAUSED
            await asyncio.sleep(0.6)
            # Personne n'a appelé `approve` : c'est le réveil différé qui reprend.
            await loom.drain()
            fin = await loom.result(run.run_id, session_id=SESSION)
            events = await loom.export_session(SESSION)
        return [e.type for e in events], str(fin.status)

    types, status = asyncio.run(go())
    assert "approval.expired" in types
    assert status == RunStatus.COMPLETED


def test_an_expiry_can_fail_the_run(atelier: ConfigFactory) -> None:
    async def go() -> tuple[str, str | None]:
        path = atelier(settings={"expires_in": 0.3, "on_expiry": "fail"})
        async with Loom.from_config(path) as loom:
            run = await loom.run("demo", DEMANDE, session_id=SESSION)
            await asyncio.sleep(0.6)
            await loom.drain()
            fin = await loom.result(run.run_id, session_id=SESSION)
        return str(fin.status), fin.error_type

    status, error_type = asyncio.run(go())
    assert (status, error_type) == (RunStatus.FAILED, "approval.expired")


def test_a_decision_still_wins_over_a_deadline_that_has_not_passed(
    atelier: ConfigFactory,
) -> None:
    """Le délai n'a pas d'effet tant qu'il n'est pas passé."""

    async def go() -> list[str]:
        path = atelier(settings={"expires_in": 30})
        async with Loom.from_config(path) as loom:
            run = await loom.run("demo", DEMANDE, session_id=SESSION)
            await loom.approve(run.run_id, by="denis", session_id=SESSION)
            await loom.drain()
            events = await loom.export_session(SESSION)
        return [e.type for e in events]

    types = asyncio.run(go())
    assert "approval.granted" in types
    assert "approval.expired" not in types


# --- Approbateur en ligne -----------------------------------------------------


def test_an_inline_approver_decides_without_pausing(atelier: ConfigFactory) -> None:
    """Le run ne passe jamais en PAUSED, mais l'audit est écrit quand même (#28)."""
    vues: list[PendingApproval] = []

    async def approbateur(demande: PendingApproval) -> ApprovalDecision:
        vues.append(demande)
        return Approved(by="denis", reason="cliente connue")

    async def go() -> tuple[list[str], int, str]:
        async with Loom.from_config(atelier()) as loom:
            run = await loom.run("demo", DEMANDE, session_id=SESSION, approver=approbateur)
            events = await loom.export_session(SESSION)
        return [e.type for e in events], len(_paused(events)), str(run.status)

    types, pauses, status = asyncio.run(go())
    assert status == RunStatus.COMPLETED
    assert pauses == 0
    assert [d.tool_name for d in vues] == ["envoyer_email"]
    assert types.index("approval.granted") < types.index("tool.called")


def test_an_inline_approver_can_refuse(atelier: ConfigFactory) -> None:
    async def approbateur(demande: PendingApproval) -> ApprovalDecision:
        return Rejected(by="denis", reason="pas ce destinataire")

    async def go() -> tuple[list[str], str]:
        async with Loom.from_config(atelier()) as loom:
            run = await loom.run("demo", DEMANDE, session_id=SESSION, approver=approbateur)
            events = await loom.export_session(SESSION)
        return [e.type for e in events], str(run.status)

    types, status = asyncio.run(go())
    assert status == RunStatus.COMPLETED
    assert "approval.rejected" in types
    assert types.count("tool.called") == 1


def test_an_inline_approver_records_who_decided(atelier: ConfigFactory) -> None:
    """L'audit de #17 tient dans ce champ, et nulle part ailleurs."""

    async def approbateur(demande: PendingApproval) -> ApprovalDecision:
        return Approved(by="denis")

    async def go() -> list[FacetValue]:
        async with Loom.from_config(atelier()) as loom:
            await loom.run("demo", DEMANDE, session_id=SESSION, approver=approbateur)
            events = await loom.export_session(SESSION)
        return [e.facets.get("by") for e in events if e.type == "approval.granted"]

    assert asyncio.run(go()) == ["denis"]


# --- Sous-agent en pause (4.3b) -----------------------------------------------


def test_a_paused_child_puts_its_parent_on_hold(delegue: ConfigFactory) -> None:
    """L'appel délégant reste en suspens : pas de résultat, donc pas de conclusion."""

    async def go() -> tuple[list[str], Any]:
        async with Loom.from_config(delegue()) as loom:
            result = await loom.run("demo", DEMANDE, session_id=SESSION)
            events = await loom.export_session(SESSION)
        parent = [e for e in events if e.run_id == result.run_id]
        return [e.type for e in parent], result

    types, result = asyncio.run(go())
    assert result.status is RunStatus.WAITING_CHILD
    # L'appel au sous-agent est parti, mais rien ne l'a conclu.
    assert types.count("tool.called") == 1
    assert "tool.completed" not in types
    assert types[-1] == "run.claimed"


def test_the_waiting_transition_says_what_caused_it(delegue: ConfigFactory) -> None:
    """Toute transition se relie à l'événement qui l'a provoquée, celle-ci comprise."""

    async def go() -> tuple[str | None, str | None]:
        async with Loom.from_config(delegue()) as loom:
            result = await loom.run("demo", DEMANDE, session_id=SESSION)
            events = await loom.export_session(SESSION)
        transition = next(
            e.payload
            for e in events
            if e.run_id == result.run_id and e.facets.get("to_state") == "waiting_child"
        )
        assert isinstance(transition, RunTransitioned)
        return transition.cause_type, transition.cause_event_id

    cause_type, cause_id = asyncio.run(go())
    assert cause_type == "model.responded"
    assert cause_id is not None


def test_the_root_shows_what_its_child_awaits(delegue: ConfigFactory) -> None:
    """L'appelant n'a pas à savoir qu'un sous-agent existe pour savoir quoi trancher."""

    async def go() -> tuple[list[str], int]:
        async with Loom.from_config(delegue()) as loom:
            result = await loom.run("demo", DEMANDE, session_id=SESSION)
            racine = await loom.state(result.run_id, session_id=SESSION)
        return [a.tool_name for a in result.pending_approvals], len(racine.awaiting)

    rendues, propres = asyncio.run(go())
    assert rendues == ["envoyer_email"]
    # La racine, elle, n'attend rien elle-même : c'est son enfant qui attend.
    assert propres == 0


def test_approving_on_the_root_resumes_the_whole_tree(delegue: ConfigFactory) -> None:
    """On tranche là où on a lu, et c'est la racine qui repart."""

    async def go() -> tuple[str, list[tuple[str, bool]], int]:
        async with Loom.from_config(delegue()) as loom:
            result = await loom.run("demo", DEMANDE, session_id=SESSION)
            assert await loom.approve(result.run_id, by="denis", session_id=SESSION)
            await loom.drain()
            fin = await loom.result(result.run_id, session_id=SESSION)
            events = await loom.export_session(SESSION)
        appels = [
            (str(e.facets["tool_name"]), bool(getattr(e.payload, "resumed", False)))
            for e in events
            if e.type == "tool.called"
        ]
        enfants = len([e for e in events if e.type == "run.started"])
        return str(fin.status), appels, enfants

    status, appels, runs = asyncio.run(go())
    assert status == RunStatus.COMPLETED
    # L'enfant est repris, pas relancé : deux runs en tout, et l'appel délégant rejoué.
    assert runs == 2
    assert appels == [("secretaire", False), ("secretaire", True), ("envoyer_email", False)]


def test_a_refused_child_call_lets_the_tree_finish(delegue: ConfigFactory) -> None:
    """Un refus dans un sous-agent n'est pas une panne, pour lui ni pour son parent."""

    async def go() -> tuple[str, int]:
        async with Loom.from_config(delegue()) as loom:
            result = await loom.run("demo", DEMANDE, session_id=SESSION)
            await loom.reject(
                result.run_id, by="denis", reason="pas ce destinataire", session_id=SESSION
            )
            await loom.drain()
            fin = await loom.result(result.run_id, session_id=SESSION)
            events = await loom.export_session(SESSION)
        envois = [
            e
            for e in events
            if e.type == "tool.called" and e.facets["tool_name"] == "envoyer_email"
        ]
        return str(fin.status), len(envois)

    status, envois = asyncio.run(go())
    assert status == RunStatus.COMPLETED
    assert envois == 0


def test_a_waiting_parent_hands_its_lease_back(delegue: ConfigFactory) -> None:
    """Comme une pause : sans quoi la reprise se ferait refuser (#27)."""

    async def go() -> list[tuple[str, bool]]:
        async with Loom.from_config(delegue()) as loom:
            result = await loom.run("demo", DEMANDE, session_id=SESSION)
            events = await loom.export_session(SESSION)
        return [
            (str(e.facets["worker_id"]), _handed(e))
            for e in events
            if e.type == "run.claimed" and e.run_id == result.run_id
        ]

    claims = asyncio.run(go())
    assert [rendue for _, rendue in claims] == [False, True]


def test_a_childs_deadline_wakes_the_whole_tree(delegue: ConfigFactory) -> None:
    """La racine n'a pas de demande à elle : personne ne serait venu la réveiller."""

    async def go() -> tuple[str, int]:
        path = delegue(settings={"expires_in": 0.3})
        async with Loom.from_config(path) as loom:
            result = await loom.run("demo", DEMANDE, session_id=SESSION)
            assert result.status is RunStatus.WAITING_CHILD
            # Personne n'appelle rien : c'est le réveil différé qui reprend.
            await asyncio.sleep(0.8)
            await loom.drain()
            fin = await loom.result(result.run_id, session_id=SESSION)
            events = await loom.export_session(SESSION)
        return str(fin.status), len([e for e in events if e.type == "approval.expired"])

    status, expirees = asyncio.run(go())
    assert status == RunStatus.COMPLETED
    assert expirees == 1


# --- Config -------------------------------------------------------------------


def test_an_agent_that_can_pause_needs_a_durable_journal(atelier: ConfigFactory) -> None:
    """En pause, le run n'existe plus que dans le journal (#28)."""
    path = atelier(backend="memory")
    config = load_config(path)
    with pytest.raises(ConfigError, match="journal durable"):
        build_agent(config, "demo", InMemoryEventStore())


def test_a_pausing_policy_also_needs_a_durable_journal(atelier: ConfigFactory) -> None:
    path = atelier(approval=None, policies=[{"hook": "gros_montant"}], backend="memory")
    config = load_config(path)
    with pytest.raises(ConfigError, match="gros_montant"):
        build_agent(config, "demo", InMemoryEventStore())


def test_an_agent_that_cannot_pause_runs_on_any_journal(atelier: ConfigFactory) -> None:
    path = atelier(approval=None, backend="memory")
    config = load_config(path)
    assert build_agent(config, "demo", InMemoryEventStore()).spec.name == "demo"


# --- Utilitaires --------------------------------------------------------------


async def _events(path: Path) -> tuple[list[Event], Any]:
    async with Loom.from_config(path) as loom:
        result = await loom.run("demo", DEMANDE, session_id=SESSION)
        return await loom.export_session(SESSION), result


async def _requested(path: Path) -> tuple[list[str], Any]:
    events, result = await _events(path)
    return [e.type for e in events], result


def _text(event: Event) -> str:
    """Texte d'un résultat d'outil."""
    payload = event.payload
    assert isinstance(payload, ToolCompleted)
    return "".join(getattr(block, "text", "") for block in payload.output.blocks)


def _handed(event: Event) -> bool:
    """Concession rendue : son bail était déjà passé à l'écriture."""
    payload = event.payload
    assert isinstance(payload, RunClaimed)
    return payload.lease_until <= event.ts


def _paused(events: list[Event]) -> list[Event]:
    """Transitions vers la pause : aucune, avec un approbateur en ligne."""
    return [e for e in events if e.facets.get("to_state") == "paused"]
