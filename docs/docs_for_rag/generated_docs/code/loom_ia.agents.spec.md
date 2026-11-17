# loom_ia.agents.spec

Module `loom_ia.agents.spec` de loom_v2 (fichier `src/loom_ia/agents/spec.py`).

Définition d'un agent (fonction A9, point 50).

Sous-ensemble des jalons J1 à J3 : orchestrateur (`main`), outils Python,
serveurs MCP, rôles délégués (dont le rôle vision, qui reçoit les pièces
jointes), sous-agents, politiques (jalon J3.1), contrats de sortie (jalon
J3.2), juges (jalon J3.3) et budgets (jalon J3.4). Ce qui arrive plus tard (approbations, délai d'un
run, secours) donne une erreur qui nomme sa phase.

`main` est lui-même un rôle (fonction C6) : il partage avec les rôles délégués le
modèle, le prompt système et les réglages `llm`.

State de la session (option C) : `state` déclare les clés que la session
peut porter, chacune avec son schéma, et les outils dont un résultat la pose
(`from`) ; `main.context` dit ce que l'orchestrateur en reçoit, et le
`context` d'un rôle ce que ce rôle en reçoit (point 50 : déclaré, il est cité par
son `input_template`). Un juge ne reçoit pas le state dans cette version
(décision D11 du state de session).

Un agent se déclare dans un fichier `agents/<nom>.yaml` (`AgentSpec`) ;
`loom schema` donne le schéma JSON de ces fichiers.

## Constantes de `loom_ia.agents.spec`

- `AGENT_NAME_PATTERN = '^[A-Za-z0-9_-]{1,64}$'` : Nom d'un agent : lettres, chiffres, `_` et `-`, 64 au plus.
- `MainContextItem = Literal['session_state'] | SessionStateContext` : Ce que l'orchestrateur reçoit en plus de ses messages : le state de la session, entier (`session_state`) ou certaines de ses clés.
- `SESSION_STATE = 'session_state'` : Nom du contexte qui donne le state de la session.
- `ContextScope = Literal['run', 'session']` : Où chercher les résultats de `tool_results` : le run seul, ou aussi la session.
- `ContextName = Literal['user_input', 'caller_context', 'attachments', 'session_summary']` : Contextes simples, nommés par une chaîne.
- `ContextItem = ContextName | ToolResultsContext | LastTurnsContext` : Contexte d'un juge.
- `RoleContextItem = Literal['user_input', 'caller_context', 'attachments', 'session_summary', 'session_state'] | ToolResultsContext | LastT…` : Contexte d'un rôle : celui d'un juge, et le state de la session.  Entier (`session_state`) ou certaines de ses clés. À plat, pour que les erreurs de la config gardent des chemins courts.
- `OUTPUT_JUDGE = 'output'` : Nom d'un juge pour la réponse finale, quand il n'en déclare pas.
- `_PARAMETERIZED = parameterized_builtins()` : Outils fournis à paramètres : leur référence a son propre modèle (plus bas).  Seuls les modules légers de leurs paramètres sont importés ici.
- `_NOT_PARAMETERIZED = {'not': {'enum': list[JsonValue](sorted(_PARAMETERIZED))}} if _PARAMETERIZED else None` : Pour l'éditeur : une référence à paramètres relève de son propre modèle.
- `BUILTIN_REFERENCES = {reference: _builtin_reference(reference, model, entry) for reference, (model, entry) in _PARAMETERIZED.items()}` : Référence `loom.<outil>` → modèle de sa référence, pour les outils à paramètres (`loom.sql`, `loom.local_files`…).
- `STATE_KEY_PATTERN = '^[A-Za-z_][A-Za-z0-9_]{0,63}$'` : Nom d'une clé du state : un identifiant, que les templates citent tel quel.
- `_JSON_POINTER = re.compile('(?:/(?:[^~/]|~[01])*)*')` : Pointeur JSON (RFC 6901) : vide, ou des segments « /… » où « ~ » s'écrit « ~0 » et « / » s'écrit « ~1 ».
- `StateWriter = Literal['app', 'tools', 'model']` : Qui peut demander une écriture d'une clé (décision S1 du state v2).  L'application à l'ouverture d'un run, un outil (`from` ou `ToolOutput.state`), le modèle (`loom.state`).
- `DEFAULT_STATE_WRITERS = ('app', 'tools')` : Écrivains d'une clé qui n'en déclare pas : l'application et les outils.

## Classe `loom_ia.agents.spec.Expose`

```python
class Expose(DomainModel)
```

Points d'accès qui publient l'agent (fonctions N1 à N5).

L'API Python lance tout agent ; `expose` ne règle que REST et MCP.

**Attributs :**

- `rest` : Publié par le serveur HTTP (`/v1/agents/<nom>/…`). Défaut : vrai.
- `mcp` : Publié comme outil par le serveur MCP. Défaut : vrai.

