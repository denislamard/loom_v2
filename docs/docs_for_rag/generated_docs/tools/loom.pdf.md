# Outil fourni loom.pdf

L'outil `loom.pdf` est fourni avec loom_v2. Un agent l'active dans son `agent.yaml` : `- python: loom.pdf` dans `tools`.

## Ce que fait loom.pdf

Outil pdf : écrire un PDF mis en forme d'après ses blocs, et lire le texte d'un PDF.

Le même vocabulaire que `word` (`_document`) : le modèle décrit les blocs,
l'outil décide de leur apparence. L'écriture passe par reportlab, la lecture
par pypdf dans un process borné (`worker`) : un PDF vient de n'importe où.

reportlab et pypdf viennent de l'extra `pdf` ; ce paquet n'est importé qu'au
montage d'un agent qui active l'outil. Ses paramètres sont dans
`pdf_params`, module léger que la config importe seul.

Paramètres de `loom.pdf` : les dossiers permis, le fuseau, les modèles et le style.

Module léger — Pydantic et `_office_params` seulement — : la config
l'importe pour valider la référence au chargement, sans importer l'outil ni pypdf ni reportlab.

    - python: loom.pdf
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

`style` et les modèles sont ceux de `loom.word` : un même fichier sert aux deux.

Les dossiers suivent la forme de `loom.local_files` (`name`, `path`,
`rights`, `approval`) ; le droit `delete` n'a pas de sens ici et est refusé.

## Comment loom.pdf est monté

- cible : `loom_ia.tools_builtin.pdf:build`
- extra de loom-ia à installer : `loom-ia[pdf]`
- réglages communs refusés sur sa référence : `approval`, `side_effects`, `idempotent`, car les droits de chaque dossier les expriment (folders[].rights), et l'approbation se règle par dossier (folders[].approval)
- l'outil tire sa description de ses réglages : `description` est refusé, `skill` ajoute du texte

## Les paramètres de loom.pdf dans agent.yaml (réglages sous sa référence)

Les paramètres de `loom.pdf` sont des clés de sa référence dans `tools`, à côté de `python: loom.pdf`.

### `tools[]` (référence `python: loom.pdf`)

Paramètres de `loom.pdf`.

Modèle Pydantic : `loom_ia.tools_builtin.pdf_params.PdfParams`.

- `tools[].folders` (type `tuple[FolderSpec, ...]` ; obligatoire) : Dossiers permis, au moins un ; le droit `delete` n'y a pas cours.
- `tools[].timezone` (type `str` ; défaut : `"Europe/Paris"`) : Fuseau IANA où passe une date-heure qui en porte un : le document l'écrit à l'heure de ce fuseau, sans le nommer.
- `tools[].blocks` (type `Blocks` ; défaut : `[]`) : Les blocs facultatifs, chacun payé en tokens à chaque appel au modèle : chart (environ 430), image (environ 190), signature, toc et checkboxes (environ 190 à eux trois), fields (environ 115), code (environ 75), invoice_totals (environ 185), attendance (environ 130). Aucun par défaut.
- `tools[].templates` (type `Templates` ; défaut : `{}`) : Les modèles de document, par nom : fichiers relatifs à resources_dir, lus au chargement.
- `tools[].style` (type `StyleField` ; défaut : `null`) : La charte : un fichier de style relatif à resources_dir, lu au chargement, ou un style écrit en ligne ; le style d'un modèle se nomme dans le fichier du modèle (`style:`).

### `tools[].folders[]`

Même forme que `tools[].folders[]` (page « Outil fourni loom.excel »).

### `tools[].templates.<nom>`

Un modèle de document : son fichier YAML (relatif à `resources_dir`), puis son texte.

Le chargeur de config lit le fichier et range son texte dans `text`, comme un
`skill_file` dans `skill` ; `file` n'est alors plus gardé. Le style que nomme le
modèle (`style:` dans son fichier) est lu en même temps et rangé dans `style`. En
config, le chemin seul : `note: templates/note.yaml`.

Modèle Pydantic : `loom_ia.tools_builtin._office_params.TemplateRef`.

- `tools[].templates.<nom>.file` (type `Path | null` ; défaut : `null`) : Fichier YAML du modèle, relatif à `resources_dir` ; plus gardé une fois lu.
- `tools[].templates.<nom>.text` (type `str | null` ; défaut : `null`) : Texte du modèle, rangé par le chargeur de config.
- `tools[].templates.<nom>.style` (type `StyleField` ; défaut : `null`) : Le style du modèle, lu par le chargeur de config (`loom.word`, `loom.pdf`).

