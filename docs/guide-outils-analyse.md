# Analyse d'impact — guide d'usage d'un outil dans la config (`skill:`) loom_v2

27 octobre 2026 · Denis Lamard

> **Mise à jour du 27/10 : décidé et réalisé.** La clé s'appelle **`skill` / `skill_file`** (D-1) ; les autres décisions suivent les recommandations (§ Décisions prises). La réalisation est décrite à la fin (§ Réalisation). Le corps de l'analyse garde les noms provisoires du prototype (`guide`, `guide_file`) : il trace le raisonnement, pas le code livré.

## En bref

**La demande.** Pouvoir attacher à un outil, dans la config, un texte qui apprend au modèle à bien s'en servir — l'équivalent d'un skill :

```yaml
- python: loom.excel
  skill: prompts/excel_skill.md      # proposition de départ
  folders:
  - name: workspace
    path: workspace
    rights: [ read, write ]
```

**La proposition.** Une clé commune à **tous** les outils, pas propre à `loom.excel` : `guide` (texte en ligne) ou `guide_file` (fichier du dossier des prompts), noms provisoires (D-1). Le texte est lu au chargement et **ajouté à la description** de l'outil, que le modèle voit à chaque appel. Elle vaut pour les outils Python et fournis (`- python:`), et pour les outils MCP et de paquet (bloc de surcharges `tools:`).

**Verdict : faisable, petit, sans risque pour le moteur — à cinq conditions.**

- **Prototype complet** sur un clone, au commit `ab42377` : **+132 / −6 lignes** dans 5 fichiers de `src/`, et 13 tests nouveaux. Le moteur, le journal, les adaptateurs de modèles et les outils fournis ne changent pas.
- **Tests :** 3 454 verts, soit les 3 441 de la référence et les 13 nouveaux ; aucun test existant modifié. pyright strict à 0, ruff propre, les 5 contrats d'import tenus.
- **Bout en bout par la CLI :** `loom validate` monte l'agent ; le guide arrive à la fin de la description de `excel` ; `loom replay` d'un run fait avant un changement de guide dit « ce qui diffère : les outils proposés ».

**Les cinq conditions**, toutes nées du prototype (§ Effets de bord) :

1. **Le guide doit rester dans le dossier des prompts.** Sinon, un guide placé dans un dossier ouvert à `loom.local_files` peut être réécrit par l'agent lui-même, et relu au chargement suivant : une injection de prompt persistante. Démontré, puis corrigé dans le prototype. `system_file` a **déjà** ce défaut aujourd'hui (démontré) : à corriger en même temps.
2. **`ToolSpec.overridden()` recopie aveuglément** chaque champ de `ToolOverrides` : un nouveau champ qui n'existe pas sur `ToolSpec` y est posé sans erreur, et **perdu** sans rien dire (démontré). Le prototype l'exclut et refuse un fichier non lu.
3. **Le guide coûte des tokens à chaque appel de modèle**, que l'outil serve ou non. Le cache de prompt (`cache: { tools: true }`) le rend bon marché après le premier appel, mais tout changement de guide **invalide le cache entier** de l'agent : outils, prompt système et conversation.
4. **Un run fait avant un changement de guide ne se rejoue plus à l'identique.** C'est voulu (la requête a changé) et bien diagnostiqué, mais à documenter.
5. **Un guide n'apprend que ce que l'outil sait faire.** Pour `loom.excel`, il améliore l'*usage* du vocabulaire (types, rôles, onglets), pas la *présentation* : l'outil fixe la police, les couleurs et la mise en page, et n'a aucun champ pour un titre fusionné ou un cadre. Le bon d'intervention du 27/10 ne sera pas « beau » avec un guide seul.

Deux finitions restent à faire sur le prototype : un `guide_file` sur un outil MCP d'un agent écrit dans `loom.yaml` n'est signalé qu'en plein run (effet de bord 9), et un `guide:` en ligne n'est pas nettoyé.

**Ce qui reste à décider** : 10 points, dont le nom de la clé, l'ajout à la description ou le chargement à la demande, et les variables `{{ }}` des clients (§ Décisions à prendre).

## Le besoin, et ce qu'un guide peut y faire

Le 27/10, l'agent de `temp/agent` (Haiku) a écrit `bon_intervention_S-2026-0001.xlsx` avec `loom.excel`. Le journal (`data/default/session-01.jsonl`, appel `toolu_015DSGPLQouhtudvj7CigjrM`) montre qu'il a forcé une **fiche** dans le seul moule de l'outil, le **tableau** :

- deux colonnes « Champ | Valeur », d'où un filtre et des volets gelés sans objet sur une fiche ;
- les titres de section en lignes `note` ;
- les dates en texte (`"2026-10-28 13:00"`), donc sans format date ;
- les champs à remplir sur place (travaux, pièces, signature) vides, sans le rôle `saisie`.

`loom.excel` décrit déjà son vocabulaire dans sa description : **1 846 caractères**, environ 460 tokens, mesurés sur le prototype avec un dossier. Le modèle l'avait sous les yeux. Un guide ajoute ce que la description ne dit pas : **quand** se servir de l'outil, et comment choisir entre ses formes. Par exemple :

```markdown
## Classeurs Excel
- `excel` écrit des tableaux. Une fiche (bon, compte rendu) n'est pas un tableau Champ | Valeur :
  dis-le à la personne au lieu de la forcer.
- Chaque colonne a son type : `date_heure` pour une date et une heure, `montant_eur` pour un montant.
- Une cellule à remplir à la main a le rôle `saisie`.
- Une liste par onglet ; un onglet « Hypothèses » pour ce que citent les formules.
```

