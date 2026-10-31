# Analyse d'impact — des graphiques dans les classeurs de `loom.excel` loom_v2

31 octobre 2026 · Denis Lamard

## En bref

`loom.word` et `loom.pdf` savent dessiner des graphiques depuis le 29/10 (`charts: true`). `loom.excel`, non : ses classeurs n'ont que des cellules. L'analyse des graphiques Word/PDF (`outil-word-pdf-graphiques-analyse.md`, D-6) avait renvoyé ce cas à « plus tard, avec sa propre analyse ». La voici, sur un prototype complet.

**Verdict : rien ne bloque.** Un onglet gagne une liste `charts`. Chaque graphique est un **graphique Excel natif lié aux cellules de son onglet**, écrit par `openpyxl.chart` : aucune dépendance de plus, openpyxl est déjà dans l'extra `office`. Le comportement suit celui de `word` et `pdf` : mêmes quatre types, même paramètre `charts` (faux par défaut), même palette, mêmes formats, mêmes refus. Un classeur sans graphique reste identique à l'octet près.

- **Tests :** 3 559 verts sur `tests/unit` avec tous les extras, soit 3 542 plus 17 nouveaux ; 131 sautés, comme avant.
- **Qualité :** pyright strict à 0 sur les fichiers touchés, ruff propre, les 5 contrats d'import tenus.
- **Coût :** environ 400 tokens de plus par appel au modèle (description et schéma), payés seulement par les agents qui posent `charts: true`. Sans ce paramètre, rien ne change pour le modèle.

| Point | Constat | Preuve |
| --- | --- | --- |
| Graphique Excel natif | Barres, barres horizontales, courbe, secteurs ; une partie `xl/charts/chartN.xml` par graphique, ancrée dans `xl/drawings/drawingN.xml` | Démontré (XML relu) |
| Lié aux cellules | Libellés et séries sont des plages de l'onglet (`'Clients'!$B$2:$B$7`), le nom d'une série est la cellule de son en-tête ; aucun nombre recopié | Démontré |
| Sans total ni note | La ligne de total et les lignes `total` / `note` de la fin ne sont pas tracées | Démontré |
| Formules tracées | Une colonne de formules se trace : Excel la calcule à l'ouverture (et LibreOffice) | Démontré (rendu LibreOffice) |
| LibreOffice | Reconnaît les trois classes (`bar`, `circle`, `line`) et les plages | Démontré (conversion en `.ods`, rendus regardés) |
| Reproductible | Deux appels identiques donnent le même fichier | Démontré (empreintes) |
| Sans graphique, rien ne change | Même fichier ; résumé sans clé `charts` | Démontré (empreintes comparées au commit `7e7fe83`) |
| Sans `charts`, rien ne change pour le modèle | Description et schéma identiques à ceux d'avant | Démontré (comparés à l'octet) |

**Trois choses sont à vérifier ou à trancher :**

1. **Excel lui-même (§ À vérifier sur ton poste).** LibreOffice lit les graphiques du prototype, mais Excel applique le schéma DrawingML plus strictement, et rien ici ne peut le vérifier. Le fichier d'essai `essai-graphiques.xlsx` (envoyé dans la conversation du 31/10) contient les quatre types de graphiques.
2. **Le placement (D-3).** À droite des données, les graphiques se voient dès l'ouverture, mais ils entrent dans la zone imprimée : le réglage « une page de large » réduit alors aussi le tableau.
3. **La lecture (D-5).** `action: read` ne voit pas les graphiques : le prototype ne s'en occupe pas.

## Périmètre et état de référence

L'analyse porte sur ton dépôt au commit `7e7fe83` (« bug scale on chart »), arbre propre, identique à `origin/main`. Rien n'a été écrit dans `/home/denis/dev/loom_v2`, à part ce document. Les essais ont tourné sur un clone de cet état, dans l'espace de travail cloud de Claude, en Python 3.14.6, openpyxl 3.1.5, LibreOffice 24.2.7.

| Contrôle sur l'état de référence | Résultat |
| --- | --- |
| `pytest tests/unit`, tous les extras, sans services | 3 542 passés, 131 sautés |
| `pyright` strict sur `tools_builtin/excel` et ses tests | 0 |
| `ruff` / `lint-imports` | propre / 5 tenus |

Dans cet espace, `pyright` signale 21 erreurs dans `tools_builtin/sql/postgres.py`, avant comme après le prototype : elles viennent des stubs d'`asyncpg` installés dans l'espace cloud, pas du code de loom.

