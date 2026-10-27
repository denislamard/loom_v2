# SPDX-License-Identifier: Apache-2.0
"""Lecture de ``loom.yaml`` et des agents (M1, #35).

``safe_load`` (sans clé en double, ``yaml_source``) puis validation stricte
par les modèles Pydantic. Les chemins (``agents_dir``, ``prompts_dir``,
journal, artefacts, ``cwd`` des serveurs MCP, ``schema_file`` des contrats de
sortie) sont relatifs au fichier de config et deviennent absolus au
chargement ; un schéma de sortie donné par fichier (JSON ou YAML) est lu et
vérifié à ce moment-là.
Un serveur MCP stdio sans ``cwd`` se lance depuis le dossier de la config. Un
``system_file`` manquant, un modèle inconnu ou un agent en double arrêtent
le démarrage. Un ``system_file`` ou un ``skill_file`` doit rester dans
``prompts_dir`` : ailleurs, un dossier ouvert à un outil laisserait l'agent
réécrire son propre prompt.
"""

import os
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final, cast

import yaml
from pydantic import ValidationError

from loom_ia.agents.spec import AgentSpec, BaseRole, McpTools, PythonTool, SourceTools, system_text
from loom_ia.config.env_file import ENV_FILE_KEY
from loom_ia.config.errors import ConfigError, from_validation
from loom_ia.config.models import PROFILES, LoomConfig, Profile, StorageConfig, TenantSpec
from loom_ia.config.yaml_source import DuplicateKey, load_yaml
from loom_ia.core.model import (
    DEFAULT_TENANT,
    McpServerSpec,
    OutputContract,
    ToolOverrides,
    checked_skill,
)
from loom_ia.core.template import Template, TemplateError

