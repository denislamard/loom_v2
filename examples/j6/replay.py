# SPDX-License-Identifier: Apache-2.0
"""Phase 6.2a : rejouer un run à l'identique, sans appeler personne, et voir où il diverge.

    uv run python examples/j6/replay.py                       # les trois cas
    uv run python examples/j6/replay.py --cas identique
    uv run --extra sqlite python examples/j6/replay.py --cas j4    # tes runs de J4, rejoués
    uv run --env-file .env --extra anthropic --extra openai \\
        python examples/j6/replay.py --reel

Config des cas ``identique`` et ``divergence`` : ``examples/j5/relance/`` (la
relance de devis), journal dans un dossier **temporaire**. Le cas ``j4`` a la
sienne : celle de J4, réglée comme ``examples/j4/acces.py`` la montait. Le
rejeu ne parle à aucun fournisseur : aucun extra de modèle n'est demandé pour
rejouer. Le cas ``j4`` demande l'extra ``sqlite`` (la config de J4 y range ses
clés d'idempotence) et se saute sans lui.

* **identique** : deux runs dans une session — le second a un historique à
  reconstruire —, puis chacun rejoué par une instance qui **n'a aucune clé
  d'API** : si un rejeu appelait un modèle, il ne pourrait pas même le monter.
  Chaque appel de modèle et d'outil est refait, le journal n'a pas bougé.
* **divergence** : la même config, une phrase de plus dans un prompt. Au prompt
  de l'orchestrateur, le rejeu s'arrête au premier appel et dit que c'est le
  prompt système qui a changé ; au prompt du rôle, il rejoue les appels d'avant
  et s'arrête à celui du rôle.
* **j4** : le critère de sortie de J6 — les runs que ``examples/j4/acces.py``
  a laissés dans **ton** journal de J4 (``examples/j4/relance/data``), réels si
  tu les as lancés en ``--reel``, rejoués avec la config que cet exemple
  montait. Sans eux, le cas se saute, et le bilan le dit. Ce cas ne lit que :
  il n'enregistre rien et ne demande ni ``--reel`` ni clé.
"""

import argparse
import asyncio
import importlib
import os
import sys
import tempfile
import textwrap
from importlib.util import find_spec
from pathlib import Path
from typing import Any

from loom_ia.access import Loom
from loom_ia.adapters.models import ModelConfigError
from loom_ia.config import ConfigError, LoomConfig, load_config
from loom_ia.config.models import ArtifactsStorage, IdempotencyStorage
from loom_ia.core.model import RunId, SessionId, TenantId, new_id
from loom_ia.replay import ReplayReport
from loom_ia.runtime import apply_logging

CONFIG = Path(__file__).parent.parent / "j5" / "relance" / "loom.yaml"
CAS = ("identique", "divergence", "j4")
DUPONT = TenantId("dupont-plomberie")
DEMANDES = (
    "Relance le client du devis D-2026-042, sur un ton cordial.",
    "Refais-la sur un ton plus ferme, pour le devis D-2026-042.",
)
AJOUT = "Réponds toujours en français soutenu."


def shown(path: Path) -> str:
    return os.path.relpath(path)


def titre(texte: str) -> None:
    print(f"\n=== {texte} ===")


def agent_de(args: argparse.Namespace) -> str:
    return "relance_reel" if args.reel else "relance"


def annonce(config: Path, agent: str) -> None:
    """La config et l'agent du cas : chaque cas dit les siens."""
    print(f"  config : {shown(config)}")
    print(f"  agent  : {agent}")


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

    def bilan(self, joues: int) -> bool | None:
        """Vrai si tout a tenu, faux sinon ; ``None`` si rien n'a été joué."""
        for saute in self.sautes:
            print(f"\nCas sauté : {saute}")
        if joues == len(self.sautes):
            print("\nAucun cas joué : l'exemple n'a rien éprouvé.")
            return None
        if not self.ecarts:
            dit = "Chaque essai joué a rendu" if self.sautes else "Chaque essai a rendu"
            print(f"\n{dit} ce que l'exemple annonçait.")
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


