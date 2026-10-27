# Analyse d'impact — sortir les fichiers de la VM du forge loom_v2

27 octobre 2026 · Denis Lamard

> **Mise à jour du 27/10 : décidé et réalisé.** Les neuf décisions suivent les recommandations (§ Décisions prises). La réalisation est décrite à la fin (§ Réalisation). Le corps de l'analyse décrit le prototype tel qu'il était au moment de la décision.

## En bref

**Le besoin.** Un outil forgé tourne dans la VM Firecracker. Ce qu'il écrit dans `out/` y reste : le forge le signale (« fichiers produits, non rapatriés ») et la session execd l'efface à sa fermeture. Dans la session `rapport-01`, le graphique SVG est donc passé par la **valeur rendue** de l'outil. Haiku l'a ensuite recopié mot pour mot dans `local_files` : 8 Ko de SVG, 4 292 tokens de sortie, et un risque d'erreur de recopie à chaque caractère.

**La proposition, prototypée.** Elle s'appuie sur ce que loom a déjà ; trois pièces manquaient.

| Maillon | État avant | Prototype |
| --- | --- | --- |
| VM → hôte | `execd file.get` et `Session.get_file` existent, rien ne les appelle | le forge rapatrie `out/` après chaque appel d'un outil forgé |
| hôte → stockage d'artefacts | l'exécuteur range tout `InlineDataBlock` rendu par un outil (`origin: tool_output`), le journal n'en garde que l'URI | inchangé, le forge s'y branche |
| sous-agent → parent | l'URI remonte dans `ToolOutput.artifacts`, mais le modèle du parent ne la voit pas | le fichier suit la réponse de l'enfant |
| stockage → dossier | rien | `local_files` : `write` avec `file` au lieu de `content` copie un fichier de la conversation |
| stockage → utilisateur | `loom sessions files <session> --out`, `GET /sessions/{id}/files/{file}` | inchangé |

Les octets ne passent jamais par le modèle. Il voit `[fichier graphe.svg (image/svg+xml, 8 Ko)]`, ou l'image elle-même s'il a la vision. Pour livrer, il écrit `{"action": "write", "path": "workspace/graphe.svg", "file": "graphe.svg"}`.

**Verdict : faisable, recommandé.**
- **Taille :** le prototype touche 9 fichiers de `src/` (+466 / −54) et ajoute 41 tests.
- **Contrôles :** la suite complète est verte, pyright strict est à 0, ruff est propre et les 5 contrats d'import tiennent.
- **Essai de bout en bout :** il reprend la forme de ton agent (un chef, le forgeron en sous-agent, un dossier de rendu). Le SVG part de la VM, arrive dans le dossier, et le journal n'en contient pas un octet. Le rejeu est identique et ne relance pas la VM.

**Ce qu'il faut savoir avant de décider.**
1. **Toutes les requêtes d'un agent qui a `forge` ou `local_files` changent.**
   - Les descriptions grossissent : environ +100 tokens pour `forge`, environ +110 pour `local_files`.
   - Les runs déjà journalisés ne se rejouent plus à l'identique (« les outils proposés »).
   - Le cache de prompt repart de zéro une fois.
2. **Un changement du moteur vaut pour tous les outils, pas seulement le forge.** Une image rendue par un outil que le modèle refuse (format, taille) devient une mention ; avant, le run échouait. C'est une correction, mais elle change un comportement existant (D-3).
3. **C'est le premier passage d'un fichier binaire de la VM vers le disque de l'hôte.** La copie suit les droits et l'approbation de `write`, ni plus ni moins (D-7).

**Treize pièges trouvés en écrivant le prototype ou par une relecture indépendante.** Neuf sont corrigés et testés, quatre restent ouverts et sont mineurs ou relèvent d'une décision. Les plus sérieux :
- **Course entre deux appels parallèles.** execd vide `out/` au début de chaque job. Si le verrou de la session était relâché avant le transfert, le second appel effacerait les fichiers du premier. Corrigé : le verrou couvre exécution et transfert. Le test échoue bien quand on retire cette protection.
- **Faux PNG.** Un fichier nommé `graphe.png` qui n'en est pas un partirait comme image vers un modèle vision, et le fournisseur refuserait la requête. Corrigé : c'est la signature du fichier qui fait l'image, pas son nom.
- **Image refusée = session morte.** Une image trop grosse rendue par un outil restait dans l'historique, et chaque appel suivant échouait. Corrigé : elle devient une mention qui dit pourquoi.
- **Délai dépassé pendant le transfert.** Le résultat du job était perdu. Corrigé : le transfert a sa propre échéance (25 s) ; une fois passée, le résultat est gardé et la liste des fichiers restants est dite.

