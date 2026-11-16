# Le format des fichiers de style — analyse et format

Statut : proposition du 16/11/2026, faite le 16/11/2026 (lots 1 à 6, § 12), dans loom-ia 2.0.21 (non publiée). « Avant » désigne le format d'avant ces lots, table d'en-tête et de pied comprise. Les tableaux des §§ 5, 7 et 8 sont vérifiés contre le registre des propriétés (`PROPERTIES`) et le schéma des réglages par `test_the_format_document_matches_the_registry`. Concerne `loom.word` et `loom.pdf` : `default.yaml`, la charte d'un outil (`style:`), le style d'un modèle de document. Rien ne change pour le modèle de langage : ni la description ni le schéma des outils ne parlent des styles.

## 1. Constat

Toutes les propriétés vivent dans un seul modèle (`Props`) ; `_OWNERS` réserve certaines à un style et à ceux qui en partent. La séparation « communes / propres » existe donc, mais sans règle, et cinq listes se tiennent à la main (`Props`, `_OWNERS`, `Look`, le rendu Word et PDF, les tableaux de la doc). Ce qui en résulte :

- **Une clé, plusieurs sens.** `text` : le style de base, le texte du filigrane, la couleur du texte des graphiques (`chart.text`), le contenu d'une cellule ou d'une ligne. `size` : la taille de police, le format de papier (`page.size`). `colors` : les couleurs nommées, les séries d'un graphique (`chart.colors`). `width` : `full | content` (table), 10 à 90 % (section), `full | half | small` (bloc image). `borders` : deux listes de valeurs (table, table d'en-tête). `table` : un style, et une propriété de `header` et `footer`.
- **Une notion, plusieurs noms.** Le placement : `align`, `position`, `logo_position`, `logo: true`. Les largeurs : `width`, `widths`, `logo_width`, `label_width`.
- **Le contenu dans le style.** `header` et `footer` portent leur apparence et ce qu'ils écrivent (`left`, `center`, `right`, `lines`, `table`, `logo`) ; `watermark` porte son texte.
- **Trois façons de surcharger** : un style à toi (`base:`), `styles:` d'une section, des propriétés en ligne (`align`, `valign` d'une cellule ou d'une ligne, `space_before` d'une section).
- **Des couplages.** `center` seul suit `align`, et n'est pas centré quand `align` ne l'est pas.
- **Des unités implicites et hétérogènes** : points, centimètres, pourcentage, multiplicateur ; une même grandeur bornée autrement selon l'endroit (`space_before` : 0 à 72 dans un style, 0 à 20 dans une section).
- **Des asymétries** : `border_left` sans `border_right`.
- **Des valeurs visuelles dans le code** (§ 11), contre le principe « tout vient des fichiers » : le test `test_no_visual_value_*` ne vérifie que les couleurs et les polices.

## 2. Principes

1. **Un style et une propriété ne se confondent pas.** Un style est un ensemble nommé (`body`, `heading1`, `footer_page`) ; une propriété (`font`, `size`, `align`) ne s'écrit que dans la définition d'un style, dans un fichier de style. Partout ailleurs — section d'un modèle, cellule, ligne, zone, bloc —, on désigne un style par son nom (`style:`, `styles:`), **sans exception**.
2. **Le contenu n'est pas un style.** Ce qu'écrivent l'en-tête, le pied et le filigrane va dans le réglage `page` (§ 6), comme dans Word où ils appartiennent à la page ; leur apparence reste aux styles `header`, `footer`, `watermark`.
3. **Une clé, un sens, partout.** Un nom n'est jamais à la fois un style et une propriété, ni une propriété et un réglage de sens différent. `text` est toujours un contenu.
4. **Deux mots pour le placement.** `align` : le texte dans sa boîte ; `position` : une boîte (une table) dans la largeur du texte. `valign` : le texte dans la hauteur de sa cellule. Un `center` est toujours centré.
5. **Des unités standard** (§ 4) : un nombre seul prend l'unité de sa famille ; un suffixe peut la dire.
6. **Un registre unique des propriétés** : nom, famille, type, unité, bornes, styles qui la portent, description. `Props`, `_OWNERS`, `Look`, les messages d'erreur et les tableaux de ce document en sont tirés.
7. **Pas de bloc `styles:` obligatoire.** Au premier niveau d'un fichier, un réglage (§ 5) ou un style ; une charte de trois styles reste courte.

