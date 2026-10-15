# SPDX-License-Identifier: Apache-2.0
"""Outil fetch_page : lit une page web publique et rend son texte au modèle."""

import asyncio
import codecs
import ipaddress
import re
import socket
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Final
from urllib.parse import urljoin, urlsplit

import httpx2

from loom_ia.core.ports import ToolError
from loom_ia.tools import tool

MAX_BYTES: Final = 5_000_000  # au-delà, on ne lit pas la suite de la page
MAX_REDIRECTS: Final = 5
MAX_LINKS: Final = 50
MAX_ADDRESSES: Final = 3  # adresses d'un même nom essayées tour à tour si la connexion échoue
ALLOWED_PORTS: Final = frozenset({80, 443})
HEADERS: Final = {
    "User-Agent": "Mozilla/5.0 (compatible; loom_ia-fetch_page/1.0)",
    "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.5",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.7",
}
TIMEOUT: Final = httpx2.Timeout(15.0, connect=5.0)


@tool(timeout=45, offload_over=20_000)
async def fetch_page(url: str) -> str:
    """Lit une page web publique (http ou https) et renvoie son adresse finale, son titre,
    son texte sans le HTML, puis la liste des liens qu'elle contient.

    À utiliser pour consulter une page dont on connaît l'adresse, ou suivre un lien
    trouvé dans une page déjà lue. Ne gère ni JavaScript, ni connexion à un compte,
    ni PDF, ni image.
    """
    async with httpx2.AsyncClient(
        headers=HEADERS, timeout=TIMEOUT, follow_redirects=False, transport=_PublicOnlyTransport()
    ) as client:
        current = url.strip()
        for _ in range(MAX_REDIRECTS + 1):
            check_url(current)
            try:
                async with client.stream("GET", current) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise ToolError(f"Redirection sans destination depuis {current}.")
                        current = urljoin(current, location)
                        continue
                    if response.status_code >= 400:
                        raise ToolError(
                            f"La page répond {response.status_code} "
                            f"{response.reason_phrase} : {current}"
                        )
                    media_type = response.headers.get("content-type", "").split(";")[0]
                    media_type = media_type.strip().lower() or "text/html"
                    if not _is_text(media_type):
                        raise ToolError(f"Contenu non pris en charge ({media_type}) : {current}")
                    body, truncated = await _read_capped(response)
                    charset = response.charset_encoding
            except httpx2.TimeoutException as exc:
                raise ToolError(f"Pas de réponse à temps de {current}.") from exc
            except (httpx2.HTTPError, httpx2.InvalidURL) as exc:
                raise ToolError(f"Page injoignable ({type(exc).__name__}) : {current}") from exc
            # Décoder et analyser une grosse page prend jusqu'à une seconde ou deux : hors de
            # la boucle asyncio, pour ne pas figer le moteur ni les outils lancés en parallèle.
            return await asyncio.to_thread(_render, body, charset, media_type, current, truncated)
    raise ToolError(f"Plus de {MAX_REDIRECTS} redirections depuis {url}.")


# --- Garde-fous réseau ---------------------------------------------------------

_DEFAULT_PORTS: Final = {"http": 80, "https": 443}
# IPv4 logées dans une IPv6 que ipaddress tient pour globales : préfixe NAT64 bien connu
# (64:ff9b::7f00:1 désigne 127.0.0.1 derrière une passerelle NAT64) et IPv4 « compatibles »
# (::a.b.c.d, obsolètes). Les IPv4 « mappées », 6to4 et Teredo, ipaddress les juge déjà.
_EMBEDDED_IPV4: Final = (ipaddress.IPv6Network("64:ff9b::/96"), ipaddress.IPv6Network("::/96"))


async def resolve(host: str, port: int) -> list[str]:
    """Adresses IP du nom ; séparé pour pouvoir le remplacer dans les tests."""
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


