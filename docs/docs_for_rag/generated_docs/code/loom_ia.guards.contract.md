# loom_ia.guards.contract

Module `loom_ia.guards.contract` de loom_v2 (fichier `src/loom_ia/guards/contract.py`).

Contrats de sortie : normalisation, contrôle, réparation, échec (fonctions E1, E2, E4 et E5, point 20).

Le contrat est une politique fournie par loom-ia, `loom.contract`, branchée
en tête des politiques d'un agent : `on_output` pour la réponse finale (le
contrat `output` de l'agent), `after_tool` pour la sortie d'un rôle ou le
résultat d'un outil (le contrat porté par la déclaration de l'outil).

Pour chaque sortie contrôlée, un `guard.checked` est journalisé, réussi ou
non, puis la décision :

- **réussi** : `Continue`, ou `Replace` par la sortie normalisée (et, avec
  un schéma, l'objet JSON dans `data`) ;
- **échoué, réparable** (réponse finale, rôle) : `Retry` avec le diagnostic,
  tant que `repair.max_attempts` le permet ; la réponse finale est réparée
  par l'orchestrateur (sans outils, sauf `repair.tools: allowed`), un rôle
  par son propre modèle ;
- **échoué, sans réparation possible** : `on_failure` (`fail`,
  `unverified`, `fallback`). Pour la réponse finale, `fail` fait échouer
  le run ; pour un rôle ou un outil, la sortie revient à l'orchestrateur en
  erreur, avec ses problèmes (`refusal`) : une règle, pas une panne, et un
  appel à ne pas refaire à l'identique. L'orchestrateur n'en est pas l'auteur,
  il ne reçoit donc pas le diagnostic de réparation.

La normalisation est déterministe et ne coûte aucun appel : retrait d'un
bloc de code qui entoure la sortie, extraction du JSON quand un schéma est
attendu (bloc de code, puis première accolade ou crochet jusqu'au dernier),
espaces de début et de fin retirés.

Les motifs (`must_match`, `must_not_match`, `pattern` d'un schéma) ont un
délai : un motif qui ne finit pas (`core.bounded_regex`) est un problème du
contrôle, jamais un « absent » ni un « conforme ». Le problème d'un motif
interdit dit le motif, la position et la longueur de ce qui a été trouvé, pas
le texte lui-même : le diagnostic part au journal, dans l'erreur de fin de run
et au modèle, et un texte interdit (une clé, un secret) n'a pas à y être recopié.

## Constantes de `loom_ia.guards.contract`

- `CONTRACT_POLICY = 'loom.contract'` : Nom de la politique des contrats dans le journal.
- `GUARD = 'contract'` : Nom du guard dans `guard.checked`.
- `_WHOLE_FENCE = re.compile('```[\\w-]*[ \\t]*\\n(.*?)```', re.DOTALL)` : Les espaces et le saut de ligne avant la fermeture restent dans le groupe, que les appelants strip() : les écarter dans le motif le rendait quadratique.
- `_FENCE_LINE = re.compile('^ {0,3}```', re.MULTILINE)` : Une ouverture de bloc au milieu d'un corps : le texte compte plusieurs blocs.

## Classe `loom_ia.guards.contract.Checked`

```python
@dataclass(frozen=True, slots=True)
class Checked
```

Résultat du contrôle d'une sortie.

**Attributs :**

- `text` : Sortie retenue : normalisée si la normalisation l'a changée.
- `normalized` : Vrai si la normalisation a changé la sortie.
- `problems` : Ce qui ne va pas, une phrase par règle ; vide si la sortie convient.
- `data` : Objet JSON validé par le schéma.

### Méthode `loom_ia.guards.contract.Checked.ok`

```python
@property
def ok(self) -> bool
```

Vrai si la sortie tient toutes les règles du contrat.

## Fonction `loom_ia.guards.contract.check`

```python
def check(contract: OutputContract, text: str, data: JsonValue=None) -> Checked
```

Contrôle une sortie ; `data` : ses données structurées, s'il y en a (outil).

**Arguments :**

- `contract` : Le contrat : schéma, motifs exigés ou interdits, longueurs, sortie non vide.
- `text` : La sortie.
- `data` : Ses données structurées.

**Renvoie :**

La sortie retenue (normalisée), ses problèmes, et l'objet JSON validé.

## Fonction `loom_ia.guards.contract.normalize`

```python
def normalize(text: str, *, json_expected: bool=False) -> str
```

Sortie corrigée sans appel au modèle : bloc de code retiré, JSON extrait, espaces.

**Arguments :**

- `text` : La sortie.
- `json_expected` : Vrai si un schéma attend du JSON : il est extrait.

## Fonction `loom_ia.guards.contract.diagnostic`

```python
def diagnostic(contract: OutputContract, problems: tuple[str, ...]) -> str
```

Diagnostic donné à l'auteur de la sortie pour qu'il la répare.

Les règles que la sortie tient déjà sont rappelées : sans elles, l'auteur
corrige un point et en casse un autre qu'il ignore (essai de la fonction E4, 19/10 : un
motif exigé ajouté, qui était aussi interdit). Le schéma, lui, est toujours
donné en entier.

**Arguments :**

- `contract` : Le contrat.
- `problems` : Les problèmes de la sortie.

**Renvoie :**

Le texte à donner à l'auteur : problèmes, règles déjà tenues, schéma.

## Fonction `loom_ia.guards.contract.refusal`

```python
def refusal(name: str, problems: tuple[str, ...], *, role: bool, repairs: int=0, by: str='son contrat de sortie') -> str
```

Refus d'une sortie de rôle ou d'outil, dit à l'orchestrateur.

`by` : ce qui refuse, « son contrat de sortie » ou « son juge <nom> ». Au
essai de la fonction E5 du 19/10, le refus d'un juge sur un rôle portait aussi la consigne
de réparation : Haiku a rappelé le rôle à l'identique, puis écrit la sortie
lui-même, hors du juge.

Ce n'est pas le diagnostic de réparation : l'orchestrateur n'est pas l'auteur
de la sortie et ne peut pas la corriger. À l'essai de la fonction E1 du 19/10, la consigne
« Réponds de nouveau avec la sortie corrigée » lui faisait relancer l'appel
identique, refusé de la même façon. Le texte dit donc une règle, pas une
panne, et demande de ne pas refaire l'appel tel quel. Ni la sortie (elle peut
porter un secret, qui passerait dans le journal et sous les yeux du modèle),
ni le schéma (il pousse le modèle à fabriquer le résultat lui-même).

**Arguments :**

- `name` : Nom du rôle ou de l'outil.
- `problems` : Les problèmes.
- `role` : Vrai pour un rôle, faux pour un outil.
- `repairs` : Réparations déjà tentées.
- `by` : Ce qui refuse.

**Renvoie :**

Le message d'erreur que lit l'orchestrateur.

## Fonction `loom_ia.guards.contract._kept_rules`

```python
def _kept_rules(contract: OutputContract, problems: tuple[str, ...]) -> list[str]
```

Règles du contrat (hors schéma) que les problèmes ne citent pas.

## Classe `loom_ia.guards.contract.ContractGuard`

```python
class ContractGuard
```

Politique `loom.contract` : contrats de la réponse finale, des rôles et des outils.

### Méthode `loom_ia.guards.contract.ContractGuard.name`

```python
@property
def name(self) -> str
```

`loom.contract`.

### Méthode `loom_ia.guards.contract.ContractGuard.points`

```python
@property
def points(self) -> frozenset[HookPoint]
```

`after_tool`, et `on_output` si l'agent a un contrat de réponse finale.

### Méthode `loom_ia.guards.contract.ContractGuard.decisions`

```python
@property
def decisions(self) -> frozenset[DecisionKind]
```

`replace`, `retry` et `fail`.

### Méthode `loom_ia.guards.contract.ContractGuard.decide`

```python
async def decide(self, subject: PolicySubject, context: PolicyContext) -> Decision
```

Contrôle la réponse finale (`on_output`) ou le résultat d'un rôle ou d'un outil sous contrat (`after_tool`).

Un résultat d'outil déjà en erreur n'est pas contrôlé.

**Arguments :**

- `subject` : Le sujet du point d'accroche.
- `context` : Contexte de la politique (réparations déjà demandées, contrôles enregistrés).

**Renvoie :**

`CONTINUE` ou `Replace` (sortie normalisée) si la sortie convient ;
sinon `Retry` avec le diagnostic, ou la décision de `on_failure`.

### Méthode `loom_ia.guards.contract.ContractGuard._final`

```python
def _final(self, contract: OutputContract, message: Message, context: PolicyContext) -> Decision
```

Réponse finale : réparée par l'orchestrateur, puis `on_failure`.

### Méthode `loom_ia.guards.contract.ContractGuard._result`

```python
def _result(self, contract: OutputContract, output: ToolOutput, name: str, repairable: bool, context: PolicyContext) -> Decision
```

Sortie d'un rôle (réparée par son modèle) ou résultat d'un outil (jamais réparé).

## Fonction `loom_ia.guards.contract._structured`

```python
def _structured(output: ToolOutput, checked: Checked) -> ToolOutput
```

Résultat normalisé : le texte retenu, et l'objet JSON validé s'il y en a un.

## Fonction `loom_ia.guards.contract.visible_text`

```python
def visible_text(output: ToolOutput) -> str
```

Texte d'un résultat : ses blocs de texte, sinon ses données en JSON.

Ce que lisent le contrat d'un outil et son juge : la même chose.

## Fonction `loom_ia.guards.contract._unfenced`

```python
def _unfenced(text: str) -> str
```

Texte sans son bloc de code, si un seul bloc l'occupe en entier.

## Fonction `loom_ia.guards.contract._between_brackets`

```python
def _between_brackets(text: str) -> str
```

De la première accolade (ou crochet) à la dernière du même type.
