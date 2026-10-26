# SPDX-License-Identifier: Apache-2.0
"""Phase 5.5c : la rétention — effacer les sessions dormantes, et rien d'autre.

    uv run python examples/j5/retention.py                    # les trois cas
    uv run python examples/j5/retention.py --cas borne
    uv run python examples/j5/retention.py --cas clients
    uv run --extra crypto python examples/j5/retention.py --cas scelle
    uv run --env-file .env --extra crypto --extra anthropic --extra openai \\
        python examples/j5/retention.py --reel

Le cas `scelle` demande l'extra `crypto` (il éprouve la rencontre avec le sceau
de 5.5b) ; les deux autres n'en demandent aucun. Journal, fichiers et clés vont
dans un dossier **temporaire** avec les backends qui n'exigent rien.

Config : ``examples/j5/relance/``, celle de 5.1a. Les bornes sont posées **en
code**, et rien n'est écrit sous ``relance/`` : la rétention **supprime**, et un
exemple ne doit pas s'entraîner sur les journaux des autres phases.

Comment le temps passe ici, en deux façons. Le cas `borne` **vieillit** pour de
vrai un journal — ses lignes sont redatées de 90 jours —, si bien que la
rétention lit l'horloge comme le fera la commande ; c'est la seule manière
d'avoir deux âges différents dans un dossier neuf, et un journal **scellé** ne
se vieillirait pas ainsi (depuis 5.5c le sceau tient l'horodatage, justement
parce qu'une suppression en dépend). Les deux autres cas n'ont pas besoin de
deux âges : ils passent ``now=`` à ``Loom.apply_retention`` et regardent le même
journal plus tard. La commande `loom retention`, elle, n'a pas de ``--now`` :
c'est la plateforme qui la met à l'heure, comme les portes de 5.4c.

* **borne** : une session dormante part, une session vivante reste. L'essai à
  blanc ne touche à rien et dit ce qu'il coûte ; le balayage emporte le journal,
  les fichiers et les clés d'idempotence.
* **clients** : une borne par client, celle de la racine sinon ; un client qui
  annule la règle commune ; un seul client balayé sans toucher aux autres.
* **scelle** : un journal scellé dont la clé a disparu s'efface quand même —
  c'est le seul moyen de reprendre la place. Et la conséquence assumée : une
  session dont le run n'a jamais fini part comme les autres.
"""

import argparse
import asyncio
import os
import sys
import tempfile
import textwrap
from base64 import b64encode
from datetime import UTC, datetime, timedelta
from importlib.util import find_spec
from pathlib import Path
from typing import Any

from loom_ia.access import Loom, RetentionReport
from loom_ia.adapters.models import ModelConfigError
from loom_ia.config import ConfigError, LoomConfig, load_config
from loom_ia.config.models import ArtifactsStorage, EncryptionStorage, IdempotencyStorage
from loom_ia.config.models import RetentionStorage as Borne
from loom_ia.core.events import Event
from loom_ia.core.model import SessionId, TenantId, new_id
from loom_ia.core.ports import SealError
from loom_ia.runtime import apply_logging
from loom_ia.testing import RunJournal

CONFIG = Path(__file__).parent / "relance" / "loom.yaml"
CAS = ("borne", "clients", "scelle")

DUPONT = TenantId("dupont-plomberie")
MARTIN = TenantId("martin-chauffage")
DEVIS = {DUPONT: "D-2026-042", MARTIN: "D-2026-117"}
CLE = "LOOM_JOURNAL_KEY"


def clef(graine: bytes) -> str:
    """Une clé AES-256 en base64 (5.5b), graine fixe pour que l'exemple se relise."""
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
    return SessionId(f"retention-{new_id()[-8:]}-{quoi}")


def dans(jours: int) -> datetime:
    """Le même journal, regardé plus tard : aucune date n'est maquillée."""
    return datetime.now(UTC) + timedelta(days=jours)


