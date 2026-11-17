# loom_ia.core.bounded_regex

Module `loom_ia.core.bounded_regex` de loom_v2 (fichier `src/loom_ia/core/bounded_regex.py`).

Expressions régulières à délai : un motif ne gèle plus la boucle d'événements.

Un motif mal écrit — `(a|aa)+$` — cherche pendant des secondes, parfois des
minutes, sur quelques dizaines de caractères ; la boucle d'événements attend
avec lui, donc tous les runs de l'hôte. Le module `re` ne sait pas s'arrêter.
Le module tiers `regex` (dépendance du noyau) le sait : `timeout=`.

Tout motif qui ne vient pas du code de loom-ia passe par ce module :

- `must_match` et `must_not_match` d'un contrat de sortie ;
- les motifs de masquage de la télémétrie ;
- les mots-clés de JSON Schema qui cherchent un motif (`pattern`,
  `patternProperties`, `additionalProperties`, `unevaluatedProperties`),
  pour les schémas qu'écrit le modèle (forge) ou que sa sortie doit respecter
  (contrat).

Un motif qui dépasse `REGEX_TIMEOUT` lève `RegexTimeout` : l'appelant fait
échouer le contrôle (ou masque tout le texte), il ne conclut jamais « absent »
ni « conforme » faute d'avoir pu finir. Le délai est celui d'une recherche, pas
d'une validation entière : un schéma qui porte plusieurs motifs lents peut en
cumuler plusieurs.

`regex` accepte ce que `re` accepte (sa version 0, par défaut) et plus :
classes `\p{L}`, quantificateurs possessifs, groupes atomiques. Il va aussi
plus vite que `re` sur plusieurs motifs catastrophiques classiques, comme
`(a+)+$` : un motif lent sous `re` ne l'est pas forcément sous `regex`.

## Constantes de `loom_ia.core.bounded_regex`

- `SPECIFICATIONS = cast('Registry[Any]', _SPECIFICATIONS)` : Registre des méta-schémas que `jsonschema` sait résoudre, celui qu'il utilise lui-même.  `unresolved_references` y cherche une `$ref` qui ne se résout pas dans le schéma lui-même.
- `REGEX_TIMEOUT = 1.0` : Durée maximale d'une recherche ou d'une substitution, en secondes.  Un motif sain finit en millisecondes ; la marge couvre une machine chargée et un texte de plusieurs mégaoctets. Au-delà, `RegexTimeout` est levée.
- `PatternError = regex.error` : Exception levée par un motif invalide ; la config contrôle les motifs au chargement.

## Classe `loom_ia.core.bounded_regex.RegexTimeout`

```python
class RegexTimeout(Exception)
```

Un motif n'a pas fini sa recherche dans le délai : il est trop coûteux.

L'appelant fait échouer le contrôle (contrat de sortie, validation par
schéma) ou masque tout le texte (télémétrie) ; il ne conclut jamais que le
motif est absent ou que la valeur est conforme.

**Arguments :**

- `pattern` : Le motif qui a dépassé le délai.

**Attributs :**

- `pattern` : Le même motif. Le message dit « motif trop coûteux : <motif> (délai de 1 s dépassé) ».

## Fonction `loom_ia.core.bounded_regex.compile_pattern`

```python
def compile_pattern(pattern: str) -> regex.Pattern[str]
```

Compile un motif avec le moteur `regex`, celui qui l'exécutera.

Sert à contrôler un motif de la config au chargement : un motif accepté ici
s'exécute ensuite sans surprise.

**Arguments :**

- `pattern` : Le motif, dans la syntaxe de `regex` (version 0, compatible `re`).

**Renvoie :**

Le motif compilé.

**Lève :**

- `PatternError` : Le motif est invalide.

## Fonction `loom_ia.core.bounded_regex.search`

```python
def search(pattern: str | regex.Pattern[str], text: str) -> regex.Match[str] | None
```

Cherche la première occurrence d'un motif dans un texte, avec un délai.

**Arguments :**

- `pattern` : Le motif, en texte (compilé à chaque appel) ou déjà compilé.
- `text` : Le texte où chercher.

**Renvoie :**

La première occurrence, ou `None` si le motif n'apparaît pas.

**Lève :**

- `RegexTimeout` : La recherche a dépassé `REGEX_TIMEOUT` (1 s).

## Fonction `loom_ia.core.bounded_regex.substitute`

```python
def substitute(pattern: regex.Pattern[str], replacement: str, text: str) -> str
```

Remplace chaque occurrence d'un motif dans un texte, avec un délai.

Sert au masquage des données sensibles dans la télémétrie.

**Arguments :**

- `pattern` : Le motif compilé.
- `replacement` : Le texte de remplacement (références de groupes permises, comme pour `re.sub`).
- `text` : Le texte à traiter.

**Renvoie :**

Le texte, chaque occurrence remplacée.

**Lève :**

- `RegexTimeout` : La substitution a dépassé `REGEX_TIMEOUT` (1 s).

## Classe `loom_ia.core.bounded_regex._BoundedRe`

```python
class _BoundedRe
```

Tient lieu du module `re` dans les copies des fonctions de `jsonschema`.

## Fonction `loom_ia.core.bounded_regex._function`

```python
def _function(module: types.ModuleType, name: str) -> Callable[..., Any]
```

