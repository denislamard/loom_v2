# loom_ia.replay.evals

Module `loom_ia.replay.evals` de loom_v2 (fichier `src/loom_ia/replay/evals.py`).

Évaluer un agent : des cas, des attendus, des variantes à comparer (fonction O1, jalon J6.3a).

Une **suite** (YAML) désigne une config et un agent, et porte des **cas** :
une demande, et ce qu'on attend du run qu'elle lance. Chaque cas est joué pour
chaque **variante** — la config telle quelle, une autre config, ou un autre
modèle à une étape de l'agent (`main`, un rôle, `judge:<nom>`, comme la
variante du rejeu) —, `repeat` fois.

Deux sortes d'attendus :

- des **contrôles** déterministes : le statut du run ; son texte (contient,
  ne contient pas, motif) ; un champ de sa sortie structurée ; un outil
  appelé, avec une partie de ses arguments, ou jamais appelé ; une clé du
  state de la session après le run, avec sa valeur ou absente ;
- des **critères** notés par le **juge d'éval**, au format des juges de 3.3
  (`rule`, `min_score`) : il voit la demande et la réponse finale, et rend
  une note par critère. Il juge **hors du run** : il ne le change pas, et ce
  qu'il coûte est compté à part. Les juges propres à l'agent, eux, tournent
  dans le run comme configurés.

Un cas peut poser le **state de la session** à l'ouverture de son run
(`state`), comme l'application le fait par `Loom.run(state=…)` : chaque
cas part d'une session neuve.

Un cas peut aussi **rejouer des journaux** (`replay: journaux/*.jsonl`,
J6.3b) : chaque run fini de chaque fichier est rejoué à l'identique avec la
config de chaque variante, sans appeler personne, et passe s'il se rejoue tel
quel ; sinon le rapport dit où il s'écarte. Un tel cas n'a ni attendus ni
critères, n'est pas répété, ne dépense rien. Les journaux s'enregistrent avec
`loom eval suite.yaml --export journaux/` ; un motif sans fichier fait
tomber le cas.

Le monde, pendant une éval (décision du 06/10) : un outil à **effets de
bord** n'est **jamais** exécuté — sa doublure (`doubles`) répond à sa place,
sinon le modèle reçoit une erreur qui le dit. Les autres outils s'exécutent ;
les rôles tournent, les sous-agents aussi — sauf s'ils ont une doublure, qui
répond alors à leur place, comme à la variante du rejeu. Une approbation est
accordée quand rien ne part pour de vrai (doublure, refus), refusée pour une
exécution réelle : personne n'est là pour trancher.

Ce module décrit la suite, sert les outils et juge les résultats ; la façade
(`Loom.evaluate`, `loom eval`) monte chaque variante — en mémoire, hors des
quotas et des plafonds de période des clients (`isolated`) — et joue les cas.

## Constantes de `loom_ia.replay.evals`

- `NAME_PATTERN = '^[A-Za-z0-9_.-]{1,64}$'` : Un nom de cas ou de variante sert aussi de nom de fichier (`--export`).
- `BASE_VARIANT = 'base'` : Nom de la variante jouée quand la suite n'en déclare aucune : la config telle quelle.
- `EVAL_JUDGE = 'eval'` : Nom du juge d'éval dans ses appels de modèle (`judge:eval`) et ses verdicts.
- `EVAL_APPROVER = 'éval'` : Qui tranche une approbation pendant une éval.
- `REFUSED = "Non exécuté : éval. L'outil {name} a des effets de bord ({effects}) et la suite ne lui donne pas de doublure ; il n'es…` : Erreur rendue au modèle pour un outil à effets de bord sans doublure.
- `NOBODY = "éval : cet appel s'exécuterait pour de vrai, et personne n'est là pour l'approuver"` : Motif du refus d'une approbation pour un appel qui s'exécuterait pour de vrai.
- `IDENTICAL = "se rejoue à l'identique"` : Le contrôle d'un run rejoué (jalon J6.3b).
- `EvalFate = Literal['double', 'refused', 'run']` : Ce que devient un appel d'outil pendant une éval.
- `CheckKind = Literal['status', 'text', 'field', 'tool', 'judge', 'replay', 'state']` : Sorte d'un contrôle, telle que le rapport la donne.

## Classe `loom_ia.replay.evals.EvalError`

