# loom_ia.tools.idempotent

Module `loom_ia.tools.idempotent` de loom_v2 (fichier `src/loom_ia/tools/idempotent.py`).

Outils dont l'effet n'est produit qu'une fois (points 18 et 49).

Le moteur relance un appel interrompu. Pour un outil sans effet de bord c'est
sans conséquence ; pour un outil qui envoie un courriel ou débite un compte,
c'est l'accident. `@idempotent` lui donne de quoi s'en garder : l'effet est
mémorisé sous une clé, et l'appel rejoué rend ce qui avait été produit au
lieu de le produire une seconde fois.

    @idempotent
    @tool(side_effects="irreversible")
    async def envoyer_relance(devis_id: str) -> str:
        '''Envoie la relance du devis.'''

Décorer, c'est déclarer : l'outil passe à `idempotent: true`, et le moteur
le relance sans hésiter — c'est le décorateur qui tient la promesse.

**Deux portées de clé.** Sans `key`, la clé est celle de l'appel
(`hash(run_id, call_id)`) : deux exécutions du **même** appel n'en font
qu'une. Avec `key`, l'outil fournit une clé **métier**, tirée de ses
arguments — `key=lambda a: f"relance:{a['devis']}"` — et deux appels
distincts qui demandent la même chose n'en font qu'une aussi, même depuis
deux runs ou deux conversations. Une clé métier est préfixée par le client :
deux artisans ne se partagent pas « la relance du devis D-2026-042 ». Elle
l'est aussi par le **nom de l'outil** : deux outils qui tirent la même clé de
leurs arguments (un courriel et un SMS de relance) ne se bloquent pas, et ne
se rendent pas le résultat l'un de l'autre. Renommer l'outil change donc ses
clés. Elle exige un magasin partagé et durable, ce que le chargement vérifie —
le journal d'un run ne la porterait que dans ce run.

