# Outil fourni loom.fetch_page

L'outil `loom.fetch_page` est fourni avec loom_v2. Un agent l'active dans son `agent.yaml` : `- python: loom.fetch_page` dans `tools`.

## Ce que fait loom.fetch_page

Outil fetch_page : lit une page web publique et rend son texte au modèle.

## Comment loom.fetch_page est monté

- cible : `loom_ia.tools_builtin.fetch_page:fetch_page`

## Les arguments que le modèle donne à loom.fetch_page

Description de l'outil, telle que le modèle la reçoit :

```text
Lit une page web publique (http ou https) et renvoie son adresse finale, son titre,
son texte sans le HTML, puis la liste des liens qu'elle contient.

À utiliser pour consulter une page dont on connaît l'adresse, ou suivre un lien
trouvé dans une page déjà lue. Ne gère ni JavaScript, ni connexion à un compte,
ni PDF, ni image.
```

- `url` (`string` ; obligatoire) : (sans description)
