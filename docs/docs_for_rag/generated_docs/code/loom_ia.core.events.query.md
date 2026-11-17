# loom_ia.core.events.query

Module `loom_ia.core.events.query` de loom_v2 (fichier `src/loom_ia/core/events/query.py`).

Recherche d'événements par enveloppe et facettes (point 22).

`EventQuery` porte les critères ; chaque store (mémoire, JSONL, SQLite,
Postgres) les applique. C'est ce que servent `GET /v1/events` et la
lecture du journal par l'API Python.

## Constantes de `loom_ia.core.events.query`

- `EVENTS_LIMIT = 100` : Événements rendus par page quand la recherche ne dit pas `limit`.
- `EVENTS_MAX = 10000` : Événements qu'une page peut rendre au plus (`limit` borné à cette valeur).
- `EVENT_ID_PATTERN = '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'` : Forme d'un identifiant d'événement : un UUID écrit comme loom les tire, en minuscules et avec ses tirets.  Le curseur `after` se compare en chaîne : une autre écriture (majuscules, sans tirets, ou un texte quelconque) se classerait ailleurs et rendrait, sans erreur, une page vide ou fausse.

## Classe `loom_ia.core.events.query.EventQuery`

```python
class EventQuery(DomainModel)
```

Critères d'une recherche d'événements dans le journal ; le client est toujours obligatoire (point 34).

Les résultats sont rendus **dans l'ordre où le journal les a reçus** (mémoire,
SQLite, Postgres) et `after` reprend la pagination après l'événement
indiqué *dans cet ordre*. L'`event_id` (UUIDv7) est posé à la création du
brouillon, avant l'écriture : un écrivain lent peut donc écrire après un
autre un événement d'identifiant plus petit. Trier ou borner sur l'id le
perdrait pour qui suit le journal ; l'ordre d'écriture, non. Un `after`
inconnu du journal (session effacée, id inventé) retombe sur la comparaison
des identifiants. Le journal JSONL, qui a un fichier par session et aucun
ordre commun, reste trié par `event_id` (`select`).

Tous les critères donnés doivent être vrais ; un critère vide ou `None`
ne filtre rien.

**Attributs :**

- `tenant_id` : Client dont lire le journal ; obligatoire, pour qu'aucune recherche ne traverse les clients.
- `session_id` : Session, ou toutes celles du client.
- `run_id` : Run, ou tous.
- `types` : Types d'événements acceptés (`tool.completed`…) ; vide : tous.
- `categories` : Catégories acceptées ; vide : toutes.
- `status` : Statuts acceptés (`ok`, `warning`, `error`) ; vide : tous.
- `agent` : Agent du run.
- `role` : Rôle LLM délégué, ou `judge:<nom>`.
- `tool_name` : Nom d'outil, cherché dans les facettes.
- `model_id` : Nom du modèle chez le fournisseur (`claude-haiku-5-5`), cherché dans les facettes.
- `model` : Identifiant du modèle dans la config (`HAIKU`), cherché dans les facettes ; absent des journaux antérieurs.
- `facets` : Égalité sur n'importe quelle facette, `{nom: valeur}`.
- `since` : Événements écrits à partir de cet instant (inclus).
- `until` : Événements écrits avant cet instant (exclu).
- `after` : Curseur de pagination : l'`event_id` du dernier événement de la page précédente.
- `limit` : Événements rendus au plus. De 1 à 10 000 ; 100 par défaut.

**Lève :**

- `ValueError` : `after` n'a pas la forme d'un identifiant d'événement.

### Méthode `loom_ia.core.events.query.EventQuery._check_after`

```python
@field_validator('after')
@classmethod
def _check_after(cls, value: EventId | None) -> EventId | None
```

Refuse un curseur qui n'a pas la forme d'un identifiant d'événement.

### Méthode `loom_ia.core.events.query.EventQuery.matches`

```python
def matches(self, event: Event) -> bool
```

Vrai si l'événement satisfait tous les critères, hors pagination (`after`, `limit`).

**Arguments :**

- `event` : Un événement écrit.

**Renvoie :**

Vrai si chaque critère donné est tenu, facettes comprises.

### Méthode `loom_ia.core.events.query.EventQuery.wanted_facets`

```python
def wanted_facets(self) -> dict[str, FacetValue]
```

Facettes exigées : `facets`, plus celles que nomment `tool_name`, `model_id` et `model`.

Les magasins SQL les traduisent dans leur requête.

**Renvoie :**

Nom de facette → valeur attendue.

### Méthode `loom_ia.core.events.query.EventQuery.refine`

```python
def refine(self, events: list[Event]) -> list[Event]
```

Rejoue les critères sur des événements qu'un magasin a déjà ordonnés.

L'ordre est celui de l'écriture, fixé par le magasin avec le curseur
`after` : ni tri ni curseur ici, seulement les critères et la borne.

**Arguments :**

- `events` : Événements dans l'ordre d'écriture, après le curseur.

**Renvoie :**

Ceux qui satisfont les critères, `limit` au plus.

### Méthode `loom_ia.core.events.query.EventQuery.select`

```python
def select(self, events: list[Event]) -> list[Event]
```

Filtre, trie par `event_id` et pagine une liste d'événements.

Sert au journal JSONL, qui n'a pas d'ordre d'écriture commun à ses
sessions.

**Arguments :**

- `events` : Événements à filtrer, dans n'importe quel ordre.

**Renvoie :**

Ceux qui satisfont les critères, triés par `event_id`, après
`after`, `limit` au plus.
