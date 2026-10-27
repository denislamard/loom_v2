# SPDX-License-Identifier: Apache-2.0
"""La source ``forge`` : des outils forgés par les agents, exécutés dans la VM (J6.4c).

Un agent **forge** un outil — un nom, une description, un schéma d'entrée, le
code d'un module qui définit une fonction du même nom, des exemples — puis
l'**appelle**. Rien de ce code ne s'exécute sur l'hôte : l'hôte le lit
(syntaxe, signature), le range, et l'envoie à execd à chaque exécution. C'est
le principe d'execd : la VM ne garde aucun catalogue, chaque appel transporte
le code.

    tool_sources:
      - name: forge
        entry_point: forge
        params:
          vm_dir: ~/temp          # dossier de VM construit par make_vm.sh
          catalog_dir: outils     # catalogue, relatif au dossier de la config
          limits: {wall_ms: 10000}
          files: {max_files: 10}  # fichiers de out/ rapatriés ; false : aucun

La source fournit à chaque run :

- ``forge`` : forge (ou remplace) un outil. Les contrôles de l'hôte d'abord,
  puis chaque exemple exécuté dans la VM et comparé à ce qu'il doit rendre ;
  au premier écart, l'outil est refusé et le modèle apprend pourquoi.
  Accepté, il est écrit dans ``catalog_dir/<client>/<nom>/`` : ``<nom>.py``
  et ``outil.json``.
- ``call`` : appelle un outil du catalogue par son nom, avec ses arguments —
  le catalogue est lu à l'appel, donc un outil forgé plus tôt dans le run
  s'y trouve.
- chaque outil du catalogue du client, lu à l'ouverture du run : un outil
  forgé pendant un run n'y apparaît qu'au run suivant (la liste des outils
  d'un run est fixée à son ouverture).

La VM n'est démarrée qu'à la première exécution, et la session execd du run
ouverte à ce moment-là : ``loom validate``, le rejeu et le montage d'essai de
``serve --reload`` ouvrent la source sans rien exécuter.

``input_schema`` et ``examples`` se donnent en objets JSON, ou en texte JSON :
certains fournisseurs (MiniMax) déforment les objets libres d'un appel d'outil
— une liste ``required`` devenue un objet, des ``properties`` emboîtées —,
alors qu'une chaîne arrive intacte. Un refus qui porte sur leur forme le
suggère, pour ceux venus en objets.

Le schéma est écrit par le modèle, et l'hôte le valide dans sa boucle
d'événements, pour les exemples comme pour chaque appel : ses motifs
(``pattern``, ``patternProperties``…) s'exécutent avec un délai
(``core.bounded_regex``). Un motif trop coûteux refuse l'outil ou l'appel ; il
ne gèle pas l'hôte.

Fichiers produits : ce qu'un outil forgé écrit dans ``out/`` est rapatrié à
la fin de l'appel, dans la même prise de la session (un autre appel du run
viderait ``out/``), puis rendu en blocs de données que le moteur range dans
le stockage d'artefacts : le modèle n'en voit qu'une mention (ou l'image,
s'il les voit), le journal l'URI. Chaque fichier est borné en nombre et en
taille avant transfert, d'après le manifeste d'execd, puis contrôlé à
l'arrivée (taille et sha256) : un fichier qui a changé entre-temps est
écarté, pas rendu à moitié. Les exemples de ``forge`` ne rapatrient rien.
"""

import ast
import asyncio
import contextlib
import hashlib
import json
import keyword
import logging
import os
import re
import shutil
import sys
import unicodedata
import uuid
from collections.abc import AsyncGenerator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Protocol, cast

from jsonschema import SchemaError, ValidationError
from pydantic import JsonValue

from loom_ia.adapters.firecracker.session import (
    EXEC_MARGIN,
    ExecdError,
    Execution,
    Hello,
    ProtocolError,
    Session,
)
from loom_ia.adapters.firecracker.vm import Vm, VmError
from loom_ia.core.bounded_regex import RegexTimeout, bounded_validator, check_schema
from loom_ia.core.json_values import same_json
from loom_ia.core.model import (
    InlineDataBlock,
    OutputBlock,
    TextBlock,
    ToolOutput,
    ToolSpec,
    media_type_of,
)
from loom_ia.core.ports import SourceContext, SourceUnavailable, Tool, ToolContext, ToolError

__all__ = [
    "CALL",
    "FORGE",
    "MANIFEST",
    "Catalog",
    "FilePolicy",
    "ForgeSource",
    "Forged",
    "Jobs",
    "Runner",
    "VmRunner",
    "check_forged",
    "forge_source",
]

logger = logging.getLogger(__name__)

