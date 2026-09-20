# loom-ia V2 — Backlog

Points à traiter, numérotés dans l'ordre d'ajout (#001, #002…). Les renvois `#n` sans zéros pointent vers les points de conception de `fonctions.md`.

---

## #001 — Configurer le contenu écrit dans le journal

**Origine :** relecture d'un événement `run.completed` pendant la phase 1.2.

**Constat :** le JSON du journal est verbeux. Les valeurs par défaut (`cache_breakpoint: false`, `provider_meta: {}`, tokens à 0…) sont écrites sur chaque ligne.

**Contrainte :** le journal fait foi (#22) et contient toujours les contenus complets (#30). On peut configurer la forme et ce qui s'ajoute au journal, pas retirer ce dont la reprise, l'historique et le rejeu ont besoin.

**Réglages envisagés :**

| Réglage | Effet | Existe déjà ? |
|---|---|---|
| Valeurs par défaut omises | Lignes plus courtes, relecture identique | Non, nouveau |
| Compression (par exemple gzip des fichiers JSONL clos) | Moins d'espace disque | Non, nouveau |
| Seuil de déport (`offload_over`) | Les gros résultats vont dans le stockage d'artefacts ; le journal garde une référence | Oui (#16, réalisé en 2.3) |
| Échanges HTTP bruts (`raw_exchanges`) | Ajoute les requêtes et réponses complètes des fournisseurs, pour le débogage | Oui, désactivé par défaut (#31) |
| Chiffrement par client | Contenus illisibles sans la clé | Oui, en option (#30) |
| Rétention (`retention.events_days`) | Suppression des journaux anciens | Oui (#50) |

**Non désactivable :** contenus des messages et des résultats d'outils, `step.*`, `run.transitioned`, décisions des politiques. Les événements éphémères (`model.delta`) ne sont jamais écrits : c'est une règle, pas un réglage.

**Config envisagée :**

```yaml
storage:
  events:
    backend: jsonl
    path: data/events
    serialization: {omit_defaults: true, compress_closed: false}
```

**Statut :** à traiter plus tard.

---

## #002 — Emplacement de la règle sur les dépendances du noyau

**Origine :** point #44.

**Constat :** la règle « toute nouvelle dépendance du noyau est justifiée par écrit » n'a pas d'emplacement.

**Options :** une section de `docs/conception.md` (§19), un `CONTRIBUTING.md`, ou un fichier dédié dans `docs/`.

**Statut :** à décider.

---

## #003 — Mettre à jour les mentions « jalons à revoir »

**Constat :** `docs/fonctions.md` (point #47, partie « Ouverts ») et `docs/conception.md` (§20) indiquent encore « jalons à revoir », alors que `docs/jalons.md` existe.

**À faire :** passer #47 en décidé dans `fonctions.md` avec un renvoi vers `jalons.md`, et retirer la ligne correspondante du §20 de `conception.md`.

**Statut :** à faire.

---

## #004 — Retirer les mentions de respx dans les docs

**Origine :** phase 1.4.

**Constat :** les SDK `anthropic` (1.6) et `openai` (3.14) utilisent `httpx2`, que respx n'intercepte pas. Les tests de contrat des adaptateurs simulent donc les réponses avec `httpx2.MockTransport`, passé au SDK par `http_client`. respx a été retiré des dépendances de dev.

**À faire :** remplacer « respx » par `httpx2.MockTransport` dans `docs/fonctions.md` (#9 et outils de dev), `docs/conception.md` (§10.1 et §19) et `docs/jalons.md` (principes et tests du J1).

**Statut :** à faire.

---

## #005 — `model.retried` écrit dès la phase 1.4

**Origine :** phase 1.4.

**Constat :** `docs/jalons.md` place `model.retried` en phase 3.5 ; il est écrit dès la 1.4, avec le retry.

**À faire :** retirer `model.retried` de la ligne 3.5 et le mentionner en 1.4 (`fell_back` reste en 3.5).

**Statut :** à faire.

---

## #006 — `timeout` d'agent : nommer la phase qui l'apportera

**Origine :** phase 1.6.

**Constat :** la clé `timeout` d'un agent est refusée avec le message « J1.6 (cycle de vie des runs) » (`agents/spec.py`, `LATER_AGENT`). Or la ligne 1.6 de `docs/jalons.md` ne couvre que les trois accès : délai maximal d'un run, annulation et expiration ne sont décrits nulle part.

**À décider :** où atterrit le cycle de vie d'un run (délai, annulation, `run.cancelled`) — J3 avec les politiques et les budgets, ou J4 avec les sessions et le bus. Le message de refus suivra.

**Décision (phase 2.4) :** J4.2, qui prévoit déjà l'annulation et le timeout global. En 2.4, l'annulation d'un parent se propage à ses sous-agents par asyncio (même arbre de tâches), sans rien écrire : les runs restent reprenables. `run.cancelled`, le délai d'un agent et l'API `cancel` arrivent en J4.2. Le message de refus de `timeout` nomme désormais J4.2.

**Statut :** tranché, à faire en J4.2.

---

## #007 — Nom de l'exemple des trois accès

**Origine :** phase 1.6.

**Constat :** `docs/jalons.md` annonce `examples/j1/run.py` pour l'accès Python ; l'exemple livré est `examples/j1/acces.py`, et il montre les trois accès plutôt que le seul accès Python (`run.py` se confondrait d'ailleurs avec la commande `loom run`).

**À faire :** renommer le fichier, ou corriger la ligne du J1 dans `jalons.md`.

**Statut :** fait en 2.5 : les lignes Python du J1 et du J2 nomment `acces.py` (`examples/j2/acces.py` montre les trois accès avec une image). J4 et J5 annoncent encore `run.py` : le nom sera choisi avec chaque exemple. J3 : un exemple par phase (`politiques.py`, `contrats.py`, `juge.py`, `budget.py`, `secours.py`, `acces.py`), décidé au début du jalon.

---

## #008 — `policy.decided` pour un outil terminal appelé en parallèle

**Origine :** phase 2.1.

**Constat :** #13 prévoit que `policy.decided` signale un outil terminal appelé avec d'autres outils. Cet événement n'existera qu'avec les hooks (3.1), et son vocabulaire (`Continue`, `Deny`…) ne décrit pas ce cas. En 2.1, le moteur écrit un avertissement dans les logs (`engine/loop.py`, `_is_terminal`).

**À faire en 3.1 :** écrire l'événement à cet endroit, avec une décision adaptée.

**Réalisation (3.1) :** le moteur écrit un `policy.decided` pour chaque outil terminal appelé avec d'autres outils : règle du moteur `loom.terminal` (préfixe `loom.` réservé), point `after_tool`, décision `continue`, statut `warning`, avec le `call_id` de l'appel et le motif. L'avertissement dans les logs reste.

**Statut :** fait en 3.1.

---

## #009 — Diffusion en direct de la sortie d'un rôle terminal

**Origine :** phase 2.1.

**Constat :** seuls les morceaux du modèle `main` partent vers `on_chunk`. La sortie d'un rôle terminal n'apparaît donc pas dans `Loom.stream()`. Elle apparaît dans le `tool.completed` terminal (SSE), dans le résultat du run (REST, MCP) et, pour `loom run --stream`, elle est affichée à la fin du run.

**À faire :** avec `stream_output` (3.2), diffuser les morceaux du rôle terminal en `live`, attribués au rôle (`model.delta` du bus en J4).

**Réalisation (3.2) :** en `live`, un rôle terminal seul dans son lot envoie ses morceaux à `on_chunk` (`RunView.on_chunk`), réparations comprises, précédées d'un `StreamReset`. En `after_guards`, sa sortie part une fois le run clos, comme une réponse de l'orchestrateur. `loom run --stream` n'affiche donc plus la sortie terminale à la fin du run. L'attribution des morceaux au rôle reste pour le bus (`model.delta`, J4).

**Statut :** fait en 3.2.

---

## #010 — Budget sur un agent dont un modèle n'a pas de tarif

**Origine :** exemple de la phase 2.1 (`relance_reel`), dont les modèles n'avaient pas de `pricing` : coût du run à 0 $.

**Constat :** un modèle sans `pricing` coûte 0 $. Un budget en dollars (3.4) ne se déclenche donc jamais pour lui, sans que rien ne le signale.

**À faire en 3.4 :** contrôle au démarrage quand un budget s'applique à un agent dont un modèle (`main`, rôle, juge, secours) n'a pas de tarif : avertissement en profil dev, erreur en profil prod. Les tarifs par palier (MiniMax-M3 double ses prix au-delà de 512k tokens d'entrée) sont une question voisine, à trancher au même moment.

**Réalisation (3.4) :** au montage d'un agent dont le budget a une limite en dollars (run ou session), un avertissement (logs) nomme ses modèles sans tarif (`main`, rôles, juges ; les secours s'y ajouteront en 3.5) : « budget en dollars, mais sans tarif pour … : leurs appels comptent 0 $ ». L'erreur en profil prod arrivera avec les profils (J5). Un budget en tokens (`max_tokens`) reste efficace sans tarif. Paliers réalisés : `pricing.tiers` ; la config de l'exemple J3 déclare celui de MiniMax-M3.

**Statut :** fait en 3.4 (erreur en profil prod : J5).

---

## #011 — Disjoncteur des serveurs MCP

**Origine :** phase 2.2.

**Constat :** en 2.2, un serveur MCP en échec est seulement réessayé avec backoff (1, 2, 5, 10 puis 30 s). Le disjoncteur prévu en #19 (serveur écarté pendant T secondes après N échecs, pour tous les runs) n'est pas réalisé.

**À faire en 3.5 :** un mécanisme commun aux modèles et aux serveurs MCP, avec son événement.

**Réalisation (3.5a) :** `circuit_breaker: {failures: 5, cooldown: 60}` par défaut sur chaque modèle et chaque serveur MCP (`null` le retire), disjoncteurs communs aux runs d'une instance `Loom` (`engine/circuit.py`). Côté MCP, une connexion ratée compte un échec, un refus pendant le backoff du serveur (`SourceUnavailable(attempted=False)`) non ; ouvert, le serveur est déclaré indisponible sans nouvel essai. Événement `circuit.opened` (catégorie `circuit`), écrit dans le run dont l'échec l'a ouvert, avant le `tool.source_unavailable`.

**Statut :** fait en 3.5a.

---

## #012 — Forcer un appel d'outil (`tool_choice: required`)

**Origine :** exemple de la phase 2.2 (`assistant_reel`). MiniMax-M3 a répondu sans appeler aucun outil, en inventant l'heure et un calcul. En essai direct, la même requête a donné 8 appels d'outils sur 9 : le modèle n'est pas régulier, et rien ne l'oblige à appeler un outil.

**Constat :** `ModelRequest.tool_choice` ne connaît que `auto` et `none`. Les fournisseurs savent imposer un appel d'outil (`{"type": "any"}` chez Anthropic, `"required"` chez OpenAI).

**À trancher en 3.1 :** une valeur `required` dans le domaine et les adaptateurs, et qui la pose : un réglage de l'agent (premier tour seulement ?) ou un hook `before_model` (décision `Replace` sur la requête). Garde-fou : jamais en `FINALIZING`, où `tool_choice` reste `none`.

**Décision et réalisation (3.1) :** une politique fournie, `loom.require_tool` (`before_model`, `Replace`) : `tool_choice: required` tant que le run n'a appelé aucun outil ; sans effet pendant la réponse forcée, sans outils proposés, ou pendant une réparation sans outils. `ToolChoice` gagne `required` (`{"type": "any"}` chez Anthropic, `"required"` chez OpenAI, respecté par le modèle `fake`). Le moteur remet `none` en `FINALIZING`, quoi qu'une politique demande. Vérifié en run réel le 20/09 : MiniMax-M3 (API compatible Anthropic) accepte `{"type": "any"}` et appelle un outil.

**Statut :** fait en 3.1.

---

## #013 — Pièces jointes autres que les images (PDF, audio, fichiers)

**Origine :** phase 2.3 (choix « images seules » pour le jalon J2).

**Constat :** G1 prévoit en V2 les PDF, l'audio et les fichiers quelconques. En 2.3, seules les images JPEG, PNG, GIF et WebP sont acceptées à l'entrée d'un run ; un fichier produit par un outil (PDF d'un serveur MCP…) est bien rangé comme artefact, mais un modèle n'en reçoit qu'une mention.

**À faire :** signatures binaires des nouveaux types, capacités des modèles (blocs `document` chez Anthropic, audio chez OpenAI), traduction dans les adaptateurs, contexte des rôles qui les reçoivent. Phase à fixer (aucune ne le prévoit dans `jalons.md`).

**Statut :** à placer.

---

## #014 — Images envoyées par URL signée ou `file_id`

**Origine :** phase 2.3.

**Constat :** #14 prévoit trois formes d'envoi (`image_input: base64 | url | file_id`). Seul le base64 est réalisé : `image_input` doit le contenir, sinon la config est refusée. L'URL signée à courte durée de vie suppose un stockage distant (GCS) et une autorisation explicite dans la config (RGPD) ; `file_id` suppose l'envoi préalable du fichier au fournisseur.

**À faire en J5 :** avec le stockage GCS du mode service.

**Statut :** à faire en J5.

---

## #015 — gpt-oss appelé avec des outils via l'API `chat` (Together)

**Origine :** exemple de la phase 2.4 (`sous_agent.py --reel`, sous-agent `verificateur_reel` sur `openai/gpt-oss-120b`).

**Constat :** avec des outils, gpt-oss continue d'écrire après un appel d'outil au lieu d'attendre son résultat : appels en double, sans identifiant fourni par le fournisseur, puis texte au format interne (« analysis… commentary to=functions… ») rendu comme réponse finale. L'adaptateur `openai` ne renvoie jamais le raisonnement du modèle ; pour gpt-oss, OpenAI recommande de renvoyer le champ `reasoning` des tours précédents pendant les appels d'outils. Le sous-agent de l'exemple est passé sur GLM-5.3-Flash ; la définition `GPT_OSS` reste dans `loom.yaml`, sans usage.

**À faire en 3.5 :** avec le raisonnement (#7), renvoyer le raisonnement des tours d'outils aux modèles qui l'attendent, puis refaire l'essai avec gpt-oss.

**Avancement (3.5a) :** chaque bloc de raisonnement porte désormais le modèle qui l'a produit (`model_id`, posé par le moteur pour tous les adaptateurs). Le renvoi du raisonnement et le nouvel essai avec gpt-oss restent pour 3.5b.

**Réalisation (3.5b) :** avec l'API Chat, un modèle déclaré `capabilities.thinking: true` reçoit le raisonnement de sa boucle d'outils en cours (les réponses qui suivent sa dernière réponse sans appel d'outil), dans le champ où le fournisseur l'a donné (`reasoning` chez Together) ; sans `thinking`, rien n'est renvoyé.

**Essais réels (3.5b, 20/09, `sous_agent.py --reel` avec `verificateur_reel` sur gpt-oss-120b chez Together, `thinking: true`) :** le renvoi fonctionne (champ `reasoning` accepté), mais ne règle rien, car le défaut est dans le service de Together :

- dès sa première réponse, sans historique à renvoyer, gpt-oss continue d'écrire après un appel d'outil comme si le résultat était arrivé : 8 appels dans la même réponse (4 fois `time__maintenant`, un `2+2`), puis une conclusion inventée ;
- avec `params: {parallel_tool_calls: false}`, Together ne garde que le premier appel, mais le modèle continue de même et invente la conclusion (« 2026-09-20 12:00:00 ») ;
- au tour suivant, raisonnement renvoyé, il écrit son format interne en texte (« analysis… assistantcommentary to=functions.time__jours_entre json{…} »), rendu comme réponse finale du sous-agent.

L'orchestrateur (MiniMax-M3) a chaque fois vu la réponse inutilisable et refait le calcul lui-même : réponse finale juste. L'exemple garde GLM-5.3-Flash sur le sous-agent.

**Pistes :** un autre fournisseur de gpt-oss (vLLM, Groq, OpenRouter), ou l'API Responses si Together la propose pour ce modèle ; un contrat de sortie sur l'agent appelé, qui refuserait le format interne.

**Statut :** renvoi du raisonnement fait en 3.5b ; gpt-oss chez Together toujours inutilisable avec des outils, ouvert.

---

## #016 — Commande qui crée un agent par questions

**Origine :** après la phase 2.5 (accès MCP) : brancher une nouvelle tâche loom sur Claude Desktop demande d'écrire à la main toute la structure d'un projet.

**Constat :** la CLI (N4) sait lancer, reprendre, servir et valider, mais pas créer. Un projet loom demande `loom.yaml` (modèles, stockage JSONL, `imports`, `server.mcp.file_roots` au besoin), `agents/<agent>.yaml` (nom, description lue par le client MCP, `expose`, modèle, outils, `max_iterations`), `prompts/<agent>.md`, éventuellement `outils.py` et les dossiers `serveurs/` et `data/`, les noms des variables de clés, puis l'entrée de configuration du client MCP (commande `uv run … loom … mcp` en chemins absolus).

**À faire :** une commande de la CLI qui pose les questions (projet, agent, description, modèles, outils Python ou serveurs MCP, accès publiés, dossiers autorisés) et crée toute la structure, validée ensuite comme par `loom validate`.

**À trancher :** nom de la commande ; projet neuf seulement ou ajout d'un agent à un projet existant ; création d'un projet uv dépendant de loom ou usage de l'environnement du dépôt ; entrée du client MCP affichée ou écrite ; mode sans questions (options en ligne de commande) pour les scripts. Phase à fixer (aucune ne le prévoit dans `jalons.md`).

**Statut :** à placer.

---

## #017 — Le journal d'une session est relu en entier à chaque run

**Origine :** phase 4.1a (snapshots d'historique).

**Constat :** le snapshot (#22) supprime le **rejeu** de la session — la projection de tous ses runs et la reconstruction des messages — mais pas la **lecture** : `drive` appelle toujours `store.read(tenant, session)` sans position de départ, puis l'historique repart du dernier marqueur. Sur une longue conversation, l'entrée-sortie et la désérialisation continuent donc de grandir avec la session. Même remarque pour le rapport et les verdicts d'un résultat, déjà notés en 3.6.

**Pourquoi ce n'est pas bloquant :** le coût qui explosait était celui du rejeu (une projection par run, des `model_copy` en cascade) ; la lecture d'un fichier JSONL ou d'une table indexée est linéaire et bien plus légère. La compaction (4.1b) borne par ailleurs la taille de l'historique, pas celle du journal.

**Pistes :**

| Piste | Effet | Coût |
|---|---|---|
| Position du dernier marqueur tenue par l'écrivain de session | Une seule lecture `after_seq` dans le process qui vient d'écrire | Ne sert à rien au premier run d'un process ou d'un worker |
| Opération de port « dernier marqueur d'une session » | Lecture `after_seq` dans tous les cas | Une méthode de plus à écrire dans chaque adaptateur |
| `EventQuery` qui sait rendre les derniers événements d'un type | Même effet, sans nouvelle méthode | Change un modèle du noyau pour un seul usage |

**À trancher :** faut-il le faire avant que les journaux ne grossissent (J5, Postgres et multi-workers), ou attendre une mesure sur une vraie session longue ?

**Statut :** à traiter plus tard.
