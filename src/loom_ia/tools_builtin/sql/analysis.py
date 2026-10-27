# SPDX-License-Identifier: Apache-2.0
"""Analyse d'une instruction, sans base : ce qu'elle est, les tables qu'elle touche, et comment.

C'est elle qui décide de l'approbation d'un appel (``assess_call`` ne parle à
aucune base) et qui donne au modèle des erreurs qu'il sait corriger. Elle ne
fait pas foi : les droits de la base (rôle Postgres, authorizer SQLite) sont
la barrière ; l'analyse passe devant, et refuse plus tôt et plus clairement.

Règles :

- une instruction par entrée ; ``query`` n'admet qu'une lecture (``exp.Query``,
  sans ``INTO`` ni ``FOR UPDATE``, sans écriture imbriquée), ``execute`` un
  ``INSERT``, ``UPDATE`` ou ``DELETE`` à la racine, sans autre écriture
  imbriquée (pas de ``WITH … DELETE``) ; tout le reste est refusé ;
- toute table citée est déclarée, sans schéma ni base devant ; la cible d'une
  écriture demande le droit de son opération (``ON CONFLICT DO UPDATE`` :
  ``update`` en plus), ``RETURNING``, ``WHERE`` et toute autre table citée
  demandent ``select`` ; une CTE n'est pas une table ;
- ``UPDATE`` et ``DELETE`` sans ``WHERE`` sont refusés, sauf ``allow_full_table`` ;
- une fonction que sqlglot ne connaît pas (``exp.Anonymous``) est refusée, sauf
  si la base la permet (``functions``) ; celles de ``FORBIDDEN`` le sont
  toujours — toutes celles qui agissent hors de la requête y sont inconnues
  de sqlglot (essai du 26/10 sur 30.24), et un test le revérifie ;
- les paramètres s'écrivent ``$1``, ``$2``… et chacun de ``params`` sert.
"""

import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from itertools import pairwise
from typing import Final, Literal

import sqlglot
from sqlglot import TokenType, exp
from sqlglot.errors import ParseError, TokenError

from loom_ia.core.ports import ToolError
from loom_ia.tools_builtin.sql_params import DatabaseSpec, RightApproval, SqlRight

type Dialect = Literal["postgres", "sqlite"]
type Operation = Literal["select", "insert", "update", "delete"]

# Fonctions refusées même si une base les permet : elles agissent hors de la requête
# (process, verrous, séquences, fichiers, réseau, SQL dynamique).
FORBIDDEN: Final = frozenset(
    {
        # Postgres
        "pg_sleep",
        "pg_sleep_for",
        "pg_sleep_until",
        "pg_terminate_backend",
        "pg_cancel_backend",
        "pg_reload_conf",
        "pg_rotate_logfile",
        "pg_promote",
        "pg_switch_wal",
        "pg_create_restore_point",
        "pg_notify",
        "pg_logical_emit_message",
        "set_config",
        "nextval",
        "setval",
        "pg_read_file",
        "pg_read_binary_file",
        "pg_ls_dir",
        "pg_stat_file",
        "query_to_xml",
        "query_to_xml_and_xmlschema",
        "cursor_to_xml",
        "table_to_xml",
        "table_to_xml_and_xmlschema",
        "schema_to_xml",
        "database_to_xml",
        # SQLite
        "load_extension",
        "readfile",
        "writefile",
        "edit",
        "fts3_tokenizer",
    }
)
FORBIDDEN_PREFIXES: Final = ("pg_advisory", "pg_try_advisory", "lo_", "dblink")

# Ce que l'on dit d'une instruction refusée, quand le nom du nœud n'est pas parlant.
_SAID: Final[Mapping[str, str]] = {
    "truncatetable": "TRUNCATE",
    "transaction": "BEGIN",
    "altertable": "ALTER TABLE",
}

_FALLBACK: Final = "Falling back to parsing as a 'Command'"
_SPACED: Final = re.compile(r"\$(\d+)(?=\S)")


class _QuietFallback(logging.Filter):
    """sqlglot dit en avertissement chaque instruction qu'il ne sait pas lire, SQL compris.

    Ce SQL vient du modèle, et l'outil le refuse de toute façon : il n'a rien à
    faire dans les journaux de l'application.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        return _FALLBACK not in record.getMessage()


logging.getLogger("sqlglot").addFilter(_QuietFallback())


@dataclass(frozen=True, slots=True)
class Catalog:
    """Les tables d'une base, sous le nom qui les compare, avec leurs droits."""

    dialect: Dialect
    # Nom déclaré → droits.
    rights: Mapping[str, Mapping[SqlRight, RightApproval]]
    functions: frozenset[str]
    allow_full_table: bool

    @classmethod
    def of(cls, spec: DatabaseSpec) -> Catalog:
        return cls(
            dialect=spec.dialect,
            rights={table: dict(found.rights) for table, found in spec.tables.items()},
            functions=frozenset(name.lower() for name in spec.functions),
            allow_full_table=spec.allow_full_table,
        )

    def declared(self, name: str, *, quoted: bool) -> str | None:
        """Le nom déclaré qui désigne cet identifiant, ou ``None``.

        Postgres replie en minuscules un nom sans guillemets, et compare tel quel
        un nom entre guillemets ; SQLite ne distingue pas la casse.
        """
        if self.dialect == "sqlite":
            folded = name.lower()
            return next((t for t in self.rights if t.lower() == folded), None)
        wanted = name if quoted else name.lower()
        return wanted if wanted in self.rights else None

    def granted(self, table: str) -> str:
        return ", ".join(self.rights[table])


