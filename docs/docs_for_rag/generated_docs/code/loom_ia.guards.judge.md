# loom_ia.guards.judge

Module `loom_ia.guards.judge` de loom_v2 (fichier `src/loom_ia/guards/judge.py`).

Juge : politique fournie `loom.judge.<nom>` (fonctions E3 et E6, point 21).

Un juge est branché d'office après le guard des contrats : la forme d'une
sortie est contrôlée d'abord, son fond ensuite. Il porte sur la réponse
finale de l'agent (`on_output`) ou sur la sortie d'un rôle (`after_tool`).

Déroulé, pour chaque sortie :

1. **déclenchement** : choix de l'appelant (`judges`), puis clauses de
   `when` (`tenants`, `sample`, `condition`) ; un juge qui ne
   s'exécute pas écrit un `guard.checked` `skipped` avec son motif ;
2. **appel du modèle du juge** : la sortie, les critères et le contexte
   déclaré (comme un rôle, #12 ; les arguments du rôle jugé en plus). Le
   verdict est imposé par un outil, `verdict` (`tool_choice: required`),
   dont les arguments sont contrôlés par un schéma ; l'appel est journalisé
   dans le run jugé, au nom de `judge:<nom>`. Un modèle qui refuse l'outil
   imposé (`capabilities.forced_tool_choice: false` : Claude Opus 5.5, Fable
   5.1) reçoit la requête en `auto` : la consigne seule demande `verdict`,
   et un verdict absent est une erreur du juge (`on_error`) ;
3. **verdict** : `judge.evaluated` (une note et un motif par critère), puis
   `guard.checked` ;
4. **suite** : un critère bloquant sous son seuil fait refuser la sortie.
   Son auteur la répare (`Retry` : l'orchestrateur, qui garde ses outils
   sauf `repair.tools: none`, ou le modèle du rôle), tant que
   `repair.max_attempts` le permet ; ensuite `on_failure` décide, comme
   pour un contrat (`fail`, `unverified`, `fallback`).

Secours (fonction B4, point 10) : un juge peut déclarer ses `fallbacks`. Une bascule est
journalisée dans le span de l'appel du juge ; le run garde ensuite le secours
pour ce juge (adhérence, emplacement `judge:<nom>`).

Une erreur du juge (modèle en échec après ses nouvelles tentatives et ses
secours, verdict absent ou invalide, délai dépassé) est une erreur de la
politique : selon son `on_error`, le run échoue ou la sortie passe sans
jugement.

## Constantes de `loom_ia.guards.judge`

- `JUDGE_POLICY_PREFIX = 'loom.judge'` : Préfixe du nom de la politique d'un juge (`loom.judge.<nom>`).
- `JUDGE_GUARD = 'judge'` : Nom du guard dans `guard.checked`.
- `VERDICT_TOOL = 'verdict'` : Nom de l'outil que le juge doit appeler.
- `JUDGE_SYSTEM = "Tu es un juge. Tu évalues une sortie produite par un autre modèle (<output>), selon les critères donnés (<criteria>).\…` : Prompt système d'un juge.
- `Condition = Callable[[JudgeInput], bool | Awaitable[bool]]` : Prédicat de `when.condition` : synchrone ou non.

## Fonction `loom_ia.guards.judge.judge_policy_name`

```python
def judge_policy_name(judge: str) -> str
```

Nom de la politique d'un juge, dans le journal (`loom.judge.<nom>`).

## Classe `loom_ia.guards.judge.JudgeDefinition`

```python
@dataclass(frozen=True, kw_only=True)
class JudgeDefinition
```

Un juge tel que le moteur l'exécute : critères, contexte, déclenchement, suite.

**Attributs :**

- `name` : Nom du juge (`output` pour la réponse finale, le nom du rôle pour un rôle, sauf `name` déclaré).
- `role` : Rôle dont la sortie est jugée ; `None` pour la réponse finale.
- `criteria` : Les critères : nom, règle, seuil, bloquant ou non.
- `context` : Contexte reçu par le juge, comme un rôle.
- `sample` : Proportion des runs jugés, entre 0 et 1 (tirage déterministe par run et par juge).
- `condition` : Prédicat `when.condition` résolu ; `None` : pas de condition.
- `tenants` : Clients dont les runs sont jugés ; `None` : tous.
- `profiles` : Profils où ce juge travaille, et celui qui est actif (jalon J5.5a) : les deux sont connus au montage, mais comparés à l'exécution pour que le journal dise qu'un juge déclaré a été sauté, et pourquoi.
- `profile` : Profil actif.
- `repair` : Réparations demandées à l'auteur de la sortie refusée.
- `on_failure` : Suite d'une sortie encore refusée : `fail`, `unverified` ou `fallback`.
- `fallback_message` : Message de repli, avec `fallback`.
- `max_tokens` : Réglage propre au juge : remplace celui de son modèle.
- `params` : Paramètres propres au juge, sur ceux de son modèle.
- `timeout` : Délai du juge, en secondes ; sans lui, ceux de son modèle et son retry le bornent.
- `on_error` : Erreur du juge : `block` fait échouer le run, `allow` laisse passer la sortie.

### Méthode `loom_ia.guards.judge.JudgeDefinition.target`

```python
@property
def target(self) -> str
```

Cible dans le journal : `output` ou `role:<nom>`.

### Méthode `loom_ia.guards.judge.JudgeDefinition.blocking`

```python
@property
def blocking(self) -> bool
```

Vrai si un critère au moins peut faire refuser la sortie.

## Classe `loom_ia.guards.judge._Judged`

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class _Judged
```

Sortie à juger, telle que le point d'accroche la donne.

**Attributs :**

- `text` : Texte de la sortie.
- `data` : Ses données structurées, s'il y en a.
- `arguments` : Arguments du rôle jugé.
- `call_id` : Appel du rôle jugé ; `None` pour la réponse finale.
- `output` : Résultat d'outil jugé (rôle) ; `None` pour la réponse finale.

## Classe `loom_ia.guards.judge.JudgeGuard`

```python
class JudgeGuard(TracingPolicy)
```

Politique `loom.judge.<nom>` : un juge, sur la réponse finale ou sur un rôle.

### Méthode `loom_ia.guards.judge.JudgeGuard.__init__`

```python
def __init__(self, definition: JudgeDefinition, model: ModelClient, model_spec: ModelSpec, *, artifacts: ArtifactStore | None=None, fallbacks: Sequence[ModelLink]=(), breakers: CircuitBreakers | None=None) -> None
```

Prépare le juge et l'outil `verdict` de ses critères.

**Arguments :**

- `definition` : Le juge.
- `model` : Client du modèle du juge.
- `model_spec` : Définition de ce modèle.
- `artifacts` : Stockage des fichiers, pour les pièces jointes du contexte.
- `fallbacks` : Secours du modèle, dans l'ordre (fonction B4, point 10).
- `breakers` : Disjoncteurs partagés de l'instance.

### Méthode `loom_ia.guards.judge.JudgeGuard.name`

```python
@property
def name(self) -> str
```

`loom.judge.<nom>`.

### Méthode `loom_ia.guards.judge.JudgeGuard.points`

```python
@property
def points(self) -> frozenset[HookPoint]
```

`on_output` pour la réponse finale, `after_tool` pour un rôle.

### Méthode `loom_ia.guards.judge.JudgeGuard.decisions`

```python
@property
def decisions(self) -> frozenset[DecisionKind]
```

`replace`, `retry` et `fail`.

### Méthode `loom_ia.guards.judge.JudgeGuard.decide_traced`

```python
async def decide_traced(self, subject: PolicySubject, context: PolicyContext, trace: Trace) -> Decision
```

Juge la sortie : déclenchement, appel du modèle, verdict, puis suite.

**Arguments :**

- `subject` : Le sujet du point d'accroche.
- `context` : Contexte de la politique (réparations déjà demandées).
- `trace` : Reçoit les événements de l'appel du juge et `judge.evaluated`.

**Renvoie :**

`CONTINUE` si la sortie passe ou n'est pas jugée (un `guard.checked`
`skipped` dit pourquoi) ; `Retry` avec le diagnostic tant que les
réparations le permettent ; sinon la décision de `on_failure`.

**Lève :**

- `PolicyFailure` : Modèle en échec, verdict absent ou invalide.

### Méthode `loom_ia.guards.judge.JudgeGuard._judged`

```python
def _judged(self, subject: PolicySubject) -> _Judged | None
```

Sortie que ce juge évalue à ce point, ou None.

### Méthode `loom_ia.guards.judge.JudgeGuard._skipped`

```python
async def _skipped(self, state: RunState, judged: _Judged) -> SkipReason | None
```

Motif pour ne pas juger cette sortie, ou None : décidé par le code (point 21).

### Méthode `loom_ia.guards.judge.JudgeGuard.chain`

```python
def chain(self) -> ModelChain
```

Modèle du juge et ses secours, avec les réglages du juge (fonction B6).

### Méthode `loom_ia.guards.judge.JudgeGuard._evaluate`

```python
async def _evaluate(self, state: RunState, judged: _Judged, trace: Trace, context: PolicyContext) -> tuple[tuple[CriterionScore, ...], ModelSpec]
```

Appel du juge, secours compris, journalisé ; les notes et le modèle qui a jugé.

### Méthode `loom_ia.guards.judge.JudgeGuard._message`

```python
async def _message(self, state: RunState, judged: _Judged, context: PolicyContext) -> Message
```

Ce que le juge reçoit : critères, contexte déclaré, arguments du rôle, sortie.

### Méthode `loom_ia.guards.judge.JudgeGuard._scores`

```python
def _scores(self, message: Message) -> tuple[CriterionScore, ...]
```

Notes du verdict, contrôlées ; lève `PolicyFailure` si le verdict ne convient pas.

### Méthode `loom_ia.guards.judge.JudgeGuard._exhausted`

```python
def _exhausted(self, judged: _Judged, blocking: tuple[CriterionScore, ...], reason: str, repairs: int) -> Decision
```

Réparations épuisées : `on_failure` décide de la suite.

Le refus d'un rôle est dit à l'orchestrateur, qui n'en est pas l'auteur :
une règle et le motif du juge, pas la consigne de réparation (`refusal`).

## Fonction `loom_ia.guards.judge.verdict_tool`

```python
def verdict_tool(criteria: tuple[Criterion, ...]) -> ToolDefinition
```

Outil imposé au juge : une note et un motif par critère.

**Arguments :**

- `criteria` : Les critères.

**Renvoie :**

L'outil `verdict` : une liste `criteria`, une entrée par critère
(`name`, `score` entre 0 et 1, `reason`), dans l'ordre donné.

## Fonction `loom_ia.guards.judge.verdict_scores`

```python
def verdict_scores(message: Message, criteria: tuple[Criterion, ...], *, judge: str, validator: Validator | None=None) -> tuple[CriterionScore, ...]
```

Les notes que porte la réponse d'un juge, critère par critère, dans l'ordre des critères.

Le verdict est l'appel de l'outil `verdict`, contrôlé par son schéma
(`validator`, sinon celui de `verdict_tool(criteria)`). Lève
`PolicyFailure` si la réponse n'en porte pas, ou un qui ne convient pas.

**Arguments :**

- `message` : La réponse du juge.
- `criteria` : Les critères.
- `judge` : Nom du juge, pour le message d'erreur.
- `validator` : Contrôle du schéma de `verdict`.

**Renvoie :**

Une note par critère, avec son seuil et s'il est bloquant.

**Lève :**

- `PolicyFailure` : Pas d'appel à `verdict`, ou arguments invalides.

## Fonction `loom_ia.guards.judge.diagnostic`

```python
def diagnostic(judge: str, refused: tuple[CriterionScore, ...], kept: tuple[Criterion, ...]=()) -> str
```

Diagnostic donné à l'auteur de la sortie pour qu'il la répare.

`kept` : les critères bloquants que la sortie tient déjà, avec leur règle
(pas le motif du juge, propre à la sortie refusée). À l'essai de la fonction E3 du 19/10,
le diagnostic ne citait que le critère échoué (« trois phrases au plus ») :
Haiku a raccourci sa réponse et perdu celui qu'elle tenait (« traite la
demande »), refusé à son tour.

**Arguments :**

- `judge` : Nom du juge.
- `refused` : Les critères bloquants sous leur seuil, avec le motif.
- `kept` : Les critères bloquants tenus.

## Fonction `loom_ia.guards.judge.correlated`

```python
def correlated(judge: ModelSpec, evaluated: ModelSpec) -> bool
```

Vrai si le juge utilise le même modèle que la sortie qu'il évalue (fonction E6).

**Arguments :**

- `judge` : Le modèle du juge.
- `evaluated` : Le modèle qui a produit la sortie.

## Fonction `loom_ia.guards.judge._json`

```python
def _json(text: str) -> JsonValue
```

Objet JSON d'une réponse finale qui en est un, sinon None.
