# SPDX-License-Identifier: Apache-2.0
"""Les commandes de la console : contrôlées, endossées, puis passées aux méthodes de ``Loom`` (docs/analyse-exortateur-loom.md, § 6.6).

Le canal n'ajoute aucune capacité : chaque commande appelle la méthode de
``Loom`` que l'API REST appelle, au nom d'un utilisateur loom que la passerelle
endosse (``act_as``, ``act_as_roles``). Une commande n'a donc jamais plus de
droits que cet utilisateur.

Contrôles, dans l'ordre :

1. une passerelle déclarée (``console.commands.user``), sinon ``commands_disabled`` ;
2. une commande déjà vue rend **le même résultat**, sans rejouer l'action ;
3. le débit (``console.commands.rate`` par minute), sinon ``rate_limited`` ;
4. l'échéance : passée, ``expired`` ; trop lointaine (``max_ttl``), ``ttl_too_long`` ;
5. l'endossement de ``as_user`` ;
6. les arguments de la commande, puis ses droits, vérifiés par ``Loom`` (et
   pour les lectures, ici, comme le fait le REST).

Un refus de droits est écrit au journal d'audit, par l'accès ``console``. Chaque
commande tourne dans sa propre tâche : une lecture longue (un fichier) ne
retient ni les acquittements ni les autres commandes.
"""

import asyncio
import base64
import hashlib
import logging
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final, Literal

from pydantic import AwareDatetime, Field, JsonValue, ValidationError

from loom_ia.access import audit
from loom_ia.access.api import ApprovalFileRefused
from loom_ia.access.authz import AccessDenied, Actor
from loom_ia.access.caller import Caller
from loom_ia.config.console import ConsoleConfig
from loom_ia.config.models import Permission
from loom_ia.console.channel import ConsoleChannel
from loom_ia.console.contract import CommandPayload, CommandResultPayload, FileDataPayload
from loom_ia.console.translate import public_event
from loom_ia.core.events import EVENTS_LIMIT, EventCategory, EventQuery, EventStatus
from loom_ia.core.model import DomainModel, EventId, RunId, SessionId, Unknown
from loom_ia.tenancy import BudgetExhausted, QuotaExceeded

if TYPE_CHECKING:
    from loom_ia.access.api import Loom

logger = logging.getLogger(__name__)

QUERY_MAX: Final = 1_000
"""Événements rendus par ``events.query``, au plus : la réponse tient en un message."""

SESSIONS_LIMIT: Final = 1_000
"""Sessions rendues par ``sessions.list`` sans ``limit``."""

SESSIONS_MAX: Final = 5_000
"""Sessions rendues par ``sessions.list``, au plus : la réponse tient en un message."""

_READS: Final = frozenset({"events.query", "approval.get", "file.get", "approval.file", "approval.preview", "sessions.list"})
"""Les lectures : rejouées, elles se refont (rien n'est à protéger d'un double effet)."""

_REFUSALS: Final = (AccessDenied, Unknown, LookupError, ValueError, BudgetExhausted, QuotaExceeded, PermissionError)
"""Ce qu'une commande refusée lève : un refus dit à la console, jamais une erreur du canal."""


class _Args(DomainModel):
    """Arguments d'une commande : un champ inconnu est refusé, comme dans la config."""


class RunStartArgs(_Args):
    """``run.start`` : lance un run, en arrière-plan (``Loom.submit``).

    Attributes:
        agent: L'agent.
        message: La demande.
        run_id: Identifiant du run, fixé par la console : rejouée, la commande ne
            relance rien (D-10).
        session_id: Session où lancer le run ; à défaut, la sienne.
        state: State de session posé par l'application.
    """

    agent: str
    message: str = Field(min_length=1)
    run_id: RunId
    session_id: SessionId | None = None
    state: dict[str, JsonValue] | None = None


class QuestionAnswerArgs(_Args):
    """``question.answer`` : répond à une question ``loom.ask`` (``Loom.answer``)."""

    run_id: RunId
    session_id: SessionId | None = None
    call_id: str | None = None
    answers: tuple[tuple[str, ...], ...] | None = None
    text: str | None = None


