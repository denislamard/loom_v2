# loom_ia.config.models

Module `loom_ia.config.models` de loom_v2 (fichier `src/loom_ia/config/models.py`).

Schéma de la configuration (fonctions M1 et M2, points 35 et 50).

Les modèles Pydantic sont le schéma : le YAML n'est qu'une façon de les
remplir, et une configuration écrite en Python (fonction M2) les construit
directement. Tout champ inconnu est refusé ; une clé prévue pour une phase
suivante donne une erreur qui nomme cette phase.

Sous-ensemble des jalons J1 et J2 ; le schéma complet est dans
`docs/conception.md` §17.

`LoomConfig` est la racine de `loom.yaml` ; un agent se décrit à part
(`agents/spec.py`). `loom schema` publie le schéma JSON des deux, pour
l'éditeur.

## Constantes de `loom_ia.config.models`

- `SCHEMA_VERSION = 1` : Version du schéma de la configuration (`version`).
- `EVENT_BACKENDS = ('memory', 'jsonl', 'sqlite', 'postgres')` : Backends du journal (`storage.events.backend`).
- `IDEMPOTENCY_BACKENDS = ('journal', 'memory', 'sqlite', 'postgres', 'redis')` : Backends du magasin d'idempotence (`storage.idempotency.backend`).
- `SHARED_IDEMPOTENCY = ('sqlite', 'postgres', 'redis')` : Magasins qu'une clé métier peut exiger : partagés entre runs **et** durables. Le journal ne voit que son run, la mémoire que son process.
- `FILE_BACKENDS = ('jsonl', 'sqlite')` : Journaux rangés hors de la mémoire : ils donnent aussi le dossier des artefacts.
- `DURABLE_BACKENDS = ('jsonl', 'sqlite', 'postgres')` : Journaux qui survivent au process : ce qu'exige une approbation (point 28).
- `DSN_BACKENDS = ('postgres',)` : Stockages dont le raccordement passe par un DSN, jamais par un chemin.
- `QUEUE_BACKENDS = ('asyncio', 'rabbitmq')` : Backends de la file des tâches de fond (`storage.queue.backend`).
- `BUS_BACKENDS = ('memory', 'postgres', 'redis')` : Backends du bus entre process (`storage.bus.backend`).
- `REQUIRE_TOOL = 'loom.require_tool'` : Politique fournie qui impose un appel d'outil (`policies.builtin`) : la config ne dépend pas des politiques, d'où le nom écrit ici.
- `SHARED_BUSES = ('postgres', 'redis')` : Bus qui franchissent la frontière d'un process : eux seuls servent à quelque chose quand le service tourne à plusieurs.
- `BROKERED_QUEUES = ('rabbitmq',)` : Files servies par un courtier : les tâches tournent dans un autre process, celui de `loom worker`, et pas dans celui qui les met en file.
- `DEFAULT_DB_ROLE = 'loom_app'` : Rôle applicatif Postgres par défaut, celui que crée le DDL de loom. Écrit ici plutôt qu'importé : la config ne dépend pas d'un pilote de base.
- `ARTIFACTS_SUBDIR = '.artifacts'` : Dossier des artefacts sous celui du journal, quand la config n'en donne pas.
- `Permission = Literal['run', 'answer', 'cancel', 'approve', 'self_approve', 'amend', 'read', 'read_content', 'admin', 'act_as']` : Permission d'un rôle d'accès (décision D-3 des utilisateurs et rôles).
- `PERMISSIONS = ('run', 'answer', 'cancel', 'approve', 'self_approve', 'amend', 'read', 'read_content', 'admin', 'act_as')` : Ordre d'affichage (`loom validate`) et liste fermée des permissions (décision D-3 des utilisateurs et rôles).
- `ACCESS_NAME_PATTERN = AGENT_NAME_PATTERN` : Identifiants d'utilisateur et de rôle d'accès : le motif des noms d'agent. Ils s'écrivent au journal (`by`) et sur la ligne de commande (`--user`).
- `LOOPBACK_HOSTS = frozenset({'127.0.0.1', 'localhost', '::1'})` : Adresses de la boucle locale : les seules où `server.http.user` est permis (décision D-1b des utilisateurs et rôles).
- `MINIMAL_AUDIT = ' audit:\n path: data/audit.jsonl'` : Le bloc d'audit que les messages de config donnent à recopier (décision D-9 des utilisateurs et rôles).
- `MINIMAL_SECURITY = f'security:\n roles:\n - id: owner\n permissions: [run, answer, cancel, approve, self_approve, amend, read, read_conten…` : Ce que le message d'une config sans rôle ni utilisateur donne à recopier (décision D-1 des utilisateurs et rôles).
- `Profile = Literal['dev', 'prod']` : Profil de la config : `dev` assouplit, `prod` durcit.
- `PROFILES = ('dev', 'prod')` : Les profils connus.

## Classe `loom_ia.config.models.PostgresPoolSettings`

```python
class PostgresPoolSettings(DomainModel)
```

Taille et délais du pool de connexions d'un stockage Postgres.

Un client qui déclare son propre stockage a son propre pool : ces valeurs
s'appliquent à chacun. Les délais sont en secondes ; ils ne valaient pas
avant (une attente pouvait durer sans fin).

**Attributs :**

- `min_size` : Connexions ouvertes au moins (1 par défaut).
- `max_size` : Connexions ouvertes au plus (10 par défaut) ; au moins `min_size`.
- `command_timeout` : Durée maximale d'une instruction, en secondes (60 par défaut) ; au-delà, elle est interrompue.
- `lock_timeout` : Attente d'un verrou (journal d'une session, schéma) avant d'y renoncer, en secondes (10 par défaut).
- `acquire_timeout` : Attente d'une connexion libre quand les `max_size` sont prises, en secondes (30 par défaut).

## Classe `loom_ia.config.models.EventsStorage`

```python
class EventsStorage(DomainModel)
```

Journal des événements (`storage.events`).

