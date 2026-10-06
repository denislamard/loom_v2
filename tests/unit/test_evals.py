# SPDX-License-Identifier: Apache-2.0
"""Évals : une suite de cas, leurs attendus, des variantes comparées (O1, J6.3a).

Ce qui s'éprouve ici :

- la suite se lit et refuse ce qui n'éprouverait rien (cas sans attendu,
  critères sans juge, noms en double, motif invalide) ;
- les contrôles disent ce qu'ils ont trouvé — statut, texte, champ, outils ;
- un outil à effets de bord n'est **jamais** exécuté : doublé (avec les
  arguments résolus) ou refusé, l'approbation accordée puisque rien ne part ;
  un outil sans effets de bord s'exécute ; un sous-agent tourne, ses outils
  interceptés comme ceux du parent ;
- les variantes (autre modèle par étape, autre config) se comparent ;
- le juge d'éval note hors du run, son coût à part ; un juge en échec fait
  tomber ses critères en le disant ;
- rien n'entre dans le journal de l'instance ni dans les quotas de ses
  clients ; ``export`` garde un journal par run ; le plafond de dépense arrête
  les runs suivants ;
- ``loom eval`` : 0, 1 ou 2, ``--json``, ``--case``.
"""

import asyncio
import json
import logging
from collections.abc import Iterator
from importlib.util import find_spec
from pathlib import Path
from typing import Any

import pytest
import yaml

from loom_ia.access import Loom
from loom_ia.access.cli import main as cli_main
from loom_ia.access.evals import evaluate
from loom_ia.config import ConfigError, load_config
from loom_ia.core.events import ApprovalGranted, Event, ToolCompleted
from loom_ia.core.model import (
    Approved,
    ModelSpec,
    PendingApproval,
    PendingCall,
    Rejected,
    RunId,
    RunStatus,
)
from loom_ia.replay import (
    EvalCriterion,
    EvalError,
    EvalJudgeClient,
    EvalSuite,
    EvalTools,
    Expect,
    Outcome,
    ToolUse,
    check,
    isolated,
    load_suite,
    render_eval,
)
from loom_ia.testing import ScriptedModel, tool_call_message
from loom_ia.tools import tool

OUTILS = '''
import os
from pathlib import Path

from loom_ia.tools import tool


@tool
def calculer(expr: str) -> str:
    """Calcule une expression."""
    return str(eval(expr))


@tool
async def envoyer(destinataire: str) -> str:
    """Envoie la relance : chaque envoi laisse une ligne dans la boîte."""
    with Path(os.environ["EVAL_BOITE"]).open("a", encoding="utf-8") as boite:
        boite.write(destinataire + "\\n")
    return f"envoyé à {destinataire}"
'''

DOUBLURES = '''
import os
from pathlib import Path


def faux_envoi(destinataire: str) -> str:
    """Ce que reçoit le modèle à la place d'un envoi ; ce qu'elle a reçu, noté à part."""
    with Path(os.environ["EVAL_DOUBLES"]).open("a", encoding="utf-8") as vus:
        vus.write(str(destinataire) + "\\n")
    return f"(doublure) envoi à {destinataire}"


PAS_UNE_FONCTION = 3
'''

RELANCE = "Relance martin."
CALCUL = "Combien font 2 + 2 ?"
VERDICT: dict[str, Any] = {
    "tool_calls": [
        {
            "name": "verdict",
            "arguments": {
                "criteria": [
                    {"name": "fidele", "score": 1.0, "reason": "Rien d'inventé."},
                    {"name": "ton", "score": 0.5, "reason": "Un peu sec."},
                ]
            },
        }
    ]
}


def verdict(note: float) -> dict[str, Any]:
    return {
        "tool_calls": [
            {
                "name": "verdict",
                "arguments": {
                    "criteria": [
                        {"name": "fidele", "score": note, "reason": f"Note {note}."},
                        {"name": "ton", "score": note, "reason": f"Note {note}."},
                    ]
                },
            }
        ]
    }


# Le juge ``VOIT`` ne met 1 que s'il voit le résultat du calcul (87) : 0 sinon.
VERDICT_PLEIN = verdict(1.0)
VERDICT_NUL = verdict(0.0)


