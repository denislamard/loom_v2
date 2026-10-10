# SPDX-License-Identifier: Apache-2.0
"""Rejouer un run, à l'identique ou en variante, et dire où il s'écarte de son journal (K6, #31).

Le rejeu se fait **en mémoire**, sur une copie de la session arrêtée juste
après la demande du run : tout ce qui précédait — les runs d'avant, leurs
résumés, les instantanés — est là, si bien que l'historique et la requête se
reconstruisent comme la première fois. Le run garde **son identifiant** : un
juge tiré au sort (``sample``) l'est sur lui, et le tirage est donc le même.

Puis le moteur reprend le run là où la demande l'a laissé (``drive``), avec un
agent monté comme d'habitude, sauf pour le monde : au rejeu **identique**, ses
clients de modèle, ses outils (sauf les rôles, qui sont de la logique) et ses
approbations sont servis par le journal, et la première divergence arrête
tout ; en **variante** (``variant``), ce que le journal connaît est servi, le
reste part pour de vrai — outils à effets de bord exceptés —, et le rapport
compare les deux runs.

Rien n'est écrit dans le vrai journal. ``export`` garde le journal du rejeu.
"""

import asyncio
import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Literal

from pydantic import JsonValue, ValidationError

from loom_ia.adapters.stores import InMemoryEventStore
from loom_ia.core.events import (
    Event,
    EventDraft,
    JudgeEvaluated,
    ModelResponded,
    RunCancelled,
    RunCompleted,
    RunFailed,
    RunStarted,
    RunTransitioned,
    ToolCalled,
    ToolCompleted,
    UserMessage,
)
from loom_ia.core.model import RunId, RunState, RunStatus, SessionId, TenantId, Usage
from loom_ia.core.ports import EventStore
from loom_ia.core.projections import fold
from loom_ia.engine import RunContext, drive
from loom_ia.replay.book import Divergence, JournalTools, ReplayBook, ReplayError
from loom_ia.replay.variant import Double, ToolFate, VariantTools

logger = logging.getLogger(__name__)

type ReplayMode = Literal["exact", "variant"]

# États qui dépendent de la façon de piloter, pas de ce que le run a fait : une
# approbation tranchée en ligne ne passe pas par la pause, un sous-agent rejoué
# depuis le journal ne fait pas attendre son parent.
_ASIDE: Final = frozenset({RunStatus.PAUSED, RunStatus.WAITING_CHILD})
_TERMINAL: Final = (RunCompleted, RunFailed, RunCancelled)


@dataclass(frozen=True, slots=True)
class Verdict:
    """Un verdict de juge, tel que le run l'a reçu."""

    judge: str
    attempt: int
    passed: bool
    scores: tuple[tuple[str, float], ...]


@dataclass(frozen=True, slots=True)
class RunSide:
    """Un des deux runs comparés : ce qu'il a fait et ce qu'il a coûté."""

    end: str
    text: str | None
    data: JsonValue
    error_type: str | None
    # Appels de modèle et d'outil de son arbre (sous-agents compris, rôles à
    # part pour les outils : leurs appels de modèle sont comptés avec les autres).
    model_calls: int
    tool_calls: int
    usage: Usage
    cost_usd: float
    active_ms: float
    verdicts: tuple[Verdict, ...]

    def as_json(self) -> dict[str, JsonValue]:
        return {
            "end": self.end,
            "text": self.text,
            "data": self.data,
            "error_type": self.error_type,
            "model_calls": self.model_calls,
            "tool_calls": self.tool_calls,
            "usage": self.usage.model_dump(mode="json"),
            "cost_usd": self.cost_usd,
            "active_ms": self.active_ms,
            "verdicts": [
                {
                    "judge": v.judge,
                    "attempt": v.attempt,
                    "passed": v.passed,
                    "scores": dict(v.scores),
                }
                for v in self.verdicts
            ],
        }


