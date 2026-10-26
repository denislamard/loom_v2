# SPDX-License-Identifier: Apache-2.0
"""Phase 5.5b : le sceau — contenus illisibles au repos, une clé par client.

    uv run --extra crypto python examples/j5/chiffrement.py        # les trois cas
    uv run --extra crypto python examples/j5/chiffrement.py --cas scelle
    uv run --extra crypto python examples/j5/chiffrement.py --cas efface
    uv run --extra crypto python examples/j5/chiffrement.py --cas clients
    uv run --env-file .env --extra crypto --extra anthropic --extra openai \\
        python examples/j5/chiffrement.py --reel

Un seul extra en plus des modèles : ``crypto``. Le journal, les fichiers et
les clés d'idempotence vont dans un dossier **temporaire** avec les backends
qui n'exigent rien (JSONL, dossier local, magasin ``journal``), si bien que
l'exemple tourne sans ``sqlite`` ni le reste.

Config : ``examples/j5/relance/``, celle de 5.1a. Le sceau et les clés sont
posés **en code**, et rien n'est écrit sous ``relance/`` : un journal scellé
laissé dans ``relance/data`` serait illisible pour les autres exemples, qui
n'ont pas la clé. Les fichiers de ``relance/`` ne changent pas.

Ce que le sceau ferme : la **charge** de chaque événement et les **octets** de
chaque fichier. Ce qu'il laisse en clair : l'enveloppe du journal — qui, quand,
quel type, quel statut, quelles facettes —, si bien qu'un journal scellé se
filtre exactement comme un journal en clair.

* **scelle** : un run ordinaire sous le sceau. Ce qui est rangé sur le disque,
  ce que l'instance en relit avec sa clé, et la requête par outil qui marche
  quand même.
* **efface** : le *crypto-shredding*. La clé retirée, la charge ne s'ouvre
  plus — mais la session se **liste et se supprime** encore, ce qu'exige le
  RGPD. Plus le renouvellement d'une clé : la neuve ferme, l'ancienne ouvre.
* **clients** : une clé par client, par la redirection ``secrets`` de 5.1a. La
  clé de l'un n'ouvre pas le journal de l'autre, effacer celle de l'un ne
  touche pas l'autre, et le chargement dit ce qui manque.
"""

import argparse
import asyncio
import json
import os
import sys
import tempfile
import textwrap
from base64 import b64encode
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

from loom_ia.access import Loom
from loom_ia.adapters.artifacts.sealing import MAGIC
from loom_ia.adapters.crypto import decode_key, fingerprint
from loom_ia.adapters.models import ModelConfigError
from loom_ia.adapters.stores.codec import SEALED
from loom_ia.config import ConfigError, LoomConfig, load_config
from loom_ia.config.models import ArtifactsStorage, EncryptionStorage, IdempotencyStorage
from loom_ia.core.events import EventQuery
from loom_ia.core.model import SessionId, TenantId, new_id
from loom_ia.core.ports import MissingKey, SealError
from loom_ia.runtime import apply_logging, create_keyring, encryption_warnings
from loom_ia.tenancy import EnvironmentSecrets

CONFIG = Path(__file__).parent / "relance" / "loom.yaml"
CAS = ("scelle", "efface", "clients")

DUPONT = TenantId("dupont-plomberie")
MARTIN = TenantId("martin-chauffage")
DEVIS = {DUPONT: "D-2026-042", MARTIN: "D-2026-117"}

# Le nom du secret qui porte la clé, tel que la config le déclarerait :
#   storage: {encryption: {keys: [LOOM_JOURNAL_KEY]}}
CLE = "LOOM_JOURNAL_KEY"
CLE_DUPONT = "DUPONT_JOURNAL_KEY"
CLE_MARTIN = "MARTIN_JOURNAL_KEY"


def clef(graine: bytes) -> str:
    """Une clé AES-256 en base64, comme une variable d'environnement en porte.

    En service, elle se fabrique au hasard une fois pour toutes :
    ``python -c "import base64, os; print(base64.b64encode(os.urandom(32)).decode())"``
    Ici la graine est fixe, pour que l'exemple soit relisible.
    """
    return b64encode(graine * 32)[:44].decode()


