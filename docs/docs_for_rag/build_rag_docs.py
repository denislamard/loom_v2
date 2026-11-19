# SPDX-License-Identifier: Apache-2.0
"""Génère les pages Markdown du RAG de loom_v2, et le JSONL à charger dans ``loom.memory``.

Le RAG lit des documents, pas du Python : ce script tire du dépôt des pages
Markdown, découpées par titres, que ``loom.memory`` range en passages. Chaque
titre porte le nom complet de ce qu'il décrit, pour qu'un passage isolé dise
encore de quoi il parle.

Cinq sortes de pages, dans ``docs/docs_for_rag/generated_docs/`` :

- ``code/`` : une page par module de ``src/loom_ia``, tirée des docstrings
  (lues dans l'AST, sans importer le module). Un module qui ne contient que
  des constantes n'a pas de page ;
- ``config/`` : la référence de ``loom.yaml`` et d'``agent.yaml``, tirée des
  modèles Pydantic : chaque clé avec son chemin, son type, son défaut, ses
  bornes et sa description ;
- ``tools/`` : une page par outil fourni (``loom.<outil>``) : comment
  l'activer, ses réglages sous sa référence dans ``agent.yaml``, et les
  arguments que le modèle lui donne quand ils ne dépendent pas de la config ;
- ``cli/`` : une page par commande ``loom``, tirée de l'analyseur argparse
  et de la docstring de la fonction qui l'exécute ;
- ``rest/`` : les routes HTTP, tirées des fonctions décorées par
  ``@app.<verbe>`` ou ``@router.<verbe>``.

Le JSONL (``docs/docs_for_rag/generated_docs.jsonl``) reprend ces pages et
les documents existants retenus (``EXISTING_DOCS``), au format de
``loom memory export`` : il se charge par ``loom memory import``. Un document
de plus de ``SPLIT_OVER`` caractères y est découpé à ses titres ``##``, puis
``###`` pour une section encore trop grosse (``split_page``) : ``get`` rend
alors une section qu'un agent peut lire, et la recherche, qui rend au plus
quelques passages par document, en trouve dans plus de sections. Les fichiers
sur le disque ne sont pas découpés. Un document a un identifiant stable, tiré
de son chemin et de sa section : un jeu doré peut s'y référer. ``import``
saute un document déjà présent, même modifié : après une régénération, on
recharge une base vide.

Les pages sont écrites seulement si leur texte change, et une page qui n'est
plus générée est supprimée.

Usage::

    uv run python docs/docs_for_rag/build_rag_docs.py [--repo CHEMIN]
"""

from __future__ import annotations

import argparse
import ast
import importlib
import importlib.util
import inspect
import json
import os
import re
import subprocess
import sys
import types
import uuid
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import (
    Annotated,
    Any,
    Literal,
    TypeAliasType,
    Union,
    cast,
    get_args,
    get_origin,
)

from pydantic import BaseModel
from pydantic.fields import FieldInfo

OUT: str = "docs/docs_for_rag/generated_docs"
"""Le dossier des pages générées, relatif à la racine du dépôt."""
JSONL: str = "docs/docs_for_rag/generated_docs.jsonl"
"""Le fichier à charger par ``loom memory import``, relatif à la racine du dépôt."""
PROJECT: str = "loom_v2"
"""Le projet des documents dans la mémoire."""
EXISTING_DOCS: tuple[str, ...] = (
    "README.md",
    "docs/configuration.md",
    "docs/conception.md",
    "docs/fonctions.md",
    "docs/style_format.md",
    "docs/loom-mem-gpu.md",
    "docs/loom_role_user.md",
    "docs/state-et-context.md",
    "docs/docs_for_rag/glossaire.md",
    "docs/howto/01-demarrer.md",
    "docs/howto/02-maitriser.md",
    "docs/howto/03-production.md",
    "docs/howto/04-qualite-et-expert.md",
    "docs/howto/05-state-de-session.md",
    "docs/howto/06-outils-fournis.md",
)
"""Les documents existants qui vont dans le RAG, tels quels. Les analyses d'impact n'y vont pas."""
SPLIT_OVER: int = 8000
"""Au-delà, en caractères, un document est découpé à ses titres dans le JSONL."""
DOC_NAMESPACE: uuid.UUID = uuid.uuid5(uuid.NAMESPACE_URL, "https://github.com/denislamard/loom_v2")
"""L'espace des identifiants de documents : ``uuid5(DOC_NAMESPACE, chemin)``."""


@dataclass(frozen=True, slots=True)
class Page:
    """Un document du RAG.

    Attributes:
        path: Son chemin, relatif à la racine du dépôt.
        title: Son titre : la racine du chemin de titres de chaque passage.
        tags: Ses tags dans la mémoire.
        text: Son texte Markdown.
        generated: Vrai pour une page de ``generated_docs`` ; faux pour un document existant.
    """

    path: str
    title: str
    tags: tuple[str, ...]
    text: str
    generated: bool = True


# --- Docstrings en Markdown ------------------------------------------------------

_SECTIONS: dict[str, str] = {
    "Args": "Arguments",
    "Arguments": "Arguments",
    "Parameters": "Arguments",
    "Attributes": "Attributs",
    "Returns": "Renvoie",
    "Yields": "Produit",
    "Raises": "Lève",
    "Example": "Exemple",
    "Examples": "Exemples",
    "Note": "Note",
    "Notes": "Notes",
    "Warning": "Attention",
    "See Also": "Voir aussi",
}
"""Les sections Google reconnues, et leur intitulé dans les pages."""
_ENTRY_SECTIONS: frozenset[str] = frozenset({"Args", "Arguments", "Parameters", "Attributes", "Raises"})
"""Les sections faites d'entrées « nom : texte »."""
_ENTRY: re.Pattern[str] = re.compile(r"^(\*{0,2}[\w.\[\]]+)(?:\s*\(([^)]*)\))?\s*:\s*(.*)$")
_DOUBLE_TICKS: re.Pattern[str] = re.compile(r"``([^`]+)``")


