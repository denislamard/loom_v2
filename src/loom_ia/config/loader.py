# SPDX-License-Identifier: Apache-2.0
"""Lecture de ``loom.yaml`` et des agents (M1, #35).

``safe_load`` puis validation stricte par les modèles Pydantic. Les chemins
(``agents_dir``, ``prompts_dir``, journal) sont relatifs au fichier de
config et deviennent absolus au chargement. Un ``system_file`` manquant, un
modèle inconnu ou un agent en double arrêtent le démarrage.
"""

from pathlib import Path
from typing import Any, cast

import yaml
from pydantic import ValidationError

from loom_ia.agents.spec import AgentSpec
from loom_ia.config.errors import ConfigError, from_validation
from loom_ia.config.models import LoomConfig

AGENT_SUFFIXES = (".yaml", ".yml")


def load_config(path: str | Path) -> LoomConfig:
    """Config complète : fichier racine, puis les agents de ``agents_dir``."""
    root = Path(path).resolve()
    data = _read_yaml(root)
    base_dir = root.parent
    draft = _validate(LoomConfig, {**data, "agents": data.get("agents", [])}, source=root)

    agents = [
        *draft.agents,
        *_load_agents(base_dir / draft.agents_dir, base_dir / draft.prompts_dir),
    ]
    config = _validate(LoomConfig, {**data, "agents": agents}, source=root)
    return config.model_copy(
        update={
            "base_dir": base_dir,
            "agents_dir": base_dir / config.agents_dir,
            "prompts_dir": base_dir / config.prompts_dir,
            "storage": _absolute_storage(config, base_dir),
        }
    )


def _load_agents(agents_dir: Path, prompts_dir: Path) -> list[AgentSpec]:
    if not agents_dir.is_dir():
        raise ConfigError(f"Dossier d'agents introuvable : {agents_dir}")
    agents: list[AgentSpec] = []
    for file in sorted(f for f in agents_dir.iterdir() if f.suffix in AGENT_SUFFIXES):
        spec = _validate(AgentSpec, _read_yaml(file), source=file)
        agents.append(_with_prompt(spec, prompts_dir, source=file))
    return agents


def _with_prompt(spec: AgentSpec, prompts_dir: Path, *, source: Path) -> AgentSpec:
    """Rend le chemin du prompt absolu et vérifie qu'il est lisible."""
    if spec.main.system_file is None:
        return spec
    prompt = prompts_dir / spec.main.system_file
    if not prompt.is_file():
        raise ConfigError(f"main.system_file — prompt introuvable : {prompt}", source=source)
    try:
        prompt.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"main.system_file — prompt illisible : {exc}", source=source) from exc
    return spec.model_copy(update={"main": spec.main.model_copy(update={"system_file": prompt})})


def _absolute_storage(config: LoomConfig, base_dir: Path) -> object:
    events = config.storage.events
    if events.path is None:
        return config.storage
    return config.storage.model_copy(
        update={"events": events.model_copy(update={"path": base_dir / events.path})}
    )


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"Fichier illisible : {exc}") from exc
    try:
        data = cast(object, yaml.safe_load(content))
    except yaml.YAMLError as exc:
        raise ConfigError(f"YAML invalide : {exc}", source=path) from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError("Le fichier doit contenir un objet YAML", source=path)
    return cast(dict[str, Any], data)


def _validate[T](model: type[T], data: dict[str, Any], *, source: Path) -> T:
    adapter = cast(Any, model)
    try:
        return cast(T, adapter.model_validate(data))
    except ValidationError as exc:
        raise from_validation(exc, source=source) from exc
