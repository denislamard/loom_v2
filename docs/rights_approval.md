# Droits et approbations : séparer `rights` et `approval`

27 octobre 2026 · Denis Lamard

## En bref

**Le problème.** Dans `loom.local_files`, `loom.excel` et `loom.sql`, la clé `rights` est aujourd'hui une table **droit → approbation** :

```yaml
rights: { read: never, write: never, overwrite: never, delete: never }
```

`never` ne veut pas dire « jamais permis » mais « jamais d'approbation » : le droit est accordé, et l'appel passe sans validation humaine. La ligne ci-dessus accorde donc **tout, y compris la suppression, sans rien demander**, alors qu'elle se lit comme « rien n'est permis ». Le contresens a été fait le 27/10, pendant les essais de `examples/agent_sql`. Pour une clé de sécurité, ce n'est pas acceptable.

**La cause.** Une seule clé porte deux informations :
1. quels droits sont accordés : la présence de la clé ;
2. lesquels attendent un humain : la valeur.

Renommer `rights` en `approve` ne suffit pas. La forme courte `approve: [read, write]` se lirait alors « approuver la lecture et l'écriture », alors qu'elle les accorde **sans** approbation. Et un droit absent paraîtrait accordé.

**La proposition : deux clés, une par notion.**

```yaml
rights: [read, write, overwrite, delete]            # ce qui est accordé ; le reste est refusé
approval: { overwrite: always, delete: always }     # facultatif : ce qui attend un humain
```

- **`rights`** est toujours une **liste**, celle des droits accordés.
- **`approval`** est facultatif. C'est une table droit → `never` ou `always`, dont les valeurs sont celles de l'`approval` des outils (`approval: always`). Un droit qui n'y figure pas garde son approbation par défaut.
- Une clé d'`approval` absente de `rights` est une **erreur**.
- L'ancienne table `rights: { … }` reste acceptée pour `loom.local_files` et `loom.excel`, publiés en 2.0.13, avec un **avertissement de dépréciation**. `loom.sql`, non publié, ne connaîtra que la nouvelle forme.

**Coût.** Les deux modules de paramètres, la normalisation, un avertissement, le schéma JSON, l'affichage de `loom validate`, une trentaine de tests à adapter et la documentation. **Le reste du code ne change pas** : la forme interne, droit → approbation, est conservée.

## 1. L'existant (2.0.14, dépôt au commit `22abbdd`)

### 1.1 Où vit la clé

| Outil | Module | Champ | Droits | Défauts (forme courte) | Publié |
|---|---|---|---|---|---|
| `loom.local_files` | `tools_builtin/local_files_params.py` | `FolderSpec.rights` | `read`, `write`, `overwrite`, `delete` | `read`, `write` : `never` ; `overwrite`, `delete` : `always` | 2.0.13 |
| `loom.excel` | `tools_builtin/excel_params.py`, avec le `FolderSpec` de `local_files` | `folders[].rights` | `read`, `write`, `overwrite` (`delete` refusé) | ceux de `local_files` | 2.0.13 |
| `loom.sql` | `tools_builtin/sql_params.py` | `DatabaseSpec.tables[<table>]` (`TableRights`) | `select`, `insert`, `update`, `delete` | `select`, `insert` : `never` ; `update`, `delete` : `always` | non (2.0.14) |

Les deux modules ont chacun leur `_short_form` (une liste devient une table avec les défauts) et leur `_RIGHTS_SCHEMA` (`anyOf` objet | liste).

### 1.2 Qui lit la forme interne

Le reste du code ne voit qu'un `dict[droit, "never" | "always"]` et ne lit que deux choses : **la présence** d'une clé (droit accordé) et **la valeur `"always"`** (approbation) :

