# Analyse d'impact — outils fournis `loom.word` et `loom.pdf` loom_v2

29 octobre 2026 · Denis Lamard

## En bref

Le but : lire et écrire des documents Word (.docx) et PDF depuis un agent, dans la même démarche que `loom.excel` — le modèle décrit ce que **sont** les contenus, l'outil décide de leur apparence, les fichiers vivent dans des dossiers déclarés avec leurs droits. Cette analyse mesure ce qu'il en coûte, sur un prototype complet.

**Verdict : rien ne bloque.** Le changement tient dans deux outils nouveaux (`word/`, `pdf/`), un socle commun (`_document/`), deux modules partagés avec `excel` (`_values.py`, `_office_params.py`), un extra `pdf` et des retouches courtes : le catalogue (+21 lignes), `excel` (−182 lignes nettes, déplacées dans les modules partagés), la CI (+12 / −7). Le moteur, le journal, `wiring`, `agents/spec.py` et la CLI ne bougent pas. Un agent qui n'active ni `loom.word` ni `loom.pdf` monte exactement comme avant.

- **Tests :** 3 745 verts, soit les 3 665 existants plus 80 nouveaux. En noyau seul, 2 822 verts (2 775 + 47 : `loom.word` n'a besoin d'aucun extra), le module de `loom.pdf` sautant faute de l'extra.
- **Qualité :** pyright strict à 0, ruff propre, les 5 contrats d'import tenus.
- **Couverture :** 94,14 % sans services, contre 94,16 % à la référence.

| Point | Constat | Preuve |
| --- | --- | --- |
| Aucun outil actif par défaut | `loom.word` et `loom.pdf` ne s'activent que par `- python: loom.word` / `loom.pdf` | Démontré (montage, `loom validate`) |
| Chargement à la demande | Valider une config n'importe ni `word`, ni `_document`, ni `pdf`, ni reportlab, ni pypdf | Démontré (sous-process, `sys.modules`) |
| Word sans dépendance | Le .docx est écrit et lu avec la bibliothèque standard (`zipfile`, expat) | Démontré (job noyau seul) |
| Extra absent | `dépendance 'reportlab' absente (installer l'extra : loom-ia[pdf])`, au montage donc dès `loom validate` | Démontré |
| `loom.excel` inchangé | Même classeur à l'octet près (sha256 `af8b36…` avant et après), mêmes messages, 124 tests inchangés | Démontré |
| Le modèle ne décide pas du style | Il décrit des blocs (titre, paragraphe, liste, tableau typé, note, citation) ; l'outil pose police, couleurs, marges, numéros de page | Démontré (rendus relus, § Le rendu) |
| Mêmes règles de fichiers qu'`excel` | Dossiers déclarés, droits, approbation par appel, liens sortants refusés, écriture atomique, `overwrite` | Démontré, par `_folders` lui-même |
| Fichiers reproductibles | Même description, même fichier à l'octet près, pour les deux formats, y compris d'un process à l'autre | Démontré |
| Lecture sûre | Bombes zip et PDF, entités XML, imbrication extrême : refusées. Un PDF forgé ne peut ni bloquer l'agent ni épuiser sa mémoire (process borné, tué à l'échéance ou à l'annulation) | Démontré (un essai par cas) |

**Trois choses sont à trancher avant de réaliser :**

1. **Un ou deux outils (D-1).** Le prototype fait deux outils, comme `excel` fait un format. Chacun coûte environ 1,6 k tokens par appel au modèle ; les deux actifs, 3,3 k.
2. **Word sans python-docx (D-2).** C'est le choix du prototype, et la raison est mesurée : python-docx tire lxml, et **lxml change les octets des classeurs de `loom.excel`** (son empreinte passe de `af8b36…` à `df9916…`) et fait passer sa lecture par libxml2.
3. **La lecture des PDF dans un process à part (D-5)** : 0,2 s de plus par lecture, contre une protection réelle — pypdf a eu deux boucles sans fin en 2026 (CVE-2026-59935, CVE-2026-59937), qu'un fil d'exécution ne sait pas interrompre.

## Périmètre et état de référence

L'analyse porte sur le commit `87937c5` (« bug CI on build »), le même sur le poste et sur `origin/main`, arbre propre. La version est 2.0.15, non publiée.

Rien n'a été écrit dans `/home/denis/dev/loom_v2`, à part ce document. Les essais ont tourné sur un clone, dans l'espace de travail cloud de Claude, en Python 3.14.6, avec `uv sync --locked --all-extras --group types` et LibreOffice 24.2.7.

**Lu :** `tools_builtin/__init__.py`, `_folders.py`, `local_files_params.py`, `excel_params.py`, tout `excel/` ; `runtime/wiring.py` (`_builtin_tool`) ; `tests/unit/test_excel.py`, `test_excel_read.py`, `test_tools_builtin.py` ; `.github/workflows/ci.yml` ; `pyproject.toml` ; `docs/outil-excel-analyse.md`, `docs/outil-excel-lecture-analyse.md`.

