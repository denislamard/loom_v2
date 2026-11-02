# Analyse d'impact — le modèle pose une question, le moteur attend la réponse (`wait_answer`) loom_v2

31 octobre 2026 · Denis Lamard

## En bref

Le besoin : quand le modèle a besoin d'une information, il pose la question, et **le moteur doit savoir qu'il attend une réponse de l'utilisateur**. Aujourd'hui, une question du modèle est une réponse finale comme une autre : le run se termine en `completed`, et rien dans le journal, dans `RunResult` ni dans aucun accès ne dit qu'il attend quelque chose. La réponse arrive ensuite comme un nouveau message, dans un nouveau run.

Cette attente doit rester **distincte de l'approbation paramétrée**. C'est le modèle de Claude, qui sépare la permission (décidée par la configuration, tranchée par oui ou non) de la question (`AskUserQuestion`, posée par le modèle, dont la réponse revient comme résultat de l'outil, dans le même tour).

**Verdict : rien ne bloque.** Le modèle appelle un outil fourni, `loom.ask` (nom de travail, D-1). Le moteur ne l'exécute pas :
- il écrit la question au journal (`question.asked`) ;
- le run se met en pause, avec la même mécanique que l'approbation (concession rendue, reprise, échéance), mais avec une attente à part ;
- la réponse (`question.answered`) revient au modèle comme résultat de l'appel, et **c'est le même run qui reprend**.

Par tous les accès, l'utilisateur répond en écrivant simplement son message dans la session, comme dans une conversation. Une route, une commande et une méthode dédiées existent aussi.

- **Tests :** 3 588 verts sur `tests/unit` et `tests/regression`, tous les extras, soit 3 564 plus 24 nouveaux ; 131 sautés, comme avant. Les 7 corpus de rejeu (j1 à j7) se rejouent à l'identique.
- **Qualité :** ruff propre, 5 contrats d'import tenus. pyright strict : aucune erreur nouvelle (les 68 de `src`, toutes dans les adaptateurs Postgres faute de stubs d'`asyncpg` dans l'espace d'essai, étaient là avant).
- **Coût :** 1 425 caractères (description 766, schéma 659), soit environ 390 tokens par appel de modèle (320 avant les deux phrases ajoutées après l'essai sur Haiku). Seuls les agents qui déclarent `loom.ask` le paient ; les requêtes des autres ne changent pas d'un octet.

| Point | Constat | Preuve |
| --- | --- | --- |
| Le moteur sait qu'il attend | Run en `paused`, question au journal, `RunResult.pending_questions` | Démontré (SDK, CLI, REST, MCP) |
| Distincte de l'approbation | Attente, événements, droits et commandes à part ; répondre ne tranche aucune approbation, approuver ne répond à rien | Démontré (question et approbation dans le même lot) |
| Même run, même contexte | La réponse est le résultat de l'appel `ask` ; l'historique se reconstruit sans changement | Démontré |
| Répondre en écrivant | Un message dans la session reprend le run qui a posé la question (même `run_id`) | Démontré (SDK, CLI, CLI `--stream`, REST, MCP) |
| Sous-agent | Sa question remonte à la racine, la réponse sur la racine le reprend | Démontré |
| Répondeur en ligne | Elicitation MCP (formulaire), script : pas de pause | Démontré |
| Échéance | Sans réponse, la question expire et le modèle l'apprend ; le run continue | Démontré (réveil différé, et sans lui) |
| Rejeu | Les réponses sont servies par le journal, sans pause | Démontré (`assert_replays`) |
| Agents sans `loom.ask` | Requêtes et journaux identiques | Démontré (corpus j1 à j7) |
| Lecture par une version antérieure | Une session qui contient une question est illisible pour une loom qui ne connaît pas ces événements | Démontré (`JournalCorrupted`) |

**Un bug existant a été trouvé et corrigé dans le prototype (§ Bugs existants, B-1).** Deux approbations simultanées du même appel rendent la session **définitivement illisible**, sur ton commit `7e7fe83`. Le cas typique est un double clic, ou la CLI et le REST en même temps. La même faille aurait touché les réponses.

**Trois décisions comptent avant tout :**
- le nom de l'outil et des événements (D-1, D-12) ;
- répondre en écrivant dans la session, ou seulement par une route dédiée (D-3) ;
- le droit qu'il faut pour répondre en REST (D-6).

## Périmètre et état de référence

L'analyse porte sur ton dépôt au commit `7e7fe83` (« bug scale on chart »). Le commit suivant, `1c43b7e` (graphiques de `loom.excel`), ne touche aucun des fichiers concernés ici. Rien n'a été écrit dans `/home/denis/dev/loom_v2`, à part ce document. Les essais ont tourné sur un clone de cet état, dans l'espace de travail cloud de Claude, en Python 3.14.6.

| Contrôle sur l'état de référence | Résultat |
| --- | --- |
| `pytest tests/unit tests/regression`, tous les extras, sans services | 3 564 passés (3 542 + 22), 131 sautés |
| `ruff` / `lint-imports` | propre / 5 tenus |
| `pyright` strict sur `src` | 68 erreurs, toutes d'`asyncpg` (environnement d'essai) |

Le noyau seul (sans extras) n'a pas été rejoué.

## 1. Deux attentes, deux notions

