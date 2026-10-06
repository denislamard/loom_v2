# SPDX-License-Identifier: Apache-2.0
"""Évaluer un agent : des cas, des attendus, des variantes à comparer (O1, J6.3a).

Une **suite** (YAML) désigne une config et un agent, et porte des **cas** :
une demande, et ce qu'on attend du run qu'elle lance. Chaque cas est joué pour
chaque **variante** — la config telle quelle, une autre config, ou un autre
modèle à une étape de l'agent (``main``, un rôle, ``judge:<nom>``, comme la
variante du rejeu) —, ``repeat`` fois.

Deux sortes d'attendus :

- des **contrôles** déterministes : le statut du run ; son texte (contient,
  ne contient pas, motif) ; un champ de sa sortie structurée ; un outil
  appelé, avec une partie de ses arguments, ou jamais appelé ;
- des **critères** notés par le **juge d'éval**, au format des juges de 3.3
  (``rule``, ``min_score``) : il voit la demande et la réponse finale, et rend
  une note par critère. Il juge **hors du run** : il ne le change pas, et ce
  qu'il coûte est compté à part. Les juges propres à l'agent, eux, tournent
  dans le run comme configurés.

Un cas peut aussi **rejouer des journaux** (``replay: journaux/*.jsonl``,
J6.3b) : chaque run fini de chaque fichier est rejoué à l'identique avec la
config de chaque variante, sans appeler personne, et passe s'il se rejoue tel
quel ; sinon le rapport dit où il s'écarte. Un tel cas n'a ni attendus ni
critères, n'est pas répété, ne dépense rien. Les journaux s'enregistrent avec
``loom eval suite.yaml --export journaux/`` ; un motif sans fichier fait
tomber le cas.

Le monde, pendant une éval (décision du 06/10) : un outil à **effets de
bord** n'est **jamais** exécuté — sa doublure (``doubles``) répond à sa place,
sinon le modèle reçoit une erreur qui le dit. Les autres outils s'exécutent ;
les rôles et les sous-agents tournent. Une approbation est accordée quand rien
ne part pour de vrai (doublure, refus), refusée pour une exécution réelle :
personne n'est là pour trancher.

Ce module décrit la suite, sert les outils et juge les résultats ; la façade
(``Loom.evaluate``, ``loom eval``) monte chaque variante — en mémoire, hors des
quotas et des plafonds de période des clients (``isolated``) — et joue les cas.
"""

import glob
import json
import re
from collections.abc import Mapping, Sequence
from contextlib import aclosing
from dataclasses import dataclass, field
from importlib.util import find_spec
from pathlib import Path
from typing import Annotated, Final, Literal, Self, cast

import yaml
from pydantic import (
    Field,
    JsonValue,
    PositiveFloat,
    PositiveInt,
    ValidationError,
    field_validator,
    model_validator,
)

from loom_ia.config import ConfigError, LoomConfig
from loom_ia.config.errors import from_validation
from loom_ia.config.models import EventsStorage, IdempotencyStorage, StorageConfig
from loom_ia.core.model import (
    CRITERION_NAME_PATTERN,
    DEFAULT_MIN_SCORE,
    ApprovalDecision,
    Approved,
    Budgets,
    Criterion,
    CriterionScore,
    Message,
    ModelRequest,
    ModelSpec,
    PendingApproval,
    PendingCall,
    Quotas,
    Rejected,
    RunId,
    RunStatus,
    TenantBudget,
    TenantId,
    TextBlock,
    ToolOutput,
    Usage,
)
from loom_ia.core.model.base import DomainModel
from loom_ia.core.ports import ModelClient, ModelError
from loom_ia.engine import (
    Answered,
    AnyTool,
    Consumption,
    ModelChain,
    ModelLink,
    PolicyFailure,
    tagged,
)
from loom_ia.guards import JUDGE_SYSTEM, verdict_scores, verdict_tool
from loom_ia.replay.book import Divergence
from loom_ia.replay.variant import Double, doubled

# Un nom de cas ou de variante sert aussi de nom de fichier (``--export``).
NAME_PATTERN: Final = r"^[A-Za-z0-9_.-]{1,64}$"
BASE_VARIANT: Final = "base"
EVAL_JUDGE: Final = "eval"
# Qui tranche une approbation pendant une éval.
EVAL_APPROVER: Final = "éval"
REFUSED: Final = (
    "Non exécuté : éval. L'outil {name} a des effets de bord ({effects}) et la suite ne lui "
    "donne pas de doublure ; il n'est jamais exécuté pendant une éval."
)
NOBODY: Final = "éval : cet appel s'exécuterait pour de vrai, et personne n'est là pour l'approuver"
# Le contrôle d'un run rejoué (J6.3b).
IDENTICAL: Final = "se rejoue à l'identique"

