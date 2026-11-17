# loom_ia.tools_builtin.word_params

Module `loom_ia.tools_builtin.word_params` de loom_v2 (fichier `src/loom_ia/tools_builtin/word_params.py`).

Paramètres de `loom.word` : les dossiers permis, et le fuseau des dates-heures.

Module léger — Pydantic et `_office_params` seulement — : la config
l'importe pour valider la référence au chargement, sans importer l'outil.

    - python: loom.word
      folders:
      - name: modeles
        path: modeles
        rights: [ read ]               # les fichiers que l'outil peut lire
      - name: sortie
        path: sortie
        rights: [ write, overwrite ]   # remplacer un fichier : approbation par défaut
      timezone: Europe/Paris           # par défaut : le fuseau des dates-heures écrites
      blocks: [chart, image]           # les blocs facultatifs : chart, image, signature, toc,
\                                       # checkboxes, fields, code, invoice_totals,
\                                       # attendance ; aucun par défaut
      style: styles/charte.yaml        # la charte (facultatif), relative à resources_dir
      templates:                       # modèles de document, relatifs à resources_dir ; le
        facture: templates/facture.yaml  # style d'un modèle se nomme dans son fichier

Les dossiers suivent la forme de `loom.local_files` (`name`, `path`,
`rights`, `approval`) ; le droit `delete` n'a pas de sens ici et est refusé.

## Constantes de `loom_ia.tools_builtin.word_params`

- `REFERENCE = 'loom.word'` : Référence de l'outil dans la config.

## Classe `loom_ia.tools_builtin.word_params.WordParams`

```python
class WordParams(BaseModel)
```

Paramètres de `loom.word`.

**Attributs :**

- `folders` : Dossiers permis, au moins un ; le droit `delete` n'y a pas cours.
- `timezone` : Fuseau IANA où passe une date-heure qui en porte un : le document l'écrit à l'heure de ce fuseau, sans le nommer.
- `blocks` : Les blocs facultatifs, chacun payé en tokens à chaque appel au modèle : chart (environ 430), image (environ 190), signature, toc et checkboxes (environ 190 à eux trois), fields (environ 115), code (environ 75), invoice_totals (environ 185), attendance (environ 130). Aucun par défaut.
- `templates` : Les modèles de document, par nom : fichiers relatifs à resources_dir, lus au chargement.
- `style` : La charte : un fichier de style relatif à resources_dir, lu au chargement, ou un style écrit en ligne ; le style d'un modèle se nomme dans le fichier du modèle (`style:`).

### Méthode `loom_ia.tools_builtin.word_params.WordParams.precheck`

```python
@classmethod
def precheck(cls, value: object) -> object
```

Avant toute validation, aussi sur la référence de la config (`agents.spec`) : les clés de la 2.0 refusées avec ce qui les remplace.
