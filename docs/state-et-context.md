# State de session et contexte de l'orchestrateur

> Statut : **réalisé en 2.0.6**. Ce document décrit ce que fait loom. La proposition d'origine — un événement `state.set` et des `writers` — a été remplacée par l'**option C** après l'analyse du 14/10 (`state-et-context-analyse.md`), qui garde le raisonnement, les preuves et le plan de non-régression. Voir aussi `fonctions.md` (F8, point 51), `conception.md` (§9.5, §9.6, §11.2, §14.3, §17.4, §18), l'exemple `examples/j7/` et le guide `howto/05-state-de-session.md`.

## 1. Le problème

Ce que loom garde d'une session :

| Quoi | Où | Survit à la compaction ? |
|---|---|---|
| Journal d'événements typés | `data/<client>/<session>.jsonl` | Oui, il n'est **jamais réécrit** |
| Historique (messages) | projection `history()` | Remplacé par un résumé en prose |
| Résultats d'outils structurés | dans `tool.completed` | **Plus dans l'historique** après compaction (seul le journal les garde) |
| Faits métier | base de l'application | Oui, hors loom |

Il manquait deux choses, une par côté de la requête :

1. **Un state de session** : quelques valeurs **typées, petites, durables**, que l'application ou un outil pose (« client courant = C-002 ») et que tout le reste relit, sans dépendre de ce que le modèle a gardé de la conversation ni de la qualité d'un résumé.
2. **Un contexte déclaratif pour l'orchestrateur** : seuls les rôles déclaraient ce qu'ils reçoivent (`context:`). `main` recevait « système + historique + messages du run », rien d'autre.

Le **state** est ce que le système *sait* ; le **contexte** est ce que le modèle en *voit* à un appel donné.

## 2. Le principe

- **Le journal fait foi.** Le state est une projection pure du journal (`session_state`), recalculée sur tout le journal : la reprise après plantage, le rejeu et les évals le retrouvent sans rien de plus.
- **Aucun type d'événement nouveau** (D1). Le state voyage dans deux champs d'événements existants, omis quand ils sont vides :
  - `run.started.state` : ce que l'application pose à l'ouverture d'un run ;
  - la sortie de `tool.completed` (`ToolOutput.state`) : ce que pose le résultat réussi d'un outil.

  Un journal sans state s'écrit exactement comme avant ; un loom 2.0.5 relit un journal qui en porte (un champ ajouté est ignoré, un type d'événement inconnu ne le serait pas) ; le rejeu ressert le state avec le résultat de l'outil ; écrit avec son `tool.completed`, il ne peut pas en être séparé par un plantage.
- **Des clés déclarées** (D6) : chacune a un JSON Schema, contrôlé au chargement. Un outil ou l'application ne posent jamais une clé que l'agent ne déclare pas.
- **Rien de métier dans loom** : « client courant » est une clé que l'application déclare ; loom ne connaît que des clés, des schémas et des valeurs. Les données métier restent dans la base de l'application : le state n'en porte que les repères.

## 3. Déclarer

Dans l'agent (`agents/*.yaml`), l'exemple de `examples/j7/relance/` :

```yaml
main:
  model: FAKE_MAIN
  system_file: relance.md
  context: [session_state]          # le state à la fin de chaque requête

state:
  signature:
    schema: {type: string, minLength: 1, maxLength: 80}
    description: Nom de l'entreprise, au bas de chaque e-mail.
    required: true                  # run refusé tant qu'elle n'a pas de valeur
  ton:
    schema: {enum: [cordial, ferme]}
    description: Ton des relances, choisi par l'artisan.
    default: cordial                # lue quand la clé manque, jamais écrite
  devis_courant:
    schema: {type: string, pattern: "^D-[0-9]{4}-[0-9]{3}$"}
    description: Numéro du devis dont parle la conversation.
    from: [{tool: chercher_devis, path: /numero}]
  client_courant:
    schema: {type: string}
    description: Client du devis dont parle la conversation.
    from: [{tool: chercher_devis, path: /client}]
```

Règles contrôlées au chargement :

