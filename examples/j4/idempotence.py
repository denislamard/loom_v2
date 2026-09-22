# SPDX-License-Identifier: Apache-2.0
"""Phase 4.4 : un effet déjà produit ne se refait pas.

    uv run python examples/j4/idempotence.py                 # les quatre cas
    uv run python examples/j4/idempotence.py --cas rejoue
    uv run --env-file .env --extra anthropic --extra openai \\
        python examples/j4/idempotence.py --reel

Config : ``examples/j4/relance/``, à laquelle l'exemple ajoute en code
l'outil qui envoie vraiment la relance et rend le rôle non terminal, pour que
l'orchestrateur reçoive l'e-mail et l'envoie. La config y déclare le magasin
d'idempotence partagé (``sqlite``, ``data/keys.db``), celui qu'exige une clé
métier ; les quatre premiers cas le ramènent **en code** au magasin
``journal``, qu'ils sont là pour montrer.

Le moteur rejoue un appel interrompu : c'est ce qui fait qu'un run survit à
une panne (4.2). Pour un outil qui envoie un courriel, c'est l'accident.
Quatre façons de le regarder :

* **rejoué** (``--cas rejoue``) : l'outil porte ``@idempotent``. L'effet est
  mémorisé sous la clé de l'appel (``idempotency.recorded``) dès qu'il a eu
  lieu. Le pilote meurt avant que le résultat soit au journal, l'appel
  repart — et rend le résultat mémorisé. La relance part **une fois**, pour
  deux ``tool.called``.
* **état inconnu** (``--cas etat_inconnu``) : le même outil sans
  ``@idempotent``. Le moteur ne le relance pas de lui-même : il rend une
  erreur au modèle, qui l'annonce à l'artisan au lieu de risquer un doublon.
* **humain** (``--cas humain``) : le même, en ``on_unknown: pause``. Le run
  se met en pause et c'est l'artisan qui tranche. S'il accorde, l'appel
  repart — et la relance part une seconde fois : il a vu, il a choisi.
* **deux appels** (``--cas deux_appels``) : la clé technique vaut pour **un**
  appel. Deux appels distincts qui demandent la même chose restent deux
  effets. C'est la limite que lève la clé métier, juste après.

Puis la clé **métier** (4.4b), tirée des arguments de l'outil —
``key=lambda a: f"relance:{a['devis']}"`` — et rangée dans un magasin partagé
(SQLite), que tous les runs voient et qui survit au process :

* **clé métier** (``--cas cle_metier``) : deux **conversations** distinctes
  demandent la même relance. Rien au journal ne les relie — deux sessions,
  deux runs. Une seule relance part, et la seconde reçoit le résultat de la
  première. Aucune clé technique ne sait faire ça.
* **effet inconnu** (``--cas effet_inconnu``) : une exécution précédente a
  pris la clé et n'a jamais rendu de résultat. Son effet a peut-être eu lieu,
  et cette fois le magasin s'en souvient. L'outil est en ``on_unknown: pause``
  : le run s'arrête, l'artisan vérifie la boîte du client, accorde — et
  l'appel reprend la réservation restée en plan.

Les cas à clé métier repartent d'un magasin vide : une clé posée par une
exécution précédente de l'exemple vaudrait encore, et c'est justement ce
qu'elle promet.

Le pilote est « tué » en abandonnant sa tâche, ce qui laisse le journal dans
le même état qu'un process mort. Un vrai ``kill -9``, avec un vrai
sous-process, est dans ``tests/integration/test_idempotence_kill.py``.

La fenêtre entre l'effet et son résultat est élargie par une politique
``after_tool`` qui relit lentement. Ce n'est pas un artifice : entre un
résultat et son écriture au journal, il y a les politiques, les juges et le
déport des gros résultats — de quoi mourir dedans.
"""

import argparse
import asyncio
import contextlib
import os
import sys
import time
from pathlib import Path
from typing import Any

from loom_ia.access.api import Loom, RunResult, UnknownSession
from loom_ia.adapters.models import ModelConfigError
from loom_ia.config import ConfigError, LoomConfig, load_config
from loom_ia.core.events import (
    Event,
    IdempotencyRecorded,
    IdempotencyReused,
    ToolCalled,
    ToolCompleted,
)
from loom_ia.core.model import (
    CONTINUE,
    DEFAULT_TENANT,
    AfterTool,
    Decision,
    ModelSpec,
    RunId,
    SessionId,
    new_id,
    new_run_id,
)
from loom_ia.core.ports import KeyScope
from loom_ia.policies import policy
from loom_ia.runtime import apply_logging, create_idempotency_store, prompt_text
from loom_ia.tools import idempotent, tool

