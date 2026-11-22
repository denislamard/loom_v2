# SPDX-License-Identifier: Apache-2.0
"""La sécurité partagée : la part reçue de la console, ses contrôles, son application et son cache.

Avec ``security.source: console`` (analyse_referentiel_utilisateurs.md de
Heddlo), les rôles, les utilisateurs et les clés d'API ne sont plus déclarés
dans ``loom.yaml`` : la console les tient dans son référentiel et envoie à
chaque instance sa part (``security.sync``), par le canal, avec la clé
d'instance qui existe déjà.

- **Contrôles** : une part passe les contrôles d'une section ``security`` de
  ``loom.yaml`` (``LoomConfig.check``), sauf la borne d'un rôle à un agent que
  l'instance n'a pas. ``check_part`` les rejoue sans config de loom : la
  console l'appelle avant d'enregistrer une modification, avec les blocs
  ``access`` que chaque instance lui a envoyés.
- **Empreinte** : celle du JSON canonique de la part, sans ``version`` ; les
  deux côtés la calculent avec ``part_fingerprint``.
- **Cache** : la dernière part appliquée, scellée en AES-256-GCM avec une clé
  tirée de la clé d'instance (HKDF-SHA256), sans seconde clé. L'en-tête
  (contrat, instance, version) est authentifié sans être chiffré. Un cache
  illisible, d'une autre clé, d'un autre contrat ou d'une autre instance est
  traité comme absent, avec un avertissement.
"""

import base64
import json
import logging
import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Literal, cast

from pydantic import ValidationError

from loom_ia.agents.spec import AgentAccess, ApproveAccess
from loom_ia.config.models import (
    AccessRole,
    ApiKey,
    AuditConfig,
    LoomConfig,
    SecurityConfig,
    UserSpec,
    check_gateway,
    check_security_references,
)
from loom_ia.console.contract import CONTRACT, SecurityAgent, SecurityRole, SecuritySyncKey, SecuritySyncPayload, SecurityUser
from loom_ia.console.security import fingerprint
from loom_ia.core.model import TenantId, new_id

if TYPE_CHECKING:
    from loom_ia.adapters.crypto.aesgcm import AesGcmCipher

logger = logging.getLogger(__name__)

CACHE_FILE: Final = Path(".loom") / "security.json"
"""Le cache de la sécurité partagée, sous ``workpath``."""

FIRST_PART_TIMEOUT: Final = 10.0
"""Attente de la première part sans cache valide, en secondes (décision Q-4 du référentiel)."""

_HKDF_INFO: Final = b"loom security cache"
"""Contexte de la dérivation de la clé du cache, pour qu'elle ne serve à rien d'autre."""

type Received = Literal["same", "new", "older", "rewritten"]
"""Une part comparée à la précédente : identique, plus récente, de version plus basse, ou de même version au contenu différent."""


class PartRefused(ValueError):
    """Une part que loom n'applique pas : empreinte fausse, autre client, ou contrôle de la sécurité manqué."""


def part_fingerprint(part: SecuritySyncPayload) -> str:
    """L'empreinte d'une part : celle de son JSON canonique, sans ``version`` ni ``fingerprint``.

    Args:
        part: La part.

    Returns:
        ``sha256:`` et le condensé.
    """
    return fingerprint(part.model_dump(mode="json", exclude={"version", "fingerprint"}))


def make_part(
    *,
    version: int,
    tenant: str,
    roles: Iterable[SecurityRole] = (),
    users: Iterable[SecurityUser] = (),
    api_keys: Iterable[SecuritySyncKey] = (),
) -> SecuritySyncPayload:
    """Une part, avec son empreinte : ce que la console envoie à une instance.

    Args:
        version: Version du référentiel.
        tenant: Client de l'instance.
        roles: Les rôles d'accès.
        users: Les utilisateurs rattachés à l'instance.
        api_keys: Leurs clés, avec leur empreinte.

    Returns:
        La part.
    """
    draft = SecuritySyncPayload(
        version=version, tenant=tenant, roles=tuple(roles), users=tuple(users), api_keys=tuple(api_keys), fingerprint="sha256:" + "0" * 64
    )
    return draft.model_copy(update={"fingerprint": part_fingerprint(draft)})


