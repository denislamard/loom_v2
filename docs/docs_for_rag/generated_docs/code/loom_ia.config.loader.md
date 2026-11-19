# loom_ia.config.loader

Module `loom_ia.config.loader` de loom_v2 (fichier `src/loom_ia/config/loader.py`).

Lecture de l'amorce, de `loom.yaml` et des agents (fonction M1, point 35).

L'amorce (`loom-boot.yaml`) est lue d'abord : elle donne le profil, le
fichier d'environnement et l'endroit où lire la config (décisions A-1 et
A-2). `safe_load` (sans clé en double, `yaml_source`) puis validation
stricte par les modèles Pydantic.

Deux racines pour les chemins relatifs (décisions A-5 et A-6) :

- le dossier de la config pour les documents : `agents_dir`,
  `resources_dir`, `schema_file` des contrats de sortie ;
- `workpath`, lui-même relatif au dossier de l'amorce, pour le travail :
  journal, artefacts, idempotence, audit, `cwd` des serveurs MCP,
  `file_roots`, stockage des clients, modules Python voisins.

Tous deviennent absolus au chargement ; un schéma de sortie donné par
fichier (JSON ou YAML) est lu et vérifié à ce moment-là.
Un serveur MCP stdio sans `cwd` se lance depuis `workpath`. Un
`system_file` manquant, un modèle inconnu ou un agent en double arrêtent
le démarrage. Un `system_file` ou un `skill_file` doit rester dans
`resources_dir` : ailleurs, un dossier ouvert à un outil laisserait l'agent
réécrire son propre prompt.

## Constantes de `loom_ia.config.loader`

- `AGENT_SUFFIXES = ('.yaml', '.yml')` : Extensions des fichiers d'agents lus dans `agents_dir`.
- `_AGENT_NAMED = re.compile("Agent '([^']+)'")` : Début du message d'un contrôle qui porte sur un agent (`LoomConfig.check`).
- `BOOT_KEYS = ('profile', ENV_FILE_KEY)` : Clés de l'amorce refusées dans la config : elles sont lues avant elle (décision A-2).
- `WORKPATH_KEY = 'workpath'` : Clé du dossier de travail, obligatoire dans la config (décision A-4).
- `CONFIG_KEYS = frozenset({'models', 'agents', 'agents_dir', 'resources_dir', 'security', 'storage', 'mcp_servers', WORKPATH_KEY})` : Clés qui disent qu'un fichier est une config et non une amorce.
- `BOOT_EXAMPLE = f' version: 1\n config:\n source: file\n path: .\n file: {CONFIG_FILE}'` : L'amorce minimale, donnée dans les messages qui la réclament.
- `PROFILE_ENV = 'LOOM_PROFILE'` : Variable qui l'emporte sur le fichier : ce qu'un conteneur sait poser.
- `WHOLE = frozenset({'params', 'llm'})` : Blocs remplacés en entier par une surcharge de profil, jamais fusionnés : ils sont transmis tels quels au fournisseur, et une moitié de l'un n'a pas de sens (décision de conception, conception §17.9).
- `_COLON = 'mapping values are not allowed here'` : Le message de PyYAML devant un texte qui contient « : » sans guillemets.

## Fonction `loom_ia.config.loader.load_config`

```python
def load_config(path: str | Path, *, profile: str | None=None) -> LoomConfig
```

Config complète : l'amorce, la config qu'elle désigne, les surcharges du profil, puis les agents.

Le profil se choisit à trois endroits, du plus fort au plus faible :
l'argument (`--profile`), `LOOM_PROFILE`, puis `profile:` dans
l'amorce. `chosen_profile` rend le même arbitrage avec sa provenance,
pour qui veut l'afficher.

Étapes : lecture de l'amorce, puis de la config qu'elle désigne (YAML
sans clé en double), surcharges du profil, `workpath` exigé, chemins
rendus absolus, agents de `agents_dir` lus et préparés (prompts,
skills, schémas de sortie, modèles et styles de documents), puis
validation de l'ensemble et contrôle des variables des prompts.

**Arguments :**

- `path` : Chemin de l'amorce (`loom-boot.yaml`).
- `profile` : Profil donné par l'appelant (`--profile`).

