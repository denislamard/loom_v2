# SPDX-License-Identifier: Apache-2.0
"""Phase 6.4a : ``loom serve --reload`` — le serveur relancé quand un fichier de la config change.

    uv run --extra http python examples/j6/rechargement.py                  # tous les cas
    uv run --extra http python examples/j6/rechargement.py --cas prompt
    uv run --extra http python examples/j6/rechargement.py --cas agent
    uv run --extra http python examples/j6/rechargement.py --cas casse
    uv run --extra http python examples/j6/rechargement.py --cas donnees
    uv run --extra http python examples/j6/rechargement.py --cas prod
    uv run --env-file .env --extra http --extra anthropic --extra openai \\
        python examples/j6/rechargement.py --reel

Config : celle de J4 (``examples/j4/relance/``), **copiée** dans un dossier
temporaire à chaque cas — l'exemple y modifie des fichiers, et le serveur y
écrit son journal. Les fichiers de ``relance/`` ne changent pas. Chaque cas
lance ``loom serve --reload`` dans un sous-process, sur un port libre, lit sa
console et lui parle en REST. En simulé, le serveur ne reçoit aucune clé
d'API : rien d'autre que des modèles simulés ne peut tourner. En ``--reel``,
les runs sont ceux de ``relance_reel``.

À chaque changement, un **nouveau** process charge la config et monte chaque
agent pendant que l'ancien sert encore ; s'il échoue, l'ancien continue ;
sinon il prend la main, et l'ancien finit ce qu'il a en cours avant de sortir
(un run en vol qui s'achève dans l'ancien process est éprouvé par
``tests/integration/test_rechargement_process.py``).

* **prompt** : le prompt du rôle qui rédige l'e-mail (``rediger_relance``)
  reçoit une consigne de plus ; le run suivant la lui transmet. Le rôle est
  terminal : son e-mail est le texte que le run rend, c'est donc là que la
  consigne peut se voir. Ce qui est parti vers son modèle se lit au journal,
  par ``GET /v1/events`` : la copie capture les échanges bruts
  (``telemetry.capture.raw_exchanges``) — en simulé, le modèle y dépose la
  requête qu'il a reçue. En ``--reel``, que le modèle suive la consigne est
  montré, pas exigé.
* **agent** : un fichier d'agent ajouté, et l'agent est servi ; retiré, il ne
  l'est plus.
* **casse** : trois façons de casser la config — un YAML invalide, un outil
  introuvable, une erreur de syntaxe dans un module voisin. Chaque fois le
  rechargement est refusé, en disant pourquoi, et l'ancien process continue
  de servir ; la correction est servie.
* **donnees** : ce que les runs écrivent (journal, base d'idempotence) et ce
  que l'import des voisins écrit (``__pycache__``) ne relance rien. Un prompt
  touché ensuite recharge : le guetteur était bien là.
* **prod** : ``--reload`` est refusé en profil ``prod``, au lancement comme
  quand la config passe en ``prod`` en cours de route.
"""

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from importlib.util import find_spec
from pathlib import Path
from typing import Any

from loom_ia.config import ConfigError, LoomConfig, load_config

J4 = Path(__file__).parent.parent / "j4" / "relance"
CAS = ("prompt", "agent", "casse", "donnees", "prod")
LANCEUR = "import sys; from loom_ia.access.cli import main; sys.exit(main(sys.argv[1:]))"
DEMANDE = "Relance le client du devis D-2026-042."
# Le rôle qui rédige l'e-mail (cas prompt), ce que son prompt reçoit de plus,
# et la marque qui reconnaît la consigne dans une requête : en ASCII, parce
# qu'un corps de requête réel peut échapper « » et é.
ROLE = "rediger_relance"
CONSIGNE = "Termine le corps de l'e-mail par la ligne « Relu par loom. »"
MARQUE = "Relu par loom."
# Un démarrage de process sur une machine chargée ; un run réel.
DELAI = 60.0
# Plus que l'attente de regroupement des changements (1,6 s) : ce qui n'a pas
# relancé après ça ne relancera pas.
CALME = 3.0


def shown(path: Path) -> str:
    return os.path.relpath(path)


def titre(texte: str) -> None:
    print(f"\n=== {texte} ===")


