# SPDX-License-Identifier: Apache-2.0
"""Serveur MCP « math » de l'exemple : calcul d'une expression arithmétique.

L'expression est analysée (``ast``), jamais exécutée : seuls les nombres, les
parenthèses et les opérateurs + - * / // % ** sont acceptés.
"""

import ast
from collections.abc import Callable

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

OPERATIONS: dict[type[ast.operator], Callable[[float, float], float]] = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.FloorDiv: lambda a, b: a // b,
    ast.Mod: lambda a, b: a % b,
    ast.Pow: lambda a, b: a**b,
}

mcp = FastMCP("math", log_level="WARNING")


def evaluer(noeud: ast.expr) -> float:
    match noeud:
        case ast.Constant(value=int() | float() as valeur) if not isinstance(valeur, bool):
            return valeur
        case ast.UnaryOp(op=ast.USub(), operand=operande):
            return -evaluer(operande)
        case ast.BinOp(left=gauche, op=op, right=droite) if type(op) in OPERATIONS:
            return OPERATIONS[type(op)](evaluer(gauche), evaluer(droite))
        case _:
            raise ValueError(f"élément non autorisé : {ast.unparse(noeud)!r}")


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def calculer(expression: str) -> str:
    """Calcule une expression arithmétique (nombres, parenthèses, + - * / // % **)."""
    resultat = evaluer(ast.parse(expression, mode="eval").body)
    return str(int(resultat)) if float(resultat).is_integer() else str(resultat)


if __name__ == "__main__":
    mcp.run()
