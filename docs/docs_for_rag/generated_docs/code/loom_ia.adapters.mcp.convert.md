# loom_ia.adapters.mcp.convert

Module `loom_ia.adapters.mcp.convert` de loom_v2 (fichier `src/loom_ia/adapters/mcp/convert.py`).

Traductions entre MCP et loom-ia : déclarations d'outils, résultats (points 15 et 19).

**Annotations → déclarations.** Les annotations d'un serveur sont des
indications, pas des garanties ; elles donnent seulement les valeurs par
défaut, que la config remplace. Sans annotation, la spec MCP considère un
outil comme modifiant (`readOnlyHint: false`) et destructeur
(`destructiveHint: true`) : il est donc traité comme irréversible.

**Schéma d'entrée.** Il est transmis tel quel, sauf son `title` racine
(`maintenantArguments` chez FastMCP) : un nom technique sans intérêt pour le
modèle, que les outils Python retirent déjà.

**Résultats.** Les blocs `text` deviennent des blocs texte, et le contenu
structuré (`structuredContent`) le `data` du résultat. Images, audio et
ressources binaires deviennent des octets (`inline_data`) que le moteur
range dans le stockage d'artefacts avant d'écrire le résultat (point 15) ; un
lien de ressource reste une mention.

## Fonction `loom_ia.adapters.mcp.convert.declared`

```python
def declared(annotations: types.ToolAnnotations | None) -> tuple[SideEffects, bool]
```

Effets de bord et idempotence annoncés par le serveur.

## Fonction `loom_ia.adapters.mcp.convert.input_schema`

```python
def input_schema(tool: types.Tool) -> dict[str, JsonValue]
```

Schéma d'entrée montré au modèle : celui du serveur, sans son `title` racine.

## Fonction `loom_ia.adapters.mcp.convert.description`

```python
def description(tool: types.Tool) -> str
```

Description montrée au modèle : celle de l'outil, sinon son titre.

## Fonction `loom_ia.adapters.mcp.convert.to_output`

```python
def to_output(result: types.CallToolResult) -> ToolOutput
```

Résultat MCP traduit en `ToolOutput`.

## Fonction `loom_ia.adapters.mcp.convert._inline`

```python
def _inline(mime: str, data: str, name: str | None) -> OutputBlock
```

Octets d'un contenu binaire, décodés du base64 ; une mention s'ils sont illisibles.
