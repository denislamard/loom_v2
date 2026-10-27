# SPDX-License-Identifier: Apache-2.0
"""Outil fourni ``loom.local_files`` : dossiers permis, droits, approbation par appel.

L'outil est construit par sa fabrique sur des dossiers temporaires ; ses appels
passent par ``invoke`` et ``assess_call``, les deux chemins du moteur.
"""

import asyncio
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any, Final

import pytest
import yaml
from conftest import MODEL, ConfigFactory, demo_agent
from pydantic import JsonValue, ValidationError

import loom_ia.tools_builtin._folders as folders_module
from loom_ia.access.cli import main
from loom_ia.adapters.stores import InMemoryEventStore
from loom_ia.config import ConfigError, agent_json_schema, load_config
from loom_ia.core.events import ToolCompleted
from loom_ia.core.model import DEFAULT_TENANT, RunId, RunStatus, SessionId, TenantId
from loom_ia.core.ports import CallAssessment, ToolContext, ToolError
from loom_ia.engine import begin_run, drive
from loom_ia.runtime import build_agent
from loom_ia.tenancy import Tenants
from loom_ia.tools_builtin import BuiltinContext, Protected
from loom_ia.tools_builtin.local_files import (
    MAX_READ_BYTES,
    TEMP_PREFIX,
    LocalFilesTool,
    build,
)
from loom_ia.tools_builtin.local_files_params import FolderSpec, LocalFilesParams

CONTEXT: Final = ToolContext(
    tenant_id=DEFAULT_TENANT,
    session_id=SessionId("s"),
    run_id=RunId("r"),
    call_id="c1",
    agent="demo",
)
# Tous les droits, sans aucune approbation.
ALL: Final = {
    "rights": ["read", "write", "overwrite", "delete"],
    "approval": {"overwrite": "never", "delete": "never"},
}


def make(tmp_path: Path, *folders: dict[str, Any], **context: Any) -> LocalFilesTool:
    params = LocalFilesParams.model_validate({"folders": list(folders)})
    return build(params, BuiltinContext(base_dir=tmp_path, **context))


@pytest.fixture
def disk(tmp_path: Path) -> Path:
    """``work`` (tous les droits), ``clients`` (lire, écrire avec approbation), ``ailleurs``."""
    for name in ("work/sub", "work/archives", "clients", "ailleurs"):
        (tmp_path / name).mkdir(parents=True)
    (tmp_path / "work/sub/a.txt").write_text("bonjour\n", encoding="utf-8")
    (tmp_path / "work/.env").write_text("SECRET=1\n", encoding="utf-8")
    (tmp_path / "work/archives/vieux.txt").write_text("vieux", encoding="utf-8")
    (tmp_path / "clients/c1.txt").write_text("client 1", encoding="utf-8")
    (tmp_path / "ailleurs/secret.txt").write_text("hors champ", encoding="utf-8")
    return tmp_path


@pytest.fixture
def files(disk: Path) -> LocalFilesTool:
    return make(
        disk,
        {"name": "work", "path": "work", **ALL},
        {"name": "archives", "path": "work/archives", "rights": ["read"]},
        {
            "name": "clients",
            "path": "clients",
            "rights": ["read", "write"],
            "approval": {"write": "always"},
        },
    )


async def call(tool: LocalFilesTool, **arguments: JsonValue) -> dict[str, Any]:
    output = await tool.invoke(dict(arguments), CONTEXT)
    assert isinstance(output.data, dict)
    return output.data


async def refused(tool: LocalFilesTool, match: str, **arguments: JsonValue) -> None:
    with pytest.raises(ToolError, match=match):
        await tool.invoke(dict(arguments), CONTEXT)


# --- Ce que voit le modèle ------------------------------------------------------------


def test_the_spec_names_the_folders_not_their_paths(files: LocalFilesTool, disk: Path) -> None:
    spec = files.spec
    assert spec.name == "local_files"
    assert "- work : read, write, overwrite, delete" in spec.description
    assert "- clients : read, write (attend une approbation humaine : write)" in spec.description
    assert str(disk) not in spec.description
    assert spec.input_schema["required"] == ["action", "path"]
    # Le pire des cas, pour ce qui ne regarde pas l'appel : la garde du journal durable.
    assert (spec.side_effects, spec.approval) == ("irreversible", "always")


def test_a_read_only_tool_declares_no_effect(disk: Path) -> None:
    tool = make(disk, {"name": "work", "path": "work", "rights": ["read"]})
    assert (tool.spec.side_effects, tool.spec.approval) == ("none", "never")


def test_settings_lines_for_validate(files: LocalFilesTool, disk: Path) -> None:
    assert files.settings_lines() == (
        f"work → {disk / 'work'} : read, write, overwrite, delete",
        f"archives → {disk / 'work/archives'} : read",
        f"clients → {disk / 'clients'} : read, write ; approbation : write",
    )


