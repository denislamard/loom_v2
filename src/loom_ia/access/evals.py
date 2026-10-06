# SPDX-License-Identifier: Apache-2.0
"""Jouer une suite d'évals : chaque variante montée à part, en mémoire (O1, J6.3a).

``evaluate`` est ce que font ``Loom.evaluate`` et ``loom eval``. Pour chaque
variante :

1. sa config — celle de la suite, une autre, ou d'autres modèles par étape
   (``swap_models``, comme la variante du rejeu) — passe par ``isolated`` :
   journal, fichiers, file et bus en mémoire, ni collecteur, ni quotas, ni
   plafonds de période ;
2. une instance ``Loom`` la monte, ses outils interceptés par ``EvalTools`` :
   un outil à effets de bord n'est jamais exécuté, sa doublure répond ;
3. chaque cas y est joué ``repeat`` fois, chaque run dans sa propre session ;
   ses contrôles sont évalués, puis le juge d'éval note ses critères.

Le plafond ``max_cost_usd`` se vérifie **avant** chaque run : un run en cours
n'est pas coupé, les suivants ne partent pas. Rien de ce qu'une éval écrit
ne survit à l'instance de la variante, sauf ce que garde ``export``.
"""

import asyncio
import logging
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path

from pydantic import JsonValue

from loom_ia.access.api import Loom, RunResult, doubles_problem, tree_tools
from loom_ia.adapters.models import create_model_client
from loom_ia.agents.registry import AgentRegistry
from loom_ia.config import LoomConfig, Registry, load_config, resolve
from loom_ia.core.events import Event, ToolCalled, ToolCompleted
from loom_ia.core.model import DEFAULT_TENANT, TenantId
from loom_ia.engine import RunContext
from loom_ia.engine.refs import output_text
from loom_ia.replay import (
    CheckResult,
    Double,
    EvalCase,
    EvalError,
    EvalJudgeClient,
    EvalReport,
    EvalRun,
    EvalSuite,
    EvalTools,
    EvalVariant,
    Outcome,
    ReplayError,
    ToolUse,
    check,
    isolated,
    judged,
    swap_models,
    unjudged,
)
from loom_ia.runtime import load_registry

logger = logging.getLogger(__name__)

CAP_REACHED = "plafond de dépense atteint ({spent:.4f} $ sur {cap:.4f} $)"


async def evaluate(
    suite: EvalSuite,
    *,
    config: LoomConfig | None = None,
    environ: Mapping[str, str] | None = None,
    profile: str | None = None,
    cases: Sequence[str] = (),
    variants: Sequence[str] = (),
    export: Path | None = None,
    registry: Registry | None = None,
) -> EvalReport:
    """Joue la suite et rend son rapport ; ``EvalError`` ou ``ConfigError`` si elle est injouable.

    ``config`` sert quand la suite n'en désigne pas. ``cases`` et
    ``variants`` restreignent ce qui est joué, par nom. ``export`` reçoit le
    journal de chaque run, ``<variante>--<cas>--<n>.jsonl``. ``registry`` :
    les objets enregistrés d'une instance (``Loom.register``) — outils et
    doublures que la config ne déclare pas ; il sert aux variantes qui
    gardent la config de la suite, une variante qui a la sienne a le sien.
    """
    base = _base(suite, config, profile)
    chosen_cases = _chosen("cas", [c.name for c in suite.cases], cases)
    played = suite.played_variants()
    chosen_variants = _chosen("variante", [v.name for v in played], variants)
    doubles = _doubles(suite, base, registry)
    judge = _judge(suite, base, environ)
    runs: list[EvalRun] = []
    described: dict[str, Mapping[str, JsonValue]] = {}
    if export is not None:
        await asyncio.to_thread(export.mkdir, parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix="loom-eval-") as scratch:
            for variant in (v for v in played if v.name in chosen_variants):
                described[variant.name] = _described(suite, variant)
                variant_config = _variant_config(suite, base, variant, profile)
                _check_launch(variant_config, suite, variant)
                tools = EvalTools(doubles)
                setup = isolated(variant_config, Path(scratch) / variant.name)
                shared = registry if variant.config is None else None
                async with Loom(setup, environ=environ, intercept=tools, registry=shared) as loom:
                    _check_tools(loom, suite, variant, doubles)
                    for case in (c for c in suite.cases if c.name in chosen_cases):
                        for attempt in range(1, suite.repeat + 1):
                            runs.append(
                                await _play(
                                    loom, suite, case, variant, attempt, tools, judge, runs, export
                                )
                            )
    finally:
        if judge is not None:
            await judge.aclose()
    return EvalReport(
        suite=suite.title,
        agent=suite.agent,
        repeat=suite.repeat,
        variants=described,
        cases=tuple(c.name for c in suite.cases if c.name in chosen_cases),
        runs=tuple(runs),
        max_cost_usd=suite.max_cost_usd,
        judge_model=suite.judge.model if suite.judge is not None else None,
    )


