# loom_ia.replay.book

Module `loom_ia.replay.book` de loom_v2 (fichier `src/loom_ia/replay/book.py`).

Ce qu'un run a reçu du monde, relu dans son journal pour le lui resservir (fonction K6, point 31).

Le rejeu identique refait tourner la logique de loom — boucle, politiques,
contrats, juges, budgets — et lui sert, au lieu du monde, ce que le journal a
gardé : les réponses des modèles, les résultats des outils, les décisions
d'approbation. Le **livre** (`ReplayBook`) tient ces réponses et note la
première divergence :

- un appel de modèle est servi par son **empreinte** (`request_hash`) : une
  requête qui n'en a aucune au journal diverge, et le livre dit quelle partie
  diffère quand le journal porte `request_parts` (jalon J6.2a) ;
- un appel d'outil est servi par son `call_id` — au rejeu identique, les
  réponses du modèle sont celles du journal, donc ses appels aussi —, et son
  nom et ses arguments doivent être les mêmes ;
- une demande d'approbation reçoit la décision du journal ;
- un run fini sur une **panne de modèle** (`run.failed`, `model.*`) se
  la voit resservir : une fois ses réponses toutes rejouées, l'appel suivant
  lève la même erreur, sans diverger (essai de la fonction O3, 23/10 — l'appel en panne
  passait pour un appel en trop, et un tel run ne se rejouait jamais).

Au rejeu identique, après la première divergence, plus rien n'est servi :
continuer obligerait à deviner quelle réponse enregistrée irait à quelle
requête nouvelle. En variante (J6.2b, `replay.variant`), le même livre sert
toute requête identique, avant comme après, et ce qu'il ne connaît pas part
pour de vrai ; un outil s'y retrouve par son nom et ses arguments.

## Constantes de `loom_ia.replay.book`

- `DivergenceKind = Literal['model', 'tool', 'approval', 'question', 'end']` : Ce qui peut diverger : un appel de modèle ou d'outil, une approbation, une question, ou la fin du run.
- `PARTS = ('model', 'system', 'tools', 'messages', 'settings')` : Noms des parties d'une requête, dans l'ordre où le diagnostic les cite.
- `STOPPED = 'rejeu arrêté : une divergence a été trouvée plus haut'` : Erreur servie à tout appel qui suit la première divergence d'un rejeu identique.
- `MODEL_FAILURE = 'model.'` : Préfixe du type d'erreur d'un run arrêté par une panne de modèle (`model.overloaded`).

## Classe `loom_ia.replay.book.ReplayError`

```python
class ReplayError(ValueError)
```

Ce run ne peut pas être rejoué (introuvable, inachevé, sous-run, variante mal dite…).

## Classe `loom_ia.replay.book.Divergence`

```python
@dataclass(frozen=True, slots=True)
class Divergence
```

Le premier endroit où le rejeu ne retrouve pas le journal.

**Attributs :**

- `kind` : Ce qui diverge : `model`, `tool`, `approval`, `question` ou `end` (la fin du run : appels non refaits, autre issue, autre réponse).
- `where` : Ce qui diverge, en une phrase : l'appel et sa place.
- `detail` : Ce qui diffère, quand on peut le dire.
- `parts` : Parties de la requête qui diffèrent (`model`, `system`, `tools`, `messages`, `settings`) : appel de modèle, journal qui porte les empreintes par partie.
- `expected_hash` : Empreinte de la requête attendue, celle du journal (appel de modèle).
- `actual_hash` : Empreinte de la requête du rejeu (appel de modèle).
- `rank` : Rang de l'appel de modèle qui diverge, à partir de 1 (appel de modèle).

## Classe `loom_ia.replay.book._Response`

```python
@dataclass
class _Response
```

Une réponse de modèle du journal, et sa place.

## Classe `loom_ia.replay.book.ToolCallRecord`

```python
@dataclass
class ToolCallRecord
```

Un appel d'outil du journal, et ce que le rejeu en a fait.