Ce qu'un guide **ne peut pas** faire : donner à l'outil des moyens qu'il n'a pas. Pour une vraie fiche, il faut soit enrichir `loom.excel` (une forme `fiche`, ou un mode `template:`), soit donner au modèle l'exécution de code — loom a déjà la source `forge` (`adapters/firecracker/forge.py`, J6.4c), qui exécute dans une microVM le code que l'agent écrit. Un guide sur `forge` est alors l'équivalent exact d'un skill « classeurs openpyxl » ; reste à vérifier qu'openpyxl est dans l'image de la VM.

## Périmètre et état de référence

L'analyse porte sur `ab42377` (« approvol param - lot 4 »), le commit du poste et d'`origin/main`, en version 2.0.14. Sur le poste, seuls quatre fichiers d'exemples ne sont pas commités (`examples/agent_postgres` et `examples/agent_sql`, `compta.yaml` et `compta.md`) ; ils ne touchent pas le code.

**Rien n'a été écrit dans `/home/denis/dev/loom_v2`, à part ce document.** Les essais ont tourné sur un clone, dans l'espace de travail cloud de Claude, en Python 3.14.6, avec `uv sync --all-extras --group types`. Le prototype vit sur une branche `proto-guide` de ce clone.

| Contrôle sur la référence, avant toute modification | Résultat |
| --- | --- |
| `pytest`, tous les extras, sans `LOOM_TEST_POSTGRES`, `LOOM_TEST_RABBITMQ` ni `LOOM_TEST_REDIS` | 3 441 passés, 203 sautés (6 min 06 s) |
| `pyright` strict | 0 erreur |
| `ruff check` | propre |
| `ruff format --check`, `lint-imports` | non relancés sur la référence ; propres sur le prototype |

Parmi les sauts, les tests d'intégration qui demandent Postgres (42), RabbitMQ (9) ou Redis (4), faute de ces services dans cet espace : la CI les fait tourner, et le prototype ne touche aucun adaptateur de stockage ni de bus.

**Lu :**
- `agents/spec.py` : `PythonTool`, `_builtin_reference`, `McpTools`, `SourceTools`, `ToolRef`, `BaseRole`, `system_text` ;
- `core/model/tooling.py` : `ToolDefinition`, `ToolOverrides`, `ToolSpec.overridden`, `ToolSpec.definition` ;
- `tools/configured.py` (`ConfiguredTool`, `configure`) ;
- `runtime/wiring.py` : `build_agent`, `_tool`, `_builtin_tool`, `_builtin_closables`, `_assesses_approval`, `_returns_data`, `_check_names`, `prompt_text`, `_protected_paths` ;
- `runtime/sources.py` (`PackagedSource.select`) et `adapters/mcp/source.py` (surcharges des outils MCP) ;
- `config/loader.py` (`load_config`, `_load_agents`, `_with_prompts`, `_with_contracts`, `_check_variables`) et `config/models.py` (`TenantSpec`) ;
- `engine/executor.py` (`definitions`), `engine/model_call.py` (`estimate_tokens`, `_check_context`) ;
- `core/model/streaming.py` (`request_hash`, `request_parts`), `replay/book.py`, `replay/variant.py` ;
- `adapters/models/anthropic.py` (`to_anthropic_tools`, `place_cache_points`), `openai_chat.py`, `openai_responses.py` ;
- `access/cli/main.py` (`loom validate`), `config/schema.py`, `core/model/budget.py` ;
- `tools_builtin/excel/` et `tools_builtin/__init__.py` ;
- `docs/configuration.md` (bloc surcharges d'outil), `docs/fonctions.md` (D13, M7).

## Ce qui existe déjà

Trois choses rendent l'ajout naturel :

1. **Les surcharges d'outil.** `PythonTool` (`agents/spec.py`, l. 528) porte déjà les réglages que la config impose à un outil Python : `timeout`, `side_effects`, `approval`, `idempotent`, `offload_over`, `output`. `_tool` (`runtime/wiring.py`, l. 1820) les passe à `configure()` (`tools/configured.py`, l. 65), qui enveloppe l'outil dans un `ConfiguredTool` dont la `spec` est corrigée.
2. **La description réécrite, pour MCP et les paquets.** `ToolOverrides` (`core/model/tooling.py`, l. 126) a **déjà** une clé `description`, qui remplace celle de l'outil (`docs/configuration.md`, « Bloc surcharges d'outil »). `PythonTool` ne l'a pas : c'est une asymétrie existante (D-10).
3. **Un texte ajouté à la description.** `ToolSpec.definition()` (l. 183) ajoute déjà `TERMINAL_HINT` à la description d'un outil terminal. Le guide suit le même chemin.

## Analyse fonctionnelle

### Ce que voit l'utilisateur de loom

```yaml
# agents/agent.yaml
tools:
- python: loom.excel
  guide_file: excel.md                # relatif à prompts_dir, comme system_file
  folders:
  - name: workspace
    path: workspace
    rights: [ write ]
- python: loom.current_datetime
  guide: "Appelle-moi avant d'écrire une date relative."   # en ligne
- mcp: crm
  tools:
    chercher_client: { guide_file: crm_recherche.md }      # outil MCP, pour cet agent
```

```yaml
# loom.yaml — pour tous les agents qui prennent ce serveur
mcp_servers:
- name: crm
  transport: stdio
  command: python
  args: [ serveurs/crm.py ]
  tools:
    chercher_client: { guide_file: crm_recherche.md }
```

