# loom_ia.tools_builtin.pdf.worker

Module `loom_ia.tools_builtin.pdf.worker` de loom_v2 (fichier `src/loom_ia/tools_builtin/pdf/worker.py`).

Lecture d'un PDF dans un process à part, borné en mémoire et en temps.

Lancé par `reader` — `python -I worker.py '<demande JSON>'` — avec les
octets du PDF sur l'entrée standard ; il écrit le résultat en JSON sur la
sortie standard. Un PDF vient de n'importe où, et pypdf a connu des boucles
sans fin et des explosions de mémoire sur des fichiers forgés : un fil
d'exécution ne s'interrompt pas, un process si. Avant d'importer pypdf, le
process se borne lui-même (mémoire adressable, temps CPU, aucun fichier
écrit) ; le parent le tue à l'échéance.

Autonome : la bibliothèque standard et pypdf seulement, rien de loom — il est
lancé par son chemin, sans importer le paquet ni ses dépendances.

## Constantes de `loom_ia.tools_builtin.pdf.worker`

- `_CONTROL = re.compile('[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')` : Ce qu'aucun texte n'affiche : des glyphes sans correspondance (puces…), du bruit.

## Fonction `loom_ia.tools_builtin.pdf.worker._limit`

```python
def _limit(memory: int, seconds: int) -> None
```

Les bornes du process, posées avant toute lecture du fichier.

## Fonction `loom_ia.tools_builtin.pdf.worker._fields`

```python
def _fields(reader: Any, limit: int) -> dict[str, str] | None
```

Les champs d'un formulaire et leur valeur ; `None` sans formulaire.

## Fonction `loom_ia.tools_builtin.pdf.worker.read`

```python
def read(data: bytes, first: int, last: int | None, budget: int) -> dict[str, Any]
```

Lit le texte des pages `first` à `last` d'un PDF, dans la limite de `budget` caractères ; un PDF protégé ou illisible donne une erreur.

## Fonction `loom_ia.tools_builtin.pdf.worker.main`

```python
def main() -> int
```

Point d'entrée du process de lecture : bornes de mémoire et de temps posées, PDF lu sur l'entrée standard, résultat en JSON sur la sortie.