def shown(path: Path) -> str:
    return os.path.relpath(path)


def titre(texte: str) -> None:
    print(f"\n=== {texte} ===")


def agent_de(args: argparse.Namespace) -> str:
    return "relance_reel" if args.reel else "relance"


def demande(tenant: TenantId) -> str:
    return f"Relance le client du devis {DEVIS[tenant]}, sur un ton cordial."


def session(quoi: str) -> SessionId:
    return SessionId(f"sceau-{new_id()[-8:]}-{quoi}")


class Controle:
    """Ce que chaque essai devait rendre : l'exemple l'annonce, puis le vérifie."""

    def __init__(self) -> None:
        self.ecarts: list[str] = []

    def tient(self, quoi: str, vrai: bool) -> str:
        if not vrai:
            self.ecarts.append(quoi)
        return "oui" if vrai else "NON"

    def bilan(self) -> bool:
        if not self.ecarts:
            print("\nChaque essai a rendu ce que l'exemple annonçait.")
            return True
        print("\nUn essai au moins n'a pas rendu ce qui était annoncé :")
        for ecart in self.ecarts:
            print(f"  {ecart}")
        return False


def enonce(quoi: str, texte: str, largeur: int = 96) -> None:
    """Imprime un message long sur plusieurs lignes, sans rien en couper.

    Ces messages sont la matière de l'exemple : l'un nomme l'empreinte de la
    clé qui manque, l'autre les deux clients qui partagent la leur. Tronquer
    les ferait dire à l'exemple qu'il montre ce qu'on ne lit pas.
    """
    marge = " " * len(quoi)
    for numero, ligne in enumerate(textwrap.wrap(texte, largeur - len(quoi))):
        print(f"{quoi if numero == 0 else marge}{ligne}")


# --- La config de l'exemple : la même, ailleurs, scellée ----------------------


def scellee(
    dossier: Path, *, keys: Sequence[str] = (CLE,), offload: int | None = None
) -> LoomConfig:
    """La config de ``relance/``, journal et fichiers dans un dossier à part, scellés.

    Le sceau se déclare à la racine ; ce qui est propre à un client, c'est sa
    clé. Les journaux des clients qui ont le leur sont déplacés aussi : le
    codec de l'instance vaut pour eux, et on ne scelle pas les journaux
    d'exemple des autres phases.

    L'idempotence passe au magasin ``journal`` — celui de la config est en
    SQLite, sous ``relance/data``. Deux raisons : ne pas écrire là-bas, et
    parce qu'aucun outil de cette config ne déclare de clé métier, le magasin
    ``journal`` suffit. Il a en plus l'intérêt de ranger ses clés **dans** le
    journal, donc sous le sceau ; un magasin partagé (SQLite, Postgres, Redis)
    garde ses résultats en clair, et le sceau ne le couvre pas.
    """
    config = load_config(CONFIG)
    events = config.storage.events.model_copy(update={"path": dossier / "events"})
    storage = config.storage.model_copy(
        update={
            "events": events,
            "artifacts": ArtifactsStorage(backend="local", path=dossier / "files"),
            "idempotency": IdempotencyStorage(),
            "encryption": EncryptionStorage(keys=tuple(keys)),
        }
    )
    tenants = tuple(
        tenant.model_copy(
            update={
                "storage": tenant.storage.model_copy(
                    update={
                        "events": tenant.storage.events.model_copy(
                            update={"path": dossier / "events" / tenant.id}
                        )
                    }
                )
            }
        )
        if tenant.storage is not None
        else tenant
        for tenant in config.tenants
    )
    change: dict[str, object] = {"storage": storage, "tenants": tenants}
    if offload is not None:
        # Le seuil de déport, abaissé : les résultats d'outils partent au
        # stockage de fichiers, où le sceau les attend.
        outils = config.execution.tools.model_copy(update={"offload_over": offload})
        change["execution"] = config.execution.model_copy(update={"tools": outils})
    return config.model_copy(update=change)


def journal(config: LoomConfig, tenant: TenantId, session_id: SessionId) -> Path:
    """Le fichier où ce client range cette session."""
    spec = config.tenant_spec(tenant)
    storage = spec.storage if spec is not None and spec.storage is not None else config.storage
    racine = storage.events.path
    assert racine is not None
    return racine / tenant / f"{session_id}.jsonl"


