# Analyse d'impact — des styles de document pour `loom.word` loom_v2

9 novembre 2026 · Denis Lamard

> **Décisions prises le 09/11/2026** : D-1 à D-6 et D-8 à D-11 : (a). D-7 : (c), les numéros de page du sommaire au choix de la charte (`toc.pages`), avec numéros par défaut. D-12 : la version 2.0.20, l'actuelle. D-13 : B-4 corrigé dans ce lot. La clé qui active les trois blocs (D-6) s'appelle `extra_blocks`.

## En bref

Le besoin vient des essais de factures du POC shaper.ia (journaux `fac-01` à `fac-03`). Le modèle `facture` donne la bonne structure, mais le style maison de `loom.word` est le même pour tous les documents : Calibri, titres bleu nuit, tableaux à grille pleine largeur. Une facture d'artisan demande autre chose : son logo, émetteur et client côte à côte, des titres de section qu'on ne voit pas, un récapitulatif étroit à droite, les mentions légales en pied de page, une zone « Bon pour accord ». Le principe est arrêté : **le style se décrit dans des fichiers de l'agent, et l'outil fournit seulement les possibilités de Word.** Rien de propre à un type de document n'est codé dans `loom.word`.

**Verdict : rien ne bloque.** Trois couches couvrent 38 des 44 types de documents de l'inventaire (§ Les documents visés), soit 86 % :

1. **La charte**, un fichier YAML déclaré sur l'outil : page, polices, couleurs, titres, tableaux, en-tête et pied de page avec logo, filigrane, titres et légendes numérotés.
2. **La disposition**, un fichier YAML par modèle de document : section par section, titre masqué ou discret, sections côte à côte, encadré, petit texte, colonnes, paysage, page de garde.
3. **Trois blocs nouveaux** que le modèle de langage peut envoyer : `signature`, `sommaire`, et les cases à cocher (`liste` avec `checkboxes`).

Les couches 1 et 2 **ne coûtent rien en jetons** : le modèle de langage écrit les mêmes blocs et les mêmes titres de section qu'aujourd'hui, et c'est le style qui les pose. La couche 3 coûte environ 220 jetons par appel, payés seulement là où elle est activée (230 mesurés à la mise en œuvre). Sans `style`, rien ne change, à l'octet près : le schéma, la description et chaque fichier écrit.

| Point | Constat | Preuve |
| --- | --- | --- |
| Charte | En-tête avec logo et texte à droite, pied de page de mentions légales sur deux lignes, « Page n / N », police et couleurs de l'artisan, tableaux à filets, filigrane « BROUILLON » | Démontré : prototype rendu par LibreOffice 24.2 (facture d'une page) |
| Disposition | Titres masqués (relus, jamais imprimés), émetteur et client côte à côte, client encadré, récapitulatif à 42 % de la largeur aligné à droite, titres discrets, petit texte justifié | Démontré (même rendu) |
| Rapport | Page de garde sans en-tête, sommaire, titres numérotés (1., 2.1), légendes « Figure 1 — », grille de deux photos, page en paysage, conditions générales sur deux colonnes équilibrées | Démontré (rapport de 5 pages) |
| Blocs nouveaux | Signature à deux signataires, cases cochées et vides | Démontré (rendu) ; coût mesuré : 811 caractères, environ 220 jetons |
| Relecture | Titres masqués et numérotés relus correctement aujourd'hui ; les tableaux de disposition, la signature, le sommaire, les cases et les préfixes de légende demandent cinq changements du lecteur | Démontré (le lecteur actuel lancé sur les deux prototypes) |
| Sans `style`, rien ne change | Le style maison devient la valeur par défaut du style, et son XML reste celui d'aujourd'hui | Démontré à la mise en œuvre : 12 documents, 8 schémas et 8 descriptions identiques à ceux de `9ae0492` ; un test garde l'empreinte d'un devis |

**Trois points demandent une décision avant tout :**

1. **Où se déclarent la charte et la disposition (D-1).** Je propose la charte sur l'outil (`style:`) et la disposition sur chaque modèle (`templates: {facture: {file, style}}`). La disposition d'un modèle complète la charte, clé par clé.
2. **Le PDF (D-5).** Je propose `loom.word` seul dans ce lot. Le PDF demande de vraies polices (des fichiers TTF), alors que Word ne demande qu'un nom de police.
3. **Comment activer les trois blocs (D-6)** : par une clé à part, ou dès qu'un `style` est déclaré. Le nom de la clé est à choisir.

## Périmètre et état de référence

