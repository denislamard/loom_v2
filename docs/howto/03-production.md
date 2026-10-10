# Niveaux 3 (fin) et 4 : fiabilité, durabilité et service multi-clients

> **Rappel.** Dans [02-maitriser.md](02-maitriser.md), l'agent a appris à tenir une conversation, à demander une validation humaine, à respecter des politiques et des contrats, à déléguer à des rôles et à se faire contrôler par des juges. Il fonctionne, mais en local, pour une seule personne, avec un modèle qui répond toujours. Ce fichier le prépare à la vraie vie : pannes de fournisseur, budget, crash du serveur, plusieurs entreprises clientes.

Ce fichier couvre les chapitres 15 à 24. Il suit une progression simple : d'abord un agent qui **ne s'arrête pas** quand un fournisseur tombe (ch. 15 à 18), puis un agent qui **survit à un redémarrage** (ch. 19), enfin un agent **servi à plusieurs entreprises** par REST, MCP ou webhook (ch. 20 à 24).

## Le projet `relance/` de ce fichier

Le fil rouge est celui du README : la Plomberie Dupont a envoyé le 3 septembre le devis D-2026-042 (1 840 € TTC, remplacement d'un chauffe-eau) à Mme Martin, qui n'a pas répondu. L'artisan écrit à son agent : « Relance Mme Martin pour le devis D-2026-042, sur un ton cordial. »

Les chapitres de ce fichier partent tous d'un même petit projet autonome, `relance/`, que le chapitre 15 crée et que les suivants font évoluer. Il n'a pas besoin des projets des fichiers précédents. Il tourne sur des modèles simulés (`sdk: fake`) : aucune clé, aucun réseau, et les sorties que vous verrez sont celles que ce guide a obtenues en les exécutant. Seul l'exemple 15.4 fait exception : il parle à un faux fournisseur écrit en Python et lancé en local, sans clé ni réseau extérieur.

```text
relance/
├── loom.yaml            modèles, stockage, modules à importer
├── outils.py            chercher_devis, envoyer_email
├── agents/
│   └── relance.yaml     l'agent : un outil de recherche, un rôle de rédaction, un envoi à approuver
└── prompts/
    ├── relance.md       prompt système de l'orchestrateur
    └── rediger_relance.md  prompt système du rôle rédacteur
```

Trois conventions valent pour tout le fichier.

- **Outillage.** Tout passe par `uv` : `uv init`, `uv add`, `uv run`, `uv run loom …`. Le dossier de travail est toujours `relance/`.
- **Identifiants.** Les identifiants de run (`01a1266b-…`) changent à chaque exécution. Remplacez ceux des exemples par les vôtres. Les durées exactes et les délais tirés au hasard varient aussi un peu.
- **Chemins.** `/chemin/vers/relance` remplace le chemin réel du dossier dans les sorties.

La version de Loom-IA utilisée pour tout exécuter est la **2.0.0**, celle de PyPI. Quand elle s'écarte de ce que dit la documentation, le texte le signale (encadrés « Écart »).

---

## 15. Résilience des modèles : timeouts, retry, secours, disjoncteur

### Ce qu'il faut comprendre avant de commencer

Un modèle de langage est un service distant. Il sature (HTTP 529), il coupe la connexion, il épuise le quota du mois, il voit sa clé révoquée. Aucune de ces pannes n'a de rapport avec le métier de l'artisan, et aucune ne doit lui valoir un « erreur 500 » à l'écran ni, pire, un e-mail envoyé deux fois à cause d'une reprise mal gérée.

Loom-IA traite ces pannes en six mécanismes, tous déclarés dans `loom.yaml` ou dans l'agent. Le code de votre agent n'en sait rien.

| Mécanisme | Où | Rôle |
|---|---|---|
| `timeouts` | modèle | Trois délais : `first_token` (attente du premier morceau), `idle` (silence entre deux morceaux), `total` (appel entier). Défauts : 60 s, 60 s, 300 s. `null` retire un délai. |
| `retry` | modèle | Nouvelles tentatives sur les erreurs passagères. `max_attempts` compte la première tentative (défaut 3), `initial_delay` 1 s, `multiplier` 2, `max_delay` 30 s. Le délai réel est tiré au hasard entre la moitié et la totalité de la valeur calculée. |
| classement des erreurs | moteur | Décide, selon la nature de l'erreur, de retenter, de basculer ou d'abandonner. |
| `fallbacks` | `main`, rôle, juge | Modèles de secours, dans l'ordre. |
| `circuit_breaker` | modèle | Écarte un modèle en panne pour tous les runs de l'instance. Défaut : 5 échecs, puis 60 s d'écart. `null` le retire. |
| `capabilities` | modèle | Ce que le modèle sait faire. Loom-IA le vérifie **au chargement**, pas en plein run. |

Le classement des erreurs est la clé de l'ensemble :

| Erreur | Exemples | Ce que fait Loom-IA |
|---|---|---|
| passagère (`transient`, `overloaded`) | 429, 5xx, délai dépassé, réseau coupé | Retente avec un délai exponentiel (en respectant `Retry-After`), puis bascule sur le secours quand les tentatives sont épuisées. |
| quota (`quota_exhausted`) | crédit épuisé | Bascule **tout de suite**, sans retenter : attendre ne servirait à rien. |
| fenêtre de contexte (`context_overflow`) | requête trop longue | Bascule vers le premier secours dont la fenêtre déclarée est plus grande. |
| authentification, requête invalide, contenu filtré (`auth`, `invalid_request`, `content_filtered`) | clé révoquée, schéma refusé | **Jamais** retentée, jamais basculée : le run échoue et le dit. |

Pourquoi ne pas basculer sur une erreur d'authentification ? Parce qu'elle signale une faute de configuration, que le secours masquerait : l'artisan croirait que tout va bien alors que sa clé principale est morte depuis une semaine.

Autres points de détail utiles :

- La politique de retry est celle de Loom-IA, **pas** celle des SDK des fournisseurs, qui est désactivée. Chaque tentative ratée est écrite dans le journal (`model.retried`).
- Un `Retry-After` envoyé par le fournisseur remplace le calcul du délai. S'il dépasse `max_delay`, Loom-IA renonce à retenter et passe au secours.
- Un run qui a basculé **reste** sur le secours jusqu'à sa fin. Le run suivant repart du modèle principal.
- Le streaming est la primitive de base : chaque appel est un flux, et une réponse complète n'est que ce flux rassemblé. Un modèle qui ne sait pas streamer se déclare `capabilities: {streaming: false}` ; Loom-IA fait alors un appel complet et rejoue la réponse en flux simulé.
- La vérification des capacités couvre les outils (`tools`), la vision, le raisonnement (`thinking`), le JSON natif (`native_json`) et la fenêtre de contexte (`context_window`, contrôlée avant chaque appel).

### Exemple 15.1 : le fournisseur est saturé, le secours prend le relais

**Pourquoi.** Un mardi matin, le fournisseur de votre modèle principal affiche « overloaded ». L'artisan, lui, veut relancer Mme Martin avant de partir en chantier. Il ne doit rien remarquer.

**Objectif.** Créer le projet `relance/`, lui déclarer un modèle de secours, et voir un run traverser une panne sans échouer.

**Mise en place.**

```bash
uv init --bare --python 3.14 relance
cd relance
uv add "loom-ia[anthropic,openai,http,mcp,sqlite,crypto,postgres,redis,rabbitmq]"
mkdir agents prompts
```

Les extras installés ici servent à tout le fichier : `http` pour REST, `mcp` pour le serveur MCP, `sqlite` pour le magasin d'idempotence, `crypto` pour le chiffrement, `postgres`, `redis` et `rabbitmq` pour les services des chapitres 23 et 24, `anthropic` et `openai` pour les vrais modèles.

`outils.py` : deux outils. `chercher_devis` lit les données de l'entreprise ; `envoyer_email` a un effet de bord irréversible, donc soumis à approbation. Pour que l'on puisse **constater** qu'un e-mail est parti, il ajoute une ligne à `data/emails.log`.

```python
from pathlib import Path

from loom_ia.tools import tool

DEVIS = {
    "D-2026-042": {
        "numero": "D-2026-042",
        "client": "Mme Martin",
        "email": "mme.martin@example.fr",
        "objet": "Remplacement du chauffe-eau",
        "montant_ttc": 1840.0,
        "envoye_le": "2026-09-03",
    },
}


@tool
def chercher_devis(numero: str) -> dict:
    """Retrouve un devis par son numéro : client, e-mail, objet, montant TTC, date d'envoi."""
    if numero not in DEVIS:
        raise KeyError(f"devis {numero} introuvable")
    return DEVIS[numero]


@tool(side_effects="irreversible", approval="always")
def envoyer_email(destinataire: str, objet: str, corps: str) -> str:
    """Envoie un e-mail au client. Action irréversible, soumise à approbation."""
    journal = Path("data/emails.log")
    journal.parent.mkdir(exist_ok=True)
    with journal.open("a", encoding="utf-8") as f:
        f.write(f"{destinataire} | {objet}\n")
    return f"E-mail envoyé à {destinataire}"
```

`prompts/relance.md` :

```markdown
Tu es l'assistant de la Plomberie Dupont. Pour relancer un client :
1. retrouve le devis avec `chercher_devis` ;
2. fais rédiger l'e-mail par le rôle `rediger_relance` ;
3. envoie-le avec `envoyer_email`.
Ne calcule et n'invente jamais un montant : reprends celui du devis.
```

`prompts/rediger_relance.md` :

```markdown
Tu rédiges, pour la Plomberie Dupont, de courts e-mails de relance de devis,
en français. Réponds en JSON : {"objet": "...", "corps": "..."}.
```

`agents/relance.yaml` : l'orchestrateur `main` déclare sa chaîne (`model` puis `fallbacks`). Le rôle `rediger_relance` ne reçoit que la demande de l'artisan, le devis trouvé et le ton choisi.

```yaml
name: relance
description: Relance un client pour un devis resté sans réponse.

main:
  model: PRINCIPAL
  fallbacks: [SECOURS]
  system_file: relance.md

max_iterations: 8

tools:
  - python: chercher_devis
  - python: envoyer_email

roles:
  - name: rediger_relance
    description: Rédige l'e-mail de relance d'un devis.
    model: REDACTEUR
    system_file: rediger_relance.md
    input_schema:
      type: object
      properties:
        ton: {type: string, description: "cordial, ferme, bref…"}
      required: [ton]
    context: [user_input, {tool_results: [chercher_devis]}]
    input_template: |-
      Demande de l'artisan : {{ context.user_input }}
      Devis : {{ context.tool_results.chercher_devis }}
      Ton : {{ args.ton }}
```

`loom.yaml` : trois modèles simulés. `PRINCIPAL` est en panne permanente (`error: overloaded` à chaque appel, c'est la façon du modèle simulé de jouer une saturation). `SECOURS` joue le scénario complet de la relance, que l'on retrouvera dans tous les chapitres. `REDACTEUR` rédige le texte de l'e-mail.

```yaml
version: 1

imports: [outils]
agents_dir: agents/
prompts_dir: prompts/

models:
  - id: PRINCIPAL
    sdk: fake
    model: fake-principal
    params:
      script:
        - error: overloaded            # le fournisseur est saturé, à chaque appel
  - id: SECOURS
    sdk: fake
    model: fake-secours
    params:
      script:
        - text: Je commence par retrouver le devis.
          tool_calls:
            - {name: chercher_devis, arguments: {numero: D-2026-042}}
        - text: Je confie la rédaction au rôle prévu.
          tool_calls:
            - {name: rediger_relance, arguments: {ton: cordial}}
        - text: J'envoie la relance.
          tool_calls:
            - name: envoyer_email
              arguments:
                destinataire: mme.martin@example.fr
                objet: Votre devis D-2026-042
                corps: "Bonjour Madame Martin, je me permets de revenir vers vous au sujet du devis D-2026-042 (1 840 €) envoyé le 3 septembre. Restant à votre disposition, cordialement."
        - text: La relance du devis D-2026-042 est partie chez Mme Martin.
  - id: REDACTEUR
    sdk: fake
    model: fake-redacteur
    params:
      script:
        - text: '{"objet": "Votre devis D-2026-042", "corps": "Bonjour Madame Martin, je me permets de revenir vers vous au sujet du devis D-2026-042 (1 840 €) envoyé le 3 septembre. Restant à votre disposition, cordialement."}'

telemetry:
  logging: {level: WARNING}

storage:
  events: {backend: jsonl, path: data}
```

Le modèle simulé joue sa liste de réponses dans l'ordre : une par tour, en repartant de la première à chaque nouvelle demande de l'utilisateur. Un tour dont la réponse est `error: …` lève cette erreur à **chaque** tentative, ce qui représente une vraie panne durable.

**Exécution.** Vérifiez d'abord que la configuration se charge, puis lancez le run :

```bash
uv run loom validate
```

```text
Config     : loom.yaml
Profil     : aucun (ni --profile, ni LOOM_PROFILE, ni 'profile:') ; surcharges : aucune
Modèles    : PRINCIPAL, SECOURS, REDACTEUR
Agents     : relance
Outils     : chercher_devis, envoyer_email
Paquets    : forge (loom-ia 2.0.0) (groupe loom_ia.tools)
Journal    : jsonl (/chemin/vers/relance/data)
Artefacts  : local (/chemin/vers/relance/data/.artifacts)
Idempotence: journal
File       : asyncio
Bus        : memory (les nouvelles ne sortent pas de ce process)
Chiffrement: aucun (contenus en clair au repos)
Rétention  : aucune (rien ne s'efface)
Clés d'API : aucune (API REST ouverte)
  relance : modèle PRINCIPAL → SECOURS, 2 outil(s) Python, rôle rediger_relance (REDACTEUR)

1 agent(s) monté(s) sans erreur.
```

La dernière ligne de détail montre la chaîne de secours : `PRINCIPAL → SECOURS`.

```bash
uv run loom run relance "Relance Mme Martin pour le devis D-2026-042, sur un ton cordial."
```

```text
2026-10-10 17:25:48 WARNING  loom_ia.engine.model_call — Tentative 1/3 du modèle PRINCIPAL ratée (overloaded) : nouvel essai dans 0.7 s
2026-10-10 17:25:49 WARNING  loom_ia.engine.model_call — Tentative 2/3 du modèle PRINCIPAL ratée (overloaded) : nouvel essai dans 1.2 s
—

Statut     : paused · itérations : 3 · tokens : 2779/340 · coût : 0.0000 $
Run        : 01a1266b-9bdf-7341-ba83-db843d5cff44
En attente : envoyer_email (fake_2_0) — loom approve 01a1266b-9bdf-7341-ba83-db843d5cff44 --call fake_2_0
```

Le run est en pause parce que `envoyer_email` attend l'approbation de l'artisan (chapitre 9). Avant d'y arriver, il a perdu 2 secondes en tentatives sur `PRINCIPAL`, puis a basculé. Les deux avertissements viennent du niveau de log `WARNING`. Lisez ce qui s'est passé avec `inspect` :

```bash
uv run loom inspect 01a1266b-9bdf-7341-ba83-db843d5cff44
```

```text
Run        : 01a1266b-9bdf-7341-ba83-db843d5cff44 (agent relance, client default)
Session    : 01a1266b-9bdf-7341-ba83-db843d5cff44
Statut     : paused, 3 itération(s) — run inachevé : durées provisoires
Usage      : 2779 → 340 tokens, 0.000000 $, 2.0 s de pilotage

run relance — inachevé, 2.0 s (ouvert)
  étape 1
    · tentative 1 ratée (overloaded), nouvel essai
    · tentative 2 ratée (overloaded), nouvel essai
    · secours : PRINCIPAL → SECOURS
    modèle fake-secours (main) — 2.0 s, 616 → 70 tokens, 0.000000 $
      · répond    : Je commence par retrouver le devis.
      · appelle   : chercher_devis({"numero": "D-2026-042"})
  étape 2
    outil chercher_devis — 2 ms
      · arguments : {"numero": "D-2026-042"}
      · résultat  : {"numero": "D-2026-042", "client": "Mme Martin", "email": "mme.martin@example.fr", "objet": "Remp…
  étape 3
    modèle fake-secours (main) — 1 ms, 857 → 69 tokens, 0.000000 $
      · répond    : Je confie la rédaction au rôle prévu.
      · appelle   : rediger_relance({"ton": "cordial"})
  étape 4
    rôle rediger_relance — 4 ms
      · arguments : {"ton": "cordial"}
      · résultat  : {"objet": "Votre devis D-2026-042", "corps": "Bonjour Madame Martin, je me permets de revenir ver…
      appels du rôle rediger_relance
        modèle fake-redacteur (rediger_relance) — 0 ms, 233 → 79 tokens, 0.000000 $
          · répond    : {"objet": "Votre devis D-2026-042", "corps": "Bonjour Madame Martin, je me permets de revenir ver…
  étape 5
    modèle fake-secours (main) — 1 ms, 1073 → 122 tokens, 0.000000 $
      · répond    : J'envoie la relance.
      · appelle   : envoyer_email({"destinataire": "mme.martin@example.fr", "objet": "Votre devis D-2026-042", "corps…
  étape 6
    approbation pour envoyer_email — en attente

Bilan      : 4 appel(s) de modèle, 2 appel(s) d'outil, 1 approbation(s)
```

Dans l'étape 1, les trois lignes `·` racontent la panne : deux tentatives ratées, puis le passage au secours. Les étapes 3 et 5, elles, sont servies par `fake-secours` : le run est resté sur le secours. Le coût est à `0.000000 $` parce qu'aucun modèle n'a de `pricing` : on y remédie au chapitre 16.

**À retenir.**

- Un `fallbacks:` sur `main` suffit pour qu'une panne passagère ne se voie plus. Le même `fallbacks:` existe sur un rôle et sur un juge.
- Tout est dans le journal : `model.retried` pour chaque tentative ratée, `model.fell_back` pour la bascule, avec le motif.
- Chaque tentative coûte du temps : avec les défauts (3 tentatives, 1 s puis 2 s de délai), une saturation prolongée retarde le run d'environ 1,5 à 3 s avant la bascule. Pour un service où la réactivité compte, baissez `retry.max_attempts` du modèle principal.
- **Piège.** Le secours est facturé à **son** tarif, et c'est lui qui répond pour tout le reste du run. Un secours bon marché mais moins précis peut dégrader un run entier à cause d'une seule saturation d'une seconde. Choisissez un secours dont vous acceptez la qualité.

### Exemple 15.2 : ce qui est retenté, ce qui bascule, ce qui échoue

**Pourquoi.** Vous voulez vérifier, avant la mise en production, que votre configuration réagit comme vous le pensez à chaque sorte de panne. Les tester sur de vrais fournisseurs serait long et coûteux, et peu reproductible.

**Objectif.** Jouer les trois classes d'erreur côte à côte : saturation, quota épuisé, clé refusée.

**Mise en place.** Une seconde configuration, `pannes.yaml`, avec ses propres agents dans `agents_pannes/`. Elle n'altère pas le projet principal. Elle réutilise l'option globale `--config`.

```bash
mkdir agents_pannes
```

`pannes.yaml` (le chemin du journal est `data-pannes`, pour ne pas mélanger les runs d'essai avec les vrais) :

```yaml
version: 1

agents_dir: agents_pannes/

models:
  - id: SATURE
    sdk: fake
    model: fake-sature
    retry: {max_attempts: 3, initial_delay: 0.2, max_delay: 1}
    params: {script: [{error: overloaded}]}
  - id: SANS_QUOTA
    sdk: fake
    model: fake-sans-quota
    params: {script: [{error: quota_exhausted}]}
  - id: CLE_REFUSEE
    sdk: fake
    model: fake-cle-refusee
    params: {script: [{error: auth}]}
  - id: SECOURS
    sdk: fake
    model: fake-secours
    params:
      script:
        - text: Le secours répond à la place du modèle principal.

telemetry:
  logging: {level: WARNING}

storage:
  events: {backend: jsonl, path: data-pannes}
```

Trois agents, un par panne. Le nom d'un agent n'accepte que lettres, chiffres, `_` et `-` : pas d'accent.

`agents_pannes/panne-saturation.yaml` :

```yaml
name: panne-saturation
description: Agent d'essai dont le modèle principal est saturé.
main:
  model: SATURE
  fallbacks: [SECOURS]
  system: Tu es un assistant de test.
```

`agents_pannes/panne-quota.yaml` :

```yaml
name: panne-quota
description: Agent d'essai dont le modèle principal n'a plus de quota.
main:
  model: SANS_QUOTA
  fallbacks: [SECOURS]
  system: Tu es un assistant de test.
```

`agents_pannes/panne-auth.yaml` :

```yaml
name: panne-auth
description: Agent d'essai dont le modèle principal refuse la clé.
main:
  model: CLE_REFUSEE
  fallbacks: [SECOURS]
  system: Tu es un assistant de test.
```

**Exécution.**

```bash
uv run loom --config pannes.yaml run panne-saturation "Bonjour"
```

```text
2026-10-10 17:26:19 WARNING  loom_ia.engine.model_call — Tentative 1/3 du modèle SATURE ratée (overloaded) : nouvel essai dans 0.2 s
2026-10-10 17:26:20 WARNING  loom_ia.engine.model_call — Tentative 2/3 du modèle SATURE ratée (overloaded) : nouvel essai dans 0.4 s
Le secours répond à la place du modèle principal.

Statut     : completed · itérations : 1 · tokens : 84/37 · coût : 0.0000 $
Run        : 01a1266c-14d1-7026-b9e7-a7fc564f94af
```

Deux nouvelles tentatives, un délai qui double (0,2 s puis 0,4 s, à l'aléa près), puis le secours répond.

```bash
uv run loom --config pannes.yaml run panne-quota "Bonjour"
```

```text
Le secours répond à la place du modèle principal.

Statut     : completed · itérations : 1 · tokens : 84/37 · coût : 0.0000 $
Run        : 01a1266c-19b4-73d4-8bb3-f75f1ee6d217
```

Aucune tentative : un quota épuisé ne se règle pas en attendant, la bascule est immédiate.

```bash
uv run loom --config pannes.yaml run panne-auth "Bonjour"
```

```text
2026-10-10 17:26:21 ERROR    loom_ia.engine.loop — Échec de l'appel au modèle CLE_REFUSEE : model.auth — Panne simulée par le script du modèle 'CLE_REFUSEE' (réponse n°1 : auth) [run_id=01a1266c-1c2b-7214-a750-9cac9c6c28a4 span_id=01a1266c-1c39-7050-b12c-474cbf753a7e]
Panne simulée par le script du modèle 'CLE_REFUSEE' (réponse n°1 : auth)

Statut     : failed · itérations : 0 · tokens : 0/0 · coût : 0.0000 $
Run        : 01a1266c-1c2b-7214-a750-9cac9c6c28a4
Erreur     : Panne simulée par le script du modèle 'CLE_REFUSEE' (réponse n°1 : auth) (model.auth)
```

Le secours **n'est pas appelé**. Le type d'erreur du run est `model.auth`, que l'on peut tester dans un script (`result.error_type`) ou dans une supervision.

```bash
uv run loom --config pannes.yaml inspect 01a1266c-19b4-73d4-8bb3-f75f1ee6d217
```

```text
Run        : 01a1266c-19b4-73d4-8bb3-f75f1ee6d217 (agent panne-quota, client default)
Session    : 01a1266c-19b4-73d4-8bb3-f75f1ee6d217
Statut     : completed, 1 itération(s)
Usage      : 84 → 37 tokens, 0.000000 $, 4 ms de pilotage

run panne-quota — completed, 8 ms, 0.000000 $
  étape 1
    · secours : SANS_QUOTA → SECOURS
    modèle fake-secours (main) — 1 ms, 84 → 37 tokens, 0.000000 $
      · répond    : Le secours répond à la place du modèle principal.

Réponse finale :
  Le secours répond à la place du modèle principal.
Bilan      : 1 appel(s) de modèle, 0 appel(s) d'outil
```

**À retenir.**

| Panne simulée | Tentatives | Secours appelé | Statut du run |
|---|---|---|---|
| `overloaded` (ou `transient`) | 3 (réglable avec `retry.max_attempts`) | oui, après les tentatives | `completed` |
| `quota_exhausted` | 0 | oui, tout de suite | `completed` |
| `auth` | 0 | **non** | `failed`, `error_type: model.auth` |

- Réglez `retry.max_attempts: 1` si vous préférez basculer immédiatement plutôt que d'attendre : l'attente est alors nulle.
- **Piège.** Une `auth` qui échoue ne bascule pas, même si vous avez déclaré un secours. C'est voulu (voir plus haut), mais surprenant la première fois : si votre secours ne se déclenche pas, regardez d'abord `error_type`.
- Les délais d'attente (`timeouts`) suivent la même logique : un délai dépassé est classé `transient`, donc retenté puis basculé. Le modèle simulé répond instantanément, il ne peut pas dépasser un délai : l'exemple 15.4 les éprouve contre un faux fournisseur local, où l'on voit un `first_token` ou un `idle` dépassé échouer ou basculer sur le secours.

### Exemple 15.3 : le disjoncteur et les capacités déclarées

**Pourquoi.** Quand un fournisseur est durablement tombé, chaque run de la journée perd quelques secondes à retenter avant de basculer, et surcharge encore un service déjà à genoux. Le disjoncteur écarte le modèle malade **pour tous les runs** pendant un temps donné.

**Objectif.** Voir le disjoncteur s'ouvrir, puis vérifier qu'une configuration qui demande à un secours ce qu'il ne sait pas faire est refusée au chargement.

**Mise en place.** On ajoute à `pannes.yaml` un modèle `FRAGILE`, qui échoue à chaque appel, sans nouvelle tentative, avec un disjoncteur à seuil bas (2 échecs, 60 s d'écart). Insérez-le avant `SECOURS` :

```yaml
  - id: FRAGILE
    sdk: fake
    model: fake-fragile
    retry: {max_attempts: 1}                       # pas de nouvel essai : chaque échec compte
    circuit_breaker: {failures: 2, cooldown: 60}   # écarté 60 s après 2 échecs
    params: {script: [{error: transient}]}
```

`agents_pannes/panne-disjoncteur.yaml` :

```yaml
name: panne-disjoncteur
description: Agent d'essai dont le modèle principal échoue à chaque appel.
main:
  model: FRAGILE
  fallbacks: [SECOURS]
  system: Tu es un assistant de test.
```

Le disjoncteur est commun aux runs d'**une même instance** `Loom`. Une commande `loom run` ne vit que le temps d'un run : il faut donc un script Python qui garde l'instance ouverte. `disjoncteur.py` :

```python
import asyncio

from loom_ia.access import Loom


async def main() -> None:
    # Une seule instance Loom : le disjoncteur est commun à tous ses runs.
    async with Loom.from_config("pannes.yaml") as loom:
        for numero in range(1, 5):
            result = await loom.run("panne-disjoncteur", f"Essai {numero}")
            vus = [
                f"{event.type}({getattr(event.payload, 'reason', '')})".replace("()", "")
                for event in await loom.events(result.run_id)
                if event.type in ("model.retried", "model.fell_back", "circuit.opened")
            ]
            print(f"essai {numero} : {result.status} — {' → '.join(vus) or 'rien'}")


asyncio.run(main())
```

**Exécution.**

```bash
rm -rf data-pannes
uv run python disjoncteur.py
```

```text
essai 1 : completed — model.fell_back(transient)
essai 2 : completed — circuit.opened → model.fell_back(transient)
essai 3 : completed — model.fell_back(circuit_open)
essai 4 : completed — model.fell_back(circuit_open)
```

Les deux premiers essais appellent vraiment `FRAGILE` et échouent. Le second ouvre le disjoncteur (`circuit.opened`). Les essais 3 et 4 **ne tentent même plus** le modèle : motif `circuit_open`, bascule immédiate. Au bout de 60 s, un seul essai est permis : réussi, le disjoncteur se referme ; raté, il se rouvre.

Passons aux capacités. Copiez `loom.yaml` dans `loom.capacites.yaml`, et ajoutez `capabilities: {tools: false}` au modèle `SECOURS` :

```yaml
  - id: SECOURS
    sdk: fake
    model: fake-secours
    capabilities: {tools: false}
    params:
      # … le reste est inchangé
```

```bash
uv run loom --config loom.capacites.yaml validate
```

```text
Configuration : /chemin/vers/relance/loom.capacites.yaml: (racine) — Agent 'relance' : il appelle des outils, mais le modèle de secours 'SECOURS' ne sait pas le faire (capabilities.tools: false)
```

La commande sort en code 2 : la configuration est refusée **avant** qu'un seul run ne soit lancé. Supprimez ensuite `loom.capacites.yaml`.

**À retenir.**

- `circuit_breaker: {failures: N, cooldown: S}` est actif par défaut (5 échecs, 60 s) pour chaque modèle. `circuit_breaker: null` le retire.
- Le disjoncteur est **par instance** `Loom`. Un service `loom serve` a une instance : tous ses runs en profitent. Plusieurs workers ont chacun la leur.
- Un modèle qui n'a pas `capabilities.tools: true` ne peut pas piloter un agent qui a des outils, ni juger, ni servir de secours à l'un d'eux. L'erreur apparaît au chargement.
- **Piège.** Un secours qui cumule le même fournisseur que le principal ne protège pas d'une panne du fournisseur. Choisissez un secours chez un autre fournisseur, ou au moins sur un autre endpoint.

### Exemple 15.4 : latence « voix » : le premier morceau, `first_token` et `idle`

**Pourquoi.** La Plomberie Dupont veut que son agent décroche le téléphone quand l'artisan est en chantier. Au téléphone, trois secondes de silence font raccrocher. Ce qui compte n'est plus la durée de la réponse, mais le temps avant ses **premiers mots** : tant qu'ils arrivent vite, le reste peut suivre au rythme de la parole.

**Objectif.** Mesurer le temps jusqu'au premier `TextDelta` avec `stream()` (chapitre 4), voir ce que font les délais `first_token` et `idle` d'un modèle quand le fournisseur tarde ou se tait, et voir qu'un contrôle sur la réponse finale peut retarder les premiers mots. L'exemple dit aussi, sans détour, ce que le modèle simulé permet de vérifier : presque rien sur la latence.

**Ce que la documentation promet, et ce qu'elle ne promet pas.** `docs/fonctions.md` range la « latence adaptée à la voix (premier token rapide) » parmi les fonctions, et en donne les ingrédients : le streaming est la primitive de base, trois délais (`first_token`, `idle`, `total`) encadrent chaque appel de modèle, et le réglage `stream_output: live` est exigé pour la voix. Rien dans le dépôt n'annonce une latence chiffrée, et Loom-IA ne traite pas l'audio : le transport vocal est confié à un composant extérieur (Pipecat, d'après la conception) qui appelle `loom.stream()`. La démo voix prévue au planning a d'ailleurs été abandonnée. Il n'y a donc ici ni audio, ni garantie : seulement des mesures que vous pouvez refaire.

**Mise en place.** Un modèle simulé (`sdk: fake`) répond instantanément : il ne peut ni tarder ni se taire. Pour éprouver les délais pour de bon, ce chapitre fait tourner un **faux fournisseur** local, qui parle comme l'API Chat Completions d'OpenAI (en flux SSE) et dont on règle les lenteurs par le nom du modèle demandé. C'est un petit serveur de la bibliothèque standard, sans extra. `faux_fournisseur.py` :

```python
"""Faux fournisseur OpenAI-compatible, local, dont on règle les lenteurs par le nom du modèle."""
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PHRASES = ["Bonjour, ", "la Plomberie Dupont ", "à votre écoute. ", "Comment puis-je ", "vous aider ?"]

# modèle demandé -> (attente avant le 1er morceau, attente entre deux morceaux, morceau où le silence tombe)
SCENARIOS = {
    "rapide": (0.3, 0.1, None),
    "depart-lent": (3.0, 0.1, None),
    "silence": (0.1, 0.1, 2),
}


def morceau(delta: dict, fin: str | None = None) -> bytes:
    corps = {"id": "x", "object": "chat.completion.chunk", "created": 0, "model": "m",
             "choices": [{"index": 0, "delta": delta, "finish_reason": fin}]}
    return b"data: " + json.dumps(corps).encode() + b"\n\n"


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        demande = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        avant, entre, silence = SCENARIOS[demande["model"]]
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        time.sleep(avant)
        try:
            for i, phrase in enumerate(PHRASES):
                if i == silence:
                    time.sleep(3.0)
                delta = {"role": "assistant", "content": phrase} if i == 0 else {"content": phrase}
                self.wfile.write(morceau(delta))
                self.wfile.flush()
                time.sleep(entre)
            self.wfile.write(morceau({}, "stop"))
            usage = {"id": "x", "object": "chat.completion.chunk", "created": 0, "model": "m", "choices": [],
                     "usage": {"prompt_tokens": 20, "completion_tokens": 12, "total_tokens": 32}}
            self.wfile.write(b"data: " + json.dumps(usage).encode() + b"\n\ndata: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass  # le client a abandonné (délai dépassé) : rien à signaler

    def log_message(self, *args) -> None:
        pass


ThreadingHTTPServer(("127.0.0.1", 18501), Handler).serve_forever()
```

Trois comportements : `rapide` (premier morceau après 0,3 s, puis un morceau toutes les 0,1 s), `depart-lent` (premier morceau après 3 s) et `silence` (premiers morceaux vite, puis un silence de 3 s au milieu de la réponse).

Une configuration à part, `voix.yaml`, avec son dossier d'agents `agents_voix/` :

```bash
mkdir agents_voix
```

```yaml
version: 1

agents_dir: agents_voix/

models:
  - id: RAPIDE
    sdk: openai
    api: chat
    base_url: http://127.0.0.1:18501/v1
    api_key_env: FAUX_FOURNISSEUR_CLE
    model: rapide
    retry: {max_attempts: 1}
  - id: DEPART_LENT
    sdk: openai
    api: chat
    base_url: http://127.0.0.1:18501/v1
    api_key_env: FAUX_FOURNISSEUR_CLE
    model: depart-lent
    timeouts: {first_token: 1}
    retry: {max_attempts: 1}
  - id: SILENCE
    sdk: openai
    api: chat
    base_url: http://127.0.0.1:18501/v1
    api_key_env: FAUX_FOURNISSEUR_CLE
    model: silence
    timeouts: {idle: 1}
    retry: {max_attempts: 1}
  - id: SECOURS
    sdk: fake
    model: fake-secours
    params:
      script:
        - text: Bonjour, ici la Plomberie Dupont, le secours vous répond.

telemetry:
  logging: {level: WARNING}

storage:
  events: {backend: jsonl, path: data-voix}
```

`DEPART_LENT` n'attend le premier morceau qu'une seconde (`timeouts: {first_token: 1}`), `SILENCE` ne tolère qu'une seconde de silence entre deux morceaux (`timeouts: {idle: 1}`). `retry: {max_attempts: 1}` supprime les nouvelles tentatives, pour que chaque délai dépassé se voie tout de suite. Les trois modèles OpenAI-compatibles lisent leur clé dans `FAUX_FOURNISSEUR_CLE` ; le faux serveur ne la vérifie pas. Les valeurs d'une seconde sont là pour la démonstration : elles ne sont pas des recommandations.

Sept agents, un par fichier de `agents_voix/`. Ils ne diffèrent que par leur modèle et leur réglage de diffusion :

`agents_voix/voix-simule.yaml` :

```yaml
name: voix-simule
description: Standard téléphonique joué par le modèle simulé.
main:
  model: SECOURS
  system: Tu réponds au téléphone pour la Plomberie Dupont, en une phrase.
```

`agents_voix/voix-rapide.yaml` :

```yaml
name: voix-rapide
description: Standard téléphonique, modèle RAPIDE.
main:
  model: RAPIDE
  system: Tu réponds au téléphone pour la Plomberie Dupont, en une phrase.
```

`agents_voix/voix-depart-lent.yaml` :

```yaml
name: voix-depart-lent
description: Standard téléphonique, modèle DEPART_LENT.
main:
  model: DEPART_LENT
  system: Tu réponds au téléphone pour la Plomberie Dupont, en une phrase.
```

`agents_voix/voix-silence.yaml` :

```yaml
name: voix-silence
description: Standard téléphonique, modèle SILENCE.
main:
  model: SILENCE
  system: Tu réponds au téléphone pour la Plomberie Dupont, en une phrase.
```

`agents_voix/voix-secours.yaml` :

```yaml
name: voix-secours
description: Standard téléphonique dont le départ lent bascule sur un secours.
main:
  model: DEPART_LENT
  fallbacks: [SECOURS]
  system: Tu réponds au téléphone pour la Plomberie Dupont, en une phrase.
```

`agents_voix/voix-controle.yaml` :

```yaml
name: voix-controle
description: Standard téléphonique dont la réponse est contrôlée (200 caractères maximum).
main:
  model: RAPIDE
  system: Tu réponds au téléphone pour la Plomberie Dupont, en une phrase.
output:
  max_chars: 200
```

`agents_voix/voix-controle-live.yaml` :

```yaml
name: voix-controle-live
description: Comme voix-controle, mais la réponse part au fil de l'eau.
main:
  model: RAPIDE
  system: Tu réponds au téléphone pour la Plomberie Dupont, en une phrase.
output:
  max_chars: 200
stream_output: live
```

`voix-controle` ajoute un contrat de sortie (`output: {max_chars: 200}`, chapitre 11), donc un contrôle sur la réponse finale. `voix-controle-live` y ajoute `stream_output: live`.

`latence.py` lance trois fois l'agent donné en argument, avec `stream()`, et note le temps écoulé jusqu'au premier `TextDelta` et jusqu'à la fin du flux. Trois essais, parce que le premier ne ressemble pas aux autres (voir plus bas).

```python
import asyncio
import sys
import time

from loom_ia.access import Loom
from loom_ia.core.model import TextDelta


async def main(agent: str) -> None:
    async with Loom.from_config("voix.yaml") as loom:
        for essai in (1, 2, 3):
            run_id = f"latence-{agent}-{essai}"
            depart = time.perf_counter()
            premier = None
            async for item in loom.stream(agent, "Allô ?", run_id=run_id):
                if isinstance(item, TextDelta) and premier is None:
                    premier = time.perf_counter() - depart
            total = time.perf_counter() - depart
            result = await loom.result(run_id)
            avant = "aucun" if premier is None else f"{premier:.2f} s"
            print(f"{agent:<19} essai {essai} : premier TextDelta {avant:<7} total {total:.2f} s  {result.status}")
            if result.error:
                print("  erreur :", result.error_type, "-", result.error)


asyncio.run(main(sys.argv[1]))
```

**Exécution.** Dans un premier terminal, le faux fournisseur :

```bash
uv run python faux_fournisseur.py
```

Dans un second, la clé factice, puis l'agent simulé, pour savoir ce que le modèle simulé mesure :

```bash
export FAUX_FOURNISSEUR_CLE=cle-de-test
uv run python latence.py voix-simule
```

```text
voix-simule         essai 1 : premier TextDelta 0.01 s  total 0.02 s  completed
voix-simule         essai 2 : premier TextDelta 0.01 s  total 0.01 s  completed
voix-simule         essai 3 : premier TextDelta 0.00 s  total 0.01 s  completed
```

Quelques millisecondes : c'est le coût de la tuyauterie de Loom-IA, pas celui d'un fournisseur. Avec `sdk: fake`, **la latence réelle d'un modèle n'est pas vérifiable** ; ce que le modèle simulé permet de vérifier, c'est que le flux émet bien des `TextDelta` avant la fin du run. Avec le faux fournisseur, un modèle qui met 0,3 s à démarrer :

```bash
uv run python latence.py voix-rapide
```

```text
voix-rapide         essai 1 : premier TextDelta 0.73 s  total 1.23 s  completed
voix-rapide         essai 2 : premier TextDelta 0.31 s  total 0.82 s  completed
voix-rapide         essai 3 : premier TextDelta 0.31 s  total 0.82 s  completed
```

Le premier `TextDelta` arrive à 0,31 s (les 0,3 s du faux serveur), alors que la réponse ne se termine qu'à 0,82 s : l'appelant peut commencer à parler avant que le modèle ait fini. Le tout premier essai de l'instance est plus lent d'environ 0,4 s. La cause n'a pas été cherchée ici ; retenez que la première requête d'une instance neuve n'est pas représentative.

Premier morceau trop lent, avec `first_token: 1` :

```bash
uv run python latence.py voix-depart-lent
```

```text
voix-depart-lent    essai 1 : premier TextDelta aucun   total 1.36 s  failed
  erreur : model.transient - Délai dépassé : aucun premier morceau en 1 s
voix-depart-lent    essai 2 : premier TextDelta aucun   total 1.01 s  failed
  erreur : model.transient - Délai dépassé : aucun premier morceau en 1 s
voix-depart-lent    essai 3 : premier TextDelta aucun   total 1.01 s  failed
  erreur : model.transient - Délai dépassé : aucun premier morceau en 1 s
```

Au bout d'une seconde sans un seul morceau, l'appel est abandonné et classé `transient`. Sans nouvelle tentative et sans secours, le run échoue : mieux vaut une erreur franche au bout d'une seconde que trois secondes de silence. Avec un secours (`voix-secours`, qui ajoute `fallbacks: [SECOURS]`), le run bascule et aboutit :

```bash
uv run python latence.py voix-secours
```

```text
voix-secours        essai 1 : premier TextDelta 1.37 s  total 1.38 s  completed
voix-secours        essai 2 : premier TextDelta 1.01 s  total 1.02 s  completed
voix-secours        essai 3 : premier TextDelta 1.01 s  total 1.02 s  completed
```

Les premiers mots (ceux du secours) arrivent à 1,01 s : la seconde d'attente du principal, puis le secours. C'est le prix de la protection : au pire, `first_token` secondes d'attente avant que le secours ne parle.

Silence au milieu de la réponse, avec `idle: 1` :

```bash
uv run python latence.py voix-silence
```

```text
voix-silence        essai 1 : premier TextDelta 0.49 s  total 1.60 s  failed
  erreur : model.transient - Délai dépassé : aucun nouveau morceau en 1 s
voix-silence        essai 2 : premier TextDelta 0.12 s  total 1.22 s  failed
  erreur : model.transient - Délai dépassé : aucun nouveau morceau en 1 s
voix-silence        essai 3 : premier TextDelta 0.11 s  total 1.22 s  failed
  erreur : model.transient - Délai dépassé : aucun nouveau morceau en 1 s
```

Ici le premier `TextDelta` est arrivé (0,11 s), puis le flux s'est tu : au bout d'une seconde de silence, l'appel est abandonné. Les premiers mots étaient donc **déjà partis** chez l'appelant.

Enfin, l'effet d'un contrôle sur la réponse finale :

```bash
uv run python latence.py voix-controle
uv run python latence.py voix-controle-live
```

```text
voix-controle       essai 1 : premier TextDelta 1.30 s  total 1.30 s  completed
voix-controle       essai 2 : premier TextDelta 0.82 s  total 0.82 s  completed
voix-controle       essai 3 : premier TextDelta 0.82 s  total 0.82 s  completed
voix-controle-live  essai 1 : premier TextDelta 0.76 s  total 1.26 s  completed
voix-controle-live  essai 2 : premier TextDelta 0.31 s  total 0.82 s  completed
voix-controle-live  essai 3 : premier TextDelta 0.31 s  total 0.82 s  completed
```

Avec le contrat de sortie, la réponse est contrôlée avant d'être diffusée : le premier `TextDelta` arrive en même temps que la fin (0,82 s). Avec `stream_output: live`, il arrive à 0,31 s, comme pour l'agent sans contrôle.

Arrêtez le faux fournisseur avec Ctrl+C.

**À retenir.**

- **Mesurez au bon endroit.** Le temps utile à la voix est celui du premier `TextDelta` vu par celui qui consomme `loom.stream()`, pas la durée du run. Chaque appel de modèle est un flux (voir le début de ce chapitre) et `stream()` le laisse voir.
- **Le modèle simulé ne prouve pas la latence.** Il répond en quelques millisecondes quel que soit le fournisseur visé ; seuls un vrai fournisseur ou un faux serveur comme celui-ci donnent des chiffres. Les chiffres de cet exemple viennent d'un serveur local sur la machine de rédaction : ils montrent le mécanisme, pas la vitesse d'un fournisseur réel. *Non exécuté ici : mesure contre un vrai fournisseur, avec une vraie voix de bout en bout.*
- **`first_token` et `idle` valent 60 s par défaut**, et `total` 300 s. Pour la voix, 60 secondes de silence est une éternité : choisissez des valeurs d'après vos propres mesures. Un délai dépassé est une erreur `transient` : nouvelle tentative (`retry`), puis secours (`fallbacks`), puis échec du run, comme au début de ce chapitre.
- **Un échec après le début du flux est visible de l'appelant.** Les premiers `TextDelta` sont déjà partis (voir `voix-silence`). Si une nouvelle tentative a lieu (`max_attempts` supérieur à 1), le moteur envoie d'abord un `StreamReset` avant de rediffuser, pour que l'interface efface le texte partiel : une application vocale doit décider ce qu'elle fait de ce signal, puisqu'on ne peut pas « effacer » une phrase déjà prononcée. Ce cas n'a pas été exercé ici (`max_attempts: 1`).
- **Piège : un contrôle sur la réponse finale retarde les premiers mots.** Quand la réponse finale est contrôlée (contrat `output`, juge, politique `on_output`, rôle terminal sous contrat ou jugé), `stream_output` vaut `after_guards` par défaut : rien n'est diffusé avant la fin du contrôle. La documentation précise que la voix exige `live` et des contrôles légers, compatibles avec le flux, ou limités aux outils. Avec `live`, une réparation de la réponse déclenche un `StreamReset`.

> **Non exécuté ici : vrais fournisseurs.** Les formes `sdk: anthropic` / `sdk: openai` avec `timeouts`, `retry`, `circuit_breaker` et `pricing` se déclarent comme dans le README (exemple MiniMax-M3) : leur comportement face à de vraies erreurs HTTP (429, 529, `Retry-After`) n'a pas pu être éprouvé sans clé ni réseau. Les seuls délais (`timeouts`) l'ont été, contre un faux fournisseur local (exemple 15.4), pas contre un vrai.

---

## 16. Budgets, quotas et rapports de consommation

### Ce qu'il faut comprendre avant de commencer

Un agent qui tourne en boucle sur un modèle cher peut coûter plus qu'il ne rapporte. Loom-IA compte donc chaque appel (tokens d'entrée, de sortie, de cache, de raisonnement), le multiplie par le tarif du modèle réellement utilisé (secours compris), et en tire des **budgets** et des **rapports**. Deux choses à ne pas confondre :

- Un **budget** dit *combien* on peut dépenser : en dollars, en tokens, en nombre d'appels.
- Un **quota** dit *à quelle vitesse* on peut demander : des runs par minute.

| Budget | Section | Limites | Contrôlé |
|---|---|---|---|
| run | `budgets.run` | `max_cost`, `max_tokens`, `max_calls` | avant chaque appel de l'orchestrateur |
| session | `budgets.session` | `max_cost`, `max_tokens` | avant chaque appel de l'orchestrateur (runs précédents de la session compris) |
| client | `budgets.tenant` | `max_cost_per_day`, `max_tokens_per_day`, `max_cost_per_month`, `max_tokens_per_month` | **une fois**, au lancement du run, sur des fenêtres calendaires UTC |

`on_exceed` choisit la réaction pour les budgets de run et de session : `warn` écrit un `budget.exceeded` dans le journal et continue ; `stop` fait passer le run en phase **`finalizing`** : l'orchestrateur reçoit l'ordre de répondre maintenant, sans outils, avec ce qu'il a obtenu. Le dépassement est ainsi borné à une dernière génération. Un budget de client, lui, refuse le run avant qu'il commence et n'écrit rien.

Un agent peut surcharger les budgets de la racine, clé par clé, avec sa propre section `budget:` (au singulier). Un sous-agent reçoit une part de ce qui reste à son parent (`budget_share`, chapitre 17).

**`pricing` est obligatoire pour un budget en dollars.** Un modèle sans tarif coûte 0 $ : son budget ne se déclenche jamais. Loom-IA le signale au chargement (avertissement en profil `dev` ou sans profil, **erreur en profil `prod`**). Les tarifs se déclarent en dollars par million de tokens, avec des paliers possibles (`pricing.tiers`) au-delà d'un volume d'entrée.

Le rapport de consommation se lit de quatre façons : `loom report` en ligne de commande, `Loom.report()` et `Loom.consumption()` en Python, `GET /v1/sessions/{id}/report` en REST (chapitre 20), l'outil MCP `run_report` (chapitre 21).

### Exemple 16.1 : donner un prix aux appels et lire le rapport

**Pourquoi.** Le patron veut savoir ce que coûte une relance. Sans tarif déclaré, le journal affiche `0.0000 $` et personne ne peut répondre.

**Objectif.** Déclarer `pricing`, remettre le modèle principal en état de marche et lire le coût d'un run, puis d'une session.

**Mise en place.** On repart du projet du chapitre 15, mais le modèle `PRINCIPAL` répond maintenant. Plus de secours dans l'agent : `agents/relance.yaml` perd sa ligne `fallbacks`.

```yaml
name: relance
description: Relance un client pour un devis resté sans réponse.

main:
  model: PRINCIPAL
  system_file: relance.md

max_iterations: 8

tools:
  - python: chercher_devis
  - python: envoyer_email

roles:
  - name: rediger_relance
    description: Rédige l'e-mail de relance d'un devis.
    model: REDACTEUR
    system_file: rediger_relance.md
    input_schema:
      type: object
      properties:
        ton: {type: string, description: "cordial, ferme, bref…"}
      required: [ton]
    context: [user_input, {tool_results: [chercher_devis]}]
    input_template: |-
      Demande de l'artisan : {{ context.user_input }}
      Devis : {{ context.tool_results.chercher_devis }}
      Ton : {{ args.ton }}
```

`loom.yaml` : `PRINCIPAL` reprend le script de l'ancien secours, et les deux modèles reçoivent un `pricing`. Les montants sont ceux d'un modèle principal « cher » et d'un rédacteur « économique » (les vrais tarifs sont dans la documentation de votre fournisseur).

```yaml
version: 1

imports: [outils]
agents_dir: agents/
prompts_dir: prompts/

models:
  - id: PRINCIPAL
    sdk: fake
    model: fake-principal
    pricing: {input: 3.0, output: 15.0}       # dollars par million de tokens
    params:
      script:
        - text: Je commence par retrouver le devis.
          tool_calls:
            - {name: chercher_devis, arguments: {numero: D-2026-042}}
        - text: Je confie la rédaction au rôle prévu.
          tool_calls:
            - {name: rediger_relance, arguments: {ton: cordial}}
        - text: J'envoie la relance.
          tool_calls:
            - name: envoyer_email
              arguments:
                destinataire: mme.martin@example.fr
                objet: Votre devis D-2026-042
                corps: "Bonjour Madame Martin, je me permets de revenir vers vous au sujet du devis D-2026-042 (1 840 €) envoyé le 3 septembre. Restant à votre disposition, cordialement."
        - text: La relance du devis D-2026-042 est partie chez Mme Martin.
  - id: REDACTEUR
    sdk: fake
    model: fake-redacteur
    pricing: {input: 0.8, output: 4.0}
    params:
      script:
        - text: '{"objet": "Votre devis D-2026-042", "corps": "Bonjour Madame Martin, je me permets de revenir vers vous au sujet du devis D-2026-042 (1 840 €) envoyé le 3 septembre. Restant à votre disposition, cordialement."}'

telemetry:
  logging: {level: WARNING}

storage:
  events: {backend: jsonl, path: data}
```

**Exécution.** On rattache le run à une session nommée, pour pouvoir demander le rapport de la session :

```bash
rm -rf data
uv run loom run relance "Relance Mme Martin pour le devis D-2026-042, sur un ton cordial." --session relance-martin
```

```text
—

Statut     : paused · itérations : 3 · tokens : 2781/340 · coût : 0.0121 $
Run        : 01a1266e-5234-74b8-8c1b-0f59ee966d09
En attente : envoyer_email (fake_2_0) — loom approve 01a1266e-5234-74b8-8c1b-0f59ee966d09 --call fake_2_0
```

Le run est en pause, mais il a déjà un coût : celui des trois premiers appels. On approuve pour le terminer (`--session` dit dans quel journal chercher le run, `--by` est inscrit au journal) :

```bash
uv run loom approve 01a1266e-5234-74b8-8c1b-0f59ee966d09 --session relance-martin --by denis
cat data/emails.log
```

```text
Accordé : fake_2_0
La relance du devis D-2026-042 est partie chez Mme Martin.

Statut     : completed · itérations : 4 · tokens : 4078/379 · coût : 0.0165 $
Run        : 01a1266e-5234-74b8-8c1b-0f59ee966d09
mme.martin@example.fr | Votre devis D-2026-042
```

Le rapport d'un run, puis celui de la session :

```bash
uv run loom report 01a1266e-5234-74b8-8c1b-0f59ee966d09 --session relance-martin
```

```text
Consommation — run 01a1266e-5234-74b8-8c1b-0f59ee966d09
  Total             5 appels ·   4078/379 tokens · 0,01654 $
  Par rôle :
    main              4 appels ·   3845/300 tokens · 0,01604 $
    rediger_relance    1 appel ·     233/79 tokens · 0,00050 $
  Par modèle :
    fake-principal    4 appels ·   3845/300 tokens · 0,01604 $
    fake-redacteur     1 appel ·     233/79 tokens · 0,00050 $
```

```bash
uv run loom report --session relance-martin
```

```text
Consommation — session relance-martin
  Total             5 appels ·   4078/379 tokens · 0,01654 $
  Par run :
     5 appels ·   4078/379 tokens · 0,01654 $ — relance (completed) 01a1266e-5234-74b8-8c1b-0f59ee966d09
  Par rôle :
    main              4 appels ·   3845/300 tokens · 0,01604 $
    rediger_relance    1 appel ·     233/79 tokens · 0,00050 $
  Par modèle :
    fake-principal    4 appels ·   3845/300 tokens · 0,01604 $
    fake-redacteur     1 appel ·     233/79 tokens · 0,00050 $
```

La ventilation par rôle montre l'intérêt de déléguer : le rédacteur, qui travaille sur un modèle bon marché, pèse 0,0005 $ sur 0,0165 $. Les coûts exacts varient de quelques millièmes d'un run à l'autre (les identifiants entrent dans le nombre de tokens simulés).

Le même rapport en Python, avec `Loom.report()`. Le script `rapport.py` lance un run puis en lit le rapport (`run_id` + `session_id` + `tenant_id`, car un identifiant de run n'est unique que dans sa session et chez son client) :

```python
import asyncio

from loom_ia.access import Loom


async def main() -> None:
    async with Loom.from_config("loom.yaml") as loom:
        resultat = await loom.run(
            "relance",
            "Relance Mme Martin pour le devis D-2026-042.",
            session_id="relance-martin",
        )
        rapport = await loom.report(resultat.run_id, session_id="relance-martin")
        print(f"total : {rapport.total.calls} appels, {rapport.total.cost:.4f} $")
        for ligne in rapport.roles:
            print(f"  rôle {ligne.name} : {ligne.calls} appel(s), {ligne.cost:.5f} $")


asyncio.run(main())
```

```bash
rm -rf data
uv run python rapport.py
```

```text
total : 4 appels, 0.0120 $
  rôle main : 3 appel(s), 0.01151 $
  rôle rediger_relance : 1 appel(s), 0.00050 $
```

Ce run reste en pause à l'approbation (4 appels : 3 de l'orchestrateur, 1 du rôle) ; son rapport est celui de ce qui a eu lieu jusque-là.

**À retenir.**

- `loom report <run_id> --session <session>` pour un run, `loom report --session <session>` pour toute une conversation. Sans `--session`, le run est cherché dans la session qui porte son propre identifiant.
- `pricing` est en **dollars par million de tokens**. `cache_read` et `cache_write` ont leur propre tarif.
- Un rapport est recalculé depuis le journal : il vaut pour les runs passés, y compris ceux d'avant l'ajout d'un budget.
- **Piège.** Un modèle sans `pricing` coûte 0 $ sans que rien ne s'affiche à l'exécution : seule la validation prévient. Lisez toujours la sortie de `loom validate` après avoir ajouté un modèle.

### Exemple 16.2 : un budget de run qui prévient ou qui arrête

**Pourquoi.** Un modèle capricieux peut appeler vingt fois le même outil. L'artisan préfère une réponse incomplète à une facture inattendue.

**Objectif.** Poser un plafond de coût sur le run et comparer `warn` et `stop`.

**Mise en place.** On ajoute une section `budgets` à la fin de `loom.yaml`, le reste du fichier étant celui de l'exemple 16.1 :

```yaml
budgets:
  run: {max_cost: 0.004}
  on_exceed: stop
```

Le plafond de 0,004 $ est volontairement bas : les deux premiers appels de l'orchestrateur le dépassent.

**Exécution.**

```bash
rm -rf data
uv run loom validate | tail -5
```

```text
  relance : modèle PRINCIPAL, 2 outil(s) Python, rôle rediger_relance (REDACTEUR)
    politique loom.budget : before_model
    budget : run max_cost 0.004 ; stop

1 agent(s) monté(s) sans erreur.
```

Le budget est une politique fournie, `loom.budget`, branchée avant chaque appel de modèle de l'orchestrateur. Lancez le run :

```bash
uv run loom run relance "Relance Mme Martin pour le devis D-2026-042, sur un ton cordial."
```

```text
J'envoie la relance.

Statut     : completed · itérations : 3 · tokens : 2847/248 · coût : 0.0109 $
Run        : 01a1266e-b6de-723c-85e0-c85132c5ab78
```

Le run est **terminé**, sans pause d'approbation et sans e-mail : il a été arrêté avant d'arriver à `envoyer_email`. Voyez la fin de son `inspect` :

```bash
uv run loom inspect 01a1266e-b6de-723c-85e0-c85132c5ab78 | tail -10
```

```text
  étape 5
    · budget.exceeded (warning)
    · politique loom.budget (before_model) : stop
  étape 6
    modèle fake-principal (main) — 1 ms, 1140 → 30 tokens, 0.003870 $
      · répond    : J'envoie la relance.

Réponse finale :
  J'envoie la relance.
Bilan      : 4 appel(s) de modèle, 2 appel(s) d'outil
```

L'étape 5 est le passage en `finalizing` : la politique a vu 0,006 $ dépensés pour un plafond de 0,004 $, et a ordonné l'arrêt. L'orchestrateur a fait un dernier appel **sans outils**.

> **Attention à ce texte.** « J'envoie la relance. » vient du script du modèle simulé, qui ne sait pas qu'on l'arrête. Un vrai modèle reçoit en plus la consigne « Tu ne peux plus appeler d'outil. Réponds maintenant […] ; si tu n'as pas pu tout faire, dis ce qui reste à faire. » et répond par exemple « Le devis est retrouvé et l'e-mail rédigé, mais je n'ai pas pu l'envoyer ». Dans tous les cas, **vérifiez le statut et les approbations en attente**, pas seulement le texte.

Voyons le journal de plus près (`jq` n'est qu'un moyen commode de lire le JSONL, tout autre outil convient) :

```bash
uv run loom sessions export 01a1266e-b6de-723c-85e0-c85132c5ab78 | jq -c 'select(.type=="budget.exceeded") | .payload|del(.type)'
```

```text
{"scope":"run","limit":"max_cost","value":0.004,"spent":0.0070094,"action":"stop","policy":"loom.budget"}
```

Passons maintenant à `warn`. Remplacez `stop` par `warn` dans `loom.yaml` et relancez :

```yaml
budgets:
  run: {max_cost: 0.004}
  on_exceed: warn
```

```bash
rm -rf data
uv run loom run relance "Relance Mme Martin pour le devis D-2026-042, sur un ton cordial."
uv run loom approve 01a1266f-67f2-7356-a46a-0cc1b07d7945 --by denis
```

```text
—

Statut     : paused · itérations : 3 · tokens : 2781/340 · coût : 0.0121 $
Run        : 01a1266f-67f2-7356-a46a-0cc1b07d7945
En attente : envoyer_email (fake_2_0) — loom approve 01a1266f-67f2-7356-a46a-0cc1b07d7945 --call fake_2_0
Accordé : fake_2_0
La relance du devis D-2026-042 est partie chez Mme Martin.

Statut     : completed · itérations : 4 · tokens : 4078/379 · coût : 0.0165 $
Run        : 01a1266f-67f2-7356-a46a-0cc1b07d7945
```

Cette fois le run va au bout. Le dépassement est seulement noté dans le journal (`budget.exceeded`, avec `"action":"warn"`), une fois, que vous pouvez brancher sur une alerte.

Reste le **budget par agent**. Les budgets de la racine servent de défaut, et un agent les surcharge clé par clé. Mettez dans `loom.yaml` :

```yaml
budgets:
  run: {max_cost: 0.05, max_calls: 10}
  on_exceed: warn
```

et ajoutez à `agents/relance.yaml`, après `max_iterations: 8` :

```yaml
budget:
  run: {max_cost: 0.004}   # remplace seulement max_cost ; max_calls: 10 reste celui de la racine
  on_exceed: stop
```

```bash
uv run loom validate | tail -5
```

```text
  relance : modèle PRINCIPAL, 2 outil(s) Python, rôle rediger_relance (REDACTEUR)
    politique loom.budget : before_model
    budget : run max_cost 0.004, max_calls 10 ; stop

1 agent(s) monté(s) sans erreur.
```

Le budget effectif de l'agent est la fusion des deux : `max_cost 0.004` (celui de l'agent) et `max_calls 10` (celui de la racine), avec `stop` (celui de l'agent). Le run se comporte alors comme dans le premier essai de cet exemple : arrêt en `finalizing`.

Enfin, le tarif manquant. Retirez `pricing` du modèle `REDACTEUR` et validez :

```bash
uv run loom validate 2>&1 | tail -6
uv run loom --profile prod validate 2>&1 | tail -1
```

```text
2026-10-10 17:30:11 WARNING  loom_ia.runtime.wiring — Agent 'relance' : budget en dollars, mais sans tarif pour REDACTEUR : leurs appels comptent 0 $
  relance : modèle PRINCIPAL, 2 outil(s) Python, rôle rediger_relance (REDACTEUR)
    politique loom.budget : before_model
    budget : run max_cost 0.004, max_calls 10 ; stop

1 agent(s) monté(s) sans erreur.
Configuration : Profil prod : Agent 'relance' : budget en dollars, mais sans tarif pour REDACTEUR : leurs appels comptent 0 $
```

Sans profil, un avertissement ; en `prod`, la configuration est refusée. (Remettez le `pricing` de `REDACTEUR` pour la suite.)

**À retenir.**

- Un budget de run ne s'évalue qu'**avant un appel de l'orchestrateur**. Un appel déjà parti peut donc dépasser le plafond (borné à une génération), et un appel de rôle ou de juge en plein tour est compté mais ne s'interrompt pas.
- `max_tokens` est la parade quand un modèle n'a pas de tarif : il fonctionne sans `pricing`.
- `max_calls` compte les appels de modèle du run (orchestrateur, rôles, juges) ; un sous-agent a son propre compte.
- **Piège.** Avec `stop`, le run se termine en `completed` : son statut ne dit pas qu'il a été interrompu par le budget. Cherchez `budget.exceeded` dans le journal, ou surveillez-le avec `loom inspect`.

### Exemple 16.3 : un budget et un quota par client

**Pourquoi.** Si vous servez plusieurs artisans avec la même instance, l'un d'eux ne doit pas pouvoir vider votre compte fournisseur, ni saturer le service par un script qui s'emballe. Le budget de client se règle par journée ou par mois ; le quota, par minute.

**Objectif.** Poser un plafond journalier à `dupont-plomberie`, constater un refus avant que le run ne commence, puis lire la consommation du client. On y ajoute un quota de débit.

**Mise en place.** Déclarer des clients (`tenants`) rend la liste fermée : tout client non déclaré est refusé, et `default` n'existe plus tant que vous ne le déclarez pas. Le chapitre 22 détaille les clients ; ici, un seul suffit. Remplacez la section `budgets` de `loom.yaml` et ajoutez `tenants` (retirez la section `budget` de l'agent pour revenir à l'état de l'exemple 16.1) :

```yaml
budgets:
  run: {max_cost: 0.05, max_calls: 10}
  on_exceed: stop

tenants:
  - id: dupont-plomberie
    budgets:
      tenant: {max_cost_per_day: 0.02}
```

Le plafond est bas pour que le test aille vite : un run en pause en consomme déjà 0,012 $. `budget_client.py` lance trois runs au nom du client puis lit sa consommation :

```python
import asyncio

from loom_ia.access import Loom
from loom_ia.tenancy import BudgetExhausted


async def main() -> None:
    async with Loom.from_config("loom.yaml") as loom:
        for numero in range(1, 4):
            try:
                result = await loom.run(
                    "relance",
                    "Relance Mme Martin pour le devis D-2026-042.",
                    tenant="dupont-plomberie",
                )
                print(f"run {numero} : {result.status}, {result.cost_usd:.4f} $")
            except BudgetExhausted as erreur:
                print(f"run {numero} : refusé — {erreur}")
                print(f"  réessayer dans {erreur.retry_after / 3600:.1f} h")

        jour = await loom.consumption("dupont-plomberie", period="day")
        print(f"{jour.runs} runs, {jour.spent.cost:.4f} $, plafond {jour.limits}")


asyncio.run(main())
```

**Exécution.**

```bash
rm -rf data
uv run python budget_client.py
```

```text
run 1 : paused, 0.0120 $
run 2 : paused, 0.0120 $
run 3 : refusé — Client 'dupont-plomberie' : budget du client atteint pour la journée : 0,02402 $ (plafond 0,02000 $)
  réessayer dans 8.5 h
2 runs, 0.0240 $, plafond (('max_cost', 0.02),)
```

Le run 2 démarre parce que, à son lancement, la journée n'est qu'à 0,012 $ : un budget de client se lit **une fois**, au départ. Il n'interrompt jamais un run en cours. Le run 3 est refusé avant d'écrire quoi que ce soit : ni run, ni événement. `retry_after` donne les secondes avant la prochaine fenêtre (minuit UTC).

La même consommation en ligne de commande :

```bash
uv run loom report --tenant dupont-plomberie --periode jour
```

```text
Client     : dupont-plomberie
Période    : journée en cours, depuis 2026-10-10 00:00 UTC
Runs       : 2
Dépense    : 0.024025 $, 6202 tokens
  plafond max_cost : 0,02000 $ — reste 0,00000 $
Remise à 0 : 2026-10-11 00:00 UTC
```

`--periode` accepte `jour` ou `mois`. Avec `--json`, la forme est celle d'un `TenantConsumption` (`tenant_id`, `period`, `since`, `resets_at`, `runs`, `cost_usd`, `tokens`, `limits`, `left`).

Passons au **quota**. Dans `loom.yaml`, remplacez le plafond de la journée par un quota de débit :

```yaml
tenants:
  - id: dupont-plomberie
    budgets:
      tenant: {max_cost_per_day: 5.0}
    quotas: {runs_per_minute: 2}
```

`quota.py` lance trois runs de suite :

```python
import asyncio

from loom_ia.access import Loom
from loom_ia.tenancy import QuotaExceeded


async def main() -> None:
    async with Loom.from_config("loom.yaml") as loom:
        for numero in range(1, 4):
            try:
                result = await loom.run(
                    "relance",
                    "Relance Mme Martin pour le devis D-2026-042.",
                    tenant="dupont-plomberie",
                )
                print(f"run {numero} : {result.status}")
            except QuotaExceeded as erreur:
                print(f"run {numero} : refusé — {erreur}")


asyncio.run(main())
```

```bash
rm -rf data
uv run python quota.py
```

```text
run 1 : paused
run 2 : paused
run 3 : refusé — Client 'dupont-plomberie' : 2 par minute dépassé, réessayer dans 59.9 s
```

**À retenir.**

- Un budget de **client** vit dans la fiche du client (`tenants[].budgets.tenant`) ou dans `budgets.tenant` à la racine pour le client `default`. Il est refusé **avant** le run : le refus ne laisse aucune trace de run dans le journal.
- En REST, un budget de client épuisé et un quota dépassé se traduisent par un refus `429` accompagné d'un `Retry-After` (constaté au chapitre 22).
- Le quota `runs_per_minute` compte les runs lancés par le client (un sous-run n'en est pas un), sur une fenêtre glissante d'une minute, et vaut pour tous les accès (Python, REST, MCP, ligne de commande).
- **Piège 1.** La fenêtre glissante du quota est tenue **en mémoire de l'instance** `Loom`. Une commande `loom run` qui ne vit que le temps d'un run ne peut donc jamais atteindre un quota : lancez trois fois la commande en ligne de commande, les trois passeront. Le quota protège un service qui reste vivant (`loom serve`, un script, un worker) ; avec plusieurs workers, chacun tient sa propre fenêtre.
- **Piège 2.** Écart avec la documentation, version 2.0.0 : quand un budget de client est épuisé, `loom run` affiche une **trace d'erreur Python complète** (`loom_ia.tenancy.usage.BudgetExhausted: …`) et sort en code 1. Le dépôt corrige cela (version 2.0.1 : une ligne claire sur `stderr`, code de sortie 2 pour les refus d'exploitation). Dans un script ou un service, interceptez `BudgetExhausted` et `QuotaExceeded` (`from loom_ia.tenancy import …`).

---

## 17. Sous-agents : déléguer à un agent complet

### Ce qu'il faut comprendre avant de commencer

Un **rôle** (chapitre 12) fait un seul appel de modèle, sans outils ni mémoire. Un **sous-agent** est un agent complet appelé comme un outil : il a sa propre boucle, ses propres outils, ses propres modèles et ses propres limites. Il faut y penser quand une tâche demande plusieurs étapes et mérite un prompt à elle : contrôler un e-mail contre un devis, envoyer un e-mail, chercher dans un carnet d'adresses.

| Réglage | Où | Effet |
|---|---|---|
| `subagents: [{agent, name, description, budget_share}]` | agent appelant | Chaque entrée devient un outil. `agent` est l'agent appelé. `name` est le nom de l'outil que voit le modèle (par défaut, celui de l'agent). `description` (celle de l'agent, à défaut) dit au modèle quand l'appeler. |
| `budget_share` | entrée de sous-agent | Part de ce qui **reste** au budget du run appelant au moment de l'appel (de 0 exclu à 1). Exige un budget de run sur l'agent appelant. |
| `max_depth` | agent | Profondeur d'imbrication que **cet agent** autorise pour appeler ses propres sous-agents (1 par défaut). |
| `expose: {rest: false, mcp: false}` | agent appelé | Évite de publier le sous-agent comme un agent à part entière. |

Ce que fait le moteur :

- Le sous-agent tourne dans un **run enfant**, écrit dans le **même journal** (même session) que le parent. Il ne reçoit ni l'historique de la session ni celui du parent : seulement l'argument `message`. Le parent doit donc y mettre tout ce dont l'enfant a besoin.
- Seule la **réponse finale** de l'enfant revient au parent, comme un résultat d'outil. Un échec de l'enfant devient un résultat d'erreur.
- La **consommation de l'enfant s'ajoute à celle du parent** : coût, tokens, appels.
- Quand l'enfant attend une validation humaine, **son approbation remonte jusqu'au run racine**, qui passe en `waiting_child`. C'est là qu'on la tranche : celui qui supervise n'a pas à savoir qu'un sous-agent existe.
- `loom inspect` montre l'arbre : le run racine, ses étapes, et sous chaque `sous-agent`, le `run` de l'enfant avec ses propres étapes.

### Exemple 17.1 : faire vérifier l'e-mail par un sous-agent

**Pourquoi.** Le rôle rédacteur écrit vite, mais l'artisan ne veut pas qu'un montant erroné parte chez Mme Martin. Un contrôleur dédié relit l'e-mail contre le devis, avec ses propres outils et son propre prompt.

**Objectif.** Ajouter à l'agent `relance` un sous-agent `verifier`, voir son run dans l'arbre, et constater que son coût remonte au parent.

**Mise en place.** On repart du projet de l'exemple 16.1, avec une section `budgets` : un budget de run est indispensable pour `budget_share`.

`prompts/verificateur.md` :

```markdown
Tu contrôles des e-mails de relance de devis pour la Plomberie Dupont.
Retrouve le devis avec `chercher_devis`, puis compare le montant et la date
de l'e-mail à ceux du devis. Réponds en une phrase : « Contrôle réussi » ou
la liste des écarts.
```

`agents/verificateur.yaml` : un agent ordinaire, qui n'est pas publié seul.

```yaml
name: verificateur
description: Vérifie qu'un e-mail de relance reprend exactement le montant et la date du devis.
expose: {rest: false, mcp: false}      # sous-agent seulement : pas publié seul

main:
  model: VERIF
  system_file: verificateur.md

max_iterations: 4

tools:
  - python: chercher_devis
```

`agents/relance.yaml` : l'agent du chapitre 16, avec une section `subagents` en plus. `envoyer_email` reste ici pour l'instant.

```yaml
name: relance
description: Relance un client pour un devis resté sans réponse.

main:
  model: PRINCIPAL
  system_file: relance.md

max_iterations: 8

tools:
  - python: chercher_devis
  - python: envoyer_email

roles:
  - name: rediger_relance
    description: Rédige l'e-mail de relance d'un devis.
    model: REDACTEUR
    system_file: rediger_relance.md
    input_schema:
      type: object
      properties:
        ton: {type: string, description: "cordial, ferme, bref…"}
      required: [ton]
    context: [user_input, {tool_results: [chercher_devis]}]
    input_template: |-
      Demande de l'artisan : {{ context.user_input }}
      Devis : {{ context.tool_results.chercher_devis }}
      Ton : {{ args.ton }}

subagents:
  - agent: verificateur
    name: verifier
    description: Vérifie l'e-mail rédigé contre le devis (montant, date d'envoi).
    budget_share: 0.3          # au plus 30 % de ce qui reste au budget du run
```

`loom.yaml` : le script de l'orchestrateur gagne une étape (appel de `verifier` entre la rédaction et l'envoi) et un modèle `VERIF` joue le contrôleur.

```yaml
version: 1

imports: [outils]
agents_dir: agents/
prompts_dir: prompts/

models:
  - id: PRINCIPAL
    sdk: fake
    model: fake-principal
    pricing: {input: 3.0, output: 15.0}
    params:
      script:
        - text: Je commence par retrouver le devis.
          tool_calls:
            - {name: chercher_devis, arguments: {numero: D-2026-042}}
        - text: Je confie la rédaction au rôle prévu.
          tool_calls:
            - {name: rediger_relance, arguments: {ton: cordial}}
        - text: Je fais vérifier l'e-mail avant de l'envoyer.
          tool_calls:
            - name: verifier
              arguments:
                message: "Vérifie que l'e-mail de relance pour le devis D-2026-042 cite bien 1 840 € et un envoi le 3 septembre."
        - text: J'envoie la relance.
          tool_calls:
            - name: envoyer_email
              arguments:
                destinataire: mme.martin@example.fr
                objet: Votre devis D-2026-042
                corps: "Bonjour Madame Martin, je me permets de revenir vers vous au sujet du devis D-2026-042 (1 840 €) envoyé le 3 septembre. Restant à votre disposition, cordialement."
        - text: La relance du devis D-2026-042 est partie chez Mme Martin.
  - id: REDACTEUR
    sdk: fake
    model: fake-redacteur
    pricing: {input: 0.8, output: 4.0}
    params:
      script:
        - text: '{"objet": "Votre devis D-2026-042", "corps": "Bonjour Madame Martin, je me permets de revenir vers vous au sujet du devis D-2026-042 (1 840 €) envoyé le 3 septembre. Restant à votre disposition, cordialement."}'
  - id: VERIF
    sdk: fake
    model: fake-verif
    pricing: {input: 0.8, output: 4.0}
    params:
      script:
        - text: Je relis le devis.
          tool_calls:
            - {name: chercher_devis, arguments: {numero: D-2026-042}}
        - text: "Contrôle réussi : l'e-mail cite 1 840 € et un envoi le 3 septembre, conformes au devis D-2026-042."

telemetry:
  logging: {level: WARNING}

storage:
  events: {backend: jsonl, path: data}

budgets:
  run: {max_cost: 0.05}
  on_exceed: stop
```

**Exécution.**

```bash
rm -rf data
uv run loom validate | tail -8
```

```text
  relance : modèle PRINCIPAL, 2 outil(s) Python, rôle rediger_relance (REDACTEUR), sous-agent verifier (verificateur)
    politique loom.budget : before_model
    budget : run max_cost 0.05 ; stop
  verificateur : modèle VERIF, 1 outil(s) Python
    politique loom.budget : before_model
    budget : run max_cost 0.05 ; stop

2 agent(s) monté(s) sans erreur.
```

Les deux agents sont montés ; `verifier` apparaît dans les outils de `relance`.

```bash
uv run loom run relance "Relance Mme Martin pour le devis D-2026-042, sur un ton cordial."
uv run loom approve 01a12671-ceef-7769-bfeb-1d829ada4187 --by denis
```

```text
—

Statut     : paused · itérations : 4 · tokens : 5473/549 · coût : 0.0203 $
Run        : 01a12671-ceef-7769-bfeb-1d829ada4187
En attente : envoyer_email (fake_3_0) — loom approve 01a12671-ceef-7769-bfeb-1d829ada4187 --call fake_3_0
Accordé : fake_3_0
La relance du devis D-2026-042 est partie chez Mme Martin.

Statut     : completed · itérations : 5 · tokens : 7145/588 · coût : 0.0259 $
Run        : 01a12671-ceef-7769-bfeb-1d829ada4187
```

Le coût final (0,0259 $) inclut celui du contrôleur. L'arbre :

```bash
uv run loom inspect 01a12671-ceef-7769-bfeb-1d829ada4187 | grep -E "^ *(run|sous-agent) |Bilan"
```

```text
run relance — completed, 987 ms, 0.025947 $
    sous-agent verifier — 31 ms
      run verificateur — completed, 20 ms, 0.001064 $
Bilan      : 8 appel(s) de modèle, 5 appel(s) d'outil, 1 approbation(s), 1 sous-run(s)
```

Et le rapport, qui sépare les deux runs :

```bash
uv run loom report 01a12671-ceef-7769-bfeb-1d829ada4187
```

```text
Consommation — run 01a12671-ceef-7769-bfeb-1d829ada4187
  Total                       8 appels ·   7145/588 tokens · 0,02595 $
  Par run :
     6 appels ·   6390/473 tokens · 0,02488 $ — relance (completed) 01a12671-ceef-7769-bfeb-1d829ada4187
     2 appels ·    755/115 tokens · 0,00106 $ —   verificateur (completed) 01a12671-cf23-7412-924f-b2d3a1775db2
  Par rôle :
    relance · main              5 appels ·   6157/394 tokens · 0,02438 $
    relance · rediger_relance    1 appel ·     233/79 tokens · 0,00050 $
    verificateur · main         2 appels ·    755/115 tokens · 0,00106 $
  Par modèle :
    fake-principal              5 appels ·   6157/394 tokens · 0,02438 $
    fake-redacteur               1 appel ·     233/79 tokens · 0,00050 $
    fake-verif                  2 appels ·    755/115 tokens · 0,00106 $
```

Le total (0,02595 $) est la somme du parent (0,02488 $) et de l'enfant (0,00106 $). Enfin, la part de budget que l'enfant a reçue est écrite dans son `run.started` :

```bash
uv run loom sessions export 01a12671-ceef-7769-bfeb-1d829ada4187 | jq -c 'select(.type=="run.started") | {run: .run_id[-8:], parent: (.payload.parent_run_id // "-")[-8:], depth: .payload.depth, budget: .payload.budget}'
```

```text
{"run":"9ada4187","parent":"-","depth":0,"budget":null}
{"run":"a1775db2","parent":"9ada4187","depth":1,"budget":{"max_cost":0.011067480000000001,"max_calls":null,"max_tokens":null}}
```

L'enfant a reçu 0,011 $ : 30 % de ce qui restait des 0,05 $ du parent quand il l'a appelé (0,05 − 0,013 dépensés).

**À retenir.**

- Un sous-agent est un outil comme un autre pour le parent : le modèle le choisit d'après sa `description`. Soignez-la.
- `budget_share` est un **plafond supplémentaire**, pas un remplacement : la limite la plus basse l'emporte entre celle de l'agent enfant et sa part. Sans budget de run sur le parent, `budget_share` est refusé au chargement.
- Le parent doit tout dire dans `message` : l'enfant ne voit rien d'autre.
- **Piège.** Un sous-agent sans `description` (ni dans la référence, ni dans l'agent appelé) est refusé au chargement : le modèle ne saurait pas quand l'appeler.

### Exemple 17.2 : l'approbation d'un enfant remonte au run racine

**Pourquoi.** L'artisan ne doit pas avoir à connaître l'architecture de l'agent. Qu'un e-mail parte depuis un sous-agent ou depuis l'agent principal, la question qu'on lui pose est la même : « Voulez-vous envoyer cet e-mail ? », et il y répond sur **le run qu'il connaît**.

**Objectif.** Déplacer l'envoi dans un sous-agent `expediteur`, et approuver depuis le run racine.

**Mise en place.** `envoyer_email` quitte l'agent `relance` pour l'agent `expediteur`. On crée :

`prompts/expediteur.md` :

```markdown
Tu envoies des e-mails de relance pour la Plomberie Dupont, avec l'outil
`envoyer_email`, exactement comme on te le demande. Confirme en une phrase.
```

`agents/expediteur.yaml` :

```yaml
name: expediteur
description: Envoie un e-mail de relance au client.
expose: {rest: false, mcp: false}

main:
  model: EXPED
  system_file: expediteur.md

max_iterations: 4

tools:
  - python: envoyer_email
```

On met à jour `prompts/relance.md` pour décrire le nouveau parcours :

```markdown
Tu es l'assistant de la Plomberie Dupont. Pour relancer un client :
1. retrouve le devis avec `chercher_devis` ;
2. fais rédiger l'e-mail par le rôle `rediger_relance` ;
3. fais vérifier l'e-mail avec `verifier`, puis envoie-le avec le sous-agent `expedier`.
Ne calcule et n'invente jamais un montant : reprends celui du devis.
```

`agents/relance.yaml` : sans `envoyer_email`, avec un second sous-agent.

```yaml
name: relance
description: Relance un client pour un devis resté sans réponse.

main:
  model: PRINCIPAL
  system_file: relance.md

max_iterations: 8

tools:
  - python: chercher_devis

roles:
  - name: rediger_relance
    description: Rédige l'e-mail de relance d'un devis.
    model: REDACTEUR
    system_file: rediger_relance.md
    input_schema:
      type: object
      properties:
        ton: {type: string, description: "cordial, ferme, bref…"}
      required: [ton]
    context: [user_input, {tool_results: [chercher_devis]}]
    input_template: |-
      Demande de l'artisan : {{ context.user_input }}
      Devis : {{ context.tool_results.chercher_devis }}
      Ton : {{ args.ton }}

subagents:
  - agent: verificateur
    name: verifier
    description: Vérifie l'e-mail rédigé contre le devis (montant, date d'envoi).
    budget_share: 0.3          # au plus 30 % de ce qui reste au budget du run
  - agent: expediteur
    name: expedier
    description: Envoie l'e-mail de relance au client (avec l'approbation de l'artisan).
```

`loom.yaml` : le script de l'orchestrateur appelle `expedier` au lieu de `envoyer_email`, et un modèle `EXPED` joue l'expéditeur.

```yaml
version: 1

imports: [outils]
agents_dir: agents/
prompts_dir: prompts/

models:
  - id: PRINCIPAL
    sdk: fake
    model: fake-principal
    pricing: {input: 3.0, output: 15.0}
    params:
      script:
        - text: Je commence par retrouver le devis.
          tool_calls:
            - {name: chercher_devis, arguments: {numero: D-2026-042}}
        - text: Je confie la rédaction au rôle prévu.
          tool_calls:
            - {name: rediger_relance, arguments: {ton: cordial}}
        - text: Je fais vérifier l'e-mail avant de l'envoyer.
          tool_calls:
            - name: verifier
              arguments:
                message: "Vérifie que l'e-mail de relance pour le devis D-2026-042 cite bien 1 840 € et un envoi le 3 septembre."
        - text: Je confie l'envoi au sous-agent expéditeur.
          tool_calls:
            - name: expedier
              arguments:
                message: "Envoie à mme.martin@example.fr, objet « Votre devis D-2026-042 », l'e-mail de relance rédigé plus haut."
        - text: La relance du devis D-2026-042 est partie chez Mme Martin.
  - id: REDACTEUR
    sdk: fake
    model: fake-redacteur
    pricing: {input: 0.8, output: 4.0}
    params:
      script:
        - text: '{"objet": "Votre devis D-2026-042", "corps": "Bonjour Madame Martin, je me permets de revenir vers vous au sujet du devis D-2026-042 (1 840 €) envoyé le 3 septembre. Restant à votre disposition, cordialement."}'
  - id: EXPED
    sdk: fake
    model: fake-exped
    pricing: {input: 0.8, output: 4.0}
    params:
      script:
        - text: J'envoie l'e-mail.
          tool_calls:
            - name: envoyer_email
              arguments:
                destinataire: mme.martin@example.fr
                objet: Votre devis D-2026-042
                corps: "Bonjour Madame Martin, je me permets de revenir vers vous au sujet du devis D-2026-042 (1 840 €) envoyé le 3 septembre. Restant à votre disposition, cordialement."
        - text: E-mail envoyé à mme.martin@example.fr.
  - id: VERIF
    sdk: fake
    model: fake-verif
    pricing: {input: 0.8, output: 4.0}
    params:
      script:
        - text: Je relis le devis.
          tool_calls:
            - {name: chercher_devis, arguments: {numero: D-2026-042}}
        - text: "Contrôle réussi : l'e-mail cite 1 840 € et un envoi le 3 septembre, conformes au devis D-2026-042."

telemetry:
  logging: {level: WARNING}

storage:
  events: {backend: jsonl, path: data}

budgets:
  run: {max_cost: 0.05}
  on_exceed: stop
```

**Exécution.**

```bash
rm -rf data
uv run loom run relance "Relance Mme Martin pour le devis D-2026-042, sur un ton cordial."
```

```text
—

Statut     : waiting_child · itérations : 4 · tokens : 5480/521 · coût : 0.0199 $
Run        : 01a12672-0ffa-72df-ba71-8910613ef47f
En attente : envoyer_email (fake_0_0) — loom approve 01a12672-0ffa-72df-ba71-8910613ef47f --call fake_0_0
```

Le statut du run racine est `waiting_child` : un de ses enfants attend. La demande d'approbation (`envoyer_email`, appel `fake_0_0`) est rapportée **sur le run racine**, avec la commande exacte pour la trancher. Voyez où le run en est :

```bash
uv run loom inspect 01a12672-0ffa-72df-ba71-8910613ef47f | tail -22
```

```text
          outil chercher_devis — 1 ms
            · arguments : {"numero": "D-2026-042"}
            · résultat  : {"numero": "D-2026-042", "client": "Mme Martin", "email": "mme.martin@example.fr", "objet": "Remp…
        étape 3
          modèle fake-verif (main) — 1 ms, 486 → 49 tokens, 0.000585 $
            · répond    : Contrôle réussi : l'e-mail cite 1 840 € et un envoi le 3 septembre, conformes au devis D-2026-042.
  étape 7
    modèle fake-principal (main) — 1 ms, 1450 → 94 tokens, 0.005760 $
      · répond    : Je confie l'envoi au sous-agent expéditeur.
      · appelle   : expedier({"message": "Envoie à mme.martin@example.fr, objet « Votre devis D-2026-042 », l'e-mail …
  étape 8
    sous-agent expedier — 0 ms (ouvert)
      · arguments : {"message": "Envoie à mme.martin@example.fr, objet « Votre devis D-2026-042 », l'e-mail de relanc…
      run expediteur — inachevé, 9 ms (ouvert)
        étape 1
          modèle fake-exped (main) — 1 ms, 311 → 121 tokens, 0.000733 $
            · répond    : J'envoie l'e-mail.
            · appelle   : envoyer_email({"destinataire": "mme.martin@example.fr", "objet": "Votre devis D-2026-042", "corps…
        étape 2
          approbation pour envoyer_email — en attente

Bilan      : 8 appel(s) de modèle, 5 appel(s) d'outil, 1 approbation(s), 2 sous-run(s)
```

L'approbation est bien dans le run `expediteur`, imbriqué dans l'étape 8. Pour la trancher, on s'adresse pourtant au run racine :

```bash
uv run loom approve 01a12672-0ffa-72df-ba71-8910613ef47f --call fake_0_0 --by denis
cat data/emails.log
```

```text
Accordé : fake_0_0
La relance du devis D-2026-042 est partie chez Mme Martin.

Statut     : completed · itérations : 5 · tokens : 7951/715 · coût : 0.0268 $
Run        : 01a12672-0ffa-72df-ba71-8910613ef47f
mme.martin@example.fr | Votre devis D-2026-042
```

**À retenir.**

- L'identifiant d'approbation (`fake_0_0` ici) est celui de l'appel **dans l'enfant**. Sans `--call`, `loom approve <racine>` tranche tout ce que le run attend, enfants compris.
- On s'adresse toujours à la racine, quelle que soit la profondeur de l'enfant qui attend.
- **Piège.** Un `Statut : waiting_child` n'est pas une erreur. Pour un script de supervision, traitez `paused` **et** `waiting_child` comme « attend un humain ».

> Les arguments longs sont coupés par `loom inspect` (« corps… »). L'option `--full` les affiche en entier.

### Exemple 17.3 : limiter la profondeur d'imbrication avec `max_depth`

**Pourquoi.** Des agents qui s'appellent les uns les autres peuvent boucler ou coûter cher sans que personne ne s'en rende compte. `max_depth` fixe un plafond net.

**Objectif.** Comprendre à quel niveau un sous-agent est proposé, en donnant un sous-agent au contrôleur.

**Mise en place.** Le contrôleur `verificateur` reçoit à son tour un sous-agent, `arbitre`, qui tranche un doute. Par défaut (`max_depth: 1`), le contrôleur, qui est lui-même à la profondeur 1, **ne se voit pas proposer** `arbitrer`. Le modèle simulé permet de le constater : une réponse conditionnée par `with_tool` n'est jouée que si l'outil est proposé, `without_tool` que s'il ne l'est pas.

`agents/arbitre.yaml` :

```yaml
name: arbitre
description: Tranche un doute sur un montant ou une date.
expose: {rest: false, mcp: false}

main:
  model: ARBITRE
  system: Tu tranches un doute sur un montant ou une date, en une phrase.

max_iterations: 2
```

`agents/verificateur.yaml` : le contrôleur déclare son sous-agent. La ligne `max_depth` est commentée pour le premier essai.

```yaml
name: verificateur
description: Vérifie qu'un e-mail de relance reprend exactement le montant et la date du devis.
expose: {rest: false, mcp: false}      # sous-agent seulement : pas publié seul

main:
  model: VERIF
  system_file: verificateur.md

max_iterations: 4

tools:
  - python: chercher_devis

subagents:
  - agent: arbitre
    name: arbitrer
    description: Tranche un doute sur un montant ou une date.

# max_depth: 2     # à décommenter à l'étape suivante
```

Dans `loom.yaml`, remplacez le modèle `VERIF` et ajoutez `ARBITRE` (le reste du fichier est celui de l'exemple 17.2) :

```yaml
  - id: VERIF
    sdk: fake
    model: fake-verif
    pricing: {input: 0.8, output: 4.0}
    params:
      script:
        - text: Je relis le devis.
          tool_calls:
            - {name: chercher_devis, arguments: {numero: D-2026-042}}
        - text: "Un doute sur le montant, je demande l'avis de l'arbitre."
          with_tool: arbitrer
          tool_calls:
            - name: arbitrer
              arguments: {message: "Le devis D-2026-042 est à 1 840 € TTC ; l'e-mail annonce-t-il le même montant ?"}
        - text: "Contrôle réussi : l'e-mail cite 1 840 € et un envoi le 3 septembre, conformes au devis D-2026-042 (arbitre consulté)."
          with_tool: arbitrer
        - text: "Contrôle réussi : l'e-mail cite 1 840 € et un envoi le 3 septembre, conformes au devis D-2026-042 (sans arbitre : l'outil n'est pas proposé à ce niveau)."
          without_tool: arbitrer
  - id: ARBITRE
    sdk: fake
    model: fake-arbitre
    pricing: {input: 0.8, output: 4.0}
    params:
      script:
        - text: Le montant de l'e-mail est conforme au devis.
```

**Exécution.** Premier essai, avec `max_depth` par défaut :

```bash
rm -rf data
uv run loom run relance "Relance Mme Martin pour le devis D-2026-042, sur un ton cordial."
uv run loom inspect 01a12673-2751-731d-8e28-447b4cd4125f | grep -E "^ *(run|sous-agent) |Bilan"
```

```text
—

Statut     : waiting_child · itérations : 4 · tokens : 5726/535 · coût : 0.0202 $
Run        : 01a12673-2751-731d-8e28-447b4cd4125f
En attente : envoyer_email (fake_0_0) — loom approve 01a12673-2751-731d-8e28-447b4cd4125f --call fake_0_0
run relance — inachevé, 96 ms (ouvert)
    sous-agent verifier — 25 ms
      run verificateur — completed, 15 ms, 0.001306 $
    sous-agent expedier — 0 ms (ouvert)
      run expediteur — inachevé, 11 ms (ouvert)
Bilan      : 8 appel(s) de modèle, 5 appel(s) d'outil, 1 approbation(s), 2 sous-run(s)
```

Le contrôleur n'a pas pu appeler `arbitrer` : son script a joué la dernière réponse, celle du cas « sans arbitre ». Décommentez maintenant `max_depth` dans `agents/verificateur.yaml` :

```yaml
max_depth: 2
```

et relancez :

```bash
rm -rf data
uv run loom run relance "Relance Mme Martin pour le devis D-2026-042, sur un ton cordial."
uv run loom inspect 01a12673-2d7f-748a-9718-26a65b11b25b | grep -E "^ *(run|sous-agent) |Bilan"
```

```text
—

Statut     : waiting_child · itérations : 4 · tokens : 7153/653 · coût : 0.0218 $
Run        : 01a12673-2d7f-748a-9718-26a65b11b25b
En attente : envoyer_email (fake_0_0) — loom approve 01a12673-2d7f-748a-9718-26a65b11b25b --call fake_0_0
run relance — inachevé, 136 ms (ouvert)
    sous-agent verifier — 61 ms
      run verificateur — completed, 45 ms, 0.002926 $
          sous-agent arbitrer — 16 ms
            run arbitre — completed, 7 ms, 0.000232 $
    sous-agent expedier — 0 ms (ouvert)
      run expediteur — inachevé, 13 ms (ouvert)
Bilan      : 10 appel(s) de modèle, 6 appel(s) d'outil, 1 approbation(s), 3 sous-run(s)
```

L'arbre a un niveau de plus : `relance` → `verificateur` → `arbitre`.

**À retenir.**

- Règle : un sous-agent n'est proposé à un run que si la **profondeur de ce run** est strictement inférieure au `max_depth` de **son agent**. Le run racine est à la profondeur 0 ; avec `max_depth: 1`, il appelle ses sous-agents, mais ceux-ci ne peuvent pas appeler les leurs. Le niveau suivant demande `max_depth: 2` **sur l'agent intermédiaire** (ici `verificateur`), pas sur la racine.
- Un outil masqué par la profondeur disparaît simplement de la liste que voit le modèle : il n'y a pas d'erreur à la configuration. C'est ce qui garde bornée une chaîne d'agents qui s'appelleraient en boucle.
- **Piège.** Si un sous-agent « ne fait rien » alors que vous l'avez déclaré, vérifiez d'abord `max_depth` du niveau qui doit l'appeler.

### Exemple 17.4 : annuler un run qui a des enfants

**Pourquoi.** L'artisan s'aperçoit qu'il a demandé la relance du mauvais devis. Il doit pouvoir tout arrêter, y compris ce que font les sous-agents.

**Objectif.** Annuler un run racine et constater ce qui arrive à ses enfants, dans deux situations : un enfant qui attend une approbation, et un enfant en plein travail.

**Mise en place.** Il n'y a pas de commande `loom cancel` : l'annulation passe par `Loom.cancel()`, par `POST /v1/runs/{id}/cancel` (chapitre 20) ou par l'outil MCP `cancel` (chapitre 21). Le script `annuler.py` soumet un run en arrière-plan (`submit`, chapitre 19), attend le bon moment puis l'annule :

```python
import asyncio
import sys

from loom_ia.access import Loom


async def main(attente: str) -> None:
    async with Loom.from_config("loom.yaml") as loom:
        run_id = await loom.submit(
            "relance", "Relance Mme Martin pour le devis D-2026-042, sur un ton cordial."
        )
        # On annule soit quand l'expéditeur attend l'approbation, soit en plein contrôle.
        while True:
            events = await loom.events(run_id)
            if attente == "approbation" and (await loom.state(run_id)).status.value == "waiting_child":
                break
            if attente == "actif" and any(
                e.type == "tool.called" and e.agent == "verificateur" for e in events
            ):
                break
            await asyncio.sleep(0.05)

        print("annulé :", await loom.cancel(run_id, by="denis"))

        events = await loom.events(run_id)
        for run in [run_id, *[e.run_id for e in events if e.type == "run.started" and e.run_id != run_id]]:
            state = await loom.state(run, session_id=run_id)
            print(f"  {state.agent:12} {state.status.value}")
        racine = await loom.result(run_id)
        print("approbations encore affichées :", len(racine.pending_approvals))


asyncio.run(main(sys.argv[1]))
```

**Exécution.** Premier cas : l'enfant attend l'approbation d'un e-mail.

```bash
rm -rf data
uv run python annuler.py approbation
```

```text
annulé : True
  relance      cancelled
  verificateur completed
  arbitre      completed
  expediteur   paused
approbations encore affichées : 1
```

Le run racine est `cancelled`, état **terminal** : il ne se reprendra pas. L'enfant `expediteur`, lui, est resté `paused` dans le journal : il n'a pas d'événement d'annulation propre. Et le run racine affiche encore une approbation en attente. Que se passe-t-il si quelqu'un l'accorde malgré tout ?

```bash
uv run loom approve $(ls data/default | head -1 | sed 's/.jsonl//') --by denis
ls data
```

```text
Accordé : fake_0_0
—

Statut     : cancelled · itérations : 4 · tokens : 7153/653 · coût : 0.0218 $
Run        : 01a12675-1519-7086-93c1-b53889b37012
default
```

La commande affiche « Accordé » mais le run reste `cancelled` et `data/` ne contient pas de `emails.log` : **aucun e-mail n'est parti**. L'annulation est bien effective, c'est l'affichage de l'approbation qui est trompeur.

Second cas : l'enfant est en plein travail. Pour avoir le temps de l'interrompre, donnez-lui un outil lent. Dans `outils.py`, ajoutez `import asyncio` en première ligne, puis :

```python
@tool
async def controle_lent(numero: str) -> str:
    """Contrôle approfondi du devis (long : cinq secondes)."""
    await asyncio.sleep(5)
    return f"devis {numero} contrôlé"
```

Dans `agents/verificateur.yaml`, ajoutez `- python: controle_lent` à `tools`, et dans le script du modèle `VERIF`, remplacez `chercher_devis` par `controle_lent` à la première réponse. Puis :

```bash
rm -rf data
time uv run python annuler.py actif
```

```text
annulé : True
  relance      cancelled
  verificateur awaiting_tools
approbations encore affichées : 0

real	0m0.724s
```

Le script a fini en moins d'une seconde alors que l'outil en demandait cinq : **l'exécution de l'enfant a bien été interrompue** avec celle du parent. Son run `verificateur` reste cependant à l'état `awaiting_tools` dans le journal, sans événement de clôture : comme le dit le code, l'annulation d'un parent n'écrit rien pour ses enfants, qui restent « reprenables ». (Pensez à retirer `controle_lent` ensuite.)

**À retenir.**

- Annuler le run racine stoppe l'exécution de tous ses descendants, et le run racine devient `cancelled` (terminal). C'est le seul run **clos** au journal : les runs enfants gardent l'état qu'ils avaient.
- `Loom.recover()` (chapitre 19) ne remet en file que les runs **racines** laissés en plan : il ne ressuscitera pas un enfant d'un racine annulé.
- Dans un tableau de bord, jugez de l'état d'un travail par le statut du **run racine**, pas par celui de ses enfants.
- **Piège.** Après une annulation, `pending_approvals` peut encore afficher une approbation, et `loom approve` répond « Accordé » sans rien exécuter. Vérifiez `status` avant d'afficher un bouton « Approuver ».

---
## 18. Gros résultats et fichiers : le journal reste léger

### Ce qu'il faut comprendre avant de commencer

Un outil peut renvoyer beaucoup de texte (la liste de tous les devis ouverts) ou un fichier (une étiquette PNG). Un utilisateur peut joindre une photo. Si tout cela finissait dans le journal et dans la conversation, le coût en tokens exploserait et le journal grossirait vite. Loom range donc les fichiers **à part** et ne laisse dans le journal qu'une référence.

| Réglage | Où | Effet |
|---|---|---|
| `offload_over: N` | entrée d'outil de l'agent | Seuil en caractères au-delà duquel le résultat d'un outil est déporté (50 000 par défaut). |
| `execution.attachments` | racine de la config | `max_bytes` (5 Mio par défaut), `types` (les images JPEG, PNG, GIF, WebP par défaut), `max_files` (10 par défaut). |
| `storage.artifacts` | racine de la config | Où ranger les fichiers : `local` (avec un `path`) ou `memory`. Par défaut, dans `.artifacts` à côté du journal JSONL. |
| `capabilities: {vision: true}` | modèle | Autorise un modèle à recevoir des images. |
| `capabilities: {thinking: true}` | modèle | Le modèle produit un raisonnement que Loom conserve. |
| `cache: {system, tools, messages}` | modèle `sdk: anthropic` | Pose les points de cache de prompt du fournisseur. |

Ce que fait le moteur :

- **Déport.** Quand le résultat d'un outil dépasse `offload_over`, Loom l'écrit dans le stockage d'artefacts, et le modèle ne voit qu'un **aperçu** (environ 2 000 caractères) suivi d'une note qui donne la référence `artifact://…`. À partir de ce moment, l'agent reçoit un outil de plus, `artifact_read(ref, offset, limit)`, pour lire la suite par morceaux (en caractères). Cet outil n'accepte que les références **déportées par ce run** : une autre référence est refusée avec la liste de celles qui sont valides.
- **Sans stockage d'artefacts**, le résultat est simplement **tronqué** au seuil, avec une note qui le dit.
- **Fichiers produits.** Un outil Python qui renvoie une `Image` (importée de `loom_ia.tools`) produit un fichier : Loom le range, et le modèle reçoit la référence à la place des octets.
- **Pièces jointes.** Elles sont contrôlées **avant** que le run commence : signature binaire (le contenu doit vraiment être l'image annoncée), taille, type, nombre. Une pièce refusée empêche tout écrit. Une image jointe n'est visible que d'un **rôle vision** (contexte `attachments`, modèle `vision: true`). Ce rôle n'est proposé à l'orchestrateur que si le run contient une image.
- **Récupération.** `RunResult.artifacts` liste tous les fichiers du run (avec leur `origin` : `offload`, `attachment` ou `tool_output`), et `RunResult.produced` seulement ceux que les outils ont produits. `await loom.artifact(uri)` rend les octets.

### Exemple 18.1 : déporter la liste de tous les devis et la lire par morceaux

**Pourquoi.** Madame Martin n'est pas la seule cliente : l'artisan veut « faire le point » sur les 120 devis sans réponse. La liste fait plus de 7 000 caractères. La coller dans la conversation à chaque tour coûterait cher pour rien : le modèle n'a besoin que de la fin.

**Objectif.** Déporter la liste, voir où le fichier est rangé, et faire lire sa fin par `artifact_read`.

**Mise en place.** On ajoute à `outils.py` un outil qui fabrique 120 devis fictifs. Remplacez le haut du fichier pour ajouter les imports dont les exemples suivants ont aussi besoin :

```python
import struct
import zlib
from pathlib import Path

from loom_ia.tools import Image, tool
```

Puis, à la fin de `outils.py` :

```python
@tool
def lister_devis_ouverts() -> str:
    """Liste tous les devis envoyés restés sans réponse (un par ligne)."""
    lignes = [
        f"D-2026-{n:03d} | client {n:03d} | {900 + (n * 37) % 4000} € TTC | envoyé le 2026-09-{1 + n % 28:02d}"
        for n in range(1, 121)
    ]
    return "\n".join(lignes)


def _png(largeur: int, hauteur: int, rvb: tuple[int, int, int]) -> bytes:
    """Fabrique un PNG uni, sans bibliothèque d'images."""

    def bloc(type_: bytes, donnees: bytes) -> bytes:
        return struct.pack(">I", len(donnees)) + type_ + donnees + struct.pack(
            ">I", zlib.crc32(type_ + donnees)
        )

    lignes = b"".join(b"\x00" + bytes(rvb) * largeur for _ in range(hauteur))
    return (
        b"\x89PNG\r\n\x1a\n"
        + bloc(b"IHDR", struct.pack(">IIBBBBB", largeur, hauteur, 8, 2, 0, 0, 0))
        + bloc(b"IDAT", zlib.compress(lignes))
        + bloc(b"IEND", b"")
    )


@tool
def etiquette_devis(numero: str) -> Image:
    """Produit l'étiquette (image PNG) à coller sur le dossier d'un devis."""
    return Image(data=_png(120, 40, (30, 90, 160)), name=f"etiquette-{numero}.png")
```

(`_png` fabrique un petit PNG uni avec la bibliothèque standard : l'exemple n'a besoin d'aucun logiciel d'image.)

Une troisième configuration, `fichiers.yaml`, avec ses agents dans `agents_fichiers/`, pour ne pas toucher au projet principal. Elle contient d'emblée les modèles des exemples 18.2 et 18.3, que l'on découvre ensuite un à un.

```bash
mkdir agents_fichiers
```

`fichiers.yaml` :

```yaml
version: 1

imports: [outils]
agents_dir: agents_fichiers/

models:
  - id: CARNET
    sdk: fake
    model: fake-carnet
    pricing: {input: 0.8, output: 4.0}
    params:
      script:
        - text: Je consulte le carnet.
          tool_calls:
            - {name: lister_devis_ouverts, arguments: {}}
        - text: Je lis la fin de la liste pour connaître le dernier devis.
          tool_calls:
            - name: artifact_read
              arguments:
                ref: artifact://default/carnet/6086b54db14960ae03238937f41c7bcef1b19a0fd148b14f5c93b8451a6b8b2d.txt
                offset: 6900
                limit: 400
        - text: Il y a 120 devis ouverts, de D-2026-001 à D-2026-120.
  - id: PHOTO_MAIN
    sdk: fake
    model: fake-photo-main
    pricing: {input: 0.8, output: 4.0}
    params:
      script:
        - text: Je fais décrire la photo.
          with_tool: decrire_photo
          tool_calls:
            - {name: decrire_photo, arguments: {consigne: "Décris ce que montre la photo de chantier."}}
        - text: "La photo montre l'arrivée d'eau d'une chaudière, avec un raccord rouge."
          with_tool: decrire_photo
        - text: Aucune photo n'est jointe à la demande.
          without_tool: decrire_photo
  - id: VISION
    sdk: fake
    model: fake-vision
    pricing: {input: 0.8, output: 4.0}
    capabilities: {vision: true}
    params:
      script:
        - text: Une surface rouge unie, sans détail exploitable.
  - id: ETIQUETTE
    sdk: fake
    model: fake-etiquette
    pricing: {input: 0.8, output: 4.0}
    params:
      script:
        - text: Je produis l'étiquette.
          tool_calls:
            - {name: etiquette_devis, arguments: {numero: D-2026-042}}
        - text: L'étiquette du devis D-2026-042 est prête.
  - id: PENSEUR
    sdk: fake
    model: fake-penseur
    pricing: {input: 0.8, output: 4.0}
    capabilities: {thinking: true}
    params:
      script:
        - reasoning: "Je dois d'abord lire le devis avant de conclure."
          tool_calls:
            - {name: chercher_devis, arguments: {numero: D-2026-042}}
        - reasoning: "1 840 € TTC, envoyé le 3 septembre : plus de trente jours, une relance se justifie."
          text: Le devis D-2026-042 mérite une relance.

telemetry:
  logging: {level: WARNING}

storage:
  events: {backend: jsonl, path: data-fichiers}
```

Le script du modèle `CARNET` est écrit pour la démonstration : la référence `artifact://…` qu'il utilise est déterministe (l'empreinte est celle du contenu, toujours le même ici). Avec un vrai modèle, c'est lui qui recopierait la référence donnée dans la note de déport.

`agents_fichiers/carnet.yaml` :

```yaml
name: carnet
description: Fait le point sur les devis restés sans réponse.

main:
  model: CARNET
  system: "Tu aides la Plomberie Dupont à faire le point sur ses devis sans réponse. Utilise `lister_devis_ouverts`, puis réponds en une phrase."

max_iterations: 5

tools:
  - python: lister_devis_ouverts
    offload_over: 2000        # au-delà de 2 000 caractères, le résultat est déporté
```

Les trois autres agents de `agents_fichiers/` sont donnés tout de suite, pour que la configuration se charge d'un bloc. Ils serviront aux exemples 18.2 et 18.3.

`agents_fichiers/photo.yaml` :

```yaml
name: photo
description: Décrit une photo de chantier jointe à la demande.

main:
  model: PHOTO_MAIN
  system: "Tu aides un plombier à lire les photos de ses chantiers. Si une photo est jointe, fais-la décrire par `decrire_photo` et résume en une phrase."

max_iterations: 4

roles:
  - name: decrire_photo
    description: Décrit la photo jointe à la demande.
    model: VISION
    system: Tu décris précisément une photo de chantier, en français.
    input_schema:
      type: object
      properties:
        consigne: {type: string, description: "Ce qu'il faut regarder"}
      required: [consigne]
    context: [attachments]       # rôle vision : il reçoit les pièces jointes du run
```

`agents_fichiers/etiquette.yaml` :

```yaml
name: etiquette
description: Produit l'étiquette d'un dossier de devis.

main:
  model: ETIQUETTE
  system: "Tu produis l'étiquette d'un devis avec `etiquette_devis`, puis tu confirmes."

max_iterations: 4

tools:
  - python: etiquette_devis
```

`agents_fichiers/reflexion.yaml` :

```yaml
name: reflexion
description: Dit s'il faut relancer un devis, après réflexion.

main:
  model: PENSEUR
  system: Tu dis s'il faut relancer un devis. Consulte-le avec `chercher_devis`.

max_iterations: 4

tools:
  - python: chercher_devis
```

**Exécution.**

```bash
rm -rf data-fichiers
uv run loom --config fichiers.yaml run carnet "Fais le point sur les devis sans réponse." --session carnet
```

```text
Il y a 120 devis ouverts, de D-2026-001 à D-2026-120.

Statut     : completed · itérations : 3 · tokens : 2764/203 · coût : 0.0030 $
Run        : 01a1267a-8a32-7404-9496-4a547fcdda01
```

Où est passée la liste ? Regardez l'arbre du run (remplacez l'identifiant par le vôtre) et le dossier :

```bash
uv run loom --config fichiers.yaml inspect 01a1267a-8a32-7404-9496-4a547fcdda01 --session carnet | sed -n 6,20p
find data-fichiers -type f
```

```text
run carnet — completed, 30 ms, 0.003023 $
  étape 1
    modèle fake-carnet (main) — 2 ms, 254 → 63 tokens, 0.000455 $
      · répond    : Je consulte le carnet.
      · appelle   : lister_devis_ouverts({})
  étape 2
    outil lister_devis_ouverts — 3 ms
      · fichier rangé : artifact://default/carnet/6086b54db14960ae03238937f41c7bcef1b19a0fd148b14f5c93b8451a6b8b2d.txt
      · résultat  : D-2026-001 | client 001 | 937 € TTC | envoyé le 2026-09-02 D-2026-002 | client 002 | 974 € TTC | …
  étape 3
    modèle fake-carnet (main) — 1 ms, 1124 → 102 tokens, 0.001307 $
      · répond    : Je lis la fin de la liste pour connaître le dernier devis.
      · appelle   : artifact_read({"ref": "artifact://default/carnet/6086b54db14960ae03238937f41c7bcef1b19a0fd148b14f…
  étape 4
    outil artifact_read — 1 ms
data-fichiers/.artifacts/default/carnet/6086b54db14960ae03238937f41c7bcef1b19a0fd148b14f5c93b8451a6b8b2d.txt
data-fichiers/default/carnet.jsonl
```

Le fichier est à côté du journal, dans `.artifacts`. Voyons maintenant ce que le **modèle** a reçu à l'étape 2, et ce que renvoie `artifact_read`. Les deux sont dans le journal :

```bash
uv run loom --config fichiers.yaml sessions export carnet \
  | jq -r 'select(.type=="tool.completed" and .payload.tool_name=="lister_devis_ouverts") | .payload.output.blocks[0].text' | tail -5
uv run loom --config fichiers.yaml sessions export carnet \
  | jq -r 'select(.type=="tool.completed" and .payload.tool_name=="artifact_read") | .payload.output.blocks[0].text' | head -2
```

```text
D-2026-032 | client 032 | 2084 € TTC | envoyé le 2026-09-05
D-2026-033 | client 033 | 2121 € TTC | envoyé le 2026-09-06
D-2026-034 | client 03…

[Résultat déporté : 7195 caractères (texte), seul l'aperçu ci-dessus est montré. Pour lire la suite : outil artifact_read avec ref="artifact://default/carnet/6086b54db14960ae03238937f41c7bcef1b19a0fd148b14f5c93b8451a6b8b2d.txt", offset et limit en caractères.]
[Caractères 6900 à 7195 sur 7195 ; fin du contenu]
26-116 | client 116 | 1192 € TTC | envoyé le 2026-09-05
```

Le modèle a vu une trentaine de lignes sur 120, puis la note qui lui explique comment lire la suite. L'aperçu fait 2 263 caractères au total, note comprise, au lieu de 7 195. Le deuxième appel lit la fin : « fin du contenu » dit au modèle qu'il n'y a rien après.

**À retenir.**

- Le seuil `offload_over` se règle **par outil**. Les 50 000 caractères par défaut conviennent à un outil de recherche ordinaire ; descendez le seuil pour un outil dont les résultats sont bruyants.
- L'outil `artifact_read` n'est proposé qu'**après** le premier déport du run, et il n'accepte que les références déportées par ce run. Le modèle ne peut pas lire n'importe quel fichier du stockage.
- Offset et limite comptent des **caractères**, pas des octets. Le fichier fait 7 555 octets pour 7 195 caractères, parce que chaque « € » en prend trois.
- **Piège.** Si l'agent n'a pas de stockage d'artefacts, le résultat n'est pas déporté mais **tronqué**, et le modèle ne peut pas lire la suite. Gardez un stockage d'artefacts dès que vous fixez un `offload_over`.

### Exemple 18.2 : joindre une photo et récupérer un fichier produit

**Pourquoi.** L'artisan photographie une chaudière sur un chantier et veut que l'agent la lise. À l'inverse, un outil doit pouvoir rendre un fichier, ici l'étiquette d'un dossier de devis, sans que ses octets passent par le modèle.

**Objectif.** Joindre une image à un run, voir qu'elle est rangée et décrite, récupérer l'étiquette produite par un outil, puis voir les refus.

**Mise en place.** Créez une petite image de test avec la fonction `_png` de `outils.py` :

```bash
uv run python -c "from outils import _png; open('chaudiere.png','wb').write(_png(64,64,(200,30,30)))"
```

Puis ajoutez à la fin de `fichiers.yaml` une politique de pièces jointes volontairement stricte, pour pouvoir provoquer les refus :

```yaml
execution:
  attachments: {max_bytes: 100000, types: [image/png], max_files: 2}
```

**Exécution.** Avec la photo :

```bash
uv run loom --config fichiers.yaml run photo "Que montre la photo ?" --attach chaudiere.png --session photo
```

```text
La photo montre l'arrivée d'eau d'une chaudière, avec un raccord rouge.

Statut     : completed · itérations : 2 · tokens : 1151/156 · coût : 0.0015 $
Run        : 01a1267a-6a7e-7785-aaac-439184e55598
```

Sans la photo, le rôle vision n'est pas proposé à l'orchestrateur (il serait inutilisable), et le script le sait :

```bash
uv run loom --config fichiers.yaml run photo "Que montre la photo ?" --session photo2
```

```text
Aucune photo n'est jointe à la demande.

Statut     : completed · itérations : 1 · tokens : 194/35 · coût : 0.0003 $
Run        : 01a12679-cfeb-7362-b283-633d60348f7b
```

L'étiquette, produite par un outil, est rendue avec le résultat :

```bash
uv run loom --config fichiers.yaml run etiquette "Étiquette du devis D-2026-042." --session etiq
```

```text
L'étiquette du devis D-2026-042 est prête.

Statut     : completed · itérations : 2 · tokens : 571/102 · coût : 0.0009 $
Run        : 01a1267a-70f2-76b2-8307-0243bf2e634b
Fichier    : artifact://default/etiq/3344912e8d2427550cedc5ac765326614ccdadfd83c06836f7bdcb0c0836b4d8.png (image/png, 157 octets)
```

Les refus, avant tout appel de modèle :

```bash
python3 -c "d=open('chaudiere.png','rb').read(); open('grosse.png','wb').write(d+b'\0'*150000)"
head -c 300 /dev/urandom > note.png
uv run loom --config fichiers.yaml run photo "x" --attach chaudiere.png --attach chaudiere.png --attach chaudiere.png
uv run loom --config fichiers.yaml run photo "x" --attach note.png
uv run loom --config fichiers.yaml run photo "x" --attach grosse.png
```

```text
Demande refusée : 3 pièces jointes, au-delà de la limite de 2
Demande refusée : note.png : format non reconnu (acceptés : image/png)
Demande refusée : grosse.png : 150178 octets, au-delà de la limite de 100000
```

`note.png` porte une extension d'image mais ses octets sont aléatoires : Loom lit la signature, pas le nom. Ce qu'on récupère depuis Python se montre avec un script `fichiers.py` :

```python
import asyncio

from loom_ia.access import Loom
from loom_ia.core.model import Attachment


async def main() -> None:
    async with Loom.from_config("fichiers.yaml") as loom:
        photo = Attachment.from_path("chaudiere.png")
        resultat = await loom.run(
            "photo", "Que montre la photo du chantier ?", attachments=[photo]
        )
        print("réponse :", resultat.text)
        for a in resultat.artifacts:
            print(f"{a.origin:12} {a.media_type} {a.size} octets {a.name} {a.uri[:40]}…")

        resultat = await loom.run("etiquette", "Étiquette pour le devis D-2026-042.")
        for a in resultat.produced:
            octets = await loom.artifact(a.uri)
            print("produit :", a.name, len(octets), "octets,", octets[:4])


asyncio.run(main())
```

```bash
uv run python fichiers.py
```

```text
réponse : La photo montre l'arrivée d'eau d'une chaudière, avec un raccord rouge.
attachment   image/png 178 octets chaudiere.png artifact://default/01a1267a-6d85-7177-8d…
produit : etiquette-D-2026-042.png 157 octets, b'\x89PNG'
```

Dans le journal (`sessions export photo`), la pièce jointe a donné un événement `artifact.stored` d'origine `attachment`, et l'étiquette un événement d'origine `tool_output`.

**À retenir.**

- Les trois origines de fichier : `attachment` (jointe par l'appelant), `tool_output` (produite par un outil), `offload` (résultat déporté). `RunResult.produced` ne garde que `tool_output`.
- Une pièce jointe est rangée sous `artifact://<client>/<session>/<empreinte>.<ext>`.
- Les refus ont lieu **avant** le run : rien n'est écrit au journal, aucun token n'est dépensé. En Python, `loom.run()` lève `AttachmentError`.
- Les formats acceptés sont des images (JPEG, PNG, GIF, WebP). Les PDF et l'audio ne passent pas encore (c'est le point #013 du backlog du projet).
- **Piège.** `types` ne peut que **restreindre** les formats reconnus, pas en ajouter. Et un rôle vision sans modèle `vision: true` est refusé dès `loom validate`, pas à l'exécution.

### Exemple 18.3 : le raisonnement du modèle et le cache de prompt

**Pourquoi.** Les modèles « à raisonnement » produisent des pensées intermédiaires que certains fournisseurs exigent de revoir dans la boucle d'outils. Par ailleurs, un prompt système long, renvoyé à chaque appel, se paie à chaque fois, sauf si le fournisseur le met en cache.

**Objectif.** Constater que le raisonnement est conservé au journal et que Loom vérifie les réglages de cache au chargement.

**Mise en place.** Le modèle `PENSEUR` et l'agent `reflexion` sont déjà dans les fichiers de l'exemple 18.1 : le modèle déclare `capabilities: {thinking: true}`, et son script produit un bloc `reasoning` avant chaque réponse.

**Exécution.** Deux runs dans la même session :

```bash
uv run loom --config fichiers.yaml run reflexion "Faut-il relancer D-2026-042 ?" --session refl
uv run loom --config fichiers.yaml run reflexion "Et si on attend une semaine de plus ?" --session refl
uv run loom --config fichiers.yaml sessions export refl \
  | jq -r 'select(.type=="model.responded") | .payload.message.blocks[] | select(.type=="reasoning") | .text' | head -2
```

```text
Le devis D-2026-042 mérite une relance.

Statut     : completed · itérations : 2 · tokens : 682/157 · coût : 0.0012 $
Run        : 01a1267a-200b-7738-8004-1cfb4eba63d4
Le devis D-2026-042 mérite une relance.

Statut     : completed · itérations : 2 · tokens : 1210/157 · coût : 0.0016 $
Run        : 01a1267a-22e6-744d-bf68-ae73be9e8a92
Je dois d'abord lire le devis avant de conclure.
1 840 € TTC, envoyé le 3 septembre : plus de trente jours, une relance se justifie.
```

Le journal garde le raisonnement de chaque réponse du modèle. La règle, côté fournisseur, est autre : il n'est renvoyé au modèle **que pendant la boucle d'outils en cours**, et seulement à un modèle déclaré `thinking: true` (certains fournisseurs refusent ce champ sinon). Les runs suivants de la session relisent l'historique **sans** le raisonnement, ce que décrit aussi la documentation (« les tentatives refusées et le raisonnement en sont exclus »).

Le cache de prompt ne se joue pas avec le modèle simulé, mais ses garde-fous se testent. Copiez `fichiers.yaml` vers `cache.yaml` et ajoutez à un modèle `sdk: anthropic` :

```yaml
  - id: HAIKU
    sdk: anthropic
    model: claude-haiku-4-5
    cache: {system: true, tools: true, messages: true}
```

Sans clé d'API, le chargement le dit :

```bash
uv run loom --config cache.yaml validate
```

```text
Modèle 'HAIKU' : la variable d'environnement ANTHROPIC_API_KEY est absente ou vide
```

(Avec une clé bidon exportée, `validate` passe.) Avec `sdk: openai` à la place, le même `cache:` est refusé :

```text
'cache' pose des points de cache pour sdk: anthropic ; les API OpenAI et leurs compatibles cachent seules
```

**À retenir.**

- Le raisonnement est conservé au journal sous une forme neutre, indépendante du fournisseur : on peut basculer d'un fournisseur à l'autre en cours de run.
- `thinking: true` sert à deux choses : Loom sait que le modèle produit du raisonnement, et il le lui renvoie dans la boucle d'outils.
- `cache` est propre à `sdk: anthropic`. Au plus quatre points de cache par requête, jamais sur un bloc de raisonnement. Un prompt plus court que le minimum du modèle n'est pas mis en cache (4 096 tokens pour Claude Haiku 4.5).
- **Non exécuté ici** : le cache réel (il demande une clé Anthropic et un prompt assez long). Seule la validation de la configuration est démontrée.
- **Piège.** `pricing.cache_write` doit refléter la durée de cache choisie (`ttl: 5m` ou `1h`), sinon les coûts du rapport (chapitre 16) seront faux.

### Exemple 18.4 : choisir où ranger les fichiers avec `storage.artifacts`

**Pourquoi.** Par défaut, les fichiers vont dans `.artifacts` à côté du journal. Pour une sauvegarde plus simple, ou pour séparer les gros fichiers du journal, vous voulez les ranger ailleurs.

**Objectif.** Changer l'emplacement des fichiers et vérifier le résultat.

**Mise en place.** Dans `storage` de `fichiers.yaml`, ajoutez la ligne `artifacts` :

```yaml
storage:
  events: {backend: jsonl, path: data-fichiers}
  artifacts: {backend: local, path: fichiers-artefacts}
```

**Exécution.**

```bash
rm -rf data-fichiers
uv run loom --config fichiers.yaml validate | grep -E 'Journal|Artefacts'
uv run python fichiers.py
find data-fichiers fichiers-artefacts -type f
```

```text
Journal    : jsonl (/chemin/vers/relance/data-fichiers)
Artefacts  : local (/chemin/vers/relance/fichiers-artefacts)
```

(`validate` affiche d'autres lignes avant et après celles-ci ; on ne garde que celles qui nous intéressent.) Puis, après `fichiers.py`, le résultat de `find` :

```text
data-fichiers/default/01a1267b-e09b-779e-8232-7a0692f860bf.jsonl
data-fichiers/default/01a1267b-e0be-73d6-b740-44a93ed0ef3e.jsonl
fichiers-artefacts/default/01a1267b-e0be-73d6-b740-44a93ed0ef3e/3344912e8d2427550cedc5ac765326614ccdadfd83c06836f7bdcb0c0836b4d8.png
fichiers-artefacts/default/01a1267b-e09b-779e-8232-7a0692f860bf/574470afa5eba2378a14729a0b8b28fed44877225142042658cc1b8a983e79d9.png
```

Les fichiers sont maintenant séparés du journal : le dossier `data-fichiers` ne contient que du texte. Le rangement suit le client, puis la session (ou le run, quand la session n'est pas nommée), puis l'empreinte du contenu.

**À retenir.**

- `backend: memory` existe aussi, pour les tests : les fichiers disparaissent avec le processus.
- Journal et fichiers se sauvegardent ensemble : un journal qui référence un `artifact://` absent donnera une erreur à la lecture (`ArtifactNotFound`).
- Les seuls backends de fichiers sont `local` et `memory` : même avec un journal Postgres (chapitre 24), les fichiers restent sur disque. À prévoir pour les sauvegardes.

---
## 19. Exécution durable : survivre à un redémarrage

### Ce qu'il faut comprendre avant de commencer

Jusqu'ici, un run vivait dans un process : si le process s'arrêtait, le run mourait avec lui. Un service doit mieux faire. Un déploiement, un manque de mémoire, un `kill -9` ne doivent pas faire perdre la relance d'une cliente, ni la faire partir deux fois.

Loom y arrive parce que **le journal fait foi** : chaque événement écrit est un point de sauvegarde, et l'état d'un run n'est que le résultat de la relecture de son journal. Reprendre un run, c'est relire son journal et continuer là où il s'est arrêté.

| API Python | Commande | Rôle |
|---|---|---|
| `Loom.resume(run_id, session_id=…)` | `loom resume <run_id> --session …` | Reprend un run précis. |
| `Loom.recover()` | (à appeler au démarrage d'un service) | Remet en file tous les runs racines laissés en plan. |
| `Loom.submit(…)` | (`"background": true` en REST) | Ouvre un run, l'inscrit au journal, rend son identifiant **avant** qu'il ne tourne. |
| `Loom.result(run_id)` | | Relit ce que le run a produit, sans l'attendre. |
| `Loom.state(run_id)`, `Loom.events(run_id)` | `loom inspect` | État et journal d'un run. |
| `Loom.follow(run_id)` | | Journal en direct : les événements déjà écrits, puis les suivants. |
| `Loom.cancel(run_id, by=…)` | | Arrête un run et le clôt (`run.cancelled`). Terminal. |
| `@idempotent` | | L'effet d'un outil n'est produit qu'une fois. |
| `storage.idempotency` | | Magasin des clés d'idempotence : `journal` (défaut), `memory`, `sqlite`, `postgres`, `redis`. |
| `execution.lease` | | Durée de la concession (« bail ») d'un run : 60 s par défaut. |
| `timeout` (clé de l'agent) | | Borne le temps de pilotage cumulé d'un run, en secondes (exemple 19.6). |

Quatre règles font tout l'ensemble :

- **Un seul pilote par run.** Le worker qui pilote un run prend une **concession** (un bail), inscrite au journal (`run.claimed`), et la renouvelle tant qu'il travaille. Si le worker meurt, la concession expire et un autre peut reprendre le run. Tant qu'elle court, toute tentative de reprise est refusée (`ClaimConflict`).
- **Un outil terminé n'est jamais relancé.** Le journal contient son résultat.
- **Un outil interrompu n'est relancé que s'il est sûr de l'être :** il n'a pas d'effet de bord, ou il est `@idempotent`.
- **Pour les autres,** on ne sait pas s'il a agi. L'outil choisit alors avec `on_unknown` : `error` (défaut) prévient le modèle, `pause` fait vérifier un humain avant toute nouvelle tentative.

Le journal doit être **durable** : un journal `memory` (le défaut quand on ne déclare rien) ne survit pas au process. Dans ce chapitre, on utilise `sqlite`.

### Exemple 19.1 : tuer le process en plein run, puis reprendre

**Pourquoi.** Le serveur de l'artisan redémarre au milieu d'une relance. L'agent avait déjà retrouvé le devis, et attendait une consultation d'agenda. Il ne doit ni recommencer depuis le début, ni oublier la relance.

**Objectif.** Provoquer un vrai `kill -9`, constater ce que le journal a gardé, puis reprendre le run avec `loom resume`.

**Mise en place.** On repart d'un agent plus simple que celui du chapitre 17 : un seul orchestrateur, sans sous-agent. Une nouvelle configuration, `service.yaml`, avec son dossier d'agents `agents_service/`, laisse intacts les fichiers précédents.

```bash
mkdir agents_service
```

`outils_service.py` : un outil **lent**, qui simule la consultation d'un calendrier distant. Il note chaque exécution dans `data/agenda.log`, ce qui permet de **compter** combien de fois il a tourné.

```python
import asyncio
from pathlib import Path

from loom_ia.tools import tool


@tool
async def consulter_agenda(jour: str) -> str:
    """Consulte l'agenda de l'artisan pour un jour donné (lent : il interroge un calendrier distant)."""
    trace = Path("data/agenda.log")
    trace.parent.mkdir(exist_ok=True)
    with trace.open("a", encoding="utf-8") as f:
        f.write(f"consultation de l'agenda du {jour}\n")
    await asyncio.sleep(8)
    return f"{jour} : libre toute la journée"
```

`agents_service/relance.yaml` :

```yaml
name: relance
description: Relance un client pour un devis resté sans réponse.

main:
  model: PRINCIPAL
  system: |-
    Tu es l'assistant de la Plomberie Dupont. Pour relancer un client :
    1. retrouve le devis avec `chercher_devis` ;
    2. vérifie l'agenda de l'artisan avec `consulter_agenda` ;
    3. envoie l'e-mail avec `envoyer_email`.
    Ne calcule et n'invente jamais un montant : reprends celui du devis.

max_iterations: 8

tools:
  - python: chercher_devis
  - python: consulter_agenda
  - python: envoyer_email
```

`service.yaml` : le journal est en **SQLite** (un seul fichier, durable). La concession est ramenée à 5 secondes pour que la démonstration ne dure pas une minute ; en production, laissez le défaut (60 s).

```yaml
version: 1

imports: [outils, outils_service]
agents_dir: agents_service/

models:
  - id: PRINCIPAL
    sdk: fake
    model: fake-principal
    pricing: {input: 3.0, output: 15.0}
    params:
      script:
        - text: Je commence par retrouver le devis.
          tool_calls:
            - {name: chercher_devis, arguments: {numero: D-2026-042}}
        - text: Je consulte l'agenda de l'artisan pour choisir le jour d'envoi.
          tool_calls:
            - {name: consulter_agenda, arguments: {jour: "2026-10-12"}}
        - text: J'envoie la relance.
          tool_calls:
            - name: envoyer_email
              arguments:
                destinataire: mme.martin@example.fr
                objet: Votre devis D-2026-042
                corps: "Bonjour Madame Martin, je me permets de revenir vers vous au sujet du devis D-2026-042 (1 840 €) envoyé le 3 septembre. Restant à votre disposition, cordialement."
        - text: La relance du devis D-2026-042 est partie chez Mme Martin.

telemetry:
  logging: {level: WARNING}

storage:
  events: {backend: sqlite, path: data/journal.db}

execution:
  lease: 5          # secondes (60 par défaut) : raccourci pour la démonstration
```

`plantage.py` : un script qui lance le run en arrière-plan, attend que l'outil lent ait démarré, puis **se tue lui-même** avec `SIGKILL` : pas de nettoyage, pas de `finally`, exactement comme une coupure de courant.

```python
import asyncio
import os
import signal
import sys

from loom_ia.access import Loom


async def main(run_id: str) -> None:
    async with Loom.from_config("service.yaml") as loom:
        await loom.submit(
            "relance",
            "Relance Mme Martin pour le devis D-2026-042, sur un ton cordial.",
            session_id="martin",
            run_id=run_id,
        )
        # On attend que l'outil lent ait démarré, puis on « débranche la prise ».
        while not any(e.type == "tool.called" and e.payload.tool_name == "consulter_agenda"
                      for e in await loom.events(run_id, session_id="martin")):
            await asyncio.sleep(0.05)
        print("l'outil consulter_agenda a démarré, kill -9", flush=True)
        os.kill(os.getpid(), signal.SIGKILL)


asyncio.run(main(sys.argv[1]))
```

**Exécution.** On choisit nous-mêmes l'identifiant du run, pour le retrouver ensuite.

```bash
rm -rf data
RID=$(python3 -c "import uuid; print(uuid.uuid4())")
uv run python plantage.py $RID; echo "code de sortie : $?"
```

```text
l'outil consulter_agenda a démarré, kill -9
code de sortie : 137
```

Le code 137 est celui d'un process tué par `SIGKILL`. Voici ce que le journal a gardé :

```bash
uv run loom --config service.yaml inspect $RID --session martin
```

```text
Run        : e9e9097d-1a1d-4c8d-9ed2-55efa5cde9f5 (agent relance, client default)
Session    : martin
Statut     : awaiting_tools, 2 itération(s) — run inachevé : durées provisoires
Usage      : 1315 → 147 tokens, 0.006150 $, 18 ms de pilotage

run relance — inachevé, 33 ms (ouvert)
  étape 1
    modèle fake-principal (main) — 1 ms, 547 → 70 tokens, 0.002691 $
      · répond    : Je commence par retrouver le devis.
      · appelle   : chercher_devis({"numero": "D-2026-042"})
  étape 2
    outil chercher_devis — 3 ms
      · arguments : {"numero": "D-2026-042"}
      · résultat  : {"numero": "D-2026-042", "client": "Mme Martin", "email": "mme.martin@example.fr", "objet": "Remp…
  étape 3
    modèle fake-principal (main) — 1 ms, 768 → 77 tokens, 0.003459 $
      · répond    : Je consulte l'agenda de l'artisan pour choisir le jour d'envoi.
      · appelle   : consulter_agenda({"jour": "2026-10-12"})
  étape 4 (ouvert)
    outil consulter_agenda — 0 ms (ouvert)
      · arguments : {"jour": "2026-10-12"}

Bilan      : 2 appel(s) de modèle, 2 appel(s) d'outil
```

Le run est resté à l'état `awaiting_tools`. Le devis a été trouvé, l'agenda est « ouvert » (appel commencé, jamais terminé).

On tente de reprendre **tout de suite** :

```bash
uv run loom --config service.yaml resume $RID --session martin 2>&1 | tail -1
```

```text
loom_ia.engine.loop.ClaimConflict: Run e9e9097d-1a1d-4c8d-9ed2-55efa5cde9f5 : piloté par worker-94bfc3648bf7, concession valable jusqu'à 2026-10-10T15:55:52+00:00
```

C'est la concession qui parle : le process est mort, mais son bail court encore pendant 5 secondes, et Loom ne peut pas savoir s'il est mort ou simplement lent. Reprendre trop tôt ferait deux pilotes pour un run. (En 2.0.0, la commande affiche ici une trace Python complète, d'où le `tail -1`.) On attend l'expiration, puis :

```bash
sleep 6
uv run loom --config service.yaml resume $RID --session martin
```

```text
—

Statut     : paused · itérations : 3 · tokens : 2242/269 · coût : 0.0108 $
Run        : e9e9097d-1a1d-4c8d-9ed2-55efa5cde9f5
En attente : envoyer_email (fake_2_0) — loom approve e9e9097d-1a1d-4c8d-9ed2-55efa5cde9f5 --call fake_2_0
```

Le run a repris à l'agenda, l'a terminé, a rédigé l'envoi et s'est arrêté sur l'approbation, comme au chapitre 9. On approuve :

```bash
uv run loom --config service.yaml approve $RID --session martin --by denis
cat data/emails.log
cat data/agenda.log
```

```text
Accordé : fake_2_0
La relance du devis D-2026-042 est partie chez Mme Martin.

Statut     : completed · itérations : 4 · tokens : 3372/308 · coût : 0.0147 $
Run        : e9e9097d-1a1d-4c8d-9ed2-55efa5cde9f5
mme.martin@example.fr | Votre devis D-2026-042
consultation de l'agenda du 2026-10-12
consultation de l'agenda du 2026-10-12
```

Un seul e-mail est parti. Dans le journal, les appels se lisent ainsi :

```bash
uv run loom --config service.yaml sessions export martin \
  | jq -r '.type + " " + (.payload.tool_name // "")' \
  | grep -E "tool\.|approval|run.completed"
```

```text
tool.called chercher_devis
tool.completed chercher_devis
tool.called consulter_agenda
tool.called consulter_agenda
tool.completed consulter_agenda
approval.requested envoyer_email
approval.granted envoyer_email
tool.called envoyer_email
tool.completed envoyer_email
run.completed
```

`chercher_devis` n'a tourné qu'**une fois** (déjà terminé avant le crash). `consulter_agenda` a été appelé deux fois, d'où les deux lignes de `agenda.log` : l'appel interrompu a été **relancé**, parce qu'un outil sans effet de bord n'a rien à craindre d'une seconde exécution. `envoyer_email` n'a tourné qu'une fois.

**À retenir.**

- Un `kill -9` ne perd rien de ce qui est au journal. Le journal doit être durable (`sqlite`, `jsonl`, `postgres`). Avec `memory`, il n'y a rien à reprendre.
- La concession protège contre le double pilotage. Réglez `execution.lease` en pensant au temps qu'il vous faut pour redémarrer : trop court, un process lent est jugé mort ; trop long, une reprise doit attendre.
- Le bail se renouvelle au tiers de sa durée tant que le run tourne : ce sont les événements `run.claimed` qui s'accumulent dans le journal.
- `loom resume` ne s'applique qu'à un run **interrompu**. Un run annulé (`run.cancelled`) est terminal.
- **Piège.** Reprendre « tout de suite » échoue pendant la durée du bail. Un script de déploiement qui relance les runs doit attendre ou réessayer.
- **Écart de version.** En 2.0.0, `loom resume` laisse échapper une trace Python quand la concession est tenue. Le message de l'exception reste lisible, mais une automatisation doit tester le code de sortie plutôt que d'attendre une phrase propre.

### Exemple 19.2 : tout reprendre au démarrage avec `Loom.recover()`

**Pourquoi.** Après un incident, il peut y avoir plusieurs runs en plan, dans plusieurs sessions. Les reprendre à la main, un par un, n'est pas tenable. Un service doit s'en charger au démarrage.

**Objectif.** Remettre en file tous les runs laissés en plan, depuis un script de démarrage.

**Mise en place.** `reprise.py` :

```python
import asyncio

from loom_ia.access import Loom


async def main() -> None:
    async with Loom.from_config("service.yaml") as loom:
        remis = await loom.recover()
        print("remis en file :", [str(r)[:8] for r in remis])
        await loom.drain()
        for run_id in remis:
            etat = await loom.state(run_id, session_id="martin")
            print(f"{str(run_id)[:8]} : {etat.status.value}")


asyncio.run(main())
```

`recover()` balaie toutes les sessions du client, trouve les runs racines non terminés et les met dans la file de l'instance. `drain()` attend que la file se vide. Le script suppose que tous les runs en plan sont dans la session `martin`, ce qui est vrai ici.

**Exécution.** On refait un crash, puis on appelle `recover()` aussitôt, puis après le bail :

```bash
rm -rf data
RID=$(python3 -c "import uuid; print(uuid.uuid4())")
uv run python plantage.py $RID
uv run python reprise.py
sleep 6
uv run python reprise.py
```

```text
l'outil consulter_agenda a démarré, kill -9
remis en file : ['e84d4f6b']
e84d4f6b : awaiting_tools
remis en file : ['e84d4f6b']
e84d4f6b : paused
```

Le premier appel a bien remis le run en file, mais la concession du process mort courait encore : le pilotage a été refusé en silence, et le run est resté `awaiting_tools`. Le second, passé le bail, l'a repris jusqu'à l'approbation (`paused`). `recover()` est donc **sans danger** : on peut l'appeler plusieurs fois, et un run qu'un worker vivant pilote ne sera jamais pris en double.

**À retenir.**

- `recover()` ne reprend que les runs **racines**. Un sous-agent est repris avec son parent.
- Il ne redémarre rien à l'insu de l'appelant : c'est à votre service de l'appeler (au démarrage, et éventuellement à intervalle régulier).
- Un run dont l'agent n'est plus déclaré dans la configuration est ignoré, avec un avertissement dans les logs.
- **Piège.** Au démarrage d'un service qui redémarre en moins que `execution.lease`, un premier `recover()` ne reprendra pas les runs du process précédent. Rappelez-le après le bail, ou à intervalle régulier.

### Exemple 19.3 : soumettre en arrière-plan, suivre, annuler

**Pourquoi.** Un service ne peut pas garder une connexion ouverte pendant toute la durée d'un run (8 secondes ici, plusieurs minutes dans la vraie vie). Il doit rendre un identifiant, puis permettre de suivre, de trancher et d'annuler.

**Objectif.** Parcourir tout le cycle `submit`, `state`, `follow`, `approve`, `result`, `cancel`.

**Mise en place.** `arriere_plan.py` :

```python
import asyncio

from loom_ia.access import Loom

# Événements de plomberie interne, qu'on ne montre pas pour garder une sortie lisible.
BRUIT = {"step.started", "step.completed", "run.transitioned", "run.claimed"}
MESSAGE = "Relance Mme Martin pour le devis D-2026-042, sur un ton cordial."


async def main() -> None:
    async with Loom.from_config("service.yaml") as loom:
        # 1. Un run en arrière-plan : l'identifiant revient aussitôt.
        run_id = await loom.submit("relance", MESSAGE, session_id="martin-bg")
        etat = await loom.state(run_id, session_id="martin-bg")
        print(f"accepté  : {str(run_id)[:8]} -> {etat.status.value}")

        # 2. On suit le journal en direct, jusqu'à la demande d'approbation.
        async for event in loom.follow(run_id, session_id="martin-bg"):
            if event.type not in BRUIT:
                print(f"  {event.seq:>3} {event.type}")
            if event.type == "approval.requested":
                break

        # 3. On attend que le run soit réellement en pause, puis on tranche : il repart.
        while (await loom.state(run_id, session_id="martin-bg")).status.value != "paused":
            await asyncio.sleep(0.05)
        accordes = await loom.approve(run_id, by="denis", session_id="martin-bg")
        print("accordé  :", accordes)
        async for event in loom.follow(run_id, session_id="martin-bg", after_seq=31):
            if event.type not in BRUIT:
                print(f"  {event.seq:>3} {event.type}")
        resultat = await loom.result(run_id, session_id="martin-bg")
        print(f"terminé  : {resultat.status.value} -> {resultat.text}")

        # 4. Un second run, annulé pendant la consultation de l'agenda.
        autre = await loom.submit("relance", MESSAGE, session_id="martin-annule")
        async for event in loom.follow(autre, session_id="martin-annule"):
            if event.type == "tool.called" and event.payload.tool_name == "consulter_agenda":
                break
        print("annulé   :", await loom.cancel(autre, session_id="martin-annule", by="denis"))
        etat = await loom.state(autre, session_id="martin-annule")
        print(f"état     : {etat.status.value}")
        print("annuler encore :", await loom.cancel(autre, session_id="martin-annule", by="denis"))


asyncio.run(main())
```

**Exécution.**

```bash
rm -rf data
uv run python arriere_plan.py
```

```text
accepté  : 01a12682 -> ready_for_model
    1 run.started
    2 message.user
    5 model.responded
    9 tool.called
   10 tool.completed
   14 model.responded
   18 tool.called
   23 tool.completed
   27 model.responded
   31 approval.requested
accordé  : ('fake_2_0',)
   35 approval.granted
   39 tool.called
   40 tool.completed
   44 model.responded
   47 run.completed
terminé  : completed -> La relance du devis D-2026-042 est partie chez Mme Martin.
annulé   : True
état     : cancelled
annuler encore : False
```

Les numéros d'événement (`seq`) sont ceux du journal de la session : `after_seq` permet à un client qui se reconnecte de reprendre le fil sans rien manquer ni rien répéter. C'est ce que fait le SSE de l'API REST au chapitre 20 avec `Last-Event-ID`.

**À retenir.**

- `submit()` écrit `run.started` au journal **avant** de rendre l'identifiant : un run rendu existe, on peut le suivre ou l'arrêter aussitôt.
- `follow()` rejoue d'abord ce qui est écrit, puis suit en direct : même appelé après coup, il ne perd rien. Il se termine à la clôture du run.
- `approve()` accorde tout ce que le run attend (ou l'appel désigné par `call_id`). `by` est le seul audit : mettez-y l'identité de la personne.
- `cancel()` rend `True` la première fois, `False` ensuite : annuler un run déjà fini ne fait rien.
- **Piège.** Attendez que le run soit réellement en pause avant d'approuver. Si votre code approuve dans le même instant où l'événement `approval.requested` arrive, la reprise peut être dédoublonnée avec le pilotage encore en cours, et le run reste `paused` alors que l'approbation est accordée (en 2.0.0, il reste alors à appeler `resume`). Constaté : un délai de 10 ms entre l'événement et l'approbation suffit à l'éviter. Un humain, ou un client REST qui passe par le réseau, n'est pas concerné ; un script qui réagit dans le même instant, si.

### Exemple 19.4 : une clé métier pour ne relancer qu'une fois par devis

**Pourquoi.** Un double clic sur le bouton « Relancer », un webhook livré deux fois, deux collègues qui relancent le même devis : chacun ouvre **son** run. Chacun voudrait envoyer l'e-mail. La clé d'idempotence **technique** n'y fait rien : elle ne protège que la reprise du **même** appel. Pour qu'une action ne soit faite qu'une fois quoi qu'il arrive, il faut une clé **métier**.

**Objectif.** Déclarer une clé métier, voir que le chargement exige un magasin partagé, puis constater que deux runs envoient un seul e-mail.

**Mise en place.** `relances.py` : un outil dont la clé dépend du numéro de devis.

```python
from pathlib import Path

from loom_ia.tools import idempotent, tool


@idempotent(key=lambda args: f"relance:{args['numero']}")
@tool(side_effects="irreversible", approval="always")
async def envoyer_relance(numero: str, destinataire: str, corps: str) -> str:
    """Envoie la relance d'un devis au client. Une seule relance par devis."""
    journal = Path("data/relances.log")
    journal.parent.mkdir(exist_ok=True)
    with journal.open("a", encoding="utf-8") as f:
        f.write(f"{numero} -> {destinataire}\n")
    return f"Relance du devis {numero} envoyée à {destinataire}"
```

`agents_service/relance_unique.yaml` :

```yaml
name: relance-unique
description: Envoie la relance d'un devis, une seule fois par devis.

main:
  model: UNIQUE
  system: Tu envoies la relance d'un devis avec `envoyer_relance`, puis tu confirmes.

max_iterations: 4

tools:
  - python: envoyer_relance
```

Dans `service.yaml`, importez le module (`imports: [outils, outils_service, relances]`) et ajoutez le modèle `UNIQUE` après `PRINCIPAL` :

```yaml
  - id: UNIQUE
    sdk: fake
    model: fake-unique
    pricing: {input: 3.0, output: 15.0}
    params:
      script:
        - text: J'envoie la relance du devis.
          tool_calls:
            - name: envoyer_relance
              arguments:
                numero: D-2026-042
                destinataire: mme.martin@example.fr
                corps: "Bonjour Madame Martin, je reviens vers vous au sujet du devis D-2026-042 (1 840 €)."
        - text: La relance du devis D-2026-042 a été traitée.
```

**Exécution.** Sans magasin d'idempotence partagé, le chargement refuse :

```bash
uv run loom --config service.yaml validate 2>&1 | tail -1
```

```text
Configuration : Agent 'relance-unique' : outil(s) 'envoyer_relance' à clé métier — il leur faut un magasin d'idempotence partagé et durable (sqlite ou postgres ou redis), pas 'journal' (#49)
```

Le magasin `journal` (défaut) range la clé dans le journal **du run** : il ne verrait pas ce qu'a fait un autre run. On déclare un magasin SQLite, dans la section `storage` de `service.yaml` :

```yaml
storage:
  events: {backend: sqlite, path: data/journal.db}
  idempotency: {backend: sqlite, path: data/cles.db}
```

Puis `doublon.py` lance deux runs de deux sessions différentes, avec un approbateur en ligne (qui accorde sans pause, comme au chapitre 9) :

```python
import asyncio

from loom_ia.access import Loom
from loom_ia.core.model import Approved


async def accorder(demande):
    return Approved(by="denis")


async def main() -> None:
    async with Loom.from_config("service.yaml") as loom:
        for session in ("clic-1", "clic-2"):
            resultat = await loom.run(
                "relance-unique",
                "Relance Mme Martin pour le devis D-2026-042.",
                session_id=session,
                approver=accorder,
            )
            print(f"{session} : {resultat.status.value} -> {resultat.text}")


asyncio.run(main())
```

```bash
rm -rf data
uv run python doublon.py
cat data/relances.log
uv run loom --config service.yaml sessions export clic-2 | jq -c 'select(.type=="idempotency.reused") | .payload'
```

```text
clic-1 : completed -> La relance du devis D-2026-042 a été traitée.
clic-2 : completed -> La relance du devis D-2026-042 a été traitée.
D-2026-042 -> mme.martin@example.fr
{"type":"idempotency.reused","key":"default:relance:D-2026-042","call_id":"fake_0_0","tool_name":"envoyer_relance"}
```

Deux runs, deux réponses complètes, **une seule ligne** dans `relances.log`. Le second run a reçu le résultat mémorisé du premier, et le journal le dit : `idempotency.reused`, avec la clé. La clé est préfixée par le client (`default:`) : deux entreprises clientes ne se partagent pas « la relance du devis D-2026-042 ».

Pour mesurer ce que la clé métier change, retirez-la (`@idempotent` tout court) et relancez `doublon.py` : `relances.log` reçoit alors **deux** lignes, parce que la clé technique de chaque run est différente.

**À retenir.**

- **Clé technique** (`@idempotent` seul) : `hash(run_id, call_id)`. Protège la **reprise** d'un même appel. Ne protège pas contre deux runs.
- **Clé métier** (`@idempotent(key=lambda args: …)`) : protège contre toute répétition de la même demande, depuis n'importe quel run. Exige `sqlite`, `postgres` ou `redis`.
- Décorer, c'est **promettre** : l'outil passe à `idempotent: true` et le moteur le relance sans hésiter après un crash. Si votre outil n'est pas réellement sans danger à répéter, ne le décorez pas.
- Si l'outil lève une exception, la réservation est rendue : un outil qui échoue est réputé n'avoir rien produit. Ne levez donc pas **après** avoir agi.
- `ttl` règle combien de temps le résultat reste mémorisé (celui du magasin par défaut). `reservation` règle le temps pendant lequel une clé est « prise » par un appel en cours.
- Pour l'API d'un prestataire d'e-mail qui accepte elle-même une clé d'idempotence, un paramètre annoté `ToolContext` donne accès à celle de l'appel (`ctx`) : transmettez-la, vous cumulez alors les deux protections.
- **Piège.** Une clé métier trop large bloque des actions légitimes. `relance:{numero}` interdit toute seconde relance du même devis, y compris dans trois semaines. Intégrez une date ou un rang dans la clé si vous voulez en autoriser une par période.

### Exemple 19.5 : un appel interrompu dont on ignore l'issue (`on_unknown`)

**Pourquoi.** C'est le pire moment : le serveur est tué **pendant** l'envoi d'un e-mail. Il est peut-être parti, peut-être pas. Le relancer risque un doublon chez la cliente ; ne pas le relancer risque de l'oublier. Loom ne devine pas : l'outil dit ce qu'il veut qu'on fasse.

**Objectif.** Voir les deux réponses possibles, `error` (le modèle est prévenu) et `pause` (un humain vérifie), après un vrai crash.

**Mise en place.** On ajoute à `outils_service.py` un outil d'envoi **lent à confirmer** : l'e-mail part (la ligne est écrite) puis la confirmation tarde six secondes. Ajoutez en fin de fichier :

```python
@tool(side_effects="irreversible")
async def envoyer_email_lent(destinataire: str, objet: str, corps: str) -> str:
    """Envoie un e-mail au client, via un serveur de messagerie lent à confirmer."""
    journal = Path("data/emails.log")
    journal.parent.mkdir(exist_ok=True)
    with journal.open("a", encoding="utf-8") as f:
        f.write(f"{destinataire} | {objet}\n")        # l'e-mail part ici...
    await asyncio.sleep(6)                              # ...mais la confirmation tarde
    return f"E-mail envoyé à {destinataire}"
```

L'outil est déclaré `irreversible` et sans `approval` : il part sans validation humaine, pour que l'on observe seulement la règle de reprise. (Le piège de l'approbation est traité juste après.)

`agents_service/relance_lente.yaml` :

```yaml
name: relance-lente
description: Envoie un e-mail de relance par un serveur de messagerie lent.

main:
  model: LENT
  system: Tu envoies l'e-mail de relance avec `envoyer_email_lent`, puis tu confirmes.

max_iterations: 4

tools:
  - python: envoyer_email_lent
```

Le modèle `LENT`, à ajouter à `service.yaml` :

```yaml
  - id: LENT
    sdk: fake
    model: fake-lent
    pricing: {input: 3.0, output: 15.0}
    params:
      script:
        - text: J'envoie l'e-mail de relance.
          tool_calls:
            - name: envoyer_email_lent
              arguments:
                destinataire: mme.martin@example.fr
                objet: Votre devis D-2026-042
                corps: "Bonjour Madame Martin, je reviens vers vous au sujet du devis D-2026-042 (1 840 €)."
        - text: "L'envoi a été interrompu : je ne peux pas dire si l'e-mail est parti, je ne le renvoie pas sans vérification."
```

(Le modèle simulé répond toujours la même phrase après l'outil. Un vrai modèle lirait le message d'erreur et formulerait lui-même sa réponse.)

`plantage_envoi.py` coupe le process une fois l'e-mail parti, et accorde par avance toute approbation :

```python
import asyncio
import os
import signal
import sys

from loom_ia.access import Loom
from loom_ia.core.model import Approved


async def accorder(demande):
    return Approved(by="denis")


async def main(run_id: str) -> None:
    async with Loom.from_config("service.yaml") as loom:
        envoi = asyncio.create_task(
            loom.run(
                "relance-lente",
                "Relance Mme Martin pour le devis D-2026-042.",
                session_id="martin-lent",
                run_id=run_id,
                approver=accorder,
            )
        )
        # On attend que l'e-mail soit parti (l'outil tourne), puis on coupe tout.
        while not os.path.exists("data/emails.log"):
            await asyncio.sleep(0.05)
        print("l'e-mail est parti, confirmation en attente : kill -9", flush=True)
        os.kill(os.getpid(), signal.SIGKILL)
        await envoi


asyncio.run(main(sys.argv[1]))
```

**Exécution, variante `error` (par défaut).**

```bash
rm -rf data
RID=$(python3 -c "import uuid; print(uuid.uuid4())")
uv run python plantage_envoi.py $RID
cat data/emails.log
sleep 6
uv run loom --config service.yaml resume $RID --session martin-lent
cat data/emails.log
uv run loom --config service.yaml inspect $RID --session martin-lent | sed -n 11,19p
```

```text
l'e-mail est parti, confirmation en attente : kill -9
mme.martin@example.fr | Votre devis D-2026-042
L'envoi a été interrompu : je ne peux pas dire si l'e-mail est parti, je ne le renvoie pas sans vérification.

Statut     : completed · itérations : 2 · tokens : 781/158 · coût : 0.0047 $
Run        : f8b3a9b0-27be-4225-95ee-ac03d7d29940
mme.martin@example.fr | Votre devis D-2026-042
  étape 2 (ouvert)
    outil envoyer_email_lent — 0 ms
      · arguments : {"destinataire": "mme.martin@example.fr", "objet": "Votre devis D-2026-042", "corps": "Bonjour Ma…
  étape 3
    appel envoyer_email_lent — refusé avant exécution (ouvert)
      · résultat  : (erreur) État inconnu : l'exécution de cet outil a été interrompue et il a peut-être produit son …
  étape 4
    modèle fake-lent (main) — 1 ms, 500 → 52 tokens, 0.002280 $
      · répond    : L'envoi a été interrompu : je ne peux pas dire si l'e-mail est parti, je ne le renvoie pas sans v…
```

Une seule ligne dans `emails.log` : l'outil **n'a pas été relancé**. Le modèle a reçu un résultat d'erreur « État inconnu… vérifie avant de le rappeler » et a pu décider de la suite.

**Variante `pause`.** On remplace la déclaration de l'outil par :

```python
@tool(side_effects="irreversible", on_unknown="pause")
```

(`on_unknown` se déclare sur l'outil, pas dans l'agent : le YAML de l'agent le refuse.) Même scénario :

```bash
rm -rf data
RID=$(python3 -c "import uuid; print(uuid.uuid4())")
uv run python plantage_envoi.py $RID
sleep 6
uv run loom --config service.yaml resume $RID --session martin-lent
```

```text
l'e-mail est parti, confirmation en attente : kill -9
—

Statut     : paused · itérations : 1 · tokens : 281/106 · coût : 0.0024 $
Run        : 896bea15-e02c-4899-ba42-11b5c0a73382
En attente : envoyer_email_lent (fake_0_0) — loom approve 896bea15-e02c-4899-ba42-11b5c0a73382 --call fake_0_0
```

Le run est en pause, et la demande d'approbation porte le motif de l'incertitude :

```bash
uv run loom --config service.yaml sessions export martin-lent | jq -r 'select(.type=="approval.requested") | .payload.reason'
```

```text
État inconnu : l'exécution de cet outil a été interrompue et il a peut-être produit son effet. Il n'a pas été relancé automatiquement ; vérifie avant de le rappeler.
```

L'artisan va regarder sa boîte d'envoi. Il y voit l'e-mail : il **refuse** de le renvoyer, en le disant.

```bash
uv run loom --config service.yaml reject $RID --session martin-lent --call fake_0_0 --by denis \
  --reason "Vérifié dans la boîte d'envoi : l'e-mail est bien parti"
cat data/emails.log
```

```text
Refusé : fake_0_0
L'envoi a été interrompu : je ne peux pas dire si l'e-mail est parti, je ne le renvoie pas sans vérification.

Statut     : completed · itérations : 2 · tokens : 763/158 · coût : 0.0047 $
Run        : 896bea15-e02c-4899-ba42-11b5c0a73382
mme.martin@example.fr | Votre devis D-2026-042
```

Un seul e-mail, et la vérification humaine est au journal (qui, quand, pourquoi). S'il avait constaté que l'e-mail n'était **pas** parti, `loom approve` aurait relancé l'outil.

**À retenir.**

- Le critère de reprise est `side_effects` : `none` ou `reversible` relancent sans question ; `irreversible` ne relance que si l'outil est `@idempotent`.
- `on_unknown: error` convient quand le modèle sait réagir (réessayer plus tard, avertir l'utilisateur). `pause` convient quand une erreur coûte cher (argent, e-mail client) et qu'un humain est joignable.
- Une demande de vérification expire comme toute approbation (24 h par défaut), voir le chapitre 9.
- **Écart de version (important).** En **2.0.0**, un outil `approval: always` dont l'approbation a **déjà été accordée** avant le crash est relancé à la reprise, sans passer par `on_unknown`. Constaté ici : en ajoutant `approval="always"` à `envoyer_email_lent`, puis en refaisant le crash et `loom resume` avec l'approbateur en ligne, `emails.log` contient **deux** lignes. Même `@idempotent(reservation=3)` n'y change rien quand la réservation a expiré, parce que l'approbation déjà donnée « autorise à reprendre une réservation périmée ». Les sources de la 2.0.1 corrigent cela : un accord ne vaut que pour un seul lancement, et un second lancement après crash retombe sous la règle `on_unknown`. En attendant, deux protections : **faites passer à l'e-mail la clé d'idempotence de l'appel** (`ToolContext`) si votre prestataire la reconnaît, et gardez la **clé métier** d'idempotence, qui évite le doublon quand l'effet a été mémorisé.
- **Piège.** `side_effects` décide de la reprise. Un outil qui envoie un e-mail mais n'est pas déclaré `irreversible` serait relancé sans précaution. Déclarez-le honnêtement.

### Exemple 19.6 : borner le temps d'un run avec `timeout`

**Pourquoi.** Un run qui attend un service lent occupe un worker, et l'artisan qui attend sa relance ne sait pas si l'agent travaille ou s'il est coincé. On veut une borne : au-delà, le run s'arrête proprement et le dit, au lieu de pendre.

**Objectif.** Poser un délai maximal sur un agent, voir un run échouer sur ce délai, lire ce que le journal en garde, puis distinguer ce que le délai compte (le temps de pilotage) de ce qu'il ne compte pas (l'attente d'un humain).

**Mise en place.** Un nouvel agent dans `agents_service/`, copie de `relance` avec une clé de plus, `timeout`, en secondes. Il reprend l'outil `consulter_agenda` de l'exemple 19.1, qui dort huit secondes. `agents_service/relance_bornee.yaml` :

```yaml
name: relance-bornee
description: Comme relance, mais le pilotage du run est borné à 3 secondes.

main:
  model: PRINCIPAL
  system: |-
    Tu es l'assistant de la Plomberie Dupont. Pour relancer un client :
    1. retrouve le devis avec `chercher_devis` ;
    2. vérifie l'agenda de l'artisan avec `consulter_agenda` ;
    3. envoie l'e-mail avec `envoyer_email`.
    Ne calcule et n'invente jamais un montant : reprends celui du devis.

max_iterations: 8
timeout: 3

tools:
  - python: chercher_devis
  - python: consulter_agenda
  - python: envoyer_email
```

La configuration est `service.yaml`, celle des exemples 19.1 à 19.5 (le journal en SQLite). Videz d'abord `data/` pour partir d'un journal propre.

**Exécution.** On choisit l'identifiant du run et celui de la session, pour les retrouver :

```bash
rm -rf data
uv run loom --config service.yaml run relance-bornee "Relance Mme Martin pour le devis D-2026-042, sur un ton cordial." \
  --session martin-delai --run-id delai-1
echo "code de sortie : $?"
```

```text
délai maximal de 3.0 s dépassé : l'étape 4 a été interrompue en cours (0.0 s d'étapes déjà terminées)

Statut     : failed · itérations : 2 · tokens : 1315/147 · coût : 0.0061 $
Run        : delai-1
Erreur     : délai maximal de 3.0 s dépassé : l'étape 4 a été interrompue en cours (0.0 s d'étapes déjà terminées) (timeout)
code de sortie : 1
```

Le run a trouvé le devis, a commencé à consulter l'agenda, et a été coupé au bout de trois secondes. Le journal l'a écrit comme un échec (`run.failed`), de type `timeout`, et non comme une annulation :

```bash
uv run loom --config service.yaml sessions export martin-delai | jq -c 'select(.type=="run.failed") | .payload | {type, error_type, error}'
uv run loom --config service.yaml inspect delai-1 --session martin-delai
cat data/agenda.log
```

```text
{"type":"run.failed","error_type":"timeout","error":"délai maximal de 3.0 s dépassé : l'étape 4 a été interrompue en cours (0.0 s d'étapes déjà terminées)"}
Run        : delai-1 (agent relance-bornee, client default)
Session    : martin-delai
Statut     : failed (timeout), 2 itération(s)
Usage      : 1315 → 147 tokens, 0.006150 $, 19 ms de pilotage

run relance-bornee — failed, 3.0 s, 0.006150 $ [timeout]
  · échec : timeout
  étape 1
    modèle fake-principal (main) — 1 ms, 547 → 70 tokens, 0.002691 $
      · répond    : Je commence par retrouver le devis.
      · appelle   : chercher_devis({"numero": "D-2026-042"})
  étape 2
    outil chercher_devis — 4 ms
      · arguments : {"numero": "D-2026-042"}
      · résultat  : {"numero": "D-2026-042", "client": "Mme Martin", "email": "mme.martin@example.fr", "objet": "Remp…
  étape 3
    modèle fake-principal (main) — 1 ms, 768 → 77 tokens, 0.003459 $
      · répond    : Je consulte l'agenda de l'artisan pour choisir le jour d'envoi.
      · appelle   : consulter_agenda({"jour": "2026-10-12"})
  étape 4 (ouvert)
    outil consulter_agenda — 0 ms (ouvert)
      · arguments : {"jour": "2026-10-12"}

Réponse finale : aucune
Bilan      : 2 appel(s) de modèle, 2 appel(s) d'outil
consultation de l'agenda du 2026-10-12
```

L'agenda est resté « ouvert » (appel commencé, jamais terminé). Le code de sortie 1 est celui de n'importe quel run en échec. En Python, `result.status` vaut `failed` et `result.error_type` vaut `timeout`.

**Peut-on reprendre ce run avec un délai plus large ?** Le commentaire de la clé dans le code source dit que le run « reste reprenable avec un délai relevé ». On relève donc le délai à 30 secondes dans `relance_bornee.yaml` (`timeout: 30`), et on reprend :

```bash
sed -i 's/^timeout: 3$/timeout: 30/' agents_service/relance_bornee.yaml
uv run loom --config service.yaml resume delai-1 --session martin-delai
echo "code de sortie : $?"
```

```text
délai maximal de 3.0 s dépassé : l'étape 4 a été interrompue en cours (0.0 s d'étapes déjà terminées)

Statut     : failed · itérations : 2 · tokens : 1315/147 · coût : 0.0061 $
Run        : delai-1
Erreur     : délai maximal de 3.0 s dépassé : l'étape 4 a été interrompue en cours (0.0 s d'étapes déjà terminées) (timeout)
code de sortie : 1
```

Rien ne repart : `resume` rend le résultat déjà écrit, sans rien exécuter, et `agenda.log` ne bouge pas. Le run est clos, comme n'importe quel run en échec.

> **Écart.** La documentation et le commentaire du code décrivent un échec « reprenable ». En 2.0.0, `loom resume` sur un run échoué pour `timeout` ne le relance pas, même avec un délai relevé. Le même essai avec les sources de la 2.0.1 donne le même résultat. Pour passer outre, il faut **lancer un nouveau run** (avec un nouvel identifiant, dans la même session si l'on veut que le modèle retrouve l'historique), ce que fait la suite.

**Ce que le délai compte.** On porte le délai à 12 secondes (`timeout: 12`) et on lance un nouveau run. La consultation de l'agenda dure huit secondes, donc elle tient. Le run s'arrête ensuite sur l'approbation de l'e-mail. On laisse passer plus de temps que le délai avant d'approuver :

```bash
sed -i 's/^timeout: 30$/timeout: 12/' agents_service/relance_bornee.yaml
uv run loom --config service.yaml run relance-bornee "Relance Mme Martin pour le devis D-2026-042, sur un ton cordial." \
  --session martin-delai --run-id delai-2
sleep 15
uv run loom --config service.yaml approve delai-2 --session martin-delai --by denis
uv run loom --config service.yaml inspect delai-2 --session martin-delai | sed -n 1,6p
```

```text
—

Statut     : paused · itérations : 3 · tokens : 2242/269 · coût : 0.0108 $
Run        : delai-2
En attente : envoyer_email (fake_2_0) — loom approve delai-2 --call fake_2_0
Accordé : fake_2_0
La relance du devis D-2026-042 est partie chez Mme Martin.

Statut     : completed · itérations : 4 · tokens : 3372/308 · coût : 0.0147 $
Run        : delai-2
Run        : delai-2 (agent relance-bornee, client default)
Session    : martin-delai
Statut     : completed, 4 itération(s)
Usage      : 3372 → 308 tokens, 0.014736 $, 8.0 s de pilotage

run relance-bornee — completed, 23.8 s, 0.014736 $
```

Le run a duré 23,8 secondes d'horloge, de son ouverture à sa clôture, avec un délai de 12 secondes, et il a abouti : `8.0 s de pilotage` est ce que le délai a compté. L'attente de l'approbation (une quinzaine de secondes) n'en fait pas partie.

**À retenir.**

- `timeout` se met à la racine de l'agent, à côté de `max_iterations`, en secondes (strictement positives). Sans valeur, il n'y a pas de délai.
- Il borne le **temps de pilotage cumulé** du run : les appels de modèle et d'outils des étapes. Ne comptent pas : l'attente dans une file, une pause d'approbation, le temps entre un plantage et sa reprise. Le délai d'une attente humaine est un autre réglage (`approval.expires_in`, 24 h par défaut, chapitre 9).
- Le délai est contrôlé avant chaque étape et borne celle qui commence : une étape trop longue est coupée en plein vol (ici, la consultation d'agenda), et ce qu'elle avait déjà écrit reste au journal.
- Le dépassement s'écrit `run.failed` avec `error_type: timeout`. Ce n'est pas une annulation (`run.cancelled`, exemple 19.3), qui est une décision de l'appelant.
- Il ne remplace pas les `timeouts` d'un modèle (chapitre 15), qui bornent **un** appel au fournisseur : le `timeout` d'agent borne **le run entier**, tous appels cumulés. Les deux se combinent.
- **Piège.** Le délai se règle sur le pire cas utile, pas sur le cas moyen : il doit couvrir les appels de modèle et les outils les plus lents. Trop court, il tue des runs sains (ici, 3 secondes pour un agenda de 8).
- **Piège.** Un run échoué pour délai ne repart pas tout seul : si votre service doit réessayer, c'est à lui de soumettre un nouveau run.

---
## 20. L'API REST : servir les agents à une application

### Ce qu'il faut comprendre avant de commencer

Jusqu'ici, l'agent se pilotait depuis un terminal ou un script Python. Une application mobile (Flutter, par exemple), un site ou un autre service ne savent pas faire cela : ils parlent HTTP. `loom serve` publie les agents sous forme d'une API REST, avec un document OpenAPI généré qui permet de produire un client pour n'importe quel langage.

| Route | Rôle | Portée de clé (chapitre 22) |
|---|---|---|
| `GET /v1/agents` | Lister les agents publiés | `read` |
| `POST /v1/agents/{nom}/runs` | Lancer un run (JSON ou multipart avec pièces jointes) | `run` |
| `GET /v1/runs`, `GET /v1/runs/{id}` | Lister les runs, lire le statut et le résultat | `read` |
| `GET /v1/runs/{id}/events` | Déroulé en direct (SSE), reprise par `Last-Event-ID` | `read` |
| `POST /v1/runs/{id}/approve`, `/reject` | Validation humaine | `approve` |
| `POST /v1/runs/{id}/cancel` | Arrêt d'un run | `run` |
| `GET /v1/sessions`, `GET /v1/sessions/{id}` | Sessions, fiche d'une session | `read` |
| `GET /v1/sessions/{id}/events`, `/report` | Journal (JSONL) et consommation d'une session | `read` |
| `DELETE /v1/sessions/{id}` | Effacement RGPD | `admin` |
| `GET /v1/events`, `GET /v1/traces/{run_id}` | Recherche dans le journal, trace d'un run | `read` |
| `POST /v1/hooks/{nom}` | Webhook entrant (chapitre 23) | `run` |

Trois réglages de la section `server` de `loom.yaml` : `http.host` (`127.0.0.1` par défaut), `http.port` (8000), `http.base_path` (préfixe commun, par exemple `/loom`). Les options `--host` et `--port` de la commande les surchargent.

Quelques règles à connaître :

- **Synchrone par défaut.** `POST …/runs` attend la fin du run et rend le résultat complet. Avec `"background": true`, il répond **tout de suite** `202 Accepted` avec l'identifiant du run, qui tourne en arrière-plan (c'est `Loom.submit()` du chapitre 19).
- **Session nommée.** Quand on donne un `session_id`, les routes qui lisent un run (`GET /v1/runs/{id}`, `…/events`, `…/approve`) doivent le recevoir **aussi**, en paramètre de requête : `?session_id=…`. Sans lui, le run est introuvable.
- **Publication.** Un agent déclare dans son YAML `expose: {rest: true, mcp: true}` (les deux par défaut). Mettre `rest: false` le retire de l'API.
- **L'API est ouverte tant qu'aucune clé n'est déclarée.** `loom validate` le rappelle (« Clés d'API : aucune (API REST ouverte) »), et le profil `prod` en fait une erreur. Le chapitre 22 ajoute les clés. Dans ce chapitre, le serveur n'écoute que sur `127.0.0.1`.

### Exemple 20.1 : lancer le serveur et interroger un agent par `curl`

**Pourquoi.** L'application de l'artisan doit pouvoir poser une question à l'agent : « Que sait-on du devis D-2026-042 ? », et obtenir la réponse en JSON.

**Objectif.** Démarrer `loom serve`, voir la documentation interactive, lancer un run synchrone, et voir une erreur propre pour un agent inconnu.

**Mise en place.** Une configuration `api.yaml` et ses trois agents dans `agents_api/` : `devis` (lecture seule), `relance` (envoi à approuver) et `photo` (rôle vision, repris du chapitre 18).

```bash
mkdir agents_api
```

`agents_api/devis.yaml` :

```yaml
name: devis
description: Répond à une question sur un devis (lecture seule).

main:
  model: DEVIS
  system: Tu réponds aux questions sur les devis de la Plomberie Dupont. Consulte-les avec `chercher_devis`.

max_iterations: 4

tools:
  - python: chercher_devis
```

`agents_api/relance.yaml` :

```yaml
name: relance
description: Relance un client pour un devis resté sans réponse (l'envoi demande une approbation).

main:
  model: PRINCIPAL
  system: |-
    Tu es l'assistant de la Plomberie Dupont. Pour relancer un client :
    retrouve le devis avec `chercher_devis`, puis envoie l'e-mail avec `envoyer_email`.
    Ne calcule et n'invente jamais un montant : reprends celui du devis.

max_iterations: 6

tools:
  - python: chercher_devis
  - python: envoyer_email
```

`agents_api/photo.yaml` (identique à celui du chapitre 18) :

```yaml
name: photo
description: Décrit une photo de chantier jointe à la demande.

main:
  model: PHOTO_MAIN
  system: "Tu aides un plombier à lire les photos de ses chantiers. Si une photo est jointe, fais-la décrire par `decrire_photo` et résume en une phrase."

max_iterations: 4

roles:
  - name: decrire_photo
    description: Décrit la photo jointe à la demande.
    model: VISION
    system: Tu décris précisément une photo de chantier, en français.
    input_schema:
      type: object
      properties:
        consigne: {type: string, description: "Ce qu'il faut regarder"}
      required: [consigne]
    context: [attachments]
```

`api.yaml` :

```yaml
version: 1

imports: [outils]
agents_dir: agents_api/

models:
  - id: DEVIS
    sdk: fake
    model: fake-devis
    pricing: {input: 3.0, output: 15.0}
    params:
      script:
        - text: Je consulte le devis.
          tool_calls:
            - {name: chercher_devis, arguments: {numero: D-2026-042}}
        - text: Le devis D-2026-042 de Mme Martin (remplacement du chauffe-eau) s'élève à 1 840 € TTC, envoyé le 3 septembre.
  - id: PRINCIPAL
    sdk: fake
    model: fake-principal
    pricing: {input: 3.0, output: 15.0}
    params:
      script:
        - text: Je commence par retrouver le devis.
          tool_calls:
            - {name: chercher_devis, arguments: {numero: D-2026-042}}
        - text: J'envoie la relance.
          tool_calls:
            - name: envoyer_email
              arguments:
                destinataire: mme.martin@example.fr
                objet: Votre devis D-2026-042
                corps: "Bonjour Madame Martin, je me permets de revenir vers vous au sujet du devis D-2026-042 (1 840 €) envoyé le 3 septembre. Restant à votre disposition, cordialement."
        - text: La relance du devis D-2026-042 est partie chez Mme Martin.
  - id: PHOTO_MAIN
    sdk: fake
    model: fake-photo-main
    pricing: {input: 0.8, output: 4.0}
    params:
      script:
        - text: Je fais décrire la photo.
          with_tool: decrire_photo
          tool_calls:
            - {name: decrire_photo, arguments: {consigne: "Décris ce que montre la photo de chantier."}}
        - text: "La photo montre l'arrivée d'eau d'une chaudière, avec un raccord rouge."
          with_tool: decrire_photo
        - text: Aucune photo n'est jointe à la demande.
          without_tool: decrire_photo
  - id: VISION
    sdk: fake
    model: fake-vision
    pricing: {input: 0.8, output: 4.0}
    capabilities: {vision: true}
    params:
      script:
        - text: Une surface rouge unie, sans détail exploitable.

telemetry:
  logging: {level: WARNING}

storage:
  events: {backend: sqlite, path: data-api/journal.db}

server:
  http: {host: 127.0.0.1, port: 18301}
```

(Le port 18301 évite de heurter un autre service sur votre machine ; omis, le port est 8000.) Le journal est en SQLite : sur un service, c'est lui qui permet de relire les runs après un redémarrage.

**Exécution.** Dans un premier terminal :

```bash
uv run loom --config api.yaml serve
```

```text
Profil     : aucun (ni --profile, ni LOOM_PROFILE, ni 'profile:') ; surcharges : aucune
API REST   : http://127.0.0.1:18301/v1
Agents     : devis, photo, relance
```

Dans un second terminal. D'abord les agents publiés, puis un run synchrone :

```bash
curl -s http://127.0.0.1:18301/v1/agents | jq -c '.[]'
curl -s -X POST http://127.0.0.1:18301/v1/agents/devis/runs \
  -H 'Content-Type: application/json' \
  -d '{"message":"Que sait-on du devis D-2026-042 ?","session_id":"question-martin"}' \
  | jq '{run_id, session_id, status, text, iterations, cost_usd}'
```

```text
{"name":"devis","description":"Répond à une question sur un devis (lecture seule)."}
{"name":"photo","description":"Décrit une photo de chantier jointe à la demande."}
{"name":"relance","description":"Relance un client pour un devis resté sans réponse (l'envoi demande une approbation)."}
{
  "run_id": "01a1268a-9143-7488-b816-363a3c53117f",
  "session_id": "question-martin",
  "status": "completed",
  "text": "Le devis D-2026-042 de Mme Martin (remplacement du chauffe-eau) s'élève à 1 840 € TTC, envoyé le 3 septembre.",
  "iterations": 2,
  "cost_usd": 0.003741
}
```

Le JSON complet contient aussi `usage`, `artifacts`, `data`, `verdicts`, `pending_approvals` et un `report` ventilé par run, rôle et modèle (le même que `loom report`). La documentation interactive est à `http://127.0.0.1:18301/docs` (Swagger UI) : chaque route, chaque schéma, avec un bouton « Try it out ». Le document OpenAPI brut est à `/openapi.json`.

Un agent inconnu donne une erreur claire :

```bash
curl -s -i -X POST http://127.0.0.1:18301/v1/agents/nope/runs -H 'Content-Type: application/json' -d '{"message":"x"}' | sed -n '1p;/^{/p'
```

```text
HTTP/1.1 404 Not Found
{"detail":"Agent 'nope' inconnu (agents : devis, photo, relance)"}
```

**À retenir.**

- Un client HTTP n'a pas besoin de connaître Loom : un `POST` JSON et un `jq`. Les erreurs sont des codes HTTP ordinaires avec un champ `detail` lisible.
- Le même moteur sert la CLI et l'API : un run lancé par REST apparaît dans `loom inspect` (avec `--session` si besoin) et dans `loom sessions list`.
- **Piège.** Ne confondez pas les modèles de ce chapitre avec de vrais fournisseurs : ici tout est simulé, les coûts sont ceux du tarif déclaré.

### Exemple 20.2 : un run en arrière-plan, suivi en direct, puis validé

**Pourquoi.** Une relance demande l'accord de l'artisan : le run s'arrête sur l'approbation et peut attendre des heures. Une requête HTTP ne peut pas rester ouverte aussi longtemps. L'application lance le run en arrière-plan, suit son déroulé, et affiche un bouton « Approuver ».

**Objectif.** Lancer en arrière-plan (202), suivre en SSE, reprendre le flux après une coupure, approuver, refuser, annuler.

**Mise en place.** Rien à ajouter : le serveur de l'exemple précédent tourne toujours.

**Exécution.** Un run en arrière-plan, rattaché à une session nommée :

```bash
curl -s -i -X POST http://127.0.0.1:18301/v1/agents/relance/runs \
  -H 'Content-Type: application/json' \
  -d '{"message":"Relance Mme Martin pour le devis D-2026-042, sur un ton cordial.","session_id":"martin-rest","background":true}' \
  | sed -n '1p;/^{/p'
```

```text
HTTP/1.1 202 Accepted
{"run_id":"01a1268a-9177-706d-bc25-6ff8518a181b","session_id":"martin-rest","status":"ready_for_model"}
```

Une seconde plus tard, le statut montre la pause et ce qui est demandé (notez le `?session_id=…`) :

```bash
R=01a1268a-9177-706d-bc25-6ff8518a181b      # votre run_id
S="session_id=martin-rest"
curl -s "http://127.0.0.1:18301/v1/runs/$R?$S" | jq -c '{status, text, pending: [.pending_approvals[] | {call_id, tool_name, reason}]}'
```

```text
{"status":"paused","text":"","pending":[{"call_id":"fake_1_0","tool_name":"envoyer_email","reason":"outil déclaré à approbation obligatoire (effets : irreversible)"}]}
```

Le flux d'événements est du **SSE** (*Server-Sent Events*) : une connexion HTTP qui reste ouverte et pousse des événements. Chaque événement porte un `id` (son numéro de séquence dans le journal) et un `event` (son type). Sans les événements de plomberie :

```bash
curl -s -N --max-time 3 "http://127.0.0.1:18301/v1/runs/$R/events?$S" \
  | grep -E "^(id|event):" | paste - - | grep -vE "step\.|transitioned|claimed"
```

```text
id: 1	event: run.started
id: 2	event: message.user
id: 5	event: model.responded
id: 9	event: tool.called
id: 10	event: tool.completed
id: 14	event: model.responded
id: 18	event: approval.requested
```

(`--max-time 3` coupe la connexion : le flux reste ouvert tant que le run n'est pas clos, et un run en pause n'est pas clos.) Après une coupure réseau, le client rouvre le flux avec l'en-tête `Last-Event-ID` : il ne reçoit que ce qui suit.

```bash
curl -s -N --max-time 2 -H "Last-Event-ID: 14" "http://127.0.0.1:18301/v1/runs/$R/events?$S" | grep -E "^(id|event):" | paste - -
```

```text
id: 15	event: step.completed
id: 16	event: run.transitioned
id: 17	event: step.started
id: 18	event: approval.requested
id: 19	event: step.completed
id: 20	event: run.transitioned
id: 21	event: run.claimed
```

(L'équivalent en paramètre est `?after_seq=14`, utile pour un client qui ne sait pas poser d'en-tête, comme `EventSource` dans un navigateur.) On approuve :

```bash
curl -s -X POST "http://127.0.0.1:18301/v1/runs/$R/approve?$S" -H 'Content-Type: application/json' -d '{"by":"denis"}' | jq -c .
sleep 1
curl -s "http://127.0.0.1:18301/v1/runs/$R?$S" | jq -r '.status + " : " + .text'
cat data/emails.log
```

```text
{"run_id":"01a1268a-9177-706d-bc25-6ff8518a181b","calls":["fake_1_0"]}
completed : La relance du devis D-2026-042 est partie chez Mme Martin.
mme.martin@example.fr | Votre devis D-2026-042
```

Le corps de `approve` accepte `by` (qui décide), `call_id` (un appel précis ; sans lui, tout ce qui attend), `reason` et `arguments` (pour **corriger** les arguments de l'appel avant de l'accorder, par exemple un montant). Un second run, refusé cette fois :

```bash
R=$(curl -s -X POST http://127.0.0.1:18301/v1/agents/relance/runs -H 'Content-Type: application/json' \
  -d '{"message":"Relance Mme Martin pour le devis D-2026-042.","session_id":"martin-refus","background":true}' | jq -r .run_id)
sleep 1
curl -s -X POST "http://127.0.0.1:18301/v1/runs/$R/reject?session_id=martin-refus" -H 'Content-Type: application/json' \
  -d '{"by":"denis","reason":"Mme Martin a déjà répondu par téléphone"}' | jq -c .
sleep 1
wc -l < data/emails.log
curl -s http://127.0.0.1:18301/v1/sessions/martin-refus/events | jq -c 'select(.type=="approval.rejected") | .payload | del(.type)'
```

```text
{"run_id":"01a1268a-d09c-7576-987e-bd597c8662db","calls":["fake_1_0"]}
1
{"call_id":"fake_1_0","tool_name":"envoyer_email","by":"denis","reason":"Mme Martin a déjà répondu par téléphone"}
```

`emails.log` n'a toujours qu'**une** ligne : l'e-mail refusé n'est pas parti, et le refus, avec son motif, est au journal. (Le modèle simulé rend sa phrase scriptée quelle que soit la décision ; un vrai modèle lit le refus et répond en conséquence.) Enfin, un troisième run annulé pendant sa pause. Le corps de `cancel` est obligatoire, même vide d'information (`{}` est accepté, `by` est facultatif) :

```bash
R=$(curl -s -X POST http://127.0.0.1:18301/v1/agents/relance/runs -H 'Content-Type: application/json' \
  -d '{"message":"Relance Mme Martin pour le devis D-2026-042.","session_id":"martin-annule","background":true}' | jq -r .run_id)
sleep 1
curl -s -X POST "http://127.0.0.1:18301/v1/runs/$R/cancel?session_id=martin-annule" -H 'Content-Type: application/json' -d '{"by":"denis"}' | jq -c .
curl -s "http://127.0.0.1:18301/v1/runs/$R?session_id=martin-annule" | jq -c '{status}'
curl -s -X POST "http://127.0.0.1:18301/v1/runs/$R/cancel?session_id=martin-annule" -H 'Content-Type: application/json' -d '{"by":"denis"}' | jq -c .
```

```text
{"run_id":"01a1268a-d8ac-711e-ae55-9f2bcf0f8481","cancelled":true}
{"status":"cancelled"}
{"run_id":"01a1268a-d8ac-711e-ae55-9f2bcf0f8481","cancelled":false}
```

**À retenir.**

- Le schéma d'une application : `POST … "background": true` → 202 et un identifiant ; ouvrir le SSE ; à l'événement `approval.requested`, afficher le bouton ; `POST …/approve` ou `…/reject` ; le flux se termine à la clôture du run.
- `Last-Event-ID` et `after_seq` rendent le flux **reprenable** : un mobile qui perd le réseau ne rate rien et ne rejoue rien.
- `approve` et `reject` répondent `200` avec les appels tranchés ; un run qui n'attendait rien rend une liste vide.
- Annuler un run déjà fini ne fait rien : `"cancelled": false`.
- **Piège.** Oublier `?session_id=…` pour un run de session nommée donne un 404 « Run … inconnu », et l'on croit à tort que le run a disparu. Gardez toujours l'identifiant de session à côté de celui du run.
- **Vérifié.** Approuver dès l'arrivée de l'événement `approval.requested` dans le flux fonctionne par le réseau (six essais sur six). La course dont parle le chapitre 19 ne concerne que du code Python qui approuve dans la même microseconde.

### Exemple 20.3 : sessions, journal, recherche et trace

**Pourquoi.** L'application doit afficher l'historique d'une conversation, la dépense d'une session, retrouver tous les envois d'e-mail du mois, et le support doit pouvoir analyser un run précis.

**Objectif.** Parcourir les routes de lecture.

**Mise en place.** Rien : on lit ce que les exemples précédents ont écrit.

**Exécution.**

```bash
B=http://127.0.0.1:18301
curl -s $B/v1/sessions | jq -r '.[].session_id'
curl -s $B/v1/sessions/martin-rest/events | wc -l
curl -s $B/v1/sessions/martin-rest/events | head -1 | cut -c1-160
curl -s $B/v1/sessions/martin-rest/report | jq -c '{cout: .total.cost, appels: .total.calls}'
curl -s "$B/v1/events?type=approval.granted&limit=5" | jq -c '.[0] | {type, session_id}'
R=$(curl -s $B/v1/sessions/martin-rest | jq -r '.runs[0].run_id')
curl -s "$B/v1/traces/$R?session_id=martin-rest" | jq -c 'keys'
```

```text
martin-annule
martin-refus
martin-rest
question-martin
34
{"event_id":"01a1268a-9177-706d-bc25-6ffa04bf23f5","ts":"2026-10-10T15:59:37.847677Z","schema_version":1,"tenant_id":"default","session_id":"martin-rest","run_i
{"cout":0.009222,"appels":3}
{"type":"approval.granted","session_id":"martin-rest"}
["active_ms","agent","content","cost_usd","error_type","finished","iterations","output","run_id","session_id","spans","status","tenant_id","trace_id","usage"]
```

- `/v1/sessions/{id}/events` rend le journal en **JSONL** : un événement JSON par ligne. C'est le format de l'export RGPD (chapitre 24).
- `/v1/events` cherche dans tous les journaux du client : `type`, `tool_name`, `agent`, `model_id`, `status`, `since`/`until`, `after` (curseur) et `limit`. Par exemple `?tool_name=envoyer_email&type=tool.completed` retrouve tous les envois.
- `/v1/traces/{run_id}` rend la trace d'un run (durées, spans, coût, sortie) : c'est ce que consomme `loom inspect`.

L'effacement d'une session (RGPD) emporte son journal, ses fichiers et ses clés d'idempotence :

```bash
curl -s -w " [HTTP %{http_code}]\n" -X DELETE $B/v1/sessions/martin-annule
```

```text
{"session_id":"martin-annule","events":23,"artifacts":0,"keys":0} [HTTP 200]
```

**À retenir.**

- Les routes de lecture ne lancent rien : elles relisent le journal. Elles marchent après un redémarrage tant que le journal est durable.
- **Piège.** `DELETE` a réussi ici **sans aucune clé**, parce que l'API est ouverte. Sur un service réel, cette route doit demander la portée `admin` : voir le chapitre 22.

### Exemple 20.4 : envoyer une photo en multipart

**Pourquoi.** L'application de l'artisan prend une photo de chantier et veut la faire lire à l'agent. Une image ne passe pas bien dans du JSON : on utilise un formulaire `multipart/form-data`.

**Objectif.** Envoyer un message et une pièce jointe, voir l'artefact dans la réponse et les refus.

**Mise en place.** Le fichier `chaudiere.png` et la fabrique de `note.png` (un faux PNG) du chapitre 18.

**Exécution.**

```bash
B=http://127.0.0.1:18301
curl -s -X POST $B/v1/agents/photo/runs -F 'message=Que montre la photo ?' -F 'session_id=photo-rest' \
  -F 'attachments=@chaudiere.png;type=image/png' \
  | jq '{status, text, artifacts: [.artifacts[] | {origin, media_type, size, name}]}'
curl -s -X POST $B/v1/agents/photo/runs -F 'message=Que montre la photo ?' | jq -r .text
curl -s -w " [HTTP %{http_code}]\n" -X POST $B/v1/agents/photo/runs -F 'message=x' -F 'attachments=@note.png;type=image/png'
curl -s -w " [HTTP %{http_code}]\n" -X POST $B/v1/agents/photo/runs -H 'Content-Type: text/plain' -d 'x'
```

```text
{
  "status": "completed",
  "text": "La photo montre l'arrivée d'eau d'une chaudière, avec un raccord rouge.",
  "artifacts": [
    {
      "origin": "attachment",
      "media_type": "image/png",
      "size": 178,
      "name": "chaudiere.png"
    }
  ]
}
Aucune photo n'est jointe à la demande.
{"detail":"note.png : format non reconnu (acceptés : image/jpeg, image/png, image/gif, image/webp)"} [HTTP 422]
{"detail":"Type de corps non pris en charge : text/plain (acceptés : application/json, multipart/form-data)"} [HTTP 415]
```

Les champs du formulaire sont `message`, `session_id`, `run_id`, `user_id` et `metadata` (JSON), et les fichiers vont sous `attachments`. Les règles du chapitre 18 s'appliquent (signature binaire, taille, nombre) et un refus donne **422**. Une charge trop grosse est refusée en **413** dès l'en-tête, avant d'être lue en entier (`server.http.max_body_bytes`, 128 Mio par défaut). Un corps d'un autre type donne **415**, un formulaire illisible **400**.

**À retenir.**

- Les pièces jointes passent par `multipart/form-data`, pas par du JSON en base64.
- L'artefact revient dans la réponse (`artifacts`) avec son `origin`. L'URI complète (`artifact://…`) permet de le redonner à un autre run par le serveur MCP (chapitre 21).

### Exemple 20.5 : intégrer l'API dans une application FastAPI existante

**Pourquoi.** Vous avez déjà un portail web (pour les clients de la Plomberie Dupont, par exemple) et vous ne voulez pas deux serveurs à déployer.

**Objectif.** Monter l'API Loom dans votre propre application avec `create_app(loom)`.

**Mise en place.** `mon_app.py` :

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI

from loom_ia.access import Loom
from loom_ia.access.http import create_app

loom = Loom.from_config("api.yaml")


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await loom.aclose()          # l'instance est à nous : c'est à nous de la fermer


app = FastAPI(title="Portail Plomberie Dupont", lifespan=lifespan)


@app.get("/sante")
def sante() -> dict[str, str]:
    return {"statut": "ok"}


# L'API de Loom est montée sous /loom, à côté de nos propres routes.
app.mount("/loom", create_app(loom))
```

**Exécution.** (`uvicorn` est installé avec l'extra `http`.)

```bash
uv run uvicorn mon_app:app --port 18302 &
curl -s localhost:18302/sante
curl -s -X POST localhost:18302/loom/v1/agents/devis/runs -H 'Content-Type: application/json' \
  -d '{"message":"Que sait-on du devis D-2026-042 ?"}' | jq -r '.status + " : " + .text'
```

```text
{"statut":"ok"}
completed : Le devis D-2026-042 de Mme Martin (remplacement du chauffe-eau) s'élève à 1 840 € TTC, envoyé le 3 septembre.
```

À l'arrêt du serveur (Ctrl-C), `uvicorn` ferme proprement l'application : `Application shutdown complete`.

**À retenir.**

- `create_app(loom)` rend une application ASGI ordinaire. On la monte, on la protège avec ses propres middlewares, on la déploie avec ce qu'on veut (uvicorn, gunicorn, un conteneur).
- L'instance `Loom` reste à l'appelant par défaut. `create_app(loom, own=True)` confie sa fermeture à l'application, mais seulement quand c'est elle qui est servie directement ; une application montée dans une autre n'exécute pas son propre cycle de vie, d'où la fermeture explicite ci-dessus.
- Le chemin de documentation devient `/loom/docs`. Si vous préférez un préfixe défini par la configuration, utilisez `server.http.base_path`.

### Exemple 20.6 : recharger automatiquement en développement et masquer un agent

**Pourquoi.** Pendant que vous écrivez vos prompts, relancer le serveur à chaque modification est fastidieux. À l'inverse, certains agents (un contrôleur interne, un sous-agent) ne doivent pas être publiés.

**Objectif.** Utiliser `--reload`, voir qu'une configuration cassée est refusée sans arrêter le serveur, et masquer un agent avec `expose`.

**Mise en place.** Pour masquer l'agent `devis` de l'API, on ajoute à `agents_api/devis.yaml` :

```yaml
expose: {rest: false, mcp: true}
```

**Exécution.** Le rechargement :

```bash
uv run loom --config api.yaml serve --reload
```

```text
Profil     : aucun (ni --profile, ni LOOM_PROFILE, ni 'profile:') ; surcharges : aucune
API REST   : http://127.0.0.1:18301/v1
Agents     : devis, photo, relance
Rechargement : surveille /chemin/vers/relance (sauf data-api/.artifacts/, data-api/journal.db)
```

On casse volontairement `api.yaml` (une ligne invalide à la fin) :

```text
expected the node content, but found '<stream end>'
  in "<unicode string>", line 66, column 1:
    
    ^
Rechargement refusé : l'ancien process continue de servir.
```

Le serveur répond toujours (`/v1/agents` rend la même liste). On répare le fichier :

```text
Profil     : aucun (ni --profile, ni LOOM_PROFILE, ni 'profile:') ; surcharges : aucune
API REST   : http://127.0.0.1:18301/v1
Agents     : devis, photo, relance
Rechargé : le nouveau process sert ; l'ancien finit ce qu'il a en cours.
```

Avec `expose: {rest: false}` sur `devis`, l'API ne liste plus que `photo` et `relance`, et un `POST /v1/agents/devis/runs` renvoie `404 Agent 'devis' inconnu (agents : photo, relance)`. L'agent reste disponible en MCP (chapitre 21).

**À retenir.**

- `--reload` surveille le dossier de la configuration (hors dossiers de données). L'ancien process continue de servir pendant que le nouveau charge, et **seule une configuration valide le remplace**. Il est refusé en profil `prod`.
- `expose` se règle agent par agent et accès par accès (`rest`, `mcp`). Un sous-agent appelé par d'autres agents s'écrit généralement `expose: {rest: false, mcp: false}`, comme au chapitre 17.
- **Piège.** `expose: {rest: false}` cache la **route** mais ne protège rien d'autre : un autre agent peut toujours l'appeler comme sous-agent. La protection d'un agent passe par les clés (chapitre 22).

---
## 21. Le serveur MCP : brancher les agents sur Claude et sur d'autres clients

### Ce qu'il faut comprendre avant de commencer

**MCP** (*Model Context Protocol*) est le protocole par lequel une application de LLM (Claude Desktop, Claude Code, un éditeur) découvre et appelle des outils. Jusqu'ici, vos agents appelaient des outils. Avec `loom mcp`, c'est l'inverse : **vos agents deviennent des outils** que d'autres LLM peuvent appeler. Denis, depuis Claude Code, demande « relance Mme Martin », et c'est l'agent `relance` de la Plomberie Dupont qui travaille, avec son journal, son budget et ses approbations.

| Élément | Ce que Loom expose |
|---|---|
| Un agent publié | Un **outil** du même nom, avec trois arguments : `message`, `session_id`, `attachments`. |
| Outils de contrôle | `run_status` (relire un run), `run_report` (sa consommation), `cancel` (l'arrêter). |
| Ressources `loom://` | `loom://runs`, `loom://sessions`, et des gabarits : `loom://runs/{run_id}`, `…/events`, `loom://traces/{run_id}`, `loom://sessions/{session_id}`, `…/events`, `loom://artifacts/{client}/{session}/{fichier}`. |
| Progression | Si le client fournit un jeton de progression, chaque ligne du déroulé du run lui arrive en notification. |

Deux transports :

| Transport | Lancement | Clients servis | Authentification |
|---|---|---|---|
| **stdio** | `loom mcp` (le client lance la commande) | Un seul, choisi avec `--tenant` | Aucune : le process appartient à celui qui le lance |
| **HTTP** | `server: {mcp: {http: true}}`, servi avec `loom serve` sous `/mcp` | Tous, par clé d'API | Clé à chaque requête, mêmes portées qu'en REST |

Une règle de sécurité structure tout le reste : **`approve` n'est jamais un outil MCP.** Si l'était, le LLM client pourrait valider lui-même l'action sensible qu'il vient de demander. La validation passe soit par l'*elicitation* MCP (un formulaire montré à un humain), soit par l'API REST ou la CLI.

### Exemple 21.1 : écrire un petit client MCP et parler à `loom mcp` en stdio

**Pourquoi.** Avant de brancher Claude Desktop, vous voulez voir exactement ce que le serveur expose, sans interface qui masque les détails. Un client de quinze lignes en Python suffit, avec le SDK officiel `mcp` (installé avec l'extra `mcp`).

**Objectif.** Lister les outils et les ressources, appeler un agent, lire le résultat structuré.

**Mise en place.** On réutilise `api.yaml` du chapitre 20. Le journal de la démonstration repart de zéro :

```bash
rm -rf data-api
```

`client_mcp.py` :

```python
import asyncio

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVEUR = StdioServerParameters(
    command="uv", args=["run", "loom", "--config", "api.yaml", "mcp"]
)


async def main() -> None:
    async with stdio_client(SERVEUR) as (lecture, ecriture):
        async with ClientSession(lecture, ecriture) as session:
            await session.initialize()

            outils = await session.list_tools()
            print("outils :", [t.name for t in outils.tools])

            reponse = await session.call_tool(
                "devis", {"message": "Que sait-on du devis D-2026-042 ?", "session_id": "mcp-martin"}
            )
            print("devis  :", reponse.content[0].text)
            print("champs :", sorted(reponse.structuredContent))

            ressources = await session.list_resources()
            print("ressources :", [str(r.uri) for r in ressources.resources])
            modeles = await session.list_resource_templates()
            print("modèles :", [m.uriTemplate for m in modeles.resourceTemplates])


asyncio.run(main())
```

Le client **lance lui-même** `loom mcp` comme sous-process et lui parle sur son entrée et sa sortie standard (d'où « stdio »). C'est exactement ce que fera Claude Desktop.

**Exécution.**

```bash
uv run python client_mcp.py
```

```text
outils : ['devis', 'photo', 'relance', 'run_status', 'run_report', 'cancel']
devis  : Le devis D-2026-042 de Mme Martin (remplacement du chauffe-eau) s'élève à 1 840 € TTC, envoyé le 3 septembre.
champs : ['agent', 'artifacts', 'cost_usd', 'data', 'error', 'error_type', 'iterations', 'pending_approvals', 'report', 'run_id', 'session_id', 'status', 'text', 'unverified', 'usage', 'verdicts']
ressources : ['loom://runs', 'loom://sessions']
modèles : ['loom://runs/{run_id}{?session_id}', 'loom://runs/{run_id}/events{?session_id}', 'loom://traces/{run_id}{?session_id}', 'loom://sessions/{session_id}', 'loom://sessions/{session_id}/events', 'loom://artifacts/{client}/{session}/{fichier}']
```

Trois agents, donc trois outils, plus les trois outils de contrôle. Le texte de la réponse est celui de l'agent ; le **résultat structuré** (`structuredContent`) reprend les champs de la réponse REST du chapitre 20 : `status`, `run_id`, `cost_usd`, `pending_approvals`, etc. Un client programmé s'appuie sur celui-là, un LLM lit surtout le texte.

**À retenir.**

- Un agent est un outil MCP dont la **description** est celle de l'agent (`description:` dans son YAML). C'est ce texte qui permet au LLM client de choisir quand l'appeler : soignez-le.
- `expose: {mcp: false}` retire l'agent du serveur MCP (chapitre 20, comme pour REST).
- En stdio, un seul client est servi, choisi par `loom mcp --tenant <client>` (chapitre 22). Sans cela, c'est le client de la configuration, sinon `default`.
- **Piège.** `uv run` imprime parfois des avertissements sur la sortie d'erreur. Ce n'est pas un problème pour MCP, qui n'utilise que la sortie standard pour le protocole : mais n'ajoutez jamais de `print` dans un outil Python servi en stdio, il corromprait le flux.

### Exemple 21.2 : un agent qui a besoin d'une approbation, vu depuis MCP

**Pourquoi.** L'agent `relance` envoie un e-mail à une cliente : action irréversible, donc approbation obligatoire. Que voit le LLM client quand l'agent s'arrête dessus ? Il ne doit ni croire que l'e-mail est parti, ni pouvoir l'approuver lui-même.

**Objectif.** Voir le run rendu **en pause** avec ce qu'il attend, relire son état, sa consommation, sa ressource, puis l'annuler.

**Mise en place.** `client_mcp_pause.py` :

```python
import asyncio
import json

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVEUR = StdioServerParameters(
    command="uv", args=["run", "loom", "--config", "api.yaml", "mcp"]
)


async def main() -> None:
    async with stdio_client(SERVEUR) as (lecture, ecriture):
        async with ClientSession(lecture, ecriture) as session:
            await session.initialize()

            schema = (await session.list_tools()).tools[0].inputSchema
            print("arguments de l'outil devis :", sorted(schema["properties"]))

            reponse = await session.call_tool(
                "relance",
                {"message": "Relance Mme Martin pour le devis D-2026-042.", "session_id": "mcp-relance"},
            )
            print("erreur ?  :", reponse.isError)
            print("texte     :", reponse.content[0].text)
            s = reponse.structuredContent
            run_id = s["run_id"]
            print("statut    :", s["status"], "| en attente :", [a["tool_name"] for a in s["pending_approvals"]])

            etat = await session.call_tool("run_status", {"run_id": run_id, "session_id": "mcp-relance"})
            print("run_status:", etat.structuredContent["status"])

            rapport = await session.call_tool("run_report", {"run_id": run_id, "session_id": "mcp-relance"})
            print("run_report:", rapport.structuredContent["total"]["calls"], "appels")

            ressource = await session.read_resource(f"loom://runs/{run_id}?session_id=mcp-relance")
            print("ressource :", json.loads(ressource.contents[0].text)["status"])

            arret = await session.call_tool("cancel", {"run_id": run_id, "session_id": "mcp-relance"})
            print("cancel    :", arret.structuredContent)


asyncio.run(main())
```

**Exécution.**

```bash
uv run python client_mcp_pause.py
```

```text
arguments de l'outil devis : ['attachments', 'message', 'session_id']
erreur ?  : False
texte     : Run 01a1268d-4c87-75c9-bc58-c9fdeee4d1c3 en attente d'approbation : envoyer_email (fake_1_0). Un humain doit trancher (API REST ou « loom approve »), puis run_status relit le run (run_id=01a1268d-4c87-75c9-bc58-c9fdeee4d1c3, session_id=mcp-relance).
statut    : paused | en attente : ['envoyer_email']
run_status: paused
run_report: 2 appels
ressource : paused
cancel    : {'run_id': '01a1268d-4c87-75c9-bc58-c9fdeee4d1c3', 'cancelled': True}
```

L'outil rend la main **dès que le run s'arrête**, avec `isError = False` : un run qui attend un humain n'est pas un échec. Le texte dit au LLM ce qui attend et comment reprendre. À partir de là, un humain tranche par `loom approve` ou par REST, et le client relit avec `run_status`.

**À retenir.**

- Un run en pause rend `status: paused`, le `run_id` et `pending_approvals` dans le résultat structuré. Le client n'a pas à deviner.
- `run_status`, `run_report` et `cancel` prennent `run_id` et, pour une session nommée, `session_id` (comme en REST).
- Le même run se lit en ressource : `loom://runs/{run_id}?session_id=…`, `…/events`, `loom://traces/{run_id}`.
- **Piège.** Le LLM client n'a aucun moyen d'approuver : si vous voulez qu'il le puisse, vous contournez la sécurité. Prévoyez plutôt un canal humain (REST, application, ou l'elicitation ci-dessous).

### Exemple 21.3 : l'elicitation, ou le formulaire d'approbation dans le client

**Pourquoi.** Faire sortir l'artisan de Claude Desktop pour aller approuver ailleurs casse le fil. MCP prévoit un mécanisme : le serveur demande à l'application cliente de montrer un **formulaire** à l'utilisateur et de renvoyer sa réponse, dans le même appel.

**Objectif.** Déclarer la capacité côté client, recevoir le formulaire, répondre, et vérifier que l'e-mail part une seule fois avec la décision au journal.

**Mise en place.** `client_mcp_elicitation.py` : le client déclare un `elicitation_callback`, ce qui annonce la capacité au serveur. Le callback joue le rôle de l'artisan qui lit le formulaire et accorde.

```python
import asyncio

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import ElicitRequestParams, ElicitResult

SERVEUR = StdioServerParameters(
    command="uv", args=["run", "loom", "--config", "api.yaml", "mcp"]
)


async def questionner(contexte, params: ElicitRequestParams) -> ElicitResult:
    """Un humain répond au formulaire : ici, on accorde."""
    print("formulaire :", params.message)
    print("champs     :", sorted(params.requestedSchema["properties"]))
    return ElicitResult(action="accept", content={"decision": "accorder", "motif": "Vérifié par Denis"})


async def main() -> None:
    async with stdio_client(SERVEUR) as (lecture, ecriture):
        async with ClientSession(lecture, ecriture, elicitation_callback=questionner) as session:
            await session.initialize()
            reponse = await session.call_tool(
                "relance",
                {"message": "Relance Mme Martin pour le devis D-2026-042.", "session_id": "mcp-eli"},
            )
            print("statut :", reponse.structuredContent["status"])
            print("texte  :", reponse.content[0].text)


asyncio.run(main())
```

**Exécution.**

```bash
rm -f data/emails.log
uv run python client_mcp_elicitation.py
cat data/emails.log
uv run loom --config api.yaml sessions export mcp-eli | jq -c 'select(.type|test("approval")) | {type, by:.payload.by, reason:.payload.reason}'
```

```text
formulaire : Approbation demandée pour l'outil envoyer_email.
Arguments : {"destinataire": "mme.martin@example.fr", "objet": "Votre devis D-2026-042", "corps": "Bonjour Madame Martin, je me permets de revenir vers vous au sujet du devis D-2026-042 (1 840 €) envoyé le 3 septembre. Restant à votre disposition, cordialement."}
Motif : outil déclaré à approbation obligatoire (effets : irreversible)
champs     : ['decision', 'motif']
statut : completed
texte  : La relance du devis D-2026-042 est partie chez Mme Martin.
mme.martin@example.fr | Votre devis D-2026-042
{"type":"approval.requested","by":null,"reason":"outil déclaré à approbation obligatoire (effets : irreversible)"}
{"type":"approval.granted","by":"mcp:mcp","reason":"Vérifié par Denis"}
```

Le run **n'est pas passé par `paused`** : le serveur a monté un approbateur en ligne bâti sur l'elicitation. Le formulaire a deux champs, `decision` (`accorder` ou `refuser`) et `motif`. L'identité de celui qui a tranché, `mcp:mcp`, est le nom que le client MCP a donné à sa session (ici, celui par défaut du SDK) : sur stdio, Loom n'a pas d'identité plus précise.

**À retenir.**

- L'elicitation n'existe que si le **client** la déclare. Sinon, on retombe sur le run en pause de l'exemple précédent.
- Un formulaire MCP n'a qu'un niveau de propriétés : on ne peut pas y **corriger les arguments** d'un appel (ce que permet `POST …/approve` avec `arguments`).
- Le motif saisi est inscrit au journal, comme toute décision.
- **Piège.** Dans un vrai client (Claude Desktop), c'est lui qui affiche le formulaire à l'utilisateur ; faites attention à ce que celui-ci lise bien l'adresse et le montant avant d'accorder. Les arguments sont dans le message du formulaire.

### Exemple 21.4 : brancher Claude Code et Claude Desktop

**Pourquoi.** C'est le but : demander à Claude, dans votre éditeur ou votre application de bureau, de relancer Mme Martin, et que ce soit l'agent de la Plomberie Dupont qui s'en charge.

**Objectif.** Déclarer le serveur dans Claude Code et dans Claude Desktop.

**Non exécuté ici** : Claude Desktop n'est pas disponible dans l'environnement qui a servi à vérifier ce guide, et l'ajout d'un serveur dans la configuration de Claude Code n'a pas été fait pour ne pas la modifier. Seule la syntaxe de `claude mcp add` a été confirmée avec `claude mcp add --help`. Les commandes lancées par les deux configurations (`uv run loom --config api.yaml mcp` et le serveur HTTP) sont, elles, exécutées dans les exemples 21.1 à 21.6.

**Claude Code.** Un serveur stdio s'ajoute en nommant la commande à lancer. L'option `--directory` de `uv` place le process dans le dossier du projet, ce qui compte parce que `api.yaml` et `data-api/` sont des chemins relatifs :

```bash
claude mcp add plomberie -- uv --directory /chemin/vers/relance run loom --config api.yaml mcp
claude mcp list
```

Pour le serveur HTTP de l'exemple 21.5, la clé passe en en-tête :

```bash
claude mcp add --transport http plomberie-http http://127.0.0.1:18305/mcp/ --header "Authorization: Bearer lk_votre_cle"
```

**Claude Desktop.** Dans le fichier de configuration des serveurs MCP (menu Réglages, Développeur, Modifier la configuration), ajoutez :

```json
{
  "mcpServers": {
    "plomberie": {
      "command": "uv",
      "args": ["--directory", "/chemin/vers/relance", "run", "loom", "--config", "api.yaml", "mcp"]
    }
  }
}
```

Redémarrez l'application : les outils `devis`, `photo` et `relance` apparaissent dans la liste des outils. Dans Claude, « Relance Mme Martin pour le devis D-2026-042 » appelle `relance`. Si Claude Desktop déclare l'elicitation, un formulaire d'approbation s'affiche ; sinon le run rend la main en pause, et vous approuvez avec `loom approve`.

**À retenir.**

- Un serveur stdio, c'est une **commande** que l'application lance. Tous les chemins relatifs sont ceux du dossier courant du process : fixez-le (`uv --directory`).
- Le client MCP agit toujours **au nom d'un seul client Loom** en stdio (`--tenant`). Pour servir plusieurs entreprises, passez au MCP en HTTP.
- Un `loom.yaml` avec des modèles réels a besoin de ses clés d'API (`ANTHROPIC_API_KEY`…) dans l'environnement du process lancé par l'application : Claude Desktop ne transmet pas celui de votre terminal. Ajoutez un bloc `"env": {…}` à la configuration JSON si besoin.

### Exemple 21.5 : le MCP en HTTP, avec clé d'API

**Pourquoi.** Un serveur stdio ne sert qu'un utilisateur sur sa machine. Pour qu'un outil distant (un client MCP hébergé, un collègue) utilise vos agents, il faut un serveur réseau, authentifié, qui sait **quelle entreprise** appelle.

**Objectif.** Monter le MCP dans l'application REST, créer une clé, se connecter avec un client HTTP, et voir les protections (clé, `Origin`, `Host`).

**Mise en place.** Il faut une clé d'API : le serveur MCP HTTP exige une authentification à chaque requête, sans quoi il refuse de démarrer. On en crée une avec ses portées (le chapitre 22 détaille tout cela) :

```bash
uv run loom --config api.yaml keys create app-dupont --scope run --scope read --scope read_content --scope approve
```

```text
Clé        : lk_shcHj-jK7D5Ayz-VER2qxZ9Y0tuG1RHDAd0OYEAKhHA
Elle n'est affichée qu'ici : la config ne garde que son empreinte.

À ajouter dans la configuration :

security:
  api_keys:
    - id: app-dupont
      hash: sha256:f6cb1026557406800e3203e24d9e184440b791db290ea51e4c840d388abc77c5
      scopes: [run, read, read_content, approve]
```

Sans clé déclarée, `loom validate` et `loom serve` refusent la configuration :

```text
Configuration : …/mcp-http.yaml: (racine) — 'server.mcp.http' expose les agents comme outils : déclarer au moins une clé dans 'security.api_keys' (loom keys create)
```

(Votre clé sera différente. Notez-la : elle n'est plus affichée ensuite, la configuration n'en garde que l'empreinte.) On crée `mcp-http.yaml` en copiant `api.yaml`, avec trois changements : `storage.events.path` devient `data-mcp/journal.db`, le port devient 18305, et la fin du fichier (sections `server` et `security`) devient :

```yaml
server:
  http: {host: 127.0.0.1, port: 18305}
  mcp: {http: true}

security:
  api_keys:
    - id: app-dupont
      hash: sha256:f6cb1026557406800e3203e24d9e184440b791db290ea51e4c840d388abc77c5
      scopes: [run, read, read_content, approve]
```

Le client Python, `client_mcp_http.py`, passe la clé dans l'en-tête `Authorization` :

```python
import asyncio
import os

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

URL = "http://127.0.0.1:18305/mcp/"
CLE = os.environ["LOOM_CLE"]


async def main() -> None:
    en_tetes = httpx.AsyncClient(headers={"Authorization": f"Bearer {CLE}"})
    async with streamable_http_client(URL, http_client=en_tetes) as (lecture, ecriture, _):
        async with ClientSession(lecture, ecriture) as session:
            await session.initialize()
            print("outils :", [t.name for t in (await session.list_tools()).tools])
            reponse = await session.call_tool(
                "devis", {"message": "Que sait-on du devis D-2026-042 ?", "session_id": "mcp-http"}
            )
            print("devis  :", reponse.content[0].text)


asyncio.run(main())
```

**Exécution.** Dans un premier terminal :

```bash
uv run loom --config mcp-http.yaml serve
```

```text
Profil     : aucun (ni --profile, ni LOOM_PROFILE, ni 'profile:') ; surcharges : aucune
API REST   : http://127.0.0.1:18305/v1
Agents     : devis, photo, relance
MCP HTTP   : http://127.0.0.1:18305/mcp
Outils MCP : devis, photo, relance
Ressources : loom://runs, loom://sessions, et 6 gabarits
```

Dans un second :

```bash
export LOOM_CLE=lk_shcHj-jK7D5Ayz-VER2qxZ9Y0tuG1RHDAd0OYEAKhHA     # votre clé
uv run python client_mcp_http.py
```

```text
outils : ['devis', 'photo', 'relance', 'run_status', 'run_report', 'cancel']
devis  : Le devis D-2026-042 de Mme Martin (remplacement du chauffe-eau) s'élève à 1 840 € TTC, envoyé le 3 septembre.
```

Les protections, une par une, avec `curl` (la requête `initialize` est la première d'une session MCP) :

```bash
B=http://127.0.0.1:18305
KEY=$LOOM_CLE
INIT='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"t","version":"1"}}}'
H=(-H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream')
curl -s -o /dev/null -w "%{http_code}\n"      -X POST $B/mcp/ "${H[@]}" -H "Authorization: Bearer $KEY" -d "$INIT"
curl -s -w " [HTTP %{http_code}]\n"            -X POST $B/mcp/ "${H[@]}" -d "$INIT"
curl -s -w " [HTTP %{http_code}]\n"            -X POST $B/mcp/ "${H[@]}" -H "Authorization: Bearer lk_mauvaise" -d "$INIT"
curl -s -w " [HTTP %{http_code}]\n"            -X POST $B/mcp/ "${H[@]}" -H "Authorization: Bearer $KEY" -H "Origin: https://evil.example" -d "$INIT"
curl -s -w " [HTTP %{http_code}]\n"            -X POST $B/mcp/ "${H[@]}" -H "Authorization: Bearer $KEY" -H "Host: evil.example" -d "$INIT"
```

```text
200
{"detail":"Clé d'API absente : en-tête 'Authorization: Bearer …' ou 'X-API-Key'"} [HTTP 401]
{"detail":"Clé d'API refusée"} [HTTP 401]
Invalid Origin header [HTTP 403]
Invalid Host header [HTTP 421]
```

La clé bonne passe ; sans clé ou avec une mauvaise clé, `401`. Un `Origin` qui n'est pas autorisé donne `403` (protection contre les pages web malveillantes qui tenteraient de parler à votre serveur local). Un `Host` inattendu donne `421` (protection contre le *DNS rebinding*). Une requête **sans** `Origin` passe : un client natif n'en envoie pas. Derrière un nom de domaine ou un proxy, déclarez les valeurs attendues :

```yaml
server:
  mcp:
    http: true
    allowed_origins: [https://portail.plomberie-dupont.example]
    allowed_hosts: [mcp.plomberie-dupont.example]
```

Le même serveur répond aussi en REST avec la même clé (`curl -H "Authorization: Bearer $KEY" $B/v1/agents`), ou avec l'en-tête `X-API-Key: $KEY`.

**À retenir.**

- Le MCP HTTP est un **seul serveur pour tous les clients** : c'est la clé d'API qui désigne l'entreprise, jamais le corps de la requête.
- L'URL se termine par `/mcp/` (avec la barre oblique finale) : sans elle, le serveur répond par une redirection que certains clients ne suivent pas.
- Les portées de la clé s'appliquent : une clé sans `run` ne peut pas appeler un agent, une clé sans `read` ne voit pas les ressources. Les **outils** restent listés quelle que soit la clé ; seule la liste `agents` de la clé les filtre.
- **Un serveur MCP en HTTP par process.** Le suivi du projet (point #020 du backlog) signale qu'un second serveur MCP en HTTP, dans le même process, ne répond plus. Ne montez donc pas deux applications avec `mcp.http` dans un même process (par exemple deux `create_app` côte à côte) ; lancez-les dans deux process.
- **Piège.** Un client MCP hébergé voit le texte de vos réponses : n'y faites pas figurer de secrets.

### Exemple 21.6 : l'approbation à travers le MCP HTTP

**Pourquoi.** En HTTP, l'elicitation n'est pas disponible pour le serveur de Loom : il est sans état, et la réponse d'un formulaire ne pourrait pas arriver par la requête qui l'attend. Que se passe-t-il quand le client la déclare quand même ?

**Objectif.** Constater que le run se met en pause, puis l'approuver par REST avec la clé.

**Mise en place.** `client_mcp_http_eli.py` reprend celui de l'exemple 21.3 pour le callback, et celui de l'exemple 21.5 pour la connexion :

```python
import asyncio
import os

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import ElicitRequestParams, ElicitResult

URL = "http://127.0.0.1:18305/mcp/"
CLE = os.environ["LOOM_CLE"]


async def questionner(contexte, params: ElicitRequestParams) -> ElicitResult:
    print("formulaire reçu :", params.message.splitlines()[0])
    return ElicitResult(action="accept", content={"decision": "accorder", "motif": "Vérifié par Denis"})


async def main() -> None:
    en_tetes = httpx.AsyncClient(headers={"Authorization": f"Bearer {CLE}"})
    async with streamable_http_client(URL, http_client=en_tetes) as (lecture, ecriture, _):
        async with ClientSession(lecture, ecriture, elicitation_callback=questionner) as session:
            await session.initialize()
            reponse = await session.call_tool(
                "relance",
                {"message": "Relance Mme Martin pour le devis D-2026-042.", "session_id": "mcp-http-relance"},
            )
            s = reponse.structuredContent
            print("statut :", s["status"], "| en attente :", [a["tool_name"] for a in s["pending_approvals"]])
            print("texte  :", reponse.content[0].text[:140])


asyncio.run(main())
```

**Exécution.**

```bash
rm -f data/emails.log
uv run python client_mcp_http_eli.py
ls data/emails.log
```

```text
statut : paused | en attente : ['envoyer_email']
texte  : Run 01a1268e-2084-76be-91b0-16cb94561e32 en attente d'approbation : envoyer_email (fake_1_0). Un humain doit trancher (API REST ou « loom ap
ls: cannot access 'data/emails.log': No such file or directory
```

Le formulaire n'a **jamais été envoyé** (aucune ligne « formulaire reçu ») : le run est en pause et l'e-mail n'est pas parti. On tranche par REST, avec la même clé (portée `approve`) :

```bash
B=http://127.0.0.1:18305
R=01a1268e-2084-76be-91b0-16cb94561e32      # votre run_id
curl -s -X POST "$B/v1/runs/$R/approve?session_id=mcp-http-relance" -H "Authorization: Bearer $LOOM_CLE" \
  -H 'Content-Type: application/json' -d '{"reason":"Vérifié par Denis"}' | jq -c .
sleep 1
curl -s -H "Authorization: Bearer $LOOM_CLE" "$B/v1/runs/$R?session_id=mcp-http-relance" | jq -r '.status'
cat data/emails.log
curl -s -H "Authorization: Bearer $LOOM_CLE" "$B/v1/sessions/mcp-http-relance/events" | jq -c 'select(.type=="approval.granted") | .payload | {by, reason}'
```

```text
{"run_id":"01a1268e-2084-76be-91b0-16cb94561e32","calls":["fake_1_0"]}
completed
mme.martin@example.fr | Votre devis D-2026-042
{"by":"app-dupont","reason":"Vérifié par Denis"}
```

Cette fois, la décision est **signée par l'identifiant de la clé** (`app-dupont`) : en REST, la clé fait foi de l'identité de celui qui tranche (un champ `by` dans le corps la remplace si on en donne un).

**À retenir.**

- **Elicitation ou pause, jamais les deux.** En stdio, si le client déclare l'elicitation, le run ne passe pas par la pause. En HTTP, il s'arrête toujours.
- Pour un portail ou une application mobile, le canal naturel reste REST (chapitre 20). L'elicitation est le confort d'un outil de bureau.
- Donner `approve` à une clé qui n'a pas `read_content` revient à approuver à l'aveugle (les arguments sont masqués) ; `loom validate` le signale.
- **Écart de version.** La documentation du projet précise que le serveur MCP HTTP est « sans état » et que l'elicitation n'y marche jamais. Le comportement est bien celui-là en 2.0.0 (constaté ci-dessus), mais le commentaire qui l'explique n'est présent que dans les sources de la 2.0.1.

---
## 22. Servir plusieurs entreprises : clients, clés d'API et isolation

### Ce qu'il faut comprendre avant de commencer

Jusqu'ici, tout s'est passé chez un seul client, `default`. Mais le but est de servir **plusieurs entreprises** avec une seule instance : la Plomberie Dupont, et son concurrent Chauffage Martin, qui ne doivent jamais voir les données l'une de l'autre. Loom appelle **client** (*tenant*) un espace isolé : son journal, ses budgets, ses secrets, ses variables de prompt.

Dès que la configuration déclare un client, la liste devient **fermée** : le client `default` n'existe plus, et un client inconnu est refusé.

| Réglage de `tenants[]` | Effet |
|---|---|
| `id` | Nom du client. |
| `variables` | Valeurs des `{{ variable }}` des prompts (le prompt est commun, les variables changent). |
| `models` | Correspondance de modèles : `{PRINCIPAL: ECONOMIQUE}` fait utiliser `ECONOMIQUE` à ce client partout où la configuration dit `PRINCIPAL` (orchestrateur, rôles, juges, secours). |
| `approvals` | Approbation imposée par outil, quoi qu'en dise l'outil : `{chercher_devis: always}`. |
| `agents`, `tools_deny` | Agents que le client peut lancer ; outils (ou rôles, sous-agents) qu'on lui retire. |
| `budgets`, `quotas` | Plafonds du client (chapitre 16) : coût par jour ou par mois, runs par minute. |
| `secrets` | `{nom attendu par la configuration: variable d'environnement de ce client}`, par exemple `{ANTHROPIC_API_KEY: MARTIN_ANTHROPIC_KEY}` : chacun paie son fournisseur avec sa propre clé. |
| `storage` | Journal (et fichiers) propre au client, sur un autre support : isolation physique. Il doit déclarer `events`. |

Ce qu'un client ne peut **pas** changer : les prompts eux-mêmes (seules leurs variables), les outils Python, la structure des agents. La logique métier reste dans un seul endroit.

Pour qu'un appel arrive chez le bon client, il y a deux voies :

- **En Python et en CLI**, on le nomme : `loom run … --tenant martin-chauffage`, `loom.run(…, tenant="martin-chauffage")`.
- **En REST et en MCP HTTP**, c'est la **clé d'API** qui désigne le client. Rien dans le corps de la requête ne peut le changer, ce qui interdit à une clé d'agir au nom d'un autre.

Les clés se déclarent dans `security.api_keys` :

| Champ | Effet |
|---|---|
| `id`, `tenant` | Nom de la clé, client auquel elle donne accès. |
| `hash` | Empreinte `sha256:` de la clé. La clé elle-même n'est **jamais** dans la configuration. |
| `scopes` | `run` (lancer, arrêter), `read` (lire sans contenu), `read_content` (lire aussi les contenus), `approve` (trancher une approbation, avec `read_content` pour voir ce qu'on approuve), `admin` (effacer une session, lancer sans juges). |
| `agents` | Liste des agents que cette clé peut appeler. |
| `rate_limit: {per_minute: N}` | Requêtes par minute accordées à cette clé. |
| `expires` | Fin de validité. |

Une requête présente la clé dans `Authorization: Bearer lk_…` ou dans `X-API-Key`.

### Exemple 22.1 : deux entreprises, une instance

**Pourquoi.** Chauffage Martin veut aussi utiliser l'agent de relance. Elle est la concurrente de la Plomberie Dupont : ses devis, ses clients, ses coûts doivent rester chez elle. Chacune a son nom de signature et son budget, et Martin préfère un modèle moins cher et valider chaque consultation de devis avant qu'elle ne parte.

**Objectif.** Déclarer trois clients (Dupont, Martin, et le cabinet comptable de Dupont, limité à la lecture de devis), les faire tourner, et constater l'isolation.

**Mise en place.** Un dossier d'agents `agents_clients/` et un dossier de prompts `prompts_clients/`.

```bash
mkdir agents_clients prompts_clients
```

`prompts_clients/relance.md` : le prompt cite des **variables**.

```markdown
Tu es l'assistant de l'entreprise {{ entreprise }}. Pour relancer un client :
retrouve le devis avec `chercher_devis`, puis envoie l'e-mail avec `envoyer_email`.
Signe toujours « {{ signature }} ». Ne calcule et n'invente jamais un montant : reprends celui du devis.
```

`agents_clients/relance.yaml` :

```yaml
name: relance
description: Relance un client pour un devis resté sans réponse.

main:
  model: PRINCIPAL
  system_file: relance.md

max_iterations: 6

tools:
  - python: chercher_devis
  - python: envoyer_email
```

`agents_clients/devis.yaml` :

```yaml
name: devis
description: Répond à une question sur un devis (lecture seule).

main:
  model: DEVIS
  system: Tu réponds aux questions sur les devis de {{ entreprise }}. Consulte-les avec `chercher_devis`.

max_iterations: 4

tools:
  - python: chercher_devis
```

`clients.yaml` :

```yaml
version: 1

imports: [outils]
agents_dir: agents_clients/
prompts_dir: prompts_clients/

models:
  - id: PRINCIPAL
    sdk: fake
    model: fake-principal
    pricing: {input: 3.0, output: 15.0}
    params:
      script:
        - text: Je commence par retrouver le devis.
          tool_calls:
            - {name: chercher_devis, arguments: {numero: D-2026-042}}
        - text: J'envoie la relance.
          tool_calls:
            - name: envoyer_email
              arguments:
                destinataire: mme.martin@example.fr
                objet: Votre devis D-2026-042
                corps: "Bonjour Madame Martin, je me permets de revenir vers vous au sujet du devis D-2026-042 (1 840 €) envoyé le 3 septembre. Restant à votre disposition, cordialement."
        - text: La relance du devis D-2026-042 est partie chez Mme Martin.
  - id: ECONOMIQUE
    sdk: fake
    model: fake-economique
    pricing: {input: 0.8, output: 4.0}
    params:
      script:
        - text: Je commence par retrouver le devis.
          tool_calls:
            - {name: chercher_devis, arguments: {numero: D-2026-042}}
        - text: J'envoie la relance.
          tool_calls:
            - name: envoyer_email
              arguments:
                destinataire: mme.martin@example.fr
                objet: Votre devis D-2026-042
                corps: "Bonjour Madame Martin, je me permets de revenir vers vous au sujet du devis D-2026-042 (1 840 €) envoyé le 3 septembre. Restant à votre disposition, cordialement."
        - text: La relance du devis D-2026-042 est partie chez Mme Martin.
  - id: DEVIS
    sdk: fake
    model: fake-devis
    pricing: {input: 3.0, output: 15.0}
    params:
      script:
        - text: Je consulte le devis.
          tool_calls:
            - {name: chercher_devis, arguments: {numero: D-2026-042}}
        - text: Le devis D-2026-042 s'élève à 1 840 € TTC, envoyé le 3 septembre.

tenants:
  - id: dupont-plomberie
    variables: {entreprise: Plomberie Dupont, signature: "L'équipe Dupont"}
  - id: martin-chauffage
    variables: {entreprise: Chauffage Martin, signature: "Chauffage Martin, service client"}
    models: {PRINCIPAL: ECONOMIQUE}
    approvals: {chercher_devis: always}
    storage:
      events: {backend: jsonl, path: data-clients/martin}
  - id: comptable-dupont
    variables: {entreprise: Plomberie Dupont (comptabilité), signature: "Cabinet comptable"}
    agents: [devis]

telemetry:
  logging: {level: WARNING}

storage:
  events: {backend: sqlite, path: data-clients/journal.db}
```

(Les modèles simulés sont des doublons à des fins de démonstration : `ECONOMIQUE` joue le même scénario que `PRINCIPAL` mais avec un tarif plus bas, ce qui rend la différence visible dans les coûts.)

**Exécution.** La validation décrit chaque client :

```bash
rm -rf data-clients
uv run loom --config clients.yaml validate | sed -n '/client dupont/,$p'
```

```text
  client dupont-plomberie
    variables : entreprise, signature
    devis : modèle DEVIS, 1 outil(s) Python
    relance : modèle PRINCIPAL, 2 outil(s) Python

  client martin-chauffage
    modèles : PRINCIPAL → ECONOMIQUE
    approbations : chercher_devis : always
    variables : entreprise, signature
    stockage : jsonl (/chemin/vers/relance/data-clients/martin)
    devis : modèle DEVIS, 1 outil(s) Python
    relance : modèle PRINCIPAL, 2 outil(s) Python

  client comptable-dupont
    agents : devis
    variables : entreprise, signature
    devis : modèle DEVIS, 1 outil(s) Python

5 agent(s) monté(s) sans erreur.
```

Les clients se choisissent avec `--tenant`. Un client inconnu, et un agent non ouvert à un client, sont refusés :

```bash
uv run loom --config clients.yaml run relance "Relance Mme Martin pour le devis D-2026-042." --tenant dupont-plomberie --session r1
uv run loom --config clients.yaml run devis "Quel est le devis ?" --tenant inconnu
uv run loom --config clients.yaml run relance "Relance" --tenant comptable-dupont
```

```text
—

Statut     : paused · itérations : 2 · tokens : 1084/192 · coût : 0.0061 $
Run        : 01a12690-3b71-72bb-9353-a8c1e92d617a
En attente : envoyer_email (fake_1_0) — loom approve 01a12690-3b71-72bb-9353-a8c1e92d617a --call fake_1_0
Client 'inconnu' non déclaré (clients : dupont-plomberie, martin-chauffage, comptable-dupont)
Agent 'relance' non ouvert au client 'comptable-dupont'
```

Le même agent, la même session `r1`, chez Martin :

```bash
uv run loom --config clients.yaml run relance "Relance Mme Martin pour le devis D-2026-042." --tenant martin-chauffage --session r1
find data-clients -type f | sort
uv run loom --config clients.yaml sessions list --tenant dupont-plomberie
uv run loom --config clients.yaml sessions list --tenant martin-chauffage
```

```text
—

Statut     : paused · itérations : 1 · tokens : 436/70 · coût : 0.0006 $
Run        : 01a12690-544d-7393-a7b3-b43c3ba8fb29
En attente : chercher_devis (fake_0_0) — loom approve 01a12690-544d-7393-a7b3-b43c3ba8fb29 --call fake_0_0
data-clients/journal.db
data-clients/martin/martin-chauffage/r1.jsonl
r1                           21 événements   2026-10-10 18:05
r1                           12 événements   2026-10-10 18:05
```

Deux clients, deux sessions du même nom `r1`, aucune collision :

- chez Dupont, le run est allé jusqu'à l'envoi (21 événements), chez Martin il s'est arrêté dès la consultation du devis (12 événements), parce que `approvals: {chercher_devis: always}` impose une validation que l'outil ne demandait pas ;
- le journal de Martin est dans **son propre fichier** (`data-clients/martin/…`), pas dans la base de la racine ;
- Martin a utilisé `fake-economique` (`loom inspect … --tenant martin-chauffage` montre `modèle fake-economique`) et son run a coûté 0,0006 $ ;
- la commande `inspect` doit recevoir `--tenant` pour retrouver le run d'un autre client que celui de la configuration.

Une variable citée par un prompt et absente d'un client est détectée **au chargement** : en retirant `signature` au cabinet comptable, `loom validate` répond :

```text
Configuration : …/clients.yaml: Agent 'relance', main.system — {{ signature }} : variable non définie pour le client 'comptable-dupont' (tenants[].variables)
```

**À retenir.**

- Un client n'a besoin de déclarer que ce qui le distingue. Tout le reste est commun.
- L'isolation est vérifiée à tous les niveaux : journal, sessions, consommation, fichiers.
- Les noms de session sont **par client** : deux clients peuvent avoir chacun une session `r1`.
- Les secrets d'un client (`secrets`) s'affichent dans `loom validate` (ligne `secrets : ANTHROPIC_API_KEY → MARTIN_ANTHROPIC_KEY`) : seule la correspondance de **noms** est dans la configuration, jamais la valeur. **Non exécuté ici** : l'usage de clés distinctes avec un vrai fournisseur (pas de clé disponible).
- **Piège.** Un client qui déclare `storage` y déclare aussi `events`, sans quoi la configuration est refusée (le journal par défaut est la mémoire). Et sans bloc `storage`, le client partage le journal de la racine, où seul son identifiant le distingue.

### Exemple 22.2 : des clés d'API par client, avec leurs limites

**Pourquoi.** Les applications de Dupont et de Martin appellent l'API REST. Chacune doit être reconnue, ne voir que ses données, et ne pouvoir faire que ce qu'on lui a donné : une application de comptabilité ne doit pas pouvoir lancer ni effacer quoi que ce soit.

**Objectif.** Fabriquer quatre clés, les déclarer, et vérifier chaque refus : 401, 403, 404, 429.

**Mise en place.** On fabrique les clés avec la commande `loom keys create`. Chacune s'affiche **une seule fois** ; notez-la.

```bash
uv run loom --config clients.yaml keys create app-dupont --tenant dupont-plomberie \
  --scope run --scope read --scope read_content --scope approve --expires 90j
uv run loom --config clients.yaml keys create app-martin --tenant martin-chauffage \
  --scope run --scope read --scope read_content --scope approve --agent relance --rate-limit 4
uv run loom --config clients.yaml keys create compta-dupont --tenant comptable-dupont --scope read
uv run loom --config clients.yaml keys create ancienne --tenant dupont-plomberie --expires 2026-09-01
```

La première commande donne :

```text
Clé        : lk_126gyHTMB4XRNbqirfrtOtmRq_Rm_WwI73EPJ9dzMKk
Elle n'est affichée qu'ici : la config ne garde que son empreinte.

À ajouter dans la configuration :

security:
  api_keys:
    - id: app-dupont
      hash: sha256:a97f5931574adf8a54857c8067a683aef428870a69362033f25c3794cbfb8435
      tenant: dupont-plomberie
      scopes: [run, read, read_content, approve]
      expires: 2027-01-08T16:06:40.037430Z
```

Les autres donnent leurs propres clés et empreintes (les vôtres seront différentes). On copie le bloc `security` de chacune dans un nouveau fichier `clients-api.yaml`, qui est une copie de `clients.yaml` avec ces changements :

- les dossiers de données deviennent `data-clients-api/` ;
- un quota de **2 runs par minute** chez Dupont : `quotas: {runs_per_minute: 2}` ;
- un budget de **0,001 $ par jour** chez Martin : `budgets: {tenant: {max_cost_per_day: 0.001}}` ;
- un port dédié et les quatre clés.

Les ajouts, avec vos propres empreintes à la place des `…` (ici, celles de l'exécution de ce guide) :

```yaml
tenants:
  - id: dupont-plomberie
    variables: {entreprise: Plomberie Dupont, signature: "L'équipe Dupont"}
    quotas: {runs_per_minute: 2}
  - id: martin-chauffage
    variables: {entreprise: Chauffage Martin, signature: "Chauffage Martin, service client"}
    models: {PRINCIPAL: ECONOMIQUE}
    approvals: {chercher_devis: always}
    budgets: {tenant: {max_cost_per_day: 0.001}}
    storage:
      events: {backend: jsonl, path: data-clients-api/martin}
  - id: comptable-dupont
    variables: {entreprise: Plomberie Dupont (comptabilité), signature: "Cabinet comptable"}
    agents: [devis]

server:
  http: {host: 127.0.0.1, port: 18307}

security:
  api_keys:
    - id: app-dupont
      hash: sha256:a97f5931574adf8a54857c8067a683aef428870a69362033f25c3794cbfb8435
      tenant: dupont-plomberie
      scopes: [run, read, read_content, approve]
      expires: 2027-01-08T16:06:40Z
    - id: app-martin
      hash: sha256:8fdf68326e3b5c859c8f58ea5acff451bd17491fb3bfe87a7e0d873264453dd2
      tenant: martin-chauffage
      scopes: [run, read, read_content, approve]
      agents: [relance]
      rate_limit: {per_minute: 4}
    - id: compta-dupont
      hash: sha256:80e9561eb5c20855e3a51dee76b3956ed0fabbddf2a70b7b1e33159337e9a4b3
      tenant: comptable-dupont
      scopes: [read]
    - id: ancienne
      hash: sha256:c48b2a84c97c22ca7b0d1f228feb74af890c696bb64d989a1dd189e8c38f3842
      tenant: dupont-plomberie
      scopes: [run, read]
      expires: 2026-09-01T00:00:00Z
```

(Le reste du fichier — `imports`, `models`, `telemetry`, `storage` — est celui de `clients.yaml`, avec `data-clients-api` à la place de `data-clients`.) La validation résume les clés :

```bash
uv run loom --config clients-api.yaml validate | grep -E "Clés|^    [a-z-]+ : client"
```

```text
Clés d'API : app-dupont, app-martin, compta-dupont, ancienne
    app-dupont : client dupont-plomberie, portées run, read, read_content, approve, expire le 2027-01-08
    app-martin : client martin-chauffage, portées run, read, read_content, approve, agents relance, débit 4/min
    compta-dupont : client comptable-dupont, portées read
    ancienne : client dupont-plomberie, portées run, read, EXPIRÉE
```

**Exécution.** On lance le serveur (`uv run loom --config clients-api.yaml serve`), puis, dans un autre terminal, on prépare des variables avec les clés obtenues :

```bash
B=http://127.0.0.1:18307
D=lk_126gyHTMB4XRNbqirfrtOtmRq_Rm_WwI73EPJ9dzMKk      # app-dupont
M=lk_znFWbG_VR5lfIUtPzxw83zH2ox90ibO1qCcMxza9G1M      # app-martin
C=lk_I7gMKEGw5fz5UreX0StvJM3_f2qXf96fv64w8kvTlYs      # compta-dupont
A=lk_NHOrSuxsCv4AWuijRHJre38PDz7kwiSbu2C_f6VKZGk      # ancienne (expirée)
J='Content-Type: application/json'
```

**401 : pas de clé, mauvaise clé, clé expirée.**

```bash
curl -s -w " [HTTP %{http_code}]\n" $B/v1/agents
curl -s -w " [HTTP %{http_code}]\n" -H "Authorization: Bearer lk_nope" $B/v1/agents
curl -s -w " [HTTP %{http_code}]\n" -H "Authorization: Bearer $A" $B/v1/agents
```

```text
{"detail":"Clé d'API absente : en-tête 'Authorization: Bearer …' ou 'X-API-Key'"} [HTTP 401]
{"detail":"Clé d'API refusée"} [HTTP 401]
{"detail":"Clé 'ancienne' expirée le 2026-09-01 00:00 UTC"} [HTTP 401]
```

**La clé décide de ce qu'on voit** (les deux formes d'en-tête sont acceptées) :

```bash
curl -s -H "Authorization: Bearer $D" $B/v1/agents | jq -c '[.[].name]'
curl -s -H "X-API-Key: $M" $B/v1/agents | jq -c '[.[].name]'
curl -s -H "Authorization: Bearer $C" $B/v1/agents | jq -c '[.[].name]'
```

```text
["devis","relance"]
["relance"]
["devis"]
```

Dupont voit les deux agents. Martin n'en voit qu'un, parce que sa clé est limitée à `relance`. Le comptable n'en voit qu'un aussi, parce que son **client** n'a le droit qu'à `devis`.

**403 : portée ou agent non autorisé.**

```bash
curl -s -w " [HTTP %{http_code}]\n" -X POST $B/v1/agents/devis/runs -H "Authorization: Bearer $C" -H "$J" -d '{"message":"x"}'
curl -s -w " [HTTP %{http_code}]\n" -X POST $B/v1/agents/devis/runs -H "Authorization: Bearer $M" -H "$J" -d '{"message":"x"}'
```

```text
{"detail":"Clé sans la portée 'run'"} [HTTP 403]
{"detail":"Clé non autorisée sur l'agent 'devis'"} [HTTP 403]
```

**L'isolation des données.** Dupont lance une relance en arrière-plan (elle s'arrête sur l'approbation). Martin tente de la lire, de l'approuver, de lister les sessions :

```bash
R=$(curl -s -X POST $B/v1/agents/relance/runs -H "Authorization: Bearer $D" -H "$J" \
  -d '{"message":"Relance Mme Martin pour le devis D-2026-042.","session_id":"r1","background":true}' | jq -r .run_id)
sleep 1
curl -s -w " [HTTP %{http_code}]\n" -H "Authorization: Bearer $M" "$B/v1/runs/$R?session_id=r1"
curl -s -w " [HTTP %{http_code}]\n" -X POST "$B/v1/runs/$R/approve?session_id=r1" -H "Authorization: Bearer $M" -H "$J" -d '{}'
curl -s -H "Authorization: Bearer $M" $B/v1/sessions
curl -s -H "Authorization: Bearer $D" $B/v1/sessions | jq -c .
```

```text
{"detail":"Run 01a12691-749f-766d-9678-09cf5e1c61af inconnu"} [HTTP 404]
{"detail":"Run 01a12691-749f-766d-9678-09cf5e1c61af inconnu"} [HTTP 404]
{"detail":"Clé limitée à certains agents : la liste des sessions ne peut pas être filtrée"}
[{"session_id":"r1","last_seq":21,"updated_at":"2026-10-10T16:07:09.258264Z"}]
```

Pour Martin, le run de Dupont **n'existe pas** : `404`, et non `403`. On ne révèle même pas son existence. Notez aussi la dernière réponse de Martin : une clé limitée à certains agents ne peut pas lister les sessions, parce que Loom ne saurait pas filtrer correctement les sessions qui mélangent des agents.

**Les approbations et l'effacement demandent leur portée.**

```bash
curl -s -w " [HTTP %{http_code}]\n" -X POST "$B/v1/runs/$R/approve?session_id=r1" -H "Authorization: Bearer $C" -H "$J" -d '{}'
curl -s -w " [HTTP %{http_code}]\n" -X DELETE "$B/v1/sessions/r1" -H "Authorization: Bearer $D"
```

```text
{"detail":"Clé sans la portée 'approve'"} [HTTP 403]
{"detail":"Clé sans la portée 'admin'"} [HTTP 403]
```

La clé de Dupont a `run`, `read`, `read_content` et `approve`, mais pas `admin` : elle ne peut pas effacer une session. Réservez `admin` à une clé d'exploitation, jamais à celle d'une application.

**429 : le débit de la clé.** `app-martin` a droit à 4 requêtes par minute (toutes routes confondues) :

```bash
for i in 1 2 3 4 5; do curl -s -o /dev/null -w "$i : %{http_code}\n" -H "Authorization: Bearer $M" $B/v1/agents; done
curl -s -i -H "Authorization: Bearer $M" $B/v1/agents | sed -n '1p;/[Rr]etry-[Aa]fter/p;/^{/p'
```

```text
1 : 200
2 : 200
3 : 200
4 : 200
5 : 429
HTTP/1.1 429 Too Many Requests
retry-after: 60
{"detail":"Clé 'app-martin' : 4 requêtes par minute dépassé"}
```

(Attendez une minute après les commandes précédentes avant de lancer cette boucle : chaque requête de Martin compte dans la limite, et les trois précédentes l'auraient déjà entamée. Lors de la première exécution de ce guide, l'approbation tentée par Martin avait d'ailleurs reçu un 429 pour cette raison, avant d'être rejouée une minute plus tard et de recevoir le 404 montré plus haut.)

**429 aussi : le quota et le budget du client.** Dupont a droit à 2 runs par minute, Martin à 0,001 $ par jour :

```bash
for i in 1 2 3; do curl -s -o /tmp/q$i.out -D /tmp/q$i.hdr -w "run $i : %{http_code}\n" -X POST $B/v1/agents/devis/runs \
  -H "Authorization: Bearer $D" -H "$J" -d '{"message":"Que sait-on du devis D-2026-042 ?"}'; done
cat /tmp/q3.out; echo; grep -i retry /tmp/q3.hdr
```

```text
run 1 : 201
run 2 : 201
run 3 : 429
{"detail":"Client 'dupont-plomberie' : 2 par minute dépassé, réessayer dans 59.9 s"}
retry-after: 60
```

```bash
for i in 1 2 3; do curl -s -o /tmp/b$i.out -D /tmp/b$i.hdr -w "run $i : %{http_code}\n" -X POST $B/v1/agents/relance/runs \
  -H "X-API-Key: $M" -H "$J" -d "{\"message\":\"Relance\",\"session_id\":\"b$i\"}"; done
cat /tmp/b3.out; echo; grep -i retry /tmp/b3.hdr
```

```text
run 1 : 201
run 2 : 201
run 3 : 429
{"detail":"Client 'martin-chauffage' : budget du client atteint pour la journée : 0,00124 $ (plafond 0,00100 $)"}
retry-after: 28225
```

Le budget est contrôlé **au lancement** : le deuxième run démarre (on n'avait dépensé que 0,0006 $), le troisième est refusé. Le `Retry-After` de 28 225 secondes est le temps qui reste jusqu'à la fin de la journée UTC, où le compteur repart de zéro. Un run refusé n'écrit rien au journal.

**À retenir.**

- **401** : identité inconnue, absente ou expirée. **403** : identité connue, mais portée ou agent non autorisé. **404** : la ressource n'existe pas *pour ce client*. **429** : débit de la clé, quota de runs ou budget du client, avec `Retry-After`.
- La clé est comparée par son empreinte. Perdue, elle ne se retrouve pas : on en fabrique une autre.
- Une clé expirée est signalée par `loom validate` (`EXPIRÉE`) avant même qu'on s'en serve.
- Les clés sont des identifiants d'**applications**, pas de personnes. L'identité d'un approbateur humain se met dans le champ `by` du corps de `approve`, ou à défaut dans l'identifiant de la clé.
- **Piège.** `rate_limit` compte **toutes** les requêtes de la clé (lectures comprises), alors que `quotas.runs_per_minute` ne compte que les **runs**. Une application qui scrute l'état d'un run chaque seconde peut épuiser sa limite sans lancer un seul run. Préférez le SSE du chapitre 20.
- **Piège.** Les limites de débit et de quota par minute sont gardées **en mémoire de l'instance** (chapitre 16). Avec plusieurs process derrière un répartiteur, chacun a sa propre fenêtre : la limite réelle est multipliée par le nombre de process.

### Exemple 22.3 : deux clients en même temps dans un même process

**Pourquoi.** Dans un service, les requêtes de Dupont et de Martin arrivent en même temps, sur la même instance. Il faut s'assurer qu'aucune donnée ne se mélange quand elles se chevauchent.

**Objectif.** Lancer le même agent pour les deux clients en parallèle, avec le même nom de session, et vérifier journaux, coûts et sessions.

**Mise en place.** `deux_clients.py` (attention : ne nommez pas ce fichier `concurrent.py`, qui masquerait le module standard `concurrent` et ferait échouer `asyncio`) :

```python
import asyncio

from loom_ia.access import Loom
from loom_ia.core.model import Approved


async def accorder(demande):
    return Approved(by="denis")


async def relancer(loom: Loom, client: str):
    resultat = await loom.run(
        "relance",
        "Relance Mme Martin pour le devis D-2026-042.",
        session_id="relance-042",          # le même nom de session pour les deux clients
        tenant=client,
        approver=accorder,
    )
    return client, resultat


async def main() -> None:
    async with Loom.from_config("clients.yaml") as loom:
        resultats = await asyncio.gather(
            relancer(loom, "dupont-plomberie"), relancer(loom, "martin-chauffage")
        )
        for client, r in resultats:
            print(f"{client:18} {r.status.value:10} {r.cost_usd:.6f} $  {r.text}")
        for client in ("dupont-plomberie", "martin-chauffage"):
            conso = await loom.consumption(client, period="day")
            sessions = await loom.sessions(tenant_id=client)
            print(f"{client:18} jour : {conso.spent.cost:.6f} $ ({conso.runs} run) ; sessions : {[s.session_id for s in sessions]}")


asyncio.run(main())
```

**Exécution.**

```bash
rm -rf data-clients data/emails.log
uv run python deux_clients.py
find data-clients -type f | sort
cat data/emails.log
```

```text
dupont-plomberie   completed  0.009285 $  La relance du devis D-2026-042 est partie chez Mme Martin.
martin-chauffage   completed  0.002487 $  La relance du devis D-2026-042 est partie chez Mme Martin.
dupont-plomberie   jour : 0.009285 $ (1 run) ; sessions : ['relance-042']
martin-chauffage   jour : 0.002487 $ (1 run) ; sessions : ['relance-042']
data-clients/journal.db
data-clients/martin/martin-chauffage/relance-042.jsonl
mme.martin@example.fr | Votre devis D-2026-042
mme.martin@example.fr | Votre devis D-2026-042
```

Chacun ne voit que sa consommation (un run et son propre coût) et sa propre session, qui porte pourtant le même nom. Les deux journaux sont physiquement séparés (une base pour Dupont, un fichier JSONL pour Martin). Les deux e-mails sont partis, à la même adresse, parce que le devis fictif est le même pour les deux clients : c'est une commodité de l'exemple.

**À retenir.**

- Le nom du client est un argument de `run()`, `submit()`, `consumption()`, `sessions()`, `report()`… En Python, c'est à vous de le fournir ; en REST, la clé le fournit.
- Plusieurs clients dans un même process partagent les modèles et les outils (code Python), mais chaque client a son propre contexte monté : sa configuration, ses secrets, ses variables.
- **Isolation au niveau du journal partagé.** Avec Postgres, une politique de sécurité au niveau des lignes (chapitre 24) filtre chaque requête sur le client courant. Trois points du suivi du projet restent ouverts en 2.0.x et méritent d'être connus : une empreinte de requête peut être commune à deux clients (#019, sans fuite de contenu : elle ne porte que des octets communs), la table d'idempotence est hors de la politique de lignes (#021), et un second serveur MCP HTTP dans un même process ne répond plus (#020, chapitre 21).
- **Piège.** Un outil Python partagé par plusieurs clients reçoit `ToolContext.tenant_id` : si cet outil ouvre un fichier ou une base, c'est à lui de séparer les clients. Loom isole les journaux, pas ce que votre code fait de ses propres ressources (par exemple, notre `data/emails.log` est commun aux deux clients).

---
## 23. Webhooks, workers et bus : brancher Loom sur le reste du système

### Ce qu'il faut comprendre avant de commencer

Jusqu'ici, c'est vous (ou un client REST) qui lanciez les runs. En production, ce sont souvent d'autres systèmes qui déclenchent : le CRM de la Plomberie Dupont signale qu'un devis vient d'être signé, un planificateur lance chaque matin la tournée des relances. Et dès qu'on a plusieurs process, il faut répondre à trois questions : **qui ouvre le run** (les portes d'entrée), **qui le pilote** (les workers), **comment les autres process le voient passer** (le bus).

| Brique | Rôle | Réglage |
|---|---|---|
| **Porte d'entrée** (*trigger*) | Transforme un `POST` d'un système tiers en run : message, session et agent sont décidés par la configuration, pas par l'appelant. | `triggers:` + `POST /v1/hooks/{nom}` |
| **File de tâches** | Les runs à piloter ou à reprendre y sont posés ; un worker les prend. `asyncio` : dans le process même ; `rabbitmq` : dans un courtier partagé. | `storage.queue` |
| **Worker** | Process dont le seul métier est de consommer la file. | `loom worker` |
| **Bus** | Prévient les autres process qu'un journal partagé a reçu du neuf, pour que leurs flux (SSE, `follow`) restent en direct. Il **ne transporte pas** les événements : on les relit dans le journal. | `storage.bus` : `memory`, `postgres`, `redis` |
| **Magasin d'idempotence partagé** | Garde les clés métier de chapitre 19 visibles de tous les process. | `storage.idempotency` : `sqlite`, `postgres`, `redis` |

Chaque adaptateur est un extra à installer, et la configuration ne contient jamais l'adresse du service : seulement le **nom de la variable d'environnement** qui la porte (`dsn_env` pour Postgres, `url_env` pour Redis et RabbitMQ). Les extras `postgres`, `redis` et `rabbitmq` ont été installés en début de fichier ; sinon : `uv add "loom-ia[postgres,redis,rabbitmq]"`.

Une porte d'entrée se déclare ainsi :

| Champ de `triggers[]` | Effet |
|---|---|
| `name` | Nom dans l'URL : `POST /v1/hooks/<name>`. |
| `agent` | Agent lancé. |
| `message` | Texte envoyé à l'agent, avec des `{{ payload.… }}` tirés du corps JSON reçu. |
| `session` | Nom de la session (même mécanique de gabarit). Sans lui : une session par run. |
| `delivery_header` | En-tête HTTP qui porte l'identifiant de livraison de l'émetteur. |

Un *webhook* est livré **au moins une fois** : l'émetteur recommence quand il n'a pas reçu de réponse. D'où `delivery_header` : l'identifiant de livraison devient l'identifiant du run, et une relivraison retombe sur le run existant au lieu d'en ouvrir un second.

### Exemple 23.1 : le CRM signale un devis signé

**Pourquoi.** Le CRM de la Plomberie Dupont appelle une URL quand Mme Martin signe le devis D-2026-042. Il ne connaît ni l'API de Loom, ni ses agents, et il réessaie en cas de doute. Il faut que chaque signature produise **un** run, avec un message écrit par vous, et que la clé du CRM ne puisse rien faire d'autre.

**Objectif.** Déclarer deux portes d'entrée (`devis-signe`, appelée par le CRM, et `relance-matinale`, appelée par un planificateur), constater la relivraison dédoublonnée, et voir les refus.

**Mise en place.** On part de la configuration du chapitre 20 :

```bash
cp api.yaml hooks.yaml
sed -i 's#data-api/journal.db#data-hooks/journal.db#; s#port: 18301#port: 18308#' hooks.yaml
```

Deux clés, une par appelant, chacune limitée à un agent (chapitre 22) :

```bash
uv run loom --config hooks.yaml keys create crm-dupont --scope run --agent devis
uv run loom --config hooks.yaml keys create planificateur --scope run --agent relance
```

```text
Clé        : lk_INoaEOOPp_yAC6CxawsLbjJEJBO0JyiXZ70_2p7ub-o
Elle n'est affichée qu'ici : la config ne garde que son empreinte.

À ajouter dans la configuration :

security:
  api_keys:
    - id: crm-dupont
      hash: sha256:e67864559c6b38caa6d223aa0a3acf4da0fd4ff218955a97a6616813452b36ed
      scopes: [run]
      agents: [devis]
```

Votre clé et votre empreinte seront différentes des miennes : collez les vôtres. Ajoutez à la fin de `hooks.yaml` :

```yaml
security:
  api_keys:
    - id: crm-dupont
      hash: sha256:e67864559c6b38caa6d223aa0a3acf4da0fd4ff218955a97a6616813452b36ed
      scopes: [run]
      agents: [devis]
    - id: planificateur
      hash: sha256:016167d94470aa155e92ca35b738124d85661da9d4dbc6ce8644400762306164
      scopes: [run]
      agents: [relance]

triggers:
  - name: devis-signe
    agent: devis
    message: "Le devis {{ payload.devis.numero }} de {{ payload.client.nom }} vient d'être signé."
    session: "devis-{{ payload.devis.numero }}"
    delivery_header: X-Delivery-Id
  - name: relance-matinale
    agent: relance
    message: "Relance Mme Martin pour le devis D-2026-042, sur un ton cordial."
```

`loom validate` liste les portes :

```bash
uv run loom --config hooks.yaml validate | grep -A4 Portes
```

```text
Portes     : devis-signe, relance-matinale
    POST /v1/hooks/devis-signe → agent devis / session devis-{{ payload.devis.numero }}, livraison sur X-Delivery-Id
    POST /v1/hooks/relance-matinale → agent relance / session par run, aucun en-tête de livraison : une relivraison rouvre un run
```

La seconde ligne annonce déjà le piège de `relance-matinale`, qu'on verra plus bas.

**Exécution.** On démarre le serveur, puis on joue le CRM avec `curl` (les clés sont dans `$CRM` et `$PLAN`) :

```bash
uv run loom --config hooks.yaml serve &
B=http://127.0.0.1:18308
D='{"devis":{"numero":"D-2026-042"},"client":{"nom":"Mme Martin"}}'

# première livraison
curl -si -X POST $B/v1/hooks/devis-signe -H "Authorization: Bearer $CRM" \
  -H 'content-type: application/json' -H 'X-Delivery-Id: crm-evt-0001' -d "$D" | sed -n '1p;$p'
sleep 1
# le CRM n'a pas vu la réponse : il réessaie
curl -si -X POST $B/v1/hooks/devis-signe -H "Authorization: Bearer $CRM" \
  -H 'content-type: application/json' -H 'X-Delivery-Id: crm-evt-0001' -d "$D" | sed -n '1p;$p'
```

```text
HTTP/1.1 202 Accepted
{"trigger":"devis-signe","run_id":"crm-evt-0001","session_id":"devis-D-2026-042","status":"ready_for_model","repeated":false}
HTTP/1.1 200 OK
{"trigger":"devis-signe","run_id":"crm-evt-0001","session_id":"devis-D-2026-042","status":"completed","repeated":true}
```

La première livraison est **acceptée** (`202`) : le run existe, il tourne en arrière-plan, sous l'identifiant `crm-evt-0001` fourni par le CRM. La seconde est reconnue : `200`, `repeated: true`, et le statut du run existant (déjà terminé). Aucun second run n'est ouvert.

Ce que l'agent a reçu, lu dans le journal (la clé du CRM n'a que `run` : on lit avec la CLI) :

```bash
uv run loom --config hooks.yaml sessions export devis-D-2026-042 \
  | jq -c 'select(.type=="run.started") | .payload.trigger'
uv run loom --config hooks.yaml sessions export devis-D-2026-042 \
  | jq -r 'select(.type=="message.user") | .payload.message.blocks[0].text'
```

```text
"devis-signe"
Le devis D-2026-042 de Mme Martin vient d'être signé.
```

Le message est celui de **votre** gabarit, rempli avec le corps reçu, et le run porte le nom de sa porte (`trigger`) : on peut retrouver plus tard tous les runs venus du CRM. L'appelant n'a décidé ni de l'agent, ni de la consigne.

Maintenant, ce qui tourne mal. Un corps incomplet (pas de nom de client), sans en-tête de livraison :

```bash
curl -s -X POST $B/v1/hooks/devis-signe -H "Authorization: Bearer $CRM" \
  -H 'content-type: application/json' -d '{"devis":{"numero":"D-2026-077"},"client":{}}' | jq -c '{session_id,status}'
uv run loom --config hooks.yaml sessions export devis-D-2026-077 \
  | jq -r 'select(.type=="message.user") | .payload.message.blocks[0].text'
```

```text
{"session_id":"devis-D-2026-077","status":"ready_for_model"}
Le devis D-2026-077 de  vient d'être signé.
```

Une variable absente du corps devient une **chaîne vide** : le run démarre quand même, avec une phrase trouée. Loom ne peut pas deviner si l'absence est une erreur ; c'est à votre gabarit de ne pas dépendre de champs facultatifs, ou à l'agent de savoir réagir à un message incomplet.

Les refus :

```bash
curl -s -o /dev/null -w "porte inconnue : %{http_code}\n" -X POST $B/v1/hooks/inconnue \
  -H "Authorization: Bearer $CRM" -H 'content-type: application/json' -d '{}'
curl -s -o /dev/null -w "sans clé : %{http_code}\n" -X POST $B/v1/hooks/devis-signe \
  -H 'content-type: application/json' -d "$D"
curl -s -w " -> %{http_code}\n" -X POST $B/v1/hooks/relance-matinale \
  -H "Authorization: Bearer $CRM" -H 'content-type: application/json' -d '{}'
curl -s -w " -> %{http_code}\n" -X POST $B/v1/hooks/relance-matinale \
  -H "Authorization: Bearer $PLAN" -H 'content-type: application/json' -d '{}'
```

```text
porte inconnue : 404
sans clé : 401
{"detail":"Clé non autorisée sur l'agent 'relance'"} -> 403
{"trigger":"relance-matinale","run_id":"01a1269d-ef54-7397-a2f0-ec8f76848c1e","session_id":"01a1269d-ef54-7397-a2f0-ec8f76848c1e","status":"ready_for_model","repeated":false} -> 202
```

La clé du CRM, limitée à l'agent `devis`, ne peut pas déclencher la relance : c'est la même règle que pour les autres routes du chapitre 22, appliquée à l'agent de la porte. La clé du planificateur y arrive. Une porte inconnue répond `404` (et le message nomme les portes déclarées), et le serveur ne lance rien sans clé.

Le même déclenchement existe sans HTTP, quand votre code est déjà dans le même process que Loom. `declencher.py` :

```python
import asyncio

from loom_ia.access import Loom

CHARGE = {"devis": {"numero": "D-2026-042"}, "client": {"nom": "Mme Martin"}}


async def main() -> None:
    async with Loom.from_config("hooks.yaml") as loom:
        for essai in (1, 2):
            livre = await loom.trigger("devis-signe", CHARGE, delivery_id="crm-evt-0002")
            print(f"livraison {essai} : run {livre.run_id}, répétée = {livre.repeated}, statut = {livre.status.value}")
            await loom.drain()
        resultat = await loom.result(livre.run_id, session_id=livre.session_id)
        print("réponse :", resultat.text)


asyncio.run(main())
```

```bash
uv run python declencher.py
```

```text
livraison 1 : run crm-evt-0002, répétée = False, statut = ready_for_model
livraison 2 : run crm-evt-0002, répétée = True, statut = completed
réponse : Le devis D-2026-042 de Mme Martin (remplacement du chauffe-eau) s'élève à 1 840 € TTC, envoyé le 3 septembre.
```

`Loom.trigger(nom, charge, delivery_id=…)` fait le même travail que la route, sans le réseau. `drain()` attend la fin des runs lancés en arrière-plan par ce process.

**À retenir.**

- Une porte est un **contrat figé** : l'appelant fournit des *données* (le corps JSON) ; l'agent, le message et la session sont fixés par la configuration. C'est ce qui permet de donner une clé à un système que vous ne maîtrisez pas.
- La relivraison n'est dédoublonnée que si l'émetteur envoie un identifiant (`delivery_header`) : le run prend cet identifiant pour nom. Sans lui, **chaque appel ouvre un run** (c'est ce que `validate` dit pour `relance-matinale`). Si l'émetteur ne sait pas fournir d'identifiant, bornez les dégâts d'une relivraison avec une clé métier (chapitre 19), pas avec l'espoir qu'elle n'arrive pas.
- Loom n'a **pas de cron**. `relance-matinale` attend qu'un planificateur extérieur l'appelle. Sur le serveur, une ligne de crontab suffit :

  ```text
  30 7 * * 1-5  curl -fsS -X POST http://127.0.0.1:8000/v1/hooks/relance-matinale -H "Authorization: Bearer $LOOM_PLAN_KEY" -H 'content-type: application/json' -d '{}'
  ```

  Non exécuté ici (il aurait fallu attendre 7 h 30) : la commande `curl` est celle qu'on vient de jouer à la main. Retenez la règle : ce qui se déclenche à heure fixe se met à l'heure par la plateforme (cron, planificateur de tâches du cloud, GitHub Actions), jamais par Loom.
- Une porte qui lance une relance avec envoi d'e-mail s'arrêtera sur l'approbation demandée (chapitre 9) comme n'importe quel run : `202` veut dire « pris en charge », pas « e-mail parti ».
- **Piège.** `repeated: true` ne dit pas que le premier appel a réussi, seulement qu'il a eu lieu : lisez aussi le `status`.

### Exemple 23.2 : une file de tâches et des workers

**Pourquoi.** Tant qu'un seul process fait tout, la file de tâches est un détail interne (`asyncio`). Mais dès que le serveur HTTP doit rester réactif pendant que des runs de dix minutes tournent, ou qu'un worker mort doit être remplacé par un autre, il faut une file **hors du process**.

**Objectif.** Comprendre la répartition des rôles avec RabbitMQ, voir ce que Loom contrôle au chargement, et constater le refus de `loom worker` quand la file n'est pas un courtier.

**Mise en place.** `rabbit.yaml` est `service.yaml` (chapitre 19) dont on remplace la section `storage` par des services partagés, tous désignés par des variables d'environnement :

```bash
python3 - <<'EOF'
s = open("service.yaml").read()
s = s[: s.index("storage:")] + """storage:
  events: {backend: postgres, dsn_env: LOOM_PG}
  artifacts: {backend: local, path: /srv/loom/artefacts}
  idempotency: {backend: postgres, dsn_env: LOOM_PG}
  queue: {backend: rabbitmq, url_env: LOOM_AMQP}
  bus: {backend: redis, url_env: LOOM_REDIS}

execution:
  lease: 60
"""
open("rabbit.yaml", "w").write(s)
EOF
```

Les rôles :

- `loom serve` (un ou plusieurs) reçoit les requêtes et **met en file** ;
- `loom worker` (un ou plusieurs, `--jobs N` pour plusieurs tâches de front) consomme la file et **pilote** les runs ;
- le journal Postgres, l'idempotence Postgres, le bus Redis sont communs.

**Exécution.** Je n'ai pas de RabbitMQ sur ma machine de travail : cette partie n'est donc **pas exécutée de bout en bout**. Voici ce que j'ai pu vérifier, c'est-à-dire tout ce que Loom contrôle avant de parler au courtier. Sans la variable d'URL :

```bash
env -u LOOM_AMQP uv run loom --config rabbit.yaml validate | tail -3
```

```text
    Stockage : file servie par un courtier et artefacts 'local' (/srv/loom/artefacts) — les process doivent partager ce dossier, sinon un run repris ailleurs ne retrouvera ni ses pièces jointes ni ses résultats déportés
Clés d'API : aucune (API REST ouverte)
Configuration : File 'rabbitmq' : la variable 'LOOM_AMQP' est vide ou absente — c'est elle qui porte l'URL du courtier
```

Avec les variables renseignées, `validate` décrit l'architecture sans se connecter à rien :

```bash
export LOOM_PG=postgresql://loom_login@127.0.0.1:15432/loom_prod
export LOOM_AMQP=amqp://guest:guest@127.0.0.1:5672/
export LOOM_REDIS=redis://127.0.0.1:16379/0
uv run loom --config rabbit.yaml validate | grep -E "^(Journal|Artefacts|Idempotence|File|Bus)"
```

```text
Journal    : postgres (DSN dans LOOM_PG : renseignée, rôle : loom_app)
Artefacts  : local (/srv/loom/artefacts)
Idempotence: postgres (DSN dans LOOM_PG : renseignée, rôle : loom_app)
File       : rabbitmq (URL dans LOOM_AMQP : renseignée)
Bus        : redis (LOOM_REDIS : renseignée)
```

(Cet affichage vaut pour la lecture ; voir plus bas le piège du `validate` qui ne rend pas la main avec un bus Redis.) Et un worker lancé sur une file qui n'est pas un courtier est refusé, ce que j'ai pu jouer :

```bash
uv run loom --config service.yaml worker 2>&1 | tail -1
```

```text
Configuration : Un worker demande une file servie par un courtier ('storage.queue.backend: rabbitmq'), pas 'asyncio' — qui exécute déjà ses tâches dans le process qui les met en file
```

Ce qui est décrit dans les sources et que je n'ai donc pas vérifié :

- au démarrage, un worker **reprend** ce qui traînait (`recover()`, chapitre 19) avant de consommer ;
- la livraison est **au moins une fois** : le courtier ne retire une tâche qu'une fois exécutée, donc une tâche d'un worker tué est redonnée à un autre ;
- une tâche redonnée alors que le bail du mort court encore (`execution.lease`) est reposée pour l'après-bail, jusqu'à vingt attentes ;
- `SIGINT` / `SIGTERM` : plus aucune tâche n'est prise, les tâches en cours vont au bout.

**À retenir.**

- Avec un courtier, `loom serve` **ne pilote plus**, il met en file. Un serveur sans worker derrière laisse tout attendre : `validate` le dit sous la ligne `File`.
- Les **fichiers** ne traversent pas les process : le journal, le bus et la file le font, pas les artefacts du chapitre 18. Avec plusieurs process, un dossier `local` doit être un volume partagé (`validate` avertit, sans pouvoir deviner si c'est le cas) ; `memory` ne sort jamais du process.
- La file `asyncio` n'a pas de worker : `loom worker` la refuse, c'est le comportement voulu.
- **Piège.** Au moment où j'ai testé (2.0.0), `loom worker` sur une file `asyncio` imprimait d'abord « En écoute, 1 tâche(s) de front » avant d'afficher le refus : seule la dernière ligne compte.

### Exemple 23.3 : Redis pour l'idempotence et le bus

**Pourquoi.** Deux process (deux `loom serve`) partagent le même journal. Un clic en double dans une application ne doit pas envoyer deux relances, quel que soit le process qui reçoit le clic. Et un client qui suit un run sur le process B doit voir les événements du run piloté par le process A.

**Objectif.** Utiliser Redis comme magasin d'idempotence (clé métier du chapitre 19) puis comme bus, et constater ce qui marche et ce qui ne marche pas en 2.0.0.

**Mise en place.** Redis tourne sur le port 16379 de ma machine (`redis-server --port 16379`). `redis-idem.yaml` est `service.yaml` avec un autre stockage :

```yaml
storage:
  events: {backend: sqlite, path: data-redis/journal.db}
  idempotency: {backend: redis, url_env: LOOM_REDIS}

execution:
  lease: 5
```

(Le reste du fichier est celui de `service.yaml` du chapitre 19.) On rejoue `doublon.py` : deux sessions différentes, même devis, approbateur en ligne.

**Exécution.**

```bash
export LOOM_REDIS=redis://127.0.0.1:16379/0
redis-cli -p 16379 flushall
rm -rf data/relances.log
uv run python doublon.py          # avec "redis-idem.yaml" à la place de "service.yaml"
cat data/relances.log
redis-cli -p 16379 keys '*'
```

```text
clic-1 : completed -> La relance du devis D-2026-042 a été traitée.
clic-2 : completed -> La relance du devis D-2026-042 a été traitée.
D-2026-042 -> mme.martin@example.fr
loom:idem:o:default:clic-1
loom:idem:k:default:relance:D-2026-042
```

Une seule ligne dans `relances.log` : comme avec SQLite au chapitre 19, la seconde session a reçu le résultat de la première. La clé métier est dans Redis, préfixée par le client (`default` ici) ; l'autre clé garde le résultat technique du run `clic-1`. N'importe quel process qui parle à ce Redis la voit.

Passons au bus. `bus-redis.yaml` : `service.yaml` avec

```yaml
storage:
  events: {backend: sqlite, path: data-bus/journal.db}
  bus: {backend: redis, url_env: LOOM_REDIS}
  idempotency: {backend: sqlite, path: data-bus/cles.db}

server:
  http: {host: 127.0.0.1, port: 18309}
```

Le process A pilote ; on suit le run depuis un autre process Python avec `suivre_bus.py`, qui écrit l'heure d'arrivée de chaque événement :

```python
import asyncio
import sys
import time

from loom_ia.access import Loom

BRUIT = {"step.started", "step.completed", "run.transitioned", "run.claimed", "message.user"}


async def main(config: str, run_id: str, session: str) -> None:
    t0 = time.monotonic()
    async with Loom.from_config(config) as loom:      # ouvrir le contexte met l'oreille sur le bus
        async for event in loom.follow(run_id, session_id=session):
            if event.type not in BRUIT:
                print(f"{time.monotonic() - t0:5.1f} s  {event.type}", flush=True)
            if event.type == "approval.requested":
                break


asyncio.run(main(*sys.argv[1:]))
```

L'agent `relance` de ce projet consulte une agenda qui prend 8 secondes (`consulter_agenda`, chapitre 19), ce qui rend les arrivées visibles :

```bash
uv run loom --config bus-redis.yaml serve &            # process A, port 18309
B=http://127.0.0.1:18309
R=$(curl -s -X POST $B/v1/agents/relance/runs -H 'content-type: application/json' \
  -d '{"message":"Relance Mme Martin pour le devis D-2026-042.","session_id":"bus2","background":true}' | jq -r .run_id)
uv run python suivre_bus.py bus-redis.yaml $R bus2     # process B
```

```text
  0.1 s  run.started
  0.1 s  model.responded
  0.1 s  tool.called
  0.1 s  tool.completed
  0.1 s  model.responded
  0.1 s  tool.called
  7.5 s  tool.completed
  7.6 s  model.responded
  7.6 s  approval.requested
```

Le process B, qui n'a **rien à voir** avec le process A, reçoit en direct `tool.completed` à 7,5 s (la fin de l'agenda) et l'approbation demandée. Avec `bus: memory` (le défaut), `validate` le dit : « les nouvelles ne sortent pas de ce process ».

Reste le chemin le plus courant : un client SSE branché sur un **second `loom serve`**. Je lance un process B sur le port 18310 avec la même configuration (même journal, même bus), puis je suis le run du process A à travers lui :

```bash
uv run loom --config bus-redis.yaml serve --port 18310 &       # process B
R=$(curl -s -X POST $B/v1/agents/relance/runs -H 'content-type: application/json' \
  -d '{"message":"Relance Mme Martin pour le devis D-2026-042.","session_id":"bus3","background":true}' | jq -r .run_id)
curl -s -N --max-time 14 "http://127.0.0.1:18310/v1/runs/$R/events?session_id=bus3" | grep "^event:"
```

```text
event: run.started
event: model.responded
event: tool.called
event: tool.completed
event: model.responded
event: tool.called
```

Le flux s'arrête là : il montre ce qui était **déjà écrit** quand le client s'est connecté (0,1 s), puis plus rien, ni la fin de l'agenda à 8 s, ni la demande d'approbation. Le run, lui, continue bien dans A (`GET /v1/runs/$R?session_id=bus3` sur A répond `paused`).

**À retenir.**

- Redis (ou Postgres) comme **magasin d'idempotence** marche comme annoncé : une clé métier est vue de tous les process. `postgres`, `redis` et `sqlite` (fichier partagé) sont les trois choix d'un service ; `journal` et `memory` ne conviennent qu'à un process.
- Le bus ne transporte pas les événements ; il dit « du neuf dans tel journal ». Qui l'écoute relit le journal. Un bus en panne ne fait donc jamais échouer une écriture : on perd une notification, jamais un événement, et le suivi rattrape à la notification suivante.
- **Écart avec la documentation (2.0.0).** Dans mes essais, c'est `async with Loom.from_config(…)` qui met l'oreille sur le bus (premier essai, process B en direct), alors que `loom serve` ne le fait pas : le flux SSE d'un second `loom serve` reste figé sur ce qui était écrit à la connexion. J'ai obtenu le même résultat avec `bus: memory` et avec `bus: redis`. Les sources de 2.0.1 ont le même code côté `serve`. En pratique : pour suivre en direct, passez par un seul serveur (les clients SSE se connectent à celui qui pilote), ou rouvrez la connexion avec `Last-Event-ID` / `?after_seq=` (chapitre 20), qui relit le journal et, lui, ne rate rien.
- **Piège.** `loom validate` avec un bus Redis affiche toute sa sortie puis **ne rend pas la main** (2.0.0) ; il faut l'interrompre avec Ctrl-C. `loom run` et `loom serve` ne sont pas touchés. Avec un bus `postgres` (`bus: {backend: postgres, dsn_env: LOOM_PG}`, qui utilise `LISTEN/NOTIFY`), `validate` se termine normalement et affiche `Bus : postgres (LOOM_PG : renseignée)` ; je n'ai pas rejoué le suivi inter-process avec Postgres.
- La **variable manquante** est dite en clair : `Configuration : Bus 'redis' : la variable 'LOOM_REDIS' est vide ou absente — c'est elle qui porte l'URL`.

---
## 24. Données sensibles : isolation en base, chiffrement, rétention et RGPD

### Ce qu'il faut comprendre avant de commencer

Les journaux de Loom contiennent tout : le devis de Mme Martin, son adresse e-mail, les arguments des outils, les réponses du modèle. Dès qu'on sert plusieurs entreprises (chapitre 22), la question n'est plus « que se passe-t-il si le code se trompe ? » mais « que se passe-t-il quand quelqu'un lit la base directement, quand une sauvegarde fuit, quand un client demande l'effacement de ses données ? ». Loom répond à ces questions par quatre mécanismes **de stockage** :

| Besoin | Mécanisme | Réglage |
|---|---|---|
| Un client ne lit jamais le journal d'un autre, même si le code se trompe | Sécurité au niveau des lignes (RLS) de Postgres, plus un rôle applicatif sans droit de modification | `storage.events: {backend: postgres, dsn_env: …}` |
| Une base ou un disque volé ne livre rien | Chiffrement AES-256-GCM du contenu des événements et des fichiers | `storage.encryption: {keys: [NOM_SECRET]}` |
| Un client part : ses données doivent disparaître | Effacement d'une session (journal, fichiers, clés d'idempotence), et *crypto-shredding* | `loom sessions delete`, effacement de la clé du client |
| Garder moins longtemps | Rétention : effacer les sessions dormantes | `storage.retention.events_days` |

Ce qui n'est **pas** dans ce chapitre : ce qui entre dans le journal au départ (niveaux de `capture`, masquage des champs sensibles) se règle à l'écriture et est traité au chapitre 25 du fichier suivant. Ici, on protège et on efface ce qui a été écrit.

### Exemple 24.1 : le journal en Postgres, une base qui isole les clients

**Pourquoi.** Au chapitre 22, Dupont et Martin avaient chacun un `WHERE tenant_id = …` dans les requêtes de Loom. Si un jour une requête l'oublie, ou si un script d'exploitation lit la table directement, un client voit l'autre. Avec Postgres, on déplace la barrière **dans la base**.

**Objectif.** Passer le journal des deux clients en Postgres, en préparant la base comme le ferait un administrateur prudent, puis tenter de lire et d'écrire en dehors des règles.

**Mise en place.** Postgres tourne sur ma machine (port 15432, en confiance locale : en vrai, chaque rôle a un mot de passe). Trois rôles, une base :

```bash
psql -h /tmp -p 15432 -U postgres \
  -c "create role dba login createrole" \
  -c "create role loom_login login" \
  -c "create database loom_prod owner dba"
```

- `dba` : celui qui **crée** le schéma et qui possède les tables (c'est lui, et pas Loom, qui a le droit de créer des rôles) ;
- `loom_login` : la connexion de l'application ;
- `loom_app` : le rôle sous lequel Loom travaille, que `loom_login` prendra à chaque connexion. Il n'existe pas encore : le script de Loom le crée.

Le fichier `pg.yaml` est `clients.yaml` (chapitre 22) dont on retire le stockage particulier de Martin et dont on change le journal :

```bash
cp clients.yaml pg.yaml
```

À la fin de `pg.yaml`, la section `tenants` et le stockage deviennent :

```yaml
tenants:
  - id: dupont-plomberie
    variables: {entreprise: Plomberie Dupont, signature: "L'équipe Dupont"}
  - id: martin-chauffage
    variables: {entreprise: Chauffage Martin, signature: "Chauffage Martin, service client"}
    models: {PRINCIPAL: ECONOMIQUE}
    approvals: {chercher_devis: always}

  - id: comptable-dupont
    variables: {entreprise: Plomberie Dupont (comptabilité), signature: "Cabinet comptable"}
    agents: [devis]

telemetry:
  logging: {level: WARNING}

storage:
  events: {backend: postgres, dsn_env: LOOM_PG}
  artifacts: {backend: local, path: data-pg/artefacts}
```

Deux détails. La configuration nomme la **variable** `LOOM_PG`, pas l'adresse. Et `storage.artifacts` est obligatoire dès que le journal est Postgres : Postgres ne fournit pas de dossier, et un défaut silencieux aurait laissé les fichiers en mémoire sous un journal durable.

Le script `deux_clients_pg.py` est `deux_clients.py` du chapitre 22 qui lit `pg.yaml` :

```bash
sed 's/clients.yaml/pg.yaml/' deux_clients.py > deux_clients_pg.py
```

**Exécution.** D'abord, la tentative naïve : l'application se connecte avec `loom_login`, qui n'a pas le droit de préparer la base.

```bash
export LOOM_PG=postgresql://loom_login@127.0.0.1:15432/loom_prod
uv run python deux_clients_pg.py 2>&1 | tail -2
```

```text
loom_ia.adapters.postgres.pool.PostgresNotPrepared: Base Postgres non préparée : le rôle 'loom_login' n'a pas pu poser le schéma de loom ('loom_events' et ce qui va avec). Postgres a dit : « permission denied to create role
DETAIL:  Only roles with the CREATEROLE attribute may create roles. ». Appliquer la sortie de 'loom storage sql' avec un rôle qui en a le droit.
```

Le message dit quoi faire. Loom sait imprimer le SQL dont il a besoin :

```bash
uv run loom --config pg.yaml storage sql | tee loom.sql
```

```sql
DO $$ BEGIN
    CREATE ROLE loom_app NOLOGIN;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
GRANT loom_app TO CURRENT_USER;
CREATE TABLE IF NOT EXISTS loom_events (
    tenant_id   text        NOT NULL,
    session_id  text        NOT NULL,
    seq         bigint      NOT NULL,
    event_id    text        NOT NULL,
    ts          timestamptz NOT NULL,
    run_id      text        NOT NULL,
    root_run_id text        NOT NULL,
    type        text        NOT NULL,
    category    text        NOT NULL,
    status      text        NOT NULL,
    agent       text,
    role        text,
    facets      jsonb       NOT NULL,
    event       text        NOT NULL,
    PRIMARY KEY (tenant_id, session_id, seq)
);
CREATE INDEX IF NOT EXISTS loom_events_run
    ON loom_events (tenant_id, run_id, seq);
CREATE INDEX IF NOT EXISTS loom_events_event_id
    ON loom_events (tenant_id, event_id);
CREATE INDEX IF NOT EXISTS loom_events_type
    ON loom_events (tenant_id, type, ts);
CREATE INDEX IF NOT EXISTS loom_events_facets
    ON loom_events USING gin (facets);
ALTER TABLE loom_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE loom_events FORCE ROW LEVEL SECURITY;
DO $$ BEGIN
    CREATE POLICY loom_events_tenant ON loom_events
        USING (tenant_id = current_setting('loom.tenant_id', true))
        WITH CHECK (tenant_id = current_setting('loom.tenant_id', true));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
GRANT SELECT, INSERT, DELETE ON loom_events TO loom_app;
```

À lire en trois temps :

1. `ENABLE` puis `FORCE ROW LEVEL SECURITY` et la politique : une ligne n'est visible, ou écrivable, que si son `tenant_id` est égal au réglage `loom.tenant_id` de la transaction. `FORCE` étend la règle au **propriétaire** de la table.
2. `GRANT SELECT, INSERT, DELETE` à `loom_app` : **pas d'`UPDATE`**. Le journal est immuable par privilège, pas par politesse du code. `DELETE` reste accordé, c'est l'effacement RGPD.
3. Loom pose `loom.tenant_id` à chaque transaction avec le client de la requête.

On applique avec le rôle `dba`, puis on autorise la connexion de l'application à prendre le rôle `loom_app` :

```bash
psql -h /tmp -p 15432 -U dba loom_prod -f loom.sql
psql -h /tmp -p 15432 -U dba loom_prod -c "grant loom_app to loom_login"
```

```text
DO
GRANT ROLE
CREATE TABLE
CREATE INDEX
CREATE INDEX
CREATE INDEX
CREATE INDEX
ALTER TABLE
ALTER TABLE
DO
GRANT
GRANT ROLE
```

Cette fois, les deux clients tournent :

```bash
uv run python deux_clients_pg.py
```

```text
dupont-plomberie   completed  0.009285 $  La relance du devis D-2026-042 est partie chez Mme Martin.
martin-chauffage   completed  0.002487 $  La relance du devis D-2026-042 est partie chez Mme Martin.
dupont-plomberie   jour : 0.009285 $ (1 run) ; sessions : ['relance-042']
martin-chauffage   jour : 0.002487 $ (1 run) ; sessions : ['relance-042']
```

Identique au chapitre 22, avec une base qui garde les deux journaux dans une table. Les chiffres du coût sont les mêmes à la décimale près : seul le stockage a changé.

Maintenant, on joue l'attaquant. Cinq essais, avec `psql`, directement sur la base :

```bash
Q="psql -h /tmp -p 15432 -U dba loom_prod"

echo "--- 1. le propriétaire lit, sans dire pour quel client"
$Q -c "select count(*) from loom_events"

echo "--- 2. il lit en se déclarant Dupont"
$Q -c "begin; select set_config('loom.tenant_id','dupont-plomberie',true);
       select tenant_id, count(*) from loom_events group by 1; commit;"

echo "--- 3. déclaré Dupont, il tente d'écrire une ligne au nom de Martin"
$Q -c "begin; select set_config('loom.tenant_id','dupont-plomberie',true);
       insert into loom_events select 'martin-chauffage', session_id, 999, event_id, ts, run_id,
         root_run_id, type, category, status, agent, role, facets, event from loom_events limit 1;
       rollback;"

echo "--- 4. le rôle de l'application tente de modifier le journal"
psql -h /tmp -p 15432 -U loom_login loom_prod \
  -c "set role loom_app; select set_config('loom.tenant_id','martin-chauffage',false);
      update loom_events set status='x'"
```

```text
--- 1. le propriétaire lit, sans dire pour quel client
 count 
-------
     0
(1 row)

--- 2. il lit en se déclarant Dupont
     tenant_id     | count 
------------------+-------
 dupont-plomberie |    28
(1 row)

--- 3. déclaré Dupont, il tente d'écrire une ligne au nom de Martin
ERROR:  new row violates row-level security policy for table "loom_events"
--- 4. le rôle de l'application tente de modifier le journal
ERROR:  permission denied for table loom_events
```

(Les sorties sont raccourcies : j'ai retiré les lignes `BEGIN`, `set_config` et `COMMIT` de `psql`.) Une base qui contient 58 lignes en montre **0** à qui ne dit pas pour quel client il lit, 28 à Dupont (ses 28 événements, pas les 30 de Martin), refuse l'écriture au nom d'un autre, et refuse toute modification au rôle de l'application.

Dernier point, qui surprend toujours le jour où on veut une sauvegarde :

```bash
pg_dump -h /tmp -p 15432 -U dba -t loom_events --data-only loom_prod > /dev/null
pg_dump -h /tmp -p 15432 -U dba --enable-row-security -t loom_events --data-only loom_prod | grep -c "^dupont\|^martin"
```

```text
pg_dump: error: query failed: ERROR:  query would be affected by row-level security policy for table "loom_events"
HINT:  To disable the policy for the table's owner, use ALTER TABLE NO FORCE ROW LEVEL SECURITY.
pg_dump: detail: Query was: COPY public.loom_events (tenant_id, session_id, seq, event_id, ts, run_id, root_run_id, type, category, status, agent, role, facets, event) TO stdout;
0
```

**À retenir.**

- Trois protections superposées : la politique de lignes (le `WHERE` de Loom n'est plus ce qui protège), `FORCE` (même le propriétaire est soumis), le rôle `loom_app` sans `UPDATE` (le journal ne se réécrit pas).
- Le schéma se pose **une fois, par un rôle qui en a le droit**. Loom le fait seul quand sa connexion le peut ; sinon, `loom storage sql` imprime ce qu'il faut faire appliquer, et le message d'erreur y renvoie. Le script est rejouable.
- **Piège, la sauvegarde.** Une sauvegarde logique (`pg_dump`) échoue (message ci-dessus). Pire : avec `--enable-row-security`, elle réussit mais **ne contient aucune ligne**, sans prévenir (`0` ci-dessus). Il faut un rôle `BYPASSRLS` réservé à l'exploitation, ou une sauvegarde physique (`pg_basebackup`, instantanés du disque), qui n'est pas concernée par les politiques. Testez la **restauration** avant d'en avoir besoin.
- **Piège, l'oubli est silencieux, et c'est voulu** : lire sans dire pour quel client donne un journal vide, jamais celui d'un autre (essai 1). Si un script d'exploitation « ne trouve rien », c'est qu'il n'a pas posé `loom.tenant_id`.
- Ce que la politique ne couvre pas, et que le suivi du projet garde ouvert en 2.0.x : la **table d'idempotence** n'a pas de politique de lignes, parce qu'une clé se relit sans nommer de client (#021 ; ce qui la cadre, c'est le préfixe par client de la clé) ; et une **empreinte de requête** (`request_hash`) peut être commune à deux clients quand leurs premières requêtes sont identiques octet pour octet, sans fuite de contenu (#019).
- Deux clients peuvent aussi avoir chacun leur **propre base** : un client déclare son propre `storage.events` avec son `dsn_env` (isolation physique, chapitre 22). La politique de lignes est alors facultative.
- Non exécuté ici : l'idempotence en Postgres (`idempotency: {backend: postgres, dsn_env: LOOM_PG}`), dont `validate` confirme la déclaration (chapitre 23) mais pour laquelle je n'ai pas joué de doublon.

### Exemple 24.2 : chiffrer le journal, une clé par client

**Pourquoi.** Une politique de lignes protège la base contre ceux qui l'interrogent. Elle ne protège pas contre un disque emporté, une sauvegarde copiée, un administrateur trop curieux. Chauffage Martin veut de plus pouvoir quitter le service en emportant la garantie que ses données sont **illisibles pour de bon**.

**Objectif.** Sceller le contenu des journaux avec une clé par client, constater que les fichiers ne contiennent plus rien de lisible, puis retirer la clé de Martin et voir ce qui reste possible.

**Mise en place.** Il faut l'extra `crypto` (installé en début de fichier).

`scelle.yaml` est encore `clients.yaml` sans le troisième client ni le stockage JSONL de Martin. Les clients redirigent le **nom** de secret vers leur propre variable :

```yaml
tenants:
  - id: dupont-plomberie
    variables: {entreprise: Plomberie Dupont, signature: "L'équipe Dupont"}
    secrets: {LOOM_JOURNAL_KEY: DUPONT_JOURNAL_KEY}
  - id: martin-chauffage
    variables: {entreprise: Chauffage Martin, signature: "Chauffage Martin, service client"}
    models: {PRINCIPAL: ECONOMIQUE}
    approvals: {chercher_devis: always}
    secrets: {LOOM_JOURNAL_KEY: MARTIN_JOURNAL_KEY}

telemetry:
  logging: {level: WARNING}

storage:
  events: {backend: sqlite, path: data-scelle/journal.db}
  artifacts: {backend: local, path: data-scelle/artefacts}
  encryption: {keys: [LOOM_JOURNAL_KEY]}
```

La clé ne figure jamais dans la configuration : `keys` nomme un **secret**, et chaque client redirige ce nom vers sa variable d'environnement. Une clé est 32 octets aléatoires en base64 :

```bash
export DUPONT_JOURNAL_KEY=$(openssl rand -base64 32)
export MARTIN_JOURNAL_KEY=$(openssl rand -base64 32)
sed 's/clients.yaml/scelle.yaml/' deux_clients.py > deux_clients_scelle.py
```

**Exécution.** `validate` montre ce qui ferme, et avec quelle clé (par empreinte, jamais la clé) :

```bash
uv run loom --config scelle.yaml validate | grep -A3 Chiffrement
```

```text
Chiffrement: charges et fichiers scellés — secret 'LOOM_JOURNAL_KEY' ferme
    dupont-plomberie : clé c3b22dd4c01b
    martin-chauffage : clé 85d798a1008a
```

(Vos empreintes seront différentes.) On joue les deux relances, puis on regarde ce qu'il y a **dans** la base :

```bash
rm -rf data-scelle
uv run python deux_clients_scelle.py
```

```text
dupont-plomberie   completed  0.009285 $  La relance du devis D-2026-042 est partie chez Mme Martin.
martin-chauffage   completed  0.002487 $  La relance du devis D-2026-042 est partie chez Mme Martin.
dupont-plomberie   jour : 0.009285 $ (1 run) ; sessions : ['relance-042']
martin-chauffage   jour : 0.002487 $ (1 run) ; sessions : ['relance-042']
```

```bash
uv run python - <<'EOF'
import json, sqlite3
c = sqlite3.connect("data-scelle/journal.db")
e = c.execute("select event from events where tenant_id='martin-chauffage' and seq=5").fetchone()[0]
d = json.loads(e)
for k in ("type", "tenant_id", "session_id", "agent", "seq", "payload"):
    v = str(d[k]); print(f"{k:10} {v if len(v) < 70 else v[:62] + '…'}")
EOF
echo "occurrences de « Mme Martin » dans le fichier :"
printf "  scellé   : "; grep -a -c "Mme Martin" data-scelle/journal.db
printf "  en clair : "; grep -a -c "Mme Martin" data-clients/journal.db
```

```text
type       model.responded
tenant_id  martin-chauffage
session_id relance-042
agent      relance
seq        5
payload    {'sealed': 'W1SuJLQCDa/v6pLKcIXPrSrQGztupo5mJ9nJzec+LJuIMKmV…
occurrences de « Mme Martin » dans le fichier :
  scellé   : 0
  en clair : 3
```

(`data-clients/journal.db` est le journal en clair du chapitre 22.) L'**enveloppe** reste en clair, la **charge** est un bloc scellé. On voit le type, le client, la session, l'agent : c'est ce qui permet de filtrer et de lister un journal scellé comme un autre. On ne voit ni ce que le modèle a répondu, ni les arguments des outils.

Maintenant, Chauffage Martin part et fait effacer sa clé. On relance les commandes **sans** `MARTIN_JOURNAL_KEY` (la variable n'existe plus) :

```bash
unset MARTIN_JOURNAL_KEY
uv run loom --config scelle.yaml sessions list --tenant martin-chauffage 2>/dev/null
uv run loom --config scelle.yaml sessions export relance-042 --tenant martin-chauffage 2>&1 | tail -1
uv run loom --config scelle.yaml sessions export relance-042 --tenant dupont-plomberie --out dupont.jsonl 2>/dev/null
```

```text
relance-042                  30 événements   2026-10-10 18:19
loom_ia.core.ports.cipher.MissingKey: Client 'martin-chauffage' : aucune clé de sceau — le secret 'LOOM_JOURNAL_KEY' est absent ou vide. Son journal reste illisible et ses runs sont refusés : c'est l'effet voulu après un effacement de clé, et si la clé n'était pas censée disparaître, c'est elle qu'il faut remettre ; la charge à ouvrir est scellée par '10ed344fb6f0'
28 événements écrits dans dupont.jsonl
```

- La session de Martin se **liste** encore (30 événements) : lister ne lit que l'enveloppe.
- Elle ne se **lit** plus : l'export est refusé, avec le nom du client et l'empreinte de la clé manquante.
- Dupont n'est pas touché : sa clé est là.

Et pour finir le travail, on peut supprimer la session de Martin **sans la clé**, puisque la suppression n'a pas besoin du contenu :

```bash
uv run loom --config scelle.yaml sessions delete relance-042 --tenant martin-chauffage --yes 2>/dev/null
```

```text
Session relance-042 supprimée : 30 événement(s), 0 fichier(s), 0 clé(s).
```

**À retenir.**

- Le chiffrement est un **scellé de ligne** (AES-256-GCM) : la charge de chaque événement et les octets de chaque fichier d'artefact. L'enveloppe (identifiants, horodatage, type, statut, agent, `seq`) reste en clair pour que le journal reste filtrable.
- Le scellé est lié à sa place (client, session, rang, identifiant de l'événement) : une charge copiée ailleurs dans la base ne s'ouvre pas.
- **Une clé par client** (`secrets: {LOOM_JOURNAL_KEY: MARTIN_JOURNAL_KEY}`) permet le *crypto-shredding* : effacer la clé d'un client rend son journal illisible sans toucher aux autres. Sans redirection, deux clients liraient la même variable et la perdre les effacerait tous les deux ; le chargement le signale.
- **Le renouvellement** : `keys: [NOUVELLE, ANCIENNE]` : la première ferme, toutes ouvrent. Chaque scellé porte l'empreinte de la clé qui l'a fermé. Une ligne en clair reste lisible après avoir activé le chiffrement (rien n'est réécrit) ; l'inverse n'est pas vrai.
- **Piège, l'effacement d'une clé est un simple avertissement** au chargement (le serveur démarre quand même, pour que les autres clients continuent). Une variable oubliée dans un fichier d'environnement produit le même effet qu'une clé effacée : surveillez cet avertissement dans vos logs.
- **Écart de forme (2.0.0).** Pour un client dont la clé est absente, `loom sessions export` affiche la trace Python complète terminée par `MissingKey`, au lieu d'un message net. D'après les sources, le serveur REST répond alors `424`, que je n'ai pas rejoué.
- **Ce que le sceau ne protège pas**, d'après la documentation : le **nom d'un fichier** d'artefact est le SHA-256 de son contenu en clair (qui a accès au dossier peut confirmer un contenu qu'il devine) ; les **exports** (`loom sessions export`) sont écrits en clair par construction ; le **magasin d'idempotence** partagé (`sqlite`, `postgres`, `redis`) garde le résultat des outils en clair pendant sa durée de vie (le magasin `journal`, lui, est sous le sceau mais ne convient qu'aux clés techniques).
- Le chiffrement joue sur `jsonl` et `sqlite` comme sur Postgres ; je l'ai exécuté sur SQLite.

### Exemple 24.3 : effacer, à la demande et par la durée

**Pourquoi.** Deux obligations. Un client, ou la personne dont les données figurent dans un devis, demande l'effacement (RGPD) : il faut le faire sur une session précise, sans rien oublier. Et personne n'a besoin de garder les relances de 2025 pour toujours : il faut une règle de durée qui s'applique sans y penser.

**Objectif.** Exporter et supprimer une session, sur le journal Postgres de 24.1 ; puis fixer une durée de conservation, la voir à blanc, et l'appliquer.

**Mise en place (à la demande).** Rien de nouveau : `loom sessions` travaille sur le journal de la configuration, pour le client nommé par `--tenant`.

**Exécution (à la demande).** Sur `pg.yaml` (journal Postgres, deux clients qui ont chacun une session `relance-042`) :

```bash
export LOOM_PG=postgresql://loom_login@127.0.0.1:15432/loom_prod
L="uv run loom --config pg.yaml"
$L sessions list --tenant martin-chauffage
$L sessions export relance-042 --tenant martin-chauffage --out martin-relance-042.jsonl
head -1 martin-relance-042.jsonl | cut -c1-200
$L sessions delete relance-042 --tenant martin-chauffage --yes
$L sessions list --tenant martin-chauffage
$L sessions list --tenant dupont-plomberie
```

```text
relance-042                  30 événements   2026-10-10 18:18
30 événements écrits dans martin-relance-042.jsonl
{"event_id":"01a1269b-fec4-7779-b776-4c805dee24b3","ts":"2026-10-10T16:18:39.940537Z","schema_version":1,"tenant_id":"martin-chauffage","session_id":"relance-042","run_id":"01a1269b-fec4-7779-b776-4c7e89ca7a7b","root_run_id":"01a1269b-fec4-7779-b776-4c7e89ca7a
Session relance-042 supprimée : 30 événement(s), 0 fichier(s), 0 clé(s).
Aucune session.
relance-042                  28 événements   2026-10-10 18:18
```

L'export (un événement JSON par ligne, **en clair**, c'est la copie qu'on remet à la personne) précède la suppression ; la suppression emporte le journal, les fichiers d'artefacts et les clés d'idempotence de la session. La session de Dupont, qui porte pourtant le même nom, est intacte. Ce `DELETE` passe par le rôle `loom_app`, qui l'a conservé, et par la politique de lignes, qui l'empêche de toucher un autre client.

En REST, les mêmes opérations existent : `GET /v1/sessions/{id}/events` et `DELETE /v1/sessions/{id}`, ce dernier réservé à une clé de portée `admin` (chapitre 20).

**Mise en place (par la durée).** `retention.yaml` est `clients.yaml` avec un journal à part et une règle commune de 365 jours. Chauffage Martin, lui, la désactive explicitement (on veut garder son historique) :

```yaml
tenants:
  - id: dupont-plomberie
    variables: {entreprise: Plomberie Dupont, signature: "L'équipe Dupont"}
  - id: martin-chauffage
    variables: {entreprise: Chauffage Martin, signature: "Chauffage Martin, service client"}
    models: {PRINCIPAL: ECONOMIQUE}
    approvals: {chercher_devis: always}
    retention: {events_days: null}

  - id: comptable-dupont
    variables: {entreprise: Plomberie Dupont (comptabilité), signature: "Cabinet comptable"}
    agents: [devis]

telemetry:
  logging: {level: WARNING}

storage:
  events: {backend: sqlite, path: data-retention/journal.db}
  retention: {events_days: 365}
```

`events_days` est un entier **strictement positif** (zéro voudrait dire « tout effacer maintenant »), et un client annule la règle commune avec `null`, écrit noir sur blanc. `validate` rend le résultat explicite :

```bash
uv run loom --config retention.yaml validate | grep Rétention
```

```text
Rétention  : sessions effacées après 365 jour(s) ; martin-chauffage: aucune — par « loom retention », que la plateforme met à l'heure
```

Pour avoir des sessions anciennes sans attendre un an, `anciennes.py` crée, pour Dupont et Martin, une session `relance-2025` et une `relance-2026`, puis on **antidate** les premières dans la base (c'est de la mise en scène : une vraie session ancienne l'est parce que personne n'y a écrit depuis) :

```python
import asyncio

from loom_ia.access import Loom
from loom_ia.core.model import Approved


async def accorder(demande):
    return Approved(by="denis")


async def main() -> None:
    async with Loom.from_config("retention.yaml") as loom:
        for client in ("dupont-plomberie", "martin-chauffage"):
            for session in ("relance-2025", "relance-2026"):
                await loom.run("relance", "Relance Mme Martin pour le devis D-2026-042.",
                               session_id=session, tenant=client, approver=accorder)


asyncio.run(main())
```

```bash
rm -rf data-retention
uv run python anciennes.py
uv run python - <<'EOF'
import sqlite3
c = sqlite3.connect("data-retention/journal.db")
c.execute("update events set ts='2025-03-02T10:00:00.000000Z' where session_id='relance-2025'")
c.commit()
EOF
```

**Exécution (par la durée).** D'abord à blanc, qui est le comportement par défaut :

```bash
uv run loom --config retention.yaml retention
```

```text
dupont-plomberie : 365 jour(s)
martin-chauffage : aucune règle
comptable-dupont : 365 jour(s)
  relance-2025 (dupont-plomberie) à effacer — dernière écriture 2025-03-02 10:00 UTC, 28 événement(s)
1 session(s) seraient effacées, 28 événement(s) ; 1 gardée(s) sur 2 regardée(s).
Essai à blanc : rien n'a été supprimé. Ajouter --yes pour effacer.
```

Puis pour de bon :

```bash
uv run loom --config retention.yaml retention --yes
uv run loom --config retention.yaml sessions list --tenant dupont-plomberie
uv run loom --config retention.yaml sessions list --tenant martin-chauffage
```

```text
dupont-plomberie : 365 jour(s)
martin-chauffage : aucune règle
comptable-dupont : 365 jour(s)
  relance-2025 (dupont-plomberie) effacée — dernière écriture 2025-03-02 10:00 UTC, 28 événement(s), 0 fichier(s), 0 clé(s)
1 session(s) effacées, 28 événement(s) ; 1 gardée(s) sur 2 regardée(s).
relance-2026                 28 événements   2026-10-10 18:19
relance-2026                 30 événements   2026-10-10 18:19
relance-2025                 30 événements   2025-03-02 11:00
```

Seule la session de Dupont qui dépassait la borne est partie ; la session récente est gardée, et celles de Martin sont intactes, y compris la vieille (`relance-2025`), puisque son client a désactivé la règle.

**À retenir.**

- **L'unité est la session**, jugée sur sa dernière écriture. On n'efface jamais le début d'une session : elle part en entier (journal, fichiers, clés d'idempotence), par le même chemin que `sessions delete`.
- La rétention ne **lit aucun contenu** : elle décide sur la marque de la session (client, session, rang, date). Elle fonctionne donc aussi sur un journal scellé dont la clé a disparu, là où la perte serait définitive.
- **Rien ne s'efface tout seul.** `loom retention` est un essai à blanc ; `--yes` supprime. Et Loom n'a pas de cron : la commande se met à l'heure par la plateforme (même principe que `relance-matinale` au chapitre 23), par exemple une fois par nuit.
- Le rapport donne son **prix** et la borne de chaque client. Dans l'essai à blanc, le nombre de fichiers et de clés effacés est inconnu : il ne se compte qu'en supprimant.
- **Piège.** Une session « muette » plus longtemps que la borne part, **même si un run y attendait une approbation**. Une durée de rétention plus courte que le délai d'une approbation (chapitre 9) détruit des approbations en attente.
- **Piège.** L'effacement porte sur ce que Loom contrôle. Les sauvegardes de la base, les anciens exports (en clair) et le système de fichiers restent à votre charge. Pour SQLite, Loom purge ce qu'il peut (`secure_delete`, puis `wal_checkpoint`) et avertit si un lecteur d'un autre process empêche de vider le fichier de journalisation ; les pages libérées avant cette version et les sauvegardes restent hors de portée.
- Ce que ces trois exemples ne règlent pas : décider **ce qui entre** dans le journal. Les niveaux de `capture` et le masquage des champs sensibles (numéros de téléphone, IBAN dans un message) se règlent à l'écriture ; ils sont traités au chapitre 25 de `04-qualite-et-expert.md`.

---

## Et ensuite

Vous savez maintenant faire tenir un agent en production : il résiste aux pannes des fournisseurs (chapitre 15), reste dans son budget (16), délègue (17), traite de gros fichiers (18), survit à un redémarrage sans doubler un e-mail (19), se laisse appeler par REST, MCP ou webhook (20, 21, 23), sert plusieurs entreprises sans les mélanger (22) et garde ses données comme il faut (24).

Reste à savoir si l'agent **fait bien son travail**, et à le faire évoluer sans casser ce qui marche : les évaluations, le rejeu d'un vrai run, les politiques de garde-fous, les juges, la capture et le masquage des données, les extensions. C'est l'objet de [`04-qualite-et-expert.md`](04-qualite-et-expert.md) (chapitres 25 à 30).