# Ce que devient un appel d'outil pendant une éval.
type EvalFate = Literal["double", "refused", "run"]
_FATES: Final[Mapping[EvalFate, str]] = {
    "double": "remplacé par sa doublure",
    "refused": "non exécuté : effets de bord",
    "run": "exécuté (sans effets de bord)",
}


class EvalError(Exception):
    """Suite impossible à jouer : agent, client, modèle ou doublure inconnus."""


# --- La suite ------------------------------------------------------------------


class EvalCriterion(DomainModel):
    """Un critère noté par le juge d'éval : une règle et son seuil."""

    name: str = Field(pattern=CRITERION_NAME_PATTERN)
    rule: str = Field(min_length=1)
    min_score: float = Field(default=DEFAULT_MIN_SCORE, ge=0.0, le=1.0)

    @property
    def criterion(self) -> Criterion:
        return Criterion(name=self.name, rule=self.rule, min_score=self.min_score)


class ToolExpectation(DomainModel):
    """Un outil que le run doit appeler, avec au moins ces arguments."""

    name: str = Field(min_length=1)
    arguments: dict[str, JsonValue] = Field(default_factory=dict[str, JsonValue])

    @model_validator(mode="before")
    @classmethod
    def _bare(cls, data: object) -> object:
        # ``called: [chercher_devis]`` : un nom seul suffit.
        return {"name": data} if isinstance(data, str) else data


class Expect(DomainModel):
    """Les contrôles déterministes d'un cas."""

    status: RunStatus | None = None
    contains: tuple[str, ...] = ()
    not_contains: tuple[str, ...] = ()
    # Motifs cherchés dans le texte (``re.search``) ; ``(?i)``, ``(?s)`` s'y écrivent.
    matches: tuple[str, ...] = ()
    # Champs de la sortie structurée, par chemin pointé (``lignes.0.total``).
    fields: dict[str, JsonValue] = Field(default_factory=dict[str, JsonValue])
    called: tuple[ToolExpectation, ...] = ()
    not_called: tuple[str, ...] = ()

    @field_validator("matches")
    @classmethod
    def _patterns(cls, patterns: tuple[str, ...]) -> tuple[str, ...]:
        for pattern in patterns:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"motif {pattern!r} invalide : {exc}") from exc
        return patterns

    @property
    def count(self) -> int:
        return (
            (self.status is not None)
            + len(self.contains)
            + len(self.not_contains)
            + len(self.matches)
            + len(self.fields)
            + len(self.called)
            + len(self.not_called)
        )


class EvalCase(DomainModel):
    """Une demande et ce qu'on attend du run qu'elle lance — ou des journaux à rejouer."""

    name: str = Field(pattern=NAME_PATTERN)
    input: Annotated[str, Field(min_length=1)] | None = None
    # Non-régression (J6.3b) : motif des journaux dont chaque run doit se
    # rejouer à l'identique, relatif à la suite (``journaux/*.jsonl``).
    replay: Annotated[str, Field(min_length=1)] | None = None
    # Client au nom duquel le cas est joué ; à défaut, celui de la suite.
    tenant: TenantId | None = None
    expect: Expect = Expect()
    criteria: tuple[EvalCriterion, ...] = ()

    @model_validator(mode="after")
    def _kind(self) -> Self:
        if self.input is None and self.replay is None:
            raise ValueError(
                f"cas {self.name!r} : ni demande ('input') ni journaux à rejouer ('replay')"
            )
        if self.input is not None and self.replay is not None:
            raise ValueError(
                f"cas {self.name!r} : une demande ('input') ou des journaux à rejouer "
                "('replay'), pas les deux"
            )
        if self.replay is not None and (self.expect.count or self.criteria or self.tenant):
            raise ValueError(
                f"cas {self.name!r} : un cas de rejeu n'a ni attendus, ni critères, ni client — "
                "il passe si chaque run de ses journaux se rejoue à l'identique, au nom du "
                "client que le journal porte"
            )
        return self

    @property
    def request(self) -> str:
        """La demande du cas ; un cas de rejeu n'en a pas."""
        if self.input is None:
            raise EvalError(f"cas {self.name!r} : un cas de rejeu n'a pas de demande")
        return self.input


class EvalVariant(DomainModel):
    """Une façon de jouer les cas : la config, une autre, ou d'autres modèles par étape."""

    name: str = Field(pattern=NAME_PATTERN)
    # Autre fichier de config, relatif à la suite.
    config: Path | None = None
    # Étape de l'agent (``main``, un rôle, ``judge:<nom>``) → modèle déclaré.
    models: dict[str, str] = Field(default_factory=dict[str, str])


