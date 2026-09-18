# SPDX-License-Identifier: Apache-2.0
"""Outils de l'agent demo, chargés par `imports` dans loom.yaml."""

from loom_ia.tools import tool


@tool
def calculer(expr: str) -> str:
    """Évalue une expression arithmétique simple (chiffres, + - * / et parenthèses)."""
    if not set(expr) <= set("0123456789+-*/(). "):
        raise ValueError("caractères non autorisés")
    return str(eval(expr, {"__builtins__": {}}))