### `tools[].templates.<nom>.style`

Un style : son fichier (relatif à `resources_dir`), ou son contenu.

Le chargeur de config lit le fichier (et ses logos) et range le style dans
`spec` ; `file` n'est alors plus gardé. En config : `style: styles/charte.yaml`,
ou le style écrit en ligne (`style: { heading1: { color: 0B5D3B } }`).

Modèle Pydantic : `loom_ia.tools_builtin._style_params.StyleRef`.

- `tools[].templates.<nom>.style.file` (type `Path | null` ; défaut : `null`) : (sans description)
- `tools[].templates.<nom>.style.spec` (type `StyleFile | null` ; défaut : `null`) : (sans description)

### `tools[].templates.<nom>.style.spec`

Un fichier de style lu : ses réglages et ses styles nommés.

Modèle Pydantic : `loom_ia.tools_builtin._style_params.StyleFile`.

- `tools[].templates.<nom>.style.spec.colors` (type `dict[ColorName, Hex] | null` ; défaut : `null`) : (sans description)
- `tools[].templates.<nom>.style.spec.page` (type `PageSpec | null` ; défaut : `null`) : (sans description)
- `tools[].templates.<nom>.style.spec.numbering` (type `Numbering | null` ; défaut : `null`) : (sans description)
- `tools[].templates.<nom>.style.spec.language` (type `Language | null` ; défaut : `null`) : (sans description)
- `tools[].templates.<nom>.style.spec.labels` (type `LabelsSpec | null` ; défaut : `null`) : (sans description)
- `tools[].templates.<nom>.style.spec.image` (type `ImageSpec | null` ; défaut : `null`) : (sans description)
- `tools[].templates.<nom>.style.spec.chart` (type `ChartSpec | null` ; défaut : `null`) : (sans description)
- `tools[].templates.<nom>.style.spec.fonts` (type `dict[FontName, FontFiles] | null` ; défaut : `null`) : (sans description)
- `tools[].templates.<nom>.style.spec.styles` (type `dict[StyleName, Props]` ; défaut : `{}`) : (sans description)

### `tools[].templates.<nom>.style.spec.page`

La page d'un fichier de style : format, marges, distances, en-tête, pied et filigrane.

Modèle Pydantic : `loom_ia.tools_builtin._style_params.PageSpec`.

- `tools[].templates.<nom>.style.spec.page.paper` (type `PageSize | null` ; défaut : `null`) : Format : `A4`, `A5` ou `Letter`.
- `tools[].templates.<nom>.style.spec.page.orientation` (type `Orientation | null` ; défaut : `null`) : `portrait` ou `landscape`.
- `tools[].templates.<nom>.style.spec.page.margins` (type `Margin | null` ; défaut : `null`) : Les quatre marges, en cm.
- `tools[].templates.<nom>.style.spec.page.margin_top` (type `Margin | null` ; défaut : `null`) : Marge du haut, en cm.
- `tools[].templates.<nom>.style.spec.page.margin_bottom` (type `Margin | null` ; défaut : `null`) : Marge du bas, en cm.
- `tools[].templates.<nom>.style.spec.page.margin_left` (type `Margin | null` ; défaut : `null`) : Marge de gauche, en cm.
- `tools[].templates.<nom>.style.spec.page.margin_right` (type `Margin | null` ; défaut : `null`) : Marge de droite, en cm.
- `tools[].templates.<nom>.style.spec.page.header_distance` (type `float | null` ; défaut : `null`) : La distance de l'en-tête au bord de la page, en cm.
- `tools[].templates.<nom>.style.spec.page.footer_distance` (type `float | null` ; défaut : `null`) : La distance du pied au bord de la page, en cm.
- `tools[].templates.<nom>.style.spec.page.column_gap` (type `float | null` ; défaut : `null`) : L'écart entre les colonnes d'une section, en cm.
- `tools[].templates.<nom>.style.spec.page.beside_gap` (type `float | null` ; défaut : `null`) : L'écart entre deux sections (ou deux blocs) côte à côte, en cm.
- `tools[].templates.<nom>.style.spec.page.beside_space` (type `Points | null` ; défaut : `null`) : L'espace après des sections côte à côte ou encadrées, en points.
- `tools[].templates.<nom>.style.spec.page.cover_margin_top` (type `float | null` ; défaut : `null`) : Le haut d'une page de garde sans `margin_top`, en cm.
- `tools[].templates.<nom>.style.spec.page.header` (type `BandSpec | null` ; défaut : `null`) : Ce qu'écrit l'en-tête : du contenu, pas un style.
- `tools[].templates.<nom>.style.spec.page.footer` (type `BandSpec | null` ; défaut : `null`) : Ce qu'écrit le pied.
- `tools[].templates.<nom>.style.spec.page.watermark` (type `str | null` ; défaut : `null`) : Le texte du filigrane, 40 caractères au plus.