class ApprovalDecideArgs(_Args):
    """``approval.decide`` : accorde ou refuse un appel (``Loom.approve`` / ``Loom.reject``)."""

    run_id: RunId
    decision: Literal["approve", "reject"]
    session_id: SessionId | None = None
    call_id: str | None = None
    reason: str = ""
    arguments: dict[str, JsonValue] | None = None


class RunCancelArgs(_Args):
    """``run.cancel`` : arrête un run (``Loom.cancel``)."""

    run_id: RunId
    session_id: SessionId | None = None


class EventsQueryArgs(_Args):
    """``events.query`` : cherche des événements, comme ``GET /v1/events`` (``Loom.query``) ; le client est celui de la console."""

    session_id: SessionId | None = None
    run_id: RunId | None = None
    types: tuple[str, ...] = ()
    categories: tuple[EventCategory, ...] = ()
    status: tuple[EventStatus, ...] = ()
    agent: str | None = None
    tool_name: str | None = None
    since: AwareDatetime | None = None
    until: AwareDatetime | None = None
    after: EventId | None = None
    limit: int = Field(default=EVENTS_LIMIT, ge=1, le=QUERY_MAX)


class SessionsListArgs(_Args):
    """``sessions.list`` : les sessions du client, de la plus récemment écrite à la plus ancienne, comme ``GET /v1/sessions``.

    Attributes:
        limit: Sessions rendues, au plus.
        after: Le jeton ``next`` de la page précédente ; absent, la première page.
    """

    limit: int = Field(default=SESSIONS_LIMIT, ge=1, le=SESSIONS_MAX)
    after: str | None = None


class ApprovalGetArgs(_Args):
    """``approval.get`` : le détail des demandes en attente d'un run (§ 6.7)."""

    run_id: RunId
    session_id: SessionId | None = None
    call_id: str | None = None


class FileGetArgs(_Args):
    """``file.get`` : un fichier rangé dans une session, décrit par son journal (``Loom.session_file``, cas 1)."""

    session_id: SessionId
    file: str


class ApprovalFileArgs(_Args):
    """``approval.file`` : un fichier qu'une demande en attente désigne (``Loom.approval_file``, cas 2)."""

    run_id: RunId
    call_id: str
    path: str
    session_id: SessionId | None = None


class ApprovalPreviewArgs(_Args):
    """``approval.preview`` : le fichier qu'un appel en attente écrirait (``Loom.approval_preview``, cas 3)."""

    run_id: RunId
    call_id: str
    session_id: SessionId | None = None


class CommandRefused(Exception):
    """Une commande refusée avant d'atteindre ``Loom`` ; le motif est un code court, puis ce qu'il en est."""

    def __init__(self, code: str, said: str = "") -> None:
        super().__init__(f"{code} : {said}" if said else code)
        self.code = code


