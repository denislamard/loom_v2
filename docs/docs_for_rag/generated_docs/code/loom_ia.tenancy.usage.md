# loom_ia.tenancy.usage

Module `loom_ia.tenancy.usage` de loom_v2 (fichier `src/loom_ia/tenancy/usage.py`).

Budget d'un client sur une période : le compteur et le contrôle (fonctions J4 et L3, conception §15).

Le contrôle a lieu **au lancement d'un run**, une fois, et pas avant chaque
appel de modèle comme le budget d'un run. Deux raisons. La sémantique : une
enveloppe quotidienne épuisée ne veut pas dire « réponds vite », elle veut
dire « reviens demain », et un run refusé n'écrit rien au journal. Le coût :
lire la dépense d'un client demande d'agréger toutes ses sessions, ce qu'on
ne fait pas quatre fois par run.

Le compteur est un cache, reconstructible : à la première question posée pour
un client et une période, `TenantUsage` le **réchauffe** en relisant les
`model.responded` (et `model.retried` facturés) du journal depuis le début
de la fenêtre ; la vie courante l'entretient — un run enregistre ce qu'il a
coûté en se terminant. Comme `record` pose une valeur par run racine au lieu
de l'ajouter, les deux sources peuvent se chevaucher sans jamais compter deux
fois.

**Chaque contrôle relit la suite du journal** (essai N du 23/10). Le journal
est commun à tous les process — un autre `loom serve`, un worker, ``loom
run`` — et le compteur ne l'est pas : réchauffé une seule fois par fenêtre,
celui d'un `serve` qui dure ne voyait plus que ses propres runs, et deux
`serve` sur un plafond de 2 615 tokens en ont laissé passer 4 184. Avant de
répondre, `check` lit donc ce qui a été écrit depuis sa dernière lecture
(`_Window` : les totaux par run tirés du journal, les événements déjà vus,
et une borne qui recule de `OVERLAP` pour rattraper un événement écrit en
retard) et repose le total de chaque run touché. Reste l'écart des runs
admis ensemble, avant qu'aucun n'ait consommé : le contrôle a lieu au
lancement.

