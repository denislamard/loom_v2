# SPDX-License-Identifier: Apache-2.0
"""Les réglages de ``loom-mem-gpu`` : options, variables ``LOOM_MEM_GPU_*``, YAML facultatif.

Les mêmes clés partout (``port`` ↔ ``--port`` ↔ ``LOOM_MEM_GPU_PORT``). Une option
donnée l'emporte sur la variable, la variable sur le fichier, le fichier sur le
défaut ; une variable vide compte comme absente. Une variable ``LOOM_MEM_GPU_*``
inconnue (une faute de frappe) est refusée, sauf celle que nomme ``token_env``.
Les réglages des modèles sont validés par ``ModelsSpec``, comme dans loom, à
une différence près : le device vaut ``cuda`` par défaut. Une application
« gpu » qui retomberait en silence sur le CPU serait un piège ; ``cpu`` et
``auto`` restent permis, à demander.

L'application ne lit jamais ``loom.yaml`` : ses réglages sont les siens.
"""

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final, Literal, Self, cast, get_args

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from loom_ia.tools_builtin.memory_params import DEVICE_PATTERN, ModelsSpec, is_loopback
from loom_ia.tools_builtin.sql_params import ENV_NAME_PATTERN

__all__ = [
    "ENV_PREFIX",
    "LOG_LEVELS",
    "ServiceSettings",
    "SettingsError",
    "as_environment",
    "describe",
    "env_name",
    "is_loopback",
    "load_settings",
]

DEFAULT_HOST: Final = "127.0.0.1"
DEFAULT_PORT: Final = 8765
ENV_PREFIX: Final = "LOOM_MEM_GPU_"
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]
LOG_LEVELS: Final[tuple[str, ...]] = get_args(LogLevel)
# Les réglages sans défaut, écrits en commentaire dans l'unité, avec cette valeur d'exemple.
_EXAMPLES: Final = {"token_env": "LOOM_MEM_GPU_TOKEN", "max_vram_fraction": "0.3"}
_DEFAULT_MODELS: Final = ModelsSpec()


class SettingsError(Exception):
    """Réglages refusés : le message dit lequel et pourquoi."""


class ServiceSettings(BaseModel):
    """Ce que l'application sert, où, et avec quelles limites."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # Chaque ``description`` sert deux fois : l'aide de l'option, le commentaire de la
    # variable dans l'unité systemd (``as_environment``).
    host: str = Field(
        default=DEFAULT_HOST,
        description="adresse d'écoute : la boucle locale seulement (127.0.0.1, localhost, ::1)",
    )
    port: int = Field(
        default=DEFAULT_PORT,
        ge=1,
        le=65535,
        description="port d'écoute ; un autre port : changer aussi models.server.url des agents",
    )
    # Le seul défaut qui diffère de ``models`` : ``cuda``, pas ``auto``.
    device: str = Field(
        default="cuda",
        pattern=DEVICE_PATTERN,
        description="cuda (la carte), cuda:N (la carte N), cpu ou auto",
    )
    fp16: bool = Field(
        default=True, description="true : demi-précision sur la carte ; false : float32"
    )
    offline: bool = Field(
        default=False,
        description="true : modèles lus dans le cache local de Hugging Face seulement, sans réseau",
    )
    embedder: str = Field(
        default=_DEFAULT_MODELS.embedder,
        min_length=1,
        description="modèle de la famille BGE-M3 : nom Hugging Face ou dossier local",
    )
    reranker: str = Field(
        default=_DEFAULT_MODELS.reranker,
        min_length=1,
        description="reranker à classifieur de paires : nom Hugging Face ou dossier local",
    )
    # Jamais le jeton lui-même : il serait visible dans ``ps`` et ``systemctl show``.
    token_env: str | None = Field(
        default=None,
        pattern=ENV_NAME_PATTERN,
        description="nom de la variable qui porte le jeton exigé des clients (pas le jeton)",
    )
    max_vram_fraction: float | None = Field(
        default=None,
        gt=0.0,
        le=1.0,
        description="part de la carte permise, entre 0 et 1, à côté d'un LLM local (cuda)",
    )
    log_level: LogLevel = Field(
        default="INFO", description="niveau du journal : DEBUG, INFO, WARNING ou ERROR"
    )

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


def describe(field: str) -> str:
    """Ce que fait un réglage, en une ligne : l'aide de l'option, le commentaire de l'unité."""
    description = ServiceSettings.model_fields[field].description
    assert description is not None, field
    return description


