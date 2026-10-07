# SPDX-License-Identifier: Apache-2.0
"""Phase 6.4b : des outils fournis par un paquet installé (points d'entrée ``loom_ia.tools``).

    uv run python examples/j6/points_entree.py                 # tous les cas
    uv run python examples/j6/points_entree.py --cas source
    uv run python examples/j6/points_entree.py --cas absent
    uv run python examples/j6/points_entree.py --cas doublon
    uv run --env-file .env --extra anthropic \\
        python examples/j6/points_entree.py --reel

Le paquet : ``carnet-devis`` (module ``carnet_devis``), le carnet de devis d'un
artisan en source d'outils. Il déclare le point d'entrée ``carnet`` du groupe
``loom_ia.tools``, qui désigne sa fabrique : elle reçoit de la config le
fichier du carnet (``params``), et rend une source qui ouvre le carnet au
début de chaque run et fournit l'outil ``chercher_devis``. L'exemple
**l'installe dans un dossier temporaire** — son module et son ``dist-info``
(``METADATA``, ``entry_points.txt``) —, mis en tête du chemin de Python : pour
``importlib.metadata``, c'est un paquet installé comme un autre, et rien ne
reste sur le disque. ``uv`` ou ``pip`` installeraient les mêmes fichiers.

La config, écrite elle aussi dans le dossier temporaire, déclare la source
(``tool_sources``) et l'agent qui la référence (``tools: [{source:
carnet}]``) : ``relance``, un orchestrateur simulé, ou en ``--reel``
``relance_reel``, MiniMax-M3 comme dans la config de J4 (clé dans
``M3_API_KEY``). Le modèle voit l'outil sous le nom ``carnet__chercher_devis``.

* **source** : le paquet est listé sans être importé, ``loom validate`` le
  montre avec ses outils, puis l'agent l'utilise — fabriqué une fois, ouvert et
  fermé à chaque run, fermé avec l'instance. Le run se rejoue à l'identique
  sans rappeler l'outil du paquet. En ``--reel``, ce que fait MiniMax est
  montré, pas exigé ; la vie de la source, si.
* **absent** : un point d'entrée qu'aucun paquet ne déclare, un paquet qui ne
  s'importe pas, des paramètres que la fabrique refuse — chaque fois le
  montage est refusé en disant pourquoi.
* **doublon** : un second paquet déclare lui aussi ``carnet`` ; loom ne
  choisit pas, il refuse en nommant les deux, et n'importe aucun d'eux.

Les cas ``absent`` et ``doublon`` passent par ``loom validate`` dans un
process neuf : ce que ce process importe ne dépend pas des cas d'avant.
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import textwrap
from collections.abc import Generator
from contextlib import contextmanager
from importlib import invalidate_caches
from pathlib import Path
from typing import Any

import yaml

from loom_ia.access import Loom, RunResult
from loom_ia.adapters.models import ModelConfigError
from loom_ia.config import ConfigError, load_config
from loom_ia.core.events import ToolCalled
from loom_ia.core.model import RunStatus
from loom_ia.runtime.sources import GROUP, installed

J4 = Path(__file__).parent.parent / "j4" / "relance"
CAS = ("source", "absent", "doublon")
LANCEUR = "import sys; from loom_ia.access.cli import main; sys.exit(main(sys.argv[1:]))"
DEMANDE = "Que dit le devis D-2026-042 ?"
OUTIL = "carnet__chercher_devis"
NUMERO = "D-2026-042"
# Le carnet de l'artisan : celui de l'outil Python de J4, ici dans un fichier.
CARNET = {
    NUMERO: {
        "numero": NUMERO,
        "entreprise": "Plomberie Dupont",
        "client": "Mme Martin",
        "objet": "Remplacement d'un chauffe-eau de 200 L",
        "montant_ttc": 1840.0,
        "envoye_le": "2026-09-02",
        "statut": "en attente",
    },
}

# Le module du paquet : une fabrique, une source, un outil. Ce que la source
# vit est noté dans JOURNAL, que l'exemple relit.
CARNET_DEVIS = '''
"""Le carnet de devis d'un artisan, en source d'outils pour loom."""

import json
from contextlib import asynccontextmanager

from loom_ia.core.ports import ToolError
from loom_ia.tools import tool

JOURNAL = []


class Carnet:
    name = "carnet"
    required = False

    def __init__(self, fichier):
        self.fichier = fichier

    @asynccontextmanager
    async def open(self, context):
        devis = json.loads(self.fichier.read_text(encoding="utf-8"))
        JOURNAL.append(f"ouvert pour le run {context.run_id}")

        @tool
        def chercher_devis(numero: str) -> dict:
            """Renvoie un devis du carnet par son numéro (format D-AAAA-NNN, ex. D-2026-042)."""
            JOURNAL.append(f"appel chercher_devis({numero})")
            trouve = devis.get(numero)
            if trouve is None:
                raise ToolError(f"Aucun devis {numero!r}. Devis connus : {', '.join(devis)}.")
            return trouve

        try:
            yield [chercher_devis]
        finally:
            JOURNAL.append(f"fermé pour le run {context.run_id}")

    async def aclose(self):
        JOURNAL.append("fermé avec l'instance")


def fabrique(*, name, params, secrets, base_dir):
    fichier = params.get("fichier")
    if not isinstance(fichier, str):
        raise ValueError("'fichier' : le chemin du carnet (JSON) est attendu")
    chemin = base_dir / fichier
    if not chemin.is_file():
        raise ValueError(f"'fichier' : {chemin} introuvable")
    JOURNAL.append(f"fabriqué pour la source {name}, carnet {fichier}")
    return Carnet(chemin)
'''

# Un paquet dont l'import échoue (cas absent).
CASSE = "from carnet_devis_inexistant import fabrique  # le paquet en dépend, il manque\n"


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


# --- Le paquet installé, et la config ---------------------------------------------------


def installe(site: Path, paquet: str, module: str, code: str, points: dict[str, str]) -> Path:
    """Un paquet installé dans ``site`` : son module et son ``dist-info`` ; rend ce dernier."""
    (site / f"{module}.py").write_text(code.lstrip(), encoding="utf-8")
    info = site / f"{paquet.replace('-', '_')}-0.1.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {paquet}\nVersion: 0.1\n", encoding="utf-8"
    )
    lignes = "".join(f"{nom} = {cible}\n" for nom, cible in points.items())
    (info / "entry_points.txt").write_text(f"[{GROUP}]\n{lignes}", encoding="utf-8")
    invalidate_caches()
    return info


def ecrit_config(dossier: Path, source: dict[str, Any], *, reel: bool = False) -> Path:
    """La config de l'exemple : la source, et l'agent qui la référence.

    Un seul agent, celui du mode : ``loom validate`` monte chaque agent, et
    celui d'un vrai modèle demande sa clé.
    """
    base = dossier / "relance"
    (base / "agents").mkdir(parents=True)
    (base / "carnet.json").write_text(
        json.dumps(CARNET, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    m3 = load_config(J4 / "loom.yaml").model_spec("M3_MAIN")
    simule: dict[str, Any] = {
        "id": "FAKE_MAIN",
        "sdk": "fake",
        "model": "fake-main",
        "params": {
            "script": [
                {
                    "text": "Je cherche le devis.",
                    "tool_calls": [{"name": OUTIL, "arguments": {"numero": NUMERO}}],
                },
                {
                    "text": (
                        "Le devis D-2026-042 de Mme Martin porte sur le remplacement d'un "
                        "chauffe-eau de 200 L, pour 1 840 € TTC ; il est en attente."
                    )
                },
            ]
        },
    }
    config: dict[str, Any] = {
        "version": 1,
        "models": [m3.model_dump(mode="json", exclude_defaults=True) if reel else simule],
        "tool_sources": [source],
        "telemetry": {"logging": {"level": "WARNING"}},
    }
    (base / "loom.yaml").write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    systeme = (
        "Tu réponds aux questions d'un artisan sur ses devis. Cherche le devis avec "
        f"{OUTIL} avant de répondre, et réponds en une ou deux phrases."
    )
    nom, modele = ("relance_reel", m3.id) if reel else ("relance", "FAKE_MAIN")
    agent = {
        "name": nom,
        "description": "Répond sur un devis du carnet.",
        "main": {"model": modele, "system": systeme},
        "max_iterations": 4,
        "tools": [{"source": "carnet"}],
    }
    (base / "agents" / f"{nom}.yaml").write_text(
        yaml.safe_dump(agent, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return base / "loom.yaml"


CARNET_SOURCE: dict[str, Any] = {
    "name": "carnet",
    "entry_point": "carnet",
    "params": {"fichier": "carnet.json"},
}


def valide(config: Path, site: Path) -> tuple[int, str]:
    """``loom validate`` dans un process neuf, le dossier des paquets dans son chemin."""
    environ = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(filter(None, (str(site), os.environ.get("PYTHONPATH")))),
    }
    fait = subprocess.run(
        [sys.executable, "-c", LANCEUR, "--config", str(config), "validate"],
        capture_output=True,
        text=True,
        env=environ,
        timeout=120,
    )
    return fait.returncode, fait.stdout + fait.stderr


def montre(sortie: str, *debuts: str) -> None:
    """Les lignes de la sortie qui commencent par l'un des débuts — en entier."""
    for ligne in sortie.splitlines():
        if ligne.strip().startswith(debuts):
            print(f"  │ {ligne}")


