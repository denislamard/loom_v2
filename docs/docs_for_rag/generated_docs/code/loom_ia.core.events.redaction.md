# loom_ia.core.events.redaction

Module `loom_ia.core.events.redaction` de loom_v2 (fichier `src/loom_ia/core/events/redaction.py`).

Masquage du contenu d'un événement (phase 5.2a, conception §14.2).

Le journal garde tout, toujours : c'est lui qui fait foi, et un run ne se
rejoue pas sans ce que le modèle a lu et répondu. Ce qui se règle, c'est **ce
qu'on en montre** — une clé d'API qui a `read` mais pas `read_content` voit
passer les runs, leurs coûts, leurs durées et leurs verdicts, sans lire la
correspondance d'un artisan avec ses clients.

Le masquage rend un **dictionnaire**, pas un événement : un champ obligatoire
retiré ne repasserait pas la validation, et c'est voulu — un journal masqué
n'est pas un journal qu'on peut rejouer. Ce qui a été retiré est nommé à côté
(`redacted`), pour qu'un lecteur ne prenne pas une absence pour un vide.

Chaque charge déclare ses champs de contenu (`content_fields`, dans
`payloads.py`) ; ici, on ne fait que suivre ces chemins. Le masquage
s'applique dans les lectures du journal (REST, MCP, CLI) quand l'appelant n'a
pas la permission `read_content`.

Contenu : `redacted` et `redacted_all` (masquer), `contents` (extraire
le seul contenu, pour les exports).

## Constantes de `loom_ia.core.events.redaction`

- `MARK = 'redacted'` : Clé ajoutée à la charge masquée : la liste des chemins retirés, dans l'ordre déclaré.

## Fonction `loom_ia.core.events.redaction.redacted`

```python
def redacted(event: Event) -> dict[str, JsonValue]
```

Événement en JSON, privé de ce que sa charge déclare comme contenu.

Un champ absent ou vide n'est pas retiré, et n'est pas nommé : le masquage
ne dit avoir caché que ce qui portait quelque chose.

**Arguments :**

- `event` : L'événement écrit.

**Renvoie :**

L'événement sérialisé en JSON. Si quelque chose a été retiré, la
charge porte `redacted` : la liste des chemins retirés. Le résultat
ne repasse pas la validation : un journal masqué ne se rejoue pas.

## Fonction `loom_ia.core.events.redaction.redacted_all`

```python
def redacted_all(events: Sequence[Event]) -> list[dict[str, JsonValue]]
```

Masque une suite d'événements, chacun par `redacted`, dans le même ordre.

## Fonction `loom_ia.core.events.redaction.contents`

```python
def contents(event: Event) -> dict[str, JsonValue]
```

Le contenu d'un événement, et lui seul : `{chemin déclaré: valeur}` (phase 6.1a).

C'est l'inverse de `redacted` — ce que le masquage retire, ici rendu.
Les exports en `content` s'en servent pour joindre le contenu à part,
sous son chemin, au lieu de recopier toute la charge. Un chemin dans une
liste (`criteria[].reason`) rend la liste de ses valeurs.

**Arguments :**

- `event` : L'événement écrit.

**Renvoie :**

Chemin déclaré → valeur, pour chaque champ de contenu qui porte
quelque chose ; un champ absent ou vide n'y figure pas.

## Fonction `loom_ia.core.events.redaction._pick`

```python
def _pick(where: object, path: str) -> object
```

Ce que désigne le chemin, ou l'absence ; même grammaire que `_remove`.

## Fonction `loom_ia.core.events.redaction._remove`

```python
def _remove(where: object, path: str) -> bool
```

Retire ce que désigne le chemin ; vrai si quelque chose est parti.

Un seul cran de descente suffit à ce que portent les charges :
`arguments` (ici même), `context.metadata` (un cran), et
`criteria[].reason` (un cran, dans chaque élément d'une liste). Un champ
absent ou vide n'est pas retiré.

## Fonction `loom_ia.core.events.redaction._carries`

```python
def _carries(value: object) -> bool
```

Vrai si la valeur porte vraiment quelque chose à cacher.

## Classe `loom_ia.core.events.redaction._Absent`

```python
class _Absent
```

Sentinelle : `None` est une valeur possible, l'absence n'en est pas une.
