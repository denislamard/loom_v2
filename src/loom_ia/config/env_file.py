# SPDX-License-Identifier: Apache-2.0
"""Le fichier d'environnement d'un projet (``.env``), lu au démarrage de ``loom``.

Les clés d'API, les DSN, ``LOOM_PROFILE``… vivent dans l'environnement : la
configuration ne fait qu'en nommer les variables (§16.3). Un fichier ``.env``
les y pose, sans ``export`` ni ``uv run --env-file``.

Le fichier lu, du plus fort au plus faible :

1. celui que donne l'appelant (``--env-file``) ;
2. ``env_file:`` à la racine de ``loom.yaml``, relatif à son dossier ;
3. ``.env`` dans le dossier de ``loom.yaml``, s'il existe.

Un fichier demandé (1 ou 2) et absent est une erreur ; le ``.env`` par défaut
absent n'en est pas une, et il n'est cherché que si ``loom.yaml`` existe.
Jamais le dossier courant, jamais un dossier parent : un projet rangé sous un
dépôt n'hérite pas des clés de sa racine.

Une variable déjà définie — même vide — l'emporte sur le fichier : ce que le
shell, le service ou ``uv run --env-file`` ont posé n'est jamais écrasé. Le
fichier est lu tel quel, sans interpolation (``${A}`` reste ``${A}``). Les
valeurs ne sortent jamais d'ici : ni message d'erreur, ni rapport, seulement
les noms.

La librairie ne lit rien d'elle-même : ``load_env_file("loom.yaml")`` le fait
à la demande, avant ``Loom.from_config``.
"""

import io
import os
from collections.abc import Iterable, MutableMapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, cast

import yaml
from dotenv.parser import parse_stream

from loom_ia.config.errors import ConfigError
from loom_ia.config.yaml_source import load_yaml

# Le fichier cherché à côté de loom.yaml quand rien d'autre n'est désigné.
DEFAULT_NAME: Final = ".env"
# La clé de loom.yaml qui désigne le fichier : à la racine seulement, car il
# est lu avant le choix du profil.
ENV_FILE_KEY: Final = "env_file"

# D'où vient le fichier lu.
type EnvSource = Literal["option", "config", "défaut", "désactivé"]


@dataclass(frozen=True, slots=True)
class EnvFileReport:
    """Ce qu'une lecture a fait : le fichier, d'où il vient, et les noms — jamais les valeurs."""

    # Fichier retenu ; ``None`` quand rien n'est à lire (lecture désactivée,
    # ou pas de loom.yaml à côté duquel chercher).
    path: Path | None
    source: EnvSource
    # Vrai si le fichier existait et a été lu.
    found: bool = False
    # Variables posées depuis le fichier, dans son ordre.
    loaded: tuple[str, ...] = ()
    # Variables du fichier déjà définies : l'environnement l'emporte.
    kept: tuple[str, ...] = ()

    def describe(self) -> str:
        """Une ligne pour ``loom validate`` et le bandeau de ``loom serve``."""
        if self.source == "désactivé":
            return "non lu (--no-env-file)"
        if self.path is None:
            return "aucun"
        if not self.found:
            return f"aucun ({self.path})"
        details: list[str] = []
        if self.loaded:
            details.append(f"lues : {', '.join(self.loaded)}")
        if self.kept:
            details.append(f"déjà définies, gardées : {', '.join(self.kept)}")
        return f"{self.path} (par {self.source}) — {' ; '.join(details) or 'aucune variable'}"

    def prod_warning(self) -> str | None:
        """L'avertissement du profil prod quand des variables viennent du fichier, sinon None.

        Un avertissement, pas un refus : un ``EnvironmentFile`` de systemd ne
        fait pas autre chose, et le service démarre.
        """
        if not self.loaded:
            return None
        return (
            f"variables lues dans {self.path} ({', '.join(self.loaded)}) — "
            "en prod, préférer l'environnement du service"
        )


# Rapport de ``--no-env-file`` : rien n'est lu.
DISABLED: Final = EnvFileReport(path=None, source="désactivé")