Backends : `memory` (défaut, perdu à l'arrêt), `jsonl` (un fichier par
session sous `path`), `sqlite` (une base en fichier), `postgres`
(DSN lu dans `dsn_env`). `path` et `dsn_env` ne valent que pour leur
backend ; `pool` seulement pour `postgres`.

Exemple :

```
storage:
  events: {backend: jsonl, path: .loom/journal}
```

**Attributs :**

- `backend` : `memory`, `jsonl`, `sqlite` ou `postgres`.
- `path` : `jsonl` : dossier des journaux. `sqlite` : fichier de la base. Relatif au fichier de config ; obligatoire pour ces deux backends.
- `dsn_env` : `postgres` : **nom** de la variable d'environnement qui porte le DSN (obligatoire). La config ne porte jamais un secret, seulement où le lire (conception §16.3).
- `role` : `postgres` : rôle applicatif pris par chaque connexion (`loom_app` par défaut) : c'est à lui que s'applique la politique de lignes, et il n'a pas le droit de modifier un événement écrit. `null` s'en passe — la politique tient encore, par `FORCE`, mais le journal redevient modifiable.
- `pool` : `postgres` : taille et délais du pool.

### Méthode `loom_ia.config.models.EventsStorage._check_backend`

```python
@model_validator(mode='after')
def _check_backend(self) -> Self
```

Backend connu, et seulement les réglages qui valent pour lui.

**Lève :**

- `ValueError` : Backend inconnu ; `path` ou `dsn_env` manquant ou de trop ; `pool` hors de `postgres`.

### Méthode `loom_ia.config.models.EventsStorage.directory`

```python
@property
def directory(self) -> Path | None
```

Dossier du journal : celui des fichiers JSONL, celui de la base SQLite.

## Classe `loom_ia.config.models.ArtifactsStorage`

```python
class ArtifactsStorage(DomainModel)
```

Stockage des fichiers : pièces jointes, sorties d'outils, résultats déportés (fonction G2).

Sans `backend`, il suit le journal : dossier `.artifacts` sous celui
d'un journal `jsonl`, mémoire pour un journal `memory`.

**Attributs :**

- `backend` : `local` (dossier) ou `memory` ; `None` : suit le journal.
- `path` : Dossier du stockage `local`, relatif au fichier de config ; obligatoire quand le journal n'est pas en fichiers.

## Classe `loom_ia.config.models.IdempotencyStorage`

```python
class IdempotencyStorage(DomainModel)
```

Magasin des clés d'idempotence (`storage.idempotency` ; points 18 et 49).

`journal` (défaut) n'a pas de stockage propre : chaque run garde ses
enregistrements dans son journal, ce qui suffit aux clés techniques d'un
appel. `memory` voit tout un process, mais ne survit pas à sa
fermeture. Une clé **métier** exige d'être vue de partout et de durer :
seuls `sqlite`, `postgres` et `redis` s'en chargent, et le chargement
le vérifie.

**Attributs :**

- `backend` : `journal`, `memory`, `sqlite`, `postgres` ou `redis`.
- `path` : `sqlite` : fichier de la base (obligatoire), relatif au fichier de config. Sa propre base : ses écritures ne se disputent pas le verrou du journal.
- `dsn_env` : `postgres` : nom de la variable qui porte le DSN (obligatoire). Peut être celle du journal — les deux tables cohabitent sans se gêner, Postgres ne se verrouille pas par fichier.
- `url_env` : `redis` : nom de la variable qui porte l'URL du serveur.
- `role` : `postgres` : rôle applicatif des connexions (`loom_app` par défaut).
- `pool` : `postgres` : taille et délais du pool.

### Méthode `loom_ia.config.models.IdempotencyStorage._check_backend`

```python
@model_validator(mode='after')
def _check_backend(self) -> Self
```

Backend connu, et seulement les réglages qui valent pour lui.

**Lève :**

- `ValueError` : Backend inconnu ; `path`, `dsn_env` ou `url_env` manquant ou de trop ; `pool` hors de `postgres`.

### Méthode `loom_ia.config.models.IdempotencyStorage.shared`

```python
@property
def shared(self) -> bool
```

Vrai si ce magasin est vu de tous les runs et survit au process (point 49).

## Classe `loom_ia.config.models.QueueStorage`

```python
class QueueStorage(DomainModel)
```

File des tâches de fond : runs soumis, reprises, résumés (point 27).

`asyncio` exécute dans le process qui met en file : c'est le mode
librairie, et la durabilité vient du journal, pas de la file. `rabbitmq`
ne fait que **publier** — les tâches tournent dans les workers
(`loom worker`), et un process qui met en file sans worker derrière voit
ses tâches attendre.

**Attributs :**

- `backend` : `asyncio` (défaut) ou `rabbitmq`.
- `url_env` : `rabbitmq` : nom de la variable d'environnement qui porte l'URL du courtier (`amqp://…`), obligatoire. La config ne porte jamais un secret (conception §16.3).
- `max_running` : `asyncio` : tâches menées de front (16 par défaut) ; les suivantes attendent leur tour. Une reprise remet tous les runs laissés en plan d'un coup (point 27, fonction H3).

### Méthode `loom_ia.config.models.QueueStorage._check_backend`

```python
@model_validator(mode='after')
def _check_backend(self) -> Self
```

Backend connu ; `url_env` pour `rabbitmq` seulement, `max_running` pour `asyncio` seulement.

**Lève :**

- `ValueError` : Backend inconnu, ou réglage manquant ou de trop.

### Méthode `loom_ia.config.models.QueueStorage.brokered`

```python
@property
def brokered(self) -> bool
```

Vrai si les tâches tournent dans un autre process que celui qui les met en file.

## Classe `loom_ia.config.models.BusStorage`

```python
class BusStorage(DomainModel)
```

Bus des nouvelles d'écriture, entre les process d'un même service (point 5).

`memory` ne franchit aucune frontière : dans un seul process, le journal
remet déjà ses écritures à ses abonnés. Les deux autres servent dès qu'un
`loom serve` doit suivre un run piloté par un `loom worker`.

**Attributs :**

