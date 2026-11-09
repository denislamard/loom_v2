# Analyse d'impact — styles de document, lot 2 (loom_v2)

9 novembre 2026 · Denis Lamard

## En bref

Le lot 1 des styles (`docs/outil-word-styles-analyse.md`, commits `d8f025d` et `0f38f5f`) a laissé six sujets de côté :

1. le style dans `loom.pdf`, refusé jusqu'ici (D-5 du lot 1), avec les trois blocs d'`extra_blocks` ;
2. les notes de bas de page ;
3. les renvois (« voir § 3 ») ;
4. la feuille d'émargement ;
5. les polices embarquées ;
6. le filigrane en image.

**Verdict : les six sont faisables, et aucun ne change le principe du lot 1.** Le style reste dans des fichiers de l'agent, et le modèle de langage n'en voit rien. Quatre sujets ne coûtent aucun jeton : le PDF, les polices, le filigrane en image, et les blocs que `loom.pdf` gagne. Trois ajoutent du vocabulaire, soit environ 105 jetons de plus pour `extra_blocks` : la note de bas de page, le renvoi et l'émargement.

| Sujet | Constat | Preuve |
| --- | --- | --- |
| Style dans `loom.pdf` | La facture et le rapport du lot 1, depuis le même document préparé (`prepare`) et le même style résolu (`resolve`) : logo, mentions, « Page n / N », filigrane, émetteur et client côte à côte, client encadré, filets, récapitulatif à droite, titres masqués et discrets, petit texte justifié, page de garde, titres numérotés, cases à cocher, grille de photos et légendes, page en paysage, deux colonnes équilibrées, signature | Démontré : prototype reportlab (462 lignes), rendus regardés |
| Sommaire en PDF | Les vrais numéros de page, sans mise à jour (`multiBuild` de reportlab) ; c'est mieux qu'en Word, où le traitement de texte les calcule | Démontré : « 1. Contexte … 2 », « 3. Mesures … 3 » |
| Coût du PDF | Rapport de 4 pages avec sommaire : 0,12 s ; facture : 5 Ko en polices standard | Démontré (mesure) |
| Notes de bas de page (Word) | `footnotes.xml`, la référence en exposant, la note en bas de page | Démontré : rendu LibreOffice 24.2 |
| Renvoi (Word) | Signet sur le titre visé, champ `REF \r` (son numéro) et `PAGEREF` : LibreOffice recalcule « § 3 (page 1) » à l'ouverture | Démontré |
| Émargement | Un tableau aux lignes d'au moins 1,5 cm (`trHeight`), les cases vides pour signer | Démontré (Word, rendu) |
| Police embarquée (Word) | Police obfusquée (`fontTable.xml`, `.odttf`, clé GUID) : LibreOffice s'en sert quand elle n'est pas installée, **si la police permet l'édition** (`fsType`) ; refusée pour une police « aperçu et impression seulement » | Démontré : le PDF de LibreOffice nomme Bitstream Vera Sans, absente du poste ; avec le `fsType` d'origine (0x4), il prend DejaVu Sans |
| Police embarquée (PDF) | Police TrueType embarquée en sous-ensemble ; le fichier reste le même octet pour octet | Démontré (deux rendus, même empreinte) |
| Filigrane en image | Forme VML de l'en-tête (« WordPictureWatermark »), estompée (`gain`, `blacklevel`), centrée, derrière le texte | Démontré ; sans `stroked="f"`, LibreOffice l'entourait d'un cadre noir (piège 3) |
| Word | Notes, renvois, police embarquée, filigrane en image dans Word lui-même | À vérifier sur ton poste (§ À vérifier) |

**Trois points demandent une décision avant tout :**

1. **Le périmètre du PDF (D-1).** Je recommande la parité complète en une fois : charte, disposition et trois blocs. Le prototype montre que tout passe, et le vocabulaire est commun.
2. **Les notes de bas de page en PDF (D-4).** reportlab ne sait pas en faire. Je recommande des notes en fin de document, numérotées comme en Word, et de vraies notes de bas de page plus tard.
3. **Les droits des polices embarquées (D-8).** Je recommande de refuser au chargement une police qui ne permet pas l'édition : LibreOffice l'ignore, et Word restreint l'édition du document (à vérifier).

