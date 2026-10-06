# SPDX-License-Identifier: Apache-2.0
"""Phase 6.3 : évaluer un agent — des cas, leurs attendus, des variantes comparées.

    uv run python examples/j6/evals.py                     # tous les cas
    uv run python examples/j6/evals.py --cas suite
    uv run python examples/j6/evals.py --cas regression
    uv run --env-file .env --extra anthropic --extra openai \\
        python examples/j6/evals.py --reel

Config : celle de J4 (``examples/j4/relance/``), réglée comme
``examples/j4/acces.py`` la montait — l'outil ``envoyer_email``, irréversible et
soumis à approbation, et le rôle non terminal pour que l'orchestrateur envoie
ce qu'il a fait rédiger —, son journal déplacé dans un dossier **temporaire**.
Les fichiers de ``relance/`` ne changent pas.

* **suite** (6.3a) : une suite d'évals, écrite en YAML, jouée par
  ``Loom.evaluate``. ``loom eval suite.yaml`` fait de même en ligne de
  commande, pour une config dont les outils viennent de ses ``imports`` ; ici
  l'outil d'envoi et sa doublure vivent en Python (``register``), d'où
  l'appel par la façade, qui les prête à l'éval. Deux cas : la relance du
  devis D-2026-042, qui doit chercher le devis et envoyer l'e-mail à la
  cliente ; un devis inconnu, pour lequel rien ne doit partir. Deux variantes
  comparées : en simulé, l'orchestrateur scripté et un autre qui **oublie
  l'envoi** — la suite doit le voir, et ne voir que ça ; en ``--reel``,
  MiniMax-M3 puis Claude Haiku 4.5 à l'orchestrateur, et l'on verra ce que
  chacun fait. Le juge d'éval note la réponse finale (fidélité, ton) : un
  modèle scripté en simulé, Haiku en réel — qui note alors aussi sa propre
  variante. Le juge voit aussi le devis que l'agent a lu (``tool_results``) :
  il juge les faits, pas seulement la demande. L'envoi est **doublé** : la
  doublure répond à sa place, comme l'outil le ferait, la boîte d'envoi ne
  bouge pas, et l'approbation est accordée par l'éval, puisque rien ne part.
* **regression** (6.3b) : la non-régression depuis les traces. La même suite,
  une seule variante et sans juge d'éval, reçoit un **cas de rejeu**
  (``replay: journaux/*.jsonl``). On enregistre d'abord ses deux cas, chaque
  run exporté dans ``journaux/`` — ce que fait ``loom eval suite.yaml
  --export journaux/``. Puis le cas de rejeu est joué : avec la même config,
  chaque run se rejoue à l'identique, servi par son journal, par une instance
  qui n'a **aucune clé d'API** ; après une consigne ajoutée au prompt système
  de l'agent, chaque run s'écarte dès le premier appel de modèle, et le rapport
  dit que c'est le prompt système. ``assert_replays``, du kit de test, fait le
  même contrôle dans un test. En ``--reel``, les journaux sont ceux de vrais
  modèles, et leur rejeu n'en appelle aucun.
"""

import argparse
import asyncio
import copy
import importlib
import os
import sys
import tempfile
import textwrap
from pathlib import Path
from typing import Any

import yaml

from loom_ia.access import Loom
from loom_ia.adapters.models import ModelConfigError
from loom_ia.config import ConfigError, LoomConfig, load_config
from loom_ia.config.models import ArtifactsStorage, IdempotencyStorage
from loom_ia.core.events import ApprovalGranted, Event
from loom_ia.replay import EvalError, EvalReport, render_eval
from loom_ia.runtime import apply_logging
from loom_ia.testing import assert_replays

