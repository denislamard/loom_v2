# loom_ia.access.cli.main

Module `loom_ia.access.cli.main` de loom_v2 (fichier `src/loom_ia/access/cli/main.py`).

Ligne de commande `loom` (fonction N4).

    loom validate                  vérifie la config et monte les agents
    loom run demo "Bonjour"        lance un run (`--stream` pour le direct,
                                   `--attach photo.jpg` pour joindre une image,
                                   `--state client=C-002` pour poser du state)
    loom state <session>           state d'une session, en JSON
    loom resume <run_id>           reprend un run interrompu
    loom replay <run_id>           rejoue un run, à l'identique ou en variante
                                   (`--journal F` : les runs d'un journal exporté)
    loom inspect <run_id>          relit un run : son arbre, sa réponse, son bilan
    loom eval suite.yaml           joue une suite d'évals, variante par variante
    loom approve <run_id>          autorise ce que le run attend, et le reprend
    loom reject <run_id>           refuse ce que le run attend, et le reprend
    loom serve                     sert l'API REST (`--reload` : relancé quand
                                   un fichier de la config change)
    loom mcp                       sert les agents en MCP, sur stdio
    loom keys create <nom>         fabrique une clé d'API
    loom worker                    consomme la file des tâches de fond
    loom storage sql               SQL du stockage Postgres déclaré
    loom memory export <agent> F   entretient la mémoire d'un agent (loom.memory) :
                                   export, import, reindex, eval
    loom schema                    JSON Schema du fichier de configuration

`--config` désigne le fichier de configuration (`./loom.yaml` par
défaut). Avant de le lire, chaque commande pose les variables du fichier
d'environnement du projet : `env_file:` de la config, sinon le `.env` de
son dossier ; `--env-file` en désigne un autre, `--no-env-file` n'en lit
aucun, et une variable déjà définie l'emporte toujours.

Les codes de sortie : 0 tout va bien, 1 le run a échoué, 2 la
configuration ou la demande est en cause. Un refus d'exploitation sort lui aussi
en 2, sur une ligne de `stderr` et sans trace : budget ou quota atteint, run
mené par un autre worker, fichier ou réseau inaccessible, entrée standard fermée
quand une réponse était attendue.

## Constantes de `loom_ia.access.cli.main`

- `PROG = 'loom'` : Nom du programme.
- `USER_ENV = 'LOOM_USER'` : Utilisateur des commandes qui agissent, faute de `--user` (décision D-10 des utilisateurs et rôles).
- `DEFAULT_CONFIG = Path('loom.yaml')` : Fichier de configuration, sans `--config`.
- `MISSING_EXTRA = "{what} demande l'extra '{extra}' (installer l'extra : loom-ia[{extra}])"` : Message d'une commande dont l'extra n'est pas installé.
- `OK = 0` : Code de sortie : la commande a abouti.
- `FAILED = 1` : Code de sortie : la commande a échoué (run en échec, élément inconnu, journal illisible…).
- `REFUSED = 2` : Code de sortie : la demande est refusée (config, droits, arguments, budget…).
- `_PERIODS = {'day': 'day', 'month': 'month', 'jour': 'day', 'mois': 'month'}` : `loom report --period` : l'écriture d'avant (`--periode jour|mois`) reste comprise.
- `_BIENTOT = timedelta(days=7)` : Délai au-delà duquel `loom validate` ne dit plus qu'une clé expire bientôt.
- `_WIDTH = 88` : Largeur visée d'une ligne du rapport : le verdict s'aligne à droite, les listes s'y replient.
- `_LABEL = 11` : Largeur minimale des libellés de section.
- `_LISTED = _WIDTH - 2 - _LABEL - 2` : Largeur d'une liste repliée, à droite des libellés.
- `_ASKS = '*'` : Marques d'un droit dans l'arbre des agents, et ce que dit la légende.
- `_BRANCH = 11` : Largeur minimale du nom d'une branche.
- `_FALLBACK_SHOWN = 40` : Longueur montrée d'un message de repli : le contrat se lit d'une ligne.
- `_FATE_ORDER = ('journal', 'run', 'relaunched', 'double', 'refused')` : Ordre dans lequel le rapport dit le sort des appels d'outil de la variante.
- `MEMORY_JOURNAL = "Journal en mémoire : il ne vit que le temps d'un process ; cette commande ne voit pas les runs des autres commandes lo…` : Avertissement des commandes qui relisent le journal quand il est en mémoire.

## Fonction `loom_ia.access.cli.main.main`

```python
def main(argv: Sequence[str] | None=None) -> int
```

Point d'entrée de `loom` : lit la ligne de commande, lance la sous-commande et traduit ses erreurs en code de sortie.

Les refus sont dits sur stderr et rendent 2 : config invalide ; agent, run,
approbation ou client inconnu ; accès refusé ; utilisateur manquant ;
demande mal formée ; budget ou quota atteint ; run mené ailleurs ; journal
occupé ; stockage pas prêt ; clé de sceau absente ; entrée standard fermée ;
erreur d'entrée-sortie. Un journal illisible ou altéré, et Ctrl-C, rendent 1.

**Arguments :**

- `argv` : Arguments, sans le nom du programme ; à défaut, ceux du process.

**Renvoie :**

Le code de sortie : 0 (`OK`), 1 (`FAILED`) ou 2 (`REFUSED`).

## Fonction `loom_ia.access.cli.main.build_parser`

```python
def build_parser() -> argparse.ArgumentParser
```

L'analyseur de la ligne de commande : options globales, puis une sous-commande par action.

Sous-commandes : `validate`, `run`, `state`, `resume`, `replay`,
`inspect`, `eval`, `approve`, `reject`, `answer`, `serve`,
`mcp`, `keys create`, `report`, `sessions` (`list`, `show`,
`files`, `export`, `delete`), `worker`, `retention`,
`storage sql`, `memory` (`export`, `import`, `reindex`, `eval`)
et `schema`. Les commandes qui agissent prennent `--user` (à défaut
`LOOM_USER`), celles qui visent un client `--tenant`. Les options
globales (`--config`, `--profile`, `--env-file`, `--no-env-file`)
se lisent avant comme après la sous-commande, où elles l'emportent.

## Fonction `loom_ia.access.cli.main._global_options`

```python
def _global_options(parser: argparse.ArgumentParser, *, after: bool=False) -> None
```

