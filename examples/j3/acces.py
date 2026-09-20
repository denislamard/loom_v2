# SPDX-License-Identifier: Apache-2.0
"""Phase 3.6 : le scénario du jalon J3 par les trois accès, puis le rapport de coûts.

    uv run --extra http --extra mcp python examples/j3/acces.py
    uv run --extra http --extra mcp python examples/j3/acces.py --cas b --cas c2

Même config que les exemples précédents (``examples/j3/relance/``), modèles
simulés. L'exemple en tire un agent par cas du jalon, copie de ``relance``
avec ses propres modèles simulés et leurs scripts (M2) :

- **a**, normalisation : le rôle enveloppe son JSON dans du texte et un bloc
  de code ; le contrat de sortie l'en extrait (comme ``contrats.py``) ;
- **b**, montant inventé : le rôle promet une réduction absente du devis ; le
  juge refuse, le rôle corrige, le juge accepte (comme ``juge.py``) ;
- **c1**, budget : le juge refuse deux fois ; ses appels ont épuisé le budget
  du run, qui s'arrête sur une réponse forcée sans outils (comme ``budget.py``) ;
- **c2**, panne : le modèle de l'orchestrateur est en panne simulée ; après
  ses tentatives, bascule vers son secours (comme ``secours.py``).

Chaque cas passe par les trois accès :

- l'API Python : ``Loom.run()`` ;
- l'API REST : un vrai serveur, sur un port libre de la machine ;
- le serveur MCP : client et serveur dans le même process.

Pour chaque cas : le déroulé, tiré du journal (chaque décision y est), puis
ce que chaque accès a rendu — statut, ``unverified``, coût, verdicts des
juges — et la comparaison : même suite d'événements, même réponse, même coût,
même ventilation, mêmes verdicts. Pour finir, le rapport de coûts de chaque
cas, tel que le rend l'outil MCP ``run_report``.

Le journal va dans ``examples/j3/relance/data/`` (ignoré par git).
"""

import argparse
import asyncio
import json
import socket
import sys
import threading
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loom_ia.access.api import Loom
from loom_ia.access.progress import Progress
from loom_ia.agents import RoleSpec
from loom_ia.config import ConfigError, LoomConfig, load_config
from loom_ia.core.events import (
    BudgetExceeded,
    Event,
    GuardChecked,
    ModelFellBack,
    ModelResponded,
    ModelRetried,
)
from loom_ia.core.model import Budgets, ModelSpec, RetryPolicy, RunId
from loom_ia.runtime import apply_logging

CONFIG = Path(__file__).parent / "relance" / "loom.yaml"
BASE = "relance"
QUESTION = "Relance le client du devis D-2026-042, sur un ton cordial."
ACCESSES = ("python", "rest", "mcp")
# Les appels REST vont sur la boucle locale : pas de proxy.
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

CORPS = (
    "Bonjour Madame Martin,\n\nJe reviens vers vous au sujet du devis D-2026-042 de "
    "1 840 € pour le remplacement de votre chauffe-eau, envoyé le 2 septembre. "
    "Avez-vous pu en prendre connaissance ?{promesse}\n\nBien cordialement,\nPlomberie Dupont"
)
PROMESSE = " Pour une signature avant la fin du mois, nous vous accordons une réduction de 10 %."
CONSIGNES = "Aucune réduction : elle n'est pas dans le devis."
OBJET = "Votre devis D-2026-042"
FIDELE = {"objet": OBJET, "corps": CORPS.format(promesse="")}
INVENTEE = {"objet": OBJET, "corps": CORPS.format(promesse=PROMESSE)}
FORCEE = "Relance du devis D-2026-042 non rédigée : budget du run atteint. À reprendre à la main."


def email(content: dict[str, str]) -> str:
    return json.dumps(content, ensure_ascii=False)


