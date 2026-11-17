# SPDX-License-Identifier: Apache-2.0
"""Paramètres de ``loom.memory`` : la base, le projet, les modèles et les actions permises.

Module léger — Pydantic seulement — : la config l'importe pour valider la
référence au chargement, sans importer l'outil, ni torch, ni transformers.

    - python: loom.memory
      store: { path: memoire/agent.db }        # ou { path_env: MEMOIRE_BASE }
      project: documentation                   # fixé : le modèle ne le choisit pas
      folders:                                 # pour add_file seulement
      - { name: workspace, path: /home/denis/temp/agent/workspace }
      models: { device: cpu, preload: true, offline: true }
      # ou, calcul confié à loom-mem-gpu : models: { server: { url: http://127.0.0.1:8765 } }
      retrieval: { rerank_candidates: 8 }
      actions:
        search: {}
        get: {}
        add_text: {}                           # écriture : approbation always par défaut
        add_file: {}
        update: {}
        delete: {}

``actions`` liste les actions **permises** ; une action absente n'est ni
montrée au modèle ni exécutée. Chacune prend son approbation par défaut :
``never`` pour une lecture (``search``, ``get``, ``list_docs``, ``projects``),
``always`` pour une écriture (``add_text``, ``add_url``, ``add_file``,
``update``, ``delete``). ``{approval: never}`` ou ``{approval: always}`` la
change. Une liste (``actions: [search, get]``) vaut des actions aux défauts.

``store`` désigne le fichier SQLite de la mémoire : ``path``, absolu ou
relatif au fichier de config, ou ``path_env``, la variable qui le porte — lue
dans les secrets du client, pour qu'un client ait sa base. L'outil crée le
fichier s'il manque, jamais son dossier.

``models.server`` confie les calculs à ``loom-mem-gpu``, qui garde les modèles
en VRAM (``docs/loom-mem-gpu.md``) : ``device``, ``fp16``, ``offline`` et
``preload`` se règlent alors sur l'application, et sont refusés ici.

``project`` fixé, le modèle n'a pas d'argument ``project`` : tout ce qu'il
écrit va dans ce projet, et il ne lit, ne modifie ni ne supprime rien d'un
autre. Sans lui, le modèle donne le projet à chaque écriture et peut filtrer
ses recherches.
"""

import ipaddress
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Annotated, Any, Final, Literal, Self, cast
from urllib.parse import urlsplit

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    NonNegativeInt,
    PositiveFloat,
    PositiveInt,
    WithJsonSchema,
    model_validator,
)

from loom_ia.tools_builtin.local_files_params import FOLDER_NAME_PATTERN
from loom_ia.tools_builtin.sql_params import ENV_NAME_PATTERN

type MemoryAction = Literal["search", "get", "list_docs", "projects", "add_text", "add_url", "add_file", "update", "delete"]
"""Action de ``loom.memory``."""
type ActionApproval = Literal["never", "always"]
"""Approbation d'une action : ``never`` ou ``always``."""

ACTIONS: Final[tuple[MemoryAction, ...]] = (
    "search",
    "get",
    "list_docs",
    "projects",
    "add_text",
    "add_url",
    "add_file",
    "update",
    "delete",
)
"""Dans l'ordre où on les dit."""
READS: Final[frozenset[MemoryAction]] = frozenset({"search", "get", "list_docs", "projects"})
"""Les actions qui lisent."""
DEFAULT_APPROVAL: Final[Mapping[MemoryAction, ActionApproval]] = {action: "never" if action in READS else "always" for action in ACTIONS}
"""Approbation par défaut : ``never`` pour une lecture, ``always`` pour une écriture."""
PROJECT_PATTERN: Final = r"^[a-z0-9][a-z0-9_.-]{0,63}$"
"""Un projet s'écrit en minuscules : l'outil range et cherche les projets ainsi."""
MODELS_EXTRA: Final = "memory"
"""L'extra de loom-ia qui apporte les vrais modèles."""


def _no_expansion(path: Path, where: str) -> None:
    said = str(path)
    if said.startswith("~") or "$" in said:
        raise ValueError(
            f"{where} : {said!r} — ni « ~ » ni variable ($HOME…) dans 'path', qui n'est pas "
            "interprété : donner un chemin absolu, ou relatif au fichier de config"
        )


