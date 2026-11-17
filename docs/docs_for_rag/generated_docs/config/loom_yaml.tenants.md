# loom.yaml : la clé tenants

La clé `tenants` de `loom.yaml` et tout ce qu'elle contient.

## `tenants[]`

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

Modèle Pydantic : `loom_ia.config.models.TenantSpec`.

- `tenants[].id` (type `loom_ia.core.model.ids.TenantId` ; obligatoire) : Identifiant du client.
- `tenants[].agents` (type `tuple[str, ...]` ; défaut : `[]`) : Agents que ce client peut lancer ; vide signifie tous ceux de la config.
- `tenants[].tools_deny` (type `tuple[str, ...]` ; défaut : `[]`) : Outils retirés à ce client, sous le nom que voit le modèle (préfixe MCP compris) : un rôle, un sous-agent ou un outil qu'on ne lui ouvre pas.
- `tenants[].models` (type `dict[str, str]` ; défaut : `{}`) : Correspondance des modèles : {modèle de la config: modèle de ce client}. Elle vaut partout — orchestrateur, rôles, juges, chaînes de secours, compaction —, la cible devant être déclarée dans `models`.
- `tenants[].approvals` (type `dict[str, Approval]` ; défaut : `{}`) : Approbation imposée par outil, quoi qu'en dise sa déclaration (point 17) : c'est le client qui sait ce qui l'engage.
- `tenants[].secrets` (type `dict[str, str]` ; défaut : `{}`) : Secrets : {nom attendu par la config: variable d'environnement de ce client}. Ce qui n'y est pas reste lu dans l'environnement commun.
- `tenants[].variables` (type `dict[str, JsonValue]` ; défaut : `{}`) : Variables citées par les prompts système (`{{ entreprise }}`).
- `tenants[].budgets` (type `Budgets | null` ; défaut : `null`) : Budgets de ce client : ils surchargent ceux de la racine clé par clé, et portent en plus ses plafonds par période (`tenant`, jalon J5.1b).
- `tenants[].quotas` (type `Quotas` ; défaut : les défauts de la section) : Débit accordé à ce client, quel que soit l'accès emprunté (`runs_per_minute` ; fonction L3).
- `tenants[].storage` (type `StorageConfig | null` ; défaut : `null`) : Stockage propre à ce client (isolation physique, `TenantRouter`) ; sans lui, celui de la racine, où seul le `tenant_id` le distingue. Le bloc déclare `events` : le défaut d'un journal est la mémoire, et un client qui n'aurait que `artifacts` perdrait le sien à chaque arrêt. `encryption` et `idempotency` ne s'y déclarent pas.
- `tenants[].retention` (type `RetentionStorage | null` ; défaut : `null`) : Rétention propre à ce client, à côté de `storage` et non dedans : le bloc `storage` dit **où** ses données vivent, et le déclarer lui donnerait son propre journal. Combien de temps elles vivent est une autre question — souvent contractuelle, et propre au client (jalon J5.5c).
- `tenants[].telemetry` (type `TenantTelemetry` ; défaut : les défauts de la section) : Capture des exports propre à ce client (jalon J6.1a) : la sienne, clé par clé, sur celle de la racine. Ses collecteurs et ses motifs restent communs.

## `tenants[].budgets`

Même forme que `budget` (page « agent.yaml : la clé budget »).

## `tenants[].quotas`

Débit accordé à un client (fonction L3), déclaré sous `tenants[].quotas`.

Un run refusé lève `QuotaExceeded` ; l'API REST le rend en 429 avec
`Retry-After`.

Modèle Pydantic : `loom_ia.core.model.quota.Quotas`.

- `tenants[].quotas.runs_per_minute` (type `int | null` ; défaut : `null`) : Runs qu'un client peut lancer par minute, tous accès confondus, par instance `Loom` (fenêtre en mémoire, voir le module). Un sous-run n'en est pas un : il est lancé par son parent, pas par le client. `None` (défaut) : pas de limite.

## `tenants[].storage`

Même forme que `storage` (page « loom.yaml : la clé storage »).

## `tenants[].retention`

Même forme que `storage.retention` (page « loom.yaml : la clé storage »).

## `tenants[].telemetry`

Ce qu'un client surcharge de la télémétrie : sa capture, rien d'autre.

Les motifs de masquage et les collecteurs sont ceux du déploiement : un
client choisit si son contenu part, pas où il va.

Modèle Pydantic : `loom_ia.config.telemetry.TenantTelemetry`.

- `tenants[].telemetry.capture` (type `CaptureOverride` ; défaut : les défauts de la section) : Capture propre au client.

## `tenants[].telemetry.capture`

Capture propre à un client : chaque clé absente reste celle de la racine.

Modèle Pydantic : `loom_ia.config.telemetry.CaptureOverride`.

- `tenants[].telemetry.capture.exports` (type `CaptureLevel | null` ; défaut : `null`) : `metadata` ou `content` ; absent : celui de la racine.
- `tenants[].telemetry.capture.raw_exchanges` (type `bool | null` ; défaut : `null`) : Échanges bruts au journal ; absent : réglage de la racine.
- `tenants[].telemetry.capture.raw_max_bytes` (type `int | null` ; défaut : `null`) : Taille maximale d'un corps gardé ; absent : celle de la racine.
