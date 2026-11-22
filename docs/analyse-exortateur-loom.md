# Analyse d'impact — exportateur et canal bidirectionnel vers la console

*Analyse du 19/11/2026, branche `exporteur`, version 2.0.23. Elle confronte le document de conception « loom_v2 — Exportateur et canal bidirectionnel chiffré » (première version du 19/11/2026, ci-après **la conception**) au code de loom_v2. Rien n'est codé avant que les décisions de la § 9 soient tranchées.*

---

## 1. Objet et périmètre

Réaliser **tout le côté loom** du canal décrit par la conception : contrat versionné, connexion WSS sortante, exportateur des événements, commandes descendantes, configuration, `loom validate`, essais et exemple. L'appli web (la console) est hors périmètre ; un **écouteur** livré avec loom (`loom console listen`) affiche ce que loom envoie et sert de faux serveur aux essais.

Hors périmètre, comme dans la conception : la configuration poussée par la console (`config.push`), le multi-clients côté console, les coûts.

---

## 2. Décisions déjà retenues (19/11/2026)

| N° | Décision |
|---|---|
| D-1 | Les commandes passent par un **utilisateur loom déclaré**, la passerelle, dont un rôle a `act_as` et `act_as_roles` (mécanisme existant). La section ne porte que son nom (`commands.user`) ; sans lui, toute commande est refusée. |
| D-2 | **Curseur** : (client, session) → dernier `seq` acquitté, rangé **avec le journal**, dans le stockage de `storage.events` (révisé le 19/11 : d'abord prévu en fichier sous `workpath`). Reprise par `sessions()` et `read(after_seq)`. |
| D-3 | **Acquittement par lot** (identifiant du lot) ; loom avance alors le curseur de chaque session du lot. Ordre garanti dans une session, pas entre sessions. Dédoublonnage côté console par `(tenant, session, seq)`. |
| D-4 | **Saturation** (révisé le 20/11) : jamais de perte, quel que soit le journal — le journal en mémoire se relit lui aussi tant que le process vit. File pleine : la session est marquée « à relire » et relue dans le journal. `on_overflow` et le message `gap` sont retirés. |
| D-5 | **Contenu exporté** : `console.export.mode` — `on_demand` (métadonnées en continu, contenu lu à la demande par `events.query`) ou `push` (contenu poussé, masqué par `telemetry.redaction`). Absente, elle suit `capture.exports` du client (`metadata` → `on_demand`, `content` → `push`), qui reste ainsi seul maître de l'export OpenTelemetry. Plus `exclude_events`. `redact_fields` est abandonné. |
| D-6 | **Révisée le 20/11 : dès qu'un run s'exécute, la console doit le savoir.** Le canal s'ouvre dans tout process qui exécute des runs ou efface des sessions : `loom serve`, `loom worker`, `loom mcp` (pour leur durée) ; `loom run`, `resume`, `approve`, `reject`, `answer`, `sessions delete`, `retention` (pour la durée de la commande, avec au plus 5 s d'attente des acquittements à la fin, `CONSOLE_GRACE`) ; loom-chat. En librairie : `Loom.start_console()`, appelé par l'application. |
| D-7 | **Une connexion par process** ; le `hello` porte l'identifiant d'instance (déclaré, ou créé et gardé sous `workpath` — à déclarer dans `console.instance_id` quand plusieurs machines servent la même instance) et le `worker_id`. |
| D-8 | **Tâche asyncio**, pas de thread. Bibliothèque `websockets`, dans un nouvel extra `console`. |
| D-9 | `ws://` accepté **sur `localhost` en profil `dev` seulement** ; mTLS recommandé (avertissement) en `prod`, pas exigé. |
| D-10 | **`run.start` porte un `run_id` fixé par la console** ; plus une table en mémoire des commandes vues, gardée jusqu'à leur expiration. |
| D-11 | **Contrat 1** : les événements partent **au format des événements du REST** (`GET /v1/events`), masquage compris ; la couche de traduction le fige, et absorbera une évolution du format interne. |
| D-12 | Noms : section `console`, paquet `src/loom_ia/console/`, messages et commandes de la § 5, écouteur `loom console listen`, faux serveur `loom_ia/testing/console_server.py`. |
| D-13 | Aucun fichier **descendant** (pas de pièce jointe dans `run.start`). Fichiers **montants** pour décider d'une approbation : les trois cas de la § 6.7 (artefact rangé, fichier cité dans un dossier déclaré, aperçu d'un fichier pas encore écrit), plus le détail d'une demande en attente. |

---

## 3. Ce que le code fournit déjà

### 3.1 Journal et abonnement

- **`NotifyingEventStore.listen(sink, run_id=None, *, accept=None, own=False)`** (`adapters/stores/notifying.py`) remet chaque `Event` écrit, au moment de l'écriture, dans l'ordre. `own=True` écarte ce que le bus rapporte des autres process : **chaque process n'exporte que ce qu'il écrit**. Un abonné qui lève est journalisé, l'écriture n'échoue pas. Le `sink` est appelé depuis `append` : il ne doit rien attendre.
- **`RunExporter`** (`telemetry/export.py`) est le précédent direct : monté dans `Loom.__init__` par `create_run_exporter`, abonné en `own=True` dans une `ExitStack`, fermé dans `aclose(grace)`. L'exportateur de la console suit le même chemin.
- **`EventStore.sessions(tenant)`** rend chaque session avec son `last_seq` et son `updated_at` ; **`read(tenant, session, after_seq=…)`** relit la suite. Les quatre stockages (mémoire, JSONL, SQLite, Postgres) les servent.
- Le **sceau** (`SealingCodec`) s'applique au stockage : `listen` et `read` rendent des `Event` déchiffrés, en mémoire. L'exportateur n'écrit jamais de version en clair.