# Les deux outils fixes de la source ; aucun outil forgé ne peut porter leur nom.
FORGE: Final = "forge"
CALL: Final = "call"
# Ce qui accompagne le code d'un outil dans son dossier du catalogue.
MANIFEST: Final = "outil.json"
# Nom d'outil forgé : un identifiant Python en minuscules. Il devient le nom
# du module et de la fonction ; préfixé par loom, il tient dans les 64
# caractères des API.
NAME_PATTERN: Final = r"[a-z][a-z0-9_]{0,47}"
# Identifiant de client admis comme nom de dossier.
TENANT_PATTERN: Final = r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}"
MAX_EXAMPLES: Final = 10
# Ce que le modèle voit de stdout et de stderr, chacun (début et fin).
SHOWN: Final = 4000
# Clés de ``limits`` qu'execd connaît.
LIMIT_KEYS: Final = (
    "wall_ms",
    "cpu_ms",
    "mem_bytes",
    "fsize_bytes",
    "nofile",
    "nproc",
    "out_files",
)
# wall_ms qu'execd applique quand on ne demande rien.
DEFAULT_WALL_MS: Final = 30_000
# Attente du boot de la VM et d'execd, au premier appel d'un run (secondes).
BOOT_WAIT: Final = 60.0
# Rapatriement des fichiers de out/ : plafond d'un fichier (params ``files``),
# et temps laissé au transfert dans le délai de l'outil (secondes).
MAX_FILE_CEILING: Final = 64 << 20
MAX_TOTAL_CEILING: Final = 256 << 20
# out_files : ce qu'execd accepte au plus dans out/ (son plafond).
MAX_FILES_CEILING: Final = 512
FETCH_WAIT: Final = 30.0
# Échéance du transfert lui-même, prise dans FETCH_WAIT (le reste : marge).
FETCH_BUDGET: Final = 25.0
# Nom de fichier montré, et lignes « non rapatriés » montrées au plus.
MAX_NAME: Final = 200
SHOWN_LEFT: Final = 20

_ERRORS: Final = (VmError, ProtocolError, ExecdError, TimeoutError, OSError)
# Les champs de ``forge`` admis aussi en texte JSON.
_TEXT_FIELDS: Final = ("input_schema", "examples")


# ---------------------------------------------------------------------- #
# Exécution : une session execd par run
# ---------------------------------------------------------------------- #


class Jobs(Protocol):
    """Ce que la source demande d'une session execd (``Session`` le fait)."""

    @property
    def closed(self) -> bool: ...

    @property
    def hello(self) -> Hello: ...

    async def put_code(self, path: str, data: bytes) -> None: ...

    async def exec(
        self,
        entrypoint: str,
        *,
        args: Mapping[str, object] | None = None,
        limits: Mapping[str, int] | None = None,
        env: Mapping[str, str] | None = None,
        wait: float | None = None,
    ) -> Execution: ...

    async def get_file(self, path: str, *, max_bytes: int) -> bytes: ...

    async def close(self) -> None: ...


class Runner(Protocol):
    """D'où viennent les sessions : la VM, ou un double dans les essais."""

    async def session(self) -> Jobs:
        """Une session neuve ; la VM est démarrée si elle ne tourne pas."""
        ...

    async def aclose(self) -> None:
        """Arrête ce qui doit l'être quand la source est démontée."""
        ...


class VmRunner:
    """Les sessions d'un dossier de VM : démarrée au besoin, arrêtée si c'est nous."""

    def __init__(self, vm: Vm) -> None:
        self.vm = vm
        # Vrai si cette source a démarré la VM : c'est elle qui l'arrêtera.
        self.started = False
        self._lock = asyncio.Lock()

    async def session(self) -> Session:
        port = self.vm.execd_port
        if port is None:
            raise VmError(f"{self.vm.name} : EXECD_PORT absent de vm.env")
        async with self._lock:
            # À chaque session : la VM a pu être arrêtée par un autre process.
            if await self.vm.ensure_started(wait=BOOT_WAIT):
                self.started = True
                logger.info("VM %s démarrée pour la source forge", self.vm.name)
        reader, writer = await self.vm.connect(port, wait=BOOT_WAIT)
        return await Session.open(reader, writer)

    async def aclose(self) -> None:
        if self.started:
            how = await self.vm.stop()
            logger.info("VM %s arrêtée (%s)", self.vm.name, how)
            self.started = False


@dataclass(frozen=True, slots=True)
class FilePolicy:
    """Ce qu'un appel rapatrie de ``out/`` (params ``files``)."""

    max_files: int = 10
    max_file_bytes: int = 16 << 20
    max_total_bytes: int = 32 << 20


DEFAULT_FILES: Final = FilePolicy()


@dataclass(frozen=True, slots=True)
class _Fetched:
    """Fichiers rapatriés d'une exécution, et ceux laissés dans la VM, avec la raison."""

    blocks: tuple[InlineDataBlock, ...] = ()
    left: tuple[str, ...] = ()


