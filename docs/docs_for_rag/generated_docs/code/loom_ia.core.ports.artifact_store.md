# loom_ia.core.ports.artifact_store

Module `loom_ia.core.ports.artifact_store` de loom_v2 (fichier `src/loom_ia/core/ports/artifact_store.py`).

Port du stockage d'artefacts (fonction G2, point 16).

Les fichiers (pièces jointes, images produites par les outils, gros
résultats) vivent hors du journal, qui n'en garde que l'URI. L'URI est
calculée par le noyau (`artifact_uri`) : le stockage ne fait que ranger et
rendre des octets.

Implémentations fournies (`adapters/artifacts`) : en mémoire, dossier local,
et une enveloppe qui scelle les octets avec la clé du client
(`storage.encryption`). Le stockage se déclare sous `storage.artifacts`.

## Classe `loom_ia.core.ports.artifact_store.ArtifactNotFound`

```python
class ArtifactNotFound(Unknown)
```

Aucun artefact à cette URI ; l'API REST la rend en 404.

**Arguments :**

- `uri` : L'URI demandée.

**Attributs :**

- `uri` : La même URI. Le message dit « Artefact introuvable : <uri> ».

## Classe `loom_ia.core.ports.artifact_store.ArtifactStore`

```python
class ArtifactStore(Protocol)
```

Stockage des fichiers d'un run, hors du journal, adressés par URI.

### Méthode `loom_ia.core.ports.artifact_store.ArtifactStore.put`

```python
async def put(self, uri: str, data: bytes) -> None
```

Range des octets à une URI ; réécrire le même contenu ne change rien.

**Arguments :**

- `uri` : URI calculée par le noyau (`artifact_uri`), qui contient le client et la session.
- `data` : Les octets du fichier.

### Méthode `loom_ia.core.ports.artifact_store.ArtifactStore.get`

```python
async def get(self, uri: str) -> bytes
```

Rend les octets d'un artefact.

**Arguments :**

- `uri` : URI de l'artefact.

**Renvoie :**

Ses octets, en clair.

**Lève :**

- `ArtifactNotFound` : Aucun artefact à cette URI.

### Méthode `loom_ia.core.ports.artifact_store.ArtifactStore.delete`

```python
async def delete(self, tenant_id: TenantId, session_id: SessionId) -> int
```

Supprime les fichiers d'une session (RGPD, conception §11.5).

**Arguments :**

- `tenant_id` : Client de la session.
- `session_id` : La session.

**Renvoie :**

Le nombre de fichiers supprimés.

### Méthode `loom_ia.core.ports.artifact_store.ArtifactStore.aclose`

```python
async def aclose(self) -> None
```

Libère les ressources du stockage.