# L'orchestrateur simulé : chercher le devis, faire rédiger, puis (si la
# rédaction échoue) le dire en une phrase.
MAIN: list[dict[str, Any]] = [
    {"tool_calls": [{"name": "chercher_devis", "arguments": {"numero": "D-2026-042"}}]},
    {"tool_calls": [{"name": "rediger_relance", "arguments": {"ton": "cordial"}}]},
    {"text": "La relance du devis D-2026-042 n'a pas pu être rédigée : à reprendre à la main."},
]
# Cas c1 : après l'échec du rôle, l'orchestrateur le rappellerait avec des
# consignes ; « forced » : sa réponse forcée, sans outils.
MAIN_BUDGET: list[dict[str, Any]] = [
    *MAIN[:2],
    {
        "tool_calls": [
            {"name": "rediger_relance", "arguments": {"ton": "cordial", "consignes": CONSIGNES}}
        ],
        "forced": FORCEE,
    },
]


@dataclass(frozen=True)
class Case:
    """Un cas du jalon : ses scripts, son réglage, et le résultat attendu."""

    key: str
    title: str
    main: list[dict[str, Any]]
    role: list[dict[str, Any]]
    expected: str
    # Vrai si le run a donné le résultat attendu (réponse, journal).
    check: Callable[[dict[str, Any], list[Event]], bool]
    budget: float | None = None
    outage: bool = False

    @property
    def agent(self) -> str:
        return f"{BASE}_{self.key}"


def _normalized(result: dict[str, Any], events: list[Event]) -> bool:
    return result["data"] == FIDELE and any(
        isinstance(e.payload, GuardChecked) and e.payload.normalized for e in events
    )


def _corrected(result: dict[str, Any], events: list[Event]) -> bool:
    blocked = [v["blocked"] for v in result["verdicts"]]
    return result["data"] == FIDELE and blocked == [True, False]


def _forced(result: dict[str, Any], events: list[Event]) -> bool:
    exceeded = any(isinstance(e.payload, BudgetExceeded) for e in events)
    return result["text"] == FORCEE and exceeded


def _fell_back(result: dict[str, Any], events: list[Event]) -> bool:
    fell = any(isinstance(e.payload, ModelFellBack) for e in events)
    return result["data"] == FIDELE and fell


CASES: tuple[Case, ...] = (
    Case(
        "a",
        "normalisation de la sortie du rôle",
        MAIN,
        [{"text": f"Voici l'e-mail :\n\n```json\n{email(FIDELE)}\n```"}],
        "le JSON est extrait du texte, sans appel au modèle ; le juge accepte",
        _normalized,
    ),
    Case(
        "b",
        "montant inventé, refusé par le juge puis corrigé",
        MAIN,
        [{"text": email(INVENTEE)}, {"text": email(FIDELE)}],
        "le juge refuse la réduction inventée, le rôle corrige, le juge accepte",
        _corrected,
    ),
    Case(
        "c1",
        "budget dépassé, réponse forcée",
        MAIN_BUDGET,
        [
            {"without_text": CONSIGNES, "text": email(INVENTEE)},
            {"without_text": CONSIGNES, "text": email(INVENTEE)},
            {"with_text": CONSIGNES, "text": email(FIDELE)},
        ],
        "deux refus du juge ; le budget du run est atteint : réponse forcée sans outils",
        _forced,
        budget=0.002,
    ),
    Case(
        "c2",
        "orchestrateur en panne, bascule vers le secours",
        MAIN,
        [{"text": email(FIDELE)}],
        "le modèle en panne échoue après ses tentatives ; son secours termine le run",
        _fell_back,
        outage=True,
    ),
)


def scenario(config: LoomConfig, cases: tuple[Case, ...]) -> LoomConfig:
    """Config de l'exemple : un agent par cas, copie de ``relance`` avec ses modèles simulés."""
    base = next(a for a in config.agents if a.name == BASE)
    usual = config.model_spec(base.main.model)
    models = list(config.models)
    agents = list(config.agents)
    for case in cases:
        suffix = case.key.upper()
        main_id = f"{usual.id}_{suffix}"
        models.append(_scripted(usual, main_id, case.main))
        roles: list[RoleSpec] = []
        for role in base.roles:
            role_id = f"{role.model}_{suffix}"
            models.append(_scripted(config.model_spec(role.model), role_id, case.role))
            roles.append(role.model_copy(update={"model": role_id}))
        main = base.main.model_copy(update={"model": main_id})
        if case.outage:
            # Copie du modèle de l'orchestrateur, en panne à chaque appel ; secours : le modèle.
            down = f"FAKE_PANNE_{suffix}"
            retry = RetryPolicy(max_attempts=2, initial_delay=0.1)
            outage = {
                "id": down,
                "model": "fake-panne",
                "params": {"script": [{"error": "overloaded"}]},
                "retry": retry,
            }
            models.append(usual.model_copy(update=outage))
            main = main.model_copy(update={"model": down, "fallbacks": (main_id,)})
        changes: dict[str, Any] = {
            "name": case.agent,
            "description": f"Relance, cas {case.key} du jalon J3 : {case.title}.",
            "main": main,
            "roles": tuple(roles),
        }
        if case.budget is not None:
            budget = {"run": {"max_cost": case.budget}, "on_exceed": "stop"}
            changes["budget"] = Budgets.model_validate(budget)
        agents.append(base.model_copy(update=changes))
    return config.model_copy(update={"models": tuple(models), "agents": tuple(agents)})


