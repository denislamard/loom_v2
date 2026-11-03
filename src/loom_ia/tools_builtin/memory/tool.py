# SPDX-License-Identifier: Apache-2.0
"""L'outil ``memory`` monté : ses actions, leur approbation, leur exécution.

L'approbation se décide appel par appel (``assess_call``) d'après l'action et
la config ; la déclaration fixe (``spec``) dit le pire des actions permises,
pour ce qui ne regarde pas l'appel lui-même.

| Action | Effet | Idempotente |
| --- | --- | --- |
| search, get, list_docs, projects | aucun | — |
| add_text, add_url, add_file | réversible | oui : même contenu ou même source, rien de doublé |
| update | réversible | oui : le même texte ne réécrit rien |
| delete | irréversible | non |
"""

import asyncio
import importlib.util
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Final, cast

from pydantic import BaseModel, JsonValue

from loom_ia.core.model import SideEffects, ToolOutput, ToolSpec
from loom_ia.core.ports import CallAssessment, ToolContext, ToolError
from loom_ia.tools import to_output
from loom_ia.tools_builtin import (
    BuiltinContext,
    Grant,
    Place,
    Protected,
    Settings,
)
from loom_ia.tools_builtin._folders import Folders, Located, mount
from loom_ia.tools_builtin.local_files_params import FolderSpec
from loom_ia.tools_builtin.memory.arguments import NAME, MemoryArguments, parse, schema
from loom_ia.tools_builtin.memory.chunk import content_hash
from loom_ia.tools_builtin.memory.models import shared_models
from loom_ia.tools_builtin.memory.service import (
    MIN_TITLE_CHARS,
    Extracted,
    MemoryService,
    clean_project,
)
from loom_ia.tools_builtin.memory.store import MemoryStore
from loom_ia.tools_builtin.memory_params import READS, MemoryAction, MemoryParams

# add_file : ce qu'on lit d'un fichier, et lesquels.
MAX_FILE_BYTES: Final = 1_000_000
# Délai d'un appel, en secondes, quand l'agent n'en donne pas (``timeout``). Indexer une
# page sur CPU prend de l'ordre de 1,5 s par passage, et le premier appel peut charger les
# modèles : le délai commun des outils (30 s) n'y suffit pas.
TIMEOUT: Final = 300.0
FILE_SUFFIXES: Final = frozenset({".md", ".markdown", ".txt"})
_H1: Final = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)

_EFFECTS: Final[Mapping[MemoryAction, tuple[SideEffects, bool]]] = {
    "search": ("none", True),
    "get": ("none", True),
    "list_docs": ("none", True),
    "projects": ("none", True),
    "add_text": ("reversible", True),
    "add_url": ("reversible", True),
    "add_file": ("reversible", True),
    "update": ("reversible", True),
    "delete": ("irreversible", False),
}
_SEVERITY: Final[Mapping[SideEffects, int]] = {"none": 0, "reversible": 1, "irreversible": 2}
_SAID: Final[Mapping[MemoryAction, str]] = {
    "search": "chercher dans la mémoire",
    "get": "lire un document de la mémoire",
    "list_docs": "lister les documents de la mémoire",
    "projects": "lister les projets de la mémoire",
    "add_text": "mémoriser un texte",
    "add_url": "mémoriser une page web",
    "add_file": "mémoriser un fichier",
    "update": "remplacer le texte d'un document",
    "delete": "supprimer un document",
}


