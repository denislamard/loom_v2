# loom_ia.tools_builtin._rights

Module `loom_ia.tools_builtin._rights` de loom_v2 (fichier `src/loom_ia/tools_builtin/_rights.py`).

Droits accordés et approbation de chacun : la normalisation commune des outils fournis.

Les dossiers de `loom.local_files` et `loom.excel`, comme les tables de
`loom.sql`, se déclarent avec deux clés, une par notion :

    rights: [read, write, overwrite, delete]        # accordés ; le reste est refusé
    approval: { overwrite: never, delete: never }   # facultatif : qui attend un humain

`resolve` les ramène à la forme que lit l'outil, ``dict[droit, "never" |
"always"]` : un droit présent est accordé, et `always`` le met sous
approbation. Un droit absent d'`approval` garde son approbation par défaut.

L'ancienne écriture, une table droit → approbation dans `rights`, est
refusée : `rights: { delete: never }` se lisait « suppression interdite »
et l'accordait sans rien demander. Le message donne l'écriture équivalente.

Module léger, sans dépendance : la config l'importe, par les modules de
paramètres, sans importer les outils.

## Constantes de `loom_ia.tools_builtin._rights`

- `RightApproval = Literal['never', 'always']` : Approbation d'un droit : `never` (l'appel passe) ou `always` (validation humaine).
- `Form = Literal['folder', 'table']` : Comment dire l'écriture équivalente : clés d'un dossier, ou valeur d'une table SQL.
- `APPROVALS = ('never', 'always')` : Les approbations possibles.

## Fonction `loom_ia.tools_builtin._rights.resolve`

```python
def resolve[R: str](rights: object, approval: object, *, known: Sequence[R], defaults: Mapping[R, RightApproval], where: str, form: Form) -> dict[str, object]
```

La table interne droit → approbation ; `ValueError` dit ce qui ne va pas.

`known` donne les droits de l'outil dans l'ordre où on les dit : la table
rendue le suit, quel que soit l'ordre de la config. Un droit inconnu y
reste, à la fin : le modèle de l'outil le refuse ensuite, en le nommant.
`where` nomme le dossier ou la table dans les messages.

## Fonction `loom_ia.tools_builtin._rights._approval`

```python
def _approval(approval: object, *, given: Sequence[str], known: Sequence[str], where: str) -> dict[str, RightApproval]
```

La table `approval` lue : chaque droit doit être connu, accordé, et valoir `never` ou `always` ; `ValueError` sinon.

## Fonction `loom_ia.tools_builtin._rights._legacy`

```python
def _legacy(table: Mapping[object, object], *, known: Sequence[str], defaults: Mapping[str, RightApproval], where: str, form: Form) -> str
```

Le refus de l'ancienne table, avec l'écriture équivalente, prête à recopier.