def check_url(url: str) -> None:
    """Refuse ce que l'adresse suffit à exclure : schéma, identifiants, port.

    Les adresses IP ne se jugent qu'à la connexion (``_PublicOnlyTransport``) : vérifier
    ici le nom, puis laisser httpx2 le résoudre de nouveau, ne protégerait de rien.
    """
    try:
        parts = urlsplit(url)
        port = parts.port if parts.port is not None else _DEFAULT_PORTS.get(parts.scheme)
    except ValueError as exc:  # port non numérique, crochets IPv6 mal formés…
        raise ToolError(f"Adresse invalide : {url}") from exc
    if parts.scheme not in ("http", "https"):
        raise ToolError(f"Seules les adresses http:// et https:// sont acceptées : {url}")
    if parts.username is not None or parts.password is not None:
        raise ToolError("Adresse refusée : elle contient un identifiant ou un mot de passe.")
    if not parts.hostname:
        raise ToolError(f"Adresse sans nom de domaine : {url}")
    if port not in ALLOWED_PORTS:
        raise ToolError(f"Port {port} refusé : seuls 80 et 443 sont acceptés.")


class _PublicOnlyTransport(httpx2.AsyncBaseTransport):
    """Transport qui ne se connecte qu'à des adresses publiques : celles qu'il vient de vérifier.

    Vérifier un nom puis laisser httpx2 le résoudre de nouveau laisse deux brèches. Un DNS
    complaisant (rebinding, TTL nul) répond une adresse publique à la vérification, puis
    127.0.0.1 à la connexion. Et le nom vérifié n'est pas toujours celui que httpx2 résout :
    pour « faß.de », getaddrinfo cherche fass.de (IDNA 2003), httpx2 xn--fa-hia.de (IDNA 2008).

    Ici, le nom tel que httpx2 l'a encodé est résolu une seule fois, et la connexion part
    vers une adresse vérifiée. Le nom reste dans l'en-tête Host et dans le SNI : le
    certificat TLS est contrôlé pour lui, pas pour l'adresse.
    """

    def __init__(self, inner: httpx2.AsyncBaseTransport | None = None) -> None:
        # ``inner`` ne sert qu'aux essais (``httpx2.MockTransport``) : sans lui, le réseau.
        # Aucune connexion gardée ouverte : le pool les range par adresse IP, et pourrait
        # resservir une connexion TLS ouverte pour un autre nom hébergé à la même adresse.
        if inner is None:
            inner = httpx2.AsyncHTTPTransport(limits=httpx2.Limits(max_keepalive_connections=0))
        self._inner = inner

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        url, extensions = request.url, request.extensions
        host = url.raw_host.decode("ascii")
        port = url.port if url.port is not None else _DEFAULT_PORTS.get(url.scheme)
        if port is None or port not in ALLOWED_PORTS:
            raise ToolError(f"Port {port} refusé : seuls 80 et 443 sont acceptés.")
        *others, last = (await _public_addresses(host, port))[:MAX_ADDRESSES]
        request.extensions = {**extensions, "sni_hostname": host}
        try:
            for address in others:
                try:
                    return await self._send(request, url, address)
                except httpx2.ConnectError, httpx2.ConnectTimeout:
                    continue  # adresse injoignable (IPv6 sans route…) : on essaie la suivante
            return await self._send(request, url, last)
        finally:
            # Le client range les cookies et journalise d'après request.url : on lui rend le nom.
            request.url, request.extensions = url, extensions

    async def _send(
        self, request: httpx2.Request, url: httpx2.URL, address: str
    ) -> httpx2.Response:
        request.url = url.copy_with(host=address)
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()


async def _public_addresses(host: str, port: int) -> list[str]:
    """Adresses du nom, toutes publiques ; sinon ``ToolError``.

    Une seule adresse interne suffit à refuser le nom : un domaine qui mêle adresses
    publiques et internes n'a rien d'une page web ordinaire.
    """
    try:
        async with asyncio.timeout(TIMEOUT.connect):
            found = await resolve(host, port)
    except TimeoutError as exc:
        raise ToolError(f"Pas de réponse à temps du DNS pour {host}.") from exc
    except (OSError, UnicodeError) as exc:  # nom inconnu, ou invalide (« a..b »)
        raise ToolError(f"Nom de domaine introuvable : {host}") from exc
    addresses = list(dict.fromkeys(raw.partition("%")[0] for raw in found))
    if not addresses:
        raise ToolError(f"Nom de domaine introuvable : {host}")
    for raw in addresses:
        address = ipaddress.ip_address(raw)
        if not _is_public(address):
            raise ToolError(
                f"Adresse refusée : {host} désigne une adresse non publique ({address})."
            )
    return addresses


