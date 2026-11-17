# loom_ia.access.authz

Module `loom_ia.access.authz` de loom_v2 (fichier `src/loom_ia/access/authz.py`).

Qui peut faire quoi : le noyau d'autorisation (docs/loom_role_user.md, § 4).

Un **acteur** est l'utilisateur au nom duquel une opération est faite, une
fois résolu : par sa clé d'API, par `--user` ou `user=`, ou endossé par une
passerelle (`as_user`). Ce module dit s'il peut faire une action sur un agent,
et s'il peut trancher une demande d'approbation. Les accès (SDK, CLI, REST,
MCP) l'appellent tous : c'est ce qui fait qu'aucun n'y échappe.

Le code ne teste jamais un nom de rôle, seulement des permissions : les rôles
restent de la configuration. Et pour trancher une approbation, **un même rôle**
doit remplir toutes les conditions (§ 4.4) — sinon un rôle annexe qui aurait
`self_approve` ouvrirait l'auto-approbation sur des demandes où il n'est pas
approbateur.

L'identité, elle, n'est pas vérifiée ici : la clé d'API l'a été par l'accès, et
`--user` ou `user=` sont déclaratifs (§ 6.3, § 6.4). Ce module ne fait que
la confronter à la matrice des droits.

## Classe `loom_ia.access.authz.AccessDenied`

```python
class AccessDenied(PermissionError)
```

L'acteur n'a pas le droit de faire ce qu'il demande ; le message dit ce qui manque.

Il porte aussi, à part, de qui et de quoi il s'agit — utilisateur, passerelle,
clé, permission, agent —, pour que le journal d'audit le dise sans relire le
message (`access/audit.py`). `audited` est posé quand il y est écrit :
un refus qui traverse plusieurs couches n'y figure qu'une fois.

### Méthode `loom_ia.access.authz.AccessDenied.__init__`

```python
def __init__(self, message: str, *, actor: Actor | None=None, user: str | None=None, permission: str | None=None, agent: str | None=None) -> None
```

Le refus, avec de qui et de quoi il s'agit.

**Arguments :**

- `message` : Ce qui manque, en clair.
- `actor` : L'acteur refusé ; il donne l'utilisateur, la passerelle et la clé.
- `user` : L'utilisateur, à défaut d'acteur.
- `permission` : La permission qui manque.
- `agent` : L'agent visé.

## Classe `loom_ia.access.authz.Actor`

```python
@dataclass(frozen=True, slots=True)
class Actor
```

L'utilisateur au nom duquel on agit, ses rôles effectifs, et par où il est passé.

`roles` sont les rôles **effectifs** : ceux de l'utilisateur, restreints
à `act_as_roles` quand une passerelle agit en son nom (décision D-5b des utilisateurs et rôles). `key`
est la clé d'API qui a identifié la requête — celle de la passerelle, pour
un acteur endossé : ses `scopes` et ses `agents` restreignent encore.

**Attributs :**

- `user` : Utilisateur déclaré au nom duquel on agit.
- `tenant` : Son client.
- `roles` : Ses rôles effectifs, bornés par `act_as_roles` quand une passerelle agit en son nom.
- `via` : Utilisateur de la passerelle qui agit au nom de `user` (`as_user`).
- `key` : La clé d'API qui a identifié la requête, s'il y en a une : elle restreint, elle n'ajoute jamais.

### Méthode `loom_ia.access.authz.Actor.of`

```python
@classmethod
def of(cls, security: SecurityConfig, user: str, *, key: ApiKey | None=None) -> Self
```

L'acteur d'un utilisateur déclaré, identifié ou non par une clé.

`key` doit être une clé de cet utilisateur : une clé n'identifie
qu'une personne (décision D-4 des utilisateurs et rôles).

### Méthode `loom_ia.access.authz.Actor.endorse`

```python
def endorse(self, security: SecurityConfig, user: str) -> Actor
```

L'acteur `user`, au nom duquel cette passerelle agit (`as_user`, décisions D-5 et D-5b des utilisateurs et rôles).

La passerelle doit avoir `act_as` (sa clé comprise), viser un
utilisateur de son client, et ne peut exercer en son nom que les rôles
de `act_as_roles` : si Paul est gérant et assistant, une passerelle
bornée à `assistante` n'exerce que ce rôle-là. Pas d'endossement en
chaîne : la config refuse qu'`act_as_roles` cite un rôle qui a
`act_as`, et un acteur déjà endossé ne peut pas endosser.

