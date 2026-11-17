# loom_ia.engine.executor

Module `loom_ia.engine.executor` de loom_v2 (fichier `src/loom_ia/engine/executor.py`).

Exécution des appels d'outils d'un tour (points 12, 15 et 18 ; fonctions D1, D2, D4 et D5).

Chaîne d'un appel : outil connu, reprise sans risque, arguments lisibles,
références `$ref` résolues, arguments conformes au schéma, refus éventuel
d'un outil délégué, puis exécution avec timeout. Tout échec devient un
résultat d'erreur destiné au modèle ; seule une annulation demandée de l'extérieur
interrompt le lot : un outil qui s'annule lui-même a échoué, il n'arrête rien.
Une erreur de schéma sur une valeur venue d'une référence dit laquelle, ce
qu'elle a transmis et ce que le champ attend : le modèle ne reconnaîtrait
pas une valeur qu'il n'a pas écrite.

`tool.called` n'est écrit que pour un appel réellement lancé : c'est la
marque qu'un effet de bord a peut-être eu lieu. Il est écrit juste avant le
lancement, puis chaque `tool.completed` dès que son outil répond.

Ordre des appels (`order: effects`, par défaut) : les appels d'une réponse
partent par segments, dans l'ordre où le modèle les a écrits. Les appels sans
effet qui se suivent forment un segment et partent ensemble ; un appel à effet
(`side_effects`, ou l'évaluation de l'appel) forme un segment à lui seul : il
attend que tout ce qui le précède soit soldé, et ce qui le suit l'attend. Un
appel qui attend un humain (approbation) retient de même ceux qui le suivent :
ils restent en attente, sans événement, et partent au lot suivant ; ceux qui
exigent une approbation la demandent dans la même pause. La chaîne s'arrête sur
un run délégué en pause, un effet inconnu qu'un humain tranchera, et une
politique `after_tool` qui fait échouer le run. Avec `order: free`, tous
les appels partent ensemble, comme avant 2.0.20.

Un outil délégué (rôle) produit aussi des événements pendant son appel. Ils
passent par la même file que les résultats : ils précèdent toujours le
`tool.completed` de leur appel. Le délai par défaut des outils ne
s'applique pas à eux : les délais et le retry de leur modèle les bornent.
Un sous-agent écrit son propre run, par l'écrivain de la session ; son
`tool.called` nomme ce run enfant, et son `tool.completed` porte sa
consommation.

Fichiers (points 15 et 16) : avant d'écrire un résultat, les octets qu'il porte
(image renvoyée par un outil) sont rangés dans le stockage d'artefacts et
remplacés par leur référence, puis un résultat trop long est déporté. Chaque
fichier rangé donne un `artifact.stored`, écrit avant le `tool.completed`
de l'appel. Un outil délégué indisponible dans le run (rôle vision sans pièce
jointe, `artifact_read` sans déport) n'est ni montré au modèle ni appelable.

Politiques (point 2) : `before_tool` s'applique à un appel accepté, avant tout
lancement (arguments remplacés puis validés de nouveau, appel refusé, échec
du run) ; `after_tool` au résultat, avant ses fichiers et son déport
(résultat remplacé, refusé ou échec du run). Chaque décision part dans la file
du lot avant l'événement qu'elle concerne. Un appel déjà lancé et repris
après une interruption n'est pas réévalué : ses arguments remplacés sont
repris du journal. Un résultat refusé (`Retry`) est réparé par son auteur :
un rôle par son propre modèle, dans la même conversation (`repair`),
tant que la politique le demande ; les réparations se comptent par appel.
Un outil qui ne se répare pas renvoie son résultat à l'orchestrateur en
erreur, avec le diagnostic : c'est lui qui a écrit l'appel.

