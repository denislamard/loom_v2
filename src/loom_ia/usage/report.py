# SPDX-License-Identifier: Apache-2.0
"""Rapport de consommation d'un run ou d'une session (J3, J5).

Le rapport se calcule à partir du ledger (``core.projections.ledger``) :
chaque appel de modèle du journal, sans double compte des sous-agents. Il
ventile la consommation par run (le run et ses sous-runs, ou tous les runs
de la session), par rôle (``main``, rôles délégués, ``judge:<nom>``,
préfixés par l'agent quand plusieurs agents ont travaillé) et par modèle.
Par client et par période : J5.
"""

from collections.abc import Callable, Iterable, Sequence

from pydantic import NonNegativeFloat, NonNegativeInt

from loom_ia.core.events import Event, RunCompleted, RunFailed, RunStarted
from loom_ia.core.model import DomainModel, RunId, SessionId, Usage
from loom_ia.core.projections import LedgerEntry, RunTree, ledger


class UsageLine(DomainModel):
    """Consommation d'une part : un run, un rôle, un modèle, ou le total."""

    name: str
    calls: NonNegativeInt = 0
    usage: Usage = Usage()
    cost: NonNegativeFloat = 0.0


class RunUsage(UsageLine):
    """Consommation d'un run seul (``name`` : son identifiant), sans ses sous-runs."""

    agent: str
    depth: NonNegativeInt = 0
    # ``completed``, ``failed``, ou None pour un run pas encore clos.
    status: str | None = None


class UsageReport(DomainModel):
    """Consommation d'un run et de ses sous-runs, ou de toute une session."""

    session_id: SessionId
    # Run racine du rapport ; None pour toute la session.
    run_id: RunId | None = None
    total: UsageLine
    runs: tuple[RunUsage, ...] = ()
    roles: tuple[UsageLine, ...] = ()
    models: tuple[UsageLine, ...] = ()


def usage_report(
    events: Sequence[Event], session_id: SessionId, run_id: RunId | None = None
) -> UsageReport:
    """Rapport des événements d'une session ; avec ``run_id``, de ce run et de ses sous-runs."""
    selected = RunTree(run_id).select(events) if run_id is not None else list(events)
    entries = ledger(selected)
    agents = {entry.agent for entry in entries}

    def role(entry: LedgerEntry) -> str:
        return f"{entry.agent} · {entry.role}" if len(agents) > 1 else entry.role

    runs: list[RunUsage] = []
    for event in selected:
        payload = event.payload
        if isinstance(payload, RunStarted):
            mine = [e for e in entries if e.run_id == event.run_id]
            line = _line(event.run_id, mine)
            runs.append(
                RunUsage(
                    name=line.name,
                    calls=line.calls,
                    usage=line.usage,
                    cost=line.cost,
                    agent=event.agent or "",
                    depth=payload.depth,
                )
            )
        elif isinstance(payload, RunCompleted | RunFailed):
            status = "completed" if isinstance(payload, RunCompleted) else "failed"
            runs = [
                r.model_copy(update={"status": status}) if r.name == event.run_id else r
                for r in runs
            ]
    return UsageReport(
        session_id=session_id,
        run_id=run_id,
        total=_line("total", entries),
        runs=tuple(runs),
        roles=_grouped(entries, role),
        models=_grouped(entries, lambda e: e.model_id),
    )


def render(report: UsageReport) -> list[str]:
    """Le rapport en lignes de texte, pour la CLI."""
    subject = f"run {report.run_id}" if report.run_id else f"session {report.session_id}"
    width = max((len(line.name) for line in (*report.roles, *report.models)), default=10)
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
    lines += [f"    {_row(line.name, line, width)}" for line in report.models]
    return lines


def _line(name: str, entries: Iterable[LedgerEntry]) -> UsageLine:
    calls, usage, cost = 0, Usage(), 0.0
    for entry in entries:
        calls, usage, cost = calls + 1, usage + entry.usage, cost + entry.cost
    return UsageLine(name=name, calls=calls, usage=usage, cost=cost)


def _grouped(
    entries: Sequence[LedgerEntry], key: Callable[[LedgerEntry], str]
) -> tuple[UsageLine, ...]:
    """Une ligne par clé, dans l'ordre de première apparition."""
    names = list(dict.fromkeys(key(entry) for entry in entries))
    return tuple(_line(name, [e for e in entries if key(e) == name]) for name in names)


def _row(label: str, line: UsageLine, width: int) -> str:
    return f"{label:<{width}}  {_values(line)}"


def _values(line: UsageLine) -> str:
    usage = line.usage
    calls = f"{line.calls} appel{'s' if line.calls > 1 else ''}"
    tokens = f"{usage.prompt_tokens}/{usage.output_tokens} tokens"
    cost = f"{line.cost:.5f} $".replace(".", ",")
    return f"{calls:>9} · {tokens:>17} · {cost}"