J4 = Path(__file__).parent.parent / "j4"
CAS = ("suite", "regression")
# Le cas de rejeu de la suite de non-régression, et ses journaux.
REJEU = "regression"
JOURNAUX = "journaux/*.jsonl"
# La retouche du prompt système que la non-régression doit voir.
CONSIGNE = " Réponds en trois phrases au plus."
DOUBLURE = "faux_envoi"
SANS_ENVOI = "FAKE_MAIN_SANS_ENVOI"
JUGE_EVAL = "FAKE_JUGE_EVAL"
INCONNU = "Relance le client du devis D-2026-999."
# Ce que la doublure a reçu, hors du journal : de quoi compter ses appels.
DOUBLES: list[str] = []


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
    for numero, ligne in enumerate(textwrap.wrap(texte, largeur - len(quoi))):
        print(f"{quoi if numero == 0 else marge}{ligne}")


# --- La config de l'exemple --------------------------------------------------------


def _acces() -> Any:
    """L'exemple de J4 — sa config, son outil d'envoi, sa boîte —, chargé par son chemin."""
    if str(J4) not in sys.path:
        sys.path.insert(0, str(J4))
    return importlib.import_module("acces")


def deplacee(config: LoomConfig, dossier: Path) -> LoomConfig:
    """La config, son journal et ses fichiers dans ``dossier``, ses clés au journal."""
    events = config.storage.events.model_copy(update={"path": dossier / "events"})
    storage = config.storage.model_copy(
        update={
            "events": events,
            "artifacts": ArtifactsStorage(backend="local", path=dossier / "files"),
            "idempotency": IdempotencyStorage(),
        }
    )
    return config.model_copy(update={"storage": storage})


def verdict(fidele: float, motif: str) -> dict[str, Any]:
    return {
        "tool_calls": [
            {
                "name": "verdict",
                "arguments": {
                    "criteria": [
                        {"name": "fidele", "score": fidele, "reason": motif},
                        {"name": "ton", "score": 0.9, "reason": "Bref et professionnel."},
                    ]
                },
            }
        ]
    }


# Le juge d'éval scripté ne tient la réponse pour fidèle que s'il voit les faits
# — le devis, ou l'erreur qui dit qu'il n'existe pas : sans les résultats de
# chercher_devis, il met 0.
JUGEMENTS: list[dict[str, Any]] = [
    {**verdict(1.0, "Conforme au devis."), "with_text": '"objet": "Remplacement'},
    {**verdict(1.0, "Conforme : le devis n'existe pas."), "with_text": "Aucun devis"},
    verdict(0.0, "Rien n'est vérifiable : aucun résultat d'outil sous les yeux."),
]


def scripte(config: LoomConfig, acces: Any) -> LoomConfig:
    """L'orchestrateur scripté selon le devis demandé, un second qui oublie l'envoi, et le
    juge d'éval."""
    relance = [{**reponse, "with_text": "D-2026-042"} for reponse in copy.deepcopy(acces.MAIN)]
    inconnu: list[dict[str, Any]] = [
        {
            "with_text": "D-2026-999",
            "text": "Je cherche le devis.",
            "tool_calls": [{"name": "chercher_devis", "arguments": {"numero": "D-2026-999"}}],
        },
        {
            "with_text": "D-2026-999",
            "text": "Le devis D-2026-999 n'est pas dans le carnet : je n'envoie rien.",
        },
    ]
    oubli = [*copy.deepcopy(relance[:2]), copy.deepcopy(relance[-1])]
    oubli[-1]["text"] = "La relance du devis D-2026-042 est prête."
    base = config.model_spec("FAKE_MAIN")
    models = tuple(
        m.model_copy(update={"params": {**m.params, "script": [*relance, *inconnu]}})
        if m.id == "FAKE_MAIN"
        else m
        for m in config.models
    )
    sans_envoi = base.model_copy(
        update={
            "id": SANS_ENVOI,
            "model": "fake-main-sans-envoi",
            "params": {**base.params, "script": [*oubli, *copy.deepcopy(inconnu)]},
        }
    )
    juge = config.model_spec("FAKE_JUDGE").model_copy(
        update={
            "id": JUGE_EVAL,
            "model": "fake-juge-eval",
            "params": {**base.params, "script": JUGEMENTS},
        }
    )
    return config.model_copy(update={"models": (*models, sans_envoi, juge)})


