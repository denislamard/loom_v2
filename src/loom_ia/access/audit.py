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

Le format ``json`` des logs met le détail sous ``audit`` ; le format console
le dit dans le message. Une commande de la CLI qui affiche elle-même le refus
écarte la ligne de son terminal (``TOLD``) : le refus se lit une fois. Ce sont
des logs : ils suivent ``telemetry.logging`` (un niveau au-dessus de WARNING
les tait) et ne durent que ce que dure leur destination — un service qui les
garde les envoie à un collecteur.
"""

import functools
import inspect
import logging
from collections.abc import AsyncGenerator, Callable, Coroutine, Generator
from contextlib import aclosing, contextmanager
from contextvars import ContextVar
from typing import Any, Final, Literal, NamedTuple

from loom_ia.access.authz import AccessDenied
from loom_ia.core import AUDIT, TOLD

# Logger des refus : à router à part (fichier, collecteur) pour en garder la trace.
AUDIT_LOGGER: Final = "loom_ia.access.audit"

type Access = Literal["sdk", "rest", "mcp", "cli"]

logger = logging.getLogger(AUDIT_LOGGER)

# L'accès par lequel la requête en cours est arrivée : posé à l'entrée de chaque
# accès (``through``), lu par le refus qui sort de ``Loom``. Le SDK appelé
# directement n'en pose aucun.
_ACCESS: ContextVar[Access] = ContextVar("loom_access", default="sdk")


@contextmanager
def through(access: Access) -> Generator[None]:
    """Tout refus écrit dans ce bloc dit qu'il est arrivé par ``access``."""
    token = _ACCESS.set(access)
    try:
        yield
    finally:
        _ACCESS.reset(token)


def entering(access: Access) -> None:
    """Pose l'accès de la requête en cours, pour la tâche qui la sert (une route REST)."""
    _ACCESS.set(access)


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
    """Écrit un refus : une ligne WARNING, son détail à part ; les champs absents sont omis.

    Sans ``access``, celui de la requête en cours (``through``), ``sdk`` à défaut.
    """
    detail: dict[str, str] = {
        name: value
        for name, value in (
            ("access", access or _ACCESS.get()),
            ("action", action),
            ("user", user),
            ("via", via),
            ("key", key),
            ("permission", permission),
            ("agent", agent),
            ("tenant_id", tenant),
            ("run_id", run_id),
            ("call_id", call_id),
            ("reason", reason),
        )
        if value is not None
    }
    said = " ".join(f"{name}={value}" for name, value in detail.items() if name != "reason")
    logger.warning(
        "Accès refusé — %s [%s]",
        reason,
        said,
        extra={AUDIT: detail, "tenant_id": tenant, "run_id": run_id, TOLD: True},
    )


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
