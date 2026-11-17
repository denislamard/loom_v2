# loom_ia.adapters.models.openai_chat

Module `loom_ia.adapters.models.openai_chat` de loom_v2 (fichier `src/loom_ia/adapters/models/openai_chat.py`).

Adaptateur `sdk: openai`, `api: chat` : API Chat Completions (fonctions B1 et B8).

Vise les fournisseurs compatibles (Together, vLLM, Ollama…) ; sans
`base_url`, l'adresse officielle d'OpenAI, quelle que soit
`OPENAI_BASE_URL`.

- le prompt système devient un message `system` ;
- chaque résultat d'outil devient un message `tool` ; l'API n'ayant pas
  d'indicateur d'erreur, le contenu d'un résultat en erreur est préfixé ;
- une image (`inline_data`) d'un message utilisateur devient une partie
  `image_url` en URL `data:` ; l'API n'accepte pas d'image dans un
  résultat d'outil (`tool_result_media: false`) ;
- le raisonnement est lu dans les champs `reasoning_content` ou
  `reasoning` que ces fournisseurs ajoutent ; il n'est renvoyé qu'à un
  modèle déclaré `thinking: true`, pour la boucle d'outils en cours, dans le
  champ où il est arrivé (point 7, backlog #015 : gpt-oss l'attend) — certains
  fournisseurs refusent ce champ ;
- la limite de sortie passe par `max_tokens`, qu'ils reconnaissent tous ;
- `tool_choice` passe tel quel (`auto`, `none`, `required`) ;
- un identifiant d'appel d'outil manquant est généré ;
- le schéma de sortie (`output_schema`) devient `response_format`
  (`json_schema`) pour un modèle déclaré `native_json: true` ; `strict`
  seulement si le schéma en suit les règles (fonction B9) ;
- fin du flux : un flux est complet quand un choix porte son `finish_reason`,
  ou, à défaut (certains serveurs compatibles l'omettent), quand la ligne
  `data: [DONE]` est reçue. Ni l'un ni l'autre : le flux a été coupé, et
  l'appel échoue en `transient` plutôt que de rendre un début de réponse.
  Un événement de flux, ou une réponse non streamée, au JSON invalide échoue
  aussi en `transient` : le SDK laisse passer l'erreur de `json` telle quelle.

Les retries du SDK sont désactivés : la politique de loom-ia s'applique.

## Constantes de `loom_ia.adapters.models.openai_chat`

- `PROVIDER = 'openai'` : Nom du fournisseur, tel que le journal et la télémétrie l'écrivent.
- `DEFAULT_BASE_URL = 'https://api.openai.com/v1'` : Adresse sans `base_url` : passée au SDK pour qu'il ne lise pas OPENAI_BASE_URL.
- `CUSTOM_HEADERS_ENV = 'OPENAI_CUSTOM_HEADERS'` : Variable que le SDK lit pour ajouter des en-têtes à chaque requête.
- `REASONING_FIELDS = ('reasoning_content', 'reasoning')` : Champs de raisonnement ajoutés par les fournisseurs compatibles.
- `DEFAULT_REASONING_FIELD = 'reasoning'` : Champ de renvoi d'un raisonnement dont l'origine est inconnue (convention d'OpenAI pour gpt-oss).
- `_DONE = re.compile(b'\\ndata: ?\\[DONE\\]')` : Ligne qui clôt un flux SSE de ce type d'API. Un saut de ligne la précède toujours : le JSON d'un morceau n'en porte pas de brut (il est échappé).
- `_DONE_TAIL = 16` : Octets gardés d'une lecture à l'autre, de quoi reconnaître cette ligne coupée en deux.

## Fonction `loom_ia.adapters.models.openai_chat.own_headers`

```python
def own_headers(api_key: str) -> Mapping[str, str]
```

En-têtes par défaut qui rendent le SDK sourd à l'environnement du process.

Comme `base_url`, ce que le client envoie vient de la config seule : le SDK
lirait sinon `OPENAI_ORG_ID` et `OPENAI_PROJECT_ID` (l'organisation et le
projet facturés seraient ceux du process pour tous les clients) et
`OPENAI_CUSTOM_HEADERS`, dont `Authorization` remplacerait la clé du
client. L'organisation et le projet sont omis, la clé est reposée telle
quelle, et tout autre en-tête de la variable est omis.

## Classe `loom_ia.adapters.models.openai_chat._Sentinel`

```python
class _Sentinel
```

Note si le flux a reçu `data: [DONE]`, que le SDK consomme sans le dire.

Le SDK s'arrête sur cette ligne comme à la fin du flux : rien, dans les
événements qu'il rend, ne dit laquelle des deux est arrivée. On la lit donc
au passage des octets, en enrobant `Response.aiter_bytes` — ce qu'il lit,
déjà décompressé.

## Classe `loom_ia.adapters.models.openai_chat.OpenAIChatModel`

```python
class OpenAIChatModel
```

Client `ModelClient` pour l'API Chat Completions.

### Méthode `loom_ia.adapters.models.openai_chat.OpenAIChatModel.__init__`

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

### Méthode `loom_ia.adapters.models.openai_chat.OpenAIChatModel.provider`

```python
@property
def provider(self) -> str
```

Nom du fournisseur : `openai`.

### Méthode `loom_ia.adapters.models.openai_chat.OpenAIChatModel.aclose`

```python
async def aclose(self) -> None
```

Ferme le client du SDK.

### Méthode `loom_ia.adapters.models.openai_chat.OpenAIChatModel.stream`

```python
async def stream(self, request: ModelRequest) -> AsyncGenerator[ModelChunk]
```

Envoie la requête à l'API Chat Completions et rend la réponse en morceaux neutres.

Le prompt système devient un message `system` ; `max_tokens` est celui
de la requête, puis du modèle ; le schéma de sortie passe en
`response_format` (`native_json`) ; les `params` du modèle partent
dans le corps. Un flux est complet s'il porte un `finish_reason` ou la
ligne `data: [DONE]`. Sans `capabilities.streaming`, la réponse entière est lue puis
rendue en flux simulé.

**Arguments :**

- `request` : La requête neutre.

**Produit :**

Les morceaux de la réponse.

**Lève :**

- `ModelError` : Erreur de l'API ou du transport, classée ; flux coupé avant sa fin (`transient`) ; JSON illisible (`transient`).

## Fonction `loom_ia.adapters.models.openai_chat.to_openai_messages`

```python
def to_openai_messages(system: str, messages: Sequence[Message], *, reasoning: bool=False) -> list[ChatCompletionMessageParam]
```

Messages neutres → messages de l'API.

`reasoning` : le raisonnement des réponses de la boucle d'outils en
cours est renvoyé (modèle `thinking: true`).

## Fonction `loom_ia.adapters.models.openai_chat.to_response_format`

```python
def to_response_format(schema: dict[str, JsonValue]) -> ResponseFormat
```

Schéma de sortie du contrat → `response_format` (fonction B9).

## Fonction `loom_ia.adapters.models.openai_chat.to_openai_tools`

```python
def to_openai_tools(request: ModelRequest) -> list[ChatCompletionFunctionToolParam]
```

Les outils proposés, au format de l'API : des fonctions avec nom, description et schéma des paramètres.

**Arguments :**

- `request` : La requête neutre.

## Fonction `loom_ia.adapters.models.openai_chat._user_content`

```python
def _user_content(message: Message) -> str | list[ChatCompletionContentPartParam]
```

Texte seul, ou parties texte et image quand le message porte des images.

## Fonction `loom_ia.adapters.models.openai_chat._assistant`

```python
def _assistant(message: Message, *, reasoning: bool=False) -> ChatCompletionAssistantMessageParam
```

Une réponse du modèle, au format de l'API : texte, appels d'outils, et son raisonnement s'il doit lui être renvoyé.

**Arguments :**

- `message` : La réponse.
- `reasoning` : Renvoyer son raisonnement, dans le champ où il est arrivé (boucle d'outils en cours, modèle `thinking: true`).

## Fonction `loom_ia.adapters.models.openai_chat._reasoning_echo`

```python
def _reasoning_echo(message: Message) -> tuple[str, str] | None
```

Champ et texte du raisonnement d'une réponse, pour le lui renvoyer.

## Classe `loom_ia.adapters.models.openai_chat.StreamParser`

```python
class StreamParser
```

Morceaux `ChatCompletionChunk` → morceaux neutres.

### Méthode `loom_ia.adapters.models.openai_chat.StreamParser.feed`

```python
def feed(self, event: ChatCompletionChunk) -> list[ModelChunk]
```

Traduit un morceau du flux en morceaux neutres.

Seul le premier choix compte. Le raisonnement est lu dans
`reasoning_content` ou `reasoning`, et le champ d'origine est gardé ;
un refus passe en texte ; un appel d'outil sans identifiant en reçoit un.

**Arguments :**

- `event` : Morceau `ChatCompletionChunk` du SDK.

### Méthode `loom_ia.adapters.models.openai_chat.StreamParser.feed_completion`

```python
def feed_completion(self, completion: ChatCompletion) -> list[ModelChunk]
```

Réponse complète, lue comme un flux d'un seul tenant.

### Méthode `loom_ia.adapters.models.openai_chat.StreamParser.check_end`

```python
def check_end(self, *, done: bool) -> None
```

Lève `ModelError("transient")` si le flux n'est pas allé à sa fin.

Un `finish_reason` le dit. À défaut (certains serveurs compatibles
l'omettent), la ligne `[DONE]` (`done`) le dit aussi, pourvu que le
flux ait apporté quelque chose. Sinon il a été coupé, ou il est vide.

### Méthode `loom_ia.adapters.models.openai_chat.StreamParser.finish`

```python
def finish(self) -> list[ModelChunk]
```

Morceaux de fin : l'API ne signale ni la fin des appels ni l'arrêt séparément.

## Fonction `loom_ia.adapters.models.openai_chat.completion_to_chunks`

```python
def completion_to_chunks(completion: ChatCompletion) -> list[ModelChunk]
```

Réponse non streamée → flux simulé.

## Fonction `loom_ia.adapters.models.openai_chat.to_usage`

```python
def to_usage(usage: CompletionUsage) -> Usage
```

Les tokens en cache sont compris dans `prompt_tokens` : ils en sont retirés.

## Fonction `loom_ia.adapters.models.openai_chat._reasoning`

```python
def _reasoning(part: BaseModel) -> tuple[str, str]
```

Champ et texte du raisonnement d'un morceau ou d'une réponse (texte vide sans lui).

## Fonction `loom_ia.adapters.models.openai_chat.to_model_error`

```python
def to_model_error(exc: openai.APIError) -> ModelError
```

Une erreur du SDK, classée en `ModelError` : par son statut HTTP, son code, son type et son `Retry-After`.

**Arguments :**

- `exc` : L'erreur levée.
