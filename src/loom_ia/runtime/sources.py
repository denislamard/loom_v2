# SPDX-License-Identifier: Apache-2.0
"""Sources d'outils fournies par des paquets installés : le groupe ``loom_ia.tools`` (J6.4b).

Un paquet déclare dans ses métadonnées un point d'entrée qui désigne une
fabrique (``ToolSourceFactory``) :

    [project.entry-points."loom_ia.tools"]
    carnet = "carnet_devis:fabrique"

La config la déclare dans ``tool_sources`` (``name``, ``entry_point``,
``params``) et un agent la référence (``tools: [{source: carnet}]``).

**Rien n'est importé sans être demandé** : la liste des points d'entrée se lit
dans les métadonnées des paquets installés, sans importer aucun d'eux ; seul
le paquet d'une source qu'un agent référence est importé, au montage de cet
agent. Deux paquets qui déclarent le même nom sont refusés, chacun nommé.

La source rendue par la fabrique fournit ses outils sous leur nom court ;
``PackagedSource`` les préfixe, les choisit et leur applique les
déclarations de la config, comme ``McpSource`` le fait pour un serveur.
"""

import logging
import re
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from importlib.metadata import EntryPoint, entry_points
from typing import Final, cast

from loom_ia.config import ConfigError
from loom_ia.core.model import MCP_PREFIX_SEPARATOR, TOOL_NAME_PATTERN, ToolOverrides
from loom_ia.core.ports import SourceContext, Tool, ToolSource, ToolSourceFactory
from loom_ia.tools import ConfiguredTool

logger = logging.getLogger(__name__)

# Le groupe des points d'entrée que loom lit.
GROUP: Final = "loom_ia.tools"


@dataclass(frozen=True, slots=True)
class Installed:
    """Un point d'entrée installé, lu dans les métadonnées de son paquet sans l'importer."""

    name: str
    # Ce qu'il désigne : ``module:attribut``.
    value: str
    # Paquet qui le déclare, et sa version ; vides si les métadonnées ne le disent pas.
    package: str
    version: str


def installed() -> list[Installed]:
    """Les points d'entrée ``loom_ia.tools`` des paquets installés, par nom puis paquet."""
    found = [_installed(point) for point in entry_points(group=GROUP)]
    return sorted(found, key=lambda point: (point.name, point.package))


def _installed(point: EntryPoint) -> Installed:
    dist = point.dist
    return Installed(
        name=point.name,
        value=point.value,
        package=dist.name if dist is not None else "",
        version=dist.version if dist is not None else "",
    )