### 3.2 Contenu et masquage

- Chaque charge déclare ses **`content_fields`** et un **`export_content`** (faux pour les échanges bruts du modèle, qui ne partent jamais).
- `core/events/redaction.py` : `redacted(event)` rend l'événement en JSON privé de son contenu, avec la liste `redacted` des chemins retirés ; `contents(event)` extrait le seul contenu.
- `config.capture_for(tenant).exports` (`metadata` par défaut, ou `content`) et `config.telemetry.redaction.redactor()` (motifs fournis ou déclarés) décident, client par client, de ce qui part et de ce qui est masqué. C'est exactement ce que fait déjà l'export OpenTelemetry (`telemetry/spans.py`, `_event`).

### 3.3 Utilisateurs, rôles et passerelle

- `Actor.of(security, user)` et **`Actor.endorse(security, as_user)`** (`access/authz.py`) : la passerelle doit avoir `act_as`, viser un utilisateur de son client, et n'exerce en son nom que les rôles de `act_as_roles` ; pas d'endossement en chaîne. La config refuse `act_as` sans `act_as_roles`, et l'inverse (`AccessRole._check`).
- Le REST passe déjà par là (`as_user`, `access/http/app.py`), avec la signature « marie (via passerelle) » dans les décisions.

### 3.4 Couche de service

Les fonctions du REST sont des méthodes de `Loom` (`access/api.py`), qui contrôlent toutes `user=` :

| Commande du canal | Méthode | Contrôle déjà fait |
|---|---|---|
| `run.start` | `Loom.submit(agent, message, *, session_id, run_id, attachments, state, tenant, user)` | `run` sur l'agent |
| `question.answer` | `Loom.answer(run_id, *, answers, text, call_id, session_id, tenant_id, user)` | `answer` ; `UnknownQuestion` si rien n'attend (une deuxième réponse est refusée), `QuestionStale`, `InvalidAnswer` |
| `approval.decide` | `Loom.approve(…)` / `Loom.reject(…)` | approbateurs figés, `self_approve`, `access.self_approval: false`, `amend` ; `ApprovalStale` |
| `run.cancel` | `Loom.cancel(run_id, *, session_id, tenant_id, user)` | `cancel` sur l'agent |

**Aucune couche de service n'est à extraire.** Le canal n'ajoute aucune capacité que le REST n'offre pas (objectif 5 de la conception).

### 3.5 Profils, avertissements, validate

- `announce(config, warnings, refuse=None)` (`runtime/wiring.py`) : avertissement en `dev`, refus en `prod`, sauf arbitrage propre.
- `loom validate` (`access/cli/main.py`) bâtit un rapport par sections (`report.section(...)`) suivi d'un bloc d'avertissements : on y ajoute une section `CONSOLE`.
- `loom serve --reload` est **refusé en profil `prod`** ; chaque process relancé construit son propre `Loom`.

### 3.6 Ce qui manque

- Aucune bibliothèque WebSocket : `websockets` n'est ni dans `uv.lock` ni dans les extras ; uvicorn est installé sans elle.
- Aucun identifiant d'instance : seul le `worker_id` existe (`worker-<12 car.>`, tiré à chaque démarrage).

---

## 4. Écarts entre la conception et le code

| § conception | Ce qui ne tient pas | Ce que l'analyse retient |
|---|---|---|
| § 5, § 6 | « Position = numéro de séquence croissant **par instance** » : le `seq` est **par session** (`PRIMARY KEY (tenant_id, session_id, seq)`). JSONL et mémoire n'ont aucun ordre commun aux sessions. | Curseur par (client, session) (D-2) ; position transmise : `(tenant, session, seq)`. |
| § 2 | « Une seule connexion par instance » : un service peut compter plusieurs process (`--reload`, workers, plusieurs machines sur un journal Postgres), chacun avec son `worker_id`. | Une connexion par process (D-7). |
| § 4, § 7 | `commands.act_as_roles` dans la section : doublonne `act_as_roles` des rôles. | `commands.user`, un utilisateur déclaré (D-1). |
| § 4 | `redact_fields` : un mécanisme parallèle à `content_fields`. | Règles existantes (D-5). |
| § 5 | `ping` / `pong` dans le protocole : redondant avec les trames de contrôle WebSocket que `websockets` gère. | Supprimés de l'enveloppe ; pings natifs. |
| § 5 | `message.send` : dans loom, un message dans une session **est** un run dans cette session. | Fusionné dans `run.start` (avec `session_id`). |
| § 6 | « Mode librairie : appel explicite » alors que l'export OTel démarre dès que la config le déclare. | Appel explicite (D-6) : un `loom run` ponctuel n'ouvre pas de connexion. |
| § 1 | « Une appli sert un seul client » : une instance loom peut servir plusieurs clients (`tenants`), et le canal porterait leurs journaux à tous. | Voir Q-1 : une section `console` = un client. |

---

## 5. Protocole — contrat 1