**Attributs :**

- `run_id` : Run qui a fait l'appel.
- `called` : L'événement `tool.called` du journal.
- `completed` : L'événement `tool.completed` ; `None` si l'appel n'a pas de résultat au journal.
- `served` : Le rejeu en a resservi le résultat.
- `reserved` : Variante : promis à un appel du rejeu, qui le servira (`match_tool`).

## Classe `loom_ia.replay.book.ReplayBook`

```python
@dataclass
class ReplayBook
```

Les réponses du monde à un run, et ce qu'on en a resservi.

**Attributs :**

- `responses` : Réponses de modèle du journal, dans l'ordre.
- `tools` : Appels d'outil du journal, par run et `call_id` : un identifiant d'appel n'est unique que dans son run.
- `approvals` : Première décision d'approbation du journal, par run et `call_id`.
- `answers` : Réponse ou expiration d'une question (`wait_answer`), par run et `call_id`.
- `fallbacks` : Bascules du run d'origine sur un modèle de secours, que le rejeu identique ne reproduit pas.
- `divergence` : La première divergence ; `None` tant que le rejeu suit le journal.
- `failure` : La panne de modèle sur laquelle le run racine s'est arrêté, s'il s'est arrêté sur une panne : son type et son message, resservis au rejeu identique.
- `read_runs` : Variante : sous-runs lus au journal (leur appel servi tel quel) ; leurs propres appels n'ont pas à être refaits.
- `real_calls` : Variante : appels vraiment partis aux modèles.
- `real_usage` : Variante : tokens de ces vrais appels.
- `spent_usd` : Variante : ce qu'ont coûté ces vrais appels, en dollars.
- `_by_hash` : Réponses du journal pas encore servies, par empreinte de requête.
- `_children` : Sous-runs de chaque run, d'après les `run.started`.

### Méthode `loom_ia.replay.book.ReplayBook.of`

```python
@classmethod
def of(cls, events: Sequence[Event]) -> ReplayBook
```

Le livre d'un run (ou de son arbre, en variante), tiré de ses événements.

### Méthode `loom_ia.replay.book.ReplayBook.diverged`

```python
@property
def diverged(self) -> bool
```

Vrai dès qu'une divergence est notée.

### Méthode `loom_ia.replay.book.ReplayBook.served_responses`

```python
@property
def served_responses(self) -> int
```

Réponses du journal resservies au rejeu.

### Méthode `loom_ia.replay.book.ReplayBook.journal_tools`

```python
@property
def journal_tools(self) -> int
```

Appels d'outil que le journal sert : tous, sauf les rôles.

### Méthode `loom_ia.replay.book.ReplayBook.served_tools`

```python
@property
def served_tools(self) -> int
```

Appels d'outil du journal resservis au rejeu.

### Méthode `loom_ia.replay.book.ReplayBook.unserved_responses`

```python
@property
def unserved_responses(self) -> list[_Response]
```

Réponses du journal pas encore resservies, sauf celles des sous-runs lus tels quels (variante).

### Méthode `loom_ia.replay.book.ReplayBook.unserved_tools`

```python
@property
def unserved_tools(self) -> list[ToolCallRecord]
```

Appels servis par le journal au run d'origine et pas rejoués.

Un rôle n'est jamais servi par le journal — il tourne — : il n'en fait
pas partie, ses appels de modèle se comptent avec les réponses. Les
appels d'un sous-run lu tel quel non plus (variante).

### Méthode `loom_ia.replay.book.ReplayBook.read_through`

```python
def read_through(self, record: ToolCallRecord) -> None
```

Le sous-run de cet appel, et les siens, sont lus au journal, pas refaits.

### Méthode `loom_ia.replay.book.ReplayBook.diverge`

```python
def diverge(self, divergence: Divergence) -> None
```

Note une divergence ; seule la première compte.

### Méthode `loom_ia.replay.book.ReplayBook.answer`