**Renvoie :**

La configuration validée, `workpath`, `boot_file` et
`config_file` posés.

**Lève :**

- `ConfigError` : Amorce absente ou invalide, config absente, fichier illisible, YAML invalide, clé en double, `profile` ou `env_file` dans la config, `workpath` absent, champ inconnu ou invalide, fichier cité absent ou hors de `resources_dir`, agent en double, incohérence (modèle, serveur, agent non déclarés…). Le message nomme le fichier et le chemin dans le YAML.

## Fonction `loom_ia.config.loader.read_boot`

```python
def read_boot(path: Path) -> BootConfig
```

L'amorce (`loom-boot.yaml`), lue et validée.

Un projet sans amorce est refusé (décision A-3) : le message donne
l'amorce minimale à écrire. Un fichier de config donné à la place de
l'amorce (`--config loom.yaml`) est reconnu à ses clés, et le message
le dit.

**Arguments :**

- `path` : Chemin de l'amorce.

**Renvoie :**

L'amorce validée.

**Lève :**

- `ConfigError` : Amorce absente, illisible, invalide, ou fichier de config donné à sa place.

## Fonction `loom_ia.config.loader.chosen_profile`

```python
def chosen_profile(given: str | None, in_file: object=None) -> tuple[Profile | None, str]
```

Le profil actif et d'où il vient : option, environnement, amorce, ou rien.

Un profil qu'on ne voit pas est un profil qu'on oublie : la provenance est
rendue avec lui pour que `loom validate` et `loom serve` la disent.

**Arguments :**

- `given` : Profil donné par l'appelant.
- `in_file` : Valeur de `profile:` dans l'amorce.

**Renvoie :**

Le profil (`dev`, `prod` ou `None`) et sa provenance.

**Lève :**

- `ConfigError` : Profil inconnu.

## Fonction `loom_ia.config.loader._check_boot_keys`

```python
def _check_boot_keys(data: dict[str, Any], *, source: Path) -> None
```

Refuse `profile` et `env_file` dans la config : ils appartiennent à l'amorce (décision A-2).

Lus avant la config, ils n'y seraient jamais pris en compte : le fichier
d'environnement peut donner `LOOM_PROFILE`, et le profil choisit les
surcharges. Les surcharges de chaque profil sont vérifiées aussi, pas
seulement celles de l'actif : l'erreur ne doit pas attendre la prod.

## Fonction `loom_ia.config.loader._workpath`

```python
def _workpath(data: dict[str, Any], boot_dir: Path, *, source: Path) -> Path
```

Le dossier de travail, exigé et rendu absolu (décision A-4).

Lu après les surcharges du profil : un profil peut le changer
(`profiles.prod.workpath: /loom-data`). Un chemin relatif se rapporte
au dossier de l'amorce.

**Lève :**

- `ConfigError` : `workpath` absent, vide ou qui n'est pas un chemin.

## Fonction `loom_ia.config.loader._merged`

```python
def _merged(data: dict[str, Any], active: Profile | None, *, source: Path) -> dict[str, Any]
```

Applique les surcharges du profil actif au fichier racine (fonction M4).

Les objets fusionnent en profondeur, les listes sont **remplacées** : une
liste partiellement fusionnée ne veut rien dire — que ferait-on d'un
troisième modèle à demi surchargé ?

## Fonction `loom_ia.config.loader._deep_merge`

```python
def _deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]
```

Fusion profonde des objets ; toute autre valeur remplace, listes comprises.

## Fonction `loom_ia.config.loader._launched_from`

```python
def _launched_from(server: McpServerSpec, base_dir: Path) -> McpServerSpec
```

Un serveur stdio se lance depuis le dossier de la config, sauf `cwd` absolu.

## Fonction `loom_ia.config.loader._load_agents`

```python
def _load_agents(agents_dir: Path, resources_dir: Path, base_dir: Path) -> list[tuple[AgentSpec, Path]]
```

Les agents du dossier, chacun avec le fichier d'où il vient.

## Fonction `loom_ia.config.loader._prepared`

```python
def _prepared(spec: AgentSpec, resources_dir: Path, base_dir: Path, *, source: Path) -> AgentSpec
```

Prompts, skills et schémas de sortie d'un agent, lus et vérifiés.