# --- Avant de jouer : ce que la suite désigne existe ------------------------------


def _base(suite: EvalSuite, config: LoomConfig | None, profile: str | None) -> LoomConfig:
    path = suite.resolved(suite.config)
    if path is not None:
        return load_config(path, profile=profile)
    if config is None:
        raise EvalError(
            f"Suite {suite.title!r} : aucune config — la désigner ('config:' dans la suite) "
            "ou évaluer depuis une instance"
        )
    return config


def _chosen(what: str, declared: Sequence[str], asked: Sequence[str]) -> set[str]:
    unknown = sorted(set(asked) - set(declared))
    if unknown:
        raise EvalError(
            f"{what} inconnu(s) : {', '.join(unknown)} (déclarés : {', '.join(declared)})"
        )
    return set(asked) if asked else set(declared)


def _doubles(suite: EvalSuite, base: LoomConfig, registry: Registry | None) -> dict[str, Double]:
    """Les doublures de la suite, résolues : un nom enregistré, ou ``module:fonction``
    relatif à la suite."""
    if not suite.doubles:
        return {}
    registry = registry if registry is not None else load_registry(base)
    found: dict[str, Double] = {}
    for name, reference in suite.doubles.items():
        target = resolve(reference, registry, base_dir=suite.base_dir)
        if not callable(target):
            raise EvalError(f"Doublure {name}={reference} : ce n'est pas une fonction")
        found[name] = target
    return found


def _judge(
    suite: EvalSuite, base: LoomConfig, environ: Mapping[str, str] | None
) -> EvalJudgeClient | None:
    """Le juge d'éval, un modèle de la config de la suite : le même pour toutes les variantes."""
    if suite.judge is None:
        return None
    declared = {spec.id: spec for spec in base.models}
    spec = declared.get(suite.judge.model)
    if spec is None:
        raise EvalError(
            f"Juge d'éval : modèle {suite.judge.model!r} non déclaré dans la config "
            f"(modèles : {', '.join(declared) or 'aucun'})"
        )
    try:
        client = create_model_client(spec, environ=environ)
    except ValueError as exc:
        raise EvalError(
            f"Juge d'éval : le modèle {spec.id} ne peut pas être appelé — {exc}"
        ) from exc
    return EvalJudgeClient(spec, client)


def _variant_config(
    suite: EvalSuite, base: LoomConfig, variant: EvalVariant, profile: str | None
) -> LoomConfig:
    path = suite.resolved(variant.config)
    config = base if path is None else load_config(path, profile=profile)
    try:
        return swap_models(config, suite.agent, variant.models)
    except ReplayError as exc:
        raise EvalError(f"Variante {variant.name} : {exc}") from exc


def _check_launch(config: LoomConfig, suite: EvalSuite, variant: EvalVariant) -> None:
    """L'agent existe dans la config de la variante, et le client de la suite peut le lancer."""
    names = AgentRegistry.from_config(config).names
    if suite.agent not in names:
        raise EvalError(
            f"Variante {variant.name} : l'agent {suite.agent!r} n'est pas dans la config "
            f"(agents : {', '.join(sorted(names)) or 'aucun'})"
        )
    tenant = suite.tenant or DEFAULT_TENANT
    if not config.tenants:
        if tenant != DEFAULT_TENANT:
            raise EvalError(
                f"Variante {variant.name} : client {tenant!r} inconnu — la config ne déclare "
                "aucun client"
            )
        return
    declared = {spec.id: spec for spec in config.tenants}
    spec = declared.get(tenant)
    if spec is None:
        raise EvalError(
            f"Variante {variant.name} : client {tenant!r} inconnu (clients : {', '.join(declared)})"
        )
    if spec.agents and suite.agent not in spec.agents:
        raise EvalError(
            f"Variante {variant.name} : l'agent {suite.agent!r} n'est pas ouvert au client "
            f"{tenant!r}"
        )