| | Approbation paramétrée (existe) | Question à l'utilisateur (prototype) |
| --- | --- | --- |
| Qui la déclenche | La config : `approval: always`, politiques `before_tool`, droits des dossiers et des tables | Le modèle, en appelant `ask` |
| Sur quoi | Un appel d'outil déjà formé | Une information qui manque |
| Qui répond | Un approbateur (portée `approve`, clé qui signe), pas forcément l'utilisateur | L'utilisateur de la session |
| Ce qu'il dit | Oui ou non, et des arguments corrigés | Un choix, plusieurs, ou un texte libre |
| Ce que reçoit le modèle | L'outil exécuté, ou « refusé par … » | La réponse, comme résultat de l'appel |
| À échéance | `approval.on_expiry` : refus (par défaut) ou échec du run | Le modèle apprend qu'il n'y a pas de réponse ; le run continue |
| Journal | `approval.requested`, `.granted`, `.rejected`, `.expired` | `question.asked`, `.answered`, `.expired` (catégorie `question`) |
| État du run | `PendingApproval` dans `approvals`, run `paused` | `PendingQuestion` dans `questions`, run `paused` |
| Résultat | `pending_approvals` | `pending_questions` |
| Répondre | `approve` / `reject` (SDK, CLI, REST), elicitation MCP | `answer` (SDK, CLI, REST), un message dans la session, elicitation MCP |

Trois règles en découlent, toutes tenues par le prototype :

- **Une réponse n'autorise rien.** Une action à faire valider reste une approbation, que la config décide. La description de l'outil le dit au modèle : « Pas pour faire valider une action : ce n'est pas une autorisation. »
- **Les deux attentes se cumulent.** Si le modèle pose une question et appelle un outil à approuver dans le même lot, le run reprend quand les deux sont levées. Répondre seul laisse le run en pause, en attente de la décision.
- **Un approbateur en ligne ne répond pas aux questions, et inversement.** `approver=` et `answerer=` sont deux paramètres distincts.

## 2. Analyse fonctionnelle

### Activer l'outil

```yaml
tools:
- python: loom.ask
  expires_in: 86400      # facultatif : sans réponse après ce délai (s), le modèle l'apprend
```