class Controle:
    """Ce que chaque essai devait rendre : l'exemple l'annonce, puis le vérifie."""

    def __init__(self) -> None:
        self.ecarts: list[str] = []
        self.sautes: list[str] = []
        self.parties: list[str] = []

    def tient(self, quoi: str, vrai: bool) -> str:
        if not vrai:
            self.ecarts.append(quoi)
        return "oui" if vrai else "NON"

    def saute(self, quoi: str, *, partie: bool = False) -> None:
        """Un cas, ou une partie d'un cas, qui n'a pas pu être jouée : à dire,
        sinon le bilan mentirait. Une partie sautée laisse le cas joué."""
        (self.parties if partie else self.sautes).append(quoi)

    def bilan(self, joues: int) -> bool | None:
        """Vrai si tout a tenu, faux sinon ; ``None`` si aucun cas n'a été joué."""
        for saute in self.sautes:
            print(f"\nCas sauté : {saute}")
        for partie in self.parties:
            print(f"\nPartie sautée : {partie}")
        if joues == len(self.sautes):
            print("\nAucun cas joué : l'exemple n'a rien éprouvé.")
            return None
        if not self.ecarts:
            sautes = self.sautes or self.parties
            dit = "Chaque essai joué a rendu" if sautes else "Chaque essai a rendu"
            print(f"\n{dit} ce que l'exemple annonçait.")
            return True
        print("\nUn essai au moins n'a pas rendu ce qui était annoncé :")
        for ecart in self.ecarts:
            print(f"  {ecart}")
        return False


def enonce(quoi: str, texte: str, largeur: int = 96) -> None:
    """Imprime un message long sur plusieurs lignes, sans rien en couper."""
    marge = " " * len(quoi)
    lignes = [
        morceau
        for bloc in texte.splitlines()
        for morceau in textwrap.wrap(bloc, largeur - len(quoi)) or [""]
    ]
    for numero, ligne in enumerate(lignes or [""]):
        print(f"{quoi if numero == 0 else marge}{ligne}")


# --- La copie de la config, et son serveur ----------------------------------------------


def copie(dossier: Path) -> Path:
    """La config de J4 copiée dans ``dossier``, sans ses données ; elle capture les échanges."""
    cible = dossier / "relance"
    shutil.copytree(J4, cible, ignore=shutil.ignore_patterns("data", "__pycache__"))
    fichier = cible / "loom.yaml"
    texte = fichier.read_text(encoding="utf-8")
    avant = "  logging: {level: WARNING, format: console}\n"
    if avant not in texte:
        raise ConfigError(f"{shown(fichier)} : la ligne de journalisation attendue a changé")
    fichier.write_text(
        texte.replace(avant, f"{avant}  capture: {{raw_exchanges: true}}\n"), encoding="utf-8"
    )
    return fichier


def sans_cles(config: LoomConfig) -> dict[str, str]:
    """L'environnement, moins toutes les clés d'API que la config nomme."""
    noms = {spec.api_key_env for spec in config.models if spec.api_key_env}
    return {nom: valeur for nom, valeur in os.environ.items() if nom not in noms}


def port_libre() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def lancer(
    fichier: Path, console: Path, environ: dict[str, str], *options: str
) -> tuple[subprocess.Popen[bytes], int]:
    """``loom serve --reload`` sur la copie, sa console dans ``console`` ; rend le process et son
    port."""
    port = port_libre()
    sortie = console.open("ab")
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            LANCEUR,
            "--config",
            str(fichier),
            *options,
            "serve",
            "--reload",
            "--port",
            str(port),
        ],
        stdout=sortie,
        stderr=subprocess.STDOUT,
        env=environ,
    )
    sortie.close()
    return process, port


def lue(console: Path) -> str:
    return console.read_text(encoding="utf-8") if console.exists() else ""


def appel(port: int, methode: str, chemin: str, corps: Any = None) -> tuple[int, Any]:
    """Une requête REST, sans passer par un proxy : le serveur est sur la machine."""
    data = None if corps is None else json.dumps(corps).encode()
    requete = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1{chemin}", data=data, method=methode
    )
    if data is not None:
        requete.add_header("content-type", "application/json")
    ouvreur = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with ouvreur.open(requete, timeout=DELAI) as reponse:
            return reponse.status, json.loads(reponse.read() or b"null")
    except urllib.error.HTTPError as erreur:
        return erreur.code, json.loads(erreur.read() or b"null")


