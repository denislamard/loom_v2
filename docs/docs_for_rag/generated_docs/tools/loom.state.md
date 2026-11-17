# Outil fourni loom.state

L'outil `loom.state` est fourni avec loom_v2. Un agent l'active dans son `agent.yaml` : `- python: loom.state` dans `tools`.

## Ce que fait loom.state

`loom.state` : le modèle tient à jour le state de la session (state v2).

Le modèle **désigne** un résultat d'outil du run par sa référence (`result:3`,
celle qui précède chaque résultat) ; l'outil le relit (`ToolContext.results`)
et en **tire** la valeur selon la règle déclarée sur la clé (`extract:`). Le
modèle ne transmet aucune donnée : il ne peut ni mal recopier un identifiant,
ni en inventer un.

    {"set": "client", "result": "result:3", "match": {"code": "C-261"}}
    {"set": "selection", "result": "result:5", "label": "factures en retard"}
    {"clear": "document"}

L'outil est tolérant sur la forme de ce qu'il reçoit, strict sur le fond :
`result` vaut `"result:3"`, `"3"`, `3` ou `{"$ref": "result:3"}` (le
moteur ne remplace pas cette référence par des données, pour cet outil) ; la
ligne se désigne par `match` (des colonnes et leurs valeurs, que doit porter
une seule ligne) plutôt que par `row`, qu'un modèle compte mal. Poser une clé
efface aussi celles de son `clears:`.

Seules les clés qui ont `model` dans leurs `writers` sont proposées. Un
refus — clé inconnue, résultat introuvable, extraction ou schéma — est un
résultat en erreur qui dit quoi corriger (décision S4 du state v2) : l'outil n'a pas d'autre effet
que l'écriture. L'écriture passe ensuite par les contrôles du moteur, comme
celle de tout outil (décisions D6 à D9 du state de session) : seule la taille totale du state, qui dépend des
voisins du lot, peut encore la refuser, en le disant à la suite du résultat.

## Comment loom.state est monté

- cible : `loom_ia.tools_builtin.state:build`
- réglages communs refusés sur sa référence : `approval`, `side_effects`, `idempotent`, car l'outil n'agit que sur le state de la session, sans effet hors de loom ; qui peut écrire chaque clé se règle sur la clé (state.<clé>.writers)
- l'outil tire sa description de ses réglages : `description` est refusé, `skill` ajoute du texte

## Les réglages de loom.state dans agent.yaml

Ce sont des clés de sa référence dans `tools`, à côté de `python: loom.state`.

### `tools[]` (référence `python: loom.state`)

Aucun paramètre : une clé inconnue est refusée.

Modèle Pydantic : `loom_ia.tools_builtin.state_params.StateParams`.
