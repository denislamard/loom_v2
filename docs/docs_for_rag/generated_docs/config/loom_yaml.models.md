# loom.yaml : la clé models

La clé `models` de `loom.yaml` et tout ce qu'elle contient.

## `models[]`

Un modèle déclaré sous `models` dans `loom.yaml`.

Modèle Pydantic : `loom_ia.core.model.model_spec.ModelSpec`.

- `models[].id` (type `str` ; obligatoire ; bornes : longueur ≥ 1) : Identifiant du modèle dans la config (`HAIKU`), par lequel un agent, un rôle, un juge ou une chaîne de secours le désigne. Recopié en `model` dans les événements.
- `models[].sdk` (type `Sdk` ; obligatoire) : Adaptateur : `anthropic`, `openai` ou `fake`.
- `models[].api` (type `ModelApi | null` ; défaut : `null`) : Pour `sdk: openai` seulement : `chat` (défaut) ou `responses`.
- `models[].model` (type `str` ; obligatoire ; bornes : longueur ≥ 1) : Nom du modèle chez le fournisseur (`claude-haiku-5-5`), recopié en `model_id` dans les événements.
- `models[].base_url` (type `str | null` ; défaut : `null`) : Point d'accès ; sans lui, l'adresse officielle du fournisseur du SDK (jamais celle d'une variable d'environnement).
- `models[].api_key_env` (type `str | null` ; défaut : `null`) : Nom de la variable d'environnement qui contient la clé ; un client peut la rediriger vers la sienne (`secrets`).
- `models[].max_tokens` (type `int | null` ; défaut : `null`) : Plafond de tokens de sortie par appel ; un emplacement peut le surcharger. Une sortie coupée sur cette limite est une erreur `truncated`.
- `models[].params` (type `dict[str, JsonValue]` ; défaut : `{}`) : Réglages transmis tels quels au fournisseur (température, thinking…). Une surcharge remplace une clé entière, pas son contenu (fonction B6).
- `models[].timeouts` (type `ModelTimeouts` ; défaut : les défauts de la section (`first_token: 60.0`, `idle: 60.0`, `total: 300.0`)) : Délais de l'appel (`ModelTimeouts`).
- `models[].retry` (type `RetryPolicy` ; défaut : les défauts de la section (`max_attempts: 3`, `initial_delay: 1.0`, `multiplier: 2.0`, `max_delay: 30.0`)) : Nouvelles tentatives (`RetryPolicy`).
- `models[].capabilities` (type `ModelCapabilities` ; défaut : les défauts de la section (`tools: true`, `thinking: false`, `native_json: false`, `forced_tool_choice: true`, `streaming: true`, `vision: false`, `image_input: ["base64"]`, `image_formats: ["jpeg", "png", "gif", "webp"]`, `tool_result_media: false`)) : Capacités déclarées (`ModelCapabilities`).
- `models[].pricing` (type `Pricing` ; défaut : les défauts de la section (`input: 0.0`, `output: 0.0`, `cache_read: 0.0`, `cache_write: 0.0`, `tiers: []`)) : Grille tarifaire, en dollars par million de tokens (`Pricing`). Sans tarif, les appels comptent 0 $.
- `models[].circuit_breaker` (type `CircuitBreaker | null` ; défaut : les défauts de la section (`failures: 5`, `cooldown: 60.0`)) : Disjoncteur (point 10) ; actif par défaut (5 échecs, 60 s), `null` le retire.
- `models[].cache` (type `PromptCache | null` ; défaut : `null`) : Points de cache de prompt, pour `sdk: anthropic` seulement.

## `models[].timeouts`

Délais d'un appel de modèle (`models[].timeouts`), en secondes ; `None` désactive un délai.

Un délai dépassé est une erreur `transient` : il donne lieu à de
nouvelles tentatives, puis à la bascule vers le secours.

Modèle Pydantic : `loom_ia.core.model.model_spec.ModelTimeouts`.

- `models[].timeouts.first_token` (type `float | null` ; défaut : `60.0`) : Attente au plus du premier morceau du flux. Défaut : 60.
- `models[].timeouts.idle` (type `float | null` ; défaut : `60.0`) : Attente au plus entre deux morceaux. Défaut : 60.
- `models[].timeouts.total` (type `float | null` ; défaut : `300.0`) : Durée au plus de l'appel complet, flux compris. Défaut : 300.

## `models[].retry`

Nouvelles tentatives sur les erreurs `transient` et `overloaded`.