- `backend` : `memory` (défaut), `postgres` ou `redis`.
- `dsn_env` : `postgres` : nom de la variable qui porte le DSN — celle du journal si c'est la même base.
- `url_env` : `redis` : nom de la variable qui porte l'URL.

### Méthode `loom_ia.config.models.BusStorage._check_backend`

```python
@model_validator(mode='after')
def _check_backend(self) -> Self
```

Backend connu ; `dsn_env` pour `postgres`, `url_env` pour `redis`, rien pour `memory`.

**Lève :**

- `ValueError` : Backend inconnu, ou variable manquante ou de trop.

### Méthode `loom_ia.config.models.BusStorage.shared`

```python
@property
def shared(self) -> bool
```

Vrai si ce bus porte les nouvelles au-delà de ce process.

### Méthode `loom_ia.config.models.BusStorage.variable`

```python
@property
def variable(self) -> str | None
```

Nom de la variable d'environnement qui porte le raccordement.

## Classe `loom_ia.config.models.EncryptionStorage`

```python
class EncryptionStorage(DomainModel)
```

Sceau des contenus au repos, avec la clé de chaque client (`storage.encryption` ; point 30, conception §11.5).

Déclarer ce bloc scelle la **charge** de chaque événement écrit et les
**octets** de chaque fichier rangé. L'enveloppe du journal reste en clair
— identifiants, horodatage, type, statut, agent, facettes —, si bien que
tout ce qui se filtre continue de se filtrer ; ce qui part, c'est le
contenu.

`keys` ne porte pas de clés mais des **noms de secrets**, comme partout
ailleurs dans la config. Chaque client redirige ce nom vers sa variable
(`secrets: {journal_key: MARTIN_JOURNAL_KEY}`) : c'est ainsi qu'une clé
est propre à un client, et effacer sa variable rend son journal illisible
pour de bon — *crypto-shredding* —, sans toucher à celui du voisin.

Plusieurs noms, dans l'ordre : le premier ferme, tous ouvrent. C'est le
renouvellement d'une clé, l'ancienne restant le temps que les anciens
journaux servent.

**Attributs :**

- `keys` : Noms des secrets qui portent les clés, un au moins ; la première scelle, les suivantes ne font que lire (rotation).

## Classe `loom_ia.config.models.RetentionStorage`

```python
class RetentionStorage(DomainModel)
```

Combien de temps une session dormante est gardée (`storage.retention` ; point 30, conception §11.5).

`events_days` est un nombre de jours **depuis la dernière écriture** d'une
session. Au-delà, la session part en entier — journal, fichiers, clés
d'idempotence —, comme une suppression RGPD demandée à la main. `null`
(le défaut) n'efface rien : c'est le comportement de toujours.

La borne se décide sur la **marque** d'une session (sa dernière écriture),
jamais sur son contenu : c'est ce qui permet d'effacer un journal scellé
dont la clé a disparu (jalon J5.5b) — là justement où la place est perdue si on ne
peut pas la reprendre. Une session **est donc effacée même si un run y
attendait une approbation** : une borne plus courte que le délai d'une
approbation détruit des approbations en attente, et c'est au déploiement de
ne pas s'y mettre.

Rien ne s'efface tout seul : `loom retention` balaie, et c'est la
plateforme qui le met à l'heure (comme les portes de 5.4c).

**Attributs :**

- `events_days` : Jours depuis la dernière écriture d'une session au-delà desquels elle est effacée ; `null` : jamais.

## Classe `loom_ia.config.models.StorageConfig`

```python
class StorageConfig(DomainModel)
```

Où loom range ce qu'il garde (`storage`), à la racine ou pour un client.

Un chiffrement exige un journal durable ; un journal `postgres` exige
de déclarer `artifacts`.

**Attributs :**

- `events` : Le journal des événements.
- `artifacts` : Le stockage des fichiers.
- `idempotency` : Le magasin d'idempotence.
- `queue` : La file des tâches de fond.
- `bus` : Le bus des nouvelles d'écriture entre process.
- `encryption` : Sceau des contenus au repos. Sans ce bloc, les contenus sont rangés en clair : c'est le journal de toujours, et l'isolation par client, les portées de clés et la suppression RGPD restent ce qui les protège.
- `retention` : Rétention des sessions dormantes ; sans borne, rien ne s'efface.

### Méthode `loom_ia.config.models.StorageConfig.artifacts_backend`

```python
@property
def artifacts_backend(self) -> Literal['local', 'memory']
```

Stockage d'artefacts effectif : celui déclaré, sinon celui qui suit le journal.

### Méthode `loom_ia.config.models.StorageConfig.artifacts_path`

```python
@property
def artifacts_path(self) -> Path | None
```

Dossier du stockage `local` : celui déclaré, sinon `.artifacts` sous le journal.

## Classe `loom_ia.config.models.StateLimits`

```python
class StateLimits(DomainModel)
```

Taille du state d'une session (`sessions.state` ; décision D7 du state de session), en octets de JSON compact.

Le state part avec chaque appel de l'orchestrateur qui le reçoit, et
l'estimation qui déclenche la compaction ne le compte pas : il reste petit.

**Attributs :**

- `max_value_bytes` : Taille maximale d'une valeur, écrite par un outil ou posée par l'application (1024 par défaut).
- `max_total_bytes` : Taille maximale de tout le state de la session (4096 par défaut).

## Classe `loom_ia.config.models.SessionMedia`

```python
class SessionMedia(DomainModel)
```

Fichiers des tours précédents dans les requêtes d'une session (fonction F4).

**Attributs :**

- `keep_images` : `all` (défaut) : un modèle vision revoit toutes les images de la session, à chaque appel, relues dans le stockage d'artefacts. `last_run` : une image d'un tour précédent est remplacée par une note (nom, type, taille) ; seules celles du run en cours partent en octets. Pas par défaut : pour une image rendue par un outil, le modèle peut alors désavouer la description qu'il en avait faite (essai de la fonction G3, 20/10).

## Classe `loom_ia.config.models.SessionsConfig`

```python
class SessionsConfig(DomainModel)
```

Vie d'une session (`sessions`) : historique matérialisé, compaction (fonctions F1 à F4), médias, state.