CONFIG = Path(__file__).parent / "relance" / "loom.yaml"
DEMANDE = "Relance le client du devis D-2026-042, sur un ton cordial."
CAS = ("rejoue", "etat_inconnu", "humain", "deux_appels", "cle_metier", "effet_inconnu")
ENVOI = "envoyer_relance"
RELECTURE = "relecture"
CLIENTE = "mme.martin@example.com"
# Temps de relecture du résultat de l'envoi : la fenêtre où le pilote meurt.
LENTEUR = 1.5
# Au-delà, l'attente d'un événement abandonne : l'exemple ne doit pas pendre.
ATTENTE_MAX = 60.0

# Ce que l'outil a vraiment fait. Hors du journal, comme un vrai envoi : c'est
# précisément ce qu'aucune reprise ne peut défaire.
BOITE: list[str] = []

CONSIGNE_ENVOI = (
    "\n\nUne fois la relance rédigée, envoie-la avec `envoyer_relance` : objet "
    "et corps tels que le rôle les a produits, destinataire "
    f"`{CLIENTE}`. N'annonce pas l'envoi avant de l'avoir fait.\n"
)
CONSIGNE_DOUBLE = (
    "\n\nUne fois la relance rédigée, envoie-la avec `envoyer_relance` : objet "
    "et corps tels que le rôle les a produits, destinataire "
    f"`{CLIENTE}`. Puis, par sécurité, appelle `envoyer_relance` une "
    "seconde fois avec exactement les mêmes arguments.\n"
)
CONSIGNE_METIER = (
    "\n\nUne fois la relance rédigée, envoie-la avec `envoyer_relance` : objet "
    "et corps tels que le rôle les a produits, destinataire "
    f"`{CLIENTE}`, et `devis` le numéro du devis relancé. "
    "N'annonce pas l'envoi avant de l'avoir fait.\n"
)


def _poster(destinataire: str, objet: str) -> str:
    BOITE.append(destinataire)
    return f"Relance n°{len(BOITE)} envoyée à {destinataire} — objet « {objet[:50]} »"


@idempotent
@tool(name=ENVOI, side_effects="irreversible")
async def envoyer_garde(destinataire: str, objet: str = "", corps: str = "") -> str:
    """Envoie l'e-mail de relance au client. Irréversible : parti, il est parti."""
    return _poster(destinataire, objet)


@tool(name=ENVOI, side_effects="irreversible")
async def envoyer_nu(destinataire: str, objet: str = "", corps: str = "") -> str:
    """Envoie l'e-mail de relance au client. Irréversible : parti, il est parti."""
    return _poster(destinataire, objet)


@tool(name=ENVOI, side_effects="irreversible", on_unknown="pause")
async def envoyer_a_verifier(destinataire: str, objet: str = "", corps: str = "") -> str:
    """Envoie l'e-mail de relance au client. Irréversible : parti, il est parti."""
    return _poster(destinataire, objet)


# Clé métier : c'est le devis qui fait la clé, pas l'appel. Deux runs, deux
# conversations, deux workers — la même clé, donc un seul envoi.
DEVIS_KEY = "relance:{devis}"


@idempotent(key=lambda a: DEVIS_KEY.format(devis=a["devis"]))
@tool(name=ENVOI, side_effects="irreversible")
async def envoyer_metier(destinataire: str, devis: str, objet: str = "", corps: str = "") -> str:
    """Envoie l'e-mail de relance du devis au client. Irréversible : parti, il est parti."""
    return _poster(destinataire, objet)


@idempotent(key=lambda a: DEVIS_KEY.format(devis=a["devis"]))
@tool(name=ENVOI, side_effects="irreversible", on_unknown="pause")
async def envoyer_metier_a_verifier(
    destinataire: str, devis: str, objet: str = "", corps: str = ""
) -> str:
    """Envoie l'e-mail de relance du devis au client. Irréversible : parti, il est parti."""
    return _poster(destinataire, objet)


OUTILS = {
    "rejoue": envoyer_garde,
    "deux_appels": envoyer_garde,
    "humain": envoyer_a_verifier,
    "cle_metier": envoyer_metier,
    "effet_inconnu": envoyer_metier_a_verifier,
}
# Cas qui ont besoin d'un magasin partagé et durable : la clé métier l'exige.
PARTAGE = frozenset({"cle_metier", "effet_inconnu"})