- `_folders.py` : `Folder.asking()`, contrôle d'accès `missing`, approbations d'un appel (l. 61, 65, 143, 153, 221) ;
- `local_files.py` (l. 103, 121) ; `excel/tool.py` (l. 45, 55, 180) ; `excel_params.py` (l. 42, 48) ;
- `sql/tool.py` (l. 59, 65, 96, 146) ; `sql/analysis.py` (l. 137 à 142, 218, 370) ; `sql/sqlite.py` (l. 76, 198) ; `sql/postgres.py` (l. 149, 172, 279, 288, 298).

**Conséquence : si la nouvelle syntaxe se traduit dès la validation dans cette même forme interne, aucun de ces fichiers ne change.**

### 1.3 Ce qui s'écrit aujourd'hui avec la table

- `docs/howto/06-outils-fournis.md`, l. 594 et 597 (exemple de `local_files`) ;
- `examples/agent_sql/agents/compta.yaml` et `examples/agent_postgres/agents/compta.yaml`, l. 19 ;
- `CHANGELOG.md`, entrées 2.0.13 (`local_files`) et 2.0.14 (`loom.sql`, exemple `devis: { select: never, update: always }`) ;
- `docs/sql_builtin_tool.md`, décision D-5 ;
- les tests : `tests/unit/test_local_files.py`, `test_excel.py`, `test_sql.py`, `test_tools_builtin.py`, `test_call_assessment.py` et `tests/integration/test_sql_postgres.py`, soit 57 occurrences de `rights` en tout, dont une partie en forme table.

### 1.4 Le vocabulaire existant de l'approbation

Au niveau d'un outil, loom a déjà `approval: never | always | policy` (`core/model/tooling.py`, `type Approval`). Pour `loom.local_files`, `approval` est **refusé sur la référence** (« que les droits expriment »). La nouvelle clé `approval` se place **dans un dossier** ou **dans une table**, jamais sur la référence. La section 2.6 traite le risque de confusion.

## 2. La proposition

### 2.1 `loom.local_files` et `loom.excel`

```yaml
- python: loom.local_files
  folders:
  - name: work                         # rien ne bloque, suppression comprise
    path: workspace
    rights: [read, write, overwrite, delete]
    approval: { overwrite: never, delete: never }
  - name: clients                      # lire librement ; écrire demande l'accord
    path: /home/jean/clients
    rights: [read, write]
    approval: { write: always }
  - name: devis                        # lecture seule
    path: devis
    rights: [read]
```

```yaml
- python: loom.excel
  folders:
  - name: out
    path: workspace
    rights: [write, overwrite]          # remplacer un classeur : approbation par défaut
```

### 2.2 `loom.sql`

Chaque table prend **soit** une liste (forme courte, approbations par défaut), **soit** un objet `{rights, approval}` :

```yaml
- python: loom.sql
  databases:
  - name: crm
    postgres: { dsn_env: CRM_DATABASE_URL }
    tables:
      clients: [select]
      relances: [select, insert]
      devis:
        rights: [select, update]
        approval: { update: always }    # facultatif ici : c'est le défaut
      documents:
        rights: [select, update]
        approval: { update: never }     # modifier sans validation : dit explicitement
```

### 2.3 Sémantique