# --- Lire, lister, écrire, supprimer ------------------------------------------------


async def test_list_hides_hidden_files(files: LocalFilesTool) -> None:
    listed = await call(files, action="list", path="work")
    assert listed == {
        "path": "work",
        "entries": [{"name": "archives", "type": "dir"}, {"name": "sub", "type": "dir"}],
        "truncated": False,
    }
    listed = await call(files, action="list", path="work/sub")
    assert listed["entries"] == [{"name": "a.txt", "type": "file", "size": 8}]


async def test_read(files: LocalFilesTool) -> None:
    assert await call(files, action="read", path="work/sub/a.txt") == {
        "path": "work/sub/a.txt",
        "content": "bonjour\n",
        "size": 8,
    }


async def test_write_creates_folders_and_refuses_to_replace(
    files: LocalFilesTool, disk: Path
) -> None:
    done = await call(files, action="write", path="work/notes/2026/n.md", content="# Notes")
    assert done == {"path": "work/notes/2026/n.md", "bytes": 7, "written": True, "replaced": False}
    assert (disk / "work/notes/2026/n.md").read_text(encoding="utf-8") == "# Notes"
    await refused(
        files,
        "existe déjà : overwrite=true",
        action="write",
        path="work/notes/2026/n.md",
        content="v2",
    )
    done = await call(
        files, action="write", path="work/notes/2026/n.md", content="v2", overwrite=True
    )
    assert (done["written"], done["replaced"]) == (True, True)
    assert (disk / "work/notes/2026/n.md").read_text(encoding="utf-8") == "v2"
    # Pas de fichier temporaire laissé derrière.
    assert not [p for p in (disk / "work/notes/2026").iterdir() if p.name.startswith(TEMP_PREFIX)]


async def test_writing_the_same_content_again_changes_nothing(
    files: LocalFilesTool, disk: Path
) -> None:
    """C'est ce qui rend l'écriture sûre à refaire après une interruption."""
    target = disk / "work/sub/a.txt"
    before = target.stat().st_mtime_ns
    done = await call(files, action="write", path="work/sub/a.txt", content="bonjour\n")
    assert done == {"path": "work/sub/a.txt", "bytes": 8, "written": False}
    assert target.stat().st_mtime_ns == before


async def test_a_replaced_file_keeps_its_permissions(files: LocalFilesTool, disk: Path) -> None:
    target = disk / "work/sub/a.txt"
    target.chmod(0o640)
    await call(files, action="write", path="work/sub/a.txt", content="autre", overwrite=True)
    assert stat.S_IMODE(target.stat().st_mode) == 0o640


async def test_a_new_file_follows_the_umask(files: LocalFilesTool, disk: Path) -> None:
    previous = os.umask(0o022)
    try:
        await call(files, action="write", path="work/neuf.txt", content="x")
    finally:
        os.umask(previous)
    assert stat.S_IMODE((disk / "work/neuf.txt").stat().st_mode) == 0o644


async def test_delete_removes_files_only(files: LocalFilesTool, disk: Path) -> None:
    assert await call(files, action="delete", path="work/sub/a.txt") == {
        "path": "work/sub/a.txt",
        "deleted": True,
    }
    assert not (disk / "work/sub/a.txt").exists()
    await refused(
        files,
        "est un dossier : delete ne supprime que des fichiers",
        action="delete",
        path="work/sub",
    )
    await refused(files, "fichier introuvable", action="delete", path="work/sub/a.txt")


async def test_size_and_encoding_limits(files: LocalFilesTool, disk: Path) -> None:
    (disk / "work/gros.txt").write_bytes(b"x" * (MAX_READ_BYTES + 1))
    await refused(files, "au-delà de", action="read", path="work/gros.txt")
    (disk / "work/latin1.txt").write_bytes("été".encode("latin-1"))
    await refused(files, "n'est pas un fichier texte UTF-8", action="read", path="work/latin1.txt")
    await refused(
        files, "au-delà de", action="write", path="work/x.txt", content="é" * MAX_READ_BYTES
    )


