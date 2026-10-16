# SPDX-License-Identifier: Apache-2.0
"""Outil fourni ``loom.fetch_page`` : lire une page web publique, sans sortir du process.

Le réseau est simulé : ``resolve`` est remplacé par un DNS d'essai, et le transport
interne de ``_PublicOnlyTransport`` par un ``httpx2.MockTransport``. Le contrôle des
adresses, lui, tourne pour de vrai : c'est lui qu'on éprouve.
"""

import asyncio
import socket
from collections.abc import Callable, Mapping
from typing import Final

import httpx2
import pytest

import loom_ia.tools_builtin.fetch_page as fetch_module
from loom_ia.core.ports import ToolError
from loom_ia.tools_builtin.fetch_page import (
    _PublicOnlyTransport as PublicOnlyTransport,  # pyright: ignore[reportPrivateUsage]
)
from loom_ia.tools_builtin.fetch_page import fetch_page, resolve

PUBLIC: Final = "93.184.215.14"
PUBLIC_V6: Final = "2606:2800:21f:cb07:6820:80da:af6b:8b2c"
HTML: Final = "text/html; charset=utf-8"

type Route = Callable[[httpx2.Request], httpx2.Response]


class Web:
    """Le réseau des essais : un DNS et des pages, servis par un ``MockTransport``."""

    def __init__(self) -> None:
        self.dns: dict[str, list[str]] = {"exemple.fr": [PUBLIC]}
        self.routes: dict[str, Route] = {}
        self.requests: list[httpx2.Request] = []
        # Adresse et SNI de chaque connexion, notés à l'appel : ensuite, le transport
        # rend au client sa requête avec le nom.
        self.connections: list[tuple[str, object]] = []
        self.resolved: list[str] = []

    def page(
        self,
        url: str,
        body: str | bytes = "",
        *,
        status: int = 200,
        content_type: str | None = HTML,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        data = body.encode() if isinstance(body, str) else body
        sent = dict(headers or {})
        if content_type is not None:
            sent["content-type"] = content_type
        self.routes[url] = lambda _: httpx2.Response(status, headers=sent, content=data)

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        self.connections.append((request.url.host, request.extensions.get("sni_hostname")))
        path = request.url.raw_path.decode("ascii")
        route = self.routes.get(f"{request.url.scheme}://{request.headers['host']}{path}")
        return route(request) if route is not None else httpx2.Response(404)

    async def resolve(self, host: str, port: int) -> list[str]:
        self.resolved.append(host)
        if host not in self.dns:
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        return self.dns[host]


@pytest.fixture
def web(monkeypatch: pytest.MonkeyPatch) -> Web:
    web = Web()
    monkeypatch.setattr(fetch_module, "resolve", web.resolve)
    monkeypatch.setattr(
        fetch_module,
        "_PublicOnlyTransport",
        lambda: PublicOnlyTransport(httpx2.MockTransport(web.handle)),
    )
    return web


async def read(web: Web, html: str, url: str = "https://exemple.fr/") -> str:
    """Le rendu d'une page HTML servie à ``url``."""
    web.page(url, html)
    return await fetch_page(url)


# --- Adresses refusées avant toute connexion -------------------------------------------


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("ftp://exemple.fr/", "Seules les adresses http:// et https://"),
        ("http://moi:secret@exemple.fr/", "identifiant ou un mot de passe"),
        ("http:///chemin", "Adresse sans nom de domaine"),
        ("http://exemple.fr:8080/", "Port 8080 refusé"),
        ("http://exemple.fr:abc/", "Adresse invalide"),
    ],
)
async def test_url_is_checked_before_anything(web: Web, url: str, message: str) -> None:
    with pytest.raises(ToolError, match=message):
        await fetch_page(url)
    assert web.requests == [] and web.resolved == []


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "192.168.1.10",
        "169.254.169.254",  # métadonnées d'un fournisseur de cloud
        "::1",
        "fe80::1%eth0",  # lien local, avec son identifiant de zone
        "::ffff:127.0.0.1",  # IPv4 mappée
        "64:ff9b::7f00:1",  # 127.0.0.1 derrière une passerelle NAT64
        "::7f00:1",  # IPv4 « compatible » (::127.0.0.1)
        "224.0.0.1",  # multicast, que ipaddress tient pour global
        "ff0e::1",
    ],
)
async def test_non_public_addresses_are_refused(web: Web, address: str) -> None:
    web.dns["interne.fr"] = [address]
    with pytest.raises(ToolError, match=r"Adresse refusée : interne\.fr désigne une adresse"):
        await fetch_page("http://interne.fr/")
    assert web.requests == []