class MemoryTool:
    """L'outil ``memory`` monté sur sa base, ses modèles et ses dossiers."""

    def __init__(
        self,
        params: MemoryParams,
        store: MemoryStore,
        folders: Folders | None,
    ) -> None:
        self.params = params
        self.folders = folders
        models = shared_models(params.models)
        self.service = MemoryService(store, models, params.retrieval, params.chunking)
        declared = params.declared()
        effects: SideEffects = "none"
        for action in declared:
            found = _EFFECTS[action][0]
            if _SEVERITY[found] > _SEVERITY[effects]:
                effects = found
        self._spec = ToolSpec(
            name=NAME,
            description=self._description(),
            input_schema=schema(params),
            kind="python",
            # Le pire des actions permises ; chaque appel est évalué à part.
            side_effects=effects,
            approval="always" if any(params.approval(a) == "always" for a in declared) else "never",
            timeout=TIMEOUT,
        )
        if params.models.preload:
            models.preload()

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    @property
    def store(self) -> MemoryStore:
        return self.service.store

    # --- Évaluation --------------------------------------------------------------------

    def assess_call(self, arguments: Mapping[str, JsonValue]) -> CallAssessment:
        """Approbation et effets de cet appel, d'après son action et la config."""
        try:
            request = parse(arguments, self.params)
        except ToolError:
            # L'appel sera refusé à l'exécution, pour la même raison : aucun effet.
            return CallAssessment(side_effects="none")
        if request.action == "add_file":
            try:
                # Dossier, droit et extension : un fichier qui ne peut pas être lu n'est
                # pas soumis à un humain. Le contenu, lui, est lu à l'exécution : il peut
                # changer pendant l'attente, et l'exécution refait ces contrôles.
                self._file_target(cast(str, request.file))
            except ToolError:
                return CallAssessment(side_effects="none")
        if request.action in ("update", "delete") and self._writes_nothing(request):
            return CallAssessment(side_effects="none")
        if request.action == "add_text" and self._adds_nothing(request):
            return CallAssessment(side_effects="none")
        effects, idempotent = _EFFECTS[request.action]
        if self.params.approval(request.action) == "never":
            return CallAssessment(side_effects=effects, idempotent=idempotent)
        return CallAssessment(
            approval="always",
            side_effects=effects,
            idempotent=idempotent,
            reason=f"{_SAID[request.action]}{self._what(request)} — la config exige une "
            f"approbation pour {request.action}",
        )

    def _writes_nothing(self, request: MemoryArguments) -> bool:
        """Vrai si ``update`` ou ``delete`` ne peut rien écrire ; au moindre doute, faux.

        Document inconnu, ou hors du projet imposé : l'appel sera refusé. ``update``
        d'un texte vide sera refusé ; d'un texte inchangé (casse et espaces mis à part),
        il n'écrit rien ; du texte d'un autre document, il est refusé. La base est lue
        sans être créée ; illisible, l'approbation est demandée comme d'habitude.
        """
        digest: str | None = None
        if request.action == "update":
            text = (request.text or "").strip()
            if not text:
                return True
            digest = content_hash(text)
        seen = self.store.peek(cast(str, request.doc_id).strip(), digest)
        if seen is None:
            return False
        project = self._project(request)
        if seen.doc is None or (project is not None and seen.doc.project != project):
            return True
        return seen.same is not None

    def _adds_nothing(self, request: MemoryArguments) -> bool:
        """Vrai si ``add_text`` ne peut rien écrire ; au moindre doute, faux.

        Titre trop court, texte vide ou projet absent : l'appel sera refusé. Un texte
        que le projet a déjà (casse et espaces mis à part) : l'exécution rend
        ``duplicate_of`` sans rien écrire. La base est lue sans être créée.
        """
        title = (request.title or "").strip()
        text = (request.text or "").strip()
        place = clean_project(self._project(request))
        if len(title) < MIN_TITLE_CHARS or not text or place is None:
            return True
        seen = self.store.peek_content(content_hash(text), place)
        return seen is not None and seen.same is not None

    def _what(self, request: MemoryArguments) -> str:
        project = self._project(request)
        where = f" dans le projet {project}" if project else ""
        match request.action:
            case "add_text":
                return f" « {request.title} »{where}"
            case "add_url":
                return f" ({request.url}){where}"
            case "add_file":
                return f" ({request.file}){where}"
            case "update" | "delete" | "get":
                return f" ({request.doc_id})"
            case _:
                return where

    # --- Exécution ---------------------------------------------------------------------

    async def invoke(self, arguments: dict[str, JsonValue], context: ToolContext) -> ToolOutput:
        request = parse(arguments, self.params)
        project = self._project(request)
        tags = request.tags or []
        service = self.service
        match request.action:
            case "search":
                found = await service.search(
                    cast(str, request.query), project=project, tags=tags, k=request.k
                )
                return to_output(found)
            case "get":
                return to_output(await service.get(cast(str, request.doc_id), project=project))
            case "list_docs":
                return to_output(await service.list_docs(project=project, n=request.n))
            case "projects":
                return to_output(await service.projects())
            case "add_text":
                extracted = Extracted(cast(str, request.title), cast(str, request.text))
                done = await service.add(
                    extracted, kind="text", source=None, project=project, tags=tags
                )
                return to_output(done)
            case "add_url":
                from loom_ia.tools_builtin.memory.web import fetch_markdown

                url = cast(str, request.url).strip()
                page = await fetch_markdown(url)
                done = await service.add(page, kind="url", source=url, project=project, tags=tags)
                return to_output(done)
            case "add_file":
                address, extracted = await asyncio.to_thread(
                    self._read_file, cast(str, request.file)
                )
                done = await service.add(
                    extracted, kind="file", source=address, project=project, tags=tags
                )
                return to_output(done)
            case "update":
                done = await service.update(
                    cast(str, request.doc_id), cast(str, request.text), project=project
                )
                return to_output(done)
            case "delete":
                return to_output(await service.delete(cast(str, request.doc_id), project=project))

    def _project(self, request: MemoryArguments) -> str | None:
        return self.params.project if self.params.project is not None else request.project

    def _file_target(self, path: str) -> tuple[Located, Path]:
        """Le fichier « <dossier>/<chemin> », s'il est permis : dossier, droit, extension."""
        if self.folders is None:  # add_file sans dossiers : refusé par les paramètres
            raise ToolError("Aucun dossier n'est permis à add_file.")
        located = self.folders.locate(path)
        Folders.require(located, ("read",))
        target = self.folders.resolved(located)
        if target.suffix.lower() not in FILE_SUFFIXES:
            raise ToolError(
                f"{located.address} : extension {target.suffix or 'absente'} refusée ; "
                f"permises : {', '.join(sorted(FILE_SUFFIXES))}."
            )
        return located, target

    def _read_file(self, path: str) -> tuple[str, Extracted]:
        """Le fichier désigné « <dossier>/<chemin> » : son adresse, son titre, son texte."""
        located, target = self._file_target(path)
        try:
            if not target.is_file():
                raise ToolError(f"{located.address} : fichier introuvable.")
            with target.open("rb") as handle:
                data = handle.read(MAX_FILE_BYTES + 1)
        except OSError as exc:
            raise ToolError(f"{located.address} : {exc.strerror or type(exc).__name__}.") from exc
        if len(data) > MAX_FILE_BYTES:
            raise ToolError(f"{located.address} dépasse {MAX_FILE_BYTES} octets.")
        try:
            text = data.decode("utf-8").strip()
        except UnicodeDecodeError as exc:
            raise ToolError(f"{located.address} n'est pas un fichier texte UTF-8.") from exc
        if not text:
            raise ToolError(f"{located.address} est vide.")
        heading = _H1.search(text)
        title = heading.group(1).strip() if heading else target.stem
        return located.address, Extracted(title=title, text=text)

    # --- Description et réglages -------------------------------------------------------

    def _description(self) -> str:
        params = self.params
        if params.project is None:
            scope = "Chaque document appartient à un projet."
        else:
            scope = (
                f"Projet imposé : {params.project} ; tout ce qui est mémorisé y va, et seuls "
                "ses documents sont visibles."
            )
        lines = [
            "Mémoire long terme : des documents (textes, pages web, fichiers) retrouvés par "
            "une recherche par le sens et par les mots. " + scope,
            "Actions permises :",
        ]
        for action in params.declared():
            waits = (
                " (attend une approbation humaine)" if params.approval(action) == "always" else ""
            )
            lines.append(f"- {action} : {_SAID[action]}{waits}")
        if "search" in params.actions:
            lines.append(
                "search rend des extraits avec doc_id, titre et score ; un extrait marqué "
                "truncated se lit en entier avec get."
            )
        if self.folders is not None:
            lines.append("add_file lit un fichier .md ou .txt « <dossier>/<chemin> » :")
            lines.extend(self.folders.described())
        writes = [a for a in params.declared() if a not in READS]
        if writes:
            lines.append(
                "N'écrire dans la mémoire que si l'utilisateur le demande ; chaque écriture "
                "rend ce qu'elle a réellement fait (duplicate_of : rien n'a été écrit)."
            )
        return "\n".join(lines)

    def settings(self) -> Settings:
        """La base, les actions et leur approbation, les modèles : pour ``loom validate``."""
        params = self.params
        grants = tuple(
            Grant(action, params.approval(action) == "always", action == "delete")
            for action in params.declared()
        )
        base = Place(
            name="memory",
            kind="sqlite",
            path=self.store.path,
            notes=() if params.project is None else (f"projet {params.project}",),
            grants=(("", grants),),
            rights=params.declared(),
        )
        places = (base, *(self.folders.places() if self.folders is not None else ()))
        models = params.models
        described = (
            "factices"
            if models.kind == "fake"
            else f"{models.embedder} + {models.reranker} ({models.device})"
        )
        return Settings(
            places=places,
            options=(("modèles", described), ("préchargement", "oui" if models.preload else "non")),
        )


