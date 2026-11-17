# loom.yaml : la clé server

La clé `server` de `loom.yaml` et tout ce qu'elle contient.

## `server`

Serveurs de l'instance (`server`) : HTTP et MCP.

Modèle Pydantic : `loom_ia.config.models.ServerConfig`.

- `server.http` (type `HttpServer` ; défaut : les défauts de la section) : Serveur HTTP (`loom serve`).
- `server.mcp` (type `McpAccess` ; défaut : les défauts de la section) : Serveur MCP (`loom mcp`, et HTTP sous `/mcp`).

## `server.http`

Serveur HTTP de `loom serve` (`server.http`).

Modèle Pydantic : `loom_ia.config.models.HttpServer`.

- `server.http.host` (type `str` ; défaut : `"127.0.0.1"`) : Adresse d'écoute (`127.0.0.1` par défaut).
- `server.http.port` (type `int` ; défaut : `8000` ; bornes : ≥ 1, ≤ 65535) : Port d'écoute (8000 par défaut).
- `server.http.base_path` (type `str` ; défaut : `""`) : Préfixe commun des routes, par exemple `/loom` ; commence par `/`.
- `server.http.max_body_bytes` (type `int` ; défaut : `134217728` ; bornes : > 0) : Plafond du corps d'une requête (JSON, déclencheurs, MCP, envois de fichiers) : au-delà, 413 sans lire la suite. Par MCP, le SDK plafonne déjà le corps à 4 Mio avant tout code de Loom-IA : cette valeur ne peut que l'abaisser, pas le relever. Les fichiers y voyagent en base64 dans le JSON (4 octets pour 3), donc environ 3 Mio de fichier brut ; REST accepte jusqu'à ce plafond (défaut 128 Mio).
- `server.http.docs` (type `bool | null` ; défaut : `null`) : Documentation interactive (`/docs`, `/redoc`) et schéma (`/openapi.json`) : ils se lisent **sans clé**, et décrivent toutes les routes. `null` (défaut) : servis, sauf en profil strict (`prod`) ; `true` / `false` tranchent (essai de la fonction N2, 23/10 : ils répondaient 200 sans clé à côté d'une API protégée).
- `server.http.user` (type `str | null` ; défaut : `null` ; bornes : motif `^[A-Za-z0-9_-]{1,64}$`) : Utilisateur au nom duquel une instance **locale sans clé** répond (décision D-1b) : l'usage local reste simple, et le journal nomme quelqu'un. Refusé hors de la boucle locale, avec des clés, avec le MCP HTTP, et en profil `prod` (`LoomConfig.check`).

## `server.mcp`

Serveur MCP de l'instance : stdio (`loom mcp`) et HTTP (jalon J5.2b).

Modèle Pydantic : `loom_ia.config.models.McpAccess`.

- `server.mcp.file_roots` (type `tuple[Path, ...]` ; défaut : `[]`) : Dossiers où un lien `file://` joint à un appel peut être lu ; aucun par défaut : les liens `file://` sont refusés. Relatifs au dossier de la config.
- `server.mcp.http` (type `bool` ; défaut : `false`) : Monte le serveur MCP dans l'application REST, sous `<base_path>/mcp`. Il expose des **outils** à un LLM tiers : il exige des clés d'API.
- `server.mcp.allowed_origins` (type `tuple[str, ...]` ; défaut : `[]`) : Origines acceptées par le transport (spec MCP, protection contre le rebinding DNS). Une requête **sans** `Origin` passe — un client natif n'en envoie pas ; avec un `Origin`, il doit figurer ici.
- `server.mcp.allowed_hosts` (type `tuple[str, ...]` ; défaut : `[]`) : Hôtes acceptés, **en plus** de l'adresse d'écoute que loom ajoute lui-même (`127.0.0.1:*`, `localhost:*`, et `server.http.host`). À renseigner derrière un nom de domaine ou un proxy.
