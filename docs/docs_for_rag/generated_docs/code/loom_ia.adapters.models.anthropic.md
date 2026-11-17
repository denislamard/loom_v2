# loom_ia.adapters.models.anthropic

Module `loom_ia.adapters.models.anthropic` de loom_v2 (fichier `src/loom_ia/adapters/models/anthropic.py`).

Adaptateur `sdk: anthropic` : API Messages, en streaming (fonctions B1 et B8).

Sert aussi les fournisseurs compatibles (MiniMax…) via `base_url` ; sans
lui, l'adresse officielle d'Anthropic, quelle que soit `ANTHROPIC_BASE_URL`.

Traduction des messages :

- le rôle `tool` devient `user` avec des blocs `tool_result` ;
- les messages consécutifs de même rôle sont fusionnés ;
- les blocs de texte vides sont omis (l'API les refuse) ;
- le raisonnement n'est renvoyé que s'il porte des données Anthropic
  (signature ou contenu masqué) ; les autres blocs de raisonnement sont omis ;
- une image (`inline_data`) devient un bloc `image` en base64, dans un
  message comme dans un résultat d'outil ;
- `tool_choice: required` devient `{"type": "any"}` ; un modèle qui le
  refuse est déclaré `capabilities.forced_tool_choice: false` (sa requête
  arrive alors en `auto`), et l'erreur 400 du fournisseur le rappelle ;
- cache de prompt (fonction B5) : les points déclarés par `cache` (prompt système,
  dernier outil, dernier bloc de la conversation) et les blocs marqués
  `cache_breakpoint` reçoivent un `cache_control`, au plus quatre par
  requête (les marques les plus récentes d'abord) ; jamais sur un bloc de
  raisonnement. Le state de la session, que le moteur ajoute en fin de requête
  (décision D4 du state de session), n'est pas de la conversation : son point se pose sur le bloc qui le
  précède, et un state qui change ne fait pas perdre le cache de l'historique ;
- le schéma de sortie (`output_schema`) devient `output_config.format`
  pour un modèle déclaré `native_json: true`, après adaptation par le SDK
  (`transform_schema` : mots-clés non pris en charge reportés dans les
  descriptions ; le contrat de sortie vérifie le schéma d'origine) (fonction B9) ;
- fin du flux : `message_stop` la dit. Sans lui, le flux a été coupé : l'appel
  échoue en `transient`, au lieu de rendre un début de réponse sans consommation ;
- coupure, délai ou erreur de protocole du client HTTP (`httpx2`) pendant la lecture du flux :
  le SDK ne les enrobe pas ; elles deviennent des `ModelError` `transient` (retry, secours,
  disjoncteur). De même un événement de flux, ou une réponse non streamée, au JSON
  invalide. L'annulation (`CancelledError`) n'est jamais convertie.

Les retries du SDK sont désactivés : la politique de loom-ia s'applique.

## Constantes de `loom_ia.adapters.models.anthropic`

- `PROVIDER = 'anthropic'` : Nom du fournisseur, tel que le journal et la télémétrie l'écrivent.
- `DEFAULT_BASE_URL = 'https://api.anthropic.com'` : Adresse sans `base_url` : passée au SDK pour qu'il ne lise pas ANTHROPIC_BASE_URL.
- `CUSTOM_HEADERS_ENV = 'ANTHROPIC_CUSTOM_HEADERS'` : Variable que le SDK lit pour ajouter des en-têtes à chaque requête.
- `DEFAULT_MAX_TOKENS = 4096` : L'API exige max_tokens.
- `MAX_CACHE_POINTS = 4` : Points de cache permis par requête.
- `_UNCACHEABLE = frozenset({'thinking', 'redacted_thinking'})` : Blocs qui ne peuvent pas porter de point de cache.
- `_Mark = tuple[int, int]` : Bloc d'un message marqué `cache_breakpoint` : (tour, position dans le tour).

## Fonction `loom_ia.adapters.models.anthropic._own_headers`

```python
def _own_headers(api_key: str) -> dict[str, str]
```

En-têtes par défaut qui rendent le SDK sourd à `ANTHROPIC_CUSTOM_HEADERS`.

Comme `base_url`, ce que le client envoie vient de la config seule : un
en-tête de l'environnement du process passerait sinon pour tous les clients,
et `x-api-key` y remplacerait la clé du client. La clé est donc reposée
telle quelle, et tout autre en-tête de la variable est omis.

## Classe `loom_ia.adapters.models.anthropic.AnthropicModel`

```python
class AnthropicModel
```

Client `ModelClient` pour l'API Messages.

### Méthode `loom_ia.adapters.models.anthropic.AnthropicModel.__init__`

```python
def __init__(self, spec: ModelSpec, *, api_key: str, http_client: httpx2.AsyncClient | None=None) -> None
```

Prépare le client du SDK, sans retries et sans rien lire de l'environnement.

L'adresse vient de `base_url`, à défaut celle de l'API officielle : le SDK
ne lit pas `ANTHROPIC_BASE_URL`. Les en-têtes que l'environnement ajouterait sont
omis (`ANTHROPIC_CUSTOM_HEADERS`), le délai total est `timeouts.total`. Sans
`api_key_env`, aucune clé n'est envoyée : un 401 le dira.

**Arguments :**

- `spec` : Le modèle déclaré.
- `api_key` : Clé de l'API (`NO_API_KEY` : aucune).
- `http_client` : Client HTTP à utiliser, par exemple celui qui garde les échanges bruts (`RecordingClient`) ; à défaut, celui du SDK.

### Méthode `loom_ia.adapters.models.anthropic.AnthropicModel.provider`

```python
@property
def provider(self) -> str
```

Nom du fournisseur : `anthropic`.

### Méthode `loom_ia.adapters.models.anthropic.AnthropicModel.aclose`

```python
async def aclose(self) -> None
```

Ferme le client du SDK.

### Méthode `loom_ia.adapters.models.anthropic.AnthropicModel.stream`

```python
async def stream(self, request: ModelRequest) -> AsyncGenerator[ModelChunk]
```

Envoie la requête à l'API Messages et rend la réponse en morceaux neutres.

Pose les points de cache, le choix d'outil, `max_tokens` (celui de la
requête, puis du modèle, puis 4 096) et le schéma de sortie
(`output_config`) ; les `params` du modèle partent dans le corps. Sans `capabilities.streaming`, la réponse entière est lue puis
rendue en flux simulé.

**Arguments :**

- `request` : La requête neutre.

**Produit :**

Les morceaux de la réponse.

**Lève :**

- `ModelError` : Erreur de l'API ou du transport, classée ; flux coupé avant sa fin (`transient`) ; JSON illisible (`transient`).

### Méthode `loom_ia.adapters.models.anthropic.AnthropicModel._output_config`

```python
def _output_config(self, request: ModelRequest, params: dict[str, JsonValue]) -> sdk.OutputConfigParam | anthropic.Omit
```

`output_config` : schéma de sortie (`native_json`), ajouté à celui des `params`.

## Fonction `loom_ia.adapters.models.anthropic.to_anthropic_messages`

```python
def to_anthropic_messages(messages: Sequence[Message], *, marks: list[_Mark] | None=None, tail: list[_Mark] | None=None) -> list[sdk.MessageParam]
```

Messages neutres → messages de l'API, fusionnés par rôle.

`marks` reçoit la place des blocs marqués `cache_breakpoint` ; `tail`,
celle du state de la session (décision D4 du state de session) en fin de requête, où finit la conversation.

## Fonction `loom_ia.adapters.models.anthropic._trailing_state`

```python
def _trailing_state(messages: Sequence[Message]) -> int | None
```

Place du state que le moteur ajoute en fin de requête (décision D4 du state de session), ou None.

C'est le dernier message, ou l'avant-dernier devant la consigne d'une
réponse forcée, et jamais le premier : la demande le précède toujours.
Une demande plus ancienne qui commencerait par la balise, ou le message
d'un rôle qui commence par sa section `<session_state>`, n'est pas lui :
ils ne déplacent pas le point de cache.

## Fonction `loom_ia.adapters.models.anthropic.place_cache_points`

```python
def place_cache_points(cache: PromptCache | None, system: str | list[sdk.TextBlockParam] | anthropic.Omit, tools: list[sdk.ToolParam], messages: list[sdk.MessageParam], marks: Sequence[_Mark]=(), *, tail: _Mark | None=None) -> None
```

Pose les `cache_control` : réglage `cache` du modèle, puis blocs marqués (fonction B5).

Au plus quatre : ceux du réglage d'abord (outils, système, conversation),
puis les marques, des plus récentes aux plus anciennes. `tail` : place du
state de la session (décision D4 du state de session) ; le point de la conversation se pose avant lui.

## Fonction `loom_ia.adapters.models.anthropic._last_cacheable`

```python
def _last_cacheable(messages: list[sdk.MessageParam], tail: _Mark | None=None) -> dict[str, Any] | None
```

Dernier bloc de la conversation qui peut porter un point de cache.

Sans `tail`, dans le dernier message. Avec, avant ce bloc — le state de
la session (décision D4 du state de session) —, en remontant les messages s'il le faut : un point posé
sur le state, qui change, ne servirait à aucun appel suivant.

## Fonction `loom_ia.adapters.models.anthropic._hinted`

```python
def _hinted(error: ModelError, request: ModelRequest) -> ModelError
```

Erreur d'un outil imposé refusé : elle dit quelle capacité déclarer.

Claude Opus 5.5 et Fable 5.1 refusent `tool_choice` `any` et `tool`
(400, « tool_choice: type "tool" and "any" are not supported for this
model. ») ; le message du fournisseur ne dit pas quoi changer dans la config.

## Fonction `loom_ia.adapters.models.anthropic._tool_choice`

```python
def _tool_choice(request: ModelRequest) -> sdk.ToolChoiceParam
```

`required` devient `any` : le modèle doit appeler au moins un outil (backlog #012).

## Fonction `loom_ia.adapters.models.anthropic.to_anthropic_tools`

```python
def to_anthropic_tools(request: ModelRequest) -> list[sdk.ToolParam]
```

Les outils proposés, au format de l'API : nom, description et schéma d'entrée.

**Arguments :**

- `request` : La requête neutre.

## Fonction `loom_ia.adapters.models.anthropic._to_block`

```python
def _to_block(block: ContentBlock) -> sdk.ContentBlockParam | None
```

Un bloc neutre au format de l'API ; `None` pour un bloc qui ne part pas.

Un texte vide ne part pas, ni un raisonnement venu d'un autre fournisseur
ou sans signature ; un JSON part en texte, une image en base64. Une
référence de fichier non remplacée par le moteur lève `ModelError`.

## Fonction `loom_ia.adapters.models.anthropic._result_content`

```python
def _result_content(output: ToolOutput) -> list[sdk.tool_result_block_param.Content]
```

Contenu d'un résultat : ses textes réunis, et ses images à leur place.

## Fonction `loom_ia.adapters.models.anthropic._beyond`

```python
def _beyond(total: Usage, sent: Usage) -> Usage
```

Ce que `total` ajoute à ce qui a déjà été transmis (compteurs cumulés).

## Classe `loom_ia.adapters.models.anthropic.StreamParser`

```python
class StreamParser
```

Événements bruts du flux → morceaux neutres.

### Méthode `loom_ia.adapters.models.anthropic.StreamParser.feed`

```python
def feed(self, event: sdk.RawMessageStreamEvent) -> list[ModelChunk]
```

Traduit un événement du flux en morceaux neutres.

L'entrée est facturée dès `message_start` et part tout de suite en
`UsageDelta` ; le reste de la consommation et la raison d'arrêt suivent
au `message_stop`, qui marque le flux complet (`stopped`).

**Arguments :**

- `event` : Événement brut du SDK.

### Méthode `loom_ia.adapters.models.anthropic.StreamParser.record_usage`

```python
def record_usage(self, usage: sdk.Usage | sdk.MessageDeltaUsage) -> None
```

Retient les compteurs ; ceux d'un `message_delta` sont cumulés.

### Méthode `loom_ia.adapters.models.anthropic.StreamParser.usage`

```python
def usage(self) -> Usage
```

La consommation retenue jusqu'ici.

### Méthode `loom_ia.adapters.models.anthropic.StreamParser._block_start`

```python
def _block_start(self, index: int, block: sdk.ContentBlock) -> list[ModelChunk]
```

Le début d'un bloc : texte, raisonnement (signé ou masqué), ou appel d'outil, numéroté dans l'ordre des appels ; un autre type de bloc est ignoré.

## Fonction `loom_ia.adapters.models.anthropic.response_to_chunks`

```python
def response_to_chunks(message: sdk.Message) -> list[ModelChunk]
```

Réponse non streamée → flux simulé.

## Fonction `loom_ia.adapters.models.anthropic.to_model_error`

```python
def to_model_error(exc: anthropic.APIError | httpx2.HTTPError) -> ModelError
```

Une erreur du SDK ou du transport, classée en `ModelError`.

Une coupure, un délai ou une erreur de protocole pendant la lecture du flux
est transitoire. Une erreur HTTP est classée par son statut (sauf le 200
d'un flux ouvert), son type et son `Retry-After`.

**Arguments :**

- `exc` : L'erreur levée.
