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
from typing import Literal, Protocol, runtime_checkable

from pydantic import JsonValue

from loom_ia.core.model.content import ArtifactRefBlock, ToolOutput
from loom_ia.core.model.context import CallerContext
from loom_ia.core.model.ids import RunId, SessionId, TenantId
from loom_ia.core.model.tooling import SideEffects, ToolSpec
from loom_ia.core.ports.idempotency import IdempotencyStore


def idempotency_key(run_id: RunId, call_id: str) -> str:
    """Clé technique d'un appel : identique à chaque reprise du même appel (#18)."""
    return hashlib.sha256(f"{run_id}:{call_id}".encode()).hexdigest()


# Signalement d'un effet déjà mémorisé, avec la clé sous laquelle il l'est.
type ReuseNote = Callable[[str], None]


class SeenFiles(Protocol):
    """Les fichiers que l'agent a vus passer : pièces jointes, fichiers rendus par ses outils.

    C'est tout ce qu'un outil peut en relire : un fichier se désigne par le
    nom sous lequel le modèle l'a vu, jamais par une URI qu'on devinerait.
    """

    def find(self, name: str) -> ArtifactRefBlock | None:
        """Le dernier fichier de ce nom (nom donné, ou nom dans le stockage)."""
        ...

    def names(self) -> tuple[str, ...]:
        """Les noms des fichiers vus, sans doublon, pour dire ce qui existe."""
        ...

    async def read(self, file: ArtifactRefBlock, *, max_bytes: int) -> bytes:
        """Ses octets ; ``ValueError`` au-delà de ``max_bytes``, ``LookupError`` s'il manque."""
        ...


class RunResults(Protocol):
    """Les résultats d'outils du run en cours, en lecture (state v2, ``loom.state``).

    Un résultat se désigne par la référence que le modèle voit en tête de chacun
    (``result:3``) : seuls ceux du run, obtenus avant le tour en cours.
    """

    async def read(self, ref: str) -> JsonValue:
        """Ses données (``data``), sinon son texte ; ``ToolError`` qui dit pourquoi sinon."""
        ...


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
    # State de la session tel qu'au début du lot de l'appel, en lecture : les
    # clés que l'agent déclare, valeurs par défaut comprises. Ce qu'un voisin
    # du même lot écrit n'y est pas encore. Vide hors moteur, et dans un sous-run.
    state: Mapping[str, JsonValue] = field(default_factory=dict[str, JsonValue])
    # Fichiers de la conversation, en lecture (``SeenFiles``). Absent hors
    # moteur, et sans stockage d'artefacts.
    files: SeenFiles | None = field(default=None, compare=False)
    # Résultats du run, en lecture (``RunResults``). Absent hors moteur.
    results: RunResults | None = field(default=None, compare=False)
    # Vrai si le run tient le state de la session : un run racine ordinaire d'un
    # agent qui en déclare un (D3). Faux dans un sous-run et hors moteur.
    keeps_state: bool = False

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
class CallAssessment:
    """Ce qu'un appel précis demande, décidé par son outil d'après ses arguments.

    Un outil dont les effets dépendent de l'appel — lire ou supprimer, dans un
    dossier ou dans un autre — le dit appel par appel, plutôt qu'une
    déclaration fixe qui vaudrait pour le pire des cas. Pour cet appel, le
    moteur s'en sert à la place de ``spec.approval``, ``spec.side_effects`` et
    ``spec.idempotent`` pour approuver et pour reprendre un appel interrompu.
    Au rejeu et en évaluation, où l'appel est jugé avant que ses références et
    les politiques ne fixent ses arguments, elle ne peut que durcir la
    déclaration (``strictest_side_effects``).

    La décision ne dépend que des arguments et de la configuration de
    l'outil : elle est la même à chaque reprise, et au rejeu. Les effets sont
    toujours à dire : ils décident si l'appel peut être refait.
    """

    side_effects: SideEffects
    approval: Literal["never", "always"] = "never"
    idempotent: bool = False
    # Motif montré à l'approbateur quand ``approval`` vaut ``always``.
    reason: str | None = None

    @property
    def safe_to_retry(self) -> bool:
        """Vrai si refaire l'appel après une interruption est sans risque (#18)."""
        return self.side_effects == "none" or self.idempotent


@runtime_checkable
class AssessedTool(Protocol):
    """Outil qui évalue chacun de ses appels ; c'est facultatif.

    Un outil qui n'a pas ``assess_call`` garde sa déclaration fixe (``spec``)
    pour tous ses appels, et rien ne change pour lui. Celui qui l'a déclare
    quand même dans ``spec`` le pire de ses appels : c'est ce que voient ce qui
    ne regarde pas l'appel lui-même — la garde du journal durable, les
    politiques ``before_tool``, le rejeu et les évaluations.
    """

    def assess_call(self, arguments: Mapping[str, JsonValue]) -> CallAssessment | None:
        """L'évaluation de cet appel ; ``None`` : la déclaration fixe vaut."""
        ...


class _Declared(Protocol):
    @property
    def spec(self) -> ToolSpec: ...


_SEVERITY: dict[SideEffects, int] = {"none": 0, "reversible": 1, "irreversible": 2}


def strictest_side_effects(tool: _Declared, arguments: Mapping[str, JsonValue]) -> SideEffects:
    """Les effets de l'appel, l'évaluation ne pouvant que durcir la déclaration.

    Pour qui juge un appel avant que ses références ne soient résolues et que
    les politiques ne remplacent ses arguments (rejeu, évaluations) : les
    arguments vus ne sont pas forcément ceux qui partiront.
    """
    declared = tool.spec.side_effects
    found = assessed(tool, arguments)
    if found is None or _SEVERITY[found.side_effects] <= _SEVERITY[declared]:
        return declared
    return found.side_effects


def assessed(tool: object, arguments: Mapping[str, JsonValue]) -> CallAssessment | None:
    """L'évaluation d'un appel par son outil, ou ``None`` s'il n'en fait pas.

    Une évaluation qui échoue vaut la plus prudente — approbation exigée, effet
    irréversible : un défaut de l'outil ne doit pas laisser passer un appel
    qu'il aurait retenu.
    """
    if not isinstance(tool, AssessedTool):
        return None
    try:
        return tool.assess_call(arguments)
    except Exception as exc:
        return CallAssessment(
            approval="always",
            side_effects="irreversible",
            reason=f"évaluation de l'appel impossible ({type(exc).__name__}: {exc})",
        )


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
