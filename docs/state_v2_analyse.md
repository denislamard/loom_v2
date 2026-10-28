# Analyse d'impact — state v2 : le modèle tient le state, loom fournit les valeurs

28 octobre 2026 · Denis Lamard

> Statut : **réalisé le 28/10/2026 dans 2.0.15 (non publiée)**, les 4 lots, avec les recommandations de §8 (S10 : version gardée à 2.0.15). Écart avec l'analyse : la syntaxe d'`extract` est `value` / `fields` / `columns` (champ ← colonne) avec `label: <champ>` — §2.3 et §11 la suivent. Ce que fait loom : `state-et-context.md` §4.5. Analyse d'origine : Porte sur le commit `f23a44c` (version `2.0.15` non publiée). Fait suite à l'essai réel `state-01` sur l'agent de test `/home/denis/temp/agent` (Marie, Haiku, base `crm`). Documents liés : `state-et-context.md` (le state tel qu'il est), `state-et-context-analyse.md` (l'option C), `sql_builtin_tool.md`.

## En bref

Le state actuel (option C, 2.0.6) est sain, mais ne sert pas un agent dont toutes les données passent par un outil générique (`loom.sql`) : aucune clé n'a pu être posée dans l'essai, et le state posé par l'application a contredit la conversation. La proposition — un outil fourni **`loom.state`** par lequel le modèle *désigne* un résultat d'outil, loom en *extrayant* les valeurs selon une règle déclarée sur la clé, plus un droit d'écriture par clé (**`writers`**) — **tient dans l'option C : aucun type d'événement nouveau**, les écritures voyagent dans `tool.completed.output.state` comme aujourd'hui. Un prototype sans aucune modification du moteur (outil Python ordinaire) pose `client = {id: 261, code: "C-261"}` depuis un résultat SQL simulé (essai X4).

| | Constat | Preuve |
| --- | --- | --- |
| **Bugs existants** | E1 : n'importe quel outil écrit n'importe quelle clé déclarée, contrairement à ce que dit la config (`sans source, seule l'application l'écrit`) | Démontré (X1) |
| | E2 : un outil peut effacer une clé `required` ; la session est ensuite **bloquée** (tout run refusé) tant que l'application ne la repose pas | Démontré (X2, X3) |
| | E3 : un `path` qui ne peut jamais rien donner sur `sql` (`/rows/0/code`) passe `loom validate` ; un `path` positionnel (`/rows/0/0`) produit un refus à chaque requête | Démontré (`loom validate`, essai `state-01`) |
| | E4 : les références `[result:N]` et leur consigne ne sont montrées au modèle **que si l'agent a un rôle ou un sous-agent**, alors qu'elles se résolvent pour tout outil | Lu (`executor.py` l. 505-514) ; X4 |
| **Points bloquants** de la proposition telle qu'esquissée le 28/10 | B1 : avec `{"$ref": …}`, rien n'empêche le modèle d'écrire les données lui-même : un client **inventé** (`C-999`) est posé | Démontré (X5) |
| | B2 : changer la consigne `STATE_HINT` fait diverger le corpus j7 | Démontré (X6) |
| | B3 : rendre `writers` strict par défaut casse 11 tests unitaires | Démontré (X7) |
| | B4 : le moteur ne peut pas importer `tools_builtin` (contrat import-linter) : il ne sait pas, seul, qu'une écriture vient du modèle | Lu (`pyproject.toml`) |
| **Coût** | Chaque changement de sujet coûte un appel de modèle de plus ; l'agent fait déjà 9 appels sur 10 permis pour un rapport | Mesuré (`rapport-04`) |

**Recommandation** : réaliser en 4 lots, en commençant par corriger E1 à E4 (lot 1), qui valent seuls ; `loom.state` prend un **numéro de résultat en texte** (`"result:3"`) et non un `$ref`, et le lit lui-même par un accès en lecture aux résultats du run (`ToolContext.results`) : c'est ce qui rend la fabrication impossible (B1). `writers` vaut par défaut `[app, tools]`, le comportement d'aujourd'hui (B3). La consigne du prompt système ne change pas d'un octet pour un agent sans clé écrite par le modèle (B2).

---

## 1. Le besoin, vu à l'essai `state-01`

Config essayée : `client_courant` (`^C-[0-9]{3,}$`, `from: [{tool: sql, path: /rows/0/0, on_missing: ignore}]`), `document_courant` (sans source), `main.context: [session_state]`.

| Run | Demande | Ce qui s'est passé |
| --- | --- | --- |
| 1 | « Montre-moi la fiche du client Léa Renard. » | 1re colonne du SELECT : `id` → `261` refusé (« doit être du texte »). Rien posé |
| 2 | « Quelles sont ses factures impayées ? » | 1re colonne : `number` → `INV-2024-0004` refusé (motif). Le modèle s'en sort par l'historique |
| 3 | « Et ses devis en cours ? » + `--state client_courant=C-002` | State posé, bloc envoyé ; Haiku interroge quand même `customer_id = 261` et demande confirmation. La session finit incohérente : state C-002, conversation C-261 |

Trois enseignements :

1. **Un pointeur positionnel ne peut pas servir sur un outil générique** : c'est le modèle qui choisit les colonnes et leur ordre (dans `rapport-02` à `-04`, 8 listes de colonnes différentes, avec 7 premières colonnes différentes : `id`, `number`, `numero`, `client`, `status`, `mois_echeance`, `n`).
2. **Seul le modèle sait de quoi parle la conversation**, mais il ne peut rien écrire. Toute valeur posée de l'extérieur se désaligne.
3. **Le repère utile est l'id interne** : toutes les requêtes filtrent sur `customer_id`, jamais sur le code.

## 2. Ce qui est proposé

### 2.1 Les clés de l'agent (validées le 28/10, « pour l'instant »)

| Clé | Valeur | Écrite par |
| --- | --- | --- |
| `client` | `{id, code}` | le modèle, depuis un résultat |
| `document` | `{id, number, type}` | le modèle, depuis un résultat |
| `demande` | `{id}` (quand `support_requests` existera) | le modèle, depuis un résultat |
| `selection` | `{ids: [...] ≤ 100, libelle}` | le modèle, depuis un résultat (une colonne) |
| `utilisateur` | `{signature, ton}` | l'application seule |

Le state ne porte que des repères ; noms, montants, dates restent en base. La clé `fichiers` (derniers fichiers produits) est **écartée du premier lot** : voir §6, effet S14.

### 2.2 Qui écrit : `writers`

Loom écrit toujours ; la demande vient de trois sources, qu'une clé autorise par `writers` :

| Valeur | Source | Existe |
| --- | --- | --- |
| `app` | `run(state=…)`, `--state` | oui |
| `tools` | sources `from:` de la clé, et `ToolOutput.state` d'un outil | oui |
| `model` | `loom.state` | **nouveau** |

Défaut : `[app, tools]`, le comportement actuel (décision S1).

### 2.3 Comment le modèle écrit : `loom.state`

```json
{"set": "client", "result": "result:3", "row": 0}
{"set": "selection", "result": "result:5", "libelle": "factures en retard au 28/10/2026"}
{"clear": "document"}
```

- `result` est le numéro que le modèle voit en tête de chaque résultat (`[result:3]`). L'outil lit ce résultat lui-même dans le run (`ToolContext.results`) : **le modèle ne transmet aucune donnée**, il ne peut ni recopier mal ni inventer (B1).
- La règle d'extraction est **déclarée sur la clé**, pas donnée par le modèle :

```yaml
state:
  client:
    description: Client dont parle la conversation. Pose-la dès qu'une recherche désigne un client ; efface-la quand on change de sujet.
    schema: {type: object, properties: {id: {type: integer}, code: {type: string, pattern: "^C-[0-9]{3,}$"}}, required: [id, code], additionalProperties: false}
    writers: [model]
    extract: {fields: {id: id, code: code}}      # champ du state ← colonne du résultat
  selection:
    description: Liste de travail en cours (ids de documents).
    schema: {type: object, properties: {ids: {type: array, items: {type: integer}, maxItems: 100}, libelle: {type: string, maxLength: 120}}, required: [ids]}
    writers: [model]
    extract: {columns: {ids: id}, label: libelle}  # tous les id du résultat ; libellé donné par le modèle
  utilisateur:
    description: Réglages de l'artisan.
    schema: {type: object, properties: {signature: {type: string}, ton: {enum: [cordial, ferme]}}}
    writers: [app]
```

- Formes de résultat lues : tableau `{columns, rows}` (`loom.sql`), liste d'objets, objet seul (`row` ignoré). Les autres (texte, image) : refus.
- Un refus de `loom.state` est un **résultat en erreur** qui nomme la clé et le motif (décision S4) : l'outil n'a pas d'autre effet que l'écriture, le modèle doit corriger.

## 3. État de référence

Clone du commit `f23a44c` (seul `.gitignore` diffère au poste), Python 3.14.8, extras `anthropic sql sqlite office`, sans Postgres, RabbitMQ ni Redis.

| Contrôle | Résultat |
| --- | --- |
| `pytest tests/unit tests/regression` | **2 926 passés, 251 sautés** |
| `pytest tests/regression` | 19 passés (dont j7, le corpus du state) |
| ruff, pyright, import-linter | non lancés (place disque insuffisante sur la VM d'essai) : **à relancer au poste avant le lot 1** |

Rien n'a été écrit dans `loom_v2` ni dans l'agent, hors ce document : les essais ont tourné sur des copies.

## 4. Bugs et défauts existants

Ils existent sans la proposition ; chacun a un correctif indépendant.

### E1 — Tout outil écrit toute clé déclarée

`StateRules.posed` (`engine/session_state.py` l. 256) fusionne `output.state` sans regarder si l'outil est une source de la clé ; seule la déclaration de la clé est contrôlée (`_problem`, l. 290). Or `StateKey.sources` dit : « sans source, seule l'application l'écrit » (`agents/spec.py` l. 905-906). `state-et-context.md` §4.2 laisse entendre l'inverse (« un outil peut aussi écrire lui-même »), sans condition.

Essai X1 : un outil `fiche`, cité dans aucun `from`, rend `state={"note_libre": …}` ; `loom state` montre la clé posée.

Risque : un outil tiers (paquet, source `forge`, outil forgé qui rendrait un `ToolOutput`) écrit une clé que l'auteur de l'agent croyait réservée à l'application — `signature`, un plafond, un identifiant d'utilisateur.

Correctif : `writers` (§2.2). Le défaut `[app, tools]` garde le comportement actuel ; le commentaire de `StateKey.sources` est corrigé. Un agent qui veut une clé réservée l'écrit `writers: [app]`.

### E2 — Un outil peut effacer une clé `required` et bloquer la session

`_problem` rend `None` pour une valeur `None` avant tout autre contrôle (l. 292-293) : l'effacement passe toujours. Une clé `required` effacée par un outil fait refuser **chaque run suivant** (« clé obligatoire sans valeur », code 2 en CLI, 422 en REST), y compris en REST et en MCP, qui ne peuvent pas reposer de state (D5) : la session est perdue pour ces accès.

Essais X2 puis X3 : `efface` rend `{"signature": None}`, le run réussit ; le run suivant est refusé.

Correctif : un outil (et le modèle) ne peut pas effacer une clé `required` : écriture refusée, motif « clé obligatoire : seule l'application la remplace ». ⚠ Changement de comportement, aucun test existant ne le couvre (à vérifier au lot 1).

### E3 — `from.path` sur un outil générique : accepté, inutile, bruyant

`_check_state` (`runtime/wiring.py` l. 1679-1723) vérifie que l'outil rend des données, pas que le chemin peut y mener. Sur `sql`, `/rows/0/code` ne mène jamais à rien (les lignes sont des listes) : avec `on_missing: ignore`, rien n'est posé, rien n'est dit. `/rows/0/0` mène à « la première colonne de la première ligne », quelle qu'elle soit : refus à chaque requête, en `WARNING` au log et en ligne `[state]` lue par le modèle (essai `state-01`, runs 1 et 2).

Correctif proposé : `loom validate` et le montage **avertissent** (sans refuser) d'un `from` sur `loom.sql`, en renvoyant vers `loom.state`. Pas de vérification générale des chemins : la forme des données d'un outil n'est connue que de lui.

### E4 — Références montrées seulement avec un rôle ou un sous-agent

`shows_refs` (`engine/executor.py` l. 505-514) ne marque les résultats `[result:N]` et n'ajoute `REFS_HINT` au système que si un rôle ou un sous-agent est proposé. La résolution, elle, marche pour tout outil (`executor.py` l. 839 ; essai X4, où le modèle simulé n'en a pas besoin). Un agent avec `loom.state` mais sans rôle ni sous-agent aurait un outil qu'il ne peut pas appeler correctement.

Correctif : `shows_refs` vrai aussi quand `loom.state` est proposé. Sans effet sur les agents existants (il faut que l'outil soit déclaré).

### Hors loom — l'agent de test

`agent.yaml` accorde `select, insert, update` sur `support_requests`, que `db/schema.sql` ne crée pas. `loom.sql` le tolère (simple avertissement au montage, `postgres.py` l. 304-308). À créer avant la clé `demande`.

## 5. Points bloquants de l'esquisse du 28/10, et leur levée

### B1 — `$ref` ne garantit pas que la valeur vient d'un résultat

Les arguments sont résolus **avant** l'outil (`executor.py` l. 839) : l'outil reçoit des données, sans savoir d'où. Essai X5 : le modèle écrit `{"resultat": {"columns": ["id","code"], "rows": [[999,"C-999"]]}}` en clair ; le client inexistant `C-999` est posé. Le journal le sait (`tool.called.refs` vide), l'outil non.

Levée : `loom.state` prend le numéro en texte (`"result": "result:3"`) et le lit par un nouvel accès en lecture, `ToolContext.results` (un protocole de `core/ports`, servi par le `ResultIndex` du lot, déjà construit pour la résolution). Pas de donnée dans les arguments ; un numéro inconnu, un résultat en erreur ou pas encore arrivé (même tour) donnent l'erreur de `ResultIndex` (« Références disponibles : … »). Avantage annexe : un résultat SQL de 200 lignes ne repasse pas par la validation des arguments.

### B2 — La consigne du système fait partie de l'empreinte des requêtes

Essai X6 : changer une phrase de `STATE_HINT` (`session_state.py` l. 62-67) fait diverger les trois journaux de j7 dès le premier appel (« ce qui diffère : le prompt système »).

Levée : `STATE_HINT` ne change pas. Un paragraphe s'y **ajoute** seulement si une clé montrée a `model` dans ses `writers` (« Tiens toi-même à jour les clés … avec l'outil `state` … »). Les agents existants gardent des requêtes identiques à l'octet.

### B3 — `writers` strict par défaut casse 11 tests

Essai X7 : refuser `ToolOutput.state` d'un outil qui n'est pas source de la clé fait tomber 11 tests (10 dans `test_executor.py`, 1 dans `test_idempotence.py`), qui écrivent par `ToolOutput.state` sans `from`. La suite j7 passe.

Levée : défaut `[app, tools]`. La rigueur se demande clé par clé.

### B4 — Le moteur ne connaît pas `loom.state`

`loom_ia.engine` ne peut pas importer `loom_ia.tools_builtin` (contrat « Moteur indépendant… »). Pour attribuer une écriture au modèle, `StateRules` reçoit du montage (`runtime/wiring.py`, qui a le droit d'importer les deux) **le nom sous lequel `loom.state` est monté**. `posed` classe alors l'écriture : `model` si l'outil est celui-là, `tools` sinon. Un outil utilisateur qui s'appellerait `state` est déjà refusé en double par `_check_names`.

## 6. Effets de bord

| # | Domaine | Effet | Traitement proposé |
| --- | --- | --- | --- |
| S1 | Coût, durée | Poser une clé = un appel d'outil = **un appel de modèle de plus** (préfixe en cache : environ 0,0005 $ et 1 à 2 s avec Haiku, d'après `state-01`) | Consigne : poser le state **dans le même lot** que l'appel suivant, quand il y en a un. À mesurer au lot 4 |
| S2 | Itérations | `rapport-04` : 9 appels de modèle sur `max_iterations: 10` ; un rapport qui pose `selection` peut atteindre le plafond, puis la réponse forcée | Relever `max_iterations` de l'agent (12 à 15) ; à dire dans le guide |
| S3 | Références | Seuls les résultats du **run courant** sont adressables (`refs.py` l. 8-11) : on ne peut pas poser `client` depuis la recherche d'un run précédent | Accepté : poser dans le run qui trouve. À dire dans la description de l'outil |
| S4 | Durabilité (D2) | Les écritures du modèle ne restent que si le run **réussit** ; un run en échec (budget, délai) perd aussi celles de `loom.state` | Accepté, cohérent avec l'historique |
| S5 | Sous-runs (D3) | Dans un sous-run, les écritures sont retirées **sans bruit** (`executor.py` l. 1041-1044) : le modèle croirait avoir posé | `ToolContext` dit si le run garde le state ; `loom.state` rend une erreur sinon |
| S6 | Refus | Les refus de `posed` (taille totale du lot, D7) restent des « succès + ligne `[state]` » (D8), alors que les refus propres à `loom.state` sont des erreurs (S4 de §8) | `loom.state` contrôle schéma et taille de valeur lui-même (erreur) ; seule la taille totale, qui dépend des voisins du lot, reste en D8. À documenter |
| S7 | Résultats tronqués | `sql` rend 200 lignes au plus (`truncated: true` au-delà) : une `selection` extraite serait **incomplète sans le dire** | Extraction d'une colonne refusée sur un résultat tronqué ; `row` sur un résultat tronqué accepté |
| S8 | Résultats déportés | Un gros résultat est déporté dans les artefacts ; `ResultIndex.content` le relit | Rien à faire ; refus clair sans stockage d'artefacts. Test à écrire |
| S9 | Données | Colonnes en double (`SELECT d.id, c.id`), `NULL`, `numeric` rendu en nombre à virgule | Colonne en double : refus qui le dit ; `NULL` et types : le schéma de la clé tranche |
| S10 | Tailles (D7) | `selection` : 100 ids de 4 chiffres ≈ 500 octets, de 7 chiffres ≈ 800 ; plafond par valeur 1 024, total 4 096 | `maxItems: 100` dans le schéma de l'agent ; relever `max_value_bytes` si besoin (déjà possible) |
| S11 | Application contre modèle | Avec `writers: [app, model]`, la dernière écriture gagne ; le modèle peut défaire ce que l'application vient de poser (cas du run 3) | Décision S8 (§8) |
| S12 | Sessions anciennes | Une valeur écrite avant `writers` par une source désormais interdite reste dans le state (la projection ne refait pas les contrôles d'écriture) | Accepté ; `visible` continue d'écarter ce qui ne tient plus le schéma |
| S13 | Rejeu, évals, banc | Rejeu identique : le résultat de `loom.state` est servi du journal, state compris (`_as_served`). Variante : `loom.state` exécuté relit les résultats rejoués, déterministe. Évals : `expect.state` inchangé. Banc hors moteur : `ToolContext.results` vide → erreur | Test de rejeu et de variante au lot 3 ; limite du banc documentée |
| S14 | Clé `fichiers` | `from` sur `local_files` (`path: /path`) serait posé aussi par `read` et `list`, qui rendent un `path` ; une liste « derniers fichiers » demanderait un ajout, que le state ne connaît pas (il remplace) | Écartée du lot 1 ; à rediscuter (condition sur `written`, ou extraction par `loom.state`) |
| S15 | Requêtes | Un agent qui déclare `loom.state` reçoit sa définition (≈ 250 tokens, à mesurer), `REFS_HINT` (E4) et le paragraphe de consigne | Aucun effet sur les agents sans `loom.state` |
| S16 | Journal | Aucun type d'événement ni champ nouveau : un loom 2.0.14 relit une session v2 | Vérifié par construction (champ `output.state` existant) |
| S17 | Config | `writers` et `extract` sont refusés par un loom plus ancien (`extra="forbid"`) | Normal pour une config ; dit au CHANGELOG |
| S18 | Idempotence, approbation | `loom.state` est sans effet externe | `side_effects: none`, `idempotent: true`, pas d'approbation par défaut |
| S19 | Accès | REST et MCP ne posent toujours pas de state applicatif (D5) ; les écritures du modèle y fonctionnent (c'est un outil) | Rien |
| S20 | Sécurité | Les valeurs sont des repères, dans un champ de contenu (masqué, scellé, effacé avec la session) | Rien de nouveau |

## 7. Fichiers touchés

| Fichier | Changement | Lot |
| --- | --- | --- |
| `engine/session_state.py` | `StateKeyRule.writers` ; `posed` classe l'écriture (`tools` / `model`) et refuse hors `writers` ; refus d'effacer une clé `required` (E2) ; `opening` refuse une clé sans `app` ; paragraphe de consigne si clé `model` | 1, 3 |
| `agents/spec.py` | `StateKey.writers` (défaut `[app, tools]`), `StateKey.extract` (`value`, `fields` ou `columns`, `label`) ; contrôles : `extract` exige `model` dans `writers`, `model` exige `loom.state` dans les outils et `main.context` | 1, 3 |
| `runtime/wiring.py` | `state_rules` passe `writers`, `extract` et le nom de `loom.state` ; avertissement `from` sur `sql` (E3) | 1, 3 |
| `core/ports/tool.py` | `ToolContext.results` (protocole de lecture), `ToolContext.keeps_state` | 2 |
| `engine/executor.py` | Remplit `results` et `keeps_state` ; `shows_refs` avec `loom.state` (E4) | 2 |
| `core/state_extract.py` (nouveau, pur) | Extraction : `{columns, rows}`, liste d'objets, objet ; `value` / `fields` / `columns` ; refus (tronqué, colonne absente ou en double, ligne hors bornes) | 2 |
| `tools_builtin/state.py`, `state_params.py` (nouveaux), `tools_builtin/__init__.py` | L'outil `loom.state` ; description générée d'après les clés `model` | 3 |
| `access/cli` (`validate`) | Ligne par clé : écrivains, extraction | 3 |
| `tests/unit/…` | E1, E2, E3, E4 ; extraction ; `loom.state` (succès, chaque refus, sous-run, rejeu, variante, déport, tronqué) ; non-régression des 11 tests de X7 | 1-3 |
| `tests/regression/` | Nouveau corpus (modèles simulés) : session à plusieurs runs, `client` puis `selection` | 4 |
| `docs/` | `state-et-context.md` (§3, §4, §8), `howto/05-state-de-session.md`, `howto/06-outils-fournis.md`, `fonctions.md`, `configuration.md`, `CHANGELOG.md` ; site de formation | 4 |

## 8. Décisions à prendre

| # | Question | Recommandation |
| --- | --- | --- |
| S1 | Défaut de `writers` | `[app, tools]` (comportement actuel, 0 test cassé) |
| S2 | Désigner un résultat : `$ref` ou numéro en texte | Numéro en texte + `ToolContext.results` (B1) |
| S3 | Où déclarer l'extraction | Sur la clé (`extract`) : une seule source de vérité, lue aussi par `validate` |
| S4 | Refus de `loom.state` | Résultat en erreur (exception à D8, justifiée : pas d'autre effet) |
| S5 | Effacer une clé `required` | Interdit aux outils et au modèle (E2) — ⚠ comportement |
| S6 | Consigne du système | `STATE_HINT` inchangé ; paragraphe ajouté seulement avec une clé `model` (B2) |
| S7 | Résultat tronqué | Refus pour `column`, accepté pour `row` |
| S8 | Application et modèle sur une même clé | La dernière écriture gagne (simple, prévisible) ; à revoir si l'essai réel montre des retours en arrière |
| S9 | `nom` dans `client` | Non : `{id, code}` ; le nom se relit en base |
| S10 | Version | 2.0.15 (non publiée, déjà porteuse de ⚠) ou 2.0.16 : **à toi de choisir** |

## 9. Non-régression

- Avant chaque lot : `pytest tests/unit tests/regression` (référence : 2 926 / 251), ruff, pyright `src`, `lint-imports` (5 contrats).
- j1 à j7 doivent se rejouer à l'identique à chaque lot (garantis par S1 et S6).
- Les 11 tests de X7 doivent passer **sans modification** (S1).
- Ajouter : un journal de session v2 relu par la projection de 2.0.14 (copie du module) — rien ne doit changer pour elle.
- Essai réel en fin de lot 4 sur l'agent de test, mêmes demandes que `state-01`, plus « relance-les » après une sélection : mesurer appels de modèle, coût, et vérifier `loom state` à chaque étape.

## 10. Réalisation par lots

1. **Socle et corrections** : `writers` (défaut actuel), E1 (commentaire et doc), E2, E3 (avertissement), tests.
2. **Lecture des résultats** : module d'extraction pur, `ToolContext.results` et `keeps_state`, E4.
3. **`loom.state`** : outil, paramètres, description, paragraphe de consigne, `validate`, tests de rejeu et de variante.
4. **Documentation, corpus et essai réel** : guides, CHANGELOG, corpus de régression, config de l'agent de test (§11).

## 11. Config cible de l'agent de test (lot 4)

```yaml
max_iterations: 14

main:
  model: HAIKU
  system_file: system.md
  context: [session_state]

tools:
- python: loom.state
# … outils actuels inchangés

state:
  client:
    description: Client dont parle la conversation. Pose-la dès qu'une recherche désigne un seul client ; efface-la quand on change de sujet.
    schema: {type: object, properties: {id: {type: integer}, code: {type: string, pattern: "^C-[0-9]{3,}$"}}, required: [id, code], additionalProperties: false}
    writers: [model, app]
    extract: {fields: {id: id, code: code}}
  document:
    description: Devis ou facture dont parle la conversation.
    schema: {type: object, properties: {id: {type: integer}, number: {type: string, pattern: "^(Q|INV)-[0-9]{4}-[0-9]{4}$"}, type: {enum: [quote, invoice]}}, required: [id, number, type], additionalProperties: false}
    writers: [model]
    extract: {fields: {id: id, number: number, type: type}}
  selection:
    description: Liste de travail en cours (ids de documents), avec son libellé.
    schema: {type: object, properties: {ids: {type: array, items: {type: integer}, maxItems: 100}, libelle: {type: string, maxLength: 120}}, required: [ids]}
    writers: [model]
    extract: {columns: {ids: id}, label: libelle}
  utilisateur:
    description: Réglages de l'artisan pour les messages aux clients.
    schema: {type: object, properties: {signature: {type: string, maxLength: 80}, ton: {enum: [cordial, ferme]}}}
    writers: [app]
```

La clé `demande` s'ajoute quand `support_requests` existera.

## Annexe — essais

Copie du dépôt (`f23a44c`), projet d'essai séparé, modèle simulé (`sdk: fake`), outils Python ad hoc.

| # | Essai | Résultat |
| --- | --- | --- |
| X1 | Outil hors de tout `from` qui rend `state={"note_libre": …}` | Clé posée |
| X2 | Outil qui rend `state={"signature": None}` (clé `required`) | Run réussi, `signature` effacée |
| X3 | Run suivant dans la même session | `Demande refusée : State refusé : signature : clé obligatoire sans valeur` |
| X4 | `requete` (rend `{columns, rows}`), puis outil prototype `poser_client(resultat={"$ref": "result:1"})` | `client = {"id": 261, "code": "C-261"}` ; `tool.called.refs = ["result:1"]` |
| X5 | Même outil, données écrites en clair par le modèle | `client = {"id": 999, "code": "C-999"}` ; `refs = []` |
| X6 | Une phrase changée dans `STATE_HINT` | j7 diverge (3 journaux, « le prompt système ») ; 18 autres passent |
| X7 | `posed` refuse `ToolOutput.state` hors `from` | 11 échecs (`test_executor.py` ×10, `test_idempotence.py` ×1) sur 2 926 |
| — | `loom validate` avec `from: [{tool: sql, path: /rows/0/code}]` | « 1 agent(s) monté(s) sans erreur » |
