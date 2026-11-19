# loom_ia.testing.bench

Module `loom_ia.testing.bench` de loom_v2 (fichier `src/loom_ia/testing/bench.py`).

Un agent au banc d'essai : sa config, des faux modèles, des faux outils (fonction O2, jalon J6.3c).

    async with Bench("loom-boot.yaml", models={"M3_MAIN": ScriptedModel(...)},
                     tools={"envoyer_email": faux_envoi}) as banc:
        result = await banc.run("relance", "Relance le client du devis D-2026-042.")
        banc.expect(result, status="completed", called=[{"name": "envoyer_email"}])

Le banc monte la config **à part**, comme une éval (`isolated`) : journal et
fichiers dans un dossier temporaire, ni collecteur, ni quotas, ni plafonds de
période ; rien n'est écrit là où la config range ses données. Le monde y suit
les règles des évals :

- un **modèle** se remplace par son identifiant (`models`), par n'importe
  quel client de modèle — `ScriptedModel` d'abord. Un modèle simulé de la
  config (`sdk: fake`) sert tel quel ; un modèle réel qui n'est pas remplacé
  est **refusé** à son premier appel, sauf `real_models=True` : un essai ne
  dépense rien par mégarde ;
- un **outil** se remplace par son nom (`tools`), par une fonction qui
  reçoit ce que l'outil aurait reçu et répond comme lui. Un outil à effets de
  bord sans faux n'est jamais exécuté : le modèle reçoit une erreur qui le
  dit. Un outil sans effets s'exécute ; rôles et sous-agents tournent ;
- une **approbation** est accordée quand rien ne part (faux, refus), refusée
  pour une exécution réelle.

`expect` reprend les contrôles d'un cas d'éval et lève `AssertionError`
avec chacun de ceux qui tombent ; `calls` dit ce que chaque outil a reçu et
rendu ; `export` écrit le journal d'un run, prêt pour `assert_replays`.

## Constantes de `loom_ia.testing.bench`

- `SIMULATED = 'fake'` : Le modèle d'un adaptateur sans réseau ni clé : il sert tel quel au banc.

## Classe `loom_ia.testing.bench.BenchError`

```python
class BenchError(Exception)
```

Banc mal monté : un faux qui ne vise aucun outil de l'agent, ou qui vise un rôle.

## Classe `loom_ia.testing.bench.RefusedModel`

```python
class RefusedModel
```

Un modèle réel que le banc n'appelle pas : son premier appel lève, en le disant.

Le run échoue en `model.auth`, son erreur dit comment autoriser l'appel ;
le moteur le note sans le crier (`by_client`) : c'est le banc qui refuse,
pas le fournisseur qui tombe.

### Méthode `loom_ia.testing.bench.RefusedModel.provider`

```python
@property
def provider(self) -> str
```

`sdk` du modèle refusé.

### Méthode `loom_ia.testing.bench.RefusedModel.stream`

```python
async def stream(self, request: ModelRequest) -> AsyncGenerator[ModelChunk]
```

Refuse l'appel.

**Lève :**

- `ModelError` : `auth`, marquée `by_client`, qui dit comment remplacer le modèle ou l'autoriser.

### Méthode `loom_ia.testing.bench.RefusedModel.aclose`

```python
async def aclose(self) -> None
```

Ne ferme rien.

## Classe `loom_ia.testing.bench.Bench`

```python
class Bench
```

Un agent de la config, monté à part, ses modèles et ses outils remplacés à la demande.

### Méthode `loom_ia.testing.bench.Bench.__init__`

```python
def __init__(self, config: LoomConfig | Path | str, *, models: Mapping[str, ModelClient] | None=None, tools: Mapping[str, Double] | None=None, register: Mapping[str, object] | None=None, environ: Mapping[str, str] | None=None, real_models: bool=False, profile: str | None=None) -> None
```

Charge la config et retient les faux.

**Arguments :**