> Le contrat 2 (message `security`, commande `sessions.list`) et l'envoi des runs non terminés au premier branchement sont décrits dans `docs/analyse-securite-hello.md`.

### 5.1 Enveloppe

Chaque message est un objet JSON, un message par trame texte :

| Champ | Rôle |
|---|---|
| `type` | `hello`, `welcome`, `events`, `ack`, `session.deleted`, `command`, `command.result`, `file.data`, `error`, `bye` |
| `id` | Identifiant unique du message (UUIDv7, comme les identifiants de loom) |
| `contract` | Contrat négocié ; absent du `hello`, qui propose |
| `payload` | Contenu propre au type |

Taille maximale d'une trame : `max_size` de `websockets`, borné dans la config (§ 7). Un type inconnu reçu par loom → `error` (`unknown_type`), la connexion reste ouverte.

### 5.2 Poignée de main

1. loom ouvre `wss://…` avec l'en-tête `Authorization: Bearer <clé>` ; jamais la clé dans l'URL.
2. `hello` : `instance_id`, `worker_id`, version de loom, `contracts: [1]`, client servi, agents servis (nom ; empreinte de leur config), présence des commandes.
3. `welcome` : contrat retenu. Ou `error` (`no_common_contract`, `unauthorized`) suivi de la fermeture ; loom écrit un avertissement clair et ne réessaie qu'avec le long délai (§ 6.5).
4. loom envoie d'abord le **rattrapage** (§ 6.3), puis le direct.

### 5.3 Montant

- **`events`** : `{batch_id, events: [...]}`. Chaque événement est au format du REST : `event_id`, `seq`, `ts`, `schema_version`, `tenant_id`, `session_id`, `run_id`, `root_run_id`, `span_id`, `parent_span_id`, `type`, `category`, `status`, `agent`, `role`, `facets`, `payload`. En mode métadonnées, la charge est privée de ses `content_fields` et porte `redacted`, la liste des chemins retirés (`core/events/redaction.py`, comme le REST sans `read_content`) ; en mode contenu, elle est complète, masquée par les motifs de `telemetry.redaction`.
- **`ack`** (console → loom) : `{batch_id}` une fois le lot enregistré de façon durable.
- **`bye`** : arrêt propre (arrêt de loom, fin de process `--reload`, clé révoquée côté console).

### 5.4 Descendant

`command` : `{command_id, expires_at, as_user, name, args}`.

| `name` | `args` | Méthode |
|---|---|---|
| `run.start` | `agent`, `message`, `run_id` (obligatoire, D-10), `session_id` ?, `state` ? | `Loom.submit` |
| `question.answer` | `run_id`, `session_id` ?, `call_id` ?, `answers` ou `text` | `Loom.answer` |
| `approval.decide` | `run_id`, `session_id` ?, `call_id` ?, `decision` (`approve` / `reject`), `reason` ?, `arguments` ? | `Loom.approve` / `Loom.reject` |
| `run.cancel` | `run_id`, `session_id` ? | `Loom.cancel` |
| `events.query` | critères d'`EventQuery` : `session_id` ?, `run_id` ?, `types` ?, `categories` ?, `status` ?, `agent` ?, `tool_name` ?, `since` ?, `until` ?, `after` ?, `limit` ? (1 000 au plus, `QUERY_MAX` ; client imposé : `console.tenant`) | `Loom.query`, comme `GET /v1/events` ; masqué selon `read_content` de l'utilisateur |
| `approval.get` | `run_id`, `session_id` ?, `call_id` ? | `Loom.approval_details` : détail des demandes en attente que l'utilisateur peut trancher (§ 6.7) |
| `file.get` | `session_id`, `file` | `Loom.session_file` (cas 1) |
| `approval.file` | `run_id`, `call_id`, `path`, `session_id` ? | `Loom.approval_file` (cas 2) |
| `approval.preview` | `run_id`, `call_id`, `session_id` ? | `Loom.approval_preview` (cas 3) ; son `result` ajoute `warnings`, les avertissements du rendu |

`command.result` : `{command_id, outcome: accepted | refused | error, reason?, result?}` (`result` : le `run_id`, les appels accordés…). Le déroulé remonte ensuite par les événements.

Pas de pièce jointe dans `run.start` (D-13) : la console administre, elle ne dépose pas de documents.

`file.data` (montant) : `{command_id, part, last, data}` — `data` en base64, morceaux de `files.chunk_size` ; le premier morceau porte aussi `name`, `media_type`, `size` et `sha256`. Le `command.result` (`accepted`) part avant le premier morceau, ou `refused` sans aucun morceau. Son `result` annonce le fichier : `{name, media_type, size, sha256, parts}`.

Arguments : un champ inconnu ou manquant donne `refused` (`invalid_args`). Les lectures (`events.query`, `approval.get`, `file.get`) rejouées se refont : rien n'est gardé pour elles.

### 5.5 Format des charges

Les 34 types de charge (`core/events/payloads.py`) ne sont pas réécrits un à un. Le contrat 1 fige :

- l'**enveloppe publique** ci-dessus, traduite explicitement (`console/translate.py`) ;
- la **liste des types** et, pour chacun, le **JSON Schema de sa charge**, généré depuis les modèles pydantic et commité dans `console/schemas/v1/`.

