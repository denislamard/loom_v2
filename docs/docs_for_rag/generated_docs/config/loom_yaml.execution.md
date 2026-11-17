# loom.yaml : la clé execution

La clé `execution` de `loom.yaml` et tout ce qu'elle contient.

## `execution`

Exécution des runs (`execution`) : outils, pièces jointes, arrêt, concession.

Modèle Pydantic : `loom_ia.config.models.ExecutionConfig`.

- `execution.tools` (type `ToolsExecution` ; défaut : les défauts de la section (`timeout: 30.0`, `validate_arguments: true`, `offload_over: 50000`, `order: "effects"`, `threads: 16`, `grace: 30.0`)) : Exécution des outils.
- `execution.attachments` (type `AttachmentPolicy` ; défaut : les défauts de la section (`max_bytes: 5242880`, `types: ["image/jpeg", "image/png", "image/gif", "image/webp"]`, `max_files: 10`)) : Pièces jointes acceptées à l'entrée d'un run (fonction G1) : formats d'image, taille et nombre maximaux.
- `execution.shutdown_timeout` (type `float` ; défaut : `30.0` ; bornes : > 0) : Délai laissé aux tâches de fond (compaction) à la fermeture de l'instance, en secondes (30).
- `execution.lease` (type `float` ; défaut : `60.0` ; bornes : > 0) : Durée de la concession prise sur un run par l'instance qui le pilote (point 27), en secondes ; elle est renouvelée au tiers tant qu'il tourne. Passée, un autre worker peut reprendre le run — ce qui n'arrive que si le porteur est mort.

## `execution.tools`

Exécution des outils (`execution.tools`), pour tous les agents ; un agent la surcharge par son `execution.tools`.

Modèle Pydantic : `loom_ia.config.models.ToolsExecution`.

- `execution.tools.timeout` (type `float | null` ; défaut : `30.0`) : Délai par défaut d'un outil, en secondes (30) ; `null` retire la limite.
- `execution.tools.validate_arguments` (type `bool` ; défaut : `true`) : Contrôle des arguments contre le schéma de l'outil (vrai par défaut).
- `execution.tools.offload_over` (type `int | null` ; défaut : `50000`) : Au-delà, en caractères, le résultat est déporté (50 000 par défaut ; point 16) ; `null` le désactive.
- `execution.tools.order` (type `"effects" | "free"` ; défaut : `"effects"`) : Ordre des appels d'un même tour : `effects` (défaut), un appel à effet attend ceux qui le précèdent et ceux qui le suivent l'attendent ; `free`, tous partent ensemble (comme avant 2.0.20).
- `execution.tools.threads` (type `int` ; défaut : `16` ; bornes : > 0) : Threads du pool des outils synchrones (`loom-outil`), à part de celui d'asyncio, qui sert le journal et les fichiers (16 par défaut ; décision B-2 du délai et des threads). Un pool par process.
- `execution.tools.grace` (type `float | null` ; défaut : `30.0`) : Après un délai dépassé, attente au plus du travail en thread d'un appel à effet avant de lancer les appels suivants du tour, en secondes (30) ; `null` : sans borne.

## `execution.attachments`

Pièces jointes acceptées à l'entrée d'un run (`execution.attachments`, fonction G1).

Le format se reconnaît à la signature binaire ; PDF, audio et autres
fichiers ne sont pas encore pris en charge.

Modèle Pydantic : `loom_ia.core.model.media.AttachmentPolicy`.

- `execution.attachments.max_bytes` (type `int` ; défaut : `5242880` ; bornes : > 0) : Taille maximale d'une pièce jointe, en octets. Défaut : 5 Mio, la limite d'une image chez Anthropic.
- `execution.attachments.types` (type `tuple[str, ...]` ; défaut : `["image/jpeg", "image/png", "image/gif", "image/webp"]` ; bornes : longueur ≥ 1) : Types MIME acceptés, parmi les images reconnues. Défaut : JPEG, PNG, GIF, WebP.
- `execution.attachments.max_files` (type `int` ; défaut : `10` ; bornes : > 0) : Nombre de pièces jointes d'un run ; borne aussi la taille d'un envoi REST. Défaut : 10.