async def test_one_internal_address_is_enough_to_refuse_the_name(web: Web) -> None:
    web.dns["mixte.fr"] = [PUBLIC, "10.0.0.1"]
    with pytest.raises(ToolError, match=r"non publique \(10\.0\.0\.1\)"):
        await fetch_page("https://mixte.fr/")
    assert web.requests == []


@pytest.mark.parametrize("address", ["64:ff9b::5db8:d70e", "::ffff:93.184.215.14", PUBLIC_V6])
async def test_public_addresses_in_ipv6_are_accepted(web: Web, address: str) -> None:
    web.dns["exemple.fr"] = [address]
    assert "Bonjour" in await read(web, "<p>Bonjour</p>")


async def test_unknown_name(web: Web) -> None:
    with pytest.raises(ToolError, match=r"Nom de domaine introuvable : inconnu\.fr"):
        await fetch_page("https://inconnu.fr/")


async def test_name_without_address(web: Web) -> None:
    web.dns["vide.fr"] = []
    with pytest.raises(ToolError, match=r"Nom de domaine introuvable : vide\.fr"):
        await fetch_page("https://vide.fr/")


async def test_invalid_name(web: Web, monkeypatch: pytest.MonkeyPatch) -> None:
    async def invalid(host: str, port: int) -> list[str]:
        raise UnicodeError("label vide")

    monkeypatch.setattr(fetch_module, "resolve", invalid)
    with pytest.raises(ToolError, match="Nom de domaine introuvable"):
        await fetch_page("https://exemple.fr/")


async def test_slow_dns(web: Web, monkeypatch: pytest.MonkeyPatch) -> None:
    async def slow(host: str, port: int) -> list[str]:
        await asyncio.sleep(1)
        return [PUBLIC]

    monkeypatch.setattr(fetch_module, "resolve", slow)
    monkeypatch.setattr(fetch_module, "TIMEOUT", httpx2.Timeout(15.0, connect=0.01))
    with pytest.raises(ToolError, match=r"Pas de réponse à temps du DNS pour exemple\.fr"):
        await fetch_page("https://exemple.fr/")


async def test_the_real_resolver_answers_an_address_literal() -> None:
    assert await resolve("127.0.0.1", 80) == ["127.0.0.1"]


async def test_the_transport_refuses_a_port_on_its_own() -> None:
    """Défense en profondeur : même sans ``check_url``, le transport ne sort que sur 80 et 443.

    C'est le transport réseau, sans ``MockTransport`` : refusée avant, la requête ne part pas.
    """
    transport = PublicOnlyTransport()
    with pytest.raises(ToolError, match="Port 8080 refusé"):
        await transport.handle_async_request(httpx2.Request("GET", "http://exemple.fr:8080/"))
    await transport.aclose()


# --- Connexion ----------------------------------------------------------------------------


async def test_connects_to_the_checked_address_under_the_name(web: Web) -> None:
    await read(web, "<p>Bonjour</p>")
    (request,) = web.requests
    # La connexion part vers l'adresse vérifiée ; le nom reste dans Host et dans le SNI.
    assert web.connections == [(PUBLIC, "exemple.fr")]
    assert request.headers["host"] == "exemple.fr"
    # Puis le client retrouve sa requête telle qu'il l'a faite (cookies, journaux).
    assert request.url.host == "exemple.fr" and "sni_hostname" not in request.extensions


async def test_the_name_resolved_is_the_one_httpx2_encodes(web: Web) -> None:
    """« faß.de » : IDNA 2008 (xn--fa-hia.de), celui que httpx2 envoie, pas fass.de."""
    web.dns["xn--fa-hia.de"] = [PUBLIC]
    web.page("https://xn--fa-hia.de/", "<p>Straße</p>")
    out = await fetch_page("https://faß.de/")
    assert web.resolved == ["xn--fa-hia.de"]
    assert out.startswith("URL : https://faß.de/") and "Straße" in out


async def test_an_unreachable_address_gives_way_to_the_next(web: Web) -> None:
    web.dns["exemple.fr"] = [PUBLIC_V6, PUBLIC]
    web.page("https://exemple.fr/", "<p>Par IPv4</p>")
    handle = web.handle

    def no_ipv6(request: httpx2.Request) -> httpx2.Response:
        if request.url.host == PUBLIC_V6:
            raise httpx2.ConnectError("pas de route", request=request)
        return handle(request)

    web.handle = no_ipv6
    assert "Par IPv4" in await fetch_page("https://exemple.fr/")