# --- La config de l'exemple ------------------------------------------------------


def deplacee(
    dossier: Path, *, prompt_main: str | None = None, prompt_role: str | None = None
) -> LoomConfig:
    """La config de ``relance/``, journal dans ``dossier`` ; un prompt changé si demandé.

    Le prompt changé est celui du fichier **plus une phrase** : c'est la plus
    petite modification qui change une requête.
    """
    config = load_config(CONFIG)
    events = config.storage.events.model_copy(update={"path": dossier / "events"})
    storage = config.storage.model_copy(
        update={
            "events": events,
            "artifacts": ArtifactsStorage(backend="local", path=dossier / "files"),
            "idempotency": IdempotencyStorage(),
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
                if tenant.storage is not None
                else None
            }
        )
        for tenant in config.tenants
    )
    agents = tuple(_retouche(agent, prompt_main, prompt_role) for agent in config.agents)
    return config.model_copy(update={"storage": storage, "tenants": tenants, "agents": agents})


def _retouche(agent: Any, prompt_main: str | None, prompt_role: str | None) -> Any:
    change: dict[str, Any] = {}
    if prompt_main is not None and agent.main.system_file is not None:
        texte = Path(agent.main.system_file).read_text(encoding="utf-8")
        change["main"] = agent.main.model_copy(
            update={"system_file": None, "system": f"{texte}\n{prompt_main}"}
        )
    if prompt_role is not None and agent.roles:
        role = agent.roles[0]
        texte = Path(role.system_file).read_text(encoding="utf-8")
        change["roles"] = (
            role.model_copy(update={"system_file": None, "system": f"{texte}\n{prompt_role}"}),
            *agent.roles[1:],
        )
    return agent.model_copy(update=change) if change else agent


def sans_cles(config: LoomConfig) -> dict[str, str]:
    """L'environnement, moins toutes les clés d'API que la config nomme."""
    noms = {spec.api_key_env for spec in config.models if spec.api_key_env}
    return {nom: valeur for nom, valeur in os.environ.items() if nom not in noms}


async def enregistrer(
    config: LoomConfig, args: argparse.Namespace, session: SessionId
) -> list[str]:
    """Les deux runs de la session, comme d'habitude : ce sont eux qu'on rejouera."""
    ids: list[str] = []
    async with Loom(config) as loom:
        for demande in DEMANDES:
            result = await loom.run(agent_de(args), demande, session_id=session, tenant=DUPONT)
            print(f"  run {result.run_id} : {result.status}")
            ids.append(result.run_id)
    return ids


def montre(report: ReplayReport) -> None:
    print(f"  issue          : {report.original_end} au journal, {report.replay_end} au rejeu")
    print(
        f"  appels         : {report.model_calls[0]} de modèle au journal, "
        f"{report.model_calls[1]} servi(s) au rejeu ; {report.tool_calls[0]} d'outil "
        f"au journal, {report.tool_calls[1]} servi(s)"
    )
    divergence = report.divergence
    if divergence is None:
        print("  verdict        : identique")
        return
    enonce("  divergence     : ", divergence.where)
    if divergence.detail:
        enonce("                   ", divergence.detail)


# --- Cas 1 : identique --------------------------------------------------------------


