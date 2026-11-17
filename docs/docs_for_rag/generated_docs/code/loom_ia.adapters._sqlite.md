# loom_ia.adapters._sqlite

Module `loom_ia.adapters._sqlite` de loom_v2 (fichier `src/loom_ia/adapters/_sqlite.py`).

Mise en place commune des connexions SQLite des adaptateurs (journal, idempotence).

## Constantes de `loom_ia.adapters._sqlite`

- `WAL_PATIENCE = 5.0` : Patience laissée à la bascule en WAL : celle du `busy_timeout` des adaptateurs.

## Fonction `loom_ia.adapters._sqlite.enable_wal`

```python
async def enable_wal(connection: aiosqlite.Connection) -> None
```

Met la base en mode WAL, même si un autre process la met en place au même instant.

`PRAGMA journal_mode = WAL` est la seule instruction de mise en place que
le délai d'occupation (`busy_timeout`) ne couvre pas : sur une base neuve
que plusieurs process ouvrent ensemble, SQLite répond « database is locked »
sans attendre. Or le mode WAL se garde dans le fichier : celui qui a perdu
la course le trouve en place au nouvel essai. On lit donc le mode avant de
le poser, et on reprend, dans la limite de `WAL_PATIENCE`, tant que c'est
un verrou qui refuse. Toute autre erreur (fichier qui n'est pas une base…)
remonte aussitôt.

## Fonction `loom_ia.adapters._sqlite.enable_secure_delete`

```python
async def enable_secure_delete(connection: aiosqlite.Connection) -> None
```

Fait écraser par des zéros ce que la base supprime (RGPD).

`PRAGMA secure_delete` est propre à la connexion : à poser à chaque ouverture.

## Fonction `loom_ia.adapters._sqlite.purge_wal`

```python
async def purge_wal(connection: aiosqlite.Connection, path: Path) -> None
```

Reporte le WAL dans la base puis le vide, pour qu'une suppression n'y survive pas.

Sans cela, les pages d'avant la suppression restent dans le `-wal` jusqu'au
prochain point de reprise. Un lecteur ouvert dans un autre process peut
empêcher le point de reprise d'aboutir : SQLite répond alors par un drapeau
« occupé », pas par une erreur. La suppression a eu lieu et reste valable ;
on le dit par un avertissement, car le WAL n'est pas purgé. L'attente est
celle du `busy_timeout` de la connexion.
