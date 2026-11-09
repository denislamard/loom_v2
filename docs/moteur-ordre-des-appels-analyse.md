# Analyse d'impact — l'ordre des appels d'outils d'un même tour (loom_v2)

9 novembre 2026 · Denis Lamard

> **Décisions prises le 09/11/2026** : D-1, D-2, D-4, D-5, D-7, D-10 : (a). D-3, D-6, D-11 : (b). D-8 : `order`. D-9 : la version 2.0.20, l'actuelle. Le réglage par agent (D-2) reprend le chemin de `loom.yaml` : `execution.tools.order` dans le fichier de l'agent. Mise en œuvre : § Mise en œuvre, en fin de document.

## En bref

Quand le modèle de langage demande plusieurs appels d'outils dans une même réponse, le moteur les lance tous en même temps. Un appel qui écrit et un appel qui relit le même fichier font donc la course : dans deux sessions du POC shaper.ia, la relecture a fini avant l'écriture et a rendu **l'ancienne version** du document, sans erreur ni avertissement. Dans l'une, le modèle a vu un écart sans en trouver la cause ; dans l'autre, il a présenté la relecture comme la preuve que sa correction était faite.

**Verdict : la course se corrige dans le moteur, par une barrière sur les effets (option a), sans toucher aux outils ni au schéma des événements.** Les appels sans effet d'une réponse restent en parallèle. Un appel à effet attend que tout ce qui le précède dans la réponse soit fini, et ce qui le suit l'attend. Un appel qui attend une approbation retient de même ceux qui le suivent. Le prototype tient en un peu plus de cent lignes de code dans `engine/executor.py` (180 avec les commentaires), plus une clé de configuration, `execution.tools.order` (`effects` par défaut, `free` pour le comportement d'avant).

Le prix est du temps, et seulement là où il y a un effet : une écriture suivie d'une lecture prend la somme des deux durées au lieu du maximum. Sur les 47 lots d'outils des 19 journaux du POC, un seul s'allonge de façon sensible : trois `wireframe` du même tour passent de 0,75 s à 2,1 s, sur un tour dont l'appel de modèle prend plusieurs secondes.

| Point | Constat | Preuve |
| --- | --- | --- |
| La course existe | Écriture lente puis lecture du même fichier dans une réponse : la lecture rend « version 1 » quand l'écriture pose « version 2 » ; « fichier introuvable » si le fichier n'existait pas | Démontré : essai du moteur (`sdk: fake`, `loom run`), § Reproduire la course |
| Le cas shaper | La même réponse que celle de la session `01a1bc66…` (mêmes arguments de `word`, même version d'avant sur le disque) : la relecture rend l'ancien texte avant le correctif, le nouveau après | Démontré : cas rejoué avec le vrai `loom.word` |
| La barrière corrige | Écriture puis lecture : la lecture rend la nouvelle version ; deux écritures gardent l'ordre du modèle | Démontré : 29 tests dans `tests/unit/test_call_order.py` |
| Les lectures restent parallèles | Trois lectures de 100 ms : 102 ms avant comme après | Démontré : mesure (§ Mesures) |
| Approbation | Le run se met en pause après les appels qui précèdent l'écriture à approuver, plus aucun après ; deux écritures à approuver sont demandées dans la même pause, puis exécutées dans l'ordre | Démontré : tests de bout en bout (`Loom`, journal JSONL) |
| Échec, délai, état inconnu, annulation | Un échec ou un délai dépassé solde l'appel et la chaîne continue ; un état inconnu tranché par un humain (`on_unknown: pause`) ou une annulation arrêtent la chaîne, et les appels suivants n'ont aucun `tool.called` | Démontré (tests) |
| Reprise | Un appel pas encore parti n'a plus de `tool.called` : à la reprise, il part normalement, sans « état inconnu » | Démontré (tests sur un journal replié) |
| Rejeu et consommation | Un journal d'avant se rejoue à l'identique ; mêmes itérations, mêmes tokens, même requête au modèle | Démontré (tests, et rejeu de 4 journaux d'essai) |
| Affichage en direct | Les lignes suivent le nouvel ordre du journal : « ecrire : fait » avant « lire(…) » | Démontré pour `Progress` (CLI `--stream`, notifications MCP) ; à vérifier sur un client REST |
| Schéma des événements | Inchangé : mêmes types, mêmes champs, `schema_version` 1 ; seul l'ordre des `tool.called` change | Démontré : les 19 journaux du POC se relisent |
| Non-régression | Un seul test existant change d'attente (l'ordre du journal, en deux variantes) ; tout le reste passe, avec et sans extras ; pyright, ruff et les 6 contrats d'import passent | Démontré (§ Mesures) |
| Effets mal déclarés | Un outil Python qui écrit sans déclarer `side_effects` passe pour une lecture : la course reste possible pour lui | Démontré par le code ; c'est la limite de l'option |

**Trois points demandent une décision avant tout :**

1. **L'option (D-1).** Je recommande la barrière sur les effets. Les autres options sont soit fausses (consigne dans le prompt), soit trop lentes (un appel par réponse), soit plus fines mais dépendantes d'outils qui ne savent pas encore nommer leurs ressources (verrou par ressource).
2. **Le défaut et le réglage (D-2).** Je recommande la correction active par défaut, réglable globalement et par agent. C'est un changement de comportement, marqué ⚠ au CHANGELOG.
3. **Les sous-agents (D-3).** Ils sont déclarés `irreversible` : deux sous-agents d'une même réponse ne tournent plus en même temps. Je recommande de laisser la référence du sous-agent déclarer ses effets.

## Périmètre et état de référence

L'analyse porte sur ton commit `020cc8f` (« tool builtin word style - B2&B3 », égal à `origin/main`), version 2.0.20 non publiée. Rien n'a été écrit dans `/home/denis/dev/loom_v2`, à part ce document : ni commit, ni push. Les fichiers `.env` n'ont pas été lus.

Le prototype a été fait dans l'espace de travail cloud de Claude, sur un clone de ce commit : Python 3.14.6, environnement `uv sync --all-extras --group types` (anthropic 1.6.0, openai 3.14.1), et un second environnement sans extra pour la passe « noyau seul ». Les journaux du POC (`/home/denis/dev/poc_shapper/data/journal/default/`) et sa config ont été copiés dans cet espace, sans le `.env` ; le cas shaper a été rejoué avec une clé factice.

Contrairement au lot des styles, **le prototype est une implémentation** : `executor.py` est complet, testé, et passe pyright strict et ruff. Il reste à le relire, à trancher les décisions et à écrire la documentation.

## Le problème, vu dans les journaux

| Session | Réponse du modèle | Ce qui s'est passé |
| --- | --- | --- |
| `01a1bc66-7989-7323-bffe-9f84547790e2`, étape 4 (événements 19 à 24) | `word` (écriture de `workspace/maquettes-test.docx`, `overwrite: true`) puis `word` `read` du même fichier | `tool.called` 20 et 21 à 4 ms d'écart ; la lecture finit en 22 (5 ms), l'écriture en 23 (60 ms). La lecture rend 13 blocs, dont « Le salarié décrit la formation souhaitée… » ; l'écriture posait « Le salarié (acteur : Salarié) renseigne le détail… ». Le modèle a vu l'écart (« la relecture ne correspond pas à ce que j'ai envoyé ») sans en comprendre la cause |
| `test-100`, étape 6 (événements 31 à 35) | Même enchaînement sur `workspace/test-100.docx` | La lecture finit en 34 (12 ms), l'écriture en 35 (62 ms). L'écriture passait le titre « Connexion » du niveau 2 au niveau 1 pour lever un avertissement ; la relecture rend encore le niveau 2. Le modèle écrit pourtant « J'ai passé le titre « Connexion » au niveau 1 et réécrit le fichier » et cite la relecture comme vérification |

Tes « étapes 19 et 31 » sont les numéros d'événement des `step.started` ; ce sont les étapes 4 et 6 de leur run.

Sur les 19 journaux du POC, 8 lots d'outils sur 47 portent plusieurs appels, et tous contiennent un appel à effet : deux fois `word` écriture + lecture (la course ci-dessus), trois fois `word` lecture + `wireframe`, une fois `wireframe` + `word` écriture (`test-03` : l'écriture a échoué pour une autre raison, mais elle aurait cherché une image pas encore dessinée), une fois `word` modèle + `wireframe`, une fois trois `wireframe`.

## Analyse fonctionnelle

### La règle

Les appels d'une réponse partent par **segments**, dans l'ordre où le modèle les a écrits :

- les appels **sans effet** qui se suivent forment un segment, et partent ensemble ;
- un appel **à effet** forme un segment à lui seul ;
- un segment part quand le précédent est entièrement soldé (résultat, erreur, délai dépassé).

```
réponse :   lire A   lire B   écrire C   lire C   lire D   écrire E
segments : [lire A · lire B] → [écrire C] → [lire C · lire D] → [écrire E]
```

« À effet » veut dire : `side_effects` autre que `none`, selon l'évaluation de l'appel (`assess_call`) quand l'outil en fait une, sinon selon sa déclaration. C'est la règle qui décide déjà de l'approbation et de la reprise d'un appel interrompu : le moteur n'a rien de nouveau à apprendre des outils. Une lecture `word` reste donc une lecture, alors que l'outil `word` est déclaré au pire de ses appels (`irreversible`).

### Ce qui attend un humain

- **Une approbation en attente** (personne pour la trancher en ligne) retient ce qui la suit : tout, si l'appel à approuver a un effet ; seulement les appels à effet, s'il n'en a pas (une lecture dans un dossier sous `approval: { read: always }`). Les appels retenus restent en attente, sans aucun événement, et partent après la décision. Le run se met en pause au même moment qu'aujourd'hui (à la fin du lot), mais **moins d'appels sont passés avant** : la relecture qui suit une écriture à approuver ne part plus avant elle.
- **Un appel retenu qui demande de toute façon une approbation la demande tout de suite** (déclaration, évaluation de l'appel ou consigne de l'appelant) : l'humain tranche tout le tour dans la même pause. Accordés, les appels partent dans l'ordre de la réponse.
- **Un approbateur en ligne** (`approver`, un rejeu) tranche pendant la préparation, avant tout lancement, comme aujourd'hui : rien n'est retenu.
- **Une question à l'utilisateur** (`loom.ask`) ne retient rien : elle est sans effet, et les appels qui la suivent ont déjà leurs arguments (D-5).

### Ce que voit le modèle

Rien ne change pour lui. Les résultats d'un tour lui parviennent déjà **dans l'ordre des appels**, quel que soit leur ordre d'arrivée (`engine/refs.py`, `in_call_order`, appelé dans `loop.py` au moment de construire la requête). Seul le contenu change : la relecture rend la nouvelle version.

### Ce que voit l'utilisateur

Le direct (`loom run --stream`, notifications MCP, flux SSE) suit le journal. Il montre désormais :

```
· ecrire(nom='note.txt', texte='version 2')
· ecrire : fait
· lire(nom='note.txt')
· lire : fait
```

au lieu des deux lignes d'appel suivies des deux lignes « fait ».

### La configuration

Le prototype ajoute `execution.tools.order` dans `loom.yaml` :

```yaml
execution:
  tools:
    order: effects   # par défaut ; free : tous les appels d'un tour partent ensemble (avant 2.0.20)
```

D-2 propose d'y ajouter un réglage par agent.

### Ce que la barrière ne règle pas

1. **Un outil qui écrit sans le déclarer.** `@tool` déclare `side_effects: none` par défaut (`tools/python.py`) : un outil Python qui écrit sans le dire passe pour une lecture, et peut toujours se faire doubler. C'est la même faille qu'aujourd'hui pour l'approbation et la reprise (§ Qui connaît les effets d'un appel).
2. **Un outil exécuté dans un thread qui dépasse son délai** continue en arrière-plan : son écriture peut arriver après la lecture qui le suit (B-2).
3. **Le state de la session** : un outil lit le state tel qu'au début de son lot (option C, `ToolContext.state`). Une écriture `loom.state` suivie, dans la même réponse, d'un outil qui lit le state ne lui est pas visible. C'est voulu et documenté, indépendant de l'ordre.
4. **Un rôle à contexte `tool_results`** lit les résultats du run tels qu'au début du lot (`RunView`) : il ne voit pas le résultat d'un appel de la même réponse, même lancé avant lui.

## Analyse technique

### Reproduire la course

Le dossier d'essai : un `loom.yaml` avec un modèle `sdk: fake` dont `params.script` porte deux `tool_calls` dans une même réponse, et deux outils Python : `ecrire` (`side_effects: reversible`, `idempotent: true`, 0,3 s avant d'écrire) et `lire`.

```yaml
models:
- id: SIMULE
  sdk: fake
  model: fake-1
  params:
    script:
    - tool_calls:
      - {name: ecrire, arguments: {nom: note.txt, texte: "version 2"}}
      - {name: lire, arguments: {nom: note.txt}}
    - text: "Écrit puis relu."
```

`workspace/note.txt` contient « version 1 », puis `loom run course "Écris puis relis la note." --stream`.

| Code | Journal du lot | La lecture rend |
| --- | --- | --- |
| `020cc8f` (actuel) | `tool.called` ecrire (9), `tool.called` lire (10), `tool.completed` lire (11, 0 ms), `tool.completed` ecrire (12, 302 ms) | « version 1 » |
| `020cc8f`, fichier absent | même ordre | « note.txt : fichier introuvable » |
| Prototype | `tool.called` ecrire (9), `tool.completed` ecrire (10, 303 ms), `tool.called` lire (11), `tool.completed` lire (12) | « version 2 » |
| Prototype, fichier absent | même ordre | « version 2 » |

**Le cas shaper rejoué.** Les journaux d'avant ne se rejouent plus à l'identique avec la config actuelle du POC, avec ou sans correctif : `loom replay` diverge dès le premier appel de modèle (« le prompt système, les outils proposés » ont changé depuis le 8 novembre). J'ai donc rejoué la situation : un modèle simulé écrit d'abord la version d'avant (les 12 blocs que la lecture de l'événement 22 a rendus), puis envoie **les mêmes arguments** que l'événement 16 (l'écriture des 12 blocs, `overwrite: true`, puis `read`), avec le vrai `loom.word` (`images: true`, dossier `workspace` en `read, write, overwrite`) et quatre images PNG de remplacement.

