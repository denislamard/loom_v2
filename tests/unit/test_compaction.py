# SPDX-License-Identifier: Apache-2.0
"""Compaction d'une session (J4.1b) : segment, résumé, fidélité, filet de sécurité."""

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from loom_ia.access.api import Loom
from loom_ia.adapters.queue import AsyncioTaskQueue
from loom_ia.adapters.stores import InMemoryEventStore, JsonlEventStore
from loom_ia.config import ConfigError, load_config
from loom_ia.config.compaction import COMPACTION_AGENT
from loom_ia.core.events import (
    Event,
    ModelResponded,
    SessionCompacted,
    SessionTrimmed,
    ToolCompleted,
)
from loom_ia.core.model import (
    DEFAULT_TENANT,
    Message,
    SessionId,
    ToolOutput,
    ToolResultBlock,
)
from loom_ia.core.ports import Job
from loom_ia.core.projections import SUMMARY_MARKER, history
from loom_ia.engine import rendered
from loom_ia.guards.fidelity import markers, missing
from loom_ia.sessions import cut, oversized, run_starts, summarised
from loom_ia.testing import RunJournal, tool_call_message

SESSION = SessionId("atelier")
DEVIS = "Relance le devis D-2026-042 de 1 840,00 € pour Mme Martin."
REPONSE = "Relance envoyée pour le devis D-2026-042 (1 840 €), Mme Martin."
# Résumés simulés : le premier oublie le montant, le second le garde.
OUBLI = "L'artisan a demandé une relance pour le devis D-2026-042 de Mme Martin."
FIDELE = "L'artisan a demandé une relance du devis D-2026-042 (1 840 €) pour Mme Martin."

type ConfigFactory = Callable[..., Path]


