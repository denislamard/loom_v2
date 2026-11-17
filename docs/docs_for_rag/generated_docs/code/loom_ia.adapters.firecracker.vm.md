# loom_ia.adapters.firecracker.vm

Module `loom_ia.adapters.firecracker.vm` de loom_v2 (fichier `src/loom_ia/adapters/firecracker/vm.py`).

Une microVM Firecracker construite par `make_vm.sh`, vue de l'hôte.

Portage de l'ancien client de la plateforme (`vm_client.py`, resté hors du
dépôt), dans sa version qui laisse le verrou d'instance à `run.sh` : c'est la
seule qui s'accorde avec le `run.sh` actuel, qui prend lui-même `flock -n` sur
`runtime/vm.lock` — un verrou déjà pris côté Python le lui ferait refuser.

Ce que ce module garde de la plateforme, et pourquoi :

- les chemins de sockets sont **lus** dans `vm.env`, jamais recomposés :
  sous jailer ils passent dans le chroot ;
- l'état « lancée » se lit au verrou `flock` de `run.sh`, que le noyau
  relâche à la mort du VMM quelle qu'en soit la cause ;
- la VM est lancée par `subprocess.Popen` et non par un sous-process
  asyncio : elle doit pouvoir survivre à son lanceur, et un transport asyncio
  se plaindrait de sa boucle fermée ;
- l'arrêt essaie `reboot` sur la console série, puis Ctrl+Alt+Suppr, puis
  SIGTERM et SIGKILL, et dit lequel a abouti.

Laissé de côté parce que loom n'en a pas l'usage : la découverte d'un dossier
de VMs, pause et reprise, le sens invité → hôte du vsock.

## Constantes de `loom_ia.adapters.firecracker.vm`

- `Stopped = Literal['console', 'acpi', 'sigterm', 'sigkill']` : La manière dont la VM s'est arrêtée. « console » et « acpi » laissent systemd démonter /data et vider ses caches ; les deux autres coupent net.
- `_SPAWNED = {}` : Lancements faits par ce processus, par dossier de VM : leur tube stdin est la console série, et les garder ici permet de récolter les zombies (une VM est un fils qu'on n'attend jamais). Indexés par dossier et non par le PID du verrou : un run.sh qui perd la course rouvre ce fichier avec « > » et l'efface, le PID qu'il portait est alors perdu.

## Classe `loom_ia.adapters.firecracker.vm.VmError`

```python
class VmError(RuntimeError)
```

Échec côté VM : dossier invalide, API en erreur, démarrage ou arrêt impossible.

## Classe `loom_ia.adapters.firecracker.vm.VsockRefused`

```python
class VsockRefused(VmError)
```

Personne n'écoute sur ce port vsock dans l'invité.

## Fonction `loom_ia.adapters.firecracker.vm._parse_env`

```python
def _parse_env(path: Path) -> dict[str, str]
```

Lit un `vm.env` (`CLÉ="valeur"`) sans l'exécuter.

## Classe `loom_ia.adapters.firecracker.vm.Vm`

```python
@dataclass(frozen=True, slots=True)
class Vm
```

Poignée sur un dossier de VM : `vm.env`, `run.sh`, `runtime/`.

**Attributs :**

- `name` : Nom de la VM (`VM_NAME`).
- `cid` : CID vsock de l'invité (`GUEST_CID`).
- `api_sock` : Socket de l'API de Firecracker (`API_SOCK`).
- `vsock_uds` : Socket Unix du vsock, côté hôte (`VSOCK_UDS`).
- `directory` : Dossier de la VM.
- `execd_port` : Port vsock d'execd dans l'invité, tel que vm.env le donne ; `None` s'il n'y est pas.
- `execd_src` : Dossier source d'execd retenu à la construction ; vide : image sans execd.

### Méthode `loom_ia.adapters.firecracker.vm.Vm.load`

```python
@classmethod
def load(cls, vm_dir: str | os.PathLike[str]) -> Vm
```

La VM d'un dossier, lue dans son `vm.env`.

**Lève :**

- `VmError` : `vm.env` absent, clé manquante ou valeur illisible.

### Méthode `loom_ia.adapters.firecracker.vm.Vm.lock_path`

```python
@property
def lock_path(self) -> Path
```

Verrou que tient `run.sh` ; il porte le PID du VMM.

### Méthode `loom_ia.adapters.firecracker.vm.Vm.console_log`

```python
@property
def console_log(self) -> Path
```

Journal de la console série de l'invité.

### Méthode `loom_ia.adapters.firecracker.vm.Vm.is_running`

```python
def is_running(self) -> bool
```

Le verrou de `run.sh` est-il tenu ?

Un fichier de verrou absent veut dire qu'aucun `run.sh` ne l'a
jamais ouvert : rien ne tourne, et on ne le crée pas pour le savoir.

