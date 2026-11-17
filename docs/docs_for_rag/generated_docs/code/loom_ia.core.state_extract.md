# loom_ia.core.state_extract

Module `loom_ia.core.state_extract` de loom_v2 (fichier `src/loom_ia/core/state_extract.py`).

Extraction d'une valeur du state de session depuis le résultat d'un outil (state v2).

Le modèle désigne un résultat par l'outil `loom.state` ; la règle
d'extraction, elle, est déclarée sur la clé du state dans la config de l'agent
(`extract:`). Le modèle ne transmet aucune donnée : il ne peut ni mal
recopier ni inventer. Voir `docs/state_v2_analyse.md`.

Trois formes de résultat sont lues, comme des lignes :

- un tableau `{"columns": [...], "rows": [[...], ...]}` (`loom.sql`) ;
- une liste d'objets ;
- un objet seul (une ligne).

Trois règles :

- `value` : la colonne d'une ligne, telle quelle ;
- `fields` : un objet, chaque champ pris dans une colonne d'une ligne ;
- `columns` : un objet, chaque champ la liste d'une colonne sur toutes les
  lignes. Refusé sur un résultat tronqué (`"truncated": true`), qui
  donnerait une liste incomplète sans le dire.

Une colonne présente plusieurs fois (`SELECT id, code, *`) est lue quand
toutes ses occurrences ont la même valeur ; sinon (`SELECT d.id, c.id`), refus.

Une ligne se choisit par `match` — des colonnes et leurs valeurs, que doit
porter exactement une ligne du résultat (`{"code": "C-261"}`) — ou par `row`
(à partir de 0) ; sans l'un ni l'autre, le résultat doit avoir exactement une
ligne. `match` est le plus sûr : un modèle compte mal les lignes, et une
valeur qu'il lit ne peut désigner qu'une ligne qui la porte vraiment.
`label` ajoute un champ, rempli par le texte que donne le modèle (un
libellé, jamais une donnée du résultat).

Chaque refus lève `ExtractError`, dont le message s'adresse au modèle et dit
quoi corriger (ajouter une colonne à `match`, refaire la requête sans alias…).

Module pur : aucune entrée-sortie, aucune dépendance au reste de loom.

Contenu : `Extraction` (la règle d'une clé), `ModelKey` (une clé tenue par
le modèle), `extract` (appliquer une règle à un résultat) et
`ExtractError`.

## Classe `loom_ia.core.state_extract.ExtractError`

```python
class ExtractError(ValueError)
```

Extraction impossible ; le message, destiné au modèle, dit quoi corriger.

`loom.state` la change en `ToolError` (« <clé> non posé : <motif> ») :
le modèle lit le motif comme résultat en erreur, et rien n'est posé dans le
state.

## Classe `loom_ia.core.state_extract.Extraction`

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class Extraction
```

Règle d'extraction d'une clé du state, déclarée sous `extract:`.

Exactement une de `value`, `fields` et `columns` est donnée ; la
config le vérifie au chargement.

**Attributs :**

- `value` : Nom de la colonne dont la valeur, prise dans une ligne, devient la valeur de la clé, telle quelle.
- `fields` : Champ de l'objet produit → colonne où le prendre, dans une ligne. Vide par défaut.
- `columns` : Champ de l'objet produit → colonne dont il reçoit la liste des valeurs, sur toutes les lignes. Vide par défaut.
- `label` : Champ qui reçoit le libellé donné par le modèle (un texte libre, jamais une donnée du résultat) ; seulement avec `fields` ou `columns`. `None` : la clé ne prend pas de libellé.

### Méthode `loom_ia.core.state_extract.Extraction.single_row`

```python
@property
def single_row(self) -> bool
```

Vrai si la règle lit une seule ligne (`value`, `fields`).

## Classe `loom_ia.core.state_extract._Table`

```python
@dataclass(frozen=True, slots=True)
class _Table
```

Un résultat d'outil lu comme des lignes, quelle que soit sa forme.

**Attributs :**

- `names` : Noms des colonnes, dans l'ordre (doublons compris pour un tableau `columns`/`rows`).
- `rows` : Les lignes : des listes de valeurs pour un tableau, des objets sinon.
- `tabular` : Vrai pour un tableau `{"columns": …, "rows": …}`.
- `truncated` : Vrai si le tableau porte `"truncated": true`.

### Méthode `loom_ia.core.state_extract._Table.cell`

```python
def cell(self, row: int, column: str) -> JsonValue
```

Valeur d'une colonne dans une ligne.

**Arguments :**

- `row` : Indice de la ligne, à partir de 0.
- `column` : Nom de la colonne.

**Renvoie :**

La valeur de la cellule. Une colonne présente plusieurs fois est
lue si toutes ses occurrences ont la même valeur.

**Lève :**

- `ExtractError` : La colonne est absente du résultat ou de la ligne, ou présente plusieurs fois avec des valeurs différentes (le message conseille un alias dans la requête).

### Méthode `loom_ia.core.state_extract._Table.indexes`

```python
def indexes(self, column: str) -> list[int]
```

Positions d'une colonne dans les noms du tableau.

Une colonne en double (`SELECT id, *`) a plusieurs positions ; `cell`
la lit si toutes ses occurrences ont la même valeur, et la refuse sinon.

**Arguments :**

- `column` : Nom de la colonne.

**Renvoie :**

Ses positions, dans l'ordre, au moins une.

**Lève :**

- `ExtractError` : La colonne est absente ; le message liste les colonnes présentes (20 au plus).

## Classe `loom_ia.core.state_extract.ModelKey`

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class ModelKey
```