Diffusion (backlog #009) : un rôle terminal seul dans son lot diffuse sa
sortie en direct si l'agent diffuse en `live` (`on_chunk`).

State de la session (option C, `docs/state-et-context.md`) : un outil lit le state tel qu'au début de son
lot (`ToolContext.state`). Son résultat réussi, après les politiques
`after_tool` et avant ses fichiers et son déport, reçoit les écritures que
ses sources `from` et lui-même posent, contrôlées par `StateRules` ; un
résultat en erreur n'en pose aucune. Au rejeu, un résultat lu au journal
porte déjà les siennes : elles ne sont pas recalculées. Celui d'une doublure
est neuf : il les reçoit comme celui de l'outil.

Les décisions B-2, D-4 et D-6 citées ici sont celles du délai et des threads
des outils (`docs/moteur-delai-thread-analyse.md`) ; D3, D6 à D9 et D12
celles du state de session.

## Constantes de `loom_ia.engine.executor`

- `DEFAULT_TOOL_TIMEOUT = 30.0` : Délai par défaut d'un outil ordinaire, en secondes (`execution.tools.timeout`).
- `CallOrder = Literal['effects', 'free']` : Ordre des appels d'un même tour (`execution.tools.order`).  `effects` : un appel à effet attend ceux qui le précèdent et ceux qui le suivent l'attendent ; `free` : tous partent ensemble.
- `LATE_EFFECT = "Délai dépassé : pas de réponse en {limit:g} s, et l'outil n'a pas pu être arrêté. Son effet est d'état inconnu : il a …` : Message au modèle : délai dépassé sur un appel à effet dont le travail continue en thread (décision B-2).
- `REMOTE_EFFECT = "Délai dépassé : pas de réponse en {limit:g} s. Le serveur a été prévenu de l'abandon de l'appel, mais il a pu en produ…` : Message au modèle : délai dépassé, le serveur MCP a été prévenu sans qu'on sache s'il s'arrête (décision D-6).
- `STOPPED_EFFECT = "Délai dépassé : pas de réponse en {limit:g} s. L'outil a été arrêté en cours de route : il a pu produire tout ou parti…` : Message au modèle : délai dépassé, l'outil (asynchrone) a été arrêté en cours de route.
- `NOT_RUN = "Non exécuté : l'appel à {tool}, qui le précède dans ta réponse, a dépassé son délai et travaille encore. Cet appel n'e…` : Message au modèle : appel non lancé parce qu'un appel à effet qui le précède travaille encore.
- `NOT_RUN_SINCE = "Non exécuté : l'appel à {tool} d'un tour précédent a dépassé son délai et travaille encore. Cet appel n'est pas parti …` : Message au modèle : l'appel en retard est d'un tour précédent du run ; le lot suivant l'attend aussi.
- `NOT_STARTED = "Délai dépassé : l'outil n'a pas démarré en {limit:g} s, tous les threads des outils étant pris. Il n'a rien fait ; tu …` : Message au modèle : délai dépassé avant que l'outil démarre.  Tous les threads des outils étaient pris, et le travail, annulé dans la file, n'a rien fait.
- `UNKNOWN_STATE = "État inconnu : l'exécution de cet outil a été interrompue et il a peut-être produit son effet. Il n'a pas été relancé …` : Message au modèle : appel interrompu dont l'effet a peut-être eu lieu, non relancé (point 18).
- `AnyTool = Tool | DelegatedTool` : Un outil ordinaire (port `Tool`) ou délégué.
- `ToolEvent = ToolCalled | ToolCompleted | IdempotencyReused | Stored | Delegated | Decided | ApprovalRequested | ApprovalGranted | A…` : Ce que `run_batch` émet, dans l'ordre où le moteur l'écrit (`Waiting` n'est pas écrit).
- `_DECIDED = "C'est une décision, pas une panne : ne refais pas cet appel ; dis à l'utilisateur ce qui a été refusé et pourquoi."` : Ce qu'un refus demande au modèle : ne pas insister, et le dire.
- `_KINDS = {'string': 'du texte', 'object': 'un objet JSON', 'array': 'une liste', 'integer': 'un entier', 'number': 'un nombre', …` : Les types JSON Schema, dits au modèle.

## Classe `loom_ia.engine.executor.Delegated`

```python
@dataclass(frozen=True, slots=True)
class Delegated
```

Événement produit par un outil délégué pendant son appel.

**Attributs :**

- `call_id` : L'appel.
- `role` : Rôle qui a produit l'événement, recopié dans l'enveloppe.
- `payload` : L'événement.

## Classe `loom_ia.engine.executor.Stored`

```python
@dataclass(frozen=True, slots=True)
class Stored
```

Fichier rangé pendant un appel : sortie d'outil ou résultat déporté.

**Attributs :**

- `call_id` : L'appel.
- `payload` : L'événement `artifact.stored`.

## Classe `loom_ia.engine.executor.Decided`

```python
@dataclass(frozen=True, slots=True)
class Decided
```

Décision ou contrôle d'une politique d'outil, à écrire avant l'événement concerné.

**Attributs :**

- `call_id` : L'appel.
- `payload` : `policy.decided`, `guard.checked` ou un événement d'un juge.

## Classe `loom_ia.engine.executor._Ready`

```python
@dataclass(frozen=True, slots=True)
class _Ready
```

Appel accepté, prêt à partir.

**Attributs :**

- `call` : L'appel du modèle.
- `tool` : L'outil.
- `arguments` : Arguments après résolution des références.
- `refs` : Références résolues.
- `child_run_id` : Run enfant d'un sous-agent.
- `approved` : Vrai si un humain a approuvé cet appel **après** un lancement d'effet inconnu : il repart, y compris sur une réservation périmée (points 17 et 18). Un accord donné avant le premier lancement ne vaut pas cela.
- `replayed` : Vrai si le résultat vient du rejeu (journal, doublure ou refus), pas de l'outil — décidé une fois, à la préparation de l'appel.

## Classe `loom_ia.engine.executor.OpenedTools`

```python
@dataclass(frozen=True, slots=True)
class OpenedTools
```

Outils d'un run : ceux de l'agent, plus ceux de ses sources disponibles.

**Attributs :**

- `tools` : Les outils du run.
- `events` : À journaliser, dans l'ordre : disjoncteurs ouverts (`circuit.opened`), sources injoignables (`tool_source.unavailable`).

### Méthode `loom_ia.engine.executor.OpenedTools.unavailable`

```python
@property
def unavailable(self) -> tuple[ToolSourceUnavailable, ...]
```

Sources injoignables à l'ouverture.

## Classe `loom_ia.engine.executor._Crashed`

```python
@dataclass(frozen=True, slots=True)
class _Crashed
```

Exception sortie d'une tâche d'exécution : elle interrompt le lot.

## Classe `loom_ia.engine.executor._Unknown`

```python
@dataclass(frozen=True, slots=True)
class _Unknown
```

Appel dont l'effet est d'état inconnu (point 18) : il solde l'appel sans le conclure.

Ni résultat ni attente : ce que l'appel devient est décidé après le lot,
selon ce que son outil a déclaré en `on_unknown`.

## Classe `loom_ia.engine.executor.ToolReplay`

```python
class ToolReplay(Protocol)
```

Résultats d'outils servis à part, au lieu d'exécuter (rejeu, jalons J6.2a et J6.2b ; évals, jalon J6.3a).

`serves` dit, une fois par appel et avant tout lancement, si le rejeu
sert lui-même cet appel. Au rejeu identique, ce sont tous les outils qui
touchent le monde (Python, MCP) et les sous-agents ; un rôle n'en fait
jamais partie — il est de la logique de l'agent, et son appel de modèle
passe par un client qui rejoue. En variante, cela dépend aussi de l'appel :
retrouvé au journal, doublé, ou refusé parce qu'il a des effets de bord ;
sinon, l'outil s'exécute pour de vrai. Pendant une éval, un outil à effets
de bord est doublé ou refusé, les autres s'exécutent.

`output` rend le résultat servi et la consommation qu'il portait (celle
d'un sous-agent). Il reçoit les arguments tels que le modèle les a écrits
(`arguments` : ceux que le journal garde, que le rejeu compare) et ceux
que l'outil aurait reçus (`resolved` : références résolues, arguments
remplacés par une politique ou une approbation) — ceux d'une doublure.
`from_journal` dit, après `output`, si ce résultat est celui du
journal : il porte alors le state que le run d'origine a posé.

### Méthode `loom_ia.engine.executor.ToolReplay.serves`

```python
def serves(self, tool: AnyTool, call: PendingCall, run_id: RunId) -> bool
```

Vrai si le rejeu sert lui-même cet appel, au lieu de l'exécuter.

**Arguments :**

- `tool` : L'outil.
- `call` : L'appel du modèle.
- `run_id` : Le run.

### Méthode `loom_ia.engine.executor.ToolReplay.from_journal`

```python
def from_journal(self, run_id: RunId, call_id: str) -> bool
```

Le résultat servi pour cet appel vient du journal, avec ses écritures du state.

Sinon — doublure, refus —, il est neuf : le state se pose dessus comme
sur le résultat de l'outil (sources `from`, contrôles).

### Méthode `loom_ia.engine.executor.ToolReplay.output`

```python
async def output(self, run_id: RunId, call_id: str, name: str, arguments: Mapping[str, JsonValue], resolved: Mapping[str, JsonValue]) -> tuple[ToolOutput, Consumption | None]
```

Résultat servi pour un appel, et la consommation qu'il portait.

**Arguments :**

- `run_id` : Le run.
- `call_id` : L'appel.
- `name` : Nom de l'outil.
- `arguments` : Arguments tels que le modèle les a écrits.
- `resolved` : Arguments que l'outil aurait reçus.

**Renvoie :**

Le résultat et la consommation d'un sous-agent, ou `None`.

### Méthode `loom_ia.engine.executor.ToolReplay.approve`

```python
async def approve(self, run_id: RunId, pending: PendingApproval) -> ApprovalDecision
```

Décision en ligne d'une demande d'approbation : un rejeu ne s'arrête pas pour attendre.

L'appel est désigné par son run et son `call_id` — un identifiant
d'appel n'est unique que dans son run.

### Méthode `loom_ia.engine.executor.ToolReplay.answer`

```python
async def answer(self, run_id: RunId, pending: PendingQuestion) -> QuestionReply | NoAnswer
```

Réponse en ligne à une question (`ask`) : celle du journal, au rejeu.

## Classe `loom_ia.engine.executor.ToolExecutor`

```python
class ToolExecutor
```

Outils disponibles pour un run, et exécution de leurs appels.

`artifacts` est le stockage des fichiers du run : pièces jointes, sorties
d'outils, résultats déportés. Avec lui, l'outil intégré `artifact_read`
est déclaré. `offload_over` est le seuil de déport des outils qui n'en
fixent pas ; `None` le désactive.

`breakers` : disjoncteurs des sources (point 19, backlog #011), réglés par
`circuits` (nom de la source → réglage, `None` pour aucun ; réglage
par défaut pour une source absente). Sans eux, pas de disjoncteur.

`state_rules` : clés du state que l'agent déclare (option C) ; sans
elles, un outil ne voit pas de state et toute écriture est refusée.

L'exécuteur d'un agent est construit au montage ; `opened` en tire, pour
chaque run, une copie qui ajoute les outils de ses sources.

**Attributs :**

- `default_timeout` : Délai des outils qui n'en fixent pas, en secondes.
- `order` : Ordre des appels d'un même tour.
- `grace` : Attente au plus du travail en thread d'un appel à effet, après son délai (décision B-2).
- `threads` : Pool des outils synchrones (décision D-4).
- `state_rules` : Clés du state de l'agent.
- `approval` : Délai et effet d'une expiration des demandes d'approbation (point 17).
- `approvers` : Qui tranche les demandes de cet agent : figé dans chacune (`docs/loom_role_user.md` §5.3).
- `denied` : Outils retirés à l'appelant, par le nom que voit le modèle.
- `imposed` : Approbations imposées par l'appelant, par nom d'outil.
- `idempotency` : Magasin d'idempotence partagé (point 49) ; `None` : celui du journal du run.
- `validate_arguments` : Faux pour ne pas contrôler les arguments contre le schéma.
- `artifacts` : Magasin des fichiers du run.
- `offload_over` : Seuil de déport, en caractères ; `None` : jamais.
- `breakers` : Disjoncteurs partagés de l'instance.
- `circuits` : Réglage du disjoncteur de chaque source.
- `sources` : Sources ouvertes au début de chaque run (serveurs MCP, point 19).
- `replay` : Rejeu (jalon J6.2a) : posé sur l'exécuteur d'un agent monté pour rejouer.

### Méthode `loom_ia.engine.executor.ToolExecutor.__init__`

```python
def __init__(self, tools: Iterable[AnyTool]=(), *, sources: Iterable[ToolSource]=(), default_timeout: float | None=DEFAULT_TOOL_TIMEOUT, validate_arguments: bool=True, artifacts: ArtifactStore | None=None, offload_over: int | None=DEFAULT_OFFLOAD_OVER, breakers: CircuitBreakers | None=None, circuits: Mapping[str, CircuitBreaker | None] | None=None, approval: ApprovalSettings | None=None, approvers: Approvers | None=None, idempotency: IdempotencyStore | None=None, denied: Iterable[str]=(), imposed: Mapping[str, Approval] | None=None, state_rules: StateRules | None=None, order: CallOrder='effects', grace: float | None=tool_threads.DEFAULT_GRACE, threads: tool_threads.ToolThreads | None=None) -> None
```

Déclare les outils et règle l'exécution.

**Arguments :**

- `tools` : Outils de l'agent ; `artifact_read` s'y ajoute si `artifacts` est donné.
- `sources` : Sources d'outils ouvertes à chaque run (serveurs MCP).
- `default_timeout` : Délai des outils qui n'en fixent pas (30 s).
- `validate_arguments` : Contrôler les arguments contre le schéma.
- `artifacts` : Magasin des fichiers du run.
- `offload_over` : Seuil de déport (50 000 caractères) ; `None` le désactive.
- `breakers` : Disjoncteurs partagés de l'instance.
- `circuits` : Réglage du disjoncteur de chaque source.
- `approval` : Délai et effet d'une expiration des approbations.
- `approvers` : Qui tranche les demandes de l'agent.
- `idempotency` : Magasin d'idempotence partagé.
- `denied` : Outils retirés à l'appelant.
- `imposed` : Approbations imposées par l'appelant.
- `state_rules` : Clés du state de l'agent.
- `order` : Ordre des appels d'un même tour.
- `grace` : Attente au plus du travail en thread d'un appel à effet après son délai ; `None` : sans borne.
- `threads` : Pool des outils synchrones : celui de l'instance `Loom`, sinon un pool propre, dont les threads ne naissent qu'au premier travail.

**Lève :**

- `ValueError` : Deux outils du même nom.

### Méthode `loom_ia.engine.executor.ToolExecutor.opened`

```python
@asynccontextmanager
async def opened(self, context: SourceContext) -> AsyncGenerator[OpenedTools]
```

Outils du run, jusqu'à la sortie du contexte.

Chaque source est ouverte dans l'ordre de déclaration. Une source
injoignable est écartée et signalée, les autres restent utilisables.
Un outil de source dont le nom ou le schéma pose problème est écarté,
avec un avertissement.

Disjoncteur : une source écartée n'est pas contactée ; une connexion
ratée compte un échec, et celle qui ouvre le disjoncteur est signalée
(`circuit.opened`) ; une connexion réussie remet le compte à zéro.

**Arguments :**

- `context` : Contexte de l'ouverture (run, client, session) donné aux sources.

**Produit :**

Les outils du run et les événements à journaliser ; les sources
sont fermées à la sortie.

### Méthode `loom_ia.engine.executor.ToolExecutor._copy`

```python
def _copy(self) -> ToolExecutor
```

Mêmes outils et réglages, sans les sources : base des outils d'un run.

### Méthode `loom_ia.engine.executor.ToolExecutor.add`

```python
def add(self, tool: AnyTool) -> None
```

Déclare un outil ; un outil retiré à l'appelant (`denied`) est ignoré.

**Arguments :**

- `tool` : L'outil.

**Lève :**

- `ValueError` : Un outil du même nom est déjà déclaré.
- `jsonschema.SchemaError` : Le schéma des arguments est invalide.

### Méthode `loom_ia.engine.executor.ToolExecutor.get`

```python
def get(self, name: str) -> AnyTool | None
```

L'outil de ce nom, ou `None`.

### Méthode `loom_ia.engine.executor.ToolExecutor.names`

```python
@property
def names(self) -> tuple[str, ...]
```

Noms des outils déclarés, dans l'ordre où ils ont été ajoutés.

### Méthode `loom_ia.engine.executor.ToolExecutor.shows_refs`

```python
def shows_refs(self, run: RunView) -> bool
```

Vrai si les références `$ref` sont montrées : un rôle, un sous-agent ou `loom.state`.

`loom.state` désigne un résultat par sa référence : sans elles, le modèle
ne saurait pas quoi lui donner (écart E4 du state v2, `docs/state_v2_analyse.md`).

Un outil masqué (rôle vision sans pièce jointe, sous-agent au-delà de la
profondeur permise) ne compte pas.

### Méthode `loom_ia.engine.executor.ToolExecutor.specs`

```python
@property
def specs(self) -> tuple[ToolSpec, ...]
```

Descriptions des outils déclarés, dans l'ordre.

### Méthode `loom_ia.engine.executor.ToolExecutor.view`

```python
def view(self, state: RunState, *, writer: SessionWriter | None=None, scope: RunScope | None=None, spans: Mapping[str, SpanId] | None=None, step_span: SpanId | None=None, turns: tuple[tuple[Message, ...], ...]=(), summary: str | None=None, session_state: Mapping[str, JsonValue] | None=None) -> RunView
```

Ce que les outils délégués voient du run.

**Arguments :**

- `state` : L'état du run.
- `writer` : Écrivain du journal.
- `scope` : Run qui écrit.
- `spans` : Span de chaque appel du lot.
- `step_span` : Span de l'étape.
- `turns` : Historique de la session, par tour.
- `summary` : Dernier résumé de compaction.
- `session_state` : State de la session.

**Renvoie :**

La vue, avec le magasin de fichiers de l'exécuteur.

### Méthode `loom_ia.engine.executor.ToolExecutor.definitions`

```python
def definitions(self, run: RunView | None=None) -> tuple[ToolDefinition, ...]
```

Ce que le modèle voit des outils, dans l'ordre de déclaration.

Avec `run`, les outils délégués indisponibles dans ce run sont écartés.

**Arguments :**

- `run` : La vue du run, ou `None` pour tous les outils.

### Méthode `loom_ia.engine.executor.ToolExecutor.run_batch`

```python
async def run_batch(self, state: RunState, *, writer: SessionWriter | None=None, scope: RunScope | None=None, spans: Mapping[str, SpanId] | None=None, step_span: SpanId | None=None, policies: Policies | None=None, on_chunk: ChunkCallback | None=None, turns: tuple[tuple[Message, ...], ...]=(), summary: str | None=None, approver: Approver | None=None, session_state: Mapping[str, JsonValue] | None=None, answerer: Answerer | None=None) -> AsyncGenerator[ToolEvent]
```

Traite les appels en attente du run et émet leurs événements.

`writer`, `scope`, `spans` et `step_span` situent le run dans
le journal : le journal où écrire (les sous-agents y écrivent le
leur), la portée de ses événements, le span de chaque appel et celui
de l'étape qui les porte. `policies` : celles de l'agent.
Une décision `Fail` à `before_tool` arrête le lot avant tout
lancement. `on_chunk` : diffusion en direct, pour un rôle terminal
seul dans son lot. `approver` : approbateur en ligne (point 28) — il
tranche ici même, et le run ne passe jamais par `PAUSED` ; les
arguments qu'il corrige sont validés comme ceux du modèle. Au rejeu,
c'est le rejeu qui tranche (`ToolReplay.approve`). `session_state` :
le state de la session à l'ouverture du run ; les outils voient en plus
ce que le run a écrit avant ce lot.

**Arguments :**

- `state` : L'état du run ; ses appels en attente forment le lot.
- `writer` : Écrivain du journal.
- `scope` : Run qui écrit.
- `spans` : Span de chaque appel du lot.
- `step_span` : Span de l'étape.
- `policies` : Politiques de l'agent.
- `on_chunk` : Diffusion en direct d'un rôle terminal.
- `turns` : Historique de la session, par tour.
- `summary` : Dernier résumé de compaction.
- `approver` : Approbateur en ligne.
- `session_state` : State de la session à l'ouverture du run.
- `answerer` : Répondeur en ligne aux questions (`ask`).

**Produit :**

Les événements du lot, dans l'ordre où le moteur les écrit :
décisions des politiques, `tool.called`, événements des outils
délégués, fichiers rangés, `tool.completed` ; puis demandes
d'approbation et questions qui mettent le run en pause, ou
`Waiting` pour un sous-agent qui attend.

### Méthode `loom_ia.engine.executor.ToolExecutor._lingering`

```python
async def _lingering(self, lingering: dict[str, tool_threads.CallThreads]) -> tuple[str, str] | None
```

Attend le travail encore en cours d'appels à effet ; l'appel et son outil s'il dure.

L'attente est bornée par `grace` ; sans borne (`None`), elle dure le
temps qu'il faut. Un travail fini est oublié.

### Méthode `loom_ia.engine.executor.ToolExecutor._ask`

```python
async def _ask(self, call: PendingCall, prepared: _Ready, state: RunState, answerer: Answerer | None, held: list[QuestionAsked]) -> AsyncGenerator[ToolEvent]
```

Un appel à `ask` : sa réponse, ou sa question à écrire (`docs/wait_answer.md`).

Une réponse déjà au journal, ou donnée en ligne par `answerer`, part
au modèle ; sinon la question rejoint `held` et le run attendra en
`PAUSED`.

### Méthode `loom_ia.engine.executor.ToolExecutor._prepare`

```python
async def _prepare(self, call: PendingCall, tool: AnyTool, view: RunView) -> _Ready | str
```

Appel prêt à partir, ou motif de refus destiné au modèle.

### Méthode `loom_ia.engine.executor.ToolExecutor._before_tool`

```python
async def _before_tool(self, item: _Ready, view: RunView, policies: Policies) -> Verdict
```

Politiques `before_tool` d'un appel accepté.

Un appel repris après une interruption n'est pas réévalué : ses
arguments remplacés sont repris tels qu'ils ont été journalisés.

### Méthode `loom_ia.engine.executor.ToolExecutor._corrected`

```python
def _corrected(self, name: str, arguments: dict[str, JsonValue]) -> str | None
```

Le refus destiné au modèle si les arguments corrigés par l'approbateur sont refusés.

Comme ceux du modèle, ils doivent respecter le schéma de l'outil avant
de partir (sauf `validate_arguments=False`). L'accord est déjà au
journal : l'appel se conclut en erreur, qui dit ce que l'approbateur a
écrit et ce que le schéma refuse.

### Méthode `loom_ia.engine.executor.ToolExecutor._schema_errors`

```python
def _schema_errors(self, name: str, arguments: dict[str, JsonValue], *, written: tuple[dict[str, JsonValue], ResultIndex] | None=None) -> str | None
```

Le refus destiné au modèle, ou `None` si les arguments sont conformes.

`written` : les arguments tels que le modèle les a écrits, et les
résultats du run — une erreur sur une valeur venue d'une référence le
dit, sans quoi le modèle lit une valeur qu'il n'a pas écrite.

### Méthode `loom_ia.engine.executor.ToolExecutor._execute`

```python
async def _execute(self, item: _Ready, view: RunView, emit: Callable[[ToolEvent | _Unknown], None], policies: Policies | None=None, tally: BatchTally | None=None, lingering: dict[str, tool_threads.CallThreads] | None=None) -> None
```

Exécute un appel ; ses événements et son résultat partent dans la file du lot.

`tally` : le state que le lot laissera, compté dans l'ordre des appels.
`lingering` : reçoit le travail en thread d'un appel à effet qui a dépassé
son délai et tourne encore (décision B-2) ; la chaîne l'attendra.

### Méthode `loom_ia.engine.executor.ToolExecutor._posed`

```python
def _posed(self, output: ToolOutput, tool: str, view: RunView, tally: BatchTally, call_id: str) -> ToolOutput
```

Le résultat avec les écritures du state qu'il pose (décisions D6 à D8 et D12 du state de session).

Un résultat en erreur n'en pose aucune, même s'il en portait avant
qu'une politique ne le refuse. Un sous-run n'écrit pas le state de la
session (décision D3), ni un agent qui n'en déclare pas : ce qu'un outil y écrit
est alors retiré sans bruit. Un outil qui n'écrit rien et qu'aucune
source ne désigne garde son résultat tel quel.

### Méthode `loom_ia.engine.executor.ToolExecutor._idempotency`

```python
def _idempotency(self, call_id: str, tool_name: str, view: RunView) -> IdempotencyStore | None
```

Magasin donné à cet appel : le partagé s'il y en a un, sinon le journal.

Sans écrivain ni portée — outil appelé hors moteur —, il n'y en a pas :
un outil décoré s'exécute alors tel quel.

### Méthode `loom_ia.engine.executor.ToolExecutor._after_tool`

```python
async def _after_tool(self, item: _Ready, output: ToolOutput, exchange: Exchange | None, context: ToolContext, view: RunView, emit: Callable[[ToolEvent], None], policies: Policies) -> ToolOutput
```

Politiques `after_tool` : résultat gardé, remplacé, réparé ou refusé.

Un rôle refusé (`Retry`) est réparé par son modèle, puis contrôlé de
nouveau ; ses réparations se comptent dans l'appel. Un `Fail` laisse
le résultat tel quel : le run échouera à la fin du lot.

### Méthode `loom_ia.engine.executor.ToolExecutor._settle`

```python
async def _settle(self, output: ToolOutput, spec: ToolSpec, call_id: str, view: RunView, emit: Callable[[ToolEvent], None]) -> ToolOutput
```

Résultat prêt pour le journal : fichiers rangés, contenu trop long déporté.

### Méthode `loom_ia.engine.executor.ToolExecutor._store_files`

```python
async def _store_files(self, output: ToolOutput, call_id: str, state: RunState, emit: Callable[[ToolEvent], None]) -> ToolOutput
```

Octets des blocs rangés dans le stockage, remplacés par leur référence (fonction G3).

Chaque fichier rangé émet un `Stored` (`artifact.stored`).

### Méthode `loom_ia.engine.executor.ToolExecutor._offload`

```python
async def _offload(self, output: ToolOutput, limit: int, spec: ToolSpec, call_id: str, view: RunView, emit: Callable[[ToolEvent], None]) -> ToolOutput
```

Contenu complet rangé à part, aperçu à la place (point 16) ; tronqué sans stockage.

## Fonction `loom_ia.engine.executor._invoked`

```python
async def _invoked(tool: Tool, arguments: dict[str, JsonValue], context: ToolContext, limit: float | None, state: RunState, *, effects: bool=False, threads: tool_threads.CallThreads | None=None) -> ToolOutput
```

Exécute un outil ordinaire avec son délai ; toute erreur devient un résultat d'erreur.

Un délai dépassé sur un appel à effet n'est pas un échec : l'effet a pu avoir
lieu, ou aura lieu (un thread ne s'arrête pas, un serveur MCP continue). Il
est d'état inconnu (`UnknownEffect`), et l'outil dit ce qu'il en advient
(`on_unknown`), comme à la reprise d'un appel interrompu (décision B-2).

## Fonction `loom_ia.engine.executor._delegated`

```python
async def _delegated(produced: AsyncGenerator[DelegatedPayload | Consumption | Exchange | ToolOutput | Waiting], tool: DelegatedTool, context: ToolContext, emit: Callable[[ToolEvent], None], limit: float | None, state: RunState) -> tuple[ToolOutput | Waiting, Consumption | None, Exchange | None]
```

Déroule un outil délégué (appel ou réparation) avec son délai.

Ses événements sont émis ; son résultat, sa consommation et son échange
sont renvoyés. Toute erreur devient un résultat d'erreur. Un run délégué
qui attend une décision humaine rend `Waiting` : pas de résultat.

## Fonction `loom_ia.engine.executor._writes`

```python
def _writes(item: _Ready) -> bool
```

Vrai si l'appel a un effet : l'évaluation de l'appel, sinon la déclaration de l'outil.

Ce qui sert à approuver et à reprendre sert aussi à ordonner : un outil qui
écrit sans le déclarer passe pour une lecture.

## Fonction `loom_ia.engine.executor._segments`

```python
def _segments(ready: list[_Ready]) -> list[list[_Ready]]
```

Les appels prêts, en segments à lancer l'un après l'autre, dans l'ordre de la réponse.

Un appel à effet forme un segment à lui seul ; les appels sans effet qui se
suivent en forment un, et partent ensemble.

## Classe `loom_ia.engine.executor._Blocked`

```python
class _Blocked
```

Ce que les appels en attente d'un humain retiennent derrière eux.

Un appel à effet en attente retient tous ceux qui le suivent ; un appel sans
effet en attente retient seulement les appels à effet qui le suivent. Un
appel retenu retient à son tour, de la même façon.

### Méthode `loom_ia.engine.executor._Blocked.hold`

```python
def hold(self, item: _Ready) -> None
```

Cet appel attend (un humain, ou un appel d'avant) : il retient ce qui le suit.

### Méthode `loom_ia.engine.executor._Blocked.holds`

```python
def holds(self, item: _Ready) -> bool
```

Vrai si cet appel doit attendre un appel d'avant.

## Fonction `loom_ia.engine.executor._always_asked`

```python
def _always_asked(item: _Ready, imposed: Mapping[str, Approval]) -> bool
```

Vrai si l'appel demande une approbation quoi que disent les politiques.

La même règle que `_asks_approval` sans politique : la consigne de
l'appelant, sinon l'évaluation de l'appel, sinon la déclaration.

## Fonction `loom_ia.engine.executor._retry_safe`

```python
def _retry_safe(tool: AnyTool, arguments: Mapping[str, JsonValue]) -> bool
```

Vrai si refaire cet appel est sans risque : l'évaluation de l'appel, sinon la déclaration.

## Fonction `loom_ia.engine.executor._asks_approval`

```python
def _asks_approval(spec: ToolSpec, verdict: Verdict, imposed: Mapping[str, Approval] | None=None, tenant: str | None=None, *, assessment: CallAssessment | None=None) -> tuple[str, str | None] | None
```

Motif et politique de l'approbation à demander, ou None s'il n'y en a pas.

Trois sources : la déclaration de l'outil (`approval: always`), que rien
ne lève — une politique qui rend `Continue` ne l'a pas levée —, une
consigne de l'appelant qui la remplace (`imposed`), et une politique
`before_tool` qui rend `Pause`, laquelle peut en exiger une sur
n'importe quel outil.

Le motif dit laquelle des deux premières s'applique : un approbateur qui lit
« outil déclaré à approbation obligatoire » pour un outil qui ne l'est pas
cherche la raison au mauvais endroit (essai de la fonction D7, 18/10).

Un outil qui évalue ses appels (`assessment`) remplace sa déclaration
pour cet appel : c'est lui qui dit s'il faut approuver, et pourquoi. La
consigne de l'appelant garde le dernier mot.

## Fonction `loom_ia.engine.executor._request`

```python
def _request(item: _Ready, asked: tuple[str, str | None], settings: ApprovalSettings, approvers: Approvers) -> ApprovalRequested
```

Demande d'approbation d'un appel, prête à écrire, avec qui peut la trancher (`docs/loom_role_user.md` §5.3).

## Fonction `loom_ia.engine.executor._decision`

```python
def _decision(request: ApprovalRequested, decided: ApprovalDecision) -> ApprovalGranted | ApprovalRejected
```

Décision d'un approbateur en ligne, telle qu'elle s'écrit au journal.

## Fonction `loom_ia.engine.executor._refused`

```python
def _refused(tool_name: str, verdict: str, reason: str, by: str | None=None) -> str
```

Ce que le modèle lit d'un appel qu'une approbation n'a pas autorisé.

À l'essai de la fonction D12 du 18/10, « Appel à forge__forge refusé : test D12, pas de VM »
a été lu comme une panne : Haiku a relancé l'appel, puis conclu que « le
service de forge a refusé ». Le texte dit donc qui a tranché, que c'est
une décision, et ce que le modèle doit en faire.

## Fonction `loom_ia.engine.executor._already_refused`

```python
def _already_refused(state: RunState, item: _Ready) -> str | None
```

Texte d'erreur si ce run a déjà vu refuser ce même appel, mêmes arguments.

Les arguments comparés sont ceux de la demande, après les politiques
`before_tool` : ceux que l'approbateur a vus. Seul un refus compte —
une demande expirée peut être refaite, personne n'a dit non.

## Fonction `loom_ia.engine.executor._from_ref`

```python
def _from_ref(error: ValidationError, origin: RefOrigin, results: ResultIndex) -> str
```

Une erreur de schéma sur une valeur venue d'une référence, dite au modèle.

Le message de `jsonschema` recopie la valeur fautive : le résultat
transmis, que le modèle n'a pas écrit et ne reconnaît pas. On lui dit
d'où elle vient, ce qu'elle est, et ce que le champ attend.

## Classe `loom_ia.engine.executor._RunResults`

```python
class _RunResults
```

Les résultats du run, en lecture pour un outil (`ToolContext.results`, state v2).

### Méthode `loom_ia.engine.executor._RunResults.read`

```python
async def read(self, ref: str) -> JsonValue
```

Contenu du résultat que désigne `ref` (`result:N`).

**Lève :**

- `ToolError` : Référence impossible à résoudre ; le message est destiné au modèle.

## Fonction `loom_ia.engine.executor._as_served`

```python
def _as_served(output: ToolOutput, served: ToolOutput) -> ToolOutput
```

Résultat d'un appel lu au journal : les écritures du state sont les siennes.

Les politiques `after_tool` tournent de nouveau sur le résultat servi, et
peuvent en rendre un neuf (repli d'un contrat) : le state que le journal
avait retenu y est remis, sans être recalculé. Un résultat en erreur n'en
porte pas.

## Fonction `loom_ia.engine.executor._batch_tally`

```python
def _batch_tally(state: RunState, current: Mapping[str, JsonValue]) -> BatchTally
```

Compte du lot : le state au début du lot, puis les voisins déjà soldés à leur rang (décisions D7 et D9 du state de session).

Le rang d'un appel est sa place dans la réponse du modèle, comme dans la
projection ; à la reprise d'un lot interrompu, les appels déjà soldés au
journal y sont reportés, pour que les suivants les comptent.

## Fonction `loom_ia.engine.executor._batch_turn`

```python
def _batch_turn(state: RunState) -> int
```

Position de la réponse du modèle dont le lot s'exécute : la dernière de l'assistant.

## Fonction `loom_ia.engine.executor._offered`

```python
def _offered(tool: AnyTool, run: RunView) -> bool
```

Vrai si l'outil est montré au modèle et appelable dans ce run.

## Fonction `loom_ia.engine.executor._cancelled`

```python
def _cancelled(name: str, exc: asyncio.CancelledError, state: RunState) -> ToolOutput
```

Résultat d'un outil annulé de l'intérieur, sans que le lot ait été arrêté.

## Fonction `loom_ia.engine.executor._unprepared`

```python
def _unprepared(name: str, exc: Exception, state: RunState) -> str
```

Refus destiné au modèle d'un appel dont la préparation a levé une exception imprévue.

## Fonction `loom_ia.engine.executor._completed`

```python
def _completed(call: PendingCall, output: ToolOutput, *, started: float | None, consumption: Consumption | None=None) -> ToolCompleted
```

`tool.completed` d'un appel : résultat, durée, taille en octets du JSON, consommation d'un sous-agent.

**Arguments :**

- `call` : L'appel.
- `output` : Son résultat.
- `started` : Instant du lancement (`time.perf_counter`) ; `None` pour un appel conclu sans lancement (durée 0).
- `consumption` : Consommation d'un sous-agent.
