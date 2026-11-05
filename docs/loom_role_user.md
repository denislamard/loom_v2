# Conception — utilisateurs et rôles d'accès, approbation des runs loom_v2

4 novembre 2026 · Denis Lamard

> Document de conception, rédigé avant tout code. Il s'appuie sur la lecture de `main` (commit `63de779`, version 2.0.17). Aucun prototype n'a été écrit : les estimations d'impact viennent du code lu, pas d'un essai. Les décisions prises le 04/11 sont intégrées. Celles qui n'ont pas encore été discutées sont marquées « à confirmer » au § 10.

## En bref

**Le besoin.** Dans loom_v2, n'importe qui peut lancer un run et surtout **trancher une approbation**. On ajoute des **utilisateurs** et des **rôles d'accès**, déclarés dans la configuration, avec une **matrice des droits** qui dit qui peut faire quoi. Elle dit en particulier qui peut approuver quel appel de quel agent, et si l'on peut approuver ses propres demandes.

**Décisions structurantes (04/11) :**
- **Toute configuration déclare au moins un rôle et un utilisateur.** Il n'y a plus de mode ouvert : une config sans `security.roles` ni `security.users` est refusée au chargement (D-1).
- Utilisateurs et rôles se déclarent dans `loom.yaml`. `agent.yaml` ne fait que citer des rôles, dans un bloc `access`. Toutes les clés sont en anglais (D-2, D-3).
- Le `by` libre disparaît. Une passerelle agit au nom d'un utilisateur déclaré par `as_user`, ce qui demande la permission `act_as`, bornée aux rôles qu'elle peut endosser (D-5, D-5b).
- L'auto-approbation passe par une permission à part, `self_approve`. Un agent peut l'interdire (`access.self_approval: false`), jamais l'ouvrir (D-7, D-7b).
- `loom serve` en local sans clé agit au nom d'un utilisateur désigné, seulement sur la boucle locale et hors profil `prod` (D-1b).

**Le constat.** Il existe déjà une plomberie de droits, mais elle est attachée à la **clé d'API** et ne vaut que pour REST et MCP HTTP. La CLI, le SDK et le MCP stdio n'ont aucun contrôle. Le droit `approval.scope` est déclaré par l'agent et écrit au journal, mais personne ne le vérifie (§ 2).

**Verdict : faisable, sans toucher au cœur du moteur.** Le contrôle se fait dans `Loom` (`access/api.py`), par où passent déjà tous les accès. Le moteur ne change qu'en deux points :
- la demande d'approbation fige au journal les rôles qui peuvent la trancher ;
- l'approbateur en ligne (élicitation MCP) doit pouvoir **passer la main** à la pause appel par appel.

Le coût principal est ailleurs : **la migration**. Les droits étant obligatoires, chaque config doit être migrée : 10 `loom.yaml` dans le dépôt, les agents de test, le site de formation, et les 415 appels à `load_config` de 77 fichiers de tests.

| Point | Faisabilité | Difficulté |
| --- | --- | --- |
| Déclarer utilisateurs et rôles dans `loom.yaml`, obligatoires | Directe (pydantic, validation au chargement) | Faible |
| Bloc `access` dans `agent.yaml` | Directe | Faible |
| Clé d'API reliée à un utilisateur | Directe : la clé identifie, l'utilisateur porte les droits | Faible |
| Contrôle de run, answer, cancel, approve, reject dans `Loom` | Directe : tous les accès y passent | Moyenne (5 accès à suivre) |
| Approbateurs figés dans `approval.requested` | Champ facultatif | Faible |
| `self_approve` et `access.self_approval` | Le lanceur est au journal (`run.started`), à rendre fiable (`as_user`) | Moyenne |
| `as_user` borné par `act_as_roles` | Résolution de l'acteur dans la couche d'accès | Moyenne |
| Élicitation MCP qui passe la main | Petit changement dans `engine/executor.py:748-756` | Moyenne |
| Identité en CLI et en SDK | **Déclarative** : loom ne peut pas authentifier un process qui lit le journal directement | — (limite de principe, § 6.4) |
| Migration des configs et des tests | Mécanique, mais volumineuse | Moyenne |
| Voir seulement ses propres sessions | Filtrer les lectures du journal par utilisateur | Hors v1 (§ 11) |

---

## 1. Périmètre

**Dans le périmètre :**
- déclarer des utilisateurs et des rôles d'accès (obligatoire) ;
- une matrice des droits (rôle × permission), bornée par agent et, pour l'approbation, par outil ;
- l'auto-approbation, réglée par rôle et durcie par agent ;
- le contrôle par tous les accès : SDK, CLI, REST, MCP stdio, MCP HTTP, déclencheurs ;
- l'audit au journal : qui a tranché, au nom de qui, à quel titre ;
- `loom validate`, le schéma, la documentation, la migration des configs.

**Hors périmètre (§ 11) :**
- l'authentification par mot de passe, OIDC ou JWT ;
- les quorums ;
- la visibilité des sessions par utilisateur ;
- une granularité plus fine que l'outil.

---

## 2. Existant

### 2.1 Ce qui identifie un appelant aujourd'hui

| Accès | Identité | Droits vérifiés | Source |
| --- | --- | --- | --- |
| REST | Clé d'API (`Authorization: Bearer` ou `X-API-Key`) ; sans clé déclarée, instance ouverte | Portées de la clé, agents de la clé, client de la clé | `access/http/auth.py` |
| MCP HTTP | Même clé | `run` pour un agent, `read` pour `run_status` / `run_report` | `access/mcp_server/server.py:393` |
| MCP stdio | Aucune ; client fixé par `loom mcp --tenant` | Aucun : « tout lui est permis » | `access/caller.py` (docstring) |
| CLI | Aucune ; `--by` libre | Aucun | `access/cli/main.py:2125` |
| SDK | Aucune ; `by=` libre, `CallerContext.user_id` libre | Aucun | `access/api.py:2794` |

`Caller` (`access/caller.py`) est déjà l'abstraction commune à REST et MCP HTTP : `tenant`, `may(scope)`, `allows(agent)`, `masks`. C'est le point d'ancrage naturel.

