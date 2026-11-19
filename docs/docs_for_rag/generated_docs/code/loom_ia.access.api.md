# loom_ia.access.api

Module `loom_ia.access.api` de loom_v2 (fichier `src/loom_ia/access/api.py`).

Accès Python : la façade `Loom` (fonction N1).

Une instance rassemble ce qui était jusqu'ici assemblé à la main : la
configuration, les agents, le journal, les clients de modèle. Les deux autres
accès du jalon J1.6 — l'API REST et le serveur MCP — passent par elle, si
bien qu'un run donne le même journal quel que soit le chemin emprunté.

    async with Loom.from_config("loom-boot.yaml") as loom:
        result = await loom.run("demo", "Bonjour")
        print(result.text)

`stream` mêle les événements du journal et les morceaux du modèle, dans
l'ordre où ils arrivent : c'est la même source pour le direct de la CLI et
pour le flux SSE.

Sous-runs (fonction C5) : `stream`, `follow` et `events` rendent par défaut
l'arbre du run — ses événements et ceux de ses sous-runs, dans l'ordre du
journal ; chaque événement dit à quel run il appartient (`run_id`,
`agent`). `subruns=False` s'en tient au run lui-même. Les morceaux du
modèle d'un sous-run ne sont pas diffusés : sa réponse arrive avec le
`tool.completed` de l'appel.

Fichiers (fonctions G1 à G3) : `run` et `stream` acceptent des pièces jointes
(`Attachment`), validées puis rangées dans le stockage d'artefacts de
l'instance ; `RunResult.artifacts` liste les fichiers du run, et
`artifact(uri)` en rend les octets.

    photo = Attachment.from_path("photo.jpg")
    result = await loom.run("assistant", "Que montre la photo ?", attachments=[photo])

Disjoncteurs (point 10) : ceux des modèles et des serveurs MCP sont communs à
tous les runs de l'instance. Un modèle écarté après ses échecs l'est pour
tous ses agents, qui passent directement à leur secours.

Clients (fonction L1, point 33) : un run appartient toujours à un client — `default`
en mode librairie, où il ne surcharge rien. `tenant=` le nomme sur
`run`, `stream` et `submit`, et il se relit ensuite sur chaque
opération qui vise un run ou une session. Les agents sont montés **par
client** : deux clients du même agent n'ont ni les mêmes modèles, ni les
mêmes secrets, ni les mêmes connexions MCP.

Budgets et quotas d'un client (fonction L3, jalon J5.1b) : ils bornent ce qu'un client a le
droit de **lancer**, et sont donc vérifiés avant d'ouvrir le run — un budget
de journée épuisé lève `BudgetExhausted`, un débit dépassé
`QuotaExceeded`, et rien n'est écrit au journal. Les deux portent les
secondes à attendre.

Résultat (fonction J3) : `RunResult` dit, en plus de la réponse, si elle a été
gardée sans respecter son contrat ou son juge (`unverified`), la
consommation ventilée du run et de ses sous-runs (`report` : par run, par
rôle, par modèle) et les verdicts des juges (`verdicts`). Un échec a un
type (`error_type` : `guard.judge`, `model.auth`…) et un message
lisible (`error`). Les trois accès rendent ce même résultat.

## Constantes de `loom_ia.access.api`

- `StreamItem = Event | ModelChunk` : Ce qu'un run donne à voir pendant qu'il se déroule.
- `RunKey = tuple[TenantId, SessionId, RunId]` : Un run dans son journal : le même `run_id` peut exister chez deux clients, ou dans deux sessions d'un même client.
- `MAX_CLAIM_WAITS = 20` : File servie par un courtier (jalon J5.3b) : combien de fois un travail attend la fin d'un bail avant d'être laissé au journal.
- `CLAIM_MARGIN = 0.5` : Ce dont on dépasse la fin du bail avant de reprendre le travail, en secondes.
- `RUNS_LIMIT = 50` : Liste de runs (fonction K5) : runs rendus par défaut.
- `SESSIONS_READ = 50` : Liste de runs : journaux de session ouverts par défaut pour les trouver ; c'est la borne qui tient le coût d'une lecture sans projection.
- `_UNREADABLE = (JournalCorrupted, SealError, ValidationError, ProjectionError)` : Ce que lève la lecture d'un journal de session illisible : une ligne qu'on ne décode pas, un sceau qui ne s'ouvre pas (clé effacée), des événements que la projection refuse. Un balayage de sessions (`runs`, `recover`) saute celle-là.
- `RUNS_MAX = 200` : Runs qu'un appelant peut demander au plus dans une liste.
- `SESSIONS_MAX = 200` : Journaux qu'un appelant peut faire ouvrir au plus pour une liste de runs : une lecture par journal, donc une borne bien plus basse que celle d'une recherche d'événements.
- `UNCHECKED = Unchecked()` : Le seul jeton `Unchecked` : passé à `run`, il lance sans contrôle d'accès, pour le code de loom seul.

## Fonction `loom_ia.access.api.is_final`

```python
def is_final(event: Event) -> bool
```

Vrai sur l'événement qui clôt un run : fin, échec ou annulation.

## Fonction `loom_ia.access.api.check_deletable_id`

```python
def check_deletable_id(kind: str, value: str) -> None
```

Refuse, avant toute suppression, un identifiant vide.

Il ne désigne aucune session ni aucun client précis, et un magasin ne doit
jamais le lire comme « tout le client » (`UnusableId`).

## Classe `loom_ia.access.api.AgentNotAllowed`

```python
class AgentNotAllowed(PermissionError)
```

Cet agent n'est pas ouvert à ce client (fonction L1).

## Classe `loom_ia.access.api.UnknownRun`

```python
class UnknownRun(Unknown)
```

Aucun run de cet identifiant dans le journal.

## Classe `loom_ia.access.api.UnknownApproval`

```python
class UnknownApproval(Unknown)
```

Aucune demande d'approbation en attente pour cet appel.

## Classe `loom_ia.access.api._AlreadyDecided`

```python
class _AlreadyDecided(Exception)
```

Une autre décision est passée entre la lecture et l'écriture de la nôtre.

## Classe `loom_ia.access.api.UnknownQuestion`

```python
class UnknownQuestion(Unknown)
```

Aucune question en attente pour cet appel (wait_answer).

## Classe `loom_ia.access.api.InvalidAnswer`

```python
class InvalidAnswer(ValueError)
```

La réponse ne convient pas aux questions posées : rien n'est écrit.

## Classe `loom_ia.access.api.QuestionStale`

```python
class QuestionStale(ValueError)
```

Question périmée : elle ne prend plus de réponse ; la reprise du run la clôt.

## Classe `loom_ia.access.api.ApprovalStale`

```python
class ApprovalStale(ValueError)
```

Demande d'approbation périmée : elle ne se tranche plus (point 17).

L'`expire_at` du journal fait foi, même quand le réveil différé qui
devait l'appliquer a été perdu (process arrêté, file en mémoire). Rien
n'est écrit : c'est la reprise du run qui conclut la demande, selon
`approval.on_expiry`. À l'essai de la fonction H4 du 20/10, un `loom approve` lancé
45 s après l'échéance accordait l'appel, qui s'exécutait.

## Fonction `loom_ia.access.api._local`

```python
def _local(moment: datetime | None) -> str
```

Date et heure à l'heure locale de la machine, avec son fuseau.

## Classe `loom_ia.access.api.UnknownSession`

```python
class UnknownSession(Unknown)
```

Aucune session de cet identifiant dans le journal.

## Classe `loom_ia.access.api.UnknownTrigger`

```python
class UnknownTrigger(Unknown)
```

Aucune porte d'entrée de ce nom dans la configuration (fonction H6).

## Classe `loom_ia.access.api.DeliveryRefused`

```python
class DeliveryRefused(ValueError)
```

La livraison porte un identifiant ou une session inutilisables (fonction H6).

## Classe `loom_ia.access.api.Triggered`

```python
class Triggered(DomainModel)
```

Ce qu'une livraison a ouvert (fonction H6, jalon J5.4c).

**Attributs :**

- `trigger` : Nom du déclencheur.
- `run_id` : Run ouvert, ou retrouvé.
- `session_id` : Session du run.
- `status` : Statut du run à la réponse.
- `repeated` : Vrai si cette livraison avait déjà été reçue : le run est retrouvé, pas rouvert. Les plateformes réessaient, et une relance ne part qu'une fois.

## Classe `loom_ia.access.api.SessionDeletion`

```python
class SessionDeletion(DomainModel)
```

Ce qu'a retiré la suppression d'une session (RGPD, F7).

**Attributs :**

- `session_id` : La session supprimée.
- `events` : Événements retirés du journal.
- `artifacts` : Fichiers retirés.
- `keys` : Clés d'idempotence oubliées : celles d'un magasin partagé, le magasin `journal` gardant les siennes dans les événements ci-dessus.

## Classe `loom_ia.access.api.SessionSwept`

```python
class SessionSwept(DomainModel)
```

Une session que la rétention a retirée, ou retirerait (jalon J5.5c).

**Attributs :**

- `tenant_id` : Client de la session.
- `session_id` : La session.
- `last_write` : Dernière écriture : c'est elle, et elle seule, qui décide.
- `events` : Longueur du journal, connue par la marque de la session : vraie dans les deux modes, sans rien ouvrir.
- `artifacts` : Fichiers retirés : connus en supprimant, donc `None` en essai à blanc. Un zéro y mentirait — « aucun fichier » n'est pas « je n'ai pas regardé ».
- `keys` : Clés d'idempotence oubliées ; `None` en essai à blanc, comme `artifacts`.

## Classe `loom_ia.access.api.AuditSwept`

```python
class AuditSwept(DomainModel)
```

Les lignes du fichier d'audit que la rétention a retirées, ou retirerait (décision D-9 des utilisateurs et rôles).

**Attributs :**

- `days` : La borne lue : `security.audit.retention_days`.
- `scanned` : Lignes lues.
- `removed` : Lignes plus vieilles que la borne : retirées, ou qui le seraient.

## Classe `loom_ia.access.api.RetentionReport`

```python
class RetentionReport(DomainModel)
```

Ce qu'un balayage a fait, ou ferait (jalon J5.5c).

`dry_run` dit lequel des deux : sans lui, rien n'a été supprimé. Le
rapport dit aussi son **prix** — combien de sessions ont été regardées —
et la borne appliquée à chaque client, parce qu'une rétention qui n'efface
rien et une rétention non déclarée se ressemblent trop.

**Attributs :**

- `dry_run` : Essai à blanc : rien n'a été supprimé.
- `days` : Borne par client, telle qu'elle a été lue ; `None` : aucune règle.
- `scanned` : Sessions examinées.
- `kept` : Sessions gardées.
- `swept` : Sessions retirées, ou qui le seraient.
- `audit` : Le fichier d'audit, balayé avec tous les clients quand sa borne est déclarée ; `None` sinon. Il les mêle tous : un balayage borné à un client n'y touche pas.

### Méthode `loom_ia.access.api.RetentionReport.events`

```python
@property
def events(self) -> int
```

Événements retirés, ou qui le seraient.

## Fonction `loom_ia.access.api._masked_approvals`

```python
def _masked_approvals(approvals: tuple[PendingApproval, ...]) -> tuple[PendingApproval, ...]
```

Demandes d'approbation sans leurs arguments ni leur motif.

