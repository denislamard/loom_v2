# loom_ia.adapters.models.fake

Module `loom_ia.adapters.models.fake` de loom_v2 (fichier `src/loom_ia/adapters/models/fake.py`).

Adaptateur `sdk: fake` : modèle scripté, sans réseau ni clé (fonction O2).

Le script est lu dans `params.script` : une liste de réponses, chacune avec
un texte, des appels d'outils et éventuellement un raisonnement.

    models:
      - id: FAKE
        sdk: fake
        model: fake-1
        params:
          script:
            - tool_calls: [{name: calculer, arguments: {expr: "12*7+3"}}]
            - text: "12 * 7 + 3 = 87"

Le modèle est sans état : la réponse est choisie selon le nombre de réponses
déjà données depuis la dernière demande de l'utilisateur. Le même script sert
donc à chaque run, quel que soit le point d'accès. Sans script, le modèle
renvoie la demande en écho.

Une réponse peut dépendre des outils proposés au modèle : `with_tool` la
garde seulement si cet outil est proposé, `without_tool` seulement s'il ne
l'est pas. Un même script sert ainsi un run avec pièce jointe (rôle vision
visible) et un run sans (rôle masqué).

          - text: Je regarde la photo.
            with_tool: decrire_image
            tool_calls: [{name: decrire_image, arguments: {consigne: "Décris-la."}}]

Une réponse peut aussi dépendre de la demande : `with_text` la garde
seulement si le dernier message de l'utilisateur contient ce texte,
`without_text` seulement s'il ne le contient pas. Un rôle, appelé à chaque
fois avec un seul message, répond ainsi selon ce qu'on lui demande.

Une réponse peut être une panne : `error` (`transient`, `overloaded`,
`quota_exhausted`…) fait lever l'erreur de ce type à la place de la réponse,
à chaque tentative. Un modèle dont le script ne contient que ``- error:
overloaded`` est en panne pour tous les appels : de quoi montrer un secours
(point 10).

Sans HTTP, pas d'échange brut à garder : quand le moteur ouvre un registre
(`telemetry.capture.raw_exchanges`, jalon J6.1b), le modèle y dépose un échange
**synthétique** — la requête reçue et la réponse rendue, en JSON, marquées
`synthetic` — pour que le chemin des échanges s'éprouve sans réseau.

Comme un fournisseur, le modèle respecte `tool_choice` : avec `required`,
une réponse sans appel d'outil est une erreur du script ; avec `none`, une
réponse qui appelle des outils donne à la place son texte `forced` (la
réponse forcée d'un run arrêté), ou à défaut son texte. Un diagnostic de
réparation (point 20) n'est pas une nouvelle demande, ni la consigne de la réponse
forcée : le script continue.

## Constantes de `loom_ia.adapters.models.fake`

- `PROVIDER = 'fake'` : Nom du fournisseur, tel que le journal et la télémétrie l'écrivent.

## Classe `loom_ia.adapters.models.fake.FakeToolCall`

```python
class FakeToolCall(DomainModel)
```

Un appel d'outil du script.

**Attributs :**

- `name` : Nom de l'outil.
- `arguments` : Ses arguments.

## Classe `loom_ia.adapters.models.fake.FakeReply`

```python
class FakeReply(DomainModel)
```

Une réponse du script : texte, appels d'outils, raisonnement, conditions et panne.

**Attributs :**

- `text` : Texte de la réponse.
- `tool_calls` : Appels d'outils de la réponse.
- `reasoning` : Raisonnement rendu avant le texte.
- `with_tool` : Réponse gardée seulement si cet outil est proposé au modèle.
- `without_tool` : Réponse gardée seulement si cet outil n'est pas proposé.
- `with_text` : Réponse gardée seulement si la dernière demande contient ce texte.
- `without_text` : Réponse gardée seulement si elle ne le contient pas.
- `forced` : Texte rendu à la place des appels d'outils quand la requête les interdit (`tool_choice: none`).
- `error` : Panne simulée : erreur de ce type levée à la place de la réponse.

### Méthode `loom_ia.adapters.models.fake.FakeReply.fits`

```python
def fits(self, tools: set[str], asked: str) -> bool
```

Vrai si les outils proposés et la dernière demande remplissent les conditions de la réponse.

Conditions : `with_tool`, `without_tool`, `with_text`,
`without_text`.

**Arguments :**

- `tools` : Noms des outils proposés au modèle.
- `asked` : Texte de la dernière demande de l'utilisateur.

## Classe `loom_ia.adapters.models.fake.FakeModel`

```python
class FakeModel
```

Client `ModelClient` scripté par la config.

### Méthode `loom_ia.adapters.models.fake.FakeModel.provider`

```python
@property
def provider(self) -> str
```

Nom du fournisseur : `fake`.

### Méthode `loom_ia.adapters.models.fake.FakeModel.aclose`

```python
async def aclose(self) -> None
```

Rien à fermer.

### Méthode `loom_ia.adapters.models.fake.FakeModel._deposit`

```python
def _deposit(self, request: ModelRequest, message: Message | None, error: str | None) -> None
```

Échange synthétique, si le moteur en garde : ce qui a été reçu et rendu.

### Méthode `loom_ia.adapters.models.fake.FakeModel.stream`

```python
async def stream(self, request: ModelRequest) -> AsyncGenerator[ModelChunk]
```

Rend la réponse du script qui suit, en morceaux neutres.

La réponse est choisie parmi celles qui conviennent, selon le nombre de
réponses déjà données depuis la dernière demande ; sans script, l'écho de
la demande. `tool_choice: none` remplace les appels d'outils par le
texte `forced` ; la consommation est estimée sur la taille des messages.

**Arguments :**

- `request` : La requête neutre.

**Produit :**

Les morceaux de la réponse.

**Lève :**

- `ModelError` : Script épuisé, réponse sans appel d'outil quand la requête en impose un (`invalid_request`), ou panne simulée (`error`).

## Fonction `loom_ia.adapters.models.fake._turn`

```python
def _turn(request: ModelRequest) -> int
```

Réponses déjà données depuis la dernière demande de l'utilisateur.

Ne sont pas des demandes : le message qui porte les images des résultats
d'outils, le diagnostic d'une réparation, la consigne de la réponse forcée,
le state de la session, une note du système.

## Fonction `loom_ia.adapters.models.fake._engine_note`

```python
def _engine_note(message: Message, *, first: bool) -> bool
```

Message ajouté par le moteur : images déplacées, réparation, réponse forcée, state, note.

Le state n'est jamais le premier message (`first`) : la demande le précède.
Un message de rôle qui commence par sa section `<session_state>` est une demande.

## Fonction `loom_ia.adapters.models.fake._last_user_text`

```python
def _last_user_text(request: ModelRequest) -> str
```

Dernière demande de l'utilisateur, sans les messages ajoutés par le moteur.