async def identique(args: argparse.Namespace, controle: Controle) -> None:
    with tempfile.TemporaryDirectory(prefix="loom-replay-") as dossier:
        config = deplacee(Path(dossier))
        session = SessionId(f"replay-{new_id()[-8:]}")
        annonce(CONFIG, agent_de(args))
        titre("Deux runs enregistrés")
        ids = await enregistrer(config, args, session)
        environ = sans_cles(config)
        retirees = sorted(set(os.environ) - set(environ))
        titre(
            "Rejoués par une instance sans clé d'API"
            + (f" ({', '.join(retirees)} retirées)" if retirees else "")
        )
        async with Loom(config, environ=environ) as loom:
            avant = len(await loom.export_session(session, tenant_id=DUPONT))
            for rang, run_id in enumerate(ids, 1):
                print(f"\n  run {rang} ({run_id})")
                report = await loom.replay(RunId(run_id), session_id=session, tenant_id=DUPONT)
                montre(report)
                print(
                    "  rejoué à l'identique : "
                    + controle.tient(f"identique : le run {rang} a divergé", report.identical)
                )
                print(
                    "  chaque appel refait, ni plus ni moins : "
                    + controle.tient(
                        f"identique : le run {rang} n'a pas refait les mêmes appels",
                        report.model_calls[0] == report.model_calls[1] > 0
                        and report.tool_calls[0] == report.tool_calls[1] > 0,
                    )
                )
            apres = len(await loom.export_session(session, tenant_id=DUPONT))
        print(
            f"\n  le journal n'a pas bougé ({avant} événement(s) avant, {apres} après) : "
            + controle.tient("identique : le rejeu a écrit dans le journal", avant == apres)
        )


# --- Cas 2 : divergence ---------------------------------------------------------------


async def divergence(args: argparse.Namespace, controle: Controle) -> None:
    with tempfile.TemporaryDirectory(prefix="loom-replay-") as dossier:
        config = deplacee(Path(dossier))
        session = SessionId(f"replay-{new_id()[-8:]}")
        annonce(CONFIG, agent_de(args))
        titre("Deux runs enregistrés ; on rejoue le premier")
        [run_id, _] = await enregistrer(config, args, session)
        for ou, change in (
            ("orchestrateur", {"prompt_main": AJOUT}),
            ("rôle rediger_relance", {"prompt_role": AJOUT}),
        ):
            titre(f"Une phrase de plus au prompt — {ou}")
            enonce("  ajoutée : ", AJOUT)
            modifiee = deplacee(Path(dossier), **change)
            async with Loom(modifiee, environ=sans_cles(modifiee)) as loom:
                report = await loom.replay(RunId(run_id), session_id=session, tenant_id=DUPONT)
            montre(report)
            trouvee = report.divergence
            print(
                "  la divergence est vue, sur un appel de modèle : "
                + controle.tient(
                    f"divergence ({ou}) : aucune divergence, ou pas sur un appel de modèle",
                    trouvee is not None and trouvee.kind == "model",
                )
            )
            print(
                "  elle nomme le prompt système, et lui seul : "
                + controle.tient(
                    f"divergence ({ou}) : la partie nommée n'est pas le prompt système",
                    trouvee is not None and trouvee.parts == ("system",),
                )
            )
            if "prompt_main" in change:
                print(
                    "  au premier appel, rien n'est rejoué au-delà : "
                    + controle.tient(
                        "divergence (orchestrateur) : des appels ont été rejoués",
                        trouvee is not None
                        and "n°1 " in trouvee.where
                        and report.model_calls[1] == 0,
                    )
                )
            else:
                print(
                    f"  à l'appel du rôle, après {report.model_calls[1]} appel(s) servi(s) : "
                    + controle.tient(
                        "divergence (rôle) : pas à l'appel du rôle, ou rien de rejoué avant",
                        trouvee is not None
                        and "rediger_relance au journal" in trouvee.where
                        and report.model_calls[1] > 0,
                    )
                )


# --- Cas 3 : les runs de J4 ---------------------------------------------------------


J4 = Path(__file__).parent.parent / "j4"
# Sessions que ``examples/j4/acces.py`` écrit : ``<préfixe>-<cas>``.
CAS_J4 = ("python", "rest", "mcp-elicite", "mcp-pause")