def security_of(part: SecuritySyncPayload, audit: AuditConfig | None) -> SecurityConfig:
    """La section ``security`` que la part déclare, avec le fichier des refus de l'instance.

    Chaque rôle, utilisateur et clé passe les contrôles de son modèle ; la
    section, elle, est posée sans repasser par le sien, qui refuse ce qu'une
    sécurité partagée déclare dans ``loom.yaml``.

    Args:
        part: La part reçue.
        audit: Le fichier des refus de l'instance (``security.audit``).

    Returns:
        La sécurité, ``source: console``.

    Raises:
        PartRefused: Un rôle, un utilisateur ou une clé invalide.
    """
    try:
        roles = tuple(
            AccessRole(id=role.id, description=role.description, permissions=role.permissions, agents=role.agents, act_as_roles=role.act_as_roles)
            for role in part.roles
        )
        users = tuple(UserSpec(id=user.id, name=user.name, roles=user.roles, tenant=TenantId(part.tenant)) for user in part.users)
        keys = tuple(
            ApiKey(id=key.id, hash=key.hash, user=key.user, rate_limit=key.rate_limit, expires=key.expires, scopes=key.scopes, agents=key.agents)
            for key in part.api_keys
        )
    except ValidationError as error:
        raise PartRefused(f"part refusée : {error.errors()[0]['msg']}") from None
    local = SecurityConfig(roles=roles, users=users, api_keys=keys, audit=audit)
    return local.model_copy(update={"source": "console"})


def access_of(agents: Sequence[SecurityAgent]) -> dict[str, AgentAccess]:
    """Les blocs ``access`` des agents, tels qu'une instance les envoie dans ``security``.

    Args:
        agents: Les agents reçus.

    Returns:
        Le bloc ``access`` de chaque agent, par nom.
    """
    return {
        agent.name: AgentAccess(
            run=agent.run,
            answer=agent.answer,
            cancel=agent.cancel,
            approve=ApproveAccess(default=agent.approve_default, tools=agent.approve_tools),
            self_approval=agent.self_approval,
        )
        for agent in agents
    }


def check_part(
    part: SecuritySyncPayload,
    *,
    tenant: str,
    agents: Mapping[str, AgentAccess],
    gateway_user: str | None = None,
    keys_required: bool = False,
) -> SecurityConfig:
    """Contrôle une part comme loom le fera à sa réception, sans sa config : ce que la console appelle avant d'enregistrer.

    Args:
        part: La part.
        tenant: Le client de l'instance.
        agents: Le bloc ``access`` de chaque agent de l'instance (``access_of``).
        gateway_user: La passerelle de l'instance, que la part doit déclarer.
        keys_required: Vrai si l'instance publie ses agents en MCP HTTP
            (``server.mcp.http``) : une part qui déclare quelqu'un doit
            déclarer une clé au moins.

    Returns:
        La sécurité que l'instance appliquerait.

    Raises:
        PartRefused: Empreinte fausse, autre client, ou contrôle manqué ; le
            message dit lequel.
    """
    _check_envelope(part, tenant)
    security = security_of(part, None)
    try:
        check_security_references(security, agents, shared=True)
        if security.received and gateway_user is not None:
            check_gateway(security, gateway_user, tenant)
    except ValueError as error:
        raise PartRefused(str(error)) from None
    if keys_required and security.received and not security.api_keys:
        raise PartRefused("'server.mcp.http' expose les agents comme outils : la part doit déclarer au moins une clé d'API")
    return security


def applied(config: LoomConfig, part: SecuritySyncPayload) -> LoomConfig:
    """La config de l'instance avec la sécurité de la part, contrôlée comme au chargement de ``loom.yaml``.

    Args:
        config: La config, en sécurité partagée.
        part: La part reçue.

    Returns:
        Une copie de la config, la part posée.

    Raises:
        PartRefused: Config en sécurité locale, empreinte fausse, autre client,
            ou contrôle de ``LoomConfig.check`` manqué.
    """
    console = config.console
    if config.security.source != "console" or console is None:
        raise PartRefused("part refusée : la sécurité de cette instance est locale (security.source absent)")
    _check_envelope(part, console.tenant)
    candidate = config.model_copy(update={"security": security_of(part, config.security.audit)})
    try:
        candidate.check()
    except ValueError as error:
        raise PartRefused(f"part refusée : {error}") from None
    return candidate


def compared(previous: SecuritySyncPayload | None, received: SecuritySyncPayload) -> Received:
    """Ce qu'est une part reçue au regard de la précédente (décision Q-13 du référentiel).

    Args:
        previous: La part appliquée, ou celle du cache ; ``None`` s'il n'y en a pas.
        received: La part reçue.

    Returns:
        ``same`` (même version, même contenu), ``new`` (rien avant, ou version
        plus haute), ``older`` (version plus basse : base de la console
        restaurée ?) ou ``rewritten`` (même version, autre contenu).
    """
    if previous is None or received.version > previous.version:
        return "new"
    if received.version < previous.version:
        return "older"
    return "same" if received.fingerprint == previous.fingerprint else "rewritten"


