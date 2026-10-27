# Analyse d'impact — outil fourni `loom.excel` loom_v2

25 octobre 2026 · Denis Lamard

## En bref

Le but : livrer avec loom-ia un outil qui écrit des classeurs Excel aussi propres que ceux de Claude. La conception est dans `docs/outil-excel-loom_v2.md` ; cette analyse mesure ce qu'il en coûte, sur un prototype complet.

**Verdict : rien ne bloque.** Le changement tient dans deux modules nouveaux (`excel.py`, `excel_params.py`), un extra `office` et trois retouches courtes : le catalogue (+10 lignes), `runtime/wiring.py` (+6) et `local_files.py` (+10). Le moteur, le journal et le schéma de config ne bougent pas, à part la nouvelle référence dans `loom schema`. Un agent qui n'active pas `loom.excel` monte exactement comme avant, et sans l'extra, openpyxl n'est jamais importé.

- **Tests :** 3 249 verts, soit les 3 202 existants plus 47 nouveaux. En noyau seul, 2 580 verts comme la référence, le module d'essai sautant faute de l'extra.
- **Qualité :** pyright strict à 0, ruff propre, les 5 contrats d'import tenus.
- **Rejeu :** un run qui écrit un classeur se rejoue à l'identique.

| Point | Constat | Preuve |
| --- | --- | --- |
| Aucun outil actif par défaut | `loom.excel` ne s'active que par `- python: loom.excel` | Démontré (montage, `loom validate`) |
| Chargement à la demande | Valider une config n'importe ni `excel.py` ni openpyxl | Démontré (sous-process, `sys.modules`) |
| Extra absent | Message au montage, donc dès `loom validate` : `dépendance 'openpyxl' absente (installer l'extra : loom-ia[office])` | Démontré |
| Le modèle ne décide pas du style | Il décrit onglets, colonnes typées, rôles, formules ; l'outil pose police, couleurs, formats, largeurs, volets, filtre | Démontré (classeur relu et rendu, § Le rendu) |
| Mêmes règles de fichiers que `local_files` | Dossiers déclarés, droits, approbation par appel, liens sortants refusés, écriture atomique, `overwrite` | Démontré, par les fonctions de `local_files` elles-mêmes |
| Validation des formules sans dépendance | Fonctions françaises, `;`, onglet inconnu ou mal quoté, fonctions à débordement, références hors feuille : refusés avec le correctif | Démontré (11 cas) |
| Recalcul LibreOffice optionnel | Une formule en erreur fait échouer l'appel et rien n'est écrit ; 4 recalculs parallèles sans conflit | Démontré (LibreOffice 24.2) |
| Fichier reproductible | Même description, même fichier à l'octet près : la reprise d'un appel interrompu ne réécrit rien | Démontré |
| Rejeu | `loom replay` sert le résultat du journal sans réécrire le fichier | « Identique » |

**Trois choses sont à régler avant de fusionner :**

1. **Les accès privés à `local_files` (D-A).** Le prototype se sert de 6 membres privés de `local_files` (trois méthodes, une fonction, deux types), avec 15 `pyright: ignore`. Il faut les rendre publiques ou les sortir dans un module commun.
2. **La taille de `excel.py` (D-I).** Il fait 1 121 lignes, contre 566 pour `local_files.py`. Le découper en sous-paquet le rendrait plus facile à relire.
3. **La documentation et la CI.** Le guide, `fonctions.md`, le README, `conception.md`, le CHANGELOG, et la vérification « noyau sans SDK » de la CI (§ Plan de mise en œuvre).

## Périmètre et état de référence

L'analyse porte sur le commit `fdc6a0a` (« add doc about tool excel »), le même sur le poste et sur `origin/main`, arbre propre. La version est 2.0.13.

Rien n'a été écrit dans `/home/denis/dev/loom_v2`, à part ce document. Les essais ont tourné sur un clone, dans l'espace de travail cloud de Claude, en Python 3.14.6, avec `uv sync --locked --all-extras --group types` et LibreOffice 24.2.7.

**Lu :** `tools_builtin/__init__.py`, `local_files.py`, `local_files_params.py` ; `runtime/wiring.py` (`_tool`, `_builtin_tool`, `_protected_paths`) ; `agents/spec.py` (`_PARAMETERIZED`, `_builtin_reference`) ; `core/ports/tool.py` (`ToolContext`, `CallAssessment`) ; `tests/unit/test_tools_builtin.py`, `test_local_files.py`, `test_dependances.py` ; `.github/workflows/ci.yml` ; `pyproject.toml` ; `docs/tools-builtin-analyse.md` ; `docs/howto/06-outils-fournis.md`.

