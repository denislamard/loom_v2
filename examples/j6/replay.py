# SPDX-License-Identifier: Apache-2.0
"""Phases 6.2a et 6.2b : rejouer un run, à l'identique ou en variante, et voir où il s'écarte.

    uv run python examples/j6/replay.py                       # les quatre cas
    uv run python examples/j6/replay.py --cas identique
    uv run python examples/j6/replay.py --cas variante
    uv run --extra sqlite python examples/j6/replay.py --cas j4    # tes runs de J4, rejoués
    uv run --env-file .env --extra anthropic --extra openai \\
        python examples/j6/replay.py --reel

Config des cas ``identique`` et ``divergence`` : ``examples/j5/relance/`` (la
relance de devis), journal dans un dossier **temporaire**. Les cas
``variante`` et ``j4`` ont la leur : celle de J4, réglée comme
``examples/j4/acces.py`` la montait (l'outil ``envoyer_email``, irréversible et
soumis à approbation) ; ``variante`` la déplace elle aussi dans un dossier
temporaire. Le rejeu identique ne parle à aucun fournisseur ; la variante, si.
Le cas ``j4`` demande l'extra ``sqlite`` (la config de J4 y range ses clés
d'idempotence) et se saute sans lui.

* **identique** : deux runs dans une session — le second a un historique à
  reconstruire —, puis chacun rejoué par une instance qui **n'a aucune clé
  d'API** : si un rejeu appelait un modèle, il ne pourrait pas même le monter.
  Chaque appel de modèle et d'outil est refait, le journal n'a pas bougé.
* **divergence** : la même config, une phrase de plus dans un prompt. Au prompt
  de l'orchestrateur, le rejeu s'arrête au premier appel et dit que c'est le
  prompt système qui a changé ; au prompt du rôle, il rejoue les appels d'avant
  et s'arrête à celui du rôle.
* **variante** : un run de la relance de J4 enregistré — l'e-mail approuvé
  par l'artisan, et parti —, puis rejoué avec un **autre modèle** à
  l'orchestrateur. En simulé, deux autres orchestrateurs scriptés : l'un
  refait le même envoi, que la variante **lit au journal** avec sa décision
  d'approbation ; l'autre change l'objet de l'e-mail, et l'envoi est
  **refusé**. En ``--reel``, Claude Haiku 4.5 remplace MiniMax-M3, et l'on
  verra ce qu'il fait. Dans tous les cas, la boîte d'envoi ne bouge pas.
* **j4** : le critère de sortie de J6 — les runs que ``examples/j4/acces.py``
  a laissés dans **ton** journal de J4 (``examples/j4/relance/data``), réels si
  tu les as lancés en ``--reel``, rejoués avec la config que cet exemple
  montait. Sans eux, le cas se saute, et le bilan le dit. Ce cas ne lit que :
  il n'enregistre rien et ne demande ni ``--reel`` ni clé.
"""

import argparse
import asyncio
import copy
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
from loom_ia.core.events import ApprovalGranted
from loom_ia.core.model import RunId, SessionId, TenantId, new_id
from loom_ia.replay import Comparison, ReplayReport, ToolFate, fate_label
from loom_ia.runtime import apply_logging

CONFIG = Path(__file__).parent.parent / "j5" / "relance" / "loom.yaml"
CAS = ("identique", "divergence", "variante", "j4")
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
    dossier: Path,
    *,
    depuis: LoomConfig | None = None,
    prompt_main: str | None = None,
    prompt_role: str | None = None,
) -> LoomConfig:
    """La config de ``relance/`` (ou ``depuis``), journal dans ``dossier`` ; un prompt
    changé si demandé.

    Le prompt changé est celui du fichier **plus une phrase** : c'est la plus
    petite modification qui change une requête.
    """
    config = depuis if depuis is not None else load_config(CONFIG)
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
        for ou, au_role in (("orchestrateur", False), ("rôle rediger_relance", True)):
            titre(f"Une phrase de plus au prompt — {ou}")
            enonce("  ajoutée : ", AJOUT)
            modifiee = (
                deplacee(Path(dossier), prompt_role=AJOUT)
                if au_role
                else deplacee(Path(dossier), prompt_main=AJOUT)
            )
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
            if not au_role:
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


# --- Cas 3 : variante ----------------------------------------------------------------