## 3. Un fichier de style

```yaml
# ── Réglages ─────────────────────────────────────────────────────────────────
colors: { brand: 1F3A5F, accent: E07A1F, pale: EAF0F6 }
language: fr
numbering: decimal
chart: { series: [ brand, accent, 5B8DB8, F2B880 ], text_color: 595959 }
page:
  paper: A4
  margins: 2cm
  watermark: BROUILLON
  header:
    first_page: false
    logo: { file: images/logo.png, width: 3cm }
    table:
      widths: [ 4cm, auto ]
      rows:
      - - { logo: true }
        - { text: "{title}", style: header_title }
  footer:
    lines:
    - { text: "Document confidentiel", style: footer_center }
    table:
      widths: [ auto, 1.5cm ]
      rows:
      - - [ "App-Novative · 2 allée du Semoir, 77185 Lognes", "SIRET 000 000 000 00000" ]
        - { text: "{page}/{pages}", style: footer_page }

# ── Styles : les propriétés, et seulement ici ────────────────────────────────
body:           { font: Aptos, size: 11pt, line_spacing: 1.15, space_after: 6pt }
document_title: { size: 24pt, color: brand, bold: true, border_bottom: 1.5pt accent }
heading1:       { size: 16pt, color: brand, bold: true, space_before: 16pt }
note:           { fill: pale, border_left: 3pt brand, indent: 0.3cm }
table:          { size: 10pt, borders: horizontal, line: 0.5pt brand }
table_header:   { fill: brand, color: FFFFFF, bold: true }
watermark:      { color: D9D9D9 }
header:         { size: 9pt, border_bottom: 1pt accent }
header_title:   { base: header, align: right, valign: bottom }
footer:         { size: 8pt, color: 595959, border_top: 0.5pt brand, borders: vertical, line: 0.5pt brand, column_gap: 0.4cm }
footer_center:  { base: footer, align: center }
footer_page:    { base: footer, align: right, valign: center, bold: true, color: brand }
```

## 4. Valeurs et unités

| Forme | S'écrit | Exemples |
| --- | --- | --- |
| Longueur | un nombre, et au besoin son unité collée : `pt`, `mm`, `cm`, `in` ; virgule ou point | `11pt`, `0,5pt`, `2cm`, `12mm` |
| Proportion | un nombre suivi de `%` : de la largeur du texte (ou de la table) ; largeurs seulement | `55%` |
| Multiplicateur | un nombre sans unité | `line_spacing: 1.15` |
| Couleur | six chiffres hexadécimaux sans `#`, ou un nom de `colors` | `1F3A5F`, `brand` |
| Fond | une couleur, ou `none` pour retirer celui d'un fichier précédent | `fill: none` |
| Filet | « épaisseur couleur », l'épaisseur en `pt` (0,25 à 6), ou `none` | `0.5pt brand` |
| Booléen | `true`, `false` | |
| Énumération | une des valeurs de la propriété | `align: right` |

**L'unité d'un nombre seul** : `pt` pour la typographie, les espacements et les filets (`size`, `space_before`, `space_after`) ; `cm` pour la géométrie (marges, largeurs, retraits, écarts, hauteurs de ligne). Toutes les longueurs se convertissent : une unité de longueur vaut partout. Une charte d'avant, en nombres seuls, se lit pareil. Ni `px` (sans sens à l'impression), ni `em` (relatif à une police, rendu différemment par Word et le PDF). Un interligne exact (`line_spacing: 14pt`, `lineRule="exact"` dans Word) pourra venir plus tard.

## 5. Les réglages