### 2.2 Le chemin d'une approbation

1. **Demande.** L'exécuteur décide qu'un appel doit être approuvé (`_asks_approval`, `engine/executor.py:1442`), pour l'une de trois raisons : `approval: always` sur l'outil, une consigne du client (`tenants[].approvals`), ou une politique qui rend `Pause`. Il écrit `approval.requested` avec `scope = agent.approval.scope`, `approve` par défaut.
2. **Attente.** Le run passe en `PAUSED`. Une note dit au modèle des tours suivants qu'une action attend (`engine/pending.py`).
3. **Décision.** Elle arrive par l'une de ces voies :
   - `Loom.approve` / `Loom.reject` (`access/api.py:2794`), appelés par le SDK, `loom approve|reject` ou `POST /v1/runs/{id}/approve|reject` ;
   - un **approbateur en ligne** (`run(..., approver=…)`, élicitation MCP), qui tranche dans la boucle, sans pause.
4. **Audit.** `approval.granted` / `approval.rejected` portent `by`, et c'est tout l'audit qu'il y a.

La protection contre deux décisions sur le même appel existe déjà (`_AlreadyDecided`, `access/api.py:2916`). Elle reste valable.

### 2.3 Limites constatées

- **L-1 — Pas de contrôle hors REST et MCP HTTP.** Quiconque peut lancer `loom approve` ou importer `Loom` tranche n'importe quelle demande, au nom de qui il veut (`--by`).
- **L-2 — `approval.scope` n'est contrôlé par personne.** L'agent le déclare (`core/model/tooling.py:158`), l'événement le porte (`payloads.py:913`), mais la route REST exige `"approve"` en dur (`access/http/app.py:703`). Et `ApprovalSettings.scope` est un `str` libre, alors que `Scope` est une liste fermée : une valeur inventée passe le chargement sans qu'aucune clé ne puisse jamais la porter.
- **L-3 — Le `by` du corps REST remplace la clé.** Toute clé qui a la portée `approve` peut signer au nom de n'importe qui (`body.by or _signature(who)`, `app.py:709`).
- **L-4 — `user_id` vient du corps de la requête.** Sur `POST /v1/runs`, `RunRequest.user_id` est libre (`schemas.py:70`). Le journal ne sait donc pas de façon sûre qui a lancé un run, et on ne peut pas interdire l'auto-approbation.
- **L-5 — L'élicitation MCP fait approuver par le lanceur**, sans exiger `approve` (`server.py:560`).
- **L-6 — Une clé n'est pas une personne.** Deux humains derrière une passerelle signent avec la même clé ; une personne qui a deux clés a deux identités.
- **L-7 — Approuver sans voir.** Une clé `approve` sans `read_content` tranche des arguments qu'elle ne peut pas relire. `loom keys create` le signale (`main.py:2974`), mais pas la config.

---

## 3. Vocabulaire

**Collision traitée d'emblée.** Dans `agent.yaml`, `roles:` désigne déjà les **rôles LLM** (`RoleSpec`, `agents/spec.py:276`). Les rôles d'accès ne s'écrivent donc jamais `roles:` au niveau de l'agent.

| Terme | Sens | Où |
| --- | --- | --- |
| **utilisateur** (`user`) | Une personne ou un service qui agit sur loom. Identifiant stable, client, rôles. | `loom.yaml`, `security.users` |
| **rôle d'accès** (`role`) | Un ensemble nommé de permissions, éventuellement borné à des agents. | `loom.yaml`, `security.roles` |
| **permission** | Une action élémentaire (§ 4.1). Elle remplace et étend la « portée » actuelle. | — |
| **accès de l'agent** | Ce que l'agent exige : qui le lance, qui répond, qui arrête, qui approuve. | `agent.yaml`, bloc `access` |
| **acteur** | L'utilisateur au nom duquel une opération est faite, une fois résolu (clé, `as_user`, `--user`, `user=`). | interne (`Actor`) |
| **lanceur** | L'acteur du `run.started` du run racine. | journal |
| rôle LLM | Inchangé : `roles:` dans `agent.yaml`. | `agent.yaml` |

Les nouvelles classes ne s'appellent jamais `RoleSpec` ni `Role`. Proposition : `AccessRole`, `UserSpec`, `AgentAccess`, `Actor`. Dans la documentation, on écrit « rôle d'accès » quand le contexte ne suffit pas.

---

## 4. Modèle

### 4.1 Permissions

| Permission | Ce qu'elle ouvre | Aujourd'hui |
| --- | --- | --- |
| `run` | Lancer un run, continuer une session, appeler un déclencheur | portée `run` |
| `answer` | Répondre à une question `loom.ask` (route `answer`, ou message dans la session) | inclus dans `run` |
| `cancel` | Arrêter un run | inclus dans `run` |
| `approve` | Accorder ou refuser une demande d'approbation posée par le run **d'un autre**, dans la limite de l'`access` de l'agent | portée `approve` |
| `self_approve` | Avec `approve`, **dans le même rôle** : trancher aussi les demandes de **ses propres** runs | — |
| `amend` | Corriger les arguments d'un appel en l'accordant | inclus dans `approve` |
| `read` | Statuts, événements, rapports, sessions | portée `read` |
| `read_content` | Contenu des lectures (messages, réponses, arguments) | portée `read_content` |
| `admin` | `judges: skip`, effacement RGPD, rétention | portée `admin` |
| `act_as` | Agir au nom d'un utilisateur déclaré (passerelle), borné par `act_as_roles` | `by` libre de toute clé `approve` |

Pourquoi `answer`, `cancel`, `amend` et `self_approve` sont séparés :
- **`amend`** : corriger les arguments, c'est écrire l'action (changer un destinataire, un montant).
- **`answer`** : répondre n'autorise rien. Ce droit doit pouvoir être donné sans celui de lancer l'agent.
- **`cancel`** : arrêter un run est une action à part, et cela réserve la place, plus tard, à « arrêter seulement ses propres runs ».
- **`self_approve`** : la matrice `run` / `approve` suffit à séparer « qui lance » de « qui approuve » quand ce sont des rôles différents. `self_approve` règle le cas d'une personne qui a **les deux** : peut-elle valider son propre travail ? (§ 4.4)

