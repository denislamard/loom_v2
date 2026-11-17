# loom_ia.core.model.judge

Module `loom_ia.core.model.judge` de loom_v2 (fichier `src/loom_ia/core/model/judge.py`).

Juge : un modèle qui note une sortie selon des critères (fonctions E3 et E6, point 21).

Un juge porte sur la réponse finale d'un agent ou sur la sortie d'un rôle.
Pour chaque critère, il rend une note entre 0 et 1 et un motif. Un critère
est réussi si sa note atteint son seuil (`min_score`). Un critère
bloquant sous son seuil fait refuser la sortie : son auteur la répare, puis
`on_failure` décide, comme pour un contrat de sortie. Un critère non
bloquant sous son seuil est seulement signalé.

Déclenchement (point 21) : décidé par le code, jamais par le LLM. Le juge
s'exécute si toutes les clauses présentes de `when` sont vraies :

- `tenants` : clients concernés ;
- `sample` : proportion des runs jugés, par un tirage déterministe
  (`hash(run_id + nom du juge) < sample`) : deux juges ne tirent pas les
  mêmes runs, et le rejeu reproduit le même tirage ;
- `condition` : prédicat Python qui reçoit un `JudgeInput`.

L'appelant peut forcer tous les juges (`judges="force"`, audit, évals) ou
les sauter (`skip`). Un juge qui ne s'exécute pas écrit un `guard.checked`
`skipped` avec son motif (`SkipReason`).

Un juge se déclare sous `judge` d'un agent ou d'un rôle ; son modèle ne
doit pas être celui qu'il juge (avertissement, erreur en profil `prod`,
fonction E6). Son verdict est écrit dans `judge.evaluated`.

## Constantes de `loom_ia.core.model.judge`

- `JudgesMode = Literal['auto', 'force', 'skip']` : Juges d'un run, choisis par l'appelant : selon leur `when` (`auto`), tous (`force`), ou aucun (`skip`).  Se passe à `run` (`judges=`), à `loom run --judges` ; écrit dans `run.started` et hérité par les sous-runs.
- `SkipReason = Literal['filtered', 'sampled_out', 'condition_false', 'caller_skip', 'other_profile']` : Motif d'un juge qui ne s'exécute pas : client hors `tenants`, run non tiré, condition fausse, `skip` de l'appelant, autre profil.
- `JUDGES_MODES = ('auto', 'force', 'skip')` : Les valeurs de `JudgesMode`, dans l'ordre.
- `CRITERION_NAME_PATTERN = '^[A-Za-z0-9_-]{1,64}$'` : Forme d'un nom de critère : 1 à 64 lettres, chiffres, `_` ou `-`.
- `DEFAULT_MIN_SCORE = 0.8` : Seuil d'un critère par défaut.

## Classe `loom_ia.core.model.judge.Criterion`

```python
class Criterion(DomainModel)
```

Critère d'un juge : une règle, un seuil, bloquant ou non.

**Attributs :**

- `name` : Nom du critère, repris dans le verdict.
- `rule` : Ce que la sortie doit respecter, donné au juge tel quel.
- `min_score` : Seuil, entre 0 et 1. Défaut : 0,8.
- `blocking` : Sous son seuil, un critère bloquant fait refuser la sortie ; un critère non bloquant est seulement signalé. Défaut : vrai.

## Classe `loom_ia.core.model.judge.JudgeWhen`

```python
class JudgeWhen(DomainModel)
```

Déclenchement d'un juge (`when`) : toutes les clauses présentes doivent être vraies.

**Attributs :**

- `sample` : Proportion des runs jugés, entre 0 et 1 (tirage déterministe par run et par juge). Défaut : 1, tous.
- `condition` : Prédicat `(JudgeInput) -> bool`, synchrone ou non, donné par `module:attr`.
- `tenants` : Clients dont les runs sont jugés, un au moins.
- `profiles` : Profils où ce juge travaille (phase 5.5a) : ailleurs il est sauté, et le journal le dit (`other_profile`) — un juge déclaré ne disparaît pas en silence parce qu'on a changé de profil.

## Classe `loom_ia.core.model.judge.CriterionScore`

```python
class CriterionScore(DomainModel)
```

Note d'un critère par le juge, avec son seuil ; écrite dans `judge.evaluated`.

**Attributs :**

- `name` : Nom du critère.
- `score` : Note donnée par le juge, entre 0 et 1.
- `min_score` : Seuil du critère.
- `blocking` : Vrai si le critère est bloquant.
- `reason` : Motif donné par le juge ; du contenu.

### Méthode `loom_ia.core.model.judge.CriterionScore.passed`

```python
@property
def passed(self) -> bool
```

Vrai si la note atteint le seuil.

## Classe `loom_ia.core.model.judge.JudgeInput`

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class JudgeInput
```

Ce que reçoit le prédicat `when.condition` d'un juge.

**Attributs :**

- `run_id` : Run jugé.
- `agent` : Agent du run.
- `target` : `output` (réponse finale) ou `role:<nom>`.
- `role` : Rôle dont la sortie est jugée ; `None` pour la réponse finale.
- `output` : Sortie jugée, en texte.
- `data` : Son objet JSON s'il y en a un (contrat avec schéma).
- `request` : Demande de l'utilisateur qui a lancé le run.
- `arguments` : Arguments du rôle jugé.
- `caller` : Contexte de l'appelant (client, utilisateur, métadonnées).

## Fonction `loom_ia.core.model.judge.sampled`

```python
def sampled(run_id: str, judge: str, rate: float) -> bool
```

Tirage déterministe d'un run pour un juge : vrai si le run est jugé.

Le tirage dépend du run et du juge (SHA-256 de `<run_id>:<juge>`) : deux
juges ne tirent pas les mêmes runs, et le rejeu reproduit le même tirage.

**Arguments :**

- `run_id` : Le run.
- `judge` : Le nom du juge.
- `rate` : Proportion des runs jugés (`when.sample`).

**Renvoie :**

Vrai si le run est jugé ; toujours vrai à 1, toujours faux à 0.