Les traces écrites avant ce préfixe (`client:clé`, sans le nom de l'outil)
sont encore lues : si l'une existe, c'est elle qui fait foi pour cet appel —
résultat rendu, réservation respectée, réservation périmée reprise sous elle.
Sans cela, le déploiement ferait refaire tout effet dont la trace date d'avant
lui. Elles ne portent pas le nom de l'outil : pendant leur durée de vie
(24 h par défaut, ou le `ttl` de l'outil), deux outils de même clé s'y
confondent encore, comme avant. Ce repli est à retirer une rétention après le
déploiement de tous les workers.

Un appel qui rend un effet déjà mémorisé le **dit** : le moteur écrit un
`idempotency.reused` dans le journal du run, avec la clé. Sans lui, on y
lirait un appel, un résultat venu d'ailleurs, et rien qui l'explique.

Fenêtre résiduelle : l'effet est mémorisé après coup. Une interruption entre
l'effet et son enregistrement laisse la clé réservée sans résultat. Avec un
magasin partagé, cette réservation périmée se voit, et l'outil dit ce qu'il
advient (`on_unknown`) ; avec le magasin `journal`, elle ne laisse aucune
trace et l'appel rejoué refait l'effet.

Mémoriser ne fait jamais échouer un appel dont l'effet a eu lieu. Si le magasin
tombe en panne à cet instant, l'appel rend son **vrai** résultat et l'échec est
journalisé (`logger`, niveau erreur) : le présenter comme une panne ferait
relancer le modèle sous une autre clé, donc refaire l'effet. La clé reste alors
réservée sans résultat — la fenêtre résiduelle ci-dessus, ni plus ni moins. Un
résultat qui dépasse `MAX_RECORDED` caractères est mémorisé sous une forme
**réduite** : l'appel rend son vrai résultat, et un rejeu rend un résultat qui
dit que l'effet a eu lieu, que son résultat n'a pas été conservé, et de ne pas
le relancer (les références d'artefacts, elles, sont gardées).

Chaque prise de clé a son **jeton de détenteur**, opaque : le magasin le retient
avec la clé, et `complete` comme `release` ne s'appliquent que s'il
correspond. Si la réservation a expiré et qu'un autre l'a reprise, l'outil
resté en vie ne peut ni effacer ni écraser celle de son successeur. Un magasin
tiers dont ces méthodes n'ont pas de paramètre `holder` est appelé comme avant.

Si l'outil lève, la réservation est rendue : un outil qui échoue est réputé
n'avoir rien produit. C'est le contrat demandé à son auteur — produire
l'effet puis lever ferait mentir la promesse. Un délai (celui de l'outil) ou
une annulation (arrêt du run, du process) n'est pas un échec de l'outil : on
ignore où il en était, la réservation n'est **pas** rendue et expire d'elle-même,
après quoi l'effet est d'état inconnu.

## Constantes de `loom_ia.tools.idempotent`

- `DEFAULT_RESERVATION = 300.0` : Durée d'une réservation sans délai propre à l'outil : au-delà, elle est réputée abandonnée et l'état de son effet devient inconnu.
- `KeyMaker = Callable[[dict[str, JsonValue]], str]` : Clé métier d'un appel, tirée de ses arguments.
- `BUSY = 'Cet appel est déjà en cours ailleurs : ne le relance pas, attends son résultat.'` : Quelqu'un d'autre tient la clé et n'a pas fini : ce n'est pas un échec, et le modèle ne doit surtout pas en relancer un second.
- `UNKNOWN_EFFECT = "État inconnu : une exécution précédente de cet appel s'est interrompue et a peut-être produit son effet. Il n'a pas ét…` : Réservation périmée : l'exécution précédente n'a pas rendu la clé, et rien ne dit si son effet a eu lieu (point 18).
- `OVERSIZED = "Cet appel a déjà produit son effet, mais son résultat ({size} caractères) dépasse la limite de {limit} caractères mémo…` : Ce que rend un appel rejoué dont l'effet a eu lieu, mais dont le résultat était trop gros pour être mémorisé : le dire, plutôt que de laisser croire à un échec.

## Classe `loom_ia.tools.idempotent.IdempotentTool`

```python
class IdempotentTool
```

Outil dont chaque appel ne produit son effet qu'une fois.

Reste appelable comme la fonction d'origine : la mémorisation n'a lieu que
par `invoke`, qui est le chemin du moteur.

`key` : clé métier tirée des arguments ; sans elle, celle de l'appel.
`reservation` : durée de la prise de clé ; par défaut le délai de
l'outil, sinon `DEFAULT_RESERVATION`. `ttl` : durée pendant laquelle
le résultat reste mémorisé ; sans elle, celle du magasin.
`retry_unknown` : reprendre une réservation périmée et refaire l'effet,
au lieu de le signaler — à ne vouloir que si refaire est moins grave que
ne pas faire, par exemple quand l'API appelée dédoublonne elle-même.

**Arguments :**

- `tool` : L'outil, déclaré par `@tool`.
- `key` : Clé métier tirée des arguments.
- `reservation` : Durée de la prise de clé, en secondes.
- `ttl` : Durée de mémorisation du résultat, en secondes.
- `retry_unknown` : Reprendre une réservation périmée et refaire l'effet.

**Attributs :**

- `reservation` : Durée de la prise de clé, ou `None`.
- `ttl` : Durée de mémorisation, ou `None`.
- `retry_unknown` : Reprise d'une réservation périmée.

### Méthode `loom_ia.tools.idempotent.IdempotentTool.__init__`

```python
def __init__(self, tool: FunctionTool[P, R], *, key: KeyMaker | None=None, reservation: float | None=None, ttl: float | None=None, retry_unknown: bool=False) -> None
```

Enveloppe l'outil : sa déclaration passe à `idempotent: true`.

### Méthode `loom_ia.tools.idempotent.IdempotentTool.spec`

```python
@property
def spec(self) -> ToolSpec
```

Déclaration de l'outil, `idempotent` vrai et `business_key` selon `key`.

### Méthode `loom_ia.tools.idempotent.IdempotentTool.returns_data`

```python
@property
def returns_data(self) -> bool
```

Comme l'outil qu'il mémorise : faux si sa signature exclut toute donnée.

### Méthode `loom_ia.tools.idempotent.IdempotentTool.held_for`

```python
@property
def held_for(self) -> float
```

Durée d'une réservation de cet outil.

### Méthode `loom_ia.tools.idempotent.IdempotentTool.key_for`

```python
def key_for(self, arguments: dict[str, JsonValue], context: ToolContext) -> str
```

Clé sous laquelle cet appel mémorise son effet.

Une clé métier est préfixée par le client et par l'outil : elle vaut
pour lui seul, et pour cet outil seul. La clé technique porte déjà
l'identité du run, donc de son client, et celle de l'appel.

### Méthode `loom_ia.tools.idempotent.IdempotentTool.__call__`

```python
def __call__(self, *args: P.args, **kwargs: P.kwargs) -> R
```

Appelle l'outil enveloppé directement, sans clé ni registre d'idempotence.

### Méthode `loom_ia.tools.idempotent.IdempotentTool.invoke`

```python
async def invoke(self, arguments: dict[str, JsonValue], context: ToolContext) -> ToolOutput
```

L'effet mémorisé s'il a eu lieu, sinon l'appel de l'outil sous réservation de sa clé.

Hors moteur (pas de magasin), l'outil est appelé tel quel.

**Arguments :**

- `arguments` : Arguments du modèle.
- `context` : Contexte de l'appel : magasin, clé technique, client.

**Renvoie :**

Le résultat mémorisé (le moteur écrit alors `idempotency.reused`),
ou celui de l'outil.

**Lève :**

- `ToolError` : Un autre appel tient la clé et n'a pas fini (`BUSY`).
- `UnknownEffect` : Réservation périmée sans `retry_unknown` : l'effet a peut-être eu lieu.

### Méthode `loom_ia.tools.idempotent.IdempotentTool._record`

```python
async def _record(self, store: IdempotencyStore, key: str, output: ToolOutput, proof: dict[str, str], context: ToolContext) -> None
```

Mémorise l'effet qui vient d'avoir lieu, sans jamais en faire une panne.

L'effet est produit : l'appel en rend le résultat, quoi que le magasin
en dise. Un échec d'enregistrement n'est que journalisé.

## Fonction `loom_ia.tools.idempotent._proof`

```python
def _proof(store: IdempotencyStore) -> dict[str, str]
```

Le jeton de détenteur d'une prise de clé, sous la forme d'arguments du port.

Vide pour un magasin dont une des trois méthodes n'a pas de paramètre
`holder` : écrit avant le jeton, il est appelé comme avant.

## Fonction `loom_ia.tools.idempotent._reduced`

```python
def _reduced(output: ToolOutput, size: int) -> JsonValue
```

Ce qu'on mémorise d'un résultat trop gros : un constat, et ses références.

## Fonction `loom_ia.tools.idempotent.idempotent`

```python
@overload
def idempotent[**P, R](tool: FunctionTool[P, R], /) -> IdempotentTool[P, R]
```

(sans docstring)

## Fonction `loom_ia.tools.idempotent.idempotent`

```python
@overload
def idempotent[**P, R](*, key: KeyMaker | None=None, reservation: float | None=None, ttl: float | None=None, retry_unknown: bool=False) -> Callable[[FunctionTool[P, R]], IdempotentTool[P, R]]
```

(sans docstring)

## Fonction `loom_ia.tools.idempotent.idempotent`

```python
def idempotent[**P, R](tool: FunctionTool[P, R] | None=None, /, *, key: KeyMaker | None=None, reservation: float | None=None, ttl: float | None=None, retry_unknown: bool=False) -> IdempotentTool[P, R] | Callable[[FunctionTool[P, R]], IdempotentTool[P, R]]
```

Mémorise l'effet de l'outil sous sa clé, avec ou sans options.

**Arguments :**

- `tool` : L'outil, quand le décorateur s'emploie sans parenthèses.
- `key` : Clé métier tirée des arguments ; sans elle, la clé de l'appel.
- `reservation` : Durée de la prise de clé, en secondes.
- `ttl` : Durée de mémorisation du résultat, en secondes.
- `retry_unknown` : Refaire l'effet d'une réservation périmée.

**Renvoie :**

Une `IdempotentTool`, ou un décorateur qui en rend une. À placer
au-dessus de `@tool`.
