# Le souligné dans le texte des documents : analyse

10 novembre 2026, sur la 2.0.21 non publiée (renommage `resources_dir` et `title.underline` compris). Rien n'est écrit dans `src/` tant que les décisions ne sont pas prises : le prototype tourne sur une copie du dépôt.

## La demande

Le modèle de langage sait mettre un mot en gras (`**gras**`) ou en italique (`*italique*`) dans le texte d'un document `loom.word`. Il ne sait pas le souligner. `title.underline` (styles) souligne le titre du document entier, c'est du style ; ici, c'est le modèle de langage qui choisit les mots : c'est du **contenu**, comme le gras.

## Ce qui existe

Une seule fonction découpe le texte en segments, pour Word et PDF : `inline()` (`_document/content.py`), qui rend des `Run(text, bold, italic)`.

| Où | Ce qu'il fait de l'emphase |
| --- | --- |
| `_document/content.py`, `inline()` | `**` et `*` ouvrent s'ils sont suivis d'un caractère visible et refermés plus loin, ferment s'ils suivent un caractère visible ; sinon ils restent du texte (« 2 * 3 ») |
| `word/writer.py`, `_runs()` | `<w:b/>`, `<w:i/>` dans les propriétés du segment |
| `pdf/writer.py`, `_markup()` | `<b>`, `<i>` du balisage de `Paragraph` (reportlab) |
| `word/reader.py`, `_segments()` et `_markdown()` | la lecture rend `**` et `*` à partir des `<w:b/>` et `<w:i/>` posés sur le segment : ce qui est lu se réécrit à l'identique |
| `_document/tool.py`, `WRITING_HELP` | la description, commune à `loom.word` et `loom.pdf` : « Dans un texte : **gras**, *italique*, et \n … ; pas d'autre balisage (ni #, ni -, ni \|) » |

L'emphase vaut pour le titre du document, les titres, les paragraphes, les éléments de liste (cases à cocher comprises), les notes, les citations et les légendes. **Pas pour les cellules de tableau**, ni gras ni italique aujourd'hui : elles restent du texte simple.

## La proposition

`<u>texte</u>` : la balise HTML, la convention de fait du souligné en markdown (GitHub, Obsidian, Typora), que les modèles de langage connaissent et écrivent d'eux-mêmes quand on leur demande un souligné. Le reste suit les règles du gras :

- `<u>` ouvre s'il est refermé plus loin ; `</u>` ferme ce qui est ouvert ; sinon, la balise reste du texte. Ni majuscules (`<U>`), ni attributs.
- Il se combine avec le gras et l'italique, dans n'importe quel ordre : `**<u>mot</u>**`, `<u>**mot**</u>`, `*<u>mot</u>*`.
- Word : `<w:u w:val="single"/>` sur le segment ; PDF : `<u>` de reportlab. La couleur du soulignement est celle du texte.
- La lecture rend `<u>…</u>` pour un segment souligné, quel que soit le trait (`single`, `double`, pointillé…) ; `w:val="none"` n'est pas souligné. Comme pour le gras, seules les propriétés posées sur le segment comptent, pas celles d'un style de caractère.

## Les syntaxes possibles (D-1)

| | Syntaxe | Pour | Contre |
| --- | --- | --- | --- |
| **A** | `<u>mot</u>` | Explicite, connue des modèles, aucune ambiguïté avec le markdown ; rendue soulignée par les lecteurs markdown courants | Une balise HTML au milieu d'un balisage markdown ; un modèle pourrait tenter `<b>` ou `<br>` (qui resteraient du texte) |
| B | `__mot__` | Court | **En markdown, `__mot__` est du gras** : un modèle qui l'écrit pour du gras obtiendrait un souligné, et la lecture rendrait un « gras » pour tout lecteur markdown. Les noms comme `__init__` deviendraient « init » souligné |
| C | `++mot++` | Court, sans collision fréquente (extension markdown-it-ins) | Peu connue : le modèle ne l'apprend que de la description ; « C++, C++ » est ambigu sans règle de mot |

Recommandation : **A**.

## Le prototype

Sur une copie du dépôt (`scratchpad/proto-u`), 5 fichiers de `src/` : `content.py` (+21 / −5), `word/reader.py` (+29 / −21), `pdf/writer.py` (+2), `word/writer.py` (+1), `_document/tool.py` (la description).

**Découpage**, tel que le prototype le fait :

| Texte | Segments |
| --- | --- |
| `un <u>mot</u> souligné` | « un » · « mot » souligné · « souligné » |
| `**<u>gras souligné</u>**` et `<u>**gras souligné**</u>` | « gras souligné », gras et souligné |
| `mi<u>lieu</u>du mot` | « lieu » souligné, au milieu du mot |
| `a <u> b`, `fin </u> seule`, `<u>ouvert`, `<U>maj</U>` | inchangés, du texte |
| `<u><u>x</u></u>` | « <u>x » souligné, puis « </u> » en texte : pas d'imbrication |

