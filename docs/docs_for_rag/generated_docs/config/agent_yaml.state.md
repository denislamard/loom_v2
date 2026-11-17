# agent.yaml : la clé state

La clé `state` de `agent.yaml` et tout ce qu'elle contient.

## `state.<nom>`

Clé du state de la session (décision D6 du state de session) : son schéma, ce qu'elle désigne, ce qui la pose.

Modèle Pydantic : `loom_ia.agents.spec.StateKey`.

- `state.<nom>.schema` (type `dict[str, JsonValue]` ; obligatoire) : JSON Schema de la valeur (clé `schema`), contrôlé au chargement : une écriture qui ne le tient pas est refusée (décision D8).
- `state.<nom>.description` (type `str` ; défaut : `""`) : Ce que la clé désigne : donné avec sa valeur à qui la reçoit.
- `state.<nom>.from` (type `tuple[StateSource, ...]` ; défaut : `[]`) : Outils dont un résultat réussi pose la clé (clé `from`, décision D12) ; chaque outil une fois. Qui peut l'écrire, avec ou sans source, c'est `writers` : avec `tools`, tout outil peut aussi la poser par `ToolOutput.state` (écart E1 du state v2).
- `state.<nom>.writers` (type `tuple[StateWriter, ...]` ; défaut : `["app", "tools"]` ; bornes : longueur ≥ 1) : Qui peut demander une écriture (décision S1 du state v2) : `app`, `tools`, `model` ; `[app, tools]` par défaut. `from` exige `tools` ; `model` exige `extract`.
- `state.<nom>.extract` (type `StateExtract | null` ; défaut : `null`) : Ce que `loom.state` tire du résultat que le modèle désigne : exigé avec `model` dans `writers`, refusé sans.
- `state.<nom>.clears` (type `tuple[str, ...]` ; défaut : `[]`) : Clés que poser celle-ci par `loom.state` efface dans la même écriture : une nouvelle liste remplace le document dont on parlait (`clears: [document]`). Exige `extract` ; jamais une clé `required`.
- `state.<nom>.required` (type `bool` ; défaut : `false`) : Run refusé à l'ouverture si la clé manque au state de la session (décision D13) ; exclut `default`.
- `state.<nom>.default` (type `JsonValue` ; défaut : `null`) : Valeur lue quand la clé est absente ; elle n'est jamais écrite (décision D13).

## `state.<nom>.from[]`

Ce qui pose une clé du state (décision D12 du state de session) : chaque résultat réussi de l'outil `tool`.

Modèle Pydantic : `loom_ia.agents.spec.StateSource`.

- `state.<nom>.from[].tool` (type `str` ; obligatoire ; bornes : longueur ≥ 1) : Nom de l'outil, tel que le voit le modèle.
- `state.<nom>.from[].path` (type `str | null` ; défaut : `null`) : Pointeur JSON dans les données du résultat.
- `state.<nom>.from[].value` (type `JsonValue` ; défaut : `null`) : Valeur fixe.
- `state.<nom>.from[].on_missing` (type `"refuse" | "ignore"` ; défaut : `"refuse"`) : `refuse` (défaut) ou `ignore`.

## `state.<nom>.extract`

Ce que `loom.state` tire du résultat que le modèle désigne (décision S3 du state v2).

Modèle Pydantic : `loom_ia.agents.spec.StateExtract`.

- `state.<nom>.extract.value` (type `str | null` ; défaut : `null` ; bornes : longueur ≥ 1) : Colonne dont la valeur, sur la ligne désignée, devient celle de la clé.
- `state.<nom>.extract.fields` (type `dict[str, str]` ; défaut : `{}`) : Objet construit à partir d'une ligne : champ de la clé ← colonne.
- `state.<nom>.extract.columns` (type `dict[str, str]` ; défaut : `{}`) : Objet construit à partir de toutes les lignes : champ de la clé ← liste des valeurs d'une colonne.
- `state.<nom>.extract.label` (type `str | null` ; défaut : `null` ; bornes : longueur ≥ 1) : Champ de l'objet qui reçoit le libellé donné par le modèle.