@dataclass(frozen=True, slots=True)
class Comparison:
    """Le run d'origine et sa variante, côte à côte (J6.2b)."""

    original: RunSide
    variant: RunSide
    # Appels de modèle de la variante : servis par le journal, partis pour de vrai.
    served_models: int
    real_models: int
    # Ce que les vrais appels ont coûté : la seule dépense de la variante.
    spent_usd: float
    real_usage: Usage
    # Sort des appels d'outil conclus de la variante (rôles à part), compté…
    tools: Mapping[ToolFate, int]
    # … et appel par appel : (outil, sort), dans l'ordre du journal du rejeu.
    calls: tuple[tuple[str, ToolFate], ...] = ()
    # Modèles changés par étape (``main``, rôle, ``judge:<nom>``).
    swapped: Mapping[str, str] = field(default_factory=dict[str, str])

    @property
    def same_answer(self) -> bool:
        return (
            self.original.end == self.variant.end
            and self.original.text == self.variant.text
            and self.original.data == self.variant.data
        )

    def as_json(self) -> dict[str, JsonValue]:
        return {
            "original": self.original.as_json(),
            "variant": self.variant.as_json(),
            "served_models": self.served_models,
            "real_models": self.real_models,
            "spent_usd": self.spent_usd,
            "real_usage": self.real_usage.model_dump(mode="json"),
            "tools": {fate: count for fate, count in self.tools.items()},
            "calls": [[name, fate] for name, fate in self.calls],
            "swapped": dict(self.swapped),
            "same_answer": self.same_answer,
        }


@dataclass(frozen=True, slots=True)
class ReplayReport:
    """Ce que le rejeu a trouvé, comparé au journal."""

    run_id: RunId
    session_id: SessionId
    tenant_id: TenantId
    agent: str
    mode: ReplayMode
    identical: bool
    # Rejeu identique : là où il s'arrête ; variante : là où elle quitte le run d'origine.
    divergence: Divergence | None
    # Issue du run : ``completed``, ``failed`` ou ``cancelled``.
    original_end: str
    replay_end: str
    # (au journal, servis au rejeu par le journal). Les outils sont ceux que le
    # journal sert — pas les rôles, qui tournent et se comptent par leurs
    # appels de modèle. En variante, l'arbre du run (sous-agents compris).
    model_calls: tuple[int, int]
    tool_calls: tuple[int, int]
    # Variante : les deux runs côte à côte.
    comparison: Comparison | None = None
    # Événements du run rejoué (de son arbre en variante), demande comprise.
    events: tuple[Event, ...] = field(default=(), repr=False)

    def as_json(self) -> dict[str, object]:
        divergence = self.divergence
        return {
            "run_id": self.run_id,
            "session_id": self.session_id,
            "tenant_id": self.tenant_id,
            "agent": self.agent,
            "mode": self.mode,
            "identical": self.identical,
            "divergence": None
            if divergence is None
            else {
                "kind": divergence.kind,
                "where": divergence.where,
                "detail": divergence.detail,
                "parts": list(divergence.parts),
                "expected_hash": divergence.expected_hash,
                "actual_hash": divergence.actual_hash,
                "rank": divergence.rank,
            },
            "original_end": self.original_end,
            "replay_end": self.replay_end,
            "model_calls": {"journal": self.model_calls[0], "replay": self.model_calls[1]},
            "tool_calls": {"journal": self.tool_calls[0], "replay": self.tool_calls[1]},
            "comparison": None if self.comparison is None else self.comparison.as_json(),
        }


@dataclass(frozen=True, slots=True)
class JournalRun:
    """Un run racine d'un journal, et où le trouver."""

    tenant_id: TenantId
    session_id: SessionId
    run_id: RunId
    agent: str
    finished: bool


@dataclass(frozen=True, slots=True)
class JournalReplay:
    """Les runs d'un journal, rejoués un à un (J6.3b)."""

    # Le fichier rejoué ; ``None`` quand ses événements ont été donnés déjà lus.
    journal: Path | None
    reports: tuple[ReplayReport, ...]
    # Runs racines inachevés : un rejeu compare un run fini, ils ne sont pas
    # rejoués — et le journal ne se rejoue donc pas en entier.
    unfinished: tuple[RunId, ...] = ()

    @property
    def identical(self) -> bool:
        """Chaque run du journal se rejoue à l'identique, et aucun n'est resté de côté."""
        return bool(self.reports) and not self.unfinished and all(r.identical for r in self.reports)

    def as_json(self) -> dict[str, object]:
        return {
            "journal": None if self.journal is None else str(self.journal),
            "identical": self.identical,
            "runs": [report.as_json() for report in self.reports],
            "unfinished": list(self.unfinished),
        }