def _check_envelope(part: SecuritySyncPayload, tenant: str) -> None:
    """Empreinte juste et bon client ; ``PartRefused`` sinon."""
    if part.fingerprint != part_fingerprint(part):
        raise PartRefused("part refusée : son empreinte ne correspond pas à son contenu")
    if part.tenant != tenant:
        raise PartRefused(f"part refusée : elle est pour le client {part.tenant!r}, l'instance sert {tenant!r}")


# --- Cache -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SecurityCache:
    """La dernière part appliquée, scellée sous ``workpath`` (``.loom/security.json``).

    Attributes:
        path: Le fichier.
        instance: Identifiant de l'instance, authentifié par le sceau.
        cipher: La clé tirée de la clé d'instance.
    """

    path: Path
    instance: str
    cipher: AesGcmCipher

    @classmethod
    def of(cls, workpath: Path, instance: str, instance_key: str) -> SecurityCache:
        """Le cache d'une instance.

        Args:
            workpath: Dossier de travail de l'instance.
            instance: Son identifiant (``console_instance_id``).
            instance_key: La clé d'instance, dont la clé du cache est tirée.

        Returns:
            Le cache.
        """
        from cryptography.hazmat.primitives.hashes import SHA256
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF

        from loom_ia.adapters.crypto.aesgcm import AesGcmCipher

        key = HKDF(algorithm=SHA256(), length=32, salt=None, info=_HKDF_INFO).derive(instance_key.encode("utf-8"))
        return cls(path=workpath / CACHE_FILE, instance=instance, cipher=AesGcmCipher(key))

    def read(self) -> SecuritySyncPayload | None:
        """La part du cache, ou ``None`` : absente, ou refusée (avertissement dans les logs).

        Returns:
            La dernière part appliquée par l'instance.
        """
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as error:
            return self._refused(f"illisible ({error})")
        try:
            header = cast("dict[str, Any]", json.loads(text))
            contract, instance, version, key_id, sealed = (header["contract"], header["instance"], header["version"], header["key_id"], header["sealed"])
        except (ValueError, TypeError, KeyError) as error:
            return self._refused(f"illisible ({error})")
        if contract != CONTRACT:
            return self._refused(f"contrat {contract!r}, loom sert le contrat {CONTRACT}")
        if instance != self.instance:
            return self._refused(f"il est de l'instance {instance!r}, celle-ci est {self.instance!r}")
        if key_id != self.cipher.key_id:
            return self._refused("scellé par une autre clé d'instance")
        from loom_ia.core.ports import SealBroken

        try:
            clear = self.cipher.unseal(base64.b64decode(sealed, validate=True), _aad(instance, version))
            part = SecuritySyncPayload.model_validate_json(clear)
        except (SealBroken, ValueError) as error:
            return self._refused(f"sceau ou contenu refusé ({error})")
        if part.version != version:
            return self._refused(f"version {part.version} scellée, {version} annoncée")
        return part

    def write(self, part: SecuritySyncPayload) -> None:
        """Remplace le cache par cette part : fichier temporaire puis renommage, droits ``0600``.

        Une écriture manquée est dite dans les logs : le cache sert au
        prochain démarrage, il n'arrête rien.

        Args:
            part: La part appliquée.
        """
        sealed = self.cipher.seal(part.model_dump_json().encode("utf-8"), _aad(self.instance, part.version))
        header = {
            "contract": CONTRACT,
            "instance": self.instance,
            "version": part.version,
            "key_id": self.cipher.key_id,
            "sealed": base64.b64encode(sealed).decode("ascii"),
        }
        temporary = self.path.with_name(f".{self.path.name}.{new_id()}")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as file:
                json.dump(header, file)
            temporary.replace(self.path)
        except OSError as error:
            logger.warning("Sécurité partagée : cache %s non écrit (%s) ; la part reste appliquée", self.path, error)

    def _refused(self, why: str) -> None:
        """Dit pourquoi le cache est écarté ; il est traité comme absent."""
        logger.warning("Sécurité partagée : cache %s refusé, traité comme absent — %s", self.path, why)


def _aad(instance: str, version: int) -> bytes:
    """Ce que le sceau authentifie sans le chiffrer : contrat, instance, version."""
    return f"{CONTRACT}|{instance}|{version}".encode()