```python
def answer(self, request: ModelRequest) -> ModelResponded
```

La réponse du journal à cette requête ; `ModelError` sinon (rejeu identique).

L'erreur est un arrêt voulu (`by_client`) : le moteur ne la crie pas,
c'est le rejeu qui dit sa divergence.

### Méthode `loom_ia.replay.book.ReplayBook.known`

```python
def known(self, request: ModelRequest) -> ModelResponded | None
```

Variante : la réponse du journal à une requête identique, avant comme après la divergence.

`None` si le journal ne la connaît pas : elle partira pour de vrai, et
la première requête inconnue est notée comme divergence.

**Arguments :**

- `request` : La requête de la variante.

### Méthode `loom_ia.replay.book.ReplayBook.paid`

```python
def paid(self, cost_usd: float, usage: Usage) -> None
```

Variante : un appel parti pour de vrai, et ce qu'il a coûté.

### Méthode `loom_ia.replay.book.ReplayBook._model_divergence`

```python
def _model_divergence(self, request: ModelRequest, digest: str, *, exact: bool) -> Divergence
```

La divergence d'un appel de modèle que le journal ne connaît pas.

Si toutes les réponses du journal sont déjà servies, le run d'origine n'a
pas fait cet appel. Sinon, l'attendu est la première réponse pas encore
servie : la divergence dit quelles parties de la requête diffèrent quand le
journal en porte les empreintes, et signale au rejeu identique une bascule
du run d'origine sur un secours, qu'il ne reproduit pas.

**Arguments :**

- `request` : La requête du rejeu.
- `digest` : Son empreinte.
- `exact` : Rejeu identique (et non variante).

### Méthode `loom_ia.replay.book.ReplayBook.match_tool`

```python
def match_tool(self, name: str, arguments: Mapping[str, JsonValue]) -> ToolCallRecord | None
```

Variante : le premier appel du journal de même nom et mêmes arguments.

Seul un appel pas encore promis ni servi compte, dans l'ordre du
journal ; il est promis à l'appelant (`reserved`). Un rôle, ou un
appel sans résultat, n'est jamais retrouvé.

**Arguments :**

- `name` : Nom de l'outil.
- `arguments` : Arguments de l'appel.

### Méthode `loom_ia.replay.book.ReplayBook.recorded_output`

```python
def recorded_output(self, record: ToolCallRecord) -> tuple[ToolOutput, Consumption | None]
```

Le résultat enregistré d'un appel, et la consommation qu'il portait.

### Méthode `loom_ia.replay.book.ReplayBook.tool_output`

```python
def tool_output(self, run_id: str, call_id: str, name: str, arguments: Mapping[str, JsonValue]) -> tuple[ToolOutput, Consumption | None]
```

Le résultat du journal pour cet appel d'outil (rejeu identique), et sa consommation.

L'appel est retrouvé par run et `call_id` ; son nom et ses arguments
doivent être ceux du journal. Absent, sans résultat ou différent, il diverge
et le modèle reçoit une erreur ; après une divergence, plus rien n'est servi.

**Arguments :**

- `run_id` : Run qui appelle.
- `call_id` : Identifiant de l'appel.
- `name` : Nom de l'outil.
- `arguments` : Arguments tels que le modèle les a écrits.

**Renvoie :**

Le résultat enregistré, ou une erreur, et la consommation que portait
l'appel.

### Méthode `loom_ia.replay.book.ReplayBook.recorded_decision`

```python
def recorded_decision(self, run_id: str, call_id: str) -> ApprovalDecision | None
```

La décision d'approbation que le journal porte pour cet appel de ce run.

### Méthode `loom_ia.replay.book.ReplayBook.approve`

```python
async def approve(self, run_id: str, pending: PendingApproval) -> ApprovalDecision
```

La décision du journal, rendue en ligne : le rejeu ne s'arrête pas pour attendre.

### Méthode `loom_ia.replay.book.ReplayBook.recorded_answer`