def repond(port: int) -> bool:
    try:
        return appel(port, "GET", "/agents")[0] == 200
    except urllib.error.URLError, ConnectionError, TimeoutError:
        return False


def agents(port: int) -> dict[str, str]:
    code, liste = appel(port, "GET", "/agents")
    return {a["name"]: a["description"] for a in liste} if code == 200 else {}


def attendre(quoi: Callable[[], bool], process: subprocess.Popen[bytes]) -> bool:
    """Vrai dès que ``quoi`` l'est ; faux au bout du délai, ou si le superviseur est sorti."""
    limite = time.monotonic() + DELAI
    while time.monotonic() < limite:
        if quoi():
            return True
        if process.poll() is not None:
            return quoi()
        time.sleep(0.1)
    return False


def compte(console: Path, ligne: str) -> int:
    return sum(1 for dite in lue(console).splitlines() if dite.startswith(ligne))


def releve(
    console: Path, process: subprocess.Popen[bytes], changer: Callable[[], object]
) -> tuple[str, str]:
    """Fait un changement et attend que le superviseur ait tranché ; rend son verdict et ce qu'il
    a dit.

    Le verdict : ``recharge``, ``refuse``, ou ``rien`` si rien n'est venu dans le délai.
    """
    avant = len(lue(console))
    faits, refuses = compte(console, "Rechargé"), compte(console, "Rechargement refusé")
    changer()
    tranche = attendre(
        lambda: (
            compte(console, "Rechargé") > faits or compte(console, "Rechargement refusé") > refuses
        ),
        process,
    )
    dit = lue(console)[avant:]
    if not tranche:
        return "rien", dit
    return ("recharge" if compte(console, "Rechargé") > faits else "refuse"), dit


def montre(dit: str) -> None:
    """Ce que la console a dit du rechargement — le bandeau du nouveau process compris."""
    for ligne in dit.splitlines():
        print(f"  │ {ligne}")


def arreter(process: subprocess.Popen[bytes], port: int, controle: Controle, cas: str) -> None:
    if process.poll() is None:
        process.send_signal(signal.SIGINT)
    try:
        code = process.wait(timeout=DELAI)
    except subprocess.TimeoutExpired:
        process.kill()
        code = process.wait()
    titre("Ctrl+C")
    print(
        f"  le superviseur s'arrête (code {code}), et plus aucun process ne répond : "
        + controle.tient(
            f"{cas} : l'arrêt laisse un process ou rend {code}", code == 0 and not repond(port)
        )
    )


def lance_run(port: int, agent: str) -> dict[str, Any]:
    code, corps = appel(port, "POST", f"/agents/{agent}/runs", {"message": DEMANDE})
    return corps if code in (200, 201) else {"status": f"HTTP {code}", "error": corps}


def requetes(port: int, run_id: str, modele: str) -> list[str]:
    """Les corps de requête que le modèle ``modele`` a reçus pendant le run, lus au journal.

    ``modele`` est le nom du modèle chez son fournisseur (``model`` dans la
    config) : c'est lui que l'échange porte.
    """
    code, events = appel(
        port, "GET", f"/events?run_id={run_id}&type=model.exchanged&model_id={modele}"
    )
    return [e["payload"]["request_body"] for e in events] if code == 200 else []


# --- Les cas ------------------------------------------------------------------------------


