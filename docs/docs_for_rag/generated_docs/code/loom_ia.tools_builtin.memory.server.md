# loom_ia.tools_builtin.memory.server

Paquet `loom_ia.tools_builtin.memory.server` de loom_v2 (fichier `src/loom_ia/tools_builtin/memory/server/__init__.py`).

`loom-mem-gpu` : une petite application à part, qui garde BGE-M3 et son reranker en VRAM.

    loom-mem-gpu [serve] [--device cuda] [--offline] [--port 8765] [--config f.yaml] …
    loom-mem-gpu serve --check [--env]
    loom-mem-gpu status  [--url http://127.0.0.1:8765] [--token-env VAR]

Chaque réglage a sa variable (`port` → `LOOM_MEM_GPU_PORT`) : une option donnée
l'emporte sur la variable, la variable sur `--config`. `serve --check --env`
écrit les réglages en lignes `Environment=` pour l'unité systemd.

Les process loom lui confient leurs calculs (`models.server` de
`loom.memory`) au lieu de charger les modèles chacun. Elle ne lit jamais
`loom.yaml` et ne voit aucune base : elle calcule des vecteurs et des scores
pour les textes qu'on lui envoie, et n'en garde rien.

Elle n'importe de loom que la mémoire (modèles, protocole, réglages des
modèles) : un contrat import-linter le vérifie. Conception :
`docs/loom-mem-gpu.md`.

Codes de sortie : 0 arrêt demandé, 1 `status` sans application prête,
2 réglages refusés ou dépendance absente, 3 port déjà pris, 4 modèles non chargés.

## Constantes de `loom_ia.tools_builtin.memory.server`

- `PROGRAM = APP` : Le nom du programme, dans ses messages et son aide.
- `EXIT_NOT_READY = 1` : Code de sortie de `status` quand l'application n'est pas prête, ou injoignable.
- `STATUS_TIMEOUT = 5.0` : Délai de la requête de `status`, en secondes.

## Fonction `loom_ia.tools_builtin.memory.server.main`

```python
def main(argv: Sequence[str] | None=None) -> int
```

Point d'entrée de `loom-mem-gpu` : `serve` (la commande par défaut), `serve --check`/`--env`, ou `status` ; rend le code de sortie.

## Fonction `loom_ia.tools_builtin.memory.server.check`

```python
def check(settings: ServiceSettings, *, environment: bool=False) -> int
```

`serve --check` : les réglages et les dépendances, sans rien charger ni ouvrir.

Avec `environment` (`--env`), les réglages en lignes `Environment=` au lieu
du message.

## Fonction `loom_ia.tools_builtin.memory.server.log_format`

```python
def log_format(environ: Mapping[str, str]) -> str
```

Le format du journal ; sous systemd (`JOURNAL_STREAM`), sans l'heure, déjà notée.

## Fonction `loom_ia.tools_builtin.memory.server._parser`

```python
def _parser() -> argparse.ArgumentParser
```

L'analyseur de la ligne de commande : `serve` et ses options (sans défaut, pour que le fichier `--config` et les variables comptent), `status`.

## Fonction `loom_ia.tools_builtin.memory.server.status`

```python
def status(url: str, token_env: str | None) -> int
```

Interroge `/v1/health` et affiche l'état ; 0 si prête, 1 sinon.
