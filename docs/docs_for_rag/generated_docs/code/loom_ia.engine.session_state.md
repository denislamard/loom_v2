# loom_ia.engine.session_state

Module `loom_ia.engine.session_state` de loom_v2 (fichier `src/loom_ia/engine/session_state.py`).

State de la session côté moteur : clés déclarées, écritures des outils (option C, `docs/state-et-context.md`).

Les clés viennent de la config (`state:`) ; le montage les traduit en
`StateRules`, que l'exécuteur applique au résultat réussi de chaque outil,
après les politiques `after_tool` et avant le déport :

- les sources `from` de l'outil donnent chacune une valeur, lue dans les
  données du résultat (`path`, pointeur JSON) ou fixe (`value`). Ce que
  l'outil écrit lui-même (`ToolOutput.state`) l'emporte sur une même clé
  (décision D12 du state de session) ;
- chaque écriture est contrôlée : clé déclarée, schéma (décision D6), taille
  de la valeur et du state de la session (décision D7). `None` efface la clé.

Une écriture refusée n'est pas posée, les autres le sont, et le résultat reste
un succès : l'outil a pu produire son effet, et le dire en erreur pousserait
le modèle à le refaire. Le refus est gardé dans le résultat
(`ToolOutput.state_refused`), que le modèle lit à sa suite, et dit au log
(décision D8 du state de session, du 14/10).

Le state que l'application pose à l'ouverture d'un run suit les mêmes
contrôles, mais un refus y refuse le run, avant tout écrit (`StateRefused`) ;
de même une clé `required` restée sans valeur (décision D13).

L'orchestrateur qui déclare `main.context` reçoit le state en dernier message
de chaque requête (décision D4, `state_message`), jamais journalisé comme message, et,
dans son prompt système, ce que désigne chaque clé (`StateRules.hint`) : le
système ne change pas d'un appel à l'autre, seul le bloc de fin est payé hors cache.

Les codes D1 à D13 sont les décisions du state de session (`fonctions.md`,
point 51, et `docs/state-et-context.md`) ; S1 à S8 celles du state v2, et
E2 un écart relevé par son analyse (`docs/state_v2_analyse.md`).

## Constantes de `loom_ia.engine.session_state`

- `StateWriter = Literal['app', 'tools', 'model']` : Qui peut demander une écriture (décision S1 du state v2).  L'application à l'ouverture d'un run, un outil (sources `from` ou `ToolOutput.state`), le modèle (`loom.state`).
- `DEFAULT_WRITERS = frozenset[StateWriter]({'app', 'tools'})` : Écrivains d'une clé qui n'en déclare pas : l'application et les outils.
- `DEFAULT_MAX_VALUE_BYTES = 1024` : Taille maximale d'une valeur, en octets de JSON compact (décision D7 du state de session, `sessions.state`).
- `DEFAULT_MAX_TOTAL_BYTES = 4096` : Taille maximale de tout le state de la session, en octets de JSON compact (décision D7).
- `STATE_HINT = f"La fin de chaque requête donne l'état courant de la session, entre balises <{SESSION_STATE_TAG}> : un objet JSON tenu…` : Début de la consigne du prompt système d'un orchestrateur qui reçoit le state.  Suit une ligne par clé montrée, avec ce qu'elle désigne.

## Classe `loom_ia.engine.session_state.StateSourceRule`

```python
@dataclass(frozen=True, slots=True)
class StateSourceRule
```

Ce que pose un résultat réussi de l'outil : `path` dans ses données, ou `value`.

`on_missing` : `ignore` ne dit rien d'un `path` absent du résultat, que
`refuse` compte comme une écriture refusée (décision D8 du state de session).

Config : `state.<clé>.from.<outil>`.

**Attributs :**

- `path` : Pointeur JSON (RFC 6901) dans les données du résultat.
- `value` : Valeur fixe, si `path` n'est pas donné.
- `on_missing` : `refuse` ou `ignore` un `path` absent du résultat.

