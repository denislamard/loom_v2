# loom_ia.core.model.messages

Module `loom_ia.core.model.messages` de loom_v2 (fichier `src/loom_ia/core/model/messages.py`).

Messages neutres échangés avec les modèles (fonction B2).

Trois rôles : `user`, `assistant` et `tool` (résultats d'outils).
Le prompt système n'est pas un message : il appartient à la requête. Chaque
adaptateur traduit les messages vers le format de son fournisseur, à l'appel
seulement.

Le moteur ajoute à certaines requêtes des messages `user` qui ne sont pas
des demandes : le state de la session (`is_session_state`) et les notes du
système (`is_system_note`).

## Constantes de `loom_ia.core.model.messages`

- `Role = Literal['user', 'assistant', 'tool']` : Rôle d'un message : `user`, `assistant` ou `tool` (résultats d'outils).
- `SESSION_STATE_TAG = 'session_state'` : Balise du state de la session que le moteur ajoute à la fin d'une requête de l'orchestrateur.  Décision D4 du state de session : `<session_state>{…}</session_state>`. Ce message n'est jamais journalisé comme message.
- `SYSTEM_NOTE = 'Note du système'` : Début des notes que le moteur ajoute à une requête, entre crochets : « [Note du système : …] ».  Une note dit au modèle ce qu'il ne lit pas ailleurs : une approbation en attente, des arguments remplacés.

## Classe `loom_ia.core.model.messages.Message`

```python
class Message(DomainModel)
```

Un message de la conversation, neutre vis-à-vis du fournisseur.

**Attributs :**

- `role` : `user`, `assistant` ou `tool`.
- `blocks` : Ses blocs, au moins un.

**Lève :**

- `ValueError` : Message sans bloc ; résultat d'outil hors d'un message `tool` (ou message `tool` qui porte autre chose) ; appel d'outil ou raisonnement hors d'un message `assistant`.

### Méthode `loom_ia.core.model.messages.Message._check_blocks`

```python
@model_validator(mode='after')
def _check_blocks(self) -> Self
```

Vérifie que chaque bloc est permis dans un message de ce rôle.

### Méthode `loom_ia.core.model.messages.Message.user`

```python
@classmethod
def user(cls, text: str) -> Self
```

Message `user` fait d'un seul bloc de texte.

### Méthode `loom_ia.core.model.messages.Message.assistant`

```python
@classmethod
def assistant(cls, text: str) -> Self
```

Message `assistant` fait d'un seul bloc de texte.

### Méthode `loom_ia.core.model.messages.Message.text`

```python
@property
def text(self) -> str
```

Contenu des blocs `text`, concaténé.

### Méthode `loom_ia.core.model.messages.Message.tool_calls`

```python
@property
def tool_calls(self) -> tuple[ToolCallBlock, ...]
```

Appels d'outils du message, dans l'ordre.

### Méthode `loom_ia.core.model.messages.Message.without_reasoning`

```python
def without_reasoning(self) -> Self | None
```

Copie sans blocs de raisonnement ; le message lui-même s'il n'en avait pas, `None` si plus rien ne reste.

## Fonction `loom_ia.core.model.messages.is_session_state`

```python
def is_session_state(message: Message) -> bool
```

Vrai pour le message du state de la session ajouté par le moteur à une requête (décision D4 du state de session).

Un adaptateur pose son point de cache avant lui, car c'est la partie de la
requête qui change d'un appel à l'autre ; le faux modèle ne le prend pas
pour une demande de l'utilisateur. Le moteur échappe la balise dans les
valeurs du state : seul un message qui commence par elle passe pour lui.

## Fonction `loom_ia.core.model.messages.is_system_note`

```python
def is_system_note(message: Message) -> bool
```

Vrai pour un message `user` qui n'est qu'une note du moteur, pas une demande.

Le faux modèle ne compte pas ses réponses à partir d'elle : la note des
arguments remplacés suit les résultats d'un tour, au milieu de la boucle.