## Périmètre et état de référence

- **Base :** ton commit `f413ee5` (« excel tool read files »). Ton dépôt est propre à ce commit.
- **Prototype :** branche `forge-files`, commit `fb3a150`, dans l'espace de travail cloud de Claude. Rien n'est poussé, et rien n'a été écrit dans `/home/denis/dev/loom_v2`, à part ce document.
- **Environnement :** Python 3.14, tous les extras ; execd réel en socket Unix pour les essais avec code exécuté. La VM elle-même n'est pas utilisée : c'est le même service, sans vsock.

| Contrôle | Référence `f413ee5` | Prototype `fb3a150` |
| --- | --- | --- |
| `pytest` (sans Postgres, RabbitMQ ni Redis) | 3 536 passés, 203 sautés | 3 577 passés (+41), 203 sautés |
| `pyright` strict | 0 | 0 |
| `ruff check`, `ruff format --check` | propres | propres |
| `lint-imports` | 5 contrats tenus | 5 contrats tenus |

**Lu :**
- `adapters/firecracker/forge.py` et `session.py` ;
- côté invité, `firecracker/service/session.py` et `limits.py` (`file.get`, `_scan_outputs`, `out/` vidé avant chaque job, `out_files`) ;
- `engine/executor.py` (rangement des `InlineDataBlock`, construction du `ToolContext`) ;
- `engine/media.py`, `engine/subagents.py`, `engine/offload.py`, `engine/delegated.py` (`RunView`) ;
- `core/model/media.py`, `core/model/content.py`, `core/projections/run_state.py`, `core/ports/artifact_store.py` ;
- `adapters/artifacts/sealing.py`, `access/api.py` (`session_files`, `session_file`, suppression), `access/cli/main.py` (`--out`) ;
- `tools_builtin/local_files.py` et `_folders.py`, `replay/book.py`.

## Analyse fonctionnelle

### Ce que voit l'utilisateur de loom

Rien à configurer : le rapatriement est actif par défaut, avec des bornes. Un paramètre facultatif de la source les règle, ou le coupe :

```yaml
tool_sources:
- name: forge
  entry_point: forge
  params:
    vm_dir: /home/denis/dev/vm_loomv2/forge
    catalog_dir: catalogue
    limits: { wall_ms: 9000 }
    files: { max_files: 10, max_file_bytes: 16777216, max_total_bytes: 33554432 }   # ou files: false
```

| Clé | Défaut | Plafond | Rôle |
| --- | --- | --- | --- |
| `max_files` | 10 | 512 (le plafond `out_files` d'execd) | fichiers rapatriés par appel |
| `max_file_bytes` | 16 Mo | 64 Mo | taille d'un fichier |
| `max_total_bytes` | 32 Mo | 256 Mo | total d'un appel ; au moins `max_file_bytes` |

Les fichiers vont au stockage d'artefacts. Ton agent en a déjà un : sans réglage, il suit le journal `jsonl`, dans `data/.artifacts`. Ensuite :
- **en ligne de commande :** `loom sessions files <session>` les liste, `--out <dossier>` les écrit sous leur nom ;
- **en REST :** `GET /sessions/{id}/files` puis `/sessions/{id}/files/{file}` ;
- **par l'agent lui-même :** `local_files` avec `file`.

### Ce que le modèle lit

**La description de `forge`** gagne une phrase (376 caractères), absente avec `files: false` :

> Un fichier (image, PDF, CSV…) s'écrit dans le dossier `os.environ['OUT_DIR']` : à chaque appel de l'outil (pas pendant les exemples), ce qui s'y trouve est rapatrié et rendu comme fichier, 10 au plus, 16 Mo chacun ; la valeur rendue reste courte (n'y recopie pas le contenu du fichier). Les exemples vérifient alors cette valeur, par exemple le nom du fichier ou un décompte.

Aujourd'hui, le modèle ne sait pas que `OUT_DIR` existe. La phrase répond aussi au problème des exemples tautologiques de `rapport-01` : on vérifie une valeur courte, pas le document.

