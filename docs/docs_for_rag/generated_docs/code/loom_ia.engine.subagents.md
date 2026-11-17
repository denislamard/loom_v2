# loom_ia.engine.subagents

Module `loom_ia.engine.subagents` de loom_v2 (fichier `src/loom_ia/engine/subagents.py`).

Sous-agents : un agent appelé comme un outil, dans un run enfant (fonction C5, point 4).

L'orchestrateur appelle un sous-agent avec un seul argument, `message` : la
demande qu'il lui adresse, comme à n'importe quel agent. Le sous-agent tourne
avec sa propre boucle, ses outils, ses modèles et ses limites, dans un **run
enfant** :

- même journal que le parent (même session), par le même écrivain ;
- `root_run_id` du parent, `parent_run_id`, `parent_call_id`, et une
  profondeur de plus ; contexte de l'appelant hérité ;
- ni l'historique de la session ni celui du parent : seulement le message.

Seule la réponse finale de l'enfant revient au parent, comme résultat
d'outil ; un échec de l'enfant devient un résultat d'erreur. Sa consommation
(tout son run) s'ajoute à celle du parent.

Reprise : l'identifiant de l'enfant est choisi avant l'appel et écrit dans le
`tool.called`. Si le parent est interrompu, la reprise retrouve l'enfant et
le fait avancer au lieu d'en lancer un autre ; s'il avait fini, sa réponse
est reprise telle quelle.

Profondeur : un sous-agent n'est proposé que si la profondeur du run appelant
est inférieure au `max_depth` de son agent. Au-delà, il est masqué ; une
chaîne d'agents qui s'appellent en boucle reste donc bornée.

Annulation : l'enfant tourne dans la tâche de l'appel ; annuler le parent
l'annule aussi. Rien n'est écrit : parent et enfant restent reprenables.
Clore le parent — délai dépassé, `cancel`, échec d'une politique, d'un modèle
ou d'un outil — est autre chose : plus rien ne reprendra l'enfant, qui est alors
annulé avec lui (`run.cancelled`, `reason: parent`), avant le parent et dans
la même écriture, au lieu de rester ouvert au journal.

Config : `agents.<nom>.subagents` de l'agent appelant (nom de l'outil,
agent, description, `budget_share`) et son `max_depth`.

## Constantes de `loom_ia.engine.subagents`

- `AGENT_HINT = 'Sous-agent : il ne reçoit que `message`, sans la conversation ni les résultats précédents ; mets-y tout ce dont il a b…` : Mention ajoutée à la description d'un sous-agent, montrée au modèle.
- `MESSAGE_SCHEMA = {'type': 'object', 'properties': {'message': {'type': 'string', 'minLength': 1, 'description': 'La demande adressée au …` : Schéma des arguments d'un sous-agent : un seul champ, `message`, chaîne non vide.
- `AgentResolver = Callable[[str], RunContext]` : Contexte d'exécution d'un agent de la config, par son nom.
- `_WAITING = frozenset({RunStatus.PAUSED, RunStatus.WAITING_CHILD})` : États dans lesquels un enfant attend quelqu'un : son parent attend avec lui.

## Classe `loom_ia.engine.subagents.SubAgentDefinition`

```python
@dataclass(frozen=True, kw_only=True)
class SubAgentDefinition
```

Un sous-agent tel que le moteur l'appelle.

**Attributs :**

- `name` : Nom de l'outil vu par l'orchestrateur.
- `agent` : Agent de la config qui fait le travail.
- `description` : Description de l'outil ; `AGENT_HINT` y est ajouté.
- `max_depth` : `max_depth` de l'agent appelant : profondeur au-delà de laquelle le sous-agent est masqué.
- `budget_share` : Part de ce qui reste au budget du run appelant, donnée à l'enfant (fonction J4) ; `None` : pas de part, l'enfant n'a que ses propres limites.
- `parent_budget` : Limites du run de l'agent appelant (sa config) ; s'y ajoute sa propre part reçue.
- `side_effects` : Effets du sous-agent, ceux des outils de son agent ; ils décident de l'ordre des appels d'un même tour. `irreversible` à défaut de déclaration.

## Classe `loom_ia.engine.subagents.AgentTool`

```python
class AgentTool(DelegatedTool)
```

Outil qui confie la demande à un autre agent, dans un run enfant.

L'outil est déclaré idempotent : un appel interrompu reprend l'enfant au
lieu de le relancer.

**Arguments :**

- `definition` : Le sous-agent.
- `agents` : Donne le contexte d'exécution d'un agent de la config.

**Attributs :**

- `definition` : Le sous-agent.

### Méthode `loom_ia.engine.subagents.AgentTool.__init__`

```python
def __init__(self, definition: SubAgentDefinition, agents: AgentResolver) -> None
```

Construit la description de l'outil.

### Méthode `loom_ia.engine.subagents.AgentTool.spec`

```python
@property
def spec(self) -> ToolSpec
```

Description de l'outil : celle du sous-agent, `AGENT_HINT` et le schéma `message`.

### Méthode `loom_ia.engine.subagents.AgentTool.available`

```python
def available(self, run: RunView) -> bool
```

Vrai si la profondeur du run appelant est inférieure à `max_depth`.

### Méthode `loom_ia.engine.subagents.AgentTool.child_run_id`

```python
def child_run_id(self, call: PendingCall) -> RunId
```

L'enfant déjà choisi pour cet appel (reprise), sinon un nouvel identifiant.

### Méthode `loom_ia.engine.subagents.AgentTool._budget`

```python
def _budget(self, parent: RunState) -> tuple[bool, RunBudget | None]
```

Part de budget de l'enfant (None sans part), et vrai si le parent n'a plus rien.

### Méthode `loom_ia.engine.subagents.AgentTool.run`

```python
async def run(self, arguments: dict[str, JsonValue], context: ToolContext, run: RunView) -> AsyncGenerator[DelegatedPayload | Consumption | ToolOutput | Waiting]
```

Lance l'enfant s'il n'existe pas encore, puis le fait avancer jusqu'à son arrêt.

L'enfant écrit dans le journal du parent, sans diffusion en direct.
S'il n'a pas encore d'événement, il est commencé avec le message, le
contexte du parent et sa part de budget.

**Arguments :**

- `arguments` : `message`, la demande adressée au sous-agent.
- `context` : Contexte de l'appel ; `call_id` désigne l'enfant dans `run.children`.
- `run` : La vue du run parent.

**Produit :**

`Waiting` si l'enfant attend une décision humaine (fonction
H4, point 17) ; sinon sa consommation, puis le résultat : sa
réponse finale avec les fichiers produits par ses outils, ou une
erreur (« Le sous-agent <nom> a échoué (<type>) : … », « … n'a rien
répondu », « Budget du run atteint : … »).

## Fonction `loom_ia.engine.subagents._output`

```python
def _output(name: str, final: RunState) -> ToolOutput
```

Résultat de l'appel : la réponse finale de l'enfant, ou l'erreur qui l'a arrêté.