Un essai de la CI compare les schémas commités à ceux générés : un changement d'une charge interne casse la CI, et oblige à choisir entre traduire (même contrat) et ouvrir le contrat 2. C'est l'isolement demandé par la conception (§ 5, « couche de traduction »), sans recopier 2 000 lignes de modèles.

---

## 6. Exportateur et canal

### 6.1 Chemin d'un événement

```
append ─► NotifyingEventStore._offer ─► sink (own=True, filtre du client) ─► file bornée (put_nowait)
                                                                               │
                         tâche « loom-console » ◄────────────────────────────────┘
                           ├─ lots (taille ou délai), traduction, masquage
                           ├─ envoi, attente des ack, avance du curseur
                           └─ reconnexion, rattrapage depuis le journal
```

Le `sink` ne fait que filtrer (client, `exclude_events`) et `put_nowait` : il ne traduit ni ne masque (le masquage par motifs coûte, il est fait dans la tâche). Une exception y est déjà contenue par `_offer`.

### 6.2 Curseur

Le curseur suit le journal : il est rangé dans le stockage déclaré par `storage.events` — celui de `console.tenant` quand ce client a son propre stockage (isolation physique, `TenantRouter`). Restaurer le journal restaure le curseur qui va avec, et plusieurs machines sur un même journal partagent un seul curseur.

Contenu, quel que soit le stockage : par session, le dernier `seq` acquitté (`acked`) et un drapeau « effacée » (§ 6.8) ; pour le client, `flushed_at`, l'instant de la dernière écriture du curseur (§ 6.3).

| Journal | Curseur |
|---|---|
| `memory` | En mémoire ; rien ne survit à l'arrêt, comme le journal. |
| `jsonl` | Fichier `<path>/<client>/.console-cursor.json`, à côté des `<session>.jsonl`. Le store ne lit que `*.jsonl` (`SUFFIX`) : ce fichier n'est jamais pris pour une session. Écrit par remplacement atomique (fichier temporaire puis `os.replace`), sous verrou `fcntl` : deux process sur le même dossier **fusionnent** (maximum par session). |
| `sqlite` | Table `console_cursor` dans le même fichier, créée à la première connexion comme `events` (`CREATE TABLE IF NOT EXISTS`). |
| `postgres` | Table `console_cursor` dans la même base : composant de migration propre dans `loom_schema` (comme `idempotency`), SQL imprimé par `loom storage sql` avec les droits du rôle, colonne `tenant_id` et même politique de lignes que `events`. |

- **Port `ConsoleCursor`** (`console/cursor.py`) : `load(tenant)`, `advance(tenant, {session: seq})`, `mark_deleted`, `forget`. `advance` ne fait jamais reculer une position : maximum par session en fichier, `ON CONFLICT … DO UPDATE SET acked = GREATEST(…)` en SQL.
- **Écrit par lots** (délai court) après les `ack`, pas à chaque événement.
- **Monté par le câblage** d'après `storage.events.backend`, comme le journal (`create_event_store`).

### 6.3 Rattrapage

**Premier démarrage** (aucun curseur pour `console.tenant`) : le curseur est initialisé à la position actuelle de chaque session du client (`sessions()`, `acked = last_seq`), sans rien envoyer. Seul ce qui est écrit ensuite part ; l'historique antérieur se lit à la demande (Q-11).

À chaque (re)connexion, avant le direct :

1. sessions du curseur dont `acked < last_seq` ;
2. plus les sessions dont `updated_at ≥ flushed_at − marge` : ce qu'un process a écrit sans avoir eu le temps de garder la session dans le fichier (arrêt brutal) ;
3. pour chacune, `read(tenant, session, after_seq=acked)`, envoyé par lots ; pour une session de l'étape 2 absente du curseur, seuls partent les événements dont `ts ≥ flushed_at − marge`, pas tout son historique.

Avec plusieurs machines sur un même journal Postgres, l'étape 2 renvoie aussi, sur la fenêtre de la marge, des sessions écrites par d'autres : la console les dédoublonne (D-3). C'est borné et assumé.

Pendant le rattrapage, le direct s'accumule dans la file ; un événement déjà envoyé par le rattrapage est écarté (comparaison avec le `seq` envoyé par session).

### 6.4 File bornée et saturation

- `export.buffer_size` (défaut 10 000).
- **File pleine** : l'événement n'y entre pas ; sa session est marquée « à relire » et relue dans le journal après son dernier `seq` envoyé. Aucune perte, pour tous les journaux : le journal en mémoire garde aussi ses événements tant que le process vit (D-4, révisé).
- Jamais de run suspendu ni ralenti (objectif 1).

### 6.5 Connexion

- `websockets` client asyncio, `ssl.SSLContext` : TLS 1.2 minimum, vérification du certificat toujours active, `ca_file` facultatif, certificat client facultatif (mTLS) avec la clé privée lue dans l'environnement.
- Reconnexion : attente exponentielle avec part d'aléatoire, plafonnée (le modèle de `FOLLOW_BACKOFF` du bus). Refus de contrat ou clé refusée : long délai, et avertissement clair.
- Arrêt (`aclose`) : vidage de la file dans le délai de grâce, `bye`, fermeture ; ce qui n'est pas acquitté sera rattrapé au prochain démarrage (journal durable).

### 6.6 Commandes

