# SPDX-License-Identifier: Apache-2.0
"""Outil fourni ``loom.memory`` : paramètres, schéma, approbation par appel, exécution.

L'outil est construit par sa fabrique, avec les modèles factices, sur une base
dans un dossier temporaire ; ses appels passent par ``invoke`` et
``assess_call``, les deux chemins du moteur. Le montage par la config et
``loom validate`` sont éprouvés à la fin.
"""

import importlib.util
import io
import os
import zipfile
from collections.abc import Callable
from importlib.machinery import ModuleSpec
from pathlib import Path
from typing import Any, Final, cast

import pytest
from conftest import ConfigFactory, demo_agent
from pydantic import JsonValue, ValidationError

import loom_ia.tools_builtin.memory.files as files_module
from loom_ia.access.cli import main
from loom_ia.adapters.stores import InMemoryEventStore
from loom_ia.config import ConfigError, load_config
from loom_ia.core.model import DEFAULT_TENANT, RunId, SessionId
from loom_ia.core.ports import ToolContext, ToolError
from loom_ia.runtime.wiring import build_agent
from loom_ia.tools_builtin import BuiltinContext, Protected, builtin_tool
from loom_ia.tools_builtin.memory import MemoryTool, build
from loom_ia.tools_builtin.memory.fake import FakeEmbedder
from loom_ia.tools_builtin.memory.files import MAX_TEXT_CHARS, word_markdown
from loom_ia.tools_builtin.memory_params import MemoryParams

FAKE: Final = {"kind": "fake"}
ALL_ACTIONS: Final[dict[str, dict[str, object]]] = {
    "search": {},
    "get": {},
    "list_docs": {},
    "add_text": {},
    "add_file": {},
    "update": {},
    "delete": {},
}
CONTEXT: Final = ToolContext(
    tenant_id=DEFAULT_TENANT, session_id=SessionId("s"), run_id=RunId("r"), call_id="c", agent="a"
)
NOTE: Final = "Le devis D-2026-042 de Mme Martin porte sur un chauffe-eau de 200 L."


def params(**given: Any) -> MemoryParams:
    values: dict[str, Any] = {"store": {"path": "memoire.db"}, "models": FAKE, **given}
    values.setdefault("actions", ALL_ACTIONS)
    return MemoryParams.model_validate(values)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    folder = tmp_path / "workspace"
    folder.mkdir()
    (folder / "cgv.md").write_text("# Conditions générales\n\nAcompte de 30 %.", encoding="utf-8")
    return folder


def mounted(tmp_path: Path, **given: Any) -> MemoryTool:
    if "add_file" in given.get("actions", ALL_ACTIONS) and "folders" not in given:
        given["folders"] = [{"name": "workspace", "path": "workspace"}]
    return build(params(**given), BuiltinContext(base_dir=tmp_path))


@pytest.fixture
def memory(tmp_path: Path, workspace: Path) -> MemoryTool:
    return mounted(tmp_path, project="documentation")


async def call(tool: MemoryTool, **arguments: JsonValue) -> Any:
    return (await tool.invoke(dict(arguments), CONTEXT)).data


async def refused(tool: MemoryTool, match: str, **arguments: JsonValue) -> None:
    with pytest.raises(ToolError, match=match):
        await tool.invoke(dict(arguments), CONTEXT)


# --- Paramètres --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("given", "match"),
    [
        ({"actions": {}}, "au moins une action"),
        ({"actions": {"add_file": {}}}, "add_file attend au moins un dossier"),
        (
            {"actions": ["search"], "folders": [{"name": "w", "path": "w"}]},
            "'folders' ne sert qu'à add_file",
        ),
        ({"actions": ["projects"], "project": "p"}, "sans objet quand 'project' est fixé"),
        ({"store": {}}, "donner 'path' ou 'path_env'"),
        ({"store": {"path": "a.db", "path_env": "B"}}, "donner 'path' ou 'path_env'"),
        ({"store": {"path": "~/m.db"}}, "ni « ~ » ni variable"),
        ({"project": "Majuscules"}, "should match pattern"),
        ({"actions": {"search": {"approval": "parfois"}}}, "never"),
        ({"actions": {"oublier": {}}}, "Input should be"),
        ({"chunking": {"target_chars": 500, "max_chars": 400}}, "target_chars"),
    ],
)
def test_parameters_are_checked_at_load(given: dict[str, Any], match: str) -> None:
    with pytest.raises(ValidationError, match=match):
        params(**given)


def test_a_list_of_actions_takes_the_defaults() -> None:
    found = params(actions=["search", "add_text", "delete"])
    assert found.declared() == ("search", "add_text", "delete")
    assert [found.approval(a) for a in found.declared()] == ["never", "always", "always"]
    assert params(actions={"add_text": {"approval": "never"}}).approval("add_text") == "never"