class EvalJudge(DomainModel):
    """Le juge d'éval : un modèle de la config, et les critères de tous les cas."""

    model: str = Field(min_length=1)
    criteria: tuple[EvalCriterion, ...] = ()
    # Outils dont il voit les résultats, dans l'arbre du run (décision du
    # 06/10) : sans eux, il ne peut juger la réponse que sur la demande.
    tool_results: tuple[str, ...] = ()

    @field_validator("tool_results")
    @classmethod
    def _distinct(cls, names: tuple[str, ...]) -> tuple[str, ...]:
        _unique("outil du juge", names)
        return names


class EvalSuite(DomainModel):
    """Une suite d'évals, telle qu'un fichier YAML la décrit."""

    version: Literal[1] = 1
    # Nom de la suite ; par défaut, celui de son fichier.
    name: str | None = None
    # Config de l'agent, relative à la suite ; à défaut, celle de l'instance.
    config: Path | None = None
    agent: str = Field(min_length=1)
    tenant: TenantId | None = None
    repeat: PositiveInt = 1
    # Plafond de dépense de la suite : runs et juge d'éval. Atteint, les runs
    # suivants ne partent pas, et le rapport le dit.
    max_cost_usd: PositiveFloat | None = None
    # Outil → ``module:fonction`` (relatif à la suite) ou nom enregistré.
    doubles: dict[str, str] = Field(default_factory=dict[str, str])
    judge: EvalJudge | None = None
    variants: tuple[EvalVariant, ...] = ()
    cases: tuple[EvalCase, ...] = Field(min_length=1)
    # Dossier du fichier de la suite, posé au chargement.
    base_dir: Path | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        _unique("cas", [case.name for case in self.cases])
        _unique("variante", [variant.name for variant in self.variants])
        common = self.judge.criteria if self.judge is not None else ()
        _unique("critère du juge", [c.name for c in common])
        for case in self.cases:
            if case.replay is not None:
                continue
            if case.expect.count == 0 and not case.criteria and not common:
                raise ValueError(
                    f"cas {case.name!r} : aucun attendu — un cas sans contrôle ni critère "
                    "n'éprouverait rien"
                )
            if case.criteria and self.judge is None:
                raise ValueError(
                    f"cas {case.name!r} : des critères sans juge — déclarer 'judge: "
                    "{model: …}' dans la suite"
                )
            _unique(f"critère du cas {case.name!r}", [c.name for c in (*common, *case.criteria)])
        return self

    @property
    def title(self) -> str:
        return self.name or "suite"

    def criteria(self, case: EvalCase) -> tuple[Criterion, ...]:
        """Critères notés pour ce cas : ceux du juge, puis les siens ; aucun pour un rejeu."""
        if case.replay is not None:
            return ()
        common = self.judge.criteria if self.judge is not None else ()
        return tuple(c.criterion for c in (*common, *case.criteria))

    def tenant_of(self, case: EvalCase) -> TenantId | None:
        return case.tenant or self.tenant

    def journals(self, case: EvalCase) -> list[Path]:
        """Les journaux d'un cas de rejeu, triés ; son motif est relatif à la suite."""
        if case.replay is None:
            return []
        pattern = Path(case.replay)
        if not pattern.is_absolute() and self.base_dir is not None:
            pattern = self.base_dir / pattern
        found = (Path(name) for name in glob.glob(str(pattern), recursive=True))
        return sorted(path for path in found if path.is_file())

    def played_variants(self) -> tuple[EvalVariant, ...]:
        return self.variants or (EvalVariant(name=BASE_VARIANT),)

    def resolved(self, path: Path | None) -> Path | None:
        """Un chemin de la suite, rapporté à son dossier."""
        if path is None or path.is_absolute() or self.base_dir is None:
            return path
        return self.base_dir / path


def load_suite(path: Path | str) -> EvalSuite:
    """Lit une suite d'évals ; ``ConfigError`` (fichier nommé) si elle ne tient pas."""
    source = Path(path).resolve()
    try:
        data = cast(object, yaml.safe_load(source.read_text(encoding="utf-8")))
    except OSError as exc:
        raise ConfigError(f"suite illisible : {exc}", source=source) from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"YAML invalide : {exc}", source=source) from exc
    if not isinstance(data, dict):
        raise ConfigError("une suite est un objet YAML (agent, cases…)", source=source)
    raw = cast(dict[str, object], data)
    raw.setdefault("name", source.stem)
    try:
        return EvalSuite.model_validate({**raw, "base_dir": source.parent})
    except ValidationError as exc:
        raise from_validation(exc, source=source) from exc


def _unique(what: str, names: Sequence[str]) -> None:
    doubles = sorted({name for name in names if names.count(name) > 1})
    if doubles:
        raise ValueError(f"{what} nommé deux fois : {', '.join(doubles)}")


# --- La config d'une éval --------------------------------------------------------


