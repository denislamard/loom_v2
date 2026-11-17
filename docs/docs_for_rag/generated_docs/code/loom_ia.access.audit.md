# loom_ia.access.audit

Module `loom_ia.access.audit` de loom_v2 (fichier `src/loom_ia/access/audit.py`).

Les refus d'accès, journalisés (décision D-9 des utilisateurs et rôles, docs/loom_role_user.md § 7).

Un refus ne change rien au run — il n'y en a parfois même pas : un lancement
refusé, une clé inconnue. Il n'a donc pas sa place dans le journal de session,
qui raconte les runs, et un appelant refusé ne doit pas pouvoir y écrire. Il
reste à savoir qu'il a eu lieu : chaque refus donne **une** ligne WARNING sur
le logger `loom_ia.access.audit`, qui dit qui (utilisateur, passerelle,
clé), quoi (action, permission, agent, run, appel), par quel accès et chez quel
client, et pourquoi.

Où la ligne s'écrit :

- un refus de droits (`AccessDenied`) s'écrit là où il **quitte** `Loom`
  (`audited`) : ses méthodes qui agissent, pour tous les accès à la fois ;
  `can`, qui répond sans agir, n'écrit rien ;
- ce que `Loom` ne voit pas s'écrit à l'accès : clé absente, inconnue ou
  expirée (401), débit d'une clé (429), restriction d'une clé vérifiée avant le
  SDK, `as_user` refusé, CLI sans utilisateur. Un `AccessDenied` déjà
  écrit ne l'est pas deux fois (`AccessDenied.audited`) ;
- une décision sur tout un run (sans `call_id`) qui réussit pour une partie
  des demandes écrit un refus par demande laissée de côté.

Chaque accès pose le sien à son entrée (`through`, `entering`) : un refus
sorti du SDK dit ainsi s'il est venu par REST, MCP ou la CLI ; `sdk` sinon.
Il y pose aussi ce que la requête vise, quand il le sait — la route ou l'outil
(`action`), l'agent, le run : un refus décidé par l'accès, avant le SDK, le
dit sans que chaque contrôle ait à le répéter. Ce qu'un refus nomme lui-même
l'emporte.

Le format `json` des logs met le détail sous `audit` ; le format console
le met en fin de ligne. Une commande de la CLI qui affiche elle-même le refus
écarte la ligne de son terminal (`TOLD`) : le refus se lit une fois. Ces
logs suivent `telemetry.logging` (un niveau au-dessus de WARNING les tait).

Ce qui dure, c'est le fichier `security.audit.path` : chaque refus y ajoute
une ligne JSON — `ts` puis le détail, sans le message —, quel que soit le
niveau des logs, et quel que soit le processus qui refuse : l'écriture se fait
sous verrou `flock`. Chaque accès dit où écrire à son entrée (`through`,
`entering`, `writing_to`) ; une méthode de `Loom` le dit elle-même, pour
le SDK appelé directement. `loom retention` en efface les lignes plus
vieilles que `security.audit.retention_days` (`sweep`).

## Constantes de `loom_ia.access.audit`

- `AUDIT_LOGGER = 'loom_ia.access.audit'` : Logger des refus : à router à part (fichier, collecteur) pour en garder la trace.
- `Access = Literal['sdk', 'rest', 'mcp', 'cli']` : Accès par lequel une requête est arrivée : `sdk`, `rest`, `mcp` ou `cli`.
- `_ACCESS = ContextVar('loom_access', default='sdk')` : L'accès par lequel la requête en cours est arrivée : posé à l'entrée de chaque accès (`through`), lu par le refus qui sort de `Loom`. Le SDK appelé directement n'en pose aucun.
- `_FILE = ContextVar('loom_audit_file', default=None)` : Le fichier où la requête en cours écrit ses refus (`security.audit.path`).
- `_NOWHERE = Aim()` : Un tuple nommé ne se modifie pas : un seul défaut partagé ne risque rien.

## Classe `loom_ia.access.audit.Aim`

```python
class Aim(NamedTuple)
```

Ce que la requête en cours vise, tel que l'accès le sait à son entrée.

**Attributs :**

- `action` : La route (`POST /v1/runs/…/approve`) ou l'outil MCP.
- `agent` : Agent visé.
- `run_id` : Run visé.