def orchestrateur(fin: str) -> list[dict[str, Any]]:
    """Calcule puis envoie, pour la relance ; calcule seulement, pour le calcul."""
    return [
        {
            "with_text": "Relance",
            "tool_calls": [{"name": "calculer", "arguments": {"expr": "12*7+3"}}],
        },
        {
            "with_text": "Relance",
            "tool_calls": [{"name": "envoyer", "arguments": {"destinataire": "martin"}}],
        },
        {"with_text": "Relance", "text": fin},
        {
            "with_text": "Combien",
            "tool_calls": [{"name": "calculer", "arguments": {"expr": "2+2"}}],
        },
        {"with_text": "Combien", "text": "2 + 2 = 4."},
    ]


@pytest.fixture
def labo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Un agent qui calcule et envoie (irréversible, sous approbation) ; son journal en JSONL.

    ``MAIN`` conclut « Relance envoyée à martin. », ``AUTRE`` « Relance faite. » ;
    ``REF`` envoie à la référence du calcul. ``JUGE`` note fidele 1,0 et ton
    0,5 ; ``MUET`` ne rend pas de verdict. Tous ont un prix.
    """
    (tmp_path / "agents").mkdir()
    (tmp_path / "outils_labo.py").write_text(OUTILS, encoding="utf-8")
    (tmp_path / "doublures_labo.py").write_text(DOUBLURES, encoding="utf-8")
    monkeypatch.setenv("EVAL_BOITE", str(tmp_path / "boite.txt"))
    monkeypatch.setenv("EVAL_DOUBLES", str(tmp_path / "doubles.txt"))
    prix = {"input": 1.0, "output": 2.0}
    ref = orchestrateur("Relance envoyée au résultat.")
    ref[1] = {
        "with_text": "Relance",
        "tool_calls": [{"name": "envoyer", "arguments": {"destinataire": {"$ref": "result:1"}}}],
    }
    modeles: list[dict[str, Any]] = [
        {
            "id": "MAIN",
            "sdk": "fake",
            "model": "main-1",
            "pricing": prix,
            "params": {"script": orchestrateur("Relance envoyée à martin.")},
        },
        {
            "id": "AUTRE",
            "sdk": "fake",
            "model": "main-2",
            "pricing": prix,
            "params": {"script": orchestrateur("Relance faite.")},
        },
        {"id": "REF", "sdk": "fake", "model": "main-3", "pricing": prix, "params": {"script": ref}},
        {
            "id": "JUGE",
            "sdk": "fake",
            "model": "juge-1",
            "pricing": prix,
            "params": {"script": [VERDICT]},
        },
        {
            "id": "VOIT",
            "sdk": "fake",
            "model": "juge-3",
            "params": {
                "script": [
                    {**VERDICT_PLEIN, "with_text": '<tool_result tool="calculer">\n87'},
                    VERDICT_NUL,
                ]
            },
        },
        {
            "id": "MUET",
            "sdk": "fake",
            "model": "juge-2",
            "params": {"script": [{"text": "Je ne sais pas."}]},
        },
    ]
    config = {
        "version": 1,
        "imports": ["outils_labo"],
        "models": modeles,
        "storage": {"events": {"backend": "jsonl", "path": "data"}},
        "telemetry": {"logging": {"level": "CRITICAL"}},
    }
    (tmp_path / "loom.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    agent = {
        "name": "demo",
        "main": {"model": "MAIN", "system": "Tu calcules et tu envoies."},
        "tools": [
            {"python": "calculer"},
            {"python": "envoyer", "side_effects": "irreversible", "approval": "always"},
        ],
    }
    (tmp_path / "agents" / "demo.yaml").write_text(yaml.safe_dump(agent), encoding="utf-8")
    return tmp_path / "loom.yaml"


def suite(path: Path, **fields: Any) -> Path:
    """Écrit une suite à côté de la config ; par défaut, un cas de relance qui attend l'envoi."""
    data: dict[str, Any] = {
        "config": "loom.yaml",
        "agent": "demo",
        "cases": [
            {
                "name": "relance",
                "input": RELANCE,
                "expect": {"status": "completed", "called": ["envoyer"]},
            }
        ],
        **fields,
    }
    target = path.parent / "suite.yaml"
    target.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return target