Conséquence assumée : **approuver demande de lire**. Une clé qui a
`approve` sans `read_content` trancherait à l'aveugle, et
`loom validate` le signale.

## Fonction `loom_ia.access.api._approvals_redacted`

```python
def _approvals_redacted(approvals: tuple[PendingApproval, ...]) -> list[str]
```

Chemins que `_masked_approvals` retire de ces demandes, s'ils portaient quelque chose.

## Fonction `loom_ia.access.api._masked_questions`

```python
def _masked_questions(questions: tuple[PendingQuestion, ...]) -> tuple[PendingQuestion, ...]
```

Questions sans leur texte : il est écrit par le modèle, donc du contenu.

## Fonction `loom_ia.access.api.unpiloted`

```python
def unpiloted(state: RunState, now: datetime | None=None) -> bool
```

Vrai pour un run ni fini ni en pause que personne ne pilote : à reprendre.

Un pilote tient une concession qu'il renouvelle ; absente ou échue, plus
personne ne fait avancer le run (process arrêté, plantage). Au test H1 du
20/10, ni `sessions show` ni `inspect` ne le disaient, ni ne donnaient
la commande, comme ils le font pour une approbation. Un run tout juste mis
en file, pas encore pris par un worker, est sans concession lui aussi :
d'où « si aucun worker ne s'en charge ».

## Classe `loom_ia.access.api.RunSummary`

```python
class RunSummary(DomainModel)
```

Un run de la session, tel que la fiche le montre (fonction F7).

**Attributs :**

- `run_id` : Identifiant du run.
- `agent` : Son agent.
- `status` : Son statut.
- `parent_run_id` : Run délégant, pour le run d'un sous-agent (point 4).
- `iterations` : Itérations de la boucle.
- `usage` : Tokens consommés.
- `cost_usd` : Coût, en dollars.
- `unpiloted` : Ni fini ni en pause, et personne ne le pilote (concession absente ou échue).

### Méthode `loom_ia.access.api.RunSummary.of`

```python
@classmethod
def of(cls, state: RunState, now: datetime | None=None) -> Self
```

La ligne d'un run tirée de son état ; `now` sert à dire s'il n'est plus piloté.

## Classe `loom_ia.access.api.RunListed`

```python
class RunListed(DomainModel)
```

Un run tel qu'une liste le montre (fonction K5) : de quoi le situer et le retrouver.

Aucun contenu ici, et c'est voulu : ni la réponse, ni le message d'erreur,
seulement son **type**. Une liste n'a donc pas à être masquée (jalon J5.2a), et la
portée `read` suffit à la lire.

**Attributs :**

- `run_id` : Identifiant du run.
- `session_id` : Sa session.
- `agent` : Son agent.
- `status` : Son statut.
- `kind` : Genre du run : `normal`, ou `compaction` (résumé de session).
- `parent_run_id` : Run délégant, pour le run d'un sous-agent.
- `started_at` : Premier événement du run au journal.
- `updated_at` : Dernier événement du run au journal.
- `iterations` : Itérations de la boucle.
- `usage` : Tokens consommés.
- `cost_usd` : Coût, en dollars.
- `error_type` : Type de l'échec (`guard.judge`, `model.auth`, `timeout`…), jamais son message : celui-là est du contenu, et se lit sur le run.

### Méthode `loom_ia.access.api.RunListed.of`

```python
@classmethod
def of(cls, state: RunState, events: Sequence[Event]) -> Self
```

La ligne d'une liste tirée de l'état du run et de ses événements (premier et dernier).

## Classe `loom_ia.access.api.RunPage`

```python
class RunPage(DomainModel)
```

Une page de runs, et ce qu'il a fallu lire pour l'obtenir (fonction K5).

`scanned` et `truncated` disent le prix et l'incomplétude : la liste
est tirée du journal, session par session, sans projection à tenir à jour.

**Attributs :**

- `runs` : Les runs de la page.
- `scanned` : Journaux de session ouverts pour cette page.
- `truncated` : Vrai si une borne a arrêté la recherche : il peut y en avoir d'autres.

## Classe `loom_ia.access.api.SessionInfo`

```python
class SessionInfo(DomainModel)
```

Ce qu'une session contient, sans dérouler son journal (fonction F7).

Les événements eux-mêmes se relisent avec `export_session`.

**Attributs :**

- `session_id` : La session.
- `last_seq` : Dernier `seq` écrit : la taille du journal.
- `updated_at` : Dernière écriture.
- `runs` : Les runs de la session, dans l'ordre où ils y sont entrés ; ceux des sous-agents compris, chacun nommant son délégant.
- `pending_approvals` : Ce qui attend un humain, tous runs confondus (point 17) : sans cela, l'appelant devrait ouvrir chaque run pour savoir ce qu'on lui demande.
- `pending_questions` : Questions posées à l'utilisateur sans réponse (wait_answer), tous runs confondus.
- `redacted` : Ce que le masquage a retiré (clé sans `read_content`), par chemin : sans lui, un champ vidé ne se distinguait pas d'un champ vide (essai de la fonction N2, 23/10). Même règle que pour un événement (`redacted`).

### Méthode `loom_ia.access.api.SessionInfo.masked`

```python
def masked(self) -> Self
```

Fiche sans le contenu : les runs et leurs coûts, pas les arguments.

## Classe `loom_ia.access.api.SessionFile`

```python
class SessionFile(DomainModel)
```

Un fichier d'une session, tel que le journal le décrit (`artifact.stored`).

Le stockage range les fichiers sous leur condensé (`file`) ; le journal
seul dit ce qu'ils sont. Un même contenu rangé plusieurs fois est un seul
fichier (l'URI est adressée par le contenu) : sa première production le
décrit, et `count` dit combien de fois il a été rangé.

**Attributs :**

- `uri` : URI du fichier (`artifact://…`).
- `file` : Nom dans le stockage (`<condensé>.<extension>`) : c'est lui qu'on demande au téléchargement.
- `name` : Nom donné par l'outil ou la pièce jointe ; contenu, donc masqué sans `read_content`.
- `media_type` : Type du fichier.
- `size` : Taille, en octets.
- `origin` : Origine : `attachment`, `tool_output` ou `offload`.
- `run_id` : Run qui l'a rangé.
- `agent` : Agent de ce run.
- `call_id` : Appel qui l'a produit (sortie d'outil ou déport).
- `tool_name` : Outil de cet appel.
- `stored_at` : Quand il a été rangé.
- `count` : Fois où ce même contenu a été rangé dans la session.
- `redacted` : Ce que le masquage a retiré (`name`), pour ne pas le prendre pour un fichier sans nom.

### Méthode `loom_ia.access.api.SessionFile.masked`

```python
def masked(self) -> Self
```

Sans le nom : il peut dire ce que contient le fichier (`facture-Dupont.pdf`).

## Classe `loom_ia.access.api.JudgeVerdict`

```python
class JudgeVerdict(DomainModel)
```

Verdict d'un juge sur une sortie du run ou d'un de ses sous-runs (`judge.evaluated`).

**Attributs :**

- `run_id` : Run jugé.
- `agent` : Son agent.
- `judge` : Nom du juge.
- `target` : `output` (réponse finale) ou `role:<nom>`.
- `call_id` : Appel du rôle jugé (`tool.called`) ; `None` pour la réponse finale.
- `model_id` : Nom du modèle du juge chez le fournisseur.
- `model` : Identifiant de ce modèle dans la config ; `None` pour un journal antérieur.
- `passed` : Tous les critères atteignent leur seuil.
- `blocked` : Un critère bloquant est sous son seuil : la sortie a été refusée.
- `attempt` : Tentative jugée, à partir de 1.
- `criteria` : Note de chaque critère.

### Méthode `loom_ia.access.api.JudgeVerdict.masked`

```python
def masked(self) -> Self
```

Verdict sans ses motifs : les notes restent, la prose part.

### Méthode `loom_ia.access.api.JudgeVerdict.of`

```python
@classmethod
def of(cls, event: Event, verdict: JudgeEvaluated) -> Self
```

Le verdict tiré d'un événement `judge.evaluated`.

## Classe `loom_ia.access.api.JudgeSkip`

```python
class JudgeSkip(DomainModel)
```

Juge déclaré qui n'a pas jugé une sortie du run (`guard.checked` `skipped`).

Sans lui, un juge sauté ne laissait aucune trace dans le résultat : rien ne
distinguait une sortie jugée et acceptée d'une sortie jamais jugée (test
E3 du 19/10). Le motif est un code, pas du contenu.

**Attributs :**

- `run_id` : Run concerné.
- `agent` : Son agent.
- `judge` : Nom du juge.
- `target` : `output` (réponse finale) ou `role:<nom>`.
- `reason` : Pourquoi il n'a pas jugé : appelant, échantillon, client, condition ou profil.

### Méthode `loom_ia.access.api.JudgeSkip.of`

```python
@classmethod
def of(cls, event: Event, checked: GuardChecked) -> Self | None
```

Le juge sauté que dit ce contrôle, ou None s'il ne dit pas un juge sauté.

## Classe `loom_ia.access.api.RunResult`

```python
class RunResult(DomainModel)
```

Ce qu'un run a produit, tiré de son état final et de son journal.

**Attributs :**

- `run_id` : Identifiant du run.
- `session_id` : Sa session.
- `agent` : Son agent.
- `status` : Statut final.
- `text` : Texte de la réponse.
- `output` : Le message de la réponse, blocs compris.
- `error_type` : Échec : son type (`guard.judge`, `guard.contract`, `model.auth`, `policy.<nom>`…).
- `error` : Échec : son message, lisible tel quel.
- `iterations` : Itérations de la boucle.
- `usage` : Tokens consommés par le run.
- `cost_usd` : Coût du run, en dollars.
- `artifacts` : Fichiers du run : pièces jointes, fichiers produits par les outils, déports (fonction G3).
- `data` : Réponse structurée : l'objet JSON validé par le schéma de sortie (fonction A7).
- `unverified` : Réponse gardée bien qu'elle ne respecte pas son contrat (`on_failure: unverified`).
- `fallback` : Réponse remplacée par le message de repli d'un contrat ou d'un juge (`on_failure: fallback`) : le run a abouti sans vraie réponse.
- `report` : Consommation ventilée du run et de ses sous-runs : par run, par rôle, par modèle.
- `verdicts` : Verdicts des juges du run et de ses sous-runs, dans l'ordre du journal.
- `skipped_judges` : Juges déclarés qui n'ont pas jugé (appelant, échantillon, client, condition, profil).
- `pending_approvals` : Approbations qu'il faut trancher pour que ce run avance (point 17), celles de ses sous-runs comprises : l'appelant n'a pas à savoir qu'un sous-agent existe pour savoir ce qu'on lui demande.
- `pending_questions` : Questions posées à l'utilisateur qui attendent sa réponse (wait_answer), celles des sous-runs comprises : `answer()` y répond.
- `redacted` : Ce que le masquage a retiré, par chemin (`text`, `artifacts[].name`…) : un `text` vidé ne se distinguait pas d'une réponse vide (essai de la fonction N2, 23/10).

### Méthode `loom_ia.access.api.RunResult.masked`

```python
def masked(self) -> Self
```

Résultat sans le contenu, pour une clé qui n'a pas `read_content`.

