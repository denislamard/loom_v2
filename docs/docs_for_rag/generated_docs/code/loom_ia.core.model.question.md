# loom_ia.core.model.question

Module `loom_ia.core.model.question` de loom_v2 (fichier `src/loom_ia/core/model/question.py`).

Question posée par le modèle à l'utilisateur, et sa réponse (outil `loom.ask`).

Distincte d'une approbation : c'est le **modèle** qui la pose, en appelant
l'outil fourni `ask` ; c'est l'**utilisateur de la session** qui y répond,
et sa réponse est une **information** rendue au modèle comme résultat de
l'appel. Une approbation, elle, vient de la config (`approval`, politiques),
est tranchée par un approbateur (portée `approve`), et dit oui ou non à un
appel déjà formé.

Le run attend la réponse en `PAUSED`, comme une approbation : même
mécanique de pause, de concession rendue et de reprise ; attente distincte.
Voir `docs/wait_answer.md` et `docs/multi-questions.md`.

Événements : `question.asked`, `question.answered`, `question.expired`.
On répond par `loom answer`, `POST /v1/runs/{id}/answer`, l'API Python, ou
en tapant un message dans la session.

## Constantes de `loom_ia.core.model.question`

- `MAX_QUESTIONS = 4` : Questions posées au plus par un appel de `loom.ask`.
- `MAX_OPTIONS = 6` : Choix proposés au plus par question.
- `MAX_HEADER = 24` : Longueur maximale de l'intitulé court d'une question, en caractères.
- `MAX_TEXT = 2000` : Longueur maximale du texte d'une question, en caractères.
- `Answerer = Callable[[PendingQuestion], Awaitable[QuestionReply | NoAnswer]]` : Répondeur en ligne (elicitation MCP, script) : il répond dans la boucle, sans que le run passe par `PAUSED`.  Ce qu'il rend est écrit au journal comme une réponse ordinaire ; `NoAnswer` laisse le run en pause, ou clôt la question si `expired` est vrai.

## Classe `loom_ia.core.model.question.QuestionOption`

```python
class QuestionOption(DomainModel)
```

Un choix proposé ; l'utilisateur peut toujours répondre autre chose.

**Attributs :**

- `label` : Libellé du choix, de 1 à 200 caractères ; c'est lui qui revient dans la réponse.
- `description` : Précision montrée avec le choix, 500 caractères au plus.

## Classe `loom_ia.core.model.question.Question`

```python
class Question(DomainModel)
```

Une question : son texte, un intitulé court, et des choix s'il y en a.

**Attributs :**

- `question` : Texte de la question, de 1 à 2 000 caractères.
- `header` : Intitulé court, 24 caractères au plus.
- `options` : Choix proposés, 6 au plus ; aucun : réponse libre.
- `multiple` : Vrai si plusieurs choix sont permis. Défaut : faux.

## Classe `loom_ia.core.model.question.QuestionReply`

```python
class QuestionReply(DomainModel)
```

La réponse de l'utilisateur.

`answers` : une liste par question, dans leur ordre (les libellés choisis,
ou un texte libre). `text` : un message libre qui répond à l'ensemble —
ce qu'il a tapé dans la conversation au lieu de remplir les questions.
L'un des deux, pas les deux.

**Attributs :**

- `answers` : Une liste de réponses par question, dans leur ordre.
- `text` : Message libre qui répond à l'ensemble.
- `by` : Identité de qui a répondu.
- `via` : Passerelle qui a porté la réponse (`as_user`, décision D-5 des utilisateurs et rôles).

## Classe `loom_ia.core.model.question.PendingQuestion`

```python
class PendingQuestion(DomainModel)
```

Question écrite au journal : elle attend tant qu'elle n'a ni réponse ni expiration.

C'est ce que montrent l'état d'un run, `loom sessions show` et les accès
à qui doit répondre.

**Attributs :**

- `call_id` : Appel de `loom.ask` qui pose la question.
- `questions` : Les questions.
- `run_id` : Run qui l'a posée : le run lui-même, ou le sous-run d'un sous-agent.
- `agent` : Agent de ce run : c'est son `access` qui dit qui lui répond (`docs/loom_role_user.md` §5.2).
- `expire_at` : Échéance ; `None` : la question attend indéfiniment.
- `reply` : La réponse, une fois donnée.
- `expired` : Vrai si la question a expiré sans réponse.

### Méthode `loom_ia.core.model.question.PendingQuestion.open`

```python
@property
def open(self) -> bool
```

Vrai tant que la question n'a ni réponse ni expiration.

### Méthode `loom_ia.core.model.question.PendingQuestion.stale`

```python
def stale(self, now: datetime) -> bool
```

Vrai si l'échéance est passée à cet instant (la question n'est pas encore marquée expirée).

## Classe `loom_ia.core.model.question.NoAnswer`

```python
@dataclass(frozen=True, slots=True)
class NoAnswer
```

Le répondeur en ligne n'a pas de réponse (formulaire refusé ou fermé).

`expired` : la question est close sans réponse (rejeu d'une question
expirée au journal, évaluation sans humain) ; le modèle l'apprend.
Sinon, elle reste posée et le run attend.

**Attributs :**

- `reason` : Pourquoi il n'y a pas de réponse.
- `expired` : Vrai pour clore la question sans réponse.