async def test_at_most_three_addresses_are_tried(web: Web) -> None:
    web.dns["exemple.fr"] = [f"93.184.215.{n}" for n in range(10, 15)]
    tried: list[str] = []

    def unreachable(request: httpx2.Request) -> httpx2.Response:
        tried.append(request.url.host)
        raise httpx2.ConnectTimeout("délai", request=request)

    web.handle = unreachable
    with pytest.raises(ToolError, match=r"Pas de réponse à temps de https://exemple\.fr/"):
        await fetch_page("https://exemple.fr/")
    assert tried == ["93.184.215.10", "93.184.215.11", "93.184.215.12"]


async def test_read_timeout(web: Web) -> None:
    def slow(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("trop long", request=request)

    web.handle = slow
    with pytest.raises(ToolError, match="Pas de réponse à temps de"):
        await fetch_page("https://exemple.fr/")


async def test_protocol_error(web: Web) -> None:
    def broken(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.RemoteProtocolError("réponse mal formée", request=request)

    web.handle = broken
    with pytest.raises(ToolError, match=r"Page injoignable \(RemoteProtocolError\)"):
        await fetch_page("https://exemple.fr/")


async def test_invalid_url_for_httpx2(web: Web) -> None:
    with pytest.raises(ToolError, match=r"Page injoignable \(InvalidURL\)"):
        await fetch_page("https://exemple.fr/\x7f")


# --- Redirections -------------------------------------------------------------------------


async def test_relative_redirect_is_followed(web: Web) -> None:
    web.page("https://exemple.fr/ancien", status=301, headers={"location": "/nouveau?x=1"})
    web.page("https://exemple.fr/nouveau?x=1", "<p>Arrivé</p>")
    out = await fetch_page("  https://exemple.fr/ancien  ")
    assert out.startswith("URL : https://exemple.fr/nouveau?x=1") and "Arrivé" in out


async def test_redirect_without_destination(web: Web) -> None:
    web.page("https://exemple.fr/", status=302, headers={"location": ""})
    with pytest.raises(ToolError, match="Redirection sans destination depuis https://exemple"):
        await fetch_page("https://exemple.fr/")


async def test_redirect_to_an_internal_address_is_refused(web: Web) -> None:
    web.dns["interne.fr"] = ["10.0.0.1"]
    web.page("https://exemple.fr/", status=302, headers={"location": "http://interne.fr/admin"})
    with pytest.raises(ToolError, match=r"Adresse refusée : interne\.fr"):
        await fetch_page("https://exemple.fr/")
    assert len(web.requests) == 1


async def test_redirect_to_another_scheme_is_refused(web: Web) -> None:
    web.page("https://exemple.fr/", status=302, headers={"location": "file:///etc/passwd"})
    with pytest.raises(ToolError, match="Seules les adresses http"):
        await fetch_page("https://exemple.fr/")


async def test_too_many_redirects(web: Web) -> None:
    web.page("https://exemple.fr/boucle", status=302, headers={"location": "/boucle"})
    with pytest.raises(
        ToolError, match=r"Plus de 5 redirections depuis https://exemple\.fr/boucle"
    ):
        await fetch_page("https://exemple.fr/boucle")
    assert len(web.requests) == 6


# --- Réponses -----------------------------------------------------------------------------


@pytest.mark.parametrize(("status", "reason"), [(404, "Not Found"), (503, "Service Unavailable")])
async def test_error_status(web: Web, status: int, reason: str) -> None:
    web.page("https://exemple.fr/", status=status)
    with pytest.raises(ToolError, match=f"La page répond {status} {reason} : https://exemple"):
        await fetch_page("https://exemple.fr/")


@pytest.mark.parametrize("content_type", ["image/png", "application/pdf", "text/event-stream"])
async def test_unsupported_content(web: Web, content_type: str) -> None:
    web.page("https://exemple.fr/", b"\x89PNG", content_type=content_type)
    with pytest.raises(ToolError, match=f"Contenu non pris en charge \\({content_type}\\)"):
        await fetch_page("https://exemple.fr/")


@pytest.mark.parametrize(
    "content_type",
    [
        "application/json",
        "application/ld+json",
        "application/rss+xml",
        "application/x-yaml",
        "text/plain; charset=utf-8",
        "text/csv",
    ],
)
async def test_text_content_is_given_as_is(web: Web, content_type: str) -> None:
    web.page("https://exemple.fr/d", '{"prix": "45 €"}', content_type=content_type)
    out = await fetch_page("https://exemple.fr/d")
    assert out == 'URL : https://exemple.fr/d\n\n{"prix": "45 €"}'


async def test_without_content_type_the_page_is_read_as_html(web: Web) -> None:
    web.page("https://exemple.fr/", "<title>T</title><p>Corps</p>", content_type=None)
    out = await fetch_page("https://exemple.fr/")
    assert out.splitlines()[:4] == ["URL : https://exemple.fr/", "Titre : T", "", "Corps"]


async def test_large_text_is_cut_and_says_so(web: Web) -> None:
    web.page("https://exemple.fr/", b"a" * 5_000_100, content_type="text/plain")
    out = await fetch_page("https://exemple.fr/")
    head, note, blank, text = out.split("\n", 3)
    assert head == "URL : https://exemple.fr/"
    assert note == "(page tronquée : seuls ses 5 premiers Mo ont été lus)"
    assert blank == "" and len(text) == 5_000_000


async def test_large_html_is_cut_and_says_so(web: Web, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fetch_module, "MAX_BYTES", 2_000_000)
    html = "<title>Gros</title>" + "<p>ligne</p>" * 200_000
    lines = (await read(web, html)).splitlines()
    assert lines[:3] == [
        "URL : https://exemple.fr/",
        "Titre : Gros",
        "(page tronquée : seuls ses 2 premiers Mo ont été lus)",
    ]


# --- Décodage -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        "﻿café €".encode(),
        "﻿café €".encode("utf-16-le"),
        "﻿café €".encode("utf-16-be"),
    ],
)
async def test_byte_order_mark_wins(web: Web, body: bytes) -> None:
    # Le BOM l'emporte même sur un jeu de caractères déclaré, et faux.
    web.page("https://exemple.fr/", body, content_type="text/plain; charset=iso-8859-1")
    assert (await fetch_page("https://exemple.fr/")).endswith("\n\ncafé €")


