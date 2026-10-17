# SPDX-License-Identifier: Apache-2.0
"""Rapport de consommation d'un run ou d'une session (J3, J5).

Le rapport se calcule à partir du ledger (``core.projections.ledger``) :
chaque appel de modèle du journal, sans double compte des sous-agents. Il
ventile la consommation par run (le run et ses sous-runs, ou tous les runs
de la session), par rôle (``main``, rôles délégués, ``judge:<nom>``,
préfixés par l'agent quand plusieurs agents ont travaillé) et par modèle — son
identifiant dans la config, qui distingue deux déclarations d'un même modèle du
fournisseur ; à défaut (journal antérieur), son nom chez le fournisseur.
Par client et par période : J5.
"""

from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Final

from pydantic import NonNegativeFloat, NonNegativeInt

from loom_ia.core.events import Event, Payload, RunCancelled, RunCompleted, RunFailed, RunStarted
from loom_ia.core.model import DomainModel, RunId, SessionId, Usage
from loom_ia.core.projections import LedgerEntry, RunTree, ledger


class UsageLine(DomainModel):
    """Consommation d'une part : un run, un rôle, un modèle, ou le total."""

    name: str
    calls: NonNegativeInt = 0
    usage: Usage = Usage()
    cost: NonNegativeFloat = 0.0


class ModelUsage(UsageLine):
    """Consommation d'un modèle (``name`` : son identifiant dans la config)."""

    # Nom chez le fournisseur ; égal à ``name`` pour un journal antérieur, qui
    # n'a pas l'identifiant de la config.
    model_id: str


class RunUsage(UsageLine):
    """Consommation d'un run seul (``name`` : son identifiant), sans ses sous-runs."""

    agent: str
    depth: NonNegativeInt = 0
    # ``completed``, ``failed``, ``cancelled``, ou None pour un run pas encore clos.
    status: str | None = None


# Ce que devient un run à l'événement qui le clôt.
_CLOSED: Final[Mapping[type[Payload], str]] = {
    RunCompleted: "completed",
    RunFailed: "failed",
    RunCancelled: "cancelled",
}


class UsageReport(DomainModel):
    """Consommation d'un run et de ses sous-runs, ou de toute une session."""

    session_id: SessionId
    # Run racine du rapport ; None pour toute la session.
    run_id: RunId | None = None
    total: UsageLine
    runs: tuple[RunUsage, ...] = ()
    roles: tuple[UsageLine, ...] = ()
    models: tuple[ModelUsage, ...] = ()


def usage_report(
    events: Sequence[Event], session_id: SessionId, run_id: RunId | None = None
) -> UsageReport:
    """Rapport des événements d'une session ; avec ``run_id``, de ce run et de ses sous-runs."""
    selected = RunTree(run_id).select(events) if run_id is not None else list(events)
    entries = ledger(selected)
    agents = {entry.agent for entry in entries}

    def role(entry: LedgerEntry) -> str:
        return f"{entry.agent} · {entry.role}" if len(agents) > 1 else entry.role

    # Un passage sur les appels, un sur les événements : chaque run est retrouvé
    # par son identifiant, et le rapport croît avec la session, pas plus vite.
    calls = _by(entries, lambda e: e.run_id)
    runs: dict[RunId, RunUsage] = {}
    for event in selected:
        payload = event.payload
        if isinstance(payload, RunStarted):
            line = _line(event.run_id, calls.get(event.run_id, ()))
            runs[event.run_id] = RunUsage(
                name=line.name,
                calls=line.calls,
                usage=line.usage,
                cost=line.cost,
                agent=event.agent or "",
                depth=payload.depth,
            )
        elif (status := _CLOSED.get(type(payload))) is not None and event.run_id in runs:
            runs[event.run_id] = runs[event.run_id].model_copy(update={"status": status})
    return UsageReport(
        session_id=session_id,
        run_id=run_id,
        total=_line("total", entries),
        runs=tuple(runs.values()),
        roles=_grouped(entries, role),
        models=tuple(
            ModelUsage(**_line(name, mine).model_dump(), model_id=mine[0].model_id)
            for name, mine in _by(entries, lambda e: e.model_key).items()
        ),
    )


def render(report: UsageReport) -> list[str]:
    """Le rapport en lignes de texte, pour la CLI."""
    subject = f"run {report.run_id}" if report.run_id else f"session {report.session_id}"
    models = [(_model_label(line), line) for line in report.models]
    labels = [line.name for line in report.roles] + [label for label, _ in models]
    width = max((len(label) for label in labels), default=10)
    lines = [f"Consommation — {subject}", f"  {_row('Total', report.total, width)}"]
    if len(report.runs) > 1 or report.run_id is None:
        lines.append("  Par run :")
        for run in report.runs:
            status = f" ({run.status})" if run.status else " (en cours)"
            indent = "  " * run.depth
            label = f"{indent}{run.agent}{status} {run.name}"
            lines.append(f"    {_values(run)} — {label}")
    lines.append("  Par rôle :")
    lines += [f"    {_row(line.name, line, width)}" for line in report.roles]
    lines.append("  Par modèle :")
    lines += [f"    {_row(label, line, width)}" for label, line in models]
    return lines


def _model_label(line: ModelUsage) -> str:
    """``HAIKU (claude-haiku-5-5)`` ; le nom seul quand le journal n'avait pas l'identifiant."""
    return line.name if line.name == line.model_id else f"{line.name} ({line.model_id})"


def _line(name: str, entries: Iterable[LedgerEntry]) -> UsageLine:
    calls, usage, cost = 0, Usage(), 0.0
    for entry in entries:
        calls, usage, cost = calls + 1, usage + entry.usage, cost + entry.cost
    return UsageLine(name=name, calls=calls, usage=usage, cost=cost)


def _grouped(
    entries: Sequence[LedgerEntry], key: Callable[[LedgerEntry], str]
) -> tuple[UsageLine, ...]:
    """Une ligne par clé, dans l'ordre de première apparition."""
    return tuple(_line(name, mine) for name, mine in _by(entries, key).items())


def _by[K](
    entries: Iterable[LedgerEntry], key: Callable[[LedgerEntry], K]
) -> dict[K, list[LedgerEntry]]:
    """Les appels rangés par clé, dans l'ordre de première apparition."""
    grouped: dict[K, list[LedgerEntry]] = {}
    for entry in entries:
        grouped.setdefault(key(entry), []).append(entry)
    return grouped


def _row(label: str, line: UsageLine, width: int) -> str:
    return f"{label:<{width}}  {_values(line)}"


def _values(line: UsageLine) -> str:
    usage = line.usage
    calls = f"{line.calls} appel{'s' if line.calls > 1 else ''}"
    tokens = f"{usage.prompt_tokens}/{usage.output_tokens} tokens"
    cost = f"{line.cost:.5f} $".replace(".", ",")
    return f"{calls:>9} · {tokens:>17} · {cost}"
