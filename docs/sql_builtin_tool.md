# Analyse d'impact — outil fourni `loom.sql` loom_v2

26 octobre 2026 · Denis Lamard

## En bref

Le but : livrer avec loom-ia un outil qui donne à un agent l'accès à des bases SQL (SQLite et Postgres pour ce lot). Le modèle écrit lui-même ses requêtes. L'outil n'admet que `SELECT`, `INSERT`, `UPDATE` et `DELETE`, sur une liste de tables déclarée, avec des droits table par table. Le cadrage, ses décisions D-1 à D-21 et leurs recommandations sont ceux du 26/10. Cette analyse mesure ce qu'ils coûtent, sur un prototype complet, en version 2.0.14.

**Verdict : rien ne bloque.**

- **Ce qui est ajouté :** deux modules nouveaux, `tools_builtin/sql_params.py` et le sous-paquet `tools_builtin/sql/`, plus l'extra `sql`.
- **Ce qui est retouché**, en trois endroits courts :
  - le catalogue et `BuiltinContext` (+25 lignes) ;
  - `runtime/wiring.py` (+39 / −2) ;
  - `tenancy/secrets.py` (+26 / −1).
- **Ce qui ne bouge pas :** le moteur, le journal et le schéma de config, à part la nouvelle référence dans `loom schema`.
- **Sans l'outil :** un agent qui n'active pas `loom.sql` monte exactement comme avant.
- **Sans l'extra :** ni sqlglot ni asyncpg ne sont importés. Un agent qui n'a que des bases SQLite n'importe pas asyncpg.

- **Tests :** 3 512 verts, soit les 3 364 de la référence et 148 nouveaux (119 unitaires, 29 d'intégration sur un vrai Postgres 16). En noyau seul, 2 580 verts comme la référence ; les deux modules d'essai sautent faute de l'extra.
- **Qualité :** pyright strict à 0, ruff propre, les 5 contrats d'import tenus. La couverture passe de 95,21 % à 95,22 % (services : Postgres seul).
- **Mutations :** 14 mutations des règles de sécurité essayées, et toutes tombent. Deux passaient d'abord : les tests ont été renforcés (§ Tests).
- **Run réel par la CLI :** `loom validate`, puis `loom run` qui s'arrête sur l'approbation d'un `UPDATE`. Ensuite `loom approve`, qui écrit en base. Enfin `loom replay` donne « Identique » sans retoucher la base.

| Point | Constat | Preuve |
| --- | --- | --- |
| Aucun outil actif par défaut | `loom.sql` ne s'active que par `- python: loom.sql` | Démontré (montage, `loom validate`) |
| Chargement à la demande | Valider une config n'importe ni le paquet `sql`, ni sqlglot, ni asyncpg ; une base SQLite seule n'importe pas asyncpg | Démontré (sous-process, `sys.modules`) |
| Extra absent | `Outil fourni 'loom.sql' : dépendance 'sqlglot' absente (installer l'extra : loom-ia[sql])`, au montage, donc dès `loom validate` | Démontré |
| Jamais de DDL | `CREATE`, `ALTER`, `DROP`, `TRUNCATE`, `MERGE`, `LOCK`, `SET`, `COPY`, `CALL`, `EXPLAIN`, `BEGIN`, `PRAGMA`, `ATTACH`, `SELECT … INTO`, `FOR UPDATE`, CTE écrivante : refusés par l'analyse, puis par la base | Démontré (40 cas d'analyse, 13 cas d'authorizer, 5 cas au rôle Postgres) |
| Liste de tables | Toute table citée est déclarée, sans schéma devant ; la base ne laisse lire que celles-là | Démontré, analyse et base séparément |
| Droits par table | `select` / `insert` / `update` / `delete`, avec une approbation par droit, comme les dossiers de `local_files` | Démontré (`assess_call`, run en pause) |
| La base fait foi | Sans l'analyse devant : le rôle Postgres et l'authorizer SQLite refusent ce que la config n'accorde pas | Démontré (instructions passées directement à la base) |
| Rôle Postgres vérifié | Superutilisateur ou BYPASSRLS refusé ; un droit promis et refusé par la base bloque les appels ; un droit en trop est dit une fois | Démontré (superutilisateur à la main, le reste en CI) |
| Transactions | `execute` : jusqu'à 20 instructions, tout ou rien ; au-delà de `max_affected_rows`, rien n'est enregistré | Démontré (SQLite et Postgres) |
| Délais et annulation | Délai de la base (`statement_timeout`, interruption SQLite) ; le délai de l'outil arrête la requête côté serveur | Démontré (`pg_stat_activity` vide après annulation) |
| Secrets par client | `dsn_env` entre dans la table de chaque client, redirigeable (`secrets:`) | Démontré |
| Stockage de loom protégé | Fichier SQLite du journal ou de l'idempotence refusé ; DSN du stockage refusé, par nom et par valeur | Démontré |
| Rejeu | Les résultats viennent du journal, la base n'est pas retouchée | « Identique » |

**Trois choses sont à régler avant de fusionner :**

1. **Les décisions nouvelles D-22 à D-26**, nées du prototype (§ Décisions à prendre). La plus visible est D-22 : le DSN est vérifié au montage, si bien qu'un rejeu sur une autre machine demande la variable.
2. **La documentation et la CI** (§ Plan de mise en œuvre). La CI a déjà le service Postgres. Le prototype ajoute sqlglot et asyncpg au contrôle « Le noyau s'importe sans SDK ».
3. **Un essai avec un vrai modèle**, pour voir s'il suit les règles (`$1`, `schema` avant d'écrire, pas de nom qualifié) sans aide.

