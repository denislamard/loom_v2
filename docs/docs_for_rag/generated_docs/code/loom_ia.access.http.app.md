# loom_ia.access.http.app

Module `loom_ia.access.http.app` de loom_v2 (fichier `src/loom_ia/access/http/app.py`).

API REST d'une instance loom (fonction N2).

Sous `/v1`, les agents et leurs runs :

- `GET  /agents` : les agents publiés (`expose.rest`) ;
- `POST /agents/{name}/runs` : lancement. **Synchrone** par défaut — la
  réponse est le résultat du run ; avec `background: true`, la réponse est
  202 et l'identifiant d'un run déjà inscrit au journal, qu'on suit ensuite
  par les routes ci-dessous. Le corps est en JSON, ou en
  `multipart/form-data` pour joindre des images (`uploads`) ;
- `GET  /runs/{run_id}` : statut et résultat d'un run ;
- `GET  /runs/{run_id}/events` : le journal du run en SSE — ce qui est déjà
  écrit, puis la suite en direct s'il tourne encore. Les événements de ses
  sous-runs y sont mêlés, sauf avec `?subruns=false` ; le flux se ferme
  sur la clôture du run demandé (fin, échec ou annulation). Comme pour la
  trace, le droit de lire vaut pour chaque agent de l'arbre : une clé qui n'a
  pas l'un d'eux reçoit 403 à l'ouverture, ou, si le sous-agent paraît en
  cours de route, un dernier message `error` puis la fermeture du flux ;
- `POST /runs/{run_id}/approve` et `/reject` : trancher une approbation
  (point 17), portée `approve` ; sans `call_id`, toutes les demandes en
  attente le sont. L'approbateur écrit au journal est l'identifiant de la
  clé d'API, qu'un `by` dans le corps remplace — une passerelle nomme
  ainsi l'humain qui a tranché ;
- `POST /runs/{run_id}/cancel` : arrêt d'un run (fonction A5), portée `run`.

Et les sessions (fonction F7) :

- `GET    /sessions` : les journaux du client, le plus récent d'abord.
  Refusé à une clé limitée à certains agents : la liste ne dit pas de quels
  agents sont les runs d'une session ;
- `GET    /sessions/{session_id}` : la fiche — ses runs et ce qui attend
  un humain, tous runs confondus ;
- `GET    /sessions/{session_id}/events` : le journal entier en JSONL ;
- `GET    /sessions/{session_id}/report` : consommation de toute la
  session (fonction J3), ventilée par run, par rôle et par modèle ;
- `DELETE /sessions/{session_id}` : effacement RGPD — journal, fichiers et
  clés d'idempotence. Irréversible, et il concerne tous les agents de la
  session : portée `admin`.

Et le journal lui-même (fonction K5, point 32) :

- `GET /runs` : les runs du client, du plus récemment écrit au plus ancien,
  avec `agent`, `status` (répétable), `since` et `until`. Ils sont
  **lus au journal**, session par session : il n'y a pas de projection à
  tenir à jour, et le prix est une lecture par journal ouvert. Deux bornes le
  tiennent — `limit` runs rendus, `sessions` journaux ouverts —, et la
  page dit ce qu'elle a coûté (`scanned`) et si une borne l'a arrêtée
  (`truncated`). Une clé limitée à certains agents y a droit, contrairement
  à `/sessions` : chaque run dit de quel agent il est, si bien que la liste
  se filtre honnêtement. Aucun contenu là-dedans, seulement le type d'un
  échec ;
- `GET /traces/{run_id}` : la trace d'un run (jalon J6.2c) — ses spans, tirés du
  journal, sous-runs compris, avec un en-tête (statut, usage, coût, durée) ;
  un run inachevé a la sienne. Sans `read_content`, aucun contenu ; avec, le
  contenu en clair, sans masquage par motifs (ceux-là sont pour les exports).
  Le droit de lire vaut pour chaque agent de l'arbre ;
