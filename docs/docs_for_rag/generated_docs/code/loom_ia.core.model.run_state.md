# loom_ia.core.model.run_state

Module `loom_ia.core.model.run_state` de loom_v2 (fichier `src/loom_ia/core/model/run_state.py`).

État d'un run et ce qui l'attend : appels, approbations, réparations, concession (points 3 et 4, fonctions H1 à H4).

Le `RunState` n'est jamais stocké : c'est la projection des événements de
son `run_id` (point 24, `core/projections/run_state.py`). Il ne contient
que des données sérialisables.

## Constantes de `loom_ia.core.model.run_state`

- `DEFAULT_LEASE = 60.0` : Durée d'une concession sur un run, en secondes (point 27, `execution.lease`).  Renouvelée au tiers ; reprise par un autre worker si son porteur se tait plus longtemps.
- `RunKind = Literal['normal', 'compaction']` : Genre d'un run : `normal`, ou `compaction`.  Un run de compaction résume une session (point 23) : il vit dans le journal de cette session, mais n'entre ni dans son historique ni dans son arbre.
- `CancelReason = Literal['requested', 'parent']` : Pourquoi un run a été arrêté avant sa fin (fonction A5) : à la demande, ou parce que son parent l'a été.  Un dépassement du délai maximal n'est pas ici : il est subi, donc écrit en `run.failed` avec `error_type: timeout` (fonction A6).
- `ApprovalDecision = Approved | Rejected` : Décision d'un approbateur en ligne.
- `ApprovalVerdict = Literal['granted', 'rejected', 'expired']` : Ce qu'une demande d'approbation est devenue.
- `Approver = Callable[[PendingApproval], Awaitable[ApprovalDecision]]` : Approbateur en ligne (point 28) : il décide dans la boucle, sans que le run passe par `PAUSED`.  Ce qu'il rend est écrit au journal comme une décision ordinaire — l'audit du point 17 vaut aussi pour les scripts. Se passe à `run(approver=…)` ; l'élicitation MCP en est un.

## Classe `loom_ia.core.model.run_state.RunStatus`

```python
class RunStatus(StrEnum)
```

Statut d'un run dans la machine à états de la boucle (point 3).