- Contrôles dans l'ordre : `commands.user` déclaré, sinon `refused` (`commands_disabled`) ; type connu ; `expires_at` dans le futur et à moins de l'horizon maximal (§ 7) ; `command_id` jamais vu (table en mémoire jusqu'à l'expiration ; une commande vue rend **le même résultat**) ; `Actor.of(security, commands.user).endorse(security, as_user)`.
- Exécution par les méthodes de la § 3.4. `AccessDenied`, `UnknownQuestion`, `ApprovalStale`… deviennent `refused` avec leur motif ; toute autre exception devient `error`, est journalisée et contenue.
- Débit : borné par `commands.rate` (commandes par minute) ; au-delà, `refused` (`rate_limited`).
- Un `run.start` rejoué avec le même `run_id` (autre process après reconnexion) est refusé par le journal : D-10 tient même sans la table en mémoire.
- Les refus sont **audités** comme ceux du REST (`access/audit.py`), avec `access: console` et `via` = la passerelle.
- Motifs d'un `refused` : un code court, puis l'explication — `commands_disabled`, `rate_limited`, `expired`, `ttl_too_long`, `invalid_args`, `nothing_pending` (aucune demande en attente pour `approval.get`), `too_large` (au-delà de `files.max_size`) ; pour `approval.file` et `approval.preview`, ceux d'`ApprovalFileRefused` : `not_cited` (chemin que l'appel ne désigne pas), `unreadable` (introuvable, ou lien qui sort du dossier), `too_large`, `no_preview` (outil sans aperçu, ou appel qui n'écrit rien), `preview_failed`, `preview_timeout` ; sinon le nom de l'exception de `Loom` (`AccessDenied : …`, `UnknownRun : …`, `RunExists : …`).
- Chaque commande tourne dans sa propre tâche : une commande longue ne retient ni la réception ni l'export.

### 6.7 Fichiers et détail des approbations

Pour approuver ou refuser, il faut voir sur quoi porte la demande. Deux manques aujourd'hui :

- les `arguments` et le `reason` d'`approval.requested` sont du **contenu** (`content_fields`) : avec `capture.exports: metadata`, le défaut, la console ne voit que l'outil ;
- les outils fournis à dossiers (`loom.word`, `loom.excel`, `loom.pdf`, `loom.local_files`) écrivent dans leurs dossiers déclarés (`tools_builtin/_folders.py`), **pas** dans le stockage d'artefacts : aucun accès, REST compris, ne sait lire le fichier qu'une demande cite.

Quatre lectures, chacune une méthode de `Loom` servie **à la fois par le REST et par le canal** (le canal n'offre rien que le REST n'a pas, objectif 5). Toutes exigent `read_content` sur l'agent du run ; les trois liées à une demande exigent aussi qu'elle soit **en attente** et que l'utilisateur puisse la trancher (même contrôle qu'`approve`, sans rien écrire : `Loom.can`).

| Lecture | Ce qu'elle rend | Bornes |
|---|---|---|
| **Détail** (`approval.get`) | Les demandes en attente du run : outil, `arguments`, `reason`, approbateurs, échéance, et la liste des fichiers que chaque demande cite (cas 2) ou peut prévisualiser (cas 3). | Masquage par motifs (`telemetry.redaction`) appliqué comme à l'export. |
| **Cas 1 — artefact rangé** (`file.get`) | Les octets d'un fichier décrit par `artifact.stored` dans le journal de la session. | Existant : `Loom.session_file` (nom cherché dans **cette** session, jamais deviné). |
| **Cas 2 — fichier cité** (`approval.file`) | Les octets d'un fichier qui existe dans un dossier déclaré et que les arguments de la demande **désignent**. | Seulement un chemin que l'outil déclare comme fichier de l'appel ; résolu et contrôlé par `Folders.locate` / `Folders.real` (liens qui sortent du dossier refusés) ; taille au plus `files.max_size`. |
| **Cas 3 — aperçu** (`approval.preview`) | Le fichier que l'appel **produirait**, rendu en mémoire avec ses arguments exacts, sans rien écrire. | Seulement les outils qui savent le faire. Il lit les fichiers que l'appel lirait (images, sources), même soumis à approbation : ils sont couverts par la demande que l'approbateur examine (Q-8). |

**Côté outils**, deux protocoles facultatifs, sur le modèle d'`AssessedTool` (`core/ports/tool.py`) — un outil qui ne les a pas n'offre ni le cas 2 ni le cas 3, et rien ne change pour lui :

- `CitingTool` : `referenced_files(arguments) -> list[str]`, les chemins `<dossier>/<chemin>` que l'appel lit, remplace ou supprime, et `read_referenced(arguments, path, max_bytes) -> bytes`, qui lit l'un d'eux (`Folders.read_cited`). Implémenté par `loom.local_files` (`path` d'une lecture, d'une suppression, ou d'une écriture avec `overwrite` ; l'outil n'a ni copie ni déplacement de dossier à dossier), `DocumentTool` (fichier lu, document remplacé, images lues) et `loom.excel` (classeur lu, classeur remplacé, sources CSV/JSON). Le droit `read` n'est pas exigé : le fichier fait partie de la demande que l'approbateur examine. Un outil d'agent peut l'implémenter aussi ; sans lui, ses arguments restent affichés mais aucun fichier n'est servi — loom ne devine pas qu'une chaîne est un chemin ;
- `PreviewingTool` : `preview(arguments) -> Preview(name, media_type, data, warnings) | None`, le rendu sans écriture ; `None` pour un appel qui n'écrit rien. `DocumentTool._rendered` (`prepare` → `_render`, borné) sert à `_write` et à l'aperçu, de même qu'Excel `_built` (sources lues → `build_workbook` → `to_bytes` ; le recalcul vérifie une copie, il ne change pas le fichier écrit) ; `loom.local_files` rend le texte d'un `write` (`content`), rien pour une copie (`file`), qui se lit par `file.get`. L'aperçu s'arrête avant `write_bytes`.
- Les enveloppes de config (`ConfiguredTool`) sont traversées par `tools/configured.py` : `cited_files`, `cited_bytes`, `previews`, `previewed`.