- **`guide` et `guide_file` s'excluent**, comme `system` et `system_file`.
- **Le chemin est relatif au dossier des prompts** (`prompts_dir`), comme `system_file`. Écrire `guide_file: prompts/excel.md`, comme dans la demande de départ, chercherait `prompts/prompts/excel.md` : le message d'erreur donne le chemin complet.
- **Contrôles au chargement**, donc dès `loom validate` :
  - `… .guide_file — guide introuvable : /…/prompts/autre.md` ;
  - `… .guide_file — guide illisible : …` (droits, encodage) ;
  - `… .guide_file — guide vide : …` ;
  - `… .guide_file — le guide doit être dans le dossier des prompts (…) : …` (§ Effets de bord, point 1).
- **Une surcharge d'agent l'emporte** sur celle du serveur ou de la source, comme pour les autres clés du bloc (`docs/configuration.md`). Le prototype **remplace** le guide du serveur par celui de l'agent ; les concaténer est l'autre choix (D-6).

### Ce que voit le modèle

La description envoyée au fournisseur se compose ainsi, dans cet ordre :

```text
<description de l'outil, ou celle de la surcharge description:>

<guide>

<TERMINAL_HINT, si l'outil est terminal>
```

Mesuré sur le prototype : avec un guide de 95 caractères (fichier, espaces retirés), la description de `excel` passe de 1 846 à 1 943 caractères (1 846 + 2 de séparation + 95) ; avec un guide en ligne de 45 caractères, celle de `current_datetime` passe de 211 à 258. `ToolSpec.description` reste celle de l'outil : seul `definition()` compose, si bien que ce qui lit la description pour un autre usage n'est pas touché.

### Ce que la clé ne fait pas

- **Pas de mise en forme**, ni pour `loom.excel` ni pour un autre outil (§ Le besoin).
- **Pas un Agent Skill au sens d'Anthropic** : pas de chargement progressif, pas de scripts ni de ressources. Le texte entier part à chaque appel (choix 1) ; le chargement à la demande est le choix 2, étudié plus bas.
- **Pas de guide par client** : un guide appartient à la configuration, comme un prompt (§6, `TenantSpec`, « Les prompts ne sont pas surchargeables »). Ce qui changerait d'un client à l'autre passerait par ses variables (D-5).
- **Pas de guide pour un rôle ou un sous-agent** : leur `description` se règle déjà dans la config.

## Analyse technique

### Modifications, mesurées sur le prototype

| Fichier | Lignes | Ce qui change |
| --- | --- | --- |
| `core/model/tooling.py` | +33 / −3 | `ToolOverrides.guide`, `guide_file`, validateur d'exclusion ; `ToolSpec.guide` ; `overridden()` exclut `guide_file` et refuse un fichier non lu ; `definition()` compose |
| `agents/spec.py` | +13 | `PythonTool.guide`, `guide_file`, validateur d'exclusion |
| `config/loader.py` | +77 / −3 | `_with_guides`, `_guided`, `_guide_read` : lecture, contrôles, chemin borné au dossier des prompts ; appliqué aux agents des fichiers, à `mcp_servers[].tools` et à `tool_sources[].tools` |
| `runtime/wiring.py` | +7 | `_tool` passe `guide` à `configure()`, et refuse un `guide_file` non lu |
| `tools/configured.py` | +2 | `configure(guide=…)` |
| `tests/unit/test_tool_guide.py` | +206 (nouveau) | 13 tests |

Ne changent pas : le moteur (`engine/`), le journal et ses événements, les adaptateurs de modèles, les outils fournis, `replay/`, et le code de `config/schema.py` — le schéma JSON généré (`loom schema`) gagne `guide` et `guide_file` de lui-même.

### Le chemin d'un guide

1. **Chargement** (`config/loader.py`). `_load_agents` lit chaque fichier d'agent, puis `_with_prompts` et, nouveau, `_with_guides`. `_guide_read` résout le chemin dans `prompts_dir`, vérifie qu'il y reste, lit le fichier, retire les espaces de tête et de fin, range le texte dans `guide` et efface `guide_file`. Même traitement pour les surcharges de `mcp_servers[]` et `tool_sources[]` de `loom.yaml`. C'est le schéma de `schema_file` des contrats de sortie, lu puis rangé dans `schema`.
2. **Montage** (`runtime/wiring.py`, `_tool`). Le guide passe à `configure()`, qui enveloppe l'outil dans un `ConfiguredTool` dont la `spec` porte `guide`. Pour MCP et les paquets, `ToolSpec.overridden()` le pose à l'ouverture de la source (`adapters/mcp/source.py`, l. 180 ; `runtime/sources.py`, l. 180).
3. **Requête** (`engine/executor.py`, `definitions`). `tool.spec.definition()` compose la description ; les adaptateurs l'envoient telle quelle (`anthropic.py`, l. 412 ; `openai_chat.py`, l. 312 ; `openai_responses.py`, l. 249).

### Choix techniques et leurs raisons

| Choix | Raison |
| --- | --- |
| Clé sur `PythonTool` et sur `ToolOverrides`, pas sur `loom.excel` | Tout outil en profite (Python, fourni, MCP, paquet, `forge`), avec un seul mécanisme. Un paramètre propre à un outil fourni ne servirait qu'à lui |
| `ToolSpec.guide` séparé de `description` | `description` reste celle de l'outil ; la surcharge `description:` et le guide se combinent sans se marcher dessus ; `TERMINAL_HINT` reste en dernier |
| Lu au chargement, pas à chaque montage | Erreur dès `loom validate` ; même schéma que `schema_file`. `system_file`, lui, est lu au chargement pour contrôle (`_with_prompt`, `_check_variables`), puis relu au montage (`system_text`) ; comme un agent n'est monté qu'une fois par instance et par client (`access/api.py`, `runtime/wiring.py`, l. 1545), la différence ne se voit qu'avec `serve --reload`, qui recharge la config de toute façon |
| Relatif à `prompts_dir`, et borné à lui | Un guide est un prompt ; le dossier des prompts est déjà protégé de tout outil fourni (`_protected_paths`, l. 1938) |
| Espaces de tête et de fin retirés d'un `guide_file` | La description composée ne finit pas par des lignes vides ; l'empreinte de la requête ne dépend pas d'un saut de ligne final. **Le prototype ne nettoie pas un `guide:` en ligne**, qui accepte même un texte fait d'espaces (`min_length=1`) : à aligner avant fusion |
| Pas de rendu des variables `{{ }}` dans le prototype | Décision à prendre (D-5) ; le prototype n'en fait pas, pour ne pas préjuger |

