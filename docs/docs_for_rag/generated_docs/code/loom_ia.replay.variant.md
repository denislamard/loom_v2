# loom_ia.replay.variant

Module `loom_ia.replay.variant` de loom_v2 (fichier `src/loom_ia/replay/variant.py`).

Rejouer un run en variante : autre modèle ou autre config, et comparer (fonction K6, point 31, jalon J6.2b).

La variante monte l'agent du run comme le rejeu identique — en mémoire, sous
le même identifiant, la logique de loom tournant pour de vrai —, mais le monde
n'est plus seulement servi par le journal :

- **un appel de modèle** dont la requête est identique à une requête du
  journal reçoit la réponse enregistrée, avant comme après la divergence ;
  sinon, il part pour de vrai (`VariantModelClient`) ;
- **un appel d'outil** est d'abord cherché au journal par son nom et ses
  arguments (pas par `call_id` : un vrai modèle en fabrique de nouveaux).
  Retrouvé, il est lu. Sinon : sa **doublure** s'il en a une ; un sous-agent
  est relancé, en variante lui aussi ; un outil à **effets de bord**
  (`side_effects` autre que `none`) n'est **jamais** exécuté, et le
  modèle reçoit une erreur qui le dit ; un outil sans effets de bord
  s'exécute pour de vrai (`VariantTools`) ;
- **une approbation** reprend la décision du journal pour un appel retrouvé ;
  rien ne part pour un appel doublé ou refusé, qui est donc accordé ; un
  appel qui s'exécuterait pour de vrai est refusé, personne n'étant là pour
  trancher.

`swap_models` change le modèle d'une étape de l'agent (`main`, un rôle,
`judge:<nom>`) sans toucher au fichier de config.

## Constantes de `loom_ia.replay.variant`

- `ToolFate = Literal['journal', 'double', 'relaunched', 'refused', 'run']` : Ce qu'un appel d'outil du rejeu en variante devient.
- `Double = Callable[..., object]` : Une doublure : appelée avec les arguments de l'appel, comme un outil Python (synchrone ou non) ; sa valeur est traduite comme celle d'un outil.
- `JUDGE_PREFIX = 'judge:'` : Préfixe de l'étape d'un juge (`judge:<nom>`), telle que le journal la nomme.
- `REFUSED = "Non exécuté : rejeu en variante. L'outil {name} a des effets de bord ({effects}) et cet appel n'est pas au journal sou…` : Erreur rendue au modèle pour un outil à effets de bord que le journal ne connaît pas sous ces arguments.
- `NOBODY = "rejeu en variante : cet appel s'exécuterait pour de vrai, et personne n'est là pour l'approuver"` : Motif du refus d'une approbation pour un appel qui s'exécuterait pour de vrai.

## Classe `loom_ia.replay.variant.VariantModelClient`

```python
class VariantModelClient(AnsweringClient)
```

Client de modèle de la variante : le journal s'il connaît la requête, sinon le vrai.

Le vrai client n'est créé qu'au premier appel qui part : un modèle que la
variante ne fait que relire n'a pas besoin de sa clé.

### Méthode `loom_ia.replay.variant.VariantModelClient.provider`

```python
@property
def provider(self) -> str
```

Fournisseur du vrai client s'il est créé, sinon celui que l'adaptateur du modèle écrirait.

### Méthode `loom_ia.replay.variant.VariantModelClient.answer`

```python
async def answer(self, request: ModelRequest) -> ModelResponse | None
```

La réponse du journal à une requête identique ; `None` sinon : l'appel part pour de vrai, en flux (`stream`).

**Arguments :**

- `request` : La requête.

### Méthode `loom_ia.replay.variant.VariantModelClient.stream`

```python
async def stream(self, request: ModelRequest) -> AsyncGenerator[ModelChunk]
```

Un vrai appel ; ce qu'il a coûté est noté une fois la réponse complète.

### Méthode `loom_ia.replay.variant.VariantModelClient.aclose`

```python
async def aclose(self) -> None
```

Ferme le vrai client, s'il a été créé.

## Classe `loom_ia.replay.variant._Decision`

```python
@dataclass(frozen=True, slots=True)
class _Decision
```

Ce que le rejeu en variante fait d'un appel d'outil, décidé une fois.

**Attributs :**

