# Outil Excel pour loom_v2 — mise en forme « niveau Claude »

> Note de conception, sans code. Rédigée le 25/10/2026.
> Question traitée : un outil fourni par loom_v2 peut-il produire des classeurs aussi bien formatés que ceux que Claude génère, et à quelles conditions ?

---

## 1. Réponse courte

Oui. Claude n'utilise aucune technologie inaccessible à loom_v2. Il écrit le classeur avec **openpyxl**, manipule les données en volume avec **pandas**, et vérifie les formules en faisant recalculer le fichier par **LibreOffice en mode headless**.

La qualité du rendu ne vient pas de la bibliothèque mais de trois couches posées au-dessus :

| Couche | Chez Claude | Dans loom_v2 |
|---|---|---|
| 1. Conventions | Écrites dans les instructions de la skill xlsx | Codées dans l'outil (style maison) + rappelées dans sa description |
| 2. Jugement | Le LLM décide de tout, en code libre | Le LLM choisit dans un vocabulaire fermé (types, rôles) |
| 3. Vérification | Recalcul LibreOffice + lecture des erreurs | Validation statique systématique + recalcul optionnel |

L'approche déclarative proposée ici reproduit les couches 1 et 2. Elle est même **plus constante** que Claude, puisque la forme ne dépend plus de la façon dont le LLM écrit son code d'un run à l'autre. La couche 3 est la seule qui a un coût réel (dépendance LibreOffice) et fait l'objet d'une décision à part.

---

## 2. Comment Claude produit un classeur bien formaté

### 2.1 Outillage