async def _fetch(jobs: Jobs, execution: Execution, policy: FilePolicy) -> _Fetched:
    """Rapatrie les fichiers du manifeste, dans ses bornes ; jamais un fichier altéré.

    Le manifeste vient de l'invité : il est trié (l'ordre d'``os.walk`` entre
    sous-dossiers n'est pas garanti), dédoublonné, et ses noms nettoyés avant
    d'être montrés. Le transfert a son échéance, prise dans le délai de
    l'outil : passée, la session est fermée et le reste listé, le résultat du
    job gardé.
    """
    blocks: list[InlineDataBlock] = []
    left: list[str] = []
    total = 0
    deadline = asyncio.get_running_loop().time() + FETCH_BUDGET
    seen: set[str] = set()
    outputs = sorted(execution.outputs, key=lambda o: o.path)
    for index, output in enumerate(outputs):
        label = f"{_shown(output.path)} ({output.size} o)"
        if output.path in seen:
            left.append(f"{label} : en double dans le manifeste")
            continue
        seen.add(output.path)
        if len(blocks) >= policy.max_files:
            left.append(f"{label} : au-delà de {policy.max_files} fichiers par appel")
            continue
        if output.size > policy.max_file_bytes:
            left.append(f"{label} : au-delà de {_megas(policy.max_file_bytes)} par fichier")
            continue
        if total + output.size > policy.max_total_bytes:
            left.append(f"{label} : au-delà de {_megas(policy.max_total_bytes)} par appel")
            continue
        try:
            # Borné par la taille annoncée : un fichier qui grossit est refusé
            # en route, sans tout lire.
            async with asyncio.timeout_at(deadline):
                data = await jobs.get_file(output.path, max_bytes=output.size)
        except ValueError:
            left.append(f"{label} : a changé depuis la fin du job")
            continue
        except ExecdError as exc:
            left.append(f"{label} : {exc.message}")
            continue
        except (ProtocolError, TimeoutError, OSError) as exc:
            # La session est fermée (Session ferme sur délai ou annulation) :
            # rien d'autre ne viendra.
            late = asyncio.get_running_loop().time() >= deadline
            reason = "délai de rapatriement dépassé" if late else "transfert interrompu"
            logger.warning("Source forge : rapatriement arrêté (%s) — %s", reason, exc)
            left += [f"{_shown(o.path)} ({o.size} o) : {reason}" for o in outputs[index:]]
            break
        if len(data) != output.size or hashlib.sha256(data).hexdigest() != output.sha256:
            left.append(f"{label} : a changé depuis la fin du job")
            continue
        total += len(data)
        name = _shown(output.path)
        blocks.append(InlineDataBlock(media_type=media_type_of(name, data), data=data, name=name))
    left += [
        f"{_shown(path)} : lien ou fichier spécial, ignoré" for path in execution.outputs_skipped
    ]
    return _Fetched(tuple(blocks), tuple(left))


def _shown(path: str) -> str:
    """Un chemin de l'invité tel qu'on peut le montrer : sans caractère de contrôle, borné."""
    clean = "".join("?" if unicodedata.category(c).startswith("C") else c for c in path)
    return clean if len(clean) <= MAX_NAME else f"{clean[: MAX_NAME - 1]}…"


def _megas(size: int) -> str:
    return f"{size / (1 << 20):g} Mo".replace(".", ",")


@dataclass(slots=True)
class _Run:
    """Ce que les outils d'un run partagent : sa session, ouverte au premier appel."""

    runner: Runner
    limits: Mapping[str, int]
    files: FilePolicy | None = None
    jobs: Jobs | None = None
    # Empreinte du code déposé par module, pour ne le renvoyer qu'au changement.
    loaded: dict[str, str] = field(default_factory=dict[str, str])
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def execute(self, name: str, code: str, arguments: Mapping[str, object]) -> Execution:
        """Dépose le module si besoin, puis appelle ``name:name`` avec ces arguments."""
        execution, _ = await self._execute(name, code, arguments, fetch=False)
        return execution

    async def produce(
        self, name: str, code: str, arguments: Mapping[str, object]
    ) -> tuple[Execution, _Fetched]:
        """Comme ``execute``, puis rapatrie ce que le job a écrit dans ``out/``."""
        return await self._execute(name, code, arguments, fetch=True)

    async def _execute(
        self, name: str, code: str, arguments: Mapping[str, object], *, fetch: bool
    ) -> tuple[Execution, _Fetched]:
        # Dépôt, appel et rapatriement d'un seul tenant : deux appels parallèles
        # du run ne s'intercalent pas (execd n'en exécute qu'un à la fois de
        # toute façon), et le job suivant ne vide pas out/ avant le transfert.
        async with self.lock:
            if self.jobs is None or self.jobs.closed:
                jobs = await self.runner.session()
                ceilings = jobs.hello.ceilings
                over = {k: v for k, v in self.limits.items() if v > ceilings.get(k, v)}
                if over:
                    await jobs.close()
                    said = ", ".join(f"{k} = {v} (plafond {ceilings[k]})" for k, v in over.items())
                    logger.error("Source forge : limits au-delà des plafonds de la VM : %s", said)
                    raise ToolError(f"limits de la source au-delà des plafonds de la VM : {said}")
                self.jobs = jobs
                self.loaded.clear()
            digest = hashlib.sha256(code.encode()).hexdigest()
            if self.loaded.get(name) != digest:
                await self.jobs.put_code(f"{name}.py", code.encode())
                self.loaded[name] = digest
            execution = await self.jobs.exec(
                f"{name}:{name}", args=dict(arguments), limits=self.limits or None
            )
            if not fetch or not execution.ok:
                return execution, _Fetched()
            if self.files is None:
                return execution, _Fetched(left=_kept_in_vm(execution))
            return execution, await _fetch(self.jobs, execution, self.files)

    async def close(self) -> None:
        if self.jobs is not None:
            await self.jobs.close()


# ---------------------------------------------------------------------- #
# Catalogue : un dossier par client, un sous-dossier par outil
# ---------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Forged:
    """Un outil du catalogue."""

    name: str
    description: str
    input_schema: dict[str, JsonValue]
    code: str
    examples: tuple[dict[str, JsonValue], ...]
    # Run qui l'a forgé, et quand (ISO 8601, UTC).
    forged_by: str
    forged_at: str