class StoreSpec(BaseModel):
    """Le fichier SQLite de la mémoire : son chemin, ou la variable qui le porte.

    Attributes:
        path: Chemin du fichier : absolu, ou relatif au dossier du fichier de
            config. Ni « ~ » ni variable.
        path_env: Variable d'environnement qui porte le chemin, lue dans les
            secrets du client.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: Path | None = None
    path_env: str | None = Field(default=None, pattern=ENV_NAME_PATTERN)

    @model_validator(mode="after")
    def _check(self) -> Self:
        if (self.path is None) == (self.path_env is None):
            raise ValueError("store : donner 'path' ou 'path_env', l'un des deux")
        if self.path is not None:
            _no_expansion(self.path, "store")
        return self


class MemoryFolder(BaseModel):
    """Un dossier où ``add_file`` peut lire : son nom pour le modèle, son chemin.

    Attributes:
        name: Nom du dossier pour le modèle.
        path: Absolu, ou relatif au dossier du fichier de config. Ni « ~ » ni
            variable.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(pattern=FOLDER_NAME_PATTERN)
    path: Path

    @model_validator(mode="after")
    def _check(self) -> Self:
        _no_expansion(self.path, f"dossier {self.name!r}")
        return self


def is_loopback(host: str) -> bool:
    """Vrai pour une adresse de la boucle locale (ou ``localhost``)."""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def server_url_problem(url: str) -> str | None:
    """Pourquoi l'adresse de ``loom-mem-gpu`` est refusée, ou ``None``.

    ``http://`` sur la boucle locale seulement : l'accès par le réseau (https,
    jeton obligatoire) viendra plus tard.
    """
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError as exc:
        return f"adresse illisible ({exc})"
    if parts.scheme != "http" or not parts.hostname:
        return "une adresse http://<hôte>:<port> est attendue"
    if parts.path not in ("", "/") or parts.query or parts.fragment or parts.username:
        return "seulement http://<hôte>:<port>, sans chemin ni paramètres"
    if not is_loopback(parts.hostname):
        return f"{parts.hostname} n'est pas la boucle locale (127.0.0.1, localhost, ::1) : l'accès par le réseau viendra plus tard"
    if port is None:
        return "le port est attendu (http://127.0.0.1:8765)"
    return None


DEVICE_PATTERN: Final = r"^(auto|cpu|mps|cuda(:[0-9]+)?)$"
"""Le device des modèles, et les défauts des réglages de calcul : une seule
définition pour ``models``, ``server.fallback`` et les réglages de
loom-mem-gpu.
"""
DEFAULT_DEVICE: Final = "auto"
"""Device des modèles sans réglage : choisi selon la machine."""
DEFAULT_FP16: Final = True
"""Calcul en demi-précision, sans réglage."""
DEFAULT_OFFLINE: Final = False
"""Modèles téléchargés au besoin, sans réglage."""


class FallbackSpec(BaseModel):
    """Le repli dans loom quand ``loom-mem-gpu`` ne répond pas : où calculer, et comment.

    Attributes:
        device: Où calculer : ``auto``, ``cpu``, ``mps`` ou ``cuda[:n]`` ; même
            défaut que dans ``models``.
        fp16: Demi-précision sur GPU ; même défaut que dans ``models``.
        offline: Modèles lus dans le cache local seulement ; même défaut que
            dans ``models``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    device: str = Field(default=DEFAULT_DEVICE, pattern=DEVICE_PATTERN)
    fp16: bool = DEFAULT_FP16
    offline: bool = DEFAULT_OFFLINE


class ServerSpec(BaseModel):
    """``loom-mem-gpu`` : l'application qui garde les modèles en VRAM et calcule pour loom.

    Attributes:
        url: Adresse de l'application.
        url_env: Variable qui porte l'adresse, lue dans les secrets du client.
        token_env: La variable qui porte le jeton, s'il en faut un ; jamais le
            jeton lui-même.
        connect_timeout: Délai de connexion, en secondes : une application
            arrêtée ne bloque pas chaque appel.
        read_timeout: Délai de réponse, en secondes : un lot, au plus.
        fallback: Calcul dans ce process quand l'application ne répond pas ;
            absent : pas de repli, une erreur qui dit comment la démarrer.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    url: str | None = None
    url_env: str | None = Field(default=None, pattern=ENV_NAME_PATTERN)
    token_env: str | None = Field(default=None, pattern=ENV_NAME_PATTERN)
    connect_timeout: PositiveFloat = 2.0
    read_timeout: PositiveFloat = 60.0
    fallback: FallbackSpec | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        if (self.url is None) == (self.url_env is None):
            raise ValueError("server : donner 'url' ou 'url_env', l'un des deux")
        if self.url is not None:
            problem = server_url_problem(self.url)
            if problem is not None:
                raise ValueError(f"server.url {self.url!r} : {problem}")
        return self

    def secret_names(self) -> tuple[str, ...]:
        """Les variables que le serveur lit : ``url_env`` et ``token_env``."""
        return tuple(name for name in (self.url_env, self.token_env) if name is not None)


