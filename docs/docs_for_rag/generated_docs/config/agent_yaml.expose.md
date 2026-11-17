# agent.yaml : la clé expose

La clé `expose` de `agent.yaml` et tout ce qu'elle contient.

## `expose`

Points d'accès qui publient l'agent (fonctions N1 à N5).

L'API Python lance tout agent ; `expose` ne règle que REST et MCP.

Modèle Pydantic : `loom_ia.agents.spec.Expose`.

- `expose.rest` (type `bool` ; défaut : `true`) : Publié par le serveur HTTP (`/v1/agents/<nom>/…`). Défaut : vrai.
- `expose.mcp` (type `bool` ; défaut : `true`) : Publié comme outil par le serveur MCP. Défaut : vrai.
