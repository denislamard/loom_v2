# SPDX-License-Identifier: Apache-2.0
"""Section ``console`` : le canal chiffré vers la console d'administration (docs/analyse-exortateur-loom.md).

loom ouvre **une** connexion WSS sortante vers la console ; elle porte les
événements des runs vers la console, et ses commandes vers loom. Rien n'entre
dans loom par ailleurs : il tourne derrière un pare-feu.

- ``enable`` est obligatoire dans la section. ``false`` : la section est gardée
  mais ignorée — rien n'est monté, ses autres clés ne sont pas exigées ; celles
  qui sont écrites sont tout de même contrôlées dans leur forme, pour qu'une
  faute de frappe ne dorme pas jusqu'au jour où l'on passe à ``true``.
  ``true`` : ``url`` et ``key_env`` sont exigées, et les contrôles croisés
  (client, utilisateur des commandes, types d'événements) s'appliquent
  (``LoomConfig.check``).
- La clé d'instance ne s'écrit jamais dans la config : ``key_env`` nomme la
  variable d'environnement qui la porte, comme ``dsn_env`` ou ``api_key_env``.
- ``ws://`` n'est accepté que vers la boucle locale, en profil ``dev`` (décision
  D-9) ; c'est ``LoomConfig.check`` qui le juge, le profil y étant connu.

Exemple ::

    console:
      enable: true
      url: wss://console.exemple.fr/loom
      key_env: LOOM_CONSOLE_KEY
      tenant: default
      commands: {user: console-gateway}
      export: {mode: on_demand}
"""

import re
from pathlib import Path
from typing import Final, Literal, Self
from urllib.parse import urlsplit

from pydantic import PositiveFloat, PositiveInt, field_validator, model_validator

from loom_ia.core.model import DEFAULT_TENANT, DomainModel, TenantId

type ExportMode = Literal["push", "on_demand"]
"""Ce qui part vers la console : ``push``, le contenu aussi ; ``on_demand``, les métadonnées, le contenu se lisant à la demande."""

CONSOLE_SCHEMES: Final = ("wss", "ws")
"""Schémas d'adresse acceptés ; ``ws`` ne vaut que vers la boucle locale, en profil ``dev``."""

INSTANCE_ID_PATTERN: Final = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$"
"""Forme d'un identifiant d'instance : lettres, chiffres, ``_``, ``.`` et ``-``, 64 caractères au plus."""

_INSTANCE_ID: Final = re.compile(INSTANCE_ID_PATTERN)


class ConsoleTls(DomainModel):
    """TLS du canal (``console.tls``) : la vérification du certificat de la console ne se désactive jamais.

    Attributes:
        ca_file: Autorité de certification à reconnaître en plus de celles du
            système (édition entreprise avec ses propres certificats).
        client_cert: Certificat client de l'instance (mTLS), en PEM.
        client_key_env: **Nom** de la variable qui porte la clé privée du
            certificat client, en PEM ; jamais la clé elle-même.
    """

    ca_file: Path | None = None
    client_cert: Path | None = None
    client_key_env: str | None = None

    @model_validator(mode="after")
    def _check_pair(self) -> Self:
        """Le certificat client et sa clé vont ensemble."""
        if (self.client_cert is None) != (self.client_key_env is None):
            raise ValueError("console.tls : 'client_cert' et 'client_key_env' vont ensemble (mTLS) — les deux, ou aucun")
        return self


class ConsoleCommands(DomainModel):
    """Commandes de la console (``console.commands``) : elles passent par une passerelle déclarée (décision D-1).

    Attributes:
        user: Utilisateur loom de la passerelle, du client de la console, dont
            un rôle a ``act_as`` ; il endosse l'utilisateur que chaque commande
            nomme, dans les bornes d'``act_as_roles``. Absent : toute commande
            est refusée.
        rate: Commandes acceptées par minute, au plus.
        max_ttl: Horizon maximal de l'échéance d'une commande, en secondes : une
            commande qui expire plus tard est refusée.
    """

    user: str | None = None
    rate: PositiveInt = 60
    max_ttl: PositiveInt = 300


class ConsoleExport(DomainModel):
    """Ce qui part vers la console, et comment (``console.export``).

    Attributes:
        mode: ``on_demand`` ou ``push`` ; absent : suit ``capture.exports`` du
            client (``metadata`` → ``on_demand``, ``content`` → ``push``).
        exclude_events: Types d'événements qui ne partent pas.
        buffer_size: Taille de la file d'envoi, en événements. Pleine, la
            file ne perd rien : la session est relue plus tard dans le journal
            (décision D-4).
        batch_size: Événements par lot, au plus.
        batch_delay: Attente maximale avant d'envoyer un lot incomplet, en
            secondes.
    """

    mode: ExportMode | None = None
    exclude_events: tuple[str, ...] = ()
    buffer_size: PositiveInt = 10_000
    batch_size: PositiveInt = 200
    batch_delay: PositiveFloat = 0.2