| Réglage | Valeur | Défaut (`default.yaml`) | Rôle |
| --- | --- | --- | --- |
| `colors` | `{ nom: couleur }` | `{}` | Les couleurs nommées, reprises partout par leur nom |
| `language` | `fr`, `en` | `fr` | La langue du document (correcteur, césure), l'écriture des nombres et des dates, les libellés |
| `numbering` | `none`, `decimal`, `article` | `none` | La numérotation des titres |
| `labels` | `{ libellé: texte }` | selon la langue | Les libellés que l'outil écrit (Tableau, Figure, Sommaire, Total HT…) |
| `image.side_by_side` | booléen | `false` | Deux images `half` qui se suivent, côte à côte (avant : `image.grid`) |
| `image.space_after`, `chart.space_after` | longueur (pt) | `6pt` | Nouveau : l'espace après une image, un graphique sans légende (en dur avant le lot 5) |
| `chart.series` | liste de couleurs, 12 au plus | palette Office | La couleur de chaque série, dans l'ordre (avant : `chart.colors`) |
| `chart.text_color`, `chart.grid_color`, `chart.title_color` | couleur | `595959`, `D9D9D9`, `595959` | Le texte, la grille, le titre d'un graphique (avant : `text`, `grid`, `title`) |
| `fonts` | `{ famille: { regular, bold, italic, bold_italic } }` | — | Les fichiers TrueType qu'embarque le PDF |
| `page.paper` | `A4`, `A5`, `Letter` | `A4` | Le format de papier (avant : `page.size`) |
| `page.orientation` | `portrait`, `landscape` | `portrait` | |
| `page.margins`, `page.margin_top`, `page.margin_bottom`, `page.margin_left`, `page.margin_right` | longueur (cm), 0,5 à 5 cm | `2cm` | Les marges |
| `page.header_distance`, `page.footer_distance` | longueur (cm) | `1cm` | Nouveau : la distance de l'en-tête et du pied au bord (en dur avant) |
| `page.column_gap` | longueur (cm) | `1cm` | Nouveau : l'écart entre les colonnes d'une section (`columns`) (en dur avant) |
| `page.beside_gap` | longueur (cm) | `0.5cm` | Nouveau : l'écart entre deux sections côte à côte (`beside`), deux images côte à côte, deux signataires ; la moitié entre un libellé et sa valeur (`fields`) (en dur avant) |
| `page.beside_space` | longueur (pt) | `6pt` | Nouveau : l'espace après des sections côte à côte ou encadrées (en dur avant) |
| `page.cover_margin_top` | longueur (cm) | `6cm` | Nouveau : le haut d'une page de garde sans `margin_top` (en dur avant) |
| `page.header`, `page.footer` | § 6 | rien | Ce qu'écrivent l'en-tête et le pied |
| `page.watermark` | texte, 40 caractères au plus | rien | Le texte du filigrane (avant : `watermark.text`) |

## 6. Le contenu de l'en-tête et du pied

`page.header` et `page.footer` ont les mêmes clés ; leur texte prend le style `header` ou `footer`, ou un style qui en part.

| Clé | Valeur | Rôle |
| --- | --- | --- |
| `first_page` | booléen, `true` par défaut | `false` : pas d'en-tête (de pied) sur la première page |
| `logo` | `{ file, width, gap }` : un PNG ou JPEG relatif à `resources_dir`, sa largeur (cm, 0,5 à 17), l'écart à son texte (pt, 4 pt par défaut) | Le logo, posé dans la zone ou la cellule qui le demande (`logo: true`) |
| `left`, `center`, `right` | un élément | Trois zones sur une ligne ; `center` toujours centré |
| `lines` | une liste d'éléments, 4 au plus | Des lignes de toute la largeur : sous les zones (ou la table) dans un en-tête, au-dessus dans un pied |
| `table` | `{ widths, rows, style }` | Une table à la place des trois zones |