@contextmanager
def chemin(site: Path, modules: tuple[str, ...]) -> Generator[None]:
    """Le dossier des paquets en tête du chemin de Python, le temps d'un cas."""
    sys.path.insert(0, str(site))
    invalidate_caches()
    try:
        yield
    finally:
        sys.path.remove(str(site))
        for module in modules:
            sys.modules.pop(module, None)
        invalidate_caches()


# --- Les cas ------------------------------------------------------------------------


async def cas_source(args: argparse.Namespace, dossier: Path, controle: Controle) -> None:
    site = dossier / "site"
    site.mkdir()
    info = installe(
        site, "carnet-devis", "carnet_devis", CARNET_DEVIS, {"carnet": "carnet_devis:fabrique"}
    )
    fichier = ecrit_config(dossier, CARNET_SOURCE, reel=args.reel)
    agent = "relance_reel" if args.reel else "relance"
    print(f"  paquet : carnet-devis 0.1, installé dans {site.name}/ (dossier temporaire)")
    enonce("  entry_points.txt : ", (info / "entry_points.txt").read_text(encoding="utf-8"))
    print(f"  agent  : {agent}")
    with chemin(site, ("carnet_devis",)):
        titre("Les points d'entrée installés, lus sans rien importer")
        trouves = [p for p in installed() if p.name == "carnet"]
        for point in trouves:
            print(f"  {point.name} : {point.package} {point.version}, {point.value}")
        config = load_config(fichier)
        print(
            "  carnet est listé, et ni la liste ni la lecture de la config n'ont importé "
            "carnet_devis : "
            + controle.tient(
                "source : le paquet n'est pas listé, ou il a été importé trop tôt",
                [(p.package, p.value) for p in trouves]
                == [("carnet-devis", "carnet_devis:fabrique")]
                and "carnet_devis" not in sys.modules,
            )
        )

        titre("loom validate")
        code, sortie = valide(fichier, site)
        montre(sortie, "Source", "Paquets", "relance", "source carnet", "Configuration")
        print(
            "  la source, le paquet utilisé et l'outil préfixé sont nommés : "
            + controle.tient(
                f"source : loom validate ne dit pas ce qui est annoncé (code {code})",
                code == 0
                and "Source     : carnet → point d'entrée carnet" in sortie
                and "carnet (carnet-devis 0.1, utilisé)" in sortie
                and f"source carnet : {OUTIL}" in sortie,
            )
        )

        async with Loom(config) as loom:
            runs: list[RunResult] = []
            for numero in (1, 2):
                result = await loom.run(agent, DEMANDE)
                runs.append(result)
                titre(f"Run {numero} de {agent} : {DEMANDE}")
                print(
                    f"  statut : {result.status.value}"
                    + (f" ({result.error})" if result.error else "")
                )
                enonce("  texte  : ", result.text or "(vide)")
                events = await loom.events(result.run_id, session_id=result.session_id)
                appels = [e.payload for e in events if isinstance(e.payload, ToolCalled)]
                print(f"  appels d'outil ({len(appels)}) :")
                for appel in appels:
                    print(
                        f"    {appel.tool_name}({json.dumps(appel.arguments, ensure_ascii=False)})"
                    )
                if not args.reel:
                    print(
                        f"  le run est allé au bout, après {OUTIL}({NUMERO}) : "
                        + controle.tient(
                            f"source : le run {numero} ne rend pas ce qui est annoncé",
                            result.status == RunStatus.COMPLETED
                            and [(a.tool_name, a.arguments) for a in appels]
                            == [(OUTIL, {"numero": NUMERO})],
                        )
                    )
            journal = sys.modules["carnet_devis"].JOURNAL
            avant = list(journal)

            titre("Le run 1 rejoué à l'identique")
            rapport = await loom.replay(runs[0].run_id)
            ajoutes = journal[len(avant) :]
            print(f"  identique : {'oui' if rapport.identical else 'non'}")
            print(f"  ce que la source a vécu pendant le rejeu ({len(ajoutes)}) :")
            for ligne in ajoutes:
                print(f"    {ligne}")
            if runs[0].status != RunStatus.COMPLETED:
                # Un vrai modèle tombé (clé, réseau) : le rejeu d'une panne n'éprouve
                # pas la source, il n'y a rien à exiger.
                print("  le run 1 n'est pas allé au bout : son rejeu n'éprouve pas la source")
                controle.saute(
                    "source (le run 1 n'est pas allé au bout : rejeu non exigé)", partie=True
                )
            else:
                print(
                    "  rejoué à l'identique, et l'outil du paquet n'a pas été rappelé : "
                    + controle.tient(
                        "source : le rejeu n'est pas identique, ou il a rappelé l'outil",
                        rapport.identical
                        and not any(ligne.startswith("appel ") for ligne in ajoutes),
                    )
                )
        titre("Ce que la source a vécu, du montage à la fermeture de l'instance")
        for ligne in journal:
            print(f"  {ligne}")
        vie = [ligne for ligne in avant if not ligne.startswith("appel ")]
        print(
            "  fabriquée une fois, ouverte et fermée à chaque run, fermée avec l'instance : "
            + controle.tient(
                "source : sa vie n'est pas celle annoncée",
                vie
                == [
                    "fabriqué pour la source carnet, carnet carnet.json",
                    f"ouvert pour le run {runs[0].run_id}",
                    f"fermé pour le run {runs[0].run_id}",
                    f"ouvert pour le run {runs[1].run_id}",
                    f"fermé pour le run {runs[1].run_id}",
                ]
                and journal[-1] == "fermé avec l'instance",
            )
        )
    if args.reel:
        controle.saute(
            "source (en --reel, ce que rendent les runs est montré, pas exigé)", partie=True
        )


