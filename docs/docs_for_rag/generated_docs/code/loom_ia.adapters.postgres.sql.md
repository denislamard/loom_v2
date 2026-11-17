# loom_ia.adapters.postgres.sql

Module `loom_ia.adapters.postgres.sql` de loom_v2 (fichier `src/loom_ia/adapters/postgres/sql.py`).

Le SQL du stockage Postgres : tables, index, rôle et politiques (fonction F5, point 5).

Ce module ne contient que du texte. Il ne connaît pas `asyncpg`, et c'est
voulu : `loom storage sql` imprime le DDL sur une machine où le pilote n'est
pas installé, et l'exploitant qui l'applique n'a pas à installer loom.

Le DDL est **rejouable** : chaque instruction se rattrape si son objet existe
déjà, par `IF NOT EXISTS` quand Postgres le propose et par un bloc qui
avale `duplicate_object` quand il ne le propose pas — c'est le cas des
politiques et des rôles. Il peut donc être appliqué par loom à la première
ouverture, ou par un DBA à la main, ou les deux.

Deux rôles, et c'est là tout l'intérêt de la sécurité au niveau des lignes :

- le **propriétaire** est le rôle qui applique ce DDL. Il possède les tables
  et peut tout faire ;
- le rôle **applicatif** est celui sous lequel loom exécute ses requêtes. Il
  n'a que `SELECT`, `INSERT` et `DELETE` sur le journal — pas
  `UPDATE` : l'immuabilité du journal cesse d'être une convention de code
  pour devenir un privilège de la base. Et comme il ne possède pas la table,
  la politique s'applique à lui.

Le DDL s'ouvre sur un `HEADER` en commentaires : ce que la politique change
pour qui lit la table à la main ou la sauvegarde. Un commentaire SQL ne gêne
pas son exécution, et c'est la seule place où l'exploitant le lira à coup sûr.

La politique compare `tenant_id` à `current_setting('loom.tenant_id')`,
posé par transaction. Sans ce réglage, `current_setting(..., true)` rend
NULL, la comparaison est NULL, et **aucune ligne** ne passe : l'oubli est
donc un journal vide, jamais un journal partagé. ``FORCE ROW LEVEL
SECURITY`` étend la politique au propriétaire lui-même, pour que la barrière
tienne même si loom se connecte avec le rôle qui a créé les tables.

## Constantes de `loom_ia.adapters.postgres.sql`