## Classe `loom_ia.agents.spec.LlmSettings`

```python
class LlmSettings(DomainModel)
```

Réglages d'appel propres à un rôle (`llm` ; fonction B6).

**Attributs :**

- `max_tokens` : Remplace `max_tokens` du modèle.
- `params` : Paramètres du fournisseur (température, raisonnement…), fusionnés clé par clé sur `params` du modèle.

## Fonction `loom_ia.agents.spec._check_chain`

```python
def _check_chain(model: str, fallbacks: tuple[str, ...]) -> None
```

Chaîne de secours sans doublon, sans le modèle lui-même (fonction B4).

**Lève :**

- `ValueError` : Un modèle cité deux fois dans la chaîne.

## Classe `loom_ia.agents.spec.BaseRole`

```python
class BaseRole(DomainModel)
```

Ce que `main` et les rôles délégués ont en commun (fonction C6).

`system` et `system_file` s'excluent.

**Attributs :**

- `model` : Identifiant d'un modèle déclaré dans `models`.
- `fallbacks` : Modèles de secours, dans l'ordre (fonction B4, point 10) : identifiants déclarés dans `models`, sans doublon ni le modèle lui-même.
- `system` : Prompt système, en ligne ; peut citer des variables `{{ … }}`.
- `system_file` : Prompt système dans un fichier, relatif à `resources_dir` ; lu au chargement.
- `llm` : Réglages d'appel du rôle.

### Méthode `loom_ia.agents.spec.BaseRole.chain`

```python
@property
def chain(self) -> tuple[str, ...]
```

Modèle, puis ses secours.

## Fonction `loom_ia.agents.spec.system_text`

```python
def system_text(role: BaseRole) -> str
```

Prompt système d'un rôle : texte en ligne, ou fichier relu à chaque montage.

## Classe `loom_ia.agents.spec.SessionStateContext`

```python
class SessionStateContext(DomainModel)
```

Contexte `session_state` limité à certaines clés du state de la session.

S'écrit `- session_state: [client, devis]` dans `context` ; `- session_state` seul donne tout le state.

**Attributs :**

- `session_state` : Les clés reçues, déclarées dans `state` ; une au moins.

## Classe `loom_ia.agents.spec.MainRole`

```python
class MainRole(BaseRole)
```

Rôle orchestrateur : le modèle qui mène le run.

**Attributs :**

- `context` : Contexte ajouté à chaque appel de l'orchestrateur, à la fin de sa requête (décision D4 du state de session) : seulement `session_state`, entier ou par clés.

### Méthode `loom_ia.agents.spec.MainRole.state_keys`

```python
@property
def state_keys(self) -> tuple[str, ...] | None
```

Clés nommées dans `main.context` ; () pour tout le state, None sans contexte.

## Classe `loom_ia.agents.spec.ToolResultsContext`

```python
class ToolResultsContext(DomainModel)
```

Contexte `tool_results` : résultats réussis des outils nommés.

S'écrit `- tool_results: [crm_lookup]`, avec `scope` en option.

**Attributs :**

- `tool_results` : Les outils dont les résultats sont reçus ; un au moins.
- `scope` : `run` (défaut) : seulement ce que le run a obtenu. `session` : à défaut, le dernier résultat connu de la session — qui peut dater d'un tour ancien.

## Classe `loom_ia.agents.spec.LastTurnsContext`

```python
class LastTurnsContext(DomainModel)
```

Contexte `last_turns` : derniers tours de la session (un tour = un run).

S'écrit `- last_turns: 3` ; les tours sont rendus en texte.

**Attributs :**

- `last_turns` : Nombre de tours reçus.

## Classe `loom_ia.agents.spec.JudgeSpec`

```python
class JudgeSpec(DomainModel)
```

Juge d'une sortie (fonctions E3 et E6, point 21) : la réponse finale (agent) ou la sortie d'un rôle.

Chaque critère reçoit une note entre 0 et 1 ; un critère bloquant sous son
seuil fait refuser la sortie, que son auteur répare, puis `on_failure`
décide. Le juge voit la sortie, ses critères, le contexte déclaré (liste
fixe du point 12) et, pour un rôle, ses arguments.

Un juge n'est pas permis sur un outil ni sur la référence d'un sous-agent.

**Attributs :**

