# loom.yaml : la clé tool_sources

La clé `tool_sources` de `loom.yaml` et tout ce qu'elle contient.

## `tool_sources[]`

Une source d'outils fournie par un paquet installé (`tool_sources` ; jalon J6.4b, conception §7.3).

`entry_point` nomme un point d'entrée du groupe `loom_ia.tools` ; loom
n'importe son paquet que si un agent référence la source. `params` est
remis tel quel à la fabrique du paquet, qui le vérifie. La config ne porte
pas de secret : la fabrique reçoit la table des secrets du client.

Modèle Pydantic : `loom_ia.config.models.ToolSourceSpec`.

- `tool_sources[].name` (type `str` ; obligatoire ; bornes : longueur ≤ 40, motif `^[A-Za-z0-9-]+(_[A-Za-z0-9-]+)*$`) : Nom dans la config (40 caractères au plus) ; il préfixe les outils (`carnet__chercher_devis`).
- `tool_sources[].entry_point` (type `str` ; obligatoire ; bornes : longueur ≥ 1, motif `^[^\s=]+$`) : Nom du point d'entrée dans le groupe `loom_ia.tools`.
- `tool_sources[].params` (type `dict[str, JsonValue]` ; défaut : `{}`) : Paramètres remis à la fabrique du paquet.
- `tool_sources[].tools` (type `dict[str, ToolOverrides]` ; défaut : `{}`) : Déclarations par outil (nom court), sur celles que la source annonce.

## `tool_sources[].tools.<nom>`

Même forme que `tools[].tools.<nom>` (page « agent.yaml : la clé tools »).