# --- Ce qui est refusé -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("arguments", "match"),
    [
        ({"action": "read", "path": "/etc/passwd"}, "Chemin absolu refusé"),
        ({"action": "read", "path": "work/../ailleurs/secret.txt"}, "« .. » n'est pas permis"),
        ({"action": "read", "path": "ailleurs/secret.txt"}, "Dossier inconnu : 'ailleurs'"),
        ({"action": "read", "path": "work/.env"}, "cachés sont inaccessibles"),
        ({"action": "write", "path": "work/.git/config", "content": "x"}, "cachés"),
        ({"action": "read", "path": ""}, "<dossier>/<chemin>"),
        ({"action": "read", "path": "work"}, "work est un dossier"),
        ({"action": "write", "path": "work/x.txt"}, "write attend 'content'"),
        (
            {"action": "read", "path": "work/sub/a.txt", "content": "x"},
            "'content' ne sert qu'à write",
        ),
        (
            {"action": "delete", "path": "work/sub/a.txt", "overwrite": True},
            "'overwrite' ne sert qu'à write",
        ),
        ({"action": "move", "path": "work/sub/a.txt"}, "Arguments invalides"),
        ({"action": "read", "path": "work/sub/a.txt", "x": 1}, "Arguments invalides"),
        ({"action": "delete", "path": "clients/c1.txt"}, "Droit delete refusé sur clients/c1.txt"),
        (
            {"action": "write", "path": "clients/c1.txt", "content": "x", "overwrite": True},
            "Droit overwrite refusé",
        ),
    ],
)
async def test_refusals(files: LocalFilesTool, arguments: dict[str, JsonValue], match: str) -> None:
    await refused(files, match, **arguments)


async def test_a_nested_folder_keeps_its_own_rights(files: LocalFilesTool, disk: Path) -> None:
    """``archives`` est dans ``work`` mais en lecture seule, même nommé par ``work/archives``."""
    await refused(
        files,
        "Droit delete refusé sur archives/vieux.txt",
        action="delete",
        path="work/archives/vieux.txt",
    )
    await refused(files, "Droit write refusé", action="write", path="archives/n.txt", content="x")
    read = await call(files, action="read", path="work/archives/vieux.txt")
    assert read["path"] == "archives/vieux.txt"
    assert (disk / "work/archives/vieux.txt").exists()


async def test_links_cannot_leave_their_folder(files: LocalFilesTool, disk: Path) -> None:
    (disk / "work/fuite").symlink_to(disk / "ailleurs")
    await refused(
        files, "mène hors des dossiers permis", action="read", path="work/fuite/secret.txt"
    )
    # Un lien vers un autre dossier déclaré : il a ses propres droits, on y passe par son nom.
    (disk / "work/vers_clients").symlink_to(disk / "clients")
    await refused(
        files,
        "passe par un lien vers le dossier clients",
        action="read",
        path="work/vers_clients/c1.txt",
    )
    # Un lien vers un fichier caché du même dossier.
    (disk / "work/visible").symlink_to(disk / "work/.env")
    await refused(files, "cachés sont inaccessibles", action="read", path="work/visible")
    # Un lien qui reste dans son dossier est suivi.
    (disk / "work/raccourci.txt").symlink_to(disk / "work/sub/a.txt")
    assert (await call(files, action="read", path="work/raccourci.txt"))["content"] == "bonjour\n"


async def test_the_error_does_not_reveal_the_real_path(files: LocalFilesTool, disk: Path) -> None:
    (disk / "work/fermé").mkdir()
    (disk / "work/fermé").chmod(0)
    try:
        if os.access(disk / "work/fermé", os.R_OK):
            pytest.skip("droits ignorés (root)")
        with pytest.raises(ToolError) as caught:
            await files.invoke({"action": "list", "path": "work/fermé"}, CONTEXT)
        assert "work/fermé" in caught.value.message
        assert str(disk) not in caught.value.message
    finally:
        (disk / "work/fermé").chmod(0o755)


async def test_dot_and_unencodable_content_are_refused(files: LocalFilesTool) -> None:
    for path in (".", "./", "./."):
        await refused(files, "<dossier>/<chemin>", action="list", path=path)
    await refused(
        files, "ne s'écrivent pas en UTF-8", action="write", path="work/x.txt", content="\ud800"
    )


async def test_a_listing_hides_links_that_leave_the_folder(
    files: LocalFilesTool, disk: Path
) -> None:
    (disk / "work/fuite.txt").symlink_to(disk / "ailleurs/secret.txt")
    (disk / "work/ici.txt").symlink_to(disk / "work/sub/a.txt")
    listed = await call(files, action="list", path="work")
    names = [entry["name"] for entry in listed["entries"]]
    assert "fuite.txt" not in names and "ici.txt" in names


async def test_deleting_a_link_removes_the_link_only(files: LocalFilesTool, disk: Path) -> None:
    (disk / "work/raccourci.txt").symlink_to(disk / "work/sub/a.txt")
    (disk / "work/fuite.txt").symlink_to(disk / "ailleurs/secret.txt")
    await call(files, action="delete", path="work/raccourci.txt")
    assert not (disk / "work/raccourci.txt").is_symlink()
    assert (disk / "work/sub/a.txt").exists()
    # Le lien est dans le dossier : il se supprime, ce qu'il désigne dehors reste.
    await call(files, action="delete", path="work/fuite.txt")
    assert (disk / "ailleurs/secret.txt").exists()