# --- Spec et schéma ----------------------------------------------------------------------


def test_the_schema_keeps_only_the_permitted_actions_and_their_arguments(
    memory: MemoryTool,
) -> None:
    schema = cast(dict[str, Any], memory.spec.input_schema)
    properties = schema["properties"]
    assert properties["action"]["enum"] == [
        "search",
        "get",
        "list_docs",
        "add_text",
        "add_file",
        "update",
        "delete",
    ]
    # Projet imposé : pas d'argument ; add_url absent : pas d'url.
    assert set(properties) == {
        "action",
        "query",
        "k",
        "tags",
        "doc_id",
        "text",
        "title",
        "file",
        "n",
    }
    assert schema["required"] == ["action"]
    assert "$defs" not in schema


def test_without_an_imposed_project_the_model_gives_it(tmp_path: Path) -> None:
    tool = mounted(tmp_path, actions=["search", "add_text", "projects"])
    properties = cast(dict[str, Any], tool.spec.input_schema)["properties"]
    assert set(properties) == {"action", "query", "k", "tags", "project", "text", "title"}


def test_the_spec_declares_the_worst_of_the_permitted_actions(
    tmp_path: Path, workspace: Path
) -> None:
    assert (mounted(tmp_path).spec.side_effects, mounted(tmp_path).spec.approval) == (
        "irreversible",
        "always",
    )
    reads = mounted(tmp_path, actions=["search", "get"])
    assert (reads.spec.side_effects, reads.spec.approval) == ("none", "never")
    trusted = mounted(tmp_path, actions={"add_text": {"approval": "never"}})
    assert (trusted.spec.side_effects, trusted.spec.approval) == ("reversible", "never")


def test_the_description_says_the_project_and_what_waits(memory: MemoryTool) -> None:
    description = memory.spec.description
    assert "Projet imposé : documentation" in description
    assert "- search : chercher dans la mémoire\n" in description
    assert "- add_text : mémoriser un texte (attend une approbation humaine)" in description
    assert "- workspace : read" in description
    assert str(Path("workspace").resolve()) not in description  # jamais un chemin réel


# --- Approbation appel par appel ----------------------------------------------------------


async def test_reads_pass_and_writes_wait(memory: MemoryTool) -> None:
    search = memory.assess_call({"action": "search", "query": "devis"})
    assert (search.approval, search.side_effects) == ("never", "none")
    add = memory.assess_call({"action": "add_text", "text": NOTE, "title": "Devis Martin"})
    assert (add.approval, add.side_effects, add.idempotent) == ("always", "reversible", True)
    assert add.reason == (
        "mémoriser un texte « Devis Martin » dans le projet documentation — la config exige "
        "une approbation pour add_text"
    )
    added = await call(memory, action="add_text", text=NOTE, title="Devis Martin")
    delete = memory.assess_call({"action": "delete", "doc_id": added["doc_id"]})
    assert (delete.approval, delete.side_effects, delete.idempotent) == (
        "always",
        "irreversible",
        False,
    )


def test_an_approval_set_to_never_lets_the_write_pass(tmp_path: Path) -> None:
    tool = mounted(tmp_path, actions={"add_text": {"approval": "never"}}, project="p")
    found = tool.assess_call({"action": "add_text", "text": NOTE, "title": "Devis"})
    assert (found.approval, found.side_effects) == ("never", "reversible")


def test_a_call_that_will_be_refused_has_no_effect(memory: MemoryTool) -> None:
    for arguments in (
        {"action": "add_url", "url": "https://x.fr"},  # non permise
        {"action": "add_text", "title": "Sans texte"},
        {"action": "delete", "doc_id": "x", "project": "autre"},
    ):
        found = memory.assess_call(cast(dict[str, JsonValue], arguments))
        assert (found.approval, found.side_effects) == ("never", "none")


async def test_a_file_add_file_cannot_read_is_not_put_to_a_human(
    memory: MemoryTool, workspace: Path
) -> None:
    """Dossier, droit et extension sont vérifiés avant l'approbation ; le contenu, à l'exécution."""
    (workspace / "plaquette.odt").write_bytes(b"PK\x03\x04")
    allowed = memory.assess_call({"action": "add_file", "file": "workspace/cgv.md"})
    assert (allowed.approval, allowed.side_effects) == ("always", "reversible")
    assert allowed.reason is not None and "workspace/cgv.md" in allowed.reason
    for file in ("workspace/plaquette.odt", "inconnu/cgv.md", "workspace/../hors.md"):
        found = memory.assess_call({"action": "add_file", "file": file})
        assert (found.approval, found.side_effects) == ("never", "none"), file
    # Refusé tout de suite à l'exécution, pour la même raison.
    await refused(
        memory, r"extension \.odt refusée", action="add_file", file="workspace/plaquette.odt"
    )
    # Un fichier absent passe l'évaluation : il peut être déposé pendant l'attente.
    missing = memory.assess_call({"action": "add_file", "file": "workspace/a-venir.md"})
    assert missing.approval == "always"


