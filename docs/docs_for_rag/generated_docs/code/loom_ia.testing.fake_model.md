# loom_ia.testing.fake_model

Module `loom_ia.testing.fake_model` de loom_v2 (fichier `src/loom_ia/testing/fake_model.py`).

Faux modèle scripté, pour les tests et les exemples (fonction O2).

Chaque appel consomme la réponse suivante du script et la découpe en
morceaux, comme le ferait un vrai fournisseur. Un élément du script peut
être un message, une exception à lever, ou une fonction qui reçoit la
requête et renvoie l'un des deux.

    model = ScriptedModel(
        tool_call_message(("c1", "calculer", {"expr": "12*7+3"})),
        Message.assistant("87"),
    )

## Constantes de `loom_ia.testing.fake_model`

- `Reply = Message | Exception` : Une réponse du script : un message, ou une exception à lever.
- `ScriptItem = Reply | Callable[[ModelRequest], Reply]` : Un élément du script : une réponse, ou une fonction qui la tire de la requête.
- `DEFAULT_USAGE = Usage(input_tokens=100, output_tokens=20)` : Usage annoncé par défaut à chaque réponse : 100 tokens en entrée, 20 en sortie.

## Classe `loom_ia.testing.fake_model.ScriptExhausted`

```python
class ScriptExhausted(AssertionError)
```

Le modèle a été appelé plus de fois que prévu par le script.

## Classe `loom_ia.testing.fake_model.ScriptedModel`

```python
class ScriptedModel
```

Implémente `ModelClient` en rejouant un script.

Les requêtes reçues sont gardées dans `requests` ; l'usage annoncé est
le même pour chaque réponse.

**Arguments :**

- `*script` : Réponses, dans l'ordre : message, exception à lever, ou fonction `(requête) -> réponse`.
- `provider` : Nom du fournisseur annoncé (`fake`).
- `usage` : Usage annoncé à chaque réponse.
- `fragment_size` : Taille des morceaux de texte diffusés.
- `delay` : Pause avant chaque morceau, en secondes (latence simulée).

**Attributs :**

- `usage` : Usage annoncé à chaque réponse.
- `fragment_size` : Taille des morceaux.
- `delay` : Pause avant chaque morceau.
- `requests` : Les requêtes reçues, dans l'ordre.

### Méthode `loom_ia.testing.fake_model.ScriptedModel.__init__`

```python
def __init__(self, *script: ScriptItem, provider: str='fake', usage: Usage=DEFAULT_USAGE, fragment_size: int=8, delay: float=0.0) -> None
```

Garde le script ; les arguments sont décrits dans la docstring de la classe.

### Méthode `loom_ia.testing.fake_model.ScriptedModel.provider`

```python
@property
def provider(self) -> str
```

Nom du fournisseur annoncé.

### Méthode `loom_ia.testing.fake_model.ScriptedModel.remaining`

```python
@property
def remaining(self) -> int
```

Réponses du script pas encore consommées.

### Méthode `loom_ia.testing.fake_model.ScriptedModel.add`

```python
def add(self, *script: ScriptItem) -> None
```

Ajoute des réponses à la fin du script.

### Méthode `loom_ia.testing.fake_model.ScriptedModel.aclose`

```python
async def aclose(self) -> None
```

Ne ferme rien.

### Méthode `loom_ia.testing.fake_model.ScriptedModel.stream`

```python
async def stream(self, request: ModelRequest) -> AsyncGenerator[ModelChunk]
```

Consomme la réponse suivante du script et la diffuse en morceaux.

**Arguments :**

- `request` : La requête, gardée dans `requests`.

**Produit :**

Les morceaux de la réponse, avec l'usage.

**Lève :**

- `ScriptExhausted` : Le script est épuisé.
- `Exception` : L'exception que le script prévoit à cet appel.