class ConsoleFiles(DomainModel):
    """Fichiers servis à la console pour décider d'une approbation (``console.files``).

    Attributes:
        max_size: Taille maximale d'un fichier servi, en octets (25 Mo par défaut).
        chunk_size: Taille d'un morceau envoyé, en octets (256 Ko par défaut).
        preview_timeout: Durée maximale du rendu d'un aperçu, en secondes.
    """

    max_size: PositiveInt = 25 * 1024 * 1024
    chunk_size: PositiveInt = 256 * 1024
    preview_timeout: PositiveFloat = 30.0

    @model_validator(mode="after")
    def _check_sizes(self) -> Self:
        """Un morceau ne dépasse pas le fichier qu'il découpe."""
        if self.chunk_size > self.max_size:
            raise ValueError(f"console.files : 'chunk_size' ({self.chunk_size}) dépasse 'max_size' ({self.max_size})")
        return self


class ConsoleConfig(DomainModel):
    """Section ``console`` : le canal vers la console d'administration.

    Attributes:
        enable: Obligatoire. ``false`` : la section est ignorée, rien n'est
            monté ; ``true`` : ``url`` et ``key_env`` sont exigées.
        url: Adresse de la console, ``wss://…`` (``ws://`` vers la boucle
            locale, en profil ``dev`` seulement).
        key_env: **Nom** de la variable qui porte la clé de l'instance, émise
            par la console ; jamais la clé elle-même.
        tenant: Client que cette console sert : seuls ses événements partent,
            et l'utilisateur des commandes doit en être.
        instance_id: Identifiant stable de l'instance ; absent, il est créé au
            premier démarrage et gardé sous ``workpath``. À déclarer quand
            plusieurs machines servent la même instance.
        tls: Autorité de certification, mTLS.
        commands: Passerelle et bornes des commandes.
        export: Ce qui part, et comment.
        files: Fichiers servis pour les approbations.
    """

    enable: bool
    url: str | None = None
    key_env: str | None = None
    tenant: TenantId = DEFAULT_TENANT
    instance_id: str | None = None
    tls: ConsoleTls = ConsoleTls()
    commands: ConsoleCommands = ConsoleCommands()
    export: ConsoleExport = ConsoleExport()
    files: ConsoleFiles = ConsoleFiles()

    @field_validator("url")
    @classmethod
    def _check_url(cls, url: str | None) -> str | None:
        """La forme de l'adresse : ``wss://`` ou ``ws://``, un hôte, et ni identifiants ni clé dedans."""
        if url is None:
            return None
        parts = urlsplit(url)
        if parts.scheme not in CONSOLE_SCHEMES:
            raise ValueError(f"console.url {url!r} : adresse en wss:// attendue (ws:// seulement vers localhost, en profil dev)")
        if not parts.hostname:
            raise ValueError(f"console.url {url!r} : l'hôte manque")
        if parts.username is not None or parts.password is not None:
            raise ValueError("console.url : pas d'identifiants dans l'adresse — la clé se lit dans la variable de 'key_env'")
        if parts.query or parts.fragment:
            raise ValueError("console.url : ni paramètres ni fragment — la clé ne passe jamais par l'adresse")
        return url

    @field_validator("instance_id")
    @classmethod
    def _check_instance_id(cls, instance_id: str | None) -> str | None:
        """Un identifiant court, sans espace ni caractère de chemin."""
        if instance_id is not None and not _INSTANCE_ID.fullmatch(instance_id):
            raise ValueError(f"console.instance_id {instance_id!r} : lettres, chiffres, '_', '.' et '-' seulement, 64 caractères au plus")
        return instance_id

    @model_validator(mode="after")
    def _check_enabled(self) -> Self:
        """Activée, la console a une adresse et une clé."""
        if not self.enable:
            return self
        missing = [name for name, value in (("url", self.url), ("key_env", self.key_env)) if value is None]
        if missing:
            raise ValueError(f"console : 'enable: true' demande {' et '.join(repr(name) for name in missing)}")
        return self

    @property
    def scheme(self) -> str | None:
        """Le schéma de l'adresse (``wss`` ou ``ws``), ``None`` sans adresse."""
        return None if self.url is None else urlsplit(self.url).scheme

    @property
    def host(self) -> str | None:
        """L'hôte de l'adresse, ``None`` sans adresse."""
        return None if self.url is None else urlsplit(self.url).hostname


def plain_text_refusal(console: ConsoleConfig | None, profile: str | None) -> str | None:
    """Pourquoi une console activée en ``ws://`` est refusée dans ce profil ; ``None`` si elle ne l'est pas (décision D-9).

    Le transport en clair n'est permis qu'en profil ``dev`` : la boucle locale,
    elle, est déjà exigée par ``LoomConfig.check``. Appelée par le chargeur,
    une fois le profil posé, et au montage pour une config construite en Python.

    Args:
        console: La section, ou ``None``.
        profile: Le profil actif.

    Returns:
        Le motif du refus, ou ``None``.
    """
    if console is None or not console.enable or console.scheme != "ws" or profile == "dev":
        return None
    shown = "sans profil" if profile is None else f"en profil {profile!r}"
    return f"console.url : ws:// (sans chiffrement) n'est accepté qu'en profil 'dev', vers la boucle locale ; ici {shown} — passer en wss://"
