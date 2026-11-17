# loom_ia.tools_builtin.memory.chunk

Module `loom_ia.tools_builtin.memory.chunk` de loom_v2 (fichier `src/loom_ia/tools_builtin/memory/chunk.py`).

Texte des documents : empreinte, découpage en passages, extraits.

Découpage structurel : par titres markdown, puis retaille des sections trop
longues en paragraphes avec chevauchement. Chaque passage connaît son chemin
de titres, qui préfixe le texte vectorisé.

Ce qui est fait pour ne pas couper le sens :

- un paragraphe qui annonce la suite (question de FAQ, « … : », étiquette) reste
  avec ce qu'il annonce, en fin de passage comme en fin de section ;
- un morceau trop court rejoint un voisin, et ses titres passent dans le texte ;
  le premier du document, qui n'a pas de précédent, rejoint le suivant.

## Constantes de `loom_ia.tools_builtin.memory.chunk`

- `LEAD_CHARS = 200` : Un paragraphe qui annonce la suite — une question, une phrase qui finit par « : », une étiquette (« **Règles générales** ») — n'est jamais laissé en fin de passage ni en fin de section : il rejoint ce qu'il annonce. Au-delà de cette longueur, ce n'est plus une annonce.

## Fonction `loom_ia.tools_builtin.memory.chunk.normalize`

```python
def normalize(text: str) -> str
```

Le texte sans ce qui ne change pas son contenu : casse, espaces, formes Unicode.

## Fonction `loom_ia.tools_builtin.memory.chunk.content_hash`

```python
def content_hash(text: str) -> str
```

Empreinte du contenu, insensible à la casse et aux espaces : la déduplication.

## Fonction `loom_ia.tools_builtin.memory.chunk.snippet`

```python
def snippet(text: str, limit: int) -> str
```

Le début du texte, sur une ligne, coupé à un espace et marqué « … ».

## Classe `loom_ia.tools_builtin.memory.chunk._Section`

```python
@dataclass(slots=True)
class _Section
```

Une section du document, entre deux titres.

**Attributs :**

- `heading_path` : Le chemin des titres de la section, rendu avec le passage.
- `headings` : Titres sous celui du document : (niveau, texte), du plus haut au plus bas.
- `body` : Le texte de la section, sans son titre.

## Classe `loom_ia.tools_builtin.memory.chunk._Piece`

```python
@dataclass(frozen=True, slots=True)
class _Piece
```

Un morceau du corps d'une section, avant la fusion des trop petits.

**Attributs :**

- `heading_path` : Le chemin des titres de sa section.
- `headings` : Les titres de sa section : (niveau, texte).
- `body` : Son texte.

## Fonction `loom_ia.tools_builtin.memory.chunk.chunk_document`

```python
def chunk_document(doc_id: str, title: str, text: str, spec: ChunkingSpec) -> list[Chunk]
```

Les passages du document, numérotés à partir de 0.

## Fonction `loom_ia.tools_builtin.memory.chunk._split_sections`

```python
def _split_sections(title: str, text: str) -> list[_Section]
```

Une section par titre rencontré ; le titre du document est la racine du chemin.

## Fonction `loom_ia.tools_builtin.memory.chunk.announces`

```python
def announces(paragraph: str) -> bool
```

Vrai si le paragraphe annonce ce qui le suit : question, « … : », ou étiquette.

L'étiquette est une ligne courte sans ponctuation finale, hors élément de liste ;
l'emphase et les guillemets qui la ferment ne comptent pas (« **Q ?** » est une
question). Code, tableau et HTML n'annoncent rien.

## Fonction `loom_ia.tools_builtin.memory.chunk._carry_leads`

```python
def _carry_leads(sections: list[_Section]) -> list[_Section]
```

Une annonce en fin de section passe en tête de la section suivante.

C'est l'étiquette d'une page web posée au-dessus d'un titre (« Contact »), ou la
phrase « … : » qui introduit la section suivante. Parcouru de la fin : une annonce
reçue n'est pas renvoyée plus loin. Une section réduite à son annonce la garde.

## Fonction `loom_ia.tools_builtin.memory.chunk._open_fence`

```python
def _open_fence(text: str) -> bool
```

Vrai si un bloc de code est ouvert à la fin du texte.

## Fonction `loom_ia.tools_builtin.memory.chunk._split_body`

```python
def _split_body(body: str, spec: ChunkingSpec) -> list[str]
```

Les passages du corps d'une section.

Un corps de `max_chars` au plus reste entier. Sinon, les paragraphes (les
trop longs découpés) sont regroupés jusqu'à `target_chars`, chaque passage
ouvert par la fin du précédent (`overlap_chars`). Une annonce ne finit
pas un passage : seule, elle garde ce qu'elle annonce jusqu'à `max_chars` ;
sinon elle passe en tête du suivant.

## Fonction `loom_ia.tools_builtin.memory.chunk._split_long`

```python
def _split_long(paragraph: str, max_chars: int) -> list[str]
```

Un paragraphe seul plus long que `max_chars` : coupé aux phrases, sinon en dur.

## Fonction `loom_ia.tools_builtin.memory.chunk._merge_tiny`

```python
def _merge_tiny(pieces: list[_Piece], spec: ChunkingSpec) -> list[_Piece]
```

Absorbe les morceaux trop courts dans un voisin, quand ça tient sous `max_chars`.

Un morceau court rejoint le précédent, qui garde son chemin. Le premier du
document, qui n'en a pas, rejoint le suivant et prend son chemin, plus précis —
si son propre chemin y est inclus (le texte d'avant le premier titre, en
général). Les titres du morceau absorbé que l'autre n'a pas sont écrits dans le
texte : le chemin n'en garde qu'un, et la recherche ne doit pas perdre l'autre.

## Fonction `loom_ia.tools_builtin.memory.chunk._joined`

```python
def _joined(first: _Piece, second: _Piece) -> str
```

Les textes de deux morceaux voisins, avec les titres du second que le premier n'a pas.