@pytest.mark.parametrize(
    ("content_type", "body", "expected"),
    [
        # latin-1 et ASCII valent windows-1252 : 0x80 est l'euro.
        ("text/plain; charset=iso-8859-1", b"10 \x80", "10 €"),
        ("text/plain; charset=us-ascii", b"l\x92apostrophe", "l\u2019apostrophe"),
        ("text/plain; charset=iso-8859-15", b"10 \xa4", "10 €"),
        # Jeu inconnu, ou qui n'en est pas un : UTF-8 par défaut.
        ("text/plain; charset=inconnu", "déjà".encode(), "déjà"),
        ("text/plain; charset=idna", "déjà".encode(), "déjà"),
        # Rien de déclaré : UTF-8 s'il est valide…
        ("text/plain", "crème brûlée".encode(), "crème brûlée"),
        # … windows-1252 si presque chaque octet non ASCII est invalide en UTF-8…
        ("text/plain", b"caf\xe9 cr\xe8me", "café crème"),
        # … sinon de l'UTF-8 avec quelques caractères de remplacement.
        ("text/plain", "été ".encode() * 10 + b"\xff", "été " * 10 + "�"),
    ],
)
async def test_charset(web: Web, content_type: str, body: bytes, expected: str) -> None:
    web.page("https://exemple.fr/", body, content_type=content_type)
    assert (await fetch_page("https://exemple.fr/")).endswith("\n\n" + expected)


async def test_html_meta_charset(web: Web) -> None:
    body = '<meta charset="windows-1252"><p>Déplacement 45 €</p>'.encode("cp1252")
    web.page("https://exemple.fr/", body, content_type="text/html")
    assert "Déplacement 45 €" in await fetch_page("https://exemple.fr/")


async def test_xml_declared_encoding(web: Web) -> None:
    body = '<?xml version="1.0" encoding="iso-8859-15"?><prix>10 €</prix>'.encode("iso-8859-15")
    web.page("https://exemple.fr/flux", body, content_type="application/rss+xml")
    assert (await fetch_page("https://exemple.fr/flux")).endswith("<prix>10 €</prix>")


