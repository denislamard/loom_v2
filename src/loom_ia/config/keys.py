# SPDX-License-Identifier: Apache-2.0
"""Clés d'API : fabrication, empreinte, vérification (points 39 et 50).

La config ne contient jamais la clé, seulement son empreinte. ``loom keys
create`` affiche la clé une fois et donne l'empreinte à coller.
"""

import hashlib
import hmac
import re
import secrets
from typing import Final

PREFIX: Final = "lk_"
"""Préfixe identifiable, pour repérer une clé dans un journal ou un ticket."""
ALGORITHM: Final = "sha256"
FINGERPRINT: Final = re.compile(rf"{ALGORITHM}:[0-9a-f]{{64}}")
"""La forme d'une empreinte : l'algorithme, puis le condensé en hexadécimal minuscule."""


def new_api_key() -> str:
    """Nouvelle clé, à n'afficher qu'une fois.

    Returns:
        ``lk_`` suivi de 43 caractères aléatoires (32 octets en base64 URL).
    """
    return PREFIX + secrets.token_urlsafe(32)


def fingerprint(key: str) -> str:
    """Empreinte à écrire dans la config (``sha256:…``).

    Args:
        key: La clé en clair.
    """
    return f"{ALGORITHM}:{hashlib.sha256(key.encode()).hexdigest()}"


def matches(key: str, expected: str) -> bool:
    """Comparaison à temps constant entre une clé et une empreinte.

    Elle porte sur des octets : ``compare_digest`` refuse deux ``str`` dès que
    l'une n'est pas ASCII, et une empreinte mal formée ferait alors un 500 au
    lieu d'un 401. La config ne laisse passer que ``FINGERPRINT``, mais cette
    comparaison ne compte pas là-dessus.

    Args:
        key: La clé présentée, en clair.
        expected: L'empreinte déclarée.

    Returns:
        Vrai si l'empreinte de la clé est celle attendue.
    """
    return hmac.compare_digest(fingerprint(key).encode(), expected.encode())