AGENT_SUFFIXES = (".yaml", ".yml")
# Début du message d'un contrôle qui porte sur un agent (``LoomConfig.check``).
_AGENT_NAMED: Final = re.compile(r"Agent '([^']+)'")
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
    _check_env_file_at_root(data, source=root)
    active, _ = chosen_profile(profile, data.get("profile"))
    data = _merged(data, active, source=root)
    draft = _validate(LoomConfig, {**data, "agents": data.get("agents", [])}, source=root)

    prompts_dir = base_dir / draft.prompts_dir
    loaded = _load_agents(base_dir / draft.agents_dir, prompts_dir, base_dir)
    # Les agents écrits dans loom.yaml suivent les mêmes règles que ceux d'agents_dir.
    inline = [_prepared(spec, prompts_dir, base_dir, source=root) for spec in draft.agents]
    agents = [*inline, *(spec for spec, _ in loaded)]
    try:
        config = LoomConfig.model_validate({**data, "agents": agents})
    except ValidationError as exc:
        culprit = _culprit(exc, loaded)
        if culprit is None:
            raise from_validation(exc, source=root, data=data) from exc
        # Le message nomme déjà l'agent : pas de « (racine) » devant.
        raise from_validation(exc, source=culprit, root=None) from exc
    servers = tuple(
        server.model_copy(
            update={
                "tools": _with_skills(
                    _with_schemas(server.tools, base_dir, source=root),
                    prompts_dir,
                    f"mcp_servers[{server.name}]",
                    source=root,
                )
            }
        )
        for server in config.mcp_servers
    )
    sources = tuple(
        declared.model_copy(
            update={
                "tools": _with_skills(
                    _with_schemas(declared.tools, base_dir, source=root),
                    prompts_dir,
                    f"tool_sources[{declared.name}]",
                    source=root,
                )
            }
        )
        for declared in config.tool_sources
    )
    loaded = config.model_copy(
        update={
            # L'option et la variable l'emportent sur le fichier : la config
            # chargée porte le profil qui s'applique vraiment.
            "profile": active,
            "base_dir": base_dir,
            "env_file": None if config.env_file is None else (base_dir / config.env_file).resolve(),
            "agents_dir": base_dir / config.agents_dir,
            "prompts_dir": base_dir / config.prompts_dir,
            "storage": _absolute_storage(config.storage, base_dir),
            "sessions": _absolute_sessions(config, base_dir / config.prompts_dir, source=root),
            "server": _absolute_server(config, base_dir),
            "mcp_servers": tuple(_launched_from(server, base_dir) for server in servers),
            "tool_sources": sources,
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


def _check_env_file_at_root(data: dict[str, Any], *, source: Path) -> None:
    """Refuse ``env_file`` dans ``profiles`` : il est lu avant le choix du profil.

    Accepté là, il passerait la validation et ne serait jamais lu — le fichier
    qu'il désigne peut lui-même donner ``LOOM_PROFILE``. Tous les profils sont
    vérifiés, pas seulement l'actif : l'erreur ne doit pas attendre la prod.
    """
    declared = data.get("profiles")
    if not isinstance(declared, dict):
        return
    for name, overrides in cast("dict[str, Any]", declared).items():
        if isinstance(overrides, dict) and ENV_FILE_KEY in overrides:
            raise ConfigError(
                f"profiles.{name}.{ENV_FILE_KEY} — refusé : le fichier d'environnement est lu "
                f"avant le choix du profil ; déclarer '{ENV_FILE_KEY}' à la racine",
                source=source,
            )


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


def _load_agents(
    agents_dir: Path, prompts_dir: Path, base_dir: Path
) -> list[tuple[AgentSpec, Path]]:
    """Les agents du dossier, chacun avec le fichier d'où il vient."""
    if not agents_dir.is_dir():
        raise ConfigError(f"Dossier d'agents introuvable : {agents_dir}")
    agents: list[tuple[AgentSpec, Path]] = []
    for file in sorted(f for f in agents_dir.iterdir() if f.suffix in AGENT_SUFFIXES):
        spec = _validate(AgentSpec, _read_yaml(file), source=file)
        agents.append((_prepared(spec, prompts_dir, base_dir, source=file), file))
    return agents


def _prepared(spec: AgentSpec, prompts_dir: Path, base_dir: Path, *, source: Path) -> AgentSpec:
    """Prompts, skills et schémas de sortie d'un agent, lus et vérifiés."""
    spec = _with_prompts(spec, prompts_dir, source=source)
    spec = _with_tool_skills(spec, prompts_dir, source=source)
    return _with_contracts(spec, base_dir, source=source)


def _culprit(error: ValidationError, loaded: Sequence[tuple[AgentSpec, Path]]) -> Path | None:
    """Le fichier d'agent que nomme l'erreur de la config entière, s'il est seul (M5).

    Un fichier d'agent est validé seul ; ce qu'il référence ailleurs (un modèle,
    un serveur MCP, une source, un autre agent, des capacités) ne l'est qu'avec
    la config entière, et l'erreur était attribuée à la racine de ``loom.yaml``
    — « (racine) — Agent 'agent' : modèle 'SONNET' non déclaré » pour une faute
    dans ``agents/agent.yaml`` (test M5 du 23/10). Les contrôles d'un agent
    commencent tous par « Agent '<nom>' » : quand toutes les erreurs nomment
    le même agent, et qu'il vient d'un fichier, c'est ce fichier. Sinon (une
    erreur de la racine, deux agents en cause), ``None`` : ``loom.yaml``.
    """
    files = {spec.name: file for spec, file in loaded}
    named: set[str] = set()
    for item in error.errors(include_url=False):
        found = _AGENT_NAMED.match(item["msg"].removeprefix("Value error, "))
        if found is None or item["loc"]:
            return None
        named.add(found.group(1))
    if len(named) != 1:
        return None
    return files.get(named.pop())


def _with_contracts(spec: AgentSpec, base_dir: Path, *, source: Path) -> AgentSpec:
    """Schémas de sortie donnés par fichier, lus : agent, rôles, outils Python, MCP et de paquet."""
    roles = tuple(
        role.model_copy(update={"output": _schema(role.output, base_dir, source=source)})
        for role in spec.roles
    )
    tools = tuple(
        tool.model_copy(update={"tools": _with_schemas(tool.tools, base_dir, source=source)})
        if isinstance(tool, McpTools | SourceTools)
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
        raise from_validation(exc, source=path, data=fields) from exc


def _with_prompts(spec: AgentSpec, prompts_dir: Path, *, source: Path) -> AgentSpec:
    """Rend absolus les chemins des prompts (``main`` et rôles), et vérifie qu'ils sont lisibles."""
    main = _with_prompt(spec.main, prompts_dir, "main", source=source)
    roles = tuple(
        _with_prompt(role, prompts_dir, f"roles[{role.name}]", source=source) for role in spec.roles
    )
    return spec.model_copy(update={"main": main, "roles": roles})


def _within_prompts(prompts_dir: Path, written: Path, label: str, *, source: Path) -> Path:
    """Le fichier d'un prompt ou d'un skill, qui doit rester dans ``prompts_dir``.

    Le chemin est comparé une fois résolu (``..``, chemin absolu, lien
    symbolique) : ``prompts_dir`` est protégé de tout outil fourni
    (``_protected_paths``), un dossier ouvert à ``loom.local_files`` ne l'est pas.
    """
    path = prompts_dir / written
    if not path.resolve().is_relative_to(prompts_dir.resolve()):
        raise ConfigError(
            f"{label} — le fichier doit être dans le dossier des prompts ({prompts_dir}) : {path}",
            source=source,
        )
    return path


def _hint(prompts_dir: Path, written: Path) -> str:
    """Un chemin qui recommence par le dossier des prompts : il est déjà relatif à lui."""
    parts = written.parts
    if parts and parts[0] == prompts_dir.name:
        return f" — le chemin est relatif à prompts_dir : écrire {Path(*parts[1:])}"
    return ""


def _with_prompt[R: BaseRole](role: R, prompts_dir: Path, label: str, *, source: Path) -> R:
    if role.system_file is None:
        return role
    prompt = _within_prompts(prompts_dir, role.system_file, f"{label}.system_file", source=source)
    if not prompt.is_file():
        raise ConfigError(
            f"{label}.system_file — prompt introuvable : {prompt}"
            f"{_hint(prompts_dir, role.system_file)}",
            source=source,
        )
    try:
        prompt.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"{label}.system_file — prompt illisible : {exc}", source=source) from exc
    return role.model_copy(update={"system_file": prompt})


def _with_tool_skills(spec: AgentSpec, prompts_dir: Path, *, source: Path) -> AgentSpec:
    """Skills d'outils donnés par fichier, lus : outils Python, références MCP et de paquet."""
    tools = tuple(
        tool.model_copy(
            update={"tools": _with_skills(tool.tools, prompts_dir, _ref_label(tool), source=source)}
        )
        if isinstance(tool, McpTools | SourceTools)
        else _skill_read(tool, prompts_dir, f"tools[{tool.python}]", source=source)
        for tool in spec.tools
    )
    return spec.model_copy(update={"tools": tools})


def _ref_label(ref: McpTools | SourceTools) -> str:
    return f"tools[mcp {ref.mcp}]" if isinstance(ref, McpTools) else f"tools[source {ref.source}]"


def _with_skills(
    overrides: dict[str, ToolOverrides], prompts_dir: Path, label: str, *, source: Path
) -> dict[str, ToolOverrides]:
    return {
        name: _skill_read(override, prompts_dir, f"{label}.tools[{name}]", source=source)
        for name, override in overrides.items()
    }


def _skill_read[T: (PythonTool, ToolOverrides)](
    declared: T, prompts_dir: Path, label: str, *, source: Path
) -> T:
    """Le skill lu depuis son fichier et rangé dans ``skill`` ; ``skill_file`` n'est plus gardé.

    Lu une fois, au chargement, comme ``schema_file`` : une faute se voit dès
    ``loom validate``. Les mêmes règles que pour un skill en ligne s'appliquent
    (``checked_skill``).
    """
    if declared.skill_file is None:
        return declared
    where = f"{label}.skill_file"
    path = _within_prompts(prompts_dir, declared.skill_file, where, source=source)
    if not path.is_file():
        raise ConfigError(
            f"{where} — skill introuvable : {path}{_hint(prompts_dir, declared.skill_file)}",
            source=source,
        )
    try:
        text = checked_skill(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"{where} — skill illisible : {exc}", source=source) from exc
    except ValueError as exc:
        raise ConfigError(f"{where} — {exc} : {path}", source=source) from exc
    return declared.model_copy(update={"skill": text, "skill_file": None})


def _absolute_sessions(config: LoomConfig, prompts_dir: Path, *, source: Path) -> object:
    """Prompt surchargé de la compaction, rapporté au dossier des prompts."""
    sessions = config.sessions
    compaction = sessions.compaction
    if compaction is None or compaction.system_file is None:
        return sessions
    label = "sessions.compaction.system_file"
    prompt = _within_prompts(prompts_dir, compaction.system_file, label, source=source)
    if not prompt.is_file():
        raise ConfigError(f"{label} — prompt introuvable : {prompt}", source=source)
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
            # Le prompt par son fichier quand il en vient : au test M7 du 23/10,
            # « main.system » ne disait pas où corriger.
            where = f"{label}.system"
            if role.system_file is not None:
                written = Path(role.system_file)
                shown_file = (
                    written.relative_to(source.parent)
                    if written.is_relative_to(source.parent)
                    else written
                )
                where = f"{label}.system_file ({shown_file})"
            try:
                template = Template.parse(system_text(role))
            except TemplateError as exc:
                raise ConfigError(f"Agent {spec.name!r}, {where} — {exc}", source=source) from exc
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
                        f"Agent {spec.name!r}, {where} — {shown} : variable non "
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
        data = load_yaml(content)
    except DuplicateKey as exc:
        raise ConfigError(str(exc), source=path) from exc
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
        raise from_validation(exc, source=source, data=data) from exc