```python
class EvalError(Exception)
```

Suite impossible à jouer : agent, client, modèle ou doublure inconnus.

## Classe `loom_ia.replay.evals.EvalCriterion`

```python
class EvalCriterion(DomainModel)
```

Un critère noté par le juge d'éval : une règle et son seuil.

**Attributs :**

- `name` : Nom du critère, unique pour un cas (critères du juge et du cas confondus).
- `rule` : La règle que le juge note.
- `min_score` : Seuil de réussite, entre 0 et 1 (par défaut `DEFAULT_MIN_SCORE`).

### Méthode `loom_ia.replay.evals.EvalCriterion.criterion`

```python
@property
def criterion(self) -> Criterion
```

Le critère au format des juges de l'agent (`Criterion`).

## Classe `loom_ia.replay.evals.ToolExpectation`

```python
class ToolExpectation(DomainModel)
```

Un outil que le run doit appeler, avec au moins ces arguments.

**Attributs :**

- `name` : Nom de l'outil ; dans le YAML, un nom seul suffit (``called: [chercher_devis]``).
- `arguments` : Arguments attendus, comparés en partie : chacun doit être là, avec cette valeur ; un objet se compare lui-même en partie.

## Classe `loom_ia.replay.evals.Expect`

```python
class Expect(DomainModel)
```

Les contrôles déterministes d'un cas.

