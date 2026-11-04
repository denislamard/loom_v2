# SPDX-License-Identifier: Apache-2.0
"""Qui peut faire quoi : le noyau d'autorisation (docs/loom_role_user.md, § 4).

Un **acteur** est l'utilisateur au nom duquel une opération est faite, une
fois résolu : par sa clé d'API, par ``--user`` ou ``user=``, ou endossé par une
passerelle (``as_user``). Ce module dit s'il peut faire une action sur un agent,
et s'il peut trancher une demande d'approbation. Les accès (SDK, CLI, REST,
MCP) l'appellent tous : c'est ce qui fait qu'aucun n'y échappe.

Le code ne teste jamais un nom de rôle, seulement des permissions : les rôles
restent de la configuration. Et pour trancher une approbation, **un même rôle**
doit remplir toutes les conditions (§ 4.4) — sinon un rôle annexe qui aurait
``self_approve`` ouvrirait l'auto-approbation sur des demandes où il n'est pas
approbateur.

L'identité, elle, n'est pas vérifiée ici : la clé d'API l'a été par l'accès, et
``--user`` ou ``user=`` sont déclaratifs (§ 6.3, § 6.4). Ce module ne fait que
la confronter à la matrice des droits.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Self

from loom_ia.agents.spec import AgentSpec
from loom_ia.config.models import AccessRole, ApiKey, Permission, SecurityConfig
from loom_ia.core.model import TenantId


class AccessDenied(PermissionError):
    """L'acteur n'a pas le droit de faire ce qu'il demande ; le message dit ce qui manque."""


@dataclass(frozen=True, slots=True)
class Actor:
    """L'utilisateur au nom duquel on agit, ses rôles effectifs, et par où il est passé.

    ``roles`` sont les rôles **effectifs** : ceux de l'utilisateur, restreints
    à ``act_as_roles`` quand une passerelle agit en son nom (D-5b). ``key``
    est la clé d'API qui a identifié la requête — celle de la passerelle, pour
    un acteur endossé : ses ``scopes`` et ses ``agents`` restreignent encore.
    """

    user: str
    tenant: TenantId
    roles: tuple[AccessRole, ...]
    # Utilisateur de la passerelle qui agit au nom de ``user`` (``as_user``).
    via: str | None = None
    key: ApiKey | None = None

    @classmethod
    def of(cls, security: SecurityConfig, user: str, *, key: ApiKey | None = None) -> Self:
        """L'acteur d'un utilisateur déclaré, identifié ou non par une clé.

        ``key`` doit être une clé de cet utilisateur : une clé n'identifie
        qu'une personne (D-4).
        """
        try:
            declared = security.user(user)
        except KeyError:
            known = ", ".join(sorted(u.id for u in security.users))
            raise AccessDenied(f"Utilisateur {user!r} inconnu (utilisateurs : {known})") from None
        if key is not None and key.user != declared.id:
            raise AccessDenied(f"La clé {key.id!r} identifie {key.user!r}, pas {user!r}")
        return cls(
            user=declared.id,
            tenant=declared.tenant,
            roles=security.roles_of(declared.id),
            key=key,
        )

    def endorse(self, security: SecurityConfig, user: str) -> Actor:
        """L'acteur ``user``, au nom duquel cette passerelle agit (``as_user``, D-5, D-5b).

        La passerelle doit avoir ``act_as`` (sa clé comprise), viser un
        utilisateur de son client, et ne peut exercer en son nom que les rôles
        de ``act_as_roles`` : si Paul est gérant et assistant, une passerelle
        bornée à ``assistante`` n'exerce que ce rôle-là. Pas d'endossement en
        chaîne : la config refuse qu'``act_as_roles`` cite un rôle qui a
        ``act_as``, et un acteur déjà endossé ne peut pas endosser.
        """
        if self.via is not None:
            raise AccessDenied(
                f"{self.user} agit déjà au nom de {self.via} : on n'endosse pas en chaîne"
            )
        gateways = [role for role in self.roles if "act_as" in role.permissions]
        if not gateways or not _kept_by_key(self.key, "act_as"):
            raise AccessDenied(f"{self.user} n'agit pas au nom d'autrui (permission 'act_as')")
        target = Actor.of(security, user)
        if target.tenant != self.tenant:
            raise AccessDenied(
                f"{self.user} agit pour le client {self.tenant}, {user} est chez {target.tenant}"
            )
        endorsable = {endorsed for role in gateways for endorsed in role.act_as_roles}
        roles = tuple(role for role in target.roles if role.id in endorsable)
        if not roles:
            raise AccessDenied(
                f"{self.user} ne peut pas agir au nom de {user} : aucun de ses rôles "
                f"({_listed(r.id for r in target.roles)}) n'est parmi ceux qu'il endosse "
                f"({_listed(sorted(endorsable))})"
            )
        return Actor(
            user=target.user, tenant=target.tenant, roles=roles, via=self.user, key=self.key
        )

    @property
    def signature(self) -> str:
        """Comment l'acteur se nomme dans un message : ``marie (via app-crm)``."""
        return self.user if self.via is None else f"{self.user} (via {self.via})"