**Attributs :**

- `snapshot_every` : Événements ajoutés depuis le dernier marqueur avant qu'un snapshot de l'historique soit écrit (50 par défaut ; conception §11.2). Le relire coûte moins que de rejouer ce qu'il couvre ; l'écrire recopie l'historique dans le journal.
- `compaction` : Résumé des sessions longues. Sans ce bloc, une session n'est jamais résumée : elle grandit jusqu'à la fenêtre du modèle.
- `media` : Images des tours précédents dans les requêtes (fonction F4).
- `state` : Limites du state de la session ; ses clés sont déclarées par les agents.

## Classe `loom_ia.config.models.ToolsExecution`

```python
class ToolsExecution(DomainModel)
```

Exécution des outils (`execution.tools`), pour tous les agents ; un agent la surcharge par son `execution.tools`.

**Attributs :**

- `timeout` : Délai par défaut d'un outil, en secondes (30) ; `null` retire la limite.
- `validate_arguments` : Contrôle des arguments contre le schéma de l'outil (vrai par défaut).
- `offload_over` : Au-delà, en caractères, le résultat est déporté (50 000 par défaut ; point 16) ; `null` le désactive.
- `order` : Ordre des appels d'un même tour : `effects` (défaut), un appel à effet attend ceux qui le précèdent et ceux qui le suivent l'attendent ; `free`, tous partent ensemble (comme avant 2.0.20).
- `threads` : Threads du pool des outils synchrones (`loom-outil`), à part de celui d'asyncio, qui sert le journal et les fichiers (16 par défaut ; décision B-2 du délai et des threads). Un pool par process.
- `grace` : Après un délai dépassé, attente au plus du travail en thread d'un appel à effet avant de lancer les appels suivants du tour, en secondes (30) ; `null` : sans borne.

## Classe `loom_ia.config.models.ExecutionConfig`

```python
class ExecutionConfig(DomainModel)
```

Exécution des runs (`execution`) : outils, pièces jointes, arrêt, concession.

**Attributs :**

- `tools` : Exécution des outils.
- `attachments` : Pièces jointes acceptées à l'entrée d'un run (fonction G1) : formats d'image, taille et nombre maximaux.
- `shutdown_timeout` : Délai laissé aux tâches de fond (compaction) à la fermeture de l'instance, en secondes (30).
- `lease` : Durée de la concession prise sur un run par l'instance qui le pilote (point 27), en secondes ; elle est renouvelée au tiers tant qu'il tourne. Passée, un autre worker peut reprendre le run — ce qui n'arrive que si le porteur est mort.

## Fonction `loom_ia.config.models._declares`

```python
def _declares(data: object, key: str) -> bool
```

Vrai si le bloc brut (avant validation) porte cette clé.

## Classe `loom_ia.config.models.LoggingConfig`

```python
class LoggingConfig(DomainModel)
```

Logs du process (`telemetry.logging`).

**Attributs :**

- `level` : Niveau : `DEBUG`, `INFO` (défaut), `WARNING`, `ERROR`…
- `format` : `console` (texte, défaut) ou `json` (une ligne JSON par log).

## Classe `loom_ia.config.models.TelemetryConfig`

```python
class TelemetryConfig(DomainModel)
```

Logs, capture des exports, masquage et collecteurs (`telemetry` ; fonctions K3, K4 et K7, conception §17.7).

**Attributs :**

- `logging` : Logs du process.
- `capture` : Ce que les exports emportent : métadonnées ou contenu.
- `redaction` : Motifs masqués dans ce qui sort.
- `exporters` : Collecteurs OpenTelemetry. Sans collecteur, rien ne sort du journal que les logs.

## Classe `loom_ia.config.models.TenantSpec`

```python
class TenantSpec(DomainModel)
```

Un client, et la liste fermée de ce qu'il surcharge (fonction L1, points 33 et 34, conception §17.8).

Tout le reste de la configuration lui est commun : mêmes agents, mêmes
prompts, mêmes outils. Un client ne redéfinit que ce qui le distingue —
les modèles qu'il paie, les secrets qu'il apporte, ce qu'on lui permet.
Les prompts ne sont **pas** surchargeables (conception §6) : seules les
variables qu'ils citent le sont, ce qui garde la logique métier dans un
seul endroit.

Exemple :

```
tenants:
  - id: dupont
    agents: [support]
    models: {SONNET: HAIKU}
    secrets: {ANTHROPIC_API_KEY: DUPONT_ANTHROPIC_KEY}
    variables: {entreprise: Dupont SARL}
```

**Attributs :**

