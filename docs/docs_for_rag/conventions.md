# Conventions de documentation du code de loom_v2

Ce document fixe comment documenter les sources de `src/loom_ia`. Le but est d'alimenter un RAG qui répond précisément à toute question sur loom_v2 : usage, configuration, comportement interne, raisons des choix. Les lecteurs sont Denis et son agent personnel bâti sur loom_v2.

Les docstrings sont rédigées en **français**, au **format Google**. La traduction en anglais viendra plus tard, en une passe à part.

## 1. Principes

1. **Chaque docstring se lit seule.** Le RAG découpe la doc en morceaux, et un morceau arrive sans son voisin. On nomme donc le sujet (« `ModelChain.run` appelle… ») au lieu d'écrire « celle-ci » ou « comme ci-dessus ». On ne renvoie pas à « la classe précédente ».
2. **On dit ce que la signature ne dit pas.** Le type est déjà dans l'annotation. La docstring donne le sens : unités, valeurs admises, ce que veut dire `None`, valeur par défaut **résolue** (`10` secondes, pas `DEFAULT_STATEMENT_TIMEOUT`), bornes.
3. **On écrit les effets.** Événements écrits au journal, et dans quel ordre ; fichiers, réseau, base ; état modifié ; ce qui est levé, et quand. Dans loom, c'est là que se trouve l'essentiel du comportement.
4. **Réécrire, c'est enrichir.** On ne perd aucune information présente aujourd'hui, ni dans les docstrings ni dans les commentaires qui documentent un champ.
5. **On ne change jamais le code.** Seules les docstrings et les commentaires changent. Un `Field(description=…)`, un message d'erreur ou une chaîne du code ne se touchent pas : ce sont du code, et souvent du texte que voit le modèle (section 3).

## 2. Format

### 2.1 Forme générale

```python
def f(...):
    """Résumé en une phrase, terminé par un point.

    Corps : le rôle, le fonctionnement, les cas particuliers, en prose.

    Args:
        nom: Sens du paramètre. Défaut : valeur résolue.

    Returns:
        Ce qui est renvoyé, et son sens.

    Raises:
        TypeErreur: Quand, et ce que dit le message.
    """
```

- **Résumé** : une seule ligne, une phrase complète terminée par un point, suivie d'une ligne vide si un corps suit.
- **Fermeture** : les `"""` de fin sont seuls sur leur ligne dès que la docstring fait plus d'une ligne.
- **Titres de section** : ils restent en anglais, parce que les outils (griffe, ruff) les reconnaissent à leur nom. Leur contenu est en français.

### 2.2 Sections Google utilisées

| Section | Où | Contenu |
|---|---|---|
| `Args:` | fonction, méthode, et classe pour les paramètres de `__init__` | Un paramètre par entrée, **tous** les paramètres (ruff D417 le vérifie). Sens, unités, valeurs admises, défaut résolu. |
| `Returns:` | fonction qui renvoie autre chose que `None` | Ce que représente la valeur, pas son type seul. |
| `Yields:` | générateur, générateur async | Ce qui sort, dans quel ordre, et ce qui sort en dernier. |
| `Raises:` | dès qu'une exception sort de la fonction | L'exception, la condition, ce que dit le message. |
| `Attributes:` | classe (dataclass, Pydantic, classe ordinaire), module pour ses constantes publiques | Un champ par entrée : sens, défaut résolu, bornes. |
| `Examples:` | quand un exemple vaut mieux qu'une phrase | YAML de config, appel Python, ligne de commande. |
| `Note:` | précision importante qui ne rentre pas ailleurs | |
| `Warning:` | piège, comportement surprenant | |

### 2.3 Sections propres à loom

Griffe garde comme texte libre une section dont il ne connaît pas le titre, et le RAG la retrouve par son titre.

