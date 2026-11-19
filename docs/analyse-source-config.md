# Analyse d'impact — sources de configuration (loom_v2)

19 novembre 2026 · Denis Lamard

Code de référence : loom_v2 2.0.22. Sources relues : `config/loader.py`, `agents/spec.py`, `access/http/serve.py`, `access/cli/main.py`, `access/api.py`, `runtime/wiring.py`, `runtime/sources.py`. Besoin : section « Sources de configuration » du plan de lancement.

## En bref

**Verdict : faisable avant le gel du 11 décembre.** Le chantier se réduit à ce qui change la façon d'écrire un projet loom : une amorce `loom-boot.yaml`, toujours lue en premier, qui dit où trouver la config, et une clé `workpath` obligatoire dans la config, à laquelle se rapportent tous les chemins de travail et le code. Pas de source distante et pas de rechargement à chaud pour l'instant (A-7, A-8).

C'est la seule partie qui casserait les projets si on l'ajoutait plus tard : elle est faite maintenant, sans migration (A-3). Le reste (sources base et URL, version de la config) pourra venir ensuite sans rupture, en s'appuyant sur la clé `source` du bloc `config:`.

L'analyse complète des sources distantes, faite le 19/11 avant ces décisions, est gardée en annexe pour le jour où elles reviendront.

## Décisions prises (19/11/2026)

| # | Décision |
|---|---|
| A-1 | Deux fichiers, toujours : **l'amorce** `loom-boot.yaml`, premier fichier lu, toujours local, désigné par `--config` (défaut `./loom-boot.yaml`) ; et **la config de loom** (`loom.yaml` ou une autre source). |
| A-2 | L'amorce porte `version`, `profile`, `env_file` et le bloc `config:`, qui dit où chercher la config. Pour la source fichiers : `source: file`, `path` (dossier de la config) et le nom du fichier (`loom.yaml` par défaut). Toutes les autres clés sont dans la config ; `profile` et `env_file` y sont refusés, quelle que soit la source (le profil se choisit en local : règle D-3). |
| A-3 | **Pas de migration** : un projet sans amorce est refusé. Les tests, `examples/`, le script d'installation et la formation sont adaptés. |
| A-4 | La config porte une clé **`workpath` obligatoire**, quelle que soit la source. Un chemin relatif se rapporte au dossier de `loom-boot.yaml` ; en Docker, un chemin absolu monté depuis l'hôte (`/loom-data`). |
| A-5 | Tout se rapporte à `workpath`, **code compris** : `imports: [tools.Wireframe]` se lit `<workpath>/tools/Wireframe`, de même `python: tools.x:y` ; journal, artefacts, idempotence, audit, données des outils (`loom.sql`, `loom.excel`, `loom.memory`, `local_files`, `forge`), `cwd` des serveurs MCP, `file_roots`. |
| A-6 | Seuls les chemins des documents restent relatifs à la racine de la config (`path` pour les fichiers, préfixes logiques pour une base ou une URL) : `agents_dir`, `resources_dir`, `schema_file`. Le `.env` reste lié à l'amorce. |
| A-7 | **Pas de source distante pour l'instant** (base de données, URL) : elle pose des problèmes de sécurité (une config distante peut lancer des commandes sur l'hôte, voir l'annexe). Seule `source: file` est acceptée ; toute autre valeur est refusée avec un message clair. L'ajout doit rester possible plus tard sans rupture. |
| A-8 | **Pas de rechargement à chaud** : trop risqué pour peu d'intérêt. `worker`, `mcp`, `run` et la librairie lisent la config une fois, comme aujourd'hui. |
| A-9 | `loom serve --reload` est **gardé tel quel** (relance du process, dev seulement, refusé en `prod`) ; seul ce qu'il surveille change : `loom-boot.yaml`, le dossier de la config (`path`), les `.py` de `workpath` (sauf ce que loom y écrit) et le `.env`. |

Exemple :

```yaml
# loom-boot.yaml
version: 1
profile: prod
env_file: .env
config:
  source: file
  path: ./config
  file: loom.yaml          # défaut
```

