# loom_ia.core.events.schema

Module `loom_ia.core.events.schema` de loom_v2 (fichier `src/loom_ia/core/events/schema.py`).

JSON Schema des événements : contrat de l'interface de suivi (fonction K2).

Le schéma est **ouvert** : aucun objet n'y refuse une propriété qu'il ne
décrit pas. C'est la règle d'évolution du journal (`conception.md`) —
ajouter un champ optionnel ne change pas `schema_version`, et un lecteur
plus ancien ignore ce qu'il ne connaît pas, comme loom lui-même depuis 2.0.4.
Un schéma fermé (`additionalProperties: false`, ce que Pydantic tire de
modèles `extra="forbid"`) ferait refuser par un validateur les événements
d'une version plus récente de loom, à version de schéma égale (essai de la
fonction K2 du 21/10). L'écriture, elle, reste stricte : c'est Pydantic qui
la garde.

## Fonction `loom_ia.core.events.schema.event_json_schema`

```python
def event_json_schema() -> dict[str, Any]
```

Schéma JSON d'un événement écrit, tel qu'il est sérialisé, ouvert aux champs ajoutés.

**Renvoie :**

Le schéma de `Event` en mode sérialisation, sans aucun
`additionalProperties: false`.

## Fonction `loom_ia.core.events.schema._open`

```python
def _open(node: object) -> None
```

Retire, partout dans le schéma, le refus des propriétés non décrites.
