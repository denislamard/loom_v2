# loom_ia.core.model.base

Module `loom_ia.core.model.base` de loom_v2 (fichier `src/loom_ia/core/model/base.py`).

Base commune des types du domaine : `DomainModel` et l'exception `Unknown`.

## Classe `loom_ia.core.model.base.DomainModel`

```python
class DomainModel(BaseModel)
```

Modèle Pydantic immuable, qui refuse les champs inconnus (point 6).

Base de tous les types du domaine et de la config : une faute de frappe
dans une clé YAML est une erreur, jamais une option ignorée. Une copie
modifiée se fait par `model_copy(update=…)`.

## Classe `loom_ia.core.model.base.Unknown`

```python
class Unknown(KeyError)
```

Ce qu'on demande n'existe pas : une `KeyError` dont le message se lit tel quel.

`str(KeyError("…"))` rend le message entre guillemets (`repr`) ; ici,
`str` rend le texte, comme pour toute autre exception.

### Méthode `loom_ia.core.model.base.Unknown.__str__`

```python
def __str__(self) -> str
```

Le message tel quel, sans les guillemets d'une `KeyError`.