def lignes(path: Path) -> list[dict[str, Any]]:
    lues = path.read_text(encoding="utf-8").splitlines()
    return [cast("dict[str, Any]", json.loads(ligne)) for ligne in lues if ligne]


# --- Cas 1 : ce que le sceau ferme, et ce qu'il laisse en clair ---------------


async def scelle(args: argparse.Namespace, controle: Controle) -> None:
    agent = agent_de(args)
    with tempfile.TemporaryDirectory(prefix="loom-sceau-") as dossier:
        config = scellee(Path(dossier), offload=100)
        environ = {**os.environ, CLE: clef(b"S")}
        empreinte = fingerprint(decode_key(environ[CLE], what="exemple"))
        titre("Un run ordinaire, sous le sceau")
        print(f"  storage.encryption.keys : [{CLE}] → empreinte {empreinte}")
        print(f"  journal                 : {Path(dossier) / 'events'} (temporaire)")
        ou = session("scelle")
        async with Loom(config, environ=environ) as loom:
            result = await loom.run(agent, demande(MARTIN), session_id=ou, tenant=MARTIN)
            print(f"\n  run {result.status}, {result.usage.total_tokens} tokens")
            # Avec la clé, rien ne change : l'instance relit son journal comme
            # elle l'a toujours fait.
            relu = await loom.events(result.run_id, session_id=ou, tenant_id=MARTIN)
            etat = await loom.state(result.run_id, session_id=ou, tenant_id=MARTIN)
            rendue = "oui" if etat.output else "NON"
            print(f"  relu par l'instance : {len(relu)} événements, sortie {rendue}")
            trouves = await loom.query(
                EventQuery(tenant_id=MARTIN, session_id=ou, tool_name="rediger_relance")
            )
            # Le détail par type, et non le seul compte : le nombre d'appels
            # d'un rôle dépend des modèles (une réparation demandée par un
            # juge en ajoute), et un compte nu laisserait deviner lequel.
            comptes = Counter(event.type for event in trouves)
            detail = ", ".join(f"{nom} x{n}" for nom, n in sorted(comptes.items()))
            print(f"  requête par outil   : {len(trouves)} événement(s) — {detail}")
            print(
                "\n  le sceau ne coûte rien à la lecture de celui qui a la clé : "
                + controle.tient(
                    "un journal scellé ne se relit pas comme avant",
                    etat.output is not None and {"tool.called", "tool.completed"} <= set(comptes),
                )
            )

        fichier = journal(config, MARTIN, ou)
        rangees = lignes(fichier)
        titre("Ce qui est écrit sur le disque")
        premiere = rangees[0]
        print(f"  {len(rangees)} lignes dans {fichier.name}")
        print("  la première, en clair :")
        for champ in ("type", "category", "status", "agent", "seq", "facets"):
            print(f"    {champ:<10}{premiere[champ]!r}")
        ferme = str(_charge(premiere).get(SEALED, ""))
        print(f"    payload   {{{SEALED}: '{ferme[:24]}…' ({len(ferme)} car.), key_id: ...}}")
        brut = fichier.read_text(encoding="utf-8")
        cherche = DEVIS[MARTIN]
        vu = "OUI" if cherche in brut else "non"
        outil = "oui" if "rediger_relance" in brut else "NON"
        print(f"\n  le numéro de devis ({cherche}) apparaît en clair : {vu}")
        print(f"  le nom de l'outil ('rediger_relance') apparaît : {outil}")
        print("    — c'est une facette, donc un champ qui ne porte pas de contenu (5.4c) ;")
        print("      c'est ce qui permet de chercher ses appels dans un journal scellé.")
        print(
            "\n  le contenu est fermé, l'enveloppe est ouverte : "
            + controle.tient(
                "le contenu ou l'enveloppe n'est pas dans l'état annoncé",
                cherche not in brut
                and all(set(_charge(ligne)) == {SEALED, "key_id"} for ligne in rangees)
                and all(ligne["type"] for ligne in rangees),
            )
        )

        titre("Et les fichiers, qui portent le plus gros")
        print(f"  execution.tools.offload_over : {config.execution.tools.offload_over} octets")
        print("    abaissé par l'exemple, pour qu'un résultat d'outil parte au stockage de")
        print("    fichiers — c'est là que vit le plus volumineux de ce qu'un run manipule.")
        fichiers = sorted(
            chemin for chemin in (Path(dossier) / "files").rglob("*") if chemin.is_file()
        )
        for chemin in fichiers[:3]:
            octets = chemin.read_bytes()
            print(f"\n  {chemin.name}")
            print(f"    {len(octets)} octets, commence par {octets[:9]!r}")
            dedans = "OUI" if DEVIS[MARTIN].encode() in octets else "non"
            print(f"    le devis ({DEVIS[MARTIN]}) y apparaît : {dedans}")
        print(
            "\n  ce qui part au stockage de fichiers est scellé aussi : "
            + controle.tient(
                "aucun fichier déporté, ou un fichier rangé en clair",
                bool(fichiers)
                and all(chemin.read_bytes().startswith(MAGIC) for chemin in fichiers)
                and all(DEVIS[MARTIN].encode() not in chemin.read_bytes() for chemin in fichiers),
            )
        )