# --- Fabrique --------------------------------------------------------------------------


def build(params: BaseModel, context: BuiltinContext) -> MemoryTool:
    """La fabrique de ``loom.memory`` : vérifie la base et les dossiers, n'ouvre rien."""
    if not isinstance(params, MemoryParams):
        raise TypeError(f"paramètres de {NAME} attendus, reçu {type(params).__name__}")
    if params.models.kind == "bge-m3":
        for module in ("torch", "transformers"):
            if importlib.util.find_spec(module) is None:
                # Le montage nomme l'extra qui l'apporte (``Builtin.extra``).
                raise ImportError(f"No module named {module!r}", name=module)
    if "add_url" in params.actions:
        from loom_ia.tools_builtin.memory.web import WEB_EXTRA, WEB_MODULES

        for module in WEB_MODULES:
            if importlib.util.find_spec(module) is None:
                raise ValueError(
                    f"add_url : dépendance {module!r} absente (installer l'extra : "
                    f"loom-ia[{WEB_EXTRA}])"
                )
    store = MemoryStore(_store_path(params, context))
    folders = None
    if params.folders:
        specs = [
            FolderSpec.model_validate({"name": f.name, "path": f.path, "rights": ["read"]})
            for f in params.folders
        ]
        folders = Folders(mount(specs, context))
    return MemoryTool(params, store, folders)