### Méthode `loom_ia.adapters.firecracker.vm.Vm.pid`

```python
@property
def pid(self) -> int | None
```

PID inscrit par `run.sh` dans le verrou ; ne vaut que si la VM tourne.

### Méthode `loom_ia.adapters.firecracker.vm.Vm.is_alive`

```python
async def is_alive(self) -> bool
```

Un VMM répond-il sur le socket d'API, verrou tenu ou non ?

### Méthode `loom_ia.adapters.firecracker.vm.Vm.start`

```python
async def start(self, *, wait: float=15.0, console: bool=True) -> None
```

Lance la VM par `run.sh` et attend que son API réponde.

L'API répond dès que le VMM tourne, bien avant que l'invité ait
démarré : c'est `connect` avec `wait` qui attend le service.
`VmError` si elle tourne déjà, ou si un VMM répond sans tenir le
verrou (lancé hors de `run.sh` : en démarrer un second mettrait deux
écrivains sur les mêmes images).

### Méthode `loom_ia.adapters.firecracker.vm.Vm._spawn`

```python
def _spawn(self, console: bool) -> subprocess.Popen[bytes]
```

Lance `run.sh` dans une session à part : son stdin reste un tube (la console série), sa sortie va au journal de la console.

### Méthode `loom_ia.adapters.firecracker.vm.Vm._launched`

```python
def _launched(self) -> subprocess.Popen[bytes] | None
```

Le VMM vivant que ce processus a lancé pour ce dossier, s'il y en a un.

### Méthode `loom_ia.adapters.firecracker.vm.Vm._wait_ready`

```python
async def _wait_ready(self, proc: subprocess.Popen[bytes], *, wait: float) -> None
```

Attend que l'API du VMM lancé réponde, verrou tenu, `wait` secondes au plus ; `VmError` si le lancement échoue ou reste muet.

### Méthode `loom_ia.adapters.firecracker.vm.Vm.ensure_started`

```python
async def ensure_started(self, *, wait: float=15.0, console: bool=True) -> bool
```

Démarre seulement si nécessaire ; True si ce sont nous qui l'avons démarrée.

Deux lanceurs simultanés n'obtiennent pas deux VMs : le second perd le
verrou de `run.sh`, puis attend que l'API réponde.

### Méthode `loom_ia.adapters.firecracker.vm.Vm.console_shutdown`

```python
def console_shutdown(self) -> bool
```

Tape `reboot` sur la console série ; False si ce n'est pas nous qui la tenons.

`reboot` et non `poweroff` : avec `reboot=k`, le RESET de
l'invité termine le processus firecracker ; un `poweroff` figerait
l'invité sans rendre la main.

### Méthode `loom_ia.adapters.firecracker.vm.Vm.shutdown`

```python
async def shutdown(self) -> None
```

Ctrl+Alt+Suppr par l'API, que systemd traduit en extinction (x86 seulement).

### Méthode `loom_ia.adapters.firecracker.vm.Vm.stop`

```python
async def stop(self, *, grace: float=10.0, wait: float=10.0) -> Stopped | None
```

Éteint la VM et rend la manière qui a abouti ; None si rien ne tournait.

`grace` est laissé à chaque extinction propre (console, puis
Ctrl+Alt+Suppr), `wait` à chaque signal (SIGTERM, puis SIGKILL).

### Méthode `loom_ia.adapters.firecracker.vm.Vm._wait_gone`

```python
async def _wait_gone(self, wait: float) -> bool
```

Disparue = verrou libre ET API muette.

### Méthode `loom_ia.adapters.firecracker.vm.Vm.api`

```python
async def api(self, method: str, path: str, body: dict[str, object] | None=None, *, wait: float=5.0) -> object
```

Une requête à l'API de Firecracker ; le JSON de la réponse, ou None.

La réponse se lit par sa longueur et non jusqu'à EOF : Firecracker
garde la connexion ouverte, un `read()` sans borne attendrait le délai.

### Méthode `loom_ia.adapters.firecracker.vm.Vm.info`

```python
async def info(self) -> dict[str, object]
```

`GET /` : id, état (Not started, Running, Paused), version du VMM.

### Méthode `loom_ia.adapters.firecracker.vm.Vm.connect`

```python
async def connect(self, port: int, *, wait: float=0.0, retry_delay: float=0.05) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]
```

Un flux vers le service qui écoute sur `port` dans l'invité.

Firecracker attend une ligne `CONNECT <port>` et répond
`OK <port_hôte>` avant de s'effacer ; cette ligne est consommée ici.
`wait` couvre le démarrage de l'invité : tant que rien n'écoute, on
réessaie. Chaque tentative est bornée : une poignée de main sans
réponse compte comme un refus.
