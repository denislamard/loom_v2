# SPDX-License-Identifier: Apache-2.0
"""Qui peut faire quoi : le noyau d'autorisation (docs/loom_role_user.md, § 4).

Un **acteur** est l'utilisateur au nom duquel une opération est faite, une
fois résolu : par sa clé d'API, par ``--user`` ou ``user=``, ou endossé par une
passerelle (``as_user``). Ce module dit s'il peut faire une action sur un agent,
et s'il peut trancher une demande d'approbation. Les accès (SDK, CLI, REST,
MCP) l'appellent tous : c'est ce qui fait qu'aucun n'y échappe. ``grants``
rend, avec les mêmes règles, ce qu'il peut faire agent par agent : c'est ce
qu'une console affiche, sans réécrire la matrice.

Le code ne teste jamais un nom de rôle, seulement des permissions : les rôles
restent de la configuration. Et pour trancher une approbation, **un même rôle**
doit remplir toutes les conditions (§ 4.4) — sinon un rôle annexe qui aurait
``self_approve`` ouvrirait l'auto-approbation sur des demandes où il n'est pas
approbateur.

L'identité, elle, n'est pas vérifiée ici : la clé d'API l'a été par l'accès, et
``--user`` ou ``user=`` sont déclaratifs (§ 6.3, § 6.4). Ce module ne fait que
la confronter à la matrice des droits.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Self

from loom_ia.agents.spec import AgentAccess, AgentSpec
from loom_ia.config.models import PERMISSIONS, AccessRole, ApiKey, Permission, SecurityConfig
from loom_ia.core.model import TenantId

type Coverage = frozenset[str] | None
"""Agents couverts par une permission ; ``None`` : tous, y compris ceux qui ne sont pas déclarés."""

_DECIDING: Final[frozenset[Permission]] = frozenset({"approve", "self_approve", "amend"})
"""Permissions qui servent à trancher une demande d'approbation (``require_decision``, § 4.4)."""


class AccessDenied(PermissionError):
    """L'acteur n'a pas le droit de faire ce qu'il demande ; le message dit ce qui manque.

    Il porte aussi, à part, de qui et de quoi il s'agit — utilisateur, passerelle,
    clé, permission, agent —, pour que le journal d'audit le dise sans relire le
    message (``access/audit.py``). ``audited`` est posé quand il y est écrit :
    un refus qui traverse plusieurs couches n'y figure qu'une fois.
    """

    def __init__(
        self,
        message: str,
        *,
        actor: Actor | None = None,
        user: str | None = None,
        permission: str | None = None,
        agent: str | None = None,
    ) -> None:
        """Le refus, avec de qui et de quoi il s'agit.

        Args:
            message: Ce qui manque, en clair.
            actor: L'acteur refusé ; il donne l'utilisateur, la passerelle et la
                clé.
            user: L'utilisateur, à défaut d'acteur.
            permission: La permission qui manque.
            agent: L'agent visé.
        """
        super().__init__(message)
        self.user = actor.user if actor is not None else user
        self.via = actor.via if actor is not None else None
        self.key = actor.key.id if actor is not None and actor.key is not None else None
        self.tenant: str | None = actor.tenant if actor is not None else None
        self.permission = permission
        self.agent = agent
        self.audited = False


