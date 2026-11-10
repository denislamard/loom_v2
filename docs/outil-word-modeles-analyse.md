# Analyse d'impact — des modèles de document pour `loom.word` et `loom.pdf` loom_v2

9 novembre 2026 · Denis Lamard

> **Décisions prises le 09/11/2026** : D-1 et D-2, refuser (un document qui s'écarte du plan, une section que le modèle ne déclare pas). D-4 : ne pas confondre le modèle d'un document avec ses maquettes. Un modèle décrit les sections fixes du document ; ce qui se répète (un écran, une exigence) est du contenu, rangé sous des titres de niveau 2 dans une section. Il n'y a donc pas de syntaxe « section répétée ».
>
> **Le 09/11 encore** : D-3, D-5, D-6, D-7 et D-8 : (a) ; D-9 : les bornes proposées ; D-11 : la version 2.0.20, l'actuelle. D-10 : `templates` pour la clé de config, `template` pour l'action et le champ. Le lot est codé (§ Mise en œuvre).

## En bref

Le besoin vient des essais du POC shaper.ia (journaux `test-01` à `test-100`, 8 novembre). Un agent écrit plusieurs types de documents : un cahier des charges, une fiche de maquettes, plus tard un devis ou un rapport. Chaque type a sa structure. Aujourd'hui, cette structure ne peut être décrite que dans le prompt de l'agent ou dans le `skill_file` de `loom.word`. Or un outil n'a qu'un seul `skill_file`, et la config refuse de monter deux fois `loom.word` dans un agent. Tous les modèles partent alors à chaque appel de modèle, et rien ne vérifie qu'un document suit le sien.

**Verdict : rien ne bloque.** Les modèles se déclarent dans la config de l'outil (`templates: {nom: fichier.md}`), comme un `skill_file` : des fichiers markdown relatifs à `resources_dir`, lus et vérifiés au chargement. Le modèle de langage n'en voit que les noms et une ligne de description. Il lit un modèle à la demande (`action: template`). À l'écriture, il nomme le modèle suivi, ou `aucun`, et **l'outil vérifie le plan du document** : les titres de niveau 1, comparés aux titres `#` du modèle. Un écart est refusé avec le plan attendu, ce qui permet au modèle de langage de se corriger dans le même tour, même s'il n'a pas lu le modèle. Sans `templates`, rien ne change pour le modèle, à l'octet près.

- **Tests :** 4 205 verts avec tous les extras, soit 4 178 plus 27 nouveaux. En noyau seul, 3 198 verts (+26) : les modèles de `word` marchent sans extra.
- **Qualité :** pyright strict à 0, ruff propre (`check` et `format --check`), les 6 contrats d'import tenus.
- **Coût :** pour les deux modèles de shaper, +780 caractères par appel au modèle (environ 210 tokens), au lieu de +3 937 caractères (environ 1 050 tokens) s'ils partaient tous deux en `skill_file`. Le texte d'un modèle ne coûte que lorsqu'il est lu.

| Point | Constat | Preuve |
| --- | --- | --- |
| Lus au chargement | Fichiers relatifs à `resources_dir`, contenus dans ce dossier ; plan vérifié dès `loom validate` | Démontré (fichier absent, hors du dossier, chemin qui répète `prompts/`, en-tête mal fermé, section en double) |
| Le modèle de langage choisit | `template` obligatoire à l'écriture : un nom déclaré, ou `aucun` | Démontré (« write attend 'template' ») |
| Plan vérifié | Section absente, en trop, en double, dans le désordre : refusé, avec le plan attendu | Démontré (dans `word` et dans `pdf`) |
| Comparaison souple | Numérotation, casse, accents, espaces et emphase ignorés | Démontré (« 1. Contexte et Objectifs », « 4) DONNÉES ») |
| Le document garde son modèle | Catégorie du `.docx` (`cp:category`) ; la relecture la rend en `template` | Démontré ; LibreOffice la garde (`OOXMLCorePropertyCategory`) |
| Document libre | `template: "aucun"` : même fichier, octet pour octet, qu'un document écrit sans modèles | Démontré (empreintes comparées) |
| Sans `templates`, rien ne change | Description et schéma de `word` et `pdf` identiques à ceux du commit `9303664`, avec ou sans `charts` et `images` (8 combinaisons) | Démontré (comparés à l'octet) |

**Les deux points qui demandaient une décision avant tout sont tranchés (09/11) :**

1. **La sévérité (D-1, D-2) : refuser.** Un document qui s'écarte du plan est refusé, de même qu'une section que le modèle ne déclare pas. Une section libre se déclare comme facultative (`# Annexes (facultatif)`).
2. **Modèle de document et maquettes (D-4).** Le modèle décrit les sections fixes du document. Les écrans de la fiche de maquettes sont du contenu de sa section « Écrans », sous des titres de niveau 2, et non des sections. La fiche a donc un plan vérifiable comme les autres (§ Ce que deviennent les skills de shaper).

## Périmètre et état de référence

L'analyse porte sur ton commit `9303664` (« add image into word doc », égal à `origin/main`, arbre propre). Rien n'a été écrit dans `/home/denis/dev/loom_v2`, à part ce document. Les essais ont tourné sur un clone de ce commit, dans l'espace de travail cloud de Claude : Python 3.14.6, LibreOffice 24.2.7, reportlab 5.1.0. La référence est celle de la fin du lot des images, au même code : 4 178 tests verts avec tous les extras, 3 172 en noyau seul, sans `tests/firecracker`.

Les essais réels se sont faits sur tes deux skills de shaper (`prompts/modele-cdc.md`, `prompts/modele-maquettes.md`), convertis au format des modèles dans l'espace cloud, pas sur ton disque (§ Ce que deviennent les skills de shaper).

## Analyse fonctionnelle

### La déclaration

```yaml
- python: loom.word
  images: true
  templates:                                   # facultatif ; relatifs à resources_dir
    cahier-des-charges: cahier-des-charges.md
    fiche-maquettes: fiche-maquettes.md
  folders:
  - name: workspace
    path: workspace
    rights: [ read, write, overwrite ]
```

- **La même clé sur `loom.word` et `loom.pdf`.** Les deux outils partagent `_document` et leur vocabulaire, et la vérification du plan est commune.
- **Les fichiers sont lus au chargement**, comme un `skill_file`, avec les mêmes règles : relatifs à `resources_dir`, contenus dans ce dossier (ni `..`, ni lien qui en sort), `loom validate` dit la faute. Le dossier des ressources reste fermé aux outils : c'est le chargeur qui lit, pas l'outil.
- **Les noms** : minuscules, chiffres et tirets, 40 caractères au plus, et pas `aucun`. Au plus 20 modèles par outil, 20 000 caractères par modèle.
- **Un modèle exige un dossier `write`** : sur un outil qui ne fait que lire, des modèles sont refusés au montage.

Les erreurs de config, telles que les dit le prototype :

```text
tools[loom.word].templates[fiche-maquettes] — modèle introuvable : …/prompts/absent.md
tools[loom.word].templates[fiche-maquettes] — le fichier doit être dans le dossier des ressources (…/prompts) : …/prompts/../secret.md
tools[loom.word].templates[fiche-maquettes] — modèle introuvable : …/prompts/prompts/fiche-maquettes.md — le chemin est relatif à resources_dir : écrire fiche-maquettes.md
tools[loom.word].templates[fiche-maquettes] — modèle fiche-maquettes : en-tête ouvert par --- et jamais fermé : …/prompts/casse.md
agents[redacteur].tools.0.loom.word.templates — nom de modèle 'aucun' : minuscules, chiffres et tirets (40 caractères au plus), et pas « aucun », qui choisit un document libre
```

### Le format d'un modèle

Du markdown, écrit comme un skill. **Ses titres `#` sont les sections que le document doit avoir**, dans cet ordre, et le texte qui les suit est la consigne de la section :

```markdown
---
description: Cahier des charges d'une application métier, à partir d'une interview.
---
Fichier : workspace/cahier-des-charges-<nom-court>.docx ; title : « Cahier des charges — <application> ».
Chaque section ci-dessous est un titre de niveau 1, dans cet ordre…

# Contexte et objectifs
paragraphe : le service demandeur, le problème actuel. citation : la demande initiale…

# Périmètre
…

# Maquettes (facultatif)
Pour chaque écran : titre 2 (son nom) ; paragraphe ; le bloc image rendu par wireframe…
```

- **`description`** (facultative, 200 caractères au plus) : la ligne que la description de l'outil montre pour ce modèle. C'est la seule clé lue dans l'en-tête.
- **« (facultatif) »** en fin de titre : la section peut manquer.
- **Seuls les titres `#` comptent.** Un `##` reste de la consigne, et un `#` dans un bloc de code (` ``` `) est ignoré.
- **Un modèle sans titre `#`** n'a que des consignes : il est lu et nommé, mais son plan n'est pas vérifié.

### Ce que voit le modèle de langage

Seulement avec `templates`. La description de l'outil gagne ces lignes (pour les deux modèles de shaper) :

> Modèles de document : à l'écriture, 'template' nomme le modèle suivi, ou aucun pour un document libre ; les titres de niveau 1 du document suivent alors le plan du modèle, dans l'ordre. Avant d'écrire, lire le modèle : action=template, template=<nom> (ses consignes et son plan).
> \- cahier-des-charges : Cahier des charges d'une application métier, à partir d'une interview.
> \- fiche-maquettes : Fiche qui présente les écrans d'une application, chacun avec sa maquette et ses champs.

Le schéma gagne l'action `template` et un champ `template`, dont les valeurs sont les noms déclarés et `aucun`. `path` devient facultatif, parce que lire un modèle ne vise aucun fichier : il reste exigé pour `write` et `read`.

**Lire un modèle :**

```json
{"action": "template", "template": "cahier-des-charges"}
```

```json
{"template": "cahier-des-charges",
 "description": "Cahier des charges d'une application métier, à partir d'une interview.",
 "plan": ["Contexte et objectifs", "Périmètre", "Acteurs et volumétrie", "Processus cible",
          "Maquettes (facultatif)", "Exigences fonctionnelles", "…"],
 "text": "---\ndescription: …"}
```

Sans effet ni approbation (`assess_call` : `none`, idempotent), puisque rien n'est lu sur le disque : le texte vient de la config.

**Écrire :**

```json
{"path": "workspace/cahier-des-charges-formations.docx", "template": "cahier-des-charges",
 "title": "Cahier des charges — Formations", "blocks": [ … ]}
```

Le résultat gagne `"template": "cahier-des-charges"` (ou `"aucun"`), à côté de `blocks`.

### La vérification du plan

Les titres de niveau 1 du document sont comparés aux sections du modèle :

- chaque section non facultative est présente ;
- aucune section n'est hors du modèle, ni en double ;
- les sections sont dans l'ordre du modèle.

La comparaison est souple : numérotation (« 1. », « 2.3 - », « 4) », « a) », « IV. »), casse, accents, espaces, emphase (`**…**`) et ponctuation finale sont ignorés. « 1. Contexte et Objectifs » est donc la section « Contexte et objectifs ». Les sous-parties (niveau 2 et 3) sont libres.

