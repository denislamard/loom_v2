# SPDX-License-Identifier: Apache-2.0
"""Vérifie qu'un lot de documentation n'a changé que de la documentation.

Deux contrôles, entre une référence git (``--base``, ``main`` par défaut) et
l'arbre de travail :

1. **Code inchangé.** Chaque module de ``src/`` est comparé sur son AST, sans
   ses docstrings (module, classe, fonction, attribut). Les commentaires ne
   sont pas dans l'AST : les modifier est permis. Toute autre différence (un ``Field``, une
   chaîne, un ordre) fait échouer le contrôle.
2. **Textes exposés.** Certaines docstrings ne sont pas que de la doc : le code
   les lit. On en prend un instantané des deux côtés :

   - ``llm`` (bloquant) : ce que voit le modèle d'un agent. Description et
     schéma des outils écrits comme fonctions (``@tool`` : la docstring est la
     description), et schémas d'arguments des outils fournis, où Pydantic
     recopie la docstring d'un modèle en ``description``.
   - ``schemas`` (à relire, non bloquant) : JSON Schema de chaque modèle
     Pydantic de ``loom_ia`` (config, agent, événements, API). La docstring
     d'un modèle y devient sa ``description`` : l'éditeur, l'OpenAPI et les
     clients MCP la montrent.
   - ``http`` (à relire, non bloquant) : docstrings des routes FastAPI, qui
     deviennent les descriptions de l'OpenAPI.

Rien n'est écrit dans le dépôt : la référence est extraite par ``git archive``
dans un dossier temporaire, et Python tourne sans écrire de bytecode.

Un troisième rapport, avec ``--lot``, liste ce qui reste sans docstring dans
les chemins du lot : modules, classes et fonctions publiques, classes privées,
fonctions privées longues.

Usage::

    uv run python scripts/verif_docs.py [--base REF] [--repo CHEMIN] [--python INTERPRÉTEUR] [--lot CHEMIN…]

Les conventions sont dans ``docs/docs_for_rag/conventions.md``.

Code de sortie : 0 si le code et la partie ``llm`` sont inchangés, 1 sinon.
"""

from __future__ import annotations

import argparse
import ast
import difflib
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import cast

# --- 1. AST sans docstrings -------------------------------------------------


def _is_text(statement: ast.stmt) -> bool:
    """Vrai pour une instruction qui n'est qu'une chaîne : docstring, ou docstring d'attribut."""
    return isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant) and isinstance(statement.value.value, str)


def _strip_docstrings(tree: ast.AST) -> ast.AST:
    """Retire les chaînes seules sur leur ligne ; un corps vidé reçoit ``pass``.

    Ce sont les docstrings de module, de classe et de fonction, et les
    docstrings d'attribut (une chaîne juste après une affectation, que griffe
    rattache à la constante ou au champ). Une chaîne seule n'a aucun effet à
    l'exécution, où qu'elle soit.
    """
    for node in ast.walk(tree):
        for field in ("body", "orelse", "finalbody"):
            block = getattr(node, field, None)
            if isinstance(block, list) and block and all(isinstance(item, ast.stmt) for item in cast(list[object], block)):
                statements = cast(list[ast.stmt], block)
                kept = [statement for statement in statements if not _is_text(statement)]
                setattr(node, field, kept or [ast.Pass()])
    return tree


def _code_of(source: str) -> str:
    return ast.dump(_strip_docstrings(ast.parse(source)), include_attributes=False)


def compare_code(base: Path, target: Path) -> list[str]:
    """Modules dont le code (hors docstrings) diffère, ou qui n'existent que d'un côté."""
    problems: list[str] = []
    before = {p.relative_to(base) for p in base.rglob("*.py")}
    after = {p.relative_to(target) for p in target.rglob("*.py")}
    for rel in sorted(before - after):
        problems.append(f"supprimé : {rel}")
    for rel in sorted(after - before):
        problems.append(f"ajouté : {rel}")
    for rel in sorted(before & after):
        try:
            same = _code_of((base / rel).read_text(encoding="utf-8")) == _code_of((target / rel).read_text(encoding="utf-8"))
        except SyntaxError as exc:
            problems.append(f"illisible : {rel} ({exc.msg}, ligne {exc.lineno})")
            continue
        if not same:
            problems.append(f"code modifié : {rel}")
    return problems


# --- 2. Textes exposés ------------------------------------------------------