| Code | Ordre au journal | Deuxième paragraphe relu |
| --- | --- | --- |
| `020cc8f` | `tool.called` write, `tool.called` read, `tool.completed` read (4 ms), `tool.completed` write (22 ms) | « Le salarié décrit la formation souhaitée, son… » (l'ancien) |
| Prototype | `tool.called` write, `tool.completed` write (18 ms), `tool.called` read, `tool.completed` read (5 ms) | « Le salarié (acteur : Salarié) renseigne le dé… » (le nouveau) |

### État des lieux du moteur

**Lancement, attente, annulation** (`engine/executor.py`, `ToolExecutor.run_batch`) :

1. **Préparation**, appel par appel et dans l'ordre de la réponse : outil connu, arguments, références `$ref`, schéma (`_prepare`) ; question à l'utilisateur (`_ask`) ; approbation déjà tranchée ; règle de reprise d'un appel déjà lancé (`call.started`, `_retry_safe`, `on_unknown`) ; politiques `before_tool` ; approbation à demander (`_asks_approval`, avec `assessed(...)`). Un approbateur en ligne tranche ici même. L'appel finit dans `ready`, dans `held` (approbation sans approbateur), ou se conclut en erreur sans être lancé.
2. **Lancement** : tous les `tool.called` des appels prêts sont émis, puis une tâche `asyncio.create_task(execute(item))` par appel. `drive` écrit chaque événement avant de demander le suivant au générateur : un `tool.called` est donc au journal avant que son outil parte.
3. **Attente** : une file (`asyncio.Queue`) reçoit, au fil de l'eau, les événements des outils délégués, les fichiers rangés, les décisions de politique et les résultats. Un appel est soldé par un `ToolCompleted`, un `Waiting` (sous-agent en pause, pas de résultat) ou un `_Unknown` (effet d'état inconnu, décidé après le lot). Un `_Crashed` (exception sortie d'une tâche) interrompt le lot. À la sortie, normale ou non, toutes les tâches sont annulées et attendues.
4. **Après le lot** : les appels d'état inconnu (erreur au modèle, ou demande d'approbation), puis les demandes d'approbation et les questions retenues. Elles s'écrivent après le lot pour qu'une décision arrivée pendant qu'il tourne ne trouve pas le run encore piloté.