## Périmètre et état de référence

L'analyse porte sur ton commit `bb36b36` (« fix validate side_effects warning on provided tools (B-5) », égal à `origin/main`). Rien n'a été écrit dans `/home/denis/dev/loom_v2`, à part ce document : ni commit, ni push. Les essais ont tourné dans l'espace de travail cloud de Claude, sur un clone de ce commit : Python 3.14.6, reportlab 5.1.0, LibreOffice 24.2.7.

Comme pour le lot 1, **les prototypes sont des essais de faisabilité, pas une implémentation** :

- `spike_pdf.py` (462 lignes) rend la facture et le rapport du lot 1 avec reportlab. Il part de `prepare` et de `resolve`, et n'est pas branché sur l'outil `pdf` ;
- `spike_docx.py` (157 lignes) fait écrire un document par `loom.word`, puis y ajoute à la main les parties des notes, du renvoi, de l'émargement, de la police et du filigrane.

Les tailles de code données plus bas sont donc des estimations.

## 1. Le style dans `loom.pdf`

### Ce qui se transpose tel quel

Le style résolu du lot 1 (`Style`, dans `_style_params.py`) suffit au PDF : aucune clé nouvelle, aucun changement au chargement. Chaque élément a son équivalent reportlab :

| Style | En PDF (reportlab) |
| --- | --- |
| Page, marges | `BaseDocTemplate`, un `PageTemplate` par sorte de page : portrait, paysage, page de garde |
| En-tête, pied, logo, `{page}`, `{pages}`, `{titre}` | Dessinés à chaque page (`onPage`) ; `{pages}` vient d'un premier rendu, comme aujourd'hui pour « Page n / N » |
| Filigrane (texte) | Dessiné en diagonale, gris à 45 % d'opacité, avant le contenu de la page |
| Titres, couleurs, majuscules, numérotation | `ParagraphStyle` ; les numéros (« 2.1 », « Article 3 – ») calculés par l'outil, comme pour le sommaire de Word |
| Titre masqué, discret | Masqué : non posé, puisque le PDF ne se relit pas en blocs. Discret : petit, couleur d'accent |
| Côte à côte, encadré | `Table` d'une ligne, une cellule par section, `BOX` sur la cellule encadrée |
| Colonnes | `BalancedColumns` (2 ou 3, équilibrées) |
| Paysage | `NextPageTemplate("landscape")` et saut de page, puis retour au portrait |
| Page de garde | Son gabarit sans en-tête ni pied, l'espace en haut, puis saut de page |
| Tableaux (`grille`, `filets`, `horizontales`, `aucune`, en-tête, bandes, `contenu`) | `TableStyle` ; la même règle `contenu` qu'en Word (un tableau étroit prend sa largeur) |
| Légendes numérotées, grille d'images | Comptées par l'outil ; deux images côte à côte dans une `Table` |
| `signature` | `Table`, une colonne par signataire |
| `sommaire` | `TableOfContents` et `multiBuild` : **les vrais numéros de page**, sans mise à jour |
| Liste à cocher | Cases **dessinées** (un carré, une croix) : Helvetica n'a pas ☐ ni ☒, qui s'imprimeraient en carré noir |

### Ce qui diffère du Word

- **Les polices.** Le PDF a trois familles standard, qui ne s'embarquent pas : Helvetica, Times et Courier. Arial, Helvetica et Calibri s'y rendent en Helvetica, Times New Roman en Times, Courier New en Courier, comme aujourd'hui. Une autre police demande son fichier TrueType (§ 5) ; sans fichier, elle se rend en Helvetica, et `loom validate` le dit (D-2).
- **Le sommaire** a ses numéros de page dès l'écriture. Il faut un rendu de plus : un pour compter les pages, puis `multiBuild` (deux passes) pour le sommaire. Le rapport de 4 pages se rend en 0,12 s.
- **La relecture** ne change pas : `loom.pdf` lit le texte des pages (pypdf), pas des blocs.
- **Les en-têtes et pieds** sont dessinés à des positions absolues, et non par des taquets : sur une page en paysage, la zone de droite suit le bord de la page couchée. C'est mieux qu'en Word, où les taquets suivent la page portrait (limite connue du lot 1).