def env_name(field: str) -> str:
    """La variable d'un réglage : ``max_vram_fraction`` → ``LOOM_MEM_GPU_MAX_VRAM_FRACTION``."""
    return ENV_PREFIX + field.upper()


def load_settings(
    config: Path | None,
    options: Mapping[str, Any],
    environ: Mapping[str, str] | None = None,
) -> ServiceSettings:
    """Les réglages : le fichier ``config``, puis les variables, puis les options données.

    ``options`` ne porte que ce qui a été donné sur la ligne de commande ;
    ``environ`` vaut ``{}`` par défaut (aucune variable lue).
    """
    environ = {} if environ is None else environ
    found = _from_file(config)
    from_env = {
        field: environ[env_name(field)].strip()
        for field in ServiceSettings.model_fields
        if environ.get(env_name(field), "").strip()
    }
    found.update(from_env)
    found.update(options)
    sources = [str(config)] if config is not None else []
    sources += ["environnement"] if from_env else []
    sources += ["options"] if options else []
    where = ", ".join(sources) or "défauts"
    try:
        settings = ServiceSettings.model_validate(found)
    except ValidationError as exc:
        raise SettingsError(f"réglages refusés ({where}) : {_errors(exc, from_env)}") from exc
    known = [env_name(field) for field in ServiceSettings.model_fields]
    allowed = {*known, settings.token_env}
    unknown = sorted(
        name for name in environ if name.startswith(ENV_PREFIX) and name not in allowed
    )
    if unknown:
        raise SettingsError(
            f"variable {ENV_PREFIX}* inconnue : {', '.join(unknown)} "
            f"(connues : {', '.join(known)} ; et celle que nomme token_env)"
        )
    return settings


def as_environment(settings: ServiceSettings) -> list[str]:
    """Les réglages en lignes ``Environment=`` d'une unité systemd, une par variable.

    Chaque variable est précédée d'un commentaire qui dit ce qu'elle fait. Un réglage
    sans valeur est écrit en commentaire, avec une valeur d'exemple. Les valeurs sont
    échappées pour systemd (``%`` doublé ; guillemets si espace).
    """
    lines: list[str] = []
    for field, value in settings.model_dump().items():
        lines.append(f"# {describe(field)}{' — non réglé par défaut' if value is None else ''}")
        if value is None:
            lines.append(f"# {_unit_line(env_name(field), _EXAMPLES[field])}")
        else:
            lines.append(_unit_line(env_name(field), _text(value)))
    return lines


def _text(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _unit_line(name: str, value: str) -> str:
    assignment = f"{name}={value}".replace("%", "%%")
    if any(char.isspace() or char in "\"'\\" for char in assignment):
        escaped = assignment.replace("\\", "\\\\").replace('"', '\\"')
        return f'Environment="{escaped}"'
    return f"Environment={assignment}"


def _from_file(config: Path | None) -> dict[str, Any]:
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
    return found


def _errors(exc: ValidationError, from_env: Mapping[str, str]) -> str:
    said: list[str] = []
    for error in exc.errors():
        place = ".".join(str(part) for part in error["loc"])
        if place in from_env:
            place = f"{place} ({env_name(place)})"
        message = str(error["msg"]).removeprefix("Value error, ")
        said.append(f"{place} : {message}" if place else message)
    return " ; ".join(said)