- `model` : Identifiant d'un modèle déclaré dans `models` ; s'il est celui qu'il évalue, un avertissement (une erreur en profil `prod`, fonction E6).
- `fallbacks` : Modèles de secours, dans l'ordre (fonction B4, point 10).
- `name` : Nom dans le journal (tirage, rôle `judge:<nom>`) ; par défaut `output` pour la réponse finale, le nom du rôle pour un rôle.
- `criteria` : Les critères (`name`, `rule`, `min_score`, `blocking`) ; un au moins.
- `context` : Contexte reçu : `user_input`, `caller_context`, `attachments`, `session_summary`, `tool_results`, `last_turns` ; pas `session_state` (décision D11 du state de session).
- `when` : Déclenchement : `sample`, `condition`, `tenants`, `profiles`.
- `repair` : Réparations demandées à l'auteur de la sortie refusée (`max_attempts`, `tools`).
- `on_failure` : Suite d'une sortie encore refusée après les réparations : `fail` (défaut), `unverified` ou `fallback`.
- `fallback_message` : Message qui remplace la sortie avec ``on_failure: fallback``.
- `llm` : Réglages d'appel du juge.
- `timeout` : Délai du juge, en secondes ; sans lui, ceux de son modèle et son retry le bornent.
- `on_error` : Erreur du juge (modèle, verdict) : `block` (défaut) fait échouer le run, `allow` laisse passer.

### Méthode `loom_ia.agents.spec.JudgeSpec.blocking`

```python
@property
def blocking(self) -> bool
```

Vrai si un critère au moins peut faire refuser la sortie.

### Méthode `loom_ia.agents.spec.JudgeSpec.wants_attachments`

```python
@property
def wants_attachments(self) -> bool
```

Vrai si le juge reçoit les pièces jointes du run (contexte `attachments`).

### Méthode `loom_ia.agents.spec.JudgeSpec.chain`

```python
@property
def chain(self) -> tuple[str, ...]
```

Modèle, puis ses secours.

## Classe `loom_ia.agents.spec.RoleSpec`

```python
class RoleSpec(BaseRole)
```

Rôle délégué (fonction C1) : l'orchestrateur l'appelle comme un outil.

Déclaré dans `roles` de l'agent. Un seul appel de modèle, avec son propre
prompt et ses réglages ; voir `engine/roles.py`.

**Attributs :**

- `name` : Nom de l'outil vu par l'orchestrateur ; `main` est réservé.
- `description` : Description vue par l'orchestrateur ; le contexte reçu y est ajouté.
- `input_schema` : Schéma JSON des arguments que l'orchestrateur fournit ; objet vide par défaut. Sans `additionalProperties`, les arguments non prévus sont refusés.
- `input_template` : Construction du message (point 12) : variables `args.<argument>` et `context.<contexte>` ; sans template, blocs de contexte puis arguments. Tout le contexte déclaré doit y être cité.
- `context` : Contexte ajouté aux arguments, pris dans une liste fixe (point 12) : `user_input`, `caller_context`, `attachments`, `session_summary`, `session_state`, `tool_results`, `last_turns`. Un rôle sans arguments ni contexte est refusé.
- `terminal` : Sortie transmise telle quelle comme réponse finale quand le rôle est appelé seul dans son tour (fonction C3, point 13).
- `timeout` : Délai de l'appel, en secondes ; sans lui, ceux du modèle et son retry le bornent.
- `output` : Contrat de sortie (fonction E5) : réparé par le modèle du rôle, puis `on_failure`.
- `judge` : Juge de la sortie (fonction E5, point 21), après le contrat : réparée par le modèle du rôle.

### Méthode `loom_ia.agents.spec.RoleSpec._check`

```python
@model_validator(mode='after')
def _check(self) -> Self
```

Nom non réservé, schéma valide, contexte sans doublon, template cohérent.

**Lève :**

- `ValueError` : Nom `main`, contexte déclaré deux fois, rôle sans arguments ni contexte, template invalide.

### Méthode `loom_ia.agents.spec.RoleSpec.declared_context`

```python
@property
def declared_context(self) -> list[str]
```

Noms du contexte déclaré, un par élément.

`tool_results.<outil>` pour chaque outil, `session_state.<clé>` pour
chaque clé nommée du state.

### Méthode `loom_ia.agents.spec.RoleSpec.wants_attachments`

```python
@property
def wants_attachments(self) -> bool
```

Vrai pour un rôle vision : il reçoit les pièces jointes du run (fonction C4).

### Méthode `loom_ia.agents.spec.RoleSpec.state_keys`

```python
@property
def state_keys(self) -> tuple[str, ...] | None
```

Clés du state que reçoit le rôle ; () pour toutes, None s'il ne le reçoit pas.

### Méthode `loom_ia.agents.spec.RoleSpec.tool_results`

```python
@property
def tool_results(self) -> tuple[str, ...]
```

Outils dont le rôle reçoit les résultats.

## Fonction `loom_ia.agents.spec._declared_context`

```python
def _declared_context(context: tuple[RoleContextItem, ...]) -> list[str]
```

Noms d'un contexte déclaré, un par élément.

`tool_results.<outil>` pour chaque outil, `session_state.<clé>` pour
chaque clé nommée du state.

## Fonction `loom_ia.agents.spec._check_input_schema`

```python
def _check_input_schema(schema: dict[str, JsonValue]) -> set[str]
```