Sans `status`, le run doit finir `completed` : un run échoué, annulé ou
en pause fait tomber le cas, même si ses autres contrôles tiennent (un
`not_contains` ou un `not_called` tiennent d'un run mort). Avec
`status`, ce contrôle seul décide du statut : `status: failed` attend
l'échec.

`state` se lit sur le state de la session après le run, tel que le
journal l'a gardé : ce que l'application a posé, ce que les outils des
runs terminés ont écrit, sans valeur par défaut. `null` attend la clé
absente.

**Attributs :**

- `status` : Statut attendu du run ; sans lui, le run doit finir `completed`.
- `contains` : Textes que la réponse doit contenir (non vides).
- `not_contains` : Textes qu'elle ne doit pas contenir (non vides).
- `matches` : Motifs cherchés dans le texte, avec un délai (`core.bounded_regex`) ; `(?i)`, `(?s)` s'y écrivent.
- `fields` : Champs de la sortie structurée, par chemin pointé (`lignes.0.total`), et leur valeur.
- `called` : Outils que le run (son arbre) doit appeler, avec au moins ces arguments.
- `not_called` : Outils que le run ne doit jamais appeler.
- `state` : Clés du state de la session après le run, et leur valeur ; `null` : absente.

### Méthode `loom_ia.replay.evals.Expect.count`

```python
@property
def count(self) -> int
```

Nombre de contrôles déterministes déclarés.

## Classe `loom_ia.replay.evals.EvalCase`

```python
class EvalCase(DomainModel)
```

Une demande et ce qu'on attend du run qu'elle lance — ou des journaux à rejouer.

**Attributs :**

- `name` : Nom du cas, unique dans la suite ; il sert aussi de nom de fichier (`--export`).
- `input` : La demande envoyée à l'agent ; exclusif de `replay`.
- `replay` : Non-régression (jalon J6.3b) : motif des journaux dont chaque run doit se rejouer à l'identique, relatif à la suite (`journaux/*.jsonl`) ; exclusif de `input`, et sans attendus, critères, client ni state.
- `tenant` : Client au nom duquel le cas est joué ; à défaut, celui de la suite.
- `state` : State que l'application pose à l'ouverture du run (`Loom.run(state=…)`) : chaque cas part d'une session neuve, ses clés obligatoires comprises.
- `expect` : Contrôles déterministes.
- `criteria` : Critères propres au cas, notés par le juge d'éval après les siens ; ils exigent un `judge` dans la suite.

### Méthode `loom_ia.replay.evals.EvalCase.request`

```python
@property
def request(self) -> str
```

La demande du cas ; un cas de rejeu n'en a pas.

## Classe `loom_ia.replay.evals.EvalVariant`

```python
class EvalVariant(DomainModel)
```

Une façon de jouer les cas : la config, une autre, ou d'autres modèles par étape.

**Attributs :**

- `name` : Nom de la variante, unique dans la suite ; il sert aussi de nom de fichier (`--export`).
- `config` : Autre amorce (`loom-boot.yaml`), relative à la suite.
- `models` : Étape de l'agent (`main`, un rôle, `judge:<nom>`) → modèle déclaré.

## Classe `loom_ia.replay.evals.EvalJudge`

```python
class EvalJudge(DomainModel)
```

Le juge d'éval : un modèle de la config, et les critères de tous les cas.

**Attributs :**

- `model` : Modèle déclaré dans la config, qui sert de juge.
- `criteria` : Critères communs, notés pour chaque cas (sauf les cas de rejeu).
- `tool_results` : Outils dont il voit les résultats, dans l'arbre du run (décision du 06/10) : sans eux, il ne peut juger la réponse que sur la demande.

## Classe `loom_ia.replay.evals.EvalSuite`

```python
class EvalSuite(DomainModel)
```

Une suite d'évals, telle qu'un fichier YAML la décrit.

**Attributs :**

- `version` : Version du format ; seule `1` existe.
- `name` : Nom de la suite ; par défaut, celui de son fichier.
- `config` : Amorce de la config de l'agent (`loom-boot.yaml`), relative à la suite ; à défaut, celle de l'instance.
- `agent` : Agent évalué.
- `tenant` : Client par défaut des cas.
- `repeat` : Nombre de fois où chaque cas est joué, par variante.
- `max_cost_usd` : Plafond de dépense de la suite : runs et juge d'éval. Atteint, les runs suivants ne partent pas, et le rapport le dit.
- `doubles` : Outil → `module:fonction` (relatif à la suite) ou nom enregistré.
- `judge` : Juge d'éval ; requis dès qu'un cas porte des critères.
- `variants` : Variantes à comparer ; sans elles, la seule variante `base` : la config telle quelle.
- `cases` : Les cas, au moins un, de noms distincts.
- `base_dir` : Dossier du fichier de la suite, posé au chargement.

### Méthode `loom_ia.replay.evals.EvalSuite.title`

```python
@property
def title(self) -> str
```

Nom de la suite ; `suite` à défaut.

### Méthode `loom_ia.replay.evals.EvalSuite.criteria`

```python
def criteria(self, case: EvalCase) -> tuple[Criterion, ...]
```

Critères notés pour ce cas : ceux du juge, puis les siens ; aucun pour un rejeu.

### Méthode `loom_ia.replay.evals.EvalSuite.tenant_of`

```python
def tenant_of(self, case: EvalCase) -> TenantId | None
```

Client au nom duquel ce cas est joué : le sien, sinon celui de la suite.

**Arguments :**

- `case` : Le cas.

### Méthode `loom_ia.replay.evals.EvalSuite.journals`

```python
def journals(self, case: EvalCase) -> list[Path]
```

Les journaux d'un cas de rejeu, triés ; son motif est relatif à la suite.

### Méthode `loom_ia.replay.evals.EvalSuite.played_variants`

```python
def played_variants(self) -> tuple[EvalVariant, ...]
```

Les variantes jouées : celles de la suite, sinon la seule `base`.

### Méthode `loom_ia.replay.evals.EvalSuite.resolved`

```python
def resolved(self, path: Path | None) -> Path | None
```

Un chemin de la suite, rapporté à son dossier.

## Fonction `loom_ia.replay.evals.load_suite`

```python
def load_suite(path: Path | str) -> EvalSuite
```

Lit une suite d'évals ; `ConfigError` (fichier nommé) si elle ne tient pas.

## Fonction `loom_ia.replay.evals.isolated`

```python
def isolated(config: LoomConfig, scratch: Path) -> LoomConfig
```

La config telle qu'une éval la monte : rien ne sort du process, rien n'est décompté.

Le journal et ses fichiers vivent dans `scratch`, un dossier temporaire
que l'éval efface en finissant — en JSONL, parce qu'une approbation exige
un journal durable (point 28), même tranchée en ligne. File et bus en mémoire ;
pas de sceau, pas de collecteur ; ni quotas ni plafonds de période des
clients (leurs compteurs ne voient pas l'éval). Un magasin d'idempotence
partagé devient une base SQLite dans `scratch` : ce que demande une clé
métier, sans toucher à celle du service — ou, sans le paquet `aiosqlite`,
le journal de chaque run, ce qui suffit à tout outil sauf à clé métier
(son montage est alors refusé, en le disant). Le reste — agents, juges,
contrats, politiques, budgets d'un run et d'une session — est la logique
évaluée : il ne bouge pas.

## Fonction `loom_ia.replay.evals._without_period`

```python
def _without_period(budgets: Budgets) -> Budgets
```

Les budgets sans plafond de période : l'éval ne consomme rien chez le client.

## Classe `loom_ia.replay.evals.EvalTools`

```python
class EvalTools
```

Outils d'une éval (`ToolReplay`) : doublés ou refusés s'ils ont des effets de bord.

### Méthode `loom_ia.replay.evals.EvalTools.serves`

```python
def serves(self, tool: AnyTool, call: PendingCall, run_id: RunId) -> bool
```

Vrai si l'éval répond à la place de l'outil : doublure ou refus.

Un rôle tourne, un sous-agent sans doublure aussi (ses outils passent ici).
Le sort de chaque appel est décidé une fois, avant les références et les
politiques : doublé s'il a une doublure, refusé s'il a des effets de bord,
exécuté sinon.

**Arguments :**

- `tool` : L'outil appelé.
- `call` : L'appel.
- `run_id` : Run qui appelle.

### Méthode `loom_ia.replay.evals.EvalTools.from_journal`

```python
def from_journal(self, run_id: RunId, call_id: str) -> bool
```

Toujours faux : une éval ne lit aucun journal, doublure et refus sont des résultats neufs.

### Méthode `loom_ia.replay.evals.EvalTools.output`

```python
async def output(self, run_id: RunId, call_id: str, name: str, arguments: Mapping[str, JsonValue], resolved: Mapping[str, JsonValue]) -> tuple[ToolOutput, Consumption | None]
```

La réponse de la doublure, ou l'erreur qui dit au modèle que l'outil n'est pas exécuté (`REFUSED`).

### Méthode `loom_ia.replay.evals.EvalTools.answer`

```python
async def answer(self, run_id: RunId, pending: PendingQuestion) -> QuestionReply | NoAnswer
```

Personne ne répond pendant une évaluation : la question se clôt sans réponse.

### Méthode `loom_ia.replay.evals.EvalTools.approve`

```python
async def approve(self, run_id: RunId, pending: PendingApproval) -> ApprovalDecision
```

Accordée quand rien ne part pour de vrai ; refusée sinon.

## Fonction `loom_ia.replay.evals.eval_fate_label`

```python
def eval_fate_label(fate: EvalFate) -> str
```

Le sort d'un appel pendant une éval, en clair.

**Arguments :**

- `fate` : `double`, `refused` ou `run`.

## Classe `loom_ia.replay.evals.ToolUse`

```python
@dataclass(frozen=True, slots=True)
class ToolUse
```

Un appel d'outil du run (de son arbre), tel qu'il est parti.

**Attributs :**

- `name` : Nom de l'outil.
- `arguments` : Ce que l'outil a reçu : références `$ref` résolues, puis les arguments qu'une politique `before_tool` a mis à la place (décision du 06/10, jalon J6.3c).
- `fate` : Sort pendant l'éval ; `None` pour un rôle ou un sous-agent.
- `result` : Ce que l'appel a rendu, tel que le journal le garde (`None` : pas de résultat) — un résultat déporté n'y a que son aperçu.
- `is_error` : Le résultat est une erreur.
- `written` : Les arguments tels que le modèle les a écrits, quand l'outil en a reçu d'autres.

## Classe `loom_ia.replay.evals.Outcome`

```python
@dataclass(frozen=True, slots=True)
class Outcome
```

Ce qu'un run d'éval a rendu, de quoi le contrôler.

**Attributs :**

- `status` : Statut du run.
- `text` : Texte de la réponse finale.
- `data` : Sortie structurée, s'il y en a une.
- `error_type` : Type d'erreur d'un run en échec.
- `tools` : Appels d'outil de l'arbre du run.
- `state` : State de la session après le run, tel que le journal l'a gardé.

## Classe `loom_ia.replay.evals.CheckResult`

```python
@dataclass(frozen=True, slots=True)
class CheckResult
```

Un contrôle ou un critère, et ce qu'il a trouvé.

**Attributs :**

- `label` : Le contrôle ou le critère, en clair.
- `passed` : Réussi ou non.
- `detail` : Ce qui a été trouvé à la place, quand le contrôle tombe ; la note et le motif du juge pour un critère. Un contrôle de texte ne recopie pas le texte : le rapport le donne une fois, en entier, sous le run.
- `kind` : Sorte de contrôle : `status`, `text`, `field`, `tool`, `judge`, `replay` ou `state`.

### Méthode `loom_ia.replay.evals.CheckResult.as_json`

```python
def as_json(self) -> dict[str, JsonValue]
```

Le contrôle en JSON (`--json` de `loom eval`).

## Fonction `loom_ia.replay.evals.check`

```python
def check(expect: Expect, outcome: Outcome) -> list[CheckResult]
```

Les contrôles déterministes d'un cas, sur ce qu'un run a rendu.

## Fonction `loom_ia.replay.evals._state_check`

```python
def _state_check(key: str, wanted: JsonValue, state: Mapping[str, JsonValue]) -> CheckResult
```

Une clé du state après le run : sa valeur, ou son absence (`None`).

## Fonction `loom_ia.replay.evals.judged`

```python
def judged(scores: Sequence[CriterionScore]) -> list[CheckResult]
```

Les critères du juge d'éval, chacun réussi si sa note atteint son seuil.

## Fonction `loom_ia.replay.evals.unjudged`

```python
def unjudged(criteria: Sequence[Criterion], reason: str) -> list[CheckResult]
```

Les critères d'un run que le juge n'a pas pu noter : ils tombent, en le disant.

## Fonction `loom_ia.replay.evals._field`

```python
def _field(data: JsonValue, path: str) -> tuple[bool, JsonValue]
```

La valeur au chemin pointé `path` de la sortie structurée, si elle y est.

## Fonction `loom_ia.replay.evals._contains`

```python
def _contains(actual: Mapping[str, JsonValue], wanted: Mapping[str, JsonValue]) -> bool
```

Vrai si chaque argument attendu est là, avec cette valeur (objets comparés en partie).

## Classe `loom_ia.replay.evals.Judgment`

```python
@dataclass(frozen=True, slots=True)
class Judgment
```

Ce que le juge d'éval a rendu : les notes, ou pourquoi il n'en a pas.

**Attributs :**

- `scores` : Note de chaque critère.
- `error` : Pourquoi le juge n'a pas noté ; `None` s'il a noté.
- `usage` : Tokens du juge.
- `cost_usd` : Ce qu'il a coûté, en dollars.

## Classe `loom_ia.replay.evals.EvalJudgeClient`

```python
class EvalJudgeClient
```

Le juge d'éval : un modèle de la config, appelé hors du run, après lui.

### Méthode `loom_ia.replay.evals.EvalJudgeClient.judge`

```python
async def judge(self, criteria: tuple[Criterion, ...], request: str, outcome: Outcome, tool_results: Sequence[str]=()) -> Judgment
```

Les notes de la réponse finale ; une erreur du juge ne lève pas, elle se dit.

Le juge voit les critères, la demande, les résultats des outils nommés
(`tool_results`, chaque appel de l'arbre du run, erreurs comprises)
et la réponse finale.

### Méthode `loom_ia.replay.evals.EvalJudgeClient.aclose`

```python
async def aclose(self) -> None
```

Ferme le client du juge, s'il lui appartient (`owned`).

## Classe `loom_ia.replay.evals.EvalRun`

```python
@dataclass(frozen=True, slots=True)
class EvalRun
```

Un cas joué une fois pour une variante, et ce que ses contrôles en disent.

**Attributs :**

- `case` : Nom du cas.
- `variant` : Nom de la variante.
- `attempt` : Répétition, à partir de 1.
- `run_id` : Identifiant du run ; `None` s'il n'a pas été lancé.
- `status` : Statut final du run.
- `text` : Texte de la réponse.
- `failure` : Un run en échec : son type et son message.
- `checks` : Résultats des contrôles et des critères.
- `usage` : Tokens du run.
- `cost_usd` : Ce qu'a coûté le run, en dollars (sous-agents et juges de l'agent compris).
- `judge_cost_usd` : Ce qu'a coûté le juge d'éval, compté à part.
- `active_ms` : Temps actif du run, en millisecondes.
- `tools` : Appels d'outil de l'arbre du run.
- `error` : Le run n'a pas pu être lancé ou s'est interrompu : pourquoi.
- `skipped` : Non joué (plafond de dépense atteint) : pourquoi.
- `journal` : Cas de rejeu (jalon J6.3b) : le journal du run, rapporté à la suite.
- `divergence` : Cas de rejeu : là où le rejeu s'écarte du journal.

### Méthode `loom_ia.replay.evals.EvalRun.passed`

```python
@property
def passed(self) -> bool
```

Joué jusqu'au bout, et tous ses contrôles passent.

### Méthode `loom_ia.replay.evals.EvalRun.spent_usd`

```python
@property
def spent_usd(self) -> float
```

Ce qu'a coûté ce run, juge d'éval compris.

### Méthode `loom_ia.replay.evals.EvalRun.as_json`

```python
def as_json(self) -> dict[str, JsonValue]
```

Le run en JSON (`--json` de `loom eval`).

## Classe `loom_ia.replay.evals.VariantSummary`

```python
@dataclass(frozen=True, slots=True)
class VariantSummary
```

Une variante, cas par cas : combien de répétitions passent, et ce qu'elle a coûté.

**Attributs :**

- `name` : Nom de la variante.
- `cases` : Cas → (répétitions qui passent, répétitions jouées).
- `skipped` : Runs non joués (plafond de dépense atteint).
- `cost_usd` : Ce qu'ont coûté ses runs, en dollars.
- `judge_cost_usd` : Ce qu'a coûté le juge d'éval sur ses runs.

### Méthode `loom_ia.replay.evals.VariantSummary.passed_cases`

```python
@property
def passed_cases(self) -> int
```

Cas joués dont toutes les répétitions passent.

## Classe `loom_ia.replay.evals.EvalReport`

```python
@dataclass(frozen=True, slots=True)
class EvalReport
```

Ce qu'une suite a trouvé, variante par variante.

**Attributs :**

- `suite` : Nom de la suite.
- `agent` : Agent évalué.
- `repeat` : Répétitions par cas.
- `variants` : Variante → ce qui la distingue : sa config, ses modèles par étape.
- `cases` : Noms des cas, dans l'ordre de la suite.
- `runs` : Tous les runs, joués ou non, de toutes les variantes.
- `max_cost_usd` : Plafond de dépense de la suite.
- `judge_model` : Modèle du juge d'éval, s'il y en a un.
- `replays` : Cas de rejeu (jalon J6.3b) : leurs runs viennent de leurs journaux.

### Méthode `loom_ia.replay.evals.EvalReport.passed`

```python
@property
def passed(self) -> bool
```

Tout a été joué, et tout passe.

### Méthode `loom_ia.replay.evals.EvalReport.spent_usd`

```python
@property
def spent_usd(self) -> float
```

Dépense de la suite : runs et juge d'éval.

### Méthode `loom_ia.replay.evals.EvalReport.skipped`

```python
@property
def skipped(self) -> int
```

Runs non joués (plafond de dépense atteint).

### Méthode `loom_ia.replay.evals.EvalReport.summary`

```python
def summary(self, variant: str) -> VariantSummary
```

Le bilan d'une variante, cas par cas.

**Arguments :**

- `variant` : Nom de la variante.

### Méthode `loom_ia.replay.evals.EvalReport.as_json`

```python
def as_json(self) -> dict[str, JsonValue]
```

Le rapport en JSON (`--json` de `loom eval`) : la suite, le bilan de chaque variante, puis chaque run.

## Fonction `loom_ia.replay.evals.render_eval`

```python
def render_eval(report: EvalReport) -> list[str]
```

Les lignes de `loom eval` : variante par variante, cas par cas, puis le bilan.

## Fonction `loom_ia.replay.evals._case`

```python
def _case(case: str, runs: Sequence[EvalRun]) -> list[str]
```

Les lignes d'un cas pour une variante : sa marque, puis le détail de chaque essai qui tombe.

Pour un essai qui tombe : ses contrôles en échec et, si un contrôle de
texte tombe, le texte entier.

## Fonction `loom_ia.replay.evals._replayed`

```python
def _replayed(case: str, runs: Sequence[EvalRun]) -> list[str]
```

Un cas de rejeu : combien de runs se rejouent à l'identique, et où les autres s'écartent.