class Catalog:
    """Les outils forgés, rangés sous ``root/<client>/<nom>/`` : ``<nom>.py`` et ``outil.json``."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def folder(self, tenant: str) -> Path:
        if re.fullmatch(TENANT_PATTERN, tenant) is None:
            raise ValueError(f"client {tenant!r} : nom inutilisable comme dossier du catalogue")
        return self.root / tenant

    def list(self, tenant: str) -> list[Forged]:
        """Les outils du client, par nom ; un dossier illisible est écarté et signalé."""
        folder = self.folder(tenant)
        if not folder.is_dir():
            return []
        found: list[Forged] = []
        for entry in sorted(folder.iterdir()):
            if entry.name.startswith(".") or not entry.is_dir():
                continue
            forged = self._read(entry)
            if forged is not None:
                found.append(forged)
        return found

    def get(self, tenant: str, name: str) -> Forged | None:
        if re.fullmatch(NAME_PATTERN, name) is None:
            return None
        entry = self.folder(tenant) / name
        return self._read(entry) if entry.is_dir() else None

    def save(self, tenant: str, forged: Forged) -> None:
        """Écrit l'outil ; un outil du même nom est remplacé d'un bloc."""
        folder = self.folder(tenant)
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / forged.name
        # Écrit à côté sous un nom caché, puis mis en place par renommage : un
        # lecteur ne voit jamais un dossier à moitié écrit.
        fresh = folder / f".{forged.name}.{uuid.uuid4().hex}"
        fresh.mkdir()
        try:
            (fresh / f"{forged.name}.py").write_text(forged.code, encoding="utf-8")
            manifest = {
                "description": forged.description,
                "input_schema": forged.input_schema,
                "examples": list(forged.examples),
                "forged_by": forged.forged_by,
                "forged_at": forged.forged_at,
            }
            (fresh / MANIFEST).write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            if target.exists():
                old = folder / f".{forged.name}.{uuid.uuid4().hex}.old"
                target.rename(old)
                fresh.rename(target)
                shutil.rmtree(old, ignore_errors=True)
            else:
                fresh.rename(target)
        except BaseException:
            shutil.rmtree(fresh, ignore_errors=True)
            raise

    def _read(self, entry: Path) -> Forged | None:
        name = entry.name
        try:
            if re.fullmatch(NAME_PATTERN, name) is None:
                raise ValueError("nom de dossier hors du format des outils")
            code = (entry / f"{name}.py").read_text(encoding="utf-8")
            manifest: object = json.loads((entry / MANIFEST).read_text(encoding="utf-8"))
            if not isinstance(manifest, dict):
                raise ValueError(f"{MANIFEST} : objet JSON attendu")
            data = cast(dict[str, JsonValue], manifest)
            schema = data.get("input_schema")
            examples = data.get("examples")
            if not isinstance(schema, dict) or not isinstance(examples, list):
                raise ValueError(f"{MANIFEST} : input_schema ou examples illisible")
            return Forged(
                name=name,
                description=str(data.get("description", "")),
                input_schema=schema,
                code=code,
                examples=tuple(
                    cast(dict[str, JsonValue], e) for e in examples if isinstance(e, dict)
                ),
                forged_by=str(data.get("forged_by", "")),
                forged_at=str(data.get("forged_at", "")),
            )
        except (OSError, ValueError) as exc:
            logger.warning("Outil forgé %s écarté : %s", entry, exc)
            return None


# ---------------------------------------------------------------------- #
# Contrôles de l'hôte : rien n'est exécuté
# ---------------------------------------------------------------------- #


class _ShapeError(ToolError):
    """Un refus qui porte sur la forme d'``input_schema`` ou d'``examples``."""


def _decoded(value: JsonValue, field: str) -> JsonValue:
    """Un champ venu en objet tel quel, ou venu en texte JSON, décodé."""
    if not isinstance(value, str):
        return value
    try:
        return cast(JsonValue, json.loads(value, parse_constant=_not_json))
    except json.JSONDecodeError as exc:
        raise _ShapeError(
            f"{field} : texte JSON illisible — {exc.msg} (ligne {exc.lineno}, colonne {exc.colno})"
        ) from None
    except ValueError as exc:
        raise _ShapeError(f"{field} : texte JSON illisible — {exc}") from None


def _not_json(constant: str) -> None:
    raise ValueError(f"{constant} n'est pas une valeur JSON")


def check_forged(
    name: str, input_schema: dict[str, JsonValue], code: str, examples: Sequence[JsonValue]
) -> list[tuple[dict[str, JsonValue], JsonValue]]:
    """Ce que l'hôte peut dire d'un outil sans l'exécuter ; ``ToolError`` au premier défaut.

    Rend les exemples sous forme (arguments, attendu). Un défaut de forme
    d'``input_schema`` ou d'``examples`` — accord du code avec le schéma
    compris — est un ``_ShapeError``.
    """
    if re.fullmatch(NAME_PATTERN, name) is None:
        raise ToolError(
            f"name {name!r} : un identifiant Python en minuscules est attendu "
            "(lettres, chiffres, _ ; 48 caractères au plus, une lettre d'abord)"
        )
    if name in (FORGE, CALL) or keyword.iskeyword(name):
        raise ToolError(f"name {name!r} : nom réservé")
    if name in sys.stdlib_module_names:
        raise ToolError(
            f"name {name!r} : c'est un module de la bibliothèque standard, que le "
            "module de l'outil masquerait ; choisis un autre nom"
        )
    try:
        check_schema(input_schema)
    except SchemaError as exc:
        raise _ShapeError(f"input_schema n'est pas un schéma JSON valide : {exc.message}") from None
    if input_schema.get("type") != "object":
        raise _ShapeError('input_schema : "type": "object" attendu (les arguments sont nommés)')
    properties = input_schema.get("properties", {})
    required = input_schema.get("required", [])
    if not isinstance(properties, dict) or not isinstance(required, list):
        raise _ShapeError("input_schema : properties (objet) et required (liste) attendus")
    _check_signature(name, code, set(properties), {str(r) for r in required})

    if not 1 <= len(examples) <= MAX_EXAMPLES:
        raise _ShapeError(
            f"examples : de 1 à {MAX_EXAMPLES} exemples attendus, {len(examples)} reçu(s)"
        )
    validator = bounded_validator(input_schema)
    pairs: list[tuple[dict[str, JsonValue], JsonValue]] = []
    for index, raw in enumerate(examples, start=1):
        example = cast(dict[str, JsonValue], raw) if isinstance(raw, dict) else {}
        arguments = example.get("arguments")
        if not isinstance(arguments, dict) or "expected" not in example:
            raise _ShapeError(f"exemple {index} : arguments (objet) et expected attendus")
        try:
            validator.validate(arguments)
        except ValidationError as exc:
            raise _ShapeError(
                f"exemple {index} : ses arguments ne suivent pas input_schema — {exc.message}"
            ) from None
        except RegexTimeout as exc:
            raise ToolError(
                f"exemple {index} : input_schema ne peut pas être appliqué — {exc}"
            ) from None
        pairs.append((arguments, example["expected"]))
    return pairs


def _check_signature(name: str, code: str, properties: set[str], required: set[str]) -> None:
    try:
        tree = ast.parse(code, filename=f"{name}.py")
    except SyntaxError as exc:
        raise ToolError(f"code : erreur de syntaxe ligne {exc.lineno} — {exc.msg}") from None
    found = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name
    ]
    if not found:
        raise ToolError(f"code : pas de fonction {name}() au premier niveau du module")
    args = found[-1].args
    if args.posonlyargs or args.vararg is not None:
        raise ToolError(
            f"code : {name}() reçoit ses arguments par nom — ni paramètre positionnel "
            "seul (/), ni *args"
        )
    named = [*args.args, *args.kwonlyargs]
    params = {arg.arg for arg in named}
    # Les derniers de args ont les défauts ; kw_defaults vaut None sans défaut.
    with_default = {arg.arg for arg in args.args[len(args.args) - len(args.defaults) :]}
    with_default |= {
        arg.arg
        for arg, default in zip(args.kwonlyargs, args.kw_defaults, strict=True)
        if default is not None
    }
    if args.kwarg is None and (missing := sorted(properties - params)):
        raise _ShapeError(
            f"code : {name}() ne reçoit pas {', '.join(missing)}, que input_schema propose"
        )
    if unsure := sorted(params - with_default - required):
        raise _ShapeError(
            f"code : {name}() exige {', '.join(unsure)}, que input_schema ne rend pas "
            "obligatoire (required) — donne-lui une valeur par défaut, ou exige-le"
        )


def _json_kind(value: object) -> str:
    """Le type JSON d'une valeur, tel que le refus le nomme."""
    match value:
        case bool():
            return "un booléen"
        case int() | float():
            return "un nombre"
        case str():
            return "une chaîne"
        case dict():
            return "un objet"
        case list():
            return "une liste"
        case _:
            return "null"


