# Analyse d'impact — des graphiques dans les documents de `loom.word` et `loom.pdf` loom_v2

29 octobre 2026 · Denis Lamard

## En bref

Le besoin vient de l'essai réel du 29/10 (session `word-01`). À la demande « ajoute un graphique », Marie a répondu que l'outil ne savait pas en insérer. Elle a alors dessiné des barres en caractères (`█`, puis `#` dans le PDF, faute de glyphe), dont elle a dit avoir saisi les longueurs à la main. Cette analyse mesure ce que coûte un vrai bloc `graphique`, sur un prototype complet.

**Verdict : rien ne bloque.** Le graphique est un nouveau bloc du vocabulaire commun, rendu en **graphique Word natif** dans le `.docx` (DrawingML, sans dépendance) et en **dessin vectoriel reportlab** dans le PDF (aucune dépendance de plus : reportlab est déjà dans l'extra `pdf`). La relecture d'un `.docx` rend ses graphiques dans le même vocabulaire. Un document sans graphique reste identique à l'octet près.

- **Tests :** 3 769 verts avec tous les extras, soit 3 747 plus 22 nouveaux. En noyau seul, 2 842 verts (+20) : les graphiques Word marchent sans extra.
- **Qualité :** pyright strict à 0, ruff propre, les 5 contrats d'import tenus.
- **Coût :** environ 500 tokens de plus par outil et par appel au modèle (description et schéma), payés seulement par les agents qui posent `charts: true` dans la config de l'outil (D-8, décidé). Sans ce paramètre, rien ne change pour le modèle.

| Point | Constat | Preuve |
| --- | --- | --- |
| Graphique Word natif | Barres, barres horizontales, courbe, secteurs ; une partie `word/charts/chartN.xml` par graphique, ses données dans son cache | Démontré (XML relu ; LibreOffice reconnaît `bar` et `circle`) |
| Le PDF a le même graphique | Même palette, mêmes étiquettes, nombres à la française, en vectoriel | Démontré (rendus comparés, texte du PDF relu) |
| Les nombres ne sont pas recopiés | `from_table` tire libellés et séries du tableau qui précède le graphique, valeurs converties, sans la ligne de total | Démontré |
| Un document sans graphique ne change pas | Mêmes octets avant et après, Word et PDF | Démontré (empreintes comparées) |
| Relecture | Un graphique du `.docx` revient en bloc `graphique` (type, titre, libellés, séries) | Démontré (aller-retour) |
| Polices du PDF | Un libellé de graphique passe par le même contrôle de glyphes que le texte | Démontré |
| Sans `charts`, rien ne change pour le modèle | Description et schéma de `word` et `pdf` identiques à ceux d'avant les graphiques | Démontré (comparés à l'octet au commit `1581a33`) |

**Deux choses sont à vérifier ou à trancher :**

1. **Word lui-même (§ À vérifier sur ton poste).** LibreOffice lit les graphiques du prototype, mais Word applique le schéma DrawingML plus strictement, et rien ici ne peut le vérifier. Un fichier d'essai, `essai-graphiques.docx`, contient les quatre types de graphiques.
2. **Les données du graphique dans Word (D-3).** Le prototype écrit les nombres dans le cache du graphique, sans classeur embarqué. Word affiche le graphique et permet d'en changer l'apparence, mais « Modifier les données » ne trouve pas de classeur.

## Périmètre et état de référence

L'analyse porte sur ton arbre de travail au moment de l'étude : le commit `1581a33` (« builtin tool word & pdf ») et le correctif des largeurs de colonnes, non commité. Rien n'a été écrit dans `/home/denis/dev/loom_v2`, à part ce document. Les essais ont tourné sur un clone de cet état, dans l'espace de travail cloud de Claude, en Python 3.14.6, LibreOffice 24.2.7, reportlab 5.1.0.

