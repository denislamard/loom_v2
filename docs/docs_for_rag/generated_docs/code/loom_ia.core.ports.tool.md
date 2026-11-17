# loom_ia.core.ports.tool

Module `loom_ia.core.ports.tool` de loom_v2 (fichier `src/loom_ia/core/ports/tool.py`).

Ports des outils : l'outil, le contexte de son appel, et la source d'outils (points 15, 18 et 19, fonction D1).

Un outil se décrit par un `ToolSpec` et s'exécute avec `invoke`. Outils
Python (`@tool`), MCP, rôles délégués, sous-agents, outils forgés et outils
fournis `loom.*` implémentent ce même port.

Une source d'outils (`ToolSource`, un serveur MCP par exemple) fournit ses
outils au début de chaque run : ils peuvent changer d'un run à l'autre, et la
source peut être indisponible. La liste obtenue reste fixe jusqu'à la fin du
run, pour que les requêtes au modèle restent stables.

Un paquet installé fournit une source par un point d'entrée du groupe
`loom_ia.tools` (phase 6.4b, fonction D12) : il désigne une fabrique
(`ToolSourceFactory`), que loom appelle avec ce que la config déclare pour
elle.

Un outil signale une erreur à corriger par le modèle en levant `ToolError` :
l'exécuteur la rattrape et rend son message au modèle comme résultat en
erreur. Un outil dont les effets dépendent de l'appel l'évalue appel par appel
(`AssessedTool`, `CallAssessment`).

## Constantes de `loom_ia.core.ports.tool`

- `ReuseNote = Callable[[str], None]` : Signalement d'un effet déjà mémorisé, avec la clé sous laquelle il l'est (`ToolContext.on_reuse`).

## Fonction `loom_ia.core.ports.tool.idempotency_key`

```python
def idempotency_key(run_id: RunId, call_id: str) -> str
```

Clé technique d'un appel : identique à chaque reprise du même appel (point 18).

**Arguments :**

- `run_id` : Le run de l'appel.
- `call_id` : L'identifiant de l'appel.

**Renvoie :**

L'empreinte SHA-256, en hexadécimal, de `<run_id>:<call_id>`.

## Classe `loom_ia.core.ports.tool.SeenFiles`

```python
class SeenFiles(Protocol)
```

Les fichiers que l'agent a vus passer : pièces jointes, fichiers rendus par ses outils.

C'est tout ce qu'un outil peut en relire : un fichier se désigne par le
nom sous lequel le modèle l'a vu, jamais par une URI qu'on devinerait.

### Méthode `loom_ia.core.ports.tool.SeenFiles.find`

```python
def find(self, name: str) -> ArtifactRefBlock | None
```

Le dernier fichier vu sous ce nom (nom donné, ou nom dans le stockage), ou `None`.

### Méthode `loom_ia.core.ports.tool.SeenFiles.names`

```python
def names(self) -> tuple[str, ...]
```

Les noms des fichiers vus, sans doublon, pour dire ce qui existe.

### Méthode `loom_ia.core.ports.tool.SeenFiles.read`

```python
async def read(self, file: ArtifactRefBlock, *, max_bytes: int) -> bytes
```

Lit les octets d'un fichier vu.

**Arguments :**

- `file` : La référence rendue par `find`.
- `max_bytes` : Taille au-delà de laquelle la lecture est refusée.

**Renvoie :**

Les octets, en clair.

**Lève :**

- `ValueError` : Le fichier dépasse `max_bytes`.
- `LookupError` : Le fichier manque dans le stockage.

## Classe `loom_ia.core.ports.tool.RunResults`

```python
class RunResults(Protocol)
```

Les résultats d'outils du run en cours, en lecture (state v2, `loom.state`).

Un résultat se désigne par la référence que le modèle voit en tête de chacun
(`result:3`) : seuls ceux du run, obtenus avant le tour en cours.

### Méthode `loom_ia.core.ports.tool.RunResults.read`

```python
async def read(self, ref: str) -> JsonValue
```

Lit un résultat d'outil du run.

**Arguments :**

- `ref` : La référence que le modèle voit en tête du résultat (`result:3`).

**Renvoie :**

Les données du résultat (`data`), sinon son texte.

**Lève :**

- `ToolError` : La référence est inconnue, ou désigne un résultat du tour en cours ; le message dit pourquoi.