## Effets de bord

Chacun est classé : **démontré** (sur le prototype ou la référence), **lu** (dans le code, sans essai), ou **à vérifier**.

### 1. Un guide hors des prompts serait réécrit par l'agent — démontré

Sans borne, `guide_file: ../workspace/guide.md` est accepté. Or `workspace` peut être ouvert à `loom.local_files` en écriture : l'agent réécrit alors son propre guide, et le chargement suivant (`serve --reload`, redémarrage) le lui sert comme une consigne de la config. C'est une injection de prompt **persistante**, qui survit à la session.

- **Démontré :** dossier d'essai `e2e_escape` (annexe), `loom validate` monte l'agent sans erreur avec ce guide.
- **Corrigé dans le prototype :** le chemin résolu doit rester dans `prompts_dir`, que `_protected_paths` interdit déjà à tous les outils fournis. Test `test_a_guide_outside_the_prompts_dir_is_refused`.
- **Constat hors périmètre, même défaut :** `system_file: ../workspace/sys.md` est accepté **aujourd'hui** (démontré sur la référence, même dossier d'essai). Le prompt système d'un agent peut donc vivre dans un dossier qu'il peut réécrire. La même borne s'applique à `_with_prompt` et `_absolute_sessions` (D-7).

### 2. `ToolSpec.overridden()` perd sans rien dire un champ inconnu — démontré

`overridden()` (l. 171) recopie sur `ToolSpec`, par `model_copy(update=…)`, **tout** champ non nul de `ToolOverrides`. Pydantic n'y valide rien : un champ absent de `ToolSpec` est posé comme attribut, sans erreur, et n'apparaît ni dans `model_dump()` ni dans la description.

```python
ToolSpec(name="x", description="d", kind="python").model_copy(update={"guide": "G"})
# getattr → 'G' ; model_dump() → pas de clé 'guide'
```

Conséquences pour cette évolution, et pour toute clé future du bloc :
- `ToolSpec` doit avoir le champ `guide`, sinon le guide d'un outil MCP serait perdu sans message ;
- `guide_file` ne doit **pas** être recopié (`_NOT_COPIED`) ;
- un `ToolOverrides` construit en Python avec un `guide_file` jamais lu doit être refusé, pas ignoré : `overridden()` lève alors une erreur (test `test_an_unread_guide_file_is_refused_rather_than_lost`).

Le même filet manque pour `PythonTool` : une `AgentSpec` construite en Python, sans `load_config`, garderait un `guide_file` non lu, et le guide disparaîtrait. Le prototype refuse au montage (`_tool`, test `test_an_unread_guide_file_on_a_python_tool_stops_the_mount`).

### 3. Toujours une enveloppe `ConfiguredTool` — démontré, sans dommage