async def test_header_charset_wins_over_meta(web: Web) -> None:
    body = '<meta charset="iso-8859-1"><p>été</p>'.encode()
    web.page("https://exemple.fr/", body, content_type=HTML)
    assert "été" in await fetch_page("https://exemple.fr/")


# --- Du HTML au texte ---------------------------------------------------------------------


async def test_page_structure(web: Web) -> None:
    html = """<!doctype html><html><head><title>  Plombier
      Dupont </title><style>p { color: red }</style>
    <script>if (a < b) { document.write("<p>caché</p>") }</script></head>
    <body><h1>Tarifs</h1><p>Déplacement   offert<br>dans Dijon.</p>
    <h2>Prestations</h2><ul><li>Fuite</li><li>Chauffe-eau</li></ul>
    <p>Fin\x00.</p></body></html>"""
    out = await read(web, html)
    assert out == (
        "URL : https://exemple.fr/\n"
        "Titre : Plombier Dupont\n"
        "\n"
        "# Tarifs\n"
        "\n"
        "Déplacement offert\n"
        "dans Dijon.\n"
        "\n"
        "## Prestations\n"
        "\n"
        "- Fuite\n"
        "- Chauffe-eau\n"
        "\n"
        "Fin."
    )


async def test_page_without_text(web: Web) -> None:
    out = await read(web, "<div id='app'></div><script>render()</script>")
    assert out.endswith("(aucun texte lisible : la page est peut-être générée en JavaScript)")


async def test_data_table_and_layout_table(web: Web) -> None:
    long_cell = "Texte " * 50
    html = f"""<table><tr><th>Prestation</th><th>Prix</th></tr>
    <tr><td>Fuite</td><td>90 €</td></tr><tr><td></td><td></td></tr></table>
    <table><tr><td>{long_cell}</td><td>Menu</td></tr><tr><td>Seule</td></tr></table>"""
    out = await read(web, html)
    assert "| Prestation | Prix |\n| Fuite | 90 € |" in out
    # Cellule trop longue, ou seule dans sa ligne : un bloc, pas une ligne de tableau.
    assert f"\n\n{long_cell.strip()}\n\nMenu\n\nSeule" in out


async def test_nested_table_and_code_in_a_cell(web: Web) -> None:
    html = """<table><tr><td>Avant<table><tr><td>a</td><td>b</td></tr></table></td>
    <td><pre>x = 1</pre></td></tr></table>"""
    out = await read(web, html)
    assert "| a | b |" in out
    assert "```\nx = 1\n```" in out


async def test_code_blocks_keep_their_layout(web: Web) -> None:
    html = """<p>Exemple :</p><pre><code>def f():
    return  1<br>fin</code><pre>imbriqué</pre></pre><pre>   </pre><p>Après</p>"""
    out = await read(web, html)
    assert "```\ndef f():\n    return  1\nfinimbriqué\n```" in out
    assert out.count("```") == 2 and out.endswith("Après")


async def test_unclosed_table(web: Web) -> None:
    assert (await read(web, "<table><tr><td>a</td><td>b")).endswith("| a | b |")


async def test_unclosed_code_block(web: Web) -> None:
    assert (await read(web, "<p>Début</p><pre>reste\n  du code")).endswith(
        "```\nreste\n  du code\n```"
    )


async def test_dropped_elements_take_their_links_with_them(web: Web) -> None:
    html = """<p>Visible</p><noscript>Activez <a href="/js">JavaScript</a></noscript>
    <template><a href="/modele">modèle</a></template>
    <svg><title>Icône</title><text>SVG</text></svg><canvas>Dessin</canvas><p>Fin</p>"""
    out = await read(web, html)
    for hidden in ("Activez", "JavaScript", "modèle", "Icône", "SVG", "Dessin", "/js", "/modele"):
        assert hidden not in out
    assert "Titre" not in out and "Visible" in out and out.endswith("Fin")


async def test_decorative_elements(web: Web) -> None:
    long_text = "Contenu important. " * 40
    html = f"""<nav aria-hidden="true"><a href="/menu">Menu replié</a></nav>
    <div aria-hidden="true"><div>{long_text}</div></div>
    <p aria-hidden="true">Paragraphe gardé</p>
    <a href="/haut"><span class="material-icons">arrow_upward</span> Haut</a>"""
    out = await read(web, html)
    # Texte court et caché retiré, mais ses liens gardés pour naviguer.
    assert "Menu replié" not in out.split("Liens :")[0] and "https://exemple.fr/menu" in out
    # Long, ce n'est plus de la décoration : gardé.
    assert long_text.strip() in out
    # Un élément sans fin connue n'est jamais tenu pour décoratif.
    assert "Paragraphe gardé" in out
    # Le nom d'une icône n'entre ni dans le texte ni dans le libellé du lien.
    assert "arrow_upward" not in out and "- Haut : https://exemple.fr/haut" in out


