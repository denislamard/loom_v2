# Outil fourni loom.local_files

L'outil `loom.local_files` est fourni avec loom_v2. Un agent l'active dans son `agent.yaml` : `- python: loom.local_files` dans `tools`.

## Ce que fait loom.local_files

Outil local_files : lire, lister, écrire et supprimer des fichiers dans des dossiers.

Les dossiers, leurs droits et l'approbation de chaque droit viennent de la
config (`local_files_params`). Le modèle désigne un fichier par
`<dossier>/<chemin>` : il ne voit jamais un chemin réel, et ne nomme que
les dossiers déclarés.

Les règles des dossiers — chemin tel qu'écrit pour juger droits et
approbation, chemin résolu qui doit rester dans son dossier, écriture
atomique — sont dans `_folders`, partagé avec `loom.excel` ; sa
docstring dit aussi la course connue.

`write` écrit du texte (`content`), ou copie un fichier que l'agent a vu
passer (`file`) : une pièce jointe, un fichier rendu par un outil — un
outil forgé, un sous-agent. Le fichier est relu dans le stockage d'artefacts
par son nom, parmi ceux de la conversation seulement (`ToolContext.files`).

Paramètres de `loom.local_files` : les dossiers permis, et pour chacun ses droits.

Module léger — Pydantic seulement — : la config l'importe pour valider la
référence au chargement, sans importer l'outil.

    - python: loom.local_files
      folders:
      - name: work
        path: /home/denis/temp/test
        rights: [ read, write, overwrite, delete ]
        approval: { overwrite: never, delete: never }   # rien ne bloque
      - name: devis
        path: devis
        rights: [ read, write ]        # approbations par défaut

`rights` liste les droits **accordés** ; un droit absent est refusé.
`approval`, facultatif, dit lesquels attendent un humain : `never` (l'appel
passe) ou `always` (validation humaine). Un droit qui n'y figure pas garde son
approbation par défaut : `read` et `write` à `never`, `overwrite` et
`delete` à `always`. Le modèle garde la forme que lit l'outil, `rights`
en table droit → approbation (`_rights`).

## Comment loom.local_files est monté

- cible : `loom_ia.tools_builtin.local_files:build`
- réglages communs refusés sur sa référence : `approval`, `side_effects`, `idempotent`, car les droits de chaque dossier les expriment (folders[].rights), et l'approbation se règle par dossier (folders[].approval)
- l'outil tire sa description de ses réglages : `description` est refusé, `skill` ajoute du texte

## Les paramètres de loom.local_files dans agent.yaml (réglages sous sa référence)

Les paramètres de `loom.local_files` sont des clés de sa référence dans `tools`, à côté de `python: loom.local_files`.

### `tools[]` (référence `python: loom.local_files`)

Paramètres de `loom.local_files`.

Modèle Pydantic : `loom_ia.tools_builtin.local_files_params.LocalFilesParams`.

- `tools[].folders` (type `tuple[FolderSpec, ...]` ; obligatoire) : Dossiers permis, au moins un, de noms distincts.

### `tools[].folders[]`

Même forme que `tools[].folders[]` (page « Outil fourni loom.excel »).

## Les arguments que le modèle donne à loom.local_files

- `action` (`"list" | "read" | "write" | "delete"` ; obligatoire) : list : contenu d'un dossier ; read : lire un fichier ; write : écrire un fichier ; delete : supprimer un fichier.
- `path` (`string` ; obligatoire) : « <dossier>/<chemin> », par exemple work/devis/D-042.txt ; pour list, « <dossier> » seul est permis.
- `content` (`string | null`) : Texte à écrire, en UTF-8 ; pour write seulement.
- `file` (`string | null`) : Pour write, à la place de content : nom d'un fichier de la conversation (joint, ou rendu par un outil), copié tel quel.
- `overwrite` (`boolean`) : Pour write : remplacer le fichier s'il existe déjà (droit overwrite).