## Fonction `loom_ia.access.audit.through`

```python
@contextmanager
def through(access: Access, config: LoomConfig | None=None, aim: Aim | None=None) -> Generator[None]
```

Tout refus écrit dans ce bloc dit qu'il est arrivé par `access`, et ce qu'il visait.

Avec `config`, il va aussi à son fichier d'audit.

## Fonction `loom_ia.access.audit.entering`

```python
def entering(access: Access, config: LoomConfig | None=None, aim: Aim | None=None) -> None
```

Pose l'accès de la requête en cours, pour la tâche qui la sert (une route REST).

## Fonction `loom_ia.access.audit.writing_to`

```python
def writing_to(config: LoomConfig) -> None
```

Les refus qui suivent vont au fichier de `config` : une commande, sa config lue.

## Fonction `loom_ia.access.audit.denied`

```python
def denied(reason: str, *, access: Access | None=None, action: str | None=None, user: str | None=None, via: str | None=None, key: str | None=None, permission: str | None=None, agent: str | None=None, tenant: str | None=None, run_id: str | None=None, call_id: str | None=None) -> None
```

Écrit un refus : une ligne WARNING, son détail à part, et une ligne au fichier d'audit.

Les champs absents sont omis.

Sans `access`, celui de la requête en cours (`through`), `sdk` à défaut ;
sans `action`, `agent` ou `run_id`, ce que la requête vise (`Aim`).

## Fonction `loom_ia.access.audit.refused`

```python
def refused(error: AccessDenied, *, access: Access | None=None, action: str | None=None, agent: str | None=None, tenant: str | None=None, run_id: str | None=None, call_id: str | None=None) -> None
```

Écrit un refus de droits, une seule fois quel que soit le nombre de couches traversées.

## Fonction `loom_ia.access.audit.audited`

```python
def audited[**P, R](method: Callable[P, Coroutine[Any, Any, R]]) -> Callable[P, Coroutine[Any, Any, R]]
```

Une méthode de `Loom` qui agit : un refus qui en sort est écrit, puis relevé.

## Fonction `loom_ia.access.audit.audited_stream`

```python
def audited_stream[**P, T](method: Callable[P, AsyncGenerator[T]]) -> Callable[P, AsyncGenerator[T]]
```

`audited`, pour une méthode qui rend un flux (`Loom.stream`).

## Classe `loom_ia.access.audit._Where`

```python
class _Where(NamedTuple)
```

L'agent, le client, le run et l'appel visés, tels que l'appel les nomme.

### Méthode `loom_ia.access.audit._Where.refused`

```python
def refused(self, error: AccessDenied, action: str) -> None
```

Écrit le refus au journal d'audit, avec ce que l'appel visait.

## Fonction `loom_ia.access.audit._where`

```python
def _where(signature: inspect.Signature, args: tuple[object, ...], kwargs: dict[str, object]) -> _Where
```

Ce que l'appel refusé visait, lu sur ses arguments.

## Classe `loom_ia.access.audit.Swept`

```python
class Swept(NamedTuple)
```

Ce qu'un balayage du fichier d'audit a lu, et retiré ou retirerait.

**Attributs :**

- `scanned` : Lignes lues.
- `removed` : Lignes retirées, ou qui le seraient.

## Fonction `loom_ia.access.audit.sweep`

```python
def sweep(path: Path, days: int, *, now: datetime, dry_run: bool) -> Swept
```

Retire du fichier d'audit les lignes plus vieilles que `days` jours.

Une ligne dont l'heure ne se lit pas est gardée : on n'efface pas ce qu'on ne
sait pas dater. Le fichier est réécrit en place, sous le verrou que prennent
les écritures : un refus écrit pendant le balayage n'est pas perdu.

## Fonction `loom_ia.access.audit._owned_by`

```python
@contextmanager
def _owned_by(args: tuple[object, ...]) -> Generator[None]
```

Les refus d'une méthode de `Loom` vont au fichier de sa config.

## Fonction `loom_ia.access.audit._write`

```python
def _write(path: Path, detail: Mapping[str, str]) -> None
```

Ajoute une ligne au fichier d'audit, sous verrou : plusieurs processus y écrivent.

Un fichier qu'on ne peut pas écrire ne change rien au refus, qui est déjà
décidé : il le dit en ERROR, et la ligne WARNING reste.