## Classe `loom_ia.core.ports.tool.ToolContext`

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class ToolContext
```

Ce qu'un outil sait de l'appel en cours ; le moteur le passe à `invoke`.

Un outil Python le reçoit s'il déclare un paramètre de ce type. Hors du
moteur (outil appelé directement), les champs facultatifs sont absents.

**Attributs :**

- `tenant_id` : Client du run.
- `session_id` : Session du run.
- `run_id` : Run de l'appel.
- `call_id` : Identifiant de l'appel, donné par le modèle.
- `agent` : Agent du run.
- `caller` : Contexte de l'appelant : client, utilisateur, passerelle, métadonnées (fonction A8).
- `idempotency` : Magasin d'idempotence du run (points 18 et 49) : c'est par ici qu'un outil décoré retrouve l'effet qu'il a déjà produit. Absent hors moteur.
- `replay_unknown` : Vrai si un humain a approuvé cet appel après un effet d'état inconnu (points 17 et 18) : l'outil décoré reprend la réservation périmée et refait l'effet. Vaut pour ce seul appel.
- `on_reuse` : Appelé, avec sa clé, par un outil qui rend un effet déjà mémorisé au lieu d'agir (point 49). Le moteur en fait un `idempotency.reused` : sans lui, le journal ne dirait pas pourquoi l'appel n'a rien fait, le magasin partagé n'y écrivant rien. Absent hors moteur.
- `state` : State de la session tel qu'au début du lot de l'appel, en lecture : les clés que l'agent déclare, valeurs par défaut comprises. Ce qu'un voisin du même lot écrit n'y est pas encore. Vide hors moteur, et dans un sous-run.
- `files` : Fichiers de la conversation, en lecture (`SeenFiles`). Absent hors moteur, et sans stockage d'artefacts.
- `results` : Résultats d'outils du run, en lecture (`RunResults`). Absent hors moteur.
- `keeps_state` : Vrai si le run tient le state de la session : un run racine ordinaire d'un agent qui en déclare un (décision D3 du state de session). Faux dans un sous-run et hors moteur.
- `cancelled` : Événement levé quand le moteur rend la main sans attendre l'outil : délai dépassé, run annulé (décision B-2 du délai et des threads). Un outil qui travaille dans un thread le regarde entre deux étapes, et s'arrête : Python ne sait pas arrêter un thread de l'extérieur. Absent hors moteur.

### Méthode `loom_ia.core.ports.tool.ToolContext.idempotency_key`

```python
@property
def idempotency_key(self) -> str
```

Clé technique de cet appel (`idempotency_key(run_id, call_id)`), stable à chaque reprise.

## Classe `loom_ia.core.ports.tool.ToolError`

```python
class ToolError(Exception)
```

Erreur d'un outil dont le message est destiné au modèle, pour qu'il corrige son appel.

L'exécuteur la rattrape : l'appel se termine en erreur
(`tool.completed` avec `is_error`), et `message` est rendu au modèle
comme résultat. Le run continue. Toute autre exception d'un outil est
aussi rattrapée et journalisée en avertissement ; le modèle lit alors
« Erreur de l'outil <nom> : <type>: <message> ».

**Arguments :**

- `message` : Texte rendu au modèle, tel quel.

**Attributs :**

- `message` : Le même texte.

## Classe `loom_ia.core.ports.tool.UnknownEffect`

```python
class UnknownEffect(ToolError)
```

Réservation périmée : l'effet a peut-être eu lieu, personne ne le sait (point 18).

C'est une `ToolError`, donc son message vaut pour le modèle. Mais
l'exécuteur la reconnaît et applique ce que l'outil a déclaré en
`on_unknown` : `error` (défaut) rend l'erreur, `pause` suspend le
run pour qu'un humain vérifie avant de refaire.

## Classe `loom_ia.core.ports.tool.Tool`

```python
@runtime_checkable
class Tool(Protocol)
```

Contrat d'un outil appelable par un agent (fonction D1).

Le moteur lit `spec` pour exposer l'outil au modèle, valide les
arguments par `spec.input_schema`, puis appelle `invoke`.
`isinstance` vérifie seulement la présence de `spec` et `invoke` :
c'est ce qui sert à reconnaître un outil dans la config.

Implémentations fournies : `FunctionTool` (`@tool`, `tools.python`),
les enveloppes de `tools.configured` et `tools.idempotent`, les outils
MCP (`adapters.mcp.source`), les outils forgés
(`adapters.firecracker.forge`) et les outils fournis `loom.*`
(`tools_builtin`).

### Méthode `loom_ia.core.ports.tool.Tool.spec`

```python
@property
def spec(self) -> ToolSpec
```

Définition de l'outil : nom, description, schéma des arguments, effets, approbation, délai.

### Méthode `loom_ia.core.ports.tool.Tool.invoke`

```python
async def invoke(self, arguments: dict[str, JsonValue], context: ToolContext) -> ToolOutput
```

Exécute un appel.

**Arguments :**

- `arguments` : Arguments de l'appel, déjà validés par le schéma (`execution.tools.validate_arguments`) et références `$ref` résolues.
- `context` : Ce que l'outil sait de l'appel.

**Renvoie :**

Le résultat, rendu au modèle : blocs de contenu, données
structurées, ou erreur.

**Lève :**

- `ToolError` : Erreur à corriger par le modèle ; son message lui est rendu.

## Classe `loom_ia.core.ports.tool.CallAssessment`

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class CallAssessment
```

