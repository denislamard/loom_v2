# SPDX-License-Identifier: Apache-2.0
"""Un serveur MCP référencé par un agent : source d'outils (point 19, fonction D3).

La référence choisit les outils exposés (``include`` ou ``exclude``), leur
préfixe (``serveur__outil``, ou ``alias__outil``) et leurs déclarations :
annotations du serveur, puis ``mcp_servers[].tools``, puis la référence de
l'agent, de la plus faible à la plus forte.

Un outil irréversible — annoncé destructeur, ou sans annotation, ce que la
spec MCP tient pour destructeur — qu'aucune déclaration ne soumet à une
approbation est exécuté sans accord humain : c'est dit une fois, au premier
listage des outils, avec ce qu'il faut écrire dans la config.

Un outil dont le nom préfixé ne respecte pas le format des API (lettres,
chiffres, ``_``, ``-``, 64 caractères au plus) est écarté avec un
avertissement. La clé d'idempotence de l'appel part dans ``_meta``.
"""

import logging
from collections.abc import AsyncGenerator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Final, cast

from mcp import McpError, types
from pydantic import JsonValue, ValidationError

from loom_ia.adapters.mcp.convert import declared, description, input_schema, to_output
from loom_ia.adapters.mcp.pool import McpPool
from loom_ia.adapters.mcp.server import ConnectionLost, McpServer
from loom_ia.adapters.mcp.transports import SessionFactory
from loom_ia.core import REPORTED, SETUP
from loom_ia.core.model import (
    MCP_PREFIX_SEPARATOR,
    McpServerSpec,
    ToolOutput,
    ToolOverrides,
    ToolSpec,
)
from loom_ia.core.ports import SourceContext, SourceUnavailable, Tool, ToolContext, ToolError

logger = logging.getLogger(__name__)

IDEMPOTENCY_META: Final = "loom-ia/idempotency_key"
"""Clé de ``_meta`` qui porte la clé d'idempotence d'un appel (conception §9.5)."""

POOLED: Final = frozenset({"shared", "tenant"})
"""Portées dont la connexion vit dans le pool, au-delà d'un run."""

LOST: Final = (
    "Connexion au serveur perdue pendant l'appel : l'outil a peut-être produit son effet. "
    "Il n'a pas été relancé automatiquement ; vérifie avant de le rappeler."
)
"""Erreur rendue au modèle quand la connexion tombe pendant un appel qui ne peut pas être relancé sans risque."""


@dataclass(frozen=True, kw_only=True)
class McpSelection:
    """Ce qu'un agent prend d'un serveur.

    Attributes:
        prefix: Préfixe des noms d'outils exposés à l'agent.
        include: Seuls ces outils du serveur ; ``None`` : tous.
        exclude: Outils du serveur écartés.
        required: Le run ne peut pas se passer de ce serveur.
        tools: Déclarations propres à l'agent, par nom d'outil du serveur.
    """

    prefix: str
    include: tuple[str, ...] | None = None
    exclude: tuple[str, ...] | None = None
    required: bool = False
    tools: Mapping[str, ToolOverrides] = field(default_factory=dict[str, ToolOverrides])


@dataclass(frozen=True, slots=True)
class McpTool:
    """Outil d'un serveur MCP, sous son nom préfixé.

    Attributes:
        spec: Définition de l'outil, sous son nom préfixé.
        server: Le serveur qui l'exécute.
        remote_name: Nom de l'outil chez le serveur.
    """

    spec: ToolSpec
    server: McpServer
    remote_name: str

    async def invoke(self, arguments: dict[str, JsonValue], context: ToolContext) -> ToolOutput:
        """Appelle l'outil chez le serveur, avec la clé d'idempotence de l'appel dans ``_meta``.

        Une perte de connexion est rejouée une fois si l'outil peut l'être sans
        risque (``safe_to_retry``).

        Raises:
            ToolError: Serveur indisponible, connexion perdue pendant l'appel, ou
                erreur MCP ; le modèle en lit le message.
        """
        meta: dict[str, Any] = {IDEMPOTENCY_META: context.idempotency_key}
        try:
            result = await self.server.call(
                self.remote_name,
                cast(dict[str, Any], arguments),
                meta=meta,
                retry=self.spec.safe_to_retry,
            )
        except SourceUnavailable as exc:
            raise ToolError(f"Serveur MCP {self.server.name} indisponible : {exc.message}") from exc
        except ConnectionLost as exc:
            raise ToolError(LOST) from exc
        except McpError as exc:
            raise ToolError(f"Erreur du serveur MCP {self.server.name} : {exc.error.message}") from exc
        return to_output(result)


def unguarded_spec(spec: ToolSpec) -> bool:
    """Vrai si l'outil est irréversible et qu'aucune déclaration n'en fait approuver les appels.

    L'approbation qu'un client impose (``tenants[].approvals``) n'est pas vue ici :
    elle se décide au run.
    """
    return spec.side_effects == "irreversible" and spec.approval == "never"