def cas_prompt(args: argparse.Namespace, dossier: Path, controle: Controle) -> None:
    fichier = copie(dossier)
    config = load_config(fichier)
    agent = "relance_reel" if args.reel else "relance"
    spec = next(spec for spec in config.agents if spec.name == agent)
    role = next(role for role in spec.roles if role.name == ROLE)
    redacteur = config.model_spec(role.model)
    modele = redacteur.model
    console = dossier / "console.log"
    process, port = lancer(fichier, console, environ(args, config))
    print(f"  config : copie de {shown(J4)}, échanges bruts capturés")
    terminal = "terminal" if role.terminal else "non terminal"
    print(f"  agent  : {agent} (rôle {ROLE}, {terminal} : {redacteur.id}, {modele})")
    try:
        if not _pret(process, port, console, controle, "prompt"):
            return
        premier = lance_run(port, agent)
        titre(f"Un premier run : {DEMANDE}")
        _run(premier, controle, "prompt", exige=not args.reel)
        avant = requetes(port, premier.get("run_id", ""), modele)

        prompt = fichier.parent / "prompts" / "rediger.md"
        titre(f"La consigne ajoutée à {prompt.relative_to(fichier.parent)}")
        enonce("  + ", CONSIGNE)
        verdict, dit = releve(
            console,
            process,
            lambda: prompt.write_text(
                f"{prompt.read_text(encoding='utf-8')}\n{CONSIGNE}\n", encoding="utf-8"
            ),
        )
        montre(dit)
        print(
            "  rechargé, et la console nomme le fichier : "
            + controle.tient(
                f"prompt : rechargement {verdict}",
                verdict == "recharge" and "prompts/rediger.md modifié" in dit,
            )
        )
        second = lance_run(port, agent)
        titre("Un second run, servi par le nouveau process")
        _run(second, controle, "prompt", exige=not args.reel)
        apres = requetes(port, second.get("run_id", ""), modele)

        titre(f"Ce qui est parti vers {modele}, lu au journal (model.exchanged)")
        print(f"  premier run : {len(avant)} requête(s), consigne présente : {_dans(avant)}")
        print(f"  second run  : {len(apres)} requête(s), consigne présente : {_dans(apres)}")
        if args.reel and not (avant and apres):
            # Un vrai orchestrateur peut ne pas appeler le rôle : il n'y a alors
            # rien à lire, et rien à exiger.
            print(f"  un run n'a pas appelé {ROLE} : ce qu'il aurait reçu ne se lit pas")
            controle.saute(
                f"prompt (en --reel, un run n'a pas appelé {ROLE} : la consigne n'a pas été lue)",
                partie=True,
            )
        else:
            print(
                "  les requêtes du premier run ne la portent pas, "
                "toutes celles du second la portent : "
                + controle.tient(
                    "prompt : la consigne n'est pas arrivée au modèle comme annoncé",
                    bool(avant)
                    and bool(apres)
                    and not any(MARQUE in r for r in avant)
                    and all(MARQUE in r for r in apres),
                )
            )
        if args.reel and apres:
            controle.saute(
                "prompt (en --reel, que le modèle suive la consigne est montré, pas exigé)",
                partie=True,
            )
            suivie = MARQUE in str(second.get("text") or "")
            print(
                "  le modèle a suivi la consigne (montré, pas exigé) : "
                + ("oui" if suivie else "non")
            )
    finally:
        arreter(process, port, controle, "prompt")


def cas_agent(args: argparse.Namespace, dossier: Path, controle: Controle) -> None:
    fichier = copie(dossier)
    config = load_config(fichier)
    source = "relance_reel" if args.reel else "relance"
    nouveau = f"{source}_bis"
    console = dossier / "console.log"
    process, port = lancer(fichier, console, environ(args, config))
    print(f"  config : copie de {shown(J4)} ; l'agent ajouté copie {source}")
    try:
        if not _pret(process, port, console, controle, "agent"):
            return
        departs = agents(port)
        print(f"  servis au départ : {', '.join(sorted(departs))}")

        ajout = fichier.parent / "agents" / f"{nouveau}.yaml"
        texte = (fichier.parent / "agents" / f"{source}.yaml").read_text(encoding="utf-8")
        titre(f"Un fichier d'agent ajouté : {ajout.relative_to(fichier.parent)}")
        verdict, dit = releve(
            console,
            process,
            lambda: ajout.write_text(
                texte.replace(f"name: {source}\n", f"name: {nouveau}\n", 1).replace(
                    "description: ", "description: (copie) ", 1
                ),
                encoding="utf-8",
            ),
        )
        montre(dit)
        servis = agents(port)
        print(
            f"  rechargé, et {nouveau} est servi ({servis.get(nouveau, 'absent')}) : "
            + controle.tient(
                f"agent : {nouveau} n'est pas servi après l'ajout ({verdict})",
                verdict == "recharge"
                and f"agents/{nouveau}.yaml ajouté" in dit
                and set(servis) == {*departs, nouveau}
                and servis[nouveau].startswith("(copie) "),
            )
        )
        run = lance_run(port, nouveau)
        titre(f"Un run de {nouveau}")
        _run(run, controle, "agent", exige=not args.reel)
        print(
            f"  le run est celui de {nouveau} : "
            + controle.tient(f"agent : le run n'est pas de {nouveau}", run.get("agent") == nouveau)
        )

        titre("Le fichier retiré")
        verdict, dit = releve(console, process, ajout.unlink)
        montre(dit)
        code, _ = appel(port, "POST", f"/agents/{nouveau}/runs", {"message": DEMANDE})
        print(
            f"  rechargé, {nouveau} n'est plus servi (lancement : HTTP {code}) : "
            + controle.tient(
                f"agent : {nouveau} est encore servi après son retrait ({verdict})",
                verdict == "recharge"
                and f"agents/{nouveau}.yaml supprimé" in dit
                and set(agents(port)) == set(departs)
                and code == 404,
            )
        )
    finally:
        arreter(process, port, controle, "agent")


