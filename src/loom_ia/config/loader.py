# SPDX-License-Identifier: Apache-2.0
"""Lecture de ``loom.yaml`` et des agents (M1, #35).

``safe_load`` puis validation stricte par les modèles Pydantic. Les chemins
(``agents_dir``, ``prompts_dir``, journal, artefacts, ``cwd`` des serveurs
MCP, ``schema_file`` des contrats de sortie) sont relatifs au fichier de
config et deviennent absolus au chargement ; un schéma de sortie donné par
fichier (JSON ou YAML) est lu et vérifié à ce moment-là.
Un serveur MCP stdio sans ``cwd`` se lance depuis le dossier de la config. Un
``system_file`` manquant, un modèle inconnu ou un agent en double arrêtent
le démarrage.
"""

from pathlib import Path
from typing import Any, cast

import yaml
from pydantic import ValidationError

from loom_ia.agents.spec import AgentSpec, BaseRole, McpTools
from loom_ia.config.errors import ConfigError, from_validation
from loom_ia.config.models import LoomConfig
from loom_ia.core.model import McpServerSpec, OutputContract, ToolOverrides

AGENT_SUFFIXES = (".yaml", ".yml")


def load_config(path: str | Path) -> LoomConfig:
    """Config complète : fichier racine, puis les agents de ``agents_dir``."""
    root = Path(path).resolve()
    data = _read_yaml(root)
    base_dir = root.parent
    draft = _validate(LoomConfig, {**data, "agents": data.get("agents", [])}, source=root)

    agents = [
        *draft.agents,
        *_load_agents(base_dir / draft.agents_dir, base_dir / draft.prompts_dir, base_dir),
    ]
    config = _validate(LoomConfig, {**data, "agents": agents}, source=root)
    servers = tuple(
        server.model_copy(update={"tools": _with_schemas(server.tools, base_dir, source=root)})
        for server in config.mcp_servers
    )
    return config.model_copy(
        update={
            "base_dir": base_dir,
            "agents_dir": base_dir / config.agents_dir,
            "prompts_dir": base_dir / config.prompts_dir,
            "storage": _absolute_storage(config, base_dir),
            "sessions": _absolute_sessions(config, base_dir / config.prompts_dir, source=root),
            "server": _absolute_server(config, base_dir),
            "mcp_servers": tuple(_launched_from(server, base_dir) for server in servers),
        }
    )


def _launched_from(server: McpServerSpec, base_dir: Path) -> McpServerSpec:
    """Un serveur stdio se lance depuis le dossier de la config, sauf ``cwd`` absolu."""
    if server.transport != "stdio":
        return server
    cwd = base_dir if server.cwd is None else base_dir / server.cwd
    return server.model_copy(update={"cwd": cwd})


def _load_agents(agents_dir: Path, prompts_dir: Path, base_dir: Path) -> list[AgentSpec]:
    if not agents_dir.is_dir():
        raise ConfigError(f"Dossier d'agents introuvable : {agents_dir}")
    agents: list[AgentSpec] = []
    for file in sorted(f for f in agents_dir.iterdir() if f.suffix in AGENT_SUFFIXES):
        spec = _validate(AgentSpec, _read_yaml(file), source=file)
        spec = _with_prompts(spec, prompts_dir, source=file)
        agents.append(_with_contracts(spec, base_dir, source=file))
    return agents


def _with_contracts(spec: AgentSpec, base_dir: Path, *, source: Path) -> AgentSpec:
    """Schémas de sortie donnés par fichier, lus : agent, rôles, outils Python et MCP."""
    roles = tuple(
        role.model_copy(update={"output": _schema(role.output, base_dir, source=source)})
        for role in spec.roles
    )
    tools = tuple(
        tool.model_copy(update={"tools": _with_schemas(tool.tools, base_dir, source=source)})
        if isinstance(tool, McpTools)
        else tool.model_copy(update={"output": _schema(tool.output, base_dir, source=source)})
        for tool in spec.tools
    )
    output = _schema(spec.output, base_dir, source=source)
    return spec.model_copy(update={"output": output, "roles": roles, "tools": tools})


def _with_schemas(
    overrides: dict[str, ToolOverrides], base_dir: Path, *, source: Path
) -> dict[str, ToolOverrides]:
    return {
        name: override.model_copy(
            update={"output": _schema(override.output, base_dir, source=source)}
        )
        for name, override in overrides.items()
    }


def _schema(
    contract: OutputContract | None, base_dir: Path, *, source: Path
) -> OutputContract | None:
    """Contrat dont le schéma donné par fichier est lu et vérifié."""
    if contract is None or contract.schema_file is None:
        return contract
    path = base_dir / contract.schema_file
    schema = _read_yaml(path)
    fields = contract.model_dump(by_alias=True, exclude={"schema_file"})
    try:
        return OutputContract.model_validate({**fields, "schema": schema})
    except ValidationError as exc:
        raise from_validation(exc, source=path) from exc


def _with_prompts(spec: AgentSpec, prompts_dir: Path, *, source: Path) -> AgentSpec:
    """Rend absolus les chemins des prompts (``main`` et rôles), et vérifie qu'ils sont lisibles."""
    main = _with_prompt(spec.main, prompts_dir, "main", source=source)
    roles = tuple(
        _with_prompt(role, prompts_dir, f"roles[{role.name}]", source=source) for role in spec.roles
    )
    return spec.model_copy(update={"main": main, "roles": roles})


def _with_prompt[R: BaseRole](role: R, prompts_dir: Path, label: str, *, source: Path) -> R:
    if role.system_file is None:
        return role
    prompt = prompts_dir / role.system_file
    if not prompt.is_file():
        raise ConfigError(f"{label}.system_file — prompt introuvable : {prompt}", source=source)
    try:
        prompt.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"{label}.system_file — prompt illisible : {exc}", source=source) from exc
    return role.model_copy(update={"system_file": prompt})


def _absolute_sessions(config: LoomConfig, prompts_dir: Path, *, source: Path) -> object:
    """Prompt surchargé de la compaction, rapporté au dossier des prompts."""
    sessions = config.sessions
    compaction = sessions.compaction
    if compaction is None or compaction.system_file is None:
        return sessions
    prompt = prompts_dir / compaction.system_file
    if not prompt.is_file():
        raise ConfigError(
            f"sessions.compaction.system_file — prompt introuvable : {prompt}", source=source
        )
    return sessions.model_copy(
        update={"compaction": compaction.model_copy(update={"system_file": prompt})}
    )


def _absolute_storage(config: LoomConfig, base_dir: Path) -> object:
    """Chemins du journal et des artefacts rapportés au dossier de la config."""
    storage = config.storage
    update: dict[str, object] = {}
    if storage.events.path is not None:
        update["events"] = storage.events.model_copy(
            update={"path": base_dir / storage.events.path}
        )
    if storage.artifacts.path is not None:
        update["artifacts"] = storage.artifacts.model_copy(
            update={"path": base_dir / storage.artifacts.path}
        )
    return storage.model_copy(update=update) if update else storage


def _absolute_server(config: LoomConfig, base_dir: Path) -> object:
    """Dossiers lisibles par le serveur MCP, rapportés au dossier de la config."""
    mcp = config.server.mcp
    if not mcp.file_roots:
        return config.server
    roots = tuple(base_dir / root for root in mcp.file_roots)
    return config.server.model_copy(update={"mcp": mcp.model_copy(update={"file_roots": roots})})


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
