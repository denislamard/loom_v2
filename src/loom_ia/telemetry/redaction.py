# SPDX-License-Identifier: Apache-2.0
"""Masquage par motifs du contenu exporté (K3, #30, §14.2).

Deux masquages coexistent, et ne se confondent pas :

- celui des **champs** (``core.events.redaction``, 5.2a) retire tout ce
  qu'une charge déclare comme contenu : c'est ce qui part quand la capture
  est ``metadata``, et ce qu'une clé sans ``read_content`` ne lit pas ;
- celui-ci, par **motifs**, remplace dans le contenu qui sort ce qui ressemble
  à un secret ou à une donnée personnelle (clé d'API, e-mail, téléphone, IBAN,
  ou un motif à soi) par son nom entre crochets. Il ne sert qu'aux exports en
  ``content``.

Un motif est une heuristique : il masque ce qui lui ressemble, il ne garantit
pas que rien d'autre ne passe. C'est la capture ``metadata`` qui garantit
qu'aucun contenu ne sort.

Les motifs s'exécutent avec un délai (``core.bounded_regex``). Un motif qui ne
finit pas dans le délai n'a pas dit ce qu'il fallait masquer : le masquage
échoue **en fermé**, et le texte entier est remplacé par une marque.
"""

from collections.abc import Iterable, Mapping
from typing import Final, cast

from pydantic import JsonValue

from loom_ia.core.bounded_regex import RegexTimeout, compile_pattern, substitute

# Motifs fournis, dans l'ordre où ils s'appliquent par défaut : les secrets
# d'abord, que le téléphone ou l'IBAN mordraient pour en laisser filer le reste.
BUILTIN_PATTERNS: Final = ("secret", "email", "phone", "iban")

# Un motif fourni peut tenir en plusieurs expressions, appliquées l'une après
# l'autre sous le même nom : réunies en une seule alternative, celles du
# secret coûtaient six fois plus (le moteur ne cherche plus leurs préfixes).
_BUILTIN: Final[Mapping[str, tuple[str, ...]]] = {
    # Clés et jetons de forme connue, masqués en entier. Le « Bearer » passe
    # avant le JWT qu'il porte souvent, pour partir avec lui.
    "secret": (
        # Clé privée PEM, jusqu'à sa fin, ou celle du texte.
        r"-----BEGIN[A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END[A-Z ]*PRIVATE KEY-----|\Z)",
        # Identifiants d'une URL : l'hôte reste lisible.
        r"(?<=://)[^\s:/?#@]+:[^\s/?#@]+(?=@)",
        # En-tête d'autorisation.
        r"(?i:\bbearer\s+)[A-Za-z0-9._~+/-]{8,}=*",
        # Clés d'API : OpenAI, Anthropic (``sk-``), Stripe, ElevenLabs (``sk_``, ``rk_``)…
        r"\b(?:sk|rk)[-_][A-Za-z0-9_-]{20,}",
        # GitHub, AWS, Slack, Google.
        r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})",
        r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b",
        r"\bxox[abposr]-[A-Za-z0-9-]{10,}",
        r"\bAIza[A-Za-z0-9_-]{35}",
        # JWT : en-tête et charge commencent par « {" » en base64.
        r"\beyJ[A-Za-z0-9_-]{5,}\.eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*",
        # La valeur d'un « password=… », d'un « api_key: … », d'un « mot de passe : … ».
        r"(?i:\b(?:password|passwd|mot[ _-]de[ _-]passe|api[_-]?key|access[_-]?token"
        r"|auth[_-]?token|client[_-]?secret|secret[_-]?key)\b)"
        r"[\"']?\s*[:=]\s*[\"']?[^\s\"',;]{4,}",
    ),
    # Une adresse ne commence qu'au début d'un mot (le lookbehind), ou là où la
    # précédente s'arrête (\G : « a@b.fr+c@d.fr ») : sans cela, chaque lettre d'un
    # long mot sans « @ » relançait la lecture jusqu'à sa fin (quadratique).
    "email": (r"(?:(?<![\w.+-])|\G)[\w.+-]+@[\w-]+(?:\.[\w-]+)+",),
    # Numéros français (0X, +33 X ou 0033 X, avec ou sans « (0) », puis quatre
    # paires) et internationaux (+indicatif puis groupes) : un montant ou une
    # référence de devis n'en a ni le préfixe ni la forme. Un indicatif en
    # « 00 » autre que 0033 n'est pas reconnu : il prendrait les références
    # à zéros de tête.
    "phone": (
        r"(?<![\w+])(?:(?:(?:\+|00)33[\s.-]?(?:\(0\)[\s.-]?)?|0)[1-9](?:[\s.-]?\d{2}){4}"
        r"|\+(?!33)\d{1,3}(?:[\s.-]?\d{2,4}){2,5})(?!\d)",
    ),
    # Deux lettres, deux chiffres de contrôle, puis 11 à 30 caractères, en
    # groupes de quatre séparés ou non par une espace ; en toute casse.
    "iban": (r"(?i)\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,7}(?:[ ]?[A-Z0-9]{1,3})?\b",),
}


class Redactor:
    """Remplace ce que ses motifs reconnaissent par ``[nom]``."""

    def __init__(self, patterns: Iterable[tuple[str, str]]) -> None:
        self._patterns = tuple((name, compile_pattern(regex)) for name, regex in patterns)

    @classmethod
    def of(cls, declared: Iterable[str | tuple[str, str]]) -> Redactor:
        """Motifs déclarés : un nom de motif fourni, ou ``(nom, expression)``."""
        resolved: list[tuple[str, str]] = []
        for item in declared:
            if isinstance(item, str):
                resolved.extend((item, expression) for expression in _BUILTIN[item])
            else:
                resolved.append(item)
        return cls(resolved)

    @property
    def names(self) -> tuple[str, ...]:
        """Les noms des motifs, dans l'ordre où ils s'appliquent, chacun une fois."""
        return tuple(dict.fromkeys(name for name, _ in self._patterns))

    def text(self, value: str) -> str:
        for name, pattern in self._patterns:
            try:
                value = substitute(pattern, f"[{name}]", value)
            except RegexTimeout:
                # En fermé : rien du texte ne sort.
                return f"[{name} : délai dépassé, texte masqué]"
        return value

    def json(self, value: JsonValue) -> JsonValue:
        """Masque chaque chaîne d'une valeur JSON, clés comprises.

        Deux clés qui se masquent pareil ne s'écrasent pas : la deuxième devient
        ``[email]#2``, la troisième ``[email]#3`` — aucune entrée ne se perd.
        """
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list):
            return [self.json(item) for item in cast(list[JsonValue], value)]
        if isinstance(value, dict):
            masked: dict[str, JsonValue] = {}
            for key, item in cast(dict[str, JsonValue], value).items():
                name = unique = self.text(key)
                rank = 2
                while unique in masked:
                    unique, rank = f"{name}#{rank}", rank + 1
                masked[unique] = self.json(item)
            return masked
        return value