## Analyse fonctionnelle

### Ce que le modèle envoie

Une liste `charts` dans un onglet. Chaque graphique nomme ses colonnes par leur en-tête, comme `from_table` dans `word` et `pdf` :

```json
{"name": "Clients",
 "columns": [{"header": "Client"}, {"header": "Total TTC", "type": "montant_eur"}],
 "rows": [{"cells": ["Julien Barre", 18034.17]}, {"cells": ["Chauffage Lemaire Rénovation", 16247.36]}, …],
 "totals": {"Total TTC": "somme"},
 "charts": [
   {"kind": "barres_horizontales", "title": "Total TTC par client", "categories": "Client", "values": ["Total TTC"]},
   {"kind": "secteurs", "title": "Part de chaque client", "categories": "Client", "values": ["Total TTC"]}
 ]}
```

- **`kind`** : `barres` (par défaut), `barres_horizontales`, `courbe`, `secteurs` ; les mêmes que pour `word` et `pdf` (le type `ChartKind` est importé de `_document`).
- **`categories`** : l'en-tête de la colonne des libellés. **`values`** : les en-têtes des colonnes de nombres, une série chacune (6 au plus), d'un même type numérique.
- **`title`** au-dessus du graphique. Pas de `caption` : une légende sous un graphique n'a pas d'équivalent dans une feuille ; une explication va dans une ligne `note`.
- **Pas de données en ligne.** Dans un classeur, les nombres vivent dans des cellules : le modèle les met dans un onglet, et le graphique les cite. C'est la différence de vocabulaire avec `word` et `pdf`, et c'est le modèle qui fait les données lisibles et retouchables.
- **Les lignes tracées** : celles des données, depuis la ligne 2. Une ligne de rôle `total` ou `note` à la fin, et la ligne `totals`, ne le sont pas. Les données d'un graphique se suivent : une ligne de données après un total ou une note est refusée.
- **Fichiers de données** : un onglet rempli par `data` (CSV, JSON, .xlsx) se trace comme les autres. C'est le cas d'une courbe longue, une année au jour le jour par exemple.

### Le rendu

Le style est celui des graphiques de `word` et de `pdf`, repris avec l'API d'openpyxl. La palette commence par le bleu nuit maison. Les graduations s'écrivent en nombres ronds (`5 000 €`). Chaque barre porte une étiquette de valeur quand il n'y a qu'une série et pas plus de 12 barres. La légende n'apparaît qu'avec plusieurs séries, ou des secteurs, et les secteurs portent leurs pourcentages. Les barres horizontales mettent la première catégorie en haut et prennent la hauteur de leurs libellés (6 à 20 cm). Une valeur vide fait un trou dans une courbe.

- **Lié aux cellules.** Changer une cellule change le graphique ; « Sélectionner les données » montre les plages. Le graphique se retouche dans Excel (type, couleurs, titre), et ses nombres suivent la langue du lecteur.
- **Bout de l'axe.** Quand toutes les valeurs sont écrites en clair, l'axe s'arrête sur un nombre rond qui laisse la place des étiquettes (le correctif « bug scale on chart » de Word, repris). Quand une valeur vient d'une formule, elle n'est connue qu'à l'ouverture : Excel choisit l'axe lui-même.
- **Placement** : à droite des données, à une colonne d'écart, depuis la ligne 2. Plusieurs graphiques s'empilent. Chacun mesure 16 cm sur 9.
- **Les axes sont montrés explicitement.** Avec openpyxl 3.1, un graphique sans réglage `delete = False` s'ouvre sans axes. Le prototype règle ce point.

Les rendus LibreOffice des quatre types ont été regardés : ordre, couleurs, étiquettes et formats conformes à ceux de Word.

### Ce que l'outil refuse

Les fautes sont dites ensemble, dans le message « Classeur refusé », avec celles du reste du classeur :

| Cas | Réponse |
| --- | --- |
| Colonne inconnue | « colonne(s) inconnue(s) : TVA (colonnes : Client, Total TTC) » |
| Une colonne citée deux fois (libellés et valeurs, ou deux séries) | « une colonne citée deux fois » |
| Séries non numériques, ou de types différents | « les séries viennent de colonnes de nombres d'un même type (ici : entier, montant_eur) » |
| Aucune ligne de données | « aucune ligne de données, le graphique serait vide » |
| Une ligne de données après un total ou une note | « la ligne 4 suit une ligne de total ou de note — les données d'un graphique se suivent depuis la ligne 2, totaux et notes à la fin » |
| Plus de 50 lignes pour des barres, plus de 1 000 pour une courbe | « 365 lignes, au-delà de 50 pour des barres — regrouper, ou garder les principales » |
| Secteurs : plusieurs séries, plus de 12 parts, une valeur négative, tout à zéro | Refusé, avec quoi faire (« regrouper le reste en « Autres », ou des barres ») |
| Plus de 10 graphiques dans un onglet | Refusé |