Un écart est refusé, avec les autres fautes du document, et **le plan attendu suit** :

```text
Document refusé :
- modèle cahier-des-charges : section hors du modèle « Annexes »
- modèle cahier-des-charges : section(s) absente(s) : « Règles de gestion »
- modèle cahier-des-charges : « Piste de solution recommandée » avant « Intégrations » : le modèle les met dans l'autre ordre
- modèle cahier-des-charges, plan attendu (titres de niveau 1, dans l'ordre) : Contexte et objectifs · Périmètre · Acteurs et volumétrie · Processus cible · Maquettes (facultatif) · Exigences fonctionnelles · Règles de gestion · Données et conformité · Exigences non fonctionnelles · Intégrations · Piste de solution recommandée · Hypothèses et points ouverts · Indicateurs de succès et échéance
```

C'est le cœur de la proposition. Un skill est une consigne que le modèle de langage peut oublier. Ici, l'outil ne laisse pas passer un document qui s'écarte du plan, et il dit comment le corriger. Les essais du 8/11 l'ont montré : sur un refus clair (`PLACEHOLDER`, un titre de niveau sauté), HAIKU corrige au tour suivant.

### La relecture (`action: read`)

- **Avec `templates`**, un `.docx` écrit avec un modèle le rend : `"template": "cahier-des-charges"`. Une réécriture le reprend sans qu'on le redise. Un document qui nomme un modèle que l'outil ne déclare pas le rend aussi, avec une note : « Le document dit suivre le modèle « offre », que cet outil ne déclare pas : pour le réécrire, nommer un modèle déclaré, ou aucun. »
- **Sans `templates`**, rien ne change : ni `template`, ni note, même pour un document qui en porte un.
- Le modèle est rangé dans la **catégorie** du document (`cp:category`, Fichier > Propriétés dans Word). Il n'y est que pour un document écrit avec un modèle : un document libre ou sans modèles a les mêmes octets qu'avant. LibreOffice garde la catégorie (propriété `OOXMLCorePropertyCategory`).
- **Le PDF** vérifie le plan, mais ne garde pas le modèle : `pdf` ne relit que le texte d'un PDF (D-8).

