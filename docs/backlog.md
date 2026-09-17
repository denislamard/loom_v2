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
