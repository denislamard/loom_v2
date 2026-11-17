# loom_ia.engine.amendments

Module `loom_ia.engine.amendments` de loom_v2 (fichier `src/loom_ia/engine/amendments.py`).

Mention, dans la requête, des appels partis avec d'autres arguments que ceux du modèle.

Un approbateur qui corrige un appel, ou une politique `before_tool` qui en
remplace les arguments, réécrit l'appel dans la conversation : le modèle lit
ce qui est parti (21/09). Mais il croit alors l'avoir écrit, et s'attribue la
correction (« je l'ai inventé ») ou cherche à la défaire. Une note du système
dit donc qui a changé quoi.

Elle suit les résultats du tour, dans un message à elle, et non dans le
résultat de l'outil : placée dans le résultat, elle se lit comme une donnée de
l'outil, dont le modèle se méfie. À l'essai de la fonction H4 du 20/10, une correction qui
contredisait la demande (95 € au lieu des 90 € demandés) a été prise pour une
erreur du modèle (« une modification faite par « denis » que je ne peux pas
vérifier »), qui proposait de la défaire.

Comme les références `[result:n]`, la mention n'existe que dans la requête :
le journal garde les résultats tels quels, et un résultat devenu réponse finale
(outil terminal) n'en porte pas. Elle ne nomme que les champs : les nouvelles
valeurs sont dans l'appel réécrit, les anciennes peuvent être longues ou
sensibles — c'est aussi ce qui permet à une politique de filtrer sans dire quoi.

## Constantes de `loom_ia.engine.amendments`

- `PREFIX = SYSTEM_NOTE` : Début de la note : « Note du système ».
- `EXECUTED = "c'est exactement ce qui a été exécuté. Tes arguments d'origine n'ont pas été utilisés."` : Phrase qui dit au modèle que l'appel affiché est celui qui est parti.

## Fonction `loom_ia.engine.amendments.amendment_note`

```python
def amendment_note(amendment: Amendment, tool: str | None=None) -> str
```

Texte de la mention d'un changement d'arguments.

Le modèle lit l'appel réécrit dans son propre tour, et croit d'abord l'avoir
écrit. Au test du 18/10, « Appel modifié par denis » laissait Haiku
s'attribuer la signature ajoutée par l'approbateur ; « L'appel ci-dessus
n'est pas celui que tu as écrit » lui faisait croire qu'une autre version,
invisible, était partie. La mention dit donc les trois choses : qui a
remplacé quoi, que l'appel affiché porte déjà ces arguments, et que les
siens n'ont pas servi. Pour un approbateur, elle ajoute que c'est sa
décision, à ne pas défaire (essai de la fonction H4 du 20/10 : sans cela,
une correction contraire à la demande était prise pour une erreur à
réparer).

**Arguments :**

- `amendment` : Le changement : qui, quels champs, pourquoi.
- `tool` : Nom de l'outil, s'il est connu.

**Renvoie :**

Le texte de la note, sans les crochets.

## Fonction `loom_ia.engine.amendments.note_amendments`

```python
def note_amendments(messages: Sequence[Message], amendments: Sequence[Amendment]) -> tuple[Message, ...]
```

Messages d'une requête, avec la note de chaque appel modifié après les résultats de son tour.

La note part dans un message `user` placé après le dernier message
`tool` du tour (les adaptateurs le fusionnent derrière les résultats) :
même place que les images renvoyées hors des résultats (`engine/media.py`).

**Arguments :**

- `messages` : Les messages de la requête.
- `amendments` : Les changements d'arguments du run.

**Renvoie :**

Les messages, avec les notes insérées ; les mêmes s'il n'y en a pas.