- `id` : Identifiant du client.
- `agents` : Agents que ce client peut lancer ; vide signifie tous ceux de la config.
- `tools_deny` : Outils retirés à ce client, sous le nom que voit le modèle (préfixe MCP compris) : un rôle, un sous-agent ou un outil qu'on ne lui ouvre pas.
- `models` : Correspondance des modèles : {modèle de la config: modèle de ce client}. Elle vaut partout — orchestrateur, rôles, juges, chaînes de secours, compaction —, la cible devant être déclarée dans `models`.
- `approvals` : Approbation imposée par outil, quoi qu'en dise sa déclaration (point 17) : c'est le client qui sait ce qui l'engage.
- `secrets` : Secrets : {nom attendu par la config: variable d'environnement de ce client}. Ce qui n'y est pas reste lu dans l'environnement commun.
- `variables` : Variables citées par les prompts système (``{{ entreprise }}``).
- `budgets` : Budgets de ce client : ils surchargent ceux de la racine clé par clé, et portent en plus ses plafonds par période (`tenant`, jalon J5.1b).
- `quotas` : Débit accordé à ce client, quel que soit l'accès emprunté (`runs_per_minute` ; fonction L3).
- `storage` : Stockage propre à ce client (isolation physique, `TenantRouter`) ; sans lui, celui de la racine, où seul le `tenant_id` le distingue. Le bloc déclare `events` : le défaut d'un journal est la mémoire, et un client qui n'aurait que `artifacts` perdrait le sien à chaque arrêt. `encryption` et `idempotency` ne s'y déclarent pas.
- `retention` : Rétention propre à ce client, à côté de `storage` et non dedans : le bloc `storage` dit **où** ses données vivent, et le déclarer lui donnerait son propre journal. Combien de temps elles vivent est une autre question — souvent contractuelle, et propre au client (jalon J5.5c).
- `telemetry` : Capture des exports propre à ce client (jalon J6.1a) : la sienne, clé par clé, sur celle de la racine. Ses collecteurs et ses motifs restent communs.

### Méthode `loom_ia.config.models.TenantSpec._check`

```python
@model_validator(mode='after')
def _check(self) -> Self
```

Ce qu'un client peut déclarer, et seulement cela.

**Lève :**

- `ValueError` : `id` vide ; `storage.encryption` ou `storage.idempotency` propres au client ; `storage` sans `events` ; nom d'agent invalide ; modèle remplacé par lui-même.

### Méthode `loom_ia.config.models.TenantSpec.allows`

```python
def allows(self, agent: str) -> bool
```

Vrai si ce client peut lancer cet agent (`agents` vide : tous).

## Classe `loom_ia.config.models.AccessRole`

```python
class AccessRole(DomainModel)
```

Rôle d'accès : des permissions, éventuellement bornées à des agents (décision D-3 des utilisateurs et rôles).

À ne pas confondre avec les rôles LLM d'un agent (`roles:` dans
`agent.yaml`), qui délèguent à un modèle : un rôle d'accès dit ce qu'un
utilisateur peut faire sur loom, et ne se déclare que dans
`security.roles`.

Permissions : `run`, `answer`, `cancel`, `approve`,
`self_approve` (avec `approve`), `amend`, `read`, `read_content`,
`admin`, `act_as` (avec `act_as_roles`).

**Attributs :**

- `id` : Identifiant du rôle : lettres, chiffres, `_` et `-`.
- `description` : Libellé affiché par `loom validate`.
- `permissions` : Les permissions ; une au moins.
- `agents` : Agents auxquels le rôle est borné ; vide signifie tous.
- `act_as_roles` : Rôles qu'une passerelle peut endosser (décision D-5b) : obligatoire avec `act_as`, interdit sans. Ils restreignent les rôles effectifs de l'utilisateur au nom duquel elle agit.

### Méthode `loom_ia.config.models.AccessRole._check`

```python
@model_validator(mode='after')
def _check(self) -> Self
```

Permissions cohérentes, agents et rôles endossés valides.

**Lève :**

- `ValueError` : `self_approve` sans `approve` ; `act_as` sans `act_as_roles` ou l'inverse ; un rôle qui s'endosse lui-même ; nom d'agent invalide ; doublon.

### Méthode `loom_ia.config.models.AccessRole.allows`

```python
def allows(self, agent: str) -> bool
```

Vrai si le rôle vaut pour cet agent.

## Classe `loom_ia.config.models.UserSpec`

```python
class UserSpec(DomainModel)
```

Utilisateur : une personne ou un service qui agit sur loom (décisions D-1 et D-2 des utilisateurs et rôles).

Il porte les droits, par ses rôles d'accès, et le client au nom duquel il
agit. Une clé d'API ne fait que le reconnaître (décision D-4) : une
personne garde la même identité au journal, quel que soit l'accès qu'elle
emprunte.

**Attributs :**

- `id` : Identifiant de l'utilisateur, écrit au journal (`by`) et donné à `--user`.
- `name` : Nom affiché (journal, CLI) ; l'identifiant sinon.
- `roles` : Ses rôles d'accès, déclarés dans `security.roles` ; un au moins.
- `tenant` : Client de l'utilisateur (`default` par défaut) : celui de toutes ses clés.

## Classe `loom_ia.config.models.ApiKey`

```python
class ApiKey(DomainModel)
```

Clé déclarée dans la config, par son empreinte seulement (point 39).

Elle identifie un utilisateur (décision D-4 des utilisateurs et rôles) : le
client et les droits sont les siens. `scopes` et `agents` ne peuvent
que les restreindre. `loom keys create` donne la clé et le bloc à coller.

**Attributs :**

- `id` : Identifiant de la clé, affiché par `loom validate`.
- `hash` : Empreinte donnée par `loom keys create` : `sha256:` et 64 chiffres hexadécimaux minuscules, vérifiés au chargement.
- `user` : Utilisateur que cette clé identifie, déclaré dans `security.users`.
- `rate_limit` : Débit de cette clé (`per_minute`), vérifié par l'accès HTTP (point 39) ; `null` : aucun.
- `expires` : Fin de validité, avec fuseau (jalon J5.2a) ; `null` : la clé ne périme pas. Une clé déjà expirée n'empêche pas le démarrage — elle est refusée à l'appel, et `loom validate` la signale. Une rotation, c'est deux clés déclarées en même temps, l'ancienne portant sa date de fin.
- `scopes` : Permissions que la clé garde de celles de son utilisateur ; `null` : toutes. Une clé restreint, elle n'ajoute jamais (décision D-4).
- `agents` : Agents autorisés ; vide signifie tous.

### Méthode `loom_ia.config.models.ApiKey.accepts`

```python
def accepts(self, key: str) -> bool
```

Vrai si la clé en clair a cette empreinte (comparaison à temps constant).

### Méthode `loom_ia.config.models.ApiKey.expired`

```python
def expired(self, now: datetime | None=None) -> bool
```

Vrai si la clé a passé sa date de fin.

### Méthode `loom_ia.config.models.ApiKey.allows`

```python
def allows(self, agent: str) -> bool
```

Vrai si la clé ouvre cet agent (`agents` vide : tous).

## Classe `loom_ia.config.models.AuditConfig`

```python
class AuditConfig(DomainModel)
```

Le fichier où chaque refus d'accès est écrit (`security.audit` ; décision D-9 des utilisateurs et rôles, `access/audit.py`).

Une ligne JSON par refus, ajoutée par tous les accès — SDK, REST, MCP,
CLI — de tous les processus qui partagent la config. `path` est relatif
au fichier de config. `retention_days` borne l'âge des lignes, que
`loom retention` efface au-delà ; `null` (le défaut) les garde toutes.