L'analyse porte sur ton commit `9ae0492` (« templete for word », égal à `origin/main`). Rien n'a été écrit dans `/home/denis/dev/loom_v2`, à part ce document. Les essais ont tourné dans l'espace de travail cloud de Claude, sur un clone de ce commit : Python 3.14.6, LibreOffice 24.2.7.

Contrairement aux deux lots précédents, **le prototype n'est pas une implémentation** : le lot est trop grand pour cela. C'est un essai de faisabilité. Deux documents (une facture, un rapport) ont été écrits à la main en WordprocessingML, avec les fonctions de `word/writer.py` (`_style`, `_run`, `_paragraph`, `_numbering`) et une partie XML par fonction visée. Ils ont été rendus par LibreOffice, puis relus par le lecteur actuel de `loom.word`. Les tailles de code données plus bas sont donc des estimations. Les deux fichiers d'essai sont joints dans la conversation, pour que tu les ouvres dans Word (§ À vérifier sur ton poste).

## Les documents visés

| Famille | Types de documents | Couverts par le lot |
| --- | --- | --- |
| Commerce et comptabilité | Devis, facture, avoir, bon de commande, bon de livraison, proposition commerciale, relance, mise en demeure | 8 sur 8 |
| Courrier | Lettre, courrier recommandé, attestation, certificat, convocation | 5 sur 5 (adresse du destinataire placée par `beside` et `space_before`) |
| Rapports et documents techniques | Rapport d'intervention, compte rendu de chantier, PV de réception, diagnostic, audit, cahier des charges, procédure, fiche technique | 8 sur 8 (sans notes de bas de page) |
| RH et administratif | Contrat, avenant, fiche de poste, attestation employeur, compte rendu de réunion, ordre du jour, note de service, règlement intérieur | 8 sur 8 (« Article 1 » par la numérotation des titres) |
| Formation | Support de cours, convention, attestation de fin de formation, feuille d'émargement, quiz | 4 sur 5 : l'émargement attend le lot 2 |
| Juridique | Statuts, PV d'assemblée, pouvoir, décision de l'associé unique | 4 sur 4 |
| Communication | Plaquette, fiche produit, newsletter, communiqué, menu, affiche | 1 sur 6 (le communiqué) : mise en page libre, hors du métier de l'outil |

**Total : 38 types sur 44.** C'est un décompte sur l'inventaire, pas une mesure d'usage. Il sert à vérifier que le vocabulaire proposé ne laisse pas de trou dans les documents courants d'un artisan ou d'une PME.

## Analyse fonctionnelle

### Le principe : le modèle de langage décrit, le style dispose

La règle actuelle de l'outil ne change pas : « Décrire ce que SONT les blocs, jamais leur apparence. » Le modèle de langage continue d'écrire `# Émetteur`, `# Client`, `# Description`… en titres de niveau 1, et le plan reste vérifié contre le modèle. Le fichier de disposition dit ensuite que ces deux sections se posent côte à côte et que leurs titres ne s'impriment pas. Le titre reste dans le fichier, invisible (`w:vanish`), si bien que la relecture retrouve le plan et que la réécriture passe la vérification.

Le contenu reste au modèle de langage, l'apparence au style. Un artisan change sa charte sans toucher au prompt, et un nouveau type de document se crée par deux fichiers : son modèle et sa disposition.

### La déclaration

```yaml
- python: loom.word
  images: true
  style: styles/charte.yaml                # la charte : tous les documents de l'outil
  templates:
    facture:
      file: templates/modele-facture.md
      style: styles/facture.yaml           # la disposition de ce modèle (facultatif)
    courrier: templates/modele-courrier.md # forme courte : la charte seule
  folders:
  - name: workspace
    path: workspace
    rights: [ read, write ]
```

- **Les fichiers sont lus au chargement**, comme les modèles : relatifs à `resources_dir`, contenus dans ce dossier, avec les fautes signalées dès `loom validate`. Le logo aussi, et son format (PNG ou JPEG) est vérifié au montage de l'outil.
- **Quatre cas :**
  - document qui suit un modèle avec disposition : la charte, complétée par la disposition ;
  - document qui suit un modèle sans disposition : la charte ;
  - `template: aucun`, ou outil sans modèles : la charte ;
  - outil sans `style` : le style maison d'aujourd'hui.
- **La disposition complète la charte, clé par clé** (D-3). Elle peut aussi en remplacer une valeur : une facture peut avoir des tableaux à filets quand la charte a des grilles.

### Couche 1 — la charte

