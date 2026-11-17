# loom_ia.core.model.streaming

Module `loom_ia.core.model.streaming` de loom_v2 (fichier `src/loom_ia/core/model/streaming.py`).

Requête, réponse et morceaux de flux d'un appel de modèle (point 11, fonction B8).

Le streaming est la seule primitive des adaptateurs : ils émettent des
`ModelChunk` neutres, que `ResponseAccumulator` rassemble en
`ModelResponse`. `message_to_chunks` fait le chemin inverse, pour les
fournisseurs sans streaming et les faux modèles.

Morceaux, par `type` : `text_delta`, `reasoning_delta`,
`tool_call_started`, `tool_args_delta`, `tool_call_ended`, `usage`,
`stopped` (fin propre) et `reset` (émis par le moteur avant de relancer un
appel déjà commencé). Ce sont eux que reçoit `on_chunk` et que diffusent
`stream()` et le SSE.

## Constantes de `loom_ia.core.model.streaming`

- `StopReason = Literal['end', 'tool_use', 'max_tokens', 'refusal']` : Pourquoi le modèle s'est arrêté : fin, appel d'outils, limite de tokens, refus.
- `ToolChoice = Literal['auto', 'none', 'required']` : Choix d'outil imposé au modèle ; `required` impose un appel (backlog #012), jamais en `FINALIZING`.
- `ModelErrorKind = Literal['transient', 'overloaded', 'quota_exhausted', 'context_overflow', 'auth', 'invalid_request', 'content_filtered'…` : Genre d'une erreur d'appel de modèle, classée de façon neutre (point 10) ; voir `ModelError`.
- `INVALID_JSON_KEY = '_loom_invalid_json'` : Clé posée dans les arguments quand le modèle a produit un JSON illisible : l'exécuteur rend alors une erreur que le modèle peut corriger.
- `ModelChunk = Annotated[TextDelta | ReasoningDelta | ToolCallStarted | ToolArgsDelta | ToolCallEnded | UsageDelta | Stopped | StreamR…` : Morceau du flux d'un appel de modèle, discriminé par `type`.

## Classe `loom_ia.core.model.streaming.ModelRequest`

```python
class ModelRequest(DomainModel)
```

Requête neutre à un modèle, que l'adaptateur traduit pour son fournisseur.

C'est ce que voit une politique `before_model` et ce qu'elle peut
remplacer. Son empreinte (`request_hash`) est écrite dans
`model.responded` pour le rejeu.

**Attributs :**

- `model_id` : Nom du modèle chez le fournisseur.
- `system` : Prompt système.
- `messages` : Conversation, dans l'ordre.
- `tools` : Outils exposés au modèle (nom, description, schéma).
- `tool_choice` : `auto` (défaut), `none` ou `required`.
- `max_tokens` : Plafond de tokens de sortie.
- `params` : Réglages transmis tels quels au fournisseur.
- `output_schema` : Schéma JSON de la réponse attendue (contrat de sortie) : l'adaptateur le transmet au fournisseur si le modèle a la capacité `native_json` (fonction B9).

### Méthode `loom_ia.core.model.streaming.ModelRequest.request_hash`

```python
def request_hash(self) -> str
```

Empreinte SHA-256 du JSON canonique de la requête (point 31) : le rejeu détecte ainsi une divergence.

Sans schéma de sortie, le champ est omis : les empreintes des
journaux antérieurs restent les mêmes.

### Méthode `loom_ia.core.model.streaming.ModelRequest.request_parts`

```python
def request_parts(self) -> dict[str, str]
```

Empreinte de chaque partie de la requête : ce qui a changé, au rejeu (phase 6.2a).

`request_hash` dit qu'une requête diffère ; ces empreintes disent
**où** : le modèle, le prompt système, les outils, les messages ou les
réglages (choix d'outil, plafond de tokens, paramètres, schéma de
sortie). Seize caractères suffisent à comparer, pas à retrouver.

**Renvoie :**

`model`, `system`, `tools`, `messages`, `settings` →
empreinte de 16 caractères, et `messages_count` → le nombre de
messages, en clair.