| Contrôle sur l'état de référence | Résultat |
| --- | --- |
| `pytest`, tous les extras, sans services | 3 747 tests (dont le test de télémétrie instable déjà connu) |
| `pytest`, noyau seul | 2 822 passés, 408 sautés |
| `pyright` strict / `ruff` / `lint-imports` | 0 / propre / 5 tenus |

## Analyse fonctionnelle

### Ce que le modèle envoie

Un bloc de plus dans `blocks`, le même pour `word` et `pdf`. Le cas de la session `word-01`, sans rien recopier :

```json
{"type": "tableau",
 "columns": [{"header": "Rang", "type": "entier"}, {"header": "Client"}, {"header": "Total TTC", "type": "montant_eur"}],
 "rows": [[1, "Julien Barre", 18034.17], [2, "Chauffage Lemaire Rénovation", 16247.36], …]},
{"type": "graphique", "kind": "barres_horizontales", "title": "Total TTC par client",
 "from_table": {"categories": "Client", "values": ["Total TTC"]}}
```

Les données peuvent aussi être données en ligne, quand elles ne sont pas dans un tableau du document :

```json
{"type": "graphique", "kind": "courbe", "title": "Chiffre d'affaires mensuel 2026", "value_type": "montant_eur",
 "categories": ["janv.", "févr.", "mars", "avr."],
 "series": [{"name": "Facturé", "values": [18200, 21500, 19800, 25400]},
            {"name": "Encaissé", "values": [15100, 19900, 20500, null]}],
 "caption": "Avril encaissé : pas encore connu."}
```

