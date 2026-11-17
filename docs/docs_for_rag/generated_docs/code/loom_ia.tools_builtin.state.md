# loom_ia.tools_builtin.state

Module `loom_ia.tools_builtin.state` de loom_v2 (fichier `src/loom_ia/tools_builtin/state.py`).

`loom.state` : le modèle tient à jour le state de la session (state v2).

Le modèle **désigne** un résultat d'outil du run par sa référence (`result:3`,
celle qui précède chaque résultat) ; l'outil le relit (`ToolContext.results`)
et en **tire** la valeur selon la règle déclarée sur la clé (`extract:`). Le
modèle ne transmet aucune donnée : il ne peut ni mal recopier un identifiant,
ni en inventer un.

    {"set": "client", "result": "result:3", "match": {"code": "C-261"}}
    {"set": "selection", "result": "result:5", "label": "factures en retard"}
    {"clear": "document"}

L'outil est tolérant sur la forme de ce qu'il reçoit, strict sur le fond :
`result` vaut `"result:3"`, `"3"`, `3` ou `{"$ref": "result:3"}` (le
moteur ne remplace pas cette référence par des données, pour cet outil) ; la
ligne se désigne par `match` (des colonnes et leurs valeurs, que doit porter
une seule ligne) plutôt que par `row`, qu'un modèle compte mal. Poser une clé
efface aussi celles de son `clears:`.

Seules les clés qui ont `model` dans leurs `writers` sont proposées. Un
refus — clé inconnue, résultat introuvable, extraction ou schéma — est un
résultat en erreur qui dit quoi corriger (décision S4 du state v2) : l'outil n'a pas d'autre effet
que l'écriture. L'écriture passe ensuite par les contrôles du moteur, comme
celle de tout outil (décisions D6 à D9 du state de session) : seule la taille totale du state, qui dépend des
voisins du lot, peut encore la refuser, en le disant à la suite du résultat.

## Constantes de `loom_ia.tools_builtin.state`

- `NAME = 'state'` : Nom de l'outil.
- `MAX_LABEL = 200` : Longueur au plus d'un libellé de clé montré au modèle.

## Classe `loom_ia.tools_builtin.state.StateTool`

```python
class StateTool
```

Pose ou efface une clé du state que le modèle tient à jour.

### Méthode `loom_ia.tools_builtin.state.StateTool.__init__`

```python
def __init__(self, keys: tuple[ModelKey, ...], max_value_bytes: int) -> None
```

Prépare l'outil pour les clés que le modèle tient à jour.

**Arguments :**

- `keys` : Les clés qui ont `model` dans leurs `writers`.
- `max_value_bytes` : Taille permise d'une valeur.

**Lève :**

- `ValueError` : Aucune clé.

### Méthode `loom_ia.tools_builtin.state.StateTool.spec`

```python
@property
def spec(self) -> ToolSpec
```

La définition de l'outil.

### Méthode `loom_ia.tools_builtin.state.StateTool.settings`

```python
def settings(self) -> Settings
```

La règle de chaque clé, pour `loom validate`.

### Méthode `loom_ia.tools_builtin.state.StateTool.invoke`

```python
async def invoke(self, arguments: dict[str, JsonValue], context: ToolContext) -> ToolOutput
```

Pose une clé depuis un résultat (`set`) ou l'efface (`clear`).

**Lève :**

- `ToolError` : Run qui ne tient pas le state, action manquante ou double, clé inconnue, clé obligatoire effacée, ou argument refusé.

### Méthode `loom_ia.tools_builtin.state.StateTool._set`

```python
async def _set(self, key: ModelKey, arguments: Mapping[str, JsonValue], context: ToolContext) -> ToolOutput
```

Pose une clé : la valeur tirée du résultat désigné (`result`, et au besoin `row` ou `match`), contrôlée par son schéma et sa taille.

### Méthode `loom_ia.tools_builtin.state.StateTool._problem`

```python
def _problem(self, name: str, value: JsonValue) -> str | None
```

Ce qui empêche de poser cette valeur, sans la recopier (elle peut porter un secret).

## Fonction `loom_ia.tools_builtin.state.build`

```python
def build(params: BaseModel, context: BuiltinContext) -> StateTool
```

La fabrique de `loom.state` : les clés viennent du state de l'agent, par le montage.

## Fonction `loom_ia.tools_builtin.state._ref`

```python
def _ref(value: JsonValue) -> str
```

`result:N` d'après ce que le modèle a écrit : texte, nombre, `$ref` ou son JSON.

## Fonction `loom_ia.tools_builtin.state._description`

```python
def _description(keys: tuple[ModelKey, ...]) -> str
```

La description de l'outil : ce que font `set` et `clear`, quand les appeler, et les clés.
