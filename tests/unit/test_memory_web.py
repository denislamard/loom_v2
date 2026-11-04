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
from loom_ia.tools_builtin.memory.chunk import chunk_document
from loom_ia.tools_builtin.memory.web import extract_from_html, fetch_markdown
from loom_ia.tools_builtin.memory_params import ChunkingSpec, MemoryParams

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


def test_faq_questions_become_headings_under_the_previous_one() -> None:
    html = """<html><body><main><h2>Questions fréquentes</h2>
    <details><summary>Vous êtes seul.
      Que se passe-t-il si vous êtes indisponible&nbsp;?</summary>
      <div class="ans">Tout le code est sur vos comptes.</div></details>
    <details><summary><span>+</span> À qui appartient le code ?</summary>
      <p>À vous, intégralement.</p></details>
    </main></body></html>"""
    text = extract_from_html(html, "https://example.org/faq").text
    # Un niveau sous le h2 ; la seconde question n'est pas rangée sous la première.
    # Les blancs de la question, insécables compris, deviennent des espaces simples.
    assert "### Vous êtes seul. Que se passe-t-il si vous êtes indisponible ?" in text
    assert "### + À qui appartient le code ?\n\nÀ vous, intégralement." in text
    assert "<details>" not in text and "summary" not in text


def test_a_faq_without_heading_before_it_starts_at_level_two() -> None:
    html = (
        "<html><body><details><summary>Une question ?</summary>Sa réponse.</details></body></html>"
    )
    assert (
        extract_from_html(html, "https://example.org/").text == "## Une question ?\n\nSa réponse."
    )


def test_details_without_summary_keep_their_content() -> None:
    html = "<html><body><h2>T</h2><details><p>Contenu replié.</p></details></body></html>"
    assert extract_from_html(html, "https://example.org/").text == "## T\n\nContenu replié."


def test_each_faq_answer_is_chunked_with_its_question() -> None:
    answer = "Une réponse assez longue pour faire un passage à elle seule, avec des mots. " * 4
    items = "".join(
        f"<details><summary>Question numéro {i} ?</summary><div>{answer}</div></details>"
        for i in range(8)
    )
    html = f"<html><body><main><h2>FAQ</h2>{items}</main></body></html>"
    page = extract_from_html(html, "https://example.org/faq")
    chunks = chunk_document("d", page.title, page.text, ChunkingSpec())
    assert [c.heading_path for c in chunks] == [
        f"https://example.org/faq > FAQ > Question numéro {i} ?" for i in range(8)
    ]
    assert all(c.text.startswith("Une réponse") for c in chunks)


def test_definition_lists_keep_each_term_with_its_definition() -> None:
    html = """<html><body><h2>Étages</h2><dl class="layers">
    <div><dt>Orchestration</dt><dd>boucle d'agent · reprise sur incident</dd></div>
    <div><dt>Modèles</dt><dt>Fournisseurs</dt><dd>API <em>Anthropic</em> · OpenAI</dd></div>
    <dt>Données</dt><dd><p>Premier point.</p><ul><li>un</li><li>deux</li></ul></dd>
    <dt>Orphelin</dt></dl></body></html>"""
    text = extract_from_html(html, "https://example.org/").text
    assert "**Orchestration** : boucle d'agent · reprise sur incident" in text
    assert "**Modèles / Fournisseurs** : API *Anthropic* · OpenAI" in text
    assert "**Données** :\n\nPremier point.\n\n- un\n- deux" in text
    assert text.endswith("**Orphelin**")


def test_a_figure_with_a_drawing_keeps_its_description_and_caption_only() -> None:
    html = """<html><body><h2>Architecture</h2>
    <figure class="schema"><p class="hint">faites défiler le schéma &rarr;</p>
      <div class="scroll"><svg role="img"><title>Architecture du harness</title>
        <desc>Un orchestrateur au-dessus de trois abstractions.</desc>
        <text>LLMProvider</text></svg></div>
      <figcaption>Trois classes de base, <em>trois</em> natures d'outil.</figcaption></figure>
    <figure class="trace"><ol><li>queue.consume</li></ol>
      <figcaption>Un run réel.</figcaption></figure>
    </body></html>"""
    text = extract_from_html(html, "https://example.org/").text
    assert "défiler" not in text and "LLMProvider" not in text
    assert "Architecture du harness — Un orchestrateur au-dessus de trois abstractions." in text
    assert "Trois classes de base, *trois* natures d'outil." in text
    # Sans schéma, la figure reste telle quelle.
    assert "1. queue.consume" in text and "Un run réel." in text


def test_a_heading_inside_a_list_item_becomes_bold() -> None:
    html = """<html><body><h2>Déroulement</h2><ol>
    <li><h3>On cadre</h3><p>Une semaine.</p></li>
    <li><h3>On le met sous pression</h3><p>Avant vos utilisateurs.</p></li></ol></body></html>"""
    text = extract_from_html(html, "https://example.org/").text
    assert "2. **On le met sous pression**" in text
    assert "###" not in text


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