### `tools[].templates.<nom>.style.spec.page.header`

Ce qu'écrit un en-tête ou un pied de page (`page.header`, `page.footer`).

Trois zones sur une ligne, ou une table ; des lignes de toute la largeur ; le logo,
posé dans l'élément qui le demande ; `first_page: false` pour une première page
sans.

Modèle Pydantic : `loom_ia.tools_builtin._style_params.BandSpec`.

- `tools[].templates.<nom>.style.spec.page.header.first_page` (type `bool | null` ; défaut : `null`) : (sans description)
- `tools[].templates.<nom>.style.spec.page.header.logo` (type `BandLogoSpec | null` ; défaut : `null`) : (sans description)
- `tools[].templates.<nom>.style.spec.page.header.left` (type `BandElementSpec | null` ; défaut : `null`) : (sans description)
- `tools[].templates.<nom>.style.spec.page.header.center` (type `BandElementSpec | null` ; défaut : `null`) : (sans description)
- `tools[].templates.<nom>.style.spec.page.header.right` (type `BandElementSpec | null` ; défaut : `null`) : (sans description)
- `tools[].templates.<nom>.style.spec.page.header.lines` (type `list[BandElementSpec] | null` ; défaut : `null`) : (sans description)
- `tools[].templates.<nom>.style.spec.page.header.table` (type `BandTableSpec | null` ; défaut : `null`) : (sans description)

### `tools[].templates.<nom>.style.spec.page.header.logo`

Le logo d'un en-tête ou d'un pied.

Son fichier (PNG ou JPEG, relatif à `resources_dir` ; ses octets une fois lu au
chargement), sa largeur (cm), l'écart à son texte (pt). Écrit en court : le fichier
seul.

Modèle Pydantic : `loom_ia.tools_builtin._style_params.BandLogoSpec`.

- `tools[].templates.<nom>.style.spec.page.header.logo.file` (type `str | bytes` ; obligatoire) : (sans description)
- `tools[].templates.<nom>.style.spec.page.header.logo.width` (type `float | null` ; défaut : `null`) : (sans description)
- `tools[].templates.<nom>.style.spec.page.header.logo.gap` (type `float | null` ; défaut : `null`) : (sans description)

### `tools[].templates.<nom>.style.spec.page.header.left`

Un élément d'un en-tête ou d'un pied de page : une zone, une ligne, une cellule de sa table.

Un texte (`text`), ou des lignes (`lines`, dans une cellule seulement), avec
`{page}`, `{pages}`, `{title}` ; le nom du style qui le met en forme
(`style`, un style qui part de `header` ou de `footer`) ; le logo de l'en-tête
ou du pied (`logo: true`). Écrit en court : un texte, ou une liste de lignes.
Aucune propriété de style : elles vont dans le style qu'il nomme.

Modèle Pydantic : `loom_ia.tools_builtin._style_params.BandElementSpec`.

- `tools[].templates.<nom>.style.spec.page.header.left.text` (type `BandText | null` ; défaut : `null`) : (sans description)
- `tools[].templates.<nom>.style.spec.page.header.left.lines` (type `list[BandText] | null` ; défaut : `null`) : (sans description)
- `tools[].templates.<nom>.style.spec.page.header.left.style` (type `StyleName | null` ; défaut : `null`) : (sans description)
- `tools[].templates.<nom>.style.spec.page.header.left.logo` (type `bool | null` ; défaut : `null`) : (sans description)

### `tools[].templates.<nom>.style.spec.page.header.center`

Même forme que `tools[].templates.<nom>.style.spec.page.header.left` (page « Outil fourni loom.pdf »).

### `tools[].templates.<nom>.style.spec.page.header.right`

Même forme que `tools[].templates.<nom>.style.spec.page.header.left` (page « Outil fourni loom.pdf »).

### `tools[].templates.<nom>.style.spec.page.header.lines[]`