| Contrôle sur `87937c5`, avant toute modification | Résultat |
| --- | --- |
| `pytest`, tous les extras, sans services | 3 665 passés, 203 sautés |
| `pytest`, noyau seul (`uv sync --locked`) | 2 775 passés, 407 sautés |
| Couverture en branches, sans services | 94,16 % |
| `pyright` strict | 0 erreur |
| `ruff check` / `ruff format --check` | propres |
| `lint-imports` | 5 contrats tenus |

Deux tests sont instables sur la référence comme sur le prototype, sans lien avec ce chantier : `test_telemetrie.py::test_the_otel_queue_size_variable_still_rules` (1 échec sur 6 passages de la référence) et, sous charge, `test_access_mcp_http.py::test_cancel_needs_the_right_on_the_agent_of_the_run`. Tous deux passent seuls.

## Analyse fonctionnelle

### Ce que voit l'utilisateur de loom

Ta config du 29/10 pour `loom.excel` s'étend sans surprise :

```yaml
tools:
- python: loom.excel
  skill_file: excel.md
  folders:
  - name: workspace
    path: workspace
    rights: [ read, write ]
- python: loom.word
  skill_file: word.md            # facultatif, comme pour excel
  folders:
  - name: workspace
    path: workspace
    rights: [ read, write ]
- python: loom.pdf
  folders:
  - name: workspace
    path: workspace
    rights: [ read, write, overwrite ]   # remplacer un PDF : approbation par défaut
  timezone: Europe/Paris                 # par défaut ; le fuseau des dates-heures écrites
```

Les paramètres sont ceux d'`excel` sans le recalcul : `folders` (forme de `local_files`, `delete` refusé : ces outils ne suppriment rien) et `timezone`. `approval`, `side_effects` et `idempotent` sont refusés sur la référence, comme pour `excel`. `skill_file` s'ajoute à la description.

`loom validate` les montre comme les autres outils fournis :

```
AGENTS
  compta · HAIKU · 3 outils Python
  ├ excel        workspace → ./workspace
  │              read, write
  │              fuseau : Europe/Paris · recalcul : non · lecture des classeurs : oui
  ├ word         workspace → ./workspace
  │              read, write
  │              fuseau : Europe/Paris · lecture des documents : oui
  └ pdf          workspace → ./workspace
                 read, write · overwrite*
                 fuseau : Europe/Paris · lecture des PDF : oui, process à part (20 s, 1 Gio)
```

### Ce que le modèle envoie

Le même vocabulaire pour les deux formats. Une écriture (`action` vaut `write` par défaut) :

```json
{
  "path": "workspace/devis-D-2026-042.docx",
  "title": "Devis n° D-2026-042 — Rénovation salle de bains",
  "blocks": [
    {"type": "paragraphe", "text": "Madame **Dupont**,\nsuite à notre visite, voici notre proposition *détaillée*."},
    {"type": "titre", "text": "Objet des travaux"},
    {"type": "liste", "items": ["Dépose de la baignoire",
                                {"text": "Douche à l'italienne", "items": ["receveur extra-plat", "paroi 120 cm"]}]},
    {"type": "tableau",
     "columns": [{"header": "Désignation"}, {"header": "Quantité", "type": "entier"},
                 {"header": "Montant HT", "type": "montant_eur"}, {"header": "TVA", "type": "pourcentage"}],
     "rows": [["Dépose baignoire", 1, 180, 0.1], ["Receveur 120x90", 1, "1 250,50", 0.1]],
     "totals": {"Montant HT": "somme"},
     "caption": "Montants hors taxes."},
    {"type": "note", "title": "Validité", "text": "Ce devis est valable **3 mois**."},
    {"type": "citation", "text": "Un travail soigné.", "source": "M. Martin"},
    {"type": "saut_de_page"},
    {"type": "titre", "text": "Conditions générales", "level": 2}
  ]
}
```

