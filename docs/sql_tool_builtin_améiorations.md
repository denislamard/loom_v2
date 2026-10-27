# Outil `loom.sql` : points à améliorer

27 octobre 2026 · Denis Lamard

## En bref

Ce document fait suite aux essais de `loom.sql` (version 2.0.14) sur un vrai modèle, avec l'agent `examples/agent_sql` : l'agent `compta` (Marie), sur la base `data/db/clients.db`. Ces essais ont tous été faits le 27/10/2026. Ils portaient sur deux questions chiffrées : les encaissements de septembre 2026 et les factures en retard. Une troisième question, plus courte, demandait le top 5 des produits.

**Constat principal : aucun montant calculé par SQL n'était faux.** Tous les écarts relevés viennent de ce que le modèle écrit autour des requêtes. Il recalcule un chiffre de tête, il lit mal un tableau, il affirme quelque chose sans l'avoir vérifié, ou il choisit une hypothèse à la place de la personne.

**Principe retenu : retoucher le prompt à chaque cas ne suffit pas.** Le résultat ne doit être ni faux ni inventé, quel que soit le prompt. Les garanties doivent donc venir du moteur (guards, contrats) et de la base (les vues), pas de consignes.

## 1. Les essais

### 1.1 Configurations essayées

| Série | Configuration | Runs | Chiffres justes | Chaque chiffre vient d'une requête | Coût par run |
|---|---|---|---|---|---|
| SQL-106 | Marie (Haiku), seule | 4 | 4/4 | non : total partiel et part du total calculés de tête, classement inventé | 0,015 à 0,019 $ |
| SQL-107_1 à 107_4 | Marie (Haiku) et sous-agent `analyste` (Haiku) | 4 | 4/4, dont 1 rattrapé par un contrôle | 2/4 | 0,038 à 0,050 $ |
| SQL-107_5 à 107_7 | Marie (Haiku) et `analyste` (Sonnet) | 3 | 3/3 | 2/3 ; le cas restant est un ajout de Marie | 0,059 à 0,083 $ |
| SQL-108_1 à 108_3 | même configuration, factures en retard | 3 | justes, sauf un minimum mal lu (108_2) | 2/3 | 0,078 à 0,131 $ |
| SQL-002 | même configuration, top 5 des produits | 1 | justes | oui ; critère de classement choisi par Marie | 0,054 $ |

### 1.2 Ce qui a marché

- **Le sous-agent de calcul.** Son prompt donne la méthode : une CTE unique, des contrôles en colonnes de requête, le départage des ex æquo, et la formule de calcul d'une ligne.
- **Retirer des droits à Marie plutôt qu'ajouter des consignes.** Sans `document_lines` ni `v_document_totals`, elle ne peut plus recopier une formule à l'analyste.
- **Des contrôles faits en requête.** Au run 107_1, un écart d'un centime sur la TVA à 5,5 % a été repéré et corrigé avant la réponse.
- **Sonnet pour l'analyste.** Il applique la méthode du premier coup et ajoute de lui-même des contrôles de qualité : factures sans ligne, format des dates, ex æquo.

### 1.3 Familles d'erreurs observées

| Famille | Exemples | Run |
|---|---|---|
| **A. Chiffre inventé ou recalculé** | total du top 5 additionné de tête ; « environ 36 % » ; minimum de 99,00 € au lieu de 95,19 € ; centimes convertis de tête | 106, 107_2, 107_4 à 107_6, 108_2 |
| **B. Affirmation fausse sans chiffre** | « J'ai vérifié les additions… mais pas les montants directement dans la base » ; « je ne les ai pas comptées dans le détail » ; « une même facture peut comporter plusieurs taux » | 106_2, 106_4, 107_5, 108_3 |
| **C. Hypothèse choisie à la place de la personne** | « en retard » vaut `overdue` au run 1 et `overdue` ou `issued` échue au run 2 ; classement des produits par quantité, toutes unités confondues | 108_1, 108_2, SQL-002 |
| **D. Règle de calcul fausse dans une requête** | TVA arrondie après la somme et non ligne par ligne (−0,01 €), rattrapée par le contrôle | 107_1 |