J4 = Path(__file__).parent.parent / "j4"
# Orchestrateurs simulés de la variante, ajoutés à la config de J4.
MEME_ENVOI = "FAKE_MAIN_MEME"
AUTRE_ENVOI = "FAKE_MAIN_AUTRE"
OBJET_CHANGE = "Rappel : votre devis D-2026-042"


def _acces() -> Any:
    """L'exemple de J4 — sa config, son outil d'envoi, sa boîte —, chargé par son chemin."""
    if str(J4) not in sys.path:
        sys.path.insert(0, str(J4))
    return importlib.import_module("acces")


def _orchestrateurs(config: LoomConfig, acces: Any) -> LoomConfig:
    """Deux orchestrateurs simulés de plus : le même envoi, puis un objet changé."""
    base = config.model_spec("FAKE_MAIN")
    meme = copy.deepcopy(acces.MAIN)
    meme[-1] = {"text": "Relance partie (autre orchestrateur, même envoi)."}
    autre = copy.deepcopy(acces.MAIN)
    autre[2]["tool_calls"][0]["arguments"]["objet"] = OBJET_CHANGE
    autre[-1] = {"text": "L'envoi de la relance n'a pas abouti."}
    ajoutes = tuple(
        base.model_copy(
            update={"id": ident, "model": modele, "params": {**base.params, "script": script}}
        )
        for ident, modele, script in (
            (MEME_ENVOI, "fake-main-meme", meme),
            (AUTRE_ENVOI, "fake-main-autre", autre),
        )
    )
    return config.model_copy(update={"models": (*config.models, *ajoutes)})


def compare(report: ReplayReport, comparison: Comparison) -> None:
    """Le run d'origine et sa variante, côte à côte — chaque chiffre lu dans le rapport."""
    origine, variante_ = comparison.original, comparison.variant
    divergence = report.divergence
    if divergence is None:
        print("  écart          : aucun")
    else:
        enonce("  écart          : ", divergence.where)
        if divergence.detail:
            enonce("                   ", divergence.detail)
    print(f"  issue          : {origine.end} à l'origine, {variante_.end} en variante")
    print(
        f"  modèles        : {origine.model_calls} appel(s) à l'origine ; en variante "
        f"{variante_.model_calls}, dont {comparison.served_models} servi(s) par le journal "
        f"et {comparison.real_models} parti(s) pour de vrai"
    )
    for outil, sort in comparison.calls:
        print(f"  outil          : {outil} — {fate_label(sort)}")
    print(
        f"  coût           : {origine.cost_usd:.6f} $ à l'origine, {variante_.cost_usd:.6f} $ "
        f"en variante, dont {comparison.spent_usd:.6f} $ dépensés pour de vrai"
    )
    enonce("  réponse (orig.): ", origine.text or "(aucune)")
    enonce("  réponse (var.) : ", variante_.text or "(aucune)")


async def variante(args: argparse.Namespace, controle: Controle) -> None:
    acces = _acces()
    config, agent = acces.adjusted(load_config(acces.CONFIG), reel=args.reel)
    print(f"  config : {shown(acces.CONFIG)}, réglée comme {shown(J4 / 'acces.py')}")
    print(f"  agent  : {agent}")
    with tempfile.TemporaryDirectory(prefix="loom-variante-") as dossier:
        config = deplacee(Path(dossier), depuis=config)
        session = SessionId(f"variante-{new_id()[-8:]}")
        # Le titre ne dit rien d'avance : en réel, rien ne garantit que le
        # modèle demande l'envoi. Ce qui s'est passé se lit dessous.
        titre("Le run d'origine")
        async with Loom(config) as loom:
            loom.register(acces.ENVOI, acces.envoyer_email)
            result = await loom.run(agent, acces.DEMANDE, session_id=session)
            accordes: tuple[str, ...] = ()
            if result.pending_approvals:
                accordes = await loom.approve(
                    result.run_id, by=acces.ARTISAN, reason="devis vérifié", session_id=session
                )
                await loom.drain()
            fin = await loom.result(result.run_id, session_id=session)
        print(f"  run {result.run_id} : {fin.status}")
        print(f"  approbation : {len(accordes)} appel(s) accordé(s) par « {acces.ARTISAN} »")
        print(f"  boîte d'envoi : {len(acces.BOITE)} e-mail(s) parti(s)")
        if str(fin.status) not in ("completed", "failed", "cancelled"):
            # Un rejeu compare un run fini.
            print("  le run n'est pas fini : rien à rejouer")
            controle.saute(f"variante (run d'origine {fin.status}, non rejouable)")
            return

        essais: list[tuple[str, str, ToolFate | None]]
        if args.reel:
            essais = [
                ("Claude Haiku 4.5 à l'orchestrateur, à la place de MiniMax-M3", "HAIKU", None)
            ]
        else:
            config = _orchestrateurs(config, acces)
            essais = [
                ("un autre orchestrateur, qui refait le même envoi", MEME_ENVOI, "journal"),
                ("un autre orchestrateur, qui change l'objet de l'e-mail", AUTRE_ENVOI, "refused"),
            ]
        for quoi, modele, attendu in essais:
            titre(f"Variante : {quoi}")
            avant = len(acces.BOITE)
            # En simulé, aucune clé : la variante n'appelle que des modèles scriptés.
            environ = None if args.reel else sans_cles(config)
            async with Loom(config, environ=environ) as loom:
                loom.register(acces.ENVOI, acces.envoyer_email)
                report = await loom.replay(
                    result.run_id, session_id=session, mode="variant", models={"main": modele}
                )
            comparison = report.comparison
            assert comparison is not None
            compare(report, comparison)
            _attendus(report, comparison, acces, avant, attendu, controle)


