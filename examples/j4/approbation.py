# SPDX-License-Identifier: Apache-2.0
"""Phase 4.3a : faire valider un outil sensible avant qu'il agisse.

    uv run python examples/j4/approbation.py                 # les quatre cas
    uv run python examples/j4/approbation.py --cas refus
    uv run --env-file .env --extra anthropic --extra openai \\
        python examples/j4/approbation.py --reel

Config : ``examples/j4/relance/``, à laquelle l'exemple ajoute en code un
outil à effet de bord — ``envoyer_email``, en ``approval: always`` — et rend
le rôle non terminal pour que l'orchestrateur reçoive l'e-mail et l'envoie.
Les fichiers de ``relance/`` ne changent pas.

Un appel qui demande une approbation ne s'exécute pas : il attend. Quatre
façons pour cette attente de finir :

* **accordé** (``--cas accord``) : ``Loom.approve()`` écrit la décision et
  remet le run en file. L'appel est rejoué, avec les arguments de
  l'approbateur s'il les a corrigés.
* **refusé** (``--cas refus``) : ``Loom.reject()``. L'outil n'est **jamais**
  appelé ; le motif revient au modèle comme résultat d'erreur, et le run se
  termine normalement — un refus n'est pas une panne.
* **périmé** (``--cas delai``) : personne ne répond avant ``expire_at``. La
  demande expire, et c'est le **journal** qui le dit : un travail différé
  ramène le run au bon moment, mais s'il est perdu, la prochaine reprise
  l'écrit quand même.
* **en ligne** (``--cas en_ligne``) : ``run(..., approver=…)``. Le rappel
  tranche dans la boucle, le run ne passe jamais par ``PAUSED``, et la demande
  comme la décision sont journalisées avec l'identité de l'approbateur.
* **sous-agent** (``--cas sous_agent``) : c'est un **enfant** qui attend.
  L'envoi est confié à un second agent, ``secretaire`` ; il se met en pause,
  et son parent passe en ``WAITING_CHILD``. La racine rend pourtant la demande
  de son enfant, et l'approbation se donne sur elle : l'appelant n'a pas à
  savoir qu'un sous-agent existe. À la reprise, la racine rejoue l'appel
  délégant, qui **reprend** l'enfant là où il s'était arrêté.

Dans tous les cas, le lot partiel se voit : ``chercher_devis`` et le rôle
s'exécutent, et le run ne s'arrête que pour l'envoi.
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any

from loom_ia.access.api import Loom, RunResult
from loom_ia.adapters.models import ModelConfigError
from loom_ia.agents import SubAgentRef
from loom_ia.config import ConfigError, LoomConfig, load_config
from loom_ia.core.events import ApprovalRequested, Event, ToolCalled, ToolCompleted
from loom_ia.core.model import (
    ApprovalDecision,
    ApprovalSettings,
    Approved,
    ModelSpec,
    PendingApproval,
    Rejected,
    SessionId,
    new_id,
)
from loom_ia.runtime import apply_logging, prompt_text
from loom_ia.tools import tool

CONFIG = Path(__file__).parent / "relance" / "loom.yaml"
DEMANDE = "Relance le client du devis D-2026-042, sur un ton cordial."
CAS = ("accord", "refus", "delai", "en_ligne", "sous_agent")
ENVOI = "envoyer_email"
# Délai laissé à l'approbateur dans le cas `delai`. Assez court pour que
# l'exemple ne traîne pas, assez long pour que la pause s'installe d'abord.
DELAI = 2.0
CORRIGE = "comptabilite@martin.example"
CONSIGNE_ENVOI = (
    "\n\nUne fois la relance rédigée, envoie-la avec `envoyer_email` : "
    "objet et corps tels que le rôle les a produits, destinataire "
    "`mme.martin@example.com`. N'annonce pas l'envoi avant de l'avoir fait.\n"
)


@tool
async def envoyer_email(destinataire: str, objet: str = "", corps: str = "") -> str:
    """Envoie l'e-mail de relance au client. Irréversible : parti, il est parti."""
    return f"Envoyé à {destinataire} — objet « {objet[:60]} »"


