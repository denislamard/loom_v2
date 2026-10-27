# Analyse d'impact — lecture des classeurs par `loom.excel` loom_v2

27 octobre 2026 · Denis Lamard

> **Mise à jour du 27/10 : décidé et réalisé.** Les dix décisions suivent les recommandations, avec deux ajustements nés d'une seconde relecture (§ Décisions prises). La réalisation est décrite à la fin (§ Réalisation). Le corps de l'analyse décrit le prototype tel qu'il était au moment de la décision.

## En bref

**Le besoin.** Aujourd'hui, `loom.excel` ne fait qu'écrire. Un agent ne peut donc ni relire le classeur qu'il vient de produire, ni lire celui qu'on lui dépose : un devis, un export de logiciel, un planning. `loom.local_files` ne lit que du texte UTF-8. C'est la décision D-E de l'analyse Excel : « outil de lecture xlsx : plus tard ».

**La proposition, prototypée.**
- **Une action `read` dans `excel`.** `write` reste l'action par défaut, si bien qu'un appel d'aujourd'hui garde le même sens. Sans `sheet`, la lecture rend la liste des onglets et leurs premières lignes ; avec `sheet`, elle rend les cellules d'une plage, en JSON, avec les formules à part.
- **Un `.xlsx` comme source `data:`**, à côté du CSV et du JSON, avec `sheet` pour choisir l'onglet.
- **Rien de nouveau dans la config** : c'est le droit `read` des dossiers de `excel` qui ouvre la lecture.

**Verdict : faisable, mais pas en l'état.** Le prototype marche. Il touche 4 fichiers de `src/` (+742 / −9 lignes) et ajoute 46 cas de test. La suite complète est verte (3 522 tests), pyright strict est à 0, ruff est propre et les 5 contrats d'import tiennent. Trois risques restent ouverts. Les deux premiers relèvent d'une décision ; le troisième est un effet de bord à annoncer.

1. **Le coût d'ouverture.** Même en lecture seule, `load_workbook` charge toute la table des textes partagés, et parcourt en entier une feuille qui n'annonce pas sa dimension. Il le fait avant de rendre la main, donc hors de toute échéance.
   - Un classeur de **19,5 Mo**, sous toutes les bornes, coûte **5,7 s et 269 Mo par ouverture**.
   - Il en faut deux quand la plage a des formules : **12,0 s et 473 Mo** pour lire 5 lignes.
   - Il faut une borne dédiée (D-2).
2. **Un agent qui ne fait que lire est impossible.** `ExcelParams` exige un dossier avec `write` (D-4).
3. **Toute requête d'un agent qui a `loom.excel` change.** Le schéma de l'outil grossit (environ 400 tokens de plus par appel de modèle). Les runs faits avant ne se rejouent plus à l'identique, et le cache de prompt repart de zéro une fois.

**Vingt pièges trouvés : onze en mesurant, neuf par une relecture indépendante du prototype.** Dix-sept sont corrigés dans le prototype et testés. Deux ne sont que dits dans le résultat : la formule sans valeur, et le format non lu. Un reste ouvert : le coût d'ouverture, D-2. Les plus graves :
- **Bombe de décompression.** En mode normal, openpyxl bloque **plus de 120 s** sur un fichier de 762 Ko qui se décompresse en 312 Mo. Corrigé : lecture seule, et contrôle du zip avant openpyxl.
- **Dimension fausse.** Un classeur qui annonce une dimension `A1` est **tronqué sans rien dire** en lecture seule. Corrigé.
- **Résultat sans borne.** Un fichier de 423 Ko produisait **200 millions de caractères**. Corrigé : budget de 40 000 caractères, et la coupe est dite.
- **Exceptions qui fuient.** Une cellule corrompue, un onglet graphique pris comme source ou un classeur sans onglet levaient une erreur interne au lieu d'un message pour le modèle. Corrigé : toute faute du fichier devient une `ToolError`.
- **Fil de calcul non annulable.** Le délai de 30 s du moteur n'arrête pas le fil qui lit. Corrigé pour le parcours des lignes par une échéance interne, mais pas pour l'ouverture (risque 1).

## Périmètre et état de référence

L'analyse part du commit `984523e` de mon clone : `ab42377` plus les skills d'outils. Ton dépôt a avancé pendant l'analyse (`536d83c` « add skill », puis `9992298` « pubish v 2.0.14 », version 2.0.15 en cours). Son `src/` et ses `tests/` sont **identiques octet pour octet** à `984523e` (même empreinte des deux arbres) : le prototype s'applique tel quel. Le prototype est la branche `proto-read` (commits `873668e`, puis `84caca5` après relecture) de ce clone, dans l'espace de travail cloud de Claude : Python 3.14.6, openpyxl 3.1.5, expat 2.8.1, LibreOffice présent.

**Rien n'a été écrit dans `/home/denis/dev/loom_v2`, à part ce document.**

| Contrôle | Référence (`984523e`) | Prototype (`84caca5`) |
| --- | --- | --- |
| `pytest`, tous les extras, sans `LOOM_TEST_POSTGRES`, `LOOM_TEST_RABBITMQ` ni `LOOM_TEST_REDIS` | 3 476 passés, 203 sautés | 3 522 passés (3 476 + 46 nouveaux), 203 sautés |
| `pyright` strict | 0 | 0 |
| `ruff check`, `ruff format --check` | propres | propres |
| `lint-imports` | 5 contrats tenus | 5 contrats tenus |
| Job CI sans extras, simulé (imports des extras bloqués) | — | `test_excel_read.py` saute (openpyxl absent) |

