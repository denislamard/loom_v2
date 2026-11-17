# loom_ia.core.model.content

Module `loom_ia.core.model.content` de loom_v2 (fichier `src/loom_ia/core/model/content.py`).

Blocs de contenu des messages et résultat d'outil (points 8 et 15, fonctions B2 et D8).

Le format est neutre : chaque adaptateur de modèle le traduit vers le format
de son fournisseur. Les données propres à un fournisseur (signature du
raisonnement, identifiant d'item…) vivent dans `provider_meta`, typé par
fournisseur ; un adaptateur ne lit que son entrée.

Blocs, par `type` : `text`, `json`, `artifact` (référence d'un
fichier), `inline_data` (octets de passage, jamais journalisés),
`reasoning`, `tool_call` et `tool_result`. Un résultat d'outil
(`ToolOutput`) ne porte que des blocs de sortie : texte, JSON, référence ou
octets.

## Constantes de `loom_ia.core.model.content`

- `ProviderMeta = Annotated[AnthropicMeta | OpenAIMeta, Field(discriminator='provider')]` : Données propres à un fournisseur, discriminées par `provider`.
- `OutputBlock = Annotated[TextBlock | JsonBlock | ArtifactRefBlock | InlineDataBlock, Field(discriminator='type')]` : Bloc qu'un résultat d'outil peut porter : texte, JSON, référence de fichier ou octets de passage.
- `ContentBlock = Annotated[TextBlock | JsonBlock | ArtifactRefBlock | InlineDataBlock | ReasoningBlock | ToolCallBlock | ToolResultBlock…` : Bloc de contenu d'un message, discriminé par `type`.

## Classe `loom_ia.core.model.content.AnthropicMeta`

```python
class AnthropicMeta(DomainModel)
```

Données d'un bloc propres à Anthropic, à renvoyer telles quelles au fournisseur (point 8).

**Attributs :**

- `provider` : `anthropic`.
- `signature` : Signature d'un bloc de raisonnement.
- `redacted_data` : Raisonnement masqué par le fournisseur, opaque.

## Classe `loom_ia.core.model.content.OpenAIMeta`

```python
class OpenAIMeta(DomainModel)
```

Données d'un bloc propres aux API OpenAI et compatibles, à renvoyer au fournisseur (point 8).

**Attributs :**

- `provider` : `openai`.
- `item_id` : API Responses : identifiant de l'élément de raisonnement.
- `encrypted_content` : API Responses : son contenu chiffré, à renvoyer.
- `reasoning_field` : API Chat : champ où le fournisseur a donné le raisonnement (`reasoning`, `reasoning_content`) ; il y est renvoyé (point 7, backlog #015).

## Classe `loom_ia.core.model.content._Block`

```python
class _Block(DomainModel)
```

Champs communs à tous les blocs de contenu.

**Attributs :**

- `cache_breakpoint` : Point de cache neutre (point 8) : chaque adaptateur le traduit (`cache_control` pour Anthropic). Défaut : faux.
- `provider_meta` : Fournisseur → ses données propres. Écrites `{"anthropic": {"signature": …}}`, le fournisseur se déduit de la clé ; la clé et le `provider` doivent s'accorder.

### Méthode `loom_ia.core.model.content._Block._infer_provider`

```python
@field_validator('provider_meta', mode='before')
@classmethod
def _infer_provider(cls, value: object) -> object
```

Déduit `provider` de la clé : `{"anthropic": {"signature": …}}`.

### Méthode `loom_ia.core.model.content._Block._check_provider_keys`

```python
@model_validator(mode='after')
def _check_provider_keys(self) -> Self
```

Refuse une entrée dont la clé n'est pas son fournisseur.

## Classe `loom_ia.core.model.content.TextBlock`

```python
class TextBlock(_Block)
```

Bloc de texte.

**Attributs :**

- `type` : `text`.
- `text` : Le texte.

## Classe `loom_ia.core.model.content.JsonBlock`

```python
class JsonBlock(_Block)
```

Bloc de données JSON, dans un résultat d'outil.

**Attributs :**

- `type` : `json`.
- `data` : Les données.

## Classe `loom_ia.core.model.content.ArtifactRefBlock`

```python
class ArtifactRefBlock(_Block)
```

Référence vers un fichier du stockage d'artefacts, jamais ses octets.

Le moteur résout les références en octets (`InlineDataBlock`) au moment
d'appeler un modèle qui sait lire le fichier (point 14).

**Attributs :**

- `type` : `artifact`.
- `uri` : Référence du fichier (`artifact://…`).
- `media_type` : Type MIME.
- `size` : Taille en octets, si elle est connue.
- `name` : Nom du fichier, tel que le modèle le voit.

## Classe `loom_ia.core.model.content.InlineDataBlock`

```python
class InlineDataBlock(_Block)
```

Octets d'un fichier, de passage : jamais écrits dans le journal.

Deux usages : le résultat d'un outil avant son stockage (le moteur le
remplace par un `ArtifactRefBlock`), et une requête au modèle après la
résolution des références (point 14). Un événement qui en porterait est
refusé.

**Attributs :**

- `type` : `inline_data`.
- `media_type` : Type MIME.
- `data` : Les octets ; en base64 dans le JSON.
- `name` : Nom du fichier.

## Classe `loom_ia.core.model.content.ReasoningBlock`

```python
class ReasoningBlock(_Block)
```

Raisonnement du modèle, neutre vis-à-vis du fournisseur (point 7).

Renvoyé au même modèle pendant ses tours d'outils (`thinking`), retiré
de l'historique de session.

**Attributs :**

- `type` : `reasoning`.
- `text` : Texte du raisonnement ; vide s'il est chiffré ou masqué.
- `model_id` : Modèle qui l'a produit : après une bascule de modèle, le raisonnement d'un autre modèle est écarté.

## Classe `loom_ia.core.model.content.ToolCallBlock`

```python
class ToolCallBlock(_Block)
```

Appel d'outil demandé par le modèle, dans un message `assistant`.

**Attributs :**

- `type` : `tool_call`.
- `call_id` : Identifiant de l'appel, donné par le modèle.
- `name` : Nom de l'outil.
- `arguments` : Arguments de l'appel ; quand une politique ou un approbateur les remplace, la conversation montre ceux qui partent.

## Classe `loom_ia.core.model.content.ToolOutput`

```python
class ToolOutput(DomainModel)
```

Résultat d'un outil, rendu au modèle et journalisé dans `tool.completed` (point 15, fonction D8).

**Attributs :**

- `blocks` : Ce que le modèle lit : texte, JSON, références de fichiers.
- `data` : Données structurées, pour les références `$ref` d'un appel suivant, la réponse structurée et l'interface.
- `is_error` : Vrai pour un résultat en erreur (`ToolError`, délai, refus) ; le modèle le lit comme tel.
- `artifacts` : URI des fichiers produits par l'appel (fonction G3).
- `offloaded` : URI du contenu complet quand il a été déporté (au-delà de `offload_over`) ; les blocs n'en montrent alors qu'un aperçu (point 16).
- `unverified` : Sortie gardée bien qu'elle ne respecte pas son contrat (`on_failure: unverified`).
- `fallback` : Sortie remplacée par le message de repli d'un contrat ou d'un juge (`on_failure: fallback`) ; omis du journal quand il est faux.
- `state` : Écritures du state de la session : `{clé: valeur}`, `None` efface la clé. Le journal les garde dans `tool.completed`, que le rejeu ressert tel quel ; la projection les retire du résultat montré au modèle. Omis quand il est vide : un résultat sans state se sérialise comme avant, et les empreintes des requêtes ne changent pas.
- `state_refused` : Écritures du state refusées (décision D8 du state de session) : une ligne par clé, avec le motif. Le résultat reste un succès — l'outil a pu produire son effet — et le modèle lit ces lignes à la suite du résultat (`state_note`). Omis quand il est vide.

### Méthode `loom_ia.core.model.content.ToolOutput.text`

```python
@classmethod
def text(cls, text: str, *, is_error: bool=False) -> Self
```

Résultat fait d'un seul bloc de texte.

**Arguments :**

- `text` : Le texte.
- `is_error` : Vrai pour un résultat en erreur.

**Renvoie :**

Le résultat.

### Méthode `loom_ia.core.model.content.ToolOutput.state_note`

```python
@property
def state_note(self) -> str
```

Ce que le modèle lit des écritures refusées, à la suite du résultat : une ligne `[state] …` par clé ; vide sinon.

### Méthode `loom_ia.core.model.content.ToolOutput.error`

```python
@classmethod
def error(cls, message: str) -> Self
```

Résultat en erreur, fait d'un seul bloc de texte : le message lu par le modèle.

### Méthode `loom_ia.core.model.content.ToolOutput.as_text`

```python
@property
def as_text(self) -> str
```

Contenu textuel des blocs `text`, concaténé.

## Classe `loom_ia.core.model.content.ToolResultBlock`

```python
class ToolResultBlock(_Block)
```

Résultat d'un appel d'outil, dans un message `tool`.

**Attributs :**

- `type` : `tool_result`.
- `call_id` : Identifiant de l'appel servi.
- `output` : Le résultat.

## Fonction `loom_ia.core.model.content.has_inline_data`

```python
def has_inline_data(blocks: tuple[ContentBlock, ...] | tuple[OutputBlock, ...]) -> bool
```

Vrai si des octets de fichier traînent dans ces blocs, résultats d'outils compris (interdit au journal).

**Arguments :**

- `blocks` : Blocs d'un message ou d'un résultat.

**Renvoie :**

Vrai si un `InlineDataBlock` s'y trouve, à tout niveau.