Délai de la tentative n+1 : `initial_delay * multiplier ** (n - 1)`, plafonné à
`max_delay`, puis tiré au hasard entre la moitié et la totalité de cette
valeur. Un `Retry-After` du fournisseur remplace ce calcul ; s'il dépasse
`max_delay`, l'appel est abandonné. Chaque tentative ratée écrit un
`model.retried`. Se déclare sous `models[].retry`.

Modèle Pydantic : `loom_ia.core.model.model_spec.RetryPolicy`.

- `models[].retry.max_attempts` (type `int` ; défaut : `3` ; bornes : > 0) : Nombre total de tentatives, la première comprise. Défaut : 3.
- `models[].retry.initial_delay` (type `float` ; défaut : `1.0` ; bornes : ≥ 0) : Attente avant la deuxième tentative, en secondes. Défaut : 1.
- `models[].retry.multiplier` (type `float` ; défaut : `2.0` ; bornes : ≥ 1.0) : Facteur appliqué à chaque tentative suivante, 1 au moins. Défaut : 2.
- `models[].retry.max_delay` (type `float` ; défaut : `30.0` ; bornes : ≥ 0) : Plafond d'une attente, en secondes. Défaut : 30.

## `models[].capabilities`

Capacités déclarées d'un modèle (`models[].capabilities`), contrôlées au démarrage (fonction B9).

Images (point 14) : un modèle sans `vision` n'en reçoit qu'une mention
textuelle. Pour un modèle avec `vision`, chaque image est lue dans le
stockage d'artefacts et envoyée en base64, après contrôle de son format
(`image_formats`) et de sa taille (`max_image_bytes`).

`tools` : le modèle sait appeler des outils ; exigé d'un orchestrateur
qui en a, d'un juge (verdict par outil imposé) et de leurs secours.
`thinking` : il produit un raisonnement et l'attend en retour pendant
ses tours d'outils ; avec l'API Chat, le raisonnement de la boucle en
cours lui est renvoyé (#7, backlog #015). `native_json` : il accepte un
schéma de sortie JSON ; celui du contrat lui est transmis pour un appel
sans outils (fonction B9). `forced_tool_choice` : il accepte qu'on lui impose
d'appeler un outil (`tool_choice: required`) ; sans elle, une requête qui
l'impose part en `auto` (`engine.fallback.sendable`). Claude Opus 5.5
et Fable 5.1 refusent l'outil imposé (400) : le verdict d'un juge leur est
alors demandé par la consigne seule.

Modèle Pydantic : `loom_ia.core.model.model_spec.ModelCapabilities`.

- `models[].capabilities.tools` (type `bool` ; défaut : `true`) : Le modèle sait appeler des outils. Défaut : vrai.
- `models[].capabilities.thinking` (type `bool` ; défaut : `false`) : Il produit un raisonnement et l'attend en retour. Défaut : faux.
- `models[].capabilities.native_json` (type `bool` ; défaut : `false`) : Il accepte un schéma de sortie JSON. Défaut : faux.
- `models[].capabilities.forced_tool_choice` (type `bool` ; défaut : `true`) : Il accepte `tool_choice: required`. Défaut : vrai.
- `models[].capabilities.streaming` (type `bool` ; défaut : `true`) : Faux : appel non streamé, dont la réponse est rejouée en un flux simulé. Défaut : vrai.
- `models[].capabilities.context_window` (type `int | null` ; défaut : `null`) : Fenêtre en tokens ; vérifiée avant l'appel si elle est déclarée (fonction B7), et utilisée pour choisir un secours en cas de `context_overflow`. Défaut : non déclarée.
- `models[].capabilities.vision` (type `bool` ; défaut : `false`) : Le modèle reçoit les images. Défaut : faux.
- `models[].capabilities.image_input` (type `tuple[ImageInput, ...]` ; défaut : `["base64"]`) : Formes d'image acceptées ; loom n'envoie pour l'instant qu'en `base64`, qui doit y figurer avec `vision`. Défaut : `[base64]`.
- `models[].capabilities.image_formats` (type `tuple[ImageFormat, ...]` ; défaut : `["jpeg", "png", "gif", "webp"]`) : Formats d'image acceptés. Défaut : jpeg, png, gif, webp.
- `models[].capabilities.max_image_bytes` (type `int | null` ; défaut : `null`) : Taille maximale d'une image, en octets. Défaut : pas de limite.
- `models[].capabilities.tool_result_media` (type `bool` ; défaut : `false`) : Images acceptées dans un résultat d'outil ; sinon, elles suivent les résultats dans un message utilisateur. Défaut : faux.

## `models[].pricing`

Grille tarifaire d'un modèle, en dollars par million de tokens.

Les tokens de raisonnement sont déjà comptés dans `output_tokens` par les
fournisseurs : ils ne sont pas facturés une seconde fois.

Paliers (`tiers`) : certains fournisseurs changent de prix au-delà d'un
nombre de tokens d'entrée par appel (MiniMax-M3 double ses prix au-delà de
512k). Le palier le plus haut dont le seuil est dépassé s'applique à tout
l'appel.