- `GET /events` : la recherche au journal (`EventQuery`) — `session_id`,
  `run_id`, `type`, `category`, `status`, `agent`, `role`,
  `tool_name`, `model_id` (nom chez le fournisseur), `model` (identifiant dans
  la config), `since`, `until`, et `after` pour
  reprendre la pagination après un événement. Le client vient de la clé : rien
  dans l'URL ne le nomme, donc on ne cherche que chez soi. Les facettes
  libres ne sont pas interrogeables par l'URL ; les trois que `EventQuery`
  nomme (`tool_name`, `model_id`, `model`) le sont.

Résultat d'un run (fonction J3) : la réponse, `unverified` si elle a été gardée sans
respecter son contrat ou son juge, l'usage et le coût, leur ventilation
(`report`) et les verdicts des juges (`verdicts`). Un run échoué garde le
code 201 : `status` vaut `failed`, `error_type` dit ce qui l'a arrêté
(`guard.judge`, `model.auth`…) et `error` le dit en clair. Le champ
`judges` du corps (`auto`, `force`, `skip`) règle les juges du run ;
`skip` demande une clé de portée `admin`.

Pour suivre un run depuis son départ, l'appelant choisit son `run_id` dans
le corps de la requête : il peut ouvrir le flux sans attendre la réponse.

Tout passe par la façade `Loom` : le journal d'un run est le même que par
la CLI ou par MCP.

Document OpenAPI : chaque route porte un résumé et une famille (`TAGS`), et
les deux façons de présenter une clé y sont déclarées — de quoi essayer l'API
depuis sa propre page, sans lire ce fichier.

## Constantes de `loom_ia.access.http.app`

- `NDJSON = 'application/x-ndjson'` : Un événement par ligne : le format d'export du journal, celui de la CLI.
- `TAGS = ({'name': 'agents', 'description': 'Les agents que cette instance publie en REST.'}, {'name': 'runs', 'description': "L…` : Familles de routes du document OpenAPI : chaque route en porte une, et le document les décrit — c'est ce qui rend l'API lisible sans ce fichier.
- `BEARER_SCHEME = HTTPBearer(auto_error=False, description="Clé d'API de l'instance")` : Schémas d'authentification déclarés au document : la clé est lue sur l'un ou l'autre en-tête (`identify`), et les déclarer ici la rend saisissable depuis la page de l'API. Ils ne refusent rien eux-mêmes — c'est `caller` qui le fait.
- `HEADER_SCHEME = APIKeyHeader(name=API_KEY_HEADER, auto_error=False, description="Clé d'API de l'instance")` : Schéma d'authentification par l'en-tête `x-api-key` (voir `BEARER_SCHEME`).
- `Who = Annotated[Caller, Depends(caller)]` : L'appelant, injecté dans chaque route.

## Fonction `loom_ia.access.http.app._who_calls`

```python
def _who_calls(config: LoomConfig, host: str | None=None) -> None
```

Refuse une API qui ne saurait pas au nom de qui elle agit (décisions D-1 et D-1b des utilisateurs et rôles).

Avec des clés, chacun présente la sienne. Sans clé, l'instance agit au nom
de `server.http.user`, et seulement sur la boucle locale. `host` est
l'adresse où l'application est servie quand ce n'est pas celle de la
config (`loom serve --host`) : c'est elle qui ouvre l'API, donc elle qui
est jugée.

## Fonction `loom_ia.access.http.app._payload`

```python
async def _payload(request: Request) -> JsonValue
```

La charge d'une livraison : du JSON, ou rien.

Un corps vide est légitime — un planificateur n'a rien à dire d'autre que
« c'est l'heure ». Un corps illisible est refusé, pour ne pas lancer un run
sur une charge que le gabarit lira vide.

## Fonction `loom_ia.access.http.app.caller`

```python
async def caller(request: Request, bearer: Annotated[HTTPAuthorizationCredentials | None, Depends(BEARER_SCHEME)]=None, header: Annotated[str | None, Depends(HEADER_SCHEME)]=None) -> Caller
```

