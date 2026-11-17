# loom_ia.tools_builtin._values

Module `loom_ia.tools_builtin._values` de loom_v2 (fichier `src/loom_ia/tools_builtin/_values.py`).

Valeurs typées : le vocabulaire des colonnes, partagé par `excel`, `word` et `pdf`.

Le modèle dit ce qu'est une valeur — un montant en euros, une date, un
pourcentage — et l'outil décide de son apparence. Ce module ne fait que la
conversion, tolérante aux exports français (`1 250,50`, `02/10/2026`,
`15%`, `oui`) ; une date-heure qui porte un fuseau passe dans celui de
l'outil, sans fuseau, car ni Excel ni un document n'en affichent.

Module léger — la bibliothèque standard seulement — : un outil l'importe sans
tirer les dépendances d'un autre.

## Constantes de `loom_ia.tools_builtin._values`

- `ColumnType = Literal['text', 'integer', 'decimal', 'amount_eur', 'amount_keur', 'percent', 'date', 'datetime', 'year', 'boolean']` : Type d'une colonne de données : texte, nombre, montant, pourcentage, date…
- `Aggregate = Literal['sum', 'average', 'count', 'min', 'max']` : Agrégat d'une colonne : somme, moyenne, compte, minimum ou maximum.
- `Scalar = str | int | float | bool | None` : Une valeur de cellule.
- `Stored = str | int | float | dt.date | dt.datetime | None` : Ce qu'une cellule reçoit : valeur convertie selon son type de colonne.

## Classe `loom_ia.tools_builtin._values.Reports`

```python
class Reports(Protocol)
```

Où dire une faute ou un avertissement : les `Problems` de chaque outil.

### Méthode `loom_ia.tools_builtin._values.Reports.error`

```python
def error(self, said: str) -> None
```

Note une erreur : la valeur est refusée.

### Méthode `loom_ia.tools_builtin._values.Reports.warn`

```python
def warn(self, said: str) -> None
```

Note un avertissement : la valeur passe, en le disant.

## Fonction `loom_ia.tools_builtin._values.convert`

```python
def convert(value: Scalar, kind: ColumnType, where: str, problems: Reports, zone: ZoneInfo) -> Stored
```

La valeur à écrire pour une colonne de ce type ; `None` et une erreur si impossible.

`zone` : le fuseau où passe une date-heure qui en porte un.

## Fonction `loom_ia.tools_builtin._values._convert`

```python
def _convert(value: Scalar, kind: ColumnType, where: str, problems: Reports, zone: ZoneInfo) -> Stored
```

Une valeur convertie selon le type de sa colonne ; `ValueError` si elle ne s'y prête pas.

Types : texte, année, booléen, nombre, montant, pourcentage, date…

## Fonction `loom_ia.tools_builtin._values._number`

```python
def _number(value: Scalar, *, percent: bool=False) -> float | int
```

Un nombre lu dans une valeur.

Espaces, « € » et « k€ » retirés, virgule décimale française comprise, `%` divisé
par 100 si `percent` ; `ValueError` sinon.