async def test_unclosed_hidden_element_keeps_its_text(web: Web) -> None:
    assert (await read(web, '<p>Début</p><div aria-hidden="true">jamais fermé')).endswith(
        "jamais fermé"
    )


async def test_links(web: Web) -> None:
    long_label = "Mot " * 40
    html = f"""<base href="https://exemple.fr/docs/"><base href="https://ailleurs.fr/">
    <header><a href="/accueil">Accueil</a><a href="/contact">Contact (menu)</a></header>
    <p><a href="tarifs.html#plomberie">Nos tarifs</a>
    <a href="https://exemple.fr/">Cette page</a>
    <a href="mailto:contact@exemple.fr">Écrire</a><a href="javascript:void(0)">JS</a>
    <a href="http://[mal-formé/">Cassé</a>
    <a href="/icone" aria-label="Ouvrir le panier"></a>
    <a href="/aide" title="Aide en ligne"></a>
    <a href="/brut"></a>
    <a href="/logo"><img src="l.png" alt="Logo Dupont"></a>
    <a href="/premier">Premier<a href="/second">Second</a>
    <a href="/long">{long_label}</a>
    <a href="/contact">Contact</a></p>
    <footer><a href="/mentions">Mentions</a><a href="/tarifs-pied">Tarifs</a></footer>"""
    links = (await read(web, html)).split("Liens :\n")[1].splitlines()
    assert links[:9] == [
        "- Nos tarifs : https://exemple.fr/docs/tarifs.html",
        "- Ouvrir le panier : https://exemple.fr/icone",
        "- Aide en ligne : https://exemple.fr/aide",
        "- https://exemple.fr/brut : https://exemple.fr/brut",
        "- Logo Dupont : https://exemple.fr/logo",
        "- Premier : https://exemple.fr/premier",
        "- Second : https://exemple.fr/second",
        # Libellé coupé à 120 caractères : 119, puis « … ».
        f"- {' '.join(['Mot'] * 30)}… : https://exemple.fr/long",
        "- Contact : https://exemple.fr/contact",
    ]
    # Les liens du pourtour viennent après ceux du contenu, sans doublon.
    assert links[9:] == [
        "- Accueil : https://exemple.fr/accueil",
        "- Mentions : https://exemple.fr/mentions",
        "- Tarifs : https://exemple.fr/tarifs-pied",
    ]


async def test_duplicate_and_empty_links(web: Web) -> None:
    html = """<nav><a href="/a">A (menu)</a><a href="/a">A encore</a><a href="/m">M</a>
    <a href="/m">M bis</a></nav><p><a href="/b">B</a><a href="/b">B bis</a>
    <a>sans adresse</a><a href="">vide</a></p>"""
    links = (await read(web, html)).split("Liens :\n")[1].splitlines()
    assert links == [
        "- B : https://exemple.fr/b",
        "- A (menu) : https://exemple.fr/a",
        "- M : https://exemple.fr/m",
    ]


# --- Le contenu principal d'abord, le pourtour ensuite -----------------------------------


def in_order(text: str, *pieces: str) -> bool:
    """Vrai si chaque morceau est dans le texte, et dans cet ordre."""
    positions = [text.index(piece) for piece in pieces]
    return positions == sorted(positions)


async def test_main_content_comes_first(web: Web) -> None:
    html = """<a href="#contenu">Aller au contenu</a>
    <header><a href="/">Logo</a><nav><a href="/menu">Menu</a></nav></header>
    <div class="bandeau">Cookies acceptés</div>
    <main id="contenu"><h1>Cours TOTALENERGIES</h1><p>80,21 €</p></main>
    <footer role="contentinfo">Mentions légales</footer>"""
    text = (await read(web, html)).split("Liens :")[0]
    # Contenu principal, puis le reste du corps, puis le pourtour : chacun dans l'ordre du document.
    assert in_order(
        text,
        "# Cours TOTALENERGIES",
        "80,21 €",
        "Aller au contenu",
        "Cookies acceptés",
        "Logo",
        "Menu",
        "Mentions légales",
    )


