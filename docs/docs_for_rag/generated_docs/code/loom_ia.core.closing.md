# loom_ia.core.closing

Module `loom_ia.core.closing` de loom_v2 (fichier `src/loom_ia/core/closing.py`).

Fermer plusieurs ressources sans que l'échec de l'une épargne les autres.

Une fermeture qui s'arrête à la première exception laisse ouvert tout ce
qu'elle n'a pas atteint : une connexion MCP qui plante, et c'est le journal
qui reste ouvert derrière elle — avec `aiosqlite`, un fil qui empêche le
process de sortir.

## Classe `loom_ia.core.closing.Closing`

```python
class Closing
```

Mène une fermeture à son terme : chaque étape est tentée, même si une précédente a levé.

Chaque étape se place dans un `with closing:` ; si elle lève, l'exception
est mise de côté (le `with` l'avale) et la suivante est tentée.
`raise_if_failed` lève ensuite **une** exception, celle que l'appelant
aurait reçue sans ce relais : la première, sauf qu'une annulation ou une
interruption (`CancelledError`, `KeyboardInterrupt`, toute
`BaseException` qui n'est pas une `Exception`) passe avant, pour ne
jamais être avalée par une erreur ordinaire. Les autres sont journalisées
en avertissement, avec leur trace.

Un `Closing` ne sert qu'à une fermeture : il garde ses échecs.

**Exemples :**

Fermer une liste de ressources ::

    closing = Closing()
    for resource in resources:
        with closing:
            await resource.aclose()
    closing.raise_if_failed()

### Méthode `loom_ia.core.closing.Closing.raise_if_failed`

```python
def raise_if_failed(self) -> None
```

Lève l'échec qui compte, s'il y en a eu un ; journalise les autres.

Ne fait rien si aucune étape n'a levé.

**Lève :**

- `BaseException` : La première interruption ou annulation mise de côté (une `BaseException` qui n'est pas une `Exception`) ; à défaut, la première exception. Les autres sont journalisées (« Fermeture : une autre étape a échoué aussi »).