@dataclass(frozen=True, slots=True)
class Actor:
    """L'utilisateur au nom duquel on agit, ses rôles effectifs, et par où il est passé.

    ``roles`` sont les rôles **effectifs** : ceux de l'utilisateur, restreints
    à ``act_as_roles`` quand une passerelle agit en son nom (décision D-5b des utilisateurs et rôles). ``key``
    est la clé d'API qui a identifié la requête — celle de la passerelle, pour
    un acteur endossé : ses ``scopes`` et ses ``agents`` restreignent encore.

    Attributes:
        user: Utilisateur déclaré au nom duquel on agit.
        tenant: Son client.
        roles: Ses rôles effectifs, bornés par ``act_as_roles`` quand une
            passerelle agit en son nom.
        via: Utilisateur de la passerelle qui agit au nom de ``user``
            (``as_user``).
        key: La clé d'API qui a identifié la requête, s'il y en a une : elle
            restreint, elle n'ajoute jamais.
    """

    user: str
    tenant: TenantId
    roles: tuple[AccessRole, ...]
    via: str | None = None
    key: ApiKey | None = None

    @classmethod
    def of(cls, security: SecurityConfig, user: str, *, key: ApiKey | None = None) -> Self:
        """L'acteur d'un utilisateur déclaré, identifié ou non par une clé.

        ``key`` doit être une clé de cet utilisateur : une clé n'identifie
        qu'une personne (décision D-4 des utilisateurs et rôles).
        """
        try:
            declared = security.user(user)
        except KeyError:
            known = ", ".join(sorted(u.id for u in security.users))
            raise AccessDenied(f"Utilisateur {user!r} inconnu (utilisateurs : {known})", user=user) from None
        if key is not None and key.user != declared.id:
            raise AccessDenied(f"La clé {key.id!r} identifie {key.user!r}, pas {user!r}", user=user)
        return cls(
            user=declared.id,
            tenant=declared.tenant,
            roles=security.roles_of(declared.id),
            key=key,
        )

    def endorse(self, security: SecurityConfig, user: str) -> Actor:
        """L'acteur ``user``, au nom duquel cette passerelle agit (``as_user``, décisions D-5 et D-5b des utilisateurs et rôles).

        La passerelle doit avoir ``act_as`` (sa clé comprise), viser un
        utilisateur de son client, et ne peut exercer en son nom que les rôles
        de ``act_as_roles`` : si Paul est gérant et assistant, une passerelle
        bornée à ``assistante`` n'exerce que ce rôle-là. Pas d'endossement en
        chaîne : la config refuse qu'``act_as_roles`` cite un rôle qui a
        ``act_as``, et un acteur déjà endossé ne peut pas endosser.
        """
        if self.via is not None:
            raise AccessDenied(
                f"{self.user} agit déjà au nom de {self.via} : on n'endosse pas en chaîne",
                actor=self,
                permission="act_as",
            )
        gateways = [role for role in self.roles if "act_as" in role.permissions]
        if not gateways or not _kept_by_key(self.key, "act_as"):
            raise AccessDenied(
                f"{self.user} n'agit pas au nom d'autrui (permission 'act_as')",
                actor=self,
                permission="act_as",
            )
        target = Actor.of(security, user)
        if target.tenant != self.tenant:
            raise AccessDenied(
                f"{self.user} agit pour le client {self.tenant}, {user} est chez {target.tenant}",
                actor=self,
                permission="act_as",
            )
        endorsable = {endorsed for role in gateways for endorsed in role.act_as_roles}
        roles = tuple(role for role in target.roles if role.id in endorsable)
        if not roles:
            raise AccessDenied(
                f"{self.user} ne peut pas agir au nom de {user} : aucun de ses rôles "
                f"({_listed(r.id for r in target.roles)}) n'est parmi ceux qu'il endosse "
                f"({_listed(sorted(endorsable))})",
                actor=self,
                permission="act_as",
            )
        return Actor(user=target.user, tenant=target.tenant, roles=roles, via=self.user, key=self.key)

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

    Attributes:
        agent: Agent du run qui a posé la demande : le sous-agent s'il y en a
            un (§ 5.2).
        tool: Outil de l'appel en attente.
        approvers: Rôles approbateurs ; ``None`` : tout rôle qui a ``approve``
            sur l'agent.
        self_approval: ``False`` si l'agent interdit l'auto-approbation
            (décision D-7b des utilisateurs et rôles).
        launcher: Utilisateur du run racine qui a posé la demande : c'est de lui
            que dépend l'auto-approbation.
    """

    agent: str
    tool: str
    approvers: tuple[str, ...] | None = None
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
    name = agent.name if agent is not None else None
    _same_tenant(actor, tenant, permission, name)
    where = f"sur l'agent {agent.name}" if agent is not None else "hors d'un agent"
    refused = f"{actor.signature} n'a pas la permission {permission!r} {where}"

    def denied(reason: str) -> AccessDenied:
        return AccessDenied(f"{refused} : {reason}", actor=actor, permission=permission, agent=name)

    holding = [role for role in actor.roles if permission in role.permissions]
    if not holding:
        raise denied(f"aucun de ses rôles ({_roles(actor)}) ne la porte")
    holding = [role for role in holding if _covers(role, name)]
    if not holding:
        raise denied("ses rôles qui la portent sont bornés à des agents")
    listed = _listed_for(agent.access if agent is not None else None, permission)
    if listed is not None:
        holding = [role for role in holding if role.id in listed]
        if not holding:
            raise denied(f"l'agent la réserve aux rôles {_listed(listed)}")
    _check_key(actor, refused, (permission,), name, permission)
    return tuple(holding)


def require_decision(actor: Actor, asked: Approvable, *, tenant: TenantId | None = None, amend: bool = False) -> tuple[AccessRole, ...]:
    """Les rôles par lesquels l'acteur peut trancher cette demande ; ``AccessDenied`` sinon.

    Règles 5 à 8 du § 4.4, remplies par **un même rôle** : ``approve`` sur
    l'agent, parmi les approbateurs figés, ``self_approve`` (et un agent qui
    ne l'interdit pas) si l'acteur a lancé le run, ``amend`` s'il corrige les
    arguments. Accorder et refuser demandent le même droit : refuser, c'est
    aussi trancher.
    """
    _same_tenant(actor, tenant, "approve", asked.agent)
    refused = f"{actor.signature} ne peut pas trancher la demande {asked.tool} de l'agent {asked.agent}"

    def denied(reason: str, permission: Permission = "approve", *, joined: str = " : ") -> AccessDenied:
        return AccessDenied(f"{refused}{joined}{reason}", actor=actor, permission=permission, agent=asked.agent)

    candidates = [role for role in actor.roles if "approve" in role.permissions and _covers(role, asked.agent)]
    if not candidates:
        raise denied(f"aucun de ses rôles ({_roles(actor)}) n'a 'approve' sur cet agent")
    if asked.approvers is not None:
        candidates = [role for role in candidates if role.id in asked.approvers]
        if not candidates:
            raise denied(f"elle n'est tranchable que par {_listed(asked.approvers)}")
    needed: list[Permission] = ["approve"]
    own = asked.launcher is not None and asked.launcher == actor.user
    if own:
        if not asked.self_approval:
            raise denied("c'est son propre run, et l'agent interdit l'auto-approbation", "self_approve")
        candidates = [role for role in candidates if "self_approve" in role.permissions]
        if not candidates:
            raise denied(
                "c'est son propre run — il faut 'self_approve' dans un rôle qui l'approuve",
                "self_approve",
            )
        needed.append("self_approve")
    if amend:
        candidates = [role for role in candidates if "amend" in role.permissions]
        if not candidates:
            raise denied(
                "en corrigeant ses arguments : il faut 'amend' dans un rôle qui l'approuve",
                "amend",
                joined=" ",
            )
        needed.append("amend")
    _check_key(actor, refused, needed, asked.agent, "approve")
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


def can_decide(actor: Actor, asked: Approvable, *, tenant: TenantId | None = None, amend: bool = False) -> bool:
    """``require_decision``, sans lever : le bouton « Approuver » pour qui peut s'en servir."""
    try:
        require_decision(actor, asked, tenant=tenant, amend=amend)
    except AccessDenied:
        return False
    return True


