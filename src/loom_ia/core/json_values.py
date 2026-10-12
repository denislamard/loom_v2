# SPDX-License-Identifier: Apache-2.0
"""Comparer des valeurs JSON comme du JSON.

En Python, ``True == 1`` et ``0 == False`` : comparer par ``==`` des valeurs
venues du JSON confond un booléen et un nombre. ``same_json`` les sépare, et
garde ce que le JSON tient pour égal : ``2`` vaut ``2.0``. Sert au forge (le
résultat d'un exemple contre celui qu'attend son auteur) et aux contrôles d'une
éval (``fields``, arguments de ``called``).
"""

from typing import cast


def same_json(left: object, right: object) -> bool:
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
        return a.keys() == b.keys() and all(same_json(a[k], b[k]) for k in a)
    if isinstance(left, list) and isinstance(right, list):
        a, b = cast(list[object], left), cast(list[object], right)
        return len(a) == len(b) and all(same_json(x, y) for x, y in zip(a, b, strict=True))
    return left is None and right is None