def _attendus(
    report: ReplayReport,
    comparison: Comparison,
    acces: Any,
    avant: int,
    attendu: ToolFate | None,
    controle: Controle,
) -> None:
    divergence = report.divergence
    print(
        "  la variante quitte l'origine au premier appel, et dit que le modèle a changé : "
        + controle.tient(
            "variante : pas d'écart au premier appel, ou le modèle n'y est pas nommé",
            divergence is not None
            and divergence.kind == "model"
            and "n°1 " in divergence.where
            and "model" in divergence.parts,
        )
    )
    print(
        "  chaque appel de modèle de la variante est compté, servi ou parti : "
        + controle.tient(
            "variante : les appels servis et partis ne font pas le compte",
            comparison.served_models + comparison.real_models == comparison.variant.model_calls
            and comparison.real_models > 0,
        )
    )
    print(
        "  la dépense n'est que celle des appels partis : "
        + controle.tient(
            "variante : une dépense sans appel parti, ou l'inverse",
            (comparison.real_models > 0) == (comparison.spent_usd > 0),
        )
    )
    envois: list[ToolFate] = [sort for outil, sort in comparison.calls if outil == acces.ENVOI]
    print(
        f"  la boîte d'envoi n'a pas bougé ({avant} avant, {len(acces.BOITE)} après) : "
        + controle.tient("variante : un e-mail est reparti", len(acces.BOITE) == avant)
    )
    if not envois:
        # Rien à protéger : l'attendu serait vrai sans rien éprouver.
        print(f"  l'orchestrateur n'a pas appelé {acces.ENVOI} : l'envoi n'est pas éprouvé")
        controle.saute(f"variante ({acces.ENVOI} jamais appelé : envoi non éprouvé)")
        return
    print(
        f"  {acces.ENVOI} lu au journal ou refusé, jamais exécuté "
        f"({', '.join(fate_label(s) for s in envois)}) : "
        + controle.tient(
            f"variante : {acces.ENVOI} exécuté ou doublé",
            all(sort in ("journal", "refused") for sort in envois),
        )
    )
    if attendu is None:
        return
    print(
        f"  ici, {fate_label(attendu)} : "
        + controle.tient(
            f"variante : {acces.ENVOI} n'a pas été {fate_label(attendu)}",
            envois == [attendu],
        )
    )
    if attendu == "journal":
        accords = [e.payload.by for e in report.events if isinstance(e.payload, ApprovalGranted)]
        print(
            f"  avec la décision du journal (accordé par {', '.join(map(str, accords))}) : "
            + controle.tient(
                "variante : la décision d'approbation ne vient pas du journal",
                accords == [acces.ARTISAN],
            )
        )


# --- Cas 4 : les runs de J4 ---------------------------------------------------------
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
    acces = _acces()

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
    elif nom == "variante":
        await variante(args, controle)
    else:
        await j4(args, controle)


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Rejouer un run, à l'identique ou en variante, et voir où il s'écarte"
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
