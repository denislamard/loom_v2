# loom_ia.engine.loop

Module `loom_ia.engine.loop` de loom_v2 (fichier `src/loom_ia/engine/loop.py`).

Boucle d'exécution d'un run : `step` et `drive` (point 3 ; fonctions A2 et A4).

`step` fait une seule chose, déduite de l'état reconstruit :

- **décider**, quand l'effet de l'état courant est déjà dans le journal :
  transition vers l'état suivant, ou clôture du run ;
- **sinon exécuter l'effet** de l'état, encadré par `step.started` et
  `step.completed` : appel du modèle (`READY_FOR_MODEL`), lot d'outils
  (`AWAITING_TOOLS`) ou réponse forcée sans outils (`FINALIZING`).

`drive` écrit chaque événement dès qu'il est produit, l'applique à l'état,
et recommence tant que le run peut avancer. La reprise après un plantage est
donc le cas normal : l'état relu dit si l'effet a eu lieu, et un appel
d'outil commencé mais non terminé est traité par l'exécuteur (point 18).

Plafond d'itérations : au retour des outils, si le modèle a été appelé
`max_iterations` fois, le run passe en `FINALIZING` ; le dépassement est
borné à cette dernière génération (et à ses réparations éventuelles). La
réponse forcée part sans outils, avec une consigne en dernier message de sa
requête (`FINALIZE_HINT`, jamais journalisée) : répondre en texte avec ce
qui est déjà obtenu.

Outil terminal (point 13) : seul dans son tour et sans erreur, sa sortie devient
la réponse finale. `run.completed` désigne alors son `tool.completed`
au lieu de recopier la sortie. Appelé avec d'autres outils, il redevient un
outil ordinaire et l'orchestrateur compose la réponse.

Réponse écrite avec `loom.state` (state v2, règle M1, `docs/state_analyse_moteur.md`) : quand le modèle écrit sa
réponse et, dans la même réponse, n'appelle que `loom.state`, tous ces
appels réussis sans écriture refusée, son texte est la réponse finale, sans
le rappeler — rappelé, il en réécrirait une autre. Elle passe par les mêmes
contrôles que toute réponse finale (`_final`). Analyse :
`docs/state_analyse_moteur.md`.

Rôles et sous-agents : si l'un d'eux est proposé dans le run, chaque résultat
montré au modèle porte sa référence (`[result:3]`) et le prompt système
explique `$ref` (point 12). Dans la requête, les résultats d'un tour suivent
l'ordre des appels, quel que soit leur ordre d'arrivée dans le journal.

Politiques (points 1 et 2) : `before_model` s'exécute avant l'appel du modèle
(requête remplacée, arrêt vers `FINALIZING`, échec) ; `after_model` et
`on_output` au moment de décider de la suite d'une réponse, pour qu'une
reprise après un plantage les évalue aussi. Une réparation (`Retry`) écrit
son `policy.decided`, ferme les appels d'outils de la réponse refusée,
puis écrit le diagnostic (`message.user` de `kind: repair`) : le modèle
répond de nouveau, sans outils si la politique l'a demandé. Les politiques
d'outils sont appliquées par l'exécuteur.

Contrats et diffusion (points 20 et 11) : les guards sont des politiques ; leurs
contrôles (`guard.checked`) précèdent leurs décisions dans le journal. Une
réponse finale structurée (schéma de sortie de l'agent) porte son objet
JSON dans `run.completed` ; une réponse gardée hors contrat y est marquée
`unverified`. Avec `stream_output: live`, les morceaux du modèle partent
au fil de l'eau, et une réparation envoie d'abord un `StreamReset` ; avec
`after_guards`, le texte d'une réponse qui appelle des outils part à la fin
de cette réponse, et la réponse finale seulement une fois ses contrôles
passés, qu'elle vienne de l'orchestrateur ou d'un rôle terminal.

Schéma natif (fonction B9) : un appel de l'orchestrateur qui ne peut pas appeler
d'outil (réponse forcée, réparation sans outils, agent sans outils) porte le
schéma de la réponse finale (`output_schema`) ; l'adaptateur le transmet
au fournisseur si le modèle a la capacité `native_json`.

Secours (fonction B4, point 10) : l'appel de l'orchestrateur passe par sa chaîne
(`RunContext.chain`, `main` puis `fallbacks`) : tentatives, bascules
(`model.fell_back`) et disjoncteur ouvert (`circuit.opened`) sont écrits
dans le span de l'étape. Après une bascule, le run reste sur le secours
(`RunState.models`) ; la requête est construite pour lui, avant les
politiques `before_model`.

Budgets (fonction J4) : `before_model` reçoit la consommation des runs précédents
de la session (`session`), calculée dans le journal par `drive` ; un
sous-run reçoit la part de budget de son parent dans son `run.started`.

Juges (point 21) : un juge est une politique fournie qui appelle son modèle ; cet
appel est écrit dans son propre span, au nom de `judge:<nom>`, avant son
verdict (`judge.evaluated`). Son coût entre dans `run.completed` (ou
`run.failed`), écrit dans la même décision ; il ne compte pas dans les
itérations. Le choix de l'appelant (`judges` : `auto`, `force`,
`skip`) est écrit dans `run.started` et hérité par les sous-runs.

