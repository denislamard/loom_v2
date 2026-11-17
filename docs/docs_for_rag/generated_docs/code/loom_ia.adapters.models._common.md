# loom_ia.adapters.models._common

Module `loom_ia.adapters.models._common` de loom_v2 (fichier `src/loom_ia/adapters/models/_common.py`).

Outils communs aux adaptateurs de modèles : classement des erreurs, conversions.

Ce module n'importe aucun SDK.

Images (point 14) : le moteur remplace chaque référence de fichier avant l'appel,
par les octets de l'image (`inline_data`) ou par une mention. Un adaptateur
ne reçoit donc jamais de `ArtifactRefBlock` ; s'il en reçoit un, c'est une
erreur de montage.

## Constantes de `loom_ia.adapters.models._common`

- `NO_API_KEY = 'not-needed'` : Clé transmise aux serveurs sans authentification (vLLM, Ollama…), faute d'`api_key_env`.
- `KEY_VARIABLES = {'anthropic': 'ANTHROPIC_API_KEY', 'openai': 'OPENAI_API_KEY'}` : Variable proposée quand un modèle de l'API officielle n'a pas d'`api_key_env`.
- `ERROR_PREFIX = '[erreur] '` : Préfixe d'un résultat d'outil en erreur, pour les API sans indicateur dédié.
- `OUTPUT_SCHEMA_NAME = 'output'` : Nom du schéma de sortie transmis aux API OpenAI (fonction B9).
- `_STRICT_KEYWORDS = frozenset({'type', 'properties', 'required', 'additionalProperties', 'items', 'enum', 'const', 'description', 'title', …` : Mots-clés de schéma que le mode strict des API OpenAI accepte.

## Fonction `loom_ia.adapters.models._common.keyless`

```python
def keyless(error: ModelError, spec: ModelSpec, *, sent_key: bool) -> ModelError
```

Refus d'authentification d'un modèle qui n'a envoyé aucune clé : l'erreur le dit.

Sans `api_key_env`, loom envoie `NO_API_KEY`, prévu pour un serveur local.
Au 20/10, un modèle de l'API officielle déclaré ainsi rendait seulement
« 401 invalid x-api-key » : rien ne disait que la clé n'avait pas été lue.

## Fonction `loom_ia.adapters.models._common.custom_header_names`

```python
def custom_header_names(variable: str) -> tuple[str, ...]
```

Noms des en-têtes que `variable` ferait ajouter par un SDK à chaque requête.

Les SDK lisent `ANTHROPIC_CUSTOM_HEADERS` et `OPENAI_CUSTOM_HEADERS` dans
l'environnement **du process** à la construction du client : une ligne
`Nom: valeur` par en-tête, que rien ne distingue de la config de loom, et
qui l'emporte sur la clé d'API du client (`x-api-key`, `Authorization`).
Ce qui est lu ici l'est comme eux ; un adaptateur s'en sert pour les écarter.

## Fonction `loom_ia.adapters.models._common.classify_error`

```python
def classify_error(message: str, *, http_status: int | None, hints: tuple[str | None, ...]=(), retry_after: float | None=None) -> ModelError
```

Traduit une erreur de fournisseur en `ModelError`.

`hints` porte les types et codes d'erreur lus dans la réponse
(`overloaded_error`, `insufficient_quota`…). Sans code HTTP (réseau,
délai, erreur reçue en cours de flux) et sans indice, l'erreur est
considérée comme transitoire.

## Fonction `loom_ia.adapters.models._common.retry_after`

```python
def retry_after(headers: Mapping[str, str] | None, *, now: float | None=None) -> float | None
```

Délai demandé par `retry-after-ms` ou `retry-after` (secondes ou date HTTP).

## Fonction `loom_ia.adapters.models._common.output_text`

```python
def output_text(output: ToolOutput) -> str
```

Contenu d'un résultat d'outil, en texte (JSON sérialisé pour les blocs `json`).

Lève `ModelError` si le résultat contient une image : l'API ne l'accepte
pas dans un résultat d'outil.

## Fonction `loom_ia.adapters.models._common.json_text`

```python
def json_text(data: JsonValue) -> str
```

Une valeur JSON en texte, sans échapper les caractères non ASCII.

## Fonction `loom_ia.adapters.models._common.unreadable_json`

```python
def unreadable_json(error: json.JSONDecodeError) -> ModelError
```

Réponse du fournisseur dont le JSON ne se lit pas : erreur transitoire.

Un événement de flux complet ou un corps de réponse au JSON invalide — proxy
qui réécrit, corps abîmé en route — ne dit rien de la requête : rien ne dit
qu'elle échouera encore. Les SDK laissent passer l'erreur de `json` telle
quelle ; sans cette traduction, elle échappait au retry, au secours et au
disjoncteur. Le message ne recopie pas ce qui a été reçu.

## Fonction `loom_ia.adapters.models._common.unresolved`

```python
def unresolved(block: ArtifactRefBlock) -> ModelError
```

L'erreur d'une référence de fichier que le moteur n'a pas remplacée avant l'appel (`invalid_request`).

## Fonction `loom_ia.adapters.models._common.image_data`

```python
def image_data(block: InlineDataBlock) -> str
```

Octets d'une image, en base64 ; lève `ModelError` pour un autre type de fichier.

## Fonction `loom_ia.adapters.models._common.is_strict`

```python
def is_strict(schema: Mapping[str, JsonValue]) -> bool
```

Vrai si le schéma suit les règles du mode strict des API OpenAI.

Objets fermés (`additionalProperties: false`), toutes leurs propriétés
requises, et seulement des mots-clés que ce mode accepte. Sinon, le schéma
est transmis sans `strict` : le fournisseur le suit au mieux, et le
contrat de sortie le vérifie de toute façon.

## Fonction `loom_ia.adapters.models._common.reasoning_loop`

```python
def reasoning_loop(messages: Sequence[Message]) -> set[int]
```

Positions des réponses du modèle dans la boucle d'outils en cours (#7, backlog #015).

Ce sont celles qui suivent sa dernière réponse sans appel d'outil : leur
raisonnement lui est renvoyé ; celui des tours déjà conclus est écarté,
comme le recommande OpenAI pour gpt-oss.