## Fonction `loom_ia.config.loader._culprit`

```python
def _culprit(error: ValidationError, loaded: Sequence[tuple[AgentSpec, Path]]) -> Path | None
```

Le fichier d'agent que nomme l'erreur de la config entière, s'il est seul (fonction M5).

Un fichier d'agent est validé seul ; ce qu'il référence ailleurs (un modèle,
un serveur MCP, une source, un autre agent, des capacités) ne l'est qu'avec
la config entière, et l'erreur était attribuée à la racine de `loom.yaml`
— « (racine) — Agent 'agent' : modèle 'SONNET' non déclaré » pour une faute
dans `agents/agent.yaml` (essai de la fonction M5, 23/10). Les contrôles d'un agent
commencent tous par « Agent '<nom>' » : quand toutes les erreurs nomment
le même agent, et qu'il vient d'un fichier, c'est ce fichier. Sinon (une
erreur de la racine, deux agents en cause), `None` : `loom.yaml`.

## Fonction `loom_ia.config.loader._with_contracts`

```python
def _with_contracts(spec: AgentSpec, base_dir: Path, *, source: Path) -> AgentSpec
```

Schémas de sortie donnés par fichier, lus : agent, rôles, outils Python, MCP et de paquet.

## Fonction `loom_ia.config.loader._schema`

```python
def _schema(contract: OutputContract | None, base_dir: Path, *, source: Path) -> OutputContract | None
```

Contrat dont le schéma donné par fichier est lu et vérifié.

## Fonction `loom_ia.config.loader._with_prompts`

```python
def _with_prompts(spec: AgentSpec, resources_dir: Path, *, source: Path) -> AgentSpec
```

Rend absolus les chemins des prompts (`main` et rôles), et vérifie qu'ils sont lisibles.

## Fonction `loom_ia.config.loader._within_resources`

```python
def _within_resources(resources_dir: Path, written: Path, label: str, *, source: Path) -> Path
```

Un fichier lu par la config, qui doit rester dans `resources_dir`.

Prompt, skill, modèle de document, style, logo.

Le chemin est comparé une fois résolu (`..`, chemin absolu, lien
symbolique) : `resources_dir` est protégé de tout outil fourni
(`_protected_paths`), un dossier ouvert à `loom.local_files` ne l'est pas.

## Fonction `loom_ia.config.loader._hint`

```python
def _hint(resources_dir: Path, written: Path) -> str
```

Un chemin qui recommence par le dossier des ressources : il est déjà relatif à lui.

## Fonction `loom_ia.config.loader._with_prompt`

```python
def _with_prompt[R: BaseRole](role: R, resources_dir: Path, label: str, *, source: Path) -> R
```

Le `system_file` d'un rôle rendu absolu, contenu dans `resources_dir` et lisible.

**Lève :**

- `ConfigError` : Fichier hors de `resources_dir` ou absent.

## Fonction `loom_ia.config.loader._with_tool_skills`

```python
def _with_tool_skills(spec: AgentSpec, resources_dir: Path, *, source: Path) -> AgentSpec
```

Skills d'outils donnés par fichier, lus : outils Python, références MCP et de paquet.

## Fonction `loom_ia.config.loader._templates_read`

```python
def _templates_read(declared: PythonTool, resources_dir: Path, label: str, *, source: Path) -> PythonTool
```

Les modèles de document d'un outil fourni (`loom.word`, `loom.pdf`), lus.

Comme un `skill_file` : chaque fichier YAML, relatif à `resources_dir` et contenu
dans ce dossier, est lu une fois au chargement, et son texte rangé à la place du
chemin. Ses sections et leur mise en page sont vérifiées ici (`templates.parse`), et
le style qu'il nomme est lu avec lui : une faute se voit dès `loom validate`.

## Fonction `loom_ia.config.loader._template_style`

```python
def _template_style(given: object, where: str, path: Path, *, source: Path) -> StyleRef
```

Le style que nomme un modèle : un chemin, ou un style écrit en ligne.

## Fonction `loom_ia.config.loader._styles_read`

```python
def _styles_read(declared: PythonTool, resources_dir: Path, label: str, *, source: Path) -> PythonTool
```