- `fate` : Le sort de l'appel.
- `record` : L'appel du journal qui le sert (`journal`).
- `effects` : Effets de bord déclarés de l'outil (`refused`), pour le dire au modèle.

## Classe `loom_ia.replay.variant.VariantTools`

```python
class VariantTools
```

Outils de la variante (`ToolReplay`) : lus, doublés, refusés, ou exécutés.

### Méthode `loom_ia.replay.variant.VariantTools.serves`

```python
def serves(self, tool: AnyTool, call: PendingCall, run_id: RunId) -> bool
```

Vrai si la variante répond à la place de l'outil : lu au journal, doublé ou refusé.

Un rôle tourne toujours. Le sort de chaque appel est décidé une fois
(`_decide`) ; un sous-agent relancé ou un outil sans effets de bord
s'exécute pour de vrai.

**Arguments :**

- `tool` : L'outil appelé.
- `call` : L'appel.
- `run_id` : Run qui appelle.

### Méthode `loom_ia.replay.variant.VariantTools.from_journal`

```python
def from_journal(self, run_id: RunId, call_id: str) -> bool
```

Vrai pour un appel lu au journal de ce même run.

Doublé, refusé, ou lu au journal d'un autre run de l'arbre, le résultat
est neuf : le state s'y pose comme en direct.

### Méthode `loom_ia.replay.variant.VariantTools.output`

```python
async def output(self, run_id: RunId, call_id: str, name: str, arguments: Mapping[str, JsonValue], resolved: Mapping[str, JsonValue]) -> tuple[ToolOutput, Consumption | None]
```

Le résultat de l'appel selon son sort.

Lu au journal (son sous-run lu avec lui), sa doublure (références
résolues, arguments remplacés compris), ou l'erreur qui dit au modèle
qu'il n'est pas exécuté.

### Méthode `loom_ia.replay.variant.VariantTools.answer`

```python
async def answer(self, run_id: RunId, pending: PendingQuestion) -> QuestionReply | NoAnswer
```

La réponse du journal si la question y est ; sinon personne ne répond en variante.

### Méthode `loom_ia.replay.variant.VariantTools.approve`

```python
async def approve(self, run_id: RunId, pending: PendingApproval) -> ApprovalDecision
```

Décision en ligne : celle du journal, ou accordée quand rien ne part pour de vrai.

### Méthode `loom_ia.replay.variant.VariantTools._decide`

```python
def _decide(self, tool: AnyTool, call: PendingCall) -> _Decision
```

Le sort d'un appel, et la divergence qu'il marque s'il n'est pas au journal.

Retrouvé au journal par son nom et ses arguments : lu. Sinon, dans l'ordre :
sa doublure, un sous-agent relancé, un refus s'il a des effets de bord
(jugés avant les références et les politiques), une exécution réelle.

**Arguments :**

- `tool` : L'outil appelé.
- `call` : L'appel.

## Fonction `loom_ia.replay.variant.doubled`

```python
async def doubled(double: Double, name: str, arguments: Mapping[str, JsonValue]) -> ToolOutput
```

Le résultat d'une doublure, traduit comme celui d'un outil ; son erreur, dite au modèle.

## Fonction `loom_ia.replay.variant.fate_label`

```python
def fate_label(fate: ToolFate) -> str
```

Le sort d'un appel du rejeu en variante, en clair.

**Arguments :**

- `fate` : `journal`, `double`, `relaunched`, `refused` ou `run`.

## Fonction `loom_ia.replay.variant.swap_models`

```python
def swap_models(config: LoomConfig, agent: str, models: Mapping[str, str]) -> LoomConfig
```

La config où les étapes nommées de `agent` appellent un autre modèle déclaré.

Une étape : `main` (l'orchestrateur), le nom d'un rôle, ou `judge:<nom>`
— les noms que le journal donne aux appels de modèle. Les secours restent
ceux de la config.

## Fonction `loom_ia.replay.variant._steps`

```python
def _steps(spec: AgentSpec) -> list[str]
```

Les étapes d'un agent qui appellent un modèle, sous leur nom au journal.

## Fonction `loom_ia.replay.variant._swapped`

```python
def _swapped(spec: AgentSpec, models: Mapping[str, str]) -> AgentSpec
```

La spec de l'agent où chaque étape nommée appelle son nouveau modèle : `main`, le juge final, les rôles et leurs juges.