- **`kind`** : `barres` (par défaut), `barres_horizontales` (pour des libellés longs, comme des noms de clients), `courbe` (une évolution), `secteurs` (les parts d'un tout).
- **`from_table`** : le tableau qui précède le graphique dans le document. `categories` nomme la colonne des libellés, `values` les colonnes de nombres (une série chacune, d'un même type). Les valeurs sont celles du tableau, déjà converties, sans la ligne de total. C'est la réponse au problème de la session : le modèle ne recopie plus les nombres, il ne peut donc ni les déformer, ni en inventer.
- **En ligne** : `categories`, `series` (`{name, values}`) et `value_type`, un type de colonne numérique (`entier`, `decimal`, `montant_eur`, `montant_keur`, `pourcentage`) qui décide de l'écriture des nombres. La conversion est celle des tableaux (`"25%"` donne 0,25). Une valeur `null` est un trou dans la courbe.
- **`title`** au-dessus du graphique, **`caption`** en légende dessous, comme pour un tableau.

### Le rendu

Le style est figé, comme celui du reste du document : la palette commence par le bleu nuit du document, puis des couleurs qui s'en distinguent (orange, bleu pâle, vert…). Les graduations s'écrivent en nombres ronds (`5 000 €`). Il y a des étiquettes de valeur sur chaque barre quand il n'y en a pas plus de 12 et qu'il n'y a qu'une série. La légende n'apparaît qu'avec plusieurs séries, ou des secteurs. Les barres horizontales mettent la première catégorie en haut, comme dans le tableau. Le graphique prend la largeur du texte, sur 9 cm de haut ; des barres horizontales prennent la hauteur de leurs libellés.

- **Dans le `.docx`**, c'est un graphique Word, pas une image. Il reste net à toute taille, se retouche dans Word (type, couleurs, titre), et ses nombres suivent la langue du lecteur.
- **Dans le PDF**, c'est un dessin vectoriel de reportlab, en Helvetica. Un libellé de plus de 18 caractères est raccourci sous l'axe, et les libellés s'inclinent quand ils ne tiennent pas côte à côte.

Les rendus des deux formats ont été comparés côte à côte sur les quatre types de graphiques : même ordre, mêmes couleurs, mêmes valeurs.

### Ce que l'outil refuse

Comme pour le reste, les fautes sont dites ensemble :

| Cas | Réponse |
| --- | --- |
| Ni données en ligne ni `from_table`, ou les deux | « un graphique prend ses données soit de 'categories' et 'series', soit de 'from_table' » |
| `from_table` sans tableau avant | « aucun tableau ne précède le graphique » |
| Colonne inconnue | « colonne(s) inconnue(s) du tableau précédent : TVA (colonnes : …) » |
| Séries de types différents, ou non numériques | « les séries viennent de colonnes de nombres d'un même type » |
| Autant de valeurs que de libellés | « 1 valeurs pour 2 libellés » |
| Secteurs : plusieurs séries, plus de 12 parts, une valeur négative, tout à zéro | Refusé, avec quoi faire (« regrouper le reste en « Autres », ou des barres ») |
| Plus de 50 libellés, plus de 6 séries | Refusé |
| `value_type` non numérique | « un graphique porte des nombres » |

La description dit aussi : « Jamais de graphique dessiné en caractères ».

### Le paramètre `charts` (D-8, décidé)

Le bloc `graphique` coûte environ 500 tokens par outil et par appel au modèle, même à un agent qui n'en fait jamais. Il ne s'active donc que sur demande, outil par outil :

```yaml
- python: loom.word
  charts: true          # facultatif, false par défaut
  folders:
  - name: workspace
    path: workspace
    rights: [ read, write, overwrite ]
- python: loom.pdf
  charts: true
  folders: …
```

- **Le même paramètre sur les deux outils.** Le bloc fait partie du vocabulaire commun : il entre dans le schéma et la description de `word` comme de `pdf`, au même prix (+1 801 caractères pour `word`, +1 802 pour `pdf`). Un agent qui a les deux outils paierait environ 1 000 tokens par appel. Le champ est déclaré dans `WordParams` et `PdfParams` (`charts: bool = False`), comme `recalc` dans `ExcelParams` : deux lignes ne justifient pas un module commun.
- **`false` par défaut.** Comme pour le reste des outils fournis, rien d'actif sans le demander. Un agent qui ne le pose pas garde des requêtes identiques à celles d'aujourd'hui : pas de rejeu cassé, pas de cache de prompt réécrit.
- **Le nom `charts`**, en anglais comme les autres clés (`folders`, `timezone`), et au sens de graphiques de données.
- **Avec `false`**, le schéma et la description sont ceux d'aujourd'hui, à l'octet près (mesuré : 1 866 + 3 785 caractères pour `word`, 2 034 + 3 774 pour `pdf`) : ni le bloc `graphique` dans l'union des blocs, ni sa ligne dans la description.
- **Un bloc `graphique` envoyé quand même** est refusé par le moteur, qui valide les arguments contre le schéma avant d'appeler l'outil. Son message est générique (« blocks : ne correspond à aucune des variantes autorisées ») et ne dit pas pourquoi. Le cas réaliste est un modèle qui réécrit un `.docx` qu'il vient de lire. La relecture le prévient donc : quand un document lu contient des graphiques et que `charts` est faux, une note le dit (« 2 graphique(s) lu(s) : cet outil n'en écrit pas ; les retirer avant de réécrire le document (ou charts: true dans la config de l'outil). »).
- **La lecture ne dépend pas du paramètre.** Lire un graphique ne coûte rien dans le schéma : `word` rend toujours les graphiques d'un `.docx`.
- **`loom validate`** ajoute `graphiques : oui` ou `graphiques : non` aux réglages de chaque outil (`fuseau : Europe/Paris · graphiques : non · lecture des documents : oui`). Dans ton dépôt actuel, `charts: true` y est refusé, puisque les paramètres n'acceptent pas de clé inconnue.

Côté code, le schéma est calculé au montage de l'outil (`DocumentTool.__init__`). Deux unions de blocs : `Block`, sans le graphique, et `AnyBlock`, avec. `Block` garde son nom parce que ce nom apparaît dans le schéma (`$defs`) : le changer aurait changé le schéma des agents sans `charts`. Chaque format déclare deux modèles d'appel, `WordCall` et `WordChartCall`, `PdfCall` et `PdfChartCall` ; le second hérite de `WithCharts`, qui élargit `blocks` à `AnyBlock`. `DocumentTool` reçoit le drapeau, choisit le modèle (`CALL` ou `CHART_CALL`), et n'ajoute `CHART_HELP` à la description qu'avec `charts`. Une écriture porte toujours des `AnyBlock` : le rendu ne dépend pas du drapeau.

### Ce que la relecture rend

`word`, avec `action: read`, rend un graphique du `.docx` à sa place dans le document :

```json
{"type": "graphique", "kind": "barres_horizontales", "title": "Total TTC par client",
 "categories": ["Julien Barre", "Chauffage Lemaire Rénovation", …],
 "series": [{"name": "Total TTC", "values": [18034.17, 16247.36, …]}]}
```

Les données viennent du cache du graphique, ce que Word y écrit aussi. C'est vrai que `charts` soit vrai ou faux (§ Le paramètre `charts`). Un graphique d'un autre type (nuage de points, aires…) revient avec son nom technique en `kind`, et sans données s'il est illisible. Un graphique n'est plus compté parmi les « images non lues ». `pdf` ne lit pas les graphiques : un PDF ne garde que leur dessin, et son texte en donne les libellés.

### Limites du premier lot

- Quatre types seulement : ni aires, ni nuage de points, ni barres empilées, ni deux axes.
- Pas de classeur embarqué dans le `.docx` (D-3).
- `from_table` ne vise que le tableau qui précède : pas de référence à un tableau plus haut.
- `loom.excel` n'est pas concerné : ses classeurs n'ont pas de graphiques, c'est un autre chantier (openpyxl sait en écrire).

## Analyse technique

### Modifications, mesurées sur le prototype

| Fichier | Changement |
| --- | --- |
| `_document/arguments.py` | +73 / −3 : le bloc `Chart` (`graphique`), `Series`, `FromTable`, et la règle « en ligne ou `from_table` » ; bornes `MAX_CATEGORIES` (50), `MAX_SLICES` (12), `MAX_SERIES` (6) ; les unions `Block` et `AnyBlock`, et `WithCharts` |
| `_document/content.py` | +165 / −4 : `ChartOut`, la validation, `from_table` (le dernier tableau et ses valeurs converties) ; `_table` rend aussi ses valeurs |
| `_document/tool.py` | +25 / −5 : `CHART_HELP`, la ligne « Graphique » de la description ; le drapeau `charts` au montage (modèle d'appel, description, `graphiques : oui/non`) |
| `word/chart.py` | Nouveau (265 lignes) : la partie DrawingML de chaque type, l'ancre dans le corps, les formats de nombres |
| `word/writer.py` | +44 / −8 : une partie, une relation et un type de contenu par graphique, **seulement s'il y en a** |
| `word/reader.py` | +109 / −3 : les graphiques relus depuis leur partie |
| `pdf/chart.py` | Nouveau (198 lignes) : les quatre types avec `reportlab.graphics` |
| `pdf/writer.py` | +29 : le titre, le dessin et la légende sur une même page ; les libellés au contrôle des glyphes |
| `word/tool.py` | +47 / −5 : `WordChartCall`, le drapeau passé par `build`, la note de relecture quand `charts` est faux |
| `pdf/tool.py` | +13 / −2 : `PdfChartCall`, le drapeau passé par `build` |
| `word_params.py`, `pdf_params.py` | +3, +2 : le champ `charts: bool = False` (et l'exemple de config de la docstring de `word`) |
| `tests/unit/test_document.py`, `test_word.py`, `test_pdf.py` | +12, +8, +2 tests |

Les lignes `charts`, `CHART_HELP`, `WithCharts`, `WordChartCall` et `PdfChartCall` viennent du paramètre `charts` (D-8).

Rien hors de `tools_builtin/_document`, `word/`, `pdf/` et des deux modules de paramètres : ni le moteur, ni le catalogue (il passe déjà ses paramètres à `build`), ni `pyproject.toml`.

### Choix techniques et leurs raisons

**Un graphique Word natif plutôt qu'une image.** Trois façons de mettre un graphique dans un `.docx` ont été pesées :

| | Natif (DrawingML) | Image PNG | Image SVG |
| --- | --- | --- | --- |
| Dépendance | Aucune | Un moteur de rendu : matplotlib (et numpy), ou le module optionnel de rendu de reportlab | Aucune (SVG écrit à la main) |
| Dans Word | Graphique retouchable, net, nombres dans la langue du lecteur | Image figée, floue à l'agrandissement | Word 2016 et plus, et Word exige en plus une image PNG de secours |
| Relecture | Données relues depuis le cache | Perdues | Perdues |
| Reproductible | Oui (texte XML) | Selon le moteur | Oui |

Le natif est le seul sans dépendance qui reste un vrai graphique. Le prix : 265 lignes de XML DrawingML, dont l'ordre des éléments doit suivre le schéma.

**Un dessin reportlab dans le PDF.** reportlab a ses propres graphiques (`reportlab.graphics.charts`), vectoriels, sans module de plus. Le style y est reproduit à la main (palette, étiquettes, graduations) pour que les deux formats se ressemblent.

**`from_table` désigne le tableau qui précède.** Une référence par numéro de bloc ou par nom a été écartée : un modèle compte mal ses blocs, et un tableau n'a pas de nom. « Le tableau juste avant » est la mise en page naturelle d'un rapport, et une règle qu'un modèle suit sans erreur.

**Un document sans graphique ne change pas.** Les types de contenu et les relations du `.docx` ne gagnent une entrée que pour un graphique. Les empreintes d'un document témoin (tableau, liste, note, citation, saut de page) sont les mêmes avant et après, en Word comme en PDF. Les fichiers déjà écrits par les agents restent reproductibles.

**Mesures.** Un document de quatre graphiques et un tableau : 11 Ko et 5 ms en `.docx`, 9,6 Ko et 0,1 à 0,2 s en PDF (3 pages). Description et schéma (JSON compact, un dossier en lecture, écriture et remplacement) :

| Outil | Sans `charts` | Avec `charts: true` | Écart |
| --- | --- | --- | --- |
| `word` | 1 866 + 3 785 | 2 418 + 5 034 | +1 801 caractères |
| `pdf` | 2 034 + 3 774 | 2 586 + 5 024 | +1 802 caractères |

Soit environ 500 tokens de plus par outil et par appel au modèle, pour un agent qui pose `charts: true`. Sans `charts`, description et schéma sont ceux du commit `1581a33`, à l'octet près.

**Couverture** des nouveaux modules : `word/chart.py` 94 %, `pdf/chart.py` 87 %, `content.py` 95 %, `word/reader.py` 91 %, les deux `writer.py` 98 %.

### Tests écrits sur le prototype (22)

| Thème | Tests |
| --- | --- |
| Socle (12) | `from_table` (libellés, séries converties, sans total, type), données en ligne (conversion, trou), 7 refus de contenu, 3 refus d'arguments |
| `charts` (5) | Word : désactivé par défaut (ni bloc dans le schéma, ni ligne dans la description ; `graphiques : oui` avec) ; `graphique` refusé par le schéma du moteur et par l'outil sans `charts` ; relecture sans `charts` : graphiques rendus, avec la note ; `loom validate` affiche `graphiques : oui`. PDF : désactivé par défaut, refusé sans, écrit avec |
| Word (4) | Graphique natif (parties, types de contenu, relations, ancres, sens des barres, cache, document reproductible) ; document sans graphique inchangé (types de contenu et relations identiques aux constantes) ; relecture dans le même vocabulaire, sans « image non lue » ; LibreOffice reconnaît les deux graphiques (`bar`, `circle`) et leurs valeurs |
| PDF (1) | Barres, courbe et secteurs dessinés (titre, libellés, `18 034,17 €`, `60,0 %` dans le texte), libellé sans glyphe signalé, fichier reproductible |

## Effets de bord sur le reste de loom

1. **Les requêtes ne changent que pour les agents qui posent `charts: true`** : environ 500 tokens par outil. Pour eux, un run fait avant ne se rejoue plus à l'identique (« ce qui diffère : les outils proposés »), et le cache de prompt est réécrit une fois. Les autres agents gardent les mêmes requêtes.
2. **La relecture d'un `.docx` change** pour un document qui contient des graphiques : un bloc `graphique` apparaît où il n'y avait rien, les indices des blocs suivants (`start`, `next_start`, `outline`) décalent d'un cran, et la note « image(s) non lue(s) » ne compte plus les graphiques.
3. **Ce qui ne change pas** : les fichiers sans graphique (mêmes octets), `loom.excel`, le moteur, le catalogue, l'extra `pdf`. La config de `loom.word` et `loom.pdf` gagne une clé facultative, `charts`.

## Plan de non-régression

| Contrôle | Attendu | Prototype |
| --- | --- | --- |
| Suite complète, tous les extras | Aucun test existant cassé | 3 769 passés (3 747 + 22), 203 sautés |
| Noyau seul | Les graphiques Word sans extra | 2 842 passés (2 822 + 20), 408 sautés |
| Documents sans graphique | Mêmes octets | Empreintes identiques, Word et PDF |
| Outils sans `charts` | Description et schéma d'aujourd'hui | Identiques à l'octet, `word` et `pdf` |
| `pyright` / `ruff` / `lint-imports` | 0 / propre / 5 tenus | 0 / propre / 5 tenus |

## Décisions à prendre

| # | Question | Options | Recommandation |
| --- | --- | --- | --- |
| D-1 | Le rendu dans le `.docx` | (a) graphique Word natif (prototype) ; (b) image PNG, avec une dépendance de rendu ; (c) image SVG | (a) : sans dépendance, retouchable, relu |
| D-2 | Les données | (a) en ligne ou `from_table` (prototype) ; (b) en ligne seulement ; (c) `from_table` seulement | (a) : `from_table` dès qu'un tableau les porte, en ligne sinon |
| D-3 | « Modifier les données » dans Word | (a) cache seul, sans classeur (prototype) ; (b) un classeur `.xlsx` embarqué par graphique, écrit à la main ou par openpyxl (extra `office`) | (a) pour ce lot ; (b) si tu veux retoucher les chiffres dans Word |
| D-4 | Les types | Les 4 du prototype ; ou y ajouter barres empilées et aires | Les 4 : ils couvrent comparer, évoluer, répartir |
| D-5 | Le style | Figé, comme le reste (prototype) ; ou des couleurs au choix du modèle | Figé |
| D-6 | Des graphiques dans `loom.excel` | Dans ce chantier, ou plus tard | Plus tard, avec sa propre analyse |
| D-7 | Version | 2.0.15 (non publiée), ou la suivante | À toi |
| D-8 | Activer les graphiques | Toujours ; ou par un paramètre de config, sur `word` et `pdf` | **Décidé** : paramètre `charts`, `false` par défaut, sur les deux outils |

**À vérifier sur ton poste :**

- Ouvrir `essai-graphiques.docx` **dans Word** : quatre graphiques (barres horizontales, courbe à deux séries, barres, secteurs). Word doit l'ouvrir sans message de réparation. Puis tester un clic droit sur un graphique, « Modifier le type de graphique ».
- Le même contenu en PDF : `essai-graphiques.pdf`.

## Plan de mise en œuvre

1. **Le socle** : le bloc `graphique` dans `_document` (arguments, contenu, description), le paramètre `charts` (les deux modèles d'appel, le drapeau au montage), et leurs tests.
2. **Word** : `word/chart.py`, l'écriture et la relecture, et leurs tests.
3. **PDF** : `pdf/chart.py` et l'écriture, et son test.
4. **Documentation** : le guide (chapitre 32, section `loom.word` et `loom.pdf`, avec la clé `charts`), `fonctions.md`, le `CHANGELOG`.
5. **Essai réel** : la même demande que dans `word-01` (« ajoute un graphique »), avec ton agent et `charts: true`.

Le prototype tient en un seul lot de code, s'il te va mieux qu'en trois.