Ce qu'un appel précis demande, décidé par son outil d'après ses arguments.

Un outil dont les effets dépendent de l'appel — lire ou supprimer, dans un
dossier ou dans un autre — le dit appel par appel, plutôt qu'une
déclaration fixe qui vaudrait pour le pire des cas. Pour cet appel, le
moteur s'en sert à la place de `spec.approval`, `spec.side_effects` et
`spec.idempotent` pour approuver et pour reprendre un appel interrompu.
Au rejeu et en évaluation, où l'appel est jugé avant que ses références et
les politiques ne fixent ses arguments, elle ne peut que durcir la
déclaration (`strictest_side_effects`).

La décision ne dépend que des arguments et de la configuration de
l'outil : elle est la même à chaque reprise, et au rejeu. Les effets sont
toujours à dire : ils décident si l'appel peut être refait.

**Attributs :**

- `side_effects` : Effets de cet appel : `none`, `reversible` ou `irreversible`.
- `approval` : `always` si cet appel attend un humain, `never` sinon. Défaut : `never`.
- `idempotent` : Vrai si refaire cet appel ne double pas son effet.
- `reason` : Motif montré à l'approbateur quand `approval` vaut `always`.

### Méthode `loom_ia.core.ports.tool.CallAssessment.safe_to_retry`

```python
@property
def safe_to_retry(self) -> bool
```

Vrai si refaire l'appel après une interruption est sans risque : sans effet, ou idempotent (point 18).

## Classe `loom_ia.core.ports.tool.AssessedTool`

```python
@runtime_checkable
class AssessedTool(Protocol)
```

Outil qui évalue chacun de ses appels ; c'est facultatif.

Un outil qui n'a pas `assess_call` garde sa déclaration fixe (`spec`)
pour tous ses appels, et rien ne change pour lui. Celui qui l'a déclare
quand même dans `spec` le pire de ses appels : c'est ce que voient ce qui
ne regarde pas l'appel lui-même — la garde du journal durable, les
politiques `before_tool`, le rejeu et les évaluations.

### Méthode `loom_ia.core.ports.tool.AssessedTool.assess_call`

```python
def assess_call(self, arguments: Mapping[str, JsonValue]) -> CallAssessment | None
```

Évalue un appel d'après ses arguments.

**Arguments :**

- `arguments` : Les arguments de l'appel.

**Renvoie :**

Ce que demande cet appel ; `None` : la déclaration fixe
(`spec`) vaut.

## Classe `loom_ia.core.ports.tool._Declared`

```python
class _Declared(Protocol)
```

Tout ce qui a une déclaration `spec` : un outil, ou une enveloppe d'outil.

### Méthode `loom_ia.core.ports.tool._Declared.spec`

```python
@property
def spec(self) -> ToolSpec
```

Définition de l'outil.

## Fonction `loom_ia.core.ports.tool.strictest_side_effects`

```python
def strictest_side_effects(tool: _Declared, arguments: Mapping[str, JsonValue]) -> SideEffects
```

Les effets d'un appel, l'évaluation ne pouvant que durcir la déclaration.

Pour qui juge un appel avant que ses références ne soient résolues et que
les politiques ne remplacent ses arguments (rejeu, évaluations) : les
arguments vus ne sont pas forcément ceux qui partiront.

**Arguments :**

- `tool` : L'outil.
- `arguments` : Les arguments vus.

**Renvoie :**

Le plus grave des effets déclarés (`spec.side_effects`) et évalués
(`assess_call`), de `none` à `irreversible`.

## Fonction `loom_ia.core.ports.tool.assessed`

```python
def assessed(tool: object, arguments: Mapping[str, JsonValue]) -> CallAssessment | None
```

L'évaluation d'un appel par son outil, ou `None` s'il n'en fait pas.

Une évaluation qui échoue vaut la plus prudente — approbation exigée, effet
irréversible : un défaut de l'outil ne doit pas laisser passer un appel
qu'il aurait retenu.

**Arguments :**

- `tool` : L'outil, évaluateur ou non.
- `arguments` : Les arguments de l'appel.

**Renvoie :**

L'évaluation ; en cas d'exception, une évaluation ``approval:
always`, `irreversible`, dont le motif nomme l'exception ; `None``
si l'outil n'a pas `assess_call`.

