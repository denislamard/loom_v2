# loom_ia.core.model.context

Module `loom_ia.core.model.context` de loom_v2 (fichier `src/loom_ia/core/model/context.py`).

Contexte fourni par l'appelant d'un run (fonction A8).

## Classe `loom_ia.core.model.context.CallerContext`

```python
class CallerContext(DomainModel)
```

Qui lance un run, et ce qu'il y joint : transmis aux outils, aux politiques et aux traces.

Écrit dans le `run.started` du run (`context`) ; `metadata` est du
contenu, retiré sans la permission `read_content`.

**Attributs :**

- `tenant_id` : Client du run. Défaut : `default`, le client implicite du mode librairie.
- `user_id` : Utilisateur au nom duquel le run est lancé : le lanceur, dont dépend l'auto-approbation (décision D-7 des utilisateurs et rôles).
- `via` : Passerelle qui l'a lancé en son nom (`as_user`, décision D-5 des utilisateurs et rôles) ; absente sans passerelle.
- `metadata` : Métadonnées libres de l'appelant (JSON), transmises aux outils (`ToolContext.caller`).