## Périmètre et état de référence

L'analyse porte sur `1ac7b19` (« Excel tool builtin »), le commit du poste et d'`origin/main`. On y ajoute les trois modifications non commitées du poste : la version 2.0.14 dans `pyproject.toml`, `uv.lock` et `CHANGELOG.md`. Elles ont été reportées à l'identique sur le clone. Seule différence : ton `uv` récrit `revision = 5` en tête du lock, le mien `3`. Le prochain `uv lock` sur ton poste l'alignera.

Rien n'a été écrit dans `/home/denis/dev/loom_v2`, à part ce document. Les essais ont tourné sur un clone, dans l'espace de travail cloud de Claude, en Python 3.14.6, avec `uv sync --all-extras --group types`, PostgreSQL 16.15 et SQLite 3.53.1. Le rôle Postgres est celui de la CI (`loom`, `NOSUPERUSER CREATEROLE`, propriétaire de sa base).

**Lu :**
- `tools_builtin/__init__.py`, `_folders.py`, `local_files.py`, `local_files_params.py`, `excel_params.py` ;
- le sous-paquet `excel/` ;
- `runtime/wiring.py` : `build_agent`, `_tool`, `_builtin_tool`, `_protected_paths`, `_check_durable_journal`, `Agent.aclose` ;
- `agents/spec.py` : `_PARAMETERIZED`, `_builtin_reference`, `builtin_settings` ;
- `tenancy/secrets.py` et `tenancy/registry.py` ;
- `core/ports/tool.py` et `core/model/tooling.py` ;
- `tools/configured.py` et `tools/idempotent.py` ;
- `engine/executor.py` (reprise, `on_unknown`) ;
- `replay/variant.py` ;
- `adapters/postgres/pool.py` et `adapters/_sqlite.py` ;
- `tests/unit/test_tools_builtin.py`, `test_excel.py` ;
- `tests/conftest.py` et `tests/unit/conftest.py` ;
- `.github/workflows/ci.yml`, `pyproject.toml` ;
- `docs/outil-excel-analyse.md` et `docs/tools-builtin-analyse.md`.

| Contrôle sur la référence, avant toute modification | Résultat |
| --- | --- |
| `pytest`, tous les extras, Postgres seul (sans RabbitMQ ni Redis) | 3 364 passés, 73 sautés |
| `pytest`, noyau seul (`uv sync --locked`) | 2 580 passés, 401 sautés |
| Couverture en branches, mêmes services | 95,21 % |
| `pyright` strict | 0 erreur |
| `ruff check` / `ruff format --check` | propres |
| `lint-imports` | 5 contrats tenus |

## Ce qui est décidé (26/10)

Les recommandations du cadrage, toutes appliquées par le prototype :

| # | Décision |
| --- | --- |
| D-1 | **`loom.sql`** : un seul outil, `sql`, avec trois actions (`schema`, `query`, `execute`) et plusieurs bases dans ses paramètres. Une référence ne produit qu'un `Tool`, et les noms d'outils sont uniques par agent |
| D-2 | Extra **`sql`** = `sqlglot>=30.24.0,<31` et `asyncpg>=0.31.0` ; SQLite par la bibliothèque standard |
| D-3 | sqlglot est obligatoire : `assess_call` juge un appel sans toucher à la base |
| D-4 | `query` : une lecture ; `execute` : `INSERT`, `UPDATE` ou `DELETE` à la racine ; `MERGE` refusé dans ce lot |
| D-5 | Droits **comme `local_files`** : la liste des droits accordés, ou `{rights, approval}` pour changer une approbation. Défauts : `select` et `insert` à `never`, `update` et `delete` à `always`. `update` et `delete` exigent `select`. *Révisée le 27/10/2026* : la table droit → approbation d'origine est refusée, voir `docs/rights_approval.md` |
| D-6 | Tables sans schéma devant ; un `schema` déclaré par base Postgres, posé en `search_path` |
| D-7 | Une fonction inconnue de sqlglot est refusée, sauf si elle est listée dans `functions:` ; celles de `FORBIDDEN` le sont toujours |
| D-8 | `UPDATE` et `DELETE` sans `WHERE` refusés ; `allow_full_table: true` pour les permettre |
| D-9 | `execute` : au plus 20 instructions, en une transaction |
| D-10 | Paramètres `$1…$n` et une liste `params`, pour les deux bases |
| D-11 | `numeric` rendu en chaîne ; dates-heures en ISO dans `timezone` ; binaire résumé ; `json`/`jsonb` décodés |
| D-12 | 200 lignes lues (plafond 1 000), 100 modifiées, 10 s par instruction, 1 000 caractères par cellule |
| D-13 | Description statique tirée de la config ; `schema` lu en direct, gardé 5 min |
| D-14 | Postgres : pool ouvert au premier appel, fermé par `aclose`. SQLite : une connexion par appel |
| D-15 | `BuiltinContext.secrets` ; `dsn_env` ajouté aux noms que garde la table d'un client |
| D-16 | Fichier SQLite hors des chemins protégés ; DSN différent de celui du stockage ; superutilisateur refusé |
| D-17 | Droit promis et refusé par la base : erreur ; droit en trop : avertissement ; `loom sql grants` plus tard |
| D-18 | Triggers SQLite du propriétaire de la base : permis |
| D-19 | `on_unknown: pause` dès qu'une écriture est accordée |
| D-20 | Masquage de colonnes : plus tard |
| D-21 | **Version 2.0.14** |

## Analyse fonctionnelle

### Ce que voit l'utilisateur de loom