def _check_tools(
    loom: Loom, suite: EvalSuite, variant: EvalVariant, doubles: Mapping[str, Double]
) -> None:
    """Doublures et outils que voit le juge : des outils de l'agent ou de son arbre."""
    judged = suite.judge.tool_results if suite.judge is not None else ()
    if not doubles and not judged:
        return
    tenant = suite.tenant

    def context_of(agent: str) -> RunContext:
        return loom.context(agent, tenant)

    problem = doubles_problem(loom.config, suite.agent, doubles, context_of)
    if problem is not None:
        raise EvalError(f"Variante {variant.name} : {problem}")
    known, prefixes = tree_tools(loom.config, suite.agent, context_of)
    unknown = [n for n in judged if n not in known and not n.startswith(prefixes)]
    if unknown:
        raise EvalError(
            f"Variante {variant.name} : le juge voit les résultats d'un outil inconnu, "
            f"{unknown[0]!r} (outils : {', '.join(sorted(known)) or 'aucun'})"
        )


def _described(suite: EvalSuite, variant: EvalVariant) -> dict[str, JsonValue]:
    path = suite.resolved(variant.config)
    return {
        "config": None if path is None else str(path),
        "models": dict(variant.models),
    }


# --- Un run ---------------------------------------------------------------------------


async def _play(
    loom: Loom,
    suite: EvalSuite,
    case: EvalCase,
    variant: EvalVariant,
    attempt: int,
    tools: EvalTools,
    judge: EvalJudgeClient | None,
    done: Sequence[EvalRun],
    export: Path | None,
) -> EvalRun:
    """Un cas, joué une fois : le run, ses contrôles, puis ses critères."""
    played = EvalRun(case=case.name, variant=variant.name, attempt=attempt)
    spent = sum(run.spent_usd for run in done)
    cap = suite.max_cost_usd
    if cap is not None and spent >= cap:
        return replace(played, skipped=CAP_REACHED.format(spent=spent, cap=cap))
    tenant = suite.tenant
    try:
        result = await loom.run(suite.agent, case.input, tenant=tenant)
    except Exception as exc:
        # Une éval dit ce qui a cassé et continue : le rapport le porte.
        logger.warning("Éval %s/%s : le run n'a pas pu aller au bout", variant.name, case.name)
        return replace(played, error=f"{type(exc).__name__}: {exc}")
    tenant_id = tenant or DEFAULT_TENANT
    events = await loom.events(result.run_id, session_id=result.session_id, tenant_id=tenant_id)
    state = await loom.state(result.run_id, session_id=result.session_id, tenant_id=tenant_id)
    outcome = _outcome(result, events, tools)
    checks: list[CheckResult] = check(case.expect, outcome)
    judge_cost = 0.0
    criteria = suite.criteria(case)
    if criteria and judge is not None:
        tool_results = suite.judge.tool_results if suite.judge is not None else ()
        judgment = await judge.judge(criteria, case.input, outcome, tool_results)
        judge_cost = judgment.cost_usd
        checks += (
            judged(judgment.scores)
            if judgment.error is None
            else unjudged(criteria, judgment.error)
        )
    if export is not None:
        await _export(
            loom, result, tenant_id, export / f"{variant.name}--{case.name}--{attempt}.jsonl"
        )
    return replace(
        played,
        run_id=result.run_id,
        status=result.status,
        text=result.text,
        failure=_failure(result),
        checks=tuple(checks),
        usage=result.usage,
        cost_usd=result.cost_usd,
        judge_cost_usd=judge_cost,
        active_ms=state.active_ms,
        tools=outcome.tools,
    )


def _outcome(result: RunResult, events: Sequence[Event], tools: EvalTools) -> Outcome:
    """Ce que le run a rendu, et les appels d'outil de son arbre : sort et résultat."""
    done = {
        (event.run_id, event.payload.call_id): event.payload.output
        for event in events
        if isinstance(event.payload, ToolCompleted)
    }
    uses: list[ToolUse] = []
    for event in events:
        if not isinstance(event.payload, ToolCalled):
            continue
        key = (event.run_id, event.payload.call_id)
        output = done.get(key)
        uses.append(
            ToolUse(
                name=event.payload.tool_name,
                arguments=dict(event.payload.arguments),
                fate=tools.fates.get(key),
                result=None if output is None else output_text(output),
                is_error=output is not None and output.is_error,
            )
        )
    return Outcome(
        status=result.status,
        text=result.text,
        data=result.data,
        error_type=result.error_type,
        tools=tuple(uses),
    )


def _failure(result: RunResult) -> str | None:
    if result.error_type is None:
        return None
    return f"{result.error_type} : {result.error}" if result.error else result.error_type


async def _export(loom: Loom, result: RunResult, tenant: TenantId, path: Path) -> None:
    """Le journal de la session du run, tel que ``loom sessions export`` l'écrirait."""
    events = await loom.store.read(tenant, result.session_id)
    lines = "".join(f"{event.model_dump_json()}\n" for event in events)
    await asyncio.to_thread(path.write_text, lines, encoding="utf-8")