@policy(points=["after_tool"], decisions=["continue"], name=RELECTURE)
async def relecture(subject: AfterTool) -> Decision:
    """Relit le résultat de l'envoi, lentement : la fenêtre où le pilote meurt."""
    if subject.spec.name == ENVOI:
        await asyncio.sleep(LENTEUR)
    return CONTINUE


# Dernière réponse de l'orchestrateur simulé, par cas : elle nomme le devis et
# dit ce qui s'est passé, comme le ferait un compte rendu utile. Un vrai modèle
# la tire du résultat de l'appel ; ici c'est le script qui la porte, le
# ``with_text`` du modèle simulé ne voyant que la demande.
FINS = {
    "etat_inconnu": (
        "Je n'ai pas pu confirmer l'envoi de la relance du devis D-2026-042 : "
        "l'appel a été interrompu, et je préfère ne pas risquer un doublon."
    )
}
FIN = "La relance du devis D-2026-042 est partie à Mme Martin."


def main_script(nom: str, *, double: bool) -> list[dict[str, Any]]:
    """Script de l'orchestrateur simulé ; sa réponse cite le devis (politique de `relance/`)."""
    arguments: dict[str, Any] = {
        "destinataire": CLIENTE,
        "objet": "Votre devis D-2026-042",
        "corps": "Bonjour Madame Martin, …",
    }
    if nom in PARTAGE:
        # La clé métier se tire du devis : l'outil le demande en argument.
        arguments["devis"] = "D-2026-042"
    envoi: dict[str, Any] = {
        "text": "L'e-mail est prêt, je l'envoie.",
        "tool_calls": [{"name": ENVOI, "arguments": arguments}],
    }
    second = {**envoi, "text": "Je renvoie, par sécurité."}
    return [
        {
            "text": "Je relis le devis.",
            "tool_calls": [{"name": "chercher_devis", "arguments": {"numero": "D-2026-042"}}],
        },
        {"tool_calls": [{"name": "rediger_relance", "arguments": {"ton": "cordial"}}]},
        envoi,
        *([second] if double else []),
        {"text": FINS.get(nom, FIN)},
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


def adjusted(config: LoomConfig, nom: str, *, reel: bool) -> tuple[LoomConfig, str]:
    """Ajoute l'outil d'envoi et la relecture lente, et rend le rôle non terminal."""
    double = nom == "deux_appels"
    consigne = CONSIGNE_DOUBLE if double else CONSIGNE_METIER if nom in PARTAGE else CONSIGNE_ENVOI
    base = "relance_reel" if reel else "relance"
    spec = next(a for a in config.agents if a.name == base)
    main = spec.main.model_copy(
        update={"system": prompt_text(spec.main) + consigne, "system_file": None}
    )
    roles = tuple(
        role.model_copy(update={"terminal": False}) if role.name == "rediger_relance" else role
        for role in spec.roles
    )
    lente = _lente(spec)
    agent = spec.model_copy(
        update={
            "main": main,
            "roles": roles,
            "tools": (*spec.tools, _envoi(spec)),
            "policies": (*spec.policies, lente),
            "max_iterations": spec.max_iterations + 2,
        }
    )
    agents = tuple(agent if a.name == base else a for a in config.agents)
    storage = config.storage
    if nom not in PARTAGE:
        # `relance/loom.yaml` déclare le magasin partagé, celui qu'exige une
        # clé métier. Ces quatre cas-ci montrent l'autre : celui du journal,
        # qui n'a pas de stockage propre. C'est le seul réglage que l'exemple
        # change — le fichier de la base, lui, est nommé dans la config.
        storage = storage.model_copy(
            update={
                "idempotency": storage.idempotency.model_copy(
                    update={"backend": "journal", "path": None}
                )
            }
        )
    models = list(config.models)
    if not reel:

        def script(model: ModelSpec, replies: list[dict[str, Any]]) -> ModelSpec:
            return model.model_copy(update={"params": {**model.params, "script": replies}})

        models = [
            script(m, main_script(nom, double=double))
            if m.id == "FAKE_MAIN"
            else script(m, ROLE)
            if m.id == "FAKE_ROLE"
            else m
            for m in models
        ]
    return (
        config.model_copy(update={"models": tuple(models), "agents": agents, "storage": storage}),
        base,
    )


def _envoi(spec: Any) -> Any:
    """Référence à l'outil d'envoi, telle que l'écrirait la config.

    Ni ``idempotent`` ni ``on_unknown`` ne sont posés ici : c'est l'outil qui
    les déclare — décorer, c'est déclarer.
    """
    return spec.tools[0].model_copy(
        update={"python": ENVOI, "side_effects": "irreversible", "idempotent": None}
    )


def _lente(spec: Any) -> Any:
    """Référence à la relecture lente, telle que l'écrirait la config."""
    modele = next(iter(spec.policies), None)
    if modele is None:
        raise ConfigError("l'agent de `relance/` devrait porter au moins une politique")
    return modele.model_copy(
        update={"hook": RELECTURE, "name": RELECTURE, "points": None, "timeout": None}
    )


def outcome(result: RunResult) -> str:
    if result.ok:
        return "terminé"
    if result.error_type:
        return f"{result.status} ({result.error_type})"
    return str(result.status)


def compte(events: list[Event], kind: str) -> int:
    return len([e for e in events if e.type == kind])


def envois(events: list[Event]) -> tuple[int, int]:
    """Appels de l'outil d'envoi, et effets mémorisés."""
    appels = len(
        [
            e
            for e in events
            if isinstance(payload := e.payload, ToolCalled) and payload.tool_name == ENVOI
        ]
    )
    return appels, compte(events, "idempotency.recorded")


def dedoublonnes(events: list[Event]) -> int:
    """Appels qui n'ont rien fait, leur effet étant déjà mémorisé."""
    return len([e for e in events if isinstance(e.payload, IdempotencyReused)])


def finis(events: list[Event]) -> list[ToolCompleted]:
    """Résultats de l'outil d'envoi écrits au journal."""
    return [
        payload
        for e in events
        if isinstance(payload := e.payload, ToolCompleted) and e.facets.get("tool_name") == ENVOI
    ]


def dernier_resultat(events: list[Event]) -> str:
    resultats = finis(events)
    return resultats[-1].output.as_text.strip() if resultats else "—"


async def _parti(loom: Loom, run_id: RunId, session: SessionId, *, memorise: bool) -> None:
    """Attend que la relance soit partie — et, si on le demande, mémorisée.

    On guette le fait, pas l'état : l'effet est dans la boîte, et son
    enregistrement au journal. Dormir un temps fixe raterait la fenêtre, qui
    ne dure que le temps de la relecture.
    """
    fin = time.monotonic() + ATTENTE_MAX
    while time.monotonic() < fin:
        events: list[Event] = []
        with contextlib.suppress(UnknownSession):
            events = [e for e in await loom.export_session(session) if e.run_id == run_id]
        recorded = any(isinstance(e.payload, IdempotencyRecorded) for e in events)
        if BOITE and (recorded or not memorise):
            return
        await asyncio.sleep(0.02)
    raise TimeoutError(f"la relance du run {run_id} n'est pas partie en {ATTENTE_MAX:g} s")


async def _interrompu(loom: Loom, agent: str, session: SessionId, *, memorise: bool) -> RunId:
    """Lance le run et abandonne le pilote dès que la relance est partie."""
    run_id = new_run_id()
    task = asyncio.create_task(loom.run(agent, DEMANDE, session_id=session, run_id=run_id))
    await _parti(loom, run_id, session, memorise=memorise)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    return run_id


async def rejoue(loom: Loom, agent: str, session: SessionId) -> None:
    print("— Rejoué : l'effet est mémorisé, l'appel repart sans le refaire\n")
    run_id = await _interrompu(loom, agent, session, memorise=True)
    events = [e for e in await loom.export_session(session) if e.run_id == run_id]
    appels, memorises = envois(events)
    print(f"  pilote tué : {appels} appel(s) d'envoi, {memorises} effet(s) mémorisé(s)")
    print(f"  boîte      : {len(BOITE)} relance(s) partie(s)")
    print(
        f"  journal    : {len(finis(events))} résultat(s) d'envoi écrit(s) — le pilote"
        " est mort avant"
    )

    repris = await loom.resume(run_id, session_id=session)
    events = [e for e in await loom.export_session(session) if e.run_id == run_id]
    appels, memorises = envois(events)
    print(f"  reprise    : {outcome(repris)} en {repris.iterations} itération(s)")
    print(f"  envoi      : {appels} appel(s), {memorises} effet(s) mémorisé(s)")
    print(f"  journal    : {dedoublonnes(events)} idempotency.reused sur l'appel rejoué")
    print(f"  boîte      : {len(BOITE)} relance(s) partie(s) — l'effet n'a pas été refait")
    print(f"  résultat   : {dernier_resultat(events)}")
    print(f"  réponse    : {repris.text.splitlines()[0][:70] if repris.text else '—'}\n")


async def etat_inconnu(loom: Loom, agent: str, session: SessionId) -> None:
    print("— État inconnu : sans garde, le moteur refuse de relancer l'appel\n")
    run_id = await _interrompu(loom, agent, session, memorise=False)
    print(f"  pilote tué : {len(BOITE)} relance(s) partie(s), aucun résultat au journal")

    repris = await loom.resume(run_id, session_id=session)
    events = [e for e in await loom.export_session(session) if e.run_id == run_id]
    appels, _ = envois(events)
    print(f"  reprise    : {outcome(repris)} en {repris.iterations} itération(s)")
    print(f"  envoi      : {appels} appel(s) — l'outil n'a pas été rappelé")
    print(f"  boîte      : {len(BOITE)} relance(s) partie(s)")
    print(f"  résultat   : {dernier_resultat(events)[:90]}")
    print(f"  réponse    : {repris.text.splitlines()[0][:70] if repris.text else '—'}\n")


async def humain(loom: Loom, agent: str, session: SessionId) -> None:
    print("— Humain : le run se met en pause, l'artisan décide de relancer ou non\n")
    run_id = await _interrompu(loom, agent, session, memorise=False)
    print(f"  pilote tué : {len(BOITE)} relance(s) partie(s) — rien ne le dit au journal")

    repris = await loom.resume(run_id, session_id=session)
    print(f"  reprise    : {outcome(repris)} — le run attend une décision")
    asked = repris.pending_approvals[0]
    print(f"  en attente : {asked.tool_name} — {asked.reason[:60]}…")
    print("  l'artisan tranche : il accorde, en sachant que la relance peut repartir")
    await loom.approve(run_id, call_id=asked.call_id, by="l'artisan", session_id=session)
    await loom.drain()
    fin = await loom.result(run_id, session_id=session)
    events = [e for e in await loom.export_session(session) if e.run_id == run_id]
    appels, _ = envois(events)
    print(f"  reprise    : {outcome(fin)} — {appels} appel(s) d'envoi")
    print(
        f"  boîte      : {len(BOITE)} relance(s) partie(s) — le doublon est un choix,"
        " pas un accident"
    )
    print(f"  réponse    : {fin.text.splitlines()[0][:70] if fin.text else '—'}\n")


async def deux_appels(loom: Loom, agent: str, session: SessionId) -> None:
    print("— Deux appels : la clé technique protège un appel, pas un doublon métier\n")
    result = await loom.run(agent, DEMANDE, session_id=session)
    events = [e for e in await loom.export_session(session) if e.run_id == result.run_id]
    appels, memorises = envois(events)
    print(f"  run        : {outcome(result)} en {result.iterations} itération(s)")
    print(f"  envoi      : {appels} appel(s), {memorises} effet(s) mémorisé(s) — un par appel")
    print(f"  boîte      : {len(BOITE)} relance(s) partie(s)")
    print("  il faudrait une clé métier et un magasin partagé (phase 4.4b)")
    print(f"  réponse    : {result.text.splitlines()[0][:70] if result.text else '—'}\n")


async def _cles_vides(config: LoomConfig) -> None:
    """Repart d'un magasin vide : la clé d'une exécution précédente vaudrait encore.

    C'est justement ce que la clé métier promet — elle survit au run, à la
    session et au process. Dans un exemple qu'on rejoue, il faut donc la
    retirer soi-même, comme le ferait une suppression de client.
    """
    store = create_idempotency_store(config)
    if store is None:
        return
    await store.forget(DEFAULT_TENANT)
    closing = getattr(store, "aclose", None)
    if closing is not None:
        await closing()


async def cle_metier(loom: Loom, agent: str, session: SessionId) -> None:
    print("— Clé métier : deux conversations, un seul envoi\n")
    premier = await loom.run(agent, DEMANDE, session_id=session)
    events = [e for e in await loom.export_session(session) if e.run_id == premier.run_id]
    appels, _ = envois(events)
    print(f"  run 1      : {outcome(premier)} — {appels} appel(s) d'envoi")
    print(f"  boîte      : {len(BOITE)} relance(s) partie(s)")

    # Une autre conversation, un autre run : rien au journal ne les relie.
    # Ce qui les relie est la clé « relance:D-2026-042 », dans la base.
    suivante = SessionId(f"{session}-bis")
    second = await loom.run(agent, DEMANDE, session_id=suivante)
    events = [e for e in await loom.export_session(suivante) if e.run_id == second.run_id]
    appels, _ = envois(events)
    print(f"  run 2      : {outcome(second)} — {appels} appel(s) d'envoi, dans une autre session")
    print(
        f"  journal    : {dedoublonnes(events)} idempotency.reused — "
        "le run dit pourquoi l'appel n'a rien fait"
    )
    print(f"  boîte      : {len(BOITE)} relance(s) partie(s) — la clé du devis a tenu")
    print(f"  résultat   : {dernier_resultat(events)}")
    print(f"  réponse    : {second.text.splitlines()[0][:70] if second.text else '—'}\n")


async def effet_inconnu(loom: Loom, agent: str, session: SessionId, config: LoomConfig) -> None:
    print("— Effet inconnu : une réservation restée en plan, et l'artisan tranche\n")
    # Une exécution précédente a pris la clé et n'a jamais rendu de résultat :
    # son effet a peut-être eu lieu. Le magasin partagé, lui, s'en souvient.
    store = create_idempotency_store(config)
    assert store is not None, "les cas à clé métier tournent sur un magasin partagé"
    clef = f"{DEFAULT_TENANT}:{DEVIS_KEY.format(devis='D-2026-042')}"
    await store.reserve(clef, -1.0, KeyScope(tenant_id=DEFAULT_TENANT, session_id=session))
    closing = getattr(store, "aclose", None)
    if closing is not None:
        await closing()
    print(f"  au magasin : réservation périmée sur « {clef} », sans résultat")

    result = await loom.run(agent, DEMANDE, session_id=session)
    print(f"  run        : {outcome(result)}")
    asked = result.pending_approvals[0]
    print(f"  en attente : {asked.tool_name} — {asked.reason[:58]}…")
    print(f"  boîte      : {len(BOITE)} relance(s) partie(s) — rien n'est parti de ce run")
    print("  l'artisan a vérifié la boîte d'envoi du client : rien n'est arrivé, il accorde")
    await loom.approve(result.run_id, call_id=asked.call_id, by="l'artisan", session_id=session)
    await loom.drain()
    fin = await loom.result(result.run_id, session_id=session)
    events = [e for e in await loom.export_session(session) if e.run_id == result.run_id]
    appels, _ = envois(events)
    print(f"  reprise    : {outcome(fin)} — {appels} appel(s) d'envoi")
    print(f"  boîte      : {len(BOITE)} relance(s) partie(s) — la clé a été reprise")
    print(f"  réponse    : {fin.text.splitlines()[0][:70] if fin.text else '—'}\n")


async def jouer(nom: str, config: LoomConfig, agent: str, session: SessionId) -> None:
    """Un cas, dans son instance : chacun a son outil d'envoi."""
    BOITE.clear()
    if nom in PARTAGE:
        await _cles_vides(config)
    async with Loom(config) as loom:
        loom.register(ENVOI, OUTILS.get(nom, envoyer_nu))
        loom.register(RELECTURE, relecture)
        if nom == "rejoue":
            await rejoue(loom, agent, session)
        elif nom == "etat_inconnu":
            await etat_inconnu(loom, agent, session)
        elif nom == "humain":
            await humain(loom, agent, session)
        elif nom == "cle_metier":
            await cle_metier(loom, agent, session)
        elif nom == "effet_inconnu":
            await effet_inconnu(loom, agent, session, config)
        else:
            await deux_appels(loom, agent, session)


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Idempotence d'un outil à effet de bord")
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
    print(f"Outil    : {ENVOI} (side_effects: irreversible)")
    magasin = base.storage.idempotency
    print(f"Magasin  : {magasin.backend} ({magasin.path}) ; journal pour les clés techniques\n")
    for nom in cas:
        try:
            config, agent = adjusted(base, nom, reel=args.reel)
            await jouer(nom, config, agent, SessionId(f"{prefixe}-{nom}"))
        except (ModelConfigError, ConfigError) as error:
            print(f"Configuration : {error}", file=sys.stderr)
            return 2
    print(f"Export : uv run loom --config {shown(CONFIG)} sessions list")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