def cas_casse(args: argparse.Namespace, dossier: Path, controle: Controle) -> None:
    fichier = copie(dossier)
    config = load_config(fichier)
    agent = "relance_reel" if args.reel else "relance"
    console = dossier / "console.log"
    process, port = lancer(fichier, console, environ(args, config))
    racine = fichier.parent
    print(f"  config : copie de {shown(J4)}")
    casses: list[tuple[str, Path, Callable[[str], str], str]] = [
        (
            "un YAML invalide",
            fichier,
            lambda texte: f"{texte}  - oups: [\n",
            "YAML invalide",
        ),
        (
            "un outil introuvable",
            racine / "agents" / f"{agent}.yaml",
            lambda texte: texte.replace("python: chercher_devis", "python: chercher_devis_absent"),
            "Référence 'chercher_devis_absent' introuvable",
        ),
        (
            "une erreur de syntaxe dans un module voisin",
            racine / "outils.py",
            lambda texte: f"{texte}\ndef (:\n",
            "SyntaxError",
        ),
    ]
    try:
        if not _pret(process, port, console, controle, "casse"):
            return
        departs = agents(port)
        for numero, (quoi, cible, casser, raison) in enumerate(casses):
            bon = cible.read_text(encoding="utf-8")
            casse_ = casser(bon)
            titre(f"Cassé : {quoi} ({cible.relative_to(racine)})")
            if casse_ == bon:
                print("  la retouche n'a rien changé : la config a changé sous l'exemple")
                controle.tient(f"casse : {quoi} — retouche sans effet", False)
                continue
            verdict, dit = releve(
                console,
                process,
                lambda cible=cible, casse_=casse_: _ecrit(cible, casse_),
            )
            montre(dit)
            print(
                "  refusé, en disant pourquoi : "
                + controle.tient(
                    f"casse : {quoi} — rechargement {verdict}, raison attendue « {raison} »",
                    verdict == "refuse" and raison in dit,
                )
            )
            if quoi.startswith("une erreur de syntaxe"):
                print(
                    "  la trace montre la ligne du module, sans la pile de loom : "
                    + controle.tient(
                        "casse : la trace d'une erreur de syntaxe n'est pas réduite",
                        "outils.py" in dit and "Traceback" not in dit,
                    )
                )
            servis = agents(port)
            print(
                "  l'ancien process sert encore, la même config : "
                + controle.tient(f"casse : {quoi} — l'ancien ne sert plus", servis == departs)
            )
            if numero == 0:
                run = lance_run(port, agent)
                print("  un run pendant que c'est cassé :")
                _run(run, controle, "casse", exige=not args.reel)
            verdict, dit = releve(console, process, lambda cible=cible, bon=bon: _ecrit(cible, bon))
            print(
                "  corrigé : rechargé : "
                + controle.tient(
                    f"casse : {quoi} — la correction n'est pas servie ({verdict})",
                    verdict == "recharge",
                )
            )
    finally:
        arreter(process, port, controle, "casse")