| Section | Contenu |
|---|---|
| `Règles:` | Les contraintes de validation d'un modèle de config, une par tiret, telles que les validateurs les appliquent. C'est souvent la réponse exacte à « pourquoi ma config est refusée ? ». |
| `Effets:` | Les événements écrits au journal (`model.fell_back`, `tool.completed`…), dans l'ordre, et les effets hors de loom (fichiers, réseau, base). |

### 2.4 Renvois aux référentiels

Les codes restent, car ils pointent vers la conception, mais ils sont **qualifiés** pour ne pas être ambigus. `D4` est en effet à la fois une fonction et une décision du state de session.

| Écrire | Référentiel |
|---|---|
| `(fonction B4)` | `docs/fonctions.md`, liste des fonctions A1…P3 |
| `(point 10)` ou `(#10)` | `docs/fonctions.md`, « Points à trancher », numérotés 1 à 52 |
| `(conception §15)` | `docs/conception.md`, sections numérotées |
| `(jalon J4)`, `(phase 6.2c)` | `docs/jalons.md` |
| `(backlog #015)` | `docs/backlog.md` |
| `(décision D4 du state de session)` | Décisions numérotées d'un chantier : on nomme le chantier |

Le glossaire des codes (`docs/docs_for_rag/glossaire.md`) donne le sens de chacun.

## 3. Docstrings qui ne sont pas que de la doc

Certaines docstrings sont lues par le code. Les modifier change ce que voit un modèle, un éditeur ou un client de l'API. Le script de vérification (`docs/docs_for_rag/verif_docs.py`) les photographie avant et après chaque lot.