Valeurs : `ready_for_model` (prochain pas : appeler l'orchestrateur),
`awaiting_tools` (des appels d'outils attendent), `waiting_child` (un
sous-agent tourne), `paused` (une approbation ou une question attend un
humain), `finalizing` (réponse forcée sans outils : `max_iterations`,
budget, `Stop`), puis les états finals `completed`, `failed` et
`cancelled`. Chaque changement écrit un `run.transitioned`.

### Méthode `loom_ia.core.model.run_state.RunStatus.is_terminal`

```python
@property
def is_terminal(self) -> bool
```

Vrai pour `completed`, `failed` et `cancelled`.

## Classe `loom_ia.core.model.run_state.PendingCall`

```python
class PendingCall(DomainModel)
```

Appel d'outil demandé par le modèle et pas encore terminé.

**Attributs :**

- `call_id` : Identifiant de l'appel.
- `name` : Nom de l'outil.
- `arguments` : Arguments écrits par le modèle.
- `started` : Vrai dès que `tool.called` est écrit : sert à la reprise (points 18 et 26).
- `child_run_id` : Run enfant quand l'outil est un sous-agent (point 4).
- `replaced_arguments` : Arguments remplacés par une politique `before_tool` : repris tels quels si l'appel est relancé après une interruption (point 2).
- `replaced_by` : Politiques qui les ont remplacés, dans l'ordre : dites au modèle avec l'appel réécrit (essai de la fonction E7 du 19/10).
- `replaced_reasons` : Leurs motifs, dans le même ordre.

## Classe `loom_ia.core.model.run_state.Approved`

```python
@dataclass(frozen=True, slots=True)
class Approved
```

Décision d'un approbateur en ligne : il autorise l'appel, avec d'autres arguments s'il le veut (point 17).

**Attributs :**

- `by` : Identité de l'approbateur.
- `reason` : Son motif.
- `arguments` : Arguments corrigés ; `None` : ceux de la demande.
- `via` : Passerelle qui a porté la décision (audit, `docs/loom_role_user.md` §7).
- `roles` : Rôles d'accès qui l'ont permise.
- `self_approved` : Vrai si l'approbateur est celui qui a lancé le run (décision D-7 des utilisateurs et rôles).

## Classe `loom_ia.core.model.run_state.Rejected`

```python
@dataclass(frozen=True, slots=True)
class Rejected
```

Décision d'un approbateur en ligne : il refuse l'appel, et le motif revient au modèle (point 17).

**Attributs :**

- `reason` : Motif du refus, lu par le modèle.
- `by` : Identité de qui refuse.
- `via` : Passerelle qui a porté la décision (audit, `docs/loom_role_user.md` §7).
- `roles` : Rôles d'accès qui l'ont permise.
- `self_approved` : Vrai si c'est celui qui a lancé le run.

## Classe `loom_ia.core.model.run_state.ApprovalOutcome`

```python
class ApprovalOutcome(DomainModel)
```

Ce qu'une demande d'approbation est devenue (point 17).

**Attributs :**

- `verdict` : `granted`, `rejected` ou `expired`.
- `by` : Qui a tranché : clé d'API, utilisateur, ou nom rendu par un approbateur en ligne. C'est l'audit demandé par le point 17, et il n'est nulle part ailleurs.
- `reason` : Motif de la décision ; pour une expiration, « sans réponse avant <échéance> ».
- `arguments` : Arguments corrigés en accordant ; absents, ceux de la demande valent.
- `via` : Passerelle qui a porté la décision (`as_user`) ; absente des journaux antérieurs.
- `roles` : Rôles d'accès qui l'ont permise.
- `self_approved` : Vrai pour une auto-approbation.

## Classe `loom_ia.core.model.run_state.PendingApproval`

```python
class PendingApproval(DomainModel)
```

Demande d'approbation écrite au journal (point 17, fonction D10).

Elle attend tant qu'`outcome` est absent, et c'est ce qui tient le run en
`PAUSED`. Une décision la referme sans l'effacer : le déroulé se relit
entier, et la reprise sait quoi faire de l'appel. C'est ce que montrent
`loom sessions show`, les accès et l'état d'un run à l'approbateur.

**Attributs :**

- `call_id` : Appel suspendu.
- `tool_name` : Nom de l'outil.
- `arguments` : Arguments tels qu'ils seront exécutés.
- `reason` : Pourquoi l'approbation est demandée.
- `run_id` : Run qui a posé la demande : le run lui-même, ou le sous-run d'un sous-agent. Une liste d'approbations (fiche de session, résultat d'un run et de ses sous-runs) en mêle plusieurs ; sans lui, on ne saurait de quel run elle vient. `approve` se fait sur la racine de ce run.
- `agent` : Agent de ce run : c'est son `access` qui dit qui tranche (`docs/loom_role_user.md` §5.2).
- `policy` : Politique qui l'a exigée ; absente pour un outil en ``approval: always``.
- `approvers` : Rôles approbateurs, figés à l'écriture (`docs/loom_role_user.md` §5.3) ; `None` : tout rôle qui a `approve` sur l'agent.
- `self_approval` : Faux si l'agent interdit l'auto-approbation (décision D-7b des utilisateurs et rôles).
- `expire_at` : Échéance ; `None` : sans échéance.
- `outcome` : La décision, une fois prise.
- `launched` : Vrai si un `tool.called` a été écrit depuis l'accord : il a servi à un lancement, et ne couvre pas le suivant (points 17 et 18). Déduit de l'ordre du journal.

### Méthode `loom_ia.core.model.run_state.PendingApproval.stale`

```python
def stale(self, now: datetime) -> bool
```

Demande sans réponse dont la date est passée.

C'est le journal qui fait foi, pas le travail différé : une file
perdue dans un redémarrage ne peut pas laisser une demande
approuvable indéfiniment.

## Classe `loom_ia.core.model.run_state.Amendment`

```python
class Amendment(DomainModel)
```

Arguments d'un appel changés avant son exécution, par qui le modèle ne l'a pas écrit.

L'appel est réécrit dans la conversation (le modèle lit ce qui est parti) ;
cette trace sert à lui dire que la réécriture n'est pas de lui. Sans elle,
il s'attribue la correction, ou cherche à la défaire. Seuls les noms des
champs sont gardés : les valeurs d'origine peuvent être longues ou
sensibles, et les nouvelles sont déjà dans l'appel réécrit.

**Attributs :**

- `call_id` : L'appel.
- `origin` : `approval` (corrigé en accordant) ou `policy` (remplacé par une politique `before_tool`).
- `fields` : Noms des champs changés : ceux d'après dans leur ordre, puis ceux retirés.
- `by` : Qui a corrigé : l'approbateur, ou la (les) politique(s) (depuis 2.0.10).
- `reason` : Pourquoi.

## Classe `loom_ia.core.model.run_state.LimitedApprover`

```python
@dataclass(frozen=True, slots=True)
class LimitedApprover
```

Un approbateur en ligne qui ne tranche que certaines demandes (`docs/loom_role_user.md` §6.2).

`takes` dit, avant que la demande ne soit écrite, s'il la prend ; celles
qu'il ne prend pas attendent comme sans approbateur en ligne — le run se
met en pause, et un approbateur habilité tranche par un autre accès. C'est
l'élicitation MCP, ou le script, de qui a lancé le run sans pouvoir tout
approuver.

**Attributs :**

- `decide` : L'approbateur.
- `takes` : Dit s'il prend une demande.

### Méthode `loom_ia.core.model.run_state.LimitedApprover.__call__`

```python
async def __call__(self, pending: PendingApproval) -> ApprovalDecision
```

Tranche la demande avec `decide`.

## Fonction `loom_ia.core.model.run_state.takes`

```python
def takes(approver: Approver, pending: PendingApproval) -> bool
```

Vrai si l'approbateur en ligne tranche cette demande ; un simple les prend toutes.

## Classe `loom_ia.core.model.run_state.PendingRepair`

```python
class PendingRepair(DomainModel)
```

Réparation décidée par une politique (`Retry`), pas encore demandée au modèle.

Gardée dans l'état pour survivre à une reprise : le message de réparation
sera écrit au prochain pas.

**Attributs :**

- `policy` : Politique qui la demande.
- `point` : `after_model` ou `on_output`.
- `feedback` : Le diagnostic, qui partira au modèle.
- `tools` : Faux : l'orchestrateur répare sans outils (échec de forme).

## Classe `loom_ia.core.model.run_state.RunClaim`

```python
class RunClaim(DomainModel)
```

Concession en cours sur un run : qui le pilote, et jusqu'à quand (point 27).

Un seul pilote à la fois. Le porteur la renouvelle tant qu'il vit ; s'il
meurt, elle expire et un autre worker peut reprendre le run.

**Attributs :**

- `worker_id` : Instance qui pilote.
- `lease_until` : Échéance de la concession.

### Méthode `loom_ia.core.model.run_state.RunClaim.alive`

```python
def alive(self, now: datetime) -> bool
```

Vrai tant que la concession n'a pas expiré.

## Fonction `loom_ia.core.model.run_state.state_size`

```python
def state_size(value: JsonValue) -> int
```

Taille d'une valeur du state : son JSON compact, en octets UTF-8 (décision D7 du state de session).

C'est cette taille que bornent `sessions.state.max_value_bytes` (1 Kio)
et `max_total_bytes` (4 Kio).

## Fonction `loom_ia.core.model.run_state.state_json`

```python
def state_json(values: JsonValue) -> str
```

Le state tel qu'un modèle le lit : JSON compact, clés triées à tous les niveaux.

Un même state donne un même texte, donc une même requête et la même empreinte.

**Arguments :**

- `values` : Le state.

**Renvoie :**

Le JSON compact, clés triées, caractères non ASCII gardés.

## Fonction `loom_ia.core.model.run_state.finite_json`

```python
def finite_json(value: JsonValue) -> bool
```

Faux si la valeur contient NaN ou un infini.

Le JSON n'en a pas : le journal les écrirait `null`, qui efface une clé
du state. Le run qui les a posées et sa relecture ne verraient pas le même state.

## Classe `loom_ia.core.model.run_state.StateWrite`

```python
class StateWrite(DomainModel)
```

Écritures du state de la session portées par le résultat réussi d'un appel.

`turn` est la position, dans les messages du run, de la réponse du
modèle qui a demandé l'appel, et `rank` le rang de l'appel dans cette
réponse : c'est l'ordre des appels, et non celui d'arrivée des résultats,
qui départage deux écritures d'une même clé (décision D9 du state de
session).

**Attributs :**

- `call_id` : L'appel.
- `turn` : Position de la réponse du modèle qui l'a demandé.
- `rank` : Rang de l'appel dans cette réponse.
- `values` : Les écritures, `{clé: valeur}` ; `None` efface la clé.

## Classe `loom_ia.core.model.run_state.RunState`

```python
class RunState(DomainModel)
```

État d'un run, reconstruit depuis le journal par `core.projections.run_state` (fonction H1).

Jamais stocké : c'est la projection des événements de son `run_id`. Le
moteur le relit à chaque reprise (plantage, approbation, réponse), et les
accès en tirent le statut et le résultat d'un run.

**Attributs :**

- `run_id` : Le run.
- `session_id` : Sa session.
- `root_run_id` : Run racine de son arbre.
- `span_id` : Span racine du run, repris à chaque reprise.
- `parent_span_id` : Span de l'appel qui l'a lancé, pour un sous-run.
- `parent_run_id` : Run parent d'un sous-run.
- `parent_call_id` : Appel du parent qui a lancé ce sous-run.
- `depth` : Profondeur dans l'arbre ; 0 pour un run racine.
- `agent` : Agent du run.
- `kind` : `normal`, ou `compaction` pour le run système qui résume la session (point 23).
- `context` : Contexte de l'appelant.
- `judges` : Juges choisis par l'appelant (point 21) : selon leur `when`, tous, ou aucun.
- `budget` : Part de budget reçue du run parent (`budget_share`) ; les limites de l'agent s'y ajoutent, la plus basse l'emporte.
- `status` : Statut courant. Défaut : `ready_for_model`.
- `step` : Numéro de la dernière étape commencée.
- `active_ms` : Temps passé à piloter ce run : somme des étapes terminées. C'est lui que borne le délai maximal d'un agent (`timeout`, fonction A6) — pas l'horloge depuis `run.started`, qui compterait aussi l'attente en file, une pause d'approbation ou une nuit entre un plantage et sa reprise.
- `iterations` : Appels du modèle orchestrateur (borné par `max_iterations`).
- `messages` : Conversation du run : demande, réponses de l'orchestrateur, résultats d'outils.
- `pending_calls` : Appels d'outils demandés et pas encore terminés.
- `usage` : Consommation du run, sous-agents compris.
- `cost_usd` : Coût du run, en dollars.
- `model_calls` : Appels de modèle du run : orchestrateur, rôles et juges (`max_calls`) ; ceux d'un sous-agent se comptent dans son propre run.
- `exceeded` : Limites de budget déjà signalées (`budget.exceeded`) : `run.max_cost`…
- `models` : Modèle courant des emplacements qui ont basculé vers un secours (`main`, rôle, `judge:<nom>`) : le run le garde jusqu'à la fin (adhérence, point 10).
- `artifacts` : Fichiers du run (pièces jointes, sorties d'outils, déports), une fois par URI.
- `posed_state` : State de la session posé par l'application à l'ouverture du run.
- `state_writes` : Écritures du state de la session faites par les outils du run, dans l'ordre des appels (décision D9) ; un résultat en erreur n'en porte pas.
- `retries` : Réparations de l'orchestrateur demandées par chaque politique (`Retry` à `after_model` ou `on_output`), bornées par son `max_attempts`.
- `pending_repair` : `Retry` journalisé dont le message de réparation n'est pas encore écrit.
- `repairs` : Positions des messages de réparation dans `messages` : avec la réponse refusée qui les précède, ils sont exclus de l'historique de session (point 20).
- `repair_without_tools` : Vrai si la prochaine réponse de l'orchestrateur se fait sans outils (réparation de forme).
- `replaced_output` : Réponse finale remplacée par une politique `on_output`.
- `output` : Réponse finale.
- `output_data` : Réponse finale structurée : l'objet JSON validé par le schéma de sortie (fonction A7).
- `unverified` : Réponse finale gardée bien qu'un contrat ou un juge la refuse.
- `fallback` : Réponse finale remplacée par le message de repli d'un contrat ou d'un juge.
- `terminal_call_id` : Appel dont le résultat est devenu la réponse finale (point 13).
- `error_type` : Type de l'échec (`timeout`, `guard.judge`, `model.auth`, `policy.<nom>`…).
- `error` : Message de l'échec.
- `cancelled` : Motif de l'annulation, s'il y en a eu une (fonction A5).
- `claim` : Dernière concession prise sur ce run (point 27) ; `None` si personne ne l'a encore piloté, ou si le journal est antérieur à la phase 4.2b.
- `approvals` : Approbations du run (point 17), dans l'ordre des demandes. Celles qui attendent encore tiennent le run en `PAUSED` ; les autres gardent leur décision, que la reprise applique à l'appel.
- `questions` : Questions posées à l'utilisateur (`loom.ask`), dans l'ordre. Celles qui attendent encore tiennent le run en `PAUSED` ; les autres gardent leur réponse, que la reprise rend au modèle.
- `amendments` : Appels partis avec d'autres arguments que ceux du modèle, dans l'ordre des lancements.
- `finished` : Vrai après `run.completed`, `run.failed` ou `run.cancelled` : plus rien n'est accepté.
- `last_seq` : `seq` du dernier événement appliqué.

### Méthode `loom_ia.core.model.run_state.RunState.state_delta`

```python
@property
def state_delta(self) -> dict[str, JsonValue]
```

State écrit par ce run : ses écritures fusionnées dans l'ordre des appels.

Une valeur `None` y reste : appliquée au state de la session, elle
efface la clé (décision D10 du state de session).

### Méthode `loom_ia.core.model.run_state.RunState.spent`

```python
@property
def spent(self) -> Spent
```

Consommation du run : usage et coût (sous-agents compris), ses appels de modèle.

### Méthode `loom_ia.core.model.run_state.RunState.pending`

```python
def pending(self, call_id: str) -> PendingCall | None
```

L'appel en attente de cet identifiant, ou `None`.

### Méthode `loom_ia.core.model.run_state.RunState.awaiting`

```python
@property
def awaiting(self) -> tuple[PendingApproval, ...]
```

Demandes d'approbation sans réponse : le run les attend (point 17).

Un run fini, ou déjà passé à son état final, n'attend plus rien : une
demande restée sans réponse (run annulé en pause, échec à l'échéance)
reste dans `approvals`, pour l'historique, mais personne ne peut plus
la trancher.

### Méthode `loom_ia.core.model.run_state.RunState.asking`

```python
@property
def asking(self) -> tuple[PendingQuestion, ...]
```

Questions sans réponse : le run attend l'utilisateur.

Comme `awaiting` : un run fini n'attend plus rien.

### Méthode `loom_ia.core.model.run_state.RunState.on_hold`

```python
@property
def on_hold(self) -> bool
```

Le run attend un humain : une décision, ou une réponse.

### Méthode `loom_ia.core.model.run_state.RunState.question`

```python
def question(self, call_id: str) -> PendingQuestion | None
```

Dernière question posée par cet appel, close ou non, ou `None`.

### Méthode `loom_ia.core.model.run_state.RunState.approval`

```python
def approval(self, call_id: str) -> PendingApproval | None
```

Dernière demande d'approbation de cet appel, tranchée ou non.

Un appel peut en porter plusieurs : la première couvre son lancement ;
une autre vient si l'effet de ce lancement devient d'état inconnu.

### Méthode `loom_ia.core.model.run_state.RunState.attachments`

```python
@property
def attachments(self) -> tuple[ArtifactRecord, ...]
```

Pièces jointes de la demande, dans l'ordre où elles ont été données.

### Méthode `loom_ia.core.model.run_state.RunState.offloaded`

```python
@property
def offloaded(self) -> bool
```

Vrai si au moins un résultat a été déporté dans ce run (point 16).

### Méthode `loom_ia.core.model.run_state.RunState.artifact`

```python
def artifact(self, uri: str) -> ArtifactRecord | None
```

Le fichier du run à cette URI, ou `None`.
