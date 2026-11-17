# loom_ia.access.cli.memory

Module `loom_ia.access.cli.memory` de loom_v2 (fichier `src/loom_ia/access/cli/memory.py`).

`loom memory` : entretenir la base d'un agent qui active `loom.memory`, hors de tout run.

    loom memory export <agent> <fichier.jsonl>    un document JSON par ligne (fichier neuf)
    loom memory import <agent> <fichier.jsonl>    réimporte un export, sans doublon
    loom memory reindex <agent>                   recalcule les passages (modèle de la config)
    loom memory eval <agent> <golden.jsonl>       recall@1, recall@5, MRR, seuil suggéré
                                                  (doc_id: null = question hors sujet)

La base est celle que l'agent monte pour le client (`--tenant`) : son
`store`, et `path_env` lu dans les secrets de ce client. Les modèles de
langage ne sont pas créés (aucune clé d'API n'est lue) ; ceux de la mémoire
servent à `import`, `reindex` et `eval`, pas à `export`. L'outil n'est
importé qu'à l'exécution de la commande.

## Constantes de `loom_ia.access.cli.memory`

- `TOOL = 'memory'` : Nom sous lequel l'agent monte l'outil `loom.memory`.
- `OK = 0` : Code de sortie : la commande a abouti.
- `FAILED = 1` : Code de sortie : la commande a échoué.
- `REFUSED = 2` : Code de sortie : la demande est refusée.

## Fonction `loom_ia.access.cli.memory.add_actions`

```python
def add_actions(memory: argparse.ArgumentParser, tenanted: Callable[[argparse.ArgumentParser], argparse.ArgumentParser], handler: Callable[[argparse.Namespace], int]) -> None
```

Les actions de la commande `memory`.

## Fonction `loom_ia.access.cli.memory.run`

```python
def run(config: LoomConfig, args: argparse.Namespace) -> int
```

Mène l'action `loom memory` demandée.

**Arguments :**

- `config` : La config de la commande.
- `args` : Arguments de la commande.

**Renvoie :**

0 ; 2 si l'agent n'active pas `loom.memory`, si l'outil refuse, si le
fichier d'export existe déjà ou si le jeu doré est vide.

## Fonction `loom_ia.access.cli.memory._run`

```python
async def _run(config: LoomConfig, args: argparse.Namespace) -> int
```

Monte l'agent sans clients de modèle de langage, et mène l'action avec son outil `loom.memory`.

Actions : `export`, `import`, `reindex` ou `eval`.

## Fonction `loom_ia.access.cli.memory._print_report`

```python
def _print_report(report: EvalReport, *, as_json: bool) -> None
```

Le rapport de `loom memory eval`, ou en JSON avec `--json`.

Une ligne par cas (rang et score), recall@1, recall@5 et MRR, puis le
`min_score` suggéré.
