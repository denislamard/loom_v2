# State de session et contexte de l'orchestrateur — proposition pour loom_v2

> Statut : **proposition**, rien n'est codé. Rédigé après lecture de `core/events`, `core/projections`, `core/model`, `engine/loop.py`, `engine/executor.py`, `engine/roles.py`, `agents/spec.py`, `config/models.py`, `sessions/`, `replay/` et des tests associés. Les numéros de ligne sont ceux du dépôt au moment de la lecture.

## 1. Le problème

Ce que loom garde aujourd'hui d'une session :

| Quoi | Où | Survit à la compaction ? |
|---|---|---|
| Journal d'événements typés | `data/<client>/<session>.jsonl` | Oui, il n'est **jamais réécrit** |
| Historique (messages) | projection `history()` | Remplacé par un résumé en prose |
| Résultats d'outils structurés | dans `tool.completed` | **Plus dans l'historique** après compaction (seul le journal les garde) |
| Faits métier | base de l'application | Oui, hors loom |

Il manque deux choses, une par côté de la requête :

1. **Un state de session** : quelques valeurs **typées, petites, durables**, que l'application ou un outil pose (« client courant = C-002 ») et que tout le reste relit — sans dépendre de ce que le modèle a gardé dans sa conversation ni de la qualité d'un résumé.
2. **Un contexte déclaratif pour l'orchestrateur** : aujourd'hui seuls les **rôles** déclarent ce qu'ils reçoivent (`context:` : `session_summary`, `last_turns`, `tool_results`…). `main` n'a rien d'équivalent : sa requête est « system + historique + messages du run », point.

Le fil conducteur : le **state** est ce que le système *sait* (contrôle) ; le **contexte** est ce que le modèle *voit* à un appel donné. Le contexte est une projection du state, de l'historique et des résultats d'outils, choisie par la config.

## 2. Ce que le code impose (contraintes vérifiées)

