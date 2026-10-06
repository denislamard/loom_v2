# SPDX-License-Identifier: Apache-2.0
"""Un agent au banc d'essai : sa config, des faux modèles, des faux outils (O2, J6.3c).

    async with Bench("loom.yaml", models={"M3_MAIN": ScriptedModel(...)},
                     tools={"envoyer_email": faux_envoi}) as banc:
        result = await banc.run("relance", "Relance le client du devis D-2026-042.")
        banc.expect(result, status="completed", called=[{"name": "envoyer_email"}])

Le banc monte la config **à part**, comme une éval (``isolated``) : journal et
fichiers dans un dossier temporaire, ni collecteur, ni quotas, ni plafonds de
période ; rien n'est écrit là où la config range ses données. Le monde y suit
les règles des évals :

- un **modèle** se remplace par son identifiant (``models``), par n'importe
  quel client de modèle — ``ScriptedModel`` d'abord. Un modèle simulé de la
  config (``sdk: fake``) sert tel quel ; un modèle réel qui n'est pas remplacé
  est **refusé** à son premier appel, sauf ``real_models=True`` : un essai ne
  dépense rien par mégarde ;
- un **outil** se remplace par son nom (``tools``), par une fonction qui
  reçoit ce que l'outil aurait reçu et répond comme lui. Un outil à effets de
  bord sans faux n'est jamais exécuté : le modèle reçoit une erreur qui le
  dit. Un outil sans effets s'exécute ; rôles et sous-agents tournent ;
- une **approbation** est accordée quand rien ne part (faux, refus), refusée
  pour une exécution réelle.

``expect`` reprend les contrôles d'un cas d'éval et lève ``AssertionError``
avec chacun de ceux qui tombent ; ``calls`` dit ce que chaque outil a reçu et
rendu ; ``export`` écrit le journal d'un run, prêt pour ``assert_replays``.
"""

import asyncio
import tempfile
from collections.abc import AsyncGenerator, Mapping, Sequence
from pathlib import Path
from types import TracebackType
from typing import Self

from loom_ia.access import Loom, RunResult
from loom_ia.access.api import doubles_problem
from loom_ia.access.evals import run_outcome
from loom_ia.config import ConfigError, LoomConfig, load_config
from loom_ia.core.events import Event
from loom_ia.core.model import (
    DEFAULT_TENANT,
    ModelChunk,
    ModelRequest,
    ModelSpec,
    RunId,
    SessionId,
    TenantId,
)
from loom_ia.core.ports import ModelClient, ModelError
from loom_ia.engine import RunContext
from loom_ia.replay import (
    CheckResult,
    Double,
    EvalTools,
    Expect,
    Outcome,
    ToolUse,
    check,
    isolated,
)

# Le modèle d'un adaptateur sans réseau ni clé : il sert tel quel au banc.
SIMULATED = "fake"


class BenchError(Exception):
    """Banc mal monté : un faux qui ne vise aucun outil de l'agent, ou qui vise un rôle."""


class RefusedModel:
    """Un modèle réel que le banc n'appelle pas : son premier appel lève, en le disant.

    Le run échoue en ``model.auth``, son erreur dit comment autoriser l'appel ;
    le moteur le note sans le crier (``by_client``) : c'est le banc qui refuse,
    pas le fournisseur qui tombe.
    """

    def __init__(self, spec: ModelSpec) -> None:
        self.spec = spec

    @property
    def provider(self) -> str:
        return self.spec.sdk

    async def stream(self, request: ModelRequest) -> AsyncGenerator[ModelChunk]:
        raise ModelError(
            "auth",
            f"Banc : le modèle {self.spec.id} ({self.spec.sdk}, {self.spec.model}) est réel et "
            "n'est pas remplacé — le remplacer (models={…}), ou l'appeler pour de vrai "
            "(real_models=True)",
            by_client=True,
        )
        yield  # pragma: no cover — un générateur, pour respecter le protocole

    async def aclose(self) -> None:
        pass