**`local_files`** gagne l'argument `file` : « Pour write, à la place de content : nom d'un fichier de la conversation (joint, ou rendu par un outil), copié tel quel. » Sa description ne parle plus de « fichiers texte » : `read` reste limité au texte, mais la copie accepte le binaire.

### Ce que le modèle reçoit

**Un outil forgé** qui écrit `graphe.svg` et rend `"graphe.svg"` produit ce résultat :

```
"graphe.svg"
[fichier graphe.svg (image/svg+xml, 8 Ko)]
```

La seconde ligne est la mention que le moteur construit à partir de la référence. Un modèle vision reçoit l'image à sa place, pour les PNG, JPEG, GIF et WebP seulement ; un SVG reste une mention.

**Un fichier laissé dans la VM** est listé avec sa raison, 20 lignes au plus :

```
fichiers non rapatriés :
- gros.bin (20971520 o) : au-delà de 16 Mo par fichier
- d.txt (1 o) : au-delà de 10 fichiers par appel
- lien : lien ou fichier spécial, ignoré
- … et 15 autre(s)
```

Raisons possibles :
- au-delà d'une borne (fichiers, taille, total) ;
- « a changé depuis la fin du job » (taille ou sha256 différents du manifeste) ;
- le refus d'execd ;
- « délai de rapatriement dépassé » ou « transfert interrompu » (session coupée) ;
- « en double dans le manifeste » ;
- « lien ou fichier spécial, ignoré » ;
- « rapatriement désactivé » (`files: false`).

**Le type** se décide ainsi : la signature d'image l'emporte ; à défaut, l'extension, d'après la table de loom et non celle du système, pour que le même fichier ait le même type sur toutes les machines. Un nom d'image que la signature ne confirme pas donne `application/octet-stream`.

**Le parent d'un sous-agent** reçoit la réponse de l'enfant, puis la référence de chaque fichier produit par les outils de l'enfant.

### Ce que l'outil refuse, et comment

`local_files`, `write` avec `file` :

| Cas | Message au modèle |
| --- | --- |
| ni `content` ni `file`, ou les deux | « write attend 'content', le texte à écrire, ou 'file', un fichier de la conversation à copier — l'un des deux. » |
| `file` hors de `write` | « 'file' ne sert qu'à write. » |
| nom inconnu | « Fichier 'x.svg' inconnu ; fichiers de la conversation : graphe.svg, photo.png. » |
| plus de 16 Mo | « graphe.svg (image/svg+xml, 20,0 Mo) : au-delà de 16,0 Mo. » |
| absent du stockage | « … : absent du stockage. » |
| pas de stockage d'artefacts | « Aucun fichier de la conversation n'est lisible ici (pas de stockage). » |
| droits, fichier existant, dossier | les messages de `write` aujourd'hui |

## Analyse technique

### Modifications, mesurées sur le prototype

| Fichier | Lignes | Ce qui change |
| --- | --- | --- |
| `adapters/firecracker/forge.py` | +233 / −27 | `FilePolicy` et param `files` ; `_fetch` (bornes, échéance, contrôle sha256, tri, dédoublonnage, noms nettoyés) ; `_Run.produce` qui rapatrie sous le verrou ; `_output` rend `TextBlock` + `InlineDataBlock` ; phrase de description ; délai des outils +30 s |
| `core/model/media.py` | +26 | `media_type_of(nom, octets)` |
| `core/ports/tool.py` | +24 / −1 | protocole `SeenFiles`, champ `ToolContext.files` |
| `engine/media.py` | +91 / −13 | image d'outil refusée → mention ; `ConversationFiles` |
| `engine/executor.py` | +11 / −1 | remplit `ToolContext.files` |
| `engine/subagents.py` | +13 / −3 | fichiers de l'enfant dans le résultat du parent ; une réponse vide avec fichiers n'est plus « rien répondu » |
| `tools_builtin/local_files.py` | +64 / −9 | argument `file`, `_copied`, `_copy`, description |
| `core/model/__init__.py`, `core/ports/__init__.py` | +4 | exports |

### Choix techniques et leurs raisons

- **Le moteur range, le forge ne connaît pas le stockage.** Le forge rend des `InlineDataBlock`, comme n'importe quel outil Python qui rend une `Image`.
  - Le chemin existant fait tout le reste : l'URI adressée par le contenu (`artifact://<client>/<session>/<sha256>.<ext>`), le sceau par client, l'événement `artifact.stored` écrit avant `tool.completed`, la liste `session_files`, la suppression RGPD.
  - Aucun contrat d'import ne bouge : un adaptateur ne voit pas le moteur.