def _inline(text: str) -> str:
    """Le balisage reST en ligne passé en Markdown : ````x```` devient ```x```."""
    return _DOUBLE_TICKS.sub(r"`\1`", text)


def _escape_heading(line: str) -> str:
    """Une ligne qui commencerait un titre Markdown est échappée : seul le générateur pose des titres."""
    return "\\" + line if line.lstrip().startswith("#") else line


def docstring_markdown(doc: str) -> str:
    """Une docstring Google en Markdown : sections en listes, blocs ``::`` en blocs de code.

    Les lignes qui ressembleraient à un titre (« # … ») sont échappées hors des
    blocs de code : seul le générateur décide des titres, donc du découpage.
    """
    lines = inspect.cleandoc(doc).splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        name = line.strip().rstrip(":")
        if line.strip().endswith(":") and not line.startswith(" ") and name in _SECTIONS:
            i, block = _section(lines, i + 1, name)
            out.extend(["", f"**{_SECTIONS[name]} :**", "", *block, ""])
            continue
        if line.rstrip().endswith("::") and i + 1 < len(lines):
            out.append(_escape_heading(_inline(line.rstrip()[:-1])))
            i, code = _indented(lines, i + 1)
            out.extend(["", "```", *code, "```", ""])
            continue
        out.append(_escape_heading(_inline(line)))
        i += 1
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def _indented(lines: list[str], start: int) -> tuple[int, list[str]]:
    """Le bloc indenté qui commence à ``start`` (lignes vides en tête sautées), dédenté ; et la ligne qui le suit."""
    i = start
    while i < len(lines) and not lines[i].strip():
        i += 1
    block: list[str] = []
    while i < len(lines) and (not lines[i].strip() or lines[i].startswith(" ")):
        block.append(lines[i])
        i += 1
    while block and not block[-1].strip():
        block.pop()
    width = min((len(b) - len(b.lstrip()) for b in block if b.strip()), default=0)
    return i, [b[width:] for b in block]


def _section(lines: list[str], start: int, name: str) -> tuple[int, list[str]]:
    """Le corps d'une section Google, en Markdown ; et la ligne qui la suit."""
    i, body = _indented(lines, start)
    if name not in _ENTRY_SECTIONS:
        if name in ("Example", "Examples") and any(b.lstrip().startswith(">>>") for b in body):
            return i, ["```", *body, "```"]
        return i, [_escape_heading(_inline(b)) for b in body]
    entries: list[str] = []
    for raw in body:
        if raw and not raw.startswith(" "):
            found = _ENTRY.match(raw.strip())
            if found:
                key, kind, text = found.groups()
                label = f"`{key}`" + (f" ({_inline(kind)})" if kind else "")
                entries.append(f"- {label} : {_inline(text)}".rstrip())
                continue
        if entries and raw.strip():
            entries[-1] += " " + _inline(raw.strip())
        elif raw.strip():
            entries.append("- " + _inline(raw.strip()))
    return i, entries


# --- 1. Une page par module -------------------------------------------------------


def _docstring_of(node: ast.AST) -> str | None:
    if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
        return ast.get_docstring(node, clean=True)
    return None


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """La signature d'une fonction telle qu'écrite, décorateurs compris."""
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    params = f"[{', '.join(ast.unparse(p) for p in node.type_params)}]" if node.type_params else ""
    returns = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    decorators = "".join(f"@{ast.unparse(d)}\n" for d in node.decorator_list)
    return f"{decorators}{prefix} {node.name}{params}({ast.unparse(node.args)}){returns}"


def _class_line(node: ast.ClassDef) -> str:
    """La ligne ``class`` telle qu'écrite, décorateurs compris."""
    bases = [ast.unparse(b) for b in (*node.bases, *node.keywords)]
    decorators = "".join(f"@{ast.unparse(d)}\n" for d in node.decorator_list)
    return f"{decorators}class {node.name}" + (f"({', '.join(bases)})" if bases else "")


