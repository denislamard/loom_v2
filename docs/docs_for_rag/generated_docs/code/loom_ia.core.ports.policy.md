# loom_ia.core.ports.policy

Module `loom_ia.core.ports.policy` de loom_v2 (fichier `src/loom_ia/core/ports/policy.py`).

Port des politiques (points 1 et 2, fonction E7).

Une politique déclare ses points d'accroche et les décisions qu'elle peut
rendre : la config vérifie au démarrage que ces décisions sont permises aux
points où l'agent la branche. Le décorateur `@policy` de
`loom_ia.policies` fabrique une politique à partir d'une fonction.

Points d'accroche : `before_model`, `after_model`, `before_tool`,
`after_tool`, `on_output`. Décisions : `continue`, `replace`,
`retry`, `deny`, `pause`, `stop`, `fail` ; toute décision autre que
`continue` est écrite au journal (`policy.decided`). Un agent branche ses
politiques sous `policies`. Les budgets, les contrats, les juges et le
contrôle de fidélité sont des politiques fournies.

## Classe `loom_ia.core.ports.policy.Policy`

```python
@runtime_checkable
class Policy(Protocol)
```

Politique branchable sur un ou plusieurs points d'accroche.

`isinstance` vérifie seulement la présence des attributs : c'est ce qui
sert à reconnaître une politique dans la config.

### Méthode `loom_ia.core.ports.policy.Policy.name`

```python
@property
def name(self) -> str
```

Nom de la politique, recopié dans `policy.decided`.

### Méthode `loom_ia.core.ports.policy.Policy.points`

```python
@property
def points(self) -> frozenset[HookPoint]
```

Points d'accroche où la politique sait s'appliquer ; la config refuse de la brancher ailleurs.

### Méthode `loom_ia.core.ports.policy.Policy.decisions`

```python
@property
def decisions(self) -> frozenset[DecisionKind]
```

Décisions qu'elle peut rendre, en plus de `continue` ; la config vérifie qu'elles sont permises à ses points.

### Méthode `loom_ia.core.ports.policy.Policy.decide`

```python
async def decide(self, subject: PolicySubject, context: PolicyContext) -> Decision
```

Rend la décision de la politique pour ce qui se passe à un point d'accroche.

**Arguments :**

- `subject` : Ce qui est en jeu au point `subject.point` : la requête au modèle, sa réponse, un appel d'outil et ses arguments, un résultat, la réponse finale.
- `context` : Ce que la politique sait d'elle-même pour cet appel : son nom dans l'agent, ses paramètres de config, les réparations déjà demandées pour cette sortie, et, pour un juge, l'historique de la session et le dernier résumé. Un guard y enregistre ses contrôles (`record`), journalisés en `guard.checked` avant sa décision.

**Renvoie :**

La décision ; `Continue` laisse faire.
