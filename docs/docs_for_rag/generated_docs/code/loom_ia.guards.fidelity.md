# loom_ia.guards.fidelity

Module `loom_ia.guards.fidelity` de loom_v2 (fichier `src/loom_ia/guards/fidelity.py`).

Contrôle de fidélité d'un résumé de session (point 23).

Un résumé n'a d'intérêt que s'il garde ce qui sert à la suite : numéros de
devis, montants, quantités, adresses. Le contrôle est déterministe — aucun
modèle n'y participe : on relève les repères du segment d'origine et on
vérifie qu'ils se retrouvent dans le résumé.

Repères relevés :

- les **références** du type `D-2026-042` (lettres, tiret, chiffres) ;
- les **adresses e-mail** ;
- les **nombres** d'au moins trois chiffres, séparateurs de milliers retirés et
  décimales comprises (`1 234,56` n'est pas `1 234,99`).

Le seuil de trois chiffres écarte le bruit — « trois phrases », le `09`
d'une date en ISO que le résumé écrira « 2 septembre » — tout en gardant les
montants, les années et les quantités. Un manque fait demander une nouvelle
tentative au modèle ; à la suivante, le résumé est gardé tel quel et le
marqueur de compaction porte `fidelity: warning`.

## Constantes de `loom_ia.guards.fidelity`

- `FIDELITY_POLICY = 'loom.fidelity'` : Nom de la politique de fidélité dans le journal.
- `FIDELITY_GUARD = 'fidelity'` : Nom du guard dans `guard.checked`.
- `MIN_DIGITS = 3` : En deçà, un nombre ne dit rien qu'un résumé doive garder.
- `SHOWN = 12` : Repères nommés dans le diagnostic, pour ne pas lui faire recopier le segment.
- `_EMAIL = re.compile('(?<![\\w.+-])[\\w.+-]+@[\\w-]+\\.[\\w.-]+')` : Le lookbehind ne laisse commencer une adresse qu'au début d'un mot : sans lui, chaque lettre d'un long mot sans « @ » relançait la lecture jusqu'à sa fin.
- `_SEPARATOR = re.compile('[\\u00a0\\u202f .,]')` : Des chiffres que séparent un seul caractère : espace (fine, insécable ou non), point ou virgule. Ce qu'ils forment — milliers, décimales, plusieurs nombres — se tranche dans `_readings`.
- `Reading = tuple[str, ...]` : Une lecture : les nombres que dit un même texte ; plusieurs si le texte est ambigu.

## Fonction `loom_ia.guards.fidelity.markers`

```python
def markers(text: str) -> set[str]
```

Repères d'un texte : références, adresses, nombres assez longs.

Un texte ambigu (« 120 150 ») les donne toutes les deux lectures : c'est ce
qu'un résumé peut avoir écrit de plus large.

**Arguments :**

- `text` : Le texte.

## Fonction `loom_ia.guards.fidelity.missing`

```python
def missing(source: str, summary: str) -> list[str]
```

Repères du segment absents du résumé, dans l'ordre du texte.

Un repère ambigu est présent si le résumé a l'une de ses lectures en entier.

**Arguments :**

- `source` : Le segment d'origine.
- `summary` : Le résumé.

## Fonction `loom_ia.guards.fidelity._requirements`

```python
def _requirements(text: str) -> list[tuple[int, list[Reading]]]
```

Repères du texte, dans l'ordre où ils apparaissent, avec leurs lectures possibles.

## Fonction `loom_ia.guards.fidelity._long_enough`

```python
def _long_enough(reading: Reading) -> Reading
```

Les nombres d'une lecture qui ont assez de chiffres pour compter.

## Fonction `loom_ia.guards.fidelity._readings`

```python
def _readings(token: str) -> list[Reading]
```

Façons de lire un nombre brut, la plus probable d'abord.

- `1.234.567`, `1,234,567`, `1 234 567` : des groupes de milliers ;
- `1 234,56`, `1.234,56`, `1,234.56` : une partie décimale, que ses
  zéros finaux n'allongent pas (`1 840,00` vaut `1 840`) ;
- `10.10.2026`, `3 15` : plusieurs nombres.

Une espace simple peut grouper des milliers ou séparer deux nombres
(`120 150`) : les deux lectures sont rendues. Un point ou une virgule
suivi d'exactement trois chiffres est lu comme un groupe de milliers.

## Fonction `loom_ia.guards.fidelity._grouped`

```python
def _grouped(parts: list[str], separators: list[str]) -> bool
```

Ces chiffres sont-ils un seul nombre, en groupes de milliers réguliers ?

## Classe `loom_ia.guards.fidelity.FidelityGuard`

```python
class FidelityGuard
```

Politique `loom.fidelity` : les repères du segment sont-ils dans le résumé ?

### Méthode `loom_ia.guards.fidelity.FidelityGuard.__init__`

```python
def __init__(self, max_attempts: int=1) -> None
```

Règle les réparations.

**Arguments :**

- `max_attempts` : Réparations demandées au modèle avant de garder le résumé tel quel.

### Méthode `loom_ia.guards.fidelity.FidelityGuard.name`

```python
@property
def name(self) -> str
```

`loom.fidelity`.

### Méthode `loom_ia.guards.fidelity.FidelityGuard.points`

```python
@property
def points(self) -> frozenset[HookPoint]
```

`on_output` : le résumé est la réponse finale de l'agent de compaction.

### Méthode `loom_ia.guards.fidelity.FidelityGuard.decisions`

```python
@property
def decisions(self) -> frozenset[DecisionKind]
```

`retry` seulement.

### Méthode `loom_ia.guards.fidelity.FidelityGuard.decide`

```python
async def decide(self, subject: PolicySubject, context: PolicyContext) -> Decision
```

Compare les repères du segment et ceux du résumé.

**Arguments :**

- `subject` : Le résumé (réponse finale de `_compaction`) et son run.
- `context` : Contexte de la politique (réparations déjà demandées).

**Renvoie :**

`CONTINUE` si rien ne manque, ou si les réparations sont épuisées
(le `guard.checked` est `unverified`) ; sinon `Retry` qui nomme les
repères manquants.

## Fonction `loom_ia.guards.fidelity._segment`

```python
def _segment(messages: tuple[Message, ...]) -> str
```

Segment à résumer : la demande faite à l'agent de compaction.
