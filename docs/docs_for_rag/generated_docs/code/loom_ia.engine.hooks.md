# loom_ia.engine.hooks

Module `loom_ia.engine.hooks` de loom_v2 (fichier `src/loom_ia/engine/hooks.py`).

Exécution des politiques d'un agent aux points d'accroche de `step` (points 1 et 2).

Les politiques d'un point s'exécutent dans l'ordre déclaré. `Replace`
transmet la valeur remplacée à la suivante ; toute autre décision que
`Continue` ou `Replace` arrête la chaîne. Chaque décision autre que
`Continue` donne un `policy.decided`, que le moteur écrit avant l'effet.

Garde-fous :

- chaque politique a un délai ; une exception, un dépassement du délai, une
  décision non déclarée ou non permise à ce point, ou une valeur de
  remplacement du mauvais type sont des erreurs de la politique : selon son
  `on_error`, le run échoue (`block`, par défaut) ou la politique est
  ignorée (`allow`), ce qui est journalisé aussi ;
- `Retry` est borné : au-delà de `max_attempts` réparations demandées par
  une même politique pour une même sortie (compteur du `RunState` pour la
  réponse finale, compteur de l'appel pour un rôle), sa décision devient
  `Fail`. Un guard ou un juge qui borne lui-même ses réparations (contrat
  de sortie, juge, fidélité d'un résumé) n'a pas de borne ici
  (`max_attempts: None`) : la sienne est son `repair.max_attempts`.

Guards : une politique peut enregistrer ses contrôles dans son contexte
(`record`) ; ils deviennent des `guard.checked`, écrits avant sa décision.

Juges (point 21) : une politique fournie par loom-ia peut aussi journaliser son
propre travail (`TracingPolicy`) : l'appel du modèle d'un juge et son
verdict, écrits avant ses contrôles. Le type est nominal et interne : une
politique écrite par l'utilisateur n'écrit pas dans le journal.

Ce module ne fait qu'évaluer : ce que chaque décision change au run (requête
remplacée, appel refusé, réparation, arrêt, échec) est appliqué par la boucle
et l'exécuteur d'outils.

Points d'accroche : `before_model`, `after_model`, `before_tool`,
`after_tool`, `on_output` (`HookPoint`). Config : `agents.<nom>.policies`
(nom, `params`, `timeout`, `on_error`, `max_attempts`).

## Constantes de `loom_ia.engine.hooks`

- `DEFAULT_POLICY_TIMEOUT = 5.0` : Délai par défaut d'une politique, en secondes.
- `OnError = Literal['block', 'allow']` : Conduite face à une politique en erreur : `block` fait échouer le run, `allow` l'ignore.
- `ArgumentsCheck = Callable[[dict[str, JsonValue]], str | None]` : Vérifie des arguments remplacés ; renvoie le problème, ou `None` s'ils conviennent.
- `TracedEvent = ModelExchanged | ModelRetried | ModelFellBack | CircuitOpened | ModelResponded | JudgeEvaluated | BudgetExceeded` : Ce qu'une politique fournie journalise de son propre travail.  Appel et verdict d'un juge (secours et disjoncteur compris), limite de budget atteinte.
- `Trace = Callable[[TracedEvent], None]` : Reçoit un événement à écrire, dans l'ordre.
- `PolicyEvent = PolicyDecided | GuardChecked | TracedEvent` : Événement produit par l'évaluation des politiques d'un point.
- `_DECISION_TYPES = (Continue, Replace, Retry, Deny, Pause, Stop, Fail)` : Types de décision qu'une politique peut rendre.

## Classe `loom_ia.engine.hooks.PolicyFailure`

```python
class PolicyFailure(Exception)
```

Erreur d'une politique : exception, délai, décision ou valeur invalide.

Elle ne sort pas de `Policies.run` : selon `on_error`, le run échoue
(`Fail`) ou la politique est ignorée, et un `policy.decided` marqué
`error` est écrit.

## Classe `loom_ia.engine.hooks.BoundPolicy`

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class BoundPolicy
```

Une politique telle qu'un agent la branche : points, paramètres, garde-fous.

**Attributs :**

- `policy` : La politique.
- `name` : Nom dans le journal ; par défaut celui de la politique.
- `points` : Points d'accroche où elle s'exécute.
- `params` : Paramètres donnés à la politique (`PolicyContext.params`).
- `timeout` : Délai de sa décision, en secondes (5 par défaut) ; `None` : sans délai.
- `on_error` : `block` (le run échoue) ou `allow` (la politique est ignorée) quand elle est en erreur.
- `max_attempts` : Réparations (`Retry`) que la politique peut demander pour une sortie ; au-delà, sa décision devient `Fail`. `None` : la politique borne elle-même ses réparations.

## Classe `loom_ia.engine.hooks.TracingPolicy`

```python
class TracingPolicy(ABC)
```

Politique fournie par loom-ia qui journalise son travail (juge, point 21 ; budget, fonction J4).

`decide_traced` remplace `decide` quand le moteur l'exécute : `trace`
reçoit les événements à écrire (appel de modèle, verdict), dans l'ordre,
avant les contrôles et la décision de la politique.

### Méthode `loom_ia.engine.hooks.TracingPolicy.decide_traced`

```python
@abstractmethod
async def decide_traced(self, subject: PolicySubject, context: PolicyContext, trace: Trace) -> Decision
```

Décision de la politique, ses événements remis à `trace` à mesure.

**Arguments :**

- `subject` : Le sujet du point d'accroche.
- `context` : Contexte de la politique (nom, paramètres, tentative, contrôles, historique).
- `trace` : Reçoit les événements à écrire.

**Renvoie :**

La décision.

### Méthode `loom_ia.engine.hooks.TracingPolicy.decide`

```python
async def decide(self, subject: PolicySubject, context: PolicyContext) -> Decision
```

Décision seule, sans journal (hors du moteur).

**Arguments :**

- `subject` : Le sujet du point d'accroche.
- `context` : Contexte de la politique.

**Renvoie :**

La décision ; les événements sont perdus.

## Classe `loom_ia.engine.hooks.Verdict`

```python
@dataclass(frozen=True, slots=True)
class Verdict
```

Issue de la chaîne des politiques d'un point.

`decision` est `Continue` si aucune politique n'a arrêté la chaîne
(`subject` porte alors les valeurs éventuellement remplacées), sinon la
décision qui l'a arrêtée, et `by` la politique qui l'a rendue.
`events` : les événements à écrire, dans l'ordre (contrôles, puis décisions).

**Attributs :**

- `decision` : `Continue`, ou la décision qui a arrêté la chaîne.
- `subject` : Le sujet, valeurs remplacées comprises.
- `events` : Les événements à écrire, dans l'ordre.
- `by` : Nom de la politique qui a arrêté la chaîne, ou `None`.

### Méthode `loom_ia.engine.hooks.Verdict.decided`

```python
@property
def decided(self) -> tuple[PolicyDecided, ...]
```

Les `policy.decided` des événements.

### Méthode `loom_ia.engine.hooks.Verdict.checked`

```python
@property
def checked(self) -> tuple[GuardChecked, ...]
```

Les `guard.checked` des événements.

### Méthode `loom_ia.engine.hooks.Verdict.replaced`

```python
@property
def replaced(self) -> bool
```

Vrai si une politique a remplacé la valeur (`Replace`).

### Méthode `loom_ia.engine.hooks.Verdict.unverified`

```python
@property
def unverified(self) -> bool
```

Vrai si un contrôle a gardé la sortie sans qu'elle respecte son contrat.

### Méthode `loom_ia.engine.hooks.Verdict.fallback`

```python
@property
def fallback(self) -> bool
```

Vrai si un contrôle a remplacé la sortie par son message de repli.

### Méthode `loom_ia.engine.hooks.Verdict.spent`

```python
@property
def spent(self) -> tuple[Usage, float]
```

Consommation des appels de modèle faits par les politiques (juges).

**Renvoie :**

Les tokens et le coût en dollars, sommés sur les
`model.responded` des événements.

## Classe `loom_ia.engine.hooks.Policies`

```python
class Policies
```

Politiques d'un agent, dans l'ordre déclaré.

**Arguments :**

- `bound` : Les politiques branchées.

**Attributs :**

- `bound` : Les politiques branchées, dans l'ordre.

### Méthode `loom_ia.engine.hooks.Policies.at`

```python
def at(self, point: HookPoint) -> tuple[BoundPolicy, ...]
```

Politiques branchées à ce point, dans l'ordre déclaré.

### Méthode `loom_ia.engine.hooks.Policies.run`

```python
async def run(self, subject: PolicySubject, *, call_id: str | None=None, ignore: frozenset[DecisionKind]=frozenset(), check_arguments: ArgumentsCheck | None=None, attempts: Mapping[str, int] | None=None, turns: tuple[tuple[Message, ...], ...]=(), summary: str | None=None) -> Verdict
```

Évalue la chaîne du point de `subject`.

`ignore` : décisions sans effet dans la situation (`Stop` pendant
la réponse forcée), traitées comme `Continue` et non journalisées.
`check_arguments` vérifie des arguments remplacés (`before_tool`).
`attempts` : réparations déjà demandées par politique, quand elles se
comptent ailleurs que dans le run (par appel, pour un rôle).

**Arguments :**

- `subject` : Le sujet ; son `point` désigne les politiques.
- `call_id` : Appel d'outil concerné, recopié dans les événements.
- `ignore` : Décisions traitées comme `Continue`.
- `check_arguments` : Contrôle des arguments remplacés.
- `attempts` : Réparations déjà demandées, par politique ; à défaut, `RunState.retries`.
- `turns` : Historique de la session, par tour, pour le contexte des politiques.
- `summary` : Dernier résumé de compaction.

**Renvoie :**

Le verdict. Une politique en erreur donne `Fail` avec
`on_error: block`, ou est sautée avec `allow` ; un `Retry`
au-delà de `max_attempts` devient `Fail`.

### Méthode `loom_ia.engine.hooks.Policies._decide`

```python
async def _decide(self, bound: BoundPolicy, subject: PolicySubject, context: PolicyContext, trace: Trace) -> Decision
```

Décision de la politique, contrôlée.

**Lève :**

- `PolicyFailure` : Délai dépassé, exception de la politique, valeur qui n'est pas une décision, décision non déclarée par la politique (`decisions`) ou non permise à ce point.

## Fonction `loom_ia.engine.hooks._replaced`

```python
def _replaced(subject: PolicySubject, value: object, check: ArgumentsCheck | None) -> PolicySubject
```

Sujet dont la valeur est remplacée.

`before_model` : une `ModelRequest` ; `before_tool` : un objet JSON
accepté par le schéma de l'outil ; `after_tool` : un `ToolOutput` ;
`on_output` : un message de l'assistant ou un texte.

**Lève :**

- `PolicyFailure` : Valeur du mauvais type, arguments refusés, ou point sans remplacement.

## Fonction `loom_ia.engine.hooks._checked`

```python
def _checked(bound: BoundPolicy, check: GuardCheck, call_id: str | None, context: PolicyContext) -> GuardChecked
```

`guard.checked` d'un contrôle enregistré par une politique.

## Fonction `loom_ia.engine.hooks._event`

```python
def _event(bound: BoundPolicy, point: HookPoint, decision: Decision, call_id: str | None, context: PolicyContext, reason: str | None=None, *, subject: PolicySubject | None=None, error: bool=False) -> PolicyDecided
```

`policy.decided` d'une décision.

Avec `Replace`, les arguments (`before_tool`) ou la sortie
(`on_output`) remplacés ; avec `Retry`, le rang de la réparation et les
outils permis.

**Arguments :**

- `bound` : La politique.
- `point` : Le point d'accroche.
- `decision` : La décision.
- `call_id` : Appel d'outil concerné.
- `context` : Contexte de la politique.
- `reason` : Raison à écrire à la place de celle de la décision.
- `subject` : Le sujet, valeurs remplacées comprises.
- `error` : Vrai si la politique est en erreur.
