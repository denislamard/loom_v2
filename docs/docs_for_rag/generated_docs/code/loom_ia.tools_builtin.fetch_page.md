# loom_ia.tools_builtin.fetch_page

Module `loom_ia.tools_builtin.fetch_page` de loom_v2 (fichier `src/loom_ia/tools_builtin/fetch_page.py`).

Outil fetch_page : lit une page web publique et rend son texte au modèle.

## Constantes de `loom_ia.tools_builtin.fetch_page`

- `MAX_BYTES = 5000000` : Octets lus au plus ; au-delà, la suite de la page n'est pas lue.
- `MAX_REDIRECTS = 5` : Redirections suivies au plus.
- `MAX_LINKS = 50` : Liens rendus au plus.
- `MAX_ADDRESSES = 3` : Adresses d'un même nom essayées tour à tour si la connexion échoue.
- `ALLOWED_PORTS = frozenset({80, 443})` : Ports permis : 80 et 443.
- `HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; loom_ia-fetch_page/1.0)', 'Accept': 'text/html,application/xhtml+xml,text/plai…` : En-têtes envoyés avec chaque requête.
- `TIMEOUT = httpx2.Timeout(15.0, connect=5.0)` : Délais : 15 s en tout, 5 s pour se connecter.
- `_EMBEDDED_IPV4 = (ipaddress.IPv6Network('64:ff9b::/96'), ipaddress.IPv6Network('::/96'))` : IPv4 logées dans une IPv6 que ipaddress tient pour globales : préfixe NAT64 bien connu (64:ff9b::7f00:1 désigne 127.0.0.1 derrière une passerelle NAT64) et IPv4 « compatibles » (::a.b.c.d, obsolètes). Les IPv4 « mappées », 6to4 et Teredo, ipaddress les juge déjà.
- `_RAW = frozenset({'script', 'style', 'iframe', 'xmp', 'noembed', 'noframes'})` : Contenu brut, que l'analyseur rend d'un bloc jusqu'à la balise fermante : ignoré.
- `_DROPPED = frozenset({'noscript', 'template', 'svg', 'canvas'})` : Écartés avec tout leur contenu.
- `_CHROME_TAGS = frozenset({'nav', 'aside'})` : Repères de la page (landmarks HTML et ARIA). Le texte et les liens du contenu principal (`<main>`, `role="main"`) passent en premier, ceux du pourtour (menus, bandeaux, pied de page, encadrés) en dernier : un aperçu tronqué montre ainsi le contenu, pas les menus. Dans le contenu principal, rien n'est pourtour : un header y est le titre d'une fiche.
- `_EDGE_TAGS = frozenset({'header', 'footer'})` : header et footer ne sont le bandeau et le pied de page que hors d'un article ou d'une section : dans l'un d'eux, ils en sont le titre et la signature.
- `_UNBOUNDED = frozenset('area base br col embed hr img input link meta source track wbr html head body p li dt dd option optgroup rb …` : Sans balise fermante, ou facultative : sans arbre DOM, impossible de savoir où ils finissent, donc jamais écartés comme décoratifs.
- `_ICON_FONT = re.compile('material-(?:icons|symbols)')` : Polices d'icônes à ligatures : le nom de l'icône (« arrow_upward ») est le texte de l'élément.

## Fonction `loom_ia.tools_builtin.fetch_page.fetch_page`

```python
@tool(side_effects='none', timeout=45, offload_over=20000)
async def fetch_page(url: str) -> str
```

Lit une page web publique (http ou https) et renvoie son adresse finale, son titre,
son texte sans le HTML, puis la liste des liens qu'elle contient.

À utiliser pour consulter une page dont on connaît l'adresse, ou suivre un lien
trouvé dans une page déjà lue. Ne gère ni JavaScript, ni connexion à un compte,
ni PDF, ni image.

## Classe `loom_ia.tools_builtin.fetch_page.Downloaded`

```python
@dataclass(frozen=True, slots=True)
class Downloaded
```

Une page téléchargée, pas encore décodée.

**Attributs :**

- `url` : Adresse finale, après les redirections.
- `body` : Le corps reçu.
- `charset` : Jeu de caractères annoncé, s'il l'est.
- `media_type` : Type annoncé.
- `truncated` : Vrai si la page dépassait `MAX_BYTES` et a été coupée.

## Fonction `loom_ia.tools_builtin.fetch_page.download`

```python
async def download(url: str) -> Downloaded
```

Télécharge une page web publique, avec les garde-fous de `fetch_page`.

Chaque adresse, redirections comprises, est vérifiée (http ou https, ports 80
et 443, hôte public) ; au plus `MAX_REDIRECTS` redirections et `MAX_BYTES`
lus ; seul un contenu textuel est accepté. `ToolError` dit ce qui a échoué.
Partagé avec `loom.memory` (action `add_url`).

