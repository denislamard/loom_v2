# loom_ia.core.json_values

Module `loom_ia.core.json_values` de loom_v2 (fichier `src/loom_ia/core/json_values.py`).

Comparer des valeurs JSON comme du JSON.

En Python, `True == 1` et `0 == False` : comparer par `==` des valeurs
venues du JSON confond un booléen et un nombre. `same_json` les sépare, et
garde ce que le JSON tient pour égal : `2` vaut `2.0`.

Utilisé par la forge (`adapters/firecracker/forge.py` : le résultat d'un
exemple contre celui qu'attend son auteur) et par les contrôles d'une éval
(`fields`, arguments de `called`).

## Fonction `loom_ia.core.json_values.same_json`

```python
def same_json(left: object, right: object) -> bool
```

Égalité de deux valeurs JSON : un booléen n'est pas un nombre, 2 vaut 2.0.

La comparaison descend dans les objets (mêmes clés, valeurs égales) et les
listes (même longueur, éléments égaux dans l'ordre). Une valeur qui n'est
pas du JSON (un tuple, un objet Python) n'est égale à rien, sauf `None` à
`None`.

**Arguments :**

- `left` : Première valeur, telle que la donne `json.loads`.
- `right` : Seconde valeur.

**Renvoie :**

Vrai si les deux valeurs sont égales au sens du JSON.