# Exécuté dans un process à part, avec PYTHONPATH sur le ``src`` à photographier.
_SNAPSHOT = r"""
import importlib, json, pkgutil, sys, warnings
warnings.simplefilter("ignore")
import loom_ia
from pydantic import BaseModel

skipped = {}
for info in pkgutil.walk_packages(loom_ia.__path__, "loom_ia."):
    if info.name.endswith("__main__"):
        continue
    try:
        importlib.import_module(info.name)
    except Exception as exc:  # extra absent, dépendance système…
        skipped[info.name] = f"{type(exc).__name__}: {exc}"[:200]

def subclasses(cls):
    for sub in cls.__subclasses__():
        yield sub
        yield from subclasses(sub)

schemas = {}
for model in subclasses(BaseModel):
    if not model.__module__.startswith("loom_ia.") or "[" in model.__name__:
        continue
    key = f"{model.__module__}:{model.__qualname__}"
    for mode in ("validation", "serialization"):
        try:
            schemas[f"{key} ({mode})"] = model.model_json_schema(mode=mode)
        except Exception as exc:
            schemas[f"{key} ({mode})"] = f"erreur : {type(exc).__name__}"

llm = {}
# Outils écrits comme fonctions (``@tool``) : leur docstring est leur description.
from loom_ia.tools.python import FunctionTool
for name, module in sorted(sys.modules.items()):
    if not name.startswith("loom_ia."):
        continue
    for attr, value in sorted(vars(module).items()):
        if isinstance(value, FunctionTool) and getattr(value.fn, "__module__", None) == name:
            spec = value.spec
            llm[f"{name}:{attr} (outil {spec.name})"] = {"description": spec.description, "input_schema": spec.input_schema}
def _try(label, make):
    try:
        llm[label] = make()
    except Exception as exc:
        skipped[label] = f"{type(exc).__name__}: {exc}"[:200]
def _sql():
    from loom_ia.tools_builtin.sql.arguments import input_schema
    return input_schema()
def _excel():
    from loom_ia.tools_builtin.excel import arguments as a
    return {name: a.input_schema(getattr(a, name)) for name in dir(a) if isinstance(getattr(a, name), type) and issubclass(getattr(a, name), a.ExcelCall)}
def _documents():
    from loom_ia.tools_builtin._document import arguments as a
    return {name: a.input_schema(getattr(a, name)) for name in dir(a) if isinstance(getattr(a, name), type) and issubclass(getattr(a, name), a.DocumentCall)}
def _local_files():
    from loom_ia.tools_builtin.local_files import _Arguments
    return _Arguments.model_json_schema()
def _memory():
    from loom_ia.tools_builtin.memory.arguments import MemoryArguments
    return MemoryArguments.model_json_schema()
for label, make in (("loom.sql (schéma)", _sql), ("loom.excel (schémas)", _excel), ("loom.word/pdf (schémas)", _documents),
                    ("loom.local_files (schéma)", _local_files), ("loom.memory (schéma brut)", _memory)):
    _try(label, make)

json.dump({"llm": llm, "schemas": schemas, "skipped": skipped}, sys.stdout, ensure_ascii=False, sort_keys=True, default=str)
"""


