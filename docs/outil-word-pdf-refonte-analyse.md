# Analyse d'impact — la forme des documents hors de l'outil (`loom.word`, `loom.pdf`) loom_v2

10 novembre 2026 · Denis Lamard

> **Décisions prises le 10/11/2026** (conversation du jour) :
>
> - le modèle de langage ne fait que du contenu ; l'outil ne contient que des mécanismes ; la forme est dans deux fichiers, le style et le modèle de document ;
> - le style maison sort du code (`default.yaml`) ;
> - des styles nommés (`base`, `description`), choisis par le modèle de document (`styles`) ou proposés au modèle de langage ;
> - le modèle de document en YAML ; le modèle de langage remplit des `sections`, et l'outil écrit leurs titres ;
> - tous les noms en anglais, `loom.excel` compris ;
> - la parité du PDF ;
> - la liste `blocks` à la place de `charts`, `images` et `extra_blocks` ;
> - les nouveaux contenus : champs, code, liens, notes de bas de page, renvois, gras dans les cellules, listes à trois niveaux, tableaux à groupes, colonnes calculées, récapitulatif de TVA, feuille d'émargement ;
> - le QR code de paiement sort du chantier, pour l'instant.
>
> Le détail est au § 16. Les sept points ouverts (O-1 à O-7) ont été tranchés le même jour : les propositions sont retenues.

## En bref

Les essais du 10 novembre (`/home/denis/temp/agent`, journaux `essai-01` à `essai-06`) ont montré trois défauts, qui ont la même cause : **la forme est éparpillée entre le code, le modèle de langage et des fichiers YAML trop riches.**

1. **La forme est codée dans l'outil.**
   - `word/style.py` fixe en dur une trentaine de valeurs de mise en page : espacements des titres, retraits, filets, légende, pied de page.
   - Le style maison est une classe (`Style()`), et le rôle des cinq couleurs de la charte y est décidé : `accent` colore le filet du titre, les titres 3, la bordure de l'encadré et le titre discret.
   - `pdf/writer.py` a un second style maison (Helvetica 10,5) qu'aucun fichier ne règle.
   - Les libellés écrits dans le document (« Sommaire », « Date : », « Tableau 1 — », « Page n / N ») sont codés en français.
2. **Le modèle de langage fait de la mise en page.**
   - Pour passer la vérification du plan, il écrit les titres de section en blocs `titre`, au mot près et dans l'ordre. Le titre `masque` n'existe que pour ça.
   - Dans `essai-06`, le refus « section absente : Destinataires » l'a poussé à écrire « À : à compléter ».
   - Son vocabulaire porte de la forme : `saut_de_page`, et `note` décrite comme « un encadré ».
3. **Les fichiers de style sont difficiles à écrire.**
   - Près de 80 réglages répartis dans une vingtaine de classes, chacune avec ses propres clés, souvent incomplètes : on ne peut régler ni le gras, ni la citation, ni la légende.
   - Deux fichiers aux mêmes clés sont fusionnés, avec des exceptions (`watermark: ""`, un `footer` déclaré qui fait perdre « Page n / N »).
   - La disposition nomme les sections du modèle.

**Le principe retenu :**

| Qui | Ce qu'il porte |
| --- | --- |
| Le modèle de langage | Le contenu : le titre du document, les blocs, et ce qu'il met dans chaque section |
| L'outil (`word`, `pdf`) | Des mécanismes : poser un titre, un tableau, un encadré, deux sections côte à côte, un en-tête avec logo, calculer un total. Aucune valeur visuelle |
| Le fichier de style | À quoi ressemble chaque élément : des styles nommés |
| Le modèle de document | De quoi est fait un type de document : ses sections, les consignes au modèle de langage, et quel style va où |

**Verdict : rien ne bloque.** Le format a été prototypé et les points nouveaux ont été essayés (§ 12).

| Point | Constat | Preuve |
| --- | --- | --- |
| Style nommé dans Word | Un style `message_title` basé sur « Heading 1 » s'applique au titre. Après un aller-retour dans LibreOffice, il est gardé avec sa base. Le lecteur actuel le relit en titre de niveau 1 | Démontré (LibreOffice 24.2) |
| Signets de section | Trois signets `_loom_s_…` posés autour des sections survivent à l'aller-retour LibreOffice | Démontré |
| Le format de style | Styles nommés, `base`, cascade de trois fichiers, couleurs nommées, styles proposés au modèle : environ 150 lignes de prototype, contre 748 pour `_style_params.py` | Démontré (prototype et 7 fautes de config) |
| Le modèle de document en YAML | Sections dans l'ordre du fichier ; ce que voit le modèle de langage, 1 083 caractères pour la note | Démontré |
| Colonnes calculées et TVA | `{Qté} * {PU HT}` évalué en décimal, sans `eval`, arrondi au centime ; TVA par taux ; 5 formules piégées refusées | Démontré (64 lignes) |
| Notes de bas de page, renvois, émargement, PDF stylé, polices embarquées | Déjà prototypés le 9 novembre (`outil-styles-lot2-analyse.md`) | Démontré le 9 novembre |
| Sans charte, Word ne change pas | `default.yaml` reprend les valeurs actuelles ; un test garde les documents d'aujourd'hui à l'octet près | À tenir à la mise en œuvre (lot 2 de ce chantier) |

**Tranché le 10 novembre** (§ 16.2) : la TVA arrondie par taux, le français comme langue par défaut des documents, les calculs de tableau pour tous, les descriptions et les messages en français, une seule rupture (la 2.1.0, après les lots 1 à 4), les notes de fin de document en PDF, et le refus des polices « aperçu et impression ».

## Périmètre et état de référence

L'analyse porte sur le commit `f57347c` (2.0.21, non publiée, égal à `origin/main`). L'arbre de travail a un changement non commité : `underline` sur les niveaux de titre, qui devient ici la propriété `underline` de tout style. Rien n'a été écrit dans `/home/denis/dev/loom_v2`, à part ce document.

Les essais ont tourné dans l'espace de travail cloud de Claude, sur un clone de ce commit, avec :

- Python 3.14.6 ;
- LibreOffice 24.2.7 ;
- reportlab 5.1.0 et pypdf 6.21.4.

Ce sont des essais de faisabilité, pas une implémentation : les tailles de code données plus bas sont des estimations. Les 304 tests de `word`, `pdf`, `_document`, des modèles et d'`excel` passent sur ce clone.

## 1. Les constats, mesurés

| Où | Ce qui est codé | Mesure |
| --- | --- | --- |
| `word/style.py` | Espacements des titres (18/6, 12/4, 10/3 pt), titre (filet 1 pt, 15 pt après), citation (retrait 1 cm, filet 1,5 pt), encadré (filet 3 pt), légende (9 pt, italique, centrée), pied (9 pt), puces « • » et « – », filigrane gris D9D9D9 | Une trentaine de valeurs WordprocessingML |
| `_style_params.py` | Le style maison (`Style()`), les couleurs de rôle (`COLOR_NAMES`) et leur emploi (`resolve()` : `color(note.border, "accent")`…) | 748 lignes, une vingtaine de classes |
| `word/writer.py` | Page de garde à 6 cm, 2 cm pour signer, écarts entre cellules et entre colonnes, bordures de tableau (0,5 pt, 1 pt, 1,5 pt) ; le seuil « tableau étroit » calculé sur la page du style maison (`HOUSE.text_width`), pas sur celle du document | Une vingtaine de valeurs |
| `pdf/writer.py` | Un second style maison : Helvetica 10,5, interligne 14, six couleurs, toutes les tailles | Une vingtaine de tailles et d'interlignes, 6 couleurs |
| `_chart_style.py` | La palette des graphiques | 12 couleurs, plus la grille et le texte |
| Les deux formats | Libellés écrits dans le document : « Tableau », « Figure », « Date : », « Signature : », « Sommaire », « Article n – », « Page n / N », « Total » ; la langue `fr-FR` ; les nombres à la française | 8 libellés |
| `_document/arguments.py`, `_values.py` | Le vocabulaire en français : 11 types de bloc, 10 types de colonne, 5 totaux, 4 sortes de graphique, 3 largeurs d'image, et `aucun` pour un document libre | Partagés avec `excel` pour les colonnes, les totaux et les graphiques |

## 2. Le principe

- **Aucune valeur que voudrait changer une charte n'est écrite dans le code** : police, taille, couleur, espacement du texte, filets, fonds, libellés. Elles sont dans `default.yaml`, livré avec loom, au format de tout fichier de style. Un test le garde : aucune couleur hexadécimale ni nom de police dans `word/` et `pdf/`.
- **Restent dans l'outil :**
  - la mécanique, c'est-à-dire les marges intérieures des cellules, l'écart entre deux colonnes de texte et entre deux sections côte à côte ;
  - les règles de composition (D-11) : nombres alignés à droite et dates centrées, en-tête de tableau répété, titre jamais seul en bas de page, ligne de tableau jamais coupée.
- **Le modèle de langage ne voit ni le style ni la disposition.** Il reçoit seulement les noms et les descriptions des styles qu'on lui propose (§ 3.7), et les consignes du modèle de document.

## 3. Le fichier de style

### 3.1 Les règles

1. **Un fichier de style est une liste de styles nommés.** Chaque style a des propriétés, et il n'y a jamais plus de deux niveaux (sauf `fonts`, § 10).
2. **Les noms prévus sont les styles par défaut de chaque élément** (§ 3.2). Tout autre nom est un style à toi, qui part d'un autre style (`base`).
3. **Tout est facultatif.** Un style ou une propriété qui n'est pas écrit vient de `default.yaml`.
4. **Sans `base`, un style part de `text`.** Exceptions : `quote_source` part de `quote` ; `table_header`, `table_total`, `table_group` et `table_subtotal` de `table` ; `fields_label` de `fields`.
5. **Les couleurs** s'écrivent en six chiffres hexadécimaux sans `#` (un `#` ouvre un commentaire YAML), ou par un nom défini dans `colors`. L'outil ne donne aucun rôle à ces noms.
6. **Les booléens** s'écrivent `true` et `false`.
7. **Un filet ou un cadre** s'écrit « épaisseur couleur » : `1pt 2F5496`, `3pt brand`.
8. **Les tailles de texte et les espacements** sont en points ; les marges, les retraits et les largeurs en centimètres.

### 3.2 Les styles prévus

