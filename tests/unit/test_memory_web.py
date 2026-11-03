# SPDX-License-Identifier: Apache-2.0
"""Mémoire (``loom.memory``) : pages web pour ``add_url`` (extra ``memory-web``).

Le réseau est celui de ``fetch_page`` (éprouvé par ``test_fetch_page``) : ici,
``download`` est remplacé par une page donnée, et l'on éprouve la conversion
en markdown et ce que l'outil en range.
"""

from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("bs4", reason="extra 'memory-web' absent")
pytest.importorskip("markdownify", reason="extra 'memory-web' absent")

import loom_ia.tools_builtin.memory.web as web_module
from loom_ia.core.model import DEFAULT_TENANT, RunId, SessionId
from loom_ia.core.ports import ToolContext, ToolError
from loom_ia.tools_builtin import BuiltinContext
from loom_ia.tools_builtin.fetch_page import Downloaded
from loom_ia.tools_builtin.memory import build
from loom_ia.tools_builtin.memory.web import extract_from_html, fetch_markdown
from loom_ia.tools_builtin.memory_params import MemoryParams

HTML = """<html><head><title>Ma page</title></head><body>
<nav>Accueil Contact</nav>
<main><h1>Titre principal</h1>
<p>Premier paragraphe assez long pour être considéré comme du contenu principal.</p>
<p>Deuxième paragraphe, tout aussi long, qui parle de plomberie et de devis pour les artisans.</p>
</main><footer>© 2026</footer></body></html>"""
CONTEXT = ToolContext(
    tenant_id=DEFAULT_TENANT, session_id=SessionId("s"), run_id=RunId("r"), call_id="c", agent="a"
)


def serve(monkeypatch: pytest.MonkeyPatch, body: str, media_type: str = "text/html") -> list[str]:
    asked: list[str] = []

    async def download(url: str) -> Downloaded:
        asked.append(url)
        return Downloaded(f"{url}#final", body.encode(), "utf-8", media_type, False)

    monkeypatch.setattr(web_module, "download", download)
    return asked


def test_the_main_content_is_kept_without_the_chrome() -> None:
    page = extract_from_html(HTML, "https://example.org/page")
    assert page.title == "Ma page"
    assert page.text.startswith("# Titre principal")
    assert "plomberie" in page.text
    assert "Accueil Contact" not in page.text and "© 2026" not in page.text


def test_the_structure_is_kept_and_glued_labels_are_separated() -> None:
    html = """<html><head><meta property="og:title" content="Guide"><title>x</title></head><body>
    <header><nav>menu</nav></header>
    <section><p class="eyebrow">Engagements</p><h2>Ce que vous voulez savoir.</h2>
    <p><span class="lbl">Ce qui arrive</span>L'agent plante.</p>
    <p><span class="lbl">Ce que je fais</span><strong>Il reprend.</strong>État persisté.</p>
    <p>Des <code>tokens</code>s et du <em>texte</em>, normal.</p>
    <h3>Détail</h3><ul><li>un</li><li>deux</li></ul>
    <pre><code>x = 1</code></pre>
    <table><tr><th>a</th><th>b</th></tr><tr><td>1</td><td>2</td></tr></table>
    </section><footer>pied</footer></body></html>"""
    page = extract_from_html(html, "https://example.org/g")
    assert page.title == "Guide"
    assert "## Ce que vous voulez savoir." in page.text
    assert "### Détail" in page.text
    assert "Ce qui arrive L'agent plante." in page.text
    assert "Ce que je fais **Il reprend.** État persisté." in page.text
    assert "Des `tokens`s et du *texte*, normal." in page.text
    assert "- un\n- deux" in page.text
    assert "```\nx = 1\n```" in page.text
    assert "| a | b |" in page.text
    assert "menu" not in page.text and "pied" not in page.text


def test_an_empty_page_is_refused() -> None:
    with pytest.raises(ToolError, match="Aucun contenu exploitable"):
        extract_from_html("<html><body></body></html>", "https://example.org/vide")


async def test_a_text_page_is_kept_as_is(monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, "  Tarifs 2026 : déplacement 45 €.  ", media_type="text/plain")
    page = await fetch_markdown("https://example.org/tarifs.txt")
    assert page.title == "https://example.org/tarifs.txt#final"
    assert page.text == "Tarifs 2026 : déplacement 45 €."


async def test_add_url_stores_the_page_under_the_given_address(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    asked = serve(monkeypatch, HTML)
    params = MemoryParams.model_validate(
        {
            "store": {"path": "m.db"},
            "models": {"kind": "fake"},
            "project": "documentation",
            "actions": ["add_url", "get", "search"],
        }
    )
    tool = build(params, BuiltinContext(base_dir=tmp_path))
    added: Any = (
        await tool.invoke({"action": "add_url", "url": " https://example.org/page "}, CONTEXT)
    ).data
    assert asked == ["https://example.org/page"]
    assert added["title"] == "Ma page"
    doc: Any = (await tool.invoke({"action": "get", "doc_id": added["doc_id"]}, CONTEXT)).data
    assert doc["source"] == "https://example.org/page" and doc["source_kind"] == "url"
    again: Any = (
        await tool.invoke({"action": "add_url", "url": "https://example.org/page"}, CONTEXT)
    ).data
    assert again["duplicate_of"] == added["doc_id"]