type ContextFactory = Callable[
    [EventStore, ReplayBook, JournalTools | VariantTools, str, TenantId], RunContext
]


def read_journal(path: Path) -> list[Event]:
    """Les événements d'un journal JSONL ; ``ReplayError`` s'il ne se lit pas.

    Le format est celui qu'écrivent ``loom sessions export``, ``loom eval
    --export`` et ``loom replay --export`` : un événement par ligne, en clair.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ReplayError(f"Journal {path} illisible : {exc}") from exc
    events: list[Event] = []
    # Coupé sur « \n » seulement : ``splitlines`` verrait aussi une fin de ligne dans U+2028,
    # U+2029 ou U+0085, que ``model_dump_json`` écrit tels quels dans un texte.
    for number, line in enumerate(text.split("\n"), start=1):
        if not line.strip():
            continue
        try:
            events.append(Event.model_validate_json(line))
        except ValidationError as exc:
            first = exc.errors()[0]
            where = ".".join(str(part) for part in first["loc"]) or "ligne"
            raise ReplayError(
                f"Journal {path}, ligne {number} : pas un événement ({where} : {first['msg']})"
            ) from exc
    if not events:
        raise ReplayError(f"Journal {path} : aucun événement")
    return events


def journal_runs(events: Sequence[Event]) -> list[JournalRun]:
    """Les runs qu'un journal donne à rejouer : ses runs racines, dans l'ordre du journal.

    Un sous-run se rejoue avec son run racine, qui le sert depuis le journal ;
    un run système (résumé de session) n'est pas un run demandé — ce qu'il a
    écrit est relu par les runs qui le suivent. Ni l'un ni l'autre n'est listé.
    """
    ended = {e.run_id for e in events if isinstance(e.payload, _TERMINAL)}
    return [
        JournalRun(
            tenant_id=event.tenant_id,
            session_id=event.session_id,
            run_id=event.run_id,
            agent=event.agent or "",
            finished=event.run_id in ended,
        )
        for event in events
        if isinstance(event.payload, RunStarted)
        and event.payload.parent_run_id is None
        and event.payload.kind == "normal"
    ]


async def replay_run(
    session_events: Sequence[Event],
    run_id: RunId,
    context_for: ContextFactory,
    *,
    mode: ReplayMode = "exact",
    doubles: Mapping[str, Double] | None = None,
    swapped: Mapping[str, str] | None = None,
    export: Path | None = None,
) -> ReplayReport:
    """Rejoue le run ``run_id`` de cette session et le compare à son journal.

    ``context_for(store, book, tools, agent, tenant)`` monte l'agent du run
    pour le rejeu : le journal en mémoire, le livre qui sert les réponses, et
    les outils du rejeu à poser sur ses exécuteurs — ceux du journal, ou ceux
    de la variante avec ses doublures (``doubles``). ``swapped`` : les modèles
    changés par étape, que le rapport recopie.
    """
    own = [e for e in session_events if e.run_id == run_id]
    if not own or not isinstance(own[0].payload, RunStarted):
        raise ReplayError(f"Run {run_id} introuvable dans cette session")
    first, started = own[0], own[0].payload
    if started.parent_run_id is not None:
        raise ReplayError(
            f"Run {run_id} : c'est le sous-run de {started.parent_run_id} — rejouer le run "
            "racine, qui sert le résultat de ce sous-agent depuis le journal"
        )
    if started.kind != "normal":
        raise ReplayError(f"Run {run_id} : run système ({started.kind}), non rejouable")
    ending = next((e for e in reversed(own) if isinstance(e.payload, _TERMINAL)), None)
    if ending is None:
        raise ReplayError(
            f"Run {run_id} : inachevé (en cours ou en pause) — un rejeu compare un run fini"
        )
    asked = next((e for e in own if isinstance(e.payload, UserMessage)), None)
    if asked is None:
        raise ReplayError(f"Run {run_id} : pas de demande au journal")

    # La session telle qu'elle était quand le run a reçu sa demande : mêmes
    # rangs, donc mêmes repères pour les instantanés et les résumés.
    prefix = [e for e in session_events if e.seq <= asked.seq]
    store = InMemoryEventStore()
    await store.append([_draft(e) for e in prefix], expected_seq=0)
    # En variante, un sous-agent relancé peut retrouver les requêtes de son
    # premier passage : le livre porte tout l'arbre du run.
    tree = _tree(session_events, run_id) if mode == "variant" else own
    book = ReplayBook.of(tree)
    tools = VariantTools(book, doubles) if mode == "variant" else JournalTools(book)
    ctx = context_for(store, book, tools, first.agent or "", first.tenant_id)
    final = await drive(ctx, run_id, session_id=first.session_id, tenant_id=first.tenant_id)

    stored = await store.read(first.tenant_id, first.session_id)
    replayed = [e for e in stored if e.run_id == run_id]
    after = [e for e in replayed if e.seq > asked.seq]
    replay_ending = next((e for e in reversed(after) if isinstance(e.payload, _TERMINAL)), None)
    _compare(book, own, after, ending, replay_ending)
    comparison: Comparison | None = None
    exported = replayed
    if mode == "variant":
        replayed_tree = _tree(stored, run_id)
        exported = replayed_tree
        calls = _fates(replayed_tree, tools if isinstance(tools, VariantTools) else None)
        comparison = Comparison(
            original=_side(tree, fold(own, run_id), ending),
            variant=_side(replayed_tree, final, replay_ending),
            served_models=book.served_responses,
            real_models=book.real_calls,
            spent_usd=book.spent_usd,
            real_usage=book.real_usage,
            tools=_counted(calls),
            calls=calls,
            swapped=dict(swapped or {}),
        )
    report = ReplayReport(
        run_id=run_id,
        session_id=first.session_id,
        tenant_id=first.tenant_id,
        agent=first.agent or "",
        mode=mode,
        identical=book.divergence is None,
        divergence=book.divergence,
        original_end=_end(ending),
        replay_end=_end(replay_ending),
        model_calls=(len(book.responses), book.served_responses),
        tool_calls=(book.journal_tools, book.served_tools),
        comparison=comparison,
        events=tuple(exported),
    )
    _log(report)
    if export is not None:
        lines = "".join(f"{event.model_dump_json()}\n" for event in exported)
        await asyncio.to_thread(export.write_text, lines, encoding="utf-8")
    return report


def _log(report: ReplayReport) -> None:
    """La divergence, dite une fois — le moteur, lui, n'a noté qu'un arrêt voulu."""
    divergence = report.divergence
    if divergence is None:
        return
    said = f"{divergence.where}" + (f" — {divergence.detail}" if divergence.detail else "")
    if report.mode == "exact":
        logger.warning(
            "Rejeu du run %s : divergence, %s",
            report.run_id,
            said,
            extra={"run_id": report.run_id, "tenant_id": report.tenant_id},
        )
    else:
        logger.info(
            "Variante du run %s : elle quitte le run d'origine, %s",
            report.run_id,
            said,
            extra={"run_id": report.run_id, "tenant_id": report.tenant_id},
        )


def _tree(events: Sequence[Event], run_id: RunId) -> list[Event]:
    """Les événements d'un run et de ses sous-runs, dans l'ordre du journal."""
    runs = {run_id}
    for event in events:
        payload = event.payload
        if isinstance(payload, RunStarted) and payload.parent_run_id in runs:
            runs.add(event.run_id)
    return [e for e in events if e.run_id in runs]


def _side(events: Sequence[Event], state: RunState, ending: Event | None) -> RunSide:
    tools = _tools_done(events)
    root = state.run_id
    return RunSide(
        end=_end(ending),
        text=state.output.text if state.output is not None else None,
        data=state.output_data,
        error_type=state.error_type,
        model_calls=sum(1 for e in events if isinstance(e.payload, ModelResponded)),
        tool_calls=len(tools),
        usage=state.usage,
        cost_usd=state.cost_usd,
        active_ms=state.active_ms,
        verdicts=tuple(
            Verdict(
                judge=e.payload.judge,
                attempt=e.payload.attempt,
                passed=e.payload.passed,
                scores=tuple((c.name, c.score) for c in e.payload.criteria),
            )
            for e in events
            if isinstance(e.payload, JudgeEvaluated) and e.run_id == root
        ),
    )


def _fates(events: Sequence[Event], tools: VariantTools | None) -> tuple[tuple[str, ToolFate], ...]:
    """Sort des appels d'outil conclus de la variante, par run et ``call_id``."""
    if tools is None:
        return ()
    fated: list[tuple[str, ToolFate]] = []
    for done in _tools_done(events):
        assert isinstance(done.payload, ToolCompleted)
        fate = tools.fates.get((done.run_id, done.payload.call_id))
        if fate is not None:
            fated.append((done.payload.tool_name, fate))
    return tuple(fated)