async def test_a_read_only_file_is_not_replaced(files: LocalFilesTool, disk: Path) -> None:
    target = disk / "work/sub/a.txt"
    target.chmod(0o444)
    try:
        if os.access(target, os.W_OK):
            pytest.skip("droits ignorés (root)")
        await refused(
            files,
            "en lecture seule",
            action="write",
            path="work/sub/a.txt",
            content="x",
            overwrite=True,
        )
        assert target.read_text(encoding="utf-8") == "bonjour\n"
    finally:
        target.chmod(0o644)


async def test_parallel_writes_without_overwrite_do_not_replace_each_other(
    files: LocalFilesTool, disk: Path
) -> None:
    """Deux appels d'un même lot vers un même nouveau fichier : un seul l'écrit."""
    for attempt in range(20):
        path = f"work/course-{attempt}.txt"
        results = await asyncio.gather(
            files.invoke({"action": "write", "path": path, "content": "premier"}, CONTEXT),
            files.invoke({"action": "write", "path": path, "content": "second"}, CONTEXT),
            return_exceptions=True,
        )
        written = [r for r in results if not isinstance(r, BaseException)]
        assert len(written) == 1, results
        [error] = [r for r in results if isinstance(r, BaseException)]
        assert isinstance(error, ToolError) and "existe déjà" in error.message
        assert (disk / path).read_text(encoding="utf-8") in {"premier", "second"}


async def test_without_hard_links_a_new_file_is_still_created_exclusively(
    files: LocalFilesTool, disk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_link(*_: object, **__: object) -> None:
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(os, "link", no_link)
    await call(files, action="write", path="work/sans-lien.txt", content="x")
    assert (disk / "work/sans-lien.txt").read_text(encoding="utf-8") == "x"
    assert not [p for p in (disk / "work").iterdir() if p.name.startswith(TEMP_PREFIX)]
    await refused(files, "existe déjà", action="write", path="work/sans-lien.txt", content="y")


async def test_a_file_writable_by_nobody_is_not_replaced_even_by_root(
    files: LocalFilesTool, disk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``os.access`` dit toujours oui à root : les droits du fichier comptent quand même."""
    target = disk / "work/sub/a.txt"
    target.chmod(0o444)

    def always(*_: object, **__: object) -> bool:
        return True

    monkeypatch.setattr(os, "access", always)
    try:
        await refused(
            files,
            "en lecture seule",
            action="write",
            path="work/sub/a.txt",
            content="x",
            overwrite=True,
        )
    finally:
        target.chmod(0o644)


def test_an_interrupted_creation_leaves_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un fichier tronqué ferait croire à un contenu, et une reprise dirait « existe déjà »."""

    def full(_: int) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "fsync", full)
    with pytest.raises(OSError, match="No space left"):
        folders_module.write_new(tmp_path / "n.txt", b"contenu")
    assert not (tmp_path / "n.txt").exists()


async def test_overwrite_creates_a_missing_file(files: LocalFilesTool, disk: Path) -> None:
    done = await call(files, action="write", path="work/neuf.md", content="x", overwrite=True)
    assert (done["written"], done["replaced"]) == (True, False)


# --- Évaluation de chaque appel -----------------------------------------------------


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ({"action": "read", "path": "work/sub/a.txt"}, CallAssessment(side_effects="none")),
        ({"action": "list", "path": "clients"}, CallAssessment(side_effects="none")),
        (
            {"action": "write", "path": "work/n.txt", "content": "x"},
            CallAssessment(side_effects="reversible", idempotent=True),
        ),
        (
            {"action": "write", "path": "work/n.txt", "content": "x", "overwrite": True},
            CallAssessment(side_effects="irreversible", idempotent=True),
        ),
        (
            {"action": "delete", "path": "work/sub/a.txt"},
            CallAssessment(side_effects="irreversible"),
        ),
        (
            {"action": "write", "path": "clients/c2.txt", "content": "x"},
            CallAssessment(
                approval="always",
                side_effects="reversible",
                idempotent=True,
                reason="écrire clients/c2.txt : le dossier clients demande une approbation "
                "pour write",
            ),
        ),
        # Refusé à l'exécution pour la même raison : rien à approuver, aucun effet.
        ({"action": "delete", "path": "clients/c1.txt"}, CallAssessment(side_effects="none")),
        ({"action": "delete", "path": "archives/vieux.txt"}, CallAssessment(side_effects="none")),
        ({"action": "read", "path": "/etc/passwd"}, CallAssessment(side_effects="none")),
        ({"action": "nope", "path": 3}, CallAssessment(side_effects="none")),
    ],
)
def test_each_call_is_assessed(
    files: LocalFilesTool, arguments: dict[str, JsonValue], expected: CallAssessment
) -> None:
    assert files.assess_call(arguments) == expected