**L'ordre des résultats.** Le journal garde l'ordre d'arrivée des `tool.completed`, et la projection (`core/projections/run_state.py`) ajoute un message `tool` par résultat dans cet ordre. La requête suivante les remet dans l'ordre des appels (`in_call_order`) : la requête ne dépend pas des durées des outils, ce qui la rend stable pour le cache des fournisseurs et pour le rejeu.

**La reprise.** `tool.called` marque un appel comme lancé (`PendingCall.started`). À la reprise, un appel lancé sans résultat est relancé s'il est sûr à refaire (sans effet, ou idempotent, selon `assess_call` puis la déclaration) ; sinon `on_unknown` décide : l'erreur `UNKNOWN_STATE` au modèle, ou une approbation. La clé d'idempotence est par appel (`run_id`, `call_id`), donc indépendante de l'ordre. Aujourd'hui, tous les appels d'un lot sont « lancés » dès son début : un arrêt en plein lot les laisse tous d'état incertain.

**Le rejeu.** Le livre du rejeu (`replay/book.py`) sert un appel de modèle par l'empreinte de sa requête, et un outil par son `(run_id, call_id)`. Le parcours comparé à la fin ignore `PAUSED` et `WAITING_CHILD`. Rien ne dépend de l'ordre d'arrivée des résultats, grâce à `in_call_order`.

### Qui connaît les effets d'un appel