```python
def recorded_answer(self, run_id: str, call_id: str) -> QuestionReply | NoAnswer | None
```

La réponse que le journal porte pour cette question de ce run.

### Méthode `loom_ia.replay.book.ReplayBook.reply_to`

```python
async def reply_to(self, run_id: str, pending: PendingQuestion) -> QuestionReply | NoAnswer
```

La réponse du journal, rendue en ligne : le rejeu ne s'arrête pas pour attendre.

## Fonction `loom_ia.replay.book.recorded_response`

```python
def recorded_response(recorded: ModelResponded) -> ModelResponse
```

La réponse telle que le journal l'a gardée, entière.

## Fonction `loom_ia.replay.book.provider_of`

```python
def provider_of(spec: ModelSpec) -> str
```

Fournisseur d'un modèle, tel que son adaptateur l'écrirait : le nom de son SDK.

## Fonction `loom_ia.replay.book._servable`

```python
def _servable(record: ToolCallRecord) -> bool
```

Un appel que le journal peut servir : fini, et pas un rôle (qui tourne).

## Classe `loom_ia.replay.book.ReplayModelClient`

```python
class ReplayModelClient(AnsweringClient)
```

Client de modèle dont les réponses sont celles du journal.

### Méthode `loom_ia.replay.book.ReplayModelClient.provider`

```python
@property
def provider(self) -> str
```

Fournisseur du modèle, tel que son adaptateur l'écrirait.

### Méthode `loom_ia.replay.book.ReplayModelClient.answer`

```python
async def answer(self, request: ModelRequest) -> ModelResponse
```

La réponse du journal à cette requête.

**Arguments :**

- `request` : La requête du rejeu.

**Lève :**

- `ModelError` : Requête inconnue du journal, rejeu déjà arrêté, ou panne du run d'origine resservie.

### Méthode `loom_ia.replay.book.ReplayModelClient.stream`

```python
async def stream(self, request: ModelRequest) -> AsyncGenerator[ModelChunk]
```

Pour qui lirait le flux : les morceaux de la réponse du journal.

### Méthode `loom_ia.replay.book.ReplayModelClient.aclose`

```python
async def aclose(self) -> None
```

Rien à fermer : aucun vrai client.

## Classe `loom_ia.replay.book.JournalTools`

```python
class JournalTools
```

Résultats d'outils servis depuis le journal (`ToolReplay`).

### Méthode `loom_ia.replay.book.JournalTools.serves`

```python
def serves(self, tool: AnyTool, call: PendingCall, run_id: RunId) -> bool
```

Le journal sert tout appel, sauf celui d'un rôle, qui tourne et dont le modèle rejoue.

### Méthode `loom_ia.replay.book.JournalTools.from_journal`

```python
def from_journal(self, run_id: RunId, call_id: str) -> bool
```

Toujours vrai : au rejeu identique, tout résultat servi vient du journal.

### Méthode `loom_ia.replay.book.JournalTools.approve`

```python
async def approve(self, run_id: RunId, pending: PendingApproval) -> ApprovalDecision
```

La décision du journal, rendue en ligne.

### Méthode `loom_ia.replay.book.JournalTools.answer`

```python
async def answer(self, run_id: RunId, pending: PendingQuestion) -> QuestionReply | NoAnswer
```

La réponse du journal, rendue en ligne.

### Méthode `loom_ia.replay.book.JournalTools.output`

```python
async def output(self, run_id: RunId, call_id: str, name: str, arguments: Mapping[str, JsonValue], resolved: Mapping[str, JsonValue]) -> tuple[ToolOutput, Consumption | None]
```

Le résultat du journal pour cet appel (`ReplayBook.tool_output`), comparé aux arguments écrits : c'est eux que le journal garde.

## Fonction `loom_ia.replay.book._model_failure`

```python
def _model_failure(failed: RunFailed) -> tuple[ModelErrorKind, str] | None
```

La panne de modèle d'un `run.failed` ; `None` si le run s'est arrêté autrement.