def _charge(ligne: dict[str, Any]) -> dict[str, Any]:
    charge: object = ligne["payload"]
    assert isinstance(charge, dict)
    return cast("dict[str, Any]", charge)


# --- Cas 2 : effacer la clé, et ce qui reste ---------------------------------


async def efface(args: argparse.Namespace, controle: Controle) -> None:
    agent = agent_de(args)
    with tempfile.TemporaryDirectory(prefix="loom-sceau-") as dossier:
        config = scellee(Path(dossier))
        avec = {**os.environ, CLE: clef(b"E")}
        empreinte = fingerprint(decode_key(avec[CLE], what="exemple"))
        ou = session("efface")
        titre("Un run écrit, puis la clé s'en va")
        async with Loom(config, environ=avec) as loom:
            result = await loom.run(agent, demande(MARTIN), session_id=ou, tenant=MARTIN)
            print(f"  run {result.status}, journal scellé par la clé {empreinte}")

        # La même config, le même journal, sans la variable : c'est tout ce que
        # « supprimer la clé » veut dire. Rien n'a été réécrit.
        sans = {name: value for name, value in os.environ.items() if name != CLE}
        async with Loom(config, environ=sans) as perdu:
            dit = await _refus(lambda: perdu.events(result.run_id, session_id=ou, tenant_id=MARTIN))
            print()
            enonce("  relire       → ", dit)
            print(
                "  l'empreinte de la clé qui manque est nommée : "
                + controle.tient("le refus ne nomme pas la clé attendue", empreinte in dit)
            )
            listees = await perdu.sessions(tenant_id=MARTIN)
            print(f"\n  lister       → {len(listees)} session(s) ; {ou} présente")
            print("    la marque d'une ligne (session, rang, date) reste en clair : lister")
            print("    et supprimer sont ce que le RGPD exige, et n'ont pas besoin du contenu.")
            retire = await perdu.delete_session(ou, tenant_id=MARTIN)
            print(f"  supprimer    → {retire.events} événement(s), {retire.artifacts} fichier(s)")
            restantes = await perdu.sessions(tenant_id=MARTIN)
            print(
                "\n  un journal sans sa clé se voit et s'efface, sans se lire : "
                + controle.tient(
                    "un journal scellé ne se liste plus ou ne s'efface plus",
                    any(record.session_id == ou for record in listees)
                    and retire.events > 0
                    and all(record.session_id != ou for record in restantes),
                )
            )

        titre("Renouveler une clé : la neuve ferme, l'ancienne ouvre encore")
        deux = scellee(Path(dossier), keys=["LOOM_JOURNAL_KEY_2", CLE])
        ancienne = fingerprint(decode_key(avec[CLE], what="exemple"))
        neuve_b64 = clef(b"N")
        tournee = {**avec, "LOOM_JOURNAL_KEY_2": neuve_b64}
        neuve = fingerprint(decode_key(neuve_b64, what="exemple"))
        suite = session("tournee")
        async with Loom(config, environ=avec) as avant:
            premier = await avant.run(agent, demande(MARTIN), session_id=suite, tenant=MARTIN)
        async with Loom(deux, environ=tournee) as apres:
            second = await apres.run(agent, demande(MARTIN), session_id=suite, tenant=MARTIN)
            tout = await apres.events(second.run_id, session_id=suite, tenant_id=MARTIN)
            avant_tout = await apres.events(premier.run_id, session_id=suite, tenant_id=MARTIN)
        empreintes = {
            str(_charge(ligne)["key_id"]) for ligne in lignes(journal(deux, MARTIN, suite))
        }
        print(f"  clés déclarées : ['LOOM_JOURNAL_KEY_2' ({neuve}), '{CLE}' ({ancienne})]")
        print(f"  le journal porte les deux empreintes : {', '.join(sorted(empreintes))}")
        print(f"  et se relit en entier : {len(avant_tout)} + {len(tout)} événements")
        print(
            "\n  la neuve ferme, l'ancienne ouvre ce qu'elle a fermé : "
            + controle.tient(
                "le renouvellement n'a pas gardé l'ancien lisible",
                empreintes == {neuve, ancienne} and bool(avant_tout) and bool(tout),
            )
        )