Les contrôles des secteurs (négatif, tout à zéro) ne portent que sur les valeurs écrites en clair : la valeur d'une formule n'est pas connue avant l'ouverture.

### Le paramètre `charts`

Repris de D-8 de l'analyse Word/PDF, avec les mêmes raisons :

```yaml
- python: loom.excel
  charts: true          # facultatif, false par défaut
  folders:
  - name: sortie
    path: sortie
    rights: [ write, overwrite ]
```

- **`false` par défaut.** Un agent qui ne le pose pas garde des requêtes identiques à celles d'aujourd'hui : description et schéma à l'octet près, pas de rejeu cassé, pas de cache de prompt réécrit.
- **Le champ** est déclaré dans `ExcelParams` (`charts: bool = False`), à côté de `recalc`.
- **Une liste `charts` envoyée quand même** est refusée par le moteur, qui valide les arguments contre le schéma avant d'appeler l'outil, comme pour `word` et `pdf` ; l'outil la refuse aussi (« Arguments invalides »).
- **`loom validate`** ajoute `graphiques : oui` ou `graphiques : non` aux réglages de l'outil : `fuseau : Europe/Paris · graphiques : non · recalcul : non · lecture des classeurs : oui`.
- **Le résultat** d'une écriture gagne `"charts": N` pour un onglet qui en porte, et seulement pour lui.
- **`recalc: true`** marche avec des graphiques : le recalcul LibreOffice ne regarde que les formules.

### Limites du premier lot

- Un graphique ne cite que les colonnes de son onglet : pas de graphique dans un onglet de synthèse sur les données d'un autre (la description dit de les y mettre).
- Quatre types seulement : ni aires, ni nuage de points, ni barres empilées, ni deux axes.
- Pas de relecture des graphiques (D-5).
- Pas d'onglet graphique (« chartsheet ») : le graphique est dans l'onglet de ses données (D-3).

## Analyse technique

### Modifications, mesurées sur le prototype

