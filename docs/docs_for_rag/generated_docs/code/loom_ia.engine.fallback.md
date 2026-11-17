# loom_ia.engine.fallback

Module `loom_ia.engine.fallback` de loom_v2 (fichier `src/loom_ia/engine/fallback.py`).

Chaîne de secours d'un appel de modèle (fonction B4, points 7 et 10).

Un emplacement (`main`, un rôle, un juge) déclare son modèle et ses
secours, dans l'ordre : `model: M3_MAIN, fallbacks: [SONNET]`.
`ModelChain` appelle le modèle courant de l'emplacement, avec ses
nouvelles tentatives (`ModelCall`), puis passe au suivant selon l'erreur :

- `transient`, `overloaded` : une fois les tentatives épuisées ;
- `quota_exhausted` : aussitôt ;
- `context_overflow` : vers le premier secours dont la fenêtre déclarée
  (`capabilities.context_window`) dépasse celle du modèle qui a débordé ;
- `auth`, `invalid_request`, `content_filtered` : jamais, l'appel échoue.

Un modèle écarté par son disjoncteur est sauté sans être appelé (motif
`circuit_open`). Chaque bascule écrit un `model.fell_back` ; un échec qui
ouvre un disjoncteur, un `circuit.opened`. Si la fin de la chaîne est
écartée par son disjoncteur, l'appel échoue en `unavailable`.

Adhérence : un run qui a basculé garde le secours pour cet emplacement
jusqu'à la fin (`RunState.models`, projeté des `model.fell_back`) ; la
chaîne repart de ce modèle.

Requête d'un secours : celle du modèle précédent, avec l'identifiant, le
`max_tokens` et les `params` du secours (ceux de l'emplacement
par-dessus). Le raisonnement produit par un autre modèle est retiré des
messages (point 7) : un fournisseur ne relit que le sien. Le cache de prompt est
perdu, et le coût suit le tarif du secours.

Outil imposé : un modèle déclaré `capabilities.forced_tool_choice: false`
(Claude Opus 5.5, Fable 5.1) refuse `tool_choice: required`. Sa requête part
en `auto` (`sendable`) : c'est la consigne qui demande l'appel (verdict d'un
juge), et le journal porte la requête réellement envoyée.

Diffusion : si le modèle qui a échoué avait déjà diffusé des morceaux,
`on_chunk` reçoit un `StreamReset` avant ceux du secours.

## Constantes de `loom_ia.engine.fallback`

- `FALLBACK_ERRORS = CIRCUIT_ERRORS` : Erreurs qui font passer au modèle suivant, tentatives épuisées.  Ce sont celles qui comptent pour le disjoncteur (`transient`, `overloaded`, `quota_exhausted`) : un modèle qu'on quitte pour elles est un modèle en difficulté ; les écrire deux fois les laissait diverger.
- `ChainEvent = ModelExchanged | ModelRetried | ModelFellBack | CircuitOpened` : Événement produit pendant l'appel d'une chaîne, avant la réponse.

## Classe `loom_ia.engine.fallback.ModelLink`

```python
@dataclass(frozen=True, slots=True)
class ModelLink
```

Un modèle d'une chaîne : sa définition et son client.

**Attributs :**

- `spec` : Définition du modèle dans la config.
- `client` : Client de son fournisseur.

## Classe `loom_ia.engine.fallback.Answered`

```python
@dataclass(frozen=True, slots=True)
class Answered
```

Réponse obtenue, et le modèle de la chaîne qui l'a donnée.

**Attributs :**

- `response` : La réponse.
- `spec` : Le modèle qui l'a donnée.
- `request` : Requête envoyée à ce modèle, avant la résolution des fichiers.
- `attempts` : Tentatives sur ce modèle, la dernière comprise.

## Classe `loom_ia.engine.fallback._Tracked`

```python
@dataclass
class _Tracked
```

Relais de `on_chunk` qui note si des morceaux sont partis.

## Classe `loom_ia.engine.fallback.ModelChain`

```python
@dataclass(frozen=True, kw_only=True)
class ModelChain
```

Modèle d'un emplacement et ses secours, dans l'ordre.

**Attributs :**

