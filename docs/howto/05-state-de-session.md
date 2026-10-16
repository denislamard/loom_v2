# Loom-IA : guide d'apprentissage

# Complément : le state de session

> **Dans ce fichier** : donner à une session quelques valeurs typées et durables — posées par l'application, ou tirées du résultat d'un outil —, les montrer à l'orchestrateur et aux rôles, voir ce que loom refuse, puis tester le tout (chapitre 31).
>
> **Navigation** : précédent : [04-qualite-et-expert.md](04-qualite-et-expert.md) · [05-state-de-session.md](05-state-de-session.md) (vous êtes ici) · suivant : [06-outils-fournis.md](06-outils-fournis.md)

Au chapitre 7, une conversation se souvient de ce qui précède parce que loom renvoie son historique au modèle, et la compaction le résume quand il devient trop long. C'est le modèle qui « se souvient », avec ce qu'un résumé a bien voulu garder. Pour quelques faits précis — le devis dont on parle, le nom à signer, le ton choisi par l'artisan —, on veut mieux : des valeurs que **le système** connaît, typées, durables, et que le modèle reçoit à chaque appel. C'est le **state de session**.

Ce fichier suppose les chapitres 7 (sessions), 12 (rôles délégués), 26 (rejeu), 27 (évaluations) et 28 (banc d'essai) lus.

---

## Sommaire

- [Avant de commencer](#avant-de-commencer)
- [31. Le state de session](#31-le-state-de-session)
  - [Exemple 31.1 : déclarer un state et le poser depuis l'application](#exemple-311--déclarer-un-state-et-le-poser-depuis-lapplication)
  - [Exemple 31.2 : un outil qui pose le state sans le savoir](#exemple-312--un-outil-qui-pose-le-state-sans-le-savoir)
  - [Exemple 31.3 : ce que reçoit l'orchestrateur](#exemple-313--ce-que-reçoit-lorchestrateur)
  - [Exemple 31.4 : une écriture refusée](#exemple-314--une-écriture-refusée)
  - [Exemple 31.5 : tester le state, et ce que le rejeu ne voit pas](#exemple-315--tester-le-state-et-ce-que-le-rejeu-ne-voit-pas)
- [Pour aller plus loin](#pour-aller-plus-loin)

---

## Avant de commencer

### Un projet neuf : `state/`

Comme les chapitres 25 à 30, ce chapitre travaille dans un projet à part, `state/`, qui ne contient que ce dont il a besoin : un agent de relance, un outil `chercher_devis` et deux modèles simulés. Chaque commande se lance depuis `state/`.

### Ce qui a été exécuté

Le state de session arrive avec **loom-ia 2.0.6**. Les sorties de ce chapitre sont réelles : loom-ia 2.0.6, Linux, Python 3.14, modèles simulés (`sdk: fake`). Les identifiants de run sont fixés avec `--run-id`, sauf ceux qu'une évaluation tire elle-même (`01a1…`), qui changeront chez vous ; les horodatages des avertissements aussi.

### Le rédacteur simulé renvoie ce qu'il reçoit

Le modèle `REDACTEUR` de ce chapitre n'a pas de script. Un modèle simulé sans script répond `Écho : ` suivi de la dernière demande qu'il a reçue : ici, le message que le rôle `rediger_relance` construit avec son `input_template`. La réponse finale montre donc **exactement** ce que le rédacteur a reçu, state compris. Avec un vrai modèle, on lirait un e-mail.

---

## 31. Le state de session

Trois façons de garder une information d'une demande à l'autre, qui ne se remplacent pas :

| | Historique (chapitre 7) | State de session (ce chapitre) | Mémoire longue (chapitre 29) |
|---|---|---|---|
| Ce que c'est | La conversation, renvoyée au modèle | Quelques valeurs typées, déclarées clé par clé | Des notes, cherchées par un outil |
| Qui l'écrit | Le modèle et les outils, en parlant | L'application, et les résultats d'outils d'après la config | L'agent, avec l'accord de l'artisan |
| Après une compaction | Résumé en prose | Intact | Intacte |
| Ce que le modèle en voit | Les messages | Un bloc JSON à la fin de chaque requête | Ce que l'outil rend |

Le state appartient à une session (et à son client), comme le journal. Il ne remplace pas la base de l'application : il n'en porte que des repères — un numéro de devis, pas le devis.

### Exemple 31.1 : déclarer un state et le poser depuis l'application

#### Pourquoi

Les e-mails de relance doivent être signés « Plomberie Dupont », et Jean Dupont a choisi un ton dans les réglages de son application. Ce n'est pas au modèle de s'en souvenir : l'application le sait, elle doit le dire à loom, et loom doit refuser un run qui partirait sans signature.

#### Objectif

Déclarer deux clés de state, lancer un run qui les pose, relire le state de la session, et voir ce que loom refuse.

#### Mise en place

```bash
uv init --bare --python 3.14 state
cd state
uv add "loom-ia>=2.0.6"
mkdir agents prompts
```

Tant que la 2.0.6 n'est pas publiée, installez-la depuis votre clone du dépôt : `uv add --editable /chemin/vers/loom_v2` à la place de la troisième ligne.

`loom.yaml` : deux modèles simulés, un journal en fichiers, et les avertissements seuls dans la console.

```yaml
version: 1

imports: [outils]
agents_dir: agents/
prompts_dir: prompts/

models:
  # L'orchestrateur simulé : il cherche le devis, puis fait rédiger.
  - id: SIMULE
    sdk: fake
    model: simule
    params:
      script:
        - with_text: D-2026-042
          tool_calls: [{name: chercher_devis, arguments: {numero: D-2026-042}}]
        - with_text: D-2026-042
          tool_calls: [{name: rediger_relance, arguments: {}}]
  # Le rédacteur simulé n'a pas de script : il renvoie ce qu'il reçoit.
  - id: REDACTEUR
    sdk: fake
    model: redacteur

storage:
  events: {backend: jsonl, path: data}

telemetry:
  logging: {level: WARNING}
```

`outils.py` : le carnet de devis, et l'outil qui le lit. Il ne sait rien du state.

```python
from loom_ia.core.ports import ToolError
from loom_ia.tools import tool

CARNET: dict[str, dict[str, str | float]] = {
    "D-2026-042": {
        "numero": "D-2026-042",
        "client": "Mme Martin",
        "objet": "Remplacement d'un chauffe-eau de 200 L",
        "montant_ttc": 1840.0,
        "envoye_le": "2026-09-02",
    },
}


@tool
def chercher_devis(numero: str) -> dict[str, str | float]:
    """Renvoie un devis par son numéro (format D-AAAA-NNN)."""
    devis = CARNET.get(numero)
    if devis is None:
        raise ToolError(f"Aucun devis {numero!r}. Devis connus : {', '.join(CARNET)}.")
    return devis
```

`agents/relance.yaml` : le bloc `state:` déclare les seules clés que la session peut porter.

```yaml
name: relance
description: Relance un client au sujet d'un devis en attente.

main:
  model: SIMULE
  system_file: relance.md

max_iterations: 5

state:
  signature:
    schema: {type: string, minLength: 1, maxLength: 80}
    description: Nom de l'entreprise, au bas de chaque e-mail.
    required: true
  ton:
    schema: {enum: [cordial, ferme]}
    description: Ton des relances, choisi par l'artisan.
    default: cordial

tools:
  - python: chercher_devis
    side_effects: none

roles:
  - name: rediger_relance
    description: Rédige l'e-mail de relance du devis cherché.
    model: REDACTEUR
    system_file: rediger.md
    input_schema: {type: object, properties: {}}
    context: [user_input, {tool_results: [chercher_devis]}, {session_state: [ton, signature]}]
    input_template: |-
      Demande : {{ context.user_input }}
      Devis : {{ context.tool_results.chercher_devis }}
      Ton : {{ context.session_state.ton }}
      Signature : {{ context.session_state.signature }}
    terminal: true
```

Chaque clé a un **schéma** (un JSON Schema, contrôlé au chargement) et une **description** (ce qu'elle désigne, donnée à qui la reçoit). `signature` est **obligatoire** : un run ne s'ouvre pas tant qu'elle n'a pas de valeur. `ton` a une **valeur par défaut** : lue tant que l'application n'en pose pas, elle n'est jamais écrite. Les deux options s'excluent.

Le rôle déclare les clés qu'il reçoit (`{session_state: [ton, signature]}`) et son template les cite (`{{ context.session_state.ton }}`) : comme pour tout contexte d'un rôle, ce qui est déclaré doit être cité (chapitre 12).

Enfin, les deux prompts :

```bash
cat > prompts/relance.md <<'EOF'
Tu aides un artisan à relancer ses clients au sujet de devis en attente.
Cherche le devis avec `chercher_devis`, puis confie la rédaction à `rediger_relance`.
EOF
cat > prompts/rediger.md <<'EOF'
Tu rédiges l'e-mail de relance d'un devis, au ton demandé, signé du nom donné.
EOF
```

```bash
uv run loom validate
```

```text
Config     : loom.yaml
…
  relance : modèle SIMULE, 1 outil(s) Python, rôle rediger_relance (REDACTEUR)

1 agent(s) monté(s) sans erreur.
```

#### Exécution

Un premier run, sans rien poser :

```bash
uv run loom run relance "Relance le client du devis D-2026-042." --session atelier --run-id relance-1
```

```text
Demande refusée : State refusé : signature : clé obligatoire sans valeur
```

Le code de sortie est 2, et rien n'a été écrit : la session `atelier` n'existe toujours pas. L'application pose la signature avec `--state CLE=VALEUR` :

```bash
uv run loom run relance "Relance le client du devis D-2026-042." --session atelier --run-id relance-1 --state signature="Plomberie Dupont"
```

```text
Écho : Demande : Relance le client du devis D-2026-042.
Devis : {"numero": "D-2026-042", "client": "Mme Martin", "objet": "Remplacement d'un chauffe-eau de 200 L", "montant_ttc": 1840.0, "envoye_le": "2026-09-02"}
Ton : cordial
Signature : Plomberie Dupont

Statut     : completed · itérations : 2 · tokens : 1095/177 · coût : 0.0000 $
Run        : relance-1
```

Le rédacteur a reçu la signature posée et le ton par défaut. Le state de la session se relit avec `loom state` :

```bash
uv run loom state atelier
```

```text
{
  "signature": "Plomberie Dupont"
}
```

`ton` n'y est pas : une valeur par défaut se lit, elle ne s'écrit pas. Jean Dupont change de ton dans son application, mais se trompe de mot :

```bash
uv run loom run relance "Relance le client du devis D-2026-042." --session atelier --run-id relance-2 --state ton=amical
```

```text
Demande refusée : State refusé : ton : ne suit pas son schéma — doit valoir l'une des valeurs ['cordial', 'ferme']
```

Puis avec une valeur permise. La signature n'est pas redonnée : la session l'a gardée.

```bash
uv run loom run relance "Relance le client du devis D-2026-042." --session atelier --run-id relance-2 --state ton=ferme
```

```text
Écho : Demande : Relance le client du devis D-2026-042.
Devis : {"numero": "D-2026-042", "client": "Mme Martin", "objet": "Remplacement d'un chauffe-eau de 200 L", "montant_ttc": 1840.0, "envoye_le": "2026-09-02"}
Ton : ferme
Signature : Plomberie Dupont

Statut     : completed · itérations : 2 · tokens : 1970/177 · coût : 0.0000 $
Run        : relance-2
```

```bash
uv run loom state atelier
```

```text
{
  "signature": "Plomberie Dupont",
  "ton": "ferme"
}
```

#### À retenir

- `state:` déclare les clés d'une session, chacune avec son schéma. Une clé non déclarée, une valeur qui ne tient pas son schéma ou qui dépasse les limites (1 Kio par valeur, 4 Kio en tout, réglables par `sessions.state`) sont refusées.
- L'application pose du state à l'ouverture d'un run : `--state CLE=VALEUR` en ligne de commande (répétable ; la valeur se lit en JSON — `3`, `true`, `null` qui efface la clé, `"texte"` —, sinon elle est prise telle quelle), `state={…}` en Python (`Loom.run`, `stream`, `submit`).
- Un state refusé, ou une clé `required` sans valeur, refusent le run **avant qu'il n'écrive quoi que ce soit** : code 2 en ligne de commande, `StateRefused` en Python, 422 en REST, un résultat d'erreur (`isError`) en MCP.
- Ce qui est posé reste dans la session pour les runs suivants. `loom state <session>` (et `Loom.session_state()` en Python) relit ce qui est écrit, sans les valeurs par défaut.
- Un rôle ne reçoit que les clés qu'il déclare, et les cite dans son template.

### Exemple 31.2 : un outil qui pose le state sans le savoir

#### Pourquoi

Après une relance, Jean Dupont écrit simplement : « Refais-la, plus ferme. » Le devis n'est plus cité. Un vrai modèle le retrouverait sans doute dans l'historique, mais pas après une compaction qui l'aurait résumé de travers. Le numéro du devis en cours est une donnée de contrôle : il doit être dans le state, et c'est `chercher_devis` qui le connaît.

#### Objectif

Faire poser `devis_courant` et `client_courant` par le résultat de `chercher_devis`, sans toucher à l'outil, et montrer le state à l'orchestrateur.

#### Mise en place

Dans `agents/relance.yaml`, ajoutez `context` à `main`, et deux clés au state :

```yaml
main:
  model: SIMULE
  system_file: relance.md
  context: [session_state]
```

```yaml
  devis_courant:
    schema: {type: string, pattern: "^D-[0-9]{4}-[0-9]{3}$"}
    description: Numéro du devis dont parle la conversation.
    from: [{tool: chercher_devis, path: /numero}]
  client_courant:
    schema: {type: string}
    description: Client du devis dont parle la conversation.
    from: [{tool: chercher_devis, path: /client}]
```

`from` dit quel outil pose la clé, et où lire la valeur dans les données de son résultat : `path` est un pointeur JSON (`/numero`, `/lignes/0/total`, ou `""` pour toutes ses données). Une valeur fixe s'écrit `value:` à la place de `path:`. `path` demande un outil qui rend des données : un outil dont la signature annonce `str`, sans contrat de sortie à schéma, est refusé au chargement.

`main.context: [session_state]` donne tout le state à l'orchestrateur, à la fin de chaque requête ; `[{session_state: [devis_courant]}]` n'en donnerait qu'une clé.

Dans `loom.yaml`, le script de l'orchestrateur simulé apprend la suite de la conversation (les deux premières lignes, déjà là, situent l'ajout) :

```yaml
        - with_text: D-2026-042
          tool_calls: [{name: rediger_relance, arguments: {}}]
        # La suite de la conversation ne cite pas le devis : un vrai modèle
        # lit son numéro dans le state ; le simulé le connaît d'avance.
        - with_text: Refais-la
          tool_calls: [{name: chercher_devis, arguments: {numero: D-2026-042}}]
        - with_text: Refais-la
          tool_calls: [{name: rediger_relance, arguments: {}}]
```

#### Exécution

Une nouvelle session, `atelier-2` :

```bash
uv run loom run relance "Relance le client du devis D-2026-042." --session atelier-2 --run-id premier --state signature="Plomberie Dupont"
```

```text
Écho : Demande : Relance le client du devis D-2026-042.
Devis : {"numero": "D-2026-042", "client": "Mme Martin", "objet": "Remplacement d'un chauffe-eau de 200 L", "montant_ttc": 1840.0, "envoye_le": "2026-09-02"}
Ton : cordial
Signature : Plomberie Dupont

Statut     : completed · itérations : 2 · tokens : 1546/177 · coût : 0.0000 $
Run        : premier
```

```bash
uv run loom state atelier-2
```

```text
{
  "client_courant": "Mme Martin",
  "devis_courant": "D-2026-042",
  "signature": "Plomberie Dupont"
}
```

`chercher_devis` n'a pas changé : c'est loom qui a lu `numero` et `client` dans son résultat. La suite de la conversation :

```bash
uv run loom run relance "Refais-la, plus ferme." --session atelier-2 --run-id second --state ton=ferme
```

```text
Écho : Demande : Refais-la, plus ferme.
Devis : {"numero": "D-2026-042", "client": "Mme Martin", "objet": "Remplacement d'un chauffe-eau de 200 L", "montant_ttc": 1840.0, "envoye_le": "2026-09-02"}
Ton : ferme
Signature : Plomberie Dupont

Statut     : completed · itérations : 2 · tokens : 2425/173 · coût : 0.0000 $
Run        : second
```

```bash
uv run loom state atelier-2
```

```text
{
  "client_courant": "Mme Martin",
  "devis_courant": "D-2026-042",
  "signature": "Plomberie Dupont",
  "ton": "ferme"
}
```

La première requête du second run se terminait par le state de la session, `devis_courant` compris : c'est là qu'un vrai modèle aurait lu le numéro à chercher. L'exemple suivant montre ce bloc.

#### À retenir

- `from` fait poser une clé par le résultat **réussi** d'un outil, sans le modifier : `path` lit une valeur dans ses données, `value` en fixe une. Un résultat en erreur ne pose rien. Un outil peut aussi écrire lui-même, en rendant `ToolOutput(..., state={…})` ; sur une même clé, sa valeur l'emporte.
- Les écritures d'un outil comptent pour la session quand le run **se termine avec succès**. Ce que l'application pose compte dès l'ouverture du run.
- `main.context: [session_state]` montre le state à l'orchestrateur à chaque appel ; sans lui, ses requêtes ne changent pas.
- Deux outils d'un même lot qui écrivent la même clé : c'est l'ordre des appels qui décide, pas celui où ils finissent.
- Un outil lit le state, tel qu'au début de son lot, dans `ToolContext.state` (un paramètre annoté `ToolContext`, exemple 12.4) ; un sous-agent ne le reçoit pas.

### Exemple 31.3 : ce que reçoit l'orchestrateur

#### Pourquoi

« Le state est dans chaque requête » : Jean Dupont veut le voir de ses yeux, et savoir ce qu'il coûte.

#### Objectif

Lancer le run au banc d'essai (chapitre 28), avec un orchestrateur scripté qui garde ses requêtes, et imprimer le dernier message de chacune et la fin du prompt système.

#### Mise en place

`voir_state.py` :

```python
import asyncio

from loom_ia.core.model import Message
from loom_ia.testing import Bench, ScriptedModel, tool_call_message


async def main() -> None:
    orchestrateur = ScriptedModel(
        tool_call_message(("c1", "chercher_devis", {"numero": "D-2026-042"})),
        Message.assistant("Le devis D-2026-042 de Mme Martin attend toujours sa réponse."),
    )
    async with Bench("loom.yaml", models={"SIMULE": orchestrateur}) as banc:
        await banc.run(
            "relance", "Où en est le devis D-2026-042 ?", state={"signature": "Plomberie Dupont"}
        )
    for numero, requete in enumerate(orchestrateur.requests, start=1):
        print(f"--- requête {numero}, dernier message :")
        print(requete.messages[-1].text)
    print("--- fin du prompt système :")
    print(orchestrateur.requests[0].system.split("\n\n")[-1])


asyncio.run(main())
```

`Bench.run` prend `state=` comme `Loom.run`. Le banc monte l'agent à part : rien n'est écrit dans `data/`.

#### Exécution

```bash
uv run python voir_state.py
```

```text
--- requête 1, dernier message :
<session_state>
{"signature":"Plomberie Dupont","ton":"cordial"}
</session_state>
--- requête 2, dernier message :
<session_state>
{"client_courant":"Mme Martin","devis_courant":"D-2026-042","signature":"Plomberie Dupont","ton":"cordial"}
</session_state>
--- fin du prompt système :
La fin de chaque requête donne l'état courant de la session, entre balises <session_state> : un objet JSON tenu à jour par l'application et les outils. Seul le dernier bloc <session_state> de la requête fait foi ; une clé absente n'a pas de valeur. Clés :
- signature : Nom de l'entreprise, au bas de chaque e-mail.
- ton : Ton des relances, choisi par l'artisan.
- devis_courant : Numéro du devis dont parle la conversation.
- client_courant : Client du devis dont parle la conversation.
```

La première requête porte ce que l'application a posé, et le ton par défaut. La seconde, envoyée après `chercher_devis`, porte aussi ce que son résultat a posé : le run voit ses propres écritures sans attendre sa fin.

#### À retenir

- Le state **termine** chaque requête de l'orchestrateur (seule la consigne d'une réponse forcée vient après lui), en JSON compact aux clés triées : un même state donne la même requête. Il part **à chaque appel, même vide** (`{}`), et n'est jamais écrit au journal comme un message : il est recalculé à chaque appel.
- La consigne qui l'explique, et la description de chaque clé, sont dans le **prompt système**, qui ne change pas d'un appel à l'autre. Avec le cache de prompt (`cache: {messages: true}` d'un modèle `sdk: anthropic`, exemple 18.3), l'adaptateur Anthropic pose son point de cache juste avant le bloc (non exécuté ici : aucune clé Anthropic) : un state qui change ne fait pas perdre le cache de l'historique, seul le bloc est payé plein tarif.
- Le bloc part à chaque appel : gardez le state **petit** — des repères, pas des documents.
- Les clés montrées ont leur valeur par défaut tant qu'elles n'en ont pas d'autre. Une valeur écrite qui ne tient plus son schéma, parce que la config a changé depuis, est écartée avec un avertissement, sans erreur.

### Exemple 31.4 : une écriture refusée

#### Pourquoi

Le carnet de Jean Dupont contient encore des devis de son ancien logiciel, numérotés avant le format `D-AAAA-NNN`. Leur numéro ne tient pas le schéma de `devis_courant`. Que se passe-t-il quand `chercher_devis` en rend un ?

#### Objectif

Faire chercher un devis à l'ancien format, et voir ce que loom pose, ce qu'il refuse et ce qu'il dit.

#### Mise en place

Dans `outils.py`, ajoutez au carnet :

```python
    # Un devis de l'ancien logiciel, numéroté avant le format D-AAAA-NNN.
    "D-2025-7": {
        "numero": "D-2025-7",
        "client": "M. Leroy",
        "objet": "Entretien annuel d'une chaudière gaz",
        "montant_ttc": 189.0,
        "envoye_le": "2025-11-14",
    },
```

Et dans le script de l'orchestrateur simulé (`loom.yaml`), avant la suite de la conversation :

```yaml
        - with_text: D-2025-7
          tool_calls: [{name: chercher_devis, arguments: {numero: D-2025-7}}]
        - with_text: D-2025-7
          tool_calls: [{name: rediger_relance, arguments: {}}]
```

#### Exécution

```bash
uv run loom run relance "Relance le client du devis D-2025-7." --session atelier-3 --run-id ancien --state signature="Plomberie Dupont"
```

```text
2026-10-15 15:32:38 WARNING  loom_ia.engine.session_state — State : outil chercher_devis, devis_courant non posé : ne suit pas son schéma — ne respecte pas le motif ^D-[0-9]{4}-[0-9]{3}$
Écho : Demande : Relance le client du devis D-2025-7.
Devis : {"numero": "D-2025-7", "client": "M. Leroy", "objet": "Entretien annuel d'une chaudière gaz", "montant_ttc": 189.0, "envoye_le": "2025-11-14"}
Ton : cordial
Signature : Plomberie Dupont

Statut     : completed · itérations : 2 · tokens : 1650/174 · coût : 0.0000 $
Run        : ancien
```

```bash
uv run loom state atelier-3
```

```text
{
  "client_courant": "M. Leroy",
  "signature": "Plomberie Dupont"
}
```

L'appel a réussi, le run aussi, et `client_courant` est posé. Seul `devis_courant` ne l'est pas, et loom l'a dit dans son log. Le modèle, lui, l'a lu à la suite du résultat de `chercher_devis` :

```text
[state] devis_courant non posé : ne suit pas son schéma — ne respecte pas le motif ^D-[0-9]{4}-[0-9]{3}$
```

#### À retenir

- Une écriture refusée par un outil (clé non déclarée, schéma, taille, chemin absent du résultat) n'est pas posée, mais **le résultat reste un succès** : l'outil a pu faire son travail, et une erreur pousserait le modèle à le refaire. Les autres écritures du même appel sont posées, sauf si l'appel faisait dépasser la taille totale du state : toutes celles qui ajoutent sont alors refusées.
- Le refus est gardé au journal avec le résultat (`state_refused`), dit dans le log (`WARNING`), et lu par le modèle à la suite du résultat, une ligne `[state]` par clé. Le motif ne recopie pas la valeur.
- Un outil générique, comme une requête SQL, rend souvent des résultats qui ne parlent pas de la clé. Pour qu'un chemin absent n'y soit pas un refus, déclarez la source avec `on_missing: ignore` : `{tool: requete_sql, path: /rows/0/code, on_missing: ignore}`. Rien n'est alors posé ni dit au modèle ; une valeur trouvée reste contrôlée.
- À l'inverse, un state refusé **à l'ouverture** (exemple 31.1) refuse le run : c'est l'application qui se trompe, et rien n'a encore eu lieu.

### Exemple 31.5 : tester le state, et ce que le rejeu ne voit pas

#### Pourquoi

Jean Dupont veut qu'une modification de sa configuration qui casserait le state se voie tout de suite, comme une modification de prompt au chapitre 28.

#### Objectif

Écrire une suite d'évaluation qui pose un state par cas et contrôle celui que le run laisse ; garder les journaux ; puis casser une source `from` et comparer ce que voient l'évaluation et le rejeu.

#### Mise en place

`suite.yaml` :

```yaml
config: loom.yaml
agent: relance
cases:
  # Seule la signature est posée : le ton vaut cordial, sans être écrit.
  - name: cordial
    input: Relance le client du devis D-2026-042.
    state: {signature: Plomberie Dupont}
    expect:
      contains: ["Ton : cordial", "Signature : Plomberie Dupont"]
      state: {devis_courant: D-2026-042, client_courant: Mme Martin, ton: null}
  # Le ton posé par l'application arrive jusqu'au rédacteur.
  - name: ferme
    input: Relance le client du devis D-2026-042.
    state: {signature: Plomberie Dupont, ton: ferme}
    expect:
      contains: ["Ton : ferme"]
      state: {ton: ferme}
  # Un numéro à l'ancien format : le client est posé, le devis non.
  - name: ancien
    input: Relance le client du devis D-2025-7.
    state: {signature: Plomberie Dupont}
    expect:
      state: {client_courant: M. Leroy, devis_courant: null}
```

`state` d'un cas est posé à l'ouverture de son run, comme `--state` ; chaque cas part d'une session neuve, donc chacun pose sa clé obligatoire. `expect.state` contrôle des clés du state après le run : leur valeur, ou leur absence avec `null`.

#### Exécution

```bash
uv run loom eval suite.yaml --export journaux/
```

```text
2026-10-15 15:32:40 WARNING  loom_ia.engine.session_state — State : outil chercher_devis, devis_courant non posé : ne suit pas son schéma — ne respecte pas le motif ^D-[0-9]{4}-[0-9]{3}$
Suite      : suite — agent relance, 3 cas, 1 variante(s), 1 répétition(s) par cas

Variante base (config de base) — 3/3 cas réussi(s), 0.000000 $ de runs, 0.000000 $ de juge
  ok       cordial (1/1)
  ok       ferme (1/1)
  ok       ancien (1/1)

Bilan      : 3/3 run(s) réussi(s), 0.000000 $ dépensé(s)
Verdict    : tout passe
```

L'avertissement est celui du cas `ancien`, attendu. Les trois journaux sont dans `journaux/`. Celui du cas `cordial` se rejoue à l'identique, state compris :

```bash
uv run loom replay --journal journaux/base--cordial--1.jsonl
```

```text
Journal    : journaux/base--cordial--1.jsonl — 1 run(s) à rejouer

Run        : 01a13fc3-d2bd-74fa-93f5-faa5d1ba975e (agent relance, client default)
Session    : 01a13fc3-d2bd-74fa-93f5-faa5d1ba975e
Issue      : completed au journal, completed au rejeu
Modèles    : 3 appel(s) au journal, 3 servi(s) au rejeu
Outils     : 1 résultat(s) au journal (rôles à part), 1 servi(s) au rejeu
Identique  : le run se rejoue tel qu'il a été, appel par appel.

Bilan      : 1/1 run(s) identique(s)
```

Maintenant, une faute de frappe dans `agents/relance.yaml` : la source de `client_courant` lit `/objet` au lieu de `/client`.

```yaml
    from: [{tool: chercher_devis, path: /objet}]
```

L'évaluation la voit :

```bash
uv run loom eval suite.yaml
```

```text
2026-10-15 15:32:42 WARNING  loom_ia.engine.session_state — State : outil chercher_devis, devis_courant non posé : ne suit pas son schéma — ne respecte pas le motif ^D-[0-9]{4}-[0-9]{3}$
Suite      : suite — agent relance, 3 cas, 1 variante(s), 1 répétition(s) par cas

Variante base (config de base) — 1/3 cas réussi(s), 0.000000 $ de runs, 0.000000 $ de juge
  ÉCHEC    cordial (0/1)
      essai 1 : run 01a13fc3-d988-72b2-929b-c9a59dc7d120, completed
        ✗ state client_courant = "Mme Martin" — vaut "Remplacement d'un chauffe-eau de 200 L"
  ok       ferme (1/1)
  ÉCHEC    ancien (0/1)
      essai 1 : run 01a13fc3-d9d9-7011-a5c9-8105b6676e25, completed
        ✗ state client_courant = "M. Leroy" — vaut "Entretien annuel d'une chaudière gaz"

Bilan      : 1/3 run(s) réussi(s), 0.000000 $ dépensé(s)
Verdict    : des attendus tombent, ou des runs n'ont pas été joués
```

Le code de sortie est 1. Le rejeu, lui, ne la voit pas :

```bash
uv run loom replay --journal journaux/base--cordial--1.jsonl
```

```text
Journal    : journaux/base--cordial--1.jsonl — 1 run(s) à rejouer

Run        : 01a13fc3-d2bd-74fa-93f5-faa5d1ba975e (agent relance, client default)
Session    : 01a13fc3-d2bd-74fa-93f5-faa5d1ba975e
Issue      : completed au journal, completed au rejeu
Modèles    : 3 appel(s) au journal, 3 servi(s) au rejeu
Outils     : 1 résultat(s) au journal (rôles à part), 1 servi(s) au rejeu
Identique  : le run se rejoue tel qu'il a été, appel par appel.

Bilan      : 1/1 run(s) identique(s)
```

Remettez `path: /client` : l'évaluation repasse.

#### À retenir

- Un cas d'évaluation pose son state (`state`) et contrôle celui que le run laisse (`expect.state`). Au banc, c'est `banc.run(…, state=…)` et `banc.expect(result, state={…})`.
- Le **rejeu** sert à chaque outil le résultat du journal, **state compris** : il ne recalcule pas les sources `from`. Une config qui ne change que ces sources se rejoue donc à l'identique — comme un changement du seul code d'un outil. C'est l'**évaluation**, qui fait tourner les outils, qui la voit. Gardez les deux : le rejeu pour les requêtes, l'évaluation pour ce que le run produit.
- Quand l'orchestrateur reçoit le state, une config qui change ce qu'il en voit (une valeur par défaut, les clés de `main.context`) change ses requêtes, et le rejeu le voit : le run diverge, sur les messages.
- Une doublure d'outil (évaluation, variante du rejeu) ou un faux du banc reçoit les sources `from` comme l'outil qu'il remplace : il doit répondre comme lui, faute de quoi le modèle lit un `[state] … chemin absent du résultat`.

---

## Pour aller plus loin

- **La référence** : `docs/state-et-context.md` — ce que fait le state, ses règles et ses décisions ; `docs/fonctions.md`, point 51.
- **L'exemple du dépôt** : `examples/j7/state.py`, qui montre le bloc tel que l'orchestrateur l'a reçu (échanges bruts au journal), et sa suite de non-régression `tests/regression/j7/`.
- **Ce que le state ne fait pas encore** : REST et MCP ne posent pas de state, si bien qu'un agent qui a une clé `required` n'y ouvre un run que dans une session où elle a déjà une valeur (sinon : 422 en REST, un résultat d'erreur en MCP) ; un juge ne reçoit pas le state ; un sous-agent non plus.

Navigation : [01-demarrer.md](01-demarrer.md) · [02-maitriser.md](02-maitriser.md) · [03-production.md](03-production.md) · [04-qualite-et-expert.md](04-qualite-et-expert.md) · [05-state-de-session.md](05-state-de-session.md) · [06-outils-fournis.md](06-outils-fournis.md)