**Lu :** `tools_builtin/excel/` (`tool.py`, `arguments.py`, `sources.py`, `values.py`, `workbook.py`, `recalc.py`), `tools_builtin/_folders.py`, `excel_params.py`, `sql/arguments.py` (le modèle des actions), `local_files.py` (lecture), `core/ports/tool.py` (`strictest_side_effects`), `replay/variant.py`, `engine/executor.py` (délai des outils), `docs/outil-excel-loom_v2.md` (§ 10 et 11), `docs/outil-excel-analyse.md` (D-A, D-E).

## Analyse fonctionnelle

### Ce que voit l'utilisateur de loom

Rien ne change dans la config. Un dossier qui a `read` peut être lu :

```yaml
- python: loom.excel
  folders:
  - name: workspace
    path: workspace
    rights: [ read, write ]      # read : lire un classeur (et toujours : une source CSV/JSON)
```

`loom validate` montre les mêmes lignes qu'avant. La description de l'outil gagne une ligne :

```text
Lecture (action=read, dans : workspace) : un .xlsx ou .xlsm. Sans 'sheet', la liste des
onglets et leurs premières lignes ; avec 'sheet', les cellules depuis A1 ou depuis 'range',
200 lignes au plus par défaut ('max_rows', jusqu'à 1000). Valeurs en JSON (dates en ISO) ;
'formulas' donne la formule d'une cellule par son adresse ; 'start' est l'adresse de la
1re cellule rendue.
```

### Ce que le modèle envoie

```json
{"action": "read", "path": "workspace/bon.xlsx"}
{"action": "read", "path": "workspace/bon.xlsx", "sheet": "Bon d'intervention"}
{"action": "read", "path": "workspace/export.xlsx", "sheet": "Data", "range": "A200:J400", "max_rows": 200}
{"path": "workspace/copie.xlsx", "sheets": [{"name": "Copie", "columns": [...],
  "data": {"path": "workspace/export.xlsx", "sheet": "Data"}}]}
```

Comme pour `loom.sql`, une clé qui ne sert pas à l'action est refusée en le disant : `sheet ne sert pas à write`, `sheets ne sert pas à read`, `'range' demande 'sheet'`. Une seule exception : `overwrite: false` sur une lecture ne dit rien, et passe.

### Ce que le modèle reçoit

Mesuré sur le vrai `bon_intervention_S-2026-0001_nouveau.xlsx` de `temp/agent`.

- **Aperçu** : 269 caractères, en moins de 0,01 s.

  ```json
  {"path": "d/bon.xlsx",
   "sheets": [{"name": "Bon d'intervention", "state": "visible", "dimension": "A1:B29",
               "preview": [["Rubrique", "Détail"], ["DEMANDE", null], ["Numéro de demande", "S-2026-0001"], …]}],
   "chartsheets": []}
  ```

- **Un onglet** : 29 lignes et 1 167 caractères, en moins de 0,01 s.

  ```json
  {"path": "d/bon.xlsx", "sheet": "Bon d'intervention", "start": "A1",
   "rows": [["Rubrique", "Détail"], ["DEMANDE", null], ["Numéro de demande", "S-2026-0001"], …],
   "row_count": 29, "truncated": false}
  ```

  Avec des formules, le résultat ajoute `"formulas": {"D2": "=B2*1.2", "G2": "{=B2:B3*2} sur G2:G3"}` et une note. Pour un onglet masqué, il ajoute `"state": "hidden"`.

**Valeurs :**
- les dates sont rendues en ISO, `2026-10-28` sans heure et `2026-10-01T09:30:00` avec ;
- une heure est rendue `13:00:00`, une durée `2:30:00` ;
- une erreur Excel garde son texte, `#DIV/0!` ;
- un nombre entier reste entier ;
- un texte est coupé à 1 000 caractères, et la coupe est dite.

Les lignes sont rectangulaires, de la largeur de la colonne non vide la plus à droite, sans les lignes vides de la fin.

**Coupe.** Une lecture s'arrête à `max_rows` (200 par défaut, 1 000 au plus) ou à **40 000 caractères** de cellules, sous le seuil de déport par défaut (50 000). Le résultat donne alors `"truncated": true` et `"next_start": "A201"`, la première ligne non parcourue, même après des lignes vides. Au-delà de 200 colonnes, les colonnes de droite sont coupées, et c'est dit.

**Notes :**
- `N formule(s) sans valeur calculée : le classeur n'a pas été recalculé depuis son écriture (Excel le fera à l'ouverture).`
- `Lecture coupée par 200 lignes : la suite commence en A201 (range, max_rows jusqu'à 1000).` ou `… par la taille du résultat (40000 caractères) …`
- `Colonnes coupées après GR (200 au plus) : demander les suivantes avec range.`
- `N texte(s) coupé(s) à 1 000 caractères.`

**Une source `.xlsx`** passe ses valeurs entières, sans coupe à 1 000 caractères. Une formule sans valeur en cache y est **refusée**, plutôt que lue comme une cellule vide. Des en-têtes en double sont refusés.

### Ce que l'outil refuse, et comment il le dit

