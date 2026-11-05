# SPDX-License-Identifier: Apache-2.0
"""Les refus d'accès, journalisés (D-9, docs/loom_role_user.md § 7).

Un refus ne change rien au run — il n'y en a parfois même pas : un lancement
refusé, une clé inconnue. Il n'a donc pas sa place dans le journal de session,
qui raconte les runs, et un appelant refusé ne doit pas pouvoir y écrire. Il
reste à savoir qu'il a eu lieu : chaque refus donne **une** ligne WARNING sur
le logger ``loom_ia.access.audit``, qui dit qui (utilisateur, passerelle,
clé), quoi (action, permission, agent, run, appel), par quel accès et chez quel
client, et pourquoi.

Où la ligne s'écrit :

- un refus de droits (``AccessDenied``) s'écrit là où il **quitte** ``Loom``
  (``audited``) : ses méthodes qui agissent, pour tous les accès à la fois ;
  ``can``, qui répond sans agir, n'écrit rien ;
- ce que ``Loom`` ne voit pas s'écrit à l'accès : clé absente, inconnue ou
  expirée (401), débit d'une clé (429), restriction d'une clé vérifiée avant le
  SDK, ``as_user`` refusé, CLI sans utilisateur. Un ``AccessDenied`` déjà
  écrit ne l'est pas deux fois (``AccessDenied.audited``) ;
- une décision sur tout un run (sans ``call_id``) qui réussit pour une partie
  des demandes écrit un refus par demande laissée de côté.

Chaque accès pose le sien à son entrée (``through``, ``entering``) : un refus
sorti du SDK dit ainsi s'il est venu par REST, MCP ou la CLI ; ``sdk`` sinon.
Il y pose aussi ce que la requête vise, quand il le sait — la route ou l'outil
(``action``), l'agent, le run : un refus décidé par l'accès, avant le SDK, le
dit sans que chaque contrôle ait à le répéter. Ce qu'un refus nomme lui-même
l'emporte.

Le format ``json`` des logs met le détail sous ``audit`` ; le format console
le met en fin de ligne. Une commande de la CLI qui affiche elle-même le refus
écarte la ligne de son terminal (``TOLD``) : le refus se lit une fois. Ces
logs suivent ``telemetry.logging`` (un niveau au-dessus de WARNING les tait).

Ce qui dure, c'est le fichier ``security.audit.path`` : chaque refus y ajoute
une ligne JSON — ``ts`` puis le détail, sans le message —, quel que soit le
niveau des logs, et quel que soit le processus qui refuse : l'écriture se fait
sous verrou ``flock``. Chaque accès dit où écrire à son entrée (``through``,
``entering``, ``writing_to``) ; une méthode de ``Loom`` le dit elle-même, pour
le SDK appelé directement. ``loom retention`` en efface les lignes plus
vieilles que ``security.audit.retention_days`` (``sweep``).
"""

import fcntl
import functools
import inspect
import json
import logging
from collections.abc import AsyncGenerator, Callable, Coroutine, Generator, Mapping
from contextlib import aclosing, contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final, Literal, NamedTuple

from loom_ia.access.authz import AccessDenied
from loom_ia.config import LoomConfig
from loom_ia.core import AUDIT, TOLD

# Logger des refus : à router à part (fichier, collecteur) pour en garder la trace.
AUDIT_LOGGER: Final = "loom_ia.access.audit"

type Access = Literal["sdk", "rest", "mcp", "cli"]

logger = logging.getLogger(AUDIT_LOGGER)

# L'accès par lequel la requête en cours est arrivée : posé à l'entrée de chaque
# accès (``through``), lu par le refus qui sort de ``Loom``. Le SDK appelé
# directement n'en pose aucun.
_ACCESS: ContextVar[Access] = ContextVar("loom_access", default="sdk")
# Le fichier où la requête en cours écrit ses refus (``security.audit.path``).
_FILE: ContextVar[Path | None] = ContextVar("loom_audit_file", default=None)


class Aim(NamedTuple):
    """Ce que la requête en cours vise, tel que l'accès le sait à son entrée."""

    # La route (``POST /v1/runs/…/approve``) ou l'outil MCP.
    action: str | None = None
    agent: str | None = None
    run_id: str | None = None


