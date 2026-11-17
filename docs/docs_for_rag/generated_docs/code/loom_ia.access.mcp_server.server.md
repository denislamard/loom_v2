# loom_ia.access.mcp_server.server

Module `loom_ia.access.mcp_server.server` de loom_v2 (fichier `src/loom_ia/access/mcp_server/server.py`).

Serveur MCP : un agent, un outil (fonctions N3 et D4).

Chaque agent publié (`expose.mcp`) devient un outil MCP qui prend un
message, éventuellement des images (argument `attachments`, décrit dans le
module du même nom), et rend la réponse du run avec la liste de ses fichiers. Trois outils
de plus : `run_status` relit un run par son identifiant, `run_report` rend
la consommation d'un run (avec ses sous-runs) ou de toute une session, et
`cancel` arrête un run.

Résultat (fonction J3) : le texte est la réponse ; le résultat structuré y ajoute
`unverified`, l'usage et le coût, leur ventilation (`report`) et les
verdicts des juges. Une réponse gardée sans respecter son contrat ou son juge
est suivie d'un second texte qui le dit. Un run échoué rend un résultat
d'erreur (`isError`) dont le texte dit en clair ce qui l'a arrêté ; son
type (`guard.judge`, `model.auth`…) est dans `error_type`. Les juges
suivent leur `when` : l'accès MCP ne les force ni ne les retire.

Progression (fonction D4) : si le client en demande une (`progressToken`), chaque
étape visible du run — appels d'outils, fichiers rangés, sous-agents qui
démarrent et se terminent, avec leurs propres appels — lui est envoyée en
notification de progression, dans l'ordre du journal.

Approbations (points 17 et 39, jalon J4.5) : `approve` n'est **jamais** un outil MCP —
le LLM du client validerait lui-même les effets qu'il demande. Deux chemins,
selon ce que le client sait faire :

