# loom_ia.config.compaction

Module `loom_ia.config.compaction` de loom_v2 (fichier `src/loom_ia/config/compaction.py`).

Agent interne de compaction, tiré de la configuration (point 23).

Résumer une session est un appel de modèle comme un autre : plutôt qu'un
chemin à part, `sessions.compaction` produit un agent ordinaire, monté comme
les autres. Il hérite ainsi du retry, du modèle de secours, du suivi des
coûts, des spans et de la réparation, sans rien recâbler.

Il n'a pas d'outils, une seule itération, et n'est publié ni en REST ni en
MCP. Son nom est réservé : un agent déclaré ainsi est refusé au chargement.

Exemple :

```
sessions:
  compaction:
    model: HAIKU
    over_tokens: 12000
    keep_last: 6
```

## Constantes de `loom_ia.config.compaction`

- `COMPACTION_AGENT = '_compaction'` : Nom réservé de l'agent interne.
- `COMPACTION_SYSTEM = "Tu résumes les échanges anciens d'une conversation entre un utilisateur et un\nagent, pour que la suite tienne dans le…` : Prompt système de l'agent interne, sauf `system_file`.

## Classe `loom_ia.config.compaction.CompactionConfig`

```python
class CompactionConfig(DomainModel)
```

Résumé d'une session devenue longue (point 23, conception §11.3).

Bloc `sessions.compaction` de la config.

**Attributs :**

- `model` : Modèle dédié (identifiant dans `models`), en général moins cher que celui de l'agent.
- `over_tokens` : Taille d'historique, en tokens estimés, au-delà de laquelle un résumé est mis en file à la fin d'un run (12 000 par défaut).
- `hard_tokens` : Taille au-delà de laquelle il devient bloquant à l'ouverture d'un run (150 000 par défaut) ; doit dépasser `over_tokens`.
- `keep_last` : Tours laissés intacts à la fin de l'historique (un tour = un run ; 6 par défaut).
- `fidelity_check` : Contrôle déterministe des repères du segment dans le résumé.
- `blocking` : Vrai : le résumé dû se fait à l'ouverture du run suivant, qui l'attend, au lieu d'une tâche de fond après le run. Rien ne reste alors en cours quand un process de courte vie (CLI) se ferme.
- `system_file` : Prompt interne surchargé, relatif au dossier des ressources.

## Fonction `loom_ia.config.compaction.compaction_agent`

```python
def compaction_agent(spec: CompactionConfig) -> AgentSpec
```

Agent interne qui résume un segment de session.

**Arguments :**

- `spec` : Le bloc `sessions.compaction`.

**Renvoie :**

L'agent `_compaction` : sans outils, une itération, publié ni en REST
ni en MCP.