def isolated(config: LoomConfig, scratch: Path) -> LoomConfig:
    """La config telle qu'une éval la monte : rien ne sort du process, rien n'est décompté.

    Le journal et ses fichiers vivent dans ``scratch``, un dossier temporaire
    que l'éval efface en finissant — en JSONL, parce qu'une approbation exige
    un journal durable (#28), même tranchée en ligne. File et bus en mémoire ;
    pas de sceau, pas de collecteur ; ni quotas ni plafonds de période des
    clients (leurs compteurs ne voient pas l'éval). Un magasin d'idempotence
    partagé devient une base SQLite dans ``scratch`` : ce que demande une clé
    métier, sans toucher à celle du service — ou, sans le paquet ``aiosqlite``,
    le journal de chaque run, ce qui suffit à tout outil sauf à clé métier
    (son montage est alors refusé, en le disant). Le reste — agents, juges,
    contrats, politiques, budgets d'un run et d'une session — est la logique
    évaluée : il ne bouge pas.
    """
    declared = config.storage.idempotency
    if not declared.shared:
        keys = IdempotencyStorage(backend=declared.backend)
    elif find_spec("aiosqlite") is not None:
        keys = IdempotencyStorage(backend="sqlite", path=scratch / "idempotence.db")
    else:
        keys = IdempotencyStorage()
    tenants = tuple(
        tenant.model_copy(
            update={
                "storage": None,
                "quotas": Quotas(),
                "budgets": None if tenant.budgets is None else _without_period(tenant.budgets),
            }
        )
        for tenant in config.tenants
    )
    agents = tuple(
        agent.model_copy(update={"budget": _without_period(agent.budget)})
        if agent.budget is not None
        else agent
        for agent in config.agents
    )
    return config.model_copy(
        update={
            "storage": StorageConfig(
                events=EventsStorage(backend="jsonl", path=scratch / "journal"),
                idempotency=keys,
            ),
            "telemetry": config.telemetry.model_copy(update={"exporters": ()}),
            "budgets": _without_period(config.budgets),
            "tenants": tenants,
            "agents": agents,
            "triggers": (),
        }
    )


def _without_period(budgets: Budgets) -> Budgets:
    """Les budgets sans plafond de période : l'éval ne consomme rien chez le client."""
    return budgets.model_copy(update={"tenant": TenantBudget()})


# --- Les outils pendant une éval ------------------------------------------------------


class EvalTools:
    """Outils d'une éval (``ToolReplay``) : doublés ou refusés s'ils ont des effets de bord."""

    def __init__(self, doubles: Mapping[str, Double] | None = None) -> None:
        self.doubles: dict[str, Double] = dict(doubles or {})
        # Sort de chaque appel, par run et ``call_id`` : décidé une fois.
        self.fates: dict[tuple[str, str], EvalFate] = {}
        self._effects: dict[tuple[str, str], str] = {}

    def serves(self, tool: AnyTool, call: PendingCall, run_id: RunId) -> bool:
        if tool.spec.kind in ("role", "agent"):
            # De la logique, et un agent : ils tournent ; leurs outils passent ici.
            return False
        key = (run_id, call.call_id)
        fate = self.fates.get(key)
        if fate is None:
            if call.name in self.doubles:
                fate = "double"
            elif tool.spec.side_effects != "none":
                fate = "refused"
            else:
                fate = "run"
            self.fates[key] = fate
            self._effects[key] = tool.spec.side_effects
        return fate != "run"

    async def output(
        self,
        run_id: RunId,
        call_id: str,
        name: str,
        arguments: Mapping[str, JsonValue],
        resolved: Mapping[str, JsonValue],
    ) -> tuple[ToolOutput, Consumption | None]:
        key = (run_id, call_id)
        if self.fates[key] == "double":
            return await doubled(self.doubles[name], name, resolved), None
        return ToolOutput.error(REFUSED.format(name=name, effects=self._effects[key])), None

    async def approve(self, run_id: RunId, pending: PendingApproval) -> ApprovalDecision:
        """Accordée quand rien ne part pour de vrai ; refusée sinon."""
        fate = self.fates.get((run_id, pending.call_id))
        if fate is None or fate == "run":
            return Rejected(reason=NOBODY, by=EVAL_APPROVER)
        return Approved(by=EVAL_APPROVER, reason=f"appel {_FATES[fate]} : rien ne part")


def eval_fate_label(fate: EvalFate) -> str:
    return _FATES[fate]


# --- Ce qu'un run a fait, et les contrôles ------------------------------------------