La lecture est **atomique** : une seule à la fois par client et par période
(un verrou par couple, un client lent n'attend pas les autres), et ce qu'elle
apprend n'est retenu qu'**une fois la lecture réussie**. Les `check()`
concurrents attendent donc un compteur rempli, pas un compteur vide ; une
lecture qui échoue se propage à l'appelant — le run est refusé, rien n'est
écrit — au lieu de laisser le plafond ouvert, et le contrôle suivant retente.

Le rapport (`consumption`), lui, ne passe pas par le compteur : il relit le
journal à chaque appel. C'est une question qu'un humain pose, pas une
vérification par run, et elle doit valoir même pour un client sans budget —
dont le compteur, justement, ne retient rien.

Config : `budgets.tenant` (`max_cost_per_day`, `max_tokens_per_day`,
`max_cost_per_month`, `max_tokens_per_month`), à la racine ou dans la
fiche d'un client.

## Constantes de `loom_ia.tenancy.usage`

- `MODEL_RESPONDED = 'model.responded'` : Type des appels aboutis, lus au réchauffage.
- `MODEL_RETRIED = 'model.retried'` : Type des tentatives ratées ; le ledger garde celles que le fournisseur a facturées (fonction J1).
- `PAGE = 5000` : Événements lus par requête au réchauffage ; la pagination fait le reste.
- `OVERLAP = timedelta(minutes=2)` : Recul de la borne de lecture.  Un événement daté avant la dernière lecture mais écrit après (un process plus lent, une horloge en retard) est rattrapé ; les événements déjà vus sont écartés par leur identifiant.

## Classe `loom_ia.tenancy.usage.Exceeded`

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class Exceeded
```

Une limite de client atteinte : ce qu'elle valait, ce qui a été dépensé.

**Attributs :**

- `period` : La fenêtre.
- `limit` : `max_cost` ou `max_tokens`.
- `value` : Le plafond.
- `spent` : La dépense.

## Classe `loom_ia.tenancy.usage.BudgetExhausted`

```python
class BudgetExhausted(RuntimeError)
```

Le client a épuisé son budget de la période : le run n'est pas lancé.

`retry_after` porte les secondes jusqu'à la remise à zéro de la fenêtre,
de quoi répondre honnêtement à l'appelant (`Retry-After` en HTTP).

**Arguments :**

- `tenant_id` : Le client.
- `reached` : La limite atteinte.
- `retry_after` : Secondes jusqu'à la remise à zéro.

**Attributs :**

- `tenant_id` : Le client.
- `reached` : La limite atteinte.
- `retry_after` : Secondes jusqu'à la remise à zéro.

## Classe `loom_ia.tenancy.usage.TenantConsumption`

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class TenantConsumption
```

Ce qu'un client a dépensé sur une fenêtre, et ce qu'elle lui permettait.

**Attributs :**

- `tenant_id` : Le client.
- `period` : La fenêtre.
- `spent` : La dépense : coût, tokens, appels.
- `limits` : Plafonds posés pour cette fenêtre : limite et valeur.
- `runs` : Runs racines comptés.

### Méthode `loom_ia.tenancy.usage.TenantConsumption.resets_at`

```python
@property
def resets_at(self) -> datetime
```

Instant de la remise à zéro, en UTC.

### Méthode `loom_ia.tenancy.usage.TenantConsumption.left`

```python
def left(self, limit: BudgetLimit) -> float | None
```

Ce qui reste sur une limite ; `None` si elle n'est pas posée.

**Arguments :**

- `limit` : `max_cost` ou `max_tokens`.

## Classe `loom_ia.tenancy.usage._Window`

```python
@dataclass(slots=True)
class _Window
```

Ce que ce process a lu du journal pour un client et une période.

**Attributs :**

- `period` : La fenêtre.
- `since` : Prochaine lecture à partir de cette date (le début de la fenêtre, puis la dernière date lue moins `OVERLAP`).
- `totals` : Dépense par run racine, tirée du journal : la somme de ce qui a été lu.
- `seen` : Événements déjà comptés, et leur date : ceux d'avant `since` sont oubliés.

## Classe `loom_ia.tenancy.usage.TenantUsage`

```python
class TenantUsage
```

Compteur de consommation par client, et contrôle de son budget de période.

**Arguments :**

- `counter` : Le compteur (port `UsageCounter`) : en mémoire, ou partagé et durable.
- `store` : Le journal, relu pour réchauffer le compteur et pour les rapports.
- `warm` : Faux pour un compteur partagé et durable (jalon J5.3), qui n'a rien à réchauffer.

### Méthode `loom_ia.tenancy.usage.TenantUsage.check`

```python
async def check(self, tenant: Tenant, now: datetime | None=None) -> None
```

Lève `BudgetExhausted` si le client n'a plus de quoi lancer un run.

Si le journal ne peut pas être relu pour réchauffer le compteur, l'erreur
de lecture se propage telle quelle : le run n'est pas lancé.

**Arguments :**

- `tenant` : Le client, et ses plafonds par période.
- `now` : L'instant, avec un fuseau ; maintenant par défaut.

**Lève :**

- `BudgetExhausted` : Une limite d'une fenêtre est atteinte.

### Méthode `loom_ia.tenancy.usage.TenantUsage.record`

```python
async def record(self, tenant: Tenant, run_id: RunId, spent: Spent, now: datetime | None=None) -> None
```

Enregistre ce qu'un run racine a coûté, dans les fenêtres du client.

Rien n'est retenu pour un client sans budget : le compteur ne sert qu'à
`check`, et le rapport relit le journal.

**Arguments :**

- `tenant` : Le client.
- `run_id` : Le run racine.
- `spent` : Ce qu'il a coûté, sous-runs compris.
- `now` : L'instant ; maintenant par défaut.

### Méthode `loom_ia.tenancy.usage.TenantUsage.consumption`

```python
async def consumption(self, tenant_id: TenantId, budget: TenantBudget | None=None, kind: BudgetPeriod='day', now: datetime | None=None) -> TenantConsumption
```

Dépense d'un client sur une fenêtre, relue dans le journal.

Sert à `loom report --period` et `Loom.consumption()`.

**Arguments :**

- `tenant_id` : Le client.
- `budget` : Ses plafonds par période, pour `limits` ; `None` : aucun.
- `kind` : `day` ou `month`.
- `now` : L'instant ; maintenant par défaut.

**Renvoie :**

La dépense, le nombre de runs et les plafonds de la fenêtre.

### Méthode `loom_ia.tenancy.usage.TenantUsage._caught_up`

```python
async def _caught_up(self, tenant_id: TenantId, period: Period) -> None
```

Lit ce que le journal a reçu depuis la dernière lecture, et le porte au compteur.

### Méthode `loom_ia.tenancy.usage.TenantUsage._forget_before`

```python
def _forget_before(self, tenant_id: TenantId, period: Period) -> None
```

Oublie les fenêtres de ce client finies avant celle-ci (même genre).

### Méthode `loom_ia.tenancy.usage.TenantUsage._read`

```python
async def _read(self, tenant_id: TenantId, since: datetime) -> list[Event]
```

Appels aboutis et tentatives facturées du client depuis `since`, toutes pages.

### Méthode `loom_ia.tenancy.usage.TenantUsage._from_journal`

```python
async def _from_journal(self, tenant_id: TenantId, period: Period) -> dict[RunId, Spent]
```

Dépense par run racine, lue dans le journal depuis le début de la fenêtre.

Appels aboutis et tentatives ratées facturées (fonction J1) : un rapport de période
ou un plafond de client qui ne lirait que les premiers sous-compterait
(essai de la fonction J5, 21/10).

Les totaux sont assemblés sur **toutes** les pages avant d'être rendus :
un run dont les appels se répartissent sur deux pages ne doit pas être
réduit à ceux de la dernière.