- **openpyxl** : création et édition, formules, styles, formats de nombre, fusion, gel des volets.
- **pandas** : chargement, nettoyage et agrégation des données avant écriture.
- **LibreOffice headless** : un script recalcule toutes les formules, réécrit le fichier avec les valeurs en cache et renvoie un rapport (statut, nombre de formules, nombre d'erreurs, cellules en erreur par type).

### 2.2 Couche 1 — les conventions

Règles appliquées à tout classeur produit, sauf demande contraire de l'utilisateur :

- **Police sobre et unique** dans tout le classeur.
- **Formules plutôt que valeurs calculées.** Un total s'écrit `=SUM(B2:B9)`, jamais avec le résultat calculé en Python. Le classeur doit se recalculer quand ses entrées changent.
- **Respect littéral de la demande.** Noms d'onglets, en-têtes et formules exactement comme spécifiés.
- **Hypothèses documentées dans le classeur**, en commentaire de cellule ou en cellule adjacente, avec leur source.
- **Classeur à remplir** : une légende indiquant les cellules à saisir et une ligne d'exemple réaliste.
- **Édition d'un fichier existant** : ses conventions priment sur toutes les autres. On n'écrit que dans ses cellules de saisie et on ne touche pas aux formules existantes.

Conventions spécifiques aux modèles financiers :

- **Code couleur** : texte bleu pour les saisies et leviers de scénario, noir pour les formules, vert pour les liens vers un autre onglet, rouge pour les liens vers un autre fichier, fond jaune pour les hypothèses clés et les cellules à remplir.
- **Nombres** : unité dans l'en-tête (« CA (k€) »), zéros affichés en tiret, négatifs entre parenthèses, pourcentages **stockés en fraction** (0,15 → 15,0 %), multiples en `0.0x`, années **stockées en texte** (sinon « 2 024 »).
- **Structure** : chaque hypothèse dans sa propre cellule libellée et référencée par les formules (`=B5*(1+$B$6)`, jamais `=B5*1.05`), formules homogènes sur toute une ligne de projection, dénominateurs protégés contre le zéro.

### 2.3 Couche 2 — le jugement

Le LLM décide au cas par cas : quelle colonne est un montant, où geler les volets, ce qui mérite d'être mis en évidence, comment découper en onglets.

### 2.4 Couche 3 — la vérification

- openpyxl écrit les formules **sans valeur en cache**. Tant que le fichier n'est pas recalculé, toute lecture des valeurs (pandas, `data_only=True`, la plupart des aperçus) renvoie `None`.
- Le recalcul LibreOffice produit les valeurs et liste les erreurs (`#REF!`, `#DIV/0!`, `#NAME?`, `#VALUE!`…). Le classeur n'est livré qu'avec zéro erreur.
- **Limite essentielle** : un recalcul sans erreur prouve que les formules *s'évaluent*, pas qu'elles sont *justes*. Une plage décalée d'une ligne donne un fichier propre aux chiffres faux. D'où la pratique d'écrire deux ou trois formules et de contrôler leurs valeurs avant de remplir toute une grille.

---

## 3. Transposition dans loom_v2

### 3.1 Place dans l'architecture

- Outil fourni dans `src/loom_ia/tools_builtin/`, **inactif par défaut**, activé par l'agent dans son `agent.yaml` (`- python: loom.<outil>`), comme `loom.fetch_page`.
- Nom de l'outil et du module : **à définir**.
- Dépendance openpyxl en **extra optionnel** du paquet `loom_ia`, avec un import paresseux. Si l'agent active l'outil sans l'extra installé, il faut une erreur explicite au chargement de l'agent, pas au premier appel.

### 3.2 Principe directeur : séparer le sens de la forme

Le LLM ne manipule jamais de styles (couleurs, polices, codes de format). Il décrit **ce que sont** les données. L'outil décide **à quoi elles ressemblent**.

| Décidé par le LLM | Décidé par l'outil |
|---|---|
| Onglets, leur ordre et leur nom | Police, tailles, couleurs |
| En-têtes et ordre des colonnes | Style des en-têtes (gras, fond, bordure) |
| Type sémantique de chaque colonne | Code de format Excel correspondant |
| Rôle des lignes et cellules (saisie, total, hypothèse) | Style associé à chaque rôle |
| Données ou référence vers les données | Largeurs de colonnes |
| Formules | Gel des volets, filtre automatique |
| Commentaires et notes d'hypothèse | Mise en page homogène d'un run à l'autre |

---

## 4. Vocabulaire sémantique proposé

### 4.1 Types de colonnes

Les codes de format sont écrits **en syntaxe neutre** dans le fichier. Excel applique ensuite les séparateurs de la locale de l'utilisateur (virgule décimale et espace des milliers en FR).

| Type | Valeur stockée | Code de format proposé | Rendu FR |
|---|---|---|---|
| `texte` | chaîne | `@` | tel quel |
| `entier` | int | `#,##0` | 1 234 |
| `decimal` | float | `#,##0.00` | 1 234,50 |
| `montant_eur` | float | `#,##0.00 "€";-#,##0.00 "€";"-"` | 1 234,50 € / -12,00 € / - |
| `montant_keur` | float (en k€) | `#,##0 "k€";-#,##0 "k€";"-"` | 1 235 k€ |
| `pourcentage` | **fraction** (0,15) | `0.0%;-0.0%;"-"` | 15,0 % |
| `date` | date | `dd/mm/yyyy` | 25/10/2026 |
| `date_heure` | datetime **naïf** | `dd/mm/yyyy hh:mm` | 25/10/2026 17:50 |
| `annee` | **chaîne** ("2026") | `@` | 2026 |
| `booleen` | texte « Oui »/« Non » | `@` | Oui |

Remarques :

- `dd/mm/yyyy` fige l'ordre français. Le format intégré n° 14 s'adapte à la locale du lecteur ; le choix dépend du public visé (décision n° 5).
- La convention des négatifs entre parenthèses vient de la finance anglo-saxonne. En France, le signe moins est plus courant. Le style maison tranche (décision n° 5).

### 4.2 Rôles de cellules et de lignes

| Rôle | Signification | Style proposé |
|---|---|---|
| `entete` | Ligne d'en-têtes | Gras, fond coloré, bordure basse, texte centré, retour à la ligne |
| `saisie` | Valeur à saisir ou modifier par l'utilisateur | Texte bleu, fond jaune pâle |
| `formule` | Cellule calculée | Texte noir (défaut) |
| `lien_onglet` | Formule pointant vers un autre onglet | Texte vert |
| `hypothese` | Paramètre utilisé par les formules | Texte bleu, fond jaune, commentaire obligatoire (source) |
| `total` | Ligne de total ou sous-total | Gras, bordure haute |
| `note` | Texte d'explication ou légende | Italique, gris |

L'outil peut **déduire** certains rôles plutôt que les demander : une cellule dont la valeur commence par `=` est une `formule`, et une formule contenant `!` est un `lien_onglet`.

### 4.3 Options de feuille

| Option | Défaut proposé |
|---|---|
| Gel des volets | Sous la ligne d'en-têtes |
| Filtre automatique | Activé sur la plage d'en-têtes |
| Largeur des colonnes | Calculée (cf. § 5) |
| Légende | Générée automatiquement si la feuille contient des cellules `saisie` |
| Ligne d'exemple | Sur demande uniquement |

---

## 5. Style maison

Ensemble de constantes codées dans l'outil, appliquées à tous les classeurs :

- **Police** : une seule famille (par ex. Arial ou Calibri), taille 10 ou 11, en-têtes à la même taille en gras.
- **Couleurs** : palette fixe pour les rôles du § 4.2.
- **Bordures** : fines et discrètes. Pas de quadrillage dessiné sur toutes les cellules (le quadrillage d'Excel suffit).
- **Alignements** : texte à gauche, nombres à droite (défaut Excel, à ne pas forcer), en-têtes centrés.
- **Largeur des colonnes** : openpyxl ne calcule rien. L'outil estime la largeur à partir de la plus longue valeur *affichée* (en-tête compris), avec un minimum et un maximum (par ex. 8 à 60 caractères). Au-delà, retour à la ligne. Les formules n'ont pas de valeur connue avant recalcul : on retient la largeur de l'en-tête ou une valeur par défaut selon le type.
- **Hauteur de ligne d'en-tête** : augmentée si le retour à la ligne est activé.

Point ouvert : style figé dans le code, ou thème surchargeable dans `loom.yaml` (décision n° 6).

---

## 6. Exemple de spécification (illustratif)

Exemple de ce que le LLM enverrait à l'outil. Le format exact sera défini lors de la conception du schéma.

```json
{
  "fichier": "relances-octobre.xlsx",
  "onglets": [
    {
      "nom": "Hypothèses",
      "colonnes": [
        {"entete": "Paramètre", "type": "texte"},
        {"entete": "Valeur", "type": "pourcentage"}
      ],
      "lignes": [
        {"cellules": ["Taux de conversion visé", 0.15], "role": "hypothese",
         "note": "Source : objectif fixé par l'utilisateur"}
      ]
    },
    {
      "nom": "Suivi",
      "colonnes": [
        {"entete": "Client", "type": "texte"},
        {"entete": "Date devis", "type": "date"},
        {"entete": "Montant HT", "type": "montant_eur"},
        {"entete": "Relances", "type": "entier"}
      ],
      "donnees": {"source": "csv", "chemin": "exports/devis.csv"},
      "total": {"Montant HT": "somme", "Relances": "somme"}
    }
  ]
}
```

Points illustrés :

- aucun style dans la spec ;
- données référencées plutôt que transmises en ligne (cf. § 9.2) ;
- ligne de total déclarée, la formule `=SUM(...)` étant générée par l'outil ;
- hypothèse isolée dans un onglet dédié, avec sa source.

---

## 7. Pièges openpyxl / Excel à intégrer

### 7.1 Formules

| Piège | Conséquence | Parade dans l'outil |
|---|---|---|
| Noms de fonctions en français (`SOMME`, `SI`, `RECHERCHEV`) | `#NAME?` | Rejet à la validation, avec un message qui donne l'équivalent anglais |
| Séparateur `;` au lieu de `,` | Formule invalide | Rejet à la validation |
| Fonctions post-2007 sans préfixe (`TEXTJOIN`, `CONCAT`, `IFS`, `SWITCH`, `MAXIFS`, `MINIFS`) | `#NAME?` | Ajout automatique du préfixe `_xlfn.` |
| Fonctions à débordement (`XLOOKUP`, `XMATCH`, `FILTER`, `SORT`, `UNIQUE`, `SEQUENCE`) | Seule la cellule haut-gauche reçoit une valeur (pas de métadonnées de spill) ; non évaluables par le LibreOffice des runtimes courants | Interdites. Le tri, le filtrage et le dédoublonnage se font avant écriture. `INDEX`/`MATCH` pour les recherches |
| Nom d'onglet contenant une espace, non quoté | `#VALUE!` | Quotage automatique : `='Mes Hypothèses'!$B$2` |
| Valeur codée en dur dans une formule (`=B5*1.05`) | Hypothèse invisible | Recommandation dans la description de l'outil ; détection possible (constante numérique dans une formule) en avertissement |

Liste blanche de fonctions recommandée : `SUM`, `SUMIF`, `SUMIFS`, `AVERAGE`, `AVERAGEIFS`, `COUNT`, `COUNTA`, `COUNTIF`, `COUNTIFS`, `MIN`, `MAX`, `ROUND`, `IF`, `IFERROR`, `AND`, `OR`, `INDEX`, `MATCH`, `SUMPRODUCT`, `TODAY`, `DATE`, `YEAR`, `MONTH`, `TEXT`, plus les six fonctions préfixées ci-dessus.

### 7.2 Valeurs et structure

- **Pourcentages** : stocker 0,15 et non 15 (sinon 1 500 %).
- **Années** : en texte, sinon formatées avec un séparateur de milliers.
- **Dates avec fuseau horaire** : openpyxl les refuse (Excel ne gère pas les fuseaux). Conversion en heure locale naïve, avec un fuseau explicite (Europe/Paris par défaut).
- **Cellules fusionnées** : seule la cellule haut-gauche est écrivable ; les autres sont en lecture seule.
- **Noms d'onglets** : 31 caractères maximum, caractères `[ ] : * ? / \` interdits, unicité insensible à la casse.
- **Limites de feuille** : 1 048 576 lignes, 16 384 colonnes.
- **Formules sans valeur en cache** : un classeur produit par openpyxl et relu en `data_only=True` (par un autre outil de loom, par pandas…) donne `None` partout tant qu'il n'est pas recalculé.

---

## 8. Validation

### 8.1 Validation statique (systématique, sans dépendance)

Ce que l'outil peut garantir avant d'écrire :

- conformité de la spec au schéma (types, rôles, options) ;
- noms d'onglets valides et uniques ;
- références de cellules dans les bornes de la feuille ;
- onglets référencés dans les formules existants ;
- fonctions dans la liste blanche, syntaxe anglaise, parenthèses équilibrées ;
- cohérence valeur/type (une date dans une colonne `date`, une fraction plausible dans une colonne `pourcentage`).

Toute erreur revient au LLM sous forme d'erreur d'outil explicite, pour qu'il corrige sa spec.

### 8.2 Ce qu'elle ne garantit pas

- les erreurs d'évaluation (`#DIV/0!`, `#REF!` produit à l'exécution, `#VALUE!` sur des données réelles) ;
- la justesse des résultats (plage décalée, mauvaise colonne).

### 8.3 Recalcul optionnel par LibreOffice

Deux modes de déploiement possibles (décision n° 3) :

1. **Sur l'hôte** : `soffice` installé, activé par une option de l'outil ou de `loom.yaml`.
2. **Dans une microVM Firecracker** : LibreOffice présent dans l'image ext4, fichier transféré par vsock. C'est plus isolé, mais l'image est plus lourde.

Contraintes techniques à prévoir :

- **Démarrage lent** de LibreOffice (quelques secondes) : timeout configurable.
- **Exécutions parallèles** : LibreOffice verrouille son profil utilisateur. Deux recalculs simultanés avec le même profil entrent en conflit. Il faut un profil temporaire par appel (option `-env:UserInstallation=file:///…`). C'est indispensable en mode API HTTP multi-clients.
- **Fichier réécrit en place** : travailler sur une copie, ne remplacer la sortie qu'en cas de succès.
- **Résultat** : renvoyer au LLM le nombre de formules, le nombre d'erreurs et les cellules concernées, pour qu'il corrige.

---

## 9. Intégration avec les autres briques de loom_v2

### 9.1 Retour au LLM

Le résultat de l'outil contient le chemin du fichier et un résumé (onglets, nombre de lignes par onglet, formules, avertissements, rapport de recalcul le cas échéant), **jamais le contenu du classeur**. C'est cohérent avec l'objectif d'économie de tokens de la délégation par rôle.

### 9.2 Volume de données

Transmettre toutes les lignes dans les arguments de l'appel coûte des tokens et plafonne vite. Sources à prévoir :

- données en ligne (petits tableaux) ;
- fichier CSV ou JSON du répertoire de travail ;
- résultat d'un outil précédent ou clé du **state de session** (chantier du 14/10/2026), ce qui suppose que l'outil puisse lire le state.

### 9.3 Fichier de sortie

- Répertoire de sortie défini (dans `loom.yaml` ou relatif au dossier de l'agent), chemins résolus et **confinés** à ce répertoire (protection contre le path traversal).
- Politique d'écrasement : refus, suffixe automatique, ou **validation humaine** via le mécanisme existant de loom_v2.

### 9.4 Observabilité et rejeu

- La trace du run enregistre la **spec complète**, ce qui permet de régénérer le fichier.
- Un **hash** du fichier produit permet de vérifier le rejeu. Attention : un .xlsx est une archive zip qui contient des dates de création et de modification (`docProps/core.xml`) et des horodatages zip. Deux générations identiques donnent des hashs différents, sauf si l'outil fixe ces dates. Autre option : hacher la spec plutôt que le fichier.

### 9.5 Asynchronisme

openpyxl est synchrone et l'écriture d'un gros classeur peut prendre du temps. L'appel passe par `asyncio.to_thread` pour ne pas bloquer la boucle, surtout en mode API HTTP.

---

## 10. Décisions à prendre

1. **Nom** de l'outil et du module dans `tools_builtin`.
2. **Nom de l'extra** optionnel portant openpyxl (et pandas si utilisé).
3. **Recalcul** : absent, optionnel sur l'hôte, ou optionnel dans Firecracker.
4. **Sources de données** acceptées dans le premier lot (en ligne seul, CSV, state de session).
5. **Conventions françaises** : format de date figé ou adaptatif, négatifs avec signe moins ou entre parenthèses, zéro affiché en tiret ou non.
6. **Style maison** : figé dans le code ou surchargeable dans `loom.yaml`.
7. **Politique d'écrasement** du fichier de sortie.
8. **Rejeu** : hash du fichier (avec dates fixées) ou hash de la spec.
9. **Outil de lecture** xlsx dans le même lot ou plus tard.

---

## 11. Hors périmètre proposé pour un premier lot

- graphiques natifs ;
- mise en forme conditionnelle ;
- tableaux croisés dynamiques ;
- validation de données (listes déroulantes) ;
- macros (.xlsm) ;
- édition de classeurs existants, qui suppose de détecter et respecter leurs conventions ; c'est un chantier distinct.

Chacun de ces points peut entrer plus tard dans le vocabulaire déclaratif sans casser le schéma. Les cas qui dépassent l'outil restent possibles via l'exécution de code en sandbox (openpyxl dans l'image Firecracker), avec un rendu non garanti par le style maison.