## Fonction `loom_ia.tools_builtin.fetch_page.is_html`

```python
def is_html(media_type: str) -> bool
```

Vrai pour une page HTML ou XHTML.

## Fonction `loom_ia.tools_builtin.fetch_page.resolve`

```python
async def resolve(host: str, port: int) -> list[str]
```

Adresses IP du nom ; séparé pour pouvoir le remplacer dans les tests.

## Fonction `loom_ia.tools_builtin.fetch_page.check_url`

```python
def check_url(url: str) -> None
```

Refuse ce que l'adresse suffit à exclure : schéma, identifiants, port.

Les adresses IP ne se jugent qu'à la connexion (`_PublicOnlyTransport`) : vérifier
ici le nom, puis laisser httpx2 le résoudre de nouveau, ne protégerait de rien.

## Classe `loom_ia.tools_builtin.fetch_page._PublicOnlyTransport`

```python
class _PublicOnlyTransport(httpx2.AsyncBaseTransport)
```

Transport qui ne se connecte qu'à des adresses publiques : celles qu'il vient de vérifier.

Vérifier un nom puis laisser httpx2 le résoudre de nouveau laisse deux brèches. Un DNS
complaisant (rebinding, TTL nul) répond une adresse publique à la vérification, puis
127.0.0.1 à la connexion. Et le nom vérifié n'est pas toujours celui que httpx2 résout :
pour « faß.de », getaddrinfo cherche fass.de (IDNA 2003), httpx2 xn--fa-hia.de (IDNA 2008).

Ici, le nom tel que httpx2 l'a encodé est résolu une seule fois, et la connexion part
vers une adresse vérifiée. Le nom reste dans l'en-tête Host et dans le SNI : le
certificat TLS est contrôlé pour lui, pas pour l'adresse.

### Méthode `loom_ia.tools_builtin.fetch_page._PublicOnlyTransport.handle_async_request`

```python
async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response
```

Envoie la requête à une adresse publique seulement.

Port 80 ou 443 ; nom résolu en adresses publiques, essayées tour à tour
(`MAX_ADDRESSES`) ; le nom gardé pour TLS.

**Lève :**

- `ToolError` : Port refusé, ou aucune adresse publique.

### Méthode `loom_ia.tools_builtin.fetch_page._PublicOnlyTransport.aclose`

```python
async def aclose(self) -> None
```

Ferme le transport enrobé.

## Fonction `loom_ia.tools_builtin.fetch_page._public_addresses`

```python
async def _public_addresses(host: str, port: int) -> list[str]
```

Adresses du nom, toutes publiques ; sinon `ToolError`.

Une seule adresse interne suffit à refuser le nom : un domaine qui mêle adresses
publiques et internes n'a rien d'une page web ordinaire.

## Fonction `loom_ia.tools_builtin.fetch_page._is_public`

```python
def _is_public(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool
```

Joignable depuis Internet ; une IPv4 logée dans une IPv6 est jugée pour elle-même.

## Fonction `loom_ia.tools_builtin.fetch_page._read_capped`

```python
async def _read_capped(response: httpx2.Response) -> tuple[bytes, bool]
```

Corps décompressé, coupé à MAX_BYTES ; vrai en second s'il a fallu couper.

## Fonction `loom_ia.tools_builtin.fetch_page._is_text`

```python
def _is_text(media_type: str) -> bool
```

Texte, JSON, XML (flux RSS et Atom compris) ; pas un flux sans fin (SSE).

## Fonction `loom_ia.tools_builtin.fetch_page.decode`

```python
def decode(body: bytes, charset: str | None, media_type: str) -> str
```

Texte de la page, dans l'ordre des navigateurs.

BOM, en-tête HTTP, déclaration dans le document ; à défaut UTF-8, ou windows-1252 si
ce n'en est visiblement pas.

## Fonction `loom_ia.tools_builtin.fetch_page._decode_as`

```python
def _decode_as(body: bytes, label: str) -> str | None
```

Décode avec le jeu déclaré ; None s'il est inconnu ou n'en est pas un (« idna »…).

## Classe `loom_ia.tools_builtin.fetch_page._Region`

```python
@dataclass(slots=True)
class _Region
```

Élément écarté en cours de lecture : ce qu'on y a écrit est retiré à sa fermeture.

Retirer à la fermeture, plutôt que ne rien écrire, rend l'erreur bénigne : un élément
jamais fermé (HTML mal formé) n'emporte pas la fin de la page, il reste dans le texte.

**Attributs :**

- `tag` : Balise de l'élément.
- `decorative` : `aria-hidden` ou icône : texte retiré seulement s'il est court.
- `out` : La partie du texte où il écrit.
- `mark` : Position dans cette partie à son ouverture.
- `links` : Nombre de liens à son ouverture.
- `depth` : Éléments de même balise ouverts dedans, plus lui.