def assessed(tool: MemoryTool, **arguments: JsonValue) -> tuple[str, str]:
    found = tool.assess_call(arguments)
    return found.approval, found.side_effects


async def test_an_update_or_delete_that_writes_nothing_is_not_put_to_a_human(
    tmp_path: Path, memory: MemoryTool
) -> None:
    """Document inconnu, d'un autre projet, texte inchangé ou déjà pris : aucune approbation."""
    base = tmp_path / "memoire.db"
    # Base absente : aucun document, et le coup d'œil ne la crée pas.
    assert assessed(memory, action="delete", doc_id="inconnu") == ("never", "none")
    assert not base.exists()
    note = await call(memory, action="add_text", text=NOTE, title="Devis Martin")
    other = await call(memory, action="add_text", text="Facture F-2026-007.", title="Facture")
    doc_id = note["doc_id"]
    waiting = ("always", "irreversible")
    assert assessed(memory, action="delete", doc_id=doc_id) == waiting
    assert assessed(memory, action="delete", doc_id=f"  {doc_id} ") == waiting
    assert assessed(memory, action="update", doc_id=doc_id, text="Nouveau texte.") == (
        "always",
        "reversible",
    )
    nothing = ("never", "none")
    assert assessed(memory, action="delete", doc_id="inconnu") == nothing
    assert assessed(memory, action="update", doc_id="inconnu", text="x") == nothing
    # Même texte, à la casse et aux espaces près : rien ne serait écrit.
    assert assessed(memory, action="update", doc_id=doc_id, text=f"  {NOTE.upper()}  ") == nothing
    # Le texte d'un autre document : refusé à l'exécution.
    assert assessed(memory, action="update", doc_id=doc_id, text="Facture F-2026-007.") == nothing
    await refused(
        memory, "déjà celui du document", action="update", doc_id=doc_id, text="Facture F-2026-007."
    )
    # Un autre projet imposé ne voit pas ce document.
    elsewhere = mounted(tmp_path, project="ailleurs")
    assert assessed(elsewhere, action="delete", doc_id=other["doc_id"]) == nothing


async def test_adding_a_text_the_project_already_has_is_not_put_to_a_human(
    tmp_path: Path, memory: MemoryTool
) -> None:
    """Le même texte (casse et espaces mis à part) : ``duplicate_of``, sans approbation."""
    new = ("always", "reversible")
    assert assessed(memory, action="add_text", text=NOTE, title="Devis Martin") == new
    first = await call(memory, action="add_text", text=NOTE, title="Devis Martin")
    nothing = ("never", "none")
    again = {"action": "add_text", "text": f" {NOTE.upper()} ", "title": "Autre titre"}
    assert assessed(memory, **again) == nothing
    assert (await call(memory, **again))["duplicate_of"] == first["doc_id"]
    assert assessed(memory, action="add_text", text="Un texte neuf.", title="Neuf") == new
    # Refusés à l'exécution : titre trop court, texte vide.
    assert assessed(memory, action="add_text", text=NOTE, title="D") == nothing
    assert assessed(memory, action="add_text", text="   ", title="Vide") == nothing
    # Un autre projet n'a pas ce texte : l'écriture y créerait un document.
    free = mounted(tmp_path, actions=["search", "add_text", "projects"])
    assert assessed(free, action="add_text", text=NOTE, title="Devis", project="chantiers") == new
    # Le projet donné par le modèle est mis en forme comme à l'écriture.
    assert assessed(
        free, action="add_text", text=NOTE, title="Devis", project=" Documentation "
    ) == (nothing)


@pytest.mark.parametrize("content", [b"pas une base SQLite", b""])
def test_an_unreadable_base_asks_as_usual(tmp_path: Path, content: bytes) -> None:
    """Au moindre doute (fichier abîmé, schéma absent), l'approbation est demandée."""
    (tmp_path / "memoire.db").write_bytes(content)
    tool = mounted(tmp_path, actions=["add_text", "delete"], project="p")
    assert assessed(tool, action="delete", doc_id="x") == ("always", "irreversible")
    assert assessed(tool, action="add_text", text=NOTE, title="Devis") == ("always", "reversible")
    assert (tmp_path / "memoire.db").read_bytes() == content


# --- Exécution ---------------------------------------------------------------------------