@dataclass(frozen=True, slots=True)
class ToolUse:
    """Un appel d'outil du run (de son arbre), tel qu'il est parti."""

    name: str
    # Ce que l'outil a reçu : références ``$ref`` résolues, puis les arguments
    # qu'une politique ``before_tool`` a mis à la place (décision du 06/10, 6.3c).
    arguments: Mapping[str, JsonValue]
    # Sort pendant l'éval ; ``None`` pour un rôle ou un sous-agent.
    fate: EvalFate | None = None
    # Ce que l'appel a rendu, tel que le journal le garde (``None`` : pas de
    # résultat) — un résultat déporté n'y a que son aperçu.
    result: str | None = None
    is_error: bool = False
    # Les arguments tels que le modèle les a écrits, quand l'outil en a reçu d'autres.
    written: Mapping[str, JsonValue] | None = None


@dataclass(frozen=True, slots=True)
class Outcome:
    """Ce qu'un run d'éval a rendu, de quoi le contrôler."""

    status: RunStatus
    text: str
    data: JsonValue
    error_type: str | None
    tools: tuple[ToolUse, ...] = ()


type CheckKind = Literal["status", "text", "field", "tool", "judge", "replay"]


@dataclass(frozen=True, slots=True)
class CheckResult:
    """Un contrôle ou un critère, et ce qu'il a trouvé."""

    label: str
    passed: bool
    # Ce qui a été trouvé à la place, quand le contrôle tombe ; la note et le
    # motif du juge pour un critère. Un contrôle de texte ne recopie pas le
    # texte : le rapport le donne une fois, en entier, sous le run.
    detail: str = ""
    kind: CheckKind = "status"

    def as_json(self) -> dict[str, JsonValue]:
        return {
            "label": self.label,
            "kind": self.kind,
            "passed": self.passed,
            "detail": self.detail,
        }


def check(expect: Expect, outcome: Outcome) -> list[CheckResult]:
    """Les contrôles déterministes d'un cas, sur ce qu'un run a rendu."""
    results: list[CheckResult] = []
    if expect.status is not None:
        same = outcome.status == expect.status
        results.append(
            CheckResult(f"statut {expect.status.value}", same, "" if same else _status(outcome))
        )
    text = outcome.text
    for wanted in expect.contains:
        results.append(CheckResult(f"contient « {wanted} »", wanted in text, kind="text"))
    for unwanted in expect.not_contains:
        results.append(
            CheckResult(f"ne contient pas « {unwanted} »", unwanted not in text, kind="text")
        )
    for pattern in expect.matches:
        found = re.search(pattern, text) is not None
        results.append(CheckResult(f"correspond à /{pattern}/", found, kind="text"))
    for path, value in expect.fields.items():
        present, actual = _field(outcome.data, path)
        label = f"champ {path} = {_compact(value)}"
        if not present:
            results.append(
                CheckResult(label, False, "absent de la sortie structurée", kind="field")
            )
        else:
            same = actual == value
            detail = "" if same else f"vaut {_compact(actual)}"
            results.append(CheckResult(label, same, detail, kind="field"))
    for wanted_tool in expect.called:
        label = f"appelle {wanted_tool.name}" + (
            f" avec {_compact(wanted_tool.arguments)}" if wanted_tool.arguments else ""
        )
        same_name = [use for use in outcome.tools if use.name == wanted_tool.name]
        hit = any(_contains(use.arguments, wanted_tool.arguments) for use in same_name)
        detail = ""
        if not hit:
            detail = (
                "arguments reçus : " + " ; ".join(_received(use) for use in same_name)
                if same_name
                else _calls(outcome.tools)
            )
        results.append(CheckResult(label, hit, detail, kind="tool"))
    for unwanted_tool in expect.not_called:
        count = sum(1 for use in outcome.tools if use.name == unwanted_tool)
        results.append(
            CheckResult(
                f"n'appelle pas {unwanted_tool}",
                count == 0,
                f"appelé {count} fois" if count else "",
                kind="tool",
            )
        )
    return results


def judged(scores: Sequence[CriterionScore]) -> list[CheckResult]:
    """Les critères du juge d'éval, chacun réussi si sa note atteint son seuil."""
    return [
        CheckResult(
            f"juge : {score.name} ≥ {_score(score.min_score)}",
            score.passed,
            f"note {_score(score.score)} — {score.reason}",
            kind="judge",
        )
        for score in scores
    ]


def unjudged(criteria: Sequence[Criterion], reason: str) -> list[CheckResult]:
    """Les critères d'un run que le juge n'a pas pu noter : ils tombent, en le disant."""
    return [
        CheckResult(f"juge : {c.name} ≥ {_score(c.min_score)}", False, reason, kind="judge")
        for c in criteria
    ]


def _status(outcome: Outcome) -> str:
    said = outcome.status.value
    return f"{said} ({outcome.error_type})" if outcome.error_type else said


