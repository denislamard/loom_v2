# Analyse d'impact — des images dans les documents de `loom.word` (et `loom.pdf`) loom_v2

8 novembre 2026 · Denis Lamard

> **Décisions prises le 08/11/2026** : la version est la 2.0.20 (D-11) et les recommandations de § Décisions à prendre sont retenues : D-1 (a), D-2 (a), D-3 (a), D-4, D-5 (a), D-6, D-7, D-8 (a), D-9 (96 ppp), D-10 (B-2 et B-3 dans un lot à part). Le bloc est codé dans ce lot (§ Mise en œuvre).

## En bref

Le besoin vient de l'essai réel du POC shaper.ia. L'agent produit des maquettes d'écrans en PNG avec son propre outil Python, et doit les mettre dans le cahier des charges `.docx` qu'il écrit avec `loom.word`. L'outil ne sait pas insérer d'image (`outil-word-pdf-analyse.md` : « Pas d'image (pas de logo) », D-6 laissée ouverte). Le besoin est générique : un logo sur un devis, une photo de chantier dans un rapport, une capture dans une procédure. Cette analyse mesure ce que coûte un bloc `image`, sur un prototype complet.

**Verdict : rien ne bloque.** Le bloc `image` s'ajoute au vocabulaire commun, sur le modèle du bloc `graphique`. Il n'est actif qu'avec `images: true`. Sans ce réglage, le modèle voit exactement ce qu'il voyait avant, à l'octet près. Les images se lisent **sans Pillow ni autre dépendance** : la bibliothèque standard suffit pour reconnaître un PNG ou un JPEG, lire ses dimensions et vérifier qu'il est entier. Le `.docx` reste reproductible octet pour octet. Le même bloc marche dans `loom.pdf` sans extra de plus : reportlab tire déjà Pillow.

- **Tests :** 4 169 verts avec tous les extras, soit 4 134 plus 35 nouveaux. En noyau seul, sans Pillow ni reportlab, 3 163 verts (+34) : les images de `word` marchent sans extra.
- **Qualité :** pyright strict à 0, ruff propre (`check` et `format --check`), les 6 contrats d'import tenus.
- **Coût :** +849 caractères par outil et par appel au modèle (description +379, schéma +470), soit **environ 230 tokens**. Seuls les agents qui posent `images: true` le paient.
- **Rendu :** contrôlé à l'œil, LibreOffice (conversion en PDF puis en PNG) et PDF reportlab côte à côte. Ce sont les mêmes images, aux mêmes tailles, sur les mêmes pages.

| Point | Constat | Preuve |
| --- | --- | --- |
| Image dans le `.docx` | Une partie `word/media/imageN.png\|jpeg` par image distincte, une relation, un `Default` de type de contenu, un `<wp:inline>` centré | Démontré (archive relue : parties, relations, types, `r:embed`) |
| Même image deux fois | Stockée une fois (empreinte SHA-256), deux dessins | Démontré |
| `docPr` unique | Un compteur pour tous les dessins, graphiques compris | Démontré (1 à 5 avec un graphique au milieu) |
| Reproductible | Deux appels identiques donnent le même fichier, `.docx` et PDF | Démontré (empreintes comparées) |
| Sans `images`, rien ne change | Description et schéma de `word` et `pdf` identiques à ceux du commit de référence | Démontré (comparés à l'octet) |
| Document sans image | Mêmes types de contenu et relations qu'avant, à l'octet | Démontré |
| Relecture puis réécriture | Une image lue revient en bloc `image` (`path`, `width`, `caption`, `alt`) ; réécrite telle quelle, elle redonne **le même fichier** | Démontré (même SHA-256) |
| Image abîmée | PNG tronqué, altéré, pixels manquants, palette absente ; JPEG tronqué, codage exotique ; dimension nulle : refusés avant toute écriture | Démontré (14 cas) |
| LibreOffice | Ouvre le `.docx`, montre les images et leur texte alternatif | Démontré (conversion en PDF et en ODT) |

**Trois choses sont à vérifier ou à trancher :**

1. **Word lui-même (§ À vérifier sur ton poste).** LibreOffice lit le prototype, mais rien ici ne peut ouvrir Word. Le fichier d'essai `essai-images.docx` contient tous les cas.
2. **Les photos prises de côté (D-3).** Ni LibreOffice ni reportlab n'appliquent l'orientation EXIF d'une photo de téléphone : elle s'affiche couchée. Le prototype avertit. Le comportement de Word décide de la suite (une image du fichier d'essai sert à le voir).
3. **La relecture (D-2).** Elle rend `path` à partir du nom du dessin, où l'écriture a rangé l'adresse du fichier lu.

En route, trois bugs existants (§ Bugs existants trouvés en route). Le premier touche les agents qui ont `charts: true` : leur schéma perd le suffixe `.docx` / `.pdf` dans la description de `path`.

## Périmètre et état de référence

L'analyse porte sur ton arbre de travail au moment de l'étude : le commit `453a8c9` (« bug on CI », égal à `origin/main`) et le passage en 2.0.20, non commité (`CHANGELOG.md`, `pyproject.toml`, `uv.lock`). Rien n'a été écrit dans `/home/denis/dev/loom_v2`, à part ce document. Le shell de ton poste n'avait plus de place disque (96 Mo libres sur `/sessions`), donc les essais ont tourné sur un clone de ce même état, dans l'espace de travail cloud de Claude. Versions : Python 3.14.6, LibreOffice 24.2.7, reportlab 5.1.0, Pillow 13.0.0, pypdf 6.21.4.

| Contrôle sur l'état de référence | Résultat |
| --- | --- |
| `pytest`, tous les extras, sans services, sans `tests/firecracker` | 4 134 passés, 201 sautés (au second tour, un échec de `test_access_mcp_http::test_cancel_needs_the_right_on_the_agent_of_the_run` : instable, il passe au premier) |
| `pyright` strict / `ruff` / `lint-imports` | 0 / propre / 6 tenus |

`tests/firecracker` est exclu des deux côtés : il demande la VM Firecracker, que l'espace cloud n'a pas.

## Analyse fonctionnelle

### Ce que le modèle envoie

Un bloc de plus dans `blocks`, le même pour `word` et `pdf` :

```json
{"type": "image", "path": "workspace/maquettes/depot-demande.png",
 "width": "pleine_largeur", "caption": "Écran 1 — Dépôt de la demande par le manager"}
```

- **`path`** (obligatoire) : un fichier d'un dossier déclaré qui a le droit `read`. Il est désigné comme les autres (`<dossier>/<chemin>`), avec les mêmes contrôles : dossier connu, pas de `..`, pas de fichier caché, lien qui sort du dossier refusé, fichier absent. Le modèle n'envoie jamais le contenu de l'image : l'outil la lit lui-même.
- **`width`** : `pleine_largeur` (17 cm, la largeur du texte, par défaut), `moitie` (8,5 cm) ou `petite` (4 cm, un logo). Des valeurs fermées et sémantiques, comme le reste de l'outil (D-4). La hauteur suit les proportions de l'image. Une image plus haute que 21 cm est ramenée à 21 cm, et sa largeur suit.
- **`caption`** (facultative) : la légende, sous l'image, dans le style des légendes de tableau (`Caption`), avec `**gras**` et `*italique*`. Elle est gardée sur la même page que l'image (`keepNext`).
- **`alt`** (facultatif) : le texte alternatif. Sans lui, c'est la légende qui sert. Sans l'un ni l'autre, un avertissement le dit (D-5).
- L'image est **en ligne, centrée**, dans le flux du texte : ni position flottante, ni habillage.

La ligne que la description ajoute (`IMAGE_HELP`, 378 caractères) :

> Image ('type': 'image') : path, un fichier PNG ou JPEG d'un dossier qui a le droit read ; width : pleine_largeur (par défaut), moitie, ou petite (un logo) ; caption, la légende dessous ; alt, ce que montre l'image pour qui ne la voit pas (la légende sinon). L'image est centrée, dans ses proportions ; l'outil la lit lui-même, ne jamais en envoyer le contenu. 50 images au plus.

### Ce que le modèle reçoit

Le résultat garde sa forme, avec `images` à côté de `blocks` (seulement pour un outil monté avec `images`) :

```json
{"path": "workspace/sortie/cdc.docx", "bytes": 638863, "written": true, "replaced": false,
 "sha256": "…", "blocks": 11, "images": 7,
 "warnings": ["bloc 7 (image) : workspace/maquettes/logo.png, 120 x 60 pixels sur 4,0 cm (76 pixels par pouce) : floue à l'impression ; une image plus grande, ou une largeur plus petite"]}
```

Quatre avertissements, qui ne refusent rien :

| Cas | Avertissement |
| --- | --- |
| Moins de 96 pixels par pouce une fois posée | « …120 x 60 pixels sur 4,0 cm (76 pixels par pouce) : floue à l'impression ; une image plus grande, ou une largeur plus petite » |
| Photo à orientation EXIF 5 à 8 | « …est une photo prise de côté (orientation EXIF 6) : elle peut s'afficher couchée ; la redresser avant » (D-3) |
| PNG animé (APNG) | « …est animée : seule sa première image s'affiche » |
| Ni `alt` ni `caption` | « ni alt ni caption : l'image n'a pas de texte pour qui ne la voit pas » |

### Ce que l'outil refuse

Les fautes d'image rejoignent la liste unique, au numéro du bloc, avec celles du reste du document. Rien n'est écrit. L'essai du prototype, huit images fautives dans un même appel :

```text
Document refusé :
- bloc 1 (image) : Droit read refusé sur valide/b.png (dossier valide : write)
- bloc 2 (image) : Dossier inconnu : 'inconnu'. Dossiers permis : maquettes, sortie, valide, secret
- bloc 3 (image) : maquettes/absente.png : fichier introuvable
- bloc 4 (image) : Chemin refusé : maquettes/dehors.png mène hors des dossiers permis
- bloc 5 (image) : maquettes/note.png : format non reconnu : PNG ou JPEG attendu (le format se lit dans le fichier, pas dans son nom)
- bloc 6 (image) : maquettes/coupee.png : PNG tronqué (fichier incomplet)
- bloc 7 (image) : maquettes/lourde.jpg dépasse 10000000 octets
```

(Le huitième bloc, une image valide, passe.) Les autres refus :

| Cas | Réponse |
| --- | --- |
| GIF | « image GIF : la convertir en PNG » |
| WebP | « image WebP : Word ne l'affiche pas partout ; la convertir en PNG » |
| SVG | « image SVG : la convertir en PNG » |
| PNG altéré (somme de contrôle d'un bloc), pixels manquants, palette absente, en-tête faux | « PNG abîmé (…) », « PNG tronqué ou abîmé (pixels manquants) » |
| JPEG sans fin d'image, segment coupé | « JPEG tronqué (…) » |
| JPEG sans perte, hiérarchique ou arithmétique | « JPEG d'un codage que Word n'affiche pas (…) : le réenregistrer en JPEG standard ou en PNG » |
| Largeur ou hauteur nulle | « image de dimension nulle » |
| Plus de 40 millions de pixels | « 8000 x 6000 pixels, au-delà de 40 millions : la réduire » |
| Plus de 50 images, plus de 40 Mo d'images distinctes | Refusé, au niveau du document |
| `width` hors des trois valeurs, champ inconnu, `path` absent | Refusé par le schéma |

### Le paramètre `images`

Comme `charts` (D-8 de l'analyse des graphiques) : `images: bool = False` dans `WordParams` et `PdfParams`, outil par outil.

```yaml
tools:
- python: loom.word
  images: true          # facultatif, false par défaut
  folders:
  - name: workspace
    path: workspace
    rights: [ read, write, overwrite ]
```

- **Avec `false`**, le schéma et la description sont ceux d'aujourd'hui, à l'octet près, pour `word` comme pour `pdf` (mesuré : 1 866 + 3 785 caractères pour `word`, 2 034 + 3 774 pour `pdf`).
- **Les deux réglages se combinent** sans multiplier les classes. `WithCharts`, `WordChartCall` et `PdfChartCall` disparaissent au profit d'une fabrique, `variant(base, charts=…, images=…)`. Elle construit l'appel du format avec `pydantic.create_model`, met le résultat en cache, et rend `base` lui-même sans réglage. Quatre unions de blocs portent un nom, parce que ce nom paraît dans le schéma (`$defs`) : `Block` (rien), `AnyBlock` (graphiques, son nom d'aujourd'hui), `ImageBlock` (images) et `EveryBlock` (les deux, et ce que porte une écriture). Le nom du modèle d'appel ne paraît pas dans le schéma allégé.
- **Un bloc `image` sans `images`** est refusé par le moteur, qui valide contre le schéma (« ne correspond à aucune des variantes autorisées »).
- **`loom validate`** : `fuseau : Europe/Paris · graphiques : oui · images : oui · lecture des documents : oui`. La ligne gagne `images : non` pour tous les outils `word` et `pdf`, ce qui ne change rien pour le modèle.

### Droits, approbation, effets

`assess_call` compte le droit `read` de chaque dossier d'image, en plus du `write` (ou `overwrite`) de la sortie. Une image d'un dossier sous `approval: { read: always }` fait attendre l'appel :

```text
écrire le fichier sortie/a.docx, avec 4 image(s) lue(s) dans maquettes, secret — la config exige une approbation pour lire dans secret
```

L'évaluation ne lit rien sur le disque : elle situe chaque chemin tel qu'écrit (`Folders.locate`), comme pour la sortie. Une image dans un dossier sans `read` rend l'appel « sans effet », puisqu'il sera refusé à l'exécution, comme toute autre faute de chemin. Les effets de bord restent ceux de l'écriture (`reversible` ou `irreversible`) : lire ne change rien.

### La relecture d'un `.docx` (`action: read`)

**Avec `images: true`**, une image du corps revient en bloc `image`, à sa place, dans le vocabulaire de l'écriture :

```json
{"type": "image", "path": "workspace/maquettes/depot-demande.png", "width": "pleine_largeur",
 "caption": "Écran 1 — Dépôt de la demande par le manager"}
```

- **`path`** : l'adresse du fichier que l'outil a lu à l'écriture. Elle est rangée dans le nom du dessin (`docPr name`), que Word et LibreOffice gardent (D-2). Une image insérée ailleurs (« Image 1 ») revient sans `path`, et une note le dit : « 4 image(s) sans fichier source (pas de path) : pour réécrire le document, donner un path vers un fichier d'un dossier read, ou retirer le bloc. »
- **`width`** : la valeur, parmi les trois, qui redonne la taille lue d'après les pixels de l'image (lus dans l'en-tête de sa partie). Elle est donc exacte même pour une image bornée en hauteur. Pour une image d'ailleurs, c'est la plus proche.
- **`caption`** : le paragraphe de style `Caption` qui suit immédiatement l'image devient sa légende, pas un bloc. Ce rattachement est propre aux images : la légende d'un tableau revient toujours en `paragraphe`, comme aujourd'hui.
- **`alt`** : le texte alternatif, omis quand il est égal à la légende (c'est ce que l'écriture en aurait fait).

Le cas « relire puis réécrire le même document » est couvert par un test : la réécriture des blocs lus redonne le même fichier, octet pour octet.

**Sans `images`**, rien ne change : les images ne sont pas rendues, la note « N image(s) ou forme(s) non lue(s) » les compte. Contrairement aux graphiques, la note n'invite pas à poser `images: true`. Changer son texte changerait les résultats des agents qui n'ont pas le réglage.

### `loom.pdf`

**Recommandation : le bloc arrive dans `loom.pdf` en même temps (D-1).** Le contrat des deux outils est « le même appel donne le même document dans les deux formats ». Le coût est faible :

- **Aucune dépendance de plus.** reportlab déclare Pillow comme dépendance : `uv tree --invert --package pillow` montre `pillow ← reportlab ← loom-ia (extra: pdf)`. L'extra `pdf` l'installe déjà.
- **26 lignes** dans `pdf/writer.py` : un `platypus.Image` aux tailles calculées par `_document` (les mêmes que dans le `.docx`), centré, gardé avec sa légende (`KeepTogether`).
- **Reproductible** : deux appels identiques donnent le même PDF (testé).
- **Limite** : un PDF de reportlab n'est pas balisé (pas de PDF/UA), donc le texte alternatif ne s'y retrouve pas. La légende, elle, est du texte.

L'autre option : refuser `images: true` sur `loom.pdf` au montage, avec un message explicite, en attendant. Elle ne fait rien gagner : le prototype est fait et testé.

### Limites du premier lot

- PNG et JPEG seulement (D-6).
- Ni position flottante, ni habillage, ni rognage, ni rotation.
- Pas d'image dans un tableau, une note ou un en-tête de page.
- La relecture ne rend pas une image placée dans un tableau ou une zone de texte : elle reste comptée comme non lue.
- Un PDF lu (`pdf`, `action: read`) ne rend pas ses images : on n'y lit que le texte.

## Analyse technique

### Modifications, mesurées sur le prototype

| Fichier | Changement |
| --- | --- |
| `_document/arguments.py` | +74 / −14 : le bloc `Image`, `ImageWidth` ; les unions `ImageBlock` et `EveryBlock` ; la fabrique `variant()` à la place de `WithCharts` (gardé comme alias, pour les tests) ; les bornes `MAX_IMAGE_BYTES` (10 Mo), `MAX_IMAGES_BYTES` (40 Mo), `MAX_IMAGES` (50), `MAX_PIXELS` (40 M) |
| `_document/picture.py` | Nouveau (287 lignes) : `parse()` rend une `Picture` (format, pixels, octets, empreinte, orientation EXIF, animée) ou une `PictureError` qui dit quoi faire ; `dimensions()` pour la relecture |
| `_document/content.py` | +120 / −3 : `ImageOut`, `prepare(…, pictures)`, `placed()` (largeur, proportions, borne de hauteur), les avertissements, les bornes du document |
| `_document/tool.py` | +99 / −18 : `IMAGE_HELP`, le drapeau `images`, `_pictures()` (la lecture des images), `assess_call` qui compte les `read`, `images: N` dans le résultat, `_load` avec une borne |
| `word/image.py` | Nouveau (55 lignes) : le `<w:drawing><wp:inline>` et son `pic:pic` |
| `word/writer.py` | +73 / −27 : `_Body` (le corps et ce qu'il demande à l'archive), les parties `word/media`, leurs relations et types de contenu **seulement s'il y en a**, le compteur de dessins, les images rangées sans recompression |
| `word/chart.py` | +8 / −3 : l'identifiant du dessin passé à part ; sans image, le même qu'avant |
| `word/reader.py` | +105 / −4 : les images rendues en blocs avec `images`, la légende rattachée, la note des images sans source |
| `word/tool.py`, `pdf/tool.py` | +8 / −10, +1 / −8 : plus de `WordChartCall` ni de `PdfChartCall` ; le drapeau passé par `build` |
| `pdf/writer.py` | +26 / −1 : l'image et sa légende |
| `word_params.py`, `pdf_params.py` | +3, +2 : `images: bool = False` |
| `tests/unit/image_files.py` | Nouveau (84 lignes) : des PNG et JPEG fabriqués avec la bibliothèque standard |
| `tests/unit/test_document.py`, `test_word.py`, `test_pdf.py` | +170, +264, +40 |

Rien hors de `tools_builtin/_document`, `word/`, `pdf/` et des deux modules de paramètres : ni le moteur, ni le catalogue, ni `pyproject.toml`.

### Où lire les images : `prepare()` reste pur

`prepare()` valide sans entrée-sortie, et c'est ce qui le rend testable sans disque. La lecture des images se fait donc **avant**, dans `DocumentTool._write`, déjà exécuté hors de la boucle asyncio (`asyncio.to_thread`) :

1. `_image_paths(request)` : les chemins des blocs `image`, chacun une fois, dans l'ordre du document.
2. `_pictures(request)` : pour chacun, `locate` puis `require(read)`, `resolved` (liens suivis, contrôlés), une lecture bornée à 10 Mo, puis `picture.parse`. Une faute n'est **pas levée** : elle est rangée comme texte (`Pictures = Mapping[str, Picture | str]`). La lecture s'arrête quand les images lues dépassent la borne du document, et les suivantes sont marquées non lues.
3. `prepare(request, zone, pictures)` : pour chaque bloc `image`, l'image lue ou sa faute, dite **au numéro du bloc**, dans la même liste « Document refusé : … » que le reste.

`prepare` reçoit des données, pas un accès au disque. Sa signature garde `pictures` facultatif, et les appels existants ne changent pas.

### Lire une image sans Pillow

Ni le noyau ni `loom.word` n'ont de dépendance, et il n'en faut pas :

- **Le format vient des octets** (`core/model/media.sniff`), jamais de l'extension. GIF, WebP et SVG sont reconnus pour être refusés avec un conseil.
- **PNG** : chaque bloc est vérifié (CRC-32), dans l'ordre (`IHDR` d'abord), jusqu'à `IEND`. Les pixels compressés (`IDAT`) sont **décompressés par tranches d'1 Mo et comptés, jamais gardés**. Le compte doit être exactement celui qu'annonce l'en-tête, entrelacement Adam7 compris. C'est ce qui attrape une image tronquée ou abîmée que Word afficherait en croix rouge. Le coût mesuré : 87 ms pour une capture Retina de 2 880 × 1 800 (11,8 Mo), 89 ms pour une photo de 12 Mpx en PNG (36 Mo, au-delà de la borne de toute façon).
- **JPEG** : les segments sont parcourus jusqu'aux données (`SOS`). On y lit les dimensions (`SOF0`, `SOF1`, `SOF2` ; les autres codages sont refusés), puis l'orientation EXIF (`APP1`). Il faut une fin d'image (`FFD9`) après les données ; ce qui suit est ignoré (les « photos animées » de certains téléphones y ajoutent une vidéo). Les pixels ne sont pas décodés : un JPEG abîmé au milieu de ses données passe. Coût : 1 ms pour une photo de 600 Ko.
- **La résolution annoncée n'est pas lue** (`pHYs`, densité JFIF, résolution EXIF). La taille dans la page vient de `width`, pas des pouces que déclare le fichier : une capture Retina déclare 144 ppp, une photo 72 ppp, et aucune des deux valeurs ne dit la taille voulue. La densité utile se calcule sur la taille posée (pixels ÷ pouces), et c'est elle qui déclenche l'avertissement « floue à l'impression ».
- **Bornes** : 40 millions de pixels au plus, vérifiés dès l'en-tête, avant de décompresser quoi que ce soit. C'est aussi ce qui protège Word, LibreOffice et Pillow (côté PDF) d'une image qui tiendrait en quelques Ko compressés mais en centaines de Mo une fois ouverte.

### L'écriture du `.docx`

- **Les parties** : `word/media/image1.png`, `image2.png`, `image3.jpeg`… numérotées dans l'ordre où chaque image paraît la première fois, et dédoublonnées par SHA-256. L'extension est `png` ou `jpeg`, d'après le format reconnu.
- **Les types de contenu** : un `Default` par format présent (`png` → `image/png`, `jpeg` → `image/jpeg`), placé après celui de `xml`.
- **Les relations** : `rIdImage1`… de type `…/relationships/image`, après celles des graphiques.
- **Le dessin** : `<w:drawing><wp:inline>` avec `wp:extent`, `wp:docPr` (`id` unique, `name` = l'adresse du fichier lu, `descr` = le texte alternatif), `a:graphicFrameLocks noChangeAspect="1"` (Word garde les proportions si on le redimensionne), `pic:pic` et `a:blip r:embed`. L'ordre des éléments suit le schéma DrawingML.
- **`docPr id` unique** : un seul compteur pour les graphiques et les images, dans l'ordre du corps. Sans image, un graphique garde l'identifiant qu'il avait (son numéro) : les documents à graphiques ne changent pas.
- **Les images sont rangées sans recompression** (`ZIP_STORED`) : un PNG ou un JPEG est déjà compressé, et le recompresser coûte du temps pour rien. Le XML reste en `ZIP_DEFLATED`.
- **Un document sans image** a les mêmes types de contenu et relations qu'avant, à l'octet (testé contre les constantes `CONTENT_TYPES` et `DOCUMENT_RELS`).

**Mesures**, sur un document de 11 blocs, 7 images (dont une photo JPEG de 600 Ko) et un graphique : 22 ms en `.docx` (639 Ko), 580 ms en PDF (806 Ko, 4 pages ; reportlab fait deux rendus pour « Page n / N », et Pillow décode les images à chaque fois). Relecture du `.docx` : 7 ms.

### Coût en tokens

Description et schéma en JSON compact, un dossier en lecture, écriture et remplacement :

| Outil | Rien | `images` | `charts` | `charts` + `images` |
| --- | --- | --- | --- | --- |
| `word` | 1 866 + 3 785 = 5 651 | 2 245 + 4 255 = 6 500 (**+849**) | 2 418 + 5 039 = 7 457 | 2 797 + 5 503 = 8 300 |
| `pdf` | 2 034 + 3 774 = 5 808 | 2 413 + 4 244 = 6 657 (**+849**) | 2 586 + 5 028 = 7 614 | 2 965 + 5 492 = 8 457 |

Les caractères sont mesurés. En tokens, c'est une estimation : environ 235 tokens au ratio de l'analyse des graphiques (1 801 caractères pour 500 tokens), 212 au ratio de loom (`CHARS_PER_TOKEN = 4`). Aucun compteur de tokens n'était accessible depuis l'espace d'essai. Un `count_tokens` sur ton poste donnerait le chiffre exact, si tu en as besoin. La colonne `charts` montre +5 caractères par rapport au commit de référence : c'est le correctif du bug B-1 (D-8).

**Couverture** des modules touchés, sur les trois fichiers de tests : `word/image.py` 100 %, `word/writer.py` 98 %, `pdf/writer.py` 98 %, `content.py` 95 %, `_document/tool.py` 94 %, `word/reader.py` 91 %, `picture.py` 85 %. Pour `picture.py`, il manquait les octets de bourrage et les marqueurs isolés du JPEG, et l'EXIF gros-boutiste : complétés à la mise en œuvre (94 %).

### Tests écrits sur le prototype (35)

| Thème | Tests |
| --- | --- |
| Socle (`test_document.py`, 7 fonctions, 26 cas) | PNG et JPEG reconnus sur leurs octets (5 types de couleur, entrelacé, APNG, orientation EXIF) ; 14 refus (vide, GIF, WebP, SVG, inconnu, PNG tronqué, altéré, dimension nulle, palette absente, pixels manquants, trop de pixels, JPEG tronqué, codage arithmétique, dimension nulle) ; tailles et proportions (trois largeurs, borne de hauteur) ; avertissements ; fautes d'image dans la liste du document ; bornes du document ; arguments refusés ; `variant()` n'ajoute que ce qui est demandé |
| Word (`test_word.py`, 10) | Désactivé par défaut et `path` qui garde `.docx` ; refusé par le schéma sans `images` ; archive (parties, `ZIP_STORED`, `Default`, relations, `r:embed`, `docPr` uniques avec un graphique, tailles en EMU, légende avec son image, reproductible) ; document sans image inchangé ; 7 fautes dites ensemble et rien d'écrit ; `assess_call` avec approbation en lecture ; relecture puis réécriture au même octet ; relecture sans `images` ; image d'ailleurs sans `path` ; LibreOffice (conversion en ODT : images, cadres, texte alternatif) ; `loom validate` |
| PDF (`test_pdf.py`, 1) | Images posées dans leurs pixels, légende dans le texte, désactivé par défaut, reproductible |

Les tests existants touchés : trois, pour la ligne `images : non` de `loom validate` (`test_settings_for_validate`, `test_validate_shows_the_folders`, `test_the_spec_names_the_folders_and_the_reading`).

## Bugs existants trouvés en route

| # | Bug | Effet | Preuve | Correctif |
| --- | --- | --- | --- | --- |
| B-1 | `WordChartCall(WithCharts, WordCall)` : par l'ordre d'héritage, le `path` de `DocumentCall` l'emporte sur celui de `WordCall` (idem `PdfChartCall`) | Avec `charts: true`, la description de `path` perd son suffixe (`« <dossier>/<chemin> »` au lieu de `« <dossier>/<chemin>.docx »`), et l'ordre des propriétés du schéma change | Schémas comparés au commit de référence | La fabrique `variant()` part du modèle du format : corrigé dans le prototype (D-8) |
| B-2 | La lecture compte chaque `drawing` **et** chaque `pict` | Une forme ou une zone de texte de Word (`mc:AlternateContent` : un dessin et son remplaçant VML) est comptée deux fois : « 2 image(s) ou forme(s) non lue(s) » pour une seule | `.docx` fabriqué, lu par le code de référence | Ne pas compter dans un `mc:Fallback` (non fait dans le prototype : hors sujet). **Corrigé le 09/11/2026** (`word/reader.py`, essai `test_a_shape_and_its_fallback_count_once`) |
| B-3 | Un `\r\n` venu du modèle n'est pas normalisé : `_run` coupe sur `\n` et laisse un `\r` brut dans `<w:t>` | La relecture rend une ligne vide de trop (`"ligne 1\n\nligne 2"`) ; LibreOffice affiche une espace en fin de ligne ; Word non vérifié | `.docx` écrit et relu par le code de référence, converti par LibreOffice | `\r\n` et `\r` ramenés à `\n` dans `_text` (non fait dans le prototype). **Corrigé le 09/11/2026** : à la préparation, dans le titre, les textes, les en-têtes et cellules de tableau, les libellés et séries d'un graphique, pour `word` et `pdf` (`content.lines`) |

## Effets de bord sur le reste de loom

1. **Les requêtes ne changent que pour les agents qui posent `images: true`** : environ 230 tokens par outil. Pour eux, un run d'avant ne se rejoue plus à l'identique (« ce qui diffère : les outils proposés »), et le cache de prompt est réécrit une fois.
2. **Les agents qui ont `charts: true`** voient leur schéma changer de 5 caractères si le correctif B-1 est retenu (D-8) : mêmes effets, une fois.
3. **`loom validate`** gagne `images : non` (ou `oui`) dans la ligne de chaque outil `word` et `pdf`.
4. **Ce qui ne change pas** : les documents sans image (mêmes octets), les agents sans `images` ni `charts` (requêtes, schémas, résultats, journaux), `loom.memory` (qui lit les `.docx` avec `reader.read` sans le drapeau), le moteur, le catalogue, les extras. Aucun corpus de rejeu (`tests/regression`, j1 à j7) n'utilise `loom.word` ni `loom.pdf`, et ils passent tous.

## Plan de non-régression

| Contrôle | Attendu | Prototype |
| --- | --- | --- |
| Suite complète, tous les extras | Aucun test existant cassé (hors les 3 lignes de `validate`) | 4 169 passés (4 134 + 35), 201 sautés |
| Noyau seul, sans Pillow ni reportlab | Le bloc image de `word` sans extra | 3 163 passés (3 129 + 34), 411 sautés |
| Corpus de rejeu j1 à j7 | Identiques | Passés (dans la suite complète) |
| Outils sans `images` ni `charts` | Description et schéma d'aujourd'hui | Identiques à l'octet, `word` et `pdf` |
| Outils avec `charts` seul | Identiques, ou le correctif B-1 | +5 caractères (`.docx` / `.pdf` rendu à `path`) |
| Documents sans image | Mêmes octets | Types de contenu et relations identiques aux constantes |
| `pyright` / `ruff` / `lint-imports` | 0 / propre / 6 tenus | 0 / propre / 6 tenus |

## Décisions (retenues le 08/11/2026)

Classées par importance.

| # | Question | Options | Recommandation |
| --- | --- | --- | --- |
| D-1 | `loom.pdf` en même temps | (a) le bloc dans les deux outils (prototype) ; (b) `images: true` refusé sur `loom.pdf` au montage, en attendant | **(a)** : le contrat « même appel, même document » tient, sans dépendance de plus (Pillow vient avec reportlab), pour 26 lignes déjà testées |
| D-2 | Ce que rend la relecture | (a) un bloc `image` dont `path` est l'adresse rangée à l'écriture dans le nom du dessin, et une note pour une image sans source (prototype) ; (b) dimensions et légende seulement, sans `path` ; (c) extraire l'image dans un dossier `write` ; (d) continuer à la compter comme non lue | **(a)** : une relecture suivie d'une réécriture redonne le même fichier. (c) ferait d'une lecture une écriture, avec droits, approbation et nom de fichier à choisir. Le prix de (a) : l'adresse (`workspace/maquettes/x.png`) est visible dans le volet de sélection de Word du destinataire. Variante : ne ranger que le nom du fichier, et le modèle retrouve le dossier |
| D-3 | Photos prises de côté (EXIF 5 à 8) | (a) avertir (prototype) ; (b) redresser : dans le PDF avec Pillow (`ImageOps.exif_transpose`), dans le `.docx` par une rotation du dessin et l'EXIF ramené à 1 (deux octets à changer, sans décoder) ; (c) refuser | **(a) pour ce lot**, puis **(b)** selon ce que fait Word avec le fichier d'essai. LibreOffice et reportlab ignorent l'EXIF : si Word aussi, (b) est sûr ; s'il l'applique, l'EXIF ramené à 1 évite la double rotation. C'est le cas des photos de chantier prises au téléphone |
| D-4 | Les largeurs | `pleine_largeur` 17 cm, `moitie` 8,5 cm, `petite` 4 cm, hauteur bornée à 21 cm (prototype) ; ou d'autres valeurs, un `tiers` | Les trois du prototype : texte, demi-page, logo. 21 cm laisse la place d'une légende sous une maquette de téléphone, sur la même page |
| D-5 | `alt` | (a) facultatif, la légende sinon, un avertissement sans l'un ni l'autre (prototype) ; (b) obligatoire | **(a)** : obligatoire, il serait rempli pour rien sur un logo, ou recopierait la légende. L'avertissement suffit à le rappeler (RGAA) |
| D-6 | Les formats | PNG et JPEG (prototype) ; plus GIF (Word et LibreOffice le lisent, sa première image seulement) ; WebP ; SVG avec une image de repli | PNG et JPEG. GIF, WebP et SVG sont refusés avec « la convertir en PNG » : l'agent qui les fabrique peut le faire. Un SVG demanderait un moteur de rendu pour son repli |
| D-7 | Les bornes | 10 Mo par image, 40 Mo par document, 50 images, 40 millions de pixels (prototype) | Celles-là : une photo de téléphone de 12 Mpx tient (3 à 5 Mo) ; 40 Mo d'images restent sous la borne de 50 Mo du fichier écrit ; une photo de 48 Mpx est refusée avec « la réduire » |
| D-8 | Le correctif B-1 (`path` des appels avec `charts`) | (a) dans ce lot, par `variant()` (prototype) ; (b) garder le schéma d'aujourd'hui à l'octet pour les agents à graphiques | **(a)** avec une ligne ⚠ au `CHANGELOG` : 5 caractères, une fois, pour des agents récents (2.0.15) ; (b) obligerait à garder les classes à double héritage |
| D-9 | Le seuil de l'avertissement de netteté | 96 pixels par pouce une fois posée (prototype) ; 150 (impression correcte) ; aucun | 96 : en deçà, l'image est agrandie au-delà de ses pixels même à l'écran. À 150, la plupart des captures d'écran en pleine largeur seraient signalées |
| D-10 | Les bugs B-2 et B-3 | Dans ce lot, ou à part | À part, dans un petit lot de la lecture et de l'écriture du texte : ils ne touchent pas aux images |
| D-11 | Version | 2.0.20 (non publiée), ou la suivante | **Décidé** : 2.0.20 |

## À vérifier sur ton poste

Je t'envoie `essai-images.docx` et `essai-images.pdf` dans la conversation ; rien n'est déposé sur ton disque.

- Ouvrir `essai-images.docx` **dans Word** : il doit s'ouvrir **sans message de réparation**. Sur 5 pages, il y a une maquette de téléphone bornée en hauteur, un PNG transparent, un logo, un graphique au milieu des images, la même maquette réutilisée, une photo JPEG, un PNG à palette entrelacé.
- **La photo à orientation EXIF 6** (dernière page) : une flèche vers la **droite**, Word ignore l'EXIF comme LibreOffice ; vers le **bas**, il l'applique. C'est ce qui décide de D-3.
- Clic droit sur une image, « Modifier le texte de remplacement » : on doit y voir la légende, ou le texte `alt` donné.
- Le volet de sélection (Accueil, Sélectionner) montre le nom des dessins, c'est-à-dire l'adresse des fichiers (D-2).

## Mise en œuvre

Faite le 08/11/2026, en un seul lot, à partir du prototype : le code ci-dessous, les tests qui manquaient à `picture.py` (bourrage et marqueur isolé du JPEG, EXIF gros-boutiste, fin d'image absente, pixels illisibles : 94 % couverts), la documentation. Résultat : 4 178 tests verts avec tous les extras (4 134 + 44), 3 172 en noyau seul (3 129 + 43), pyright strict à 0, ruff propre, 6 contrats d'import tenus. Le plan d'origine :


1. **Le socle** : `picture.py`, le bloc `image` et `variant()` dans `_document`, `prepare` avec `pictures`, la lecture dans `DocumentTool`, et leurs tests (avec ceux qui manquent à `picture.py`).
2. **Word** : `word/image.py`, l'écriture et la relecture, et leurs tests.
3. **PDF** (si D-1 a) : l'image dans `pdf/writer.py`, et son test.
4. **Documentation** : dans `docs/howto/06-outils-fournis.md`, une sous-section « Des images » sur le modèle de « Des graphiques », la clé `images` dans l'exemple de config, la ligne de `loom validate`, la relecture, « À savoir » et « Coût » mis à jour. `docs/configuration.md` ne décrit pas les paramètres de `loom.word` : rien à y changer. `docs/outil-word-pdf-analyse.md` : D-6 tranchée pour les images, avec un renvoi ici. Le `CHANGELOG`, avec ⚠ pour B-1 si D-8 a.
5. **Essai réel** : le cahier des charges de shaper.ia, avec ses maquettes et `images: true`.

Le prototype tient en un seul lot de code.
