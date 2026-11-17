# loom_ia.core.model.model_spec

Module `loom_ia.core.model.model_spec` de loom_v2 (fichier `src/loom_ia/core/model/model_spec.py`).

Définition d'un LLM, déclarée sous `models` dans `loom.yaml` (points 9 et 10, fonctions B1 à B9).

Le `sdk` choisit l'adaptateur ; le fournisseur n'est qu'une affaire de
config (`base_url`, clé, capacités). La config ne contient jamais la clé,
seulement le nom de la variable qui la porte (`api_key_env`). Un agent, un
rôle ou un juge désigne un modèle par son `id`.

Sans `base_url`, l'adaptateur prend l'adresse officielle du fournisseur de
son SDK, jamais celle d'une variable d'environnement (`ANTHROPIC_BASE_URL`,
`OPENAI_BASE_URL`) : seule la config décide où partent les requêtes.

Exemple dans `loom.yaml` :

```
models:
- id: HAIKU
  sdk: anthropic
  model: claude-haiku-5-5
  api_key_env: ANTHROPIC_API_KEY
  max_tokens: 4096
  capabilities: { vision: true, context_window: 200000 }
  pricing: { input: 1.0, output: 5.0 }
- id: QWEN
  sdk: openai
  model: qwen3-32b
  base_url: http://localhost:8000/v1
  capabilities: { thinking: true }
```

Contenu : `ModelSpec` et ses blocs `ModelTimeouts`, `RetryPolicy`,
`CircuitBreaker`, `PromptCache`, `ModelCapabilities`.

## Constantes de `loom_ia.core.model.model_spec`

- `Sdk = Literal['anthropic', 'openai', 'fake']` : Adaptateur d'un modèle : `anthropic` (aussi MiniMax), `openai` (aussi Together, vLLM, Ollama) ou `fake` (modèle simulé des tests).
- `CacheTtl = Literal['5m', '1h']` : Durée de vie d'une entrée du cache de prompt (fonction B5).
- `ModelApi = Literal['chat', 'responses']` : API d'un modèle `sdk: openai` : `chat` (Chat Completions) ou `responses`.
- `ImageInput = Literal['base64', 'url', 'file_id']` : Forme sous laquelle un modèle accepte une image (point 14).

## Classe `loom_ia.core.model.model_spec.ModelTimeouts`

```python
class ModelTimeouts(DomainModel)
```

Délais d'un appel de modèle (`models[].timeouts`), en secondes ; `None` désactive un délai.

Un délai dépassé est une erreur `transient` : il donne lieu à de
nouvelles tentatives, puis à la bascule vers le secours.

**Attributs :**

- `first_token` : Attente au plus du premier morceau du flux. Défaut : 60.
- `idle` : Attente au plus entre deux morceaux. Défaut : 60.
- `total` : Durée au plus de l'appel complet, flux compris. Défaut : 300.

## Classe `loom_ia.core.model.model_spec.RetryPolicy`

```python
class RetryPolicy(DomainModel)
```

Nouvelles tentatives sur les erreurs `transient` et `overloaded`.

Délai de la tentative n+1 : `initial_delay * multiplier ** (n - 1)`, plafonné à
`max_delay`, puis tiré au hasard entre la moitié et la totalité de cette
valeur. Un `Retry-After` du fournisseur remplace ce calcul ; s'il dépasse
`max_delay`, l'appel est abandonné. Chaque tentative ratée écrit un
`model.retried`. Se déclare sous `models[].retry`.

**Attributs :**

- `max_attempts` : Nombre total de tentatives, la première comprise. Défaut : 3.
- `initial_delay` : Attente avant la deuxième tentative, en secondes. Défaut : 1.
- `multiplier` : Facteur appliqué à chaque tentative suivante, 1 au moins. Défaut : 2.
- `max_delay` : Plafond d'une attente, en secondes. Défaut : 30.

### Méthode `loom_ia.core.model.model_spec.RetryPolicy.backoff`