- **Blocs :** `titre` (`level` 1 à 3), `paragraphe`, `liste` (à puces, ou `ordered` ; un niveau de sous-liste), `tableau`, `note` (encadré), `citation`, `saut_de_page`.
- **Tableaux :** les types de colonne d'`excel` (`texte`, `entier`, `decimal`, `montant_eur`, `montant_keur`, `pourcentage`, `date`, `date_heure`, `annee`, `booleen`), avec la même conversion tolérante (`1 250,50`, `02/10/2026`, `15%`, `oui`). Le total est **calculé par l'outil** (un document n'a pas de formules) : `somme`, `moyenne`, `nombre`, `min`, `max`.
- **Texte :** `**gras**`, `*italique*`, et `\n` pour aller à la ligne dans le paragraphe. Rien d'autre : un `#` ou un `-` en tête reste du texte.
- **Lecture :** `{"action": "read", "path": "…"}`, avec `start` (le bloc où commencer) pour Word, `pages` (`3`, `2-5`, `4-`) pour PDF.

### Ce que le modèle reçoit

**Écriture :** le chemin, la taille, `written` / `replaced`, le `sha256`, le nombre de blocs, les avertissements, et pour un PDF le nombre de pages. Jamais le contenu.

**Lecture d'un .docx :** les blocs, **dans le vocabulaire de l'écriture**, l'emphase rendue en `**…**` et `*…*` : ce qui est lu peut être réécrit tel quel.

```json
{"path": "workspace/devis.docx", "title": "Devis n° D-2026-042 — …", "start": 0, "block_count": 14, "truncated": false,
 "blocks": [
   {"type": "titre", "level": 0, "text": "Devis n° D-2026-042 — …"},
   {"type": "paragraphe", "text": "Madame **Dupont**,\nsuite à notre visite, …"},
   {"type": "liste", "ordered": false, "items": ["Dépose de la baignoire", {"text": "Douche à l'italienne", "items": ["…"]}]},
   {"type": "tableau", "rows": [["Désignation", "Quantité", "Montant HT", "TVA"], ["Dépose baignoire", "1", "180,00 €", "10,0 %"], …]},
   {"type": "note", "text": "**Validité**\nCe devis est valable **3 mois**."},
   {"type": "citation", "text": "Un travail soigné."}]}
```

Le niveau 0 est le titre du document (style « Title »). Les titres se reconnaissent au niveau de plan (`outlineLvl`) ou au nom de style intégré (« heading 1 », en anglais dans le fichier quelle que soit la langue de Word), en suivant l'héritage des styles. Au-delà de 40 000 caractères, la lecture est coupée : `next_start` dit où reprendre, et `outline` donne le plan entier (indice de chaque titre), pour aller droit à une section. Les notes du résultat signalent les images non lues, les modifications suivies (texte inséré rendu, texte supprimé ou déplacé omis) et les commentaires.

**Lecture d'un PDF :** le texte de chaque page, le nombre de pages, titre, auteur et sujet s'ils sont posés, et les **champs d'un formulaire** avec leurs valeurs (avec la première page). Une page sans texte est signalée (« page scannée ou image ; pas d'OCR »). Au-delà de 40 000 caractères : `next_page`.

### Le rendu

Le style maison est commun aux deux formats : A4 portrait, marges de 2 cm, titre en bleu nuit souligné d'un filet, titres de section bleu nuit, tableaux à en-tête bleu pâle (`D9E1F2`, celui d'`excel`) qui se répète d'une page à l'autre, ligne de total en gras sous un filet, nombres alignés à droite, dates centrées, notes en encadré gris à filet bleu, citations en retrait, « Page n / N » en pied. Le .docx est en Calibri 11 et déclaré en français (correcteur, césure) ; le PDF est en Helvetica, police standard du PDF, non embarquée (fichier léger : 4,3 Ko pour le devis d'exemple).

Le devis d'exemple, ouvert dans LibreOffice (docx) et rendu par reportlab (pdf), donne la même page : même ordre, mêmes couleurs, même tableau, même pied. Une différence : les puces du PDF sont un peu plus hautes.

Les valeurs s'écrivent **à la française**, avec des espaces insécables : `1 250,50 €`, `12 500 k€`, `15,5 %`, `02/10/2026`, `02/10/2026 11:30` (une date-heure qui porte un fuseau passe à l'heure de `timezone`). Contrairement à `excel`, dont les formats suivent la locale du lecteur, un document est du texte figé.

### Ce que l'outil refuse, et comment il le dit

Comme `excel`, toutes les fautes d'une description sont listées ensemble (10 au plus), avec le correctif quand il existe :

```
Document refusé :
- bloc 1 (titre) : texte vide
- bloc 2 (tableau) : en-tête donné deux fois : A
- bloc 2 (tableau), ligne 1 : 3 valeurs pour 2 colonnes
- bloc 2 (tableau), ligne 1, « A » : 'pas un nombre' n'est pas un(e) entier
- bloc 2 (tableau) : total sur une colonne inconnue 'B'
- bloc 3 (paragraphe) : caractère de contrôle U+0007 refusé
```