def _field(data: JsonValue, path: str) -> tuple[bool, JsonValue]:
    """La valeur au chemin pointé ``path`` de la sortie structurée, si elle y est."""
    node = data
    for step in path.split("."):
        if isinstance(node, dict) and step in node:
            node = node[step]
        elif isinstance(node, list) and step.isdigit() and int(step) < len(node):
            node = node[int(step)]
        else:
            return False, None
    return True, node


def _contains(actual: Mapping[str, JsonValue], wanted: Mapping[str, JsonValue]) -> bool:
    """Vrai si chaque argument attendu est là, avec cette valeur (objets comparés en partie)."""
    for key, value in wanted.items():
        if key not in actual:
            return False
        found = actual[key]
        if isinstance(value, dict) and isinstance(found, dict):
            if not _contains(found, value):
                return False
        elif found != value:
            return False
    return True


def _received(use: ToolUse) -> str:
    said = _compact(dict(use.arguments))
    return said if use.written is None else f"{said} (écrits : {_compact(dict(use.written))})"


def _calls(tools: Sequence[ToolUse]) -> str:
    if not tools:
        return "aucun appel d'outil"
    return "appels : " + ", ".join(use.name for use in tools)


def _compact(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(", ", ": "))


def _score(value: float) -> str:
    return f"{value:.2f}"


# --- Le juge d'éval -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Judgment:
    """Ce que le juge d'éval a rendu : les notes, ou pourquoi il n'en a pas."""

    scores: tuple[CriterionScore, ...] = ()
    error: str | None = None
    usage: Usage = field(default_factory=Usage)
    cost_usd: float = 0.0


class EvalJudgeClient:
    """Le juge d'éval : un modèle de la config, appelé hors du run, après lui."""

    def __init__(self, spec: ModelSpec, client: ModelClient) -> None:
        self.spec = spec
        self.client = client

    async def judge(
        self,
        criteria: tuple[Criterion, ...],
        request: str,
        outcome: Outcome,
        tool_results: Sequence[str] = (),
    ) -> Judgment:
        """Les notes de la réponse finale ; une erreur du juge ne lève pas, elle se dit.

        Le juge voit les critères, la demande, les résultats des outils nommés
        (``tool_results``, chaque appel de l'arbre du run, erreurs comprises)
        et la réponse finale.
        """
        if not criteria:
            return Judgment()
        output = outcome.text or (_compact(outcome.data) if outcome.data is not None else "")
        rules = "\n".join(f"- {c.name} : {c.rule}" for c in criteria)
        sections = [tagged("criteria", rules), tagged("user_input", request)]
        for name in tool_results:
            found = [use for use in outcome.tools if use.name == name and use.result is not None]
            if not found:
                sections.append(tagged("tool_result", "(aucun résultat dans ce run)", tool=name))
            for use in found:
                attributes = {"tool": name, **({"status": "erreur"} if use.is_error else {})}
                sections.append(tagged("tool_result", use.result or "", **attributes))
        sections.append(tagged("output", output or "(aucune réponse)"))
        chain = ModelChain(links=(ModelLink(self.spec, self.client),), slot=f"judge:{EVAL_JUDGE}")
        message = Message(role="user", blocks=(TextBlock(text="\n\n".join(sections)),))
        request_ = chain.request_for(
            self.spec,
            ModelRequest(
                model_id=self.spec.model,
                system=JUDGE_SYSTEM,
                messages=(message,),
                tools=(verdict_tool(criteria),),
                tool_choice="required",
            ),
        )
        answered: Answered | None = None
        try:
            async with aclosing(chain.run(request_)) as outcomes:
                async for item in outcomes:
                    if isinstance(item, Answered):
                        answered = item
        except ModelError as exc:
            return Judgment(error=f"juge d'éval en échec : model.{exc.kind} — {exc.message}")
        if answered is None:
            return Judgment(error="juge d'éval : appel terminé sans réponse")
        usage = answered.response.usage
        cost = answered.spec.pricing.cost(usage)
        try:
            scores = verdict_scores(answered.response.message, criteria, judge=EVAL_JUDGE)
        except PolicyFailure as exc:
            return Judgment(error=str(exc), usage=usage, cost_usd=cost)
        return Judgment(scores=scores, usage=usage, cost_usd=cost)

    async def aclose(self) -> None:
        await self.client.aclose()