class Controle:
    """Ce que chaque essai devait rendre : l'exemple l'annonce, puis le vérifie."""

    def __init__(self) -> None:
        self.ecarts: list[str] = []
        self.sautes: list[str] = []

    def tient(self, quoi: str, vrai: bool) -> str:
        if not vrai:
            self.ecarts.append(quoi)
        return "oui" if vrai else "NON"

    def saute(self, quoi: str) -> None:
        """Un cas qui n'a pas pu être joué : à dire, sinon le bilan mentirait."""
        self.sautes.append(quoi)

    def bilan(self) -> bool:
        for saute in self.sautes:
            print(f"\nCas sauté : {saute}")
        if not self.ecarts:
            joues = "Chaque essai joué a rendu" if self.sautes else "Chaque essai a rendu"
            print(f"\n{joues} ce que l'exemple annonçait.")
            return True
        print("\nUn essai au moins n'a pas rendu ce qui était annoncé :")
        for ecart in self.ecarts:
            print(f"  {ecart}")
        return False


def enonce(quoi: str, texte: str, largeur: int = 96) -> None:
    """Imprime un message long sur plusieurs lignes, sans rien en couper."""
    marge = " " * len(quoi)
    for numero, ligne in enumerate(textwrap.wrap(texte, largeur - len(quoi))):
        print(f"{quoi if numero == 0 else marge}{ligne}")


# --- La config de l'exemple : la même, ailleurs, avec une borne ---------------


def bornee(
    dossier: Path,
    *,
    jours: int | None = None,
    par_client: dict[TenantId, int | None] | None = None,
    scelle: bool = False,
    offload: int | None = None,
) -> LoomConfig:
    """La config de ``relance/``, dans un dossier à part, avec sa rétention.

    Les journaux des clients qui ont le leur sont déplacés aussi : la rétention
    supprime pour de bon, et ce n'est pas aux journaux d'exemple des autres
    phases d'en faire l'expérience.
    """
    config = load_config(CONFIG)
    events = config.storage.events.model_copy(update={"path": dossier / "events"})
    change: dict[str, Any] = {
        "events": events,
        "artifacts": ArtifactsStorage(backend="local", path=dossier / "files"),
        "idempotency": IdempotencyStorage(),
        "retention": Borne(events_days=jours),
    }
    if scelle:
        change["encryption"] = EncryptionStorage(keys=(CLE,))
    storage = config.storage.model_copy(update=change)
    bornes = par_client or {}
    tenants = tuple(
        tenant.model_copy(
            update={
                "retention": Borne(events_days=bornes[tenant.id])
                if tenant.id in bornes
                else tenant.retention,
                "storage": tenant.storage.model_copy(
                    update={
                        "events": tenant.storage.events.model_copy(
                            update={"path": dossier / "events" / tenant.id}
                        )
                    }
                )
                if tenant.storage is not None
                else None,
            }
        )
        for tenant in config.tenants
    )
    dit: dict[str, object] = {"storage": storage, "tenants": tenants}
    if offload is not None:
        outils = config.execution.tools.model_copy(update={"offload_over": offload})
        dit["execution"] = config.execution.model_copy(update={"tools": outils})
    return config.model_copy(update=dit)


def montre(report: RetentionReport) -> None:
    """Le rapport d'un balayage, tel que la commande l'imprime."""
    for tenant, jours in report.days.items():
        print(f"  {tenant:<20}{f'{jours} jour(s)' if jours is not None else 'aucune règle'}")
    for partie in report.swept:
        connu = [f"{partie.events} événement(s)"]
        if partie.artifacts is not None:
            connu.append(f"{partie.artifacts} fichier(s)")
        if partie.keys is not None:
            connu.append(f"{partie.keys} clé(s)")
        else:
            connu.append("fichiers et clés inconnus sans supprimer")
        verbe = "à effacer" if report.dry_run else "effacée"
        print(f"  {partie.session_id} ({partie.tenant_id}) {verbe} — {', '.join(connu)}")
    faites = "seraient effacées" if report.dry_run else "effacées"
    print(
        f"  → {len(report.swept)} session(s) {faites}, {report.events} événement(s) ; "
        f"{report.kept} gardée(s) sur {report.scanned} regardée(s)"
    )


# --- Cas 1 : la borne ---------------------------------------------------------