async def cas_absent(args: argparse.Namespace, dossier: Path, controle: Controle) -> None:
    site = dossier / "site"
    site.mkdir()
    installe(
        site, "carnet-devis", "carnet_devis", CARNET_DEVIS, {"carnet": "carnet_devis:fabrique"}
    )
    installe(site, "carnet-casse", "carnet_casse", CASSE, {"casse": "carnet_casse:fabrique"})
    essais = [
        (
            "un point d'entrée qu'aucun paquet ne déclare",
            {**CARNET_SOURCE, "entry_point": "carnet_absent"},
            "Point d'entrée 'carnet_absent' absent du groupe loom_ia.tools",
        ),
        (
            "un paquet qui ne s'importe pas",
            {**CARNET_SOURCE, "entry_point": "casse"},
            "(carnet-casse 0.1, carnet_casse:fabrique) : import impossible — ModuleNotFoundError",
        ),
        (
            "des paramètres que la fabrique refuse",
            {**CARNET_SOURCE, "params": {"fichier": "ailleurs.json"}},
            "refusée par sa fabrique — 'fichier' :",
        ),
    ]
    for numero, (quoi, source, raison) in enumerate(essais, start=1):
        sous = dossier / f"essai-{numero}"
        sous.mkdir()
        fichier = ecrit_config(sous, source)
        titre(f"Refusé : {quoi}")
        print(f"  tool_sources : {json.dumps(source, ensure_ascii=False)}")
        code, sortie = valide(fichier, site)
        montre(sortie, "Configuration", "Paquets")
        print(
            f"  loom validate refuse (code {code}), en disant pourquoi : "
            + controle.tient(
                f"absent : {quoi} — code {code}, raison attendue « {raison} »",
                code == 2 and raison in sortie,
            )
        )