Vérifie le schéma d'entrée et renvoie les noms de ses propriétés.

## Fonction `loom_ia.agents.spec._check_template`

```python
def _check_template(source: str, properties: set[str], declared: list[str]) -> None
```

Variables connues, et tout le contexte déclaré utilisé (point 50).

**Arguments :**

- `source` : Le template.
- `properties` : Les arguments du schéma d'entrée.
- `declared` : Le contexte déclaré.

**Lève :**

- `ValueError` : Template invalide, variable inconnue, ou contexte déclaré qu'il ne cite pas.

## Fonction `loom_ia.agents.spec._state_used`

```python
def _state_used(shown: str, rest: list[str], declared: list[str]) -> set[str]
```

Contexte déclaré qu'utilise `context.session_state`, entier ou par l'une de ses clés.

Avec des clés nommées, seules elles peuvent être citées. Avec le state
entier, ce sont celles de l'agent : `AgentSpec` les contrôle.

## Fonction `loom_ia.agents.spec.state_cited`

```python
def state_cited(source: str) -> set[str]
```

Clés du state citées par un template (`context.session_state.<clé>`).

## Fonction `loom_ia.agents.spec._reject_judge`

```python
def _reject_judge(data: object, what: str) -> None
```

Un juge porte sur la réponse finale ou sur un rôle, jamais sur un outil.

Sans ce contrôle, la clé tombait sous « Extra inputs are not permitted »,
qui ne dit pas pourquoi (essai de la fonction E5, 19/10).

## Fonction `loom_ia.agents.spec._context_items`

```python
def _context_items(data: object) -> list[object]
```

Éléments de `context` tels qu'écrits, avant validation.

## Fonction `loom_ia.agents.spec._reject_session_state`

```python
def _reject_session_state(data: object, reason: str) -> None
```

`session_state` dans un contexte qui ne le reçoit pas : refusé, et dit pourquoi.

Sans ce refus, l'élément serait lu comme un contexte inconnu, avec la liste
de ceux qu'attend un rôle, ou pire, accepté et ignoré à l'exécution.

## Fonction `loom_ia.agents.spec._only_session_state`

```python
def _only_session_state(data: object) -> None
```

`main.context` n'accepte que `session_state` : le reste est déjà dans les messages.

## Classe `loom_ia.agents.spec.PythonTool`

```python
class PythonTool(DomainModel)
```

Outil Python d'un agent (`tools: - python: …`).

Référencé par un nom enregistré, un chemin `module:attr`, ou
`loom.<outil>` pour un outil fourni par loom-ia (`loom_ia.tools_builtin`).

Les champs renseignés ici remplacent ce que l'outil déclare lui-même. Un outil
fourni à paramètres (`loom.local_files`) a son propre modèle, qui ajoute ses
paramètres à ces réglages communs ; les autres n'acceptent rien de plus.

**Attributs :**

- `python` : Référence de l'outil : nom enregistré (module de `imports`), `module:attr`, ou `loom.<outil>`.
- `description` : Ce que le modèle lit de l'outil, à la place de ce que l'outil en dit (comme `description` des surcharges MCP et de paquet).
- `timeout` : Délai de l'outil, en secondes ; à défaut, `execution.tools.timeout` (30 s).
- `side_effects` : Effets de l'outil : `none`, `reversible` ou `irreversible` ; ils décident de l'ordre des appels et de la reprise d'un appel interrompu.
- `approval` : Approbation : `never`, `always` (chaque appel attend une décision humaine) ou `policy` (selon une politique `before_tool`).
- `idempotent` : Vrai si refaire l'appel est sans risque.
- `offload_over` : Seuil de déport du résultat, en caractères (point 16).
- `output` : Contrat de sortie (fonction E5) : sans réparation, un résultat non conforme revient à l'orchestrateur en erreur.
- `skill` : Mode d'emploi ajouté à la description de l'outil (fonction D14), en ligne ; avec `skill_file`, rangé ici au chargement.
- `skill_file` : Mode d'emploi dans un fichier relatif à `resources_dir`, lu au chargement ; exclut `skill`.

### Méthode `loom_ia.agents.spec.PythonTool._builtin_without_params`

```python
@model_validator(mode='before')
@classmethod
def _builtin_without_params(cls, data: object) -> object
```

Une clé de plus sur un outil fourni sans paramètres : le dire tel quel.

Sans ce contrôle, elle tombait sous « Extra inputs are not permitted »,
qui ne dit pas que l'outil n'a pas de paramètres.

## Fonction `loom_ia.agents.spec._builtin_reference`

```python
def _builtin_reference(reference: str, params: type[BaseModel], entry: Builtin) -> type[PythonTool]
```

Le modèle de la référence d'un outil fourni à paramètres.