async def test_a_note_is_added_then_found(memory: MemoryTool) -> None:
    added = await call(memory, action="add_text", text=NOTE, title="Devis Martin")
    assert added["project"] == "documentation" and added["chunks"] == 1
    [hit] = await call(memory, action="search", query="chauffe-eau Martin")
    assert hit["doc_id"] == added["doc_id"] and hit["snippet"] == NOTE
    doc = await call(memory, action="get", doc_id=added["doc_id"])
    assert doc["text"] == NOTE and doc["source_kind"] == "text"
    [summary] = await call(memory, action="list_docs")
    assert summary["title"] == "Devis Martin"


async def test_update_and_delete(memory: MemoryTool) -> None:
    added = await call(memory, action="add_text", text=NOTE, title="Devis Martin")
    updated = await call(memory, action="update", doc_id=added["doc_id"], text="Nouveau texte.")
    assert updated["updated"] is True
    gone = await call(memory, action="delete", doc_id=added["doc_id"])
    assert gone["doc_id"] == added["doc_id"]
    await refused(memory, "Document inconnu", action="get", doc_id=added["doc_id"])


async def test_a_file_is_read_through_its_folder(memory: MemoryTool) -> None:
    added = await call(memory, action="add_file", file="workspace/cgv.md")
    assert added["title"] == "Conditions générales"
    doc = await call(memory, action="get", doc_id=added["doc_id"])
    # La source est l'adresse vue par le modèle, jamais le chemin réel.
    assert doc["source"] == "workspace/cgv.md" and doc["source_kind"] == "file"
    again = await call(memory, action="add_file", file="workspace/cgv.md")
    assert again["duplicate_of"] == added["doc_id"]


