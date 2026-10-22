# SPDX-License-Identifier: Apache-2.0
"""Lecture YAML de loom : ``safe_load``, sans clé en double (M1).

``yaml.safe_load`` garde la **dernière** valeur d'une clé répétée, sans rien
dire. Une faute de copier-coller annulait ainsi un réglage en silence : deux
``retry`` (17/10), deux ``capture`` dont la seconde faisait partir du contenu
vers un collecteur (test K4 du 21/10), deux ``max_iterations`` dans un agent
(test M1 du 22/10). Une clé répétée dans un même objet est donc refusée, en
nommant la clé et ses deux lignes.

Une fusion YAML (``<<: *ancre``) reste permise : une clé écrite à côté d'une
fusion la surcharge, c'est l'usage même de la fusion, et ce n'est pas un
doublon. Deux clés **écrites** identiques dans le même objet en sont un.
"""

from typing import Any, Final

import yaml
from yaml.nodes import MappingNode

# Étiquette de la clé de fusion (``<<``).
MERGE_TAG: Final = "tag:yaml.org,2002:merge"


class DuplicateKey(yaml.YAMLError):
    """Une clé écrite deux fois dans le même objet."""

    def __init__(self, key: object, first: int, second: int) -> None:
        super().__init__(
            f"clé {key!r} donnée deux fois dans le même objet (lignes {first} et {second}) : "
            "la seconde écraserait la première en silence"
        )
        self.key = key
        self.first = first
        self.second = second


class _UniqueKeyLoader(yaml.SafeLoader):
    """``SafeLoader`` qui refuse une clé écrite deux fois dans un objet."""

    def construct_mapping(self, node: MappingNode, deep: bool = False) -> dict[Any, Any]:
        seen: dict[object, int] = {}
        for key_node, _ in node.value:
            if key_node.tag == MERGE_TAG:
                continue
            key: object = self.construct_object(key_node, deep=deep)  # pyright: ignore[reportUnknownMemberType]
            try:
                hash(key)
            except TypeError:
                # Clé non hachable : la construction d'origine dira l'erreur.
                continue
            line = key_node.start_mark.line + 1
            if key in seen:
                raise DuplicateKey(key, seen[key], line)
            seen[key] = line
        return super().construct_mapping(node, deep=deep)


def load_yaml(content: str) -> object:
    """Le document YAML, comme ``yaml.safe_load`` ; ``DuplicateKey`` pour une clé en double."""
    return yaml.load(content, Loader=_UniqueKeyLoader)