Ses paramètres s'ajoutent aux réglages communs, comme clés directes de la
référence, et sont validés au chargement par le modèle que déclare l'outil.
Un paramètre au nom d'un réglage commun est une faute du catalogue : elle
est levée ici, à l'import, et non chez l'utilisateur.

## Fonction `loom_ia.agents.spec.builtin_settings`

```python
def builtin_settings(declared: PythonTool, params: type[BaseModel]) -> dict[str, object]
```

Les paramètres d'une référence d'outil fourni, à valider par le modèle de l'outil.

**Arguments :**

- `declared` : La référence, telle que l'agent la déclare.
- `params` : Le modèle des paramètres de l'outil.

**Renvoie :**

Les valeurs des champs de `params` présents dans la référence.

## Classe `loom_ia.agents.spec.McpTools`

```python
class McpTools(DomainModel)
```

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

**Attributs :**

- `mcp` : Nom d'un serveur déclaré dans `mcp_servers`.
- `alias` : Préfixe plus court que le nom du serveur (40 caractères au plus).
- `include` : Outils exposés, par leur nom sur le serveur ; exclut `exclude`.
- `exclude` : Outils écartés ; exclut `include`.
- `required` : Sans ce serveur, le run échoue au lieu de continuer sans ses outils.
- `tools` : Surcharges par outil (`description`, `timeout`, `side_effects`, `approval`, `output`, `skill`…).

### Méthode `loom_ia.agents.spec.McpTools.prefix`

```python
@property
def prefix(self) -> str
```

Préfixe des outils : `alias`, sinon le nom du serveur.

### Méthode `loom_ia.agents.spec.McpTools.owns`

```python
def owns(self, tool: str) -> bool
```

Vrai si `tool` porte le préfixe de cette référence.

## Classe `loom_ia.agents.spec.SourceTools`

```python
class SourceTools(DomainModel)
```

Source d'outils d'un paquet installé, référencée par un agent (jalon J6.4b, fonction D12).

Comme un serveur MCP : les outils prennent le préfixe `source__` (ou
`alias__`), `include` ou `exclude` choisit ceux qui sont exposés,
`tools` fixe leurs déclarations pour cet agent.

**Attributs :**

- `source` : Nom d'une source déclarée dans `tool_sources`.
- `alias` : Préfixe plus court que le nom de la source (40 caractères au plus).
- `include` : Outils exposés ; exclut `exclude`.
- `exclude` : Outils écartés ; exclut `include`.
- `required` : Sans cette source, le run échoue au lieu de continuer sans ses outils.
- `tools` : Surcharges par outil, comme pour un serveur MCP.

### Méthode `loom_ia.agents.spec.SourceTools.prefix`

```python
@property
def prefix(self) -> str
```

Préfixe des outils : `alias`, sinon le nom de la source.

### Méthode `loom_ia.agents.spec.SourceTools.owns`

```python
def owns(self, tool: str) -> bool
```

Vrai si `tool` porte le préfixe de cette référence.

## Classe `loom_ia.agents.spec.SubAgentRef`

```python
class SubAgentRef(DomainModel)
```

Sous-agent (fonction C5) : un autre agent de la config, appelé comme un outil.

L'orchestrateur lui passe un seul argument, `message`. Nom et
description de l'outil : ceux de l'agent, sauf s'ils sont donnés ici.

**Attributs :**

- `agent` : Nom de l'agent appelé.
- `name` : Nom de l'outil vu par l'orchestrateur ; par défaut, celui de l'agent.
- `description` : Description de l'outil ; par défaut, celle de l'agent.
- `budget_share` : Part (entre 0 exclu et 1) de ce qui reste au budget du run appelant au moment de l'appel (fonction J4).
- `side_effects` : Effets du sous-agent, ceux des outils de son agent : `irreversible` par défaut. Ils décident de l'ordre des appels d'un même tour : déclaré `none`, il tourne en même temps que les autres appels sans effet.

### Méthode `loom_ia.agents.spec.SubAgentRef.tool_name`

```python
@property
def tool_name(self) -> str
```

Nom de l'outil vu par l'orchestrateur : `name`, sinon celui de l'agent.

## Fonction `loom_ia.agents.spec._tool_kind`

```python
def _tool_kind(value: object) -> str
```

Balise d'une référence d'outil : `mcp`, `source`, `python` ou `loom.<outil>` à paramètres.

## Classe `loom_ia.agents.spec.PolicyRef`

```python
class PolicyRef(DomainModel)
```

Politique branchée sur l'agent (points 1 et 2, conception §17.4).

`hook` : un nom enregistré (module de `imports`), un chemin
`module:attr`, ou une politique fournie (`loom.require_tool`).
`points` : ceux où l'agent la branche, parmi ceux qu'elle déclare ; par
défaut, tous. Les politiques d'un point s'exécutent dans l'ordre déclaré.

**Attributs :**