def sans_cles(config: LoomConfig) -> dict[str, str]:
    """L'environnement, moins toutes les clés d'API que la config nomme."""
    noms = {spec.api_key_env for spec in config.models if spec.api_key_env}
    return {nom: valeur for nom, valeur in os.environ.items() if nom not in noms}


def faux_envoi(destinataire: str, objet: str = "", corps: str = "") -> str:
    """La doublure de ``envoyer_email`` : elle répond à sa place, et rien ne part.

    Elle répond **comme l'outil** : le modèle lit sa réponse comme celle de
    l'envoi. Une doublure qui dirait « non envoyé » ferait conclure l'agent à
    une panne, et l'éval mesurerait la doublure, pas l'agent (vu au premier
    run réel, 06/10 : les deux orchestrateurs ont annoncé l'envoi en dérangement).
    """
    DOUBLES.append(destinataire)
    return f"Envoyé à {destinataire} — objet « {objet[:50]} »"


# --- La suite ------------------------------------------------------------------------------


def la_suite(acces: Any, *, reel: bool) -> dict[str, Any]:
    """La suite telle qu'on l'écrirait en YAML : deux cas, deux variantes, un juge."""
    variantes: list[dict[str, Any]] = (
        [{"name": "minimax"}, {"name": "haiku", "models": {"main": "HAIKU"}}]
        if reel
        else [{"name": "scripte"}, {"name": "sans-envoi", "models": {"main": SANS_ENVOI}}]
    )
    return {
        "name": "relance",
        "agent": "relance_reel" if reel else "relance",
        "judge": {
            "model": "HAIKU" if reel else JUGE_EVAL,
            # Le juge voit ce que l'agent a lu et fait : le devis, et l'envoi (son
            # destinataire n'est écrit que dans le prompt système, que le juge ne
            # voit pas — vu au troisième run réel, 06/10).
            "tool_results": ["chercher_devis", acces.ENVOI],
            "criteria": [
                {
                    "name": "fidele",
                    "rule": (
                        "La réponse finale n'affirme rien que ne portent la demande ou les "
                        f"résultats de chercher_devis et de {acces.ENVOI} : ni montant, ni "
                        "date, ni client, ni destinataire inventés."
                    ),
                },
                {
                    "name": "ton",
                    # Le ton demandé est celui de l'e-mail ; le juge l'avait appliqué
                    # à la réponse elle-même (troisième run réel).
                    "rule": (
                        "La réponse finale, adressée à l'artisan, est professionnelle et "
                        "dit sans détour ce qui a été fait. Le ton demandé dans la demande "
                        "vaut pour l'e-mail, pas pour cette réponse."
                    ),
                    "min_score": 0.6,
                },
            ],
        },
        "doubles": {acces.ENVOI: DOUBLURE},
        "variants": variantes,
        "cases": [
            {
                "name": "relance-042",
                "input": acces.DEMANDE,
                "expect": {
                    "status": "completed",
                    "contains": ["D-2026-042"],
                    "called": [
                        {"name": "chercher_devis", "arguments": {"numero": "D-2026-042"}},
                        {"name": acces.ENVOI, "arguments": {"destinataire": acces.CLIENTE}},
                    ],
                },
            },
            {
                "name": "devis-inconnu",
                "input": INCONNU,
                "expect": {"status": "completed", "not_called": [acces.ENVOI]},
            },
        ],
    }