Appelant de la requête, reconnu par les clés de l'instance servie.

Le débit de la clé est compté ici, donc sur toutes les routes REST : une clé qui
s'emballe est arrêtée avant qu'on ne lise son corps (point 39). Le MCP HTTP
monté par l'application compte dans la même fenêtre (`app.state.rates`).

Les deux schémas sont là pour le document OpenAPI ; la clé se relit sur la
requête elle-même, une instance locale n'en demandant aucune (décision D-1b des utilisateurs et rôles).

## Fonction `loom_ia.access.http.app._aim`

```python
def _aim(request: Request) -> audit.Aim
```

Ce que la requête vise, lu sur sa route : pour la ligne d'audit d'un refus (décision D-9 des utilisateurs et rôles).

## Fonction `loom_ia.access.http.app.create_app`

```python
def create_app(loom: Loom, *, own: bool=False, host: str | None=None) -> FastAPI
```

Application ASGI servant les agents d'une instance.

`own` confie l'instance à l'application : elle l'ouvre au démarrage du
serveur (`async with`, ce qui lance l'écoute du bus partagé) et la ferme
à son arrêt. Sans lui, l'ouverture et la fermeture restent à l'appelant.

`host` dit où l'application sera servie quand ce n'est pas l'adresse de
la config : `loom serve --host` la remplace, et c'est celle-là qu'une API
sans clé doit justifier. Omis, c'est `server.http.host`.

## Fonction `loom_ia.access.http.app._mcp`

```python
def _mcp(loom: Loom, rates: RateWindow) -> McpHttp
```

Monte le serveur MCP, ou dit ce qui manque pour l'extra `mcp`.

## Fonction `loom_ia.access.http.app.sse`

```python
def sse(item: StreamItem, *, masked: bool=False) -> dict[str, str]
```

Message SSE d'un événement du journal ou d'un morceau du modèle.

## Fonction `loom_ia.access.http.app._acting`

```python
def _acting(loom: Loom, who: Caller, as_user: str | None) -> Actor
```

L'utilisateur au nom duquel la requête agit : celui de la clé, ou celui d'`as_user`.

`as_user` est réservé à une passerelle (`act_as`), qui n'exerce au nom
de l'utilisateur que les rôles qu'elle endosse ; sa clé la restreint encore
(décisions D-5 et D-5b des utilisateurs et rôles). L'utilisateur d'une instance locale n'a pas `act_as` : 403.

## Fonction `loom_ia.access.http.app._administers`

```python
def _administers(loom: Loom, who: Caller, actor: Actor, agent: str, *, endorsed: bool) -> None
```

`admin` : celle de l'utilisateur endossé, sinon celle de l'appelant.

## Fonction `loom_ia.access.http.app._published`

```python
def _published(loom: Loom, name: str, tenant: TenantId) -> None
```

Refuse un agent inconnu, non publié en REST ou fermé au client de la clé.

Les trois ont la même réponse, 404, et la liste des agents ne nomme que
ceux de ce client : on n'apprend ni l'existence ni le nom de l'agent d'un
autre. Une clé limitée à certains agents, elle, reçoit 403 avant d'arriver
ici (`require`), que l'agent existe ou non.

## Fonction `loom_ia.access.http.app._trigger`

```python
def _trigger(loom: Loom, name: str, tenant: TenantId) -> TriggerSpec
```

Le déclencheur de ce nom, s'il est ouvert au client ; sinon 404, comme s'il n'existait pas.

Un déclencheur dont l'agent est fermé au client ne se distingue pas d'un
déclencheur inconnu, et la liste ne nomme que ceux de ce client : on
n'apprend ni le nom ni l'agent des portes d'un autre. Comme pour
`_published`, une clé limitée à certains agents reçoit 403 ensuite
(`require`), puisqu'elle sait déjà que ce déclencheur existe pour son client.