- **Inactif par défaut**, comme tous les outils fournis.
- **Réglages refusés sur la référence :** `approval`, `side_effects`, `idempotent`, `timeout` et `output`, avec le motif : « l'outil ne fait rien lui-même : il pose une question et attend la réponse de l'utilisateur ; une action à faire valider passe par l'approbation ».
- **Réglages acceptés :** `description` et `skill` (une consigne d'agent : quand poser une question, combien de choix proposer).
- **Pas pour les rôles.** Un rôle n'a pas d'outils. Ce sont `main` et les sous-agents qui posent les questions.

### Ce que le modèle envoie

```json
{"questions": [
  {"question": "Pour quel client faut-il préparer le devis ?",
   "header": "Client",
   "options": [{"label": "Dupont"}, {"label": "Martin"}]}
]}
```

Les champs :
- 1 à 4 questions par appel ;
- `header`, intitulé court de 24 caractères au plus ;
- `options`, jusqu'à 6 choix (`label`, et une `description` facultative) ;
- `multiple`, pour plusieurs choix.

L'utilisateur peut **toujours** répondre autre chose qu'un choix proposé, comme chez Claude. Une question mal formée revient au modèle en erreur, avec le champ fautif : « Question invalide (0.header) : … ».

La description de l'outil :

> Pose une ou plusieurs questions à l'utilisateur et attend sa réponse, qui te revient comme résultat de cet appel. À utiliser quand une information indispensable manque et qu'aucun autre outil ne peut la donner (un choix entre plusieurs voies, une précision, une préférence) ; ne pas écrire la question dans ta réponse. Propose des choix ('options') quand les réponses possibles sont connues ; l'utilisateur peut toujours répondre autre chose. Pas pour faire valider une action : ce n'est pas une autorisation. S'il te faut encore une réponse de l'utilisateur à la fin de ton tour, appelle ask : une question écrite dans ta réponse termine le run et n'attend rien. Si tu reposes une question, dis d'abord en une phrase pourquoi la réponse précédente ne suffisait pas.

### Ce que l'utilisateur répond

Il y a deux formes, et l'une des deux seulement :

- **`answers`** : une liste par question, dans leur ordre (le libellé choisi, ou un texte libre). C'est la forme des formulaires et de `loom answer --answer`.
- **`text`** : un message libre qui répond à l'ensemble. C'est ce que donne un message tapé dans la conversation.

Refusées, sans rien écrire (`InvalidAnswer`, 422 en REST) :

| Cas | Message |
| --- | --- |
| Ni `answers` ni `text`, ou les deux | « une réponse donne soit 'answers' (une par question), soit 'text' » |
| Pas autant de réponses que de questions | « 2 réponse(s) pour 1 question(s) » |
| Une réponse vide | « question 1 : réponse vide » |
| Plusieurs choix sans `multiple` | « question 1 : un seul choix attendu » |
| Plusieurs questions en attente, sans `call_id` | « plusieurs questions attendent (…) : indiquer call_id » |
| Plus rien n'attend, ou un `call_id` inconnu | `UnknownQuestion` (404) |
| Question expirée | `QuestionStale` (409) : la reprise du run la clôt |

### Ce que le modèle reçoit

Le résultat de son appel `ask` :

```text
Réponse de l'utilisateur :
- Pour quel client faut-il préparer le devis ? → Dupont
```

ou, pour un message libre :

```text
Réponse de l'utilisateur, en message libre : Dupont, celui de Lyon

Si ce message ne répond pas à ta question (autre sujet, ou ta demande de départ répétée), traite-le si besoin, puis repose ta question avec ask, jamais dans ta réponse en texte : le run se terminerait sans attendre la réponse.
```

La consigne vient de l'essai sur Haiku : à « Au fait, quelle heure est-il ? » pendant la question, il donnait l'heure et reposait sa question en texte. Le run se terminait alors sans question ouverte, et la réponse suivante démarrait un nouveau run. La demande de départ répétée est nommée depuis l'essai ask-12 : Haiku la prenait pour une nouvelle demande et finissait sur la question écrite en texte.

Le résultat porte aussi les données (`{"answers": [{"question": …, "answer": [...]}]}` ou `{"reply": …}`), que `$ref` et l'interface peuvent lire.

### Le run pendant l'attente

**Comment le run s'arrête :**
- Il passe en `paused` après le reste du lot : les autres appels du même tour s'exécutent d'abord, comme pour une approbation.
- Il rend sa concession, si bien qu'un autre worker pourra le reprendre.
- Le délai de l'agent ne compte pas l'attente : il ne mesure que le pilotage.

**Ce qu'on en lit :**
- `RunResult.pending_questions` liste les questions de tout l'arbre du run, celles des sous-agents comprises.
- `SessionInfo.pending_questions` les liste pour toute la session.
- Dans `pending_questions`, `run_id` désigne le run qui a posé la question.

**Pour un sous-agent,** la racine est en `waiting_child`, comme pour une approbation. L'interface doit donc se fier à `pending_questions`, pas au seul statut.

### Répondre en écrivant (routage)

Dans une session où une question de **cet agent** attend, le message suivant **est** la réponse. Il est écrit en `question.answered` (`text`), et c'est le run qui a posé la question qui reprend : le résultat rendu porte **son** `run_id`. C'est le comportement de Claude, et c'est ce qui rend la fonction utilisable sans rien changer côté client.

Le message ne devient pas une réponse, et ouvre un nouveau tour, dans ces cas :
- pas de `session_id` ;
- un message vide ;
- des pièces jointes, ou des écritures du state ;
- un `run_id` choisi par l'appelant ;
- la question attend un autre agent ;
- aucun agent du client ne déclare `loom.ask` (dans ce cas, la session n'est même pas relue).

Le modèle du nouveau tour reçoit alors une note : « au début de ce tour, une question posée plus tôt dans cette conversation attend encore la réponse de l'utilisateur : … Elle reste posée ; ne la repose pas. »

Si plusieurs questions attendent (deux appels `ask` dans le même tour), le message répond à la première ; les autres restent posées.

### Échéance

**Sans `expires_in`**, une question attend indéfiniment : la conversation reprend quand l'utilisateur revient.

**Avec `expires_in` :**
- Le réveil différé des approbations (`expire_approval`) ramène aussi le run à l'échéance d'une question.
- Sans ce réveil (file perdue dans un redémarrage), l'`expire_at` du journal fait foi : une réponse tardive est refusée (`QuestionStale`), et la reprise du run écrit `question.expired`.
- Le modèle reçoit : « L'utilisateur n'a pas répondu dans le délai. Ne repose pas la question de toi-même ; continue sans cette information si tu le peux, sinon dis-lui ce qui manque. »
- Une question expirée ne fait jamais échouer le run.

### Annulation

Un run annulé pendant l'attente clôt ses questions : leur `call_id` s'ajoute à `run.cancelled.withdrawn`, comme pour les approbations. Une réponse arrivée ensuite est refusée (`UnknownQuestion`).

### Répondeur en ligne

`Loom.run(..., answerer=…)`, et de même `stream()` et `context()`, reçoit une fonction qui répond dans la boucle. Le run ne passe alors jamais par la pause. La question et sa réponse sont tout de même écrites au journal, comme pour un approbateur en ligne.

Le répondeur peut aussi renoncer (`NoAnswer`) :
- en général, la question reste alors posée et le run se met en pause ;
- au rejeu ou en évaluation (`NoAnswer(expired=True)`), la question se clôt sans réponse.

C'est sur ce répondeur que l'elicitation MCP est bâtie.

## 3. Par accès

### SDK Python (`Loom`)

| Élément | Changement |
| --- | --- |
| `run()`, `stream()` | Routage du message vers la question en attente ; paramètre `answerer` |
| `answer(run_id, *, answers=None, text=None, call_id=None, by=None, session_id, tenant_id)` | Nouveau : écrit la réponse, remet la reprise en file (job `resume`, comme `approve`) ; rend le `call_id` |
| `RunResult.pending_questions`, `SessionInfo.pending_questions` | Nouveaux champs (additifs) |
| `RunResult.masked()`, `SessionInfo.masked()` | Sans `read_content`, le texte des questions est retiré (`redacted` : `pending_questions[].questions`) |
| Exceptions | `UnknownQuestion` (une `Unknown`), `InvalidAnswer` et `QuestionStale` (des `ValueError`) |
| `context()` | Paramètre `answerer` |
| `resume()` | Inchangé : un run qui attend une réponse ne repart pas sans elle |

### CLI

`loom run` sur un run qui s'arrête sur une question (sortie réelle du prototype, modèle simulé) :

```text
$ loom run demo "Prépare un devis." --session s1
Pour quel client faut-il préparer le devis ?
  1. Dupont
  2. Martin

Statut     : paused · itérations : 1 · tokens : 483/71 · coût : 0.0000 $
Run        : 01a19351-0a8d-77ec-9997-bd79b31601fe
Question   : fake_0_0 — répondre : loom run demo "<réponse>" --session s1 (ou loom answer 01a19351-… --answer <n°> --session s1)

$ loom run demo "Dupont, celui de Lyon" --session s1 --stream
· réponse de l'utilisateur reçue
· ask : fait
Devis préparé pour Dupont.

Statut     : completed · itérations : 2 · tokens : 1134/102 · coût : 0.0000 $
Run        : 01a19351-0a8d-77ec-9997-bd79b31601fe
```

- **La question part sur stdout**, avec ses choix numérotés, après ce que le modèle a écrit en la posant (`RunResult.text`). C'est ce que l'utilisateur doit lire. Une pause d'approbation, elle, laisse stdout vide.
- **Code de sortie 0** pour un run qui n'attend que l'utilisateur (D-8). Une pause d'approbation garde 1.
- **`loom answer RUN_ID [TEXTE] [--answer RÉPONSE …] [--call ID] [--by NOM] [--session] [--no-wait] [--json]`** :
  - `--answer` se répète, une fois par question, et « ; » y sépare plusieurs choix ; il prend le libellé d'un choix ou le numéro que `loom run` affiche (`--answer 2`), sauf si un libellé est lui-même ce nombre ;
  - comme `approve`, la commande pilote la reprise dans le terminal, sauf avec `--no-wait` ou une file servie par un courtier ;
  - une réponse refusée donne le code 2.
- **`--stream`** montre « question à l'utilisateur : … », « réponse de l'utilisateur reçue » (ou « réponse de <by> reçue ») et « question restée sans réponse : le modèle continue sans ».
- ⚠ **`loom run --session` sans `--run-id` ne fixe plus le `run_id` d'avance**, sans quoi le message ne pourrait jamais répondre. L'identifiant se lit sur le résultat, ou sur les événements du flux. Aucun changement visible.
- **Non fait dans le prototype :**
  - `loom sessions show` ne liste pas encore les questions en attente, comme il le fait pour les approbations (fait depuis ; avec leurs choix depuis le 02/11) ;
  - `loom inspect` montre les trois événements sous leur forme générique (« question.asked (warning) »).

### REST

| Route | Changement |
| --- | --- |
| `POST /v1/runs/{run_id}/answer` | Nouvelle. Portée `run`, plus le droit sur l'agent du run (D-6) |
| `POST /v1/agents/{name}/runs` | En mode synchrone, un message dans une session où une question attend y répond. La réponse est le `RunResult` du run repris : 201, avec le `run_id` de la question |
| `GET /v1/runs/{id}`, `GET /v1/sessions/{id}` | `pending_questions` ; sans `read_content`, le texte des questions est retiré |
| `GET /v1/runs/{id}/events` (SSE) | Inchangé : le flux reste ouvert pendant la pause et suit la reprise jusqu'à la clôture. C'est ce qu'il faut à une interface de conversation |
| `GET /v1/events?category=question` | Nouvelle valeur de catégorie |

La route `answer` en détail :
- **Corps** : `{"answers": [["Dupont"]]}`, ou `{"text": "…"}`, avec `call_id` et `by` facultatifs. Sans `by`, c'est la clé d'API qui signe.
- **Réponse** : 200 et `{"run_id", "call_id"}`. La reprise part en tâche de fond.
- **Erreurs** : 403 sans portée `run`, 404 si rien n'attend, 409 si la question a expiré, 422 si la réponse ne convient pas.

Les codes de la route `answer` sont démontrés. L'OpenAPI se met à jour seul (route, `Answer`, `Answered`, champs de `RunResult` et de `SessionInfo`).

**Lacune :** `POST /v1/agents/{name}/runs` en mode `background: true` passe par `submit()`, qui **ne route pas**. Le message ouvre alors un nouveau run, qui reçoit la note. C'est à faire, ou à refuser en 409 (D-13).

### MCP

**Avec elicitation** (client stdio qui la déclare) :
- Les questions deviennent un formulaire, dans la même boucle, sans pause. Une question à choix unique donne une liste (`choix_N`, `enum` des libellés) et un champ libre prioritaire (`reponse_N`). Une question à plusieurs choix donne un texte où « ; » sépare les choix, car un formulaire MCP n'a qu'un niveau de propriétés simples.
- La réponse est signée `mcp:<client>`.
- Un formulaire fermé ou refusé laisse la question posée, et le run se met en pause.

**Sans elicitation** (et toujours en HTTP, où le serveur est sans état) :
- L'outil de l'agent rend la main sans `isError`. Son texte est la question, avec ses choix, suivie de : « [L'agent attend la réponse de l'utilisateur : rappeler demo avec session_id=s1 et la réponse en message.] ».
- `structuredContent.pending_questions` détaille les questions, et `RUN_OUTPUT` les déclare.
- Le client répond en rappelant l'outil de l'agent avec la même `session_id` : c'est le routage. **Aucun outil MCP de plus**, contrairement à ce qu'il faudrait si la réponse passait par une route dédiée.
- ⚠ Le chemin avec progression (`progressToken`) ne fixe plus le `run_id` d'avance quand une session est donnée. Il le lit sur les événements du flux (`root_run_id`).

Pourquoi pas d'outil `answer` en MCP : pour l'approbation, il est exclu, sinon le LLM client approuverait lui-même ses effets. Pour une question, le risque est moindre, puisqu'une réponse n'autorise rien. Mais le routage suffit, et n'ajoute rien au schéma vu par le LLM client.

### Plusieurs questions dans un même appel (02/11)

Ajouté après l'essai réel `t-2000` (deux questions, refus de `--answer 1` sans indication). Analyse complète : `docs/multi-questions.md`.

- **Moteur** : le refus nomme les questions par leur intitulé et dit la règle, pour tous les accès : `1 réponse pour 2 questions (Relances, Acompte) : une réponse par question, dans leur ordre`, `question 2 (Acompte) : un seul choix attendu`. C'est le `detail` du 422 REST et le texte de `InvalidAnswer`.
- **CLI** : « Question i/n · intitulé » devant chaque question, une ligne vide entre elles ; la commande exacte (`--answer <Relances> --answer <Acompte>`) ; refus avec cette commande ; numéro hors des choix refusé ; écho de ce qui a été compris (`Répondu    : Relances → Oui`) ; `loom sessions show` avec les choix ; une question sans choix marquée « réponse libre, en texte », et `--answer <réponse>` au lieu de `<n°>` dans sa commande (essai t-4000).
- **REST** : une liste de réponses par question, dans leur ordre :

```json
POST /v1/runs/{run_id}/answer?session_id=t-2000
{"answers": [["Oui, relances faites"], ["Non, rien versé"]]}
```

  Avec une seule liste, `422` et `{"detail": "Réponse refusée : 1 réponse pour 2 questions (Relances, Acompte) : une réponse par question, dans leur ordre"}`.
- **MCP sans elicitation** : questions numérotées avec leur intitulé, choix séparés par « ; », et « rappeler … et les réponses en un seul message ». Un run qui attend une approbation **et** une question rend les deux (avant, l'approbation seule).
- **MCP avec elicitation** : le champ libre qui accompagne une liste est titré « <intitulé> : autre réponse ».
- Avec une seule question, tous les rendus restent ceux d'avant, sauf l'écho de `loom answer`.

### Autres accès

- **Webhooks (`POST /hooks/{name}`) :** pas de routage, puisqu'un événement extérieur n'est pas une réponse de l'utilisateur. Ils ouvrent un nouveau run, qui reçoit la note.
- **Évaluations :** personne ne répond. La question se clôt sans réponse et le modèle continue.
- **Rejeu identique :** les réponses viennent du journal, en ligne, sans pause. Une question absente du journal est une divergence de type `question`.
- **Rejeu en variante :** la réponse du journal si la même question y est ; sinon personne ne répond.

## 4. Analyse technique

### Modifications, mesurées sur le prototype

| Fichier | Changement |
| --- | --- |
| `core/model/question.py` | Nouveau (97 lignes) : `Question`, `QuestionOption`, `QuestionReply`, `PendingQuestion`, `NoAnswer`, `Answerer` |
| `core/model/run_state.py` | +23 : `RunState.questions`, `asking`, `on_hold`, `question()` |
| `core/model/tooling.py` | +10 : `AskSettings`, `ToolSpec.asks` |
| `core/events/payloads.py` | +67 : `QuestionAsked`, `QuestionAnswered`, `QuestionExpired`, catégorie `question`, union |
| `core/projections/run_state.py` | +51 / −1 : la question dans l'état, `_answered`, `asking_in_session`, réponse tardive absorbée |
| `engine/questions.py` | Nouveau (95 lignes) : questions lues dans l'appel, contrôle d'une réponse, ce que reçoit le modèle |
| `engine/executor.py` | +99 : `_ask` (réponse du journal, question à écrire, répondeur en ligne), `ToolReplay.answer`, rejeu ancien toléré |
| `engine/loop.py` | +46 / −6 : `answerer` dans `RunContext`, pause sur `on_hold`, échéance d'une question, retrait à l'annulation, note |
| `engine/pending.py` | +17 / −1 : `question_note` |
| `engine/writer.py` | +8 / −1 : `append(since=…)`, la garde vérifiée sous le verrou avant d'écrire (B-1) |
| `replay/book.py`, `variant.py`, `evals.py` | +41, +10, +7 : réponses servies au rejeu |
| `tools_builtin/ask.py`, `ask_params.py`, `__init__.py` | Nouveaux (103 et 21 lignes), +9 : l'outil, ses paramètres, son entrée au catalogue |
| `access/api.py` | +307 / −9 : `answer`, `_write_answer`, routage, `answerer`, champs et masquage, exceptions, garde de concurrence d'`approve` (B-1) |
| `access/cli/main.py` | +116 / −4 : `loom answer`, affichage, code de sortie, `run_id` laissé libre |
| `access/http/app.py`, `schemas.py` | +34, +20 : route `answer`, `Answer`, `Answered`, codes d'erreur |
| `access/mcp_server/server.py` | +109 / −1 : formulaire d'elicitation, texte de pause, `pending_questions`, `run_id` lu sur le flux |
| `access/progress.py` | +12 : trois lignes de déroulé |
| `tests/unit/test_wait_answer.py`, `test_wait_answer_access.py` | Nouveaux : 15 et 9 tests |
| `tests/unit/test_tools_builtin.py` | +2 : le catalogue et le schéma de config connaissent `loom.ask` |

En tout, 28 fichiers, +1 982 / −24 lignes, tests compris.

### Choix techniques et leurs raisons

**Un outil, plutôt qu'une balise.** Quatre façons de faire savoir au moteur qu'il attend ont été pesées :

| | Outil `ask` (prototype) | Balise dans le texte | Classifieur | « ? » final |
| --- | --- | --- | --- | --- |
| Fiabilité | Appel explicite, typé, validé par le schéma | Le modèle l'oublie, ou l'écrit mal | Un appel de plus, qui se trompe | Nulle : « Voulez-vous autre chose ? » n'attend rien |
| Choix proposés | Oui (`options`) | À inventer dans la balise | Non | Non |
| Ce que voit le client | Rien (l'appel n'est pas du texte) | La balise, à retirer | Rien | Rien |
| Coût | ~390 tokens par appel, pour les agents qui l'ont | Une consigne | Un appel de modèle par run | Rien |

L'outil est aussi ce que fait Claude, et le même mécanisme qu'une sortie d'outil terminal ou que `loom.state` : le moteur reconnaît l'appel et en tire la suite.

**Le moteur n'exécute pas l'outil.** Il le reconnaît à `ToolSpec.asks`, une déclaration posée par l'outil fourni : la configuration ne peut pas la poser, et elle survit aux surcharges (`ConfiguredTool`). Le moteur lit alors les questions, les écrit, et attend. Ni politique `before_tool`, ni approbation, ni idempotence ne s'appliquent à l'appel, puisqu'il n'a aucun effet (D-9). `AskTool.invoke` lève s'il est jamais appelé.

**La même pause, une attente à part.**
- Le run passe en `paused`, la valeur existante de `RunStatus`. Un statut nouveau aurait demandé de revoir toutes les vérifications de statut, chez loom et chez les clients (les interfaces, `unpiloted`, la sous-délégation, le rejeu), sans rien apporter de plus que `pending_questions`.
- Ce qui retient le run devient `on_hold` (`awaiting` ou `asking`) à quatre endroits du moteur : passage en pause, reprise possible (`_actionable`), échéance (`_resumed`), retrait à l'annulation.
- `awaiting`, `PendingApproval` et tout le code des approbations restent inchangés.

**La réponse est un `tool.completed`.** La reconstruction de l'historique (`turns()`) n'a pas changé d'une ligne : le tour où le modèle a posé la question, puis sa réponse, se relisent comme n'importe quel appel d'outil, y compris après une compaction.

**Le routage est dans le SDK, pas dans le moteur.** Le moteur ne sait pas ce qu'est « l'utilisateur qui écrit ». `Loom.run` et `Loom.stream` regardent la session avant d'ouvrir un run. Tous les accès passent par eux (CLI, REST synchrone, MCP), et le routage leur vient sans code de plus, sauf ce qui fixait le `run_id` d'avance.

**Le rejeu, sans casser les implémentations existantes.** `ToolReplay` gagne `answer`. Une implémentation écrite avant (il y en a une dans les tests de l'exécuteur) est tolérée : personne ne répond, la question se clôt sans réponse.

**Mesures :**
- **Coût en tokens :** description 766 et schéma 659 caractères, en JSON compact, soit environ 390 tokens par appel de modèle pour un agent qui déclare `loom.ask`.
- **Coût du routage :** une lecture de plus du journal de la session, à chaque run lancé avec une `session_id`, mais seulement pour un client dont un agent déclare `loom.ask`. Sans lui, rien n'est relu.

### Tests écrits sur le prototype (24)

| Thème | Tests |
| --- | --- |
| Pause et réponse (5) | La question met en pause (rien n'est exécuté, `pending_questions`, fiche de session) ; `answer` reprend le même run ; un message dans la session répond (un seul `run.started`) ; réponses refusées sans rien écrire (4 cas) ; répondeur en ligne sans pause |
| Échéance et annulation (3) | Le réveil différé clôt la question, et le modèle l'apprend ; sans réveil, une réponse tardive est refusée et la reprise conclut ; l'annulation retire la question |
| Avec approbation, avec sous-agent (3) | Deux attentes distinctes (répondre laisse l'approbation en attente) ; un approbateur en ligne ne répond pas ; la question d'un sous-agent remonte à la racine et la réponse la reprend |
| Rejeu, routage (2) | Un run répondu se rejoue à l'identique ; un `run_id` choisi ouvre un nouveau tour, la question reste posée |
| Concurrence (2) | Deux réponses simultanées : une seule passe, le journal reste lisible ; deux accords simultanés, idem (B-1) |
| CLI (3) | La question sur stdout, code 0, réponse par message ; `loom answer` (refus, puis réponse) ; `--stream` route et montre le déroulé |
| REST (3) | Route `answer` : 403 sans `run`, 422, 200, 404 ; un message dans la session répond (201, même `run_id`) ; sans `read_content`, la question est masquée |
| MCP (3) | Sans elicitation : question en texte, réponse en rappelant l'agent ; elicitation : formulaire (liste et champ libre), réponse signée, pas de pause ; formulaire fermé : la question reste posée |

## 5. Effets de bord sur le reste de loom

1. ⚠ **Une loom qui ne connaît pas ces événements ne lit pas une session qui en contient.** Démontré : le code de `7e7fe83` lit les journaux du prototype en `JournalCorrupted` (« category : Input should be 'run', 'message'… »). Ce sont seulement les sessions où une question a été posée ; les autres restent lisibles. Lister, la rétention et l'effacement passent toujours, car ils ne lisent que les marques. C'est la limite de la lecture tolérante (DEC-1) : un champ ajouté passe, un type nouveau non. `loom.state` v2 l'avait évitée en n'ajoutant aucun type ; ce n'est pas possible ici sans détourner les événements d'approbation, ce qui est exactement la confusion à éviter. À dire dans le CHANGELOG : « une 2.0.14 ne lit pas une session où `loom.ask` a servi ».
2. **Les requêtes des agents sans `loom.ask` ne changent pas.** Les 7 corpus de rejeu sont identiques, à l'empreinte de chaque requête près, et aucune note n'apparaît dans une session sans question.
3. **Un agent qui déclare `loom.ask` voit ses requêtes changer** (environ 390 tokens). Un run fait avant ne se rejoue plus à l'identique (« ce qui diffère : les outils proposés »), et le cache de prompt est réécrit une fois.
4. **Un message peut rendre un `run_id` déjà connu.** Dans une session où une question attend, `run()`, `POST /agents/{name}/runs` et l'outil MCP de l'agent rendent le run repris, avec son `run_id`, et non un nouveau. Une application qui suppose un run neuf par message, pour ses statistiques ou son interface, doit l'accepter. Cela ne vaut que pour les agents qui déclarent `loom.ask`.
5. **CLI et MCP :** le `run_id` n'est plus fixé d'avance dans une session (sauf `--run-id`). Rien de visible, mais c'était un prérequis du routage.
6. **Schémas publiés, tous additifs :**
   - le schéma de config (`loom schema`) gagne `Builtin_ask`, et `PythonTool` exclut `loom.ask` (un test ajusté) ;
   - l'OpenAPI gagne une route et des champs ;
   - le schéma de sortie MCP gagne `pending_questions` ;
   - la catégorie `question` s'ajoute aux valeurs de `?category=`.
7. **Masquage :** une clé sans `read_content` ne lit pas le texte des questions en relisant un run. L'application qui a lancé le run le voit dans la réponse de son lancement, qui n'est pas masquée, mais pas en le relisant (D-7).
8. **Politiques :** une politique `before_tool` ne voit pas les appels à `ask` (D-9). `tools_deny`, qui retire un outil par son nom, marche comme pour les autres.
9. **Rétention :** une session où une question attend s'efface comme une autre à son échéance. C'est déjà le cas pour une approbation.
10. **`loom inspect` et la télémétrie** exportent les trois événements sous leur forme générique (span events, facettes), sans code de plus. Une ligne dédiée dans `inspect` est à faire (lot 5).
11. **`ToolReplay` gagne une méthode.** Une implémentation tierce sans elle est tolérée (voir § Choix techniques).

## 6. Bugs existants trouvés en passant

**B-1 — Deux décisions simultanées rendent la session illisible (démontré sur `7e7fe83`).**

Le scénario : deux `approve` du même appel en même temps, par un double clic dans une interface, la CLI et le REST, ou deux approbateurs.
- Chacun lit l'appel encore en attente, et chacun écrit son `approval.granted`.
- La projection refuse le second : « approval.granted pour l'appel 'fake_0_0', déjà tranché (granted) ».
- Le run devient illisible, et **chaque run suivant de la session** est refusé (`SessionUnreadable`).

Rejoué sur le code d'origine :

```text
approve x2 : [('fake_0_0',), ('fake_0_0',)]
 -> lecture : ProjectionError … approval.granted pour l'appel 'fake_0_0', déjà tranché (granted) (seq 14)
 run suivant : SessionUnreadable Historique de la session s1 illisible : …
```

**Pourquoi :** l'écrivain d'une session sérialise les écritures, mais il ne revérifie rien quand il n'y a pas de conflit de séquence. C'est le cas dans un même process, où l'écrivain est partagé.

**Correctif du prototype :**
- `SessionWriter.append(since=…)` passe à la garde, **sous le verrou et avant d'écrire**, ce qui s'est écrit depuis la lecture de l'appelant.
- `approve` et `reject` renoncent alors, avec un résultat vide (« rien n'attendait »), et `answer` lève `UnknownQuestion`.
- Entre deux process, la garde existante joue déjà à la reprise du conflit de séquence.

Deux tests le tiennent. Je recommande de le livrer à part, avant le reste (lot 0) : il vaut pour les approbations dès aujourd'hui.

**B-2 — `approval.scope` est déclaré, mais pas appliqué (lu dans le code).** `ApprovalSettings.scope` (« droit exigé de l'approbateur, côté REST (N2) ») se règle sur l'agent et s'écrit dans `approval.requested`. Pourtant, les routes `approve` et `reject` exigent toujours `approve` (`require(who, "approve")`) et ne le lisent pas. Un agent qui demanderait `scope: admin` pour ses approbations ne serait pas protégé comme il le croit. Il faut le corriger, ou retirer le réglage.

**Point de vigilance (non démontré) :** l'elicitation MCP n'a pas de délai. `elicit_form` attend sans borne, côté approbation comme, dans le prototype, côté question. Un client stdio qui n'affiche jamais le formulaire tient le run piloté, avec sa concession renouvelée, tant que la connexion vit.

## 7. Plan de non-régression

| Contrôle | Attendu | Prototype |
| --- | --- | --- |
| Suite complète, tous les extras | Aucun test existant cassé, hors celui ajusté | 3 588 passés (3 564 + 24), 131 sautés |
| Corpus de rejeu j1 à j7 | Requêtes identiques | 22 passés, identiques |
| Agents sans `loom.ask` | Ni outil, ni note, ni relecture de session de plus | Démontré (corpus ; routage court-circuité) |
| Journaux d'avant | Lus tels quels | Démontré (toute la suite les lit) |
| Concurrence | Deux décisions ou réponses : une seule passe | Démontré (B-1) |
| `pyright` / `ruff` / `lint-imports` | Rien de nouveau / propre / 5 tenus | 68 erreurs inchangées (asyncpg) / propre / 5 tenus |
| Noyau seul | Rien de cassé sans extras | Non rejoué |
| Essai réel | Haiku appelle `ask` au lieu d'écrire sa question | À faire (lot 6) |

## 8. Décisions à prendre

| # | Question | Options | Recommandation |
| --- | --- | --- | --- |
| D-1 | Nom de l'outil | `loom.ask` (prototype) ; `loom.ask_user` ; `loom.question` | À toi : le modèle le voit sans le préfixe, un nom court et clair aide |
| D-2 | Ce que devient le run | (a) pause du même run, réponse en résultat de l'appel (prototype) ; (b) le run se termine, marqué « attend une réponse », et la réponse ouvre un nouveau run | (a) : même contexte, même budget, elicitation MCP, comme Claude |
| D-3 | Répondre en écrivant | (a) un message dans la session répond (prototype) ; (b) seulement `answer` (route, commande, méthode) | (a), avec `answer` en plus : sans (a), chaque client doit savoir qu'une question attend |
| D-4 | Quand un message n'est pas une réponse | Celles du prototype (autre agent, pièces jointes, state, `run_id` choisi, pas de session) ; ou pièces jointes acceptées comme réponse | Celles du prototype ; les pièces jointes plus tard si un cas le demande |
| D-5 | Échéance | (a) aucune par défaut, `expires_in` facultatif, le modèle est prévenu (prototype) ; (b) un défaut, comme les approbations (24 h) ; (c) un `on_expiry: fail` | (a) : une conversation reprend quand l'utilisateur revient |
| D-6 | Droit pour répondre en REST | (a) portée `run` et droit sur l'agent (prototype) ; (b) une portée `answer` | (a) : qui peut lancer l'agent peut poursuivre la conversation ; une réponse n'autorise rien |
| D-7 | Texte des questions sans `read_content` | (a) masqué, comme les arguments d'une approbation (prototype) ; (b) visible avec `read` | (a), cohérent avec le reste ; l'application qui lance le run le voit dans sa réponse |
| D-8 | Code de sortie de `loom run` en attente d'une réponse | (a) 0 (prototype) ; (b) 1, comme une pause d'approbation ; (c) un code dédié | (a) : attendre l'utilisateur n'est pas un échec ; la pause d'approbation pourrait suivre plus tard |
| D-9 | Politiques `before_tool` sur `ask` | (a) contournées (prototype) ; (b) appliquées | (a) : l'appel n'a aucun effet ; une politique qui veut limiter les questions peut agir en `after_model` |
| D-10 | Correctif B-1 | Dans ce chantier, en lot 0 ; ou à part | Lot 0, livré d'abord : il protège déjà les approbations |
| D-11 | B-2 (`approval.scope`) | Appliquer la portée déclarée ; ou retirer le réglage | À trancher à part ; hors chantier |
| D-12 | Noms des événements | `question.asked`, `.answered`, `.expired`, catégorie `question` (prototype) ; ou `input.*` | À toi |
| D-13 | REST en `background: true` | Router aussi (via `submit`) ; ou refuser en 409 tant qu'une question attend | Router, au lot 3 |
| D-14 | Version | 2.0.15 (non publiée), ou la suivante | À toi ; la rupture de lecture (effet 1) plaide pour une version qui le dit |

## 9. Plan de mise en œuvre

0. **B-1 seul** : `SessionWriter.append(since=…)`, la garde d'`approve` et `reject`, et leurs tests. Indépendant du reste.
1. **Le socle** :
   - le modèle (`question.py`, `RunState`, `ToolSpec.asks`), les événements et la projection ;
   - l'outil fourni et son catalogue ;
   - l'exécuteur (`_ask`), la boucle (pause, échéance, annulation, note) et le rejeu ;
   - et leurs tests.
2. **Le SDK** : `answer`, `pending_questions`, le masquage, `answerer`, le routage dans `run` et `stream`, les exceptions, et leurs tests.
3. **CLI et REST** :
   - CLI : `loom answer`, l'affichage, le code de sortie, le `run_id` laissé libre, `loom sessions show` ;
   - REST : la route `answer`, les codes d'erreur, le routage en `background` ;
   - et leurs tests.
4. **MCP** : formulaire d'elicitation, texte de pause, `pending_questions`, `run_id` lu sur le flux, et leurs tests.
5. **Affichage et documentation** :
   - `loom inspect` ;
   - le guide (chapitre 32 pour l'outil, et la partie approbations pour la distinction) ;
   - `fonctions.md`, `conception.md` (lecture tolérante) ;
   - le `CHANGELOG`, avec la rupture de lecture.
6. **Essai réel** avec ton agent de test et Haiku. Il s'agit de vérifier deux choses : qu'il appelle `ask` au lieu d'écrire sa question, et qu'il ne s'en sert pas pour faire valider une action. Un `skill` le guidera si besoin.

Les lots 1 et 2 peuvent n'en faire qu'un, comme pour les graphiques.