- **Nom** : un identifiant (lettres, chiffres, `_`, 64 caractères, pas de chiffre en tête), que les templates citent tel quel.
- **`schema`** : un JSON Schema valide, appliqué avec le moteur borné des contrats de sortie (un motif qui ne finit pas est arrêté).
- **`description`** : ce que la clé désigne, donné avec sa valeur à qui la reçoit.
- **`from`** : les outils dont un résultat réussi pose la clé (D12), chacun cité une fois, avec un `path` (pointeur JSON, RFC 6901, dans les **données** du résultat ; `""` les prend entières) ou une `value` fixe (`null` efface la clé). L'outil est un outil de l'agent : Python, rôle, sous-agent, ou un préfixe MCP ou de source (connu seulement au début du run). `path` exige des données : il est refusé pour un outil Python dont la signature annonce `str`, `None` ou `Image` et qui n'a pas de contrat de sortie à schéma, pour un rôle sans contrat de sortie à schéma, et pour un sous-agent. Avec un `path`, `on_missing` dit ce que fait un résultat où le chemin ne mène à rien : `refuse` (défaut) en fait une écriture refusée (§4.3), signe qu'un outil dédié a changé la forme de ses données ; `ignore` ne pose rien et ne dit rien au modèle, pour un outil générique — une requête SQL, par exemple — dont la plupart des résultats ne parlent pas de la clé : `{tool: requete_sql, path: /rows/0/code, on_missing: ignore}`.
- **`required`** et **`default`** s'excluent (D13). `default` et chaque `value` doivent tenir le schéma et les limites.
- **Limites** (D7), à la racine : `sessions.state.max_value_bytes` (1 024 par défaut) et `max_total_bytes` (4 096), en octets de JSON compact. Les valeurs par défaut d'un agent doivent tenir ensemble dans la seconde.
- **`main.context`** n'accepte que `session_state` (la demande, l'historique et les pièces jointes sont déjà dans les messages), entier ou limité à des clés déclarées : `[{session_state: [devis_courant, ton]}]`.

## 4. Écrire

### 4.1 L'application

`Loom.run(agent, message, state={…})`, de même `stream` et `submit` ; en ligne de commande, `loom run --state CLE=VALEUR`, répétable (la valeur se lit en JSON — `3`, `true`, `null` qui efface, `"texte"` —, sinon elle est prise telle quelle). REST et MCP ne posent pas de state dans cette version (D5).

Avant d'ouvrir le run, loom contrôle chaque écriture (clé déclarée, schéma, taille), puis vérifie que chaque clé `required` a une valeur dans le state qui en résulte, celui des runs précédents compris. Un refus lève `StateRefused` (une `ValueError` ; `problems` porte un motif par clé) **avant tout écrit**, y compris avant la compaction de sécurité d'une session trop longue : le journal ne garde aucune trace d'une demande refusée. La CLI dit « Demande refusée : State refusé : signature : clé obligatoire sans valeur » et rend le code 2 ; REST rend 422 (lancement d'un run, déclencheur) ; en MCP, l'outil de l'agent rend un résultat d'erreur (`isError`) dont le texte est ce refus, sans résultat structuré. Un agent qui ne déclare pas de state refuse tout state posé ; un sous-run n'en reçoit jamais de l'application.

Ce qui est posé va dans le `run.started` du run, avant la demande.

### 4.2 Un outil

La valeur se déclare dans la config, l'outil n'a pas à changer : `chercher_devis` rend le devis entier, et `from: [{tool: chercher_devis, path: /numero}]` en tire le numéro. Un outil peut aussi écrire lui-même — `ToolOutput(..., state={"cle": valeur})`, pour une règle qui dépend du contexte — ; sur une même clé, sa valeur l'emporte sur celle de `from` (D12).

Les écritures se posent sur le résultat **réussi** d'un appel, après les politiques `after_tool` (un résultat remplacé pose d'après le nouveau ; refusé, il ne pose rien) et **avant** ses fichiers et son déport : un résultat déporté garde son state. Un résultat en erreur ne pose rien. Les écritures sont rangées dans `tool.completed`, mais retirées du résultat que lit le modèle.

### 4.3 Contrôles et refus (D6, D7, D8)