def _scripted(spec: ModelSpec, model_id: str, replies: list[dict[str, Any]]) -> ModelSpec:
    return spec.model_copy(update={"id": model_id, "params": {**spec.params, "script": replies}})


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Le scénario du jalon J3 par les trois accès")
    parser.add_argument(
        "--cas", action="append", choices=[c.key for c in CASES], help="cas à jouer (défaut : tous)"
    )
    args = parser.parse_args(argv)
    cases = tuple(c for c in CASES if args.cas is None or c.key in args.cas)
    try:
        config = scenario(load_config(CONFIG), cases)
    except ConfigError as error:
        print(f"Configuration : {error}", file=sys.stderr)
        return 2
    apply_logging(config)

    print(f"> {QUESTION}")
    print(f"  cas : {', '.join(c.key for c in cases)} ; accès : {', '.join(ACCESSES)}")
    results: dict[str, dict[str, dict[str, Any]]] = {}
    print("\nAccès Python…")
    results["python"] = await by_python(config, cases)
    print("Accès REST…")
    results["rest"] = await asyncio.to_thread(by_rest, config, cases)
    print("Accès MCP…")
    results["mcp"], reports = await by_mcp(config, cases)

    success = True
    async with Loom(config) as loom:
        for case in cases:
            journals = {
                access: await loom.events(RunId(results[access][case.key]["run_id"]))
                for access in ACCESSES
            }
            success &= show(case, {a: results[a][case.key] for a in ACCESSES}, journals)

    print("\n— Rapport de coûts, par l'outil MCP run_report —")
    for case in cases:
        print(f"\nCas {case.key}")
        print(reports[case.key])
    print(
        "\nChaque cas donne le résultat attendu, de façon identique par les trois accès."
        if success
        else "\nUn cas au moins diffère de ce qui est attendu : voir plus haut."
    )
    return 0 if success else 1


def show(case: Case, results: dict[str, dict[str, Any]], journals: dict[str, list[Event]]) -> bool:
    """Déroulé du cas, ce que chaque accès a rendu, et la comparaison."""
    print(f"\n— Cas {case.key} : {case.title} —")
    print(f"  attendu : {case.expected}")
    progress = Progress()
    for line in filter(None, (describe(e, progress) for e in journals["python"])):
        print(f"  {line}")
    print(f"  réponse : {shortened(results['python']['text'])}")
    for access in ACCESSES:
        print(f"  {access:<6} : {outcome(results[access], journals[access])}")
    kept = {access: comparable(results[access]) for access in ACCESSES}
    kinds = {access: [event.type for event in journals[access]] for access in ACCESSES}
    differ = sorted(
        {
            key
            for access in ACCESSES
            for key, value in kept[access].items()
            if value != kept["python"][key]
        }
    )
    same_journal = all(kinds[access] == kinds["python"] for access in ACCESSES)
    if not same_journal:
        differ.append("suite d'événements")
    expected = case.check(results["python"], journals["python"])
    print(
        "  identique par les trois accès : réponse, statut, coût, ventilation, verdicts, journal"
        if not differ
        else f"  différences entre accès : {', '.join(differ)}"
    )
    print(f"  résultat attendu : {'oui' if expected else 'non'}")
    return expected and not differ


