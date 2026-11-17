# loom_ia.access.http.schemas

Module `loom_ia.access.http.schemas` de loom_v2 (fichier `src/loom_ia/access/http/schemas.py`).

Corps des requêtes et des réponses de l'API REST (fonction N2).

## Constantes de `loom_ia.access.http.schemas`

- `MAX_JSON_DEPTH = 64` : Profondeur permise à un JSON que l'appelant glisse dans le journal (`metadata`, `arguments`). Le journal se relit avec la limite de récursion du JSON de pydantic, enveloppe de l'événement comprise (200 niveaux en tout) : plus profond, l'événement s'écrit mais ne se relit plus, et sa session est perdue.
- `Shallow = Annotated[dict[str, JsonValue], AfterValidator(shallow)]` : Pas de `type` ici : pydantic en ferait une définition nommée du document OpenAPI.

## Fonction `loom_ia.access.http.schemas.shallow`

```python
def shallow(value: JsonValue) -> JsonValue
```

`value` si ses objets et listes ne s'emboîtent pas sur plus de `MAX_JSON_DEPTH`.

Sinon `ValueError`. Le parcours est itératif : le JSON qu'on vérifie peut être
plus profond que la pile de l'interpréteur.

## Classe `loom_ia.access.http.schemas.AgentInfo`

```python
class AgentInfo(DomainModel)
```

Un agent tel que l'API le publie.

**Attributs :**

- `name` : Nom de l'agent.
- `description` : Sa description.

### Méthode `loom_ia.access.http.schemas.AgentInfo.of`

```python
@classmethod
def of(cls, spec: AgentSpec) -> Self
```

L'agent tel que l'API le publie : son nom et sa description.

## Classe `loom_ia.access.http.schemas.RunRequest`

```python
class RunRequest(DomainModel)
```

Demande de lancement d'un run.

**Attributs :**

- `message` : La demande, non vide.
- `session_id` : Journal auquel rattacher le run ; par défaut, le run est son propre journal.
- `run_id` : Identifiant choisi par l'appelant, pour suivre le run dès son départ.
- `as_user` : Utilisateur déclaré au nom duquel une passerelle agit (décision D-5 des utilisateurs et rôles) : réservé à une clé qui a `act_as`, et borné aux rôles qu'elle endosse (`act_as_roles`). Sans lui, c'est l'utilisateur de la clé qui agit et qui signe.
- `metadata` : Métadonnées libres, inscrites au contexte appelant du run ; profondeur bornée.
- `judges` : Juges (point 21) : `auto` selon leur `when` ; `force`, tous (audit) ; `skip`, aucun — réservé aux clés de portée `admin`.
- `background` : Arrière-plan (fonction H5) : la réponse est l'identifiant du run, pas son résultat.

### Méthode `loom_ia.access.http.schemas.RunRequest.context`

```python
def context(self, tenant_id: TenantId) -> CallerContext
```

Contexte appelant du run ; le client vient de la clé, jamais du corps (point 34).

Le lanceur non plus : c'est l'utilisateur de la clé, ou celui d'`as_user`,
que le SDK y inscrit (décision D-5 des utilisateurs et rôles).

## Classe `loom_ia.access.http.schemas.RunAccepted`

```python
class RunAccepted(DomainModel)
```

Réponse d'un lancement en arrière-plan : le run existe, il n'a pas fini.

**Attributs :**

- `run_id` : Identifiant du run.
- `session_id` : Sa session.
- `status` : Son statut au moment de la réponse.

## Classe `loom_ia.access.http.schemas.Decision`

```python
class Decision(DomainModel)
```

Ce qu'un approbateur dit en tranchant (point 17).

**Attributs :**

- `call_id` : Appel visé ; sans lui, toutes les demandes en attente du run sont tranchées.
- `as_user` : Utilisateur déclaré au nom duquel une passerelle agit (décision D-5 des utilisateurs et rôles) : réservé à une clé qui a `act_as`, et borné aux rôles qu'elle endosse (`act_as_roles`). Sans lui, c'est l'utilisateur de la clé qui agit et qui signe.
- `reason` : Motif de la décision ; un refus le renvoie au modèle.

## Classe `loom_ia.access.http.schemas.Approval`

```python
class Approval(Decision)
```

Un accord, avec les arguments corrigés s'il y en a.

**Attributs :**

- `arguments` : Arguments corrigés de l'appel. Ne vaut que pour un `call_id` désigné : corriger à l'aveugle les arguments de plusieurs appels n'aurait pas de sens.

## Classe `loom_ia.access.http.schemas.Answer`

```python
class Answer(DomainModel)
```

La réponse de l'utilisateur à une question que le run attend (wait_answer).

**Attributs :**

- `call_id` : Question visée ; sans elle, la seule question en attente du run.
- `answers` : Une liste de réponses par question (libellés choisis ou texte libre) ; ou bien `text`.
- `text` : Un message libre qui répond à l'ensemble ; ou bien `answers`.
- `as_user` : Utilisateur déclaré au nom duquel une passerelle agit (décision D-5 des utilisateurs et rôles) : réservé à une clé qui a `act_as`, et borné aux rôles qu'elle endosse (`act_as_roles`). Sans lui, c'est l'utilisateur de la clé qui agit et qui signe.

## Classe `loom_ia.access.http.schemas.Answered`

```python
class Answered(DomainModel)
```

L'appel auquel la réponse a été donnée ; le run reprend en tâche de fond.

**Attributs :**

- `run_id` : Le run qui reprend.
- `call_id` : La question à laquelle la réponse a été donnée.

## Classe `loom_ia.access.http.schemas.Decided`

```python
class Decided(DomainModel)
```

Appels tranchés par une décision ; vide si le run n'attendait rien.

Ce qui reste à trancher se relit sur le run (`pending_approvals`) :
le dire ici serait une seconde vérité à tenir à jour.

**Attributs :**

- `run_id` : Le run.
- `calls` : Appels tranchés.

## Classe `loom_ia.access.http.schemas.Cancellation`

```python
class Cancellation(DomainModel)
```

Qui demande l'arrêt d'un run (fonction A5).

**Attributs :**

- `as_user` : Utilisateur déclaré au nom duquel une passerelle agit (décision D-5 des utilisateurs et rôles) : réservé à une clé qui a `act_as`, et borné aux rôles qu'elle endosse (`act_as_roles`). Sans lui, c'est l'utilisateur de la clé qui agit et qui signe.

## Classe `loom_ia.access.http.schemas.Cancelled`

```python
class Cancelled(DomainModel)
```

Issue d'une demande d'arrêt : faux si le run était déjà fini.

**Attributs :**

- `run_id` : Le run.
- `cancelled` : Vrai si le run a été arrêté ; faux s'il était déjà fini.
