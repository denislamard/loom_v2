# SPDX-License-Identifier: Apache-2.0
"""Budgets dans la config, au montage et par les accès : fusion, #010, rapport (J3.4)."""

import json
import logging
from pathlib import Path
from typing import Any

import pytest
from conftest import MODEL, QUESTION, ConfigFactory, demo_agent

from loom_ia.access import Loom
from loom_ia.access.cli import main
from loom_ia.config import ConfigError, load_config
from loom_ia.core.model import RunBudget, RunId, SessionId
from loom_ia.engine import AgentTool
from loom_ia.usage import BudgetGuard

PRICED = {**MODEL, "pricing": {"input": 1.0, "output": 5.0}}


def budgeted(demo: ConfigFactory, *, model: dict[str, Any] = PRICED, **agent: Any) -> Path:
    return demo(
        models=[model],
        storage={"events": {"backend": "jsonl", "path": "data"}},
        budgets={"run": {"max_calls": 20, "max_cost": 1.0}, "session": {"max_cost": 5.0}},
        agents=[demo_agent(**agent)],
    )


async def test_agent_budget_overrides_the_root_and_is_bound(demo: ConfigFactory) -> None:
    path = budgeted(demo, budget={"run": {"max_cost": 0.5}, "on_exceed": "warn"})
    config = load_config(path)
    budgets = config.budget_of("demo")
    assert budgets.run == RunBudget(max_cost=0.5, max_calls=20)
    assert (budgets.session.max_cost, budgets.on_exceed) == (5.0, "warn")
    async with Loom(config) as loom:
        bound = loom.context("demo").policies.bound
        assert [b.name for b in bound] == ["loom.budget"]
        guard = bound[0].policy
        assert isinstance(guard, BudgetGuard) and guard.budgets == budgets
        result = await loom.run("demo", QUESTION)
        report = await loom.report(result.run_id)
    assert report.total.calls == 2 and report.total.cost == pytest.approx(result.cost_usd)
    assert [line.name for line in report.roles] == ["main"]


def test_a_dollar_budget_without_pricing_warns(
    demo: ConfigFactory, caplog: pytest.LogCaptureFixture
) -> None:
    path = budgeted(demo, model=MODEL)
    with caplog.at_level(logging.WARNING, logger="loom_ia.runtime.wiring"):
        Loom.from_config(path).context("demo")
    assert [r.getMessage() for r in caplog.records] == [
        "Agent 'demo' : budget en dollars, mais sans tarif pour FAKE : leurs appels comptent 0 $"
    ]
    caplog.clear()
    path = demo(models=[MODEL], budgets={"run": {"max_tokens": 10_000}})
    with caplog.at_level(logging.WARNING, logger="loom_ia.runtime.wiring"):
        Loom.from_config(path).context("demo")
    assert not caplog.records


def test_the_cli_shows_budgets_and_reports(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    path = str(budgeted(demo))
    assert main(["--config", path, "validate"]) == 0
    assert "    budget : run max_cost 1, max_calls 20 ; session max_cost 5 ; stop" in (
        capsys.readouterr().out
    )
    assert main(["--config", path, "run", "demo", QUESTION, "--json"]) == 0
    run_id = json.loads(capsys.readouterr().out)["run_id"]

    assert main(["--config", path, "report", run_id]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == f"Consommation — run {run_id}"
    assert lines[1].startswith("  Total") and "2 appels" in lines[1]
    assert main(["--config", path, "report", "--session", run_id, "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["run_id"] is None and report["total"]["calls"] == 2
    assert len(report["runs"]) == 1

    assert main(["--config", path, "report"]) == 2
    assert main(["--config", path, "report", "inconnu"]) == 1
    assert "inconnu" in capsys.readouterr().err


async def test_report_needs_a_run_or_a_session(demo: ConfigFactory) -> None:
    async with Loom.from_config(budgeted(demo)) as loom:
        with pytest.raises(ValueError, match="run_id ou un session_id"):
            await loom.report()
        empty = await loom.report(session_id=SessionId("vide"))
        assert empty.total.calls == 0 and empty.runs == ()
        with pytest.raises(KeyError):
            await loom.report(RunId("absent"))


def share(path: Path, budget: str = "") -> None:
    """Donne au sous-agent de ``demo`` une part de budget (et à ``demo`` un budget)."""
    agent = path.parent / "agents" / "demo.yaml"
    text = agent.read_text(encoding="utf-8").replace(
        "name: verifier", "name: verifier\n  budget_share: 0.5"
    )
    agent.write_text(text + budget, encoding="utf-8")


def test_a_shared_subagent_is_budgeted(tree: ConfigFactory) -> None:
    path = tree()
    share(path, "budget:\n  run: {max_cost: 1.0}\n")
    loom = Loom.from_config(path)
    # verificateur n'a pas de budget à lui, mais il peut recevoir une part.
    assert [b.name for b in loom.context("verificateur").policies.bound] == ["loom.budget"]
    tool = loom.context("demo").tools.get("verifier")
    assert isinstance(tool, AgentTool) and tool.definition.budget_share == 0.5
    assert tool.definition.parent_budget == RunBudget(max_cost=1.0)


def test_budget_share_needs_a_run_budget(tree: ConfigFactory) -> None:
    path = tree()
    share(path)
    with pytest.raises(ConfigError, match="budget_share demande un budget du run"):
        load_config(path)
