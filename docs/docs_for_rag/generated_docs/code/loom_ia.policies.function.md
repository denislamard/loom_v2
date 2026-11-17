# loom_ia.policies.function

Module `loom_ia.policies.function` de loom_v2 (fichier `src/loom_ia/policies/function.py`).

Politiques écrites en Python (points 1 et 2).

Une fonction reçoit ce qui se passe au point d'accroche, et éventuellement
son contexte (nom, paramètres de la config, réparations déjà demandées) ;
elle rend une décision. Elle peut être synchrone ou asynchrone.

    @policy(points=["before_tool"], decisions=["replace", "deny"])
    def numero_devis(subject: BeforeTool, context: PolicyContext) -> Decision:
        '''Normalise le numéro de devis, refuse un numéro mal formé.'''
        ...

`points` : là où la politique sait s'appliquer ; un agent la branche sur
tout ou partie de ces points. `decisions` : ce qu'elle peut rendre en plus
de `Continue`. Une décision non permise à l'un des points est refusée au
démarrage ; une décision non déclarée, rendue quand même, est une erreur de
la politique.

Une fonction synchrone s'exécute dans la boucle : elle doit être rapide et
ne pas faire d'entrée-sortie (sinon, l'écrire en `async`).

Nom : celui de la fonction par défaut, ou `name` ; 1 à 64 caractères
(lettres, chiffres, `_`, `.`, `-`), sans le préfixe `loom.`, réservé
aux politiques fournies.

## Constantes de `loom_ia.policies.function`

- `PolicyFunction = Callable[..., Decision | Awaitable[Decision]]` : Fonction d'une politique : `(sujet)` ou `(sujet, contexte)`, synchrone ou asynchrone.

## Classe `loom_ia.policies.function.FunctionPolicy`

```python
class FunctionPolicy
```

Politique construite à partir d'une fonction ; reste appelable comme elle.

**Attributs :**

- `fn` : La fonction.

### Méthode `loom_ia.policies.function.FunctionPolicy.__init__`

```python
def __init__(self, fn: PolicyFunction, *, points: Iterable[HookPoint], decisions: Iterable[DecisionKind], name: str | None=None, builtin: bool=False) -> None
```

Contrôle la déclaration de la politique.

**Arguments :**

- `fn` : La fonction : un ou deux paramètres positionnels obligatoires (le sujet, puis le contexte).
- `points` : Points d'accroche où elle sait s'appliquer.
- `decisions` : Décisions qu'elle peut rendre en plus de `Continue`.
- `name` : Nom de la politique ; par défaut celui de la fonction.
- `builtin` : Vrai pour une politique fournie par loom-ia : son nom doit commencer par `loom.`, et seulement le sien.

**Lève :**

- `ValueError` : Nom invalide ou préfixe `loom.` mal employé, point ou décision inconnus, aucun point.
- `TypeError` : La fonction n'a pas un ou deux paramètres obligatoires.

### Méthode `loom_ia.policies.function.FunctionPolicy.name`

```python
@property
def name(self) -> str
```

Nom de la politique.

### Méthode `loom_ia.policies.function.FunctionPolicy.points`

```python
@property
def points(self) -> frozenset[HookPoint]
```

Points d'accroche où elle sait s'appliquer.

### Méthode `loom_ia.policies.function.FunctionPolicy.decisions`

```python
@property
def decisions(self) -> frozenset[DecisionKind]
```

Décisions qu'elle peut rendre, `continue` exclue.

### Méthode `loom_ia.policies.function.FunctionPolicy.decide`

```python
async def decide(self, subject: PolicySubject, context: PolicyContext) -> Decision
```

Appelle la fonction, avec le contexte si elle le reçoit, et attend son résultat.

**Arguments :**

- `subject` : Le sujet du point d'accroche.
- `context` : Le contexte de la politique.

**Renvoie :**

La décision de la fonction ; le moteur la contrôle ensuite.

### Méthode `loom_ia.policies.function.FunctionPolicy.__call__`

```python
def __call__(self, *args: object) -> Decision | Awaitable[Decision]
```

Appelle la fonction telle quelle : la politique reste utilisable comme elle.

## Fonction `loom_ia.policies.function.policy`

```python
def policy(*, points: Iterable[HookPoint], decisions: Iterable[DecisionKind], name: str | None=None) -> Callable[[PolicyFunction], FunctionPolicy]
```

Déclare une fonction comme politique.

**Arguments :**

- `points` : Points d'accroche où elle sait s'appliquer.
- `decisions` : Décisions qu'elle peut rendre en plus de `Continue`.
- `name` : Nom de la politique ; par défaut celui de la fonction.

**Renvoie :**

Un décorateur qui rend une `FunctionPolicy`.

**Lève :**

- `ValueError` : Déclaration invalide (voir `FunctionPolicy`), levée à la décoration.
- `TypeError` : La fonction n'a pas un ou deux paramètres obligatoires.

## Fonction `loom_ia.policies.function._arity`

```python
def _arity(fn: PolicyFunction, name: str) -> int
```

Nombre de paramètres positionnels : le sujet, et éventuellement le contexte.