| Cas | Message |
| --- | --- |
| `.xls`, `.xlsb`, `.ods` | `format Excel 97-2003 (.xls) non lu — l'enregistrer en .xlsx depuis Excel ou LibreOffice.` |
| `.csv` ou autre en lecture | `seuls les classeurs .xlsx et .xlsm sont lus.` |
| Dossier sans `read` | le refus de droit de `_folders`, comme pour une source |
| Fichier vide, CSV renommé, classeur protégé par mot de passe | `pas un classeur .xlsx lisible (fichier vide, renommé, ou protégé par mot de passe).` |
| Zip sans `xl/workbook.xml` | `pas un classeur Excel (xl/workbook.xml absent).` |
| Plus de 200 Mo une fois décompressé | `312016642 octets une fois décompressé, au-delà de 200000000.` |
| Entités XML (« billion laughs », entité externe) | `XML refusé (limit on input amplification factor …)` / `XML refusé (undefined entity &x; …)` |
| Onglet absent / graphique | `onglet 'Absent' introuvable (onglets : Données, Caché).` / `'Graphique' est un onglet graphique, sans cellules.` |
| Plage illisible ou hors de la feuille | `1A:B : plage illisible (A1:F50, A:C, 2:10).` / `A0 : une ligne va de 1 à 1048576.` / `XFE1 : une colonne va de A à XFD.` (une plage à l'envers, `C3:B2`, est remise dans l'ordre, comme Excel) |
| Cellule corrompue (nombre illisible, date invalide, index de texte hors de la table, CRC faux) | `classeur illisible (ValueError: …)` |
| Source : formule sans valeur, en-têtes en double, classeur sans onglet | `1 formule(s) sans valeur calculée dans la source ; l'ouvrir et l'enregistrer dans Excel ou LibreOffice, ou la donner en CSV.` / `en-têtes en double : Libellé.` / `le classeur n'a aucun onglet de cellules.` |
| Lecture trop longue | `lecture arrêtée après 20 s ; viser une plage plus proche du début, ou un classeur plus petit.` |
| `sheet` sur une source CSV | `'sheet' ne sert qu'à un .xlsx`, avant d'ouvrir le fichier |

## Analyse technique

### Modifications, mesurées sur le prototype

| Fichier | Lignes | Ce qui change |
| --- | --- | --- |
| `excel/reader.py` | +529 (nouveau) | contrôle du suffixe et du zip ; ouverture en lecture seule ; toute faute du fichier en `ToolError` ; aperçu borné ; plage remise en ordre et contrôlée ; lecture d'une plage (une passe, seconde passe pour les valeurs seulement s'il y a des formules), budget de taille, colonnes coupées dites ; échéance ; conversion JSON ; un onglet comme source, sans coupe, formules sans valeur refusées |
| `excel/arguments.py` | +105 / −5 | `ExcelCall` (ce que voit le modèle, à plat, avec `action`), `ReadArguments`, `DataSource.sheet` et son contrôle ; `READ_ROWS`, `MAX_READ_ROWS` ; `parse` rend une écriture ou une lecture |
| `excel/tool.py` | +97 / −1 | `assess_call` et `invoke` aiguillent ; `_workbook`, `_assess_read`, `_perform_read` (`next_start`, notes) ; ligne de lecture dans la description |
| `excel/sources.py` | +11 / −3 | `.xlsx` et `.xlsm` comme sources, par `reader.records` (import tardif) |
| `tests/unit/test_excel.py` | +1 / −1 | le message d'une source refusée cite `.xlsx` |
| `tests/unit/test_excel_read.py` | +620 (nouveau) | 33 tests, 46 cas |

Ne changent pas : `_folders.py`, `excel_params.py`, l'écriture (`workbook.py`, `values.py`, `formulas.py`, `recalc.py`), le moteur, le journal, le rejeu, les dépendances (pas de `defusedxml`, pas de nouvelle bibliothèque).

### Choix techniques et leurs raisons

| Choix | Raison |
| --- | --- |
| Action `read` dans `excel`, `write` par défaut | Une seule déclaration de dossiers, le droit `read` existe déjà, et un appel d'aujourd'hui garde son sens. Modèle repris de `loom.sql` (actions à plat, clés refusées hors de leur action) |
| openpyxl **toujours** en lecture seule | Le mode normal charge tout : 11 s et 456 Mo pour 100 000 lignes, plus de 120 s pour une bombe de 762 Ko. La lecture seule s'arrête à la borne : 0,01 s pour 200 lignes de la même bombe |
| Contrôle du zip avant openpyxl | Taille décompressée ≤ 200 Mo (10 × la borne de 20 Mo des sources), 10 000 membres au plus, `xl/workbook.xml` présent. **Pas de borne sur le taux de compression** : un premier essai à 100 refusait une colonne de textes identiques, compressée 163 fois sans être une bombe. La taille suffit : la bombe de 762 Ko dépasse 312 Mo |
| Toute exception du fichier devient une `ToolError` | openpyxl lève `ValueError`, `IndexError`, `BadZipFile`, `AttributeError`, ou `XMLSyntaxError` avec lxml : sans cela, l'outil sortait en erreur interne, trace aux journaux, sans message utilisable par le modèle |
| Budget de 40 000 caractères par lecture | `max_rows` ne borne pas la taille : 1 000 lignes × 200 colonnes de textes longs. Le budget garde le résultat sous le seuil de déport |
| Formule repérée par le type de la cellule (`f`) | Un texte saisi « =pas une formule » n'est pas une formule |
| `reset_dimensions()` avant de lire | La lecture seule croit la dimension déclarée : un classeur qui annonce `A1` ne rendait que A1 |
| Lecture depuis la ligne 1, avec une échéance (20 s) vérifiée toutes les 1 000 lignes | `asyncio.to_thread` ne s'annule pas, et openpyxl parcourt les lignes avant `min_row` sans rendre la main (il ne fait que les sauter). Compter soi-même permet de s'arrêter, y compris pendant le saut |
| Seconde passe (`data_only`) seulement s'il y a des formules | Les valeurs en cache demandent une seconde ouverture. Sans formule, une passe suffit : la fin d'un onglet de 1 048 576 lignes passe de 10,6 s à 5,5 s |
| Formules à part, par adresse | Les lignes restent des valeurs ; le modèle cite une cellule (`D2`) comme dans Excel |
| Import tardif du lecteur | Le module n'est chargé que pour une lecture ou une source `.xlsx` ; le noyau ne change pas |
| Pas de `defusedxml` | expat 2.8.1 (Python 3.14) refuse l'amplification d'entités et ne charge pas d'entité externe : mesuré. Mais cela dépend de l'expat lié à Python, et openpyxl lit `workbook.xml` et `styles.xml` avec lxml quand lxml est installé (D-6) |