### La configuration

`pdf_params.py` retire ses deux refus (`style`, `extra_blocks`) et gagne les mêmes clés que `loom.word` : `style`, `templates.<nom>.style` et `extra_blocks`. Le chargeur (`_styles_read`) et les contrôles (`check_charte`, `check_layout`) servent tels quels. Un même fichier de style peut servir aux deux outils d'un agent : la facture en Word pour l'artisan, en PDF pour l'envoi.

### Sans style, rien ne change

Comme pour Word : sans `style`, le style maison actuel de `writer.py` (Helvetica 10,5, titres bleu nuit, grille) reste le même, octet pour octet. C'est un point d'attention : le style maison du PDF n'est pas exactement celui de Word (le corps est en 10,5 pt et non en 11). `Style()` ne peut donc pas servir de style maison au PDF sans changer ses fichiers. Je propose un `HOUSE_PDF`, ses valeurs reprises de `writer.py`, et un test d'empreintes comme pour Word (D-3).

## 2. Les notes de bas de page

**Dans le texte**, par la syntaxe en ligne de pandoc, que les modèles connaissent :

```json
{"type": "paragraphe", "text": "La pose suit le DTU 60.1^[Documents techniques unifiés, plomberie sanitaire.] ; …"}
```

- **En Word :**
  - une partie `footnotes.xml` (les deux séparateurs, `id` -1 et 0, puis une note par appel), et sa relation et son type de contenu ;
  - `<w:footnotePr>` dans `settings.xml`, que Word exige quand il y a des séparateurs ;
  - deux styles, « footnote text » et « footnote reference » (en exposant) ;
  - la référence dans le texte : `<w:footnoteReference w:id="1"/>`.

  Démontré dans LibreOffice : l'appel « 1 » en exposant, la note en bas de page sous un filet.
- **La relecture** : le lecteur saute aujourd'hui `footnoteReference` (`_SKIPPED`). Il lira `footnotes.xml` et rendra la note à sa place, en `^[…]`. Un document relu se réécrit tel quel. `loom.memory` y gagne : le texte des notes devient cherchable.
- **En PDF** : reportlab n'a pas de notes de bas de page. Il y a trois voies (D-4) :
  - (a) des notes en fin de document, sous un titre « Notes », appelées par leur numéro en exposant, avec un lien ;
  - (b) les mêmes, en fin de section ;
  - (c) de vraies notes de bas de page : un cadre de bas de page qu'on rétrécit page par page. reportlab le permet, mais c'est le morceau le plus délicat du lot.
- **Les contrôles** :
  - une note se met dans un paragraphe, un élément de liste, un encadré, une citation ou une cellule de tableau ; pas dans un titre ni une légende (refus clair) ;
  - pas de note dans une note ;
  - 500 caractères au plus par note, 100 notes au plus par document ;
  - un `^[` sans `]` est refusé, avec l'endroit.

## 3. Les renvois

Le problème : avec des titres numérotés par le style, **le modèle de langage ne connaît pas les numéros**. Il ne peut pas écrire « voir § 3 », puisque c'est le style qui numérote. Le renvoi nomme donc le titre :

```json
{"type": "paragraphe", "text": "Les réserves sont détaillées en §[Réserves]."}
```

- **En Word :**
  - un signet sur le titre visé (`_Ref_loom_n`) ;
  - à la place du renvoi, si les titres sont numérotés : « § » et un champ `REF … \r \h`, qui donne son numéro ;
  - sinon, le titre entre guillemets, avec un lien (`\h`).

  LibreOffice recalcule le champ à l'ouverture : la valeur écrite « 3. » s'affiche « 3 ».
- **Le numéro de page** (« (page 4) ») est possible par `PAGEREF`, et démontré. Je ne le propose pas par défaut (D-5) : en Word, il a la même limite que le sommaire.
- **Les contrôles** : le titre visé doit être un titre du document, comparé comme le plan (sans numérotation, casse ni accents). Sinon, le refus liste les titres du document. Un titre masqué ne peut pas être visé.
- **La relecture** rend `§[Titre]`, d'après le signet.
- **En PDF** : le numéro calculé par l'outil, et un lien interne vers le titre.