Pièces jointes (fonction G1) : validées avant tout écrit (signature binaire, type,
taille), rangées dans le stockage d'artefacts, annoncées par un
`artifact.stored` chacune, puis jointes au message de l'utilisateur en
références. Chaque appel de modèle les résout selon ses capacités (point 14).

State de la session (option C) : l'application en pose à l'ouverture d'un run
racine, écrit dans son `run.started` après contrôle — ou le run est refusé
avant tout écrit, comme pour une pièce jointe (`StateRefused`, décision D13 du state de session). Un
orchestrateur qui déclare `main.context` reçoit le state à chaque appel,
même vide : celui des runs précédents, ce que l'application a posé et ce que
les outils du run ont écrit, en dernier message de la requête (avant la
consigne d'une réponse forcée), jamais journalisé comme message, et avant les
politiques `before_model` (décision D4 du state de session). Sans `main.context`, la requête ne change pas.

## Constantes de `loom_ia.engine.loop`

- `HISTORY_UNREADABLE = 'session.unreadable'` : `error_type` du run qui s'ouvre sur une session dont l'historique est illisible.
- `DEFAULT_MAX_ITERATIONS = 10` : Appels du modèle avant la réponse forcée, par défaut (`max_iterations`).
- `TERMINAL_RULE = 'loom.terminal'` : Règle du moteur journalisée comme une décision de politique (point 13, backlog #008).  Elle dit qu'un outil terminal appelé avec d'autres redevient ordinaire.
- `_CALL_EVENTS = (ModelExchanged, ModelRetried, ModelFellBack, CircuitOpened, ModelResponded)` : Événements d'un appel de modèle, journalisés dans son span.
- `_BARE_EVENTS = (ToolCalled, ToolCompleted, IdempotencyReused, ApprovalRequested, ApprovalGranted, ApprovalRejected, QuestionAsked, Que…` : Événements que l'exécuteur rend tels quels, sans enveloppe d'appel.
- `_ACTIONABLE = frozenset({RunStatus.READY_FOR_MODEL, RunStatus.AWAITING_TOOLS, RunStatus.FINALIZING, RunStatus.COMPLETED, RunStatus.FA…` : États d'où un run peut avancer seul.

## Classe `loom_ia.engine.loop.RunContext`

```python
@dataclass(frozen=True, kw_only=True)
class RunContext
```

Dépendances et réglages d'un agent ; rien de ce qui est ici n'est journalisé.

Construit au montage d'un agent (`agents/`) ; `drive` en tire une copie
pour chaque run, avec l'écrivain, l'historique et le state de la session.

**Attributs :**

- `agent` : Nom de l'agent.
- `store` : Journal des événements.
- `model` : Client du modèle `main`.
- `model_spec` : Définition du modèle `main`.
- `tools` : Outils de l'agent.
- `system` : Prompt système de l'orchestrateur.
- `max_iterations` : Appels du modèle avant la réponse forcée (`FINALIZING`) ; 10 par défaut.
- `max_tokens` : Réglage du rôle `main` (fonction B6) : remplace celui du modèle.
- `params` : Paramètres du rôle `main` : surchargent `model_spec.params` clé par clé.
- `on_chunk` : Reçoit les morceaux du flux du modèle, pour la diffusion en direct.
- `attachments` : Pièces jointes acceptées à l'entrée d'un run (fonction G1).
- `keep_images` : Images des tours précédents dans la requête (fonction F4) : mentionnées (`last_run`) ou renvoyées en octets (`all`).
- `writer` : Écrivain du journal, posé par `drive` pour le run en cours : les sous-agents écrivent leur run avec lui.
- `policies` : Politiques de l'agent, dans l'ordre déclaré (points 1 et 2) ; les guards en tête.
- `turns` : Historique de la session avant ce run, groupé par tour, posé par `drive` pour les contextes déclarés (points 12 et 23).
- `summary` : Dernier résumé de compaction de la session ; même règle.
- `session_state` : State de la session avant ce run, posé par `drive` pour un agent qui en déclare : vide pour un sous-run et un run de compaction, qui ne le reçoivent pas.
- `state_keys` : Clés du state que l'orchestrateur reçoit à chaque appel (`main.context`, décision D4 du state de session) : `()` pour toutes celles déclarées, `None` pour aucune.
- `output` : Contrat de la réponse finale : son schéma donne la réponse structurée (fonction A7).
- `stream_output` : Diffusion de la réponse finale : `live` (au fil de l'eau) ou `after_guards` (après ses contrôles, point 11).
- `fallbacks` : Secours du modèle `main`, dans l'ordre (fonction B4, point 10).
- `breakers` : Disjoncteurs des modèles, partagés par les runs d'une instance `Loom`.
- `timeout` : Délai maximal du run, en secondes (fonction A6). Il borne le temps de pilotage cumulé (`RunState.active_ms`), pas l'horloge : une reprise garde le budget qui reste. `None` : pas de délai.
- `worker_id` : Instance qui pilote, pour la concession (point 27). `None` : pas de concession — un `drive` appelé directement, ou un sous-run, que son parent pilote déjà.
- `lease` : Durée de la concession, en secondes ; renouvelée au tiers.
- `approval` : Approbations de l'agent (point 17) : délai, effet d'une expiration, droit exigé.
- `approver` : Approbateur en ligne (point 28) : il tranche dans la boucle, et le run ne passe jamais par `PAUSED`. Sans lui, l'approbation est asynchrone.
- `answerer` : Répondeur en ligne (`docs/wait_answer.md`) : il répond aux questions `ask` dans la boucle (elicitation MCP, script). Sans lui, le run attend en `PAUSED`.

### Méthode `loom_ia.engine.loop.RunContext.artifacts`

```python
@property
def artifacts(self) -> ArtifactStore | None
```

Stockage des fichiers du run : celui de l'exécuteur d'outils.

### Méthode `loom_ia.engine.loop.RunContext.chain`

```python
def chain(self, *, on_chunk: ChunkCallback | None=None) -> ModelChain
```

Modèle `main` et ses secours, avec les réglages du rôle (fonction B6).

## Classe `loom_ia.engine.loop.ParentRun`

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class ParentRun
```

Run parent d'un sous-run, et l'appel qui l'a lancé (point 4).

**Attributs :**

- `run_id` : Le run parent.
- `root_run_id` : Racine de l'arbre ; l'enfant en hérite.
- `call_id` : Appel du parent qui lance l'enfant.
- `depth` : Profondeur du parent ; l'enfant est à `depth + 1`.
- `span_id` : Span de l'appel dans le parent : parent du span racine de l'enfant.
- `judges` : Juges choisis pour le parent : l'enfant hérite du même choix (point 21).
- `budget` : Part du budget du parent donnée à l'enfant (`budget_share`, fonction J4).

## Fonction `loom_ia.engine.loop.begin_run`

```python
async def begin_run(ctx: RunContext, prompt: str | Message, *, attachments: Sequence[Attachment]=(), session_id: SessionId | None=None, context: CallerContext | None=None, run_id: RunId | None=None, parent: ParentRun | None=None, writer: SessionWriter | None=None, judges: JudgesMode='auto', kind: RunKind='normal', triggered_by: RunId | None=None, trigger: str | None=None, state: Mapping[str, JsonValue] | None=None) -> RunState
```

Écrit le démarrage d'un run et la demande de l'utilisateur.

Un `run_id` fourni par l'appelant ne doit pas déjà exister dans le journal :
sinon `RunExists` (une `ValueError`), même si l'autre ouverture est
simultanée — la perdante n'écrit rien.
Une pièce jointe refusée lève `AttachmentError` avant tout écrit.

Un sous-run (`parent`) s'écrit dans le journal de son parent, avec
l'écrivain de la session (`writer`) : il hérite de sa racine et du choix
de ses juges, et sa profondeur est celle du parent plus un.

`judges` (point 21) : `auto`, chaque juge selon son `when` ; `force`,
tous les juges ; `skip`, aucun.

`kind` et `triggered_by` marquent un run système : la compaction d'une
session (point 23) tourne dans le journal de cette session sans entrer dans son
historique. `trigger` nomme la porte d'entrée déclarée qui a ouvert le
run (fonction H6) : une facette du journal, pour savoir ce qu'une porte a lancé.

`state` : écritures du state de la session par l'application (`None`
efface la clé), pour un run racine ordinaire seulement. Contrôlées comme
celles d'un outil, elles vont dans `run.started` ; une écriture refusée,
ou une clé `required` restée sans valeur, lève `StateRefused` (une
`ValueError`) avant tout écrit (décision D13 du state de session).

**Arguments :**

- `ctx` : L'agent.
- `prompt` : La demande, texte ou message.
- `attachments` : Pièces jointes, validées puis rangées (un `artifact.stored` chacune).
- `session_id` : Session ; à défaut, le run est son propre journal.
- `context` : Contexte de l'appelant (client, utilisateur, rôles…).
- `run_id` : Identifiant choisi par l'appelant ; à défaut, un nouveau.
- `parent` : Run parent d'un sous-run.
- `writer` : Écrivain de la session.
- `judges` : Choix des juges.
- `kind` : `normal` ou un run système (compaction).
- `triggered_by` : Run qui a déclenché un run système.
- `trigger` : Porte d'entrée déclarée qui a ouvert le run.
- `state` : Écritures du state par l'application.

**Renvoie :**

L'état du run ouvert, projeté des événements écrits.

**Lève :**

- `RunExists` : Le `run_id` existe déjà.
- `AttachmentError` : Pièce jointe refusée.
- `StateRefused` : State refusé, ou clé `required` sans valeur.
- `SessionUnreadable` : Historique de la session illisible (run racine).

## Classe `loom_ia.engine.loop.SessionUnreadable`

```python
class SessionUnreadable(Exception)
```

L'historique de la session ne se relit pas : aucun run n'y est ouvert.

Un run précédent dont les événements sont refusés par la projection empêche
de savoir ce que le modèle doit voir et ce que la session a déjà dépensé.
Le sauter en silence ferait répondre l'agent avec une mémoire amputée ;
l'ouvrir pour le faire échouer laisserait un run échoué de plus à chaque
tentative. Rien n'est écrit : le run illisible est à réparer à part.

**Arguments :**

- `session_id` : La session.
- `error` : L'erreur de la projection.

**Attributs :**

- `session_id` : La session.

## Fonction `loom_ia.engine.loop.check_state`

```python
async def check_state(ctx: RunContext, state: Mapping[str, JsonValue] | None, *, tenant_id: TenantId, session_id: SessionId, run_id: RunId | None=None) -> None
```

Contrôle, sans rien écrire, le state avec lequel s'ouvrirait un run racine (décision D13 du state de session).

`begin_run` fait ce contrôle lui-même ; il se fait ici plus tôt quand
l'ouverture est précédée d'une écriture — la compaction de sécurité d'une
session trop longue —, pour qu'un run refusé ne laisse rien au journal.
Lève `StateRefused`, ou `SessionUnreadable` pour une session illisible.

Rien n'est relu quand rien ne peut être refusé : aucun state posé, aucune
clé `required`. Un `run_id` choisi qui existe déjà est laissé à
`begin_run` : une demande rejouée garde sa réponse (`RunExists`).

**Arguments :**

- `ctx` : L'agent.
- `state` : Le state posé par l'application.
- `tenant_id` : Client de la session.
- `session_id` : La session.
- `run_id` : Identifiant choisi pour le run, s'il y en a un.

**Lève :**

- `StateRefused` : State refusé, ou clé `required` sans valeur.
- `SessionUnreadable` : Historique de la session illisible.

## Fonction `loom_ia.engine.loop._accepts_state`

```python
def _accepts_state(ctx: RunContext, posed: Mapping[str, JsonValue]) -> None
```

Un agent sans state ne reçoit rien de l'application.

## Fonction `loom_ia.engine.loop._check_opening`

```python
def _check_opening(ctx: RunContext, posed: Mapping[str, JsonValue], prior: Mapping[str, JsonValue]) -> None
```

Le state posé, puis les clés obligatoires, sur le state de la session (décisions D6, D7 et D13 du state de session).

## Fonction `loom_ia.engine.loop._check_history`

```python
async def _check_history(ctx: RunContext, tenant_id: TenantId, session_id: SessionId) -> dict[str, JsonValue]
```

Relit l'historique de la session avant d'y ouvrir un run racine.

Rend le state de la session pour un agent qui en déclare (vide sinon) : il
se lit sur tout le journal, au-delà du dernier snapshot.

## Fonction `loom_ia.engine.loop._store_attachment`

```python
async def _store_attachment(store: ArtifactStore, scope: RunScope, attachment: Attachment, media_type: str) -> ArtifactStored
```

Range une pièce jointe validée ; son `artifact.stored` précède le message.

## Fonction `loom_ia.engine.loop.drive`

```python
async def drive(ctx: RunContext, run_id: RunId, *, session_id: SessionId | None=None, tenant_id: TenantId | None=None, writer: SessionWriter | None=None) -> RunState
```

Fait avancer le run jusqu'à un état où il ne peut plus avancer seul.

Sans `session_id`, le run est son propre journal (`session_id = run_id`).

Les sources d'outils de l'agent (serveurs MCP) sont ouvertes au début et
refermées à la fin : leurs outils restent fixes pendant tout le `drive`.
Une source injoignable est journalisée (`tool.source_unavailable`) ; si
le run ne peut pas s'en passer, il échoue.

Un sous-run reçoit l'écrivain de son parent (`writer`) : ils écrivent
dans le même journal. Il n'a pas d'historique de session. Un run de
compaction non plus : son segment est déjà dans sa demande, et lui ajouter
l'historique le ferait payer deux fois et résumer au-delà de sa coupe.

**Arguments :**

- `ctx` : L'agent.
- `run_id` : Le run.
- `session_id` : Sa session ; à défaut, `run_id`.
- `tenant_id` : Client de la session ; à défaut, le client par défaut.
- `writer` : Écrivain de la session (celui du parent pour un sous-run).

**Renvoie :**

L'état du run à l'arrêt : terminé (`COMPLETED`, `FAILED`,
`CANCELLED`) ou en attente (`PAUSED`, `WAITING_CHILD`).

**Lève :**

- `ClaimConflict` : Un autre pilote tient la concession du run.

## Fonction `loom_ia.engine.loop._effect`

```python
def _effect(event: Event) -> bool
```

L'événement est un effet du run, appel de modèle ou d'outil, et non l'appel d'un juge.

Un juge évalue une réponse ou un résultat sans en être la cause : ses
événements viennent après l'effet qu'il juge, et une reprise qui les
prendrait pour lui ne retrouverait plus le `tool.completed` d'un outil
terminal dont le juge a écrit avant la clôture.

Une source d'outils injoignable (`tool.source_unavailable`) n'est l'effet de
rien non plus : elle est écrite à l'ouverture des sources, y compris à la
reprise d'un run dont le dernier effet est le `tool.completed` d'un outil
terminal. Prise pour cause, elle masquait ce résultat, et la clôture levait
`RuntimeError` à chaque reprise (X10, `docs/state-et-context-analyse.md`).

## Fonction `loom_ia.engine.loop._closed_elsewhere`

```python
async def _closed_elsewhere(ctx: RunContext, run_id: RunId, tenant: TenantId, session: SessionId, moved: RunMoved) -> RunState
```

Le run a été clos par un autre process pendant qu'on le pilotait : le journal fait foi.

Un arrêt demandé ailleurs (`Loom.cancel`, REST ou MCP sur un autre process)
se voit à l'écriture suivante du pilote, qui s'arrête là, sans rien
réécrire après la clôture.

## Fonction `loom_ia.engine.loop._taken_over`

```python
async def _taken_over(ctx: RunContext, run_id: RunId, tenant: TenantId, session: SessionId, taken: ClaimConflict) -> RunState
```

Un autre worker a pris la concession en plein pilotage : le pilote s'arrête (point 27).

Son bail a expiré sans être renouvelé — store injoignable, process gelé —,
et l'autre a repris le run. Rien n'a été écrit depuis cette prise : ce qui
était en cours (un appel de modèle, un outil) est allé au bout ou a été
coupé, et son résultat n'est pas écrit ; le nouveau pilote traite l'appel
resté en suspens comme après un plantage (point 18). Si le run a été mené à son
terme entre-temps, le journal le dit et son état est rendu, comme pour un
run clos ailleurs ; sinon `ClaimConflict` remonte : il est mené ailleurs.

## Fonction `loom_ia.engine.loop._history_unreadable`

```python
async def _history_unreadable(state: RunState, journal: SessionWriter, scope: RunScope, error: Exception) -> RunState
```

Filet de `begin_run` : l'historique est devenu illisible depuis l'ouverture du run.

`begin_run` refuse d'ouvrir un run sur une session illisible
(`SessionUnreadable`). Reste le run déjà ouvert que la session a rattrapé
— une reprise après réparation ratée, un journal abîmé entre-temps — : il
est clos par un échec qui le dit, au lieu de rester ouvert.

## Fonction `loom_ia.engine.loop._driven`

```python
async def _driven(ctx: RunContext, state: RunState, tenant: TenantId, session: SessionId, journal: SessionWriter, scope: RunScope, claimed: RunClaim | None, earlier: Sequence[Event], cause: Event | None) -> RunState
```

Le pilotage proprement dit, concession prise ; `RunMoved` remonte à `drive`.

## Fonction `loom_ia.engine.loop._actionable`

```python
def _actionable(state: RunState) -> bool
```

Le run peut-il avancer par lui-même ?

Un run en pause ne le peut que si ses demandes d'approbation sont
tranchées — ou périmées, ce qui est une décision du journal et de personne
d'autre. Sinon il attend un humain, et `drive` n'a rien à y faire.

## Fonction `loom_ia.engine.loop._advances`

```python
async def _advances(state: RunState, ctx: RunContext, tenant: TenantId, session: SessionId) -> bool
```

Le run peut-il avancer ? Pour un parent, cela dépend de son enfant.

Parent et enfant partagent le journal de la session : le parent y relit
l'état de l'enfant plutôt que de le rejouer pour voir. Rien n'est écrit
tant que la décision n'est pas là, et une reprise pour rien ne coûte
qu'une lecture.

## Fonction `loom_ia.engine.loop._ready`

```python
def _ready(events: Sequence[Event], run_id: RunId) -> bool
```

Un run délégué peut-il repartir ? Récursif : un enfant peut en attendre un.

## Fonction `loom_ia.engine.loop._handed_back`

```python
async def _handed_back(write: Callable[[EventDraft], Awaitable[Event]], state: RunState, scope: RunScope, claim: RunClaim | None) -> None
```

Rend la concession d'un run laissé en plan volontairement (points 27 et 17).

Une concession expirée à l'écriture : le premier pilote venu peut
reprendre le run dès qu'une décision arrive, sans attendre la fin d'un
bail que plus personne ne renouvelle. Un run fini n'en a pas besoin —
plus rien ne le reprendra.

## Fonction `loom_ia.engine.loop._claim`

```python
async def _claim(state: RunState, ctx: RunContext, journal: SessionWriter, scope: RunScope) -> RunClaim | None
```

Prend la concession du run, ou lève si un autre pilote la tient (point 27).

Sans `worker_id` — un `drive` appelé directement, un sous-run que son
parent pilote déjà —, il n'y a pas de concession à prendre.

## Fonction `loom_ia.engine.loop._not_taken`

```python
def _not_taken(worker_id: str, run_id: RunId) -> Callable[[Sequence[Event]], None]
```

Garde d'une écriture de concession : un autre pilote l'a-t-il prise entre-temps ?

Le journal a avancé depuis la lecture de l'état, et l'écrivain s'apprête à
rejouer la concession derrière ce qui s'y est écrit. Si la dernière
concession posée sur ce run est celle d'un autre worker, et vivante, la
rejouer ferait deux pilotes : `ClaimConflict`, comme à la lecture. Sinon
— événements d'un autre run, concession expirée ou rendue —, le conflit
n'invalide rien.

## Fonction `loom_ia.engine.loop._renewed`

```python
@asynccontextmanager
async def _renewed(claim: RunClaim | None, journal: SessionWriter, scope: RunScope) -> AsyncGenerator[Callable[[], Awaitable[None]]]
```

Renouvelle la concession au tiers du bail, tant que le run est piloté.

Un minuteur, et pas un renouvellement entre deux étapes : un run bloqué
dans une étape plus longue que son bail est bien vivant, et perdrait sa
concession au profit d'un second pilote.

Un renouvellement raté — panne du store, journal écrit sans répit — est
journalisé et retenté au tour suivant : le bail couvre trois tours, donc
deux échecs de suite. Au-delà, un autre worker peut prendre le run ; la vue
du pilote (`SessionWriter.held`) refuse alors ses écritures, et le pilote
s'arrête à la suivante (point 27). Le minuteur ne s'arrête que si le run est
clos ailleurs, ou si un autre worker a pris la concession.

Il rend de quoi l'arrêter avant la fin du bloc : le pilote le fait avant de
restituer la concession, ou de sortir.

## Fonction `loom_ia.engine.loop.cancellation`

```python
def cancellation(state: RunState, *, reason: CancelReason='requested', by: str | None=None, via: str | None=None) -> list[EventDraft]
```

Transition et clôture d'un run arrêté à la demande (fonction A5).

L'annulation est terminale : un run annulé ne se reprend pas. C'est ce qui
la distingue d'un run simplement interrompu — plantage, flux abandonné —,
qui ne laisse rien au journal et repart où il s'était arrêté.

**Arguments :**

- `state` : L'état du run.
- `reason` : `requested` (arrêt demandé) ou `parent` (parent clos).
- `by` : Auteur de la demande : `user_id` de l'appelant, worker, run parent.
- `via` : Passerelle qui a porté la demande (`as_user`), s'il y en a une.

**Renvoie :**

Le `run.transitioned` vers `CANCELLED` et le `run.cancelled`
(consommation, demandes et questions retirées), à écrire ensemble.

## Fonction `loom_ia.engine.loop.subruns_cancellation`

```python
def subruns_cancellation(events: Sequence[Event], state: RunState) -> list[EventDraft]
```

Annulation des sous-runs encore ouverts d'un run que l'on clôt (fonctions C5 et A5).

Un sous-run ne repart que par l'appel de son parent, qui le reprend en le
rejouant : le parent clos — délai dépassé, arrêt demandé, échec —, plus rien ne le
reprendra. Resté ouvert, il figerait la frontière du snapshot de la session.
Chaque descendant ouvert est donc annulé avec son parent (`reason: parent`,
`by` : son run parent), le plus récent d'abord, donc un enfant avant son
propre parent. `events` : le journal de la session, ou l'arbre du run.

**Arguments :**

- `events` : Le journal de la session, ou l'arbre du run.
- `state` : L'état du run que l'on clôt.

**Renvoie :**

Les événements d'annulation des descendants ouverts.

## Fonction `loom_ia.engine.loop._open_subruns`

```python
async def _open_subruns(journal: SessionWriter, state: RunState) -> list[EventDraft]
```

Annulations des sous-runs encore ouverts d'un run qui va être clos, à écrire avec sa clôture.

Un sous-run n'existe que par un appel de son parent : sans appel en suspens, il n'y a
rien à fermer, et le journal n'est pas relu.

## Fonction `loom_ia.engine.loop._remaining`

```python
def _remaining(state: RunState, ctx: RunContext, decided: float=0.0) -> float | None
```

Secondes de pilotage qui restent au run, ou `None` s'il n'a pas de délai.

`decided` : temps des décisions déjà passées par ce pilote, hors étapes.

## Fonction `loom_ia.engine.loop._expired`

```python
async def _expired(write: Callable[[EventDraft], Awaitable[Event]], state: RunState, scope: RunScope, ctx: RunContext, *, cut: bool, decided: float=0.0, deciding: bool=False) -> None
```

Clôture d'un run qui a dépassé son délai maximal (fonction A6).

Un dépassement est subi, pas décidé : il s'écrit en `run.failed`, pas en
`run.cancelled`. Comme toute clôture, il ferme le run.

`cut` distingue les deux façons de dépasser : une étape coupée en plein
effet, ou un budget déjà épuisé avant même de commencer la suivante. Le
temps de l'étape coupée n'est nulle part — elle n'a pas de
`step.completed` —, donc le message ne le confond pas avec le cumul.
`deciding` : ce qui a été coupé n'est pas une étape mais une décision
(politiques, juges), qui n'en a pas ; `decided` : le temps des décisions
déjà passées, qui n'est pas dans `active_ms`.

## Fonction `loom_ia.engine.loop._release`

```python
async def _release(ctx: RunContext, state: RunState) -> None
```

`after_guards` : la réponse finale, contrôlée, part dans le flux une fois le run clos.

## Fonction `loom_ia.engine.loop.step`

```python
async def step(state: RunState, ctx: RunContext, previous: Sequence[Message]=(), *, cause: Event | None=None, session: Spent | None=None) -> AsyncGenerator[EventDraft]
```

Événements de la prochaine étape du run.

`previous` est l'historique de la session avant ce run ; `cause` le
dernier événement d'effet, référencé par les transitions ; `session` la
consommation des runs précédents de la session (budget de session).

**Arguments :**

- `state` : L'état du run.
- `ctx` : L'agent.
- `previous` : Historique de la session avant ce run.
- `cause` : Dernier événement d'effet.
- `session` : Consommation des runs précédents de la session.

**Produit :**

Les brouillons d'événements de l'étape : une décision (transition,
clôture), ou l'effet encadré par `step.started` et
`step.completed`.

## Fonction `loom_ia.engine.loop._decide`

```python
async def _decide(state: RunState, ctx: RunContext, scope: RunScope, cause: Event | None) -> list[EventDraft] | None
```

Transition ou clôture dues à l'état courant, ou None s'il reste un effet à exécuter.

Une réponse de l'orchestrateur passe d'abord par les politiques
`after_model`, une réponse finale par `on_output` : elles décident ici
de la suite, et une reprise les réévalue si leur décision n'a pas été
appliquée.

## Fonction `loom_ia.engine.loop._resumed`

```python
def _resumed(state: RunState, ctx: RunContext, scope: RunScope, cause: Event | None) -> list[EventDraft] | None
```

Ce qu'un run en pause peut faire des décisions qui lui sont parvenues (point 17).

Une demande dont la date est passée expire **ici**, au moment où on la
regarde : c'est l'`expire_at` du journal qui fait foi, et non le travail
différé qui devait la réveiller — perdu dans un redémarrage, il laisserait
sinon la demande approuvable indéfiniment.

Le run repart en `AWAITING_TOOLS` dès que plus rien n'attend : l'appel
approuvé est rejoué, l'appel refusé rend son motif au modèle. Si une
expiration est réglée sur `fail`, le run échoue au lieu de repartir.

## Fonction `loom_ia.engine.loop._review`

```python
async def _review(state: RunState, ctx: RunContext, scope: RunScope, cause: Event | None, answer: Message) -> list[EventDraft]
```

Suite d'une réponse de l'orchestrateur : politiques `after_model`, puis outils ou fin.

## Fonction `loom_ia.engine.loop._final`

```python
async def _final(state: RunState, ctx: RunContext, scope: RunScope, cause: Event | None, output: Message, terminal: ToolCallBlock | None) -> list[EventDraft]
```

Réponse finale : politiques `on_output`, puis clôture, réparation ou échec.

`terminal` : l'appel de l'outil terminal dont `output` est la sortie (point 13).

## Fonction `loom_ia.engine.loop._reset`

```python
async def _reset(ctx: RunContext, state: RunState) -> None
```

En diffusion `live`, la réponse refusée a déjà été diffusée : elle doit être effacée.

## Fonction `loom_ia.engine.loop._failure_type`

```python
def _failure_type(verdict: Verdict) -> str
```

Type d'erreur d'un échec : celui du guard qui l'a décidé, sinon celui de la politique.

## Fonction `loom_ia.engine.loop._structured`

```python
def _structured(contract: OutputContract | None, output: Message) -> JsonValue
```

Objet JSON de la réponse finale quand l'agent déclare un schéma de sortie (fonction A7).

## Fonction `loom_ia.engine.loop._stopping`

```python
def _stopping(verdict: Verdict, drafts: list[EventDraft]) -> EventDraft | None
```

Événement de la décision qui a arrêté la chaîne : cause de la transition qui suit.

## Fonction `loom_ia.engine.loop._repair`

```python
def _repair(state: RunState, ctx: RunContext, scope: RunScope, repair: PendingRepair, cause: Event | EventDraft | None) -> list[EventDraft]
```

Demande de réparation au modèle orchestrateur (point 20).

Les appels d'outils de la réponse refusée sont fermés sans être exécutés,
puis le diagnostic est écrit. Après une sortie terminale refusée, le run
revient à l'orchestrateur.

## Fonction `loom_ia.engine.loop._close_calls`

```python
def _close_calls(state: RunState, scope: RunScope, reason: str) -> list[EventDraft]
```

Résultats d'erreur pour les appels en attente, qui ne seront pas exécutés.

## Fonction `loom_ia.engine.loop._fail`

```python
def _fail(state: RunState, scope: RunScope, error_type: str, error: str, cause: Event | EventDraft | None, *, spent: tuple[Usage, float]=(Usage(), 0.0)) -> list[EventDraft]
```

Échec du run ; `spent` : consommation pas encore appliquée à `state` (juges, lot).

## Fonction `loom_ia.engine.loop._after_tools`

```python
def _after_tools(state: RunState, ctx: RunContext) -> RunStatus
```

État qui suit un lot d'outils : le modèle, ou la réponse forcée si le plafond est atteint.

## Fonction `loom_ia.engine.loop._transition`

```python
def _transition(state: RunState, scope: RunScope, target: RunStatus, cause: Event | EventDraft | str | None) -> EventDraft
```

`run.transitioned` vers `target` ; `cause` est l'événement qui l'a provoquée, ou son type.

## Fonction `loom_ia.engine.loop._terminal`

```python
def _terminal(state: RunState, ctx: RunContext) -> tuple[ToolCallBlock | None, tuple[ToolCallBlock, ...]]
```

Appel terminal dont la sortie devient la réponse finale (point 13), ou None.

Le second élément : les outils terminaux appelés avec d'autres, pour qui
la règle ne s'applique pas.

## Fonction `loom_ia.engine.loop._answered_with_state`

```python
def _answered_with_state(state: RunState, ctx: RunContext) -> Message | None
```

La réponse finale d'un tour qui n'appelait que `loom.state`, ou None (state v2, règle M1, `docs/state_analyse_moteur.md`).

Le modèle a écrit sa réponse et, dans la même réponse, tenu le state : le
rappeler lui ferait réécrire une réponse, qui remplacerait la bonne. Il
faut un texte non vide, que tous les appels du tour soient ceux de
`loom.state`, et que tous aient réussi sans écriture refusée — sinon le
modèle doit le savoir, et il est rappelé comme après tout outil. La
réponse rendue est le message du tour sans ses appels d'outils.

## Fonction `loom_ia.engine.loop._terminal_message`

```python
def _terminal_message(state: RunState) -> Message
```

Sortie de l'outil terminal, en réponse de l'assistant.

## Fonction `loom_ia.engine.loop._terminal_event`

```python
def _terminal_event(state: RunState, cause: Event | None) -> Event
```

`tool.completed` de l'outil terminal : c'est le dernier effet du run.

## Fonction `loom_ia.engine.loop._completed`

```python
def _completed(state: RunState, output: Message | None, *, terminal: Event | None=None, data: JsonValue=None, unverified: bool=False, fallback: bool=False, spent: tuple[Usage, float]=(Usage(), 0.0)) -> RunCompleted
```

Clôture ; `spent` : consommation des juges de la décision, pas encore appliquée.

Pour un outil terminal, sa sortie dit aussi si elle a été gardée malgré son
contrat ou remplacée par son repli : la clôture le reprend, comme la
projection (`unverified`, `fallback` et le statut `warning`).

## Fonction `loom_ia.engine.loop._policy_drafts`

```python
def _policy_drafts(scope: RunScope, events: Sequence[PolicyEvent], *, span_id: SpanId | None=None, parent_span_id: SpanId | None=None) -> list[EventDraft]
```

Événements des politiques d'un point, dans leur span.

Les appels de modèle d'un juge ont leur propre span, sous celui du point, au
nom de `judge:<nom>` : un span par appel (ses nouvelles tentatives, ses
bascules, puis sa réponse).

## Fonction `loom_ia.engine.loop.judge_role`

```python
def judge_role(judge: str) -> str
```

Rôle d'un juge dans l'enveloppe des événements : son coût lui est attribué (point 21).

## Classe `loom_ia.engine.loop._Step`

```python
class _Step
```

Encadrement d'une étape : numéro, span et chronométrage.

**Attributs :**

- `no` : Numéro de l'étape.
- `span` : Span de l'étape.

### Méthode `loom_ia.engine.loop._Step.draft`

```python
def draft(self, payload: StepStarted | StepCompleted | PolicyEvent) -> EventDraft
```

Brouillon dans le span de l'étape ; un appel de modèle est attribué à `main` ou à son juge.

### Méthode `loom_ia.engine.loop._Step.started`

```python
def started(self) -> EventDraft
```

Le `step.started` de l'étape.

### Méthode `loom_ia.engine.loop._Step.completed`

```python
def completed(self, emitted: int, *, ok: bool=True) -> EventDraft
```

Le `step.completed` de l'étape : durée, événements émis, issue.

## Fonction `loom_ia.engine.loop._model_step`

```python
async def _model_step(state: RunState, ctx: RunContext, scope: RunScope, previous: Sequence[Message], *, forced: bool, session: Spent) -> AsyncGenerator[EventDraft]
```

Étape d'appel du modèle orchestrateur (`model_call`, ou `finalize` pour la réponse forcée).

Construit la requête (historique, résultats dans l'ordre des appels et
marqués, notes d'amendement, state, consigne de réponse forcée), passe
les politiques `before_model`, appelle la chaîne `main` et écrit ses
événements dans le span de l'étape, puis la réponse.

## Fonction `loom_ia.engine.loop._tool_step`

```python
async def _tool_step(state: RunState, ctx: RunContext, scope: RunScope, cause: Event | None=None) -> AsyncGenerator[EventDraft]
```

Étape `tool_batch` : le lot d'outils en attente, par `ToolExecutor.run_batch`.

Chaque appel a son span, choisi d'avance ; les événements des rôles, des
juges et des sous-agents s'y rangent. Une décision `Fail` d'une politique
d'outil fait échouer le run à la fin du lot.

## Fonction `loom_ia.engine.loop.run_scope`

```python
def run_scope(state: RunState) -> RunScope
```

Portée où écrire les événements d'un run : son span, son arbre, son agent.

**Arguments :**

- `state` : L'état du run.

## Fonction `loom_ia.engine.loop._log_call`

```python
def _log_call(event: Event, payload: ModelResponded | ToolCompleted) -> None
```

Une ligne par appel de modèle ou d'outil (fonction K7, jalon J6.1b) : son coût, jamais son contenu.

Écrite par le process qui écrit l'événement, une fois : les logs d'un
service suivent ses appels sans collecteur. Un outil en erreur reste en
`INFO` — c'est un résultat que le modèle lira, pas une panne de loom.