def retouche(config: Path, target: Path, **root: Any) -> Path:
    """La config, complétée à sa racine, écrite à ``target`` (elle-même ou une copie)."""
    data = yaml.safe_load(config.read_text(encoding="utf-8"))
    target.write_text(yaml.safe_dump({**data, **root}), encoding="utf-8")
    return target


def lignes(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


async def jouer(path: Path, **options: Any) -> Any:
    return await evaluate(load_suite(path), **options)


# --- La suite ------------------------------------------------------------------------


def test_a_suite_is_read_relative_to_its_file(labo: Path) -> None:
    lue = load_suite(suite(labo, variants=[{"name": "autre", "config": "loom.yaml"}]))
    assert lue.name == "suite" and lue.base_dir == labo.parent
    assert lue.resolved(lue.config) == labo
    assert lue.resolved(lue.variants[0].config) == labo
    assert [v.name for v in lue.played_variants()] == ["autre"]
    # Sans variante déclarée, une seule : la config de la suite.
    assert [v.name for v in load_suite(suite(labo)).played_variants()] == ["base"]


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"cases": [{"name": "vide", "input": "?"}]}, "aucun attendu"),
        (
            {"cases": [{"name": "c", "input": "?", "criteria": [{"name": "x", "rule": "r"}]}]},
            "des critères sans juge",
        ),
        (
            {
                "cases": [
                    {"name": "c", "input": "?", "expect": {"status": "completed"}},
                    {"name": "c", "input": "!", "expect": {"status": "completed"}},
                ]
            },
            "cas nommé deux fois : c",
        ),
        (
            {"cases": [{"name": "c", "input": "?", "expect": {"matches": ["(non fermé"]}}]},
            "motif '(non fermé' invalide",
        ),
        (
            {
                "judge": {"model": "JUGE", "criteria": [{"name": "ton", "rule": "r"}]},
                "cases": [
                    {"name": "c", "input": "?", "criteria": [{"name": "ton", "rule": "autre"}]}
                ],
            },
            "nommé deux fois : ton",
        ),
        ({"cases": []}, "cases"),
        ({"cases": [{"name": "a b", "input": "?", "expect": {"status": "completed"}}]}, "name"),
    ],
)
def test_a_suite_refuses_what_would_prove_nothing(
    labo: Path, changes: dict[str, Any], message: str
) -> None:
    path = suite(labo, **changes)
    with pytest.raises(ConfigError, match=r"suite\.yaml") as refus:
        load_suite(path)
    assert message in str(refus.value)