### Ce que deviennent les skills de shaper

**`modele-cdc.md`** se convertit sans perte : ses 13 sections deviennent 13 titres `#`, « Maquettes » est marquée « (facultatif) », et les règles générales passent en tête. Converti, il fait 2 729 caractères et son plan est vérifié.

**`modele-maquettes.md`** (avec tes ajouts « Description » et « Champs ») mettait chaque écran en titre de niveau 1. Selon D-4, les écrans sont du contenu : la fiche a deux sections fixes, « Présentation » et « Écrans ». Chaque écran est un titre de niveau 2 dans « Écrans », avec sa phrase et son image, puis « Description » et « Champs » en titres de niveau 3. Convertie ainsi, elle fait 1 221 caractères et son plan est vérifié : une fiche qui met un écran en titre de niveau 1 est refusée, avec « section hors du modèle « Dépôt de la demande » » et « section(s) absente(s) : « Écrans » ». Ce plan est une proposition, à valider à la mise en œuvre dans shaper. Autre remarque : la consigne « aligné à gauche » ne peut pas être suivie, puisque l'alignement est fixé par le style de l'outil. Les titres y sont de toute façon alignés à gauche.

Le passage des skills aux modèles se fait dans shaper (`agents/*.yaml`, `prompts/`), après tes décisions.