- `links` : Le modèle de l'emplacement puis ses secours (`fallbacks`).
- `slot` : `main`, le nom d'un rôle ou `judge:<nom>` : recopié dans `model.fell_back`.
- `breakers` : Disjoncteurs partagés de l'instance ; `None` : aucun.
- `max_tokens` : Réglage de l'emplacement (fonction B6) : remplace celui de chaque modèle.
- `params` : Paramètres de l'emplacement, par-dessus ceux de chaque modèle.
- `on_chunk` : Diffusion en direct de la réponse.
- `artifacts` : Magasin de fichiers, pour résoudre les références.
- `sleep` : Attente entre deux tentatives ; remplaçable pour les tests.
- `jitter` : Tirage de la part aléatoire du délai ; remplaçable pour les tests.

**Lève :**

- `ValueError` : À la construction, si `links` est vide.

### Méthode `loom_ia.engine.fallback.ModelChain.position`

```python
def position(self, current: str | None) -> int
```

Rang du modèle courant de l'emplacement ; 0 s'il n'a pas basculé.

**Arguments :**

- `current` : Identifiant du modèle courant (`RunState.models`) ; un modèle retiré de la config depuis compte comme 0.

### Méthode `loom_ia.engine.fallback.ModelChain.link`

```python
def link(self, current: str | None) -> ModelLink
```

Modèle à appeler en premier : le courant de l'emplacement (adhérence).

**Arguments :**

- `current` : Identifiant du modèle courant, ou `None`.

### Méthode `loom_ia.engine.fallback.ModelChain.request_for`

```python
def request_for(self, spec: ModelSpec, request: ModelRequest) -> ModelRequest
```

Requête adaptée à un modèle de la chaîne : ses réglages, son raisonnement seul.

**Arguments :**

- `spec` : Le modèle.
- `request` : La requête du modèle précédent.

**Renvoie :**

La requête avec le nom du modèle, son `max_tokens` (ou celui de
l'emplacement), ses `params` sous ceux de l'emplacement, sans le
raisonnement d'un autre modèle, et sans outil imposé s'il le
refuse.

### Méthode `loom_ia.engine.fallback.ModelChain.run`

```python
async def run(self, request: ModelRequest, *, current: str | None=None) -> AsyncGenerator[ChainEvent | Answered]
```

Événements de l'appel (tentatives, bascules, disjoncteurs), puis la réponse.

`request` est construite pour le modèle `current` (celui de
`link(current)`).

**Arguments :**

- `request` : La requête.
- `current` : Modèle courant de l'emplacement (adhérence).

**Produit :**

`model.exchanged`, `model.retried`, `model.fell_back` et
`circuit.opened` à mesure, puis `Answered` en dernier.

**Lève :**

- `ModelError` : Aucun modèle n'a répondu : erreur définitive, fin de chaîne atteinte, ou `unavailable` si les derniers modèles sont écartés par leur disjoncteur.

### Méthode `loom_ia.engine.fallback.ModelChain._following`

```python
def _following(self, index: int, error: ModelError) -> int | None
```

Rang du modèle qui prend la suite après cette erreur, ou `None` : l'appel échoue.

`FALLBACK_ERRORS` : le suivant ; `context_overflow` : le premier
dont la fenêtre déclarée est plus grande ; toute autre erreur : aucun.

## Fonction `loom_ia.engine.fallback.sendable`

```python
def sendable(spec: ModelSpec, request: ModelRequest) -> ModelRequest
```

Requête que ce modèle accepte : sans outil imposé s'il le refuse.

`tool_choice: required` devient `auto` pour un modèle déclaré
`capabilities.forced_tool_choice: false` ; le reste est inchangé.

**Arguments :**

- `spec` : Le modèle.
- `request` : La requête.

**Renvoie :**

La requête à envoyer.

## Fonction `loom_ia.engine.fallback.own_reasoning`

```python
def own_reasoning(messages: Sequence[Message], model: str) -> tuple[Message, ...]
```

Messages sans le raisonnement produit par un autre modèle (point 7).

Un raisonnement sans modèle (journal antérieur au marquage) est gardé.

**Arguments :**

- `messages` : Les messages.
- `model` : Nom du modèle chez le fournisseur.

**Renvoie :**

Les messages, sans les blocs de raisonnement d'un autre modèle.

## Fonction `loom_ia.engine.fallback._unavailable`

```python
def _unavailable(skipped: list[str], failure: ModelError | None) -> ModelError
```

Fin de chaîne écartée par son disjoncteur.