def _mismatch_hint(expected: object, result: object) -> str:
    """Ce qu'un écart doit au type de ``expected`` ; vide s'il n'y a rien à en dire.

    Vu au run réel de MiniMax (08/10) : ``expected`` donné en texte JSON, une
    chaîne, quand la fonction rend un objet — puis encodé deux fois quand elle
    rend une chaîne. Le refus ne montrait que les deux valeurs.
    """
    if isinstance(expected, str):
        try:
            decoded = json.loads(expected)
        except ValueError, RecursionError:
            pass
        else:
            if same_json(decoded, result):
                return (
                    " (`expected` est le texte JSON de la valeur rendue : "
                    "donne la valeur elle-même)"
                )
    wanted, got = _json_kind(expected), _json_kind(result)
    return f" (attendu {wanted}, obtenu {got})" if wanted != got else ""


# ---------------------------------------------------------------------- #
# Ce que le modèle voit d'une exécution
# ---------------------------------------------------------------------- #


def _bounded(text: str, limit: int = SHOWN) -> str:
    if len(text) <= limit:
        return text
    half = limit // 2
    return f"{text[:half]}\n[… {len(text) - 2 * half} caractères omis …]\n{text[-half:]}"


def _streams(execution: Execution, *, files: bool = True) -> str:
    parts: list[str] = []
    for label, text, cut in (
        ("stdout", execution.stdout, execution.stdout_truncated),
        ("stderr", execution.stderr, execution.stderr_truncated),
    ):
        if text:
            said = " (tronqué par la VM)" if cut else ""
            parts.append(f"{label}{said} :\n{_bounded(text)}")
    if files and execution.outputs:
        said = ", ".join(f"{o.path} ({o.size} o)" for o in execution.outputs)
        parts.append(f"fichiers produits, non rapatriés : {said}")
    return "\n\n".join(parts)


def _kept_in_vm(execution: Execution) -> tuple[str, ...]:
    return tuple(f"{o.path} ({o.size} o) : rapatriement désactivé" for o in execution.outputs)


