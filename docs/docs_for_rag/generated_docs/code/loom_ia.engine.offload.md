# loom_ia.engine.offload

Module `loom_ia.engine.offload` de loom_v2 (fichier `src/loom_ia/engine/offload.py`).

Gros résultats d'outils : déport dans le stockage d'artefacts (point 16, fonction D6).

Au-delà du seuil de l'outil (`offload_over`, en caractères de ce que le
modèle verrait), le contenu complet part dans le stockage d'artefacts et le
résultat n'en garde qu'un aperçu : le début d'un texte, ou la structure d'un
JSON (clés, premiers éléments, nombre total). `tool.completed` porte
l'aperçu et la référence (`offloaded`), jamais le contenu complet.

Le modèle lit la suite avec l'outil intégré `artifact_read`, exposé
seulement après un premier déport dans le run, ou transmet le résultat entier
à un rôle par `$ref` sans le lire.

Sans stockage d'artefacts, le résultat est simplement tronqué.

Réglages : `offload_over` d'un outil, ou `execution.tools.offload_over`
pour tous (50 000 caractères par défaut).

## Constantes de `loom_ia.engine.offload`

- `DEFAULT_OFFLOAD_OVER = 50000` : Seuil de déport par défaut, en caractères (conception §17.6, `execution.tools.offload_over`).
- `PREVIEW_CHARS = 2000` : Taille maximale de l'aperçu, en caractères.
- `PREVIEW_KEYS = 20` : Nombre de clés d'un objet JSON montrées dans l'aperçu.
- `PREVIEW_ITEMS = 3` : Nombre d'éléments d'une liste JSON montrés dans l'aperçu.
- `ARTIFACT_READ = 'artifact_read'` : Nom de l'outil intégré qui lit un résultat déporté.
- `READ_LIMIT = 20000` : Nombre de caractères lus par `artifact_read` si `limit` n'est pas donné.
- `READ_MAX = 50000` : Valeur maximale de `limit` pour `artifact_read`.

## Fonction `loom_ia.engine.offload.visible_text`

```python
def visible_text(output: ToolOutput) -> str
```

Ce que le modèle verrait d'un résultat : ses textes et ses JSON, ou `data`.

C'est sur sa longueur que se mesure le seuil `offload_over`.

**Arguments :**

- `output` : Le résultat de l'outil.

**Renvoie :**

Les textes et les JSON des blocs, une ligne par bloc ; `data` en
JSON si le résultat n'a pas de blocs.

## Fonction `loom_ia.engine.offload.full_content`

```python
def full_content(output: ToolOutput) -> tuple[str, str]
```

Contenu complet à ranger, et son type : `data` en JSON s'il existe, sinon le texte.

**Arguments :**

- `output` : Le résultat de l'outil.

**Renvoie :**

Le contenu et son type MIME (`application/json` ou
`text/plain`).

## Fonction `loom_ia.engine.offload.offloaded`

```python
def offloaded(output: ToolOutput, *, uri: str, content: str, refs: bool) -> ToolOutput
```

Résultat réduit à un aperçu et à la référence de son contenu complet.

**Arguments :**

- `output` : Le résultat d'origine.
- `uri` : Référence du contenu rangé (`artifact://…`).
- `content` : Le contenu complet, pour en dire la longueur.
- `refs` : Vrai si le run accepte les références `$ref` : la note dit alors comment transmettre le résultat entier.

**Renvoie :**

Un résultat dont le texte est l'aperçu suivi d'une note « [Résultat
déporté : … outil artifact_read avec ref=…] », avec les fichiers, le
statut d'erreur et le state d'origine, et `offloaded` à `uri`.

## Fonction `loom_ia.engine.offload.truncated`

```python
def truncated(output: ToolOutput, limit: int) -> ToolOutput
```

Résultat tronqué au seuil, faute de stockage d'artefacts.

**Arguments :**

- `output` : Le résultat d'origine.
- `limit` : Nombre de caractères gardés.

**Renvoie :**

Un résultat dont le texte est coupé à `limit`, suivi d'une note
« [Résultat tronqué : …] ».

## Fonction `loom_ia.engine.offload.preview`

```python
def preview(value: JsonValue) -> str
```

Début d'un texte, ou structure d'un JSON.

**Arguments :**

- `value` : Le contenu.

**Renvoie :**

Un texte : ses 2 000 premiers caractères ; un objet : ses 20
premières clés avec la nature de chaque valeur ; une liste : sa
longueur et ses 3 premiers éléments. Toujours coupé à 2 000
caractères.

## Classe `loom_ia.engine.offload.ArtifactReadTool`

```python
class ArtifactReadTool(DelegatedTool)
```

Outil intégré `artifact_read` : lit un résultat déporté par morceaux.

Arguments du modèle : `ref` (référence `artifact://…`), `offset`
(premier caractère, 0 par défaut), `limit` (nombre de caractères,
20 000 par défaut, 50 000 au plus). Exposé seulement si le run a un
magasin de fichiers et a déjà déporté un résultat.

### Méthode `loom_ia.engine.offload.ArtifactReadTool.__init__`

```python
def __init__(self) -> None
```

Construit la description de l'outil.

### Méthode `loom_ia.engine.offload.ArtifactReadTool.spec`

```python
@property
def spec(self) -> ToolSpec
```

Description de `artifact_read` montrée au modèle.

### Méthode `loom_ia.engine.offload.ArtifactReadTool.available`

```python
def available(self, run: RunView) -> bool
```

Vrai si le run a un magasin de fichiers et au moins un résultat déporté.

### Méthode `loom_ia.engine.offload.ArtifactReadTool.check`

```python
async def check(self, arguments: dict[str, JsonValue], run: RunView) -> str | None
```

Refuse une référence qui n'est pas un résultat déporté de ce run.

**Renvoie :**

« Référence inconnue : … » avec la liste des résultats déportés du
run ; `None` si la référence est connue.

### Méthode `loom_ia.engine.offload.ArtifactReadTool.run`

```python
async def run(self, arguments: dict[str, JsonValue], context: ToolContext, run: RunView) -> AsyncGenerator[DelegatedPayload | ToolOutput]
```

Lit le morceau demandé.

**Produit :**

Un seul résultat : « [Caractères <début> à <fin> sur <total> ;
suite : offset=<fin>] » suivi du morceau, ou « fin du contenu ».

**Lève :**

- `ToolError` : Pas de magasin de fichiers, ou référence introuvable.