async def borne(args: argparse.Namespace, controle: Controle) -> None:
    agent = agent_de(args)
    with tempfile.TemporaryDirectory(prefix="loom-retention-") as dossier:
        # Un seuil de déport d'un caractère : le moindre résultat d'outil part
        # au stockage de fichiers, et doit s'en aller avec sa session.
        config = bornee(Path(dossier), jours=30, offload=1)
        titre("Deux sessions, une borne de 30 jours")
        dormante = session("dormante")
        vivante = session("vivante")
        async with Loom(config) as loom:
            for ou in (dormante, vivante):
                result = await loom.run(agent, demande(MARTIN), session_id=ou, tenant=MARTIN)
                print(f"  {ou} : run {result.status}")

            # Une des deux est vieillie de 90 jours : ses lignes sont réécrites
            # avec une date d'alors. C'est la seule façon d'avoir deux âges
            # différents dans un dossier neuf, et cela montre au passage ce que
            # la rétention lit — la date de la dernière écriture, rien d'autre.
            # Un journal **scellé** ne se vieillit pas ainsi : depuis 5.5c, le
            # sceau tient aussi l'horodatage, justement parce qu'une suppression
            # en dépend.
            vieilli = _vieillir(_journal(config, MARTIN, dormante), jours=90)
            print(f"\n  {dormante} vieillie de 90 jours ({vieilli} ligne(s) redatées)")

            titre("Essai à blanc : ce qui partirait, et ce qu'il coûte de le savoir")
            blanc = await loom.apply_retention(tenant_id=MARTIN)
            montre(blanc)
            print(
                "\n  seule la session dormante dépasse la borne : "
                + controle.tient(
                    "la borne n'a pas trié les sessions sur leur dernière écriture",
                    [partie.session_id for partie in blanc.swept] == [dormante]
                    and (blanc.kept, blanc.scanned) == (1, 2),
                )
            )
            print(
                "  un essai à blanc ne compte pas ce qu'il ne supprime pas : "
                + controle.tient(
                    "l'essai à blanc a prétendu connaître fichiers et clés",
                    all(p.artifacts is None and p.keys is None for p in blanc.swept),
                )
            )
            restantes = [record.session_id for record in await loom.sessions(tenant_id=MARTIN)]
            print(
                "  et rien n'a été supprimé : "
                + controle.tient("l'essai à blanc a supprimé quelque chose", len(restantes) == 2)
            )

            titre("Le balayage pour de bon, la session vivante épargnée")
            fichiers_avant = _fichiers(Path(dossier))
            fait = await loom.apply_retention(tenant_id=MARTIN, dry_run=False)
            montre(fait)
            apres = [record.session_id for record in await loom.sessions(tenant_id=MARTIN)]
            fichiers_apres = _fichiers(Path(dossier))
            print(f"\n  sessions restantes : {len(apres)} ({', '.join(apres) or 'aucune'})")
            print(f"  fichiers restants  : {fichiers_apres} (avant : {fichiers_avant})")
            print(
                "\n  le journal, les fichiers et les clés de la dormante partent ensemble : "
                + controle.tient(
                    "le balayage n'a pas tout emporté, ou a emporté la session vivante",
                    [p.session_id for p in fait.swept] == [dormante]
                    and sum(p.artifacts or 0 for p in fait.swept) == 1
                    and apres == [vivante]
                    and fichiers_apres == fichiers_avant - 1,
                )
            )

        titre("Ce que la plateforme met à l'heure")
        print("  Aucun cron dans loom, comme en 5.4c : la commande, et rien d'autre.")
        print("    0 3 * * *  cd /srv/loom && loom retention --yes >> /var/log/loom-retention.log")
        print("  Sans --yes, elle dit ce qui partirait et ne touche à rien.")


def _journal(config: LoomConfig, tenant: TenantId, session_id: SessionId) -> Path:
    """Le fichier où ce client range cette session."""
    spec = config.tenant_spec(tenant)
    storage = spec.storage if spec is not None and spec.storage is not None else config.storage
    racine = storage.events.path
    assert racine is not None
    return racine / tenant / f"{session_id}.jsonl"


def _vieillir(fichier: Path, *, jours: int) -> int:
    """Redate les lignes d'un journal **en clair**, et rend leur nombre."""
    alors = datetime.now(UTC) - timedelta(days=jours)
    lignes = [
        Event.model_validate_json(ligne).model_copy(update={"ts": alors}).model_dump_json()
        for ligne in fichier.read_text(encoding="utf-8").splitlines()
        if ligne
    ]
    fichier.write_text("\n".join(lignes) + "\n", encoding="utf-8")
    return len(lignes)