- `hook` : La politique.
- `name` : Nom dans le journal ; par défaut celui de la politique. Le préfixe `loom.` est réservé.
- `points` : Points où l'agent la branche ; par défaut, tous ceux qu'elle déclare.
- `params` : Paramètres donnés à la politique (`context.params`).
- `timeout` : Délai de la politique, en secondes (5 par défaut) ; `null` le retire.
- `on_error` : Erreur de la politique : `block` (défaut) fait échouer le run, `allow` l'ignore.
- `max_attempts` : Réparations (`Retry`) que la politique peut demander pour une sortie ; au-delà, sa décision devient `Fail`.

## Classe `loom_ia.agents.spec.StateSource`

```python
class StateSource(DomainModel)
```

Ce qui pose une clé du state (décision D12 du state de session) : chaque résultat réussi de l'outil `tool`.

`path` : pointeur JSON (RFC 6901) dans les données du résultat
(`data`) ; `""` les prend entières. `value` : valeur fixe ; `null`
efface la clé (décision D10). L'un ou l'autre, pas les deux.

`on_missing` : ce que fait un résultat où `path` ne mène à rien.
`refuse` (défaut) en fait une écriture refusée (décision D8) : le modèle la lit à la
suite du résultat, et un avertissement va au log — le signe qu'un outil dédié
a changé la forme de ses données. `ignore` ne pose rien, sans rien dire au
modèle (un message de débogage au log) : un outil générique, comme une
requête SQL, dont la plupart des résultats ne parlent pas de la clé.

**Attributs :**

- `tool` : Nom de l'outil, tel que le voit le modèle.
- `path` : Pointeur JSON dans les données du résultat.
- `value` : Valeur fixe.
- `on_missing` : `refuse` (défaut) ou `ignore`.

## Classe `loom_ia.agents.spec.StateExtract`

```python
class StateExtract(DomainModel)
```

Ce que `loom.state` tire du résultat que le modèle désigne (décision S3 du state v2).