Ce qui reste est ce dont une supervision a besoin : l'agent, le
statut, les itérations, la consommation, la ventilation, et le verdict
de chaque juge — sans les motifs, qui citent la sortie. Ce qui part est
la correspondance : réponse, objet structuré, message d'erreur, noms de
fichiers et arguments des approbations en attente.

Le type de l'erreur reste : « pourquoi ça a échoué » n'est pas du
contenu, et sans lui il n'y aurait plus rien à superviser.

`redacted` nomme ce qui a été retiré, et seulement ce qui avait une
valeur : comme pour un événement, une absence n'est pas un vide.

### Méthode `loom_ia.access.api.RunResult.restricted`

```python
def restricted(self, allows: Callable[[str], bool]) -> Self
```

Résultat sans les agents que le lecteur n'a pas le droit de voir.

Relire un run, c'est relire tout son arbre, et une clé limitée à
certains agents n'a pas forcément le droit sur chacun de ceux qui y ont
travaillé : `masked` retire le contenu, celui-ci retire **les agents
refusés** — leurs lignes du rapport (par run et par rôle) et leurs
verdicts —, pour que ni leur nom ni ce qu'ils ont consommé ne se lisent
à part. Il ne refuse rien : le run de l'agent permis reste lisible.

Le total, l'usage et le coût restent ceux du run : ce qu'il a coûté ne
dépend pas de qui le lit. Restent aussi, parce qu'ils ne portent aucun
agent ou qu'il faut pouvoir les trancher pour que le run reparte, les
fichiers et les approbations en attente.

### Méthode `loom_ia.access.api.RunResult.of`

```python
@classmethod
def of(cls, state: RunState, events: Sequence[Event]=()) -> Self
```

Résultat d'un état final.

`events` : le journal de sa session, d'où viennent le rapport et les verdicts.

### Méthode `loom_ia.access.api.RunResult.ok`

```python
@property
def ok(self) -> bool
```

Vrai si le run a abouti (`completed`).

### Méthode `loom_ia.access.api.RunResult.produced`

```python
@property
def produced(self) -> tuple[ArtifactRecord, ...]
```

Fichiers produits par les outils du run.

## Fonction `loom_ia.access.api._produced_below`

```python
def _produced_below(state: RunState, tree: Sequence[Event]) -> tuple[ArtifactRecord, ...]
```

Fichiers produits par les outils des sous-agents du run, qu'il n'a pas rangés lui-même.

Un enfant range ses fichiers dans son propre run : le parent ne reçoit que
leurs URI, dans le `ToolOutput.artifacts` de l'appel. Le résultat de la
racine les liste aussi — ses déports et ses pièces jointes, eux, restent à
chaque run. Aucun état n'est touché : c'est une lecture du journal.

## Fonction `loom_ia.access.api._awaited`

```python
def _awaited(state: RunState, tree: Sequence[Event]) -> tuple[PendingApproval, ...]
```

Demandes d'approbation en attente dans tout l'arbre d'un run (point 17).

Un sous-agent qui se met en pause arrête son parent : ce qu'il faut
trancher pour que le run avance n'est pas forcément dans le run lui-même.
Un run fini n'avance plus : ses sous-runs restés en pause (journaux d'avant
leur fermeture avec le parent) n'attendent plus personne.

## Fonction `loom_ia.access.api._asked`

```python
def _asked(state: RunState, tree: Sequence[Event]) -> tuple[PendingQuestion, ...]
```

Questions sans réponse dans tout l'arbre d'un run comme `_awaited` (wait_answer).

## Fonction `loom_ia.access.api._asking_runs`

```python
def _asking_runs(state: RunState, tree: Sequence[Event]) -> list[RunState]
```

Le run et ceux de ses sous-runs qui ont une question sans réponse.

## Fonction `loom_ia.access.api._asked_with`

```python
def _asked_with(state: RunState, tree: Sequence[Event]) -> str
```

Ce que le modèle a écrit en posant les questions en attente, ou `""`.

Le modèle peut écrire avant d'appeler `ask` : à l'essai Haiku (ask-09),
il donnait l'heure demandée puis reposait sa question. Sans ce texte, le
résultat du run en pause n'en disait rien : seule la question restait.

## Fonction `loom_ia.access.api._usable`

```python
def _usable(what: str, value: str) -> str
```

Un identifiant qui peut servir de nom de journal, ou un refus qui le dit.

Une livraison porte ce que l'appelant veut : l'en-tête d'un service tiers
finit en `run_id`, donc en nom de session, donc en nom de fichier pour le
journal JSONL. Il est contrôlé à la porte, et non au moment d'écrire.

## Fonction `loom_ia.access.api._keeps`

```python
def _keeps(run: RunListed, *, agent: str | None, status: Sequence[RunStatus], since: datetime | None, until: datetime | None) -> bool
```

Vrai si ce run passe les filtres d'une liste.

## Fonction `loom_ia.access.api._by_run`

```python
def _by_run(events: Sequence[Event]) -> dict[RunId, list[Event]]
```

Événements d'un journal rangés par run, dans l'ordre où les runs y sont entrés.

Les marqueurs de session sont écartés : ils portent l'identifiant de leur
session, pas celui d'un run.

## Fonction `loom_ia.access.api._run_states`

```python
def _run_states(events: Sequence[Event]) -> list[RunState]
```

État de chaque run d'un journal, dans l'ordre où ils y sont entrés.

## Classe `loom_ia.access.api.Unchecked`

```python
class Unchecked
```

Le chemin interne sans contrôle de droits (§ 6.7) : évaluations et banc d'essai.

Ils font tourner un agent pour l'éprouver, pas au nom de quelqu'un : aucun
droit n'est vérifié, et le journal ne nomme aucun utilisateur. Seul
`run` l'accepte, et seul le code de loom s'en sert (`UNCHECKED`).

## Classe `loom_ia.access.api._Routed`

```python
class _Routed(NamedTuple)
```

Une question qui attend, à laquelle un message écrit dans la session répond.

**Attributs :**

- `root` : Run racine, celui qui reprend.
- `question` : La question en attente.
- `asker` : Agent du run qui l'a posée : un sous-agent, s'il y en a un (§ 6.5).

## Classe `loom_ia.access.api._Signed`

```python
@dataclass(frozen=True, slots=True)
class _Signed
```

Qui tranche une demande, et à quel titre : ce que la décision écrit (§ 7).

## Fonction `loom_ia.access.api._launched_by`

```python
def _launched_by(actor: Actor, context: CallerContext | None, tenant: TenantId | None) -> tuple[CallerContext, TenantId | None]
```

Le contexte d'un run lancé par cet utilisateur, et son client (règle 1, décision D-7 des utilisateurs et rôles).

Le contexte porte son identifiant — et la passerelle, s'il est endossé :
c'est le lanceur, qu'une auto-approbation reconnaît. Sans client nommé,
c'est le sien ; un client nommé qui n'est pas le sien est refusé par le
contrôle des droits, pas ici.

## Fonction `loom_ia.access.api._decidable`

```python
def _decidable(actor: Actor, asked: Sequence[tuple[RunState, PendingApproval]], tree: Sequence[Event], tenant: TenantId, *, amend: bool) -> _Decidable
```

Les demandes que l'acteur peut trancher, la signature de chacune, et les autres (§ 4.4).

Une seule demande visée, ou aucune qu'il puisse trancher parmi celles qui
attendent : `AccessDenied`, avec le motif de la première — un succès vide
laisserait croire que le run n'attendait rien. Sinon, celles qu'il ne peut
pas trancher sont laissées de côté, et rendues avec leur refus
(`skipped`) : c'est à qui agit de les écrire au journal d'audit.

## Classe `loom_ia.access.api._Decidable`

```python
class _Decidable(NamedTuple)
```

Ce que `_decidable` rend : les demandes gardées, leurs signatures, les écartées.

## Fonction `loom_ia.access.api._for_launcher`

```python
def _for_launcher(actor: Actor, agent: str, approver: Approver) -> LimitedApprover
```

Un approbateur en ligne qui agit pour le lanceur `actor` (§ 6.2).

Il ne prend que les demandes que le lanceur peut trancher — c'est son
propre run : `self_approve` est exigé, et un agent qui l'interdit le
retire. Les autres attendent en pause. La décision est signée du lanceur,
quoi qu'ait écrit l'approbateur. Un accord qui corrige les arguments sans
`amend` n'autorise rien : il devient un refus, qui dit pourquoi.

## Fonction `loom_ia.access.api._launcher`

```python
def _launcher(tree: Sequence[Event], state: RunState) -> str | None
```

L'utilisateur qui a lancé le run racine de `state` (§ 4.4).

## Fonction `loom_ia.access.api._awaiting_runs`

```python
def _awaiting_runs(tree: Sequence[Event]) -> list[RunState]
```

Runs de l'arbre qui attendent une approbation, dans l'ordre du journal.

## Fonction `loom_ia.access.api._where`

```python
def _where(tree: Sequence[Event], run_id: RunId, call_id: str) -> tuple[SpanId | None, SpanId | None]
```

Span de la demande qu'une décision vient trancher (point 17).

Une décision prise hors de la boucle — par REST, par la CLI, par
`approve()` — n'a pas d'étape à elle : elle se range donc **là où la
demande attendait**, et les deux se lisent d'un bloc, comme lorsqu'un
approbateur en ligne tranche dans le lot. Il en va de même d'une réponse à
une question (wait_answer), rangée là où la question attendait. Sans demande
retrouvée, le span racine du run vaut.

## Fonction `loom_ia.access.api._unreadable`

```python
def _unreadable(what: str, tenant: TenantId, session: SessionId, error: Exception) -> None
```

Avertit qu'un balayage a sauté une session dont le journal ne se lit pas.

## Fonction `loom_ia.access.api._unfinished`

```python
def _unfinished(events: Sequence[Event]) -> list[RunState]
```

Runs racine d'un journal qui peuvent encore avancer (point 27, fonction H3).

Un sous-run n'y est pas : son parent le reprend en rejouant l'appel
d'outil qui l'a lancé. Un run de compaction non plus — il sera refait si
la session en a besoin.

Les marqueurs de session sont écartés : ils portent l'identifiant de leur
session, pas celui d'un run, et une session résumée en a.

## Fonction `loom_ia.access.api.traced_agents`

```python
def traced_agents(trace: Trace) -> list[str]
```

Les agents d'une trace — celui du run, puis ceux de ses sous-runs —, chacun une fois.

Une relecture vérifie le droit sur chacun : la trace d'un run montre ce
qu'ont fait ses sous-agents.

## Fonction `loom_ia.access.api.tree_tools`

```python
def tree_tools(config: LoomConfig, agent: str, context_of: Callable[[str], RunContext]) -> tuple[dict[str, str], tuple[str, ...]]
```

Les outils de l'agent et de ses sous-agents (nom → genre), et les préfixes de ses sources.

`context_of` monte chaque agent de l'arbre. Un outil MCP ou de paquet est
connu par son préfixe (`serveur__`, `source__`) : ses outils ne sont
listés qu'à l'ouverture de la source, au début d'un run.

## Fonction `loom_ia.access.api.doubles_problem`

```python
def doubles_problem(config: LoomConfig, agent: str, doubles: Mapping[str, object], context_of: Callable[[str], RunContext]) -> str | None
```

Ce qui ne va pas dans des doublures, ou `None` : chacune vise un outil de l'agent.