def _fichiers(dossier: Path) -> int:
    return len([path for path in (dossier / "files").rglob("*") if path.is_file()])


# --- Cas 2 : une borne par client ---------------------------------------------


async def clients(args: argparse.Namespace, controle: Controle) -> None:
    agent = agent_de(args)
    with tempfile.TemporaryDirectory(prefix="loom-retention-") as dossier:
        # Martin garde deux jours, Dupont suit la racine (30), et personne n'a
        # à écrire la même borne deux fois.
        config = bornee(Path(dossier), jours=30, par_client={MARTIN: 2})
        titre("Une borne par client, celle de la racine sinon")
        print(f"  {'racine':<20}{config.storage.retention.events_days} jour(s)")
        for tenant in (MARTIN, DUPONT):
            print(f"  {tenant:<20}{config.retention_days(tenant)} jour(s)")

        titre("Dans 10 jours : Martin a dépassé ses deux jours, Dupont pas ses trente")
        async with Loom(config) as loom:
            places = await _une_chacun(loom, agent)
            fait = await loom.apply_retention(now=dans(10), dry_run=False)
            montre(fait)
            restantes = await _restantes(loom)
        print(f"\n  restantes — Martin : {restantes[MARTIN]}, Dupont : {restantes[DUPONT]}")
        print(
            "  chaque client perd ses sessions selon sa borne : "
            + controle.tient(
                "la borne d'un client n'a pas été suivie",
                [partie.session_id for partie in fait.swept] == [places[MARTIN]]
                and restantes[MARTIN] == 0
                and restantes[DUPONT] == 1,
            )
        )

        titre("Un client peut annuler la règle commune")
        annulee = bornee(Path(dossier), jours=1, par_client={MARTIN: None})
        print("  martin-chauffage    retention: {events_days: null} → aucune règle")
        async with Loom(annulee) as loom:
            places = await _une_chacun(loom, agent)
            ailleurs = await loom.apply_retention(now=dans(1000), dry_run=False)
            montre(ailleurs)
            apres = await _restantes(loom)
        print(f"\n  restantes — Martin : {apres[MARTIN]}, Dupont : {apres[DUPONT]}")
        print(
            "  une exception s'écrit noir sur blanc, elle ne se devine pas : "
            + controle.tient(
                "l'annulation d'une règle n'a pas été lue",
                annulee.retention_days(MARTIN) is None
                and apres[MARTIN] == 1
                and apres[DUPONT] == 0
                and all(partie.tenant_id == DUPONT for partie in ailleurs.swept),
            )
        )

        titre("Un seul client, sans toucher aux autres")
        async with Loom(config) as loom:
            places = await _une_chacun(loom, agent)
            avant = await _restantes(loom)
            seul = await loom.apply_retention(tenant_id=DUPONT, now=dans(1000), dry_run=False)
            montre(seul)
            reste = await _restantes(loom)
        garde = f"{reste[MARTIN]} (avant : {avant[MARTIN]})"
        print(f"\n  restantes — Martin : {garde}, Dupont : {reste[DUPONT]}")
        print(
            "  --tenant borne le balayage à ce client : "
            + controle.tient(
                "le balayage d'un client a débordé",
                set(seul.days) == {DUPONT}
                and all(p.tenant_id == DUPONT for p in seul.swept)
                and reste[MARTIN] == avant[MARTIN]
                and avant[MARTIN] > 0
                and reste[DUPONT] == 0,
            )
        )


async def _une_chacun(loom: Loom, agent: str) -> dict[TenantId, SessionId]:
    """Une session par client, pour que chaque essai part d'un état connu."""
    places: dict[TenantId, SessionId] = {}
    for tenant in (MARTIN, DUPONT):
        ou = session(str(tenant).split("-")[0])
        places[tenant] = ou
        await loom.run(agent, demande(tenant), session_id=ou, tenant=tenant)
    return places


