# loom_ia.testing.replays

Module `loom_ia.testing.replays` de loom_v2 (fichier `src/loom_ia/testing/replays.py`).

Non-régression depuis les traces, dans un test (fonctions O2 et O3, jalon J6.3b).

    async def test_la_relance_se_rejoue() -> None:
        await assert_replays("loom-boot.yaml", *Path("journaux").glob("*.jsonl"))

Chaque run fini de chaque journal est rejoué **à l'identique** avec la config
donnée : le monde — modèles, outils, approbations — est servi par le journal,
personne n'est appelé, et la première requête qui diffère est dite. Un run
qui se rejoue prouve que la config produit encore exactement les mêmes
requêtes : le prompt, les outils, l'historique, les politiques n'ont pas bougé.

## Fonction `loom_ia.testing.replays.assert_replays`

```python
async def assert_replays(config: LoomConfig | Path | str, *journals: Path | str, register: Mapping[str, object] | None=None, environ: Mapping[str, str] | None=None) -> list[JournalReplay]
```

Rejoue les journaux ; `AssertionError` avec chaque écart, s'il y en a.

`config` : la config chargée, ou le chemin de son fichier. L'agent est
monté **à part**, comme pour une éval (`isolated`) : rien n'est écrit
dans le journal, les fichiers ou le magasin d'idempotence que la config
déclare. `register` rend des objets Python référençables par leur nom
(`Loom.register`) — un outil que la config ne trouve pas dans ses
`imports`. Un journal illisible, un run qui ne peut pas être rejoué ou
qui reste inachevé sont des écarts comme les autres. Sans journal, rien ne
serait éprouvé : c'est aussi un échec.

**Arguments :**

- `config` : La config chargée, ou le chemin de l'amorce (`loom-boot.yaml`).
- `*journals` : Les journaux JSONL (`loom sessions export`, `Bench.export`).
- `register` : Objets Python référençables par leur nom.
- `environ` : Environnement où lire les secrets.

**Renvoie :**

Ce que chaque journal a donné, pour qui veut en vérifier davantage.

**Lève :**

- `AssertionError` : Aucun journal, ou au moins un écart : la première requête qui diffère, run inachevé, journal illisible.
