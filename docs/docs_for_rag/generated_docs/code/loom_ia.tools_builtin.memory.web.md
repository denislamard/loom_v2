# loom_ia.tools_builtin.memory.web

Module `loom_ia.tools_builtin.memory.web` de loom_v2 (fichier `src/loom_ia/tools_builtin/memory/web.py`).

Pages web pour `add_url` : téléchargement par `fetch_page`, conversion en markdown.

Le réseau passe par `fetch_page.download` : mêmes garde-fous (http et https,
ports 80 et 443, hôtes publics, redirections vérifiées, 5 Mo au plus). La
conversion garde la structure — titres, listes, tableaux, code — après retrait
du bruit évident (navigation, pied de page, scripts) : un titre perdu coûte plus
à la recherche qu'un menu résiduel, et les pages mémorisées sont choisies, pas
moissonnées.

Avant ce retrait, trois structures sont réécrites pour que le découpage en
passages ne sépare pas ce qui va ensemble :

- une FAQ en `<details><summary>` : chaque question devient un titre, un
  niveau sous le titre qui la précède, et sa réponse forme sa section ;
- une liste de définitions `<dl>` : chaque terme et sa définition, un seul
  paragraphe (« **terme** : définition ») ;
- une figure qui contient un schéma `<svg>` : il reste le titre et la
  description du schéma, et la légende ; le reste (« faites défiler le schéma »)
  n'est que de l'interface.

Un titre posé dans un élément de liste devient du gras : en markdown, il ne
serait plus un titre (« 4. ### Étape »), et casserait la liste.

markdownify et BeautifulSoup viennent de l'extra `memory-web` ; ce module ne
les importe qu'à la première page convertie. L'analyse passe par
`html.parser` de la bibliothèque standard, jamais par lxml : installé, lxml
changerait la façon dont openpyxl lit les classeurs de `loom.excel`.

## Constantes de `loom_ia.tools_builtin.memory.web`

- `WEB_EXTRA = 'memory-web'` : L'extra de loom-ia qu'il faut pour `add_url`.
- `WEB_MODULES = ('bs4', 'markdownify')` : Les modules que cet extra apporte.
- `_BLOCKS = ('p', 'div', 'ul', 'ol', 'table', 'pre', 'blockquote', 'dl', *_HEADINGS)` : Ce qu'une définition (`<dd>`) peut contenir sans tenir sur une ligne.

## Fonction `loom_ia.tools_builtin.memory.web.fetch_markdown`

```python
async def fetch_markdown(url: str) -> Extracted
```

La page en markdown et son titre ; `ToolError` si elle est illisible ou vide.

## Fonction `loom_ia.tools_builtin.memory.web.extract_from_html`

```python
def extract_from_html(html: str, url: str) -> Extracted
```

Le contenu de la page en markdown, et son titre (`og:title`, `<title>`, `<h1>`).

## Fonction `loom_ia.tools_builtin.memory.web._questions_as_headings`

```python
def _questions_as_headings(soup: Any) -> None
```

`<details><summary>Q</summary>R</details>` : Q en titre, R en sa section.

Le niveau est celui du titre qui précède, plus un (6 au plus) ; sans titre
avant, 2. Les niveaux sont lus avant toute réécriture : une question réécrite
ne devient pas le parent de la suivante.

## Fonction `loom_ia.tools_builtin.memory.web._definitions_as_paragraphs`

```python
def _definitions_as_paragraphs(soup: Any) -> None
```

`<dt>terme</dt><dd>définition</dd>` : « **terme** : définition », un paragraphe.

Une définition faite de blocs (paragraphes, listes) les garde, précédés de
« **terme** : ». Les listes imbriquées sont réécrites d'abord.

## Fonction `loom_ia.tools_builtin.memory.web._figures_as_text`

```python
def _figures_as_text(soup: Any) -> None
```

Une figure qui contient un `<svg>` : le titre et la description du schéma, la légende.

## Fonction `loom_ia.tools_builtin.memory.web._list_headings_as_bold`

```python
def _list_headings_as_bold(soup: Any) -> None
```

Un titre dans un élément de liste devient du gras : la liste reste une liste.

## Fonction `loom_ia.tools_builtin.memory.web._banners`

```python
def _banners(soup: Any) -> list[Any]
```

Un bandeau (`role=banner`) qui porte la navigation : du bruit, lui aussi.

## Fonction `loom_ia.tools_builtin.memory.web._separate_glued_inlines`

```python
def _separate_glued_inlines(soup: Any, text_type: type) -> None
```

`<span>Label</span>Texte` sans espace entre les deux : on en insère un.

Sans lui, le markdown colle les mots (« Ce qui arriveL'agent »).
