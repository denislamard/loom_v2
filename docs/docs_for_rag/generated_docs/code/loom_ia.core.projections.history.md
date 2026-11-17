# loom_ia.core.projections.history

Module `loom_ia.core.projections.history` de loom_v2 (fichier `src/loom_ia/core/projections/history.py`).

Historique d'une session envoyé au modèle (points 7 et 22, fonction F2).

Seuls les runs racine terminés avec succès y figurent : un run échoué peut
contenir un appel d'outil sans résultat, que les API refusent. Un run en
pause non plus ; ce qu'il attend est dit au tour suivant par une note de la
requête (`engine/pending.py`), sans quoi le modèle proposerait de refaire
l'action en attente. Les sous-runs,
écrits dans le même journal, n'en font pas partie. Le raisonnement et les
textes vides sont retirés ; un message qui ne contient plus rien est omis
(certaines API refusent un bloc de texte vide).

Sortie d'un outil terminal (point 13) : le résultat est remplacé par un marqueur,
et la sortie suit en message `assistant`. Elle n'est pas dupliquée, et la
séquence reste valide pour les API (résultat d'outil, puis assistant).

Réponse écrite avec `loom.state` (règle M1, `docs/state_analyse_moteur.md`) :
le texte quitte le tour qui appelait
`state`, et la réponse retenue le suit en message `assistant`, comme une
sortie terminale ; une réponse remplacée par une politique y figure sous sa
forme retenue.

Réparations (point 20) : une réponse refusée par une politique et le diagnostic
qui la suit (`message.user` de `kind: repair`) restent dans le journal,
mais pas dans l'historique : seule la réponse acceptée y figure. Une réponse
finale remplacée par une politique y figure sous sa forme retenue.

Marqueurs de session (phase 4.1) : un `session.snapshot` porte l'historique déjà
calculé jusqu'à une position du journal, un `session.compacted` le résumé
qui le remplace (point 23), un `session.trimmed` la coupe faite sans résumé.
L'historique repart du dernier marqueur écrit, puis rejoue la suite. Sans
marqueur, tout le journal est relu : le résultat est le même, la relecture
plus longue.

Le résumé entre dans l'historique comme un message de l'utilisateur, précédé
d'une ligne qui le désigne. Les adaptateurs fusionnent les messages
consécutifs d'un même rôle : il se colle donc au tour suivant sans casser
l'alternance attendue par les fournisseurs.

Un run de compaction (`kind: compaction`) vit dans le journal de la
session mais n'entre pas dans son historique : il la résume, il ne la
poursuit pas.

## Constantes de `loom_ia.core.projections.history`

- `TERMINAL_MARKER = '[Sortie transmise telle quelle comme réponse finale.]'` : Texte qui remplace, dans l'historique, le résultat d'un outil terminal : la sortie suit en message `assistant`.
- `SUMMARY_MARKER = '[Résumé des échanges précédents de cette conversation.]'` : Ligne qui précède le résumé de compaction, entré dans l'historique comme un message de l'utilisateur.

## Fonction `loom_ia.core.projections.history.history`

```python
def history(events: Iterable[Event]) -> list[Message]
```

Historique de la session tel qu'il part au modèle : les messages des runs racine terminés.

**Arguments :**

- `events` : Le journal de la session, dans l'ordre.

**Renvoie :**

Les messages, dans l'ordre, à partir du dernier marqueur de session
(snapshot, résumé, coupe) ; raisonnement et textes vides retirés.

## Fonction `loom_ia.core.projections.history.turns`

```python
def turns(events: Iterable[Event]) -> list[tuple[Message, ...]]
```

Historique groupé par tour : un tour est un run racine terminé.

Le premier tour, s'il existe, est ce que reprend le dernier marqueur de
session (résumé de compaction, ou snapshot). Grouper ainsi permet de
couper l'historique sur une frontière de run (`keep_last`,
`last_turns`), là où une coupe au message séparerait un appel d'outil
de son résultat.

**Arguments :**

- `events` : Le journal de la session, dans l'ordre.

**Renvoie :**

Un tuple de messages par tour, dans l'ordre.

## Fonction `loom_ia.core.projections.history.last_summary`

```python
def last_summary(events: Iterable[Event]) -> str | None
```

Dernier résumé de compaction de la session, ou `None` (point 23).

Un juge le reçoit s'il le déclare dans son contexte (point 12).

## Fonction `loom_ia.core.projections.history._marker`

```python
def _marker(event: Event) -> tuple[int, tuple[Message, ...]] | None
```

Position couverte et historique repris, pour un marqueur de session.

## Fonction `loom_ia.core.projections.history.last_marker`

```python
def last_marker(events: Iterable[Event]) -> tuple[int, tuple[Message, ...]] | None
```

Dernier marqueur de session écrit : position couverte et base reprise.

C'est le **dernier écrit** qui fait foi, pas celui qui couvre le plus de
journal : une compaction écrite après un snapshot le périme, puisqu'elle
réécrit la région qu'il portait. Le snapshot rafraîchi qui suit une
compaction contient déjà le résumé, et reprend la main.

**Arguments :**

- `events` : Le journal de la session, dans l'ordre.

**Renvoie :**

Le dernier `seq` couvert et l'historique repris (vide pour une
coupe), ou `None` sans marqueur.

## Fonction `loom_ia.core.projections.history._from_marker`

```python
def _from_marker(events: list[Event]) -> tuple[tuple[Message, ...], list[Event]]
```

Base de l'historique et événements restant à rejouer.

## Fonction `loom_ia.core.projections.history._replayed`

```python
def _replayed(events: list[Event]) -> list[RunState]
```

États des runs dont le journal commence dans ces événements.

Un run commencé avant le marqueur y est déjà pris en compte : le reprendre
au milieu lèverait une erreur de projection.

## Fonction `loom_ia.core.projections.history._conversation`

```python
def _conversation(state: RunState) -> tuple[Message, ...]
```

Messages du run, sortie terminale comprise, sans les tentatives refusées.

## Fonction `loom_ia.core.projections.history._without_text_of_last_turn`

```python
def _without_text_of_last_turn(messages: tuple[Message, ...]) -> tuple[Message, ...]
```

Les messages, sans le texte de la dernière réponse de l'assistant (ses appels restent).

## Fonction `loom_ia.core.projections.history._accepted`

```python
def _accepted(state: RunState) -> tuple[Message, ...]
```

Messages du run sans les réponses refusées ni leurs diagnostics.

## Fonction `loom_ia.core.projections.history.repaired`

```python
def repaired(state: RunState) -> set[int]
```

Positions, dans les messages du run, de ce que les réparations retirent.

Chaque réparation retire la dernière réponse de l'assistant qui la
précède, les résultats d'outils qui suivent cette réponse, et le
diagnostic : la séquence reste valide pour les API.

**Arguments :**

- `state` : L'état du run, dont `repairs` donne la position de chaque diagnostic.

**Renvoie :**

Les positions retirées, dans `state.messages`.

## Fonction `loom_ia.core.projections.history._without_empty_text`

```python
def _without_empty_text(message: Message) -> Message | None
```

Le message sans ses blocs de texte vides ; `None` s'il ne reste rien (certaines API refusent un texte vide).