| Fichier | Changement |
| --- | --- |
| `excel/charts.py` | Nouveau (391 lignes) : la validation (`check`, `Plotted`), puis le dessin avec `openpyxl.chart` (`draw`) : types, palette, étiquettes, axes, bout de l'axe, ancres |
| `excel/arguments.py` | +47 / −5 : `ChartSpec` ; `ChartedSheetSpec`, l'onglet avec `charts` ; `ExcelChartCall`, l'appel qui le porte ; `parse` et `input_schema` prennent le modèle d'appel ; `SheetSpec.chart_specs()` |
| `excel/workbook.py` | +17 / −2 : `prepare` valide les graphiques de chaque onglet, `build_workbook` les dessine ; `Written.charts` |
| `excel/tool.py` | +39 / −8 : le drapeau `charts` au montage (modèle d'appel, description, `graphiques : oui/non`), `CHART_HELP`, `charts` dans le résumé |
| `excel_params.py` | +4 : le champ `charts: bool = False` (et l'exemple de la docstring) |
| `tests/unit/test_excel.py` | +2 / −1 : `graphiques : non` dans les réglages et dans `loom validate` |
| `tests/unit/test_excel_charts.py` | Nouveau (344 lignes, 17 tests) |

Rien hors de `tools_builtin/excel` et `excel_params.py` : ni le moteur, ni le catalogue (il passe déjà ses paramètres à `build`), ni `pyproject.toml`, ni `word`, ni `pdf`.

### Choix techniques et leurs raisons

**`openpyxl.chart` plutôt que le XML de `word/chart.py`.** Deux façons ont été pesées :

| | API d'openpyxl (prototype) | XML DrawingML de `word/chart.py`, réutilisé |
| --- | --- | --- |
| Emballage du `.xlsx` | openpyxl écrit le dessin, les relations et les types de contenu | À ajouter à la main dans l'archive, après openpyxl |
| Style | Reproduit une troisième fois, avec les constantes de Word | Identique à Word par construction |
| Données | Plages vers les cellules | Plages à paramétrer, et un cache à écrire (inconnu pour les formules) |
| Risque | Faible : une bibliothèque déjà utilisée | Retouche de l'archive, ordre des parties, relations |

L'API d'openpyxl l'emporte : c'est elle qui sait emballer un classeur. Le prix est de reproduire le style, ce que le prototype fait en important les constantes de `word/chart.py` (palette, formats des étiquettes et des axes, seuil des étiquettes, place au bout des barres). Pour la réalisation, ces constantes iraient mieux dans un module commun aux trois outils (D-6).

**Deux schémas, comme pour `word`.** `ExcelCall` reste celui d'aujourd'hui ; `ExcelChartCall` en hérite et élargit `sheets` à `ChartedSheetSpec`. `ChartedSheetSpec` et `ExcelChartCall` n'ont pas de docstring qui partirait dans le schéma ; celle d'`ExcelChartCall` reprend mot pour mot celle d'`ExcelCall`, qui en est la description racine. L'écriture interne garde `SheetSpec` : `chart_specs()` y rend `()`, et les graphiques de `ChartedSheetSpec` sinon.

**Des libellés en texte sont des textes.** `set_categories` d'openpyxl écrit toujours une plage de nombres (`numRef`). Pour une colonne de texte (ou `annee`, `booleen`), le prototype la remplace par une plage de textes (`strRef`). Une colonne de dates ou de nombres garde la plage de nombres, et son format.

**Un classeur sans graphique ne change pas.** Ni dessin, ni relation, ni type de contenu ne s'ajoute sans graphique ; le résumé ne gagne `charts` que là où il y en a. L'empreinte d'un classeur témoin (deux onglets, formules, total, hypothèse) est la même avant et après.

**Mesures.** Le classeur d'essai (deux onglets, quatre graphiques) : 12 Ko et 31 ms, contre 6,3 Ko et 20 ms sans ses graphiques. Description et schéma, en JSON compact, pour un dossier en lecture, écriture et remplacement :

| | Sans `charts` | Avec `charts: true` | Écart |
| --- | --- | --- | --- |
| Description | 2 365 | 3 002 | +637 caractères |
| Schéma | 3 697 | 4 466 | +769 caractères |

Soit environ 400 tokens de plus par appel au modèle, pour un agent qui pose `charts: true`. Sans `charts`, description et schéma sont ceux du commit `7e7fe83`, à l'octet près.

### Tests écrits sur le prototype (17)

| Thème | Tests |
| --- | --- |
| `charts` (4) | Désactivé par défaut (ni champ dans le schéma, ni ligne dans la description, `graphiques : non`) ; refusé par le schéma du moteur et par l'outil sans `charts` ; classeur sans graphique identique, résumé sans `charts` ; `loom validate` affiche `graphiques : oui` |
| Écriture (4) | Graphique lié aux cellules (parties, plages sans la ligne de total, `strRef`, nom de série, sens des barres, palette, étiquettes, bout de l'axe, secteurs, ancres empilées) ; courbe à deux séries avec un trou et barres sur une colonne de formules (pas de bout d'axe imposé) ; même appel, mêmes octets ; courbe de 365 points depuis un CSV, et barres refusées au-delà de 50 |
| Refus (8) | Colonnes inconnues, citées deux fois, non numériques (4 cas) ; types mêlés ; règles des secteurs ; lignes de données qui ne se suivent pas, aucune ligne ; fautes dites ensemble (plus de 10 graphiques et une colonne inconnue) |
| LibreOffice (1) | Conversion en `.ods` : classes `bar`, `circle`, `line`, et plages reconnues |

## Effets de bord sur le reste de loom

1. **Les requêtes ne changent que pour les agents qui posent `charts: true`** : environ 400 tokens par appel. Pour eux, un run fait avant ne se rejoue plus à l'identique (« ce qui diffère : les outils proposés »), et le cache de prompt est réécrit une fois.
2. **`loom validate` change pour tout agent qui a `loom.excel`** : la ligne des réglages gagne `graphiques : non`. C'est un affichage, rien de ce que voit le modèle ; deux tests existants sont ajustés.
3. **Ce qui ne change pas** : les classeurs sans graphique (mêmes octets), le résumé de leurs écritures, la lecture des classeurs, `loom.word`, `loom.pdf`, le moteur, le catalogue, l'extra `office`.

## Plan de non-régression

| Contrôle | Attendu | Prototype |
| --- | --- | --- |
| Suite `tests/unit`, tous les extras | Aucun test existant cassé, hors les deux ajustés | 3 559 passés (3 542 + 17), 131 sautés |
| Classeurs sans graphique | Mêmes octets | Empreintes identiques |
| Outil sans `charts` | Description et schéma d'aujourd'hui | Identiques à l'octet |
| `pyright` / `ruff` / `lint-imports` | 0 / propre / 5 tenus | 0 sur les fichiers touchés / propre / 5 tenus |

## Décisions à prendre

| # | Question | Options | Recommandation |
| --- | --- | --- | --- |
| D-1 | Le rendu | (a) graphique Excel natif par `openpyxl.chart`, lié aux cellules (prototype) ; (b) le XML de `word/chart.py`, ajouté à l'archive | (a) |
| D-2 | Les données | (a) colonnes de l'onglet, par en-tête (prototype) ; (b) y ajouter les données en ligne ; (c) y ajouter un onglet source (`sheet`) | (a) ; (c) plus tard si un onglet de synthèse le demande |
| D-3 | Le placement | (a) à droite des données, empilés (prototype) ; (b) sous le tableau ; (c) un onglet graphique par graphique | (a) : visible à l'ouverture, quel que soit le nombre de lignes ; le prix est un tableau réduit à l'impression |
| D-4 | Les bornes | (a) 50 libellés pour des barres, 12 parts, 1 000 points pour une courbe (prototype) ; (b) 50 partout, comme `word` et `pdf` | (a) : une courbe longue est un cas courant dans un classeur |
| D-5 | La lecture | (a) rien (prototype) ; (b) `action: read` signale les graphiques de chaque onglet (type, titre, plages) | (b), dans un lot à part : la lecture en `read_only` ne les voit pas, il faut lire les parties `xl/drawings` et `xl/charts` |
| D-6 | Le style commun | (a) importer les constantes de `word/chart.py` (prototype) ; (b) les déplacer dans un module commun aux trois outils | (b) à la réalisation ; les octets de Word et du PDF ne changent pas |
| D-7 | Les types | Les 4 de `word` et `pdf` ; ou y ajouter barres empilées et aires | Les 4, comme les documents |
| D-8 | Le paramètre `charts` | Comme `word` et `pdf` (faux par défaut) ; ou toujours actif | Comme `word` et `pdf` |
| D-9 | Version | 2.0.15 (non publiée), ou la suivante | À toi |

**À vérifier sur ton poste :**

- Ouvrir `essai-graphiques.xlsx` **dans Excel** : deux onglets, quatre graphiques (barres horizontales et secteurs dans `Clients`, courbe à deux séries et barres sur une colonne de formules dans `CA mensuel`). Excel doit l'ouvrir sans message de réparation.
- Changer un montant de `Clients` : les deux graphiques doivent suivre.
- Clic droit sur un graphique, « Sélectionner les données » : les plages de l'onglet.

## Hors chantier, vus en passant

1. **Une colonne de montants trop étroite (bug existant).** Un montant entier dans une colonne `montant_eur` (`18200`) donne une colonne trop étroite : LibreOffice affiche `###`. Reproduit sur le commit `7e7fe83`, sans graphique. La largeur se calcule sur la valeur brute (`18,200`, 6 caractères), sans les décimales ni le « € » que le format affiche (`18 200,00 €`, 11). Un montant décimal est juste à la limite. À corriger dans `_shown` (`workbook.py`), qui devrait mesurer le texte affiché selon le type de la colonne.
2. **Étiquettes des barres négatives.** Une barre négative porte son étiquette sur les libellés de l'axe, qui sont placés sur le zéro. Le réglage est sans doute le même dans `word`. Placer les libellés de l'axe en bas (`tickLblPos = low`) le corrige, dans les trois outils.

## Plan de mise en œuvre

1. **Le socle** : le style commun (D-6), `ChartSpec`, `ChartedSheetSpec` et `ExcelChartCall`, le paramètre `charts` et le drapeau au montage, la validation, et leurs tests.
2. **Le dessin** : `excel/charts.py`, l'appel depuis `build_workbook`, le résumé, et leurs tests (dont LibreOffice).
3. **Documentation** : le guide (section `loom.excel`, avec la clé `charts`, et la limite « Ni graphiques… » à retirer), `fonctions.md`, le `CHANGELOG`.
4. **Essai réel** : un classeur avec graphique demandé à ton agent de test, avec `charts: true`.
5. **Plus tard** : la lecture des graphiques (D-5), puis le correctif des largeurs (hors chantier 1), dans leurs propres lots.

Le prototype tient en un seul lot de code, s'il te va mieux qu'en deux.