La charte d'un outil `loom.word` ou `loom.pdf`, lue, et la cascade de chaque modèle vérifiée.

La charte, relative à `resources_dir` et contenue dans ce dossier, est lue une fois
au chargement avec ses logos et ses polices, et validée ici ; le style de chaque
modèle l'a été avec lui (`_templates_read`). La cascade est résolue comme à
l'écriture —
`default.yaml`, la charte, le style du modèle — et les styles que choisissent les
sections y sont cherchés : une faute (clé inconnue, couleur inconnue, base en boucle,
style de section inconnu, logo ou police absent) se voit dès `loom validate`.

## Fonction `loom_ia.config.loader._resolved`

```python
def _resolved(where: str, *files: StyleFile | None) -> Style
```

La cascade résolue, pour dire ses fautes au chargement (couleur, base).

## Fonction `loom_ia.config.loader._style_read`

```python
def _style_read(ref: StyleRef, resources_dir: Path, where: str, *, source: Path) -> StyleFile
```

Un fichier de style lu et validé, ses logos et ses polices lus ; ou le style écrit en ligne.

## Fonction `loom_ia.config.loader._logos_read`

```python
def _logos_read(spec: StyleFile, resources_dir: Path, where: str, *, source: Path) -> StyleFile
```

Les logos d'un style (`page.header.logo`, `page.footer.logo`), lus.

Leurs octets prennent la place de leur fichier.

## Fonction `loom_ia.config.loader._fonts_read`

```python
def _fonts_read(spec: StyleFile, resources_dir: Path, where: str, *, source: Path) -> StyleFile
```

Les polices d'un style, lues et vérifiées (TrueType, droits d'embarquement).

Leurs octets prennent la place de leur fichier.

## Fonction `loom_ia.config.loader._skill_read`

```python
def _skill_read[T: (PythonTool, ToolOverrides)](declared: T, resources_dir: Path, label: str, *, source: Path) -> T
```

Le skill lu depuis son fichier et rangé dans `skill` ; `skill_file` n'est plus gardé.

Lu une fois, au chargement, comme `schema_file` : une faute se voit dès
`loom validate`. Les mêmes règles que pour un skill en ligne s'appliquent
(`checked_skill`).

## Fonction `loom_ia.config.loader._absolute_sessions`

```python
def _absolute_sessions(config: LoomConfig, resources_dir: Path, *, source: Path) -> object
```

Prompt surchargé de la compaction, rapporté au dossier des ressources.

## Fonction `loom_ia.config.loader._absolute_tenant`

```python
def _absolute_tenant(tenant: TenantSpec, base_dir: Path) -> TenantSpec
```

Chemins du stockage propre à un client, rapportés au dossier de la config.

## Fonction `loom_ia.config.loader._check_variables`

```python
def _check_variables(config: LoomConfig, *, source: Path) -> None
```

Variables citées par les prompts définies pour chaque client (conception §6, fonction M5).

Un prompt appartient à la configuration, pas au client : la même phrase
sert tout le monde. Ce qui change d'un client à l'autre, ce sont les
valeurs — et un client à qui il en manque une écrirait un trou dans son
prompt sans que rien ne le dise. Le chargement l'attrape.

## Fonction `loom_ia.config.loader._absolute_storage`

```python
def _absolute_storage(storage: StorageConfig, base_dir: Path) -> StorageConfig
```

Chemins du journal, des artefacts et des clés rapportés au dossier de la config.

## Fonction `loom_ia.config.loader._absolute_security`

```python
def _absolute_security(config: LoomConfig, base_dir: Path) -> object
```

Fichier des refus d'accès, rapporté au dossier de la config.

## Fonction `loom_ia.config.loader._absolute_server`

```python
def _absolute_server(config: LoomConfig, base_dir: Path) -> object
```

Dossiers lisibles par le serveur MCP, rapportés au dossier de la config.

## Fonction `loom_ia.config.loader._read_yaml`

```python
def _read_yaml(path: Path) -> dict[str, Any]
```

Le contenu d'un fichier YAML, qui doit être un objet (vide : `{}`).

**Lève :**

- `ConfigError` : Fichier illisible, YAML invalide, clé en double, ou document qui n'est pas un objet.
