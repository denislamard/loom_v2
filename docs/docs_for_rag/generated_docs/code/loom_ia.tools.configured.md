# loom_ia.tools.configured

Module `loom_ia.tools.configured` de loom_v2 (fichier `src/loom_ia/tools/configured.py`).

Surcharge des déclarations d'un outil par la config (point 50).

Un outil déclare lui-même ses effets de bord, son idempotence, son délai et
son seuil de déport ; la config peut les remplacer, et lui donner un contrat
de sortie, sans toucher au code de l'outil.

Un outil qui évalue chacun de ses appels (`assess_call`) garde cette
évaluation sous l'enveloppe : sans elle, régler son délai suffirait à lui
faire perdre ses approbations par appel. Ce que la config fixe —
approbation, effets, idempotence — l'emporte, champ par champ, sur ce que
l'outil évalue.

## Constantes de `loom_ia.tools.configured`

- `ASSESSED = frozenset({'approval', 'side_effects', 'idempotent'})` : Déclarations qu'une évaluation par appel peut donner, et que la config peut fixer.

## Classe `loom_ia.tools.configured.ConfiguredTool`

```python
@dataclass(frozen=True, slots=True)
class ConfiguredTool
```

Outil dont la déclaration a été modifiée par la config.

**Attributs :**

- `tool` : L'outil enveloppé.
- `spec` : Sa déclaration, modifiée par la config.
- `fixed` : Celles de `ASSESSED` que la config a données : elles valent pour tous les appels.

### Méthode `loom_ia.tools.configured.ConfiguredTool.invoke`

```python
async def invoke(self, arguments: dict[str, JsonValue], context: ToolContext) -> ToolOutput
```

Appelle l'outil enveloppé tel quel.

### Méthode `loom_ia.tools.configured.ConfiguredTool.assess_call`

```python
def assess_call(self, arguments: Mapping[str, JsonValue]) -> CallAssessment | None
```

L'évaluation de l'outil enveloppé, corrigée de ce que la config fixe.

## Fonction `loom_ia.tools.configured.innermost`

```python
def innermost(tool: object) -> object
```

L'outil sous toutes ses enveloppes de config : ce qu'il sait faire d'autre (`aclose`…).

## Fonction `loom_ia.tools.configured.configure`

```python
def configure(tool: Tool, *, description: str | None=None, timeout: float | None=None, side_effects: SideEffects | None=None, approval: Approval | None=None, idempotent: bool | None=None, offload_over: int | None=None, output: OutputContract | None=None, skill: str | None=None) -> Tool
```

Applique les valeurs données ; `None` garde ce que l'outil déclare.

**Arguments :**

- `tool` : L'outil.
- `description` : Description vue par le modèle.
- `timeout` : Délai, en secondes.
- `side_effects` : Effets de bord.
- `approval` : Approbation.
- `idempotent` : Vrai si refaire l'appel est sans risque.
- `offload_over` : Seuil de déport, en caractères.
- `output` : Contrat de sortie.
- `skill` : Mode d'emploi ajouté à la description (vérifié).

**Renvoie :**

L'outil tel quel si rien n'est donné, sinon une `ConfiguredTool`.