## 4. La feuille d'émargement

Un bloc, sous `extra_blocks` :

```json
{"type": "emargement", "participants": ["Jeanne Martin", "Paul Durand"], "sessions": ["Lundi matin", "Lundi après-midi"]}
```

- **Le rendu** : un tableau avec une colonne « Participant » puis une colonne par session. Les lignes font au moins 1,5 cm (`trHeight`, `hRule="atLeast"`), les cases sont vides pour signer, et l'en-tête se répète à chaque page. Il porte un style de tableau « loom émargement », que la relecture reconnaît, pour rendre le bloc et non un tableau. Démontré en Word ; en PDF, ce sont les `rowHeights` d'une `Table`.
- **Les bornes** : 100 participants, 10 sessions, et 80 caractères par libellé.
- **Pourquoi un bloc**, et pas une option de tableau (D-6) : les types de colonne sont communs à `excel`, et une colonne « signature » n'a pas de sens dans un classeur.

## 5. Les polices embarquées

Le lot 1 nomme une police sans l'embarquer (D-10 du lot 1). Hors de la liste sûre, elle est remplacée chez qui ne l'a pas. Le lot 2 donne ses fichiers :

```yaml
fonts:
  body:
    name: Inter
    size: 10
    files: { regular: polices/Inter-Regular.ttf, bold: polices/Inter-Bold.ttf, italic: polices/Inter-Italic.ttf }
```

- **En Word :**
  - chaque fichier est obfusqué selon ECMA-376 : les 32 premiers octets, XOR une clé GUID dont les octets sont lus à l'envers. C'est le même ordre que le code de LibreOffice ;
  - il est rangé en `word/fonts/fontN.odttf`, nommé dans `fontTable.xml` (`embedRegular`, `embedBold`, `embedItalic`, `embedBoldItalic`, avec `fontKey`) ;
  - `<w:embedTrueTypeFonts/>` est posé dans `settings.xml` ;
  - la clé GUID est tirée de l'empreinte du fichier, pour que le `.docx` reste le même octet pour octet.
- **En PDF** : la police est enregistrée (`TTFont`) et embarquée en sous-ensemble, et le fichier reste reproductible (démontré). Comme ses glyphes couvrent ☐ et ☒, la liste à cocher peut alors les écrire au lieu de les dessiner.
- **Les règles, contrôlées au chargement :**
  - **TrueType seulement** : un fichier `.ttf` avec une table `glyf`. Word n'embarque que du TrueType, et reportlab ne lit pas les OpenType CFF. Un `.otf` CFF ou un `.woff2` (comme les polices de ton outil `wireframe`) est refusé, avec ce qu'il faut à la place ;
  - **les droits d'embarquement** (`fsType` de la table OS/2) : installable (0) ou modifiable (8) acceptés ; « restreinte » (2) refusée ; « aperçu et impression » (4), voir D-8. Démontré : LibreOffice ignore une police à 4 et prend une police de remplacement ;
  - **2 Mo par fichier, 4 fichiers par famille** (corps et titres).
- **La licence** : loom ne peut lire que `fsType`, pas la licence elle-même. La documentation dira que l'embarquement suit la licence de la police ; l'OFL (Inter et les polices Google) le permet.
- **Le poids** : Word reçoit le fichier entier, soit 300 Ko à 1 Mo par graisse selon la police, alors que le PDF n'embarque que les glyphes employés. `loom validate` dira la taille ajoutée à chaque `.docx`.

## 6. Le filigrane en image

```yaml
watermark: { image: images/tampon.png, width: 12 }   # ou toujours un texte : watermark: BROUILLON
```

- **En Word** : la forme VML que Word écrit pour un filigrane image (`WordPictureWatermark`), avec l'image dans l'en-tête et ses réglages « estompé » (`gain="19661f"`, `blacklevel="22938f"`). Elle est centrée dans la page, derrière le texte. Démontré dans LibreOffice.
- **En PDF** : l'image estompée par Pillow (déjà là avec reportlab), dessinée à chaque page avant le contenu.
- **Comme le texte** : `watermark: ""` dans une disposition retire celui de la charte. L'image est lue au chargement, comme un logo : PNG ou JPEG, 2 Mo au plus.