_SET_BY_SERVER: Final = ("device", "fp16", "offline")
"""Ce que ``server`` règle sur l'application : refusé à côté de lui."""


class ModelsSpec(BaseModel):
    """Les modèles d'embedding et de reranking, partagés par tout le process.

    Attributes:
        kind: ``bge-m3``, ou ``fake`` : modèles factices et déterministes, sans
            torch (essais, démonstrations).
        embedder: Modèle d'embedding : nom Hugging Face ou dossier local.
        reranker: Modèle de reranking : nom Hugging Face ou dossier local.
        device: ``auto`` : cuda si la carte est prise en charge, sinon mps,
            sinon cpu.
        fp16: Demi-précision sur GPU ; sans effet sur CPU, qui calcule en
            float32.
        preload: Charger les modèles en tâche de fond dès le montage de
            l'agent, au lieu du premier appel.
        offline: Lire les modèles dans le cache local de Hugging Face seulement
            : aucune requête vers le Hub. Un modèle absent du cache fait
            échouer le chargement (``hf download`` l'y met).
        server: Calculs confiés à loom-mem-gpu ; ``embedder`` et ``reranker``
            sont alors les modèles qu'elle doit annoncer.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["bge-m3", "fake"] = "bge-m3"
    embedder: str = Field(default="BAAI/bge-m3", min_length=1)
    reranker: str = Field(default="BAAI/bge-reranker-v2-m3", min_length=1)
    device: str = Field(default=DEFAULT_DEVICE, pattern=DEVICE_PATTERN)
    fp16: bool = DEFAULT_FP16
    preload: bool = False
    offline: bool = DEFAULT_OFFLINE
    server: ServerSpec | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.server is None:
            return self
        if self.kind == "fake":
            raise ValueError("models : server ne sert que les vrais modèles (kind: bge-m3)")
        given = [name for name in _SET_BY_SERVER if name in self.model_fields_set]
        if given:
            raise ValueError(
                f"models : avec server, {', '.join(given)} se règle(nt) sur loom-mem-gpu "
                "(ses options) ; pour le repli dans loom, les mettre dans server.fallback"
            )
        if "preload" in self.model_fields_set:
            raise ValueError("models : avec server, les modèles sont chargés par loom-mem-gpu à son démarrage ; preload ne sert qu'au calcul dans loom")
        return self

    def fallback(self) -> ModelsSpec | None:
        """Les modèles du repli dans loom, réglés par ``server.fallback`` ; ``None`` sans repli."""
        if self.server is None or self.server.fallback is None:
            return None
        found = self.server.fallback
        return ModelsSpec(
            kind=self.kind,
            embedder=self.embedder,
            reranker=self.reranker,
            device=found.device,
            fp16=found.fp16,
            offline=found.offline,
        )


class RetrievalSpec(BaseModel):
    """Réglages de la recherche : candidats, reranking, extraits.

    Attributes:
        prefetch_limit: Candidats retenus par chacune des deux recherches
            (dense, lexicale) avant fusion.
        rerank_candidates: Candidats passés au reranker, le poste de coût
            dominant sur CPU.
        max_chunks_per_doc: Passages rendus au plus par document.
        snippet_chars: Longueur de l'extrait rendu quand le passage est trop
            long pour être rendu entier.
        full_chunk_chars: En dessous de cette longueur, search rend le passage
            entier.
        min_score: Score du reranker (0 à 1) sous lequel un passage n'est pas
            rendu.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    prefetch_limit: PositiveInt = 15
    rerank_candidates: PositiveInt = 20
    max_chunks_per_doc: PositiveInt = 2
    snippet_chars: PositiveInt = 300
    full_chunk_chars: PositiveInt = 1000
    min_score: float = Field(default=0.1, ge=0.0, le=1.0)


class ChunkingSpec(BaseModel):
    """Découpage des documents, en caractères (environ 4 par token en français).

    Attributes:
        target_chars: Longueur visée d'un passage.
        max_chars: Longueur au plus d'un passage.
        overlap_chars: Recouvrement entre deux passages qui se suivent.
        min_chars: Longueur au moins d'un passage.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    target_chars: PositiveInt = 1800
    max_chars: PositiveInt = 2400
    overlap_chars: NonNegativeInt = 180
    min_chars: NonNegativeInt = 200

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.target_chars > self.max_chars:
            raise ValueError("chunking : target_chars ne peut pas dépasser max_chars")
        if self.overlap_chars >= self.target_chars:
            raise ValueError("chunking : overlap_chars doit rester sous target_chars")
        return self


class ActionSpec(BaseModel):
    """Une action permise, et son approbation si elle n'est pas celle par défaut.

    Attributes:
        approval: Approbation de l'action ; absente : celle par défaut
            (``never`` pour une lecture, ``always`` pour une écriture).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    approval: ActionApproval | None = None