Le maillon faible est le modèle qui **rédige** la réponse finale, Haiku dans ces essais. Il reformule une réponse déjà juste et y ajoute des erreurs.

## 2. Améliorations du moteur

### P-1. Guard de provenance des chiffres — priorité haute, candidat 2.0.15

**Le besoin.** Aucun chiffre de la réponse finale ne doit venir d'ailleurs que d'un résultat d'outil. C'est la famille A.

**Le principe.**
- Le guard est une politique `on_output`, déterministe : aucun modèle n'intervient.
- Il relève les nombres de la réponse finale et vérifie que chacun figure dans un résultat d'outil du run ou dans la demande de la personne.
- S'il en manque un, il demande une réparation (`Retry`) avec la liste des chiffres non sourcés.
- Ensuite, `on_failure` décide : `fail`, `unverified` ou `fallback`.

**Ce qui existe déjà.**
- Le relevé des nombres de `guards/fidelity.py` : nombres d'au moins trois chiffres, format français, décimales comprises.
- Le circuit `Retry` → `repair.max_attempts` → `on_failure` des contrats.

**Ce qui reste à trancher.**
- **La normalisation** : `36285.38`, `36 285,38 €` et `3628538` en centimes désignent-ils le même nombre ?
- **Les arrondis** : 36,5 doit-il passer face à 36,48 ? Je propose un réglage `match: exact | rounded`.
- **Le bruit** : les rangs, les années, les dates et les quantités reprises de la demande.
- **Les sous-agents** : pour l'agent appelant, la réponse du sous-agent est un résultat d'outil. Le sous-agent a besoin de son propre guard, appliqué à ses résultats SQL.
- **La portée** : le guard ne vérifie pas qu'un chiffre est attribué au bon élément (le bon montant au bon client), seulement qu'il existe.

**Ce qu'il aurait arrêté** : toutes les erreurs de la famille A observées dans ces essais.

### P-2. Mode terminal pour un sous-agent — priorité moyenne

**Le besoin.** Supprimer la reformulation par l'agent appelant, d'où viennent la plupart des erreurs A et B.

**Le principe.** Une option `terminal: true` sur une entrée `subagents` : la réponse du sous-agent devient telle quelle la réponse finale. Ça existe déjà pour un rôle (`RoleSpec.terminal`, dans `agents/spec.py`), mais pas pour un sous-agent.

**La contrepartie.** L'agent appelant ne peut plus rien ajouter de sa propre lecture. Dans le run 108_1, par exemple, Marie avait signalé d'elle-même trois factures `issued` échues.

### P-3. Remplir un classeur Excel depuis un résultat SQL, sans recopie — priorité moyenne

**Le besoin.** Au run 108_1, Marie a retapé 83 lignes de montants dans l'appel `excel`, et l'a fait deux fois après une erreur de dossier. Le résultat était exact, mais rien ne garantit qu'une recopie le soit, et elle coûte cher : environ 0,05 $ sur ce run.

**Ce qui manque.**
- `loom.excel` n'accepte pas la forme `{columns, rows}` que rend `loom.sql`.
- Une référence `{"$ref": "result:N"}` ne vise que les résultats du run courant, alors que les données sont dans le run du sous-agent.
- Une référence transmet le résultat entier, sans moyen d'en désigner une cellule.

**Les pistes.** Faire accepter à `excel` un résultat SQL passé par référence, ou donner `excel` au sous-agent qui a les données. Les deux sont compatibles.

### P-4. Recette « juge de fidélité » — priorité moyenne, sans code

**Le besoin.** Arrêter les affirmations fausses sans chiffre, la famille B, que le guard de provenance ne voit pas.

**Le principe.** Un `loom.judge` branché sur la réponse finale, avec un critère bloquant générique : « la réponse n'affirme rien que les résultats d'outils ne montrent et ne contredit aucun résultat ». Les résultats d'outils lui sont donnés en contexte.

**Les limites.** C'est un modèle : il réduit le risque, il ne le supprime pas. Il coûte un appel par réponse.

**À faire.** Un exemple dans la doc, et la vérification que le contexte du juge peut recevoir les résultats d'un sous-agent.

### P-5. Recette « définition annoncée » — priorité basse, sans code

