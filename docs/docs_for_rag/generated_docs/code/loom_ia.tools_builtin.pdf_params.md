# loom_ia.tools_builtin.pdf_params

Module `loom_ia.tools_builtin.pdf_params` de loom_v2 (fichier `src/loom_ia/tools_builtin/pdf_params.py`).

Paramètres de `loom.pdf` : les dossiers permis, le fuseau, les modèles et le style.

Module léger — Pydantic et `_office_params` seulement — : la config
l'importe pour valider la référence au chargement, sans importer l'outil ni pypdf ni reportlab.

    - python: loom.pdf
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

`style` et les modèles sont ceux de `loom.word` : un même fichier sert aux deux.

Les dossiers suivent la forme de `loom.local_files` (`name`, `path`,
`rights`, `approval`) ; le droit `delete` n'a pas de sens ici et est refusé.

## Constantes de `loom_ia.tools_builtin.pdf_params`

- `REFERENCE = 'loom.pdf'` : Référence de l'outil dans la config.
- `SAFE_PYPDF = (6, 14, 2)` : Plancher de pypdf pour lire un PDF : les versions antérieures bouclent sans fin sur des PDF forgés (CVE-2026-59935, -59937). Ici, et non dans l'outil, pour que `loom.memory` le vérifie sans importer reportlab.

## Classe `loom_ia.tools_builtin.pdf_params.PdfParams`

```python
class PdfParams(BaseModel)
```

Paramètres de `loom.pdf`.

**Attributs :**

- `folders` : Dossiers permis, au moins un ; le droit `delete` n'y a pas cours.
- `timezone` : Fuseau IANA où passe une date-heure qui en porte un : le document l'écrit à l'heure de ce fuseau, sans le nommer.
- `blocks` : Les blocs facultatifs, chacun payé en tokens à chaque appel au modèle : chart (environ 430), image (environ 190), signature, toc et checkboxes (environ 190 à eux trois), fields (environ 115), code (environ 75), invoice_totals (environ 185), attendance (environ 130). Aucun par défaut.
- `templates` : Les modèles de document, par nom : fichiers relatifs à resources_dir, lus au chargement.
- `style` : La charte : un fichier de style relatif à resources_dir, lu au chargement, ou un style écrit en ligne ; le style d'un modèle se nomme dans le fichier du modèle (`style:`).

### Méthode `loom_ia.tools_builtin.pdf_params.PdfParams.precheck`

```python
@classmethod
def precheck(cls, value: object) -> object
```

Avant toute validation, aussi sur la référence de la config (`agents.spec`) : les clés de la 2.0 refusées avec ce qui les remplace.