def source_factory(entry_point: str) -> ToolSourceFactory:
    """La fabrique qu'un point d'entrée désigne ; son paquet est importé à ce moment-là.

    ``ConfigError`` s'il n'est pas installé, si deux paquets le déclarent, si
    son paquet ne s'importe pas ou s'il ne désigne rien d'appelable.
    """
    points = [point for point in entry_points(group=GROUP) if point.name == entry_point]
    if not points:
        known = ", ".join(sorted({point.name for point in entry_points(group=GROUP)}))
        raise ConfigError(
            f"Point d'entrée {entry_point!r} absent du groupe {GROUP} : aucun paquet installé "
            f"ne le déclare (installés : {known or 'aucun'})"
        )
    if len(points) > 1:
        said = ", ".join(_said(_installed(point)) for point in points)
        raise ConfigError(
            f"Point d'entrée {entry_point!r} déclaré par plusieurs paquets : {said} — "
            "en désinstaller un, loom ne choisit pas"
        )
    [point] = points
    told = _said(_installed(point))
    try:
        found: object = point.load()
    except Exception as exc:
        raise ConfigError(
            f"Point d'entrée {entry_point!r} ({told}) : import impossible — "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    if not callable(found):
        raise ConfigError(
            f"Point d'entrée {entry_point!r} ({told}) : {point.value} n'est pas une fabrique "
            f"de source d'outils ({type(found).__name__})"
        )
    # Appelable : c'est l'appel, au montage, qui dira s'il rend une source.
    return cast(ToolSourceFactory, found)


def described(entry_point: str) -> str:
    """Le paquet et la cible d'un point d'entrée installé, pour un message ; vide sinon."""
    points = [point for point in entry_points(group=GROUP) if point.name == entry_point]
    return _said(_installed(points[0])) if len(points) == 1 else ""


def _said(point: Installed) -> str:
    package = f"{point.package} {point.version}".strip() or "paquet inconnu"
    return f"{package}, {point.value}"


class PackagedSource:
    """Une source d'un paquet, telle qu'un agent la référence : préfixe, choix, déclarations."""

    def __init__(
        self,
        inner: ToolSource,
        *,
        name: str,
        prefix: str,
        include: tuple[str, ...] | None = None,
        exclude: tuple[str, ...] | None = None,
        required: bool = False,
        declared: Mapping[str, ToolOverrides] | None = None,
        chosen: Mapping[str, ToolOverrides] | None = None,
    ) -> None:
        self.inner = inner
        # Nom de la source dans la config, repris au journal.
        self.name = name
        self.prefix = prefix
        self.include = include
        self.exclude = exclude
        self.required = required
        # Déclarations de ``tool_sources[].tools``, puis de la référence de l'agent.
        self.declared: Mapping[str, ToolOverrides] = declared or {}
        self.chosen: Mapping[str, ToolOverrides] = chosen or {}
        self._warned = False

    def __repr__(self) -> str:
        return f"PackagedSource({self.name!r}, préfixe {self.prefix!r})"

    @asynccontextmanager
    async def open(self, context: SourceContext) -> AsyncGenerator[Sequence[Tool]]:
        async with self.inner.open(context) as provided:
            yield self.select(provided)

    def select(self, provided: Sequence[Tool]) -> list[Tool]:
        """Les outils exposés à l'agent, sous leur nom préfixé et avec leurs déclarations."""
        if not self._warned:
            self._warn_unknown({tool.spec.name for tool in provided})
            self._warned = True
        tools: list[Tool] = []
        for tool in provided:
            short = tool.spec.name
            if self.include is not None and short not in self.include:
                continue
            if self.exclude is not None and short in self.exclude:
                continue
            name = f"{self.prefix}{MCP_PREFIX_SEPARATOR}{short}"
            if re.fullmatch(TOOL_NAME_PATTERN, name) is None:
                logger.warning(
                    "Outil %r de la source %s écarté : nom %r hors du format des API "
                    "(lettres, chiffres, _ et -, 64 caractères au plus)",
                    short,
                    self.name,
                    name,
                )
                continue
            # Un outil de paquet est du code Python : il n'emprunte ni le
            # chemin d'un rôle, ni celui d'un sous-agent, ni celui de MCP.
            spec = (
                tool.spec.model_copy(update={"name": name, "kind": "python"})
                .overridden(self.declared.get(short))
                .overridden(self.chosen.get(short))
            )
            tools.append(ConfiguredTool(tool=tool, spec=spec))
        return tools

    def _warn_unknown(self, names: set[str]) -> None:
        checks = {
            "include": self.include or (),
            "exclude": self.exclude or (),
            "tools (agent)": tuple(self.chosen),
            "tools (source)": tuple(self.declared),
        }
        for label, declared_names in checks.items():
            unknown = sorted(set(declared_names) - names)
            if unknown:
                logger.warning(
                    "Source d'outils %s : %s cite des outils inconnus de la source : %s",
                    self.name,
                    label,
                    ", ".join(unknown),
                )

    async def aclose(self) -> None:
        """Ferme la source du paquet si elle a de quoi l'être."""
        closing: object = getattr(self.inner, "aclose", None)
        if callable(closing):
            await cast(Callable[[], Awaitable[None]], closing)()
