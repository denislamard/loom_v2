# Analyse d'impact — fin de run sur une réponse écrite avec `loom.state` (M1)

28 octobre 2026 · Denis Lamard

> Statut : **réalisé le 28/10/2026 (2.0.15, non publiée)**, option A, décisions M-D1 à M-D5 telles que recommandées ; test de variante ajouté (`test_state_v2.py`). Analyse d'origine : Base : commit `0582947` plus le durcissement du state non commité (`match`, `clears`, formes de `result` — 19 fichiers). Un prototype a tourné sur une copie hors du dépôt (annexe A). Documents liés : `state_v2_analyse.md`, `state-et-context.md` §4.5, `fonctions.md` (#13 outil terminal).

## En bref

**Le défaut.** Quand le modèle écrit sa réponse finale **et**, dans la même réponse, appelle `state`, le moteur exécute l'appel puis rappelle le modèle, comme après tout outil. Le modèle, qui a déjà tout dit, écrit une seconde réponse, et **c'est elle qui devient la réponse du run** : la bonne est perdue pour l'utilisateur (essai réel t9, run 3). Le défaut n'existe que parce que `loom.state` est un appel de tenue de dossier, que le modèle place naturellement en fin de réponse ; plus il suit la consigne « pose avant de répondre », plus le cas revient.

**La correction proposée (option A).** Après le lot d'outils, si la réponse qui l'a demandé contient du texte, que **tous** ses appels sont des appels à `loom.state`, et que **tous** ont réussi sans écriture refusée, le run se termine sur ce texte — par le chemin de toute réponse finale (`_final` : politiques `on_output`, contrats, juges, réparation). Sinon, rien ne change : le modèle est rappelé.

| | Résultat |
| --- | --- |
| Périmètre | Seuls les agents qui déclarent `loom.state` peuvent changer de comportement ; pour les autres, la règle ne s'applique jamais (prouvé : condition `model_tool is not None`, test dédié, corpus j1 à j7 identiques) |
| Prototype | 4 points dans `engine/loop.py` (+53 lignes), 1 dans `core/projections/history.py` (+12) |
| Non-régression | **3 007 passés** sur la copie (2 996 de référence + 11 nouveaux), mêmes 14 échecs d'environnement (`test_telemetrie`, `test_dependances`, identiques sans le changement) ; corpus j1 à j7 et `j7_state_v2` rejoués à l'identique, nouveau cas `reponse` compris |
| Preuve du défaut | 4 des 11 nouveaux tests échouent sur le code actuel (`ScriptExhausted` : le moteur rappelle le modèle une 3ᵉ fois) |
| Journal | Aucun type d'événement ni champ nouveau ; un loom 2.0.14 relit ces sessions (rendu de l'historique différent, mais valide, §6) |
| Gain annexe | Un appel de modèle de moins à chaque fois que le cas se produit |

**Ce qu'il faut décider** (§9) : l'option (A recommandée), la phrase à ajouter à la description de l'outil, l'absence de réglage pour désactiver la règle, et l'acceptation de deux changements de comportement mineurs (§7, R2 et R3).

---

## 1. Le constat

### 1.1 L'essai t9, run 3

Demande : « Parmi les factures de la sélection, combien concernent des clients de Dijon ? », après une compaction.

| Seq | Événement | Contenu |
| --- | --- | --- |
| 96-101 | `sql` | `WHERE d.id = ANY($1)` avec les 26 ids de `selection` : 7 lignes |
| 106 | réponse du modèle | **texte** : « Sur les 26 factures de la sélection, **6 concernent des clients situés à Dijon** même. Si l'on inclut Plombières-lès-Dijon… » + tableau ; **et** appel `state` `{"clear": "client"}` |
| 111 | `state` | succès : `client` effacé |
| 116 | réponse du modèle (rappelé) | « La sélection … contient toujours les 26 factures … Que souhaitez-vous faire ? » |
| — | `run.completed` | **sortie = le texte de la seq 116** |

Le state a fait exactement ce qu'il fallait (lecture de `selection.ids`, effacement de `client`) ; c'est l'enchaînement du moteur qui perd la réponse.

### 1.2 Fréquence mesurée

Sur les journaux de l'agent de test (`rapport-02` à `t9`) :

