# loom-boot.yaml : la clé config

La clé `config` de `loom-boot.yaml` et tout ce qu'elle contient.

## `config`

Config de loom lue dans un fichier local : le bloc `config` de l'amorce (`source: file`).

Exemple :

```
config:
  source: file
  path: ./config
  file: loom.yaml
```

Le dossier `path` est la racine des documents de la config :
`agents_dir`, `resources_dir` et les `schema_file` s'y rapportent.
Les chemins de travail, eux, se rapportent à `workpath` (décisions A-5
et A-6).

Modèle Pydantic : `loom_ia.config.models.FileConfigSource`.

- `config.source` (type `"file"` ; obligatoire) : `file`, seule source disponible pour l'instant (décision A-7) ; une autre valeur est refusée en la nommant.
- `config.path` (type `Path` ; défaut : `"."`) : Dossier de la config, relatif au dossier de l'amorce ; par défaut, celui de l'amorce.
- `config.file` (type `str` ; défaut : `"loom.yaml"`) : Nom du fichier de config dans `path`, sans dossier ; `loom.yaml` par défaut.
