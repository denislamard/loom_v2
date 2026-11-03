# SPDX-License-Identifier: Apache-2.0
"""Mémoire (``loom.memory``) : empreinte, découpage par titres, extraits."""

from loom_ia.tools_builtin.memory.chunk import chunk_document, content_hash, snippet
from loom_ia.tools_builtin.memory_params import ChunkingSpec

DOC = """# Guide

Intro courte.

## Installation

Étape un. Étape deux.

### Linux

Détail linux.

## Usage

Paragraphe d'usage.
"""


def test_sections_carry_their_heading_path() -> None:
    chunks = chunk_document("d", "Guide", DOC, ChunkingSpec(min_chars=0))
    assert [c.heading_path for c in chunks] == [
        "Guide",
        "Guide > Installation",
        "Guide > Installation > Linux",
        "Guide > Usage",
    ]
    assert [c.index for c in chunks] == [0, 1, 2, 3]
    assert chunks[2].embed_text.startswith("Guide > Installation > Linux\n\n")


def test_tiny_sections_are_merged() -> None:
    chunks = chunk_document("d", "Guide", DOC, ChunkingSpec())
    assert len(chunks) == 1
    assert "Détail linux." in chunks[0].text


def test_a_long_section_is_split_with_overlap() -> None:
    spec = ChunkingSpec(target_chars=300, max_chars=400, overlap_chars=40)
    paragraphs = [f"Paragraphe numéro {i} " + "mot " * 30 for i in range(12)]
    text = "# T\n\n## S\n\n" + "\n\n".join(paragraphs)
    chunks = chunk_document("d", "T", text, spec)
    assert len(chunks) > 1
    assert all(len(c.text) <= 400 + 60 for c in chunks)
    assert all(c.heading_path == "T > S" for c in chunks)
    # Le chevauchement : la fin d'un passage reparaît au début du suivant.
    assert chunks[0].text[-20:] in chunks[1].text


def test_headings_inside_code_fences_are_ignored() -> None:
    text = "# T\n\ntexte\n\n```bash\n# pas un titre\necho ok\n```\n\nsuite"
    chunks = chunk_document("d", "T", text, ChunkingSpec(min_chars=0))
    assert len(chunks) == 1
    assert "# pas un titre" in chunks[0].text


def test_an_oversized_paragraph_is_hard_split() -> None:
    spec = ChunkingSpec(target_chars=200, max_chars=200, overlap_chars=20)
    chunks = chunk_document("d", "T", "Phrase. " * 100, spec)
    assert all(len(c.text) <= 200 for c in chunks)
    assert sum(c.text.count("Phrase") for c in chunks) >= 100


def test_an_empty_text_gives_no_passage() -> None:
    assert chunk_document("d", "T", "", ChunkingSpec()) == []


def test_the_hash_ignores_case_and_whitespace() -> None:
    assert content_hash("Bonjour  le\nmonde") == content_hash("bonjour le monde ")
    assert content_hash("a") != content_hash("b")


def test_a_snippet_is_cut_at_a_space_and_marked() -> None:
    assert snippet("court", 10) == "court"
    assert snippet("un deux trois quatre cinq", 12) == "un deux…"
    # Sans espace utile : coupé en dur.
    assert snippet("a" * 30, 10) == "a" * 10 + "…"