def test_overwrite_asks_when_either_right_asks(disk: Path) -> None:
    tool = make(
        disk, {"name": "work", "path": "work", "rights": ["read", "write", "overwrite", "delete"]}
    )
    asked = tool.assess_call(
        {"action": "write", "path": "work/a", "content": "x", "overwrite": True}
    )
    assert asked.approval == "always"
    assert asked.reason is not None and "pour overwrite" in asked.reason
    assert (
        tool.assess_call({"action": "write", "path": "work/a", "content": "x"}).approval == "never"
    )
    assert tool.assess_call({"action": "delete", "path": "work/a"}).approval == "always"


def test_the_assessment_does_not_touch_the_disk(disk: Path) -> None:
    """Il ne dépend que des arguments et de la config : même chose à la reprise et au rejeu."""
    tool = make(disk, {"name": "clients", "path": "clients", "rights": ["read", "write"]})
    (disk / "clients").rename(disk / "parti")
    assert (
        tool.assess_call({"action": "write", "path": "clients/x", "content": "y"}).side_effects
        == "reversible"
    )


# --- La fabrique ---------------------------------------------------------------------


def test_relative_paths_follow_the_config(disk: Path) -> None:
    tool = make(disk, {"name": "work", "path": "work", "rights": ["read"]})
    assert tool.folders[0].root == (disk / "work").resolve()