### 4.2 Matrice des droits (exemple)

loom ne fournit **aucun rôle intégré** : la matrice se déclare. Matrice type pour une PME artisanale, donnée comme **exemple** (les noms sont à choisir) :

| Permission | `gerant` | `approbateur` | `assistante` | `lecteur` | `passerelle` |
| --- | :-: | :-: | :-: | :-: | :-: |
| `run` | ✓ | — | ✓ | — | ✓ |
| `answer` | ✓ | — | ✓ | — | ✓ |
| `cancel` | ✓ | — | ✓ | — | ✓ |
| `approve` | ✓ | ✓ | — | — | — |
| `self_approve` | ✓ | — | — | — | — |
| `amend` | ✓ | — | — | — | — |
| `read` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `read_content` | ✓ | ✓ | ✓ | — | ✓ |
| `admin` | ✓ | — | — | — | — |
| `act_as` | — | — | — | — | ✓ (`act_as_roles: [assistante, approbateur]`) |

Lecture de la matrice :
- `approbateur` a `read_content` : on ne tranche pas ce qu'on ne voit pas (L-7).
- `passerelle` n'approuve jamais elle-même. Elle porte la décision d'un utilisateur déclaré, dont les droits sont vérifiés (§ 6.1). Elle ne peut pas endosser `gerant`.
- **Artisan seul** : un seul rôle, avec toutes les permissions dont `self_approve` (§ 5.1, config minimale).

### 4.3 Règles générales

Un acteur **A** peut faire l'action **P** sur l'agent **G** si toutes ces conditions tiennent :

1. **A** est un utilisateur déclaré, et son client est celui du run ou de la session visés. Le client vient de l'utilisateur, jamais du corps d'une requête.
2. L'un des rôles **effectifs** de **A** porte **P**, et ce rôle n'est pas borné à d'autres agents que **G** (`roles[].agents`). Les rôles effectifs sont ceux de l'utilisateur, restreints par `act_as_roles` s'il est endossé par une passerelle (§ 6.1).
3. Si l'agent restreint **P** (`access.run`, `access.answer`, `access.cancel`), ce même rôle figure dans la liste.
4. Si l'action passe par une clé d'API, ses `scopes` et ses `agents`, s'ils sont présents, **restreignent** (intersection).

### 4.4 Trancher une approbation

Pour trancher la demande **R** (agent **G**, outil **T**), l'acteur **A** doit avoir un rôle effectif **ρ** qui remplit toutes ces conditions :

5. **ρ** porte `approve` et n'est pas borné à d'autres agents que **G**.
6. **ρ** figure parmi les **approbateurs figés** dans **R** (`approval.requested.approvers`, § 5.3). Si **R** n'en fige aucun, tout rôle qui a `approve` sur **G** convient.
7. **Si A est le lanceur** du run racine de **R** :
   - **ρ** porte aussi `self_approve`, **et**
   - l'agent n'a pas `access.self_approval: false`. L'agent ne peut que retirer ce droit, jamais l'ouvrir.
8. Si la décision corrige les arguments, **ρ** porte aussi `amend`.

Les conditions 5 à 8 portent sur **le même rôle**. Sinon, un rôle annexe qui aurait `self_approve` ouvrirait l'auto-approbation sur des demandes où il n'est pas approbateur.

**Qui est le lanceur :**
- c'est l'acteur du `run.started` du run racine ;
- derrière une passerelle, c'est l'utilisateur d'`as_user`, pas la passerelle ;
- pour un sous-agent, c'est le lanceur de la racine ;
- dans une session où plusieurs personnes écrivent, c'est celui du run qui a posé la demande.

**Décision sans `call_id`** (toutes les demandes du run) : seules celles que **A** peut trancher le sont. S'il n'en reste aucune, c'est un refus explicite (403 en REST, `AccessDenied` en SDK), pas un succès vide.

**Exemple de `self_approval: false`.** Deux associés, Denis et Paul, tous deux `gerant` (avec `self_approve`). Sur `secretariat`, chacun lance et approuve ses relances. Sur `paiements`, l'agent déclare `self_approval: false` : un virement lancé par Denis ne peut être approuvé que par Paul, et inversement. Sans ce réglage, il faudrait un rôle `gerant_paiements` sans `self_approve`, et borner `gerant` à tous les autres agents.

---

## 5. Configuration

### 5.1 `loom.yaml` : rôles et utilisateurs (obligatoires)

**Config minimale** (artisan seul), que le message d'erreur du chargement donne à recopier :

```yaml
security:
  roles:
    - id: owner
      permissions: [run, answer, cancel, approve, self_approve, amend, read, read_content, admin]
  users:
    - id: denis
      roles: [owner]
```

**Config complète** (exemple) :

```yaml
security:
  roles:
    - id: gerant
      description: Gérant — lance, approuve, administre
      permissions: [run, answer, cancel, approve, self_approve, amend, read, read_content, admin]
    - id: approbateur
      permissions: [approve, read, read_content]
    - id: assistante
      permissions: [run, answer, cancel, read, read_content]
      agents: [secretariat]               # borné à ces agents ; absent : tous
    - id: passerelle
      permissions: [run, answer, cancel, read, read_content, act_as]
      act_as_roles: [assistante, approbateur]

  users:
    - id: denis
      name: Denis Lamard                  # libellé affiché ; facultatif
      roles: [gerant]
    - id: marie
      roles: [assistante]
    - id: app-crm                         # un service est un utilisateur comme un autre
      roles: [passerelle]
      tenant: dupont-plomberie            # défaut : default

  api_keys:
    - id: crm
      hash: sha256:…
      user: app-crm                       # obligatoire : la clé identifie, l'utilisateur porte les droits
    - id: denis-mcp
      hash: sha256:…
      user: denis
      scopes: [run, read]                 # facultatif : restreint (une clé MCP qui n'approuve pas)

server:
  http:
    user: denis                           # D-1b : instance locale sans clé ; voir § 6.1
```