async def j4(args: argparse.Namespace, controle: Controle) -> None:
    """Les runs que ``examples/j4/acces.py`` a laissés dans son journal, rejoués.

    Ce journal-là est le tien : il porte les runs de tes passages de J4 —
    réels si tu les as lancés avec ``--reel``. La config est celle que
    l'exemple de J4 montait (outil d'envoi en plus, rôle non terminal) : c'est
    elle qui a produit ces requêtes, c'est donc elle qui doit les reproduire.
    """
    if find_spec("aiosqlite") is None:
        # La config de J4 range ses clés d'idempotence dans SQLite.
        print("  extra 'sqlite' absent : cas non joué")
        controle.saute("j4 (extra 'sqlite' absent : uv run --extra sqlite …)")
        return
    # L'exemple de J4 lui-même, pour sa config et son outil d'envoi : un module
    # voisin, chargé par son chemin.
    sys.path.insert(0, str(J4))
    acces: Any = importlib.import_module("acces")

    base = load_config(acces.CONFIG)
    print(f"  config : {shown(acces.CONFIG)}, réglée comme {shown(J4 / 'acces.py')}")
    print("  agent  : celui de chaque run, lu au journal")
    if args.reel:
        print("  --reel : sans effet ici, ce cas ne fait que relire")
    rangees = await _sessions_j4(base)
    if not rangees:
        print(f"  aucune session de examples/j4/acces.py dans {shown(J4 / 'relance')}")
        controle.saute("j4 (lancer d'abord : uv run … python examples/j4/acces.py)")
        return
    titre(f"{len(rangees)} run(s) de J4, rejoués par une instance sans clé d'API")
    identiques = 0
    for session, run_id, agent in rangees:
        config, _ = acces.adjusted(base, reel=agent == "relance_reel")
        async with Loom(config, environ=sans_cles(config)) as loom:
            loom.register(acces.ENVOI, acces.envoyer_email)
            report = await loom.replay(RunId(run_id), session_id=SessionId(session))
        print(f"\n  {session} — run {run_id} (agent {report.agent})")
        montre(report)
        identiques += report.identical
        controle.tient(f"j4 : le run {run_id} ({session}) a divergé", report.identical)
    print(
        f"\n  rejoués à l'identique : {identiques} sur {len(rangees)} : "
        + ("oui" if identiques == len(rangees) else "NON")
    )


async def _sessions_j4(base: LoomConfig) -> list[tuple[str, str, str]]:
    """``(session, run, agent)`` des runs racine finis des sessions de l'exemple de J4."""
    trouves: list[tuple[str, str, str]] = []
    async with Loom(base, environ=sans_cles(base)) as loom:
        for record in await loom.sessions():
            if not record.session_id.startswith("atelier-") or not record.session_id.endswith(
                tuple(f"-{cas}" for cas in CAS_J4)
            ):
                continue
            for event in await loom.export_session(record.session_id):
                if event.type == "run.started" and event.payload.parent_run_id is None:  # type: ignore[union-attr]
                    trouves.append((record.session_id, event.run_id, event.agent or ""))
    return trouves


# --- Lancement --------------------------------------------------------------------------


async def jouer(nom: str, args: argparse.Namespace, controle: Controle) -> None:
    print(f"\n{'─' * 78}\nCas {nom}\n{'─' * 78}")
    if nom == "identique":
        await identique(args, controle)
    elif nom == "divergence":
        await divergence(args, controle)
    else:
        await j4(args, controle)


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Rejouer un run à l'identique, et voir où il diverge"
    )
    parser.add_argument("--reel", action="store_true", help="vrais modèles pour enregistrer")
    parser.add_argument("--cas", action="append", choices=CAS, help="cas à jouer (tous par défaut)")
    args = parser.parse_args(argv)
    cas = tuple(args.cas) if args.cas else CAS

    try:
        config = load_config(CONFIG)
    except ConfigError as error:
        print(f"Configuration : {error}", file=sys.stderr)
        return 2
    apply_logging(config)
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
