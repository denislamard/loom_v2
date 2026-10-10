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

Et la non-régression (J6.3b) : un cas de rejeu rejoue à l'identique chaque run
de ses journaux, avec la config de chaque variante — il passe s'ils se
rejouent, il dit où ils s'écartent sinon ; ses journaux sont lus avant le
premier run ; un fichier illisible, un run inachevé ou d'un autre agent, un
motif sans fichier le font tomber. Un cas peut être joué au nom de son client.

Et le kit (J6.3c) : ``called`` lit ce que l'outil a reçu — référence résolue,
arguments d'une politique —, en le disant à côté de ce que le modèle a écrit ;
``Loom(models=…)`` sert des clients fournis, sans les fermer ; le banc
(``Bench``) monte un agent à part, avec des faux modèles et des faux outils,
refuse un modèle réel non remplacé, contrôle comme un cas d'éval, garde les
appels de ses runs, enchaîne une conversation et exporte un journal qui se
rejoue.
"""

import asyncio
import json
import logging
from collections.abc import Iterator, Mapping
from importlib.util import find_spec
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import JsonValue, ValidationError

from loom_ia.access import Loom
from loom_ia.access.cli import main as cli_main
from loom_ia.access.evals import evaluate
from loom_ia.adapters.models import ModelConfigError
from loom_ia.config import ConfigError, load_config
from loom_ia.core.events import ApprovalGranted, Event, ToolCompleted
from loom_ia.core.model import (
    Approved,
    Message,
    ModelSpec,
    PendingApproval,
    PendingCall,
    Rejected,
    RunId,
    RunStatus,
    SessionId,
    TenantId,
)
from loom_ia.replay import (
    IDENTICAL,
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
from loom_ia.testing import (
    Bench,
    BenchError,
    ScriptedModel,
    assert_replays,
    tool_call_message,
)
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
        ({"cases": [{"name": "c"}]}, "ni demande ('input') ni journaux à rejouer"),
        (
            {"cases": [{"name": "c", "input": "?", "replay": "j/*.jsonl"}]},
            "pas les deux",
        ),
        (
            {"cases": [{"name": "c", "replay": "j/*.jsonl", "expect": {"status": "completed"}}]},
            "un cas de rejeu n'a ni attendus",
        ),
        (
            {"cases": [{"name": "c", "replay": "j/*.jsonl", "tenant": "dupont"}]},
            "un cas de rejeu n'a ni attendus, ni critères, ni client",
        ),
        (
            {
                "judge": {"model": "JUGE"},
                "cases": [
                    {"name": "c", "replay": "j/*.jsonl", "criteria": [{"name": "x", "rule": "r"}]}
                ],
            },
            "un cas de rejeu n'a ni attendus, ni critères",
        ),
    ],
)
def test_a_suite_refuses_what_would_prove_nothing(
    labo: Path, changes: dict[str, Any], message: str
) -> None:
    path = suite(labo, **changes)
    with pytest.raises(ConfigError, match=r"suite\.yaml") as refus:
        load_suite(path)
    assert message in str(refus.value)


def test_a_replay_pattern_is_relative_to_the_suite(tmp_path: Path) -> None:
    journaux = tmp_path / "journaux"
    (journaux / "vieux").mkdir(parents=True)
    for name in ("b.jsonl", "a.jsonl", "vieux/c.jsonl", "notes.txt"):
        (journaux / name).write_text("", encoding="utf-8")
    (journaux / "dossier.jsonl").mkdir()
    lue = EvalSuite.model_validate(
        {
            "agent": "demo",
            "base_dir": tmp_path,
            "cases": [
                {"name": "r", "replay": "journaux/**/*.jsonl"},
                {"name": "s", "replay": str(journaux / "*.jsonl")},
            ],
        }
    )
    relatif, absolu = lue.cases
    assert [p.relative_to(tmp_path).as_posix() for p in lue.journals(relatif)] == [
        "journaux/a.jsonl",
        "journaux/b.jsonl",
        "journaux/vieux/c.jsonl",
    ]
    assert [p.name for p in lue.journals(absolu)] == ["a.jsonl", "b.jsonl"]


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
        'arguments reçus : {"destinataire": "martin", "copie": {"a": 1, "b": 2}}'
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
    # Le modèle a écrit la référence ; la doublure reçoit le résultat du calcul,
    # et c'est ce que lit `called` (décision du 06/10, 6.3c).
    envoi = next(t for t in run.tools if t.name == "envoyer")
    assert envoi.arguments == {"destinataire": "87"}
    assert envoi.written == {"destinataire": {"$ref": "result:1"}}
    assert lignes(labo.parent / "doubles.txt") == ["87"]
    assert lignes(labo.parent / "boite.txt") == []
    [recu] = check(Expect.model_validate({"called": [_envoi("87")]}), _issue(run.tools))
    [ecrit] = check(Expect.model_validate({"called": [_envoi("result:1")]}), _issue(run.tools))
    assert recu.passed and not ecrit.passed
    assert ecrit.detail == (
        'arguments reçus : {"destinataire": "87"} (écrits : {"destinataire": {"$ref": "result:1"}})'
    )


POLITIQUES = '''
from loom_ia.policies import CONTINUE, BeforeTool, Decision, Replace, policy


@policy(points=["before_tool"], decisions=["replace"])
def majuscules(subject: BeforeTool) -> Decision:
    """Met le destinataire d'un envoi en majuscules."""
    if subject.spec.name != "envoyer":
        return CONTINUE
    return Replace({"destinataire": str(subject.arguments["destinataire"]).upper()})