def grants(actor: Actor, agents: Mapping[str, AgentAccess]) -> dict[Permission, Coverage]:
    """Les permissions que l'acteur exerce, chacune avec les agents qu'elle couvre.

    Mêmes règles que ``require`` (2 à 4 du § 4.3) pour run, answer, cancel,
    read, read_content, admin et act_as. Pour approve, self_approve et amend,
    un même rôle qui peut être approbateur d'au moins une demande de l'agent
    (``require_decision``, règles 5 à 8, hors lanceur) : c'est « où regarder »,
    la décision d'une demande précise restant ``can_decide``. Le client
    (règle 1) n'est pas contrôlé : ``agents`` sont ceux du client de l'acteur.

    Un agent absent de ``agents`` n'a pas de liste : un rôle qui porte la
    permission et vaut pour lui le couvre ; un rôle borné à des agents absents
    les couvre nommément. ``None`` n'est rendu que si tous les agents sont
    couverts, déclarés ou non.

    Args:
        actor: L'acteur, endossé par la passerelle s'il y a lieu.
        agents: Le bloc ``access`` de chaque agent déclaré du client de l'acteur, par nom.

    Returns:
        Chaque permission tenue, dans l'ordre de ``PERMISSIONS``, avec ses
        agents ; une permission absente n'est tenue sur aucun agent.
    """
    found: dict[Permission, Coverage] = {}
    for permission in PERMISSIONS:
        coverage = _coverage(actor, permission, agents)
        if coverage is None or coverage:
            found[permission] = coverage
    return found


