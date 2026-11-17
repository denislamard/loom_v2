# loom_ia.adapters.artifacts.memory

Module `loom_ia.adapters.artifacts.memory` de loom_v2 (fichier `src/loom_ia/adapters/artifacts/memory.py`).

Stockage d'artefacts en mémoire : tests et journaux `memory`.

## Classe `loom_ia.adapters.artifacts.memory.InMemoryArtifactStore`

```python
class InMemoryArtifactStore
```

Artefacts gardés dans un dictionnaire, perdus à la fin du process.

### Méthode `loom_ia.adapters.artifacts.memory.InMemoryArtifactStore.put`

```python
async def put(self, uri: str, data: bytes) -> None
```

Range des octets à une URI (`ArtifactStore.put`). `ValueError` si l'URI est invalide.

### Méthode `loom_ia.adapters.artifacts.memory.InMemoryArtifactStore.get`

```python
async def get(self, uri: str) -> bytes
```

Rend les octets d'un artefact ; `ArtifactNotFound` s'il n'y est pas (`ArtifactStore.get`).

### Méthode `loom_ia.adapters.artifacts.memory.InMemoryArtifactStore.delete`

```python
async def delete(self, tenant_id: TenantId, session_id: SessionId) -> int
```

Supprime les fichiers d'une session (RGPD) ; rend le nombre de fichiers retirés.

### Méthode `loom_ia.adapters.artifacts.memory.InMemoryArtifactStore.aclose`

```python
async def aclose(self) -> None
```

Rien à fermer.