@dataclass(frozen=True, slots=True)
class Analysis:
    """Une instruction jugée : son opération, et les droits qu'elle exerce, table par table."""

    operation: Operation
    needs: Mapping[str, frozenset[SqlRight]]
    # Les noms des CTE : SQLite les lit comme des tables sans base (authorizer).
    ctes: frozenset[str]
    target: str | None = None

    def exercised(self) -> Iterable[tuple[str, SqlRight]]:
        for table, rights in self.needs.items():
            for right in ("select", "insert", "update", "delete"):
                if right in rights:
                    yield table, right


def analyze(
    sql: str, params: int, catalog: Catalog, *, write: bool, position: str = ""
) -> Analysis:
    """L'instruction jugée, ou ``ToolError`` qui dit au modèle quoi corriger.

    ``write`` : l'instruction vient d'``execute``. ``position`` (« instruction 2 : »)
    préfixe les messages d'un ``execute`` à plusieurs instructions.
    """
    try:
        return _analyze(sql, params, catalog, write=write)
    except ToolError as exc:
        raise ToolError(f"{position}{exc.message}") from None


def _analyze(sql: str, params: int, catalog: Catalog, *, write: bool) -> Analysis:
    # sqlglot 30.24 ne sait pas lire « $1,$2 » sans espace en dialecte Postgres (« Error
    # tokenizing ») : la copie analysée en met une après chaque paramètre. Le SQL envoyé
    # à la base reste celui du modèle ; un « $n » ne peut pas ouvrir une chaîne
    # dollar-quotée (une étiquette ne commence pas par un chiffre).
    sql = _SPACED.sub(r"$\1 ", sql)
    try:
        parsed = [node for node in sqlglot.parse(sql, read=catalog.dialect) if node is not None]
    except (ParseError, TokenError) as exc:
        first = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
        raise ToolError(f"SQL illisible : {first}") from None
    if len(parsed) != 1:
        raise ToolError(f"Une seule instruction par entrée ({len(parsed)} données).")
    root = parsed[0]
    if root.find(exp.Placeholder) is not None:
        raise ToolError("Paramètres à écrire $1, $2… (ni « ? » ni « :nom »).")
    operation = _operation(root, write=write)
    ctes = frozenset(_canonical(cte.args["alias"].this, catalog) for cte in root.find_all(exp.CTE))
    needs: dict[str, set[SqlRight]] = {}
    target_node, target = _target(root, catalog, operation)
    if target is not None:
        needs.setdefault(target, set()).update(_target_rights(root, operation))
    for table in root.find_all(exp.Table):
        if table is target_node or not isinstance(table.this, exp.Identifier):
            # La cible, déjà jugée ; ou une fonction-table (generate_series…).
            continue
        if table.args.get("db") is not None or table.args.get("catalog") is not None:
            raise ToolError(
                f"Nom qualifié « {table.sql(dialect=catalog.dialect)} » interdit : "
                f"écrire « {table.name} »."
            )
        name = _canonical(table.this, catalog)
        if name in ctes:
            continue
        declared = catalog.declared(table.name, quoted=bool(table.this.args.get("quoted")))
        if declared is None:
            raise ToolError(_unknown_table(table.name, catalog))
        needs.setdefault(declared, set()).add("select")
    _check_functions(root, catalog)
    _check_parameters(sql, params)
    for table, rights in needs.items():
        missing = [r for r in ("select", "insert", "update", "delete") if r in rights]
        missing = [r for r in missing if r not in catalog.rights[table]]
        if missing:
            raise ToolError(
                f"{'/'.join(m.upper() for m in missing)} non autorisé sur « {table} » "
                f"(droits : {catalog.granted(table)})."
            )
    return Analysis(
        operation=operation,
        needs={table: frozenset(rights) for table, rights in needs.items()},
        ctes=ctes,
        target=target,
    )


