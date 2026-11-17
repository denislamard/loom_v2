# loom_ia.engine.refs

Module `loom_ia.engine.refs` de loom_v2 (fichier `src/loom_ia/engine/refs.py`).

Références aux résultats d'outils : `{"$ref": "result:3"}` (point 12).

L'orchestrateur transmet un résultat précédent à un outil sans le recopier.
L'exécuteur remplace la référence par le résultat avant de valider les
arguments : le `data` du résultat s'il existe, sinon son texte.

`result:3` désigne le 3ᵉ appel d'outil du run, dans l'ordre où le modèle
les a demandés. Seuls les résultats du run courant sont adressables, et
seulement ceux obtenus avant le tour en cours : un appel du même tour n'a pas
encore de résultat, et le rejeu reste déterministe.

Certains modèles écrivent la référence sérialisée, en chaîne :
`"{\"$ref\": \"result:3\"}"`. Une chaîne dont tout le contenu est cet
objet est traitée comme la référence elle-même ; sinon, la valeur transmise
serait le texte de la référence, sans aucune erreur. Si la valeur transmise
ne convient pas au champ, le refus le dit (`ref_origin`) : la référence,
écrite en chaîne ou non, ce qu'elle a transmis, ce que le champ attend.

Pour que le modèle connaisse ces numéros, chaque résultat qu'il reçoit
commence par sa référence (`[result:3]`) et une consigne s'ajoute au prompt
système, dès qu'un rôle est proposé dans le run. Ce marquage n'existe que dans
la requête : le journal garde les résultats tels quels, dans leur ordre
d'arrivée, et la requête les remet dans l'ordre des appels.

Un résultat déporté (point 16) n'a dans le journal qu'un aperçu : sa référence
transmet le contenu complet, relu dans le stockage d'artefacts. L'orchestrateur
peut ainsi passer un gros résultat à un rôle sans l'avoir lu.

## Constantes de `loom_ia.engine.refs`

- `REF_KEY = '$ref'` : Clé de l'objet référence.
- `REF_PREFIX = 'result:'` : Début d'une référence ; suit le numéro de l'appel, à partir de 1.
- `REFS_HINT = 'Chaque résultat d\'outil commence par sa référence, par exemple [result:3]. Pour passer un résultat à un argument prév…` : Consigne ajoutée au prompt système dès qu'un rôle est proposé dans le run.

## Classe `loom_ia.engine.refs.RefError`

```python
class RefError(Exception)
```

Référence impossible à résoudre ; le message est destiné au modèle.

L'exécuteur en fait un résultat d'erreur de l'appel, sans l'exécuter.

**Arguments :**

- `message` : Ce qui ne va pas, avec les références disponibles.

**Attributs :**

- `message` : Le message.

## Classe `loom_ia.engine.refs.CallRecord`

```python
@dataclass(frozen=True, slots=True)
class CallRecord
```

Un appel d'outil du run, et son résultat s'il est arrivé.

**Attributs :**

- `number` : Rang de l'appel dans le run, à partir de 1, dans l'ordre des demandes du modèle.
- `call_id` : Identifiant de l'appel.
- `name` : Nom de l'outil.
- `output` : Résultat ; `None` tant qu'il n'est pas arrivé.

### Méthode `loom_ia.engine.refs.CallRecord.ref`

```python
@property
def ref(self) -> str
```

Référence de l'appel : `result:<number>`.

### Méthode `loom_ia.engine.refs.CallRecord.usable`

```python
@property
def usable(self) -> bool
```

Vrai si l'appel a un résultat qui n'est pas une erreur : il peut être transmis.

## Fonction `loom_ia.engine.refs.output_value`

```python
def output_value(output: ToolOutput) -> JsonValue
```

Ce qu'une référence transmet : `data` s'il existe, sinon le texte.

## Fonction `loom_ia.engine.refs.output_text`

```python
def output_text(output: ToolOutput) -> str
```

Contenu d'un résultat en texte : le texte, ou le JSON de `data`.

## Classe `loom_ia.engine.refs.ResultIndex`

```python
class ResultIndex
```

Appels d'outils d'un run, numérotés dans l'ordre des demandes du modèle.

`artifacts` sert à relire le contenu complet d'un résultat déporté.

**Arguments :**

- `messages` : Messages du run : les appels viennent des messages `assistant`, les résultats des messages `tool`.
- `artifacts` : Magasin de fichiers.

**Attributs :**

- `records` : Les appels, dans l'ordre.

### Méthode `loom_ia.engine.refs.ResultIndex.ref_of`

```python
def ref_of(self, call_id: str) -> str | None
```

Référence `result:N` d'un appel ; `None` s'il n'est pas dans le run.

### Méthode `loom_ia.engine.refs.ResultIndex.record`

```python
def record(self, ref: str) -> CallRecord | None
```

L'appel que désigne une référence `result:N`, s'il existe.

**Arguments :**

- `ref` : La référence, avec ou sans `result:`.

**Renvoie :**

L'appel ; `None` pour un numéro invalide ou hors du run.

