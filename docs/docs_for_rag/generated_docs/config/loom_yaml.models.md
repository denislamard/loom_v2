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
- `models[].timeouts` (type `ModelTimeouts` ; défaut : les défauts de la section) : Délais de l'appel (`ModelTimeouts`).
- `models[].retry` (type `RetryPolicy` ; défaut : les défauts de la section) : Nouvelles tentatives (`RetryPolicy`).
- `models[].capabilities` (type `ModelCapabilities` ; défaut : les défauts de la section) : Capacités déclarées (`ModelCapabilities`).
- `models[].pricing` (type `Pricing` ; défaut : les défauts de la section) : Grille tarifaire, en dollars par million de tokens (`Pricing`). Sans tarif, les appels comptent 0 $.
- `models[].circuit_breaker` (type `CircuitBreaker | null` ; défaut : les défauts de la section) : Disjoncteur (point 10) ; actif par défaut (5 échecs, 60 s), `null` le retire.
- `models[].cache` (type `PromptCache | null` ; défaut : `null`) : Points de cache de prompt, pour `sdk: anthropic` seulement.

## `models[].timeouts`

Délais d'un appel de modèle (`models[].timeouts`), en secondes ; `None` désactive un délai.

Modèle Pydantic : `loom_ia.core.model.model_spec.ModelTimeouts`.

- `models[].timeouts.first_token` (type `float | null` ; défaut : `60.0`) : Attente au plus du premier morceau du flux. Défaut : 60.
- `models[].timeouts.idle` (type `float | null` ; défaut : `60.0`) : Attente au plus entre deux morceaux. Défaut : 60.
- `models[].timeouts.total` (type `float | null` ; défaut : `300.0`) : Durée au plus de l'appel complet, flux compris. Défaut : 300.

## `models[].retry`

Nouvelles tentatives sur les erreurs `transient` et `overloaded`.

Modèle Pydantic : `loom_ia.core.model.model_spec.RetryPolicy`.

- `models[].retry.max_attempts` (type `int` ; défaut : `3` ; bornes : > 0) : Nombre total de tentatives, la première comprise. Défaut : 3.
- `models[].retry.initial_delay` (type `float` ; défaut : `1.0` ; bornes : ≥ 0) : Attente avant la deuxième tentative, en secondes. Défaut : 1.
- `models[].retry.multiplier` (type `float` ; défaut : `2.0` ; bornes : ≥ 1.0) : Facteur appliqué à chaque tentative suivante, 1 au moins. Défaut : 2.
- `models[].retry.max_delay` (type `float` ; défaut : `30.0` ; bornes : ≥ 0) : Plafond d'une attente, en secondes. Défaut : 30.

## `models[].capabilities`

Capacités déclarées d'un modèle (`models[].capabilities`), contrôlées au démarrage (fonction B9).

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

Modèle Pydantic : `loom_ia.core.model.model_spec.CircuitBreaker`.

- `models[].circuit_breaker.failures` (type `int` ; défaut : `5` ; bornes : > 0) : Échecs de suite qui ouvrent le disjoncteur. Défaut : 5.
- `models[].circuit_breaker.cooldown` (type `float` ; défaut : `60.0` ; bornes : > 0) : Durée pendant laquelle la cible est écartée, en secondes. Défaut : 60.

## `models[].cache`

Points de cache de prompt d'un modèle `sdk: anthropic` (`models[].cache`, fonction B5).

Modèle Pydantic : `loom_ia.core.model.model_spec.PromptCache`.

- `models[].cache.system` (type `bool` ; défaut : `false`) : Point de cache sur le prompt système. Défaut : faux.
- `models[].cache.tools` (type `bool` ; défaut : `false`) : Point de cache sur le dernier outil. Défaut : faux.
- `models[].cache.messages` (type `bool` ; défaut : `false`) : Point de cache sur le dernier bloc de la conversation, qui avance à chaque appel. Défaut : faux.
- `models[].cache.ttl` (type `CacheTtl` ; défaut : `"5m"`) : Durée de vie d'une entrée : `5m` (défaut) ou `1h`.