### Limites du premier lot

- Seuls les titres de niveau 1 sont vérifiés : ni les sous-parties, ni les blocs attendus (un tableau et ses colonnes), ni les textes fixes.
- Les sections d'un modèle sont fixes. Ce qui se répète (un écran, une exigence, un lot) est du contenu d'une section, sous des titres de niveau 2 que le plan ne vérifie pas (D-4).
- Une numérotation à quatre chiffres en tête d'un titre (« 2026 objectifs ») est prise pour une numérotation et ignorée.
- Au plus 10 fautes sont dites par refus (`MAX_ERRORS_SAID`) : dans un document qui en a beaucoup d'autres, la ligne du plan attendu peut être coupée.

## Analyse technique

### Modifications, mesurées sur le prototype

| Fichier | Changement |
| --- | --- |
| `_templates.py` | Nouveau (164 lignes, bibliothèque standard seule ; à côté de `_office_params`, hors de `_document`, pour qu'une config se valide sans importer l'outil) : `parse` (en-tête, titres `#`, « (facultatif) », blocs de code), `key` (comparaison souple), `plan_faults`, les bornes |
| `_document/arguments.py` | +98 / −16 : `TemplateArguments`, `WriteArguments.template` ; `variant(…, templates=…)` ajoute l'action `template`, le champ `template` (noms déclarés et `aucun`), `path` facultatif, et la règle des champs par action |
| `_document/content.py` | +32 / −3 : `prepare(…, template)` vérifie le plan (`_check_plan`) ; `Prepared.template` |
| `_document/tool.py` | +62 / −4 : les modèles au montage, leurs lignes dans la description, l'action `template`, le modèle passé à `prepare`, `template` dans le résultat, `modèles : …` pour `loom validate` |
| `_office_params.py` | +72 / −2 : `TemplateRef` (`file` ou `text`, forme courte `nom: fichier.md`), le type `Templates`, `read_templates` (la faute d'une référence non chargée) |
| `config/loader.py` | +43 / −1 : `_templates_read`, à côté de `_skill_read` : chaque fichier lu dans `resources_dir`, analysé, rangé en `text` |
| `word/writer.py` | +6 / −2 : la catégorie, seulement pour un document qui suit un modèle |
| `word/reader.py` | +36 / −8 : titre et catégorie lus ensemble ; `template` et la note, seulement avec des modèles |
| `word/tool.py`, `pdf/tool.py` | +18 / −4, +9 / −2 : les modèles passés par `build` |
| `word_params.py`, `pdf_params.py` | +5, +3 : `templates: Templates = {}` |
| `tests/unit/test_document.py`, `test_word.py`, `test_pdf.py` | +148, +170, +16 |

Le moteur, le catalogue, `agents/spec.py` et `pyproject.toml` ne changent pas.

### Choix techniques et leurs raisons

**Lire au chargement, comme `skill_file`, plutôt qu'au montage (D-5).** Le chargeur a déjà `resources_dir` et le contrôle « reste dans le dossier » (`_within_resources`), et une faute de modèle se voit dès `loom validate`. Lire au montage demanderait de passer `resources_dir` aux outils fournis (`BuiltinContext`) et de refaire ce contrôle. L'outil reçoit des textes, pas des chemins. Seul prix : le chargeur reconnaît un outil à modèles par son attribut `templates`, plutôt que par une déclaration du catalogue.

**`template` obligatoire à l'écriture, avec `aucun`.** C'est l'option « exiger » sans en avoir l'inconvénient. Le modèle de langage fait un choix explicite à chaque document : suivre un modèle déclaré ou écrire librement. Un document libre reste possible. Le champ ne peut pas être `required` dans le schéma, puisqu'il ne sert pas à `read`. La règle est donc dans le validateur, avec un message qui dit quoi faire.

**`path` facultatif dans la variante à modèles.** Lire un modèle ne vise aucun fichier. Sans ce changement, le modèle de langage devrait envoyer un chemin bidon. `path` reste exigé pour `write` et `read`, par la même règle.

**La catégorie du document plutôt qu'une propriété personnalisée (D-6).** `cp:category` est une propriété standard du paquet OPC : une ligne dans `docProps/core.xml`, que Word et LibreOffice gardent. Une propriété personnalisée (`docProps/custom.xml`) demanderait une partie, une relation et un type de contenu de plus, et Word la montre aussi. L'inconvénient : quelqu'un peut changer la catégorie dans Word.

**Le module des modèles est hors de `_document`.** Valider une config ne doit importer aucun outil (`test_validating_a_config_imports_only_the_parameter_modules`) ; or les paramètres (`_office_params`), que la config importe, ont besoin de `parse`. Le premier passage de la suite complète l'a attrapé : `_templates.py` est donc à côté de `_office_params`, sans autre dépendance que la bibliothèque standard.

**La variante s'appuie sur `variant()`** (lot des images). Pas de nouvelle classe d'appel : la fabrique ajoute l'action, les champs et la règle (`__validators__`), et met le résultat en cache par noms de modèles.

### Coût mesuré

Description et schéma en JSON compact, un dossier en lecture, écriture et remplacement :

| `word` | Description + schéma | Écart |
| --- | --- | --- |
| Sans réglage | 1 866 + 3 785 = 5 651 | — |
| `images` | 2 245 + 4 255 = 6 500 | |
| `images` + 1 modèle | 2 619 + 4 535 = 7 154 | +654 |
| `images` + les 2 modèles de shaper | 2 727 + 4 553 = 7 280 | **+780** |
| shaper aujourd'hui (`charts`, `images`) | 2 797 + 5 503 = 8 300 | |
| shaper avec les 2 modèles | 3 279 + 5 801 = 9 080 | +780 |
| *Pour comparer : les 2 modèles en `skill_file`* | | *+3 937* |

Soit environ 210 tokens par appel au modèle, au ratio de loom (4 caractères par token), dont environ 125 caractères par modèle de plus (sa ligne dans la description, son nom dans le schéma). Le texte d'un modèle, lui, ne coûte qu'à sa lecture : 2 729 caractères pour le cahier des charges, qui restent ensuite dans l'historique du run.

**Couverture** sur les trois fichiers de tests : `_templates.py` 98 %, `_office_params.py` 88 %.

### Tests écrits sur le prototype (14 fonctions)

| Thème | Tests |
| --- | --- |
| Socle (`test_document.py`, 6 fonctions, 19 cas) | Un modèle est ses titres (description, facultatif, `##` et blocs de code ignorés, modèle sans plan) ; 8 refus de modèle ; 7 cas de comparaison souple ; les fautes de plan (hors modèle, double, absente, ordre) ; `prepare` refuse et donne le plan attendu ; la variante (énumération des noms, 8 règles d'arguments) |
| Word (`test_word.py`, 7) | Désactivé sans modèles (schéma, description, `modèles : aucun`), et seuls les noms dans la description ; l'action `template` et son évaluation sans effet ; plan refusé, puis suivi, la catégorie écrite et relue ; un document libre identique à un document sans modèles ; un modèle inconnu relu (note) et un outil sans modèles qui n'en dit rien ; des modèles sans dossier `write`, et une référence non chargée ; lecture au chargement, `loom validate` et 4 fautes de config |
| PDF (`test_pdf.py`, 1) | Plan refusé puis suivi |

Les tests existants touchés : quatre, pour la ligne `modèles : aucun` de `loom validate` (`test_settings_for_validate`, `test_validate_shows_the_folders`, `test_validate_shows_images` et leur équivalent dans `test_pdf.py`).

## Bugs existants trouvés en route

Aucun nouveau. B-2 (une forme Word comptée deux fois) et B-3 (`\r\n` venu du modèle) de l'analyse des images restent ouverts. La course entre une écriture et une relecture lancées dans le même tour, vue dans les journaux de shaper (sessions `01a1bc66` et `test-100`), relève du moteur et mérite son propre lot. Les modèles la rendent moins probable, puisque la relecture devient inutile pour vérifier le plan, mais ils ne la corrigent pas.

## Effets de bord sur le reste de loom

1. **Les requêtes ne changent que pour les agents qui déclarent des `templates`** : environ 210 tokens pour deux modèles. Pour eux, un run d'avant ne se rejoue plus à l'identique (« ce qui diffère : les outils proposés »), et le cache de prompt est réécrit une fois.
2. **Un modèle modifié** change ce que rend `action: template`, comme un `skill_file` modifié change la description de l'outil. Un rejeu qui relit le modèle le voit.
3. **`loom validate`** ajoute `modèles : aucun` (ou leurs noms) à la ligne de chaque outil `word` et `pdf`.
4. **Ce qui ne change pas** : les outils sans `templates` (requêtes, résultats, journaux), les documents sans modèle (mêmes octets), `loom.memory` (qui lit les `.docx` sans modèles), le moteur, le catalogue, les extras.

## Plan de non-régression

| Contrôle | Attendu | Prototype |
| --- | --- | --- |
| Suite complète, tous les extras | Aucun test existant cassé (hors les 4 lignes de `validate`) | 4 205 passés (4 178 + 27), 201 sautés |
| Noyau seul | Les modèles sans extra | 3 198 passés (3 172 + 26), 411 sautés |
| Outils sans `templates` | Description et schéma d'aujourd'hui | Identiques à l'octet, `word` et `pdf`, 8 combinaisons de `charts` et `images` |
| Documents sans modèle | Mêmes octets | Un document `aucun` identique à un document écrit sans modèles |
| `pyright` / `ruff` / `lint-imports` | 0 / propre / 6 tenus | 0 / propre / 6 tenus |

## Décisions

Classées par importance.

| # | Question | Options | Recommandation |
| --- | --- | --- | --- |
| D-1 | Ce que l'outil garantit | (a) `template` obligatoire à l'écriture (ou `aucun`) et plan vérifié, écart refusé (prototype) ; (b) plan vérifié, écart seulement averti ; (c) le modèle seulement tracé | **Décidé (09/11) : (a), refuser** |
| D-2 | Une section que le modèle ne déclare pas | (a) refusée (prototype) ; (b) permise | **Décidé (09/11) : (a), refuser.** Une section libre se déclare (`# Annexes (facultatif)`) |
| D-3 | Le format d'un modèle | (a) markdown, titres `#` = sections, « (facultatif) », `description` en tête (prototype) ; (b) un fichier structuré (YAML : sections, consignes) | **Décidé (09/11) : (a)**, le markdown, titres `#` = sections |
| D-4 | Ce qui se répète dans un document (les écrans d'une fiche de maquettes) | (a) du contenu d'une section fixe, sous des titres de niveau 2 ; (b) une syntaxe « section répétée » | **Décidé (09/11) : (a).** Ne pas confondre le modèle d'un document avec ses maquettes : la fiche a les sections « Présentation » et « Écrans », et chaque écran est un titre de niveau 2 dans « Écrans ». Rien à ajouter au format |
| D-5 | Où lire les fichiers | (a) le chargeur de config, comme `skill_file` (prototype) ; (b) la fabrique de l'outil, avec `resources_dir` dans `BuiltinContext` | **Décidé (09/11) : (a)**, le chargeur de config |
| D-6 | Où le `.docx` garde son modèle | (a) la catégorie (`cp:category`, prototype) ; (b) une propriété personnalisée ; (c) nulle part | **Décidé (09/11) : (a)**, la catégorie |
| D-7 | La relecture d'un document au modèle inconnu | (a) `template` et une note, seulement pour un outil à modèles (prototype) ; (b) rien | **Décidé (09/11) : (a)** |
| D-8 | Le PDF | (a) même vérification, sans garder le modèle (prototype) ; (b) le modèle dans les métadonnées du PDF (`/Subject`) | **Décidé (09/11) : (a)** |
| D-9 | Les bornes | 20 modèles par outil, 20 000 caractères par modèle, 200 pour la description, 40 pour le nom (prototype) | **Décidé (09/11)** : les bornes proposées |
| D-10 | Le nom de la clé, de l'action et du champ | `templates`, `template` (prototype), en anglais comme `folders`, `charts`, `images` ; ou `modeles` | **Décidé (09/11)** : `templates` dans la config, `template` pour l'action et le champ |
| D-11 | Version | 2.0.20 (non publiée, avec les images), ou la suivante | **Décidé (09/11)** : 2.0.20 |

## À vérifier sur ton poste

- Rien ne demande Word. Un document écrit avec un modèle porte seulement une catégorie, une propriété standard. Tu peux la voir dans Fichier > Informations > Propriétés.

## Mise en œuvre

Faite le 9 novembre 2026, en un seul lot, à partir du prototype, sur ton commit `9303664`. S'y ajoutent la documentation (guide : « Des modèles de document » ; `configuration.md` : `resources_dir` ; `fonctions.md` ; `CHANGELOG` 2.0.20) et un message plus net pour une section donnée deux fois dans un modèle (le titre tel qu'écrit, plus sa forme comparée). Résultat : 4 205 tests verts avec tous les extras (4 178 + 27), 3 198 en noyau seul (3 172 + 26), pyright strict à 0, ruff propre, 6 contrats d'import tenus. shaper n'est pas touché (étape 5 ci-dessous, à ta demande).

Le plan d'origine :


1. **Le socle** : `_templates.py`, la variante, `prepare`, le montage dans `DocumentTool`, les paramètres, et leurs tests.
2. **La config** : `_templates_read` dans le chargeur, et ses tests.
3. **Word** : la catégorie, la relecture, et leurs tests ; **PDF** : la vérification, et son test.
4. **Documentation** : dans `docs/howto/06-outils-fournis.md`, une sous-section « Des modèles de document » (déclaration, format, vérification, relecture), la clé `templates` dans l'exemple de config, la ligne de `loom validate`, « Coût ». Dans `docs/configuration.md`, une ligne pour les fichiers lus dans `resources_dir`. Puis `docs/fonctions.md` et le `CHANGELOG`.
5. **shaper** : les deux modèles convertis dans `prompts/` (la fiche avec ses sections « Présentation » et « Écrans », D-4), `templates:` dans `agents/shaper.yaml` et `agents/maquettes-test.yaml` à la place des `skill_file`, et le prompt de shaper allégé de sa section « Le cahier des charges ».
6. **Essai réel** : un cahier des charges et une fiche de maquettes, avec HAIKU, en relisant les journaux pour compter les refus de plan et les corrections.

Le prototype tient en un seul lot de code.