async def _refus(faire: object) -> str:
    """Le message d'un sceau qui ne s'ouvre pas, ou une chaîne vide."""
    try:
        await faire()  # type: ignore[operator]
    except SealError as error:
        return str(error)
    return ""


# --- Cas 3 : une clé par client ----------------------------------------------


async def clients(args: argparse.Namespace, controle: Controle) -> None:
    agent = agent_de(args)
    with tempfile.TemporaryDirectory(prefix="loom-sceau-") as dossier:
        config = _par_client(scellee(Path(dossier)))
        environ = {**os.environ, CLE_DUPONT: clef(b"D"), CLE_MARTIN: clef(b"M")}
        titre("Chaque client redirige le même nom vers sa variable (5.1a)")
        print(f"  storage.encryption.keys : [{CLE}]")
        for tenant, variable in ((DUPONT, CLE_DUPONT), (MARTIN, CLE_MARTIN)):
            print(f"  {tenant:<20}secrets: {{{CLE}: {variable}}}")
        trousseau = create_keyring(config, EnvironmentSecrets(config, environ))
        assert trousseau is not None
        empreintes = {tenant: trousseau.ciphers(tenant)[0].key_id for tenant in (DUPONT, MARTIN)}
        for tenant, empreinte in empreintes.items():
            print(f"  {tenant:<20}→ clé {empreinte}")
        print(
            "\n  deux clients, deux clés : "
            + controle.tient(
                "les deux clients scellent avec la même clé",
                empreintes[DUPONT] != empreintes[MARTIN],
            )
        )

        titre("Chacun écrit chez lui, sous sa clé")
        places: dict[TenantId, SessionId] = {}
        runs: dict[TenantId, str] = {}
        scellants: dict[TenantId, set[str]] = {}
        async with Loom(config, environ=environ) as loom:
            for tenant in (DUPONT, MARTIN):
                ou = session(str(tenant).split("-")[0])
                places[tenant] = ou
                result = await loom.run(agent, demande(tenant), session_id=ou, tenant=tenant)
                runs[tenant] = result.run_id
                fichier = journal(config, tenant, ou)
                scellants[tenant] = {str(_charge(ligne)["key_id"]) for ligne in lignes(fichier)}
                print(f"  {tenant:<20}{result.status:<12}{fichier.parent.name}/{fichier.name}")
                print(f"  {'':<20}scellé par {', '.join(sorted(scellants[tenant]))}")
        print(
            "\n  le journal de chacun porte l'empreinte de sa clé, et d'aucune autre : "
            + controle.tient(
                "un journal n'est pas scellé par la clé de son client",
                all(scellants[tenant] == {empreintes[tenant]} for tenant in (DUPONT, MARTIN)),
            )
        )

        titre("La clé de l'un n'ouvre pas le journal de l'autre")
        # Le trousseau de Dupont seul : Martin n'a plus de clé du tout, et la
        # sienne n'est pas emprutable — une redirection vers une variable
        # absente vaut vide, jamais le secret commun (5.1a).
        dupont_seul = {name: v for name, v in environ.items() if name != CLE_MARTIN}
        async with Loom(config, environ=dupont_seul) as boiteux:
            lisible = await boiteux.export_session(places[DUPONT], tenant_id=DUPONT)
            dit = await _refus(lambda: boiteux.export_session(places[MARTIN], tenant_id=MARTIN))
            print(f"  Dupont, avec sa clé   → {len(lisible)} événements")
            enonce("  Martin, sans la sienne → ", dit)
            refuse = await _refus(
                lambda: boiteux.run(
                    agent, demande(MARTIN), session_id=session("refus"), tenant=MARTIN
                )
            )
            enonce("  Martin, un nouveau run → ", refuse)
        print(
            "\n  effacer la clé d'un client ne touche pas l'autre : "
            + controle.tient(
                "l'effacement d'une clé a débordé sur l'autre client",
                bool(lisible) and bool(dit) and bool(refuse),
            )
        )

        titre("Ce que le chargement dit, avant le premier run")
        manquante = encryption_warnings(
            config, create_keyring(config, EnvironmentSecrets(config, dupont_seul))
        )
        for avertissement in manquante:
            enonce("  sans la clé de Martin : ", avertissement)
        # La même config, sans les redirections : les deux clients lisent alors
        # la même variable, donc scellent avec la même clé.
        commune = scellee(Path(dossier))
        environ_commun = {**os.environ, CLE: clef(b"C")}
        partagee = encryption_warnings(
            commune, create_keyring(commune, EnvironmentSecrets(commune, environ_commun))
        )
        for avertissement in partagee:
            enonce("  sans redirection      : ", avertissement)
        print(
            "\n  un client sans clé et une clé partagée sont dits, pas devinés : "
            + controle.tient(
                "le chargement ne dit pas ce qui manque ou ce qui est partagé",
                len(manquante) == 1 and len(partagee) == 1,
            )
        )
        print("    un avertissement, dans tous les profils : la config ne peut pas distinguer")
        print("    une clé effacée exprès d'une variable oubliée, et refuser de démarrer")
        print("    arrêterait le service de tous les autres clients.")