```python
def backoff(self, attempt: int, jitter: float) -> float
```

Attente après l'échec d'une tentative, avant la suivante.

**Arguments :**

- `attempt` : Numéro de la tentative qui a échoué, à partir de 1.
- `jitter` : Valeur tirée au hasard dans [0, 1].

**Renvoie :**

L'attente, en secondes, entre la moitié et la totalité de
`min(max_delay, initial_delay * multiplier ** (attempt - 1))`.

## Classe `loom_ia.core.model.model_spec.CircuitBreaker`

```python
class CircuitBreaker(DomainModel)
```

Disjoncteur d'un modèle ou d'un serveur MCP (#10, #19, backlog #011).

Après `failures` échecs de suite, la cible est écartée pendant
`cooldown` secondes, pour tous les runs de l'instance : un modèle passe
directement à son secours, un serveur MCP est indisponible. Ensuite, un
essai : réussi, le disjoncteur se referme ; raté, il se rouvre. L'ouverture
écrit un `circuit.opened`. Se déclare sous `models[].circuit_breaker` ou
`mcp_servers[].circuit_breaker` ; `null` le retire.

**Attributs :**

- `failures` : Échecs de suite qui ouvrent le disjoncteur. Défaut : 5.
- `cooldown` : Durée pendant laquelle la cible est écartée, en secondes. Défaut : 60.

## Classe `loom_ia.core.model.model_spec.PromptCache`

```python
class PromptCache(DomainModel)
```

Points de cache de prompt d'un modèle `sdk: anthropic` (`models[].cache`, fonction B5).

`system` : sur le prompt système ; `tools` : sur le dernier outil ;
`messages` : sur le dernier bloc de la conversation, un point qui avance
à chaque appel. L'adaptateur les traduit en `cache_control` (au plus
quatre par requête, blocs marqués `cache_breakpoint` compris). Le prix
d'écriture (`pricing.cache_write`) est celui de la durée choisie.

Les API OpenAI et leurs compatibles cachent seules : `cache` y est refusé.

**Attributs :**

- `system` : Point de cache sur le prompt système. Défaut : faux.
- `tools` : Point de cache sur le dernier outil. Défaut : faux.
- `messages` : Point de cache sur le dernier bloc de la conversation, qui avance à chaque appel. Défaut : faux.
- `ttl` : Durée de vie d'une entrée : `5m` (défaut) ou `1h`.

## Classe `loom_ia.core.model.model_spec.ModelCapabilities`

```python
class ModelCapabilities(DomainModel)
```

Capacités déclarées d'un modèle (`models[].capabilities`), contrôlées au démarrage (fonction B9).

Images (point 14) : un modèle sans `vision` n'en reçoit qu'une mention
textuelle. Pour un modèle avec `vision`, chaque image est lue dans le
stockage d'artefacts et envoyée en base64, après contrôle de son format
(`image_formats`) et de sa taille (`max_image_bytes`).