**Rendu et sécurité de l'aperçu** : il tourne dans le process qui sert la demande, avec l'agent monté du client ; un délai et une taille maximale le bornent (`files.preview_timeout`, `files.max_size`). Un aperçu n'écrit rien, ni sur le disque, ni dans le journal : il ne vaut pas exécution, et l'appel approuvé est rendu de nouveau à l'exécution.

**Transport** : en morceaux (`file.data`, § 5.4), envoyés entre les lots d'événements : un gros fichier ne bloque ni le montant ni les commandes. Déchiffré en mémoire s'il est scellé (`SealingCodec`), jamais écrit en clair ; ne voyage que sous TLS.

**REST** : trois routes de plus à côté de `GET /v1/sessions/{id}/files/{file}` : `GET /v1/runs/{run_id}/approvals` (détail), `GET /v1/runs/{run_id}/approvals/{call_id}/file?path=…` (fichier désigné), `GET /v1/runs/{run_id}/approvals/{call_id}/preview` (aperçu, avertissements dans l'en-tête `X-Loom-Warnings`) ; `session_id` et `as_user` (passerelle) en paramètres. Mêmes droits, vérifiés par les méthodes de `Loom`. Un refus `ApprovalFileRefused` porte son `code` : 404 (`not_cited`, `unreadable`, `no_preview`), 413 (`too_large`), 422 (`preview_failed`), 504 (`preview_timeout`).

**Délai** : au-delà de `files.preview_timeout`, la commande est refusée ; le rendu en cours finit dans son fil (un fil ne s'interrompt pas), et son résultat est perdu.

### 6.8 Effacement des sessions

Le journal n'est pas seulement écrit : `Loom.delete_session` (RGPD, `loom sessions delete`, `DELETE /v1/sessions/{id}`) et `Loom.apply_retention` (`storage.retention`) l'effacent physiquement, sans trace. La console doit effacer sa copie.

- **Message `session.deleted`** `{tenant, session}`, acquitté par la console comme un lot ; la session sort alors du curseur.
- **Effacement dans le process qui porte le canal** (REST, rétention de `loom serve`) : envoyé tout de suite, depuis `delete_session`.
- **Effacement dans un autre process** (`loom sessions delete` en ligne de commande, sans canal) : à chaque connexion, puis à intervalles réguliers, une session présente dans le curseur mais absente de `sessions()` est déclarée effacée.
- Un `session.deleted` non acquitté est renvoyé à la connexion suivante : le curseur garde la session marquée « effacée » jusqu'à l'acquittement.

---

## 7. Configuration

```yaml
console:
  enable: true                  # obligatoire ; false : section gardée mais ignorée
  url: wss://console.exemple.fr/loom
  key_env: LOOM_CONSOLE_KEY
  tenant: default                 # Q-1
  instance_id: atelier-dupont     # facultatif ; sinon créé dans .loom/console/instance_id
  tls:
    ca_file: /etc/loom/ca.pem
    client_cert: /etc/loom/client.pem
    client_key_env: LOOM_CONSOLE_CERT_KEY
  commands:
    user: console-gateway         # utilisateur déclaré, rôle avec act_as ; absent : commandes refusées
    rate: 60                      # par minute
    max_ttl: 300                  # secondes : horizon maximal d'expires_at
  export:
    mode: on_demand               # ou push ; absente : suit capture.exports du client
    exclude_events: []
    buffer_size: 10000
    batch_size: 200
    batch_delay: 0.2              # secondes
  files:
    max_size: 26214400            # octets (25 Mo) : au-delà, refusé
    chunk_size: 262144            # octets par morceau (256 Ko)
    preview_timeout: 30           # secondes
```

Règles vérifiées au chargement (`config/console.py`, `ConsoleConfig`) :

- `enable` obligatoire dans la section. **`false`** : la console n'est pas montée et ses autres clés ne sont pas exigées ; celles qui sont écrites sont seulement contrôlées dans leur forme (type, valeurs permises, clé inconnue refusée), pour qu'une faute de frappe ne dorme pas jusqu'au jour où l'on passe à `true`. **`true`** : toutes les règles qui suivent s'appliquent.

- `url` en `wss://` ; `ws://` seulement vers `localhost`, `127.0.0.1` ou `::1` en profil `dev` (D-9) ; tout autre schéma refusé.
- `key_env`, `client_key_env` : des **noms** de variables, jamais des valeurs (comme `dsn_env`, `api_key_env`).
- `commands.user` : un utilisateur déclaré, du client `tenant`, dont un rôle a `act_as`.
- `exclude_events` : des types connus.
- `client_cert` sans `client_key_env`, ou l'inverse : refusé.

`announce` : en `prod`, `ws://` refusé ; absence de mTLS → avertissement (D-9) ; variable de la clé vide → avertissement en `dev`, refus en `prod`.

`loom validate`, section `CONSOLE` : adresse, client, présence de la variable de la clé (jamais sa valeur), TLS (CA, mTLS), commandes (utilisateur, rôles qu'il endosse) ou « désactivées », mode d'export (et s'il est hérité de `capture.exports`), filtrage, taille de file et politique. En mode `push`, un avertissement dit que le contenu des sessions est copié vers la console. Avec un journal durable, elle affiche aussi le **retard d'acquittement** : sessions et événements écrits mais pas encore acquittés, lus dans le curseur et `sessions()` (sans connexion à la console). Avec `enable: false` : « désactivée », et rien d'autre.

---

## 8. Impacts

### 8.1 Fichiers touchés

| Fichier | Nature |
|---|---|
| `pyproject.toml`, `uv.lock` | extra `console` (`websockets`) |
| `src/loom_ia/config/console.py` | nouveau : `ConsoleConfig` |
| `src/loom_ia/adapters/stores/sqlite.py`, `adapters/postgres/sql.py`, `adapters/stores/postgres.py` | table `console_cursor` ; composant de migration Postgres, droits, politique de lignes ; `loom storage sql` |
| `src/loom_ia/config/models.py` | champ `console: ConsoleConfig | None` dans `LoomConfig`, contrôles croisés (utilisateur, client) |
| `src/loom_ia/console/` | nouveau : `contract.py`, `translate.py`, `channel.py`, `exporter.py`, `cursor.py`, `commands.py`, `schemas/v1/` |
| `src/loom_ia/runtime/wiring.py` | `create_console()`, `console_warnings()` |
| `src/loom_ia/access/api.py` | `Loom.start_console()`, fermeture dans `aclose` |
| `src/loom_ia/access/http/app.py` | démarrage dans le `lifespan` quand `own` (comme `__aenter__`) |
| `src/loom_ia/access/cli/main.py`, `access/cli/console.py` | section `CONSOLE` de `validate` ; `loom console listen` |
| `src/loom_ia/testing/console_server.py` | nouveau : `FakeConsole` |
| `src/loom_ia/config/schema.py` | le JSON Schema de la config (`loom schema`) suit le nouveau champ |
| `src/loom_ia/core/ports/tool.py` | protocoles facultatifs `referenced_files` et `preview` |
| `src/loom_ia/tools_builtin/_document/tool.py`, `excel/tool.py`, `local_files.py` | implémentation des deux protocoles ; le rendu sort de `_write` sans changer d'effet |
| `src/loom_ia/access/api.py` | méthodes du détail, du fichier cité et de l'aperçu (§ 6.7) |
| `src/loom_ia/access/http/app.py`, `schemas.py` | trois routes REST |
| `tests/unit/test_console_*.py`, `tests/integration/test_console_*.py` | nouveaux (à plat dans `tests/unit/`, comme les autres essais, qui importent `conftest` directement) |
| `examples/console/` | nouveau |
| `docs/configuration.md`, `docs/howto`, formation | reportés après le gel, comme pour `source-config` |

### 8.2 Fonctionnel

- Sans section `console`, ou avec `enable: false`, **rien ne change** : aucun abonné, aucun import de `websockets`, aucune connexion.
- `loom validate`, `sessions list|show|export`, `report`, `schema`… : pas de canal ; ils n'exécutent aucun run. Les commandes qui en exécutent l'ouvrent (D-6 révisée). Une console injoignable ne retient une commande courte que 5 s au plus ; ce qui n'est pas parti est rattrapé à la prochaine connexion.

### 8.3 Technique

- **Coût par événement** : un filtre et un `put_nowait` dans `append`. Traduction, masquage et sérialisation dans la tâche dédiée. À mesurer sur un run bavard (lot 2).
- **Rattrapage au démarrage** : `sessions(tenant)` sur un gros journal JSONL lit un répertoire ; borné par le filtre `updated_at`.
- **Plusieurs process** : chacun sa connexion, son `worker_id`, sa part des événements (`own=True`) ; curseur partagé par le stockage du journal (§ 6.2).
- **Commande sur un run tenu par un autre process** : `approve` et `answer` remettent le pilotage en file ; c'est le comportement du REST, inchangé.

### 8.4 Non-régression

- Suite complète de la CI au vert, extra `console` absent puis présent.
- Essais d'`act_as` du REST inchangés.
- Export OpenTelemetry inchangé (les deux abonnés coexistent).
- `loom serve --reload` : chaque process ouvre et ferme son canal ; le curseur assure la continuité.

---

## 9. Questions à trancher

| N° | Question | Recommandation |
|---|---|---|
| ~~Q-1~~ | Une instance loom peut servir plusieurs clients, une console n'en sert qu'un. | **Tranché** : `console.tenant` (défaut `default`) ; seuls les événements de ce client partent, et `commands.user` doit en être. |
| ~~Q-2~~ | Format des événements dans le contrat 1. | **Tranché (D-11)** : format des événements du REST ; schémas des charges générés et figés par la CI (§ 5.5). |
| ~~Q-3~~ | Pièces jointes dans `run.start`. | **Tranché (D-13)** : aucun fichier descendant ; fichiers montants des trois cas de la § 6.7. |
| ~~Q-7~~ | Taille maximale d'un fichier servi et d'un morceau. | **Tranché** : réglables dans `loom.yaml` (`console.files.max_size`, `chunk_size`, `preview_timeout`), par défaut 25 Mo, 256 Ko et 30 s. |
| ~~Q-8~~ | Aperçu dont le rendu lit un fichier dont la lecture est soumise à approbation (image d'un dossier `read: always`). | **Tranché : autorisé.** L'appel ne fait qu'**une** demande d'approbation, qui couvre la lecture et l'écriture (`DocumentTool._assess_write`, motif rédigé par `Folders.rule`). L'aperçu lit pour l'approbateur, jamais pour le LLM, et l'appel reste suspendu. Même règle pour le cas 2. |
| ~~Q-9~~ | Noms des commandes du § 6.7. | **Tranché** : `approval.get`, `file.get`, `approval.file`, `approval.preview`. |
| ~~Q-4~~ | Audit des commandes refusées. | **Tranché** : oui, comme le REST. |
| ~~Q-5~~ | Clé de la console vide. | **Tranché** : ne vaut que si la section `console` est déclarée dans `loom.yaml` (sans elle, rien n'est contrôlé ni monté). Alors : refus de démarrer en `prod` ; avertissement en `dev`, canal non monté. |
| ~~Q-6~~ | Marge du rattrapage (§ 6.3, étape 2). | **Tranché** : 60 s, constante nommée, pas une clé de config. |
| ~~Q-11~~ | Ce qui part à la première connexion, quand le journal a déjà un historique. | **Tranché** : on part de l'activation (§ 6.3, premier démarrage) ; l'historique antérieur se lit **à la demande**, par la commande `events.query` (§ 5.4). |
| ~~Q-10~~ | Une session effacée chez loom (RGPD, `loom sessions delete`, `DELETE /v1/sessions/{id}`, `storage.retention`) reste dans la copie de la console. | **Tranché** : message `session.deleted` (§ 6.8). |

---

## 10. Lots

Quatre lots, chacun testable avec l'écouteur (`loom console listen`) et livrable seul.

| Lot | Contenu | Essais |
|---|---|---|
| 1 — Socle et connexion | Extra `console` ; `ConsoleConfig` (`enable`, toutes les clés, contrôles, règles du profil `prod`) ; `contract.py`, `translate.py`, schémas `v1` générés et figés par la CI ; `channel.py` (TLS, CA, mTLS, clé, `hello` / `welcome`, refus de contrat, reconnexion, `bye`) ; `FakeConsole` et `loom console listen` (affichage, `--raw`) ; section `CONSOLE` de `loom validate` ; ouverture du canal (connexion seule) par `Loom.start_console()` et `loom serve`, fermeture par `aclose` (`bye`) | Config valide et refusée, `enable: false` ; traduction et masquage ; schémas ; faux serveur : connexion, clé refusée, contrat refusé, `ws://` hors `localhost` refusé, reconnexion ; `loom validate` en `dev` et `prod` |
| 2 — Export des événements | `cursor.py` (mémoire, JSONL, SQLite, Postgres : table, migration, droits, `loom storage sql`) ; `exporter.py` (abonnement, file bornée, lots, `ack`, rattrapage, premier démarrage, saturation sans perte) ; `session.deleted` ; montage dans `Loom` et `loom serve`, `start_console()`, `--reload`, arrêt propre ; retard d'acquittement dans `loom validate` | Coupure au milieu d'un lot, reprise sans perte ni doublon, pour chaque stockage ; console injoignable au démarrage : les runs tournent ; saturation en mémoire et en durable ; deux process sur un même curseur ; effacement d'une session ; rechargement pendant un export ; mesure du coût sur un run bavard |
| 3 — Commandes et lecture | `commands.py` (contrôles, endossement `act_as`, expiration, idempotence, débit, audit) ; `run.start`, `question.answer`, `approval.decide`, `run.cancel`, `events.query`, `approval.get`, `file.get` et le transport `file.data` ; le détail sert `Loom.session`, déjà lu par `GET /v1/sessions/{id}` (la route dédiée, avec les fichiers cités, vient au lot 4) ; `listen` sait envoyer des commandes (une ligne JSON tapée : `name`, `as_user`, `args` ; identifiant et échéance d'une minute posés par l'écouteur) | Commande valide, expirée, rejouée, hors droits, inconnue ; deux réponses à une question ; `run.start` rejoué ; lecture sans `read_content` masquée ; fichier d'une autre session refusé ; fichier de 25 Mo sans bloquer les événements |
| 4 — Fichiers des approbations et finitions | Protocoles `CitingTool` (`referenced_files`, `read_referenced`) et `PreviewingTool` (`preview`) dans `local_files`, `DocumentTool` (Word, PDF) et `excel` ; `approval.file` et `approval.preview`, avec leurs méthodes et routes REST ; `examples/console/` (simulé, sans clé d'API) ; CHANGELOG | Chemin non cité refusé ; lien qui sort du dossier refusé ; outil sans les protocoles ; aperçu Word, PDF et Excel identique au fichier écrit après approbation (hors horodatages), rien d'écrit, délai dépassé ; aperçu qui lit une image d'un dossier en approbation (autorisé, Q-8) ; exemple exécuté ; aucune clé dans les logs ni les sorties |

Les lots 1 et 2 suffisent à brancher une console bêta en lecture ; l'écouteur montre la poignée de main dès le lot 1, les événements dès le lot 2.