def load_env_file(
    config: str | Path,
    *,
    env_file: str | Path | None = None,
    environ: MutableMapping[str, str] | None = None,
) -> EnvFileReport:
    """Pose dans ``environ`` (par défaut ``os.environ``) les variables du fichier d'environnement.

    ``config`` est le chemin de ``loom.yaml`` : il dit où chercher. ``env_file``
    désigne un autre fichier, qui l'emporte sur ``env_file:`` de la config et
    sur le ``.env`` voisin. Une variable déjà définie n'est jamais remplacée.

    Lève ``ConfigError`` si le fichier désigné est absent, illisible ou mal
    formé ; le message dit le fichier et la ligne, jamais une valeur.
    """
    path, source = env_file_path(config, env_file)
    return _load(path, source, (), os.environ if environ is None else environ)


def reload_env_file(
    config: str | Path,
    previous: EnvFileReport,
    *,
    environ: MutableMapping[str, str] | None = None,
) -> EnvFileReport:
    """Relit le fichier pour un process neuf de ``loom serve --reload``.

    Ce process hérite de l'environnement du superviseur, où ``previous`` a
    posé ses variables : elles sont retirées, puis le fichier — peut-être
    modifié, peut-être désigné autrement par la config — est relu. Ce que le
    shell avait défini reste, comme à la première lecture.
    """
    if previous.source == "désactivé":
        return previous
    given = previous.path if previous.source == "option" else None
    path, source = env_file_path(config, given)
    target = os.environ if environ is None else environ
    return _load(path, source, previous.loaded, target)


def env_file_path(
    config: str | Path, given: str | Path | None = None
) -> tuple[Path | None, EnvSource]:
    """Le fichier à lire et d'où il vient ; ``None`` sans loom.yaml à côté duquel chercher."""
    if given is not None:
        return Path(given).resolve(), "option"
    root = Path(config).resolve()
    if not root.is_file():
        # Pas de config : load_config le dira. Chercher un .env à côté d'un
        # fichier absent lirait celui du dossier courant.
        return None, "défaut"
    declared = declared_env_file(root)
    if declared is not None:
        return declared, "config"
    return root.parent / DEFAULT_NAME, "défaut"


def declared_env_file(config: Path) -> Path | None:
    """``env_file:`` de ``loom.yaml``, lu avant la config elle-même, en chemin absolu.

    Lu à part parce que le fichier qu'il désigne peut donner ``LOOM_PROFILE`` :
    il doit l'être avant que le profil ne soit choisi. Un ``loom.yaml``
    illisible rend ``None`` — ``load_config`` le dira, avec ses mots.
    """
    try:
        data = load_yaml(config.read_text(encoding="utf-8"))
    except OSError, UnicodeDecodeError, yaml.YAMLError:
        return None
    if not isinstance(data, dict):
        return None
    value = cast(dict[str, object], data).get(ENV_FILE_KEY)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{ENV_FILE_KEY} — un chemin de fichier est attendu", source=config)
    return (config.parent / value).resolve()


def _load(
    path: Path | None,
    source: EnvSource,
    replace: Iterable[str],
    environ: MutableMapping[str, str],
) -> EnvFileReport:
    for name in replace:
        environ.pop(name, None)
    if path is None:
        return EnvFileReport(path=None, source=source)
    if not path.is_file():
        if source == "défaut":
            return EnvFileReport(path=path, source=source)
        where = "--env-file" if source == "option" else ENV_FILE_KEY
        raise ConfigError(f"Fichier d'environnement introuvable ({where}) : {path}")
    loaded: list[str] = []
    kept: list[str] = []
    for name, value in _read(path).items():
        if name in environ:
            kept.append(name)
        else:
            environ[name] = value
            loaded.append(name)
    return EnvFileReport(
        path=path, source=source, found=True, loaded=tuple(loaded), kept=tuple(kept)
    )


def _read(path: Path) -> dict[str, str]:
    """Les variables du fichier ; une ligne illisible est une erreur, qui dit son numéro."""
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigError(f"Fichier d'environnement {path} : pas de l'UTF-8") from exc
    except OSError as exc:
        raise ConfigError(f"Fichier d'environnement illisible : {path} ({exc.strerror})") from exc
    values: dict[str, str] = {}
    for binding in parse_stream(io.StringIO(text)):
        if binding.error:
            # Le numéro seulement : la ligne pourrait porter un secret.
            raise ConfigError(
                f"Fichier d'environnement {path}, ligne {binding.original.line} : "
                "illisible (attendu NOM=valeur)"
            )
        # Commentaire, ligne vide, ou NOM sans '=' : rien à poser.
        if binding.key is not None and binding.value is not None:
            values[binding.key] = binding.value
    return values