Même forme que `tools[].templates.<nom>.style.spec.page.header.left` (page « Outil fourni loom.pdf »).

### `tools[].templates.<nom>.style.spec.page.header.table`

La table d'un en-tête ou d'un pied de page.

La largeur de chaque colonne (une longueur, une proportion, ou `auto` : la place
qui reste, partagée) ; ses lignes, chacune un élément par colonne ; le nom du style
qui l'habille (`style`, qui part de `header` ou de `footer` ; `header` ou
`footer` sans lui) : son cadre, son fond, ses filets intérieurs (`borders`,
`line`), l'écart entre ses colonnes (`column_gap`).

Modèle Pydantic : `loom_ia.tools_builtin._style_params.BandTableSpec`.

- `tools[].templates.<nom>.style.spec.page.header.table.widths` (type `list[BandWidth]` ; obligatoire ; bornes : longueur ≥ 1, longueur ≤ 4) : (sans description)
- `tools[].templates.<nom>.style.spec.page.header.table.rows` (type `list[list[BandElementSpec]]` ; obligatoire ; bornes : longueur ≥ 1, longueur ≤ 3) : (sans description)
- `tools[].templates.<nom>.style.spec.page.header.table.style` (type `StyleName | null` ; défaut : `null`) : (sans description)

### `tools[].templates.<nom>.style.spec.page.header.table.rows[][]`

Même forme que `tools[].templates.<nom>.style.spec.page.header.left` (page « Outil fourni loom.pdf »).

### `tools[].templates.<nom>.style.spec.page.footer`

Même forme que `tools[].templates.<nom>.style.spec.page.header` (page « Outil fourni loom.pdf »).

### `tools[].templates.<nom>.style.spec.labels`

Les libellés que l'outil écrit, s'ils diffèrent de ceux de la langue.

Modèle Pydantic : `loom_ia.tools_builtin._style_params.LabelsSpec`.

- `tools[].templates.<nom>.style.spec.labels.table` (type `str | null` ; défaut : `null`) : Légende d'un tableau (« Tableau »).
- `tools[].templates.<nom>.style.spec.labels.figure` (type `str | null` ; défaut : `null`) : Légende d'une figure (« Figure »).
- `tools[].templates.<nom>.style.spec.labels.contents` (type `str | null` ; défaut : `null`) : Titre du sommaire (« Sommaire »).
- `tools[].templates.<nom>.style.spec.labels.date` (type `str | null` ; défaut : `null`) : Libellé de la date d'une signature (« Date : »).
- `tools[].templates.<nom>.style.spec.labels.signature` (type `str | null` ; défaut : `null`) : Libellé de la signature (« Signature : »).
- `tools[].templates.<nom>.style.spec.labels.article` (type `str | null` ; défaut : `null`) : Numérotation par articles (« Article »).
- `tools[].templates.<nom>.style.spec.labels.total` (type `str | null` ; défaut : `null`) : Ligne de total (« Total »).
- `tools[].templates.<nom>.style.spec.labels.notes` (type `str | null` ; défaut : `null`) : Titre des notes (« Notes »).
- `tools[].templates.<nom>.style.spec.labels.subtotal` (type `str | null` ; défaut : `null`) : Ligne de sous-total (« Sous-total »).
- `tools[].templates.<nom>.style.spec.labels.total_excl_tax` (type `str | null` ; défaut : `null`) : Total hors taxes (« Total HT »).
- `tools[].templates.<nom>.style.spec.labels.tax` (type `str | null` ; défaut : `null`) : Ligne de TVA : `{rate}` (le taux), et au besoin `{base}` (sa base) — « TVA {rate} sur {base} ».
- `tools[].templates.<nom>.style.spec.labels.total_incl_tax` (type `str | null` ; défaut : `null`) : Total toutes taxes (« Total TTC »).
- `tools[].templates.<nom>.style.spec.labels.paid` (type `str | null` ; défaut : `null`) : Acompte (« Acompte versé »).
- `tools[].templates.<nom>.style.spec.labels.due` (type `str | null` ; défaut : `null`) : Reste à payer (« Net à payer »).
- `tools[].templates.<nom>.style.spec.labels.participant` (type `str | null` ; défaut : `null`) : Colonne de la feuille d'émargement (« Participant »).

### `tools[].templates.<nom>.style.spec.image`

Les images d'un fichier de style.

Modèle Pydantic : `loom_ia.tools_builtin._style_params.ImageSpec`.