@pytest.mark.parametrize(
    ("exposed", "protected", "subtree", "match"),
    [
        # Le dossier de la config : ni lui, ni un dossier qui le contient.
        ("work", "work", False, "est la config"),
        (".", "work", False, r"contient la config \("),
        # Un dossier protégé en entier : pas même un de ses sous-dossiers.
        ("work/sub", "work", True, "est dans la config"),
        ("work", "work/sub", True, "contient la config"),
    ],
)
def test_a_folder_that_would_expose_loom_is_refused(
    disk: Path, exposed: str, protected: str, subtree: bool, match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        make(
            disk,
            {"name": "w", "path": exposed, "rights": ["read"]},
            protected=(Protected(disk / protected, "la config", subtree=subtree),),
        )


def test_a_subfolder_of_the_config_folder_can_be_exposed(disk: Path) -> None:
    tool = make(
        disk,
        {"name": "w", "path": "work/sub", "rights": ["read"]},
        protected=(Protected(disk / "work", "le dossier de la config"),),
    )
    assert tool.folders[0].name == "w"


@pytest.mark.parametrize(
    ("folders", "match"),
    [
        ([{"name": "a", "path": "absent", "rights": ["read"]}], "n'existe pas"),
        (
            [
                {"name": "a", "path": "work", "rights": ["read"]},
                {"name": "b", "path": "work/../work", "rights": ["read"]},
            ],
            "dossiers 'a' et 'b' : même chemin",
        ),
    ],
)
def test_the_factory_checks_the_folders(
    disk: Path, folders: list[dict[str, Any]], match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        make(disk, *folders)


# --- Par la config : montage, run, loom validate -------------------------------------

JSONL: Final = {"events": {"backend": "jsonl", "path": "journal"}}


def files_agent(**reference: Any) -> dict[str, Any]:
    return demo_agent(tools=[{"python": "loom.local_files", **reference}])


def test_mounted_from_the_config(demo: ConfigFactory, tmp_path: Path) -> None:
    (tmp_path / "espace").mkdir()
    folders = [{"name": "espace", "path": "espace", "rights": ["read", "write"]}]
    path = demo(agents=[files_agent(timeout=7, folders=folders)], storage=JSONL)
    agent = build_agent(load_config(path), "demo", InMemoryEventStore())
    (files,) = agent.context.tools.specs
    assert (files.name, files.timeout, files.approval) == ("local_files", 7, "never")


@pytest.mark.parametrize(
    ("folder", "match"),
    [
        (".", "est le dossier de la config"),
        ("agents", "est le dossier des agents"),
        ("prompts", "est le dossier des prompts"),
        ("journal", "est le journal"),
        ("journal/sous", "est dans le journal"),
        ("outils_acces.py", "n'existe pas ou n'est pas un dossier"),
    ],
)
def test_loom_paths_are_never_exposed(
    demo: ConfigFactory, tmp_path: Path, folder: str, match: str
) -> None:
    (tmp_path / "journal" / "sous").mkdir(parents=True)
    folders = [{"name": "w", "path": folder, "rights": ["read"]}]
    path = demo(agents=[files_agent(folders=folders)], storage=JSONL)
    with pytest.raises(ConfigError, match=rf"outil 'loom\.local_files' : dossier 'w' : .*{match}"):
        build_agent(load_config(path), "demo", InMemoryEventStore())


def test_the_storage_of_every_tenant_is_protected(demo: ConfigFactory, tmp_path: Path) -> None:
    """Le journal d'un client n'est exposé à aucun autre : ni à lui, ni au client par défaut."""
    (tmp_path / "dupont").mkdir()
    folders = [{"name": "w", "path": "dupont", "rights": ["read"]}]
    storage = {"events": {"backend": "jsonl", "path": "dupont"}}
    tenants = [{"id": "default"}, {"id": "dupont", "storage": storage}]
    path = demo(agents=[files_agent(folders=folders)], storage=JSONL, tenants=tenants)
    config = load_config(path)
    registry = Tenants(config)
    for tenant in ("default", "dupont"):
        with pytest.raises(ConfigError, match="est le journal"):
            build_agent(config, "demo", InMemoryEventStore(), tenant=registry.get(TenantId(tenant)))


@pytest.mark.parametrize(
    ("layout", "exposed", "reference", "match"),
    [
        # Un dossier de modules à côté de la config, même sans référence.
        ("code/devis.py", "code", None, r"est du code Python \(code/\)"),
        # Un module cité en ``module:attr`` dans un dossier sans module à sa racine.
        ("lib/outils/devis.py", "lib", "lib.outils.devis:prix", "est le module Python 'lib'"),
    ],
)
def test_the_code_loom_loads_is_never_exposed(
    demo: ConfigFactory,
    tmp_path: Path,
    layout: str,
    exposed: str,
    reference: str | None,
    match: str,
) -> None:
    module = tmp_path / layout
    module.parent.mkdir(parents=True)
    module.write_text("def prix() -> int:\n    return 1\n", encoding="utf-8")
    folders = [{"name": "w", "path": exposed, "rights": ["read"]}]
    tools: list[dict[str, Any]] = [{"python": "loom.local_files", "folders": folders}]
    if reference is not None:
        tools.append({"python": reference})
    path = demo(agents=[demo_agent(tools=tools)], storage=JSONL)
    with pytest.raises(ConfigError, match=match):
        build_agent(load_config(path), "demo", InMemoryEventStore())


async def test_a_run_waits_only_for_the_call_that_asks(demo: ConfigFactory, tmp_path: Path) -> None:
    (tmp_path / "espace").mkdir()
    (tmp_path / "espace" / "a.txt").write_text("bonjour", encoding="utf-8")
    script: list[dict[str, Any]] = [
        {
            "text": "Je range.",
            "tool_calls": [
                {"name": "local_files", "arguments": {"action": "read", "path": "espace/a.txt"}},
                {"name": "local_files", "arguments": {"action": "delete", "path": "espace/a.txt"}},
            ],
        },
        {"text": "Rangé."},
    ]
    folders = [{"name": "espace", "path": "espace", "rights": ["read", "delete"]}]
    path = demo(
        agents=[files_agent(folders=folders)],
        models=[{**MODEL, "params": {"script": script}}],
        storage=JSONL,
    )
    store = InMemoryEventStore()
    agent = build_agent(load_config(path), "demo", store)
    try:
        run = await begin_run(agent.context, "Range.")
        state = await drive(agent.context, run.run_id)
    finally:
        await agent.aclose()
    assert state.status is RunStatus.PAUSED
    [pending] = state.awaiting
    assert pending.reason == (
        "supprimer espace/a.txt : le dossier espace demande une approbation pour delete "
        "(effets : irreversible)"
    )
    events = await store.read(DEFAULT_TENANT, run.session_id)
    [read] = [e.payload for e in events if isinstance(e.payload, ToolCompleted)]
    assert read.output.data == {"path": "espace/a.txt", "content": "bonjour", "size": 7}
    assert (tmp_path / "espace" / "a.txt").exists()


def test_validate_shows_the_folders(
    demo: ConfigFactory, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "espace").mkdir()
    folders = [
        {
            "name": "espace",
            "path": "espace",
            "rights": ["read", "write"],
            "approval": {"write": "always"},
        }
    ]
    path = demo(agents=[files_agent(timeout=5, folders=folders)], storage=JSONL)
    assert main(["--config", str(path), "validate"]) == 0
    out = capsys.readouterr().out
    assert "Fournis    : loom.local_files\n" in out
    assert (
        f"    outil local_files : espace → {(tmp_path / 'espace').resolve()} : read, write ; "
        "approbation : write\n" in out
    )


def test_an_approval_on_a_memory_journal_says_which_calls(
    demo: ConfigFactory, tmp_path: Path
) -> None:
    (tmp_path / "espace").mkdir()
    folders = [{"name": "espace", "path": "espace", "rights": ["read", "delete"]}]
    path = demo(agents=[files_agent(folders=folders)])
    with pytest.raises(ConfigError, match="outil 'local_files' dont certains appels attendent"):
        build_agent(load_config(path), "demo", InMemoryEventStore())


def test_a_mcp_server_protects_its_scripts_not_what_it_serves(
    demo: ConfigFactory, tmp_path: Path
) -> None:
    """Le dossier qu'un serveur filesystem sert reste exposable ; le dossier de son script, non."""
    for folder in ("docs", "servers/crm"):
        (tmp_path / folder).mkdir(parents=True)
    (tmp_path / "servers/crm/server.js").write_text("// serveur", encoding="utf-8")
    servers = [
        {
            "name": "fs",
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "srv-fs", "docs", "."],
        },
        {"name": "crm", "transport": "stdio", "command": "node", "args": ["servers/crm/server.js"]},
        {"name": "haut", "transport": "stdio", "command": "srv", "cwd": ".."},
    ]

    def mount(exposed: str) -> None:
        folders = [{"name": "w", "path": exposed, "rights": ["read"]}]
        path = demo(agents=[files_agent(folders=folders)], storage=JSONL, mcp_servers=servers)
        build_agent(load_config(path), "demo", InMemoryEventStore())

    mount("docs")
    with pytest.raises(ConfigError, match="le code du serveur MCP 'crm'"):
        mount("servers/crm")


def test_a_package_found_elsewhere_is_protected(
    demo: ConfigFactory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un paquet en disposition ``src/`` : Python le trouve là, c'est là qu'on le protège."""
    package = tmp_path / "src" / "monpaquet_essai"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "devis.py").write_text("def prix() -> int:\n    return 1\n", encoding="utf-8")
    monkeypatch.setattr(sys, "path", [str(tmp_path / "src"), *sys.path])
    folders = [{"name": "w", "path": "src/monpaquet_essai", "rights": ["read"]}]
    tools: list[dict[str, Any]] = [
        {"python": "loom.local_files", "folders": folders},
        {"python": "monpaquet_essai.devis:prix"},
    ]
    path = demo(agents=[demo_agent(tools=tools)], storage=JSONL)
    with pytest.raises(ConfigError, match="le module Python 'monpaquet_essai'"):
        build_agent(load_config(path), "demo", InMemoryEventStore())


# --- Paramètres : rights (accordés) et approval (qui attend un humain) -----------------


def folder(**fields: Any) -> FolderSpec:
    return FolderSpec.model_validate({"name": "work", "path": "work", **fields})


@pytest.mark.parametrize(
    ("fields", "internal"),
    [
        # Liste seule : chaque droit prend son approbation par défaut.
        (
            {"rights": ["read", "write", "overwrite", "delete"]},
            {"read": "never", "write": "never", "overwrite": "always", "delete": "always"},
        ),
        ({"rights": ["read"]}, {"read": "never"}),
        # approval partiel : seuls les droits donnés changent.
        (
            {"rights": ["read", "write", "delete"], "approval": {"delete": "never"}},
            {"read": "never", "write": "never", "delete": "never"},
        ),
        (
            {"rights": ["read", "write"], "approval": {"write": "always"}},
            {"read": "never", "write": "always"},
        ),
        # Redire le défaut ne change rien.
        (
            {"rights": ["read", "delete"], "approval": {"delete": "always"}},
            {"read": "never", "delete": "always"},
        ),
        # approval vide, ou sans valeur (clé YAML vide) : comme absent.
        ({"rights": ["read", "write"], "approval": {}}, {"read": "never", "write": "never"}),
        ({"rights": ["read", "write"], "approval": None}, {"read": "never", "write": "never"}),
    ],
)
def test_rights_and_approval_resolve_to_the_internal_table(
    fields: dict[str, Any], internal: dict[str, str]
) -> None:
    assert folder(**fields).rights == internal


def test_the_internal_table_follows_the_order_of_the_rights() -> None:
    """Quel que soit l'ordre de la config : read, write, overwrite, delete."""
    found = folder(rights=["delete", "overwrite", "read", "write"], approval={"read": "always"})
    assert list(found.rights) == ["read", "write", "overwrite", "delete"]


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        (
            {"rights": ["read", "write"], "approval": {"delete": "never"}},
            "dossier 'work' : approval 'delete' porte sur un droit non accordé — l'ajouter à "
            "'rights', ou le retirer d'approval",
        ),
        (
            {"rights": ["read", "write"], "approval": {"write": "yes"}},
            "dossier 'work' : approval 'write' vaut 'yes' — attendu : never (sans approbation) "
            "ou always (approbation humaine)",
        ),
        # « yes » sans guillemets : un booléen en YAML.
        ({"rights": ["write"], "approval": {"write": True}}, "approval 'write' vaut True"),
        # policy est une approbation d'outil, pas d'un droit.
        ({"rights": ["write"], "approval": {"write": "policy"}}, "approval 'write' vaut 'policy'"),
        (
            {"rights": ["read"], "approval": {"remove": "never"}},
            "dossier 'work' : approval 'remove' — droit inconnu (droits : read, write, "
            "overwrite, delete)",
        ),
        (
            {"rights": ["read"], "approval": ["read"]},
            "dossier 'work' : 'approval' attend une table droit → never ou always",
        ),
        ({"rights": "read"}, "dossier 'work' : 'rights' attend une liste de droits"),
        ({"rights": []}, "dossier 'work' : 'rights' ne peut pas être vide"),
        ({"rights": {}}, "dossier 'work' : 'rights' ne peut pas être vide"),
        ({"rights": ["read", "write", "read"]}, "dossier 'work' : droit donné deux fois : read"),
        (
            {"rights": ["read", "remove"]},
            "Input should be 'read', 'write', 'overwrite' or 'delete'",
        ),
        ({"rights": ["read", "overwrite"]}, "'overwrite' (remplacer un fichier) exige 'write'"),
        ({"approval": {"read": "never"}}, "rights\n  Field required"),
    ],
)
def test_rights_and_approval_refused(fields: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=re.escape(message)):
        folder(**fields)