```yaml
# config/loom.yaml
version: 1
workpath: /loom-data       # volume monté depuis l'hôte
agents_dir: agents/
resources_dir: resources/
imports: [tools.Wireframe] # → /loom-data/tools/Wireframe
```

## Impact par module (périmètre retenu)

| Module | Changement | Ampleur |
|---|---|---|
| `config/models.py`, `config/schema.py` | Modèle de l'amorce (`version`, `profile`, `env_file`, bloc `config:` avec `source: file`, `path` et le nom du fichier, `loom.yaml` par défaut). Clé `workpath` obligatoire dans la config ; `profile` et `env_file` retirés de la config. JSON Schema de l'amorce et de la config (`loom schema`). | M |
| `config/loader.py` | Lecture de l'amorce, puis de la config désignée. Chemins des documents (`agents_dir`, `resources_dir`, `schema_file`) rapportés au dossier de la config ; chemins de travail (stockage, audit, `cwd` MCP, `file_roots`, stockage des clients) rapportés à `workpath`, lui-même relatif au dossier de l'amorce. `source` autre que `file` refusée. Choix du profil : option, `LOOM_PROFILE`, puis l'amorce. | M |
| `config/references.py`, `runtime/wiring.py` | Modules voisins (`imports`, `python: module:attr`, politiques, juges) importés depuis `workpath` (`_importable`) ; `base_dir` remplacé par `workpath` pour les outils fournis (`BuiltinContext`) et les sources d'outils (`forge`). | M |
| `config/env_file.py` | `.env` cherché à côté de `loom-boot.yaml` ; `env_file:` lu dans l'amorce. | S |
| `runtime/wiring.py` `_protected_paths` | Racine protégée : `workpath` et ses modules Python (code et données y cohabitent), `loom-boot.yaml`, le fichier de config et les dossiers des documents. | S |
| `access/http/serve.py` | `watched()` : surveille l'amorce, le dossier de la config, les `.py` de `workpath` et le `.env`, en excluant ce que loom écrit (A-9). Le refus « données autour de la config » s'applique aussi à `workpath`. | S |
| `access/cli/main.py` | `--config` désigne `loom-boot.yaml` (défaut `./loom-boot.yaml`). `validate` affiche l'amorce, la config lue et `workpath`. | S |
| `access/api.py`, `testing/bench.py`, `testing/replays.py`, `access/evals.py` | `Loom.from_config`, le banc et les évals prennent le chemin de l'amorce. | S |
| Tests | Fabrique de config commune (amorce + `workpath`) ; `test_config`, `test_profils`, `test_env_file`, `test_rechargement`, `test_securite`, `test_local_files`… (`load_config` revient dans environ 600 lignes de tests). | L |
| Dépôt et docs | `examples/agent_sql`, script d'installation (il génère les deux fichiers), `configuration.md`, `conception.md`, `docs_for_rag` (régénération), formation, CHANGELOG. | M |

## Rupture et non-régression