Un outil qui reçoit un guide est désormais enveloppé, même sans autre réglage (`configure` n'enveloppe que si un réglage est donné). Ce qui regarde **sous** l'enveloppe :

| Endroit | Ce qu'il fait | Avec un guide |
| --- | --- | --- |
| `ConfiguredTool.assess_call` | relaie l'évaluation par appel de l'outil | gardée : `excel` rend toujours `reversible` pour une écriture (démontré) ; `fixed` reste vide |
| `_assesses_approval` (l. 1119) | descend sous l'enveloppe | inchangé |
| `_returns_data` (l. 1648) | descend en boucle | inchangé |
| `_builtin_closables` (l. 1895) | descend **d'un** niveau pour trouver `aclose` (`loom.sql`) | inchangé : `configure` n'enveloppe qu'une fois |
| `loom validate` (`access/cli/main.py`, l. 1086) | descend **d'un** niveau pour `settings_lines` | inchangé : les lignes de `excel` s'affichent (démontré) |

Une remarque pour plus tard : `_builtin_closables` et `loom validate` ne descendent que d'un niveau. Une seconde enveloppe un jour (par exemple un guide posé par une politique) les casserait sans bruit ; une fonction `innermost()` commune éviterait le piège.

### 4. Le cache de prompt est invalidé à chaque changement de guide — lu

L'adaptateur Anthropic pose les points de cache dans l'ordre outils, système, conversation (`place_cache_points`, `cache: { tools: true, system: true, messages: true }`). Les outils sont en tête du préfixe :

- **changer un guide invalide tout le cache** de l'agent, pour toutes ses sessions : le premier appel suivant paie l'écriture du cache sur tout le préfixe ;
- **ajouter un guide** fait de même une fois.

Ce n'est pas un défaut — modifier le prompt système fait pareil —, mais un guide se retouche plus souvent qu'un prompt pendant la mise au point. Les fournisseurs OpenAI ont un cache de préfixe automatique, que la même modification invalide de la même façon (non mesuré).

### 5. Le guide coûte des tokens à chaque appel de modèle — lu, chiffré

Le guide part avec **chaque** appel du modèle de l'agent (orchestrateur ; les rôles n'ont pas d'outils), même dans un run où l'outil ne sert pas. Ordre de grandeur, avec les multiplicateurs de la config de `temp/agent` (`cache_write` = 1,25 × `input`, `cache_read` = 0,1 × `input`) et un guide de 1 500 tokens, sur un run de 6 appels de modèle :

| Situation | Tokens d'entrée facturés pour le guide (équivalent plein tarif) |
| --- | --- |
| Sans cache | 6 × 1 500 = 9 000 |
| Cache froid (premier run, ou guide modifié) | 1 500 × 1,25 + 5 × 150 = 2 625 |
| Cache chaud (run suivant dans la durée du cache) | 6 × 150 = 900 |

Autres effets du même ordre :
- **fenêtre de contexte** : `_check_context` (`engine/model_call.py`, l. 234) compte les outils dans l'estimation, donc le guide aussi ;
- **compaction** : elle ne compte que l'historique (`sessions/compaction.py`), pas les outils ; le guide ne la déclenche pas plus tôt ;
- **budgets** : `max_tokens` d'un run ou d'un client compte ces tokens ; `max_calls` n'est pas touché (choix 1).

`loom validate` devrait afficher la taille de chaque guide, et avertir au-delà d'un seuil (D-8, D-9).

### 6. Le rejeu identique diverge après un changement de guide — démontré

La description fait partie de la requête, donc de `request_hash` et de la partie `tools` de `request_parts` (`core/model/streaming.py`, l. 81). Démontré par la CLI :

```text
--- replay, guide inchangé
Identique  : le run se rejoue tel qu'il a été, appel par appel.
--- replay, guide modifié
Divergence : appel de modèle n°1 (main au journal) : la requête a changé
             ce qui diffère : les outils proposés
```

Le diagnostic est juste et précis, sans rien changer à `replay/`. À documenter : « un run fait avant une modification de guide ne se rejoue plus à l'identique ». Test unitaire : `test_changing_the_guide_changes_only_the_tools_part_of_the_request`.

**Pour mesurer l'effet d'un guide,** le rejeu en variante (`replay/variant.py`) sert les requêtes identiques depuis le journal et lance les autres pour de vrai, mais **n'exécute jamais un outil à effets de bord** : `excel` (`reversible`) y rend une erreur au modèle. La bonne mesure est donc `loom eval`, qui fait tourner l'agent pour de vrai sur un jeu de cas, avec puis sans le guide.

### 7. Le journal grossit avec la capture des échanges bruts — lu

Avec `telemetry.capture.raw_exchanges: true` — c'est le cas de `temp/agent` et des exemples `agent_postgres` —, chaque tentative d'appel écrit un `model.exchanged` avec le corps de la requête, outils compris, coupé à `raw_max_bytes` (256 Kio par défaut). Un guide de 6 000 caractères ajoute environ 6 Ko par appel de modèle au journal. Ces corps ne partent jamais vers un collecteur (`ModelExchanged`).

### 8. Les clients (tenancy) — lu

- **Commun à tous les clients**, comme les prompts (§6). Un client ne surcharge pas un guide.
- **`tools_deny`** retire l'outil, donc son guide : rien à faire.
- **Variables `{{ }}`.** Les prompts système sont rendus avec les variables du client (`prompt_text`, l. 963), et le chargement vérifie qu'aucune ne manque (`_check_variables`, M5). Le prototype ne rend pas les guides : un `{{ entreprise }}` y partirait tel quel. Rendre le guide demanderait de le faire au montage (les variables sont celles du client monté) et d'étendre `_check_variables` (D-5).

### 9. Les agents écrits dans `loom.yaml` — démontré, défaut existant

Les agents déclarés **dans** `loom.yaml` (clé `agents:`) ne passent ni par `_with_prompts` ni par `_with_contracts` : seuls ceux du dossier `agents_dir` le sont (`load_config`, l. 64-65). Démontré sur la référence : un agent en ligne avec `system_file: p.md` échoue au chargement (`load_config` → `_check_variables`), `prompt illisible : [Errno 2] No such file or directory: 'p.md'` (le chemin est lu depuis le dossier courant). Un `guide_file` sur un tel agent hérite du même défaut, et le prototype ne le rattrape qu'à moitié :

- sur un outil `- python:`, le montage le refuse avec un message clair (point 2) ;
- sur une référence `mcp:` ou `source:`, le chargement **et** le montage passent, `loom validate` dit « monté sans erreur », et l'erreur n'arrive qu'à l'ouverture de la source, **pendant un run**, en `ValueError` levée par `overridden()` (vérifié sur le prototype par le relecteur).

À corriger avant fusion : appliquer `_with_guides` (avec les prompts et les contrats) aux agents en ligne, et refuser au montage tout `guide_file` non lu, références MCP et de paquet comprises (D-7).

### 10. API Python — lu

Une `AgentSpec` construite en code, sans `load_config`, peut donner `guide` en ligne sans rien de plus. Un `guide_file` sur un outil `- python:` y est refusé au montage, avec un message qui renvoie à `load_config` (point 2) ; sur une référence MCP ou de paquet, il n'est refusé qu'à l'ouverture de la source, pendant le run (point 9, même correction). `system_file`, lui, est lu tel quel depuis le dossier courant : les deux comportements diffèrent, à dire dans la doc.

### 11. Ce qui n'est pas touché — lu

- **Événements et journal** : aucun événement ne porte de `ToolSpec` ni de `ToolDefinition` (recherche dans `core/events/`) ; seuls `request_hash`, `request_parts` et les échanges bruts changent de valeur.
- **Schéma JSON de la config** : dérivé des modèles (`config/schema.py`), il gagne les deux clés sans code.
- **Outils fournis à paramètres** : `_builtin_reference` lève à l'import si un paramètre d'outil porte le nom d'un réglage commun ; aucun ne s'appelle `guide` ni `guide_file` aujourd'hui. Les clés refusées par outil (`entry.refused`) ne concernent que `approval`, `side_effects` et `idempotent`.
- **Rôles et sous-agents** : sans outils propres (rôles) ou montés avec leur propre config (sous-agents).
- **Politiques** : `before_model` voit la requête, donc les descriptions composées, guide compris ; `before_tool` et `after_tool` reçoivent la `ToolSpec` (`core/model/policy.py`, l. 195 et 207) : la description de l'outil et, avec le prototype, le `guide` à part, pas la description composée. Aucune ne change.

### 12. À vérifier

- **Longueur maximale d'une description** chez chaque fournisseur (Anthropic, OpenAI Chat, OpenAI Responses, MiniMax) : pas vérifiée ici, faute de clé d'API dans cet espace. Un guide long pourrait être refusé ou tronqué par un fournisseur ; un essai réel par adaptateur est à faire avant de fixer un seuil (D-9).
- **Effet réel sur le modèle** : un essai `loom eval` avec Haiku, sur des demandes de classeurs (liste, fiche, export), avec et sans guide.

## Le choix 2 : chargement à la demande

Au lieu d'envoyer le guide entier, la description ne porte qu'une phrase (« Lis le guide avant ton premier appel ») et le modèle le demande quand il en a besoin.

**Mise en œuvre possible :** un outil interne, `guide`, ajouté par le montage dès qu'un outil de l'agent a un guide à la demande, avec un argument `tool` (énumération des outils guidés), qui rend le texte. Une action propre à chaque outil n'est pas possible de façon générique : le schéma d'entrée appartient à l'outil.

| | Choix 1 : ajouté à la description | Choix 2 : à la demande |
| --- | --- | --- |
| Tokens | à chaque appel, réduits par le cache | une fois par run, si l'outil sert |
| Appels de modèle | inchangés | +1 par run qui lit le guide (`max_calls`, `max_iterations`) |
| Latence | aucune | un aller-retour de plus |
| Fiabilité | le modèle a toujours le guide | le modèle peut ne pas le lire ; Haiku, surtout |
| Nom réservé | aucun | `guide`, à ajouter à `_check_names` ; collision possible avec un outil existant |
| Rejeu, évaluations | requête changée (point 6) | l'appel à `guide` est un appel d'outil de plus dans le journal et dans les évaluations qui comptent les outils |
| Compaction | sans effet | le texte rendu entre dans l'historique, et la compaction peut le résumer |
| Code | +132 lignes (prototype) | estimé deux à trois fois plus : un outil interne, son montage, ses tests |

**Recommandation : le choix 1 d'abord.** Il est simple, prévisible, et le cache en absorbe l'essentiel du coût. Le choix 2 ne vaut que pour des guides longs sur des outils rarement utilisés ; il pourra venir ensuite comme une option (`guide_load: on_demand`), sans changer la clé.

## Tests écrits sur le prototype (13)

| Test | Ce qu'il vérifie |
| --- | --- |
| `test_guide_and_guide_file_exclude_each_other` | exclusion, sur `PythonTool` et `ToolOverrides` |
| `test_the_file_is_read_at_load_relative_to_prompts_dir` | lecture, chemin, espaces retirés, `guide_file` effacé |
| `test_a_missing_or_empty_guide_stops_the_load` (×2) | `introuvable`, `vide` |
| `test_a_guide_outside_the_prompts_dir_is_refused` | effet de bord 1 |
| `test_an_unread_guide_file_on_a_python_tool_stops_the_mount` | effet de bord 2, `PythonTool` |
| `test_the_guide_follows_the_description_and_precedes_the_terminal_hint` | ordre de composition ; `description` intacte |
| `test_configure_with_a_guide_only_wraps_the_tool` | enveloppe, `fixed` vide ; sans réglage, pas d'enveloppe |
| `test_a_builtin_tool_keeps_its_behaviour_under_the_guide` | `loom.excel` monté : description composée, `assess_call` gardé |
| `test_an_override_guide_reaches_the_spec_and_the_agent_one_wins` | surcharges MCP : serveur puis agent ; avec `description:` |
| `test_an_unread_guide_file_is_refused_rather_than_lost` | effet de bord 2, `ToolOverrides` |
| `test_an_mcp_tool_gets_the_guide_read_from_its_file` | bout en bout avec un vrai serveur MCP stdio |
| `test_changing_the_guide_changes_only_the_tools_part_of_the_request` | effet de bord 6 |

**À ajouter avant fusion :**
- une source de paquet (`tool_sources[].tools`) avec guide, comme le test MCP ;
- `loom validate` : la ligne du guide (D-8) ;
- la borne `prompts_dir` sur `system_file` et la compaction, si D-7 est retenue ;
- un agent en ligne dans `loom.yaml`, si D-7 est retenue ;
- un `guide_file` non lu sur une référence MCP ou de paquet, refusé au montage (effet de bord 9) ;
- un `guide:` en ligne nettoyé, et refusé s'il n'a que des espaces ;
- les variables `{{ }}`, si D-5 est retenue ;
- un test d'intégration par adaptateur de modèle, si D-9 fixe un seuil.

## Plan de non-régression

| Contrôle | Référence | Prototype |
| --- | --- | --- |
| `pytest`, mêmes extras et services | 3 441 passés, 203 sautés | 3 454 passés (3 441 + 13 nouveaux), 203 sautés |
| `pyright` strict | 0 | 0 |
| `ruff check` | propre | propre |
| `ruff format --check` | non relancé | propre |
| `lint-imports` | non relancé | 5 contrats tenus |

Les tests existants n'ont demandé **aucune modification** : les nouvelles clés sont facultatives, et un outil sans guide suit exactement le chemin d'avant (`configure` ne l'enveloppe pas).

## Décisions à prendre

| # | Question | Recommandation |
| --- | --- | --- |
| D-1 | **Nom de la clé.** `skill`, `guide`/`guide_file`, `instructions`/`instructions_file`, autre | Éviter `skill`, qui fait attendre un Agent Skill (chargement progressif, scripts). Le couple `x`/`x_file` suit `system`/`system_file` |
| D-2 | Ajouté à la description (choix 1), ou à la demande (choix 2) | Choix 1 ; le choix 2 plus tard, en option |
| D-3 | Dossier de base du fichier : `prompts_dir` ou dossier de `loom.yaml` (comme `schema_file`) | `prompts_dir`, borné (effet de bord 1) |
| D-4 | Texte en ligne permis en plus du fichier | Oui, comme `system` |
| D-5 | Variables `{{ }}` des clients rendues dans le guide | Oui si un cas réel le demande ; sinon refuser `{{` au chargement, pour qu'un guide ne parte jamais avec une variable brute |
| D-6 | Serveur MCP et agent ont chacun un guide : remplacer ou concaténer | Remplacer, comme les autres clés du bloc |
| D-7 | Corriger en même temps les défauts existants : `system_file` hors de `prompts_dir`, agents en ligne de `loom.yaml` sans prompts ni contrats lus | Oui pour la borne (sécurité) ; les agents en ligne, dans le même lot ou juste après |
| D-8 | `loom validate` affiche le guide de chaque outil (fichier, taille en caractères et en tokens estimés) | Oui |
| D-9 | Avertir au-delà d'une taille (par exemple 8 000 caractères), ou refuser | Avertir ; refuser seulement si un fournisseur l'impose (§ À vérifier) |
| D-10 | Ajouter aussi `description:` à `PythonTool`, pour la parité avec `ToolOverrides` | Oui, même lot : quelques lignes, même chemin |

## Décisions prises (27/10)

| # | Décision |
| --- | --- |
| D-1 | **`skill` / `skill_file`**, choisi par Denis (la recommandation déconseillait `skill`) |
| D-2 | Ajouté à la description (choix 1) ; le chargement à la demande pourra venir en option |
| D-3 | Fichier relatif à `prompts_dir`, et borné à ce dossier une fois résolu (`..`, chemin absolu, lien symbolique) |
| D-4 | Texte en ligne permis (`skill`), exclusif de `skill_file` |
| D-5 | Pas de rendu des variables : un `{{` est refusé, en ligne comme en fichier |
| D-6 | Le skill de l'agent remplace celui du serveur ou de la source |
| D-7 | Borne `prompts_dir` aussi pour `system_file` (`main`, rôles, compaction) ; agents de `loom.yaml` lus comme ceux d'`agents_dir` (prompts, contrats, skills) ; tout `skill_file` non lu arrête le montage, références MCP et de paquet comprises |
| D-8 | `loom validate` montre chaque skill et sa taille, outils MCP et de paquet déclarés compris |
| D-9 | Avertissement au-delà de 8 000 caractères, jamais un refus, même en profil `prod` |
| D-10 | `description` ajoutée à l'outil Python ; refusée sur `local_files`, `excel` et `sql`, dont la description est tirée des paramètres (relecture du code) |

## Plan de mise en œuvre

1. **Trancher D-1 à D-10.**
2. **Code**, à partir du prototype : renommer selon D-1 ; D-5, D-7, D-8, D-10 selon les décisions ; refuser au montage un `guide_file` non lu sur une référence MCP ou de paquet (effet de bord 9) ; nettoyer un `guide:` en ligne comme un fichier ; une fonction `innermost()` pour `_builtin_closables` et `loom validate` (effet de bord 3).
3. **Tests** : ceux du prototype, plus la liste « à ajouter ».
4. **Documentation** :
   - `docs/configuration.md` : la clé dans le tableau des outils Python et dans le bloc surcharges d'outil ;
   - `docs/howto/06-outils-fournis.md` : un guide pour `loom.excel`, avec la limite (un guide n'ajoute pas de mise en forme) ;
   - le chapitre des outils du howto : un guide pour un outil à soi et pour un outil MCP ;
   - `docs/fonctions.md` : une entrée, et le rejeu (effet de bord 6) ;
   - `CHANGELOG.md` : version 2.0.15.
5. **Essai réel** : `temp/agent` avec un guide `excel.md`, puis `loom eval` avec et sans guide sur cinq demandes de classeurs.

## Annexe : expériences reproduites

Toutes dans l'espace de travail cloud, sur le clone ; rien sur le poste.

**Montage et description composée** (`e2e`, agent `loom.excel` avec `guide_file: excel_guide.md`, `loom.current_datetime` avec `guide:`) :

```text
== current_datetime ConfiguredTool inner: FunctionTool assessed: True
  desc chars: 211 -> sent: 258
== excel ConfiguredTool inner: ExcelTool assessed: True
  desc chars: 1846 -> sent: 1943
  tail: '…jamais le contenu.\n\n## Guide Excel\n- Une fiche ne va pas dans un tableau Champ | Valeur.\n- Dates : type date_heure.'
assess write: CallAssessment(side_effects='reversible', approval='never', idempotent=True, reason=None)
```

**`loom validate`** : `1 agent(s) monté(s) sans erreur.`, avec les lignes `outil excel : …` affichées.

**Rejeu** : `loom run agent "Salut" --run-id RUN-1`, puis `loom replay RUN-1` avant et après une ligne ajoutée au guide (§ Effets de bord, point 6).

**Guide et prompt hors du dossier des prompts** (`e2e_escape`, `loom.local_files` avec `rights: [ read, write, overwrite ]` sur `workspace`) : avant la borne, `guide_file: ../workspace/guide.md` et `system_file: ../workspace/sys.md` sont acceptés (`1 agent(s) monté(s) sans erreur.`).

**Perte silencieuse d'un champ** :

```python
>>> s = ToolSpec(name="x", description="d", kind="python")
>>> c = s.model_copy(update={"guide": "G"})
>>> getattr(c, "guide", None), "guide" in c.model_dump()
('G', False)
```

**Agent en ligne dans `loom.yaml`** (référence, sans le prototype) :

```text
ConfigError: …/inline/loom.yaml: Agent 'inline', main.system_file — prompt illisible :
[Errno 2] No such file or directory: 'p.md'
```

## Réalisation (27/10)

Sur le clone, à partir du prototype ; reporté dans `/home/denis/dev/loom_v2` sans commit. Rangé dans la section **2.0.14 (non publiée)** du CHANGELOG plutôt qu'en 2.0.15, puisque 2.0.14 n'est pas sortie.

| Fichier | Lignes | Ce qui change |
| --- | --- | --- |
| `core/model/tooling.py` | +67 / −4 | `SKILL_SEPARATOR`, `SKILL_WARN_CHARS`, `checked_skill` (espaces retirés, vide et `{{` refusés), `SkillText`, `exclusive_skill` ; `ToolOverrides.skill`, `skill_file` ; `ToolSpec.skill` (validé) ; `overridden()` ne recopie pas `skill_file`, refuse un fichier non lu et revérifie le skill ; `definition()` compose |
| `core/model/__init__.py` | +10 | exports |
| `agents/spec.py` | +20 | `PythonTool.description`, `skill`, `skill_file` ; `description` refusée sur un outil fourni qui tire la sienne de ses paramètres |
| `tools_builtin/__init__.py` | +7 | `Builtin.own_description`, vrai pour `local_files`, `excel` et `sql` |
| `config/loader.py` | +124 / −14 | `_prepared` (prompts, skills, contrats d'un agent, de fichier ou de `loom.yaml`) ; `_within_prompts` (borne, pour `system_file`, compaction et `skill_file`) ; `_hint` (chemin qui recommence par le dossier des prompts) ; `_with_tool_skills`, `_with_skills`, `_skill_read` ; skills de `mcp_servers[]` et `tool_sources[]` |
| `runtime/wiring.py` | +89 / −5 | `_tool` passe `description` et `skill`, refuse un `skill_file` non lu ; `_source_refs`, `declared_skills` (un skill par outil exposé : priorité de l'agent, `include`/`exclude`), `_check_skills_read`, `skill_warnings` ; `innermost()` dans `_builtin_closables` et `_returns_data` |
| `runtime/__init__.py`, `tools/__init__.py`, `tools/configured.py` | +28 / −2 | `declared_skills`, `skill_warnings` ; `configure(description=…, skill=…)`, qui revérifie le skill ; `innermost()` |
| `access/cli/main.py` | +15 / −3 | `loom validate` : une ligne par skill, avec sa taille, sous le nom que voit le modèle ; `innermost()` |
| `tests/unit/test_tool_skill.py` | +443 (nouveau) | 24 tests, 35 cas |

**Relecture indépendante du code**, avant report : dix remarques, toutes traitées.
- **Trois tests auraient échoué dans le job CI sans extras** : ils montaient `loom.excel` (openpyxl) ou un serveur MCP. Ils passent désormais par `loom.local_files`, ou sautent sans `mcp`. Vérifié en bloquant les imports des extras : 33 passés, 2 sautés.
- **`description` sur `local_files`, `excel` ou `sql` effaçait la liste des dossiers, droits ou tables** que leur description générée donne au modèle. Elle y est maintenant refusée, et le message renvoie à `skill`.
- **`loom validate` et l'avertissement de taille ignoraient la priorité et `include`/`exclude`**, et doublaient une ligne pour un serveur référencé deux fois. Ils suivent maintenant la même lecture que `_sources_asking_approval`.
- **Un skill posé par `model_copy` ou `configure()` échappait au contrôle** : il est revérifié là où il sert.
- **Le même outil portait deux noms**, selon l'avertissement ou `validate`. Il en a un seul.
- **Un chemin `prompts/x.md` reçoit une indication** : « le chemin est relatif à prompts_dir : écrire x.md ».
- Docs complétées (`compaction.system_file`, ligne des chemins de `configuration.md`).
- Tests ajoutés : chemin absolu, lien symbolique, rôle, compaction, serveur MCP hors des prompts, agent de `loom.yaml` refusé, fichier illisible, priorité et sélection des skills MCP.

Documentation : `configuration.md` (`description`, `skill`, `skill_file`, `prompts_dir`, `system_file`, agents de `loom.yaml`, bloc surcharges), guide chapitre 32 (« Un skill pour bien s'en servir », dans `loom.excel`) et chapitre 13, `fonctions.md` (D14), `CHANGELOG.md`.

Essai réel par la CLI, sur un agent comme celui de `temp/agent` (`loom.excel` avec le skill du guide, `loom.current_datetime` avec `description`) : `loom validate` affiche `outil excel : skill, 298 caractères (environ 74 tokens)` ; `loom run`, puis `loom replay` : « Identique ».

| Contrôle | Référence | Réalisation |
| --- | --- | --- |
| `pytest`, mêmes extras et services | 3 441 passés, 203 sautés | 3 476 passés (3 441 + 35 nouveaux), 203 sautés |
| `pyright` strict | 0 | 0 |
| `ruff check`, `ruff format --check` | propres | propres |
| `lint-imports` | — | 5 contrats tenus |
| Job CI sans extras, simulé (imports des extras bloqués), sur `test_tool_skill.py` | — | 33 passés, 2 sautés |