def _store_path(params: MemoryParams, context: BuiltinContext) -> Path:
    spec = params.store
    if spec.path_env is not None:
        secrets: Mapping[str, str] = context.secrets if context.secrets is not None else os.environ
        value = secrets.get(spec.path_env, "").strip()
        if not value:
            raise ValueError(f"store : la variable {spec.path_env} (path_env) est absente ou vide")
        given = Path(value).expanduser()
        if not given.is_absolute():
            raise ValueError(
                f"store : {spec.path_env} vaut {value!r} — un chemin absolu est attendu"
            )
        where = f"{spec.path_env} ({given})"
    else:
        assert spec.path is not None
        given = spec.path
        if not given.is_absolute() and context.base_dir is not None:
            given = context.base_dir / given
        where = str(given)
    path = given.resolve()
    if path.exists() and not path.is_file():
        raise ValueError(f"store : {where} n'est pas un fichier")
    if not path.parent.is_dir():
        raise ValueError(
            f"store : le dossier {path.parent} n'existe pas — loom.memory crée la base, pas son "
            "dossier"
        )
    problem = _exposes(path, context.protected)
    if problem is not None:
        raise ValueError(
            f"store : {path} {problem} — la mémoire ne peut pas être un fichier de loom ; "
            "donner un autre chemin"
        )
    return path


def _exposes(path: Path, protected: tuple[Protected, ...]) -> str | None:
    for item in protected:
        guarded = item.path.resolve()
        if guarded == path:
            return f"est {item.what}"
        if item.subtree and path.is_relative_to(guarded):
            return f"est dans {item.what} ({guarded})"
    return None