def main_script(envoye: bool) -> list[dict[str, Any]]:
    """Script de l'orchestrateur ; sa dernière réponse dit ce qui s'est passé."""
    # Le numéro du devis y figure parce qu'un compte rendu utile le nomme —
    # plus parce qu'un contrôle l'exige : depuis 4.5, `cite_le_devis` porte
    # sur l'e-mail rédigé, jamais sur la réponse finale.
    fin = (
        "La relance du devis D-2026-042 est partie à Mme Martin."
        if envoye
        else "Je n'ai pas pu envoyer la relance du devis D-2026-042 : l'envoi n'a pas été autorisé."
    )
    return [
        {
            "text": "Je relis le devis.",
            "tool_calls": [{"name": "chercher_devis", "arguments": {"numero": "D-2026-042"}}],
        },
        {"tool_calls": [{"name": "rediger_relance", "arguments": {"ton": "cordial"}}]},
        {
            "text": "L'e-mail est prêt, je l'envoie.",
            "tool_calls": [
                {
                    "name": ENVOI,
                    "arguments": {
                        "destinataire": "mme.martin@example.com",
                        "objet": "Votre devis D-2026-042",
                        "corps": "Bonjour Madame Martin, …",
                    },
                }
            ],
        },
        {"text": fin},
    ]


SECRETAIRE_NOM = "secretaire"
SECRETAIRE_SYSTEM = (
    "Tu es le secrétaire de l'artisan. On te confie une relance déjà rédigée : "
    "envoie-la avec `envoyer_email` au destinataire indiqué, puis dis en une "
    "phrase que c'est fait, en citant le numéro du devis. N'écris rien d'autre."
)
CONSIGNE_DELEGUE = (
    "\n\nUne fois la relance rédigée, confie-la au sous-agent `secretaire` : "
    "passe-lui l'objet, le corps et le destinataire `mme.martin@example.com`. "
    "C'est lui qui l'envoie, pas toi.\n"
)


def delegue_script() -> list[dict[str, Any]]:
    """Script de l'orchestrateur qui délègue l'envoi."""
    return [
        {
            "text": "Je relis le devis.",
            "tool_calls": [{"name": "chercher_devis", "arguments": {"numero": "D-2026-042"}}],
        },
        {"tool_calls": [{"name": "rediger_relance", "arguments": {"ton": "cordial"}}]},
        {
            "text": "Je confie l'envoi au secrétaire.",
            "tool_calls": [
                {
                    "name": "secretaire",
                    "arguments": {
                        "message": (
                            "Envoie à mme.martin@example.com la relance du devis D-2026-042, "
                            "objet « Votre devis D-2026-042 »."
                        )
                    },
                }
            ],
        },
        {"text": "La relance du devis D-2026-042 est partie."},
    ]


SECRETAIRE: list[dict[str, Any]] = [
    {
        "text": "J'envoie.",
        "tool_calls": [
            {
                "name": ENVOI,
                "arguments": {
                    "destinataire": "mme.martin@example.com",
                    "objet": "Votre devis D-2026-042",
                    "corps": "Bonjour Madame Martin, …",
                },
            }
        ],
    },
    {"text": "Relance du devis D-2026-042 envoyée."},
]


ROLE: list[dict[str, Any]] = [
    {
        "text": (
            '{"objet": "Votre devis D-2026-042", "corps": "Bonjour Madame Martin,\\n\\nJe '
            "reviens vers vous au sujet du devis D-2026-042 de 1 840 € pour le remplacement "
            "de votre chauffe-eau, envoyé le 2 septembre. Avez-vous pu en prendre "
            'connaissance ?\\n\\nBien cordialement,\\nPlomberie Dupont"}'
        )
    },
]


def shown(path: Path) -> str:
    return os.path.relpath(path)