Exactement une règle : `value` (la colonne d'une ligne), `fields` (un
objet, champ ← colonne d'une ligne) ou `columns` (un objet, champ ← liste
d'une colonne sur toutes les lignes). `label` : le champ qui reçoit le
libellé donné par le modèle, avec `fields` ou `columns`.

**Attributs :**

- `value` : Colonne dont la valeur, sur la ligne désignée, devient celle de la clé.
- `fields` : Objet construit à partir d'une ligne : champ de la clé ← colonne.
- `columns` : Objet construit à partir de toutes les lignes : champ de la clé ← liste des valeurs d'une colonne.
- `label` : Champ de l'objet qui reçoit le libellé donné par le modèle.

## Classe `loom_ia.agents.spec.StateKey`

```python
class StateKey(DomainModel)
```

Clé du state de la session (décision D6 du state de session) : son schéma, ce qu'elle désigne, ce qui la pose.

Déclarée dans `state.<clé>` de l'agent ; le nom : lettres, chiffres et
`_`, 64 au plus, sans chiffre en tête.

Exemple :

```
state:
  client_id:
    schema: {type: string}
    description: Client en cours
    from:
      - tool: crm_lookup
        path: /id
```

**Attributs :**

- `json_schema` : JSON Schema de la valeur (clé `schema`), contrôlé au chargement : une écriture qui ne le tient pas est refusée (décision D8).
- `description` : Ce que la clé désigne : donné avec sa valeur à qui la reçoit.
- `sources` : Outils dont un résultat réussi pose la clé (clé `from`, décision D12) ; chaque outil une fois. Qui peut l'écrire, avec ou sans source, c'est `writers` : avec `tools`, tout outil peut aussi la poser par `ToolOutput.state` (écart E1 du state v2).
- `writers` : Qui peut demander une écriture (décision S1 du state v2) : `app`, `tools`, `model` ; `[app, tools]` par défaut. `from` exige `tools` ; `model` exige `extract`.
- `extract` : Ce que `loom.state` tire du résultat que le modèle désigne : exigé avec `model` dans `writers`, refusé sans.
- `clears` : Clés que poser celle-ci par `loom.state` efface dans la même écriture : une nouvelle liste remplace le document dont on parlait (`clears: [document]`). Exige `extract` ; jamais une clé `required`.
- `required` : Run refusé à l'ouverture si la clé manque au state de la session (décision D13) ; exclut `default`.
- `default` : Valeur lue quand la clé est absente ; elle n'est jamais écrite (décision D13).

### Méthode `loom_ia.agents.spec.StateKey._check`

```python
@model_validator(mode='after')
def _check(self) -> Self
```

Cohérence de la clé : schéma, `required` et `default`, `writers`, `extract`, `clears`, `from`.

**Lève :**

- `ValueError` : Schéma invalide ; `required` avec `default` ; valeur par défaut ou fixe hors schéma ; écrivain cité deux fois ; `model` sans `extract` ou l'inverse ; `clears` sans `extract` ; `from` sans `tools` ; `value: null` sur une clé `required` ; outil cité deux fois.

## Fonction `loom_ia.agents.spec._conforms`

```python
def _conforms(validator: Validator, value: JsonValue, label: str) -> None
```

La valeur suit le schéma de la clé, ou le chargement s'arrête en le disant.

## Classe `loom_ia.agents.spec.ApproveAccess`

```python
class ApproveAccess(DomainModel)
```

Qui tranche les demandes d'approbation d'un agent (décision D-2 des utilisateurs et rôles, `docs/loom_role_user.md` §5.2).

Des rôles d'accès, déclarés dans `security.roles` de `loom.yaml`. Sans
liste, tout rôle qui a la permission `approve` sur l'agent convient.

**Attributs :**

- `default` : Approbateurs de toute demande de l'agent, à défaut d'une liste par outil.
- `tools` : Approbateurs par outil, sous le nom que voit le modèle (préfixe MCP compris), comme `tenants[].tools_deny` : c'est le `tool_name` de `approval.requested`.

## Classe `loom_ia.agents.spec.AgentAccess`

```python
class AgentAccess(DomainModel)
```

Ce que l'agent exige de qui agit sur lui (décisions D-2 et D-7b des utilisateurs et rôles, `docs/loom_role_user.md` §5.2).

Bloc `access` de l'agent. Il ne cite que des rôles d'accès : les
utilisateurs se déclarent dans `loom.yaml`, une fois pour tous les agents.
Une liste absente laisse décider les permissions des rôles ; une liste
présente restreint.

**Attributs :**

- `run` : Rôles qui lancent l'agent.
- `answer` : Rôles qui répondent à ses questions (`loom.ask`).
- `cancel` : Rôles qui arrêtent ses runs.
- `approve` : Qui tranche ses demandes d'approbation.
- `self_approval` : `false` interdit l'auto-approbation sur cet agent, même à un rôle qui a `self_approve` (décision D-7b). L'agent ne fait que durcir : `true` laisse décider les rôles, il n'ouvre rien.

### Méthode `loom_ia.agents.spec.AgentAccess.approvers`

```python
def approvers(self) -> Approvers
```

Qui tranche les demandes de l'agent, tel que le moteur le fige dans chacune (`docs/loom_role_user.md` §5.3).

Par outil, la liste de l'outil, à défaut celle de l'agent (`default`) ;
une politique `Pause` sur n'importe quel outil suit la même règle (§5.2).

### Méthode `loom_ia.agents.spec.AgentAccess.listed`

```python
def listed(self) -> tuple[tuple[str, tuple[str, ...]], ...]
```

Chaque liste déclarée, avec la permission qu'elle exige de ses rôles.

## Fonction `loom_ia.agents.spec._check_access_list`

```python
def _check_access_list(label: str, roles: tuple[str, ...]) -> None
```

Une liste de rôles d'accès : non vide, sans doublon, des identifiants valides.

Une liste vide ne laisserait personne faire : c'est le contraire de ce
qu'on lit, puisqu'une liste absente laisse tout rôle qui a la permission.

## Classe `loom_ia.agents.spec.AgentToolsExecution`

```python
class AgentToolsExecution(DomainModel)
```

Exécution des outils propre à l'agent ; `None` garde le réglage de la racine.

**Attributs :**

- `order` : Ordre des appels d'un même tour : `effects` ou `free` (`execution.tools.order` de loom.yaml).

## Classe `loom_ia.agents.spec.AgentExecution`

```python
class AgentExecution(DomainModel)
```

Exécution propre à l'agent (`execution`) : remplace, clé par clé, celle de loom.yaml.

**Attributs :**

- `tools` : Exécution des outils de l'agent.

## Classe `loom_ia.agents.spec.AgentSpec`

```python
class AgentSpec(DomainModel)
```

Un agent (fonction A9) : son orchestrateur, ses outils, rôles et sous-agents, ses contrôles et réglages.

Un fichier `<agents_dir>/<nom>.yaml` par agent (conception §17.4), ou un
objet passé en Python. Les noms d'outils, de rôles, de sous-agents, de
juges et les préfixes MCP sont uniques dans l'agent.

Exemple :

```
name: support
main:
  model: SONNET
  system_file: prompts/support.md
tools:
  - python: loom.fetch_page
  - mcp: crm
roles:
  - name: redacteur
    model: HAIKU
    description: Rédige la réponse au client
    context: [user_input]
    terminal: true
```

**Attributs :**

- `name` : Nom de l'agent : lettres, chiffres, `_` et `-`, 64 au plus. Un nom qui commence par `_` est réservé aux agents internes.
- `description` : Description de l'agent (sous-agent, outil MCP, `/v1/agents`).
- `expose` : Points d'accès qui le publient.
- `main` : L'orchestrateur : modèle, secours, prompt, `llm`, `context`.
- `max_iterations` : Appels de l'orchestrateur avant la réponse forcée sans outils (10 par défaut).
- `timeout` : Délai maximal d'un run, en secondes (fonction A6, backlog #006). Il borne le temps de pilotage cumulé, pas l'horloge : l'attente en file, une pause et le temps entre un plantage et sa reprise ne comptent pas. Un dépassement écrit `run.failed` avec `error_type: timeout` : c'est une clôture, que `resume` ne relance pas, même avec un délai relevé. Sans valeur, pas de délai.
- `approval` : Approbations de l'agent (point 17) : délai laissé à l'approbateur (`expires_in`), effet d'une demande périmée (`on_expiry`). Un outil les demande par sa déclaration (`approval: always`) ou par une politique ; qui les tranche, c'est `access.approve`.
- `access` : Qui lance, répond, arrête et approuve (décision D-2 des utilisateurs et rôles) : des rôles d'accès de loom.yaml.
- `output` : Contrat de la réponse finale (fonctions A7 et E1) : réparée par l'orchestrateur.
- `judge` : Juge de la réponse finale (fonction E3, point 21), après son contrat.
- `budget` : Budgets de l'agent (fonction J4 ; `run`, `session`, `on_exceed`) : surchargent ceux de la racine (`budgets`), clé par clé.
- `stream_output` : Diffusion de la réponse finale ; par défaut `after_guards` si elle est contrôlée (contrat, juge, politique `on_output`, rôle terminal sous contrat ou jugé), sinon `live` (point 11).
- `tools` : Outils Python (`python`), serveurs MCP (`mcp`) et sources d'outils (`source`), dans l'ordre de déclaration.
- `roles` : Rôles délégués.
- `subagents` : Sous-agents.
- `policies` : Politiques, dans l'ordre d'exécution à chaque point (point 2).
- `max_depth` : Profondeur maximale d'un run de cet agent pour appeler ses sous-agents : à 1, un run racine les appelle, mais un sous-run de cet agent ne le peut pas.
- `state` : State de la session (option C) : les clés qu'elle peut porter (décision D6 du state de session).
- `execution` : Exécution des outils : remplace, clé par clé, `execution.tools` de loom.yaml.

### Méthode `loom_ia.agents.spec.AgentSpec._check_state`

```python
@model_validator(mode='after')
def _check_state(self) -> Self
```

Clés du state valides, et citées seulement si déclarées (`main.context`, rôles, `loom.state`, `clears`).

**Lève :**

- `ValueError` : Nom de clé invalide, clé citée mais non déclarée, `model` dans `writers` sans l'outil `loom.state` (ou l'inverse), clé écrite par le modèle sans lui être montrée, `clears` incohérent.

