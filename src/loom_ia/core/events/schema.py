# SPDX-License-Identifier: Apache-2.0
"""JSON Schema des événements : contrat de l'interface de suivi (K2).

Le schéma est **ouvert** : aucun objet n'y refuse une propriété qu'il ne
décrit pas. C'est la règle d'évolution du journal (``conception.md``) —
ajouter un champ optionnel ne change pas ``schema_version``, et un lecteur
plus ancien ignore ce qu'il ne connaît pas, comme loom lui-même depuis 2.0.4.
Un schéma fermé (``additionalProperties: false``, ce que Pydantic tire de
modèles ``extra="forbid"``) ferait refuser par un validateur les événements
d'une version plus récente de loom, à version de schéma égale (test K2 du
21/10). L'écriture, elle, reste stricte : c'est Pydantic qui la garde.
"""

from typing import Any, cast

from pydantic import TypeAdapter

from loom_ia.core.events.envelope import Event


def event_json_schema() -> dict[str, Any]:
    """Schéma JSON d'un événement écrit, tel qu'il est sérialisé, ouvert aux champs ajoutés."""
    schema = TypeAdapter(Event).json_schema(mode="serialization")
    _open(schema)
    return schema


def _open(node: object) -> None:
    """Retire, partout dans le schéma, le refus des propriétés non décrites."""
    if isinstance(node, dict):
        table = cast("dict[str, Any]", node)
        if table.get("additionalProperties") is False:
            del table["additionalProperties"]
        for value in table.values():
            _open(value)
    elif isinstance(node, list):
        for item in cast("list[object]", node):
            _open(item)