| Nom | Ce qu'il met en forme | Style Word |
| --- | --- | --- |
| `text` | Le corps du texte ; la base de tous les autres | Normal |
| `document_title` | Le titre du document (`title`) | Title |
| `heading1` | Les titres de section, ou les titres de niveau 1 d'un document libre | Heading 1 |
| `heading2`, `heading3` | Les titres écrits par le modèle de langage | Heading 2, 3 |
| `paragraph`, `list`, `note`, `caption` | Les blocs du même nom ; `caption` est la légende | Normal (Body Text s'il diffère de `text`), List Paragraph, Note, Caption |
| `quote`, `quote_source` | Une citation et sa source | Quote, Quote Source |
| `code` | Le bloc `code` | loom code |
| `link` | Un lien dans le texte | Hyperlink (style de caractère) |
| `footnote` | Le texte d'une note de bas de page | footnote text |
| `fields`, `fields_label` | Le bloc `fields` : la valeur, le libellé | loom fields |
| `table`, `table_header`, `table_total` | Le tableau, sa ligne d'en-tête, sa ligne de total | Table Text |
| `table_group`, `table_subtotal` | La ligne d'un groupe, celle de son sous-total | — |
| `invoice_totals` | Le récapitulatif de TVA (part de `table`) | — |
| `attendance` | La feuille d'émargement (part de `table`) | — |
| `box` | Le cadre d'une section encadrée | — |
| `toc`, `signature` | Le sommaire, la signature | TOC Heading, toc 1-3… |
| `header`, `footer` | Le haut et le bas de page | Header, Footer |

Un style à toi devient un style Word du même nom, basé sur le style Word de sa base : il paraît dans la galerie de Word, où on peut le retoucher. Un style basé sur `heading1` reste un titre de niveau 1 pour Word (volet de navigation, sommaire, qui lit les niveaux de plan avec `\u`).

### 3.3 Les propriétés

**Communes à tous les styles :**

| Propriété | Valeur |
| --- | --- |
| `base` | Le style dont celui-ci part |
| `description` | Le sens du style, pour le proposer au modèle de langage (§ 3.7) |
| `font` | Un nom de police |
| `size` | En points |
| `color`, `fill` | Une couleur |
| `bold`, `italic`, `underline`, `uppercase` | `true` ou `false` |
| `align` | `left`, `center`, `right`, `justify` |
| `space_before`, `space_after` | En points |
| `line_spacing` | De 1 à 3 |
| `indent` | En cm |
| `border_top`, `border_bottom`, `border_left`, `border` | « épaisseur couleur » |

**Propres à certains styles :**

| Style | Propriétés en plus |
| --- | --- |
| `table` et ses dérivés | `borders` (`grid`, `horizontal`, `none`), `line` (les traits intérieurs), `bands` (le fond d'une ligne sur deux), `width` (`full`, `content`), `position` (`left`, `center`, `right`) |
| `header`, `footer` | `left`, `center`, `right` (avec `{page}`, `{pages}`, `{title}`), `lines` (4 au plus), `logo`, `logo_width`, `logo_position`, `first_page` |
| `caption` | `numbered` : « Tableau 1 — », « Figure 1 — » |
| `toc` | `levels` (1 à 3), `page_numbers` |
| `fields` | `label_width` (en cm) |
| `attendance` | `row_height` (en cm) |

`position` remplace l'`align` des tableaux d'aujourd'hui, qui se confondrait avec l'alignement du texte des cellules.

### 3.4 Les réglages

Ce ne sont pas des styles : on ne peut pas les prendre comme `base`.

| Clé | Ce qu'elle règle |
| --- | --- |
| `colors` | Tes couleurs nommées |
| `page` | `size` (`A4`, `A5`, `Letter`), `orientation` (`portrait`, `landscape`), `margins`, ou `margin_top`, `margin_bottom`, `margin_left`, `margin_right` |
| `numbering` | La numérotation des titres : `none`, `decimal` (1., 2.1), `article` (« Article 1 – ») |
| `watermark` | Un texte en travers de chaque page |
| `language` | `fr` ou `en` : la langue déclarée du document, les libellés par défaut, l'écriture des nombres et des dates (§ 9) |
| `labels` | Les libellés que l'outil écrit, un par un (§ 9) |
| `fonts` | Les fichiers TrueType d'une police, pour le PDF (§ 10) |
| `image` | `grid` : deux images `half` qui se suivent, côte à côte |
| `chart` | `colors` : la palette des séries |

### 3.5 `default.yaml`

Aux valeurs actuelles du code. Les styles qui n'existent pas encore (`code`, `link`, `fields`, les groupes, l'émargement) ont des valeurs proposées.

```yaml
# default.yaml — livré avec loom. Un fichier de style reprend ces noms et n'écrit que ce qui change.
colors: {}
page: { size: A4, orientation: portrait, margins: 2 }
numbering: none
language: fr                       # voir O-2
watermark: ""

text: { font: Calibri, size: 11, color: 000000, line_spacing: 1.15, space_after: 6 }
document_title:
  size: 20
  bold: true
  color: 1F3864
  space_after: 15
  border_bottom: 1pt 2F5496
heading1: { size: 16, bold: true, color: 1F3864, space_before: 18, space_after: 6 }
heading2: { size: 13, bold: true, color: 1F3864, space_before: 12, space_after: 4 }
heading3: { size: 11.5, bold: true, color: 2F5496, space_before: 10, space_after: 3 }
paragraph: {}
list: { space_after: 3 }
quote: { italic: true, color: 595959, indent: 1, space_before: 8, space_after: 4, border_left: 1.5pt A6A6A6 }
quote_source: { italic: false, align: right, space_after: 10 }
note: { fill: F2F2F2, indent: 0.3, space_before: 6, space_after: 10, border_left: 3pt 2F5496 }
caption: { size: 9, italic: true, color: 595959, align: center, space_before: 3, space_after: 12, numbered: false }
code: { font: Courier New, size: 9, fill: F2F2F2, line_spacing: 1, indent: 0.3 }
link: { color: 0563C1, underline: true }
footnote: { size: 9 }
fields: { label_width: 4 }
fields_label: { bold: true }
box: { border: 0.75pt 1F3864 }

table: { size: 10, borders: grid, line: 0.5pt A6A6A6, width: full, position: left, line_spacing: 1, space_after: 0 }
table_header: { bold: true, fill: D9E1F2, align: center }
table_total: { bold: true, border_top: 1.5pt 1F3864 }
table_group: { bold: true, fill: F2F2F2 }
table_subtotal: { italic: true, border_top: 0.5pt A6A6A6 }
invoice_totals: { base: table, width: content, position: right }
attendance: { base: table, row_height: 1.5 }

toc: { levels: 2, page_numbers: true }
signature: {}
header: { size: 9, color: 595959, space_after: 0 }
footer: { size: 9, color: 595959, space_after: 0, center: "Page {page} / {pages}" }
image: { grid: false }
chart: { colors: [1F3864, C55A11, 8EA9DB, 548235, 7F7F7F, BF9000, 2F5496, 843C0C, A5A5A5, 4472C4, 70AD47, FFC000] }
```

### 3.6 La cascade et l'héritage

**Les fichiers d'abord.** Chaque style complète le même style des fichiers précédents, propriété par propriété. Un document qui suit un modèle prend, dans cet ordre :

1. `default.yaml` ;
2. la charte (`style` sur l'outil) ;
3. le style du modèle (`style` dans le modèle de document).

Un document libre s'arrête à la charte.

**L'héritage ensuite.** Un style prend toutes les propriétés de sa `base`, déjà complétée par les trois fichiers, puis les siennes.

Avec la charte et le style de la note du § 11, le prototype résout :

| Style | Résolu |
| --- | --- |
| `document_title` | Arial, 24 pt, gras, 3A56E4, centré, majuscules, filet 1 pt 3A56E4, 15 pt après |
| `heading1` (titres de la note) | Arial, 11 pt, gras, 595959, souligné, 10 pt avant, 3 pt après |
| `message_title` (`base: heading1`) | Arial, 14 pt, gras, 595959, majuscules, non souligné, 10 pt avant, 3 pt après |
| `important_note` (§ 3.7, ajouté à la charte) | Arial 10, gras, fond FDECEA, filet gauche 3 pt C0392B, retrait 0,3 cm |

Deux conséquences :

- `message_title`, défini dans le style de la note, hérite des réglages que ce même fichier donne à `heading1` ;
- la police Arial vient de `text`, dans la charte, sans être répétée.

**Les fautes dites au chargement** (texte du prototype) :

```text
bad1.yaml : heading1.colour — Extra inputs are not permitted
style x : base inconnue « y »
style a : base en boucle (a → b → a)
style red_box : une description ne sert qu'à un style de bloc (paragraph, list, note, quote, table, heading1…)
ok5.yaml ligne 2 : YAML illisible — un texte qui contient « : » ou « # » s'écrit entre guillemets, ou après | à la ligne
style heading1.border_bottom : « épaisseur couleur », ex. 1pt 2F5496
```

### 3.7 Les styles proposés au modèle de langage

Un style qui a une `description` est proposé au modèle de langage, sur le type de bloc dont il descend (`important_note`, `base: note`, ne vaut que pour les blocs `note`) :

```yaml
important_note:
  base: note
  description: A warning the reader must not miss (deadline, safety, legal obligation).
  fill: FDECEA
  border_left: 3pt C0392B
  bold: true
```

```json
{"type": "note", "style": "important_note", "text": "Le dépôt sera fermé le <u>vendredi 13 novembre</u>."}
```

- **Il ne voit que le nom et la description**, jamais les propriétés. Une description dit le sens, pas le rendu.
- **Où il le voit :**
  - un style de la charte passe dans la description de l'outil, donc à chaque appel (environ 20 tokens) ;
  - un style du modèle de document ne part qu'à la lecture du modèle (`action: template`).
- **Le champ `style`** n'existe dans le schéma que si un style est proposé. Ses valeurs permises dépendent du type de bloc : un nom inconnu ou d'un autre type est refusé, avec la liste des noms permis.
- **Quand plusieurs styles s'appliquent, le plus précis gagne** : le `style` du bloc, puis celui de la section (`styles`, § 4.1), puis le style par défaut de l'élément.

## 4. Le modèle de document

### 4.1 Les clés

**Du document :**

| Clé | Ce que c'est | Qui la lit |
| --- | --- | --- |
| `description` | Une ligne, 200 caractères au plus | Le modèle de langage, dans la description de l'outil |
| `instructions` | Les consignes générales, écrites après `\|` | Le modèle de langage (`action: template`) |
| `style` | Un fichier de style (chemin), ou un style en ligne (table), pour ce type de document | L'outil |
| `sections` | Les sections, dans l'ordre du document | Les deux |

**D'une section** (son nom est à la fois le titre imprimé et la clé que le modèle de langage remplit) :

| Clé | Effet | Par défaut |
| --- | --- | --- |
| `instructions` | Ce que le modèle de langage y met (après `\|`) | — |
| `optional` | La section peut manquer | `false` |
| `show_title` | `false` : le nom de la section n'est pas imprimé | `true` |
| `text` | Un texte fixe, que l'outil écrit lui-même (§ 4.3) | — |
| `styles` | Le style nommé de chaque élément dans cette section : `{ heading1: message_title, note: important_note }` | — |
| `box` | La section dans un cadre (style `box`, ou celui que donne `styles`) | `false` |
| `beside` | Posée à côté de la section précédente | `false` |
| `width` | La part de la ligne, en %, pour des sections côte à côte | parts égales |
| `page_break` | La section commence une page | `false` |
| `columns` | 2 ou 3 colonnes équilibrées | 1 |
| `landscape` | La section sur des pages en paysage | `false` |
| `cover` | Page de garde : la première section seulement, avec le titre du document, sans en-tête ni pied | `false` |

Les règles de la disposition d'aujourd'hui restent :

- une section `beside` n'a ni `columns`, ni `landscape`, ni `cover` ;
- `page_break` va à la première section d'une ligne ;
- une page de garde n'a ni `columns`, ni `box`, ni `landscape`.

Les sections côte à côte n'ont plus à « se suivre dans le plan » : `beside` est par construction la section d'après. Si l'une des deux est facultative et absente, l'autre prend toute la largeur.

### 4.2 Ce que voit le modèle de langage

- **Dans la description de l'outil :** le nom et la `description` de chaque modèle.
- **Par `action: template` :** les `instructions`, puis pour chaque section qui n'est pas en texte fixe, son nom, `optional` s'il y a lieu, et ses `instructions`. Il y a aussi les styles proposés par le style du modèle.
- **Jamais :** `style`, `styles`, `show_title`, `box`, `beside`, `width`, `page_break`, `columns`, `landscape`, `cover`, ni le texte fixe.

Pour la note du § 11, cela fait 1 083 caractères, pour un fichier de 1 166.

### 4.3 Les sections au texte fixe

```yaml
  Conditions:
    text: |
      Paiement à 30 jours par virement.
      Tout retard de paiement entraîne des pénalités.
```

L'outil écrit ce texte lui-même. Il ne coûte aucun token, et le modèle de langage ne peut rien y inventer : mentions légales, conditions générales, coordonnées de l'émetteur.

- **Le balisage du texte est le même que dans un bloc** (`**gras**`, `*italique*`, `<u>…</u>`, liens).
- **Une ligne vide sépare deux paragraphes** ; un retour à la ligne simple va à la ligne dans le même paragraphe.
- **Une section fixe ne peut pas être remplie par le modèle de langage** : il en est refusé, avec un message qui le dit.

### 4.4 Le piège du texte français en YAML

Dans un texte écrit sans guillemets ni `|`, deux caractères posent problème (vérifié avec PyYAML) :

- **une espace suivie de « : »** est une erreur YAML : « Un paragraphe : qui joindre » ne se lit pas ;
- **une espace suivie de « # »** coupe le texte sans prévenir : « Voir la note # 3 » se lit « Voir la note ».

La règle à enseigner : **tout texte s'écrit après `|`, à la ligne.** Le chargeur traduit l'erreur de PyYAML en une phrase qui donne la ligne et la règle (§ 3.6).

## 5. L'appel du modèle de langage

**Un document libre :** `title` et `blocks`, avec `"template": "none"` si l'outil a des modèles.

**Un document qui suit un modèle :**

```json
{"action": "write", "path": "workspace/note-fermeture-du-depot.docx", "template": "note",
 "title": "Fermeture du dépôt",
 "sections": {
   "Destinataires": [{"type": "fields", "items": [["À", "l'équipe du dépôt de Melun"], ["De", "Jean Dupont"], ["Le", "10 novembre 2026"]]}],
   "Message": [{"type": "paragraph", "text": "Le dépôt de <u>Melun</u> sera fermé le <u>vendredi 13 novembre</u>, toute la journée, pour l'inventaire."}],
   "Contact": [{"type": "paragraph", "text": "En cas d'urgence : 06 12 23 45 85, de <u>9 h à 18 h</u>."}]}}
```

- **L'outil pose les sections dans l'ordre du modèle et écrit leurs titres.** L'ordre des clés dans l'appel ne compte pas.
- **Les seuls refus du plan** sont une section obligatoire absente, une section inconnue, une section au texte fixe remplie, ou une section vide. Le refus donne les sections attendues, avec `(optional)` et `(fixed)`.
- **Dans une section, le niveau 1 est réservé aux titres de section** : les blocs `heading` y sont de niveau 2 ou 3, et un niveau 1 est refusé, avec un message qui le dit.
- **La comparaison des noms reste souple**, comme aujourd'hui : sans casse, accents ni numérotation. « destinataires » est la section « Destinataires ».
- **Ce qui disparaît** : les titres de niveau 1 écrits pour le plan, les erreurs d'ordre, les titres `masque` et `discret`.

## 6. La relecture

- **Chaque section est entourée d'un signet caché**, `_loom_s<n>` (n est sa place dans le modèle). Le modèle suivi reste dans la catégorie du document (`cp:category`), comme aujourd'hui.
- **Un document relu rend `template` et `sections`** : la réécriture refait le même document. Une section au texte fixe n'est pas rendue.
- **Les signets survivent à un passage dans LibreOffice** (démontré). Un document dont les signets ont disparu, parce que quelqu'un a coupé et recollé une section dans Word, est relu en document libre, avec une note qui le dit.
- **Un style nommé est relu par sa base** : dans un document libre, un titre au style `message_title` revient en `heading` de niveau 1 (démontré avec le lecteur actuel). Dans un document à modèle, les styles d'une section (`styles`) ne reviennent pas : le modèle les repose. Un style proposé au modèle de langage (`important_note`) revient en `"style": "important_note"`.
- **`loom.memory`** lit les `.docx` par ce lecteur. Il suit les nouveaux noms de blocs (`memory/files.py`, `word_markdown`), et gagne les champs, les notes de bas de page et le code.

## 7. Le vocabulaire

### 7.1 Les blocs

| Bloc | Champs | Disponible |
| --- | --- | --- |
| `heading` | `text`, `level` (1 à 3) | Toujours |
| `paragraph` | `text` | Toujours |
| `list` | `items` (textes, ou `{text, items}` : trois niveaux), `ordered` | Toujours |
| `table` | `columns` (`{header, type, compute}`), `rows` (lignes, ou `{group, rows}`), `totals`, `caption` | Toujours (O-3) |
| `note` | `text`, `title` | Toujours |
| `quote` | `text`, `source` | Toujours |
| `chart` | `kind`, `title`, `caption`, `from_table` ou `categories` + `series` + `value_type` | `blocks: [chart]` |
| `image` | `path`, `width` (`full`, `half`, `small`), `caption`, `alt` | `blocks: [image]` |
| `signature` | `signers`, `mention` | `blocks: [signature]` |
| `toc` | — | `blocks: [toc]` |
| `list` + `checkboxes` | éléments en `[x]` ou `[ ]` | `blocks: [checkboxes]` |
| `fields` | `items` : des paires libellé et valeur | `blocks: [fields]` |
| `code` | `text` | `blocks: [code]` |
| `attendance` | `participants`, `sessions` | `blocks: [attendance]` |
| `invoice_totals` | `from_table` (`{amount, rate}`), `paid` | `blocks: [invoice_totals]` |

Tout bloc peut porter `style`, si un style lui est proposé (§ 3.7).

**Dans un texte :**

| Balisage | Effet |
| --- | --- |
| `**gras**`, `*italique*`, `<u>souligné</u>` | L'emphase, comme aujourd'hui |
| `\n` | Un retour à la ligne dans le même paragraphe |
| `[texte](https://…)` | Un lien |
| `^[texte de la note]` | Une note de bas de page |
| `§[Titre]` | Un renvoi à un titre du document |

Les cellules de tableau acceptent l'emphase et les liens.

### 7.2 Les valeurs, en anglais

| | Aujourd'hui | Demain |
| --- | --- | --- |
| Types de colonne | `texte`, `entier`, `decimal`, `montant_eur`, `montant_keur`, `pourcentage`, `date`, `date_heure`, `annee`, `booleen` | `text`, `integer`, `decimal`, `amount_eur`, `amount_keur`, `percent`, `date`, `datetime`, `year`, `boolean` |
| Totaux | `somme`, `moyenne`, `nombre`, `min`, `max` | `sum`, `average`, `count`, `min`, `max` |
| Graphiques | `barres`, `barres_horizontales`, `courbe`, `secteurs` | `bar`, `horizontal_bar`, `line`, `pie` |
| Largeur d'image | `pleine_largeur`, `moitie`, `petite` | `full`, `half`, `small` |
| Document libre | `aucun` | `none` |
| Rôles de `loom.excel` | `saisie`, `formule`, `hypothese`, `total`, `note` | `input`, `formula`, `assumption`, `total`, `note` |

Les types de colonne, les totaux et les graphiques sont partagés avec `loom.excel` (`_values.py`, `_chart_style.py`) : ils changent dans les trois outils à la fois. Les descriptions et les messages des outils restent en français dans ce chantier (O-4).

### 7.3 La configuration

```yaml
- python: loom.word                   # loom.pdf : les mêmes clés
  style: styles/brand.yaml            # la charte, relative à resources_dir
  templates:
    note: templates/note.yaml
    invoice: templates/invoice.yaml
  blocks: [fields, signature, toc, invoice_totals]
  folders: …
```

- **`blocks`** remplace `charts`, `images` et `extra_blocks`. Un bloc absent n'est ni dans le schéma ni dans la description, et ne coûte rien. Un bloc ajouté plus tard est un nom de plus dans la liste.
- **`templates.<nom>`** n'a plus qu'une forme, le chemin du modèle. La forme longue `{file, style}` disparaît : le modèle nomme son style lui-même.
- **Une ancienne clé ou un ancien format est refusé au chargement**, avec la nouvelle forme dans le message (D-7). Par exemple : « charts: true → blocks: [chart] », ou « templates/note.md : un modèle est un fichier YAML (description, instructions, sections) ».

### 7.4 Ce qui sort du vocabulaire

- **`saut_de_page`** sort (D-8). Une page se commence par `page_break: true` sur une section du modèle. Un document libre n'a plus de saut de page.
- **`note`** est décrite par son sens (« à retenir, mise en garde »), plus comme « un encadré » (D-9).
- **La largeur d'une image reste** (D-10) : elle dépend de l'image elle-même (un logo, une photo).

## 8. Les nouveaux contenus

### 8.1 `fields` : des libellés et leurs valeurs

```json
{"type": "fields", "items": [["Référence", "D-2026-042"], ["Date", "10/11/2026"], ["Bon pour accord", ""]]}
```

- **Word :** un tableau sans bordure à deux colonnes, au style de tableau « loom fields ». Le libellé est au style `fields_label`, la colonne fait `label_width` cm. Une valeur vide laisse une ligne à remplir à la main (filet bas sur la cellule). **PDF :** une `Table`.
- **Relecture :** reconnu à son style de tableau, rendu tel qu'écrit.
- **Bornes :** 30 paires, 80 caractères par libellé, 500 par valeur.
- **Schéma :** chaque paire est une liste de deux textes (`minItems` et `maxItems` 2), plutôt qu'un tuple (`prefixItems`), que tous les fournisseurs n'acceptent pas.

### 8.2 `code` : du texte préformaté

```json
{"type": "code", "text": "SELECT * FROM demandes\nWHERE statut = 'ouverte';"}
```

- **Rendu :** un paragraphe par ligne, au style `code`, espaces gardées (`xml:space="preserve"`), sans correcteur (`w:noProof`). En PDF, `XPreformatted`.
- **Aucun balisage n'est lu** dans un bloc `code`. 200 lignes au plus.

### 8.3 Les liens

- **`[texte](url)`**, seulement en `https`, `http` et `mailto`. Un autre schéma (`javascript:`, `file:`) est refusé.
- **Word :** `w:hyperlink` et une relation externe, au style de caractère `link`. **PDF :** `<a href>` de reportlab.
- **Relecture :** rendu en `[texte](url)`.

### 8.4 Les notes de bas de page

- **`^[texte]`**, la syntaxe de pandoc. Une note se met dans un paragraphe, un élément de liste, une note, une citation ou une cellule ; pas dans un titre ni une légende. Pas de note dans une note. 500 caractères par note, 100 notes par document. Un `^[` non refermé est refusé, avec l'endroit ; `\^[` s'écrit tel quel.
- **Word :** `footnotes.xml`, démontré le 9 novembre. **PDF :** des notes en fin de document (O-6).
- **Relecture :** rendu en `^[…]` à sa place.

### 8.5 Les renvois

- **`§[Titre]`**, le titre visé comparé comme le plan. Un titre inconnu est refusé, avec la liste des titres du document. Un titre non imprimé (`show_title: false`) ne peut pas être visé.
- **Word :** un signet sur le titre visé, et un champ `REF … \r \h` si les titres sont numérotés, sinon le titre entre guillemets avec un lien (démontré le 9 novembre). **PDF :** le numéro calculé et un lien interne.

### 8.6 L'emphase et les liens dans les cellules

Les cellules de texte acceptent `**…**`, `*…*`, `<u>…</u>` et les liens. Les cellules typées (nombres, dates) restent des valeurs : le balisage y est refusé.

### 8.7 Les listes à trois niveaux

`{text, items}` s'imbrique deux fois. La numérotation Word gagne un troisième niveau (« i. » pour une liste numérotée, « ▪ » pour les puces), et le PDF imbrique ses `ListFlowable`.

### 8.8 Les tableaux à groupes

```json
"rows": [{"group": "Lot 1 – Salle de bains", "rows": [["Dépose baignoire", 1, 180, 0.1], ["Receveur extra-plat", 1, 1250.5, 0.1]]},
         {"group": "Lot 2 – Cuisine", "rows": [["Mitigeur", 2, 89.99, 0.2]]}]
```

- **Rendu :** une ligne de groupe pleine largeur (`table_group`) avant ses lignes. Si `totals` est donné, une ligne de sous-total par groupe (`table_subtotal`), puis le total général (`table_total`), calculés par l'outil.
- **Bornes :** 20 groupes ; pas de groupe dans un groupe ; on ne mélange pas les groupes et les lignes simples dans un même tableau.
- **Relecture :** les groupes reviennent en `{group, rows}` ; les sous-totaux ne reviennent pas, puisque l'outil les recalcule.

### 8.9 Les colonnes calculées

```json
{"header": "Montant HT", "type": "amount_eur", "compute": "{Qté} * {PU HT}"}
```

- **La formule :**
  - `+ - * /`, des parenthèses, des nombres, et des `{colonnes}` de nombres de la même ligne ;
  - rien d'autre : ni fonction, ni puissance, ni nom ;
  - analysée avec `ast` et évaluée en `Decimal`, jamais par `eval` ;
  - arrondie selon le type de la colonne : au centime pour `amount_eur`, à l'unité pour `integer`.
- **Les lignes ne donnent pas les colonnes calculées** : leurs valeurs suivent l'ordre des autres colonnes. Une colonne calculée peut porter un total.
- **Fautes refusées** (prototype) :

```text
colonne inconnue « Prix » (colonnes : Désignation, Qté, PU HT, TVA, Montant HT)
« Désignation » n'est pas une colonne de nombres
formule "__import__('os')" : seuls + - * / ( ), des nombres et des {colonnes}
formule '{Qté} ** 2' : seuls + - * / ( ), des nombres et des {colonnes}
division par zéro
```

### 8.10 `invoice_totals` : le récapitulatif de TVA

```json
{"type": "invoice_totals", "from_table": {"amount": "Montant HT", "rate": "TVA"}, "paid": 500}
```

- **Les données viennent du tableau qui le précède**, comme `chart` : la colonne des montants HT et celle des taux.
- **Lignes écrites :** Total HT ; une ligne de TVA par taux (« TVA 10 % », avec sa base) ; Total TTC ; si `paid` est donné, l'acompte versé et le net à payer. Les libellés viennent de `labels` (§ 9).
- **Calcul :** la TVA de chaque taux se calcule sur le total HT de ce taux, arrondi au centime, au demi supérieur (O-1). Sur l'exemple du § 11.2 : 1 430,50 € à 10 % et 179,98 € à 20 % donnent 143,05 € et 36,00 € de TVA, et 1 789,53 € TTC.
- **Rendu :** un tableau au style `invoice_totals`, étroit et à droite par défaut.
- **Ce qui n'est pas calculé :** une mention comme « TVA non applicable, art. 293 B du CGI » (franchise en base) est du texte. Elle va dans une section au texte fixe du modèle.

### 8.11 `attendance` : la feuille d'émargement

```json
{"type": "attendance", "participants": ["Jeanne Martin", "Paul Durand"], "sessions": ["Lundi matin", "Lundi après-midi"]}
```

- **Rendu :** une colonne « Participant » (libellé de `labels`), puis une colonne par session. Les lignes font au moins `row_height` (1,5 cm), l'en-tête se répète à chaque page, au style `attendance`. Démontré le 9 novembre.
- **Bornes :** 100 participants, 10 sessions, 80 caractères par libellé.

## 9. La langue du document et ses libellés

`language` (`fr` ou `en`) fixe trois choses :

- la langue déclarée du document (`w:lang`), pour le correcteur et la césure ;
- l'écriture des nombres et des dates : « 1 250,50 € » et « 10/11/2026 », ou « €1,250.50 » et « 11/10/2026 » ;
- les libellés par défaut.

`labels` les remplace un par un :

| Clé | `fr` | `en` |
| --- | --- | --- |
| `table` | Tableau | Table |
| `figure` | Figure | Figure |
| `contents` | Sommaire | Contents |
| `date` | Date : | Date: |
| `signature` | Signature : | Signature: |
| `article` | Article | Article |
| `total` | Total | Total |
| `subtotal` | Sous-total | Subtotal |
| `total_excl_tax` | Total HT | Total excl. VAT |
| `tax` | TVA {rate} | VAT {rate} |
| `total_incl_tax` | Total TTC | Total incl. VAT |
| `paid` | Acompte versé | Deposit paid |
| `due` | Net à payer | Amount due |
| `notes` | Notes | Notes |
| `participant` | Participant | Participant |

`loom.excel` n'est pas touché : ses formats suivent déjà la langue du lecteur.

## 10. Le PDF

`loom.pdf` lit les mêmes fichiers (style, modèles) et le même vocabulaire. Le prototype du 9 novembre (`outil-styles-lot2-analyse.md`) a rendu la facture et le rapport des styles avec reportlab, depuis le même document préparé et le même style résolu. Ce qui diffère de Word :

- **Les polices.**
  - Arial, Helvetica et Calibri se rendent en Helvetica ; Times New Roman en Times ; Courier New en Courier.
  - Une autre police demande ses fichiers TrueType, déclarés dans le style : `fonts: { Inter: { regular: fonts/Inter-Regular.ttf, bold: fonts/Inter-Bold.ttf } }`. Ils sont lus au chargement, et vérifiés : table `glyf`, et droits d'embarquement `fsType`, comme le prévoit cette analyse du 9 novembre.
  - Sans fichier, la police se rend en Helvetica, et `loom validate` le dit.
  - Les fichiers `.woff2` de ton outil `wireframe` ne conviennent pas : ce n'est pas du TrueType.
- **Les notes de bas de page** deviennent des notes de fin de document (O-6).
- **Le sommaire** a ses vrais numéros de page dès l'écriture (`multiBuild`).
- **Un document sans style change d'aspect une fois** (D-6). `default.yaml` est commun aux deux formats : le PDF passe de Helvetica 10,5 au corps de 11 pt.
- **Les cases à cocher** sont dessinées, parce qu'Helvetica n'a pas ☐ ni ☒. Avec une police embarquée qui les a, elles sont écrites.

## 11. Deux exemples complets

### 11.1 La note

**`agents/agent.yaml`**

```yaml
tools:
- python: loom.word
  style: styles/brand.yaml
  templates:
    note: templates/note.yaml
  blocks: [fields]
  folders:
  - name: workspace
    path: workspace
    rights: [ read, write, overwrite ]
```

**`prompts/styles/brand.yaml`** — la charte, pour tous les documents

```yaml
colors:
  brand: 3A56E4
  grey: 595959
  light: EEF1FD
language: fr

text: { font: Arial, size: 10 }
document_title:
  size: 24
  color: brand
  align: center
  uppercase: true
  border_bottom: 1pt brand
heading1: { color: brand }
heading2: { color: brand }
note: { fill: light, border_left: 3pt brand }
box: { border: 0.75pt brand, fill: light }
header: { logo: images/logo.png, logo_width: 3 }
footer:                       # « Page n / N » vient de default.yaml
  lines:
    - "Dupont Plomberie SARL · 12 rue des Lilas, 69003 Lyon · SIRET 812 345 678 00021"
```

**`prompts/styles/note.yaml`** — ce qui ne vaut que pour les notes

```yaml
heading1: { size: 11, color: grey, underline: true, space_before: 10, space_after: 3 }
message_title:
  base: heading1
  size: 14
  uppercase: true
  underline: false
```

**`prompts/templates/note.yaml`** — le modèle de document

```yaml
description: Note courte à l'équipe ou aux clients (fermeture, changement d'horaires, consigne).

instructions: |
  Fichier : workspace/note-<objet>.docx (l'objet en minuscules, mots reliés par des tirets),
  sauf si l'utilisateur donne un nom.
  title : l'objet de la note en quelques mots (« Fermeture du dépôt »).
  N'invente rien : dates, heures, lieux et noms viennent de l'utilisateur.
  S'il manque la date ou le lieu, demande-les avant d'écrire.
  Souligne (<u>…</u>) les dates, heures et lieux qui comptent ; rien d'autre.

style: styles/note.yaml

sections:
  Destinataires:
    optional: true
    show_title: false
    instructions: |
      Un bloc fields : « À » les destinataires, « De » l'émetteur, « Le » la date de la note.
  Message:
    styles: { heading1: message_title }
    instructions: |
      Un ou deux paragraphes courts : ce qui se passe, quand, où,
      et pourquoi si l'utilisateur l'a dit.
  Consignes:
    optional: true
    instructions: |
      Une liste de ce que les destinataires doivent faire, une action par élément.
  Contact:
    box: true
    instructions: |
      Un paragraphe : qui joindre pour une question, et comment.
```

**L'appel** est celui du § 5. **Le document :**

```
┌────────────────────────────────────────────────────┐
│ [logo]                                             │  ← header
│                                                    │
│                 FERMETURE DU DÉPÔT                 │  ← document_title
│ ────────────────────────────────────────────────── │
│ À    l'équipe du dépôt de Melun                    │  ← Destinataires : fields,
│ De   Jean Dupont                                   │    titre non imprimé
│ Le   10 novembre 2026                              │
│                                                    │
│ MESSAGE                                            │  ← message_title
│ Le dépôt de Melun sera fermé le vendredi           │  ← paragraph
│ 13 novembre, toute la journée, pour l'inventaire.  │
│                                                    │
│ ┌────────────────────────────────────────────────┐ │  ← box
│ │ Contact                                        │ │  ← heading1 (11 pt, gris, souligné)
│ │ En cas d'urgence : 06 12 23 45 85,             │ │  ← paragraph
│ │ de 9 h à 18 h.                                 │ │
│ └────────────────────────────────────────────────┘ │
│                                                    │
│  Dupont Plomberie SARL · 12 rue des Lilas, Lyon…   │  ← footer
│                     Page 1 / 1                     │
└────────────────────────────────────────────────────┘
```

### 11.2 La facture

**`prompts/templates/invoice.yaml`**

```yaml
description: Facture de travaux à un particulier.

instructions: |
  Fichier : workspace/facture-<numéro>.docx ; title : « Facture n° <numéro> ».
  N'invente rien : prestations, quantités et prix viennent de l'utilisateur.

style:                                   # un style en ligne, pour les factures seulement
  table: { borders: horizontal, border_top: 1pt brand, border_bottom: 1pt brand }

sections:
  Émetteur:
    show_title: false
    width: 55
    text: |
      **Dupont Plomberie SARL**
      12 rue des Lilas, 69003 Lyon
      SIRET 812 345 678 00021
  Client:
    show_title: false
    beside: true
    width: 45
    box: true
    instructions: |
      Un bloc fields : Client, Adresse, Facture n°, Date.
  Prestations:
    show_title: false
    instructions: |
      Un tableau : Désignation, Qté (integer), PU HT (amount_eur), TVA (percent),
      Montant HT (amount_eur, compute "{Qté} * {PU HT}"), avec la somme des montants.
      Un groupe par lot si l'utilisateur en donne.
  Total:
    show_title: false
    instructions: |
      Un bloc invoice_totals tiré du tableau des prestations.
  Conditions:
    text: |
      Paiement à 30 jours par virement.
```

**L'appel**

```json
{"action": "write", "path": "workspace/facture-F-2026-118.docx", "template": "invoice",
 "title": "Facture n° F-2026-118",
 "sections": {
  "Client": [{"type": "fields", "items": [["Client", "Mme Martin"], ["Adresse", "4 rue Paul Bert, 69003 Lyon"], ["Facture n°", "F-2026-118"], ["Date", "10/11/2026"]]}],
  "Prestations": [{"type": "table",
    "columns": [{"header": "Désignation"}, {"header": "Qté", "type": "integer"},
                {"header": "PU HT", "type": "amount_eur"}, {"header": "TVA", "type": "percent"},
                {"header": "Montant HT", "type": "amount_eur", "compute": "{Qté} * {PU HT}"}],
    "rows": [{"group": "Lot 1 – Salle de bains", "rows": [["Dépose baignoire", 1, 180, 0.1], ["Receveur extra-plat", 1, 1250.5, 0.1]]},
             {"group": "Lot 2 – Cuisine", "rows": [["Mitigeur", 2, 89.99, 0.2]]}],
    "totals": {"Montant HT": "sum"}}],
  "Total": [{"type": "invoice_totals", "from_table": {"amount": "Montant HT", "rate": "TVA"}}]}}
```

**Ce que calcule l'outil :**

- les montants : 180,00 €, 1 250,50 € et 179,98 € ;
- les sous-totaux : 1 430,50 € pour le lot 1, 179,98 € pour le lot 2 ;
- le total HT : 1 610,48 € ;
- la TVA : 143,05 € à 10 %, 36,00 € à 20 % ;
- le total TTC : 1 789,53 €.

Le modèle de langage n'a fait aucun calcul. L'émetteur et les conditions ne lui sont jamais passés.

## 12. Analyse technique

### 12.1 Ce que montrent les essais

| Essai | Résultat |
| --- | --- |
| **A. Style nommé et signets** (`.docx` de l'outil, retouché) | `message_title` (`basedOn Heading1`, majuscules, C0392B) rendu par LibreOffice : « MESSAGE ». Après un aller-retour `.docx` → `.docx` dans LibreOffice, le style est gardé (nom, base, application), comme les trois signets `_loom_s_…`. Le lecteur actuel relit le titre en niveau 1, avant comme après |
| **D. Formules et TVA** | 64 lignes : `ast` filtré, `Decimal`, arrondi au centime. TVA par taux juste ; cinq formules piégées refusées |
| **F. Coût du vocabulaire** | Schémas seuls, JSON compact : `fields` 320 caractères, `code` 198, groupe de tableau 321, colonne avec `compute` 290 (dont environ 50 nouveaux), `invoice_totals` 359, `attendance` 343, élément de liste à trois niveaux 292 |
| **G. Format de style** | Le prototype (environ 150 lignes) lit `default.yaml`, la charte et le style du modèle, résout la cascade et l'héritage, et dit sept fautes (§ 3.6) |
| **Modèle en YAML** | La note se lit dans l'ordre de ses sections ; ce que voit le modèle de langage fait 1 083 caractères. Tous les exemples YAML de ce document se lisent, et ceux de style se résolvent avec le prototype |

L'analyse du 9 novembre (`outil-styles-lot2-analyse.md`) a déjà démontré les autres points : le style dans reportlab, les notes de bas de page Word, les renvois, l'émargement, les polices embarquées en Word et en PDF.

### 12.2 Le coût en tokens

**Aujourd'hui** (description et schéma, en caractères) :

| `word` | Caractères |
| --- | --- |
| Sans réglage | 1 820 + 3 785 = 5 605 |
| `charts` | 7 411 |
| `images` | 6 454 |
| `extra_blocks` | 6 465 |
| Tout, plus un modèle | 3 315 + 6 367 = 9 682 |

`pdf` sans réglage fait 5 762 caractères.

**Ce que la refonte ajoute** (estimations à mesurer, à environ 4 caractères par token) :

| Ajout | Pour qui | Estimation |
| --- | --- | --- |
| Liens, notes de bas de page, renvois, emphase dans les cellules, listes à trois niveaux | Tout agent qui a `word` ou `pdf` | +350 à +450 caractères (environ 100 tokens) |
| Colonnes calculées et groupes de tableau | Tout agent, ou seulement ceux qui le déclarent (O-3) | +350 à +450 (environ 100 tokens) |
| `sections` dans l'appel | Les agents à modèles | +150 |
| Chaque bloc de `blocks` | Seulement s'il est déclaré | De +250 (`code`) à +500 (`invoice_totals`), aide comprise |
| Un style proposé dans la charte | Seulement s'il a une `description` | Environ +80 |
| `saut_de_page` retiré, `note` décrite autrement | Tous | −60 |

### 12.3 Modifications prévues

| Fichier | Changement | Estimation |
| --- | --- | --- |
| `tools_builtin/_style_params.py` | Réécrit : un modèle `Look` commun, quelques modèles propres (`table`, bandes, page), la cascade, l'héritage, les couleurs, les styles proposés | 748 → environ 400 lignes |
| `tools_builtin/styles/default.yaml` (nouveau) | Le style maison | ~70 lignes |
| `tools_builtin/_templates.py` | YAML : sections, `text`, `styles`, comparaison des noms (gardée) | 165 → environ 250 |
| `config/loader.py` | `_styles_read` et `_templates_read` : chemins ou styles en ligne, logos, polices ; les messages des anciens formats | ~150 modifiées |
| `word_params.py`, `pdf_params.py`, `_office_params.py` | `blocks`, `templates` sous forme courte seule, `style` sur `pdf` ; refus des anciennes clés | ~120 |
| `_values.py`, `_chart_style.py` | Noms anglais ; la palette sort vers `default.yaml`, que lit aussi `excel` | ~60 |
| `_document/arguments.py` | Vocabulaire anglais, `sections`, `style` par bloc, `fields`, `code`, `attendance`, `invoice_totals`, groupes, `compute`, listes à trois niveaux, `variant` piloté par `blocks` | +400 / −150 |
| `_document/content.py` | Liens, notes, renvois dans `inline` ; cellules balisées ; calculs et groupes ; sections et plan ; texte fixe | +600 / −150 |
| `_document/formula.py` (nouveau) | La formule des colonnes calculées | ~120 |
| `word/style.py` | `styles.xml` généré depuis les styles résolus, styles nommés, troisième niveau de liste, styles de caractère (`link`) | réécrit, ~450 |
| `word/writer.py` | Sections, signets, texte fixe, nouveaux blocs, `footnotes.xml`, relations des liens ; plus aucune valeur visuelle | +700 / −400 |
| `word/reader.py` | Sections par signets, nouveaux blocs, liens, notes, renvois, styles proposés ; noms anglais | +300 / −100 |
| `pdf/writer.py` | Réécrit sur le style résolu : gabarits de page, en-têtes, disposition, tous les blocs, polices | 451 → environ 1 000 |
| `excel/*` | Noms anglais (types, totaux, graphiques, rôles) | ~80 |
| `memory/files.py` | Noms anglais, champs, code, notes | ~40 |
| Tests | `test_word_styles.py` réécrit ; noms anglais dans environ 250 lignes de tests ; empreintes Word ; parité PDF ; un essai par nouveau contenu ; le test sans valeur visuelle | ~120 fonctions nouvelles ou réécrites |
| Docs | `howto/06-outils-fournis.md` (« Des styles de document », « Des modèles de document »), `fonctions.md`, `configuration.md`, formation (modules 13 à 15), `CHANGELOG` | |
| Exemples | `/home/denis/temp/agent` et le POC shaper : modèles et styles au nouveau format | |

### 12.4 Choix techniques et leurs raisons

- **D'abord les fichiers, ensuite l'héritage.** Une charte qui change `heading1` change aussi tous les styles qui en partent, quel que soit le fichier où ils sont définis. C'est le comportement de Word (`basedOn`), et c'est le seul qui s'explique en une phrase.
- **Des styles Word réels pour les styles nommés.** Plutôt que des propriétés posées paragraphe par paragraphe : le document reste retouchable dans Word, la relecture retrouve le style, et `\u` met un titre de section stylé dans le sommaire.
- **Des signets pour les sections, plutôt que des titres masqués.** Un signet ne s'imprime pas, ne change pas la mise en page, et survit à LibreOffice. Le nom porte la place de la section dans le modèle, `_loom_s3`, parce qu'un nom de signet n'accepte ni espace ni accent.
- **`Decimal` et un `ast` filtré pour les formules.** Les montants ne passent jamais par un flottant, et aucune formule ne peut appeler quoi que ce soit.
- **Une seule valeur par défaut pour les deux formats.** C'est ce qui fait la parité, au prix d'un changement unique des PDF sans style.

### 12.5 Les pièges

1. **YAML et le texte français** : une espace suivie de « : » est une erreur, une espace suivie de « # » coupe le texte (§ 4.4). La règle du `|` et un message traduit les évitent.
2. **YAML lit `000000` comme le nombre 0, et `595959` comme un nombre.** C'est déjà corrigé par `_text_color`, à garder pour chaque couleur, palette comprise. Le prototype est tombé dans le piège.
3. **Les filets hérités** : une valeur déjà analysée (« 1pt 2F5496 ») ne doit pas l'être deux fois quand elle passe d'une base à un style. Le prototype y est tombé aussi : on analyse les propriétés propres d'un style, puis on fusionne.
4. **`align` d'un tableau** désignait sa place dans la page, et se confondait avec l'alignement du texte des cellules : d'où `position`.
5. **Le seuil « tableau étroit »** est calculé aujourd'hui sur la page du style maison (`HOUSE.text_width`), pas sur celle du document. Il est à prendre sur la page du style résolu.
6. **Le schéma d'une paire `fields`** : `prefixItems` n'est pas accepté partout ; une liste de deux textes l'est.
7. **Les signets sont fragiles à la main** : couper-coller une section dans Word peut les perdre. La relecture se rabat alors sur un document libre, et le dit.

## 13. Effets de bord sur le reste de loom

1. ⚠ **Tout agent qui a `word`, `pdf` ou `excel` voit son schéma changer** (noms anglais) : ses runs d'avant ne se rejouent plus à l'identique (« ce qui diffère : les outils proposés »), et le cache de prompt est réécrit une fois.
2. ⚠ **Les configs de la 2.0.20 sont refusées** au chargement, avec la forme nouvelle : `charts`, `images`, `extra_blocks`, un modèle markdown, un style à l'ancien format.
3. ⚠ **Les PDF sans style changent d'aspect** (D-6).
4. **La langue des documents ne change pas** : `fr` par défaut (O-2).
5. **Les `.docx` sans charte ne changent pas**, à l'octet près, si `default.yaml` reproduit bien les valeurs d'aujourd'hui (§ 14).
6. **`loom.memory`** suit les nouveaux noms, et lit davantage (champs, notes, code).
7. **`loom.excel`** : noms anglais seulement ; ni style ni modèle dans ce chantier.
8. **Le moteur, le catalogue et les contrats d'import** ne changent pas. `_style_params` et `_templates` restent légers (Pydantic, PyYAML), pour que la validation d'une config n'importe aucun outil.

## 14. Plan de non-régression

- **Word sans charte :** pour les 8 combinaisons graphiques × images × modèles d'aujourd'hui, réécrites dans le nouveau vocabulaire, le même fichier qu'à `f57347c`, octet pour octet, avec `language: fr`. C'est le test du lot 2 de ce chantier : le style sort du code sans rien changer.
- **Aucune valeur visuelle dans le code :** un test cherche des couleurs hexadécimales et des noms de police dans `word/` et `pdf/`.
- **Un style vide (`{}`) donne le même fichier que `default.yaml` seul.**
- **Ordre du schéma WordprocessingML** vérifié dans chaque partie nouvelle : `footnotes.xml`, styles de caractère, `numbering.xml` à trois niveaux, relations des liens.
- **Aller-retour :** chaque document d'essai, relu, se réécrit à l'identique : sections, champs, groupes, notes, liens, styles proposés.
- **Rendus LibreOffice** de la note et de la facture, en Word et en PDF, sautés sans LibreOffice.
- **Formules :** les refus, les arrondis, et la TVA sur des cas d'arrondi au demi-centime.

## 15. Le découpage en lots

Chaque lot est livrable seul, testé, avec sa documentation.

| Lot | Contenu | Pourquoi dans cet ordre |
| --- | --- | --- |
| **1. Le vocabulaire en anglais** (fait, § 18) | Blocs, valeurs, `none`, rôles et types d'`excel`, `memory` ; `blocks` à la place de `charts`, `images` et `extra_blocks` ; `saut_de_page` retiré, `note` redécrite | Mécanique, mais touche tous les tests : le faire d'abord évite de renommer deux fois |
| **2. Le style hors du code (Word)** (fait, § 19) | `default.yaml`, le format de style (styles nommés, `base`, cascade, couleurs, `language`, `labels`), le chargeur, `word/style.py` piloté par le style ; Word sans charte identique à l'octet ; test sans valeur visuelle | La base de tout le reste |
| **3. Les modèles en YAML et les sections (Word)** (fait, § 20) | Le format du modèle, `sections` dans l'appel, titres écrits par l'outil, `show_title`, `styles`, disposition, texte fixe, signets, relecture ; styles proposés au modèle de langage | Corrige ce qu'ont montré les essais du 10 novembre |
| **4. La parité du PDF** (fait, § 21) | Le même style et les mêmes modèles en reportlab, polices TrueType, notes de fin, `checkboxes`, `signature`, `toc` | Clôt la rupture : les quatre premiers lots forment la 2.1.0 (O-5) |
| **5. Le texte enrichi** (fait, § 22) | `fields`, `code`, liens, notes de bas de page, renvois, emphase dans les cellules, listes à trois niveaux (Word et PDF) | Ajouts sans rupture |
| **6. Les calculs** | Colonnes calculées, groupes et sous-totaux, `invoice_totals` | Le cœur du devis et de la facture |
| **7. L'émargement** | `attendance` | Indépendant des autres ajouts |

Pour comparer, les deux commits des styles du 9 novembre (`d8f025d`, `0f38f5f`) font +4 186 / −354 lignes, docs et tests compris. Ce chantier en vaut, à vue de nez, trois fois plus.

## 16. Décisions

### 16.1 Prises (10 novembre 2026)

| N° | Décision |
| --- | --- |
| D-1 | Le modèle de langage remplit des `sections` nommées ; l'outil écrit leurs titres. Dans un document à modèle, le niveau 1 est réservé aux sections |
| D-2 | Le modèle de document est un fichier YAML ; les textes s'écrivent après `\|` |
| D-3 | Le style maison sort du code : `default.yaml`, au format d'un fichier de style |
| D-4 | Tous les noms en anglais : style, modèle, configuration, vocabulaire, valeurs, `loom.excel` compris |
| D-5 | Le PDF a la parité, dans ce chantier |
| D-6 | Un seul style par défaut : les PDF sans style changent d'aspect une fois |
| D-7 | Les formats de la 2.0.20 sont refusés au chargement, avec la nouvelle forme ; version 2.1.0 |
| D-8 | `saut_de_page` sort du vocabulaire : `page_break` sur une section |
| D-9 | `note` est décrite par son sens |
| D-10 | La largeur d'une image reste |
| D-11 | Les règles de composition restent dans l'outil |
| D-12 | Des styles nommés, avec `base`. Une section choisit ses styles par `styles`, sans propriété en ligne : un style propre à une section se définit, nommé, dans le style du modèle |
| D-13 | Un style qui a une `description` est proposé au modèle de langage, sur son type de bloc (champ `style`) |
| D-14 | `show_title: false` pour ne pas imprimer le nom d'une section |
| D-15 | Partout où l'on écrit `style`, un chemin de fichier ou un style en ligne |
| D-16 | `blocks: [...]` remplace `charts`, `images` et `extra_blocks` |
| D-17 | Nouveaux contenus : `fields`, `code`, liens, notes de bas de page, renvois, emphase dans les cellules, listes à trois niveaux, groupes de tableau, colonnes calculées, `invoice_totals`, `attendance` |
| D-18 | Le QR code de paiement sort du chantier, pour l'instant |

### 16.2 Tranchées le 10 novembre 2026 : les propositions retenues

| N° | Question | Proposition |
| --- | --- | --- |
| O-1 | L'arrondi de la TVA | (a) par taux, sur le total HT de chaque taux ; (b) ligne par ligne. **Retenu : (a)**, la pratique courante. À ma connaissance, le droit de l'UE n'impose pas de méthode : à confirmer avec un expert-comptable avant la première facture réelle |
| O-2 | La langue par défaut des documents (`language`) | (a) `fr`, la langue des documents d'aujourd'hui ; (b) `en`, comme les noms de loom. **Retenu : (a), pour ce chantier** : aucun document existant ne change de libellés ni d'écriture des nombres, et Word reste identique à l'octet. Le passage à `en` ira avec celui des descriptions et des messages (O-4) |
| O-3 | Colonnes calculées et groupes : pour tous, ou déclarés | (a) dans `table`, pour tous (environ 100 tokens) ; (b) déclarés dans `blocks` (`calculations`). **Retenu : (a)** : le devis est le document de base de la cible, et une erreur de calcul coûte plus que 100 tokens |
| O-4 | La langue des descriptions et des messages des outils | (a) le français, dans ce chantier ; (b) l'anglais. **Retenu : (a)** : c'est un chantier de tout loom (moteur, CLI, tous les outils) |
| O-5 | La publication | (a) une seule rupture : 2.1.0 quand les lots 1 à 4 sont faits, puis les lots 5 à 7 en 2.1.x ; (b) une version par lot. **Retenu : (a)** |
| O-6 | Les notes de bas de page en PDF | (a) en fin de document ; (b) de vraies notes de bas de page, plus tard. **Retenu : (a)** (analyse du 9 novembre, D-4) |
| O-7 | Une police « aperçu et impression » (`fsType 4`) | (a) refusée au chargement ; (b) avertie. **Retenu : (a)** (analyse du 9 novembre, D-8) |

## 17. À vérifier sur ton poste

1. **Dans Word, un style nommé** (`message_title`) paraît dans la galerie de styles, et un sommaire mis à jour reprend le titre qui le porte. Écrit depuis le lot 3 (§ 20) ; reste à l'ouvrir dans Word, LibreOffice seul l'a vu.
2. **La règle d'arrondi de la TVA** avec ton expert-comptable (O-1).

## 18. Ce qui est écrit : le lot 1 (10 novembre 2026)

Codé sans commit sur `f57347c` et tes cinq fichiers en cours. La version est la section `2.1.0 - non publiée` du CHANGELOG ; `pyproject.toml` n'est pas touché (`set_version`).

**Ce qui change**

- **Le vocabulaire (§ 7.1, 7.2)** : blocs, types de colonne, totaux, sortes de graphique, largeurs d'image, `none` et rôles d'`excel`, dans les trois outils, dans la relecture d'un `.docx` et dans `loom.memory`. Les descriptions et les messages restent en français (O-4) ; un refus se dit « 'environ 800' n'est pas une valeur amount_eur ».
- **`blocks` (§ 7.3, D-16)** : `_office_params.py` porte la liste des blocs facultatifs (`chart`, `image`, `signature`, `toc`, `checkboxes`) et le refus des anciennes clés, avec la forme qui les remplace. Un bloc inconnu ou nommé deux fois est refusé. `variant()` construit le schéma depuis cette liste, dont l'ordre ne change rien. `loom.pdf` refuse `signature`, `toc` et `checkboxes` jusqu'au lot 4.
- **`saut_de_page` retiré (D-8)** : `PageBreak` et `BreakOut` sortent d'`arguments.py`, de `content.py` et des deux écritures. Jusqu'au lot 3 (`page_break` sur une section), aucun document n'a de saut de page ; la 2.1.0 réunit les lots 1 à 4 (O-5), donc aucune version publiée n'en sera privée.
- **`note` redécrite (D-9)** : « ce que le lecteur doit retenir, une mise en garde ».
- **La relecture** : quand un document contient un bloc que l'outil n'écrit pas, la note nomme ces blocs et la clé qui les ajoute (« … ou blocks: [chart] dans la config de l'outil »). `_warn_unwritten` remplace les deux avertissements d'avant.
- **`loom validate`** dit `blocs : chart, image`, ou `aucun`.

**En plus de ce qui était prévu (§ 12.3)**

- **`agents/spec.py`** : la référence d'un outil fourni sautait les validations « avant » de son modèle de paramètres, et une ancienne clé y donnait « Extra inputs are not permitted ». Un modèle de paramètres peut maintenant déclarer `precheck`, appelé en premier : le refus avec la nouvelle forme s'affiche aussi au chargement de la config. Le refus de `style` sur `loom.pdf` en profite.
- **`howto/06-outils-fournis.md`** : l'exemple de modèle markdown nomme les types en anglais (« Quantité (integer), Montant HT (amount_eur) »). Les commentaires de coût de `word_params.py` et `pdf_params.py` prennent les valeurs mesurées.

**Non-régression (§ 14)**

Pour un même contenu, 17 documents et un classeur sont identiques, octet pour octet, entre la base (vocabulaire français, anciennes clés) et le lot 1 (anglais, `blocks`) :

- 8 en Word, avec ou sans graphiques, images et modèle déclaré ;
- 4 qui suivent un modèle ;
- 4 en PDF ;
- un avec signature, sommaire et liste à cocher ;
- un classeur à graphiques, rôles et totaux.

La relecture rend les mêmes blocs. L'empreinte du devis d'essai (`DEVIS_SHA256`) est celle du même devis sans saut de page à la base.

**Coût mesuré (§ 12.2)** (description et schéma, en caractères) :

| `word` | Avant | Lot 1 |
| --- | --- | --- |
| Sans bloc facultatif | 5 605 | 5 402 |
| `chart` | 7 411 | 7 102 |
| `image` | 6 454 | 6 172 |
| `signature`, `toc`, `checkboxes` | 6 465 | 6 217 |
| Chacun seul : `signature`, `toc`, `checkboxes` | — | 5 834, 5 622, 5 499 |
| Tout, sans modèle | 9 114 | 8 753 |

`pdf` sans bloc : 5 557 (5 762 avant). Le gain, −203 caractères sans bloc, dépasse les −60 prévus : le saut de page et la note n'en sont qu'une part, les noms anglais sont plus courts.

**Essais** : 9 de plus :

- les anciennes clés refusées, au chargement aussi ;
- un bloc inconnu ou nommé deux fois ;
- les blocs du PDF ;
- le saut de page refusé ;
- les variantes par `blocks` ;
- la note de relecture.

Les essais existants passent au nouveau vocabulaire.

Résultats : 4 331 réussis, 201 sautés (extras de l'environnement de travail). `ruff check`, `ruff format`, `lint-imports` (6 contrats) et `pyright` sont sans erreur.

**Pas dans ce lot** :

- `templates` sous forme courte et `style` sur `loom.pdf` (lots 2 et 3) ;
- les exemples (§ 12.3).

Dans `temp/agent`, `prompts/templates/note.yaml` nomme encore `paragraphe`, `liste` et `titre` dans ses consignes. Le schéma ne les accepte plus : le modèle de langage risque un premier appel refusé. Ce fichier sera de toute façon réécrit au format YAML au lot 3. La config de l'agent, elle, se charge telle quelle (`loom validate` : `blocs : aucun`).

## 19. Ce qui est écrit : le lot 2 (10 novembre 2026)

Codé sans commit sur `ec3a467` (le lot 1). La section `2.1.0 - non publiée` du CHANGELOG a un paragraphe de plus.

**Ce qui change**

- **Le format de style (§ 3)** : styles nommés, `base`, cascade puis héritage (§ 3.6), réglages (§ 3.4), `language` et `labels` (§ 9), pour ce que Word écrit aujourd'hui. `_style_params.py` est réécrit : la lecture (`StyleFile`), la résolution (`resolve` : `default.yaml`, la charte, le style du modèle), le style résolu (`Style`, un `Look` par style nommé).
- **`tools_builtin/styles/default.yaml` (§ 3.5)** : le style maison, aux valeurs du code d'avant. Il est empaqueté avec loom (vérifié dans la roue).
- **`word/style.py` traduit les styles** : un style Word écrit ce que son style nommé déclare en plus du style nommé de sa base Word (`Style.between`), dans l'ordre du schéma ; Word reprend le reste par `basedOn`. Le code n'ajoute que la mécanique : titre gardé avec la suite, niveau de plan, numérotation, retraits des listes. `word/writer.py` et `word/chart.py` prennent couleurs, filets, fonds, libellés et langue dans le style.
- **Le chargeur** lit un chemin ou un style écrit en ligne (D-15), les logos (`logo` est un chemin), et résout la cascade au chargement pour dire ses fautes : couleur inconnue, base inconnue ou en boucle, propriété hors de son style. Un texte YAML qui contient « : » reçoit la règle des guillemets.
- **La langue** : `prepare` reçoit les libellés et la langue du style du document (nombres, dates, booléens, « Total ») ; `loom.pdf` reste en français.
- **Les couleurs des graphiques** des trois outils viennent de `default.yaml` (`_chart_style` le lit) ; Word prend celles de son style.
- **Le seuil « tableau étroit »** se prend sur la page du style (piège 5 du § 12.5).

**Écarts avec l'analyse, à valider**

1. **`signature_mention`**, un style de plus que le § 3.2 : la mention en italique n'a pas les espacements du libellé.
2. **`toc` part de `heading1`** dans `default.yaml` : le titre du sommaire suit les titres de la charte, comme avant.
3. **`watermark` est un style** (`text`, `font`, `color`), plus un réglage ; `watermark: BROUILLON` reste permis.
4. **`chart` a `text`, `grid` et `title`** en plus de `colors`.
5. **Ce qui reste dans le code** : le blanc entre deux secteurs, la police de symboles des cases à cocher (un glyphe, comme une puce), la mise en forme des entrées du sommaire (`toc 1` à `toc 3`), le titre d'une note en gras, la place pour signer (2 cm).
6. **Un style à toi déclare sa `base`** : sinon une faute de frappe (`heading_1`) serait un style à toi sans effet. Le message propose le nom proche.
7. **Ce qui attend le lot 3** : les styles à toi ne sont pas encore écrits dans `styles.xml`, ni `description` permise (§ 3.7), faute d'un modèle qui les choisisse.
8. **Ce qui attend les lots 4 à 7** : `fonts` (TrueType, § 10), refusé en clé de la 2.0 en attendant ; les styles des nouveaux blocs (`code`, `link`, `footnote`, `fields`, groupes, `invoice_totals`, `attendance`).
9. **La disposition reste dans le style du modèle** jusqu'au lot 3, valeurs en anglais : `title: hidden` ou `quiet`, `size: small`, `align: left`…, `table` à la place de `tables`. Le titre discret prend la couleur de `heading3` et la taille de `text`.
10. **`language: en`** écrit `en-US`, « €1,250.50 », les dates en MM/JJ/AAAA (§ 9), « Yes » et « No ».
11. **`_style_params.py` fait 1 074 lignes**, plutôt que les 400 estimées : la disposition provisoire, les libellés et les messages de la 2.0 en font environ 250.

**Non-régression (§ 14)**

Sans charte, les 17 documents et le classeur d'essai du lot 1 sont identiques, octet pour octet, entre `ec3a467` et le lot 2. Le `styles.xml` du style maison aussi (`HOUSE_STYLES_SHA256`), et un style vide (`{}`) donne le même fichier que `default.yaml` seul.

**Essais**

- `test_word_styles.py` est réécrit au nouveau format : 67 essais, dont ceux qui suivent.
  - Aucune couleur ni police dans `word/`.
  - La cascade et l'héritage.
  - 17 fautes de fichier et 5 fautes de cascade.
  - Le style en ligne dans la config.
  - Un document en anglais.
  - Un pied de page qui garde ses numéros.
  - Le cadre au style `box`.
- Suite complète : 4 358 réussis, 201 sautés (27 de plus qu'au lot 1).
- ruff, format, `lint-imports` et pyright passent sans erreur.

**Ton agent `temp/agent`**

Sa charte et le style de la note sont au format de la 2.0 : la config est refusée au chargement, avec la forme nouvelle. Récrits au nouveau format, ils donnent la même note, octet pour octet (avec et sans modèle). La couleur `primary` doit être posée sur les titres, puisqu'une couleur n'a plus de rôle :

```yaml
# prompts/styles/charte.yaml
colors:
  primary: 3A56E4
document_title: { size: 24, color: primary, align: center, uppercase: true, underline: false }
heading1: { color: primary }
heading2: { color: primary }
```

```yaml
# prompts/styles/note.yaml
heading1: { underline: true }
sections:
  Destinataires: { title: quiet, size: small }
  Message: { title: quiet, size: small }
  Consignes: { title: quiet, size: small }
  Contact: { title: quiet, size: small }
```

## 20. Ce qui est écrit : le lot 3 (10 novembre 2026)

Codé sans commit sur `1fe6f7a` (le lot 2). La section `2.1.0 - non publiée` du CHANGELOG a un paragraphe de plus.

**Ce qui change**

- **Le modèle de document est un fichier YAML (§ 4.1, D-2)** : `_templates.py` est réécrit (`TemplateSpec`, `SectionSpec`, `Template`). Le chargeur lit le modèle, puis le style qu'il nomme (`style:`, un chemin ou une table), et range ce style à côté du texte du modèle. Un modèle markdown, la forme longue `{file, style}` et un style qui porte `sections` ou `beside` sont refusés avec la forme nouvelle.
- **`sections` dans l'appel (§ 5, D-1)** : le champ n'existe que si l'outil a des modèles. `prepare` place les sections dans l'ordre du modèle, écrit leurs titres, refuse un niveau 1 dans une section, et dit les fautes du plan avec les sections attendues. `blocks` reste le champ du document libre (`template: none`) ; l'un pour l'autre est refusé par le schéma.
- **Le texte fixe (§ 4.3)**, `show_title` (D-14), la disposition des sections (§ 4.1) et `styles` (D-12) : `word/writer.py` pose les sections par lignes du modèle (`_groups`, `_units`) ; le style d'un élément se choisit dans l'ordre bloc, section, élément (`_style_id`).
- **Les styles à toi sont écrits dans `styles.xml`** (`word/style.py`, `_custom`) : un style Word de leur nom, basé sur le style Word de leur base, qui n'écrit que ce qu'il déclare de plus qu'elle (`Style.between`).
- **Les styles proposés (§ 3.7, D-13)** : `description` est permise à un style à toi qui part d'un style de bloc. `variant()` ajoute aux blocs concernés un champ `style` (un `Literal` des noms proposés pour ce type) ; ceux de la charte vont dans la description de l'outil, ceux d'un modèle dans le résultat d'`action: template`. `prepare` vérifie qu'un style est permis pour ce document, ce type de bloc et ce niveau de titre.
- **La relecture (§ 6)** : `word/reader.py` range les blocs par section et rend `template` et `sections` ; le titre d'une section, une section au texte fixe et le titre du document ne reviennent pas ; un style proposé revient dans `style`.
- **`loom.pdf`** suit les modèles YAML et écrit par `sections`, sans leur forme (refusée au chargement jusqu'au lot 4).

**Écarts avec l'analyse, à valider**

1. **Le signet d'une section est un point, pas une enceinte (§ 6).** L'essai d'aller-retour dans LibreOffice l'a montré sur la facture, dont la section Description commence par un tableau : LibreOffice déplace un signet posé hors d'un paragraphe (avant un tableau) jusqu'au paragraphe qui suit le tableau, et dédouble la fin de ceux qui couvrent plusieurs paragraphes (un début sans fin, puis un signet vide). Le signet `_loom_s<n>` est donc posé dans le premier paragraphe de la section, celui de la première cellule quand elle commence par un tableau, et la section court jusqu'au signet de la suivante ; sa fin ne compte pas. L'essai A du § 12.1 n'avait que des sections qui commencent par un titre. Après un passage dans LibreOffice, la facture se relit par sections, côte à côte comprises.
2. **`space_before` reste une clé de section** (l'adresse d'un courrier dans la fenêtre de l'enveloppe), absente du tableau du § 4.1.
3. **Le titre discret et le petit texte ne sont plus calculés** : `title: quiet` prenait la taille de `text` et la couleur de `heading3`, `size: small` deux points de moins que `text`. Un modèle les déclare en styles nommés (`quiet_title`, `small_text`), choisis par `styles` (D-12). De même, `table: {size: …}` d'une section devient un style qui part de `table`.
4. **`styles` met aussi en forme `caption` et `box`** (le cadre d'une section encadrée), en plus des titres et des blocs.
5. **Un défaut du lot 2 corrigé** : les filets et le fond d'un style de la famille `table` (`border_top`, `border_bottom`, `border`, `border_left`, `fill`) s'écrivaient aussi dans le style de paragraphe des cellules, donc un filet dans chaque cellule. Ils ne vont plus qu'au tableau et à ses lignes. Rien de publié n'en a souffert.
6. **Un style proposé coûte plus que l'estimation** du § 12.2 (80 caractères) : 325 pour le premier de la charte (la ligne « Styles proposés », le champ `style` et sa description dans le schéma), 239 pour un autre type de bloc, 40 pour un autre style d'un type déjà proposé. `sections` coûte 425 caractères (150 estimés) : le champ (environ 250, les blocs dits une fois dans `$defs/Block` pour `blocks` et `sections`) et une phrase de description.
7. **Les messages disent `(optional)` et `(fixed)`** dans la liste des sections attendues, les clés du modèle, comme le § 5 le prévoit ; le reste du message est en français (O-4).
8. **La pagination de la lecture** (`start`, `next_start`, `block_count`) compte toujours les blocs du document entier, titre compris : un document à modèle long se lit par fenêtres, chacune rendue par sections.
9. **`_style_params.py` passe de 1 074 à 944 lignes** (la disposition provisoire du lot 2 en sort), `_templates.py` de 165 à 415 (estimé 250 : les règles de la disposition et leurs messages).

**Non-régression (§ 14)**

Les documents libres (avec ou sans modèles déclarés, `template: none`), les PDF et le classeur d'essai sont identiques, octet pour octet, au lot 2. Un document qui suit un modèle aussi, ses signets en plus : le `document.xml` du devis d'essai, sans ses deux signets, est celui du lot 2, et son PDF a la même empreinte. La facture et le rapport du lot 2, réécrits au format du lot 3, gardent leur mise en page (rendus LibreOffice : une page pour la facture, quatre pour le rapport dont une en paysage).

**Essais**

- `test_document.py` : 112 (20 de plus) : les sections placées dans l'ordre du modèle, les fautes du plan avec les sections attendues, une section sans titre, le schéma à modèles (action et deux champs), les styles proposés dans le schéma, chaque faute de modèle refusée au chargement.
- `test_word.py` : un modèle écrit, relu par sections et réécrit à la même empreinte ; des sections perdues ; le chargement.
- `test_word_styles.py` réécrit aux modèles YAML : la facture et le rapport mis en page par leurs sections, le texte fixe, les styles proposés de la charte et du modèle, les styles des sections vérifiés au chargement, les signets retrouvés après un aller-retour `.docx` → `.docx` dans LibreOffice.
- `test_pdf.py` : un PDF qui suit son modèle ; la forme refusée.
- Suite complète : 4 379 réussis, 201 sautés (21 de plus qu'au lot 2). ruff, format, `lint-imports` (6 contrats) et pyright passent sans erreur.

**Ton agent `temp/agent`**

Son modèle (`prompts/templates/note.yaml`) est encore en markdown, et ses deux styles au format de la 2.0 : la config est refusée au chargement. Récrits comme ci-dessous, ils donnent la même note, à deux détails près : les signets des sections, et une liste un peu plus serrée, ses puces prenant maintenant la taille du petit texte (9 pt). Les titres de section discrets et le petit texte valent pour toutes les sections : ils vont dans le style de la note, sans `styles` dans le modèle.

```yaml
# agents/agent.yaml : la référence du modèle, le chemin seul
  templates:
    note: templates/note.yaml
```

```yaml
# prompts/styles/charte.yaml
colors:
  primary: 3A56E4
document_title: { size: 24, color: primary, align: center, uppercase: true, underline: false }
heading1: { color: primary }
heading2: { color: primary }
```

```yaml
# prompts/styles/note.yaml : les titres de section discrets, le petit texte
heading1: { underline: true, size: 11, color: 2F5496, space_before: 10, space_after: 3 }
paragraph: { size: 9 }
list: { size: 9 }
```

```yaml
# prompts/templates/note.yaml
description: Note courte à l'équipe ou aux clients (fermeture, changement d'horaires, consigne).

instructions: |
  Écris la note en **un seul appel**.

  - **Fichier** : `workspace/note-<objet>.docx` (l'objet en minuscules, mots reliés par des tirets), sauf si l'utilisateur donne un nom.
  - **`title`** : l'objet de la note en quelques mots (« Fermeture du dépôt »).
  - **N'invente rien** : dates, heures, lieux et noms viennent de l'utilisateur. S'il manque la date ou le lieu, demande-les avant d'écrire.
  - **Souligne** (`<u>…</u>`) les dates, heures et lieux qui comptent ; rien d'autre.

style: styles/note.yaml

sections:
  Destinataires:
    instructions: |
      Un `paragraph`, une information par ligne : « À : » les destinataires ; « De : » l'émetteur ; « Le : » la date de la note.
  Message:
    instructions: |
      Un ou deux `paragraph` courts : ce qui se passe, quand, où, et pourquoi si l'utilisateur l'a dit.
  Consignes:
    optional: true
    instructions: |
      Une `list` de ce que les destinataires doivent faire, une action par élément.
  Contact:
    instructions: |
      Un `paragraph` : qui joindre pour une question, et comment.
```

Les consignes perdent « dans l'ordre des sections ci-dessous » (l'outil range les sections) et « ne le répète pas en bloc `titre` » (le bloc n'existe plus, et un titre de niveau 1 est refusé dans une section) ; `paragraphe` et `liste` deviennent `paragraph` et `list`.

## 21. Ce qui est écrit : le lot 4 (10 novembre 2026)

Codé sans commit sur `0959fb1` (le lot 3). La section `2.1.0 - non publiée` du CHANGELOG a un paragraphe de plus. Avec ce lot, les quatre premiers forment la 2.1.0 (O-5).

**Ce qui change**

- **Le style dans `loom.pdf` (§ 10, D-5)** : `pdf_params.py` prend `style`, les modèles gardent leur style et leur forme. `read_styles` et les styles proposés passent de `word/tool.py` à `_document/tool.py`, communs aux deux outils : la charte et le style de chaque modèle sont résolus et vérifiés au montage de la même façon.
- **`pdf/writer.py` réécrit** sur le style résolu. Un style nommé devient un `ParagraphStyle` de reportlab ; un style qui a un filet ou un fond (la note, la citation, le titre du document) devient une cellule de tableau qui les dessine, comme Word les pose autour d'un paragraphe. Les sections sont posées comme dans Word (le même découpage en lignes du modèle) : côte à côte et encadrées dans un tableau sans bordure, colonnes en `BalancedColumns`, paysage et page de garde en gabarits de page. L'en-tête, le pied et le filigrane sont dessinés à chaque page (`onPage`).
- **Le sommaire** a ses vrais numéros de page : un premier rendu note la page de chaque titre (un rappel `onDraw`, qui marche aussi dans des colonnes), le suivant les écrit, avec « Page n / N » ; trois rendus au plus. Chaque entrée est un lien vers son titre.
- **Les polices (§ 10)** : `fonts`, un réglage du fichier de style ; le chargeur lit les fichiers comme les logos, `font_fault` les vérifie (bibliothèque standard), le montage de `loom.pdf` les enregistre dans reportlab. Les familles standard du PDF sont une table de `_style_params.py` (`PDF_FONTS`) : le code de `pdf/` n'a ni couleur ni nom de police, et un essai le garde, comme pour `word/`.
- **Sans style (D-6)** : `default.yaml`. Les PDF sans style changent d'aspect, et d'empreinte.

**Écarts avec l'analyse, à valider**

1. **Les notes de fin (O-6) ne sont pas dans ce lot** : sans notes de bas de page (lot 5), elles n'ont rien à rendre. Elles viendront avec elles.
2. **Les polices à empattement se rendent en Times** (Times New Roman, Cambria, Georgia, Garamond), plus en Helvetica comme le dit le § 10 ; Courier New en Courier, le reste en Helvetica. C'est la proposition D-2 (a) de l'analyse du 9 novembre : l'équivalent le plus proche.
3. **Les droits d'embarquement** : 0 et 8 acceptés, 2 et 4 refusés comme prévu, et aussi 0x100 (embarquer la police entière : le PDF n'en embarque qu'une partie) et 0x200 (en images seulement). **4 familles au plus** par fichier de style, chaque fichier à 2 Mo.
4. **Les cases d'une liste à cocher sont toujours dessinées**, même avec une police embarquée qui a ☐ et ☒ : le même dessin partout.
5. **Sans fichier `bold` ou `italic`**, le gras et l'italique d'une police embarquée s'écrivent dans sa graisse normale (Word, lui, les simule).
6. **`fonts` ne sert qu'au PDF** (§ 3.4) : Word nomme la police sans l'embarquer, et `loom validate` le dit toujours pour `loom.word`.
7. **Ce qui reste dans le code**, comme dans Word : la mise en forme des entrées du sommaire (le niveau 1 en gras, un retrait par niveau, des points de conduite), l'espace sous un tableau ou une image (celui du paragraphe vide que Word pose après : une ligne et 6 points), la distance de l'en-tête et du pied au bord (1 cm), le filigrane (45°, à demi transparent, à la largeur du cadre de Word).
8. **Une page trop petite pour un bloc** : une section côte à côte ou encadrée, une note, se coupent d'une page à l'autre ; une image plus haute que la page est réduite ; un bloc qui ne tient toujours pas (une ligne de tableau très haute) est refusé avec un message, au lieu de l'erreur de reportlab.
9. **`pdf/writer.py` fait 1 466 lignes**, plutôt que les 1 000 estimées (§ 12.3).

**Non-régression (§ 14)**

Les documents Word sont identiques, octet pour octet, au lot 3 : les 17 documents et le classeur d'essai, et les empreintes des essais de styles. Les PDF changent, comme prévu (D-6). La facture et le rapport des essais de Word, rendus en PDF puis comparés à leur rendu LibreOffice, ont la même page : mêmes zones, mêmes couleurs, mêmes filets, la même mise en page des sections ; le PDF a en plus les numéros de page du sommaire, et un pied centré sur la page en paysage.

**Essais**

- `test_pdf.py` : 52 (8 de plus) :
  - aucune valeur visuelle dans `pdf/` ;
  - le PDF sans style dans `default.yaml` ;
  - la facture et le rapport des essais de Word, en PDF ;
  - signature, sommaire et cases ;
  - les styles proposés et l'anglais ;
  - une police embarquée (sautée sans la police Caladea) ;
  - les polices vérifiées au chargement ;
  - ce que `loom validate` dit des polices.
- `test_word_styles.py` : `loom.pdf` prend les styles et les trois blocs (deux essais réécrits).
- Suite complète : 4 387 réussis, 201 sautés (8 de plus qu'au lot 3). ruff, format, `lint-imports` (6 contrats) et pyright passent sans erreur.

## 22. Ce qui est écrit : le lot 5 (10 novembre 2026)

Codé sans commit sur `8075e94` (le lot 4). Le CHANGELOG ouvre une section `2.1.1 - non publiée` : les lots 5 à 7 vont en 2.1.x (O-5).

**Ce qui change**

- **Le texte enrichi (§ 8.3 à 8.5)** : `content.py` lit les liens, les notes et les renvois avec l'emphase (`rich`) : chaque lien, note ou renvoi prend la place d'un caractère réservé le temps de lire l'emphase autour de lui, qui peut donc l'envelopper (`**[le guide](https://…)**`). Un `Run` gagne `link`, `note` (les segments de la note) et `ref` (le rang du titre visé). Les titres visés sont connus avant les blocs, dans l'ordre où ils seront écrits : ceux du document libre, ou, pour un modèle, le nom de chaque section imprimée puis ses titres. Un titre, une légende, un en-tête de colonne, un libellé de champ, la source d'une citation, le titre d'une note ou du document n'ont que l'emphase.
- **Word** : `w:hyperlink` et une relation externe par adresse et par partie (le corps, les notes) ; `footnotes.xml`, ses deux séparateurs et `footnotePr` dans les réglages, seulement s'il y a des notes ; un signet `_Ref_loom_<rang>` sur chaque titre visé (identifiants à partir de 2000) ; un renvoi en champ `REF` ou en lien interne. Les styles `Hyperlink`, `FootnoteText`, `FootnoteReference`, `LoomCode`, `LoomFields` (tableau), `LoomFieldValue` et `LoomFieldLabel` ne sont écrits que s'ils servent. La numérotation gagne deux définitions à trois niveaux (puces, numéros), écrites seulement pour une liste qui descend au troisième.
- **PDF** : `<a href>` pour un lien, `<a href="#…">` vers une ancre pour un appel de note et un renvoi ; les notes en fin de document (O-6) ; les champs en `Table` ; le code en `Paragraph` aux espaces insécables ; les listes en `ListFlowable` imbriqués.
- **Les cellules** (§ 8.6) : une colonne de texte est préparée comme un paragraphe ; `TableOut.cells` porte les segments, `rows` garde le texte seul (largeur des colonnes, relecture).
- **`fields` et `code`** (§ 8.1, 8.2) : deux blocs facultatifs de `blocks`, leurs styles dans `default.yaml`, `label_width` propre à `fields`.
- **La relecture** : les notes (de bas de page et de fin) sont lues avant le corps ; un renvoi lu avant son titre attend la fin de la lecture, qui le remplace par `§[Titre]` (le signet est celui d'un titre) ou par son texte (un autre signet). Le champ complexe de Word (« Insertion > Renvoi ») est lu comme le champ simple de l'outil.

**Écarts avec l'analyse, à valider**

1. **Le code est un seul paragraphe**, ses lignes séparées par des sauts de ligne (`w:br`), plutôt qu'un paragraphe par ligne (§ 8.2) : un bloc reste un bloc à la relecture (deux blocs de code qui se suivent ne fusionnent pas), et `keepLines` le garde sur une page quand il y tient. Dans le PDF, un `Paragraph` aux espaces insécables plutôt que `XPreformatted` : une ligne trop longue passe à la suivante au lieu de sortir du cadre.
2. **Le champ `REF` est `\n \t \h`**, plutôt que `\r \h` (§ 8.5) : `\n` donne le numéro sans son point final (« 2 » et non « 2. »), `\t` sans le texte du niveau (« 2 » et non « Article 2 – »), ce qui se lit dans une phrase (« voir l'article 2 »). LibreOffice ignore `\t` : pour un titre d'article (`numbering: article`, niveau 1), le renvoi est son numéro en lien, que Word ne recalcule pas si l'on insère un article à la main. Les niveaux 2 et 3 restent en champ.
3. **Notes et renvois vont aussi dans les cellules et les valeurs de `fields`**, pas seulement les liens et l'emphase (§ 8.6) ; le § 8.4 le disait déjà pour les notes.
4. **Le texte fixe d'un modèle n'a que les liens** : ni note ni renvoi, que l'analyse ne prévoyait pas.
5. **Les notes de fin du PDF** sont sous « Notes » (`labels.notes`, un libellé de plus), au style `heading1` mais sans numéro ni place au sommaire ; chaque note commence par son numéro en exposant. Après une page de garde seule, elles ouvrent une page.
6. **La relecture des cellules** : l'emphase d'une ligne d'en-tête (`tblHeader`) ou d'une ligne toute en gras (un total, ou l'en-tête d'un document écrit ailleurs) ne revient pas : c'est celle du style, et la réécriture la doublerait.
7. **La relecture va au-delà de l'outil** : un renvoi de Word vers un signet de titre (champ `REF` simple ou complexe, lien interne) revient en `§[Titre]`, une note de fin en `^[…]` (réécrite en note de bas de page) ; un lien vers un autre schéma (un fichier) revient en texte ; un `^[` ou un `§[` du texte revient échappé.
8. **Une liste à cocher garde deux niveaux** (« une liste à cocher a deux niveaux »).
9. **La puce du troisième niveau** est ▪ dans Word ; dans le PDF aussi quand la police l'a, sinon (les polices standard) une puce • plus petite.
10. **`label_width`** : 1 à 10 cm, et la moitié de la largeur au plus.
11. **Le coût dépasse l'estimation** (§ 12.2) : +460 caractères pour tous (350 à 450 estimés), dont 245 de description (la syntaxe et ses limites) et 215 de schéma (l'élément de liste imbriqué deux fois) ; `fields` +458 (320 estimés pour le schéma seul), `code` +303 (250).

**Non-régression (§ 14)**

Les documents sans lien, note, renvoi, champ, code ni troisième niveau sont identiques, octet pour octet, au lot 4, en Word comme en PDF : les 17 documents et la relecture du script d'empreintes, et `DEVIS_SHA256` et `HOUSE_STYLES_SHA256`. Un document qui a tout (liens dans le texte, les cellules, les notes et les champs ; notes ; renvois en numérotation `none`, `decimal` et `article` ; trois niveaux ; champs ; code) passe les schémas XSD de l'ISO/IEC 29500 ; LibreOffice l'ouvre, met les notes en bas de page et recalcule les renvois (« 2 », « 2.1 ») ; relu puis réécrit, il redonne le même fichier.

**Essais**

- `test_document.py` : 125 (13 de plus) : liens, notes et renvois préparés (l'emphase autour d'un lien, une note qui contient un lien et un renvoi, les échappements, un lien sans schéma) ; six refus (schéma, note non refermée, note dans une note, note trop longue, titre inconnu, deux titres de même nom) ; titres, légendes et libellés sans lien ni note ; 100 notes au plus ; renvois dans un modèle (section imprimée, section sans titre refusée) ; trois niveaux et la liste à cocher à deux ; champs et code ; cellules enrichies ; les variantes avec `fields` et `code`.
- `test_word.py` : 72 (7 de plus) : un document enrichi écrit (parties, relations, signets, styles), relu et réécrit au même fichier ; le renvoi à un titre numéroté (`decimal` : champ `REF` ; `article` : numéro en lien) ; pas de notes sans notes ; un document d'ailleurs (champ `REF` complexe, lien interne, note de fin, lien vers un fichier, signet qui n'est pas un titre) ; l'emphase des cellules, sauf l'en-tête et le total ; LibreOffice recalcule le renvoi.
- `test_word_styles.py` : 70 (4 de plus) : les styles du texte enrichi pris à la charte (lien, notes, code, champs, `label_width`, numérotation à trois niveaux, ordre du schéma) ; aucun sans lui ; le coût de `fields` et `code`, et la note de relecture d'un outil qui ne les déclare pas ; `label_width` propre à `fields`.
- `test_pdf.py` : 54 (2 de plus) : liens, notes de fin, renvois, trois niveaux, champs et code dans un PDF ; pas de « Notes » sans notes.
- `test_memory_tool.py` : les champs et le code en markdown (un essai complété).
- Suite complète : 4 413 réussis, 201 sautés (26 de plus qu'au lot 4). ruff, format, `lint-imports` (6 contrats) et pyright passent sans erreur.

## Sources

- `docs/outil-word-styles-analyse.md`, `docs/outil-word-modeles-analyse.md`, `docs/outil-styles-lot2-analyse.md`, `docs/outil-word-souligne-analyse.md` : les lots précédents.