**Un élément** (zone, ligne, cellule) : un texte (`"…"`), une liste de lignes (`["…", "…"]`, cellule seulement), ou `{ text | lines, style, logo }`. Ses textes ont `{page}`, `{pages}` et `{title}`, 200 caractères au plus par texte. `style` : le nom d'un style qui part de `header` (en-tête) ou de `footer` (pied) ; sans lui, `header` ou `footer`. Aucune propriété en ligne, ni style écrit en ligne (`style: { … }` est refusé) : un style se nomme, et se réutilise.

**La table** : `widths`, une largeur par colonne (4 au plus), en longueur, en `%` ou `auto` (la place qui reste, partagée, 1 cm au moins) ; `rows`, 3 lignes au plus, une cellule par colonne ; `style` (facultatif), le style de la table, qui part de `header` ou de `footer`. Les filets intérieurs et l'écart entre colonnes sont des propriétés de ce style (`borders`, `line`, `column_gap`), comme pour le style `table` du corps. Une table ne va pas avec `left`, `center` ni `right`.

## 7. Les styles

Les propriétés communes (§ 8) valent pour tous ; la dernière colonne dit celles qu'un style a en plus. Un style à toi part d'un de ceux-ci (`base:`) et en a les propriétés propres.

| Style | Base | Ce qu'il met en forme | Style Word | Propriétés propres |
| --- | --- | --- | --- | --- |
| `body` | — | Le corps du texte ; la base de tous (avant : `text`) | Normal | |
| `document_title` | `body` | Le titre du document | Title | |
| `heading1`, `heading2`, `heading3` | `body` | Les titres de niveaux 1 à 3 | Heading 1 à 3 | |
| `paragraph` | `body` | Un paragraphe | Normal, ou Body Text | |
| `list` | `body` | Une liste | List Paragraph | `level_indent`, `marker_space` (nouvelles) |
| `note` | `body` | Une note | Note | |
| `caption` | `body` | La légende d'un tableau, d'une image, d'un graphique | caption | `numbered` |
| `quote` | `body` | Une citation | Quote | |
| `quote_source` | `quote` | Sa source | Quote Source | |
| `table` | `body` | Un tableau, ses cellules | Table Text | `borders`, `line`, `bands`, `width`, `position`, `valign`, `padding` (nouvelle) |
| `table_header`, `table_total` | `table` | La ligne d'en-tête, la ligne de total | (dans Table Text) | celles de `table` |
| `table_group`, `table_subtotal` | `table` | La ligne d'un groupe, son sous-total | loom table group, loom table subtotal | celles de `table` |
| `invoice_totals` | `table` | Le récapitulatif de TVA | loom invoice totals | celles de `table` |
| `attendance` | `table` | La feuille d'émargement | loom attendance | celles de `table`, `row_height` |
| `box` | `body` | Le cadre d'une section encadrée | (cellule) | `padding` (nouvelle) |
| `toc` | `body` | Le titre du sommaire | TOC Heading | `levels`, `page_numbers`, `level_indent` (nouvelle) |
| `toc1`, `toc2`, `toc3` | `body` | Nouveaux : les entrées du sommaire, niveau par niveau | toc 1 à toc 3 | |
| `signature` | `body` | Le libellé d'un signataire | loom signer | `sign_height` (nouvelle) |
| `signature_mention` | `body` | La mention | loom mention | |
| `fields` | `body` | La valeur d'un champ | loom field value | `label_width` |
| `fields_label` | `fields` | Son libellé | loom field label | celles de `fields` |
| `code` | `body` | Un bloc de code | loom code | |
| `link` | `body` | Un lien | Hyperlink | |
| `footnote` | `body` | Une note de bas de page | footnote text | |
| `header` | `body` | L'en-tête, sa table | header | `borders`, `line`, `column_gap`, `valign` |
| `footer` | `body` | Le pied de page, sa table | footer | `borders`, `line`, `column_gap`, `valign` |
| `watermark` | `body` | Le filigrane | (forme VML) | |

Quittent les styles : `left`, `center`, `right`, `lines`, `table`, `logo`, `logo_width`, `logo_position`, `first_page` (vers `page.header`, `page.footer`), `text` de `watermark` (vers `page.watermark`).