def _same_tenant(actor: Actor, tenant: TenantId | None, permission: Permission, agent: str | None) -> None:
    """Règle 1 : l'acteur n'agit que chez son client."""
    if tenant is not None and tenant != actor.tenant:
        raise AccessDenied(
            f"{actor.signature} agit pour le client {actor.tenant}, pas pour {tenant}",
            actor=actor,
            permission=permission,
            agent=agent,
        )


def _covers(role: AccessRole, agent: str | None) -> bool:
    """Le rôle vaut-il ici ? Hors d'un agent, seul un rôle borné à aucun agent."""
    return role.allows(agent) if agent is not None else not role.agents


def _listed_for(access: AgentAccess | None, permission: Permission) -> tuple[str, ...] | None:
    """La liste ``access`` de l'agent pour cette permission, s'il en déclare une (règle 3)."""
    if access is None:
        return None
    match permission:
        case "run":
            return access.run
        case "answer":
            return access.answer
        case "cancel":
            return access.cancel
        case _:
            return None


def _coverage(actor: Actor, permission: Permission, agents: Mapping[str, AgentAccess]) -> Coverage:
    """Les agents sur lesquels l'acteur a la permission (``grants``) ; ``None`` : tous ; vide : aucun."""
    # self_approve et amend ne valent que dans le même rôle qu'approve (§ 4.4).
    needed: tuple[Permission, ...] = ("approve", permission) if permission in ("self_approve", "amend") else (permission,)
    key = actor.key
    if not all(_kept_by_key(key, wanted) for wanted in needed):
        return frozenset()
    holding = [role for role in actor.roles if all(wanted in role.permissions for wanted in needed)]
    if not holding:
        return frozenset()

    def covered(agent: str) -> bool:
        access = agents.get(agent)
        return any(_holds_on(role, agent, access, permission) for role in holding)

    if key is not None and key.agents:
        return frozenset(agent for agent in key.agents if covered(agent))
    declared = frozenset(agent for agent in agents if covered(agent))
    if any(not role.agents for role in holding) and len(declared) == len(agents):
        return None
    named = frozenset(agent for role in holding for agent in role.agents if agent not in agents)
    return declared | named


def _holds_on(role: AccessRole, agent: str, access: AgentAccess | None, permission: Permission) -> bool:
    """Le rôle, qui porte la permission, l'exerce-t-il sur l'agent ? ``access`` : son bloc, ``None`` s'il n'est pas déclaré."""
    if not role.allows(agent):
        return False
    if permission in _DECIDING:
        return access is None or _approver_on(role, access, permission)
    listed = _listed_for(access, permission)
    return listed is None or role.id in listed


def _approver_on(role: AccessRole, access: AgentAccess, permission: Permission) -> bool:
    """Le rôle peut-il être approbateur d'au moins une demande de l'agent (règles 6 et 7, hors lanceur) ?

    Les approbateurs d'une demande sont ceux de son outil, à défaut ceux de
    l'agent, à défaut tout rôle qui a ``approve`` (``Approvers.of``) : sans
    ``default``, les outils sans liste vont à tout approbateur.
    """
    if permission == "self_approve" and not access.self_approval:
        return False
    approve = access.approve
    if approve.default is None or role.id in approve.default:
        return True
    return any(role.id in roles for roles in approve.tools.values())


def _kept_by_key(key: ApiKey | None, permission: Permission) -> bool:
    return key is None or key.scopes is None or permission in key.scopes


def _check_key(
    actor: Actor,
    refused: str,
    permissions: Sequence[Permission],
    agent: str | None,
    permission: Permission,
) -> None:
    """Règle 4 : la clé qui a identifié la requête restreint, elle n'ajoute jamais (décision D-4 des utilisateurs et rôles)."""
    key = actor.key
    if key is None:
        return
    removed = [p for p in permissions if not _kept_by_key(key, p)]
    if removed:
        raise AccessDenied(
            f"{refused} : la clé {key.id} retire {_listed(removed)} (scopes)",
            actor=actor,
            permission=permission,
            agent=agent,
        )
    if key.agents and (agent is None or agent not in key.agents):
        raise AccessDenied(
            f"{refused} : la clé {key.id} est bornée à {_listed(key.agents)}",
            actor=actor,
            permission=permission,
            agent=agent,
        )


def _roles(actor: Actor) -> str:
    return _listed(role.id for role in actor.roles)


def _listed(names: Iterable[str]) -> str:
    return ", ".join(names) or "aucun"