- `tools[].templates.<nom>.style.spec.image.side_by_side` (type `bool | null` ; défaut : `null`) : Deux images `half` qui se suivent : côte à côte.
- `tools[].templates.<nom>.style.spec.image.space_after` (type `Points | null` ; défaut : `null`) : L'espace après une image sans légende, en points.

### `tools[].templates.<nom>.style.spec.chart`

Les graphiques d'un fichier de style.

Modèle Pydantic : `loom_ia.tools_builtin._style_params.ChartSpec`.

- `tools[].templates.<nom>.style.spec.chart.series` (type `list[Color] | null` ; défaut : `null`) : La couleur de chaque série, dans l'ordre (12 au plus).
- `tools[].templates.<nom>.style.spec.chart.text_color` (type `Color | null` ; défaut : `null`) : La couleur du texte.
- `tools[].templates.<nom>.style.spec.chart.grid_color` (type `Color | null` ; défaut : `null`) : La couleur de la grille.
- `tools[].templates.<nom>.style.spec.chart.title_color` (type `Color | null` ; défaut : `null`) : La couleur du titre.
- `tools[].templates.<nom>.style.spec.chart.space_after` (type `Points | null` ; défaut : `null`) : L'espace après un graphique sans légende, en points.

### `tools[].templates.<nom>.style.spec.fonts.<nom>`

Les fichiers TrueType d'une police, relatifs à `resources_dir` ; leurs octets une fois lus au chargement.

`regular` seul suffit : le gras et l'italique le prennent.

Modèle Pydantic : `loom_ia.tools_builtin._style_params.FontFiles`.

- `tools[].templates.<nom>.style.spec.fonts.<nom>.regular` (type `str | bytes` ; obligatoire) : (sans description)
- `tools[].templates.<nom>.style.spec.fonts.<nom>.bold` (type `str | bytes | null` ; défaut : `null`) : (sans description)
- `tools[].templates.<nom>.style.spec.fonts.<nom>.italic` (type `str | bytes | null` ; défaut : `null`) : (sans description)
- `tools[].templates.<nom>.style.spec.fonts.<nom>.bold_italic` (type `str | bytes | null` ; défaut : `null`) : (sans description)

### `tools[].templates.<nom>.style.spec.styles.<nom>`

Les propriétés d'un style nommé, telles qu'écrites ; toutes facultatives.

Leurs noms sont ceux du registre (`PROPERTIES`) ; une autre est dite par
`describe`.

Modèle Pydantic : `loom_ia.tools_builtin._style_params.Props`.

