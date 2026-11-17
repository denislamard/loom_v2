# Outil fourni loom.excel

L'outil `loom.excel` est fourni avec loom_v2. Un agent l'active dans son `agent.yaml` : `- python: loom.excel` dans `tools`.

## Ce que fait loom.excel

Outil excel : écrire un classeur .xlsx mis en forme, d'après une description sémantique.

Le modèle dit **ce que sont** les données — onglets, colonnes et leur type,
rôle des lignes (input, assumption, total…), formules — et jamais à quoi
elles ressemblent : police, couleurs, formats de nombre, largeurs, volets
gelés et filtre viennent du style maison (`workbook`). Deux appels
identiques donnent deux fichiers identiques, octet pour octet.

Les fichiers lus (CSV, JSON) et écrits (.xlsx) sont désignés comme dans
`loom.local_files` — `<dossier>/<chemin>` — et soumis aux mêmes règles,
celles de `_folders` : dossiers déclarés, droits, approbation par appel,
liens qui sortent du dossier refusés, écriture atomique, fichier existant
remplacé seulement avec `overwrite`.

Avant d'écrire, la description est validée sans dépendance (`arguments`,
`values`, `formulas`). Avec `recalc`, LibreOffice recalcule une copie et
toute formule en erreur fait échouer l'appel (`recalc`).

openpyxl vient de l'extra `office` ; ce paquet n'est importé qu'au montage
d'un agent qui active l'outil. Ses paramètres sont dans `excel_params`,
module léger que la config importe seul.

## Comment loom.excel est monté

- cible : `loom_ia.tools_builtin.excel:build`
- extra de loom-ia à installer : `loom-ia[office]`
- réglages communs refusés sur sa référence : `approval`, `side_effects`, `idempotent`, car les droits de chaque dossier les expriment (folders[].rights), et l'approbation se règle par dossier (folders[].approval)
- l'outil tire sa description de ses réglages : `description` est refusé, `skill` ajoute du texte

## Les réglages de loom.excel dans agent.yaml

Ce sont des clés de sa référence dans `tools`, à côté de `python: loom.excel`.

### `tools[]` (référence `python: loom.excel`)

Paramètres de `loom.excel`.

Modèle Pydantic : `loom_ia.tools_builtin.excel_params.ExcelParams`.

- `tools[].folders` (type `tuple[FolderSpec, ...]` ; obligatoire) : Dossiers permis, au moins un ; le droit `delete` n'y a pas cours.
- `tools[].timezone` (type `str` ; défaut : `"Europe/Paris"`) : Fuseau IANA où passe une date-heure qui en porte un : Excel n'en connaît pas, la cellule reçoit l'heure de ce fuseau, sans fuseau.
- `tools[].recalc` (type `bool` ; défaut : `false`) : Recalculer le classeur avec LibreOffice avant de l'écrire : une formule en erreur (#DIV/0!, #NAME?…) fait échouer l'appel, et le fichier n'est pas écrit.
- `tools[].soffice` (type `Path | null` ; défaut : `null`) : L'exécutable de LibreOffice ; par défaut `soffice` cherché dans le PATH.
- `tools[].recalc_timeout` (type `float` ; défaut : `60.0` ; bornes : > 0) : Délai du recalcul, en secondes.
- `tools[].charts` (type `bool` ; défaut : `false`) : Des graphiques Excel liés aux cellules de leur onglet. Faux par défaut : leur mode d'emploi et leur schéma coûtent à chaque appel du modèle.

### `tools[].folders[]`

Un dossier permis : son nom pour le modèle, son chemin, ses droits.

Modèle Pydantic : `loom_ia.tools_builtin.local_files_params.FolderSpec`.

- `tools[].folders[].name` (type `str` ; obligatoire ; bornes : motif `^[a-z0-9][a-z0-9_-]{0,31}$`) : Ce que voit le modèle : il désigne un fichier par « <name>/<chemin> », et le chemin réel ne lui est jamais montré.
- `tools[].folders[].path` (type `Path` ; obligatoire) : Absolu, ou relatif au dossier du fichier de config. Ni « ~ » ni variable (« $HOME ») : la config n'interprète rien.
- `tools[].folders[].rights` (type `dict[Right, RightApproval]` ; obligatoire) : Droits accordés (`read`, `write`, `overwrite`, `delete`) et l'approbation de chacun. En entrée, une liste et `approval` ; ici, la table droit → approbation.