@pytest.fixture
def conversation(tmp_path: Path) -> ConfigFactory:
    """Config d'un agent bavard, avec un modèle de résumé scripté."""

    def build(
        *,
        summaries: list[dict[str, Any]] | None = None,
        compaction: dict[str, Any] | None = None,
        **root: Any,
    ) -> Path:
        (tmp_path / "agents").mkdir(exist_ok=True)
        config: dict[str, Any] = {
            "version": 1,
            "models": [
                {
                    "id": "FAKE",
                    "sdk": "fake",
                    "model": "fake-1",
                    "params": {"script": [{"text": REPONSE}]},
                },
                {
                    "id": "RESUME",
                    "sdk": "fake",
                    "model": "fake-resume",
                    "params": {"script": summaries or [{"text": FIDELE}]},
                },
            ],
            "storage": {"events": {"backend": "jsonl", "path": "data"}},
            "telemetry": {"logging": {"level": "CRITICAL"}},
            "sessions": {
                "snapshot_every": 1,
                "compaction": {"model": "RESUME", **(compaction or {})},
            },
            **root,
        }
        (tmp_path / "loom.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
        agent = {
            "name": "demo",
            "description": "Relance des devis.",
            "main": {"model": "FAKE", "system": "Tu relances."},
        }
        (tmp_path / "agents" / "demo.yaml").write_text(yaml.safe_dump(agent), encoding="utf-8")
        return tmp_path / "loom.yaml"

    return build


def talked(session: SessionId, question: str, answer: str) -> RunJournal:
    journal = RunJournal(session_id=session)
    journal.start(question)
    journal.model_turn(tool_call_message(("c1", "chercher", {"numero": "D-2026-042"})))
    journal.tool_results({"c1": ToolOutput.text("1 840,00 €")})
    journal.model_turn(Message.assistant(answer))
    journal.complete()
    return journal


async def filled(store: InMemoryEventStore, turns: int) -> list[Event]:
    for number in range(turns):
        journal = talked(SESSION, f"{DEVIS} ({number})", REPONSE)
        last = await store.last_seq(DEFAULT_TENANT, SESSION)
        await store.append(journal.take(), expected_seq=last)
    return await store.read(DEFAULT_TENANT, SESSION)


# --- Repères et fidélité ------------------------------------------------------


def test_markers_keep_what_matters() -> None:
    found = markers("Devis D-2026-042, 1 840,00 € TTC, envoyé le 2026-09-02, 200 L, 3 phrases")

    assert "D-2026-042" in found
    # Séparateur de milliers et décimales : le montant se compare à « 1 840 €ct».
    assert "1840" in found
    assert "200" in found
    # Trop court pour dire quoi que ce soit : le 09 et le 02 d'une date, un compte.
    assert "3" not in found
    assert "09" not in found


def test_a_reworded_amount_still_matches() -> None:
    assert missing("Montant : 1 840,00 €", "Le devis porte sur 1 840 €.") == []


def test_a_lost_marker_is_named() -> None:
    absent = missing("Devis D-2026-042 de 1 840 €", "Une relance a été demandée.")

    assert absent == ["D-2026-042", "2026", "042", "1840"]


def test_an_email_must_survive() -> None:
    assert missing("Écrire à a.martin@exemple.fr", "Écrire à la cliente.") == [
        "a.martin@exemple.fr"
    ]


# --- Segment et coupe ---------------------------------------------------------


async def test_the_cut_falls_on_a_run_boundary() -> None:
    store = InMemoryEventStore()
    events = await filled(store, 3)
    starts = run_starts(events)

    assert len(starts) == 3
    # Deux derniers tours gardés : la coupe précède le début de l'avant-dernier.
    assert cut(events, keep_last=2) == starts[-2] - 1
    assert cut(events, keep_last=0) == events[-1].seq
    # Moins de tours que demandé : rien à résumer.
    assert cut(events, keep_last=5) == 0


async def test_nothing_is_summarised_twice() -> None:
    store = InMemoryEventStore()
    events = await filled(store, 2)

    assert summarised(events) == 0
    assert oversized(events, 10)
    assert not oversized(events, 10_000)


def test_the_segment_is_rendered_for_the_model() -> None:
    text = rendered(
        [
            Message.user(DEVIS),
            tool_call_message(("c1", "chercher", {"numero": "D-2026-042"})),
            Message(
                role="tool",
                blocks=(ToolResultBlock(call_id="c1", output=ToolOutput.text("1 840,00 €")),),
            ),
            Message.assistant(REPONSE),
        ]
    )

    assert text.startswith(f"Utilisateur : {DEVIS}")
    assert "Agent appelle chercher" in text
    assert "Résultat : 1 840,00 €" in text
    assert text.endswith(f"Agent : {REPONSE}")


# --- Historique repris du résumé ----------------------------------------------


async def test_history_restarts_from_the_summary() -> None:
    store = InMemoryEventStore()
    events = await filled(store, 2)
    scope = talked(SESSION, "x", "y").scope
    payload = SessionCompacted(up_to_seq=events[-1].seq, summary=FIDELE, kept=0)

    await store.append([scope.draft(payload)], expected_seq=events[-1].seq)
    messages = history(await store.read(DEFAULT_TENANT, SESSION))

    assert len(messages) == 1
    assert messages[0].role == "user"
    assert messages[0].text.startswith(SUMMARY_MARKER)
    assert FIDELE in messages[0].text


async def test_a_trim_drops_what_it_covers() -> None:
    store = InMemoryEventStore()
    events = await filled(store, 2)
    scope = talked(SESSION, "x", "y").scope
    middle = run_starts(events)[-1] - 1

    await store.append(
        [scope.draft(SessionTrimmed(up_to_seq=middle, dropped=3))], expected_seq=events[-1].seq
    )
    messages = history(await store.read(DEFAULT_TENANT, SESSION))

    # Seul le dernier tour reste, sans rien à la place de l'autre.
    assert [m.role for m in messages] == ["user", "assistant", "tool", "assistant"]


# --- File de tâches -----------------------------------------------------------


async def test_the_queue_runs_deduplicates_and_drains() -> None:
    seen: list[str] = []

    async def handler(job: Job) -> None:
        await asyncio.sleep(0)
        seen.append(job.session_id)

    queue = AsyncioTaskQueue({"compaction": handler})
    job = Job(kind="compaction", tenant_id=DEFAULT_TENANT, session_id=SESSION)

    first = await queue.submit(job, key="k")
    second = await queue.submit(job, key="k")
    assert first == second

    await queue.drain()
    assert seen == [SESSION]
    assert await queue.state(first) == "done"
    assert await queue.state("inconnu") == "unknown"
    # La clé est libérée : un second travail repart.
    assert await queue.submit(job, key="k") != first
    await queue.aclose()


async def test_an_unknown_job_kind_fails_without_breaking_the_queue() -> None:
    queue = AsyncioTaskQueue({})
    job_id = await queue.submit(
        Job(kind="compaction", tenant_id=DEFAULT_TENANT, session_id=SESSION)
    )

    await queue.drain()

    assert await queue.state(job_id) == "failed"
    await queue.aclose()


async def test_a_failing_job_is_logged_not_raised() -> None:
    async def boom(job: Job) -> None:
        raise RuntimeError("cassé")

    queue = AsyncioTaskQueue({"compaction": boom})
    job_id = await queue.submit(
        Job(kind="compaction", tenant_id=DEFAULT_TENANT, session_id=SESSION)
    )

    await queue.drain()

    assert await queue.state(job_id) == "failed"
    await queue.aclose()


# --- De bout en bout ----------------------------------------------------------


def test_compacting_replaces_the_old_turns(conversation: ConfigFactory) -> None:
    path = conversation(compaction={"over_tokens": 50, "keep_last": 1})

    async def go() -> tuple[list[Event], list[Message]]:
        async with Loom.from_config(path) as loom:
            for _ in range(2):
                await loom.run("demo", DEVIS, session_id=SESSION)
            await loom.drain()
            events = await loom.export_session(SESSION)
            return events, history(events)

    events, messages = asyncio.run(go())
    compacted = [e for e in events if e.type == "session.compacted"]
    assert len(compacted) == 1
    payload = compacted[0].payload
    assert isinstance(payload, SessionCompacted)
    assert payload.summary == FIDELE
    assert payload.kept == 1
    assert payload.tokens_after < payload.tokens_before
    assert payload.fidelity == "ok"
    # Le résumé ouvre l'historique, le dernier tour suit intact.
    assert messages[0].text.startswith(SUMMARY_MARKER)
    assert FIDELE in messages[0].text
    assert messages[-1].text == REPONSE


def test_the_compaction_run_stays_out_of_the_history(conversation: ConfigFactory) -> None:
    path = conversation(compaction={"over_tokens": 50, "keep_last": 1})

    async def go() -> tuple[list[Event], list[Message]]:
        async with Loom.from_config(path) as loom:
            for _ in range(2):
                await loom.run("demo", DEVIS, session_id=SESSION)
            await loom.drain()
            events = await loom.export_session(SESSION)
            return events, history(events)

    events, messages = asyncio.run(go())
    system = [e for e in events if e.agent == COMPACTION_AGENT]

    assert system, "le run de compaction doit être dans le journal de la session"
    assert all(FIDELE not in m.text for m in messages[1:])
    # Son résumé n'apparaît qu'une fois, en tête, porté par le marqueur.
    assert sum(1 for m in messages if SUMMARY_MARKER in m.text) == 1


def test_a_refreshed_snapshot_follows_the_summary(conversation: ConfigFactory) -> None:
    path = conversation(compaction={"over_tokens": 50, "keep_last": 1})

    async def go() -> list[Event]:
        async with Loom.from_config(path) as loom:
            for _ in range(2):
                await loom.run("demo", DEVIS, session_id=SESSION)
            await loom.drain()
            return await loom.export_session(SESSION)

    events = asyncio.run(go())
    markers_seq = [(e.seq, e.type) for e in events if e.category == "session"]
    compacted = next(seq for seq, kind in markers_seq if kind == "session.compacted")
    after = [seq for seq, kind in markers_seq if kind == "session.snapshot" and seq > compacted]

    assert after, "un snapshot doit suivre le marqueur de compaction"


def test_compacting_on_demand_ignores_the_threshold(conversation: ConfigFactory) -> None:
    path = conversation(compaction={"over_tokens": 100_000, "keep_last": 1})

    async def go() -> tuple[SessionCompacted | None, list[Event]]:
        async with Loom.from_config(path) as loom:
            for _ in range(2):
                await loom.run("demo", DEVIS, session_id=SESSION)
            written = await loom.compact(SESSION)
            return written, await loom.export_session(SESSION)

    written, events = asyncio.run(go())

    assert written is not None
    assert [e.type for e in events if e.type == "session.compacted"] == ["session.compacted"]


def test_a_summary_that_loses_a_marker_is_repaired(conversation: ConfigFactory) -> None:
    path = conversation(
        summaries=[{"text": OUBLI}, {"text": FIDELE}],
        compaction={"over_tokens": 50, "keep_last": 1},
    )

    async def go() -> list[Event]:
        async with Loom.from_config(path) as loom:
            for _ in range(2):
                await loom.run("demo", DEVIS, session_id=SESSION)
            await loom.drain()
            return await loom.export_session(SESSION)

    events = asyncio.run(go())
    checks = [
        e for e in events if e.type == "guard.checked" and e.facets.get("guard") == "fidelity"
    ]
    compacted = next(e.payload for e in events if e.type == "session.compacted")

    assert [c.facets.get("outcome") for c in checks] == ["failed", "passed"]
    assert isinstance(compacted, SessionCompacted)
    assert compacted.summary == FIDELE
    assert compacted.fidelity == "ok"


def test_a_summary_that_keeps_losing_it_is_kept_with_a_warning(
    conversation: ConfigFactory,
) -> None:
    path = conversation(
        summaries=[{"text": OUBLI}, {"text": OUBLI}],
        compaction={"over_tokens": 50, "keep_last": 1},
    )

    async def go() -> list[Event]:
        async with Loom.from_config(path) as loom:
            for _ in range(2):
                await loom.run("demo", DEVIS, session_id=SESSION)
            await loom.drain()
            return await loom.export_session(SESSION)

    events = asyncio.run(go())
    compacted = next(e for e in events if e.type == "session.compacted")
    payload = compacted.payload

    assert isinstance(payload, SessionCompacted)
    assert payload.fidelity == "warning"
    assert compacted.status == "warning"
    assert payload.summary == OUBLI


def test_fidelity_can_be_turned_off(conversation: ConfigFactory) -> None:
    path = conversation(
        summaries=[{"text": OUBLI}],
        compaction={"over_tokens": 50, "keep_last": 1, "fidelity_check": False},
    )

    async def go() -> list[Event]:
        async with Loom.from_config(path) as loom:
            for _ in range(2):
                await loom.run("demo", DEVIS, session_id=SESSION)
            await loom.drain()
            return await loom.export_session(SESSION)

    events = asyncio.run(go())
    payload = next(e.payload for e in events if e.type == "session.compacted")

    assert isinstance(payload, SessionCompacted)
    assert payload.fidelity == "skipped"
    assert not [e for e in events if e.facets.get("guard") == "fidelity"]


async def written(path: Path, turns: int) -> None:
    """Journal d'une session déjà longue, écrit sans passer par l'instance."""
    store = JsonlEventStore(path / "data")
    for number in range(turns):
        journal = talked(SESSION, f"{DEVIS} ({number})", REPONSE)
        last = await store.last_seq(DEFAULT_TENANT, SESSION)
        await store.append(journal.take(), expected_seq=last)
    await store.aclose()


def test_ensure_fits_compacts_before_the_run(conversation: ConfigFactory, tmp_path: Path) -> None:
    path = conversation(compaction={"over_tokens": 40, "hard_tokens": 60, "keep_last": 1})

    async def go() -> list[Event]:
        # Session déjà trop longue quand l'instance démarre : aucune compaction
        # n'a pu être mise en file, c'est le filet qui agit.
        await written(tmp_path, 2)
        async with Loom.from_config(path) as loom:
            await loom.run("demo", DEVIS, session_id=SESSION)
            return await loom.export_session(SESSION)

    events = asyncio.run(go())
    starts = [e.seq for e in events if e.type == "run.started" and e.facets.get("kind") == "normal"]
    compacted = [e.seq for e in events if e.type == "session.compacted"]

    assert compacted, "le filet doit avoir compacté"
    assert compacted[0] < starts[-1], "la compaction précède le run qu'elle protège"


def test_a_session_that_cannot_be_summarised_is_trimmed(
    conversation: ConfigFactory, tmp_path: Path
) -> None:
    path = conversation(
        summaries=[{"error": "invalid_request"}],
        compaction={"over_tokens": 40, "hard_tokens": 60, "keep_last": 1},
    )

    async def go() -> list[Event]:
        await written(tmp_path, 2)
        async with Loom.from_config(path) as loom:
            await loom.run("demo", DEVIS, session_id=SESSION)
            return await loom.export_session(SESSION)

    events = asyncio.run(go())
    trimmed = [e for e in events if e.type == "session.trimmed"]

    assert len(trimmed) == 1
    payload = trimmed[0].payload
    assert isinstance(payload, SessionTrimmed)
    assert payload.dropped > 0
    assert trimmed[0].status == "warning"


# --- Configuration ------------------------------------------------------------


def test_the_internal_agent_is_mounted(conversation: ConfigFactory) -> None:
    path = conversation()

    async def go() -> tuple[tuple[str, ...], tuple[str, ...]]:
        async with Loom.from_config(path) as loom:
            return loom.names, tuple(spec.name for spec in loom.exposed("rest"))

    names, exposed = asyncio.run(go())

    assert COMPACTION_AGENT in names
    assert COMPACTION_AGENT not in exposed


def test_without_compaction_nothing_is_mounted(demo: Any) -> None:
    async def go() -> tuple[str, ...]:
        async with Loom.from_config(demo()) as loom:
            return loom.names

    assert COMPACTION_AGENT not in asyncio.run(go())


def test_the_reserved_name_is_refused(conversation: ConfigFactory, tmp_path: Path) -> None:
    path = conversation()
    agent = {"name": COMPACTION_AGENT, "main": {"model": "FAKE", "system": "x"}}
    (tmp_path / "agents" / "interne.yaml").write_text(yaml.safe_dump(agent), encoding="utf-8")

    with pytest.raises(ConfigError, match="réservé"):
        load_config(path)


def test_the_hard_threshold_must_exceed_the_soft_one(conversation: ConfigFactory) -> None:
    path = conversation(compaction={"over_tokens": 100, "hard_tokens": 50})

    with pytest.raises(ConfigError, match="hard_tokens"):
        load_config(path)


def test_the_summary_run_only_sees_its_segment(conversation: ConfigFactory) -> None:
    """Le run de compaction n'a pas l'historique de session (run réel du 21/09).

    Il vit dans le journal de sa session, donc ``drive`` lui servait la
    conversation entière en plus de son segment : coût doublé, et un résumé qui
    parlait de tours situés au-delà de sa propre coupe.
    """
    gros = "Z" * 4_000
    path = conversation(compaction={"over_tokens": 50, "keep_last": 1})

    async def go() -> list[Event]:
        async with Loom.from_config(path) as loom:
            for _ in range(2):
                await loom.run("demo", DEVIS, session_id=SESSION)
            # Le dernier tour est gardé par `keep_last` : il ne doit pas partir.
            await loom.run("demo", gros, session_id=SESSION)
            await loom.drain()
            return await loom.export_session(SESSION)

    events = asyncio.run(go())
    appels = [
        e.payload for e in events if e.type == "model.responded" and e.agent == COMPACTION_AGENT
    ]

    assert appels
    # Le modèle simulé compte ses tokens d'entrée sur la requête entière : le
    # seul tour gardé pèse à lui seul 1 000 tokens, ils ne sont pas là.
    entrees = [a.usage.input_tokens for a in appels if isinstance(a, ModelResponded)]
    assert len(entrees) == len(appels)
    assert max(entrees) < 1_000


def test_an_unknown_compaction_model_is_refused(conversation: ConfigFactory) -> None:
    path = conversation(compaction={"model": "ABSENT"})

    with pytest.raises(ConfigError, match="ABSENT"):
        load_config(path)


# --- Contextes de session pour un rôle ----------------------------------------

CHERCHE = "Trouve le devis D-2026-042."
REDIGE = "Rédige la relance."
OUTILS = '''
from loom_ia.tools import tool


@tool
def chercher(numero: str) -> dict[str, object]:
    """Renvoie un devis par son numéro."""
    return {"numero": numero, "montant_ttc": 1840.0, "client": "Mme Martin"}
'''
MAIN_SCRIPT: list[dict[str, Any]] = [
    {
        "with_text": "Trouve",
        "tool_calls": [{"name": "chercher", "arguments": {"numero": "D-2026-042"}}],
    },
    {"with_text": "Trouve", "text": "Devis D-2026-042 trouvé (1 840 €)."},
    {"with_text": "Rédige", "tool_calls": [{"name": "rediger", "arguments": {"ton": "bref"}}]},
]


@pytest.fixture
def withrole(tmp_path: Path) -> Callable[..., Path]:
    """Agent à un outil et un rôle terminal, dont le contexte déclaré varie.

    Le modèle du rôle n'a pas de script : il renvoie en écho ce qu'il reçoit,
    ce qui permet de lire exactement le contexte qu'on lui a donné.
    """

    def build(*, context: list[Any], compaction: dict[str, Any] | None = None) -> Path:
        (tmp_path / "agents").mkdir(exist_ok=True)
        (tmp_path / "outils_role.py").write_text(OUTILS, encoding="utf-8")
        sessions: dict[str, Any] = {"snapshot_every": 1}
        if compaction is not None:
            sessions["compaction"] = {"model": "RESUME", **compaction}
        config: dict[str, Any] = {
            "version": 1,
            "imports": ["outils_role"],
            "models": [
                {"id": "FAKE", "sdk": "fake", "model": "f1", "params": {"script": MAIN_SCRIPT}},
                {"id": "ROLE", "sdk": "fake", "model": "f2"},
                {
                    "id": "RESUME",
                    "sdk": "fake",
                    "model": "f3",
                    "params": {"script": [{"text": FIDELE}]},
                },
            ],
            "storage": {"events": {"backend": "jsonl", "path": "data"}},
            "telemetry": {"logging": {"level": "CRITICAL"}},
            "sessions": sessions,
        }
        (tmp_path / "loom.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
        agent: dict[str, Any] = {
            "name": "demo",
            "description": "Relance des devis.",
            "main": {"model": "FAKE", "system": "Tu relances."},
            "tools": [{"python": "chercher"}],
            "roles": [
                {
                    "name": "rediger",
                    "description": "Rédige la relance.",
                    "model": "ROLE",
                    "system": "Tu rédiges.",
                    "input_schema": {
                        "type": "object",
                        "properties": {"ton": {"type": "string"}},
                        "required": ["ton"],
                    },
                    "context": context,
                    "terminal": True,
                }
            ],
        }
        (tmp_path / "agents" / "demo.yaml").write_text(yaml.safe_dump(agent), encoding="utf-8")
        return tmp_path / "loom.yaml"

    return build


def two_turns(path: Path, *, compact: bool = False) -> str:
    """Deux runs d'une session ; rend la réponse finale du second."""

    async def go() -> str:
        async with Loom.from_config(path) as loom:
            await loom.run("demo", CHERCHE, session_id=SESSION)
            if compact:
                await loom.drain()
            second = await loom.run("demo", REDIGE, session_id=SESSION)
            return second.text

    return asyncio.run(go())


def test_a_role_reads_the_session_summary(withrole: Callable[..., Path]) -> None:
    path = withrole(context=["session_summary"], compaction={"over_tokens": 10, "keep_last": 0})

    answer = two_turns(path, compact=True)

    assert "<session_summary>" in answer
    assert FIDELE in answer


def test_a_role_reads_the_last_turns(withrole: Callable[..., Path]) -> None:
    path = withrole(context=[{"last_turns": 1}])

    answer = two_turns(path)

    assert "<last_turns>" in answer
    assert f"Utilisateur : {CHERCHE}" in answer
    assert "Agent appelle chercher" in answer


def test_a_role_can_reach_a_result_from_an_earlier_turn(withrole: Callable[..., Path]) -> None:
    path = withrole(context=[{"tool_results": ["chercher"], "scope": "session"}])

    answer = two_turns(path)

    assert "<tool_result" in answer
    assert "D-2026-042" in answer


def test_without_the_session_scope_the_role_is_refused(withrole: Callable[..., Path]) -> None:
    path = withrole(context=[{"tool_results": ["chercher"]}])

    async def go() -> list[Event]:
        async with Loom.from_config(path) as loom:
            await loom.run("demo", CHERCHE, session_id=SESSION)
            await loom.run("demo", REDIGE, session_id=SESSION)
            return await loom.export_session(SESSION)

    events = asyncio.run(go())
    refused = [
        e
        for e in events
        if e.type == "tool.completed"
        and e.facets.get("tool_name") == "rediger"
        and e.facets.get("is_error")
    ]

    assert refused, "le rôle doit être refusé faute de résultat dans le run"
    payload = refused[0].payload
    assert isinstance(payload, ToolCompleted)
    assert "a besoin d'un résultat de chercher" in payload.output.as_text