async def test_role_main_ends_with_its_own_element(web: Web) -> None:
    html = """<div>Avant</div><div role="main"><div><p>Contenu</p></div><div>Suite</div></div>
    <div>Après</div>"""
    assert in_order(await read(web, html), "Contenu", "Suite", "Avant", "Après")


async def test_nothing_is_chrome_inside_main(web: Web) -> None:
    html = """<nav><a href="/menu">Menu du site</a></nav>
    <main><header><nav><a href="/cours">Cours</a></nav><h1>Fiche</h1></header>
    <p>Corps</p><aside>Encadré</aside><footer>Pied de fiche</footer></main>"""
    text, links = (await read(web, html)).split("Liens :\n")
    assert in_order(text, "Cours", "# Fiche", "Corps", "Encadré", "Pied de fiche", "Menu du site")
    # Les liens suivent le même partage : ceux de l'en-tête de la fiche sont du contenu.
    assert links.splitlines() == [
        "- Cours : https://exemple.fr/cours",
        "- Menu du site : https://exemple.fr/menu",
    ]


async def test_without_main_the_chrome_goes_last(web: Web) -> None:
    html = """<header>Plomberie Dupont</header><nav>Accueil · Tarifs</nav>
    <article><header><h1>Nos tarifs</h1></header><p>Déplacement 45 €</p>
    <footer>Mis à jour en octobre</footer></article>
    <aside>Avis clients</aside><footer>Mentions légales</footer>"""
    # Le header et le footer d'un article en sont le titre et la signature : ils restent.
    assert in_order(
        await read(web, html),
        "# Nos tarifs",
        "Déplacement 45 €",
        "Mis à jour en octobre",
        "Plomberie Dupont",
        "Accueil · Tarifs",
        "Avis clients",
        "Mentions légales",
    )


async def test_aria_roles_mark_the_chrome(web: Web) -> None:
    html = """<div role="banner">Bandeau</div><div role="navigation">Menu</div><p>Texte</p>
    <div role="complementary">Encadré</div><div role="contentinfo">Pied</div>"""
    assert in_order(await read(web, html), "Texte", "Bandeau", "Menu", "Encadré", "Pied")


async def test_an_element_without_known_end_is_no_landmark(web: Web) -> None:
    # Un <li> se ferme sans balise : en faire le contenu principal prendrait toute la suite.
    html = '<p>Avant</p><ul><li role="main">Élément<li>Autre</ul><p>Après</p>'
    assert in_order(await read(web, html), "Avant", "Élément", "Autre", "Après")


async def test_a_long_site_header_no_longer_hides_the_content(web: Web) -> None:
    """La structure de la page de cours de Boursorama : un en-tête de site de plus de
    20 000 caractères, puis main, qui commence par l'en-tête de la fiche (titre et cours)."""
    menus = "".join(f'<li><a href="/rubrique/{n}">Rubrique {n}</a></li>' for n in range(1500))
    html = f"""<a href="#main-content">Aller au contenu principal</a>
    <header class="l-header"><nav><ul>{menus}</ul></nav></header>
    <main id="main-content"><section class="l-quotepage">
    <header class="l-quotepage__header"><nav><ul><li><a href="/cours/">Cours</a></li>
    <li><a href="/actualites/">Actualités</a></li></ul></nav>
    <h1>Cours TOTALENERGIES</h1><p>80,210 EUR</p></header>
    <article><h2>Historique 5 jours</h2></article></section></main>
    <footer role="contentinfo">Mentions légales</footer>"""
    out = await read(web, html)
    text, links = out.split("Liens :\n")
    assert len(text) > 20_000
    # Ce qu'un aperçu montrerait : le début du texte, le titre et le cours.
    assert text.index("# Cours TOTALENERGIES") < 200
    assert in_order(text, "80,210 EUR", "Historique 5 jours", "Aller au contenu", "Rubrique 0")
    assert links.splitlines()[:2] == [
        "- Cours : https://exemple.fr/cours/",
        "- Actualités : https://exemple.fr/actualites/",
    ]


async def test_at_most_fifty_links(web: Web) -> None:
    html = "".join(f'<a href="/p{n}">Page {n}</a>' for n in range(60))
    links = (await read(web, html)).split("Liens :\n")[1].splitlines()
    assert len(links) == 50 and links[-1] == "- Page 49 : https://exemple.fr/p49"