def test_a_suite_file_must_be_an_object(tmp_path: Path) -> None:
    path = tmp_path / "liste.yaml"
    path.write_text("- un\n- deux\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="une suite est un objet YAML"):
        load_suite(path)


# --- Les contrôles -------------------------------------------------------------------------


def test_checks_say_what_they_found() -> None:
    outcome = Outcome(
        status=RunStatus.FAILED,
        text="Relance envoyée à martin, 87 €.",
        data={"objet": "Relance", "lignes": [{"total": 87}], "meta": {"ton": "cordial"}},
        error_type="guard.judge",
        tools=(
            ToolUse("calculer", {"expr": "12*7+3"}, "run"),
            ToolUse("envoyer", {"destinataire": "martin", "copie": {"a": 1, "b": 2}}, "double"),
        ),
    )
    expect = Expect.model_validate(
        {
            "status": "completed",
            "contains": ["martin", "dupont"],
            "not_contains": ["87 €", "relancer"],
            "matches": [r"\d+ €", r"^Bonjour"],
            "fields": {"lignes.0.total": 87, "objet": "Autre", "meta.ton": "cordial", "x.y": 1},
            "called": [
                "calculer",
                {"name": "envoyer", "arguments": {"copie": {"a": 1}}},
                {"name": "envoyer", "arguments": {"destinataire": "dupont"}},
                "annuler",
            ],
            "not_called": ["envoyer", "annuler"],
        }
    )
    results = {r.label: r for r in check(expect, outcome)}
    assert len(results) == expect.count == 17
    passed = sorted(label for label, r in results.items() if r.passed)
    assert passed == sorted(
        [
            "contient « martin »",
            "ne contient pas « relancer »",
            r"correspond à /\d+ €/",
            "champ lignes.0.total = 87",
            'champ meta.ton = "cordial"',
            "appelle calculer",
            'appelle envoyer avec {"copie": {"a": 1}}',
            "n'appelle pas annuler",
        ]
    )
    assert results["statut completed"].detail == "failed (guard.judge)"
    assert results['champ objet = "Autre"'].detail == 'vaut "Relance"'
    assert results["champ x.y = 1"].detail == "absent de la sortie structurée"
    assert results['appelle envoyer avec {"destinataire": "dupont"}'].detail == (
        'arguments : {"destinataire": "martin", "copie": {"a": 1, "b": 2}}'
    )
    assert results["appelle annuler"].detail == "appels : calculer, envoyer"
    assert results["n'appelle pas envoyer"].detail == "appelé 1 fois"
    # Un contrôle de texte ne recopie pas le texte : le rapport le donne une fois.
    assert (
        results["contient « dupont »"].detail == ""
        and results["contient « dupont »"].kind == "text"
    )


# --- Le monde pendant une éval ---------------------------------------------------------------


async def test_a_side_effect_tool_is_refused_unless_doubled(labo: Path) -> None:
    refus = await jouer(suite(labo, variants=[{"name": "sans"}]), export=labo.parent / "sans")
    double = await jouer(
        suite(
            labo,
            variants=[{"name": "avec"}],
            doubles={"envoyer": "doublures_labo:faux_envoi"},
        ),
        export=labo.parent / "avec",
    )

    [sans] = refus.runs
    [avec] = double.runs
    assert [(t.name, t.fate) for t in sans.tools] == [("calculer", "run"), ("envoyer", "refused")]
    assert [(t.name, t.fate) for t in avec.tools] == [("calculer", "run"), ("envoyer", "double")]
    # Jamais exécuté : la boîte est vide ; la doublure a reçu l'appel.
    assert lignes(labo.parent / "boite.txt") == []
    assert lignes(labo.parent / "doubles.txt") == ["martin"]
    # Le modèle a reçu le refus, ou la réponse de la doublure ; l'approbation, accordée.
    for dossier, attendu in (("sans", "Non exécuté : éval"), ("avec", "(doublure) envoi à martin")):
        events = _journal(labo.parent / dossier / f"{dossier}--relance--1.jsonl")
        sorties = [e.payload.output.as_text for e in events if isinstance(e.payload, ToolCompleted)]
        assert any(attendu in sortie for sortie in sorties)
        [accord] = [e.payload for e in events if isinstance(e.payload, ApprovalGranted)]
        assert accord.by == "éval"
    assert sans.passed and avec.passed


async def test_an_approval_is_granted_only_when_nothing_leaves() -> None:
    """Doublé ou refusé : accordée par l'éval. Exécuté pour de vrai : personne ne dit oui."""

    @tool(approval="always")
    def lire(x: int) -> str:
        """Lit."""
        return str(x)

    @tool(approval="always", side_effects="irreversible")
    def poster(x: int) -> str:
        """Poste."""
        return str(x)

    outils = EvalTools()
    decisions: dict[str, Any] = {}
    for call_id, outil, nom in (("c1", lire, "lire"), ("c2", poster, "poster")):
        appel = PendingCall(call_id=call_id, name=nom, arguments={"x": 1})
        outils.serves(outil, appel, RunId("r1"))
        decisions[nom] = await outils.approve(
            RunId("r1"), PendingApproval(call_id=call_id, tool_name=nom)
        )
    assert outils.fates == {("r1", "c1"): "run", ("r1", "c2"): "refused"}
    assert isinstance(decisions["lire"], Rejected) and "personne" in decisions["lire"].reason
    assert isinstance(decisions["poster"], Approved) and decisions["poster"].by == "éval"
    # Un appel inconnu de l'éval n'est pas accordé non plus.
    inconnu = await outils.approve(RunId("r1"), PendingApproval(call_id="c9", tool_name="x"))
    assert isinstance(inconnu, Rejected)


async def test_a_double_receives_the_resolved_arguments(labo: Path) -> None:
    report = await jouer(
        suite(
            labo,
            variants=[{"name": "ref", "models": {"main": "REF"}}],
            doubles={"envoyer": "doublures_labo:faux_envoi"},
        )
    )
    [run] = report.runs
    # Le modèle a écrit la référence ; la doublure reçoit le résultat du calcul.
    envoi = next(t for t in run.tools if t.name == "envoyer")
    assert envoi.arguments == {"destinataire": {"$ref": "result:1"}}
    assert lignes(labo.parent / "doubles.txt") == ["87"]
    assert lignes(labo.parent / "boite.txt") == []


async def test_a_subagent_runs_and_its_tools_are_intercepted(
    tree: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tree()
    (path.parent / "doublures_arbre.py").write_text(
        "def calcul(expr: str) -> str:\n    return 'doublé : ' + expr\n", encoding="utf-8"
    )
    target = path.parent / "suite.yaml"
    target.write_text(
        yaml.safe_dump(
            {
                "config": "loom.yaml",
                "agent": "demo",
                "doubles": {"calculer": "doublures_arbre:calcul"},
                "cases": [
                    {
                        "name": "arbre",
                        "input": "Combien font 2 + 2 ?",
                        "expect": {
                            "status": "completed",
                            "called": [
                                "verifier",
                                {"name": "calculer", "arguments": {"expr": "2+2"}},
                            ],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    report = await jouer(target)
    [run] = report.runs
    assert run.passed, [c for c in run.checks if not c.passed]
    # Le sous-agent tourne (pas de sort) ; l'outil de l'enfant est doublé.
    assert [(t.name, t.fate) for t in run.tools] == [("verifier", None), ("calculer", "double")]


# --- Variantes, répétitions, juge ------------------------------------------------------------


async def test_variants_compare_models_and_configs(labo: Path) -> None:
    autre = retouche(labo, labo.parent / "autre.yaml")
    path = suite(
        labo,
        repeat=2,
        variants=[
            {"name": "base"},
            {"name": "autre-modele", "models": {"main": "AUTRE"}},
            {"name": "autre-config", "config": "autre.yaml"},
        ],
        cases=[
            {"name": "relance", "input": RELANCE, "expect": {"contains": ["martin"]}},
            {"name": "calcul", "input": CALCUL, "expect": {"contains": ["4"]}},
        ],
    )
    report = await jouer(path)

    assert len(report.runs) == 3 * 2 * 2
    assert report.summary("base").cases == {"relance": (2, 2), "calcul": (2, 2)}
    assert report.summary("autre-modele").cases == {"relance": (0, 2), "calcul": (2, 2)}
    assert report.summary("autre-config").passed_cases == 2
    assert not report.passed
    assert report.variants["autre-modele"] == {"config": None, "models": {"main": "AUTRE"}}
    assert report.variants["autre-config"]["config"] == str(autre)
    # Le prix des modèles : chaque run a coûté, et le rapport en fait la somme.
    assert all(run.cost_usd > 0 for run in report.runs)
    assert report.spent_usd == pytest.approx(sum(run.cost_usd for run in report.runs))
    texte = "\n".join(render_eval(report))
    assert "Variante autre-modele (main=AUTRE) — 1/2 cas réussi(s)" in texte
    assert "ÉCHEC    relance (0/2)" in texte and "  texte :" in texte
    assert "Relance faite." in texte


async def test_the_eval_judge_notes_outside_the_run(labo: Path) -> None:
    path = suite(
        labo,
        judge={"model": "JUGE", "criteria": [{"name": "fidele", "rule": "Rien d'inventé."}]},
        cases=[
            {
                "name": "relance",
                "input": RELANCE,
                "criteria": [{"name": "ton", "rule": "Ton cordial.", "min_score": 0.8}],
            }
        ],
    )
    report = await jouer(path, export=labo.parent / "juge")
    [run] = report.runs
    notes = {c.label: c for c in run.checks}
    assert notes["juge : fidele ≥ 0.80"].passed
    assert notes["juge : fidele ≥ 0.80"].detail == "note 1.00 — Rien d'inventé."
    assert not notes["juge : ton ≥ 0.80"].passed
    assert run.judge_cost_usd > 0 and report.judge_model == "JUGE"
    # Hors du run : aucun appel du juge d'éval dans son journal, son coût à part.
    events = _journal(labo.parent / "juge" / "base--relance--1.jsonl")
    assert not any(e.role is not None and "judge" in e.role for e in events)
    assert run.spent_usd == pytest.approx(run.cost_usd + run.judge_cost_usd)


@pytest.mark.parametrize(("outils", "note"), [(["calculer"], True), ([], False)])
async def test_the_eval_judge_sees_the_named_tool_results(
    labo: Path, outils: list[str], note: bool
) -> None:
    """Avec ``tool_results``, le juge voit ce que les outils ont rendu dans le run ; sans, non."""
    juge = {
        "model": "VOIT",
        "criteria": [{"name": "fidele", "rule": "Fidèle au calcul."}, {"name": "ton", "rule": "r"}],
        "tool_results": outils,
    }
    [run] = (await jouer(suite(labo, judge=juge, cases=[{"name": "r", "input": RELANCE}]))).runs
    assert [c.passed for c in run.checks] == [note, note]


async def test_the_eval_judge_reads_results_errors_and_absences() -> None:
    """Chaque appel de l'outil nommé, erreurs comprises ; un outil sans résultat le dit."""
    modele = ScriptedModel(
        tool_call_message(("v1", "verdict", VERDICT_PLEIN["tool_calls"][0]["arguments"]))
    )
    juge = EvalJudgeClient(
        ModelSpec.model_validate({"id": "J", "sdk": "fake", "model": "j"}), modele
    )
    rendu = Outcome(
        status=RunStatus.COMPLETED,
        text="Fait : 87.",
        data=None,
        error_type=None,
        tools=(
            ToolUse("calculer", {"expr": "12*7+3"}, "run", result="87"),
            ToolUse("calculer", {"expr": "1/0"}, "run", result="boum", is_error=True),
            ToolUse("autre", {}, "run", result="secret"),
        ),
    )
    criteres = tuple(EvalCriterion(name=nom, rule="r").criterion for nom in ("fidele", "ton"))
    jugement = await juge.judge(criteres, "Calcule.", rendu, ["calculer", "absent"])
    assert jugement.error is None and [s.passed for s in jugement.scores] == [True, True]
    [requete] = modele.requests
    texte = requete.messages[0].text
    attendus = [
        "<criteria>",
        "<user_input>\nCalcule.",
        '<tool_result tool="calculer">\n87\n</tool_result>',
        '<tool_result tool="calculer" status="erreur">\nboum\n</tool_result>',
        '<tool_result tool="absent">\n(aucun résultat dans ce run)',
        "<output>\nFait : 87.",
    ]
    positions = [texte.index(morceau) for morceau in attendus]
    assert positions == sorted(positions)
    assert "secret" not in texte


async def test_a_failing_judge_fails_its_criteria_saying_why(labo: Path) -> None:
    path = suite(
        labo,
        judge={"model": "MUET", "criteria": [{"name": "fidele", "rule": "Rien d'inventé."}]},
        cases=[{"name": "relance", "input": RELANCE}],
    )
    [run] = (await jouer(path)).runs
    [note] = run.checks
    assert not note.passed and "juge d'éval en échec" in note.detail


# --- Isolement, export, plafond ----------------------------------------------------------------


async def test_nothing_reaches_the_instance_journal_nor_its_quotas(labo: Path) -> None:
    retouche(labo, labo, tenants=[{"id": "dupont", "quotas": {"runs_per_minute": 1}}])
    report = await jouer(suite(labo, tenant="dupont", repeat=3))

    # Trois runs en une minute, quand le client n'en a qu'un : l'éval ne compte pas.
    assert [run.passed for run in report.runs] == [True, True, True]
    assert not (labo.parent / "data").exists()


def test_an_isolated_config_keeps_the_logic_and_drops_the_world(tmp_path: Path) -> None:
    from loom_ia.config import LoomConfig

    config = LoomConfig.model_validate(
        {
            "version": 1,
            "storage": {
                "events": {"backend": "jsonl", "path": "data"},
                "idempotency": {"backend": "sqlite", "path": "data/keys.db"},
            },
            "telemetry": {"exporters": [{"type": "otel"}]},
            "budgets": {"run": {"max_cost": 0.05}, "tenant": {"max_cost_per_day": 1.0}},
            "tenants": [
                {
                    "id": "dupont",
                    "quotas": {"runs_per_minute": 1},
                    "budgets": {"tenant": {"max_cost_per_day": 0.5}},
                    "storage": {"events": {"backend": "jsonl", "path": "data/dupont"}},
                }
            ],
        }
    )
    pris = isolated(config, tmp_path)
    assert pris.storage.events.backend == "jsonl"
    assert pris.storage.events.path == tmp_path / "journal"
    assert pris.storage.artifacts_path == tmp_path / "journal" / ".artifacts"
    # Le magasin partagé : une base à part, ou le journal de chaque run sans aiosqlite.
    if find_spec("aiosqlite") is not None:
        assert pris.storage.idempotency.backend == "sqlite"
        assert pris.storage.idempotency.path == tmp_path / "idempotence.db"
    else:
        assert pris.storage.idempotency.backend == "journal"
    assert pris.telemetry.exporters == ()
    assert pris.budgets.run.max_cost == 0.05 and not pris.budgets.tenant.limited
    [dupont] = pris.tenants
    assert dupont.storage is None and not dupont.quotas.limited
    assert dupont.budgets is not None and not dupont.budgets.tenant.limited


async def test_the_cap_stops_further_runs(labo: Path) -> None:
    report = await jouer(suite(labo, repeat=3, max_cost_usd=0.000001))
    premier, *suivants = report.runs
    assert premier.skipped is None and premier.cost_usd > 0.000001
    assert all(r.skipped is not None and "plafond" in r.skipped for r in suivants)
    assert len(suivants) == 2 and report.skipped == 2
    assert not report.passed
    assert "2 run(s) non joué(s)" in "\n".join(render_eval(report))


# --- Ce qui rend une suite injouable ------------------------------------------------------------


@pytest.mark.parametrize(
    ("changes", "options", "message"),
    [
        ({"agent": "absent"}, {}, "l'agent 'absent' n'est pas dans la config"),
        ({"tenant": "inconnu"}, {}, "client 'inconnu' inconnu"),
        ({"doubles": {"inexistant": "doublures_labo:faux_envoi"}}, {}, "outil inconnu"),
        ({"doubles": {"envoyer": "doublures_labo:PAS_UNE_FONCTION"}}, {}, "pas une fonction"),
        (
            {"judge": {"model": "ABSENT", "criteria": [{"name": "x", "rule": "r"}]}},
            {},
            "modèle 'ABSENT' non déclaré",
        ),
        ({"variants": [{"name": "v", "models": {"main": "ABSENT"}}]}, {}, "non déclaré"),
        (
            {
                "judge": {
                    "model": "JUGE",
                    "criteria": [{"name": "x", "rule": "r"}],
                    "tool_results": ["absent"],
                }
            },
            {},
            "le juge voit les résultats d'un outil inconnu, 'absent'",
        ),
        ({}, {"cases": ["absent"]}, "cas inconnu(s) : absent"),
        ({}, {"variants": ["absente"]}, "variante inconnu(s) : absente"),
    ],
)
async def test_a_suite_that_cannot_be_played_is_refused(
    labo: Path, changes: dict[str, Any], options: dict[str, Any], message: str
) -> None:
    with pytest.raises(EvalError, match=message.replace("(", r"\(").replace(")", r"\)")):
        await jouer(suite(labo, **changes), **options)
    assert lignes(labo.parent / "boite.txt") == []


async def test_an_instance_evaluates_with_its_own_config(labo: Path) -> None:
    sans_config = EvalSuite.model_validate(
        {
            "agent": "demo",
            "cases": [{"name": "calcul", "input": CALCUL, "expect": {"contains": ["4"]}}],
        }
    )
    async with Loom.from_config(labo) as loom:
        report = await loom.evaluate(sans_config)
    assert report.passed and report.suite == "suite"
    with pytest.raises(EvalError, match="aucune config"):
        await evaluate(sans_config)


async def test_an_instance_lends_what_it_registered(labo: Path) -> None:
    """Un outil et une doublure enregistrés en Python servent à l'éval de l'instance."""
    agent = yaml.safe_load((labo.parent / "agents" / "demo.yaml").read_text(encoding="utf-8"))
    agent["tools"][1] = {
        "python": "envoi_enregistre",
        "side_effects": "irreversible",
        "approval": "always",
    }
    (labo.parent / "agents" / "demo.yaml").write_text(yaml.safe_dump(agent), encoding="utf-8")
    vus: list[str] = []

    async def envoi(destinataire: str) -> str:
        """Envoie."""
        vus.append(f"envoi {destinataire}")
        return "envoyé"

    def doublure(destinataire: str) -> str:
        vus.append(f"doublure {destinataire}")
        return "doublé"

    lue = load_suite(suite(labo, doubles={"envoyer": "doublure_enregistree"}))
    async with Loom.from_config(labo) as loom:
        # Enregistré sous un autre nom que celui que voit le modèle.
        loom.register("envoi_enregistre", tool(name="envoyer")(envoi))
        loom.register("doublure_enregistree", doublure)
        report = await loom.evaluate(lue.model_copy(update={"cases": (_appelle("envoyer"),)}))
    [run] = report.runs
    assert run.passed and [t.fate for t in run.tools] == ["run", "double"]
    assert vus == ["doublure martin"]


# --- loom eval --------------------------------------------------------------------------------


@pytest.fixture
def logs_intacts() -> Iterator[None]:
    """La commande installe ses logs (``apply_logging``) : ils sont rendus tels quels après.

    Sans cela, le logger ``loom_ia`` resterait sans propagation, et les essais
    suivants qui lisent les logs par ``caplog`` ne verraient plus rien.
    """
    racine = logging.getLogger("loom_ia")
    handlers, level, propagate = list(racine.handlers), racine.level, racine.propagate
    yield
    for handler in list(racine.handlers):
        if handler not in handlers:
            racine.removeHandler(handler)
            handler.close()
    racine.setLevel(level)
    racine.propagate = propagate


@pytest.mark.usefixtures("logs_intacts")
async def test_the_command_says_0_1_or_2(labo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = suite(
        labo,
        variants=[{"name": "base"}, {"name": "autre", "models": {"main": "AUTRE"}}],
        cases=[
            {"name": "relance", "input": RELANCE, "expect": {"contains": ["martin"]}},
            {"name": "calcul", "input": CALCUL, "expect": {"contains": ["4"]}},
        ],
    )
    assert await _cli(["eval", str(path), "--variant", "base"]) == 0
    sortie = capsys.readouterr().out
    assert "Verdict    : tout passe" in sortie
    assert await _cli(["eval", str(path)]) == 1
    assert "ÉCHEC    relance (0/1)" in capsys.readouterr().out
    assert await _cli(["eval", str(path), "--case", "calcul", "--json"]) == 0
    rapport = json.loads(capsys.readouterr().out)
    assert rapport["passed"] is True and {r["case"] for r in rapport["runs"]} == {"calcul"}
    assert await _cli(["eval", str(path), "--case", "absent"]) == 2
    assert "Éval impossible" in capsys.readouterr().err
    (labo.parent / "casse.yaml").write_text("agent: demo\n", encoding="utf-8")
    assert await _cli(["eval", str(labo.parent / "casse.yaml")]) == 2
    # Sans config dans la suite, c'est --config qui sert.
    (labo.parent / "nue.yaml").write_text(
        yaml.safe_dump(
            {
                "agent": "demo",
                "cases": [{"name": "c", "input": CALCUL, "expect": {"contains": ["4"]}}],
            }
        ),
        encoding="utf-8",
    )
    assert await _cli(["--config", str(labo), "eval", str(labo.parent / "nue.yaml")]) == 0
    assert load_config(labo).agents  # la config de l'instance n'a pas bougé


def _appelle(outil: str) -> Any:
    from loom_ia.replay import EvalCase

    return EvalCase.model_validate(
        {"name": "relance", "input": RELANCE, "expect": {"called": [outil]}}
    )


def _journal(path: Path) -> list[Event]:
    return [Event.model_validate_json(line) for line in lignes(path)]


async def _cli(argv: list[str]) -> int:
    """La commande lance sa propre boucle : on la fait tourner hors de celle de l'essai."""
    return await asyncio.to_thread(cli_main, argv)