async def test_files_that_add_file_refuses(memory: MemoryTool, workspace: Path) -> None:
    (workspace / ".env").write_text("SECRET=1", encoding="utf-8")
    (workspace / "id_rsa").write_text("clé", encoding="utf-8")
    (workspace / "cle.pem").write_text("clé", encoding="utf-8")
    (workspace / "gros.txt").write_bytes(b"a" * (files_module.MAX_FILE_BYTES + 1))
    (workspace / "long.txt").write_text("mot " * (MAX_TEXT_CHARS // 4 + 1), encoding="utf-8")
    (workspace / "latin1.txt").write_bytes("é".encode("latin-1"))
    (workspace / "vide.md").write_text("  ", encoding="utf-8")
    (workspace / "lien.md").symlink_to("/etc/hostname")
    for file, match in (
        ("workspace/.env", "cachés sont inaccessibles"),
        ("workspace/id_rsa", "extension absente refusée"),
        ("workspace/cle.pem", "extension .pem refusée"),
        ("workspace/gros.txt", "dépasse"),
        ("workspace/long.txt", f"plus de {MAX_TEXT_CHARS} caractères"),
        ("workspace/latin1.txt", "pas un fichier texte UTF-8"),
        ("workspace/vide.md", "est vide"),
        ("workspace/absent.md", "fichier introuvable"),
        ("workspace/lien.md", "hors des dossiers permis"),
        ("/etc/passwd", "Chemin absolu refusé"),
        ("ailleurs/x.md", "Dossier inconnu"),
    ):
        await refused(memory, match, action="add_file", file=file)


# --- add_file : ce que dit l'approbation ----------------------------------------------


def guide(sections: int) -> str:
    """Un markdown d'une section par partie, chacune assez longue pour faire un passage."""
    return "# Guide\n\n" + "\n\n".join(
        f"## Partie {i}\n\n" + "Du texte utile au guide. " * 12 for i in range(sections)
    )


def test_the_approval_says_the_size_and_the_passages_of_a_text_file(
    memory: MemoryTool, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (workspace / "guide.md").write_text(guide(5), encoding="utf-8")
    found = memory.assess_call({"action": "add_file", "file": "workspace/guide.md"})
    assert found.approval == "always" and found.reason is not None
    assert "(workspace/guide.md, 2 Ko, 5 passages)" in found.reason
    # Modèle pas encore chargé : on ne sait pas sur quel device, pas de durée.
    assert "calcul" not in found.reason
    monkeypatch.setattr(FakeEmbedder, "device", property(lambda _: "cpu"))
    found = memory.assess_call({"action": "add_file", "file": "workspace/guide.md"})
    assert found.reason is not None and "5 passages, ~10 s de calcul sur cpu)" in found.reason
    monkeypatch.setattr(FakeEmbedder, "device", property(lambda _: "cuda:0"))
    found = memory.assess_call({"action": "add_file", "file": "workspace/guide.md"})
    assert found.reason is not None and "moins d'une seconde de calcul sur cuda:0" in found.reason
    monkeypatch.setattr(FakeEmbedder, "device", property(lambda _: "mps"))
    found = memory.assess_call({"action": "add_file", "file": "workspace/guide.md"})
    assert found.reason is not None and "calcul" not in found.reason


def test_a_long_computation_is_said_in_minutes(
    memory: MemoryTool, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (workspace / "guide.md").write_text(guide(63), encoding="utf-8")
    monkeypatch.setattr(FakeEmbedder, "device", property(lambda _: "cpu"))
    found = memory.assess_call({"action": "add_file", "file": "workspace/guide.md"})
    assert found.reason is not None and "63 passages, ~2 min de calcul sur cpu)" in found.reason


def test_the_approval_says_what_will_be_refused_but_still_asks(
    memory: MemoryTool, workspace: Path
) -> None:
    """Le fichier peut changer pendant l'attente : ce qu'on en voit informe, sans décider."""
    (workspace / "long.txt").write_text("mot " * (MAX_TEXT_CHARS // 4 + 1), encoding="utf-8")
    (workspace / "latin1.txt").write_bytes("é".encode("latin-1"))
    for file, said in (
        ("workspace/long.txt", "— trop long : sera refusé"),
        ("workspace/a-venir.md", "— introuvable pour l'instant"),
        ("workspace/latin1.txt", "(workspace/latin1.txt)"),
    ):
        found = memory.assess_call({"action": "add_file", "file": file})
        assert (found.approval, found.side_effects) == ("always", "reversible"), file
        assert found.reason is not None and said in found.reason, file


def test_the_approval_gives_only_the_size_of_a_word_or_pdf_file(
    memory: MemoryTool, workspace: Path
) -> None:
    """Un .docx ou un .pdf n'est pas converti pour évaluer l'appel : sa taille seule."""
    (workspace / "plaquette.docx").write_bytes(b"PK\x03\x04" + b"x" * 64_000)
    found = memory.assess_call({"action": "add_file", "file": "workspace/plaquette.docx"})
    assert found.reason is not None and "(workspace/plaquette.docx, 64 Ko)" in found.reason


# --- add_file : Word et PDF -----------------------------------------------------------

WORD_NS: Final = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
WORD_STYLES: Final = (
    '<w:style w:type="paragraph" w:styleId="Titre"><w:name w:val="Title"/></w:style>'
    '<w:style w:type="paragraph" w:styleId="Titre1"><w:name w:val="heading 1"/></w:style>'
)


def paragraph(text: str, style: str | None = None) -> str:
    props = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f"<w:p>{props}<w:r><w:t>{text}</w:t></w:r></w:p>"


def docx(*body: str) -> bytes:
    """Un .docx minimal : le corps, et les styles Titre et Titre 1."""
    rels = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    parts = {
        "[Content_Types].xml": "<Types/>",
        "_rels/.rels": (
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="r1" Type="{rels}/officeDocument" Target="word/document.xml"/>'
            "</Relationships>"
        ),
        "word/document.xml": f"<w:document {WORD_NS}><w:body>{''.join(body)}</w:body></w:document>",
        "word/_rels/document.xml.rels": (
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rS" Type="{rels}/styles" Target="styles.xml"/></Relationships>'
        ),
        "word/styles.xml": f"<w:styles {WORD_NS}>{WORD_STYLES}</w:styles>",
    }
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, text in parts.items():
            archive.writestr(name, text)
    return raw.getvalue()


def test_word_blocks_become_markdown() -> None:
    title, text = word_markdown(
        [
            {"type": "titre", "level": 0, "text": "Plaquette commerciale"},
            {"type": "titre", "level": 1, "text": "**Offre**"},
            {"type": "paragraphe", "text": "Un **agent** par artisan."},
            {
                "type": "liste",
                "ordered": True,
                "items": ["Cadrage", {"text": "Réalisation", "items": ["code", "tests"]}],
            },
            {"type": "tableau", "rows": [["Poste", "Prix"], ["Audit | conseil", "500\n€"]]},
            {"type": "citation", "text": "Travail\nsoigné."},
            {"type": "graphique", "title": "Ventes"},
            {"type": "titre", "level": 2, "text": "Détail"},
            {"type": "note", "text": "**Validité**\n3 mois."},
        ]
    )
    assert title == "Plaquette commerciale"
    assert text == (
        "# Plaquette commerciale\n\n## Offre\n\nUn **agent** par artisan.\n\n"
        "1. Cadrage\n2. Réalisation\n   - code\n   - tests\n\n"
        "| Poste | Prix |\n|---|---|\n| Audit \\| conseil | 500 € |\n\n"
        "> Travail\n> soigné.\n\n### Détail\n\n**Validité**\n3 mois."
    )


async def test_a_word_file_is_stored_with_its_sections(memory: MemoryTool, workspace: Path) -> None:
    (workspace / "plaquette.docx").write_bytes(
        docx(
            paragraph("Plaquette commerciale", "Titre"),
            paragraph("Offre", "Titre1"),
            paragraph("Un agent de relance par artisan."),
            paragraph("Tarifs", "Titre1"),
            paragraph("Sur devis, après un audit gratuit."),
        )
    )
    added = await call(memory, action="add_file", file="workspace/plaquette.docx")
    assert added["title"] == "Plaquette commerciale"
    doc = await call(memory, action="get", doc_id=added["doc_id"])
    assert doc["source"] == "workspace/plaquette.docx" and doc["source_kind"] == "file"
    assert doc["text"] == (
        "# Plaquette commerciale\n\n## Offre\n\nUn agent de relance par artisan.\n\n"
        "## Tarifs\n\nSur devis, après un audit gratuit."
    )
    [hit] = await call(memory, action="search", query="audit gratuit")
    assert hit["doc_id"] == added["doc_id"]
    # Le même fichier : rien de doublé ; modifié : le même document, remplacé.
    again = await call(memory, action="add_file", file="workspace/plaquette.docx")
    assert again["duplicate_of"] == added["doc_id"]
    (workspace / "plaquette.docx").write_bytes(
        docx(paragraph("Plaquette commerciale", "Titre"), paragraph("Nouvelle offre."))
    )
    changed = await call(memory, action="add_file", file="workspace/plaquette.docx")
    assert changed["doc_id"] == added["doc_id"] and changed["updated"]


async def test_a_word_file_without_title_is_named_after_the_file(
    memory: MemoryTool, workspace: Path
) -> None:
    (workspace / "cgv-2026.docx").write_bytes(docx(paragraph("Acompte de 30 % à la commande.")))
    added = await call(memory, action="add_file", file="workspace/cgv-2026.docx")
    assert added["title"] == "cgv-2026"


async def test_a_word_file_beyond_the_text_limit_is_refused(
    memory: MemoryTool, workspace: Path
) -> None:
    # Un paragraphe seul plus long que la borne : coupé par le lecteur, donc refusé.
    (workspace / "un-bloc.docx").write_bytes(docx(paragraph("mot " * (MAX_TEXT_CHARS // 4))))
    many = [paragraph(f"Paragraphe {i} : " + "mot " * 100) for i in range(800)]
    (workspace / "beaucoup.docx").write_bytes(docx(*many))
    for file in ("workspace/un-bloc.docx", "workspace/beaucoup.docx"):
        await refused(memory, f"plus de {MAX_TEXT_CHARS} caractères", action="add_file", file=file)


def pdf_of(*pages: str) -> bytes:
    """Un PDF d'essai : une page par texte ; un texte vide, une page sans texte."""
    canvas_module = pytest.importorskip("reportlab.pdfgen.canvas", reason="extra 'pdf' absent")
    pytest.importorskip("pypdf", reason="extra 'pdf' absent")
    raw = io.BytesIO()
    canvas: Any = canvas_module.Canvas(raw, invariant=1)
    for text in pages:
        if text:
            for number, line in enumerate(text.splitlines()):
                canvas.drawString(72, 760 - 14 * number, line)
        else:
            canvas.rect(72, 600, 200, 100, fill=1)
        canvas.showPage()
    canvas.save()
    return raw.getvalue()


async def test_a_pdf_gives_its_text_page_by_page(memory: MemoryTool, workspace: Path) -> None:
    (workspace / "devis-martin.pdf").write_bytes(
        pdf_of("Devis D-2026-042\nChauffe-eau 200 L", "Conditions\nAcompte de 30 %")
    )
    added = await call(memory, action="add_file", file="workspace/devis-martin.pdf")
    assert added["title"] == "devis-martin"
    doc = await call(memory, action="get", doc_id=added["doc_id"])
    assert "Chauffe-eau 200 L" in doc["text"] and "Acompte de 30 %" in doc["text"]
    assert doc["text"].index("Chauffe-eau") < doc["text"].index("Acompte")


async def test_a_pdf_without_text_is_refused(memory: MemoryTool, workspace: Path) -> None:
    (workspace / "scan.pdf").write_bytes(pdf_of("", ""))
    await refused(memory, "aucun texte dans le PDF", action="add_file", file="workspace/scan.pdf")


async def test_without_the_pdf_extra_a_pdf_is_refused_before_approval(
    tmp_path: Path, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    said = "la lecture des .pdf demande l'extra loom-ia[pdf] (pypdf absent)"
    monkeypatch.setattr(files_module, "_pdf_problem", lambda: said)
    tool = mounted(tmp_path, project="documentation")
    (workspace / "devis.pdf").write_bytes(b"%PDF-1.4")
    assert assessed(tool, action="add_file", file="workspace/devis.pdf") == ("never", "none")
    await refused(tool, r"extra loom-ia\[pdf\]", action="add_file", file="workspace/devis.pdf")
    # Le modèle n'est pas invité à donner un PDF.
    assert ".pdf" not in tool.spec.description and ".docx" in tool.spec.description


def test_why_pdf_files_cannot_be_read(monkeypatch: pytest.MonkeyPatch) -> None:
    # Les modules sont dits présents ou absents ici : le test ne dépend pas des extras
    # installés (le job « noyau seul » de la CI n'a ni pypdf ni reportlab).
    def without(missing: str) -> Callable[[str], ModuleSpec | None]:
        return lambda name: None if name == missing else ModuleSpec(name, None)

    monkeypatch.setattr(files_module.importlib.util, "find_spec", without("pypdf"))
    assert files_module.unreadable() == {
        ".pdf": "la lecture des .pdf demande l'extra loom-ia[pdf] (pypdf absent)"
    }
    monkeypatch.setattr(files_module.importlib.util, "find_spec", without("autre"))

    def old_pypdf(_: str) -> str:
        return "6.1.0"

    monkeypatch.setattr(files_module, "version", old_pypdf)
    [problem] = files_module.unreadable().values()
    assert problem.startswith("pypdf 6.1.0 : 6.14.2 ou plus attendu")


async def test_an_imposed_project_is_not_an_argument(memory: MemoryTool) -> None:
    await refused(
        memory,
        r"'project' n'est pas un argument : le projet est imposé \(documentation\)",
        action="search",
        query="x",
        project="autre",
    )


async def test_documents_of_another_project_stay_invisible(tmp_path: Path) -> None:
    other = mounted(tmp_path, actions=["add_text", "search"], project="desktop")
    mine = mounted(tmp_path, actions=["get", "update", "delete", "search"], project="documentation")
    added = await call(other, action="add_text", text=NOTE, title="Devis Martin")
    assert await call(mine, action="search", query="chauffe-eau") == []
    for arguments in (
        {"action": "get", "doc_id": added["doc_id"]},
        {"action": "update", "doc_id": added["doc_id"], "text": "Effacé."},
        {"action": "delete", "doc_id": added["doc_id"]},
    ):
        await refused(mine, "Document inconnu dans le projet 'documentation'", **arguments)
    assert len(await call(other, action="search", query="chauffe-eau")) == 1


async def test_arguments_that_do_not_fit_the_action(memory: MemoryTool) -> None:
    await refused(memory, "non permise", action="add_url", url="https://x.fr")
    await refused(memory, "add_text attend : text, title", action="add_text")
    await refused(memory, "url ne sert pas à search", action="search", query="x", url="u")
    await refused(memory, "Arguments invalides", action="search", query="x", k=50)


async def test_the_model_gives_the_project_when_none_is_imposed(tmp_path: Path) -> None:
    tool = mounted(tmp_path, actions=["add_text", "search", "projects"])
    await refused(tool, "add_text attend : project", action="add_text", text=NOTE, title="Devis")
    await call(tool, action="add_text", text=NOTE, title="Devis", project="Dupont")
    assert await call(tool, action="projects") == {"dupont": 1}
    assert len(await call(tool, action="search", query="chauffe-eau", project="dupont")) == 1
    assert await call(tool, action="search", query="chauffe-eau", project="martin") == []


# --- Fabrique ------------------------------------------------------------------------------


def test_the_base_is_created_on_first_use_never_its_folder(tmp_path: Path) -> None:
    tool = mounted(tmp_path, actions=["search"])
    assert tool.store.path == (tmp_path / "memoire.db").resolve()
    assert not tool.store.path.exists()  # monter ne crée rien
    with pytest.raises(ValueError, match="crée la base, pas son dossier"):
        mounted(tmp_path, actions=["search"], store={"path": "absent/memoire.db"})
    (tmp_path / "dossier.db").mkdir()
    with pytest.raises(ValueError, match="n'est pas un fichier"):
        mounted(tmp_path, actions=["search"], store={"path": "dossier.db"})


def test_a_base_path_from_the_client_secrets(tmp_path: Path) -> None:
    given = params(actions=["search"], store={"path_env": "MEMOIRE_BASE"})
    assert given.secret_names() == ("MEMOIRE_BASE",)
    secrets = {"MEMOIRE_BASE": str(tmp_path / "dupont.db")}
    tool = build(given, BuiltinContext(base_dir=tmp_path, secrets=secrets))
    assert tool.store.path == tmp_path / "dupont.db"
    with pytest.raises(ValueError, match="MEMOIRE_BASE \\(path_env\\) est absente ou vide"):
        build(given, BuiltinContext(base_dir=tmp_path, secrets={}))
    with pytest.raises(ValueError, match="un chemin absolu est attendu"):
        build(given, BuiltinContext(base_dir=tmp_path, secrets={"MEMOIRE_BASE": "rel.db"}))


def test_a_file_of_loom_is_never_the_base(tmp_path: Path) -> None:
    journal = tmp_path / "journal"
    journal.mkdir()
    context = BuiltinContext(
        base_dir=tmp_path, protected=(Protected(journal, "le journal", subtree=True),)
    )
    with pytest.raises(ValueError, match="est dans le journal"):
        build(params(actions=["search"], store={"path": "journal/m.db"}), context)


def without(missing: str) -> Callable[..., ModuleSpec | None]:
    """``find_spec`` qui ne trouve pas ``missing`` : son extra n'est pas installé."""
    real = importlib.util.find_spec

    def find(name: str, package: str | None = None) -> ModuleSpec | None:
        return None if name == missing else real(name, package)

    return find


def test_the_real_models_need_the_memory_extra(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(importlib.util, "find_spec", without("torch"))
    given = params(actions=["search"], models={"device": "cpu"})
    with pytest.raises(ImportError) as caught:
        build(given, BuiltinContext(base_dir=tmp_path))
    assert caught.value.name == "torch"


def test_add_url_needs_the_memory_web_extra(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(importlib.util, "find_spec", without("bs4"))
    with pytest.raises(
        ValueError, match=r"'bs4' absente \(installer l'extra : loom-ia\[memory-web\]\)"
    ):
        build(params(actions=["add_url"], project="p"), BuiltinContext(base_dir=tmp_path))


# --- Par la config -------------------------------------------------------------------------

JSONL: Final = {"events": {"backend": "jsonl", "path": "journal"}}


def memory_agent(**reference: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "store": {"path": "memoire.db"},
        "models": FAKE,
        "actions": ["search", "add_text"],
        "project": "documentation",
        **reference,
    }
    return demo_agent(tools=[{"python": "loom.memory", **values}])


def test_mounted_from_the_config(demo: ConfigFactory) -> None:
    path = demo(agents=[memory_agent(skill="Base documentaire de l'entreprise.")], storage=JSONL)
    agent = build_agent(load_config(path), "demo", InMemoryEventStore())
    [spec] = [tool for tool in agent.context.tools.specs if tool.name == "memory"]
    assert "Projet imposé : documentation" in spec.description
    assert spec.skill == "Base documentaire de l'entreprise."


def test_the_tool_has_its_own_timeout_and_the_agent_may_change_it(demo: ConfigFactory) -> None:
    """Indexer une page sur CPU dépasse les 30 s communs : l'outil en déclare 300."""
    for given, expected in (({}, 300.0), ({"timeout": 900}, 900.0)):
        path = demo(agents=[memory_agent(**given)], storage=JSONL)
        agent = build_agent(load_config(path), "demo", InMemoryEventStore())
        [spec] = [tool for tool in agent.context.tools.specs if tool.name == "memory"]
        assert spec.timeout == expected


@pytest.mark.parametrize("setting", ["approval", "side_effects", "idempotent"])
def test_the_common_settings_that_actions_express_are_refused(
    demo: ConfigFactory, setting: str
) -> None:
    value = {"approval": "never", "side_effects": "none", "idempotent": True}[setting]
    path = demo(agents=[memory_agent(**{setting: value})], storage=JSONL)
    with pytest.raises(ConfigError, match="chaque action permise a son approbation"):
        load_config(path)


def test_a_write_under_approval_needs_a_durable_journal(demo: ConfigFactory) -> None:
    path = demo(agents=[memory_agent()])
    with pytest.raises(ConfigError, match="dont certains appels attendent"):
        build_agent(load_config(path), "demo", InMemoryEventStore())


def test_validate_shows_the_base_and_the_actions(
    demo: ConfigFactory, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = demo(agents=[memory_agent()], storage=JSONL)
    assert main(["--config", str(path), "validate"]) == 0
    out = capsys.readouterr().out
    assert "loom.memory" in out
    assert "memoire.db" in out
    assert "add_text*" in out
    assert not (tmp_path / "memoire.db").exists()  # valider ne crée pas la base


def test_the_tool_is_in_the_catalogue() -> None:
    tool = builtin_tool(
        "loom.memory",
        params(actions=["search"]),
        context=BuiltinContext(base_dir=Path(os.getcwd())),
    )
    assert isinstance(tool, MemoryTool)
