# loom_ia.tools_builtin.memory.maintenance

Module `loom_ia.tools_builtin.memory.maintenance` de loom_v2 (fichier `src/loom_ia/tools_builtin/memory/maintenance.py`).

Entretien d'une base de mémoire, hors de tout run : ce que fait `loom memory`.

- `export` : un document JSON par ligne, texte intégral et métadonnées — la
  sauvegarde de référence, les vecteurs se recalculant ;
- `import_` : réimporte un export (de loom.memory ou de loom-notes, même
  format), en sautant les `doc_id` déjà présents et les contenus déjà rangés
  dans le même projet ;
- `reindex` : recalcule tous les passages avec le modèle de la config, après
  un changement de modèle ou de découpage ; interrompue, elle se relance ;
- `evaluate` : mesure la recherche sur un jeu doré (requête → document
  attendu, ou `null` pour une question hors sujet) : recall@1, recall@5, MRR
  sur les questions qui attendent un document, et un `min_score` qui ne perd
  aucune réponse attendue ; avec des questions hors sujet, celui qui les sépare
  du bruit, s'il existe.

## Fonction `loom_ia.tools_builtin.memory.maintenance.export`

```python
async def export(service: MemoryService, path: Path) -> int
```

Écrit tous les documents dans `path`, qui ne doit pas exister ; rend leur nombre.

## Classe `loom_ia.tools_builtin.memory.maintenance.Imported`

```python
class Imported(BaseModel)
```

Le bilan d'un import.

**Attributs :**

- `imported` : Documents importés.
- `skipped` : Documents sautés : déjà présents, par identifiant ou par contenu.

## Fonction `loom_ia.tools_builtin.memory.maintenance.import_`

```python
async def import_(service: MemoryService, path: Path) -> Imported
```

Réimporte un export ; un document déjà présent (identifiant ou contenu) est sauté.

## Fonction `loom_ia.tools_builtin.memory.maintenance.reindex`

```python
async def reindex(service: MemoryService) -> int
```

Recalcule les passages de tous les documents avec le modèle de la config.

## Classe `loom_ia.tools_builtin.memory.maintenance.GoldenCase`

```python
class GoldenCase(BaseModel)
```

Un cas du jeu doré : une requête, et le document qu'elle doit trouver.

`doc_id: null` : une question hors sujet, qui ne doit rien trouver. Le
champ reste obligatoire : un oubli ne devient pas une question hors sujet.

**Attributs :**

- `query` : La question posée.
- `doc_id` : Le document à trouver ; `None` : une question hors sujet.
- `title` : Informatif, pour relire le fichier.
- `project` : Filtre appliqué à la recherche, comme le ferait un agent au projet imposé.

## Classe `loom_ia.tools_builtin.memory.maintenance.CaseResult`

```python
class CaseResult(BaseModel)
```

Le résultat d'un cas du jeu doré.

**Attributs :**

- `query` : La question posée.
- `doc_id` : Le document attendu ; `None` : question hors sujet.
- `rank` : Rang du document attendu (1 = premier), `None` s'il n'est pas trouvé.
- `expected_score` : Score du document attendu, s'il est trouvé.
- `top_doc_id` : Le premier document rendu.
- `top_score` : Le score du premier résultat.

## Classe `loom_ia.tools_builtin.memory.maintenance.EvalReport`

```python
class EvalReport(BaseModel)
```

Le bilan d'une évaluation sur le jeu doré.

**Attributs :**

- `cases` : Les questions qui attendent un document : recall et MRR portent sur elles.
- `off_topic` : Les questions hors sujet (`doc_id: null`).
- `recall_at_1` : Part des cas dont le document attendu arrive premier.
- `recall_at_5` : Part des cas dont le document attendu est dans les cinq premiers.
- `mrr` : Moyenne de l'inverse du rang du document attendu (0 s'il n'est pas trouvé).
- `lowest_expected` : Plus bas score d'un document attendu et trouvé.
- `highest_off_topic` : Plus haut score obtenu par une question hors sujet.
- `separable` : Un seuil sépare-t-il les réponses attendues du hors-sujet ? `None` sans question hors sujet.
- `suggested_min_score` : Le seuil `min_score` suggéré (voir sa description).
- `noise_removed_at_suggested` : Part des résultats hors document attendu qui tomberaient sous ce seuil.
- `results` : Le résultat de chaque cas.

## Fonction `loom_ia.tools_builtin.memory.maintenance.load_golden`

```python
def load_golden(path: Path) -> list[GoldenCase]
```

Les cas du fichier JSONL, un par ligne ; `ToolError` sur une ligne illisible.

## Fonction `loom_ia.tools_builtin.memory.maintenance.evaluate`

```python
async def evaluate(service: MemoryService, cases: list[GoldenCase], k: int=10) -> EvalReport
```

Joue chaque cas sans seuil, et dit où se range le document attendu.

Une question hors sujet (`doc_id` à None) ne compte pas dans recall et
MRR : son meilleur score situe le bruit, que le seuil suggéré doit écarter.

## Fonction `loom_ia.tools_builtin.memory.maintenance._threshold`

```python
def _threshold(lowest: float | None, highest: float | None) -> tuple[bool | None, float | None]
```

Séparables ?, et le seuil suggéré, d'après la réponse attendue la plus faible et le hors-sujet le plus fort (None.

Pas de question hors sujet, ou aucun résultat).

Un passage est rendu si son score atteint `min_score` : le seuil doit être au plus
`lowest` et au-dessus de `highest`. Le milieu laisse de la marge des deux côtés.