## Bugs et pièges trouvés

Les essais sont reproduits en annexe ; les pièges 11 à 19 viennent de la relecture indépendante du prototype, qui a ses propres scripts d'essai. Statut : **corrigé** dans le prototype, **dit** (le résultat le signale) ou **ouvert** (décision à prendre).

| # | Constat | Mesure | Statut |
| --- | --- | --- | --- |
| 1 | **Bombe de décompression** : en mode normal, openpyxl charge tout | 762 Ko → 312 Mo de XML : tué au bout de 120 s ; 100 000 lignes : 456 Mo | corrigé : lecture seule, contrôle du zip (refus en 0,00 s) |
| 2 | **Dimension fausse** : la lecture seule tronque sans rien dire | `<dimension ref="A1"/>` sur A1:D5 : 1 ligne rendue au lieu de 5 | corrigé : `reset_dimensions()` ; test |
| 3 | **Formule matricielle** : openpyxl rend un objet `ArrayFormula`, que le JSON ne sait pas écrire | — | corrigé : `{=B2:B3*2} sur G2:G3` |
| 4 | **Heure et durée** : `datetime.time`, `timedelta` | — | corrigé : `13:00:00`, `2:30:00` |
| 5 | **Fil non annulable** : le délai de l'outil (30 s) rend la main au moteur, mais le fil continue de lire | — | corrigé pour le parcours : échéance interne de 20 s, y compris pendant le saut des lignes, dans l'aperçu et les sources ; **ouvert** pour l'ouverture (n° 9) |
| 6 | **Faux « fin de lecture »** : une donnée après des lignes vides passait pour « pas de suite » | A1, A2, puis A50 : `truncated` faux | corrigé : toute la fin est parcourue ; test |
| 7 | **`sheet` sur une source CSV** : contrôlé après la lecture du fichier | message « fichier introuvable » au lieu de la vraie faute | corrigé : contrôle dans les arguments |
| 8 | **Formules sans valeur** : un classeur écrit par openpyxl, donc par `excel` lui-même, n'a pas de valeur en cache | relecture de `excel` : `B4` (=SUM) vaut `null` | dit : note dans le résultat (D-5) |
| 9 | **Coût d'ouverture** : `load_workbook` charge tous les textes partagés, et parcourt en entier une feuille sans `<dimension>`, hors de toute échéance | 19,5 Mo (119 Mo de textes) : 5,7 s et 269 Mo par ouverture, 12,0 s et 473 Mo avec la seconde passe des formules ; trois feuilles sans dimension (15,7 Mo) : 8,6 s pour 3 lignes (relecteur) | **ouvert** (D-2) |
| 10 | **Formats non lus** : `.xls`, `.xlsb`, `.ods` donnaient « pas un zip » | — | dit : message qui dit quoi faire |
| 11 | **Exceptions qui fuient** (relecture) : `<v>abc</v>`, date invalide, index de texte hors de la table, CRC faux, `<dimension ref="1:5"/>`, onglet graphique ou classeur sans onglet comme source, lxml | erreur interne de l'outil au lieu d'un message | corrigé : `_guarded` ; tests |
| 12 | **Résultat sans borne** (relecture) | 423 Ko → 200,8 millions de caractères en 0,78 s | corrigé : budget de 40 000 caractères ; aperçu borné à 20 colonnes et 100 caractères par cellule ; test |
| 13 | **Colonnes au-delà de 200 coupées sans rien dire** (relecture) | 250 colonnes : 200 rendues, `truncated` faux, une ligne perdue | corrigé : note « Colonnes coupées après GR » ; test |
| 14 | **Source `.xlsx` qui perd des données** (relecture) : formules sans valeur lues comme vides, textes coupés à 1 000 | `warnings: []` | corrigé : valeurs entières, formule sans valeur refusée, en-têtes en double refusés ; test |
| 15 | **Plages anormales** (relecture) : `C5:A1` vide, `A0` lu comme `A1`, `XFE1` accepté | résultats faux sans erreur | corrigé : remise en ordre, bornes refusées ; test |
| 16 | **Formules sans valeur effacées des lignes** (relecture) | `range="D5"` : `rows: []` mais `formulas: {D5: …}` | corrigé : la coupe suit ce qui est écrit ; test |
| 17 | **Suite mal comptée** (relecture) : après des lignes vides, « coupée à 3 lignes » au lieu de 5 parcourues | le modèle repartait trop haut | corrigé : `next_start` ; test |
| 18 | **Texte « =… » pris pour une formule** (relecture) | — | corrigé : type de cellule ; test |
| 19 | **Infini** (`1E+999`) devenu `null` muet (relecture) | — | corrigé : `"inf"` ; test |
| 20 | **Taux de compression** : la borne à 100 de mon premier essai refusait un classeur légitime | colonne de textes identiques : 163 fois | corrigé : borne retirée, la taille décompressée suffit |