def _short(value: str, limit: int = 120) -> str:
    flat = " ".join(value.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _constants(body: Sequence[ast.stmt]) -> list[tuple[str, str, str]]:
    """Les constantes documentées d'un corps : (nom, valeur ou type, docstring d'attribut)."""
    found: list[tuple[str, str, str]] = []
    for stmt, following in zip(body, (*body[1:], None), strict=True):
        if not (isinstance(following, ast.Expr) and isinstance(following.value, ast.Constant) and isinstance(following.value.value, str)):
            continue
        doc = inspect.cleandoc(following.value.value)
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
            found.append((stmt.targets[0].id, f"= {_short(ast.unparse(stmt.value))}", doc))
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            shown = f"= {_short(ast.unparse(stmt.value))}" if stmt.value else f": {_short(ast.unparse(stmt.annotation))}"
            found.append((stmt.target.id, shown, doc))
        elif isinstance(stmt, ast.TypeAlias):
            found.append((stmt.name.id, f"= {_short(ast.unparse(stmt.value))}", doc))
    return found


def _wanted(node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Un symbole a sa section s'il est documenté, ou public."""
    return _docstring_of(node) is not None or not node.name.startswith("_")


def _function_md(node: ast.FunctionDef | ast.AsyncFunctionDef, qualname: str, level: int, kind: str) -> list[str]:
    doc = _docstring_of(node)
    out = [
        f"{'#' * level} {kind} `{qualname}`",
        "",
        "```python",
        _signature(node),
        "```",
        "",
    ]
    out += [docstring_markdown(doc), ""] if doc else ["(sans docstring)", ""]
    return out


def _class_md(node: ast.ClassDef, qualname: str, level: int) -> list[str]:
    doc = _docstring_of(node)
    out = [
        f"{'#' * level} Classe `{qualname}`",
        "",
        "```python",
        _class_line(node),
        "```",
        "",
    ]
    out += [docstring_markdown(doc), ""] if doc else ["(sans docstring)", ""]
    for stmt in node.body:
        if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef) and _wanted(stmt):
            out += _function_md(stmt, f"{qualname}.{stmt.name}", level + 1, "Méthode")
        elif isinstance(stmt, ast.ClassDef) and _wanted(stmt):
            out += _class_md(stmt, f"{qualname}.{stmt.name}", level + 1)
    return out


def module_page(path: Path, src: Path) -> Page | None:
    """La page d'un module, ou ``None`` s'il ne contient que des constantes (un ``__init__`` documenté a la sienne)."""
    rel = path.relative_to(src).with_suffix("")
    parts = rel.parts[:-1] if rel.name == "__init__" else rel.parts
    module = ".".join(parts)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    doc = ast.get_docstring(tree, clean=True)
    defined = [s for s in tree.body if isinstance(s, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)]
    if not defined and not (rel.name == "__init__" and doc):
        return None
    where = path.relative_to(src.parent).as_posix()
    kind = "Paquet" if rel.name == "__init__" else "Module"
    lines = [
        f"# {module}",
        "",
        f"{kind} `{module}` de loom_v2 (fichier `src/{where.removeprefix('src/')}`).",
        "",
    ]
    if doc:
        lines += [docstring_markdown(doc), ""]
    constants = _constants(tree.body)
    if constants:
        lines += [f"## Constantes de `{module}`", ""]
        lines += [f"- `{name} {shown}` : {_inline(text)}".replace("\n", " ") for name, shown, text in constants]
        lines.append("")
    for stmt in defined:
        if not _wanted(stmt):
            continue
        if isinstance(stmt, ast.ClassDef):
            lines += _class_md(stmt, f"{module}.{stmt.name}", 2)
        else:
            lines += _function_md(stmt, f"{module}.{stmt.name}", 2, "Fonction")
    name = f"{module}.md"
    return Page(f"{OUT}/code/{name}", module, ("code",), "\n".join(lines).strip() + "\n")


def code_pages(repo: Path) -> list[Page]:
    """Les pages de tous les modules de ``src/loom_ia``."""
    src = repo / "src"
    pages = (module_page(path, src) for path in sorted((src / "loom_ia").rglob("*.py")) if "__pycache__" not in path.parts)
    return [page for page in pages if page is not None]


# --- 2. La référence de la configuration ------------------------------------------


def attribute_docs(model: type) -> dict[str, str]:
    """La section ``Attributes`` de la docstring d'une classe, par nom de champ, en une ligne chacun."""
    doc = inspect.cleandoc(model.__doc__ or "")
    lines = doc.splitlines()
    found: dict[str, str] = {}
    for i, line in enumerate(lines):
        if line.strip() == "Attributes:" and not line.startswith(" "):
            _, body = _indented(lines, i + 1)
            current: str | None = None
            for raw in body:
                entry = _ENTRY.match(raw.strip()) if raw and not raw.startswith(" ") else None
                if entry:
                    key = entry.group(1)
                    found[key] = entry.group(3)
                    current = key
                elif current and raw.strip():
                    found[current] += " " + raw.strip()
    return {key: _inline(" ".join(text.split())) for key, text in found.items()}


def summary_of(model: type) -> str:
    """Le premier paragraphe de la docstring d'une classe, en une ligne."""
    doc = inspect.cleandoc(model.__doc__ or "")
    return _inline(" ".join(doc.split("\n\n")[0].split()))


def description_of(model: type) -> str:
    """La docstring d'une classe en Markdown, sans sa section ``Attributes`` (ses champs sont listés à part).

    Un modèle de config peut lire une autre forme que ses champs (une liste
    ramenée à une table par un validateur) : sa docstring le dit, et la page
    doit le dire aussi.
    """
    doc = inspect.cleandoc(model.__doc__ or "")
    kept = re.split(r"^Attributes:\s*$", doc, maxsplit=1, flags=re.MULTILINE)[0]
    return docstring_markdown(kept)


def _is_model(value: object) -> bool:
    return isinstance(value, type) and issubclass(value, BaseModel)


def type_text(annotation: object) -> str:
    """Un type lisible : ``list[str] | None``, ``"a" | "b"`` pour un ``Literal``."""
    if annotation is None or annotation is type(None):
        return "null"
    if isinstance(annotation, TypeAliasType):
        return annotation.__name__
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is Annotated:
        return type_text(args[0])
    if origin is Literal:
        return " | ".join(json.dumps(a, ensure_ascii=False) for a in args)
    if origin in (Union, types.UnionType):
        return " | ".join(type_text(a) for a in args)
    if origin is not None:
        name = getattr(origin, "__name__", str(origin))
        if args:
            shown = [type_text(a) if a is not Ellipsis else "..." for a in args]
            return f"{name}[{', '.join(shown)}]"
        return str(name)
    if isinstance(annotation, type):
        return annotation.__name__
    return str(annotation).replace("typing.", "")


def models_in(annotation: object, suffix: str = "") -> Iterator[tuple[str, type[BaseModel]]]:
    """Les modèles Pydantic d'un type, avec le suffixe de chemin qui y mène (``[]`` pour une liste, ``.<nom>`` pour une table)."""
    if isinstance(annotation, TypeAliasType):
        yield from models_in(annotation.__value__, suffix)
        return
    if _is_model(annotation):
        yield suffix, cast(type[BaseModel], annotation)
        return
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is Annotated:
        yield from models_in(args[0], suffix)
    elif origin in (Union, types.UnionType):
        for arg in args:
            yield from models_in(arg, suffix)
    elif origin in (list, tuple, set, frozenset) or getattr(origin, "__name__", "") in (
        "Sequence",
        "Iterable",
        "AbstractSet",
    ):
        for arg in args:
            if arg is not Ellipsis:
                yield from models_in(arg, suffix + "[]")
    elif origin is dict or getattr(origin, "__name__", "") == "Mapping":
        if len(args) == 2:
            yield from models_in(args[1], suffix + ".<nom>")


def value_text(value: object) -> str:
    """Une valeur par défaut écrite comme en YAML."""
    if isinstance(value, BaseModel):
        return "les défauts de la section"
    if isinstance(value, frozenset | set):
        value = sorted(cast(set[Any], value), key=str)
    if isinstance(value, tuple):
        value = list(cast(tuple[Any, ...], value))
    try:
        return _short(json.dumps(value, ensure_ascii=False, default=str), 100)
    except TypeError, ValueError:
        return _short(repr(value), 100)


def bounds_text(info: FieldInfo) -> str:
    """Les bornes d'un champ (``ge``, ``le``, longueurs…), d'après ses métadonnées."""
    said: list[str] = []
    for meta in info.metadata:
        for attr, sign in (
            ("ge", "≥"),
            ("gt", ">"),
            ("le", "≤"),
            ("lt", "<"),
            ("min_length", "longueur ≥"),
            ("max_length", "longueur ≤"),
        ):
            bound = getattr(meta, attr, None)
            if bound is not None:
                said.append(f"{sign} {bound}")
        pattern = getattr(meta, "pattern", None)
        if pattern is not None:
            said.append(f"motif `{pattern}`")
    return ", ".join(said)


def yaml_key(name: str, info: FieldInfo) -> str:
    """La clé écrite dans le YAML : l'alias du champ s'il en a un."""
    alias = info.validation_alias if isinstance(info.validation_alias, str) else info.alias
    return alias or name


def section_defaults(made: BaseModel, limit: int = 600) -> str:
    """Les défauts d'une section, à plat et sans les valeurs nulles : « `events.backend: memory`… ».

    La section et ses sous-sections peuvent finir dans des documents séparés
    du RAG : la clé qui les contient dit donc elle-même ce qu'elles valent par
    défaut.
    """
    leaves: list[str] = []

    def walk(value: object, prefix: str) -> None:
        if isinstance(value, Mapping) and value:
            for key, inner in cast(Mapping[str, object], value).items():
                walk(inner, f"{prefix}.{key}" if prefix else key)
        elif value is not None:
            leaves.append(f"`{prefix}: {value_text(cast(object, value))}`")

    walk(made.model_dump(mode="json", by_alias=True), "")
    said = ", ".join(leaves)
    return f"les défauts de la section ({_short(said, limit)})" if leaves else "les défauts de la section"


def field_line(path: str, name: str, info: FieldInfo, docs: Mapping[str, str]) -> str:
    """Une clé : chemin, type, obligatoire ou défaut, bornes, description."""
    facts = [f"type `{type_text(info.annotation)}`"]
    if info.is_required():
        facts.append("obligatoire")
    elif info.default_factory is not None:
        try:
            made = cast(Any, info.default_factory)()
        except TypeError:
            made = None
        if made is not None:
            facts.append(f"défaut : `{value_text(made)}`" if not isinstance(made, BaseModel) else f"défaut : {section_defaults(made)}")
    elif isinstance(info.default, BaseModel):
        facts.append(f"défaut : {section_defaults(info.default)}")
    else:
        facts.append(f"défaut : `{value_text(info.default)}`")
    bounds = bounds_text(info)
    if bounds:
        facts.append(f"bornes : {bounds}")
    text = docs.get(name) or _inline(info.description or "") or "(sans description)"
    return f"- `{path}` ({' ; '.join(facts)}) : {text}"


@dataclass(slots=True)
class Reference:
    """Ce qui est déjà décrit, pour renvoyer à la première description d'un modèle plutôt que la répéter.

    Attributes:
        seen: Modèle → le chemin où il est décrit, et la page.
    """

    seen: dict[type[BaseModel], tuple[str, str]]


def _labelled(
    found: Sequence[tuple[str, type[BaseModel]]],
) -> list[tuple[str, type[BaseModel], str]]:
    """Les modèles trouvés sous des chemins, chacun nommé quand plusieurs partagent un chemin (une union)."""
    counts: dict[str, int] = {}
    for path, _ in found:
        counts[path] = counts.get(path, 0) + 1
    out: list[tuple[str, type[BaseModel], str]] = []
    for path, model in found:
        builtin = _builtin_of(model)
        label = f"référence `python: {builtin}`" if builtin else f"forme `{model.__name__}`"
        out.append((path, model, label if counts[path] > 1 else ""))
    return out


def model_sections(
    model: type[BaseModel],
    path: str,
    page: str,
    ref: Reference,
    depth: int = 0,
    label: str = "",
    level: int = 2,
) -> list[str]:
    """Les sections d'un modèle et de ceux qu'il contient : une par chemin, ses clés en liste.

    Un modèle déjà décrit renvoie à sa première description. La référence d'un
    outil fourni (``python: loom.<outil>``) renvoie à la page de l'outil.
    """
    heading = f"{'#' * level} `{path}`" + (f" ({label})" if label else "")
    builtin = _builtin_of(model)
    if builtin is not None:
        said = f"La référence `python: {builtin}` : les clés communes d'un outil Python, et les réglages de l'outil, décrits page « Outil fourni {builtin} »."
        return [heading, "", said, ""]
    if model in ref.seen:
        where, other = ref.seen[model]
        return [heading, "", f"Même forme que `{where}` (page « {other} »).", ""]
    ref.seen[model] = (path, page)
    docs = attribute_docs(model)
    out = [
        heading,
        "",
        description_of(model),
        "",
        f"Modèle Pydantic : `{model.__module__}.{model.__qualname__}`.",
        "",
    ]
    nested: list[tuple[str, type[BaseModel]]] = []
    for name, info in model.model_fields.items():
        key = yaml_key(name, info)
        out.append(field_line(f"{path}.{key}", name, info, docs))
        for suffix, inner in models_in(info.annotation):
            nested.append((f"{path}.{key}{suffix}", inner))
    out.append("")
    if depth < 8:
        for inner_path, inner, inner_label in _labelled(nested):
            out += model_sections(inner, inner_path, page, ref, depth + 1, inner_label, level)
    return out


def _builtin_of(model: type[BaseModel]) -> str | None:
    """La référence ``loom.<outil>`` dont ``model`` est le modèle dans ``agent.yaml``, ou ``None``."""
    references = cast(
        Mapping[str, type],
        importlib.import_module("loom_ia.agents.spec").BUILTIN_REFERENCES,
    )
    return next((name for name, built in references.items() if built is model), None)


def config_pages(file: str, root: type[BaseModel], ref: Reference, slug: str) -> list[Page]:
    """La référence d'un fichier de configuration : une page de ses clés de premier niveau, puis une par clé qui est une section."""
    docs = attribute_docs(root)
    overview_title = f"{file} : les clés de premier niveau"
    lines = [
        f"# {overview_title}",
        "",
        summary_of(root),
        "",
        f"Modèle Pydantic : `{root.__module__}.{root.__qualname__}`.",
        "",
    ]
    pages: list[Page] = []
    sections: list[tuple[str, list[tuple[str, type[BaseModel]]]]] = []
    for name, info in root.model_fields.items():
        key = yaml_key(name, info)
        lines.append(field_line(key, name, info, docs))
        inner = list(models_in(info.annotation))
        if inner:
            sections.append((key, [(f"{key}{suffix}", model) for suffix, model in inner]))
    pages.append(
        Page(
            f"{OUT}/config/{slug}.md",
            overview_title,
            ("config",),
            "\n".join(lines) + "\n",
        )
    )
    ref.seen[root] = ("(racine)", overview_title)
    for key, found in sections:
        title = f"{file} : la clé {key}"
        body = [
            f"# {title}",
            "",
            f"La clé `{key}` de `{file}` et tout ce qu'elle contient.",
            "",
        ]
        for path, model, label in _labelled(found):
            body += model_sections(model, path, title, ref, label=label)
        pages.append(
            Page(
                f"{OUT}/config/{slug}.{key}.md",
                title,
                ("config",),
                "\n".join(body).strip() + "\n",
            )
        )
    return pages


# --- 3. Les outils fournis --------------------------------------------------------


def _schema_lines(schema: Mapping[str, object]) -> list[str]:
    """Les propriétés de premier niveau d'un schéma d'arguments : nom, type, obligatoire, description."""
    properties = cast(Mapping[str, Mapping[str, object]], schema.get("properties") or {})
    required = set(cast(list[str], schema.get("required") or []))
    defs = cast(Mapping[str, Mapping[str, object]], schema.get("$defs") or {})
    out: list[str] = []
    for name, prop in properties.items():
        kind = _schema_kind(prop, defs)
        said = " ; obligatoire" if name in required else ""
        text = str(prop.get("description") or "(sans description)")
        out.append(f"- `{name}` (`{kind}`{said}) : {text}")
    return out


def _schema_kind(node: Mapping[str, object], defs: Mapping[str, Mapping[str, object]], depth: int = 0) -> str:
    """Le type d'une propriété de schéma JSON, lisible : ``"a" | "b"``, ``array[string]``, ``string | null``."""
    ref = node.get("$ref")
    if isinstance(ref, str) and depth < 4:
        target = defs.get(ref.rsplit("/", 1)[-1], {})
        return _schema_kind(target, defs, depth + 1) if "enum" in target or "anyOf" in target else ref.rsplit("/", 1)[-1]
    enum = node.get("enum")
    if isinstance(enum, list):
        return " | ".join(json.dumps(v, ensure_ascii=False) for v in cast(list[object], enum))
    if "const" in node:
        return json.dumps(node["const"], ensure_ascii=False)
    variants = node.get("anyOf") or node.get("oneOf")
    if isinstance(variants, list):
        return " | ".join(_schema_kind(cast(Mapping[str, object], v), defs, depth + 1) for v in cast(list[object], variants))
    kind = node.get("type")
    if kind == "array":
        items = node.get("items")
        inner = _schema_kind(cast(Mapping[str, object], items), defs, depth + 1) if isinstance(items, Mapping) else "?"
        return f"array[{inner}]"
    if isinstance(kind, list):
        return " | ".join(str(k) for k in cast(list[object], kind))
    return str(kind) if kind else "objet"


def _arguments_schema(name: str) -> tuple[str, Mapping[str, object]] | None:
    """La description et le schéma d'arguments d'un outil, quand ils ne dépendent pas de la config."""
    from loom_ia.tools.python import FunctionTool
    from loom_ia.tools_builtin import BUILTIN_TOOLS

    entry = BUILTIN_TOOLS[name]
    if isinstance(entry, str):
        module, attr = entry.split(":")
        tool = getattr(importlib.import_module(module), attr)
        if isinstance(tool, FunctionTool):
            return tool.spec.description, tool.spec.input_schema
        return None
    match name:
        case "loom.sql":
            sql = module_alone("loom_ia.tools_builtin.sql.arguments")
            return "", cast(Mapping[str, object], sql.input_schema())
        case "loom.memory":
            memory = module_alone("loom_ia.tools_builtin.memory.arguments")
            return "", cast(type[BaseModel], memory.MemoryArguments).model_json_schema()
        case "loom.local_files":
            from loom_ia.tools_builtin.local_files import _Arguments  # pyright: ignore[reportPrivateUsage]

            return "", _Arguments.model_json_schema()
        case _:
            return None


def module_docstring(module: str) -> str | None:
    """La docstring d'un module, lue dans son fichier, sans l'importer.

    Le module d'un outil fourni peut importer ce qu'apporte son extra
    (``openpyxl`` pour ``loom.excel``…) : la doc se génère sans les extras.
    """
    spec = importlib.util.find_spec(module)
    if spec is None or spec.origin is None:
        return None
    return ast.get_docstring(ast.parse(Path(spec.origin).read_text(encoding="utf-8")), clean=True)


def module_alone(module: str) -> types.ModuleType:
    """Un module importé seul, sans le ``__init__`` de son paquet.

    ``loom_ia.tools_builtin.sql`` importe à son ``__init__`` ce qu'apporte
    l'extra ``sql`` ; son module ``arguments`` n'en a pas besoin. Le module est
    chargé depuis son fichier, sous un nom à part.
    """
    loom = importlib.import_module("loom_ia")
    path = Path(cast(str, loom.__file__)).parent.joinpath(*module.split(".")[1:]).with_suffix(".py")
    spec = importlib.util.spec_from_file_location(f"_rag_{module.replace('.', '_')}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"{module} introuvable ({path})")
    found = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = found
    spec.loader.exec_module(found)
    return found


def tool_pages(ref: Reference) -> list[Page]:
    """Une page par outil fourni : activation, réglages dans ``agent.yaml``, arguments du modèle."""
    from loom_ia.tools_builtin import BUILTIN_TOOLS

    pages: list[Page] = []
    for name, entry in sorted(BUILTIN_TOOLS.items()):
        title = f"Outil fourni {name}"
        target = entry if isinstance(entry, str) else entry.target
        intro = f"L'outil `{name}` est fourni avec loom_v2. Un agent l'active dans son `agent.yaml` : `- python: {name}` dans `tools`."
        lines = [f"# {title}", "", intro, ""]
        params = None if isinstance(entry, str) else entry.params
        # Ce que fait l'outil, puis son mode d'emploi (le module de ses paramètres) : la
        # section que trouvent les questions générales (« quelles actions ? quels modèles ? »).
        about = [module_docstring(target.split(":")[0]), module_docstring(params.split(":")[0]) if params else None]
        if any(about):
            lines += [f"## Ce que fait {name}", ""]
            lines += [part for text in about if text for part in (docstring_markdown(text), "")]
        lines += [f"## Comment {name} est monté", "", f"- cible : `{target}`"]
        if not isinstance(entry, str):
            if entry.extra:
                lines.append(f"- extra de loom-ia à installer : `loom-ia[{entry.extra}]`")
            if entry.refused:
                refused = ", ".join(f"`{r}`" for r in entry.refused)
                lines.append(f"- réglages communs refusés sur sa référence : {refused}, car {entry.refused_because}")
            if entry.own_description:
                lines.append("- l'outil tire sa description de ses réglages : `description` est refusé, `skill` ajoute du texte")
        lines.append("")
        if params:
            module_name, model_name = params.split(":")
            model = cast(
                type[BaseModel],
                getattr(importlib.import_module(module_name), model_name),
            )
            lines += [
                f"## Les paramètres de {name} dans agent.yaml (réglages sous sa référence)",
                "",
                f"Les paramètres de `{name}` sont des clés de sa référence dans `tools`, à côté de `python: {name}`.",
                "",
            ]
            lines += model_sections(
                model,
                "tools[]",
                title,
                ref,
                label=f"référence `python: {name}`",
                level=3,
            )
        found = _arguments_schema(name)
        if found:
            description, schema = found
            lines += [f"## Les arguments que le modèle donne à {name}", ""]
            if description:
                lines += [
                    "Description de l'outil, telle que le modèle la reçoit :",
                    "",
                    "```text",
                    description,
                    "```",
                    "",
                ]
            lines += [*_schema_lines(schema), ""]
        pages.append(
            Page(
                f"{OUT}/tools/{name}.md",
                title,
                ("outil",),
                "\n".join(lines).strip() + "\n",
            )
        )
    return pages


# --- 4. La ligne de commande ------------------------------------------------------


def _subcommands(
    parser: argparse.ArgumentParser,
) -> list[tuple[str, argparse.ArgumentParser, str]]:
    """Les sous-commandes d'un analyseur : (nom, analyseur, aide)."""
    found: list[tuple[str, argparse.ArgumentParser, str]] = []
    for action in parser._actions:  # pyright: ignore[reportPrivateUsage]
        if isinstance(action, argparse._SubParsersAction):  # pyright: ignore[reportPrivateUsage]
            subparsers = cast("argparse._SubParsersAction[argparse.ArgumentParser]", action)  # pyright: ignore[reportPrivateUsage]
            helps = {a.dest: a.help or "" for a in subparsers._choices_actions}  # pyright: ignore[reportPrivateUsage]
            found += [(name, sub, helps.get(name, "")) for name, sub in subparsers.choices.items()]
    return found


def _options(parser: argparse.ArgumentParser) -> list[str]:
    """Les arguments et options d'un analyseur, en liste."""
    out: list[str] = []
    for action in parser._actions:  # pyright: ignore[reportPrivateUsage]
        if isinstance(action, argparse._SubParsersAction | argparse._HelpAction):  # pyright: ignore[reportPrivateUsage]
            continue
        names = ", ".join(action.option_strings) or action.dest
        facts: list[str] = []
        if action.choices is not None:
            facts.append("choix : " + ", ".join(str(c) for c in cast(Sequence[object], action.choices)))
        if action.option_strings and action.default not in (
            None,
            False,
            argparse.SUPPRESS,
        ):
            facts.append(f"défaut : {action.default}")
        if action.required and action.option_strings:
            facts.append("obligatoire")
        said = f" ({' ; '.join(facts)})" if facts else ""
        out.append(f"- `{names}`{said} : {action.help or '(sans aide)'}")
    return out


def _command_md(path: str, parser: argparse.ArgumentParser, help_text: str, level: int) -> list[str]:
    out = [f"{'#' * level} Commande `{path}`", ""]
    if help_text:
        out += [help_text[0].upper() + help_text[1:] + ".", ""]
    out += ["```text", parser.format_usage().strip(), "```", ""]
    handler = parser.get_default("handler")
    if handler is not None and getattr(handler, "__doc__", None):
        out += [docstring_markdown(cast(str, handler.__doc__)), ""]
    options = _options(parser)
    if options:
        out += [f"Arguments et options de `{path}` :", "", *options, ""]
    for name, sub, sub_help in _subcommands(parser):
        out += _command_md(f"{path} {name}", sub, sub_help, level + 1)
    return out


def cli_pages() -> list[Page]:
    """Une page pour les options globales de ``loom``, puis une par commande."""
    main = importlib.import_module("loom_ia.access.cli.main")
    parser = cast(argparse.ArgumentParser, main.build_parser())
    title = "Commande loom : options globales et liste des commandes"
    lines = [f"# {title}", "", "```text", parser.format_usage().strip(), "```", ""]
    if main.build_parser.__doc__:
        lines += [docstring_markdown(main.build_parser.__doc__), ""]
    lines += ["Options globales :", "", *_options(parser), "", "Commandes :", ""]
    commands = _subcommands(parser)
    lines += [f"- `loom {name}` : {help_text}" for name, _, help_text in commands]
    pages = [Page(f"{OUT}/cli/loom.md", title, ("cli",), "\n".join(lines) + "\n")]
    for name, sub, help_text in commands:
        page_title = f"Commande loom {name}"
        body = _command_md(f"loom {name}", sub, help_text, 1)
        body[0] = f"# {page_title}"
        pages.append(
            Page(
                f"{OUT}/cli/loom-{name}.md",
                page_title,
                ("cli",),
                "\n".join(body).strip() + "\n",
            )
        )
    return pages


# --- 5. L'API REST ----------------------------------------------------------------

_VERBS: frozenset[str] = frozenset({"get", "post", "put", "patch", "delete"})


def rest_pages(repo: Path) -> list[Page]:
    """Une page par module de ``access/http`` qui déclare des routes."""
    pages: list[Page] = []
    root = repo / "src" / "loom_ia" / "access" / "http"
    for path in sorted(root.rglob("*.py")):
        module = "loom_ia.access.http." + ".".join(path.relative_to(root).with_suffix("").parts)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        routes: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for deco in node.decorator_list:
                call = deco.func if isinstance(deco, ast.Call) else deco
                if not (isinstance(call, ast.Attribute) and call.attr in _VERBS and isinstance(call.value, ast.Name) and call.value.id in {"router", "app"}):
                    continue
                first = deco.args[0] if isinstance(deco, ast.Call) and deco.args else None
                route = first.value if isinstance(first, ast.Constant) and isinstance(first.value, str) else (ast.unparse(first) if first else "?")
                doc = ast.get_docstring(node, clean=True)
                routes += [
                    f"## `{call.attr.upper()} {route}`",
                    "",
                    f"Fonction `{node.name}` de `{module}`.",
                    "",
                    "```python",
                    _signature(node),
                    "```",
                    "",
                ]
                routes += [docstring_markdown(doc), ""] if doc else ["(sans docstring)", ""]
        if routes:
            title = f"API REST : les routes de {module}"
            text = "\n".join([f"# {title}", "", *routes]).strip() + "\n"
            pages.append(Page(f"{OUT}/rest/{module}.md", title, ("rest",), text))
    return pages


# --- Documents existants, écriture, JSONL -----------------------------------------


def existing_pages(repo: Path) -> list[Page]:
    """Les documents existants retenus, tels quels ; leur titre est leur premier titre de niveau 1."""
    pages: list[Page] = []
    for rel in EXISTING_DOCS:
        text = (repo / rel).read_text(encoding="utf-8")
        heading = next(
            (line[2:].strip() for line in text.splitlines() if line.startswith("# ")),
            Path(rel).stem,
        )
        pages.append(Page(rel, heading, ("doc",), text, generated=False))
    return pages


def write_pages(repo: Path, pages: Sequence[Page]) -> tuple[int, int]:
    """Écrit les pages générées dont le texte change, supprime celles qui ne sont plus générées ; rend (écrites, supprimées)."""
    out = repo / OUT
    wanted = {repo / page.path: page.text for page in pages if page.generated}
    written = 0
    for path, text in wanted.items():
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            written += 1
    removed = 0
    if out.exists():
        for stale in sorted(out.rglob("*.md")):
            if stale not in wanted:
                stale.unlink()
                removed += 1
    return written, removed


def commit_time(repo: Path) -> str:
    """La date du commit courant, en ISO 8601 UTC : la date d'ajout des documents, stable d'une génération à l'autre."""
    done = subprocess.run(
        ["git", "--no-optional-locks", "-C", str(repo), "log", "-1", "--format=%cI"],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        return datetime.fromisoformat(done.stdout.strip()).astimezone(UTC).isoformat()
    except ValueError:
        return datetime.now(UTC).isoformat()


_FENCE_LINE: re.Pattern[str] = re.compile(r"^\s*(```|~~~)")


def _sections(text: str, level: int) -> list[tuple[str | None, list[str]]]:
    """Le texte coupé à ses titres de ``level`` (hors blocs de code) : (titre, lignes sans le titre) ; le premier morceau n'a pas de titre."""
    marker = "#" * level + " "
    found: list[tuple[str | None, list[str]]] = [(None, [])]
    fenced = False
    for line in text.splitlines():
        if _FENCE_LINE.match(line):
            fenced = not fenced
        if not fenced and line.startswith(marker):
            found.append((line[len(marker) :].strip(), []))
        else:
            found[-1][1].append(line)
    return found


def anchor(heading: str) -> str:
    """L'ancre d'un titre, comme GitHub l'écrit : minuscules, sans ponctuation, espaces en tirets."""
    lowered = re.sub(r"[^\w\- ]", "", heading.lower(), flags=re.UNICODE)
    return lowered.strip().replace(" ", "-")


def split_page(page: Page) -> list[Page]:
    """Les documents d'une page dans le JSONL : elle-même, ou ses sections si elle dépasse ``SPLIT_OVER``.

    Chaque section devient un document dont le titre est « titre de la page >
    titre de la section » et dont le texte commence par ce titre en ``#`` :
    le chemin de titres de ses passages reste complet. Le texte d'avant le
    premier ``##`` garde le titre et le chemin de la page. Une section encore
    trop grosse est recoupée à ses ``###`` ; au-delà, elle reste entière.
    """
    if len(page.text) <= SPLIT_OVER:
        return [page]
    pieces: list[Page] = []
    used: dict[str, int] = {}

    def add(titles: Sequence[str], lines: Sequence[str]) -> None:
        body = "\n".join(lines).strip()
        if not body:
            return
        if not titles:
            pieces.append(Page(page.path, page.title, page.tags, body + "\n", page.generated))
            return
        key = "/".join(anchor(t) for t in titles)
        used[key] = used.get(key, 0) + 1
        path = f"{page.path}#{key}" + (f"-{used[key]}" if used[key] > 1 else "")
        title = " > ".join([page.title, *titles])
        pieces.append(Page(path, title, page.tags, f"# {title}\n\n{body}\n", page.generated))

    for heading, lines in _sections(page.text, 2):
        if heading is None:
            add([], lines)
            continue
        if len("\n".join(lines)) <= SPLIT_OVER:
            add([heading], lines)
            continue
        for sub, sub_lines in _sections("\n".join(lines), 3):
            add([heading] if sub is None else [heading, sub], sub_lines)
    return pieces


def write_jsonl(repo: Path, pages: Sequence[Page]) -> int:
    """Écrit le JSONL de ``loom memory import`` : un ``Document`` par page, ou par section d'une grosse page ; rend leur nombre."""
    from loom_ia.tools_builtin.memory.chunk import content_hash
    from loom_ia.tools_builtin.memory.records import Document

    added = commit_time(repo)
    lines: list[str] = []
    for page in (piece for whole in pages for piece in split_page(whole)):
        doc = Document(
            doc_id=str(uuid.uuid5(DOC_NAMESPACE, page.path)),
            title=page.title,
            project=PROJECT,
            tags=list(page.tags),
            source_kind="file",
            source=page.path,
            added_at=added,
            content_hash=content_hash(page.text),
            text=page.text,
        )
        lines.append(doc.model_dump_json())
    (repo / JSONL).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(lines)


def all_pages(repo: Path) -> list[Page]:
    """Toutes les pages : code, configuration (agent, amorce, loom.yaml), outils, CLI, REST, puis documents existants."""
    ref = Reference(seen={})
    loom_config = cast(type[BaseModel], importlib.import_module("loom_ia.config.models").LoomConfig)
    boot_config = cast(type[BaseModel], importlib.import_module("loom_ia.config.models").BootConfig)
    agent_spec = cast(type[BaseModel], importlib.import_module("loom_ia.agents.spec").AgentSpec)
    pages = code_pages(repo)
    pages += config_pages("agent.yaml", agent_spec, ref, "agent_yaml")
    pages += config_pages("loom-boot.yaml", boot_config, ref, "loom_boot_yaml")
    pages += config_pages("loom.yaml", loom_config, ref, "loom_yaml")
    pages += tool_pages(ref)
    pages += cli_pages()
    pages += rest_pages(repo)
    pages += existing_pages(repo)
    return pages


def main(argv: Sequence[str] | None = None) -> int:
    """Génère les pages et le JSONL ; affiche un bilan. Rend 0."""
    parser = argparse.ArgumentParser(description="Génère les pages Markdown du RAG de loom_v2 et le JSONL de loom memory import.")
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path.cwd(),
        help="racine du dépôt (défaut : dossier courant)",
    )
    args = parser.parse_args(argv)
    # L'aide d'argparse se plie à la largeur du terminal : une largeur fixe rend les pages identiques d'un poste à l'autre.
    os.environ["COLUMNS"] = "100"
    # Et elle se colore dans un terminal (Python 3.14) : les codes ANSI finiraient dans les pages.
    os.environ["PYTHON_COLORS"] = "0"
    os.environ["NO_COLOR"] = "1"
    repo = cast(Path, args.repo).resolve()
    pages = all_pages(repo)
    written, removed = write_pages(repo, pages)
    documents = write_jsonl(repo, pages)
    by_tag: dict[str, int] = {}
    for page in pages:
        by_tag[page.tags[0]] = by_tag.get(page.tags[0], 0) + 1
    counts = ", ".join(f"{tag} {n}" for tag, n in sorted(by_tag.items()))
    print(f"{len(pages)} pages ({counts}) ; {written} écrite(s), {removed} supprimée(s) ; {JSONL} : {documents} documents")
    return 0


if __name__ == "__main__":
    sys.exit(main())
