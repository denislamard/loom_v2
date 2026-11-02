# Analyse d'impact — plusieurs questions dans un même `ask` : lecture et réponse par accès loom_v2

## En bref

`loom.ask` accepte jusqu'à 4 questions dans un même appel, et le moteur les traite bien. Leur **présentation** est le point faible, surtout en CLI : l'essai réel du 02/11 (`t-2000`, Haiku, mise en demeure de INV-2026-0013) a posé deux questions, « Relances » et « Acompte », et `loom answer … --answer 1` a été refusé (« 1 réponse(s) pour 2 question(s) ») sans dire comment répondre.

Ce qui est proposé :

- **CLI** (`access/cli/main.py`, `access/progress.py`) : les questions numérotées avec leur intitulé ; une ligne d'aide qui donne la commande exacte, un `--answer` par question ; un refus qui dit comment corriger ; un numéro hors des choix refusé au lieu de partir comme texte ; l'écho de ce qui a été compris ; le même rendu dans `loom sessions show`.
- **Moteur, message seulement** (`engine/questions.py`, `reply_problem`) : un refus qui nomme les questions par leur intitulé et dit la règle. Il sert à la fois la CLI, le SDK et le REST.
- **MCP** (`access/mcp_server/server.py`) : sans elicitation, les questions numérotées avec leur intitulé, et la consigne d'y répondre en un seul message ; avec elicitation, un titre distinct pour le champ libre ; et la correction d'un défaut existant (un run qui attend une approbation **et** une question ne montre que l'approbation).
- **SDK et REST** : aucun changement de code. Ils sont structurés et reçoivent des libellés, pas des numéros. Un exemple REST à deux questions est ajouté à la doc.

**Rien ne change** dans le journal, les événements, les requêtes au modèle ni le rejeu. Avec **une seule question**, tous les rendus restent ceux d'aujourd'hui, sauf l'écho après `loom answer`.

## Périmètre et état de référence

- Dépôt `loom_v2`, commit `a9839a4` (« ask & approval bugs »), version 2.0.15 non publiée.
- Ce qui existe :
  - CLI : `_questions_text` (questions sur stdout), les lignes d'aide de `_report` (« Question : … — répondre : … »), `cmd_answer`, `_choices_by_number` et `_numbered` (numéro affiché → libellé), le bloc « En attente » de `cmd_sessions_show` ;
  - `access/progress.py` : la ligne « question à l'utilisateur : … », lue par `--stream` **et** envoyée en notification de progression MCP ;
  - moteur : `reply_problem` (`engine/questions.py`), qui vérifie une réponse (une par question, au moins une valeur, une seule sauf `multiple`) ;
  - MCP : `_text`, `_asking` (texte sans elicitation), `_answering` et `_question_form` (formulaire d'elicitation).
- `reply_problem` est appelé à deux endroits : `Loom.answer` (`api.py`), dont le message devient `InvalidAnswer` (SDK), un `422` (REST) ou un refus de la CLI ; et l'exécuteur (`executor.py`), pour un répondeur en ligne, où il ne sert **qu'à un avertissement de log**. Il n'est jamais envoyé au modèle ni écrit au journal.
- Hors périmètre : l'interface graphique qu'un client REST ou MCP construit lui-même ; `loom inspect`, qui montre déjà les questions événement par événement.

## 1. L'essai qui a montré le défaut

```text
$ loom run agent "Prépare la mise en demeure de la facture INV-2026-0013" --session t-2000
Pour INV-2026-0013 (C-644, Électricité Nguyen Pro, client professionnel), … [texte du modèle]

Aucune relance n'est notée sur la facture INV-2026-0013. Les J+10 et J+21 ont-ils été faits sans être notés ?
  1. Oui, relances faites (je précise la date et le canal) — Je les ajoute aux notes de la facture…
  2. Non, aucune relance faite — Je prépare la mise en demeure en signalant…
Un acompte ou un paiement partiel a-t-il été versé sur cette facture ?
  1. Non, rien versé — Le calcul reste sur 1 077,79 € TTC.
  2. Oui (je donne le montant) — Les pénalités seront recalculées sur le solde restant dû.

Statut     : paused · …
Question   : toolu_0145… — répondre : loom run agent "<réponse>" --session t-2000 (ou loom answer 01a19cc9-… --answer <n°> --session t-2000)

$ loom answer 01a19cc9-… --answer 1 --session t-2000
Réponse refusée : 1 réponse(s) pour 2 question(s)
```

Ce qui gêne :

- les deux questions se suivent sans séparation, et leurs choix sont numérotés tous deux à partir de 1 : rien ne dit où commence la seconde ;
- l'aide dit `--answer <n°>`, au singulier ;
- le refus dit ce qui manque, pas comment le donner ;
- une fois la réponse acceptée, `Répondu : toolu_0145…` ne permet pas de vérifier ce qui a été compris.

## 2. Analyse fonctionnelle

### 2.1 CLI : les questions numérotées, avec leur intitulé

Avec **plusieurs** questions, chacune est précédée de `Question i/n`, suivi de son intitulé (`header`) s'il y en a un, et séparée de la suivante par une ligne vide :

```text
Question 1/2 · Relances
Aucune relance n'est notée sur la facture INV-2026-0013. Les J+10 et J+21 ont-ils été faits sans être notés ?
  1. Oui, relances faites (je précise la date et le canal) — Je les ajoute aux notes de la facture…
  2. Non, aucune relance faite — Je prépare la mise en demeure en signalant…

Question 2/2 · Acompte
Un acompte ou un paiement partiel a-t-il été versé sur cette facture ?
  1. Non, rien versé — Le calcul reste sur 1 077,79 € TTC.
  2. Oui (je donne le montant) — Les pénalités seront recalculées sur le solde restant dû.
```

- Une question à **choix multiples** (`multiple: true`) le dit sous ses choix : `  plusieurs choix : les séparer par « ; » (ex. 1;3)`.
- Avec **une seule** question : affichage inchangé (ni `Question 1/1`, ni intitulé).

### 2.2 CLI : une ligne d'aide qui donne la commande exacte

Sur stderr, à la place de la ligne `Question : …` quand il y a plusieurs questions :

```text
Questions  : 2 en attente (toolu_0145…) — une réponse par question, dans l'ordre :
             loom answer 01a19cc9-… --answer <Relances> --answer <Acompte> --session t-2000
             (le numéro d'un choix, son libellé, ou un texte ; en message libre :
              loom run agent "<réponse>" --session t-2000)
```

- Chaque `<…>` reprend l'intitulé de sa question, sinon `<réponse i>`. Deux intitulés identiques sont suffixés de leur rang (`<Client 1>`, `<Client 2>`).
- Les `<…>` restent **sans guillemets**, comme `<n°>` aujourd'hui : une ligne copiée telle quelle échoue dans le shell (« < » est une redirection) au lieu d'envoyer « <Relances> » au modèle comme une réponse.
- `--tenant` est ajouté s'il a été donné, comme aujourd'hui.
- Avec une seule question : ligne inchangée (`--answer <n°>`).
- Plusieurs appels `ask` en attente dans l'arbre du run (rare : sous-agents) : une ligne par appel, chacune avec `--call`, comme aujourd'hui.

### 2.3 CLI : un refus qui dit comment corriger

Nombre de réponses différent du nombre de questions : le message du moteur (§ 2.7), suivi de la commande.

```text
Réponse refusée : 1 réponse pour 2 questions (Relances, Acompte) : une réponse par question, dans leur ordre.
  loom answer 01a19cc9-… --answer <Relances> --answer <Acompte> --session t-2000
```

**Numéro hors des choix** (nouveau contrôle, en CLI seulement) : `--answer 3` sur une question à deux choix part aujourd'hui en **texte libre** « 3 », et le modèle reçoit « 3 » comme réponse. Proposé :

```text
Réponse refusée : question 2 (Acompte) : choix 3 inconnu, attendu 1 à 2 ou un libellé.
Pour répondre « 3 » en texte, l'écrire en message : loom answer 01a19cc9-… "3" --session t-2000
```

Le contrôle ne vaut que si la question **a des choix** et qu'**aucun libellé n'est un nombre** (règle déjà suivie par `_numbered`). Une question sans choix garde « 3 » comme texte. Code de sortie 2, comme les autres refus.

### 2.4 CLI : l'écho de ce qui a été compris

```text
Répondu    : Relances → Oui, relances faites (je précise la date et le canal)
             Acompte  → Non, rien versé
```

- Une seule question : `Répondu    : Client → Martin` (son intitulé, sinon `Répondu    : Martin`).
- Message libre (`loom answer RUN "texte"`) : `Répondu    : message libre « … »`, coupé à 110 caractères avec `…`, comme `inspect`.
- Écrit par `_say`, donc les caractères de contrôle d'un libellé se montrent sans agir.
- `--json` : inchangé.

### 2.5 CLI : `loom sessions show`

Aujourd'hui, une ligne par appel, les questions jointes par « ; », sans leurs choix : on ne peut pas répondre par numéro sans relancer le run pour les revoir. Proposé : le même bloc qu'en 2.1, en retrait sous la ligne de l'appel, puis la commande de 2.2. Avec une question : la ligne d'aujourd'hui, suivie des choix numérotés.

### 2.6 `--stream` et progression MCP

La même fonction (`progress.py`) écrit la ligne de `--stream` et la notification de progression MCP. Avec plusieurs questions : `questions à l'utilisateur (2) : Relances ; Acompte` (les intitulés ; une question sans intitulé garde son texte). Avec une question : inchangé.

### 2.7 Moteur : le message de refus (CLI, SDK, REST)

`reply_problem` nomme les questions par leur intitulé et dit la règle :

| Aujourd'hui | Proposé |
|---|---|
| `1 réponse(s) pour 2 question(s)` | `1 réponse pour 2 questions (Relances, Acompte) : une réponse par question, dans leur ordre` |
| `question 2 : un seul choix attendu` | `question 2 (Acompte) : un seul choix attendu` |
| `question 2 : réponse vide` | `question 2 (Acompte) : réponse vide` |

Seul le texte change : mêmes cas refusés, même exception, même code (`422` en REST, 2 en CLI). Sans intitulé, `(Relances, Acompte)` devient `(questions 1 et 2)`.

### 2.8 MCP sans elicitation : le texte rendu au client

Le client est souvent un modèle (Claude Desktop) qui lit ce texte et rappelle l'agent avec la réponse en message. Aujourd'hui, les questions sont empilées, chacune suivie de « (choix : …) ». Proposé, avec plusieurs questions :

```text
[texte du modèle]

Question 1/2 · Relances : Aucune relance n'est notée sur la facture INV-2026-0013. Les J+10 et J+21 ont-ils été faits sans être notés ? (choix : Oui, relances faites… ; Non, aucune relance faite…)
Question 2/2 · Acompte : Un acompte ou un paiement partiel a-t-il été versé sur cette facture ? (choix : Non, rien versé ; Oui (je donne le montant))

[L'agent attend la réponse de l'utilisateur aux 2 questions : rappeler agent avec session_id=t-2000 et les réponses en un seul message.]
```

- Les choix sont séparés par « ; » au lieu de « , » : un libellé contient souvent une virgule.
- Avec une question : texte inchangé.
- `structuredContent.pending_questions` : inchangé.

### 2.9 MCP avec elicitation : le formulaire

Chaque question donne un champ « choix » (liste) et un champ « texte libre », **tous deux titrés** par l'intitulé : un formulaire à deux questions montre quatre champs, deux fois deux titres identiques. Proposé : le champ libre est titré `Relances : autre réponse` quand il accompagne une liste, et garde l'intitulé seul quand il est le seul champ de sa question. Les **noms** des champs (`choix_1`, `reponse_1`) ne changent pas : ce sont eux qui portent les valeurs.

## 3. Par accès

| Accès | Changement |
|---|---|
| CLI `run`, `answer`, `approve`, `resume` (même rendu), `sessions show`, `--stream` | affichage (§ 2.1 à 2.6), contrôle du numéro hors des choix (§ 2.3) |
| SDK `Loom.answer`, `RunResult.pending_questions` | le texte de `InvalidAnswer` (§ 2.7) ; rien d'autre |
| REST `POST /v1/runs/{id}/answer`, `GET /v1/runs/{id}` | le `detail` du `422` (§ 2.7) ; un exemple à deux questions dans la doc |
| MCP sans elicitation | le texte rendu (§ 2.8), la correction du § 6.1, la ligne de progression (§ 2.6) |
| MCP avec elicitation | le titre du champ libre (§ 2.9) |
| Journal, événements, requêtes au modèle, rejeu | aucun changement |

## 4. Analyse technique

- **`engine/questions.py`, `reply_problem`** : les intitulés dans les messages. Environ 10 lignes.
- **`access/cli/main.py`** :
  - `_questions_text(result)` : numérotation et intitulés quand `len(questions) > 1`, ligne « plusieurs choix » pour `multiple`. Environ 20 lignes ;
  - `_answer_hint(pending, run_id, where)` (nouveau) : la commande de § 2.2, partagée par `_report`, le refus de `cmd_answer` et `cmd_sessions_show`. Environ 25 lignes ;
  - `cmd_answer` : la commande ajoutée sous un refus, le numéro hors des choix (`_numbered` rend aussi les numéros qu'il n'a pas pu convertir), l'écho (`_choices_by_number` lit déjà le résultat ; il rendra aussi les questions visées). Le contrôle local ne vaut que si **une seule** question est visée (comme la conversion des numéros aujourd'hui) ; sinon le moteur tranche, comme avant. Environ 35 lignes ;
  - `cmd_sessions_show` : réutilise `_questions_text` et `_answer_hint`. Environ 10 lignes.
- **`access/progress.py`** : la ligne de § 2.6. 3 lignes.
- **`access/mcp_server/server.py`** : `_asking` (§ 2.8), `_question_form` (§ 2.9), `_text` (§ 6.1). Environ 25 lignes.

Aucun type, événement, paramètre de config ni dépendance nouveau. Pyright strict et ruff, comme le reste.

## 5. Effets de bord

**Sur le comportement**

- **Numéro hors des choix refusé en CLI** : seul cas où un accès accepte moins qu'avant. Cas légitime perdu : une question à choix non numériques (« Aucun », « Un seul ») à laquelle on répond un nombre (« 3 ») voulu comme texte. Il reste possible par le message libre (`loom answer RUN "3"`), que le refus indique. Le SDK, le REST et le MCP ne sont pas concernés : ils n'ont pas de numéros.
- **Contrôle local du nombre de réponses en CLI** : il doublerait celui du moteur avec le risque de diverger. Il n'est fait que pour ajouter la commande sous le message ; la décision reste au moteur, qui reste le seul juge pour le SDK et le REST.

**Sur ce qui est lu par un programme**

- **Sortie standard de la CLI** : avec plusieurs questions, elle change (numérotation, intitulés). Un script qui lirait stdout pour y trouver les questions doit lire `--json` (`pending_questions`), inchangé. ⚠ à dire dans le CHANGELOG.
- **Texte MCP sans elicitation** : il est lu par le modèle du client. Un changement de tournure peut changer la façon dont ce modèle répond (un message par question au lieu d'un seul, par exemple). C'est la raison de la consigne « en un seul message ». À vérifier par un essai réel.
- **Un loom qui appelle un autre loom par MCP** : le texte de § 2.8 devient le résultat d'un outil, donc une partie de sa requête au modèle. Un rejeu à l'identique le sert depuis le journal (pas d'écart) ; une variante qui rappelle vraiment le serveur verra le nouveau texte. Avec une question, rien ne change.
- **Notifications de progression MCP** : avec plusieurs questions, les intitulés au lieu des textes (§ 2.6). Un client qui affichait la ligne verra « Relances ; Acompte » ; le détail reste dans le résultat et dans `structuredContent`.
- **Message de `InvalidAnswer` et du `422`** : un client qui comparerait le texte exact (au lieu du code) verra un autre texte. Les sous-chaînes « un seul choix » et « réponse vide » sont gardées.

**Sur le reste**

- **Rejeu et journal** : aucun effet. Le message de `reply_problem` ne part ni au modèle ni au journal (dans l'exécuteur, il ne sert qu'à un avertissement de log).
- **Coût et lectures** : nuls. L'écho réutilise le résultat que `_choices_by_number` lit déjà.
- **Sécurité du terminal** : intitulés et libellés viennent du modèle. Les lignes d'aide et l'écho passent par `_say` (`printable`), la question sur stdout par `_for_stdout`, comme aujourd'hui.
- **Formation** (`docs_loom_v2`) : les exemples à une question restent justes ; un exemple à deux questions serait à ajouter là où `ask` est présenté.

## 6. Défauts existants trouvés en passant

1. **MCP : une question cachée par une approbation.** `_text` rend `_awaiting` dès qu'une approbation attend, et n'appelle `_asking` que sinon. Un run qui attend une approbation **et** une question (cas possible : les deux attentes se cumulent) ne montre au client que l'approbation. Le client approuve, le run reprend, et se remet aussitôt en pause sur une question qu'il n'avait jamais vue. Correction : rendre les deux, l'approbation d'abord.
2. **CLI : numéro hors des choix accepté comme texte** (§ 2.3) : `--answer 3` sur deux choix envoie « 3 » au modèle, sans rien dire.
3. **CLI : `Répondu : <call_id>`** : l'identifiant n'apprend rien à l'utilisateur ; corrigé par § 2.4.
4. **CLI : `sessions show` sans les choix** des questions en attente ; corrigé par § 2.5.
5. **MCP : choix séparés par des virgules** dans le texte, alors qu'un libellé en contient souvent une (« Oui, relances faites… ») ; corrigé par § 2.8.

## 7. Plan de non-régression

- **Inchangés et à faire passer tels quels** :
  - `tests/unit/test_wait_answer.py` (moteur) : seul `match="un seul choix"` touche un message, et la sous-chaîne reste ;
  - `tests/unit/test_wait_answer_access.py` : les quatre essais CLI à une question ;
  - `tests/unit/test_wait_answer_mcp.py` : `"rappeler demo avec session_id=s1"` (gardé pour une question), le titre `"Client"` du champ de choix (inchangé : c'est le champ libre qui change), « question à l'utilisateur » dans la progression (une question) ;
  - corpus de rejeu `tests/regression` : inchangés.
- **Essais nouveaux**, avec un modèle simulé dont l'`ask` porte deux questions, dont une à choix multiples :
  - moteur : les trois messages, avec et sans intitulés ;
  - CLI : l'affichage (`Question 1/2 · …`, « plusieurs choix »), l'aide (`--answer <Relances> --answer <Acompte>`, intitulés en double), le refus du nombre de réponses avec la commande, le numéro hors des choix (refusé ; « 3 » accepté sur une question sans choix ; « 2 » pris comme libellé quand un libellé vaut « 2 »), l'écho (plusieurs questions, une question, message libre), `sessions show`, la ligne `--stream` ;
  - MCP : le texte à deux questions, le titre du champ libre, et le run qui attend une approbation et une question (les deux montrées) ;
  - REST : le `detail` du `422` avec les intitulés.
- **Essais réels** sur l'agent de test : le cas `t-2000` en CLI ; un cas à une question ; le même cas à deux questions par Claude Desktop en MCP, sans elicitation, pour voir s'il répond en un seul message.

## 8. Décisions à prendre

| # | Question | Proposé |
|---|---|---|
| D1 | Format de l'en-tête de question (CLI) | `Question 1/2 · Relances` ; sans intitulé, `Question 1/2` |
| D2 | Contenu des `<…>` de la ligne d'aide | l'intitulé, sinon `<réponse i>` ; suffixe de rang si deux intitulés sont identiques ; sans guillemets |
| D3 | Numéro hors des choix | refusé en CLI (code 2), seulement si la question a des choix et aucun libellé numérique ; le refus indique le message libre |
| D4 | Écho après `answer` | `intitulé → valeur`, une ligne par question ; message libre coupé à 110 caractères |
| D5 | `sessions show` | même bloc que `run`, choix compris |
| D6 | Une seule question | tous les rendus inchangés, sauf l'écho |
| D7 | Message de refus | corrigé à la source (`reply_problem`), pour la CLI, le SDK et le REST |
| D8 | Texte MCP sans elicitation | questions numérotées avec intitulés, choix séparés par « ; », consigne « en un seul message » |
| D9 | Formulaire MCP | `<intitulé> : autre réponse` pour le champ libre qui accompagne une liste |
| D10 | Défaut § 6.1 (approbation et question) | corrigé dans ce chantier : les deux attentes montrées, l'approbation d'abord |
| D11 | Version | reste 2.0.15 (non publiée) ; au CHANGELOG, une entrée « ⚠ Ligne de commande » et une ligne MCP |

## 9. Plan de mise en œuvre

0. **Lot 0, moteur** : les messages de `reply_problem` ; essais du moteur.
1. **Lot 1, affichage CLI** : `_questions_text`, `_answer_hint`, `_report`, `progress.py` ; essais d'affichage et d'aide.
2. **Lot 2, `loom answer`** : la commande sous un refus, le numéro hors des choix, l'écho ; essais.
3. **Lot 3, `sessions show`** ; essai.
4. **Lot 4, MCP** : `_asking`, `_question_form`, `_text` (§ 6.1) ; essais.
5. **Lot 5, docs** : `wait_answer.md` (§ CLI, § REST avec un exemple à deux questions, § MCP), `fonctions.md` (D13, `loom.ask`), `howto/06-outils-fournis.md`, CHANGELOG.

À chaque lot : ruff, format, pyright strict, essais unitaires du module, puis la suite complète au dernier lot. Puis les essais réels du § 7.