## Ce que voit le modèle de langage

| Ajout | Jetons par appel | Où |
| --- | --- | --- |
| Style dans `loom.pdf` | 0 | Le style ne passe pas par le modèle |
| Polices embarquées, filigrane en image | 0 | Idem |
| `extra_blocks` sur `loom.pdf` | ~230 | Les mêmes que sur `loom.word`, seulement si déclaré |
| Notes de bas de page `^[…]` | ~30 | Une ligne d'aide |
| Renvois `§[Titre]` | ~30 | Une ligne d'aide |
| `emargement` | ~45 | Le bloc et sa ligne d'aide |

Sous `extra_blocks` (D-7), le coût passe d'environ 230 à environ 335 jetons par appel.

## Modifications prévues

| Fichier | Changement | Estimation |
| --- | --- | --- |
| `pdf/writer.py` | Le rendu à partir d'un `Style` : gabarits de page, en-têtes, filigrane, disposition, les trois blocs ; `HOUSE_PDF` ; notes en fin de document | +600 |
| `pdf/tool.py`, `pdf_params.py` | `style`, `templates.*.style`, `extra_blocks` ; résolution des styles au montage, comme `word/tool.py` (`read_styles` à partager) | +80 |
| `_style_params.py` | `fonts.*.files`, `watermark` en objet (`image`, `width`) ; contrôles TrueType et `fsType` | +120 |
| `config/loader.py` | Lecture des polices et de l'image du filigrane, comme les logos | +50 |
| `_document/content.py` | `^[…]` et `§[…]` dans le texte (`inline`) ; le bloc `emargement` | +150 |
| `_document/arguments.py`, `tool.py` | `emargement`, les lignes d'aide | +40 |
| `word/writer.py`, `word/style.py` | `footnotes.xml`, signets et `REF`, émargement, `fontTable.xml` et `.odttf`, filigrane image | +300 |
| `word/reader.py` | Notes relues en `^[…]`, renvois en `§[…]`, émargement en bloc | +100 |
| Tests | PDF (parité, empreintes sans style, polices), notes, renvois, émargement, polices, filigrane image, relecture | ~60 fonctions |

C'est un lot de la taille du lot 1. Je le coderais en trois étapes, livrables chacune (D-9) :

1. le style dans `loom.pdf`, avec `extra_blocks` ;
2. les polices embarquées et le filigrane image, en Word et en PDF ;
3. les notes, les renvois et l'émargement.

## Pièges trouvés

1. **Le premier gabarit de page** : reportlab prend le premier `PageTemplate` de la liste pour la première page. Le prototype commençait toujours par la page de garde, et la facture sortait sans en-tête ni filigrane. Le gabarit de la première page se choisit d'après la première section.
2. **Les droits des polices** : une police « aperçu et impression » (Vera, `fsType 0x4`) est embarquée sans erreur, mais LibreOffice ne s'en sert pas. Sans contrôle au chargement, l'utilisateur croirait sa police embarquée.
3. **Le filigrane image** : sans `stroked="f"` (que Word met dans sa définition de forme `_x0000_t75`), LibreOffice entoure l'image d'un cadre noir.
4. **Le renvoi** : `REF \r` rend le numéro tel que le style l'écrit. Avec `numbering: article`, il rendrait « Article 3 – » au lieu de « 3 » ; le format exact est à vérifier dans Word, puis à fixer (`\r` ou `\n`).
5. **Les cases en PDF** : ☐ et ☒ ne sont pas dans le jeu WinAnsi des polices standard, et le contrôle actuel des glyphes absents les signalerait. Elles sont donc dessinées, ou écrites avec une police embarquée qui les a.

## Effets de bord

- **`loom.memory`** lit les notes de bas de page des `.docx`, grâce au changement du lecteur.
- **`loom validate`** montre le style de `pdf`, et la taille des polices embarquées.
- **Le moteur, la config des autres outils et les contrats d'import** ne changent pas. Les polices sont lues par le chargeur comme les logos ; leurs tables (`fsType`, `glyf`) se lisent avec la bibliothèque standard, sans fontTools.

