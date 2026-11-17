# loom_ia.tools_builtin.sql.values

Module `loom_ia.tools_builtin.sql.values` de loom_v2 (fichier `src/loom_ia/tools_builtin/sql/values.py`).

Les valeurs de la base, rendues au modèle en JSON.

- `numeric` (`Decimal`) en **chaîne** : un montant reste exact (« 1250.50 ») ;
- une date-heure avec fuseau passe dans le fuseau de l'outil, en ISO 8601 ;
  sans fuseau, elle est rendue telle quelle ; dates et heures en ISO 8601 ;
- une durée en ISO 8601 (« P1DT2H ») ;
- un binaire n'est jamais rendu : « <bytes: 1234> » ;
- un texte de plus de `MAX_CELL_CHARS` caractères est coupé, et le dit ;
- `json` et `jsonb` arrivent décodés (codec posé sur la connexion) ;
- le reste (UUID, adresses, intervalles de valeurs…) en texte.

## Constantes de `loom_ia.tools_builtin.sql.values`

- `MAX_CELL_CHARS = 1000` : Caractères au plus par cellule rendue ; au-delà, le texte est coupé et le dit.

## Fonction `loom_ia.tools_builtin.sql.values.cell`

```python
def cell(value: object, zone: ZoneInfo) -> JsonValue
```

Une valeur rendue au modèle.

## Fonction `loom_ia.tools_builtin.sql.values.row`

```python
def row(values: Sequence[object], zone: ZoneInfo) -> list[JsonValue]
```

Une ligne rendue en JSON, cellule par cellule.

## Fonction `loom_ia.tools_builtin.sql.values.duration`

```python
def duration(value: timedelta) -> str
```

ISO 8601 : « P1DT2H30M », « PT0.5S », « -P1D ».