_ACTIONS_SCHEMA: Final[dict[str, Any]] = {
    "description": (
        "Actions permises ; une action absente est refusée. En liste, chacune garde son "
        "approbation par défaut (lectures : never ; écritures : always) ; en objet, "
        "{action: {approval: never | always}}."
    ),
    "anyOf": [
        {"type": "array", "items": {"enum": list(ACTIONS)}, "uniqueItems": True, "minItems": 1},
        {
            "type": "object",
            "propertyNames": {"enum": list(ACTIONS)},
            "additionalProperties": {
                "type": "object",
                "properties": {"approval": {"enum": ["never", "always"]}},
                "additionalProperties": False,
            },
            "minProperties": 1,
        },
    ],
}


def _actions(value: object) -> object:
    # Une liste vaut des actions aux réglages par défaut.
    if isinstance(value, list | tuple):
        names = cast(Sequence[object], value)
        found: dict[str, dict[str, object]] = {str(name): {} for name in names}
        return found
    return value


def _some(actions: dict[MemoryAction, ActionSpec]) -> dict[MemoryAction, ActionSpec]:
    if not actions:
        raise ValueError("au moins une action est attendue")
    return actions


def _unique_folders(folders: tuple[MemoryFolder, ...]) -> tuple[MemoryFolder, ...]:
    names = [folder.name for folder in folders]
    doubles = sorted({name for name in names if names.count(name) > 1})
    if doubles:
        raise ValueError(f"nom de dossier donné deux fois : {', '.join(doubles)}")
    return folders


class MemoryParams(BaseModel):
    """Paramètres de ``loom.memory``.

    Attributes:
        store: Le fichier SQLite de la mémoire.
        project: Projet où l'outil range et cherche ; en minuscules.
        folders: Dossiers où ``add_file`` peut lire, de noms distincts.
        models: Modèles d'embedding et de reranking.
        retrieval: Réglages de la recherche.
        chunking: Découpage des documents.
        actions: Actions permises (``search``, ``get``, ``list_docs``,
            ``projects``, ``add_text``, ``add_url``, ``add_file``, ``update``,
            ``delete``), et l'approbation de chacune.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    store: StoreSpec
    project: str | None = Field(default=None, pattern=PROJECT_PATTERN)
    folders: Annotated[tuple[MemoryFolder, ...], AfterValidator(_unique_folders)] = ()
    models: ModelsSpec = ModelsSpec()
    retrieval: RetrievalSpec = RetrievalSpec()
    chunking: ChunkingSpec = ChunkingSpec()
    # Validateurs de champ, non de modèle : la référence de l'outil reprend les
    # champs de ce modèle tels quels (``agents.spec``), avec leurs validateurs.
    actions: Annotated[
        dict[MemoryAction, ActionSpec],
        BeforeValidator(_actions),
        AfterValidator(_some),
        WithJsonSchema(_ACTIONS_SCHEMA),
    ]

    @model_validator(mode="after")
    def _check(self) -> Self:
        if "add_file" in self.actions and not self.folders:
            raise ValueError("add_file attend au moins un dossier dans 'folders'")
        if self.folders and "add_file" not in self.actions:
            raise ValueError("'folders' ne sert qu'à add_file, qui n'est pas dans 'actions'")
        if self.project is not None and "projects" in self.actions:
            raise ValueError("'projects' liste les projets de la base : sans objet quand 'project' est fixé")
        return self

    def declared(self) -> tuple[MemoryAction, ...]:
        """Les actions permises, dans l'ordre où on les dit."""
        return tuple(action for action in ACTIONS if action in self.actions)

    def approval(self, action: MemoryAction) -> ActionApproval:
        """L'approbation de l'action : celle de la config, sinon celle par défaut."""
        given = self.actions[action].approval
        return DEFAULT_APPROVAL[action] if given is None else given

    def secret_names(self) -> tuple[str, ...]:
        """Les variables que l'outil lit : un client peut les rediriger (``secrets``)."""
        names = () if self.store.path_env is None else (self.store.path_env,)
        server = self.models.server
        return names if server is None else (*names, *server.secret_names())
