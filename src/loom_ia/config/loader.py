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

import os
from pathlib import Path
from typing import Any, Final, cast

import yaml
from pydantic import ValidationError

from loom_ia.agents.spec import AgentSpec, BaseRole, McpTools, system_text
from loom_ia.config.errors import ConfigError, from_validation
from loom_ia.config.models import PROFILES, LoomConfig, Profile, StorageConfig, TenantSpec
from loom_ia.core.model import (
    DEFAULT_TENANT,
    McpServerSpec,
    OutputContract,
    ToolOverrides,
)
from loom_ia.core.template import Template, TemplateError

AGENT_SUFFIXES = (".yaml", ".yml")
# Variable qui l'emporte sur le fichier : ce qu'un conteneur sait poser.
PROFILE_ENV: Final = "LOOM_PROFILE"
# Blocs remplacés en entier par une surcharge de profil, jamais fusionnés :
# ils sont transmis tels quels au fournisseur, et une moitié de l'un n'a pas
# de sens (décision de conception, §17.9).
WHOLE: Final = frozenset({"params", "llm"})


def load_config(path: str | Path, *, profile: str | None = None) -> LoomConfig:
    """Config complète : fichier racine, surcharges du profil, puis les agents.

    Le profil se choisit à trois endroits, du plus fort au plus faible :
    l'argument (``--profile``), ``LOOM_PROFILE``, puis ``profile:`` dans le
    fichier. ``chosen_profile`` rend le même arbitrage avec sa provenance,
    pour qui veut l'afficher.
    """
    root = Path(path).resolve()
    data = _read_yaml(root)
    base_dir = root.parent
    active, _ = chosen_profile(profile, data.get("profile"))
    data = _merged(data, active, source=root)
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
    loaded = config.model_copy(
        update={
            # L'option et la variable l'emportent sur le fichier : la config
            # chargée porte le profil qui s'applique vraiment.
            "profile": active,
            "base_dir": base_dir,
            "agents_dir": base_dir / config.agents_dir,
            "prompts_dir": base_dir / config.prompts_dir,
            "storage": _absolute_storage(config.storage, base_dir),
            "sessions": _absolute_sessions(config, base_dir / config.prompts_dir, source=root),
            "server": _absolute_server(config, base_dir),
            "mcp_servers": tuple(_launched_from(server, base_dir) for server in servers),
            "tenants": tuple(_absolute_tenant(tenant, base_dir) for tenant in config.tenants),
        }
    )
    _check_variables(loaded, source=root)
    return loaded


def chosen_profile(given: str | None, in_file: object = None) -> tuple[Profile | None, str]:
    """Le profil actif et d'où il vient : option, environnement, fichier, ou rien.

    Un profil qu'on ne voit pas est un profil qu'on oublie : la provenance est
    rendue avec lui pour que ``loom validate`` et ``loom serve`` la disent.
    """
    candidats: tuple[tuple[object, str], ...] = (
        (given, "option"),
        (os.environ.get(PROFILE_ENV), PROFILE_ENV),
        (in_file, "config"),
    )
    for value, source in candidats:
        if value is None:
            continue
        if not isinstance(value, str) or value not in PROFILES:
            known = " ou ".join(PROFILES)
            raise ConfigError(f"Profil {value!r} inconnu ({source}) : attendu {known}")
        return value, source
    return None, "aucun"


def _merged(data: dict[str, Any], active: Profile | None, *, source: Path) -> dict[str, Any]:
    """Applique les surcharges du profil actif au fichier racine (M4).

    Les objets fusionnent en profondeur, les listes sont **remplacées** : une
    liste partiellement fusionnée ne veut rien dire — que ferait-on d'un
    troisième modèle à demi surchargé ?
    """
    declared = data.get("profiles")
    if active is None or not isinstance(declared, dict):
        return data
    table = cast("dict[str, Any]", declared)
    overrides = table.get(active)
    if overrides is None:
        return data
    if not isinstance(overrides, dict):
        raise ConfigError(f"{source} : 'profiles.{active}' doit être un objet")
    return _deep_merge(data, cast("dict[str, Any]", overrides))


def _deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    """Fusion profonde des objets ; toute autre valeur remplace, listes comprises."""
    merged = dict(base)
    for key, value in over.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict) and key not in WHOLE:
            merged[key] = _deep_merge(
                cast("dict[str, Any]", current), cast("dict[str, Any]", value)
            )
        else:
            merged[key] = value
    return merged


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


def _absolute_tenant(tenant: TenantSpec, base_dir: Path) -> TenantSpec:
    """Chemins du stockage propre à un client, rapportés au dossier de la config."""
    if tenant.storage is None:
        return tenant
    return tenant.model_copy(update={"storage": _absolute_storage(tenant.storage, base_dir)})


def _check_variables(config: LoomConfig, *, source: Path) -> None:
    """Variables citées par les prompts définies pour chaque client (§6, M5).

    Un prompt appartient à la configuration, pas au client : la même phrase
    sert tout le monde. Ce qui change d'un client à l'autre, ce sont les
    valeurs — et un client à qui il en manque une écrirait un trou dans son
    prompt sans que rien ne le dise. Le chargement l'attrape.
    """
    tenants: tuple[TenantSpec | None, ...] = config.tenants or (None,)
    for spec in config.all_agents:
        for label, role in (("main", spec.main), *((f"roles[{r.name}]", r) for r in spec.roles)):
            try:
                template = Template.parse(system_text(role))
            except TemplateError as exc:
                raise ConfigError(
                    f"Agent {spec.name!r}, {label}.system — {exc}", source=source
                ) from exc
            except OSError as exc:
                raise ConfigError(
                    f"Agent {spec.name!r}, {label}.system_file — prompt illisible : {exc}",
                    source=source,
                ) from exc
            for path in template.variables:
                for tenant in tenants:
                    known = tenant.variables if tenant is not None else {}
                    if path[0] in known:
                        continue
                    who = tenant.id if tenant is not None else DEFAULT_TENANT
                    shown = "{{ " + ".".join(path) + " }}"
                    raise ConfigError(
                        f"Agent {spec.name!r}, {label}.system — {shown} : variable non "
                        f"définie pour le client {who!r} (tenants[].variables)",
                        source=source,
                    )


def _absolute_storage(storage: StorageConfig, base_dir: Path) -> StorageConfig:
    """Chemins du journal, des artefacts et des clés rapportés au dossier de la config."""
    update: dict[str, object] = {}
    if storage.events.path is not None:
        update["events"] = storage.events.model_copy(
            update={"path": base_dir / storage.events.path}
        )
    if storage.artifacts.path is not None:
        update["artifacts"] = storage.artifacts.model_copy(
            update={"path": base_dir / storage.artifacts.path}
        )
    if storage.idempotency.path is not None:
        update["idempotency"] = storage.idempotency.model_copy(
            update={"path": base_dir / storage.idempotency.path}
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
