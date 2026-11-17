# loom_ia.telemetry.inspect

Module `loom_ia.telemetry.inspect` de loom_v2 (fichier `src/loom_ia/telemetry/inspect.py`).

Une trace, mise en texte pour un humain : `loom inspect` (K5, N4, 6.2c).

L'arbre du run se lit de haut en bas : le run, ses étapes, dans chaque étape
les appels de modèle (rôle, durée, tokens — l'entrée cache comprise, comme
l'en-tête —, coût, ce que le modèle a répondu) et les appels d'outil (durée,
arguments, résultat ; un appel refusé avant de partir le dit), sous un rôle
son modèle et son juge, sous un sous-agent son propre run ; puis la réponse
finale en entier, et un bilan compté sur l'arbre.

Arguments et résultats tiennent sur une ligne, coupés à `WIDTH` caractères
en le disant (`…`) ; `full` les montre en entier. La réponse finale n'est
jamais coupée. Sans contenu dans la trace (`content` faux), seules les
métadonnées restent, et le texte le dit.

Les lignes portent les textes du modèle et des outils tels quels, caractères
de contrôle compris : qui les imprime dans un terminal les passe par
`telemetry.logs.printable`, comme `loom inspect`.

## Constantes de `loom_ia.telemetry.inspect`

- `WIDTH = 110` : Largeur d'un extrait : au-delà, la ligne est coupée en le disant.
- `_SILENT = frozenset({'run.started', 'run.claimed', 'run.released', 'run.transitioned', 'run.completed', 'message.user', 'step.sta…` : Événements d'un span qui ne disent rien de plus que le span lui-même.

## Fonction `loom_ia.telemetry.inspect.render_trace`

```python
def render_trace(trace: Trace, *, full: bool=False) -> list[str]
```

Les lignes de `loom inspect` pour cette trace.

**Arguments :**

- `trace` : La trace.
- `full` : Vrai pour ne pas couper les extraits.

**Renvoie :**

Les lignes : en-tête du run, puis l'arbre de ses spans.

## Fonction `loom_ia.telemetry.inspect._tree`

```python
def _tree(span: TraceSpan, children: Mapping[str | None, list[TraceSpan]], depth: int, full: bool, asked: frozenset[str]=frozenset()) -> list[str]
```

Les lignes d'un span et de ses enfants, indentées selon la profondeur.

## Fonction `loom_ia.telemetry.inspect._head`

```python
def _head(span: TraceSpan, asked: frozenset[str]=frozenset()) -> str
```

La ligne d'un span : ce qu'il est, combien de temps, combien d'argent.

## Fonction `loom_ia.telemetry.inspect._body`

```python
def _body(span: TraceSpan, full: bool) -> list[str]
```

Ce qui s'est passé dans un span, une ligne par fait.

## Fonction `loom_ia.telemetry.inspect._said`

```python
def _said(span: TraceSpan, event: TraceEvent, full: bool) -> list[str]
```

Ce qu'un événement ajoute sous son span : réponse, arguments, résultat, verdict, approbation, question.

## Fonction `loom_ia.telemetry.inspect._answered`

```python
def _answered(span: TraceSpan, asked: frozenset[str]) -> bool
```

Le résultat d'un outil qui pose une question : rendu sans exécution, et sans erreur.

## Fonction `loom_ia.telemetry.inspect._executed`

```python
def _executed(span: TraceSpan) -> bool
```

Un appel d'outil est parti s'il a son `tool.called` ; un appel refusé n'a que sa fin.

## Fonction `loom_ia.telemetry.inspect._other`

```python
def _other(span: TraceSpan) -> str
```

Un span qui n'est ni run, ni étape, ni appel : une approbation, ou une question.

## Fonction `loom_ia.telemetry.inspect._approval`

```python
def _approval(event: TraceEvent) -> str
```

Une décision d'approbation en clair : qui, par quelle passerelle, et le motif d'un refus.

## Fonction `loom_ia.telemetry.inspect._questions`

```python
def _questions(given: list[JsonValue]) -> str
```

Les questions posées, en une ligne : le texte de chacune.

## Fonction `loom_ia.telemetry.inspect._answer`

```python
def _answer(content: Mapping[str, JsonValue] | None, full: bool) -> list[str]
```

Ce que le modèle a répondu : son texte, puis les outils qu'il appelle.

## Fonction `loom_ia.telemetry.inspect._output`

```python
def _output(value: JsonValue) -> str
```

Le texte d'un résultat d'outil : ses blocs de texte, sinon ses données.

## Fonction `loom_ia.telemetry.inspect._line`

```python
def _line(label: str, text: str, full: bool) -> str
```

Une ligne d'extrait ; coupée en le disant, sauf en entier.

## Fonction `loom_ia.telemetry.inspect._tokens`

```python
def _tokens(a: Mapping[str, AttributeValue]) -> str
```

Tokens d'un appel : l'entrée cache comprise, comme l'en-tête — la part du cache dite.

## Fonction `loom_ia.telemetry.inspect._tally`

```python
def _tally(spans: Sequence[TraceSpan], asked: frozenset[str]=frozenset()) -> str
```

Ce que l'arbre contient, compté sur ses spans.