async def suite(args: argparse.Namespace, controle: Controle) -> None:
    acces = _acces()
    config, agent = acces.adjusted(load_config(acces.CONFIG), reel=args.reel)
    print(f"  config : {shown(acces.CONFIG)}, réglée comme {shown(J4 / 'acces.py')}")
    print(f"  agent  : {agent}")
    with tempfile.TemporaryDirectory(prefix="loom-evals-") as dossier:
        racine = Path(dossier)
        config = deplacee(config, racine)
        if not args.reel:
            config = scripte(config, acces)
        decrite = la_suite(acces, reel=args.reel)
        fichier = racine / "suite.yaml"
        fichier.write_text(yaml.safe_dump(decrite, allow_unicode=True, sort_keys=False))
        titre("La suite, telle qu'écrite (sans config : celle de l'instance sert)")
        for ligne in fichier.read_text().splitlines():
            print(f"  {ligne}")

        titre("Jouée par Loom.evaluate")
        avant, doubles_avant = len(acces.BOITE), len(DOUBLES)
        journaux = racine / "journaux"
        # En simulé, aucune clé : l'éval n'appelle que des modèles scriptés.
        environ = None if args.reel else sans_cles(config)
        async with Loom(config, environ=environ) as loom:
            loom.register(acces.ENVOI, acces.envoyer_email)
            loom.register(DOUBLURE, faux_envoi)
            try:
                report = await loom.evaluate(fichier, export=journaux)
            except EvalError as erreur:
                print(f"  éval impossible : {erreur}")
                controle.tient(f"suite : éval impossible — {erreur}", False)
                return
            restees = await loom.sessions()
        print("\n".join(f"  {ligne}" for ligne in render_eval(report)))

        titre("Ce que l'exemple vérifie")
        _joue(report, decrite, controle)
        _envoi(report, acces, avant, doubles_avant, journaux, controle)
        _juge(report, decrite, journaux, controle)
        print(
            f"  rien n'est écrit dans le journal de l'instance ({len(restees)} session(s)) : "
            + controle.tient("suite : l'éval a écrit dans le journal de l'instance", not restees)
        )
        if args.reel:
            # Ce que font les vrais modèles ne se sait pas d'avance : le rapport
            # le dit, l'exemple ne le présume pas.
            controle.saute(
                "suite, détection (en réel, ce que font les modèles ne se sait pas d'avance)",
                partie=True,
            )
        else:
            _detection(report, acces, controle)


def _joue(report: EvalReport, decrite: dict[str, Any], controle: Controle) -> None:
    attendus = len(decrite["cases"]) * len(decrite["variants"])
    joues = [r for r in report.runs if r.skipped is None and r.error is None]
    print(
        f"  chaque cas est joué pour chaque variante ({len(joues)} run(s) sur {attendus}) : "
        + controle.tient(
            "suite : un run manque, n'est pas joué ou s'est interrompu",
            len(report.runs) == attendus == len(joues),
        )
    )
    for run in report.runs:
        if run.error is not None:
            enonce(f"    {run.variant}/{run.case} : ", run.error)


def _envoi(
    report: EvalReport,
    acces: Any,
    avant: int,
    doubles_avant: int,
    journaux: Path,
    controle: Controle,
) -> None:
    print(
        f"  la boîte d'envoi n'a pas bougé ({avant} avant, {len(acces.BOITE)} après) : "
        + controle.tient("suite : un e-mail est parti", len(acces.BOITE) == avant)
    )
    envois = [t for run in report.runs for t in run.tools if t.name == acces.ENVOI]
    if not envois:
        # Rien à protéger : l'attendu serait vrai sans rien éprouver.
        print(f"  aucun appel de {acces.ENVOI} dans la suite : la doublure n'est pas éprouvée")
        controle.saute(f"suite ({acces.ENVOI} jamais appelé : doublure non éprouvée)", partie=True)
        return
    doubles = len(DOUBLES) - doubles_avant
    print(
        f"  {acces.ENVOI} jamais exécuté, chaque appel passé par la doublure "
        f"({len(envois)} appel(s), {doubles} reçu(s) par la doublure) : "
        + controle.tient(
            f"suite : un appel de {acces.ENVOI} n'est pas passé par la doublure",
            all(t.fate == "double" for t in envois) and doubles == len(envois),
        )
    )
    accords = [
        e.payload
        for fichier in sorted(journaux.glob("*.jsonl"))
        for e in _journal(fichier)
        if isinstance(e.payload, ApprovalGranted)
    ]
    print(
        f"  chaque approbation est accordée par l'éval ({len(accords)} sur {len(envois)} "
        "envoi(s)) : "
        + controle.tient(
            "suite : une approbation manque, ou n'est pas celle de l'éval",
            len(accords) == len(envois) and all(a.by == "éval" for a in accords),
        )
    )