```yaml
# agents/relances.yaml
tools:
- python: loom.sql
  timezone: Europe/Paris              # par défaut
  databases:
  - name: crm                         # ce que voit le modèle
    postgres:
      dsn_env: CRM_DATABASE_URL       # jamais le DSN en clair
      schema: public                  # par défaut
    tables:
      devis:    [select, update]      # update : approbation par défaut
      clients:  [select]
      relances: [select, insert]
    describe:                         # facultatif, dans la description de l'outil
      devis: "Devis envoyés aux clients"
      devis.statut: "brouillon | envoye | relance"
    functions: [age]                  # facultatif : fonctions inconnues de sqlglot permises
    # statement_timeout: 10  max_rows: 200  max_affected_rows: 100  allow_full_table: false
  - name: stock
    sqlite:
      path: data/stock.db             # absolu ou relatif au loom.yaml ; jamais créé
    tables:
      articles: [select]
```

```bash
uv add 'loom-ia[sql]'
```

`approval`, `side_effects` et `idempotent` sont refusés sur la référence, comme pour `local_files` : `idempotent ne se règle pas sur cet outil — les droits de chaque table les expriment (tables)`. `timeout` et `offload_over` restent permis.

**Les contrôles au chargement.** Ce sont des messages Pydantic, rendus comme ceux de `local_files` :
- **une base :** `postgres` ou `sqlite`, et un seul ; au moins une table ;
- **les tables :** des noms simples (`^[A-Za-z_][A-Za-z0-9_$]{0,62}$`). Une majuscule veut dire un nom entre guillemets côté Postgres. En SQLite, deux tables qui ne diffèrent que par la casse sont refusées ;
- **les droits :** `update` ou `delete` sans `select` est refusé, comme un droit en double ;
- **`describe` et `functions` :** une clé de `describe` doit nommer une table déclarée ; une fonction donnée deux fois est refusée ;
- **le reste :** des noms de base uniques, un fuseau IANA, aucun `~` ni `$` dans `path`.

**Les contrôles au montage**, donc dès `loom validate`, sans jamais se connecter :
- **SQLite :** le fichier doit exister. Il ne doit être ni le journal, ni le magasin d'idempotence, ni sous un dossier protégé (artefacts, agents, prompts).
- **Postgres :** la variable du DSN doit être présente et non vide. Elle ne doit pas être celle du stockage de loom (journal, idempotence, bus, à la racine et pour chaque client), ni porter le même DSN.

`loom validate` affiche :

```text
Fournis    : loom.sql
  relances : modèle FAKE, 1 outil(s) Python
    outil sql : crm → sqlite, /…/data/crm.db : devis select, update (approbation) ; clients select ; relances select, insert
    outil sql : crm : 200 lignes lues, 100 modifiées au plus ; 10 s par instruction
    outil sql : fuseau : Europe/Paris
```

Pour une base Postgres, la ligne dit `crm → postgres, CRM_DATABASE_URL, schéma public` : la variable, jamais le DSN.

### Ce que le modèle envoie

```json
{"action": "schema",  "database": "crm", "tables": ["devis"]}
{"action": "query",   "database": "crm",
 "sql": "SELECT d.id, c.nom, d.montant_ht FROM devis d JOIN clients c ON c.id = d.client_id WHERE d.statut = $1 AND d.envoye_le < $2",
 "params": ["envoye", "2026-10-15"]}
{"action": "execute", "database": "crm", "statements": [
   {"sql": "UPDATE devis SET statut = 'relance' WHERE id = $1", "params": [1]},
   {"sql": "INSERT INTO relances (devis_id, canal) VALUES ($1,$2)", "params": [1, "email"]}]}
```

Les clés sont en anglais, comme pour `local_files` et `excel`. Une clé qui ne sert pas à l'action est refusée en le disant : `statements ne sert pas à query`.

### Ce que le modèle reçoit

- **`query`** : `{"database", "columns", "rows" (listes), "row_count", "truncated"}`.
- **`execute`** : `{"database", "results": [{"affected_rows", "columns"?, "returning"?}], "affected_rows", "committed": true}`.
- **`schema`** : du DDL, avec les droits, `describe`, les commentaires, les clés et les contraintes. Seules les clés étrangères vers une table permise y figurent. Exemple réel, sur Postgres :

```sql
-- Base « crm » (postgres) : tables permises et leurs droits.

-- droits : SELECT, UPDATE (approbation humaine)
CREATE TABLE devis (  -- Devis envoyés aux clients
  id integer NOT NULL DEFAULT nextval('devis_id_seq'::regclass),
  client_id integer NOT NULL,
  statut text NOT NULL DEFAULT 'brouillon'::text,
  montant_ht numeric(10,2),
  envoye_le timestamp with time zone,
  …
  PRIMARY KEY (id),
  FOREIGN KEY (client_id) REFERENCES clients(id),
  CHECK ((statut = ANY (ARRAY['brouillon'::text, 'envoye'::text, 'relance'::text])))
);
```

Sur SQLite, c'est le DDL d'origine de `sqlite_schema`, commentaires `--` compris.

**Les valeurs** (D-11), éprouvées sur Postgres (le binaire, en test unitaire) :

| Type de la base | Rendu au modèle |
| --- | --- |
| `numeric` | `"1250.50"` |
| `timestamptz` | `"2026-10-01T09:30:00+02:00"`, passé dans le fuseau de l'outil |
| `date` | `"2026-11-01"` |
| `jsonb` | `{"canal": "email"}`, décodé |
| `uuid` | en texte |
| `interval` | `"P1DT2H30M"` |
| `text[]` | une liste |
| binaire | `"<bytes: 12>"` |