def adjusted(
    config: LoomConfig,
    *,
    reel: bool,
    envoye: bool,
    delai: float | None,
    delegue: bool = False,
) -> tuple[LoomConfig, str]:
    """Ajoute l'outil sensible à l'agent, et rend le rôle non terminal.

    Le rôle de ``relance/`` est terminal : sa sortie **est** la réponse finale,
    et le run s'arrêterait là. Ici l'orchestrateur doit la recevoir pour
    l'envoyer : c'est tout ce qui change du rôle.

    Avec ``delegue``, l'envoi passe à un second agent (``secretaire``) qui seul
    porte l'outil sensible : c'est lui qui se met en pause, et son parent
    attend avec lui.
    """
    base = "relance_reel" if reel else "relance"
    spec = next(a for a in config.agents if a.name == base)
    consigne = CONSIGNE_DELEGUE if delegue else CONSIGNE_ENVOI
    main = spec.main.model_copy(
        update={"system": prompt_text(spec.main) + consigne, "system_file": None}
    )
    roles = tuple(
        role.model_copy(update={"terminal": False}) if role.name == "rediger_relance" else role
        for role in spec.roles
    )
    approval = ApprovalSettings(expires_in=delai) if delai is not None else ApprovalSettings()
    sensible = _outil_sensible()
    agent = spec.model_copy(
        update={
            "main": main,
            "roles": roles,
            "tools": spec.tools if delegue else (*spec.tools, sensible),
            "approval": approval,
            "subagents": (_secretaire_ref(),) if delegue else spec.subagents,
        }
    )
    agents = tuple(agent if a.name == base else a for a in config.agents)
    if delegue:
        agents = (*agents, _secretaire(spec, sensible, approval, reel=reel))
    models = list(config.models)
    if not reel:

        def script(spec: ModelSpec, replies: list[dict[str, Any]]) -> ModelSpec:
            return spec.model_copy(update={"params": {**spec.params, "script": replies}})

        models = [
            script(m, delegue_script() if delegue else main_script(envoye))
            if m.id == "FAKE_MAIN"
            else script(m, SECRETAIRE)
            if m.id == "FAKE_RESUME" and delegue
            else script(m, ROLE)
            if m.id == "FAKE_ROLE"
            else m
            for m in models
        ]
    return config.model_copy(update={"models": tuple(models), "agents": agents}), base


def _secretaire(spec: Any, sensible: Any, approval: ApprovalSettings, *, reel: bool) -> Any:
    """Second agent : il ne sait qu'envoyer, et c'est lui qui demande l'approbation."""
    modele = "HAIKU" if reel else "FAKE_RESUME"
    return spec.model_copy(
        update={
            "name": SECRETAIRE_NOM,
            "description": "Envoie une relance déjà rédigée.",
            "main": spec.main.model_copy(
                update={"model": modele, "system": SECRETAIRE_SYSTEM, "system_file": None}
            ),
            "tools": (sensible,),
            "roles": (),
            "judges": (),
            "subagents": (),
            "policies": (),
            "approval": approval,
            "expose": spec.expose.model_copy(update={"rest": False, "mcp": False}),
            "max_iterations": 4,
        }
    )


def _secretaire_ref() -> SubAgentRef:
    """Référence au sous-agent, telle que l'écrirait la config."""
    return SubAgentRef(agent=SECRETAIRE_NOM, description="Envoie une relance déjà rédigée.")


def _outil_sensible() -> Any:
    """Référence à ``envoyer_email``, telle que l'écrirait la config."""
    spec = next(a for a in load_config(CONFIG).agents if a.name == "relance").tools[0]
    return spec.model_copy(
        update={
            "python": ENVOI,
            "side_effects": "irreversible",
            "idempotent": False,
            "approval": "always",
        }
    )


def outcome(result: RunResult) -> str:
    if result.ok:
        return "terminé"
    if result.error_type:
        return f"{result.status} ({result.error_type})"
    return str(result.status)


def envoi(events: list[Event]) -> str:
    """Ce que l'envoi est devenu : parti, refusé, ou jamais tenté."""
    appels = [e for e in events if e.type == "tool.called" and e.facets.get("tool_name") == ENVOI]
    finis = [
        e.payload
        for e in events
        if e.type == "tool.completed" and e.facets.get("tool_name") == ENVOI
    ]
    if not finis:
        return "jamais tenté"
    fini = finis[-1]
    assert isinstance(fini, ToolCompleted)
    texte = "".join(getattr(b, "text", "") for b in fini.output.blocks)
    marque = "jamais appelé" if not appels else "appelé"
    return f"{marque} — {texte.strip()}"


def demande(events: list[Event]) -> ApprovalRequested | None:
    return next(
        (p for e in events if isinstance(p := e.payload, ApprovalRequested)),
        None,
    )