## Fonction `loom_ia.core.model.streaming._short_hash`

```python
def _short_hash(value: JsonValue) -> str
```

Les 16 premiers caractères du SHA-256 du JSON canonique d'une valeur.

## Classe `loom_ia.core.model.streaming.TextDelta`

```python
class TextDelta(DomainModel)
```

Morceau de texte de la réponse.

**Attributs :**

- `type` : `text_delta`.
- `text` : Le texte à ajouter.

## Classe `loom_ia.core.model.streaming.ReasoningDelta`

```python
class ReasoningDelta(DomainModel)
```

Morceau du raisonnement du modèle.

**Attributs :**

- `type` : `reasoning_delta`.
- `text` : Le texte à ajouter.
- `start` : Vrai au premier morceau d'un bloc : deux blocs qui se suivent restent distincts (chacun a sa signature).
- `provider_meta` : Données du fournisseur (signature…), qui peuvent arriver en cours de flux.

## Classe `loom_ia.core.model.streaming.ToolCallStarted`

```python
class ToolCallStarted(DomainModel)
```

Début d'un appel d'outil dans la réponse.

**Attributs :**

- `type` : `tool_call_started`.
- `index` : Rang de l'appel dans la réponse, qui relie les morceaux d'arguments.
- `call_id` : Identifiant de l'appel.
- `name` : Nom de l'outil.

## Classe `loom_ia.core.model.streaming.ToolArgsDelta`

```python
class ToolArgsDelta(DomainModel)
```

Morceau des arguments JSON d'un appel d'outil.

**Attributs :**

- `type` : `tool_args_delta`.
- `index` : Rang de l'appel.
- `json_fragment` : Fragment du JSON des arguments.

## Classe `loom_ia.core.model.streaming.ToolCallEnded`

```python
class ToolCallEnded(DomainModel)
```

Fin d'un appel d'outil dans la réponse.

**Attributs :**

- `type` : `tool_call_ended`.
- `index` : Rang de l'appel.

## Classe `loom_ia.core.model.streaming.UsageDelta`

```python
class UsageDelta(DomainModel)
```

Consommation à ajouter au total (certains fournisseurs la donnent en fin de flux).

**Attributs :**

- `type` : `usage`.
- `usage` : Les tokens à ajouter.

## Classe `loom_ia.core.model.streaming.Stopped`

```python
class Stopped(DomainModel)
```

Fin propre du flux ; sans lui, le flux s'est interrompu et l'appel est une erreur `transient`.

**Attributs :**

- `type` : `stopped`.
- `reason` : Pourquoi le modèle s'est arrêté.
- `model_id` : Modèle effectivement utilisé, s'il diffère de celui demandé.

## Classe `loom_ia.core.model.streaming.StreamReset`

```python
class StreamReset(DomainModel)
```

Émis par le moteur seulement, avant de relancer un appel déjà commencé.

Le texte partiel reçu jusque-là doit être effacé (nouvelle tentative, ou
bascule vers le secours).

**Attributs :**

- `type` : `reset`.
- `attempt` : Tentative qui va commencer.

## Classe `loom_ia.core.model.streaming.ModelResponse`

```python
class ModelResponse(DomainModel)
```

Réponse complète d'un modèle, assemblée depuis le flux.

**Attributs :**

- `model_id` : Nom du modèle qui a répondu.
- `provider` : Fournisseur.
- `message` : Le message `assistant` : texte, raisonnement, appels d'outils.
- `usage` : Tokens consommés.
- `stop_reason` : Pourquoi le modèle s'est arrêté. Défaut : `end`.

## Classe `loom_ia.core.model.streaming._PendingCall`

```python
class _PendingCall
```

Appel d'outil en cours d'assemblage : ses fragments d'arguments.

### Méthode `loom_ia.core.model.streaming._PendingCall.block`

```python
def block(self) -> ToolCallBlock
```

Le bloc d'appel ; un JSON illisible ou qui n'est pas un objet est rangé sous `INVALID_JSON_KEY`.