**Liste des clés (`loom.yaml`) :**

| Clé | Type | Obligatoire | Sens |
| --- | --- | --- | --- |
| `security.roles` | liste | oui, au moins 1 | Rôles d'accès |
| `security.roles[].id` | texte | oui | Identifiant unique |
| `security.roles[].description` | texte | non | Libellé affiché par `loom validate` |
| `security.roles[].permissions` | liste | oui, au moins 1 | Valeurs du § 4.1 |
| `security.roles[].agents` | liste | non (vide : tous) | Agents auxquels le rôle est borné |
| `security.roles[].act_as_roles` | liste | **oui si `act_as`**, interdite sinon | Rôles que ce rôle peut endosser |
| `security.users` | liste | oui, au moins 1 | Utilisateurs (personnes ou services) |
| `security.users[].id` | texte | oui | Identifiant unique, écrit au journal |
| `security.users[].name` | texte | non | Nom affiché |
| `security.users[].roles` | liste | oui, au moins 1 | Rôles déclarés dans `security.roles` |
| `security.users[].tenant` | texte | non (`default`) | Client de l'utilisateur |
| `security.api_keys[].user` | texte | **oui** (nouvelle) | Utilisateur que la clé identifie |
| `security.api_keys[].scopes` | liste | non | Ne fait plus que restreindre les permissions de l'utilisateur |
| `security.api_keys[].agents` | liste | non | Restreint, comme aujourd'hui |
| `security.api_keys[].tenant` | — | **retirée** | Vient de l'utilisateur |
| `server.http.user` | texte | non | Utilisateur d'une instance locale sans clé (D-1b) |