- **Rapatrier sous le verrou de la session.** execd vide `out/` au début de chaque job, et `file.get` n'est valable qu'après un `tool.exec`. Le verrou de `_Run` couvre donc l'exécution et le transfert. Le prix : deux appels parallèles du même run attendent le transfert l'un de l'autre, une centaine de millisecondes pour 16 Mo (§ Mesures).
- **Contrôler avant et après.** Le manifeste vient de l'invité.
  - **Avant :** les bornes se jugent sur la taille annoncée, sans rien lire, et `get_file` est borné par cette taille, si bien qu'un fichier qui grossit est coupé en route.
  - **Après :** taille et sha256 doivent correspondre au manifeste. Sinon le fichier est écarté, jamais rendu à moitié.
  - **Ce que cela couvre :** un processus du job qui aurait survécu et modifierait `out/` après le scan, comme une VM qui ment.
- **Les exemples de `forge` ne rapatrient rien** : ce sont des tests, et leurs fichiers ne concernent personne.
- **`ConversationFiles` : un outil ne relit que ce que l'agent a vu.**
  - **Ce qu'elle contient :** les références (`ArtifactRefBlock`) présentes dans l'historique de la session et les messages du run, pièces jointes et résultats d'outils, ceux d'un sous-agent compris.
  - **Ce qui est ignoré :** une référence d'un autre client ou d'une autre session, par exemple un `Message` construit par l'application.
  - **Désignation :** par le nom vu (le plus récent l'emporte), ou par le nom dans le stockage (`<sha256>.svg`). Jamais par une URI devinée.
- **La copie passe par `write`.** Elle réutilise les droits, l'approbation (`assess_call`), l'écriture atomique, le refus d'écraser sans `overwrite`, et l'idempotence : une copie refaite après une reprise trouve le même contenu et rend `written: false`.
- **Images d'outils refusées → mention.** Une image reste dans l'historique ; si le modèle la refuse, une erreur ferait échouer chaque appel suivant de la session. L'outil n'a pas choisi le modèle. Une pièce jointe refusée reste une erreur : celui qui l'a jointe peut en joindre une autre.

## Bugs et pièges trouvés

| # | Piège | Trouvé | État |
| --- | --- | --- | --- |
| 1 | Deux appels parallèles : le second job vide `out/` avant le transfert du premier | en écrivant | **corrigé** (verrou), test réel avec execd, qui échoue quand la protection est retirée |
| 2 | Un `graphe.png` qui n'est pas un PNG part comme image, et le fournisseur refuse la requête | en écrivant | **corrigé** (`media_type_of`) |
| 3 | Une image d'outil refusée par le modèle (format, `max_image_bytes`) fait échouer le run, puis chaque appel suivant de la session | en écrivant | **corrigé** (mention) ; vaut pour tous les outils |
| 4 | Le parent ne voit pas les fichiers de l'enfant (l'URI reste dans `ToolOutput.artifacts`, que le modèle ne lit pas) | en écrivant | **corrigé** |
| 5 | Un enfant qui ne répond rien mais a produit des fichiers : « n'a rien répondu », fichiers perdus | relecture | **corrigé** |
| 6 | Liste « non rapatriés » sans borne : 400 fichiers donnent 98 706 caractères, le déport ne garde que `data` et la liste est perdue | relecture | **corrigé** (20 lignes, puis « et K autre(s) ») |
| 7 | Manifeste hostile : chemins en double rapatriés plusieurs fois ; noms avec retours à la ligne ou caractères de contrôle, longueur illimitée | relecture | **corrigé** (dédoublonnage, caractères de contrôle remplacés par `?`, 200 caractères au plus) |
| 8 | L'ordre d'`os.walk` entre sous-dossiers n'est pas garanti : le choix des 10 fichiers changeait d'une machine à l'autre | relecture | **corrigé** (tri par chemin) |
| 9 | Transfert plus long que le délai de l'outil : résultat du job perdu, session fermée | relecture | **corrigé** (échéance de 25 s dans les 30 s ajoutées au délai ; plafonds de configuration) |
| 10 | Une image de plus de 5 Mo vers un modèle vision **sans `max_image_bytes`** : le fournisseur refuse, le run échoue | relecture | **ouvert**, D-4 ; ne touche pas ton agent (modèles sans vision) |
| 11 | Un nom porté par deux fichiers (deux appels rendent `graphe.png`) : `file` prend le plus récent ; avec `tool_result_media: true`, un modèle vision ne voit pas le nom de l'image | relecture | **ouvert**, D-8 |
| 12 | Plus de `out_files` fichiers dans `out/` (128 par défaut côté execd) : execd fait échouer **tout** le job, la borne `max_files` n'y peut rien | relecture | **ouvert**, dit dans le message d'execd ; à mettre dans la doc |
| 13 | Copier dans le même lot d'appels que l'outil qui produit le fichier : « inconnu » (les fichiers vus sont ceux du début du lot) ; un petit-enfant ne fait pas remonter ses fichiers au grand-parent ; sans stockage d'artefacts, le forge rapatrie pour rien et `file` reste proposé | relecture | **ouvert**, mineur |

