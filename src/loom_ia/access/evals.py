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

Un cas de rejeu (J6.3b) rejoue à l'identique, sur l'instance de la variante,
chaque run de ses journaux — lus **avant** le premier run de l'éval : ceux
qu'elle exporte ne seront rejoués qu'à la suivante.

Le plafond ``max_cost_usd`` se vérifie **avant** chaque run : un run en cours
n'est pas coupé, les suivants ne partent pas ; un rejeu ne dépense rien et
n'y est pas soumis. Rien de ce qu'une éval écrit ne survit à l'instance de la
variante, sauf ce que garde ``export``.
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
from loom_ia.core.events import Event, PolicyDecided, ToolCalled, ToolCompleted
from loom_ia.core.model import DEFAULT_TENANT, RunId, TenantId
from loom_ia.core.ports import ArtifactStore
from loom_ia.core.projections import fold
from loom_ia.engine import RunContext
from loom_ia.engine.refs import RefError, ResultIndex, output_text
from loom_ia.replay import (
    IDENTICAL,
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
    ReplayReport,
    ToolUse,
    check,
    isolated,
    journal_runs,
    judged,
    read_journal,
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
    journals = await _journals(suite, chosen_cases)
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
                _check_launch(variant_config, suite, variant, chosen_cases)
                tools = EvalTools(doubles)
                setup = isolated(variant_config, Path(scratch) / variant.name)
                shared = registry if variant.config is None else None
                async with Loom(setup, environ=environ, intercept=tools, registry=shared) as loom:
                    _check_tools(loom, suite, variant, doubles, chosen_cases)
                    for case in (c for c in suite.cases if c.name in chosen_cases):
                        if case.replay is not None:
                            runs += await _replayed(loom, suite, case, variant, journals[case.name])
                            continue
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
        replays=tuple(
            c.name for c in suite.cases if c.replay is not None and c.name in chosen_cases
        ),
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


def _check_launch(
    config: LoomConfig, suite: EvalSuite, variant: EvalVariant, chosen: set[str]
) -> None:
    """L'agent existe dans la config de la variante, et chaque client des cas joués peut le
    lancer."""
    names = AgentRegistry.from_config(config).names
    if suite.agent not in names:
        raise EvalError(
            f"Variante {variant.name} : l'agent {suite.agent!r} n'est pas dans la config "
            f"(agents : {', '.join(sorted(names)) or 'aucun'})"
        )
    played = (c for c in suite.cases if c.replay is None and c.name in chosen)
    for tenant in dict.fromkeys(suite.tenant_of(c) or DEFAULT_TENANT for c in played):
        _check_tenant(config, suite, variant, tenant)


def _check_tenant(
    config: LoomConfig, suite: EvalSuite, variant: EvalVariant, tenant: TenantId
) -> None:
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
    loom: Loom,
    suite: EvalSuite,
    variant: EvalVariant,
    doubles: Mapping[str, Double],
    chosen: set[str],
) -> None:
    """Doublures et outils que voit le juge : des outils de l'agent ou de son arbre."""
    judged = suite.judge.tool_results if suite.judge is not None else ()
    # Un rejeu monte l'agent avec les clients du journal : ni doublure ni juge à
    # vérifier, et pas de clé à demander.
    played = [c for c in suite.cases if c.replay is None and c.name in chosen]
    if not played or (not doubles and not judged):
        return
    # L'arbre des outils, vu par le client du premier cas joué.
    tenant = suite.tenant_of(played[0])

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
    tenant = suite.tenant_of(case)
    try:
        result = await loom.run(suite.agent, case.request, tenant=tenant)
    except Exception as exc:
        # Une éval dit ce qui a cassé et continue : le rapport le porte.
        logger.warning("Éval %s/%s : le run n'a pas pu aller au bout", variant.name, case.name)
        return replace(played, error=f"{type(exc).__name__}: {exc}")
    tenant_id = tenant or DEFAULT_TENANT
    events = await loom.events(result.run_id, session_id=result.session_id, tenant_id=tenant_id)
    state = await loom.state(result.run_id, session_id=result.session_id, tenant_id=tenant_id)
    outcome = await run_outcome(result, events, tools, loom.artifacts)
    checks: list[CheckResult] = check(case.expect, outcome)
    judge_cost = 0.0
    criteria = suite.criteria(case)
    if criteria and judge is not None:
        tool_results = suite.judge.tool_results if suite.judge is not None else ()
        judgment = await judge.judge(criteria, case.request, outcome, tool_results)
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


# --- Un cas de rejeu (J6.3b) ---------------------------------------------------------

type _Loaded = list[tuple[str, list[Event] | ReplayError]]


async def _journals(suite: EvalSuite, chosen: set[str]) -> dict[str, _Loaded]:
    """Les journaux des cas de rejeu, lus avant le premier run de l'éval.

    Lus d'avance, ils sont rejoués tels qu'ils étaient : ce qu'exporte cette
    même éval, dans le même dossier, ne le sera qu'à la suivante. Un fichier
    illisible fait tomber son cas, pas l'éval.
    """
    loaded: dict[str, _Loaded] = {}
    for case in suite.cases:
        if case.replay is None or case.name not in chosen:
            continue
        entries: _Loaded = []
        for path in await asyncio.to_thread(suite.journals, case):
            try:
                events: list[Event] | ReplayError = await asyncio.to_thread(read_journal, path)
            except ReplayError as error:
                events = error
            entries.append((_shown(suite, path), events))
        loaded[case.name] = entries
    return loaded


def _shown(suite: EvalSuite, path: Path) -> str:
    base = suite.base_dir
    if base is not None and path.is_relative_to(base):
        return str(path.relative_to(base))
    return str(path)