def entete(events: list[Event]) -> None:
    """Ce que le run a fait avant de s'arrêter, et ce qu'il attend."""
    faits = [
        str(e.facets.get("tool_name"))
        for e in events
        if isinstance(e.payload, ToolCalled | ToolCompleted) and e.type == "tool.completed"
    ]
    print(f"  déjà fait : {', '.join(dict.fromkeys(faits)) or '—'}")
    asked = demande(events)
    if asked is not None:
        echeance = "" if asked.expire_at is None else f", jusqu'à {asked.expire_at:%H:%M:%S}"
        print(f"  en attente : {asked.tool_name} — {asked.reason}{echeance}")
        print(f"    arguments : {asked.arguments}")


async def accord(loom: Loom, agent: str, session: SessionId) -> None:
    print("— Accordé : l'artisan valide, et l'appel part avec ses corrections\n")
    result = await loom.run(agent, DEMANDE, session_id=session)
    events = await loom.export_session(session)
    print(f"  run       : {outcome(result)}")
    entete(events)
    asked = result.pending_approvals[0]
    print(f"  l'artisan valide, en corrigeant le destinataire → {CORRIGE}")
    await loom.approve(
        result.run_id,
        call_id=asked.call_id,
        by="l'artisan",
        arguments={**asked.arguments, "destinataire": CORRIGE},
        session_id=session,
    )
    await loom.drain()
    fin = await loom.result(result.run_id, session_id=session)
    events = await loom.export_session(session)
    print(f"  reprise   : {outcome(fin)} en {fin.iterations} itération(s)")
    print(f"  envoi     : {envoi(events)}")
    print(f"  réponse   : {fin.text.splitlines()[0][:70] if fin.text else '—'}\n")


async def refus(loom: Loom, agent: str, session: SessionId) -> None:
    print("— Refusé : l'outil n'est jamais appelé, le motif revient au modèle\n")
    result = await loom.run(agent, DEMANDE, session_id=session)
    events = await loom.export_session(session)
    print(f"  run       : {outcome(result)}")
    entete(events)
    motif = "le devis n'est pas signé, on n'envoie rien pour l'instant"
    print(f"  l'artisan refuse : « {motif} »")
    await loom.reject(result.run_id, by="l'artisan", reason=motif, session_id=session)
    await loom.drain()
    fin = await loom.result(result.run_id, session_id=session)
    events = await loom.export_session(session)
    print(f"  reprise   : {outcome(fin)} — un refus n'est pas une panne")
    print(f"  envoi     : {envoi(events)}")
    print(f"  réponse   : {fin.text.splitlines()[0][:70] if fin.text else '—'}\n")


async def delai(loom: Loom, agent: str, session: SessionId) -> None:
    print(f"— Périmé : personne ne répond dans les {DELAI:g} s\n")
    result = await loom.run(agent, DEMANDE, session_id=session)
    events = await loom.export_session(session)
    print(f"  run       : {outcome(result)}")
    entete(events)
    print("  personne ne répond ; le réveil différé ramènera le run à l'échéance…")
    await asyncio.sleep(DELAI + 1.0)
    await loom.drain()
    fin = await loom.result(result.run_id, session_id=session)
    events = await loom.export_session(session)
    expiree = [e for e in events if e.type == "approval.expired"]
    print(f"  journal   : {len(expiree)} approval.expired — c'est lui qui fait foi")
    print(f"  reprise   : {outcome(fin)}")
    print(f"  envoi     : {envoi(events)}")
    print(f"  réponse   : {fin.text.splitlines()[0][:70] if fin.text else '—'}\n")