Un outil de l'agent ou de l'un de ses sous-agents (`context_of` monte
chacun), ou d'un de leurs serveurs MCP ou sources de paquet (par son
préfixe) ; jamais un rôle,
qui est de la logique et tourne. Commun au rejeu en variante et aux évals.

## Fonction `loom_ia.access.api._no_subagent`

```python
def _no_subagent(agent: str) -> RunContext
```

Au rejeu identique, un sous-agent est servi par le journal : il n'est jamais monté.

## Classe `loom_ia.access.api._ReplayMount`

```python
class _ReplayMount
```

Les agents montés pour un rejeu : celui du run, et en variante ses sous-agents.

Tous partagent le journal en mémoire, le livre, les outils du rejeu et des
disjoncteurs à part — un rejeu ne doit ni ouvrir ni fermer ceux des runs
vivants. Au rejeu identique, les clients de modèle répondent depuis le
journal ; en variante, ce que le journal connaît est servi, le reste part
pour de vrai, et un sous-agent qui n'est pas lu au journal est monté ici —
en variante lui aussi.

### Méthode `loom_ia.access.api._ReplayMount.__init__`

```python
def __init__(self, build: Callable[..., Agent], tenant: Tenant, config: LoomConfig, store: EventStore, book: ReplayBook, tools: JournalTools | VariantTools) -> None
```

Prépare le montage, sans rien monter : les agents le sont au premier besoin.

**Arguments :**

- `build` : `build_agent`, avec ce que l'instance partage (registre, MCP, fichiers).
- `tenant` : Le client du run.
- `config` : La config du rejeu (en variante, modèles changés compris).
- `store` : Le journal en mémoire du rejeu.
- `book` : Le livre qui sert les réponses du journal.
- `tools` : Les outils du rejeu : ceux du journal, ou ceux de la variante.

### Méthode `loom_ia.access.api._ReplayMount.variant`

```python
@property
def variant(self) -> bool
```

Vrai pour un rejeu en variante.

### Méthode `loom_ia.access.api._ReplayMount.__call__`

```python
def __call__(self, agent: str) -> RunContext
```

Le contexte de l'agent `agent`, monté une fois (résolveur des sous-agents).

### Méthode `loom_ia.access.api._ReplayMount._mount`

```python
def _mount(self, agent: str) -> RunContext
```

Monte un agent pour le rejeu.

Clients de modèle du journal (ou de la variante), disjoncteurs à part,
outils du rejeu qui tranchent aussi les approbations.

### Méthode `loom_ia.access.api._ReplayMount.check_doubles`

```python
def check_doubles(self, agent: str, doubles: Mapping[str, Double]) -> None
```

Chaque doublure vise un outil de l'agent ou de ses sous-agents — jamais un rôle.

### Méthode `loom_ia.access.api._ReplayMount.aclose`

```python
async def aclose(self) -> None
```

Ferme les agents montés ; la première erreur remonte une fois tous tentés.

## Classe `loom_ia.access.api.Loom`

```python
class Loom
```

Une configuration chargée, prête à faire tourner ses agents.

### Méthode `loom_ia.access.api.Loom.__init__`

```python
def __init__(self, config: LoomConfig, *, store: EventStore | None=None, registry: Registry | None=None, environ: Mapping[str, str] | None=None, artifacts: ArtifactStore | None=None, breakers: CircuitBreakers | None=None, secrets: SecretProvider | None=None, counter: UsageCounter | None=None, intercept: ToolReplay | None=None, models: Mapping[str, ModelClient] | None=None) -> None
```

Ouvre l'instance : clients, journal, fichiers, bus, export, idempotence et file de tâches.

Un agent n'est monté, pour un client, qu'au premier besoin. Ce que
l'appelant fournit lui reste : il le ferme lui-même.

**Arguments :**

- `config` : La configuration chargée.
- `store` : Journal à utiliser ; à défaut, celui de la config (`storage.events`).
- `registry` : Registre des outils et politiques ; à défaut, chargé d'après la config.
- `environ` : Variables lues pour les secrets ; à défaut, celles du process.
- `artifacts` : Stockage des fichiers ; à défaut, celui de la config.
- `breakers` : Disjoncteurs des modèles et des serveurs MCP ; à défaut, neufs.
- `secrets` : Fournisseur des secrets des clients ; à défaut, l'environnement.
- `counter` : Compteur de consommation par client ; à défaut, en mémoire.
- `intercept` : Outils servis à part, posés sur chaque agent monté : une éval double ou refuse ainsi les outils à effets de bord.
- `models` : Clients de modèle à la place de ceux que la config déclare, par identifiant de modèle.

**Lève :**

- `ConfigError` : Client fourni pour un modèle non déclaré, ou avertissement qui refuse en profil prod.

### Méthode `loom_ia.access.api.Loom.from_config`

```python
@classmethod
def from_config(cls, path: Path | str, *, environ: Mapping[str, str] | None=None, profile: str | None=None) -> Self
```

Charge la config que désigne l'amorce, et ouvre l'instance.

`path` est l'amorce (`loom-boot.yaml`), qui dit où lire la config
(décision A-1). `profile` l'emporte sur `LOOM_PROFILE` et sur
`profile:` de l'amorce (fonction M4).

### Méthode `loom_ia.access.api.Loom.config`

```python
@property
def config(self) -> LoomConfig
```

La configuration de l'instance.

### Méthode `loom_ia.access.api.Loom.worker_id`

```python
@property
def worker_id(self) -> str
```

Identité que ce process inscrit dans la concession d'un run (point 27).

C'est elle qui départage deux pilotes au journal : un `run.claimed`
la porte, et la lire dit qui tient le run — utile dès qu'il y a
plusieurs workers (jalon J5.3b).

### Méthode `loom_ia.access.api.Loom.store`

```python
@property
def store(self) -> NotifyingEventStore
```

Journal de l'instance, abonnable pendant qu'un run se déroule.

### Méthode `loom_ia.access.api.Loom.exporter`

```python
@property
def exporter(self) -> RunExporter | None
```

Export des runs terminés ici vers les collecteurs ; `None` sans collecteur monté.

### Méthode `loom_ia.access.api.Loom.artifacts`

```python
@property
def artifacts(self) -> ArtifactStore
```

Stockage des fichiers des runs de l'instance.

### Méthode `loom_ia.access.api.Loom.registry`

```python
@property
def registry(self) -> Registry
```

Registre des outils et politiques de l'instance.

### Méthode `loom_ia.access.api.Loom.agents`

```python
@property
def agents(self) -> tuple[AgentSpec, ...]
```

Les agents déclarés.

### Méthode `loom_ia.access.api.Loom.names`

```python
@property
def names(self) -> tuple[str, ...]
```

Noms des agents déclarés.

### Méthode `loom_ia.access.api.Loom.exposed`

```python
def exposed(self, access: str, tenant_id: TenantId | None=None) -> tuple[AgentSpec, ...]
```

Agents publiés par un point d'accès (`rest` ou `mcp`), pour un client.

### Méthode `loom_ia.access.api.Loom.register`

```python
def register(self, name: str, obj: object) -> None
```

Rend un objet Python référençable par son nom dans la config.

À faire avant le premier run de l'agent qui s'en sert : les outils
sont résolus au premier montage de l'agent, puis gardés.

### Méthode `loom_ia.access.api.Loom.tenants`

```python
@property
def tenants(self) -> tuple[TenantId, ...]
```

Clients de l'instance ; `default` seul quand la config n'en nomme aucun.

### Méthode `loom_ia.access.api.Loom.tenant`

```python
def tenant(self, tenant_id: TenantId | None=None) -> Tenant
```

Un client et ses surcharges ; lève `UnknownTenant` s'il n'est pas déclaré.

### Méthode `loom_ia.access.api.Loom._for`

```python
def _for(self, agent: str, context: CallerContext | None, tenant: TenantId | None) -> tuple[CallerContext, Tenant]
```

Contexte appelant et client d'un lancement, l'agent vérifié ouvert à lui.

### Méthode `loom_ia.access.api.Loom._admitted`

```python
async def _admitted(self, agent: str, context: CallerContext | None, tenant: TenantId | None, actor: Actor | None=None) -> tuple[CallerContext, Tenant]
```

Ce qu'il faut vérifier avant d'ouvrir un run, dans l'ordre du moins cher.

Le client, puis l'agent qu'on lui ouvre, puis les droits de
l'utilisateur (`actor`, § 4.3), puis son débit (en mémoire), puis son
budget de période (qui peut relire le journal). Rien n'est écrit tant
que tout n'est pas passé : un run refusé n'a pas existé, et ne
consomme pas le débit de son client.

Avec un utilisateur, le client est le sien (règle 1) et le contexte
porte son identifiant : c'est le lanceur, dont dépend l'auto-approbation
(décision D-7 des utilisateurs et rôles).

### Méthode `loom_ia.access.api.Loom.can`

```python
async def can(self, user: str | Actor, permission: Permission, *, agent: str | None=None, run_id: RunId | None=None, call_id: str | None=None, session_id: SessionId | None=None, tenant_id: TenantId | None=None) -> bool
```

Vrai si l'utilisateur peut faire cette action ; rien n'est écrit (§ 6.3).

Pour n'afficher une action qu'à qui peut s'en servir — le bouton
« Approuver », par exemple. Les mêmes règles que les méthodes qui
agissent : un `True` ici, et l'action passera ses contrôles de droits.

- `approve` ou `amend` : sur un run (`run_id`), la demande
  `call_id` — ou l'une au moins de celles qui attendent ; `amend`,
  c'est l'accorder en corrigeant ses arguments. `self_approve` ne se
  demande pas : il se déduit de qui a lancé le run.
- Toute autre permission : sur l'agent nommé (`agent`), ou sur celui
  du run (`run_id`) ; sans l'un ni l'autre, hors de tout agent.

Un utilisateur inconnu ne peut rien ; un run inconnu lève `UnknownRun`.

### Méthode `loom_ia.access.api.Loom._actor`

```python
def _actor(self, user: str | Actor) -> Actor
```

L'acteur d'un appel : un identifiant déclaré, ou un `Actor` déjà résolu.

Un accès le résout lui-même quand il le peut — clé d'API, passerelle
(`as_user`) — pour que les restrictions de la clé suivent.
`AccessDenied` pour un utilisateur inconnu.

### Méthode `loom_ia.access.api.Loom._in_line`

```python
def _in_line(self, actor: Actor | None, agent: str, approver: Approver | None, answerer: Answerer | None) -> tuple[Approver | None, Answerer | None]
```

L'approbateur et le répondeur en ligne, bornés aux droits du lanceur (§ 6.2, § 6.5).

Ils agissent pour qui a lancé le run : ils ne prennent que ce qu'il peut
trancher ou répondre, et c'est lui qui signe. Le reste attend un humain
habilité, par un autre accès.

### Méthode `loom_ia.access.api.Loom._answering_for`

```python
def _answering_for(self, actor: Actor, agent: str, answerer: Answerer) -> Answerer
```

Le répondeur en ligne du lanceur : `answer` sur l'agent qui demande, sinon rien.

### Méthode `loom_ia.access.api.Loom._require_on_run`

```python
def _require_on_run(self, actor: Actor, permission: Permission, state: RunState) -> None
```

Les droits de l'acteur sur l'agent d'un run existant, chez le client du run.