**Mesures :**

- **Relecture puis réécriture** : un titre, un paragraphe (souligné, gras souligné, italique) et une liste écrits, relus (les blocs relus sont exactement ceux écrits), réécrits : le même fichier, au sha256 près.
- **Un document d'ailleurs** : `<w:u w:val="double"/>` se relit `<u>double</u>` ; `w:val="none"` reste du texte ; un lien souligné en propriété directe se relit `<u>https://exemple.fr</u>`. À la réécriture, un double soulignement devient simple.
- **Sans `<u>`, rien ne change** : les empreintes des 8 combinaisons graphiques × images × modèles (documents et schémas) sont identiques ; seules les 8 descriptions changent.
- **Rendu** : Word (LibreOffice) et PDF soulignent le titre, le paragraphe, l'élément de liste et la note, gras et italique conservés.
- Les 4 281 essais unitaires passent sans changement (131 sautés).

## Effets de bord

1. ⚠ **La description de `loom.word` et `loom.pdf` grandit** de 17 caractères (`<u>souligné</u>, `), environ 7 tokens par appel de modèle d'un agent qui a l'un de ces outils (estimation : le compteur de tokens n'a pas été appelé). Un run d'avant ne se rejoue plus à l'identique pour ces agents (« ce qui diffère : les outils proposés »), comme à chaque changement de description.
2. **Un texte qui contient déjà `<u>…</u>`** est désormais souligné, au lieu d'être écrit tel quel. Rare, et seulement s'il est refermé.
3. **La lecture d'un document souligné ailleurs** rendait le texte sans le souligné, perdu en silence ; elle rend désormais les balises, que le modèle de langage voit et réécrit. Un lien hypertexte souligné par le style « Hyperlink » (le cas de Word) ne l'est pas : le style n'est pas lu, comme pour le gras.
4. **D'autres balises** (`<b>`, `<i>`, `<br>`) restent du texte : un modèle qui les tenterait les verrait écrites. D-4 propose de le dire dans la description.
5. **Le gras dans les cellules de tableau** n'existe pas, le souligné non plus : inchangé, hors de cette analyse.
6. `title.underline` (style) et `<u>` dans le texte du titre (contenu) se combinent sans conflit.

## Les décisions

| | Question | Recommandation |
| --- | --- | --- |
| **D-1** | La syntaxe : A `<u>mot</u>`, B `__mot__`, C `++mot++` | A |
| **D-2** | `loom.pdf` aussi ? Les deux outils partagent la description et le découpage ; ne pas le faire en PDF y laisserait le souligné disparaître en silence | Oui |
| **D-3** | La lecture : tout `w:u` autre que `none` se relit `<u>…</u>` | Oui |
| **D-4** | La description : « **gras**, *italique*, <u>souligné</u>, et \n … ; pas d'autre balisage (ni #, ni -, ni \|, ni autre balise) » | Oui, avec « ni autre balise » |
| **D-5** | La version : 2.0.21, ligne ⚠ (description et rejeu) | 2.0.21 |

**Décidé le 10 novembre 2026 : les cinq recommandations.** Codé sur la 2.0.21.

## Ce qui est écrit

- En plus du prototype : `_templates.py`, `key()` ignore aussi `<u>` et `</u>`, comme `**` et `*`, pour un titre de section écrit souligné dans un fichier de modèle ou nommé ainsi dans une disposition. Le titre d'un document passe déjà par le découpage, qui retire les balises.
- Essais : 3 dans `test_document.py` (découpage, deux cas de titre comparé), 3 dans `test_word.py` (description, relecture et réécriture, document d'ailleurs), 1 dans `test_word_styles.py` (une section soulignée garde sa disposition), 1 dans `test_pdf.py`. Avec tous les extras : 4 515 réussis, 203 sautés, 1 échec (la forge Firecracker, le même) ; noyau seul : 3 500 réussis, 416 sautés.

## Ce qui était prévu

- `src/` : les 5 fichiers du prototype, la ligne trop longue de la description reprise.
- Essais : `test_document.py` (le découpage, cas du tableau ci-dessus), `test_word.py` (Word, relecture et réécriture à l'identique, document d'ailleurs à double soulignement et `none`), `test_pdf.py` (le balisage `<u>`).
- Docs : `howto/06-outils-fournis.md` (le texte des blocs, la légende d'une image), `CHANGELOG.md` (2.0.21, ⚠).