`--config`, `--profile`, `--env-file`, `--no-env-file`.

`after` : sur une sous-commande, sans valeur par défaut (`SUPPRESS`),
pour ne pas effacer celle donnée avant la sous-commande.

## Fonction `loom_ia.access.cli.main._subcommands`

```python
def _subcommands(parser: argparse.ArgumentParser) -> list[argparse.ArgumentParser]
```

Les sous-commandes, à tous les niveaux (`sessions show`, `keys new`…).

## Fonction `loom_ia.access.cli.main._config`

```python
def _config(args: argparse.Namespace, path: Path | None=None) -> LoomConfig
```

La config de la commande, lue après le fichier d'environnement de son projet.

Le fichier passe avant la config : il peut donner `LOOM_PROFILE`. Le
rapport reste sur `args` (`env_report`) pour `validate` et le bandeau
de `serve` ; en profil prod, des variables venues du fichier valent un
avertissement.

## Fonction `loom_ia.access.cli.main.cmd_validate`

```python
def cmd_validate(args: argparse.Namespace) -> int
```

`loom validate` : vérifie la config, monte chaque agent pour chaque client, puis imprime le rapport.

Le verdict passe en tête, puis les sections CONFIGURATION, EXTENSIONS,
EXÉCUTION, ACCÈS et CLIENTS (ou AGENTS), et les avertissements.
`--no-keys` monte les agents sans créer leurs clients de modèle : une clé
d'API ou un SDK absent ne fait pas échouer. Si un montage échoue, le rapport
partiel s'imprime avec un verdict d'échec, avant l'erreur.

**Arguments :**

- `args` : Arguments de la commande.

**Renvoie :**

0 si tout se monte ; une erreur remonte à `main`, qui la traduit.

## Fonction `loom_ia.access.cli.main._validate`

```python
async def _validate(args: argparse.Namespace, *, root: Path) -> int
```

Le rapport est construit d'abord, puis imprimé : le verdict passe en tête.

Si le montage d'un agent échoue, ce qui a été construit s'imprime quand
même, avec un verdict d'échec, avant que l'erreur ne remonte : on y lit
où le montage s'est arrêté.

## Classe `loom_ia.access.cli.main._Field`

```python
@dataclass(frozen=True, slots=True)
class _Field
```

Une ligne de section : son libellé, sa valeur, et ses lignes de suite.

## Classe `loom_ia.access.cli.main._Warning`

```python
@dataclass(frozen=True, slots=True)
class _Warning
```

Un avertissement du rapport : son sujet, puis ce qu'il en dit.

## Classe `loom_ia.access.cli.main._Section`

```python
@dataclass(slots=True)
class _Section
```

Une section : des champs alignés, puis des lignes libres (les agents).

### Méthode `loom_ia.access.cli.main._Section.add`

```python
def add(self, label: str, value: str, more: Iterable[str]=()) -> None
```

Ajoute un champ aligné : libellé, valeur, et lignes de suite.

### Méthode `loom_ia.access.cli.main._Section.add_listed`

```python
def add_listed(self, label: str, names: Iterable[str]) -> None
```

Une liste, repliée sur plusieurs lignes si elle dépasse la largeur.

## Classe `loom_ia.access.cli.main._Report`

```python
@dataclass(slots=True)
class _Report
```

Ce que `loom validate` imprime ; `root` est le dossier de la config.

**Attributs :**

- `config` : Fichier de configuration, tel que donné.
- `root` : Dossier de la config : les chemins du rapport s'y rapportent.
- `sections` : Sections, dans l'ordre d'impression.
- `warnings` : Avertissements, chacun dit une fois.
- `mounted` : Agents montés, tous clients confondus.
- `marks` : Marques de droits montrées dans l'arbre des agents : la légende n'explique qu'elles.

### Méthode `loom_ia.access.cli.main._Report.section`

```python
def section(self, title: str) -> _Section
```

Ouvre une section à la suite du rapport, et la rend.

### Méthode `loom_ia.access.cli.main._Report.warn`

```python
def warn(self, subject: str, message: str) -> None
```

Un avertissement, dit une fois : un agent monté pour plusieurs clients le redirait.

### Méthode `loom_ia.access.cli.main._Report.warn_said`

```python
def warn_said(self, text: str) -> None
```

Un avertissement déjà rédigé « Sujet : message » (ceux de `runtime`).

### Méthode `loom_ia.access.cli.main._Report.shown`

```python
def shown(self, path: Path | str) -> str
```

Un chemin sous la racine s'écrit `./…` ; un autre reste tel quel.

## Fonction `loom_ia.access.cli.main._render`

```python
def _render(report: _Report, *, failed: bool=False) -> str
```

Le rapport en texte : verdict, racine, sections, puis les avertissements.

## Fonction `loom_ia.access.cli.main._packed`

```python
def _packed(names: list[str], width: int) -> list[str]
```

Des noms séparés par des virgules, en lignes d'au plus `width` caractères.

Un nom n'est jamais coupé ; une liste vide se dit « aucun ».

## Fonction `loom_ia.access.cli.main._configuration`

```python
def _configuration(report: _Report, config: LoomConfig, args: argparse.Namespace, printed: set[str]) -> None
```

Section CONFIGURATION : profil, fichier d'environnement, modèles et clients.

Le profil actif et ses surcharges, le fichier d'environnement lu, les
modèles déclarés et les clients ; avec les avertissements sur les clés
d'API et les modèles.

## Fonction `loom_ia.access.cli.main._extensions`

```python
def _extensions(report: _Report, config: LoomConfig, registry: Registry) -> None
```

Section EXTENSIONS : outils fournis utilisés, paquets de points d'entrée installés, sources d'outils déclarées, outils et politiques enregistrés.

## Fonction `loom_ia.access.cli.main._package_lines`

```python
def _package_lines(config: LoomConfig) -> list[str]
```

Les points d'entrée installés — lus sans rien importer —, un par ligne.

Rien n'est dit sans source déclarée ni paquet installé : la plupart des
configs n'en ont pas. Le groupe se dit une fois.

## Fonction `loom_ia.access.cli.main._execution`

```python
def _execution(report: _Report, config: LoomConfig, printed: set[str]) -> None
```

Section EXÉCUTION : stockages, file, bus, chiffrement, rétention et télémétrie.

