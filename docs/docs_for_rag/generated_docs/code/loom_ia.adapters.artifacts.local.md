# loom_ia.adapters.artifacts.local

Module `loom_ia.adapters.artifacts.local` de loom_v2 (fichier `src/loom_ia/adapters/artifacts/local.py`).

Stockage d'artefacts dans un dossier local : mode librairie et dev (fonction G2).

Un fichier par artefact : `<racine>/<client>/<session>/<sha256>.<ext>`,
chemin tiré de l'URI (segments encodés, jamais « .. »). Supprimer une
session revient à supprimer son dossier.

L'URI est adressée par le contenu : un fichier déjà présent n'est pas
réécrit. L'écriture passe par un fichier temporaire renommé, pour qu'un
lecteur ne voie jamais un fichier à moitié écrit.

## Classe `loom_ia.adapters.artifacts.local.LocalArtifactStore`

```python
class LocalArtifactStore
```

Artefacts rangés sous un dossier.

### Méthode `loom_ia.adapters.artifacts.local.LocalArtifactStore.path`

```python
def path(self, uri: str) -> Path
```

Fichier d'un artefact ; lève `ValueError` si l'URI est invalide.

### Méthode `loom_ia.adapters.artifacts.local.LocalArtifactStore.put`

```python
async def put(self, uri: str, data: bytes) -> None
```

Range des octets à une URI (`ArtifactStore.put`).

Un fichier déjà présent n'est pas réécrit (l'URI est adressée par le
contenu) ; l'écriture passe par un fichier temporaire renommé. `ValueError`
si l'URI est invalide.

### Méthode `loom_ia.adapters.artifacts.local.LocalArtifactStore.get`

```python
async def get(self, uri: str) -> bytes
```

Rend les octets d'un artefact ; `ArtifactNotFound` s'il n'y est pas (`ArtifactStore.get`).

### Méthode `loom_ia.adapters.artifacts.local.LocalArtifactStore.session_path`

```python
def session_path(self, tenant_id: TenantId, session_id: SessionId) -> Path
```

Dossier d'une session ; lève `ValueError` si les segments sont invalides.

### Méthode `loom_ia.adapters.artifacts.local.LocalArtifactStore.delete`

```python
async def delete(self, tenant_id: TenantId, session_id: SessionId) -> int
```

Supprime les fichiers d'une session (RGPD) ; rend le nombre de fichiers retirés.

Le dossier de la session est supprimé.

### Méthode `loom_ia.adapters.artifacts.local.LocalArtifactStore.aclose`

```python
async def aclose(self) -> None
```

Rien à fermer.

## Fonction `loom_ia.adapters.artifacts.local._remove`

```python
def _remove(root: Path, directory: Path) -> int
```

Supprime le dossier d'une session et rend le nombre de fichiers retirés.

Défense en profondeur : après résolution, ce dossier doit être **exactement**
`<racine>/<client>/<session>`, sinon `ValueError` et rien n'est effacé.
Un client ou une session vide, un lien symbolique qui sort de la racine ou
un chemin qui remonte ne doivent jamais faire effacer le dossier d'un
client, ni la racine.
