# loom_ia.adapters.models.openai_responses

Module `loom_ia.adapters.models.openai_responses` de loom_v2 (fichier `src/loom_ia/adapters/models/openai_responses.py`).

Adaptateur `sdk: openai`, `api: responses` : API Responses d'OpenAI (points 7 et 9, fonction B1).

Sans état chez le fournisseur : `store: false`, toute la conversation part
à chaque appel, et le raisonnement revient chiffré
(`include: reasoning.encrypted_content`) pour être renvoyé au tour suivant.

- le prompt système devient `instructions` ;
- un message utilisateur devient un message `user` (`input_text` et, pour
  une image, `input_image` en URL `data:`) ;
- une réponse du modèle redevient, dans l'ordre de ses blocs : ses éléments
  de raisonnement, son texte (message `assistant`), ses appels d'outils
  (éléments `function_call`) ;
- un résultat d'outil devient un `function_call_output` : du texte, ou du
  texte et des images ; l'API n'ayant pas d'indicateur d'erreur, le contenu
  d'un résultat en erreur est préfixé ;
- raisonnement renvoyé : chiffré, toujours, sans son identifiant (rien n'est
  gardé chez le fournisseur, un identifiant y serait cherché en vain) ; en
  clair (gpt-oss servi par l'API Responses), seulement à un modèle déclaré
  `thinking: true` et pour la boucle d'outils en cours ;
- la limite de sortie passe par `max_output_tokens` ; `tool_choice` tel
  quel ; un outil est `strict` si son schéma en suit les règles ;
- le schéma de sortie (`output_schema`) devient `text.format`
  (`json_schema`) pour un modèle déclaré `native_json: true` (fonction B9) ;
- fin du flux : `response.completed` ou `response.incomplete` la dit. Sans
  l'un ni l'autre, le flux a été coupé : l'appel échoue en `transient`. Un
  événement de flux, ou une réponse non streamée, au JSON invalide aussi.

Les retries du SDK sont désactivés : la politique de loom-ia s'applique.
Adaptateur testé sur des réponses HTTP enregistrées.

## Constantes de `loom_ia.adapters.models.openai_responses`

- `PROVIDER = 'openai'` : Nom du fournisseur, tel que le journal et la télémétrie l'écrivent.
- `INCLUDE = ['reasoning.encrypted_content']` : Le raisonnement revient chiffré, pour être renvoyé sans rien garder chez le fournisseur. Envoyé sans condition, même pour un modèle déclaré `thinking: false` (MOD-1, décidé) : un modèle à raisonnement non déclaré en a besoin entre deux tours d'outils. Non vérifié sur l'API réelle : si un modèle sans raisonnement refuse ce paramètre, le conditionner à `thinking` en adaptant test_request_is_stateless_and_sends_encrypted_reasoning_back.

## Classe `loom_ia.adapters.models.openai_responses.OpenAIResponsesModel`

```python
class OpenAIResponsesModel
```

Client `ModelClient` pour l'API Responses.

### Méthode `loom_ia.adapters.models.openai_responses.OpenAIResponsesModel.__init__`

```python
def __init__(self, spec: ModelSpec, *, api_key: str, http_client: httpx2.AsyncClient | None=None) -> None
```

Prépare le client du SDK, sans retries et sans rien lire de l'environnement.

L'adresse vient de `base_url`, à défaut celle de l'API officielle : le SDK
ne lit pas `OPENAI_BASE_URL`. Les en-têtes que l'environnement ajouterait sont
omis (`OPENAI_ORG_ID, OPENAI_PROJECT_ID, OPENAI_CUSTOM_HEADERS`), le délai total est `timeouts.total`. Sans
`api_key_env`, aucune clé n'est envoyée : un 401 le dira.

**Arguments :**

- `spec` : Le modèle déclaré.
- `api_key` : Clé de l'API (`NO_API_KEY` : aucune).
- `http_client` : Client HTTP à utiliser, par exemple celui qui garde les échanges bruts (`RecordingClient`) ; à défaut, celui du SDK.

### Méthode `loom_ia.adapters.models.openai_responses.OpenAIResponsesModel.provider`

```python
@property
def provider(self) -> str
```

Nom du fournisseur : `openai`.

### Méthode `loom_ia.adapters.models.openai_responses.OpenAIResponsesModel.aclose`

```python
async def aclose(self) -> None
```

Ferme le client du SDK.

### Méthode `loom_ia.adapters.models.openai_responses.OpenAIResponsesModel.stream`

```python
async def stream(self, request: ModelRequest) -> AsyncGenerator[ModelChunk]
```

Envoie la requête à l'API Responses et rend la réponse en morceaux neutres.

Rien n'est gardé chez le fournisseur (`store: false`) : le raisonnement
revient chiffré, pour être renvoyé. Le prompt système part en
`instructions` ; `max_output_tokens` est celui de la requête, puis du
modèle ; le schéma de sortie passe en `text.format` (`native_json`) ;
les `params` du modèle partent dans le corps. Sans `capabilities.streaming`, la réponse entière est lue puis
rendue en flux simulé.

**Arguments :**

- `request` : La requête neutre.

**Produit :**

Les morceaux de la réponse.

**Lève :**

- `ModelError` : Erreur de l'API ou du transport, classée ; flux coupé avant sa fin (`transient`) ; JSON illisible (`transient`).

## Fonction `loom_ia.adapters.models.openai_responses.to_input_items`

```python
def to_input_items(messages: Sequence[Message], *, thinking: bool=False) -> list[ResponseInputItemParam]
```

Messages neutres → éléments d'entrée de l'API, dans l'ordre.

## Fonction `loom_ia.adapters.models.openai_responses.to_response_tools`

```python
def to_response_tools(request: ModelRequest) -> list[FunctionToolParam]
```

Les outils proposés, au format de l'API ; `strict` seulement si le schéma d'entrée en suit les règles.

**Arguments :**

- `request` : La requête neutre.

## Fonction `loom_ia.adapters.models.openai_responses.to_text_config`

```python
def to_text_config(schema: dict[str, JsonValue]) -> ResponseTextConfigParam
```

Schéma de sortie du contrat → `text.format` (fonction B9).

## Fonction `loom_ia.adapters.models.openai_responses._user_content`

```python
def _user_content(message: Message) -> str | list[_Item]
```

Texte seul, ou parties texte et image quand le message porte des images.

## Fonction `loom_ia.adapters.models.openai_responses._assistant_items`

```python
def _assistant_items(message: Message, *, clear: bool) -> list[_Item]
```

Une réponse du modèle, élément par élément, dans l'ordre de ses blocs.

`clear` : son raisonnement en clair est renvoyé (boucle en cours, `thinking`).

## Fonction `loom_ia.adapters.models.openai_responses._reasoning_item`

```python
def _reasoning_item(block: ReasoningBlock, *, clear: bool) -> _Item | None
```

Élément `reasoning` à renvoyer, ou None.

## Fonction `loom_ia.adapters.models.openai_responses._tool_output`

```python
def _tool_output(output: ToolOutput) -> str | list[_Item]
```

Résultat d'un outil : son texte, ou ses parties quand il porte des images.

## Classe `loom_ia.adapters.models.openai_responses.StreamParser`

```python
class StreamParser
```

Événements de l'API Responses → morceaux neutres.

### Méthode `loom_ia.adapters.models.openai_responses.StreamParser.feed`

```python
def feed(self, event: ResponseStreamEvent) -> list[ModelChunk]
```

Traduit un événement du flux en morceaux neutres.

`response.completed` ou `response.incomplete` marque la fin du flux ;
`response.failed` et une erreur de flux lèvent `ModelError`.

**Arguments :**

- `event` : Événement brut du SDK.

### Méthode `loom_ia.adapters.models.openai_responses.StreamParser.finish`

```python
def finish(self) -> list[ModelChunk]
```

Morceaux de fin ; `ModelError` si le flux s'est arrêté avant l'événement final.

### Méthode `loom_ia.adapters.models.openai_responses.StreamParser.call_started`

```python
def call_started(self, call: ResponseFunctionToolCall, index: int) -> ToolCallStarted
```

Le début d'un appel d'outil, numéroté par sa place dans la sortie.

**Arguments :**

- `call` : L'appel annoncé.
- `index` : Sa place dans la sortie.

### Méthode `loom_ia.adapters.models.openai_responses.StreamParser.item_done`

```python
def item_done(self, item: ResponseOutputItem, index: int) -> list[ModelChunk]
```

Élément terminé : ce que les deltas n'ont pas apporté, et sa fin.

### Méthode `loom_ia.adapters.models.openai_responses.StreamParser.finished`

```python
def finished(self, response: Response) -> None
```

Fin de la réponse : modèle, consommation, motif d'une réponse incomplète.

## Fonction `loom_ia.adapters.models.openai_responses.response_to_chunks`

```python
def response_to_chunks(response: Response) -> list[ModelChunk]
```

Réponse non streamée → flux simulé, élément par élément.

Une réponse `failed` ou `cancelled` est une erreur du fournisseur, comme
l'événement `response.failed` du flux : ce n'est pas une réponse vide.

## Fonction `loom_ia.adapters.models.openai_responses.to_usage`

```python
def to_usage(usage: ResponseUsage) -> Usage
```

Les tokens en cache sont compris dans `input_tokens` : ils en sont retirés.