## Classe `loom_ia.core.ports.tool.SourceContext`

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class SourceContext
```

Le run pour lequel une source ouvre ses outils.

**Attributs :**

- `tenant_id` : Client du run.
- `session_id` : Session du run.
- `run_id` : Le run.
- `agent` : Agent du run.

## Classe `loom_ia.core.ports.tool.SourceUnavailable`

```python
class SourceUnavailable(Exception)
```

La source ne peut pas fournir ses outils (serveur injoignable, erreur de protocole…).

Le moteur en fait un `tool.source_unavailable` : le run continue sans
ses outils, ou échoue si la source est requise.

**Arguments :**

- `source` : Nom de la source.
- `message` : Pourquoi.
- `attempted` : Faux quand la source a refusé sans essayer (attente avant une nouvelle connexion) : ce refus ne compte pas pour le disjoncteur. Défaut : vrai.

**Attributs :**

- `source` : Nom de la source.
- `message` : Pourquoi ; le message de l'exception est « <source> : <message> ».
- `attempted` : La source a-t-elle essayé.

## Classe `loom_ia.core.ports.tool.ToolSource`

```python
@runtime_checkable
class ToolSource(Protocol)
```

Fournisseur d'outils découverts au début d'un run : serveur MCP, source d'un paquet (`tool_sources`), forge.

### Méthode `loom_ia.core.ports.tool.ToolSource.name`

```python
@property
def name(self) -> str
```

Nom de la source, repris dans le journal.

### Méthode `loom_ia.core.ports.tool.ToolSource.required`

```python
@property
def required(self) -> bool
```

Vrai si le run ne peut pas se passer de cette source.

### Méthode `loom_ia.core.ports.tool.ToolSource.open`

```python
def open(self, context: SourceContext) -> AbstractAsyncContextManager[Sequence[Tool]]
```

Ouvre les outils de la source pour un run, jusqu'à la sortie du contexte.

La liste reste fixe jusqu'à la fin du run, pour que les requêtes au
modèle restent stables.

**Arguments :**

- `context` : Le run.

**Renvoie :**

Un gestionnaire de contexte asynchrone qui rend les outils.

**Lève :**

- `SourceUnavailable` : La source ne répond pas.

## Classe `loom_ia.core.ports.tool.ToolSourceFactory`

```python
@runtime_checkable
class ToolSourceFactory(Protocol)
```

Ce qu'un point d'entrée `loom_ia.tools` désigne : de quoi faire une source (phase 6.4b, fonction D12).

loom l'appelle au montage d'un agent qui référence la source, une fois par
agent et par client, avec :

- `name` : le nom de la source dans la config (`tool_sources[].name`) ;
- `params` : ses `params`, tels que la config les donne — à la
  fabrique de les vérifier, et de lever `ValueError` en disant ce qui
  ne va pas ;
- `secrets` : la table des secrets du client pour qui l'agent est monté,
  en lecture seule. Sans clients déclarés, c'est l'environnement ; avec
  des clients, seulement les variables que la config nomme (`api_key_env`,
  `env_from`, `headers_env`) et celles que le client redirige — une
  variable que la config ne nomme pas s'y nomme par une redirection
  (`secrets: {CARNET_JETON: CARNET_JETON}`) ;
- `base_dir` : le dossier du fichier de config, auquel se rapportent
  les chemins relatifs de `params`.

La fabrique rend la source sans travailler : `loom validate` et le
montage d'essai de `serve --reload` l'appellent aussi. Le travail
(connexion, VM…) se fait à l'ouverture, au début de chaque run
(`ToolSource.open`) ; ce qui doit durer plus qu'un run se ferme dans un
`aclose` asynchrone, appelé quand l'agent est démonté, s'il existe.

Les outils rendus portent leur nom court : loom les préfixe
(`source__outil`), les choisit (`include`, `exclude`) et applique
les déclarations de la config. Le nom et `required` de la source rendue
ne comptent pas : c'est la config qui les dit.

### Méthode `loom_ia.core.ports.tool.ToolSourceFactory.__call__`

```python
def __call__(self, *, name: str, params: Mapping[str, JsonValue], secrets: Mapping[str, str], base_dir: Path) -> ToolSource
```

Fabrique la source, sans travailler.

**Arguments :**

- `name` : Nom de la source dans la config.
- `params` : Ses `params`, à vérifier ici.
- `secrets` : Table des secrets du client, en lecture seule.
- `base_dir` : Dossier du fichier de config.

**Renvoie :**

La source.

**Lève :**

- `ValueError` : Les `params` ne conviennent pas ; le message dit ce qui ne va pas.
