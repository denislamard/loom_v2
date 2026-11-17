# loom_ia.usage.report

Module `loom_ia.usage.report` de loom_v2 (fichier `src/loom_ia/usage/report.py`).

Rapport de consommation d'un run ou d'une session (fonctions J3 et J5).

Le rapport se calcule à partir du ledger (`core.projections.ledger`) :
chaque appel de modèle du journal, sans double compte des sous-agents. Il
ventile la consommation par run (le run et ses sous-runs, ou tous les runs
de la session), par rôle (`main`, rôles délégués, `judge:<nom>`,
préfixés par l'agent quand plusieurs agents ont travaillé) et par modèle — son
identifiant dans la config, qui distingue deux déclarations d'un même modèle du
fournisseur ; à défaut (journal antérieur), son nom chez le fournisseur.
Par client et par période : fonction J5 (`tenancy/usage.py`).

Les tentatives ratées que le fournisseur a facturées (fonction J1) sont comprises dans
toutes les lignes (usage et coût, pas le nombre d'appels), et rappelées à part
dans `failed` : leur nombre, leur usage et leur coût.

Accès : `Loom.report()`, `loom report <run_id>` ou `--session`, et la
sortie `report` des outils MCP.

## Constantes de `loom_ia.usage.report`

- `_CLOSED = {RunCompleted: 'completed', RunFailed: 'failed', RunCancelled: 'cancelled'}` : Ce que devient un run à l'événement qui le clôt.

## Classe `loom_ia.usage.report.UsageLine`

```python
class UsageLine(DomainModel)
```

Consommation d'une part : un run, un rôle, un modèle, ou le total.

**Attributs :**

- `name` : Ce que la ligne compte : run, rôle, modèle ou `total`.
- `calls` : Appels de modèle réussis.
- `usage` : Tokens, tentatives ratées facturées comprises.
- `cost` : Coût en dollars, tentatives ratées facturées comprises.

## Classe `loom_ia.usage.report.ModelUsage`

```python
class ModelUsage(UsageLine)
```

Consommation d'un modèle (`name` : son identifiant dans la config).

**Attributs :**

- `model_id` : Nom chez le fournisseur ; égal à `name` pour un journal antérieur, qui n'a pas l'identifiant de la config.

## Classe `loom_ia.usage.report.RunUsage`

```python
class RunUsage(UsageLine)
```

Consommation d'un run seul (`name` : son identifiant), sans ses sous-runs.

**Attributs :**

- `agent` : Agent du run.
- `depth` : Profondeur : 0 pour le run racine, 1 pour un sous-agent…
- `status` : `completed`, `failed`, `cancelled`, ou `None` pour un run pas encore clos.

## Classe `loom_ia.usage.report.UsageReport`

```python
class UsageReport(DomainModel)
```

Consommation d'un run et de ses sous-runs, ou de toute une session.

**Attributs :**

- `session_id` : La session.
- `run_id` : Run racine du rapport ; `None` pour toute la session.
- `total` : Le total.
- `runs` : Une ligne par run, sous-runs compris, dans l'ordre du journal.
- `roles` : Une ligne par rôle (`main`, rôles délégués, `judge:<nom>` ; « agent · rôle » quand plusieurs agents ont travaillé).
- `models` : Une ligne par modèle de la config.
- `failed` : Tentatives ratées facturées, déjà comprises ci-dessus ; `calls` : leur nombre. `None` quand il n'y en a pas.

## Fonction `loom_ia.usage.report.usage_report`

```python
def usage_report(events: Sequence[Event], session_id: SessionId, run_id: RunId | None=None) -> UsageReport
```

Rapport des événements d'une session ; avec `run_id`, de ce run et de ses sous-runs.

**Arguments :**

- `events` : Les événements de la session.
- `session_id` : La session.
- `run_id` : Run racine, ou `None` pour toute la session.

**Renvoie :**

Le rapport.

## Fonction `loom_ia.usage.report.render`

```python
def render(report: UsageReport) -> list[str]
```

Le rapport en lignes de texte, pour la CLI (`loom report`).

**Arguments :**

- `report` : Le rapport.

**Renvoie :**

Les lignes : total, par run, par rôle, par modèle, puis les
tentatives ratées facturées s'il y en a.

## Fonction `loom_ia.usage.report._model_label`

```python
def _model_label(line: ModelUsage) -> str
```

`HAIKU (claude-haiku-5-5)` ; le nom seul quand le journal n'avait pas l'identifiant.

## Fonction `loom_ia.usage.report._attempts`

```python
def _attempts(entries: Sequence[LedgerEntry]) -> UsageLine | None
```

Les tentatives ratées facturées : `calls` en donne le nombre.

## Fonction `loom_ia.usage.report._grouped`

```python
def _grouped(entries: Sequence[LedgerEntry], key: Callable[[LedgerEntry], str]) -> tuple[UsageLine, ...]
```

Une ligne par clé, dans l'ordre de première apparition.

## Fonction `loom_ia.usage.report._by`

```python
def _by[K](entries: Iterable[LedgerEntry], key: Callable[[LedgerEntry], K]) -> dict[K, list[LedgerEntry]]
```

Les appels rangés par clé, dans l'ordre de première apparition.