**Attributs :**

- `path` : Fichier des refus, relatif au fichier de config ; hors du dossier d'un journal.
- `retention_days` : Âge maximal des lignes, en jours ; `null` : toutes gardées.

## Classe `loom_ia.config.models.SecurityConfig`

```python
class SecurityConfig(DomainModel)
```

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

**Attributs :**

- `roles` : Rôles d'accès.
- `users` : Utilisateurs.
- `api_keys` : Clés d'API, par leur empreinte.
- `audit` : Fichier des refus d'accès.

### Méthode `loom_ia.config.models.SecurityConfig.role`

```python
def role(self, role_id: str) -> AccessRole
```

Rôle d'accès déclaré, par son identifiant.

**Arguments :**

- `role_id` : L'identifiant.

**Lève :**

- `KeyError` : Rôle non déclaré.

### Méthode `loom_ia.config.models.SecurityConfig.user`

```python
def user(self, user_id: str) -> UserSpec
```

Utilisateur déclaré, par son identifiant.

**Arguments :**

- `user_id` : L'identifiant.

**Lève :**

- `KeyError` : Utilisateur non déclaré.

### Méthode `loom_ia.config.models.SecurityConfig.roles_of`

```python
def roles_of(self, user_id: str) -> tuple[AccessRole, ...]
```

Rôles d'accès d'un utilisateur déclaré, dans l'ordre de sa déclaration.

**Arguments :**

- `user_id` : L'utilisateur.

### Méthode `loom_ia.config.models.SecurityConfig.permissions_of`

```python
def permissions_of(self, user_id: str) -> frozenset[Permission]
```

Permissions que l'un au moins des rôles de l'utilisateur porte, tous agents confondus.

**Arguments :**

- `user_id` : L'utilisateur.

## Classe `loom_ia.config.models.HttpServer`

```python
class HttpServer(DomainModel)
```

Serveur HTTP de `loom serve` (`server.http`).

**Attributs :**