## Effets de bord sur le reste de loom

### 1. Toutes les requêtes d'un agent qui a `loom.excel` changent

Le schéma et la description de l'outil grossissent pour tous les agents qui ont `loom.excel`, qu'ils lisent ou non. Mesuré avec un dossier :
- la description passe de 1 836 à 2 252 caractères ;
- le schéma passe de 2 807 à 3 988 caractères ;
- l'ensemble fait environ 400 tokens de plus, envoyés avec chaque appel de modèle.

Le schéma perd aussi une indication : `sheets` n'est plus requis (`required` passe de `[path, sheets]` à `[path]`), et `sheets` comme `overwrite` acceptent `null`. Un oubli de `sheets` en écriture n'est donc plus signalé par le schéma, mais à l'exécution (« write attend 'sheets' »).

Conséquences, comme pour un skill :
- **Rejeu.** Un run fait avant la mise à jour ne se rejoue plus à l'identique : « ce qui diffère : les outils proposés ».
- **Cache.** Le cache de prompt de chaque agent concerné repart de zéro une fois.

À annoncer dans le CHANGELOG (⚠).

### 2. Le droit `read` des dossiers de `excel` s'élargit — ⚠

Jusqu'ici, `read` sur un dossier de `excel` ne servait qu'aux sources `data:` (CSV, JSON), et l'outil ne rendait jamais leur contenu au modèle (« jamais le contenu »). Avec la lecture, **tout `.xlsx` d'un dossier `read` devient lisible par le modèle**. Une config écrite pour alimenter des classeurs donne donc désormais à lire des classeurs qui peuvent contenir des données que l'auteur ne pensait pas exposer. Le changement est à dire en ⚠. Une autre voie serait un droit distinct (D-8).

### 3. Les onglets masqués se lisent

Un onglet `hidden` ou `veryHidden` est lu comme les autres, et le résultat le dit (`"state": "hidden"`). Un classeur peut cacher là des données techniques ou confidentielles (D-3).

### 4. Rejeu en variante : une lecture n'est jamais exécutée

La déclaration fixe de `excel` vaut le pire de ses appels (`reversible`, ou `irreversible` avec `overwrite`). Le rejeu en variante n'exécute jamais un outil à effets de bord, et `strictest_side_effects` ne peut que durcir la déclaration. Mesuré : une lecture y est jugée `reversible`. Une lecture absente du journal rend donc une erreur au modèle en variante. Le rejeu identique, lui, sert le résultat du journal, comme aujourd'hui. Ce n'est corrigeable qu'en séparant la lecture dans un outil déclaré `none` (D-1).

### 5. Ce qui vise l'outil par son nom vise aussi la lecture

- `tenants[].approvals: {excel: always}` impose une approbation aux lectures aussi ;
- `tools_deny: [excel]` retire la lecture avec l'écriture ;
- une politique `before_tool` sur `excel` voit les lectures, et doit regarder `action` ;
- une référence d'agent qui fixe `approval` ou `side_effects` sur l'outil l'impose aussi aux lectures (`ConfiguredTool.assess_call`). Pour `loom.excel`, ces clés sont aujourd'hui refusées sur la référence : ce cas ne concerne donc qu'une config construite en Python.

C'est cohérent avec `loom.sql` (un outil, plusieurs actions), mais à documenter.

### 6. Déport des gros résultats

Avant le budget, 1 000 lignes de 10 colonnes faisaient 124 127 caractères, au-delà du seuil de déport par défaut (50 000). Sans stockage d'artefacts, ce qui est le cas de `temp/agent`, un tel résultat est **tronqué**, pas déporté.

Avec le budget de 40 000 caractères, la même demande rend 375 lignes (46 348 caractères, formules comprises) et donne la suite. Le seuil n'est plus atteint, sauf réglage `offload_over` plus bas sur l'outil.

### 7. Délai et mémoire partagés

Une lecture tient un fil du pool de `asyncio.to_thread`, comme une écriture. Le pire mesuré dans les bornes est 12,0 s et 473 Mo (piège 9). Plusieurs lectures simultanées de gros classeurs se cumulent, d'où D-2.

### 8. Ce qui ne change pas

- Le noyau sans extras : `reader` n'est importé que pour une lecture ; `lint-imports` tient.
- L'écriture : mêmes classeurs, à l'octet près (`test_same_description_same_file` passe).
- `_folders` : chemins, liens et dossiers protégés, comme pour une source.
- `assess_call` d'une écriture : inchangé. Une lecture vaut `none`, idempotente, avec une approbation si le dossier la demande pour `read`.
- Un seul test existant change : le message d'une source refusée cite maintenant `.xlsx`.