| Règle | Détail |
|---|---|
| R-1 | `rights` est une liste non vide de droits distincts, pris dans ceux de l'outil. Un droit absent est **refusé**. |
| R-2 | `approval` est facultatif. C'est une table droit → `never` (l'appel passe) ou `always` (l'appel attend une validation humaine). |
| R-3 | Approbation effective d'un droit accordé : la valeur d'`approval` si le droit y figure, sinon son **défaut** (§ 1.1). |
| R-4 | Une clé d'`approval` qui n'est pas dans `rights` est une **erreur** : approuver un droit refusé n'a pas de sens, et c'est le plus souvent une faute de frappe ou un oubli dans `rights`. |
| R-5 | `approval: {}` est permis et vaut l'absence d'`approval`. |
| R-6 | `policy` n'est pas une valeur d'`approval` de droit : ce sont `never` et `always`, comme aujourd'hui. |
| R-7 | Les contraintes existantes ne changent pas : `overwrite` exige `write` (`local_files`, `excel`), `delete` refusé (`excel`), `update` et `delete` exigent `select` (`sql`), au moins un dossier `write` (`excel`). |
| R-8 | La forme interne ne change pas : `dict[droit, "never" \| "always"]`, calculée à la validation à partir de `rights`, d'`approval` et des défauts. |

**Équivalences :**

| Nouvelle écriture | Forme interne |
|---|---|
| `rights: [read]` | `{read: never}` |
| `rights: [read, write, overwrite, delete]` | `{read: never, write: never, overwrite: always, delete: always}` |
| `rights: [read, write, delete]` + `approval: {delete: never}` | `{read: never, write: never, delete: never}` |
| `rights: [read, write]` + `approval: {write: always}` | `{read: never, write: always}` |
| `tables: {devis: [select, update]}` | `{select: never, update: always}` |
| `tables: {devis: {rights: [select, update], approval: {update: never}}}` | `{select: never, update: never}` |

### 2.4 L'ancienne forme (table dans `rights`)

**`loom.local_files` et `loom.excel` (publiés en 2.0.13) :**
- `rights: { read: never, … }` reste accepté et donne la même forme interne qu'aujourd'hui.
- Un avertissement de dépréciation est émis au chargement et dans `loom validate`. Il montre l'écriture équivalente, prête à copier :
  ```
  Agent 'compta', outil loom.local_files, dossier 'work' : 'rights' en table droit → approbation est déprécié (never = sans approbation, pas « refusé »). Écrire :
    rights: [read, write, overwrite, delete]
    approval: { overwrite: never, delete: never }
  ```
  L'écriture proposée ne reprend dans `approval` que les droits dont la valeur diffère du défaut.
- Donner à la fois une table dans `rights` et une clé `approval` est une **erreur**, pour éviter deux sources d'approbation : « 'rights' en table et 'approval' ensemble : donner 'rights' en liste ».
- La forme est retirée à la prochaine version majeure (3.0). L'entrée du `CHANGELOG` le dit.

**`loom.sql` (non publié) :** pas de période de dépréciation. L'ancienne table `devis: { select: never, update: always }` est refusée dès la 2.0.14, avec un message qui montre la nouvelle écriture. Voir la décision D-3.

### 2.5 Messages d'erreur

Ils sont écrits pour la personne qui configure. Chacun nomme l'agent, l'outil, le dossier ou la table, et ce qu'il faut écrire.

| Cas | Message |
|---|---|
| `approval` sur un droit absent | `dossier 'work' : approval 'delete' porte sur un droit non accordé — l'ajouter à 'rights', ou le retirer d'approval` |
| valeur inconnue | `dossier 'work' : approval 'write' vaut 'yes' — attendu : never (sans approbation) ou always (approbation humaine)` |
| droit inconnu dans `approval` | `dossier 'work' : approval 'remove' — droit inconnu (droits : read, write, overwrite, delete)` |
| table et `approval` ensemble | `dossier 'work' : 'rights' en table et 'approval' ensemble — donner 'rights' en liste` |
| `loom.sql`, ancienne table | `base 'crm', table 'devis' : la table droit → approbation n'est plus acceptée — écrire { rights: [select, update], approval: { update: always } }, ou [select, update]` |
| `loom.sql`, objet sans `rights` | `base 'crm', table 'devis' : 'rights' manquant` |

Les messages existants sur `rights` vide, droit en double et `overwrite` sans `write` ne changent pas.

### 2.6 Pourquoi `approval`, et pas `approve` ou d'autres valeurs

- **`approval`** reprend le mot et les valeurs de l'`approval` d'un outil. Qui connaît `approval: always` sur un outil comprend `approval: { delete: always }` sur un dossier.
- **`approve`**, ou des valeurs comme `auto | ask` : un nouveau vocabulaire pour une notion qui existe déjà. Écarté.
- **`approval: [overwrite, delete]`**, une liste des droits à approuver : c'est plus court, mais on ne peut plus écrire `never` pour un droit dont le défaut est `always`, comme `delete` sans validation dans le dossier `work`. Il faudrait une deuxième clé. Écarté.
- **Le risque de confusion** avec `approval` sur la référence, refusé pour `local_files` : les niveaux sont distincts, dans `folders[]` ou `tables.<t>` d'un côté, à la racine de la référence de l'autre. Le message de refus existant au niveau de la référence ajoute : « l'approbation se règle par dossier (folders[].approval) ».

## 3. La réalisation

### 3.1 Normalisation

Dans chaque module de paramètres, un **validateur `mode="before"` du modèle**, `FolderSpec` ou un nouveau `TableSpec`, remplace `_short_form` :

1. il lit `rights` et `approval` dans l'entrée ;
2. si `rights` est une liste, il contrôle R-1 et R-4, puis construit la table interne avec les défauts surchargés par `approval` ;
3. si `rights` est une table (ancienne forme), il contrôle qu'`approval` est absent, garde la table et marque le modèle comme `legacy` ;
4. il rend `{"rights": <table interne>, …}`.

Le champ `rights: dict[droit, approbation]` reste celui que lit le code (§ 1.2). `approval` n'est pas un champ persistant : il est consommé par la normalisation.

Pour `loom.sql`, `tables` devient `dict[nom, TableRights]`, où `TableRights` accepte une liste ou un objet `{rights, approval}`. La normalisation produit la même table interne qu'aujourd'hui. `DatabaseSpec.tables[t]` reste un `dict`, ce qui laisse `sql/*.py` inchangé.

La logique commune (contrôles R-1 à R-6, construction, texte de l'écriture équivalente) va dans **une fonction partagée**, paramétrée par la liste des droits et les défauts. Elle peut vivre dans un petit module `tools_builtin/_rights.py`, léger, Pydantic seulement, que la config importe sans importer les outils. Les deux `_short_form` disparaissent.

### 3.2 Avertissement de dépréciation

- Le modèle garde le marqueur `legacy`, en attribut privé Pydantic, exclu du dump et du hash de la config.
- Une fonction `rights_warnings(config) -> list[str]`, rangée avec `model_warnings`, `storage_warnings` et les autres dans `runtime/wiring.py`, parcourt les références `loom.local_files` et `loom.excel` des agents. Elle rend un avertissement par dossier `legacy`.
- Elle est branchée comme les autres : `announce` au démarrage, et affichée par `loom validate`, à côté de `key_warnings` et `model_warnings` (`access/cli/main.py`, l. 741).
- Un avertissement ne bloque jamais, même avec un profil `prod`.

### 3.3 Schéma JSON (`loom schema`)

- **`rights`**, pour `local_files` et `excel` : `anyOf` liste | objet. L'objet porte `"deprecated": true` et une description qui renvoie à `approval`.
- **`approval`** : `{"type": "object", "propertyNames": {"enum": [<droits>]}, "additionalProperties": {"enum": ["never", "always"]}}`. Sa description : « approbation de chaque droit accordé : never (l'appel passe) ou always (validation humaine) ; un droit absent garde son défaut ».
- **`tables.<t>`**, pour `loom.sql` : `anyOf` liste | `{rights: liste, approval: objet}`, avec `additionalProperties: false`.
- Le schéma ne peut pas exprimer R-4 (une clé d'`approval` doit être dans `rights`). C'est le validateur qui le contrôle, et le message le dit.

### 3.4 `loom validate`

L'affichage actuel des dossiers et des tables (droits et approbations) reste fondé sur la forme interne. Il écrit chaque droit avec son approbation **en clair**, pour que le contresens ne soit plus possible même à la relecture :

```
loom.local_files — dossier work → /home/denis/dev/loom_v2/examples/agent_sql/workspace
  read : accordé, sans approbation
  write : accordé, sans approbation
  overwrite : accordé, sans approbation
  delete : accordé, sans approbation
```

Ce qui est refusé n'est pas listé : la ligne « accordé » suffit à lire les droits. Cela reste à confirmer à la décision D-5.

### 3.5 Ce qui ne change pas

- Le contrôle d'accès, l'évaluation appel par appel (`assess_call`), les effets et l'approbation au moteur.
- La description de l'outil que lit le modèle, qui indique déjà « droits … (approbation) ».
- Le journal et le rejeu. Aucun événement ne porte la config des droits ; le hash de la config change si l'on réécrit une config, comme pour toute réécriture.

## 4. Fichiers touchés

| Fichier | Changement |
|---|---|
| `src/loom_ia/tools_builtin/_rights.py` (nouveau) | normalisation commune, contrôles, écriture équivalente pour l'avertissement |
| `src/loom_ia/tools_builtin/local_files_params.py` | `FolderSpec` : validateur `before`, champ `approval` en entrée, marqueur `legacy`, schéma JSON ; docstring du module |
| `src/loom_ia/tools_builtin/excel_params.py` | docstring du module (exemple) ; les contrôles `delete` et `write` lisent toujours la forme interne |
| `src/loom_ia/tools_builtin/sql_params.py` | `TableRights` : liste ou `{rights, approval}`, refus de l'ancienne table, schéma JSON ; docstring |
| `src/loom_ia/runtime/wiring.py` | `rights_warnings`, branchée avec les autres avertissements |
| `src/loom_ia/access/cli/main.py` | `loom validate` : avertissements, et affichage « accordé, sans approbation » / « accordé, avec approbation » |
| refus d'`approval` sur la référence de `local_files` | complément du message (§ 2.6) |
| `tests/unit/test_local_files.py`, `test_excel.py`, `test_sql.py`, `test_tools_builtin.py`, `test_call_assessment.py`, `tests/integration/test_sql_postgres.py` | passage à la nouvelle forme ; nouveaux cas (§ 5) |
| `docs/howto/06-outils-fournis.md` | sections `local_files`, `excel` et `sql` : nouvelle écriture, tableau des défauts, encadré « never = sans approbation » |
| `CHANGELOG.md` (2.0.14) | ligne ⚠ : nouvelle forme, dépréciation de la table pour `local_files` et `excel`, forme de `loom.sql` |
| `docs/sql_builtin_tool.md` | D-5 révisée, avec renvoi à ce document |
| `docs/fonctions.md`, `README.md` | exemples de droits, s'il y en a |
| `examples/agent_sql/agents/compta.yaml`, `examples/agent_postgres/agents/compta.yaml` | nouvelle écriture, avec le choix fait sur le dossier `work` (§ 6) |

## 5. Tests

### 5.1 Normalisation (paramétrés sur `local_files` et `sql`)

1. Liste seule : défauts appliqués, pour chaque droit.
2. Liste et `approval` partiel : surcharge des seuls droits donnés.
3. `approval` qui met `never` sur un droit dont le défaut est `always` (`delete`, `update`).
4. `approval` qui met `always` sur un droit dont le défaut est `never` (`write`, `select`).
5. `approval: {}` : même résultat que sans `approval`.
6. `approval` sur un droit non accordé : erreur, avec le message du § 2.5.
7. Valeur inconnue (`yes`, `policy`) : erreur.
8. Droit inconnu dans `approval` : erreur.
9. `rights` vide, droit en double, droit inconnu : messages inchangés.
10. Contraintes R-7 inchangées : `overwrite` sans `write`, `delete` dans `excel`, `update` sans `select`.

### 5.2 Ancienne forme

11. `local_files` et `excel` : la table dans `rights` donne la même forme interne qu'en 2.0.13, avec le marqueur `legacy`.
12. Table et `approval` ensemble : erreur.
13. `rights_warnings` : un avertissement par dossier `legacy`, aucun sinon. Le texte contient l'écriture équivalente, qui ne garde dans `approval` que les écarts aux défauts.
14. Aller-retour : l'écriture proposée par l'avertissement, rechargée, redonne exactement la même forme interne.
15. `loom.sql` : l'ancienne table est refusée, et le message contient l'écriture équivalente.

### 5.3 Bout en bout

16. `loom validate` sur une config en ancienne forme : sortie en code 0, avec l'avertissement.
17. `loom validate` : affichage « accordé, sans approbation » et « accordé, avec approbation ».
18. `loom schema` : le schéma accepte les formes nouvelles, marque la table comme dépréciée pour `local_files`, et la refuse pour `sql`.
19. Les tests existants d'accès, d'approbation et d'évaluation (`test_call_assessment.py`, intégration Postgres), réécrits dans la nouvelle forme, passent sans changer leurs attentes. C'est la preuve que la forme interne n'a pas bougé.

### 5.4 Non-régression

- Les mutations de la validation : supprimer le contrôle R-4, inverser le défaut d'un droit, ignorer `approval`. Chacune doit être prise par un test.

## 6. Décisions à prendre

| N° | Question | Options | Recommandation |
|---|---|---|---|
| D-1 | Nom de la clé d'approbation | (a) `approval` ; (b) `approve` ; (c) autres valeurs (`auto` \| `ask`) | **(a)** : le vocabulaire de l'`approval` des outils (§ 2.6) |
| D-2 | Forme d'`approval` | (a) table droit → `never` \| `always` ; (b) liste des droits à approuver | **(a)** : seule à pouvoir lever un défaut `always` |
| D-3 | Ancienne table pour `loom.sql` | (a) refusée dès la 2.0.14 ; (b) dépréciée comme `local_files` | **(a)** : jamais publiée, autant ne pas traîner une forme fautive |
| D-4 | Retrait de l'ancienne table pour `local_files` et `excel` | (a) en 3.0 ; (b) en 2.1 | **(a)** : une version majeure pour un changement qui casse une config |
| D-5 | `loom validate` : lister aussi les droits refusés ? | (a) non, les droits accordés seulement ; (b) oui, « delete : refusé » | **(b)** : c'est plus long, mais on voit d'un coup d'œil ce qui est permis et ce qui ne l'est pas, ce qui est l'objet même de ce changement |
| D-6 | Avertir aussi quand `approval` met `never` sur un droit destructif (`delete`, `overwrite`, `update`, `delete` SQL) ? | (a) non ; (b) oui, une ligne d'information dans `loom validate` | **(b)**, en information et non en avertissement : le choix est légitime, mais il doit se voir |
| D-7 | Le dossier `work` de `examples/*/compta.yaml` | (a) le retirer : Marie n'en a pas l'usage ; (b) `rights: [read]` ; (c) garder tous les droits, écrits explicitement | **à décider par Denis** : la ligne actuelle accorde tout sans approbation, ce qui n'était pas l'intention |

## 7. Migration

| Avant | Après |
|---|---|
| `rights: { read: never, write: never, overwrite: never, delete: never }` | `rights: [read, write, overwrite, delete]` + `approval: { overwrite: never, delete: never }` |
| `rights: { read: never, write: always }` | `rights: [read, write]` + `approval: { write: always }` |
| `rights: { read: never }` | `rights: [read]` |
| `rights: [read, write]` | inchangé |
| `devis: { select: never, update: always }` (`loom.sql`) | `devis: [select, update]` (`update: always` est le défaut) |
| `documents: { select: never, update: never }` (`loom.sql`) | `documents: { rights: [select, update], approval: { update: never } }` |

Pour une config `local_files` ou `excel` en ancienne forme, l'avertissement de `loom validate` donne l'écriture exacte à recopier (§ 2.4). Pour `loom.sql`, c'est le message d'erreur qui la donne.