async def cas_doublon(args: argparse.Namespace, dossier: Path, controle: Controle) -> None:
    site = dossier / "site"
    site.mkdir()
    installe(
        site, "carnet-devis", "carnet_devis", CARNET_DEVIS, {"carnet": "carnet_devis:fabrique"}
    )
    # Le second paquet ne doit jamais être importé : s'il l'était, son import
    # échouerait, et le message le dirait.
    installe(
        site,
        "carnet-bis",
        "carnet_bis",
        'raise RuntimeError("carnet_bis importé")\n',
        {"carnet": "carnet_bis:fabrique"},
    )
    fichier = ecrit_config(dossier, CARNET_SOURCE)
    titre("Deux paquets déclarent le point d'entrée carnet")
    code, sortie = valide(fichier, site)
    montre(sortie, "Configuration", "Paquets")
    print(
        f"  loom validate refuse (code {code}) et nomme les deux, sans importer aucun d'eux : "
        + controle.tient(
            f"doublon : refus attendu (code {code})",
            code == 2
            and "déclaré par plusieurs paquets" in sortie
            and "carnet-devis 0.1, carnet_devis:fabrique" in sortie
            and "carnet-bis 0.1, carnet_bis:fabrique" in sortie
            and "carnet_bis importé" not in sortie,
        )
    )


# --- Lancement ----------------------------------------------------------------------


async def jouer(nom: str, args: argparse.Namespace, controle: Controle) -> None:
    print(f"\n{'─' * 78}\nCas {nom}\n{'─' * 78}")
    with tempfile.TemporaryDirectory(prefix="loom-points-entree-") as dossier:
        if nom == "source":
            await cas_source(args, Path(dossier), controle)
        elif nom == "absent":
            await cas_absent(args, Path(dossier), controle)
        else:
            await cas_doublon(args, Path(dossier), controle)


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Des outils fournis par un paquet installé")
    parser.add_argument("--reel", action="store_true", help="vrais modèles (relance_reel)")
    parser.add_argument("--cas", action="append", choices=CAS, help="cas à jouer (tous par défaut)")
    args = parser.parse_args(argv)
    cas = tuple(args.cas) if args.cas else CAS
    controle = Controle()
    for nom in cas:
        try:
            await jouer(nom, args, controle)
        except (ModelConfigError, ConfigError) as error:
            print(f"Configuration : {error}", file=sys.stderr)
            return 2
    verdict = controle.bilan(len(cas))
    return 2 if verdict is None else 0 if verdict else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
