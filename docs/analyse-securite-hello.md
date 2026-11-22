# Canal console — contrat 2 : sécurité, liste des sessions, runs en cours (L-1, L-2, L-4)

22 novembre 2026 · Denis Lamard

> Spécification avant code. Elle s'appuie sur `main` de loom_v2 (version 2.0.24, non publiée) et sur `heddlo/docs/analyse_reception.md` (§ 2.7), qui demande ces trois changements. Les décisions prises le 22/11 sont intégrées. Les points encore ouverts sont au § 4.

## En bref

**Le besoin.** Heddlo, la console d'administration, a besoin de trois choses que loom 2.0.24 ne lui donne pas :

| N° | Changement | Pourquoi Heddlo en a besoin |
|---|---|---|
| L-1 | Au premier branchement, loom envoie les **runs non terminés** | Une approbation en attente depuis 9 h apparaît dès le branchement de 10 h |
| L-2 | loom envoie sa **sécurité** (rôles, utilisateurs, clés sans empreinte, accès des agents, passerelle) | Droits des comptes Heddlo par instance (lot 4 de Heddlo) |
| L-4 | Commande **`sessions.list`**, paginée | Liste complète des sessions sans les importer |

**Décisions du 22/11 :**
- Les trois changements sont faits ensemble (L-1 + L-2 + L-4).
- Le canal passe au **contrat 2**. loom sert les contrats 2 et 1 (`CONTRACTS = (2, 1)`). Une console qui ne connaît que le contrat 1 continue de marcher, sans sécurité ni `sessions.list`.
- loom reste le référentiel des utilisateurs et des rôles (R-19 de Heddlo) : la sécurité part vers la console en **lecture seule**. Rien n'en revient.

---

## 1. Fonctionnel

### 1.1 Contrat 2

Le `hello` part **avant** la négociation : il ne peut pas changer selon le contrat. Et `HelloPayload` refuse les champs inconnus (`DomainModel`) : un champ ajouté au `hello` ferait refuser loom par une console plus ancienne. La sécurité part donc dans un **nouveau message**, envoyé seulement quand le contrat 2 est retenu.

| Règle | Exemple |
|---|---|
| loom propose `contracts: [2, 1]` dans le `hello`. | — |
| La console retient le plus récent qu'elle sert. | Heddlo actuel retient 1 ; Heddlo du lot 4 retient 2 |
| En contrat 2, loom envoie `security` juste après `welcome`, avant tout lot d'événements. | — |
| En contrat 2, la commande `sessions.list` est acceptée. | — |
| En contrat 1, rien ne change : ni `security`, ni `sessions.list` (refusée avec le motif `unsupported_command`). | Une console v1 qui envoie `sessions.list` reçoit `refused` |
| L-1 ne dépend pas du contrat : c'est un comportement de l'exportateur. | — |

### 1.2 L-2 — message `security`

**Contenu.** Seulement ce qui concerne le client que la console sert (`console.tenant`) :

| Champ | Contenu | Source |
|---|---|---|
| `tenant` | Le client de la console | `console.tenant` |
| `users` | `id`, `name`, `roles` des utilisateurs de ce client | `security.users` |
| `roles` | `id`, `description`, `permissions`, `agents`, `act_as_roles` des rôles que ces utilisateurs portent | `security.roles` |
| `api_keys` | `id`, `user`, `scopes`, `agents`, `expires` des clés de ces utilisateurs. **Jamais l'empreinte (`hash`).** | `security.api_keys` |
| `agents` | Pour chaque agent du client : `name` et son bloc `access` (`run`, `answer`, `cancel`, `approve.default`, `approve.tools`, `self_approval`) | `agent.yaml` (`AgentAccess`) |
| `gateway` | `user`, `act_as_roles` (ceux de ses rôles qui ont `act_as`), `rate`, `max_ttl` ; `null` sans passerelle | `console.commands` |
| `fingerprint` | `sha256:` + empreinte du JSON canonique (clés triées) du document sans ce champ | Calculé |

**Règles.**

| Règle | Exemple |
|---|---|
| Le message part à **chaque** connexion en contrat 2 (reconnexion comprise). | Coupure réseau, reconnexion : la console reçoit à nouveau `security` |
| loom ne recharge pas sa config à chaud : la sécurité change au redémarrage, donc au `hello` suivant. | Retirer `paul` de loom.yaml puis redémarrer : le `security` suivant n'a plus `paul` |
| Deux process de la même instance avec des configs différentes envoient deux empreintes différentes. La console peut le signaler. | Le Bureau a deux process, l'un avec `paul`, l'autre sans |
| Aucun secret ne part : ni empreinte de clé, ni clé de la console, ni variable d'environnement. | — |
| Le message n'est pas acquitté : la connexion le porte, et une connexion neuve le renvoie. | — |

