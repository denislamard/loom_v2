# loom_ia.telemetry.spans

Module `loom_ia.telemetry.spans` de loom_v2 (fichier `src/loom_ia/telemetry/spans.py`).

Les spans d'un run, tirés de son journal (point 29, conception §14.1).

Le journal porte déjà l'arbre : chaque événement a son `span_id` et son
`parent_span_id`. Ce module en fait des **spans** — un nom, un début, une
fin, des attributs, les événements qui s'y sont produits — sans rien savoir
d'OpenTelemetry : l'adaptateur `otel` ne fait que traduire.

Ce qu'est un span, d'après ce qu'il contient :

- `run` : celui de `run.started` (`invoke_agent <agent>`) ;
- `step` : une étape de la boucle (`step <n>`) ;
- `tool` : un appel d'outil, de `tool.called` à `tool.completed`
  (`execute_tool <outil>`) — un rôle délégué est un outil ;
- `judge` : le travail d'un juge ;
- `model` : un span qui ne porte que des appels de modèle, celui d'un rôle ;
- `chat` : **chaque** `model.responded` donne un span à lui (``chat
  <modèle>``), enfant du span où il a été écrit, qui dure ce qu'a duré
  l'appel. Son identifiant est celui de l'événement : il ne bouge pas d'un
  export à l'autre.

Le contenu ne part que si `content` est vrai, champ par champ
(`content_fields`), sous `loom.content.<chemin>` et masqué. Sinon les
attributs sont tous des métadonnées : facettes, usage, coûts, durées.

## Constantes de `loom_ia.telemetry.spans`

- `PREFIX = 'loom.'` : Préfixe des attributs propres à loom ; les autres suivent les conventions GenAI d'OpenTelemetry (`gen_ai.*`), pour qu'un outil qui les connaît s'y retrouve.

## Classe `loom_ia.telemetry.spans.SpanEvent`

```python
@dataclass(frozen=True, slots=True)
class SpanEvent
```

Un événement du journal, posé sur le span où il s'est produit.

**Attributs :**

- `name` : Type de l'événement (`tool.called`…).
- `at` : Horodatage de l'événement.
- `attributes` : Ses attributs : identifiants, statut, et son contenu masqué si la capture le permet.

## Classe `loom_ia.telemetry.spans.SpanRecord`

```python
@dataclass(frozen=True, slots=True)
class SpanRecord
```

Un span, en termes du journal : identifiants de loom, horodatages réels.

**Attributs :**

- `trace_id` : Identifiant de la trace : le run racine.
- `span_id` : Identifiant du span, celui du journal.
- `parent_span_id` : Span parent ; `None` pour le run racine.
- `name` : Nom du span : `invoke_agent <agent>`, `step <n>`, `execute_tool <outil>`, `judge <nom>`, `chat <modèle>`, `role <rôle>` ou `model`, à défaut le type du premier événement.
- `kind` : Genre : `run`, `step`, `tool`, `judge`, `model`, `chat` ou `other`.
- `start` : Début réel.
- `end` : Fin réelle.
- `attributes` : Attributs : conventions GenAI d'OpenTelemetry, et `loom.*` pour le reste.
- `events` : Les événements du journal posés sur ce span.
- `error` : Nom de ce qui a échoué (un type d'erreur, un type d'événement), jamais le message : lui est du contenu.

## Fonction `loom_ia.telemetry.spans.run_spans`

```python
def run_spans(events: Sequence[Event], *, content: bool=False, redactor: Redactor | None=None) -> list[SpanRecord]
```

Les spans d'un run, dans l'ordre où ils s'ouvrent.

`events` sont ceux d'un seul run, dans l'ordre du journal. Un sous-run
est un autre run : ses spans viennent de ses propres événements, et son
span racine a pour parent l'appel qui l'a lancé.

**Arguments :**

- `events` : Les événements du run.
- `content` : Vrai pour joindre le contenu (`capture.exports: content`).
- `redactor` : Masqueur appliqué au contenu.

**Renvoie :**

Les spans : un par `span_id` du journal, plus un span `chat` par
réponse de modèle.

## Fonction `loom_ia.telemetry.spans.finished`

```python
def finished(event: Event) -> bool
```

Vrai pour le dernier événement qu'écrit un run : celui qui le clôt.

**Arguments :**

- `event` : L'événement.

## Fonction `loom_ia.telemetry.spans._name`

```python
def _name(kind: SpanKind, members: Sequence[Event]) -> str
```

Nom du span selon son genre : `invoke_agent`, `step`, `execute_tool`, `judge`, `role` ou le type du premier événement.

## Fonction `loom_ia.telemetry.spans._started`

```python
def _started(event: Event) -> datetime
```

Début de ce qu'un événement raconte : l'appel d'un modèle a duré avant d'être écrit.

## Fonction `loom_ia.telemetry.spans._specific`

```python
def _specific(kind: SpanKind, members: Sequence[Event]) -> dict[str, AttributeValue]
```

Attributs propres au genre du span : run (genre, profondeur, statut, itérations…), étape, outil, juge.

## Fonction `loom_ia.telemetry.spans._usage`

```python
def _usage(usage: Usage) -> dict[str, AttributeValue]
```

Consommation, aux noms des conventions GenAI d'OpenTelemetry.

`gen_ai.usage.input_tokens` compte **toute** l'entrée, cache compris
(« SHOULD include all types of input tokens, including cached tokens ») ;
`cache_read` et `cache_write` en disent la part, et y sont inclus. Un
outil qui suit la convention lit donc l'entrée réelle — lors de l'essai de
la fonction K4, le 21/10, il recevait l'entrée hors cache, 50 tokens pour un run de 3 900.
Le raisonnement, sans attribut de la convention, reste un attribut de loom.

## Fonction `loom_ia.telemetry.spans._chat`

```python
def _chat(event: Event, content: bool, redactor: Redactor | None) -> SpanRecord
```

Le span `chat <modèle>` d'une réponse de modèle : modèle, fournisseur, tokens, coût, latence, et le message si le contenu part.

## Fonction `loom_ia.telemetry.spans._event`

```python
def _event(event: Event, content: bool, redactor: Redactor | None) -> SpanEvent
```

Un événement posé sur son span : identifiant, position, statut, facettes, et son contenu masqué si la capture le permet.

## Fonction `loom_ia.telemetry.spans._text`

```python
def _text(value: JsonValue, redactor: Redactor | None) -> str
```

Le contenu d'un champ, en texte : une chaîne telle quelle, le reste en JSON.