| Cas | Réponse |
| --- | --- |
| Tableau de plus de 8 colonnes, ou de 500 lignes | Refusé : « une page n'en tient pas plus ; scinder le tableau, ou en faire un classeur (excel) » |
| `somme` sur une colonne texte, `max` sur un booléen | Refusé, avec le type de la colonne |
| Titre de niveau 3 après un niveau 1 | Avertissement : « un niveau sauté brouille le plan » |
| Pourcentage donné 15 au lieu de 0,15 | Avertissement (règle d'`excel`) |
| Caractère hors des polices du PDF (emoji, idéogramme) | Avertissement qui les cite : ils s'impriment en carré noir. ≥, α, €, œ, « » passent |
| `.doc`, `.odt`, `.rtf` | « format Word 97-2003 (.doc) non lu — l'enregistrer en .docx depuis Word ou LibreOffice » |
| .docx protégé par mot de passe, ou renommé | « pas un document .docx lisible (fichier vide, renommé, ou protégé par mot de passe) » |
| PDF protégé par un mot de passe d'ouverture | « PDF protégé par mot de passe : il n'est pas lu ». Un PDF chiffré sans mot de passe d'ouverture (droits seuls) est lu, et le résultat le dit |
| `pages` illisible ou au-delà du document | « écrire 3, 2-5 ou 4- », « page 9 demandée, le PDF en a 4 » |

### Limites du premier lot, à documenter

- Pas d'image (pas de logo), pas d'en-tête de page personnalisé, pas de lien cliquable.
- On écrit un document neuf, ou on en lit un ; on ne modifie pas un document existant, et on ne remplit pas un modèle (.dotx) ni un formulaire PDF.
- Lecture d'un .docx : ni images, ni en-têtes et pieds de page, ni commentaires, ni zones de texte.
- Lecture d'un PDF : le texte que contient le fichier, sans OCR ; la mise en page des tableaux n'est pas reconstituée.
- PDF écrits en polices standard (Europe de l'Ouest, plus les symboles grecs et mathématiques).

## Analyse technique

### Modifications, mesurées sur le prototype

| Fichier | Changement |
| --- | --- |
| `tools_builtin/_values.py` | Nouveau (149 lignes) : le vocabulaire des colonnes (`ColumnType`, `Aggregate`, `Scalar`) et `convert`, **déplacés** d'`excel/values.py`, qui disparaît. `convert` dit ses fautes à un `Reports` (protocole) au lieu des `Problems` d'`excel` |
| `tools_builtin/_office_params.py` | Nouveau (87 lignes) : les validateurs de dossiers d'`excel_params` (au moins un, noms uniques, `delete` refusé avant les règles communes) et la vérification du fuseau, paramétrés par le nom de l'outil |
| `tools_builtin/excel/arguments.py`, `workbook.py`, `excel_params.py` | +13 / −73 : ils importent les deux modules ci-dessus. Messages identiques, à l'octet près |
| `tools_builtin/_document/` | Nouveau : `arguments.py` (236, les blocs et l'appel vu par le modèle), `content.py` (457, validation, valeurs à la française, totaux, emphase, largeurs de colonnes), `tool.py` (254, `DocumentTool` : dossiers, `assess_call`, `invoke`, description, `settings`). Pydantic et la bibliothèque standard seulement |
| `tools_builtin/word/` | Nouveau : `writer.py` (436, le .docx en WordprocessingML), `reader.py` (690, lecture au fil de l'eau), `tool.py` (82) |
| `tools_builtin/word_params.py` | Nouveau (54) : `WordParams` |
| `tools_builtin/pdf/` | Nouveau : `writer.py` (383, reportlab), `reader.py` (134, le process de lecture et ses bornes), `worker.py` (145, le process lui-même), `tool.py` (94) |
| `tools_builtin/pdf_params.py` | Nouveau (54) : `PdfParams` |
| `tools_builtin/__init__.py` | +21 : entrées `loom.word` (sans extra) et `loom.pdf` (extra `pdf`) |
| `pyproject.toml` | Extra `pdf = ["pypdf>=6.14.2,<7", "reportlab>=4.4.0,<6"]`, ajouté à `all` |
| `uv.lock` | +3 paquets : pypdf 6.21.4, reportlab 5.1.0, pillow 13.0.0 (dépendance de reportlab) |
| `.github/workflows/ci.yml` | `reportlab` et `pypdf` dans le contrôle « Le noyau s'importe sans SDK » ; `libreoffice-writer-nogui` à côté de `libreoffice-calc-nogui`, dans les deux jobs qui installent LibreOffice |
| `tests/unit/test_document.py`, `test_word.py`, `test_pdf.py` | Nouveaux : 22, 25 et 33 tests ; `test_pdf` saute sans l'extra |
| `tests/unit/test_tools_builtin.py` | +21 / −3 : paramètres d'essai des deux outils, le test « valider n'importe que les paramètres » vérifie aussi `word`, `_document`, `pdf`, reportlab et pypdf, le test du schéma attend les deux références |

Ni `core/`, ni `engine/`, ni `runtime/wiring.py`, ni `agents/spec.py`, ni `access/cli` ne sont touchés : la référence à paramètres, le message d'extra absent, `loom validate` et `loom schema` fonctionnent par le catalogue, comme pour `excel`.

### Choix techniques et leurs raisons

**Word sans bibliothèque.** Un .docx est un zip de dix parties XML. Le prototype les écrit lui-même (`writer.py`) et les lit avec expat (`reader.py`). Trois raisons :

1. **lxml.** python-docx en dépend. Installé à côté d'openpyxl, lxml change la façon dont openpyxl écrit : **le même classeur n'a plus la même empreinte** (essayé : `af8b36…` sans lxml, `df9916…` avec). La reproductibilité d'`excel` deviendrait dépendante des extras installés, et sa lecture passerait par libxml2 (`xml_safety` le prévoit, mais c'est un autre chemin à éprouver).
2. **La reproductibilité.** python-docx date les entrées de l'archive au moment de l'écriture ; il aurait fallu la réécrire après coup, comme pour openpyxl. Ici, rien ne dépend de l'heure : dates des propriétés et de l'archive fixées au 01/01/2000, comme `excel`.
3. **La lecture.** python-docx ne rend pas les blocs dans l'ordre du corps (paragraphes et tableaux sont deux listes séparées), ne suit ni les contrôles de contenu, ni les modifications suivies, et charge tout en mémoire. Le lecteur du prototype fait tout cela au fil de l'eau.

Le prix : 1 126 lignes à maintenir au lieu d'une dépendance, et l'ordre des éléments à respecter (Word applique le schéma strictement, LibreOffice non). Le prototype suit l'ordre du schéma ; LibreOffice et python-docx ouvrent ses fichiers, mais **Word lui-même n'a pas pu être essayé** (§ À vérifier sur ton poste).

**Un style commun, deux rendus.** `_document/content.py` décide de tout ce qui ne dépend pas du format : valeurs mises en forme, alignements, totaux, largeur relative des colonnes (d'après le contenu), segments gras et italiques. `word/writer.py` et `pdf/writer.py` ne font que poser ces éléments. Un même appel donne donc les mêmes chiffres dans les deux formats.

**L'emphase en `**` et `*`.** Les modèles l'écrivent spontanément. Un marqueur n'ouvre que s'il est suivi d'un caractère visible et refermé plus loin : `2 * 3 * 4` reste du texte. La lecture rend la même syntaxe, pour qu'un document lu puisse être réécrit.

**Le PDF par reportlab.** Pure Python, licence BSD, mode `invariant` qui fixe date et identifiant : deux appels, et deux process, donnent le même fichier. Le « Page n / N » demande de connaître le total : le prototype rend deux fois (le premier rendu compte les pages), plutôt que de sous-classer le canevas par une méthode privée de reportlab. Coût : 3,3 s pour un document de 51 pages (600 blocs et un tableau de 500 lignes), 0,03 s pour le devis d'exemple.

**La lecture des PDF dans un process à part.** pypdf a eu en 2026 au moins deux failles de déni de service : une boucle sans fin sur une image en ligne mal terminée (CVE-2026-59935, corrigée en 6.14.2) et une récupération de table de références interminable (CVE-2026-59937, corrigée en 6.14.0). Un fil d'exécution ne s'interrompt pas : le délai de l'outil rendrait la main au moteur, mais le fil continuerait de tourner. Le prototype lance donc `python -I worker.py`, qui se borne lui-même avant d'importer pypdf (1 Gio de mémoire adressable, le temps CPU de l'échéance, aucun fichier écrit), et le tue à l'échéance (20 s) ou quand l'appel est annulé. Le process n'importe rien de loom : il est lancé par son chemin, et démarre en 0,2 s. Le plancher de pypdf est 6.14.2, et la fabrique le vérifie au montage dès qu'un dossier a le droit `read`.

**La lecture des .docx dans le process de loom.** Pas de bibliothèque tierce ici : expat (refuse l'amplification d'entités depuis 2.4.0, vérifié au montage comme pour `excel`), zip contrôlé avant toute lecture (10 000 membres, 200 Mo décompressés, 100 Mo de corps, 10 Mo par partie lue en entier), corps lu au fil de l'eau avec chaque bloc libéré une fois rendu, échéance de 20 s. Mesures : 0,9 s pour 11 Mo de XML (50 000 paragraphes), 6 s pour 68 Mo (300 000) ; la mémoire ne suit pas la taille du corps.

**Ce que les deux outils partagent avec `excel`.** Les types de colonne et leur conversion (`_values.py`) : un montant ou une date se décrit de la même façon dans un classeur et dans un document. Les validateurs de dossiers (`_office_params.py`) : un seul endroit dit que ces outils ne suppriment rien. Le module des dossiers (`_folders.py`) était déjà commun.

**L'approbation se décide appel par appel**, comme pour `excel` : `write` sur la sortie, `overwrite` si demandé, `read` sur la source. Effets : `reversible` pour une création, `irreversible` pour un remplacement, `none` pour une lecture ; toujours idempotent (le même contenu déjà là n'est pas réécrit).

**Le coût en tokens.** Description et schéma : 1 857 + 4 440 caractères pour `word`, 2 025 + 4 425 pour `pdf`, contre 2 365 + 3 988 pour `excel` (mêmes dossiers). Environ 1,6 k tokens par outil et par appel au modèle. Le schéma est le gros poste : l'union des sept blocs, et deux docstrings de classes qui y passent (D-11).

**L'extra `pdf`.** reportlab tire pillow : 32 Mo sur le disque en tout (pillow 21, reportlab 7, pypdf 4). C'est la raison de l'extra séparé : `office` reste léger pour qui n'utilise qu'`excel`.

### Tests écrits sur le prototype (80)

| Thème | Tests |
| --- | --- |
| Socle (`test_document`, 22) | Arguments et champs propres à `read` ; ordre des blocs ; emphase (dont `2 * 3 * 4`) ; valeurs à la française ; tableau converti, aligné, totalisé ; tous les agrégats, fuseau des dates-heures ; fautes listées ensemble ; bornes (colonnes, lignes, titre, texte, éléments de liste) ; avertissements ; document vide ; paramètres (`delete`, fuseau, dossiers) des deux outils |
| Word (`test_word`, 25) | Description, schéma, `settings` ; document écrit relu dans son XML (styles, emphase, sauts de ligne, en-tête répété, total en gras, numérotations distinctes, propriétés) ; même fichier, reprise sans réécriture ; `overwrite` et son droit ; chemins et liens ; approbation ; LibreOffice ouvre le fichier ; relecture dans le même vocabulaire ; document fait main (styles hérités, `outlineLvl`, liste par style, insertions, suppressions, déplacements, champs, liens, contrôles de contenu, cellules fusionnées) ; document « d'ailleurs » produit par LibreOffice ; lecture par parties et plan ; formats refusés ; bombe zip ; entités XML (amplification refusée, entité externe jamais lue) ; imbrication extrême ; expat trop ancien ; config, run complet, `loom validate` |
| PDF (`test_pdf`, 33) | Description, `settings` ; pypdf trop ancien refusé au montage ; PDF écrit relu (pages, titre, pied, montants) ; même fichier, y compris depuis un autre process ; caractères sans glyphe signalés ; chemins et droits ; relecture ; `pages` (5 cas acceptés, 4 refusés) ; lecture par parties ; page sans texte ; champs de formulaire ; PDF chiffrés (avec et sans mot de passe d'ouverture) ; pas un PDF ; bombe de décompression ; process qui ne rend pas la main, tué ; process tué par ses bornes ; appel annulé, process tué ; borne mémoire effective ; le process appelé en direct (pour sa couverture) ; extra absent ; `loom validate` |

Huit mutations ont été essayées. **Trois passaient d'abord en silence**, et les tests ont été corrigés :

1. Remettre la date du jour dans les propriétés du .docx : le test comparait au `FIXED_DATE` du module, que la mutation changeait aussi. Il compare maintenant à la date écrite en dur.
2. Ouvrir l'italique sur un astérisque suivi d'une espace : le test n'avait qu'un astérisque isolé. `2 * 3 * 4` le prend en défaut.
3. Ne plus sauter `w:del` : la mutation était sans effet (le texte supprimé est dans `w:delText`, jamais lu). En cherchant, un vrai manque : le texte **déplacé** (`w:moveFrom`) était lu deux fois, à son ancienne place et à la nouvelle. Corrigé, et testé.

Les cinq autres tombaient dès le départ : droit `overwrite` non vérifié, process non tué à l'échéance, bornes du process non posées, caractères sans glyphe non signalés, deux listes voisines fusionnées à la lecture. Ce dernier défaut était d'ailleurs dans le premier jet du lecteur : le test d'aller-retour l'a trouvé (une liste à puces suivie d'une liste numérotée revenait en une seule liste).

Couverture des nouveaux modules : 91 à 100 % ; `pdf/worker.py`, qui tourne dans un autre process, est couvert par ses appels directs (75 %).

## Effets de bord sur le reste de loom

### 1. `loom.excel` change de modules, pas de comportement

`excel/values.py` disparaît ; `ColumnType`, `Aggregate` et `Scalar` restent importables depuis `excel.arguments`. Les 124 tests d'`excel` passent sans modification, le classeur d'essai garde son empreinte, les messages des paramètres sont identiques. Un code tiers qui importerait `loom_ia.tools_builtin.excel.values` casserait : c'est un module interne, sans usage hors de loom.

### 2. `loom schema`

Deux définitions nouvelles, `Builtin_word` et `Builtin_pdf` ; `PythonTool` exclut aussi ces deux références. C'est le seul test existant dont l'attendu change.

### 3. L'extra `all` grossit

`loom-ia[all]` installe désormais reportlab, pillow et pypdf (32 Mo). lxml n'est **pas** tiré : `excel` écrit les mêmes octets dans un environnement `all`.

### 4. Un process par lecture de PDF

La lecture lance `sys.executable`. C'est sans effet dans un service ordinaire, mais à savoir pour un loom embarqué dans un exécutable figé (PyInstaller), où `worker.py` n'est pas un fichier : la lecture y échouerait proprement (« lecture impossible ici »), l'écriture marcherait. Les bornes reposent sur `setrlimit` (Linux, la plate-forme déclarée de loom-ia).

### 5. Rejeu

Comme pour `excel` : un appel d'écriture se rejoue depuis le journal sans réécrire le fichier ; une lecture, sans effet, s'exécute en rejeu « variante ».

### 6. CI

Le job complet et celui des bornes basses installent maintenant LibreOffice Writer en plus de Calc : avec `--require-services`, les deux tests Word qui passent par LibreOffice échoueraient sans lui. Les bornes basses de l'extra (`reportlab 4.4.0`, `pypdf 6.14.2`) ont été essayées : 33 tests verts.

### 7. Ce qui ne change pas

Le moteur, le journal et ses événements, `wiring`, la config des agents qui n'activent pas ces outils, les autres outils fournis.

## Mesures

| Mesure | Valeur |
| --- | --- |
| Devis d'exemple, .docx | 5,7 Ko, 4 ms |
| Devis d'exemple, PDF | 4,3 Ko, 2 pages, 0,03 s |
| 600 blocs et un tableau de 500 lignes, .docx | 17 Ko, 0,11 s |
| Le même, PDF | 66 Ko, 51 pages, 3,3 s (deux rendus) |
| Lecture d'un petit .docx | 3 ms |
| Lecture d'un petit PDF | 0,22 s (le démarrage du process) |
| Lecture du gros .docx (budget atteint, plan complet) | 0,24 s |
| Lecture du gros PDF (budget atteint) | 0,5 s |
| .docx de 11 Mo / 68 Mo de XML | 0,9 s / 6 s |
| Bombe PDF (1 Gio décompressé, 1 Mo sur disque) | Refusée en 0,7 s par les bornes de pypdf |

## Plan de non-régression

| Contrôle | Attendu | Prototype |
| --- | --- | --- |
| Suite complète, tous les extras, sans services | Aucun test existant cassé | 3 745 passés (3 665 + 80), 203 sautés, comme la référence |
| Suite complète, noyau seul | Rien ne demande reportlab ni pypdf | 2 822 passés (2 775 + 47), 408 sautés (407 + le module `test_pdf`) |
| `loom.excel` | Comportement et octets inchangés | 124 tests sans modification ; même sha256 |
| Agents sans `loom.word` ni `loom.pdf` | Mêmes outils, mêmes requêtes | Aucun champ de requête ni d'événement modifié |
| `loom schema` | Deux entrées nouvelles | Test du schéma mis à jour : le seul attendu existant qui change |
| `pyright` strict / `ruff` / `lint-imports` | 0 / propre / 5 tenus | 0 / propre / 5 tenus |
| Couverture (seuil 94 en CI, avec services) | Au-dessus du seuil | 94,14 % sans services (référence 94,16 %) |
| Bornes basses (job `lowest-direct`) | Installable | reportlab 4.4.0 et pypdf 6.14.2 : 33 tests verts |

## Décisions à prendre

| # | Question | Options | Recommandation |
| --- | --- | --- | --- |
| D-1 | Un ou deux outils | (a) `loom.word` et `loom.pdf`, un format par outil comme `excel` (prototype) ; (b) un seul `loom.document` avec un `format` | (a) : le modèle choisit l'outil comme il choisit l'extension, chacun s'active seul, et `word` n'exige aucun extra. (b) divise par deux le coût en tokens quand les deux sont actifs |
| D-2 | Comment écrire et lire le .docx | (a) bibliothèque standard (prototype) ; (b) python-docx, qui tire lxml | (a) : lxml change les octets d'`excel` ; la lecture au fil de l'eau et l'ordre des blocs sont à écrire de toute façon |
| D-3 | L'extra | (a) `pdf` séparé, `word` sans extra (prototype) ; (b) tout dans `office` | (a) : 32 Mo que n'a pas à installer qui n'utilise qu'`excel` |
| D-4 | Polices du PDF | (a) polices standard, avertissement pour un caractère absent (prototype) ; (b) embarquer une police libre (DejaVu, environ 700 Ko dans le paquet) ; (c) convertir le .docx par LibreOffice | (a) pour ce lot ; (b) si un vrai besoin apparaît. (c) exigerait LibreOffice et perdrait la reproductibilité |
| D-5 | Lecture des PDF | (a) process à part, borné, tué à l'échéance (prototype) ; (b) un fil d'exécution, comme la lecture d'un classeur | (a) : pypdf a eu deux boucles sans fin cette année, et un fil ne s'arrête pas |
| D-6 | Le vocabulaire | Les 7 blocs, les types de colonne d'`excel`, `**` et `*` (prototype) ; ou y ajouter dès ce lot images, en-têtes de page, liens | Le prototype tel quel ; la suite après un essai réel (§ D-10) |
| D-7 | Style | Figé dans le code (prototype, comme `excel`) ; ou un `theme` sur la référence | Figé : la constance est l'argument de l'outil |
| D-8 | Le partage avec `excel` | (a) `_values.py` et `_office_params.py`, `excel` les importe (prototype) ; (b) copier ce qu'il faut, `excel` intact | (a) : un seul vocabulaire de colonnes, un seul refus de `delete` ; `excel` est prouvé inchangé |
| D-9 | La lecture | Dans le vocabulaire de l'écriture, par blocs et plan (Word), par pages et champs de formulaire (PDF) | Oui |
| D-10 | La suite | Modifier un .docx existant, remplir un modèle (.dotx) ou un formulaire PDF (CERFA), logo et en-tête d'entreprise | Plus tard, chacun avec son analyse ; le remplissage de formulaires PDF est probablement le plus utile aux artisans |
| D-11 | Poids du schéma (4,4 k caractères) | Retirer les docstrings de classes qui y passent, raccourcir les descriptions de champs | Oui, à mesurer sur un vrai modèle, comme pour `excel` |
| D-12 | Version | 2.0.15 (non publiée), ou 2.1.0 | À toi |

**À vérifier sur ton poste :**

- Ouvrir un .docx du prototype **dans Word** (et pas seulement LibreOffice) : Word refuse un fichier dont les éléments ne suivent pas l'ordre du schéma, ce que rien ne vérifie ici.
- Écrire un document avec ton agent et un vrai modèle, pour voir s'il suit le vocabulaire (blocs, types, emphase) sans aide, puis avec un `skill_file`.

## Plan de mise en œuvre

Un lot par étape, que tu relis et commites :

1. **Socle partagé (D-8) :** `_values.py` et `_office_params.py`, `excel` les importe, sans changer son comportement (ses 124 tests en sont la preuve).
2. **`_document` et `loom.word` :** le socle des documents, l'outil Word, son entrée au catalogue, ses tests ; LibreOffice Writer dans la CI.
3. **`loom.pdf` :** l'outil, son process de lecture, l'extra `pdf`, le lock, le contrôle « noyau sans SDK » de la CI, ses tests.
4. **Documentation :**
   - `fonctions.md` (D13 et la table des extras) ;
   - le guide, au chapitre 32 (`howto/06-outils-fournis.md`) : une section par outil ;
   - la table des extras (`README.md`, `conception.md`) : `pdf`, qui apporte reportlab et pypdf ;
   - le `CHANGELOG`.
5. **Essai réel :** les deux outils sur ton agent de test, avec un vrai modèle ; un `skill_file` si le modèle hésite.

## Annexe : expériences reproduites

Toutes ont tourné sur un clone de `87937c5`, dans l'espace de travail cloud de Claude ; rien n'a été écrit dans le dépôt du poste.

| Expérience | Commande, en résumé | Résultat |
| --- | --- | --- |
| lxml et `excel` | Le même classeur écrit sans puis avec lxml installé (`uv run --with lxml`) | `af8b36…` puis `df9916…` : l'empreinte change |
| Rendu Word | Devis d'exemple → `soffice --convert-to pdf` → `pdftoppm` | Page conforme au style maison ; python-docx ouvre aussi le fichier (20 paragraphes, 1 tableau, styles nommés) |
| Rendu PDF | Devis d'exemple → `pdftoppm` | Même page que le .docx ; pied « Page 1 / 2 » |
| Polices standard | « Été œuvre € — « » ’ ≥ α 🙂 中 » rendu par reportlab | Tout s'affiche sauf 🙂 et 中, en carré noir |
| Reproductibilité PDF | Même appel dans deux interpréteurs | Même sha256 |
| Démarrage du process de lecture | `python -I -c "import pypdf"`, 3 fois | 0,20 à 0,26 s |
| Bombe PDF | Page de 1 Gio de zéros compressés (1 Mo), puis 2 Gio | Refusée en 0,5 à 0,7 s (`LimitReachedError` de pypdf, borne de 75 Mo par flux) |
| Bornes basses | `uv run --with reportlab==4.4.0 --with pypdf==6.14.2 pytest tests/unit/test_pdf.py` | 33 verts |
| Gros .docx | 50 000 puis 300 000 paragraphes fabriqués | 0,9 s puis 6 s ; budget atteint, plan et compte complets |
| Instabilité préexistante | `test_the_otel_queue_size_variable_still_rules`, 6 fois sur `87937c5` | 1 échec sur 6 |