`tools` : le modèle sait appeler des outils ; exigé d'un orchestrateur
qui en a, d'un juge (verdict par outil imposé) et de leurs secours.
`thinking` : il produit un raisonnement et l'attend en retour pendant
ses tours d'outils ; avec l'API Chat, le raisonnement de la boucle en
cours lui est renvoyé (#7, backlog #015). `native_json` : il accepte un
schéma de sortie JSON ; celui du contrat lui est transmis pour un appel
sans outils (fonction B9). `forced_tool_choice` : il accepte qu'on lui impose
d'appeler un outil (`tool_choice: required`) ; sans elle, une requête qui
l'impose part en `auto` (`engine.fallback.sendable`). Claude Opus 5.5
et Fable 5.1 refusent l'outil imposé (400) : le verdict d'un juge leur est
alors demandé par la consigne seule.

**Attributs :**

- `tools` : Le modèle sait appeler des outils. Défaut : vrai.
- `thinking` : Il produit un raisonnement et l'attend en retour. Défaut : faux.
- `native_json` : Il accepte un schéma de sortie JSON. Défaut : faux.
- `forced_tool_choice` : Il accepte `tool_choice: required`. Défaut : vrai.
- `streaming` : Faux : appel non streamé, dont la réponse est rejouée en un flux simulé. Défaut : vrai.
- `context_window` : Fenêtre en tokens ; vérifiée avant l'appel si elle est déclarée (fonction B7), et utilisée pour choisir un secours en cas de `context_overflow`. Défaut : non déclarée.
- `vision` : Le modèle reçoit les images. Défaut : faux.
- `image_input` : Formes d'image acceptées ; loom n'envoie pour l'instant qu'en `base64`, qui doit y figurer avec `vision`. Défaut : `[base64]`.
- `image_formats` : Formats d'image acceptés. Défaut : jpeg, png, gif, webp.
- `max_image_bytes` : Taille maximale d'une image, en octets. Défaut : pas de limite.
- `tool_result_media` : Images acceptées dans un résultat d'outil ; sinon, elles suivent les résultats dans un message utilisateur. Défaut : faux.

**Lève :**

- `ValueError` : `vision` sans `base64` dans `image_input`.

### Méthode `loom_ia.core.model.model_spec.ModelCapabilities._check_images`

```python
@model_validator(mode='after')
def _check_images(self) -> Self
```

Refuse `vision` sans l'envoi en base64, seul pris en charge.

## Classe `loom_ia.core.model.model_spec.ModelSpec`

```python
class ModelSpec(DomainModel)
```

Un modèle déclaré sous `models` dans `loom.yaml`.

**Attributs :**

- `id` : Identifiant du modèle dans la config (`HAIKU`), par lequel un agent, un rôle, un juge ou une chaîne de secours le désigne. Recopié en `model` dans les événements.
- `sdk` : Adaptateur : `anthropic`, `openai` ou `fake`.
- `api` : Pour `sdk: openai` seulement : `chat` (défaut) ou `responses`.
- `model` : Nom du modèle chez le fournisseur (`claude-haiku-5-5`), recopié en `model_id` dans les événements.
- `base_url` : Point d'accès ; sans lui, l'adresse officielle du fournisseur du SDK (jamais celle d'une variable d'environnement).
- `api_key_env` : Nom de la variable d'environnement qui contient la clé ; un client peut la rediriger vers la sienne (`secrets`).
- `max_tokens` : Plafond de tokens de sortie par appel ; un emplacement peut le surcharger. Une sortie coupée sur cette limite est une erreur `truncated`.
- `params` : Réglages transmis tels quels au fournisseur (température, thinking…). Une surcharge remplace une clé entière, pas son contenu (fonction B6).
- `timeouts` : Délais de l'appel (`ModelTimeouts`).
- `retry` : Nouvelles tentatives (`RetryPolicy`).
- `capabilities` : Capacités déclarées (`ModelCapabilities`).
- `pricing` : Grille tarifaire, en dollars par million de tokens (`Pricing`). Sans tarif, les appels comptent 0 $.
- `circuit_breaker` : Disjoncteur (point 10) ; actif par défaut (5 échecs, 60 s), `null` le retire.
- `cache` : Points de cache de prompt, pour `sdk: anthropic` seulement.

**Lève :**

- `ValueError` : `api` donné avec un autre `sdk` qu'`openai` ; ou `cache` donné avec `sdk: openai`.

### Méthode `loom_ia.core.model.model_spec.ModelSpec._check_api`

```python
@model_validator(mode='after')
def _check_api(self) -> Self
```

Refuse `api` hors de `sdk: openai`, et `cache` avec `sdk: openai`.

### Méthode `loom_ia.core.model.model_spec.ModelSpec.effective_api`

```python
@property
def effective_api(self) -> ModelApi | None
```

API utilisée : `chat` par défaut avec `sdk: openai`.