def _par_client(config: LoomConfig) -> LoomConfig:
    """Donne à chaque client sa redirection du secret de la clé."""
    variables = {DUPONT: CLE_DUPONT, MARTIN: CLE_MARTIN}
    tenants = tuple(
        tenant.model_copy(
            update={"secrets": {**tenant.secrets, CLE: variables.get(tenant.id, CLE)}}
        )
        for tenant in config.tenants
    )
    return config.model_copy(update={"tenants": tenants})


# --- Mise en route -----------------------------------------------------------


async def jouer(nom: str, args: argparse.Namespace, controle: Controle) -> None:
    print(f"\n{'─' * 78}\nCas {nom}\n{'─' * 78}")
    if nom == "scelle":
        await scelle(args, controle)
    elif nom == "efface":
        await efface(args, controle)
    else:
        await clients(args, controle)


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Le sceau : contenus illisibles au repos, une clé par client"
    )
    parser.add_argument("--reel", action="store_true", help="vrais modèles")
    parser.add_argument("--cas", action="append", choices=CAS, help="cas à jouer (tous par défaut)")
    args = parser.parse_args(argv)
    cas = tuple(args.cas) if args.cas else CAS

    try:
        config = load_config(CONFIG)
    except ConfigError as error:
        print(f"Configuration : {error}", file=sys.stderr)
        return 2
    apply_logging(config)
    print(f"Config   : {shown(CONFIG)}")
    print(f"Agent    : {agent_de(args)}")
    print(f"Clients  : {', '.join(str(tenant.id) for tenant in config.tenants)}")
    controle = Controle()
    for nom in cas:
        try:
            await jouer(nom, args, controle)
        except ModelConfigError as error:
            print(f"Configuration : {error}", file=sys.stderr)
            return 2
        except MissingKey as error:
            print(f"Clé : {error}", file=sys.stderr)
            return 2
        except ImportError as error:
            print(f"Extra manquant : {error} (uv sync --extra crypto)", file=sys.stderr)
            return 2
    return 0 if controle.bilan() else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