**Règles de chargement, toutes en erreur :**
- `security.roles` et `security.users` présents et non vides (D-1). Le message donne la config minimale ;
- identifiants d'utilisateur et de rôle uniques et sûrs (même motif que les noms d'agent) ;
- tout rôle cité (par un utilisateur, `act_as_roles` ou un `access` d'agent) doit être déclaré ;
- `permissions` est une liste fermée ;
- `self_approve` sans `approve` dans le même rôle : erreur (permission sans effet) ;
- `act_as` sans `act_as_roles`, ou `act_as_roles` sans `act_as` : erreur ;
- `act_as_roles` ne peut pas citer un rôle qui a `act_as` (pas d'endossement en chaîne) ;
- `users[].tenant` doit être déclaré si `tenants` existe ;
- chaque clé nomme un `user` déclaré ; une clé qui porte encore `tenant` est refusée, avec un message qui explique pourquoi ;
- `server.http.user` : utilisateur déclaré, `server.http.host` en boucle locale, aucune `api_keys`, `server.mcp.http: false`, et pas de profil `prod`.

**Pas d'utilisateurs dans `agent.yaml`.** Un utilisateur agit sur plusieurs agents : le déclarer dans un agent ferait autant de copies que d'agents. `agent.yaml` ne fait que **citer des rôles** (§ 5.2).

### 5.2 `agent.yaml` : l'accès de l'agent

```yaml
name: paiements

access:
  run: [gerant]                         # qui lance cet agent ; absent : tout rôle qui a `run`
  answer: [gerant]                      # qui répond à ses questions ; absent : tout rôle qui a `answer`
  cancel: [gerant]                      # qui arrête ses runs ; absent : tout rôle qui a `cancel`
  approve:
    default: [gerant]                   # approbateurs de toute demande de cet agent
    tools:                              # par outil, sous le nom que voit le modèle
      sql: [gerant]
      envoyer_email: [approbateur, gerant]
  self_approval: false                  # durcit : même un rôle avec self_approve ne valide pas ses demandes

approval:
  expires_in: 3600
  on_expiry: deny
  # `scope` est retirée : remplacée par access.approve
```

**Liste des clés (`agent.yaml`) :**

| Clé | Type | Obligatoire | Sens |
| --- | --- | --- | --- |
| `access` | bloc | non | Sans lui, les permissions des rôles suffisent |
| `access.run` | liste de rôles | non | Qui peut lancer l'agent |
| `access.answer` | liste de rôles | non | Qui répond à ses questions `loom.ask` |
| `access.cancel` | liste de rôles | non | Qui arrête ses runs |
| `access.approve.default` | liste de rôles | non | Approbateurs de toutes ses demandes |
| `access.approve.tools` | dictionnaire outil → rôles | non | Approbateurs par outil |
| `access.self_approval` | booléen | non (`true`) | `false` interdit l'auto-approbation sur cet agent ; `true` ne fait que laisser décider les rôles |
| `approval.scope` | — | **retirée** | Remplacée par `access.approve` |

Précisions :
- **Clé des outils** : le nom que voit le modèle, préfixe MCP compris, comme `tenants[].tools_deny`. C'est le `tool_name` de `approval.requested`.
- **Granularité** : l'outil, en v1. `delete` et `write` de `local_files`, ou deux tables de `loom.sql`, ont les mêmes approbateurs (§ 11).
- **Politique `Pause`** sur n'importe quel outil : les approbateurs sont ceux de l'outil visé, à défaut `default`.
- **Sous-agent** : ses demandes suivent l'`access` **du sous-agent**, `self_approval` compris. La décision se prend toujours sur la racine, comme aujourd'hui.
- **Consigne d'un client** (`tenants[].approvals`) : elle peut imposer une approbation, mais pas désigner les approbateurs en v1.

### 5.3 Ce que le journal fige

```yaml
# approval.requested
call_id: toolu_01…
tool_name: envoyer_email
approvers: [approbateur, gerant]        # nouveau ; écrit seulement si l'agent en liste (absent : tout rôle qui a `approve`)
self_approval: false                    # nouveau ; écrit seulement si l'agent l'interdit
scope: approve                          # lu dans les anciens journaux, plus écrit
```

- **Figés** : les rôles approbateurs et l'interdiction d'auto-approbation, tels qu'au moment de la demande. Le journal fait foi, comme pour `expire_at` : une config changée entre-temps ne change pas, en silence, qui peut trancher une demande déjà posée. Une interface peut afficher « en attente de : gérant » sans charger la config.
- **Relus dans la config du moment** : les rôles de l'utilisateur et leurs permissions. Un utilisateur à qui l'on retire `gerant` perd tout de suite le droit de trancher, y compris les demandes déjà en attente.
- On fige des **rôles**, pas des utilisateurs : une demande ne devient pas intranchable si son seul approbateur s'en va.

---

## 6. Par accès

### 6.1 REST et MCP HTTP

**Identification.** La clé d'API, inchangée. `Caller` gagne un `actor` résolu : utilisateur de la clé, rôles effectifs, permissions restreintes par la clé.

**Routes :**

| Route | Permission | Bornée par |
| --- | --- | --- |
| `POST /v1/runs`, déclencheurs | `run` | `access.run` |
| `POST /v1/runs/{id}/answer` | `answer` | `access.answer` |
| `POST /v1/runs/{id}/approve` | `approve` (+ `self_approve` si lanceur, + `amend` si `arguments`) | approbateurs figés, `self_approval` |
| `POST /v1/runs/{id}/reject` | `approve` (+ `self_approve` si lanceur) | approbateurs figés, `self_approval` |
| `POST /v1/runs/{id}/cancel` | `cancel` | `access.cancel` |
| lectures | `read` / `read_content` | inchangé |
| `judges: skip`, effacement, rétention | `admin` | inchangé |

**`as_user` : agir au nom d'un utilisateur déclaré (D-5, D-5b).** Le `by` du corps est retiré, sur toutes les routes. `as_user` le remplace, ainsi que `RunRequest.user_id`, et vaut sur `runs`, `answer`, `approve`, `reject`, `cancel` et les déclencheurs.

*Le cas* : une application métier (le POC FastAPI, un CRM) authentifie elle-même ses utilisateurs et appelle loom avec **une seule clé**. Sans `as_user`, loom ne voit que la passerelle. Il faudrait lui donner `approve`, et tout utilisateur de l'application pourrait alors approuver. Avec `as_user`, l'application dit au nom de qui elle agit, et loom vérifie les droits **de cette personne** :

```http
POST /v1/runs/r-123/approve
Authorization: Bearer <clé crm>

{"call_id": "toolu_01…", "as_user": "marie", "reason": "Montant vérifié"}
```

Vérifications, dans l'ordre :
1. L'utilisateur de la clé (`app-crm`) a `act_as`. Sinon, 403 « cette clé n'agit pas au nom d'autrui ».
2. `marie` est déclarée, dans le même client. Sinon, 403.
3. `marie` porte au moins un rôle de `act_as_roles`. Sinon, 403 « app-crm ne peut pas agir au nom de marie ».
4. Les **rôles effectifs** de `marie` sont ses rôles **restreints à `act_as_roles`**. Si Paul est `gerant` et `assistante`, la passerelle ne peut exercer que `assistante` en son nom, jamais `gerant`. C'est ce qui borne vraiment la passerelle : sans cette restriction, elle obtiendrait les pouvoirs du gérant par lui.
5. Les règles du § 4.3 et du § 4.4 s'appliquent à `marie` avec ces rôles effectifs. Les restrictions de la clé (`scopes`, `agents`) s'ajoutent.

Au journal :

```yaml
# approval.granted
by: marie
via: app-crm
roles: [approbateur]
```

Pourquoi c'est réservé à `act_as` : sinon n'importe quelle clé se ferait passer pour n'importe qui, ce qui est la faille actuelle (L-3). loom ne peut pas vérifier que la passerelle a bien authentifié Marie : il lui **fait confiance**, comme à un programme qui l'embarque. `act_as` rend cette confiance explicite, et `act_as_roles` limite ce qu'une clé volée permettrait.

Sans `act_as`, un `as_user` est refusé, même s'il nomme l'utilisateur de la clé : un champ inutile n'est pas accepté.

**MCP HTTP n'a pas d'`as_user`.** Les arguments d'un outil MCP sont écrits par le modèle, et **un LLM ne doit jamais choisir l'identité**. L'acteur est l'utilisateur de la clé.

**Instance locale sans clé (D-1b).** Avec `server.http.user: denis`, `loom serve` répond sans clé, au nom de `denis`. Ce n'est permis que :
- sur la boucle locale ;
- sans aucune clé déclarée ;
- sans MCP HTTP ;
- hors profil `prod`.

Sinon, `loom serve` exige des clés et refuse de démarrer sans elles. `as_user` y est refusé (`denis` n'a pas `act_as`, et ne doit pas l'avoir pour ça).

### 6.2 MCP : l'élicitation (L-5)

L'approbateur en ligne bâti sur l'élicitation est **toujours le lanceur**. Il ne tranche donc une demande que si l'un de ses rôles remplit les règles 5 à 8, `self_approve` compris. Pour les autres, il **passe la main** : la demande est écrite, le run se met en pause, et un approbateur habilité tranche par REST, CLI ou SDK.

Le changement est dans l'exécuteur (`engine/executor.py:748-756`). Avant `yield request`, on demande à l'approbateur s'il prend cet appel : un prédicat `takes(pending) -> bool` sur un protocole `Approver` élargi. S'il ne le prend pas, l'appel rejoint `held`, comme sans approbateur. Le rejeu n'est pas touché : il sert les décisions du journal.

Point à éprouver par les tests : un **lot mixte**, avec une demande tranchée en ligne et une autre tenue en pause.

En **stdio**, le lanceur est l'utilisateur de `loom mcp --user`, désormais obligatoire.

### 6.3 SDK Python (`Loom`)

Le SDK est un contexte **de confiance** : l'intégrateur authentifie, loom vérifie la matrice.

```python
loom = Loom(config)

result = await loom.run("secretariat", "Relance le devis Q-2026-0042", user="marie")

if loom.can("denis", "approve", run_id=result.run_id, call_id=pending.call_id):
    await loom.approve(result.run_id, call_id=pending.call_id, user="denis")
```

- `user=` est **obligatoire** sur `run`, `stream`, `approve`, `reject`, `answer` et `cancel`. Il alimente `context.user_id` et `by`.
- `by=` est retiré. `CallerContext(user_id=…)` ne peut plus contredire `user=` : s'ils diffèrent, c'est une erreur.
- Un refus lève `AccessDenied` (une `PermissionError`). Le message nomme l'utilisateur, l'action, l'agent et ce qui manque : « marie : aucun rôle parmi gerant ».
- `can(...)` applique les mêmes règles sans rien écrire. Une interface s'en sert pour n'afficher « Approuver » qu'à qui peut s'en servir.
- `approver=` (en ligne) reste pour les scripts et les tests. Il est soumis au § 6.2, puisqu'il agit pour le lanceur.
- Pas d'`as_user` : `user=` joue déjà ce rôle, l'intégrateur étant de confiance.

### 6.4 CLI

La CLI **lit et écrit le journal directement**. loom ne peut donc pas l'authentifier : quiconque peut lancer `loom approve` peut aussi éditer le JSONL. L'identité en CLI est **déclarative**, et les droits y sont un **garde-fou** contre les erreurs, pas une sécurité. Pour la sécurité, il y a les permissions du système de fichiers, ou le passage par REST. La documentation le dira en toutes lettres.

- `--user <id>`, ou `LOOM_USER`, est **obligatoire** sur `run`, `approve`, `reject`, `answer`, `cancel` et `resume`. `LOOM_USER` peut se mettre dans le `.env` que la commande `loom` lit déjà, ce qui évite de le retaper.
- `--by` est retiré.
- `loom keys create --user <id>` écrit la ligne `user:`.
- `loom mcp --user <id>` est obligatoire, à côté de `--tenant`.
- Les commandes de lecture et de maintenance (`validate`, `sessions`, `inspect`, `replay`, `eval`, `memory`, `storage`, `retention`, `schema`) ne demandent pas d'utilisateur en v1.

### 6.5 Répondre en écrivant dans la session

Un message envoyé dans une session où une question attend devient sa réponse (`_answered_run`, `access/api.py`). Il passe par `run` (on écrit à l'agent) **et** par `answer` (on répond), tous deux vérifiés sur l'acteur. Le `by` de la réponse est son identifiant.

### 6.6 Déclencheurs

La clé nomme son utilisateur, qui doit avoir `run` sur l'agent du déclencheur. Un CRM qui déclenche est un utilisateur de service. Avec `act_as`, le corps peut porter `as_user`, et le lanceur est alors cet utilisateur.

### 6.7 Rejeu, évals, variantes

**Aucun contrôle.** Le rejeu sert les décisions du journal (`replay/book.py`), et les évals et variantes ont leur approbateur programmatique. Les droits portent sur les **accès**, pas sur l'exécution : un rejeu ne prend aucune décision nouvelle.

---

## 7. Journal et audit

| Événement | Champ | Changement |
| --- | --- | --- |
| `run.started` | `context.user_id` | Acteur résolu (clé, `as_user`, `--user`, `user=`), plus jamais libre |
| `run.started` | `context.via` | **Nouveau** : passerelle, si `as_user` |
| `approval.requested` | `approvers` | **Nouveau** : rôles figés ; écrit seulement si l'agent en liste |
| `approval.requested` | `self_approval` | **Nouveau** : écrit seulement s'il vaut `false` |
| `approval.requested` | `scope` | Lu dans les anciens journaux, plus écrit |
| `approval.granted` / `.rejected` | `by` | Identifiant d'utilisateur |
| `approval.granted` / `.rejected` | `via` | **Nouveau** : passerelle ; absent sans passerelle, facette comprise |
| `approval.granted` / `.rejected` | `roles` | **Nouveau** : rôle(s) qui ont permis la décision ; absent si vide |
| `approval.granted` / `.rejected` | `self_approved` | **Nouveau** : `true` si l'approbateur est le lanceur (auto-approbation) ; absent sinon |
| `question.answered`, `run.cancelled` | `by` / `via` | Même traitement |

**Compatibilité de lecture.**
- Un ancien journal se relit et se rejoue à l'identique : tous les champs nouveaux sont facultatifs.
- **L'inverse est faux.** `DomainModel` refuse les champs inconnus (`extra="forbid"`, `core/model/base.py:10`). Une 2.0.17 ne lira pas une session écrite par la nouvelle version : `by` vaut toujours un utilisateur, et `approvers` sera presque toujours présent. C'est sans retour, comme pour `wait_answer`, et à annoncer pour un journal partagé par des workers de versions différentes.

Règle d'écriture (2.0.17, comme `run.started.state` et les facettes `judges` et `trigger`) : un champ nouveau qui ne dit rien de plus que sa valeur par défaut n'est pas écrit. Absent, il se relit à sa valeur par défaut, exactement comme dans un journal antérieur.

**Tentatives refusées.** Elles ne sont **pas** écrites au journal de session : un refus ne change rien au run, et le journal raconte le run. Chacune donne une ligne WARNING sur le logger `loom_ia.access.audit` (`access/audit.py`) : accès, action, utilisateur, passerelle, clé, permission, agent, client, run, appel, motif — sous `audit` au format `json`. Le span OpenTelemetry `loom.access.denied` n'est pas fait : les exports de loom sont tirés du journal, il n'y a pas de traceur en direct où l'accrocher. Un journal d'audit durable (un magasin par client, `loom audit`) reste possible, sur le même point d'écriture.

**Affichage.** `loom inspect` et le déroulé affichent `par marie (via app-crm)` et `auto-approuvé par denis`. `via` devient une facette, pour retrouver tout ce qu'une passerelle a porté.

---

## 8. `loom validate`

Une section **Accès**, dans la présentation validée le 29/10 :

```
Accès
  rôles        gerant (9 permissions, self_approve) · approbateur (approve, read, read_content)
               assistante (5, agent secretariat) · passerelle (act_as → assistante, approbateur)
  utilisateurs denis [gerant] · marie [assistante] · app-crm [passerelle] → dupont-plomberie
  clés         crm → app-crm · denis-mcp → denis (restreinte : run, read)
  serveur      local sans clé, au nom de denis
  paiements
    lance      gerant
    approuve   (défaut) gerant · sql → gerant · envoyer_email → approbateur, gerant
    auto-approbation interdite (l'agent)
```

**Avertissements :**
- outil soumis à approbation dont aucun **utilisateur** déclaré ne peut trancher les demandes (elles expireront toutes) ;
- `self_approval: false` alors qu'un seul utilisateur peut à la fois lancer et approuver l'agent (ses demandes ne seront jamais tranchées) ;
- rôle qui a `approve` sans `read_content` ;
- rôle déclaré mais porté par personne ;
- clé qui restreint vers une permission que son utilisateur n'a pas ;
- rôle d'`act_as_roles` qui porte `approve` (la passerelle peut faire approuver : c'est voulu, mais à voir).

---

## 9. Analyse d'impact technique

### 9.1 Fichiers touchés

| Fichier | Changement |
| --- | --- |
| `config/models.py` | `Permission` (remplace `Scope`), `AccessRole`, `UserSpec`, `SecurityConfig.roles/users` obligatoires, `ApiKey.user`, retrait d'`ApiKey.tenant`, `HttpServer.user`, validations |
| `config/loader.py` | Contrôles croisés ; message de migration avec la config minimale |
| `agents/spec.py` | `AgentAccess`, `AgentSpec.access` ; contrôle des rôles cités |
| `core/model/tooling.py` | Retrait d'`ApprovalSettings.scope`, avec un message qui pointe vers `access.approve` |
| `core/model/context.py` | `CallerContext.via` |
| `core/events/payloads.py` | `approvers`, `self_approval`, `via`, `roles`, `self` ; `scope` en lecture seule |
| `core/model/run_state.py`, `core/projections/run_state.py` | `PendingApproval.approvers/self_approval`, `ApprovalOutcome.via/roles/self` |
| `engine/executor.py` | `_request` fige `approvers` et `self_approval` ; approbateur en ligne qui passe la main |
| **nouveau** `access/authz.py` | `Actor`, résolution (clé, `as_user`, `act_as_roles`), règles 1 à 8, `can`, `AccessDenied` |
| `access/caller.py` | `Caller.actor` ; `may()` / `allows()` délèguent à `authz` |
| `access/api.py` | `user=` obligatoire sur les six méthodes ; filtre de `_decided` ; lanceur du run racine ; `can()` |
| `access/http/app.py`, `schemas.py`, `auth.py` | Routes, `as_user`, retrait de `by` et de `user_id`, `server.http.user` |
| `access/mcp_server/server.py`, `http.py` | Acteur, élicitation qui passe la main, `--user` en stdio |
| `access/cli/main.py` | `--user` / `LOOM_USER`, retrait de `--by`, `keys create --user`, `mcp --user`, section `validate` |
| `telemetry/inspect.py`, `access/progress.py` | `par … (via …)`, `auto-approuvé` |
| `tenancy/registry.py` | Utilisateurs par client |

Ordre de grandeur, sans prototype : 1 nouveau module, environ 17 fichiers modifiés, l'essentiel dans `access/` et `config/`. Le moteur ne change qu'en deux points.

### 9.2 Migration (conséquence de D-1)

| Quoi | Volume | Comment |
| --- | --- | --- |
| `loom.yaml` du dépôt (exemples, docs) | 10 | Bloc `security` minimal |
| Tests | 415 appels à `load_config`, 77 fichiers | Fixture ou helper qui ajoute le bloc minimal ; tests dédiés pour son absence |
| Tests qui passent `by=` / `--by` / `user_id` | à recenser au lot 0 | Remplacés par `user=` / `--user` / `as_user` |
| Agents de test (`agent`, `agent-mem`…) | hors dépôt | Bloc `security` + `LOOM_USER` dans le `.env` |
| `docs/configuration.md`, `fonctions.md`, `conception.md` | — | Sections `security`, `access`, retrait d'`approval.scope` |
| Site de formation (`docs_loom_v2`) | exercices avec `loom.yaml` | Bloc minimal ; un module sur les droits |
| Clients REST existants | — | `by` et `user_id` refusés : `as_user` + `act_as` |

### 9.3 Choix techniques et leurs raisons

- **Contrôler dans `Loom`, pas dans chaque accès.** Aujourd'hui, REST vérifie dans `app.py` et MCP dans `server.py`, et c'est pour cela que la CLI et le SDK y échappent. Un seul `authz` couvre les cinq accès.
- **Le code teste des permissions, jamais des noms de rôles.** Il demande « ce rôle a-t-il `approve` et figure-t-il parmi les approbateurs figés ? », jamais « est-ce un `gerant` ? ».
- **L'utilisateur porte les droits, la clé l'identifie.** Une personne a une seule identité au journal, quel que soit l'accès (L-6). Faire tourner une clé ne touche pas aux droits.
- **Un seul rôle doit satisfaire toutes les conditions d'une approbation.** Cela évite qu'une permission prise dans un rôle complète celle d'un autre.
- **`act_as_roles` restreint les rôles effectifs**, pas seulement les utilisateurs visés. C'est ce qui empêche une passerelle de passer par un utilisateur qui a plusieurs rôles.

---

## 10. Décisions

| # | Question | Décision | Statut |
| --- | --- | --- | --- |
| **D-1** | Droits obligatoires ? | **Oui** : au moins un rôle et un utilisateur dans toute config ; plus de mode ouvert | Validée 04/11 |
| **D-1b** | `loom serve` local sans clé | `server.http.user` : boucle locale, sans clé ni MCP HTTP, hors `prod` | Validée 04/11 |
| **D-2** | Où déclarer | Utilisateurs et rôles dans `loom.yaml` ; `agent.yaml` cite des rôles (`access`) | Validée 04/11 |
| **D-3** | Noms des clés YAML | En anglais, listés au § 5.1 et au § 5.2 ; `roles` en liste avec `id` ; jamais `roles:` au niveau de l'agent | Validée 04/11 |
| **D-4** | Clés d'API | `user` obligatoire ; `scopes` et `agents` restreignent ; `tenant` retirée | Validée 04/11 (par D-1 et D-3) |
| **D-5** | `by` libre | Retiré partout ; `as_user`, réservé à `act_as`, sur REST et les déclencheurs ; `via` au journal | Validée 04/11 |
| **D-5b** | Borner `act_as` | `act_as_roles` obligatoire avec `act_as` ; il restreint les rôles effectifs de l'utilisateur endossé | Validée 04/11 |
| **D-6** | `amend` séparé d'`approve` | Oui : corriger, c'est écrire l'action | À confirmer |
| **D-7** | Auto-approbation | Permission `self_approve`, dans le même rôle qu'`approve` | Validée 04/11 |
| **D-7b** | Durcissement par l'agent | `access.self_approval: false` ; retire, n'ouvre jamais | Validée 04/11 |
| **D-8** | Approbateurs figés ou relus | Rôles approbateurs et `self_approval` figés dans la demande ; rôles des utilisateurs relus | À confirmer |
| **D-9** | Tentatives refusées | Log WARNING sur `loom_ia.access.audit`, pas au journal de session ; pas de span | Réalisée 05/11 |
| **D-10** | Utilisateur en CLI | `--user` / `LOOM_USER` obligatoire | Validée 04/11 (par D-1) |
| **D-11** | Élicitation MCP | Le lanceur tranche ce que ses rôles permettent ; le reste en pause | À confirmer |
| **D-12** | Granularité d'approbation | L'outil, en v1 | À confirmer |
| **D-13** | `approval.scope` | Retirée, avec un message de migration | À confirmer |
| **D-14** | Version | 2.0.17 gardée, ou 2.1.0 (le format de config change pour tout le monde) | À trancher |
| **D-15** | Rôles intégrés | Aucun ; config minimale donnée par le message d'erreur et la doc | À confirmer |

---

## 11. Hors périmètre, à garder possible

- **Visibilité par utilisateur** (« ne voir que ses sessions »). Il faudrait filtrer les lectures du journal par `user_id`, ce que les magasins ne savent pas faire aujourd'hui. `run.started` porte désormais un `user_id` fiable, donc rien ne s'y oppose.
- **Quorum** (deux approbations pour un appel). Il demanderait des événements à part : la projection refuse deux décisions sur le même appel.
- **Approbateurs par droit ou par table** : `access.approve.tools.<outil>.rights.<droit>`, en s'appuyant sur `assess_call`.
- **Authentification déléguée** (OIDC, JWT) : un port `Authenticator` à côté des clés d'API, qui rend un utilisateur. `Actor` ignore comment il a été identifié.
- **Approbateurs désignés par client** (`tenants[].approvers`).
- **Boîte de décisions** : `Loom.pending_for(user)`, les demandes qu'un utilisateur peut trancher, toutes sessions confondues.

---

## 12. Plan de non-régression

- **Migration** : toute la suite passe avec le bloc minimal injecté par le fixture, sans autre changement que `by` → `user`.
- **Corpus de rejeu** : ils se rejouent à l'identique (le rejeu ne contrôle rien).
- **Anciens journaux** (2.0.17) relus par la nouvelle version : projection et rejeu identiques.
- **Nouveaux tests**, en table, rôle × permission × accès :
  - config sans `security`, ou avec une liste vide : refus, et le message donne la config minimale ;
  - refus et accord par REST, MCP HTTP, MCP stdio, CLI, SDK ;
  - auto-approbation : rôle avec ou sans `self_approve`, agent avec `self_approval: false`, sous-agent, lanceur derrière une passerelle ;
  - règle « même rôle » : `approve` dans un rôle et `self_approve` dans un autre → refus ;
  - approbateurs figés : config changée entre la demande et la décision ; utilisateur retiré d'un rôle pendant l'attente ;
  - `amend` refusé sans la permission ;
  - `as_user` : sans `act_as` (refus), vers un utilisateur hors `act_as_roles` (refus), vers un utilisateur à plusieurs rôles (seuls ceux d'`act_as_roles` comptent), `via` au journal ;
  - décision sans `call_id` sur des demandes hors d'atteinte : refus explicite ;
  - élicitation qui passe la main dans un lot mixte ;
  - clé qui restreint son utilisateur ;
  - `server.http.user` : accepté en local, refusé avec des clés, avec MCP HTTP, hors boucle locale, en `prod`.

---

## 13. Plan de mise en œuvre

| Lot | Contenu | Testable par |
| --- | --- | --- |
| **0** | Configuration : `security.roles/users` obligatoires, `api_keys[].user`, `act_as_roles`, `server.http.user`, `access` dans `agent.yaml`, retrait d'`approval.scope` et d'`api_keys[].tenant`, validations, schéma, section `loom validate`. Fixture de tests et migration des 10 `loom.yaml` du dépôt. Aucun contrôle encore. | `loom validate`, suite complète au vert |
| **1** | Noyau `access/authz.py` : `Actor`, résolution, rôles effectifs, règles 1 à 8, `can`, `AccessDenied`. Tests unitaires de la matrice. | tests unitaires |
| **2** | Journal : `approvers`, `self_approval`, `by`, `via`, `roles`, `self` ; projection ; lecture des anciens journaux. | rejeu, anciens journaux |
| **3** | SDK : `user=` obligatoire, retrait de `by=`, `can()`, filtre de `_decided`, lanceur du run racine. | script d'essai |
| **4** | REST et MCP : `Caller.actor`, routes, `as_user`, retrait de `by` / `user_id`, `server.http.user`, élicitation qui passe la main (exécuteur), `loom mcp --user`. | curl, client MCP |
| **5** | CLI : `--user`, `LOOM_USER`, retrait de `--by`, `keys create --user`, affichage `par … (via …)`, `auto-approuvé`. | essai réel |
| **6** | Documentation (`configuration.md`, `fonctions.md`, `conception.md`) ; essai de bout en bout sur un agent de test : Marie lance, le gérant approuve, un troisième utilisateur est refusé, auto-approbation permise puis interdite par l'agent. | parcours utilisateur |