| Contrôle sur `fdc6a0a`, avant toute modification | Résultat |
| --- | --- |
| `pytest`, tous les extras, sans services | 3 202 passés, 174 sautés |
| `pytest`, noyau seul (`uv sync --locked`) | 2 580 passés, 400 sautés |
| Couverture en branches, sans services | 94,61 % |
| `pyright` strict | 0 erreur |
| `ruff check` / `ruff format --check` | propres |
| `lint-imports` | 5 contrats tenus |

## Ce qui est décidé (25/10)

Les décisions n° 1 à 7 de la note de conception :

| # | Décision |
| --- | --- |
| 1 | Nom : **`loom.excel`**, module `tools_builtin/excel.py` ; le modèle voit `excel` |
| 2 | Extra : **`office`**, qui porte openpyxl |
| 3 | Recalcul : **optionnel, sur l'hôte**, par LibreOffice |
| 4 | Sources : **fichiers CSV / JSON** pour le volume, **lignes en ligne** pour les petits tableaux (hypothèses, saisies, formules), bornées |
| 5 | Conventions : **adaptatives**. Les dates suivent la locale du lecteur (formats intégrés n° 14 et 22), les négatifs prennent le signe moins, le zéro s'affiche 0 |
| 7 | Écrasement : **des droits comme `local_files`**. Les dossiers sont déclarés avec `rights` ; remplacer un classeur exige `overwrite=true` et le droit `overwrite`, avec approbation humaine par défaut |
| — | Démarche : analyse d'impact d'abord, comme pour `tools_builtin` |

Restent ouvertes : n° 6 (style figé ou surchargeable), n° 8 (rejeu) et n° 9 (outil de lecture). Le prototype propose une réponse à chacune (§ Décisions à prendre).

## Analyse fonctionnelle

### Ce que voit l'utilisateur de loom

```yaml
# agents/assistant.yaml
tools:
- python: loom.excel
  folders:
  - name: donnees
    path: donnees
    rights: [read]              # les CSV / JSON que l'outil peut lire
  - name: sortie
    path: sortie
    rights: [write, overwrite]  # overwrite : approbation humaine par défaut
  recalc: true                  # facultatif : vérifier les formules avec LibreOffice
  # soffice: /usr/bin/soffice   # facultatif : sinon cherché dans le PATH
  # recalc_timeout: 60
```

```bash
uv add 'loom-ia[office]'
```

Les dossiers ont **la même forme que ceux de `local_files`**, contrôlés par le même code : chemin absolu ou relatif au `loom.yaml`, refus des dossiers qui exposent loom, noms uniques. Deux règles s'y ajoutent. Le droit `delete` est refusé, puisque l'outil ne supprime rien. Au moins un dossier doit avoir `write`.

`approval`, `side_effects` et `idempotent` sont refusés sur la référence, comme pour `local_files` : les droits les expriment déjà.

`loom validate` affiche :

```text
Fournis    : loom.excel
  assistant : modèle SIMULE, 1 outil(s) Python
    outil excel : donnees → /…/donnees : read
    outil excel : sortie → /…/sortie : write
    outil excel : recalcul : /usr/bin/soffice (au plus 60 s)
```

### Ce que le modèle envoie

Le modèle ne passe ni couleur, ni police, ni code de format. Exemple réel du run de bout en bout :

```json
{"path": "sortie/relances-octobre.xlsx",
 "sheets": [
  {"name": "Hypothèses",
   "columns": [{"header": "Paramètre"}, {"header": "Valeur", "type": "pourcentage"}],
   "rows": [
     {"cells": ["Taux de conversion visé", 0.15], "role": "hypothese",
      "note": "Source : objectif fixé par l'artisan"},
     {"cells": ["TVA", 0.2], "role": "hypothese", "note": "Source : taux normal, art. 278 CGI"}]},
  {"name": "Suivi",
   "columns": [{"header": "Client"}, {"header": "Date devis", "type": "date"},
               {"header": "Montant HT", "type": "montant_eur"},
               {"header": "Relances", "type": "entier"}, {"header": "Statut"}],
   "data": {"path": "donnees/devis.csv"},
   "totals": {"Montant HT": "somme", "Relances": "somme"}},
  {"name": "Synthèse",
   "columns": [{"header": "Indicateur"}, {"header": "Valeur", "type": "montant_eur"}],
   "rows": [
     {"cells": ["Montant HT en attente", "=SUMIF(Suivi!E2:E5,\"En attente\",Suivi!C2:C5)"]},
     {"cells": ["Montant TTC en attente", "=B2*(1+'Hypothèses'!$B$3)"]},
     {"cells": ["CA HT espéré", "=B2*'Hypothèses'!$B$2"], "role": "total"},
     {"cells": ["Objectif HT du mois", null], "role": "saisie"}]}]}
```

