# Analyse d'impact — outils fournis (`tools_builtin`) loom_v2

15 octobre 2026 · Denis Lamard

## En bref

loom_v2 ne fournit aujourd'hui aucun outil à ses agents : chacun doit écrire les siens. Le but est d'en livrer avec la librairie, dans `src/loom_ia/tools_builtin/`. Un agent ne s'en sert que s'il les active ; par défaut, aucun n'est actif. Le premier lot ne contient que `fetch_page`, repris de `agent-01/tools/web_search.py`.

**Verdict : rien ne bloque.** Le changement tient dans un nouveau paquet et une branche ajoutée à `runtime/wiring._tool` (+19 lignes). On ne touche ni au schéma de config, ni aux événements du journal, ni au moteur. Un agent qui n'active aucun outil fourni monte exactement comme avant. Le prototype, monté sur un clone, en donne la preuve :

- **Tests :** 2 774 verts, soit les 2 768 existants plus 6 nouveaux.
- **Qualité :** pyright strict à 0, ruff propre, les 5 contrats d'import tenus.
- **Rejeu :** un run qui appelle `fetch_page` se rejoue à l'identique.

| Point | Constat | Preuve |
| --- | --- | --- |
| Aucun outil actif par défaut | Un agent n'expose que ce que liste son `tools:` ; rien dans `loom.yaml` | Démontré (test « par défaut ») |
| Activation | `- python: loom.fetch_page` ; le modèle voit l'outil sous le nom `fetch_page` | Démontré |
| Réglages par agent | `timeout`, `offload_over`, `approval`… s'appliquent à l'outil fourni comme aux autres | Démontré |
| Chargement à la demande | `fetch_page` et `httpx2` ne sont importés que si un agent référence l'outil | Démontré |
| `fetch_page` sous les règles de loom_v2 | pyright strict : 0 erreur ; ruff : 2 corrections automatiques (groupes d'imports, fin de fichier) | Démontré |
| Garde-fou réseau traversé par loom | `http://127.0.0.1/admin` donne un résultat en erreur au modèle (« Adresse refusée »), et le run finit `completed` | Démontré |
| Rejeu, banc d'essai | Le rejeu sert le résultat depuis le journal sans relire la page ; `Bench` double `fetch_page` par son nom | Démontré |

**Deux choses sont à régler avant de fusionner :**

1. **Les tests de `fetch_page` (point T6).** Sans eux, la couverture tombe à 92,72 % et la CI refuse, puisque son seuil est à 94 %.
2. **Le choix du paquet qui apporte `httpx2` (D1).** Il n'est aujourd'hui tiré que par les extras `anthropic` et `openai`.

## Périmètre et état de référence

L'analyse porte sur le commit `79bca0b` (« Start v2.0.7 »), le même sur le poste et sur `origin/main`. La version est 2.0.7, non publiée.

Rien n'a été écrit dans `/home/denis/dev/loom_v2`, à part ce document. Les essais ont tourné sur un clone, en Python 3.14.6, avec `uv sync --locked --all-extras --group types`. Le `fetch_page` étudié est le fichier `/home/denis/temp/agent-01/tools/web_search.py`.

**Lu :**
- dans `src/loom_ia` : `config/references.py`, `runtime/wiring.py` (`build_agent`, `_tool`, `_policy`, `_check_names`), `runtime/sources.py`, `agents/spec.py` (`PythonTool`, `ToolRef`), `core/model/tooling.py`, `core/model/policy.py` (`RESERVED_PREFIX`), `engine/executor.py` (déport), `engine/offload.py` (`artifact_read`), `policies/builtin.py`, `tenancy/registry.py`, `testing/bench.py`, `access/api.py` (`register`, `doubles_problem`), `access/cli/main.py` (`validate`, `replay`) ;
- `pyproject.toml` (extras, contrats d'import), `.github/workflows/ci.yml`, `tests/unit/test_dependances.py`, `tests/unit/test_wiring.py`, `tests/unit/test_cli.py` ;
- `docs/fonctions.md` §50, `docs/conception.md` (décisions de schéma), `docs/howto/01-demarrer.md` (« Les extras », chapitre 2).

| Contrôle sur `79bca0b`, avant toute modification | Résultat |
| --- | --- |
| `pytest` (unitaires, régression, firecracker ; intégration sans services) | 2 768 passés, 171 sautés |
| Couverture en branches, sans services | 94,30 % (la CI mesure 94,67 % avec services ; seuil à 94) |
| `pyright` strict | 0 erreur |
| `ruff check` / `ruff format --check` | propres |
| `lint-imports` | 5 contrats tenus |

La CI qualité est **verte** sur ce commit. Elle ne l'était pas au moment de l'analyse du state de session, quand on était sur `4426b0d`.

## Ce qui est décidé

| Décision | Choix |
| --- | --- |
| Emplacement | `src/loom_ia/tools_builtin/`, dans le paquet, donc publié sur PyPI |
| Activation | Par agent, dans `agents/<agent>.yaml` : `- python: loom.<outil>`. Le préfixe `loom.` est réservé, comme pour les politiques fournies (`loom.require_tool`) |
| Par défaut | Aucun outil fourni n'est actif ; `loom.yaml` ne change pas |
| Premier lot | `fetch_page` seul |

## Analyse fonctionnelle

### Ce que voit l'utilisateur de loom

```yaml
# agents/agent.yaml
tools:
- python: loom.fetch_page        # outil fourni : le modèle le voit sous le nom fetch_page
  timeout: 30                    # les réglages habituels restent possibles
- python: chercher_devis         # ses propres outils, inchangés
```

Il n'y a rien à mettre dans `imports:`. Une référence `loom.` inconnue est refusée au montage, donc dès `loom validate` :

```
Configuration : Outil fourni 'loom.fetch_pag' inconnu (fournis : loom.fetch_page)
```

### Ce que fait `fetch_page`, repris tel quel

- **Adresses acceptées :** `http` et `https` seulement, sur les ports 80 et 443, sans identifiant dans l'URL.
- **Adresses publiques seulement :** la vérification se fait au moment de la connexion. Le nom est résolu une seule fois, puis la connexion part vers l'adresse vérifiée. Ça bloque le changement d'adresse entre la vérification et la connexion, l'écart d'encodage IDNA, et les IPv4 cachées dans une IPv6.
- **Redirections :** cinq au plus, chacune revérifiée.
- **Taille et type :** la lecture s'arrête à 5 Mo, et seuls les contenus texte sont acceptés (HTML, texte, JSON, XML).
- **Rendu du HTML :** texte avec titre, intertitres, listes, tableaux et blocs de code, suivi de 50 liens au plus, ceux du contenu avant ceux du pourtour de la page.
- **Déclarations :** `side_effects` vaut `none`, `timeout` 45 s, `offload_over` 20 000 caractères.
- **Erreurs :** un échec lève `ToolError`. Le modèle reçoit un résultat `is_error` et le run continue.

### Ce qui marche sans rien changer

| Mécanisme | Comportement avec `loom.fetch_page` | Preuve |
| --- | --- | --- |
| Réglages par agent (`PythonTool`) | `timeout`, `side_effects`, `approval`, `idempotent`, `offload_over`, `output` remplacent ce que l'outil déclare | Démontré (`timeout`, `offload_over`) |
| Approbation | `approval: always` est soumis aux mêmes règles que pour un autre outil : il faut un journal durable (#28) | Démontré : refus avec un journal `memory` |
| Multi-clients | `tools_deny` et les approbations imposées par client désignent l'outil par le nom que voit le modèle, donc `fetch_page` | Lu dans `tenancy/registry.py` |
| Reprise après plantage | `side_effects: none` rend `safe_to_retry` vrai : un appel interrompu est relancé, ce qui est correct pour une lecture | Lu dans `tooling.py` l. 167, `executor.py` l. 647 |
| Rejeu | Le résultat est servi depuis le journal ; la page n'est pas relue | Démontré : « 1 servi(s) au rejeu », « Identique » |
| Banc d'essai, variantes | `Bench(tools={"fetch_page": faux})` et `--double fetch_page=…` visent l'outil par son nom | Démontré (`Bench`) |
| `tool_results` d'un rôle, `from:` du state | Désignent l'outil par son nom | Lu dans `_check_names`, `_check_state` |
| Rechargement à chaud (J6) | `tools_builtin` est un module installé, pas un module voisin : `_forget_others` n'y touche jamais | Lu dans `config/references.py` |

**Points d'accès.** Les quatre accès passent par `Loom`, qui monte ses agents avec `build_agent`, puis `_tool` : la branche `loom.` les couvre tous, sans rien changer dans `access/`. Essai sur le prototype, avec le modèle simulé et `http://127.0.0.1/admin` : à chaque accès, le journal contient l'appel à `fetch_page`, puis le refus « Adresse refusée : 127.0.0.1 désigne une adresse non publique », et le run se termine en `completed`.

| Accès | Ce qui a été lancé | Résultat |
| --- | --- | --- |
| CLI | `loom validate`, `loom run agent …`, `loom replay <run>` | Agent monté, run `completed`, rejeu « Identique » |
| Python | `Loom(load_config(...)).run("agent", …)` | `completed`, rejeu « Identique » |
| HTTP REST | `loom serve`, puis `POST /v1/agents/agent/runs` avec une clé | `completed`, rejeu « Identique » ; sans clé : 401 |
| MCP stdio | `loom mcp` + un client MCP : liste des outils, puis appel de `agent` | Outils `agent, run_status, run_report, cancel` ; `completed`, rejeu « Identique » |
| MCP HTTP | `server.mcp.http: true`, client MCP sur `/mcp` avec une clé | Mêmes outils ; `completed`, rejeu « Identique » |

Non couverts par ces essais :
- **Un vrai modèle (Claude).** Le nom `fetch_page` respecte `TOOL_NAME_PATTERN`, mais n'a pas été soumis à l'API d'Anthropic.
- **La lecture d'une vraie page par chacun des accès.** Elle n'a été faite qu'en appelant directement l'outil, sur pypi.org, et elle a marché.

### Limites à documenter, sans les traiter dans ce lot

- **L1 — Le proxy sortant est ignoré.** `fetch_page` passe son propre transport, et httpx2 ne lit alors ni `HTTP_PROXY` ni `HTTPS_PROXY` (`allow_env_proxies = trust_env and transport is None`). Là où la sortie doit passer par un proxy, l'outil ne joint rien. Un proxy rendrait d'ailleurs inutile le contrôle des adresses, puisque ce serait lui qui résoudrait le nom.
- **L2 — Ce qui n'est pas lu.** Ni JavaScript, ni PDF, ni image, ni page derrière une connexion à un compte.
- **L3 — Pas de paramètres propres.** La forme `python:` n'accepte que les réglages existants. Il n'y a ni domaines autorisés, ni taille maximale réglable par agent, ni secret. Ça suffit pour `fetch_page` ; un futur outil qui aura besoin d'une clé (`search`) demandera une décision (D4).
- **L4 — Contenu non fiable.** Le texte d'une page peut contenir des instructions adressées au modèle. La documentation doit recommander deux choses : ne pas activer `fetch_page` sur un agent qui a des outils `irreversible` sans `approval`, et s'appuyer sur les approbations imposées par client.
- **L5 — Ce que garde le journal.** Le texte lu va dans `tool.completed`. Au-delà de 20 000 caractères, il part dans le stockage d'artefacts, et `artifact_read` apparaît dans le run (comportement existant). Sans stockage d'artefacts, il est seulement tronqué. La rétention et le chiffrement s'appliquent. Une page peut contenir des données personnelles.

## Analyse technique

### Modifications, mesurées sur le prototype

| Fichier | Changement |
| --- | --- |
| `src/loom_ia/tools_builtin/__init__.py` | Nouveau (≈ 25 lignes) : table `BUILTIN_TOOLS` (`loom.fetch_page` → `loom_ia.tools_builtin.fetch_page:fetch_page`) et `builtin_tool()`, qui importe le module au premier usage |
| `src/loom_ia/tools_builtin/fetch_page.py` | Copie de `web_search.py`, plus l'en-tête SPDX, les 2 corrections ruff et une couture de test (T6) |
| `src/loom_ia/runtime/wiring.py` | `_tool` (l. 1565) : branche sur `RESERVED_PREFIX` avant `resolve()`, plus `_builtin_tool`, qui traduit les erreurs en `ConfigError`. C'est le même schéma que `_policy` (l. 880-889). +19 lignes |
| `pyproject.toml` | `loom_ia.tools_builtin` ajouté aux modules interdits des contrats « Noyau indépendant » et « Moteur indépendant » ; l'extra de D1 |
| `src/loom_ia/agents/spec.py` | Docstring de `PythonTool` : mentionner `loom.…`, qui apparaît dans la description de `loom schema` |
| `src/loom_ia/access/cli/main.py` | Selon D3 : une ligne pour les outils fournis dans `loom validate` |

Le code du prototype, pour le paquet :

```python
BUILTIN_TOOLS: Final[Mapping[str, str]] = {
    "loom.fetch_page": "loom_ia.tools_builtin.fetch_page:fetch_page",
}


def builtin_tool(reference: str) -> Tool:
    """L'outil fourni ``reference`` ; ``KeyError`` s'il n'existe pas, ``ImportError`` si
    une dépendance manque."""
    module_name, _, attribute = BUILTIN_TOOLS[reference].partition(":")
    found: object = getattr(importlib.import_module(module_name), attribute)
    if not isinstance(found, Tool):
        raise TypeError(f"{reference} : {BUILTIN_TOOLS[reference]} n'est pas un outil")
    return found
```

Et pour le montage :

```python
def _tool(declared: PythonTool, registry: Registry, base_dir: Path | None) -> Tool:
    if declared.python.startswith(RESERVED_PREFIX):
        found: object = _builtin_tool(declared.python)
    else:
        found = resolve(declared.python, registry, base_dir=base_dir)
    ...


def _builtin_tool(reference: str) -> Tool:
    try:
        return builtin_tool(reference)
    except KeyError:
        raise ConfigError(
            f"Outil fourni {reference!r} inconnu (fournis : {', '.join(BUILTIN_TOOLS)})"
        ) from None
    except ImportError as exc:
        raise ConfigError(
            f"Outil fourni {reference!r} : dépendance {exc.name!r} absente (installer l'extra)"
        ) from exc
```

### Organisation du dossier `tools_builtin`

```
src/loom_ia/tools_builtin/
├── __init__.py      # le catalogue : BUILTIN_TOOLS et builtin_tool(), sans dépendance lourde
└── fetch_page.py    # un module par outil : le @tool et tout ce dont il a besoin
```

- **Un module par outil, au nom de l'outil.** Le module contient la fonction décorée `@tool` et toute sa mécanique privée (fonctions et classes en `_…`). Pour `fetch_page.py`, ça fait l'outil, ses garde-fous réseau, le décodage et l'extraction du HTML. Ses réglages par défaut sont dans le décorateur (`@tool(timeout=45, offload_over=20_000)`), et l'agent peut les surcharger.
- **Un catalogue explicite dans `__init__.py`, pas de découverte automatique.** `BUILTIN_TOOLS` associe chaque référence à son emplacement `module:attr`, et `builtin_tool()` n'importe le module qu'à l'activation. Parcourir le dossier pour y trouver les `@tool` importerait au démarrage tous les modules, donc toutes leurs dépendances. La table, elle, donne la liste des outils fournis sans rien importer : elle sert au message « inconnu (fournis : …) » et pourrait servir à `loom validate` (D3).
- **Règle de nommage : la référence vaut `loom.` + le nom que voit le modèle.** Avec `loom.fetch_page`, le modèle voit `fetch_page`. Un test parcourt `BUILTIN_TOOLS` et vérifie pour chaque entrée que le module s'importe, que l'objet est un outil, que le nom vu par le modèle est la référence sans le préfixe, et que l'outil a une description.
- **Les dépendances restent dans leur module.** Chaque outil importe ce qu'il lui faut en haut de son fichier. Une dépendance absente ne gêne que l'agent qui l'active, avec un message qui nomme l'extra à installer (D1).
- **Ajouter un outil :** créer son module, ajouter une ligne à `BUILTIN_TOOLS`, écrire ses tests, puis, le cas échéant, déclarer sa dépendance dans l'extra et le documenter.
- **Plus tard :** quand deux outils partageront du code, sortir la partie commune dans un module privé du dossier ; ce sera le cas d'un `search` qui réutiliserait le contrôle des adresses de `fetch_page`. On pourra aussi grouper les outils par famille dans des sous-dossiers. Ce n'est pas utile tant qu'il n'y a qu'un outil, et les noms seront à choisir à ce moment-là.

### Choix techniques et leurs raisons

- **T1 — Le préfixe se teste dans `_tool`, pas dans `resolve()`.** `config/references.resolve()` sert aussi aux politiques, aux conditions des juges et à `--double`. Son message d'erreur est cité dans les guides (`01-demarrer.md` l. 1104 et 2149, `04-qualite-et-expert.md` l. 4282). Le laisser tel quel évite toute régression de message, et c'est déjà ce que fait `_policy`.
- **T2 — Aucune ambiguïté de nom.** Un nom d'outil ne peut pas contenir de point (`TOOL_NAME_PATTERN`), donc aucun `@tool` ne s'enregistre sous `loom.…`. En revanche, `Loom.register("loom.x", obj)` accepte n'importe quel nom (`Registry.add` ne contrôle rien) ; comme le préfixe est testé avant `resolve()`, cet objet n'est jamais atteint (D5).
- **T3 — Collision avec un outil maison.** Un agent qui liste `loom.fetch_page` et son propre `fetch_page` est refusé par le contrôle existant : `Agent 'demo' : plusieurs outils, rôles ou sous-agents s'appellent fetch_page`. Démontré, sans changement de code.
- **T4 — Le type reste `python`, pas `builtin`.** `ToolKind` a déjà une valeur `builtin`, celle d'`artifact_read` (`engine/offload.py` l. 158), et l'exécuteur ne déporte jamais un outil de ce type (`executor.py` l. 1111). Avec ce type, une page de 5 Mo irait entière dans le contexte et dans le journal. La contrepartie : le journal ne distingue pas un outil fourni d'un outil maison du même nom.
- **T5 — Chargement à la demande.** Après `import loom_ia.runtime, loom_ia.access.api`, le paquet `tools_builtin` (la table seule) est chargé, mais ni `fetch_page` ni `httpx2` ne le sont. Ils ne le sont qu'à la résolution d'une référence. Démontré.
- **T6 — `fetch_page` doit pouvoir se tester sans réseau.** `resolve()` est déjà séparé pour être remplacé. Il manque une couture pour le transport interne : `_PublicOnlyTransport` crée son `AsyncHTTPTransport` lui-même. Il faut le rendre injectable (`inner: httpx2.AsyncBaseTransport | None = None`) pour y brancher `httpx2.MockTransport`, qui existe. Le code de production ne change pas.
- **T7 — Couches.** `tools_builtin` importe `loom_ia.tools`, `loom_ia.core.ports` et `httpx2` ; `runtime` l'importe. Avec les deux ajouts dans les contrats, les 5 contrats sont tenus.
- **T8 — Dépendance.** `httpx2` n'est déclaré que dans les extras `anthropic` et `openai` (et le groupe `dev`). Sans lui, le montage refuse proprement : `Outil fourni 'loom.fetch_page' : dépendance 'httpx2' absente`. Démontré en masquant le module. Mais le guide promet que loom « nomme l'extra à installer », d'où D1.

### Tests à écrire

**Montage** (les 6 du prototype, tous verts) :
- un agent sans référence `loom.` a la même liste d'outils qu'avant ;
- l'activation donne `fetch_page` avec `timeout` 45 et `offload_over` 20 000 ;
- les réglages de l'agent l'emportent ;
- une référence `loom.` inconnue est refusée ;
- la collision avec un outil maison est refusée ;
- un run vers `127.0.0.1` rend un résultat en erreur et finit `completed`.

À ajouter : la cohérence du catalogue (voir « Organisation du dossier `tools_builtin` »), l'extra absent (D1), la ligne de `loom validate` (D3), le refus de `register("loom.…")` (D5).

**`fetch_page`** : indispensables pour la CI. Dans le prototype, avec les seuls tests de montage, le module est couvert à 23,13 % (401 instructions, 278 non couvertes), et le total tombe de 94,30 % à **92,72 %**, sous le seuil de 94. Il faut viser au moins 90 % sur le module. Les cas à couvrir, avec la couture T6 et `resolve` remplacé :

- **Adresse :** schéma, port, identifiants, nom absent, port non numérique.
- **Résolution :** `127.0.0.1`, `10.0.0.1`, `::1`, `::ffff:127.0.0.1`, `64:ff9b::7f00:1`, `::127.0.0.1`, nom mêlant adresses publiques et internes, DNS trop lent, nom inconnu.
- **Connexion :** adresse suivante essayée après `ConnectError`, `sni_hostname` posé, `request.url` rendu au client.
- **Redirections :** relative, sans `Location`, plus de cinq, vers une adresse interne.
- **Réponses :** 4xx et 5xx, type non texte, `text/event-stream`, troncature à 5 Mo.
- **Décodage :** les trois BOM, jeu de caractères de l'en-tête, `meta charset`, `encoding` XML, latin-1 lu en cp1252, UTF-8 invalide basculé en cp1252, étiquette inconnue.
- **HTML :** titre, intertitres, listes, tableau de données contre tableau de mise en page, `<pre>`, `script` et `style`, `aria-hidden` court ou long, icônes Material, éléments jamais fermés, `<base href>`, ordre des liens, `MAX_LINKS`, libellé tronqué, page sans texte.

## Plan de non-régression

| Contrôle | Attendu | Prototype |
| --- | --- | --- |
| Suite complète (`pytest`, sans services) | Aucun test existant modifié ni cassé | 2 774 passés (2 768 + 6), 171 sautés, comme la référence |
| Corpus de régression j1 à j5 et j7 (`tests/regression`) | Journaux rejoués à l'identique | Verts, compris dans la suite |
| Agents sans `loom.` | Même liste d'outils, mêmes requêtes, donc mêmes `request_hash` | Test « par défaut » ; aucun champ de requête ni d'événement modifié |
| Journal | Aucun nouveau type d'événement, aucun champ ajouté : les lecteurs 2.0.x lisent tout | Rien de modifié dans `core/events` |
| Messages d'erreur existants | `resolve()` inchangé | Inchangé (T1) |
| `loom validate` | La ligne `Outils     :` reste celle du registre (`test_cli.py` l. 44) | Inchangée |
| `pyright` strict / `ruff` / `lint-imports` | 0 erreur / propre / 5 contrats tenus | 0 / propre (hors fichier d'essai du prototype) / 5 tenus |
| Couverture (seuil 94) | Au moins la référence | 92,72 % sans les tests de `fetch_page`, à rétablir (T6) |

## Décisions à prendre

| # | Question | Options | Recommandation |
| --- | --- | --- | --- |
| D1 | Quel paquet apporte `httpx2` pour les outils fournis ? | (a) un nouvel extra, dont le nom est à choisir, ajouté aussi à `all` et à la table « Les extras » du guide ; (b) une dépendance du noyau ; (c) rien de plus, en comptant sur `anthropic` ou `openai` | (a) : le noyau ne gagne aucune dépendance, et le message d'erreur peut nommer l'extra comme le promet le guide |
| D2 | Quelle version ? | Dans la 2.0.7 non publiée, ou une 2.1.0 puisque c'est une fonction nouvelle | À toi |
| D3 | `loom validate` affiche-t-il les outils fournis ? | Aujourd'hui, un agent qui n'a que `loom.fetch_page` affiche `Outils : aucun`, puis `1 outil(s) Python` | Oui : une ligne `Outils fournis : loom.fetch_page`, seulement s'il y en a, ce qui ne change pas les sorties actuelles |
| D4 | Plus tard, un outil fourni qui a besoin de paramètres ou d'un secret (`search`) | Ajouter `params` aux références `loom.` ; ou le livrer comme source d'outils (point d'entrée, comme `forge`) | Hors de ce lot ; à trancher avec `search` |
| D5 | `Loom.register()` et `Bench(register=…)` avec un nom en `loom.` | Refuser ; ou tolérer, puisqu'il n'est jamais atteint | Refuser, avec un message clair |
| D6 | Migration d'agent-01 | Remplacer `imports: [tools.web_search]` et `- python: fetch_page` par `- python: loom.fetch_page`, puis retirer `tools/web_search.py` | Une fois la version publiée sur PyPI |

## Plan de mise en œuvre

Un lot par étape, que tu relis et commites :

1. **Socle :** le paquet `tools_builtin` (table vide d'abord, ou avec `fetch_page`), la branche dans `_tool`, les contrats d'import et les tests de montage.
2. **`fetch_page` :** la copie, l'en-tête SPDX, les corrections ruff, la couture de test (T6) et les tests unitaires, jusqu'à rétablir la couverture.
3. **Dépendance (D1)**, avec le test de l'extra absent et le message qui le nomme.
4. **`loom validate` (D3)** et **`register` (D5)**.
5. **Documentation :**
   - `fonctions.md` §50 et `conception.md` (décision de schéma 1) : troisième forme de référence, `loom.…` ;
   - le guide : un exemple d'outil fourni, au chapitre 2 de `01-demarrer.md` ou dans `02-maitriser.md`, la table « Les extras » si D1 (a), et les limites L1 à L5 ;
   - le `CHANGELOG`, plus une entrée de jalon ou de backlog.
6. **Migration d'agent-01 (D6).**

## Annexe : expériences reproduites

Toutes ont tourné sur un clone de `79bca0b`, dans un environnement isolé ; rien n'a été écrit dans le dépôt du poste.

| Expérience | Commande, en résumé | Résultat |
| --- | --- | --- |
| Référence | `pytest --cov=loom_ia` ; `pyright` ; `ruff` ; `lint-imports` | 2 768 passés, 171 sautés, 94,30 % ; 0 erreur ; propre ; 5 tenus |
| `fetch_page` sous les règles de loom_v2 | Copie dans `src/loom_ia/tools_builtin/`, `ruff check`, `ruff format --check`, `pyright` | ruff : I001 et fin de fichier, corrigés par `--fix` et `format` ; pyright strict : 0 erreur |
| Prototype complet | Paquet, branche dans `_tool`, contrats, 6 tests ; suite complète avec couverture | 2 774 passés, 171 sautés ; 92,72 % (module `fetch_page` : 23,13 %) |
| Chargement à la demande | `python -I` : importer `loom_ia.runtime` et `loom_ia.access.api`, puis inspecter `sys.modules` | `fetch_page` et `httpx2` absents, puis présents après `builtin_tool("loom.fetch_page")` |
| Extra absent | `sys.modules["httpx2"] = None`, puis `build_agent` | `ConfigError : Outil fourni 'loom.fetch_page' : dépendance 'httpx2' absente` |
| `loom validate` | Agent avec `- python: loom.fetch_page`, puis avec une faute de frappe | « 1 agent(s) monté(s) sans erreur » ; puis `Outil fourni 'loom.fetch_pag' inconnu` |
| Run et rejeu | `loom run` (modèle simulé qui appelle `http://127.0.0.1/admin`, journal JSONL), puis `loom replay <run>` | `completed`, résultat « Adresse refusée… » ; rejeu « Identique », « 1 servi(s) au rejeu » |
| Banc d'essai | `Bench(…, tools={"fetch_page": faux})` | `completed`, la doublure a reçu l'URL |
| Proxy | Lecture de `httpx2.AsyncClient.__init__` | `allow_env_proxies = trust_env and transport is None` |