### Méthode `loom_ia.access.api.Loom._judged`

```python
def _judged(self, judges: JudgesMode) -> JudgesMode
```

Ce que l'appelant demande aux juges, si le profil le permet (fonction M4).

`skip` retire un contrôle : c'est bon pour une mise au point, et
c'est précisément ce qu'un profil `prod` refuse. Sans profil déclaré,
rien ne change — la portée `admin` de l'accès REST reste la seule
barrière, comme depuis 3.3.

### Méthode `loom_ia.access.api.Loom.run`

```python
@audited
async def run(self, agent: str, message: str | Message, *, attachments: Sequence[Attachment]=(), session_id: SessionId | None=None, context: CallerContext | None=None, run_id: RunId | None=None, on_chunk: ChunkCallback | None=None, judges: JudgesMode='auto', approver: Approver | None=None, tenant: TenantId | None=None, state: Mapping[str, JsonValue] | None=None, answerer: Answerer | None=None, user: str | Actor | Unchecked) -> RunResult
```

Fait tourner un run jusqu'au bout et renvoie ce qu'il a produit.

`user` : l'utilisateur qui lance (docs/loom_role_user.md). Ses droits
sont vérifiés — `run` sur l'agent, et `answer` si le message répond
à une question qui attend —, le client est le sien, et il devient le
lanceur du run. `AccessDenied` sinon, avant que rien ne soit écrit.
`UNCHECKED` est le chemin interne des évaluations : rien n'y est
contrôlé.

Une session où une question de cet agent attend sa réponse (wait_answer)
prend le message comme réponse : la question reçoit ce texte, et c'est
le run qui l'avait posée qui reprend — son résultat est rendu, avec son
`run_id`. `answerer` répond aux questions dans la boucle.

Une pièce jointe refusée (format, taille) lève `AttachmentError`
avant que le run ne commence. `judges` (point 21) : `auto`, chaque juge
selon son `when` ; `force`, tous (audit, évals) ; `skip`, aucun.

`state` : écritures du state de la session par l'application (décision D5 du state de session),
`None` effaçant une clé. Contrôlées comme celles d'un outil, elles
vont dans le `run.started` ; une écriture refusée, ou une clé
`required` sans valeur, lève `StateRefused` (une `ValueError`)
avant que rien ne soit écrit (décision D13 du state de session). `session_state(session_id)` relit
ensuite le state de la session.

`approver` (point 28) : un approbateur en ligne, appelé dans la boucle
pour chaque appel qui demande une approbation. Le run ne passe alors
jamais en pause — c'est le mode des scripts, de la CLI et des tests.
Sans lui, l'approbation est asynchrone : le run s'arrête en `PAUSED`
et `approve()` le reprend. Avec `user`, l'approbateur en ligne agit
pour lui (§ 6.2) : il ne tranche que ce que ses rôles lui permettent
— `self_approve` compris, puisqu'il a lancé le run —, laisse le reste
en pause, et c'est l'utilisateur qui signe. `answerer` de même : sans
`answer` sur l'agent qui demande, la question attend.

Un run arrêté par `cancel()` rend son résultat, au statut
`CANCELLED` : l'appelant n'est pas annulé, et ne reçoit pas
`CancelledError`. Si c'est la tâche appelante qui est annulée
(`task.cancel()`, délai), `CancelledError` se propage, sans
`run.cancelled` : le run reste reprenable.

Une file qui refuse un travail de suite (résumé, échéance d'une
approbation) alors que le run est écrit ne fait pas échouer l'appel : la
panne est journalisée (ERROR) et le résultat est celui du run.

### Méthode `loom_ia.access.api.Loom._routed`

```python
async def _routed(self, agent: str, tenant: TenantId, session_id: SessionId | None, message: str | Message, attachments: Sequence[Attachment], state: Mapping[str, JsonValue] | None, run_id: RunId | None) -> _Routed | None
```

La question de cet agent qui attend une réponse dans la session, s'il y en a une.

Le message ne lui est donné que s'il n'est qu'un texte : avec des pièces
jointes, des écritures du state ou un `run_id` choisi, c'est un
nouveau tour, et le modèle apprend par une note que la question attend.

### Méthode `loom_ia.access.api.Loom._may_answer`

```python
def _may_answer(self, actor: Actor | None, routed: _Routed) -> None
```

Répondre en écrivant, c'est aussi répondre : `answer` sur l'agent qui a demandé.

Celui du run qui a posé la question — un sous-agent, s'il y en a un :
c'est son `access` qui dit qui lui répond (§ 5.2, § 6.5).

### Méthode `loom_ia.access.api.Loom._answered_later`

```python
async def _answered_later(self, routed: _Routed, message: str | Message, caller: CallerContext) -> RunId
```

Le message devient la réponse, et la reprise du run part en file (`submit`).

### Méthode `loom_ia.access.api.Loom._asks`

```python
def _asks(self, tenant: TenantId) -> bool
```

Un agent de ce client a-t-il `loom.ask` ? Sans lui, aucune question n'attend.

### Méthode `loom_ia.access.api.Loom._answered_run`

```python
async def _answered_run(self, ctx: RunContext, routed: _Routed, message: str | Message, caller: CallerContext) -> RunResult
```

Le message devient la réponse à la question, et le run qui l'a posée reprend.

### Méthode `loom_ia.access.api.Loom.stream`

```python
@audited_stream
async def stream(self, agent: str, message: str | Message, *, attachments: Sequence[Attachment]=(), session_id: SessionId | None=None, context: CallerContext | None=None, run_id: RunId | None=None, subruns: bool=True, judges: JudgesMode='auto', approver: Approver | None=None, tenant: TenantId | None=None, state: Mapping[str, JsonValue] | None=None, answerer: Answerer | None=None, user: str | Actor) -> AsyncGenerator[StreamItem]
```

Événements du journal et morceaux du modèle, dans l'ordre d'arrivée.

Le run est lancé en tâche de fond ; abandonner l'itération l'annule.
Son résultat se relit ensuite avec `result(run_id)`. Avec
`subruns`, les événements des sous-runs sont mêlés au flux. Le flux
ne montre que le journal de ce run — son client, sa session : le run
d'un autre client qui aurait choisi le même `run_id` n'y paraît pas
(dans un même client, ce `run_id` pris ailleurs est refusé :
`RunExists`).
`judges`, `approver`, `state` et `user` : comme pour `run`.

Un run arrêté par `cancel()` termine le flux proprement, après
`run.cancelled` ; l'arrêt de la tâche qui itère, lui, se propage.

### Méthode `loom_ia.access.api.Loom.resume`

```python
@audited
async def resume(self, run_id: RunId, *, session_id: SessionId | None=None, tenant_id: TenantId | None=None, on_chunk: ChunkCallback | None=None, user: str | Actor) -> RunResult
```

Reprend un run interrompu, là où son journal s'est arrêté.

`user` : reprendre, c'est faire tourner l'agent — `run` sur lui,
chez son client. `AccessDenied` sinon.

### Méthode `loom_ia.access.api.Loom.context`

```python
def context(self, agent: str, tenant_id: TenantId | None=None, *, on_chunk: ChunkCallback | None=None, approver: Approver | None=None, answerer: Answerer | None=None) -> RunContext
```

Contexte d'exécution d'un agent pour un client, monté au premier appel.

Le client de modèle et les outils sont gardés d'un run à l'autre ; le
contexte est gelé, `on_chunk` en donne donc une copie. Le montage a
lieu **par client** (fonction L1) : la configuration qu'il voit, ses secrets,
ses outils et ses connexions MCP lui appartiennent.

### Méthode `loom_ia.access.api.Loom.replay`

```python
async def replay(self, run_id: RunId, *, session_id: SessionId | None=None, tenant_id: TenantId | None=None, mode: ReplayMode='exact', models: Mapping[str, str] | None=None, doubles: Mapping[str, Double] | None=None, export: Path | None=None) -> ReplayReport
```

Rejoue un run fini, à l'identique ou en variante, et dit où il s'écarte (fonction K6).

La logique de l'agent — boucle, politiques, contrats, juges, budgets —
tourne avec la config **d'aujourd'hui**. Au rejeu identique
(`exact`), le monde — modèles, outils, sous-agents, approbateurs —
est servi par le journal, sans appeler personne : un run qui se rejoue
à l'identique prouve que la config produit encore exactement les mêmes
requêtes, et le premier écart est rapporté avec la partie de la requête
qui a changé.

En variante (`variant`, J6.2b), une requête identique à une requête
du journal reçoit sa réponse, les autres partent pour de vrai ; un
outil est lu au journal sous les mêmes nom et arguments, sinon doublé
(`doubles`, par nom d'outil), relancé pour un sous-agent, refusé s'il
a des effets de bord, exécuté sinon ; le rapport compare les deux runs.
`models` change le modèle d'une étape de l'agent : `main`, un rôle,
ou `judge:<nom>`. Ce qu'une variante dépense n'entre pas dans les
compteurs du client : seul le rapport le dit.

Rien n'est écrit dans le journal ; `export` garde celui du rejeu en JSONL.

### Méthode `loom_ia.access.api.Loom.replay_journal`

```python
async def replay_journal(self, journal: Path | str | Sequence[Event], *, run_id: RunId | None=None, mode: ReplayMode='exact', models: Mapping[str, str] | None=None, doubles: Mapping[str, Double] | None=None, export: Path | None=None) -> JournalReplay
```

Rejoue les runs d'un journal JSONL, fichier ou événements déjà lus (jalon J6.3b).

Le journal est celui qu'écrivent `loom sessions export`, ``loom eval
--export` ou `loom replay --export`` : il n'a pas à être dans le
journal de l'instance. Chaque run racine fini est rejoué comme
`replay` le ferait — à l'identique par défaut —, avec la config de
l'instance et celle de son client, nommé par le journal ; `run_id`
n'en rejoue qu'un. Un run inachevé n'est pas rejoué : le rapport le
nomme. `export` garde le journal d'un rejeu, donc d'un seul run.

`ReplayError` si le journal ne se lit pas, ne donne aucun run fini à
rejouer, ou si l'un de ses runs ne peut pas l'être (client ou agent
absents de la config) : le message nomme le run.

### Méthode `loom_ia.access.api.Loom._replay_events`

```python
async def _replay_events(self, events: Sequence[Event], run_id: RunId, tenant: Tenant, *, mode: ReplayMode, models: Mapping[str, str] | None, doubles: Mapping[str, Double] | None, export: Path | None) -> ReplayReport
```

Rejoue le run `run_id` des événements de sa session, pour ce client.

### Méthode `loom_ia.access.api.Loom.evaluate`

```python
async def evaluate(self, suite: EvalSuite | Path | str, *, cases: Sequence[str]=(), variants: Sequence[str]=(), export: Path | None=None) -> EvalReport
```

Joue une suite d'évals et dit, cas par cas et variante par variante, ce qui passe (fonction O1).

La suite (`EvalSuite`, ou le chemin de son fichier YAML) porte des
cas — une demande et ses attendus — et les variantes à comparer. Sans
`config` dans la suite, c'est celle de cette instance qui sert.
Chaque variante est montée **à part, en mémoire** : rien n'est écrit
dans le journal de l'instance, rien n'entre dans les compteurs de ses
clients ; `export` garde le journal de chaque run (un JSONL par run).
Un outil à effets de bord n'est jamais exécuté : sa doublure répond,
sinon le modèle reçoit une erreur. `cases` et `variants` n'en
jouent qu'une partie. Ce qui est enregistré ici (`register`) — un
outil, une doublure — sert aux variantes qui gardent la config de la
suite.

Les secrets de l'instance (`secrets`) et son profil servent comme
pour `run` : aux instances que l'éval monte, et à son juge. Les
clients de modèle fournis (`models`) servent à la place de ceux de la
config, juge compris, mais seulement tant que l'éval joue la config de
l'instance — ils portent des identifiants de **cette** config, pas de
celle qu'une suite ou une variante désigne. L'éval ne les ferme pas.

### Méthode `loom_ia.access.api.Loom.events`

```python
async def events(self, run_id: RunId, *, session_id: SessionId | None=None, tenant_id: TenantId | None=None, after_seq: int=0, subruns: bool=True) -> list[Event]
```

Événements d'un run dans l'ordre du journal, et ceux de ses sous-runs.

`subruns=False` s'en tient au run. `after_seq` ne rend que la
suite ; l'arbre, lui, se reconnaît depuis le début de la session.

### Méthode `loom_ia.access.api.Loom.trace`

```python
async def trace(self, run_id: RunId, *, session_id: SessionId | None=None, tenant_id: TenantId | None=None, content: bool=True) -> Trace
```

La trace d'un run et de ses sous-runs : ses spans, tirés du journal (fonction K5, jalon J6.2c).

Ce sont les spans de l'export OTel, avec un en-tête (statut, usage,
coût, durée de pilotage) ; un run inachevé a la sienne, ses spans
encore ouverts le disant. `content=False` la rend sans aucun contenu
— ce que voit une clé sans `read_content`. Pas de masquage par
motifs : c'est la portée qui décide, les motifs sont pour les exports.