- **Types de colonne :** ceux de la note (`texte`, `entier`, `decimal`, `montant_eur`, `montant_keur`, `pourcentage`, `date`, `date_heure`, `annee`, `booleen`).
- **Rôles :** `saisie`, `hypothese`, `total`, `note`, et `formule` pour forcer. On les donne sur une ligne ou sur une cellule (`{"value": …, "role": …, "note": …}`). Une cellule `=…` est une formule ; si elle cite un autre onglet, elle passe en vert (lien d'onglet).
- **Libellés :** dans une ligne `saisie` ou `hypothese`, un texte non vide est un libellé et reste noir. C'est ce qui garde « TVA » en noir et « 20 % » en bleu sur jaune.
- **Sources :** `rows` (au plus 200 lignes) ou `data` (un `.csv` ou un `.json`), jamais les deux.
- **Totaux :** `totals` ajoute une ligne de total générée (`=SUM(C2:C5)`), hors du filtre automatique.

### Ce que le modèle reçoit

Un résumé, jamais le contenu du classeur :

```json
{"path": "sortie/relances-octobre.xlsx", "bytes": 8579, "written": true, "replaced": false,
 "sha256": "d4e06c65…", "sheets": [{"name": "Hypothèses", "rows": 2, "formulas": 0},
 {"name": "Suivi", "rows": 4, "formulas": 2}, {"name": "Synthèse", "rows": 4, "formulas": 3}],
 "warnings": [], "recalc": {"formulas": 5, "errors": 0}}
```

`written: false` veut dire que le même fichier était déjà là : c'est le cas d'un appel repris.

### Le rendu

Le classeur du run de bout en bout, relu avec openpyxl puis rendu en PDF par LibreOffice :

- **En-têtes :** Calibri 11 gras, fond bleu-gris pâle, bordure basse, centrés. Volets gelés sous l'en-tête (`A2`), filtre automatique sur les lignes de données.
- **Hypothèses :** valeurs en bleu sur fond jaune, commentaire de cellule avec la source, légende en italique gris sous le tableau.
- **Suivi :** CSV français (`;`, `1 250,50`, `02/10/2026`) lu et converti. Montants au format `#,##0.00 "€"`, dates au format n° 14. La ligne « Total » est en gras avec une bordure haute.
- **Synthèse :** formules entre onglets en vert, total en gras, cellule à saisir en bleu sur fond jaune pâle, légende « cellule à saisir ».
- **Largeurs :** estimées sur les valeurs affichées, bornées entre 8 et 60 caractères.

Le rendu de l'essai est en locale anglaise (`10/2/2026`, `1,250.50 €`). Sous un Excel français, les mêmes formats donnent `02/10/2026` et `1 250,50 €` : c'est la décision n° 5.

### Ce que l'outil refuse, et comment il le dit

Les fautes sont listées ensemble (10 au plus), pour que le modèle corrige en une fois. Il y a deux passages : la structure (onglets, colonnes, lignes, sources), puis les valeurs et les formules.

| Faute | Message (extrait) |
| --- | --- |
| Fonction française | `Suivi!C5 : fonction française SOMME dans '=SOMME(A1:A3)' — écrire SUM` |
| Séparateur `;` | `séparateur « ; » … arguments séparés par « , »` |
| Fonction à débordement | `XLOOKUP n'est pas permise (fonction à débordement) — trier, filtrer ou dédoublonner les données avant, et INDEX/MATCH pour une recherche` |
| Fonction hors liste blanche | `fonction HYPERLINK non permise` |
| Onglet inconnu ou non quoté | `onglet inconnu 'Hypothèses' … un nom avec espace ou accent s'écrit entre apostrophes : 'Mes Hypothèses'!B2` |
| Référence hors feuille | `référence XFE1 hors de la feuille` |
| Parenthèses, guillemets | `parenthèses déséquilibrées`, `guillemet non fermé` |
| Nom d'onglet | 31 caractères, `[ ] : * ? / \`, apostrophe en bord, doublon (casse ignorée) |
| Valeur incompatible | `'demain' n'est pas un(e) date`, `1.5 n'est pas un(e) entier` |
| Hypothèse sans source | `une hypothèse porte sa source en 'note' (« Source : … »)` |
| Trop de lignes en ligne | `201 lignes données en ligne, au-delà de 200 — les écrire dans un fichier CSV ou JSON et le citer dans 'data'` |
| Colonne absente du fichier | `colonne(s) absente(s) du fichier : Date devis (trouvées : Client)` |
| Recalcul en erreur | `Recalcul : 1 formule(s) en erreur sur 2 — #DIV/0! : R!B2. Le classeur n'est pas écrit ; corriger ces formules (IFERROR …)` |

Deux points ne sont qu'**avertis**, sans bloquer l'appel :

- un pourcentage plus grand que 1 (`15 affiché 1500% — un pourcentage se donne en fraction`) ;
- une constante dans une formule (`constante 1.05 dans '=B5*1.05' — une hypothèse se met dans sa propre cellule`). Un chiffre seul et 100 sont ignorés (`ROUND(x,2)`).

L'outil corrige aussi en silence ce qui est sans ambiguïté : il ajoute le préfixe `_xlfn.` à `TEXTJOIN`, `CONCAT`, `IFS`, `SWITCH`, `MAXIFS` et `MINIFS`, et convertit en heure de Paris, sans fuseau, une date-heure qui en porte un.

### Limites du premier lot, à documenter

- **Hors périmètre, comme prévu par la note :** graphiques, mise en forme conditionnelle, tableaux croisés, listes déroulantes, macros, édition d'un classeur existant.
- **Pas de valeurs en cache.** Excel recalcule à l'ouverture (`fullCalcOnLoad`). En revanche, un outil qui relit le fichier en `data_only` (pandas…) obtient `None` pour les formules, même avec `recalc` (D-F).
- **La validation ne prouve pas la justesse.** Une plage décalée d'une ligne passe la validation et le recalcul.
- **Le fuseau est figé sur Europe/Paris** (D-G).

## Analyse technique

### Modifications, mesurées sur le prototype

| Fichier | Changement |
| --- | --- |
| `src/loom_ia/tools_builtin/excel_params.py` | Nouveau (73 lignes) : `ExcelParams`. Module léger : Pydantic et `local_files_params` seulement. Il réutilise `FolderSpec` |
| `src/loom_ia/tools_builtin/excel.py` | Nouveau (1 121 lignes) : arguments vus par le modèle, conversion des valeurs, validation des formules, style maison, écriture déterministe, lecture CSV/JSON, recalcul, `ExcelTool` (`assess_call`, `invoke`, `settings_lines`) et la fabrique `build` |
| `src/loom_ia/tools_builtin/__init__.py` | +10 : entrée `loom.excel` (`Builtin` avec `params`, `refused`) et un champ `Builtin.extra`, l'extra qui apporte les dépendances |
| `src/loom_ia/runtime/wiring.py` | +6 / −1 dans `_builtin_tool` (l. 1842) : sur `ImportError`, le message nomme l'extra s'il y en a un. Sans effet sur `fetch_page` et `local_files` |
| `src/loom_ia/tools_builtin/local_files.py` | +10 : `_written` délègue à `_written_bytes(target, located, data, overwrite)`, qui écrit des octets. Comportement inchangé (72 tests de `local_files`, dont 2 sautés en root, comme avant) |
| `pyproject.toml` | Extra `office = ["openpyxl>=3.1.5,<4"]`, ajouté à `all` ; `types-openpyxl` dans le groupe `dev` (des stubs seuls, qui ne tirent pas openpyxl) |
| `uv.lock` | +3 paquets : openpyxl 3.1.5, et-xmlfile 2.0.0, types-openpyxl 3.1.5.20261017 |
| `tests/unit/test_excel.py` | Nouveau (638 lignes, 47 tests), sauté sans l'extra (`importorskip`) |
| `tests/unit/test_tools_builtin.py` | +19 / −5 : `SAMPLE_PARAMS` pour `loom.excel` ; le test du catalogue tolère l'`ImportError` d'un outil à extra (job noyau seul) ; le test « valider n'importe que les paramètres » valide aussi une référence `loom.excel` et vérifie qu'`excel.py` et openpyxl restent hors de `sys.modules` ; le test du schéma attend `loom.excel` |

Ni `core/`, ni `engine/`, ni `core/events`, ni `agents/spec.py`, ni `access/cli` ne sont touchés. La référence à paramètres, `loom validate` et `loom schema` fonctionnent sans changement, grâce au mécanisme posé pour `local_files` (D4 du 25/10).

### Choix techniques et leurs raisons

**Les règles de chemins sont celles de `local_files`, appelées telles quelles.** La fabrique construit un `LocalFilesTool` interne avec les mêmes dossiers, ce qui reprend existence, protection de loom et noms uniques. Chaque chemin passe ensuite par son `_locate`, son `_check_rights` et son `_resolved`. L'écriture passe par `_written_bytes`, donc par le même fichier temporaire, le même renommage atomique, la même mise en place exclusive et le même refus de remplacer. Un seul endroit décide de ce qui est permis. Le prix : 6 membres privés utilisés depuis un autre module (D-A).

**Le fichier est reproductible.** openpyxl date le classeur (`docProps/core.xml`) et chaque entrée de l'archive au moment de l'écriture. Le prototype fixe les deux : propriétés au 01/01/2000 et entrées zip à la même date. Il écrit par `ExcelWriter` et non par `Workbook.save`, qui réimpose « maintenant ». openpyxl refuse une date absente (essayé : `AttributeError`). Deux conséquences :

1. **La reprise est idempotente**, comme pour `local_files` : un appel interrompu puis repris retrouve le même fichier et répond `written: false`. L'outil peut donc se déclarer `idempotent`.
2. **L'empreinte du fichier est stable.** Le résultat porte un `sha256`, ce qui tranche la décision n° 8 de la note dans le sens « hash du fichier, avec dates fixées » (D-C).

**Le recalcul vérifie, il ne livre pas.** `soffice --convert-to xlsx` recalcule bien les formules d'un fichier openpyxl qui n'a pas de valeurs en cache (essayé : `6`, `#DIV/0!`, `2,0`, `#NAME?`, `3` aux bonnes cellules). En revanche, LibreOffice **réécrit les formats** : une date `mm-dd-yy` devient `m/d/yyyy`. Le prototype fait donc recalculer une **copie** dans un dossier temporaire, avec un profil LibreOffice neuf à chaque appel (`-env:UserInstallation`). Il relit les valeurs aux seules cellules qui portent une formule et refuse l'appel si l'une est en erreur. Le fichier écrit reste celui d'openpyxl. Mesures : 1,6 s pour un appel avec recalcul, 3,5 s pour 4 appels en parallèle, sans conflit de profil.

**La conversion des valeurs est tolérante aux exports français.** Les nombres acceptent `1 250,50`, les espaces insécables et `€`. Les dates acceptent `JJ/MM/AAAA` et ISO 8601. Un pourcentage peut s'écrire `15%`, qui donne 0,15. Un booléen peut s'écrire `oui`, `non`, `true` ou `1`. Le délimiteur CSV est `;` ou `,`, selon la première ligne. Les années sont stockées en texte.

**Le style est figé dans le code.** Police Calibri 11, une palette par rôle (bleu `0000FF` pour les saisies et hypothèses, vert `008000` pour les liens d'onglet, gris `7F7F7F` pour les notes), largeurs entre 8 et 60, volets gelés en `A2`, filtre sur les données. C'est la réponse proposée pour la décision n° 6 (D-D).

**Le coût en tokens.** La description et le schéma de l'outil font 1 472 + 3 672 caractères, contre 623 + 911 pour `local_files`, soit environ 1,3 k tokens de plus par appel au modèle quand l'outil est actif. Le schéma est le gros poste : ses descriptions de champs doublent en partie la description (D-H).

**Les performances, sans recalcul.** 10 000 lignes CSV donnent 2,0 s et 212 Ko ; 100 000 lignes, 18,5 s et 2 Mo. Tout le travail passe par `asyncio.to_thread`, la boucle n'est jamais bloquée. Les bornes du prototype : 50 onglets, 200 colonnes, 200 lignes en ligne, 100 000 lignes lues, 20 Mo par fichier source, 50 Mo en sortie.

**L'approbation se décide appel par appel** (`assess_call`), sur les droits réellement exercés : `write` sur la sortie, `overwrite` si demandé, `read` sur chaque source. Le motif montré à l'approbateur les nomme : `écrire le classeur sortie/a.xlsx : approbation demandée pour overwrite sur sortie/a.xlsx`. Effets : `reversible` pour une création, `irreversible` pour un remplacement, toujours idempotent.

### Tests écrits sur le prototype (47)

| Thème | Tests |
| --- | --- |
| Ce que voit le modèle | Description (dossiers, droits, vocabulaire), schéma, `settings_lines` |
| Paramètres | `delete` refusé, pas de dossier `write`, dossiers vides ou en double, `soffice` et `recalc_timeout` sans `recalc`, LibreOffice introuvable |
| Écriture | Classeur complet relu (onglets, volets, filtre, formats, totaux, couleurs, commentaire, légende, largeurs) ; même description donnant le même fichier, dates comprises ; `overwrite` et ses droits |
| Chemins | `.xlsx` exigé, `..`, dossier inconnu, droit `write` ou `read` manquant, lien qui sort du dossier |
| Approbation | Création libre, remplacement approuvé par défaut, dossier qui demande `write`, appel invalide jugé sans effet |
| Sources | CSV français, JSON, colonne absente, fichier introuvable, lignes en ligne bornées, `rows` et `data` exclusifs |
| Valeurs | Année, booléen, date-heure avec fuseau, pourcentage `15%` ; avertissement sur 15 ; 5 valeurs incompatibles ; fautes de structure listées ensemble |
| Formules | 11 refus, acceptation, préfixe `_xlfn.` hors des chaînes, avertissement de constante, formules contrôlées dans le classeur, couleur des liens d'onglet |
| Recalcul | Formule en erreur (fichier non écrit), puis corrigée (rapport, formats d'openpyxl conservés) ; LibreOffice qui échoue ; délai dépassé |
| Montage et run | Référence dans un agent, extra absent nommé, réglage commun refusé, run complet par le moteur, `loom validate` |

Six mutations ont été essayées. **La première passait en silence** : remettre la date du jour dans les propriétés ne faisait tomber aucun test, parce que deux appels dans la même seconde donnent la même date. Le test relit maintenant les dates du fichier, et la mutation tombe. Les cinq autres tombaient dès le départ : droits de sortie non vérifiés, préfixe `_xlfn.` retiré, erreurs de recalcul ignorées, lien hors dossier non contrôlé, fuseau ignoré.

## Plan de non-régression

| Contrôle | Attendu | Prototype |
| --- | --- | --- |
| Suite complète, tous les extras, sans services | Aucun test existant cassé | 3 249 passés (3 202 + 47), 174 sautés, comme la référence |
| Suite complète, noyau seul | Rien ne demande openpyxl | 2 580 passés (identique), 401 sautés (400 + le module `test_excel`) |
| Corpus de régression (`tests/regression`) | Journaux rejoués à l'identique | Verts, compris dans la suite |
| Agents sans `loom.excel` | Mêmes outils, mêmes requêtes | Aucun champ de requête ni d'événement modifié |
| `local_files` | Comportement inchangé après l'extraction de `_written_bytes` | 72 tests collectés, 70 verts et 2 sautés en root, comme la référence, sans modification |
| Messages d'extra absent des autres outils fournis | Inchangés (`extra` vaut `None`) | Inchangés |
| `loom schema` | Nouvelle entrée `Builtin_excel` ; `PythonTool` exclut aussi `loom.excel` | Test du schéma mis à jour en conséquence : c'est le seul test existant dont l'attendu change |
| `pyright` strict / `ruff` / `lint-imports` | 0 / propre / 5 tenus | 0 / propre / 5 tenus |
| Couverture (seuil 94) | Au-dessus du seuil | 94,55 % sans services (référence 94,61 %) ; `excel.py` 92,35 %, `excel_params.py` 100 % |
| Bornes basses (job `lowest-direct`) | Installable | openpyxl 3.1.5 est la seule version admise par le plancher |

## Décisions à prendre

| # | Question | Options | Recommandation |
| --- | --- | --- | --- |
| D-A | Comment `excel` partage-t-il les règles de `local_files` ? | (a) un module commun (`tools_builtin/_folders.py` : `Folder`, localisation, résolution, écriture atomique) que les deux outils importent ; (b) rendre publiques les méthodes de `LocalFilesTool` ; (c) garder les accès privés | (a) : un seul endroit pour la sécurité des chemins, testé une fois ; c'est aussi la base d'un futur outil de lecture |
| D-B | Langue des clés des arguments | (a) anglais, comme `local_files` (`path`, `sheets`, `columns`, `rows`, `totals`, `overwrite`), avec des valeurs du vocabulaire en français (`montant_eur`, `hypothese`, `somme`) : c'est le prototype ; (b) tout en français, comme l'exemple de la note (`fichier`, `onglets`…) ; (c) tout en anglais | À toi ; (a) reste cohérent avec les outils existants |
| D-C | Décision n° 8 : rejeu et empreinte | (a) dates fixées et `sha256` du fichier (prototype), avec 01/01/2000 comme date ; (b) une autre date fixe ; (c) hacher la spec | (a) : c'est aussi ce qui rend la reprise idempotente |
| D-D | Décision n° 6 : style figé ou surchargeable | (a) figé dans le code (prototype) ; (b) un `theme` sur la référence (police, palette) | (a) pour le premier lot : la constance est l'argument de l'outil |
| D-E | Décision n° 9 : outil de lecture xlsx | Dans ce lot, ou plus tard | Plus tard, sur le module de D-A |
| D-F | Recalcul en erreur | (a) l'appel échoue et rien n'est écrit (prototype, la règle de Claude) ; (b) le fichier est écrit et le rapport signale les erreurs ; et à part : livrer le fichier recalculé par LibreOffice (valeurs en cache, mais formats réécrits) | (a), et garder le fichier d'openpyxl |
| D-G | Fuseau des dates-heures | Europe/Paris figé (prototype) ; ou un paramètre `timezone` sur la référence | À toi |
| D-H | Poids du schéma vu par le modèle (3,7 k caractères) | Raccourcir les descriptions de champs qui répètent la description | Oui, à mesurer sur un vrai modèle |
| D-I | Découpage d'`excel.py` (1 121 lignes) | Un sous-paquet `tools_builtin/excel/` (arguments, valeurs, formules, style, recalcul) ; ou un seul module | Le sous-paquet, si D-A est retenu |
| D-J | Version | 2.0.14, ou 2.1.0 puisque c'est une fonction et un extra nouveaux | À toi |

**À vérifier sur ton poste :** `which soffice`. LibreOffice n'est qu'une dépendance système de `recalc: true` ; l'outil le cherche au montage et refuse l'agent s'il manque.

**Ce qui a été décidé (26/10) :** les recommandations, sauf la version et le fuseau.

| # | Décision | Réalisé |
| --- | --- | --- |
| D-A | Un module commun, `tools_builtin/_folders.py` (`Folder`, `Located`, `Folders`, `mount`, `write_bytes`) ; `local_files` l'importe | `local_files.py` passe de 566 à 308 lignes ; ses tests sont inchangés, sauf un, qui appelle `write_new` à son nouvel endroit |
| D-B | Clés en anglais, vocabulaire en français | Comme le prototype |
| D-C | Dates fixées au 01/01/2000, `sha256` du fichier | Comme le prototype |
| D-D | Style figé dans le code | Comme le prototype |
| D-E | Outil de lecture plus tard | — ; réalisée en 2.0.15 (`action: read`), voir `docs/outil-excel-lecture-analyse.md` |
| D-F | Recalcul en erreur : l'appel échoue, rien n'est écrit ; le fichier livré reste celui d'openpyxl | Comme le prototype |
| D-G | **Un paramètre `timezone`**, nom IANA, `Europe/Paris` par défaut, vérifié au chargement (« fuseau inconnu ») ; affiché par `loom validate` et dit dans la description | Nouveau |
| D-H | Schéma allégé : titres de Pydantic retirés, descriptions de champs réduites à ce que la description ne dit pas | Description + schéma : 1 747 + 2 722 caractères, contre 1 472 + 3 672 (−13 %) |
| D-I | Un sous-paquet `tools_builtin/excel/` : `arguments`, `values`, `formulas`, `workbook`, `sources`, `recalc`, `tool` | Le plus long module, `workbook.py`, fait 366 lignes ; plus aucun accès privé ni `pyright: ignore` |
| D-J | **Version 2.0.13** | Entrée du CHANGELOG sous 2.0.13 |

**Premier essai réel (26/10).** Ton agent Marie (service client, vrai modèle) a produit le tableau des prélèvements du client C-042. Le modèle a suivi le vocabulaire : types justes, total déclaré, doublons repérés, IBAN laissé de côté. Le classeur et son export PDF ont montré quatre défauts du style maison, corrigés :

| Défaut constaté | Correction |
| --- | --- |
| Ligne de total sans libellé quand la première colonne est une date | « Total » dans la première colonne de texte sans agrégat, à défaut la première sans agrégat |
| Une date ou un montant, alignés à droite, collés au texte de la colonne suivante | Dates, années et booléens centrés ; textes en retrait d'un caractère (largeur ajustée) |
| Unité en double : « Montant (€) » au-dessus de « 19,90 € » | La description de l'outil et celle de `header` disent de ne mettre l'unité que si le format ne l'affiche pas |
| La dernière colonne imprimée seule sur une deuxième page | Chaque onglet à une page de large (`fitToWidth`), en paysage au-delà de 90 caractères de largeur |

Même description, régénérée et exportée par LibreOffice : une seule page, total libellé, colonnes séparées. Le fichier de l'exemple du guide change donc d'empreinte.

## Plan de mise en œuvre

Un lot par étape, que tu relis et commites :

1. **D-A :** extraire les règles de dossiers de `local_files` dans un module commun, sans changer son comportement (ses 72 tests en sont la preuve).
2. **Socle de l'outil :** `excel_params.py`, l'entrée du catalogue avec `Builtin.extra`, le message de `wiring`, l'extra `office`, `types-openpyxl`, le lock.
3. **L'outil**, découpé selon D-I, et ses tests, en reprenant le prototype et les réponses à D-B à D-H.
4. **CI :** ajouter `openpyxl` à la liste du contrôle « Le noyau s'importe sans SDK » ; prévoir LibreOffice dans le job complet, faute de quoi le test de recalcul réel saute (il saute aujourd'hui proprement s'il est absent).
5. **Documentation :**
   - `fonctions.md` (D13 et point 50) : `loom.excel` ;
   - le guide, au chapitre 32 (`howto/06-outils-fournis.md`) : une section `loom.excel` ;
   - la table des extras (`README.md`, `conception.md`, `fonctions.md`) : `office`, qui apporte `openpyxl` ;
   - le `CHANGELOG`.
6. **Exemple et essai réel :** un exemple dans `examples/`, puis un essai avec un vrai modèle et ton agent de test, pour voir si le modèle suit le vocabulaire sans aide.

## Annexe : expériences reproduites

Toutes ont tourné sur un clone de `fdc6a0a`, dans l'espace de travail cloud de Claude ; rien n'a été écrit dans le dépôt du poste.

| Expérience | Commande, en résumé | Résultat |
| --- | --- | --- |
| Référence | `pytest --cov=loom_ia` ; noyau seul ; `pyright` ; `ruff` ; `lint-imports` | 3 202 / 174 ; 2 580 / 400 ; 94,61 % ; 0 ; propre ; 5 tenus |
| Prototype | Même chose | 3 249 / 174 ; 2 580 / 401 ; 94,55 % ; 0 ; propre ; 5 tenus |
| LibreOffice sur un fichier openpyxl | `soffice -env:UserInstallation=… --headless --calc --convert-to xlsx`, puis relecture `data_only` | Formules calculées, erreurs `#DIV/0!` et `#NAME?` lues, en 1,4 s ; format de date réécrit (`mm-dd-yy` → `m/d/yyyy`) |
| Date absente | `properties.created = None`, puis `save` | `AttributeError` dans openpyxl : une date fixe est nécessaire |
| Run de bout en bout | Projet `loom.yaml` + agent `loom.excel` (`recalc: true`), modèle simulé, CSV français ; `loom validate`, `loom run`, `loom replay` | « monté sans erreur » ; `completed` ; 3 onglets, 5 formules, 0 erreur ; rejeu « Identique », 1 outil servi depuis le journal |
| Rendu | Classeur → PDF (LibreOffice) → PNG | Mise en forme conforme au § Le rendu |
| Parallélisme | 4 appels avec recalcul en `asyncio.gather` | 3,5 s, 4 rapports sans erreur |
| Volume | CSV de 10 000 et 100 000 lignes, sans recalcul | 2,0 s / 212 Ko ; 18,5 s / 2 Mo |
| Extra absent | `sys.modules["openpyxl"] = None`, puis `build_agent` | `dépendance 'openpyxl' absente (installer l'extra : loom-ia[office])` |
| Mutations | 6 mutations de `excel.py`, `pytest tests/unit/test_excel.py` | 5 tombent ; 1 passait (dates), test renforcé, elle tombe |