def _juge(report: EvalReport, decrite: dict[str, Any], journaux: Path, controle: Controle) -> None:
    criteres = len(decrite["judge"]["criteria"])
    joues = [r for r in report.runs if r.skipped is None and r.error is None]
    notes = [sum(1 for c in r.checks if c.kind == "judge") for r in joues]
    print(
        f"  le juge d'éval note chaque run joué ({criteres} critère(s) par run) : "
        + controle.tient(
            "suite : un run joué sans ses critères",
            bool(joues) and all(n == criteres for n in notes),
        )
    )
    dans_les_runs = [
        e
        for fichier in sorted(journaux.glob("*.jsonl"))
        for e in _journal(fichier)
        if e.role is not None and e.role.endswith(":eval")
    ]
    fichiers = len(list(journaux.glob("*.jsonl")))
    print(
        f"  il juge hors du run : aucun de ses appels dans les {fichiers} journaux exportés, "
        f"son coût à part ({report.summary(next(iter(report.variants))).judge_cost_usd:.6f} $ "
        "pour la première variante) : "
        + controle.tient(
            "suite : le juge d'éval a écrit dans un run, ou un journal manque",
            not dans_les_runs and fichiers == len(joues),
        )
    )


def _detection(report: EvalReport, acces: Any, controle: Controle) -> None:
    """En simulé, on sait ce que font les scripts : la suite doit voir l'oubli, et lui seul."""
    tombes = [
        (run.variant, run.case, check.label)
        for run in report.runs
        for check in run.checks
        if not check.passed
    ]
    fideles = [
        check.passed for run in report.runs for check in run.checks if "fidele" in check.label
    ]
    print(
        "  le juge d'éval a vu le devis — le juge scripté ne tient la réponse pour fidèle "
        f"qu'en le voyant ({sum(fideles)} note(s) de fidélité sur {len(fideles)}) : "
        + controle.tient(
            "suite : le juge d'éval n'a pas vu les résultats de chercher_devis",
            bool(fideles) and all(fideles),
        )
    )
    attendu = [("sans-envoi", "relance-042", f"appelle {acces.ENVOI}")]
    vus = [(v, c, label.split(" avec ")[0]) for v, c, label in tombes]
    print(
        "  la variante scriptée passe tout ; celle qui oublie l'envoi tombe sur l'envoi "
        f"de relance-042, et seulement là ({len(tombes)} contrôle(s) tombé(s)) : "
        + controle.tient(
            "suite : la suite n'a pas vu l'oubli de l'envoi, ou a vu autre chose",
            vus == attendu,
        )
    )


def _journal(path: Path) -> list[Event]:
    return [Event.model_validate_json(ligne) for ligne in path.read_text().splitlines()]


# --- Non-régression depuis les traces (6.3b) ------------------------------------------------


def la_suite_rejouee(acces: Any, *, reel: bool) -> dict[str, Any]:
    """La suite de la relance — une variante, sans juge d'éval — et un cas qui rejoue ses
    journaux."""
    decrite = {k: v for k, v in la_suite(acces, reel=reel).items() if k != "judge"}
    return {
        **decrite,
        "variants": decrite["variants"][:1],
        "cases": [*decrite["cases"], {"name": REJEU, "replay": JOURNAUX}],
    }


def allongee(config: LoomConfig, agent: str) -> LoomConfig:
    """La config, le prompt système de l'agent suivi de ``CONSIGNE``."""
    spec = next(spec for spec in config.agents if spec.name == agent)
    main = spec.main
    texte = main.system_file.read_text(encoding="utf-8") if main.system_file else main.system
    main = main.model_copy(update={"system": texte + CONSIGNE, "system_file": None})
    retouche = spec.model_copy(update={"main": main})
    agents = tuple(retouche if autre is spec else autre for autre in config.agents)
    return config.model_copy(update={"agents": agents})


