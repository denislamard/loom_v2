# loom_ia.core.projections.ledger

Module `loom_ia.core.projections.ledger` de loom_v2 (fichier `src/loom_ia/core/projections/ledger.py`).

Ledger : la consommation d'une session, appel par appel (fonctions J1 à J3 et J5).

Projection des `model.responded` du journal : chaque appel de modèle
(orchestrateur, rôle, juge, sous-agent) avec son run, son agent, son rôle
(celui de l'enveloppe : `main`, le nom du rôle, `judge:<nom>`), son
modèle — son identifiant dans la config et son nom chez le fournisseur —, son
usage et son coût. La consommation d'un sous-agent recopiée dans
le `tool.completed` de son appel n'y entre pas : ses appels y sont déjà,
dans son propre run.

S'y ajoutent les tentatives ratées que le fournisseur a facturées
(`model.retried` avec `usage`, fonction J1) : `failed`, elles comptent dans
l'usage et le coût, pas dans le nombre d'appels (`max_calls`).

La ventilation (par run, rôle, modèle, session) et le rapport se calculent
à partir de ces lignes ; le budget de session (`loom.budget`) en fait la
somme pour les runs qui précèdent le run en cours.

## Classe `loom_ia.core.projections.ledger.LedgerEntry`

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class LedgerEntry
```

Un appel de modèle, tel que le journal le rapporte : une ligne du ledger.

**Attributs :**

- `seq` : Position de l'événement dans le journal de la session.
- `ts` : Horodatage de l'événement.
- `tenant_id` : Client.
- `session_id` : Session.
- `run_id` : Run qui a fait l'appel.
- `root_run_id` : Run racine de son arbre.
- `agent` : Agent du run.
- `role` : `main` (l'orchestrateur), le nom d'un rôle délégué, ou `judge:<nom>`.
- `model_id` : Nom du modèle chez le fournisseur (`claude-haiku-5-5`).
- `model` : Identifiant du modèle dans la config (`HAIKU`) ; `None` pour un journal antérieur.
- `provider` : Fournisseur.
- `usage` : Tokens consommés.
- `cost` : Coût, en dollars.
- `call_id` : Appel d'outil servi (rôle délégué, juge d'un rôle).
- `judge` : Juge qui a fait l'appel.
- `failed` : Vrai pour une tentative ratée, facturée par le fournisseur : elle compte dans l'usage et le coût, pas comme un appel (fonction J1).

### Méthode `loom_ia.core.projections.ledger.LedgerEntry.spent`

```python
@property
def spent(self) -> Spent
```

Consommation de la ligne : usage, coût, et 1 appel (0 pour une tentative ratée).

### Méthode `loom_ia.core.projections.ledger.LedgerEntry.model_key`

```python
@property
def model_key(self) -> str
```

Modèle de l'appel, pour ventiler : son id dans la config, sinon son nom chez le fournisseur.

## Fonction `loom_ia.core.projections.ledger.ledger`

```python
def ledger(events: Iterable[Event]) -> tuple[LedgerEntry, ...]
```

Appels de modèle d'un ensemble d'événements, dans l'ordre du journal.

**Arguments :**

- `events` : Événements d'une session, d'un run ou d'un arbre.

**Renvoie :**

Une ligne par `model.responded`, et une par `model.retried`
facturé (`failed`).

## Fonction `loom_ia.core.projections.ledger.spent`

```python
def spent(events: Iterable[Event]) -> Spent
```

Consommation totale des appels de modèle d'un ensemble d'événements.

**Arguments :**

- `events` : Événements à compter.

**Renvoie :**

La somme des lignes du ledger : usage, coût et nombre d'appels.