class Bench:
    """Un agent de la config, monté à part, ses modèles et ses outils remplacés à la demande."""

    def __init__(
        self,
        config: LoomConfig | Path | str,
        *,
        models: Mapping[str, ModelClient] | None = None,
        tools: Mapping[str, Double] | None = None,
        register: Mapping[str, object] | None = None,
        environ: Mapping[str, str] | None = None,
        real_models: bool = False,
        profile: str | None = None,
    ) -> None:
        self.config = (
            config if isinstance(config, LoomConfig) else load_config(config, profile=profile)
        )
        declared = {spec.id: spec for spec in self.config.models}
        unknown = sorted(set(models or {}) - set(declared))
        if unknown:
            raise ConfigError(
                f"Banc : modèles remplacés mais non déclarés : {', '.join(unknown)} "
                f"(modèles : {', '.join(sorted(declared)) or 'aucun'})"
            )
        self._models: dict[str, ModelClient] = dict(models or {})
        if not real_models:
            for spec in declared.values():
                if spec.id not in self._models and spec.sdk != SIMULATED:
                    self._models[spec.id] = RefusedModel(spec)
        self._fakes: dict[str, Double] = dict(tools or {})
        self._register = dict(register or {})
        self._environ = environ
        self._tools = EvalTools(self._fakes)
        self._scratch: tempfile.TemporaryDirectory[str] | None = None
        self._loom: Loom | None = None
        self._checked: set[tuple[str, TenantId | None]] = set()
        self._outcomes: dict[RunId, Outcome] = {}
        self._tenants: dict[RunId, TenantId] = {}

    # --- Monter et démonter -----------------------------------------------------

    async def __aenter__(self) -> Self:
        scratch = tempfile.TemporaryDirectory(prefix="loom-banc-")
        try:
            loom = Loom(
                isolated(self.config, Path(scratch.name)),
                environ=self._environ,
                intercept=self._tools,
                models=self._models,
            )
            await loom.__aenter__()
        except BaseException:
            scratch.cleanup()
            raise
        self._scratch, self._loom = scratch, loom
        for name, obj in self._register.items():
            loom.register(name, obj)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            if self._loom is not None:
                await self._loom.__aexit__(exc_type, exc, tb)
        finally:
            self._loom = None
            if self._scratch is not None:
                self._scratch.cleanup()
                self._scratch = None

    @property
    def loom(self) -> Loom:
        """L'instance du banc, pour ce que le banc ne dit pas lui-même."""
        if self._loom is None:
            raise RuntimeError("Banc : à utiliser dans `async with Bench(…)`")
        return self._loom

    # --- Faire tourner -----------------------------------------------------------

    async def run(
        self,
        agent: str,
        message: str,
        *,
        tenant: TenantId | None = None,
        session_id: SessionId | None = None,
    ) -> RunResult:
        """Lance un run et attend sa fin ; ce que ses outils ont reçu et rendu est gardé.

        Chaque faux doit viser un outil de l'agent ou de ses sous-agents, jamais
        un rôle : vérifié au premier run de chaque agent (``BenchError``).
        ``session_id`` enchaîne les tours d'une conversation.
        """
        loom = self.loom
        self._check(loom, agent, tenant)
        result = await loom.run(agent, message, tenant=tenant, session_id=session_id)
        tenant_id = tenant or DEFAULT_TENANT
        events = await loom.events(result.run_id, session_id=result.session_id, tenant_id=tenant_id)
        self._outcomes[result.run_id] = await run_outcome(
            result, events, self._tools, loom.artifacts
        )
        self._tenants[result.run_id] = tenant_id
        return result

    def _check(self, loom: Loom, agent: str, tenant: TenantId | None) -> None:
        if not self._fakes or (agent, tenant) in self._checked:
            return

        def context_of(name: str) -> RunContext:
            return loom.context(name, tenant)

        problem = doubles_problem(loom.config, agent, self._fakes, context_of)
        if problem is not None:
            raise BenchError(f"Banc, agent {agent} : {problem.replace('doublure', 'faux')}")
        self._checked.add((agent, tenant))

    # --- Ce qu'un run a fait ---------------------------------------------------------

    def outcome(self, result: RunResult) -> Outcome:
        """Ce qu'un run du banc a rendu, et chaque appel d'outil de son arbre."""
        found = self._outcomes.get(result.run_id)
        if found is None:
            raise BenchError(f"Banc : le run {result.run_id} n'a pas été lancé par ce banc")
        return found

    def calls(self, name: str | None = None, result: RunResult | None = None) -> list[ToolUse]:
        """Les appels d'outil des runs du banc (ou de l'un d'eux), dans l'ordre, filtrés par nom.

        Chacun dit ce que l'outil a reçu (références résolues, arguments d'une
        politique), ce qu'il a écrit si c'est autre chose, son sort — faussé,
        refusé, exécuté — et son résultat.
        """
        outcomes = [self.outcome(result)] if result is not None else self._outcomes.values()
        return [use for o in outcomes for use in o.tools if name is None or use.name == name]

    def expect(self, result: RunResult, **expect: object) -> list[CheckResult]:
        """Les contrôles d'un cas d'éval sur ce run ; ``AssertionError`` si l'un tombe.

        ``status``, ``contains``, ``not_contains``, ``matches``, ``fields``,
        ``called`` (avec une partie des arguments que l'outil a reçus),
        ``not_called``. Le message dit chaque contrôle tombé et ce qu'il a
        trouvé à la place, et le texte du run quand un contrôle de texte tombe.
        """
        wanted = Expect.model_validate(expect)
        if wanted.count == 0:
            raise ValueError("Banc : expect sans attendu — rien ne serait éprouvé")
        outcome = self.outcome(result)
        results = check(wanted, outcome)
        failed = [r for r in results if not r.passed]
        if failed:
            raise AssertionError(_said(result, outcome, results, failed))
        return results

    async def events(self, result: RunResult) -> list[Event]:
        """Les événements du run et de ses sous-runs, dans l'ordre du journal."""
        return await self.loom.events(
            result.run_id, session_id=result.session_id, tenant_id=self._tenant_of(result)
        )

    async def export(self, result: RunResult, path: Path | str) -> Path:
        """Écrit le journal de la session du run en JSONL — de quoi le rejouer
        (``assert_replays``)."""
        events = await self.loom.store.read(self._tenant_of(result), result.session_id)
        target = Path(path)
        lines = "".join(f"{event.model_dump_json()}\n" for event in events)
        await asyncio.to_thread(target.write_text, lines, encoding="utf-8")
        return target

    def _tenant_of(self, result: RunResult) -> TenantId:
        self.outcome(result)
        return self._tenants[result.run_id]


def _said(
    result: RunResult,
    outcome: Outcome,
    results: Sequence[CheckResult],
    failed: Sequence[CheckResult],
) -> str:
    status = outcome.status.value + (f" ({outcome.error_type})" if outcome.error_type else "")
    lines = [
        f"Banc : {len(failed)} contrôle(s) tombé(s) sur {len(results)}, "
        f"run {result.run_id} ({status})"
    ]
    lines += [f"  ✗ {r.label}" + (f" — {r.detail}" if r.detail else "") for r in failed]
    if any(r.kind == "text" for r in failed):
        lines.append("  texte :")
        lines += [f"    {line}" for line in (outcome.text or "(vide)").splitlines() or [""]]
    return "\n".join(lines)
