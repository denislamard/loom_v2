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
import uuid
from collections.abc import AsyncGenerator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Protocol, cast

from jsonschema import Draft202012Validator, SchemaError, ValidationError
from jsonschema.validators import validator_for
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
from loom_ia.core.model import TextBlock, ToolOutput, ToolSpec
from loom_ia.core.ports import SourceContext, SourceUnavailable, Tool, ToolContext, ToolError

__all__ = [
    "CALL",
    "FORGE",
    "MANIFEST",
    "Catalog",
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


@dataclass(slots=True)
class _Run:
    """Ce que les outils d'un run partagent : sa session, ouverte au premier appel."""

    runner: Runner
    limits: Mapping[str, int]
    jobs: Jobs | None = None
    # Empreinte du code déposé par module, pour ne le renvoyer qu'au changement.
    loaded: dict[str, str] = field(default_factory=dict[str, str])
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def execute(self, name: str, code: str, arguments: Mapping[str, object]) -> Execution:
        """Dépose le module si besoin, puis appelle ``name:name`` avec ces arguments."""
        # Dépôt et appel d'un seul tenant : deux appels parallèles du run ne
        # s'intercalent pas (execd n'en exécute qu'un à la fois de toute façon).
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
            return await self.jobs.exec(
                f"{name}:{name}", args=dict(arguments), limits=self.limits or None
            )

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
        validator_for(input_schema, default=Draft202012Validator).check_schema(input_schema)
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
    validator = validator_for(input_schema, default=Draft202012Validator)(input_schema)
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


def _same(left: object, right: object) -> bool:
    """Égalité de valeurs JSON : un booléen n'est pas un nombre, 2 vaut 2.0."""
    if isinstance(left, bool) or isinstance(right, bool):
        # True et False sont uniques : 1 n'est pas True.
        return left is right
    if isinstance(left, int | float) and isinstance(right, int | float):
        return left == right
    if isinstance(left, str) and isinstance(right, str):
        return left == right
    if isinstance(left, dict) and isinstance(right, dict):
        a, b = cast(dict[str, object], left), cast(dict[str, object], right)
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if isinstance(left, list) and isinstance(right, list):
        a, b = cast(list[object], left), cast(list[object], right)
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b, strict=True))
    return left is None and right is None


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
            if _same(decoded, result):
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


def _streams(execution: Execution) -> str:
    parts: list[str] = []
    for label, text, cut in (
        ("stdout", execution.stdout, execution.stdout_truncated),
        ("stderr", execution.stderr, execution.stderr_truncated),
    ):
        if text:
            said = " (tronqué par la VM)" if cut else ""
            parts.append(f"{label}{said} :\n{_bounded(text)}")
    if execution.outputs:
        files = ", ".join(f"{o.path} ({o.size} o)" for o in execution.outputs)
        parts.append(f"fichiers produits, non rapatriés : {files}")
    return "\n\n".join(parts)


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


def _output(execution: Execution) -> ToolOutput:
    if not execution.ok:
        return ToolOutput.error(_failure(execution))
    value = json.dumps(execution.result, ensure_ascii=False)
    streams = _streams(execution)
    text = f"{value}\n\n{streams}" if streams else value
    return ToolOutput(blocks=(TextBlock(text=text),), data=cast(JsonValue, execution.result))


def _unavailable(exc: BaseException) -> ToolOutput:
    logger.warning("Source forge : VM ou execd indisponible — %s", exc)
    return ToolOutput.error(f"VM indisponible : {type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------- #
# Les outils
# ---------------------------------------------------------------------- #


def _timeout(limits: Mapping[str, int], jobs: int = 1) -> float:
    """Délai de loom pour un outil qui lance ``jobs`` exécutions, boot compris.

    Sans lui, loom couperait au bout de son délai par défaut, avant qu'execd
    ait rendu son verdict.
    """
    wall = limits.get("wall_ms", DEFAULT_WALL_MS) / 1000
    return BOOT_WAIT + jobs * (wall + 5) + EXEC_MARGIN


FORGE_DESCRIPTION: Final = (
    "Forge un nouvel outil Python, exécuté dans une VM isolée sans réseau "
    "(bibliothèque standard seulement). `code` est un module qui définit, au premier "
    "niveau, une fonction nommée comme l'outil (`name`) : elle reçoit les propriétés de "
    "`input_schema` par nom et rend une valeur JSON. `examples` : de 1 à 10 cas, "
    "exécutés dans la VM avant l'enregistrement ; `expected` doit être exactement la "
    "valeur rendue. Un outil du même nom est remplacé. L'outil forgé s'appelle "
    "aussitôt par `call`, et apparaît comme outil à part entière au run suivant."
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
            if not _same(execution.result, expected):
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
        validator = validator_for(forged.input_schema, default=Draft202012Validator)
        try:
            validator(forged.input_schema).validate(given)
        except ValidationError as exc:
            raise ToolError(f"arguments refusés par le schéma de {name} : {exc.message}") from None
        try:
            return _output(await self.run.execute(name, forged.code, given))
        except _ERRORS as exc:
            return _unavailable(exc)


@dataclass(frozen=True, slots=True)
class _ForgedTool:
    forged: Forged
    run: _Run
    spec: ToolSpec

    async def invoke(self, arguments: dict[str, JsonValue], context: ToolContext) -> ToolOutput:
        try:
            return _output(await self.run.execute(self.forged.name, self.forged.code, arguments))
        except _ERRORS as exc:
            return _unavailable(exc)


def _forge_spec(limits: Mapping[str, int]) -> ToolSpec:
    return ToolSpec(
        name=FORGE,
        description=FORGE_DESCRIPTION,
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
        timeout=_timeout(limits),
    )


def _forged_spec(forged: Forged, limits: Mapping[str, int]) -> ToolSpec:
    return ToolSpec(
        name=forged.name,
        description=forged.description,
        input_schema=forged.input_schema,
        kind="python",
        timeout=_timeout(limits),
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
        self, *, name: str, catalog: Catalog, runner: Runner, limits: Mapping[str, int]
    ) -> None:
        self.name = name
        self.required = False
        self.catalog = catalog
        self.runner = runner
        self.limits = dict(limits)

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
        run = _Run(self.runner, self.limits)
        tools: list[Tool] = [
            _ForgeTool(self.catalog, run, _forge_spec(self.limits)),
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


def forge_source(
    *,
    name: str,
    params: Mapping[str, JsonValue],
    secrets: Mapping[str, str],
    base_dir: Path,
) -> ForgeSource:
    """La fabrique du point d'entrée ``forge`` : vérifie ``params``, ne démarre rien."""
    unknown = sorted(set(params) - {"vm_dir", "catalog_dir", "limits"})
    if unknown:
        raise ValueError(f"params inconnus : {', '.join(unknown)} (vm_dir, catalog_dir, limits)")
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
    )