1. **Le journal est la source de vérité** ; `RunState`, historique, coûts, rejeu en sont des projections (`core/projections/`, fonctions pures). Un state doit suivre la même règle : *événements écrits, projection pure*, sinon la reprise après plantage et le rejeu divergent.
2. **La compaction ne supprime rien.** `session.compacted` n'est qu'un marqueur ; le journal complet reste lisible, et `drive()` le relit déjà en entier (`events = await ctx.store.read(...)`, `loop.py` ~l. 497). Un state peut donc se **recalculer depuis le début du journal** sans passer par les marqueurs. Un snapshot du state reste une optimisation possible, jamais une nécessité (même logique que `snapshot.py`).
3. **Couches (import-linter)** : `core` n'importe rien d'autre ; `engine` ne dépend ni de `config`, `agents`, `sessions`, `tools`… Le state (modèle + projection + événement) va dans `core` ; l'injection dans `engine` ; la déclaration dans `agents/spec.py` et `config/`.
4. **Lecture tolérante (DEC-1)** : un champ **ajouté** à un payload est ignoré par un ancien lecteur ; un **type d'événement inconnu** ne l'est pas — l'union `DurablePayload` est discriminée sur `type`, un `type` absent de l'union fait échouer la validation. Conséquence : ajouter un type d'événement rend les journaux *nouveaux* illisibles par un *ancien* loom (l'inverse marche). Précédent dans le dépôt : `run.claimed` et `run.cancelled` ont été ajoutés après coup à l'union (commentaire de `DURABLE_PAYLOADS`, `payloads.py` ~l. 1158). À couvrir par un test sur le modèle de `test_lecture_tolerante.py`.
5. **Les événements de catégorie `session` sont écartés des projections par run** : `fold`/`_by_run` filtrent `category != "session"` (`run_state.py` ~l. 437/452, `access/api.py` ~l. 737), `Cuts` les ignore (`snapshot.py` l. 105), et ils portent l'identifiant de la session comme `run_id` quand ils sont écrits hors run. Un événement de state **ne doit donc pas** être de catégorie `session` : il doit être visible dans le `RunState` du run en cours (pour que l'appel de modèle suivant, dans le même run, voie la valeur posée à l'étape précédente) et être clos avec son run.
6. **`ToolContext` est figé** et n'a aucun accès à un magasin de state (`core/ports/tool.py`) ; les outils reçoivent `tenant_id`, `session_id`, `run_id`, `call_id`, `agent`, `caller`, idempotence.
7. **Construction de la requête** : `_model_step` (`loop.py` ~l. 1580-1620) assemble `system` (+ `REFS_HINT` si besoin), `messages = (*previous, *messages du run, *hint)`, puis exécute les politiques `before_model` avec `turns` et `summary` déjà disponibles dans `RunContext` (posés par `_driven` à partir de `earlier`). C'est le point d'injection naturel, à côté de ce qui existe.
8. **Contexte des rôles** : `engine/roles.py` (~l. 225-275) construit le message du rôle à partir de `ContextItem` (`agents/spec.py` l. 120-140) et `RoleSpec` impose que tout contexte déclaré soit **utilisé** dans `input_template` (#50). Cette mécanique se réutilise telle quelle pour un nouvel item `session_state`.
9. **Schéma de config** : `DomainModel` = `extra="forbid"` + `frozen` ; le JSON Schema (`config/schema.py`) est généré par Pydantic, donc tout nouveau champ de `AgentSpec` y apparaît sans travail supplémentaire. Un champ **optionnel avec valeur par défaut** ne casse aucune config existante.

## 3. Conception du state

### 3.1 Modèle

Un state de session = un dictionnaire `clé → valeur JSON`, dont les **clés sont déclarées** par l'agent (avec un JSON Schema par clé), pour qu'un outil ou le modèle ne puisse pas y poser n'importe quoi.

```yaml
# agents/agent.yaml
state:
  client_courant:
    schema: { type: string, pattern: "^C-[0-9]+$" }
    description: Code du client dont on parle.        # montrée au modèle si la clé est injectée
    writers: [get_client]                              # outils autorisés à la poser
  dernier_devis:
    schema: { type: string }
    writers: [creer_devis]
```

Règles :
- valeur **petite** (limite de taille par clé et au total, configurable) : ce n'est ni un stockage de documents ni de la mémoire longue (F6 / RAG reste pour les notes) ;
- une clé a **un schéma** ; une écriture invalide est **refusée** comme le sont les arguments d'outil (erreur renvoyée à l'outil, rien n'est écrit) ;
- `writers` : liste blanche d'outils. Sans liste, seule l'application (API/`run(..., state=...)`) peut écrire. Évite qu'une sortie d'outil détourne un état de contrôle ;
- pas de `delete` implicite : une valeur `null` écrite = clé effacée (visible dans le journal).

### 3.2 Événement

Un seul nouveau type de payload, **`state.set`**, ajouté à `DurablePayload` :

```python
class StateSet(Payload):
    category: ClassVar[EventCategory] = "tool"      # voir 2.5 : reste dans le run
    type: Literal["state.set"] = "state.set"
    key: str
    value: JsonValue                                 # None = effacé
    source: Literal["tool", "application"]
    call_id: str | None = None                       # l'appel d'outil qui l'a posé
    tool_name: str | None = None
    content_fields = ("value",)                      # masquage / chiffrement / rétention comme un contenu
```

- Catégorie `tool` : aucun changement de `EventCategory`, aucun changement des filtres `session`, `Cuts`, `_by_run`.
- Écrit **dans le même lot atomique** que le `tool.completed` qui l'a produit (le lot d'un outil est déjà une écriture unique) : jamais un résultat sans son state, ni l'inverse.
- `content_fields=("value",)` : la valeur est une donnée (« C-002 », un téléphone, un montant) ; elle suit la rédaction et la rétention des contenus (voir `core/events/redaction.py`) — pas de fuite par un chemin que la config RGPD ne couvre pas.
- Rejeu (`replay/`) : il rejoue le journal ; un événement ignoré par son `match` ne le perturbe pas, mais il faut un test (rejeu d'une session qui contient du state).

### 3.3 Projection

Dans `core/projections/` (pure, comme `history.py`) :

```python
def session_state(events: Iterable[Event]) -> Mapping[str, JsonValue]:
    """State de la session : dernier `state.set` de chaque clé, dans l'ordre du journal."""
```

- Elle lit **tout** le journal (ou repart d'un snapshot si on l'ajoute plus tard) ; elle ne dépend d'aucun marqueur, donc **survit à la compaction, au trim et au snapshot** sans rien y changer.
- Dans un run : `RunState` gagne `state_delta: Mapping[str, JsonValue] = {}` (valeur par défaut → anciens états inchangés), alimenté par `apply` sur `StateSet`. L'état vu par un appel de modèle = `{**état avant le run, **state_delta}`.
- Ordre : l'ordre du journal (`seq`) fait foi. Deux runs concurrents sur la même session : le dernier écrit gagne, déterministe au rejeu. À documenter, pas à verrouiller.
- Runs échoués / annulés : leurs `state.set` **comptent** (l'outil a eu lieu ; le journal dit la vérité). C'est une différence assumée avec l'historique, qui ne garde que les tours réussis. Alternative possible si on préfère : ignorer les runs non réussis dans la projection — décision à prendre (§6).
- Sous-runs : `earlier` est vide pour eux (`rooted` faux, `loop.py` ~l. 505) ; ils ne voient pas le state. Un rôle, lui, le reçoit par son contexte (§4.3), construit depuis le run parent.

### 3.4 API côté outil

Deux écritures possibles, la première suffit pour démarrer :

```python
# 1) retour d'outil — aucune dépendance au contexte
return ToolOutput(blocks=(...), state={"client_courant": code})

# 2) lecture dans l'outil
async def creer_devis(ctx: ToolContext, ...):
    code = ctx.state.get("client_courant")
```

- `ToolOutput.state` : champ **optionnel** (`Mapping[str, JsonValue] = {}`), consommé par l'exécuteur qui émet les `state.set` puis **retire** le champ avant d'écrire `tool.completed` (le journal garde ainsi un seul chemin de vérité, et les anciens lecteurs voient un `ToolOutput` inchangé).
- `ToolContext.state` : vue **en lecture seule** (mapping figé) calculée au lancement de l'appel — dans `executor._execute` (~l. 883), qui a déjà `view.state` ; il suffit d'un paramètre de plus (valeur par défaut vide : les tests et outils existants ne changent pas).
- Les outils Python décorés par `@tool` (`tools/python.py`) renvoient déjà `str | ToolOutput | Image…` : rien à casser, seul le cas `ToolOutput` apporte `state`.
- Côté application : `loom.run(prompt, session_id=..., state={"client_courant": "C-002"})` et/ou une commande CLI. Elle écrit des `state.set` `source="application"` dans `begin_run`, dans le même lot que `run.started` et `user.message`, pour rester dans le run (contrainte 5). À confirmer à l'implémentation.

### 3.5 Compaction

Rien à changer pour la correction (le state ne dépend pas de l'historique). Deux améliorations optionnelles, séparées :
- le résumé de compaction ne doit **pas** recopier le state (il est réinjecté par ailleurs) — ajouter une phrase au prompt de `_compaction` ;
- si la relecture complète devient coûteuse, ajouter un champ optionnel `state` à `SessionSnapshot` (champ ajouté = lecture tolérante, DEC-1). Trois écrivains de marqueurs seraient à tenir à jour (`snapshot.write_snapshot`, `compaction._refresh`, `compaction.compact`) : **ne pas le faire tant qu'il n'y a pas de besoin mesuré**.

## 4. Conception du contexte de l'orchestrateur

### 4.1 Déclaration

Même vocabulaire que les rôles, sur `main` :

```yaml
main:
  model: HAIKU
  system_file: system.md
  context:                       # NOUVEAU, optionnel
    - session_state              # tout le state déclaré
    - { session_state: [client_courant] }   # ou certaines clés
    - session_summary            # déjà connu des rôles
```

- Sans `context`, **comportement identique à aujourd'hui** (valeur par défaut `()` ; aucun journal, test ou config existant ne change).
- Réutilise `ContextItem` (`agents/spec.py`) ; on y ajoute `session_state` (forme simple ou avec clés). `MainRole` reçoit `context: tuple[ContextItem, ...] = ()`.
- Pour `main`, tous les items n'ont pas de sens : `user_input` est déjà dans les messages, `attachments` aussi. On valide en config que `main.context` n'accepte que `session_state` et, plus tard, `session_summary`/`last_turns` s'ils sont utiles (sinon erreur claire au chargement — comme les autres validations de `spec.py`).

### 4.2 Injection

Dans `_model_step`, juste après la construction de `system`/`messages` et **avant** les politiques `before_model` :

```python
system = with_context(system, ctx, state)    # bloc <session_state>…</session_state>
```

- Fonction pure, dans `engine/` (à côté de `transcript.py`), qui lit `ctx.session_state` (posé par `_driven` comme `turns`/`summary`) et `state.state_delta`.
- Rendu : balises comme les rôles (`tagged(...)` dans `roles.py`), JSON compact, clés triées → **déterministe**, donc stable pour le cache de prompt tant que le state ne change pas.
- **Où l'injecter** (décision, §6) :
  - *Fin du `system`* — simple ; une modification du state invalide le cache du system et des messages. Le state change rarement : acceptable.
  - *Message `user` inséré juste avant les messages du run* — n'invalide que la partie du run en cours ; mais place du texte « hors conversation » dans les messages.
- Les politiques `before_model` voient la requête **déjà enrichie** (elles peuvent la lire ou la remplacer), et `ModelExchanged` (capture brute) la journalise : le rejeu voit exactement ce qui est parti.
- Si le state est vide : rien n'est ajouté (pas de balise vide).

### 4.3 Rôles et `session_state`

Même item pour les rôles : `engine/roles.py` ajoute un `case SessionState(...)` qui expose `context.session_state` (mapping) au template, avec la règle #50 existante (déclaré ⇒ utilisé). Les rôles ne reçoivent donc le state que s'ils le demandent, comme tout le reste.

## 5. Compatibilité : ce qui ne casse pas, ce qui change

| Élément | Impact |
|---|---|
| Configs existantes (`loom.yaml`, `agents/*.yaml`) | Aucun : `state:` et `main.context:` optionnels |
| Journaux existants | Lus normalement (pas de `state.set` → state vide) |
| Journaux **nouveaux** avec `state.set` lus par un **ancien** loom | **Illisibles** (type inconnu de l'union) — upgrade à sens unique, comme `run.claimed` en son temps. À noter dans les notes de version |
| `RunState` | Champ ajouté avec défaut → sérialisations et tests existants intacts |
| `ToolOutput` | Champ `state` optionnel, retiré avant journalisation |
| `ToolContext` | Champ ajouté avec défaut |
| Couches import-linter | Respectées : modèle/projection/événement dans `core`, injection dans `engine`, déclaration dans `agents`/`config` |
| Compaction / snapshot / trim | Inchangés (le state ne dépend pas des marqueurs) |
| Rejeu | À tester avec une session contenant du state |
| Idempotence | `state.set` rejoué à la reprise : même valeur, même clé → idempotent par construction (dernier écrit) ; la reprise d'un lot déjà écrit ne le réécrit pas |
| Coût | Le state ajoute des tokens d'entrée à **chaque** appel du modèle : à garder petit |

## 6. Décisions à prendre

1. **Catégorie de l'événement** : `tool` (proposée) ou nouvelle catégorie `state` ?
2. **Runs échoués/annulés** : leurs écritures de state comptent-elles (proposé : oui) ?
3. **Emplacement dans la requête** : fin du `system` (proposé) ou message `user` dédié ?
4. **Écriture par l'application** : option `state=` sur `run()`/CLI dès la première version, ou seulement par les outils ?
5. **Déclaration obligatoire des clés** (proposé) ou state libre ? Le typage est ce qui protège d'un outil qui écrit n'importe quoi.
6. **Limites** de taille (par clé / total) et valeurs par défaut.

## 7. Plan de mise en œuvre (par petites étapes, chacune testable seule)

1. `core` : `StateSet` + union `DurablePayload` + `session_state()` + `RunState.state_delta` + `apply` ; tests dans `test_events.py`, `test_projections.py`, `test_lecture_tolerante.py` (journal avec `state.set` : lu par le nouveau, refusé par l'ancien — documenté).
2. `core/model` + `engine/executor.py` : `ToolOutput.state` consommé par l'exécuteur (validation contre les clés déclarées, `writers`), `ToolContext.state` ; tests `test_executor.py`, `test_tools.py`.
3. `agents/spec.py` + `config/` : bloc `state:` et `main.context:` avec validations ; tests `test_config.py`, `test_config_roles.py` ; régénérer le JSON Schema.
4. `engine/loop.py` : `RunContext.session_state`, posé par `_driven` ; `with_context` dans `_model_step` ; tests `test_loop.py`, `test_loop_policies.py` (la politique voit le bloc), `test_rejeu.py`.
5. `engine/roles.py` : item `session_state` pour les rôles ; `test_roles.py`.
6. Application : option `state=` (API Python + CLI), `loom state <session>` pour lire (même esprit que les commandes qui lisent le journal).
7. Documentation : `fonctions.md` (nouveau point en F : « State de session »), `conception.md` (§11 et §9.6), un how-to.

## 8. Ce que ça donne sur agent_01

```yaml
state:
  client_courant:
    schema: { type: string, pattern: "^C-[0-9]+$" }
    writers: [get_client]
main:
  model: HAIKU
  system_file: system.md
  context:
    - session_state
```

`get_client` renvoie `ToolOutput(..., state={"client_courant": code})`. À partir de là :
- « donne-moi son téléphone » ne dépend plus d'un résumé de compaction : `client_courant` est dans chaque requête ;
- le rôle `rediger_message_client` n'a plus besoin de `tool_results: [get_client] scope: session` pour savoir *quel* client — il peut lire `context.session_state.client_courant` ;
- les données métier (téléphone, adresse) **restent dans la base** : le state ne porte que l'identifiant.

## 9. Ce que cette proposition ne fait pas

- Pas de mémoire longue ni de RAG (F6 reste séparée).
- Pas de logique métier dans loom : « client courant » est une clé déclarée par l'application, loom ne connaît que des clés, des schémas et des valeurs.
- Pas de state partagé entre sessions ni entre clients (le state appartient à `(tenant, session)`, comme le journal).
- Pas de modification de l'historique ni de la compaction.