## 8. Les propriétés

| Propriété | Famille | Valeur | Unité d'un nombre seul | Bornes | Styles | Rôle |
| --- | --- | --- | --- | --- | --- | --- |
| `base` | commune | nom de style | | | tout style à toi | Le style dont il part |
| `description` | commune | texte | | 200 caractères | style à toi qui part d'un style de bloc | Le proposer au modèle de langage, sur son type de bloc |
| `font` | commune | nom de police | | 60 caractères | tous | La police |
| `size` | commune | longueur | pt | 6 à 72 pt | tous | La taille du texte |
| `color` | commune | couleur | | | tous | La couleur du texte |
| `fill` | commune | fond | | | tous | Le fond du paragraphe (de la cellule, dans une table) |
| `bold`, `italic`, `underline`, `uppercase` | commune | booléen | | | tous | Gras, italique, souligné, capitales |
| `align` | commune | `left`, `center`, `right`, `justify` | | | tous | L'alignement du texte dans sa boîte |
| `space_before`, `space_after` | commune | longueur | pt | 0 à 72 pt | tous | L'espace avant, après |
| `line_spacing` | commune | multiplicateur | | 1 à 3 | tous | L'interligne |
| `indent` | commune | longueur | cm | 0 à 10 cm | tous | Le retrait à gauche et à droite |
| `border` | commune | filet | | | tous | Un filet sur les quatre côtés |
| `border_top`, `border_right`, `border_bottom`, `border_left` | commune | filet | | | tous | Un filet sur un côté (`border_right` : nouvelle) |
| `border_space` | commune | une à quatre longueurs, dans l'ordre de CSS | pt | 0 à 20 pt | tous | Nouvelle : l'écart entre un filet (ou un fond) et le texte ; `4pt 8pt` dans `default.yaml` (en dur avant le lot 5) ; Word l'arrondit au point |
| `valign` | propre | `top`, `center`, `bottom` | | | `table`, `header`, `footer` | Nouvelle : le texte dans la hauteur de sa cellule (avant : en ligne dans une cellule) |
| `borders` | propre | `none`, `horizontal`, `vertical`, `grid` | | | `table`, `header`, `footer` | Les filets intérieurs d'une table (`vertical` : nouvelle pour `table`) |
| `line` | propre | filet | | | `table`, `header`, `footer` | Le trait des filets intérieurs |
| `column_gap` | propre | longueur | cm | 0 à 3 cm | `header`, `footer` | L'écart entre deux colonnes de leur table (avant : `gap` dans la table) |
| `bands` | propre | couleur | | | `table` | Le fond d'une ligne sur deux |
| `width` | propre | `full`, `content` | | | `table` | Toute la largeur, ou celle du contenu |
| `position` | propre | `left`, `center`, `right` | | | `table` | La place d'un tableau étroit |
| `row_height` | propre | longueur | cm | 0,5 à 5 cm | `attendance` | La hauteur d'une ligne d'émargement |
| `numbered` | propre | booléen | | | `caption` | « Tableau 1 — », « Figure 1 — » |
| `levels` | propre | 1 à 3 | | | `toc` | Les niveaux de titre repris |
| `page_numbers` | propre | booléen | | | `toc` | Les numéros de page du sommaire |
| `label_width` | propre | longueur | cm | 1 à 10 cm | `fields` | La largeur des libellés |
| `padding` | propre | une à quatre longueurs, dans l'ordre de CSS | cm | 0 à 2 cm | `box`, `table` | Nouvelle : l'écart entre le cadre d'une section, le bord d'une cellule, et le texte ; `0.3cm 0.3cm 0.15cm` pour `box`, `2pt 0.15cm` pour `table` dans `default.yaml` (en dur avant le lot 5) |
| `level_indent` | propre | longueur | cm | 0 à 5 cm | `list`, `toc` | Nouvelle : le retrait de chaque niveau d'une liste (1,27 cm), des entrées du sommaire (0,5 cm) |
| `marker_space` | propre | longueur | cm | 0 à 3 cm | `list` | Nouvelle : la place de la puce ou du numéro devant le texte (0,635 cm) |
| `sign_height` | propre | longueur | cm | 0 à 10 cm | `signature` | Nouvelle : la place pour signer, sous « Signature : » (2 cm) |

