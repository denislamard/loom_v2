# SPDX-License-Identifier: Apache-2.0
"""Clés d'API : fabrication, empreinte, vérification (#39, #50).

La config ne contient jamais la clé, seulement son empreinte. ``loom keys
create`` affiche la clé une fois et donne l'empreinte à coller.
"""

import hashlib
import hmac
import secrets
from typing import Final

# Préfixe identifiable, pour repérer une clé dans un journal ou un ticket.
PREFIX: Final = "lk_"
ALGORITHM: Final = "sha256"


def new_api_key() -> str:
    """Nouvelle clé, à n'afficher qu'une fois."""
    return PREFIX + secrets.token_urlsafe(32)


def fingerprint(key: str) -> str:
    """Empreinte à écrire dans la config (``sha256:…``)."""
    return f"{ALGORITHM}:{hashlib.sha256(key.encode()).hexdigest()}"


def matches(key: str, expected: str) -> bool:
    """Comparaison à temps constant entre une clé et une empreinte."""
    return hmac.compare_digest(fingerprint(key), expected)