def _operation(root: exp.Expr, *, write: bool) -> Operation:
    nested = [
        node
        for node in root.find_all(exp.Insert, exp.Update, exp.Delete, exp.Merge)
        if node is not root
    ]
    if isinstance(root, exp.Query):
        if write:
            raise ToolError(
                "execute écrit : INSERT, UPDATE ou DELETE ; une lecture passe par query."
            )
        if nested:
            raise ToolError("query ne fait que lire : une écriture passe par execute.")
        if root.find(exp.Into) is not None:
            raise ToolError("SELECT … INTO crée une table : interdit.")
        if root.find(exp.Lock) is not None:
            raise ToolError("FOR UPDATE / FOR SHARE verrouille des lignes : interdit.")
        return "select"
    if isinstance(root, exp.Insert | exp.Update | exp.Delete):
        if not write:
            raise ToolError(
                f"{root.key.upper()} écrit : passer par execute ; query ne fait que lire."
            )
        if nested:
            raise ToolError(
                "Une instruction n'écrit que dans sa table cible (pas de WITH … "
                "INSERT/UPDATE/DELETE) : en faire deux instructions d'execute."
            )
        if root.find(exp.Lock) is not None:
            raise ToolError("FOR UPDATE / FOR SHARE verrouille des lignes : interdit.")
        if isinstance(root, exp.Insert):
            return "insert"
        return "update" if isinstance(root, exp.Update) else "delete"
    if isinstance(root, exp.Command):
        said = str(root.this).upper()
    else:
        said = _SAID.get(root.key, root.key.upper())
    allowed = "INSERT, UPDATE, DELETE (execute)" if write else "SELECT (query)"
    raise ToolError(f"Instruction {said} interdite. Autorisé : {allowed}.")


def _target(
    root: exp.Expr, catalog: Catalog, operation: Operation
) -> tuple[exp.Table | None, str | None]:
    if operation == "select":
        return None, None
    node = root.this
    if isinstance(node, exp.Schema):
        node = node.this
    if not isinstance(node, exp.Table) or not isinstance(node.this, exp.Identifier):
        raise ToolError(f"{operation.upper()} : table cible illisible.")
    if node.args.get("db") is not None or node.args.get("catalog") is not None:
        raise ToolError(
            f"Nom qualifié « {node.sql(dialect=catalog.dialect)} » interdit : "
            f"écrire « {node.name} »."
        )
    declared = catalog.declared(node.name, quoted=bool(node.this.args.get("quoted")))
    if declared is None:
        raise ToolError(_unknown_table(node.name, catalog))
    if operation in ("update", "delete") and root.args.get("where") is None:
        if not catalog.allow_full_table:
            raise ToolError(
                f"{operation.upper()} sans WHERE refusé : il toucherait toute la table "
                f"« {declared} » ; ajouter une condition."
            )
    return node, declared


def _target_rights(root: exp.Expr, operation: Operation) -> set[SqlRight]:
    rights: set[SqlRight] = {operation}
    if operation in ("update", "delete") or root.args.get("returning") is not None:
        # La base lit les colonnes du WHERE, et rend celles de RETURNING.
        rights.add("select")
    conflict = root.args.get("conflict")
    if isinstance(conflict, exp.OnConflict):
        action = conflict.args.get("action")
        if conflict.args.get("expressions") or "UPDATE" in str(action or "").upper():
            rights.update(("update", "select"))
        elif conflict.args.get("conflict_keys"):
            rights.add("select")
    return rights


def _check_functions(root: exp.Expr, catalog: Catalog) -> None:
    for dot in root.find_all(exp.Dot):
        if isinstance(dot.expression, exp.Anonymous):
            raise ToolError(
                f"Fonction qualifiée « {dot.sql(dialect=catalog.dialect)} » interdite : "
                "l'appeler sans schéma."
            )
    for function in root.find_all(exp.Anonymous):
        name = str(function.name).lower()
        if is_forbidden(name):
            raise ToolError(f"Fonction {name} interdite : elle agit hors de la requête.")
        if name not in catalog.functions:
            raise ToolError(
                f"Fonction {name} non permise : inconnue de l'analyse. Si elle ne modifie "
                "rien, la base peut la permettre (functions) ; sinon s'en passer."
            )


def is_forbidden(name: str) -> bool:
    lowered = name.lower()
    return lowered in FORBIDDEN or lowered.startswith(FORBIDDEN_PREFIXES)


def _check_parameters(sql: str, given: int) -> None:
    """``$1…$n`` : chaque numéro cité a sa valeur, et chaque valeur sert."""
    try:
        tokens = sqlglot.tokenize(sql, read="postgres")
    except TokenError as exc:
        first = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
        raise ToolError(f"SQL illisible : {first}") from None
    used: set[int] = set()
    for current, following in pairwise(tokens):
        if current.token_type == TokenType.PARAMETER and following.token_type == TokenType.NUMBER:
            if following.text.isdigit():
                used.add(int(following.text))
    beyond = sorted(n for n in used if n < 1 or n > given)
    if beyond:
        cited = ", ".join(f"${n}" for n in beyond)
        raise ToolError(f"{cited} cité, mais {given} paramètre(s) donné(s) dans params.")
    unused = [n for n in range(1, given + 1) if n not in used]
    if unused:
        cited = ", ".join(f"${n}" for n in unused)
        raise ToolError(f"Paramètre {cited} donné mais jamais cité dans le SQL.")


def _canonical(identifier: object, catalog: Catalog) -> str:
    if not isinstance(identifier, exp.Identifier):
        return str(identifier).lower()
    name = str(identifier.this)
    if catalog.dialect == "sqlite" or not identifier.args.get("quoted"):
        return name.lower()
    return name


def _unknown_table(name: str, catalog: Catalog) -> str:
    return f"Table « {name} » non autorisée. Tables de la base : {', '.join(catalog.rights)}."