async def _restantes(loom: Loom) -> dict[TenantId, int]:
    return {tenant: len(await loom.sessions(tenant_id=tenant)) for tenant in (MARTIN, DUPONT)}


# --- Cas 3 : la rencontre avec le sceau ---------------------------------------


async def scelle(args: argparse.Namespace, controle: Controle) -> None:
    agent = agent_de(args)
    if find_spec("cryptography") is None:
        controle.saute("scelle — l'extra 'crypto' est absent (uv sync --extra crypto)")
        return
    with tempfile.TemporaryDirectory(prefix="loom-retention-") as dossier:
        config = bornee(Path(dossier), jours=1, scelle=True)
        avec = {**os.environ, CLE: clef(b"T")}
        titre("Un journal scellé, puis la clé s'en va")
        ou = session("scelle")
        async with Loom(config, environ=avec) as loom:
            result = await loom.run(agent, demande(MARTIN), session_id=ou, tenant=MARTIN)
            print(f"  run {result.status}, journal scellé")

        sans = {name: value for name, value in os.environ.items() if name != CLE}
        async with Loom(config, environ=sans) as perdu:
            dit = ""
            try:
                await perdu.export_session(ou, tenant_id=MARTIN)
            except SealError as error:
                dit = str(error)
            enonce("  relire   → ", dit)
            print("\n  balayer  →")
            fait = await perdu.apply_retention(now=dans(2), tenant_id=MARTIN, dry_run=False)
            montre(fait)
            restantes = await perdu.sessions(tenant_id=MARTIN)
        print(
            "\n  la place se reprend même quand le contenu est perdu : "
            + controle.tient(
                "un journal scellé sans clé n'a pas pu être effacé",
                bool(dit) and [p.session_id for p in fait.swept] == [ou] and not restantes,
            )
        )
        print("    la borne se lit sur la marque d'une session — sa dernière écriture —,")
        print("    jamais sur son contenu : c'est ce qui rend ce balayage possible.")

        titre("Et la conséquence assumée : un run inachevé part aussi")
        libre = bornee(Path(dossier), jours=1)
        inachevee = session("inachevee")
        # Un journal qui porte un run jamais fini, écrit ici à la main : aucune
        # tâche ne tourne, et c'est voulu — effacer la session d'un run **en
        # train de tourner** ferait écrire le moteur dans un journal disparu.
        lignes = _inachevee(_journal(libre, MARTIN, inachevee), inachevee)
        print(f"  {inachevee} : {lignes} événement(s), aucun 'run.completed'")
        async with Loom(libre) as loom:
            fait = await loom.apply_retention(now=dans(2), tenant_id=MARTIN, dry_run=False)
            montre(fait)
        print(
            "\n  la rétention ne lit pas le journal, donc elle ne sait pas : "
            + controle.tient(
                "une session au run inachevé a été épargnée",
                [partie.session_id for partie in fait.swept] == [inachevee],
            )
        )
        print("    une borne plus courte que le délai d'une approbation détruit des")
        print("    approbations en attente : c'est au déploiement de ne pas s'y mettre.")


def _inachevee(fichier: Path, session_id: SessionId) -> int:
    """Écrit un journal dont le run s'arrête après son premier événement."""
    scribe = RunJournal(session_id=session_id, tenant_id=MARTIN, agent="relance")
    scribe.start("Relance interrompue")
    lignes = [
        draft.to_event(seq).model_dump_json() for seq, draft in enumerate(scribe.take(), start=1)
    ]
    fichier.parent.mkdir(parents=True, exist_ok=True)
    fichier.write_text("\n".join(lignes) + "\n", encoding="utf-8")
    return len(lignes)


# --- Mise en route -----------------------------------------------------------


async def jouer(nom: str, args: argparse.Namespace, controle: Controle) -> None:
    print(f"\n{'─' * 78}\nCas {nom}\n{'─' * 78}")
    if nom == "borne":
        await borne(args, controle)
    elif nom == "clients":
        await clients(args, controle)
    else:
        await scelle(args, controle)


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="La rétention : effacer les sessions dormantes, et rien d'autre"
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
        except ImportError as error:
            print(f"Extra manquant pour le cas {nom} : {error}", file=sys.stderr)
            return 2
    return 0 if controle.bilan() else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
