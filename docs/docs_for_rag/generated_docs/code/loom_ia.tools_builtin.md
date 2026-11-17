# loom_ia.tools_builtin

Paquet `loom_ia.tools_builtin` de loom_v2 (fichier `src/loom_ia/tools_builtin/__init__.py`).

Outils fournis par loom-ia, référencés par leur nom `loom.…` ; aucun n'est actif d'office.

Un agent en active un en le listant dans ses outils : `tools: [{python: loom.<outil>}]`.
Le modèle le voit sous son nom sans le préfixe ; les réglages de l'agent (`timeout`,
`approval`…) s'y appliquent comme à tout outil Python.

Un module par outil, au nom de l'outil. `BUILTIN_TOOLS` les recense sans les
importer : le module d'un outil, et ses dépendances, ne sont chargés que lorsqu'un
agent l'active.

Un outil peut avoir des **paramètres**, donnés sur sa référence, à côté des
réglages communs (`- python: loom.local_files` puis `folders: …`). Son
entrée (`Builtin`) nomme alors deux choses : le modèle Pydantic de ses
paramètres, dans un module léger — Pydantic et le noyau seulement —, que la
config importe pour valider la référence au chargement ; et la fabrique de
l'outil, qui reçoit les paramètres validés et n'est importée qu'au montage.
Un paramètre ne porte jamais le nom d'un réglage commun.

## Constantes de `loom_ia.tools_builtin`

- `BUILTIN_TOOLS = {'loom.current_datetime': 'loom_ia.tools_builtin.current_datetime:current_datetime', 'loom.fetch_page': 'loom_ia.tools_…` : Référence `loom.<outil>` → `module:attr` de l'outil, ou `Builtin`.

## Classe `loom_ia.tools_builtin.Builtin`

```python
@dataclass(frozen=True, slots=True)
class Builtin
```

Un outil fourni : où le trouver, et ses paramètres s'il en a.

**Attributs :**

- `target` : `module:attr` : l'outil lui-même ; avec `params`, sa fabrique (`BuiltinFactory`).
- `params` : `module:Modèle` : modèle Pydantic des paramètres, dans un module léger.
- `refused` : Réglages communs que l'outil refuse sur sa référence : ses paramètres les expriment déjà, et deux endroits pour une même décision finiraient par se contredire.
- `refused_because` : Pourquoi ils sont refusés, pour le message.
- `own_description` : Vrai si l'outil tire sa description de ses paramètres (dossiers, droits, tables) : `description` l'effacerait, et le modèle ne saurait plus où agir. Du texte s'ajoute avec `skill`.
- `extra` : L'extra de loom-ia qui apporte ses dépendances, s'il en faut un : le dire quand le montage échoue faute d'elles.

## Classe `loom_ia.tools_builtin.Protected`

```python
@dataclass(frozen=True, slots=True)
class Protected
```

Un chemin de loom qu'un outil fourni ne doit jamais exposer au modèle.

**Attributs :**

- `path` : Le chemin protégé.
- `what` : Ce que c'est, pour le message : « la config », « le journal »…
- `subtree` : Vrai pour un dossier dont aucun sous-dossier ne peut être exposé non plus ; faux pour un fichier, ou pour le dossier de la config, dont on peut exposer un sous-dossier qui ne contient rien de protégé.

