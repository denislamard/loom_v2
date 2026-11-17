# loom_ia.config.yaml_source

Module `loom_ia.config.yaml_source` de loom_v2 (fichier `src/loom_ia/config/yaml_source.py`).

Lecture YAML de loom : `safe_load`, sans clé en double (fonction M1).

`yaml.safe_load` garde la **dernière** valeur d'une clé répétée, sans rien
dire. Une faute de copier-coller annulait ainsi un réglage en silence : deux
`retry` (17/10), deux `capture` dont la seconde faisait partir du contenu
vers un collecteur (essai de la fonction K4, 21/10), deux `max_iterations` dans un agent
(essai de la fonction M1, 22/10). Une clé répétée dans un même objet est donc refusée, en
nommant la clé et ses deux lignes.

Une fusion YAML (`<<: *ancre`) reste permise : une clé écrite à côté d'une
fusion la surcharge, c'est l'usage même de la fusion, et ce n'est pas un
doublon. Deux clés **écrites** identiques dans le même objet en sont un.

## Constantes de `loom_ia.config.yaml_source`

- `MERGE_TAG = 'tag:yaml.org,2002:merge'` : Étiquette de la clé de fusion (`<<`).

## Classe `loom_ia.config.yaml_source.DuplicateKey`

```python
class DuplicateKey(yaml.YAMLError)
```

Une clé écrite deux fois dans le même objet.

**Arguments :**

- `key` : La clé.
- `first` : Ligne de la première.
- `second` : Ligne de la seconde.

**Attributs :**

- `key` : La clé.
- `first` : Ligne de la première.
- `second` : Ligne de la seconde.

## Classe `loom_ia.config.yaml_source._UniqueKeyLoader`

```python
class _UniqueKeyLoader(yaml.SafeLoader)
```

`SafeLoader` qui refuse une clé écrite deux fois dans un objet.

### Méthode `loom_ia.config.yaml_source._UniqueKeyLoader.construct_mapping`

```python
def construct_mapping(self, node: MappingNode, deep: bool=False) -> dict[Any, Any]
```

Construit l'objet après avoir vérifié que chaque clé écrite n'y figure qu'une fois.

**Lève :**

- `DuplicateKey` : Une clé écrite deux fois ; la clé de fusion `<<` ne compte pas.

## Fonction `loom_ia.config.yaml_source.load_yaml`

```python
def load_yaml(content: str) -> object
```

Le document YAML, comme `yaml.safe_load`.

**Arguments :**

- `content` : Le texte YAML.

**Lève :**

- `DuplicateKey` : Une clé écrite deux fois dans un même objet.
- `yaml.YAMLError` : YAML invalide.