'''


def avec_politique(config: Path) -> None:
    """Une politique ``before_tool`` qui remplace les arguments de l'envoi."""
    (config.parent / "politiques_labo.py").write_text(POLITIQUES, encoding="utf-8")
    data = yaml.safe_load(config.read_text(encoding="utf-8"))
    data["imports"] = [*data["imports"], "politiques_labo"]
    config.write_text(yaml.safe_dump(data), encoding="utf-8")
    agents = config.parent / "agents" / "demo.yaml"
    agent = yaml.safe_load(agents.read_text(encoding="utf-8"))
    agent["policies"] = [{"hook": "majuscules"}]
    agents.write_text(yaml.safe_dump(agent), encoding="utf-8")


async def test_called_reads_what_a_policy_sent_in_place(labo: Path) -> None:
    avec_politique(labo)
    report = await jouer(
        suite(
            labo,
            doubles={"envoyer": "doublures_labo:faux_envoi"},
            cases=[
                {
                    "name": "relance",
                    "input": RELANCE,
                    "expect": {"called": [_envoi("MARTIN")], "not_called": ["autre"]},
                }
            ],
        )
    )
    [run] = report.runs
    assert run.passed
    envoi = next(t for t in run.tools if t.name == "envoyer")
    assert envoi.arguments == {"destinataire": "MARTIN"}
    assert envoi.written == {"destinataire": "martin"}
    # La doublure reçoit, elle aussi, ce que la politique a mis à la place.
    assert lignes(labo.parent / "doubles.txt") == ["MARTIN"]
    # Un outil que rien n'a changé n'a pas d'arguments écrits à part.
    calcul = next(t for t in run.tools if t.name == "calculer")
    assert calcul.written is None


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


class Coffre:
    """Fournisseur de secrets de l'appelant : la clé n'est pas dans l'environnement."""

    def secrets(self, tenant_id: TenantId) -> Mapping[str, str]:
        return {"CLE_COFFRE": "sk-test-pas-une-vraie"}


def avec_cle_du_coffre(labo: Path) -> None:
    """L'agent ``demo`` parle à un modèle qui exige sa clé, sur un port fermé : pas de réseau."""
    config = yaml.safe_load(labo.read_text(encoding="utf-8"))
    config["models"].append(
        {
            "id": "CLE",
            "sdk": "anthropic",
            "model": "claude-x",
            "base_url": "http://127.0.0.1:9",
            "api_key_env": "CLE_COFFRE",
            "retry": {"max_attempts": 1},
        }
    )
    labo.write_text(yaml.safe_dump(config), encoding="utf-8")
    agent_file = labo.parent / "agents" / "demo.yaml"
    agent = yaml.safe_load(agent_file.read_text(encoding="utf-8"))
    agent["main"]["model"] = "CLE"
    agent_file.write_text(yaml.safe_dump(agent), encoding="utf-8")


def modeles_qui_repondent(labo: Path, texte: str) -> list[dict[str, Any]]:
    """Les modèles de la config, ``MAIN`` répondant ``texte`` d'emblée."""
    models: list[dict[str, Any]] = yaml.safe_load(labo.read_text(encoding="utf-8"))["models"]
    for model in models:
        if model["id"] == "MAIN":
            model["params"] = {"script": [{"text": texte}]}
    return models


async def test_an_instance_evaluates_with_its_own_secrets(labo: Path) -> None:
    """Le fournisseur de secrets de l'instance sert à l'éval, variantes et juge, comme à ``run``."""
    pytest.importorskip("anthropic")
    avec_cle_du_coffre(labo)
    lue = EvalSuite.model_validate(
        {
            "agent": "demo",
            "judge": {"model": "CLE", "criteria": [{"name": "fidele", "rule": "r"}]},
            "cases": [{"name": "calcul", "input": CALCUL}],
        }
    )
    async with Loom(load_config(labo), environ={}) as sans_secrets:
        with pytest.raises(EvalError, match="Juge d'éval : le modèle CLE ne peut pas être appelé"):
            await sans_secrets.evaluate(lue)
    async with Loom(load_config(labo), environ={}, secrets=Coffre()) as loom:
        report = await loom.evaluate(lue)
    [run] = report.runs
    # La clé a été lue : le run est allé jusqu'à l'appel du modèle, qui n'a pas répondu.
    assert run.error is None and (run.failure or "").startswith("model.transient")


async def test_an_instance_evaluates_in_its_own_profile(labo: Path) -> None:
    """La config qu'une suite désigne se charge sous le profil de l'instance, comme la sienne."""
    models = modeles_qui_repondent(labo, "Réponse de dev.")
    autre = retouche(labo, labo.parent / "autre.yaml", profiles={"dev": {"models": models}})
    lue = load_suite(
        suite(
            labo,
            config=autre.name,
            cases=[{"name": "calcul", "input": CALCUL, "expect": {"contains": ["de dev"]}}],
        )
    )
    async with Loom.from_config(labo, profile="dev") as dev:
        report = await dev.evaluate(lue)
    async with Loom.from_config(labo) as sans_profil:
        ailleurs = await sans_profil.evaluate(lue)
    assert report.passed
    assert not ailleurs.passed


# --- Rejouer des journaux (J6.3b) ---------------------------------------------------------------

REJEU: dict[str, Any] = {"name": "rejeu", "replay": "journaux/*.jsonl"}
DEUX_CAS: list[dict[str, Any]] = [
    {"name": "relance", "input": RELANCE, "expect": {"called": ["envoyer"]}},
    {"name": "calcul", "input": CALCUL, "expect": {"contains": ["4"]}},
]


async def enregistre(labo: Path, **fields: Any) -> Path:
    """Les deux cas joués, leurs journaux exportés à côté de la suite."""
    journaux = labo.parent / "journaux"
    report = await jouer(suite(labo, cases=DEUX_CAS, **fields), export=journaux)
    assert report.passed and len(list(journaux.glob("*.jsonl"))) == 2
    return journaux


async def test_a_replay_case_replays_each_recorded_run(labo: Path) -> None:
    journaux = await enregistre(labo)
    # Le juge de la suite ne note pas un rejeu : ses critères ne s'y appliquent pas.
    juge = {"model": "JUGE", "criteria": [{"name": "fidele", "rule": "r"}]}
    path = suite(labo, cases=[REJEU], judge=juge)
    lue = load_suite(path)
    assert lue.criteria(lue.cases[0]) == ()
    report = await jouer(path)
    assert report.passed and report.replays == ("rejeu",)
    assert [r.journal for r in report.runs] == [
        "journaux/base--calcul--1.jsonl",
        "journaux/base--relance--1.jsonl",
    ]
    assert all([c.label for c in r.checks] == [IDENTICAL] for r in report.runs)
    assert all(r.checks[0].kind == "replay" and r.spent_usd == 0 for r in report.runs)
    assert "  ok       rejeu (2/2 run(s) rejoué(s) à l'identique)" in render_eval(report)
    # Ni envoi, ni doublure : le journal sert l'appel d'envoi.
    assert lignes(labo.parent / "boite.txt") == lignes(labo.parent / "doubles.txt") == []
    assert {e.run_id for e in _journal(journaux / "base--calcul--1.jsonl")} >= {
        report.runs[0].run_id
    }


@pytest.mark.usefixtures("logs_intacts")
async def test_a_replay_case_says_where_todays_config_diverges(labo: Path) -> None:
    await enregistre(labo)
    path = suite(labo, cases=[REJEU])
    assert await _cli(["eval", str(path)]) == 0
    agents = labo.parent / "agents" / "demo.yaml"
    agent = yaml.safe_load(agents.read_text(encoding="utf-8"))
    agent["main"]["system"] = "Tu calcules, tu envoies, et tu signes."
    agents.write_text(yaml.safe_dump(agent), encoding="utf-8")
    # La commande en fait un garde-fou : 1 quand un run ne se rejoue plus.
    assert await _cli(["eval", str(path)]) == 1
    report = await jouer(path)
    assert not report.passed and len(report.runs) == 2
    for run in report.runs:
        assert run.divergence is not None and run.divergence.parts == ("system",)
        [ecart] = run.checks
        assert not ecart.passed and "appel de modèle n°1 (main au journal)" in ecart.detail
        assert "le prompt système" in ecart.detail
    rendu = "\n".join(render_eval(report))
    assert "ÉCHEC    rejeu (0/2 run(s) rejoué(s) à l'identique)" in rendu
    assert "✗ se rejoue à l'identique — appel de modèle n°1" in rendu


def sans_cle(config: Path) -> None:
    """Le modèle ``MAIN`` servi par un vrai client, dont la clé manque ; ses réglages intacts."""
    data = yaml.safe_load(config.read_text(encoding="utf-8"))
    for model in data["models"]:
        if model["id"] == "MAIN":
            model.update(sdk="anthropic", api_key_env="EVAL_CLE_ABSENTE")
    config.write_text(yaml.safe_dump(data), encoding="utf-8")


async def test_a_replay_needs_no_key(labo: Path) -> None:
    """Rejouer ne monte aucun vrai client : un modèle sans sa clé se rejoue, il ne se joue pas."""
    await enregistre(labo)
    sans_cle(labo)
    path = suite(labo, cases=[*DEUX_CAS, REJEU], doubles={"envoyer": "doublures_labo:faux_envoi"})
    rejeu = await jouer(path, cases=["rejeu"])
    assert rejeu.passed and len(rejeu.runs) == 2
    # Jouer un cas, lui, monte le vrai client : sa clé manque, et c'est dit.
    with pytest.raises(ModelConfigError, match="EVAL_CLE_ABSENTE"):
        await jouer(path, cases=["calcul"])


async def test_a_replay_case_is_replayed_with_each_variant_config(labo: Path) -> None:
    await enregistre(labo)
    variants = [{"name": "base"}, {"name": "autre", "models": {"main": "AUTRE"}}]
    report = await jouer(suite(labo, cases=[REJEU], variants=variants))
    assert report.summary("base").passed_cases == 1
    assert report.summary("autre").passed_cases == 0
    autres = [r for r in report.runs if r.variant == "autre"]
    assert all(r.divergence is not None and "model" in r.divergence.parts for r in autres)


async def test_journals_are_read_before_the_first_run(labo: Path) -> None:
    """Ce qu'exporte une éval n'est rejoué qu'à la suivante, jamais par elle-même."""
    path = suite(labo, cases=[*DEUX_CAS, REJEU])
    journaux = labo.parent / "journaux"
    premier = await jouer(path, export=journaux)
    [manque] = [r for r in premier.runs if r.case == "rejeu"]
    assert manque.error == "aucun journal ne correspond à journaux/*.jsonl"
    assert len(list(journaux.glob("*.jsonl"))) == 2 and not premier.passed
    rendu = "\n".join(render_eval(premier))
    assert "ÉCHEC    rejeu (0/0 run(s) rejoué(s) à l'identique, 1 non rejoué(s))" in rendu
    assert "      aucun journal ne correspond à journaux/*.jsonl" in rendu
    second = await jouer(path)
    assert second.passed and second.summary("base").cases["rejeu"] == (2, 2)


async def test_what_a_replay_case_cannot_replay_fails_it(labo: Path) -> None:
    journaux = await enregistre(labo)
    (journaux / "casse.jsonl").write_text("{pas un événement\n", encoding="utf-8")
    calcul = _journal(journaux / "base--calcul--1.jsonl")
    relance = _journal(journaux / "base--relance--1.jsonl")
    # Un run fini, et un autre arrêté juste après sa demande.
    pause = [*relance, *(e for e in calcul if e.seq <= calcul[0].seq + 1)]
    (journaux / "pause.jsonl").write_text(
        "".join(f"{e.model_dump_json()}\n" for e in pause), encoding="utf-8"
    )
    ailleurs = [e.model_copy(update={"agent": "autre"}) for e in calcul]
    (journaux / "zz-autre.jsonl").write_text(
        "".join(f"{e.model_dump_json()}\n" for e in ailleurs), encoding="utf-8"
    )
    report = await jouer(suite(labo, cases=[REJEU]))
    erreurs = {r.journal: r.error for r in report.runs if r.error is not None}
    assert set(erreurs) == {
        "journaux/casse.jsonl",
        "journaux/pause.jsonl",
        "journaux/zz-autre.jsonl",
    }
    assert "ligne 1 : pas un événement" in str(erreurs["journaux/casse.jsonl"])
    assert "inachevé au journal" in str(erreurs["journaux/pause.jsonl"])
    assert (
        erreurs["journaux/zz-autre.jsonl"] == "journal de l'agent 'autre' — la suite évalue 'demo'"
    )
    # Les runs sains passent toujours, celui du journal mêlé compris.
    assert sum(1 for r in report.runs if r.passed) == 3 and not report.passed
    rendu = "\n".join(render_eval(report))
    assert "ÉCHEC    rejeu (3/3 run(s) rejoué(s) à l'identique, 3 non rejoué(s))" in rendu
    assert "journaux/casse.jsonl : Journal" in rendu


async def test_a_case_is_played_for_its_own_tenant(labo: Path) -> None:
    retouche(labo, labo, tenants=[{"id": "dupont"}, {"id": "martin"}])
    cas = [{**DEUX_CAS[0], "tenant": "dupont"}, {**DEUX_CAS[1], "tenant": "martin"}]
    journaux = labo.parent / "journaux"
    report = await jouer(suite(labo, cases=cas, tenant="dupont"), export=journaux)
    assert report.passed
    clients = {f.name: {e.tenant_id for e in _journal(f)} for f in sorted(journaux.glob("*.jsonl"))}
    assert clients == {"base--relance--1.jsonl": {"dupont"}, "base--calcul--1.jsonl": {"martin"}}
    # Rejoués au nom du client que porte chaque journal.
    assert (await jouer(suite(labo, cases=[REJEU]))).passed
    with pytest.raises(EvalError, match="client 'inconnu' inconnu"):
        await jouer(suite(labo, cases=[{**DEUX_CAS[1], "tenant": "inconnu"}]))


# --- Le banc (J6.3c) --------------------------------------------------------------------------


def relance_scriptee() -> ScriptedModel:
    """L'orchestrateur en Python : calcule, envoie à martin, conclut."""
    return ScriptedModel(
        tool_call_message(("c1", "calculer", {"expr": "12*7+3"})),
        tool_call_message(("c2", "envoyer", {"destinataire": "martin"})),
        Message.assistant("Relance envoyée à martin."),
    )


class Ferme:
    """Un client fourni qui note s'il a été fermé."""

    def __init__(self, inner: ScriptedModel) -> None:
        self.inner = inner
        self.closed = False

    @property
    def provider(self) -> str:
        return self.inner.provider

    def stream(self, request: Any) -> Any:
        return self.inner.stream(request)

    async def aclose(self) -> None:
        self.closed = True


async def test_a_bench_runs_an_agent_with_python_fakes(labo: Path) -> None:
    modele = relance_scriptee()
    vus: list[str] = []

    def faux(destinataire: str) -> str:
        vus.append(destinataire)
        return f"envoyé à {destinataire}"

    async with Bench(labo, models={"MAIN": modele}, tools={"envoyer": faux}) as banc:
        result = await banc.run("demo", RELANCE)
        checks = banc.expect(
            result,
            status="completed",
            contains=["martin"],
            called=[_envoi("martin"), {"name": "calculer", "arguments": {"expr": "12*7+3"}}],
        )
        [envoi] = banc.calls("envoyer")
    assert len(checks) == 4 and all(c.passed for c in checks)
    assert envoi.fate == "double" and envoi.result == "envoyé à martin" and not envoi.is_error
    assert [t.name for t in banc.calls(result=result)] == ["calculer", "envoyer"]
    # Le faux a répondu, l'outil n'a rien envoyé, le modèle a vu ses trois tours.
    assert vus == ["martin"] and lignes(labo.parent / "boite.txt") == []
    assert len(modele.requests) == 3 and modele.remaining == 0
    # Monté à part : rien n'a été écrit là où la config range son journal.
    assert not (labo.parent / "data").exists()


async def test_a_side_effect_tool_without_fake_is_refused_on_the_bench(labo: Path) -> None:
    async with Bench(labo) as banc:
        result = await banc.run("demo", RELANCE)
        banc.expect(result, status="completed", called=["envoyer"])
        second = await banc.run("demo", RELANCE)
        [envoi] = banc.calls("envoyer", result=result)
    # Les appels du banc, run par run ou tous ensemble.
    assert len(banc.calls("envoyer")) == 2 and len(banc.calls(result=second)) == 2
    assert envoi.fate == "refused" and envoi.is_error
    assert "jamais exécuté pendant une éval" in str(envoi.result)
    assert lignes(labo.parent / "boite.txt") == []


async def test_a_real_model_is_refused_unless_asked(labo: Path) -> None:
    sans_cle(labo)
    async with Bench(labo) as banc:
        result = await banc.run("demo", CALCUL)
    assert result.status == RunStatus.FAILED and result.error_type == "model.auth"
    assert "MAIN" in str(result.error) and "real_models=True" in str(result.error)
    # Demandé pour de vrai, il l'est : sa clé manque, et c'est dit.
    async with Bench(labo, real_models=True, environ={}) as banc:
        with pytest.raises(ModelConfigError, match="EVAL_CLE_ABSENTE"):
            await banc.run("demo", CALCUL)
    # Remplacé, il ne demande rien.
    async with Bench(labo, models={"MAIN": relance_scriptee()}) as banc:
        assert (await banc.run("demo", RELANCE)).ok


async def test_bench_expect_says_what_fell(labo: Path) -> None:
    async with Bench(labo, models={"MAIN": relance_scriptee()}) as banc:
        result = await banc.run("demo", RELANCE)
        with pytest.raises(AssertionError) as tombe:
            banc.expect(result, contains=["dupont"], not_called=["calculer"], status="completed")
        with pytest.raises(ValueError, match="sans attendu"):
            banc.expect(result)
        with pytest.raises(ValidationError):
            banc.expect(result, appelle=["envoyer"])
    lignes_ = str(tombe.value).splitlines()
    assert lignes_[0] == f"Banc : 2 contrôle(s) tombé(s) sur 3, run {result.run_id} (completed)"
    assert lignes_[1:] == [
        "  ✗ contient « dupont »",
        "  ✗ n'appelle pas calculer — appelé 1 fois",
        "  texte :",
        "    Relance envoyée à martin.",
    ]


async def test_a_bench_is_mounted_right_or_refused(labo: Path) -> None:
    with pytest.raises(ConfigError, match="modèles remplacés mais non déclarés : ABSENT"):
        Bench(labo, models={"ABSENT": relance_scriptee()})
    banc = Bench(labo)
    with pytest.raises(RuntimeError, match="async with"):
        _ = banc.loom
    async with Bench(labo, tools={"inexistant": lambda: "x"}) as banc:
        with pytest.raises(BenchError, match="faux pour un outil inconnu, 'inexistant'"):
            await banc.run("demo", CALCUL)
    async with Bench(labo) as banc, Bench(labo) as autre:
        result = await autre.run("demo", CALCUL)
        with pytest.raises(BenchError, match="n'a pas été lancé par ce banc"):
            banc.outcome(result)


async def test_a_conversation_on_the_bench(labo: Path) -> None:
    """Deux tours d'une même session : le second voit le premier."""
    vus: list[int] = []

    def repond(request: Any) -> Message:
        vus.append(len(request.messages))
        return Message.assistant(f"tour {len(vus)}")

    async with Bench(labo, models={"MAIN": ScriptedModel(repond, repond)}) as banc:
        session = SessionId("conversation")
        premier = await banc.run("demo", "Bonjour.", session_id=session)
        second = await banc.run("demo", "Et ensuite ?", session_id=session)
    assert (premier.text, second.text) == ("tour 1", "tour 2")
    assert vus[1] > vus[0]


async def test_a_bench_journal_replays(labo: Path) -> None:
    fichier = labo.parent / "banc.jsonl"
    async with Bench(labo, models={"MAIN": relance_scriptee()}) as banc:
        result = await banc.run("demo", RELANCE)
        assert len(await banc.events(result)) > 0
        await banc.export(result, fichier)
    [rejoue] = await assert_replays(labo, fichier)
    assert rejoue.identical and [r.run_id for r in rejoue.reports] == [result.run_id]


async def test_an_instance_serves_provided_models_and_leaves_them_open(labo: Path) -> None:
    calcul = ScriptedModel(
        tool_call_message(("c1", "calculer", {"expr": "2+2"})), Message.assistant("2 + 2 = 4.")
    )
    fourni = Ferme(calcul)
    with pytest.raises(ConfigError, match="non déclarés : ABSENT"):
        Loom(load_config(labo), models={"ABSENT": fourni})
    async with Loom(load_config(labo), models={"MAIN": fourni}) as loom:
        result = await loom.run("demo", CALCUL)
    assert result.text == "2 + 2 = 4." and calcul.remaining == 0
    # Il reste à l'appelant : l'instance ne l'a pas fermé.
    assert not fourni.closed


async def test_an_instance_evaluates_with_its_provided_models(labo: Path) -> None:
    """Les clients fournis servent à l'éval — l'agent et le juge — et restent à l'appelant."""
    calcul = Ferme(
        ScriptedModel(
            tool_call_message(("c1", "calculer", {"expr": "2+2"})), Message.assistant("Quatre.")
        )
    )
    notes: dict[str, JsonValue] = {
        "criteria": [{"name": "fidele", "score": 1.0, "reason": "Vu par le client fourni."}]
    }
    juge = Ferme(ScriptedModel(tool_call_message(("v1", "verdict", notes))))
    lue = EvalSuite.model_validate(
        {
            "agent": "demo",
            "judge": {"model": "JUGE", "criteria": [{"name": "fidele", "rule": "r"}]},
            "cases": [{"name": "calcul", "input": CALCUL, "expect": {"contains": ["Quatre"]}}],
        }
    )
    async with Loom(load_config(labo), models={"MAIN": calcul, "JUGE": juge}) as loom:
        report = await loom.evaluate(lue)
    [run] = report.runs
    assert run.passed, [c for c in run.checks if not c.passed]
    assert [c.detail for c in run.checks if c.label.startswith("juge")] == [
        "note 1.00 — Vu par le client fourni."
    ]
    assert not calcul.closed and not juge.closed


async def test_provided_models_do_not_serve_a_config_the_suite_designates(labo: Path) -> None:
    """Ces clients portent des identifiants de la config de l'instance, pas d'une autre."""
    models = modeles_qui_repondent(labo, "Réponse de l'autre config.")
    autre = retouche(labo, labo.parent / "autre.yaml", models=models)
    lue = load_suite(
        suite(
            labo,
            config=autre.name,
            cases=[{"name": "calcul", "input": CALCUL, "expect": {"contains": ["autre config"]}}],
        )
    )
    fourni = Ferme(ScriptedModel(Message.assistant("Quatre.")))
    async with Loom(load_config(labo), models={"MAIN": fourni}) as loom:
        report = await loom.evaluate(lue)
    assert report.passed and fourni.inner.remaining == 1


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


def _envoi(destinataire: str) -> dict[str, Any]:
    return {"name": "envoyer", "arguments": {"destinataire": destinataire}}


def _issue(tools: tuple[ToolUse, ...]) -> Outcome:
    return Outcome(status=RunStatus.COMPLETED, text="", data=None, error_type=None, tools=tools)


def _journal(path: Path) -> list[Event]:
    return [Event.model_validate_json(line) for line in lignes(path)]


async def _cli(argv: list[str]) -> int:
    """La commande lance sa propre boucle : on la fait tourner hors de celle de l'essai."""
    return await asyncio.to_thread(cli_main, argv)