def describe(event: Event, progress: Progress) -> str | None:
    """Une ligne par étape : appels de modèle (rôle, coût), puis le déroulé de la CLI."""
    payload = event.payload
    if isinstance(payload, ModelResponded):
        usage = payload.usage
        return (
            f"· modèle [{event.role}] {payload.model_id} · "
            f"{usage.input_tokens}/{usage.output_tokens} tokens · {payload.cost_usd:.5f} $"
        )
    if isinstance(payload, ModelRetried):
        return (
            f"· tentative {payload.attempt} de {payload.model_id} en échec : {payload.error_kind}"
        )
    return progress.line(event)


def outcome(result: dict[str, Any], events: list[Event]) -> str:
    """Ce qu'un accès a rendu, en une ligne."""
    verdicts = " → ".join("refus" if v["blocked"] else "accord" for v in result["verdicts"])
    checked = "non vérifiée" if result["unverified"] else "vérifiée"
    cost = f"{result['cost_usd']:.5f} $".replace(".", ",")
    return (
        f"{result['status']} · {checked} · {cost} · juge : {verdicts or '—'} · "
        f"{len(events)} événements · {result['run_id']}"
    )


def shortened(text: str, width: int = 96) -> str:
    """Le texte sur une ligne, coupé à ``width`` caractères."""
    line = " ".join(text.split())
    return line if len(line) <= width else f"{line[: width - 1]}…"


def comparable(result: dict[str, Any]) -> dict[str, Any]:
    """Ce qui doit être identique d'un accès à l'autre (les identifiants de run mis à part)."""
    report: dict[str, Any] = result["report"] or {}
    return {
        "statut": result["status"],
        "réponse": (result["text"], result["data"]),
        "unverified": result["unverified"],
        "erreur": (result["error_type"], result["error"]),
        "coût": (result["cost_usd"], result["usage"]),
        "ventilation": [report.get(part) for part in ("total", "roles", "models")],
        "verdicts": [
            {k: v for k, v in item.items() if k != "run_id"} for item in result["verdicts"]
        ],
    }


# --- Les trois accès ------------------------------------------------------------------


async def by_python(config: LoomConfig, cases: tuple[Case, ...]) -> dict[str, dict[str, Any]]:
    """``Loom.run()`` : le ``RunResult`` de chaque cas, en JSON."""
    async with Loom(config) as loom:
        return {
            case.key: (await loom.run(case.agent, QUESTION)).model_dump(mode="json")
            for case in cases
        }


def by_rest(config: LoomConfig, cases: tuple[Case, ...]) -> dict[str, dict[str, Any]]:
    """Un vrai serveur uvicorn ; un ``POST`` par cas, la réponse est le résultat du run."""
    import uvicorn

    from loom_ia.access.http import create_app

    port = free_port()
    settings = uvicorn.Config(
        create_app(Loom(config), own=True), host="127.0.0.1", port=port, log_level="warning"
    )
    server = uvicorn.Server(settings)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}/v1"
    try:
        while not server.started:
            time.sleep(0.05)
        return {
            case.key: post_json(f"{base}/agents/{case.agent}/runs", {"message": QUESTION})
            for case in cases
        }
    finally:
        server.should_exit = True
        thread.join(timeout=5)


async def by_mcp(
    config: LoomConfig, cases: tuple[Case, ...]
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """Client et serveur MCP dans le même process : l'outil de chaque agent, puis ``run_report``."""
    from mcp.shared.memory import create_connected_server_and_client_session as connected
    from mcp.types import TextContent

    from loom_ia.access.mcp_server import REPORT_TOOL, create_server

    results: dict[str, dict[str, Any]] = {}
    reports: dict[str, str] = {}
    async with Loom(config) as loom:
        async with connected(create_server(loom)) as client:
            for case in cases:
                called = await client.call_tool(case.agent, {"message": QUESTION})
                results[case.key] = called.structuredContent or {}
                report = await client.call_tool(
                    REPORT_TOOL, {"run_id": results[case.key]["run_id"]}
                )
                reports[case.key] = "\n".join(
                    part.text for part in report.content if isinstance(part, TextContent)
                )
    return results, reports


# --- Petits utilitaires HTTP, sans dépendance -----------------------------------------


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def post_json(url: str, body: dict[str, Any]) -> Any:
    request = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST")
    request.add_header("Content-Type", "application/json")
    with OPENER.open(request, timeout=30) as response:
        return json.loads(response.read().decode())


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
