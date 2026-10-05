# SPDX-License-Identifier: Apache-2.0
"""Ce qu'un run a reçu du monde, relu dans son journal pour le lui resservir (K6, #31).

Le rejeu identique refait tourner la logique de loom — boucle, politiques,
contrats, juges, budgets — et lui sert, au lieu du monde, ce que le journal a
gardé : les réponses des modèles, les résultats des outils, les décisions
d'approbation. Le **livre** (``ReplayBook``) tient ces réponses et note la
première divergence :

- un appel de modèle est servi par son **empreinte** (``request_hash``) : une
  requête qui n'en a aucune au journal diverge, et le livre dit quelle partie
  diffère quand le journal porte ``request_parts`` (6.2a) ;
- un appel d'outil est servi par son ``call_id`` — au rejeu identique, les
  réponses du modèle sont celles du journal, donc ses appels aussi —, et son
  nom et ses arguments doivent être les mêmes ;
- une demande d'approbation reçoit la décision du journal.

Au rejeu identique, après la première divergence, plus rien n'est servi :
continuer obligerait à deviner quelle réponse enregistrée irait à quelle
requête nouvelle. En variante (J6.2b, ``replay.variant``), le même livre sert
toute requête identique, avant comme après, et ce qu'il ne connaît pas part
pour de vrai ; un outil s'y retrouve par son nom et ses arguments.
"""

from collections import deque
from collections.abc import AsyncGenerator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final, Literal

from pydantic import JsonValue

from loom_ia.core.events import (
    ApprovalExpired,
    ApprovalGranted,
    ApprovalRejected,
    Event,
    ModelFellBack,
    ModelResponded,
    RunStarted,
    ToolCalled,
    ToolCompleted,
)
from loom_ia.core.model import (
    ApprovalDecision,
    Approved,
    ModelChunk,
    ModelRequest,
    ModelResponse,
    ModelSpec,
    PendingApproval,
    PendingCall,
    Rejected,
    RunId,
    ToolOutput,
    Usage,
    message_to_chunks,
)
from loom_ia.core.ports import AnsweringClient, ModelError
from loom_ia.engine import AnyTool, Consumption

type DivergenceKind = Literal["model", "tool", "approval", "end"]

# Noms des parties d'une requête, dans l'ordre où le diagnostic les cite.
PARTS: Final = ("model", "system", "tools", "messages", "settings")
_PART_LABELS: Final[Mapping[str, str]] = {
    "model": "le modèle",
    "system": "le prompt système",
    "tools": "les outils proposés",
    "messages": "les messages",
    "settings": "les réglages (choix d'outil, plafond de tokens, paramètres, schéma de sortie)",
}
# Fournisseur de chaque SDK, tel qu'un adaptateur l'écrit dans ``model.responded``.
_PROVIDERS: Final[Mapping[str, str]] = {
    "fake": "fake",
    "anthropic": "anthropic",
    "openai": "openai",
}
STOPPED: Final = "rejeu arrêté : une divergence a été trouvée plus haut"


class ReplayError(ValueError):
    """Ce run ne peut pas être rejoué (introuvable, inachevé, sous-run, variante mal dite…)."""


@dataclass(frozen=True, slots=True)
class Divergence:
    """Le premier endroit où le rejeu ne retrouve pas le journal."""

    kind: DivergenceKind
    # Ce qui diverge, en une phrase : l'appel et sa place.
    where: str
    # Ce qui diffère, quand on peut le dire.
    detail: str = ""
    # Parties de la requête qui diffèrent (appel de modèle, journal récent).
    parts: tuple[str, ...] = ()
    expected_hash: str | None = None
    actual_hash: str | None = None


@dataclass
class _Response:
    """Une réponse de modèle du journal, et sa place."""

    seq: int
    run_id: str
    role: str | None
    payload: ModelResponded
    served: bool = False


@dataclass
class ToolCallRecord:
    """Un appel d'outil du journal, et ce que le rejeu en a fait."""

    run_id: str
    called: ToolCalled
    completed: ToolCompleted | None = None
    served: bool = False
    # Variante : promis à un appel du rejeu, qui le servira (``match_tool``).
    reserved: bool = False


