# loom_ia.telemetry.redaction

Module `loom_ia.telemetry.redaction` de loom_v2 (fichier `src/loom_ia/telemetry/redaction.py`).

Masquage par motifs du contenu exporté (fonction K3, point 30, conception §14.2).

Deux masquages coexistent, et ne se confondent pas :

- celui des **champs** (`core.events.redaction`, 5.2a) retire tout ce
  qu'une charge déclare comme contenu : c'est ce qui part quand la capture
  est `metadata`, et ce qu'une clé sans `read_content` ne lit pas ;
- celui-ci, par **motifs**, remplace dans le contenu qui sort ce qui ressemble
  à un secret ou à une donnée personnelle (clé d'API, e-mail, téléphone, IBAN,
  ou un motif à soi) par son nom entre crochets. Il ne sert qu'aux exports en
  `content`.

Un motif est une heuristique : il masque ce qui lui ressemble, il ne garantit
pas que rien d'autre ne passe. C'est la capture `metadata` qui garantit
qu'aucun contenu ne sort.

Les motifs s'exécutent avec un délai (`core.bounded_regex`). Un motif qui ne
finit pas dans le délai n'a pas dit ce qu'il fallait masquer : le masquage
échoue **en fermé**, et le texte entier est remplacé par une marque.

## Constantes de `loom_ia.telemetry.redaction`

- `BUILTIN_PATTERNS = ('secret', 'email', 'phone', 'iban')` : Motifs fournis, dans l'ordre où ils s'appliquent par défaut : les secrets d'abord, que le téléphone ou l'IBAN mordraient pour en laisser filer le reste.
- `_BUILTIN = {'secret': ('-----BEGIN[A-Z ]*PRIVATE KEY-----[\\s\\S]*?(?:-----END[A-Z ]*PRIVATE KEY-----|\\Z)', '(?<=://)[^\\s:/?#@]+…` : Un motif fourni peut tenir en plusieurs expressions, appliquées l'une après l'autre sous le même nom : réunies en une seule alternative, celles du secret coûtaient six fois plus (le moteur ne cherche plus leurs préfixes).

## Classe `loom_ia.telemetry.redaction.Redactor`

```python
class Redactor
```

Remplace ce que ses motifs reconnaissent par `[nom]`.

**Arguments :**

- `patterns` : Motifs `(nom, expression)`, dans l'ordre d'application.

### Méthode `loom_ia.telemetry.redaction.Redactor.of`

```python
@classmethod
def of(cls, declared: Iterable[str | tuple[str, str]]) -> Redactor
```

Motifs déclarés : un nom de motif fourni, ou `(nom, expression)`.

**Arguments :**

- `declared` : Les motifs.

### Méthode `loom_ia.telemetry.redaction.Redactor.names`

```python
@property
def names(self) -> tuple[str, ...]
```

Les noms des motifs, dans l'ordre où ils s'appliquent, chacun une fois.

### Méthode `loom_ia.telemetry.redaction.Redactor.text`

```python
def text(self, value: str) -> str
```

Le texte, chaque motif remplacé par `[nom]`.

Un motif qui ne finit pas à temps masque tout le texte
(`[nom : délai dépassé, texte masqué]`).

**Arguments :**

- `value` : Le texte.

### Méthode `loom_ia.telemetry.redaction.Redactor.json`

```python
def json(self, value: JsonValue) -> JsonValue
```

Masque chaque chaîne d'une valeur JSON, clés comprises.

Deux clés qui se masquent pareil ne s'écrasent pas : la deuxième devient
`[email]#2`, la troisième `[email]#3` — aucune entrée ne se perd.