def cas_donnees(args: argparse.Namespace, dossier: Path, controle: Controle) -> None:
    fichier = copie(dossier)
    config = load_config(fichier)
    agent = "relance_reel" if args.reel else "relance"
    racine = fichier.parent
    console = dossier / "console.log"
    depart = _etat(racine)
    process, port = lancer(fichier, console, environ(args, config))
    print(f"  config : copie de {shown(J4)}, journal en JSONL sous data/")
    try:
        if not _pret(process, port, console, controle, "donnees"):
            return
        print("  la console dit ce qui est surveillé et ce qui ne l'est pas :")
        montre(
            "\n".join(
                ligne
                for ligne in lue(console).splitlines()
                if ligne.startswith("Rechargement : surveille")
            )
        )
        nombre = 1 if args.reel else 2
        titre(f"{nombre} run(s) de {agent}, puis {CALME:.0f} s d'attente")
        for _ in range(nombre):
            _run(lance_run(port, agent), controle, "donnees", exige=not args.reel)
        time.sleep(CALME)
        ecrits = sorted(p for p, mtime in _etat(racine).items() if depart.get(p) != mtime)
        print(f"  {len(ecrits)} fichier(s) écrit(s) depuis le lancement :")
        for chemin in ecrits:
            print(f"    {chemin.relative_to(racine)}")
        donnees = [p for p in ecrits if p.is_relative_to(racine / "data")]
        caches = [p for p in ecrits if "__pycache__" in p.parts]
        print(
            "  le journal (sous data/) et les caches d'import (__pycache__), rien d'autre : "
            + controle.tient(
                "donnees : les écritures ne sont pas celles annoncées",
                bool(donnees) and bool(caches) and len(donnees) + len(caches) == len(ecrits),
            )
        )
        print(
            "  aucun rechargement : "
            + controle.tient(
                "donnees : une écriture de loom a relancé le serveur",
                compte(console, "Rechargement : ") == 1,
            )
        )
        rediger = racine / "prompts" / "rediger.md"
        titre(f"Puis un prompt touché : {rediger.relative_to(racine)}")
        verdict, dit = releve(
            console,
            process,
            lambda: rediger.write_text(
                f"{rediger.read_text(encoding='utf-8')}\n", encoding="utf-8"
            ),
        )
        montre(dit)
        print(
            "  rechargé : le guetteur était là, il a laissé passer les données : "
            + controle.tient(
                f"donnees : le prompt touché n'a pas rechargé ({verdict})", verdict == "recharge"
            )
        )
    finally:
        arreter(process, port, controle, "donnees")


def cas_prod(args: argparse.Namespace, dossier: Path, controle: Controle) -> None:
    fichier = copie(dossier)
    config = load_config(fichier)
    console = dossier / "console.log"
    titre("loom --profile prod serve --reload")
    process, port = lancer(fichier, console, environ(args, config), "--profile", "prod")
    try:
        code = process.wait(timeout=DELAI)
    except subprocess.TimeoutExpired:
        process.kill()
        code = process.wait()
    dit = lue(console)
    montre(dit)
    print(
        f"  refusé au lancement (code {code}), et rien ne répond : "
        + controle.tient(
            "prod : --reload n'est pas refusé au lancement",
            code == 2 and "refusé en profil prod" in dit and not repond(port),
        )
    )

    titre("La config qui passe en prod pendant que le serveur tourne")
    console = dossier / "console-2.log"
    process, port = lancer(fichier, console, environ(args, config))
    try:
        if not _pret(process, port, console, controle, "prod"):
            return
        departs = agents(port)
        bon = fichier.read_text(encoding="utf-8")
        verdict, dit = releve(
            console, process, lambda: fichier.write_text(f"profile: prod\n{bon}", encoding="utf-8")
        )
        montre(dit)
        print(
            "  refusé, et l'ancien process sert encore : "
            + controle.tient(
                f"prod : le passage en prod n'est pas refusé ({verdict})",
                verdict == "refuse" and "refusé en profil prod" in dit and agents(port) == departs,
            )
        )
        verdict, _ = releve(console, process, lambda: fichier.write_text(bon, encoding="utf-8"))
        print(
            "  la ligne retirée : rechargé : "
            + controle.tient(
                f"prod : la config rétablie n'est pas servie ({verdict})", verdict == "recharge"
            )
        )
    finally:
        arreter(process, port, controle, "prod")


