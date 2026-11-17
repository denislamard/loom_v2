# loom_ia.engine.transcript

Module `loom_ia.engine.transcript` de loom_v2 (fichier `src/loom_ia/engine/transcript.py`).

Historique rendu en texte, pour un modèle qui le lit plutôt qu'il ne le poursuit.

Un segment de conversation ne peut pas être envoyé tel quel à un rôle ou à un
agent de compaction : ce sont ses messages, pas les leurs. On le rend donc en
texte suivi — qui a dit quoi, quel outil a été appelé, ce qu'il a répondu.

Les résultats d'outils sont tronqués : ce qui compte ici est le fil de la
conversation, pas le détail d'un gros JSON. Le texte rendu est celui que le
contrôle de fidélité compare au résumé (point 23), donc la troncature fait partie
de ce qui est vérifié.

Utilisé par la compaction (`sessions/compaction.py`), les rôles qui
reçoivent l'historique (`engine/roles.py`) et les juges
(`guards/judge.py`).

## Constantes de `loom_ia.engine.transcript`

- `MAX_RESULT_CHARS = 800` : Longueur maximale d'un résultat d'outil dans le texte rendu, en caractères.
- `CUT_MARK = '… (résultat tronqué)'` : Marque ajoutée à un résultat coupé.
- `TURN_SEPARATOR = '\n\n---\n\n'` : Séparateur entre deux tours.

## Fonction `loom_ia.engine.transcript.rendered`

```python
def rendered(messages: Sequence[Message]) -> str
```

Suite de messages rendue en texte suivi.

Chaque message devient une ou plusieurs lignes : « Utilisateur : … »,
« Agent : … », « Agent appelle <outil> : <arguments JSON> », « Résultat :
… » (coupé à 800 caractères). Le raisonnement n'y figure pas ; un fichier
se dit « [fichier <nom>] ».

**Arguments :**

- `messages` : Les messages, dans l'ordre.

**Renvoie :**

Le texte, paragraphes séparés par une ligne vide.

## Fonction `loom_ia.engine.transcript.transcript`

```python
def transcript(turns: Sequence[Sequence[Message]]) -> str
```

Tours rendus en texte, séparés par une ligne `---`.

**Arguments :**

- `turns` : Les tours, chacun une suite de messages.

**Renvoie :**

Le texte ; un tour vide n'y laisse pas de trace.

## Fonction `loom_ia.engine.transcript._body`

```python
def _body(message: Message) -> str
```

Le texte d'un message : textes, JSON et fichiers, sans raisonnement ni appels.

## Fonction `loom_ia.engine.transcript._output`

```python
def _output(output: ToolOutput) -> str
```

Le texte d'un résultat d'outil ; « erreur — <message> » pour un résultat en erreur.

## Fonction `loom_ia.engine.transcript._short`

```python
def _short(text: str) -> str
```

Le texte coupé à `MAX_RESULT_CHARS`, marqué s'il l'est.
