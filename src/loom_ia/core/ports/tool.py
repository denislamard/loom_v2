# SPDX-License-Identifier: Apache-2.0
"""Ports des outils (#15, #18, #19).

Un outil se décrit par un ``ToolSpec`` et s'exécute avec ``invoke``. Python,
MCP, rôles et sous-agents implémentent ce même port.

Une source d'outils (``ToolSource``, un serveur MCP par exemple) fournit ses
outils au début de chaque run : ils peuvent changer d'un run à l'autre, et la
source peut être indisponible. La liste obtenue reste fixe jusqu'à la fin du
run, pour que les requêtes au modèle restent stables.

Un paquet installé fournit une source par un point d'entrée du groupe
``loom_ia.tools`` (J6.4b) : il désigne une fabrique (``ToolSourceFactory``),
que loom appelle avec ce que la config déclare pour elle.
"""

import hashlib
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import JsonValue

from loom_ia.core.model.content import ToolOutput
from loom_ia.core.model.context import CallerContext
from loom_ia.core.model.ids import RunId, SessionId, TenantId
from loom_ia.core.model.tooling import ToolSpec
from loom_ia.core.ports.idempotency import IdempotencyStore


def idempotency_key(run_id: RunId, call_id: str) -> str:
    """Clé technique d'un appel : identique à chaque reprise du même appel (#18)."""
    return hashlib.sha256(f"{run_id}:{call_id}".encode()).hexdigest()


# Signalement d'un effet déjà mémorisé, avec la clé sous laquelle il l'est.
type ReuseNote = Callable[[str], None]


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolContext:
    """Ce qu'un outil sait de l'appel en cours."""

    tenant_id: TenantId
    session_id: SessionId
    run_id: RunId
    call_id: str
    agent: str
    caller: CallerContext = field(default_factory=CallerContext)
    # Magasin d'idempotence du run (#18, #49) : c'est par ici qu'un outil
    # décoré retrouve l'effet qu'il a déjà produit. Absent hors moteur.
    idempotency: IdempotencyStore | None = field(default=None, compare=False)
    # Un humain a approuvé cet appel après un effet d'état inconnu (#17, #18) :
    # l'outil décoré reprend la réservation périmée et refait l'effet. Vaut
    # pour ce seul appel, et pour personne d'autre.
    replay_unknown: bool = False
    # Appelé, avec sa clé, par un outil qui rend un effet déjà mémorisé au
    # lieu d'agir (#49). Le moteur en fait un ``idempotency.reused`` : sans
    # lui, le journal ne dirait pas pourquoi l'appel n'a rien fait, le
    # magasin partagé n'y écrivant rien. Absent hors moteur.
    on_reuse: ReuseNote | None = field(default=None, compare=False)

    @property
    def idempotency_key(self) -> str:
        return idempotency_key(self.run_id, self.call_id)


class ToolError(Exception):
    """Erreur dont le message est destiné au modèle, pour qu'il corrige son appel."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class UnknownEffect(ToolError):
    """Réservation périmée : l'effet a peut-être eu lieu, personne ne le sait (#18).

    C'est une ``ToolError``, donc son message vaut pour le modèle. Mais
    l'exécuteur la reconnaît et applique ce que l'outil a déclaré en
    ``on_unknown`` : rendre l'erreur, ou suspendre le run pour qu'un humain
    vérifie avant de refaire.
    """


@runtime_checkable
class Tool(Protocol):
    """Outil appelable par un agent.

    ``isinstance`` vérifie seulement la présence de ``spec`` et ``invoke`` :
    c'est ce qui sert à reconnaître un outil dans la config.
    """

    @property
    def spec(self) -> ToolSpec: ...

    async def invoke(self, arguments: dict[str, JsonValue], context: ToolContext) -> ToolOutput:
        """Exécute l'appel ; les arguments ont déjà été validés par le schéma."""
        ...


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceContext:
    """Le run pour lequel une source ouvre ses outils."""

    tenant_id: TenantId
    session_id: SessionId
    run_id: RunId
    agent: str


class SourceUnavailable(Exception):
    """La source ne peut pas fournir ses outils (serveur injoignable, erreur de protocole…).

    ``attempted`` est faux quand la source a refusé sans essayer (attente
    avant une nouvelle connexion) : ce refus ne compte pas pour le disjoncteur.
    """

    def __init__(self, source: str, message: str, *, attempted: bool = True) -> None:
        super().__init__(f"{source} : {message}")
        self.source = source
        self.message = message
        self.attempted = attempted


@runtime_checkable
class ToolSource(Protocol):
    """Fournisseur d'outils découverts au début d'un run (serveur MCP…)."""

    @property
    def name(self) -> str:
        """Nom de la source, repris dans le journal."""
        ...

    @property
    def required(self) -> bool:
        """Vrai si le run ne peut pas se passer de cette source."""
        ...

    def open(self, context: SourceContext) -> AbstractAsyncContextManager[Sequence[Tool]]:
        """Outils disponibles pour ce run, jusqu'à la sortie du contexte.

        Lève ``SourceUnavailable`` si la source ne répond pas.
        """
        ...


@runtime_checkable
class ToolSourceFactory(Protocol):
    """Ce qu'un point d'entrée ``loom_ia.tools`` désigne : de quoi faire une source (J6.4b).

    loom l'appelle au montage d'un agent qui référence la source, une fois par
    agent et par client, avec :

    - ``name`` : le nom de la source dans la config (``tool_sources[].name``) ;
    - ``params`` : ses ``params``, tels que la config les donne — à la
      fabrique de les vérifier, et de lever ``ValueError`` en disant ce qui
      ne va pas ;
    - ``secrets`` : la table des secrets du client pour qui l'agent est monté,
      en lecture seule. Sans clients déclarés, c'est l'environnement ; avec
      des clients, seulement les variables que la config nomme (``api_key_env``,
      ``env_from``, ``headers_env``) et celles que le client redirige — une
      variable que la config ne nomme pas s'y nomme par une redirection
      (``secrets: {CARNET_JETON: CARNET_JETON}``) ;
    - ``base_dir`` : le dossier du fichier de config, auquel se rapportent
      les chemins relatifs de ``params``.

    La fabrique rend la source sans travailler : ``loom validate`` et le
    montage d'essai de ``serve --reload`` l'appellent aussi. Le travail
    (connexion, VM…) se fait à l'ouverture, au début de chaque run
    (``ToolSource.open``) ; ce qui doit durer plus qu'un run se ferme dans un
    ``aclose`` asynchrone, appelé quand l'agent est démonté, s'il existe.

    Les outils rendus portent leur nom court : loom les préfixe
    (``source__outil``), les choisit (``include``, ``exclude``) et applique
    les déclarations de la config. Le nom et ``required`` de la source rendue
    ne comptent pas : c'est la config qui les dit.
    """

    def __call__(
        self,
        *,
        name: str,
        params: Mapping[str, JsonValue],
        secrets: Mapping[str, str],
        base_dir: Path,
    ) -> ToolSource: ...