### Méthode `loom_ia.access.authz.Actor.signature`

```python
@property
def signature(self) -> str
```

Comment l'acteur se nomme dans un message : `marie (via app-crm)`.

## Classe `loom_ia.access.authz.Approvable`

```python
@dataclass(frozen=True, slots=True)
class Approvable
```

Ce qu'une demande d'approbation dit de qui peut la trancher (§ 4.4, § 5.3).

`approvers` et `self_approval` sont figés dans la demande quand elle
est écrite : une config changée ensuite ne change pas, en silence, qui
peut trancher. `launcher` est l'utilisateur du run racine qui l'a posée.

**Attributs :**

- `agent` : Agent du run qui a posé la demande : le sous-agent s'il y en a un (§ 5.2).
- `tool` : Outil de l'appel en attente.
- `approvers` : Rôles approbateurs ; `None` : tout rôle qui a `approve` sur l'agent.
- `self_approval` : `False` si l'agent interdit l'auto-approbation (décision D-7b des utilisateurs et rôles).
- `launcher` : Utilisateur du run racine qui a posé la demande : c'est de lui que dépend l'auto-approbation.

## Fonction `loom_ia.access.authz.require`

```python
def require(actor: Actor, permission: Permission, *, agent: AgentSpec | None=None, tenant: TenantId | None=None) -> tuple[AccessRole, ...]
```

Les rôles par lesquels l'acteur a cette permission sur l'agent ; `AccessDenied` sinon.

Règles 1 à 4 du § 4.3 : le client, un rôle qui porte la permission et
vaut pour l'agent, la liste `access` de l'agent pour `run`,
`answer` et `cancel`, et ce que la clé laisse. Sans agent (lister les
sessions d'un client), seuls comptent les rôles qui ne sont bornés à aucun
agent, et une clé qui ne l'est pas non plus.

`approve` se demande à `require_decision` : il dépend de la demande.

## Fonction `loom_ia.access.authz.require_decision`

```python
def require_decision(actor: Actor, asked: Approvable, *, tenant: TenantId | None=None, amend: bool=False) -> tuple[AccessRole, ...]
```

Les rôles par lesquels l'acteur peut trancher cette demande ; `AccessDenied` sinon.

Règles 5 à 8 du § 4.4, remplies par **un même rôle** : `approve` sur
l'agent, parmi les approbateurs figés, `self_approve` (et un agent qui
ne l'interdit pas) si l'acteur a lancé le run, `amend` s'il corrige les
arguments. Accorder et refuser demandent le même droit : refuser, c'est
aussi trancher.

## Fonction `loom_ia.access.authz.can`

```python
def can(actor: Actor, permission: Permission, *, agent: AgentSpec | None=None, tenant: TenantId | None=None) -> bool
```

`require`, sans lever : pour n'afficher une action qu'à qui peut la faire.

## Fonction `loom_ia.access.authz.can_decide`

```python
def can_decide(actor: Actor, asked: Approvable, *, tenant: TenantId | None=None, amend: bool=False) -> bool
```

`require_decision`, sans lever : le bouton « Approuver » pour qui peut s'en servir.

## Fonction `loom_ia.access.authz._same_tenant`

```python
def _same_tenant(actor: Actor, tenant: TenantId | None, permission: Permission, agent: str | None) -> None
```

Règle 1 : l'acteur n'agit que chez son client.

## Fonction `loom_ia.access.authz._covers`

```python
def _covers(role: AccessRole, agent: str | None) -> bool
```

Le rôle vaut-il ici ? Hors d'un agent, seul un rôle borné à aucun agent.

## Fonction `loom_ia.access.authz._listed_for`

```python
def _listed_for(agent: AgentSpec | None, permission: Permission) -> tuple[str, ...] | None
```

La liste `access` de l'agent pour cette permission, s'il en déclare une (règle 3).

## Fonction `loom_ia.access.authz._check_key`

```python
def _check_key(actor: Actor, refused: str, permissions: Sequence[Permission], agent: str | None, permission: Permission) -> None
```

Règle 4 : la clé qui a identifié la requête restreint, elle n'ajoute jamais (décision D-4 des utilisateurs et rôles).