@dataclass(frozen=True, slots=True)
class Approvable:
    """Ce qu'une demande d'approbation dit de qui peut la trancher (§ 4.4, § 5.3).

    ``approvers`` et ``self_approval`` sont figés dans la demande quand elle
    est écrite : une config changée ensuite ne change pas, en silence, qui
    peut trancher. ``launcher`` est l'utilisateur du run racine qui l'a posée.
    """

    # Agent du run qui a posé la demande : le sous-agent s'il y en a un (§ 5.2).
    agent: str
    tool: str
    # Rôles approbateurs ; ``None`` : tout rôle qui a ``approve`` sur l'agent.
    approvers: tuple[str, ...] | None = None
    # ``False`` si l'agent interdit l'auto-approbation (D-7b).
    self_approval: bool = True
    launcher: str | None = None


def require(
    actor: Actor,
    permission: Permission,
    *,
    agent: AgentSpec | None = None,
    tenant: TenantId | None = None,
) -> tuple[AccessRole, ...]:
    """Les rôles par lesquels l'acteur a cette permission sur l'agent ; ``AccessDenied`` sinon.

    Règles 1 à 4 du § 4.3 : le client, un rôle qui porte la permission et
    vaut pour l'agent, la liste ``access`` de l'agent pour ``run``,
    ``answer`` et ``cancel``, et ce que la clé laisse. Sans agent (lister les
    sessions d'un client), seuls comptent les rôles qui ne sont bornés à aucun
    agent, et une clé qui ne l'est pas non plus.

    ``approve`` se demande à ``require_decision`` : il dépend de la demande.
    """
    if permission in ("approve", "self_approve", "amend"):
        raise ValueError(f"{permission!r} dépend de la demande : require_decision")
    _same_tenant(actor, tenant)
    where = f"sur l'agent {agent.name}" if agent is not None else "hors d'un agent"
    refused = f"{actor.signature} n'a pas la permission {permission!r} {where}"
    holding = [role for role in actor.roles if permission in role.permissions]
    if not holding:
        raise AccessDenied(f"{refused} : aucun de ses rôles ({_roles(actor)}) ne la porte")
    name = agent.name if agent is not None else None
    holding = [role for role in holding if _covers(role, name)]
    if not holding:
        raise AccessDenied(f"{refused} : ses rôles qui la portent sont bornés à des agents")
    listed = _listed_for(agent, permission)
    if listed is not None:
        holding = [role for role in holding if role.id in listed]
        if not holding:
            raise AccessDenied(f"{refused} : l'agent la réserve aux rôles {_listed(listed)}")
    _check_key(actor, refused, (permission,), name)
    return tuple(holding)