- `host` : Adresse d'écoute (`127.0.0.1` par défaut).
- `port` : Port d'écoute (8000 par défaut).
- `base_path` : Préfixe commun des routes, par exemple `/loom` ; commence par `/`.
- `max_body_bytes` : Plafond du corps d'une requête (JSON, déclencheurs, MCP, envois de fichiers) : au-delà, 413 sans lire la suite. Par MCP, le SDK plafonne déjà le corps à 4 Mio avant tout code de Loom-IA : cette valeur ne peut que l'abaisser, pas le relever. Les fichiers y voyagent en base64 dans le JSON (4 octets pour 3), donc environ 3 Mio de fichier brut ; REST accepte jusqu'à ce plafond (défaut 128 Mio).
- `docs` : Documentation interactive (`/docs`, `/redoc`) et schéma (`/openapi.json`) : ils se lisent **sans clé**, et décrivent toutes les routes. `null` (défaut) : servis, sauf en profil strict (`prod`) ; `true` / `false` tranchent (essai de la fonction N2, 23/10 : ils répondaient 200 sans clé à côté d'une API protégée).
- `user` : Utilisateur au nom duquel une instance **locale sans clé** répond (décision D-1b) : l'usage local reste simple, et le journal nomme quelqu'un. Refusé hors de la boucle locale, avec des clés, avec le MCP HTTP, et en profil `prod` (`LoomConfig.check`).

## Classe `loom_ia.config.models.McpAccess`

```python
class McpAccess(DomainModel)
```

Serveur MCP de l'instance : stdio (`loom mcp`) et HTTP (jalon J5.2b).

**Attributs :**

- `file_roots` : Dossiers où un lien `file://` joint à un appel peut être lu ; aucun par défaut : les liens `file://` sont refusés. Relatifs au dossier de la config.
- `http` : Monte le serveur MCP dans l'application REST, sous `<base_path>/mcp`. Il expose des **outils** à un LLM tiers : il exige des clés d'API.
- `allowed_origins` : Origines acceptées par le transport (spec MCP, protection contre le rebinding DNS). Une requête **sans** `Origin` passe — un client natif n'en envoie pas ; avec un `Origin`, il doit figurer ici.
- `allowed_hosts` : Hôtes acceptés, **en plus** de l'adresse d'écoute que loom ajoute lui-même (`127.0.0.1:*`, `localhost:*`, et `server.http.host`). À renseigner derrière un nom de domaine ou un proxy.

## Classe `loom_ia.config.models.ServerConfig`

```python
class ServerConfig(DomainModel)
```

Serveurs de l'instance (`server`) : HTTP et MCP.

**Attributs :**

- `http` : Serveur HTTP (`loom serve`).
- `mcp` : Serveur MCP (`loom mcp`, et HTTP sous `/mcp`).

## Classe `loom_ia.config.models.TriggerSpec`

```python
class TriggerSpec(DomainModel)
```

Une porte d'entrée déclarée (`triggers`) : ce qu'un appel extérieur ouvre (fonction H6, jalon J5.4c).

Un déclencheur existe parce que celui qui appelle **ne connaît pas l'API
de loom** : un planificateur, un CRM, une passerelle de paiement envoient
leur charge à eux. La config dit donc quoi en faire — quel agent, et quel
message, rendu depuis la charge reçue.

Le client vient de la **clé d'API** et de nulle part ailleurs (point 34) : un
déclencheur ne le nomme pas, sans quoi une clé pourrait lancer un run au
nom d'un autre. La même liste `agents` d'une clé borne ce qu'elle peut
déclencher, puisqu'un déclencheur nomme son agent.

**Attributs :**

- `name` : Dernier segment de la route (`POST /v1/hooks/<name>`) : un identifiant sûr, qui sert aussi de facette au journal.
- `agent` : Agent lancé.
- `message` : Gabarit du message, rendu sur ``{payload, trigger, delivery}``. Une variable absente donne une chaîne vide (point 50) : une charge incomplète ne fait pas échouer la livraison, elle fait un message plus pauvre.
- `session` : Gabarit de la session, pour rattacher les livraisons d'un même sujet au même journal. Sans lui, chaque run a sa session, comme ailleurs.
- `delivery_header` : En-tête qui porte l'identifiant de livraison (`X-Delivery-Id`, `Stripe-Id`…). Il devient le `run_id`, si bien qu'une seconde livraison **retrouve** le run au lieu d'en ouvrir un autre.

### Méthode `loom_ia.config.models.TriggerSpec.templates`

```python
@property
def templates(self) -> tuple[str, ...]
```

Les gabarits à analyser au chargement.

## Classe `loom_ia.config.models.ToolSourceSpec`

```python
class ToolSourceSpec(DomainModel)
```

Une source d'outils fournie par un paquet installé (`tool_sources` ; jalon J6.4b, conception §7.3).

`entry_point` nomme un point d'entrée du groupe `loom_ia.tools` ; loom
n'importe son paquet que si un agent référence la source. `params` est
remis tel quel à la fabrique du paquet, qui le vérifie. La config ne porte
pas de secret : la fabrique reçoit la table des secrets du client.

**Attributs :**

- `name` : Nom dans la config (40 caractères au plus) ; il préfixe les outils (`carnet__chercher_devis`).
- `entry_point` : Nom du point d'entrée dans le groupe `loom_ia.tools`.
- `params` : Paramètres remis à la fabrique du paquet.
- `tools` : Déclarations par outil (nom court), sur celles que la source annonce.

## Classe `loom_ia.config.models.LoomConfig`

```python
class LoomConfig(DomainModel)
```

La configuration d'une instance : `loom.yaml` et les agents de `agents_dir`.

Chargée par `load_config` (profil, variables, fichiers d'agents,
prompts, skills) ou construite en Python (fonction M2). `check` contrôle
la cohérence d'ensemble : agents, modèles, serveurs MCP, sécurité,
clients, déclencheurs, state.

Exemple :

```
version: 1
models:
  - id: SONNET
    sdk: anthropic
    model: claude-sonnet-5-5
    api_key_env: ANTHROPIC_API_KEY
storage:
  events: {backend: jsonl, path: .loom/journal}
security:
  roles: [{id: operateur, permissions: [run, read]}]
  users: [{id: denis, roles: [operateur]}]
  audit: {path: .loom/audit.jsonl}
```

**Attributs :**

- `version` : Version du schéma ; 1.
- `profile` : Profil actif (fonction M4) : `dev` assouplit, `prod` durcit, absent ne change rien. Posé au chargement, où l'option et la variable d'environnement l'emportent sur le fichier.
- `profiles` : Surcharges partielles par profil, fusionnées au chargement : les objets en profondeur, les listes remplacées.
- `base_dir` : Dossier du fichier de config, posé au chargement : les modules voisins sont importables et les chemins relatifs s'y rapportent. Hors du schéma publié (`loom schema`) : on ne l'écrit pas (essai de la fonction M1, 21/10).
- `env_file` : Fichier d'environnement du projet, relatif au dossier de la config : lu par `loom` avant la config elle-même (`config/env_file.py`), au lieu du `.env` voisin. À la racine seulement, car il peut donner le profil.
- `imports` : Modules chargés au démarrage, qui enregistrent leurs outils et politiques (point 50).
- `agents_dir` : Dossier des fichiers d'agents (`agents` par défaut), un `<nom>.yaml` par agent.
- `resources_dir` : Dossier des prompts et skills (`prompts` par défaut), base des `system_file` et `skill_file`.
- `models` : Modèles, référencés par leur `id`.
- `mcp_servers` : Serveurs MCP, référencés par les agents (point 19).
- `tool_sources` : Sources d'outils fournies par des paquets installés (jalon J6.4b).
- `storage` : Stockages de la racine.
- `sessions` : Snapshots d'historique, compaction, médias et state ; l'agent interne `_compaction` en sort (voir `all_agents`).
- `execution` : Exécution des runs et des outils.
- `budgets` : Budgets par défaut des agents ; un agent les surcharge par son `budget` (fonction J4).
- `telemetry` : Logs, capture, masquage, collecteurs.
- `tenants` : Clients (fonction L1, point 33) : sans cette liste, seul `default` existe ; avec elle, la liste est fermée et un client inconnu est refusé.
- `security` : Rôles d'accès, utilisateurs, clés d'API, fichier des refus.
- `server` : Serveurs HTTP et MCP.
- `triggers` : Portes d'entrée déclarées (fonction H6) : ce qu'un appel extérieur ouvre.
- `agents` : Les agents : remplis depuis `agents_dir` au chargement, ou donnés directement en Python.

### Méthode `loom_ia.config.models.LoomConfig.strict`

```python
@property
def strict(self) -> bool
```

Vrai en profil `prod` : ce qui avertit ailleurs y est une erreur.

### Méthode `loom_ia.config.models.LoomConfig.docs_served`

```python
@property
def docs_served(self) -> bool
```

Vrai si `/docs`, `/redoc` et `/openapi.json` sont servis (`server.http.docs`).

### Méthode `loom_ia.config.models.LoomConfig.lax`

```python
@property
def lax(self) -> bool
```

Vrai en profil `dev` : ce qui refuse ailleurs y est un avertissement.

### Méthode `loom_ia.config.models.LoomConfig.check`

```python
def check(self) -> None
```

Contrôles de cohérence (fonction M5).

Détaché du validateur pour être rejoué sur la configuration résolue
d'un client : la correspondance des modèles d'un client doit passer
les mêmes contrôles que la configuration d'origine (fonction L1, point 34).

Lancés à la construction (validateur), et de nouveau sur la config
d'un client, sa correspondance des modèles appliquée.

**Lève :**

- `ValueError` : Version inconnue ; agent `_compaction` déclaré ; serveur MCP, source, modèle, agent, rôle d'accès ou utilisateur cité mais non déclaré ; capacité manquante (vision, outils) ; sécurité ou audit absents ; `server.http.user` mal employé ; state au-delà de ses limites ; capture `content` sans collecteur.

### Méthode `loom_ia.config.models.LoomConfig._check_state`

```python
def _check_state(self, agent: AgentSpec) -> None
```

Valeurs de la config qui iront dans le state : dans ses limites (décision D7 du state de session).

Une valeur par défaut est lue à chaque fois que sa clé manque : si les
valeurs par défaut ne tiennent pas ensemble, le state de la session est
trop gros avant même la première écriture.

### Méthode `loom_ia.config.models.LoomConfig._check_security`

```python
def _check_security(self) -> None
```

Rôles et utilisateurs obligatoires, et tout ce qui les cite déclaré (décisions D-1 à D-5b des utilisateurs et rôles).

Comme pour les clients, les agents ne sont contrôlés que lorsqu'il y en
a : la première validation ne connaît pas encore `agents_dir`.

### Méthode `loom_ia.config.models.LoomConfig._check_audit`

```python
def _check_audit(self, audit: AuditConfig | None) -> None
```

Le fichier des refus : obligatoire, et hors des dossiers de clients du journal.

### Méthode `loom_ia.config.models.LoomConfig._check_local_user`

```python
def _check_local_user(self, users: set[str]) -> None
```

`server.http.user` : une instance locale, sans clé, hors production (décision D-1b des utilisateurs et rôles).

### Méthode `loom_ia.config.models.LoomConfig._check_triggers`

```python
def _check_triggers(self) -> None
```

Déclencheurs nommés une fois, sur des agents qui existent, gabarits valides.

Comme pour les clients, les agents ne sont contrôlés que lorsqu'il y en
a : la première validation ne connaît pas encore `agents_dir`.

Un agent **non publié en REST** est accepté : un déclencheur est une
porte déclarée, pas l'API ouverte — c'est même la façon de n'ouvrir un
agent qu'à un planificateur.

### Méthode `loom_ia.config.models.LoomConfig._check_tenants`

```python
def _check_tenants(self) -> None
```

Clients déclarés une fois, sur des agents et des modèles qui existent (fonctions L1 et M5).

Les agents ne sont contrôlés que lorsqu'il y en a : le chargement
valide d'abord le fichier racine seul, pour savoir où lire
`agents_dir`, et la liste est alors vide. C'est la seconde
validation, agents lus, qui fait foi.

### Méthode `loom_ia.config.models.LoomConfig._check_chain`

```python
def _check_chain(self, label: str, chain: tuple[str, ...], *, vision: bool=False, tools: bool=False) -> None
```

Modèles d'une chaîne de secours déclarés, avec les capacités exigées (fonction B9, point 10).

### Méthode `loom_ia.config.models.LoomConfig._check_telemetry`

```python
def _check_telemetry(self) -> None
```

Un contenu capturé doit avoir où aller (jalon J6.1a).

`content` sans collecteur ne fait rien : le journal garde déjà tout,
et les logs ne portent jamais de contenu. Un réglage qui ne fait rien
se lit pourtant comme une décision — que le contenu d'un client part
quelque part —, d'où le refus plutôt que le silence.

### Méthode `loom_ia.config.models.LoomConfig.all_agents`

```python
@property
def all_agents(self) -> tuple[AgentSpec, ...]
```

Agents déclarés, plus l'agent interne de compaction s'il est configuré.

### Méthode `loom_ia.config.models.LoomConfig.budget_of`

```python
def budget_of(self, agent: str) -> Budgets
```

Budgets d'un agent : ceux de la racine, surchargés par son `budget`.

**Arguments :**

- `agent` : L'agent.

### Méthode `loom_ia.config.models.LoomConfig.tenant_ids`

```python
@property
def tenant_ids(self) -> tuple[TenantId, ...]
```

Clients déclarés ; `default` seul quand la config n'en nomme aucun (point 33).

### Méthode `loom_ia.config.models.LoomConfig.tenant_spec`

```python
def tenant_spec(self, tenant_id: TenantId) -> TenantSpec | None
```

Fiche d'un client déclaré ; `None` s'il ne surcharge rien.

**Arguments :**

- `tenant_id` : Le client.

### Méthode `loom_ia.config.models.LoomConfig.retention_days`

```python
def retention_days(self, tenant_id: TenantId) -> int | None
```

Borne de rétention de ce client : la sienne, sinon celle de la racine.

`None` : ce client ne perd rien. Un client peut aussi **annuler** la
règle commune en déclarant `retention: {events_days: null}`, ce qui se
lit comme ce que c'est — une exception assumée, écrite noir sur blanc.

**Arguments :**

- `tenant_id` : Le client.

**Renvoie :**

Les jours de `events_days` ; `None` : rien ne s'efface.

### Méthode `loom_ia.config.models.LoomConfig.capture_for`

```python
def capture_for(self, tenant_id: TenantId) -> CaptureConfig
```

Capture des exports pour ce client : la sienne, clé par clé, sur celle de la racine.

**Arguments :**

- `tenant_id` : Le client.

### Méthode `loom_ia.config.models.LoomConfig.tool_source`

```python
def tool_source(self, name: str) -> ToolSourceSpec
```

Définition d'une source d'outils par son nom.

**Arguments :**

- `name` : Nom de la source.

**Lève :**

- `KeyError` : Source non déclarée.

### Méthode `loom_ia.config.models.LoomConfig.mcp_server`

```python
def mcp_server(self, name: str) -> McpServerSpec
```

Définition d'un serveur MCP par son nom.

**Arguments :**

- `name` : Nom du serveur.

**Lève :**

- `KeyError` : Serveur non déclaré.

### Méthode `loom_ia.config.models.LoomConfig.model_spec`

```python
def model_spec(self, model_id: str) -> ModelSpec
```

Définition d'un modèle par son identifiant.

**Arguments :**

- `model_id` : Identifiant du modèle (`id`).

**Lève :**

- `KeyError` : Modèle non déclaré.

## Fonction `loom_ia.config.models._thinking`

```python
def _thinking(params: dict[str, JsonValue]) -> bool
```

Vrai si les réglages activent le raisonnement étendu d'Anthropic (`thinking`).
