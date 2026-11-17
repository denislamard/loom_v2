# loom_ia.core

Paquet `loom_ia.core` de loom_v2 (fichier `src/loom_ia/core/__init__.py`).

Le noyau de loom : modèle de domaine, événements, ports et projections.

Le noyau ne dépend d'aucun fournisseur ni d'aucun stockage : il définit ce que
le moteur manipule, et les contrats que les adaptateurs remplissent. Il se
découpe ainsi :

- `core.model` : types du domaine (messages, blocs de contenu, `RunState`,
  consommation, définitions d'outils et de modèles), en Pydantic (point 6) ;
- `core.events` : le journal, c'est-à-dire l'enveloppe d'un événement et la
  charge typée de chaque type d'événement (point 22) ;
- `core.ports` : les contrats que remplissent les adaptateurs (journal, bus,
  file, client de modèle, outil, stockage d'artefacts, idempotence…) ;
- `core.projections` : ce qui se déduit du journal (historique envoyé au
  modèle, état d'un run, state de session, consommation, arbre des runs).

Les modules à la racine (`template`, `schema_errors`, `bounded_regex`,
`state_extract`, `tool_threads`, `closing`, `json_values`) sont des
utilitaires que plusieurs couches partagent.

Le module définit aussi les clés `extra` des logs que les commandes de la CLI
filtrent, pour ne pas dire deux fois la même chose.

## Constantes de `loom_ia.core`

- `TOLD = 'loom_told'` : Clé `extra` d'un log dont l'appelant reçoit aussi le contenu.  Cas typiques : un échec de run (`RunResult.error`), un refus levé (`BudgetExhausted`), une divergence de rejeu (le rapport). Le log sert l'exploitant d'un service, qui ne voit pas ce que reçoit le client. Une commande qui dit elle-même ce contenu écarte ce log de sa console, pour ne pas le dire deux fois (lot B du 23/10).
- `SETUP = 'loom_setup'` : Clé `extra` d'un avertissement de configuration, dit au montage d'une instance.  Il concerne ce qui tourne (stockages d'un service, collecteurs, sceau). Une commande qui ne fait que lire le journal l'écarte de sa console : à l'essai de la fonction H7 du 21/10, `loom sessions show` le répétait à chaque appel. `loom validate`, `loom run` et le service le disent toujours.
- `REPORTED = 'loom_reported'` : Clé `extra` d'un avertissement que `loom validate` reprend dans son rapport.  `loom validate` l'écarte de sa console, pour ne pas le dire deux fois. `loom run` et le service, qui n'ont pas de rapport, le disent comme les autres avertissements.
- `AUDIT = 'loom_audit'` : Clé `extra` du détail d'un refus d'accès (`access/audit.py`, décision D-9 des utilisateurs et rôles).  Le détail dit qui, quoi, par quel accès et pourquoi. Le format `json` des logs le rend sous la clé `audit`, pour qu'un collecteur le filtre sans relire le message.
