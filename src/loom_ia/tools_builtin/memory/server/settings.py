# SPDX-License-Identifier: Apache-2.0
"""Les réglages de ``loom-mem-gpu`` : options de la ligne de commande, YAML facultatif.

Les mêmes clés des deux côtés ; une option donnée l'emporte sur le fichier.
Les réglages des modèles sont validés par ``ModelsSpec``, comme dans loom, à
une différence près : le device vaut ``cuda`` par défaut. Une application
« gpu » qui retomberait en silence sur le CPU serait un piège ; ``cpu`` et
``auto`` restent permis, à demander.

L'application ne lit jamais ``loom.yaml`` : ses réglages sont les siens.
"""

import ipaddress
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final, Self, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from loom_ia.tools_builtin.memory_params import ModelsSpec
from loom_ia.tools_builtin.sql_params import ENV_NAME_PATTERN

DEFAULT_HOST: Final = "127.0.0.1"
DEFAULT_PORT: Final = 8765
_DEFAULT_MODELS: Final = ModelsSpec()


class SettingsError(Exception):
    """Réglages refusés : le message dit lequel et pourquoi."""


class ServiceSettings(BaseModel):
    """Ce que l'application sert, où, et avec quelles limites."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    host: str = DEFAULT_HOST
    port: int = Field(default=DEFAULT_PORT, ge=1, le=65535)
    device: str = Field(default="cuda", pattern=r"^(auto|cpu|mps|cuda(:[0-9]+)?)$")
    fp16: bool = True
    offline: bool = False
    embedder: str = Field(default=_DEFAULT_MODELS.embedder, min_length=1)
    reranker: str = Field(default=_DEFAULT_MODELS.reranker, min_length=1)
    # Variable qui porte le jeton ; jamais le jeton lui-même (il serait visible dans ``ps``).
    token_env: str | None = Field(default=None, pattern=ENV_NAME_PATTERN)
    # Part de la mémoire de la carte que l'application peut prendre (torch).
    max_vram_fraction: float | None = Field(default=None, gt=0.0, le=1.0)

    @model_validator(mode="after")
    def _check(self) -> Self:
        if not is_loopback(self.host):
            raise ValueError(
                f"host {self.host!r} : seule la boucle locale est permise "
                "(127.0.0.1, localhost, ::1) ; l'accès réseau viendra plus tard"
            )
        if self.max_vram_fraction is not None and not self.device.startswith("cuda"):
            raise ValueError("max_vram_fraction ne vaut que sur une carte (device cuda)")
        return self

    def models(self) -> ModelsSpec:
        """Les réglages des modèles, tels que ``BgeM3Embedder`` et ``BgeReranker`` les lisent."""
        return ModelsSpec(
            kind="bge-m3",
            embedder=self.embedder,
            reranker=self.reranker,
            device=self.device,
            fp16=self.fp16,
            offline=self.offline,
        )

    def token(self, environ: Mapping[str, str] | None = None) -> str | None:
        """Le jeton lu dans ``token_env`` ; ``SettingsError`` si la variable est vide."""
        if self.token_env is None:
            return None
        value = (os.environ if environ is None else environ).get(self.token_env, "").strip()
        if not value:
            raise SettingsError(f"token_env : la variable {self.token_env} est absente ou vide")
        return value


def is_loopback(host: str) -> bool:
    """Vrai pour une adresse de la boucle locale (ou ``localhost``)."""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def load_settings(config: Path | None, options: Mapping[str, Any]) -> ServiceSettings:
    """Les réglages : le fichier ``config`` s'il est donné, puis les options données.

    ``options`` ne porte que ce qui a été donné sur la ligne de commande.
    """
    found: dict[str, Any] = {}
    if config is not None:
        try:
            text = config.read_text(encoding="utf-8")
        except OSError as exc:
            raise SettingsError(f"{config} : {exc.strerror or type(exc).__name__}") from exc
        try:
            loaded: object = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise SettingsError(f"{config} : YAML illisible ({exc})") from exc
        if loaded is not None and not isinstance(loaded, dict):
            raise SettingsError(f"{config} : un objet YAML (clé: valeur) est attendu")
        found.update(cast(dict[str, Any], loaded or {}))
    found.update(options)
    try:
        return ServiceSettings.model_validate(found)
    except ValidationError as exc:
        where = f"{config} et options" if config is not None else "options"
        raise SettingsError(f"réglages refusés ({where}) : {_errors(exc)}") from exc


def _errors(exc: ValidationError) -> str:
    said: list[str] = []
    for error in exc.errors():
        place = ".".join(str(part) for part in error["loc"])
        message = str(error["msg"]).removeprefix("Value error, ")
        said.append(f"{place} : {message}" if place else message)
    return " ; ".join(said)
