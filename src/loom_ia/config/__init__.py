# SPDX-License-Identifier: Apache-2.0
"""Configuration : schéma Pydantic, lecture YAML, références Python (M1 à M3)."""

from loom_ia.config.errors import ConfigError
from loom_ia.config.loader import load_config
from loom_ia.config.models import LoomConfig
from loom_ia.config.references import Registry, import_modules, resolve
from loom_ia.config.schema import agent_json_schema, config_json_schema
from loom_ia.core.model import UnsupportedKey

__all__ = [
    "ConfigError",
    "LoomConfig",
    "Registry",
    "UnsupportedKey",
    "agent_json_schema",
    "config_json_schema",
    "import_modules",
    "load_config",
    "resolve",
]