### 9. À suivre hors du code

- **Le skill `excel.md` de `temp/agent`** ne parle que d'écriture : y ajouter quand relire (vérifier un classeur écrit, lire un fichier déposé).
- **`loom.local_files` sur un `.xlsx`** répond « n'est pas un fichier texte UTF-8 ». Il pourrait renvoyer vers `excel` avec `action=read`.

## Mesures

Fichiers produits par openpyxl ; temps à travers l'outil (`invoke`), dans l'espace cloud.

| Classeur | Taille | Appel | Temps | Résultat |
| --- | --- | --- | --- | --- |
| 10 000 lignes × 10 colonnes | 0,46 Mo | aperçu | 0,14 s | 599 car. |
| 100 000 lignes × 10 colonnes (avec formules) | 4,6 Mo | aperçu | 1,64 s | 600 car. |
| idem | | 200 lignes | 2,81 s | 24 617 car., coupé |
| idem | | 1 000 lignes demandées | 2,55 s | 375 lignes, 46 348 car., coupé au budget |
| idem | | `A99990:J100010` | 12,99 s | 12 lignes (deux passes, formules) |
| 1 048 576 lignes, sans formule | 5,2 Mo (51 Mo décompressés) | dernières lignes | 5,56 s | 7 lignes |
| bombe 762 Ko → 312 Mo | 0,76 Mo | aperçu | 0,00 s | refus (taille décompressée) |
| 200 000 lignes vides stylées | 1,0 Mo | 200 lignes | 2,21 s | 5 lignes |
| textes partagés : 950 000 textes uniques | 19,5 Mo (119 Mo de textes) | 5 lignes | **12,0 s** (2 ouvertures de 5,7 s) | 473 Mo de mémoire au plus (269 Mo pour une ouverture) |
| `bon_intervention_S-2026-0001_nouveau.xlsx` | 6 Ko | onglet | 0,01 s | 29 lignes, 1 167 car. |

## Tests écrits sur le prototype (33 tests, 46 cas)

- **Arguments** : clés par action (5 cas) ; `overwrite: false` sur une lecture ; description et schéma ; description sans dossier `read`.
- **Droits** : `read` exigé ; `assess_call` d'une lecture (sans effet, approbation seulement si le dossier la demande) ; formats non lus (4 cas).
- **Résultat** :
  - aperçu (onglets, état, graphiques) ;
  - valeurs JSON (dates, heure, durée, erreur, texte coupé, formules simple et matricielle, note sans valeur) ;
  - plage et `start` ; plage illisible ;
  - coupe à 200 lignes et suite par `range` ;
  - données après des lignes vides ; plage après des lignes vides ;
  - onglet masqué ; onglet absent ou graphique (2 cas) ;
  - dimension fausse ;
  - relecture d'un classeur écrit par `excel`.
- **Source `.xlsx`** : un onglet alimente un nouveau classeur ; `sheet` refusé sur un CSV.
- **Sécurité** :
  - « billion laughs » et entité externe (2 cas) ;
  - bombe de décompression ; taille décompressée bornée ;
  - fichier vide et CSV renommé (2 cas) ; zip sans classeur ;
  - échéance.

- **Relecture** (un test par défaut corrigé) : cellule corrompue (2 cas, aperçu et onglet) ; onglet graphique comme source ; budget de taille ; colonnes au-delà de 200 ; source (texte entier, formule sans valeur, en-têtes en double) ; plages remises en ordre ou refusées (3 cas) ; formule sans valeur gardée ; `next_start` après des lignes vides ; texte « =… » et infini.

**À ajouter avant fusion :** la borne d'ouverture (D-2), un `ExcelParams` sans dossier `write` (D-4), le contrôle d'expat et de lxml (D-6), un essai de bout en bout par `loom run` avec un vrai modèle.

## Décisions à prendre