def _failure(execution: Execution) -> str:
    error = execution.error
    kind, message = (error.kind, error.message) if error is not None else ("inconnu", "")
    lines = [f"Échec dans la VM ({kind}) : {message}"]
    detail = dict(error.detail) if error is not None else {}
    trace = detail.pop("traceback", None)
    if isinstance(trace, list):
        lines.append("\n".join(str(line) for line in cast(list[object], trace)))
    if detail:
        lines.append(json.dumps(detail, ensure_ascii=False, default=str))
    if kind == "import_error":
        lines.append(
            "La VM n'a que la bibliothèque standard de Python : rien d'autre ne s'importe."
        )
    if streams := _streams(execution):
        lines.append(streams)
    return "\n".join(lines)


def _output(execution: Execution, fetched: _Fetched) -> ToolOutput:
    if not execution.ok:
        return ToolOutput.error(_failure(execution))
    parts = [json.dumps(execution.result, ensure_ascii=False)]
    if streams := _streams(execution, files=False):
        parts.append(streams)
    if fetched.left:
        lines = [f"- {line}" for line in fetched.left[:SHOWN_LEFT]]
        if (more := len(fetched.left) - SHOWN_LEFT) > 0:
            lines.append(f"- … et {more} autre(s)")
        parts.append("fichiers non rapatriés :\n" + "\n".join(lines))
    # Les fichiers rapatriés suivent le texte : le moteur les range et les
    # remplace par leur mention (nom, type, taille).
    blocks: tuple[OutputBlock, ...] = (TextBlock(text="\n\n".join(parts)), *fetched.blocks)
    return ToolOutput(blocks=blocks, data=cast(JsonValue, execution.result))


