# loom_ia.tools_builtin.memory.protocol

Module `loom_ia.tools_builtin.memory.protocol` de loom_v2 (fichier `src/loom_ia/tools_builtin/memory/protocol.py`).

Le contrat du fil entre loom et `loom-mem-gpu` (protocole 1), partagé par les deux côtés.

Module léger — Pydantic, la bibliothèque standard, et `bge_m3` pour ses
constantes (qui n'importe torch qu'au chargement des poids) — : le client de
loom l'importe sans torch.

    GET  /v1/health   l'état de l'application, ses modèles, ses plafonds
    POST /v1/embed    {texts}            → {items: [{dense, sparse}]}
    POST /v1/score    {query, passages}  → {scores}

Le dense voyage en base64 de float32 petit-boutiste : c'est ce que la base
range (`store.py`), donc rien ne se perd en route. Le lexical voyage tel
quel : identifiants de tokens triés et leurs poids.

Les plafonds valent par requête. Le client envoie des lots de `BATCH`
textes : il ne les atteint jamais.

## Constantes de `loom_ia.tools_builtin.memory.protocol`

- `PROTOCOL = 1` : Version du protocole entre loom et `loom-mem-gpu`.
- `APP = 'loom-mem-gpu'` : Le nom de l'application, dans ses messages comme dans ceux du client.
- `MAX_TEXTS = 32` : Textes (ou passages) au plus par requête ; caractères au plus par texte, la borne que les modèles appliquent eux-mêmes (le client coupe avant d'envoyer, rien n'est donc refusé) ; octets au plus par corps de requête, lu seulement après ce contrôle. Le corps le plus lourd — une question et MAX_TEXTS passages, tout en caractères échappés en JSON (6 octets) — tient sous MAX_BODY_BYTES.
- `MAX_CHARS = bge_m3.MAX_CHARS` : Caractères au plus par texte : la borne de `bge_m3`.
- `MAX_BODY_BYTES = 4000000` : Octets au plus par corps de requête.
- `BATCH = bge_m3.BATCH` : Textes par requête du client : les lots de BGE-M3. Un appel annulé s'arrête au suivant.
- `State = Literal['loading', 'ready', 'failed']` : L'état de l'application : modèles en chargement, prêts, ou chargement raté.

## Classe `loom_ia.tools_builtin.memory.protocol._Wire`

```python
class _Wire(BaseModel)
```

La base des messages du fil : figés, sans champ inconnu.

## Classe `loom_ia.tools_builtin.memory.protocol.EmbedRequest`

```python
class EmbedRequest(_Wire)
```

Le corps de `POST /v1/embed`.

**Attributs :**

- `texts` : Les textes à vectoriser.

## Classe `loom_ia.tools_builtin.memory.protocol.SparseWire`

```python
class SparseWire(_Wire)
```

Le lexical tel qu'il voyage.

**Attributs :**

- `indices` : Identifiants de tokens triés.
- `values` : Leurs poids.

## Classe `loom_ia.tools_builtin.memory.protocol.EmbedItem`

```python
class EmbedItem(_Wire)
```

Les vecteurs d'un texte, tels qu'ils voyagent.

**Attributs :**

- `dense` : Le dense, en base64 de float32 petit-boutiste.
- `sparse` : Le lexical.

## Classe `loom_ia.tools_builtin.memory.protocol.EmbedResponse`

```python
class EmbedResponse(_Wire)
```

La réponse de `POST /v1/embed`.

**Attributs :**

- `items` : Les vecteurs, un par texte, dans l'ordre.

## Classe `loom_ia.tools_builtin.memory.protocol.ScoreRequest`

```python
class ScoreRequest(_Wire)
```

Le corps de `POST /v1/score`.

**Attributs :**

- `query` : La question.
- `passages` : Les passages à noter.

## Classe `loom_ia.tools_builtin.memory.protocol.ScoreResponse`

```python
class ScoreResponse(_Wire)
```

La réponse de `POST /v1/score`.

**Attributs :**

- `scores` : Les scores, un par passage, dans l'ordre.

## Classe `loom_ia.tools_builtin.memory.protocol.Health`

```python
class Health(_Wire)
```

L'état de l'application. Avant `ready`, seuls `protocol`, `state` et `error`.

## Classe `loom_ia.tools_builtin.memory.protocol.ErrorBody`

```python
class ErrorBody(_Wire)
```

Le corps d'une réponse d'erreur.

**Attributs :**

- `error` : Le message.
- `state` : Sur un 503 : `loading` ou `failed` quand les modèles ne sont pas prêts ; absent quand la file est pleine.

## Fonction `loom_ia.tools_builtin.memory.protocol.over_caps`

```python
def over_caps(texts: Sequence[str], what: str='textes') -> str | None
```

Pourquoi ces textes dépassent les plafonds d'une requête, ou `None`.

## Fonction `loom_ia.tools_builtin.memory.protocol.encode_dense`

```python
def encode_dense(values: Sequence[float]) -> str
```

Le dense en base64 de float32 petit-boutiste.

## Fonction `loom_ia.tools_builtin.memory.protocol.decode_dense`

```python
def decode_dense(text: str) -> list[float]
```

L'inverse d'`encode_dense` ; `ValueError` si le texte n'en est pas un.

## Fonction `loom_ia.tools_builtin.memory.protocol.to_item`

```python
def to_item(embedding: Embedding) -> EmbedItem
```

Les vecteurs d'un texte, prêts à voyager.

## Fonction `loom_ia.tools_builtin.memory.protocol.from_item`

```python
def from_item(item: EmbedItem) -> Embedding
```

Les vecteurs reçus, décodés.

**Lève :**

- `ValueError` : Indices et poids du lexical de longueurs différentes.
