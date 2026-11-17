# loom_ia.core.schema_errors

Module `loom_ia.core.schema_errors` de loom_v2 (fichier `src/loom_ia/core/schema_errors.py`).

Dire une erreur de schéma sans citer la valeur fautive.

Les messages de `jsonschema` recopient la valeur qui ne convient pas :
`'sk-SECRET-123' is not of type 'integer'`. Or ce texte part dans le journal
(`guard.checked.reason`, `run.failed.error`, `tool.completed.output`) et
dans les journaux de l'hôte, qui ne doivent pas porter ce que l'utilisateur ou
l'outil a écrit. `schema_problem` ne dit que ce que le schéma exige — des mots du
code ou du schéma, que son auteur a écrits — et, de la valeur, son genre ou sa
taille : jamais son contenu (seuls les noms de propriétés en trop sont cités). Le
chemin de la valeur est dit à part par l'appelant.

Utilisé partout où une valeur venue d'un utilisateur, d'un modèle ou d'un outil
est validée par un JSON Schema : arguments d'un appel d'outil
(`engine/executor.py`), sortie contrôlée par un contrat ou lue par un juge
(`guards/contract.py`, `guards/judge.py`), valeurs du state de session
(`engine/session_state.py`, `tools_builtin/state.py`).

## Fonction `loom_ia.core.schema_errors._kind`

```python
def _kind(value: object) -> str
```

Le genre d'une valeur, jamais son contenu.

## Fonction `loom_ia.core.schema_errors._extra`

```python
def _extra(error: ValidationError) -> str
```

Les propriétés en trop, par leur nom : un nom est de la structure, pas une valeur.

Sans `patternProperties` (qui en admet d'autres par leur forme), le calcul
est exact ; sinon, le message reste général.

## Fonction `loom_ia.core.schema_errors.schema_problem`

```python
def schema_problem(error: ValidationError) -> str
```

Dit ce que le schéma exige de la valeur, sans la recopier.

Chaque mot-clé de JSON Schema a sa phrase : le type attendu et le genre
reçu (« doit être un entier (reçu : du texte) »), les valeurs permises
(`enum`, `const`, coupées à 120 caractères), les propriétés requises
absentes, les tailles (reçue et attendue), les bornes, le motif, le
format. Pour `additionalProperties`, les noms des propriétés en trop sont
cités, car un nom est de la structure. Un mot-clé inconnu donne une phrase
générale qui le nomme.

**Arguments :**

- `error` : Erreur levée par `jsonschema` ; son chemin (`error.absolute_path`) n'est pas dit ici, l'appelant le dit à part.

**Renvoie :**

Une phrase en français, sans le contenu de la valeur fautive.