| Source | Effet retenu | Cas mal connus, et ce qu'il en advient |
| --- | --- | --- |
| Outil Python (`@tool`, `FunctionTool`) | `side_effects` déclaré ; `none` par défaut | **Un outil qui écrit sans le déclarer passe pour une lecture** : pas d'ordre, pas d'approbation par défaut, relancé à la reprise, exécuté en éval. La config peut le corriger (`side_effects:` sur la référence). Ton `wireframe` est bien déclaré (`irreversible`, `idempotent`) |
| `loom.word`, `loom.pdf`, `loom.local_files`, `loom.excel`, `loom.sql`, `loom.memory` | `assess_call` appel par appel : une lecture `none`, une écriture `reversible` ou `irreversible` ; la déclaration (`spec`) dit le pire des appels | Une évaluation qui lève vaut `irreversible` (prudent). Des arguments refusés valent `none` : l'appel échouera, sans effet. La config ne peut pas fixer `side_effects` sur ces outils (refusé au chargement) |
| Outil MCP (`adapters/mcp/convert.py`) | `readOnlyHint: true` → `none` ; `destructiveHint: false` → `reversible` ; sans indication → `irreversible` | Un serveur sans annotations : **tous ses outils passent pour des écritures**, et ses appels d'un même tour s'exécutent l'un après l'autre. Un serveur qui annonce à tort `readOnlyHint` : ses écritures passent pour des lectures. La config corrige outil par outil (`tools:` sous la référence `mcp:` de l'agent) |
| Rôle (`engine/roles.py`) | `none` | Juste : un rôle est un appel de modèle, sans outil. Deux rôles d'un même tour restent en parallèle |
| Sous-agent (`engine/subagents.py`) | `irreversible`, `idempotent` | Prudent : ses effets sont ceux des outils de son agent. **Deux sous-agents d'un même tour s'exécutent désormais l'un après l'autre** (D-3). Aucune déclaration possible sur la référence |
| `loom.state` | `none` | Juste pour l'ordre : un outil lit le state du début du lot, les écritures sont comptées dans l'ordre des appels (`BatchTally`) |
| `loom.ask` | `none`, et rien n'est exécuté | La question s'écrit, le run attend ; elle ne retient rien (D-5) |
| `artifact_read`, `loom.fetch_page` | `none` | Juste |
| Forge (`adapters/firecracker/forge.py`) | `reversible` | Ses appels d'un même tour s'exécutent l'un après l'autre |
| Config (`side_effects`, `approval`, `idempotent` sur une référence) | Remplace la déclaration ; vaut aussi pour `assess_call` quand elle fixe le champ (`tools/configured.py`) | — |

### Les options

| Option | Justesse | Coût en temps | Complexité | Effet sur le reste |
| --- | --- | --- | --- | --- |
| **(a) Barrière sur les effets** | Juste dès que les effets sont déclarés ; s'appuie sur ce qui décide déjà de l'approbation et de la reprise | Lectures : inchangé. Une écriture et ce qui l'entoure : somme des durées au lieu du maximum | ~100 lignes dans `executor.py`, aucune ailleurs dans le moteur | Ordre du journal ; pause plus tôt dans le lot ; sous-agents et outils MCP sans annotation en série |
| (b) Même outil en série, outils différents en parallèle | Corrige `word` + `word`, pas `wireframe` + `word` (l'image que `word` doit lire), ni `local_files` + `word` | Sérialise deux lectures `word` sans raison | Faible | Ne règle pas le cas que tu cites |
| (c) Verrou par ressource (chemin, table) | La plus fine : deux écritures de fichiers différents restent parallèles | Le meilleur | Chaque outil doit nommer ses ressources (un nouveau protocole, comme `assess_call`), et un outil qui ne sait pas le faire (MCP, Python) retombe sur (a). Les ressources se recouvrent mal : un dossier et son fichier, une table et une vue, un `.docx` et l'image qu'il insère | Large : chaque outil fourni |
| (d) Un appel par réponse (`disable_parallel_tool_use`) | Juste, quel que soit l'outil | Un appel de modèle (2 à 7 s dans tes journaux) par appel d'outil, même pour trois lectures | Faible côté moteur, mais propre à chaque SDK | Plus d'appels, donc de tokens et de budget ; le modèle ne peut plus regrouper ses lectures |
| (e) Consigne dans le prompt | Non garantie : le modèle a déjà enchaîné écriture et lecture alors qu'il pouvait attendre | Nul | Nulle | Des tokens à chaque appel |
| (f) L'outil d'écriture rend ce qu'il a écrit | Supprime le besoin de relire pour `word` seulement | Nul | Par outil | Ne règle rien pour `wireframe` → `word`, ni pour les autres outils |
| (g) Refuser au modèle une lecture d'une ressource écrite dans le même tour | Juste si l'outil nomme ses ressources | Un tour de plus, mais seulement en cas de conflit | Comme (c) | Le modèle apprend à séparer ses tours |

Sur (d) : chez Anthropic, `disable_parallel_tool_use: true` se pose **dans** `tool_choice` ; avec `auto`, le modèle fait au plus un appel par réponse ([documentation](https://platform.claude.com/docs/en/agents-and-tools/tool-use/parallel-tool-use)). Chez OpenAI, c'est `parallel_tool_calls: false`, au niveau de la requête, dans Chat Completions comme dans Responses (non essayé ici). Aujourd'hui, `params` part en `extra_body` dans les deux adaptateurs. J'ai vérifié qu'un `tool_choice` mis dans `params` d'un modèle Anthropic **remplace** celui que calcule l'adaptateur : il passerait, mais écraserait aussi le `none` de la réponse forcée et le `any` de `required`. Ce n'est donc pas un contournement sûr ; il faudrait une capacité du modèle (`capabilities.parallel_tools: false`) traduite par chaque adaptateur.

**Variante écartée de (a) : un segment par étape.** Le moteur sait déjà enchaîner deux étapes d'outils : un lot qui laisse des appels en attente, sans pause, est relancé par la boucle (`_decide` ne décide rien, `step` relance `_tool_step`). Chaque segment aurait été une étape durable, sans toucher à l'ordre des `tool.called` dans un lot. Mais il aurait fallu choisir le segment **avant** les politiques `before_tool`, donc d'après les arguments d'origine, et les politiques des appels suivants auraient été rejouées à chaque étape. La barrière dans le lot garde une seule préparation, et un seul passage des politiques.

### Ce que montre le prototype

Les interactions demandées, chacune démontrée par un test de `tests/unit/test_call_order.py` (29 tests) :

| Interaction | Ce qui se passe | Test |
| --- | --- | --- |
| Écriture puis lecture | La lecture rend la nouvelle version ; sans ordre (`free`), l'ancienne | `test_a_read_after_a_write_in_the_same_turn_reads_the_new_version`, `test_without_ordering_the_read_overtakes_the_write` |
| Lectures | Toujours ensemble ; une écriture attend celles d'avant et retient celles d'après | `test_reads_still_run_together`, `test_a_write_waits_for_the_reads_before_it_and_holds_the_reads_after_it` |
| Deux écritures | Dans l'ordre du modèle, même si la première est la plus lente | `test_two_writes_keep_the_order_the_model_wrote` |
| Approbation en attente | Les appels d'avant passent, ceux d'après attendent sans événement ; la pause arrive avec moins d'appels passés ; après l'accord, l'écriture puis la relecture, qui lit la nouvelle version | `test_a_write_waiting_for_an_approval_holds_back_the_calls_after_it`, `test_the_run_pauses_before_the_calls_after_a_write_to_approve` (bout en bout) |
| Lecture à approuver | Retient seulement les écritures qui la suivent | `test_a_read_waiting_for_an_approval_holds_back_only_the_writes_after_it` |
| Deux écritures à approuver | Demandées dans la même pause ; une décision pour tout le run ; exécutées dans l'ordre, avec la lecture qui les sépare | `test_two_writes_to_approve_are_asked_together_and_run_in_order`, `test_one_decision_for_two_writes_then_everything_runs_in_order` (bout en bout) |
| Échec, délai dépassé | L'appel est soldé en erreur, la chaîne continue | `test_a_failed_write_does_not_stop_the_chain`, `test_a_timed_out_write_does_not_stop_the_chain` |
| État inconnu (`on_unknown`) | `error` : soldé, la chaîne continue ; `pause` : la chaîne s'arrête, la demande s'écrit, la suite attend | `test_an_unknown_effect_said_to_the_model_does_not_stop_the_chain`, `test_an_unknown_effect_a_human_must_decide_stops_the_chain` |
| Annulation entre deux appels | Le suivant n'a jamais de `tool.called` et n'est pas lancé | `test_closing_the_batch_launches_nothing_more` |
| Sous-agent en pause au milieu | Les appels qui le suivent ne partent pas ; ils partiront à la reprise du parent | `test_a_paused_sub_agent_holds_back_the_calls_after_it` |
| Rôles, sous-agents | Deux rôles restent ensemble ; deux sous-agents passent l'un après l'autre | `test_two_roles_of_a_turn_still_run_together`, `test_two_sub_agents_of_a_turn_now_run_one_after_the_other` |
| Arrêt entre deux appels ordonnés | La lecture repart comme un appel neuf (`resumed: false`) | `test_a_stop_between_two_ordered_calls_resumes_the_second_as_new` |
| Arrêt pendant l'écriture | Sûre à refaire : relancée (`resumed: true`), puis la lecture ; pas sûre : `UNKNOWN_STATE` au modèle, puis la lecture | `test_a_stop_during_the_write_resumes_it_before_the_read`, `test_a_write_of_unknown_effect_on_resume_is_settled_before_the_read` |
| Budget, tokens | Mêmes itérations, même usage, même coût, même première requête | `test_ordering_changes_neither_the_consumption_nor_the_model_requests` |
| Rejeu d'un journal d'avant | Identique, avec ou sans approbation | `test_a_journal_written_without_ordering_replays_identically` |
| Affichage en direct | Lignes dans le nouvel ordre | `test_the_live_display_follows_the_new_journal_order` |
| Comportement d'avant | `order: free` : tous les `tool.called` en tête, deux approbations dans la même pause | `test_free_order_keeps_the_previous_journal`, `test_without_ordering_two_writes_to_approve_are_asked_together`, `test_without_ordering_the_read_after_a_write_to_approve_runs_before_the_pause` |

**Le budget.** Il est vérifié avant chaque appel de l'orchestrateur (`loom.budget`), jamais pendant un lot : l'ordre ne change ni ce qui est compté, ni le moment du contrôle. Un sous-agent reçoit sa part (`budget_share`) calculée sur l'état du run **au début du lot** (`RunView`, `engine/subagents.py`, `_budget`) : avec deux sous-agents en série, le second reçoit la même part qu'aujourd'hui, sans tenir compte de ce que le premier vient de dépenser. Rien ne change donc, ni en mieux ni en pire (lu dans le code, non mesuré).

**La reprise est plus sûre qu'avant.** Aujourd'hui, un arrêt en plein lot laisse tous ses appels « lancés » : à la reprise, une écriture non idempotente qui n'avait pas encore commencé devient quand même « état inconnu ». Avec la barrière, un appel qui n'est pas parti n'a pas de `tool.called`.

**Une limite connue du prototype.** Si un sous-agent se met en pause ou qu'un état inconnu attend un humain, les appels qui le suivaient ont déjà passé leurs politiques `before_tool` (la préparation précède le lancement). Ils ne sont pas lancés, et à la reprise, leurs politiques tourneront une seconde fois : deux `policy.decided` pour le même appel, et le coût d'un juge payé deux fois. Le cas demande un sous-agent en pause **suivi** d'autres appels dans la même réponse.

### Modifications prévues

| Fichier | Changement | Estimation |
| --- | --- | --- |
| `engine/executor.py` | `order` sur `ToolExecutor` ; segments (`_segments`, `_writes`) ; appels retenus (`_Blocked`, `_always_asked`) ; lancement segment par segment, `tool.called` juste avant chaque segment ; arrêt de la chaîne sur `Waiting` et sur un état inconnu à trancher ; docstring | +180, −40 (fait) |
| `config/models.py` | `ToolsExecution.order` | +4 (fait) |
| `runtime/wiring.py` | `order=execution.order` | +1 (fait) ; +3 pour le réglage par agent (D-2) |
| `agents/spec.py` | Réglage par agent (D-2), et `side_effects` sur `SubAgentRef` (D-3 b) | ~15 |
| `engine/subagents.py` | La déclaration de la référence (D-3 b) | ~5 |
| `access/cli` (`loom validate`) | « ordre des appels : effets » dans les réglages de l'agent | ~5 |
| `tests/unit/test_call_order.py` (nouveau) | Les 29 tests ci-dessus | ~850 (fait) |
| `tests/unit/test_loop.py` | Une attente d'ordre du journal (reprise après un arrêt en plein lot) | 3 (fait) |
| `tests/unit/test_approval.py`, `test_call_assessment.py` | Rien : passent tels quels avec la demande groupée des approbations | 0 |
| `docs/conception.md` (§ lot parallèle, l. 119 et 513), `fonctions.md` (A3, l. 650), `configuration.md` | La règle, la clé, le nouvel ordre du journal | ~25 |
| `CHANGELOG.md` | L'entrée, marquée ⚠ | ~12 |

### Entrée proposée au CHANGELOG

Rien n'est écrit dans `CHANGELOG.md` tant que les décisions ne sont pas prises. Avec les recommandations, l'entrée serait :

```markdown
### Moteur : l'ordre des appels d'un même tour
- ⚠ **Un appel à effet attend ceux qui le précèdent dans la réponse du modèle, et ceux qui le suivent l'attendent** (`execution.tools.order: effects`, par défaut). Les appels sans effet qui se suivent partent toujours ensemble. Une écriture suivie d'une relecture du même fichier relit la nouvelle version ; avant, la relecture pouvait finir la première et rendre l'ancienne (POC shaper, sessions `01a1bc66…` et `test-100`). Un effet est ce que dit l'évaluation de l'appel (`assess_call`), sinon `side_effects`.
- ⚠ **Une approbation en attente retient les appels qui la suivent** : ils partent après la décision, dans l'ordre. Avant, une relecture placée après une écriture à approuver s'exécutait avant elle (B-1). Les appels qui exigent une approbation sont demandés dans la même pause.
- ⚠ **Le journal** : un `tool.called` s'écrit juste avant le lancement de son appel, plus tous en tête du lot. Un appel retenu n'a pas de `tool.called` : à la reprise, il part comme un appel neuf. Le schéma des événements ne change pas.
- ⚠ Deux sous-agents, deux appels de la forge ou deux outils d'un serveur MCP sans annotations, dans un même tour, s'exécutent l'un après l'autre. `order: free` (globalement, ou sur l'agent) rend le comportement d'avant.
- Essais : `tests/unit/test_call_order.py`. Analyse : `docs/moteur-ordre-des-appels-analyse.md`.
```

## Mesures

**Temps d'un lot** (médiane de 20 lots par cas, outils simulés qui dorment le temps indiqué ; `free` reproduit exactement le code d'avant) :

| Cas | Avant (`free`) | Après (`effects`) |
| --- | --- | --- |
| 3 lectures de 100 ms | 102 ms | 102 ms |
| Écriture 100 ms + lecture 100 ms | 101 ms | 202 ms |
| Cas shaper : écriture 60 ms + lecture 5 ms | 62 ms | 67 ms |
| 2 lectures, 1 écriture, 2 lectures (100 ms chacune) | 102 ms | 303 ms |
| 2 écritures de 100 ms | 101 ms | 202 ms |

**Sur tes journaux** (latences relevées, somme recalculée selon la règle) : 7 des 8 lots à plusieurs appels s'allongent de 0 à 12 ms. Le huitième (`01a1bc02…`, trois `wireframe`) passe de 748 ms à 2 143 ms. Un verrou par ressource (c) le garderait parallèle : trois images différentes.

**Tests** (le même jeu de commandes sur `020cc8f` et sur le prototype) :

| Passe | `020cc8f` | Prototype |
| --- | --- | --- |
| Tous les extras (`uv sync --all-extras`, sans services Postgres, Redis, RabbitMQ) | 4 439 réussis, 203 sautés, 1 échec | 4 468 réussis (+29), 203 sautés, 1 échec |
| Noyau seul (`uv sync --locked`, aucun extra) | 3 430 réussis, 416 sautés | 3 459 réussis (+29), 416 sautés |

L'échec est le même des deux côtés et ne tient pas à l'ordre des appels : `tests/firecracker/test_firecracker_forge.py::test_real_refusals_say_what_went_wrong`. Dans cet espace, la forge accepte un outil dont l'exemple lève `ValueError`, que le test attend refusé (à refaire sur ton poste). Les 29 tests de plus sont ceux de `test_call_order.py`, et ils passent aussi sans extra. Le seul test existant modifié est `test_loop.py::test_resume_after_a_crash_during_the_tool_batch` (variantes `memory` et `jsonl`) : il attendait les deux `tool.called` en tête du lot.

**Qualité** (prototype, tous les extras et le groupe `types`, comme la CI) : `pyright` strict, 0 erreur ; `ruff check .` et `ruff format --check .`, rien à redire (474 fichiers) ; `lint-imports`, 6 contrats tenus sur 6.

**Journaux** : les 19 journaux du POC (848 événements, 22 runs) se relisent sans erreur avec le prototype. Le schéma des événements ne change pas : mêmes types, mêmes champs, `schema_version` 1. Seul l'ordre change, et un lecteur d'avant lit un journal d'après (la projection accepte un `tool.called` à tout moment pour un appel en attente). Les 4 journaux de l'essai de course (écrits avant et après le correctif) se rejouent à l'identique avec le prototype (`loom replay --journal`).

## Bugs existants trouvés en route

- **B-1 — Avec une approbation en attente, l'appel qui la suit part toujours avant elle.** Ce n'est pas une course : c'est systématique. Le modèle écrit `ecrire` (à approuver) puis `lire` ; aujourd'hui `lire` s'exécute dans le lot, rend « version 1 », puis le run se met en pause, et `ecrire` ne partira qu'après la décision (démontré : `test_without_ordering_the_read_after_a_write_to_approve_runs_before_the_pause`). La barrière le corrige.
- **B-2 — Un outil exécuté dans un thread qui dépasse son délai écrit quand même, plus tard.** `asyncio.timeout` rend « Délai dépassé : pas de réponse en 0.1 s » au modèle, mais Python ne sait pas arrêter le thread (`asyncio.to_thread`, utilisé par les outils Python synchrones, `loom.word`, `loom.local_files`) : le fichier change 0,3 s plus tard (démontré par un essai : la lecture qui suit rend « version 1 », le fichier contient « version 2 » 0,4 s après). C'est déjà noté dans `docs/loom_testing.md` (l. 331, « Reste à noter »), mais deux conséquences n'y sont pas : le modèle croit qu'aucun effet n'a eu lieu, et la barrière ne protège pas la lecture qui suit (D-7).

## Effets de bord

- **Le journal** : un `tool.called` s'écrit juste avant le lancement de son segment, plus tous en tête du lot. La docstring de `executor.py` qui disait le contraire est réécrite. Une projection, un rapport ou une trace qui supposerait « tous les `tool.called` d'un lot, puis les `tool.completed` » se tromperait ; je n'en ai pas trouvé dans `src/` (`access/progress.py`, `access/evals.py`, `replay/runner.py`, `telemetry/spans.py` et `trace.py` apparient par `call_id` ; dans une trace, le span d'un appel retenu commence à son lancement, ce qui montre l'attente telle qu'elle est).
- **Les approbations** : un appel retenu qui n'exige pas d'approbation de lui-même ne passe ses politiques `before_tool` qu'à son lancement, après la décision. Une politique qui rend `Pause` sur lui fera donc une seconde pause (la demande groupée ne couvre que les approbations connues sans politique).
- **Une politique `after_tool` qui rend `Fail`** : le run échoue toujours à la fin du lot, mais les segments suivants partent encore entre-temps, comme aujourd'hui tous les appels étaient déjà partis (D-11).
- **Les sous-agents et la forge** : en série dans un même tour (D-3).
- **Les serveurs MCP sans annotations** : leurs outils sont tous `irreversible`, donc en série dans un même tour. Tes serveurs `loom-memory` et `loom-plan` sont à vérifier (§ À vérifier sur ton poste).
- **Les évaluations et le rejeu en variante** : inchangés. Un outil à effet y est doublé ou refusé avant tout lancement ; l'ordre s'applique aux doublures comme aux outils.
- **Le guide** (`howto/01-demarrer.md`, exemple 2.4) : ses trois outils sont sans effet, sa sortie ne change pas.
- **Les contrats d'import** : le moteur n'importe rien de nouveau ; la config ne dépend pas du moteur (la valeur `effects | free` est écrite des deux côtés).

## Plan de non-régression

- **`order: free` redonne le journal d'avant**, `tool.called` en tête (test), et la suite existante passe en `effects` à une attente d'ordre près (`test_loop.py`).
- **Rejeu** : le corpus `tests/regression/` passe dans la suite ; un journal écrit sans ordre se rejoue à l'identique avec l'ordre (test, avec et sans approbation).
- **Sans appel à effet, rien ne change** : trois lectures gardent leurs `tool.called` en tête et leur durée (test et mesure).
- **Le cas shaper** devient un test de bout en bout avec `loom.word` : écriture puis lecture du même `.docx`, la lecture rend le nouveau contenu.
- **Durées** : les tests d'ordre ne mesurent pas de durée absolue ; ils comparent l'ordre des débuts et des fins (journal de l'outil), pour ne pas dépendre de la charge de la machine.

## Décisions

| N° | Question | Proposition |
| --- | --- | --- |
| D-1 | Quelle option ? | (a) barrière sur les effets ; (b) même outil en série ; (c) verrou par ressource ; (d) un appel par réponse ; (e) consigne dans le prompt. **Je recommande (a)**, et (c) plus tard, par-dessus, pour les outils qui sauront nommer leurs ressources |
| D-2 | Défaut et réglage | (a) active par défaut, réglable globalement (`execution.tools.order`) et par agent ; (b) active par défaut, réglage global seulement (le prototype) ; (c) désactivée par défaut ; (d) toujours active, sans réglage. **Je recommande (a)**, ⚠ au CHANGELOG : un agent qui délègue à plusieurs sous-agents, ou qui appelle un serveur MCP sans annotations, peut revenir à `free` sans toucher aux autres |
| D-3 | Les sous-agents | (a) restent `irreversible`, donc en série ; (b) leur référence déclare `side_effects` (`irreversible` par défaut) ; (c) le moteur prend le pire des effets des outils de l'agent enfant. **Je recommande (b)** : explicite, et (c) ne connaît pas les outils MCP de l'enfant avant l'ouverture de ses sources |
| D-4 | Plusieurs approbations dans un tour | (a) toutes demandées dans la même pause, exécutées dans l'ordre après décision (le prototype) ; (b) l'une après l'autre, une pause par appel. **Je recommande (a)** |
| D-5 | `loom.ask` dans le même tour qu'une écriture | (a) ne retient rien (comme aujourd'hui) ; (b) retient les appels à effet qui la suivent. **Je recommande (a)** : retenue, l'écriture partirait après la réponse, mais avec les arguments écrits avant elle |
| D-6 | Outils Python sans `side_effects` | (a) rien ne change, la documentation le dit ; (b) `loom validate` signale un outil Python qui ne déclare pas ses effets ; (c) `reversible` par défaut. **Je recommande (b)**. (c) changerait aussi l'approbation par politique, la reprise et les évaluations (un outil à effet y est refusé) |
| D-7 | B-2 (thread après délai) | (a) une analyse à part ; (b) dans ce lot : un délai dépassé par un outil en thread devient un état inconnu (`on_unknown`), et la chaîne attend la fin réelle du thread. **Je recommande (a)** : le sujet touche tous les outils, avec ou sans ordre |
| D-8 | Nom de la clé | `order: effects \| free` (le prototype) ; `parallel: ordered \| free` ; `sequential_effects: true`. **Je recommande `order`**, avec des valeurs qui disent sur quoi l'ordre porte |
| D-9 | La version | 2.0.20 (l'actuelle, non publiée) ou 2.1.0 (le comportement par défaut du moteur change). À toi de voir ; les lots précédents sont restés en 2.0.20 avec des lignes ⚠ |
| D-10 | Le moment du `tool.called` | (a) juste avant le lancement de son segment (le prototype) ; (b) tous en tête du lot, comme avant. **Je recommande (a)** : avec (b), un appel retenu puis jamais lancé passerait pour lancé, et deviendrait « état inconnu » à la reprise |
| D-11 | Une politique `after_tool` qui rend `Fail` au milieu d'une chaîne | (a) les appels suivants partent quand même, et le run échoue à la fin du lot (comme aujourd'hui, et le prototype) ; (b) la chaîne s'arrête : aucun effet de plus dans un run qui va échouer (~5 lignes : le lot voit passer le `policy.decided` `fail`). **Je recommande (b)** |

## À vérifier sur ton poste

1. **Le POC avec Haiku** : relancer `maquettes-test` sur un document qui existe déjà. Au journal, le `tool.called` de `word read` doit venir après le `tool.completed` de l'écriture, et la relecture rendre les blocs écrits.
2. **Trois maquettes dans un tour** : le temps du tour de `shaper` qui dessine plusieurs écrans s'allonge d'environ 0,6 s par maquette. Est-ce acceptable, ou faut-il `order: free` sur cet agent en attendant (c) ?
3. **`loom run --stream`** sur ce même agent : l'affichage alterne appel et « fait ».
4. **Tes serveurs MCP** (`loom-memory`, `loom-plan`) : leurs outils annoncent-ils `readOnlyHint` ? Sans annotation, leurs lectures d'un même tour passeraient l'une après l'autre.
5. **`test_real_refusals_say_what_went_wrong`** (forge Firecracker), qui échoue dans l'espace cloud sur `020cc8f` comme sur le prototype : passe-t-il chez toi ?
6. **Un client REST (SSE) ou MCP** qui supposerait tous les `tool.called` avant les résultats : je n'en connais pas dans le dépôt, mais tes interfaces à toi peuvent le faire.

## Mise en œuvre

### 9 novembre 2026 : le lot entier

Appliqué dans `/home/denis/dev/loom_v2`, sur `020cc8f`, sans commit. Le prototype est repris tel quel, avec les décisions :

| Décision | Ce qui est fait | Où |
| --- | --- | --- |
| D-1, D-4, D-10 | La barrière du prototype : segments, `tool.called` juste avant chaque segment, approbations retenues demandées dans la même pause | `engine/executor.py` |
| D-2 | `execution.tools.order` dans `loom.yaml` (`effects` par défaut) et dans le fichier de l'agent, qui le remplace pour lui seul (`AgentExecution`, seule clé permise : `order`) ; `loom validate` dit l'écart dans l'en-tête de l'agent : « appels d'un tour tous ensemble (order: free) » | `config/models.py`, `agents/spec.py`, `runtime/wiring.py`, `access/cli/main.py` |
| D-3 | `side_effects` sur la référence d'un sous-agent (`irreversible` par défaut), recopié sur `SubAgentDefinition` puis sur la déclaration de l'outil. Les évaluations et le rejeu en variante choisissent le sort d'un sous-agent par sa nature (`kind: agent`), pas par ses effets : ils ne changent pas | `agents/spec.py`, `engine/subagents.py`, `runtime/wiring.py` |
| D-5, D-7 | Rien : `loom.ask` ne retient rien ; B-2 attend son analyse | — |
| D-6 | `@tool` et `FunctionTool` reçoivent `side_effects=None` par défaut et retiennent s'il a été donné (`declares_effects`) ; la déclaration de l'outil reste `none`. `loom validate` avertit pour un outil Python qui ne déclare rien, ni dans `@tool` ni sur sa référence : « ⚠ Agent demo   outil lire : side_effects non déclaré, compté comme sans effet (none s'il ne fait que lire) ». Les outils fournis (`loom.*`) et les classes d'outil, qui écrivent leur `ToolSpec`, ne sont pas concernés | `tools/python.py`, `access/cli/main.py` |
| D-8 | La clé s'appelle `order` | — |
| D-9 | Entrée dans la section 2.0.20 du CHANGELOG, cinq lignes ⚠ | `CHANGELOG.md` |
| D-11 | Le lot voit passer le `policy.decided` `fail` d'une politique `after_tool` et ne lance plus de segment | `engine/executor.py` |

**Documentation** : `configuration.md` (la clé de `loom.yaml`, la rubrique `execution` de l'agent, `side_effects` d'un outil Python et d'un sous-agent), `conception.md` (§ lot parallèle), `fonctions.md` (A3, approbations dans un lot), docstrings de `executor.py` et de `tools/python.py`.

**Essais** : `tests/unit/test_call_order.py`, 42 tests (les 29 du prototype, plus D-11 avec et sans ordre, le réglage de l'agent contre celui de la racine sur 4 combinaisons, le refus d'une autre clé d'exécution sur l'agent, la déclaration d'un sous-agent montée par `Loom`, deux sous-agents sans effet ensemble, l'avertissement de `loom validate` sur 3 déclarations, la déclaration portée par la référence). Essais existants modifiés :

- `test_loop.py::test_resume_after_a_crash_during_the_tool_batch` : le nouvel ordre du journal ;
- `test_loop.py::test_a_policy_failure_closes_the_subrun_waiting_for_an_approval` et `test_a_failing_subrun_closes_its_own_open_subruns` : ils lancent ensemble un outil dont le résultat fait échouer le run et un sous-agent qui se met en pause, pour éprouver la clôture des sous-runs. Avec D-11, l'échec arrête la chaîne avant le sous-agent : ils passent en `order: free`, ce qui garde ce qu'ils éprouvent ;
- `test_cli.py::test_validate_puts_the_verdict_first_and_the_warnings_last` : un avertissement de plus (D-6), son outil `calculer` ne déclarant pas ses effets.

**Mesures** (mêmes commandes que pour l'analyse) :

| Passe | `020cc8f` | Lot |
| --- | --- | --- |
| Tous les extras | 4 439 réussis, 203 sautés, 1 échec | 4 481 réussis (+42), 203 sautés, 1 échec (le même essai de la forge Firecracker) |
| Noyau seul | 3 430 réussis, 416 sautés | 3 472 réussis (+42), 416 sautés |

`pyright` strict : 0 erreur ; `ruff check .` et `ruff format --check .` : rien à redire ; `lint-imports` : 6 contrats tenus sur 6.

**Reste à faire, hors de ce lot** :

- **Les sorties du guide** (`docs/howto/`) : les `loom validate` qui y sont recopiés montreront un avertissement de plus pour chaque outil Python sans `side_effects` (la plupart des exemples). Elles ne sont pas réécrites ; ajouter `side_effects="none"` aux outils de lecture des exemples, ou refaire les sorties, au prochain passage sur le guide.
- **Les outils du POC** : ton `wireframe` déclare ses effets ; vérifie les autres avec `loom validate`.
- **B-2** (D-7) et le verrou par ressource (option c) : à analyser à part.