def _http_docstrings(src: Path) -> dict[str, str | None]:
    """Docstrings des fonctions décorées par ``@router.<verbe>`` ou ``@app.<verbe>``."""
    found: dict[str, str | None] = {}
    verbs = {"get", "post", "put", "patch", "delete"}
    for path in sorted(src.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for deco in node.decorator_list:
                call = deco.func if isinstance(deco, ast.Call) else deco
                if isinstance(call, ast.Attribute) and call.attr in verbs and isinstance(call.value, ast.Name) and call.value.id in {"router", "app"}:
                    route = deco.args[0].value if isinstance(deco, ast.Call) and deco.args and isinstance(deco.args[0], ast.Constant) else "?"
                    found[f"{path.relative_to(src)} {call.attr.upper()} {route} {node.name}"] = ast.get_docstring(node)
    return found


def snapshot(src: Path, python: str) -> dict[str, dict[str, object]]:
    """Textes exposés du ``src`` donné, photographiés dans un process à part.

    Args:
        src: Dossier ``src`` à importer (mis en tête de ``PYTHONPATH``).
        python: Interpréteur qui a les dépendances de ``loom_ia``.

    Returns:
        Les sections ``llm``, ``schemas``, ``http`` et ``skipped`` (modules
        non importables, faute d'un extra).
    """
    env = {**os.environ, "PYTHONPATH": str(src.resolve()), "PYTHONDONTWRITEBYTECODE": "1"}
    with tempfile.TemporaryDirectory() as cwd:  # rien d'écrit à côté des sources
        done = subprocess.run([python, "-c", _SNAPSHOT], env=env, cwd=cwd, capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise SystemExit(f"Instantané impossible pour {src} :\n{done.stderr[-2000:]}")
    data = cast(dict[str, dict[str, object]], json.loads(done.stdout))
    data["http"] = dict(_http_docstrings(src))
    return data


def _changes(before: dict[str, object], after: dict[str, object]) -> list[str]:
    lines: list[str] = []
    for key in sorted(before.keys() | after.keys()):
        if key not in after:
            lines.append(f"  - disparu : {key}")
        elif key not in before:
            lines.append(f"  - nouveau : {key}")
        elif before[key] != after[key]:
            old = json.dumps(before[key], ensure_ascii=False, indent=1, sort_keys=True).splitlines()
            new = json.dumps(after[key], ensure_ascii=False, indent=1, sort_keys=True).splitlines()
            diff = [d for d in difflib.unified_diff(old, new, lineterm="", n=0) if not d.startswith(("---", "+++", "@@"))]
            lines.append(f"  - modifié : {key}")
            lines.extend(f"      {d[:200]}" for d in diff[:12])
    return lines


# --- 3. Couverture du lot ---------------------------------------------------

LONG_PRIVATE = 15  # lignes à partir desquelles une fonction privée se documente aussi


def coverage(src: Path, prefixes: list[str]) -> tuple[int, int, list[str]]:
    """Symboles à documenter sous ``prefixes`` : (attendus, documentés, manquants).

    Attendus : chaque module, chaque classe et fonction publique, et chaque
    fonction privée d'au moins ``LONG_PRIVATE`` lignes. Les méthodes magiques
    sont exclues, sauf ``__call__`` et ``__init__`` d'au moins autant de lignes.
    """
    expected = documented = 0
    missing: list[str] = []
    for path in sorted(src.rglob("*.py")):
        rel = path.relative_to(src).as_posix()
        if prefixes and not any(rel.startswith(p.strip("/")) for p in prefixes):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        expected += 1
        if ast.get_docstring(tree):
            documented += 1
        else:
            missing.append(f"{rel} (module)")

        def visit(body: list[ast.stmt], owner: str, rel: str = rel) -> None:
            nonlocal expected, documented
            for node in body:
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    continue
                name = node.name
                size = (node.end_lineno or node.lineno) - node.lineno + 1
                magic = name.startswith("__") and name.endswith("__")
                if magic:
                    wanted = name in {"__call__", "__init__"} and size >= LONG_PRIVATE
                elif name.startswith("_"):
                    wanted = isinstance(node, ast.ClassDef) or size >= LONG_PRIVATE
                else:
                    wanted = True
                if wanted:
                    expected += 1
                    if ast.get_docstring(node):
                        documented += 1
                    else:
                        missing.append(f"{rel}:{node.lineno} {owner}{name}")
                if isinstance(node, ast.ClassDef):
                    visit(node.body, f"{owner}{name}.", rel)

        visit(tree.body, "")
    return expected, documented, missing


# --- Programme ---------------------------------------------------------------


def _extract(repo: Path, ref: str, into: Path) -> Path:
    done = subprocess.run(["git", "-C", str(repo), "archive", ref, "src"], capture_output=True, check=False)
    if done.returncode != 0:
        raise SystemExit(f"Référence {ref!r} illisible : {done.stderr.decode(errors='replace').strip()}")
    archive = done.stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(into, filter="data")
    return into / "src"


def _section(title: str, lines: list[str]) -> None:
    print(title)
    for line in lines:
        print(line)


def main() -> int:
    """Lance les contrôles et affiche le rapport ; 1 si le code ou la partie ``llm`` a changé."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--base", default="main", help="référence git à comparer (défaut : main)")
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="racine du dépôt (défaut : dossier courant)")
    parser.add_argument("--python", default=sys.executable, help="interpréteur qui a les dépendances de loom_ia")
    parser.add_argument("--lot", nargs="*", default=[], metavar="CHEMIN", help="chemins du lot sous src/, pour le rapport de couverture")
    args = parser.parse_args()
    target = args.repo / "src"
    with tempfile.TemporaryDirectory() as tmp:
        base = _extract(args.repo, args.base, Path(tmp))
        code = compare_code(base, target)
        before, after = snapshot(base, args.python), snapshot(target, args.python)
    llm = _changes(before["llm"], after["llm"])
    schemas = _changes(before["schemas"], after["schemas"])
    http = _changes(before["http"], after["http"])
    touched = sum(line.startswith("  - ") for line in schemas)
    print(f"Base : {args.base}")
    _section(f"1. Code hors docstrings : {'inchangé' if not code else f'{len(code)} module(s) en écart'}", [f"  - {line}" for line in code])
    _section(f"2. Vu par le modèle (bloquant) : {'inchangé' if not llm else 'MODIFIÉ'}", llm)
    _section(f"3. Schémas JSON (à relire) : {'inchangés' if not schemas else f'{touched} modèle(s) modifié(s)'}", schemas)
    _section(f"4. Descriptions OpenAPI (à relire) : {'inchangées' if not http else 'modifiées'}", http)
    skipped = after.get("skipped", {})
    if skipped:
        print(f"Non photographiés ({len(skipped)}, extras absents ?) : " + ", ".join(sorted(skipped)))
    if args.lot:
        expected, documented, missing = coverage(target, args.lot)
        print(f"5. Couverture du lot : {documented}/{expected} ({100 * documented // max(1, expected)} %)")
        for item in missing:
            print(f"  - sans docstring : {item}")
    return 1 if code or llm else 0


if __name__ == "__main__":
    raise SystemExit(main())