| Où | Qui la lit | Règle |
|---|---|---|
| Fonction décorée par `@tool` (`loom.fetch_page`, `loom.current_datetime`) | `inspect.getdoc` : c'est la **description de l'outil envoyée au modèle** à chaque appel | **Ne pas toucher.** La doc pour le développeur va dans la docstring du module. |
| Modèles Pydantic des arguments d'un outil fourni, dont le schéma part au modèle sans être allégé : `loom.sql`, `loom.excel`, `loom.local_files`, `loom.memory` | Pydantic recopie la docstring en `description` du schéma, que le modèle reçoit à chaque appel | **Ne pas toucher.** La doc va dans la docstring du module `arguments`. Les schémas de `loom.word` et `loom.pdf` retirent ces descriptions (`_light`) : leurs modèles se documentent normalement. |
| Tout autre modèle Pydantic : config, agent, événements, requêtes et réponses de l'API | Le JSON Schema de `loom.yaml` et d'`agent.yaml` (éditeur), le schéma des événements (contrat de l'interface, fonction K2), l'OpenAPI, les schémas de sortie du serveur MCP | **Permis.** Le changement est montré dans le rapport « Schémas JSON (à relire) ». |
| Route FastAPI (`@router.get`…) | Description de l'opération dans l'OpenAPI | **Permis.** Montré dans le rapport « Descriptions OpenAPI ». |

Le rapport « Vu par le modèle » doit rester **inchangé** : c'est le contrôle bloquant.

## 4. Modèles par cas

Les exemples viennent du dépôt. Pour chacun, on montre ce qui manque aujourd'hui et la forme attendue.

### 4.1 Module

Le module est le meilleur endroit pour les concepts. On y décrit son rôle dans l'architecture, ses notions, l'ordre des choses et les raisons des choix. Beaucoup de modules de loom le font déjà bien, par exemple `engine/fallback.py` ou `core/events/payloads.py`. Il reste deux choses à faire : qualifier les codes, et ajouter un paragraphe « Contenu » quand le module expose plusieurs classes.

```python
"""Chaîne de secours d'un appel de modèle (fonction B4, points 7 et 10).

Un emplacement (``main``, un rôle, un juge) déclare son modèle et ses
secours, dans l'ordre : ``model: M3_MAIN, fallbacks: [SONNET]``.
[… texte existant conservé …]

Contenu : ``ModelChain`` (la chaîne et son appel), ``ModelLink`` (un modèle
et son client), ``Answered`` (la réponse et le modèle qui l'a donnée),
``sendable`` et ``own_reasoning`` (préparation de la requête).
"""
```

### 4.2 Dataclass : les champs commentés passent en `Attributes:`

Aujourd'hui, le sens des champs est dans des commentaires `#`, qu'aucun extracteur ne lit.

Avant :

```python
@dataclass(frozen=True, slots=True)
class Answered:
    """Réponse obtenue, et le modèle de la chaîne qui l'a donnée."""

    response: ModelResponse
    spec: ModelSpec
    # Requête envoyée à ce modèle, avant la résolution des fichiers.
    request: ModelRequest
    # Tentatives sur ce modèle, la dernière comprise.
    attempts: int
```

Après :

```python
@dataclass(frozen=True, slots=True)
class Answered:
    """Réponse obtenue par ``ModelChain.run``, et le modèle de la chaîne qui l'a donnée.

    Attributes:
        response: Réponse du modèle.
        spec: Définition du modèle qui a répondu ; ce n'est pas forcément le
            premier de la chaîne, s'il y a eu bascule.
        request: Requête envoyée à ce modèle, avant la résolution des fichiers.
        attempts: Tentatives sur ce modèle, la dernière comprise (1 sans
            nouvelle tentative).
    """
```

Le commentaire d'un champ qui est passé dans `Attributes:` est retiré, pour ne pas avoir deux textes qui divergent. Un commentaire qui explique l'implémentation, et pas le sens du champ, reste en place.

### 4.3 Méthode

```python
def position(self, current: str | None) -> int:
    """Rang, dans la chaîne, du modèle courant de l'emplacement.

    Args:
        current: Identifiant du modèle sur lequel l'emplacement a basculé
            pendant ce run, ou ``None`` s'il n'a pas basculé.

    Returns:
        Le rang de ``current`` dans ``links``. 0 si ``current`` est ``None``,
        ou si ce modèle a été retiré de la config depuis.
    """
```

### 4.4 Générateur async

```python
async def run(self, request: ModelRequest, *, current: str | None = None) -> AsyncGenerator[ChainEvent | Answered]:
    """Appelle le modèle courant de l'emplacement, puis ses secours selon l'erreur.

    Chaque modèle reçoit ses nouvelles tentatives (``ModelCall``) avant que la
    chaîne passe au suivant. Un modèle dont le disjoncteur est ouvert est sauté
    sans être appelé.

    Args:
        request: Requête construite pour le modèle ``current`` (celui que
            renvoie ``link(current)``).
        current: Modèle sur lequel l'emplacement a déjà basculé dans ce run
            (adhérence), ou ``None`` pour partir du début de la chaîne.

    Yields:
        Les événements de l'appel, dans l'ordre : ``ModelRetried`` et
        ``ModelExchanged`` des tentatives, ``CircuitOpened`` quand un échec
        ouvre un disjoncteur, ``ModelFellBack`` avant chaque bascule. En
        dernier, un ``Answered`` unique.

    Raises:
        ModelError: L'erreur du dernier modèle appelé, quand elle ne permet
            pas de bascule (``auth``, ``invalid_request``, ``content_filtered``)
            ou qu'il n'y a plus de secours. Avec le genre ``unavailable``, quand
            la fin de la chaîne est écartée par son disjoncteur.

    Effets:
        Envoie ``StreamReset`` à ``on_chunk`` quand une bascule survient après
        que des morceaux sont déjà partis. Note le succès dans les
        disjoncteurs.
    """
```

### 4.5 Modèle Pydantic de paramètres (config YAML)

C'est le cas qui répond aux questions « quels sont les paramètres de… ». Tous les champs sont décrits, avec leur défaut **résolu** et leurs bornes. Les validateurs sont recopiés en `Règles:`, et un exemple YAML est fourni.

```python
class DatabaseSpec(BaseModel):
    """Une base que ``loom.sql`` peut interroger, déclarée sous ``databases``.

    Le modèle de l'agent désigne la base par ``name``. Seules les tables
    listées dans ``tables`` lui sont accessibles, avec les droits accordés.

    Attributes:
        name: Nom de la base pour le modèle : c'est la valeur de l'argument
            ``database`` d'un appel.
        postgres: Connexion Postgres ; exclusif de ``sqlite``. Le DSN n'est
            jamais dans la config : ``dsn_env`` nomme la variable qui le porte.
        sqlite: Fichier SQLite, qui doit exister ; exclusif de ``postgres``.
        tables: Table ou vue → droits accordés, en liste (``[select, insert]``)
            ou en objet (``{rights: […], approval: {droit: never | always}}``).
            Un droit absent est refusé. Approbation par défaut : ``never`` pour
            ``select`` et ``insert``, ``always`` pour ``update`` et ``delete``.
        describe: Sens métier d'une table (``devis``) ou d'une colonne
            (``devis.statut``), repris dans la description de l'outil.
            500 caractères au plus par entrée. Défaut : vide.
        functions: Fonctions SQL inconnues de sqlglot, permises quand même
            (``age``, ``jsonb_build_object``…). Les autres fonctions inconnues
            sont refusées. Défaut : aucune.
        statement_timeout: Délai d'une instruction côté base, en secondes.
            Supérieur à 0, 300 au plus. Défaut : 10.
        max_rows: Lignes rendues au modèle par une lecture ; au-delà, le
            résultat est marqué ``truncated``. De 1 à 1 000. Défaut : 200.
        max_affected_rows: Lignes qu'un ``execute`` peut modifier au total ;
            au-delà, rien n'est enregistré. De 1 à 100 000. Défaut : 100.
        allow_full_table: Permet ``UPDATE`` et ``DELETE`` sans ``WHERE``.
            Défaut : ``False``.

    Règles:
        - Exactement un de ``postgres`` et ``sqlite``.
        - ``tables`` n'est pas vide, et chaque table a au moins un droit.
        - ``update`` et ``delete`` exigent ``select`` : la base lit les
          colonnes du ``WHERE``.
        - SQLite : deux tables qui ne diffèrent que par la casse sont refusées.
        - Chaque clé de ``describe`` commence par une table déclarée.
        - Une fonction n'apparaît qu'une fois dans ``functions``, casse ignorée.

    Examples:
        Dans ``agent.yaml`` ::

            - python: loom.sql
              databases:
              - name: crm
                postgres: { dsn_env: CRM_DATABASE_URL }
                tables:
                  clients: [ select ]
                  devis: { rights: [ select, update ], approval: { update: never } }
    """
```

### 4.6 Modèle Pydantic vu par le modèle d'un agent

Le cas est `SqlArguments` et `Statement` (`loom.sql`), et de même pour `loom.excel`, `loom.local_files` et `loom.memory`. **On ne touche pas** à la docstring de classe. Les arguments sont documentés dans la docstring du module `arguments.py` : chaque argument, l'action pour laquelle il sert, et les limites (`MAX_SQL_CHARS`, `MAX_PARAMS` avec leur valeur).

### 4.7 Outil `@tool`

Le cas est `fetch_page` et `current_datetime`. **On ne touche pas** à la docstring de la fonction. La docstring du module dit ce que le modèle ne voit pas : limites (délai, taille, `offload_over`), effets, en-têtes envoyés, et comment l'activer (`- python: loom.fetch_page`).

### 4.8 Port (`Protocol`)

On décrit le contrat, les invariants, qui l'appelle, et les implémentations fournies.

```python
@runtime_checkable
class Tool(Protocol):
    """Contrat d'un outil appelable par un agent (fonction D1).

    Le moteur lit ``spec`` pour exposer l'outil au modèle, valide les
    arguments par ``spec.input_schema``, puis appelle ``invoke``.
    ``isinstance`` vérifie seulement la présence de ``spec`` et ``invoke`` :
    c'est ce qui sert à reconnaître un outil dans la config.

    Implémentations fournies : ``FunctionTool`` (``@tool``, ``tools.python``),
    les enveloppes de ``tools.configured`` et ``tools.idempotent``, les outils
    MCP (``adapters.mcp.source``), les outils forgés
    (``adapters.firecracker.forge``) et les outils fournis ``loom.*``
    (``tools_builtin``).
    """
```

### 4.9 Exception

On dit quand elle est levée, qui la rattrape, et ce que devient son message.

```python
class ToolError(Exception):
    """Erreur d'un outil dont le message est destiné au modèle, pour qu'il corrige son appel.

    L'exécuteur la rattrape : l'appel se termine en erreur
    (``tool.completed`` avec ``is_error``), et ``message`` est rendu au modèle
    comme résultat. Le run continue.

    Args:
        message: Texte rendu au modèle, tel quel.

    Attributes:
        message: Le même texte.
    """
```

### 4.10 Méthode redéfinie

Une méthode qui redéfinit celle d'une classe parente a sa propre docstring, d'une ligne si c'est assez. Elle dit ce qui est particulier à cette classe, pas ce que fait la méthode en général. C'est ce qui permet de répondre à « quel statut a un `judge.evaluated` ? ».

```python
@property
def event_status(self) -> EventStatus:
    """``ok`` si le juge a validé la sortie, ``warning`` sinon."""
```

### 4.11 Commande CLI

La docstring de `cmd_<commande>` dit ce que fait la commande, ses codes de sortie et ses effets. Les options sont déjà décrites dans `build_parser` (aide argparse) : on ne les recopie pas. Le générateur de doc les extraira du parseur.

### 4.12 Route REST

La docstring devient la description de l'opération dans l'OpenAPI. Elle dit ce que fait la route, la portée d'accès demandée, les codes d'erreur et leur cause, et l'événement écrit au journal s'il y en a un.

### 4.13 Constantes

Une constante publique qui fixe un comportement (limite, défaut, clé) se documente par une **docstring d'attribut** : une chaîne placée juste après l'affectation, que griffe rattache à la constante. C'est la forme déjà employée dans `core/__init__.py`. On y donne le sens de la constante, son unité, et où elle sert. Une constante privée garde son commentaire.

```python
DEFAULT_MAX_ROWS: Final = 200
"""Lignes rendues au modèle par une lecture de ``loom.sql``, sauf ``max_rows`` de la base."""
```

## 5. Ce qui est exigé, et ce qui ne l'est pas

À documenter :

- chaque module ;
- chaque classe, publique ou privée ;
- chaque fonction et méthode publique, propriétés comprises ;
- chaque fonction ou méthode privée de 15 lignes ou plus ;
- `__init__` et `__call__` de 15 lignes ou plus, les autres méthodes spéciales non.

`Args:` liste tous les paramètres dès qu'il y en a, sauf `self` et `cls`. Les paramètres de `__init__` vont dans la docstring de la classe.

## 6. Déroulement d'un lot

1. Rédiger les docstrings du lot, en suivant ce document.
2. Lancer le script de vérification depuis la racine du dépôt :

   ```bash
   uv run python docs/docs_for_rag/verif_docs.py --lot loom_ia/core/events loom_ia/core/model
   ```

   Il compare l'arbre de travail à `main` (`--base` pour une autre référence). Ce qu'il faut obtenir :
   - « Code hors docstrings » et « Vu par le modèle » : **inchangés** ;
   - « Schémas JSON » et « Descriptions OpenAPI » : relire les écarts ;
   - « Couverture du lot » : 100 %.
3. Lancer `uv run ruff check <chemins>` : rien à signaler. Les règles `D` sont actives pour tout le dépôt depuis le lot 14 (`select` de `[tool.ruff.lint]`, sans D105 ni D107 ; les essais sont dispensés des règles de présence, D100 à D104). La convention Google est réglée dans `pyproject.toml` (`[tool.ruff.lint.pydocstyle]`).
4. Lancer `ruff format --check`, `pyright` et `pytest` : au vert.
5. Denis relit le diff et commite.