# --- Aides des cas ----------------------------------------------------------------------


def environ(args: argparse.Namespace, config: LoomConfig) -> dict[str, str]:
    """En simulé, aucune clé : le serveur ne peut faire tourner que des modèles simulés."""
    return dict(os.environ) if args.reel else sans_cles(config)


def _pret(
    process: subprocess.Popen[bytes], port: int, console: Path, controle: Controle, cas: str
) -> bool:
    titre("loom serve --reload")
    pret = attendre(lambda: repond(port) and "Rechargement : surveille" in lue(console), process)
    montre(lue(console))
    print(
        f"  le serveur répond sur le port {port} : "
        + controle.tient(f"{cas} : le serveur n'a pas démarré", pret)
    )
    return pret


def _run(run: dict[str, Any], controle: Controle, cas: str, *, exige: bool) -> None:
    statut = str(run.get("status"))
    erreur = f" ({run['error']})" if run.get("error") else ""
    print(f"  statut : {statut}{erreur}")
    enonce("  texte  : ", str(run.get("text") or "(vide)"))
    if not exige:
        # Un vrai modèle peut échouer pour ses raisons : le rechargement n'en dit
        # rien, et le bilan doit dire ce qui n'a pas été exigé.
        quoi = f"{cas} (en --reel, ce que les runs rendent est montré, pas exigé)"
        if quoi not in controle.parties:
            controle.saute(quoi, partie=True)
        return
    print(
        "  le run est allé au bout : "
        + controle.tient(f"{cas} : un run n'est pas allé au bout ({statut})", statut == "completed")
    )


def _ecrit(cible: Path, texte: str) -> None:
    cible.write_text(texte, encoding="utf-8")


def _dans(corps: list[str]) -> str:
    if not corps:
        return "aucune requête"
    presentes = sum(1 for r in corps if MARQUE in r)
    return f"{presentes} sur {len(corps)}"


def _etat(racine: Path) -> dict[Path, float]:
    return {p: p.stat().st_mtime for p in racine.rglob("*") if p.is_file()}


def _cles(config: LoomConfig) -> list[str]:
    """Les variables de clé des modèles réels de la config qui manquent à l'environnement."""
    noms = {spec.api_key_env for spec in config.models if spec.sdk != "fake" and spec.api_key_env}
    return sorted(nom for nom in noms if not os.environ.get(nom))


# --- Lancement --------------------------------------------------------------------------


JOUES: dict[str, Callable[[argparse.Namespace, Path, Controle], None]] = {
    "prompt": cas_prompt,
    "agent": cas_agent,
    "casse": cas_casse,
    "donnees": cas_donnees,
    "prod": cas_prod,
}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="loom serve --reload : la config rechargée")
    parser.add_argument("--reel", action="store_true", help="vrais modèles (relance_reel)")
    parser.add_argument("--cas", action="append", choices=CAS, help="cas à jouer (tous par défaut)")
    args = parser.parse_args(argv)
    cas = tuple(args.cas) if args.cas else CAS
    if find_spec("fastapi") is None or find_spec("watchfiles") is None:
        print(
            "Extra manquant : l'exemple lance loom serve --reload, qui demande l'extra 'http' "
            "(uv sync --extra http)",
            file=sys.stderr,
        )
        return 2
    try:
        config = load_config(J4 / "loom.yaml")
    except ConfigError as error:
        print(f"Configuration : {error}", file=sys.stderr)
        return 2
    if args.reel and (manquantes := _cles(config)):
        print(
            f"Configuration : clé(s) absente(s) pour --reel : {', '.join(manquantes)}",
            file=sys.stderr,
        )
        return 2
    controle = Controle()
    for nom in cas:
        print(f"\n{'─' * 78}\nCas {nom}\n{'─' * 78}")
        with tempfile.TemporaryDirectory(prefix="loom-rechargement-") as dossier:
            try:
                JOUES[nom](args, Path(dossier), controle)
            except ConfigError as error:
                print(f"Configuration : {error}", file=sys.stderr)
                return 2
    verdict = controle.bilan(len(cas))
    return 2 if verdict is None else 0 if verdict else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