class CommandExecutor:
    """Contrôle chaque commande de la console, l'exécute au nom de son utilisateur, et rend son résultat.

    Args:
        loom: L'instance, dont les méthodes font le travail.
        channel: La connexion, où partent les résultats et les fichiers.
        console: La section ``console`` (passerelle, bornes, fichiers, client).
        clock: L'heure, remplacée dans les essais.
    """

    def __init__(self, loom: Loom, channel: ConsoleChannel, console: ConsoleConfig, *, clock: Callable[[], datetime] | None = None) -> None:
        self._loom = loom
        self._channel = channel
        self._console = console
        self._clock = clock or (lambda: datetime.now(UTC))
        self._seen: dict[str, tuple[datetime, CommandResultPayload]] = {}
        self._running: dict[str, asyncio.Task[CommandResultPayload]] = {}
        self._recent: deque[datetime] = deque()
        self._tasks: set[asyncio.Task[None]] = set()
        # Ce qui part après le résultat : les morceaux d'un fichier (``file.data``).
        self._after: dict[str, Callable[[], Awaitable[None]]] = {}

    def receive(self, command: CommandPayload) -> None:
        """Prend une commande reçue, sans retenir la réception : elle tourne dans sa propre tâche."""
        task = asyncio.get_running_loop().create_task(self._serve(command), name=f"loom-console-command-{command.command_id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def aclose(self) -> None:
        """Annule les commandes encore en cours."""
        for task in tuple(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.wait(tuple(self._tasks))

    async def _serve(self, command: CommandPayload) -> None:
        try:
            result = await self.execute(command)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Console : commande %s (%s) en erreur", command.command_id, command.name)
            result = CommandResultPayload(command_id=command.command_id, outcome="error", reason="internal")
        follow = self._after.pop(command.command_id, None)
        if not await self._channel.send(result):
            logger.warning("Console : résultat de la commande %s non envoyé (connexion fermée)", command.command_id)
            return
        if follow is not None and result.outcome == "accepted":
            await follow()

    async def execute(self, command: CommandPayload) -> CommandResultPayload:
        """Contrôle et exécute une commande ; rend son résultat, que la console reçoit.

        Une commande déjà vue rend le résultat de la première fois ; une commande
        encore en cours sous le même identifiant attend la première.
        """
        self._forget_expired()
        if command.name in _READS:
            return await self._decided(command)
        known = self._seen.get(command.command_id)
        if known is not None:
            return known[1]
        running = self._running.get(command.command_id)
        if running is not None:
            return await asyncio.shield(running)
        task = asyncio.get_running_loop().create_task(self._decided(command))
        self._running[command.command_id] = task
        try:
            result = await asyncio.shield(task)
        finally:
            self._running.pop(command.command_id, None)
        self._seen[command.command_id] = (command.expires_at, result)
        return result

    async def _decided(self, command: CommandPayload) -> CommandResultPayload:
        identifier = command.command_id
        with audit.through("console", self._loom.config, audit.Aim(action=f"console {command.name}")):
            try:
                actor = self._admitted(command)
                result = await self._dispatch(command, actor)
            except CommandRefused as refusal:
                return CommandResultPayload(command_id=identifier, outcome="refused", reason=str(refusal))
            except ValidationError as error:
                said = "; ".join(f"{'.'.join(map(str, item['loc']))} : {item['msg']}" for item in error.errors())
                return CommandResultPayload(command_id=identifier, outcome="refused", reason=f"invalid_args : {said}")
            except ApprovalFileRefused as error:
                return CommandResultPayload(command_id=identifier, outcome="refused", reason=str(error))
            except _REFUSALS as error:
                if isinstance(error, AccessDenied):
                    audit.refused(error, access="console", action=f"console {command.name}")
                return CommandResultPayload(command_id=identifier, outcome="refused", reason=f"{type(error).__name__} : {error}")
        return CommandResultPayload(command_id=identifier, outcome="accepted", result=result)

    def _admitted(self, command: CommandPayload) -> Actor:
        """Les contrôles avant ``Loom`` : passerelle, débit, échéance, endossement."""
        gateway = self._console.commands.user
        if gateway is None:
            raise CommandRefused("commands_disabled", "console.commands.user n'est pas déclaré")
        now = self._clock()
        window = now - timedelta(minutes=1)
        while self._recent and self._recent[0] < window:
            self._recent.popleft()
        if len(self._recent) >= self._console.commands.rate:
            raise CommandRefused("rate_limited", f"{self._console.commands.rate} commandes par minute au plus")
        self._recent.append(now)
        if command.expires_at <= now:
            raise CommandRefused("expired", f"échéance passée ({command.expires_at.isoformat()})")
        if command.expires_at - now > timedelta(seconds=self._console.commands.max_ttl):
            raise CommandRefused("ttl_too_long", f"échéance au-delà de {self._console.commands.max_ttl} s")
        security = self._loom.config.security
        return Actor.of(security, gateway).endorse(security, command.as_user)

    async def _dispatch(self, command: CommandPayload, actor: Actor) -> JsonValue:
        loom, tenant = self._loom, actor.tenant
        match command.name:
            case "run.start":
                start = RunStartArgs.model_validate(command.args)
                run_id = await loom.submit(
                    start.agent, start.message, session_id=start.session_id, run_id=start.run_id, state=start.state, tenant=tenant, user=actor
                )
                return {"run_id": run_id}
            case "question.answer":
                reply = QuestionAnswerArgs.model_validate(command.args)
                answers = None if reply.answers is None else [list(answer) for answer in reply.answers]
                call = await loom.answer(
                    reply.run_id, answers=answers, text=reply.text, call_id=reply.call_id, session_id=reply.session_id, tenant_id=tenant, user=actor
                )
                return {"call_id": call}
            case "approval.decide":
                decision = ApprovalDecideArgs.model_validate(command.args)
                if decision.decision == "approve":
                    calls = await loom.approve(
                        decision.run_id,
                        call_id=decision.call_id,
                        reason=decision.reason,
                        arguments=decision.arguments,
                        session_id=decision.session_id,
                        tenant_id=tenant,
                        user=actor,
                    )
                else:
                    calls = await loom.reject(
                        decision.run_id, call_id=decision.call_id, reason=decision.reason, session_id=decision.session_id, tenant_id=tenant, user=actor
                    )
                return {"calls": list(calls)}
            case "run.cancel":
                cancel = RunCancelArgs.model_validate(command.args)
                return {"cancelled": await loom.cancel(cancel.run_id, session_id=cancel.session_id, tenant_id=tenant, user=actor)}
            case "events.query":
                return await self._query(EventsQueryArgs.model_validate(command.args), actor)
            case "sessions.list":
                return await self._sessions(SessionsListArgs.model_validate(command.args), actor)
            case "approval.get":
                return await self._approvals(ApprovalGetArgs.model_validate(command.args), actor)
            case "file.get":
                return await self._file(command, FileGetArgs.model_validate(command.args), actor)
            case "approval.file":
                cited = ApprovalFileArgs.model_validate(command.args)
                found = await loom.approval_file(
                    cited.run_id, cited.call_id, cited.path, session_id=cited.session_id, tenant_id=tenant, user=actor, max_bytes=self._console.files.max_size
                )
                return self._serve_bytes(command, found.name, found.media_type, found.data)
            case "approval.preview":
                asked = ApprovalPreviewArgs.model_validate(command.args)
                rendered = await loom.approval_preview(
                    asked.run_id,
                    asked.call_id,
                    session_id=asked.session_id,
                    tenant_id=tenant,
                    user=actor,
                    max_bytes=self._console.files.max_size,
                    within=self._console.files.preview_timeout,
                )
                served = self._serve_bytes(command, rendered.name, rendered.media_type, rendered.data)
                return {**served, "warnings": list(rendered.warnings)}

    # --- Lectures : les droits que le REST vérifie, au nom de l'utilisateur endossé --------------

    async def _query(self, args: EventsQueryArgs, actor: Actor) -> JsonValue:
        caller = Caller(actor=actor)
        _require(caller, "read")
        if args.agent is not None:
            _require(caller, "read", args.agent)
        query = EventQuery(tenant_id=actor.tenant, **args.model_dump(exclude_none=True))
        events = await self._loom.query(query)
        for agent in dict.fromkeys(event.agent for event in events if event.agent):
            _require(caller, "read", agent)
        mode = "on_demand" if caller.masks else "push"
        found: list[JsonValue] = [public_event(event, mode=mode) for event in events]
        return {"events": found, "next": events[-1].event_id if len(events) == args.limit else None}

    async def _sessions(self, args: SessionsListArgs, actor: Actor) -> JsonValue:
        """Une page de sessions ; le jeton ``next`` est la position de la dernière, ``None`` quand il n'y en a plus."""
        caller = Caller(actor=actor)
        _require(caller, "read")
        if actor.key is not None and actor.key.agents:
            # Comme le REST : la liste ne dit pas de quels agents sont les runs d'une session.
            raise AccessDenied(
                f"La clé {actor.key.id} est bornée à certains agents : la liste des sessions ne peut pas être filtrée", actor=actor, permission="read"
            )
        after = None if args.after is None else _position(args.after)
        records = sorted(await self._loom.sessions(tenant_id=actor.tenant), key=lambda record: (record.updated_at, record.session_id), reverse=True)
        remaining = [record for record in records if after is None or (record.updated_at, record.session_id) < after]
        page = remaining[: args.limit]
        found: list[JsonValue] = [record.model_dump(mode="json") for record in page]
        last = page[-1] if page else None
        more = last is not None and len(remaining) > len(page)
        return {"sessions": found, "next": f"{last.updated_at.isoformat()}|{last.session_id}" if more and last is not None else None}

    async def _approvals(self, args: ApprovalGetArgs, actor: Actor) -> JsonValue:
        details = await self._loom.approval_details(args.run_id, call_id=args.call_id, session_id=args.session_id, tenant_id=actor.tenant, user=actor)
        if not details:
            raise CommandRefused("nothing_pending", f"aucune demande en attente pour le run {args.run_id}")
        return {"approvals": [detail.model_dump(mode="json") for detail in details]}

    async def _file(self, command: CommandPayload, args: FileGetArgs, actor: Actor) -> JsonValue:
        caller = Caller(actor=actor)
        _require(caller, "read_content")
        described, data = await self._loom.session_file(args.session_id, args.file, tenant_id=actor.tenant)
        if described.agent is not None:
            _require(caller, "read_content", described.agent)
        return self._serve_bytes(command, described.name or described.file, described.media_type, data)

    def _serve_bytes(self, command: CommandPayload, name: str, media_type: str, data: bytes) -> dict[str, JsonValue]:
        """Annonce un fichier dans le résultat ; ses morceaux (``file.data``) partiront après lui.

        Raises:
            CommandRefused: ``too_large``, au-delà de ``console.files.max_size``.
        """
        files = self._console.files
        if len(data) > files.max_size:
            raise CommandRefused("too_large", f"{len(data)} octets, {files.max_size} au plus (console.files.max_size)")
        digest = hashlib.sha256(data).hexdigest()
        parts = max(1, -(-len(data) // files.chunk_size))

        async def send_parts() -> None:
            # Après le résultat : ``accepted`` annonce le fichier, puis ses morceaux suivent,
            # chacun un message — les lots d'événements passent entre eux.
            for part in range(parts):
                first = part == 0
                payload = FileDataPayload(
                    command_id=command.command_id,
                    part=part,
                    last=part == parts - 1,
                    data=base64.b64encode(data[part * files.chunk_size : (part + 1) * files.chunk_size]).decode("ascii"),
                    name=name if first else None,
                    media_type=media_type if first else None,
                    size=len(data) if first else None,
                    sha256=digest if first else None,
                )
                if not await self._channel.send(payload):
                    logger.warning("Console : fichier de la commande %s interrompu au morceau %d (connexion fermée)", command.command_id, part)
                    return

        self._after[command.command_id] = send_parts
        return {"name": name, "media_type": media_type, "size": len(data), "sha256": digest, "parts": parts}

    def _forget_expired(self) -> None:
        now = self._clock()
        for identifier in [identifier for identifier, (expires, _) in self._seen.items() if expires <= now]:
            del self._seen[identifier]


def _position(token: str) -> tuple[datetime, str]:
    """La position d'un jeton ``next`` de ``sessions.list`` : l'heure de la dernière écriture et la session.

    Raises:
        ValueError: Jeton illisible.
    """
    stamp, separator, session = token.partition("|")
    if not separator or not session:
        raise ValueError(f"jeton de page illisible : {token!r}")
    try:
        written = datetime.fromisoformat(stamp)
    except ValueError:
        raise ValueError(f"jeton de page illisible : {token!r}") from None
    if written.tzinfo is None:
        raise ValueError(f"jeton de page illisible : {token!r}")
    return written, session


def _require(caller: Caller, permission: Permission, agent: str | None = None) -> None:
    """La permission, comme ``access/http/auth.require`` : sinon ``AccessDenied``, que l'accès audite."""
    if not caller.may(permission) or (agent is not None and not caller.allows(agent, permission)):
        raise AccessDenied(caller.lacking(permission, agent), actor=caller.actor, permission=permission, agent=agent)