def _counted(calls: Sequence[tuple[str, ToolFate]]) -> dict[ToolFate, int]:
    counted: dict[ToolFate, int] = {}
    for _, fate in calls:
        counted[fate] = counted.get(fate, 0) + 1
    return counted


def _tools_done(events: Sequence[Event]) -> list[Event]:
    """Appels d'outil conclus, rôles à part (leurs appels de modèle se comptent ailleurs)."""
    kinds = {
        (e.run_id, e.payload.call_id): e.payload.tool_kind
        for e in events
        if isinstance(e.payload, ToolCalled)
    }
    return [
        e
        for e in events
        if isinstance(e.payload, ToolCompleted)
        and kinds.get((e.run_id, e.payload.call_id)) != "role"
    ]


def _compare(
    book: ReplayBook,
    original: Sequence[Event],
    replayed: Sequence[Event],
    ending: Event,
    replay_ending: Event | None,
) -> None:
    """Ce qui ne se voit qu'à la fin : appels non refaits, issue, réponse, parcours."""
    if book.diverged:
        return
    missing_models = book.unserved_responses
    missing_tools = book.unserved_tools
    if missing_models or missing_tools:
        said: list[str] = []
        if missing_models:
            said.append(f"{len(missing_models)} appel(s) de modèle")
        if missing_tools:
            said.append(f"{len(missing_tools)} appel(s) d'outil")
        book.diverge(
            Divergence(
                kind="end",
                where="le rejeu s'est arrêté avant le run d'origine",
                detail=f"non refaits : {' et '.join(said)}",
            )
        )
        return
    if replay_ending is None or replay_ending.type != ending.type:
        book.diverge(
            Divergence(
                kind="end",
                where="le run ne finit pas de la même façon",
                detail=f"{_end(ending)} au journal, {_end(replay_ending)} au rejeu",
            )
        )
        return
    if not _same_outcome(ending, replay_ending):
        book.diverge(
            Divergence(
                kind="end",
                where="la réponse finale diffère",
                detail="même issue, autre réponse (texte ou données)",
            )
        )
        return
    if _path(original) != _path(replayed):
        book.diverge(
            Divergence(
                kind="end",
                where="le parcours du run diffère",
                detail=f"{' → '.join(_path(original))} au journal ; "
                f"{' → '.join(_path(replayed))} au rejeu",
            )
        )


def _path(events: Sequence[Event]) -> list[str]:
    """Les états traversés, sans ceux qui tiennent à la façon de piloter."""
    states: list[str] = []
    for event in events:
        if isinstance(event.payload, RunTransitioned):
            state = event.payload.to_state
            if state in _ASIDE or (states and states[-1] == state):
                continue
            states.append(state.value)
    return states


def _same_outcome(original: Event, replayed: Event) -> bool:
    match original.payload, replayed.payload:
        case RunCompleted() as a, RunCompleted() as b:
            text_a = a.output.text if a.output is not None else None
            text_b = b.output.text if b.output is not None else None
            return text_a == text_b and a.data == b.data
        case RunFailed() as a, RunFailed() as b:
            return a.error_type == b.error_type
        case _:
            return True


def _end(event: Event | None) -> str:
    return "inachevé" if event is None else event.type.removeprefix("run.")


def _draft(event: Event) -> EventDraft:
    return EventDraft.model_validate(json.loads(event.model_dump_json(exclude={"seq"})))
