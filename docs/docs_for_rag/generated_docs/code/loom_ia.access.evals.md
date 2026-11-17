# loom_ia.access.evals

Module `loom_ia.access.evals` de loom_v2 (fichier `src/loom_ia/access/evals.py`).

Jouer une suite d'évals : chaque variante montée à part, en mémoire (fonction O1, jalon J6.3a).

`evaluate` est ce que font `Loom.evaluate` et `loom eval`. Pour chaque
variante :

1. sa config — celle de la suite, une autre, ou d'autres modèles par étape
   (`swap_models`, comme la variante du rejeu) — passe par `isolated` :
   journal, fichiers, file et bus en mémoire, ni collecteur, ni quotas, ni
   plafonds de période ;
2. une instance `Loom` la monte, ses outils interceptés par `EvalTools` :
   un outil à effets de bord n'est jamais exécuté, sa doublure répond ;
3. chaque cas y est joué `repeat` fois, chaque run dans sa propre session ;
   ses contrôles sont évalués, puis le juge d'éval note ses critères.

Un cas de rejeu (jalon J6.3b) rejoue à l'identique, sur l'instance de la variante,
chaque run de ses journaux — lus **avant** le premier run de l'éval : ceux
qu'elle exporte ne seront rejoués qu'à la suivante.

Le plafond `max_cost_usd` se vérifie **avant** chaque run : un run en cours
n'est pas coupé, les suivants ne partent pas ; un rejeu ne dépense rien et
n'y est pas soumis. Rien de ce qu'une éval écrit ne survit à l'instance de la
variante, sauf ce que garde `export`.

## Constantes de `loom_ia.access.evals`

- `CAP_REACHED = 'plafond de dépense atteint ({spent:.4f} $ sur {cap:.4f} $)'` : Motif d'un run non joué parce que le plafond de dépense de la suite est atteint.

## Fonction `loom_ia.access.evals.evaluate`

```python
async def evaluate(suite: EvalSuite, *, config: LoomConfig | None=None, environ: Mapping[str, str] | None=None, profile: str | None=None, cases: Sequence[str]=(), variants: Sequence[str]=(), export: Path | None=None, registry: Registry | None=None, secrets: SecretProvider | None=None, models: Mapping[str, ModelClient] | None=None) -> EvalReport
```

Joue la suite et rend son rapport ; `EvalError` ou `ConfigError` si elle est injouable.

`config` sert quand la suite n'en désigne pas. `cases` et
`variants` restreignent ce qui est joué, par nom. `export` reçoit le
journal de chaque run, `<variante>--<cas>--<n>.jsonl`. `registry` :
les objets enregistrés d'une instance (`Loom.register`) — outils et
doublures que la config ne déclare pas ; il sert aux variantes qui
gardent la config de la suite, une variante qui a la sienne a le sien.

`secrets` : le fournisseur de secrets d'une instance, pour les instances
montées ici et pour le juge ; sans lui, `environ`. `models` : ses
clients de modèle fournis, par identifiant de la config — ils servent à la
place de ceux que la config déclare, au juge comme aux variantes, et ne
sont pas fermés ici. Ils n'ont de sens que pour `config` : une suite ou
une variante qui désigne sa propre config ne les reçoit pas.

## Fonction `loom_ia.access.evals._doubles`

```python
def _doubles(suite: EvalSuite, base: LoomConfig, registry: Registry | None) -> dict[str, Double]
```

Les doublures de la suite, résolues.

Chacune est un nom enregistré, ou `module:fonction` relatif à la suite.

## Fonction `loom_ia.access.evals._judge`

```python
def _judge(suite: EvalSuite, base: LoomConfig, environ: Mapping[str, str] | None, secrets: SecretProvider | None, provided: Mapping[str, ModelClient] | None) -> EvalJudgeClient | None
```

Le juge d'éval, un modèle de la config de la suite : le même pour toutes les variantes.

Un client fourni pour ce modèle sert tel quel, et reste à qui l'a fourni. Sinon
le juge lit sa clé dans les secrets de l'instance, ceux de la racine, ou dans
`environ` à défaut.

## Fonction `loom_ia.access.evals._check_launch`

```python
def _check_launch(config: LoomConfig, suite: EvalSuite, variant: EvalVariant, chosen: set[str]) -> None
```

Vérifie que l'agent existe dans la config de la variante, et que chaque client des cas joués peut le lancer.

## Fonction `loom_ia.access.evals._check_tools`

```python
def _check_tools(loom: Loom, suite: EvalSuite, variant: EvalVariant, doubles: Mapping[str, Double], chosen: set[str]) -> None
```

Doublures et outils que voit le juge : des outils de l'agent ou de son arbre.

## Fonction `loom_ia.access.evals._play`

```python
async def _play(loom: Loom, suite: EvalSuite, case: EvalCase, variant: EvalVariant, attempt: int, tools: EvalTools, judge: EvalJudgeClient | None, done: Sequence[EvalRun], export: Path | None) -> EvalRun
```

Un cas, joué une fois : le run, ses contrôles, puis ses critères.

## Fonction `loom_ia.access.evals._journals`

```python
async def _journals(suite: EvalSuite, chosen: set[str]) -> dict[str, _Loaded]
```

Les journaux des cas de rejeu, lus avant le premier run de l'éval.

Lus d'avance, ils sont rejoués tels qu'ils étaient : ce qu'exporte cette
même éval, dans le même dossier, ne le sera qu'à la suivante. Un fichier
illisible fait tomber son cas, pas l'éval.

## Fonction `loom_ia.access.evals._replayed`

```python
async def _replayed(loom: Loom, suite: EvalSuite, case: EvalCase, variant: EvalVariant, loaded: _Loaded) -> list[EvalRun]
```

Un cas de rejeu : chaque run de ses journaux, rejoué à l'identique sur cette variante.

## Fonction `loom_ia.access.evals.run_outcome`

```python
async def run_outcome(result: RunResult, events: Sequence[Event], tools: EvalTools, artifacts: ArtifactStore, *, state: Mapping[str, JsonValue] | None=None) -> Outcome
```

Ce que le run a rendu, et les appels d'outil de son arbre : reçu, sort et résultat.

`state` : le state de la session après le run (`Loom.session_state`).

## Fonction `loom_ia.access.evals.received_arguments`

```python
async def received_arguments(events: Sequence[Event], artifacts: ArtifactStore | None=None) -> dict[tuple[str, str], dict[str, JsonValue]]
```

Ce que chaque outil de l'arbre a reçu, par run et `call_id` (décision du 06/10).

Comme l'exécuteur : les références `$ref` résolues sur les résultats du
run (`artifacts` relit un résultat déporté), puis les arguments qu'une
politique `before_tool` a mis à la place, la dernière l'emportant, puis
ceux qu'un approbateur a corrigés en accordant : sa décision vient après
les politiques, et c'est elle qui part. Une référence qui ne se résout plus
(contenu déporté introuvable) laisse les arguments tels qu'écrits.

## Fonction `loom_ia.access.evals._export`

```python
async def _export(loom: Loom, result: RunResult, tenant: TenantId, path: Path) -> None
```

Le journal de la session du run, tel que `loom sessions export` l'écrirait.