### 1.3 L-4 — commande `sessions.list`

| Règle | Exemple |
|---|---|
| Rend les sessions du client de la console, de la plus récemment écrite à la plus ancienne, comme `GET /v1/sessions` (`Loom.sessions`). | — |
| Chaque session : `session_id`, `last_seq`, `updated_at` (`SessionRecord`). | `{"session_id": "avant", "last_seq": 12, "updated_at": "…"}` |
| Paginée : `limit` (défaut 1 000, max 5 000), `after` = le jeton `next` de la page précédente. `next` vaut `null` sur la dernière page. | 2 300 sessions : 3 pages |
| Droits : `read` pour l'utilisateur endossé, comme le REST. | `--as paul`, rôle sans `read` → `refused`, `AccessDenied` |
| C'est une lecture : rejouée, elle se refait (`_READS`). | — |
| Une session écrite pendant la pagination remonte en tête : elle peut manquer aux pages suivantes. La console la reçoit de toute façon par l'export. | — |

### 1.4 L-1 — runs non terminés au premier démarrage

Aujourd'hui (Q-11 de `analyse-exortateur-loom.md`), au premier démarrage, le curseur part de la position actuelle de chaque session : rien d'antérieur ne part.

| Règle | Exemple |
|---|---|
| Au premier démarrage du curseur seulement (`cursor.load` rend `None`), loom cherche, dans chaque session du client, les **runs racine non terminés** : même règle que la reprise (`_unfinished` : racine, `kind: normal`, pas `finished`). | Un run en pause sur une approbation, un run lancé puis abandonné par un process mort |
| Pour une session qui en a, le curseur part juste avant le **premier événement du plus ancien** de ces runs. Le rattrapage habituel envoie ensuite tout ce qui suit. | Run démarré au `seq` 40, session à 55 : les événements 40 à 55 partent |
| Une session sans run non terminé part de sa position actuelle, comme aujourd'hui. | — |
| Les sous-runs et ce qui suit dans la session partent avec, puisque l'envoi va jusqu'au bout du journal. | — |
| Une session illisible est journalisée et passée, comme dans `recover()`. | — |
| Les exclusions (`console.export.exclude_events`) et le mode (`on_demand` / `push`) s'appliquent. | — |
| Les démarrages suivants ne changent pas : le curseur existe. | — |

### 1.5 Hors périmètre

| Sujet | Où |
|---|---|
| Modifier la sécurité de loom depuis la console | Évolution conjointe loom + Heddlo (R-19) |
| Droits des comptes Heddlo calculés depuis `security` | Lot 4 de Heddlo (`analyse_reception.md`) |
| Recharger la config à chaud | Non prévu |

### 1.6 Critères d'acceptation

1. Avec `loom console listen` (contrats 2 et 1) : le `hello` propose `[2, 1]`, le contrat 2 est retenu, puis `security` arrive avant le premier lot.
2. `security` liste les utilisateurs du client de la console et leurs rôles. Aucun `hash` de clé n'y figure. L'empreinte est stable d'un démarrage à l'autre et change si un rôle change.
3. Une console qui ne sert que le contrat 1 se connecte, ne reçoit pas `security`, et reçoit `refused` / `unsupported_command` pour `sessions.list`.
4. `sessions.list` rend toutes les sessions, page par page, sans doublon ni trou quand rien n'écrit pendant la pagination.
5. Un utilisateur sans `read` reçoit `refused` (`AccessDenied`), écrit au journal d'audit.
6. Un run en pause avant le tout premier branchement arrive à la console dès le premier branchement, avec ses demandes en attente. Une session dont tous les runs sont finis n'envoie rien.
7. Heddlo actuel (contrat 1) continue de fonctionner avec ce loom : essais de bout en bout des lots 1 à 3 de Heddlo verts.

---

## 2. Technique

### 2.1 Ce que le code fournit déjà