Modèle Pydantic : `loom_ia.core.model.usage.Pricing`.

- `models[].pricing.input` (type `float` ; défaut : `0.0` ; bornes : ≥ 0) : Prix des tokens d'entrée hors cache, en dollars par million. Défaut : 0.
- `models[].pricing.output` (type `float` ; défaut : `0.0` ; bornes : ≥ 0) : Prix des tokens de sortie. Défaut : 0.
- `models[].pricing.cache_read` (type `float` ; défaut : `0.0` ; bornes : ≥ 0) : Prix des tokens lus dans le cache. Défaut : 0.
- `models[].pricing.cache_write` (type `float` ; défaut : `0.0` ; bornes : ≥ 0) : Prix des tokens écrits dans le cache (celui de la durée de cache choisie). Défaut : 0.
- `models[].pricing.tiers` (type `tuple[PriceTier, ...]` ; défaut : `[]`) : Paliers, aux seuils `above` strictement croissants.

## `models[].pricing.tiers[]`

Palier de tarif : prix d'un appel dont les tokens d'entrée (cache compris) dépassent `above`.

Modèle Pydantic : `loom_ia.core.model.usage.PriceTier`.

- `models[].pricing.tiers[].above` (type `int` ; obligatoire ; bornes : > 0) : Seuil de tokens d'entrée par appel, cache compris.
- `models[].pricing.tiers[].input` (type `float | null` ; défaut : `null`) : Prix des tokens d'entrée, en dollars par million ; absent : celui du tarif de base.
- `models[].pricing.tiers[].output` (type `float | null` ; défaut : `null`) : Prix des tokens de sortie ; absent : celui du tarif de base.
- `models[].pricing.tiers[].cache_read` (type `float | null` ; défaut : `null`) : Prix des tokens lus dans le cache ; absent : celui du tarif de base.
- `models[].pricing.tiers[].cache_write` (type `float | null` ; défaut : `null`) : Prix des tokens écrits dans le cache ; absent : celui du tarif de base.

## `models[].circuit_breaker`

Disjoncteur d'un modèle ou d'un serveur MCP (#10, #19, backlog #011).

Après `failures` échecs de suite, la cible est écartée pendant
`cooldown` secondes, pour tous les runs de l'instance : un modèle passe
directement à son secours, un serveur MCP est indisponible. Ensuite, un
essai : réussi, le disjoncteur se referme ; raté, il se rouvre. L'ouverture
écrit un `circuit.opened`. Se déclare sous `models[].circuit_breaker` ou
`mcp_servers[].circuit_breaker` ; `null` le retire.

Modèle Pydantic : `loom_ia.core.model.model_spec.CircuitBreaker`.

- `models[].circuit_breaker.failures` (type `int` ; défaut : `5` ; bornes : > 0) : Échecs de suite qui ouvrent le disjoncteur. Défaut : 5.
- `models[].circuit_breaker.cooldown` (type `float` ; défaut : `60.0` ; bornes : > 0) : Durée pendant laquelle la cible est écartée, en secondes. Défaut : 60.

## `models[].cache`

Points de cache de prompt d'un modèle `sdk: anthropic` (`models[].cache`, fonction B5).

`system` : sur le prompt système ; `tools` : sur le dernier outil ;
`messages` : sur le dernier bloc de la conversation, un point qui avance
à chaque appel. L'adaptateur les traduit en `cache_control` (au plus
quatre par requête, blocs marqués `cache_breakpoint` compris). Le prix
d'écriture (`pricing.cache_write`) est celui de la durée choisie.

Les API OpenAI et leurs compatibles cachent seules : `cache` y est refusé.

Modèle Pydantic : `loom_ia.core.model.model_spec.PromptCache`.

- `models[].cache.system` (type `bool` ; défaut : `false`) : Point de cache sur le prompt système. Défaut : faux.
- `models[].cache.tools` (type `bool` ; défaut : `false`) : Point de cache sur le dernier outil. Défaut : faux.
- `models[].cache.messages` (type `bool` ; défaut : `false`) : Point de cache sur le dernier bloc de la conversation, qui avance à chaque appel. Défaut : faux.
- `models[].cache.ttl` (type `CacheTtl` ; défaut : `"5m"`) : Durée de vie d'une entrée : `5m` (défaut) ou `1h`.