def require_decision(
    actor: Actor, asked: Approvable, *, tenant: TenantId | None = None, amend: bool = False
) -> tuple[AccessRole, ...]:
    """Les rôles par lesquels l'acteur peut trancher cette demande ; ``AccessDenied`` sinon.

    Règles 5 à 8 du § 4.4, remplies par **un même rôle** : ``approve`` sur
    l'agent, parmi les approbateurs figés, ``self_approve`` (et un agent qui
    ne l'interdit pas) si l'acteur a lancé le run, ``amend`` s'il corrige les
    arguments. Accorder et refuser demandent le même droit : refuser, c'est
    aussi trancher.
    """
    _same_tenant(actor, tenant)
    refused = (
        f"{actor.signature} ne peut pas trancher la demande {asked.tool} de l'agent {asked.agent}"
    )
    candidates = [
        role for role in actor.roles if "approve" in role.permissions and _covers(role, asked.agent)
    ]
    if not candidates:
        raise AccessDenied(
            f"{refused} : aucun de ses rôles ({_roles(actor)}) n'a 'approve' sur cet agent"
        )
    if asked.approvers is not None:
        candidates = [role for role in candidates if role.id in asked.approvers]
        if not candidates:
            raise AccessDenied(
                f"{refused} : elle n'est tranchable que par {_listed(asked.approvers)}"
            )
    needed: list[Permission] = ["approve"]
    own = asked.launcher is not None and asked.launcher == actor.user
    if own:
        if not asked.self_approval:
            raise AccessDenied(
                f"{refused} : c'est son propre run, et l'agent interdit l'auto-approbation"
            )
        candidates = [role for role in candidates if "self_approve" in role.permissions]
        if not candidates:
            raise AccessDenied(
                f"{refused} : c'est son propre run — il faut 'self_approve' dans un rôle "
                "qui l'approuve"
            )
        needed.append("self_approve")
    if amend:
        candidates = [role for role in candidates if "amend" in role.permissions]
        if not candidates:
            raise AccessDenied(
                f"{refused} en corrigeant ses arguments : il faut 'amend' dans un rôle "
                "qui l'approuve"
            )
        needed.append("amend")
    _check_key(actor, refused, needed, asked.agent)
    return tuple(candidates)


def can(
    actor: Actor,
    permission: Permission,
    *,
    agent: AgentSpec | None = None,
    tenant: TenantId | None = None,
) -> bool:
    """``require``, sans lever : pour n'afficher une action qu'à qui peut la faire."""
    try:
        require(actor, permission, agent=agent, tenant=tenant)
    except AccessDenied:
        return False
    return True


def can_decide(
    actor: Actor, asked: Approvable, *, tenant: TenantId | None = None, amend: bool = False
) -> bool:
    """``require_decision``, sans lever : le bouton « Approuver » pour qui peut s'en servir."""
    try:
        require_decision(actor, asked, tenant=tenant, amend=amend)
    except AccessDenied:
        return False
    return True


def _same_tenant(actor: Actor, tenant: TenantId | None) -> None:
    """Règle 1 : l'acteur n'agit que chez son client."""
    if tenant is not None and tenant != actor.tenant:
        raise AccessDenied(
            f"{actor.signature} agit pour le client {actor.tenant}, pas pour {tenant}"
        )


def _covers(role: AccessRole, agent: str | None) -> bool:
    """Le rôle vaut-il ici ? Hors d'un agent, seul un rôle borné à aucun agent."""
    return role.allows(agent) if agent is not None else not role.agents


def _listed_for(agent: AgentSpec | None, permission: Permission) -> tuple[str, ...] | None:
    """La liste ``access`` de l'agent pour cette permission, s'il en déclare une (règle 3)."""
    if agent is None:
        return None
    access = agent.access
    match permission:
        case "run":
            return access.run
        case "answer":
            return access.answer
        case "cancel":
            return access.cancel
        case _:
            return None


def _kept_by_key(key: ApiKey | None, permission: Permission) -> bool:
    return key is None or key.scopes is None or permission in key.scopes


def _check_key(
    actor: Actor, refused: str, permissions: Sequence[Permission], agent: str | None
) -> None:
    """Règle 4 : la clé qui a identifié la requête restreint, elle n'ajoute jamais (D-4)."""
    key = actor.key
    if key is None:
        return
    removed = [p for p in permissions if not _kept_by_key(key, p)]
    if removed:
        raise AccessDenied(f"{refused} : la clé {key.id} retire {_listed(removed)} (scopes)")
    if key.agents and (agent is None or agent not in key.agents):
        raise AccessDenied(f"{refused} : la clé {key.id} est bornée à {_listed(key.agents)}")


def _roles(actor: Actor) -> str:
    return _listed(role.id for role in actor.roles)


def _listed(names: Iterable[str]) -> str:
    return ", ".join(names) or "aucun"