- `config` : La config chargée, ou le chemin de l'amorce (`loom-boot.yaml`).
- `models` : Faux modèles, par identifiant de modèle de la config.
- `tools` : Faux outils, par nom d'outil : fonctions ``(arguments) -> résultat``.
- `register` : Objets Python référençables par leur nom (`Loom.register`).
- `environ` : Environnement où lire les secrets.
- `real_models` : Vrai pour autoriser les appels aux modèles réels non remplacés.
- `profile` : Profil de la config.

**Lève :**

- `ConfigError` : Un faux modèle vise un identifiant que la config ne déclare pas.

### Méthode `loom_ia.testing.bench.Bench.loom`

```python
@property
def loom(self) -> Loom
```

L'instance du banc, pour ce que le banc ne dit pas lui-même.

### Méthode `loom_ia.testing.bench.Bench.run`

```python
async def run(self, agent: str, message: str, *, tenant: TenantId | None=None, session_id: SessionId | None=None, state: Mapping[str, JsonValue] | None=None) -> RunResult
```

Lance un run et attend sa fin ; ce que ses outils ont reçu et rendu est gardé.

Chaque faux doit viser un outil de l'agent ou de ses sous-agents, jamais
un rôle : vérifié au premier run de chaque agent (`BenchError`).
`session_id` enchaîne les tours d'une conversation ; `state` est
celui que l'application pose à l'ouverture du run (`Loom.run`). Un
faux rend un résultat neuf : les sources `from` du state s'y
appliquent comme au résultat de l'outil.

**Arguments :**

- `agent` : L'agent.
- `message` : La demande.
- `tenant` : Le client.
- `session_id` : Session à poursuivre.
- `state` : State posé par l'application.

**Renvoie :**

Le résultat du run.

**Lève :**

- `BenchError` : Un faux vise un rôle, ou aucun outil de l'agent.

### Méthode `loom_ia.testing.bench.Bench.outcome`

```python
def outcome(self, result: RunResult) -> Outcome
```

Ce qu'un run du banc a rendu, et chaque appel d'outil de son arbre.

### Méthode `loom_ia.testing.bench.Bench.calls`

```python
def calls(self, name: str | None=None, result: RunResult | None=None) -> list[ToolUse]
```

Les appels d'outil des runs du banc (ou de l'un d'eux), dans l'ordre, filtrés par nom.

Chacun dit ce que l'outil a reçu (références résolues, arguments d'une
politique), ce qu'il a écrit si c'est autre chose, son sort — faussé,
refusé, exécuté — et son résultat.

**Arguments :**

- `name` : Nom de l'outil ; `None` : tous.
- `result` : Un run du banc ; `None` : tous.

### Méthode `loom_ia.testing.bench.Bench.expect`

```python
def expect(self, result: RunResult, **expect: object) -> list[CheckResult]
```

Les contrôles d'un cas d'éval sur ce run ; `AssertionError` si l'un tombe.

`status`, `contains`, `not_contains`, `matches`, `fields`,
`called` (avec une partie des arguments que l'outil a reçus),
`not_called`. Le message dit chaque contrôle tombé et ce qu'il a
trouvé à la place, et le texte du run quand un contrôle de texte tombe.
Sans `status`, le run doit finir `completed`, comme pour un cas.

**Arguments :**

- `result` : Un run du banc.
- `**expect` : Les attendus.

**Renvoie :**

Le résultat de chaque contrôle.

**Lève :**

- `AssertionError` : Un contrôle tombe.
- `ValueError` : Aucun attendu.
- `BenchError` : Run lancé hors de ce banc.

### Méthode `loom_ia.testing.bench.Bench.events`

```python
async def events(self, result: RunResult) -> list[Event]
```

Les événements du run et de ses sous-runs, dans l'ordre du journal.

### Méthode `loom_ia.testing.bench.Bench.export`

```python
async def export(self, result: RunResult, path: Path | str) -> Path
```

Écrit le journal de la session du run en JSONL — de quoi le rejouer (`assert_replays`).

**Arguments :**

- `result` : Un run du banc.
- `path` : Le fichier à écrire.

**Renvoie :**

Le chemin du fichier.
