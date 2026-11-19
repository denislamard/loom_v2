# loom-boot.yaml : les clés de premier niveau

L'amorce (`loom-boot.yaml`) : le premier fichier que loom lit, toujours local (décisions A-1 et A-2).

Modèle Pydantic : `loom_ia.config.models.BootConfig`.

- `version` (type `int` ; obligatoire) : Version du schéma ; 1.
- `profile` (type `Profile | null` ; défaut : `null`) : Profil actif (`dev` ou `prod`) ; l'option `--profile` et `LOOM_PROFILE` l'emportent.
- `env_file` (type `Path | null` ; défaut : `null`) : Fichier d'environnement du projet, relatif au dossier de l'amorce ; à défaut, le `.env` de ce dossier.
- `config` (type `FileConfigSource` ; obligatoire) : Où lire la config de loom.