@dataclass
class ReplayBook:
    """Les réponses du monde à un run, et ce qu'on en a resservi."""

    responses: list[_Response] = field(default_factory=list[_Response])
    # Par run et ``call_id`` : un identifiant d'appel n'est unique que dans son run.
    tools: dict[tuple[str, str], ToolCallRecord] = field(
        default_factory=dict[tuple[str, str], ToolCallRecord]
    )
    approvals: dict[tuple[str, str], ApprovalGranted | ApprovalRejected | ApprovalExpired] = field(
        default_factory=dict[tuple[str, str], ApprovalGranted | ApprovalRejected | ApprovalExpired]
    )
    fallbacks: list[ModelFellBack] = field(default_factory=list[ModelFellBack])
    divergence: Divergence | None = None
    # Variante : sous-runs lus au journal (leur appel servi tel quel) ; leurs
    # propres appels n'ont pas à être refaits.
    read_runs: set[str] = field(default_factory=set[str])
    # Variante : ce qui est vraiment parti aux modèles.
    real_calls: int = 0
    real_usage: Usage = field(default_factory=Usage)
    spent_usd: float = 0.0
    _by_hash: dict[str, deque[_Response]] = field(default_factory=dict[str, deque[_Response]])
    _children: dict[str, list[str]] = field(default_factory=dict[str, list[str]])

    @classmethod
    def of(cls, events: Sequence[Event]) -> ReplayBook:
        """Le livre d'un run (ou de son arbre, en variante), tiré de ses événements."""
        book = cls()
        for event in events:
            match event.payload:
                case ModelResponded() as responded:
                    entry = _Response(
                        seq=event.seq, run_id=event.run_id, role=event.role, payload=responded
                    )
                    book.responses.append(entry)
                    book._by_hash.setdefault(responded.request_hash, deque()).append(entry)
                case ToolCalled() as called:
                    book.tools[event.run_id, called.call_id] = ToolCallRecord(
                        run_id=event.run_id, called=called
                    )
                case ToolCompleted() as completed if (
                    event.run_id,
                    completed.call_id,
                ) in book.tools:
                    book.tools[event.run_id, completed.call_id].completed = completed
                case ApprovalGranted() | ApprovalRejected() | ApprovalExpired() as decided:
                    book.approvals[event.run_id, decided.call_id] = decided
                case ModelFellBack() as fell:
                    book.fallbacks.append(fell)
                case RunStarted(parent_run_id=parent) if parent is not None:
                    book._children.setdefault(parent, []).append(event.run_id)
                case _:
                    pass
        return book

    @property
    def diverged(self) -> bool:
        return self.divergence is not None

    @property
    def served_responses(self) -> int:
        return sum(1 for r in self.responses if r.served)

    @property
    def journal_tools(self) -> int:
        """Appels d'outil que le journal sert : tous, sauf les rôles."""
        return sum(1 for t in self.tools.values() if _servable(t))

    @property
    def served_tools(self) -> int:
        return sum(1 for t in self.tools.values() if t.served)

    @property
    def unserved_responses(self) -> list[_Response]:
        return [r for r in self.responses if not r.served and r.run_id not in self.read_runs]

    @property
    def unserved_tools(self) -> list[ToolCallRecord]:
        """Appels servis par le journal au run d'origine et pas rejoués.

        Un rôle n'est jamais servi par le journal — il tourne — : il n'en fait
        pas partie, ses appels de modèle se comptent avec les réponses. Les
        appels d'un sous-run lu tel quel non plus (variante).
        """
        return [
            t
            for t in self.tools.values()
            if not t.served and _servable(t) and t.run_id not in self.read_runs
        ]

    def read_through(self, record: ToolCallRecord) -> None:
        """Le sous-run de cet appel, et les siens, sont lus au journal, pas refaits."""
        waiting = [record.called.child_run_id] if record.called.child_run_id else []
        while waiting:
            run_id = waiting.pop()
            self.read_runs.add(run_id)
            waiting += self._children.get(run_id, [])

    def diverge(self, divergence: Divergence) -> None:
        """Note une divergence ; seule la première compte."""
        if self.divergence is None:
            self.divergence = divergence

    # --- Modèles ------------------------------------------------------------

    def answer(self, request: ModelRequest) -> ModelResponded:
        """La réponse du journal à cette requête ; ``ModelError`` sinon (rejeu identique).

        L'erreur est un arrêt voulu (``by_client``) : le moteur ne la crie pas,
        c'est le rejeu qui dit sa divergence.
        """
        if self.diverged:
            raise ModelError("invalid_request", STOPPED, by_client=True)
        digest = request.request_hash()
        found = self._take(digest)
        if found is not None:
            return found
        self.diverge(self._model_divergence(request, digest, exact=True))
        assert self.divergence is not None
        raise ModelError("invalid_request", f"rejeu : {self.divergence.where}", by_client=True)

    def known(self, request: ModelRequest) -> ModelResponded | None:
        """Variante : la réponse du journal à une requête identique, avant comme après
        la divergence ; ``None`` si le journal ne la connaît pas — elle partira pour de vrai.
        """
        digest = request.request_hash()
        found = self._take(digest)
        if found is None:
            self.diverge(self._model_divergence(request, digest, exact=False))
        return found

    def paid(self, cost_usd: float, usage: Usage) -> None:
        """Variante : un appel parti pour de vrai, et ce qu'il a coûté."""
        self.real_calls += 1
        self.real_usage = self.real_usage + usage
        self.spent_usd += cost_usd

    def _take(self, digest: str) -> ModelResponded | None:
        waiting = self._by_hash.get(digest)
        while waiting and waiting[0].served:
            waiting.popleft()
        if not waiting:
            return None
        entry = waiting.popleft()
        entry.served = True
        return entry.payload

    def _model_divergence(self, request: ModelRequest, digest: str, *, exact: bool) -> Divergence:
        pending = self.unserved_responses
        rank = len(self.responses) - len(pending) + 1
        if not pending:
            return Divergence(
                kind="model",
                where=f"appel de modèle n°{rank} : le run d'origine n'a pas fait cet appel",
                detail=f"{len(self.responses)} appel(s) au journal, tous déjà rejoués",
                actual_hash=digest,
            )
        expected = pending[0]
        role = expected.role or "main"
        where = f"appel de modèle n°{rank} ({role} au journal) : la requête a changé"
        recorded = expected.payload.request_parts
        if not recorded:
            detail = (
                "empreinte différente ; ce journal est antérieur aux empreintes par partie "
                "(6.2a), la partie qui diffère ne peut pas être dite"
            )
            changed: tuple[str, ...] = ()
        else:
            now = request.request_parts()
            changed = tuple(part for part in PARTS if recorded.get(part) != now.get(part))
            said = [_PART_LABELS[part] for part in changed]
            detail = "ce qui diffère : " + (", ".join(said) if said else "rien de visible")
            if "messages" in changed:
                detail += (
                    f" ({recorded.get('messages_count', '?')} message(s) au journal, "
                    f"{now['messages_count']} maintenant)"
                )
        if self.fallbacks and exact:
            detail += (
                " — le run d'origine avait basculé sur un secours "
                f"({', '.join(f'{f.from_model} → {f.to_model}' for f in self.fallbacks)}), "
                "ce que le rejeu identique ne reproduit pas"
            )
        return Divergence(
            kind="model",
            where=where,
            detail=detail,
            parts=changed,
            expected_hash=expected.payload.request_hash,
            actual_hash=digest,
        )

    # --- Outils -------------------------------------------------------------

    def match_tool(self, name: str, arguments: Mapping[str, JsonValue]) -> ToolCallRecord | None:
        """Variante : le premier appel du journal de même nom et mêmes arguments, pas encore
        promis ni servi, dans l'ordre du journal ; il est promis à l'appelant.
        """
        wanted = dict(arguments)
        for record in self.tools.values():
            if (
                not record.served
                and not record.reserved
                and _servable(record)
                and record.called.tool_name == name
                and record.called.arguments == wanted
            ):
                record.reserved = True
                return record
        return None

    def recorded_output(self, record: ToolCallRecord) -> tuple[ToolOutput, Consumption | None]:
        """Le résultat enregistré d'un appel, et la consommation qu'il portait."""
        completed = record.completed
        assert completed is not None
        record.served = True
        consumption = (
            Consumption(usage=completed.usage, cost_usd=completed.cost_usd)
            if completed.usage is not None
            else None
        )
        return completed.output, consumption

    def tool_output(
        self, run_id: str, call_id: str, name: str, arguments: Mapping[str, JsonValue]
    ) -> tuple[ToolOutput, Consumption | None]:
        if self.diverged:
            return ToolOutput.error(STOPPED), None
        recorded = self.tools.get((run_id, call_id))
        if recorded is None or recorded.completed is None:
            self.diverge(
                Divergence(
                    kind="tool",
                    where=f"appel d'outil {name} ({call_id}) : absent du journal"
                    if recorded is None
                    else f"appel d'outil {name} ({call_id}) : sans résultat au journal",
                )
            )
            return ToolOutput.error(f"rejeu : appel {name} absent du journal"), None
        called = recorded.called
        if called.tool_name != name or called.arguments != dict(arguments):
            self.diverge(
                Divergence(
                    kind="tool",
                    where=f"appel d'outil {name} ({call_id}) : il diffère du journal",
                    detail=(
                        f"outil {called.tool_name!r} au journal"
                        if called.tool_name != name
                        else "mêmes outil et identifiant, autres arguments"
                    ),
                )
            )
            return ToolOutput.error(f"rejeu : appel {name} différent du journal"), None
        return self.recorded_output(recorded)

    # --- Approbations -------------------------------------------------------

    def recorded_decision(self, run_id: str, call_id: str) -> ApprovalDecision | None:
        """La décision d'approbation que le journal porte pour cet appel de ce run."""
        match self.approvals.get((run_id, call_id)):
            case ApprovalGranted(by=by, reason=reason, arguments=arguments):
                return Approved(by=by, reason=reason, arguments=arguments)
            case ApprovalRejected(by=by, reason=reason):
                return Rejected(reason=reason, by=by)
            case ApprovalExpired():
                return Rejected(reason="demande expirée (au journal)", by="rejeu")
            case None:
                return None

    async def approve(self, run_id: str, pending: PendingApproval) -> ApprovalDecision:
        """La décision du journal, rendue en ligne : le rejeu ne s'arrête pas pour attendre."""
        decided = self.recorded_decision(run_id, pending.call_id)
        match decided:
            case Approved() | Rejected():
                return decided
            case None:
                self.diverge(
                    Divergence(
                        kind="approval",
                        where=(
                            f"demande d'approbation de {pending.tool_name} "
                            f"({pending.call_id}) : absente du journal"
                        ),
                    )
                )
                return Rejected(reason="rejeu : demande absente du journal", by="rejeu")