## Classe `loom_ia.core.model.streaming.ResponseAccumulator`

```python
class ResponseAccumulator
```

Rassemble les morceaux d'un flux en message, dans l'ordre d'arrivée.

Un `StreamReset` efface tout ce qui a été reçu. Les deux appels d'une
même réponse ne peuvent pas porter le même `call_id` : le second est
renommé (`_2`, `_3`…).

**Attributs :**

- `usage` : Tokens cumulés jusqu'ici.
- `stop_reason` : Raison d'arrêt, une fois `Stopped` reçu.
- `model_id` : Modèle annoncé par `Stopped`, s'il y en a un.

### Méthode `loom_ia.core.model.streaming.ResponseAccumulator._reset`

```python
def _reset(self) -> None
```

Remet l'accumulateur à vide.

### Méthode `loom_ia.core.model.streaming.ResponseAccumulator.stopped`

```python
@property
def stopped(self) -> bool
```

Vrai si le flux a livré son `Stopped` ; sans lui, il s'est interrompu en route.

### Méthode `loom_ia.core.model.streaming.ResponseAccumulator.add`

```python
def add(self, chunk: ModelChunk) -> None
```

Ajoute un morceau : texte et raisonnement se concatènent par bloc, les arguments par appel.

### Méthode `loom_ia.core.model.streaming.ResponseAccumulator.result`

```python
def result(self, *, model_id: str, provider: str) -> ModelResponse
```

La réponse assemblée.

**Arguments :**

- `model_id` : Modèle demandé ; remplacé par celui de `Stopped` s'il en annonce un.
- `provider` : Fournisseur.

**Renvoie :**

La réponse ; un texte vide si le flux n'a rien livré. Sans
`Stopped`, la raison est `tool_use` s'il y a des appels,
`end` sinon.

### Méthode `loom_ia.core.model.streaming.ResponseAccumulator._stamped`

```python
@staticmethod
def _stamped(block: ContentBlock, model_id: str) -> ContentBlock
```

Raisonnement marqué du modèle demandé : après une bascule, il sera écarté (point 7).

## Fonction `loom_ia.core.model.streaming._distinct_call_ids`

```python
def _distinct_call_ids(blocks: tuple[ContentBlock, ...]) -> tuple[ContentBlock, ...]
```

Deux appels d'une même réponse ne portent jamais le même `call_id`.

Un fournisseur qui répète un identifiant (vu chez des serveurs compatibles
qui numérotent leurs appels) ferait écrire deux `tool.completed` pour un
seul appel en attente : la projection lèverait `ProjectionError` et la
session deviendrait illisible. Le second reçoit donc un suffixe (`_2`,
`_3`…) qui ne heurte aucun autre identifiant de la réponse. Une réponse
dont les identifiants sont distincts passe telle quelle, octet pour octet.

## Fonction `loom_ia.core.model.streaming.message_to_chunks`

```python
def message_to_chunks(message: Message, *, usage: Usage | None=None, stop_reason: StopReason | None=None, model_id: str | None=None, fragment_size: int=8) -> list[ModelChunk]
```

Morceaux de flux qu'un fournisseur enverrait pour ce message.

Sert aux fournisseurs sans streaming (`capabilities.streaming: false`) et
aux faux modèles.

**Arguments :**

- `message` : Message `assistant` : texte, raisonnement, appels d'outils.
- `usage` : Consommation à annoncer en fin de flux.
- `stop_reason` : Raison d'arrêt ; par défaut `tool_use` s'il y a des appels, `end` sinon.
- `model_id` : Modèle annoncé par `Stopped`.
- `fragment_size` : Taille des morceaux de texte, en caractères. Défaut : 8.

**Renvoie :**

Les morceaux, `Stopped` en dernier.

**Lève :**

- `ValueError` : Le message porte un bloc qu'une réponse de modèle ne peut pas contenir.

## Fonction `loom_ia.core.model.streaming._split`

```python
def _split(text: str, size: int) -> list[str]
```

Découpe en morceaux ; un texte vide donne un morceau vide.
