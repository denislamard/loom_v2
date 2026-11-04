# SPDX-License-Identifier: Apache-2.0
"""Mémoire (``loom.memory``) : empreinte, découpage par titres, extraits."""

import pytest

from loom_ia.tools_builtin.memory.chunk import announces, chunk_document, content_hash, snippet
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


def test_tiny_sections_are_merged_with_their_headings() -> None:
    chunks = chunk_document("d", "Guide", DOC, ChunkingSpec())
    assert len(chunks) == 1
    # Le chemin n'en garde qu'un : les titres des sections absorbées passent dans le texte.
    assert chunks[0].text == (
        "Intro courte.\n\n## Installation\n\nÉtape un. Étape deux.\n\n### Linux\n\n"
        "Détail linux.\n\n## Usage\n\nParagraphe d'usage."
    )
    # Le texte d'avant le premier titre a rejoint la section suivante, et son chemin.
    assert chunks[0].heading_path == "Guide > Installation"


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


# --- Annonces : une question reste avec sa réponse -----------------------------------

FAQ_SPEC = ChunkingSpec(target_chars=300, max_chars=420, overlap_chars=30, min_chars=0)


def _faq(question: str, answer_words: int = 30) -> str:
    filler = "Un paragraphe de contexte qui remplit le passage avec des mots. " * 3
    answer = "Réponse " + "détaillée " * answer_words + "fin."
    return f"{filler.strip()}\n\n{filler.strip()}\n\n{question}\n\n{answer}"


@pytest.mark.parametrize(
    "question",
    [
        "Que se passe-t-il si vous êtes indisponible\xa0?",
        "**Que se passe-t-il si vous êtes indisponible ?**",
        "Les règles à suivre :",
        "**Règles générales**",
    ],
)
def test_a_lead_paragraph_goes_with_what_it_announces(question: str) -> None:
    chunks = chunk_document("d", "T", _faq(question), FAQ_SPEC)
    holding = [c for c in chunks if question in c.text]
    assert len(holding) == 1
    assert "Réponse détaillée" in holding[0].text
    # La coupure est passée avant l'annonce : le passage précédent ne finit pas par elle.
    assert not any(c.text.endswith(question) for c in chunks)


def test_a_lone_lead_keeps_a_long_answer_up_to_max_chars() -> None:
    # Annonce + réponse dépassent target_chars : elles tiennent sous max_chars, ensemble.
    chunks = chunk_document("d", "T", _faq("Une question ?", answer_words=33), FAQ_SPEC)
    holding = next(c for c in chunks if "Une question ?" in c.text)
    assert "Réponse détaillée" in holding.text
    assert len(holding.text) > FAQ_SPEC.target_chars
    assert all(len(c.text) <= FAQ_SPEC.max_chars for c in chunks)


def test_labels_only_still_progress() -> None:
    # Que des annonces : chaque passage en garde au moins une, rien ne boucle.
    text = "\n\n".join(f"Étiquette numéro {i}" for i in range(60))
    chunks = chunk_document("d", "T", text, FAQ_SPEC)
    assert len(chunks) > 1
    assert all(f"Étiquette numéro {i}" in "\n".join(c.text for c in chunks) for i in range(60))


def test_a_lead_at_the_end_of_a_section_opens_the_next_one() -> None:
    text = (
        "# T\n\n## Offre\n\nNous faisons ceci, et le faisons bien.\n\nContact\n\n"
        "## Écrivez-moi\n\nUn courriel suffit pour commencer."
    )
    chunks = chunk_document("d", "T", text, ChunkingSpec(min_chars=0))
    assert [c.text for c in chunks] == [
        "Nous faisons ceci, et le faisons bien.",
        "Contact\n\nUn courriel suffit pour commencer.",
    ]


def test_a_section_made_of_a_lead_keeps_it() -> None:
    text = "# T\n\n## A\n\nRègles\n\n## B\n\nTexte de B."
    chunks = chunk_document("d", "T", text, ChunkingSpec(min_chars=0))
    assert [(c.heading_path, c.text) for c in chunks] == [
        ("T > A", "Règles"),
        ("T > B", "Texte de B."),
    ]


def test_a_lead_inside_a_code_block_stays_there() -> None:
    text = "# T\n\n## A\n\nAvant.\n\n```\ncode\n\nfin du bloc\n```\n\n## B\n\nTexte."
    chunks = chunk_document("d", "T", text, ChunkingSpec(min_chars=0))
    assert chunks[0].text.endswith("fin du bloc\n```")


@pytest.mark.parametrize(
    ("paragraph", "expected"),
    [
        ("Une question ?", True),
        ("« Une citation ? »", True),
        ("Les étapes :", True),
        ("**Règles générales**", True),
        ("Une phrase finie.", False),
        ("- un élément de liste", False),
        ("1. un élément numéroté", False),
        ("| a | b |", False),
        ("```", False),
        ("Une ligne\npuis une autre", False),
        ("Une question ? " + "mot " * 60, False),
    ],
)
def test_what_announces_the_next_paragraph(paragraph: str, expected: bool) -> None:
    assert announces(paragraph) is expected


# --- Morceaux trop courts ------------------------------------------------------------


def test_a_short_text_before_the_first_heading_joins_the_next_section() -> None:
    long_body = "Contenu de la section. " * 20
    text = f"Accroche courte.\n\n# T\n\n## Partie\n\n{long_body}"
    chunks = chunk_document("d", "T", text, ChunkingSpec())
    assert len(chunks) == 1
    assert chunks[0].heading_path == "T > Partie"
    assert chunks[0].text.startswith("Accroche courte.\n\n## Partie\n\nContenu")


def test_a_short_first_section_with_its_own_heading_does_not_move_forward() -> None:
    # Son titre n'est pas dans le chemin du suivant : le rejoindre le perdrait.
    long_body = "Contenu de la section. " * 20
    text = f"# T\n\n## Avant\n\nCourt.\n\n## Après\n\n{long_body}"
    chunks = chunk_document("d", "T", text, ChunkingSpec(max_chars=500, target_chars=480))
    assert [c.heading_path for c in chunks] == ["T > Avant", "T > Après"]


def test_a_document_that_is_all_short_stays_one_passage() -> None:
    chunks = chunk_document("d", "Note", "Une seule ligne.", ChunkingSpec())
    assert [(c.heading_path, c.text) for c in chunks] == [("Note", "Une seule ligne.")]


def test_a_short_piece_that_does_not_fit_stays_alone() -> None:
    long_body = "Contenu de la section. " * 20
    text = f"Accroche.\n\n# T\n\n## Partie\n\n{long_body}"
    spec = ChunkingSpec(target_chars=470, max_chars=470, overlap_chars=0)
    chunks = chunk_document("d", "T", text, spec)
    assert [c.heading_path for c in chunks] == ["T", "T > Partie"]