| # | Question | Options | Recommandation |
| --- | --- | --- | --- |
| D-1 | Forme | (a) action `read` dans `excel` ; (b) outil séparé `loom.excel_read`, déclaré `side_effects: none` | (a), prototypée : une déclaration de dossiers, et le modèle de `loom.sql`. (b) seulement si le rejeu en variante des lectures compte (effet de bord 4) |
| D-2 | Coût d'ouverture (piège 9) | (a) bornes dédiées : `xl/sharedStrings.xml` décompressé, et XML de toute feuille sans `<dimension>` ; (b) baisser la borne générale (200 Mo) ; (c) une seule ouverture, en lisant `<f>` et `<v>` dans le même parcours (lecteur XML maison pour les feuilles) ; (d) accepter | (a) maintenant, vers 40 Mo chacune : environ 2 s et 110 Mo par ouverture, estimés par proportion depuis la mesure (119 Mo → 5,7 s, 269 Mo) ; message clair. (c) plus tard s'il le faut : elle divise par deux le cas avec formules, mais c'est du code de parseur à maintenir |
| D-3 | Onglets masqués | (a) les lire et le dire (prototype) ; (b) refuser `veryHidden` ; (c) refuser tout onglet masqué | (a) : un masquage n'est pas une protection, et la personne a déposé le fichier ; l'état est dans le résultat |
| D-4 | Agent qui ne fait que lire | (a) `ExcelParams` exige `write` ou `read` ; (b) garder `write` obligatoire | (a) : quelques lignes ; la description dit déjà où lire et où écrire |
| D-5 | Formules sans valeur | (a) le dire (prototype) ; (b) avec `recalc`, faire recalculer une copie par LibreOffice et rendre les valeurs | (a) maintenant ; (b) en option plus tard (1 à 2 s de plus par lecture) |
| D-6 | Entités XML | (a) contrôle au montage : `pyexpat.version_info` ≥ (2, 4, 0), sinon la lecture est refusée ; et `OPENPYXL_LXML=False` (ou lxml traité de même) ; (b) `defusedxml` dans l'extra `office` ; (c) rien | (a) : aucune dépendance ; le Python de cet espace embarque expat 2.8.1, mais une distribution peut lier Python à l'expat du système, éventuellement plus ancien |
| D-7 | Plafond de `max_rows` et budget | 1 000 lignes et 40 000 caractères (prototype) ; ou un budget réglable | Garder le prototype : le budget tient le résultat sous le seuil de déport quelle que soit la largeur, et `next_start` donne la suite |
| D-8 | Droit de lecture | (a) `read` (prototype) ; (b) un droit `inspect` distinct de la lecture des sources | (a), avec ⚠ au CHANGELOG ; (b) alourdit la config pour un cas rare |
| D-9 | Échéance | (a) 20 s fixes ; (b) liée au `timeout` de l'outil | (a) : l'outil ne connaît pas le délai que la config lui donne |
| D-10 | Version | 2.0.15 (non publiée, ouverte le 27/10) ou suivante | 2.0.15 |

## Décisions prises (27/10)

| # | Décision |
| --- | --- |
| D-1 | Action `read` dans `excel`, `write` par défaut |
| D-2 | Bornes sur ce qu'openpyxl lit en entier avant de rendre la main. **Ajustée** après la seconde relecture : une règle unique, indépendante des noms (openpyxl suit les rels et `[Content_Types].xml`, pas les chemins). Tout membre de plus de **20 Mo** décompressés est refusé, sauf une feuille qui annonce sa dimension avant ses données, reconnue par le parseur (un `<!--<dimension-->` ne trompe pas, un `<x:dimension>` préfixé compte). La borne de 40 Mo sur les seuls textes partagés et feuilles ne voyait pas les styles : 46 Mo de styles coûtaient 14,1 s et 782 Mo |
| D-3 | Onglets masqués lus, avec leur état |
| D-4 | `loom.excel` sans dossier `write` permis. Sa description ne porte alors que la lecture, et l'outil se déclare `side_effects: none` : ses lectures se rejouent en variante |
| D-5 | Formules sans valeur : le dire |
| D-6 | Contrôle au montage : expat ≥ 2.4.0 ; avec lxml, lxml ≥ 5.0 et libxml2 ≥ 2.11 ; sinon lecture refusée et dite par `loom validate` |
| D-7 | 1 000 lignes au plus, et budget de 40 000 caractères. **Ajusté** : une ligne qui ferait dépasser le budget n'est pas ajoutée ; une première ligne plus longue est rendue seule |
| D-8 | Droit `read`, avec ⚠ au CHANGELOG |
| D-9 | Échéance fixe de 20 s |
| D-10 | Version 2.0.15 |

## Plan de mise en œuvre

1. Trancher D-1 à D-10.
2. **Code**, à partir du prototype : D-2, D-4, D-6 ; le message de `local_files` pour un `.xlsx`.
3. **Tests** : ceux du prototype, plus la liste « à ajouter ».
4. **Documentation** :
   - `docs/howto/06-outils-fournis.md` : « Lire un classeur », dans `loom.excel` (aperçu, onglet, plage, formules sans valeur, ce qui est refusé) ;
   - `docs/configuration.md` : le droit `read` de `excel` ;
   - `fonctions.md` (D13) ;
   - `CHANGELOG.md`, avec les ⚠ des effets de bord 1 et 2 ;
   - `docs/outil-excel-analyse.md` : D-E réalisée.
5. **Essai réel** : `temp/agent` relit le bon d'intervention qu'il vient d'écrire, puis lit un export déposé dans `workspace`.

## Annexe : expériences reproduites

Toutes dans l'espace de travail cloud de Claude ; rien sur le poste.

**openpyxl seul**, par mode (normal, `data_only`, lecture seule), sur des classeurs fabriqués puis retouchés dans leur zip :

```text
simple / formules        : D2 '=B2*1.2'            ; data_only : D2 None (pas de cache)
cas / lecture seule      : H2 <ArrayFormula …>, I2 time(13,0), J2 timedelta(seconds=9000), K2 '#DIV/0!'
cas                      : onglet 'Caché' state=hidden ; chartsheets ['Graphique'] ; fusion F1:G1 (mode normal seulement)
dimension A1 / lecture seule : max_row=1, 1 ligne lue (5 en mode normal)
partagées (t="shared")   : traduites : D3 '=B3*1.2'
lol (billion laughs)     : ParseError: limit on input amplification factor (from DTD and entities) breached
xxe (file:///etc/hostname): ParseError: undefined entity &xxe;
CSV renommé, fichier vide: BadZipFile: File is not a zip file
macro.xlsm               : lu comme un .xlsx
```

**Performances d'openpyxl seul** (`probe2.py`) :

