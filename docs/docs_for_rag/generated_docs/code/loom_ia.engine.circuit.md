# loom_ia.engine.circuit

Module `loom_ia.engine.circuit` de loom_v2 (fichier `src/loom_ia/engine/circuit.py`).

Disjoncteurs des modèles et des serveurs MCP (points 10 et 19, backlog #011).

Un disjoncteur compte les échecs de suite d'une cible : un modèle dont
l'appel a échoué après ses nouvelles tentatives (`transient`,
`overloaded`, `quota_exhausted`), ou un serveur MCP dont la connexion a
échoué. Au seuil (`failures`), il s'ouvre : la cible est écartée pendant
`cooldown` secondes, pour tous les runs qui partagent les disjoncteurs
(ceux d'une instance `Loom`). Un modèle écarté passe directement la main à
son secours ; un serveur MCP écarté est déclaré indisponible sans nouvel
essai.

À la fin de la pause, la cible est de nouveau appelée : réussi, cet essai
referme le disjoncteur ; raté, il le rouvre pour une nouvelle pause. Une
réussite remet toujours le compte à zéro.

Les erreurs propres à une requête (`invalid_request`, `context_overflow`,
`content_filtered`) et les erreurs de config (`auth`) ne comptent pas :
elles ne disent rien de la disponibilité de la cible.

Réglage : `circuit_breaker` d'un modèle ou d'un serveur MCP (5 échecs, 60 s
par défaut ; `null` le retire). L'ouverture écrit un `circuit.opened`.

## Constantes de `loom_ia.engine.circuit`

- `CIRCUIT_ERRORS = frozenset({'transient', 'overloaded', 'quota_exhausted'})` : Genres d'erreur d'un modèle qui comptent pour son disjoncteur.

## Fonction `loom_ia.engine.circuit.model_key`

```python
def model_key(model_id: str) -> str
```

Clé du disjoncteur d'un modèle (identifiant dans la config).

## Fonction `loom_ia.engine.circuit.mcp_key`

```python
def mcp_key(server: str) -> str
```

Clé du disjoncteur d'un serveur MCP.

## Classe `loom_ia.engine.circuit._Circuit`

```python
@dataclass
class _Circuit
```

État du disjoncteur d'une cible.

**Attributs :**

- `failures` : Échecs de suite.
- `until` : Fin de la pause, selon l'horloge ; `None` : disjoncteur fermé.
- `trial` : Vrai pour l'essai d'après la pause : raté, le disjoncteur se rouvre aussitôt.

## Classe `loom_ia.engine.circuit.Tripped`

```python
@dataclass(frozen=True, slots=True)
class Tripped
```

Disjoncteur que le dernier échec vient d'ouvrir ; de quoi écrire le `circuit.opened`.

**Attributs :**

- `failures` : Échecs de suite qui l'ont ouvert ; 1 pour un essai raté après une pause.
- `cooldown` : Durée de la pause, en secondes.

## Classe `loom_ia.engine.circuit.CircuitBreakers`

```python
class CircuitBreakers
```

Disjoncteurs partagés par des runs, un par cible (`model:<id>`, `mcp:<nom>`).

Une instance `Loom` en tient un jeu, partagé par tous ses runs.

**Arguments :**

- `clock` : Horloge monotone, en secondes ; remplaçable pour les tests.

### Méthode `loom_ia.engine.circuit.CircuitBreakers.remaining`

```python
def remaining(self, key: str) -> float | None
```

Secondes de pause restantes si la cible est écartée ; `None` si elle peut être appelée.

**Arguments :**

- `key` : Clé de la cible (`model_key`, `mcp_key`).

### Méthode `loom_ia.engine.circuit.CircuitBreakers.failed`

```python
def failed(self, key: str, setting: CircuitBreaker) -> Tripped | None
```

Compte un échec de la cible.

**Arguments :**

- `key` : Clé de la cible.
- `setting` : Réglage du disjoncteur (`failures`, `cooldown`).

**Renvoie :**

Le disjoncteur s'il vient de s'ouvrir (au seuil, ou à l'essai raté
d'après une pause) ; `None` sinon.

### Méthode `loom_ia.engine.circuit.CircuitBreakers.succeeded`

```python
def succeeded(self, key: str) -> None
```

Appel ou connexion réussis : le disjoncteur se referme, son compte repart de zéro.