class McpSource:
    """Source d'outils : un serveur, tel qu'un agent le référence."""

    def __init__(
        self,
        spec: McpServerSpec,
        selection: McpSelection,
        *,
        factory: SessionFactory,
        pool: McpPool | None = None,
        pool_key: str | None = None,
    ) -> None:
        """Prépare la source, sans se connecter.

        Args:
            spec: Le serveur déclaré.
            selection: Ce que l'agent en prend.
            factory: Ouvre une session selon le transport.
            pool: Pool des connexions partagées ; requis en portée ``shared`` ou
                ``tenant``.
            pool_key: Clé de la connexion dans le pool : le nom du serveur en portée
                ``shared``, son nom et son client en portée ``tenant``.

        Raises:
            ValueError: Portée partagée sans pool.
        """
        if spec.scope in POOLED and pool is None:
            raise ValueError(f"Serveur MCP {spec.name!r} de portée {spec.scope} : pool requis")
        self.spec = spec
        self.selection = selection
        self._factory = factory
        self._pool = pool
        # Clé de pool : le nom du serveur en portée ``shared``, son nom et son
        # client en portée ``tenant`` (point 34) — c'est l'appelant qui la pose,
        # parce que lui seul sait pour qui il monte l'agent.
        self.pool_key = pool_key or spec.name
        self._warned = False
        self._warned_unguarded = False

    @property
    def name(self) -> str:
        """Nom du serveur, repris dans le journal."""
        return self.spec.name

    @property
    def required(self) -> bool:
        """Vrai si le run ne peut pas se passer de ce serveur (``required``)."""
        return self.selection.required

    def __repr__(self) -> str:
        return f"McpSource({self.spec.name!r}, préfixe {self.selection.prefix!r})"

    @asynccontextmanager
    async def open(self, context: SourceContext) -> AsyncGenerator[Sequence[Tool]]:
        """Ouvre les outils du serveur pour un run.

        En portée ``shared`` ou ``tenant``, la connexion vient du pool et lui
        survit ; sinon elle est ouverte pour le run et fermée à la sortie.

        Args:
            context: Le contexte du run.

        Yields:
            Les outils retenus, sous leur nom préfixé.
        """
        pooled = self._pool is not None and self.spec.scope in POOLED
        server = self._pool.server(self.spec, self.pool_key, self._factory) if self._pool is not None and pooled else McpServer(self.spec, self._factory)
        try:
            listed = await server.tools()
            yield self.select(server, listed)
        finally:
            if not pooled:
                await server.aclose()

    def select(self, server: McpServer, listed: Sequence[types.Tool]) -> list[Tool]:
        """Outils exposés à l'agent, sous leur nom préfixé et avec leurs déclarations."""
        chosen = self.selection
        names = {tool.name for tool in listed}
        if not self._warned:
            self._warn_unknown(names)
            self._warned = True
        tools: list[McpTool] = []
        for tool in listed:
            if chosen.include is not None and tool.name not in chosen.include:
                continue
            if chosen.exclude is not None and tool.name in chosen.exclude:
                continue
            spec = self._spec(tool)
            if spec is not None:
                tools.append(McpTool(spec=spec, server=server, remote_name=tool.name))
        if not self._warned_unguarded:
            self._warn_unguarded(tools)
            self._warned_unguarded = True
        return list[Tool](tools)

    def _warn_unguarded(self, tools: Sequence[McpTool]) -> None:
        """Les outils irréversibles sans approbation : le dire, avec la ligne de config."""
        unguarded = [tool.remote_name for tool in tools if unguarded_spec(tool.spec)]
        if not unguarded:
            return
        logger.warning(
            "Serveur MCP %s : %s exécuté(s) sans approbation alors qu'irréversible(s) — sans "
            "annotation, la spec MCP tient un outil pour destructeur. Dans mcp_servers[].tools "
            "ou la référence de l'agent : approval: always pour demander un accord, ou "
            "side_effects: none | reversible pour dire ce qu'il fait vraiment.",
            self.spec.name,
            ", ".join(unguarded),
            extra={SETUP: True, REPORTED: True},
        )

    def _spec(self, tool: types.Tool) -> ToolSpec | None:
        """La définition d'un outil du serveur ; ``None`` pour un nom hors du format des API.

        Nom préfixé, description, schéma, effets lus dans ses annotations, puis
        les déclarations du serveur et de l'agent.
        """
        side_effects, idempotent = declared(tool.annotations)
        name = f"{self.selection.prefix}{MCP_PREFIX_SEPARATOR}{tool.name}"
        try:
            spec = ToolSpec(
                name=name,
                description=description(tool),
                input_schema=input_schema(tool),
                kind="mcp",
                side_effects=side_effects,
                idempotent=idempotent,
            )
        except ValidationError:
            logger.warning(
                "Outil MCP %r du serveur %s écarté : nom %r hors du format des API (lettres, chiffres, _ et -, 64 caractères au plus)",
                tool.name,
                self.spec.name,
                name,
            )
            return None
        return spec.overridden(self.spec.tools.get(tool.name)).overridden(self.selection.tools.get(tool.name))

    def _warn_unknown(self, names: set[str]) -> None:
        """Signale les outils cités par ``include``, ``exclude`` ou ``tools`` que le serveur ne connaît pas."""
        chosen = self.selection
        checks = {
            "include": chosen.include or (),
            "exclude": chosen.exclude or (),
            "tools (agent)": tuple(chosen.tools),
            "tools (serveur)": tuple(self.spec.tools),
        }
        for label, declared_names in checks.items():
            unknown = sorted(set(declared_names) - names)
            if unknown:
                logger.warning(
                    "Serveur MCP %s : %s cite des outils inconnus du serveur : %s",
                    self.spec.name,
                    label,
                    ", ".join(unknown),
                )