@pytest.mark.parametrize(
    ("table", "written"),
    [
        (
            {"read": "never", "write": "never", "overwrite": "never", "delete": "never"},
            "rights: [read, write, overwrite, delete] et approval: { overwrite: never, "
            "delete: never }",
        ),
        (
            {"write": "always", "read": "never"},
            "rights: [read, write] et approval: { write: always }",
        ),
        ({"read": "never"}, "rights: [read]"),
        ({"read": "never", "delete": "always"}, "rights: [read, delete]"),
    ],
)
def test_the_old_table_is_refused_with_what_to_write(table: dict[str, str], written: str) -> None:
    """L'ancienne table droit → approbation : refusée, avec l'écriture équivalente."""
    with pytest.raises(ValidationError) as raised:
        folder(rights=table)
    said = str(raised.value)
    assert (
        "dossier 'work' : 'rights' en table droit → approbation n'est plus accepté (never "
        "voulait dire « sans approbation », pas « refusé ») — écrire " + written
    ) in said
    # L'écriture proposée, recopiée telle quelle, donne ce que l'ancienne table voulait dire.
    rights = yaml.safe_load(written.split("rights: ")[1].split(" et ")[0])
    approval = yaml.safe_load(written.split("approval: ")[1]) if " et " in written else None
    assert folder(rights=rights, approval=approval).rights == table