Une clé du state que le modèle tient à jour (`writers: [model]`), vue par `loom.state`.

**Attributs :**

- `name` : Nom de la clé dans le state.
- `description` : Ce que la clé désigne, repris dans la description de l'outil `loom.state`.
- `schema` : JSON Schema de la valeur, contrôlé à chaque écriture.
- `extraction` : La règle qui tire la valeur du résultat désigné.
- `required` : Vrai si un run ne s'ouvre pas tant que la clé n'a pas de valeur (décision D13 du state de session). Faux par défaut.
- `clears` : Clés que poser celle-ci efface, dans la même écriture (`clears:`). Aucune par défaut.

## Fonction `loom_ia.core.state_extract.extract`

```python
def extract(data: JsonValue, rule: Extraction, *, row: int | None=None, match: Mapping[str, JsonValue] | None=None, label: str | None=None) -> JsonValue
```

Applique une règle d'extraction au résultat d'un outil.

Avec une règle d'une ligne (`value`, `fields`), la ligne se choisit par
`match`, sinon par `row`, sinon le résultat doit avoir exactement une
ligne. Avec `columns`, toutes les lignes sont lues, et le résultat ne
doit être ni tronqué ni vide.

**Arguments :**

- `data` : Données du résultat : un tableau ``{"columns", "rows", "truncated"?}``, une liste d'objets ou un objet seul.
- `rule` : La règle déclarée sur la clé.
- `row` : Indice de la ligne, à partir de 0 ; exclusif de `match`.
- `match` : Colonnes et valeurs que doit porter exactement une ligne. Un nombre écrit en texte vaut le nombre (`"42"` trouve `42`), un booléen ne vaut que lui-même.
- `label` : Libellé donné par le modèle, rangé dans le champ `rule.label`.

**Renvoie :**

La valeur à poser : la cellule pour `value`, un objet pour
`fields` et `columns`.

**Lève :**

- `ExtractError` : Libellé donné à une clé qui n'en prend pas ; `row` et `match` donnés ensemble ; `row` ou `match` sur une règle `columns` ; résultat tronqué, vide, illisible ou sans données structurées ; ligne introuvable, ambiguë ou hors du résultat ; colonne absente ou en double avec des valeurs différentes.

## Fonction `loom_ia.core.state_extract._table`

```python
def _table(data: JsonValue) -> _Table
```

Lit un résultat comme des lignes ; `ExtractError` s'il n'a pas de données structurées.

Un tableau `columns`/`rows` (`loom.sql`), une liste d'objets (les
colonnes sont l'union de leurs clés, dans l'ordre d'apparition) ou un objet
seul (une ligne) sont acceptés ; du texte ou une image ne le sont pas.

## Fonction `loom_ia.core.state_extract._row`

```python
def _row(table: _Table, row: int | None) -> int
```

Indice de la ligne à lire : `row`, ou la seule ligne du résultat.

**Lève :**

- `ExtractError` : `row` hors du résultat ; ou, sans `row`, un résultat vide ou de plusieurs lignes (le message propose `match` ou `row`).

## Fonction `loom_ia.core.state_extract._matched`

```python
def _matched(table: _Table, match: Mapping[str, JsonValue]) -> int
```

Indice de la seule ligne qui porte ces valeurs ; un nombre écrit en texte vaut le nombre.

**Lève :**

- `ExtractError` : Aucune ligne, ou plusieurs lignes, portent ces valeurs ; ou une colonne de `match` est absente.

## Fonction `loom_ia.core.state_extract._same`

```python
def _same(value: JsonValue, wanted: JsonValue) -> bool
```

Égalité pour `match` : un booléen ne vaut que lui-même, `"42"` vaut `42`, espaces de bord ignorés.
