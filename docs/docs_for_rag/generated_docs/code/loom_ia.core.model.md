# loom_ia.core.model

Paquet `loom_ia.core.model` de loom_v2 (fichier `src/loom_ia/core/model/__init__.py`).

Modèle de domaine : les types que manipulent le moteur, la config et les accès (point 6).

Tous sont des modèles Pydantic immuables qui refusent les champs inconnus
(`DomainModel`), ou des dataclasses figées. Par module :

- `messages`, `content` : messages neutres et blocs de contenu, résultat
  d'outil (`ToolOutput`) ;
- `streaming` : requête, réponse et morceaux de flux d'un appel de modèle ;
- `model_spec` : un LLM déclaré sous `models` (délais, nouvelles
  tentatives, disjoncteur, cache, capacités) ;
- `mcp_spec` : un serveur MCP déclaré sous `mcp_servers` ;
- `tooling` : définition d'un outil, déclarations de la config,
  approbations, idempotence ;
- `contract`, `judge` : contrats de sortie et juges ;
- `policy` : points d'accroche, décisions et ce que voit une politique ;
- `budget`, `usage`, `quota` : budgets, consommation et tarifs, débit ;
- `media` : pièces jointes, types de fichiers, URI d'artefacts ;
- `question` : questions posées à l'utilisateur (`loom.ask`) ;
- `run_state` : l'état d'un run et ce qu'il attend ;
- `context`, `ids`, `base` : contexte de l'appelant, identifiants, base
  commune.