def _unavailable(exc: BaseException) -> ToolOutput:
    logger.warning("Source forge : VM ou execd indisponible — %s", exc)
    return ToolOutput.error(f"VM indisponible : {type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------- #
# Les outils
# ---------------------------------------------------------------------- #


def _timeout(limits: Mapping[str, int], jobs: int = 1, *, fetch: bool = False) -> float:
    """Délai de loom pour un outil qui lance ``jobs`` exécutions, boot compris.

    Sans lui, loom couperait au bout de son délai par défaut, avant qu'execd
    ait rendu son verdict. ``fetch`` : le rapatriement de out/ en plus.
    """
    wall = limits.get("wall_ms", DEFAULT_WALL_MS) / 1000
    return BOOT_WAIT + jobs * (wall + 5) + EXEC_MARGIN + (FETCH_WAIT if fetch else 0.0)


FORGE_DESCRIPTION: Final = (
    "Forge un nouvel outil Python, exécuté dans une VM isolée sans réseau "
    "(bibliothèque standard seulement). `code` est un module qui définit, au premier "
    "niveau, une fonction nommée comme l'outil (`name`) : elle reçoit les propriétés de "
    "`input_schema` par nom et rend une valeur JSON. `examples` : de 1 à 10 cas, "
    "exécutés dans la VM avant l'enregistrement ; `expected` doit être exactement la "
    "valeur rendue. Un outil du même nom est remplacé. L'outil forgé s'appelle "
    "aussitôt par `call`, et apparaît comme outil à part entière au run suivant."
)


def _files_hint(files: FilePolicy) -> str:
    """Ce que la description de ``forge`` dit des fichiers, quand ils sont rapatriés."""
    return (
        " Un fichier (image, PDF, CSV…) s'écrit dans le dossier "
        "`os.environ['OUT_DIR']` : à chaque appel de l'outil (pas pendant les exemples), "
        f"ce qui s'y trouve est rapatrié et rendu comme fichier, {files.max_files} au plus, "
        f"{_megas(files.max_file_bytes)} chacun ; la valeur rendue reste courte "
        "(n'y recopie pas le contenu du fichier). Les exemples vérifient alors "
        "cette valeur, par exemple le nom du fichier ou un décompte."
    )


CALL_DESCRIPTION: Final = (
    "Appelle un outil forgé par son nom (`name`), avec ses arguments (`arguments`, "
    "selon son schéma) ; il s'exécute dans la VM. Sert à appeler dans ce run un outil "
    "qu'on vient de forger, avant qu'il n'apparaisse comme outil à part entière."
)


@dataclass(frozen=True, slots=True)
class _ForgeTool:
    catalog: Catalog
    run: _Run
    spec: ToolSpec

    async def invoke(self, arguments: dict[str, JsonValue], context: ToolContext) -> ToolOutput:
        name = str(arguments["name"])
        code = str(arguments["code"])
        try:
            schema = _decoded(arguments["input_schema"], "input_schema")
            examples = _decoded(arguments["examples"], "examples")
            if not isinstance(schema, dict):
                raise _ShapeError(
                    "input_schema : un objet JSON est attendu (un schéma de type object)"
                )
            if not isinstance(examples, list):
                raise _ShapeError("examples : une liste JSON est attendue")
            pairs = check_forged(name, schema, code, examples)
        except _ShapeError as exc:
            # Venus en objets, ils ont pu être déformés en route : le texte
            # JSON, lui, arrive tel quel.
            objects = [f for f in _TEXT_FIELDS if not isinstance(arguments[f], str)]
            if not objects:
                raise
            raise ToolError(
                f"{exc.message} (si ton format d'appel déforme les objets imbriqués, donne "
                f"{' et '.join(objects)} en texte JSON)"
            ) from None
        for index, (example, expected) in enumerate(pairs, start=1):
            try:
                execution = await self.run.execute(name, code, example)
            except _ERRORS as exc:
                return _unavailable(exc)
            if not execution.ok:
                return ToolOutput.error(
                    f"Outil {name} refusé — exemple {index} {json.dumps(example)} :\n"
                    + _failure(execution)
                )
            if not same_json(execution.result, expected):
                got = json.dumps(execution.result, ensure_ascii=False)
                wanted = json.dumps(expected, ensure_ascii=False)
                streams = _streams(execution)
                return ToolOutput.error(
                    f"Outil {name} refusé — exemple {index} {json.dumps(example)} : "
                    f"attendu {wanted}, obtenu {got}{_mismatch_hint(expected, execution.result)}"
                    + (f"\n{streams}" if streams else "")
                )
        forged = Forged(
            name=name,
            description=str(arguments["description"]).strip(),
            input_schema=schema,
            code=code,
            examples=tuple(
                {"arguments": example, "expected": expected} for example, expected in pairs
            ),
            forged_by=str(context.run_id),
            forged_at=datetime.now(UTC).isoformat(timespec="milliseconds"),
        )
        try:
            self.catalog.save(str(context.tenant_id), forged)
        except (OSError, ValueError) as exc:
            logger.error("Outil forgé %s non enregistré : %s", name, exc)
            return ToolOutput.error(f"Outil {name} non enregistré : {exc}")
        logger.info("Outil forgé %s enregistré (run %s)", name, context.run_id)
        said = (
            f"Outil {name} forgé : {len(pairs)} exemple(s) passé(s) dans la VM. "
            f"Appelable dans ce run par call (name: {name}), et comme outil à part "
            "entière au run suivant."
        )
        return ToolOutput(
            blocks=(TextBlock(text=said),), data={"name": name, "examples": len(pairs)}
        )


@dataclass(frozen=True, slots=True)
class _CallTool:
    catalog: Catalog
    run: _Run
    spec: ToolSpec

    async def invoke(self, arguments: dict[str, JsonValue], context: ToolContext) -> ToolOutput:
        name = str(arguments["name"])
        given = cast(dict[str, JsonValue], arguments.get("arguments") or {})
        forged = self.catalog.get(str(context.tenant_id), name)
        if forged is None:
            known = ", ".join(f.name for f in self.catalog.list(str(context.tenant_id)))
            raise ToolError(f"Outil forgé {name!r} inconnu ; au catalogue : {known or 'aucun'}")
        try:
            bounded_validator(forged.input_schema).validate(given)
        except ValidationError as exc:
            raise ToolError(f"arguments refusés par le schéma de {name} : {exc.message}") from None
        except RegexTimeout as exc:
            raise ToolError(f"arguments refusés par le schéma de {name} : {exc}") from None
        try:
            return _output(*await self.run.produce(name, forged.code, given))
        except _ERRORS as exc:
            return _unavailable(exc)


@dataclass(frozen=True, slots=True)
class _ForgedTool:
    forged: Forged
    run: _Run
    spec: ToolSpec

    async def invoke(self, arguments: dict[str, JsonValue], context: ToolContext) -> ToolOutput:
        try:
            return _output(*await self.run.produce(self.forged.name, self.forged.code, arguments))
        except _ERRORS as exc:
            return _unavailable(exc)


def _forge_spec(limits: Mapping[str, int], files: FilePolicy | None) -> ToolSpec:
    return ToolSpec(
        name=FORGE,
        description=FORGE_DESCRIPTION + (_files_hint(files) if files is not None else ""),
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "pattern": f"^{NAME_PATTERN}$"},
                "description": {"type": "string", "minLength": 1},
                "input_schema": {
                    "description": "Schéma JSON (type object) des arguments de l'outil, "
                    "en objet ou en texte JSON.",
                    "anyOf": [{"type": "object"}, {"type": "string"}],
                },
                "code": {"type": "string", "minLength": 1},
                "examples": {
                    "description": f"De 1 à {MAX_EXAMPLES} exemples (arguments, expected), "
                    "en liste ou en texte JSON.",
                    "anyOf": [
                        {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": MAX_EXAMPLES,
                            "items": {
                                "type": "object",
                                "properties": {"arguments": {"type": "object"}, "expected": {}},
                                "required": ["arguments", "expected"],
                            },
                        },
                        {"type": "string"},
                    ],
                },
            },
            "required": ["name", "description", "input_schema", "code", "examples"],
        },
        kind="python",
        side_effects="reversible",
        idempotent=True,
        timeout=_timeout(limits, MAX_EXAMPLES),
    )


def _call_spec(limits: Mapping[str, int]) -> ToolSpec:
    return ToolSpec(
        name=CALL,
        description=CALL_DESCRIPTION,
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "arguments": {"type": "object"},
            },
            "required": ["name", "arguments"],
        },
        kind="python",
        timeout=_timeout(limits, fetch=True),
    )


def _forged_spec(forged: Forged, limits: Mapping[str, int]) -> ToolSpec:
    return ToolSpec(
        name=forged.name,
        description=forged.description,
        input_schema=forged.input_schema,
        kind="python",
        timeout=_timeout(limits, fetch=True),
    )


# ---------------------------------------------------------------------- #
# La source et sa fabrique
# ---------------------------------------------------------------------- #


def _run_start(run_id: str) -> datetime | None:
    """Le début d'un run, lu dans son identifiant (UUIDv7, qui porte l'heure en ms)."""
    try:
        found = uuid.UUID(run_id)
    except ValueError:
        return None
    if found.version != 7:
        return None
    return datetime.fromtimestamp((found.int >> 80) / 1000, UTC)