- il déclare l'`elicitation` : chaque demande part en formulaire (accorder
  ou refuser, avec un motif) et la réponse de l'humain est tranchée **dans la
  boucle** — le run ne passe pas par `PAUSED`, et le journal garde qui a
  décidé (`mcp:<client>`, faute d'identité plus précise sur stdio). Un
  formulaire ne corrige pas les arguments d'un appel : cela reste à l'API ;
- il ne la déclare pas : le run s'arrête en `PAUSED` et l'outil **rend la
  main aussitôt**, sans erreur — le texte dit ce qui attend, le résultat
  structuré porte `run_id` et `pending_approvals`. Un humain tranche
  ailleurs (API REST ou `loom approve`), et `run_status` relit le run.

L'elicitation n'existe qu'en **stdio**. Le serveur HTTP est sans état
(`stateless=True`, `http.py`) : chaque requête a sa propre session, qui ne
connaît pas les capacités que le client a déclarées à son initialisation, et la
réponse à un formulaire arriverait par une autre requête que celle qui l'attend.
En HTTP, un run qui demande une approbation se met donc toujours en pause, que
le client déclare l'elicitation ou non, et il se tranche par REST ou
`loom approve`.

Noms réservés : `run_status`, `run_report` et `cancel` sont des outils du
serveur. Un agent publié en MCP sous l'un de ces noms serait masqué par l'outil
intégré, donc injoignable : le montage du serveur le refuse en le nommant.

Le transport du jalon J1 est stdio : le client lance le process et parle sur
son entrée et sa sortie standard. Les logs de loom vont sur la sortie
d'erreur, jamais sur stdout — le protocole y passe.

    claude mcp add loom -- uv run loom mcp

Le serveur est bâti sur l'API bas niveau du SDK MCP : les outils sont
déclarés dynamiquement, puisqu'ils viennent de la configuration.

## Constantes de `loom_ia.access.mcp_server.server`

- `SERVER_NAME = 'loom'` : Nom du serveur MCP annoncé aux clients.
- `_INSTRUCTIONS = "Agents loom : ceux que la clé d'API de la requête peut lancer (outil par agent)."` : Un serveur HTTP sert tous les clients : ce qu'il publie dépend de la clé, donc ses instructions ne peuvent pas nommer des agents à l'avance.
- `CallerSource = Callable[[], Caller]` : Ce qui rend l'appelant de la requête en cours ; absent en stdio.
- `STATUS_TOOL = 'run_status'` : Nom de l'outil qui dit où en est un run.
- `REPORT_TOOL = 'run_report'` : Nom de l'outil qui rend le rapport de consommation d'un run.
- `CANCEL_TOOL = 'cancel'` : Nommé `cancel` comme le point 40 l'annonce depuis le jalon J1, et non `run_cancel` : la famille des deux autres outils de contrôle ferait pourtant la paire.
- `FormValue = str | int | float | bool | list[str] | None` : Ce qu'un formulaire d'elicitation peut rendre, par champ.
- `UNVERIFIED_NOTE = 'Réponse non vérifiée : elle a été gardée sans respecter son contrat ou son juge.'` : Note ajoutée à une réponse gardée sans respecter son contrat ou son juge.
- `FALLBACK_NOTE = 'Réponse de repli : la sortie a été refusée par son contrat ou son juge, puis remplacée par son message de repli.'` : Note ajoutée à une réponse de repli.
- `AGENT_INPUT = {'type': 'object', 'properties': {'message': {'type': 'string', 'description': "La demande adressée à l'agent"}, 'sessi…` : Schéma d'entrée de l'outil d'un agent, lu par le modèle du client MCP.
- `STATUS_INPUT = {'type': 'object', 'properties': {'run_id': {'type': 'string', 'description': 'Identifiant du run à relire'}, 'session_…` : Schéma d'entrée de `run_status`.
- `CANCEL_INPUT = {'type': 'object', 'properties': {'run_id': {'type': 'string', 'description': 'Identifiant du run à arrêter'}, 'session…` : Schéma d'entrée de `cancel`.
- `CANCEL_OUTPUT = {'type': 'object', 'properties': {'run_id': {'type': 'string'}, 'cancelled': {'type': 'boolean'}}, 'required': ['run_id…` : Schéma du résultat structuré de `cancel`.
- `REPORT_INPUT = {'type': 'object', 'properties': {'run_id': {'type': 'string', 'description': 'Run dont rendre la consommation, sous-ru…` : Schéma d'entrée de `run_report`.
- `DECISION_SCHEMA = {'type': 'object', 'properties': {'decision': {'type': 'string', 'title': 'Décision', 'description': "Autoriser l'appel…` : Ce qu'un client capable d'elicitation montre à l'humain : accorder ou refuser, et pourquoi. Un formulaire MCP n'a qu'un niveau de propriétés : corriger les arguments d'un appel n'y entre pas, et reste à l'API.
- `REPORT_OUTPUT = UsageReport.model_json_schema(mode='serialization')` : Schéma du résultat structuré de `run_report` : celui d'`UsageReport`.
- `RUN_OUTPUT = {'type': 'object', 'properties': {'run_id': {'type': 'string'}, 'session_id': {'type': 'string'}, 'agent': {'type': 'st…` : Schéma du résultat structuré de l'outil d'un agent.

## Fonction `loom_ia.access.mcp_server.server.create_server`

```python
def create_server(loom: Loom, *, name: str=SERVER_NAME, callers: CallerSource | None=None, user: str | None=None) -> Server[object, Any]
```

Serveur MCP publiant les agents d'une instance, au nom d'un appelant.

En **stdio** il n'y a pas de clé d'API, donc rien dans le protocole ne
dirait au nom de qui une requête arrive : un serveur sert **un**
utilisateur, nommé à son lancement (`user`, `loom mcp --user`). Le
client est le sien, et ses droits valent comme partout ; sans lui,
`TypeError` (décision D-1 des utilisateurs et rôles). Jamais le modèle ne choisit l'identité : aucun outil
ne prend d'utilisateur. `AccessDenied` pour un utilisateur inconnu.

En **HTTP** (jalon J5.2b), `callers` rend l'appelant de la requête en cours,
tiré de sa clé : un même serveur sert alors tous les clients, publie à
chacun ses agents, et refuse ce que sa clé ne permet pas.

Lève `ConfigError` si un agent publié en MCP porte le nom d'un des outils
du serveur (`run_status`, `run_report`, `cancel`) : l'outil intégré
le masquerait.

## Fonction `loom_ia.access.mcp_server.server._run`

```python
async def _run(server: Server[object, Any], loom: Loom, agent: str, message: str, attachments: list[Attachment], session_id: SessionId | None, tenant: TenantId, user: Actor) -> RunResult
```

Fait tourner le run ; suit sa progression et fait trancher, si le client le sait.

L'élicitation agit pour l'utilisateur, qui a lancé le run : elle ne
tranche que ce qu'il peut s'approuver, et le reste attend en pause
(§ 6.2) ; elle ne répond que s'il a `answer`. C'est le SDK qui l'applique.

## Fonction `loom_ia.access.mcp_server.server._answering`

```python
def _answering(ctx: RequestContext[Any, Any, Any]) -> Answerer | None
```

Répondeur en ligne bâti sur l'elicitation (wait_answer), si le client la déclare.

Un formulaire MCP n'a qu'un niveau de propriétés, de types simples : une
question à choix devient une liste, avec un champ libre à côté ; plusieurs
choix s'écrivent dans un texte, séparés par « ; ». Formulaire refusé ou
fermé : la question reste posée, et le run se met en pause.

## Fonction `loom_ia.access.mcp_server.server._question_form`

```python
def _question_form(pending: PendingQuestion) -> tuple[dict[str, Any], list[tuple[str | None, str]]]
```

Le formulaire d'elicitation des questions, et ses champs (choix, texte) par question.

## Fonction `loom_ia.access.mcp_server.server._elicited`

```python
def _elicited(ctx: RequestContext[Any, Any, Any]) -> Approver | None
```

Approbateur en ligne bâti sur l'elicitation, si le client la déclare (point 17).

Sans elle, rien n'est monté : le run se mettra en pause, et l'outil rendra
la main avec ce qu'il faut trancher.

## Fonction `loom_ia.access.mcp_server.server._question`

```python
def _question(asked: PendingApproval) -> str
```

Ce que l'humain lit avant de trancher : l'appel, ses arguments, le motif.

## Fonction `loom_ia.access.mcp_server.server.run_stdio`

```python
async def run_stdio(loom: Loom, *, user: str, name: str=SERVER_NAME) -> None
```

Sert l'instance au nom de `user`, sur l'entrée et la sortie standard.

## Fonction `loom_ia.access.mcp_server.server.answer`

```python
def answer(result: RunResult, *, ran: bool=True) -> types.CallToolResult
```

Ce que rend l'outil d'un agent (`ran`) ou `run_status`.

Un run échoué est une erreur de l'outil qui l'a lancé ; `run_status`,
qui ne fait que le relire, rend son état sans erreur.

## Fonction `loom_ia.access.mcp_server.server.structured`

```python
def structured(result: RunResult) -> dict[str, Any]
```

Résultat d'un run tel que l'outil MCP le rend.

## Fonction `loom_ia.access.mcp_server.server._text`

```python
def _text(result: RunResult) -> str
```

La réponse ; pour un échec, ce qui l'a arrêté ; pour une pause, ce qui attend.

## Fonction `loom_ia.access.mcp_server.server._awaiting`

```python
def _awaiting(result: RunResult) -> str
```

Ce qu'un run arrêté sur une approbation dit au client : quoi, et comment reprendre.

## Fonction `loom_ia.access.mcp_server.server._asking`

```python
def _asking(result: RunResult) -> str
```

Ce qu'un run arrêté sur une question dit au client : la question, et comment répondre.

Le client est souvent un modèle, qui lit ce texte et rappelle l'agent. Avec
plusieurs questions dans un même appel, chacune est numérotée avec son
intitulé, ses choix séparés par « ; » (un libellé contient souvent une
virgule), et la consigne demande les réponses en un seul message. Une
question seule garde son texte d'avant.

## Fonction `loom_ia.access.mcp_server.server._report`

```python
async def _report(loom: Loom, run_id: RunId | None, session_id: SessionId | None, caller: Caller) -> types.CallToolResult
```

Consommation d'un run ou d'une session : le rapport en texte et en structuré.

Comme en REST, le droit vaut pour chaque agent dont un run figure au rapport :
une clé limitée à certains agents ne lit pas la consommation des autres.

## Fonction `loom_ia.access.mcp_server.server._cancel`

```python
async def _cancel(loom: Loom, run_id: RunId, session_id: SessionId | None, caller: Caller) -> types.CallToolResult
```

Arrête un run : l'agent du run décide du droit, comme en REST.

Il faut `cancel` sur **son** agent, vérifié après avoir lu de quel agent
il est ; c'est l'utilisateur de l'appelant qui signe l'arrêt — jamais un
nom que le modèle écrirait (décision D-5 des utilisateurs et rôles). Un run déjà terminé n'est pas une
erreur : il n'y avait rien à arrêter, et la réponse le dit.

## Fonction `loom_ia.access.mcp_server.server._always`

```python
def _always(caller: Caller) -> CallerSource
```

L'appelant de toutes les requêtes d'un serveur en stdio : son utilisateur.

## Fonction `loom_ia.access.mcp_server.server._denied`

```python
def _denied(caller: Caller, message: str, permission: Permission, agent: str | None=None) -> types.CallToolResult
```

Un refus d'accès : écrit au journal d'audit (décision D-9 des utilisateurs et rôles), puis rendu comme une erreur d'outil.