### Méthode `loom_ia.engine.refs.ResultIndex.results_of`

```python
def results_of(self, name: str) -> tuple[CallRecord, ...]
```

Résultats réussis d'un outil, dans l'ordre des appels.

### Méthode `loom_ia.engine.refs.ResultIndex.resolve`

```python
async def resolve(self, arguments: dict[str, JsonValue]) -> tuple[dict[str, JsonValue], tuple[str, ...]]
```

Arguments aux références remplacées, et les références résolues.

Une référence est remplacée à toute profondeur, en objet ou écrite en
chaîne.

**Arguments :**

- `arguments` : Arguments écrits par le modèle.

**Renvoie :**

Les arguments résolus, et les références remplacées, dans l'ordre
où elles ont été trouvées.

**Lève :**

- `RefError` : Référence invalide, inconnue, sans résultat (appel du même tour), en erreur, vide, ou contenu déporté illisible.

### Méthode `loom_ia.engine.refs.ResultIndex.content`

```python
async def content(self, record: CallRecord) -> JsonValue
```

Ce que transmet un résultat : `data` ou texte, relu s'il a été déporté.

**Arguments :**

- `record` : L'appel.

**Renvoie :**

Le `data` du résultat, sinon son texte ; pour un résultat
déporté, le contenu complet (JSON si l'URI finit par `.json`).

**Lève :**

- `RefError` : Pas de résultat, ou contenu déporté impossible à relire.

### Méthode `loom_ia.engine.refs.ResultIndex.text`

```python
async def text(self, record: CallRecord) -> str
```

Contenu d'un résultat en texte : le texte, ou le JSON de `data`.

### Méthode `loom_ia.engine.refs.ResultIndex._value`

```python
async def _value(self, ref: str, found: list[str]) -> JsonValue
```

Valeur transmise par une référence, ajoutée à `found`.

**Lève :**

- `RefError` : Référence invalide, inconnue, sans résultat, en erreur ou vide ; le message liste les références disponibles.

## Classe `loom_ia.engine.refs.RefOrigin`

```python
@dataclass(frozen=True, slots=True)
class RefOrigin
```

La référence qui a fourni une valeur des arguments résolus.

**Attributs :**

- `ref` : La référence (`result:N`).
- `path` : Où elle était dans les arguments écrits par le modèle.
- `serialized` : Écrite en chaîne (`"{\"$ref\": …}"`) plutôt qu'en objet.

## Fonction `loom_ia.engine.refs.ref_origin`

```python
def ref_origin(written: JsonValue, path: Sequence[str | int]) -> RefOrigin | None
```

La référence d'où vient la valeur à `path` des arguments résolus, s'il y en a une.

`written` sont les arguments tels que le modèle les a écrits ; `path`
un chemin dans les arguments résolus — celui d'une erreur de schéma. La
référence peut être sur le chemin même, ou plus haut : une erreur dans un
objet transmis par référence vient de cette référence.

**Arguments :**

- `written` : Arguments tels que le modèle les a écrits.
- `path` : Chemin dans les arguments résolus.

**Renvoie :**

La référence trouvée sur le chemin ; `None` s'il n'y en a pas.

## Fonction `loom_ia.engine.refs._number`

```python
def _number(text: str) -> int | None
```

Le numéro écrit en chiffres décimaux ASCII, ou `None` si ce n'en est pas un.

`str.isdigit` accepte `²` ou `٣`, que `int` refuse ; `int` refuse
aussi, par défaut, plus de 4 300 chiffres. Aucun de ces textes ne désigne un
appel : ce sont des références invalides, à dire au modèle.

## Fonction `loom_ia.engine.refs._ref_in`

```python
def _ref_in(value: JsonValue) -> str | None
```

Référence portée par une valeur : l'objet `{"$ref": …}`, ou ce même objet en chaîne.

## Fonction `loom_ia.engine.refs.in_call_order`

```python
def in_call_order(messages: Sequence[Message]) -> tuple[Message, ...]
```

Messages d'une requête, les résultats de chaque tour dans l'ordre des appels.

Le journal garde les résultats dans leur ordre d'arrivée, qui dépend des
durées des outils. La requête, elle, ne doit dépendre que de ce que le
modèle a demandé : reproductible d'un run à l'autre, et stable pour le
cache des fournisseurs.

**Arguments :**

- `messages` : Messages de la requête, résultats dans l'ordre d'arrivée.

**Renvoie :**

Les mêmes messages, les messages `tool` de chaque tour triés selon
l'ordre des appels du message `assistant` qui les précède.

## Fonction `loom_ia.engine.refs.mark_results`

```python
def mark_results(messages: Sequence[Message], index: ResultIndex) -> tuple[Message, ...]
```

Messages d'une requête, chaque résultat réussi précédé de sa référence.

**Arguments :**

- `messages` : Messages de la requête.
- `index` : Index des appels du run.

**Renvoie :**

Les messages ; chaque résultat réussi commence par un bloc texte
`[result:N]`. Les erreurs ne sont pas marquées.