```text
perf_100000 normal, 200 lignes      : 11,22 s, 436 Mo
perf_100000 lecture seule, 200      :  1,46 s,  36 Mo
bombe 1M lignes normal              : 11,06 s, 522 Mo
bombe 1M lignes lecture seule, 200  :  0,01 s,  36 Mo
bombe_zip (x410) normal             : interrompu à 120 s
bombe_zip lecture seule, tout       : 60,43 s, 1 104 Mo (12 000 000 lignes)
```

**À travers l'outil** : le tableau § Mesures, et :

```text
spec.side_effects = reversible ; variante, effets jugés d'une lecture : reversible
excel description : 1 836 → 2 252 caractères ; schéma : 2 807 → 3 988 caractères
sst.xlsx (19,5 Mo, 119 Mo de textes partagés) : ok 5 lignes, 12,0 s, rss max 473 Mo ; une ouverture seule : 5,73 s, 269 Mo
ExcelParams avec un seul dossier [read] : « au moins un dossier avec le droit 'write' est attendu »
```

## Réalisation (27/10)

Sur le clone, à partir du prototype, sur la branche `excel-read` (commit `3713d1e`). Reportée dans `/home/denis/dev/loom_v2` **sans commit**, sur `9992298` et tes modifications 2.0.15 en cours (CHANGELOG, `pyproject.toml`, `uv.lock`), qui sont gardées.

| Fichier | Lignes | Ce qui change |
| --- | --- | --- |
| `excel/reader.py` | +617 (nouveau) | ce que décrit l'analyse, plus D-2 (`MAX_WHOLE_MEMBER`, `_sized_sheet`), D-6 (`xml_safety`), le contrôle du zip sous `_guarded`, le budget qui n'ajoute pas une ligne de trop |
| `excel/tool.py` | +174 / −32 | lecture, `assess_call` ; description sans le mode d'emploi de l'écriture quand il n'y a pas de dossier `write` ; `side_effects: none` dans ce cas ; contrôle XML au montage ; ligne `lecture des classeurs` de `loom validate` |
| `excel/arguments.py` | +105 / −5 | `ExcelCall`, `ReadArguments`, `DataSource.sheet` |
| `excel/sources.py` | +11 / −3 | `.xlsx` comme source |
| `excel_params.py` | −2 | D-4 : plus de dossier `write` obligatoire |
| `local_files.py` | +6 / −1 | le refus d'un `.xlsx` renvoie vers `excel` |
| `tests/unit/test_excel.py` | +4 / −7 | `loom validate` gagne une ligne ; le cas « sans dossier write » disparaît (D-4) |
| `tests/unit/test_excel_read.py` | +815 (nouveau) | 42 tests, 61 cas |

**Seconde relecture indépendante, de la tranche des décisions.** Ses neuf points sont traités :
- **D-2 contournable** : les styles n'étaient pas bornés, et les chemins se contournaient (voir D-2 ci-dessus).
- **Contrôle du zip hors de la garde** : un membre abîmé (`zlib.error`, membre chiffré) devenait une erreur interne. Le contrôle passe désormais sous `_guarded`.
- **Budget dépassé** par la ligne qui le franchit : elle n'est plus ajoutée.
- **Outil en lecture seule déclaré `reversible`** : il se déclare maintenant `none`.
- **Doc corrigée** :
  - « l'amplification d'entités et les entités externes », et non « des entités XML » : une entité interne simple est développée ;
  - limites de l'aperçu ;
  - borne des 10 000 membres ;
  - `OPENPYXL_LXML=False` ;
  - la ligne de lecture de `loom validate`.
- **Description d'un outil qui ne fait que lire** : environ 1 500 caractères en moins.
- **Tests ajoutés** : source refusée quand la lecture est désactivée, contournements de D-2, membre abîmé, budget, outil sans effet.

**Mesures après D-2.**
- 46 Mo de styles : refusés par le contrôle du zip en 0,004 s, au lieu de 14,1 s et 782 Mo.
- 119 Mo de textes partagés : refusés en 0,001 s, au lieu de 12,0 s et 473 Mo.
- **Pire cas admis** : 19 Mo de styles, soit 5,4 s et 366 Mo par ouverture, deux fois si la plage a des formules. C'est sous l'échéance de 20 s, mais l'ouverture n'est toujours pas interruptible : c'est le **risque résiduel**. Une lecture en un seul passage (D-2, option c) le diviserait par deux.

| Contrôle | Référence (`984523e`, identique à `9992298` pour `src/` et `tests/`) | Réalisation (`3713d1e`) |
| --- | --- | --- |
| `pytest`, tous les extras, sans `LOOM_TEST_POSTGRES`, `LOOM_TEST_RABBITMQ` ni `LOOM_TEST_REDIS` | 3 476 passés, 203 sautés | 3 536 passés (3 476 − 1 cas retiré + 61 nouveaux), 203 sautés |
| `pyright` strict, `ruff check`, `ruff format --check` | propres | propres |
| `lint-imports` | 5 contrats tenus | 5 contrats tenus |
| Job CI sans extras, simulé | — | `test_excel_read.py` saute |

Deux tests d'intégration (`test_memoire.py`) et un test de télémétrie ont échoué une fois chacun pendant qu'un autre calcul tournait à côté. Ils passent seuls et dans une suite complète lancée sans rien en parallèle, sur la référence comme sur la réalisation : ils sont sensibles au temps machine, pas à ce changement.