def test_the_old_table_with_approval_is_refused_as_well() -> None:
    with pytest.raises(ValidationError, match="'rights' en table droit → approbation n'est plus"):
        folder(rights={"read": "never"}, approval={})


def test_the_old_table_is_refused_when_the_config_loads(demo: ConfigFactory) -> None:
    folders = [{"name": "work", "path": "/tmp", "rights": {"read": "never", "delete": "never"}}]
    with pytest.raises(ConfigError) as raised:
        load_config(demo(agents=[files_agent(folders=folders)]))
    assert (
        "folders.0 — dossier 'work' : 'rights' en table droit → approbation n'est plus accepté"
    ) in str(raised.value)
    assert "écrire rights: [read, delete] et approval: { delete: never }" in str(raised.value)


def test_approval_on_the_reference_says_where_it_goes(demo: ConfigFactory) -> None:
    folders = [{"name": "w", "path": "/tmp", "rights": ["read"]}]
    with pytest.raises(ConfigError, match=r"par dossier \(folders\[\]\.approval\)"):
        load_config(demo(agents=[files_agent(approval="always", folders=folders)]))


def test_the_schema_shows_rights_as_a_list_and_approval() -> None:
    """``loom schema`` : une liste pour rights, et approval, que l'éditeur ne refuse pas."""
    schema = agent_json_schema()
    spec = schema["$defs"]["FolderSpec"]
    assert spec["additionalProperties"] is False
    assert spec["properties"]["rights"]["type"] == "array"
    assert "anyOf" not in spec["properties"]["rights"]
    assert spec["properties"]["approval"]["additionalProperties"] == {"enum": ["never", "always"]}
    assert spec["properties"]["approval"]["propertyNames"] == {
        "enum": ["read", "write", "overwrite", "delete"]
    }
    assert "approval" not in spec["required"]
