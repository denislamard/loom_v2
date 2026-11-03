# SPDX-License-Identifier: Apache-2.0
"""Pages web pour ``add_url`` : téléchargement par ``fetch_page``, conversion en markdown.

Le réseau passe par ``fetch_page.download`` : mêmes garde-fous (http et https,
ports 80 et 443, hôtes publics, redirections vérifiées, 5 Mo au plus). La
conversion garde la structure — titres, listes, tableaux, code — après retrait
du bruit évident (navigation, pied de page, scripts) : un titre perdu coûte plus
à la recherche qu'un menu résiduel, et les pages mémorisées sont choisies, pas
moissonnées.

markdownify et BeautifulSoup viennent de l'extra ``memory-web`` ; ce module ne
les importe qu'à la première page convertie. L'analyse passe par
``html.parser`` de la bibliothèque standard, jamais par lxml : installé, lxml
changerait la façon dont openpyxl lit les classeurs de ``loom.excel``.
"""

import asyncio
import importlib
import re
from typing import Any, Final

from loom_ia.core.ports import ToolError
from loom_ia.tools_builtin.fetch_page import decode, download, is_html
from loom_ia.tools_builtin.memory.service import Extracted

WEB_EXTRA: Final = "memory-web"
WEB_MODULES: Final = ("bs4", "markdownify")

_NOISE: Final = (
    "nav, footer, script, style, noscript, iframe, svg, form, template, "
    "[aria-hidden='true'], [role='navigation']"
)
_INLINE_TAGS: Final = ("span", "strong", "b", "em", "i", "code", "a", "small", "abbr", "time")


async def fetch_markdown(url: str) -> Extracted:
    """La page en markdown et son titre ; ``ToolError`` si elle est illisible ou vide."""
    page = await download(url)

    def convert() -> Extracted:
        text = decode(page.body, page.charset, page.media_type)
        if is_html(page.media_type):
            return extract_from_html(text, page.url)
        stripped = text.strip()
        if not stripped:
            raise ToolError(f"Aucun contenu dans {page.url}.")
        return Extracted(title=page.url, text=stripped)

    return await asyncio.to_thread(convert)


def extract_from_html(html: str, url: str) -> Extracted:
    """Le contenu de la page en markdown, et son titre (``og:title``, ``<title>``, ``<h1>``)."""
    bs4: Any = importlib.import_module("bs4")
    markdownify: Any = importlib.import_module("markdownify")
    soup: Any = bs4.BeautifulSoup(html, "html.parser")
    title = _page_title(soup) or url
    for node in [*soup.select(_NOISE), *_banners(soup)]:
        if not node.decomposed:
            node.decompose()
    _separate_glued_inlines(soup, bs4.NavigableString)
    root = soup.find("main") or soup.find("article") or soup.find("body") or soup
    converter = markdownify.MarkdownConverter(
        heading_style="ATX", strip=["img", "a"], bullets="-", code_language=""
    )
    text = _tidy(str(converter.convert_soup(root)))
    if not text:
        raise ToolError(f"Aucun contenu exploitable dans {url}.")
    return Extracted(title=title, text=text)


def _banners(soup: Any) -> list[Any]:
    """Un bandeau (``role=banner``) qui porte la navigation : du bruit, lui aussi."""
    return [node for node in soup.select("[role='banner']") if node.find("nav") is not None]


def _page_title(soup: Any) -> str | None:
    meta = soup.find("meta", attrs={"property": "og:title"})
    candidates = [
        meta.get("content") if meta is not None else None,
        soup.title.get_text() if soup.title is not None else None,
        soup.h1.get_text() if soup.h1 is not None else None,
    ]
    for value in candidates:
        joined = " ".join(str(value or "").split())
        if joined:
            return joined
    return None


def _separate_glued_inlines(soup: Any, text_type: type) -> None:
    """``<span>Label</span>Texte`` sans espace entre les deux : on en insère un.

    Sans lui, le markdown colle les mots (« Ce qui arriveL'agent »).
    """
    for element in soup.find_all(_INLINE_TAGS):
        text = element.get_text().rstrip()
        if not text:
            continue
        ends_word = text[-1].isalnum()
        ends_sentence = text[-1] in ".!?:;"
        following = element.next_sibling
        if isinstance(following, text_type):
            tail = str(following)
            if tail and (
                (ends_word and tail[0].isupper()) or (ends_sentence and tail[0].isalpha())
            ):
                following.replace_with(" " + tail)
            if tail:
                continue
            following = following.next_sibling
        if ends_word and following is not None and getattr(following, "name", None) in _INLINE_TAGS:
            head = following.get_text().lstrip()
            if head and head[0].isalnum():
                element.insert_after(" ")


def _tidy(text: str) -> str:
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