async def en_ligne(loom: Loom, agent: str, session: SessionId) -> None:
    print("— En ligne : le rappel tranche dans la boucle, sans pause durable\n")
    vues: list[PendingApproval] = []

    async def approbateur(asked: PendingApproval) -> ApprovalDecision:
        vues.append(asked)
        print(f"  [rappel] {asked.tool_name} → {asked.arguments.get('destinataire')}")
        if str(asked.arguments.get("destinataire", "")).endswith("example.com"):
            return Approved(by="l'artisan", reason="cliente connue")
        return Rejected(by="l'artisan", reason="destinataire inconnu")

    result = await loom.run(agent, DEMANDE, session_id=session, approver=approbateur)
    events = await loom.export_session(session)
    pauses = [e for e in events if e.facets.get("to_state") == "paused"]
    print(f"  run       : {outcome(result)} en {result.iterations} itération(s)")
    print(f"  demandes  : {len(vues)} vue(s) par le rappel, {len(pauses)} passage(s) en pause")
    print(f"  envoi     : {envoi(events)}")
    print(f"  réponse   : {result.text.splitlines()[0][:70] if result.text else '—'}\n")


async def sous_agent(loom: Loom, agent: str, session: SessionId) -> None:
    print("— Sous-agent : c'est l'enfant qui attend, et son parent avec lui\n")
    result = await loom.run(agent, DEMANDE, session_id=session)
    events = await loom.export_session(session)
    enfants = {e.run_id for e in events if e.run_id != result.run_id}
    print(f"  racine    : {outcome(result)} ({len(enfants)} sous-run)")
    entete(events)
    asked = result.pending_approvals[0]
    print(f"  la racine rend la demande de son enfant : {asked.tool_name}")
    print("  l'artisan valide, sur la racine")
    await loom.approve(result.run_id, by="l'artisan", session_id=session)
    await loom.drain()
    fin = await loom.result(result.run_id, session_id=session)
    events = await loom.export_session(session)
    rejoue = [e for e in events if e.type == "tool.called" and getattr(e.payload, "resumed", False)]
    demarres = len([e for e in events if e.type == "run.started"])
    print(f"  reprise   : {outcome(fin)} — {len(rejoue)} appel délégant rejoué")
    print(f"  runs      : {demarres} au total — l'enfant est repris, pas relancé")
    print(f"  envoi     : {envoi(events)}")
    print(f"  réponse   : {fin.text.splitlines()[0][:70] if fin.text else '—'}\n")


async def jouer(nom: str, config: LoomConfig, session: SessionId) -> None:
    """Un cas, dans son instance : chacun a ses réglages d'approbation."""
    async with Loom(config) as loom:
        loom.register(ENVOI, envoyer_email)
        # L'agent à lancer est la racine : celui qui n'est pas un sous-agent.
        enfants = {ref.agent for a in config.agents for ref in a.subagents}
        agent = next(
            a.name
            for a in config.agents
            if a.name not in enfants
            and (
                any(getattr(t, "python", None) == ENVOI for t in a.tools)
                or any(ref.agent == SECRETAIRE_NOM for ref in a.subagents)
            )
        )
        if nom == "accord":
            await accord(loom, agent, session)
        elif nom == "refus":
            await refus(loom, agent, session)
        elif nom == "delai":
            await delai(loom, agent, session)
        elif nom == "en_ligne":
            await en_ligne(loom, agent, session)
        else:
            await sous_agent(loom, agent, session)


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Approbation d'un outil sensible")
    parser.add_argument("--reel", action="store_true", help="vrais modèles")
    parser.add_argument("--cas", action="append", choices=CAS, help="cas à jouer (tous par défaut)")
    parser.add_argument("--session", default=None, help="préfixe des sessions")
    args = parser.parse_args(argv)
    cas = tuple(args.cas) if args.cas else CAS
    prefixe = args.session or f"atelier-{new_id()[-8:]}"

    try:
        base = load_config(CONFIG)
    except ConfigError as error:
        print(f"Configuration : {error}", file=sys.stderr)
        return 2
    apply_logging(base)
    print(f"Sessions : {prefixe}-<cas>")
    print(f"Outil    : {ENVOI} (side_effects: irreversible, approval: always)\n")
    for nom in cas:
        try:
            config, _ = adjusted(
                base,
                reel=args.reel,
                envoye=nom in {"accord", "en_ligne"},
                delai=DELAI if nom == "delai" else None,
                delegue=nom == "sous_agent",
            )
            await jouer(nom, config, SessionId(f"{prefixe}-{nom}"))
        except (ModelConfigError, ConfigError) as error:
            print(f"Configuration : {error}", file=sys.stderr)
            return 2
    print(f"Export : uv run loom --config {shown(CONFIG)} sessions list")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