**L'espacement d'un tableau.** `space_before` et `space_after` du style d'un tableau (`table`, `invoice_totals`, `attendance`, un style à toi qui en part, et le style de la table d'un en-tête ou d'un pied) valent autour du tableau entier, et non plus pour chaque paragraphe de ses cellules : `table: { space_after: 6pt }` remplace l'espace en dur. Les paragraphes d'une cellule n'ont d'espacement que ce que le style de la cellule écrit en plus de celui de la table (son style de ligne, `table_group`, `table_subtotal` ; `style:`, dans une table d'en-tête ou de pied) ; sans lui, aucun. ⚠ Avant le lot 5, le `space_before` et le `space_after` du style `footer` valaient pour chaque paragraphe des cellules de sa table. `signature` et `fields` gardent leur espacement de ligne (lot 5) : leur `space_after` est celui de chaque ligne, et le `space_after` de `table` vaut après eux.

## 9. Les sections d'un modèle

Inchangées, sauf `space_before`, renommée `margin_top` : la marge au-dessus de la section, de la mise en page en cm (l'adresse d'un courrier dans la fenêtre de l'enveloppe), comme `box` ou `columns`, et non l'espace d'un style en points, qui aurait le même nom pour un autre sens. Une section désigne des styles (`styles:`), elle n'en écrit pas les propriétés. `width` prend aussi `55%` (un nombre seul reste un pourcentage).

| Clé | Valeur | Rôle |
| --- | --- | --- |
| `instructions`, `optional`, `show_title`, `text` | | Le contenu de la section, comme avant |
| `styles` | `{ élément: style }` | Le style de chaque élément de la section |
| `box`, `beside`, `width`, `page_break`, `columns`, `landscape`, `cover` | | La disposition, comme avant |
| `margin_top` | longueur (cm), 0 à 20 cm | La marge au-dessus de la section (avant : `space_before`) ; une page de garde sans elle : `page.cover_margin_top` (6 cm) |

## 10. Correspondance

Chaque ancienne clé est refusée au chargement avec celle qui la remplace (`RENAMED_STYLES`, `refuse_renamed`, `_MOVED`, comme `OLD_KEYS` au passage à 2.1.0), toutes celles d'un fichier d'un coup. ⚠ Toute charte et tout modèle d'avant est à reprendre.

| Avant | Depuis 2.0.21 |
| --- | --- |
| style `text` | style `body` (et `base: body`) |
| `page.size` | `page.paper` |
| `chart.colors`, `chart.text`, `chart.grid`, `chart.title` | `chart.series`, `chart.text_color`, `chart.grid_color`, `chart.title_color` |
| `image.grid` | `image.side_by_side` |
| `header` / `footer` : `left`, `center`, `right`, `lines`, `table`, `first_page` | `page.header` / `page.footer` : les mêmes clés |
| `header.logo`, `logo_width`, `logo_position` | `page.header.logo: { file, width, gap }`, et `logo: true` dans la zone ou la cellule |
| `watermark: BROUILLON`, `watermark.text` | `page.watermark: BROUILLON` |
| `table.gap` (table d'en-tête, de pied) | `column_gap` du style de la table |
| `table.borders`, `table.line` (table d'en-tête, de pied) | `borders`, `line` du style de la table |
| `align`, `valign` d'une cellule, d'une ligne | `style:`, un style qui les porte |
| `space_before` d'une section | `margin_top` d'une section |

## 11. Les valeurs qui étaient dans le code

| Valeur | Où | Avant | Clé |
| --- | --- | --- | --- |
| Distance de l'en-tête et du pied au bord | `word/writer.py` (`w:header`, `w:footer`), `pdf/writer.py` (`BAND_DISTANCE`) | 1 cm | `page.header_distance`, `page.footer_distance` |
| Écart entre les colonnes d'une section | `COLUMN_GAP` (Word, PDF) | 1 cm | `page.column_gap` |
| Écart entre deux sections côte à côte | `CELL_GAP` (Word, PDF) | 284 twips (0,5 cm) | `page.beside_gap` |
| Écart entre le cadre d'une section et son texte | `BOX_PAD` (Word, PDF) | 170 twips (0,3 cm) | `box.padding` |
| Écart entre un logo et son texte | `pdf/writer.py` (4.0) | 4 pt | `page.header.logo.gap`, `page.footer.logo.gap` |
| Écart entre un filet et le texte | `_RULE_SPACE` (Word), `RULE_SPACE` (PDF) | 4 pt en haut et en bas, 8 pt à gauche et à droite | `border_space`, propriété commune (`4pt 8pt`) |
| Espace après un tableau | `_AFTER_TABLE` (Word) | 6 pt | `space_after` du style `table`, autour du tableau (§ 8) ; après une signature et des champs aussi |
| Espace après une image, un graphique sans légende | `_AFTER_TABLE` (Word), `AFTER_TABLE` (PDF) | 6 pt | `image.space_after`, `chart.space_after` |
| Espace après des sections côte à côte ou encadrées | `_AFTER_TABLE` (Word), `AFTER_TABLE` (PDF) | 6 pt | `page.beside_space` |
| Espace en haut d'une page de garde | `COVER_SPACE` (Word, PDF) | 6 cm | `page.cover_margin_top` |
| Marges d'une cellule de tableau | `tblCellMar` (Word), `CELL_PADDING` (PDF) | 2 pt en haut et en bas, 0,15 cm sur les côtés | `table.padding` |
| Entrées du sommaire | `_extra_styles` (Word), `contents` (PDF) | niveau 1 en gras, 3 pt avant ; 2 pt après chaque entrée ; 0,5 cm de retrait par niveau | les styles `toc1` à `toc3`, `toc.level_indent` |
| Place pour signer | `SIGN_SPACE` (Word, PDF) | 2 cm | `signature.sign_height` |
| Retraits des listes | `_abstract` (Word), `LIST_INDENT`, `LIST_HANG` (PDF) | 1,27 cm par niveau, la puce 0,635 cm avant | `list.level_indent`, `list.marker_space` |
| Espace entre l'en-tête (le pied) et le corps, PDF | `BAND_GAP` | 6 pt | aucune clé : Word n'en a pas l'équivalent (le corps commence à la marge) ; une clé du seul PDF romprait l'identité des deux rendus. Reste une mécanique, nommée comme telle dans le test des valeurs visuelles |

Leur valeur d'avant passe dans `default.yaml` : un document sans charte reste le même. Le test des valeurs visuelles s'étend aux longueurs (`test_no_visual_length_in_the_code`, `test_no_visual_length_in_the_pdf_code`) : chaque nombre resté dans le code y est nommé, avec sa raison.

Restent dans le code, des mécaniques : la taille d'un graphique (17 × 9 cm), la place et l'opacité du filigrane, la géométrie d'une case à cocher, le titre d'un graphique dans le PDF (comme Word le dessine), `BAND_GAP`. Et, non choisis au lot 5 : l'espace sous « Date : » d'une signature (3 pt), le paragraphe d'une image et d'un graphique dans Word (6 pt avant, 3 pt après).

## 12. Mise en œuvre, par lots

1. **Le registre** des propriétés (`PROPERTIES`) : `_OWNERS` et le message d'une propriété inconnue en sont tirés ; `Props` et `Look` restent écrits (le typage strict de pyright y tient), et un essai exige qu'ils aient exactement les noms du registre. Aucun changement visible. *Fait le 16/11/2026.* La vérification des tableaux de ce document contre le registre viendra au lot 6, quand le registre décrira le nouveau format.
2. **Les unités** : la lecture des longueurs à suffixe et des proportions ; un nombre seul inchangé. *Fait le 16/11/2026* : styles, page, table d'en-tête et de pied (largeurs en longueur, en `%` ou `auto`), largeur et `space_before` des sections.
3. **Les renommages** (§ 10, sauf l'en-tête et le pied) et leurs refus ; `border_right`, `vertical` pour `table`, `valign` propriété de style. *Fait le 16/11/2026* ; le `space_before` des sections est renommé `margin_top` plutôt que retiré : il place l'adresse d'un courrier dans la fenêtre d'une enveloppe, à plusieurs centimètres, ce que le `space_before` d'un style (en points, 72 au plus, pour chaque paragraphe de ce style) ne fait pas proprement.
4. **Le contenu de l'en-tête, du pied et du filigrane** dans `page` ; les éléments avec `style:` seulement ; `center` toujours centré. *Fait le 16/11/2026* : l'ancien contenu d'un style est refusé avec la nouvelle place de chaque clé, toutes d'un coup ; le logo dans Word est écarté de son texte de `logo.gap`, comme dans le PDF (une espace insécable d'un point, à l'espacement de `gap` : LibreOffice ne lit pas l'écart d'une image ancrée en ligne) ; la charte des essais, réécrite, donne les mêmes en-tête et pied.
5. **Les valeurs du code** dans `default.yaml` (§ 11). *Fait le 16/11/2026* : en plus du § 11 d'origine, les marges des cellules d'un tableau, le sommaire (les styles `toc1` à `toc3`), la place pour signer, les retraits des listes. Word ne change pas d'un octet ; le PDF prend les distances au twip, comme Word (moins de 0,03 pt de différence), et gagne trois espaces qu'il oubliait : après une section encadrée seule, après deux images côte à côte, après une signature.
6. **La doc** (`docs/howto/06-outils-fournis.md`), le CHANGELOG (⚠), les chartes et les modèles des exemples et des essais. *Fait le 16/11/2026* : les tableaux de ce document et de la doc vérifiés contre le registre par un essai ; le CHANGELOG de la 2.0.21 regroupé à l'état final ; `examples/` n'a ni charte ni modèle de document, et les essais sont au nouveau format (les anciennes clés n'y servent qu'aux refus).

## 13. Décisions

Prises :
- le style de base s'appelle `body` ;
- un style et une propriété se distinguent : les propriétés ne s'écrivent que dans un style, sans exception (ni `align` ni `valign` en ligne) ;
- le `space_before` des sections devient `margin_top`, une clé de mise en page en cm (et non un style) ;
- le contenu de l'en-tête, du pied et du filigrane va dans `page.header`, `page.footer`, `page.watermark` ;
- pas de bloc `styles:` obligatoire ;
- les suffixes `pt`, `mm`, `cm`, `in` et `%` ;
- les filets intérieurs et l'écart entre colonnes d'une table d'en-tête ou de pied sont des propriétés de son style (`borders`, `line`, `column_gap`) : `footer` (`header`), ou le style que nomme la table (`style: footer_grid`) ;
- un style se désigne toujours par son nom : pas de style écrit en ligne (`style: { … }`) ;
- `image.side_by_side` (avant : `image.grid`), `chart.series` (avant : `chart.colors`), et `chart.text_color`, `chart.grid_color`, `chart.title_color` ;
- les valeurs du code (§ 11) : `logo.gap`, `border_space`, le `space_after` du style `table` autour du tableau, et `BAND_GAP` sans clé ;
- lot 5 : `page.cover_margin_top` ; l'espace après une image, un graphique, des sections, dans `image.space_after`, `chart.space_after`, `page.beside_space` ; `signature` et `fields` gardent leur espacement de ligne, le `space_after` de `table` après eux ; `padding` et `border_space` en une à quatre longueurs, dans l'ordre de CSS ; `table.padding`, `toc1` à `toc3` et `toc.level_indent`, `signature.sign_height`, `list.level_indent` et `list.marker_space`.

À prendre : rien.