- `tools[].templates.<nom>.style.spec.styles.<nom>.base` (type `StyleName | null` ; défaut : `null`) : Style de base, dont il reprend les propriétés.
- `tools[].templates.<nom>.style.spec.styles.<nom>.font` (type `FontName | null` ; défaut : `null`) : Police.
- `tools[].templates.<nom>.style.spec.styles.<nom>.size` (type `float | null` ; défaut : `null`) : Taille du texte, en points (6 à 72).
- `tools[].templates.<nom>.style.spec.styles.<nom>.color` (type `Color | null` ; défaut : `null`) : Couleur du texte.
- `tools[].templates.<nom>.style.spec.styles.<nom>.fill` (type `Fill | null` ; défaut : `null`) : Couleur du fond, ou `none`.
- `tools[].templates.<nom>.style.spec.styles.<nom>.bold` (type `bool | null` ; défaut : `null`) : Gras.
- `tools[].templates.<nom>.style.spec.styles.<nom>.italic` (type `bool | null` ; défaut : `null`) : Italique.
- `tools[].templates.<nom>.style.spec.styles.<nom>.underline` (type `bool | null` ; défaut : `null`) : Souligné.
- `tools[].templates.<nom>.style.spec.styles.<nom>.uppercase` (type `bool | null` ; défaut : `null`) : En capitales.
- `tools[].templates.<nom>.style.spec.styles.<nom>.align` (type `Align | null` ; défaut : `null`) : Alignement : `left`, `center`, `right` ou `justify`.
- `tools[].templates.<nom>.style.spec.styles.<nom>.space_before` (type `Points | null` ; défaut : `null`) : Espace avant, en points.
- `tools[].templates.<nom>.style.spec.styles.<nom>.space_after` (type `Points | null` ; défaut : `null`) : Espace après, en points.
- `tools[].templates.<nom>.style.spec.styles.<nom>.line_spacing` (type `float | null` ; défaut : `null`) : Interligne, de 1 à 3.
- `tools[].templates.<nom>.style.spec.styles.<nom>.indent` (type `float | null` ; défaut : `null`) : Retrait à gauche, en cm.
- `tools[].templates.<nom>.style.spec.styles.<nom>.border_top` (type `Rule | null` ; défaut : `null`) : Filet au-dessus.
- `tools[].templates.<nom>.style.spec.styles.<nom>.border_bottom` (type `Rule | null` ; défaut : `null`) : Filet au-dessous.
- `tools[].templates.<nom>.style.spec.styles.<nom>.border_left` (type `Rule | null` ; défaut : `null`) : Filet à gauche.
- `tools[].templates.<nom>.style.spec.styles.<nom>.border_right` (type `Rule | null` ; défaut : `null`) : Filet à droite.
- `tools[].templates.<nom>.style.spec.styles.<nom>.border` (type `Rule | null` ; défaut : `null`) : Filet des quatre côtés.
- `tools[].templates.<nom>.style.spec.styles.<nom>.border_space` (type `Sides | null` ; défaut : `null`) : L'écart entre un filet (ou le fond) et le texte, en points, les quatre côtés.
- `tools[].templates.<nom>.style.spec.styles.<nom>.valign` (type `VAlign | null` ; défaut : `null`) : Le tableau, et les tables d'en-tête et de pied : le texte dans la hauteur d'une cellule.
- `tools[].templates.<nom>.style.spec.styles.<nom>.borders` (type `Borders | null` ; défaut : `null`) : Le tableau : ses filets (`none`, `horizontal`, `vertical`, `grid`).
- `tools[].templates.<nom>.style.spec.styles.<nom>.line` (type `Rule | null` ; défaut : `null`) : Le tableau : le filet de ses traits.
- `tools[].templates.<nom>.style.spec.styles.<nom>.bands` (type `Fill | null` ; défaut : `null`) : Le tableau : le fond d'une ligne sur deux.
- `tools[].templates.<nom>.style.spec.styles.<nom>.width` (type `TableWidth | null` ; défaut : `null`) : Le tableau : toute la largeur (`full`) ou celle du contenu.
- `tools[].templates.<nom>.style.spec.styles.<nom>.position` (type `Position | null` ; défaut : `null`) : Le tableau : sa place dans la largeur.
- `tools[].templates.<nom>.style.spec.styles.<nom>.padding` (type `Sides | null` ; défaut : `null`) : Le cadre d'une section, une cellule de tableau : l'écart au texte, en cm, les quatre côtés.
- `tools[].templates.<nom>.style.spec.styles.<nom>.column_gap` (type `float | null` ; défaut : `null`) : La table d'un en-tête ou d'un pied : l'écart entre deux colonnes, en cm.
- `tools[].templates.<nom>.style.spec.styles.<nom>.numbered` (type `bool | null` ; défaut : `null`) : La légende, le sommaire : numérotés.
- `tools[].templates.<nom>.style.spec.styles.<nom>.levels` (type `int | null` ; défaut : `null`) : Le sommaire : niveaux de titres repris, de 1 à 3.
- `tools[].templates.<nom>.style.spec.styles.<nom>.page_numbers` (type `bool | null` ; défaut : `null`) : Le sommaire : avec les numéros de page.
- `tools[].templates.<nom>.style.spec.styles.<nom>.level_indent` (type `float | null` ; défaut : `null`) : Une liste, le sommaire : le retrait de chaque niveau, en cm.
- `tools[].templates.<nom>.style.spec.styles.<nom>.marker_space` (type `float | null` ; défaut : `null`) : Une liste : la place de la puce, en cm.
- `tools[].templates.<nom>.style.spec.styles.<nom>.sign_height` (type `float | null` ; défaut : `null`) : La signature : la place pour signer, en cm.
- `tools[].templates.<nom>.style.spec.styles.<nom>.label_width` (type `float | null` ; défaut : `null`) : Les champs : la largeur de la colonne des libellés, en cm.
- `tools[].templates.<nom>.style.spec.styles.<nom>.row_height` (type `float | null` ; défaut : `null`) : La hauteur d'une ligne d'émargement, en cm : la place de signer.
- `tools[].templates.<nom>.style.spec.styles.<nom>.description` (type `str | null` ; défaut : `null`) : Le sens d'un style à toi : il est proposé au modèle de langage, sur son type de bloc.

### `tools[].style`

Même forme que `tools[].templates.<nom>.style` (page « Outil fourni loom.pdf »).