async def evalue(
    config: LoomConfig,
    acces: Any,
    fichier: Path,
    environ: dict[str, str] | None,
    cas: list[str],
    export: Path | None = None,
) -> tuple[EvalReport, int]:
    """La suite jouée par une instance de ``config`` ; et combien de sessions son journal a
    reçues."""
    async with Loom(config, environ=environ) as loom:
        loom.register(acces.ENVOI, acces.envoyer_email)
        loom.register(DOUBLURE, faux_envoi)
        report = await loom.evaluate(fichier, cases=cas, export=export)
        return report, len(await loom.sessions())


def rejoues(report: EvalReport) -> list[Any]:
    return [run for run in report.runs if run.case == REJEU]


async def regression(args: argparse.Namespace, controle: Controle) -> None:
    acces = _acces()
    config, agent = acces.adjusted(load_config(acces.CONFIG), reel=args.reel)
    print(f"  config : {shown(acces.CONFIG)}, réglée comme {shown(J4 / 'acces.py')}")
    print(f"  agent  : {agent}")
    with tempfile.TemporaryDirectory(prefix="loom-regression-") as dossier:
        racine = Path(dossier)
        config = deplacee(config, racine)
        if not args.reel:
            config = scripte(config, acces)
        decrite = la_suite_rejouee(acces, reel=args.reel)
        joues = [cas["name"] for cas in decrite["cases"] if "input" in cas]
        fichier = racine / "suite.yaml"
        fichier.write_text(yaml.safe_dump(decrite, allow_unicode=True, sort_keys=False))
        titre(f"La suite : {len(joues)} cas à enregistrer, et {REJEU} qui rejoue leurs journaux")
        for ligne in fichier.read_text().splitlines():
            print(f"  {ligne}")
        boite = len(acces.BOITE)

        titre(f"Enregistrer : {', '.join(joues)}, chaque run exporté dans journaux/")
        journaux = racine / "journaux"
        try:
            # En simulé, aucune clé : l'éval n'appelle que des modèles scriptés.
            environ = None if args.reel else sans_cles(config)
            enregistre, _ = await evalue(config, acces, fichier, environ, joues, journaux)
        except EvalError as erreur:
            print(f"  éval impossible : {erreur}")
            controle.tient(f"regression : enregistrement impossible — {erreur}", False)
            return
        print("\n".join(f"  {ligne}" for ligne in render_eval(enregistre)))
        fichiers = sorted(journaux.glob("*.jsonl"))
        allees = [r for r in enregistre.runs if r.run_id is not None and r.error is None]
        print(
            f"\n  chaque run joué laisse son journal ({len(fichiers)} fichier(s) pour "
            f"{len(allees)} run(s)) : "
            + controle.tient(
                "regression : un run joué n'a pas laissé son journal",
                bool(fichiers) and len(fichiers) == len(allees),
            )
        )
        if not fichiers:
            controle.saute("regression (aucun journal enregistré : rien à rejouer)", partie=True)
            return

        titre(f"Rejouer {REJEU} avec la même config, par une instance sans aucune clé d'API")
        meme, sessions = await evalue(config, acces, fichier, sans_cles(config), [REJEU])
        print("\n".join(f"  {ligne}" for ligne in render_eval(meme)))
        runs = rejoues(meme)
        print(
            f"\n  chaque journal est rejoué ({len(runs)} run(s) pour {len(fichiers)} "
            "journal(aux)) : "
            + controle.tient(
                "regression : un journal n'a pas été rejoué",
                {r.journal for r in runs} == {f"journaux/{f.name}" for f in fichiers}
                and len(runs) == len(fichiers),
            )
        )
        print(
            "  chacun à l'identique, servi par son journal — aucun modèle appelé : "
            + controle.tient(
                "regression : un run ne se rejoue pas avec la même config",
                all(r.passed for r in runs),
            )
        )
        print(
            f"  rien n'est écrit dans le journal de l'instance ({sessions} session(s)) : "
            + controle.tient("regression : le rejeu a écrit dans le journal", sessions == 0)
        )

        titre(f"Rejouer {REJEU} après une consigne ajoutée au prompt système")
        retouchee = allongee(config, agent)
        print(f"  prompt système de {agent}, suivi de : « {CONSIGNE.strip()} »\n")
        ecart, _ = await evalue(retouchee, acces, fichier, sans_cles(retouchee), [REJEU])
        print("\n".join(f"  {ligne}" for ligne in render_eval(ecart)))
        runs = rejoues(ecart)
        au_prompt = [
            r
            for r in runs
            if r.divergence is not None
            and r.divergence.kind == "model"
            and r.divergence.rank == 1
            and r.divergence.parts == ("system",)
        ]
        print(
            "\n  chaque run s'écarte dès le premier appel de modèle, et seulement par le prompt "
            f"système ({len(au_prompt)} run(s) sur {len(runs)}) : "
            + controle.tient(
                "regression : la retouche du prompt n'est pas vue, ou pas là où elle est",
                bool(runs) and len(au_prompt) == len(runs) == len(fichiers),
            )
        )

        titre("Le même garde-fou dans un test : assert_replays")
        outils = {acces.ENVOI: acces.envoyer_email}
        await _assert(config, fichiers, outils, racine, controle, leve=False)
        await _assert(retouchee, fichiers, outils, racine, controle, leve=True)
        print(
            f"\n  la boîte d'envoi n'a pas bougé ({boite} avant, {len(acces.BOITE)} après) : "
            + controle.tient("regression : un e-mail est parti", len(acces.BOITE) == boite)
        )