# Un tuple nommé ne se modifie pas : un seul défaut partagé ne risque rien.
_NOWHERE: Final = Aim()
_AIM: ContextVar[Aim] = ContextVar("loom_audit_aim", default=_NOWHERE)


@contextmanager
def through(
    access: Access, config: LoomConfig | None = None, aim: Aim | None = None
) -> Generator[None]:
    """Tout refus écrit dans ce bloc dit qu'il est arrivé par ``access``, et ce qu'il visait.

    Avec ``config``, il va aussi à son fichier d'audit.
    """
    token = _ACCESS.set(access)
    kept = _FILE.set(_path(config)) if config is not None else None
    aimed = _AIM.set(aim) if aim is not None else None
    try:
        yield
    finally:
        if aimed is not None:
            _AIM.reset(aimed)
        if kept is not None:
            _FILE.reset(kept)
        _ACCESS.reset(token)


def entering(access: Access, config: LoomConfig | None = None, aim: Aim | None = None) -> None:
    """Pose l'accès de la requête en cours, pour la tâche qui la sert (une route REST)."""
    _ACCESS.set(access)
    if aim is not None:
        _AIM.set(aim)
    if config is not None:
        writing_to(config)


def writing_to(config: LoomConfig) -> None:
    """Les refus qui suivent vont au fichier de ``config`` : une commande, sa config lue."""
    _FILE.set(_path(config))


def denied(
    reason: str,
    *,
    access: Access | None = None,
    action: str | None = None,
    user: str | None = None,
    via: str | None = None,
    key: str | None = None,
    permission: str | None = None,
    agent: str | None = None,
    tenant: str | None = None,
    run_id: str | None = None,
    call_id: str | None = None,
) -> None:
    """Écrit un refus : une ligne WARNING, son détail à part, et une ligne au fichier
    d'audit ; les champs absents sont omis.

    Sans ``access``, celui de la requête en cours (``through``), ``sdk`` à défaut ;
    sans ``action``, ``agent`` ou ``run_id``, ce que la requête vise (``Aim``).
    """
    aim = _AIM.get()
    run_id = run_id or aim.run_id
    detail: dict[str, str] = {
        name: value
        for name, value in (
            ("access", access or _ACCESS.get()),
            ("action", action or aim.action),
            ("user", user),
            ("via", via),
            ("key", key),
            ("permission", permission),
            ("agent", agent or aim.agent),
            ("tenant_id", tenant),
            ("run_id", run_id),
            ("call_id", call_id),
            ("reason", reason),
        )
        if value is not None
    }
    logger.warning(
        "Accès refusé — %s",
        reason,
        extra={AUDIT: detail, "tenant_id": tenant, "run_id": run_id, TOLD: True},
    )
    path = _FILE.get()
    if path is not None:
        _write(path, detail)


def refused(
    error: AccessDenied,
    *,
    access: Access | None = None,
    action: str | None = None,
    agent: str | None = None,
    tenant: str | None = None,
    run_id: str | None = None,
    call_id: str | None = None,
) -> None:
    """Écrit un refus de droits, une seule fois quel que soit le nombre de couches traversées."""
    if error.audited:
        return
    error.audited = True
    denied(
        str(error),
        access=access,
        action=action,
        user=error.user,
        via=error.via,
        key=error.key,
        permission=error.permission,
        agent=error.agent or agent,
        tenant=error.tenant or tenant,
        run_id=run_id,
        call_id=call_id,
    )


def audited[**P, R](
    method: Callable[P, Coroutine[Any, Any, R]],
) -> Callable[P, Coroutine[Any, Any, R]]:
    """Une méthode de ``Loom`` qui agit : un refus qui en sort est écrit, puis relevé."""
    signature = inspect.signature(method)

    @functools.wraps(method)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        with _owned_by(args):
            try:
                return await method(*args, **kwargs)
            except AccessDenied as error:
                _where(signature, args, kwargs).refused(error, method.__name__)
                raise

    return wrapper