# --- Le rapport --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EvalRun:
    """Un cas joué une fois pour une variante, et ce que ses contrôles en disent."""

    case: str
    variant: str
    # Répétition, à partir de 1.
    attempt: int
    run_id: RunId | None = None
    status: RunStatus | None = None
    text: str = ""
    # Un run en échec : son type et son message.
    failure: str | None = None
    checks: tuple[CheckResult, ...] = ()
    usage: Usage = field(default_factory=Usage)
    # Ce qu'a coûté le run (sous-agents et juges de l'agent compris)…
    cost_usd: float = 0.0
    # … et le juge d'éval, à part.
    judge_cost_usd: float = 0.0
    active_ms: float = 0.0
    tools: tuple[ToolUse, ...] = ()
    # Le run n'a pas pu être lancé ou s'est interrompu : pourquoi.
    error: str | None = None
    # Non joué (plafond de dépense atteint) : pourquoi.
    skipped: str | None = None
    # Cas de rejeu (J6.3b) : le journal du run, rapporté à la suite, et là où
    # le rejeu s'en écarte.
    journal: str | None = None
    divergence: Divergence | None = None

    @property
    def passed(self) -> bool:
        return self.skipped is None and self.error is None and all(c.passed for c in self.checks)

    @property
    def spent_usd(self) -> float:
        return self.cost_usd + self.judge_cost_usd

    def as_json(self) -> dict[str, JsonValue]:
        return {
            "case": self.case,
            "variant": self.variant,
            "attempt": self.attempt,
            "run_id": self.run_id,
            "status": None if self.status is None else self.status.value,
            "passed": self.passed,
            "text": self.text,
            "failure": self.failure,
            "checks": [c.as_json() for c in self.checks],
            "usage": self.usage.model_dump(mode="json"),
            "cost_usd": self.cost_usd,
            "judge_cost_usd": self.judge_cost_usd,
            "active_ms": self.active_ms,
            "tools": [
                {
                    "name": t.name,
                    "arguments": dict(t.arguments),
                    "written": None if t.written is None else dict(t.written),
                    "fate": t.fate,
                }
                for t in self.tools
            ],
            "error": self.error,
            "skipped": self.skipped,
            "journal": self.journal,
            "divergence": None
            if self.divergence is None
            else {
                "kind": self.divergence.kind,
                "where": self.divergence.where,
                "detail": self.divergence.detail,
                "parts": list(self.divergence.parts),
                "rank": self.divergence.rank,
            },
        }


@dataclass(frozen=True, slots=True)
class VariantSummary:
    """Une variante, cas par cas : combien de répétitions passent, et ce qu'elle a coûté."""

    name: str
    # Cas → (répétitions qui passent, répétitions jouées).
    cases: Mapping[str, tuple[int, int]]
    skipped: int
    cost_usd: float
    judge_cost_usd: float

    @property
    def passed_cases(self) -> int:
        return sum(1 for ok, played in self.cases.values() if played and ok == played)


@dataclass(frozen=True, slots=True)
class EvalReport:
    """Ce qu'une suite a trouvé, variante par variante."""

    suite: str
    agent: str
    repeat: int
    # Variante → ce qui la distingue : sa config, ses modèles par étape.
    variants: Mapping[str, Mapping[str, JsonValue]]
    cases: tuple[str, ...]
    runs: tuple[EvalRun, ...]
    max_cost_usd: float | None = None
    judge_model: str | None = None
    # Cas de rejeu (J6.3b) : leurs runs viennent de leurs journaux.
    replays: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        """Tout a été joué, et tout passe."""
        return bool(self.runs) and all(run.passed for run in self.runs)

    @property
    def spent_usd(self) -> float:
        return sum(run.spent_usd for run in self.runs)

    @property
    def skipped(self) -> int:
        return sum(1 for run in self.runs if run.skipped is not None)

    def summary(self, variant: str) -> VariantSummary:
        runs = [run for run in self.runs if run.variant == variant]
        cases: dict[str, tuple[int, int]] = {}
        for case in self.cases:
            played = [r for r in runs if r.case == case and r.skipped is None]
            cases[case] = (sum(1 for r in played if r.passed), len(played))
        return VariantSummary(
            name=variant,
            cases=cases,
            skipped=sum(1 for r in runs if r.skipped is not None),
            cost_usd=sum(r.cost_usd for r in runs),
            judge_cost_usd=sum(r.judge_cost_usd for r in runs),
        )

    def as_json(self) -> dict[str, JsonValue]:
        return {
            "suite": self.suite,
            "agent": self.agent,
            "repeat": self.repeat,
            "passed": self.passed,
            "spent_usd": self.spent_usd,
            "max_cost_usd": self.max_cost_usd,
            "judge_model": self.judge_model,
            "replays": list(self.replays),
            "variants": {
                name: {
                    **dict(described),
                    "passed_cases": self.summary(name).passed_cases,
                    "cases": {
                        case: {"passed": ok, "played": played}
                        for case, (ok, played) in self.summary(name).cases.items()
                    },
                    "skipped": self.summary(name).skipped,
                    "cost_usd": self.summary(name).cost_usd,
                    "judge_cost_usd": self.summary(name).judge_cost_usd,
                }
                for name, described in self.variants.items()
            },
            "runs": [run.as_json() for run in self.runs],
        }


# --- Le rapport, en texte ------------------------------------------------------------------