### Méthode `loom_ia.access.api.Loom.follow`

```python
async def follow(self, run_id: RunId, *, session_id: SessionId | None=None, tenant_id: TenantId | None=None, after_seq: int=0, subruns: bool=True) -> AsyncGenerator[Event]
```

Événements déjà écrits, puis les suivants si le run tourne encore.

Avec `subruns`, ceux de ses sous-runs suivent aussi. L'itération
s'arrête sur la clôture du run demandé, même si elle précède
`after_seq`. Un run inconnu lève `UnknownRun`.

### Méthode `loom_ia.access.api.Loom.state`

```python
async def state(self, run_id: RunId, *, session_id: SessionId | None=None, tenant_id: TenantId | None=None) -> RunState
```

État d'un run, reconstruit depuis son journal.

### Méthode `loom_ia.access.api.Loom.result`

```python
async def result(self, run_id: RunId, *, session_id: SessionId | None=None, tenant_id: TenantId | None=None) -> RunResult
```

Ce qu'un run a produit, relu depuis son journal : réponse, coûts, verdicts.

### Méthode `loom_ia.access.api.Loom.consumption`

```python
async def consumption(self, tenant_id: TenantId | None=None, *, period: BudgetPeriod='day') -> TenantConsumption
```

Ce qu'un client a dépensé sur la journée ou le mois en cours (fonction L3, jalon J5.1b).

Relu dans le journal à chaque appel, et non pris au compteur : c'est
une question qu'un humain pose, et elle doit valoir même pour un client
sans budget — dont le compteur ne retient rien.

### Méthode `loom_ia.access.api.Loom.report`

```python
async def report(self, run_id: RunId | None=None, *, session_id: SessionId | None=None, tenant_id: TenantId | None=None) -> UsageReport
```

Rapport de consommation (fonction J5) : d'un run et de ses sous-runs, ou de toute une session.

Sans `run_id`, `session_id` est requis.

### Méthode `loom_ia.access.api.Loom.artifact`

```python
async def artifact(self, uri: str) -> bytes
```

Octets d'un fichier du stockage ; lève `ArtifactNotFound`.

### Méthode `loom_ia.access.api.Loom.sessions`

```python
async def sessions(self, *, tenant_id: TenantId | None=None) -> list[SessionRecord]
```

Sessions du client, de la plus récemment écrite à la plus ancienne.

### Méthode `loom_ia.access.api.Loom.runs`

```python
async def runs(self, *, tenant_id: TenantId | None=None, agent: str | None=None, status: Sequence[RunStatus]=(), since: datetime | None=None, until: datetime | None=None, limit: int=RUNS_LIMIT, sessions: int=SESSIONS_READ) -> RunPage
```

Runs du client, du plus récent au plus ancien (fonction K5, point 32).

Lus **au journal**, session par session, de la plus récemment écrite à
la plus ancienne, jusqu'à ce que la page soit pleine et qu'aucune
session restante ne puisse porter un run plus récent que son dernier
(un run n'est jamais plus récent que la dernière écriture de sa
session) : les chiffres sont exactement ceux de la fiche d'une
session, et il n'y a aucune projection à tenir à jour, à reconstruire
ou à resynchroniser. Le prix est une lecture par session ouverte, et
deux bornes le tiennent : `limit` runs rendus au plus, `sessions`
journaux ouverts au plus, chacune de 1 à 200 (`RUNS_MAX`,
`SESSIONS_MAX`, comme en REST ; `ValueError` au-delà). Quand une
borne arrête la recherche, la page le dit (`truncated`) — il peut y
avoir d'autres runs derrière.

`since` et `until` se comparent à la **dernière** écriture du run.
La lecture s'arrête à la première session écrite avant `since` : ni
elle ni les suivantes ne peuvent porter un run assez récent, et la page
n'est donc pas `truncated` pour autant.
Un sous-run est listé comme les autres, en nommant son délégant.

Un journal de session illisible n'empêche pas de lire les autres : il
est sauté, avec un avertissement qui nomme le client et la session, et
ses runs ne sont pas listés. Il compte parmi les journaux ouverts
(`scanned`) et la page ne le signale pas : le journal fait foi, et
c'est lui qu'il faut réparer.

### Méthode `loom_ia.access.api.Loom.query`

```python
async def query(self, query: EventQuery) -> list[Event]
```

Événements du journal qui satisfont ces critères (point 32, fonction K5).

C'est la lecture des traces : le journal **est** la trace, et
`EventQuery` est la façon de l'interroger. L'ordre est celui des
identifiants d'événement — donc celui du temps —, et `after` reprend
la pagination où elle s'était arrêtée.

### Méthode `loom_ia.access.api.Loom.session`

```python
async def session(self, session_id: SessionId, *, tenant_id: TenantId | None=None) -> SessionInfo
```

Fiche d'une session : ses runs, et ce qu'il faut trancher pour qu'elle avance.

Le journal est lu une fois et replié run par run. Une session inconnue
lève `UnknownSession` — un journal vide n'existe pas.

### Méthode `loom_ia.access.api.Loom.session_files`

```python
async def session_files(self, session_id: SessionId, *, tenant_id: TenantId | None=None, origin: Sequence[ArtifactOrigin]=()) -> tuple[SessionFile, ...]
```

Fichiers d'une session, dans l'ordre où ils ont été rangés.

Les fichiers d'un même lot d'appels sont rangés dans l'ordre où leurs
outils finissent, qui peut changer d'une exécution à l'autre.

Tirés du journal (`artifact.stored`), qui est la seule description des
fichiers : rien n'est écrit à côté du stockage, la liste vaut pour les
journaux déjà écrits, et le sceau comme la suppression RGPD la suivent
sans rien à faire. `origin` filtre (`attachment`, `tool_output`,
`offload`). Une session inconnue lève `UnknownSession`.

### Méthode `loom_ia.access.api.Loom.session_file`

```python
async def session_file(self, session_id: SessionId, file: str, *, tenant_id: TenantId | None=None) -> tuple[SessionFile, bytes]
```

Un fichier de la session, désigné par son nom dans le stockage, et ses octets.

Cherché parmi les fichiers que le journal de **cette** session décrit :
un nom qui n'y est pas lève `ArtifactNotFound`, quel que soit le
stockage — on ne lit pas un fichier d'une autre session ou d'un autre
client en devinant son condensé.

### Méthode `loom_ia.access.api.Loom.export_session`

```python
async def export_session(self, session_id: SessionId, *, tenant_id: TenantId | None=None) -> list[Event]
```

Tous les événements d'une session, dans l'ordre du journal.

Les fichiers n'y sont pas : chaque événement porte leur URI, et
`artifact(uri)` en rend les octets.

### Méthode `loom_ia.access.api.Loom.session_state`

```python
async def session_state(self, session_id: SessionId, *, tenant_id: TenantId | None=None) -> dict[str, JsonValue]
```

State de la session, relu dans son journal : ce qui est écrit, sans valeurs par défaut.

Ce que l'application a posé à l'ouverture de chaque run, et ce que les
outils des runs terminés ont écrit (décision D2 du state de session). `UnknownSession` si la session
n'a aucun événement ; `SessionUnreadable` si l'un de ses runs ne se relit pas.

### Méthode `loom_ia.access.api.Loom.delete_session`

```python
async def delete_session(self, session_id: SessionId, *, tenant_id: TenantId | None=None) -> SessionDeletion
```

Supprime physiquement une session : ses clés, ses fichiers, son journal (RGPD).

Le journal part en dernier : tant qu'il est là, on sait ce qu'il reste
à retirer. Les clés d'idempotence partent avec lui — une clé métier
peut porter une référence client (« relance:D-2026-042 »), et l'effet
qu'elle protégeait n'a plus de trace de toute façon.

Un identifiant de session ou de client vide est refusé (`UnusableId`)
**avant** de toucher à quoi que ce soit : il ne doit jamais se lire comme
« tout le client ». Un `tenant_id` vide n'est pas non plus pris pour le
client par défaut — seul `None` l'est.

### Méthode `loom_ia.access.api.Loom.apply_retention`

```python
async def apply_retention(self, *, tenant_id: TenantId | None=None, dry_run: bool=True, now: datetime | None=None) -> RetentionReport
```

Efface les sessions dormantes au-delà de la borne (`storage.retention`, jalon J5.5c).

Une session est choisie sur sa **dernière écriture**, et sur rien
d'autre : aucun contenu n'est lu, si bien qu'un journal scellé dont la
clé a disparu s'efface aussi — c'est là que la place serait perdue pour
de bon. Conséquence assumée : une session qui portait un run en pause
part comme les autres si elle est restée muette plus longtemps que la
borne.

`dry_run` est le défaut : le rapport dit ce qui partirait, et rien
n'est supprimé. Les fichiers et les clés d'une session ne se comptent
qu'en la supprimant, donc ils restent inconnus (`None`) en essai à
blanc ; le nombre d'événements, lui, est la longueur du journal, que la
marque de la session donne sans rien ouvrir.

Une session que le journal liste sous un identifiant vide n'est jamais
prise pour tout le client : elle reste en place, un avertissement la
nomme, et le balayage des autres continue (voir `delete_session`).

Rien ne se déclenche tout seul : c'est `loom retention` qui appelle, et
la plateforme qui le met à l'heure (jalon J5.4c).

### Méthode `loom_ia.access.api.Loom._sweep_audit`

```python
async def _sweep_audit(self, moment: datetime, *, dry_run: bool) -> AuditSwept | None
```

Les lignes du fichier d'audit plus vieilles que `security.audit.retention_days`.

### Méthode `loom_ia.access.api.Loom._sweep`