| Élément | Où | Usage |
|---|---|---|
| `CONTRACT`, `CONTRACTS`, `MessageType`, `CommandName`, `PAYLOADS`, `encode`/`decode`, `contract_schemas`, `write_schemas` | `console/contract.py` | Étendus pour le contrat 2 |
| `ConsoleChannel._handshake`, `ChannelSettings` | `console/channel.py` | Envoi de `security` après `welcome` |
| `Loom.start_console` (construit le `HelloPayload`) | `access/api.py` | Construit aussi `SecurityPayload` |
| `CommandExecutor._dispatch`, `_query`, `_require`, `_READS` | `console/commands.py` | `sessions.list` sur le modèle d'`events.query` |
| `Loom.sessions`, `EventStore.sessions`, `SessionRecord` | `access/api.py`, `core/ports/event_store.py` | Source de `sessions.list` |
| `ConsoleExporter._load` (Q-11), `_catch_up`, `_resend` | `console/exporter.py` | L-1 : point de départ du curseur |
| `_unfinished`, `_run_states`, `_UNREADABLE` | `access/api.py` | Règle des runs non terminés (L-1) |
| `SecurityConfig`, `AccessRole`, `UserSpec`, `ApiKey` | `config/models.py` | Source de `security` |
| `AgentAccess`, `ApproveAccess` | `agents/spec.py` | Accès des agents |
| `FakeConsole` | `testing/console_server.py` | Essais ; `loom console listen` |

### 2.2 Contrat

```python
CONTRACT: Final = 2
CONTRACTS: Final = (2, 1)

type MessageType = Literal[..., "security"]          # loom → console, contrat 2
type CommandName = Literal[..., "sessions.list"]     # contrat 2

class SecurityRole(DomainModel):
    id: str
    description: str = ""
    permissions: tuple[Permission, ...]
    agents: tuple[str, ...] = ()
    act_as_roles: tuple[str, ...] = ()

class SecurityUser(DomainModel):
    id: str
    name: str | None = None
    roles: tuple[str, ...]

class SecurityKey(DomainModel):                      # jamais le hash
    id: str
    user: str
    scopes: tuple[Permission, ...] | None = None
    agents: tuple[str, ...] = ()
    expires: AwareDatetime | None = None

class SecurityAgent(DomainModel):
    name: str
    run: tuple[str, ...] | None = None
    answer: tuple[str, ...] | None = None
    cancel: tuple[str, ...] | None = None
    approve_default: tuple[str, ...] | None = None
    approve_tools: dict[str, tuple[str, ...]] = {}
    self_approval: bool = True

class SecurityGateway(DomainModel):
    user: str
    act_as_roles: tuple[str, ...]
    rate: PositiveInt
    max_ttl: PositiveInt

class SecurityPayload(DomainModel):
    tenant: str
    roles: tuple[SecurityRole, ...]
    users: tuple[SecurityUser, ...]
    api_keys: tuple[SecurityKey, ...] = ()
    agents: tuple[SecurityAgent, ...] = ()
    gateway: SecurityGateway | None = None
    fingerprint: str                                   # "sha256:<64 hex>"
```

`sessions.list` : arguments `SessionsListArgs(limit: int = 1000 (1..5000), after: str | None)`, résultat `{"sessions": [SessionRecord…], "next": str | None}`.

### 2.3 Fichiers impactés

| Fichier | Changement |
|---|---|
| `console/contract.py` | Contrat 2, modèles `Security*`, `SecurityPayload`, `"security"`, `"sessions.list"` ; schémas par contrat (décision Q-3) |
| `console/schemas/v2/` | Nouveau, généré par `write_schemas` |
| `console/security.py` | Nouveau : `security_payload(config, agents, console) -> SecurityPayload`, `fingerprint(document)` |
| `console/channel.py` | `ChannelSettings.security` ; envoi après `welcome` si contrat ≥ 2 |
| `access/api.py` | `start_console` construit `SecurityPayload` ; `_unfinished` déplacée (voir ligne suivante) |
| `core/projections/run_state.py` | `unfinished_roots(events)` publique, reprise de `_unfinished` ; `recover()` l'appelle |
| `console/commands.py` | `SessionsListArgs`, `sessions.list`, refus `unsupported_command` en contrat 1 |
| `console/exporter.py` | `_load` : point de départ des sessions qui ont un run non terminé (L-1) |
| `testing/console_server.py` | `FakeConsole(contracts=…)` sert 2 et 1 ; note `security` |
| `access/cli/…` (`loom console listen`) | Option `--contract` *(nom à choisir)* pour essayer le contrat 1 ; affiche `security` |
| `CHANGELOG.md` | Entrée sous 2.0.24 |
| `docs/analyse-exortateur-loom.md` | Renvoi vers cette spec (§ 5, Q-11) |

### 2.4 Dépendances

Aucune nouvelle.

### 2.5 Côté Heddlo (pour mémoire, lot 4 de Heddlo)

- `pyproject.toml` : `loom-ia[console]` ≥ 2.0.24.
- Le point d'accès sert `(2, 1)` ; en contrat 2, il range `security` dans `security_snapshots` et l'empreinte dans `processes.security_fingerprint`.
- `sessions.list` alimente `loom_sessions` (lot 3 complété).

### 2.6 Stratégie de tests