Une fonction de `jsonschema` (ses stubs ne typent pas les paramètres).

## Fonction `loom_ia.core.bounded_regex._bounded`

```python
def _bounded[F: Callable[..., Any]](function: F, **names: object) -> F
```

Copie d'une fonction de `jsonschema` dont `re.search` passe par `search`.

`jsonschema` cherche ses motifs avec `re.search`, dans des fonctions de
ses modules privés : `pattern`, `patternProperties`,
`additionalProperties` (par `find_additional_properties`) et
`unevaluatedProperties` (par `find_evaluated_property_keys_by_schema`,
récursive). La copie a le même code et le même espace de noms, sauf `re` ;
une récursion appelle la copie, pas l'original.

**Attention :**

Ces fonctions sont privées dans `jsonschema` : la dépendance est
bornée (`jsonschema>=4.26.0,<4.27`), et relever la borne exige de
relire ce module.

**Arguments :**

- `function` : La fonction de `jsonschema` à copier.
- `**names` : Fonctions qu'elle appelle, à remplacer par leur copie à délai (par exemple `find_additional_properties`).

**Renvoie :**

La copie, de même signature.

## Fonction `loom_ia.core.bounded_regex._clone`

```python
def _clone(module: types.ModuleType, name: str, **names: object) -> tuple[Callable[..., Any], Callable[..., Any]]
```

Une fonction de `jsonschema` et sa copie à délai, pour la table `_CLONES`.

**Arguments :**

- `module` : Le module privé de `jsonschema` qui la définit.
- `name` : Le nom de la fonction (celui du mot-clé qu'elle valide).
- `**names` : Fonctions appelées à rediriger, comme pour `_bounded`.

**Renvoie :**

Le couple (original, copie).

## Fonction `loom_ia.core.bounded_regex._bounded_class`

```python
@cache
def _bounded_class(base: type[Validator]) -> type[Validator]
```

La classe de validation `base`, dont les mots-clés de motif ont un délai.

Mise en cache : une classe par version de JSON Schema.

**Arguments :**

- `base` : Classe de validation de `jsonschema` (`Draft202012Validator`…).

**Renvoie :**

Une classe dérivée par `validators.extend`, où chaque mot-clé dont la
fonction figure dans `_CLONES` est remplacé par sa copie.

## Fonction `loom_ia.core.bounded_regex.bounded_validator`

```python
def bounded_validator(schema: Mapping[str, Any]) -> Validator
```

Validateur d'un schéma dont les motifs ont un délai.

La classe suit `$schema` (2020-12 sinon), comme `validator_for`. Le
`RegexTimeout` sort tel quel de `validate`, `iter_errors` et
`is_valid` : une erreur de validation serait lue par `not`, `anyOf` ou
`if` comme « ne correspond pas », et ferait réussir un contrôle qui n'a
pas eu lieu.

**Arguments :**

- `schema` : Le schéma, déjà contrôlé par `check_schema`.

**Renvoie :**

Un validateur `jsonschema` ordinaire, dont `pattern`,
`patternProperties`, `additionalProperties` et
`unevaluatedProperties` cherchent leurs motifs avec un délai.

## Fonction `loom_ia.core.bounded_regex.check_schema`

```python
def check_schema(schema: Mapping[str, Any]) -> None
```

Contrôle un schéma JSON avant de s'en servir.

Comme `check_schema` de `jsonschema`, mais le format `regex` se
compile avec `regex` : un motif accepté ici s'exécute à la validation.

**Arguments :**

- `schema` : Le schéma à contrôler (sortie structurée, clé du state, schéma écrit par le modèle pour la forge…).

**Lève :**

- `SchemaError` : Le schéma ne respecte pas son méta-schéma (la première erreur trouvée) ; un `$schema` est déclaré ailleurs qu'à la racine (`jsonschema` changerait de classe de validation à ce sous-schéma et reprendrait la classe d'origine, sans délai) ; ou une `$ref` ne se résout pas (« « $ref » introuvable dans le schéma »).

## Fonction `loom_ia.core.bounded_regex.unresolved_references`

```python
def unresolved_references(schema: Mapping[str, Any]) -> list[str]
```

Les `$ref` d'un schéma qui ne se résolvent pas, dans l'ordre où elles apparaissent.

Une référence se résout dans le schéma lui-même (pointeur, ancre, `$id`
d'un sous-schéma) ou dans les méta-schémas de `jsonschema`. Rien ne va
chercher un fichier ni une adresse : la validation d'une sortie ne doit pas
sortir du process, et un `$ref: autre.json` ne se résoudrait pas non plus
à l'exécution — il ferait échouer le contrôle de la réponse finale, une
fois le modèle payé.

**Arguments :**

- `schema` : Le schéma à parcourir.

**Renvoie :**

Les références introuvables, chacune une seule fois ; une liste vide
si toutes se résolvent.

## Fonction `loom_ia.core.bounded_regex._is_regex`

```python
def _is_regex(instance: object) -> bool
```

Contrôle du format `regex` : vrai pour un non-texte, `PatternError` pour un motif invalide.

## Fonction `loom_ia.core.bounded_regex._nested_dialect`

```python
def _nested_dialect(schema: Mapping[str, Any]) -> bool
```

Vrai si un sous-schéma (et non la racine) déclare un `$schema`.