```python
async def _sweep(self, tenant: TenantId, record: SessionRecord, *, dry_run: bool) -> SessionSwept
```

Retire une session dormante, ou dit ce qui partirait (`dry_run`) : en essai à blanc, fichiers et clés restent inconnus.

### Méthode `loom_ia.access.api.Loom.aclose`

```python
async def aclose(self, *, wait: bool=True) -> None
```

Ferme les clients de modèle, les connexions MCP, et les stockages venus de la config.

Chaque étape est tentée, même si une précédente a levé : une connexion
MCP qui plante à la fermeture ne laisse pas le journal ouvert derrière
elle. L'échec est levé une fois tout fermé — le premier, ou
l'annulation si l'appelant a été annulé —, les autres sont journalisés
(`Closing`).

`wait=False` n'attend pas les tâches de fond de l'instance (runs
soumis, reprises mises en file, résumés) : elles sont annulées. Rien
n'est perdu pour autant, le journal fait foi et le run reste à
reprendre (`resume`, `recover`, un worker) ; c'est ce que fait
`loom approve --no-wait`. Une tâche déjà en route garde sa concession
jusqu'au terme du bail. Sans effet avec une file qui ne fait que
publier : ce qu'elle a publié tourne ailleurs.

### Méthode `loom_ia.access.api.Loom.__aenter__`

```python
async def __aenter__(self) -> Self
```

Ouvre l'instance, et met une oreille sur le bus s'il y en a un.

Le suivi du bus demande une boucle : il ne peut pas démarrer à la
construction. Une instance utilisée sans `async with` écrit et lit
normalement, mais ne verra pas ce que les autres process écrivent.

### Méthode `loom_ia.access.api.Loom._piloted`

```python
async def _piloted(self, ctx: RunContext, state: RunState, writer: SessionWriter | None) -> RunState
```

Pilote un run dans une tâche suivie, que `cancel` sait retrouver.

Si l'appelant est annulé (`task.cancel()`, délai), la tâche l'est
aussi : elle n'écrit rien de plus, le run reste reprenable, et
l'annulation se propage (`CancelledError`). Seul `cancel` écrit
`run.cancelled`.

Quand c'est `cancel` qui interrompt le pilote, l'appelant n'est pas
annulé : il n'a pas à recevoir `CancelledError`, qui lui ferait croire
qu'on l'arrête, lui. Il attend la fin de l'arrêt, comme un pilote en
attente, et reçoit le run tel que le journal le dit — `CANCELLED`.
Si l'arrêt n'a pas clos le run (il a échoué), le run reste reprenable et
l'interruption se propage comme avant.

Un run n'a qu'un pilote à la fois dans l'instance : deux tâches sur le
même run répètent l'outil, puis le journal ne se relit plus. Un second
pilotage attend donc la fin du premier, puis relit le journal avant de
rien faire : un run fini est rendu tel quel, un run laissé en pause et
réveillé entre-temps — une approbation — repart. Le second pilotage ne
sert donc ses `on_chunk` que si le run reste à faire.

### Méthode `loom_ia.access.api.Loom._holding`

```python
@asynccontextmanager
async def _holding(self, key: RunKey, *, takeover: bool=False) -> AsyncGenerator[None]
```

Tient un run le temps du bloc : un seul pilotage, ou un seul arrêt, à la fois.

Qui arrive pendant qu'un autre tient le run attend qu'il le lâche.
`takeover` (`cancel`) ne fait pas la queue : il prend la place de
celui qu'il interrompt, pour que les pilotes en attente ne repartent
pas avant que `run.cancelled` soit écrit.

### Méthode `loom_ia.access.api.Loom.cancel`

```python
@audited
async def cancel(self, run_id: RunId, *, session_id: SessionId | None=None, tenant_id: TenantId | None=None, user: str | Actor) -> bool
```

Arrête un run et écrit `run.cancelled` (fonction A5) ; faux s'il est déjà fini.

`user` : `cancel` sur l'agent du run, chez son client ; il signe
l'arrêt (`by`, et `via` pour une passerelle). `AccessDenied`
sinon, et rien n'est interrompu.

Le run piloté ici est d'abord interrompu, puis clos au journal. Un run
que cette instance ne pilote pas — repris ailleurs, ou laissé en plan
par un plantage — est clos directement. Ses sous-runs encore ouverts
sont annulés avec lui (`reason: parent`), dans la même écriture.

Un run annulé est **terminal** : il ne se reprend pas. Un run seulement
interrompu, lui, ne laisse rien au journal et repart où il en était.
Qui pilotait le run ici (`run`, `stream`, `resume`) n'est pas
annulé pour autant : il reçoit le run `CANCELLED` (ou voit son flux
finir), et non `CancelledError`.
Un run qu'un autre process finit entre la lecture et l'écriture de
l'arrêt est déjà fini : rien n'est écrit après sa fin.
Si l'appelant est lui-même annulé pendant l'arrêt — un délai qui tombe,
un client REST qui se déconnecte —, l'arrêt va quand même au bout
(`run.cancelled` est écrit), puis l'annulation remonte :
`CancelledError`, ou `TimeoutError` sous `asyncio.timeout`.
L'appelant ne reçoit pas le booléen ; le journal dit ce qu'il en est.

### Méthode `loom_ia.access.api.Loom._cancel`

```python
async def _cancel(self, run_id: RunId, *, session_id: SessionId | None, tenant_id: TenantId | None, by: str | None, via: str | None=None) -> bool
```

L'arrêt lui-même : interrompre le pilote d'ici, puis écrire la clôture.

### Méthode `loom_ia.access.api.Loom.approve`

```python
@audited
async def approve(self, run_id: RunId, *, call_id: str | None=None, reason: str='', arguments: dict[str, JsonValue] | None=None, session_id: SessionId | None=None, tenant_id: TenantId | None=None, user: str | Actor) -> tuple[str, ...]
```

Autorise un appel que le run attend, et remet son pilotage en file (point 17).

`user` : l'approbateur. Il ne tranche que les demandes que ses rôles
lui permettent (§ 4.4) — approbateurs figés, `self_approve` s'il a
lancé le run, `amend` s'il corrige les arguments — et il signe la
décision (`by`, `via`, `roles`, `self_approved`). Une demande
désignée qu'il ne peut pas trancher, ou aucune parmi celles qui
attendent : `AccessDenied`, et rien n'est écrit.

Sans `call_id`, toutes les demandes en attente sont accordées —
le cas courant, un seul appel à valider. `arguments` corrige ceux de
l'appel, et ne vaut que pour un `call_id` désigné : sans lui, la demande
est refusée (`ValueError`) plutôt que la correction perdue.

Rend les appels accordés ; vide si le run n'attendait rien — un run fini
n'attend plus rien, même annulé en pause. Une demande dont l'échéance
est passée ne s'accorde plus : `ApprovalStale` si c'était la seule
visée, et rien n'est écrit (la reprise du run applique
`approval.on_expiry`) ; sans `call_id`, les demandes périmées sont
laissées de côté.

Une fois la décision écrite au journal, une file qui refuse la reprise
ne fait pas échouer l'appel — la décision, elle, a eu lieu : la panne
est journalisée (ERROR), le run reste en attente de reprise, et
`recover()` ou `resume()` le reprend.

### Méthode `loom_ia.access.api.Loom.reject`

```python
@audited
async def reject(self, run_id: RunId, *, call_id: str | None=None, reason: str='', session_id: SessionId | None=None, tenant_id: TenantId | None=None, user: str | Actor) -> tuple[str, ...]
```

Refuse un appel que le run attend (point 17) : le motif revient au modèle.

Le run n'échoue pas : l'appel rend une erreur, et l'orchestrateur fait
ce qu'il peut de ce refus. Comme pour `approve`, une panne de la file
après l'écriture du refus est journalisée et ne fait pas échouer l'appel,
et une demande périmée ne se tranche plus (`ApprovalStale`). Refuser,
c'est aussi trancher : `user` demande les mêmes droits qu'un accord.

### Méthode `loom_ia.access.api.Loom._decided`

```python
async def _decided(self, run_id: RunId, call_id: str | None, *, session_id: SessionId | None, tenant_id: TenantId | None, payload: Callable[[PendingApproval, _Signed], ApprovalGranted | ApprovalRejected], actor: Actor, action: Literal['approve', 'reject'], amend: bool=False) -> tuple[str, ...]
```

Écrit une décision d'approbation, puis remet la racine en file.

La demande est cherchée dans tout l'arbre du run : on tranche là où on
a lu, et c'est toujours la racine qui repart — c'est elle qui rejouera
l'appel menant au sous-run qui attend.

Seules les demandes que l'acteur peut trancher le sont, chacune signée
des rôles qui le lui permettent (§ 4.4) ; le contrôle passe avant
l'échéance, pour ne rien dire d'une demande à qui ne peut pas la voir.

### Méthode `loom_ia.access.api.Loom.answer`

```python
@audited
async def answer(self, run_id: RunId, *, answers: Sequence[Sequence[str]] | None=None, text: str | None=None, call_id: str | None=None, session_id: SessionId | None=None, tenant_id: TenantId | None=None, user: str | Actor) -> str
```

Répond à une question que le run attend (wait_answer), et remet son pilotage en file.

`user` : `answer` sur l'agent du run qui a posé la question (un
sous-agent, s'il y en a un), chez son client ; il signe la réponse.
`AccessDenied` sinon, et rien n'est écrit.

