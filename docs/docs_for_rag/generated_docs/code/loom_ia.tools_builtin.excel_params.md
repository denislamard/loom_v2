# loom_ia.tools_builtin.excel_params

Module `loom_ia.tools_builtin.excel_params` de loom_v2 (fichier `src/loom_ia/tools_builtin/excel_params.py`).

Paramètres de `loom.excel` : les dossiers permis, le recalcul par LibreOffice, les graphiques.

Module léger — Pydantic et `_office_params` seulement — : la config
l'importe pour valider la référence au chargement, sans importer l'outil ni
openpyxl.

    - python: loom.excel
      folders:
      - name: donnees
        path: donnees
        rights: [ read ]               # les CSV / JSON que l'outil peut lire
      - name: sortie
        path: sortie
        rights: [ write, overwrite ]   # remplacer un classeur : approbation par défaut
      - name: brouillons
        path: brouillons
        rights: [ write, overwrite ]
        approval: { overwrite: never } # remplacer sans validation : dit explicitement
      timezone: Europe/Paris           # par défaut : le fuseau des dates-heures écrites
      recalc: true                     # vérifier les formules avec LibreOffice
      charts: true                     # des graphiques dans les onglets ; false par défaut

Les dossiers suivent la forme de `loom.local_files` (`name`, `path`,
`rights`, `approval`) ; le droit `delete` n'a pas de sens ici et est refusé.

## Constantes de `loom_ia.tools_builtin.excel_params`

- `DEFAULT_RECALC_TIMEOUT = 60.0` : Délai du recalcul par LibreOffice sans réglage, en secondes.
- `REFERENCE = 'loom.excel'` : Référence de l'outil dans la config.

## Classe `loom_ia.tools_builtin.excel_params.ExcelParams`

```python
class ExcelParams(BaseModel)
```

Paramètres de `loom.excel`.

**Attributs :**

- `folders` : Dossiers permis, au moins un ; le droit `delete` n'y a pas cours.
- `timezone` : Fuseau IANA où passe une date-heure qui en porte un : Excel n'en connaît pas, la cellule reçoit l'heure de ce fuseau, sans fuseau.
- `recalc` : Recalculer le classeur avec LibreOffice avant de l'écrire : une formule en erreur (#DIV/0!, #NAME?…) fait échouer l'appel, et le fichier n'est pas écrit.
- `soffice` : L'exécutable de LibreOffice ; par défaut `soffice` cherché dans le PATH.
- `recalc_timeout` : Délai du recalcul, en secondes.
- `charts` : Des graphiques Excel liés aux cellules de leur onglet. Faux par défaut : leur mode d'emploi et leur schéma coûtent à chaque appel du modèle.