async def _replayed(
    loom: Loom, suite: EvalSuite, case: EvalCase, variant: EvalVariant, loaded: _Loaded
) -> list[EvalRun]:
    """Un cas de rejeu : chaque run de ses journaux, rejoué à l'identique sur cette variante."""
    if not loaded:
        return [
            EvalRun(
                case=case.name,
                variant=variant.name,
                attempt=1,
                error=f"aucun journal ne correspond à {case.replay}",
            )
        ]
    runs: list[EvalRun] = []

    def played(shown: str, run_id: RunId | None = None, error: str | None = None) -> EvalRun:
        return EvalRun(
            case=case.name,
            variant=variant.name,
            attempt=len(runs) + 1,
            journal=shown,
            run_id=run_id,
            error=error,
        )

    for shown, events in loaded:
        if isinstance(events, ReplayError):
            runs.append(played(shown, error=str(events)))
            continue
        others = sorted({run.agent for run in journal_runs(events)} - {suite.agent})
        if others:
            # Un journal d'un autre agent n'éprouve pas celui de la suite.
            runs.append(
                played(
                    shown,
                    error=f"journal de l'agent {others[0]!r} — la suite évalue {suite.agent!r}",
                )
            )
            continue
        try:
            replayed = await loom.replay_journal(events)
        except ReplayError as exc:
            runs.append(played(shown, error=str(exc)))
            continue
        except Exception as exc:
            logger.warning("Éval %s/%s : %s ne se rejoue pas", variant.name, case.name, shown)
            runs.append(played(shown, error=f"{type(exc).__name__}: {exc}"))
            continue
        for report in replayed.reports:
            runs.append(_replay_run(played(shown, run_id=report.run_id), report))
        for run_id in replayed.unfinished:
            runs.append(
                played(
                    shown,
                    run_id=run_id,
                    error="inachevé au journal — un rejeu compare un run fini",
                )
            )
    return runs


def _replay_run(run: EvalRun, report: ReplayReport) -> EvalRun:
    divergence = report.divergence
    detail = ""
    if divergence is not None:
        detail = divergence.where + (f" ; {divergence.detail}" if divergence.detail else "")
    return replace(
        run,
        checks=(CheckResult(IDENTICAL, report.identical, detail, kind="replay"),),
        divergence=divergence,
    )


# --- Ce qu'un run a rendu ----------------------------------------------------------


async def run_outcome(
    result: RunResult, events: Sequence[Event], tools: EvalTools, artifacts: ArtifactStore
) -> Outcome:
    """Ce que le run a rendu, et les appels d'outil de son arbre : reçu, sort et résultat."""
    done = {
        (event.run_id, event.payload.call_id): event.payload.output
        for event in events
        if isinstance(event.payload, ToolCompleted)
    }
    received = await received_arguments(events, artifacts)
    uses: list[ToolUse] = []
    for event in events:
        if not isinstance(event.payload, ToolCalled):
            continue
        key = (event.run_id, event.payload.call_id)
        output = done.get(key)
        written = dict(event.payload.arguments)
        got = received.get(key, written)
        uses.append(
            ToolUse(
                name=event.payload.tool_name,
                arguments=got,
                fate=tools.fates.get(key),
                result=None if output is None else output_text(output),
                is_error=output is not None and output.is_error,
                written=None if got == written else written,
            )
        )
    return Outcome(
        status=result.status,
        text=result.text,
        data=result.data,
        error_type=result.error_type,
        tools=tuple(uses),
    )


async def received_arguments(
    events: Sequence[Event], artifacts: ArtifactStore | None = None
) -> dict[tuple[str, str], dict[str, JsonValue]]:
    """Ce que chaque outil de l'arbre a reçu, par run et ``call_id`` (décision du 06/10).

    Comme l'exécuteur : les références ``$ref`` résolues sur les résultats du
    run (``artifacts`` relit un résultat déporté), puis les arguments qu'une
    politique ``before_tool`` a mis à la place, la dernière l'emportant. Une
    référence qui ne se résout plus (contenu déporté introuvable) laisse les
    arguments tels qu'écrits.
    """
    replaced: dict[tuple[str, str], dict[str, JsonValue]] = {}
    for event in events:
        payload = event.payload
        if (
            isinstance(payload, PolicyDecided)
            and payload.point == "before_tool"
            and payload.decision == "replace"
            and payload.call_id is not None
            and payload.arguments is not None
        ):
            replaced[(event.run_id, payload.call_id)] = dict(payload.arguments)
    indexes: dict[str, ResultIndex] = {}
    received: dict[tuple[str, str], dict[str, JsonValue]] = {}
    for event in events:
        payload = event.payload
        if not isinstance(payload, ToolCalled):
            continue
        key = (event.run_id, payload.call_id)
        if key in replaced:
            received[key] = replaced[key]
            continue
        written = dict(payload.arguments)
        if not payload.refs:
            received[key] = written
            continue
        index = indexes.get(event.run_id)
        if index is None:
            own = [e for e in events if e.run_id == event.run_id]
            index = ResultIndex(fold(own, event.run_id).messages, artifacts)
            indexes[event.run_id] = index
        try:
            received[key], _ = await index.resolve(written)
        except RefError:
            received[key] = written
    return received


def _failure(result: RunResult) -> str | None:
    if result.error_type is None:
        return None
    return f"{result.error_type} : {result.error}" if result.error else result.error_type


async def _export(loom: Loom, result: RunResult, tenant: TenantId, path: Path) -> None:
    """Le journal de la session du run, tel que ``loom sessions export`` l'écrirait."""
    events = await loom.store.read(tenant, result.session_id)
    lines = "".join(f"{event.model_dump_json()}\n" for event in events)
    await asyncio.to_thread(path.write_text, lines, encoding="utf-8")