def _moment(text: str) -> datetime | None:
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


class ForgeSource:
    """La source ``forge`` : ses deux outils fixes et le catalogue du client.

    Un run voit comme outils à part entière ceux du catalogue forgés avant son
    début ; ``call`` voit tout le catalogue, au moment de l'appel.
    """

    def __init__(
        self,
        *,
        name: str,
        catalog: Catalog,
        runner: Runner,
        limits: Mapping[str, int],
        files: FilePolicy | None = DEFAULT_FILES,
    ) -> None:
        self.name = name
        self.required = False
        self.catalog = catalog
        self.runner = runner
        self.limits = dict(limits)
        self.files = files

    def __repr__(self) -> str:
        return f"ForgeSource({self.name!r}, catalogue {self.catalog.root})"

    @asynccontextmanager
    async def open(self, context: SourceContext) -> AsyncGenerator[Sequence[Tool]]:
        try:
            catalog = self.catalog.list(str(context.tenant_id))
        except (OSError, ValueError) as exc:
            raise SourceUnavailable(self.name, f"catalogue illisible : {exc}") from exc
        # Seuls les outils forgés avant le début du run : sa liste d'outils
        # reste la même à sa reprise et à son rejeu, même si on a forgé depuis.
        started = _run_start(str(context.run_id))
        if started is not None:
            catalog = [f for f in catalog if (at := _moment(f.forged_at)) is None or at < started]
        run = _Run(self.runner, self.limits, self.files)
        tools: list[Tool] = [
            _ForgeTool(self.catalog, run, _forge_spec(self.limits, self.files)),
            _CallTool(self.catalog, run, _call_spec(self.limits)),
            *(_ForgedTool(forged, run, _forged_spec(forged, self.limits)) for forged in catalog),
        ]
        try:
            yield tools
        finally:
            with contextlib.suppress(Exception):
                await run.close()

    async def aclose(self) -> None:
        await self.runner.aclose()


def _folder(params: Mapping[str, JsonValue], key: str, base_dir: Path) -> Path:
    value = params.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key!r} : un chemin est attendu")
    path = Path(os.path.expanduser(value))
    return path if path.is_absolute() else base_dir / path


def _limits(value: JsonValue) -> dict[str, int]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("'limits' : un objet est attendu (wall_ms, cpu_ms, mem_bytes…)")
    limits: dict[str, int] = {}
    for key, raw in value.items():
        if key not in LIMIT_KEYS:
            raise ValueError(f"'limits.{key}' inconnue ; connues : {', '.join(LIMIT_KEYS)}")
        if not isinstance(raw, int) or isinstance(raw, bool) or raw <= 0:
            raise ValueError(f"'limits.{key}' : un entier positif est attendu")
        limits[key] = raw
    return limits


def _files(value: JsonValue) -> FilePolicy | None:
    """``files`` : absent, les bornes par défaut ; ``false``, rien n'est rapatrié."""
    if value is None or value is True:
        return DEFAULT_FILES
    if value is False:
        return None
    if not isinstance(value, dict):
        raise ValueError("'files' : un objet (max_files, max_file_bytes, max_total_bytes) ou false")
    known = ("max_files", "max_file_bytes", "max_total_bytes")
    found: dict[str, int] = {}
    for key, raw in value.items():
        if key not in known:
            raise ValueError(f"'files.{key}' inconnue ; connues : {', '.join(known)}")
        if not isinstance(raw, int) or isinstance(raw, bool) or raw <= 0:
            raise ValueError(f"'files.{key}' : un entier positif est attendu")
        found[key] = raw
    policy = FilePolicy(**found)
    if policy.max_files > MAX_FILES_CEILING:
        raise ValueError(f"'files.max_files' : {MAX_FILES_CEILING} au plus (plafond d'execd)")
    if policy.max_file_bytes > MAX_FILE_CEILING:
        raise ValueError(f"'files.max_file_bytes' : {MAX_FILE_CEILING} octets au plus")
    if policy.max_total_bytes > MAX_TOTAL_CEILING:
        raise ValueError(f"'files.max_total_bytes' : {MAX_TOTAL_CEILING} octets au plus")
    if policy.max_total_bytes < policy.max_file_bytes:
        raise ValueError("'files.max_total_bytes' doit valoir au moins max_file_bytes")
    return policy


def forge_source(
    *,
    name: str,
    params: Mapping[str, JsonValue],
    secrets: Mapping[str, str],
    base_dir: Path,
) -> ForgeSource:
    """La fabrique du point d'entrée ``forge`` : vérifie ``params``, ne démarre rien."""
    unknown = sorted(set(params) - {"vm_dir", "catalog_dir", "limits", "files"})
    if unknown:
        raise ValueError(
            f"params inconnus : {', '.join(unknown)} (vm_dir, catalog_dir, limits, files)"
        )
    vm_dir = _folder(params, "vm_dir", base_dir)
    catalog_dir = _folder(params, "catalog_dir", base_dir)
    if catalog_dir.exists() and not catalog_dir.is_dir():
        raise ValueError(f"'catalog_dir' : {catalog_dir} n'est pas un dossier")
    try:
        vm = Vm.load(vm_dir)
    except VmError as exc:
        raise ValueError(f"'vm_dir' : {exc}") from exc
    return ForgeSource(
        name=name,
        catalog=Catalog(catalog_dir),
        runner=VmRunner(vm),
        limits=_limits(params.get("limits")),
        files=_files(params.get("files")),
    )
