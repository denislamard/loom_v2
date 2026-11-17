# loom_ia.access.mcp_server.attachments

Module `loom_ia.access.mcp_server.attachments` de loom_v2 (fichier `src/loom_ia/access/mcp_server/attachments.py`).

Pièces jointes d'un appel MCP (fonction G1).

L'outil d'un agent reçoit ses images dans l'argument `attachments`, sous
deux formes calquées sur les contenus MCP :

- `{"type": "image", "data": "<base64>", "mimeType": "image/png"}` : les
  octets dans l'appel ;
- `{"type": "resource_link", "uri": "…"}` : un lien, vers

  - `artifact://…` : un fichier déjà rangé par loom pour le même client, par
    exemple la pièce jointe d'un appel précédent (listée dans `artifacts` du
    résultat) ;
  - `file:///…` : un fichier de la machine de loom, lu seulement sous
    `server.mcp.file_roots` (aucun dossier par défaut : refusé). C'est la
    forme utile depuis Claude Code ou Claude Desktop en local : le modèle du
    client connaît le chemin d'un fichier, pas ses octets en base64.

`name` et `mimeType` sont facultatifs dans les deux formes. Le contenu est
contrôlé ensuite par le moteur (signature, type accepté) ; la taille l'est
dès la lecture.

## Constantes de `loom_ia.access.mcp_server.attachments`

- `FILE_SCHEME = 'file://'` : Préfixe d'un lien vers un fichier local.
- `ATTACHMENTS_INPUT = {'type': 'array', 'description': 'Images jointes : en base64 (type image), ou par lien (type resource_link) vers un fic…` : Schéma du champ `attachments` d'un outil d'agent : images en base64, ou liens `artifact://` et `file:///`.

## Classe `loom_ia.access.mcp_server.attachments.AttachmentReader`

```python
class AttachmentReader
```

Lit les pièces jointes d'un appel, pour un client et des dossiers donnés.

### Méthode `loom_ia.access.mcp_server.attachments.AttachmentReader.read`

```python
async def read(self, raw: object) -> list[Attachment]
```

Pièces jointes de l'argument `attachments` (absent : aucune).

### Méthode `loom_ia.access.mcp_server.attachments.AttachmentReader._item`

```python
async def _item(self, raw: object, n: int) -> Attachment
```

Une pièce jointe : image en base64 (`image`) ou lien vers un fichier (`resource_link`).

**Lève :**

- `AttachmentError` : Objet attendu, type inconnu, ou lien refusé.

### Méthode `loom_ia.access.mcp_server.attachments.AttachmentReader._linked`

```python
async def _linked(self, uri: str) -> tuple[bytes, str]
```

Octets et nom du fichier désigné par un lien.

### Méthode `loom_ia.access.mcp_server.attachments.AttachmentReader._artifact`

```python
async def _artifact(self, uri: str) -> tuple[bytes, str]
```

Octets et nom d'un fichier rangé par loom (`artifact://…`) ; un fichier d'un autre client est introuvable, comme un fichier absent.

### Méthode `loom_ia.access.mcp_server.attachments.AttachmentReader._file`

```python
def _file(self, uri: str) -> tuple[bytes, str]
```

Fichier local sous un dossier autorisé, lu jusqu'à la taille limite.
