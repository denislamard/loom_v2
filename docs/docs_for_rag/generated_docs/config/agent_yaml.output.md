# agent.yaml : la clé output

La clé `output` de `agent.yaml` et tout ce qu'elle contient.

## `output`

Contrat de sortie : ce qu'une sortie doit respecter (`output`).

Une sortie vide est toujours refusée. Avec un schéma, la réponse finale
validée devient la réponse structurée du run (`data`, fonction A7).

Modèle Pydantic : `loom_ia.core.model.contract.OutputContract`.

- `output.schema` (type `dict[str, JsonValue] | null` ; défaut : `null`) : Schéma JSON de la sortie, écrit `schema` dans la config : elle doit être un JSON conforme. Contrôlé au chargement.
- `output.schema_file` (type `Path | null` ; défaut : `null`) : Fichier du schéma, relatif au dossier de la config ; lu au chargement. Exclusif de `schema`.
- `output.must_match` (type `str | null` ; défaut : `null`) : Motif qui doit se trouver dans la sortie (module `regex`, avec un délai d'1 s).
- `output.must_not_match` (type `str | null` ; défaut : `null`) : Motif interdit dans la sortie.
- `output.max_chars` (type `int | null` ; défaut : `null`) : Longueur maximale de la sortie, en caractères.
- `output.normalize` (type `bool` ; défaut : `true`) : Normalisation déterministe avant le contrôle : blocs de code retirés, JSON extrait, espaces nettoyés. Défaut : vrai.
- `output.repair` (type `RepairSettings` ; défaut : les défauts de la section (`max_attempts: 1`, `tools: "auto"`)) : Réparations demandées (`RepairSettings`) ; une par défaut.
- `output.on_failure` (type `OnFailure` ; défaut : `"fail"`) : `fail` (défaut), `unverified` ou `fallback`.
- `output.fallback_message` (type `str | null` ; défaut : `null`) : Sortie de remplacement quand `on_failure` vaut `fallback` ; obligatoire dans ce cas.

## `output.repair`

Réparations demandées à l'auteur de la sortie refusée (`repair`, point 20).

Chaque réparation écrit un `policy.decided` (`retry`) puis un
`message.user` de `kind: repair` qui porte le diagnostic.

Modèle Pydantic : `loom_ia.core.model.contract.RepairSettings`.

- `output.repair.max_attempts` (type `int` ; défaut : `1` ; bornes : ≥ 0) : Nombre de réparations ; 0 n'en demande aucune. Défaut : 1.
- `output.repair.tools` (type `RepairTools` ; défaut : `"auto"`) : Outils de l'orchestrateur pendant la réparation : `auto` (défaut), `none` ou `allowed`.
