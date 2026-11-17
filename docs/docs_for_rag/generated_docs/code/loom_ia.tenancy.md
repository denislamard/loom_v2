# loom_ia.tenancy

Paquet `loom_ia.tenancy` de loom_v2 (fichier `src/loom_ia/tenancy/__init__.py`).

Multi-clients : contexte, surcharges, secrets, isolation, budgets (fonctions L1 à L3).

- `registry` : les clients déclarés (`tenants`) et leurs surcharges ;
- `secrets` : table des secrets de chaque client ;
- `router` : journal et magasin de fichiers propres à un client ;
- `quota` : runs par minute d'un client, débit d'une clé d'API ;
- `usage` et `period` : budgets par client et par jour ou par mois.