| Niveau | Contenu |
|---|---|
| Unitaires contrat | Modèles `Security*` ; `decode` de `security` ; refus des champs inconnus ; schémas v2 à jour (essai existant, étendu) |
| Unitaires sécurité | Seuls les utilisateurs du client ; rôles portés ; aucun `hash` ; empreinte stable et sensible ; passerelle absente → `null` |
| Canal | `FakeConsole` contrat 2 : `security` avant le premier lot ; contrat 1 : pas de `security` ; reconnexion : `security` renvoyé |
| Commandes | `sessions.list` : pages, `next`, `limit` bornée, `after` invalide refusé, droits (`read`), audit, refus en contrat 1 |
| Exportateur | Premier démarrage : run en pause → envoyé depuis son premier événement ; runs finis → rien ; session illisible → passée ; démarrage suivant inchangé ; avec journal mémoire, JSONL et SQLite |
| De bout en bout | Un vrai `Loom` avec `FakeConsole` : run en pause avant `start_console`, puis branchement → `run.started` et `approval.requested` reçus |
| Non-régression | Tous les essais `test_console_*` ; `recover()` inchangé |

CI de loom_v2 (`.github/workflows/ci.yml`) : `ruff check`, `ruff format --check`, `pyright`, `lint-imports`, `pytest`.

---

## 3. Lots

| Lot | Contenu | Tests | Test utilisateur |
|---|---|---|---|
| **1 — Contrat 2 et `security` (L-2)** | Contrat 2 ; modèles `Security*` ; `console/security.py` ; envoi après `welcome` ; schémas v2 ; `FakeConsole` et `loom console listen` en 2 et 1 | Contrat, sécurité, canal | `examples/console` + `loom console listen` : `hello` avec `[2, 1]`, contrat 2, `security` affiché sans `hash` ; avec le contrat 1 forcé, pas de `security` ; Heddlo actuel se connecte toujours |
| **2 — `sessions.list` (L-4)** | `SessionsListArgs`, pagination par jeton, droits, refus en contrat 1 | Commandes | Dans `loom console listen`, taper la commande `sessions.list` avec `limit: 2` : deux sessions et un `next` ; refaire avec `after` |
| **3 — Runs non terminés au premier branchement (L-1)** | `unfinished_roots` dans `core/projections` ; `_load` de l'exportateur ; `recover()` sur la fonction déplacée | Exportateur, bout en bout, non-régression `recover()` | Effacer le curseur, lancer un run qui se met en pause (`examples/console`, agent `devis`), arrêter loom, démarrer l'écouteur, relancer loom : `run.started` et `approval.requested` arrivent |

Ensuite : lot 4 de Heddlo (droits par instance), qui lit `security` et utilise `sessions.list`.

---

## 4. Points de décision ouverts

**Q-1 : comment la sécurité arrive à la console**
- A. Message `security` poussé par loom après `welcome`, en contrat 2 (ce document).
- B. Commande `security.get` que la console demande. Elle exige une passerelle (`console.commands.user`) : une instance sans commandes ne donnerait pas sa sécurité.
- C. Champ dans le `hello`. Impossible sans casser les consoles existantes (`HelloPayload` refuse les champs inconnus, et le `hello` précède la négociation).

Recommandation : A.

**Q-2 : périmètre de la sécurité envoyée**
- A. Le client de la console seulement : ses utilisateurs, leurs rôles, leurs clés, ses agents.
- B. Toute la section `security` : les utilisateurs des autres clients partent aussi, alors que la console ne peut rien faire en leur nom (`endorse` refuse un autre client).

Recommandation : A.

**Q-3 : schémas publiés du contrat 1**
- A. `schemas/v1/` est figé tel quel ; `contract_schemas()` génère `schemas/v2/`, que l'essai vérifie. Un essai vérifie aussi que v1 n'est pas modifié.
- B. Générer v1 et v2 depuis des modèles séparés par contrat : deux jeux de modèles à tenir, pour deux messages de différence.

Recommandation : A.

**Q-4 : coût du premier démarrage (L-1)**
- A. Lire toutes les sessions du client une fois, comme `recover()` le fait déjà. Lourd sur un gros journal, mais une seule fois, dans la tâche de l'exportateur, sans retenir les runs.
- B. Seulement les sessions écrites depuis N jours, avec une clé `console.export.backfill_days` : coût borné, mais une approbation plus ancienne n'apparaît pas.

Recommandation : A.

**Q-5 : jeton de pagination de `sessions.list`**
- A. Jeton opaque `updated_at|session_id` (positionnement par clé) : stable quand des sessions s'ajoutent.
- B. Décalage (`offset`) : simple, mais une session écrite pendant la pagination décale tout et fait voir des doublons.

Recommandation : A.