Journal, artefacts, idempotence, file, bus, chiffrement, rétention et
télémétrie, avec les avertissements de chacun (variable absente, rien de
chiffré, rien ne s'efface…).

## Fonction `loom_ia.access.cli.main._storage_said`

```python
def _storage_said(label: str, declared: EventsStorage | IdempotencyStorage, report: _Report) -> str
```

Un stockage : son backend, et son dossier relatif à la racine ou sa variable de DSN.

## Fonction `loom_ia.access.cli.main._retains_everything`

```python
def _retains_everything(config: LoomConfig) -> bool
```

Vrai si aucune borne de rétention n'est posée, ni à la racine ni pour un client.

## Fonction `loom_ia.access.cli.main._telemetry_lines`

```python
def _telemetry_lines(config: LoomConfig) -> list[str]
```

Logs et exports, échanges bruts, puis un collecteur par ligne et le masquage.

## Fonction `loom_ia.access.cli.main._access`

```python
def _access(report: _Report, config: LoomConfig) -> None
```

Section ACCÈS : rôles, comptes, journal des refus, clés d'API, MCP HTTP et portes (déclencheurs `POST …/v1/hooks/<nom>`).

## Fonction `loom_ia.access.cli.main._mount`

```python
async def _mount(report: _Report, config: LoomConfig, registry: Registry, *, no_keys: bool) -> None
```

Monte chaque agent pour chaque client ; avec des clients, les agents se rangent sous eux.

## Fonction `loom_ia.access.cli.main._profile_said`

```python
def _profile_said(config: LoomConfig, given: str | None) -> tuple[str, str]
```

Le profil actif, d'où il vient et ce qu'il change ; puis les surcharges déclarées.

Un profil qu'on ne voit pas est un profil qu'on oublie : un déploiement
qui croit être en prod doit pouvoir le lire ici.

## Fonction `loom_ia.access.cli.main._profile_line`

```python
def _profile_line(config: LoomConfig, given: str | None) -> str
```

Le profil en une ligne, pour le bandeau de `loom serve`.

## Fonction `loom_ia.access.cli.main._retention_line`

```python
def _retention_line(config: LoomConfig) -> str
```

La borne de rétention, et les clients qui n'ont pas la même.

Une règle qui efface des journaux doit se lire ici : elle ne se rappelle à
personne avant d'avoir supprimé quelque chose.

## Fonction `loom_ia.access.cli.main._telemetry_details`

```python
def _telemetry_details(config: LoomConfig) -> list[str]
```

Une ligne par collecteur, l'état de sa variable d'adresse, puis les motifs de masquage.

La valeur des variables n'est pas imprimée : l'adresse peut porter un jeton,
les en-têtes en portent un. « vide » dit ce que le chargement dit aussi :
ce collecteur n'est pas monté.

## Fonction `loom_ia.access.cli.main._sealing_line`

```python
def _sealing_line(config: LoomConfig) -> str
```

L'état du sceau : quels secrets portent les clés, lequel ferme.

## Fonction `loom_ia.access.cli.main._sealing_report`

```python
def _sealing_report(config: LoomConfig) -> tuple[list[str], list[str]]
```

Une ligne par client — l'empreinte de sa clé ou son absence —, et à part les avertissements du trousseau.

L'empreinte est imprimée, jamais la clé : elle suffit à voir que deux
clients scellent avec la même, ou que celle d'un client a changé. Un
client sans clé ne fait pas échouer la commande : c'est justement l'état
d'un client dont on a effacé la clé, et le reste de la config reste à
valider.

## Fonction `loom_ia.access.cli.main._storage_line`

```python
def _storage_line(declared: EventsStorage | IdempotencyStorage) -> str
```

Un stockage tel que `loom validate` l'affiche.

Le DSN n'est jamais imprimé : seulement le nom de la variable qui le
porte, et si elle est renseignée ici et maintenant — c'est ce qui manque
le plus souvent quand un service refuse de démarrer.

## Fonction `loom_ia.access.cli.main._role_lines`

```python
def _role_lines(config: LoomConfig, report: _Report) -> list[str]
```

Les permissions de chaque rôle d'accès ; ce qui cloche va aux avertissements (§ 8).

## Fonction `loom_ia.access.cli.main._user_lines`

```python
def _user_lines(config: LoomConfig) -> list[str]
```

Les rôles de chaque utilisateur, et son client quand la config en nomme.

## Fonction `loom_ia.access.cli.main._key_lines`

```python
def _key_lines(config: LoomConfig, report: _Report) -> list[str]
```

Ce que chaque clé d'API permet ; ce qui cloche va aux avertissements (jalon J5.2a, point 39).

## Fonction `loom_ia.access.cli.main._tenant_lines`

```python
def _tenant_lines(tenant: Tenant) -> list[str]
```

Ce qu'un client surcharge, en quelques lignes lisibles (fonction L1, point 34).

## Classe `loom_ia.access.cli.main._Branch`

```python
@dataclass(frozen=True, slots=True)
class _Branch
```

Une branche de l'arbre d'un agent : son nom, puis ses lignes.

## Fonction `loom_ia.access.cli.main._tree`

```python
def _tree(header: str, branches: Sequence[_Branch], *, indent: str) -> list[str]
```

L'en-tête de l'agent, puis une branche par outil ou réglage, alignées.

## Fonction `loom_ia.access.cli.main._agent_header`

```python
def _agent_header(spec: AgentSpec, chain: Callable[[tuple[str, ...]], str], order: str='effects') -> str
```

« agent · HAIKU · 6 outils Python · sous-agent : forgeron ».

## Fonction `loom_ia.access.cli.main._agent_lines`

```python
async def _agent_lines(loom: Loom, tenant_id: TenantId, spec: AgentSpec, report: _Report, *, indent: str) -> list[str]
```

L'arbre d'un agent monté pour un client : ses outils, ses réglages, ses sources.

## Fonction `loom_ia.access.cli.main._able`

```python
def _able(config: LoomConfig, spec: AgentSpec, permission: Permission) -> set[str]
```

Utilisateurs qui ont cette permission sur l'agent, sa liste `access` comprise.

Un rôle compte s'il porte la permission, vaut pour l'agent, et figure
dans la liste de l'agent quand il y en a une (règles 2 et 3 du § 4.3).

## Fonction `loom_ia.access.cli.main._access_lines`

```python
def _access_lines(config: LoomConfig, spec: AgentSpec, asking: Sequence[str], report: _Report) -> list[str]
```

Qui lance, répond, arrête et approuve cet agent ; ce qui bloque va aux avertissements.

`asking` : les outils montés qui demandent une approbation à chaque appel.

## Fonction `loom_ia.access.cli.main._internal_line`

```python
def _internal_line(loom: Loom, tenant_id: TenantId, spec: AgentSpec, *, indent: str) -> str
```

Un agent interne (`_compaction`) en une ligne : son modèle et ses politiques.

## Fonction `loom_ia.access.cli.main._settings_lines`

```python
def _settings_lines(settings: Settings, report: _Report, *, agent: str, tool: str) -> list[str]
```

Les réglages d'un outil fourni : ses dossiers ou ses bases, ses clés, ses options.

Un droit destructif accordé sans approbation est marqué, et va aux avertissements.

## Fonction `loom_ia.access.cli.main._mark`

```python
def _mark(grant: Grant, report: _Report) -> str
```

« * » : approbation requise ; « ! » : destructif, sans approbation.

## Fonction `loom_ia.access.cli.main._grants_said`

```python
def _grants_said(grants: Sequence[Grant], report: _Report) -> str
```

« read, write · overwrite*, delete* » : ce qui passe seul, puis ce qui attend un humain.

## Fonction `loom_ia.access.cli.main._matrix`

```python
def _matrix(place: Place, report: _Report) -> list[str]
```

Les droits d'une base, une ligne par table, une colonne par droit accordé quelque part.

## Fonction `loom_ia.access.cli.main._chain`

```python
def _chain(models: tuple[str, ...], mapped: Mapping[str, str] | None=None) -> str
```

Modèle et ses secours : `M3_MAIN → SONNET`.

« → » sépare les secours ; un modèle qu'un client fait correspondre à un
autre s'écrit `OPUS (HAIKU pour ce client)`, pour ne pas se lire comme un
secours (essai de la fonction E6, 19/10 : sous « client dupont », le juge affichait
« modèle OPUS » alors que ce client le fait tourner sur HAIKU).

## Fonction `loom_ia.access.cli.main._contracts`

```python
def _contracts(spec: AgentSpec) -> list[tuple[str, OutputContract, bool]]
```

Contrats de sortie d'un agent : cible, contrat, et si son auteur le répare.

La réponse finale et un rôle se réparent ; un outil, non.

## Fonction `loom_ia.access.cli.main._skill_size`

```python
def _skill_size(text: str) -> str
```

Ce que coûte un skill : il part avec chaque appel de modèle de l'agent.

## Fonction `loom_ia.access.cli.main._contract_line`

```python
def _contract_line(contract: OutputContract, *, repairs: bool) -> str
```

Un contrat en une ligne : ses règles, ses réparations, ce qui se passe à l'échec.

À l'essai de la fonction E1 du 19/10, `validate` montrait que `loom.contract` était posé,
mais pas ce qu'il exigeait. Les motifs sont montrés tels quels ; un schéma,
par ses champs requis.

## Fonction `loom_ia.access.cli.main.cmd_run`

```python
def cmd_run(args: argparse.Namespace) -> int
```

`loom run <agent> <message>` : lance un run et attend sa fin ; la réponse sur stdout, le bilan sur stderr.

Options : `--session` (journal où rattacher le run), `--run-id`,
`--attach` (image jointe, répétable), `--state CLE=VALEUR` (state posé à
l'ouverture, répétable), `--judges auto|force|skip`, `--stream`
(réponse et déroulé au fil de l'eau), `--json`, `--json --onlydata`
(seulement `data`), `-q` (bilan tu). Sans `--run-id`, dans une
session, le message peut répondre à une question qu'un run attend.

**Arguments :**

- `args` : Arguments de la commande.

**Renvoie :**

0 si le run a abouti, ou attend une réponse à une question ; 1 sinon ;
2 pour une demande refusée (`--onlydata` sans `--json`, pièce jointe
illisible, clé de `--state` donnée deux fois).

## Fonction `loom_ia.access.cli.main._budgets_reached`

```python
async def _budgets_reached(loom: Loom, result: RunResult, tenant: TenantId | None) -> list[BudgetExceeded]
```

Plafonds atteints pendant le run, lus dans son journal (sous-runs compris).

À l'essai de la fonction J4 du 21/10, ni la console ni le résumé ne disaient qu'un budget
avait arrêté le run ou l'avait averti : seul le journal le montrait. Le
déroulé `--stream` en a déjà une ligne. Lu par la même instance que le
run : un journal en mémoire n'existe que dans ce process.

## Fonction `loom_ia.access.cli.main._opening_summaries`

```python
async def _opening_summaries(loom: Loom, result: RunResult, tenant: TenantId | None) -> list[SessionCompacted]
```

Résumés de la session faits à l'ouverture du run, lus dans le journal.

Sans compaction déclarée, il n'y en a pas : le journal n'est pas relu.

## Fonction `loom_ia.access.cli.main._state_item`

```python
def _state_item(text: str) -> tuple[str, JsonValue]
```

Un `--state CLE=VALEUR` : la valeur en JSON si elle se lit ainsi, sinon le texte.

`NaN`, `Infinity` ou `1e999` ne sont pas du JSON : ils restent du texte.

## Fonction `loom_ia.access.cli.main.cmd_state`

```python
def cmd_state(args: argparse.Namespace) -> int
```

State d'une session : ce qui est écrit, sans les valeurs par défaut, en JSON.

## Fonction `loom_ia.access.cli.main.cmd_resume`

```python
def cmd_resume(args: argparse.Namespace) -> int
```

`loom resume <run_id>` : reprend un run interrompu et attend sa fin.

Un run déjà terminé n'est pas repris : la commande le dit, avec la
commande `loom inspect` qui le relit.

**Arguments :**

- `args` : Arguments de la commande.

**Renvoie :**

0 si le run aboutit, attend une réponse, ou était déjà terminé ; 1 sinon.

## Fonction `loom_ia.access.cli.main.cmd_replay`

```python
def cmd_replay(args: argparse.Namespace) -> int
```

Rejoue un run ; rend 1 s'il diverge (ou si la variante échoue), 2 s'il ne peut pas l'être.

Le code de sortie fait de la commande un garde-fou : une config qui ne
reproduit plus un run de référence se voit en CI, sans lire la sortie. En
variante, s'écarter est le but : le code dit seulement si la variante est
allée au bout (0) ou a échoué (1). Avec `--journal` (jalon J6.3b), chaque run
fini du fichier est rejoué ; un seul qui diverge, ou un run inachevé,
rend 1.

## Fonction `loom_ia.access.cli.main._replay_journal`

```python
def _replay_journal(args: argparse.Namespace, config: LoomConfig, models: dict[str, str], doubles: dict[str, Double]) -> int
```

`loom replay --journal` : les runs d'un fichier, l'un après l'autre, puis le bilan.

## Fonction `loom_ia.access.cli.main._print_replay`

```python
def _print_replay(report: ReplayReport, export: Path | None) -> int
```

Le rapport d'un rejeu, en texte ; rend son code de sortie.

## Fonction `loom_ia.access.cli.main.cmd_inspect`

```python
def cmd_inspect(args: argparse.Namespace) -> int
```

Relit un run et ses sous-runs, lisiblement ; 2 s'il est introuvable.

En local, la commande lit le journal elle-même : elle montre le contenu.
`--json` rend la trace telle que l'API la rend à une clé `read_content`.

## Fonction `loom_ia.access.cli.main.cmd_eval`

```python
def cmd_eval(args: argparse.Namespace) -> int
```

Joue une suite d'évals ; 0 si tout passe, 1 si un attendu tombe, 2 si elle est injouable.

Un run non joué (plafond de dépense atteint) rend aussi 1 : ce qu'il
devait éprouver ne l'a pas été. Sans `config` dans la suite, c'est
`--config` qui sert.

## Fonction `loom_ia.access.cli.main._pairs`

```python
def _pairs(values: list[str], option: str, form: str) -> dict[str, str]
```

`nom=valeur` répétés, en table ; un nom donné deux fois est refusé.

## Fonction `loom_ia.access.cli.main._replay_code`

```python
def _replay_code(report: ReplayReport) -> int
```

Identique : 0 si le run se rejoue tel quel ; variante : 0 si elle est allée au bout.

## Fonction `loom_ia.access.cli.main._print_variant`

```python
def _print_variant(report: ReplayReport, comparison: Comparison, export: Path | None) -> None
```

Le run d'origine et sa variante, côte à côte.

## Fonction `loom_ia.access.cli.main.cmd_decide`

```python
def cmd_decide(args: argparse.Namespace) -> int
```

Tranche ce qu'un run attend, puis pilote sa reprise (point 17).

La décision met un travail `resume` en file **dans cette instance** :
sans `--no-wait`, c'est donc ce terminal qui mène le run à son terme et
en affiche la réponse. Avec, la décision est écrite au journal et la
commande sort sans rien reprendre ni attendre : la reprise revient à qui
écoute ailleurs — un worker (la file d'un courtier a reçu le travail),
`loom resume`, ou la reprise au démarrage d'un worker.

Avec une file servie par un courtier (`storage.queue.backend: rabbitmq`),
le travail de reprise part au worker : ce terminal n'a rien à attendre, et
relire le run aussitôt montrerait encore la pause (essai de la fonction H7, 21/10 :
« Statut : paused », code 1, pendant que le worker concluait). La commande
dit donc à qui la reprise est confiée et sort en 0, comme avec `--no-wait`.

## Fonction `loom_ia.access.cli.main.cmd_answer`

```python
def cmd_answer(args: argparse.Namespace) -> int
```

Répond à une question que le run attend (wait_answer), puis pilote sa reprise.

Comme `approve` : la reprise se fait dans ce terminal, sauf `--no-wait`
ou une file servie par un courtier. Plus simple encore : ``loom run <agent>
"<réponse>" --session <session>`` répond aussi, dans la conversation.

Un refus montre la commande à corriger ; une réponse acceptée est répétée
question par question (« Relances → Oui »), pour vérifier ce qui a été
compris avant la reprise (essai réel t-2000 du 02/11, docs/multi-questions.md).

## Classe `loom_ia.access.cli.main._Hint`

```python
@dataclass(frozen=True, slots=True)
class _Hint
```

Ce qu'il faut à une commande `loom answer` pour viser le bon run.

## Classe `loom_ia.access.cli.main._Target`

```python
@dataclass(slots=True)
class _Target
```

L'appel `ask` visé par `loom answer`, et de quoi montrer la commande.

**Attributs :**

- `pending` : La question visée.
- `hint` : De quoi viser le bon run dans la commande montrée.
- `sent` : Les réponses envoyées, numéros remplacés par les libellés (pour l'écho).

## Fonction `loom_ia.access.cli.main._target`

```python
async def _target(loom: Loom, run_id: RunId, call_id: str | None, session: SessionId | None, tenant: TenantId | None) -> _Target | None
```

La seule question visée, ou `None` : run introuvable, ou plusieurs appels sans `--call`.

Sans cible, les réponses partent telles quelles et `Loom.answer` dit ce
qui ne va pas, comme avant.

## Classe `loom_ia.access.cli.main._OutOfChoices`

```python
class _OutOfChoices(InvalidAnswer)
```

Un numéro qui ne désigne aucun choix affiché : refusé avant d'écrire.

## Fonction `loom_ia.access.cli.main._check_choices`

```python
def _check_choices(target: _Target, answers: list[list[str]], tenant: TenantId | None) -> None
```

Refuse un numéro hors des choix d'une question (`--answer 3` sur deux choix).

Avant, il partait au modèle comme texte libre « 3 », sans rien dire. Le
contrôle ne vaut que pour une question qui a des choix dont aucun libellé
n'est un nombre : sur une question sans choix, « 3 » est peut-être la
réponse. Le message libre reste le moyen de répondre ce texte-là.

## Fonction `loom_ia.access.cli.main._answer_command`

```python
def _answer_command(pending: PendingQuestion, hint: _Hint) -> str
```

La commande `loom answer` à compléter : un `--answer` par question, dans l'ordre.

## Fonction `loom_ia.access.cli.main._echo`

```python
def _echo(target: _Target | None, text: str | None, call: str) -> list[str]
```

Ce qui a été compris, à relire avant la reprise : une ligne par question.

« Relances → Oui » ; une question seule sans intitulé : sa valeur ; un message
libre : son texte, coupé à 110 caractères. Sans cible connue : l'appel,
comme avant.

## Fonction `loom_ia.access.cli.main._numbered`

```python
def _numbered(questions: Sequence[Question], answers: list[list[str]]) -> list[list[str]]
```

Chaque valeur qui est le numéro d'un choix (1 à n) devient le libellé de ce choix.

Un libellé qui est lui-même un nombre (« 2 » pour « combien de radiateurs ? »)
reste ce qu'il est ; une question sans choix garde le texte donné.

## Fonction `loom_ia.access.cli.main._json_object`

```python
def _json_object(text: str) -> dict[str, JsonValue] | None
```

Objet JSON d'un argument de ligne de commande, ou None avec un message.

## Fonction `loom_ia.access.cli.main.cmd_report`

```python
def cmd_report(args: argparse.Namespace) -> int
```

`loom report` : consommation d'un run, d'une session, ou du client sur une période.

Un run (`run_id`), une session (`--session`), ou le client sur la
journée ou le mois en cours (`--period day|month`).

`--json` pour le rapport en JSON.

**Arguments :**

- `args` : Arguments de la commande.

**Renvoie :**

0 ; 1 pour un run inconnu ; 2 sans run, session ni période.

## Fonction `loom_ia.access.cli.main._cmd_consumption`

```python
def _cmd_consumption(args: argparse.Namespace, config: LoomConfig) -> int
```

`loom report --period day|month` : la dépense d'un client et ce qui lui reste.

## Fonction `loom_ia.access.cli.main._warn_memory_journal`

```python
def _warn_memory_journal(config: LoomConfig, args: argparse.Namespace) -> None
```

Dit, sur stderr, qu'un journal `memory` ne garde rien d'une commande à l'autre.

À l'essai de la fonction F5 du 19/10, `loom sessions list` répondait « Aucune session. »
juste après un run : exact — chaque commande ouvre un journal vide —, mais
rien ne le disait. Le journal regardé est celui du client de la commande.

## Fonction `loom_ia.access.cli.main.cmd_sessions_list`

```python
def cmd_sessions_list(args: argparse.Namespace) -> int
```

`loom sessions list` : sessions du client, la plus récemment écrite d'abord (identifiant, nombre d'événements, date).

`--json` pour la liste en JSON. Un journal en mémoire est signalé : il
est vide à chaque commande.

**Arguments :**

- `args` : Arguments de la commande.

**Renvoie :**

0.

## Fonction `loom_ia.access.cli.main.cmd_sessions_show`

```python
def cmd_sessions_show(args: argparse.Namespace) -> int
```

Fiche d'une session : ce qu'un approbateur doit voir en revenant.

Les runs, avec leur statut, et chaque approbation en attente avec la
commande qui la tranche. Les arguments des appels ne sont pas affichés —
ils peuvent être longs ou sensibles — mais `--json` donne la fiche
entière, `SessionInfo` tel que l'API le rend.

## Fonction `loom_ia.access.cli.main.cmd_sessions_files`

```python
def cmd_sessions_files(args: argparse.Namespace) -> int
```

Fichiers d'une session, lus au journal ; `--out` les écrit sous leur vrai nom.

Le stockage les range sous leur condensé (`cea7c3….csv`) : seul le
journal dit ce qu'ils sont. Deux fichiers de même nom sont départagés
(`graphique-2.png`), un fichier sans nom garde son condensé, et un
fichier déjà présent dans le dossier n'est pas écrasé sans `--force`.

## Fonction `loom_ia.access.cli.main._file_lines`

```python
def _file_lines(files: Sequence[SessionFile]) -> list[str]
```

Une ligne par fichier : nom, type, taille, origine, outil ou run qui l'a rangé.

## Fonction `loom_ia.access.cli.main._extracted_names`

```python
def _extracted_names(files: Sequence[SessionFile]) -> list[str]
```

Nom de chaque fichier à l'extraction : le sien, sans chemin, départagé s'il revient.

## Fonction `loom_ia.access.cli.main.cmd_sessions_export`

```python
def cmd_sessions_export(args: argparse.Namespace) -> int
```

`loom sessions export <session_id>` : écrit les événements de la session en JSONL, sur stdout ou dans `--out`.

C'est le format que relisent `loom replay --journal` et les cas de rejeu
des évals.

**Arguments :**

- `args` : Arguments de la commande.

**Renvoie :**

0 ; 1 pour une session inconnue.

## Fonction `loom_ia.access.cli.main.cmd_sessions_delete`

```python
def cmd_sessions_delete(args: argparse.Namespace) -> int
```

`loom sessions delete <session_id>` : supprime la session — journal, fichiers et clés d'idempotence (RGPD).

Demande confirmation, sauf avec `--yes`.

**Arguments :**

- `args` : Arguments de la commande.

**Renvoie :**

0 ; 1 pour une session inconnue ; 2 si la suppression n'est pas
confirmée.

## Fonction `loom_ia.access.cli.main.cmd_retention`

```python
def cmd_retention(args: argparse.Namespace) -> int
```

Balaie les sessions dormantes ; sans `--yes`, dit seulement ce qui partirait.

L'essai à blanc est le défaut, et il n'y a pas de question posée : cette
commande est faite pour être mise à l'heure par une plateforme, et une
question sans personne pour y répondre bloquerait le balayage.

## Fonction `loom_ia.access.cli.main.cmd_serve`

```python
def cmd_serve(args: argparse.Namespace) -> int
```

`loom serve` : sert l'API REST (extra `http`) ; `--host` et `--port` remplacent ceux de `server.http`.

Imprime d'abord le bandeau (`serving_banner`). `--reload` relance le
serveur quand un fichier de la config change ; refusé en profil prod.

**Arguments :**

- `args` : Arguments de la commande.

**Renvoie :**

0 à l'arrêt ; 2 sans l'extra `http`, ou `--reload` refusé.

## Fonction `loom_ia.access.cli.main._serve_reloading`

```python
def _serve_reloading(config: LoomConfig, args: argparse.Namespace, host: str, port: int) -> int
```

`loom serve --reload` : un superviseur, et un process qui sert par version de la config.

## Fonction `loom_ia.access.cli.main._cache_said`

```python
def _cache_said(usage: Usage) -> str
```

Le cache, quand il y en a : « tokens : 18/35 » ne compte que l'entrée hors cache.

À l'essai de la fonction J1 du 21/10, la console disait « 50/543 » quand le rapport, cache
compris, disait « 4499/543 », sans qu'aucun des deux nomme le cache.

## Fonction `loom_ia.access.cli.main.serving_banner`

```python
def serving_banner(config: LoomConfig, given: str | None, host: str, port: int, env: EnvFileReport) -> None
```

Le bandeau de `loom serve` : profil, fichier d'environnement, adresses, agents, portes.

`--reload` le fait imprimer par chaque process qui prend la main : ce
qu'on lit après un rechargement est ce que sert le nouveau.

## Fonction `loom_ia.access.cli.main.cmd_mcp`

```python
def cmd_mcp(args: argparse.Namespace) -> int
```

`loom mcp` : sert les agents en MCP sur stdio (extra `mcp`), au nom de `--user` ; rien d'autre ne passe sur stdout.

**Arguments :**

- `args` : Arguments de la commande.

**Renvoie :**

0 à l'arrêt ; 2 sans l'extra `mcp`.

## Fonction `loom_ia.access.cli.main.cmd_keys_create`

```python
def cmd_keys_create(args: argparse.Namespace) -> int
```

`loom keys create <id> --user <utilisateur>` : fabrique une clé d'API et imprime le bloc `security.api_keys` à ajouter à la config.

La clé n'est affichée qu'une fois : la config n'en garde que l'empreinte.
Options : `--scope` (permission gardée, répétable ; défaut : toutes),
`--agent` (agent autorisé, répétable ; défaut : tous), `--expires`
(date ou durée : `2027-01-01`, `90j`, `12h`), `--rate-limit`
(requêtes par minute). Une clé qui peut approuver sans `read_content` est
signalée : les arguments d'un appel en attente lui sont masqués.

**Arguments :**

- `args` : Arguments de la commande.

**Renvoie :**

0 ; 2 pour un `--expires` illisible.

## Fonction `loom_ia.access.cli.main._period`

```python
def _period(value: str) -> str
```

`day` ou `month` ; `jour` et `mois`, l'écriture d'avant, restent compris.

## Fonction `loom_ia.access.cli.main._deadline`

```python
def _deadline(given: str | None) -> datetime | None
```

Une date (`2027-01-01`) ou une durée à partir de maintenant (`90j`).

## Fonction `loom_ia.access.cli.main.cmd_worker`

```python
def cmd_worker(args: argparse.Namespace) -> int
```

`loom worker` : consomme la file des tâches de fond jusqu'à Ctrl-C ou SIGTERM, `--jobs` tâches de front.

Reprend d'abord, pour chaque client, les runs restés en plan. Demande une
file servie par un courtier (`storage.queue.backend: rabbitmq`).

**Arguments :**

- `args` : Arguments de la commande.

**Renvoie :**

0 à l'arrêt.

## Fonction `loom_ia.access.cli.main._worker`

```python
async def _worker(args: argparse.Namespace) -> int
```

Consomme la file jusqu'à Ctrl-C, après avoir repris ce qui traînait.

La reprise d'abord : un worker qui démarre est souvent celui qui remplace
un worker mort, et ce qui restait en plan n'est dans aucune file — il est
au journal (fonction H3).

## Fonction `loom_ia.access.cli.main._on_signals`

```python
def _on_signals(loom: Loom) -> Callable[[], None]
```

Fait finir le worker proprement sur SIGINT et SIGTERM ; rend de quoi défaire.

Un arrêt demandé n'interrompt pas la tâche en cours : elle va au bout, et
c'est ce qui évite de la faire redélivrer pour rien.

## Fonction `loom_ia.access.cli.main._bus_line`

```python
def _bus_line(bus: BusStorage) -> str
```

Le bus tel que `loom validate` l'affiche, sans jamais son raccordement.

## Fonction `loom_ia.access.cli.main.cmd_storage_sql`

```python
def cmd_storage_sql(args: argparse.Namespace) -> int
```

Le SQL à appliquer pour le stockage Postgres que la config déclare.

Rien n'est exécuté : la sortie se relit, se met en revue et s'applique
avec le rôle qui en a le droit (`psql -f`). loom l'applique aussi de
lui-même à la première ouverture, si le rôle connecté le peut.

## Fonction `loom_ia.access.cli.main.cmd_memory`

```python
def cmd_memory(args: argparse.Namespace) -> int
```

`loom memory` : la base de l'agent, hors de tout run (`access/cli/memory.py`).

## Fonction `loom_ia.access.cli.main.cmd_schema`

```python
def cmd_schema(args: argparse.Namespace) -> int
```

`loom schema` : imprime le JSON Schema du fichier de configuration.

**Arguments :**

- `args` : Arguments de la commande.

**Renvoie :**

0.

## Classe `loom_ia.access.cli.main._Live`

```python
class _Live
```

Un run suivi en direct : le texte du modèle sur stdout, le déroulé sur stderr.

Les événements des sous-runs arrivent dans le flux ; leurs lignes sont
décalées selon leur profondeur.

### Méthode `loom_ia.access.cli.main._Live.show`

```python
def show(self, item: StreamItem) -> None
```

Affiche un morceau du flux : le texte sur stdout ; sur stderr, la ligne de déroulé d'un événement qui en a une, et une réponse reprise.

## Fonction `loom_ia.access.cli.main._tenant`

```python
def _tenant(args: argparse.Namespace) -> TenantId | None
```

Client demandé par `--tenant` ; `None` laisse la config décider (point 33).

## Classe `loom_ia.access.cli.main.MissingUser`

```python
class MissingUser(Exception)
```

Une commande qui agit sans dire au nom de qui : ni `--user`, ni `LOOM_USER`.

## Fonction `loom_ia.access.cli.main._command`

```python
def _command(args: argparse.Namespace) -> str
```

La commande lancée, telle que le journal d'audit la nomme : `approve`, `keys create`.

## Fonction `loom_ia.access.cli.main._acting`

```python
def _acting(args: argparse.Namespace, config: LoomConfig) -> tuple[Actor, TenantId]
```

L'utilisateur de la commande et son client (décision D-10 des utilisateurs et rôles).

`--user`, sinon `LOOM_USER` — qui peut se poser dans le `.env` du
projet, que `loom` lit avant tout. L'identité est **déclarative** : la
CLI lit le journal elle-même, loom ne peut pas l'authentifier ; les droits
y sont un garde-fou contre l'erreur, pas une sécurité (§ 6.4). Un
`--tenant` qui n'est pas celui de l'utilisateur est refusé.

## Fonction `loom_ia.access.cli.main._budget_line`

```python
def _budget_line(budgets: Budgets) -> str
```

Limites d'un agent, en une ligne : « run max_cost 0.05, max_calls 20 ; stop ».

## Fonction `loom_ia.access.cli.main._source_branches`

```python
async def _source_branches(agent: str, tools: ToolExecutor, report: _Report, imposed: Mapping[str, Approval]) -> list[_Branch]
```

Ouvre les sources de l'agent — serveurs MCP, sources de paquet — et liste leurs outils.

Ce qu'un serveur a écrit est rendu imprimable : il ne pilote pas le terminal.
Un outil MCP irréversible que rien ne soumet à une approbation — ni sa
déclaration, ni le client (`imposed`) — est un avertissement.

## Fonction `loom_ia.access.cli.main._report`

```python
def _report(result: RunResult, *, as_json: bool=False, quiet: bool=False, brief: bool=False, only_data: bool=False, tenant: TenantId | None=None, summaries: Sequence[SessionCompacted]=(), budgets: Sequence[BudgetExceeded]=()) -> int
```

Rend le run : la réponse sur stdout, le bilan sur stderr.

`quiet` tait la réponse (déjà diffusée par `--stream`) ; `brief`
(`-q`) tait le bilan, sauf ce qui demande d'agir : une approbation en
attente, une erreur. `only_data` (`--json --onlydata`) ne rend que `data`.

## Fonction `loom_ia.access.cli.main._questions_text`

```python
def _questions_text(result: RunResult) -> str
```

Les questions en attente, lisibles : le texte, puis les choix numérotés.

Avec plusieurs questions dans un même appel, chacune est précédée de
« Question i/n · intitulé » et séparée de la suivante par une ligne vide :
sans cela, deux listes de choix numérotées à partir de 1 se suivaient sans
dire où commençait la seconde (essai réel t-2000 du 02/11). Une question
seule garde son rendu d'avant.

## Fonction `loom_ia.access.cli.main._question_blocks`

```python
def _question_blocks(pending: PendingQuestion) -> list[str]
```

Un bloc par question d'un appel `ask` : en-tête s'il y en a plusieurs, texte, choix.

## Fonction `loom_ia.access.cli.main._choice_lines`

```python
def _choice_lines(question: Question) -> list[str]
```

Les choix numérotés d'une question, tels que `--answer <n°>` les désigne.

## Fonction `loom_ia.access.cli.main._placeholders`

```python
def _placeholders(questions: Sequence[Question]) -> list[str]
```

Les « <…> » de la commande `loom answer` : l'intitulé de chaque question.

Sans intitulé, « <réponse i> » ; deux intitulés identiques prennent leur rang
(« <Client 1> », « <Client 2> »). Sans guillemets, comme « <n°> » : copiée
telle quelle, la ligne échoue dans le shell (« < » redirige) au lieu
d'envoyer « <Relances> » au modèle comme une réponse.

## Fonction `loom_ia.access.cli.main._answer_hint`

```python
def _answer_hint(pending: PendingQuestion, *, run_id: RunId, agent: str, session_id: SessionId, where: str, tenant: TenantId | None, with_call: bool=False) -> list[str]
```

Comment répondre à un appel `ask` en attente : les lignes à montrer sur stderr.

Une question : la ligne d'avant. Plusieurs : la commande exacte, un
`--answer` par question dans leur ordre ; l'essai t-2000 (02/11) avait
tapé un seul `--answer`, refusé sans savoir comment corriger.
`with_call` : plusieurs appels attendent dans le run, `loom answer`
exige alors `--call` (sans lui, le moteur refuse : « indiquer call_id »).

## Fonction `loom_ia.access.cli.main._only_data`

```python
def _only_data(result: RunResult) -> int
```

`data` seul sur stdout ; sans lui, stdout reste vide et stderr dit pourquoi.

Un script qui lit la sortie n'a ainsi que la donnée à analyser : un run en
échec ou sans contrat ne lui donne pas un `null` qu'il prendrait pour une
réponse.

## Fonction `loom_ia.access.cli.main._verdicts`

```python
def _verdicts(result: RunResult) -> list[str]
```

Une ligne par verdict ; pour un rôle, le rang de l'appel jugé parmi ceux de son run.

## Fonction `loom_ia.access.cli.main._console`

```python
def _console(config: LoomConfig) -> None
```

Les logs d'une commande qui rend compte du run : ce qu'elle dit elle-même en est écarté.

À l'essai de la fonction L2 du 22/10, un 401 se lisait trois fois (log ERROR, ligne « Erreur »,
sortie standard) ; à l'essai de la fonction J4, le refus d'un client deux fois. Un log marqué
`TOLD` revient à l'appelant — ici, la commande, qui le dit une fois.

## Fonction `loom_ia.access.cli.main._reading`

```python
def _reading(config: LoomConfig) -> None
```

Les logs d'une commande qui ne lance rien : sans les avertissements de montage.

Ils concernent ce qui tourne (file servie par un courtier, collecteurs) ;
à l'essai de la fonction H7 du 21/10, `sessions show` les répétait à chaque appel, à celui de la fonction K4
aussi. `validate`, `run` et le service les disent toujours.

## Fonction `loom_ia.access.cli.main._say`

```python
def _say(text: str='', *, file: TextIO | None=None) -> None
```

Une ligne pour un humain : ses caractères de contrôle se montrent, sans agir.

Les rendus (`inspect`, `eval`, `replay`), le déroulé et le bilan citent
ce qu'ont écrit un modèle, un outil, un serveur ou un journal reçu : ils ne
doivent piloter ni le terminal, ni plus tard celui qui lira leur copie.

## Fonction `loom_ia.access.cli.main._for_stdout`

```python
def _for_stdout(answer: str) -> str
```

La réponse d'un run pour la sortie standard : montrée vers un terminal, intacte ailleurs.

Un tube ou un fichier reçoit le texte du modèle tel quel ; `--json` le
donne toujours, échappé par JSON.

## Fonction `loom_ia.access.cli.main._delay`

```python
def _delay(seconds: float) -> str
```

Un délai à attendre, arrondi par excès : `45 s`, `12 min`, `7 h 12 min`.
