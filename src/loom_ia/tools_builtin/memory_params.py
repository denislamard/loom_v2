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

type MemoryAction = Literal[
    "search", "get", "list_docs", "projects", "add_text", "add_url", "add_file", "update", "delete"
]
type ActionApproval = Literal["never", "always"]

# Dans l'ordre où on les dit.
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
READS: Final[frozenset[MemoryAction]] = frozenset({"search", "get", "list_docs", "projects"})
DEFAULT_APPROVAL: Final[Mapping[MemoryAction, ActionApproval]] = {
    action: "never" if action in READS else "always" for action in ACTIONS
}
# Un projet s'écrit en minuscules : l'outil range et cherche les projets ainsi.
PROJECT_PATTERN: Final = r"^[a-z0-9][a-z0-9_.-]{0,63}$"
# L'extra de loom-ia qui apporte les vrais modèles.
MODELS_EXTRA: Final = "memory"


def _no_expansion(path: Path, where: str) -> None:
    said = str(path)
    if said.startswith("~") or "$" in said:
        raise ValueError(
            f"{where} : {said!r} — ni « ~ » ni variable ($HOME…) dans 'path', qui n'est pas "
            "interprété : donner un chemin absolu, ou relatif au fichier de config"
        )


class StoreSpec(BaseModel):
    """Le fichier SQLite de la mémoire : son chemin, ou la variable qui le porte."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # Absolu, ou relatif au dossier du fichier de config. Ni « ~ » ni variable.
    path: Path | None = None
    # Variable d'environnement qui porte le chemin, lue dans les secrets du client.
    path_env: str | None = Field(default=None, pattern=ENV_NAME_PATTERN)

    @model_validator(mode="after")
    def _check(self) -> Self:
        if (self.path is None) == (self.path_env is None):
            raise ValueError("store : donner 'path' ou 'path_env', l'un des deux")
        if self.path is not None:
            _no_expansion(self.path, "store")
        return self


class MemoryFolder(BaseModel):
    """Un dossier où ``add_file`` peut lire : son nom pour le modèle, son chemin."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(pattern=FOLDER_NAME_PATTERN)
    # Absolu, ou relatif au dossier du fichier de config. Ni « ~ » ni variable.
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
        return (
            f"{parts.hostname} n'est pas la boucle locale (127.0.0.1, localhost, ::1) : "
            "l'accès par le réseau viendra plus tard"
        )
    if port is None:
        return "le port est attendu (http://127.0.0.1:8765)"
    return None


class ServerSpec(BaseModel):
    """``loom-mem-gpu`` : l'application qui garde les modèles en VRAM et calcule pour loom."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # L'adresse, ou la variable qui la porte (lue dans les secrets du client).
    url: str | None = None
    url_env: str | None = Field(default=None, pattern=ENV_NAME_PATTERN)
    # La variable qui porte le jeton, s'il en faut un ; jamais le jeton lui-même.
    token_env: str | None = Field(default=None, pattern=ENV_NAME_PATTERN)
    # Secondes : une application arrêtée ne bloque pas chaque appel ; un lot, au plus.
    connect_timeout: PositiveFloat = 2.0
    read_timeout: PositiveFloat = 60.0

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
        return tuple(name for name in (self.url_env, self.token_env) if name is not None)


# Ce que ``server`` règle sur l'application : refusé à côté de lui.
_SET_BY_SERVER: Final = ("device", "fp16", "offline")


class ModelsSpec(BaseModel):
    """Les modèles d'embedding et de reranking, partagés par tout le process."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # ``fake`` : modèles factices et déterministes, sans torch (essais, démonstrations).
    kind: Literal["bge-m3", "fake"] = "bge-m3"
    # Nom Hugging Face ou dossier local.
    embedder: str = Field(default="BAAI/bge-m3", min_length=1)
    reranker: str = Field(default="BAAI/bge-reranker-v2-m3", min_length=1)
    # ``auto`` : cuda si la carte est prise en charge, sinon mps, sinon cpu.
    device: str = Field(default="auto", pattern=r"^(auto|cpu|mps|cuda(:[0-9]+)?)$")
    # Demi-précision sur GPU ; sans effet sur CPU, qui calcule en float32.
    fp16: bool = True
    # Charger les modèles en tâche de fond dès le montage de l'agent, au lieu du premier appel.
    preload: bool = False
    # Lire les modèles dans le cache local de Hugging Face seulement : aucune requête vers le
    # Hub. Un modèle absent du cache fait échouer le chargement (``hf download`` l'y met).
    offline: bool = False
    # Calculs confiés à loom-mem-gpu ; ``embedder`` et ``reranker`` sont alors les modèles
    # qu'elle doit annoncer.
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
                "(ses options), pas ici"
            )
        if "preload" in self.model_fields_set:
            raise ValueError(
                "models : avec server, les modèles sont chargés par loom-mem-gpu à son "
                "démarrage ; preload ne sert qu'au calcul dans loom"
            )
        return self


class RetrievalSpec(BaseModel):
    """Réglages de la recherche : candidats, reranking, extraits."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # Candidats retenus par chacune des deux recherches (dense, lexicale) avant fusion.
    prefetch_limit: PositiveInt = 15
    # Candidats passés au reranker, le poste de coût dominant sur CPU.
    rerank_candidates: PositiveInt = 20
    max_chunks_per_doc: PositiveInt = 2
    # Longueur de l'extrait rendu quand le passage est trop long pour être rendu entier.
    snippet_chars: PositiveInt = 300
    # En dessous, search rend le passage entier.
    full_chunk_chars: PositiveInt = 1000
    # Score du reranker (0 à 1) sous lequel un passage n'est pas rendu.
    min_score: float = Field(default=0.1, ge=0.0, le=1.0)


class ChunkingSpec(BaseModel):
    """Découpage des documents, en caractères (environ 4 par token en français)."""

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
    """Une action permise, et son approbation si elle n'est pas celle par défaut."""

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
    """Paramètres de ``loom.memory``."""

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
            raise ValueError(
                "'projects' liste les projets de la base : sans objet quand 'project' est fixé"
            )
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