## Classe `loom_ia.tools_builtin.fetch_page._Landmark`

```python
@dataclass(slots=True)
class _Landmark
```

Repère ouvert : le contenu principal, un élément du pourtour, ou un article.

**Attributs :**

- `tag` : Balise du repère.
- `kind` : Contenu principal, pourtour ou article.
- `depth` : Éléments de même balise ouverts dedans, plus lui.

## Classe `loom_ia.tools_builtin.fetch_page._Table`

```python
@dataclass(slots=True)
class _Table
```

Un tableau en cours de lecture.

**Attributs :**

- `parent` : Où vont les lignes rendues.
- `cells` : Cellules de la ligne en cours.
- `cell` : Cellule ouverte.

## Classe `loom_ia.tools_builtin.fetch_page._Extractor`

```python
class _Extractor(HTMLParser)
```

Texte d'une page : titres, listes, tableaux, blocs de code.

Le contenu principal vient d'abord, puis le reste du corps, puis le pourtour ; dans
chacune de ces parties, l'ordre du document.

### Méthode `loom_ia.tools_builtin.fetch_page._Extractor.__init__`

```python
def __init__(self, url: str) -> None
```

Prépare la lecture d'une page.

**Arguments :**

- `url` : Adresse de la page : la base des liens, sauf `<base href>`.

### Méthode `loom_ia.tools_builtin.fetch_page._Extractor.handle_starttag`

```python
def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None
```

Une balise ouvrante : repères, régions écartées, titres, listes, tableaux, liens, blocs de code.

### Méthode `loom_ia.tools_builtin.fetch_page._Extractor.handle_endtag`

```python
def handle_endtag(self, tag: str) -> None
```

Une balise fermante : ferme ce qu'elle avait ouvert.

### Méthode `loom_ia.tools_builtin.fetch_page._Extractor.handle_data`

```python
def handle_data(self, data: str) -> None
```

Du texte : au titre, au bloc de code (tel quel) ou à la partie en cours (espaces réduits).

### Méthode `loom_ia.tools_builtin.fetch_page._Extractor.close`

```python
def close(self) -> None
```

Termine la lecture : titre, lien, bloc de code et tableaux restés ouverts.

### Méthode `loom_ia.tools_builtin.fetch_page._Extractor.text`

```python
def text(self) -> str
```

Le texte de la page : le contenu principal, le reste du corps, puis le pourtour, blocs de code remis à leur place.

### Méthode `loom_ia.tools_builtin.fetch_page._Extractor.links`

```python
def links(self) -> list[tuple[str, str]]
```

Liens du contenu d'abord, puis ceux du pourtour (menus, pied de page).

### Méthode `loom_ia.tools_builtin.fetch_page._Extractor._out`

```python
def _out(self) -> list[str]
```

Où écrire : bloc de code, cellule de tableau ou partie de la page.

### Méthode `loom_ia.tools_builtin.fetch_page._Extractor._part`

```python
def _part(self) -> list[str]
```

La partie de la page où l'on est : contenu principal, pourtour ou reste du corps.

### Méthode `loom_ia.tools_builtin.fetch_page._Extractor._landmark_kind`

```python
def _landmark_kind(self, tag: str, attributes: dict[str, str | None]) -> _Kind | None
```

Le repère qu'ouvre cet élément, s'il en ouvre un.

### Méthode `loom_ia.tools_builtin.fetch_page._Extractor._end_regions`

```python
def _end_regions(self, tag: str) -> None
```

Ferme les régions de cette balise ; ce qu'une région écartée a écrit est retiré (une région décorative ne perd son texte que s'il est court).

### Méthode `loom_ia.tools_builtin.fetch_page._Extractor._end_row`

```python
def _end_row(self) -> None
```

Ligne de données sur une ligne ; tableau de mise en page en blocs successifs.

## Fonction `loom_ia.tools_builtin.fetch_page._is_decorative`

```python
def _is_decorative(attributes: dict[str, str | None]) -> bool
```

Masqué aux lecteurs d'écran, ou icône dont le nom serait lu comme du texte.

## Fonction `loom_ia.tools_builtin.fetch_page._absolute`

```python
def _absolute(base: str, href: str | None) -> str | None
```

Adresse http(s) absolue d'un lien, sans fragment ; None pour un autre schéma (mailto:, javascript:…).

## Fonction `loom_ia.tools_builtin.fetch_page._is_compact`

```python
def _is_compact(cell: str) -> bool
```

Cellule assez courte pour tenir dans une ligne de tableau.

## Fonction `loom_ia.tools_builtin.fetch_page._tidy`

```python
def _tidy(parts: list[str]) -> str
```

Lignes sans espaces superflus, pas plus d'une ligne vide d'affilée.