## Classe `loom_ia.tools_builtin.BuiltinContext`

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class BuiltinContext
```

Ce que reçoit la fabrique d'un outil fourni, avec ses paramètres.

**Attributs :**

- `base_dir` : Dossier du fichier de config : les chemins relatifs des paramètres s'y rapportent.
- `protected` : Config, fichier d'environnement, prompts, stockage… du client monté : ce que l'outil ne doit jamais exposer.
- `secrets` : Table des secrets du client pour qui l'agent est monté, en lecture seule — celle que reçoivent les fabriques de `tool_sources` ; `None` : l'environnement.
- `protected_dsns` : Variables des DSN du stockage de loom (journal, idempotence) : une base qu'un outil fourni expose ne doit pas être celle-là.
- `state_keys` : Clés du state que le modèle tient à jour (``writers: [model]`), pour `loom.state``.
- `state_max_value_bytes` : Taille permise d'une valeur du state (`sessions.state`).

## Classe `loom_ia.tools_builtin.Grant`

```python
@dataclass(frozen=True, slots=True)
class Grant
```

Un droit accordé, pour `loom validate` : s'il attend une approbation, s'il détruit.

**Attributs :**

- `right` : Le droit.
- `approval` : Il attend une approbation.
- `destructive` : Il détruit ce qui existe.

### Méthode `loom_ia.tools_builtin.Grant.unguarded`

```python
@property
def unguarded(self) -> bool
```

Destructif et sans approbation : un choix légitime, qui doit se voir.

## Classe `loom_ia.tools_builtin.Place`

```python
@dataclass(frozen=True, slots=True)
class Place
```

Un dossier ou une base qu'expose un outil fourni : où, ce qu'on y peut, ses bornes.

Un dossier a ses droits sous la cible `""` ; une base, table par table.
`path` est montré relatif au dossier de la config quand il s'y trouve.

**Attributs :**

- `name` : Nom du dossier ou de la base, tel que le modèle le voit.
- `kind` : « sqlite », « postgres » ; vide pour un dossier.
- `path` : Chemin du dossier ou du fichier.
- `notes` : Dit après l'emplacement, sur la même ligne (variable du DSN, schéma).
- `grants` : Droits accordés, par cible : `""` pour un dossier, chaque table pour une base.
- `rights` : L'ordre où dire les droits (les colonnes, pour une base).
- `limits` : Bornes, une ligne chacune (lignes lues au plus, délai…).

## Classe `loom_ia.tools_builtin.Settings`

```python
@dataclass(frozen=True, slots=True)
class Settings
```

Ce qu'un outil fourni montre à `loom validate` ; la CLI en fait la mise en page.

`rows` : un réglage par ligne, alignés (les clés du state) ; `options` :
des réglages courts, dits sur une ligne (fuseau, recalcul…).

**Attributs :**

- `places` : Dossiers et bases exposés.
- `rows` : Réglages dits un par ligne, alignés (les clés du state).
- `options` : Réglages courts, dits sur une ligne (fuseau, recalcul…).

## Classe `loom_ia.tools_builtin.ShowsSettings`

```python
@runtime_checkable
class ShowsSettings(Protocol)
```

Outil fourni qui dit ses réglages à `loom validate`.

### Méthode `loom_ia.tools_builtin.ShowsSettings.settings`

```python
def settings(self) -> Settings
```

Les réglages à montrer.

## Classe `loom_ia.tools_builtin.NamesSecrets`

```python
@runtime_checkable
class NamesSecrets(Protocol)
```

Paramètres d'un outil fourni qui lisent des secrets (`dsn_env`…).

Un client peut rediriger ces noms vers ses variables (`secrets`), et sa
table de secrets les contient : sans cela, elle ne garderait que ce que
`loom.yaml` nomme.

### Méthode `loom_ia.tools_builtin.NamesSecrets.secret_names`

```python
def secret_names(self) -> tuple[str, ...]
```

Les noms des variables que les paramètres lisent.

## Classe `loom_ia.tools_builtin.BuiltinFactory`

```python
class BuiltinFactory(Protocol)
```

Fabrique d'un outil fourni à paramètres ; `ValueError` dit ce qui ne va pas.

### Méthode `loom_ia.tools_builtin.BuiltinFactory.__call__`

```python
def __call__(self, params: BaseModel, context: BuiltinContext, /) -> Tool
```

Fabrique l'outil, avec ses paramètres validés et le contexte du montage.

## Fonction `loom_ia.tools_builtin.is_builtin`

```python
def is_builtin(reference: str) -> bool
```

Vrai si la référence désigne un outil fourni : `loom.<outil>`.

`loom.x:y` reste un chemin d'import : celui d'un paquet à soi nommé `loom`.

## Fonction `loom_ia.tools_builtin.builtin_entry`

```python
def builtin_entry(reference: str) -> Builtin
```

L'entrée du catalogue ; `KeyError` si la référence n'y est pas.

## Fonction `loom_ia.tools_builtin.builtin_params`

```python
def builtin_params(reference: str) -> type[BaseModel] | None
```

Le modèle des paramètres de l'outil, ou `None` s'il n'en a pas.

`KeyError` s'il n'est pas au catalogue, `ImportError` si son module manque.

## Fonction `loom_ia.tools_builtin.parameterized_builtins`

```python
def parameterized_builtins() -> Mapping[str, tuple[type[BaseModel], Builtin]]
```

Les outils fournis à paramètres, et leur modèle : seuls ces modules légers sont importés.

## Fonction `loom_ia.tools_builtin.builtin_tool`

```python
def builtin_tool(reference: str, params: BaseModel | None=None, *, context: BuiltinContext | None=None) -> Tool
```

L'outil fourni `reference`, son module importé à ce moment-là.

Un outil à paramètres est construit par sa fabrique, avec `params`
(validés par son modèle) et `context`. `KeyError` s'il n'est pas au
catalogue, `ImportError` si une dépendance de son module manque,
`ValueError` si sa fabrique refuse ses paramètres.