## Classe `loom_ia.engine.session_state.StateKeyRule`

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class StateKeyRule
```

Une clé déclarée : son schéma, ce qu'elle désigne, ses sources par outil, son défaut.

`required` : un run racine ne s'ouvre pas tant qu'elle n'a pas de valeur
(décision D13 du state de session) ; ni un outil ni le modèle ne peuvent
l'effacer (écart E2 du state v2). `writers` : qui peut demander une
écriture (décision S1 du state v2) ; par défaut l'application et les outils.

Config : `state.<clé>`.

**Attributs :**

- `schema` : JSON Schema de la valeur.
- `description` : Ce que désigne la clé, montré à l'orchestrateur.
- `sources` : Sources `from`, par nom d'outil.
- `required` : Vrai si un run racine ne s'ouvre pas sans valeur.
- `default` : Valeur lue en l'absence de valeur ; jamais écrite. Exclut `required`.
- `writers` : Qui peut demander une écriture.

## Classe `loom_ia.engine.session_state.StateRefused`

```python
class StateRefused(ValueError)
```

State refusé à l'ouverture d'un run (décisions D6, D7 et D13 du state de session) : rien n'est écrit.

`problems` : un motif par ligne, qui nomme la clé.

**Arguments :**

- `problems` : Les motifs.

**Attributs :**

- `problems` : Les motifs, un par clé refusée.

## Classe `loom_ia.engine.session_state.StateRules`

```python
class StateRules
```

Clés du state que déclare un agent, et limites du state de la session (décisions D6 et D7 du state de session).

**Attributs :**

- `keys` : Les clés déclarées, dans l'ordre de la config.
- `model_tool` : Nom sous lequel `loom.state` est monté : ses écritures sont celles du modèle (décisions S1 et B4 du state v2). Le moteur ne connaît pas l'outil, le montage le lui nomme.
- `max_value_bytes` : Taille maximale d'une valeur, en octets.
- `max_total_bytes` : Taille maximale du state de la session, en octets.

### Méthode `loom_ia.engine.session_state.StateRules.__init__`

```python
def __init__(self, keys: Mapping[str, StateKeyRule] | None=None, *, max_value_bytes: int=DEFAULT_MAX_VALUE_BYTES, max_total_bytes: int=DEFAULT_MAX_TOTAL_BYTES, model_tool: str | None=None) -> None
```

Prépare les validateurs des schémas et les sources par outil.

**Arguments :**

- `keys` : Les clés déclarées.
- `max_value_bytes` : Taille maximale d'une valeur, en octets.
- `max_total_bytes` : Taille maximale du state, en octets.
- `model_tool` : Nom de `loom.state` dans l'agent, s'il est monté.

### Méthode `loom_ia.engine.session_state.StateRules.requires`

```python
@property
def requires(self) -> bool
```

Vrai si une clé est `required` : ouvrir un run demande de relire la session.

### Méthode `loom_ia.engine.session_state.StateRules.writes`

```python
def writes(self, tool: str) -> bool
```

Vrai si une source `from` désigne cet outil.

**Arguments :**

- `tool` : Nom de l'outil.

### Méthode `loom_ia.engine.session_state.StateRules.visible`

```python
def visible(self, values: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]
```

Ce qu'on montre du state : les clés déclarées, valeurs par défaut comprises.

Une valeur qui ne tient plus son schéma — config modifiée depuis son
écriture, autre agent sur la même session — est écartée, avec un
avertissement : jamais une erreur ; la valeur par défaut de la clé la
remplace, s'il y en a une. La copie rendue est en lecture seule ; la
modifier ne touche à rien d'autre.

**Arguments :**

- `values` : Le state de la session.

**Renvoie :**

Les valeurs des clés déclarées, en lecture seule.

### Méthode `loom_ia.engine.session_state.StateRules.opening`

```python
def opening(self, posed: Mapping[str, JsonValue], session: Mapping[str, JsonValue]) -> list[str]
```

Ce qui empêche d'ouvrir un run avec ce state posé par l'application ; vide sinon.

Chaque écriture est contrôlée comme celle d'un outil (décisions D6 et
D7 du state de session), `None` effaçant la clé. Puis chaque clé
`required` doit avoir une valeur dans le state qui en résulte,
`session` étant celui des runs précédents (décision D13) : une
valeur gardée qui ne tient plus son schéma ne compte pas.

**Arguments :**

- `posed` : Le state posé par l'application.
- `session` : Le state de la session avant le run.

**Renvoie :**

Les motifs de refus, un par clé ; vide si le run peut s'ouvrir.

### Méthode `loom_ia.engine.session_state.StateRules.shown`

```python
def shown(self, keys: Sequence[str]) -> tuple[str, ...]
```

Clés que reçoit l'orchestrateur : celles nommées, ou toutes si aucune ne l'est.

Dans l'ordre de leur déclaration.

**Arguments :**

- `keys` : Clés nommées par `main.context` ; vide : toutes.

### Méthode `loom_ia.engine.session_state.StateRules.hint`

```python
def hint(self, keys: Sequence[str]) -> str
```

Consigne du prompt système de l'orchestrateur : la balise, et ce que désigne chaque clé.

Elle ne dépend que de la config : le préfixe de la requête, et son cache,
ne bougent pas quand le state change.

**Arguments :**

- `keys` : Clés nommées par `main.context` ; vide : toutes.

**Renvoie :**

`STATE_HINT` suivi d'une ligne par clé montrée, et d'un paragraphe
sur `loom.state` si une clé est écrite par le modèle.

### Méthode `loom_ia.engine.session_state.StateRules.posed`

```python
def posed(self, tool: str, output: ToolOutput, tally: BatchTally, call_id: str='') -> ToolOutput
```

Le résultat réussi d'un outil, avec les écritures qu'il pose et celles qui sont refusées.

`tally` : le state que le lot laissera, compté dans l'ordre des appels ;
les écritures posées par l'appel `call_id` y sont reportées. Chaque
appel est ainsi jugé sur ce que la session gardera vraiment, quel que
soit l'ordre d'arrivée des résultats, et le lot ne dépasse pas la limite
à lui tous (décisions D7 et D9 du state de session).

**Arguments :**

- `tool` : Nom de l'outil.
- `output` : Son résultat réussi.
- `tally` : Le state du lot, mis à jour.
- `call_id` : L'appel.

**Renvoie :**

Le résultat, `state` réduit aux écritures retenues et
`state_refused` portant les motifs des refus.

### Méthode `loom_ia.engine.session_state.StateRules._forbidden`

```python
def _forbidden(self, name: str, value: JsonValue, writer: StateWriter) -> str | None
```

Pourquoi cet écrivain ne peut pas poser cette valeur, ou `None`.

Écrivain absent des `writers` de la clé (décision S1 du state v2), ou
effacement d'une clé `required` (écart E2 du state v2).

Une clé non déclarée est laissée à `_problem`, qui le dit.

### Méthode `loom_ia.engine.session_state.StateRules._problem`

```python
def _problem(self, name: str, value: JsonValue) -> str | None
```

Pourquoi cette valeur ne peut pas être posée sous cette clé, ou `None`.

Clé non déclarée, schéma non tenu, ou valeur trop grosse.

## Fonction `loom_ia.engine.session_state.keeps_state`

```python
def keeps_state(state: RunState) -> bool
```

Vrai pour un run racine ordinaire : seul lui lit et écrit le state de la session (décision D3 du state de session).

Un sous-run et un run de compaction ne le lisent ni ne l'écrivent.

**Arguments :**

- `state` : L'état du run.

## Fonction `loom_ia.engine.session_state.state_message`

```python
def state_message(values: Mapping[str, JsonValue]) -> Message
```

Le state que reçoit l'orchestrateur, en message de fin de requête (décision D4 du state de session).

Envoyé à chaque appel, même vide (`{}`) : le dernier message de la requête
est toujours lui, jamais une demande ou un résultat d'outil qui l'imiterait
(décision D4 amendée le 14/10). JSON compact aux clés triées, à tous les niveaux : un
même state donne un même texte, donc une même requête. La balise est
échappée dans les valeurs, qui ne peuvent ni fermer le bloc ni en ouvrir un autre.

**Arguments :**

- `values` : Le state montré.

**Renvoie :**

Un message `user` : `<session_state>{…}</session_state>`.

## Classe `loom_ia.engine.session_state.BatchTally`

```python
class BatchTally
```

Le state que la session gardera d'un lot d'appels, compté dans l'ordre des appels (décisions D7 et D9 du state de session).

Il part du state au début du lot. S'y ajoutent les écritures de chaque appel
soldé, rangées à son rang dans la réponse du modèle — et non dans l'ordre où
les résultats arrivent —, comme les range la projection : deux appels qui
écrivent une même clé, c'est le dernier appelé qui l'emporte. Un appel déjà
soldé au journal (reprise d'un lot interrompu, rejeu) compte comme les autres.

`ranks` : le rang de chaque appel du lot ; un appel absent se range après,
dans l'ordre où il est reporté.

**Arguments :**

- `base` : Le state au début du lot.
- `ranks` : Rang de chaque appel du lot, par `call_id`.

### Méthode `loom_ia.engine.session_state.BatchTally.record`

```python
def record(self, call_id: str, writes: Mapping[str, JsonValue]) -> None
```

Reporte les écritures retenues de l'appel `call_id`.

**Arguments :**

- `call_id` : L'appel.
- `writes` : Ses écritures ; `None` efface la clé.

### Méthode `loom_ia.engine.session_state.BatchTally.state`

```python
def state(self, call_id: str | None=None, writes: Mapping[str, JsonValue] | None=None) -> dict[str, JsonValue]
```

Le state du lot ; avec `call_id`, comme si cet appel écrivait aussi `writes`.

**Arguments :**

- `call_id` : Appel à essayer, ou `None`.
- `writes` : Ses écritures.

**Renvoie :**

Le state, écritures appliquées dans l'ordre des rangs.

### Méthode `loom_ia.engine.session_state.BatchTally.overridden`

```python
def overridden(self, call_id: str, name: str) -> bool
```

Vrai si un appel suivant du lot, déjà soldé, écrit aussi `name` : il l'emporte.

**Arguments :**

- `call_id` : L'appel.
- `name` : La clé.

## Fonction `loom_ia.engine.session_state._merged`

```python
def _merged(values: Mapping[str, JsonValue], writes: Mapping[str, JsonValue]) -> dict[str, JsonValue]
```

Le state après ces écritures : `None` efface la clé (décision D10 du state de session).

## Fonction `loom_ia.engine.session_state._writers`

```python
def _writers(rule: StateKeyRule) -> str
```

Les écrivains permis d'une clé, pour un motif de refus.

## Fonction `loom_ia.engine.session_state._said`

```python
def _said(error: ValidationError) -> str
```

Le problème, sans recopier la valeur : elle peut porter un secret.

## Classe `loom_ia.engine.session_state._Absent`

```python
class _Absent
```

Sentinelle : `None` est une valeur possible, l'absence n'en est pas une.

## Fonction `loom_ia.engine.session_state.pointed`

```python
def pointed(data: JsonValue, path: str) -> JsonValue | _Absent
```

Valeur que désigne un pointeur JSON (RFC 6901) dans les données d'un résultat.

Un résultat sans données n'a rien à montrer, même pour `""`. Un indice de
liste s'écrit en chiffres, sans zéro en tête.

**Arguments :**

- `data` : Les données du résultat.
- `path` : Le pointeur (`""` : tout ; `/a/0/b` : un chemin).

**Renvoie :**

La valeur ; `_ABSENT` si le chemin n'existe pas.
