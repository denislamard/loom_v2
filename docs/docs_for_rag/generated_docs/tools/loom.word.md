# Outil fourni loom.word

L'outil `loom.word` est fourni avec loom_v2. Un agent l'active dans son `agent.yaml` : `- python: loom.word` dans `tools`.

## Ce que fait loom.word

Outil word : écrire un document .docx mis en forme d'après ses blocs, et lire un .docx.

Le modèle dit **ce que sont** les blocs — titres, paragraphes, listes,
tableaux aux colonnes typées, notes, citations — et jamais à quoi ils
ressemblent : le style maison est dans `writer`. Deux appels identiques
donnent deux fichiers identiques, octet pour octet.

Aucune dépendance : un .docx est un zip de XML, écrit et lu avec la
bibliothèque standard (`zipfile`, expat). Les fichiers sont désignés comme
dans `loom.local_files` et soumis aux mêmes règles (`_document.tool`).

Paramètres de `loom.word` : les dossiers permis, et le fuseau des dates-heures.

Module léger — Pydantic et `_office_params` seulement — : la config
l'importe pour valider la référence au chargement, sans importer l'outil.

    - python: loom.word
      folders:
      - name: modeles
        path: modeles
        rights: [ read ]               # les fichiers que l'outil peut lire
      - name: sortie
        path: sortie
        rights: [ write, overwrite ]   # remplacer un fichier : approbation par défaut
      timezone: Europe/Paris           # par défaut : le fuseau des dates-heures écrites
      blocks: [chart, image]           # les blocs facultatifs : chart, image, signature, toc,
\                                       # checkboxes, fields, code, invoice_totals,
\                                       # attendance ; aucun par défaut
      style: styles/charte.yaml        # la charte (facultatif), relative à resources_dir
      templates:                       # modèles de document, relatifs à resources_dir ; le
        facture: templates/facture.yaml  # style d'un modèle se nomme dans son fichier

Les dossiers suivent la forme de `loom.local_files` (`name`, `path`,
`rights`, `approval`) ; le droit `delete` n'a pas de sens ici et est refusé.

## Comment loom.word est monté

- cible : `loom_ia.tools_builtin.word:build`
- réglages communs refusés sur sa référence : `approval`, `side_effects`, `idempotent`, car les droits de chaque dossier les expriment (folders[].rights), et l'approbation se règle par dossier (folders[].approval)
- l'outil tire sa description de ses réglages : `description` est refusé, `skill` ajoute du texte

## Les paramètres de loom.word dans agent.yaml (réglages sous sa référence)

Les paramètres de `loom.word` sont des clés de sa référence dans `tools`, à côté de `python: loom.word`.

### `tools[]` (référence `python: loom.word`)

Paramètres de `loom.word`.

Modèle Pydantic : `loom_ia.tools_builtin.word_params.WordParams`.

- `tools[].folders` (type `tuple[FolderSpec, ...]` ; obligatoire) : Dossiers permis, au moins un ; le droit `delete` n'y a pas cours.
- `tools[].timezone` (type `str` ; défaut : `"Europe/Paris"`) : Fuseau IANA où passe une date-heure qui en porte un : le document l'écrit à l'heure de ce fuseau, sans le nommer.
- `tools[].blocks` (type `Blocks` ; défaut : `[]`) : Les blocs facultatifs, chacun payé en tokens à chaque appel au modèle : chart (environ 430), image (environ 190), signature, toc et checkboxes (environ 190 à eux trois), fields (environ 115), code (environ 75), invoice_totals (environ 185), attendance (environ 130). Aucun par défaut.
- `tools[].templates` (type `Templates` ; défaut : `{}`) : Les modèles de document, par nom : fichiers relatifs à resources_dir, lus au chargement.
- `tools[].style` (type `StyleField` ; défaut : `null`) : La charte : un fichier de style relatif à resources_dir, lu au chargement, ou un style écrit en ligne ; le style d'un modèle se nomme dans le fichier du modèle (`style:`).

### `tools[].folders[]`

Même forme que `tools[].folders[]` (page « Outil fourni loom.excel »).

### `tools[].templates.<nom>`

Même forme que `tools[].templates.<nom>` (page « Outil fourni loom.pdf »).

### `tools[].style`

Même forme que `tools[].templates.<nom>.style` (page « Outil fourni loom.pdf »).
