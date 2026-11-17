# loom_ia.tools_builtin.sql.schema

Module `loom_ia.tools_builtin.sql.schema` de loom_v2 (fichier `src/loom_ia/tools_builtin/sql/schema.py`).

Le schéma que voit le modèle : celui des tables permises, en DDL, avec leurs droits.

Les LLM lisent mieux un `CREATE TABLE` qu'un JSON de colonnes, et il dit
d'un coup types, clés, contraintes et commentaires. Seules les tables
déclarées y sont ; chacune porte ses droits, et ce que la config en dit
(`describe`).

Le schéma est lu dans la base par l'outil (jamais par le modèle) et gardé
`CACHE_SECONDS` : l'agent ne peut pas le changer, seul un tiers le peut.
Il sert aussi à proposer les colonnes proches d'un nom inconnu.

## Constantes de `loom_ia.tools_builtin.sql.schema`

- `CACHE_SECONDS = 300.0` : Durée de vie du schéma lu, en secondes.

## Classe `loom_ia.tools_builtin.sql.schema.Column`

```python
@dataclass(frozen=True, slots=True)
class Column
```

Une colonne d'une table.

**Attributs :**

- `name` : Son nom.
- `type` : Son type, tel que la base le nomme.
- `not_null` : Vrai si elle refuse NULL.
- `default` : Sa valeur par défaut, en SQL.
- `comment` : Son commentaire dans la base.

## Classe `loom_ia.tools_builtin.sql.schema.TableInfo`

```python
@dataclass(frozen=True, slots=True)
class TableInfo
```

Une table ou une vue lue dans la base.

**Attributs :**

- `name` : Son nom.
- `kind` : Table ou vue.
- `columns` : Ses colonnes.
- `constraints` : Clés, unicité, CHECK, clés étrangères vers une table permise (Postgres).
- `comment` : Son commentaire dans la base.
- `ddl` : Le DDL d'origine, quand la base le garde (SQLite) : rendu tel quel.

## Fonction `loom_ia.tools_builtin.sql.schema.render`

```python
def render(tables: Iterable[TableInfo], rights: Mapping[str, Mapping[SqlRight, RightApproval]], describe: Mapping[str, str], *, database: str, dialect: str, wanted: Iterable[str] | None=None) -> str
```

Le DDL des tables permises (`wanted` : celles-là seulement), avec leurs droits.

## Fonction `loom_ia.tools_builtin.sql.schema._ddl`

```python
def _ddl(table: TableInfo) -> str
```

Le DDL d'une table reconstruit à partir de ce que Postgres en dit : colonnes, défauts, contraintes, commentaires en `--`.

## Classe `loom_ia.tools_builtin.sql.schema.SchemaCache`

```python
@dataclass(slots=True)
class SchemaCache
```

Le schéma lu, gardé `CACHE_SECONDS`.

### Méthode `loom_ia.tools_builtin.sql.schema.SchemaCache.tables`

```python
async def tables(self) -> list[TableInfo]
```

Les tables, relues au plus toutes les `CACHE_SECONDS`.

### Méthode `loom_ia.tools_builtin.sql.schema.SchemaCache.forget`

```python
def forget(self) -> None
```

Oublie le schéma lu : le prochain appel le relit.

## Fonction `loom_ia.tools_builtin.sql.schema.close_columns`

```python
def close_columns(message: str, tables: Iterable[TableInfo], within: Iterable[str]) -> str | None
```

« colonnes proches dans devis : montant_ht » pour une colonne inconnue, ou `None`.
