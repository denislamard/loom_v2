# loom.yaml : la clé sessions

La clé `sessions` de `loom.yaml` et tout ce qu'elle contient.

## `sessions`

Vie d'une session (`sessions`) : historique matérialisé, compaction (fonctions F1 à F4), médias, state.

Modèle Pydantic : `loom_ia.config.models.SessionsConfig`.

- `sessions.snapshot_every` (type `int` ; défaut : `50` ; bornes : > 0) : Événements ajoutés depuis le dernier marqueur avant qu'un snapshot de l'historique soit écrit (50 par défaut ; conception §11.2). Le relire coûte moins que de rejouer ce qu'il couvre ; l'écrire recopie l'historique dans le journal.
- `sessions.compaction` (type `CompactionConfig | null` ; défaut : `null`) : Résumé des sessions longues. Sans ce bloc, une session n'est jamais résumée : elle grandit jusqu'à la fenêtre du modèle.
- `sessions.media` (type `SessionMedia` ; défaut : les défauts de la section (`keep_images: "all"`)) : Images des tours précédents dans les requêtes (fonction F4).
- `sessions.state` (type `StateLimits` ; défaut : les défauts de la section (`max_value_bytes: 1024`, `max_total_bytes: 4096`)) : Limites du state de la session ; ses clés sont déclarées par les agents.

## `sessions.compaction`

Résumé d'une session devenue longue (point 23, conception §11.3).

Bloc `sessions.compaction` de la config.

Modèle Pydantic : `loom_ia.config.compaction.CompactionConfig`.

- `sessions.compaction.model` (type `str` ; obligatoire ; bornes : longueur ≥ 1) : Modèle dédié (identifiant dans `models`), en général moins cher que celui de l'agent.
- `sessions.compaction.over_tokens` (type `int` ; défaut : `12000` ; bornes : > 0) : Taille d'historique, en tokens estimés, au-delà de laquelle un résumé est mis en file à la fin d'un run (12 000 par défaut).
- `sessions.compaction.hard_tokens` (type `int` ; défaut : `150000` ; bornes : > 0) : Taille au-delà de laquelle il devient bloquant à l'ouverture d'un run (150 000 par défaut) ; doit dépasser `over_tokens`.
- `sessions.compaction.keep_last` (type `int` ; défaut : `6` ; bornes : ≥ 0) : Tours laissés intacts à la fin de l'historique (un tour = un run ; 6 par défaut).
- `sessions.compaction.fidelity_check` (type `bool` ; défaut : `true`) : Contrôle déterministe des repères du segment dans le résumé.
- `sessions.compaction.blocking` (type `bool` ; défaut : `false`) : Vrai : le résumé dû se fait à l'ouverture du run suivant, qui l'attend, au lieu d'une tâche de fond après le run. Rien ne reste alors en cours quand un process de courte vie (CLI) se ferme.
- `sessions.compaction.system_file` (type `Path | null` ; défaut : `null`) : Prompt interne surchargé, relatif au dossier des ressources.

## `sessions.media`

Fichiers des tours précédents dans les requêtes d'une session (fonction F4).

Modèle Pydantic : `loom_ia.config.models.SessionMedia`.

- `sessions.media.keep_images` (type `KeepImages` ; défaut : `"all"`) : `all` (défaut) : un modèle vision revoit toutes les images de la session, à chaque appel, relues dans le stockage d'artefacts. `last_run` : une image d'un tour précédent est remplacée par une note (nom, type, taille) ; seules celles du run en cours partent en octets. Pas par défaut : pour une image rendue par un outil, le modèle peut alors désavouer la description qu'il en avait faite (essai de la fonction G3, 20/10).

## `sessions.state`

Taille du state d'une session (`sessions.state` ; décision D7 du state de session), en octets de JSON compact.

Le state part avec chaque appel de l'orchestrateur qui le reçoit, et
l'estimation qui déclenche la compaction ne le compte pas : il reste petit.

Modèle Pydantic : `loom_ia.config.models.StateLimits`.

- `sessions.state.max_value_bytes` (type `int` ; défaut : `1024` ; bornes : > 0) : Taille maximale d'une valeur, écrite par un outil ou posée par l'application (1024 par défaut).
- `sessions.state.max_total_bytes` (type `int` ; défaut : `4096` ; bornes : > 0) : Taille maximale de tout le state de la session (4096 par défaut).