- `EVENTS_TABLE = 'loom_events'` : Les tables portent leur préfixe plutôt qu'un schéma : elles cohabitent avec celles de l'application dans la base que le DSN désigne. Un client qui veut son propre schéma déclare son propre `dsn_env`, dont le rôle a son `search_path` (conception §7.3).
- `IDEMPOTENCY_TABLE = 'loom_idempotency'` : Table du magasin d'idempotence partagé.
- `SCHEMA_TABLE = 'loom_schema'` : Version du schéma de chaque composant (`events`), pour que le DDL sache ce qui est déjà appliqué. Elle appartient au propriétaire ; le rôle applicatif peut la lire, pas l'écrire.
- `TENANT_SETTING = 'loom.tenant_id'` : Réglage de transaction qui porte le client courant. Le point est obligatoire : Postgres n'accepte un réglage inconnu de lui que préfixé.
- `POLICY = 'loom_events_tenant'` : Nom de la politique de lignes de la table des événements.
- `DEFAULT_ROLE = 'loom_app'` : Rôle applicatif par défaut : celui que crée le DDL et que prend chaque connexion. `storage.events.role: null` s'en passe — la politique tient quand même, par `FORCE`, mais le journal redevient modifiable.
- `HEADER = "-- Stockage Postgres de loom (sortie de « loom storage sql »).\n--\n-- La table des événements est sous politique de l…` : En tête du DDL, parce que la politique se remarque le jour où l'on veut lire la table à la main ou la sauvegarder, et que ce jour-là on cherche loin.
- `EVENTS_DDL = f"CREATE TABLE IF NOT EXISTS {EVENTS_TABLE} (\n tenant_id text NOT NULL,\n session_id text NOT NULL,\n seq bigint NOT N…` : Table des événements, ses index et sa politique de lignes ; sans effet sur ce qui existe déjà.
- `SCHEMA_DDL = f'CREATE TABLE IF NOT EXISTS {SCHEMA_TABLE} (\n component text NOT NULL PRIMARY KEY,\n version integer NOT NULL,\n appl…` : Table des versions du schéma.
- `IDEMPOTENCY_SCHEMA_VERSION = 2` : Version du schéma de l'idempotence que ce loom attend : en deçà, l'ouverture migre.
- `IDEMPOTENCY_V2 = f' ALTER TABLE {IDEMPOTENCY_TABLE} ADD COLUMN IF NOT EXISTS holder text;'` : `holder` est le jeton du détenteur de la clé : `complete` et `release` qui en présentent un ne touchent que la ligne encore à lui. La colonne est nullable. Version 1 : la table de la 2.0, sans elle. Version 2 : la colonne, ajoutée une seule fois — la version inscrite évite le verrou exclusif de `ALTER TABLE` à chaque ouverture.
- `IDEMPOTENCY_UPGRADE = '\n'.join([SCHEMA_DDL, f"INSERT INTO {SCHEMA_TABLE} (component, version) VALUES ('idempotency', 1) ON CONFLICT (compone…` : Rejouable : c'est ce que loom applique à l'ouverture d'une table qui existe déjà. Une table sans ligne de version est au moins en version 1.
- `IDEMPOTENCY_DDL = f'CREATE TABLE IF NOT EXISTS {IDEMPOTENCY_TABLE} (\n key text NOT NULL PRIMARY KEY,\n tenant_id text NOT NULL,\n sessio…` : Pas de politique ici, et ce n'est pas un oubli : `IdempotencyStore.get` ne nomme pas de client (`get(key)`), et une politique par client rendrait invisible la ligne qu'il faut relire. Le cadrage vient de la clé — préfixée par client pour une clé métier (point 49) — et des colonnes, qui portent le client et la session pour l'oubli RGPD.
- `EVENTS_SCHEMA_VERSION = 3` : Version du schéma du journal que ce loom attend : en deçà, l'ouverture migre.
- `EVENTS_V2 = f' DROP INDEX IF EXISTS {EVENTS_TABLE}_event_id;\n CREATE UNIQUE INDEX {EVENTS_TABLE}_event_id ON {EVENTS_TABLE} (tenan…` : Version 1 : le schéma de la 2.0 (`EVENTS_DDL`). Version 2 : l'identifiant d'un événement est unique par client — l'index de la 2.0 ne l'était pas. La création échoue s'il existe des doublons, et la migration entière s'annule.
- `EVENTS_V3 = f' ALTER TABLE {EVENTS_TABLE} ADD COLUMN IF NOT EXISTS ingested bigint\n GENERATED ALWAYS AS IDENTITY;\n CREATE INDEX I…` : Version 3 : `ingested`, le rang d'écriture, que `after` suit (l'id d'un événement est posé avant son écriture, donc pas dans l'ordre où elle se fait). Les lignes existantes reçoivent leur rang dans l'ordre où la table les parcourt, ce qui est celui de leur écriture tant qu'aucune n'a été supprimée.
- `EVENTS_UPGRADE = '\n'.join([SCHEMA_DDL, f"INSERT INTO {SCHEMA_TABLE} (component, version) VALUES ('events', 1) ON CONFLICT (component) D…` : Rejouable : c'est ce que loom applique à l'ouverture d'une table qui existe déjà. Une table de la 2.0 n'a pas de ligne de version : elle est au moins en version 1.

## Fonction `loom_ia.adapters.postgres.sql.check_name`

```python
def check_name(name: str) -> str
```

Refuse un nom de rôle qui ne s'écrit pas sans guillemets.

Le DDL est du texte, pas une requête paramétrée : un nom venu de la
config y est recopié tel quel. Plutôt que d'échapper, on n'accepte que ce
qui n'a pas besoin de l'être.

## Fonction `loom_ia.adapters.postgres.sql._migration`

```python
def _migration(component: str, version: int, body: str) -> str
```

Une migration, jouée une seule fois : sa version est inscrite avec elle.

Le test de version et l'inscription sont dans le même bloc, donc dans la
même transaction que `body` : une migration qui échoue ne laisse ni
changement ni version. `$mig$` : `body` peut contenir des `$$`.

## Fonction `loom_ia.adapters.postgres.sql.role_ddl`

```python
def role_ddl(role: str) -> str
```

Création du rôle applicatif, sans erreur s'il est déjà là.

## Fonction `loom_ia.adapters.postgres.sql.grants_ddl`

```python
def grants_ddl(role: str, *, table: str, update: bool) -> str
```

Droits du rôle applicatif sur une table.

`update=False` pour le journal : un événement écrit ne se modifie pas,
et le rôle n'en a pas le droit. `DELETE` reste nécessaire, c'est la
suppression RGPD d'une session (conception §11.5).

## Fonction `loom_ia.adapters.postgres.sql.membership_ddl`

```python
def membership_ddl(role: str) -> str
```

Rend le rôle applicatif endossable par celui qui applique ce DDL.

C'est ce qui permet à loom de se connecter en propriétaire et de prendre
le rôle applicatif (`SET ROLE`) à chaque connexion. Sans appartenance,
il faut connecter loom directement avec le rôle applicatif.

## Fonction `loom_ia.adapters.postgres.sql.ddl`

```python
def ddl(*, role: str | None=DEFAULT_ROLE, events: bool=True, idempotency: bool=True) -> str
```

Le DDL complet, dans l'ordre où il s'applique.

`role=None` laisse les droits au seul propriétaire : les tables et les
politiques sont créées, mais rien n'est accordé à un rôle applicatif.