## Effets de bord sur le reste de loom

### 1. Les requêtes changent — ⚠ CHANGELOG
- **Outil `forge`** (seulement s'il est proposé : ton chef l'exclut, le forgeron l'a) : +376 caractères de description.
- **`local_files`** : +190 caractères de description, et +231 caractères de schéma (`file`).

**Conséquences :**
- Les runs journalisés avant le changement par un agent qui propose l'un de ces outils divergent au rejeu identique, sur « les outils proposés ».
- Le cache de prompt est réécrit une fois.
- Tout run d'un sous-agent qui a produit des fichiers montre désormais ces fichiers au parent : les nouveaux runs seulement, le rejeu des anciens sert ce qui est journalisé.

### 2. Comportement du moteur pour tous les outils
- **Images refusées :** une image d'outil refusée ne fait plus échouer le run ; elle devient une mention.
- **Sous-agents :** les fichiers produits par un sous-agent (`Image` d'un outil Python, image MCP, fichier forgé) arrivent chez le parent. Avec un parent vision et `keep_images: all`, ses images partent en octets à chaque appel suivant, comme s'il avait appelé l'outil lui-même : coût et fenêtre de contexte (D-5).

### 3. Délai des outils forgés
`call` et chaque outil forgé gagnent 30 s de délai : 60 de démarrage + (`wall_ms` + 5) + 30 de marge + 30 de transfert. Pour ton `wall_ms: 9000`, cela fait 134 s au lieu de 104. `forge` est inchangé.

### 4. Mémoire et disque
- **Mémoire :** le pic est d'environ deux fois le fichier rapatrié, car `get_file` copie son tampon. Comme le verrou sérialise les appels du run, le pire cas est d'environ 2 × `max_total_bytes` par run qui rapatrie : 64 Mo avec les bornes par défaut.
- **Disque :** le stockage grossit de 32 Mo au plus par appel, sans quota par session. L'adressage par contenu ne range qu'une fois un fichier identique dans une session. La suppression d'une session et `loom retention` effacent ces fichiers avec le journal.

### 5. Frontière du bac à sable — D-7
Un fichier binaire produit dans la VM peut désormais atterrir sur le disque de l'hôte, par `local_files`, avec le seul droit `write` du dossier. Écrire n'exécute rien, mais c'est un franchissement nouveau. Si un dossier mérite plus de prudence, la configuration permet déjà `approval: {write: always}`.

### 6. Rejeu, reprise, déport
- **Rejeu :** les fichiers sont rangés avant `tool.completed`. Le journal ne porte que les références, ce que le test de bout en bout vérifie. Le rejeu sert le résultat journalisé, sans VM ni copie.
- **Reprise :** une copie refaite trouve le fichier identique et n'écrit rien.
- **Déport (au-delà de 50 000 caractères) :** il garde les références après l'aperçu.

### 7. Ce qui ne change pas
- Les contrats d'import ;
- les URI et le format du journal ;
- le stockage d'artefacts et son sceau ;
- la CLI et le REST des fichiers ;
- `local_files` `read`, `list`, `delete` ;
- `forge` et ses exemples ;
- un outil Python qui rend une `Image` ;
- un agent sans stockage d'artefacts (« non conservé », comme aujourd'hui).

### 8. Ton agent `temp/agent`
- **Rien à configurer :** le stockage suit déjà le journal.
- **Modèles sans vision :** Haiku, Sonnet et Opus n'ont pas `vision` dans ton `loom.yaml`. Ils verront `[fichier graphe.svg …]`, jamais d'octets d'image ; les points 10 et 11 ne te touchent pas.
- **Le chef appelle les outils forgés lui-même** (`forge__call` et les outils du catalogue). Le fichier lui arrive donc directement, et il le range avec `local_files` + `file`.
- **Le prompt du forgeron** (`forgeron.md`) gagnerait à reprendre la règle de la description : écrire le document dans `OUT_DIR`, ne rendre qu'une valeur courte.

## Mesures

execd réel en socket Unix sur l'espace de travail cloud, session déjà ouverte, médiane non calculée (un passage par cas : ordres de grandeur). Le vsock de la VM n'est pas mesuré.

| Fichiers produits | Appel sans rapatriement | Avec | Surcoût | Pic mémoire Python de l'appel |
| --- | --- | --- | --- | --- |
| 1 × 1 Mo | 111 ms | 118 ms | +7 ms | 3 Mo |
| 1 × 16 Mo | 249 ms | 345 ms | +96 ms | 34 Mo |
| 4 × 16 Mo | 694 ms | 1 091 ms | +398 ms | 82 Mo |
| 1 × 64 Mo | 660 ms | 605 ms | dans le bruit | 129 Mo |

Le script est `mesure.py`, dans l'espace de travail du prototype.

## Tests écrits sur le prototype (41)

**`tests/firecracker/test_firecracker_forge.py`** (+25) :
- **avec une session simulée :**
  - fichiers rendus en blocs, dans l'ordre des chemins ;
  - faux PNG ;
  - bornes appliquées avant tout transfert ;
  - fichier changé depuis le manifeste ;
  - refus d'execd ;
  - session coupée en route (résultat gardé) ;
  - échéance du transfert ;
  - manifeste hostile ;
  - liste bornée ;
  - liens ;
  - exemples et échecs sans rapatriement ;
  - phrase de description ;
  - paramètres `files` (7 cas) ;
- **avec execd réel :**
  - fichier écrit dans la VM puis rapatrié ;
  - trois appels parallèles, chacun avec ses fichiers ;
  - lien symbolique dans `out/` non suivi ;
- **monté par loom :**
  - stockage, URI journalisée, octets absents du journal, liste et téléchargement par `session_files`, rejeu identique ;
  - le chemin complet chef → forgeron → VM → dossier.

**`tests/unit/test_local_files.py`** (+12) :
- copie (le plus récent du nom, binaire, nom dans le stockage) ;
- six refus ;
- sans stockage ;
- taille et fichier absent ;
- autre client ou autre session ignorés ;
- `assess_call` d'une copie ;
- copie refaite après une reprise.

**`tests/unit/test_vision.py`** (+2) : image d'outil refusée (format, taille), qui devient une mention.

**`tests/unit/test_subagents.py`** (+2, et une assertion de plus) :
- le parent voit le fichier de l'enfant ;
- un enfant qui n'a produit que des fichiers a bien répondu.

## Décisions à prendre

| # | Question | Options | Recommandation |
| --- | --- | --- | --- |
| D-1 | Rapatriement par défaut ? | (a) actif sans config ; (b) seulement avec `files:` | **(a)** : sans lui, la phrase sur `OUT_DIR` n'existe pas et le modèle continue de tout faire passer par la valeur rendue |
| D-2 | Bornes par défaut | 10 fichiers, 16 Mo par fichier, 32 Mo par appel ; plafonds 512, 64 Mo, 256 Mo | **garder** ; 16 Mo est aussi la borne de `get_file` aujourd'hui |
| D-3 | Image d'outil refusée par le modèle | (a) mention qui dit pourquoi ; (b) erreur comme aujourd'hui | **(a)**, pour tous les outils ; pièces jointes inchangées |
| D-4 | Modèle vision sans `max_image_bytes` | (a) le documenter et le déclarer dans `loom.yaml` ; (b) un plafond par défaut de 5 Mo pour les images d'outils | **(a)** maintenant, (b) si un modèle vision entre dans tes agents |
| D-5 | Fichiers d'un sous-agent chez le parent | (a) références : mention, ou image si le parent a la vision ; (b) mention texte seulement | **(a)** : c'est ce qui permet au parent de les livrer ; le coût ne touche qu'un parent vision |
| D-6 | Où livrer dans un dossier | (a) `write` + `file` dans `local_files` ; (b) une action `copy` ; (c) un outil `loom.deliver` à part | **(a)** : mêmes droits, même approbation, même écriture atomique, un seul argument de plus |
| D-7 | Approbation d'une copie | (a) celle de `write` ; (b) toujours demander pour `file` | **(a)** ; un dossier sensible se règle déjà par `approval: {write: always}` |
| D-8 | Nom porté par plusieurs fichiers | (a) le plus récent ; (b) refuser et lister les versions | **(a)**, c'est ce qu'on entend par « le graphe » ; (b) si un cas réel trompe le modèle |
| D-9 | Version et annonce | ligne ⚠ dans le CHANGELOG (rejeu non identique, cache, fichiers des sous-agents, images refusées) | **oui** |

## Plan de mise en œuvre

Une fois les décisions prises, partir du prototype (`fb3a150`, dont les décisions par défaut sont celles de la colonne « Recommandation »), puis :
1. **Documentation :**
   - `docs/fonctions.md` : param `files` du forge, `local_files` `file`, et l'image refusée qui devient une mention (l. 587) ;
   - `docs/conception.md` (l. 859) ;
   - la docstring d'`engine/media.py` (l. 11) ;
   - `docs/howto/06-outils-fournis.md` (copie d'un fichier) ;
   - la limite `out_files` d'execd (piège 12).
2. **CHANGELOG :** section 2.0.16 avec ⚠ (D-9).
3. **Optionnel, mineur :** éviter la copie du tampon dans `Session.get_file` (pic mémoire ×2) ; dire dans la description de `local_files` qu'un fichier se copie au tour suivant celui qui le produit (piège 13).
4. **Dans `temp/agent`, sur ta demande :** une ligne dans `forgeron.md` (le document dans `OUT_DIR`, une valeur courte en retour). Puis refaire la demande de `rapport-01` pour comparer les tokens.

## Décisions prises (27/10)

Toutes suivent la recommandation : D-1 (a) rapatriement actif par défaut ; D-2 bornes 10 / 16 Mo / 32 Mo, plafonds 512 / 64 Mo / 256 Mo ; D-3 (a) image d'outil refusée → mention ; D-4 (a) `max_image_bytes` à déclarer, documenté ; D-5 (a) références de l'enfant chez le parent ; D-6 (a) `write` + `file` ; D-7 (a) approbation de `write` ; D-8 (a) le plus récent ; D-9 ligne ⚠ au CHANGELOG.

Deux écarts au plan :
- **CHANGELOG** : les lignes vont dans la section **2.0.15**, encore « non publiée », plutôt que dans une 2.0.16.
- **Copie du tampon de `Session.get_file`** (point 3 du plan) : non faite. `InlineDataBlock.data` est un `bytes`, et toute conversion depuis le tampon de lecture recopie ; l'éviter demanderait de changer le type du bloc pour tous les outils. Le pic reste d'environ deux fois le fichier (§ Mesures), borné par `max_total_bytes`.

## Réalisation (27/10)

Le code est celui du prototype (`fb3a150`), plus :
- `engine/media.py` : la docstring dit qu'une image d'outil refusée devient une mention ;
- `local_files` : sa description dit qu'un fichier rendu par un outil se copie au tour suivant, pas dans le même lot (piège 13).

Documentation :
- `CHANGELOG.md`, section 2.0.15 : `local_files` copie un fichier ; nouvelle section « Sandbox `forge` et fichiers » (rapatriement, puis trois lignes ⚠ : requêtes qui changent, fichiers d'un sous-agent chez le parent, images d'outils refusées) ;
- `docs/fonctions.md` : D9, D13, G3, réalisation 6.4c (param `files`, délai, paragraphe « Fichiers produits », limite `out_files` d'execd, « reste connu » mis à jour), images refusées (§ 14) ;
- `docs/conception.md` : images refusées ;
- guide, chapitre 32 (`docs/howto/06-outils-fournis.md`) : « Copier un fichier de la conversation » ; chapitre 29.2 (`docs/howto/04-qualite-et-expert.md`) : les fichiers d'un outil forgé.

Contrôles sur la version livrée : `pytest` 3 577 passés, 203 sautés (référence `f413ee5` : 3 536) ; `pyright` strict 0 ; `ruff check` et `ruff format --check` propres ; `lint-imports` 5 contrats tenus.

Rien n'a changé dans `temp/agent` : la ligne pour `forgeron.md` reste à faire, sur ta demande.
