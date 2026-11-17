# loom_ia.tools_builtin.ask

Module `loom_ia.tools_builtin.ask` de loom_v2 (fichier `src/loom_ia/tools_builtin/ask.py`).

Outil fourni `loom.ask` : poser une question à l'utilisateur.

Le moteur ne l'exécute pas : il lit les questions dans les arguments, les
écrit au journal (`question.asked`) et met le run en pause jusqu'à la
réponse de l'utilisateur, qui revient au modèle comme résultat de l'appel.
`invoke` n'est donc jamais appelé par le moteur ; il refuse s'il l'est.

Ce n'est pas une demande d'autorisation : une action à faire valider passe
par l'approbation (`approval`), que la config décide.

## Constantes de `loom_ia.tools_builtin.ask`

- `NAME = 'ask'` : Nom de l'outil.
- `DESCRIPTION = "Pose une ou plusieurs questions à l'utilisateur et attend sa réponse, qui te revient comme résultat de cet appel. À ut…` : Description de l'outil, lue par le modèle.
- `SCHEMA = {'type': 'object', 'properties': {'questions': {'type': 'array', 'minItems': 1, 'maxItems': MAX_QUESTIONS, 'items': {'t…` : Schéma des arguments de l'outil, lu par le modèle.

## Classe `loom_ia.tools_builtin.ask.AskTool`

```python
class AskTool
```

La déclaration de `ask` ; le moteur fait le reste.

### Méthode `loom_ia.tools_builtin.ask.AskTool.spec`

```python
@property
def spec(self) -> ToolSpec
```

La définition de l'outil : nom, description et schéma, délai d'expiration des questions.

### Méthode `loom_ia.tools_builtin.ask.AskTool.invoke`

```python
async def invoke(self, arguments: dict[str, JsonValue], context: ToolContext) -> ToolOutput
```

Jamais exécuté : le moteur pose la question et attend la réponse ; lève `ToolError`.

## Fonction `loom_ia.tools_builtin.ask.build`

```python
def build(params: BaseModel, context: BuiltinContext) -> AskTool
```

Fabrique l'outil `loom.ask` à partir de ses paramètres.