`answers` : une liste de réponses par question, dans leur ordre (le
libellé d'un choix, ou un texte libre) ; `text` : un message libre
qui répond à l'ensemble. Sans `call_id`, la seule question en attente
de l'arbre du run ; s'il y en a plusieurs, `call_id` est exigé.

Rend l'appel auquel la réponse est donnée. `UnknownQuestion` si rien
n'attend, `InvalidAnswer` si la réponse ne convient pas,
`QuestionStale` si la question a expiré : rien n'est écrit.
Une réponse n'est pas une approbation : elle ne tranche aucune demande
d'approbation du même run.

### Méthode `loom_ia.access.api.Loom._write_answer`

```python
async def _write_answer(self, run_id: RunId, call_id: str | None, reply: QuestionReply, *, session_id: SessionId | None, tenant_id: TenantId | None, actor: Actor | None=None) -> tuple[RunState, str]
```

Écrit la réponse à une question de l'arbre du run ; rend la racine et l'appel.

Sans acteur, les droits ont été vérifiés avant (`_may_answer`), ou ne
le sont pas (`UNCHECKED`). Avec, il faut `answer` sur l'agent du run
qui l'a posée,
vérifié avant de juger la réponse : on ne dit pas à qui ne peut pas
répondre ce que la réponse aurait dû être.

### Méthode `loom_ia.access.api.Loom.submit`

```python
@audited
async def submit(self, agent: str, message: str | Message, *, attachments: Sequence[Attachment]=(), session_id: SessionId | None=None, context: CallerContext | None=None, run_id: RunId | None=None, judges: JudgesMode='auto', tenant: TenantId | None=None, trigger: str | None=None, state: Mapping[str, JsonValue] | None=None, user: str | Actor) -> RunId
```

Ouvre un run et met son pilotage en file ; rend son identifiant (fonction H5).

Le run est **inscrit au journal avant le retour** : l'identifiant rendu
désigne un run qui existe, qu'on peut suivre (`follow`), interroger
(`state`) ou arrêter (`cancel`) aussitôt. Son résultat se relit
ensuite avec `result(run_id)`, ou s'attend avec `drain()`.

Une pièce jointe refusée lève `AttachmentError` avant l'ouverture ;
un `state` refusé, `StateRefused` ; un utilisateur sans les droits,
`AccessDenied` (voir `run`).

### Méthode `loom_ia.access.api.Loom.triggers`

```python
@property
def triggers(self) -> tuple[TriggerSpec, ...]
```

Portes d'entrée déclarées par la configuration.

### Méthode `loom_ia.access.api.Loom.trigger_spec`

```python
def trigger_spec(self, name: str) -> TriggerSpec
```

Un déclencheur par son nom ; lève `UnknownTrigger`.

### Méthode `loom_ia.access.api.Loom.trigger`

```python
@audited
async def trigger(self, name: str, payload: JsonValue=None, *, delivery_id: str | None=None, tenant_id: TenantId | None=None, user: str | Actor) -> Triggered
```

Ouvre le run d'un déclencheur, depuis la charge reçue (fonction H6).

`user` : l'utilisateur de la clé qui livre ; il faut `run` sur
l'agent du déclencheur, vérifié avant de chercher une livraison déjà
reçue. Il devient le lanceur du run.

Le message est rendu depuis `{{ payload.… }}`, plus `{{ trigger }}`
et `{{ delivery }}`. Une variable absente donne une chaîne vide : une
charge incomplète fait un message plus pauvre, pas une livraison
perdue.

`delivery_id` devient le `run_id` : une **seconde livraison de la
même chose retrouve son run** au lieu d'en ouvrir un autre
(`repeated`). C'est ce qui rend une porte tenable — les plateformes
réessaient, et une relance ne doit partir qu'une fois. Un identifiant
déjà pris dans une autre session du client (charge différente pour un
gabarit `session`) est refusé (`DeliveryRefused`) : un identifiant
de livraison, un run.

Le run est lancé en arrière-plan : celui qui livre attend un accusé,
pas une réponse de modèle.

### Méthode `loom_ia.access.api.Loom._elsewhere`

```python
async def _elsewhere(self, name: str, run_id: RunId, session_id: SessionId, tenant: TenantId) -> None
```

Refuse un identifiant de livraison déjà pris dans une autre session du client.

Quand la session se rend depuis la charge, la même livraison avec une
autre charge (corrigée, ou un identifiant recyclé par l'émetteur)
tomberait dans une autre session et y ouvrirait un second run du même
nom : l'action partirait deux fois. À l'essai de la fonction H6 du 20/10, `crm-0001`
renvoyé avec un autre numéro de devis avait ouvert un second run
`crm-0001`. Le journal est cherché pour tout le client : indexé en
SQLite et en Postgres, relu en entier en JSONL — seulement pour une
porte qui déclare à la fois `session` et `delivery_header`.

### Méthode `loom_ia.access.api.Loom._already`

```python
async def _already(self, name: str, run_id: RunId, session_id: SessionId | None, tenant: TenantId) -> Triggered | None
```

Le run de cette livraison s'il existe déjà, sinon rien.

### Méthode `loom_ia.access.api.Loom.recover`

```python
async def recover(self, *, session_id: SessionId | None=None, tenant_id: TenantId | None=None) -> tuple[RunId, ...]
```

Remet en file les runs racine laissés en plan, et rend leurs identifiants (fonction H3).

À appeler soi-même : une instance ne redémarre pas les runs d'un autre
process à l'insu de son appelant. Un run déjà piloté par cette instance
est sauté, sans erreur et sans figurer dans le résultat. Celui qu'un
worker vivant d'un autre process pilote sera refusé par sa concession,
donc le remettre en file est sans risque. Un run dont l'agent n'est
plus déclaré est ignoré, avec un avertissement.

Sans `session_id`, toutes les sessions du locataire sont balayées —
c'est la reprise au démarrage d'un process. Avec, une seule l'est.

Tout ce qui est trouvé est remis en file d'un coup : avec la file en
mémoire, `storage.queue.max_running` (16 par défaut) borne ce qui
tourne de front, les autres attendent leur tour ; avec RabbitMQ, c'est
`loom worker --jobs`.

Au balayage, une session dont le journal est illisible est sautée, avec
un avertissement qui nomme le client et la session : les runs des
autres sont remis en file, et le résultat ne dit rien de celle-là. Le
journal n'est pas réparé. Une session désignée par `session_id` qui
ne se lit pas, elle, lève l'erreur : c'est la réponse à la question.

### Méthode `loom_ia.access.api.Loom._piloted_job`

```python
async def _piloted_job(self, job: Job) -> None
```

Pilote un run mis en file : soumission (fonction H5) ou reprise (fonction H3).

### Méthode `loom_ia.access.api.Loom._after_the_lease`

```python
async def _after_the_lease(self, job: Job, conflict: ClaimConflict) -> None
```

Repose le travail pour la fin du bail, quand la file est servie (fonction H6).

Avec une file en mémoire, le pilote qui tient le run est dans ce
process : il le mènera au bout, et il n'y a rien à reprogrammer.

Avec un courtier, c'est autre chose. Un worker tué n'acquitte pas son
travail, donc le courtier le redélivre **tout de suite** — mais le bail
du mort court encore, et personne ne le renouvellera. Le second pilote
arrive donc trop tôt, et s'il en restait là le run attendrait la
prochaine reprise au démarrage. On repose le travail pour l'après-bail,
et c'est ainsi qu'un run passe d'un worker à l'autre sans qu'on s'en
occupe.

Si le bail appartient à un pilote bien vivant, la même chose se répète
au rythme des renouvellements : c'est une surveillance, et elle
s'arrête d'elle-même quand le run se termine. Bornée quand même — au
bout de `MAX_CLAIM_WAITS` attentes, on laisse le run au journal.

### Méthode `loom_ia.access.api.Loom._result`

```python
async def _result(self, state: RunState) -> RunResult
```

Résultat d'un run qui vient de s'arrêter, avec son rapport et ses verdicts.

### Méthode `loom_ia.access.api.Loom._counted`

```python
async def _counted(self, state: RunState, result: RunResult) -> None
```

Porte au compteur du client ce que ce run a coûté, sous-runs compris.

Seulement pour un run racine : la consommation d'un sous-agent est déjà
dans le total de son parent. Le compteur pose une valeur par run, donc
repasser ici pour le même run — une reprise, par exemple — ne double
rien. Un compteur qui refuse n'a pas à faire échouer un run terminé.

### Méthode `loom_ia.access.api.Loom.compact`

```python
async def compact(self, session_id: SessionId, *, tenant_id: TenantId | None=None) -> SessionCompacted | None
```

Résume maintenant les échanges anciens d'une session (point 23).

Ne tient pas compte du seuil `over_tokens` : c'est la compaction à
la demande. Rend `None` si la session n'a rien de neuf à résumer, ou
si la configuration ne déclare pas de compaction.

### Méthode `loom_ia.access.api.Loom.drain`

```python
async def drain(self) -> None
```

Attend les tâches de fond en cours : runs soumis, résumés.

### Méthode `loom_ia.access.api.Loom.work`

```python
async def work(self, *, jobs: int=1) -> None
```

Consomme la file jusqu'à l'arrêt : ce que fait `loom worker` (fonction H6).

Ne rend la main qu'une fois `stop_work` demandé **et** les tâches en
cours menées au bout. Une file qui exécute chez elle n'a rien à
consommer : la demande est alors refusée en nommant ce qu'il faut
déclarer.

### Méthode `loom_ia.access.api.Loom.stop_work`

```python
async def stop_work(self) -> None
```

Demande au worker de s'arrêter : plus de tâche prise, celles en cours finissent.

### Méthode `loom_ia.access.api.Loom._compacted`

```python
async def _compacted(self, job: Job) -> None
```

Tâche de compaction, sortie de la file.

### Méthode `loom_ia.access.api.Loom._schedule`

```python
async def _schedule(self, state: RunState, events: Sequence[Event]) -> None
```

Met un résumé en file si la session a dépassé son seuil (point 23).

Avec `blocking`, rien : le résumé se fera à l'ouverture du run suivant.

### Méthode `loom_ia.access.api.Loom._expiring`

```python
async def _expiring(self, state: RunState, events: Sequence[Event]) -> None
```

Ramène un run en attente à l'échéance de sa première demande (point 17).

Le travail ne décide rien : c'est `expire_at`, au journal, qui fait
expirer une demande, et `drive` le lit en reprenant. Le travail ne
fait qu'y revenir au bon moment, pour que personne n'ait à repasser.
Différé, il ne retient ni `drain()` ni la fermeture : s'il est
abandonné, la demande n'en est pas moins périmée.

Les échéances sont cherchées dans tout l'arbre : quand c'est un
sous-agent qui attend, la racine n'a pas de demande à elle, et
personne ne serait venu la réveiller.

### Méthode `loom_ia.access.api.Loom._snapshot`

```python
async def _snapshot(self, state: RunState, events: Sequence[Event]) -> None
```

Matérialise l'historique de la session, si le gain le justifie (conception §11.2).

Le snapshot n'est qu'une vue : s'il ne peut pas être écrit, le run
reste juste et la session se relit entièrement.

### Méthode `loom_ia.access.api.Loom._writer`

```python
async def _writer(self, tenant_id: TenantId, session_id: SessionId | None) -> SessionWriter | None
```

Écrivain partagé d'une session nommée ; None pour un run anonyme.

### Méthode `loom_ia.access.api.Loom._unclaimed`

```python
async def _unclaimed(self, run_id: RunId | None, session_id: SessionId | None, tenant: TenantId) -> None
```

Refuse un `run_id` choisi par l'appelant et déjà pris dans une autre session.

`begin_run` ne regarde que la session du run. À l'essai de la fonction N2 du 23/10, un
même `run_id` imposé en REST dans deux sessions a ouvert deux runs :
leurs spans tombaient dans une même trace OTel (son identifiant vient
du `run_id` et du client) et `GET /v1/events?run_id=…` les mêlait.
Un identifiant choisi désigne donc un seul run du client ; la même
session reste l'affaire de `begin_run` (une demande rejouée garde sa
réponse). La recherche est indexée en SQLite et en Postgres, et relit
les journaux du client en JSONL — seulement quand l'appelant choisit
l'identifiant. Deux demandes simultanées peuvent encore passer toutes
les deux : le journal n'a pas d'index unique par client.

### Méthode `loom_ia.access.api.Loom._start`

```python
async def _start(self, ctx: RunContext, message: str | Message, attachments: Sequence[Attachment], session_id: SessionId | None, context: CallerContext | None, run_id: RunId | None, judges: JudgesMode='auto', state: Mapping[str, JsonValue] | None=None) -> RunState
```

Ouvre un run : state contrôlé, session résumée d'abord si elle dépasse son seuil (compaction), puis la demande et le `run.started` écrits.

## Fonction `loom_ia.access.api._caller_cancelled`

```python
def _caller_cancelled() -> bool
```

Vrai si asyncio a demandé l'arrêt de la tâche en cours (`task.cancel()`, délai).

## Fonction `loom_ia.access.api._closes`

```python
def _closes(event: Event, run_id: RunId) -> bool
```

Vrai sur la clôture du run suivi (celle d'un sous-run ne compte pas).
