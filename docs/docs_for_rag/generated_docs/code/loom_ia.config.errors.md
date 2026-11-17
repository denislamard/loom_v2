# loom_ia.config.errors

Module `loom_ia.config.errors` de loom_v2 (fichier `src/loom_ia/config/errors.py`).

Erreurs de configuration, lisibles au démarrage (fonction M1).

Le message nomme le fichier et l'endroit dans le YAML :
`agents/demo.yaml: main.model — modèle 'X' non déclaré`. Quand la
validation trouve plusieurs erreurs, elles viennent une par ligne, et seule la
première porte le nom du fichier.

## Classe `loom_ia.config.errors.ConfigError`

```python
class ConfigError(Exception)
```

Configuration inutilisable : le run ne démarre pas.

**Arguments :**

- `message` : Ce qui ne va pas, et où dans le YAML.
- `source` : Fichier en cause ; il préfixe le message.

**Attributs :**

- `reason` : Le message sans le fichier.
- `source` : Le fichier, ou `None`.

## Fonction `loom_ia.config.errors.from_validation`

```python
def from_validation(error: ValidationError, *, source: Path | None=None, root: str | None='(racine)', data: object=None) -> ConfigError
```

Traduit les erreurs Pydantic en une erreur lisible.

`root` nomme l'emplacement d'une erreur sans chemin (un contrôle de tout
l'objet) ; `None` la laisse sans préfixe — pour un agent rapporté à son
fichier, dont le message nomme déjà l'agent. `data` : le YAML validé ; un
élément de liste y est nommé par son `id` ou son `name` (`where`).

**Arguments :**

- `error` : L'erreur Pydantic.
- `source` : Fichier validé.
- `root` : Nom d'une erreur sans chemin.
- `data` : Le YAML validé.

**Renvoie :**

Une `ConfigError`, une ligne « chemin — message » par erreur.

## Fonction `loom_ia.config.errors.where`

```python
def where(loc: Sequence[int | str], data: object=None) -> str
```

L'endroit d'une erreur, un élément de liste nommé par son `id` ou son `name`.

À l'essai de la fonction M1 du 21/10, « models.0.max_token » obligeait à compter les modèles :
« models[HAIKU].max_token » se retrouve dans le fichier. Sans nom (ou sans
`data`), l'indice reste.

**Arguments :**

- `loc` : Le chemin de l'erreur Pydantic.
- `data` : Le YAML validé.

**Renvoie :**

Le chemin écrit `models[HAIKU].max_tokens`.