def _is_public(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Joignable depuis Internet ; une IPv4 logée dans une IPv6 est jugée pour elle-même."""
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            address = address.ipv4_mapped
        elif any(address in network for network in _EMBEDDED_IPV4):
            address = ipaddress.IPv4Address(int(address) & 0xFFFF_FFFF)
    return address.is_global and not address.is_multicast


async def _read_capped(response: httpx2.Response) -> tuple[bytes, bool]:
    """Corps décompressé, coupé à MAX_BYTES ; vrai en second s'il a fallu couper."""
    body = bytearray()
    async for chunk in response.aiter_bytes():  # décompressé morceau par morceau
        body += chunk
        if len(body) > MAX_BYTES:
            return bytes(body[:MAX_BYTES]), True
    return bytes(body), False


# --- Types de contenu et décodage ------------------------------------------------

_HTML: Final = frozenset({"text/html", "application/xhtml+xml"})
_TEXT_APPLICATIONS: Final = frozenset(
    f"application/{name}"
    for name in "json xml javascript x-javascript yaml x-yaml x-ndjson".split()
)


def _is_text(media_type: str) -> bool:
    """Texte, JSON, XML (flux RSS et Atom compris) ; pas un flux sans fin (SSE)."""
    if media_type == "text/event-stream":
        return False
    return (
        media_type.startswith("text/")
        or media_type in _TEXT_APPLICATIONS
        or (media_type.startswith("application/") and media_type.endswith(("+json", "+xml")))
    )


_BOMS: Final = (
    (codecs.BOM_UTF8, "utf-8"),
    (codecs.BOM_UTF16_LE, "utf-16-le"),
    (codecs.BOM_UTF16_BE, "utf-16-be"),
)
_META_CHARSET: Final = re.compile(rb"""<meta[^>]+charset=["']?([\w.:-]+)""", re.IGNORECASE)
_XML_ENCODING: Final = re.compile(rb"""<\?xml[^>]+encoding=["']([\w.:-]+)""", re.IGNORECASE)
_ASCII: Final = bytes(range(128))


def _decode(body: bytes, charset: str | None, media_type: str) -> str:
    """Texte de la page, dans l'ordre des navigateurs : BOM, en-tête HTTP, déclaration dans
    le document ; à défaut UTF-8, ou windows-1252 si ce n'en est visiblement pas."""
    for bom, encoding in _BOMS:
        if body.startswith(bom):
            return body[len(bom) :].decode(encoding, errors="replace")
    found = (_META_CHARSET if media_type in _HTML else _XML_ENCODING).search(body[:4096])
    declared = found.group(1).decode("ascii") if found else None
    for label in (charset, declared):
        if label and (text := _decode_as(body, label)) is not None:
            return text
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        text = body.decode("utf-8", errors="replace")
    # Rien de déclaré et de l'UTF-8 invalide : soit quelques octets abîmés dans une page
    # UTF-8, soit une page en windows-1252 (vieux sites français), où presque chaque octet
    # non ASCII (lettre accentuée) est invalide en UTF-8. translate(None, _ASCII) ne garde
    # que ces octets-là.
    if text.count("\ufffd") * 2 > len(body.translate(None, _ASCII)):
        return body.decode("cp1252", errors="replace")
    return text


def _decode_as(body: bytes, label: str) -> str | None:
    """Décode avec le jeu déclaré ; None s'il est inconnu ou n'en est pas un (« idna »…)."""
    try:
        encoding = codecs.lookup(label).name
        # Comme les navigateurs : latin-1 et ASCII valent windows-1252, qui y ajoute
        # notamment l'euro et l'apostrophe typographique (sinon des caractères de contrôle).
        if encoding in {"iso8859-1", "ascii"}:
            encoding = "cp1252"
        return body.decode(encoding, errors="replace")
    except LookupError, UnicodeError:
        return None


def _render(body: bytes, charset: str | None, media_type: str, url: str, truncated: bool) -> str:
    text = _decode(body, charset, media_type)
    note = f"(page tronquée : seuls ses {MAX_BYTES // 1_000_000} premiers Mo ont été lus)"
    if media_type in _HTML:
        return _render_html(text, url, note if truncated else None)
    head = [f"URL : {url}", note] if truncated else [f"URL : {url}"]
    return "\n".join([*head, "", text])


# --- HTML vers texte -------------------------------------------------------------

# Contenu brut, que l'analyseur rend d'un bloc jusqu'à la balise fermante : ignoré.
_RAW: Final = frozenset({"script", "style", "iframe", "xmp", "noembed", "noframes"})
# Écartés avec tout leur contenu.
_DROPPED: Final = frozenset({"noscript", "template", "svg", "canvas"})
_BLOCKS: Final = frozenset(
    "p div br hr section article main header footer nav aside ul ol blockquote form figure "
    "figcaption dl dt dd address details summary fieldset caption".split()
)
_HEADINGS: Final = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
# Pourtour de la page : ses liens passent après ceux du contenu.
_CHROME: Final = frozenset({"nav", "header", "footer", "aside"})
# Sans balise fermante, ou facultative : sans arbre DOM, impossible de savoir où ils
# finissent, donc jamais écartés comme décoratifs.
_UNBOUNDED: Final = frozenset(
    "area base br col embed hr img input link meta source track wbr "  # vides
    "html head body p li dt dd option optgroup rb rt rtc rp colgroup caption "  # fin facultative
    "thead tbody tfoot tr td th".split()
)
# Polices d'icônes à ligatures : le nom de l'icône (« arrow_upward ») est le texte de l'élément.
_ICON_FONT: Final = re.compile(r"material-(?:icons|symbols)")
_DECORATIVE_MAX: Final = 500  # au-delà, un élément « caché » est sans doute du contenu
_CELL_MAX: Final = 200  # au-delà, une cellule est un bloc de mise en page, pas une donnée
_LABEL_MAX: Final = 120
_SPACES: Final = re.compile(r"\s+")
_BLANK_LINES: Final = re.compile(r"\n{3,}")
_CODE_MARK: Final = re.compile(r"\x00(\d+)\x00")


@dataclass(slots=True)
class _Region:
    """Élément écarté en cours de lecture : ce qu'on y a écrit est retiré à sa fermeture.

    Retirer à la fermeture, plutôt que ne rien écrire, rend l'erreur bénigne : un élément
    jamais fermé (HTML mal formé) n'emporte pas la fin de la page, il reste dans le texte.
    """

    tag: str
    decorative: bool  # aria-hidden ou icône : texte retiré seulement s'il est court
    out: list[str]
    mark: int
    links: int
    depth: int = 1


@dataclass(slots=True)
class _Table:
    parent: list[str]  # où vont les lignes rendues
    cells: list[str] = field(default_factory=list[str])  # cellules de la ligne en cours
    cell: list[str] | None = None  # cellule ouverte


class _Extractor(HTMLParser):
    """Texte d'une page dans l'ordre du document : titres, listes, tableaux, blocs de code."""

    def __init__(self, url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.url = url
        self.base = url  # remplacée par <base href> s'il y en a un
        self.title = ""
        self._title: list[str] | None = None
        self._parts: list[str] = []
        self._code: list[str] = []  # contenus des <pre>, remis à leur place en fin de rendu
        self._pre: list[str] | None = None
        self._pre_depth = 0
        self._raw: str | None = None
        self._regions: list[_Region] = []
        self._tables: list[_Table] = []
        self._chrome = 0
        self._based = False
        self._links: list[tuple[str, str, bool]] = []  # adresse, libellé, dans le pourtour
        self._link: tuple[str, str, int] | None = None  # adresse, libellé de repli, régions
        self._label: list[str] = []

    # -- Événements de l'analyseur

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._raw is not None:
            return
        if tag in _RAW:
            self._raw = tag
            return
        attributes = dict(attrs)
        for region in self._regions:
            if region.tag == tag:
                region.depth += 1
        if tag in _DROPPED or (attrs and tag not in _UNBOUNDED and _is_decorative(attributes)):
            out = self._out()
            self._regions.append(_Region(tag, tag not in _DROPPED, out, len(out), len(self._links)))
        if tag == "base" and not self._based and attributes.get("href"):
            self._based = True
            self.base = _absolute(self.url, attributes.get("href")) or self.url
        elif tag == "title" and not self.title and not self._regions:
            self._title = []
        elif tag == "pre":
            self._pre_depth += 1
            if self._pre is None:
                self._pre = []
        elif tag == "table":
            out = self._out()
            out.append("\n\n")
            self._tables.append(_Table(out))
        elif tag == "tr" and self._tables:
            self._end_row()
        elif tag in ("td", "th") and self._tables:
            self._end_cell()
            self._tables[-1].cell = []
        elif tag in _HEADINGS:
            self._out().append(f"\n\n{'#' * int(tag[1])} ")
        elif tag == "li":
            self._out().append("\n- ")
        elif tag == "a":
            self._open_link(attributes)
        elif tag == "img" and (alt := attributes.get("alt")):
            self._add_label(f" {alt} ")
        if tag in _BLOCKS and (self._pre is None or tag == "br"):
            self._out().append("\n")
        if tag in _CHROME:
            self._chrome += 1

    def handle_endtag(self, tag: str) -> None:
        if self._raw is not None:
            if tag == self._raw:
                self._raw = None
            return
        if tag == "title":
            self._end_title()
        elif tag == "pre" and self._pre is not None:
            self._pre_depth -= 1
            if self._pre_depth <= 0:
                self._end_pre()
        elif tag in ("td", "th") and self._tables:
            self._end_cell()
        elif tag == "tr" and self._tables:
            self._end_row()
        elif tag == "table" and self._tables:
            self._end_table()
        elif tag in _HEADINGS:
            self._out().append("\n")
        elif tag == "a":
            self._close_link()
        if tag in _BLOCKS and tag not in ("br", "hr") and self._pre is None:
            self._out().append("\n")
        if tag in _CHROME and self._chrome:
            self._chrome -= 1
        self._end_regions(tag)

    def handle_data(self, data: str) -> None:
        if self._raw is not None:
            return
        data = data.replace("\x00", "")  # réservé aux marques des blocs de code
        if self._title is not None:
            self._title.append(data)
            return
        self._out().append(data if self._pre is not None else _SPACES.sub(" ", data))
        self._add_label(data)

    def close(self) -> None:
        super().close()
        self._end_title()
        self._close_link()
        if self._pre is not None:
            self._end_pre()
        while self._tables:
            self._end_table()
        # Une région encore ouverte (élément jamais fermé) garde son texte.

    # -- Résultat

    def text(self) -> str:
        text = _tidy(self._parts)
        return _CODE_MARK.sub(lambda mark: f"```\n{self._code[int(mark[1])]}\n```", text)

    def links(self) -> list[tuple[str, str]]:
        """Liens du contenu d'abord, puis ceux du pourtour (menus, pied de page)."""
        content: dict[str, str] = {}
        chrome: dict[str, str] = {}
        for href, label, in_chrome in self._links:
            if href in content:
                continue
            if not in_chrome:
                chrome.pop(href, None)
                content[href] = label
            elif href not in chrome:
                chrome[href] = label
        return [*content.items(), *chrome.items()][:MAX_LINKS]

    # -- Rouages

    def _out(self) -> list[str]:
        """Où écrire : bloc de code, cellule de tableau ou texte de la page."""
        if self._pre is not None:
            return self._pre
        if self._tables:
            table = self._tables[-1]
            return table.parent if table.cell is None else table.cell
        return self._parts

    def _end_regions(self, tag: str) -> None:
        for region in self._regions:
            if region.tag == tag:
                region.depth -= 1
        for index, region in enumerate(self._regions):
            if region.depth <= 0:
                # Celles ouvertes à l'intérieur et restées ouvertes tombent avec elle.
                del self._regions[index:]
                if not region.decorative:
                    del region.out[region.mark :]
                    del self._links[region.links :]
                elif sum(map(len, region.out[region.mark :])) <= _DECORATIVE_MAX:
                    # Texte retiré, liens gardés : un menu replié (aria-hidden) reste un
                    # chemin pour naviguer dans le site.
                    del region.out[region.mark :]
                return

    def _end_title(self) -> None:
        if self._title is not None:
            self.title = " ".join("".join(self._title).split())
            self._title = None

    def _end_pre(self) -> None:
        code = "".join(self._pre or ()).replace("\r\n", "\n").strip("\n")
        self._pre, self._pre_depth = None, 0
        if code.strip():
            self._code.append(code)
            self._out().append(f"\n\x00{len(self._code) - 1}\x00\n")

    def _end_cell(self) -> None:
        table = self._tables[-1]
        if table.cell is not None:
            table.cells.append(_tidy(table.cell))
            table.cell = None

    def _end_row(self) -> None:
        """Ligne de données sur une ligne ; tableau de mise en page en blocs successifs."""
        self._end_cell()
        table = self._tables[-1]
        cells, table.cells = table.cells, []
        if not any(cells):
            return
        if len(cells) > 1 and all(_is_compact(cell) for cell in cells):
            row = " | ".join(cell.replace("\n", " ") for cell in cells)
            table.parent.append(f"| {row} |\n")
        else:
            table.parent.append("\n\n" + "\n\n".join(cell for cell in cells if cell) + "\n\n")

    def _end_table(self) -> None:
        self._end_row()
        self._tables.pop().parent.append("\n\n")

    def _open_link(self, attributes: dict[str, str | None]) -> None:
        self._close_link()  # <a> imbriqué ou jamais fermé : le précédent s'arrête là
        href = _absolute(self.base, attributes.get("href"))
        if href and href != self.url.partition("#")[0]:
            fallback = attributes.get("aria-label") or attributes.get("title") or ""
            self._link = (href, fallback, len(self._regions))
            self._label = []

    def _add_label(self, text: str) -> None:
        # Pas le texte d'un élément écarté ouvert dans le lien (icône…).
        if self._link is not None and len(self._regions) == self._link[2]:
            self._label.append(text)

    def _close_link(self) -> None:
        if self._link is None:
            return
        href, fallback, _ = self._link
        self._link = None
        label = " ".join("".join(self._label).split()) or " ".join(fallback.split()) or href
        if len(label) > _LABEL_MAX:
            label = label[: _LABEL_MAX - 1].rstrip() + "…"
        self._links.append((href, label, self._chrome > 0))


def _is_decorative(attributes: dict[str, str | None]) -> bool:
    """Masqué aux lecteurs d'écran, ou icône dont le nom serait lu comme du texte."""
    hidden = (attributes.get("aria-hidden") or "").strip().lower() == "true"
    return hidden or bool(_ICON_FONT.search(attributes.get("class") or ""))


def _absolute(base: str, href: str | None) -> str | None:
    """Adresse http(s) absolue d'un lien, sans fragment ; None pour mailto:, javascript:…"""
    if not href:
        return None
    try:
        absolute = urljoin(base, href.strip()).partition("#")[0]
    except ValueError:  # « http://[mal formé »
        return None
    return absolute if absolute.startswith(("http://", "https://")) else None


def _is_compact(cell: str) -> bool:
    """Cellule assez courte pour tenir dans une ligne de tableau."""
    return (
        len(cell) <= _CELL_MAX
        and "\x00" not in cell
        and not any(line.startswith("|") for line in cell.split("\n"))
    )


def _tidy(parts: list[str]) -> str:
    """Lignes sans espaces superflus, pas plus d'une ligne vide d'affilée."""
    text = "".join(parts)
    if "\n" not in text:  # le cas d'une cellule ordinaire
        return " ".join(text.split())
    lines = (" ".join(line.split()) for line in text.split("\n"))
    return _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()


def _render_html(html: str, url: str, note: str | None) -> str:
    page = _Extractor(url)
    page.feed(html)
    page.close()
    text = page.text() or "(aucun texte lisible : la page est peut-être générée en JavaScript)"
    out = [f"URL : {url}"]
    if page.title:
        out.append(f"Titre : {page.title}")
    if note:
        out.append(note)
    out += ["", text]
    if links := page.links():
        out += ["", "Liens :", *(f"- {label} : {href}" for href, label in links)]
    return "\n".join(out)
