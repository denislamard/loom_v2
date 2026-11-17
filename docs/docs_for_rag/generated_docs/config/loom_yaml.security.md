# loom.yaml : la clé security

La clé `security` de `loom.yaml` et tout ce qu'elle contient.

## `security`

Qui agit sur loom, et ce que chacun peut faire (`security` ; décisions D-1 à D-5 des utilisateurs et rôles).

Au moins un rôle et un utilisateur sont exigés (décision D-1), et le fichier
des refus (décision D-9) : le contrôle est fait par `LoomConfig.check`,
pour que le message donne la config minimale plutôt que l'erreur générique
d'un champ manquant.

Exemple :

```
security:
  roles:
    - id: operateur
      permissions: [run, answer, read]
  users:
    - id: denis
      roles: [operateur]
  audit:
    path: .loom/audit.jsonl
```

Modèle Pydantic : `loom_ia.config.models.SecurityConfig`.

- `security.roles` (type `tuple[AccessRole, ...]` ; défaut : `[]`) : Rôles d'accès.
- `security.users` (type `tuple[UserSpec, ...]` ; défaut : `[]`) : Utilisateurs.
- `security.api_keys` (type `tuple[ApiKey, ...]` ; défaut : `[]`) : Clés d'API, par leur empreinte.
- `security.audit` (type `AuditConfig | null` ; défaut : `null`) : Fichier des refus d'accès.

## `security.roles[]`

Rôle d'accès : des permissions, éventuellement bornées à des agents (décision D-3 des utilisateurs et rôles).

À ne pas confondre avec les rôles LLM d'un agent (`roles:` dans
`agent.yaml`), qui délèguent à un modèle : un rôle d'accès dit ce qu'un
utilisateur peut faire sur loom, et ne se déclare que dans
`security.roles`.

Permissions : `run`, `answer`, `cancel`, `approve`,
`self_approve` (avec `approve`), `amend`, `read`, `read_content`,
`admin`, `act_as` (avec `act_as_roles`).

Modèle Pydantic : `loom_ia.config.models.AccessRole`.

- `security.roles[].id` (type `str` ; obligatoire ; bornes : motif `^[A-Za-z0-9_-]{1,64}$`) : Identifiant du rôle : lettres, chiffres, `_` et `-`.
- `security.roles[].description` (type `str` ; défaut : `""`) : Libellé affiché par `loom validate`.
- `security.roles[].permissions` (type `tuple[Permission, ...]` ; obligatoire ; bornes : longueur ≥ 1) : Les permissions ; une au moins.
- `security.roles[].agents` (type `tuple[str, ...]` ; défaut : `[]`) : Agents auxquels le rôle est borné ; vide signifie tous.
- `security.roles[].act_as_roles` (type `tuple[str, ...]` ; défaut : `[]`) : Rôles qu'une passerelle peut endosser (décision D-5b) : obligatoire avec `act_as`, interdit sans. Ils restreignent les rôles effectifs de l'utilisateur au nom duquel elle agit.

## `security.users[]`

Utilisateur : une personne ou un service qui agit sur loom (décisions D-1 et D-2 des utilisateurs et rôles).

Il porte les droits, par ses rôles d'accès, et le client au nom duquel il
agit. Une clé d'API ne fait que le reconnaître (décision D-4) : une
personne garde la même identité au journal, quel que soit l'accès qu'elle
emprunte.

Modèle Pydantic : `loom_ia.config.models.UserSpec`.

- `security.users[].id` (type `str` ; obligatoire ; bornes : motif `^[A-Za-z0-9_-]{1,64}$`) : Identifiant de l'utilisateur, écrit au journal (`by`) et donné à `--user`.
- `security.users[].name` (type `str | null` ; défaut : `null` ; bornes : longueur ≥ 1) : Nom affiché (journal, CLI) ; l'identifiant sinon.
- `security.users[].roles` (type `tuple[str, ...]` ; obligatoire ; bornes : longueur ≥ 1) : Ses rôles d'accès, déclarés dans `security.roles` ; un au moins.
- `security.users[].tenant` (type `loom_ia.core.model.ids.TenantId` ; défaut : `"default"`) : Client de l'utilisateur (`default` par défaut) : celui de toutes ses clés.

## `security.api_keys[]`

Clé déclarée dans la config, par son empreinte seulement (point 39).

Elle identifie un utilisateur (décision D-4 des utilisateurs et rôles) : le
client et les droits sont les siens. `scopes` et `agents` ne peuvent
que les restreindre. `loom keys create` donne la clé et le bloc à coller.

Modèle Pydantic : `loom_ia.config.models.ApiKey`.

- `security.api_keys[].id` (type `str` ; obligatoire ; bornes : longueur ≥ 1) : Identifiant de la clé, affiché par `loom validate`.
- `security.api_keys[].hash` (type `str` ; obligatoire) : Empreinte donnée par `loom keys create` : `sha256:` et 64 chiffres hexadécimaux minuscules, vérifiés au chargement.
- `security.api_keys[].user` (type `str` ; obligatoire ; bornes : motif `^[A-Za-z0-9_-]{1,64}$`) : Utilisateur que cette clé identifie, déclaré dans `security.users`.
- `security.api_keys[].rate_limit` (type `RateLimit | null` ; défaut : `null`) : Débit de cette clé (`per_minute`), vérifié par l'accès HTTP (point 39) ; `null` : aucun.
- `security.api_keys[].expires` (type `AwareDatetime | null` ; défaut : `null`) : Fin de validité, avec fuseau (jalon J5.2a) ; `null` : la clé ne périme pas. Une clé déjà expirée n'empêche pas le démarrage — elle est refusée à l'appel, et `loom validate` la signale. Une rotation, c'est deux clés déclarées en même temps, l'ancienne portant sa date de fin.
- `security.api_keys[].scopes` (type `tuple[Permission, ...] | null` ; défaut : `null`) : Permissions que la clé garde de celles de son utilisateur ; `null` : toutes. Une clé restreint, elle n'ajoute jamais (décision D-4).
- `security.api_keys[].agents` (type `tuple[str, ...]` ; défaut : `[]`) : Agents autorisés ; vide signifie tous.

## `security.api_keys[].rate_limit`

Débit d'une clé d'API, vérifié par l'accès HTTP (point 39) ; au-delà, 429 et `Retry-After`.

Se pose à la création de la clé (`loom keys create --rate-limit`).

Modèle Pydantic : `loom_ia.core.model.quota.RateLimit`.

- `security.api_keys[].rate_limit.per_minute` (type `int` ; obligatoire ; bornes : > 0) : Requêtes permises par minute à cette clé.

## `security.audit`

Le fichier où chaque refus d'accès est écrit (`security.audit` ; décision D-9 des utilisateurs et rôles, `access/audit.py`).

Une ligne JSON par refus, ajoutée par tous les accès — SDK, REST, MCP,
CLI — de tous les processus qui partagent la config. `path` est relatif
au fichier de config. `retention_days` borne l'âge des lignes, que
`loom retention` efface au-delà ; `null` (le défaut) les garde toutes.

Modèle Pydantic : `loom_ia.config.models.AuditConfig`.

- `security.audit.path` (type `Path` ; obligatoire) : Fichier des refus, relatif au fichier de config ; hors du dossier d'un journal.
- `security.audit.retention_days` (type `int | null` ; défaut : `null`) : Âge maximal des lignes, en jours ; `null` : toutes gardées.