- **Rupture voulue (A-3).** Un projet sans `loom-boot.yaml` ou sans `workpath` est refusé, avec un message qui donne l'amorce et la clé à écrire. Les agents de test hors du dépôt (`/home/denis/temp/*`) doivent recevoir une amorce et un `workpath`.
- **Comportement inchangé quand l'amorce, la config et `workpath` sont dans le même dossier** (`workpath: .`) : mêmes chemins résolus, mêmes imports, même `serve --reload`, mêmes protections.
- **À vérifier pendant les lots :**
  - un `.env` qui donne `LOOM_PROFILE` reste lu avant le choix du profil (il est maintenant lié à l'amorce) ;
  - les modules voisins sont cherchés dans `workpath` et plus dans le dossier de la config : un projet dont le code est à côté de `loom.yaml` mais avec un `workpath` ailleurs ne trouvera plus ses outils (message clair à prévoir) ;
  - `serve --reload` ne doit pas relancer sur ce que loom écrit dans `workpath` (journal, artefacts, bases SQLite, audit) ;
  - les chemins affichés par `validate` et les messages d'erreur nomment le bon fichier (amorce ou config).

## Découpage

| Lot | Contenu | Ampleur |
|---|---|---|
| 0 | Modèle de l'amorce et de `workpath`, schémas, refus des sources autres que `file`, choix du profil | M |
| 1 | Chargement (amorce puis config), chemins des documents et de travail, imports depuis `workpath`, `.env`, protections, CLI, `serve --reload` | M |
| 2 | Tests adaptés, `examples/agent_sql`, script d'installation | L |
| 3 | Docs : `configuration.md`, `conception.md`, `docs_for_rag`, formation, CHANGELOG | M |

## Ce qui reste prêt pour les sources distantes

- La clé `source` du bloc `config:` : une source `postgres` ou `url` viendra s'y brancher sans changer le format.
- La séparation documents / travail : les documents se rapportent à la racine de la config (préfixes logiques pour une base ou une archive), le travail à `workpath`, local à chaque hôte ou conteneur.
- La règle de partage des clés : `profile` et `env_file` dans l'amorce seulement, ce qui empêche déjà une config de choisir son profil.
- Ce qui est reporté (voir l'annexe) : port `ConfigSource` et instantané, chargeur sur un arbre, points d'entrée, connecteurs base et URL, version de la config dans le journal, décisions D-4 à D-8. Le chargeur sur un arbre ne change rien pour l'utilisateur : il se fera avec la première source distante.

## Annexe — sources distantes (reportées)

Analyse du 19/11/2026, avant les décisions A-7 et A-8. Gardée pour le jour où les sources base et URL reviendront.

### Constats

1. **Le rechargement à chaud n'existe pas aujourd'hui.** `serve --reload` relance le *process* : un superviseur garde la socket, un nouveau process charge la config et l'ancien finit ses runs. Le commentaire de `serve.py` le dit : « rien ne se recharge à chaud dans un process vivant ». Ce rechargement est refusé en `prod` et ne vaut que pour `loom serve`. `worker`, `mcp` et la librairie lisent la config une seule fois. La spec, qui veut un rechargement à chaud pour toutes les sources avec les mêmes règles de `prod`, contredit ce refus.
2. **Les documents ne se limitent pas à loom.yaml, aux agents et aux prompts.** Le chargeur lit aussi les skills, les modèles de document, les styles, les logos et les polices. Ces deux derniers sont **binaires** (jusqu'à `MAX_FONT_BYTES`). Il lit aussi les `schema_file`, relatifs au dossier de la config et pas à `resources_dir` : ils peuvent donc être **hors de l'arbre**. L'interface doit transporter des octets, pas du texte.
3. **`base_dir` a deux sens.** C'est d'abord l'origine de la config. C'est aussi le dossier de travail de l'instance, et c'est à lui que se rapportent :
   - les modules Python voisins (`imports`, `module:attr`) ;
   - les chemins de `loom.sql`, `loom.excel`, `loom.memory`, `local_files` et `forge` ;
   - le journal, les artefacts et l'audit ;
   - le `cwd` des serveurs MCP et le `.env`.

   Avec une source distante, ce dossier reste local. **Un fichier local d'amorçage est donc indispensable** : il porte la source, le `.env` et le profil. Le `.env` doit d'ailleurs être lu *avant* de se connecter, puisque c'est lui qui donne le DSN ou le jeton.
4. **Une config distante, c'est du code.** Elle peut déclarer des `mcp_servers` en stdio (`command`), des `imports`, des `python: module:attr`, des `tool_sources`, des dossiers ouverts aux outils, ainsi que les utilisateurs, les rôles et les empreintes de clés. Pouvoir écrire dans la table ou sur l'URL revient à pouvoir lancer des commandes sur l'hôte. Aujourd'hui, la frontière de confiance est l'accès au disque. Demain, ce sera l'accès en écriture à la base ou au serveur.
5. **Un agent pourrait réécrire sa propre config.** C'est le cas de `loom.sql` branché sur la base qui contient la table de config, ou de `local_files` qui verrait le fichier SQLite de config. `_protected_paths` protège aujourd'hui `agents_dir` et `resources_dir`, mais rien de l'équivalent pour une base.
6. **Il y a plusieurs process et aucune traçabilité de version.** `serve` et les workers RabbitMQ interrogent chacun la source, donc pendant quelques secondes ils ne tournent pas sur la même version. Or un run lancé par l'API s'exécute **dans le worker**, avec la version de celui-ci. Et `run.started` ne dit pas quelle config a fait tourner le run.
7. **La publication n'est pas couverte, et la table proposée n'est pas atomique.** Sans commande pour remplir la table ou produire l'archive, les connecteurs base et URL ne s'utilisent qu'en SQL à la main. Et une table « un document = une version » laisse lire un état à moitié publié : trois documents modifiés, deux lus.

### Architecture proposée

#### Un instantané et une interface

Les noms ci-dessous sont provisoires.

```python
@dataclass(frozen=True, slots=True)
class ConfigSnapshot:
    version: str                    # opaque, comparée par égalité (pas d'ordre)
    documents: Mapping[str, bytes]  # chemin logique POSIX → contenu ("agents/relance.yaml")
    origin: str                     # pour les messages : "postgres:prod#release 42"

class ConfigSource(Protocol):
    async def fetch(self, known: str | None) -> ConfigSnapshot | None: ...  # None : inchangée
    def changes(self) -> AsyncIterator[None] | None: ...  # poussée (NOTIFY, canal de l'appli web) ; None : interrogation
    async def aclose(self) -> None: ...

type ConfigSourceFactory = Callable[[Mapping[str, JsonValue], Path], ConfigSource]  # params, dossier d'amorçage
```

- `changes()` est nécessaire dès maintenant. Le plan prévoit que l'appli web **pousse** la config par le canal ouvert par loom. Si l'interface ne prévoyait que l'interrogation, il faudrait la casser en janvier. Il faut aussi la **versionner** (un numéro de contrat, comme pour l'appli web), puisque des paquets tiers vont l'implémenter.
- Le port va dans `core/ports`, qui n'importe que la bibliothèque standard. Les adaptateurs vont dans `adapters/config_sources/`, et le registre de points d'entrée est calqué sur `runtime/sources.py` : lecture des métadonnées sans import, et refus de deux paquets qui déclarent le même nom. Les connecteurs fournis s'enregistrent par `pyproject`, comme `forge`. Ces emplacements respectent les contrats de couches d'import-linter.

#### Le chargeur lit un arbre, pas le disque

On abstrait les lectures (`read_bytes`, `is_file`, `list`) derrière un `FileTree` (le disque, résolution des liens symboliques gardée) et un `SnapshotTree` (en mémoire, chemins normalisés, `..` refusé). La source fichiers garde donc exactement la protection actuelle de `_within_resources`.

#### Amorce `loom-boot.yaml` et `workpath`

Voir les décisions A-1 à A-6. Pour une source distante, seul le bloc `config:` de l'amorce change de forme :

```yaml
# loom-boot.yaml
version: 1
profile: prod
env_file: .env
config:
  source: postgres          # point d'entrée du groupe de sources
  dsn_env: LOOM_CONFIG_DSN
  poll_interval: 30
```

La config distante ne peut contenir ni bloc `config:` (pas de chaînage), ni `profile`, ni `env_file`. Elle porte son `workpath`, comme la config en fichiers.

#### Une version par source

| Source | Version | Détection |
|---|---|---|
| Fichiers | sha256 des documents lus | `watchfiles` comme aujourd'hui, puis comparaison de l'empreinte. Un `touch` ou un enregistrement sans modification ne recharge plus. |
| Base | numéro de publication | interrogation de `current` ; en plus, sous Postgres, `pg_notify` (le code existe déjà dans `adapters/bus/postgres.py`) |
| URL | sha256 de l'archive | requête conditionnelle avec `If-None-Match`. L'ETag ne sert pas de version : un ETag faible ou plusieurs serveurs derrière l'URL provoqueraient des rechargements pour rien. |

### Impact complet avec sources distantes

| Module | Changement | Ampleur |
|---|---|---|
| `config/loader.py` (766 l.) | Lectures passées par l'arbre. Une douzaine de fonctions sont concernées : `_read_yaml`, `_load_agents`, `_with_prompt`, `_within_resources`, `_skill_read`, `_templates_read`, `_style_read`, `_logos_read`, `_fonts_read`, `_schema`, `_absolute_sessions`. Nouvelle entrée par instantané ; `load_config(path)` inchangée. | **L** |
| `agents/spec.py`, `runtime/wiring.py:1144`, `config/compaction.py` | `system_text` relit le `system_file` **à chaque montage**. Le prompt devient lu au chargement, comme les skills, et une config forme un tout cohérent. | S |
| `config/models.py`, `schema.py`, `errors.py` | Modèle de l'amorce (`loom-boot.yaml`, bloc `config:`), clé `workpath` obligatoire, `config_version` et `origin` sur `LoomConfig`, `profile` et `env_file` retirés de la config, JSON Schema de l'amorce et de la config. `ConfigError` doit accepter un chemin logique avec son origine. | M |
| `config/references.py`, `runtime/wiring.py` | Modules voisins importés depuis `workpath` (`_importable`) ; `base_dir` remplacé par `workpath` pour les chemins de travail des outils, du stockage, de l'audit et des serveurs MCP. | M |
| `config/env_file.py` | Le `.env` se cherche à côté de `loom-boot.yaml`, et `env_file:` se lit dans l'amorce. | S |
| Nouveaux : port, registre, 3 adaptateurs | Fichiers, base (SQLite en bibliothèque standard, Postgres par l'extra `postgres` existant), URL avec `httpx2`, qui est déjà une dépendance du noyau : **aucun nouvel extra**. | L |
| `access/http/serve.py` | En dev, surveillance de l'amorce, de `path` (documents) et des `.py` de `workpath` (sauf ce que loom y écrit). Superviseur piloté par la version de la source. Il transmet l'instantané au process neuf, pour que celui-ci charge exactement la version détectée et pas une plus récente. La règle « refusé en prod » est à revoir (D-1). | M |
| `access/cli/main.py` | `--config` désigne `loom-boot.yaml` (défaut `./loom-boot.yaml`) ; `_config()` passe par l'amorce. `validate` gagne une section SOURCE (nom, origine, version, nombre de documents). `worker` recharge par relance. Commandes de publication et de lecture d'une config (D-6). | M |
| `access/api.py` | `from_config` est synchrone et peut être appelée dans une boucle déjà lancée : la lecture de la source se fait donc dans un thread avec sa propre boucle. Propriété `config_version`. | S |
| `core/events/payloads.py` | `RunStarted.config_version`, en contenu et non en facette. Affiché par `inspect` et repris par l'exportateur. | S |
| `runtime/wiring.py` `_protected_paths` + `loom.sql` | La racine protégée devient `workpath`, avec ses modules Python (code et données y cohabitent) ; l'amorce et la config en fichiers restent protégées. Le fichier SQLite de config est protégé. Les tables de config sont refusées par `loom.sql`, par un préfixe réservé, quelle que soit la base. | S |
| Docs | `configuration.md`, `conception.md` (§ rechargement), régénération de `docs_for_rag`, site de formation, CHANGELOG | M |

**Sur « le code de connexion de loom.sql »** : `PostgresDatabase` et `SqliteDatabase` sont faits pour des requêtes construites par un LLM (analyse sqlglot, autorisateur, limites). Ce qu'on réutilise réellement, c'est la convention `dsn_env`, asyncpg et sqlite3, et `LISTEN/NOTIFY` du bus. Il est déconseillé d'importer `tools_builtin.sql` depuis la couche config.

### Connecteurs : recommandations

#### Base de données

Passer de « une table » à un modèle par publication :

```sql
CREATE TABLE loom_config_release (config text, release bigint, published_at timestamptz DEFAULT now(), note text,
                                  PRIMARY KEY (config, release));
CREATE TABLE loom_config_document (config text, release bigint, path text, content bytea NOT NULL,
                                   PRIMARY KEY (config, release, path),
                                   FOREIGN KEY (config, release) REFERENCES loom_config_release);
CREATE TABLE loom_config_current (config text PRIMARY KEY, release bigint NOT NULL);
```

- **Publier** se fait en une transaction : on insère la publication et ses documents, on bascule `current`, puis on appelle `pg_notify`.
- **Lire** se fait en une transaction `REPEATABLE READ`.
- **Revenir en arrière** se résume à un `UPDATE current`, ce qui sera utile à l'appli web.
- La colonne `config` permet à plusieurs instances ou environnements de partager une même base.
- Il faut documenter un rôle Postgres en lecture seule pour loom.

#### URL

- **Le format : une archive plutôt qu'un manifeste.** C'est une seule requête, un seul ETag, et l'archive est atomique par nature. Une future signature porte sur un seul fichier. Avec un manifeste, il faut N+1 requêtes, et les documents peuvent changer entre le manifeste et leur lecture.
- **À verrouiller :**
  - HTTPS seulement, et pas de redirection vers HTTP ;
  - chemins absolus, `..` et liens symboliques refusés dans l'archive ;
  - plafonds de taille et de nombre d'entrées, contre les bombes de décompression ;
  - délais d'attente ;
  - contenu des documents jamais écrit dans les logs.

#### Pour toutes les sources

- **Source inaccessible au démarrage** : sortie en code 2 avec un message clair, sans cache local en V1.
- **Source inaccessible en cours de route** : la config actuelle continue de servir, avec un avertissement et des nouvelles tentatives espacées.
- **Interrogation** : un intervalle avec une petite part d'aléa, pour que N instances ne frappent pas la source au même instant.

### Décisions qui restaient ouvertes

| # | Question | Recommandation |
|---|---|---|
| D-1 | Quel rechargement ? | **Tranché (A-8) : pas de rechargement à chaud.** Proposition d'origine : **Relance du process, généralisée** (`serve` + `worker`), autorisée en `prod` **quand elle vient d'une nouvelle version validée**. Le rechargement dans le process vivant (garder l'ancienne config pour les runs en cours, refuser les clés d'infrastructure comme `storage`, `server`, `triggers` ou la file) touche `Loom`, les routes et le pool MCP : c'est trop risqué avant le gel. `loom mcp` en stdio et la librairie ne rechargent pas en V1 ; on leur fournit un utilitaire qui rend les configs validées. |
| D-2 | Source fichiers en `prod` | **Tranché (A-8) : pas de rechargement à chaud.** Proposition d'origine : Rechargement désactivé par défaut et activable explicitement, pour garder le comportement actuel. Les modifications de `.py` et du `.env` continuent de recharger en dev, en dehors de la version. Sinon, `serve --reload` régresse. |
| D-3 | Profil | **Tranché (A-2).** Choisi **en local seulement** : option, puis `LOOM_PROFILE`, puis l'amorçage. Un `profile:` dans la config distante est refusé, pour qu'une base ne puisse pas faire passer une instance de prod en dev. Les surcharges `profiles:` restent permises. |
| D-4 | Confiance | Par défaut, une config distante ne peut déclarer ni serveur MCP stdio, ni `imports`, ni `tool_sources`. L'amorçage local doit le permettre explicitement (D-4 se décide sur l'hôte). La signature peut attendre janvier, mais l'interface doit prévoir dès maintenant le point où elle se vérifiera. |
| D-5 | Secrets | La règle n'est pas garantie aujourd'hui : `mcp_servers[].env` passe des valeurs **en clair**, et les `params` des outils sont libres. Avec une source distante, `loom validate` avertit sur les noms qui ressemblent à des secrets (KEY, TOKEN, SECRET, PASSWORD, DSN), et c'est une erreur en `prod`. |
| D-6 | Publication | Une commande qui valide un dossier local puis le publie dans la base, ou construit l'archive, et une commande qui récupère une version dans un dossier local. Noms à choisir. Sans elles, la livraison de janvier n'est pas utilisable en open source. |
| D-7 | Schéma de la base, archive ou manifeste | Le modèle par publication ci-dessus, et l'archive. Ce sont les deux questions laissées ouvertes dans le plan. |
| D-8 | Statut des instances | Optionnel : chaque instance écrit dans la base la version appliquée ou refusée, avec la raison. Sinon, une publication refusée par les instances passe inaperçue. C'est un plus pour l'appli web. |
