# agent.yaml : la clé tools

La clé `tools` de `agent.yaml` et tout ce qu'elle contient.

## `tools[]` (forme `PythonTool`)

Outil Python d'un agent (`tools: - python: …`).

Référencé par un nom enregistré, un chemin `module:attr`, ou
`loom.<outil>` pour un outil fourni par loom-ia (`loom_ia.tools_builtin`).

Les champs renseignés ici remplacent ce que l'outil déclare lui-même. Un outil
fourni à paramètres (`loom.local_files`) a son propre modèle, qui ajoute ses
paramètres à ces réglages communs ; les autres n'acceptent rien de plus.

Modèle Pydantic : `loom_ia.agents.spec.PythonTool`.

- `tools[].python` (type `str` ; obligatoire ; bornes : longueur ≥ 1) : Référence de l'outil : nom enregistré (module de `imports`), `module:attr`, ou `loom.<outil>`.
- `tools[].description` (type `str | null` ; défaut : `null` ; bornes : longueur ≥ 1) : Ce que le modèle lit de l'outil, à la place de ce que l'outil en dit (comme `description` des surcharges MCP et de paquet).
- `tools[].timeout` (type `float | null` ; défaut : `null`) : Délai de l'outil, en secondes ; à défaut, `execution.tools.timeout` (30 s).
- `tools[].side_effects` (type `SideEffects | null` ; défaut : `null`) : Effets de l'outil : `none`, `reversible` ou `irreversible` ; ils décident de l'ordre des appels et de la reprise d'un appel interrompu.
- `tools[].approval` (type `Approval | null` ; défaut : `null`) : Approbation : `never`, `always` (chaque appel attend une décision humaine) ou `policy` (selon une politique `before_tool`).
- `tools[].idempotent` (type `bool | null` ; défaut : `null`) : Vrai si refaire l'appel est sans risque.
- `tools[].offload_over` (type `int | null` ; défaut : `null`) : Seuil de déport du résultat, en caractères (point 16).
- `tools[].output` (type `OutputContract | null` ; défaut : `null`) : Contrat de sortie (fonction E5) : sans réparation, un résultat non conforme revient à l'orchestrateur en erreur.
- `tools[].skill` (type `SkillText | null` ; défaut : `null`) : Mode d'emploi ajouté à la description de l'outil (fonction D14), en ligne ; avec `skill_file`, rangé ici au chargement.
- `tools[].skill_file` (type `Path | null` ; défaut : `null`) : Mode d'emploi dans un fichier relatif à `resources_dir`, lu au chargement ; exclut `skill`.

## `tools[].output`

Même forme que `output` (page « agent.yaml : la clé output »).

## `tools[]` (référence `python: loom.local_files`)

La référence `python: loom.local_files` : les clés communes d'un outil Python, et les réglages de l'outil, décrits page « Outil fourni loom.local_files ».

## `tools[]` (référence `python: loom.excel`)

La référence `python: loom.excel` : les clés communes d'un outil Python, et les réglages de l'outil, décrits page « Outil fourni loom.excel ».

## `tools[]` (référence `python: loom.word`)

La référence `python: loom.word` : les clés communes d'un outil Python, et les réglages de l'outil, décrits page « Outil fourni loom.word ».

## `tools[]` (référence `python: loom.pdf`)

La référence `python: loom.pdf` : les clés communes d'un outil Python, et les réglages de l'outil, décrits page « Outil fourni loom.pdf ».

## `tools[]` (référence `python: loom.sql`)

La référence `python: loom.sql` : les clés communes d'un outil Python, et les réglages de l'outil, décrits page « Outil fourni loom.sql ».

## `tools[]` (référence `python: loom.memory`)

La référence `python: loom.memory` : les clés communes d'un outil Python, et les réglages de l'outil, décrits page « Outil fourni loom.memory ».

## `tools[]` (référence `python: loom.state`)

La référence `python: loom.state` : les clés communes d'un outil Python, et les réglages de l'outil, décrits page « Outil fourni loom.state ».

## `tools[]` (référence `python: loom.ask`)

La référence `python: loom.ask` : les clés communes d'un outil Python, et les réglages de l'outil, décrits page « Outil fourni loom.ask ».

## `tools[]` (forme `McpTools`)

Serveur MCP référencé par un agent (point 19).

Les outils prennent le préfixe `serveur__` (ou `alias__`) ; `include`
ou `exclude` choisit ceux qui sont exposés, `tools` fixe leurs
déclarations pour cet agent.

Exemple :

```
tools:
  - mcp: crm
    alias: c
    include: [lookup_client]
    required: true
```

Modèle Pydantic : `loom_ia.agents.spec.McpTools`.