| Clé | Ce qu'elle règle | Valeurs |
| --- | --- | --- |
| `page` | Format, orientation, marges | `size` : A4, A5, Letter ; `orientation` : portrait, paysage ; `margins` : `top`, `bottom`, `left`, `right` en cm |
| `fonts` | Police du corps et des titres | `body` : `{name, size}` ; `headings` : `{name}` (Word ne demande qu'un nom, D-10) |
| `colors` | Couleurs nommées, reprises ailleurs par leur nom | `text`, `primary`, `accent`, `muted`, `rule`, en hexadécimal |
| `paragraphs` | Interligne, espacement, justification | `line` (1,0 à 2,0), `after` (pt), `justify` |
| `title` | Le titre du document | `size`, `color`, `align`, `uppercase`, `rule` (filet dessous) |
| `headings` | Titres de niveaux 1 à 3 | par niveau : `size`, `color`, `uppercase` ; `numbering` : `aucune`, `decimale` (1., 1.1, 1.1.1), `article` (« Article 1 – ») |
| `tables` | Tous les tableaux | `borders` : grille, filets, horizontales, aucune ; `header` : `{fill, color}` ; `bands` ; `size` ; `width` : pleine, contenu ; `align` |
| `note` | L'encadré | `fill`, `border` |
| `captions` | Légendes numérotées | `numbered` : « Tableau 1 — », « Figure 1 — » (champ `SEQ`) |
| `images` | La grille d'images | `grid` : deux images `moitie` consécutives se posent côte à côte, chacune avec sa légende |
| `header`, `footer` | En-tête et pied de page | `left`, `center`, `right` : texte avec `{page}`, `{pages}`, `{titre}` ; `lines` : lignes centrées (mentions légales) ; `logo` : `{file, width, position}` ; `first_page` : afficher sur la première page |
| `watermark` | Filigrane | un texte : « BROUILLON », « DUPLICATA » |
| `toc` | Le bloc `sommaire` | `levels` (1 à 3), `pages` (numéros de page, D-7) |

```yaml
# prompts/styles/charte.yaml — la charte de l'artisan
fonts: { body: { name: Arial, size: 10 } }
colors: { text: "222222", primary: "0B5D3B", accent: "C0392B" }
title: { size: 22, align: droite, uppercase: true, rule: false }
headings:
  1: { size: 13, color: primary, uppercase: true }
tables:
  borders: filets
  header: { fill: "E8F1EC" }
header:
  logo: { file: images/logo.png, width: 3.5, position: gauche }
  right: "{titre}"
footer:
  lines:
  - "Dupont Plomberie SARL au capital de 8 000 € · 12 rue des Lilas, 69003 Lyon · SIRET 812 345 678 00021"
  - "TVA FR41 812345678 · Assurance décennale MAAF Pro, contrat 123456, France métropolitaine"
  right: "Page {page} / {pages}"
```

Aucune date n'est permise dans l'en-tête ni le pied de page. Un champ `DATE` ferait dépendre le PDF de l'heure, et deux documents identiques ne donneraient plus le même fichier. La date d'un document fait partie de son contenu.

### Couche 2 — la disposition des sections

Elle se fait section par section, par le titre de la section tel qu'il est écrit dans le modèle. Les titres sont comparés comme pour le plan : sans tenir compte de la numérotation, de la casse ni des accents. Un nom qui n'est pas une section du modèle est refusé au chargement.

| Clé | Effet |
| --- | --- |
| `title` | `visible` (par défaut), `masque` (relu, jamais imprimé), `discret` (petit, couleur d'accent) |
| `align` | gauche, centre, droite, justifie |
| `size` | `petit` : conditions générales, mentions |
| `box` | La section dans un cadre |
| `columns` | Texte sur 2 ou 3 colonnes équilibrées |
| `page_break` | Nouvelle page avant la section |
| `landscape` | La section sur des pages en paysage |
| `cover` | Page de garde : centrée, sans en-tête ni pied de page, suivie d'un saut de page |
| `space_before` | Espace avant, en cm (l'adresse d'un courrier dans la fenêtre de l'enveloppe) |
| `tables` | Les tableaux de la section : mêmes clés que dans la charte |

Au premier niveau, `beside` liste les sections posées côte à côte, avec leurs largeurs. Ces sections doivent se suivre dans le plan du modèle. Une section facultative absente laisse l'autre seule, en pleine largeur. `beside` ne se combine pas avec `columns`, `landscape` ni `cover` : le chargement le refuse.

```yaml
# prompts/styles/facture.yaml — la disposition du modèle facture
beside:
- { sections: [Émetteur, Client], widths: [55, 45] }
sections:
  Émetteur: { title: masque }
  Client: { title: masque, box: true }
  Description: { title: masque, tables: { width: contenu, align: droite } }
  Informations de paiement: { title: discret }
  Conditions et termes: { title: discret, size: petit, align: justifie }
```

Avec `width: contenu`, un tableau étroit (la moitié de la page au plus) prend sa largeur naturelle, à droite : c'est le récapitulatif à deux colonnes. Un plus large, comme celui des prestations, garde toute la largeur. Aucun indice de tableau n'est nécessaire. (Corrigé à la mise en œuvre : une première version donnait sa largeur naturelle à tout tableau, et les prestations aux désignations courtes devenaient un tableau étroit à droite.)

### Couche 3 — trois blocs nouveaux

Ce sont des contenus, pas de l'apparence : aucun style ne peut les faire exister. Ils sont donc ajoutés au vocabulaire, comme `graphique` et `image`.

| Bloc | Ce que le modèle de langage envoie | Rendu |
| --- | --- | --- |
| `signature` | `signers` : « Le client », « L'entreprise »… (4 au plus) ; `mention` : « Bon pour accord » | Une colonne par signataire : libellé, mention, « Date : », espace de signature, sans cadre |
| `sommaire` | rien d'autre que son type | La table des matières tirée des titres, profondeur et numéros de page selon `toc` |
| `liste` + `checkboxes` | chaque élément commence par `[x]` ou `[ ]`, la convention markdown | ☒ ou ☐ devant chaque élément |

La forme est la plus sobre possible. Un premier jet, avec des signataires en objets, une profondeur de sommaire et des cases en objets, coûtait 1 554 caractères (environ 420 jetons). La forme retenue en coûte 811 (environ 220), aide comprise.

### La relecture et la réécriture

Une fois relu, un document doit se réécrire tel quel, et la disposition est réappliquée par le style. Le lecteur rend donc les blocs, jamais la mise en page. Il en lit déjà correctement une partie :

- les titres masqués, relus en titres de niveau 1 : le style masqué dérive du titre 1 ;
- les titres numérotés, relus sans leur numéro : « Contexte » et non « 1. Contexte » ;
- l'en-tête, le pied de page, le filigrane et les sections, qui ne sont pas lus.

Cinq changements sont nécessaires :

| Ce qu'il relit aujourd'hui | Ce qu'il doit relire |
| --- | --- |
| Le tableau de disposition (émetteur et client) : un `tableau` d'une ligne, titres compris, qui casse le plan | Les blocs de chaque cellule, dans l'ordre (style de tableau `loom layout`) |
| La signature : un `tableau` | Le bloc `signature`, reconnu à son style |
| Le sommaire : un titre lu comme liste, puis une ligne par entrée (« Contexte\t? ») | Un bloc `sommaire` ; les entrées sont sautées |
| Les cases : « ☒\tFuite localisée » | `liste` avec `checkboxes`, « [x] Fuite localisée » |
| Les légendes : « Figure 1 — Avant », qui se doublerait à la réécriture | « Avant » : le préfixe tiré d'un champ `SEQ` est retiré |

Les images de la grille, aujourd'hui « non lues » parce qu'elles sont dans un tableau, reviennent avec le premier changement. `loom.memory` lit les `.docx` avec ce même lecteur : il y gagne aussi, puisque le texte côte à côte n'y est plus aplati en tableau.

### Ce que voit le modèle de langage

- **Charte et disposition : rien.** Ni le schéma ni la description ne changent. Le style n'est jamais envoyé au modèle de langage, et `action: template` ne rend que le modèle.
- **Les trois blocs : environ 220 jetons par appel**, s'ils sont activés (D-6).
- **Le résultat d'une écriture** ne change pas. Il dit déjà le modèle suivi, dont la disposition dépend.

### Hors du premier lot

- **Lot 2 :**
  - le style dans `loom.pdf` (D-5) ;
  - notes de bas de page et renvois ;
  - feuille d'émargement ;
  - polices embarquées dans le `.docx` ;
  - filigrane en image.
- **À part :** les présentations (`.pptx`) et la mise en page libre (plaquette, affiche).

## Analyse technique

### Ce que montre le prototype

| Fonction | En WordprocessingML | Rendu LibreOffice |
| --- | --- | --- |
| Charte dans `styles.xml` | Les mêmes styles qu'aujourd'hui, générés depuis des valeurs (`_style` existe déjà) | Conforme |
| Logo en en-tête | Partie `header1.xml`, ses relations propres, image en ligne, taquets centre et droite | Conforme |
| Mentions en pied de page | Paragraphes du `footer1.xml`, champs `PAGE` et `NUMPAGES` | Conforme |
| Filigrane | Forme VML `PowerPlusWaterMarkObject` dans l'en-tête, celle qu'écrit Word | Conforme, sous le texte |
| Titre masqué | Style `loom heading hidden`, `basedOn Heading1`, `w:vanish` | Non imprimé ; relu comme titre 1 |
| Côte à côte, encadré | Tableau sans bordure, style `loom layout`, cadre sur une cellule | Conforme |
| Tableau étroit à droite | `tblW` en twips, `jc right` | Conforme |
| Filets | Bordures haut, bas et horizontales intérieures seulement | Conforme |
| Titres numérotés | Une numérotation à trois niveaux liée aux styles `Heading1` à `Heading3` | « 1. », « 2.1 » ; texte relu sans le numéro |
| Légendes numérotées | Champ `SEQ Figure` / `SEQ Tableau` | « Figure 1 — Avant » |
| Page de garde | Section propre, `titlePg` (en-tête vide sur sa première page) | En-tête absent comme voulu ; centrage vertical non appliqué (piège 2) |
| Sommaire | Champ `TOC` marqué `dirty`, entrées pré-remplies avec liens et `PAGEREF` | Entrées et liens conformes ; numéros de page « ? » (piège 3) |
| Paysage | Section `w:orient="landscape"`, dimensions inversées | Page 4 en 842 × 595 pt |
| Deux colonnes | Section continue `w:cols num=2`, puis section continue finale | Colonnes équilibrées (piège 1) |
| Cases | ☐ U+2610, ☒ U+2612, police `Segoe UI Symbol` | Conformes (substitution par DejaVu Sans) |
| Signature | Tableau `loom layout`, une colonne par signataire | Conforme |

### Les pièges trouvés

1. **Les colonnes ne s'équilibrent que suivies d'une section continue.** Sans elle, le texte remplit la première colonne, et une section continue qui suit une page en paysage reste en paysage. Le rédacteur ferme donc toujours une section en colonnes par une section continue, et ouvre une nouvelle page après le paysage.
2. **LibreOffice 24.2 ignore le centrage vertical d'une page (`w:vAlign`).** La page de garde gagne donc un espace avant calculé, qui marche partout, plutôt que ce réglage.
3. **Le sommaire ne connaît pas les numéros de page.** Seul le traitement de texte les calcule. Word met à jour un champ marqué `dirty` à l'ouverture (à vérifier sur ton poste). LibreOffice garde ce qui est écrit, jusqu'à « Mettre à jour l'index ». D'où D-7.
4. **Word applique l'ordre du schéma, LibreOffice non.** Dans `numbering.xml`, toutes les définitions (`abstractNum`) précèdent les numérotations (`num`). Le premier prototype rendait bien dans LibreOffice avec l'ordre inverse, mais Word l'aurait refusé. Aucun test ne vérifie aujourd'hui cet ordre : le lot en ajoute un pour chaque partie nouvelle.
5. **Une police absente du poste du lecteur est remplacée.** Word n'embarque rien par défaut : une charte en « Inter » s'affiche en Calibri chez un client qui n'a pas Inter. Le chargement avertit si la police n'est pas d'une liste sûre : Arial, Calibri, Cambria, Georgia, Times New Roman, Verdana, Tahoma, Trebuchet MS, Garamond (D-10).

### Modifications prévues

| Fichier | Changement | Estimation |
| --- | --- | --- |
| `tools_builtin/_style_params.py` (nouveau) | Le style en Pydantic (`extra="forbid"`), la fusion charte + disposition, les contrôles (couleurs, bornes, sections du plan, `beside` contigu) | ~350 lignes |
| `_office_params.py`, `word_params.py` | `style` sur l'outil et sur `TemplateRef` | ~40 |
| `config/loader.py` | `_styles_read` : YAML et logo lus au chargement, sur le modèle de `_templates_read` | ~70 |
| `word/style.py` (nouveau) | Style résolu → `styles.xml`, numérotations, en-têtes, pieds, filigrane ; le style maison en est la valeur par défaut | ~400 |
| `word/writer.py` | Sections (paysage, colonnes, page de garde), tableaux de disposition, disposition appliquée par section, `SEQ`, trois blocs | ~350 modifiées |
| `word/reader.py` | Les cinq changements de relecture, et le bug B-4 | ~150 |
| `_document/arguments.py`, `content.py`, `tool.py` | Trois blocs, la variante qui les active, le style passé au rendu | ~200 |
| Tests | Charte, disposition, blocs, relecture, empreintes sans style, ordre du schéma | ~60 fonctions |

C'est le plus gros lot de l'outil. Je le coderais en deux étapes, livrables chacune :

1. **Charte et disposition.** Elles ne coûtent aucun jeton et ne touchent pas au vocabulaire.
2. **Les trois blocs et leur relecture.**

### Choix techniques et leurs raisons

- **Le style maison devient une valeur, pas du code.** Les constantes de `writer.py` (`NAVY`, `FONT`, `MARGIN`…) deviennent le style par défaut. `STYLES` se génère depuis lui, et un test garde les octets d'aujourd'hui.
- **Le tableau de disposition** plutôt que des colonnes de section pour `beside`. Il fixe les largeurs, encadre une cellule et garde chaque section dans sa colonne. Les colonnes de section ne servent qu'au texte qui coule (`columns`).
- **`w:vanish`** plutôt qu'un texte blanc ou minuscule pour le titre masqué. C'est la marque de Word pour « texte masqué ». Elle n'est pas imprimée, est relue, et ne change pas la mise en page.
- **Le style se résout au montage de l'outil**, une fois. À l'écriture, le rendu reçoit la charte et la disposition du modèle suivi, déjà fusionnées. Pas de disque à l'écriture.
- **Déterminisme tenu** : ni date, ni identifiant aléatoire. Les parties et les relations sont numérotées dans un ordre fixe.

## Bugs existants trouvés en route

- **B-4 — Une annulation de numérotation est lue comme une liste.** Un style qui annule la numérotation de son parent (`numId 0`, ce que fait Word pour « En-tête de table des matières » quand les titres sont numérotés) est lu comme un élément de liste. `_styles` ignore le `0`, puis `_style_meaning` va chercher la numérotation du parent. Correctif : un `numId 0` arrête la recherche. Il est indépendant des styles et pourrait passer avant.

## Effets de bord sur le reste de loom

- **`loom.memory`** lit les `.docx` avec `word.reader`. Les changements de relecture s'y appliquent, au bénéfice des documents mis en page.
- **`loom.pdf`** ne change pas dans ce lot si D-5 (a) est retenu. `style` y est refusé, avec un message qui le dit.
- **`loom validate`** affiche une ligne de plus : « style : charte.yaml ; facture → facture.yaml ».
- Aucun effet sur le moteur, la config des autres outils ni les contrats d'import. `_style_params` reste léger, comme `_templates`, parce que la validation de la config n'importe aucun outil (le test existant le vérifie).

## Plan de non-régression

- **Sans `style` :** même schéma, même description, même fichier, octet pour octet, que `9ae0492`, pour les 8 combinaisons `charts` × `images` × `templates`.
- **Avec un style vide (`{}`) :** même fichier qu'avec le style maison.
- **Ordre du schéma WordprocessingML :** vérifié dans chaque partie nouvelle (`header`, `footer`, `numbering`, `sectPr`).
- **Aller-retour :** chaque document du jeu d'essai, relu, rend le plan de son modèle, et se réécrit en passant la vérification du plan. (Un tableau relu ne rend que ses lignes, sans les types de colonne, comme avant les styles : l'aller-retour n'est pas identique à l'octet dès qu'il y a un tableau.)
- **Rendu LibreOffice :** les deux documents du prototype deviennent des tests de rendu, ignorés sans LibreOffice, comme ceux des images.

## Décisions

| N° | Question | Proposition |
| --- | --- | --- |
| D-1 | Où déclarer charte et disposition ? | (a) charte sur l'outil (`style`), disposition sur chaque modèle (`templates.<nom>.style`) ; (b) un seul fichier par modèle, charte comprise ; (c) dans l'en-tête du modèle markdown. **Je recommande (a)** : une charte par entreprise, une disposition par type de document, et le modèle markdown reste ce que lit le modèle de langage |
| D-2 | Format des fichiers de style | (a) YAML, clés en anglais comme toute la config, valeurs en français (`filets`, `masque`) ; (b) clés en français. **Je recommande (a)** |
| D-3 | Disposition et charte | (a) la disposition complète la charte, clé par clé ; (b) elle la remplace entièrement. **Je recommande (a)** |
| D-4 | Section inconnue dans une disposition | (a) refusée au chargement ; (b) avertissement. **Je recommande (a)**, comme D-1 et D-2 des modèles |
| D-5 | Le PDF | (a) `loom.word` seul dans ce lot, `style` refusé sur `loom.pdf` jusqu'au lot 2 ; (b) les deux ensemble. **Je recommande (a)** : le PDF demande des fichiers de police, et doublerait le lot |
| D-6 | Activer les trois blocs | (a) une clé booléenne à part (nom à choisir) ; (b) dès qu'un `style` est déclaré ; (c) une clé par bloc. **Je recommande (a)** : une signature sert aussi sans charte |
| D-7 | Numéros de page du sommaire | (a) numéros, mis à jour par Word à l'ouverture, et par « Mettre à jour l'index » dans LibreOffice ; (b) jamais de numéros, seulement des liens ; (c) au choix de la charte (`toc.pages`), (a) par défaut. **Je recommande (c)** |
| D-8 | La page de garde | (a) une disposition (`cover`) sur une section du modèle ; (b) un bloc. **Je recommande (a)** : zéro jeton, et c'est de l'apparence |
| D-9 | La grille d'images | (a) une option de la charte (`images.grid`) ; (b) un bloc. **Je recommande (a)** |
| D-10 | Les polices | (a) un nom seulement, avec un avertissement hors de la liste sûre ; (b) embarquer le fichier dès ce lot. **Je recommande (a)**, et l'embarquement au lot 2 |
| D-11 | Le découpage | (a) deux étapes (charte et disposition, puis blocs) ; (b) un seul lot. **Je recommande (a)** |
| D-12 | La version | 2.0.20 (l'actuelle, comme pour les modèles) ou 2.1.0 (le vocabulaire s'étend) |
| D-13 | B-4 | Le corriger avec ce lot, ou à part avant |

## À vérifier sur ton poste

Deux fichiers d'essai sont joints dans la conversation (`facture-charte.docx`, `rapport-charte.docx`). À ouvrir dans Word :

1. Word les ouvre sans message de réparation.
2. Le filigrane « BROUILLON » paraît sous le texte de la facture.
3. Les titres « Émetteur », « Client » et « Description » ne s'impriment pas. Ils restent visibles à l'écran seulement avec l'affichage des marques de mise en forme.
4. À l'ouverture du rapport, Word met à jour le sommaire et y écrit les numéros de page (champ `dirty`), avec ou sans question. C'est ce qui tranche D-7.
5. La page de garde est centrée verticalement dans Word (`vAlign`).
6. Les cases ☐ et ☒ s'affichent en `Segoe UI Symbol`.

## Mise en œuvre

### Étape 1 : la charte et la disposition (9 novembre 2026)

Codée sur un clone de `9ae0492`, déposée dans ton dépôt sans commit.

- **Tests :** 4 234 verts avec tous les extras, soit 4 205 plus 29 nouveaux. En noyau seul, 3 225 verts (+27 : les deux tests de rendu LibreOffice demandent pypdf, et sont sautés sans lui).
- **Qualité :** pyright strict à 0, ruff propre (`check` et `format --check`), les 6 contrats d'import tenus.
- **Sans style, rien ne change :** 12 documents (les 8 combinaisons `charts` × `images` × `templates`, avec et sans `aucun`), 8 schémas et 8 descriptions, comparés à l'octet avec ceux de `9ae0492`. Un style vide (`{}`) donne le même fichier. Un test garde l'empreinte du devis de `test_word.py`.
- **Coût :** aucun token. Le schéma et la description de l'outil sont les mêmes avec ou sans style.

| Fichier | Changement | Lignes |
| --- | --- | --- |
| `tools_builtin/_style_params.py` (nouveau) | Le fichier de style en Pydantic, la fusion, les contrôles, le style résolu (`Style()` : le style maison) | 730 |
| `tools_builtin/word/style.py` (nouveau) | `styles.xml`, numérotations, en-têtes et pieds, filigrane | 388 |
| `word/writer.py` | Sections du modèle, disposition, sections Word, en-têtes et logo, légendes numérotées, grille | +909 / −301 sur 10 fichiers |
| `word/reader.py` | Tableaux de disposition traversés, préfixe `SEQ` retiré, B-4 | ″ |
| `word/tool.py` | Styles résolus au montage (`read_styles`), choisis au rendu, `loom validate` | ″ |
| `config/loader.py` | `_styles_read` : YAML, logos, contrôle contre le plan | ″ |
| `_office_params.py`, `word_params.py`, `pdf_params.py` | `style` sur l'outil et sur un modèle ; refus sur `loom.pdf` | ″ |
| `_document/content.py`, `word/image.py`, `word/chart.py` | Largeur naturelle d'un tableau, largeur demandée d'une image ; dessin d'image extrait ; graphique réduit à la place disponible | ″ |
| `tests/unit/test_word_styles.py` (nouveau) | 24 fonctions, 29 essais | 705 |

**Ce qui diffère de l'analyse :**

- Les valeurs de numérotation sont `aucune`, `decimale` et `article` (au lieu de `1.`), et la charte nomme deux couleurs de plus : `muted` (légendes, en-tête, pied) et `rule` (filets fins).
- `width: contenu` ne réduit qu'un tableau étroit (la moitié de la page au plus) ; un plus large garde toute la largeur (§ Couche 2).
- Un pied de page déclaré remplace celui de la maison. Pour garder les numéros de page, il faut les écrire (`right: "Page {page} / {pages}"`).
- `watermark: ""` dans une disposition retire le filigrane de la charte.
- Une page de garde prend le titre du document, et un espace fixe en haut (6 cm, ou `space_before`) plutôt qu'un centrage vertical (piège 2).
- Les styles propres à une disposition (titre masqué, titre discret, en-tête, tableau de disposition) ne sont écrits que si le document s'en sert.
- L'aller-retour : un document relu rend le plan de son modèle et se réécrit en passant la vérification. Il ne redonne pas le même fichier quand il a un tableau, parce que la relecture d'un tableau ne rend que ses lignes, comme avant les styles.

**Essais réels :** une facture (logo, émetteur et client côte à côte, client encadré, titres masqués et discrets, prestations en pleine largeur, récapitulatif à droite, mentions en pied de page, filigrane) et un rapport (page de garde sans en-tête, titres numérotés, grille de deux photos, légendes numérotées, page en paysage, conditions générales sur deux colonnes) ont été écrits par l'outil, rendus par LibreOffice 24.2, puis relus.

**Instabilité vue en route :** `test_memory_gpu_server.py::test_the_log_never_holds_the_texts` a échoué une fois, quand les deux suites (extras et noyau) tournaient ensemble sur la machine. Il passe seul, trois fois sur trois, avec tout son fichier, et la suite complète relancée seule est verte. Il est sans lien avec ce lot.

### Étape 2 : les trois blocs (`extra_blocks`), 9 novembre 2026

Codée sur la même base, à la suite de l'étape 1, et déposée sans commit.

- **Tests :** 4 242 verts avec tous les extras (4 234 plus 8), et 3 232 en noyau seul (+7, car le test de rendu LibreOffice demande pypdf). Les 8 nouvelles fonctions sont dans `tests/unit/test_word_styles.py`.
- **Qualité :** pyright strict à 0, ruff propre, les 6 contrats d'import tenus.
- **Sans `extra_blocks`, rien ne change :** les empreintes de `9ae0492` (12 documents, 8 schémas, 8 descriptions) sont encore identiques.
- **Coût mesuré :** 860 caractères, environ 230 tokens par appel (598 de schéma, 262 de description), contre 811 estimés dans l'analyse.

| Bloc | Écrit | Relu |
| --- | --- | --- |
| `signature` | Tableau « loom signature », une colonne par signataire : libellé (« loom signer »), mention (« loom mention »), « Date : », « Signature : », 2 cm | `{"type": "signature", "signers": […], "mention": …}` |
| `sommaire` | « TOC Heading » « Sommaire », puis un champ `TOC` `dirty` aux entrées préremplies (numéro et titre, lien vers un signet `_Toc_loom_n`, `PAGEREF`) | `{"type": "sommaire"}`, une fois ; les entrées d'un sommaire de Word aussi |
| Liste à cocher | ☒ / ☐ et une tabulation, styles « loom checkbox » et « loom checkbox 2 » | `liste` avec `checkboxes: true`, éléments en `[x]` / `[ ]` |

**Ce qui diffère de l'analyse :**

- Le sommaire n'a pas de champ dans le bloc : sa profondeur et ses numéros de page viennent de la charte (`toc`, D-7).
- Un outil sans `extra_blocks` qui relit ces blocs le dit dans `notes`, comme pour les graphiques.
- La liste à cocher n'est pas numérotée.
- La liste, la signature et le sommaire relus puis réécrits redonnent le même fichier.

**À vérifier sur ton poste (Word) :** le sommaire du rapport joint se met à jour à l'ouverture et écrit les numéros de page (D-7), et les cases s'affichent en Segoe UI Symbol.

