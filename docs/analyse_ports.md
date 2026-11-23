# Analyse — suite de ports des applications loom

*23/11/2026, version 1.0, loom 2.0.26. Sujet commun à loom et à Heddlo ; toutes les décisions sont prises (§ 4). Références : `config/models.py` (`HttpServer`), `access/cli/console.py`, `tools_builtin/memory/server/settings.py` ; dans Heddlo : `settings.py`, `docs/analyse_referentiel_utilisateurs.md`.*

---

## 1. Fonctionnel

### 1.1 Besoin

Heddlo et l'API REST de loom écoutent tous deux sur **8000** par défaut : lancés sur la même machine, le second ne démarre pas, et un `curl` vers « loom » arrive chez Heddlo (404 sur `/v1/agents`, constaté le 23/11). 8000 et 8080 sont aussi les ports par défaut de nombreux outils (uvicorn, Django, vLLM, proxys). Il faut une **suite de ports propre aux applications loom**, sans collision entre elles ni avec ces ports courants.

### 1.2 Inventaire (avant)

| Application | Réglage | Défaut |
|---|---|---|
| loom, API REST (et MCP HTTP sous `/mcp`) | `server.http.port`, `loom serve --port` | 8000 |
| loom-mem-gpu | `DEFAULT_PORT` (`memory/server/settings.py`) | 8765 |
| loom, fausse console `loom console listen` ; `console.url` des exemples | `DEFAULT_PORT` (`access/cli/console.py`) | 8766 |
| Heddlo, interface web | `HEDDLO_WEB_PORT` | 8000 |
| Heddlo, canal des instances | `HEDDLO_CHANNEL_PORT` | 8786 (loom attend 8766) |

### 1.3 Règles (après, décision P-1)

| Bloc | Port | Application | Changement |
|---|---|---|---|
| **876x : loom** | **8760** | API REST de loom (et MCP HTTP) | 8000 → 8760 |
| | 8765 | loom-mem-gpu | inchangé |
| | 8766 | canal de la console : `loom console listen`, et Heddlo | inchangé pour loom ; Heddlo 8786 → 8766 |
| **877x : Heddlo** | **8770** | interface web de Heddlo | 8000 → 8770 |

- Une **deuxième instance** loom sur la même machine prend le port suivant du bloc : **8761** (`--port 8761`), comme dans la formation (module 11).
- Les ports **8762 à 8764 et 8767 à 8769** restent libres pour de futures applications loom ; **8771 à 8779** pour Heddlo.
- Un port fixé dans `loom.yaml`, sur la ligne de commande ou dans `.env` garde la priorité : seul le **défaut** change.
- `base_url: http://localhost:8000/v1` des exemples de modèles locaux (vLLM) **ne change pas** : c'est le port de vLLM, pas de loom.

### 1.4 Cas limites

| Cas | Comportement |
|---|---|
| `loom.yaml` sans `server.http.port`, script ou client qui appelle `:8000` | Refusé (connexion impossible) après la mise à jour : CHANGELOG le signale en tête de 2.0.26, avec la ligne à ajouter pour garder 8000 (`server: {http: {port: 8000}}`) |
| `.env` de Heddlo avec `HEDDLO_WEB_PORT=8000` | Inchangé : la valeur posée l'emporte ; la retirer pour passer à 8770 |
| Instance loom configurée vers `ws://…:8786/loom` | Inchangé tant que Heddlo garde `HEDDLO_CHANNEL_PORT=8786` ; sinon, passer l'URL à `:8766` |

### 1.5 Critères d'acceptation

1. `loom serve` sans port écoute sur `127.0.0.1:8760` ; `loom validate` et la bannière le disent.
2. `heddlo serve` sans `.env` écoute sur `:8770` (web) et `:8766` (canal) ; un `loom serve` de `examples/console` s'y connecte sans changer `console.url`.
3. Heddlo et une instance loom tournent ensemble sur la même machine, sans réglage de port.
4. Plus aucune mention de `:8000` pour loom ou Heddlo dans les docs, la formation et les exemples (les `base_url` de vLLM restent).
5. CI au vert dans les deux dépôts.

---

## 2. Technique

### 2.1 Fichiers impactés

| Dépôt | Fichier | Changement |
|---|---|---|
| loom | `src/loom_ia/config/models.py` | `HttpServer.port` : défaut 8760 (champ et docstring) |
| loom | `src/loom_ia/access/http/uploads.py` | Exemple de la docstring : `:8760` |
| loom | `docs/configuration.md`, `docs/conception.md`, `docs/howto/03-production.md` | Défaut et exemples : 8760 |
| loom | `docs/formation/*.html` (modules 10 à 15, `reference.html`) | `127.0.0.1:8000` → `127.0.0.1:8760` ; module 11 : la deuxième instance passe de 8001 à 8761 |
| loom | `CHANGELOG.md` | 2.0.26 : « Changement de défaut » en tête |
| loom | `tests/unit/` | Essai du défaut 8760 ; les essais qui posent 8000 eux-mêmes ne changent pas |
| Heddlo | `src/heddlo/settings.py` | `web_port` 8770, `channel_port` 8766 |
| Heddlo | `tests/test_settings.py` | Défauts 8770 et 8766 |
| Heddlo | `README.md`, `docs/analyse_reception.md` | Ports par défaut et exemples |

`docs/docs_for_rag/` (généré) n'est pas retouché à la main : il suit la prochaine génération.

### 2.2 Dépendances

Aucune.

### 2.3 Stratégie de tests

loom : défaut du modèle (`HttpServer().port == 8760`) ; essais existants inchangés. Heddlo : défauts de `Settings`. Recherche de `:8000` dans les docs (critère 4), à la main avant livraison.

---

## 3. Lots

Un seul lot par dépôt, livré avec les suites du référentiel (Heddlo, `analyse_referentiel_utilisateurs.md` § 3.2).

| Lot | Dépôt | Contenu | Test utilisateur |
|---|---|---|---|
| 1 | loom | Défaut 8760, docs, formation, CHANGELOG, essai | `loom serve` dans `examples/console` : bannière sur `:8760` |
| 1 | Heddlo | Défauts 8770 / 8766, README, essai | Retirer `HEDDLO_WEB_PORT` et `HEDDLO_CHANNEL_PORT` de `.env` ; `heddlo serve` puis `loom serve` de l'exemple : connecté, `curl :8760/v1/agents` répond loom, `:8770` répond Heddlo |

---

## 4. Décisions

| N° | Décision |
|---|---|
| P-1 | **A** (23/11) : bloc 876x pour loom (REST 8760, loom-mem-gpu 8765, canal 8766), 877x pour Heddlo (web 8770) ; le canal de Heddlo s'aligne sur 8766. |
| P-2 | Le changement de défaut part dans **2.0.26**, pas encore publiée, signalé en tête du CHANGELOG. |
| P-3 | Spec dans `loom_v2/docs/analyse_ports.md` ; Heddlo la cite. |
