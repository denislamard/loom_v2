# loom-ia V2 — Backlog

Points à traiter, numérotés dans l'ordre d'ajout (#001, #002…). Les renvois `#n` sans zéros pointent vers les points de conception de `fonctions.md`.

---

## #001 — Configurer le contenu écrit dans le journal

**Origine :** relecture d'un événement `run.completed` pendant la phase 1.2.

**Constat :** le JSON du journal est verbeux. Les valeurs par défaut (`cache_breakpoint: false`, `provider_meta: {}`, tokens à 0…) sont écrites sur chaque ligne.

**Contrainte :** le journal fait foi (#22) et contient toujours les contenus complets (#30). On peut configurer la forme et ce qui s'ajoute au journal, pas retirer ce dont la reprise, l'historique et le rejeu ont besoin.

**Réglages envisagés :**

| Réglage | Effet | Existe déjà ? |
|---|---|---|
| Valeurs par défaut omises | Lignes plus courtes, relecture identique | Non, nouveau |
| Compression (par exemple gzip des fichiers JSONL clos) | Moins d'espace disque | Non, nouveau |
| Seuil de déport (`offload_over`) | Les gros résultats vont dans le stockage d'artefacts ; le journal garde une référence | Oui (#16) |
| Échanges HTTP bruts (`raw_exchanges`) | Ajoute les requêtes et réponses complètes des fournisseurs, pour le débogage | Oui, désactivé par défaut (#31) |
| Chiffrement par client | Contenus illisibles sans la clé | Oui, en option (#30) |
| Rétention (`retention.events_days`) | Suppression des journaux anciens | Oui (#50) |

**Non désactivable :** contenus des messages et des résultats d'outils, `step.*`, `run.transitioned`, décisions des politiques. Les événements éphémères (`model.delta`) ne sont jamais écrits : c'est une règle, pas un réglage.

**Config envisagée :**

```yaml
storage:
  events:
    backend: jsonl
    path: data/events
    serialization: {omit_defaults: true, compress_closed: false}
```

**Statut :** à traiter plus tard.

---

## #002 — Emplacement de la règle sur les dépendances du noyau

**Origine :** point #44.

**Constat :** la règle « toute nouvelle dépendance du noyau est justifiée par écrit » n'a pas d'emplacement.

**Options :** une section de `docs/conception.md` (§19), un `CONTRIBUTING.md`, ou un fichier dédié dans `docs/`.

**Statut :** à décider.

---

## #003 — Mettre à jour les mentions « jalons à revoir »

**Constat :** `docs/fonctions.md` (point #47, partie « Ouverts ») et `docs/conception.md` (§20) indiquent encore « jalons à revoir », alors que `docs/jalons.md` existe.

**À faire :** passer #47 en décidé dans `fonctions.md` avec un renvoi vers `jalons.md`, et retirer la ligne correspondante du §20 de `conception.md`.

**Statut :** à faire.

---

## #004 — Retirer les mentions de respx dans les docs

**Origine :** phase 1.4.

**Constat :** les SDK `anthropic` (1.6) et `openai` (3.14) utilisent `httpx2`, que respx n'intercepte pas. Les tests de contrat des adaptateurs simulent donc les réponses avec `httpx2.MockTransport`, passé au SDK par `http_client`. respx a été retiré des dépendances de dev.

**À faire :** remplacer « respx » par `httpx2.MockTransport` dans `docs/fonctions.md` (#9 et outils de dev), `docs/conception.md` (§10.1 et §19) et `docs/jalons.md` (principes et tests du J1).

**Statut :** à faire.

---

## #005 — `model.retried` écrit dès la phase 1.4

**Origine :** phase 1.4.

**Constat :** `docs/jalons.md` place `model.retried` en phase 3.5 ; il est écrit dès la 1.4, avec le retry.

**À faire :** retirer `model.retried` de la ligne 3.5 et le mentionner en 1.4 (`fell_back` reste en 3.5).

**Statut :** à faire.

---

## #006 — `timeout` d'agent : nommer la phase qui l'apportera

**Origine :** phase 1.6.

**Constat :** la clé `timeout` d'un agent est refusée avec le message « J1.6 (cycle de vie des runs) » (`agents/spec.py`, `LATER_AGENT`). Or la ligne 1.6 de `docs/jalons.md` ne couvre que les trois accès : délai maximal d'un run, annulation et expiration ne sont décrits nulle part.

**À décider :** où atterrit le cycle de vie d'un run (délai, annulation, `run.cancelled`) — J3 avec les politiques et les budgets, ou J4 avec les sessions et le bus. Le message de refus suivra.

**Statut :** à décider.

---

## #007 — Nom de l'exemple des trois accès

**Origine :** phase 1.6.

**Constat :** `docs/jalons.md` annonce `examples/j1/run.py` pour l'accès Python ; l'exemple livré est `examples/j1/acces.py`, et il montre les trois accès plutôt que le seul accès Python (`run.py` se confondrait d'ailleurs avec la commande `loom run`).

**À faire :** renommer le fichier, ou corriger la ligne du J1 dans `jalons.md`.

**Statut :** à décider.