- `tools[].mcp` (type `str` ; obligatoire ; bornes : motif `^[A-Za-z0-9-]+(_[A-Za-z0-9-]+)*$`) : Nom d'un serveur déclaré dans `mcp_servers`.
- `tools[].alias` (type `str | null` ; défaut : `null` ; bornes : longueur ≤ 40, motif `^[A-Za-z0-9-]+(_[A-Za-z0-9-]+)*$`) : Préfixe plus court que le nom du serveur (40 caractères au plus).
- `tools[].include` (type `tuple[str, ...] | null` ; défaut : `null`) : Outils exposés, par leur nom sur le serveur ; exclut `exclude`.
- `tools[].exclude` (type `tuple[str, ...] | null` ; défaut : `null`) : Outils écartés ; exclut `include`.
- `tools[].required` (type `bool` ; défaut : `false`) : Sans ce serveur, le run échoue au lieu de continuer sans ses outils.
- `tools[].tools` (type `dict[str, ToolOverrides]` ; défaut : `{}`) : Surcharges par outil (`description`, `timeout`, `side_effects`, `approval`, `output`, `skill`…).

## `tools[].tools.<nom>`

Déclarations d'un outil fixées par la config ; `None` garde celle de l'outil.

Se déclarent sur la référence de l'outil dans `tools` d'un agent, ou
sous `mcp_servers[].tools.<outil>`. Un outil fourni à paramètres en
refuse certaines (`approval`, `side_effects`, `idempotent`), que
ses paramètres expriment déjà.

Modèle Pydantic : `loom_ia.core.model.tooling.ToolOverrides`.

- `tools[].tools.<nom>.description` (type `str | null` ; défaut : `null` ; bornes : longueur ≥ 1) : Ce que le modèle lit de l'outil, à la place de ce que l'outil en dit : une description écrite pour un autre usage se réécrit sans toucher à l'outil.
- `tools[].tools.<nom>.side_effects` (type `SideEffects | null` ; défaut : `null`) : `none`, `reversible` ou `irreversible`.
- `tools[].tools.<nom>.approval` (type `Approval | null` ; défaut : `null`) : `never`, `always` ou `policy`.
- `tools[].tools.<nom>.idempotent` (type `bool | null` ; défaut : `null`) : Vrai si refaire un appel ne double pas son effet.
- `tools[].tools.<nom>.timeout` (type `float | null` ; défaut : `null`) : Délai d'un appel, en secondes ; sinon celui de `execution.tools.timeout` (30 s).
- `tools[].tools.<nom>.offload_over` (type `int | null` ; défaut : `null`) : Taille d'un résultat, en caractères, au-delà de laquelle il est déporté ; sinon celle de `execution.tools.offload_over` (50 000).
- `tools[].tools.<nom>.output` (type `OutputContract | null` ; défaut : `null`) : Contrat de sortie propre à cet outil (fonction E5).
- `tools[].tools.<nom>.skill` (type `SkillText | null` ; défaut : `null`) : Mode d'emploi ajouté à la description (fonction D14), en ligne.
- `tools[].tools.<nom>.skill_file` (type `Path | null` ; défaut : `null`) : Le même, dans un fichier relatif à `resources_dir`, que le chargement lit et range dans `skill`. Exclusif de `skill`.

## `tools[].tools.<nom>.output`

Même forme que `output` (page « agent.yaml : la clé output »).

## `tools[]` (forme `SourceTools`)

Source d'outils d'un paquet installé, référencée par un agent (jalon J6.4b, fonction D12).

Comme un serveur MCP : les outils prennent le préfixe `source__` (ou
`alias__`), `include` ou `exclude` choisit ceux qui sont exposés,
`tools` fixe leurs déclarations pour cet agent.

Modèle Pydantic : `loom_ia.agents.spec.SourceTools`.

- `tools[].source` (type `str` ; obligatoire ; bornes : motif `^[A-Za-z0-9-]+(_[A-Za-z0-9-]+)*$`) : Nom d'une source déclarée dans `tool_sources`.
- `tools[].alias` (type `str | null` ; défaut : `null` ; bornes : longueur ≤ 40, motif `^[A-Za-z0-9-]+(_[A-Za-z0-9-]+)*$`) : Préfixe plus court que le nom de la source (40 caractères au plus).
- `tools[].include` (type `tuple[str, ...] | null` ; défaut : `null`) : Outils exposés ; exclut `exclude`.
- `tools[].exclude` (type `tuple[str, ...] | null` ; défaut : `null`) : Outils écartés ; exclut `include`.
- `tools[].required` (type `bool` ; défaut : `false`) : Sans cette source, le run échoue au lieu de continuer sans ses outils.
- `tools[].tools` (type `dict[str, ToolOverrides]` ; défaut : `{}`) : Surcharges par outil, comme pour un serveur MCP.

## `tools[].tools.<nom>`

Même forme que `tools[].tools.<nom>` (page « agent.yaml : la clé tools »).