### Méthode `loom_ia.agents.spec.AgentSpec._check_clears`

```python
def _check_clears(self) -> None
```

`clears` : des clés déclarées, que le modèle peut effacer, jamais `required`.

### Méthode `loom_ia.agents.spec.AgentSpec._check_model_state`

```python
def _check_model_state(self) -> None
```

Clés tenues par le modèle : `loom.state` déclaré, et les clés montrées à `main`.

### Méthode `loom_ia.agents.spec.AgentSpec._check_role_state`

```python
def _check_role_state(self, role: RoleSpec) -> None
```

Le state que reçoit un rôle : des clés que l'agent déclare, citées comme telles.

### Méthode `loom_ia.agents.spec.AgentSpec._unique_tools`

```python
@model_validator(mode='after')
def _unique_tools(self) -> Self
```

Outils, préfixes MCP et de source, rôles, sous-agents et juges uniques dans l'agent.

**Lève :**

- `ValueError` : Un nom déclaré deux fois ; le message dit comment départager (`alias`, `name`).

### Méthode `loom_ia.agents.spec.AgentSpec.judges`

```python
@property
def judges(self) -> tuple[tuple[str, RoleSpec | None, JudgeSpec], ...]
```

Juges de l'agent : nom, rôle jugé (None pour la réponse finale), définition.

### Méthode `loom_ia.agents.spec.AgentSpec.contracts`

```python
@property
def contracts(self) -> bool
```

Vrai si l'agent déclare un contrat de sortie (réponse, rôle ou outil).

### Méthode `loom_ia.agents.spec.AgentSpec.python_tools`

```python
@property
def python_tools(self) -> tuple[PythonTool, ...]
```

Outils Python de l'agent, outils fournis compris.

### Méthode `loom_ia.agents.spec.AgentSpec.mcp_tools`

```python
@property
def mcp_tools(self) -> tuple[McpTools, ...]
```

Serveurs MCP référencés par l'agent.

### Méthode `loom_ia.agents.spec.AgentSpec.source_tools`

```python
@property
def source_tools(self) -> tuple[SourceTools, ...]
```

Sources d'outils de paquets référencées par l'agent.