_INDENT: Final = "  "


def render_eval(report: EvalReport) -> list[str]:
    """Les lignes de ``loom eval`` : variante par variante, cas par cas, puis le bilan."""
    variants = len(report.variants)
    lines = [
        f"Suite      : {report.suite} — agent {report.agent}, {len(report.cases)} cas, "
        f"{variants} variante(s), {report.repeat} répétition(s) par cas",
    ]
    if report.judge_model is not None:
        lines.append(f"Juge       : {report.judge_model}")
    if report.max_cost_usd is not None:
        lines.append(f"Plafond    : {report.max_cost_usd:.4f} $")
    for name, described in report.variants.items():
        summary = report.summary(name)
        lines += [
            "",
            f"Variante {name} ({_variant(described)}) — {summary.passed_cases}/"
            f"{len(summary.cases)} cas réussi(s), {summary.cost_usd:.6f} $ de runs, "
            f"{summary.judge_cost_usd:.6f} $ de juge",
        ]
        for case in report.cases:
            runs = [r for r in report.runs if r.variant == name and r.case == case]
            lines += _replayed(case, runs) if case in report.replays else _case(case, runs)
    played = [r for r in report.runs if r.skipped is None]
    lines += [
        "",
        f"Bilan      : {sum(1 for r in played if r.passed)}/{len(played)} run(s) réussi(s), "
        f"{report.spent_usd:.6f} $ dépensé(s)"
        + (f", {report.skipped} run(s) non joué(s)" if report.skipped else ""),
        "Verdict    : "
        + (
            "tout passe"
            if report.passed
            else "des attendus tombent, ou des runs n'ont pas été joués"
        ),
    ]
    return lines


def _variant(described: Mapping[str, JsonValue]) -> str:
    said: list[str] = []
    if described.get("config"):
        said.append(f"config {described['config']}")
    models = described.get("models")
    if isinstance(models, dict) and models:
        said.append(", ".join(f"{step}={model}" for step, model in models.items()))
    return " ; ".join(said) or "config de base"


def _case(case: str, runs: Sequence[EvalRun]) -> list[str]:
    played = [r for r in runs if r.skipped is None]
    passed = sum(1 for r in played if r.passed)
    if not played:
        reason = runs[0].skipped if runs else "aucun run"
        return [f"{_INDENT}non joué  {case} : {reason}"]
    mark = "ok       " if passed == len(played) else "ÉCHEC    "
    lines = [f"{_INDENT}{mark}{case} ({passed}/{len(played)})"]
    pad = _INDENT * 3
    for run in runs:
        if run.skipped is not None:
            lines.append(f"{pad}essai {run.attempt} non joué : {run.skipped}")
            continue
        if run.passed:
            continue
        if run.error is not None:
            lines.append(f"{pad}essai {run.attempt} : le run n'est pas allé au bout — {run.error}")
            continue
        status = run.status.value if run.status is not None else "?"
        head = f"{pad}essai {run.attempt} : run {run.run_id}, {status}"
        lines.append(head + (f" ({run.failure})" if run.failure else ""))
        failed = [c for c in run.checks if not c.passed]
        for result in failed:
            lines.append(
                f"{pad}{_INDENT}✗ {result.label}" + (f" — {result.detail}" if result.detail else "")
            )
        if any(c.kind == "text" for c in failed):
            text = run.text or "(vide)"
            lines.append(f"{pad}{_INDENT}texte :")
            lines += [f"{pad}{_INDENT * 2}{line}" for line in text.splitlines() or [""]]
    return lines


def _replayed(case: str, runs: Sequence[EvalRun]) -> list[str]:
    """Un cas de rejeu : combien de runs se rejouent à l'identique, et où les autres s'écartent."""
    replayed = [r for r in runs if r.run_id is not None and r.error is None]
    passed = sum(1 for r in replayed if r.passed)
    mark = "ok       " if runs and all(r.passed for r in runs) else "ÉCHEC    "
    missed = len(runs) - len(replayed)
    lines = [
        f"{_INDENT}{mark}{case} ({passed}/{len(replayed)} run(s) rejoué(s) à l'identique"
        + (f", {missed} non rejoué(s)" if missed else "")
        + ")"
    ]
    pad = _INDENT * 3
    for run in runs:
        if run.passed:
            continue
        named = (run.journal, None if run.run_id is None else f"run {run.run_id}")
        where = ", ".join(part for part in named if part)
        if run.error is not None:
            lines.append(f"{pad}{where + ' : ' if where else ''}{run.error}")
            continue
        lines.append(f"{pad}{where} :")
        lines += [
            f"{pad}{_INDENT}✗ {c.label}" + (f" — {c.detail}" if c.detail else "")
            for c in run.checks
            if not c.passed
        ]
    return lines