**Le besoin.** Une hypothèse choisie à la place de la personne (famille C) n'est pas une erreur tant qu'elle est annoncée. Le risque, c'est qu'elle reste silencieuse.

**Le principe.** Un contrat de sortie (`loom.contract`), par exemple un motif `must_match`, qui exige qu'une réponse chiffrée énonce sa définition. La forme exacte reste à choisir.

## 3. Améliorations de `loom.sql`

### P-6. Les règles métier dans des vues — documentation

**Le besoin.** La famille D : une formule de calcul réécrite par le modèle, juste ou fausse selon le run.

**Le principe.** Les règles de calcul (montant d'une ligne, TVA par taux, impayé, encaissé, chiffre d'affaires par produit) vont dans des vues, écrites et vérifiées une fois par celui qui possède la base, puis exposées en lecture seule. Le modèle ne fait plus que filtrer et agréger des colonnes déjà justes.

**À faire.** Un paragraphe dans `docs/howto/06-outils-fournis.md`, section `loom.sql`, avec l'exemple d'une vue `v_line_amounts` (HT et TVA par ligne).

### P-7. Droits par colonne — priorité moyenne

**Le constat.** `documents: { select: never, update: always }` permet de modifier n'importe quelle colonne de `documents`, y compris `notes` ou `customer_id`. Or Marie ne devrait pouvoir changer que `status` et `paid_at`.

**Le principe.** Une liste de colonnes modifiables par table, contrôlée par l'analyse sqlglot et, sous SQLite, par l'authorizer, qui voit les écritures colonne par colonne.

### P-8. Un motif d'approbation qui montre la portée — priorité moyenne

**Le constat.** Quand une écriture attend une validation, la personne voit la requête, mais pas sa portée : le nombre d'instructions et les tables touchées.

**Le principe.** Le motif d'approbation affiche le nombre d'instructions, les tables et le plafond `max_affected_rows`.

### P-9. Les colonnes dans la description de l'outil — priorité basse, en option

**Le constat.** Quand Haiku envoie `schema` et la requête dans le même tour, il devine les noms de colonnes.

**Le principe.** Une option qui met les colonnes des tables déclarées dans la description de l'outil. On économise ainsi un appel `schema`, mais la description, et donc le prompt, devient plus longue.

### P-10. Un filtre de lignes — priorité basse

**Le besoin.** Restreindre un agent aux lignes d'un client, par exemple celui que désigne le state de session.

**Le principe.** Un prédicat par table, injecté de façon sûre, par une vue paramétrée ou une réécriture sqlglot. C'est à cadrer à part : l'injection doit résister aux sous-requêtes et aux jointures.

## 4. Récapitulatif

| Réf. | Point | Famille | Nature | Priorité |
|---|---|---|---|---|
| P-1 | Guard de provenance des chiffres | A | moteur, déterministe | haute |
| P-2 | Sous-agent `terminal` | A, B | moteur | moyenne |
| P-3 | Excel rempli depuis un résultat SQL | A | outils | moyenne |
| P-4 | Recette juge de fidélité | B | doc, existant | moyenne |
| P-5 | Recette définition annoncée | C | doc, existant | basse |
| P-6 | Règles métier dans des vues | D | doc | moyenne |
| P-7 | Droits par colonne | sécurité | `loom.sql` | moyenne |
| P-8 | Motif d'approbation avec la portée | sécurité | `loom.sql` | moyenne |
| P-9 | Colonnes dans la description | ergonomie | `loom.sql`, option | basse |
| P-10 | Filtre de lignes | sécurité | `loom.sql` | basse |

**Ce que le moteur peut garantir**, une fois P-1 et P-5 en place : aucun chiffre sans source, et aucune réponse chiffrée sans définition annoncée. `loom.sql` garantit déjà qu'aucun droit ne dépasse ceux déclarés.

**Ce que la base peut garantir**, avec P-6 : les règles de calcul.

**Ce qui restera seulement borné** : qu'une requête filtre bien ce qu'il faut, et qu'une phrase sans chiffre soit vraie. Les contrôles faits en requête et le juge (P-4) réduisent ce risque. Le journal permet, après coup, de remonter de chaque chiffre à sa requête.