async def _assert(
    config: LoomConfig,
    fichiers: list[Path],
    outils: dict[str, Any],
    racine: Path,
    controle: Controle,
    *,
    leve: bool,
) -> None:
    """``assert_replays`` sur les journaux : il doit passer (même config) ou lever (retouchée)."""
    quoi = "config retouchée" if leve else "même config"
    try:
        await assert_replays(config, *fichiers, register=outils, environ=sans_cles(config))
    except AssertionError as erreur:
        message = str(erreur)
        print(f"  {quoi} : AssertionError")
        # Le dossier temporaire, à l'affichage seulement : le contrôle lit le message entier.
        for ligne in message.replace(f"{racine}/", "").splitlines():
            marge = " " * (len(ligne) - len(ligne.lstrip()))
            enonce(f"    {marge}", ligne.lstrip())
        nommes = all(fichier.name in message for fichier in fichiers)
        print(
            f"  {quoi} : il lève, et nomme chaque journal ({len(fichiers)}) : "
            + controle.tient(
                f"regression : assert_replays, {quoi}, ne devait pas lever"
                if not leve
                else "regression : assert_replays ne nomme pas chaque journal",
                leve and nommes,
            )
        )
        return
    print(
        f"  {quoi} : {'il passe' if not leve else 'il passe, alors que le prompt a changé'} : "
        + controle.tient(f"regression : assert_replays, {quoi}, devait lever", not leve)
    )


# --- Lancement --------------------------------------------------------------------------


async def jouer(nom: str, args: argparse.Namespace, controle: Controle) -> None:
    print(f"\n{'─' * 78}\nCas {nom}\n{'─' * 78}")
    if nom == "suite":
        await suite(args, controle)
    else:
        await regression(args, controle)


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Évaluer un agent : cas, attendus, variantes")
    parser.add_argument("--reel", action="store_true", help="vrais modèles")
    parser.add_argument("--cas", action="append", choices=CAS, help="cas à jouer (tous par défaut)")
    args = parser.parse_args(argv)
    cas = tuple(args.cas) if args.cas else CAS

    try:
        config = load_config(_acces().CONFIG)
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