def audited_stream[**P, T](
    method: Callable[P, AsyncGenerator[T]],
) -> Callable[P, AsyncGenerator[T]]:
    """``audited``, pour une méthode qui rend un flux (``Loom.stream``)."""
    signature = inspect.signature(method)

    @functools.wraps(method)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> AsyncGenerator[T]:
        try:
            # Fermé avec le flux : un appelant qui s'arrête en route referme aussi l'original.
            async with aclosing(method(*args, **kwargs)) as items:
                async for item in items:
                    yield item
        except AccessDenied as error:
            with _owned_by(args):
                _where(signature, args, kwargs).refused(error, method.__name__)
            raise

    return wrapper


class _Where(NamedTuple):
    """L'agent, le client, le run et l'appel visés, tels que l'appel les nomme."""

    agent: str | None = None
    tenant: str | None = None
    run_id: str | None = None
    call_id: str | None = None

    def refused(self, error: AccessDenied, action: str) -> None:
        refused(
            error,
            action=action,
            agent=self.agent,
            tenant=self.tenant,
            run_id=self.run_id,
            call_id=self.call_id,
        )


def _where(
    signature: inspect.Signature, args: tuple[object, ...], kwargs: dict[str, object]
) -> _Where:
    """Ce que l'appel refusé visait, lu sur ses arguments."""
    try:
        given = signature.bind_partial(*args, **kwargs).arguments
    except TypeError:
        return _Where()

    def named(*names: str) -> str | None:
        for name in names:
            value = given.get(name)
            if isinstance(value, str):
                return value
        return None

    return _Where(
        agent=named("agent"),
        tenant=named("tenant_id", "tenant"),
        run_id=named("run_id"),
        call_id=named("call_id"),
    )


class Swept(NamedTuple):
    """Ce qu'un balayage du fichier d'audit a lu, et retiré ou retirerait."""

    scanned: int = 0
    removed: int = 0


def sweep(path: Path, days: int, *, now: datetime, dry_run: bool) -> Swept:
    """Retire du fichier d'audit les lignes plus vieilles que ``days`` jours.

    Une ligne dont l'heure ne se lit pas est gardée : on n'efface pas ce qu'on ne
    sait pas dater. Le fichier est réécrit en place, sous le verrou que prennent
    les écritures : un refus écrit pendant le balayage n'est pas perdu.
    """
    if not path.is_file():
        return Swept()
    limit = now - timedelta(days=days)
    with path.open("r+", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            lines = fh.readlines()
            kept = [line for line in lines if not _older(line, limit)]
            removed = len(lines) - len(kept)
            if removed and not dry_run:
                fh.seek(0)
                fh.writelines(kept)
                fh.truncate()
                fh.flush()
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)
    return Swept(scanned=len(lines), removed=removed)


def _older(line: str, limit: datetime) -> bool:
    try:
        written: object = json.loads(line).get("ts")
        when = datetime.fromisoformat(written) if isinstance(written, str) else None
    except ValueError, AttributeError:
        return False
    return when is not None and when.tzinfo is not None and when < limit


def _path(config: LoomConfig) -> Path | None:
    audit = config.security.audit
    return audit.path if audit is not None else None


@contextmanager
def _owned_by(args: tuple[object, ...]) -> Generator[None]:
    """Les refus d'une méthode de ``Loom`` vont au fichier de sa config."""
    config: object = getattr(args[0], "config", None) if args else None
    path = _path(config) if isinstance(config, LoomConfig) else None
    if path is None:
        yield
        return
    token = _FILE.set(path)
    try:
        yield
    finally:
        _FILE.reset(token)


def _write(path: Path, detail: Mapping[str, str]) -> None:
    """Ajoute une ligne au fichier d'audit, sous verrou : plusieurs processus y écrivent.

    Un fichier qu'on ne peut pas écrire ne change rien au refus, qui est déjà
    décidé : il le dit en ERROR, et la ligne WARNING reste.
    """
    line = json.dumps(
        {"ts": datetime.now(UTC).isoformat(timespec="milliseconds"), **detail},
        ensure_ascii=False,
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                fh.write(line + "\n")
                fh.flush()
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)
    except OSError as error:
        logger.error("Refus non écrit dans le fichier d'audit %s : %s", path, error)