## Plan de non-régression

- **Sans style** : `loom.pdf` écrit le même fichier qu'à `bb36b36`, octet pour octet, et `loom.word` le même qu'aujourd'hui (empreintes, comme au lot 1).
- **Sans `^[` ni `§[`** : le texte est inchangé. Un texte qui contient littéralement `^[` sans fermeture est refusé ; c'est le seul changement de comportement, et il demande l'échappement `\^[`.
- **Aller-retour Word** : notes, renvois et émargement relus puis réécrits redonnent le même fichier.
- **Rendus LibreOffice** : les deux PDF et le `.docx` des prototypes deviennent des tests de rendu, sautés sans LibreOffice.

## Décisions

| N° | Question | Proposition |
| --- | --- | --- |
| D-1 | Le périmètre du PDF | (a) parité complète : charte, disposition et `extra_blocks` ; (b) charte et disposition d'abord, les blocs ensuite. **Je recommande (a)** : le prototype rend tout, et le vocabulaire est commun |
| D-2 | Une police de la charte sans fichier, en PDF | (a) rendue en Helvetica, Times ou Courier selon l'équivalent métrique, et sinon en Helvetica, avec un avertissement de `loom validate` ; (b) refusée sur `loom.pdf`. **Je recommande (a)** |
| D-3 | Le style maison du PDF | (a) `HOUSE_PDF`, les valeurs de `writer.py`, le même fichier sans style ; (b) le style maison de Word (11 pt), qui change les PDF sans style. **Je recommande (a)** |
| D-4 | Les notes de bas de page en PDF | (a) en fin de document ; (b) en fin de section ; (c) de vraies notes de bas de page. **Je recommande (a) maintenant**, et (c) plus tard si le besoin se confirme |
| D-5 | Le renvoi | (a) le numéro (ou le titre) et un lien ; (b) plus le numéro de page. **Je recommande (a)** |
| D-6 | L'émargement | (a) un bloc `emargement` ; (b) un type de colonne « signature » dans `tableau`. **Je recommande (a)** |
| D-7 | L'activation de `^[…]`, `§[…]` et `emargement` | (a) sous `extra_blocks` ; (b) une clé à part. **Je recommande (a)** |
| D-8 | Une police « aperçu et impression » (`fsType 4`) | (a) refusée au chargement ; (b) acceptée avec un avertissement. **Je recommande (a)** : LibreOffice l'ignore, et Word restreint l'édition (à vérifier) |
| D-9 | Le découpage | (a) trois étapes : PDF, puis polices et filigrane image, puis notes, renvois et émargement ; (b) un seul lot. **Je recommande (a)** |
| D-10 | La forme du filigrane image | (a) `watermark: {image, width}`, le texte restant accepté ; (b) une clé à part, `watermark_image`. **Je recommande (a)** |
| D-11 | La version | 2.0.20 (l'actuelle) ou 2.1.0 |

## À vérifier sur ton poste

`lot2.docx` est joint dans la conversation. À ouvrir dans Word :

1. La note de bas de page s'affiche en bas de page, sous un filet.
2. Le renvoi rend « § 3 (page 1) », et un clic va au titre « Réserves ». Après avoir passé les titres en `numbering: article`, noter ce que rend le champ (piège 4).
3. La police embarquée est Bitstream Vera Sans d'origine, marquée « aperçu et impression » (`fsType 0x4`) ; elle n'est sans doute pas installée sur ton poste. Noter ce que fait Word : il s'en sert pour afficher et imprimer, il ouvre le document en lecture seule, ou il la remplace. C'est ce qui tranche D-8. Dans LibreOffice, elle est remplacée ; une copie marquée installable, gardée dans l'espace de travail et non jointe (la licence de Vera interdit de diffuser une version modifiée sous son nom), y était bien employée.
4. Le filigrane en image est estompé, centré, derrière le texte, sans cadre.
5. L'émargement : les lignes font bien 1,5 cm, et l'en-tête se répète si le tableau passe sur une autre page.