**Les paramètres** sont convertis d'après les types que Postgres donne à la préparation. asyncpg refuse une chaîne pour une `date` : sans cette conversion, presque tout paramètre de date échouerait.

| Ce que le modèle donne | Ce que l'outil en fait |
| --- | --- |
| `"2026-10-15"` pour une `date` | une date |
| `"2026-10-15T00:00:00"` pour un `timestamptz` | heure de Paris, faute de fuseau |
| `500` ou `"299.99"` pour un `numeric` | un `Decimal` |
| `[1, 2]` pour `ANY($1)` | un tableau |
| `{"canal": "email"}` pour un `jsonb` | du JSON |
| `3600` ou `"P2D"` pour un `interval` | une durée |
| `"oui"` pour un `bool` | `true` |

Un type non géré (`inet`…) demande un cast dans le SQL : `($n::text::inet)`.

### Ce que l'outil refuse, et comment il le dit

| Faute | Message (extrait) |
| --- | --- |
| DDL et commandes | `Instruction DROP interdite. Autorisé : INSERT, UPDATE, DELETE (execute).` ; idem pour `LOCK`, `CALL`, `SET`, `COPY`, `TRUNCATE`, `EXPLAIN`, `MERGE`, `BEGIN` |
| Plusieurs instructions | `Une seule instruction par entrée (2 données).` |
| Écriture dans `query` | `UPDATE écrit : passer par execute ; query ne fait que lire.` |
| CTE écrivante | `query ne fait que lire : une écriture passe par execute.` / `Une instruction n'écrit que dans sa table cible (pas de WITH … INSERT/UPDATE/DELETE) : en faire deux instructions d'execute.` |
| `SELECT … INTO`, `FOR UPDATE` | `SELECT … INTO crée une table : interdit.` / `FOR UPDATE / FOR SHARE verrouille des lignes : interdit.` |
| Table non déclarée | `Table « audit » non autorisée. Tables de la base : devis, clients, relances.` |
| Nom qualifié | `Nom qualifié « public.devis » interdit : écrire « devis ».` |
| Droit absent | `DELETE non autorisé sur « clients » (droits : select).` |
| Sans `WHERE` | `UPDATE sans WHERE refusé : il toucherait toute la table « devis » ; ajouter une condition.` |
| Fonction dangereuse | `Fonction pg_sleep interdite : elle agit hors de la requête.` |
| Fonction inconnue | `Fonction clock_timestamp non permise : inconnue de l'analyse. Si elle ne modifie rien, la base peut la permettre (functions) ; sinon s'en passer.` |
| Paramètres | `Paramètres à écrire $1, $2… (ni « ? » ni « :nom »).` / `$2 cité, mais 1 paramètre(s) donné(s) dans params.` / `Paramètre $1 donné mais jamais cité dans le SQL.` |
| Valeur | `Paramètre $1 : '01/11/2026' ne convient pas au type date.` |
| Colonne inconnue | `… column "montant" does not exist … Colonne « montant » inconnue — colonnes proches dans devis : montant_ht, meta.` |
| Trop de lignes | `2 lignes modifiées, au-delà de 1 : rien n'est enregistré (transaction annulée). Restreindre le WHERE, ou procéder par lots.` |
| Échec en cours d'`execute` | `Instruction 2 : Erreur Postgres (23514) : new row … violates check constraint … Rien n'est enregistré.` |
| Délai | `Instruction arrêtée après 10 s (délai de la base).` |
| Base injoignable, rôle inconnu | `Base crm injoignable (ConnectionRefusedError) : réessayer plus tard.` / `Base crm : connexion refusée (28000).` (jamais le DSN) |
| Rôle | `Base crm : le rôle postgres est superutilisateur — il passe outre tout droit, loom.sql le refuse.` / `… droits accordés par la config mais refusés par la base au rôle … : clients INSERT. Les donner (GRANT), ou les retirer de la config.` |

### Approbation, reprise, rejeu

| Appel | Effets | Approbation | Reprise après interruption |
| --- | --- | --- | --- |
| `schema` | `none` | jamais | relancé |
| `query` | `none` | celle du droit `select` des tables lues | relancé |
| `execute` avec des `INSERT` seuls | `reversible` | selon les droits exercés | `on_unknown: pause` |
| `execute` avec `UPDATE` ou `DELETE` | `irreversible` | selon les droits exercés | `on_unknown: pause` |

**Le motif montré à l'approbateur** nomme les droits exercés, par exemple : `crm : insert sur relances, update sur devis — approbation demandée pour update sur devis`.

**La `spec` déclare le pire des cas**, comme `local_files`. Un `UPDATE` à `always` met donc l'agent sous la garde du journal durable (`_check_durable_journal`), hors profil `dev`.

**Le rejeu** sert les résultats du journal. En variante, un `query` nouveau s'exécute pour de vrai et relit la base du moment. Un `execute` nouveau n'est jamais exécuté : c'est la règle de `replay/variant.py` pour un outil à effets.

### Limites du premier lot, à documenter

- **Hors périmètre, comme prévu :** `MERGE`, `EXPLAIN`, le masquage de colonnes, l'échantillonnage des valeurs pour le schéma, une transaction sur plusieurs appels, MySQL, `loom sql grants`.
- **Les données lues partent chez le fournisseur du modèle.** C'est le cas de tout ce que le rôle peut lire, y compris le `detail` d'une erreur Postgres (`Failing row contains (1, 1, nope, 1250.50, …)`). Pour le RGPD, la barrière est la liste des tables et les droits du rôle (D-25).
- **`loom validate` ne se connecte pas.** Un `GRANT` manquant ne se voit qu'au premier appel, où il bloque tous les appels avec un message qui le nomme (D-23).
- **L'analyse ne prouve pas la justesse.** Un `WHERE` trop large passe ; c'est `max_affected_rows` qui borne alors les dégâts.
- **SQLite :** l'outil ne touche pas aux réglages de la base. `foreign_keys` reste celui de SQLite, donc coupé par défaut. Une base en WAL se lit en `mode=ro` (essayé).
- **PgBouncer en mode transaction :** non essayé. L'outil prépare chaque instruction ; il faut PgBouncer 1.21 ou plus récent, avec `max_prepared_statements`.

## Analyse technique

### Modifications, mesurées sur le prototype

| Fichier | Changement |
| --- | --- |
| `src/loom_ia/tools_builtin/sql_params.py` | Nouveau (252 lignes) : `SqlParams`, `DatabaseSpec`, `PostgresSpec`, `SqliteSpec`, `secret_names()`. Module léger : Pydantic seulement |
| `src/loom_ia/tools_builtin/sql/` | Nouveau sous-paquet, découpé comme `excel/` (1 936 lignes au total, voir ci-dessous) |
| `src/loom_ia/tools_builtin/__init__.py` | +25 : l'entrée `loom.sql` (`Builtin` avec `params`, `refused`, `extra="sql"`) ; `BuiltinContext.secrets` et `BuiltinContext.protected_dsns` ; le protocole `NamesSecrets` (paramètres qui lisent des secrets) |
| `src/loom_ia/runtime/wiring.py` | +39 / −2 dans `build_agent` : le contexte reçoit la table des secrets du client et les variables des DSN du stockage (`_protected_dsns`) ; les outils fournis qui ont un `aclose` sont fermés avec l'agent (`_builtin_closables`). Seulement les outils fournis : un outil à soi qui aurait un `aclose` n'était pas fermé par loom, et ne l'est toujours pas |
| `src/loom_ia/tenancy/secrets.py` | +26 / −1 : `_named` lit aussi les noms des paramètres `NamesSecrets` des outils fournis (`_builtin_secrets`), d'après `config.all_agents` et les seuls modules légers |
| `pyproject.toml` | Extra `sql`, ajouté à `all`, avec son commentaire |
| `uv.lock` | +1 paquet : sqlglot 30.24.0 ; asyncpg 0.31.0 y était déjà |
| `.github/workflows/ci.yml` | `sqlglot` et `asyncpg` dans le contrôle « Le noyau s'importe sans SDK » |
| `tests/unit/test_sql.py` | Nouveau (119 tests), sauté sans l'extra |
| `tests/integration/test_sql_postgres.py` | Nouveau (29 tests, marque `integration`), sur `LOOM_TEST_POSTGRES` ; chaque essai crée son schéma et son rôle, puis les supprime |
| `tests/unit/test_tools_builtin.py` | +25 / −4 : `SAMPLE_PARAMS` pour `loom.sql` ; le test « valider n'importe que les paramètres » valide aussi une référence `loom.sql` et vérifie que `sql`, sqlglot et asyncpg restent hors de `sys.modules` ; le test du schéma attend `loom.sql`. C'est le seul test existant dont l'attendu change |

Le sous-paquet `tools_builtin/sql/` :

| Module | Lignes | Rôle |
| --- | --- | --- |
| `arguments.py` | 125 | Ce que voit le modèle : `SqlArguments`, `Statement`, le schéma JSON sans titres |
| `analysis.py` | 370 | sqlglot : type d'instruction, tables et droits exercés, fonctions, `WHERE`, paramètres |
| `sqlite.py` | 321 | Connexion par appel, authorizer, délai, interruption, introspection |
| `postgres.py` | 522 | Pool, contrôle du rôle, transaction par appel, conversion des paramètres, introspection |
| `schema.py` | 151 | Rendu DDL, cache de 5 min, colonnes proches |
| `values.py` | 84 | Valeurs rendues au modèle |
| `tool.py` | 339 | `SqlTool` (`spec`, `assess_call`, `invoke`, `settings_lines`, `aclose`), description, fabrique `build` |

Ni `core/`, ni `engine/`, ni `core/events`, ni `agents/spec.py`, ni `access/cli` ne sont touchés. La référence à paramètres, `loom validate` et `loom schema` fonctionnent grâce au mécanisme posé pour `local_files`.

### Choix techniques et leurs raisons

**Deux barrières, une seule qui fait foi.** L'analyse (sqlglot) passe devant. Elle sert à `assess_call`, qui ne doit pas toucher la base, et au modèle, qui reçoit des erreurs qu'il sait corriger. La base fait foi :
- **Postgres** — le rôle du DSN. Les lectures passent en transaction `READ ONLY`, chaque instruction est préparée (le serveur refuse alors plusieurs instructions), et `search_path`, `statement_timeout` et `lock_timeout` sont posés en local.
- **SQLite** — l'authorizer de la connexion, en liste blanche, sur une connexion `mode=ro` ou `mode=rw` (jamais `rwc`), avec `trusted_schema = OFF` et `ATTACH` borné à zéro.

Les tests passent des instructions interdites directement à la base, sans l'analyse : elles y sont refusées.

**Ce que le prototype a appris de sqlglot 30.24.**
1. **Les fonctions qui agissent hors de la requête lui sont inconnues.** Sur une soixantaine de fonctions Postgres essayées, toutes les courantes sont typées (`date_trunc`, `to_char`, `string_agg`, `coalesce`, `make_interval`, `generate_series`, `now`…). Seules quatre restent inconnues : `age`, `clock_timestamp`, `jsonb_build_object`, `jsonb_array_elements`. Toutes les dangereuses sont inconnues : `pg_sleep`, `pg_terminate_backend`, `set_config`, `nextval`, `setval`, `pg_advisory_lock`, `dblink`, `lo_import`, `pg_read_file`, `pg_notify`… D'où D-7 : une fonction inconnue est refusée, sauf si `functions:` la permet ; celles de `FORBIDDEN` le sont même permises. Un test les reprend toutes, pour qu'une montée de sqlglot qui en typerait une le dise.
2. **`$1,$2` sans espace ne se lit pas en dialecte Postgres** (« Error tokenizing »). C'est ce qu'écrit un LLM dans un `VALUES ($1,$2,$3)`. La copie analysée insère une espace après chaque `$n` ; le SQL envoyé à la base reste celui du modèle. C'est sans risque : une étiquette de chaîne dollar-quotée ne commence pas par un chiffre.
3. **Les instructions qu'il ne sait pas lire** (`LOCK`, `CALL`, `EXPLAIN ANALYZE`) deviennent des `Command`, refusés par la liste blanche des types. Il le dit en avertissement, avec le SQL du modèle : un filtre sur le logger `sqlglot` retire ce seul message (D-24).

**Ce que le prototype a appris de l'authorizer SQLite 3.53.**
- **`count(*)` sur une table** se voit comme `READ(table, '', None, None)`, la même forme qu'une CTE lue sans colonne. Une règle naïve laisserait compter les lignes d'une table interdite. L'outil passe donc à l'authorizer les noms des CTE vus par l'analyse.
- **`count(*)` sur une vue** lit ses colonnes avec la vue pour origine, puis la table d'un bloc, sans origine : `READ(table, '', 'main', None)`. Le prototype refusait d'abord `SELECT count(*) FROM v_audit` sur une vue permise dont la table ne l'est pas. Corrigé : une table lue par une vue permise dans le même appel peut l'être ensuite d'un bloc. `SELECT count(*) FROM audit` reste refusé.
- **Un trigger qui écrit** se voit comme `INSERT(table, None, 'main', trigger)`. Les noms des vues et des triggers sont lus avant de poser l'authorizer ; les accès dont un trigger est l'origine sont permis (D-18).
- **`INSERT` dans une table `AUTOINCREMENT`** : l'écriture dans `sqlite_sequence` ne passe pas par l'authorizer.

**Le rôle Postgres est vérifié au premier usage du pool**, une fois :
- un rôle superutilisateur ou `BYPASSRLS` est refusé ;
- un schéma absent, ou un droit déclaré que `has_table_privilege` dément, fait échouer chaque appel jusqu'au `GRANT` ;
- un droit en trop est dit une fois en avertissement : TRUNCATE, REFERENCES, TRIGGER, une table non déclarée lisible, `CREATE` sur le schéma, `TEMP` sur la base. Message réel : `le rôle … peut plus que la config — devis DELETE, devis TRUNCATE, audit (non déclarée). La défense ne repose alors que sur l'analyse de l'outil : retirer ces droits (REVOKE).`

`has_schema_privilege` lève sur un schéma absent : l'existence est donc vérifiée d'abord.

**Délais et annulation.**
- **Postgres :** `statement_timeout` est posé à chaque transaction. Le délai de l'outil, plus long, annule la tâche ; asyncpg envoie alors l'annulation au serveur. L'essai vérifie qu'aucune requête du rôle n'est encore active dans `pg_stat_activity` après une annulation à 0,5 s.
- **SQLite :** `set_progress_handler` porte le délai. Une annulation appelle `interrupt()` depuis la boucle, et la base n'est plus tenue juste après (`BEGIN IMMEDIATE` passe).

**Les secrets suivent les clients.** Le DSN est lu dans la table du client pour qui l'agent est monté, celle que reçoivent déjà les fabriques de `tool_sources`. `secret_names()` le fait entrer dans la table de chaque client : un client peut le rediriger (`secrets: {CRM_DATABASE_URL: DUPONT_CRM_URL}`), et chaque artisan a ainsi sa base. Sans clients déclarés, la table est l'environnement, comme avant.

**Le coût en tokens.** La description et le schéma de l'outil font 1 129 + 2 001 caractères pour une base de trois tables, contre 624 + 911 pour `local_files`. Cela fait environ 0,9 k tokens par appel au modèle quand l'outil est actif ; chaque base ajoute sa ligne de tables et ses `describe`. Le run de bout en bout : 1 047 tokens d'entrée au premier appel (modèle simulé). Le schéma porte `$defs` et `$ref` (paramètres, instructions), comme celui d'`excel`.

**Les performances**, sur le poste de l'essai :

| Mesure | Résultat |
| --- | --- |
| Analyse sqlglot d'une jointure | 0,54 ms |
| Import du paquet (sqlglot compris), au montage | 392 ms |
| SQLite, `query` de 200 lignes sur 100 000 | 2,2 ms en moyenne (4,7 k caractères de résultat) |
| SQLite, `execute` de 20 `INSERT` | 8,1 ms |
| Postgres, premier appel (pool et contrôle du rôle) | 28,6 ms |
| Postgres, appels suivants | 2,4 ms |
| Postgres, 20 appels en parallèle (pool de 4) | 39,6 ms |

Un `SELECT *` sans `LIMIT` sur 100 000 lignes rend 200 lignes et `truncated: true`.

### Tests écrits sur le prototype (148)

| Thème | Tests |
| --- | --- |
| Paramètres | Forme courte et ses défauts ; 15 fautes d'une base ; noms de base, fuseau ; schéma Postgres ; `secret_names` |
| Montage | Fichier SQLite absent, chemin relatif, journal et dossier protégé refusés ; DSN absent, DSN du stockage (nom et valeur) ; déclarations au pire des cas ; description et `settings_lines` sans le DSN |
| Analyse | 40 refus ; toutes les fonctions de `FORBIDDEN`, même permises ; 12 instructions et les droits qu'elles exercent ; casse SQLite, `$1,$2` collés ; `allow_full_table` |
| SQLite | Lecture, troncature, vue permise, refus jusqu'au modèle, colonnes proches ; délai et annulation ; base en WAL ; `execute` tout ou rien, trigger, plafond de lignes, refus, paramètres par numéro |
| Authorizer seul | 13 instructions refusées sans l'analyse devant ; ce qui est accordé passe, dont `count(*)` sur une vue permise |
| Approbation | `assess_call` : lecture libre, lecture approuvée, insertion, mise à jour et son motif, appel invalide |
| Schéma, valeurs | Rendu DDL et `describe`, table demandée non permise ; conversions de rendu ; conversion des paramètres Postgres (24 cas, sans base) ; contrôle du rôle (superutilisateur, BYPASSRLS) |
| Montage et run | Référence dans un agent, réglage commun refusé, extra absent nommé, asyncpg non importé pour SQLite, run lecture puis écriture par le moteur, fermeture avec l'agent, `loom validate`, table des secrets des clients |
| Postgres (intégration) | Valeurs rendues ; 12 conversions et 5 refus de paramètres ; `execute` tout ou rien ; plafond de lignes ; le rôle refuse sans l'analyse (DELETE, table non permise, écriture en lecture seule, DDL, deux instructions) ; droit promis et refusé puis accordé ; droits en trop dits une fois ; rôle inconnu, schéma absent ; délai ; annulation arrêtée sur le serveur ; schéma depuis le catalogue ; colonnes proches ; pool fermé |

**Mutations (14).** Chacune affaiblit une règle de sécurité. Les tests sont relancés avec la mutation, puis le code est rétabli.

| Mutation | Résultat |
| --- | --- |
| Authorizer : lecture sans base toujours permise (`count(*)`) | tombe |
| Analyse : nom qualifié accepté | tombe |
| Analyse : `FORBIDDEN` ignoré | tombe |
| Postgres : `query` hors lecture seule | tombe |
| Postgres : droits promis et refusés tus | tombe |
| SQLite : plafond de lignes modifiées retiré | tombe |
| `assess_call` : approbation jamais demandée | tombe |
| Câblage : outils fournis non fermés | tombe |
| Clients : `dsn_env` hors de la table du client | **passait** : le test redirigeait la variable, qui était donc nommée par ailleurs ; renforcé (client sans redirection), elle tombe |
| Analyse : `$1,$2` non espacés | tombe |
| Analyse : `WHERE` non exigé | tombe |
| Analyse : CTE écrivante permise en `query` | tombe |
| SQLite : trigger non reconnu | tombe |
| Postgres : superutilisateur accepté | **passait** : la CI n'a pas de DSN superutilisateur ; un test unitaire du contrôle du rôle, sans base, la fait tomber |

## Plan de non-régression

| Contrôle | Attendu | Prototype |
| --- | --- | --- |
| Suite complète, tous les extras, Postgres | Aucun test existant cassé | 3 512 passés (3 364 + 148), 73 sautés, comme la référence |
| Suite complète, noyau seul | Rien ne demande sqlglot ni asyncpg | 2 580 passés (identique), 403 sautés (401 + les deux modules `sql`) |
| Contrôle « Le noyau s'importe sans SDK » | Aucun module d'extra installé | Vide, avec sqlglot et asyncpg ajoutés à la liste |
| Corpus de régression (`tests/regression`) | Journaux rejoués à l'identique | Verts, compris dans la suite |
| Agents sans `loom.sql` | Mêmes outils, mêmes requêtes | Aucun champ de requête ni d'événement modifié |
| Outils à soi qui auraient un `aclose` | Toujours pas fermés par loom | `_builtin_closables` ne regarde que les références `loom.<outil>` |
| Clients sans `loom.sql` | Même table de secrets | `_builtin_secrets` ne rend rien ; tests de `test_tenancy.py` inchangés et verts |
| `local_files`, `excel` | Comportement inchangé | Leurs tests passent sans modification |
| `loom schema` | Nouvelle entrée `Builtin_sql` ; `PythonTool` exclut aussi `loom.sql` | Le test du schéma est mis à jour en conséquence : c'est le seul test existant dont l'attendu change |
| `pyright` strict / `ruff` / `lint-imports` | 0 / propre / 5 tenus | 0 / propre / 5 tenus |
| Couverture (seuil 94) | Au-dessus du seuil | 95,22 % (référence 95,21 %) ; le paquet `sql` entre 93,7 et 100 % par module, `sql_params.py` 99,2 % |
| Bornes basses (job `lowest-direct`) | Installable | sqlglot 30.24.0 et asyncpg 0.31.0 sont les versions éprouvées, et les planchers |

Un avertissement pytest est apparu une fois sur quatre passes complètes, sans se reproduire (ni sur la passe suivante, ni en ciblant les modules `sql`) : à surveiller dans la CI.

## Décisions à prendre

Nées du prototype :

| # | Question | Options | Recommandation |
| --- | --- | --- | --- |
| D-22 | Quand vérifier la variable du DSN ? | (a) au montage, comme une clé d'API : `loom validate` dit `la variable CRM_DSN (dsn_env) est absente ou vide`, mais un rejeu sur une autre machine doit définir la variable (sa valeur ne sert pas, aucune connexion n'a lieu) ; (b) au premier appel | (a) : l'erreur tôt vaut plus que le confort du rejeu ailleurs, et c'est déjà la règle des modèles |
| D-23 | `loom validate` doit-il se connecter ? | (a) non (prototype) : les droits du rôle se vérifient au premier appel ; (b) une option `--check-db`, avec la future `loom sql grants` | (a) dans ce lot, (b) au lot de `loom sql grants` |
| D-24 | L'avertissement de sqlglot qui cite le SQL du modèle | (a) le filtrer, ce seul message, sur le logger `sqlglot` (prototype) ; (b) le laisser | (a) : le SQL du modèle n'a rien à faire dans les logs de l'application |
| D-25 | Le `detail` des erreurs Postgres (« Failing row contains … ») rendu au modèle | (a) le garder (prototype) : il aide à corriger, et le rôle peut lire ces données ; (b) le retirer | (a), documenté avec la note RGPD |
| D-26 | Poids du schéma vu par le modèle (2 k caractères) | Raccourcir les descriptions de champs, comme D-H d'`excel` | Oui, après l'essai avec un vrai modèle |

## Plan de mise en œuvre

Un lot par étape, que tu relis et commites :

1. **Socle :**
   - `sql_params.py` ;
   - l'entrée du catalogue, `BuiltinContext.secrets` et `protected_dsns`, `NamesSecrets` ;
   - le câblage (`_protected_dsns`, `_builtin_closables`) et `tenancy/secrets._builtin_secrets` ;
   - l'extra `sql` et le lock ;
   - la liste du contrôle CI ;
   - les trois tests de `test_tools_builtin.py`.
2. **L'analyse et SQLite :** `arguments`, `analysis`, `values`, `schema`, `sqlite`, `tool`, et `tests/unit/test_sql.py`.
3. **Postgres :** `postgres.py` et `tests/integration/test_sql_postgres.py`. Le job complet de la CI a déjà le service Postgres et un rôle `CREATEROLE`.
4. **Documentation :**
   - `fonctions.md` (D13 et point 50) : `loom.sql` ;
   - le guide, au chapitre 32 (`howto/06-outils-fournis.md`) : une section `loom.sql`, avec la recette du rôle dédié (`CREATE ROLE … NOSUPERUSER`, `GRANT` table par table, `REVOKE CREATE ON SCHEMA`, `REVOKE TEMPORARY`) ;
   - la table des extras (`README.md`, `conception.md`, `fonctions.md`) : `sql`, qui apporte `sqlglot` et `asyncpg` ;
   - `SECURITY.md` : les deux barrières ;
   - le `CHANGELOG`, sous 2.0.14.
5. **Exemple et essai réel :** un exemple dans `examples/` (SQLite, sans service), puis un essai avec un vrai modèle et ton agent de test. Puis un exercice sur le site de formation.

## Annexe : expériences reproduites

Toutes ont tourné sur un clone de `1ac7b19` avec la version 2.0.14, dans l'espace de travail cloud de Claude ; rien n'a été écrit dans le dépôt du poste.

| Expérience | Commande, en résumé | Résultat |
| --- | --- | --- |
| Référence | `pytest --cov=loom_ia` avec Postgres ; noyau seul ; `pyright` ; `ruff` ; `lint-imports` | 3 364 / 73 ; 2 580 / 401 ; 95,21 % ; 0 ; propre ; 5 tenus |
| Prototype | Même chose | 3 512 / 73 ; 2 580 / 403 ; 95,22 % ; 0 ; propre ; 5 tenus |
| Fonctions dans sqlglot | Une soixantaine d'appels de fonctions Postgres analysés, typés ou `Anonymous` | Courantes typées sauf `age`, `clock_timestamp`, `jsonb_build_object`, `jsonb_array_elements` ; toutes les dangereuses `Anonymous` |
| `$1,$2` collés | `sqlglot.tokenize("VALUES ($1,$2)", read="postgres")` | « Error tokenizing » ; lu après l'insertion d'une espace |
| Authorizer SQLite | Journal des appels de l'authorizer sur `count(*)`, vues, CTE, triggers, `AUTOINCREMENT` | Formes notées au § Choix techniques ; `count(*)` sur vue corrigé |
| Binding SQLite | `$1` lié par un dict `{"1": v}` ; par une liste | Le dict passe ; la liste est une erreur en Python 3.14 |
| Délai et annulation SQLite | Requête récursive infinie ; `progress_handler` à 0,3 s ; `wait_for` à 0,2 s | `interrupted` ; base libre juste après |
| Rôle Postgres | Rôle `NOSUPERUSER` aux seuls `GRANT` de la config ; puis DSN superutilisateur | Refus attendus ; superutilisateur refusé avec son message |
| Annulation Postgres | `wait_for(… generate_series(1, 1e9) …, 0.5)`, puis `pg_stat_activity` | Aucune requête active du rôle |
| Run de bout en bout | Projet `loom.yaml` (journal SQLite) + agent `loom.sql` sur une base SQLite, modèle simulé : `schema`, `query`, `execute` (`UPDATE` à approbation + `INSERT`) ; `loom validate`, `loom run`, `loom approve`, `loom replay`, `loom inspect` | « monté sans erreur » ; `paused` sur `sql`, base inchangée ; après `approve`, `completed`, devis 1 en `relance`, une relance écrite ; rejeu « Identique », 3 outils servis par le journal, base inchangée |
| Mutations | 14 mutations, `pytest` des modules `sql` et du catalogue | 12 tombent d'emblée ; 2 passaient, tests renforcés, elles tombent |