Chaque écriture d'un outil est contrôlée : clé déclarée, valeur finie (ni NaN ni infini, que le JSON du journal écrirait `null`), schéma, taille de la valeur, puis taille du state de la session, comptée sur le lot dans l'ordre des appels : chaque appel est jugé sur ce que la session gardera, voisins déjà soldés compris (à la reprise d'un lot interrompu aussi), quel que soit l'ordre où les résultats arrivent. Une écriture refusée n'est pas posée, les autres le sont — sauf si l'appel faisait dépasser la taille totale : toutes ses écritures qui ajoutent sont alors refusées, hors celles qu'un appel suivant du lot remplace (D9) —, et **le résultat reste un succès** : l'outil a pu produire son effet, et une erreur pousserait le modèle à le refaire. Le refus est gardé dans la sortie (`state_refused`), dit au log, et le modèle le lit à la suite du résultat :

```text
[state] devis_courant non posé : ne suit pas son schéma — ne respecte pas le motif ^D-[0-9]{4}-[0-9]{3}$
```

Le motif nomme la clé et le problème, sans recopier la valeur, qui peut porter un secret.

Un chemin absent d'une source en `on_missing: ignore` n'est pas un refus : rien n'est posé, le modèle ne lit rien, et le log n'en a qu'un message de débogage. Le chemin présent, la valeur suit les mêmes contrôles qu'ailleurs.

### 4.4 Deux écritures d'une même clé dans un lot (D9)

Les appels d'un lot tournent en parallèle et finissent dans un ordre qui change d'une exécution à l'autre — et au rejeu, où leurs résultats sont instantanés. C'est donc **l'ordre des appels** dans la réponse du modèle qui décide : le dernier appelé l'emporte, quel que soit celui qui a fini le dernier.

## 5. Lire

### 5.1 L'orchestrateur (D4)

Avec `main.context`, chaque requête de l'orchestrateur se termine par le state, après l'historique et les résultats du run (et avant la consigne d'une réponse forcée) :

```text
<session_state>
{"client_courant":"Mme Martin","devis_courant":"D-2026-042","signature":"Plomberie Dupont","ton":"cordial"}
</session_state>
```

- JSON compact, clés triées à tous les niveaux : un même state donne la même requête, donc la même empreinte.
- Envoyé **à chaque appel, même vide** (`{}`) : il termine toujours la requête, seule la consigne d'une réponse forcée venant après lui (D4 amendé le 14/10).
- Les clés montrées ont leur valeur par défaut tant qu'elles n'en ont pas d'autre. Une valeur qui ne tient plus son schéma (config changée depuis son écriture) est écartée avec un avertissement, remplacée par la valeur par défaut s'il y en a une — jamais une erreur.
- Le bloc n'est jamais journalisé comme un message : il est recalculé à chaque appel. Les politiques `before_model` le voient.
- La consigne qui l'explique, et la description de chaque clé montrée, vont dans le **prompt système**, qui ne dépend que de la config ; elle dit aussi que seul le dernier bloc `<session_state>` de la requête fait foi.
- **Cache** : l'adaptateur Anthropic pose son point de cache des messages **avant** le bloc. Un state qui change ne fait perdre que le bloc, jamais le cache de l'historique.
- Sans `main.context`, la requête ne change pas d'un octet, même avec `state:` déclaré.

### 5.2 Un rôle

`context: [session_state]`, ou `[{session_state: [ton, signature]}]`. Avec un `input_template`, il cite `{{ context.session_state }}` (le JSON) ou `{{ context.session_state.ton }}` ; la règle #50 s'applique (tout contexte déclaré est cité). Une clé sans valeur donne une chaîne vide. Sans template, le rôle reçoit la section `<session_state>`, et la consigne qui l'explique, avec la description des clés, s'ajoute à son prompt système. L'orchestrateur lit, dans la description du rôle, qu'il reçoit déjà « l'état de la session (ton, signature) ». Un juge ne reçoit pas le state dans cette version (D11) : `session_state` dans son contexte est refusé au chargement.

### 5.3 Un outil

`ToolContext.state` : en lecture seule, une copie par appel, les clés déclarées avec leurs valeurs par défaut, telles qu'au **début du lot** — ce qu'un voisin du même lot écrit n'y est pas encore, et la reprise d'un lot interrompu montre le même state. Vide dans un sous-run et hors moteur.

### 5.4 L'application

`Loom.session_state(session_id, tenant_id=…)` et `loom state <session> [--tenant]` rendent ce qui est **écrit** — sans les valeurs par défaut — en JSON. Une session vide lève `UnknownSession` (code 1 en CLI) ; une session illisible, `SessionUnreadable`. Le `RunResult` ne porte pas de state.

## 6. Ce que la session garde

La projection `session_state` lit le journal dans l'ordre :

- le state posé par l'application compte dès le `run.started` d'un run racine, **quel que soit le sort du run** ;
- les écritures des outils d'un run racine comptent à sa clôture `run.completed`, **s'il a réussi**, sans celles des appels qu'une réparation a retirés de l'historique (D2) : le modèle ne lit jamais un state que l'historique ne justifie pas, et ce qui a vraiment eu lieu reste dans la base métier ;
- le run en cours voit, en plus, ses propres écritures, lot après lot ;
- un sous-run ne reçoit pas le state et n'y écrit pas ; un run de compaction non plus (D3) ;
- `null` efface une clé (D10) ;
- les écritures de deux runs concurrents d'une même session comptent dans l'ordre de leurs clôtures ; le state posé par l'application, dans l'ordre de leurs ouvertures.

Snapshots, compaction et coupes de l'historique ne changent rien au state : la projection ne passe pas par leurs marqueurs. Le state ne compte pas dans l'estimation qui déclenche la compaction : il doit rester petit (D7).

`run.started.state` est du **contenu** : masqué dans les relectures sans la portée `read_content`, fermé par le sceau quand le journal est chiffré, effacé avec la session (RGPD).

## 7. Rejeu, évals, banc

- **Rejeu identique** : la session est recopiée jusqu'à la demande du run, `run.started` compris : le state posé par l'application est rejoué. Un résultat d'outil lu au journal garde le state que le journal a retenu, sans que les sources `from` soient recalculées. Quand l'orchestrateur reçoit le state, une config qui change ce qu'il en voit (valeur par défaut, clés de `main.context`, schéma qui écarte une valeur) fait diverger le rejeu, sur les messages.
- **Variante** : un résultat lu au journal garde le sien ; un outil exécuté, une doublure, ou un résultat retrouvé dans le journal d'un autre run de l'arbre pose le sien comme en direct ; un refus n'en pose aucun.
- **Évals** : un cas pose son state (`cases[].state`) et part d'une session neuve ; `expect.state` contrôle une clé du state après le run, sa valeur ou son absence (`null`). Une doublure reçoit les sources `from` comme l'outil qu'elle remplace : elle doit répondre comme lui. Un state refusé fait tomber le cas, en le disant.
- **Banc** : `Bench.run(…, state=…)` et `Bench.expect(…, state=…)` ; un faux reçoit les sources `from` comme l'outil.
- **Corpus** : `tests/regression/j7/relance.yaml`, ses trois journaux rejoués à l'identique, et la suite jouée en simulé pour que ses `expect.state` voient ce que le rejeu ne voit pas.

## 8. Les décisions

| # | Décision |
|---|---|
| D1 | Option C : le state dans `run.started` et `tool.completed`, aucun type d'événement nouveau |
| D2 | State posé par l'application : compte toujours. Écritures d'outils : runs racine terminés avec succès, hors appels retirés par une réparation ; le run en cours voit les siennes |
| D3 | Sous-runs et runs de compaction : ni lecture ni écriture |
| D4 | Bloc en fin de requête, JSON trié, envoyé à chaque appel même vide (amendé le 14/10) ; consigne fixe dans le système ; point de cache Anthropic avant le bloc |
| D5 | API Python et CLI ; REST et MCP plus tard, sous une portée dédiée |
| D6 | Clés déclarées, un JSON Schema chacune, contrôlé au chargement |
| D7 | 1 Kio par valeur, 4 Kio au total (`sessions.state`), en JSON compact |
| D8 | Écriture d'outil refusée : pas posée, résultat qui reste un succès, refus lu par le modèle (amendé le 14/10) ; state refusé à l'ouverture : run refusé avant tout écrit |
| D9 | L'ordre des appels départage deux écritures d'une même clé dans un lot |
| D10 | `null` efface |
| D11 | Pas de state pour les juges dans cette version, refusé au chargement |
| D12 | `from:` (`path` ou `value`) dans la config ; `ToolOutput.state` de l'outil en secours, prioritaire sur une même clé |
| D13 | `required` refuse l'ouverture ; `default` se lit sans s'écrire ; exclusifs |

## 9. Sur agent_01 (essai à faire)

```yaml
state:
  client_courant:
    schema: {type: string, pattern: "^C-[0-9]+$"}
    description: Code du client dont parle la conversation.
    from: [{tool: get_client, path: /code}]
main:
  model: HAIKU
  system_file: system.md
  context: [session_state]
```

Ce que l'essai réel doit montrer : `get_client` ne change pas ; « donne-moi son téléphone » ne dépendrait plus d'un résumé de compaction, `client_courant` étant dans chaque requête de l'orchestrateur ; un rôle comme `rediger_message_client` pourrait lire `{{ context.session_state.client_courant }}` au lieu de chercher le dernier `get_client` de la session. Le téléphone et l'adresse resteraient dans la base : le state ne porte que l'identifiant.

## 10. Ce que le state ne fait pas

- Pas de mémoire longue ni de RAG (F6 reste séparée).
- Pas de state partagé entre sessions ni entre clients : il appartient à `(client, session)`, comme le journal.
- Pas de modification de l'historique ni de la compaction.
- Pas d'écriture par REST ni par MCP (D5) : un agent qui a une clé `required` n'y ouvre un run que dans une session où elle a déjà une valeur (sinon : 422 en REST, un résultat d'erreur en MCP).
- Pas de state pour les juges (D11).
- Le rejeu identique ne recalcule pas les sources `from` : un changement de ces seules sources ne le fait pas diverger. Les évals le voient (`expect.state`).
- En variante, un résultat d'un autre run de l'arbre, déporté, ne donne pas de données à un `path:` (backlog #022).
- L'effet du bloc de fin de requête sur un modèle à raisonnement n'est pas encore mesuré en réel.
