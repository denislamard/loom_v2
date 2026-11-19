# SPDX-License-Identifier: Apache-2.0
"""Configuration : schéma Pydantic, lecture YAML, références Python (fonctions M1 à M3).

- ``models`` : le schéma de ``loom.yaml`` (``LoomConfig``) et de l'amorce
  ``loom-boot.yaml`` (``BootConfig``) ;
- ``loader`` : ``load_config``, qui lit ``loom.yaml``, les agents, les
  prompts et les skills, applique le profil et contrôle le tout ;
- ``env_file`` : le fichier d'environnement du projet (``.env``) ;
- ``references`` : ``imports``, registre des outils, ``module:attr`` ;
- ``telemetry`` : capture, masquage et collecteurs ;
- ``compaction`` : l'agent interne ``_compaction`` ;
- ``keys`` : clés d'API et leur empreinte ;
- ``schema`` : le JSON Schema publié pour l'éditeur (``loom schema``) ;
- ``errors`` : ``ConfigError``, des messages qui nomment fichier et chemin.
"""

from loom_ia.config.env_file import EnvFileReport, load_env_file
from loom_ia.config.errors import ConfigError
from loom_ia.config.keys import fingerprint, new_api_key
from loom_ia.config.loader import load_config
from loom_ia.config.models import ApiKey, AuditConfig, BootConfig, LoomConfig, SecurityConfig, TenantSpec
from loom_ia.config.references import Registry, import_modules, resolve
from loom_ia.config.schema import agent_json_schema, boot_json_schema, config_json_schema

__all__ = [
    "ApiKey",
    "AuditConfig",
    "BootConfig",
    "ConfigError",
    "EnvFileReport",
    "LoomConfig",
    "Registry",
    "SecurityConfig",
    "TenantSpec",
    "agent_json_schema",
    "boot_json_schema",
    "config_json_schema",
    "fingerprint",
    "import_modules",
    "load_config",
    "load_env_file",
    "new_api_key",
    "resolve",
]