def recorded_response(recorded: ModelResponded) -> ModelResponse:
    """La réponse telle que le journal l'a gardée, entière."""
    return ModelResponse(
        model_id=recorded.model_id,
        provider=recorded.provider,
        message=recorded.message,
        usage=recorded.usage,
        stop_reason=recorded.stop_reason,
    )


def provider_of(spec: ModelSpec) -> str:
    """Fournisseur d'un modèle, tel que son adaptateur l'écrirait."""
    return _PROVIDERS.get(spec.sdk, spec.sdk)


def _servable(record: ToolCallRecord) -> bool:
    """Un appel que le journal peut servir : fini, et pas un rôle (qui tourne)."""
    return record.completed is not None and record.called.tool_kind != "role"


class ReplayModelClient(AnsweringClient):
    """Client de modèle dont les réponses sont celles du journal."""

    def __init__(self, book: ReplayBook, spec: ModelSpec) -> None:
        self.book = book
        self.spec = spec

    @property
    def provider(self) -> str:
        return provider_of(self.spec)

    async def answer(self, request: ModelRequest) -> ModelResponse:
        return recorded_response(self.book.answer(request))

    async def stream(self, request: ModelRequest) -> AsyncGenerator[ModelChunk]:
        """Pour qui lirait le flux : les morceaux de la réponse du journal."""
        response = await self.answer(request)
        for chunk in message_to_chunks(
            response.message, usage=response.usage, stop_reason=response.stop_reason
        ):
            yield chunk

    async def aclose(self) -> None:
        pass


class JournalTools:
    """Résultats d'outils servis depuis le journal (``ToolReplay``)."""

    def __init__(self, book: ReplayBook) -> None:
        self.book = book

    def serves(self, tool: AnyTool, call: PendingCall, run_id: RunId) -> bool:
        # Un rôle est de la logique : il tourne, et son modèle rejoue.
        return tool.spec.kind != "role"

    async def approve(self, run_id: RunId, pending: PendingApproval) -> ApprovalDecision:
        """La décision du journal, rendue en ligne."""
        return await self.book.approve(run_id, pending)

    async def output(
        self, run_id: RunId, call_id: str, name: str, arguments: Mapping[str, JsonValue]
    ) -> tuple[ToolOutput, Consumption | None]:
        return self.book.tool_output(run_id, call_id, name, arguments)