| Mesure | Valeur |
| --- | --- |
| Réponses du modèle qui appellent un outil | 97 |
| … dont avec du texte | 2 (un récit avant `sql` en t3 ; la réponse finale avant `state` en t9) |
| Réponses qui appellent `state` | 15 |
| … sans texte (le modèle pose, puis répond au tour suivant) | 14 |
| … avec du texte | 1 — c'était **la réponse finale** (t9) |
| … avec un texte d'annonce (« Je note… ») | **0** |

Haiku écrit rarement du texte en appelant un outil ; quand il le fait avec `state`, c'est sa réponse. Le risque inverse — une annonce prise pour la réponse (§7, R1) — n'a jamais été observé, mais l'échantillon est petit et ne couvre qu'un modèle.

## 2. Ce que fait le moteur aujourd'hui

La boucle est une machine à états (`engine/loop.py`). Après un lot d'outils, `_decide` (l. 1224) prend la branche `AWAITING_TOOLS if not state.pending_calls` (l. 1265) :

1. `_terminal(state, ctx)` (l. 1558) : un **outil terminal** (#13), seul dans son tour et sans erreur, rend sa sortie comme réponse finale, par `_final(…, terminal)` ;
2. sinon, transition vers `_after_tools` (l. 1531) : `READY_FOR_MODEL`, ou `FINALIZING` si `max_iterations` est atteint (réponse forcée sans outils).

La réponse finale passe toujours par `_final` (l. 1371) : politiques `on_output` (contrats de sortie, juges, politiques de l'agent), puis clôture (`run.transitioned` vers `COMPLETED`, puis `run.completed`), ou réparation (`_repair`, l. 1473), ou échec.

Le précédent utile est l'**outil terminal** : un run peut déjà se terminer sur un résultat d'outil, sans rappeler le modèle, et tout le moteur le sait (reprise, historique, diffusion, rejeu). La correction réutilise ce chemin, avec une sortie qui vient du texte du modèle au lieu du résultat de l'outil.

## 3. Les options

| | Option | Ce que voit l'utilisateur en t9 | Avantages | Inconvénients |
| --- | --- | --- | --- | --- |
| **A** | **Fin immédiate** sur le texte du tour (recommandée) | La bonne réponse | Déterministe ; un appel de modèle en moins ; réutilise `_final` et le précédent terminal | Une annonce (« Je note la sélection. ») serait prise pour la réponse (R1) |
| B | Rappeler le modèle, puis **concaténer** les deux textes | Bonne réponse + « Que souhaitez-vous faire ? » | Rien n'est perdu | Réponses doublées ou contradictoires ; un appel de plus ; la réponse finale contrôlée (contrat JSON) ne serait plus celle d'un seul message |
| C | Rappeler le modèle avec une consigne (« ton texte a déjà été transmis, ne le répète pas ») puis concaténer | Variable | Le modèle peut compléter | Dépend de la discipline du modèle (celle qui a déjà fait défaut) ; requête modifiée ; un appel de plus |
| D | Seulement une consigne dans la description de `state` (« n'écris pas ta réponse dans le même tour ») | Variable | Aucun code moteur | Haiku a ignoré plusieurs consignes nettes pendant les essais t2 à t8 |
| E | Généraliser A à tout outil marqué « de tenue de dossier » (`ToolSpec`) | — | Réutilisable | Champ de `ToolSpec` nouveau, sérialisé dans les requêtes ; aucun autre outil n'en a besoin aujourd'hui : à garder pour plus tard |

**Recommandation : A**, avec une phrase dans la description de `state` qui rend la règle explicite pour le modèle (§4.3) — ce qui réduit R1 à un usage contraire à la consigne.

## 4. La règle précise

### 4.1 Conditions (toutes nécessaires)

À la fin d'un lot (`AWAITING_TOOLS`, plus aucun appel en attente), **après** la règle de l'outil terminal :

1. l'agent a `loom.state` monté (`ctx.tools.state_rules.model_tool` n'est pas `None`) ;
2. la dernière réponse de l'assistant a des appels, et **tous** s'adressent à `loom.state` (par son nom monté) ;
3. son texte, espaces retirés, n'est pas vide ;
4. chacun de ces appels a un résultat, qui n'est **ni en erreur, ni porteur d'une écriture refusée** (`state_refused`, par exemple la taille totale du state, contrôlée par le moteur après l'outil).

Alors la réponse finale est **ce message sans ses blocs d'appel** (texte, et raisonnement s'il y en a, comme toute réponse finale), et elle passe par `_final(state, ctx, scope, cause, réponse, None)` — `source: "model"` pour les politiques `on_output`.

Sinon (texte vide, autre outil dans le tour, appel en erreur, écriture refusée), le modèle est rappelé comme aujourd'hui : il doit lire le refus, ou il n'a encore rien répondu.

### 4.2 Reprise après un plantage

`_final` écrit d'abord `run.transitioned` vers `COMPLETED`, puis `run.completed`, chacun par son propre `append`. Un plantage entre les deux laisse un run `COMPLETED` dont le dernier message est un résultat d'outil. La branche de reprise (l. 1240) suppose aujourd'hui que c'est un outil terminal (« Seul un outil terminal mène à COMPLETED sur un résultat d'outil ») et lève `RuntimeError` si la cause n'est pas un `tool.completed` (`_terminal_event`). Elle doit désormais trancher : outil terminal → comme avant ; sinon, si les conditions du §4.1 tiennent → `run.completed` avec la réponse (ou celle qu'une politique `on_output` a déjà remplacée, `state.replaced_output`) et sa donnée structurée. La décision ne dépend que du journal et de la config : elle est la même à la reprise qu'en direct.

### 4.3 Ce que le modèle en sait

Phrase ajoutée à la description de `state` (outil des seuls agents qui l'ont) :

> Si ta réponse n'appelle que state, son texte est ta réponse finale : le run s'arrête après l'écriture. Écris donc ta réponse dans ce même tour, ou n'écris rien et réponds au tour suivant.

Elle aligne le modèle sur la règle et réduit R1. Elle change la requête des agents qui ont `loom.state` (corpus `j7_state_v2` à réenregistrer), pas celle des autres.

## 5. Les changements, fichier par fichier

| Fichier | Changement | Prototype |
| --- | --- | --- |
| `engine/loop.py`, `_decide`, branche `AWAITING_TOOLS` (l. 1265) | Après `_terminal`, `answer = _answered_with_state(state, ctx)` ; si elle existe : `return await _final(state, ctx, scope, cause, answer, None)` | ✓ |
| `engine/loop.py`, `_decide`, branche de reprise `COMPLETED` sur résultat d'outil (l. 1240) | Outil terminal d'abord (inchangé) ; sinon réponse du §4.1 → `_completed(state, state.replaced_output or answer, data=…)` | ✓ |
| `engine/loop.py`, nouveau `_answered_with_state` | Les quatre conditions du §4.1 ; rend le message sans ses `ToolCallBlock` | ✓ |
| `engine/loop.py`, `_model_step`, diffusion `after_guards` (l. 1849) | Le texte d'une réponse dont **tous** les appels sont `state` ne part pas tout de suite : il partira à la clôture (`_release`), une seule fois | ✓ |
| `core/projections/history.py`, `_conversation` (l. 155) | Run terminé sur un résultat d'outil sans outil terminal : le texte quitte le tour qui appelait `state`, et la réponse retenue suit, comme une sortie terminale (`… assistant[appels], tool[résultats], assistant[réponse]`). Une réponse remplacée par une politique y figure sous sa forme retenue, comme toute réponse finale | ✓ |
| `tools_builtin/state.py`, `_description` | Phrase du §4.3 | à faire |
| `engine/loop.py`, docstring du module ; commentaire l. 1241 | Ajouter la règle M1 ; corriger « Seul un outil terminal mène à COMPLETED sur un résultat d'outil » | à faire |
| Tests | 11 tests moteur (annexe A) ; cas `reponse` du corpus `j7_state_v2` | ✓ (copie) |
| Docs | `state-et-context.md` §4.5, `fonctions.md` (#13 et F8), `conception.md` (boucle), guide 31.6, `CHANGELOG` | à faire |

Rien d'autre ne change : ni les événements, ni la projection de l'état d'un run (`run.completed` avec `output` existe déjà pour toute réponse finale), ni l'exécuteur, ni les adaptateurs, ni l'API.

## 6. Effets de bord, un par un

| # | Domaine | Effet | Vérifié par |
| --- | --- | --- | --- |
| S1 | Agents sans `loom.state` | Aucun : la condition 1 est fausse, la branche n'est jamais prise ; requêtes identiques à l'octet | Test « run sans loom.state » ; j1 à j7 identiques |
| S2 | Diffusion `live` | Le texte part au fil de l'eau pendant la réponse, comme aujourd'hui ; plus de second texte ensuite | Lecture (`_model_step` l. 1730) |
| S3 | Diffusion `after_guards` | Le texte part **une fois**, après ses contrôles, à la clôture. Sans la correction du §5, il partait deux fois (tôt, puis par `_release`) | Test dédié |
| S4 | Contrats de sortie, juges, politiques `on_output` | Appliqués au texte du tour, comme à toute réponse finale (`_final`) | Test « contrat » : réponse non conforme → réparation → réponse JSON retenue, `output_data` rempli |
| S5 | Réparation (`Retry` en `on_output`) | `_repair` part d'`AWAITING_TOOLS`, comme pour une sortie terminale refusée ; le tour réparé et ses résultats quittent l'historique, et **ses écritures du state sont retirées de la session** (D2, `repaired`) | Test « contrat » : `session_state == {}` après la réparation |
| S6 | Politiques `after_model` | Elles ont vu ce message quand il demandait l'outil ; `on_output` le voit ensuite comme réponse finale. Aujourd'hui, `on_output` voyait la réponse suivante | Lecture ; à dire dans la doc |
| S7 | `max_iterations` | Le lot fini, la fin du §4.1 passe **avant** le contrôle du plafond : un run au plafond se termine sur la bonne réponse au lieu d'une réponse forcée (`FINALIZING`) | Lecture (`_after_tools` n'est plus atteint) |
| S8 | Itérations, coût, budgets | Un appel de modèle de moins ; `run.completed.iterations` plus petit d'un | Test (2 appels au lieu de 3) |
| S9 | Approbation de `state` (un client peut l'imposer) | Le run se met en pause, puis reprend en `AWAITING_TOOLS` : la règle s'applique après l'approbation ; un refus est un résultat en erreur → modèle rappelé | Lecture (`_resumed`) |
| S10 | Politique `before_tool` qui refuse ou remplace un appel `state` | Résultat en erreur → modèle rappelé (condition 4) | Lecture |
| S11 | Écriture refusée par le moteur (taille totale) | Résultat réussi mais `state_refused` → modèle rappelé, pour qu'il lise le refus | Test dédié |
| S12 | Outil terminal dans le même tour que `state` | `_terminal` rend la main (plusieurs appels) ; la condition 2 est fausse → tour ordinaire | Lecture |
| S13 | `state` avec un autre outil | Tour ordinaire | Test dédié |
| S14 | Sous-run | `state` y est refusé (pas de state) → résultat en erreur → tour ordinaire | Lecture |
| S15 | Reprise entre `run.transitioned` et `run.completed` | Clôture réécrite à l'identique | Test « reprise » |
| S16 | Reprise d'un appel `state` interrompu | `state` est sans effet et idempotent : relancé, puis la règle s'applique | Lecture (`safe_to_retry`) |
| S17 | Rejeu identique, journaux nouveaux | Identique (décision pure du journal et de la config) | Cas `reponse` du corpus |
| S18 | Rejeu identique, **journaux anciens** qui contiennent le cas (t9 run 3) | Divergence attendue : le moteur s'arrête là où le journal a un appel de modèle de plus | Lecture ; à dire au `CHANGELOG` |
| S19 | Variante, évals, banc | Même moteur, même règle ; en variante, `state` est relancé (sans effet) et la décision est la même | Évals : cas `reponse` du corpus ; **variante : test à ajouter** à la réalisation |
| S20 | Historique du run suivant | `assistant[appels state] · tool · assistant[réponse]` : la réponse une seule fois, séquence valide pour les API | Test « historique » (et requête du run suivant) |
| S21 | Compaction | Le résumé lit ce même historique | Lecture (`rendered(history)`) |
| S22 | Session relue par un loom 2.0.14 | Projection valide ; son historique garde le texte dans le tour qui appelait `state` et n'ajoute pas la réponse : séquence valide, texte une fois | Lecture (`_conversation` d'avant) |
| S23 | API, CLI, REST, MCP, `journal_html` | Ils lisent `run.completed.output` : la bonne réponse | Lecture (`scripts/journal_html` l. 351-362) |
| S24 | Raisonnement (thinking signé) | Gardé dans la sortie comme pour toute réponse finale ; retiré de l'historique comme toujours | Lecture |

## 7. Risques restants

| # | Risque | Gravité | Traitement |
| --- | --- | --- | --- |
| R1 | Le modèle écrit une **annonce** (« Je note la sélection. ») avec `state` seul, en comptant continuer : le run s'arrête sur l'annonce | Haute si elle survient, **jamais observée** (0 sur 15 tours `state`, 97 tours d'outils) | Phrase du §4.3 ; à surveiller dans les essais ; si un modèle le fait, l'option E ou un réglage pourront être ajoutés |
| R2 | `after_guards` : si les appels `state` échouent, le texte du tour (non diffusé tôt) n'est jamais diffusé ; le modèle rappelé répond de nouveau | Faible : c'était un texte intermédiaire | Accepté ; à documenter |
| R3 | Rejeu des journaux anciens qui contiennent le cas : divergence | Faible : sessions d'essai | `CHANGELOG` |
| R4 | Autres modèles que Haiku (Sonnet, OpenAI) : comportement non mesuré | Moyenne | Essai réel avec Sonnet en `main` après réalisation |

## 8. Défauts relevés en passant

- **Commentaire faux à terme** : « Seul un outil terminal mène à COMPLETED sur un résultat d'outil » (l. 1241) — à corriger avec la règle.
- **Test d'aujourd'hui qui passerait par hasard** : en `after_guards`, sur le code actuel, le texte du tour part tôt, puis le run échoue (script épuisé) ; un test qui ne vérifierait que le flux passerait. Le test du prototype vérifie aussi l'issue du run.
- **X10 (14/10) est bien corrigé** : `_effect` (l. 626) n'a plus pris `tool.source_unavailable` pour cause. La branche de reprise terminale dépend toujours de la cause ; la nouvelle branche, elle, n'en dépend pas (elle relit le dernier tour).

## 9. Décisions à prendre

| # | Question | Recommandation |
| --- | --- | --- |
| M-D1 | Option | **A** (fin immédiate) |
| M-D2 | Phrase dans la description de `state` (§4.3) | Oui ; corpus `j7_state_v2` réenregistré |
| M-D3 | Réglage pour désactiver la règle | Non pour l'instant : elle ne vise que `loom.state`, et R1 n'a pas été observé |
| M-D4 | R2 et R3 | Acceptés, documentés |
| M-D5 | Version | 2.0.15 (non publiée), avec le durcissement du state |

## 10. Non-régression

- Avant de coder : `uv run pytest`, `pyright`, `ruff`, `lint-imports` au poste (référence de la copie : 2 996 passés, 14 échecs d'environnement).
- Après : les mêmes ; **j1 à j7 identiques** (preuve de S1) ; `j7_state_v2` réenregistré **une fois** (phrase du §4.3), puis identique.
- Les 11 tests de l'annexe A, dont les 4 qui échouent sur le code actuel.
- Essai réel : rejouer le scénario t9 (session neuve, compaction forcée), puis le même avec Sonnet en `main` (R4).

## 11. Réalisation

Un seul lot : le code du §5 (prototype repris tel quel, nettoyé), la phrase du §4.3, les tests, le corpus, puis la documentation. Pas de changement de version.

---

## Annexe A — prototype

Copie de `src/`, `tests/`, `examples/j1` à `j7` et `pyproject.toml` du dépôt (état non commité compris), hors du dépôt ; Python 3.14.8 ; extras `anthropic sql sqlite office`.

| Test (`tests/unit/test_state_final.py`) | Code actuel | Prototype |
| --- | --- | --- |
| Le texte écrit avec `state` est la réponse finale (2 appels de modèle, state posé) | ✗ `ScriptExhausted` | ✓ |
| Sans texte, le modèle est rappelé | ✓ | ✓ |
| Appel `state` en erreur (référence, clé) → modèle rappelé | ✓ | ✓ |
| Écriture refusée par le moteur → modèle rappelé | ✓ | ✓ |
| `state` avec un autre outil → tour ordinaire | ✓ | ✓ |
| Historique du run suivant : la réponse une fois, après le résultat | ✗ | ✓ |
| `after_guards` : le texte part une fois | ✓ (par hasard, §8) | ✓ |
| Contrat de sortie : réparation, réponse structurée, écritures du tour réparé retirées | ✗ | ✓ |
| Reprise entre la transition et la clôture | ✗ | ✓ |
| Run sans `loom.state` : inchangé | ✓ | ✓ |

| Mesure | Copie de référence | Prototype |
| --- | --- | --- |
| `pytest tests/unit tests/regression` | 2 996 passés, 14 échecs d'environnement | **3 007** passés, mêmes 14 échecs |
| Corpus j1 à j7 | identiques | identiques |
| Corpus `j7_state_v2` (6 cas, dont `reponse`) | — | identique après enregistrement |
